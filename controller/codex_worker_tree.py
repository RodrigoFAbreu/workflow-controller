"""The private clone a Codex worker runs in (``workflow-controller-h4e``, CP1, D1 and D2).

A Codex worker never runs in the managed repository. :func:`prepare` makes a separate repository of the managed branch
at the commit the Controller recorded before the job, with nothing connecting it back (no remote, no alternates, no
hooks), a known author, and refuses -- with a named ``cause`` -- unless the managed branch is where the Controller left
it and its tree is clean. Every Git command runs in the scrubbed environment of ``review._git_env`` (a fixed ``PATH``,
no global or system configuration), every read of the managed repository without optional locks and without an
fsmonitor program, and every clone-side write without hooks. Nothing here lands anything: the landing is
``codex_worker_landing`` (CP2).
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Collection
from pathlib import Path
from typing import Any

from . import codex_worker_sandbox, gitrepo, review, runtime
from .errors import ControllerError, GitOperationError, RuntimeContainmentError

#: The author every commit of a worker carries (D1): written into the clone's own config so a plain ``git commit`` in
#: the sandboxed shell, which has no identity of its own, succeeds with a fixed, recognisable author.
WORKER_AUTHOR_NAME = "Codex worker"
WORKER_AUTHOR_EMAIL = "codex-worker@controller.invalid"

#: The causes of a :class:`CodexTreeError`.
CAUSES = (
    "detached_head", "managed_branch_moved", "managed_tree_not_clean", "runtime_inside_managed",
    "managed_hooks_configured", "clone_failed",
)


class CodexTreeError(ControllerError):
    """The clone could not be made, or the managed repository is not in a state a clone may start from. ``cause`` is
    one of :data:`CAUSES`; ``evidence`` carries it too."""

    code = "CodexTreeError"

    def __init__(self, cause: str, message: str, *, evidence: dict | None = None) -> None:
        super().__init__(message, evidence={**(evidence or {}), "cause": cause})
        self.cause = cause


@dataclasses.dataclass(frozen=True)
class Prepared:
    """A prepared clone: where it is, the branch checked out in it, the commit that branch is at, and the digest of
    its ``.git`` (taken after the identity was written) for the landing's check that nothing but commits changed."""

    path: Path
    branch: str
    base: str
    guard: dict
    #: The managed repository's uncommitted paths (``tracked_changes`` and ``untracked``), only for a ``read_only``
    #: clone, which is made whatever they are; ``None`` otherwise.
    dirty: dict | None = None

    def to_dict(self) -> dict:
        """The ``clone`` block of the ``codex_worker`` record."""
        block = {"path": str(self.path), "branch": self.branch, "base": self.base, "guard": self.guard}
        if self.dirty is not None:
            block["dirty"] = self.dirty
        return block


def scrubbed_runner(ceiling: Path, *, no_optional_locks: bool = False) -> gitrepo.Runner:
    """A :data:`gitrepo.Runner` in the scrubbed environment of ``review._git_env(ceiling)``; with
    ``no_optional_locks`` also ``GIT_OPTIONAL_LOCKS=0``."""
    env = review._git_env(ceiling)
    if no_optional_locks:
        env["GIT_OPTIONAL_LOCKS"] = "0"
    return gitrepo.subprocess_runner(env)


def managed_hooks(repo_root: Path, runner: gitrepo.Runner) -> list[str]:
    """The ``hook.*`` keys of the managed repository's configuration (``git config --get-regexp '^hook\\.'``,
    without ``--local``, so a hook in a file reached by ``include.path`` is found), empty for none."""
    out = gitrepo.config_probe(repo_root, ["--get-regexp", r"^hook\."], runner=runner)
    return [line for line in (out or "").splitlines() if line]


def runtime_is_inside(runtime_root: Path, managed_root: Path) -> bool:
    """Whether the resolved runtime root is the managed root or lies below it (links followed)."""
    runtime_path, managed_path = Path(runtime_root).resolve(), Path(managed_root).resolve()
    return runtime_path == managed_path or managed_path in runtime_path.parents


def _refuse(cause: str, message: str, **evidence: Any) -> CodexTreeError:
    return CodexTreeError(cause, message, evidence=evidence)


def check_managed(managed_repo: Any, layout: codex_worker_sandbox.WorkerLayout, base: str, *,
                  read_only: bool = False, allow_dirty: Collection[str] | None = None) -> str:
    """D2's refusals, in order, with no write anywhere. Returns the managed branch's name. With ``read_only`` (a job
    that lands nothing, ``workflow-controller-h4e2`` D2) only the ``managed_tree_not_clean`` refusal is skipped. With
    ``allow_dirty`` (a carry, ``workflow-controller-h4e3`` D5) the refusal is skipped only when every dirty path is
    one of those paths."""
    root = Path(managed_repo.root)
    runner = scrubbed_runner(layout.base, no_optional_locks=True)
    try:
        head = gitrepo.head_state(root, runner=runner, no_optional_locks=True)
        if head.detached:
            raise _refuse("detached_head", "the managed repository's HEAD is detached; a Codex worker's clone "
                          "starts from a branch", commit=head.commit)
        branch_commit = gitrepo.ref_commit(root, f"refs/heads/{head.branch}", runner=runner, no_optional_locks=True)
        if branch_commit != base:
            raise _refuse("managed_branch_moved", f"the managed branch {head.branch} is at {branch_commit}, not at "
                          f"{base}, the commit recorded before the job", branch=head.branch, branch_commit=branch_commit,
                          base=base)
        changes = gitrepo.tracked_changes(root, runner=runner, no_optional_locks=True)
        untracked = gitrepo.untracked_files(root, runner=runner, no_optional_locks=True)
        unaccounted = [path for path in [gitrepo.status_path(entry) for entry in changes] + untracked
                       if allow_dirty is None or path not in allow_dirty]
        if unaccounted and not read_only:
            raise _refuse("managed_tree_not_clean", "the managed repository has uncommitted changes, which a clone "
                          "cannot receive and a landing could not return", tracked_changes=changes,
                          untracked=untracked)
        if runtime_is_inside(layout.runtime_root, root):
            raise _refuse("runtime_inside_managed", "the Controller's runtime root lies inside the managed "
                          "repository; a landing could add a file to the Controller's own state",
                          runtime_root=str(layout.runtime_root), managed_root=str(root))
        hooks = managed_hooks(root, runner)
        if hooks:
            raise _refuse("managed_hooks_configured", "the managed repository defines a config hook, which a "
                          "landing would have to switch off event by event", hooks=hooks)
    except GitOperationError as exc:
        raise _refuse("clone_failed", f"the managed repository could not be read: {exc.message}",
                      **exc.evidence) from exc
    return head.branch


def _remove_partial(layout: codex_worker_sandbox.WorkerLayout) -> None:
    try:
        runtime.remove_tree(layout.runtime_root, layout.rel("tree"))
    except (RuntimeContainmentError, OSError):
        pass


def _git(clone: Path, args: list[str], runner: gitrepo.Runner, what: str) -> None:
    argv = ["git", "-C", str(clone), *args]
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitOperationError(f"git {what} could not run: {exc}", evidence={"argv": argv}) from None
    if result.returncode != 0:
        raise GitOperationError(
            f"git {what} failed (exit {result.returncode}): {result.stderr.decode('utf-8', 'replace').strip()}",
            evidence={"argv": argv, "exit_code": result.returncode,
                      "stderr": result.stderr.decode("utf-8", "replace").strip()})


def managed_dirty(managed_repo: Any, layout: codex_worker_sandbox.WorkerLayout) -> dict:
    """The managed repository's uncommitted paths, read without optional locks."""
    root = Path(managed_repo.root)
    runner = scrubbed_runner(layout.base, no_optional_locks=True)
    try:
        return {"tracked_changes": gitrepo.tracked_changes(root, runner=runner, no_optional_locks=True),
                "untracked": gitrepo.untracked_files(root, runner=runner, no_optional_locks=True)}
    except GitOperationError as exc:
        raise _refuse("clone_failed", f"the managed repository could not be read: {exc.message}",
                      **exc.evidence) from exc


def prepare(managed_repo: Any, layout: codex_worker_sandbox.WorkerLayout, base: str, *,
            read_only: bool = False, allow_dirty: Collection[str] | None = None) -> Prepared:
    """D1 and D2: refuse unless the managed branch is clean and at ``base``, then make the private clone at
    ``layout.worktree`` and return it. A clone partly made is removed before the error is raised. With ``read_only``
    a dirty managed tree is not refused (the clone is made from the commit, a read-only job lands nothing) and its
    dirty paths are returned in :attr:`Prepared.dirty`; every other refusal stands. With ``allow_dirty`` (the carry's
    set, ``workflow-controller-h4e3`` D5) a dirty tree is accepted only when every dirty path is in it."""
    root = Path(managed_repo.root)
    branch = check_managed(managed_repo, layout, base, read_only=read_only, allow_dirty=allow_dirty)
    dirty = managed_dirty(managed_repo, layout) if read_only else None
    clone = layout.worktree
    runner = scrubbed_runner(layout.base)
    argv = ["git", "clone", "--no-checkout", "--no-local", "--no-hardlinks", "--no-tags", "--single-branch",
            "--branch", branch, "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
            "--", str(root), str(clone)]
    try:
        try:
            result = runner(argv)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitOperationError(f"git clone could not run: {exc}", evidence={"argv": argv}) from None
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise GitOperationError(f"git clone failed (exit {result.returncode}): {stderr}",
                                    evidence={"argv": argv, "exit_code": result.returncode, "stderr": stderr})
        _git(clone, ["remote", "remove", "origin"], runner, "remote remove")
        for key, value in (("core.hooksPath", "/dev/null"), ("gc.auto", "0"), ("maintenance.auto", "false"),
                           ("user.name", WORKER_AUTHOR_NAME), ("user.email", WORKER_AUTHOR_EMAIL)):
            _git(clone, ["config", "--local", key, value], runner, "config")
        gitrepo.checkout_branch_at(clone, branch, base, runner=runner)
        codex_worker_sandbox.check_worktree(clone)
        guard = codex_worker_sandbox.guard_digest(clone)
    except (GitOperationError, ControllerError) as exc:
        _remove_partial(layout)
        raise _refuse("clone_failed", f"the clone could not be made: {exc.message}", **exc.evidence) from exc
    except BaseException:
        _remove_partial(layout)
        raise
    return Prepared(path=clone, branch=branch, base=base, guard=guard, dirty=dirty)
