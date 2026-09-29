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
``work_item``: ``work_item_id``, ``phase``, ``governing_workflow_version``,
and -- for the ``IMPLEMENTING``/``SELF_REVIEWING_IMPLEMENTATION``
plan-approval gate -- ``plan_approval``).
**``NO_PHASE``/``NO_PHASE_WIRE`` are the one exception to "imports nothing
from later modules", and it runs the other way**: this module *declares*
them (revision 71 relocation, ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``'s
"``NO_PHASE``'s durable form" section) precisely because
:func:`decide_no_work_item` below is the sentinel's first producer, and
``target_state`` (later in the order) imports it from here rather than the
reverse -- the single-instance property the ``is NO_PHASE`` comparisons
CP6/CP7 perform depend on requires exactly one owning module, and it must
be the earliest one that needs it. The same holds for the phase list:
:data:`KNOWN_PHASES` and :data:`TERMINAL_PHASES` are declared here, once,
and ``target_state`` re-exports them.

**Split with CP4B** (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP4 /
CP4B -- Next-action decision engine and human-gate classification"): this
checkpoint owns the phase -> action mapping over all twenty known
phases, the union-derived user-only denylist and command-file partition
(:func:`classify_command_files`/:func:`derive_user_only_commands`, which
scan a ``.claude/commands/`` directory -- a structural fact about the
installed Workflow version, not a per-work-item evidence read, and
asserted *at test time* against the installed artifact rather than called
from :func:`decide`, which reads no command file at all), the phase-set
equality assertion, and the ``Decision``/``Action``/``HumanGate`` shapes.
It does **not** read any ``.ai-review/`` bundle, feedback or manifest file
scoped to the work item's current review round -- that is CP4B's
``controller/evidence.py``, which extends :func:`decide` in place once it
exists.

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
mistake the placeholder for the finished behaviour. The same applies to
``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` (revision 64's own
three-way sub-case, "Reporting rules for the phases Generation 1 does not
drive"): CP4 reports it with a fixed, evidence-independent text, exactly
as it already does for the three pre-existing static gate phases
(``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW``, ``APPLYING_REVIEW_FEEDBACK``,
``AWAITING_FUNCTIONAL_REVIEW``); CP4B sharpens all four alike.

**The general automatic-dispatch rule** (``docs/ai-workflow/
CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md``, CP3) replaced
Generation 1's per-phase report-only/declined/gate sets. Every phase
handler -- here and in ``controller.evidence`` -- either returns a gate or
*selects* an action, and :func:`apply_dispatch_rule` then decides, in one
place for every phase alike, whether the selection launches: automatic iff
its ``(phase, governing_workflow_version, command token)`` is one of
:data:`AUTOMATIC_TRIPLES` (the keys of ``controller.job.EXPECTED_OUTCOMES``),
declined otherwise.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from controller.errors import MissingCommandsDirectoryError, NoSupportedActionError, UnknownPhaseError

class _NoPhaseType:
    """The sentinel type of :data:`NO_PHASE`.

    Revision 64 (round 63's B2), relocated here at revision 71: the single
    in-memory value every field that would otherwise need to represent
    "there is no phase" carries -- ``Decision.observed_phase`` for a
    ``NoWorkItemYet`` target (:func:`decide_no_work_item`),
    ``pre_state.phase``, ``observed_phase_before`` and a job record's
    ``expected_transition.from``. Never ``None``: ``None`` keeps the one
    meaning the schema already gives it (an absent optional field) and is
    never overloaded a second way. ``is``-comparable, deliberately not a
    plain string -- nothing about a real Workflow phase name should ever
    compare equal to it.
    """

    def __repr__(self) -> str:  # pragma: no cover -- diagnostic convenience
        return "NO_PHASE"


#: The single sentinel used everywhere "no phase" needs representing (see
#: :class:`_NoPhaseType`). Never a Workflow phase, never ``None``. Declared
#: here, not in ``target_state`` -- see this module's own docstring.
NO_PHASE = _NoPhaseType()

#: ``NO_PHASE``'s single durable (JSON) form -- a reserved literal that can
#: never collide with a real Workflow phase name, since :data:`KNOWN_PHASES`
#: is a closed set of ``A-Z_`` identifiers none of which begins or ends
#: with a double underscore. A writer maps ``NO_PHASE -> NO_PHASE_WIRE``
#: and every real phase to its own name; a reader maps
#: ``NO_PHASE_WIRE -> NO_PHASE``, any member of :data:`KNOWN_PHASES` to
#: itself, and anything else to a refusal, never a guess.
NO_PHASE_WIRE = "__NO_PHASE__"

#: The closed set of all twenty phases every admitted Workflow release's
#: own ``scripts/workflow_state.py:KNOWN_PHASES`` persists. A literal copy,
#: not an import -- the Controller must never import ``scripts/`` -- and the
#: Controller's one phase list: ``controller.target_state`` re-exports it
#: rather than holding a second copy. Kept honest by a two-directional
#: set-equality test against each admitted release's own module
#: (``tests/test_target_state.py``, one subprocess per vendored release
#: tree) and against a hand-copied set of the twenty names
#: (``tests/test_decision.py``).
KNOWN_PHASES: frozenset[str] = frozenset({
    # v1 (docs/ai-workflow/MILESTONE_WORKFLOW.md)
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
    # v2.1-only additions
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
    "AWAITING_TECHNICAL_APPROVAL",
    # D-Legacy phase 1 -- dormant, not terminal
    "LEGACY_READY",
    # workflow-2.4.0 addition (D-Plan-Amendment-1): real and persisted --
    # entered by request_plan_amendment alone, survives an interruption
    # between the amendment request and the first post-request
    # /milestone-plan call.
    "AMENDING_PLAN",
    # workflow-2.5.0 additions (D-Implementation-Review-Stages): "2.2"-only,
    # real and persisted, mirroring AWAITING_LOCAL_PLAN_REVIEW/
    # AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW's own local-then-manual-external
    # shape at the implementation stage.
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
})

#: Only ``MILESTONE_COMPLETE`` is terminal -- ``LEGACY_READY`` is
#: explicitly dormant, not terminal, matching every admitted release's own
#: ``TERMINAL_PHASES``. Re-exported by ``controller.target_state``, like
#: :data:`KNOWN_PHASES`.
TERMINAL_PHASES: frozenset[str] = frozenset({"MILESTONE_COMPLETE"})


def phase_to_wire(phase: "str | _NoPhaseType") -> str:
    """``NO_PHASE``'s durable-form round-trip rule (``docs/ai-workflow/
    CONTROLLER_GEN1_PLAN.md``, "Controller-owned runtime state"), writer
    half: maps :data:`NO_PHASE` to :data:`NO_PHASE_WIRE` and every real
    phase to its own, unchanged name. The single function every one of
    ``pre_state.phase``, ``observed_phase_before`` and a job record's
    ``expected_transition.from`` is written through, so the mapping can
    never drift between the three call sites (CP6)."""
    if phase is NO_PHASE:
        return NO_PHASE_WIRE
    return phase


def phase_from_wire(wire: Any) -> "str | _NoPhaseType":
    """The reader half of the same rule: maps :data:`NO_PHASE_WIRE` to
    :data:`NO_PHASE`, any member of :data:`KNOWN_PHASES` to itself, and
    anything else -- a literal ``null``, a bare ``"None"``, or an
    unrecognised string -- to :class:`~controller.errors.UnknownPhaseError`,
    never a guess. Total over the domain and fail-closed outside it, the
    same posture :class:`~controller.errors.UnknownPhaseError` already
    takes for a phase field read out of the *target's* own state."""
    if wire == NO_PHASE_WIRE:
        return NO_PHASE
    if wire in KNOWN_PHASES:
        return wire
    raise UnknownPhaseError(
        f"{wire!r} is neither NO_PHASE_WIRE nor a member of the known-phase set",
        evidence={"wire": wire},
    )


#: The four vocabulary phases frozen Workflow's ``KNOWN_PHASES``
#: carries but that no writer in ``scripts/workflow_state.py`` ever
#: persists. Recognising one as *known* is not the same as *acting* on it:
#: every one of them maps to :class:`~controller.errors.NoSupportedActionError`.
VOCABULARY_PHASES: frozenset[str] = frozenset({
    "SELF_REVIEWING_PLAN",
    "AWAITING_TECHNICAL_APPROVAL",
    "FIXING_FUNCTIONAL_FINDINGS",
    "AWAITING_USER_ACCEPTANCE",
})

# ---------------------------------------------------------------------------
# The general automatic-dispatch rule (automatic-lifecycle-orchestration
# CP3, ``docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md``,
# "The general automatic-dispatch rule").
# ---------------------------------------------------------------------------

#: Every ``(phase, governing_workflow_version, command token)`` a selected
#: action may be **launched** at -- a literal copy of
#: ``{(eo.from_phase, eo.governing_version, eo.action) for eo in
#: controller.job.EXPECTED_OUTCOMES}``. This module cannot import ``job``
#: (it sits later in ``DEPENDENCY_ORDER``), so the copy is held equal to the
#: table by a two-directional test, the same pattern the ``KNOWN_PHASES``
#: copies use: "has a declared, verifiable expected outcome" and "is
#: launched" can never drift apart. The command token is the
#: slash-prefixed first word of ``Action.command``.
AUTOMATIC_TRIPLES: frozenset[tuple["str | _NoPhaseType", str | None, str]] = frozenset({
    # The plan stage (rows 1-7).
    ("PLANNING", "1", "/milestone-plan"),
    ("PLANNING", "2.1", "/milestone-plan"),
    ("PLANNING", "2.2", "/milestone-plan"),
    ("AWAITING_LOCAL_PLAN_REVIEW", "2.1", "/review-plan"),
    ("AWAITING_LOCAL_PLAN_REVIEW", "2.2", "/review-plan"),
    ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.1", "/record-manual-plan-review"),
    ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.2", "/record-manual-plan-review"),
    ("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review"),
    ("REVISING_PLAN", "2.1", "/apply-plan-review"),
    ("REVISING_PLAN", "2.2", "/apply-plan-review"),
    (NO_PHASE, None, "/milestone-plan"),
    # The implementation stage (rows 12-18).
    ("IMPLEMENTING", "2.1", "/milestone-implement"),
    ("IMPLEMENTING", "2.2", "/milestone-implement"),
    ("SELF_REVIEWING_IMPLEMENTATION", "2.1", "/milestone-implement"),
    ("SELF_REVIEWING_IMPLEMENTATION", "2.2", "/milestone-implement"),
    ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation"),
    ("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2", "/record-manual-implementation-review"),
    ("APPLYING_REVIEW_FEEDBACK", "2.2", "/apply-implementation-review"),
})

def command_token(command: str) -> str:
    """The one action normalisation: the slash-prefixed command token
    (``"/milestone-plan"``) of a full ``Action.command``
    (``"/milestone-plan wi-1"``) -- the form ``AUTOMATIC_TRIPLES`` and
    ``ExpectedOutcome.action`` both hold."""
    return command.split()[0]


def _triple_text(phase: "str | _NoPhaseType", version: str | None, token: str) -> str:
    version_text = "None" if version is None else f'"{version}"'
    return f"({phase_to_wire(phase)}, {version_text}, {token})"


def uniform_decline_reason(phase: "str | _NoPhaseType", version: str | None, command: str) -> str:
    """The single reason every action the dispatch rule declines carries,
    whatever phase selected it."""
    return (
        f"{phase_to_wire(phase)} selects {command}, which is model-invocable, but no verifiable "
        f"ExpectedOutcome is declared for {_triple_text(phase, version, command_token(command))}, "
        "so this Controller reports it instead of launching it"
    )


@dataclasses.dataclass(frozen=True)
class ActionClassification:
    """:func:`classify_selected_action`'s answer: ``automatic`` iff the
    selected action may be launched; otherwise ``decline_reason`` says
    why not."""

    automatic: bool
    decline_reason: str | None


def classify_selected_action(
    phase: "str | _NoPhaseType", version: str | None, command: str,
) -> ActionClassification:
    """The general automatic-dispatch rule, in one place for every phase
    alike: a selected ``command`` is **automatic** iff ``(phase, version,
    command_token(command))`` is a member of :data:`AUTOMATIC_TRIPLES`, and
    **declined** otherwise. "Model-invocable and not user-only" is already
    guaranteed by :func:`classify_command_files`'s partition (test time)
    and ``worker.USER_ONLY_COMMANDS`` (launch time); "not otherwise
    blocked" is the phase handler's own gate, which never reaches this
    function.

    CP3's interim set of phases awaiting their evidence handlers (whose
    rows existed before their evidence gates did) is gone: CP4
    installed the gates of ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` and
    ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW``, and CP4B those of
    ``APPLYING_REVIEW_FEEDBACK`` (feedback admissibility and the apply
    relaunch bound, ``controller.evidence``), so the rule alone decides."""
    if (phase, version, command_token(command)) in AUTOMATIC_TRIPLES:
        return ActionClassification(True, None)
    return ActionClassification(False, uniform_decline_reason(phase, version, command))


# ---------------------------------------------------------------------------
# The command-file partition and the user-only denylist.
# ---------------------------------------------------------------------------

#: Where the target repository's own copy of the frozen Workflow release's
#: command files lives, relative to ``managed_repo.root``.
COMMANDS_REL_DIR = ".claude/commands"

#: Recogniser 1, the qualified-literal guard (revision 46, local round
#: 45's ``OPUS-R45-B1``): a command file "carries a user-confirmation
#: guard" when its text contains the literal dotted reference
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

#: Recogniser 2, the front-matter flag (revision 64, round 63's ``B6``,
#: widening the denylist from one recogniser to the union of two): a
#: command file "declares itself model-uninvocable" when its YAML front
#: matter -- the block delimited by the file's own first two ``---``
#: lines, never the body -- carries a line whose text, after stripping
#: leading whitespace, is exactly ``disable-model-invocation: true``. A
#: body that merely *discusses* the flag is not a file that carries it;
#: the delimiters answer that by location.
_FRONT_MATTER_DELIMITER = "---"
_DISABLE_MODEL_INVOCATION_LINE = "disable-model-invocation: true"


def carries_user_confirmation_guard(text: str) -> bool:
    """``True`` iff ``text`` contains the qualified literal
    ``workflow_state.validate_...confirmation`` -- matched anywhere in the
    file's text, with or without a following argument list. A bare prose
    citation of another command's guard (the ``` `validate_user_confirmation` ```
    form, with no ``workflow_state.`` prefix) never matches."""
    return _USER_CONFIRMATION_GUARD_PATTERN.search(text) is not None


def _front_matter_block(text: str) -> str | None:
    """The text strictly between a file's first two ``---``-only lines,
    or ``None`` if the file does not open with one. Scoping to this block
    is what keeps recogniser 2 a declaration rather than a mention -- a
    body paragraph that discusses the flag (as ``request-plan-amendment.md``'s
    own user-only prose, and this plan, both do) is outside it."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIMITER:
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONT_MATTER_DELIMITER:
            return "\n".join(lines[1:index])
    return None


def carries_disable_model_invocation_flag(text: str) -> bool:
    """``True`` iff ``text``'s own YAML front matter -- never its body --
    carries a line that, stripped of leading whitespace, is exactly
    ``disable-model-invocation: true``."""
    block = _front_matter_block(text)
    if block is None:
        return False
    return any(line.strip() == _DISABLE_MODEL_INVOCATION_LINE for line in block.splitlines())


#: The eight command files a selected action may be **launched** for: the
#: command stems of :data:`AUTOMATIC_TRIPLES`, i.e. of the eighteen rows of
#: ``controller/job.py``'s ``EXPECTED_OUTCOMES`` (a documentation
#: cross-reference only -- ``job`` imports this module, never the reverse;
#: a test holds ``{token[1:] for (_, _, token) in AUTOMATIC_TRIPLES}``
#: inside this set). This partition is over *files*, not rows.
SELECTED_COMMANDS: frozenset[str] = frozenset({
    "milestone-plan",
    "review-plan",
    "record-manual-plan-review",
    "apply-plan-review",
    "milestone-implement",
    "review-implementation",
    "apply-implementation-review",
    "record-manual-implementation-review",
})

#: The five model-invocable command files no ``ExpectedOutcome`` row
#: declares, so the dispatch rule never launches any of them, each with the
#: one-line reason.
DELIBERATELY_NOT_SELECTED_COMMANDS: dict[str, str] = {
    "review-functional": (
        "an advisory, report-only review that writes no Workflow state, so no ExpectedOutcome "
        "row declares it and the dispatch rule never launches it"
    ),
    "apply-functional-review": (
        "AWAITING_FUNCTIONAL_REVIEW selects it when findings are unconsumed, but no "
        "ExpectedOutcome row declares it, so the dispatch rule declines it"
    ),
    "prepare-functional-review": (
        "named only as a functional-review gate's safe_resume_command; no ExpectedOutcome row "
        "declares it, so the dispatch rule never launches it"
    ),
    "bootstrap-workflow-v2": (
        "outside the milestone lifecycle entirely; no ExpectedOutcome row declares it"
    ),
    "prepare-review": "outside the milestone lifecycle entirely; no ExpectedOutcome row declares it",
}

#: Category labels :func:`classify_command_files` returns.
CATEGORY_SELECTED = "selected"
CATEGORY_DELIBERATELY_NOT_SELECTED = "deliberately_not_selected"
CATEGORY_USER_ONLY = "user_only"


def _iter_command_files(commands_dir: Path) -> list[Path]:
    # `Path.glob` treats a missing directory as vacuously empty, not a
    # refusal -- indistinguishable from a genuinely empty, existing one.
    # Fail closed instead (`O1`, MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW
    # round 1): `classify_command_files`'s contract is total classification
    # of an existing command surface, not a silent empty one.
    if not commands_dir.is_dir():
        raise MissingCommandsDirectoryError(
            f"commands directory not found: {commands_dir}",
            evidence={"commands_dir": str(commands_dir)},
        )
    return sorted(commands_dir.glob("*.md"))


def classify_command_files(commands_dir: Path) -> dict[str, str]:
    """Enumerate every ``*.md`` file directly under ``commands_dir`` --
    **the external artifact, not the plan's own tables** -- and classify
    each one into exactly one of :data:`CATEGORY_SELECTED`,
    :data:`CATEGORY_DELIBERATELY_NOT_SELECTED` or
    :data:`CATEGORY_USER_ONLY`.

    The union of the two recognisers runs first and wins unconditionally:
    a file that carries **either** the qualified user-confirmation literal
    **or** the front-matter ``disable-model-invocation: true`` flag is
    ``user_only`` regardless of whether its name also appears in
    :data:`SELECTED_COMMANDS` or :data:`DELIBERATELY_NOT_SELECTED_COMMANDS`
    -- the property is what is authoritative, never a name list a future
    file could simply not appear on. Neither recogniser is subordinate to
    the other (revision 64, round 63's ``B6``): at the 2.5.1 reference
    release they are no longer nested (``request-plan-amendment.md``
    carries only the flag; ``recover-implementation-provenance.md``
    carries only the guard), so a single-recogniser derivation misses
    exactly one file each way. A file whose name is in neither static set
    and that carries neither recogniser is a command this Controller
    generation cannot classify at all, and that is a fail-closed
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
        if carries_user_confirmation_guard(text) or carries_disable_model_invocation_flag(text):
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
    """The **four**-command user-only denylist, derived fresh from
    ``commands_dir`` as the **union** of the two recognisers above --
    never a copied list. Two independent recognisers, each total over the
    same enumeration, combined by set union -- the fail-closed direction,
    because a file either recogniser claims is user-only is refused, and a
    wrong classification can only *remove* a command from the Controller's
    reach, never add one."""
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
    to CP5's worker launcher unchanged.

    ``task_addendum`` (automatic-lifecycle-orchestration CP4B, additive:
    every existing construction leaves it ``None``) is the one place a
    worker's task is not the bare selected command: when set, the launcher
    hands the worker ``f"{command}\\n\\n{task_addendum}"``. Only the
    ``"2.2"`` ``APPLYING_REVIEW_FEEDBACK`` handler sets it, with
    ``controller.evidence``'s pinned pending-review-stage-write addendum.
    ``command`` alone still keys the ``ExpectedOutcome`` lookup and the
    dispatch rule."""

    command: str
    task_addendum: str | None = None


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


#: The phase a milestone-branch gate reports: the gate comes from the
#: repository preflight (``controller.milestone_branch``), ahead of any
#: Workflow phase handler.
BRANCH_GATE_PHASE = "MILESTONE_BRANCH"

#: ``controller.milestone_branch``'s gate codes (``GATE_CODES``) -> what the
#: human has to do, in plain language (trunk-branch-pr-release CP7). The
#: preflight's own message and exits carry the specifics.
BRANCH_GATE_TEXTS = {
    "switch_to_trunk": "the milestone branch's binding is finished; switch to the trunk",
    "bound_item_missing": "restore the bound work item's plan files, or abandon the binding",
    "pr_closed_unmerged": "the milestone's pull request was closed without merge; reopen it, "
                          "or run milestone-binding --new-pr or --abandon",
    "merged_before_acceptance": "the milestone branch was merged before acceptance; run "
                                "milestone-binding with the disposition the gate names",
    "fast_forward_trunk": "fast-forward the trunk to its remote before a milestone starts",
    "post_acceptance_commits": "commits follow the acceptance commit; merge anyway on GitHub, or leave "
                               "the pull request unready",
    "integration_required": "the trunk moved past the milestone's base; mark the pull request ready and "
                            "merge it on GitHub with \"Create a merge commit\"",
    "checks_pending": "wait for the pull request's checks to finish",
    "checks_failing": "fix the pull request's failing checks",
    "checks_cancelled": "re-run the pull request's cancelled checks on GitHub",
    "pr_head_not_accepted": "wait until the pull request shows the acceptance commit",
    "merge_pull_request": "merge the pull request on GitHub with \"Create a merge commit\"",
    "merge_method_rewrote_history": "a squash or rebase merge rewrote the reviewed history; switch to the "
                                    "trunk by hand, and disable squash and rebase merging",
    "unmerged_commits": "move the unmerged commits off the milestone branch",
    "dirty_tree": "commit, stash or discard the tracked changes",
    "pr_title_invalid": "the pull request's title is not a valid Conventional Commit; set a valid title "
                        "on GitHub",
}

#: The texts a squash-mode gate (``merge_method: "squash"`` in the binding's
#: policy) uses instead of :data:`BRANCH_GATE_TEXTS`'s.
BRANCH_GATE_TEXTS_SQUASH = {
    "post_acceptance_commits": "commits follow the acceptance commit; merge anyway on GitHub with \"Squash "
                               "and merge\", or leave the pull request unready",
    "integration_required": "the trunk moved past the milestone's base; mark the pull request ready and "
                            "merge it on GitHub with \"Squash and merge\"",
    "merge_pull_request": "merge the pull request on GitHub with \"Squash and merge\"",
}


def branch_human_gate(repository: str, gate: Any, *, phase: str = BRANCH_GATE_PHASE) -> HumanGate:
    """The :class:`HumanGate` for a milestone-branch preflight gate (a
    duck-typed ``controller.milestone_branch.Gate``: ``code``,
    ``work_item_id``, ``message``, ``exits``, and ``merge_method``, whose
    ``"squash"`` selects :data:`BRANCH_GATE_TEXTS_SQUASH`)."""
    if gate.code not in BRANCH_GATE_TEXTS:
        raise ValueError(f"unknown milestone-branch gate {gate.code!r}")
    texts = BRANCH_GATE_TEXTS_SQUASH if getattr(gate, "merge_method", "merge") == "squash" else {}
    return HumanGate(
        repository=repository,
        work_item_id=gate.work_item_id or "",
        phase=phase,
        what_is_required=f"{texts.get(gate.code, BRANCH_GATE_TEXTS[gate.code])} ({gate.code}): {gate.message}",
        artifact_path=None,
        safe_resume_command=gate.exits[0] if gate.exits else "workflow-controller step",
    )


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

    observed_phase: str | _NoPhaseType
    evidence: tuple[str, ...]
    action: Action | None
    automatic: bool
    gate: HumanGate | None
    declined: bool
    reason: str


def apply_dispatch_rule(selected: Decision, governing_workflow_version: str | None) -> Decision:
    """Turn a phase handler's output into the final :class:`Decision`.

    Handlers only *select*: a selected action arrives as
    ``automatic=True`` with its ``action`` populated, and whether it is
    launched is decided here, by :func:`classify_selected_action` keyed on
    ``(selected.observed_phase, governing_workflow_version, command
    token)`` -- the same key ``controller.job`` looks the ``ExpectedOutcome``
    up by. A declined selection keeps its phase, action and evidence and
    carries the classification's reason. Every other decision (a gate, a
    no-action phase, an already-declined one) passes through unchanged, so
    applying the rule twice is harmless."""
    if not selected.automatic or selected.action is None:
        return selected
    classification = classify_selected_action(
        selected.observed_phase, governing_workflow_version, selected.action.command,
    )
    if classification.automatic:
        return selected
    # Built field by field (not `dataclasses.replace`), so the package-wide
    # write-containment scan never has a `.replace(` call to adjudicate.
    return Decision(
        observed_phase=selected.observed_phase,
        evidence=selected.evidence,
        action=selected.action,
        automatic=False,
        gate=None,
        declined=True,
        reason=classification.decline_reason,
    )


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


def _selected(*, phase: str, command: str, reason: str, evidence: tuple[str, ...] = ()) -> Decision:
    """A phase handler's selection of ``command``, ahead of
    :func:`apply_dispatch_rule`, which alone decides whether it launches."""
    return Decision(
        observed_phase=phase,
        evidence=evidence,
        action=Action(command=command),
        automatic=True,
        gate=None,
        declined=False,
        reason=reason,
    )


#: The governing versions whose ``/milestone-implement`` branch runs step
#: 1a's entry validation (``implementing_entry_reachable``) on every
#: invocation -- the ``"2.1"`` branch, which ``"2.2"`` takes identically.
#: The ``"1"`` branch reads no plan approval, so it gets no gate here: it
#: is selected and the dispatch rule declines it (no row).
_PLAN_APPROVAL_GATED_VERSIONS: frozenset[str] = frozenset({"2.1", "2.2"})


def _plan_approval_status(plan_approval: Any) -> str:
    if plan_approval is None:
        return "absent"
    if not isinstance(plan_approval, dict):
        return f"not an object ({type(plan_approval).__name__})"
    return f"status {plan_approval.get('status')!r}"


def _decide_implementing(managed_repo: Any, work_item: Any) -> Decision:
    """``IMPLEMENTING``/``SELF_REVIEWING_IMPLEMENTATION``: select
    ``/milestone-implement <id>``, after gating first when the plan
    approval is not ``CURRENT`` -- launching a worker that the command's
    own step 1a refuses would be wasted spend."""
    phase = work_item.phase
    wid = _work_item_id(work_item)
    plan_approval = work_item.plan_approval
    current = isinstance(plan_approval, dict) and plan_approval.get("status") == "CURRENT"
    if work_item.governing_workflow_version in _PLAN_APPROVAL_GATED_VERSIONS and not current:
        status = _plan_approval_status(plan_approval)
        return Decision(
            observed_phase=phase,
            evidence=(f"plan_approval: {status}",),
            action=None,
            automatic=False,
            gate=HumanGate(
                repository=str(managed_repo.root),
                work_item_id=wid,
                phase=phase,
                what_is_required=(
                    "/milestone-implement's entry validation (step 1a) refuses on a missing "
                    "or stale plan approval"
                ),
                artifact_path=None,
                safe_resume_command=f"workflow-controller explain --work-item {wid}",
            ),
            declined=False,
            reason=f"{phase}: plan_approval is {status}, not CURRENT -- /milestone-implement "
                   "would refuse at its own step 1a, so nothing is launched",
        )
    return _selected(
        phase=phase, command=f"/milestone-implement {wid}",
        reason=f"{phase}: /milestone-implement is the phase's one legal next step (one "
               "checkpoint per invocation, then the final self-review and bundle generation)",
    )


def _decide_amending_plan(managed_repo: Any, work_item: Any) -> Decision:
    return _selected(
        phase="AMENDING_PLAN", command=f"/milestone-plan {_work_item_id(work_item)}",
        reason="AMENDING_PLAN's next step is /milestone-plan",
    )


def _decide_awaiting_local_implementation_review(managed_repo: Any, work_item: Any) -> Decision:
    return _selected(
        phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
        command=f"/review-implementation {_work_item_id(work_item)}",
        reason="AWAITING_LOCAL_IMPLEMENTATION_REVIEW's next step is the local "
               "/review-implementation stage",
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
        "writer in the frozen Workflow release ever persists -- recognising it as a known "
        "phase is not the same as being able to act on it",
        evidence={"work_item_id": _work_item_id(work_item), "phase": phase},
    )


#: The evidence-independent gate each of these phases reports from this
#: module: ``(what_is_required, safe_resume_command template)``. Three of
#: the four are refined by ``controller.evidence``'s own handlers, which
#: run first; ``APPLYING_REVIEW_FEEDBACK`` is decided here at ``"1"``/
#: ``"2.1"``, with the corrected text of automatic-lifecycle-orchestration
#: CP4: ``/apply-implementation-review`` skips its own entry transition
#: exactly when the phase is already ``APPLYING_REVIEW_FEEDBACK`` and then
#: runs its steps 1-8, so it *is* legal from this phase (the ``"2.2"``
#: two-stage writers set this phase directly on ``REVISE``). At ``"2.2"``,
#: ``controller.evidence``'s automatic path (CP4B) runs in front of it and
#: falls back to the same text, naming what stops the launch.
_STATIC_GATES: dict[str, tuple[str, str]] = {
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW": (
        "an external implementation review must be uploaded and its verdict "
        "recorded (record it with /apply-implementation-review, or approve it "
        "with the user-only /approve-review implementation)",
        "/apply-implementation-review {wid}",
    ),
    "APPLYING_REVIEW_FEEDBACK": (
        "an implementation-review remediation is in progress or was interrupted; "
        "/apply-implementation-review is legal from this phase (it skips its own entry "
        "transition), so rerun it once the feedback on file is confirmed current",
        "/apply-implementation-review {wid}",
    ),
    "AWAITING_FUNCTIONAL_REVIEW": (
        "manual functional testing findings must be prepared, placed and "
        "consumed before this milestone can be accepted "
        "(/prepare-functional-review, then /apply-functional-review, then the "
        "user-only /accept-milestone)",
        "/prepare-functional-review {wid}",
    ),
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": (
        "an implementation bundle is waiting on a manual external reviewer's "
        "verdict -- upload the bundle and paste the verdict, or, once one is "
        "on file, record it with /record-manual-implementation-review (a "
        "Status: BLOCK verdict requires explicit user resolution first)",
        "/record-manual-implementation-review {wid}",
    ),
}


def _decide_static_gate(managed_repo: Any, work_item: Any) -> Decision:
    phase = work_item.phase
    what_is_required, resume_template = _STATIC_GATES[phase]
    return _decide_gate_report(
        phase=phase, what_is_required=what_is_required,
        safe_resume_command=resume_template.format(wid=_work_item_id(work_item)),
        managed_repo=managed_repo, work_item=work_item,
    )


_DISPATCH = {
    "PLANNING": _decide_planning,
    "REVISING_PLAN": _decide_revising_plan,
    "AWAITING_LOCAL_PLAN_REVIEW": _decide_awaiting_local_plan_review,
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": _decide_awaiting_manual_external_plan_review,
    "AWAITING_EXTERNAL_PLAN_REVIEW": _decide_awaiting_external_plan_review,
    "AWAITING_PLAN_APPROVAL": _decide_awaiting_plan_approval,
    "AMENDING_PLAN": _decide_amending_plan,
    "IMPLEMENTING": _decide_implementing,
    "SELF_REVIEWING_IMPLEMENTATION": _decide_implementing,
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW": _decide_awaiting_local_implementation_review,
    **{phase: _decide_static_gate for phase in _STATIC_GATES},
    "LEGACY_READY": _decide_legacy_ready,
    "MILESTONE_COMPLETE": _decide_milestone_complete,
}


def decide(managed_repo: Any, snapshot: Any, work_item: Any) -> Decision:
    """The whole of CP4's decision engine: a pure mapping from
    ``work_item.phase`` to a :class:`Decision`. Reads no ``.ai-review/``
    bundle/feedback content -- that is CP4B's extension of this function
    -- and, deliberately, reads no command file either: the command-file
    partition and the user-only denylist above
    (:func:`classify_command_files`, :func:`derive_user_only_commands`)
    are a **test-time** property over the installed artifact (the plan's
    own "computed fresh from the seventeen files *at test time*"), not a
    per-decision read. The runtime enforcement of "never fabricate user
    approval" lives in the second, independent layer --
    ``controller.worker``'s own literal copy of the resulting four-name
    set and its own token scan, checked before any subprocess is spawned
    -- so this function selecting only from :data:`SELECTED_COMMANDS` and
    that scan refusing anything on the denylist are two different
    mechanisms fed by two different sources, which is the point.

    ``snapshot`` is accepted for CP4B's own future evidence reads (e.g.
    resolving sibling work items) and is not read by CP4's own mapping.

    Each phase handler either gates or *selects* an action; whether a
    selected action launches is decided afterwards, for every phase alike,
    by :func:`apply_dispatch_rule` (the general automatic-dispatch rule,
    automatic-lifecycle-orchestration CP3).

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

    handler = _DISPATCH.get(phase)
    if handler is None:
        raise NoSupportedActionError(
            f"{phase!r} is a known phase with no mapped decision in this Controller "
            "generation",
            evidence={"phase": phase},
        )
    return apply_dispatch_rule(handler(managed_repo, work_item), work_item.governing_workflow_version)


def decide_no_work_item(managed_repo: Any, *, base: str | None = None) -> Decision:
    """The distinct, sibling entry point for a ``target_state.NoWorkItemYet``
    target (revision 63, B2, ``REQ-40``): called *before* :func:`decide`
    ever runs, since there is no ``WorkItemView`` -- and so no ``phase`` --
    to key the twenty-row table on. Manufacturing a synthetic phase string
    for this case would be exactly the invented-vocabulary-state mistake
    :data:`KNOWN_PHASES` is a closed, Workflow-vocabulary-derived set
    specifically built to avoid.

    Unconditional and version-independent: there is no work item yet to
    carry a ``governing_workflow_version``. Always names bare
    ``/milestone-plan`` with no work-item id -- frozen ``/milestone-plan``
    alone derives and creates the first work item from
    ``docs/ACTIVE_MILESTONE.md`` (``D-Plan-Amendment`` constraint), so the
    Controller must never supply one here, mirroring exactly what a human
    operator would type for a brand-new milestone.

    ``base`` (``workflow-controller-squash-merge-tag-versioning`` Design F)
    is the trunk tip a passed trunk start proved equal to
    ``<remote>/<trunk>``, given when milestone branches are enabled: the
    command is then ``/milestone-plan <base>``, Workflow's one-argument
    ``<base-sha>`` form, so the next milestone's base is the trunk tip even
    after a squash merge left the previous completion commit off the trunk.
    The triple stays ``(NO_PHASE, None, "/milestone-plan")``: every table
    keys on the command token. Without one the command is bare, as
    before."""
    command, reason = "/milestone-plan", ("no non-terminal work item exists and none was explicitly named; "
                                          "frozen /milestone-plan derives and creates the first one from "
                                          "docs/ACTIVE_MILESTONE.md")
    if base is not None:
        command = f"/milestone-plan {base}"
        reason += f", based on the trunk tip {base}"
    selected = Decision(
        observed_phase=NO_PHASE,
        evidence=(),
        action=Action(command=command),
        automatic=True,
        gate=None,
        declined=False,
        reason=reason,
    )
    # The bootstrap's own `(NO_PHASE, None, "/milestone-plan")` triple is
    # in AUTOMATIC_TRIPLES, so this stays automatic -- through the same
    # rule as every other selection, not around it.
    return apply_dispatch_rule(selected, None)
