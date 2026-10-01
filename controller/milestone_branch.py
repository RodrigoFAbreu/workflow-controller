"""Milestone branch binding (``workflow-controller-trunk-branch-pr-release-
orchestration`` CP6, the plan's "Milestone branch binding").

One short-lived branch per milestone, ``branch_format`` rendered for the work
item (``milestone/<id>`` in the reference policy), bound by a durable record
under the runtime root:

- ``<runtime_root>/repositories/<repo_key>/milestones/<work_item_id>.json`` is
  the binding record. ``repo_key`` is the SHA-256 of the canonical Git common
  directory, so every worktree of a repository shares it;
- ``.../milestones/<work_item_id>/events.jsonl`` is the append-only log of
  every binding of that id, each line naming its ``binding_generation``;
- ``.../milestones/<work_item_id>.abandoned-<n>.json`` are retired
  ``ABANDONED`` records, renamed aside by the bind of a re-planned id.

:func:`preflight` resolves which binding governs a lifecycle step (the plan's
"Work-item resolution", rules 1-4) and performs its writers: the bind step,
the adopt row and the ``BRANCH_PLANNED`` adopt/collision rows, the
trunk-start preflight, the per-preflight no-rewrite and remote checks, and
branch sync (CP6); then the Draft PR lifecycle (creation, discovery, reuse,
re-verification), drift, readiness, the merge gate, the merged-PR handling
and close-out, from the branch and from trunk, and the PR-less close-out
(CP7). :func:`acknowledge` is the operator acknowledgement
(``milestone-binding --new-pr`` / ``--abandon``).

CP8 wires it into the lifecycle: :func:`repository_preflight` is step 1b of
every ``step`` (the no-policy :func:`probe` first, so a repository with no
policy at ``HEAD`` and no binding record costs two read-only ``git`` calls
and nothing else, I1), :func:`verify_post_step` is the post-step branch
verification of every worker job run under a binding, and
:func:`observation`, :func:`predict` and :func:`binding_lines` are the
read-only ``inspect``/``explain``/``status`` views (I10).

Every Git call goes through :mod:`controller.gitrepo`, every record write
through :mod:`controller.runtime`. Every record write is checked against
:data:`TRANSITIONS`; any other write raises :class:`BranchBindingError` and
writes nothing. Nothing here deletes a ref, forces anything, or merges (I2,
I3), and ``HEAD`` moves only in the bind step, when adopting a
crash-interrupted bind whose branch sits at ``HEAD``'s own commit, and at
close-out after a verified merge (I4).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import forge as forge_mod
from . import conventional_commit, gitrepo, release_notes, repo_policy, runtime
from .errors import (
    BranchBindingError, BranchInvariantViolatedError, GitOperationError, InvalidRepositoryPolicyError, InvalidTitleError,
)

SCHEMA_VERSION = 1
STATE_REL_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"

# ---------------------------------------------------------------------------
# States, their classification, and the transition table.
# ---------------------------------------------------------------------------

BRANCH_PLANNED = "BRANCH_PLANNED"
BRANCH_BOUND = "BRANCH_BOUND"
PR_PLANNED = "PR_PLANNED"
PR_OPEN = "PR_OPEN"
READY = "READY"
MERGED = "MERGED"
MERGED_SQUASHED = "MERGED_SQUASHED"
CLOSED = "CLOSED"
MERGED_REWRITTEN = "MERGED_REWRITTEN"
MERGED_BEFORE_ACCEPTANCE = "MERGED_BEFORE_ACCEPTANCE"
PR_CLOSED_UNMERGED = "PR_CLOSED_UNMERGED"
ABANDONED = "ABANDONED"

STATES = (BRANCH_PLANNED, BRANCH_BOUND, PR_PLANNED, PR_OPEN, READY, MERGED, MERGED_SQUASHED, CLOSED,
          MERGED_REWRITTEN, MERGED_BEFORE_ACCEPTANCE, PR_CLOSED_UNMERGED, ABANDONED)

#: The Controller drives these.
NON_TERMINAL_STATES = frozenset({BRANCH_PLANNED, BRANCH_BOUND, PR_PLANNED, PR_OPEN, READY, MERGED,
                                 MERGED_SQUASHED})
#: Blocking until an exit moves the record out, from the branch or trunk.
REFUSAL_STATES = frozenset({MERGED_BEFORE_ACCEPTANCE, PR_CLOSED_UNMERGED})
#: Never blocking.
TERMINAL_STATES = frozenset({CLOSED, MERGED_REWRITTEN, ABANDONED})

#: The outcomes of the merged-PR handling (CP7's "Close-out");
#: ``MERGED_SQUASHED`` only under ``merge_method: "squash"``
#: (``workflow-controller-squash-merge-tag-versioning`` Design F).
MERGED_PR_HANDLING = (MERGED, MERGED_SQUASHED, MERGED_REWRITTEN, MERGED_BEFORE_ACCEPTANCE)
#: The merged states close-out runs from.
CLOSE_OUT_STATES = (MERGED, MERGED_SQUASHED)


def _pairs(sources, targets) -> set[tuple[str | None, str]]:
    return {(s, t) for s in sources for t in targets}


#: ``(from, to)``, ``None`` meaning "no record". The plan's transition table,
#: row by row. A same-state rewrite is not a transition and is always legal;
#: ``ABANDONED``'s rename aside is not a record write (:func:`_retire_abandoned`).
TRANSITIONS = frozenset(
    _pairs([None], [BRANCH_PLANNED, BRANCH_BOUND])
    | _pairs([BRANCH_PLANNED], [BRANCH_BOUND])
    | _pairs([BRANCH_BOUND], [PR_PLANNED, MERGED, MERGED_BEFORE_ACCEPTANCE])
    | _pairs([PR_PLANNED], [BRANCH_BOUND, MERGED, MERGED_BEFORE_ACCEPTANCE, PR_OPEN, PR_CLOSED_UNMERGED])
    | _pairs([PR_PLANNED], MERGED_PR_HANDLING)
    | _pairs([PR_OPEN], [READY])
    | _pairs([PR_OPEN, READY], [PR_CLOSED_UNMERGED, *MERGED_PR_HANDLING])
    | _pairs([PR_CLOSED_UNMERGED], [PR_OPEN, *MERGED_PR_HANDLING])
    | _pairs([PR_CLOSED_UNMERGED, MERGED_BEFORE_ACCEPTANCE], [BRANCH_BOUND, ABANDONED])
    | _pairs([BRANCH_PLANNED, BRANCH_BOUND], [ABANDONED])
    | _pairs(CLOSE_OUT_STATES, [CLOSED])
)

#: The bind step's plan-stage phases: an explicit set, because Workflow's
#: ``KNOWN_PHASES`` has no order. ``AMENDING_PLAN`` always has
#: ``plan_approval`` set and is excluded.
PLAN_STAGE_PHASES = frozenset({
    "PLANNING", "SELF_REVIEWING_PLAN", "AWAITING_EXTERNAL_PLAN_REVIEW", "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_PLAN_APPROVAL",
})
#: Workflow's ``TERMINAL_PHASES``, the same in 2.5.1 and 2.6.0
#: (``scripts/workflow_state.py:321`` and ``:342``).
MILESTONE_COMPLETE = "MILESTONE_COMPLETE"

TRAILER_WORK_ITEM = "Workflow-Work-Item"
TRAILER_PLAN_APPROVAL = "Workflow-Plan-Approval"

# Gate codes (``decision.BRANCH_GATE_TEXTS`` gives each its ``HumanGate`` text).
GATE_SWITCH_TO_TRUNK = "switch_to_trunk"
GATE_BOUND_ITEM_MISSING = "bound_item_missing"
GATE_PR_CLOSED_UNMERGED = "pr_closed_unmerged"
GATE_MERGED_BEFORE_ACCEPTANCE = "merged_before_acceptance"
GATE_FAST_FORWARD_TRUNK = "fast_forward_trunk"
GATE_POST_ACCEPTANCE_COMMITS = "post_acceptance_commits"
GATE_INTEGRATION_REQUIRED = "integration_required"
GATE_CHECKS_PENDING = "checks_pending"
GATE_CHECKS_FAILING = "checks_failing"
GATE_CHECKS_CANCELLED = "checks_cancelled"
GATE_PR_HEAD_NOT_ACCEPTED = "pr_head_not_accepted"
GATE_MERGE_PULL_REQUEST = "merge_pull_request"
GATE_MERGE_METHOD_REWROTE_HISTORY = "merge_method_rewrote_history"
GATE_UNMERGED_COMMITS = "unmerged_commits"
GATE_DIRTY_TREE = "dirty_tree"
GATE_PR_TITLE_INVALID = "pr_title_invalid"
GATE_RELEASE_NOTES_INVALID = "release_notes_invalid"

#: Every gate a preflight can return.
GATE_CODES = frozenset({
    GATE_SWITCH_TO_TRUNK, GATE_BOUND_ITEM_MISSING, GATE_PR_CLOSED_UNMERGED, GATE_MERGED_BEFORE_ACCEPTANCE,
    GATE_FAST_FORWARD_TRUNK, GATE_POST_ACCEPTANCE_COMMITS, GATE_INTEGRATION_REQUIRED, GATE_CHECKS_PENDING,
    GATE_CHECKS_FAILING, GATE_CHECKS_CANCELLED, GATE_PR_HEAD_NOT_ACCEPTED, GATE_MERGE_PULL_REQUEST,
    GATE_MERGE_METHOD_REWROTE_HISTORY, GATE_UNMERGED_COMMITS, GATE_DIRTY_TREE, GATE_PR_TITLE_INVALID,
    GATE_RELEASE_NOTES_INVALID,
})

#: The marker line of every Draft PR body the Controller creates.
PR_MARKER = "<!-- workflow-controller: work_item={work_item_id} -->"
#: A plan's declared pull request title (``workflow-controller-squash-merge-
#: tag-versioning`` Design E): exactly one line starting with
#: :data:`PLAN_TITLE_PREFIX`, and it must match :data:`PLAN_TITLE_RE` whole.
PLAN_TITLE_PREFIX = "Pull request title:"
PLAN_TITLE_RE = re.compile(r"Pull request title: `(?P<title>[^`\r\n]+)`")
_PR_URL_RE = re.compile(r"^https://[^/\s]+/(?P<repo>[^/\s]+/[^/\s]+)/pull/(?P<number>[0-9]+)/?$")

# ``milestone-binding`` dispositions.
NEW_PR = "new-pr"
ABANDON = "abandon"
DISPOSITIONS = (NEW_PR, ABANDON)

_ABANDONED_NAME_RE = re.compile(r"^(?P<id>[a-z0-9][a-z0-9_-]{0,63})\.abandoned-(?P<n>[1-9][0-9]*)\.json$")
_GITHUB_URL_RES = (
    re.compile(r"^https://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^(?:ssh://)?git@github\.com[:/](?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$"),
)


# ---------------------------------------------------------------------------
# Context and outcomes.
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclasses.dataclass(frozen=True)
class Context:
    """Where a preflight runs: the target worktree (``repo_root``, its
    top level), the Controller runtime root, the Git runner, a forge factory
    (``repository -> Forge``; default :class:`~controller.forge.GhForge`)
    and a clock.

    ``events``, when a list is given, receives the name of every binding
    event :func:`_event` writes through this context, in order (a plain
    append, never a file write). ``controller.job`` passes a fresh one per
    step, so a refusal after the preflight can say what it did
    (workflow-2-6-integration CP3)."""

    repo_root: Path
    runtime_root: Path
    runner: gitrepo.Runner | None = None
    forge_factory: Callable[[str], forge_mod.Forge] | None = None
    clock: Callable[[], str] = _utc_now
    events: list[str] | None = None

    def forge(self, repository: str) -> forge_mod.Forge:
        return (self.forge_factory or forge_mod.GhForge)(repository)


@dataclasses.dataclass(frozen=True)
class Proceed:
    """The step proceeds. ``work_item_override`` replaces ``decide``'s own
    selection (only after acceptance, and only with the bound work item);
    ``binding`` is the governing record, or ``None`` when no binding governs
    the step; ``action`` names what this preflight did (``none``, ``bound``,
    ``adopted``, ``completed``, ``observed``, ``trunk_start``, ``pr_created``,
    ``closed_out``). ``base`` is the trunk tip a passed trunk start proved
    equal to ``<remote>/<trunk>`` (full ``HEAD``), which the bootstrap names
    as the next milestone's base; ``None`` otherwise."""

    work_item_override: str | None = None
    binding: Mapping[str, Any] | None = None
    action: str = "none"
    base: str | None = None


@dataclasses.dataclass(frozen=True)
class Gate:
    """The step stops for a human: ``code`` names the gate, ``exits`` the
    actions that clear it, in the order the message states them."""

    code: str
    work_item_id: str | None
    branch: str | None
    message: str
    exits: tuple[str, ...] = ()
    #: The binding policy's merge method; ``decision.branch_human_gate``
    #: words a squash-mode gate for "Squash and merge".
    merge_method: str = repo_policy.MERGE_METHOD_MERGE


def _refuse(message: str, *, work_item_id: str | None = None, branch: str | None = None,
            exits: list[str] | tuple[str, ...] = (), **evidence: Any) -> BranchBindingError:
    details: dict[str, Any] = {"work_item_id": work_item_id, "branch": branch, "exits": list(exits)}
    details.update(evidence)
    text = message if not exits else f"{message} Exit: {'; or '.join(exits)}."
    return BranchBindingError(text, evidence=details)


# ---------------------------------------------------------------------------
# Durable records.
# ---------------------------------------------------------------------------


def repo_key(common_dir: Path) -> str:
    """The SHA-256 of the canonical Git common directory."""
    return hashlib.sha256(str(Path(common_dir).resolve()).encode("utf-8")).hexdigest()


def milestones_rel(key: str) -> Path:
    return Path("repositories") / key / "milestones"


def record_rel(key: str, work_item_id: str) -> Path:
    return milestones_rel(key) / f"{work_item_id}.json"


def events_rel(key: str, work_item_id: str) -> Path:
    return milestones_rel(key) / work_item_id / "events.jsonl"


def _load_record(path: Path) -> dict | None:
    try:
        record = runtime.read_json(path)
    except (OSError, ValueError) as exc:
        raise _refuse(f"the binding record {path} cannot be read: {exc}", path=str(path)) from None
    if record is None:
        return None
    if (not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION
            or record.get("state") not in STATES or not isinstance(record.get("work_item_id"), str)
            or not isinstance(record.get("branch"), str) or not isinstance(record.get("trunk"), str)):
        raise _refuse(f"the binding record {path} is malformed", path=str(path))
    return record


def read_record(runtime_root: Path, key: str, work_item_id: str) -> dict | None:
    """The live binding record of ``work_item_id``, or ``None``."""
    record = _load_record(Path(runtime_root) / record_rel(key, work_item_id))
    if record is not None and record["work_item_id"] != work_item_id:
        raise _refuse(f"the binding record for {work_item_id} names {record['work_item_id']}",
                      work_item_id=work_item_id)
    return record


def live_records(runtime_root: Path, key: str) -> dict[str, dict]:
    """``{work_item_id: record}`` for every live binding record of the
    repository. Renamed ``.abandoned-<n>`` records are never live."""
    directory = Path(runtime_root) / milestones_rel(key)
    if not directory.is_dir():
        return {}
    records = {}
    for path in sorted(directory.glob("*.json")):
        if _ABANDONED_NAME_RE.match(path.name) or not repo_policy.WORK_ITEM_ID_RE.match(path.stem):
            continue
        records[path.stem] = read_record(runtime_root, key, path.stem)
    return records


def abandoned_records(runtime_root: Path, key: str, work_item_id: str) -> list[tuple[int, dict]]:
    """``[(n, record)]`` for every ``<work_item_id>.abandoned-<n>.json``, by
    ``n``. Their only reader besides the generation count is CP7's PR
    discovery, for the PR numbers they exclude."""
    directory = Path(runtime_root) / milestones_rel(key)
    if not directory.is_dir():
        return []
    found = []
    for path in directory.glob(f"{work_item_id}.abandoned-*.json"):
        match = _ABANDONED_NAME_RE.match(path.name)
        if match and match["id"] == work_item_id:
            found.append((int(match["n"]), _load_record(path)))
    return sorted(found, key=lambda item: item[0])


def write_record(runtime_root: Path, key: str, record: dict, *, now: str) -> dict:
    """Write ``record``, checked against :data:`TRANSITIONS` from the state
    on disk, read fresh. No record on disk means an ``O_EXCL`` create.
    Anything else refuses and writes nothing. Returns the written record."""
    work_item_id, target = record.get("work_item_id"), record.get("state")
    if target not in STATES or not isinstance(work_item_id, str):
        raise _refuse(f"refusing to write a binding record in state {target!r}", work_item_id=work_item_id)
    current = read_record(runtime_root, key, work_item_id)
    source = None if current is None else current["state"]
    if source != target and (source, target) not in TRANSITIONS:
        raise _refuse(f"refusing the binding transition {source or '(no record)'} -> {target} for "
                      f"{work_item_id}: it is not in the transition table",
                      work_item_id=work_item_id, branch=record.get("branch"),
                      from_state=source, to_state=target)
    written = dict(record, updated_at=now)
    rel = record_rel(key, work_item_id)
    if current is None:
        try:
            runtime.create_json(runtime_root, rel, written)
        except FileExistsError:
            raise _refuse(f"the binding record for {work_item_id} was created concurrently",
                          work_item_id=work_item_id) from None
    else:
        runtime.write_json(runtime_root, rel, written)
    return written


def _retire_abandoned(runtime_root: Path, key: str, work_item_id: str) -> None:
    """Rename the live ``ABANDONED`` record aside, to the next free
    ``<work_item_id>.abandoned-<n>.json``. An interrupted earlier rename
    (both names holding the same bytes) is completed instead."""
    rel = record_rel(key, work_item_id)
    live = Path(runtime_root) / rel
    record = read_record(runtime_root, key, work_item_id)
    if record is None:
        return
    if record["state"] != ABANDONED:
        raise _refuse(f"refusing to retire the {record['state']} binding of {work_item_id}",
                      work_item_id=work_item_id)
    data = live.read_bytes()
    taken = abandoned_records(runtime_root, key, work_item_id)
    for n, _ in taken:
        if (live.parent / f"{work_item_id}.abandoned-{n}.json").read_bytes() == data:
            runtime.remove_file(runtime_root, rel)
            return
    n = 1 + max((n for n, _ in taken), default=0)
    runtime.rename_exclusive(runtime_root, rel, milestones_rel(key) / f"{work_item_id}.abandoned-{n}.json")


def _event(ctx: Context, key: str, record: Mapping[str, Any], event: str, **details: Any) -> None:
    runtime.append_jsonl(ctx.runtime_root, events_rel(key, record["work_item_id"]), {
        "at": ctx.clock(), "event": event, "binding_generation": record["binding_generation"],
        "state": record["state"], **details})
    if ctx.events is not None:
        ctx.events.append(event)


def _write(ctx: Context, key: str, record: dict, event: str | None = None, **details: Any) -> dict:
    written = write_record(ctx.runtime_root, key, record, now=ctx.clock())
    if event is not None:
        _event(ctx, key, written, event, **details)
    return written


def binding_policy(record: Mapping[str, Any]) -> repo_policy.RepositoryPolicy:
    """The policy snapshot a binding recorded at its branch point. A record
    whose snapshot no longer parses, or whose bytes do not match the
    recorded SHA-256, refuses."""
    snapshot = record.get("policy") or {}
    raw = snapshot.get("raw")
    if not isinstance(raw, str) or hashlib.sha256(raw.encode("utf-8")).hexdigest() != snapshot.get("sha256"):
        raise _refuse(f"the policy snapshot of the {record['work_item_id']} binding is damaged",
                      work_item_id=record["work_item_id"])
    return repo_policy.parse_policy(raw.encode("utf-8"))


def _policy_snapshot(policy: repo_policy.RepositoryPolicy) -> dict:
    return {"sha256": policy.sha256, "raw": policy.raw.decode("utf-8"), "snapshot": dict(policy.data)}


# ---------------------------------------------------------------------------
# Git and Workflow reads.
# ---------------------------------------------------------------------------


def _work_items(state: Mapping[str, Any], where: str) -> dict:
    items = state.get("work_items", {})
    if not isinstance(items, dict) or not all(isinstance(v, dict) for v in items.values()):
        raise _refuse(f"{where} has no readable work_items mapping")
    return items


def _parse_state(raw: bytes, where: str) -> dict:
    try:
        state = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _refuse(f"{where} is not readable JSON: {exc}") from None
    if not isinstance(state, dict):
        raise _refuse(f"{where} is not a JSON object")
    _work_items(state, where)
    return state


def worktree_state(ctx: Context) -> dict:
    """The working tree's ``WORKFLOW_STATE.json``; an absent file is empty."""
    path = ctx.repo_root / STATE_REL_PATH
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _refuse(f"cannot read {path}: {exc}") from None
    return _parse_state(raw, f"the working tree's {STATE_REL_PATH}")


def committed_state(ctx: Context, rev: str) -> dict:
    """``rev``'s committed ``WORKFLOW_STATE.json``; absent at ``rev`` is empty."""
    raw = gitrepo.show(ctx.repo_root, rev, STATE_REL_PATH, runner=ctx.runner)
    return {} if raw is None else _parse_state(raw, f"{STATE_REL_PATH} at {rev}")


def _phase(state: Mapping[str, Any], work_item_id: str) -> str | None:
    entry = state.get("work_items", {}).get(work_item_id)
    return None if entry is None else entry.get("phase")


def _non_terminal(state: Mapping[str, Any], work_item_id: str) -> bool:
    phase = _phase(state, work_item_id)
    return phase is not None and phase != MILESTONE_COMPLETE


@dataclasses.dataclass(frozen=True)
class AcceptanceSearch:
    """The result of looking for a work item's acceptance commit on a
    first-parent path: ``commit`` is ``A``; ``untrailered`` is the commit
    that first records ``MILESTONE_COMPLETE`` without the
    ``Workflow-Work-Item`` trailer (the phase changed outside
    ``/accept-milestone``), when that is what the path holds instead."""

    commit: str | None
    untrailered: str | None = None


def find_acceptance_commit(ctx: Context, work_item_id: str, start: str, end: str) -> AcceptanceSearch:
    """``A``: the first commit on the first-parent path ``start..end`` whose
    committed ``WORKFLOW_STATE.json`` records ``work_item_id`` as
    ``MILESTONE_COMPLETE`` while its first parent's does not, carrying the
    ``Workflow-Work-Item: <work_item_id>`` trailer. Local Git reads only."""
    if _phase(committed_state(ctx, end), work_item_id) != MILESTONE_COMPLETE:
        return AcceptanceSearch(None)
    previous = _phase(committed_state(ctx, start), work_item_id) == MILESTONE_COMPLETE
    for commit in gitrepo.first_parent_log(ctx.repo_root, start, end, runner=ctx.runner):
        complete = _phase(committed_state(ctx, commit), work_item_id) == MILESTONE_COMPLETE
        if complete and not previous:
            trailers = gitrepo.commit_trailers(ctx.repo_root, commit, runner=ctx.runner)
            if (TRAILER_WORK_ITEM, work_item_id) in trailers:
                return AcceptanceSearch(commit)
            return AcceptanceSearch(None, untrailered=commit)
        previous = complete
    return AcceptanceSearch(None)


def _approval_commits(ctx: Context, work_item_id: str, start: str, end: str) -> list[str]:
    """The plan approval commits for ``work_item_id`` (a
    ``Workflow-Plan-Approval`` trailer beside ``Workflow-Work-Item: <id>``)
    on the first-parent path ``start..end``."""
    found = []
    for commit in gitrepo.first_parent_log(ctx.repo_root, start, end, runner=ctx.runner):
        trailers = gitrepo.commit_trailers(ctx.repo_root, commit, runner=ctx.runner)
        if (TRAILER_WORK_ITEM, work_item_id) in trailers and any(k == TRAILER_PLAN_APPROVAL for k, _ in trailers):
            found.append(commit)
    return found


def _forge_repository_of(url: str | None) -> str | None:
    """``OWNER/NAME`` of a GitHub remote URL, or ``None`` for any other URL."""
    for pattern in _GITHUB_URL_RES:
        match = pattern.match(url or "")
        if match:
            return match["repo"]
    return None


def _remote_refs(ctx: Context, remote: str, branches: list[str]) -> dict[str, str | None]:
    """``{branch: commit or None}`` for each of ``branches`` on ``remote``,
    each present one fetched into ``refs/remotes/<remote>/`` so its commit
    is available locally. Contacts the remote; an undecidable read refuses."""
    listing = gitrepo.ls_remote(ctx.repo_root, remote, [f"refs/heads/{b}" for b in branches], runner=ctx.runner)
    result = {}
    for branch in branches:
        if f"refs/heads/{branch}" in listing:
            result[branch] = gitrepo.fetch_branch(ctx.repo_root, remote, branch, runner=ctx.runner)
        else:
            result[branch] = None
    return result


def _remote_trunk(ctx: Context, remote: str, trunk: str) -> str:
    tip = _remote_refs(ctx, remote, [trunk])[trunk]
    if tip is None:
        raise _refuse(f"{remote} has no {trunk} branch")
    return tip


def _local_branch(ctx: Context, branch: str) -> str | None:
    return gitrepo.ref_commit(ctx.repo_root, f"refs/heads/{branch}", runner=ctx.runner)


def _ancestor(ctx: Context, a: str, b: str) -> bool:
    return gitrepo.is_ancestor(ctx.repo_root, a, b, runner=ctx.runner)


def _invert_branch(branch_format: str, branch: str) -> str | None:
    prefix, _, suffix = branch_format.partition("{work_item_id}")
    if len(branch) <= len(prefix) + len(suffix) or not branch.startswith(prefix) or not branch.endswith(suffix):
        return None
    candidate = branch[len(prefix):len(branch) - len(suffix)]
    return candidate if repo_policy.WORK_ITEM_ID_RE.match(candidate) else None


def _cli(work_item_id: str, disposition: str, repo_root: Path) -> str:
    return f"`workflow-controller --work-item {work_item_id} milestone-binding --{disposition} {repo_root}`"


def _require_not_checked_out_elsewhere(ctx: Context, record: Mapping[str, Any]) -> None:
    """A record write made without ``HEAD`` on the bound branch requires the
    bound branch not to be checked out in any other worktree."""
    here = ctx.repo_root.resolve()
    for path, branch in gitrepo.worktree_branches(ctx.repo_root, runner=ctx.runner).items():
        if branch == record["branch"] and Path(path).resolve() != here:
            raise _refuse(f"{record['branch']} is checked out in the worktree {path}; run this there, "
                          f"or switch that worktree off it first",
                          work_item_id=record["work_item_id"], branch=record["branch"], worktree=path)


# ---------------------------------------------------------------------------
# The preflight: work-item resolution.
# ---------------------------------------------------------------------------

_UNSET = object()


def preflight(ctx: Context, *, requested_work_item_id: str | None = None,
              head_policy: Any = _UNSET) -> Proceed | Gate:
    """Resolve the binding that governs this step, performing the writers
    this checkpoint owns. Returns :class:`Proceed` or :class:`Gate`, or
    raises :class:`BranchBindingError` (or a Git/forge/policy refusal).

    ``requested_work_item_id`` is an explicit ``--work-item``.
    ``head_policy`` is the admissible policy committed at ``HEAD`` (or
    ``None``), when the caller has already read it."""
    head = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
    key = repo_key(gitrepo.common_dir(ctx.repo_root, runner=ctx.runner))
    records = live_records(ctx.runtime_root, key)
    if head_policy is _UNSET:
        head_policy = None if head.commit is None else repo_policy.read_committed_policy(ctx.repo_root, "HEAD")
    if not records and not repo_policy.milestone_branches_enabled(head_policy):
        return Proceed()  # I1: no activation, no change

    # Rule 1: HEAD on the branch of a binding record.
    if head.branch is not None:
        for record in records.values():
            if record["branch"] == head.branch:
                return _on_bound_branch(ctx, key, record, head, requested_work_item_id)
    if head.branch is None:
        raise _refuse("HEAD is detached; attach it to the trunk or to a milestone branch",
                      head=head.commit)

    trunks = ({head_policy.trunk_branch} if head_policy is not None
              else {record["trunk"] for record in records.values()})
    if head.branch in trunks:
        return _on_trunk(ctx, key, records, head, head_policy, requested_work_item_id)

    # Rule 2: a non-trunk branch that inverts branch_format, with no record.
    if head_policy is not None:
        candidate = _adopt_candidate(ctx, head_policy, head)
        if candidate is not None and candidate not in records:
            return _adopt(ctx, key, head_policy, head, candidate, requested_work_item_id)

    # Rule 4.
    raise _refuse(f"HEAD is on {head.branch}, which is neither the trunk ({', '.join(sorted(trunks))}) nor "
                  f"a milestone branch this repository can bind; switch to the trunk",
                  branch=head.branch)


# -- rule 1 -------------------------------------------------------------------


def _on_bound_branch(ctx: Context, key: str, record: dict, head: gitrepo.HeadState,
                     requested: str | None) -> Proceed | Gate:
    work_item_id, branch = record["work_item_id"], record["branch"]
    action = "observed"
    if record["state"] == BRANCH_PLANNED:
        record = _reconcile_planned(ctx, key, record, head)
        head = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
        action = "completed"
    # The terminal and refusal cases. The reopen re-read of a
    # PR_CLOSED_UNMERGED record may leave a non-terminal state, which then
    # meets the bound_item_missing case below in this same preflight.
    if record["state"] == PR_CLOSED_UNMERGED:
        record = _reopen_reread(ctx, key, record)
    state = record["state"]
    if state in TERMINAL_STATES or state in REFUSAL_STATES:
        return _stopped_on_branch(ctx, key, record, head)

    wt = worktree_state(ctx)
    items = _work_items(wt, "the working tree's state") if wt else {}
    if work_item_id not in items:
        return _gate_bound_item_missing(ctx, record, head)
    selected = requested or wt.get("active_work_item_id")
    if selected is not None and selected != work_item_id \
            and (items.get(selected) or {}).get("parent_work_item_id") != work_item_id:
        raise _refuse(f"{selected} is neither {work_item_id}, which {branch} is bound to, nor one of its "
                      f"remediation children", work_item_id=work_item_id, branch=branch, selected=selected)
    override = work_item_id if items[work_item_id].get("phase") == MILESTONE_COMPLETE else None
    return _branch_cells(ctx, key, record, head, override, action)


def _stopped_on_branch(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> Gate:
    """Rule 1's gate for a record in a terminal or refusal state."""
    work_item_id, branch, state = record["work_item_id"], record["branch"], record["state"]
    if state == MERGED_REWRITTEN and not record.get("rewrite_gate_shown"):
        return _report_rewrite(ctx, key, record)
    if state in TERMINAL_STATES:
        return Gate(GATE_SWITCH_TO_TRUNK, work_item_id, branch,
                    f"the {branch} binding of {work_item_id} is {state}; switch to the trunk "
                    f"(`git switch {record['trunk']}`)", (f"git switch {record['trunk']}",))
    if state == PR_CLOSED_UNMERGED:
        return _gate_pr_closed_unmerged(ctx, record)
    return _gate_merged_before_acceptance(ctx, record, head)


def _branch_cells(ctx: Context, key: str, record: dict, head: gitrepo.HeadState, override: str | None,
                  action: str) -> Proceed | Gate:
    """The branch column of the outcome matrix for a non-terminal record,
    state after state, until one cell returns an outcome."""
    observed = False
    while True:
        state = record["state"]
        if state in TERMINAL_STATES or state in REFUSAL_STATES:
            return _stopped_on_branch(ctx, key, record, head)
        if state in CLOSE_OUT_STATES:
            return _close_out_on_branch(ctx, key, record, head)
        if state in (PR_OPEN, READY):
            pr = _verified_pr(ctx, record, record["pr"]["number"])
            if pr.state != "OPEN":
                record = _pr_left_open(ctx, key, record, pr)
                continue
            if not observed:
                # A readied pull request stays at the accepted head (I7):
                # nothing past it is pushed while the record is READY.
                record, observed = _observe_branch(ctx, key, record, head, sync=state != READY), True
            record = _write(ctx, key, dict(record, pr=_pr_ref(pr)))
            if state == READY:
                return _merge_gate(record, tip=head.commit)
            if _phase(committed_state(ctx, "HEAD"), record["work_item_id"]) != MILESTONE_COMPLETE:
                if _squash(record):
                    _sync_title(ctx, key, record, pr)
                return Proceed(work_item_override=override, binding=record, action=action)
            return _readiness(ctx, key, record, head)
        if not observed:
            record, observed = _observe_branch(ctx, key, record, head), True
        if state == BRANCH_BOUND:
            observation = record["last_observation"]
            if _ancestor(ctx, head.commit, observation["remote_trunk"]):
                # The PR creation condition is false: nothing to create yet.
                if _phase(committed_state(ctx, head.commit), record["work_item_id"]) != MILESTONE_COMPLETE:
                    return Proceed(work_item_override=override, binding=record, action=action)
                record = _merged_handling(ctx, key, record, None, head.commit)  # the PR-less close-out
                continue
            record = _ensure_pushed(ctx, key, record, head.commit)
            record = _write(ctx, key, dict(record, state=PR_PLANNED), "pr_planned", tip=head.commit)
            state = PR_PLANNED
        if state == PR_PLANNED:
            outcome = _discover_on_branch(ctx, key, record, head)
            if not isinstance(outcome, dict):
                return outcome
            if outcome["state"] == BRANCH_BOUND:
                return Proceed(work_item_override=override, binding=outcome, action=action)
            if outcome["state"] == PR_OPEN and outcome["pr"].get("created") and action == "observed":
                action = "pr_created"
            record = outcome
            continue
        raise _refuse(f"no branch-side cell for the {state} binding of {record['work_item_id']}",
                      work_item_id=record["work_item_id"], branch=record["branch"], state=state)


def _observe_branch(ctx: Context, key: str, record: dict, head: gitrepo.HeadState, *, sync: bool = True) -> dict:
    """Every later preflight for a bound, non-terminal item: no rewrite of
    the branch, the remote branch absent or an ancestor of the tip, then
    sync (fast-forward the remote branch when it exists and is behind;
    skipped when ``sync`` is false) and the drift observation."""
    remote, trunk, branch = record["repository"]["remote"], record["trunk"], record["branch"]
    tip = head.commit
    refs = _remote_refs(ctx, remote, [trunk, branch])
    if refs[trunk] is None:
        raise _refuse(f"{remote} has no {trunk} branch", work_item_id=record["work_item_id"], branch=branch)
    remote_branch = refs[branch]
    last = (record.get("last_observation") or {}).get("tip") or record["branch_point"]
    if not _ancestor(ctx, last, tip):
        raise _refuse(f"{branch} was rewritten: its tip {tip} does not descend from the last observed "
                      f"tip {last}", work_item_id=record["work_item_id"], branch=branch,
                      exits=[f"restore {branch} to a descendant of {last}"], last_tip=last, tip=tip)
    if remote_branch is not None and not _ancestor(ctx, remote_branch, tip):
        raise _refuse(f"{remote}/{branch} ({remote_branch}) is not an ancestor of the local tip {tip}. "
                      f"The likely cause is GitHub's \"Update branch\" button, which pushes a merge "
                      f"commit to the milestone branch on the server",
                      work_item_id=record["work_item_id"], branch=branch,
                      exits=[f"bring {branch} and {remote}/{branch} back into a fast-forward relation"],
                      remote_commit=remote_branch, tip=tip)
    push = None
    if sync and remote_branch is not None and remote_branch != tip:
        record = _push(ctx, key, record, tip)
        remote_branch, push = tip, {"intent": tip, "outcome": tip}
    fresh = _ancestor(ctx, refs[trunk], tip)
    behind = gitrepo.ahead_behind(ctx.repo_root, tip, refs[trunk], runner=ctx.runner)[1]
    observation: dict[str, Any] = {} if push is None else {"push": push}
    observation.update({"tip": tip, "remote_branch": remote_branch, "remote_trunk": refs[trunk],
                        "fresh": fresh, "behind": behind, "observed_at": ctx.clock()})
    return _write(ctx, key, dict(record, last_observation=observation))


def _push(ctx: Context, key: str, record: dict, tip: str) -> dict:
    """Push the bound branch as a fast-forward, persisted as an intent first
    and re-read with ``ls_remote`` after (I5)."""
    remote, branch = record["repository"]["remote"], record["branch"]
    pending = dict(record.get("last_observation") or {}, push={"intent": tip, "outcome": None})
    record = _write(ctx, key, dict(record, last_observation=pending), "push_intent", commit=tip)
    gitrepo.push_branch(ctx.repo_root, remote, branch, runner=ctx.runner)
    pushed = gitrepo.ls_remote(ctx.repo_root, remote, [f"refs/heads/{branch}"],
                               runner=ctx.runner).get(f"refs/heads/{branch}")
    if pushed != tip:
        raise _refuse(f"pushing {branch} did not leave {remote}/{branch} at {tip} (it is at {pushed})",
                      work_item_id=record["work_item_id"], branch=branch)
    done = dict(record["last_observation"], push={"intent": tip, "outcome": pushed}, remote_branch=pushed)
    return _write(ctx, key, dict(record, last_observation=done), "pushed", commit=tip)


def _ensure_pushed(ctx: Context, key: str, record: dict, tip: str) -> dict:
    """PR creation step 1: the remote branch at the local tip."""
    if (record.get("last_observation") or {}).get("remote_branch") == tip:
        return record
    return _push(ctx, key, record, tip)


# -- gates of rule 1 ------------------------------------------------------------


def _gate_pr_closed_unmerged(ctx: Context, record: Mapping[str, Any]) -> Gate:
    work_item_id, branch = record["work_item_id"], record["branch"]
    exits = (f"reopen pull request #{(record.get('pr') or {}).get('number')} on GitHub",
             _cli(work_item_id, NEW_PR, ctx.repo_root), _cli(work_item_id, ABANDON, ctx.repo_root))
    return Gate(GATE_PR_CLOSED_UNMERGED, work_item_id, branch,
                f"the pull request of {work_item_id} was closed without merge. The exits are exclusive: "
                f"reopen it on GitHub, or {exits[1]}, or {exits[2]}; after --new-pr, do not also reopen the "
                f"old pull request. GitHub cannot reopen a pull request whose head branch was deleted on "
                f"GitHub: restore the branch there first, or use --new-pr or --abandon. If the branch has "
                f"already been merged into the trunk by hand, --new-pr is the exit to take", exits)


def _mba_new_pr_problem(ctx: Context, record: Mapping[str, Any]) -> str | None:
    """Common check 5, and the ``--abandon``-alone case of the
    ``merged_before_acceptance`` gate: ``<remote>/<trunk>`` records the item
    ``MILESTONE_COMPLETE`` with no acceptance commit on ``branch_point..H``."""
    work_item_id = record["work_item_id"]
    remote, trunk = record["repository"]["remote"], record["trunk"]
    remote_trunk = _remote_trunk(ctx, remote, trunk)
    if _phase(committed_state(ctx, remote_trunk), work_item_id) != MILESTONE_COMPLETE:
        return None
    tip = _branch_tip_for_trunk_side(ctx, record)
    if find_acceptance_commit(ctx, work_item_id, record["branch_point"], tip).commit is not None:
        return None
    return (f"{remote}/{trunk} already records {work_item_id} as {MILESTONE_COMPLETE} without an acceptance "
            f"commit on {record['branch_point']}..{tip}, so --new-pr would only record the same state again")


def _gate_merged_before_acceptance(ctx: Context, record: Mapping[str, Any], head: gitrepo.HeadState) -> Gate:
    work_item_id, branch = record["work_item_id"], record["branch"]
    abandon_ok = not abandon_problems(ctx, record, head)
    alone = _mba_new_pr_problem(ctx, record)
    if alone is not None:
        exits: tuple[str, ...] = (_cli(work_item_id, ABANDON, ctx.repo_root),)
        why = f"{alone}. Only --abandon remains: {exits[0]}"
    else:
        exits = (_cli(work_item_id, NEW_PR, ctx.repo_root),) + (
            (_cli(work_item_id, ABANDON, ctx.repo_root),) if abandon_ok else ())
        why = "exits: " + "; or ".join(exits)
    untrailered = record.get("untrailered_completion")
    if untrailered:
        why = (f"{untrailered} records {work_item_id} as {MILESTONE_COMPLETE} without the "
               f"`{TRAILER_WORK_ITEM}: {work_item_id}` trailer, so it is not an acceptance commit; {why}")
    return Gate(GATE_MERGED_BEFORE_ACCEPTANCE, work_item_id, branch,
                f"{branch} was merged into {record['trunk']} before {work_item_id} was accepted; {why}", exits)


def _gate_bound_item_missing(ctx: Context, record: Mapping[str, Any], head: gitrepo.HeadState) -> Gate:
    work_item_id, branch = record["work_item_id"], record["branch"]
    restore = (f"restore the plan files ({'`git restore`'} them, or `git stash pop` if they were stashed), "
               f"after which the step proceeds")
    exits: tuple[str, ...] = (restore,)
    if not abandon_problems(ctx, record, head):
        exits += (_cli(work_item_id, ABANDON, ctx.repo_root),)
    return Gate(GATE_BOUND_ITEM_MISSING, work_item_id, branch,
                f"{branch} is bound to {work_item_id}, but the working tree's {STATE_REL_PATH} has no entry "
                f"for it; no worker is launched. Exits: " + "; or ".join(exits), exits)


# ---------------------------------------------------------------------------
# The Draft PR lifecycle (CP7).
# ---------------------------------------------------------------------------


def _pr_ref(pr: forge_mod.PullRequest) -> dict:
    return {"number": pr.number, "url": pr.url, "is_draft": pr.is_draft, "head_oid": pr.head_oid}


def _identity_problems(record: Mapping[str, Any], pr: forge_mod.PullRequest) -> list[str]:
    """I6: a pull request is this binding's only if its head ref, base ref,
    repository and non-cross-repository flag all match."""
    problems = []
    if pr.head_ref != record["branch"]:
        problems.append(f"its head is {pr.head_ref}, not {record['branch']}")
    if pr.base_ref != record["trunk"]:
        problems.append(f"its base is {pr.base_ref}, not {record['trunk']}")
    if pr.is_cross_repository:
        problems.append("it comes from another repository (a fork)")
    match = _PR_URL_RE.match(pr.url)
    expected = record["repository"]["forge_repository"]
    if match is None or match["repo"].lower() != expected.lower() or int(match["number"]) != pr.number:
        problems.append(f"its URL {pr.url} is not pull request #{pr.number} of {expected}")
    return problems


def _verified_pr(ctx: Context, record: Mapping[str, Any], number: int) -> forge_mod.PullRequest:
    """A fresh ``view_pr`` of the recorded number, refusing unless it is
    still this binding's pull request (I6). Undecidable reads refuse (I9)."""
    pr = ctx.forge(record["repository"]["forge_repository"]).view_pr(int(number))
    problems = _identity_problems(record, pr)
    if problems:
        raise _refuse(f"pull request #{number} is no longer {record['work_item_id']}'s: " + "; ".join(problems)
                      + ".", work_item_id=record["work_item_id"], branch=record["branch"], pr=number,
                      exits=[f"restore pull request #{number}'s head and base on GitHub, or "
                             f"{_cli(record['work_item_id'], NEW_PR, ctx.repo_root)} once it is closed"])
    return pr


def _reopen_reread(ctx: Context, key: str, record: dict) -> dict:
    """The reopen exit: a ``PR_CLOSED_UNMERGED`` record re-reads its pull
    request. Open again with its identity intact returns it to ``PR_OPEN``;
    merged goes to the merged-PR handling; still closed leaves it."""
    pr = _verified_pr(ctx, record, record["pr"]["number"])
    if pr.state == "OPEN":
        return _write(ctx, key, dict(record, state=PR_OPEN, pr=_pr_ref(pr)), "reopened", pr=pr.number)
    if pr.state == "MERGED":
        return _merged_handling(ctx, key, record, pr, pr.head_oid)
    return record


def _pr_left_open(ctx: Context, key: str, record: dict, pr: forge_mod.PullRequest) -> dict:
    """Per-preflight re-verification of a ``PR_OPEN``/``READY`` pull request
    that is no longer open: closed without merge is ``PR_CLOSED_UNMERGED``
    (never recreated automatically); merged goes to the merged-PR
    handling."""
    if pr.state == "MERGED":
        return _merged_handling(ctx, key, record, pr, pr.head_oid)
    return _write(ctx, key, dict(record, state=PR_CLOSED_UNMERGED, pr=_pr_ref(pr)), "pr_closed_unmerged",
                  pr=pr.number)


def _merge_gate(record: Mapping[str, Any], *, tip: str | None = None) -> Gate:
    pr, accepted = record["pr"], record.get("accepted_head")
    later = (f". Local commits after the acceptance commit {accepted} (the tip is {tip}) are not pushed to a "
             f"ready pull request" if tip is not None and accepted is not None and tip != accepted else "")
    if _squash(record):
        return Gate(GATE_MERGE_PULL_REQUEST, record["work_item_id"], record["branch"],
                    f"pull request #{pr['number']} ({pr['url']}) is ready; a human merges it on GitHub with "
                    f"\"{SQUASH_BUTTON}\". The squash commit's subject is the pull request's title and its body "
                    f"the pull request's body. The Controller never merges{later}",
                    (f"merge pull request #{pr['number']} on GitHub with \"{SQUASH_BUTTON}\"",),
                    repo_policy.MERGE_METHOD_SQUASH)
    return Gate(GATE_MERGE_PULL_REQUEST, record["work_item_id"], record["branch"],
                f"pull request #{pr['number']} ({pr['url']}) is ready; a human merges it on GitHub with "
                f"\"Create a merge commit\". Squash and rebase merges take the reviewed commits off "
                f"{record['trunk']}. The Controller never merges{later}",
                (f"merge pull request #{pr['number']} on GitHub with \"Create a merge commit\"",))


def _report_rewrite(ctx: Context, key: str, record: dict) -> Gate:
    """``MERGED_REWRITTEN``'s one-time gate: shown once, then the record
    never blocks (a same-state rewrite of its flag)."""
    record = _write(ctx, key, dict(record, rewrite_gate_shown=True))
    pr = record.get("pr") or {}
    return Gate(GATE_MERGE_METHOD_REWROTE_HISTORY, record["work_item_id"], record["branch"],
                f"pull request #{pr.get('number')} was merged, but its head {record['merged_head']} is not on "
                f"{record['repository']['remote']}/{record['trunk']} (merge commit "
                f"{record.get('merge_commit')}): a squash or rebase merge rewrote the reviewed history, and "
                f"the Workflow provenance on {record['trunk']} is unreachable. The Controller cannot undo this "
                f"and does not switch; switch to the trunk by hand. Disable squash and rebase merging in the "
                f"repository's settings",
                (f"git switch {record['trunk']}",))


def _merged_handling(ctx: Context, key: str, record: dict, pr: forge_mod.PullRequest | None, h: str) -> dict:
    """Steps 1 and 2 of the merged-PR handling, for a merged pull request
    ``pr`` with final head ``h``, or (``pr is None``) the PR-less close-out
    with ``h`` the branch tip. Reads only the first-parent path
    ``branch_point..h``, its committed states and trailers, and the fetched
    ``<remote>/<trunk>`` -- never the working tree -- and writes
    ``MERGED_BEFORE_ACCEPTANCE``, ``MERGED_REWRITTEN`` or ``MERGED``, or,
    in squash mode only, ``MERGED_SQUASHED`` for a :func:`verified_squash`."""
    work_item_id, remote, trunk = record["work_item_id"], record["repository"]["remote"], record["trunk"]
    if pr is not None and gitrepo.ref_commit(ctx.repo_root, h, runner=ctx.runner) is None:
        # The head branch was deleted on GitHub after the merge.
        gitrepo.fetch(ctx.repo_root, remote, [f"refs/pull/{pr.number}/head:refs/remotes/{remote}/pull/{pr.number}"],
                      runner=ctx.runner)
        if gitrepo.ref_commit(ctx.repo_root, h, runner=ctx.runner) is None:
            raise _refuse(f"the merged head {h} of pull request #{pr.number} cannot be fetched",
                          work_item_id=work_item_id, branch=record["branch"], merged_head=h)
    fields: dict[str, Any] = {} if pr is None else {"pr": _pr_ref(pr)}
    search = find_acceptance_commit(ctx, work_item_id, record["branch_point"], h)
    if search.commit is None:
        return _write(ctx, key, dict(record, state=MERGED_BEFORE_ACCEPTANCE, untrailered_completion=search.untrailered,
                                     **fields),
                      "merged_before_acceptance", head=h, untrailered=search.untrailered)
    remote_trunk = _remote_trunk(ctx, remote, trunk)
    if not _ancestor(ctx, h, remote_trunk):
        if pr is not None and _squash(record) and verified_squash(ctx, record, pr, h, remote_trunk):
            return _write(ctx, key, dict(record, state=MERGED_SQUASHED, merged_head=h, accepted_head=search.commit,
                                         merge_commit=pr.merge_commit, **fields),
                          "merged_squashed", head=h, merge_commit=pr.merge_commit, pr=pr.number)
        return _write(ctx, key, dict(record, state=MERGED_REWRITTEN, merged_head=h, accepted_head=search.commit,
                                     merge_commit=None if pr is None else pr.merge_commit, rewrite_gate_shown=False,
                                     **fields),
                      "merged_rewritten", head=h, remote_trunk=remote_trunk)
    return _write(ctx, key, dict(record, state=MERGED, merged_head=h, accepted_head=search.commit, **fields),
                  "merged", head=h, pr=None if pr is None else pr.number)


#: ``git merge-tree --write-tree``'s floor, needed on the integration path only.
MERGE_TREE_MIN_GIT = (2, 38, 0)


def verified_squash(ctx: Context, record: Mapping[str, Any], pr: forge_mod.PullRequest, h: str,
                    remote_trunk: str) -> bool:
    """Whether merged pull request ``pr``, whose head ``h`` is not on the
    fetched ``remote_trunk``, was squash-merged with the reviewed content
    (Design F's five conditions). ``False`` means "not verified", and the
    caller writes ``MERGED_REWRITTEN``; a state this cannot tell apart from
    a rebase is not verified. An undecidable read -- Git older than 2.38 on
    the integration path, or a failing ``merge-tree`` -- refuses and writes
    nothing (I3)."""
    m = pr.merge_commit
    # 1. the merge commit is on the trunk.
    if not m or gitrepo.ref_commit(ctx.repo_root, m, runner=ctx.runner) != m or not _ancestor(ctx, m, remote_trunk):
        return False
    # 2. one parent.
    parents = gitrepo.commit_parents(ctx.repo_root, m, runner=ctx.runner)
    if len(parents) != 1:
        return False
    p = parents[0]
    # 3. GitHub's squash subject: the title and the number.
    if gitrepo.commit_subject(ctx.repo_root, m, runner=ctx.runner) != f"{pr.title} (#{pr.number})":
        return False
    # 5. not a rewrite of h: a rebase keeps h's author, author date and message.
    if gitrepo.commit_identity(ctx.repo_root, m, runner=ctx.runner) \
            == gitrepo.commit_identity(ctx.repo_root, h, runner=ctx.runner):
        return False
    # 4. the reviewed content.
    tree = gitrepo.tree_of(ctx.repo_root, m, runner=ctx.runner)
    if _ancestor(ctx, p, h):
        return tree == gitrepo.tree_of(ctx.repo_root, h, runner=ctx.runner)
    if gitrepo.merge_drivers(ctx.repo_root, runner=ctx.runner):
        return False  # never run a configured merge driver's program
    version = gitrepo.git_version(ctx.repo_root, runner=ctx.runner)
    if version < MERGE_TREE_MIN_GIT:
        raise _refuse(f"pull request #{pr.number}'s merge commit {m} is verified against `git merge-tree "
                      f"--write-tree`, which needs Git {'.'.join(map(str, MERGE_TREE_MIN_GIT))} or later; this Git "
                      f"is {'.'.join(map(str, version))}", work_item_id=record["work_item_id"],
                      branch=record["branch"], pr=pr.number, merge_commit=m,
                      exits=[f"upgrade Git to {'.'.join(map(str, MERGE_TREE_MIN_GIT))} or later"])
    merged = gitrepo.merge_tree(ctx.repo_root, p, h, runner=ctx.runner)
    return merged is not None and merged == tree


def _unmerged_commits_gate(ctx: Context, record: Mapping[str, Any], tip: str, *, from_trunk: bool) -> Gate:
    h, branch = record["merged_head"], record["branch"]
    commits = gitrepo.first_parent_log(ctx.repo_root, h, tip, runner=ctx.runner)
    exits = [f"move them off {branch} (for example `git branch <new-branch> {branch}`, then "
             f"`git reset --keep {h}` on {branch})"]
    if from_trunk:
        exits.append(f"delete the local {branch} (`git branch -D {branch}`)")
    return Gate(GATE_UNMERGED_COMMITS, record["work_item_id"], branch,
                f"{branch} has commits that were never merged: {', '.join(commits)}. They are never left "
                f"behind silently. Exits: " + "; or ".join(exits), tuple(exits))


def _close_out_on_branch(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> Proceed | Gate:
    """Close-out step 3 from the branch: a clean tree and the tip at
    ``merged_head``, then switch to the trunk, fast-forward it to
    ``<remote>/<trunk>``, write ``CLOSED``, and continue to the trunk start
    (step 4). From ``MERGED_SQUASHED`` the squash commit stands in for
    ``merged_head`` in the trunk-membership check."""
    work_item_id, branch, trunk = record["work_item_id"], record["branch"], record["trunk"]
    remote, h = record["repository"]["remote"], record["merged_head"]
    changes = gitrepo.tracked_changes(ctx.repo_root, runner=ctx.runner)
    if changes:
        return Gate(GATE_DIRTY_TREE, work_item_id, branch,
                    f"{branch} was merged, but the tracked tree has changes; close-out switches to {trunk} only "
                    f"from a clean tree: {', '.join(changes)}", ("commit, stash or discard them",))
    if head.commit != h:
        if _ancestor(ctx, h, head.commit):
            return _unmerged_commits_gate(ctx, record, head.commit, from_trunk=False)
        raise _refuse(f"the tip of {branch} ({head.commit}) is not the merged head {h}",
                      work_item_id=work_item_id, branch=branch,
                      exits=[f"fast-forward {branch} to {h} (`git merge --ff-only {h}`)"])
    remote_trunk = _remote_trunk(ctx, remote, trunk)
    local_trunk = _local_branch(ctx, trunk)
    if record["state"] == MERGED_SQUASHED:
        m = record["merge_commit"]
        if not _ancestor(ctx, m, remote_trunk):
            raise _refuse(f"the squash commit {m} of the merged head {h} is not on {remote}/{trunk}",
                          work_item_id=work_item_id, branch=branch)
    elif not _ancestor(ctx, h, remote_trunk):
        raise _refuse(f"the merged head {h} is not on {remote}/{trunk}", work_item_id=work_item_id, branch=branch)
    if local_trunk is None or not _ancestor(ctx, local_trunk, remote_trunk):
        raise _refuse(f"the local {trunk} ({local_trunk}) cannot fast-forward to {remote}/{trunk} ({remote_trunk})",
                      work_item_id=work_item_id, branch=branch,
                      exits=[f"reconcile {trunk} with {remote}/{trunk} by hand"])
    gitrepo.switch(ctx.repo_root, trunk, runner=ctx.runner)
    gitrepo.fast_forward(ctx.repo_root, trunk, f"refs/remotes/{remote}/{trunk}", runner=ctx.runner)
    _verify_on(ctx, trunk, remote_trunk)
    _write(ctx, key, dict(record, state=CLOSED), "closed", side="branch")
    return _after_close(ctx, key)


def _after_close(ctx: Context, key: str) -> Proceed | Gate:
    """Close-out step 4: continue to the trunk start, exactly as for a
    repository with no active work item."""
    head = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
    policy = None if head.commit is None else repo_policy.read_committed_policy(ctx.repo_root, "HEAD")
    outcome = _on_trunk(ctx, key, live_records(ctx.runtime_root, key), head, policy, None)
    if isinstance(outcome, Proceed) and outcome.action in ("none", "trunk_start"):
        return Proceed(action="closed_out", base=outcome.base)
    return outcome


# -- PR discovery ---------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Discovery:
    """PR creation steps 3 and 4: ``row`` is the first matching row
    (``excluded_open``, ``many_open``, ``many_merged``, ``merged``,
    ``open``, ``closed_unmerged`` or ``none``); ``prs`` the pull requests it
    names; ``closed`` the other non-excluded closed-unmerged numbers."""

    row: str
    prs: tuple[forge_mod.PullRequest, ...] = ()
    closed: tuple[int, ...] = ()
    excluded: Mapping[int, str] = dataclasses.field(default_factory=dict)


def excluded_prs(runtime_root: Path, key: str, record: Mapping[str, Any]) -> dict[int, str]:
    """``{number: why}``: the live record's ``superseded_prs``, plus the
    ``pr.number`` and every ``superseded_prs`` number of each renamed
    ``<work_item_id>.abandoned-<n>.json`` record (their only reader)."""
    excluded = {int(n): "superseded (milestone-binding --new-pr)" for n in record.get("superseded_prs") or []}
    for _, old in abandoned_records(runtime_root, key, record["work_item_id"]):
        numbers = [(old.get("pr") or {}).get("number"), *(old.get("superseded_prs") or [])]
        for n in numbers:
            if n is not None:
                excluded.setdefault(int(n), "of an abandoned binding")
    return excluded


def discover(ctx: Context, key: str, record: Mapping[str, Any]) -> Discovery:
    """``list_prs`` for the bound branch, filtered to this binding's
    identity (head ref, base ref, repository, not cross-repository), minus
    the excluded set, classified into the first matching row. Read-only."""
    listed = ctx.forge(record["repository"]["forge_repository"]).list_prs(record["branch"])
    matches = [pr for pr in listed if not _identity_problems(record, pr)]
    excluded = excluded_prs(ctx.runtime_root, key, record)
    excluded_open = tuple(pr for pr in matches if pr.number in excluded and pr.state == "OPEN")
    live = [pr for pr in matches if pr.number not in excluded]
    by_state = {state: sorted((pr for pr in live if pr.state == state), key=lambda pr: pr.number)
                for state in ("OPEN", "MERGED", "CLOSED")}
    closed = tuple(pr.number for pr in by_state["CLOSED"])
    if excluded_open:
        return Discovery("excluded_open", excluded_open, excluded=excluded)
    if len(by_state["OPEN"]) >= 2:
        return Discovery("many_open", tuple(by_state["OPEN"]))
    if len(by_state["MERGED"]) >= 2:
        return Discovery("many_merged", tuple(by_state["MERGED"]))
    if by_state["MERGED"]:
        return Discovery("merged", tuple(by_state["MERGED"]), closed)
    if by_state["OPEN"]:
        return Discovery("open", tuple(by_state["OPEN"]), closed)
    if by_state["CLOSED"]:
        return Discovery("closed_unmerged", (by_state["CLOSED"][-1],), closed[:-1])
    return Discovery("none")


def _discovery_refusal(ctx: Context, record: Mapping[str, Any], found: Discovery) -> BranchBindingError | None:
    """The three refusal rows, identical from the branch and from trunk."""
    work_item_id, branch = record["work_item_id"], record["branch"]
    numbers = [pr.number for pr in found.prs]
    if found.row == "excluded_open":
        named = ", ".join(f"#{pr.number} ({found.excluded[pr.number]})" for pr in found.prs)
        return _refuse(f"pull request(s) {named} for {branch} are open again. They are never adopted, and no new "
                       f"pull request can be created while one is open for the same head and base.",
                       work_item_id=work_item_id, branch=branch, prs=numbers,
                       exits=[f"close {', '.join('#' + str(n) for n in numbers)} on GitHub"])
    if found.row == "many_open":
        return _refuse(f"{len(numbers)} open pull requests match {branch}: "
                       f"{', '.join('#' + str(n) for n in numbers)}.", work_item_id=work_item_id, branch=branch,
                       prs=numbers, exits=["close the extras on GitHub"])
    if found.row == "many_merged":
        return _refuse(f"{len(numbers)} merged pull requests match {branch}: "
                       f"{', '.join('#' + str(n) for n in numbers)}. The records that excluded the older ones "
                       f"were lost with the runtime root, and the Controller cannot tell which is this binding's.",
                       work_item_id=work_item_id, branch=branch, prs=numbers,
                       exits=[f"the lost-runtime-root recovery: restore "
                              f"<runtime_root>/{milestones_rel('<repo_key>').parent} from a backup"])
    return None


def _discover_on_branch(ctx: Context, key: str, record: dict,
                        head: gitrepo.HeadState) -> Proceed | Gate | dict:
    """PR creation steps 3 to 5 from the branch, for a ``PR_PLANNED``
    record. Returns the next record to continue with (``PR_OPEN``,
    ``BRANCH_BOUND``, ``MERGED``, ...) or an outcome."""
    work_item_id, branch, trunk = record["work_item_id"], record["branch"], record["trunk"]
    found = discover(ctx, key, record)
    refusal = _discovery_refusal(ctx, record, found)
    if refusal is not None:
        raise refusal
    superseded = sorted({*(record.get("superseded_prs") or []), *found.closed})
    if found.row == "merged":
        pr = found.prs[0]
        return _merged_handling(ctx, key, dict(record, superseded_prs=superseded), pr, pr.head_oid)
    if found.row == "open":
        pr = found.prs[0]
        return _write(ctx, key, dict(record, state=PR_OPEN, pr=_pr_ref(pr), superseded_prs=superseded),
                      "pr_adopted", pr=pr.number)
    if found.row == "closed_unmerged":
        pr = found.prs[0]
        return _write(ctx, key, dict(record, state=PR_CLOSED_UNMERGED, pr=_pr_ref(pr), superseded_prs=superseded),
                      "pr_closed_unmerged", pr=pr.number)
    # 0 matches: re-check the creation condition against the same fetch.
    if _ancestor(ctx, head.commit, record["last_observation"]["remote_trunk"]):
        if _phase(committed_state(ctx, head.commit), work_item_id) == MILESTONE_COMPLETE:
            return _merged_handling(ctx, key, record, None, head.commit)  # the PR-less close-out
        return _write(ctx, key, dict(record, state=BRANCH_BOUND), "pr_not_needed", tip=head.commit)
    record = _ensure_pushed(ctx, key, record, head.commit)
    if _squash(record):
        declared = declared_title(ctx, work_item_id)
        title, body = declared.title or work_item_id, squash_body(work_item_id, declared.plan_path)
    else:
        item = _work_items(worktree_state(ctx), "the working tree's state").get(work_item_id) or {}
        title = work_item_id
        body = (f"Milestone `{work_item_id}` (plan: `{item.get('plan_path') or 'unrecorded'}`), driven by "
                f"workflow-controller. A human merges it with \"Create a merge commit\".\n\n"
                + PR_MARKER.format(work_item_id=work_item_id) + "\n")
    forge = ctx.forge(record["repository"]["forge_repository"])
    pr = forge.create_draft_pr(branch, trunk, title, body)
    problems = _identity_problems(record, pr)
    if problems or pr.state != "OPEN":
        raise _refuse(f"the pull request #{pr.number} just created for {branch} does not verify: "
                      + "; ".join(problems or [f"it is {pr.state}"]) + ".",
                      work_item_id=work_item_id, branch=branch, pr=pr.number)
    return _write(ctx, key, dict(record, state=PR_OPEN, pr=dict(_pr_ref(pr), created=True)),
                  "pr_created", pr=pr.number)


# -- the pull request title and body in squash mode --------------------------------------

SQUASH_BUTTON = "Squash and merge"
MERGE_BUTTON = "Create a merge commit"


def _squash(record: Mapping[str, Any]) -> bool:
    """Whether the binding's policy merges milestone pull requests with
    "Squash and merge". Every other mode is 1.3.0's, byte for byte."""
    return binding_policy(record).milestone_branches.merge_method == repo_policy.MERGE_METHOD_SQUASH


def _merge_button(squash: bool) -> tuple[str, str]:
    return ((SQUASH_BUTTON, repo_policy.MERGE_METHOD_SQUASH) if squash
            else (MERGE_BUTTON, repo_policy.MERGE_METHOD_MERGE))


@dataclasses.dataclass(frozen=True)
class DeclaredTitle:
    """The pull request title a plan declares, read at ``HEAD``:
    ``plan_path`` from ``HEAD``'s committed state, ``title`` the declared
    title when there is exactly one valid declaration (else ``None``), and
    ``problem`` why there is none."""

    plan_path: str | None
    title: str | None
    problem: str | None


def plan_title(text: str) -> tuple[str | None, str | None]:
    """``(title, problem)`` for a plan's text: the one line starting with
    :data:`PLAN_TITLE_PREFIX` must match :data:`PLAN_TITLE_RE` whole. Zero
    lines, more than one, or a malformed one declare no title. The title's
    grammar is the caller's to check."""
    lines = [line for line in text.split("\n") if line.startswith(PLAN_TITLE_PREFIX)]
    if len(lines) != 1:
        return None, f"the plan has {len(lines)} `{PLAN_TITLE_PREFIX}` lines, not exactly one"
    match = PLAN_TITLE_RE.fullmatch(lines[0])
    if match is None:
        return None, f"the plan's line {lines[0]!r} is not ``{PLAN_TITLE_PREFIX} `<title>` ``"
    return match["title"], None


def title_problem(ctx: Context, title: str) -> str | None:
    """Why ``title`` is not a valid pull request title, or ``None``: checked
    with :func:`conventional_commit.bump` against the ``change_types`` of the
    policy committed at ``HEAD`` when its trigger is ``conventional_commit``,
    and against the grammar alone otherwise."""
    policy = repo_policy.read_committed_policy(ctx.repo_root, "HEAD")
    try:
        if policy is not None and policy.release.trigger == repo_policy.TRIGGER_CONVENTIONAL_COMMIT:
            conventional_commit.bump(title, policy.release.change_types)
        else:
            conventional_commit.parse(title)
    except InvalidTitleError as exc:
        return str(exc)
    return None


def declared_title(ctx: Context, work_item_id: str) -> DeclaredTitle:
    """The title ``work_item_id``'s plan declares, from ``HEAD``'s commit
    only: ``plan_path`` from the committed state, the plan text from
    ``HEAD:<plan_path>``. The working tree is never read, so an uncommitted
    edit of either changes nothing. A title that is not valid UTF-8 is
    none: it is never repaired into one the plan does not contain."""
    item = _work_items(committed_state(ctx, "HEAD"), f"{STATE_REL_PATH} at HEAD").get(work_item_id) or {}
    plan_path = item.get("plan_path")
    if not isinstance(plan_path, str) or not plan_path:
        return DeclaredTitle(None, None, f"{STATE_REL_PATH} at HEAD records no plan_path for {work_item_id}")
    raw = gitrepo.show(ctx.repo_root, "HEAD", plan_path, runner=ctx.runner)
    if raw is None:
        return DeclaredTitle(plan_path, None, f"HEAD has no {plan_path}")
    title, problem = plan_title(raw.decode("utf-8", "surrogateescape"))
    if title is not None:
        try:
            title.encode("utf-8")
        except UnicodeEncodeError:
            return DeclaredTitle(plan_path, None, f"the plan's declared title {title!r} is not valid UTF-8")
        problem = title_problem(ctx, title)
        if problem is not None:
            title = None
    return DeclaredTitle(plan_path, title, problem)


def squash_body(work_item_id: str, plan_path: str | None, *, accepted: str | None = None,
                branch: str | None = None, notes_block: str | None = None) -> str:
    """The squash-mode pull request body, which becomes the squash commit's
    body. No line parses as a Git trailer (I8). The "Accepted at" line is
    added at readiness, and with it the milestone's release-notes block,
    when there is one, above the Controller's lines (settings-and-telemetry
    D.2)."""
    lines = [f"Milestone `{work_item_id}`, planned in `{plan_path or 'unrecorded'}`, driven by workflow-controller."]
    if accepted is not None:
        lines.append(f"Accepted at {accepted} on `{branch}`; merge with \"{SQUASH_BUTTON}\".")
    head = "" if notes_block is None else notes_block + "\n\n"
    return head + "\n".join(lines) + "\n\n" + PR_MARKER.format(work_item_id=work_item_id) + "\n"


#: Readiness's ``release_notes`` event detail (D.2).
NOTES_ABSENT = "absent"
NOTES_EMPTY = "empty"
NOTES_INCLUDED = "included"


@dataclasses.dataclass(frozen=True)
class _MilestoneNotes:
    """The milestone's notes at the acceptance commit: ``status`` is
    :data:`NOTES_ABSENT`, :data:`NOTES_EMPTY` or :data:`NOTES_INCLUDED`,
    ``block`` the rendered block when included, ``path`` the narrative."""

    status: str
    path: str
    block: str | None = None
    problem: str | None = None


def _milestone_notes(ctx: Context, record: Mapping[str, Any], a: str) -> _MilestoneNotes | None:
    """The notes section at the path and heading of the binding's policy
    snapshot, read from the acceptance commit ``a``'s tree (never the
    working tree); ``None`` when the snapshot has no ``release_notes``.
    ``problem`` names a section that cannot be carried (not UTF-8, or an I8
    line rule)."""
    config = binding_policy(record).milestone_branches.release_notes
    if config is None:
        return None
    work_item_id = record["work_item_id"]
    path = config.path_for(work_item_id)
    raw = gitrepo.show(ctx.repo_root, a, path, runner=ctx.runner)
    if raw is None:
        return _MilestoneNotes(NOTES_ABSENT, path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _MilestoneNotes(NOTES_INCLUDED, path, problem=f"{path} at {a} is not valid UTF-8 ({exc})")
    notes = release_notes.extract_section(text, config.heading)
    if notes is None:
        return _MilestoneNotes(NOTES_ABSENT, path)
    if not notes:
        return _MilestoneNotes(NOTES_EMPTY, path)
    problem = release_notes.notes_problem(notes)
    if problem is not None:
        return _MilestoneNotes(NOTES_INCLUDED, path,
                               problem=f"the `## {config.heading}` section of {path}, {problem.describe()}")
    return _MilestoneNotes(NOTES_INCLUDED, path, block=release_notes.render_block(work_item_id, notes))


def _edit_pr(ctx: Context, key: str, record: Mapping[str, Any], number: int, *, title: str | None = None,
             body: str | None = None, **details: Any) -> None:
    """``gh pr edit``, re-read by the forge. Idempotent: a restart re-reads
    the pull request and finds nothing left to edit."""
    ctx.forge(record["repository"]["forge_repository"]).edit_pr(number, title=title, body=body)
    _event(ctx, key, record, "pr_edited", pr=number, title=title, body=body is not None, **details)


def _sync_title(ctx: Context, key: str, record: Mapping[str, Any], pr: forge_mod.PullRequest) -> None:
    """A ``PR_OPEN`` step in squash mode: set the declared title when the
    pull request's differs. Without a valid declaration nothing is edited."""
    declared = declared_title(ctx, record["work_item_id"])
    if declared.title is not None and pr.title != declared.title:
        _edit_pr(ctx, key, record, pr.number, title=declared.title)


def _sync_for_readiness(ctx: Context, key: str, record: Mapping[str, Any], pr: forge_mod.PullRequest,
                        a: str) -> tuple[Gate | None, str | None]:
    """Readiness in squash mode, after conditions 4-6 and before 7: the
    title decision (the declared title wins; without one a valid current
    title is kept; otherwise ``pr_title_invalid``), then the body with the
    acceptance commit and the milestone's release notes, checked whole
    against I8 before any edit (``release_notes_invalid``). An edit ends the
    step at ``checks_pending``, so ``READY`` is never written on checks
    sampled before it. Also returns the notes status, ``None`` when the
    binding's policy has no ``release_notes``."""
    work_item_id, branch, number = record["work_item_id"], record["branch"], pr.number
    declared = declared_title(ctx, work_item_id)
    title = None
    if declared.title is not None:
        if pr.title != declared.title:
            title = declared.title
    else:
        problem = title_problem(ctx, pr.title)
        if problem is not None:
            return Gate(GATE_PR_TITLE_INVALID, work_item_id, branch,
                        f"pull request #{number}'s title {pr.title!r} is not a valid Conventional Commit ({problem}), "
                        f"and the plan declares none ({declared.problem}). A squash merge makes the title the trunk "
                        f"commit's subject. The plan can no longer be amended after acceptance, so set a valid title "
                        f"on GitHub",
                        (f"set a valid Conventional Commit title on pull request #{number} on GitHub",),
                        repo_policy.MERGE_METHOD_SQUASH), None
    notes = _milestone_notes(ctx, record, a)
    status = None if notes is None else notes.status
    body = squash_body(work_item_id, declared.plan_path, accepted=a, branch=branch,
                       notes_block=None if notes is None else notes.block)
    problem = None if notes is None else notes.problem
    if problem is None and notes is not None and notes.block is not None:
        found = release_notes.paragraph_problem(body)
        if found is not None:
            problem = f"the body carrying the notes of {notes.path}, {found.describe()}"
    if problem is not None:
        return Gate(GATE_RELEASE_NOTES_INVALID, work_item_id, branch,
                    f"the release notes of {work_item_id} cannot go into pull request #{number}'s body: {problem}. "
                    f"The pull request is not edited. A commit after the acceptance commit {a} stops readiness "
                    f"(post_acceptance_commits), so the notes are supplied at release instead",
                    (f"merge pull request #{number} on GitHub with \"{SQUASH_BUTTON}\", then supply the notes in a "
                     f"later trunk commit whose message carries the block `python3 tools/release.py notes-block "
                     f"--work-item {work_item_id} <file>` prints",),
                    repo_policy.MERGE_METHOD_SQUASH), status
    if forge_mod.same_text(pr.body, body):
        body = None
    if title is None and body is None:
        return None, status
    extra = {} if status is None else {"release_notes": status}
    _edit_pr(ctx, key, record, number, title=title, body=body, **extra)
    return Gate(GATE_CHECKS_PENDING, work_item_id, branch,
                f"pull request #{number}'s title or body was just updated; its checks re-run",
                ("re-run the step once the checks finish",), repo_policy.MERGE_METHOD_SQUASH), status


# -- readiness --------------------------------------------------------------------------


def _readiness(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> Gate:
    """Readiness of a ``PR_OPEN`` record whose work item is
    ``MILESTONE_COMPLETE`` in the branch's own committed state: every
    condition, then ``gh pr ready`` (skipped when already ready), a re-read,
    ``READY`` with ``accepted_head``, and the merge gate. A failed condition
    is a gate."""
    work_item_id, branch, trunk = record["work_item_id"], record["branch"], record["trunk"]
    remote, number = record["repository"]["remote"], record["pr"]["number"]
    tip = head.commit
    # 2. a clean tracked tree.
    changes = gitrepo.tracked_changes(ctx.repo_root, runner=ctx.runner)
    if changes:
        return Gate(GATE_DIRTY_TREE, work_item_id, branch,
                    f"{work_item_id} is accepted, but the tracked tree has changes: {', '.join(changes)}",
                    ("commit, stash or discard them",))
    # 3. the tip is exactly the acceptance commit A.
    search = find_acceptance_commit(ctx, work_item_id, record["branch_point"], tip)
    if search.commit is None:
        found = (f"{search.untrailered} records it {MILESTONE_COMPLETE} without the "
                 f"`{TRAILER_WORK_ITEM}: {work_item_id}` trailer, so the phase changed outside /accept-milestone"
                 if search.untrailered else f"no commit on {record['branch_point']}..{tip} records the change")
        raise _refuse(f"{work_item_id} has no acceptance commit: {found}.", work_item_id=work_item_id,
                      branch=branch, untrailered=search.untrailered,
                      exits=["accept the milestone with /accept-milestone"])
    a = search.commit
    squash = _squash(record)
    button, method = _merge_button(squash)
    if tip != a:
        after = gitrepo.first_parent_log(ctx.repo_root, a, tip, runner=ctx.runner)
        return Gate(GATE_POST_ACCEPTANCE_COMMITS, work_item_id, branch,
                    f"{branch} has commits after the acceptance commit {a}: {', '.join(after)}. A pull request is "
                    f"marked ready only at the accepted head, and the Controller never removes commits. A human "
                    f"decides: merge anyway on GitHub (mark it ready, \"{button}\"), after which the "
                    f"merged-PR handling converges; otherwise this gate persists",
                    (f"merge pull request #{number} on GitHub anyway with \"{button}\"",), method)
    # 4. and 5. pushed, and the pull request shows exactly A.
    observation = record["last_observation"]
    pr = _verified_pr(ctx, record, number)
    if observation.get("remote_branch") != a or pr.state != "OPEN" or pr.head_oid != a:
        return Gate(GATE_PR_HEAD_NOT_ACCEPTED, work_item_id, branch,
                    f"pull request #{number} does not show the acceptance commit {a} yet ({remote}/{branch} is "
                    f"{observation.get('remote_branch')}; the pull request is {pr.state} at {pr.head_oid})",
                    ("re-run the step once GitHub shows the pushed head",))
    # 6. fresh: <remote>/<trunk> is an ancestor of A (I7).
    remote_trunk = observation["remote_trunk"]
    if not _ancestor(ctx, remote_trunk, a):
        behind = observation["behind"]
        return Gate(GATE_INTEGRATION_REQUIRED, work_item_id, branch,
                    f"{remote}/{trunk} has moved {behind} commit(s) past {branch}'s base. Workflow 2.5.1 and 2.6.0 "
                    f"have no transition that moves a work item's base, so the Controller does not integrate. "
                    f"The manual procedure: on GitHub, mark pull request #{number} ready and merge it with "
                    f"\"{button}\"",
                    (f"mark pull request #{number} ready and merge it on GitHub with \"{button}\"",), method)
    # Squash mode: the title decision and the body sync, before condition 7,
    # whose failing `PR title` check only this sync can fix. An edit ends the step.
    notes_status = None
    if squash:
        gate, notes_status = _sync_for_readiness(ctx, key, record, pr, a)
        if gate is not None:
            return gate
    # 7. green checks, when the binding's policy requires them.
    if binding_policy(record).milestone_branches.ready_requires_green_checks:
        gate = _checks_gate(ctx, record, number)
        if gate is not None:
            return gate
    forge = ctx.forge(record["repository"]["forge_repository"])
    if pr.is_draft:
        # The one forge mutation with no persisted intent (I5): it is idempotent, and a
        # restart re-reads the pull request, skips it for a non-draft and writes READY.
        forge.mark_ready(number)
        pr = _verified_pr(ctx, record, number)
        if pr.is_draft or pr.state != "OPEN":
            raise _refuse(f"pull request #{number} is still {'a draft' if pr.is_draft else pr.state} after "
                          f"`gh pr ready`", work_item_id=work_item_id, branch=branch, pr=number)
    extra = {} if notes_status is None else {"release_notes": notes_status}
    record = _write(ctx, key, dict(record, state=READY, pr=_pr_ref(pr), accepted_head=a), "ready", pr=number,
                    accepted_head=a, **extra)
    return _merge_gate(record, tip=tip)


def _checks_gate(ctx: Context, record: Mapping[str, Any], number: int) -> Gate | None:
    """Readiness condition 7: at least one check, each ``pass`` or
    ``skipping``. Precedence: failing, then pending (including none
    reported), then cancelled."""
    work_item_id, branch = record["work_item_id"], record["branch"]
    checks = ctx.forge(record["repository"]["forge_repository"]).pr_checks(number)
    by_bucket: dict[str, list[str]] = {}
    for check in checks.checks:
        by_bucket.setdefault(check.bucket, []).append(check.name)
    if by_bucket.get("fail"):
        return Gate(GATE_CHECKS_FAILING, work_item_id, branch,
                    f"pull request #{number} has failing checks: {', '.join(by_bucket['fail'])}",
                    ("fix the failures on the branch, or re-run the checks on GitHub",))
    if checks.outcome == "no_checks" or not checks.checks or by_bucket.get("pending"):
        what = (f"pending checks: {', '.join(by_bucket['pending'])}" if by_bucket.get("pending")
                else "no checks reported yet")
        return Gate(GATE_CHECKS_PENDING, work_item_id, branch,
                    f"pull request #{number} has {what}", ("re-run the step once the checks finish",))
    if by_bucket.get("cancel"):
        return Gate(GATE_CHECKS_CANCELLED, work_item_id, branch,
                    f"pull request #{number} has cancelled checks: {', '.join(by_bucket['cancel'])}",
                    ("re-run the cancelled checks on GitHub",))
    return None


# -- the BRANCH_PLANNED adopt/collision rows ---------------------------------------


def _bind_problems(ctx: Context, work_item_id: str, item: Mapping[str, Any] | None, tip: str) -> list[str]:
    """The failing bind-step preconditions for ``work_item_id`` at trunk tip
    ``tip`` (``HEAD`` on the trunk is the caller's)."""
    if item is None:
        return [f"the working tree's {STATE_REL_PATH} has no entry for {work_item_id}"]
    problems = []
    phase = item.get("phase")
    if phase not in PLAN_STAGE_PHASES:
        problems.append(f"{work_item_id} is in {phase}, not a plan-stage phase")
    if item.get("plan_approval") is not None:
        problems.append(f"{work_item_id}'s plan is already approved (the plan approval commit is on the trunk; "
                        f"the Controller never moves it -- recover by hand)")
    base = item.get("base_commit")
    if not isinstance(base, str) or gitrepo.ref_commit(ctx.repo_root, base, runner=ctx.runner) is None \
            or not _ancestor(ctx, base, tip):
        problems.append(f"{work_item_id}'s base_commit {base} is not an ancestor of the trunk tip {tip}")
    return problems


def _repository_mismatch(ctx: Context, record: Mapping[str, Any]) -> list[str]:
    repository = record["repository"]
    problems = []
    observed_common = str(gitrepo.common_dir(ctx.repo_root, runner=ctx.runner).resolve())
    if observed_common != repository["common_dir"]:
        problems.append(f"common directory: recorded {repository['common_dir']}, observed {observed_common}")
    url = gitrepo.remote_url(ctx.repo_root, repository["remote"], runner=ctx.runner)
    observed = _forge_repository_of(url)
    if observed is not None:
        if observed.lower() != repository["forge_repository"].lower():
            problems.append(f"forge repository: recorded {repository['forge_repository']}, observed {observed} "
                            f"({repository['remote']} is {url})")
    elif url != repository.get("remote_url"):
        problems.append(f"{repository['remote']} URL: recorded {repository.get('remote_url')}, observed {url}")
    return problems


def _reconcile_planned(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> dict:
    """The ``BRANCH_PLANNED`` rows of the adopt/collision table, first match
    wins, from the branch (rule 1) or from trunk (rule 3.1). Returns the
    ``BRANCH_BOUND`` record, with ``HEAD`` then on the branch, or refuses."""
    work_item_id, branch, trunk = record["work_item_id"], record["branch"], record["trunk"]
    remote, t = record["repository"]["remote"], record["branch_point"]

    mismatch = _repository_mismatch(ctx, record)
    if mismatch:
        exits = [f"restore {remote}'s URL to the recorded forge repository {record['repository']['forge_repository']}"]
        try:
            abandon_ok = not abandon_problems(ctx, record, head)
        except GitOperationError:
            abandon_ok = False  # the mismatched remote cannot answer; the exit is simply not offered
        if abandon_ok:
            exits.append(_cli(work_item_id, ABANDON, ctx.repo_root))
        raise _refuse(f"the {branch} binding of {work_item_id} was planned in another repository: "
                      + "; ".join(mismatch) + ".", work_item_id=work_item_id, branch=branch, exits=exits,
                      mismatch=mismatch)

    local = _local_branch(ctx, branch)
    remote_tip = _remote_refs(ctx, remote, [branch])[branch]
    on_trunk = head.branch == trunk

    # Re-bind: the crash came before `switch -c`, so nothing was created.
    if local is None and remote_tip is None and on_trunk and _ancestor(ctx, t, head.commit):
        policy = repo_policy.read_committed_policy(ctx.repo_root, "HEAD")
        item = _work_items(worktree_state(ctx), "the working tree's state").get(work_item_id)
        if repo_policy.milestone_branches_enabled(policy) and not _bind_problems(ctx, work_item_id, item,
                                                                                  head.commit):
            if head.commit != t:
                record = _write(ctx, key, dict(record, branch_point=head.commit, policy=_policy_snapshot(policy)),
                                "rebind", previous_branch_point=t)
            return _switch_and_bind(ctx, key, record)
    # Complete from the branch at T or a descendant of it.
    if local is not None and head.branch == branch and _ancestor(ctx, t, local):
        return _write(ctx, key, dict(record, state=BRANCH_BOUND), "bound", completed="from_branch")
    # The branch at T, HEAD on trunk at T: switch to it (same commit).
    if local == t and on_trunk and head.commit == t:
        gitrepo.switch_at_head(ctx.repo_root, branch, runner=ctx.runner)
        _verify_on(ctx, branch, t)
        return _write(ctx, key, dict(record, state=BRANCH_BOUND), "bound", completed="switched")
    # A branch the bind did not create.
    for where, tip in (("local", local), ("remote", remote_tip)):
        if tip is not None and not _ancestor(ctx, t, tip):
            raise _refuse(f"the {where} {branch} ({tip}) does not descend from the recorded branch point {t} "
                          f"of the planned {work_item_id} binding, so the bind did not create it",
                          work_item_id=work_item_id, branch=branch,
                          exits=[f"remove or rename {branch}, after which the bind re-runs"], observed=tip)
    # The catch-all.
    if local is not None or remote_tip is not None:
        observation = f"{branch} exists ({'locally' if local else 'on ' + remote}) at a descendant of {t}"
        exits = [f"switch to it (`git switch {branch}`), after which the binding completes"]
    else:
        tip = head.commit
        start = tip if on_trunk and tip is not None and _ancestor(ctx, t, tip) else t
        item = _work_items(worktree_state(ctx), "the working tree's state").get(work_item_id)
        problems = _bind_problems(ctx, work_item_id, item, tip) if on_trunk else [f"HEAD is on {head.branch}"]
        observation = (f"{branch} exists neither locally nor on {remote}, and the bind cannot re-run: "
                       + "; ".join(problems))
        if not abandon_problems(ctx, record, head):
            exits = [f"if the plan was discarded, {_cli(work_item_id, ABANDON, ctx.repo_root)}"]
        else:
            exits = [f"create {branch} and switch to it (`git switch -c {branch} {start}`), after which the "
                     f"binding completes"]
    raise _refuse(f"the planned {work_item_id} binding cannot be completed: {observation}.",
                  work_item_id=work_item_id, branch=branch, exits=exits)


def _verify_on(ctx: Context, branch: str, commit: str) -> None:
    after = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
    if after.branch != branch or after.commit != commit:
        raise BranchInvariantViolatedError(
            f"HEAD is on {after.branch or 'a detached commit'} at {after.commit}, not on {branch} at {commit}",
            evidence={"branch": branch, "commit": commit, "head_branch": after.branch, "head": after.commit})


def _switch_and_bind(ctx: Context, key: str, record: dict) -> dict:
    gitrepo.create_and_switch(ctx.repo_root, record["branch"], runner=ctx.runner)
    _verify_on(ctx, record["branch"], record["branch_point"])
    return _write(ctx, key, dict(record, state=BRANCH_BOUND), "bound")


# -- rule 3: HEAD on trunk ----------------------------------------------------------


def _on_trunk(ctx: Context, key: str, records: dict[str, dict], head: gitrepo.HeadState,
              policy: repo_policy.RepositoryPolicy | None, requested: str | None) -> Proceed | Gate:
    for record in records.values():
        if record["state"] == BRANCH_PLANNED:
            continue
        if record["state"] == MERGED_REWRITTEN and not record.get("rewrite_gate_shown"):
            return _report_rewrite(ctx, key, record)
        if record["state"] not in TERMINAL_STATES:
            gate = _reconcile_from_trunk(ctx, key, record, head)
            if gate is not None:
                return gate
    records = live_records(ctx.runtime_root, key)
    blocking = [r for r in records.values() if r["state"] not in TERMINAL_STATES]
    for record in blocking:
        bound = _reconcile_planned(ctx, key, record, head)
        return _on_bound_branch(ctx, key, bound, gitrepo.head_state(ctx.repo_root, runner=ctx.runner), requested)

    if not repo_policy.milestone_branches_enabled(policy):
        return Proceed()
    wt = worktree_state(ctx)
    items = _work_items(wt, "the working tree's state") if wt else {}
    candidates = sorted(
        work_item_id for work_item_id, item in items.items()
        if item.get("phase") != MILESTONE_COMPLETE and item.get("parent_work_item_id") is None
        and (work_item_id not in records or records[work_item_id]["state"] == ABANDONED))
    if len(candidates) > 1:
        raise _refuse(f"several unbound work items are in progress on {head.branch}: {', '.join(candidates)}; "
                      f"the Controller binds one milestone at a time", candidates=candidates)
    if candidates:
        return _bind(ctx, key, policy, head, candidates[0], items[candidates[0]], records.get(candidates[0]))
    return _trunk_start(ctx, policy, head)


def _reconcile_from_trunk(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> Gate | None:
    """Rule 3.1 for one non-terminal or refusal record other than
    ``BRANCH_PLANNED``, with ``HEAD`` on the trunk: the stated
    reconciliations (close-out from trunk, its PR-less form, and PR
    discovery read from the trunk), each converging to ``CLOSED``
    (``None``), a gate, or a refusal naming the record's exits. Never
    switches (I4); never creates a pull request."""
    state = record["state"]
    if state in (PR_OPEN, READY, *CLOSE_OUT_STATES, PR_CLOSED_UNMERGED):
        return _close_out_from_trunk(ctx, key, record, head)
    if state == BRANCH_BOUND:
        h = _branch_tip_for_trunk_side(ctx, record)
        if not _prless_applies(ctx, record, h):
            raise _trunk_block(ctx, record, head)
        _require_not_checked_out_elsewhere(ctx, record)
        return _trunk_merged(ctx, key, _merged_handling(ctx, key, record, None, h), head)
    if state == PR_PLANNED:
        return _discover_from_trunk(ctx, key, record, head)
    raise _trunk_block(ctx, record, head)


def _prless_applies(ctx: Context, record: Mapping[str, Any], h: str) -> bool:
    """The PR-less close-out's condition: after ``fetch``, ``h`` is on
    ``<remote>/<trunk>`` and the bound item is ``MILESTONE_COMPLETE`` in
    ``h``'s committed state."""
    remote_trunk = _remote_trunk(ctx, record["repository"]["remote"], record["trunk"])
    return (_ancestor(ctx, h, remote_trunk)
            and _phase(committed_state(ctx, h), record["work_item_id"]) == MILESTONE_COMPLETE)


def _trunk_merged(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> Gate | None:
    """The trunk side of a merged-PR handling outcome: refuse on
    ``MERGED_BEFORE_ACCEPTANCE``, gate once on ``MERGED_REWRITTEN``, and
    run close-out-from-trunk step 3 on ``MERGED`` and ``MERGED_SQUASHED``."""
    if record["state"] == MERGED_BEFORE_ACCEPTANCE:
        raise _trunk_block(ctx, record, head)
    if record["state"] == MERGED_REWRITTEN:
        return _report_rewrite(ctx, key, record)
    return _close_trunk_step3(ctx, key, record)


def _close_out_from_trunk(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> Gate | None:
    """Close-out from trunk, steps 0 to 3, for a ``PR_OPEN``, ``READY``,
    ``MERGED``, ``MERGED_SQUASHED`` or ``PR_CLOSED_UNMERGED`` record."""
    work_item_id, branch = record["work_item_id"], record["branch"]
    # 0. the bound branch is not checked out in another worktree.
    _require_not_checked_out_elsewhere(ctx, record)
    if record["state"] in CLOSE_OUT_STATES:
        return _close_trunk_step3(ctx, key, record)
    # 1. the pull request must show merged.
    pr = _verified_pr(ctx, record, record["pr"]["number"])
    if pr.state == "OPEN":
        exits = [f"switch to it (`git switch {branch}`)"
                 + (", where the reopened pull request is taken up again" if record["state"] == PR_CLOSED_UNMERGED
                    else "")]
        raise _refuse(f"{work_item_id} is bound to {branch} (binding state {record['state']}), and its pull "
                      f"request #{pr.number} is open, so the trunk cannot start a milestone.",
                      work_item_id=work_item_id, branch=branch, exits=exits, state=record["state"])
    if pr.state == "CLOSED":
        if record["state"] != PR_CLOSED_UNMERGED:
            record = _write(ctx, key, dict(record, state=PR_CLOSED_UNMERGED, pr=_pr_ref(pr)), "pr_closed_unmerged",
                            pr=pr.number, side="trunk")
        raise _trunk_block(ctx, record, head)
    # 2. steps 1 and 2 of the merged-PR handling.
    return _trunk_merged(ctx, key, _merged_handling(ctx, key, record, pr, pr.head_oid), head)


def _close_trunk_step3(ctx: Context, key: str, record: dict) -> Gate | None:
    """Close-out from trunk step 3: the local bound branch, if any, holds
    nothing beyond ``merged_head``, and ``merged_head`` (from
    ``MERGED_SQUASHED``, the squash commit) is on ``<remote>/<trunk>``; then
    ``CLOSED``. Local trunk is not
    fast-forwarded here: the trunk start that follows gates a behind
    trunk."""
    work_item_id, branch, h = record["work_item_id"], record["branch"], record["merged_head"]
    remote, trunk = record["repository"]["remote"], record["trunk"]
    local = _local_branch(ctx, branch)
    if local is not None and not _ancestor(ctx, local, h):
        return _unmerged_commits_gate(ctx, record, local, from_trunk=True)
    remote_trunk = _remote_trunk(ctx, remote, trunk)
    if record["state"] == MERGED_SQUASHED:
        m = record["merge_commit"]
        if not _ancestor(ctx, m, remote_trunk):
            raise _refuse(f"the squash commit {m} of {branch} is not on {remote}/{trunk}", work_item_id=work_item_id,
                          branch=branch, exits=[f"fetch {remote}/{trunk} once the merge is visible"])
    elif not _ancestor(ctx, h, remote_trunk):
        raise _refuse(f"the merged head {h} of {branch} is not on {remote}/{trunk}", work_item_id=work_item_id,
                      branch=branch, exits=[f"fetch {remote}/{trunk} once the merge is visible"])
    _write(ctx, key, dict(record, state=CLOSED), "closed", side="trunk")
    return None


def _discover_from_trunk(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> Gate | None:
    """A ``PR_PLANNED`` record from trunk: step 0, then PR creation steps 3
    and 4 read-only, row by row. Nothing is ever created from trunk."""
    work_item_id, branch = record["work_item_id"], record["branch"]
    _require_not_checked_out_elsewhere(ctx, record)
    found = discover(ctx, key, record)
    refusal = _discovery_refusal(ctx, record, found)
    if refusal is not None:
        raise refusal
    superseded = sorted({*(record.get("superseded_prs") or []), *found.closed})
    if found.row == "merged":
        pr = found.prs[0]
        record = _merged_handling(ctx, key, dict(record, superseded_prs=superseded), pr, pr.head_oid)
        return _trunk_merged(ctx, key, record, head)
    if found.row == "closed_unmerged":
        pr = found.prs[0]
        record = _write(ctx, key, dict(record, state=PR_CLOSED_UNMERGED, pr=_pr_ref(pr), superseded_prs=superseded),
                        "pr_closed_unmerged", pr=pr.number, side="trunk")
        raise _trunk_block(ctx, record, head)
    if found.row == "none":
        h = _branch_tip_for_trunk_side(ctx, record)
        if _prless_applies(ctx, record, h):
            return _trunk_merged(ctx, key, _merged_handling(ctx, key, record, None, h), head)
    # 1 open (adoption is branch-side), or 0 matches without the PR-less close-out.
    what = f"its pull request #{found.prs[0].number} is open" if found.row == "open" else "no pull request exists yet"
    raise _refuse(f"{work_item_id} is bound to {branch} (binding state {PR_PLANNED}), and {what}, so the trunk "
                  f"cannot start a milestone.", work_item_id=work_item_id, branch=branch, state=PR_PLANNED,
                  exits=[f"switch to it (`git switch {branch}`)"])


def _branch_tip_for_trunk_side(ctx: Context, record: Mapping[str, Any]) -> str:
    """``H`` as trunk reads it: the local branch tip, else the remote one. If
    neither exists, refuse naming the last observed tip."""
    branch, remote = record["branch"], record["repository"]["remote"]
    tip = _local_branch(ctx, branch) or _remote_refs(ctx, remote, [branch])[branch]
    if tip is None:
        last = _last_tip(record)
        raise _refuse(f"{branch} exists neither locally nor on {remote}; its last observed tip is {last}",
                      work_item_id=record["work_item_id"], branch=branch,
                      exits=[f"restore it there by hand (`git branch {branch} {last}`)"], last_tip=last)
    return tip


def _last_tip(record: Mapping[str, Any]) -> str:
    return (record.get("last_observation") or {}).get("tip") or record["branch_point"]


def _trunk_block(ctx: Context, record: Mapping[str, Any], head: gitrepo.HeadState) -> BranchBindingError:
    """Rule 3.1's refusal for a record that blocks the trunk start."""
    work_item_id, branch, state = record["work_item_id"], record["branch"], record["state"]
    where = f"{work_item_id} is bound to {branch} (binding state {state}), so the trunk cannot start a milestone."
    if state == PR_CLOSED_UNMERGED:
        gate = _gate_pr_closed_unmerged(ctx, record)
        return _refuse(f"{where} Its pull request was closed without merge.", work_item_id=work_item_id,
                       branch=branch, exits=list(gate.exits), state=state)
    if state == MERGED_BEFORE_ACCEPTANCE:
        gate = _gate_merged_before_acceptance(ctx, record, head)
        untrailered = record.get("untrailered_completion")
        note = (f" {untrailered} records it {MILESTONE_COMPLETE} without the `{TRAILER_WORK_ITEM}` trailer."
                if untrailered else "")
        return _refuse(f"{where} It was merged before acceptance.{note}", work_item_id=work_item_id,
                       branch=branch, exits=list(gate.exits), state=state)
    remote = record["repository"]["remote"]
    exists = _local_branch(ctx, branch) is not None or _remote_refs(ctx, remote, [branch])[branch] is not None
    if not exists:
        last = _last_tip(record)
        return _refuse(f"{where} {branch} exists neither locally nor on {remote}.", work_item_id=work_item_id,
                       branch=branch, exits=[f"restore it at the last observed tip (`git branch {branch} {last}`)"],
                       state=state, last_tip=last)
    exits = [f"switch to it (`git switch {branch}`)"]
    if state == BRANCH_BOUND and not abandon_problems(ctx, record, head):
        exits = [f"if the plan was discarded, {_cli(work_item_id, ABANDON, ctx.repo_root)}",
                 f"if it was stashed, switch back to {branch} and restore it (`git stash pop`, or `git restore`)"]
    return _refuse(where, work_item_id=work_item_id, branch=branch, exits=exits, state=state)


def _bind(ctx: Context, key: str, policy: repo_policy.RepositoryPolicy, head: gitrepo.HeadState,
          work_item_id: str, item: Mapping[str, Any], previous: dict | None) -> Proceed:
    """The bind step: persist ``BRANCH_PLANNED`` at the trunk tip ``T``,
    ``git switch -c``, verify, persist ``BRANCH_BOUND``."""
    branch, remote, t = policy.milestone_branches.branch_name(work_item_id), policy.trunk_remote, head.commit
    problems = _bind_problems(ctx, work_item_id, item, t)
    if problems:
        raise _refuse(f"cannot bind {work_item_id} to {branch}: " + "; ".join(problems) + ".",
                      work_item_id=work_item_id, branch=branch, problems=problems)
    local = _local_branch(ctx, branch)
    remote_tip = gitrepo.ls_remote(ctx.repo_root, remote, [f"refs/heads/{branch}"],
                                   runner=ctx.runner).get(f"refs/heads/{branch}")
    if local is not None or remote_tip is not None:
        exits = []
        if local is not None:
            exits.append(f"`git branch -d {branch}` (`-D` only to discard commits on it)"
                         if previous is not None else f"switch to {branch} if it is this milestone's branch, "
                                                      f"or remove or rename it")
        if remote_tip is not None:
            exits.append(f"`git push {remote} --delete {branch}`" if previous is not None
                         else f"remove {remote}/{branch}, which another clone may own")
        what = ("a leftover branch of the abandoned binding" if previous is not None else "a branch collision")
        raise _refuse(f"cannot bind {work_item_id}: {branch} already exists "
                      f"({'locally' if local else ''}{' and ' if local and remote_tip else ''}"
                      f"{'on ' + remote if remote_tip else ''}), {what}.",
                      work_item_id=work_item_id, branch=branch, exits=exits)
    if previous is not None:
        _retire_abandoned(ctx.runtime_root, key, work_item_id)
    common = str(gitrepo.common_dir(ctx.repo_root, runner=ctx.runner).resolve())
    record = {
        "schema_version": SCHEMA_VERSION, "work_item_id": work_item_id,
        "repository": {"common_dir": common, "worktree_root": str(ctx.repo_root.resolve()), "remote": remote,
                       "remote_url": gitrepo.remote_url(ctx.repo_root, remote, runner=ctx.runner),
                       "forge_repository": policy.forge_repository},
        "policy": _policy_snapshot(policy), "trunk": policy.trunk_branch, "branch": branch,
        "branch_point": t, "workflow_base_commit": item.get("base_commit"),
        "binding_generation": 1 + len(abandoned_records(ctx.runtime_root, key, work_item_id)),
        "state": BRANCH_PLANNED, "pr": None, "superseded_prs": [], "merged_head": None,
        "last_observation": None,
    }
    record = _write(ctx, key, record, "bind_planned", branch_point=t)
    return Proceed(binding=_switch_and_bind(ctx, key, record), action="bound")


def _trunk_start(ctx: Context, policy: repo_policy.RepositoryPolicy, head: gitrepo.HeadState) -> Proceed | Gate:
    trunk, remote = policy.trunk_branch, policy.trunk_remote
    changes = gitrepo.tracked_changes(ctx.repo_root, runner=ctx.runner)
    if changes:
        raise _refuse(f"the tracked tree on {trunk} has changes; a milestone starts from a clean trunk",
                      branch=trunk, exits=["commit, stash or discard them"], tracked_changes=changes)
    remote_tip = _remote_trunk(ctx, remote, trunk)
    if remote_tip == head.commit:
        return Proceed(action="trunk_start", base=head.commit)
    if _ancestor(ctx, head.commit, remote_tip):
        behind = gitrepo.ahead_behind(ctx.repo_root, head.commit, remote_tip, runner=ctx.runner)[1]
        return Gate(GATE_FAST_FORWARD_TRUNK, None, trunk,
                    f"{trunk} is {behind} commit(s) behind {remote}/{trunk}; fast-forward it "
                    f"(`git merge --ff-only {remote}/{trunk}`) before a milestone starts",
                    (f"git merge --ff-only {remote}/{trunk}",))
    if _ancestor(ctx, remote_tip, head.commit):
        raise _refuse(f"{trunk} has commits that are not on {remote}/{trunk}; a milestone starts from the "
                      f"published trunk", branch=trunk, exits=[f"push them to {remote}/{trunk}, or move them "
                                                               f"off {trunk}"], remote_commit=remote_tip)
    raise _refuse(f"{trunk} and {remote}/{trunk} have diverged", branch=trunk,
                  exits=[f"reconcile {trunk} with {remote}/{trunk} by hand"], remote_commit=remote_tip)


# -- rule 2: adopt ----------------------------------------------------------------


def _adopt_candidate(ctx: Context, head_policy: repo_policy.RepositoryPolicy,
                     head: gitrepo.HeadState) -> str | None:
    """The work-item id ``HEAD``'s branch names. A policy edited on the
    milestone branch itself never decides it: the branch is inverted with
    the policy at ``P = merge-base(<remote>/<trunk>, HEAD)`` when that one is
    admissible and enabled, and with ``HEAD``'s only otherwise (the adopt
    preconditions then refuse)."""
    candidate = _invert_branch(head_policy.milestone_branches.branch_format, head.branch)
    remote_trunk = _remote_refs(ctx, head_policy.trunk_remote, [head_policy.trunk_branch])[head_policy.trunk_branch]
    p = None if remote_trunk is None else gitrepo.merge_base(ctx.repo_root, remote_trunk, head.commit,
                                                             runner=ctx.runner)
    if p is None:
        return candidate
    try:
        policy_at_p = repo_policy.read_committed_policy(ctx.repo_root, p)
    except InvalidRepositoryPolicyError:
        return candidate
    if not repo_policy.milestone_branches_enabled(policy_at_p):
        return candidate
    return _invert_branch(policy_at_p.milestone_branches.branch_format, head.branch)



def _adopt(ctx: Context, key: str, head_policy: repo_policy.RepositoryPolicy, head: gitrepo.HeadState,
           work_item_id: str, requested: str | None) -> Proceed | Gate:
    """The adopt row: ``HEAD`` on ``milestone/<id>`` with no binding record
    (a human created the branch, or the runtime root was lost)."""
    branch, remote, trunk = head.branch, head_policy.trunk_remote, head_policy.trunk_branch
    refs = _remote_refs(ctx, remote, [trunk, branch])
    problems = []
    p = None if refs[trunk] is None else gitrepo.merge_base(ctx.repo_root, refs[trunk], head.commit,
                                                            runner=ctx.runner)
    if p is None:
        problems.append(f"{branch} shares no history with {remote}/{trunk}")
    item = (_work_items(worktree_state(ctx), "the working tree's state") or {}).get(work_item_id)
    policy_at_p = None
    if item is None:
        problems.append(f"the working tree's {STATE_REL_PATH} has no entry for {work_item_id}")
    elif p is not None:
        if item.get("parent_work_item_id") is not None:
            problems.append(f"{work_item_id} is a remediation child, which never gets its own binding")
        base = item.get("base_commit")
        if not isinstance(base, str) or gitrepo.ref_commit(ctx.repo_root, base, runner=ctx.runner) is None \
                or not _ancestor(ctx, base, p):
            problems.append(f"{work_item_id}'s base_commit {base} is not an ancestor of the branch point {p}")
        if item.get("phase") == MILESTONE_COMPLETE:
            acceptance = find_acceptance_commit(ctx, work_item_id, p, head.commit)
            if acceptance.commit is None:
                problems.append(f"{work_item_id} is {MILESTONE_COMPLETE} but its acceptance commit is not on "
                                f"{p}..{head.commit}")
        if item.get("plan_approval") is not None and not _approval_commits(ctx, work_item_id, p, head.commit):
            problems.append(f"{work_item_id}'s plan approval commit is not on {p}..{head.commit}; if it is on "
                            f"the trunk, it was approved on the trunk and needs manual recovery")
        if refs[branch] is not None and not _ancestor(ctx, refs[branch], head.commit):
            problems.append(f"{remote}/{branch} ({refs[branch]}) is not an ancestor of HEAD")
        try:
            policy_at_p = repo_policy.read_committed_policy(ctx.repo_root, p)
        except InvalidRepositoryPolicyError as exc:
            problems.append(f"the policy at the branch point {p} is inadmissible: {exc.message}")
        else:
            if not repo_policy.milestone_branches_enabled(policy_at_p):
                problems.append(f"the branch point {p} has no enabled milestone-branch policy")
            elif policy_at_p.milestone_branches.branch_name(work_item_id) != branch:
                problems.append(f"the policy at the branch point {p} names another branch for {work_item_id}")
    if problems:
        raise _refuse(f"cannot adopt {branch} for {work_item_id}: " + "; ".join(problems) + ".",
                      work_item_id=work_item_id, branch=branch, problems=problems,
                      exits=[f"switch to {trunk}"])
    record = {
        "schema_version": SCHEMA_VERSION, "work_item_id": work_item_id,
        "repository": {"common_dir": str(gitrepo.common_dir(ctx.repo_root, runner=ctx.runner).resolve()),
                       "worktree_root": str(ctx.repo_root.resolve()), "remote": policy_at_p.trunk_remote,
                       "remote_url": gitrepo.remote_url(ctx.repo_root, policy_at_p.trunk_remote, runner=ctx.runner),
                       "forge_repository": policy_at_p.forge_repository},
        "policy": _policy_snapshot(policy_at_p), "trunk": policy_at_p.trunk_branch, "branch": branch,
        "branch_point": p, "workflow_base_commit": item.get("base_commit"),
        "binding_generation": 1 + len(abandoned_records(ctx.runtime_root, key, work_item_id)),
        "state": BRANCH_BOUND, "pr": None, "superseded_prs": [], "merged_head": None, "last_observation": None,
    }
    record = _write(ctx, key, record, "adopted", branch_point=p)
    outcome = _on_bound_branch(ctx, key, record, head, requested)
    if isinstance(outcome, Proceed):
        return Proceed(work_item_override=outcome.work_item_override, binding=outcome.binding, action="adopted")
    return outcome


# ---------------------------------------------------------------------------
# The operator acknowledgement (``milestone-binding``).
# ---------------------------------------------------------------------------


def abandon_problems(ctx: Context, record: Mapping[str, Any], head: gitrepo.HeadState) -> list[str]:
    """The failing ``--abandon`` preconditions for ``record`` (empty: it
    holds). Contacts the remote (``fetch``)."""
    work_item_id, branch, state = record["work_item_id"], record["branch"], record["state"]
    remote, trunk = record["repository"]["remote"], record["trunk"]
    if state not in REFUSAL_STATES and state not in (BRANCH_PLANNED, BRANCH_BOUND):
        return [f"--abandon does not apply to a {state} binding"]
    problems = []
    refs = _remote_refs(ctx, remote, [trunk, branch])
    if refs[trunk] is None:
        problems.append(f"{remote} has no {trunk} branch")
    elif _non_terminal(committed_state(ctx, refs[trunk]), work_item_id):
        problems.append(f"{remote}/{trunk} already carries {work_item_id}'s non-terminal state, and Workflow "
                        f"2.5.1 and 2.6.0 have no abandonment transition")
    if state in REFUSAL_STATES:
        return problems
    local = _local_branch(ctx, branch)
    if state == BRANCH_PLANNED:
        if local is not None or refs[branch] is not None:
            problems.append(f"{branch} exists")
        if head.branch != trunk:
            problems.append(f"HEAD is not on {trunk}")
    else:
        if local != record["branch_point"]:
            problems.append(f"the tip of {branch} ({local}) is not the branch point {record['branch_point']}")
        if refs[branch] is not None:
            problems.append(f"{remote}/{branch} exists")
        if record.get("pr") is not None or record.get("superseded_prs"):
            problems.append("a pull request existed for this binding")
        last = (record.get("last_observation") or {}).get("tip")
        if last is not None and last != record["branch_point"]:
            problems.append(f"a preflight observed a commit on {branch} ({last})")
        if head.branch not in (trunk, branch):
            problems.append(f"HEAD is on neither {trunk} nor {branch}")
    wt = worktree_state(ctx)
    if _non_terminal(wt, work_item_id):
        problems.append(f"the working tree's {STATE_REL_PATH} still has {work_item_id} in progress")
    if head.commit is not None and _non_terminal(committed_state(ctx, "HEAD"), work_item_id):
        problems.append(f"HEAD's committed {STATE_REL_PATH} still has {work_item_id} in progress")
    return problems


def acknowledge(ctx: Context, work_item_id: str, disposition: str) -> dict:
    """``milestone-binding --new-pr`` / ``--abandon`` for ``work_item_id``:
    the common checks in order, then an ``acknowledged`` event and the new
    record. Any failed check refuses and writes nothing. Never touches a
    ref or a pull request. The caller holds the lifecycle lock."""
    if disposition not in DISPOSITIONS:
        raise _refuse(f"unknown disposition {disposition!r}", work_item_id=work_item_id)
    key = repo_key(gitrepo.common_dir(ctx.repo_root, runner=ctx.runner))
    record = read_record(ctx.runtime_root, key, work_item_id)
    if record is None:
        raise _refuse(f"this repository has no binding record for {work_item_id}", work_item_id=work_item_id)
    state, branch = record["state"], record["branch"]
    # 1. the record's state admits the disposition.
    admitted = state in REFUSAL_STATES or (disposition == ABANDON and state in (BRANCH_PLANNED, BRANCH_BOUND))
    if not admitted:
        raise _refuse(f"--{disposition} does not apply to the {state} binding of {work_item_id}",
                      work_item_id=work_item_id, branch=branch, state=state)
    # 2. HEAD on trunk or the bound branch, not checked out elsewhere.
    head = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
    if head.branch not in (record["trunk"], branch):
        raise _refuse(f"HEAD is on {head.branch or 'a detached commit'}; run this from {record['trunk']} or "
                      f"{branch}", work_item_id=work_item_id, branch=branch)
    _require_not_checked_out_elsewhere(ctx, record)
    # 3. a fresh read of the recorded pull request still shows the state that produced the record.
    pr = record.get("pr")
    if pr is not None and state in REFUSAL_STATES:
        live = ctx.forge(record["repository"]["forge_repository"]).view_pr(int(pr["number"]))
        if live.state == "OPEN":
            raise _refuse(f"pull request #{live.number} is open again", work_item_id=work_item_id, branch=branch,
                          exits=[f"switch to {branch}, where the next step continues with it"])
        expected = "CLOSED" if state == PR_CLOSED_UNMERGED else "MERGED"
        if live.state != expected:
            raise _refuse(f"pull request #{live.number} is {live.state}, not {expected} as the {state} binding "
                          f"recorded", work_item_id=work_item_id, branch=branch)
    # 4. the disposition's own precondition.
    if disposition == ABANDON:
        problems = abandon_problems(ctx, record, head)
        if problems:
            exits = [_cli(work_item_id, NEW_PR, ctx.repo_root)] if state in REFUSAL_STATES else []
            if state == BRANCH_PLANNED:
                exits.append(f"create {branch} and switch to it (`git switch -c {branch}`), after which the "
                             f"binding completes")
            if state == BRANCH_BOUND:
                exits.append("restore the plan (`git restore docs/ai-workflow/WORKFLOW_STATE.json` and the plan "
                             "files, or `git stash pop`)")
            raise _refuse(f"--abandon's precondition fails: " + "; ".join(problems) + ".",
                          work_item_id=work_item_id, branch=branch, exits=exits, problems=problems)
    # 5. --new-pr must not lead straight back to MERGED_BEFORE_ACCEPTANCE.
    if disposition == NEW_PR and state == MERGED_BEFORE_ACCEPTANCE:
        problem = _mba_new_pr_problem(ctx, record)
        if problem is not None:
            raise _refuse(f"--new-pr refused: {problem}.", work_item_id=work_item_id, branch=branch,
                          exits=[_cli(work_item_id, ABANDON, ctx.repo_root)])

    if disposition == ABANDON:
        updated = dict(record, state=ABANDONED)
    else:
        superseded = list(record.get("superseded_prs") or [])
        if pr is not None:
            superseded.append(int(pr["number"]))
        updated = dict(record, state=BRANCH_BOUND, pr=None, superseded_prs=superseded)
    _event(ctx, key, record, "acknowledged", disposition=disposition, previous_state=state,
           pr=None if pr is None else pr.get("number"),
           reason=f"milestone-binding --{disposition} on a {state} binding")
    return _write(ctx, key, updated)


# ---------------------------------------------------------------------------
# The lifecycle wiring (CP8): the no-policy probe, the post-step
# verification, and the read-only observation `inspect`/`explain`/`status`
# render (I10).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Probe:
    """What the no-policy probe read: the repository key, its live binding
    records (a filesystem read) and whether ``HEAD``'s committed tree lists
    the policy file."""

    key: str
    records: Mapping[str, dict]
    policy_committed: bool

    @property
    def inactive(self) -> bool:
        """No binding record and no policy at ``HEAD``: I1 holds, and
        nothing else runs."""
        return not self.records and not self.policy_committed


def probe(ctx: Context) -> Probe:
    """The no-policy probe (I1): exactly two read-only ``git`` calls, in
    this order -- ``rev-parse --path-format=absolute --git-common-dir``
    (for ``repo_key``) and ``ls-tree -z HEAD -- <policy path>`` -- or four
    on an unborn ``HEAD`` (:func:`controller.gitrepo.head_tree_has`)."""
    key = repo_key(gitrepo.common_dir(ctx.repo_root, runner=ctx.runner))
    records = live_records(ctx.runtime_root, key)
    committed = gitrepo.head_tree_has(ctx.repo_root, repo_policy.POLICY_PATH, runner=ctx.runner)
    return Probe(key=key, records=records, policy_committed=committed)


def repository_preflight(ctx: Context, *, requested_work_item_id: str | None = None) -> Proceed | Gate:
    """Step 1b of a lifecycle step: the probe, then, unless it found
    nothing, :func:`preflight`. With no binding record and no policy at
    ``HEAD`` it returns ``Proceed()`` after the probe's own calls and
    nothing else (I1)."""
    found = probe(ctx)
    if found.inactive:
        return Proceed()
    return preflight(ctx, requested_work_item_id=requested_work_item_id,
                     head_policy=_UNSET if found.policy_committed else None)


def verify_post_step(ctx: Context, binding: Mapping[str, Any], pre_step_tip: str) -> None:
    """The post-step verification of a worker job run under ``binding``:
    ``HEAD`` is still attached to the bound branch, and its tip descends
    from ``pre_step_tip``. Raises :class:`BranchInvariantViolatedError`;
    the Controller never repairs the branch."""
    branch = binding["branch"]
    head = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
    evidence = {"work_item_id": binding["work_item_id"], "branch": branch, "pre_step_tip": pre_step_tip,
                "head_branch": head.branch, "head": head.commit}
    if head.branch != branch:
        raise BranchInvariantViolatedError(
            f"the worker left HEAD on {head.branch or 'a detached commit'}, not on the bound branch {branch}",
            evidence=evidence)
    if head.commit is None or not _ancestor(ctx, pre_step_tip, head.commit):
        raise BranchInvariantViolatedError(
            f"the worker rewrote {branch}: its tip {head.commit} does not descend from the pre-step tip "
            f"{pre_step_tip}", evidence=evidence)


def _policy_view(policy: repo_policy.RepositoryPolicy, source: str) -> dict:
    return {
        "path": repo_policy.POLICY_PATH,
        "source": source,
        "sha256": policy.sha256,
        "milestone_branches_enabled": policy.milestone_branches.enabled,
        "release_enabled": policy.release.enabled,
        "trunk": {"branch": policy.trunk_branch, "remote": policy.trunk_remote},
        "forge_repository": policy.forge_repository,
    }


def _binding_view(record: Mapping[str, Any]) -> dict:
    pr = record.get("pr")
    observation = record.get("last_observation") or None
    return {
        "work_item_id": record["work_item_id"],
        "state": record["state"],
        "branch": record["branch"],
        "trunk": record["trunk"],
        "branch_point": record["branch_point"],
        "binding_generation": record["binding_generation"],
        "pr": None if pr is None else {"number": pr.get("number"), "url": pr.get("url"),
                                       "draft": pr.get("is_draft")},
        "last_observation": None if observation is None else {
            key: observation.get(key)
            for key in ("observed_at", "tip", "remote_branch", "remote_trunk", "fresh", "behind")},
    }


def _governing(records: Mapping[str, dict], head: gitrepo.HeadState) -> dict | None:
    if head.branch is None:
        return None
    return next((record for record in records.values() if record["branch"] == head.branch), None)


def observation(ctx: Context) -> dict:
    """``inspect``'s additive blocks, read from local Git and the records
    only: ``repository_policy`` when a policy is committed at ``HEAD`` or a
    binding governs (the governing binding's snapshot, else ``HEAD``'s),
    and ``milestone_branch`` when a binding governs. Both are omitted --
    the result is ``{}`` -- when the probe finds nothing (I1)."""
    found = probe(ctx)
    if found.inactive:
        return {}
    head = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
    governing = _governing(found.records, head)
    out: dict[str, Any] = {}
    if governing is not None:
        out["repository_policy"] = _policy_view(binding_policy(governing), "binding")
        out["milestone_branch"] = _binding_view(governing)
    elif found.policy_committed:
        out["repository_policy"] = _policy_view(repo_policy.read_committed_policy(ctx.repo_root, "HEAD"), "HEAD")
    return out


def binding_entries(runtime_root: Path) -> list[dict]:
    """``status``'s bindings: one entry per live binding record under
    ``runtime_root``, for every repository. Read-only."""
    base = Path(runtime_root) / "repositories"
    if not base.is_dir():
        return []
    entries = []
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        for work_item_id, record in live_records(runtime_root, directory.name).items():
            pr = record.get("pr")
            entries.append({"work_item_id": work_item_id, "state": record["state"], "branch": record["branch"],
                            "pull_request": None if pr is None else pr.get("number"),
                            "worktree_root": record["repository"].get("worktree_root")})
    return entries


def binding_lines(runtime_root: Path) -> list[str]:
    """``status``'s ``milestone:`` lines, one per :func:`binding_entries`
    entry."""
    lines = []
    for entry in binding_entries(runtime_root):
        pr_text = "" if entry["pull_request"] is None else f", pull request #{entry['pull_request']}"
        lines.append(f"milestone: {entry['work_item_id']} {entry['state']} on {entry['branch']}{pr_text} "
                     f"(worktree {entry['worktree_root']})")
    return lines


def _prediction(action: str, detail: str, *, record: Mapping[str, Any] | None = None,
                work_item_id: str | None = None, branch: str | None = None, gate: str | None = None,
                network: bool = False, base: str | None = None) -> dict:
    as_of = ((record or {}).get("last_observation") or {}).get("observed_at")
    if network:
        detail = f"{detail} (as of {as_of or 'no observation yet'})"
    return {
        "action": action,
        "work_item_id": work_item_id if record is None else record["work_item_id"],
        "branch": branch if record is None else record["branch"],
        "binding_state": None if record is None else record["state"],
        "gate": gate,
        "detail": detail,
        "as_of": as_of if network else None,
        "base": base,
    }


def _local_remote_trunk(ctx: Context, remote: str, trunk: str) -> str | None:
    return gitrepo.ref_commit(ctx.repo_root, f"refs/remotes/{remote}/{trunk}", runner=ctx.runner)


def predict(ctx: Context, *, requested_work_item_id: str | None = None) -> dict | None:
    """``explain``'s ``repository_preflight``: the preflight outcome the next
    step would take -- ``bind``, ``adopt``, ``complete_binding``, ``push``,
    ``create_pr``, ``ready``, ``close_out``, ``gate``, ``refuse`` or
    ``proceed`` -- computed from local Git and the records only, with no
    fetch and no ``gh`` (I10). Anything that depends on the network is
    labelled "as of" the binding's last observation. ``None`` when the probe
    finds nothing (I1). The step itself decides; this only predicts."""
    found = probe(ctx)
    if found.inactive:
        return None
    head = gitrepo.head_state(ctx.repo_root, runner=ctx.runner)
    policy = repo_policy.read_committed_policy(ctx.repo_root, "HEAD") if found.policy_committed else None
    governing = _governing(found.records, head)
    if governing is not None:
        return _predict_on_branch(ctx, governing, head, requested_work_item_id)
    if not found.records and not repo_policy.milestone_branches_enabled(policy):
        return _prediction("proceed", "the policy at HEAD does not enable milestone branches")
    if head.branch is None:
        return _prediction("refuse", "HEAD is detached; attach it to the trunk or to a milestone branch")
    trunks = ({policy.trunk_branch} if policy is not None
              else {record["trunk"] for record in found.records.values()})
    if head.branch in trunks:
        return _predict_on_trunk(ctx, found.records, head, policy)
    if policy is not None:
        candidate = _invert_branch(policy.milestone_branches.branch_format, head.branch)
        if candidate is not None and candidate not in found.records:
            return _prediction("adopt", f"{head.branch} is adopted as the branch of {candidate}, subject to "
                                        f"the adopt preconditions", work_item_id=candidate, branch=head.branch)
    return _prediction("refuse", f"HEAD is on {head.branch}, which is neither the trunk nor a milestone branch "
                                 f"this repository can bind", branch=head.branch)


def _predict_on_branch(ctx: Context, record: Mapping[str, Any], head: gitrepo.HeadState,
                       requested: str | None) -> dict:
    state, work_item_id, branch = record["state"], record["work_item_id"], record["branch"]
    if state == BRANCH_PLANNED:
        return _prediction("complete_binding", f"the interrupted bind of {work_item_id} completes", record=record)
    if state == MERGED_REWRITTEN and not record.get("rewrite_gate_shown"):
        return _prediction("gate", "the merge rewrote the reviewed history", record=record,
                           gate=GATE_MERGE_METHOD_REWROTE_HISTORY)
    if state in TERMINAL_STATES:
        return _prediction("gate", f"the binding is {state}; switch to {record['trunk']}", record=record,
                           gate=GATE_SWITCH_TO_TRUNK)
    if state == PR_CLOSED_UNMERGED:
        return _prediction("gate", "the pull request was closed without merge, unless it has been reopened",
                           record=record, gate=GATE_PR_CLOSED_UNMERGED, network=True)
    if state == MERGED_BEFORE_ACCEPTANCE:
        return _prediction("gate", "the branch was merged before acceptance", record=record,
                           gate=GATE_MERGED_BEFORE_ACCEPTANCE)
    wt = worktree_state(ctx)
    items = _work_items(wt, "the working tree's state") if wt else {}
    if work_item_id not in items:
        return _prediction("gate", f"{work_item_id} is missing from the working tree's state", record=record,
                           gate=GATE_BOUND_ITEM_MISSING)
    selected = requested or wt.get("active_work_item_id")
    if selected is not None and selected != work_item_id \
            and (items.get(selected) or {}).get("parent_work_item_id") != work_item_id:
        return _prediction("refuse", f"{selected} is neither {work_item_id}, which {branch} is bound to, nor one "
                                     f"of its remediation children", record=record)
    if state == MERGED:
        return _prediction("close_out", f"the merged binding closes out and HEAD switches to {record['trunk']}",
                           record=record)
    if state == MERGED_SQUASHED:
        return _prediction("close_out", f"the squash-merged binding closes out and HEAD switches to "
                                        f"{record['trunk']}", record=record)
    if state == READY:
        return _prediction("gate", "the pull request is ready; a human merges it", record=record,
                           gate=GATE_MERGE_PULL_REQUEST, network=True)
    tip = head.commit
    observed = record.get("last_observation") or {}
    complete = tip is not None and _phase(committed_state(ctx, "HEAD"), work_item_id) == MILESTONE_COMPLETE
    if state == PR_OPEN:
        if complete:
            return _prediction("ready", "readiness is checked and the pull request is marked ready if it holds",
                               record=record, network=True)
        if observed.get("remote_branch") != tip:
            return _prediction("push", f"{branch} is pushed at {tip}", record=record, network=True)
        return _prediction("proceed", "the pull request is open", record=record, network=True)
    if state == PR_PLANNED:
        return _prediction("create_pr", "the pull request is discovered, or created as a draft", record=record,
                           network=True)
    remote_trunk = _local_remote_trunk(ctx, record["repository"]["remote"], record["trunk"]) \
        or observed.get("remote_trunk")
    if tip is not None and remote_trunk is not None and not _ancestor(ctx, tip, remote_trunk):
        return _prediction("create_pr", f"{branch} is pushed and a draft pull request is created", record=record,
                           network=True)
    if complete:
        return _prediction("close_out", "the PR-less close-out runs", record=record, network=True)
    return _prediction("proceed", f"{branch} has no commit beyond the trunk yet", record=record, network=True)


def _predict_on_trunk(ctx: Context, records: Mapping[str, dict], head: gitrepo.HeadState,
                      policy: repo_policy.RepositoryPolicy | None) -> dict:
    for record in records.values():
        state = record["state"]
        if state == BRANCH_PLANNED:
            return _prediction("complete_binding", f"the interrupted bind of {record['work_item_id']} completes",
                               record=record)
        if state == MERGED_REWRITTEN and not record.get("rewrite_gate_shown"):
            return _prediction("gate", "the merge rewrote the reviewed history", record=record,
                               gate=GATE_MERGE_METHOD_REWROTE_HISTORY)
        if state == MERGED:
            return _prediction("close_out", "the merged binding closes out from the trunk", record=record)
        if state == MERGED_SQUASHED:
            return _prediction("close_out", "the squash-merged binding closes out from the trunk", record=record)
        if state not in TERMINAL_STATES:
            return _prediction("refuse", f"{record['work_item_id']} is bound to {record['branch']} (binding "
                                         f"state {state}), so the trunk cannot start a milestone, unless its "
                                         f"pull request has been merged", record=record, network=True)
    if not repo_policy.milestone_branches_enabled(policy):
        return _prediction("proceed", "the policy at HEAD does not enable milestone branches")
    wt = worktree_state(ctx)
    items = _work_items(wt, "the working tree's state") if wt else {}
    candidates = sorted(
        work_item_id for work_item_id, item in items.items()
        if item.get("phase") != MILESTONE_COMPLETE and item.get("parent_work_item_id") is None
        and (work_item_id not in records or records[work_item_id]["state"] == ABANDONED))
    if len(candidates) > 1:
        return _prediction("refuse", f"several unbound work items are in progress: {', '.join(candidates)}",
                           branch=head.branch)
    if candidates:
        branch = policy.milestone_branches.branch_name(candidates[0])
        return _prediction("bind", f"{candidates[0]} is bound to {branch}, subject to the bind preconditions",
                           work_item_id=candidates[0], branch=branch)
    trunk, remote = policy.trunk_branch, policy.trunk_remote
    if gitrepo.tracked_changes(ctx.repo_root, runner=ctx.runner):
        return _prediction("refuse", f"the tracked tree on {trunk} has changes", branch=trunk)
    remote_tip = _local_remote_trunk(ctx, remote, trunk)
    fetched = f"as of the last fetch of {remote}/{trunk}"
    if remote_tip is None or head.commit is None:
        return _prediction("proceed", f"the trunk start compares {trunk} with {remote}/{trunk} ({fetched})",
                           branch=trunk)
    if remote_tip == head.commit:
        return _prediction("proceed", f"{trunk} equals {remote}/{trunk} ({fetched})", branch=trunk,
                           base=head.commit)
    if _ancestor(ctx, head.commit, remote_tip):
        return _prediction("gate", f"{trunk} is behind {remote}/{trunk} ({fetched})", branch=trunk,
                           gate=GATE_FAST_FORWARD_TRUNK)
    return _prediction("refuse", f"{trunk} is ahead of or diverged from {remote}/{trunk} ({fetched})",
                       branch=trunk)
