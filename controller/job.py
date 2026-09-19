"""Job execution: durable Controller-owned job records, pre-state capture,
persist-before-launch, worker launch and result recording, and post-state
verification (capabilities 5, 7, 8, ``docs/ACTIVE_MILESTONE.md``; CP6 and
CP6B, ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP6 -- Job execution,
part 1" and "CP6B -- Job execution, part 2: post-state validation").

:func:`execute_step` is this module's own entry point, and it is **one**
function that spans all nine of the plan's job-execution steps -- CP6 owns
steps 1-6 (inspect and capture pre-state; decide; check for a pending
generation handoff; write the job record in two flushes, ``PLANNED`` then
``LAUNCHED``, before the worker is spawned; launch the worker; record its
result as ``COMPLETED``). **CP6B extends this same function** with steps
7-9 (a fresh post-state re-read, ``expected_transition`` verification, and
``FINISHED``/``FAILED``/``INCOMPLETE``) -- it does not add a second
function.

Dependency graph (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``,
"Dependency direction"): ``job -> {managed_repo, target_state, evidence,
worker, handoff} -> decision -> {identity, runtime, errors}``. ``handoff``
does not exist yet (CP8 adds it); step 3's pending-handoff check therefore
reads ``<runtime_root>/handoff.json`` directly through
``controller.runtime.read_json`` -- the exact read ``controller.cli``'s
own ``status`` command already performs -- rather than importing a module
that is not there yet.

**A deliberate name shadow.** :func:`execute_step`'s own parameters are
named ``identity`` (an already-resolved
``controller.identity.ControllerIdentity``) and ``runtime`` (the resolved
runtime-root ``Path``) -- the plan's own declared signature -- which
shadow this module's ``controller.identity``/``controller.runtime``
imports *inside that one function's body*. This is intentional, not an
oversight: every module-level helper below that actually performs I/O
(:func:`_persist`, :func:`_pending_handoff`, :func:`_write_worker_streams`)
is defined *outside* :func:`execute_step`, where the real modules are
still in scope, and takes the runtime root as an explicit ``runtime_root``
parameter -- never named ``runtime`` -- so :func:`execute_step` calls them
by passing its own (shadowed) local values in. This is also what keeps
"spy on ``controller.job.runtime.write_json``" a valid test technique: the
module-level import is the only thing ever called.

**CP6B's own scope note on step 7's "fresh ``managed_repo.inspect``".**
The plan's own step 7 text asks for both "a fresh ``managed_repo.inspect``
and a fresh state read." ``target_state.read`` only ever reads
``managed_repo.root`` (never ``.manifest``/``.workflow_version``/
``.profile``/``.verify``/``.status``), so a fresh :func:`target_state.read`
plus a fresh :func:`target_state.select_work_item` already re-derives
every fact the verification rule below reads, from disk, live -- never
from the ``snapshot``/``work_item`` captured before the worker ran. A
*second* ``managed_repo.inspect()`` call would additionally re-run
Workflow Manager's own ``verify``/``status`` subprocesses -- a genuine
installation-drift re-check, but an orthogonal question from "did the
selected action's own phase transition happen," and one this module's
``execute_step`` signature does not currently have a ``manager_bin`` to
resolve with (nothing else in the package threads one through yet). This
checkpoint therefore performs the fresh *state* read only, deliberately,
and leaves re-inspecting installation health mid-job to a later checkpoint
if one needs it.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
import secrets
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from controller import evidence, runtime, target_state, worker
from controller.decision import (
    NO_PHASE,
    Decision,
    decide_no_work_item,
    phase_to_wire,
)
from controller.errors import ControllerError, HumanGateError, StaleJobRecordError, UnreconcilableJobError

JobRecord = dict[str, Any]

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# The closed, ten-member job-status enumeration. `execute_step` writes the
# first six; CP6B writes `FINISHED`/`FAILED`/`INCOMPLETE`; CP7's `resume`
# writes the record-level `INTERRUPTED` on reconciliation.
# ---------------------------------------------------------------------------

STATUS_PLANNED = "PLANNED"
STATUS_LAUNCHED = "LAUNCHED"
STATUS_COMPLETED = "COMPLETED"
STATUS_FINISHED = "FINISHED"  # CP6B
STATUS_FAILED = "FAILED"  # CP6B
STATUS_INTERRUPTED = "INTERRUPTED"  # CP7 (record-level, resume-time)
STATUS_INCOMPLETE = "INCOMPLETE"  # CP6B
STATUS_GATE_BLOCKED = "GATE_BLOCKED"
STATUS_DECLINED = "DECLINED"
STATUS_HANDOFF_PENDING = "HANDOFF_PENDING"

#: `PRE_STATE_FIELDS` -- the single declaration both `_capture_pre_state`
#: and (in CP6B) the record-completeness property read, so the two cannot
#: drift (the plan's own stated defect history: a field declared in the
#: schema and never captured, round 8's B3). Only four of these seventeen
#: are ever a predicate *input* (`bundle_id`, `bundle_manifest_readable`,
#: `bundle_generated_digest` and, since revision 63's B2, `pre_work_item_keys`
#: -- CP6B's own concern); the other thirteen are report data for
#: `inspect`/`explain`/`status` and for a later
#: generation's own use.
PRE_STATE_FIELDS: frozenset[str] = frozenset({
    "phase",
    "governing_workflow_version",
    "target_head",
    "state_revision",
    "plan_revision",
    "implementation_revision",
    "last_completed_checkpoint_id",
    "checkpoints",
    "bundle_id",
    "bundle_manifest_readable",
    "bundle_manifest_generation_head",
    "bundle_generated_digest",
    "rejected_marker_present",
    "child_work_item_ids",
    "functional_review_consumed_blob",
    "functional_checklist_evidence",
    "pre_work_item_keys",
})

#: `acceptEdits` against this (real, managed) target repository --
#: `bypassPermissions` is reserved for disposable throwaway repositories
#: only (`docs/ACTIVE_MILESTONE.md`, capability 4), which `execute_step`
#: never targets.
DEFAULT_PERMISSION_MODE = "acceptEdits"

#: Generous enough for a full Workflow command turn (`/milestone-plan`,
#: `/apply-plan-review`, ...); overridable per call (and, from a later
#: checkpoint's CLI wiring, via `--timeout`).
DEFAULT_WORKER_TIMEOUT = 3600.0

#: The generator-written subset of `<bundle_dir>` that `_bundle_generated_
#: digest` covers -- exactly what `prepare-ai-review.sh` writes on every
#: supported invocation form (the plan's own "bundle_generated_digest --
#: what row 5 reads"). Explicitly excludes every author-written file,
#: `MANIFEST.md` and `PLAN.md`.
_BUNDLE_GENERATED_TOP_LEVEL_FILES = ("CHANGED_FILES.txt", "COMMITS.txt", "DIFF.patch")

# ---------------------------------------------------------------------------
# pre_state capture (step 1).
# ---------------------------------------------------------------------------


def _current_head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _bundle_generated_digest(root: Path, bundle_dir: Path) -> str | None:
    """SHA-256 over the generator-written subset of ``<bundle_dir>`` --
    :data:`_BUNDLE_GENERATED_TOP_LEVEL_FILES` and everything under
    ``files/`` -- as a mapping of POSIX-relative path to the file's own
    SHA-256, canonically serialised and hashed (the same shape
    ``controller.identity.compute_tree_digest`` uses for the source
    snapshot, scoped here to a fixed, named file set instead of a whole
    directory). ``None`` when ``bundle_dir`` does not exist or none of
    the covered paths are present -- "nothing generated to answer the
    question with" is distinct from an empty digest."""
    full_dir = root / bundle_dir
    covered: list[Path] = []
    for name in _BUNDLE_GENERATED_TOP_LEVEL_FILES:
        candidate = full_dir / name
        if candidate.is_file():
            covered.append(candidate)
    files_subdir = full_dir / "files"
    if files_subdir.is_dir():
        covered.extend(p for p in files_subdir.rglob("*") if p.is_file())
    if not covered:
        return None
    mapping = {
        p.relative_to(full_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in covered
    }
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _functional_review_consumed_blob(root: Path, work_item_id: str) -> str | None:
    """The current Git blob hash of ``FUNCTIONAL_REVIEW.md`` -- report
    data only (not a predicate input; see :data:`PRE_STATE_FIELDS`'s own
    four-of-seventeen note), ``None`` when the file does not exist."""
    findings_path = root / evidence.functional_review_findings_path(root, work_item_id)
    if not findings_path.is_file():
        return None
    result = subprocess.run(
        ["git", "hash-object", "--", str(findings_path)], cwd=root,
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _capture_pre_state(managed_repo: Any, snapshot: Any, work_item: Any) -> dict:
    """Capture every :data:`PRE_STATE_FIELDS` member -- the record's most
    consequential block (the plan's own "pre_state is revision 7's
    addition"): every input any CP6B `ExpectedOutcome` predicate reads
    must be a field of it, not merely held in the executing process's own
    memory, because at `resume` (CP7) the process that captured it is
    gone and this record is the entire input.

    ``pre_work_item_keys`` (revision 63's B2 addition, row 7's own
    `predicate_input`) is captured here for **every** call, normal or
    bootstrap alike -- a `frozenset` of the pre-snapshot's own
    `work_items` keys, serialised in sorted order so two runs over the
    same pre-state produce byte-identical records; the *post*-snapshot's
    own keys are read fresh at verification time (CP6B) and never
    captured here.

    ``work_item is target_state.NoWorkItemYet`` (revision 63's B2, the
    `NoWorkItemYet` bootstrap) is the one case with no `WorkItemView` to
    read: there is no work item yet, so every work-item-scoped member is
    its own field's report-only default (`None`/empty/``False``) and
    `phase` is the in-memory :data:`~controller.decision.NO_PHASE`
    sentinel -- never a synthetic phase string, and never Python's
    `None`, which the schema already gives a distinct meaning ("field
    absent"). `child_work_item_ids` is `[]` rather than every
    top-level (`parent_work_item_id is None`) entry in the snapshot: a
    work item that does not exist yet cannot have children, and a naive
    `parent_work_item_id == work_item_id` comparison with
    `work_item_id=None` would match every top-level entry instead."""
    root = managed_repo.root
    target_head = _current_head(root)
    pre_work_item_keys = sorted(snapshot.work_items.keys())

    if work_item is target_state.NoWorkItemYet:
        return {
            "phase": NO_PHASE,
            "governing_workflow_version": None,
            "target_head": target_head,
            "state_revision": None,
            "plan_revision": None,
            "implementation_revision": None,
            "last_completed_checkpoint_id": None,
            "checkpoints": {},
            "bundle_id": None,
            "bundle_manifest_readable": False,
            "bundle_manifest_generation_head": None,
            "bundle_generated_digest": None,
            "rejected_marker_present": False,
            "child_work_item_ids": [],
            "functional_review_consumed_blob": None,
            "functional_checklist_evidence": None,
            "pre_work_item_keys": pre_work_item_keys,
        }

    work_item_id = work_item.work_item_id
    phase = work_item.phase

    bundle_dir = evidence.resolve_bundle_dir(root, work_item_id, phase=phase)
    manifest = evidence.read_manifest_fields(root, bundle_dir)
    rejected_present, _detail = evidence.rejected_marker_detail(root, work_item_id)
    child_work_item_ids = sorted(
        wid for wid, view in snapshot.work_items.items()
        if view.parent_work_item_id == work_item_id
    )

    return {
        "phase": phase,
        "governing_workflow_version": work_item.governing_workflow_version,
        "target_head": target_head,
        "state_revision": work_item.state_revision,
        "plan_revision": work_item.plan_revision,
        "implementation_revision": work_item.implementation_revision,
        "last_completed_checkpoint_id": work_item.last_completed_checkpoint_id,
        "checkpoints": work_item.checkpoints,
        "bundle_id": work_item.current_bundle_id,
        "bundle_manifest_readable": manifest["_exists"],
        "bundle_manifest_generation_head": manifest["generation_head"],
        "bundle_generated_digest": _bundle_generated_digest(root, bundle_dir),
        "rejected_marker_present": rejected_present,
        "child_work_item_ids": child_work_item_ids,
        "functional_review_consumed_blob": _functional_review_consumed_blob(root, work_item_id),
        "functional_checklist_evidence": evidence.functional_checklist_evidence(
            root, work_item_id, work_item.base_commit, target_head, work_item.implementation_revision,
        ),
        "pre_work_item_keys": pre_work_item_keys,
    }


# ---------------------------------------------------------------------------
# CP6B -- the `ExpectedOutcome` table (plan section "CP6B -- Job execution,
# part 2", the table transcribed under "An `ExpectedOutcome` is data, not
# prose"). Six rows, one per automatic `(from_phase, governing_workflow_
# version, action)` triple the combined CP4/CP4B decision engine can ever
# produce. `to_any_of` alone drives step 4's `expected_transition`; the
# `predicate`/`predicate_inputs`/`writer_calls` columns are step 8's own
# verification concern.
# ---------------------------------------------------------------------------

WRITER_KIND_COMPLETION = "COMPLETION"
WRITER_KIND_ENTRY = "ENTRY"
WRITER_KIND_CONDITIONAL = "CONDITIONAL"


@dataclasses.dataclass(frozen=True)
class BranchSpec:
    """A located span of one frozen `.claude/commands/<file>.md` file's
    own text -- the plan's own "branch is a span of the file's text"
    (CP6B, "The text model"), narrowed to the two marker forms this
    table's own two branch-bearing rows actually need:

    - ``kind="bullet"``: a ``- `<label>`:`` / ``- <label>:`` dispatch
      bullet (row 3's `BLOCK` verdict) -- located by stripped-line match
      so its own indentation under the enclosing numbered step is never
      significant; ends at the next such bullet, or at the next
      *unindented* top-level numbered step, whichever comes first.
    - ``kind="step"``: a numbered step's own opening marker (``<label>.``
      at column 0) -- row 5's own remedy (this checkpoint's plan text,
      "CP6B cannot pass its own verification gate while row 5 stays
      unresolved": *"restate row 5's own declared branch to point at
      step 5's text directly"*, chosen here over the alternative of
      teaching the model to follow a "steps N-M execute" cross-reference,
      since step 5 already runs unconditionally on both governing-version
      branches -- its own numbered span already contains the declared
      call in full, no cross-reference needed). Ends at the next
      unindented top-level numbered step.
    """

    kind: str
    label: str


@dataclasses.dataclass(frozen=True)
class WriterCall:
    """One declared `(function, file, kind, branch)` fact about a frozen
    command file, plus the literal text `property_declaration_against_
    artifact_violations` searches a located branch's own span for
    (`match_text`, defaulting to ``f"{function}("``) -- CP6B's own
    simplification of the plan's three-form (i)/(ii)/(iii) call-admission
    grammar to a single literal substring search, deliberately: every
    call this table names is either a `workflow_state.<fn>(` invocation or
    a bare backtick-quoted `` `<fn>(...)` ``/script-name occurrence, and a
    literal search for the function's own identifier followed by ``(``
    (or, for row 5's shell-script call, the script's own basename) finds
    it under either form without needing to discriminate which form
    matched."""

    function: str
    file: str
    location: str
    kind: str
    branch: BranchSpec | None = None
    match_text: str | None = None

    def match(self) -> str:
        return self.match_text if self.match_text is not None else f"{self.function}("


PredicateFn = Callable[[Path, str, dict], bool]


@dataclasses.dataclass(frozen=True)
class ExpectedOutcome:
    """One row of the plan's own `ExpectedOutcome` table.

    ``from_phase`` is ``str`` for every row but one: row 7 (the
    `NoWorkItemYet` bootstrap, revision 63's B2) carries the
    :data:`~controller.decision.NO_PHASE` sentinel instead, since there is
    no work item and so no real phase string to name -- never the bare
    string ``"None"`` or Python's own ``None``, which the
    ``governing_version`` wildcard convention already claims."""

    from_phase: "str | Any"
    governing_version: str | None
    action: str
    to_any_of: frozenset[str]
    predicate: PredicateFn | None
    predicate_inputs: frozenset[str]
    writer_calls: tuple[WriterCall, ...]


def _predicate_row3_block_feedback_current(root: Path, work_item_id: str, pre_state: dict) -> bool:
    """Row 3's predicate: a current-round ``REVIEW_FEEDBACK.md`` now
    exists whose ``Reviewed bundle ID:`` matches the bundle the pre-state
    captured, whose ``Reviewer role:`` is ``LOCAL_MODEL_PLAN_REVIEW``, and
    whose ``Status:`` is ``BLOCK`` -- read fresh from disk, never from
    `pre_state` itself (the file did not exist, or held a different
    round's content, before the worker ran)."""
    if not pre_state.get("bundle_manifest_readable"):
        return False
    feedback_dir = evidence.resolve_feedback_dir(root, work_item_id)
    feedback = evidence.read_feedback_fields(root, feedback_dir)
    if feedback is None:
        return False
    return (
        feedback.get("status") == "BLOCK"
        and feedback.get("reviewer_role") == "LOCAL_MODEL_PLAN_REVIEW"
        and feedback.get("reviewed_bundle_id") == pre_state.get("bundle_id")
    )


def _predicate_row5_bundle_regenerated(root: Path, work_item_id: str, pre_state: dict) -> bool:
    """Row 5's predicate: the freshly recomputed `bundle_generated_digest`
    differs from the one `pre_state` captured -- the generator's own
    completed output, never trusted from `MANIFEST.md`'s stale
    `bundle_id` or from the generation-record commit alone (plan section
    "`bundle_generated_digest` -- what row 5 reads")."""
    bundle_dir = evidence.resolve_bundle_dir(root, work_item_id, phase=pre_state["phase"])
    post_digest = _bundle_generated_digest(root, bundle_dir)
    return post_digest != pre_state.get("bundle_generated_digest")


def _predicate_row7_new_work_item_created(root: Path, work_item_id: Any, pre_state: dict) -> bool:
    """Row 7's predicate (revision 63's B2, "Row 7's predicate is the
    plan's own key-set-difference rule, restated as data"): the
    post-snapshot's own `work_items` key set, read fresh from disk through
    the one real reader (`target_state.read`) -- never trusted to a
    captured post-value, the same discipline row 5's own
    `bundle_generated_digest` predicate follows -- differs from
    `pre_state["pre_work_item_keys"]` by exactly one new key. Structurally
    optional (row 7's `to_any_of` never contains `NO_PHASE`, so property
    3 does not require a predicate here), kept anyway for the same
    defence-in-depth reason every predicate-bearing row carries one: a
    `/milestone-plan` that reaches `AWAITING_LOCAL_PLAN_REVIEW` without
    actually creating a new entry must not verify. `work_item_id` is
    unused -- row 7's action never supplies one -- and accepted only to
    keep :data:`PredicateFn`'s signature uniform across every row. Any
    read/validation failure is treated as "the predicate does not hold"
    (fail-closed: never a false verification)."""
    try:
        post_snapshot = target_state.read(SimpleNamespace(root=root))
    except ControllerError:
        return False
    post_keys = frozenset(post_snapshot.work_items)
    pre_keys = frozenset(pre_state.get("pre_work_item_keys") or ())
    return len(post_keys - pre_keys) == 1


#: The seven rows, transcribed verbatim from the plan's own table (CP6B,
#: "An `ExpectedOutcome` is data, not prose"; row 7 added by revision 63's
#: B2, the `NoWorkItemYet` bootstrap).
EXPECTED_OUTCOMES: tuple[ExpectedOutcome, ...] = (
    ExpectedOutcome(
        from_phase="PLANNING", governing_version="2.1", action="/milestone-plan",
        to_any_of=frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("publish_plan_revision", "milestone-plan.md", "milestone-plan.md:202",
                       WRITER_KIND_COMPLETION),
        ),
    ),
    ExpectedOutcome(
        from_phase="PLANNING", governing_version="1", action="/milestone-plan",
        to_any_of=frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("publish_plan_revision", "milestone-plan.md", "milestone-plan.md:202",
                       WRITER_KIND_COMPLETION),
        ),
    ),
    ExpectedOutcome(
        from_phase="AWAITING_LOCAL_PLAN_REVIEW", governing_version="2.1", action="/review-plan",
        to_any_of=frozenset({
            "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW",
        }),
        predicate=_predicate_row3_block_feedback_current,
        predicate_inputs=frozenset({"bundle_id", "bundle_manifest_readable"}),
        writer_calls=(
            WriterCall("record_local_plan_review", "review-plan.md", "review-plan.md:107",
                       WRITER_KIND_COMPLETION, branch=BranchSpec(kind="bullet", label="BLOCK")),
        ),
    ),
    ExpectedOutcome(
        from_phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", governing_version="2.1",
        action="/record-manual-plan-review",
        to_any_of=frozenset({"AWAITING_PLAN_APPROVAL", "REVISING_PLAN"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("record_manual_plan_review", "record-manual-plan-review.md",
                       "record-manual-plan-review.md:96", WRITER_KIND_COMPLETION),
        ),
    ),
    ExpectedOutcome(
        from_phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_version="1", action="/apply-plan-review",
        to_any_of=frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}),
        predicate=_predicate_row5_bundle_regenerated,
        predicate_inputs=frozenset({"bundle_generated_digest"}),
        writer_calls=(
            WriterCall("prepare-ai-review.sh", "apply-plan-review.md", "apply-plan-review.md:125",
                       WRITER_KIND_COMPLETION, branch=BranchSpec(kind="step", label="5"),
                       match_text="prepare-ai-review.sh"),
        ),
    ),
    ExpectedOutcome(
        from_phase="REVISING_PLAN", governing_version="2.1", action="/apply-plan-review",
        to_any_of=frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("transition_to_awaiting_local_plan_review", "apply-plan-review.md",
                       "apply-plan-review.md:145", WRITER_KIND_COMPLETION),
        ),
    ),
    ExpectedOutcome(
        from_phase=NO_PHASE, governing_version=None, action="/milestone-plan",
        to_any_of=frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        predicate=_predicate_row7_new_work_item_created,
        predicate_inputs=frozenset({"pre_work_item_keys"}),
        writer_calls=(
            WriterCall("publish_plan_revision", "milestone-plan.md", "milestone-plan.md:208",
                       WRITER_KIND_COMPLETION),
        ),
    ),
)

_EXPECTED_OUTCOMES_BY_KEY: dict[tuple["str | Any", str | None, str], ExpectedOutcome] = {
    (eo.from_phase, eo.governing_version, eo.action): eo for eo in EXPECTED_OUTCOMES
}


def property_table_violations(
    outcomes: "tuple[ExpectedOutcome, ...] | list[ExpectedOutcome]" = EXPECTED_OUTCOMES,
) -> list[str]:
    """Properties 1, 3 (structural half) and 6 (documented, not
    mechanically checked -- see below) over ``outcomes`` (defaulting to
    :data:`EXPECTED_OUTCOMES`), no filesystem or process needed. Returns a
    list of violation descriptions; empty means the table is well-formed.
    ``outcomes`` is a parameter (rather than always reading the module
    global) so ``tests/test_job_validation.py`` can exercise the negative
    instantiation of every property below against a synthetic, deliberately
    broken row, per the plan's own "a property that cannot fail is not a
    property."

    - **Coverage** (property 1): no two rows share a
      ``(from_phase, governing_version, action)`` triple.
    - **Predicate presence and validity** (property 3, the structural
      half): a row whose ``to_any_of`` contains its own ``from_phase``
      must carry a predicate, and vice versa; every ``writer_calls`` entry
      on a predicate-bearing row must be ``WRITER_KIND_COMPLETION``. The
      converse direction exempts a row whose ``from_phase`` **is**
      :data:`~controller.decision.NO_PHASE` by name -- row 7, revision
      63's B2 -- since that row carries a predicate for an independent
      defence-in-depth reason its own docstring states, not because its
      ``to_any_of`` (which can never contain ``NO_PHASE``: it names real
      Workflow phases only) requires one. The exemption is keyed to
      ``from_phase is NO_PHASE``, never to "fails the containment test",
      so a synthetic non-``NO_PHASE`` row in the same shape still fails
      this property.
    - **Completion** (property 6): asserted narratively against the
      plan's own per-row analysis and exercised by
      ``tests/test_job_validation.py``'s reachability cases (calling
      ``evidence.decide`` from each row's own ``from_phase``/
      ``governing_version``) rather than by a second, independent
      mechanical check here -- "produced when the action completes" vs.
      "producible by the action's writers" is not a distinction this
      table's own data can decide without executing the real command.
    """
    violations: list[str] = []
    seen: set[tuple[str, str | None, str]] = set()
    for eo in outcomes:
        key = (eo.from_phase, eo.governing_version, eo.action)
        if key in seen:
            violations.append(f"duplicate ExpectedOutcome triple {key!r}")
        seen.add(key)

        own_phase_reachable = eo.from_phase in eo.to_any_of
        if own_phase_reachable and eo.predicate is None:
            violations.append(
                f"{key!r}: to_any_of contains its own from_phase but declares no predicate"
            )
        if not own_phase_reachable and eo.from_phase is not NO_PHASE:
            if eo.predicate is not None:
                violations.append(
                    f"{key!r}: predicate declared but to_any_of never contains from_phase"
                )
            if eo.predicate_inputs:
                violations.append(f"{key!r}: predicate_inputs declared with no predicate")

        if eo.predicate is not None:
            for wc in eo.writer_calls:
                if wc.kind != WRITER_KIND_COMPLETION:
                    violations.append(
                        f"{key!r}: predicate-bearing row carries a non-COMPLETION "
                        f"writer_call {wc.function!r} (kind={wc.kind!r})"
                    )
    return violations


def property_record_completeness_violations(
    outcomes: "tuple[ExpectedOutcome, ...] | list[ExpectedOutcome]" = EXPECTED_OUTCOMES,
    pre_state_fields: frozenset[str] = PRE_STATE_FIELDS,
) -> list[str]:
    """Property 4: every ``predicate_inputs`` member, over every row, is a
    member of ``pre_state_fields`` (defaulting to :data:`PRE_STATE_FIELDS`)
    -- a field declared as a predicate input that the pre-state capture
    never gathers would make the predicate unevaluable at `resume` (CP7),
    where the capturing process is long gone and the record is the entire
    input. The companion half of this property -- a captured record's own
    key set equals :data:`PRE_STATE_FIELDS` exactly -- is asserted
    directly against a real ``execute_step`` capture in
    ``tests/test_job.py``'s ``test_planned_flush_carries_and_omits_the_
    right_fields`` (CP6) and restated in ``tests/test_job_validation.py``
    (CP6B's own ownership of the property statement)."""
    union_inputs: set[str] = set()
    for eo in outcomes:
        union_inputs |= eo.predicate_inputs
    missing = union_inputs - pre_state_fields
    return [f"predicate_input {field!r} is not a member of PRE_STATE_FIELDS" for field in sorted(missing)]


def _branch_span(lines: list[str], branch: BranchSpec | None) -> tuple[int, int] | None:
    """Locate ``branch``'s own span in ``lines`` as a half-open
    ``(start, end)`` line-index pair (0-indexed, ``end`` exclusive), or
    ``None`` if its own opening marker cannot be found. ``branch=None``
    is the whole file, end-to-end."""
    if branch is None:
        return (0, len(lines))

    top_level_step = re.compile(r"^\d+[.\']\s")
    if branch.kind == "bullet":
        marker = re.compile(rf"^-\s+`?{re.escape(branch.label)}`?:")
        bullet_sibling = re.compile(r"^-\s+`?[A-Za-z_]+`?:")
    elif branch.kind == "step":
        marker = re.compile(rf"^{re.escape(branch.label)}\.\s")
    else:
        raise AssertionError(f"unknown BranchSpec.kind {branch.kind!r}")

    start = None
    for i, raw in enumerate(lines):
        probe = raw.strip() if branch.kind == "bullet" else raw
        if marker.match(probe):
            start = i
            break
    if start is None:
        return None

    end = len(lines)
    for j in range(start + 1, len(lines)):
        raw = lines[j]
        if branch.kind == "bullet" and bullet_sibling.match(raw.strip()):
            end = j
            break
        if top_level_step.match(raw):
            end = j
            break
    return (start, end)


_FURTHER_WRITE_RE = re.compile(r"workflow_state\.([A-Za-z_][A-Za-z0-9_]*)\(")


def property_declaration_against_artifact_violations(
    repo_root: Path,
    outcomes: "tuple[ExpectedOutcome, ...] | list[ExpectedOutcome]" = EXPECTED_OUTCOMES,
) -> list[str]:
    """Property 5: for every ``outcomes`` row's every ``WriterCall``, the
    declared branch is locatable in the frozen ``.claude/commands/
    <file>.md``, the declared call is found within that branch's own
    span, and no *different* durable write (``workflow_state.<writer>(``,
    other than a repeated call to the same declared function) occurs
    later in plain file order within that same span. ``repo_root`` is the
    Controller's own checkout (this repository is itself a frozen
    Workflow v2.3.1 installation, and its fifteen command files are the
    same external artifact a target managed repository carries -- see
    ``tests/fixtures.copy_real_commands_dir``'s own docstring)."""
    violations: list[str] = []
    for eo in outcomes:
        key = (eo.from_phase, eo.governing_version, eo.action)
        for wc in eo.writer_calls:
            path = repo_root / ".claude" / "commands" / wc.file
            try:
                text = path.read_text()
            except OSError as exc:
                violations.append(f"{key!r} ({wc.function!r}): {wc.file} could not be read: {exc}")
                continue
            lines = text.splitlines()
            span = _branch_span(lines, wc.branch)
            if span is None:
                violations.append(
                    f"{key!r} ({wc.function!r}): declared branch {wc.branch!r} not found in {wc.file}"
                )
                continue
            start, end = span
            located = None
            for i in range(start, end):
                if wc.match() in lines[i]:
                    located = i
                    break
            if located is None:
                violations.append(
                    f"{key!r} ({wc.function!r}): no call found in {wc.file}'s declared branch "
                    f"(lines {start + 1}-{end})"
                )
                continue
            for i in range(located + 1, end):
                for m in _FURTHER_WRITE_RE.finditer(lines[i]):
                    if m.group(1) != wc.function:
                        violations.append(
                            f"{key!r} ({wc.function!r}): a further durable write to "
                            f"{m.group(1)!r} occurs after it at {wc.file}:{i + 1}, within the "
                            f"same declared branch"
                        )
    return violations


# ---------------------------------------------------------------------------
# expected_transition (step 4's LAUNCHED-only addition).
# ---------------------------------------------------------------------------


def _expected_outcome_for(phase: Any, governing_workflow_version: str | None, decision: Decision) -> ExpectedOutcome:
    """The single :data:`EXPECTED_OUTCOMES` row an automatic ``decision``
    about to be launched corresponds to. The combined CP4/CP4B decision
    engine can only ever produce an automatic ``Decision`` for one of
    these seven triples (row 7's own `from_phase` is
    :data:`~controller.decision.NO_PHASE`, the `NoWorkItemYet` bootstrap),
    so a miss here is an invariant violation, never an ordinary
    control-flow path (the same shape as ``cli._reexec``'s own
    ``AssertionError`` after ``os.execve``). Takes ``phase``/
    ``governing_workflow_version`` explicitly, rather than a ``work_item``
    object, because a `NoWorkItemYet` target has no ``WorkItemView`` to
    read either off of."""
    command_token = decision.action.command.split()[0]
    key = (phase, governing_workflow_version, command_token)
    outcome = _EXPECTED_OUTCOMES_BY_KEY.get(key)
    if outcome is None:
        raise AssertionError(
            f"decide() produced an automatic action with no known expected transition: "
            f"phase={phase!r} governing_workflow_version="
            f"{governing_workflow_version!r} command={decision.action.command!r}"
        )
    return outcome


def _expected_transition(phase: Any, governing_workflow_version: str | None, decision: Decision) -> dict:
    """``{"from": <phase>, "to_any_of": [...]}`` for the automatic
    ``decision`` about to be launched (step 4's own ``LAUNCHED``-only
    addition). ``from`` is written through :func:`~controller.decision.
    phase_to_wire`, the single declared writer every "no phase" site uses,
    so row 7's record reads ``{"from": "__NO_PHASE__", ...}`` on disk
    rather than failing to serialise the in-memory sentinel at all."""
    outcome = _expected_outcome_for(phase, governing_workflow_version, decision)
    return {"from": phase_to_wire(phase), "to_any_of": sorted(outcome.to_any_of)}


# ---------------------------------------------------------------------------
# CP6B -- steps 7-9: fresh post-state re-read and verification.
# ---------------------------------------------------------------------------

#: The first clause of step 8's rule (CP6B, "The verification rule, stated
#: in full and in one form"), **stated positively** -- total by
#: construction: an unrecognised `worker_outcome` fails it wherever it
#: appears, so a fifth outcome a later generation adds is non-verifying by
#: default rather than admitted by a negative guard's own hole.
_VERIFYING_WORKER_OUTCOMES = frozenset({"SUCCESS", "INTERRUPTED"})

#: Landing at a phase that is a legal *effect* of an action but never a
#: *completion* of it -- checked before step 8's own "otherwise" clause,
#: which it takes precedence over. Empty after revision 10's narrowing
#: (plan section "CP6B -- Job execution, part 2": "no Generation 1 action
#: can produce it" -- `/apply-implementation-review` reaching
#: `APPLYING_REVIEW_FEEDBACK` was its only producer, and that action is
#: not one of :data:`EXPECTED_OUTCOMES`' own seven rows). Kept as a per-row
#: mapping, not deleted, because a record written by a *future* generation
#: that does drive such a landing must still be classifiable by `resume`
#: (CP7)'s own closed status table.
_INCOMPLETE_EFFECT_PHASES: dict[tuple[str, str | None, str], frozenset[str]] = {}


def _verify_transition(
    *, root: Path, work_item_id: str, outcome: ExpectedOutcome, pre_state: dict,
    observed_phase_after: str, worker_outcome: str,
) -> tuple[bool, dict]:
    """Step 8's rule, stated in full and in one form (CP6B): ``verified``
    is ``True`` iff ``worker_outcome`` is ``SUCCESS`` or ``INTERRUPTED``,
    **and** ``observed_phase_after`` is in ``outcome.to_any_of``, **and**,
    when ``observed_phase_after`` equals ``outcome.from_phase``, the row's
    own predicate holds against ``pre_state`` (evaluated fresh, against
    disk -- never against process memory, since `resume` has none).
    Returns ``(verified, evidence)`` -- ``evidence`` is a
    ``TransitionNotObservedError``-shaped dict naming the expected set,
    the observed phase, the worker outcome, and (for the predicate clause)
    that the predicate did not hold; ``{}`` when ``verified``."""
    outcome_ok = worker_outcome in _VERIFYING_WORKER_OUTCOMES
    predicate_checked = False
    predicate_ok = True
    if outcome_ok and observed_phase_after in outcome.to_any_of:
        if observed_phase_after == outcome.from_phase:
            predicate_checked = True
            predicate_ok = outcome.predicate is not None and outcome.predicate(
                root, work_item_id, pre_state
            )
        if predicate_ok:
            return True, {}

    reason = (
        "worker_outcome" if not outcome_ok
        else "predicate_not_satisfied" if predicate_checked
        else "phase_not_in_to_any_of"
    )
    return False, {
        "code": "TransitionNotObservedError",
        "reason": reason,
        "expected_to_any_of": sorted(outcome.to_any_of),
        "observed_phase": observed_phase_after,
        "worker_outcome": worker_outcome,
    }


# ---------------------------------------------------------------------------
# CP7 -- durable resume (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP7
# -- Durable resume"). `resume` loads every job record this Controller
# generation ever wrote for one target repository and reconciles each
# non-terminal one against authoritative Workflow/Git state -- never the
# Controller's own optimism, and never by relaunching a worker: this
# module's only worker-launch call site is `execute_step`'s own step 5,
# which `resume` never calls.
# ---------------------------------------------------------------------------

#: The three non-terminal statuses `execute_step` can leave a record at if
#: the process dies mid-job -- the only members `resume`'s closed
#: reconciliation table actually judges.
NON_TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_PLANNED, STATUS_LAUNCHED, STATUS_COMPLETED})

#: The remaining seven members of the closed, ten-member enumeration --
#: every one of `execute_step`'s own terminal outcomes, plus `resume`'s own
#: `INTERRUPTED` write. A terminal record is reported, never reconciled.
TERMINAL_STATUSES: frozenset[str] = frozenset({
    STATUS_FINISHED, STATUS_FAILED, STATUS_INTERRUPTED, STATUS_INCOMPLETE,
    STATUS_GATE_BLOCKED, STATUS_DECLINED, STATUS_HANDOFF_PENDING,
})

#: The worker outcomes a `COMPLETED` record's own `worker_outcome` must be
#: one of to pass validation-pass case 3 -- the same closed four-member set
#: `controller.worker.launch` itself ever classifies into.
_KNOWN_WORKER_OUTCOMES: frozenset[str] = frozenset({"SUCCESS", "FAILURE", "AMBIGUOUS", "INTERRUPTED"})


@dataclasses.dataclass(frozen=True)
class Validity:
    """The result of one :func:`validate_record` call -- the plan's own
    named ``Validity`` return type. ``valid=True`` carries nothing else.
    An invalid result carries ``terminal`` (whether :func:`resume` should
    raise ``StaleJobRecordError`` or surface the record marked, per round
    8's I2 carve-out -- decided from the record's own ``status``, never
    guessed), ``case`` (which of the four validation-pass cases fired, for
    tests and diagnostics), and a ``code``/``reason``/``message``/
    ``evidence`` describing why. :func:`validate_record` itself never
    raises -- :func:`resume` is the sole place a refusal becomes an
    exception, which is what lets every case be asserted directly against
    a plain returned value."""

    valid: bool
    terminal: bool = False
    case: int | None = None
    code: str = ""
    reason: str = ""
    message: str = ""
    evidence: dict = dataclasses.field(default_factory=dict)


#: The single shared "nothing wrong" result -- reused rather than
#: reconstructed, so ``validate_record(record, ...) == VALID`` is a plain
#: dataclass equality check in tests.
VALID = Validity(valid=True)


def _record_status_is_terminal(record: JobRecord) -> bool:
    """The carve-out's own terminality read, over the record's own
    ``status`` -- never over which validation case fired. A status this
    generation does not recognise (or an absent one) is treated as
    **non-terminal**, the fail-closed direction round 19's I2 chose: the
    carve-out's premise (protecting *this generation's own history*) does
    not hold for a record whose status this generation cannot even read."""
    return record.get("status") in TERMINAL_STATUSES


def _invalid(record: JobRecord, *, case: int, code: str, reason: str, message: str, evidence: dict) -> Validity:
    return Validity(
        valid=False, terminal=_record_status_is_terminal(record), case=case,
        code=code, reason=reason, message=message, evidence=evidence,
    )


def validate_record(record: JobRecord, *, managed_repo: Any, identity: Any) -> Validity:
    """The named validation pass CP7's own plan text states: four cases,
    in a stated order, each answering *"can this record be believed?"*
    Every case that fails returns an invalid :class:`Validity` -- never
    raises; :func:`resume` alone turns an invalid, non-terminal result
    into a raised :class:`~controller.errors.StaleJobRecordError`, per the
    carve-out :func:`_record_status_is_terminal` reads.

    ``managed_repo`` and ``identity`` are needed for case 2 (does the
    record's own target still resolve, and does its ``work_item_id``
    still exist there) and case 1's own ``controller_generation``
    comparison respectively -- the plan's own shorthand signature
    ``validate_record(record) -> Validity`` elides both, since neither is
    optional in a real implementation of case 1 or case 2.

    Case 1 and case 2 read no field this repository's target could not
    have written by accident; none of the four cases reads the target's
    **post-state** (that is what the reconciliation table below is for) --
    case 2 is the only one that touches the target at all, and only to ask
    whether it and its work item still exist.
    """
    # Case 1: uninterpretable record.
    schema_version = record.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        return _invalid(
            record, case=1, code="StaleJobRecordError", reason="unknown_schema_version",
            message=(
                f"job {record.get('job_id')!r} declares schema_version={schema_version!r}, "
                f"which this generation does not recognise (expected {SCHEMA_VERSION!r})"
            ),
            evidence={"job_id": record.get("job_id"), "schema_version": schema_version},
        )
    record_generation = record.get("controller_generation")
    running_generation = identity.generation
    if (
        isinstance(record_generation, int) and isinstance(running_generation, int)
        and record_generation > running_generation
    ):
        return _invalid(
            record, case=1, code="StaleJobRecordError", reason="newer_controller_generation",
            message=(
                f"job {record.get('job_id')!r} was written by controller_generation="
                f"{record_generation!r}, which is newer than this generation's own "
                f"{running_generation!r} -- possibly-live work belonging to a generation "
                f"that supersedes this one"
            ),
            evidence={
                "job_id": record.get("job_id"), "record_generation": record_generation,
                "running_generation": running_generation,
            },
        )

    # Case 2: unresolvable subject. The loader (`resume`) has already
    # confined `record` to this call's own `managed_repo` (its own
    # `target_repo` field equals `str(managed_repo.root)`, by construction
    # -- see `resume`'s own docstring), so "does target_repo resolve" is a
    # fresh, live re-check of that same directory, never a re-parse of the
    # record's own string.
    if not managed_repo.root.is_dir():
        return _invalid(
            record, case=2, code="StaleJobRecordError", reason="target_repo_unresolvable",
            message=(
                f"job {record.get('job_id')!r} names target_repo "
                f"{record.get('target_repo')!r}, which no longer resolves to a directory"
            ),
            evidence={"job_id": record.get("job_id"), "target_repo": record.get("target_repo")},
        )
    work_item_id = record.get("work_item_id")
    try:
        snapshot = target_state.read(managed_repo)
    except ControllerError as exc:
        # Any of target_state.read's own named refusals (missing/malformed
        # WORKFLOW_STATE.json, an unknown phase, a malformed registry) --
        # never a bare `Exception` catch, which would also swallow a real
        # programming error here rather than surfacing it as evidence.
        return _invalid(
            record, case=2, code="StaleJobRecordError", reason="target_state_unreadable",
            message=(
                f"job {record.get('job_id')!r}'s target state at {managed_repo.root} could not "
                f"be read: {exc}"
            ),
            evidence={"job_id": record.get("job_id"), "target_repo": record.get("target_repo"),
                      "error": str(exc)},
        )
    if work_item_id not in snapshot.work_items:
        return _invalid(
            record, case=2, code="StaleJobRecordError", reason="work_item_absent",
            message=(
                f"job {record.get('job_id')!r} names work_item_id {work_item_id!r}, which is "
                f"absent from {managed_repo.root}'s current Workflow state"
            ),
            evidence={"job_id": record.get("job_id"), "work_item_id": work_item_id},
        )

    # Case 3: worker_outcome disagrees with the step that owns the
    # record's status -- stated over the three named non-terminal
    # statuses, one rule each, keyed on presence/membership rather than on
    # a class test, so an unrecognised status simply is not one of the
    # three and falls through to case 4 untouched.
    status = record.get("status")
    if status in (STATUS_PLANNED, STATUS_LAUNCHED):
        if "worker_outcome" in record:
            return _invalid(
                record, case=3, code="StaleJobRecordError", reason="worker_outcome_present",
                message=(
                    f"job {record.get('job_id')!r} is {status!r} but carries a worker_outcome "
                    f"({record.get('worker_outcome')!r}) -- {status} is written before the "
                    f"worker exists and never carries one"
                ),
                evidence={"job_id": record.get("job_id"), "status": status,
                          "worker_outcome": record.get("worker_outcome")},
            )
    elif status == STATUS_COMPLETED:
        worker_outcome = record.get("worker_outcome")
        if worker_outcome not in _KNOWN_WORKER_OUTCOMES:
            return _invalid(
                record, case=3, code="StaleJobRecordError", reason="worker_outcome_invalid",
                message=(
                    f"job {record.get('job_id')!r} is COMPLETED but its worker_outcome "
                    f"{worker_outcome!r} is absent or outside {sorted(_KNOWN_WORKER_OUTCOMES)}"
                ),
                evidence={"job_id": record.get("job_id"), "status": status,
                          "worker_outcome": worker_outcome},
            )

    # Case 4: derived-field disagreement. `status` is derived from
    # `selected_action.declined` at record-write time; ranges only over
    # the enumeration's own (recognised) members -- an unrecognised status
    # has no terminality this generation can determine and falls through
    # unjudged to the reconciliation table's own unknown-status row.
    declined = (record.get("selected_action") or {}).get("declined")
    if status == STATUS_DECLINED and declined is not True:
        return _invalid(
            record, case=4, code="StaleJobRecordError", reason="declined_status_without_flag",
            message=(
                f"job {record.get('job_id')!r} is DECLINED but selected_action.declined is "
                f"{declined!r}, not True"
            ),
            evidence={"job_id": record.get("job_id"), "status": status, "declined": declined},
        )
    if status in TERMINAL_STATUSES | NON_TERMINAL_STATUSES and status != STATUS_DECLINED and declined is True:
        return _invalid(
            record, case=4, code="StaleJobRecordError", reason="declined_flag_without_status",
            message=(
                f"job {record.get('job_id')!r} carries selected_action.declined=True but its "
                f"status is {status!r}, not DECLINED"
            ),
            evidence={"job_id": record.get("job_id"), "status": status, "declined": declined},
        )

    return VALID


def _expected_outcome_for_record(record: JobRecord) -> ExpectedOutcome:
    """The single :data:`EXPECTED_OUTCOMES` row a ``LAUNCHED``/``COMPLETED``
    record's own ``pre_state`` and ``selected_action`` correspond to -- the
    resume-time mirror of :func:`_expected_outcome_for`, reading from a
    persisted record instead of a live ``work_item``/``Decision`` pair. A
    miss here is an invariant violation, never ordinary control flow: by
    the time :func:`resume` calls this, :func:`validate_record` has
    already confirmed the record is a genuine Generation-1 write (case 1's
    ``controller_generation`` check), and `execute_step` never launches a
    worker for any key outside this table."""
    pre_state = record.get("pre_state") or {}
    selected_action = record.get("selected_action") or {}
    command = selected_action.get("command")
    command_token = command.split()[0] if isinstance(command, str) and command else None
    key = (pre_state.get("phase"), pre_state.get("governing_workflow_version"), command_token)
    outcome = _EXPECTED_OUTCOMES_BY_KEY.get(key)
    if outcome is None:
        raise AssertionError(
            f"resume reached a non-terminal record with no known expected transition: "
            f"job_id={record.get('job_id')!r} key={key!r}"
        )
    return outcome


def _row2_verified(
    *, root: Path, work_item_id: str, outcome: ExpectedOutcome, pre_state: dict,
    observed_phase_after: str, status: str, worker_outcome: str | None,
) -> tuple[bool, str | None]:
    """The ``LAUNCHED``/``COMPLETED`` row's own rule (CP7's reconciliation
    table, row 2), stated once for both statuses. ``worker_outcome`` is
    guaranteed absent on a ``LAUNCHED`` record and present-and-known on a
    ``COMPLETED`` one by the time this runs (:func:`validate_record`'s own
    case 3), so a ``LAUNCHED`` record's own clause-1 is always satisfied --
    stated positively here rather than by substituting a fake outcome
    value, so the returned reason (when unverified) never misreports what
    the record actually carried. Returns ``(verified, reason)`` --
    ``reason`` is one of ``"worker_outcome"``, ``"phase_not_in_to_any_of"``
    or ``"predicate_not_satisfied"``, ``None`` when verified."""
    if status == STATUS_COMPLETED:
        outcome_ok = worker_outcome in _VERIFYING_WORKER_OUTCOMES
    else:
        outcome_ok = True  # STATUS_LAUNCHED, validated absent by case 3.
    if not outcome_ok:
        return False, "worker_outcome"
    if observed_phase_after not in outcome.to_any_of:
        return False, "phase_not_in_to_any_of"
    if observed_phase_after == outcome.from_phase:
        if outcome.predicate is None or not outcome.predicate(root, work_item_id, pre_state):
            return False, "predicate_not_satisfied"
    return True, None


def _reconcile_planned(record: JobRecord, *, runtime_root: Path) -> JobRecord:
    """Row 1: ``PLANNED``, anything observed -- nothing can have happened,
    since the record was flushed before the launch was even prepared.
    Mark ``INTERRUPTED`` and allow a fresh ``step``. No observed reality is
    read at all (there is none to read: no ``expected_transition`` was
    ever written for this record)."""
    now = _now()
    reconciled = {**record, "status": STATUS_INTERRUPTED, "reconciled_at": now, "updated_at": now}
    return _persist(runtime_root, record["job_id"], reconciled)


def _reconcile_launched(record: JobRecord, *, managed_repo: Any, runtime_root: Path) -> JobRecord:
    """Rows 2-4: ``LAUNCHED``. A fresh post-state re-read (never the
    ``pre_state`` this record itself captured) decides between "already
    succeeded" (row 2, never relaunch), "nothing durable happened" (row 3,
    ``INTERRUPTED``, a fresh ``step`` may retry), and "cannot be
    reconciled" (row 4, :class:`~controller.errors.UnreconcilableJobError`,
    fail closed and require a human)."""
    root = managed_repo.root
    work_item_id = record["work_item_id"]
    pre_state = record.get("pre_state") or {}
    outcome = _expected_outcome_for_record(record)

    post_snapshot = target_state.read(managed_repo)
    post_work_item = target_state.select_work_item(post_snapshot, work_item_id=work_item_id)
    observed_phase_after = post_work_item.phase
    observed_head = _current_head(root)

    verified, _reason = _row2_verified(
        root=root, work_item_id=work_item_id, outcome=outcome, pre_state=pre_state,
        observed_phase_after=observed_phase_after, status=STATUS_LAUNCHED, worker_outcome=None,
    )
    now = _now()
    if verified:
        reconciled = {
            **record, "status": STATUS_FINISHED, "transition_verified": True,
            "observed_phase_after": observed_phase_after, "reconciled_at": now, "updated_at": now,
        }
        return _persist(runtime_root, record["job_id"], reconciled)

    phase_unchanged = observed_phase_after == pre_state.get("phase")
    head_unchanged = observed_head == pre_state.get("target_head")
    if phase_unchanged and head_unchanged:
        reconciled = {
            **record, "status": STATUS_INTERRUPTED, "observed_phase_after": observed_phase_after,
            "reconciled_at": now, "updated_at": now,
        }
        return _persist(runtime_root, record["job_id"], reconciled)

    raise UnreconcilableJobError(
        f"job {record['job_id']!r} for {work_item_id!r} cannot be reconciled: pre-phase "
        f"{pre_state.get('phase')!r} -> observed {observed_phase_after!r}, pre-state target_head "
        f"{pre_state.get('target_head')!r} -> observed {observed_head!r}",
        evidence={
            "job_id": record["job_id"], "work_item_id": work_item_id,
            "pre_phase": pre_state.get("phase"), "observed_phase_after": observed_phase_after,
            "pre_target_head": pre_state.get("target_head"), "observed_target_head": observed_head,
        },
    )


def _reconcile_completed(record: JobRecord, *, managed_repo: Any, runtime_root: Path) -> JobRecord:
    """Row 2 (verifying) / row 5 (failing): ``COMPLETED``. Mirrors CP6B
    step 8/9's own verification rule exactly -- "exactly as CP6B step 8
    would have" -- against a fresh post-state re-read, never the
    long-gone process's own memory."""
    root = managed_repo.root
    work_item_id = record["work_item_id"]
    pre_state = record.get("pre_state") or {}
    worker_outcome = record.get("worker_outcome")
    outcome = _expected_outcome_for_record(record)

    post_snapshot = target_state.read(managed_repo)
    post_work_item = target_state.select_work_item(post_snapshot, work_item_id=work_item_id)
    observed_phase_after = post_work_item.phase

    verified, reason = _row2_verified(
        root=root, work_item_id=work_item_id, outcome=outcome, pre_state=pre_state,
        observed_phase_after=observed_phase_after, status=STATUS_COMPLETED, worker_outcome=worker_outcome,
    )
    now = _now()
    if verified:
        reconciled = {
            **record, "status": STATUS_FINISHED, "transition_verified": True,
            "observed_phase_after": observed_phase_after, "reconciled_at": now, "updated_at": now,
        }
        return _persist(runtime_root, record["job_id"], reconciled)

    reconciled = {
        **record,
        "status": STATUS_FAILED,
        "transition_verified": False,
        "observed_phase_after": observed_phase_after,
        "reconciliation_evidence": {
            "code": "TransitionNotObservedError",
            "reason": reason,
            "expected_to_any_of": sorted(outcome.to_any_of),
            "observed_phase": observed_phase_after,
            "worker_outcome": worker_outcome,
        },
        "reconciled_at": now,
        "updated_at": now,
    }
    return _persist(runtime_root, record["job_id"], reconciled)


def resume(managed_repo: Any, *, identity: Any, runtime: Path) -> list[JobRecord]:
    """Restart reconciliation: load every job record this Controller ever
    wrote for ``managed_repo`` (``<runtime>/jobs/*.json`` whose own
    ``target_repo`` equals ``str(managed_repo.root)`` -- the runtime root
    is resolved from the Controller's own origin checkout, not from the
    target, so one runtime root can carry jobs for more than one target
    repository over time; this is the "for this target repository" load
    scope), and reconcile each **non-terminal** one against authoritative
    Workflow/Git state. Terminal records are reported, never reconciled.

    Never relaunches a worker: this function contains no call to
    ``controller.worker.launch`` at all -- ``execute_step``'s own step 5 is
    the package's sole launch site, and ``resume`` never calls it,
    structurally, not by a runtime guard.

    Raises :class:`~controller.errors.StaleJobRecordError` (aborting the
    whole call) for the first record that fails :func:`validate_record`
    while its own status is non-terminal, or whose (valid) non-terminal
    status is outside the closed reconciliation table's enumeration.
    Raises :class:`~controller.errors.UnreconcilableJobError` for a
    ``LAUNCHED`` record whose observed target state the table's own row 4
    covers. A **terminal** record failing :func:`validate_record` is never
    raised for -- it is returned in the result, augmented with a
    ``resume_marked`` block naming why (``outcome`` is ``"malformed"`` for
    a case-4 failure, ``"unreadable"`` for case 1 or 2) -- the record on
    disk is never rewritten (a terminal record is history, and one bad
    file from an old generation must not stop the reconciliation of live
    work beside it)."""
    root = managed_repo.root
    target_repo_str = str(root)
    jobs_dir = runtime / "jobs"
    paths = sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []

    results: list[JobRecord] = []
    for path in paths:
        try:
            record = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StaleJobRecordError(
                f"job record at {path} could not be read or parsed as JSON: {exc}",
                evidence={"path": str(path), "error": str(exc)},
            ) from exc
        if not isinstance(record, dict) or record.get("target_repo") != target_repo_str:
            continue  # not "for this target repository" -- left untouched.

        validity = validate_record(record, managed_repo=managed_repo, identity=identity)
        if not validity.valid:
            if validity.terminal:
                outcome_word = "malformed" if validity.case == 4 else "unreadable"
                results.append({
                    **record,
                    "resume_marked": {
                        "outcome": outcome_word, "case": validity.case, "code": validity.code,
                        "reason": validity.reason, "message": validity.message,
                    },
                })
                continue
            raise StaleJobRecordError(validity.message, evidence=validity.evidence)

        status = record.get("status")
        if status in TERMINAL_STATUSES:
            results.append(record)  # reported, never reconciled, never relaunched.
        elif status == STATUS_PLANNED:
            results.append(_reconcile_planned(record, runtime_root=runtime))
        elif status == STATUS_LAUNCHED:
            results.append(_reconcile_launched(record, managed_repo=managed_repo, runtime_root=runtime))
        elif status == STATUS_COMPLETED:
            results.append(_reconcile_completed(record, managed_repo=managed_repo, runtime_root=runtime))
        else:
            # The closed table's own last row: a status outside the
            # ten-member enumeration entirely (or absent) is dispatched as
            # non-terminal -- validate_record's own cases could not judge
            # its terminality either, so this is what judges it.
            raise StaleJobRecordError(
                f"job {record.get('job_id')!r} has status {status!r}, which is outside the "
                f"closed job-status enumeration this generation recognises",
                evidence={"job_id": record.get("job_id"), "status": status},
            )
    return results


# ---------------------------------------------------------------------------
# Record assembly.
# ---------------------------------------------------------------------------


def _new_job_id() -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(4)}"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _selected_action_dict(decision: Decision) -> dict:
    return {
        "kind": "slash_command",
        "command": decision.action.command if decision.action is not None else None,
        "automatic": decision.automatic,
        "declined": decision.declined,
        "reason": decision.reason,
        "evidence": list(decision.evidence),
    }


def _human_gate_dict(gate: Any) -> dict:
    return {
        "repository": gate.repository,
        "work_item_id": gate.work_item_id,
        "phase": gate.phase,
        "what_is_required": gate.what_is_required,
        "artifact_path": gate.artifact_path,
        "safe_resume_command": gate.safe_resume_command,
    }


def _identity_block(
    managed_repo: Any, ident: Any, work_item_id: str | None, observed_phase: Any, job_id: str,
) -> dict:
    """``work_item_id``/``observed_phase`` are accepted explicitly, rather
    than derived from a ``work_item`` object, because a `NoWorkItemYet`
    target (row 7, revision 63's B2) has neither: its record's
    ``work_item_id`` is the declared literal ``None`` (never a name the
    Controller chose -- frozen Workflow alone derives one), and
    ``observed_phase_before`` is written from :attr:`Decision.observed_phase`
    through :func:`~controller.decision.phase_to_wire`, so it carries
    ``"__NO_PHASE__"`` for that one row and every real phase's own name
    otherwise -- one writer, no second representation."""
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "controller_generation": ident.generation,
        "controller_source_commit": ident.source_commit,
        "controller_source_tree_digest": ident.tree_digest,
        "target_repo": str(managed_repo.root),
        "target_workflow_version": managed_repo.workflow_version,
        "work_item_id": work_item_id,
        "observed_phase_before": phase_to_wire(observed_phase),
    }


def _durable_pre_state(pre_state: dict) -> dict:
    """``pre_state``'s own JSON-durable form: ``phase`` written through
    :func:`~controller.decision.phase_to_wire` -- the same single writer
    ``observed_phase_before`` and ``expected_transition.from`` go through
    -- every other field unchanged. The in-memory ``pre_state`` a caller
    already holds keeps the :data:`~controller.decision.NO_PHASE` sentinel
    (`_capture_pre_state`'s own contract); only the copy embedded in a
    persisted record is mapped, here, once, so every one of the record's
    "no phase" sites agrees on the same wire literal."""
    return {**pre_state, "phase": phase_to_wire(pre_state["phase"])}


def _worker_dict(result: worker.WorkerResult, *, stdout_path: str, stderr_path: str) -> dict:
    result_excerpt = result.result
    if isinstance(result_excerpt, str) and len(result_excerpt) > 4000:
        result_excerpt = result_excerpt[:4000]
    return {
        "session_id": result.session_id,
        "exit_code": result.returncode,
        "is_error": result.is_error,
        "subtype": result.subtype,
        "num_turns": result.num_turns,
        "duration_ms": result.duration_ms,
        "total_cost_usd": result.total_cost_usd,
        "permission_denials": result.permission_denials,
        "terminal_reason": result.terminal_reason,
        "stop_reason": result.stop_reason,
        "result_excerpt": result_excerpt,
        "stdout_path": stdout_path,
        "stderr_path": stderr_path,
    }


# ---------------------------------------------------------------------------
# I/O -- defined here (module scope), never inside `execute_step`, so the
# real `controller.runtime` import is always what they call. See this
# module's own docstring, "A deliberate name shadow".
# ---------------------------------------------------------------------------


def _persist(runtime_root: Path, job_id: str, record: JobRecord) -> JobRecord:
    runtime.write_json(runtime_root, f"jobs/{job_id}.json", record)
    return record


def _pending_handoff(runtime_root: Path) -> dict | None:
    return runtime.read_json(runtime_root / "handoff.json")


def _write_worker_streams(runtime_root: Path, job_id: str, result: worker.WorkerResult) -> tuple[str, str]:
    stdout_full = runtime.write_bytes(
        runtime_root, f"jobs/{job_id}/worker.stdout", result.stdout.encode("utf-8", errors="replace"),
    )
    stderr_full = runtime.write_bytes(
        runtime_root, f"jobs/{job_id}/worker.stderr", result.stderr.encode("utf-8", errors="replace"),
    )
    return str(stdout_full), str(stderr_full)


def _no_launch_record(
    runtime_root: Path, managed_repo: Any, ident: Any, work_item_id: str | None, observed_phase: Any,
    pre_state: dict, decision: Decision, *, status: str,
) -> JobRecord:
    """The single-flush record for every outcome that never reaches a
    worker launch: ``GATE_BLOCKED``, ``DECLINED``, ``HANDOFF_PENDING``."""
    job_id = _new_job_id()
    now = _now()
    record: JobRecord = {
        **_identity_block(managed_repo, ident, work_item_id, observed_phase, job_id),
        "pre_state": _durable_pre_state(pre_state),
        "selected_action": _selected_action_dict(decision),
        "status": status,
        "human_gate_pending": (
            _human_gate_dict(decision.gate) if status == STATUS_GATE_BLOCKED else None
        ),
        "handoff_pending": status == STATUS_HANDOFF_PENDING,
        "created_at": now,
        "updated_at": now,
    }
    return _persist(runtime_root, job_id, record)


# ---------------------------------------------------------------------------
# execute_step -- CP6 owns steps 1-6, CP6B extends it with steps 7-9.
# ---------------------------------------------------------------------------


def execute_step(
    managed_repo: Any,
    *,
    work_item_id: str | None = None,
    identity: Any,
    runtime: Path,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    timeout: float | None = None,
    claude_bin: str | None = None,
) -> JobRecord | Decision:
    """Execute (at most) one Controller job against ``managed_repo``.

    ``identity`` is an already-resolved
    ``controller.identity.ControllerIdentity`` (typically
    ``controller.identity.current()``, resolved once by the caller);
    ``runtime`` is the resolved runtime-root ``Path``. Both are accepted
    pre-resolved, per the plan's own declared signature, rather than
    re-derived here.

    Returns a :class:`~controller.decision.Decision` for the no-action
    class (``LEGACY_READY``, ``MILESTONE_COMPLETE``) -- nothing ran, no
    gate is open, nothing was declined, so no job record is written at
    all and the jobs directory is left unchanged. Every other reachable
    outcome returns a :data:`JobRecord` (a plain, JSON-serialisable
    ``dict`` -- exactly what was persisted): ``GATE_BLOCKED``,
    ``DECLINED`` or ``HANDOFF_PENDING`` for the no-launch class, or --
    once a worker actually launches -- one of ``FINISHED`` (step 8's
    verification rule held), ``FAILED`` (it did not, carrying
    ``reconciliation_evidence``), or ``INCOMPLETE`` (step 9: a legal
    effect of the action that is never its completion -- unreachable for
    every one of Generation 1's own seven automatic actions, see
    :data:`_INCOMPLETE_EFFECT_PHASES`). ``COMPLETED`` is never this
    function's own return value; it is an intermediate, durable flush
    step 6 always makes before steps 7-9 run.
    """
    # Step 1: inspect the repository and read Workflow state (already done
    # by the caller producing `managed_repo`); read Workflow state and
    # capture the pre-state. `work_item` is `target_state.NoWorkItemYet`
    # (revision 63's B2, row 7) when zero non-terminal work items exist
    # and none was explicitly named -- there is no `WorkItemView` in that
    # case, so `work_item_id`/`governing_workflow_version` are resolved
    # here, once, rather than re-derived at every later site that would
    # otherwise read them straight off `work_item`.
    snapshot = target_state.read(managed_repo)
    work_item = target_state.select_work_item(snapshot, work_item_id=work_item_id)
    is_bootstrap = work_item is target_state.NoWorkItemYet
    resolved_work_item_id = None if is_bootstrap else work_item.work_item_id
    governing_workflow_version = None if is_bootstrap else work_item.governing_workflow_version
    pre_state = _capture_pre_state(managed_repo, snapshot, work_item)

    # Step 2: decide. The launch guard is positive and total: a worker is
    # launched only when `decision.automatic` is True -- never inferred
    # from the *absence* of a gate or a decline, which is exactly the hole
    # a negative guard reopens every time a new non-launching outcome is
    # added (round 11's B1). `decide_no_work_item` is the distinct,
    # sibling entry point row 7 needs (CP4's own paragraph): unconditional
    # and version-independent, since there is no work item yet to key a
    # phase-dispatch table on.
    decision = decide_no_work_item(managed_repo) if is_bootstrap else evidence.decide(managed_repo, snapshot, work_item)

    if decision.gate is not None:
        return _no_launch_record(
            runtime, managed_repo, identity, resolved_work_item_id, decision.observed_phase, pre_state,
            decision, status=STATUS_GATE_BLOCKED,
        )
    if decision.declined:
        return _no_launch_record(
            runtime, managed_repo, identity, resolved_work_item_id, decision.observed_phase, pre_state,
            decision, status=STATUS_DECLINED,
        )
    if not decision.automatic:
        # LEGACY_READY / MILESTONE_COMPLETE: action=None, automatic=False,
        # gate=None, declined=False. Nothing ran, nothing is pending,
        # nothing was declined -- there is nothing to record.
        return decision

    # Step 3: pending generation handoff (CP8). `handoff.py` does not
    # exist yet, so this reads the runtime root's own `handoff.json`
    # directly, exactly as `cli._capture_pre_existing_state` already does.
    if _pending_handoff(runtime) is not None:
        return _no_launch_record(
            runtime, managed_repo, identity, resolved_work_item_id, decision.observed_phase, pre_state,
            decision, status=STATUS_HANDOFF_PENDING,
        )

    # Defensive: by construction, every branch above that could return
    # already has. Reaching here with a non-automatic decision (or no
    # action) is an invariant violation, never ordinary control flow.
    if not decision.automatic or decision.action is None:
        raise HumanGateError(
            "execute_step reached the launch guard with a non-automatic decision",
            evidence={"observed_phase": decision.observed_phase},
        )

    # Step 4: write the job record in two flushes, PLANNED then LAUNCHED,
    # both before the worker is spawned.
    job_id = _new_job_id()
    now = _now()
    record: JobRecord = {
        **_identity_block(managed_repo, identity, resolved_work_item_id, decision.observed_phase, job_id),
        "pre_state": _durable_pre_state(pre_state),
        "selected_action": _selected_action_dict(decision),
        "status": STATUS_PLANNED,
        "human_gate_pending": None,
        "handoff_pending": False,
        "created_at": now,
        "updated_at": now,
    }
    _persist(runtime, job_id, record)

    expected_transition = _expected_transition(decision.observed_phase, governing_workflow_version, decision)
    record = {
        **record,
        "expected_transition": expected_transition,
        "status": STATUS_LAUNCHED,
        "updated_at": _now(),
    }
    _persist(runtime, job_id, record)

    # Step 5: launch the worker and wait synchronously.
    resolved_timeout = DEFAULT_WORKER_TIMEOUT if timeout is None else timeout
    result = worker.launch(
        decision.action.command,
        cwd=managed_repo.root,
        permission_mode=permission_mode,
        timeout=resolved_timeout,
        claude_bin=claude_bin,
    )

    # Step 6: record the worker result.
    stdout_path, stderr_path = _write_worker_streams(runtime, job_id, result)
    record = {
        **record,
        "status": STATUS_COMPLETED,
        "worker": _worker_dict(result, stdout_path=stdout_path, stderr_path=stderr_path),
        "worker_outcome": result.outcome,
        "updated_at": _now(),
    }
    _persist(runtime, job_id, record)

    # Step 7 (CP6B): re-read the target repository's Workflow state fresh
    # -- never the `snapshot`/`work_item` captured before the worker ran
    # (see this module's own docstring, "CP6B's own scope note on step
    # 7's 'fresh managed_repo.inspect'"). Steps 7-9 are CP6B's own scope
    # (this checkpoint, CP6, owns only steps 1-6): a row-7 (`NoWorkItemYet`)
    # job still reaches here today and this line still raises
    # `AttributeError` on `work_item.work_item_id`, since there is no
    # `WorkItemView` to read one off of -- CP6's own steps 1-6 (pre-state
    # capture, the decision, and the `PLANNED`/`LAUNCHED`/`COMPLETED`
    # flushes) are correct and durable for row 7 by the time execution
    # reaches this point; wiring steps 7-9 for row 7 (a fresh
    # `work_item_id`-free re-read, and row 7's own predicate/verification)
    # is CP6B's own revalidation, next.
    post_snapshot = target_state.read(managed_repo)
    post_work_item = target_state.select_work_item(post_snapshot, work_item_id=work_item.work_item_id)
    observed_phase_after = post_work_item.phase

    outcome_row = _expected_outcome_for(work_item.phase, work_item.governing_workflow_version, decision)

    # Step 9: INCOMPLETE takes precedence over step 8's own "otherwise" --
    # a phase that is a legal *effect* of the action but never a
    # *completion* of it. Empty for every one of Generation 1's seven rows
    # (see `_INCOMPLETE_EFFECT_PHASES`'s own docstring); checked first so
    # a future generation's row can populate it without this call site
    # changing.
    incomplete_effect_phases = _INCOMPLETE_EFFECT_PHASES.get(
        (work_item.phase, work_item.governing_workflow_version, decision.action.command.split()[0]),
        frozenset(),
    )

    # Step 8: the verification rule, stated in full and in one form.
    verified, verification_evidence = _verify_transition(
        root=managed_repo.root, work_item_id=work_item.work_item_id, outcome=outcome_row,
        pre_state=pre_state, observed_phase_after=observed_phase_after,
        worker_outcome=result.outcome,
    )

    if verified:
        final_status = STATUS_FINISHED
    elif observed_phase_after in incomplete_effect_phases:
        final_status = STATUS_INCOMPLETE
    else:
        final_status = STATUS_FAILED

    record = {
        **record,
        "status": final_status,
        "observed_phase_after": observed_phase_after,
        "transition_verified": verified,
        "updated_at": _now(),
    }
    if final_status == STATUS_FAILED:
        record["reconciliation_evidence"] = verification_evidence
    elif final_status == STATUS_INCOMPLETE:
        # Not verified, not FAILED, not FINISHED: the worker's own stdout
        # is the evidence of what stopped it (CP6B step 9).
        record["reconciliation_evidence"] = {
            "code": "IncompleteEffectPhase",
            "observed_phase": observed_phase_after,
            "worker_stdout": result.stdout,
        }
    _persist(runtime, job_id, record)
    return record
