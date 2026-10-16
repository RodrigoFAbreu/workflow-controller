"""The review round of a stage and the round cap's rule
(``workflow-controller-review-convergence`` D1 and D2).

Pure: it reads the mappings it is given (a ``next-action`` snapshot and the
stored job records) and nothing else, so the cap gate (``controller.job``)
and ``telemetry`` share one function and cannot drift.

A round is the Workflow's integer ``snapshot.review_round[<stage>]`` when it
has one (protocol 1.4, Workflow 2.12.0 and later). Otherwise it is the
Controller's own count over persisted revision evidence, never a reconcile
class: the (``phase``, revision) pairs of the stored ``pre_state`` of this
repository's and work item's job records read oldest first (a
``GATE_BLOCKED`` record excluded), then the snapshot as the last
observation. ``implementation`` is 1 plus the number of times
``implementation_revision`` rose. ``plan`` is the number of distinct integer
``plan_revision`` values observed at a plan-review ready phase, at least 1;
the Workflow counts published contents, which can exceed ``plan_revision``,
so that count is a lower bound. ``None`` is never read as 0.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: ``review.round_cap``'s default (D4).
DEFAULT_REVIEW_ROUND_CAP = 4

STAGE_PLAN = "plan"
STAGE_IMPLEMENTATION = "implementation"
STAGE_FUNCTIONAL = "functional"
STAGE_NONE = "none"

SOURCE_WORKFLOW = "workflow"
SOURCE_JOBS = "jobs"

#: The stage each protocol action id belongs to, for the cap and for
#: ``telemetry``. ``plan.review.external`` and ``implementation.review.external``
#: are the real ids a Controller review job stores.
STAGE_OF_ACTION: Mapping[str, str] = {
    "plan.start": STAGE_PLAN,
    "plan.author": STAGE_PLAN,
    "plan.apply_review": STAGE_PLAN,
    "plan.review.local": STAGE_PLAN,
    "plan.review.external": STAGE_PLAN,
    "plan.record_external": STAGE_PLAN,
    "plan.satisfy": STAGE_PLAN,
    "implementation.checkpoint": STAGE_IMPLEMENTATION,
    "implementation.self_review": STAGE_IMPLEMENTATION,
    "implementation.review.local": STAGE_IMPLEMENTATION,
    "implementation.review.external": STAGE_IMPLEMENTATION,
    "implementation.record_external": STAGE_IMPLEMENTATION,
    "implementation.apply_review": STAGE_IMPLEMENTATION,
    "implementation.satisfy": STAGE_IMPLEMENTATION,
    "functional.prepare": STAGE_FUNCTIONAL,
    "functional.apply_findings": STAGE_FUNCTIONAL,
}

#: The actions the cap guards (D2): an apply opens the next round, a local
#: review reads the round it opened.
APPLY_ACTIONS = frozenset({"plan.apply_review", "implementation.apply_review"})
LOCAL_REVIEW_ACTIONS = frozenset({"plan.review.local", "implementation.review.local"})
CAPPED_ACTIONS = APPLY_ACTIONS | LOCAL_REVIEW_ACTIONS

#: Plan-review ready phases (``workflow_state.PLAN_REVIEW_READY_PHASES``): a
#: revision seen at one was published and bound.
PLAN_REVIEW_READY_PHASES = frozenset({
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
})

_GATE_BLOCKED = "GATE_BLOCKED"


def stage_of(action_id: str | None) -> str:
    """The stage of an action id; ``none`` for no id or an unlisted one."""
    return STAGE_OF_ACTION.get(action_id, STAGE_NONE) if isinstance(action_id, str) else STAGE_NONE


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def record_key(record: Mapping) -> tuple:
    """The oldest-first order D1 reads job records in."""
    block = record.get("protocol")
    decided = block.get("decided_ns") if isinstance(block, Mapping) else None
    return (str(record.get("created_at")), decided if _is_int(decided) else 0, str(record.get("job_id")))


def _observations(snapshot: Mapping | None, records: Iterable[Any], target_repo: str,
                  work_item_id: str | None) -> list[Mapping]:
    """The ``pre_state`` of each counted record, oldest first, then ``snapshot``."""
    mine = []
    for record in records:
        if not isinstance(record, Mapping) or record.get("target_repo") != target_repo \
                or record.get("work_item_id") != work_item_id or record.get("status") == _GATE_BLOCKED:
            continue
        pre_state = record.get("pre_state")
        if isinstance(pre_state, Mapping):
            mine.append((record_key(record), pre_state))
    mine.sort(key=lambda pair: pair[0])
    observed = [pre_state for _key, pre_state in mine]
    if isinstance(snapshot, Mapping):
        observed.append(snapshot)
    return observed


def stored_round(snapshot: Mapping | None, stage: str) -> int | None:
    """The Workflow's integer ``review_round[stage]``, else ``None``."""
    rounds = snapshot.get("review_round") if isinstance(snapshot, Mapping) else None
    value = rounds.get(stage) if isinstance(rounds, Mapping) else None
    return value if _is_int(value) and value >= 1 else None


def current_round(snapshot: Mapping | None, stage: str, records: Iterable[Any], *,
                  target_repo: str, work_item_id: str | None) -> tuple[int, str]:
    """``(round, source)`` of ``stage`` (D1). ``source`` is ``workflow`` for
    the Workflow's own integer, ``jobs`` for the Controller's count, which
    is never less than 1."""
    given = stored_round(snapshot, stage)
    if given is not None:
        return given, SOURCE_WORKFLOW
    observed = _observations(snapshot, records, target_repo, work_item_id)
    if stage == STAGE_PLAN:
        seen = {obs.get("plan_revision") for obs in observed
                if obs.get("phase") in PLAN_REVIEW_READY_PHASES and _is_int(obs.get("plan_revision"))}
        return max(1, len(seen)), SOURCE_JOBS
    rises = 0
    previous = None
    for obs in observed:
        revision = obs.get("implementation_revision")
        if not _is_int(revision):
            continue
        if previous is not None and revision > previous:
            rises += 1
        previous = revision
    return 1 + rises, SOURCE_JOBS


def cap_decision(action_id: str | None, round_number: int, cap: int) -> bool:
    """Whether D2 stops ``action_id`` at ``round_number`` under ``cap``: an
    apply at or above the cap, a local review above it, nothing else."""
    if action_id in APPLY_ACTIONS:
        return round_number >= cap
    if action_id in LOCAL_REVIEW_ACTIONS:
        return round_number > cap
    return False
