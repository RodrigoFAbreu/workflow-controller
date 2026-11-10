"""The carry of an apply role's uncommitted working set into a Codex clone and back (``workflow-controller-h4e3``, CP2,
D3 to D7).

``apply-plan-review`` and ``apply-implementation-review`` change work a clone made from a commit cannot see: the plan
files and the state file (intent-to-add or uncommitted), and the ignored ``.ai-review/<work item>/`` tree. The carry is
the Controller's own act of putting a bounded, named set of working-set paths into the clone before the worker starts
and bringing exactly the changed members of that set back when it ends, every check made before any write to the
managed repository, and recoverable after a kill. Nothing here runs Codex, lands a commit, regenerates a bundle or runs
a script of the managed repository.

The set is :class:`CarrySet` (D3), computed from the managed state, never from the worker. The lifecycle is
:func:`start_block`, :func:`commit_pending` (D4), :func:`carry_in` (D5), :func:`check_out` then :func:`write_out`
(D6, with D6a for the state file) and :func:`recover` (D7); the job (CP3) calls them and keeps the ``codex_carry``
block in its record through the ``persist`` callback each takes. A failure is a :class:`CarryError` with one cause of
:data:`CAUSES`.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import codex_review_role, codex_worker_sandbox, codex_worker_tree, gitrepo, review_recorder, runtime
from .errors import ControllerError, GitOperationError

#: The two roles, and the stage each one's Workflow command works at.
ROLES = {"apply-plan-review": "plan", "apply-implementation-review": "implementation"}

#: One cause per refusal (D3 to D7); a test fails when one has no case.
CAUSES = (
    "carry_foreign_dirt", "carry_commit_pending_failed", "carry_member_refused", "carry_unexpected_commit",
    "clone_git_changed", "carry_index_changed", "carry_out_of_set", "carry_managed_changed", "carry_state_foreign",
    "carry_clone_gone",
)

#: The details of ``carry_member_refused`` (D5, D6): one per check.
MEMBER_DETAILS = ("name", "kind", "git", "gitlink", "escape", "bound", "armour", "absent")

#: The block's stages, in order (D7).
STAGES_IN_ORDER = ("preparing", "carried_in", "worker_ran", "out", "done")

STATE_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"
ITEM_ROOT = ".ai-review"

#: D3, carried in: the directories of ``.ai-review/<work item>/`` a worker reads.
IN_DIRS = ("feedback", "plan-inputs", "current")

#: D3, carried out: the author-written inputs of the role's next regeneration, under ``.ai-review/<work item>/``.
OUT_MEMBERS = {
    "plan": ("plan-inputs/REVIEW_REQUEST.md", "plan-inputs/TEST_RESULTS.md", "plan-inputs/CONTEXT_FILES.txt"),
    "implementation": ("current/REVIEW_REQUEST.md", "current/IMPLEMENTATION_SUMMARY.md", "current/TEST_RESULTS.md",
                       "current/CONTEXT_FILES.txt", "current/PLAN.md"),
}

#: D3, derived: what the generators write under ``.ai-review/<work item>/`` that is never carried out or judged. The
#: names are the Workflow's own (``workflow_fingerprint``); :func:`workflow_names` reads them from the verified source so
#: a release that adds one is seen, and a test pins this list to the repository's copy.
ITEM_ROOT_DERIVED = (".pin", "AMENDMENT_DIFF.patch", "CHANGES_SINCE_REVIEW.md", "review-bundle.tar.gz",
                     "review-bundle.tar.gz.tmp", "REJECTED", "review-baselines")
ITEM_ROOT_DERIVED_PREFIXES = ("current.staging-", ".pin.staging-", "current.old-", ".pin.old-", "current.rejected-",
                              ".pin.")
#: The generated files of an implementation ``current/`` (for a plan stage all of ``current/`` is derived).
CURRENT_DERIVED = ("MANIFEST.md", "CHANGED_FILES.txt", "DIFF.patch", "COMMITS.txt", "files")

_WORKFLOW_FINGERPRINT_KEY = "scripts/workflow_fingerprint.py"
_WORKFLOW_NAME_CONSTANTS = ("_LEFTOVER_STAGING_PREFIXES", "CHANGES_SINCE_REVIEW_FILE", "STAGE_BASELINE_DIR")

_EVIDENCE_LIST = 20


class CarryError(ControllerError):
    """A refusal of the carry; ``cause`` is one of :data:`CAUSES` and ``evidence`` carries it, with ``detail`` for a
    member refusal."""

    code = "CarryError"

    def __init__(self, cause: str, message: str, **evidence: Any) -> None:
        super().__init__(message, evidence={**evidence, "cause": cause})
        self.cause = cause


def _refuse(cause: str, message: str, **evidence: Any) -> CarryError:
    return CarryError(cause, message, **evidence)


def _head(items: list[str]) -> list[str]:
    return sorted(items)[:_EVIDENCE_LIST]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# D3: the set
# ---------------------------------------------------------------------------


def workflow_names(sources: Mapping[str, bytes] | None) -> dict[str, Any]:
    """The Workflow's own names for derived files, read from the verified source of ``scripts/workflow_fingerprint.py``
    in ``sources`` (the script set's bytes) by parsing it, never by importing it. Empty for no source or one that does
    not parse."""
    data = (sources or {}).get(_WORKFLOW_FINGERPRINT_KEY)
    if data is None:
        return {}
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return {}
    found: dict[str, Any] = {}
    for node in tree.body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        value = getattr(node, "value", None)
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _WORKFLOW_NAME_CONSTANTS and value is not None:
                try:
                    found[target.id] = ast.literal_eval(value)
                except ValueError:
                    continue
    return found


@dataclasses.dataclass(frozen=True)
class CarrySet:
    """D3's closed set for one role and one work item. ``named`` are repository paths carried in and out (the plan
    role's files, the state file among them); ``item_root`` is ``.ai-review/<work item>``; ``out_members`` are the
    repository paths carried out from it; ``derived_names`` and ``derived_prefixes`` are what :meth:`is_derived`
    never judges."""

    role: str
    stage: str
    work_item_id: str
    named: tuple[str, ...]
    item_root: str
    out_members: tuple[str, ...]
    derived_names: tuple[str, ...] = ITEM_ROOT_DERIVED
    derived_prefixes: tuple[str, ...] = ITEM_ROOT_DERIVED_PREFIXES

    @property
    def members(self) -> tuple[str, ...]:
        """Every path judged at carry out and carried back when it changed: the named files, then ``out_members``."""
        return (*self.named, *self.out_members)

    def is_derived(self, rel: str) -> bool:
        """Whether ``rel`` (relative to the item root) is a derived path (D3)."""
        parts = rel.split("/")
        first = parts[0]
        if first in self.derived_names or first.startswith(self.derived_prefixes):
            return True
        if first == "current":
            return self.stage == "plan" or (len(parts) > 1 and parts[1] in CURRENT_DERIVED)
        return False

    def is_member(self, rel: str) -> bool:
        return f"{self.item_root}/{rel}" in self.out_members

    def to_dict(self) -> dict:
        return {"role": self.role, "work_item_id": self.work_item_id, "named": list(self.named),
                "item_root": self.item_root, "out_members": list(self.out_members)}


def compute(repo_root: Path, work_item_id: str, role: str, *, sources: Mapping[str, bytes] | None = None) -> CarrySet:
    """D3: the carry set of ``role`` for ``work_item_id``, from the managed repository's state file (the item's
    ``plan_path``, ``registry_path`` and ``mapping_path``, its artifacts declaration beside the registry). Never from
    the worker. A name the Workflow's verified source adds to its derived list is added to the derived names."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {tuple(ROLES)}, not {role!r}")
    stage = ROLES[role]
    item_root = f"{ITEM_ROOT}/{work_item_id}"
    named: tuple[str, ...] = ()
    if stage == "plan":
        try:
            document = json.loads((Path(repo_root) / STATE_PATH).read_bytes().decode("utf-8"))
            item = document["work_items"][work_item_id]
            registry = item["registry_path"]
            paths = [item["plan_path"], registry, item["mapping_path"],
                     f"{registry.rsplit('/', 1)[0]}/{work_item_id}-artifacts.json" if "/" in registry
                     else f"{work_item_id}-artifacts.json", STATE_PATH]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise _refuse("carry_managed_changed", f"the state file does not give {work_item_id!r}'s carry paths: "
                          f"{exc}", work_item_id=work_item_id) from exc
        named = tuple(dict.fromkeys(paths))
    names = workflow_names(sources)
    prefixes = tuple(names.get("_LEFTOVER_STAGING_PREFIXES") or ())
    extra = tuple(name for name in (names.get("CHANGES_SINCE_REVIEW_FILE"), names.get("STAGE_BASELINE_DIR"))
                  if isinstance(name, str))
    return CarrySet(
        role=role, stage=stage, work_item_id=work_item_id, named=named, item_root=item_root,
        out_members=tuple(f"{item_root}/{member}" for member in OUT_MEMBERS[stage]),
        derived_names=tuple(dict.fromkeys((*ITEM_ROOT_DERIVED, *extra))),
        derived_prefixes=tuple(dict.fromkeys((*ITEM_ROOT_DERIVED_PREFIXES, *prefixes))))


def dirty_paths(repo_root: Path, runner: gitrepo.Runner | None = None) -> list[str]:
    """The managed repository's uncommitted paths as ``git status`` sees them with the default ignore rules: tracked
    changes, intent-to-add paths and untracked files, sorted."""
    try:
        changes = gitrepo.tracked_changes(repo_root, runner=runner, no_optional_locks=True)
        untracked = gitrepo.untracked_files(repo_root, runner=runner, no_optional_locks=True)
    except GitOperationError as exc:
        raise _refuse("carry_foreign_dirt", f"the managed repository could not be read: {exc.message}",
                      **exc.evidence) from exc
    return sorted({*(gitrepo.status_path(entry) for entry in changes), *untracked})


def check_dirty(repo_root: Path, carry_set: CarrySet, runner: gitrepo.Runner | None = None) -> list[str]:
    """D3's second rule, at carry in: every dirty path must be a named file. Returns the dirty named files; a dirty
    path outside the set is ``carry_foreign_dirt`` naming them. The Controller never decides what to do with an
    unrelated working-tree change."""
    dirty = dirty_paths(repo_root, runner)
    foreign = [path for path in dirty if path not in carry_set.named]
    if foreign:
        raise _refuse("carry_foreign_dirt", f"the managed repository has uncommitted paths outside the carry set: "
                      f"{', '.join(_head(foreign))}", paths=_head(foreign), count=len(foreign))
    return dirty


# ---------------------------------------------------------------------------
# D4: the clean tracked tree
# ---------------------------------------------------------------------------


def commit_pending(repo_root: Path, work_item_id: str, pre_step_head: str, *,
                   digests: Mapping[str, str] | None = None, runner: gitrepo.Runner | None = None) -> str:
    """D4 and D4a: have the Workflow's own committer commit a pending ``APPLYING_REVIEW_FEEDBACK`` entry, and return
    the **carry base**: the commit it made, after checking that its only parent is ``pre_step_head`` and that the
    branch is at it, or ``pre_step_head`` when there was nothing to commit. Any other outcome is
    ``carry_commit_pending_failed`` (detail ``branch_moved`` for a commit that is not a child of the pre-step head)."""
    root = Path(repo_root)
    try:
        result = review_recorder.commit_pending(root, work_item_id, digests=digests)
    except ControllerError as exc:
        raise _refuse("carry_commit_pending_failed", f"the pending feedback entry could not be committed: "
                      f"{exc.message}", reason=type(exc).__name__) from exc
    if not result.ok:
        raise _refuse("carry_commit_pending_failed", f"the Workflow refused to commit the pending entry "
                      f"({result.cause}): {result.message}", reason=result.cause, workflow_class=result.workflow_class)
    committed = result.values.get("committed")
    if not committed:
        return pre_step_head
    try:
        parents = gitrepo.commit_parents(root, committed, runner=runner)
        head = gitrepo.head_state(root, runner=runner, no_optional_locks=True)
        branch = gitrepo.ref_commit(root, f"refs/heads/{head.branch}", runner=runner,
                                    no_optional_locks=True) if head.branch else None
    except GitOperationError as exc:
        raise _refuse("carry_commit_pending_failed", f"the commit could not be read: {exc.message}",
                      detail="unreadable", commit=committed) from exc
    if parents != [pre_step_head] or branch != committed:
        raise _refuse("carry_commit_pending_failed", f"the commit {committed} is not the only step from "
                      f"{pre_step_head} on the managed branch", detail="branch_moved", commit=committed,
                      parents=parents, branch_commit=branch, pre_step_head=pre_step_head)
    return committed


# ---------------------------------------------------------------------------
# D5: reading members
# ---------------------------------------------------------------------------


def _member_refused(detail: str, rel: str, message: str, **evidence: Any) -> CarryError:
    return _refuse("carry_member_refused", f"the carried path {rel!r} is refused: {message}", detail=detail,
                   path=rel, **evidence)


def _check_name(rel: str) -> None:
    parts = rel.split("/")
    if not rel or rel.startswith("/") or ".." in parts or "" in parts:
        raise _member_refused("escape", rel, "an absolute path or one with a '..' component")
    if ".git" in parts or ".gitmodules" in parts:
        raise _member_refused("git", rel, "a Git metadata path")
    if codex_review_role._is_credential_name(rel):
        raise _member_refused("name", rel, "named like a credential")


def read_member(root: Path, rel: str, *, check_name: bool = True) -> tuple[bytes, int]:
    """D5: the bytes and mode of the regular file ``<root>/<rel>``, read through one ``O_NOFOLLOW`` descriptor without
    following a link, after the checks of the carry: a name that escapes, a ``.git`` component, a credential-shaped name
    (``check_name``), a symlink or special file, a size over the per-file bound, and a private-key armour line."""
    if check_name:
        _check_name(rel)
    path = Path(root) / rel
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _member_refused("kind", rel, f"it cannot be opened without following a link ({exc})") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _member_refused("kind", rel, "not a regular file")
        if info.st_size > codex_review_role.MAX_INPUT_FILE_BYTES:
            raise _member_refused("bound", rel, f"{info.st_size} bytes, over {codex_review_role.MAX_INPUT_FILE_BYTES}",
                                  size=info.st_size)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(codex_review_role.MAX_INPUT_FILE_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > codex_review_role.MAX_INPUT_FILE_BYTES:
        raise _member_refused("bound", rel, "over the per-file bound")
    if codex_review_role.ARMOUR.search(data):
        raise _member_refused("armour", rel, "it holds a private-key armour line")
    return data, stat.S_IMODE(info.st_mode)


def _walk_files(root: Path, rel: str, found: list[str]) -> None:
    """The regular files at or under ``rel``; a symlink or special file is a ``kind`` refusal."""
    _check_name(rel)
    try:
        mode = os.lstat(Path(root) / rel).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _member_refused("kind", rel, f"it cannot be read ({exc})") from exc
    if stat.S_ISREG(mode):
        found.append(rel)
    elif stat.S_ISDIR(mode):
        with os.scandir(Path(root) / rel) as scan:
            names = sorted(entry.name for entry in scan)
        for name in names:
            _walk_files(root, f"{rel}/{name}", found)
    else:
        raise _member_refused("kind", rel, "neither a regular file nor a directory")


def _gitlinks(root: Path, paths: list[str], runner: gitrepo.Runner | None) -> list[str]:
    out = gitrepo._git(root, ["ls-files", "--stage", "-z", "--", *(f":(literal){p}" for p in paths)], runner,
                       quiet=True) if paths else ""
    return [item.partition("\t")[2] for item in out.split("\0") if item.startswith("160000 ")]


@dataclasses.dataclass(frozen=True)
class CarriedIn:
    """What the carry in read: every file to write into the clone (``files``: repository path -> ``(bytes, mode)``), and
    the digest of each (``digests``)."""

    files: Mapping[str, tuple[bytes, int]]
    digests: Mapping[str, str]


def collect_in(repo_root: Path, carry_set: CarrySet, runner: gitrepo.Runner | None = None) -> CarriedIn:
    """D5: read, in the managed repository and before any clone is made, every member of the set that goes in: the named
    files and the regular files of ``feedback/``, ``plan-inputs/`` and ``current/`` of the item root. A refusal is
    ``carry_member_refused`` with the detail of the failed check; the total is bounded as the review role's inputs are."""
    root = Path(repo_root)
    for rel in carry_set.named:
        _check_name(rel)
    try:
        links = _gitlinks(root, list(carry_set.named), runner)
    except GitOperationError as exc:
        raise _refuse("carry_member_refused", f"the managed index could not be read: {exc.message}",
                      detail="gitlink", **exc.evidence) from exc
    if links:
        raise _member_refused("gitlink", links[0], "a gitlink (a submodule)")
    paths: list[str] = []
    for rel in carry_set.named:
        _walk_files(root, rel, paths)
    for directory in IN_DIRS:
        _walk_files(root, f"{carry_set.item_root}/{directory}", paths)
    if len(paths) > codex_review_role.MAX_INPUT_PATHS:
        raise _member_refused("bound", carry_set.item_root, f"{len(paths)} paths, over "
                              f"{codex_review_role.MAX_INPUT_PATHS}", count=len(paths))
    files: dict[str, tuple[bytes, int]] = {}
    total = 0
    for rel in paths:
        try:
            data, mode = read_member(root, rel)
        except FileNotFoundError as exc:
            raise _member_refused("kind", rel, "it vanished while it was read") from exc
        total += len(data)
        if total > codex_review_role.MAX_INPUT_BYTES:
            raise _member_refused("bound", rel, f"the carried files total over {codex_review_role.MAX_INPUT_BYTES} bytes",
                                  total=total)
        files[rel] = (data, mode)
    return CarriedIn(files=files, digests={rel: _sha(data) for rel, (data, _mode) in files.items()})


def managed_digests(repo_root: Path, carry_set: CarrySet) -> dict[str, str | None]:
    """The managed digest of each member of the set (``None`` for an absent one), read for the guard of D6 and the
    recovery of D7. A member that cannot be read as a regular file is ``carry_member_refused``."""
    found: dict[str, str | None] = {}
    for rel in carry_set.members:
        try:
            found[rel] = _sha(read_member(repo_root, rel, check_name=False)[0])
        except FileNotFoundError:
            found[rel] = None
    return found


# ---------------------------------------------------------------------------
# The block (D7)
# ---------------------------------------------------------------------------


def start_block(carry_set: CarrySet, *, pre_step_head: str, base_commit: str) -> dict:
    """The ``codex_carry`` block as the job records it before anything is made (D4a): the carry set, the pre-step head
    and the carry base, at stage ``preparing``."""
    return {"role": carry_set.role, "work_item_id": carry_set.work_item_id, "stage": "preparing",
            "pre_step_head": pre_step_head, "base_commit": base_commit, "set": carry_set.to_dict(),
            "members": {}, "in_digests": {}, "intent_to_add": [], "managed": {}, "writes": {}}


def _advance(block: dict, persist: Callable[[dict], None] | None, stage: str, **fields: Any) -> None:
    block["stage"] = stage
    block.update(fields)
    if persist is not None:
        persist(copy.deepcopy(block))


def _state_in_rel(layout: codex_worker_sandbox.WorkerLayout) -> str:
    return layout.rel("carry", "state_in.json")


def remove_carry(layout: codex_worker_sandbox.WorkerLayout) -> str | None:
    """Remove the clone and the carry's own files; never raises (the failure is returned as its text)."""
    failures = []
    for rel in (layout.rel("tree"), layout.rel("carry")):
        try:
            runtime.remove_tree(layout.runtime_root, rel)
        except Exception as exc:  # noqa: BLE001 -- a removal failure is named, never raised
            failures.append(f"{type(exc).__name__}: {exc}")
    return "; ".join(failures) or None


def prepared_from_block(block: Mapping[str, Any]) -> codex_worker_tree.Prepared:
    """The :class:`~controller.codex_worker_tree.Prepared` the block recorded for the clone (D7's recovery has no
    other copy)."""
    clone = block["clone"]
    return codex_worker_tree.Prepared(path=Path(clone["path"]), branch=clone["branch"], base=clone["base"],
                                      guard=clone["guard"])


# ---------------------------------------------------------------------------
# D5: carry in
# ---------------------------------------------------------------------------


def carry_in(managed_repo: Any, layout: codex_worker_sandbox.WorkerLayout, carry_set: CarrySet, block: dict, *,
             persist: Callable[[dict], None] | None = None, runner: gitrepo.Runner | None = None
             ) -> codex_worker_tree.Prepared:
    """D5: refuse foreign dirt and every unreadable member before any clone is made, persist the block at ``preparing``,
    make the clone at the carry base (the dirty-tree refusal skipped only because the set accounts for every dirty
    path), write each member into it at its relative path, reproduce the intent-to-add set, record the digests and
    persist the block at ``carried_in``. A refusal after the clone was made removes it."""
    root = Path(managed_repo.root)
    dirty = check_dirty(root, carry_set, runner)
    carried = collect_in(root, carry_set, runner)
    _advance(block, persist, "preparing")
    base = block["base_commit"]
    prepared = codex_worker_tree.prepare(managed_repo, layout, base, allow_dirty=carry_set.named)
    try:
        ita = [path for path in gitrepo.intent_to_add_paths(root, runner=runner, no_optional_locks=True)
               if path in carry_set.named]
        for rel, (data, mode) in carried.files.items():
            runtime.write_carried_file(prepared.path, rel, data, mode=mode)
        gitrepo.add_intent_to_add(prepared.path, ita, runner=codex_worker_tree.scrubbed_runner(layout.base))
        state_digest = None
        if STATE_PATH in carry_set.named and STATE_PATH in carried.files:
            state_digest = _sha(carried.files[STATE_PATH][0])
            runtime.write_bytes(layout.runtime_root, _state_in_rel(layout), carried.files[STATE_PATH][0])
        quiet = codex_worker_tree.scrubbed_runner(layout.base, no_optional_locks=True)
        block["clone"] = {**prepared.to_dict(), "head": gitrepo.head_state(prepared.path, runner=quiet,
                                                                          no_optional_locks=True).commit,
                          "refs": gitrepo.clone_refs(prepared.path, runner=quiet)}
        _advance(block, persist, "carried_in", in_digests=dict(carried.digests), intent_to_add=ita,
                 managed=managed_digests(root, carry_set), state_in_sha256=state_digest, dirty_at_start=dirty)
    except CarryError:
        remove_carry(layout)
        raise
    except (OSError, GitOperationError, ControllerError) as exc:
        remove_carry(layout)
        raise _refuse("carry_member_refused", f"a member could not be carried into the clone: {exc}",
                      detail="kind") from exc
    except BaseException:
        remove_carry(layout)
        raise
    return prepared


# ---------------------------------------------------------------------------
# D6: carry out
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Write:
    """One member to write into the managed repository: ``sha256`` and ``mode`` of the clone's bytes."""

    rel: str
    sha256: str
    mode: int


@dataclasses.dataclass(frozen=True)
class OutPlan:
    """The result of :func:`check_out`: the changed non-state members to write, and the state entry to replace (the
    carried-in entry and the worker's) or ``None`` when the state file did not change."""

    writes: tuple[Write, ...]
    state: Mapping[str, Any] | None


def _clone_gone(layout: codex_worker_sandbox.WorkerLayout, block: Mapping[str, Any], root: Path) -> CarryError:
    return _refuse("carry_clone_gone", "the clone is gone before the carry out finished",
                   clone=str(layout.worktree), not_written=not_written(block, root))


def not_written(block: Mapping[str, Any], repo_root: Path) -> list[str]:
    """The members of the block's planned writes the managed repository does not hold yet."""
    pending = []
    for rel, write in (block.get("writes") or {}).items():
        try:
            digest = _sha(read_member(repo_root, rel, check_name=False)[0])
        except (FileNotFoundError, CarryError):
            digest = None
        if digest != write["sha256"]:
            pending.append(rel)
    return sorted(pending)


def _clone_files(clone: Path, carry_set: CarrySet) -> dict[str, str]:
    """Every file under the clone's item root outside the derived paths, as relative path -> ``regular`` or ``other``;
    a derived directory is not entered."""
    base = clone / carry_set.item_root
    found: dict[str, str] = {}
    pending = [""]
    while pending:
        rel_dir = pending.pop()
        try:
            with os.scandir(base / rel_dir if rel_dir else base) as scan:
                children = sorted(scan, key=lambda e: e.name)
        except FileNotFoundError:
            continue
        for child in children:
            rel = f"{rel_dir}/{child.name}" if rel_dir else child.name
            if carry_set.is_derived(rel):
                continue
            mode = child.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                pending.append(rel)
            else:
                found[rel] = "regular" if stat.S_ISREG(mode) else "other"
    return found


def check_out(managed_repo: Any, layout: codex_worker_sandbox.WorkerLayout, carry_set: CarrySet, block: Mapping[str, Any],
              *, prepared: codex_worker_tree.Prepared | None = None, runner: gitrepo.Runner | None = None) -> OutPlan:
    """D6: with the worker ended, and before any write to the managed repository, check in this order -- each with one
    cause -- the plan role's clone ``HEAD`` and refs, the clone's ``.git``, the intent-to-add set, that no path outside
    the set differs from the carried-in state, that every changed member is acceptable and none went absent, and that
    the managed members are still what was recorded; then return the :class:`OutPlan`. Writes nothing."""
    root = Path(managed_repo.root)
    prepared = prepared or prepared_from_block(block)
    clone = prepared.path
    if not (clone / ".git").is_dir():
        raise _clone_gone(layout, block, root)
    quiet = codex_worker_tree.scrubbed_runner(layout.base, no_optional_locks=True)
    try:
        if carry_set.stage == "plan":
            head = gitrepo.head_state(clone, runner=quiet, no_optional_locks=True)
            refs = gitrepo.clone_refs(clone, runner=quiet)
            if head.commit != block["clone"]["head"] or refs != block["clone"]["refs"]:
                raise _refuse("carry_unexpected_commit", "the plan role's clone has a commit or a ref it did not have",
                              head=head.commit, expected_head=block["clone"]["head"], refs=refs)
        try:
            codex_worker_sandbox.check_guard(prepared.guard, codex_worker_sandbox.guard_digest(clone))
        except ControllerError as exc:
            raise _refuse("clone_git_changed", f"the clone's .git changed: {exc.message}", **exc.evidence) from exc
        ita = gitrepo.intent_to_add_paths(clone, runner=quiet, no_optional_locks=True)
        if ita != sorted(block["intent_to_add"]):
            raise _refuse("carry_index_changed", "the clone's intent-to-add set is not the carried-in one",
                          intent_to_add=ita, expected=sorted(block["intent_to_add"]))
        in_digests: Mapping[str, str] = block["in_digests"]
        clone_dirty = {*(gitrepo.status_path(e) for e in gitrepo.tracked_changes(clone, runner=quiet,
                                                                                  no_optional_locks=True)),
                       *gitrepo.untracked_files(clone, runner=quiet, no_optional_locks=True)}
        outside = [path for path in sorted(clone_dirty) if path not in carry_set.named]
        files = _clone_files(clone, carry_set)
        for rel, kind in files.items():
            full = f"{carry_set.item_root}/{rel}"
            if full in carry_set.out_members:
                continue
            same = kind == "regular" and in_digests.get(full) is not None
            if same:
                try:
                    same = _sha(read_member(clone, full, check_name=False)[0]) == in_digests[full]
                except (CarryError, FileNotFoundError):
                    same = False
            if not same:
                outside.append(full)
        for full in in_digests:
            rel = full[len(carry_set.item_root) + 1:] if full.startswith(f"{carry_set.item_root}/") else None
            if (rel is not None and not carry_set.is_derived(rel) and not carry_set.is_member(rel)
                    and rel not in files):
                outside.append(full)
        if outside:
            raise _refuse("carry_out_of_set", f"the clone differs from the carried-in state outside the carry set: "
                          f"{', '.join(_head(outside))}", paths=_head(outside), count=len(outside))
        writes, changed = [], {}
        for rel in carry_set.members:
            try:
                data, mode = read_member(clone, rel, check_name=False)
            except FileNotFoundError:
                if in_digests.get(rel) is not None:
                    raise _member_refused("absent", rel, "a carried member is gone from the clone") from None
                continue
            digest = _sha(data)
            if digest != in_digests.get(rel):
                changed[rel] = data
                writes.append(Write(rel, digest, 0o755 if mode & stat.S_IXUSR else 0o644))
        managed = {rel: digest for rel, digest in managed_digests(root, carry_set).items() if rel != STATE_PATH}
        if managed != {rel: d for rel, d in block["managed"].items() if rel != STATE_PATH}:
            moved = sorted(rel for rel in managed if managed[rel] != block["managed"].get(rel))  # the state file is D6a's
            raise _refuse("carry_managed_changed", f"the managed repository's members changed while the worker ran: "
                          f"{', '.join(moved)}", paths=moved)
        if gitrepo.intent_to_add_paths(root, runner=quiet, no_optional_locks=True) != sorted(
                path for path in block["intent_to_add"]):
            raise _refuse("carry_managed_changed", "the managed intent-to-add set changed while the worker ran")
        state = None
        if STATE_PATH in changed:
            state = _state_payload(layout, block, changed[STATE_PATH], carry_set.work_item_id)
    except GitOperationError as exc:
        raise _refuse("clone_git_changed", f"the clone could not be read: {exc.message}", **exc.evidence) from exc
    return OutPlan(tuple(w for w in writes if w.rel != STATE_PATH), state)


def _state_payload(layout: codex_worker_sandbox.WorkerLayout, block: Mapping[str, Any], worker_bytes: bytes,
                   work_item_id: str) -> dict:
    """D6a: the carried-in entry and the worker's, after checking that nothing else of the state document differs."""
    path = layout.runtime_root / _state_in_rel(layout)
    try:
        original = path.read_bytes()
    except OSError as exc:
        raise _refuse("carry_state_foreign", "the carried-in copy of the state file is gone", path=str(path)) from exc
    if _sha(original) != block.get("state_in_sha256"):
        raise _refuse("carry_state_foreign", "the carried-in copy of the state file is not what was recorded")
    try:
        before_doc, after_doc = json.loads(original.decode("utf-8")), json.loads(worker_bytes.decode("utf-8"))
        before_items, after_items = before_doc["work_items"], after_doc["work_items"]
    except (ValueError, KeyError, TypeError) as exc:
        raise _refuse("carry_state_foreign", f"the worker's state file is not a state document: {exc}") from exc
    if (not isinstance(before_items, dict) or not isinstance(after_items, dict) or work_item_id not in after_items
            or work_item_id not in before_items):
        raise _refuse("carry_state_foreign", f"the worker's state file has no entry for {work_item_id!r}")
    rest_before = {k: v for k, v in before_doc.items() if k != "work_items"}
    rest_after = {k: v for k, v in after_doc.items() if k != "work_items"}
    others_before = {k: v for k, v in before_items.items() if k != work_item_id}
    others_after = {k: v for k, v in after_items.items() if k != work_item_id}
    if rest_before != rest_after or others_before != others_after:
        changed = sorted({*(k for k in {*rest_before, *rest_after} if rest_before.get(k) != rest_after.get(k)),
                          *(f"work_items.{k}" for k in {*others_before, *others_after}
                            if others_before.get(k) != others_after.get(k))})
        raise _refuse("carry_state_foreign", f"the worker's state file differs outside {work_item_id!r}'s entry: "
                      f"{', '.join(changed[:_EVIDENCE_LIST])}", paths=changed[:_EVIDENCE_LIST])
    return {"before": before_items[work_item_id], "after": after_items[work_item_id]}


def write_out(managed_repo: Any, layout: codex_worker_sandbox.WorkerLayout, carry_set: CarrySet, block: dict,
              plan: OutPlan, *, persist: Callable[[dict], None] | None = None,
              digests: Mapping[str, str] | None = None) -> dict:
    """D6 and D7's ``out``: persist the block at ``out`` with the digests to write, then write each changed non-state
    member through a temporary file and an atomic rename, skipping one already at its target digest, refusing
    (``carry_managed_changed``) one at neither its carried-in nor its target digest, and the state file last through
    the recorder's ``carry_state`` (D6a). Persists ``done`` and returns ``{"written": [...], "state_written": bool}``.
    Re-running it after a kill is safe."""
    root = Path(managed_repo.root)
    writes = {w.rel: {"sha256": w.sha256, "mode": w.mode} for w in plan.writes}
    if block["stage"] != "out":
        _advance(block, persist, "out", writes=writes, state=plan.state is not None)
    clone = prepared_from_block(block).path
    written: list[str] = []
    for write in plan.writes:
        try:
            current = _sha(read_member(root, write.rel, check_name=False)[0])
        except FileNotFoundError:
            current = None
        if current == write.sha256:
            continue
        if current != block["in_digests"].get(write.rel) and current != block["managed"].get(write.rel):
            raise _refuse("carry_managed_changed", f"{write.rel} in the managed repository is neither what was carried "
                          f"in nor what the worker wrote", path=write.rel)
        try:
            data, _mode = read_member(clone, write.rel, check_name=False)
        except FileNotFoundError as exc:
            raise _clone_gone(layout, block, root) from exc
        if _sha(data) != write.sha256:
            raise _member_refused("kind", write.rel, "it changed in the clone after the check")
        try:
            runtime.write_carried_file(root, write.rel, data, mode=write.mode)
        except OSError as exc:
            raise _refuse("carry_managed_changed", f"{write.rel} could not be written: {exc}", path=write.rel) from exc
        written.append(write.rel)
    state_written = False
    if plan.state is not None:
        payload = layout.rel("carry", "state_out.json")
        runtime.write_json(layout.runtime_root, payload, dict(plan.state))
        result = review_recorder.carry_state(root, carry_set.work_item_id, layout.runtime_root / payload,
                                             digests=digests)
        if not result.ok:
            raise _refuse("carry_managed_changed" if result.cause == "carry_managed_changed" else
                          "carry_state_foreign", f"the state file was not carried out ({result.cause}): "
                          f"{result.message}", reason=result.cause)
        state_written = bool(result.values.get("written"))
    _advance(block, persist, "done")
    return {"written": written, "state_written": state_written}


# ---------------------------------------------------------------------------
# D7: recovery
# ---------------------------------------------------------------------------


def recover(managed_repo: Any, layout: codex_worker_sandbox.WorkerLayout, carry_set: CarrySet, block: dict, *,
            worker_started: bool, persist: Callable[[dict], None] | None = None,
            digests: Mapping[str, str] | None = None, runner: gitrepo.Runner | None = None) -> dict:
    """D7: finish or end a carry from what is on disk. ``worker_started`` is whether the record names a worker process.
    Returns ``{"action": ..., "stage": ...}`` with ``action`` one of ``not_started`` (``preparing``, or ``carried_in``
    with no worker: the clone is removed and nothing is written), ``carried_out`` (the carry out ran from the kept clone
    and the block is ``done``) or ``done`` (nothing is written). A clone gone before ``done`` is ``carry_clone_gone``,
    nothing further written. Never recomputes D4."""
    stage = block["stage"]
    if stage == "done":
        return {"action": "done", "stage": stage}
    if stage == "preparing" or (stage == "carried_in" and not worker_started):
        remove_carry(layout)
        return {"action": "not_started", "stage": stage}
    if stage not in ("carried_in", "worker_ran", "out"):
        raise ValueError(f"unknown carry stage {stage!r}")
    clone = prepared_from_block(block).path
    if not clone.is_dir():
        raise _clone_gone(layout, block, Path(managed_repo.root))
    if stage == "out":
        pending = _plan_from_block(layout, block, Path(managed_repo.root))
        outcome = write_out(managed_repo, layout, carry_set, block, pending, persist=persist, digests=digests)
    else:
        plan = check_out(managed_repo, layout, carry_set, block, runner=runner)
        outcome = write_out(managed_repo, layout, carry_set, block, plan, persist=persist, digests=digests)
    return {"action": "carried_out", "stage": "done", **outcome}


def _plan_from_block(layout: codex_worker_sandbox.WorkerLayout, block: Mapping[str, Any], root: Path) -> OutPlan:
    """The plan recorded at stage ``out``: the writes of the block, and the state payload re-derived from the kept clone
    through D6a's check (a clone without the state file, or whose copy no longer passes it, is a refusal)."""
    writes = tuple(Write(rel, item["sha256"], item["mode"]) for rel, item in sorted(block["writes"].items()))
    state = None
    if block.get("state"):
        clone = prepared_from_block(block).path
        try:
            data, _mode = read_member(clone, STATE_PATH, check_name=False)
        except FileNotFoundError as exc:
            raise _clone_gone(layout, block, root) from exc
        state = _state_payload(layout, block, data, block["work_item_id"])
    return OutPlan(writes, state)
