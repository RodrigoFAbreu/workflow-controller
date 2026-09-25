"""The target-repository Git boundary (``workflow-controller-trunk-branch-pr-
release-orchestration`` CP3, "Target-repository Git boundary").

Every Git call the Controller makes against a *target* repository's refs --
reads, fetches, branch creation and switches, pushes, tags and the
integration merge -- goes through this module, and every mutating Git
spelling in ``controller/`` lives here (``tests/test_no_rewrite_invariants``
enforces both). Each function takes the repository root and an injectable
``runner`` (``argv -> CompletedProcess`` with bytes output; the default,
:func:`subprocess_runner`, runs the real ``git``), returns a typed result,
and raises :class:`~controller.errors.GitOperationError` carrying the argv,
exit code and stderr.

What it never does (I2): force anything, ``reset``, rebase, amend, delete a
ref, or move a tag. A push is a plain refspec push -- for a branch only
after a local fast-forward check against the freshly fetched remote ref, for
a tag only when the remote does not have it. A rejected push is re-read and
reported, never retried.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from .errors import GitOperationError

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

#: Generous: a fetch or push over a slow network is still a bounded wait.
DEFAULT_TIMEOUT_SECONDS = 600


def subprocess_runner(env: dict[str, str] | None = None, *,
                      timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Runner:
    """A runner executing ``argv`` for real, with stdin closed, bytes output,
    ``LC_ALL=C`` (so stderr fragments are stable) and no credential or
    terminal prompt. ``env`` replaces ``os.environ`` as the base."""
    base = dict(os.environ if env is None else env)
    base.update({"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"})

    def run(argv: Sequence[str]) -> subprocess.CompletedProcess:
        return subprocess.run(list(argv), capture_output=True, stdin=subprocess.DEVNULL,
                              check=False, env=base, timeout=timeout)

    return run


_DEFAULT_RUNNER = subprocess_runner()


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def _failure(message: str, argv: Sequence[str], result: subprocess.CompletedProcess | None = None,
             **evidence) -> GitOperationError:
    details = {"argv": list(argv)}
    if result is not None:
        details.update(exit_code=result.returncode, stderr=_text(result.stderr).strip())
    details.update(evidence)
    return GitOperationError(message, evidence=details)


def _run(repo_root: Path, args: Sequence[str], runner: Runner | None) -> tuple[list[str], subprocess.CompletedProcess]:
    argv = ["git", "-C", str(repo_root), *args]
    try:
        result = (runner or _DEFAULT_RUNNER)(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _failure(f"git {args[0]} could not run: {exc}", argv) from None
    return argv, result


def _git(repo_root: Path, args: Sequence[str], runner: Runner | None) -> str:
    """``git -C <repo_root> <args>``'s stdout; any non-zero exit refuses."""
    argv, result = _run(repo_root, args, runner)
    if result.returncode != 0:
        raise _failure(f"git {args[0]} failed (exit {result.returncode}): "
                       f"{_text(result.stderr).strip()}", argv, result)
    return _text(result.stdout)


def _probe(repo_root: Path, args: Sequence[str], runner: Runner | None, *,
           absent: int = 1, absent_stderr: str | None = None) -> str | None:
    """Like :func:`_git`, but exit ``absent`` with empty stdout -- and either
    empty stderr or stderr containing ``absent_stderr`` -- is "no such
    thing" (``None``) rather than a failure. Any other outcome refuses."""
    argv, result = _run(repo_root, args, runner)
    if result.returncode == 0:
        return _text(result.stdout)
    stderr = _text(result.stderr)
    expected = (absent_stderr in stderr) if absent_stderr is not None else not stderr.strip()
    if result.returncode == absent and not result.stdout and expected:
        return None
    raise _failure(f"git {args[0]} failed (exit {result.returncode}): "
                   f"{_text(result.stderr).strip()}", argv, result)


def _single_line(output: str, argv_hint: str) -> str:
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise GitOperationError(f"{argv_hint} returned unexpected output: {output!r}",
                                evidence={"argv": argv_hint, "stdout": output})
    return lines[0]


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class HeadState:
    """``branch`` is the short name of the branch ``HEAD`` is attached to,
    or ``None`` when detached. ``commit`` is ``None`` on an unborn branch."""

    branch: str | None
    commit: str | None

    @property
    def detached(self) -> bool:
        return self.branch is None


def head_state(repo_root: Path, *, runner: Runner | None = None) -> HeadState:
    ref = _probe(repo_root, ["symbolic-ref", "-q", "HEAD"], runner)
    branch = None
    if ref is not None:
        full = _single_line(ref, "git symbolic-ref HEAD")
        if not full.startswith("refs/heads/"):
            raise GitOperationError(f"HEAD points at {full}, not a branch",
                                    evidence={"symbolic_ref": full})
        branch = full[len("refs/heads/"):]
    return HeadState(branch=branch, commit=ref_commit(repo_root, "HEAD", runner=runner))


def ref_commit(repo_root: Path, ref: str, *, runner: Runner | None = None) -> str | None:
    """The commit ``ref`` resolves to (peeling tags), or ``None`` when no
    such ref exists."""
    out = _probe(repo_root, ["rev-parse", "-q", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
                 runner)
    return None if out is None else _single_line(out, f"git rev-parse {ref}")


def tag_commit(repo_root: Path, tag: str, *, runner: Runner | None = None) -> str | None:
    """The commit the local tag ``tag`` points at (peeled), or ``None``."""
    return ref_commit(repo_root, f"refs/tags/{tag}", runner=runner)


def is_ancestor(repo_root: Path, ancestor: str, descendant: str, *, runner: Runner | None = None) -> bool:
    """Whether ``ancestor`` is an ancestor of (or equal to) ``descendant``."""
    argv, result = _run(repo_root, ["merge-base", "--is-ancestor", ancestor, descendant], runner)
    if result.returncode in (0, 1) and not result.stderr:
        return result.returncode == 0
    raise _failure(f"cannot decide whether {ancestor} is an ancestor of {descendant}", argv, result)


def ahead_behind(repo_root: Path, a: str, b: str, *, runner: Runner | None = None) -> tuple[int, int]:
    """``(ahead, behind)``: commits reachable from ``a`` but not ``b``, and
    from ``b`` but not ``a``."""
    out = _git(repo_root, ["rev-list", "--left-right", "--count", f"{a}...{b}"], runner)
    fields = _single_line(out, "git rev-list --count").split()
    if len(fields) != 2 or not all(f.isdigit() for f in fields):
        raise GitOperationError(f"git rev-list --count returned {out!r}", evidence={"stdout": out})
    return int(fields[0]), int(fields[1])


def merge_base(repo_root: Path, a: str, b: str, *, runner: Runner | None = None) -> str | None:
    """The best common ancestor of ``a`` and ``b``, or ``None`` when they
    share no history."""
    out = _probe(repo_root, ["merge-base", a, b], runner)
    return None if out is None else _single_line(out, "git merge-base")


def first_parent_log(repo_root: Path, a: str, b: str, *, runner: Runner | None = None) -> list[str]:
    """The commits of ``a..b`` along ``b``'s first-parent chain, oldest
    first."""
    out = _git(repo_root, ["rev-list", "--first-parent", "--reverse", f"{a}..{b}"], runner)
    return out.split()


def tracked_changes(repo_root: Path, *, runner: Runner | None = None) -> list[str]:
    """``git status --porcelain --untracked-files=no`` entries, one per
    changed tracked path; empty for a clean tracked tree."""
    out = _git(repo_root, ["status", "--porcelain", "--untracked-files=no", "-z"], runner)
    entries, fields = [], out.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        entries.append(entry)
        if entry[:1] in ("R", "C") or entry[1:2] in ("R", "C"):
            i += 1  # a rename/copy's source path follows as its own field
    return entries


def common_dir(repo_root: Path, *, runner: Runner | None = None) -> Path:
    """The absolute Git common directory, shared by every linked worktree."""
    out = _git(repo_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"], runner)
    return Path(_single_line(out, "git rev-parse --git-common-dir"))


def remote_url(repo_root: Path, remote: str, *, runner: Runner | None = None) -> str | None:
    """``remote``'s fetch URL, or ``None`` when no such remote exists."""
    out = _probe(repo_root, ["remote", "get-url", "--", remote], runner, absent=2,
                 absent_stderr="No such remote")
    return None if out is None else _single_line(out, "git remote get-url")


def ls_remote(repo_root: Path, remote: str, refs: Sequence[str], *,
              runner: Runner | None = None) -> dict[str, str]:
    """``{full ref name: commit}`` for each of ``refs`` the remote has, an
    annotated tag peeled to its commit. A ref the remote lacks is absent
    from the mapping. Contacts the remote; never updates a local ref."""
    wanted = set(refs)
    for ref in wanted:
        if not ref.startswith("refs/"):
            raise GitOperationError(f"ls_remote needs full ref names, got {ref!r}",
                                    evidence={"ref": ref})
    # A pattern does not match its own peeled ``^{}`` line; ask for both.
    patterns = [p for ref in sorted(wanted) for p in (ref, f"{ref}^{{}}")]
    out = _git(repo_root, ["ls-remote", "--", remote, *patterns], runner)
    # Pattern matching is by trailing path components; keep exact names only.
    return {name: oid for name, oid in _peeled_listing(out).items() if name in wanted}


def ls_remote_tags(repo_root: Path, remote: str, *, runner: Runner | None = None) -> dict[str, str]:
    """``{tag short name: commit}`` for every tag ``remote`` has, an
    annotated tag peeled to its commit. Contacts the remote; never updates a
    local ref."""
    out = _git(repo_root, ["ls-remote", "--tags", "--", remote], runner)
    tags = {}
    for name, oid in _peeled_listing(out).items():
        if not name.startswith("refs/tags/"):
            raise GitOperationError(f"git ls-remote --tags returned {name!r}", evidence={"stdout": out})
        tags[name[len("refs/tags/"):]] = oid
    return tags


def _peeled_listing(out: str) -> dict[str, str]:
    """``git ls-remote`` output as ``{ref: oid}``, each ``^{}`` line
    replacing its ref's own object."""
    direct: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in out.splitlines():
        oid, sep, name = line.partition("\t")
        if not sep or len(oid) not in (40, 64):
            raise GitOperationError(f"git ls-remote returned an unexpected line: {line!r}",
                                    evidence={"stdout": out})
        if name.endswith("^{}"):
            peeled[name[:-3]] = oid
        else:
            direct[name] = oid
    return {name: peeled.get(name, oid) for name, oid in direct.items()}


def show(repo_root: Path, rev: str, path: str, *, runner: Runner | None = None) -> bytes | None:
    """The bytes of ``path`` in ``rev``'s tree, or ``None`` when that tree
    has no such file. An unknown ``rev``, or a non-file entry, refuses."""
    listing = _git(repo_root, ["ls-tree", "-z", "--end-of-options", rev, "--", path], runner)
    if not listing:
        return None
    entries = [entry for entry in listing.split("\0") if entry]
    meta, _, name = entries[0].partition("\t")
    fields = meta.split()
    if len(entries) != 1 or name != path or len(fields) != 3:
        raise GitOperationError(f"git ls-tree returned an unexpected listing for {path} at {rev}",
                                evidence={"stdout": listing, "rev": rev, "path": path})
    mode, kind, oid = fields
    if kind != "blob" or mode not in ("100644", "100755"):
        raise GitOperationError(f"{path} at {rev} is a {kind} with mode {mode}, not a regular file",
                                evidence={"rev": rev, "path": path, "mode": mode, "kind": kind})
    argv, result = _run(repo_root, ["cat-file", "blob", oid], runner)
    if result.returncode != 0:
        raise _failure(f"cannot read {path} ({oid}) at {rev}", argv, result)
    return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()


def commit_trailers(repo_root: Path, commit: str, *, runner: Runner | None = None) -> list[tuple[str, str]]:
    """The ``(key, value)`` trailers of ``commit``'s message, in order --
    the same last-paragraph rule ``git interpret-trailers --parse`` uses."""
    out = _git(repo_root, ["show", "-s", "--format=%(trailers:only=true,unfold=true)",
                           "--end-of-options", commit], runner)
    trailers = []
    for line in out.splitlines():
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise GitOperationError(f"unexpected trailer line {line!r} in {commit}",
                                    evidence={"commit": commit, "stdout": out})
        trailers.append((key.strip(), value.strip()))
    return trailers


def worktree_branches(repo_root: Path, *, runner: Runner | None = None) -> dict[str, str | None]:
    """``{worktree path: branch short name, or None when detached}`` for
    every worktree of the repository (the main one included; a bare main
    repository is omitted). Read-only."""
    out = _git(repo_root, ["worktree", "list", "--porcelain", "-z"], runner)
    result: dict[str, str | None] = {}
    record: dict[str, str] = {}

    def close() -> None:
        if not record:
            return
        if "worktree" not in record:
            raise GitOperationError(f"git worktree list returned a record without a path: {record}",
                                    evidence={"stdout": out})
        if "bare" not in record:
            branch = record.get("branch")
            if branch is not None and not branch.startswith("refs/heads/"):
                raise GitOperationError(f"worktree {record['worktree']} is on {branch}",
                                        evidence={"stdout": out})
            result[record["worktree"]] = None if branch is None else branch[len("refs/heads/"):]
        record.clear()

    for field in out.split("\0"):
        if not field:
            close()
            continue
        key, _, value = field.partition(" ")
        record[key] = value
    close()
    return result


# ---------------------------------------------------------------------------
# Fetch.
# ---------------------------------------------------------------------------


def _check_fetch_refspec(remote: str, refspec: str) -> None:
    src, sep, dst = refspec.partition(":")
    ok = (sep and src.startswith("refs/") and not refspec.startswith("+")
          and (dst.startswith(f"refs/remotes/{remote}/")
               or (src == "refs/tags/*" and dst == "refs/tags/*")))
    if not ok:
        raise GitOperationError(
            f"refusing fetch refspec {refspec!r}: only a non-forced refspec into "
            f"refs/remotes/{remote}/, or refs/tags/*:refs/tags/*, is allowed",
            evidence={"refspec": refspec, "remote": remote})


def fetch(repo_root: Path, remote: str, refspecs: Sequence[str], *, runner: Runner | None = None) -> None:
    """Fetch explicit ``refspecs`` from ``remote``: each into
    ``refs/remotes/<remote>/...``, or the one tag refspec
    ``refs/tags/*:refs/tags/*``, which fails rather than overwrites when a
    local tag differs. Never ``--force``, never ``--prune``, never onto a
    local branch."""
    if not refspecs:
        raise GitOperationError("fetch needs at least one refspec", evidence={"remote": remote})
    for refspec in refspecs:
        _check_fetch_refspec(remote, refspec)
    _git(repo_root, ["fetch", "--no-tags", "--no-write-fetch-head", "--", remote, *refspecs], runner)


def fetch_branch(repo_root: Path, remote: str, branch: str, *, runner: Runner | None = None) -> str:
    """Fetch ``branch`` into ``refs/remotes/<remote>/<branch>`` and return
    the commit it now names."""
    tracking = f"refs/remotes/{remote}/{branch}"
    fetch(repo_root, remote, [f"refs/heads/{branch}:{tracking}"], runner=runner)
    commit = ref_commit(repo_root, tracking, runner=runner)
    if commit is None:
        raise GitOperationError(f"{tracking} is missing right after fetching it",
                                evidence={"ref": tracking})
    return commit


# ---------------------------------------------------------------------------
# Branches.
# ---------------------------------------------------------------------------


def _require_clean(repo_root: Path, action: str, runner: Runner | None) -> None:
    changes = tracked_changes(repo_root, runner=runner)
    if changes:
        raise GitOperationError(f"refusing to {action}: the tracked tree has changes",
                                evidence={"tracked_changes": changes})


def create_and_switch(repo_root: Path, branch: str, *, runner: Runner | None = None) -> None:
    """``git switch -c <branch>`` at ``HEAD``, carrying the working tree.
    Refuses when the branch already exists."""
    if ref_commit(repo_root, f"refs/heads/{branch}", runner=runner) is not None:
        raise GitOperationError(f"refusing to create {branch}: it already exists",
                                evidence={"branch": branch})
    _git(repo_root, ["switch", "-c", branch], runner)


def switch(repo_root: Path, branch: str, *, runner: Runner | None = None) -> None:
    """``git switch <branch>`` (close-out only). Requires a clean tracked
    tree; never discards changes."""
    _require_clean(repo_root, f"switch to {branch}", runner)
    _git(repo_root, ["switch", "--no-guess", branch], runner)


def fast_forward(repo_root: Path, branch: str, onto: str, *, runner: Runner | None = None) -> str:
    """Fast-forward ``branch`` (which must be checked out) to ``onto``
    (``merge --ff-only``) and return the new tip. Refuses anything that is
    not a fast-forward."""
    state = head_state(repo_root, runner=runner)
    if state.branch != branch:
        raise GitOperationError(f"refusing to fast-forward {branch}: HEAD is on "
                                f"{state.branch or 'a detached commit'}",
                                evidence={"branch": branch, "head_branch": state.branch})
    _git(repo_root, ["merge", "--ff-only", "--no-edit", onto], runner)
    return head_state(repo_root, runner=runner).commit


def push_branch(repo_root: Path, remote: str, branch: str, *, runner: Runner | None = None) -> str:
    """Push the local ``branch`` to the same name on ``remote`` as a
    fast-forward, returning the pushed commit. The remote ref is fetched
    first; unless it is absent or an ancestor of the local tip the push is
    refused locally, without contacting the remote again. The push itself
    is a plain refspec -- no ``+``, no ``--force*``."""
    local = ref_commit(repo_root, f"refs/heads/{branch}", runner=runner)
    if local is None:
        raise GitOperationError(f"refusing to push {branch}: no such local branch",
                                evidence={"branch": branch})
    remote_ref = f"refs/heads/{branch}"
    if remote_ref in ls_remote(repo_root, remote, [remote_ref], runner=runner):
        remote_commit = fetch_branch(repo_root, remote, branch, runner=runner)
        if not is_ancestor(repo_root, remote_commit, local, runner=runner):
            raise GitOperationError(
                f"refusing to push {branch}: {remote}'s {branch} ({remote_commit}) is not an "
                f"ancestor of the local tip ({local})",
                evidence={"branch": branch, "remote": remote, "remote_commit": remote_commit,
                          "local_commit": local, "reason": "not_fast_forward"})
    _git(repo_root, ["push", "--porcelain", "--", remote, f"{remote_ref}:{remote_ref}"], runner)
    return local


# ---------------------------------------------------------------------------
# Tags.
# ---------------------------------------------------------------------------


def create_annotated_tag(repo_root: Path, tag: str, commit: str, message: str, *,
                         tagger: tuple[str, str] | None = None, runner: Runner | None = None) -> None:
    """Create the annotated tag ``tag`` at ``commit``, as ``tagger``
    (``(name, email)``) when given, else the configured identity. Refuses
    when a local tag of that name exists, at any commit."""
    existing = tag_commit(repo_root, tag, runner=runner)
    if existing is not None:
        raise GitOperationError(f"refusing to create tag {tag}: it already exists at {existing}",
                                evidence={"tag": tag, "local_commit": existing, "reason": "exists"})
    identity = [] if tagger is None else ["-c", f"user.name={tagger[0]}", "-c", f"user.email={tagger[1]}"]
    _git(repo_root, identity + ["tag", "-a", "-m", message, "--", tag, commit], runner)


def remote_tag_commit(repo_root: Path, remote: str, tag: str, *, runner: Runner | None = None) -> str | None:
    """The commit ``remote``'s tag ``tag`` points at (peeled), or ``None``."""
    return ls_remote(repo_root, remote, [f"refs/tags/{tag}"], runner=runner).get(f"refs/tags/{tag}")


def push_tag(repo_root: Path, remote: str, tag: str, *, runner: Runner | None = None) -> str:
    """Push the local tag ``tag`` to ``remote`` as a new ref
    (``refs/tags/T:refs/tags/T``, no ``+``) and return its commit. Refuses
    when the remote already has the tag -- at any commit, the same one
    included: ``evidence['reason'] == 'exists'`` with ``remote_commit``. A
    rejected push is re-read and reported, never retried."""
    local = tag_commit(repo_root, tag, runner=runner)
    if local is None:
        raise GitOperationError(f"refusing to push tag {tag}: no such local tag", evidence={"tag": tag})
    existing = remote_tag_commit(repo_root, remote, tag, runner=runner)
    if existing is not None:
        raise GitOperationError(f"refusing to push tag {tag}: {remote} already has it at {existing}",
                                evidence={"tag": tag, "remote": remote, "remote_commit": existing,
                                          "local_commit": local, "reason": "exists"})
    ref = f"refs/tags/{tag}"
    argv, result = _run(repo_root, ["push", "--porcelain", "--", remote, f"{ref}:{ref}"], runner)
    if result.returncode != 0:
        after = remote_tag_commit(repo_root, remote, tag, runner=runner)
        raise _failure(f"pushing tag {tag} to {remote} was rejected; the remote now has it "
                       f"{'at ' + after if after else 'absent'}", argv, result,
                       tag=tag, remote=remote, remote_commit=after, local_commit=local,
                       reason="exists" if after else "rejected")
    return local


# ---------------------------------------------------------------------------
# The integration primitive.
# ---------------------------------------------------------------------------


def merge_trunk(repo_root: Path, remote: str, trunk: str, *, runner: Runner | None = None) -> str:
    """Merge ``<remote>/<trunk>`` into the current branch with a
    history-preserving merge commit (``--no-ff``, first parent the branch)
    and return it. Requires a clean tracked tree. On conflict, aborts the
    merge -- leaving the tree as it was -- and refuses naming the
    conflicting paths.

    Wired into no lifecycle path in this milestone (see the plan's
    "Workflow / Controller boundary")."""
    _require_clean(repo_root, f"merge {remote}/{trunk}", runner)
    target = f"refs/remotes/{remote}/{trunk}"
    argv, result = _run(repo_root, ["merge", "--no-ff", "--no-edit", target], runner)
    if result.returncode == 0:
        return head_state(repo_root, runner=runner).commit
    conflicts = _git(repo_root, ["diff", "--name-only", "--diff-filter=U", "-z"], runner)
    paths = [p for p in conflicts.split("\0") if p]
    in_merge = _probe(repo_root, ["rev-parse", "-q", "--verify", "MERGE_HEAD"], runner) is not None
    if in_merge:
        _git(repo_root, ["merge", "--abort"], runner)
    raise _failure(f"merging {remote}/{trunk} failed"
                   + (f" with conflicts in {', '.join(paths)}; the merge was aborted" if paths else ""),
                   argv, result, conflicts=paths, aborted=in_merge)
