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
"Work-item resolution", rules 1-4) and performs this checkpoint's writers:
the bind step, the adopt row and the ``BRANCH_PLANNED`` adopt/collision rows,
the trunk-start preflight, the per-preflight no-rewrite and remote checks,
and branch sync. :func:`acknowledge` is the operator acknowledgement
(``milestone-binding --new-pr`` / ``--abandon``). The pull-request lifecycle,
readiness and close-out build on these in CP7; the lifecycle wiring is CP8.

Every Git call goes through :mod:`controller.gitrepo`, every record write
through :mod:`controller.runtime`. Every record write is checked against
:data:`TRANSITIONS`; any other write raises :class:`BranchBindingError` and
writes nothing. Nothing here deletes a ref, forces anything, or merges (I2,
I3), and ``HEAD`` moves only in the bind step and when adopting a
crash-interrupted bind whose branch sits at ``HEAD``'s own commit (I4).
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
from . import gitrepo, repo_policy, runtime
from .errors import BranchBindingError, BranchInvariantViolatedError, GitOperationError, InvalidRepositoryPolicyError

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
CLOSED = "CLOSED"
MERGED_REWRITTEN = "MERGED_REWRITTEN"
MERGED_BEFORE_ACCEPTANCE = "MERGED_BEFORE_ACCEPTANCE"
PR_CLOSED_UNMERGED = "PR_CLOSED_UNMERGED"
ABANDONED = "ABANDONED"

STATES = (BRANCH_PLANNED, BRANCH_BOUND, PR_PLANNED, PR_OPEN, READY, MERGED, CLOSED,
          MERGED_REWRITTEN, MERGED_BEFORE_ACCEPTANCE, PR_CLOSED_UNMERGED, ABANDONED)

#: The Controller drives these.
NON_TERMINAL_STATES = frozenset({BRANCH_PLANNED, BRANCH_BOUND, PR_PLANNED, PR_OPEN, READY, MERGED})
#: Blocking until an exit moves the record out, from the branch or trunk.
REFUSAL_STATES = frozenset({MERGED_BEFORE_ACCEPTANCE, PR_CLOSED_UNMERGED})
#: Never blocking.
TERMINAL_STATES = frozenset({CLOSED, MERGED_REWRITTEN, ABANDONED})

#: The three outcomes of the merged-PR handling (CP7's "Close-out").
MERGED_PR_HANDLING = (MERGED, MERGED_REWRITTEN, MERGED_BEFORE_ACCEPTANCE)


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
    | _pairs([MERGED], [CLOSED])
)

#: The bind step's plan-stage phases: an explicit set, because Workflow's
#: ``KNOWN_PHASES`` has no order. ``AMENDING_PLAN`` always has
#: ``plan_approval`` set and is excluded.
PLAN_STAGE_PHASES = frozenset({
    "PLANNING", "SELF_REVIEWING_PLAN", "AWAITING_EXTERNAL_PLAN_REVIEW", "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_PLAN_APPROVAL",
})
#: Workflow 2.5.1's ``TERMINAL_PHASES`` (``scripts/workflow_state.py:321``).
MILESTONE_COMPLETE = "MILESTONE_COMPLETE"

TRAILER_WORK_ITEM = "Workflow-Work-Item"
TRAILER_PLAN_APPROVAL = "Workflow-Plan-Approval"

# Gate codes (CP7 gives each its ``HumanGate`` text in ``decision.py``).
GATE_SWITCH_TO_TRUNK = "switch_to_trunk"
GATE_BOUND_ITEM_MISSING = "bound_item_missing"
GATE_PR_CLOSED_UNMERGED = "pr_closed_unmerged"
GATE_MERGED_BEFORE_ACCEPTANCE = "merged_before_acceptance"
GATE_FAST_FORWARD_TRUNK = "fast_forward_trunk"

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
    and a clock."""

    repo_root: Path
    runtime_root: Path
    runner: gitrepo.Runner | None = None
    forge_factory: Callable[[str], forge_mod.Forge] | None = None
    clock: Callable[[], str] = _utc_now

    def forge(self, repository: str) -> forge_mod.Forge:
        return (self.forge_factory or forge_mod.GhForge)(repository)


@dataclasses.dataclass(frozen=True)
class Proceed:
    """The step proceeds. ``work_item_override`` replaces ``decide``'s own
    selection (only after acceptance, and only with the bound work item);
    ``binding`` is the governing record, or ``None`` when no binding governs
    the step; ``action`` names what this preflight did (``none``, ``bound``,
    ``adopted``, ``completed``, ``observed``, ``trunk_start``)."""

    work_item_override: str | None = None
    binding: Mapping[str, Any] | None = None
    action: str = "none"


@dataclasses.dataclass(frozen=True)
class Gate:
    """The step stops for a human: ``code`` names the gate, ``exits`` the
    actions that clear it, in the order the message states them."""

    code: str
    work_item_id: str | None
    branch: str | None
    message: str
    exits: tuple[str, ...] = ()


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
    state = record["state"]
    if state in TERMINAL_STATES:
        return Gate(GATE_SWITCH_TO_TRUNK, work_item_id, branch,
                    f"the {branch} binding of {work_item_id} is {state}; switch to the trunk "
                    f"(`git switch {record['trunk']}`)", (f"git switch {record['trunk']}",))
    if state == PR_CLOSED_UNMERGED:
        return _gate_pr_closed_unmerged(ctx, record)
    if state == MERGED_BEFORE_ACCEPTANCE:
        return _gate_merged_before_acceptance(ctx, record, head)

    wt = worktree_state(ctx)
    items = _work_items(wt, "the working tree's state") if wt else {}
    if work_item_id not in items:
        return _gate_bound_item_missing(ctx, record, head)
    selected = requested or wt.get("active_work_item_id")
    if selected is not None and selected != work_item_id \
            and (items.get(selected) or {}).get("parent_work_item_id") != work_item_id:
        raise _refuse(f"{selected} is neither {work_item_id}, which {branch} is bound to, nor one of its "
                      f"remediation children", work_item_id=work_item_id, branch=branch, selected=selected)

    record = _observe_branch(ctx, key, record, head)
    override = work_item_id if items[work_item_id].get("phase") == MILESTONE_COMPLETE else None
    return Proceed(work_item_override=override, binding=record, action=action)


def _observe_branch(ctx: Context, key: str, record: dict, head: gitrepo.HeadState) -> dict:
    """Every later preflight for a bound, non-terminal item: no rewrite of
    the branch, the remote branch absent or an ancestor of the tip, then
    sync (fast-forward the remote branch when it exists and is behind)."""
    remote, trunk, branch = record["repository"]["remote"], record["trunk"], record["branch"]
    tip = head.commit
    refs = _remote_refs(ctx, remote, [trunk, branch])
    if refs[trunk] is None:
        raise _refuse(f"{remote} has no {trunk} branch", work_item_id=record["work_item_id"], branch=branch)
    remote_branch = refs[branch]
    observation: dict[str, Any] = {}
    if record["state"] != MERGED:
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
        if remote_branch is not None and remote_branch != tip:
            pending = dict(record.get("last_observation") or {}, push={"intent": tip, "outcome": None})
            record = _write(ctx, key, dict(record, last_observation=pending), "push_intent", commit=tip)
            gitrepo.push_branch(ctx.repo_root, remote, branch, runner=ctx.runner)
            remote_branch = gitrepo.ls_remote(ctx.repo_root, remote, [f"refs/heads/{branch}"],
                                              runner=ctx.runner).get(f"refs/heads/{branch}")
            if remote_branch != tip:
                raise _refuse(f"pushing {branch} did not leave {remote}/{branch} at {tip} "
                              f"(it is at {remote_branch})", work_item_id=record["work_item_id"], branch=branch)
            observation["push"] = {"intent": tip, "outcome": remote_branch}
    fresh = _ancestor(ctx, refs[trunk], tip)
    behind = gitrepo.ahead_behind(ctx.repo_root, tip, refs[trunk], runner=ctx.runner)[1]
    observation.update({"tip": tip, "remote_branch": remote_branch, "remote_trunk": refs[trunk],
                        "fresh": fresh, "behind": behind, "observed_at": ctx.clock()})
    return _write(ctx, key, dict(record, last_observation=observation))


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
    blocking = [r for r in records.values() if r["state"] not in TERMINAL_STATES]
    for record in blocking:
        if record["state"] != BRANCH_PLANNED:
            raise _trunk_block(ctx, record, head)
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
        return _refuse(f"{where} It was merged before acceptance.", work_item_id=work_item_id, branch=branch,
                       exits=list(gate.exits), state=state)
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
        return Proceed(action="trunk_start")
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
                        f"2.5.1 has no abandonment transition")
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
