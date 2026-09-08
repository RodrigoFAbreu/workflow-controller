"""Next-action decision engine, part 1 (capability 3, ``docs/ACTIVE_MILESTONE.md``).

``decide(managed_repo, snapshot, work_item) -> Decision`` is the whole
entry point: a pure function from what CP3's reader already established
(a target repository's own root, plus one ``WorkItemView``) to an
explainable :class:`Decision` -- never a launcher, never a writer.
``controller/decision.py`` sits to the left of ``worker`` in the
dependency graph CP1 asserts (``tests/test_package_structure.py``'s
``DEPENDENCY_ORDER``), so nothing in this module can launch a subprocess
or touch ``WORKFLOW_STATE.json`` -- there is no import path back to
either capability.

**This module deliberately imports nothing from ``controller.managed_repo``
or ``controller.target_state``.** Both of those modules sit *later* in the
dependency order than this one (``errors, runtime, identity, decision,
managed_repo, target_state, ...``), so an import in the other direction
would be the cycle ``tests/test_package_structure.py``'s
``DependencyGraphTest`` exists to catch. ``managed_repo``, ``snapshot`` and
``work_item`` are therefore accepted as plain, duck-typed objects: callers
pass real ``ManagedRepository``/``WorkflowSnapshot``/``WorkItemView``
instances, and this module only ever reads the handful of attributes named
in each function's own docstring (``managed_repo.root``, and off
``work_item``: ``work_item_id``, ``phase``, ``governing_workflow_version``).

**Split with CP4B** (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP4 /
CP4B -- Next-action decision engine and human-gate classification"): this
checkpoint owns the phase -> action mapping over all seventeen known
phases, the user-only denylist and command-file partition (both derived by
scanning the target repository's own ``.claude/commands/`` -- a structural
fact about the installed Workflow version, not a per-work-item evidence
read, so reading it here does not cross the "reads nothing outside
WorkItemView" line), the phase-set equality assertion, and the
``Decision``/``Action``/``HumanGate`` shapes. It does **not** read any
``.ai-review/`` bundle, feedback or manifest file scoped to the work
item's current review round -- that is CP4B's ``controller/evidence.py``,
which extends :func:`decide` in place once it exists.

Three phases genuinely need that evidence to resolve their "ordinary case
vs. blocked/withdrawn/superseded" sub-cases correctly
(``AWAITING_LOCAL_PLAN_REVIEW``, ``AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW``,
and ``AWAITING_EXTERNAL_PLAN_REVIEW`` on a ``"1"``-governed item). Until
CP4B lands, :func:`decide` resolves each to the *ordinary-case* automatic
row named by the plan's own six-triple table -- never a gate, and never a
guess dressed up as a refusal -- because nothing in this checkpoint's own
dependency graph *acts* on a ``Decision`` yet: ``job``/``cli`` (the modules
that would launch a worker from one) both depend on CP4B's ``evidence``,
never on this module directly, so an optimistic default here has no
executable consequence before CP4B replaces it with the real
evidence-reading disambiguation. Every one of these three rows says so
explicitly in its own ``reason`` string, so a report or a test can never
mistake the placeholder for the finished behaviour.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from controller.errors import NoSupportedActionError

#: The closed set of all seventeen phases frozen Workflow v2.3.1 persists
#: (a literal copy of ``controller.target_state.KNOWN_PHASES`` -- this
#: module cannot import ``target_state`` per the dependency graph above, so
#: the copy is kept honest by a two-directional equality test against both
#: the real ``target_state.KNOWN_PHASES`` and a hand-copied set of the
#: seventeen names, exactly as CP3's own test does for its copy).
KNOWN_PHASES: frozenset[str] = frozenset({
    "PLANNING",
    "SELF_REVIEWING_PLAN",
    "AWAITING_EXTERNAL_PLAN_REVIEW",
    "REVISING_PLAN",
    "IMPLEMENTING",
    "SELF_REVIEWING_IMPLEMENTATION",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK",
    "AWAITING_FUNCTIONAL_REVIEW",
    "FIXING_FUNCTIONAL_FINDINGS",
    "AWAITING_USER_ACCEPTANCE",
    "MILESTONE_COMPLETE",
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
    "AWAITING_TECHNICAL_APPROVAL",
    "LEGACY_READY",
})

#: The four vocabulary phases frozen Workflow v2.3.1's ``KNOWN_PHASES``
#: carries but that no writer in ``scripts/workflow_state.py`` ever
#: persists. Recognising one as *known* is not the same as *acting* on it:
#: every one of them maps to :class:`~controller.errors.NoSupportedActionError`.
VOCABULARY_PHASES: frozenset[str] = frozenset({
    "SELF_REVIEWING_PLAN",
    "AWAITING_TECHNICAL_APPROVAL",
    "FIXING_FUNCTIONAL_FINDINGS",
    "AWAITING_USER_ACCEPTANCE",
})

#: The five report-only phases (revision 10's narrowing): ``decide()``
#: returns ``automatic=False`` at every one of them, never launches a
#: worker for any of them, and this set is what the two-shape assertion
#: below is closed over.
REPORT_ONLY_PHASES: frozenset[str] = frozenset({
    "IMPLEMENTING",
    "SELF_REVIEWING_IMPLEMENTATION",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK",
    "AWAITING_FUNCTIONAL_REVIEW",
})

#: The two of ``REPORT_ONLY_PHASES`` that are automation-safe -- the
#: Controller knows the next action and it is model-invocable, this
#: generation simply does not run it (``declined=True``, ``action``
#: populated, ``gate=None``) -- as opposed to the three that are genuine
#: human gates (``gate`` populated, ``action=None``).
DECLINED_PHASES: frozenset[str] = frozenset({"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"})

#: The three of ``REPORT_ONLY_PHASES`` that are genuine human gates.
GATE_REPORT_PHASES: frozenset[str] = REPORT_ONLY_PHASES - DECLINED_PHASES


# ---------------------------------------------------------------------------
# The command-file partition and the user-only denylist.
# ---------------------------------------------------------------------------

#: Where the target repository's own copy of frozen Workflow v2.3.1's
#: command files lives, relative to ``managed_repo.root``.
COMMANDS_REL_DIR = ".claude/commands"

#: The qualified-literal recogniser (revision 46, local round 45's
#: ``OPUS-R45-B1``): a command file "carries a user-confirmation guard"
#: when its text contains the literal dotted reference
#: ``workflow_state.validate_`` immediately followed by one or more of
#: ``[A-Za-z_]`` ending in ``confirmation`` -- a call in the file's own
#: instructions, not a citation of another command's guard. The qualifying
#: ``workflow_state.`` prefix is exactly what a command's own procedural
#: step supplies when it states the call it makes, and exactly what a bare
#: prose citation (e.g. ```` `validate_user_confirmation` ````, as
#: ``recover-implementation-provenance.md:79`` cites ``/approve-review``'s
#: own guard) omits -- so this one literal is the whole rule; there is no
#: second, semantic "is this a citation" test.
_USER_CONFIRMATION_GUARD_PATTERN = re.compile(r"workflow_state\.validate_[A-Za-z_]+confirmation")

#: The four command files ``decide()`` may return as an :class:`Action`.
#: Six is the *row* count of the automatic mapping below -- two of these
#: four files are selected from two rows each; this partition is over
#: *files*, not rows.
SELECTED_COMMANDS: frozenset[str] = frozenset({
    "milestone-plan",
    "review-plan",
    "record-manual-plan-review",
    "apply-plan-review",
})

#: The eight command files ``decide()`` deliberately never selects, each
#: with the one-line reason the plan gives for excluding it.
DELIBERATELY_NOT_SELECTED_COMMANDS: dict[str, str] = {
    "review-implementation": (
        "its own write would close a review loop with no external reviewer in it"
    ),
    "review-functional": (
        "its own write would close a review loop with no external reviewer in it"
    ),
    "milestone-implement": "revision 10's report-only set",
    "apply-implementation-review": "revision 10's report-only set",
    "apply-functional-review": "revision 10's report-only set",
    "prepare-functional-review": "revision 10's report-only set",
    "bootstrap-workflow-v2": "outside the milestone lifecycle entirely",
    "prepare-review": "outside the milestone lifecycle entirely",
}

#: Category labels :func:`classify_command_files` returns.
CATEGORY_SELECTED = "selected"
CATEGORY_DELIBERATELY_NOT_SELECTED = "deliberately_not_selected"
CATEGORY_USER_ONLY = "user_only"


def carries_user_confirmation_guard(text: str) -> bool:
    """``True`` iff ``text`` contains the qualified literal
    ``workflow_state.validate_...confirmation`` -- matched anywhere in the
    file's text, with or without a following argument list. A bare prose
    citation of another command's guard (the ``` `validate_user_confirmation` ```
    form, with no ``workflow_state.`` prefix) never matches."""
    return _USER_CONFIRMATION_GUARD_PATTERN.search(text) is not None


def _iter_command_files(commands_dir: Path) -> list[Path]:
    return sorted(commands_dir.glob("*.md"))


def classify_command_files(commands_dir: Path) -> dict[str, str]:
    """Enumerate every ``*.md`` file directly under ``commands_dir`` --
    **the external artifact, not the plan's own tables** -- and classify
    each one into exactly one of :data:`CATEGORY_SELECTED`,
    :data:`CATEGORY_DELIBERATELY_NOT_SELECTED` or
    :data:`CATEGORY_USER_ONLY`.

    The guard test runs first and wins unconditionally: a file that
    carries the qualified user-confirmation literal is ``user_only``
    regardless of whether its name also appears in
    :data:`SELECTED_COMMANDS` or :data:`DELIBERATELY_NOT_SELECTED_COMMANDS`
    -- the property is what is authoritative, never a name list a future
    file could simply not appear on. A file whose name is in neither
    static set and that does not carry the guard is a command this
    Controller generation cannot classify at all, and that is a fail-closed
    :class:`~controller.errors.NoSupportedActionError`, never a silently
    skipped file -- total and disjoint by construction, not by a
    post-hoc assertion.
    """
    result: dict[str, str] = {}
    for path in _iter_command_files(commands_dir):
        stem = path.stem
        try:
            text = path.read_text()
        except OSError as exc:
            raise NoSupportedActionError(
                f"{path} could not be read to classify it: {exc}",
                evidence={"command_file": str(path), "error": str(exc)},
            ) from exc
        if carries_user_confirmation_guard(text):
            result[stem] = CATEGORY_USER_ONLY
        elif stem in SELECTED_COMMANDS:
            result[stem] = CATEGORY_SELECTED
        elif stem in DELIBERATELY_NOT_SELECTED_COMMANDS:
            result[stem] = CATEGORY_DELIBERATELY_NOT_SELECTED
        else:
            raise NoSupportedActionError(
                f"{path} is a command file this Controller generation does not "
                "recognise as selected, deliberately-not-selected or user-only -- "
                "a future Workflow release adding a command must be a Controller "
                "release too",
                evidence={"command_file": str(path), "stem": stem},
            )
    return result


def derive_user_only_commands(commands_dir: Path) -> frozenset[str]:
    """The **three**-command user-only denylist, derived fresh from
    ``commands_dir`` by the property above -- never a copied list. This is
    ``disable-model-invocation: true``'s replacement: that front-matter
    flag is corroborating evidence, not the definition, and the two come
    apart on ``recover-implementation-provenance.md`` (front matter: no;
    property: yes)."""
    classification = classify_command_files(commands_dir)
    return frozenset(
        stem for stem, category in classification.items() if category == CATEGORY_USER_ONLY
    )


# ---------------------------------------------------------------------------
# The Decision / Action / HumanGate shapes.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Action:
    """The one Workflow command ``decide()`` names, either as something
    this generation will launch a worker to run (``Decision.automatic``)
    or as something it merely reports (``Decision.declined``).

    ``command`` is the literal invocation text, e.g.
    ``"/milestone-plan workflow-controller-generation-1"`` -- ready to hand
    to CP5's worker launcher unchanged."""

    command: str


@dataclasses.dataclass(frozen=True)
class HumanGate:
    """What a human needs to know to unblock a gated phase.

    ``repository`` and ``work_item_id`` name where and what;
    ``what_is_required`` states the human action in plain language;
    ``artifact_path`` names the bundle/feedback location relevant to that
    action, when CP4 alone can resolve one -- ``None`` where the real
    resolution needs a filesystem read CP4B's ``evidence.py`` performs
    (the plan-stage/implementation-stage ``<bundle_dir>``/``<feedback_dir>``
    rule); ``safe_resume_command`` names the exact next command to run,
    which is a Workflow slash command when a human must act at that layer
    (e.g. ``"/approve-review plan <id>"``) or a regeneration/tooling
    invocation when that is what closes the gap.
    """

    repository: str
    work_item_id: str
    phase: str
    what_is_required: str
    artifact_path: str | None
    safe_resume_command: str


@dataclasses.dataclass(frozen=True)
class Decision:
    """The whole, explainable output of :func:`decide`. Every field is
    populated on every path -- an explanation is not an optional extra, it
    is the return value.

    +--------------------+----------+-----------+--------------+-----------+
    | Decision kind       | action   | automatic | gate         | declined  |
    +--------------------+----------+-----------+--------------+-----------+
    | automatic action    | command  | True      | None         | False     |
    | human gate          | None     | False     | HumanGate    | False     |
    | declined            | command  | False     | None         | True      |
    | LEGACY_READY         | None     | False     | None         | False     |
    | MILESTONE_COMPLETE   | None     | False     | None         | False     |
    +--------------------+----------+-----------+--------------+-----------+

    ``declined`` is a stored field, never a derivation from
    ``action is not None`` -- the two are distinguishable that way today
    (``LEGACY_READY``/``MILESTONE_COMPLETE`` also leave ``action`` unset),
    but a derivation would make every consumer recompute it, which is
    exactly the drift the single-source rule this plan states elsewhere
    forbids for the gate fields.
    """

    observed_phase: str
    evidence: tuple[str, ...]
    action: Action | None
    automatic: bool
    gate: HumanGate | None
    declined: bool
    reason: str


# ---------------------------------------------------------------------------
# decide()
# ---------------------------------------------------------------------------


def _work_item_id(work_item: Any) -> str:
    return work_item.work_item_id


def _decide_planning(managed_repo: Any, work_item: Any) -> Decision:
    wid = _work_item_id(work_item)
    return Decision(
        observed_phase="PLANNING",
        evidence=(),
        action=Action(command=f"/milestone-plan {wid}"),
        automatic=True,
        gate=None,
        declined=False,
        reason="PLANNING has one legal next step regardless of governing_workflow_version: "
               "publish a plan revision",
    )


def _decide_revising_plan(managed_repo: Any, work_item: Any) -> Decision:
    wid = _work_item_id(work_item)
    return Decision(
        observed_phase="REVISING_PLAN",
        evidence=(),
        action=Action(command=f"/apply-plan-review {wid}"),
        automatic=True,
        gate=None,
        declined=False,
        reason="REVISING_PLAN has one legal next step: apply the recorded plan-review "
               "feedback",
    )


def _ordinary_case_placeholder(
    *, phase: str, command: str, wid: str,
) -> Decision:
    """The interim, ordinary-case default for the three phases whose real
    automatic/gate sub-case split needs an evidence read CP4B performs
    (``AWAITING_LOCAL_PLAN_REVIEW``, ``AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW``,
    ``AWAITING_EXTERNAL_PLAN_REVIEW`` on a ``"1"``-governed item). Nothing
    in this checkpoint's own dependency graph acts on a ``Decision`` yet,
    so this placeholder has no executable consequence before CP4B replaces
    it -- see this module's own docstring."""
    return Decision(
        observed_phase=phase,
        evidence=(),
        action=Action(command=command),
        automatic=True,
        gate=None,
        declined=False,
        reason=(
            f"{phase} ordinary-case default (CP4, no evidence read): {command} is the "
            "phase's own automatic row. CP4B adds the real evidence-reading "
            "disambiguation (a current-round BLOCK verdict, a withdrawn bundle, or an "
            "already-recorded plan approval each override this to a human gate)"
        ),
    )


def _decide_awaiting_local_plan_review(managed_repo: Any, work_item: Any) -> Decision:
    wid = _work_item_id(work_item)
    return _ordinary_case_placeholder(
        phase="AWAITING_LOCAL_PLAN_REVIEW", command=f"/review-plan {wid}", wid=wid,
    )


def _decide_awaiting_manual_external_plan_review(managed_repo: Any, work_item: Any) -> Decision:
    wid = _work_item_id(work_item)
    return _ordinary_case_placeholder(
        phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
        command=f"/record-manual-plan-review {wid}",
        wid=wid,
    )


def _decide_awaiting_external_plan_review(managed_repo: Any, work_item: Any) -> Decision:
    wid = _work_item_id(work_item)
    return _ordinary_case_placeholder(
        phase="AWAITING_EXTERNAL_PLAN_REVIEW", command=f"/apply-plan-review {wid}", wid=wid,
    )


def _decide_awaiting_plan_approval(managed_repo: Any, work_item: Any) -> Decision:
    wid = _work_item_id(work_item)
    return Decision(
        observed_phase="AWAITING_PLAN_APPROVAL",
        evidence=(),
        action=None,
        automatic=False,
        gate=HumanGate(
            repository=str(managed_repo.root),
            work_item_id=wid,
            phase="AWAITING_PLAN_APPROVAL",
            what_is_required=(
                "a human runs /approve-review plan to approve or reject the reviewed plan"
            ),
            artifact_path=None,
            safe_resume_command=f"/approve-review plan {wid}",
        ),
        declined=False,
        reason="AWAITING_PLAN_APPROVAL's only writer is /approve-review plan, which is "
               "user-only (disable-model-invocation: true, and it carries the "
               "user-confirmation guard) -- the Controller never selects it",
    )


def _decide_declined(*, phase: str, command: str, managed_repo: Any, work_item: Any) -> Decision:
    wid = _work_item_id(work_item)
    return Decision(
        observed_phase=phase,
        evidence=(),
        action=Action(command=command),
        automatic=False,
        gate=None,
        declined=True,
        reason=f"{phase} is automation-safe -- {command} is model-invocable and on "
               "neither denylist -- but Generation 1's own scope (revision 10) reports "
               "it rather than launching it",
    )


def _decide_gate_report(
    *, phase: str, what_is_required: str, safe_resume_command: str, managed_repo: Any,
    work_item: Any,
) -> Decision:
    wid = _work_item_id(work_item)
    return Decision(
        observed_phase=phase,
        evidence=(),
        action=None,
        automatic=False,
        gate=HumanGate(
            repository=str(managed_repo.root),
            work_item_id=wid,
            phase=phase,
            what_is_required=what_is_required,
            artifact_path=None,
            safe_resume_command=safe_resume_command,
        ),
        declined=False,
        reason=f"{phase} is a report-only phase (revision 10's scope): {what_is_required}",
    )


def _decide_legacy_ready(managed_repo: Any, work_item: Any) -> Decision:
    return Decision(
        observed_phase="LEGACY_READY",
        evidence=(),
        action=None,
        automatic=False,
        gate=None,
        declined=False,
        reason="this repository holds a dormant D-Legacy adoption entry; there is no "
               "in-flight work item to advance and no human artifact is pending",
    )


def _decide_milestone_complete(managed_repo: Any, work_item: Any) -> Decision:
    return Decision(
        observed_phase="MILESTONE_COMPLETE",
        evidence=(),
        action=None,
        automatic=False,
        gate=None,
        declined=False,
        reason="MILESTONE_COMPLETE is terminal: there is nothing to do",
    )


def _decide_vocabulary(phase: str, managed_repo: Any, work_item: Any) -> Decision:
    raise NoSupportedActionError(
        f"{_work_item_id(work_item)!r} is at {phase!r}, one of the four vocabulary phases no "
        "writer in frozen Workflow v2.3.1 ever persists -- recognising it as a known phase is "
        "not the same as being able to act on it",
        evidence={"work_item_id": _work_item_id(work_item), "phase": phase},
    )


_DISPATCH = {
    "PLANNING": _decide_planning,
    "REVISING_PLAN": _decide_revising_plan,
    "AWAITING_LOCAL_PLAN_REVIEW": _decide_awaiting_local_plan_review,
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": _decide_awaiting_manual_external_plan_review,
    "AWAITING_EXTERNAL_PLAN_REVIEW": _decide_awaiting_external_plan_review,
    "AWAITING_PLAN_APPROVAL": _decide_awaiting_plan_approval,
    "LEGACY_READY": _decide_legacy_ready,
    "MILESTONE_COMPLETE": _decide_milestone_complete,
}


def decide(managed_repo: Any, snapshot: Any, work_item: Any) -> Decision:
    """The whole of CP4's decision engine: a pure mapping from
    ``work_item.phase`` (plus, for the command-file partition, a read of
    ``managed_repo.root/.claude/commands/*.md``) to a :class:`Decision`.
    Reads no ``.ai-review/`` bundle/feedback content -- that is CP4B's
    extension of this function.

    ``snapshot`` is accepted for CP4B's own future evidence reads (e.g.
    resolving sibling work items) and is not read by CP4's own mapping.

    Raises :class:`~controller.errors.NoSupportedActionError` for the four
    vocabulary phases, and for any phase string outside
    :data:`KNOWN_PHASES` -- unreachable in practice, since
    ``target_state.read`` already refuses an unknown phase before this
    function ever sees one, but never assumed here.
    """
    phase = work_item.phase
    if phase not in KNOWN_PHASES:
        raise NoSupportedActionError(
            f"{phase!r} is not one of the Controller's known Workflow phases",
            evidence={"phase": phase, "known_phases": sorted(KNOWN_PHASES)},
        )

    if phase in VOCABULARY_PHASES:
        return _decide_vocabulary(phase, managed_repo, work_item)

    if phase in DECLINED_PHASES:
        command = f"/milestone-implement {_work_item_id(work_item)}"
        return _decide_declined(phase=phase, command=command, managed_repo=managed_repo,
                                 work_item=work_item)

    if phase in GATE_REPORT_PHASES:
        wid = _work_item_id(work_item)
        gate_specs = {
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW": (
                "an external implementation review must be uploaded and its verdict "
                "recorded (record it with /apply-implementation-review, or approve it "
                "with the user-only /approve-review implementation)",
                f"/apply-implementation-review {wid}",
            ),
            "APPLYING_REVIEW_FEEDBACK": (
                "an implementation-review apply was interrupted here; no Workflow "
                "command can legally run from this phase -- a human must decide how "
                "to recover",
                f"workflow-controller explain --work-item {wid}",
            ),
            "AWAITING_FUNCTIONAL_REVIEW": (
                "manual functional testing findings must be prepared, placed and "
                "consumed before this milestone can be accepted "
                "(/prepare-functional-review, then /apply-functional-review, then the "
                "user-only /accept-milestone)",
                f"/prepare-functional-review {wid}",
            ),
        }
        what_is_required, safe_resume_command = gate_specs[phase]
        return _decide_gate_report(
            phase=phase, what_is_required=what_is_required,
            safe_resume_command=safe_resume_command, managed_repo=managed_repo,
            work_item=work_item,
        )

    handler = _DISPATCH.get(phase)
    if handler is None:
        raise NoSupportedActionError(
            f"{phase!r} is a known phase with no mapped decision in this Controller "
            "generation",
            evidence={"phase": phase},
        )
    return handler(managed_repo, work_item)
