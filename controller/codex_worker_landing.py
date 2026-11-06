"""Landing a Codex worker's commits (``workflow-controller-h4e``, CP2, D3 to D7).

A Codex worker commits in a private clone (``codex_worker_tree``). The landing is a Controller act with one question:
is what the clone holds exactly a fast-forward of the managed branch, and nothing else. :func:`land` runs, once the
worker has ended, every check of D3 in order (each refusal naming a ``cause``), then fetches the clone's branch into
``refs/controller/landing/<job id>`` and moves the managed branch with ``merge --ff-only``; every write goes through
``gitrepo``'s one hardened option set (D4) and runs nothing of the worker's, no hook and no automatic maintenance.
The landing judges the integrity of the move, never the content of the commits (D5): the Workflow's own checks
judge content exactly as they do for an in-place worker, because a fast-forward keeps every commit id.

:func:`verify_move` is the post-move check that :func:`land` and :func:`recover` both call, so the live path and the
recovery path cannot drift apart. :func:`decide` is the reading half of :func:`recover` (D7): it reads refs and the
tree and returns a :class:`Recovery`; the applying half lives in ``controller/job.py`` (CP3). Nothing here calls
``land`` yet: ``job`` does, from CP3.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import codex_worker_sandbox, codex_worker_tree, gitrepo, review
from .errors import ControllerError, GitOperationError

#: The most commits one landing carries (D3 (5)).
MAX_LANDED_COMMITS = 200

#: Every ``cause`` of a refused landing (D3, D4, D6, D7), in the order the checks make them where they are checks.
CAUSES = (
    "sandbox_breach", "clone_git_changed", "extra_refs", "managed_branch_moved", "managed_tree_not_clean",
    "runtime_inside_managed", "managed_hooks_configured", "not_a_descendant", "merge_commit", "too_many_commits",
    "submodule", "fetch_failed", "tip_moved", "would_overwrite_ignored", "fast_forward_failed",
    "merge_left_tree_dirty", "landing_unverified", "landing_error",
    # Recovery only (D7):
    "landing_interrupted", "landing_unreadable",
)
#: The cause recorded for an outcome ``nothing`` (a success: the clone's branch is the base, no write).
NOTHING_TO_LAND = "nothing_to_land"

#: Git's own wording when ``merge --ff-only --no-overwrite-ignore`` would replace an ignored file, or an ignored
#: directory the range turns into a file (both exit 1).
_OVERWRITE_TEXT = ("would be overwritten", "would lose untracked")


class CodexLandingError(ControllerError):
    """A refusal of the landing, internal to :func:`land`: ``cause`` is one of :data:`CAUSES`, ``moved`` whether the
    managed branch was moved, ``dirty`` the managed paths a failed merge left changed. ``land`` never raises it."""

    code = "CodexLandingError"

    def __init__(self, cause: str, message: str, *, moved: bool = False, dirty: list[str] | None = None,
                 evidence: dict | None = None) -> None:
        super().__init__(message, evidence={**(evidence or {}), "cause": cause})
        self.cause = cause
        self.moved = moved
        self.dirty = list(dirty or [])


@dataclasses.dataclass(frozen=True)
class Landing:
    """The outcome of :func:`land`. ``outcome`` is ``landed``, ``nothing`` or ``refused``; ``cause`` names a refusal
    (``nothing_to_land`` for ``nothing``); ``tip`` is the checked tip (``None`` before it was read); ``moved`` is true
    only when the managed branch was moved (so ``landing_unverified`` is ``refused`` with ``moved`` true);
    ``ref_left`` is true when the landing ref could not be deleted, and ``ref`` then names it; ``dirty`` are the paths
    a failed merge left."""

    outcome: str
    cause: str | None
    base: str
    tip: str | None
    branch: str
    commits: tuple[str, ...] = ()
    moved: bool = False
    ref_left: bool = False
    dirty: tuple[str, ...] = ()
    message: str = ""
    ref: str | None = None

    def to_block(self, *, clone_kept: bool) -> dict:
        """The final ``landing`` block of the job record (D6)."""
        block: dict[str, Any] = {
            "outcome": self.outcome, "cause": self.cause, "base": self.base, "tip": self.tip, "commits": list(self.commits),
            "branch": self.branch, "clone_kept": clone_kept, "moved": self.moved, "ref_left": self.ref_left,
            "dirty": list(self.dirty)}
        if self.message:
            block["message"] = self.message
        if self.ref_left:
            block["ref"] = self.ref
        return block


def _runner(ceiling: Path, *, fetch: bool = False) -> gitrepo.Runner:
    """The scrubbed environment of ``review._git_env``, without optional locks; the fetch adds the one allowed
    transport (``GIT_ALLOW_PROTOCOL=file``, D4)."""
    env = review._git_env(ceiling)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    if fetch:
        env["GIT_ALLOW_PROTOCOL"] = "file"
    return gitrepo.subprocess_runner(env)


def _refuse(cause: str, message: str, **kw: Any) -> CodexLandingError:
    return CodexLandingError(cause, message, **kw)


# ---------------------------------------------------------------------------
# The post-move check (D4), shared by ``land`` and ``recover``.
# ---------------------------------------------------------------------------


def verify_move(managed_repo: Any, tip: str, branch: str, *, runner: gitrepo.Runner | None = None) -> str | None:
    """``None`` when the move holds: the branch is ``tip``, ``HEAD`` is that branch and the managed tree is clean
    (``tracked_changes`` and ``untracked_files`` empty, ``GIT_OPTIONAL_LOCKS=0``); else the reason it does not. A read
    that fails is a reason too, never an exception: after a merge that may have moved the branch, a failed read cannot
    show the move did not happen."""
    root = Path(managed_repo.root)
    runner = runner or _runner(root.parent)
    try:
        head = gitrepo.head_state(root, runner=runner, no_optional_locks=True)
        if head.branch != branch:
            return f"HEAD is on {head.branch or 'a detached commit'}, not on {branch}"
        branch_commit = gitrepo.ref_commit(root, f"refs/heads/{branch}", runner=runner, no_optional_locks=True)
        if branch_commit != tip:
            return f"the branch {branch} is at {branch_commit}, not at {tip}"
        changes = gitrepo.tracked_changes(root, runner=runner, no_optional_locks=True)
        untracked = gitrepo.untracked_files(root, runner=runner, no_optional_locks=True)
    except (GitOperationError, OSError, subprocess.TimeoutExpired) as exc:
        return f"the managed repository could not be read: {getattr(exc, 'message', exc)}"
    if changes or untracked:
        return f"the managed tree is not clean after the move: {sorted([*changes, *untracked])[:20]}"
    return None


# ---------------------------------------------------------------------------
# The landing (D3, D4).
# ---------------------------------------------------------------------------


def _check_range(clone: Path, base: str, tip: str, runner: gitrepo.Runner) -> list[str]:
    """D3 (4) to (6), read in the clone with the commit graph off. Returns the commits, oldest first."""
    if not gitrepo.strict_descendant(clone, base, tip, runner=runner):
        raise _refuse("not_a_descendant", f"the clone's branch tip {tip} is not a descendant of the base {base}",
                      evidence={"base": base, "tip": tip})
    commits = gitrepo.range_commits(clone, base, tip, MAX_LANDED_COMMITS + 1, runner=runner)
    merges = [commit for commit, parents in commits if len(parents) != 1]
    if merges:
        raise _refuse("merge_commit", f"the range holds a commit that does not have exactly one parent: {merges[0]}",
                      evidence={"commit": merges[0]})
    if len(commits) > MAX_LANDED_COMMITS:
        raise _refuse("too_many_commits", f"the range holds more than {MAX_LANDED_COMMITS} commits",
                      evidence={"limit": MAX_LANDED_COMMITS})
    for commit, parents in commits:
        if gitrepo.commit_touches_submodule(clone, commit, parents[0], runner=runner):
            raise _refuse("submodule", f"the commit {commit} adds or changes a submodule", evidence={"commit": commit})
    return [commit for commit, _ in commits]


def _check_clone(prepared: Any, runner: gitrepo.Runner) -> None:
    """D3 (1) and (2): the clone's ``.git`` is unchanged but for commits, and it holds no ref but its branch."""
    clone = Path(prepared.path)
    try:
        codex_worker_sandbox.check_guard(prepared.guard, codex_worker_sandbox.guard_digest(clone))
    except ControllerError as exc:
        raise _refuse("clone_git_changed", f"the clone's .git changed during the run: {exc.message}",
                      evidence=dict(exc.evidence)) from exc
    wanted = f"refs/heads/{prepared.branch}"
    refs = gitrepo.clone_refs(clone, runner=runner)
    extra = [ref for ref in refs if ref != wanted]
    head = gitrepo.head_symref(clone, runner=runner)
    if extra or head != wanted:
        raise _refuse("extra_refs", f"the clone holds a ref other than {wanted}, or HEAD is not that branch: refs "
                      f"{extra[:20]}, HEAD {head}", evidence={"refs": extra, "head": head})


def _check_managed(managed_root: Path, prepared: Any, runtime_root: Path, runner: gitrepo.Runner) -> None:
    """D3 (3): the managed branch and tree are as the Controller left them, the runtime root lies outside the managed
    root and the managed repository defines no config hook."""
    head = gitrepo.head_state(managed_root, runner=runner, no_optional_locks=True)
    branch_commit = gitrepo.ref_commit(managed_root, f"refs/heads/{prepared.branch}", runner=runner,
                                       no_optional_locks=True)
    if head.branch != prepared.branch or branch_commit != prepared.base:
        raise _refuse("managed_branch_moved",
                      f"the managed branch {prepared.branch} is at {branch_commit} (HEAD on "
                      f"{head.branch or 'a detached commit'}), not at {prepared.base}",
                      evidence={"head_branch": head.branch, "branch_commit": branch_commit, "base": prepared.base})
    changes = gitrepo.tracked_changes(managed_root, runner=runner, no_optional_locks=True)
    untracked = gitrepo.untracked_files(managed_root, runner=runner, no_optional_locks=True)
    if changes or untracked:
        raise _refuse("managed_tree_not_clean", "the managed repository has uncommitted changes",
                      dirty=[*changes, *untracked])
    if codex_worker_tree.runtime_is_inside(runtime_root, managed_root):
        raise _refuse("runtime_inside_managed", "the Controller's runtime root lies inside the managed repository",
                      evidence={"runtime_root": str(runtime_root), "managed_root": str(managed_root)})
    hooks = codex_worker_tree.managed_hooks(managed_root, runner)
    if hooks:
        raise _refuse("managed_hooks_configured", f"the managed repository defines a config hook: {hooks[:20]}",
                      evidence={"hooks": hooks})


def _classify_after_merge(managed_root: Path, prepared: Any, tip: str, runner: gitrepo.Runner, *,
                          exit_code: int | None, text: str, failure: str) -> CodexLandingError | None:
    """After a merge that did not succeed, or whose report raised (I1 of round 5): read the branch and the tree and
    classify by where the branch stands. ``None`` means the move holds (the merge did its work and only its report was
    lost) and the landing is ``landed``."""
    try:
        branch_commit = gitrepo.ref_commit(managed_root, f"refs/heads/{prepared.branch}", runner=runner,
                                           no_optional_locks=True)
    except (GitOperationError, OSError, subprocess.TimeoutExpired) as exc:
        return _refuse("landing_unverified", f"the merge did not report success ({failure}) and the branch could not "
                       f"be read afterwards: {getattr(exc, 'message', exc)}", moved=True)
    if branch_commit == prepared.base:
        try:
            dirty = [*gitrepo.tracked_changes(managed_root, runner=runner, no_optional_locks=True),
                     *gitrepo.untracked_files(managed_root, runner=runner, no_optional_locks=True)]
        except (GitOperationError, OSError, subprocess.TimeoutExpired) as exc:
            return _refuse("landing_unverified", f"the merge failed ({failure}) and the managed tree could not be "
                           f"read afterwards: {getattr(exc, 'message', exc)}", moved=True)
        if dirty:
            return _refuse("merge_left_tree_dirty", f"the merge failed ({failure}) with the branch at the base and "
                           "the managed tree changed; nothing was repaired", dirty=dirty)
        if exit_code == 1 and any(marker in text for marker in _OVERWRITE_TEXT):
            return _refuse("would_overwrite_ignored", "the range would overwrite an ignored file of the managed "
                           f"repository: {text.strip()}")
        return _refuse("fast_forward_failed", f"the merge failed with the branch at the base: {failure}")
    if branch_commit == tip:
        reason = verify_move(SimpleNamespace(root=managed_root), tip, prepared.branch, runner=runner)
        if reason is None:
            return None
        return _refuse("landing_unverified", reason, moved=True)
    return _refuse("landing_unverified", f"the managed branch is at {branch_commit}, neither the base nor {tip}",
                   moved=True)


def land(managed_repo: Any, prepared: Any, runtime_root: Path, job_id: str, *, worker_breach: bool,
         on_intent: Callable[[str], None]) -> Landing:
    """D3 and D4: land the clone's commits, or refuse with a named cause. Never raises for a Git or OS failure: an
    unexpected one is ``landing_error`` (a refusal like the others). ``on_intent(checked_tip)`` is called once, after
    every check and before the fetch, and not for a refusal at a check or for ``nothing``."""
    managed_root = Path(managed_repo.root)
    runtime_root = Path(runtime_root)
    layout = codex_worker_sandbox.WorkerLayout(runtime_root, job_id)
    base, branch = prepared.base, prepared.branch
    read = _runner(layout.base)
    state: dict[str, Any] = {"tip": None, "commits": [], "fetched": False, "merging": False}
    result: Landing | None = None
    ref_left = False
    try:
        try:
            if worker_breach:
                raise _refuse("sandbox_breach", "the launcher judged the run untrustworthy (the .git guard, the nested "
                              "repository scan or the Codex build failed after the run)")
            _check_clone(prepared, read)
            _check_managed(managed_root, prepared, runtime_root, read)
            clone = Path(prepared.path)
            tip = gitrepo.ref_commit(clone, f"refs/heads/{branch}", runner=read, no_optional_locks=True)
            if tip is None:
                raise _refuse("not_a_descendant", f"the clone has no branch {branch}")
            state["tip"] = tip
            if tip == base:
                return Landing("nothing", NOTHING_TO_LAND, base, tip, branch)
            state["commits"] = _check_range(clone, base, tip, read)
            try:
                on_intent(tip)
            except Exception as exc:  # noqa: BLE001 -- the callback is the caller's; nothing has been fetched
                raise _refuse("landing_error", f"the intent could not be recorded: {exc}") from exc
            state["fetched"] = True
            try:
                gitrepo.landing_fetch(managed_root, clone, branch, job_id, runner=_runner(layout.base, fetch=True))
            except GitOperationError as exc:
                raise _refuse("fetch_failed", f"the fetch from the clone failed: {exc.message}",
                              evidence=dict(exc.evidence)) from exc
            fetched = gitrepo.ref_commit(managed_root, f"{gitrepo.LANDING_REF_PREFIX}{job_id}", runner=read,
                                         no_optional_locks=True)
            if fetched != tip:
                raise _refuse("tip_moved", f"the clone's branch moved between the checks and the fetch: {tip} was "
                              f"checked, {fetched} was fetched", evidence={"checked": tip, "fetched": fetched})
            # I1 of the external round: the clone's object store is the worker's, and Git reads do not verify an
            # object's id, so the clone-side checks above can be steered (a base object overlaid under its own
            # name). The managed repository holds the authentic base and the fetch verified every object it took
            # (``fsckObjects``), so the range is judged again here, where its ids are what they say.
            state["commits"] = _check_range(managed_root, base, tip, read)
            state["merging"] = True
            try:
                gitrepo.landing_merge(managed_root, tip, runner=read)
            except GitOperationError as exc:
                failed = _classify_after_merge(
                    managed_root, prepared, tip, read, exit_code=exc.evidence.get("exit_code"),
                    text=str(exc.evidence.get("stderr", "")) or exc.message, failure=exc.message)
                if failed is not None:
                    raise failed from exc
                result = Landing("landed", None, base, tip, branch, tuple(state["commits"]), moved=True)
            else:
                reason = verify_move(managed_repo, tip, branch, runner=read)
                if reason is not None:
                    raise _refuse("landing_unverified", reason, moved=True)
                result = Landing("landed", None, base, tip, branch, tuple(state["commits"]), moved=True)
        except CodexLandingError:
            raise
        except (GitOperationError, OSError, subprocess.TimeoutExpired) as exc:
            if state["merging"]:
                failed = _classify_after_merge(managed_root, prepared, state["tip"], read, exit_code=None,
                                               text="", failure=str(getattr(exc, "message", exc)))
                if failed is None:
                    result = Landing("landed", None, base, state["tip"], branch, tuple(state["commits"]), moved=True)
                else:
                    raise failed from exc
            else:
                raise _refuse("landing_error", f"an unexpected failure: {getattr(exc, 'message', exc)}",
                              evidence=dict(getattr(exc, "evidence", {}) or {})) from exc
    except CodexLandingError as refusal:
        result = Landing("refused", refusal.cause, base, state["tip"], branch, tuple(state["commits"]) if
                         refusal.moved else (), moved=refusal.moved, dirty=tuple(refusal.dirty),
                         message=refusal.message)
    finally:
        if state["fetched"]:
            try:
                gitrepo.landing_delete_ref(managed_root, job_id, runner=read)
            except (GitOperationError, OSError, subprocess.TimeoutExpired):
                ref_left = True
    assert result is not None
    if not ref_left:
        return result
    return Landing(result.outcome, result.cause, result.base, result.tip, result.branch, result.commits,
                   result.moved, True, result.dirty, result.message, f"{gitrepo.LANDING_REF_PREFIX}{job_id}")


# ---------------------------------------------------------------------------
# Recovery (D7): the deciding half.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Recovery:
    """What :func:`decide` says about a ``COMPLETED`` Codex record: the final ``landing`` block, whether to delete the
    landing ref and to remove the tree, and whether the record ends ``FAILED`` ``CodexLandingRefused`` (``refused``)
    or continues to the reconciliation. ``decide`` only reads; CP3's applying part writes."""

    landing: dict
    cause: str | None
    refused: bool
    delete_ref: bool
    remove_tree: bool
    clone: str | None
    message: str = ""


def _unreadable(clone: Any, base: Any, branch: Any, reason: str) -> Recovery:
    """``clone`` comes from a block that may be malformed: anything but a string is no path (``None``), and the
    applying part derives the path from the job id."""
    path = clone if isinstance(clone, str) else None
    block = {"outcome": "refused", "cause": "landing_unreadable", "base": base, "tip": None, "commits": [],
             "branch": branch, "clone": path, "clone_kept": True, "moved": None, "ref_left": False, "dirty": [],
             "message": f"the branch could not be read: {reason}"}
    return Recovery(block, "landing_unreadable", True, False, False, path, block["message"])


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and 40 <= len(value) <= 64 and all(c in "0123456789abcdef" for c in value)


def decide(managed_repo: Any, record: dict) -> Recovery:
    """D7's decision over a ``COMPLETED`` Codex record, in order: (d) a final ``landed``/``nothing`` block is already
    there; else by the intent block, (a) the branch still at the base, (b) at the intent's ``tip`` (through
    :func:`verify_move`), (c) at neither. Reads refs and the tree only (``GIT_OPTIONAL_LOCKS=0``, commit graph off) and
    never raises: a malformed ``clone`` or ``landing`` block, an unreadable managed root or a garbage branch ref is a
    :class:`Recovery` of cause ``landing_unreadable``, the clone kept."""
    clone_path: str | None = None
    base: Any = None
    branch: Any = None
    try:
        clone = record["codex_worker"]["clone"]
        clone_path, branch, base = clone["path"], clone["branch"], clone["base"]
        if not (isinstance(clone_path, str) and isinstance(branch, str) and branch and _is_sha(base)):
            raise ValueError("the clone block is malformed")
        block = record["codex_worker"].get("landing")
        if block is not None and not isinstance(block, dict):
            raise ValueError("the landing block is malformed")
        outcome = None if block is None else block.get("outcome")
        if block is not None and outcome not in ("started", "landed", "nothing"):
            raise ValueError(f"the landing block's outcome is {outcome!r}")
        if outcome in ("landed", "nothing"):
            # (d): the Controller was killed after the final write and before the terminal write.
            return Recovery(dict(block), None, False, False, True, clone_path)
        intent_tip = None
        if outcome == "started":
            intent_tip = block.get("tip")
            if not _is_sha(intent_tip) or block.get("base") != base or block.get("branch") != branch:
                raise ValueError("the intent block is malformed")
        root = Path(managed_repo.root)
        job_id = str(record.get("job_id") or "")
        runner = _runner(Path(clone_path).parent)
        branch_commit = gitrepo.ref_commit(root, f"refs/heads/{branch}", runner=runner, no_optional_locks=True)
        if branch_commit is None:
            raise ValueError(f"the branch {branch} cannot be read")
        delete_ref = bool(job_id)

        def refused(cause: str, message: str, *, moved: bool, dirty: list[str] | None = None) -> Recovery:
            out = {"outcome": "refused", "cause": cause, "base": base, "tip": intent_tip, "commits": [],
                   "branch": branch, "clone_kept": True, "moved": moved, "ref_left": False,
                   "dirty": list(dirty or []), "message": message}
            return Recovery(out, cause, True, delete_ref, False, clone_path, message)

        if branch_commit == base:  # (a)
            dirty = [*gitrepo.tracked_changes(root, runner=runner, no_optional_locks=True),
                     *gitrepo.untracked_files(root, runner=runner, no_optional_locks=True)]
            if dirty and intent_tip is not None:
                return refused("merge_left_tree_dirty", "the merge was interrupted between its tree update and its "
                               "ref update; nothing was repaired", moved=False, dirty=dirty)
            return refused("landing_interrupted", "the landing was interrupted before the branch moved",
                           moved=False, dirty=dirty)
        if intent_tip is not None and branch_commit == intent_tip:  # (b)
            reason = verify_move(managed_repo, intent_tip, branch, runner=runner)
            if reason is not None:
                return refused("landing_unverified", reason, moved=True)
            commits = [c for c, _ in gitrepo.range_commits(root, base, intent_tip, MAX_LANDED_COMMITS + 1,
                                                           runner=runner)]
            out = {"outcome": "landed", "cause": None, "base": base, "tip": intent_tip, "commits": commits,
                   "branch": branch, "clone_kept": False, "moved": True, "ref_left": False, "dirty": [],
                   "recovered": True}
            return Recovery(out, None, False, delete_ref, True, clone_path)
        return refused("landing_unverified",  # (c)
                       f"the managed branch is at {branch_commit}, neither the base nor the intent's tip",
                       moved=True)
    except (GitOperationError, OSError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError, AttributeError) \
            as exc:
        return _unreadable(clone_path, base, branch, str(getattr(exc, "message", exc)))


def recover(managed_repo: Any, record: dict) -> Recovery:
    """The helper of D7 that every path ending a ``COMPLETED`` Codex record calls: the decision part. The applying
    part (persist the block, delete the landing ref, remove the tree) is ``controller/job.py``'s (CP3)."""
    return decide(managed_repo, record)
