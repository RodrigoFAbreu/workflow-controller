"""A decision from the Workflow's own ``next-action``
(``docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md``, Design C).

For a protocol target (a Workflow release admitted by capability,
``managed_repo.ManagedRepository.target_protocol``) the Controller does not
run ``evidence.decide``: it asks the Workflow what the next action is and
turns the answer into the existing :class:`~controller.decision.Decision`, so
``job.execute_step``, ``cli explain`` and the launcher keep one shape.

**The Workflow decides; the Controller never overrides (I2).** A worker is
launched only for a decision with disposition ``automatic``, an action id the
:data:`PROTOCOL_ACTIONS` table names, a worker that is not ``user_only`` and a
worker role the Controller knows. Every other answer is a gate: ``human_gate``
and ``external_gate`` as the Workflow states them, ``blocked`` with its
reason and remedy, and anything this release does not know (a disposition, an
action id, a worker role) as a ``blocked`` gate that says so (I3) -- never a
launch and never success.

**The task is rendered here, not read from the answer.** The action's
``invocation`` is display only (consumer obligation 4): the worker's command
is ``/<token> <work_item_id>`` from :data:`PROTOCOL_ACTIONS` and the answer's
``arguments``. An ``invocation`` that differs from that rendering is not
launched (``workflow_invocation_mismatch``): the Workflow's ``reconcile``
refuses a stored decision whose rendering differs from its catalogue, so a
worker launched under it could never be reconciled. ``plan.start`` carries no
work item; the Controller appends its own base commit to ``/milestone-plan``
exactly as ``decision.decide_no_work_item`` does, and that appended argument
is never a mismatch.

**The one lifecycle fact the Controller still reads** (I8): the committed-state
gate of 1.6.0, :func:`controller.evidence.uncommitted_implementation_state`,
reused unchanged and applied only at the two implementation phases, only to a
decision that would launch. Nothing else in this module reads a lifecycle
file; ``tests/test_protocol_decision.py`` pins that.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any, Mapping

from controller import evidence, protocol, target_state
from controller.decision import (
    NO_PHASE,
    Action,
    Decision,
    HumanGate,
    ProtocolInfo,
    explain_gate_command,
)
from controller.errors import WorkflowProtocolFailedError, WorkflowProtocolRefusedError

#: The decision's dispositions this release knows. ``validation`` is one the
#: schema lists but that never names an action to run: it is blocked like an
#: unknown value (plan C.2).
DISPOSITIONS = frozenset({"automatic", "human_gate", "external_gate", "blocked", "complete"})

#: The worker roles an ``automatic`` action may carry. ``user`` and
#: ``external`` are gates' roles; a role outside this set (a later minor adds
#: one) is blocked, never launched.
AUTOMATIC_WORKER_ROLES = frozenset({"planner", "implementer", "self_reviewer", "independent_reviewer", "applier"})

#: The phases at which the committed-state gate applies (plan C.5): 1.6.0's
#: scope, ``evidence.MILESTONE_IMPLEMENT_PHASES``.
COMMITTED_STATE_PHASES = evidence.MILESTONE_IMPLEMENT_PHASES

#: Reason codes of the gates this module itself produces.
UNKNOWN_DISPOSITION = "workflow_unknown_disposition"
UNKNOWN_ACTION = "workflow_unknown_action"
UNKNOWN_WORKER_ROLE = "workflow_unknown_worker_role"
USER_ONLY_ACTION = "workflow_user_only_action"
INVOCATION_MISMATCH = "workflow_invocation_mismatch"
UNCOMMITTED_STATE = "uncommitted_implementation_state"


@dataclasses.dataclass(frozen=True)
class ProtocolAction:
    """One automatic action id: the command it runs (``/<command>``) and the
    :data:`~controller.routing.ROLES` member that routes its worker."""

    command: str
    route: str


#: The twelve automatic action ids (plan C.3). The route key is assigned
#: here, not derived from ``routing.role_for``'s ``registry_complete`` rule:
#: the protocol tells the two implementation actions apart by id.
PROTOCOL_ACTIONS: Mapping[str, ProtocolAction] = MappingProxyType({
    "plan.start": ProtocolAction("milestone-plan", "milestone-plan"),
    "plan.author": ProtocolAction("milestone-plan", "milestone-plan"),
    "plan.apply_review": ProtocolAction("apply-plan-review", "apply-plan-review"),
    "plan.review.local": ProtocolAction("review-plan", "review-plan"),
    "plan.record_external": ProtocolAction("record-manual-plan-review", "record-manual-plan-review"),
    "implementation.checkpoint": ProtocolAction("milestone-implement", "milestone-implement"),
    "implementation.self_review": ProtocolAction("milestone-implement", "milestone-implement-self-review"),
    "implementation.review.local": ProtocolAction("review-implementation", "review-implementation"),
    "implementation.record_external": ProtocolAction(
        "record-manual-implementation-review", "record-manual-implementation-review"),
    "implementation.apply_review": ProtocolAction("apply-implementation-review", "apply-implementation-review"),
    "functional.prepare": ProtocolAction("prepare-functional-review", "prepare-functional-review"),
    "functional.apply_findings": ProtocolAction("apply-functional-review", "apply-functional-review"),
})

#: The one action that has no work item (plan C.4).
PLAN_START = "plan.start"


def bare_invocation(action_id: str, work_item_id: str | None) -> str:
    """The rendering the Controller compares the answer's ``invocation``
    with: ``/<command>`` and the work item, without the Controller's own
    appended base commit."""
    command = f"/{PROTOCOL_ACTIONS[action_id].command}"
    return command if work_item_id is None else f"{command} {work_item_id}"


def rendered_command(action_id: str, work_item_id: str | None, *, base: str | None = None) -> str:
    """The worker's task: :func:`bare_invocation`, plus ``base`` for
    ``plan.start`` (a Controller choice about a branch, not a lifecycle
    fact)."""
    command = bare_invocation(action_id, work_item_id)
    return f"{command} {base}" if action_id == PLAN_START and base is not None else command


def decide(managed_repo: Any, work_item: Any, *, base: str | None = None, timeout: float | None = None) -> Decision:
    """The :class:`Decision` for ``work_item`` (or, for
    :data:`target_state.NoWorkItemYet`, for the repository that has none)
    from the Workflow's ``next-action``. ``base`` is the trunk tip of
    ``/milestone-plan <base>`` (plan C.4)."""
    no_item = work_item is target_state.NoWorkItemYet
    work_item_id = None if no_item else work_item.work_item_id
    answer = protocol.next_action(managed_repo.root, work_item_id, timeout=timeout)
    return from_answer(managed_repo, work_item, answer, base=base)


def from_answer(managed_repo: Any, work_item: Any, answer: protocol.Decision, *, base: str | None = None) -> Decision:
    """:func:`decide` for an answer already received (a validated
    ``next-action`` result)."""
    no_item = work_item is target_state.NoWorkItemYet
    work_item_id = None if no_item else work_item.work_item_id
    basis = answer.basis
    if work_item_id is not None and (basis is None or basis.work_item_id != work_item_id):
        # An answer for another item (plan C.6): never acted on.
        raise WorkflowProtocolFailedError(
            f"next-action answered for {None if basis is None else basis.work_item_id!r}, "
            f"but {work_item_id!r} was asked",
            evidence={"reason": "protocol_decision_invalid", "asked": work_item_id,
                      "answered": None if basis is None else basis.work_item_id, "row": answer.row})
    phase = NO_PHASE if basis is None else basis.phase
    context = _Context(managed_repo, work_item, work_item_id, phase, answer)

    disposition = answer.disposition
    if disposition == "automatic":
        return _automatic(context, base)
    if disposition in ("human_gate", "external_gate"):
        return context.gate(_gate_text(answer), evidence_label=disposition,
                            resume=_resume_invocation(answer, managed_repo.root, work_item_id), route=None)
    if disposition == "blocked":
        return context.gate(_blocked_text(answer), evidence_label="blocked",
                            resume=_resume_invocation(answer, managed_repo.root, work_item_id), route=None)
    if disposition == "complete":
        return Decision(
            observed_phase=phase, evidence=context.evidence("complete"), action=None, automatic=False,
            gate=None, declined=False, reason=answer.reason.text, protocol=context.info(None),
        )
    return context.blocked(
        UNKNOWN_DISPOSITION,
        f"the Workflow answered the disposition {disposition!r}, which this Controller release does not know "
        f"(row {answer.row}: {answer.reason.code}: {answer.reason.text}); nothing is launched")


# ---------------------------------------------------------------------------
# The automatic path.
# ---------------------------------------------------------------------------


def _automatic(context: "_Context", base: str | None) -> Decision:
    answer = context.answer
    action = answer.action
    if action is None:
        return context.blocked(UNKNOWN_ACTION, f"row {answer.row} is automatic but names no action; nothing is launched")
    if action.worker.user_only:
        return context.blocked(
            USER_ONLY_ACTION,
            f"row {answer.row} is automatic but its action {action.id!r} is user-only; the Controller never "
            "launches a user-only action")
    entry = PROTOCOL_ACTIONS.get(action.id)
    if entry is None:
        return context.blocked(
            UNKNOWN_ACTION,
            f"the Workflow's next action is {action.id!r}, which this Controller release has no command for "
            f"(row {answer.row}); a later Workflow minor may add actions, and one that becomes next stops here")
    if action.worker.role not in AUTOMATIC_WORKER_ROLES:
        return context.blocked(
            UNKNOWN_WORKER_ROLE,
            f"the action {action.id!r} names the worker role {action.worker.role!r}, which this Controller "
            f"release does not know how to launch (row {answer.row}); nothing is launched")
    # `plan.start` has no work item; every other action must name the one asked for.
    expected_item = None if action.id == PLAN_START else context.work_item_id
    argument = action.arguments.get("work_item_id")
    if action.id != PLAN_START and (expected_item is None or argument != expected_item):
        raise WorkflowProtocolFailedError(
            f"the action {action.id!r} names the work item {argument!r}, but {expected_item!r} was asked",
            evidence={"reason": "protocol_decision_invalid", "action": action.id, "argument": argument,
                      "asked": expected_item, "row": answer.row})
    rendering = bare_invocation(action.id, expected_item)
    if action.invocation != rendering:
        return context.blocked(
            INVOCATION_MISMATCH,
            f"the Workflow's invocation text {action.invocation!r} for {action.id!r} differs from the command "
            f"this Controller renders, {rendering!r}, under Workflow release {context.release}; the Workflow's "
            "reconcile would refuse the decision, so no worker is launched. This is a Workflow release defect "
            "to report, not a command to run")
    if context.phase in COMMITTED_STATE_PHASES:
        facts = evidence.uncommitted_implementation_state(context.managed_repo.root, context.work_item)
        if facts:
            return context.gate(
                f"the working tree's {_STATE_PATH} records state that HEAD does not durably record: "
                + "; ".join(facts) + ". /milestone-implement commits a checkpoint completion together with "
                "its checkpoint and the SELF_REVIEWING_IMPLEMENTATION transition in a state-only commit, so "
                "this is an interrupted or defective run, and no worker is launched on top of it. A human "
                "reconciles the working tree with HEAD first",
                evidence_label=UNCOMMITTED_STATE, extra_evidence=facts,
                resume=explain_gate_command(context.managed_repo.root, context.work_item_id or ""),
                route=None, artifact_path=str(context.managed_repo.root / _STATE_PATH))
    command = rendered_command(action.id, expected_item, base=base)
    return Decision(
        observed_phase=context.phase, evidence=context.evidence("automatic", action.id), action=Action(command=command),
        automatic=True, gate=None, declined=False, reason=answer.reason.text, protocol=context.info(entry.route),
    )


#: The state file's path in a message; the Controller names it, never reads it
#: here (the committed-state call reads it).
_STATE_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"


# ---------------------------------------------------------------------------
# Gate text.
# ---------------------------------------------------------------------------


def _alternatives_text(answer: protocol.Decision) -> str:
    shown = [f"{alt.id} ({alt.invocation})" if alt.invocation else alt.id for alt in answer.alternatives]
    return "; alternatives: " + ", ".join(shown) if shown else ""


def _gate_text(answer: protocol.Decision) -> str:
    action = answer.action
    action_text = ""
    if action is not None:
        action_text = f"; action: {action.id}" + (f" ({action.invocation})" if action.invocation else "")
    satisfied = f"; satisfied by: {answer.satisfied_by}" if answer.satisfied_by else ""
    remedy = f"; remedy: {answer.reason.remedy}" if answer.reason.remedy else ""
    return f"{answer.reason.text}{remedy}{action_text}{satisfied}{_alternatives_text(answer)}"


def _blocked_text(answer: protocol.Decision) -> str:
    remedy = f"; remedy: {answer.reason.remedy}" if answer.reason.remedy else ""
    return f"{answer.reason.code}: {answer.reason.text}{remedy}{_alternatives_text(answer)}"


def _resume_invocation(answer: protocol.Decision, root: Any, work_item_id: str | None) -> str:
    """The gate's ``safe_resume_command``: the action's own invocation, else
    the first alternative's, else ``explain`` -- display only, never run."""
    if answer.action is not None and answer.action.invocation:
        return answer.action.invocation
    for alternative in answer.alternatives:
        if alternative.invocation:
            return alternative.invocation
    return explain_gate_command(root, work_item_id or "")


# ---------------------------------------------------------------------------
# One answer's context.
# ---------------------------------------------------------------------------


class _Context:
    def __init__(self, managed_repo: Any, work_item: Any, work_item_id: str | None, phase: Any,
                 answer: protocol.Decision) -> None:
        self.managed_repo = managed_repo
        self.work_item = work_item
        self.work_item_id = work_item_id
        self.phase = phase
        self.answer = answer
        described = managed_repo.target_protocol or {}
        self.release = described.get("release")
        self.protocol_version = described.get("version")

    def evidence(self, label: str, action_id: str | None = None) -> tuple[str, ...]:
        lines = [f"protocol row {self.answer.row}: {self.answer.reason.code} ({label})"]
        if action_id is not None:
            lines.append(f"protocol action: {action_id}")
        return tuple(lines)

    def info(self, route: str | None) -> ProtocolInfo:
        answer = self.answer
        action = answer.action
        basis = answer.basis
        return ProtocolInfo(
            document=answer.raw, row=answer.row, disposition=answer.disposition,
            action_id=None if action is None else action.id,
            arguments={} if action is None else dict(action.arguments),
            state_identity=None if basis is None else basis.state_identity,
            phase=None if basis is None else basis.phase, route=route,
            alternatives=tuple(alt.invocation for alt in answer.alternatives if alt.invocation),
            release=self.release, protocol_version=self.protocol_version,
            script_digests=getattr(self.managed_repo, "script_digests", None),
        )

    def gate(self, what: str, *, evidence_label: str, resume: str, route: str | None,
             extra_evidence: tuple[str, ...] = (), artifact_path: str | None = None) -> Decision:
        phase_text = self.phase if isinstance(self.phase, str) else "NO_PHASE"
        gate = HumanGate(
            repository=str(self.managed_repo.root), work_item_id=self.work_item_id or "", phase=phase_text,
            what_is_required=what, artifact_path=artifact_path, safe_resume_command=resume)
        return Decision(
            observed_phase=self.phase, evidence=self.evidence(evidence_label) + extra_evidence, action=None,
            automatic=False, gate=gate, declined=False,
            reason=f"{phase_text}: {self.answer.reason.code}: {self.answer.reason.text}",
            protocol=self.info(route))

    def blocked(self, code: str, text: str) -> Decision:
        return self.gate(f"{code}: {text}", evidence_label=code,
                         resume=explain_gate_command(self.managed_repo.root, self.work_item_id or ""), route=None)


# ---------------------------------------------------------------------------
# The currency check and the loop guard (plan D, CP4).
# ---------------------------------------------------------------------------

#: Reason codes of the gates the stale check and the loop guard produce.
DECISION_UNSTABLE = "decision_unstable"
NO_PROGRESS_REPEATED = "no_progress_repeated"

#: How many decisions one step may discard as stale before it stops (plan D.2.1).
MAX_DECISIONS_PER_STEP = 3

#: ``reconciliation_evidence.code`` of the terminal record a decision found
#: stale immediately before the spawn leaves (plan D.2.2).
DECISION_STALE_AT_LAUNCH = "decision_stale_at_launch"


@dataclasses.dataclass(frozen=True)
class Currency:
    """The result of one currency check. ``current`` is ``False`` when the
    decision is no longer the Workflow's answer: ``stale_decision`` (the
    state's identity moved), ``answer_changed`` (the identity is unchanged but
    the row, disposition, action id or arguments differ) or
    ``work_items_changed`` (a ``plan.start`` decision whose
    ``snapshot.work_item_ids`` moved). ``answer`` is the second answer when
    one came back."""

    current: bool
    reason: str | None
    answer: protocol.Decision | None

    def evidence(self) -> dict:
        answer = self.answer
        return {
            "reason": self.reason,
            "row": None if answer is None else answer.row,
            "action_id": None if answer is None or answer.action is None else answer.action.id,
            "state_identity": None if answer is None or answer.basis is None else answer.basis.state_identity,
        }


def _differs(info: ProtocolInfo, answer: protocol.Decision) -> bool:
    """Whether ``answer`` disagrees with the stored decision on row,
    disposition, action id or arguments (plan D.2)."""
    action = answer.action
    return (answer.row != info.row or answer.disposition != info.disposition
            or (None if action is None else action.id) != info.action_id
            or (None if action is None else dict(action.arguments)) != (None if info.action_id is None
                                                                        else dict(info.arguments)))


def _snapshot_ids(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(snapshot.get("work_item_ids") or ())


def check_currency(managed_repo: Any, decision: Decision, *, timeout: float | None = None) -> Currency:
    """The currency check of plan D.2, against the decision as received.

    A decision with a state identity: ``next-action --work-item <id>
    --expect-state-identity <identity>`` must not refuse ``stale_decision``
    and must answer what the decision says. A decision without one (the
    ``plan.start`` rows): ``next-action`` with no work item must answer the
    same row, disposition, action id and ``snapshot.work_item_ids``."""
    info = decision.protocol
    if info is None:
        raise ValueError("check_currency needs a protocol decision")
    basis = info.document.get("basis")
    if info.state_identity is None or not isinstance(basis, Mapping):
        answer = protocol.next_action(managed_repo.root, None, timeout=timeout)
        if _snapshot_ids(answer.snapshot) != _snapshot_ids(info.document.get("snapshot") or {}):
            return Currency(False, "work_items_changed", answer)
        if _differs(info, answer):
            return Currency(False, "answer_changed", answer)
        return Currency(True, None, answer)
    try:
        answer = protocol.next_action(managed_repo.root, basis["work_item_id"],
                                      expect_state_identity=info.state_identity, timeout=timeout)
    except WorkflowProtocolRefusedError as exc:
        if exc.refusal["code"] == "stale_decision":
            return Currency(False, "stale_decision", None)
        raise
    if _differs(info, answer):
        return Currency(False, "answer_changed", answer)
    return Currency(True, None, answer)


def gate_for(managed_repo: Any, work_item: Any, decision: Decision, code: str, text: str) -> Decision:
    """A blocked gate over ``decision``'s own answer: ``code`` and ``text``
    are the Controller's, the rest (row, route-less info) the Workflow's."""
    info = decision.protocol
    answer = protocol.parse_decision(dict(info.document))
    no_item = work_item is target_state.NoWorkItemYet
    context = _Context(managed_repo, work_item, None if no_item else work_item.work_item_id,
                       decision.observed_phase, answer)
    return context.blocked(code, text)
