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
function. (``workflow-controller-automatic-lifecycle-orchestration`` CP5
moved the nine steps, unchanged in order, into ``_execute_step_locked``,
which :func:`execute_step` runs under the target worktree's lifecycle
lock, after refusing on any job still pending reconciliation.)

Dependency graph (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``,
"Dependency direction"): ``job -> {managed_repo, target_state, evidence,
worker, handoff} -> decision -> {identity, runtime, errors}``. Step 3's
pending-handoff check reads ``<runtime_root>/handoff.json`` directly
through ``controller.runtime.read_json`` -- the exact read
``controller.cli``'s own ``status`` command already performs. It was
written that way in CP6, before ``controller/handoff.py`` existed, and it
stays that way now that CP8 has added it: ``controller.handoff`` *publishes*
that record (``controller.handoff.write_handoff_record``) and exposes no
reader, so there is nothing there for this check to call. Detecting a
*new* handoff is the orchestration boundary's job
(``controller.cli._run_one_step``), never this function's -- step 3 only
asks whether one is already on disk.

**A deliberate name shadow.** :func:`execute_step`'s own parameters are
named ``identity`` (an already-resolved
``controller.identity.ControllerIdentity``) and ``runtime`` (the resolved
runtime-root ``Path``) -- the plan's own declared signature -- which
shadow this module's ``controller.identity``/``controller.runtime``
imports *inside that one function's body*. This is intentional, not an
oversight: every module-level helper below that actually performs I/O
(:func:`_persist`, :func:`_pending_handoff`, :func:`_create_worker_streams`)
is defined *outside* :func:`execute_step`, where the real modules are
still in scope, and takes the runtime root as an explicit ``runtime_root``
parameter -- never named ``runtime`` -- so :func:`execute_step` calls them
by passing its own (shadowed) local values in. This is also what keeps
"spy on ``controller.job.runtime.write_json``" a valid test technique: the
module-level import is the only thing ever called. The same holds for
:func:`execute_step`'s ``routing`` parameter (``workflow-controller-
automatic-lifecycle-orchestration`` CP6), the resolved
``controller.routing.RoutingOptions``, which shadows the ``controller.
routing`` import inside :func:`execute_step` and
:func:`_execute_step_locked`: the module-level :func:`_worker_route` is
what calls into ``controller.routing``.

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

**The installed Workflow release** (``workflow-controller-workflow-2-6-
integration`` CP3, invariant I3) is the one exception, and it is a plain
read of ``.workflow-manager/installation.json``, never a Manager call:
before every decision, right after the repository preflight, it must still
be the release ``inspect`` admitted (:func:`_refuse_changed_release`), and
before every verification it must still be the release the job record
carries (:func:`_verification_contract`, inside
:func:`_row_clauses_failure`). That release's Workflow contract, bound
once per decision, pre-state capture and verification, is what every
feedback-path read uses.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from controller import (
    evidence, identity, lock, milestone_branch, protocol, protocol_decision, routing, runtime, target_state,
    telemetry, worker, workflow_contract,
)
from controller.decision import (
    NO_PHASE,
    NO_PHASE_WIRE,
    Decision,
    branch_human_gate,
    decide_no_work_item,
    phase_from_wire,
    phase_to_wire,
)
from controller.errors import (
    BranchInvariantViolatedError,
    ControllerError,
    HumanGateError,
    JobAbandonRefusedError,
    LifecycleWorkerActiveError,
    LifecycleWorkerUnverifiableError,
    MalformedInstallationManifestError,
    OwnedWorkDetachedError,
    PendingJobReconciliationError,
    RuntimeContainmentError,
    StaleJobRecordError,
    UnmanagedRepositoryError,
    UnreconcilableJobError,
    UnsupportedWorkflowVersionError,
    UserOnlyCommandError,
    WorkerLaunchError,
    WorkflowProtocolFailedError,
    WorkflowProtocolUnsupportedError,
    WorkflowQueryError,
    WorkflowReleaseChangedError,
)
from controller.managed_repo import installed_workflow_version
from controller.workflow_contract import BoundContract

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

#: The no-action reason of a step whose repository preflight closed out a
#: milestone after its release wait and stopped (auto-merge-release-wait
#: D.4): no `/milestone-plan` is launched in the same step, and no job
#: record is written.
REASON_CLOSED_OUT_RELEASED = "closed_out_released"

#: `PRE_STATE_FIELDS` -- the single declaration both `_capture_pre_state`
#: and (in CP6B) the record-completeness property read, so the two cannot
#: drift (the plan's own stated defect history: a field declared in the
#: schema and never captured, round 8's B3). Five of these eighteen are a
#: predicate *input* (`bundle_manifest_bundle_id`, `bundle_generated_digest`,
#: `pre_work_item_keys`, `checkpoints` and `target_head` -- CP6B's own
#: concern, widened by `workflow-controller-automatic-lifecycle-
#: orchestration`'s CP2); the postconditions also read
#: `bundle_manifest_bundle_id`, `bundle_manifest_generation_head` and
#: `pre_work_item_keys`. The rest are report data for
#: `inspect`/`explain`/`status` and for a later generation's own use --
#: including `bundle_id`, which is `work_item.current_bundle_id`, a field
#: no Workflow 2.5.1 writer ever sets (2.6.0's `bind_plan_review_bundle`
#: does, and the Controller still does not read it), and so never a
#: predicate input again (CP2's plan-stage `BLOCK`-predicate fix).
#:
#: `bundle_manifest_bundle_id` (CP2) is the pre-state bundle
#: ``MANIFEST.md``'s own ``bundle_id:`` line -- the bundle a review job
#: started from. A record written before it existed lacks the key, and
#: every predicate/postcondition reading it treats that absence as "not
#: satisfied" (fail closed).
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
    "bundle_manifest_bundle_id",
    "bundle_manifest_generation_head",
    "bundle_generated_digest",
    "rejected_marker_present",
    "child_work_item_ids",
    "functional_review_consumed_blob",
    "functional_checklist_evidence",
    "pre_work_item_keys",
})

#: `auto` is the default for lifecycle workers against this (real,
#: managed) target repository: `acceptEdits` permits file edits but denies
#: the Bash/Python Workflow operations a non-interactive lifecycle worker
#: must run. `bypassPermissions` is reserved for disposable throwaway
#: repositories only, which `execute_step` never targets, and is never a
#: default. Any explicit `--permission-mode` is passed through unchanged --
#: the `claude` CLI, not Controller, is the authority over which modes exist.
DEFAULT_PERMISSION_MODE = "auto"

#: No limit (`workflow-controller-automatic-lifecycle-orchestration` CP5,
#: "Time is not termination"): `worker.launch` waits until the worker
#: returns control -- its direct child has exited and its process group is
#: empty (release-runtime-observability CP4) -- however long or silent it
#: is. `--timeout` stays the operator's explicit opt-in; when set, it kills
#: the whole process group before classifying. The Controller has no other
#: path that concludes a worker has ended.
DEFAULT_WORKER_TIMEOUT = None

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


def _functional_review_consumed_blob(root: Path, work_item_id: str, bound: BoundContract) -> str | None:
    """The current Git blob hash of ``FUNCTIONAL_REVIEW.md`` -- report
    data only (not a predicate input; see :data:`PRE_STATE_FIELDS`'s own
    note on which fields are), ``None`` when the file does not exist. The
    path is ``bound``'s feedback path, so under a ``workflow_query``
    contract a query failure raises here, before any decision."""
    findings_path = root / evidence.functional_review_findings_path(root, work_item_id, bound)
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
    `work_item_id=None` would match every top-level entry instead.

    The feedback path it reads (``functional_review_consumed_blob``) is
    resolved under the admitted release's contract, bound here for this
    capture alone (``workflow-controller-workflow-2-6-integration`` CP3). A
    ``WorkflowQueryError`` propagates: this runs before ``decide``, so it is
    a decision-time refusal, with no worker launched and no record written."""
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
            "bundle_manifest_bundle_id": None,
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

    if managed_repo.target_protocol is not None:
        # A protocol target's lifecycle files (bundle, manifest, feedback,
        # checklist evidence) are the Workflow's to read, not the
        # Controller's (orchestration-protocol-v1 I8): only the state's own
        # fields are captured, and the file-derived ones are empty.
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
            "bundle_manifest_readable": False,
            "bundle_manifest_bundle_id": None,
            "bundle_manifest_generation_head": None,
            "bundle_generated_digest": None,
            "rejected_marker_present": False,
            "child_work_item_ids": sorted(
                wid for wid, view in snapshot.work_items.items() if view.parent_work_item_id == work_item_id),
            "functional_review_consumed_blob": None,
            "functional_checklist_evidence": None,
            "pre_work_item_keys": pre_work_item_keys,
        }

    bound = workflow_contract.bind_release(managed_repo.workflow_version)

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
        "bundle_manifest_bundle_id": manifest["bundle_id"],
        "bundle_manifest_generation_head": manifest["generation_head"],
        "bundle_generated_digest": _bundle_generated_digest(root, bundle_dir),
        "rejected_marker_present": rejected_present,
        "child_work_item_ids": child_work_item_ids,
        "functional_review_consumed_blob": _functional_review_consumed_blob(root, work_item_id, bound),
        "functional_checklist_evidence": evidence.functional_checklist_evidence(
            root, work_item_id, work_item.base_commit, target_head, work_item.implementation_revision,
        ),
        "pre_work_item_keys": pre_work_item_keys,
    }


# ---------------------------------------------------------------------------
# CP6B -- the `ExpectedOutcome` table (plan section "CP6B -- Job execution,
# part 2", the table transcribed under "An `ExpectedOutcome` is data, not
# prose"). Eighteen rows: one per automatic `(from_phase,
# governing_workflow_version, action)` triple -- CP6B's own six, revision
# 63's B2 `NoWorkItemYet` bootstrap row (whose `from_phase` is `NO_PHASE`,
# never a phase CP4's dispatch table is keyed on), the four `"2.2"`
# plan-review rows the `workflow-controller-protocol-2-2-compatibility`
# milestone's CP1 added, and the seven implementation-stage rows (12-18)
# `workflow-controller-automatic-lifecycle-orchestration`'s CP2 added.
# `to_any_of` alone drives step 4's `expected_transition`; the
# `predicate`/`predicate_inputs`/`writer_calls` columns -- and the per-phase
# `postconditions` (`workflow-controller-worker-execution-hardening`'s CP3
# added a single `postcondition`/`postcondition_phases` pair; this
# milestone's CP2 generalised it to one postcondition per phase set) -- are
# step 8's own verification concern.
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
    matched.

    ``trailing_calls`` (`workflow-controller-automatic-lifecycle-
    orchestration` CP2) is an explicit, per-call allowlist of
    ``(function, justification)`` pairs: ``workflow_state.<function>(``
    calls the frozen text legitimately makes *after* the declared call
    inside the declared span, which property 5 would otherwise report as a
    further durable write (`milestone-implement.md` step 1f's post-guard
    ``committed_checkpoint_status`` read and ``release_checkpoint``
    claim-record release). Property 5 skips only those named functions,
    and only after the declared call; an entry naming a function that does
    not occur there is itself a violation, so the allowlist cannot go
    stale silently. Empty for every call that needs none -- none is added
    speculatively."""

    function: str
    file: str
    location: str
    kind: str
    branch: BranchSpec | None = None
    match_text: str | None = None
    trailing_calls: tuple[tuple[str, str], ...] = ()

    def match(self) -> str:
        return self.match_text if self.match_text is not None else f"{self.function}("


#: A row's predicate: ``(root, work_item_id, pre_state, bound) -> bool``.
#: ``bound`` (`workflow-controller-workflow-2-6-integration` CP3) is the
#: job's recorded release's contract, bound once per verification by
#: :func:`_row_clauses_failure` and passed explicitly to every clause; a
#: clause that reads no Workflow answer ignores it.
PredicateFn = Callable[[Path, str, dict, BoundContract], bool]

#: A predicate stated with its reason (`workflow-controller-worker-
#: lifecycle-ownership` CP6): ``(root, work_item_id, pre_state, bound) ->
#: None`` when the row's predicate holds, else a short code naming the first
#: unmet condition, written to ``reconciliation_evidence.predicate_detail``.
PredicateDetailFn = Callable[[Path, str, dict, BoundContract], "str | None"]

#: A row's artifact postcondition (`workflow-controller-worker-execution-
#: hardening` CP3): ``(root, work_item_id, pre_state, bound) -> (satisfied,
#: detail)``, evaluated fresh against disk. Distinct from
#: :data:`PredicateFn`: a predicate disambiguates a self-loop, a
#: postcondition asserts that the durable artifact a completion phase
#: promises is actually coherent, whichever way the phase was reached.
PostconditionFn = Callable[[Path, "str | None", dict, BoundContract], tuple[bool, str]]

#: One ``ExpectedOutcome.postconditions`` entry: the observed post-phases
#: it applies to, and the postcondition evaluated there.
PostconditionEntry = tuple[frozenset[str], PostconditionFn]


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
    #: Per-phase artifact postconditions (`workflow-controller-automatic-
    #: lifecycle-orchestration` CP2, generalising the single
    #: ``postcondition``/``postcondition_phases`` pair): the entry whose
    #: phase set contains the observed post-phase is evaluated, and no
    #: other. The phase sets are non-empty, pairwise disjoint and each a
    #: subset of ``to_any_of`` (:func:`property_table_violations`); a row
    #: may declare none. Not subject to the predicate's own "iff
    #: self-loop" rule.
    postconditions: tuple[PostconditionEntry, ...] = ()
    #: The row's predicate stated with a reason (CP6). When set, it is what
    #: :func:`_row_clauses_failure` evaluates -- once, never alongside
    #: ``predicate`` -- and ``predicate`` must hold exactly when it returns
    #: ``None``. Only a predicate-bearing row may declare one.
    predicate_detail: PredicateDetailFn | None = None


def _row_branch(outcome: ExpectedOutcome) -> BranchSpec | None:
    """The row's own single ``branch`` (CP6B, step 8's revision-65/66
    repair; Property 3's "total, fail-closed derivation"), derived across
    every declared :class:`WriterCall` rather than read off one
    arbitrarily -- ``writer_calls`` is a tuple precisely because a row
    *can* name more than one call (round 6's corollary), so a row whose
    entries disagree on ``branch`` is a table-construction defect, never
    silently resolved by picking the first. Every real row in
    :data:`EXPECTED_OUTCOMES` declares exactly one ``WriterCall``, so this
    is the identity on the table as it stands; it exists because step 8's
    trigger now dispatches on this value, and a field a rule dispatches on
    must be total at the level the rule names it. Raises
    ``AssertionError`` -- an invariant violation, never ordinary
    control-flow, the same shape as this module's other table-invariant
    checks -- when ``outcome`` itself was never validated by
    :func:`property_table_violations`, for either of the two shapes that
    leave no single value to return: ``writer_calls`` entries that
    disagree on ``branch``, or no ``writer_calls`` at all.
    :func:`property_table_violations` reports **both** as data instead,
    for a row that is being validated rather than executed."""
    branches = {wc.branch for wc in outcome.writer_calls}
    if len(branches) != 1:
        raise AssertionError(
            f"ExpectedOutcome writer_calls disagree on branch: {sorted(branches, key=repr)!r}"
        )
    return next(iter(branches))


def _postcondition_for_phase(outcome: ExpectedOutcome, phase: Any) -> PostconditionFn | None:
    """The postcondition of ``outcome``'s entry whose phase set contains
    ``phase``, or ``None`` when no entry does. The phase sets are pairwise
    disjoint (:func:`property_table_violations`), so at most one entry
    can match."""
    for phases, postcondition in outcome.postconditions:
        if phase in phases:
            return postcondition
    return None


def _block_feedback_bound_to_pre_state_bundle(
    root: Path, work_item_id: str, pre_state: dict, bound: BoundContract, *,
    role_matches: Callable[[str | None], bool],
) -> bool:
    """The one ``BLOCK``-self-loop evidence rule both review rows share
    (row 3's plan-stage ``/review-plan`` and row 16's ``"2.2"``
    ``/review-implementation``): a ``REVIEW_FEEDBACK.md`` read fresh from
    disk now declares ``Status: BLOCK``, a reviewer role ``role_matches``
    accepts, and a ``Reviewed bundle ID:`` equal to the bundle the job
    started from -- the pre-state ``MANIFEST.md``'s own ``bundle_id``
    (``pre_state["bundle_manifest_bundle_id"]``). Both sides must be
    non-null: a feedback file missing its binding line, a pre-state with no
    readable manifest, or a record written before the field existed is
    "not satisfied", never ``None == None`` (fail closed)."""
    expected_bundle_id = pre_state.get("bundle_manifest_bundle_id")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id:
        return False
    feedback_dir = evidence.resolve_feedback_dir(root, work_item_id, bound)
    feedback = evidence.read_feedback_fields(root, feedback_dir)
    if feedback is None:
        return False
    # `expected_bundle_id` is a non-empty string here, so equality alone
    # already refuses a feedback file whose binding line is missing.
    return (
        feedback.get("status") == "BLOCK"
        and role_matches(feedback.get("reviewer_role"))
        and feedback.get("reviewed_bundle_id") == expected_bundle_id
    )


def _predicate_row3_block_feedback_current(
    root: Path, work_item_id: str, pre_state: dict, bound: BoundContract,
) -> bool:
    """Row 3's predicate: a current-round ``REVIEW_FEEDBACK.md`` now
    exists whose ``Reviewed bundle ID:`` matches the bundle the pre-state
    plan ``MANIFEST.md`` carried, whose ``Reviewer role:`` is
    ``LOCAL_MODEL_PLAN_REVIEW`` (legacy spellings normalised exactly as
    ``evidence._normalize_role`` does), and whose ``Status:`` is ``BLOCK``
    -- read fresh from disk, never from `pre_state` itself (the file did
    not exist, or held a different round's content, before the worker
    ran).

    CP2's fix: the binding side used to be ``pre_state["bundle_id"]``
    (``work_item.current_bundle_id``), which no Workflow 2.5.1 writer ever
    sets, so a genuine ``BLOCK`` never verified while a feedback file
    *missing* its binding line did (``None == None``). Workflow 2.6.0's
    ``bind_plan_review_bundle`` writes it; the Controller still does not
    read it."""
    return _block_feedback_bound_to_pre_state_bundle(
        root, work_item_id, pre_state, bound,
        role_matches=lambda role: evidence._normalize_role(role) == "LOCAL_MODEL_PLAN_REVIEW",
    )


def _predicate_local_implementation_block_current(
    root: Path, work_item_id: str, pre_state: dict, bound: BoundContract,
) -> bool:
    """Row 16's predicate (the ``"2.2"`` ``/review-implementation``
    ``BLOCK`` no-op): a fresh ``REVIEW_FEEDBACK.md`` whose role is exactly
    ``LOCAL_MODEL_IMPLEMENTATION_REVIEW`` (no alias -- the implementation
    ledger was introduced fresh at ``"2.2"``), ``Status: BLOCK``, bound to
    the pre-state manifest's non-null ``bundle_id``."""
    return _block_feedback_bound_to_pre_state_bundle(
        root, work_item_id, pre_state, bound,
        role_matches=lambda role: role == evidence.LOCAL_IMPLEMENTATION_ROLE,
    )


def _predicate_row5_bundle_regenerated(
    root: Path, work_item_id: str, pre_state: dict, bound: BoundContract,
) -> bool:
    """Row 5's predicate: the freshly recomputed `bundle_generated_digest`
    differs from the one `pre_state` captured -- the generator's own
    completed output, never trusted from `MANIFEST.md`'s stale
    `bundle_id` or from the generation-record commit alone (plan section
    "`bundle_generated_digest` -- what row 5 reads")."""
    bundle_dir = evidence.resolve_bundle_dir(root, work_item_id, phase=pre_state["phase"])
    post_digest = _bundle_generated_digest(root, bundle_dir)
    return post_digest != pre_state.get("bundle_generated_digest")


def _predicate_row7_new_work_item_created(
    root: Path, work_item_id: Any, pre_state: dict, bound: BoundContract,
) -> bool:
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


def _postcondition_plan_bundle_coherent(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """The plan-bundle-producing rows' postcondition (CP3): the current
    plan bundle's ``MANIFEST.md`` belongs to the work item's fresh
    post-state ``plan_revision`` -- :func:`controller.evidence.
    plan_bundle_coherence`, the single definition the decision-time gate
    shares. Row 7's ``work_item_id`` is ``None``; the id is then the
    single new key against ``pre_state["pre_work_item_keys"]``, exactly
    as :func:`_observe_post_phase` resolves it (no single new key -> not
    satisfied). Any read failure is "not satisfied", never a false
    verification.

    Under a ``workflow_query`` contract, for a ``"2.1"``/``"2.2"`` item
    (`workflow-controller-workflow-2-6-integration` CP4), it is Workflow's
    answer instead: the item is at ``AWAITING_LOCAL_PLAN_REVIEW`` and
    ``--plan-review-publication-status`` reports it ``BOUND``
    (:func:`controller.evidence.plan_review_bound`). A query failure
    raises ``WorkflowQueryError``, which :func:`_row_clauses_failure`
    records as ``workflow_query_failed``."""
    try:
        post_snapshot = target_state.read(SimpleNamespace(root=root))
    except ControllerError as exc:
        return False, f"post-state could not be read: {exc}"
    if work_item_id is None:
        new_keys = frozenset(post_snapshot.work_items) - frozenset(pre_state.get("pre_work_item_keys") or ())
        if len(new_keys) != 1:
            return False, f"expected exactly one new work item, found {len(new_keys)}: {sorted(new_keys)!r}"
        work_item_id = next(iter(new_keys))
    post_work_item = post_snapshot.work_items.get(work_item_id)
    if post_work_item is None:
        return False, f"work item {work_item_id!r} is absent from the post-state"
    if evidence.queries_plan_review_publication_status(bound, post_work_item.governing_workflow_version):
        return evidence.plan_review_bound(root, work_item_id, post_work_item.phase, bound)
    return evidence.plan_bundle_coherence(root, work_item_id, post_work_item.plan_revision)


# ---------------------------------------------------------------------------
# `workflow-controller-automatic-lifecycle-orchestration` CP2 -- the
# implementation-stage predicates and postconditions (rows 12-18). Each is
# read fresh from disk and the target's own Git history, never from process
# memory, and every read failure is "not satisfied" (fail closed). Like the
# plan-stage postcondition they compare revisions, bindings and committed
# facts only; nothing here recomputes `bundle_id`/`review_content_id`.
# ---------------------------------------------------------------------------

_SELF_REVIEWING_IMPLEMENTATION = "SELF_REVIEWING_IMPLEMENTATION"


def _fresh_work_item(root: Path, work_item_id: str | None) -> tuple[Any, str | None]:
    """``(work_item_view, None)`` for ``work_item_id`` in a fresh
    post-state read, or ``(None, detail)`` when the state cannot be read or
    carries no such work item."""
    try:
        post_snapshot = target_state.read(SimpleNamespace(root=root))
    except ControllerError as exc:
        return None, f"post-state could not be read: {exc}"
    post_work_item = post_snapshot.work_items.get(work_item_id)
    if post_work_item is None:
        return None, f"work item {work_item_id!r} is absent from the post-state"
    return post_work_item, None


def _checkpoint_status(checkpoint: Any) -> Any:
    return checkpoint.get("status") if isinstance(checkpoint, Mapping) else None


#: The closed set of ``reconciliation_evidence.predicate_detail`` values
#: :func:`_checkpoint_completion_failure` returns (`workflow-controller-
#: worker-lifecycle-ownership` CP6, plan section F): each names the first
#: unmet condition of the same-phase durable-progress rule.
CHECKPOINT_PROGRESS_DETAILS: frozenset[str] = frozenset({
    "pre_state_incomplete",
    "head_unchanged",
    "state_unreadable",
    "no_newly_completed_checkpoint",
    "completion_not_committed_at_head",
    "last_completed_not_committed",
})


def _checkpoint_completion_failure(
    root: Path, work_item_id: str, pre_state: dict, bound: BoundContract,
) -> str | None:
    """The same-phase durable-progress rule, stated once with a reason
    (CP6): ``None`` when it holds, else the first unmet condition, one of
    :data:`CHECKPOINT_PROGRESS_DETAILS`.

    The rule is unchanged: ``HEAD`` moved off ``pre_state["target_head"]``,
    and at least one checkpoint is ``COMPLETE`` in the fresh state, was not
    ``COMPLETE`` in ``pre_state["checkpoints"]``, and is ``COMPLETE`` in the
    state committed at ``HEAD``. When checkpoints newly completed but none
    is committed at ``HEAD``, the detail is ``last_completed_not_committed``
    if ``last_completed_checkpoint_id`` advanced to a checkpoint that is not
    ``COMPLETE`` in the committed state (the "working-tree-only completion"
    shape), else ``completion_not_committed_at_head``. An unreadable
    ``HEAD`` counts as ``state_unreadable``."""
    pre_checkpoints = pre_state.get("checkpoints")
    pre_head = pre_state.get("target_head")
    if not isinstance(pre_checkpoints, Mapping) or not isinstance(pre_head, str):
        return "pre_state_incomplete"
    head = _current_head(root)
    if head is None:
        return "state_unreadable"
    if head == pre_head:
        return "head_unchanged"
    post_work_item, _detail = _fresh_work_item(root, work_item_id)
    if post_work_item is None or not isinstance(post_work_item.checkpoints, Mapping):
        return "state_unreadable"
    committed = evidence.committed_checkpoint_statuses(root, "HEAD")
    if committed is None:
        return "state_unreadable"
    newly_complete = [
        checkpoint_id for checkpoint_id, checkpoint in post_work_item.checkpoints.items()
        if _checkpoint_status(checkpoint) == "COMPLETE"
        and _checkpoint_status(pre_checkpoints.get(checkpoint_id)) != "COMPLETE"
    ]
    if not newly_complete:
        return "no_newly_completed_checkpoint"
    if any(committed.get((work_item_id, checkpoint_id)) == "COMPLETE" for checkpoint_id in newly_complete):
        return None
    last_completed = post_work_item.last_completed_checkpoint_id
    if (
        last_completed is not None
        and last_completed != pre_state.get("last_completed_checkpoint_id")
        and committed.get((work_item_id, last_completed)) != "COMPLETE"
    ):
        return "last_completed_not_committed"
    return "completion_not_committed_at_head"


def _predicate_checkpoint_completed_durably(
    root: Path, work_item_id: str, pre_state: dict, bound: BoundContract,
) -> bool:
    """Rows 12/13's self-loop predicate (``/milestone-implement`` from
    ``IMPLEMENTING`` back to ``IMPLEMENTING``): :func:`_checkpoint_completion_failure`
    finds no unmet condition. Step 1f persists the completion and commits
    it in one guard window, so a completion left only in the working tree
    is not durable and does not verify; a pre-state lacking either input
    is "not satisfied"."""
    return _checkpoint_completion_failure(root, work_item_id, pre_state, bound) is None


def _postcondition_self_review_entered_durably(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """Rows 12/13's ``SELF_REVIEWING_IMPLEMENTATION`` postcondition: the
    fresh ``registry_complete`` is ``True``, every registry checkpoint is
    ``COMPLETE`` in the state committed at ``HEAD``, and the work item's
    committed phase at ``HEAD`` is ``SELF_REVIEWING_IMPLEMENTATION``.

    It deliberately does not require a checkpoint to have *newly*
    completed: from ``IMPLEMENTING`` with every checkpoint already
    ``COMPLETE`` (the ``NO_CHECKPOINT`` path after a plan re-approval),
    ``/milestone-implement`` step 2 commits the transition alone, and a
    run that stops right after that commit is a legal partial run. On the
    ordinary path step 1f's own checkpoint commit makes every clause hold.

    Registry completion at ``HEAD`` is derived by
    ``target_state._resolve_registry_complete`` -- the one reader of the
    registry's declared checkpoint ids -- over the committed statuses."""
    post_work_item, detail = _fresh_work_item(root, work_item_id)
    if post_work_item is None:
        return False, detail
    if post_work_item.registry_complete is not True:
        return False, (
            f"the post-state registry_complete is {post_work_item.registry_complete!r}, not True "
            f"(a registry checkpoint is not COMPLETE)"
        )
    committed_entry = evidence.committed_work_item(root, work_item_id, "HEAD")
    committed_statuses = evidence.committed_checkpoint_statuses(root, "HEAD")
    if committed_entry is None or committed_statuses is None:
        return False, f"{work_item_id!r}'s WORKFLOW_STATE.json entry could not be read at HEAD"
    own_committed = {
        checkpoint_id: {"status": status}
        for (owner, checkpoint_id), status in committed_statuses.items() if owner == work_item_id
    }
    try:
        committed_complete = target_state._resolve_registry_complete(
            root, work_item_id,
            {"registry_path": committed_entry.get("registry_path"), "checkpoints": own_committed},
        )
    except ControllerError as exc:
        return False, f"registry completion at HEAD could not be derived: {exc}"
    if committed_complete is None:
        return False, f"{work_item_id!r}'s entry committed at HEAD declares no registry_path"
    if committed_complete is not True:
        statuses = {checkpoint_id: entry["status"] for checkpoint_id, entry in sorted(own_committed.items())}
        return False, (
            f"not every registry checkpoint is COMPLETE in the state committed at HEAD "
            f"(committed statuses {statuses!r}): the checkpoint completion is uncommitted"
        )
    committed_phase = committed_entry.get("phase")
    if committed_phase != _SELF_REVIEWING_IMPLEMENTATION:
        return False, (
            f"the committed phase at HEAD is {committed_phase!r}, not "
            f"{_SELF_REVIEWING_IMPLEMENTATION!r}: the transition is uncommitted"
        )
    return True, (
        f"every registry checkpoint is COMPLETE and the phase is "
        f"{_SELF_REVIEWING_IMPLEMENTATION!r} in the state committed at HEAD"
    )


def _postcondition_implementation_bundle_coherent(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """The implementation-bundle-producing phases' postcondition (rows
    12-15, and the coherence half of row 18's): no ``REJECTED`` marker
    (``evidence.rejected_marker_detail``), and the current implementation
    bundle belongs to the fresh post-state's round at the live ``HEAD``
    (``evidence.implementation_bundle_coherence`` with
    ``require_current_generation_head=True``: every generation-record
    commit lands immediately before its generation, so a completed round
    leaves ``generation_head == HEAD``)."""
    post_work_item, detail = _fresh_work_item(root, work_item_id)
    if post_work_item is None:
        return False, detail
    rejected, marker_detail = evidence.rejected_marker_detail(root, work_item_id)
    if rejected:
        marker_path = evidence.resolve_rejected_marker_path(root, work_item_id)
        return False, (
            f"the REJECTED marker at {marker_path} is present ({marker_detail}): the "
            f"implementation bundle was withdrawn"
        )
    coherent, _clause, coherence_detail = evidence.implementation_bundle_coherence(
        root, post_work_item, _current_head(root), require_current_generation_head=True,
    )
    return coherent, coherence_detail


def _postcondition_implementation_bundle_regenerated(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """Row 18's postcondition (``"2.2"`` ``/apply-implementation-review``):
    the coherent clause, plus a manifest ``generation_head`` different from
    ``pre_state["bundle_manifest_generation_head"]`` -- a new generation
    must have run in this job. The revision-level clauses alone cannot see
    a ``same_content`` post-fix, whose ``implementation_revision`` and
    ``reviewed_implementation_head`` deliberately do not move."""
    if "bundle_manifest_generation_head" not in pre_state:
        return False, "the pre-state carries no bundle_manifest_generation_head to compare against"
    coherent, detail = _postcondition_implementation_bundle_coherent(root, work_item_id, pre_state, bound)
    if not coherent:
        return False, detail
    manifest = evidence.read_manifest_fields(root, evidence.implementation_bundle_dir(root, work_item_id))
    pre_generation_head = pre_state["bundle_manifest_generation_head"]
    if manifest["generation_head"] == pre_generation_head:
        return False, (
            f"manifest generation_head {pre_generation_head!r} is the pre-state's own: no bundle "
            f"generation ran in this job"
        )
    return True, f"{detail}; regenerated since generation_head {pre_generation_head!r}"


def _implementation_manifest(root: Path, work_item_id: str) -> dict[str, Any]:
    return evidence.read_manifest_fields(root, evidence.implementation_bundle_dir(root, work_item_id))


def _feedback_verdict_failure(
    root: Path, work_item_id: str, bound: BoundContract, *, role: str, status: str,
    bundle_id: str | None, bundle_source: str,
) -> tuple[dict | None, str | None]:
    """``(feedback, None)`` when ``REVIEW_FEEDBACK.md`` (read fresh, at
    ``bound``'s feedback path) is on file with reviewer role exactly
    ``role``, ``Status`` exactly ``status`` and ``Reviewed bundle ID`` equal
    to a non-null ``bundle_id``; otherwise ``(feedback_or_None, detail)``
    naming the first failing clause. ``bundle_source`` names where
    ``bundle_id`` came from."""
    feedback_dir = evidence.resolve_feedback_dir(root, work_item_id, bound)
    feedback = evidence.read_feedback_fields(root, feedback_dir)
    if feedback is None:
        return None, f"no REVIEW_FEEDBACK.md is on file at {feedback_dir}"
    if feedback.get("reviewer_role") != role:
        return feedback, f"feedback Reviewer role {feedback.get('reviewer_role')!r} != {role!r}"
    if feedback.get("status") != status:
        return feedback, f"feedback Status {feedback.get('status')!r} != {status!r}"
    if not bundle_id:
        return feedback, f"{bundle_source} is {bundle_id!r}, so the feedback's binding cannot be checked"
    if feedback.get("reviewed_bundle_id") != bundle_id:
        return feedback, (
            f"feedback Reviewed bundle ID {feedback.get('reviewed_bundle_id')!r} != "
            f"{bundle_source} {bundle_id!r}"
        )
    return feedback, None


def _postcondition_local_implementation_approve_recorded(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """Row 16's ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW``
    postcondition (a local ``APPROVE``): the fresh ledger records
    ``LOCAL_MODEL_IMPLEMENTATION_REVIEW`` against the current manifest's
    ``bundle_id`` and ``review_content_id``, no manual stage yet, and the
    feedback on file is that role's ``APPROVE`` bound to the manifest's
    ``bundle_id``. There is deliberately no clause on the feedback's own
    content-id line: ``review-implementation.md`` A5 requires the value
    "as its own labelled line" without naming the label, and the ledger
    clause already binds the content."""
    post_work_item, detail = _fresh_work_item(root, work_item_id)
    if post_work_item is None:
        return False, detail
    ledger = evidence.read_implementation_review_ledger(post_work_item)
    if ledger.malformed is not None:
        return False, f"implementation_review_stages is malformed: {ledger.malformed}"
    manifest = _implementation_manifest(root, work_item_id)
    if not manifest["_exists"]:
        return False, "the implementation bundle MANIFEST.md is missing or unreadable"
    manifest_bundle_id = manifest["bundle_id"]
    manifest_content_id = manifest["review_content_id"]
    if ledger.local is None:
        return False, f"the ledger records no {evidence.LOCAL_IMPLEMENTATION_ROLE} stage"
    if not manifest_bundle_id or ledger.local["bundle_id"] != manifest_bundle_id:
        return False, (
            f"ledger {evidence.LOCAL_IMPLEMENTATION_ROLE} bundle_id {ledger.local['bundle_id']!r} != "
            f"manifest bundle_id {manifest_bundle_id!r}"
        )
    if not manifest_content_id or ledger.review_content_id != manifest_content_id:
        return False, (
            f"ledger review_content_id {ledger.review_content_id!r} != manifest review_content_id "
            f"{manifest_content_id!r}"
        )
    if ledger.manual is not None:
        return False, f"the ledger already records {evidence.MANUAL_IMPLEMENTATION_ROLE}"
    _feedback, failure = _feedback_verdict_failure(
        root, work_item_id, bound, role=evidence.LOCAL_IMPLEMENTATION_ROLE, status="APPROVE",
        bundle_id=manifest_bundle_id, bundle_source="manifest bundle_id",
    )
    if failure is not None:
        return False, failure
    return True, (
        f"{evidence.LOCAL_IMPLEMENTATION_ROLE} APPROVE recorded for bundle {manifest_bundle_id} and "
        f"review_content_id {manifest_content_id}"
    )


def _postcondition_local_implementation_revise_recorded(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """Row 16's ``APPLYING_REVIEW_FEEDBACK`` postcondition (a local
    ``REVISE``): the feedback on file is role exactly
    ``LOCAL_MODEL_IMPLEMENTATION_REVIEW``, ``Status: REVISE``, bound to the
    bundle the job started from (a non-null
    ``pre_state["bundle_manifest_bundle_id"]``), for this work item."""
    feedback, failure = _feedback_verdict_failure(
        root, work_item_id, bound, role=evidence.LOCAL_IMPLEMENTATION_ROLE, status="REVISE",
        bundle_id=pre_state.get("bundle_manifest_bundle_id"),
        bundle_source="the pre-state manifest bundle_id",
    )
    if failure is not None:
        return False, failure
    if feedback.get("work_item") != work_item_id:
        return False, f"feedback Work item {feedback.get('work_item')!r} != {work_item_id!r}"
    return True, f"{evidence.LOCAL_IMPLEMENTATION_ROLE} REVISE on file for bundle {feedback['reviewed_bundle_id']}"


def _postcondition_manual_implementation_approve_recorded(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """Row 17's ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` postcondition
    (a manual ``APPROVE``): the fresh ledger records both stages against
    the manifest's ``review_content_id``, and the manual stage's
    ``bundle_id`` equals the feedback's ``Reviewed bundle ID`` -- recorded
    verbatim by contract (``record-manual-implementation-review.md`` step
    7), even when it differs from the current bundle."""
    post_work_item, detail = _fresh_work_item(root, work_item_id)
    if post_work_item is None:
        return False, detail
    ledger = evidence.read_implementation_review_ledger(post_work_item)
    if ledger.malformed is not None:
        return False, f"implementation_review_stages is malformed: {ledger.malformed}"
    if ledger.local is None or ledger.manual is None:
        missing = [
            role for role, stage in (
                (evidence.LOCAL_IMPLEMENTATION_ROLE, ledger.local),
                (evidence.MANUAL_IMPLEMENTATION_ROLE, ledger.manual),
            ) if stage is None
        ]
        return False, f"the ledger does not record {' or '.join(missing)}"
    manifest = _implementation_manifest(root, work_item_id)
    if not manifest["_exists"]:
        return False, "the implementation bundle MANIFEST.md is missing or unreadable"
    manifest_content_id = manifest["review_content_id"]
    if not manifest_content_id or ledger.review_content_id != manifest_content_id:
        return False, (
            f"ledger review_content_id {ledger.review_content_id!r} != manifest review_content_id "
            f"{manifest_content_id!r}"
        )
    feedback = evidence.read_feedback_fields(root, evidence.resolve_feedback_dir(root, work_item_id, bound))
    if feedback is None:
        return False, "no REVIEW_FEEDBACK.md is on file to bind the manual stage to"
    reviewed_bundle_id = feedback.get("reviewed_bundle_id")
    if not reviewed_bundle_id or ledger.manual["bundle_id"] != reviewed_bundle_id:
        return False, (
            f"ledger {evidence.MANUAL_IMPLEMENTATION_ROLE} bundle_id {ledger.manual['bundle_id']!r} != "
            f"feedback Reviewed bundle ID {reviewed_bundle_id!r}"
        )
    return True, (
        f"both implementation-review stages APPROVE recorded for review_content_id {manifest_content_id}"
    )


def _postcondition_manual_implementation_revise_recorded(
    root: Path, work_item_id: "str | None", pre_state: dict, bound: BoundContract,
) -> tuple[bool, str]:
    """Row 17's ``APPLYING_REVIEW_FEEDBACK`` postcondition (a manual
    ``REVISE``): the feedback on file is role exactly
    ``MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` with ``Status: REVISE``, and
    the ledger's manual stage is still unrecorded -- the ``REVISE`` branch
    writes no ledger entry. A malformed ledger is "not satisfied"."""
    feedback = evidence.read_feedback_fields(root, evidence.resolve_feedback_dir(root, work_item_id, bound))
    if feedback is None:
        return False, "no REVIEW_FEEDBACK.md is on file"
    if feedback.get("reviewer_role") != evidence.MANUAL_IMPLEMENTATION_ROLE:
        return False, (
            f"feedback Reviewer role {feedback.get('reviewer_role')!r} != "
            f"{evidence.MANUAL_IMPLEMENTATION_ROLE!r}"
        )
    if feedback.get("status") != "REVISE":
        return False, f"feedback Status {feedback.get('status')!r} != 'REVISE'"
    post_work_item, detail = _fresh_work_item(root, work_item_id)
    if post_work_item is None:
        return False, detail
    ledger = evidence.read_implementation_review_ledger(post_work_item)
    if ledger.malformed is not None:
        return False, f"implementation_review_stages is malformed: {ledger.malformed}"
    if ledger.manual is not None:
        return False, f"the ledger records {evidence.MANUAL_IMPLEMENTATION_ROLE}, which a REVISE never writes"
    return True, f"{evidence.MANUAL_IMPLEMENTATION_ROLE} REVISE on file; the manual stage is unrecorded"


#: The `milestone-implement.md` step 1f calls that follow `complete_checkpoint`
#: inside its own declared span (property 5's `trailing_calls` allowlist).
_STEP_1F_TRAILING_CALLS: tuple[tuple[str, str], ...] = (
    ("committed_checkpoint_status",
     "a read: once the guard is released, step 1f verifies the completion is committed before "
     "releasing the claim; it writes nothing"),
    ("release_checkpoint",
     "the checkpoint claim-record release under .ai-review/runtime, made only after the "
     "completion is durable; it never writes WORKFLOW_STATE.json"),
)


def _milestone_implement_rows(version: str, review_phase: str) -> tuple[ExpectedOutcome, ...]:
    """Rows 12/13 and 14/15 for one governing version: ``/milestone-implement``
    from ``IMPLEMENTING`` (one checkpoint per invocation, or -- when every
    checkpoint is already ``COMPLETE`` -- steps 2 and 4) and from
    ``SELF_REVIEWING_IMPLEMENTATION`` (step 4), where ``review_phase`` is
    ``bundle_generation_target_phase("implementation", version)``."""
    return (
        ExpectedOutcome(
            from_phase="IMPLEMENTING", governing_version=version, action="/milestone-implement",
            to_any_of=frozenset({"IMPLEMENTING", _SELF_REVIEWING_IMPLEMENTATION, review_phase}),
            predicate=_predicate_checkpoint_completed_durably,
            predicate_inputs=frozenset({"checkpoints", "target_head"}),
            predicate_detail=_checkpoint_completion_failure,
            writer_calls=(
                WriterCall("complete_checkpoint", "milestone-implement.md", "milestone-implement.md:183",
                           WRITER_KIND_COMPLETION, branch=BranchSpec(kind="step", label="1f"),
                           trailing_calls=_STEP_1F_TRAILING_CALLS),
            ),
            postconditions=(
                (frozenset({_SELF_REVIEWING_IMPLEMENTATION}), _postcondition_self_review_entered_durably),
                (frozenset({review_phase}), _postcondition_implementation_bundle_coherent),
            ),
        ),
        ExpectedOutcome(
            from_phase=_SELF_REVIEWING_IMPLEMENTATION, governing_version=version,
            action="/milestone-implement",
            to_any_of=frozenset({review_phase}),
            predicate=None, predicate_inputs=frozenset(),
            writer_calls=(
                WriterCall("record_bundle_generation", "milestone-implement.md",
                           "milestone-implement.md:309", WRITER_KIND_COMPLETION,
                           branch=BranchSpec(kind="step", label="4")),
            ),
            postconditions=((frozenset({review_phase}), _postcondition_implementation_bundle_coherent),),
        ),
    )


#: The eighteen rows, transcribed verbatim from the plan's own table (CP6B,
#: "An `ExpectedOutcome` is data, not prose"; row 7 added by revision 63's
#: B2, the `NoWorkItemYet` bootstrap; the four `"2.2"` plan-review rows
#: added by the `workflow-controller-protocol-2-2-compatibility` milestone's
#: CP1 -- each byte-identical to its `"2.1"` counterpart except for
#: `governing_version`, since `docs/ai-workflow/MILESTONE_WORKFLOW.md`'s own
#: two-stage plan-review protocol does not branch on `"2.1"` vs. `"2.2"` at
#: all; rows 12-18, the implementation stage, added by
#: `workflow-controller-automatic-lifecycle-orchestration`'s CP2 -- no `"1"`
#: row, since the `"1"` branch of `/milestone-implement` writes no state, so
#: a job could not be verified).
EXPECTED_OUTCOMES: tuple[ExpectedOutcome, ...] = (
    ExpectedOutcome(
        from_phase="PLANNING", governing_version="2.1", action="/milestone-plan",
        to_any_of=frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("publish_plan_revision", "milestone-plan.md", "milestone-plan.md:202",
                       WRITER_KIND_COMPLETION),
        ),
        postconditions=((frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}), _postcondition_plan_bundle_coherent),),
    ),
    ExpectedOutcome(
        from_phase="PLANNING", governing_version="2.2", action="/milestone-plan",
        to_any_of=frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("publish_plan_revision", "milestone-plan.md", "milestone-plan.md:202",
                       WRITER_KIND_COMPLETION),
        ),
        postconditions=((frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}), _postcondition_plan_bundle_coherent),),
    ),
    ExpectedOutcome(
        from_phase="PLANNING", governing_version="1", action="/milestone-plan",
        to_any_of=frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("publish_plan_revision", "milestone-plan.md", "milestone-plan.md:202",
                       WRITER_KIND_COMPLETION),
        ),
        postconditions=((frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}), _postcondition_plan_bundle_coherent),),
    ),
    ExpectedOutcome(
        from_phase="AWAITING_LOCAL_PLAN_REVIEW", governing_version="2.1", action="/review-plan",
        to_any_of=frozenset({
            "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW",
        }),
        predicate=_predicate_row3_block_feedback_current,
        predicate_inputs=frozenset({"bundle_manifest_bundle_id"}),
        writer_calls=(
            WriterCall("record_local_plan_review", "review-plan.md", "review-plan.md:107",
                       WRITER_KIND_COMPLETION, branch=BranchSpec(kind="bullet", label="BLOCK")),
        ),
    ),
    ExpectedOutcome(
        from_phase="AWAITING_LOCAL_PLAN_REVIEW", governing_version="2.2", action="/review-plan",
        to_any_of=frozenset({
            "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW",
        }),
        predicate=_predicate_row3_block_feedback_current,
        predicate_inputs=frozenset({"bundle_manifest_bundle_id"}),
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
        from_phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", governing_version="2.2",
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
        postconditions=((frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}), _postcondition_plan_bundle_coherent),),
    ),
    ExpectedOutcome(
        from_phase="REVISING_PLAN", governing_version="2.1", action="/apply-plan-review",
        to_any_of=frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("transition_to_awaiting_local_plan_review", "apply-plan-review.md",
                       "apply-plan-review.md:145", WRITER_KIND_COMPLETION),
        ),
        postconditions=((frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}), _postcondition_plan_bundle_coherent),),
    ),
    ExpectedOutcome(
        from_phase="REVISING_PLAN", governing_version="2.2", action="/apply-plan-review",
        to_any_of=frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("transition_to_awaiting_local_plan_review", "apply-plan-review.md",
                       "apply-plan-review.md:145", WRITER_KIND_COMPLETION),
        ),
        postconditions=((frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}), _postcondition_plan_bundle_coherent),),
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
        postconditions=((frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}), _postcondition_plan_bundle_coherent),),
    ),
    # Rows 12 and 14 ("2.1"), 13 and 15 ("2.2").
    *_milestone_implement_rows("2.1", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"),
    *_milestone_implement_rows("2.2", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"),
    # Row 16: the "2.2" authoritative `/review-implementation` branch (A6).
    ExpectedOutcome(
        from_phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW", governing_version="2.2",
        action="/review-implementation",
        to_any_of=frozenset({
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK",
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
        }),
        predicate=_predicate_local_implementation_block_current,
        predicate_inputs=frozenset({"bundle_manifest_bundle_id"}),
        writer_calls=(
            WriterCall("record_local_implementation_review", "review-implementation.md",
                       "review-implementation.md:459", WRITER_KIND_COMPLETION,
                       branch=BranchSpec(kind="bullet", label="BLOCK")),
        ),
        postconditions=(
            (frozenset({"AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"}),
             _postcondition_local_implementation_approve_recorded),
            (frozenset({"APPLYING_REVIEW_FEEDBACK"}), _postcondition_local_implementation_revise_recorded),
        ),
    ),
    # Row 17: manual-verdict ingestion (step 7).
    ExpectedOutcome(
        from_phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", governing_version="2.2",
        action="/record-manual-implementation-review",
        to_any_of=frozenset({"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("record_manual_implementation_review", "record-manual-implementation-review.md",
                       "record-manual-implementation-review.md:108", WRITER_KIND_COMPLETION,
                       branch=BranchSpec(kind="step", label="7")),
        ),
        postconditions=(
            (frozenset({"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"}),
             _postcondition_manual_implementation_approve_recorded),
            (frozenset({"APPLYING_REVIEW_FEEDBACK"}), _postcondition_manual_implementation_revise_recorded),
        ),
    ),
    # Row 18: the "2.2" post-fix regeneration (step 7).
    ExpectedOutcome(
        from_phase="APPLYING_REVIEW_FEEDBACK", governing_version="2.2",
        action="/apply-implementation-review",
        to_any_of=frozenset({"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}),
        predicate=None, predicate_inputs=frozenset(),
        writer_calls=(
            WriterCall("record_bundle_generation", "apply-implementation-review.md",
                       "apply-implementation-review.md:147", WRITER_KIND_COMPLETION,
                       branch=BranchSpec(kind="step", label="7")),
        ),
        postconditions=(
            (frozenset({"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}), _postcondition_implementation_bundle_regenerated),
        ),
    ),
)

_EXPECTED_OUTCOMES_BY_KEY: dict[tuple["str | Any", str | None, str], ExpectedOutcome] = {
    (eo.from_phase, eo.governing_version, eo.action): eo for eo in EXPECTED_OUTCOMES
}


#: `milestone-plan.md` step 6's `[2.1]` bind bullet in Workflow 2.6.0, the
#: sole writer of `AWAITING_LOCAL_PLAN_REVIEW` from `PLANNING`.
_BIND_FROM_MILESTONE_PLAN_2_6_0 = WriterCall(
    "bind_plan_review_bundle", "milestone-plan.md", "milestone-plan.md:445", WRITER_KIND_COMPLETION,
)

#: `apply-plan-review.md` step 7' in Workflow 2.6.0: the same bind, the sole
#: writer of `AWAITING_LOCAL_PLAN_REVIEW` from `REVISING_PLAN`
#: (`transition_to_awaiting_local_plan_review` is retired there).
_BIND_FROM_APPLY_PLAN_REVIEW_2_6_0 = WriterCall(
    "bind_plan_review_bundle", "apply-plan-review.md", "apply-plan-review.md:294", WRITER_KIND_COMPLETION,
)

#: The calls a `"1"` item's `publish_plan_revision` now precedes in Workflow
#: 2.6.0's `milestone-plan.md` (property 5's `trailing_calls` allowlist).
_STEP_6_BIND_TRAILING_CALLS: tuple[tuple[str, str], ...] = tuple(
    (function, "a [2.1] bullet a \"1\" item never reaches")
    for function in ("verify_plan_review_bundle", "state_transaction", "bind_plan_review_bundle")
)

#: Each release's writer declarations, where they differ from
#: :data:`EXPECTED_OUTCOMES` (the 2.5.1 table): exactly the rows property 5
#: flags against that release's command files.
_WRITER_CALLS_BY_RELEASE: Mapping[str, Mapping[tuple["str | Any", str | None, str], tuple[WriterCall, ...]]] = {
    "2.5.1": {},
    "2.6.0": {
        ("PLANNING", "2.1", "/milestone-plan"): (_BIND_FROM_MILESTONE_PLAN_2_6_0,),
        ("PLANNING", "2.2", "/milestone-plan"): (_BIND_FROM_MILESTONE_PLAN_2_6_0,),
        (NO_PHASE, None, "/milestone-plan"): (_BIND_FROM_MILESTONE_PLAN_2_6_0,),
        ("PLANNING", "1", "/milestone-plan"): (
            WriterCall("publish_plan_revision", "milestone-plan.md", "milestone-plan.md:372",
                       WRITER_KIND_COMPLETION, trailing_calls=_STEP_6_BIND_TRAILING_CALLS),
        ),
        ("REVISING_PLAN", "2.1", "/apply-plan-review"): (_BIND_FROM_APPLY_PLAN_REVIEW_2_6_0,),
        ("REVISING_PLAN", "2.2", "/apply-plan-review"): (_BIND_FROM_APPLY_PLAN_REVIEW_2_6_0,),
    },
}


def expected_outcomes_for(release: str) -> tuple[ExpectedOutcome, ...]:
    """:data:`EXPECTED_OUTCOMES` with ``release``'s own writer declarations
    substituted (`workflow-controller-workflow-2-6-integration` CP4,
    Design D): the table property 5 checks against that release's command
    files. Only ``writer_calls`` differ, and each substituted row keeps its
    branch, so the phases, predicates, postconditions and triples -- and
    so verification, which reads :data:`EXPECTED_OUTCOMES` -- are the same
    for every release. A release with no declarations is
    ``UnsupportedWorkflowVersionError``."""
    substitutions = _WRITER_CALLS_BY_RELEASE.get(release)
    if substitutions is None:
        raise UnsupportedWorkflowVersionError(
            f"the Controller declares no Workflow writers for release {release!r} "
            f"(declared releases: {sorted(_WRITER_CALLS_BY_RELEASE)})",
            evidence={"observed_workflow_version": release,
                      "declared_workflow_releases": sorted(_WRITER_CALLS_BY_RELEASE),
                      "reason": "no_writer_declarations"},
        )
    outcomes = []
    for eo in EXPECTED_OUTCOMES:
        writer_calls = substitutions.get((eo.from_phase, eo.governing_version, eo.action))
        if writer_calls is None:
            outcomes.append(eo)
            continue
        fields = {field.name: getattr(eo, field.name) for field in dataclasses.fields(eo)}
        fields["writer_calls"] = writer_calls
        outcomes.append(ExpectedOutcome(**fields))
    return tuple(outcomes)


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
      this property. **Property 3 also states a total, fail-closed
      derivation** (revision 65/66, step 8's own repair): every
      ``WriterCall`` on one row must declare the identical ``branch`` --
      a row whose entries disagree fails here, naming the row and every
      distinct value found, and so does a row that declares **no**
      ``writer_calls`` at all, which is the other shape that leaves
      :func:`_row_branch` without a single value to return
      (:func:`_row_branch` is what step 8's ``_verify_transition`` calls
      at runtime to read this same, already total, value back, and it
      raises for both shapes alike -- so both must be reported as data
      here rather than one of them reaching execution unvalidated).
    - **Postcondition shape** (`workflow-controller-worker-execution-
      hardening` CP3, made per-phase by `workflow-controller-automatic-
      lifecycle-orchestration` CP2): every ``postconditions`` entry pairs
      a non-empty phase set with a callable; each phase set is a subset of
      ``to_any_of``; the phase sets are pairwise disjoint; a row may
      declare none. A postcondition is not subject to the predicate's
      "iff self-loop" rule.
    - **``trailing_calls`` shape** (CP2): each entry is a
      ``(function identifier, non-empty justification)`` pair naming a
      function other than the declared call, at most once. Property 5
      checks the rest (that each one really occurs after the call).
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
        if not own_phase_reachable:
            # `O4` (MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 2): the
            # `NO_PHASE` exemption documented above is scoped to exactly
            # one check -- "`to_any_of` never contains `from_phase`", which
            # can never hold for row 7 by construction (`NO_PHASE` names no
            # real Workflow phase) -- not to "`predicate_inputs` declared
            # with no predicate", an independent malformation this table
            # would still want caught on a `NO_PHASE` row. Only the first
            # check is gated on `eo.from_phase is not NO_PHASE`; the second
            # is gated on `eo.predicate is None` instead (the condition its
            # own message names), which is what lets row 7's real
            # `predicate`+`predicate_inputs` pair -- a legitimate
            # defence-in-depth predicate, not an error -- stay clean under
            # the now-narrower `NO_PHASE` exemption.
            if eo.predicate is not None and eo.from_phase is not NO_PHASE:
                violations.append(
                    f"{key!r}: predicate declared but to_any_of never contains from_phase"
                )
            if eo.predicate is None and eo.predicate_inputs:
                violations.append(f"{key!r}: predicate_inputs declared with no predicate")
        if eo.predicate is None and eo.predicate_detail is not None:
            violations.append(f"{key!r}: predicate_detail declared with no predicate")

        if eo.predicate is not None:
            for wc in eo.writer_calls:
                if wc.kind != WRITER_KIND_COMPLETION:
                    violations.append(
                        f"{key!r}: predicate-bearing row carries a non-COMPLETION "
                        f"writer_call {wc.function!r} (kind={wc.kind!r})"
                    )

        row_branches = {wc.branch for wc in eo.writer_calls}
        if not eo.writer_calls:
            # The *other* shape that makes `_row_branch` non-total (and so
            # the other way a row reaches `_verify_transition`/
            # `_row2_verified` and raises `AssertionError` at execution
            # time instead of being reported as data here): zero declared
            # writer calls, which derives an empty branch set rather than
            # a disagreeing one. Property 3's derivation is only "total
            # and fail-closed" if this row fails validation too -- and
            # property 5 says nothing about it either, since its own
            # per-call loop simply does not run.
            violations.append(f"{key!r}: declares no writer_calls at all")
        elif len(row_branches) > 1:
            violations.append(
                f"{key!r}: writer_calls disagree on branch: {sorted(row_branches, key=repr)!r}"
            )

        violations.extend(_postconditions_violations(key, eo))
        for wc in eo.writer_calls:
            violations.extend(_trailing_calls_shape_violations(key, wc))
    return violations


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _postconditions_violations(key: tuple, eo: ExpectedOutcome) -> list[str]:
    """The per-phase postcondition shape (CP2): every entry is a
    ``(non-empty phase set, callable)`` pair, each phase set is a subset of
    ``to_any_of``, and the phase sets are pairwise disjoint -- so at most
    one postcondition ever applies to an observed phase. A row may declare
    none."""
    violations: list[str] = []
    claimed: set[str] = set()
    for index, entry in enumerate(eo.postconditions):
        if not (isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], frozenset)):
            violations.append(
                f"{key!r}: postconditions entry {index} is not a (frozenset of phases, "
                f"postcondition) pair: {entry!r}"
            )
            continue
        phases, postcondition = entry
        if not phases:
            violations.append(f"{key!r}: postconditions entry {index} declares an empty phase set")
        if not callable(postcondition):
            violations.append(
                f"{key!r}: postconditions entry {index} ({sorted(phases)!r}) declares no callable "
                f"postcondition: {postcondition!r}"
            )
        stray_phases = phases - eo.to_any_of
        if stray_phases:
            violations.append(
                f"{key!r}: postconditions phases {sorted(stray_phases)!r} are not members of to_any_of"
            )
        overlap = phases & claimed
        if overlap:
            violations.append(
                f"{key!r}: postconditions phase sets overlap on {sorted(overlap)!r}"
            )
        claimed |= phases
    return violations


def _trailing_calls_shape_violations(key: tuple, wc: WriterCall) -> list[str]:
    """``trailing_calls`` misuse visible without reading the frozen text:
    every entry is a ``(function identifier, non-empty justification)``
    pair, names a function other than the declared call itself, and
    appears once. Whether each named function actually occurs after the
    declared call is property 5's check."""
    violations: list[str] = []
    seen: set[str] = set()
    for entry in wc.trailing_calls:
        if not (isinstance(entry, tuple) and len(entry) == 2):
            violations.append(
                f"{key!r} ({wc.function!r}): trailing_calls entry {entry!r} is not a "
                f"(function, justification) pair"
            )
            continue
        function, justification = entry
        if not isinstance(function, str) or not _IDENTIFIER_RE.fullmatch(function):
            violations.append(
                f"{key!r} ({wc.function!r}): trailing_calls entry names {function!r}, which is "
                f"not a function identifier"
            )
            continue
        if not isinstance(justification, str) or not justification.strip():
            violations.append(
                f"{key!r} ({wc.function!r}): trailing_calls entry {function!r} carries no justification"
            )
        if function == wc.function:
            violations.append(
                f"{key!r} ({wc.function!r}): trailing_calls names the declared call itself, "
                f"which property 5 already admits"
            )
        if function in seen:
            violations.append(f"{key!r} ({wc.function!r}): trailing_calls names {function!r} twice")
        seen.add(function)
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
    other than a repeated call to the same declared function, or a
    function the call's own ``trailing_calls`` allowlist names) occurs
    later in plain file order within that same span. Every
    ``trailing_calls`` entry must itself occur there, after the declared
    call, or it is reported as a stale entry (CP2). ``repo_root`` is the
    Controller's own checkout (this repository is itself a frozen
    Workflow installation -- 2.5.1 since revision 64's baseline update --
    and its seventeen command files are the same external artifact a
    target managed repository carries; see
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
            allowed = {
                entry[0] for entry in wc.trailing_calls
                if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], str)
            }
            trailing_seen: set[str] = set()
            for i in range(located + 1, end):
                for m in _FURTHER_WRITE_RE.finditer(lines[i]):
                    name = m.group(1)
                    if name == wc.function:
                        continue
                    if name in allowed:
                        trailing_seen.add(name)
                        continue
                    violations.append(
                        f"{key!r} ({wc.function!r}): a further durable write to "
                        f"{name!r} occurs after it at {wc.file}:{i + 1}, within the "
                        f"same declared branch"
                    )
            for name in sorted(allowed - trailing_seen):
                violations.append(
                    f"{key!r} ({wc.function!r}): trailing_calls names {name!r}, which does not "
                    f"occur after the declared call in {wc.file}'s declared branch (lines "
                    f"{located + 1}-{end}) -- a stale allowlist entry"
                )
    return violations


# ---------------------------------------------------------------------------
# expected_transition (step 4's LAUNCHED-only addition).
# ---------------------------------------------------------------------------


def _expected_outcome_for(phase: Any, governing_workflow_version: str | None, decision: Decision) -> ExpectedOutcome:
    """The single :data:`EXPECTED_OUTCOMES` row an automatic ``decision``
    about to be launched corresponds to. The combined CP4/CP4B decision
    engine can only ever produce an automatic ``Decision`` for one of
    these eighteen triples (row 7's own `from_phase` is
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


def _observe_post_phase(managed_repo: Any, work_item_id: str | None, pre_state: dict) -> "str | Any":
    """A fresh post-state phase read, live from disk -- shared by
    `execute_step`'s own step 7 (CP6B) and CP7's own `resume` (through
    `_reconcile_launched`/`_reconcile_completed`), which need the identical
    bootstrap-aware read for the identical reason (CP6B's own scope note on
    step 7's "fresh `managed_repo.inspect`", carried over rather than
    duplicated a second time -- a duplicate copy is exactly what let CP7's
    own resume path drift from this rule until revision 64's B1 named it: a
    row-7 (`NoWorkItemYet`) record has no `WorkItemView` to re-select by
    id -- `work_item_id` is `None` for it -- so its own post-read is a
    direct key-set comparison against `pre_state["pre_work_item_keys"]`,
    never a second `select_work_item(work_item_id=None)` call: that call's
    own "more than one candidate" branch is `AmbiguousWorkItemError`
    (CP3), which would crash the caller on exactly the two-or-more-key case
    this checkpoint's own declared row-7 cases require to fail *closed*
    instead (`observed_phase_after=NO_PHASE`, never a member of any row's
    `to_any_of`, so verification fails with the ordinary
    `phase_not_in_to_any_of` reason). Row 7's own predicate
    (`_predicate_row7_new_work_item_created`) performs the identical
    key-set comparison independently, at verification time -- the two
    checks are deliberately redundant (defence in depth), not merged."""
    post_snapshot = target_state.read(managed_repo)
    if work_item_id is None:
        new_work_item_keys = frozenset(post_snapshot.work_items) - frozenset(
            pre_state.get("pre_work_item_keys") or ()
        )
        if len(new_work_item_keys) == 1:
            return post_snapshot.work_items[next(iter(new_work_item_keys))].phase
        return NO_PHASE
    post_work_item = target_state.select_work_item(post_snapshot, work_item_id=work_item_id)
    return post_work_item.phase


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
#: can produce it"), and still empty now that rows 12-18 automate the
#: implementation stage (`workflow-controller-automatic-lifecycle-
#: orchestration` CP2): every "worker stopped early" case either leaves the
#: phase unchanged (`FAILED` on execute; `INTERRUPTED` on a `LAUNCHED`
#: resume when `HEAD` is also unchanged) or moves `HEAD`/state without
#: verifying (`FAILED`/`UnreconcilableJobError`), exactly as at the plan
#: stage -- row 18's `/apply-implementation-review` never lists
#: `APPLYING_REVIEW_FEEDBACK` in its `to_any_of`. Kept as a per-row
#: mapping, not deleted, because a record written by a *future* generation
#: that does drive such a landing must still be classifiable by `resume`
#: (CP7)'s own closed status table.
_INCOMPLETE_EFFECT_PHASES: dict[tuple[str, str | None, str], frozenset[str]] = {}


# ---------------------------------------------------------------------------
# `workflow-controller-workflow-2-6-integration` CP3 -- the installed-release
# re-check, before every decision and before every verification (I3).
# ---------------------------------------------------------------------------

#: :func:`_row_clauses_failure`'s two Workflow reasons. Whenever
#: verification runs, at launch or at `resume`, each records a terminal
#: `FAILED` job carrying ``workflow_error``, and leaves no pending job.
WORKFLOW_RELEASE_CHANGED_REASON = "workflow_release_changed"
WORKFLOW_QUERY_FAILED_REASON = "workflow_query_failed"
WORKFLOW_ERROR_REASONS: frozenset[str] = frozenset({WORKFLOW_RELEASE_CHANGED_REASON, WORKFLOW_QUERY_FAILED_REASON})


def _installed_release(root: Path) -> tuple[str | None, dict | None]:
    """``(installed, None)``: the release the target's installation manifest
    declares right now (``managed_repo.installed_workflow_version``, a plain
    file read). ``(None, manifest_error)`` when it cannot be established:
    the manifest is missing or unreadable, and ``manifest_error`` is the
    parser error's ``{"code", "message"}``."""
    try:
        return installed_workflow_version(root), None
    except (UnmanagedRepositoryError, MalformedInstallationManifestError) as exc:
        return None, {"code": exc.code, "message": exc.message}


def _workflow_error(exc: WorkflowReleaseChangedError | WorkflowQueryError) -> dict:
    """``workflow_error``: the error's ``code``, ``message`` and full
    ``evidence``, as a job record carries it."""
    return {"code": exc.code, "message": exc.message, "evidence": exc.evidence}


def _verification_contract(root: Path, recorded: Any) -> BoundContract:
    """The contract a job verifies under: the release its record carries
    (``target_workflow_version``, written at launch from the admitted
    ``ManagedRepository``), bound once -- provided the target still runs
    that release. :class:`~controller.errors.WorkflowReleaseChangedError`
    when it does not, when the installed release cannot be established
    (``installed`` is ``None`` and ``manifest_error`` says why), or when
    the recorded value is missing or names a release with no contract.
    ``evidence`` is ``{"recorded", "installed"}`` plus the reason's own
    key."""
    installed, manifest_error = _installed_release(root)
    evidence_dict: dict = {"recorded": recorded, "installed": installed}
    if manifest_error is not None:
        evidence_dict["manifest_error"] = manifest_error
        raise WorkflowReleaseChangedError(
            f"the installed Workflow release of {root} cannot be established ({manifest_error['code']}: "
            f"{manifest_error['message']}); the job was launched under Workflow {recorded!r} and is not "
            "verified under any other",
            evidence=evidence_dict,
        )
    if installed != recorded:
        raise WorkflowReleaseChangedError(
            f"{root} now runs Workflow {installed!r}, not Workflow {recorded!r}, which the job was launched "
            "under; it is not verified under any other",
            evidence=evidence_dict,
        )
    try:
        return workflow_contract.bind_release(recorded)
    except UnsupportedWorkflowVersionError as exc:
        raise WorkflowReleaseChangedError(
            f"the job was launched under Workflow {recorded!r}, for which this Controller holds no "
            "Workflow contract; it is not verified",
            evidence={**evidence_dict, "contract_error": {"code": exc.code, "message": exc.message}},
        ) from exc


def _protocol_identity_change(managed_repo: Any) -> tuple[str, str, dict] | None:
    """The per-step re-check of a protocol target's identity (orchestration-
    protocol-v1 B.3, M1): the release ``describe`` reports and the managed-
    script digest map, derived afresh, against the identity ``inspect``
    admitted for this invocation. ``None`` for a legacy target or when they
    are equal; else ``(what, then, evidence)`` for
    :func:`_refuse_changed_release`. A file outside the installation record's
    ``managed`` map never enters the identity, so an edit to it is not a
    change. An identity that cannot be derived is a change as well: nothing
    is decided under a Workflow that cannot answer."""
    if getattr(managed_repo, "target_protocol", None) is None:
        return None
    admitted_release = managed_repo.target_protocol["release"]
    admitted_digests = dict(managed_repo.script_digests or {})
    try:
        now = protocol.identity(managed_repo.root)
    except (WorkflowProtocolFailedError, WorkflowProtocolUnsupportedError) as exc:
        return (f"{managed_repo.root}'s Workflow protocol script no longer answers ({exc.message}); this step was "
                f"admitted under Workflow {admitted_release}",
                "restore the Workflow's scripts (the Workflow Manager's update or repair), then re-run",
                {"protocol_error": {"code": exc.code, "message": exc.message, "evidence": exc.evidence}})
    changed = sorted(key for key in {*admitted_digests, *now.digests}
                     if admitted_digests.get(key) != now.digests.get(key))
    if now.release == admitted_release and not changed:
        return None
    return (f"{managed_repo.root}'s Workflow identity changed since this step was admitted: it reported release "
            f"{admitted_release} and now reports {now.release}"
            + (f"; managed scripts changed: {', '.join(changed)}" if changed else ""),
            "re-run: a fresh inspect admits the installed Workflow, or refuses it",
            {"admitted_protocol_release": admitted_release, "installed_protocol_release": now.release,
             "changed_scripts": changed})


def _refuse_changed_release(managed_repo: Any, preflight: Any, preflight_events: list[str]) -> None:
    """The installed-release re-check before every decision (I3): right
    after the repository preflight returns -- a ``Gate`` or a ``Proceed``;
    close-out can switch to a trunk that runs another release -- and before
    a preflight gate is recorded, the pre-state is captured or ``decide``
    runs, all of which can resolve the feedback path.

    Returns when the installed release is the one ``managed_repo.inspect``
    admitted. Otherwise raises
    :class:`~controller.errors.WorkflowReleaseChangedError` (exit 20):
    nothing is decided or launched and no job record is written. The
    evidence names what the preflight did -- ``preflight_action`` (its
    ``Proceed.action``, or ``gate``), ``preflight_gate`` (the gate's code, or
    ``None``) and ``preflight_events`` (the binding events it wrote in this
    step, in order) -- and the message says it in words, so a completed
    close-out is never mistaken for a failed one."""
    admitted = managed_repo.workflow_version
    installed, manifest_error = _installed_release(managed_repo.root)
    protocol_change = None
    if manifest_error is None and installed == admitted:
        protocol_change = _protocol_identity_change(managed_repo)
        if protocol_change is None:
            return
    is_gate = isinstance(preflight, milestone_branch.Gate)
    action = "gate" if is_gate else preflight.action
    evidence_dict: dict = {
        "admitted": admitted, "installed": installed, "preflight_action": action,
        "preflight_gate": preflight.code if is_gate else None, "preflight_events": list(preflight_events),
    }
    if protocol_change is not None:
        what, then, extra = protocol_change
        evidence_dict.update(extra)
    elif manifest_error is not None:
        evidence_dict["manifest_error"] = manifest_error
        what = (f"the installed Workflow release of {managed_repo.root} cannot be established "
                f"({manifest_error['code']}: {manifest_error['message']}); this step was admitted under "
                f"Workflow {admitted}")
        then = "restore .workflow-manager/installation.json, then re-run, and a fresh inspect admits it again"
    else:
        what = (f"{managed_repo.root} now runs Workflow {installed}, not Workflow {admitted}, which this step "
                "was admitted under")
        then = "re-run: a fresh inspect admits the installed release, or refuses it"
    sentences = [f"{what}. Only the decision was refused: nothing was decided or launched, and no job "
                 "record was written."]
    closed_out = not is_gate and action == "closed_out"
    if "closed" in preflight_events:
        if closed_out:
            sentences.append("A milestone close-out completed and was recorded.")
        else:
            returned = f"the {preflight.code} gate" if is_gate else f"the {action} action"
            sentences.append(f"A milestone close-out completed and was recorded; the repository preflight then "
                             f"returned {returned}.")
    if is_gate:
        sentences.append(f"The repository preflight returned the {preflight.code} gate, which was not recorded; "
                         "the next invocation rediscovers it.")
    elif action != "none" and not (closed_out and "closed" in preflight_events):
        sentences.append(f"The repository preflight's {action} action completed and was recorded.")
    sentences.append(f"Next: {then}.")
    raise WorkflowReleaseChangedError(" ".join(sentences), evidence=evidence_dict)


def _row_clauses_failure(
    *, root: Path, work_item_id: str | None, outcome: ExpectedOutcome, pre_state: dict,
    observed_phase_after: "str | Any", release: Any,
) -> tuple[str | None, str | None, str | None, dict | None]:
    """The phase, predicate and postcondition clauses of step 8's rule,
    stated once and shared by :func:`_verify_transition` (``execute_step``)
    and :func:`_row2_verified` (``resume``) -- so the two sites cannot
    drift again (the drift :func:`_row2_verified`'s own docstring records,
    revision 64's `B1`). The worker-outcome clause stays with each caller,
    since only they know whether an outcome exists at all.

    Returns ``(reason, postcondition_detail, predicate_detail,
    workflow_error)``: ``reason`` is ``None`` when every clause holds, else
    ``"workflow_release_changed"``, ``"phase_not_in_to_any_of"``,
    ``"predicate_not_satisfied"``, ``"postcondition_not_satisfied"`` or
    ``"workflow_query_failed"``; ``postcondition_detail`` is the
    postcondition's own detail string, set only for
    ``postcondition_not_satisfied``; ``predicate_detail`` is the first unmet
    condition a row's ``predicate_detail`` names (CP6), set only for
    ``predicate_not_satisfied`` on such a row; ``workflow_error`` (the
    error's ``code``, ``message`` and ``evidence``) is set only for the two
    Workflow reasons.

    **The Workflow contract** (``workflow-controller-workflow-2-6-integration``
    CP3). Before any clause, ``release`` -- the job record's
    ``target_workflow_version`` -- is checked against the installed release
    and bound once (:func:`_verification_contract`); every predicate,
    ``predicate_detail`` and postcondition receives that binding. A release
    that changed, or cannot be established, is ``workflow_release_changed``;
    a ``WorkflowQueryError`` from any clause is ``workflow_query_failed``.
    Both are caught here, the one helper launch and ``resume`` share, so
    neither can escape after the ``COMPLETED`` flush and leave the job
    pending; each caller records ``FAILED``. A 2.5.1 job never queries, so
    it verifies exactly as before.

    The predicate clause's trigger is keyed on the row's own
    :func:`_row_branch` (revision 65's repair, round 64's `B1`): evaluated
    **unconditionally** when that branch is ``None`` (row 7's own
    unconditional writer -- its ``from_phase`` is
    :data:`~controller.decision.NO_PHASE`, which can never equal a real
    observed phase), exactly when ``observed_phase_after`` equals
    ``outcome.from_phase`` otherwise (rows 3, 5, 12/13 and 16, whose
    writer runs on one named branch only). The postcondition clause (CP3)
    is evaluated after both: the one ``outcome.postconditions`` entry
    whose phase set contains ``observed_phase_after``, if any
    (:func:`_postcondition_for_phase`; per-phase since CP2)."""
    try:
        bound = _verification_contract(root, release)
    except WorkflowReleaseChangedError as exc:
        return WORKFLOW_RELEASE_CHANGED_REASON, None, None, _workflow_error(exc)
    if observed_phase_after not in outcome.to_any_of:
        return "phase_not_in_to_any_of", None, None, None
    branch = _row_branch(outcome)
    try:
        if outcome.predicate is not None and (branch is None or observed_phase_after == outcome.from_phase):
            if outcome.predicate_detail is not None:
                predicate_detail = outcome.predicate_detail(root, work_item_id, pre_state, bound)
                if predicate_detail is not None:
                    return "predicate_not_satisfied", None, predicate_detail, None
            elif not outcome.predicate(root, work_item_id, pre_state, bound):
                return "predicate_not_satisfied", None, None, None
        postcondition = _postcondition_for_phase(outcome, observed_phase_after)
        if postcondition is not None:
            satisfied, detail = postcondition(root, work_item_id, pre_state, bound)
            if not satisfied:
                return "postcondition_not_satisfied", detail, None, None
    except WorkflowQueryError as exc:
        return WORKFLOW_QUERY_FAILED_REASON, None, None, _workflow_error(exc)
    return None, None, None, None


def _verify_transition(
    *, root: Path, work_item_id: str | None, outcome: ExpectedOutcome, pre_state: dict,
    observed_phase_after: "str | Any", worker_outcome: str, release: Any,
) -> tuple[bool, dict]:
    """Step 8's rule, stated in full and in one form (CP6B): ``verified``
    is ``True`` iff ``worker_outcome`` is ``SUCCESS`` or ``INTERRUPTED``,
    **and** every clause of :func:`_row_clauses_failure` holds --
    ``observed_phase_after`` is in ``outcome.to_any_of``, the row's
    evidence predicate (if any, on its own trigger) holds, and the row's
    postcondition (if any, for the observed phase) is satisfied --
    each evaluated fresh, against disk, never against process memory,
    since `resume` has none. Returns ``(verified, evidence)`` --
    ``evidence`` is a ``TransitionNotObservedError``-shaped dict naming the
    expected set, the observed phase, the worker outcome, the failing
    clause as ``reason``, and (for the postcondition clause) its
    ``postcondition_detail``; ``{}`` when ``verified``. ``release`` is the
    job record's ``target_workflow_version``, the contract the clauses are
    evaluated under (CP3)."""
    workflow_error = None
    if worker_outcome not in _VERIFYING_WORKER_OUTCOMES:
        reason, postcondition_detail, predicate_detail = "worker_outcome", None, None
    else:
        reason, postcondition_detail, predicate_detail, workflow_error = _row_clauses_failure(
            root=root, work_item_id=work_item_id, outcome=outcome, pre_state=pre_state,
            observed_phase_after=observed_phase_after, release=release,
        )
        if reason is None:
            return True, {}
    return False, _transition_not_observed_evidence(
        outcome=outcome, observed_phase_after=phase_to_wire(observed_phase_after),
        worker_outcome=worker_outcome, reason=reason, postcondition_detail=postcondition_detail,
        predicate_detail=predicate_detail, workflow_error=workflow_error,
    )


def _transition_not_observed_evidence(
    *, outcome: ExpectedOutcome, observed_phase_after: str, worker_outcome: str | None,
    reason: str, postcondition_detail: str | None, predicate_detail: str | None = None,
    workflow_error: dict | None = None,
) -> dict:
    """The ``TransitionNotObservedError``-shaped ``reconciliation_evidence``
    ``execute_step``, ``_reconcile_completed`` and (for the two Workflow
    reasons) ``_reconcile_launched`` write. The optional
    ``postcondition_detail`` key is present only on the
    ``postcondition_not_satisfied`` reason, the optional
    ``predicate_detail`` key (CP6) only on ``predicate_not_satisfied`` for a
    row whose predicate states its reason, and the optional
    ``workflow_error`` key (``workflow-controller-workflow-2-6-integration``
    CP3) only on ``workflow_release_changed`` and ``workflow_query_failed``."""
    evidence_dict = {
        "code": "TransitionNotObservedError",
        "reason": reason,
        "expected_to_any_of": sorted(outcome.to_any_of),
        "observed_phase": observed_phase_after,
        "worker_outcome": worker_outcome,
    }
    if postcondition_detail is not None:
        evidence_dict["postcondition_detail"] = postcondition_detail
    if predicate_detail is not None:
        evidence_dict["predicate_detail"] = predicate_detail
    if workflow_error is not None:
        evidence_dict["workflow_error"] = workflow_error
    return evidence_dict


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

    # A `null` `work_item_id` is not an unresolvable subject (revision 64,
    # round 63's B2): it is the declared literal for a row-7 (`NoWorkItemYet`
    # bootstrap) record -- frozen Workflow alone derives the id, the
    # Controller never supplies one -- whose own subject is the target
    # repository the two checks above already confirmed resolvable, not a
    # name to look up in `snapshot.work_items`. A row-7 record is
    # distinguishable from a malformed one without guessing, because
    # `expected_transition.from` reconstructs to the NO_PHASE wire literal
    # exactly for those records and to a real phase string for every other
    # row: "`work_item_id` is null" and "`expected_transition.from` is the
    # NO_PHASE wire literal" must agree, and a record in which one holds and
    # the other does not is itself a refusal, naming both fields. A `PLANNED`
    # bootstrap record carries no `expected_transition` at all yet (step 4's
    # own `LAUNCHED`-only addition), so there is nothing to disagree with --
    # `null` alone is enough for that record.
    expected_transition = record.get("expected_transition")
    expected_from = expected_transition.get("from") if isinstance(expected_transition, dict) else None
    if work_item_id is None:
        if expected_transition is not None and expected_from != NO_PHASE_WIRE:
            return _invalid(
                record, case=2, code="StaleJobRecordError",
                reason="null_work_item_id_disagrees_with_expected_transition",
                message=(
                    f"job {record.get('job_id')!r} carries work_item_id=null but its "
                    f"expected_transition.from is {expected_from!r}, not the NO_PHASE wire "
                    f"literal {NO_PHASE_WIRE!r} -- a null work_item_id is only valid for a "
                    f"row-7 bootstrap record"
                ),
                evidence={"job_id": record.get("job_id"), "work_item_id": work_item_id,
                          "expected_transition_from": expected_from},
            )
    else:
        if expected_transition is not None and expected_from == NO_PHASE_WIRE:
            return _invalid(
                record, case=2, code="StaleJobRecordError",
                reason="expected_transition_no_phase_disagrees_with_work_item_id",
                message=(
                    f"job {record.get('job_id')!r} carries expected_transition.from="
                    f"{NO_PHASE_WIRE!r} but a non-null work_item_id {work_item_id!r} -- the "
                    f"NO_PHASE wire literal is only valid for a null-work_item_id bootstrap "
                    f"record"
                ),
                evidence={"job_id": record.get("job_id"), "work_item_id": work_item_id,
                          "expected_transition_from": expected_from},
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
    if status in (STATUS_LAUNCHED, STATUS_COMPLETED):
        worker_state_breach = _worker_state_breach(record, status)
        if worker_state_breach is not None:
            reason, message, breach_evidence = worker_state_breach
            return _invalid(
                record, case=3, code="StaleJobRecordError", reason=reason,
                message=f"job {record.get('job_id')!r} is {status!r} but {message}",
                evidence={"job_id": record.get("job_id"), "status": status, **breach_evidence},
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


#: The worker states a record may carry an ``ending_offset`` at: the
#: supervisor flushed it with ``ENDING``, and a worker whose anchor was lost
#: reaches ``DRAINING``/``ENDED`` without one (plan D).
_ENDED_SESSION_WORKER_STATES = frozenset({worker.ENDING, worker.DRAINING, worker.ENDED})

#: The supervisor facts flushed together with ``ENDING`` (plan C), each of
#: which therefore needs ``ending_offset`` beside it.
_ENDING_FACTS = ("wakeup_overdue_declared_at", "command_lifecycle_overdue_declared_at", "settled_wakeups")


def _worker_state_breach(record: JobRecord, status: str) -> tuple[str, str, dict] | None:
    """Validation case 3's ``worker_state`` rule for a ``LAUNCHED``/
    ``COMPLETED`` record (``workflow-controller-worker-lifecycle-ownership``
    plan D), as ``(reason, message, evidence)``, or ``None``. A record
    without ``worker_state`` (a 1.2.x shape) is judged on its supervisor
    facts alone, which it never carries."""
    worker_state = record.get("worker_state")
    state = None
    if "worker_state" in record:
        state = worker_state.get("state") if isinstance(worker_state, Mapping) else None
        if state not in worker.WORKER_STATES:
            return ("worker_state_invalid",
                    f"its worker_state {state!r} is not one of {list(worker.WORKER_STATES)}",
                    {"worker_state": state})
        if status == STATUS_COMPLETED and state != worker.ENDED:
            return ("worker_state_not_ended",
                    f"its worker_state is {state!r} -- COMPLETED is written only after the worker ENDED",
                    {"worker_state": state})
    has_offset = record.get("ending_offset") is not None
    if has_offset and state not in _ENDED_SESSION_WORKER_STATES:
        return ("ending_offset_before_ending",
                f"it carries ending_offset with worker_state {state!r}, not one of "
                f"{sorted(_ENDED_SESSION_WORKER_STATES)}",
                {"worker_state": state, "ending_offset": record.get("ending_offset")})
    present = [key for key in _ENDING_FACTS
               if (record.get(key) not in (None, []) if key == "settled_wakeups" else record.get(key) is not None)]
    if present and not has_offset:
        return ("ending_fact_without_ending_offset",
                f"it carries {', '.join(present)} without ending_offset -- they are flushed with ENDING",
                {"facts": present})
    if record.get("command_lifecycle_overdue_declared_at") is not None:
        command_uuid = record.get("command_lifecycle_overdue_command_uuid")
        if not isinstance(command_uuid, str) or not command_uuid:
            return ("command_lifecycle_overdue_without_command_uuid",
                    f"its command_lifecycle_overdue_declared_at names no command_uuid ({command_uuid!r})",
                    {"command_lifecycle_overdue_command_uuid": command_uuid})
    if "settled_wakeups" in record:
        settled = record.get("settled_wakeups")
        if not isinstance(settled, list) or not all(isinstance(item, str) and item for item in settled) \
                or len(set(settled)) != len(settled):
            return ("settled_wakeups_invalid",
                    f"its settled_wakeups {settled!r} is not a list of distinct non-empty strings",
                    {"settled_wakeups": settled})
    return None


def _expected_outcome_for_record(record: JobRecord) -> ExpectedOutcome:
    """The single :data:`EXPECTED_OUTCOMES` row a ``LAUNCHED``/``COMPLETED``
    record's own ``pre_state`` and ``selected_action`` correspond to -- the
    resume-time mirror of :func:`_expected_outcome_for`, reading from a
    persisted record instead of a live ``work_item``/``Decision`` pair. A
    miss here is an invariant violation, never ordinary control flow: by
    the time :func:`resume` calls this, :func:`validate_record` has
    already confirmed the record is a genuine Generation-1 write (case 1's
    ``controller_generation`` check), and `execute_step` never launches a
    worker for any key outside this table.

    ``pre_state["phase"]`` is read off the persisted record, so it is
    already in its durable (wire) form -- ``"__NO_PHASE__"`` for a row-7
    (bootstrap) record, every real phase's own name otherwise
    (:func:`~controller.job._durable_pre_state`) -- and is mapped back
    through :func:`~controller.decision.phase_from_wire` before the table
    lookup, the same single reader every "no phase" site on the resume
    path uses. :data:`EXPECTED_OUTCOMES`' own table is keyed on the
    in-memory :data:`~controller.decision.NO_PHASE` sentinel for row 7,
    never on its wire literal, so looking the wire string up directly
    would always miss."""
    pre_state = record.get("pre_state") or {}
    selected_action = record.get("selected_action") or {}
    command = selected_action.get("command")
    command_token = command.split()[0] if isinstance(command, str) and command else None
    phase = phase_from_wire(pre_state.get("phase"))
    key = (phase, pre_state.get("governing_workflow_version"), command_token)
    outcome = _EXPECTED_OUTCOMES_BY_KEY.get(key)
    if outcome is None:
        raise AssertionError(
            f"resume reached a non-terminal record with no known expected transition: "
            f"job_id={record.get('job_id')!r} key={key!r}"
        )
    return outcome


def _row2_verified(
    *, root: Path, work_item_id: str, outcome: ExpectedOutcome, pre_state: dict,
    observed_phase_after: str, status: str, worker_outcome: str | None, release: Any,
) -> tuple[bool, str | None, str | None, str | None, dict | None]:
    """The ``LAUNCHED``/``COMPLETED`` row's own rule (CP7's reconciliation
    table, row 2), stated once for both statuses. ``worker_outcome`` is
    guaranteed absent on a ``LAUNCHED`` record and present-and-known on a
    ``COMPLETED`` one by the time this runs (:func:`validate_record`'s own
    case 3), so a ``LAUNCHED`` record's own clause-1 is always satisfied --
    stated positively here rather than by substituting a fake outcome
    value, so the returned reason (when unverified) never misreports what
    the record actually carried. Returns ``(verified, reason,
    postcondition_detail, predicate_detail, workflow_error)`` -- ``reason``
    is one of ``"worker_outcome"``, ``"workflow_release_changed"``,
    ``"phase_not_in_to_any_of"``, ``"predicate_not_satisfied"``,
    ``"postcondition_not_satisfied"`` or ``"workflow_query_failed"``,
    ``None`` when verified; ``postcondition_detail`` is set only for
    ``postcondition_not_satisfied``, ``predicate_detail`` (CP6) only for
    ``predicate_not_satisfied``, and ``workflow_error``
    (``workflow-controller-workflow-2-6-integration`` CP3) only for the two
    Workflow reasons. ``release`` is the record's
    ``target_workflow_version``.

    Every clause after the worker-outcome one is
    :func:`_row_clauses_failure`, the helper :func:`_verify_transition`
    also calls -- this function used to restate the predicate trigger
    itself, on raw phase equality alone, which is what left row 7's
    predicate declared and never evaluated on the resume path (revision
    64's `B1`); sharing the helper is what keeps the two sites from
    drifting again."""
    if status == STATUS_COMPLETED:
        outcome_ok = worker_outcome in _VERIFYING_WORKER_OUTCOMES
    else:
        outcome_ok = True  # STATUS_LAUNCHED, validated absent by case 3.
    if not outcome_ok:
        return False, "worker_outcome", None, None, None
    reason, postcondition_detail, predicate_detail, workflow_error = _row_clauses_failure(
        root=root, work_item_id=work_item_id, outcome=outcome, pre_state=pre_state,
        observed_phase_after=observed_phase_after, release=release,
    )
    return reason is None, reason, postcondition_detail, predicate_detail, workflow_error


def _reconcile_planned(record: JobRecord, *, runtime_root: Path) -> JobRecord:
    """Row 1: ``PLANNED``, anything observed -- nothing can have happened,
    since the record was flushed before the launch was even prepared.
    Mark ``INTERRUPTED`` and allow a fresh ``step``. No observed reality is
    read at all (there is none to read: no ``expected_transition`` was
    ever written for this record)."""
    now = _now()
    reconciled = {**record, "status": STATUS_INTERRUPTED, "reconciled_at": now, "updated_at": now}
    return _persist(runtime_root, record["job_id"], reconciled, event="reconciled",
                    details=_reconciled_details(reconciled))


def _reconcile_launched(record: JobRecord, *, managed_repo: Any, runtime_root: Path) -> JobRecord:
    """Rows 2-4: ``LAUNCHED``. A fresh post-state re-read (never the
    ``pre_state`` this record itself captured) decides between "already
    succeeded" (row 2, never relaunch), "nothing durable happened" (row 3,
    ``INTERRUPTED``, a fresh ``step`` may retry), and "cannot be
    reconciled" (row 4, :class:`~controller.errors.UnreconcilableJobError`,
    fail closed and require a human). The post-state read is
    :func:`_observe_post_phase`, the bootstrap-aware helper shared with
    ``execute_step``'s own step 7 (CP6B) -- a row-7 record's own
    ``work_item_id`` is ``None``, so this is never a second
    ``select_work_item(work_item_id=None)`` call, whose own "more than one
    candidate" branch is ``AmbiguousWorkItemError`` (CP3), which would
    abort the whole ``resume`` call on exactly the two-key case row 7's own
    declared cases require to fail closed instead (revision 64's `B1`).

    The two Workflow reasons (``workflow-controller-workflow-2-6-integration``
    CP3) come first among the unverified outcomes: a release that changed
    or cannot be established, or a query that cannot be answered, means the
    transition cannot be judged either way, so the record is ``FAILED``
    with that evidence -- never ``INTERRUPTED`` (a fresh ``step`` would
    retry blind) and never ``UnreconcilableJobError``."""
    if isinstance(record.get("protocol"), dict):
        return _reconcile_protocol(record, managed_repo=managed_repo, runtime_root=runtime_root)
    root = managed_repo.root
    work_item_id = record["work_item_id"]
    pre_state = record.get("pre_state") or {}
    outcome = _expected_outcome_for_record(record)

    observed_phase_after = _observe_post_phase(managed_repo, work_item_id, pre_state)
    observed_phase_after_wire = phase_to_wire(observed_phase_after)
    observed_head = _current_head(root)

    verified, reason, postcondition_detail, predicate_detail, workflow_error = _row2_verified(
        root=root, work_item_id=work_item_id, outcome=outcome, pre_state=pre_state,
        observed_phase_after=observed_phase_after, status=STATUS_LAUNCHED, worker_outcome=None,
        release=record.get("target_workflow_version"),
    )
    now = _now()
    branch_violation = _branch_invariant_violation(root, runtime_root, record) if verified else None
    if branch_violation is not None:
        return _branch_violation_failed(record, runtime_root, observed_phase_after_wire, branch_violation, now)
    if verified:
        reconciled = {
            **record, "status": STATUS_FINISHED, "transition_verified": True,
            "observed_phase_after": observed_phase_after_wire, "reconciled_at": now, "updated_at": now,
        }
        return _persist(runtime_root, record["job_id"], reconciled, event="reconciled",
                        details=_reconciled_details(reconciled))

    if reason in WORKFLOW_ERROR_REASONS:
        failed = {
            **record, "status": STATUS_FAILED, "transition_verified": False,
            "observed_phase_after": observed_phase_after_wire,
            "reconciliation_evidence": _transition_not_observed_evidence(
                outcome=outcome, observed_phase_after=observed_phase_after_wire, worker_outcome=None,
                reason=reason, postcondition_detail=None, workflow_error=workflow_error,
            ),
            "reconciled_at": now, "updated_at": now,
        }
        return _persist(runtime_root, record["job_id"], failed, event="reconciled",
                        details=_reconciled_details(failed))

    phase_unchanged = observed_phase_after_wire == pre_state.get("phase")
    head_unchanged = observed_head == pre_state.get("target_head")
    if phase_unchanged and head_unchanged:
        reconciled = {
            **record, "status": STATUS_INTERRUPTED, "observed_phase_after": observed_phase_after_wire,
            "reconciled_at": now, "updated_at": now,
        }
        return _persist(runtime_root, record["job_id"], reconciled, event="reconciled",
                        details=_reconciled_details(reconciled))

    unreconcilable_evidence = {
        "job_id": record["job_id"], "work_item_id": work_item_id,
        "pre_phase": pre_state.get("phase"), "observed_phase_after": observed_phase_after_wire,
        "pre_target_head": pre_state.get("target_head"), "observed_target_head": observed_head,
    }
    message = (
        f"job {record['job_id']!r} for {work_item_id!r} cannot be reconciled: pre-phase "
        f"{pre_state.get('phase')!r} -> observed {observed_phase_after_wire!r}, pre-state target_head "
        f"{pre_state.get('target_head')!r} -> observed {observed_head!r}"
    )
    # CP3: moved state that failed only its artifact postcondition says why
    # it did not verify -- the one purpose `_row2_verified`'s reason is not
    # discarded for here.
    if reason == "postcondition_not_satisfied":
        unreconcilable_evidence["postcondition_detail"] = postcondition_detail
        message += f"; postcondition not satisfied: {postcondition_detail}"
    if predicate_detail is not None:
        # CP6: likewise a same-phase self-loop that did not verify names
        # the first unmet condition of its predicate.
        unreconcilable_evidence["predicate_detail"] = predicate_detail
        message += f"; predicate not satisfied: {predicate_detail}"
    if "lifecycle_lock" in record:
        # Automatic-lifecycle-orchestration CP5: the terminal disposition.
        # This record was written under the lifecycle lock, so its worker
        # inherited the descriptor; `resume` holds the lock now, and the
        # liveness verdict was `inactive` (or no worker process was ever
        # recorded) -- the worker has definitively ended, so "cannot be
        # reconciled" is a verdict about a finished job. Persist it FAILED
        # first, so the next `step` decides from evidence instead of
        # refusing on this record forever; then raise as before, so this
        # `resume` still exits 20 and a human sees it once.
        failed = {
            **record, "status": STATUS_FAILED, "transition_verified": False,
            "observed_phase_after": observed_phase_after_wire,
            "reconciliation_evidence": {"code": "UnreconcilableJobError", **unreconcilable_evidence},
            "reconciled_at": now, "updated_at": now,
        }
        _persist(runtime_root, record["job_id"], failed, event="reconciled", details=_reconciled_details(failed))
        message += (
            " -- the worker has ended (the lifecycle lock is free and no running member of its "
            "process group is observed), so the record is now FAILED; the next `step` decides "
            "from the target's evidence"
        )
    else:
        message += (
            f" -- this record was written before the lifecycle lock existed, so nothing proves its "
            f"worker gone; once you have confirmed it has ended, "
            f"`{_abandon_command(record['job_id'], root)}` marks it FAILED"
        )
    raise UnreconcilableJobError(message, evidence=unreconcilable_evidence)


def _branch_violation_failed(record: JobRecord, runtime_root: Path, observed_phase_after_wire: Any,
                             violation: dict, now: str) -> JobRecord:
    """A reconciled job whose transition verified but whose post-step
    branch verification did not (CP8): ``FAILED``, never ``FINISHED``."""
    failed = {
        **record, "status": STATUS_FAILED, "transition_verified": False,
        "observed_phase_after": observed_phase_after_wire, "reconciliation_evidence": violation,
        "reconciled_at": now, "updated_at": now,
    }
    return _persist(runtime_root, record["job_id"], failed, event="reconciled", details=_reconciled_details(failed))


# ---------------------------------------------------------------------------
# `workflow-controller-orchestration-protocol-v1` CP5 -- the outcome of a
# protocol job comes from the Workflow's own `reconcile` (plan E), on launch
# and on `resume`; no release contract names a protocol target's phases.
# ---------------------------------------------------------------------------

WORKFLOW_PROTOCOL_FAILED_REASON = "workflow_protocol_failed"
RECONCILE_INVALID_REASON = "reconcile_invalid"
COMPLETION_NOT_COMMITTED_REASON = "completion_not_committed_at_head"
_RECONCILE_CLASSES = frozenset({"progress", "gate_reached", "no_progress", "invalid"})
#: The one action whose `progress` answer the Controller backs with the
#: committed-state fact (plan E.3).
_CHECKPOINT_ACTION = "implementation.checkpoint"


def _protocol_failure_evidence(reason: str, *, observed_phase: str, worker_outcome: str | None, **extra: Any) -> dict:
    """The ``TransitionNotObservedError``-shaped ``reconciliation_evidence``
    of a protocol job: there is no expected phase set, only the ``reason``."""
    return {"code": "TransitionNotObservedError", "reason": reason, "expected_to_any_of": [],
            "observed_phase": observed_phase, "worker_outcome": worker_outcome, **extra}


def _protocol_release_change(root: Path, record: Mapping) -> dict | None:
    """The job record's release identity against the target's, derived afresh
    (plan B.3/E.1, K1): ``None`` when the release and the managed-script
    digest map are the ones the job was decided under, else the
    ``workflow_error`` of the change. A Workflow that cannot answer, or an
    installation manifest that cannot be read, is a change as well: nothing
    is judged under an identity that cannot be established. A non-Workflow
    ``scripts/extra.py`` never enters the map."""
    block = record["protocol"]
    recorded_release = block.get("workflow_release")
    recorded_digests = dict(block.get("script_sha256") or {})
    installed, manifest_error = _installed_release(root)
    if manifest_error is not None:
        return {"code": WorkflowReleaseChangedError.code, "message": (
            f"the installed Workflow release of {root} cannot be established ({manifest_error['code']}: "
            f"{manifest_error['message']}); the job was decided under Workflow {recorded_release}"),
            "evidence": {"recorded": recorded_release, "installed": None, "manifest_error": manifest_error}}
    try:
        now = protocol.identity(root)
    except (WorkflowProtocolFailedError, WorkflowProtocolUnsupportedError) as exc:
        return {"code": WorkflowReleaseChangedError.code, "message": (
            f"{root}'s Workflow protocol script no longer answers ({exc.message}); the job was decided under "
            f"Workflow {recorded_release}"),
            "evidence": {"recorded": recorded_release, "protocol_error": {
                "code": exc.code, "message": exc.message, "evidence": exc.evidence}}}
    changed = sorted(key for key in {*recorded_digests, *now.digests}
                     if recorded_digests.get(key) != now.digests.get(key))
    if now.release == recorded_release and not changed and installed == record.get("target_workflow_version"):
        return None
    return {"code": WorkflowReleaseChangedError.code, "message": (
        f"{root} now reports Workflow {now.release} (installed {installed}), not Workflow {recorded_release}, which "
        "the job was decided under" + (f"; managed scripts changed: {', '.join(changed)}" if changed else "")
        + "; it is not reconciled under any other"),
        "evidence": {"recorded": recorded_release, "installed": installed, "reported": now.release,
                     "changed_scripts": changed}}


def _protocol_observed_phase(managed_repo: Any, record: Mapping) -> str:
    """The phase a protocol job's work item is at now, in wire form; the
    pre-state's own when it cannot be read."""
    pre_state = record.get("pre_state") or {}
    try:
        return phase_to_wire(_observe_post_phase(managed_repo, record.get("work_item_id"), pre_state))
    except ControllerError:
        return str(pre_state.get("phase"))


def _protocol_uncommitted_facts(managed_repo: Any, work_item_id: str | None) -> tuple[str, ...]:
    """Plan E.3's committed-state fact for a checkpoint ``progress``: what the
    working tree records ``COMPLETE`` that ``HEAD`` does not."""
    snapshot = target_state.read(managed_repo)
    work_item = target_state.select_work_item(snapshot, work_item_id=work_item_id)
    return evidence.uncommitted_implementation_state(managed_repo.root, work_item)


def _protocol_resolution(record: Mapping, *, managed_repo: Any, runtime_root: Path,
                         worker_outcome: str | None) -> dict:
    """What a protocol job's outcome writes, as record fields (plan E.1):
    ``status``, ``transition_verified``, ``observed_phase_after``, the
    ``protocol`` block with ``reconcile`` filled and, for a failure,
    ``reconciliation_evidence``. ``worker_outcome`` is ``None`` for a record
    whose worker's end was never recorded (a ``LAUNCHED`` job at ``resume``).

    Order, as the table fixes it: the release identity first, before the
    worker outcome is read, then a failed or timed-out worker (no state is
    trusted, ``reconcile`` is not called), then ``reconcile`` from a private
    copy equal to the recorded map."""
    root = managed_repo.root
    block = dict(record["protocol"])
    work_item_id = record.get("work_item_id")
    pre_phase = str((record.get("pre_state") or {}).get("phase"))

    def failed(reason: str, observed: str, reconcile: Any = None, **extra: Any) -> dict:
        return {
            "status": STATUS_FAILED, "transition_verified": False, "observed_phase_after": observed,
            "protocol": {**block, "reconcile": reconcile},
            "reconciliation_evidence": _protocol_failure_evidence(
                reason, observed_phase=observed, worker_outcome=worker_outcome, **extra),
        }

    change = _protocol_release_change(root, record)
    if change is not None:
        return failed(WORKFLOW_RELEASE_CHANGED_REASON, _protocol_observed_phase(managed_repo, record),
                      workflow_error=change)
    if worker_outcome is not None and worker_outcome not in _VERIFYING_WORKER_OUTCOMES:
        return failed("worker_outcome", _protocol_observed_phase(managed_repo, record))

    decision_rel = f"jobs/{record['job_id']}/decision.json"
    decision_path = runtime.write_json(runtime_root, decision_rel, dict(block["decision"]))
    try:
        answer = protocol.reconcile(root, decision_path, work_item_id,
                                    expected_digests=dict(block.get("script_sha256") or {}))
    except WorkflowReleaseChangedError as exc:
        return failed(WORKFLOW_RELEASE_CHANGED_REASON, _protocol_observed_phase(managed_repo, record),
                      workflow_error={"code": exc.code, "message": exc.message, "evidence": exc.evidence})
    except (WorkflowProtocolFailedError, WorkflowProtocolUnsupportedError) as exc:
        return failed(WORKFLOW_PROTOCOL_FAILED_REASON, _protocol_observed_phase(managed_repo, record),
                      workflow_error={"code": exc.code, "message": exc.message, "evidence": exc.evidence})
    summary = {
        "class": answer.outcome,
        "invalid_reasons": [{"code": code, "text": text} for code, text in answer.invalid_reasons],
        "evidence": dict(answer.evidence),
        "from": None if answer.from_ is None else dataclasses.asdict(answer.from_),
        "to": None if answer.to is None else dataclasses.asdict(answer.to),
        "next": answer.next,
    }
    observed = pre_phase if answer.to is None else answer.to.phase
    if answer.outcome not in _RECONCILE_CLASSES:
        return failed(WORKFLOW_PROTOCOL_FAILED_REASON, observed, summary, workflow_error={
            "code": WorkflowProtocolFailedError.code,
            "message": f"reconcile answered the class {answer.outcome!r}, which this Controller does not know",
            "evidence": {"reason": "protocol_unknown_reconcile_class", "class": answer.outcome}})
    if answer.outcome == "invalid":
        return failed(RECONCILE_INVALID_REASON, observed, summary, reconcile_class="invalid",
                      invalid_reasons=summary["invalid_reasons"])
    if answer.outcome == "progress" and block.get("action_id") == _CHECKPOINT_ACTION:
        facts = _protocol_uncommitted_facts(managed_repo, work_item_id)
        if facts:
            return failed(COMPLETION_NOT_COMMITTED_REASON, observed, summary, facts=list(facts))
    if answer.outcome == "no_progress" and worker_outcome != "SUCCESS":
        # An interrupted worker, or one whose end was never recorded, that
        # moved nothing: the existing interrupted-job handling.
        return {"status": STATUS_INTERRUPTED, "observed_phase_after": observed,
                "protocol": {**block, "reconcile": summary}}
    if answer.outcome == "no_progress":
        block["progress"] = "none"
    return {"status": STATUS_FINISHED, "transition_verified": True, "observed_phase_after": observed,
            "protocol": {**block, "reconcile": summary}}


def _reconcile_protocol(record: JobRecord, *, managed_repo: Any, runtime_root: Path) -> JobRecord:
    """``resume``'s protocol branch for a ``LAUNCHED`` or ``COMPLETED`` job:
    the same resolution as the launch path, from the stored decision. A
    post-verification branch violation fails the job, as for a legacy one."""
    worker_outcome = record.get("worker_outcome") if record.get("status") == STATUS_COMPLETED else None
    fields = _protocol_resolution(record, managed_repo=managed_repo, runtime_root=runtime_root,
                                  worker_outcome=worker_outcome)
    now = _now()
    if fields["status"] == STATUS_FINISHED:
        violation = _branch_invariant_violation(managed_repo.root, runtime_root, record)
        if violation is not None:
            fields = {**fields, "status": STATUS_FAILED, "transition_verified": False,
                      "reconciliation_evidence": violation}
    reconciled = {**record, **fields, "reconciled_at": now, "updated_at": now}
    return _persist(runtime_root, record["job_id"], reconciled, event="reconciled",
                    details=_reconciled_details(reconciled))


def _resume_drifted_protocol(record: JobRecord, *, managed_repo: Any, runtime_root: Path) -> JobRecord:
    """A pending protocol job under a drifted installation whose managed-file
    digest map differs from its record's: ``FAILED``
    ``workflow_release_changed``, from the manifest and the files' bytes
    alone -- no Workflow script ran."""
    observed = str((record.get("pre_state") or {}).get("phase"))
    worker_outcome = record.get("worker_outcome") if record.get("status") == STATUS_COMPLETED else None
    now = _now()
    block = dict(record["protocol"])
    current = protocol.managed_digests(managed_repo.root)
    if current is None:
        # The bytes became unreadable between the decision and here: no difference is established.
        raise managed_repo.drift
    failed = {
        **record, "status": STATUS_FAILED, "transition_verified": False, "observed_phase_after": observed,
        "protocol": {**block, "reconcile": None},
        "reconciliation_evidence": _protocol_failure_evidence(
            WORKFLOW_RELEASE_CHANGED_REASON, observed_phase=observed, worker_outcome=worker_outcome,
            workflow_error={
                "code": WorkflowReleaseChangedError.code,
                "message": (f"{managed_repo.root}'s Workflow installation has drifted and its managed scripts "
                            "are not the ones the job was decided under; it is not reconciled"),
                "evidence": {"changed_scripts": _changed_scripts(block.get("script_sha256"), current)}}),
        "reconciled_at": now, "updated_at": now,
    }
    return _persist(runtime_root, record["job_id"], failed, event="reconciled", details=_reconciled_details(failed))


def _changed_scripts(recorded: Any, current: Mapping[str, str]) -> list[str]:
    recorded_map = dict(recorded or {})
    current_map = dict(current)
    return sorted(key for key in {*recorded_map, *current_map} if recorded_map.get(key) != current_map.get(key))


def _drift_decides(record: Mapping, managed_repo: Any) -> bool:
    """Whether a pending record is one a drifted installation may still end:
    a protocol job whose recorded managed-script digest map differs from the
    one the manifest and the files' bytes give now (no script executed). An
    unreadable map is unknown, not a difference: the record is left alone."""
    block = record.get("protocol")
    if not isinstance(block, dict):
        return False
    current = protocol.managed_digests(managed_repo.root)
    return current is not None and bool(_changed_scripts(block.get("script_sha256"), current))


def _reconcile_completed(record: JobRecord, *, managed_repo: Any, runtime_root: Path) -> JobRecord:
    """Row 2 (verifying) / row 5 (failing): ``COMPLETED``. Mirrors CP6B
    step 8/9's own verification rule exactly -- "exactly as CP6B step 8
    would have" -- against a fresh post-state re-read, never the
    long-gone process's own memory. The post-state read is
    :func:`_observe_post_phase`, the same bootstrap-aware helper
    ``_reconcile_launched`` and ``execute_step``'s own step 7 (CP6B) use."""
    if isinstance(record.get("protocol"), dict):
        return _reconcile_protocol(record, managed_repo=managed_repo, runtime_root=runtime_root)
    root = managed_repo.root
    work_item_id = record["work_item_id"]
    pre_state = record.get("pre_state") or {}
    worker_outcome = record.get("worker_outcome")
    outcome = _expected_outcome_for_record(record)

    observed_phase_after = _observe_post_phase(managed_repo, work_item_id, pre_state)
    observed_phase_after_wire = phase_to_wire(observed_phase_after)

    verified, reason, postcondition_detail, predicate_detail, workflow_error = _row2_verified(
        root=root, work_item_id=work_item_id, outcome=outcome, pre_state=pre_state,
        observed_phase_after=observed_phase_after, status=STATUS_COMPLETED, worker_outcome=worker_outcome,
        release=record.get("target_workflow_version"),
    )
    now = _now()
    branch_violation = _branch_invariant_violation(root, runtime_root, record) if verified else None
    if branch_violation is not None:
        return _branch_violation_failed(record, runtime_root, observed_phase_after_wire, branch_violation, now)
    if verified:
        reconciled = {
            **record, "status": STATUS_FINISHED, "transition_verified": True,
            "observed_phase_after": observed_phase_after_wire, "reconciled_at": now, "updated_at": now,
        }
        return _persist(runtime_root, record["job_id"], reconciled, event="reconciled",
                        details=_reconciled_details(reconciled))

    reconciled = {
        **record,
        "status": STATUS_FAILED,
        "transition_verified": False,
        "observed_phase_after": observed_phase_after_wire,
        "reconciliation_evidence": _transition_not_observed_evidence(
            outcome=outcome, observed_phase_after=observed_phase_after_wire,
            worker_outcome=worker_outcome, reason=reason, postcondition_detail=postcondition_detail,
            predicate_detail=predicate_detail, workflow_error=workflow_error,
        ),
        "reconciled_at": now,
        "updated_at": now,
    }
    return _persist(runtime_root, record["job_id"], reconciled, event="reconciled",
                    details=_reconciled_details(reconciled))


def resume(managed_repo: Any, *, identity: Any, runtime: Path,
           drain_detach_seconds: float | None = None) -> list[JobRecord]:
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
    work beside it).

    Every record that was **non-terminal on read** and went through one of
    the three reconciliation branches below (``PLANNED``/``LAUNCHED``/
    ``COMPLETED``) carries ``reconciled_this_call: True`` in the returned
    dict -- an in-memory-only marker, added after :func:`_persist` has
    already written the record's real, unmarked shape to disk, so it is
    never part of the persisted job-record schema and never read back on a
    later invocation. This is what lets a caller (``cli.cmd_resume``,
    exit 40) distinguish a record this very invocation reconciled to
    ``INTERRUPTED`` from a record that was *already* terminal
    ``INTERRUPTED`` history returned unchanged by the ``status in
    TERMINAL_STATUSES`` branch, which never carries the marker (fixes
    `I2`, MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 1: exit 40 must mean
    "the Controller itself was interrupted, or `resume` reconciled a
    record to `INTERRUPTED`" -- a historical terminal record satisfies
    neither clause).

    **Worker lifecycle** (automatic-lifecycle-orchestration CP5). ``resume``
    takes the target's lifecycle lock first (held ->
    :class:`~controller.errors.LifecycleWorkerActiveError`, exit 45,
    nothing reconciled). The one exception is a target root that no longer
    resolves to a directory: there is no worktree to protect and no git
    directory to lock, so no lock is taken and ``validate_record`` case 2
    decides exactly as before. A root that exists but whose git directory
    cannot be resolved is :class:`~controller.errors.GitDirectoryUnresolvableError`
    (exit 20), never a skipped lock. A ``LAUNCHED`` record carrying
    ``worker_process`` is reconciled only when its liveness verdict
    (``worker.assess_worker_liveness``) is ``inactive``; ``active`` and
    ``unverifiable`` leave it ``LAUNCHED`` and return it with
    ``resume_marked.outcome`` ``"worker_active"``/``"worker_unverifiable"``
    (``cli.cmd_resume`` exits 45). A job file that is not a regular file
    is never opened (round 1's O2 of the manual external plan review): it is
    :class:`~controller.errors.StaleJobRecordError`, naming its manual
    removal.

    **Two phases** (``workflow-controller-worker-lifecycle-ownership`` CP5,
    plan E). Phase 1 (:func:`_supervise_records`) takes no lifecycle lock:
    for each non-terminal record of this target that carries
    ``worker_state`` it takes that job's supervisor lock (held by another
    Controller -> exit 45, naming it) and, for a ``LAUNCHED`` one,
    re-attaches to the unsupervised worker (:func:`worker.reattach`),
    follows it to its end, drains its owned work, ends the anchor and
    flushes ``COMPLETED`` -- or, for a session no supervisor ended, only
    drains and ends the anchor. It never reconciles and never launches.
    Phase 2 is today's path under the lifecycle lock, with the ownership
    hold generalised (:func:`_owned_work_hold`): a ``LAUNCHED`` or
    ``COMPLETED`` record whose worker or any owned process is alive, or
    whose ownership scan is unverifiable, is never reconciled.

    ``drain_detach_seconds`` (settings-and-telemetry CP2) bounds phase 1's
    re-attached drain (``None``: :data:`worker.DRAIN_DETACH_SECONDS`)."""
    if getattr(managed_repo, "drift", None) is not None:
        _refuse_drift_unless_decided(managed_repo, runtime)
    if not managed_repo.root.is_dir():
        return _resume_records(managed_repo, identity=identity, runtime_root=runtime)
    # Worker-lifecycle-ownership CP5 (plan E): phase 1, the supervision
    # pre-pass, runs before the lifecycle lock -- the recorded anchor holds
    # that lock for as long as the worker or its owned work lives -- and
    # only then phase 2, today's reconciliation under the lock.
    _supervise_records(managed_repo, identity=identity, runtime_root=runtime,
                       drain_detach_seconds=drain_detach_seconds)
    with _acquire_lifecycle_lock(runtime, managed_repo):
        return _resume_records(managed_repo, identity=identity, runtime_root=runtime)


def _refuse_drift_unless_decided(managed_repo: Any, runtime_root: Path) -> None:
    """Under a drifted installation (``managed_repo.drift``), ``resume`` goes
    on only when every pending record for this target is a protocol job whose
    managed-file digest map differs from its record's -- the one thing it may
    end without the Workflow. Otherwise the ``DriftedInstallationError`` is
    re-raised before any write: a legacy record, a map that is equal, or
    nothing pending at all leaves every record untouched."""
    pending = []
    for path in _job_file_paths(runtime_root):
        if not _is_regular_job_file(path):
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        if (isinstance(record, dict) and record.get("target_repo") == str(managed_repo.root)
                and record.get("status") in NON_TERMINAL_STATUSES):
            pending.append(record)
    if not pending or not all(_drift_decides(record, managed_repo) for record in pending):
        raise managed_repo.drift


def _resume_records(managed_repo: Any, *, identity: Any, runtime_root: Path) -> list[JobRecord]:
    """:func:`resume`'s body, run under the lifecycle lock (or with none,
    for a target root that no longer resolves)."""
    runtime = runtime_root
    root = managed_repo.root
    target_repo_str = str(root)
    paths = _job_file_paths(runtime)

    results: list[JobRecord] = []
    for path in paths:
        if not _is_regular_job_file(path):
            raise StaleJobRecordError(
                f"job file {path} is not a regular file (a symlink, directory, FIFO or other special "
                f"file); it is never opened, and `resume --abandon` handles only regular files -- "
                f"remove it by hand",
                evidence={"path": str(path), "job_id": path.stem, "clearing_command": _manual_removal(path)},
            )
        try:
            record = json.loads(path.read_text())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StaleJobRecordError(
                f"job record at {path} could not be read or parsed as JSON: {exc} -- "
                f"`{_abandon_command(path.stem, root)}` sets its bytes aside under jobs/abandoned/ "
                f"and replaces it with a terminal record",
                evidence={"path": str(path), "error": str(exc),
                          "clearing_command": _abandon_command(path.stem, root)},
            ) from exc
        except OSError as exc:
            raise StaleJobRecordError(
                f"job file {path} cannot be read: {exc} -- `resume --abandon` cannot set unreadable "
                f"bytes aside, so it is cleared by hand: {_unreadable_clearing(path)}",
                evidence={"path": str(path), "error": str(exc), "clearing_command": _unreadable_clearing(path)},
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
            clearing = _invalid_record_clearing_command(record, validity, path.stem, root)
            raise StaleJobRecordError(
                f"{validity.message} -- {clearing}",
                evidence={**validity.evidence, "clearing_command": clearing},
            )

        status = record.get("status")
        drifted = getattr(managed_repo, "drift", None) is not None
        if drifted and status in NON_TERMINAL_STATUSES:
            marked = _owned_work_hold(record, root) if status != STATUS_PLANNED else None
            if marked is not None:
                results.append(marked)
            elif _drift_decides(record, managed_repo):
                results.append({**_resume_drifted_protocol(record, managed_repo=managed_repo, runtime_root=runtime),
                                "reconciled_this_call": True})
            else:
                raise managed_repo.drift
        elif status in TERMINAL_STATUSES:
            results.append(record)  # reported, never reconciled, never relaunched.
        elif status == STATUS_PLANNED:
            results.append({**_reconcile_planned(record, runtime_root=runtime), "reconciled_this_call": True})
        elif status == STATUS_LAUNCHED:
            marked = _owned_work_hold(record, root)
            if marked is not None:
                results.append(marked)  # left LAUNCHED on disk; nothing reconciled.
                continue
            results.append({
                **_reconcile_launched(record, managed_repo=managed_repo, runtime_root=runtime),
                "reconciled_this_call": True,
            })
        elif status == STATUS_COMPLETED:
            marked = _owned_work_hold(record, root)
            if marked is not None:
                results.append(marked)  # D7: left COMPLETED on disk; nothing reconciled.
                continue
            results.append({
                **_reconcile_completed(record, managed_repo=managed_repo, runtime_root=runtime),
                "reconciled_this_call": True,
            })
        else:
            # The closed table's own last row: a status outside the
            # ten-member enumeration entirely (or absent) is dispatched as
            # non-terminal -- validate_record's own cases could not judge
            # its terminality either, so this is what judges it.
            raise StaleJobRecordError(
                f"job {record.get('job_id')!r} has status {status!r}, which is outside the "
                f"closed job-status enumeration this generation recognises -- "
                f"`{_abandon_command(path.stem, root)}` marks it FAILED",
                evidence={"job_id": record.get("job_id"), "status": status,
                          "clearing_command": _abandon_command(path.stem, root)},
            )
    return results


# ---------------------------------------------------------------------------
# The apply relaunch bound's job history (automatic-lifecycle-orchestration
# CP4B, "Where the job history comes from").
# ---------------------------------------------------------------------------

_APPLYING_REVIEW_FEEDBACK = "APPLYING_REVIEW_FEEDBACK"
_APPLY_IMPLEMENTATION_REVIEW = "/apply-implementation-review"

#: `reconciliation_evidence.code` of a record whose worker never started --
#: no process ever touched the bundle, so the relaunch bound never counts it.
WORKER_NOT_STARTED_CODE = "WorkerNotStarted"


def _launched_apply_job_view(
    record: Any, target_repo: str, work_item_id: str,
) -> tuple[tuple[str, str], evidence.LaunchedJobView] | None:
    """``((created_at, job_id), view)`` when ``record`` is a candidate J
    for :func:`last_launched_apply_job_view`, else ``None`` -- never
    raises. A candidate is a JSON object for this target and work item,
    whose status is terminal, which reached ``LAUNCHED`` (it carries
    ``expected_transition``) from ``APPLYING_REVIEW_FEEDBACK`` with the
    ``/apply-implementation-review`` token, whose worker was not recorded
    as never started, and which carries every field read here. A missing
    ``pre_state.bundle_manifest_bundle_id`` (a record written before CP2)
    reads as ``None``, which the bound counts as the same bundle (fail
    closed) -- never as a reason to skip the record."""
    if not isinstance(record, dict):
        return None
    if record.get("target_repo") != target_repo or record.get("work_item_id") != work_item_id:
        return None
    status = record.get("status")
    if not isinstance(status, str) or status not in TERMINAL_STATUSES:
        return None
    expected_transition = record.get("expected_transition")
    if not isinstance(expected_transition, dict) or expected_transition.get("from") != _APPLYING_REVIEW_FEEDBACK:
        return None
    selected_action = record.get("selected_action")
    command = selected_action.get("command") if isinstance(selected_action, dict) else None
    if not isinstance(command, str) or not command.split() or command.split()[0] != _APPLY_IMPLEMENTATION_REVIEW:
        return None
    reconciliation_evidence = record.get("reconciliation_evidence")
    if isinstance(reconciliation_evidence, dict) and reconciliation_evidence.get("code") == WORKER_NOT_STARTED_CODE:
        return None
    job_id = record.get("job_id")
    created_at = record.get("created_at")
    pre_state = record.get("pre_state")
    if not isinstance(job_id, str) or not isinstance(created_at, str) or not isinstance(pre_state, dict):
        return None
    bundle_id = pre_state.get("bundle_manifest_bundle_id")
    return (created_at, job_id), evidence.LaunchedJobView(
        job_id=job_id, command=_APPLY_IMPLEMENTATION_REVIEW, from_phase=_APPLYING_REVIEW_FEEDBACK,
        status=status, pre_bundle_manifest_bundle_id=bundle_id if isinstance(bundle_id, str) else None,
    )


def last_launched_apply_job_view(
    runtime_root: Path, target_root: Path, work_item_id: str | None,
) -> evidence.LaunchedJobView | None:
    """J for the apply relaunch bound (``evidence.relaunch_bound_applies``):
    the most recent (by ``created_at``, then ``job_id``) **terminal**
    ``/apply-implementation-review`` record this Controller launched for
    ``work_item_id`` against ``target_root`` from ``APPLYING_REVIEW_FEEDBACK``
    (:func:`_launched_apply_job_view`), or ``None``. A non-terminal record
    is never J, and a ``WorkerNotStarted`` record is skipped, so an earlier
    attempt behind it still counts.

    Reads tolerantly, over the same non-recursive ``jobs/*.json`` scan
    :func:`resume` makes, and never raises on a job file: a file that does
    not read or parse as a JSON object, another target's record (including
    a record whose ``target_repo`` is ``null``), and a record missing a
    field it reads are all skipped -- so ``explain`` still reports its
    decision while such a file is on disk."""
    if work_item_id is None:
        return None
    jobs_dir = Path(runtime_root) / "jobs"
    try:
        paths = sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []
    except OSError:
        return None
    target_repo = str(target_root)
    best: tuple[tuple[str, str], evidence.LaunchedJobView] | None = None
    for path in paths:
        if not _is_regular_job_file(path):
            continue  # never opened: a FIFO would block (CP5)
        try:
            record = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        candidate = _launched_apply_job_view(record, target_repo, work_item_id)
        if candidate is not None and (best is None or candidate[0] > best[0]):
            best = candidate
    return best[1] if best is not None else None


# ---------------------------------------------------------------------------
# Worker lifecycle and concurrency (automatic-lifecycle-orchestration CP5,
# "Concurrency and worker lifecycle"): the lifecycle lock's refusal text,
# the pending-reconciliation scan, the liveness hold `resume` applies, and
# `resume --abandon`.
# ---------------------------------------------------------------------------

#: `reconciliation_evidence.code` of a record `resume --abandon` disposed of.
OPERATOR_ABANDONED_CODE = "OperatorAbandoned"

#: `reconciliation_evidence.code` of a record `resume` persisted `FAILED`
#: because it could not be reconciled after its worker definitively ended.
UNRECONCILABLE_JOB_CODE = "UnreconcilableJobError"

#: `resume_marked.outcome` for a `LAUNCHED` record `resume` left alone
#: because its recorded worker may still run (`cli.cmd_resume` exits 45).
RESUME_WORKER_ACTIVE = "worker_active"
RESUME_WORKER_UNVERIFIABLE = "worker_unverifiable"
#: ``resume_marked.outcome`` for a record `resume` left alone because a
#: process its job owns is still running (plan E, worker-lifecycle-ownership
#: CP5; ``cli.cmd_resume`` exits 45).
RESUME_OWNED_WORK_ACTIVE = "owned_work_active"
#: Every ``resume_marked.outcome`` of a record held for a live (or
#: undecidable) worker or owned process.
RESUME_HELD_OUTCOMES = (RESUME_WORKER_ACTIVE, RESUME_WORKER_UNVERIFIABLE, RESUME_OWNED_WORK_ACTIVE)

_JOB_ENTRY_REGULAR = "regular"
_JOB_ENTRY_VANISHED = "vanished"
_JOB_ENTRY_OTHER = "other"


def _job_file_paths(runtime_root: Path) -> list[Path]:
    """The one scan every job-file reader makes: ``sorted(<runtime>/jobs/
    .glob("*.json"))``. It is not recursive, so ``jobs/abandoned/`` (whose
    copies are unparseable by construction) is outside it."""
    jobs_dir = Path(runtime_root) / "jobs"
    return sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []


def _job_entry_kind(path: Path) -> str:
    """``lstat`` before anything opens a job entry (round 1's O2 of the
    manual external plan review): a regular file, an entry that vanished,
    or anything else -- a symlink, directory, FIFO or other special file,
    which is never opened (a FIFO would block the reader) and which
    ``--abandon`` never replaces."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return _JOB_ENTRY_VANISHED
    except OSError:
        return _JOB_ENTRY_OTHER
    return _JOB_ENTRY_REGULAR if stat.S_ISREG(mode) else _JOB_ENTRY_OTHER


def _is_regular_job_file(path: Path) -> bool:
    return _job_entry_kind(path) == _JOB_ENTRY_REGULAR


def _resume_command(root: Path, runtime_root: Path | None = None) -> str:
    """``resume``'s hint for ``root``. With ``runtime_root`` (the
    ``status``/``inspect``/``explain``/``follow`` activity hint,
    :func:`controller.observe.resume_command`) it carries ``--runtime-dir``,
    exactly as :func:`follow_command` does."""
    if runtime_root is None:
        return f"workflow-controller resume {root}"
    return f"workflow-controller --runtime-dir {runtime_root} resume {root}"


def follow_command(runtime_root: Path, root: Path) -> str:
    """The command that follows ``root``'s active run or orphaned worker
    (``workflow-controller-release-runtime-observability`` CP6) -- the hint
    every exit-45 and orphan message carries."""
    return f"workflow-controller --runtime-dir {runtime_root} follow {root}"


def _abandon_command(job_id: str, root: Path, *, acknowledge: bool = False) -> str:
    flag = " --acknowledge-unverifiable-worker" if acknowledge else ""
    return f"workflow-controller resume --abandon {job_id}{flag} {root}"


def _manual_removal(path: Path) -> str:
    return f"remove {path} by hand"


def _unreadable_clearing(path: Path) -> str:
    """The clearing command for a regular job file whose bytes cannot be
    read (after a permissions change, say): ``--abandon`` cannot set
    unreadable bytes aside, so only a human clears it."""
    return f"make {path} readable again, or remove it by hand"


def _newer_generation(record: Mapping, identity: Any) -> int | None:
    """The record's ``controller_generation`` when it is an integer newer
    than the running Controller's (``validate_record`` case 1,
    ``newer_controller_generation``), else ``None``."""
    recorded, running = record.get("controller_generation"), identity.generation
    if (isinstance(recorded, int) and not isinstance(recorded, bool) and isinstance(running, int)
            and recorded > running):
        return recorded
    return None


def _newer_generation_clearing(generation: int, root: Path) -> str:
    return f"{_resume_command(root)}, run by Controller generation {generation}"


def _invalid_record_clearing_command(record: Mapping, validity: Validity, stem: str, root: Path) -> str:
    """The sentence ``resume``'s ``StaleJobRecordError`` for a non-terminal
    record ends with: ``--abandon``, except for a newer generation's record,
    which that generation's own ``resume`` clears."""
    if validity.reason == "newer_controller_generation":
        generation = record.get("controller_generation")
        return (f"it belongs to Controller generation {generation}, newer than this one, and is left "
                f"untouched: `{_newer_generation_clearing(generation, root)}` clears it")
    return f"`{_abandon_command(stem, root)}` marks it FAILED"


@dataclasses.dataclass(frozen=True)
class PendingJob:
    """One job file pending reconciliation for a target: its ``job_id``
    (the file stem), its ``status`` (``None`` for a file that is unreadable
    or not a JSON object, or whose status is not a string), the command
    that clears it, and why it is pending."""

    job_id: str
    status: str | None
    clearing_command: str
    reason: str

    def to_dict(self) -> dict:
        return {"job_id": self.job_id, "status": self.status, "clearing_command": self.clearing_command}


def _validity_or_none(record: Mapping, managed_repo: Any, identity: Any) -> Validity | None:
    """``validate_record`` for a report, never raising on a job file's own
    content (``None`` when a malformed field made it raise)."""
    try:
        return validate_record(record, managed_repo=managed_repo, identity=identity)
    except (AttributeError, TypeError, ValueError, KeyError, OSError):
        return None


def _pending_record(record: dict, stem: str, managed_repo: Any, identity: Any) -> PendingJob:
    root = managed_repo.root
    status = record.get("status")
    status_str = status if isinstance(status, str) else None
    generation = _newer_generation(record, identity)
    if generation is not None:
        return PendingJob(stem, status_str, _newer_generation_clearing(generation, root),
                          f"written by Controller generation {generation}, newer than this one; that "
                          f"generation's own `resume` clears it")
    validity = _validity_or_none(record, managed_repo, identity)
    if validity is None or not validity.valid:
        why = "its fields could not be read" if validity is None else validity.reason
        return PendingJob(stem, status_str, _abandon_command(stem, root),
                          f"`resume` cannot reconcile it ({why})")
    if status_str not in NON_TERMINAL_STATUSES:
        return PendingJob(stem, status_str, _abandon_command(stem, root),
                          f"its status {status!r} is outside the job-status enumeration")
    if "worker_state" in record and status_str in (STATUS_LAUNCHED, STATUS_COMPLETED):
        # Worker-lifecycle-ownership CP5: what the pending job is doing.
        return PendingJob(stem, status_str, _resume_command(root), _pending_activity(record, root))
    return PendingJob(stem, status_str, _resume_command(root), "not yet reconciled")


def pending_reconciliation_jobs(runtime_root: Path, managed_repo: Any, identity: Any) -> list[PendingJob]:
    """Every job file ``resume`` would reconcile or raise on for
    ``managed_repo`` -- the one read-only scan behind ``execute_step``'s
    :class:`~controller.errors.PendingJobReconciliationError` refusal and
    behind ``explain``'s pending report. Never raises on a job file, and
    writes nothing.

    Pending, over the non-recursive ``jobs/*.json`` scan ``resume`` makes:

    - a record whose ``target_repo`` is this target and whose status is not
      terminal: ``PLANNED``, ``LAUNCHED``, ``COMPLETED``, or a status
      outside the enumeration (which ``resume`` raises on);
    - any entry that does not parse as a JSON object: its target cannot be
      read, and it could be this target's ``LAUNCHED`` record (fail
      closed);
    - any regular file that cannot be read at all (cleared by hand, because
      ``--abandon`` cannot set unreadable bytes aside);
    - any entry that is not a regular file (never opened; cleared by hand,
      because ``--abandon`` replaces only regular files).

    Each carries its clearing command: ``resume`` for a record ``resume``
    can reconcile; ``resume --abandon JOB_ID`` for one it cannot (an
    unparseable file, a record failing ``validate_record``, an unknown
    status); for a newer generation's record, that generation's own
    ``resume``; by hand for the two kinds of entry above."""
    root = managed_repo.root
    target_repo = str(root)
    pending: list[PendingJob] = []
    for path in _job_file_paths(runtime_root):
        kind = _job_entry_kind(path)
        if kind == _JOB_ENTRY_VANISHED:
            continue
        if kind != _JOB_ENTRY_REGULAR:
            pending.append(PendingJob(path.stem, None, _manual_removal(path),
                                      "not a regular file: it is never opened, and `resume --abandon` "
                                      "replaces only regular files"))
            continue
        try:
            record = json.loads(path.read_text())
        except (UnicodeDecodeError, ValueError, RecursionError):
            record = None
            parsed = False
        except OSError as exc:
            pending.append(PendingJob(path.stem, None, _unreadable_clearing(path),
                                      f"it cannot be read ({exc}), and `resume --abandon` cannot set "
                                      "unreadable bytes aside (it could be this target's record)"))
            continue
        else:
            parsed = isinstance(record, dict)
        if not parsed:
            pending.append(PendingJob(path.stem, None, _abandon_command(path.stem, root),
                                      "it does not parse as a JSON object, so its target cannot be read "
                                      "(it could be this target's record)"))
            continue
        if record.get("target_repo") != target_repo:
            continue
        status = record.get("status")
        if isinstance(status, str) and status in TERMINAL_STATUSES:
            continue
        pending.append(_pending_record(record, path.stem, managed_repo, identity))
    return pending


def _refuse_pending_reconciliation(runtime_root: Path, managed_repo: Any, identity: Any) -> None:
    """``execute_step``'s refusal, under the lock and before deciding: no
    launch over an unreconciled job, so a ``LAUNCHED`` record left by a
    Controller that died can never be silently replaced by a second
    worker."""
    pending = pending_reconciliation_jobs(runtime_root, managed_repo, identity)
    if not pending:
        return
    listed = "; ".join(
        f"job {entry.job_id} ({entry.status or 'unreadable'}: {entry.reason}) -- clear it with "
        f"`{entry.clearing_command}`"
        for entry in pending
    )
    raise PendingJobReconciliationError(
        f"refusing to decide or launch for {managed_repo.root}: {len(pending)} earlier job file(s) "
        f"are pending reconciliation, and `step`/`run` refuse until each is cleared -- {listed}",
        evidence={"pending_jobs": [entry.to_dict() for entry in pending]},
    )


_OTHER_HOLDER_SENTENCE = (
    "Any holder those commands name that is not a recorded worker is that job's recorded stdin anchor "
    "(worker_anchor), which holds the lock for the job's whole owned lifetime -- while the worker waits "
    "on background work and while owned processes drain -- or a member of the worker's own process "
    "group; a tool process the worker started never receives the descriptor. The lock is released "
    "only when the job's owned work has ended."
)


def _active_guidance(job_id: Any, assessment: worker.LivenessAssessment, root: Path) -> str:
    """What to do about an ``active`` recorded worker (round 1's O3 of the
    manual external plan review): the process group to end when the
    recorded leader itself was matched; the discovered members, to verify
    first, when only a member scan found the group running."""
    pid, pgid = assessment.recorded.get("pid"), assessment.recorded.get("pgid")
    process = assessment.process
    if process is not None and process.basis == "member_scan":
        members = ", ".join(str(member) for member in process.members)
        return (
            f"Job {job_id}'s recorded worker leader (pid {pid}) is not running, but running "
            f"process(es) {members} are in its recorded process group {pgid}. That group number may "
            f"have been reused since the job started: verify those processes "
            f"(`ps -o pid,pgid,lstart,args -g {pgid}`) before ending the group "
            f"(`kill -TERM -- -{pgid}`), or wait for them; then run `{_resume_command(root)}`."
        )
    return (
        f"Job {job_id}'s recorded worker is running: pid {pid}, process group {pgid}. Wait for it, or "
        f"end that group (`kill -TERM -- -{pgid}`); then run `{_resume_command(root)}`."
    )


def _context_text(context: Mapping) -> str:
    return ", ".join(
        f"{field}={context.get(field)!r}" for field in ("hostname", "machine_id", "boot_id", "pid_namespace")
    )


def _unverifiable_guidance(job_id: Any, assessment: worker.LivenessAssessment, root: Path) -> str:
    """What to do about an ``unverifiable`` recorded worker. It names **no**
    process group: nothing observable here ties the recorded one to a
    running process."""
    text = (
        f"Job {job_id}'s recorded worker cannot be verified from here: {assessment.reason}. "
        f"Recorded context: {_context_text(assessment.recorded)}; current context: "
        f"{_context_text(assessment.current)}. "
    )
    if assessment.process is not None and assessment.process.answer == worker.POSSIBLY_LIVE:
        text += "killpg cannot tell a running member of the recorded process group from a zombie. "
    return text + (
        "No process group is named, because the recorded one cannot be tied to anything running here. "
        f"If that worker is gone, state so with `{_abandon_command(job_id, root, acknowledge=True)}`."
    )


def _launched_worker_records(runtime_root: Path, root: Path) -> list[dict]:
    """This target's ``LAUNCHED`` records carrying a ``worker_process``,
    read tolerantly (for the exit-45 message only)."""
    records = []
    for path in _job_file_paths(runtime_root):
        if not _is_regular_job_file(path):
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        if (isinstance(record, dict) and record.get("target_repo") == str(root)
                and record.get("status") == STATUS_LAUNCHED and isinstance(record.get("worker_process"), dict)):
            records.append(record)
    return records


def _recorded_worker_detail(runtime_root: Path, root: Path) -> tuple[str, list[dict]]:
    """The exit-45 lock-refusal's recorded-worker half: the recorded worker
    pid/pgid only for a ``LAUNCHED`` record whose liveness verdict is
    ``active``; for any other verdict, why the recorded group is not named
    (after a reboot its number may name an unrelated live group)."""
    parts: list[str] = []
    recorded: list[dict] = []
    records = _launched_worker_records(runtime_root, root)
    if not records:
        parts.append("No recorded worker process group is named: no LAUNCHED job record for this "
                     "target carries a worker process.")
    for record in records:
        assessment = worker.assess_worker_liveness(record["worker_process"])
        recorded.append({"job_id": record.get("job_id"), "verdict": assessment.verdict})
        if assessment.verdict == worker.ACTIVE:
            parts.append(_active_guidance(record.get("job_id"), assessment, root))
        else:
            parts.append(
                f"Job {record.get('job_id')}'s recorded worker process group is not named: its "
                f"liveness verdict is {assessment.verdict} ({assessment.reason}), so its number may "
                f"name an unrelated process group here (for example after a reboot)."
            )
        anchor_sentence = _recorded_anchor_sentence(record, assessment, root, runtime_root)
        if anchor_sentence is not None:
            parts.append(anchor_sentence)
    parts.append(_OTHER_HOLDER_SENTENCE)
    return " ".join(parts), recorded


def _recorded_anchor_sentence(record: Mapping, assessment: worker.LivenessAssessment, root: Path,
                              runtime_root: Path) -> str | None:
    """The exit-45 text for a live recorded stdin anchor (worker-lifecycle-
    ownership CP5, plan E): it holds the lock, and what clears it -- ``resume``
    re-attaches while its worker or owned work lives; otherwise it ends
    itself within the orphan lifetime of its last owned process."""
    anchor = record.get("worker_anchor")
    if not isinstance(anchor, Mapping) or worker.identity_alive(anchor.get("pid"), anchor.get("start_ticks")) is False:
        return None
    job_id = record.get("job_id")
    head = f"Job {job_id}'s stdin anchor (pid {anchor.get('pid')}) holds the lock"
    supervisor, holders = supervisor_probe(runtime_root, record)
    if supervisor == SUPERVISOR_ATTACHED:
        return (f"{head}, and a Controller (pid {', '.join(str(pid) for pid in holders)}) is attached to "
                f"the job, supervising it.")
    entries, _verifiable = _scan_record_owned(record) if "worker_state" in record else ([], True)
    if assessment.verdict != worker.INACTIVE or entries:
        return (f"{head} for the job's owned lifetime, and no Controller is attached: "
                f"`{_resume_command(root)}` re-attaches to it.")
    return (f"{head}; its worker and owned work have ended, so it ends itself within "
            f"{int(anchor_orphan_seconds())} s of its last owned process, or `{_resume_command(root)}` ends it now.")


def _waiting_run(runtime_root: Path, root: Path) -> tuple[str, dict] | None:
    """The live ``run`` that holds ``root``'s lifecycle lock while it waits
    at a gate (checks, merge or release), as ``(run_id, its last "waiting"
    event)``, or ``None``. Read tolerantly, for the exit-45 message only: a
    run counts when its record is ``running`` for this target, its recorded
    Controller process is alive, is not this one and is a taker of the
    lifecycle lock (``/proc/locks``), and the last event of its log is the
    ``waiting`` event."""
    try:
        paths = sorted((Path(runtime_root) / "runs").glob("*.json"))
    except OSError:
        return None
    holders = lock.lifecycle_lock_holders(root)
    for path in paths:
        try:
            record = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        if not (isinstance(record, dict) and isinstance(record.get("run_id"), str)
                and record.get("state") == RUN_STATE_RUNNING and record.get("target_repo") == str(root)):
            continue
        process = record.get("controller_process")
        pid = process.get("pid") if isinstance(process, Mapping) else None
        if pid == os.getpid() or pid not in holders or not (isinstance(process, Mapping)
                                      and worker.identity_alive(pid, process.get("start_ticks")) is True):
            continue
        try:
            lines = (Path(runtime_root) / "runs" / record["run_id"] / "events.jsonl").read_text().splitlines()
            last = json.loads(lines[-1])
        except (OSError, UnicodeDecodeError, ValueError, IndexError, RecursionError):
            continue
        if isinstance(last, dict) and last.get("event") == "waiting":
            return record["run_id"], last
    return None


def _acquire_lifecycle_lock(runtime_root: Path, managed_repo: Any) -> lock.LifecycleLock:
    """``lock.acquire_lifecycle_lock`` for ``managed_repo``, with the exit-45
    refusal's message completed from this Controller's own job records:
    the lock path and how to find every holder (``lock``'s own text), the
    recorded worker's pid/pgid only when its verdict is ``active``, and
    the "any other holder is the recorded anchor or a group member" sentence."""
    try:
        return lock.acquire_lifecycle_lock(managed_repo.root)
    except LifecycleWorkerActiveError as exc:
        waiting = _waiting_run(runtime_root, managed_repo.root)
        if waiting is not None:
            run_id, event = waiting
            # The holder is the user's own waiting `run`, not a worker.
            raise LifecycleWorkerActiveError(
                f"{exc.message}. Run {run_id} holds it: it is waiting at {event.get('gate')} until "
                f"{event.get('deadline')} (for checks, the merge or the release), and no worker is running. "
                f"Wait for it, or press Ctrl-C in that run (the next step continues from the binding's last "
                f"state). Follow it: `{follow_command(runtime_root, managed_repo.root)}`",
                evidence={**exc.evidence, "recorded_workers": [], "waiting_run": run_id},
            ) from exc
        detail, recorded = _recorded_worker_detail(runtime_root, managed_repo.root)
        raise LifecycleWorkerActiveError(
            f"{exc.message}. {detail} Follow it: `{follow_command(runtime_root, managed_repo.root)}`",
            evidence={**exc.evidence, "recorded_workers": recorded},
        ) from exc


def _worker_liveness_hold(record: JobRecord, root: Path) -> JobRecord | None:
    """``resume``'s second, independent check for a ``LAUNCHED`` record
    carrying ``worker_process`` (the lock is the first): ``None`` when its
    verdict is ``inactive`` (reconcile it), else the record, unchanged on
    disk, with ``resume_marked`` naming the ``worker_active`` or
    ``worker_unverifiable`` outcome. A record without ``worker_process``
    reconciles exactly as before."""
    worker_process = record.get("worker_process")
    if worker_process is None:
        return None
    assessment = worker.assess_worker_liveness(worker_process if isinstance(worker_process, Mapping) else {})
    if assessment.verdict == worker.INACTIVE:
        return None
    job_id = record.get("job_id")
    if assessment.verdict == worker.ACTIVE:
        outcome, reason = RESUME_WORKER_ACTIVE, _active_guidance(job_id, assessment, root)
    else:
        outcome, reason = RESUME_WORKER_UNVERIFIABLE, _unverifiable_guidance(job_id, assessment, root)
    return {**record, "resume_marked": {"outcome": outcome, "reason": reason, "liveness": assessment.to_dict()}}


def _owned_guidance(job_id: Any, entries: list[dict], root: Path, *, then: str | None = None) -> str:
    """What to do about a job whose owned processes still run (plan E):
    each pid with its command line, and the two ways on (``then``, the
    command to run afterwards: ``resume`` by default)."""
    listed = ", ".join(f"pid {entry.get('pid')} ({entry.get('cmdline') or 'command line unreadable'})"
                       for entry in entries)
    pids = " ".join(str(entry.get("pid")) for entry in entries)
    return (
        f"Job {job_id}'s worker has ended, but {len(entries)} process(es) it owns are still running: "
        f"{listed}. Wait for them, or end them (`kill {pids}`); then run `{then or _resume_command(root)}`."
    )


def _scan_record_owned(record: Mapping) -> tuple[list[dict], bool]:
    """:func:`worker.scan_recorded_owned_processes` for ``record``."""
    worker_state = record.get("worker_state") if isinstance(record.get("worker_state"), Mapping) else {}
    owned = worker_state.get("owned_processes")
    tag = record.get("ownership_tag")
    return worker.scan_recorded_owned_processes(
        ownership_tag=tag if isinstance(tag, str) and tag else None,
        worker_process=record.get("worker_process") if isinstance(record.get("worker_process"), Mapping) else None,
        anchor=record.get("worker_anchor") if isinstance(record.get("worker_anchor"), Mapping) else None,
        owned_processes=owned if isinstance(owned, list) else (),
    )


def _owned_work_hold(record: JobRecord, root: Path) -> JobRecord | None:
    """``resume``'s ownership hold (worker-lifecycle-ownership CP5, plan E;
    ``_worker_liveness_hold`` generalised): ``None`` when the record may be
    reconciled, else the record, unchanged on disk, with ``resume_marked``
    naming why not.

    A record without ``worker_state`` (1.2.x or earlier, I9) is judged
    exactly as before: a ``LAUNCHED`` one by its recorded worker's liveness
    verdict, a ``COMPLETED`` one not at all. A record with it -- ``LAUNCHED``
    or ``COMPLETED`` (D7) -- is held while its recorded worker is ``active``
    or ``unverifiable``, while any process its job owns is alive (the
    worker's recorded group, the ownership tag, the recorded
    ``owned_processes`` whose identity still matches; recognised daemons
    excluded), or while that scan is unverifiable. A live anchor alone is
    not owned work: ``resume``'s phase 1 disposes of it."""
    if "worker_state" not in record:
        return _worker_liveness_hold(record, root) if record.get("status") == STATUS_LAUNCHED else None
    held = _worker_liveness_hold(record, root)
    if held is not None:
        return held
    job_id = record.get("job_id")
    entries, verifiable = _scan_record_owned(record)
    if entries:
        return {**record, "resume_marked": {"outcome": RESUME_OWNED_WORK_ACTIVE,
                                            "reason": _owned_guidance(job_id, entries, root),
                                            "owned_processes": entries}}
    if not verifiable:
        return {**record, "resume_marked": {
            "outcome": RESUME_WORKER_UNVERIFIABLE,
            "reason": (f"Job {job_id}'s owned processes cannot be verified from here: /proc could not be "
                       f"listed, or a same-uid process's stat could not be read, so none is assumed gone. "
                       f"Run `{_resume_command(root)}` again once /proc is readable."),
        }}
    return None


def _pending_activity(record: JobRecord, root: Path) -> str:
    """``pending_reconciliation_jobs``' reason for a record carrying
    ``worker_state`` (plan E): what its worker or owned work is doing."""
    held = _owned_work_hold(record, root)
    if held is not None:
        return f"not yet reconciled, and held: {held['resume_marked']['reason']}"
    state = (record.get("worker_state") or {}).get("state") if isinstance(record.get("worker_state"), Mapping) \
        else None
    return f"not yet reconciled (worker_state {state}; no worker or owned process is running)"


# ---------------------------------------------------------------------------
# `resume`'s and `--abandon`'s phase 1: supervision before the lifecycle
# lock (worker-lifecycle-ownership CP5, plan E).
# ---------------------------------------------------------------------------


def _read_job_record(path: Path) -> dict | None:
    """A regular job file's record, or ``None`` when it is not a regular
    file, cannot be read, or is not a JSON object (phase 2 reports those)."""
    if not _is_regular_job_file(path):
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def _supervision_candidate(path: Path, managed_repo: Any, identity: Any, *, valid: bool = True) -> dict | None:
    """The record at ``path`` when phase 1 considers it: this target's,
    named by its own file, ``LAUNCHED`` or ``COMPLETED`` (non-terminal
    for ``--abandon``, ``valid=False``), carrying ``worker_state`` (I9:
    a legacy record goes straight to phase 2), and -- for ``resume`` --
    passing :func:`validate_record` (phase 2 raises on one that does not)."""
    record = _read_job_record(path)
    if record is None or record.get("target_repo") != str(managed_repo.root) or record.get("job_id") != path.stem \
            or "worker_state" not in record:
        return None
    status = record.get("status")
    if valid:
        if status not in (STATUS_LAUNCHED, STATUS_COMPLETED):
            return None
        validity = _validity_or_none(record, managed_repo, identity)
        if validity is None or not validity.valid:
            return None
    elif record.get("schema_version") != SCHEMA_VERSION or not isinstance(status, str) \
            or status in TERMINAL_STATUSES or _newer_generation(record, identity) is not None:
        return None
    return record


def _supervise_records(managed_repo: Any, *, identity: Any, runtime_root: Path,
                       drain_detach_seconds: float | None = None) -> None:
    """Phase 1 of :func:`resume`: under each candidate job's supervisor lock
    (never the lifecycle lock), :func:`_supervise_record`. Only one job per
    target can have held the lifecycle lock at a time, so at most one is
    actually re-attached to."""
    for path in _job_file_paths(runtime_root):
        if _supervision_candidate(path, managed_repo, identity) is not None:
            _supervise_record(managed_repo, identity=identity, runtime_root=runtime_root, path=path,
                              drain_detach_seconds=drain_detach_seconds)


def _supervise_record(managed_repo: Any, *, identity: Any, runtime_root: Path, path: Path,
                      drain_detach_seconds: float | None = None) -> None:
    """One phase-1 record, under its supervisor lock (plan E, "Re-attach").
    Another Controller attached -> exit 45 naming it, nothing touched. A
    ``COMPLETED`` record is left to phase 2. A ``LAUNCHED`` one:

    - with no recorded worker (a Controller lost between spawn and the
      ``on_spawn`` flush): a live process carrying the job's tag is exit 45,
      naming it and the by-hand remedy; with none, phase 2 fails it closed;
    - a recorded worker whose liveness is ``unverifiable``: phase 2 holds it;
    - the worker alive, or gone with ``ending_offset`` recorded (a
      supervisor ended the session): re-attach, follow it to its end, drain,
      end the anchor, flush ``ENDED`` and ``COMPLETED`` with the stream's
      classification (exit status unknown);
    - the worker gone with no ``ending_offset``: no re-attach -- drain its
      owned work with the same bound, end the anchor, and leave the record
      to phase 2's fail-closed ``_reconcile_launched`` (I7).

    A drain that outlives ``drain_detach_seconds`` (``None``:
    :data:`worker.DRAIN_DETACH_SECONDS`) detaches as
    ``execute_step`` does: ``drain_detached_at`` and
    ``worker_drain_detached`` are written, the supervisor lock released and
    :class:`~controller.errors.OwnedWorkDetachedError` raised (exit 45)."""
    root = managed_repo.root
    job_id = path.stem
    candidate = _supervision_candidate(path, managed_repo, identity) or {}
    with _supervisor_lock(runtime_root, job_id, record=candidate, nothing_done="nothing was reconciled"):
        record = _supervision_candidate(path, managed_repo, identity)
        if record is None or record.get("status") != STATUS_LAUNCHED:
            return
        worker_process = record.get("worker_process")
        if not isinstance(worker_process, Mapping):
            _refuse_unrecorded_worker(record, root)
            return
        streams = record.get("worker_streams") if isinstance(record.get("worker_streams"), Mapping) else {}
        if not isinstance(streams.get("stdout_path"), str) or not isinstance(streams.get("stderr_path"), str):
            return
        assessment = worker.assess_worker_liveness(worker_process)
        if assessment.verdict == worker.UNVERIFIABLE:
            return
        leader = worker.identity_alive(worker_process.get("pid"), worker_process.get("start_ticks"))
        if leader is None:
            return
        alive = assessment.verdict == worker.ACTIVE and leader
        _reattach(runtime_root, root, record, streams, classify=alive or record.get("ending_offset") is not None,
                  drain_detach_seconds=drain_detach_seconds)


def _refuse_unrecorded_worker(record: JobRecord, root: Path) -> None:
    """A ``STARTING`` record with no recorded worker (plan E, round 2's
    O2): nothing to re-attach to. A live process carrying its tag is exit
    45, naming it; with none, phase 2 fails the record closed."""
    tag = record.get("ownership_tag")
    if not isinstance(tag, str) or not tag:
        return
    entries, _verifiable = worker.scan_recorded_owned_processes(
        ownership_tag=tag, worker_process=None, anchor=None, owned_processes=(),
    )
    if not entries:
        return
    listed = ", ".join(f"pid {entry['pid']} ({entry.get('cmdline') or 'command line unreadable'})"
                       for entry in entries)
    pids = " ".join(str(entry["pid"]) for entry in entries)
    job_id = record.get("job_id")
    raise LifecycleWorkerActiveError(
        f"job {job_id}'s Controller was lost between spawning its worker and recording it, so there is "
        f"nothing to re-attach to, but process(es) carrying its ownership tag are running: {listed}. End "
        f"that worker by hand (`kill {pids}`); its stdin anchor then ends itself within "
        f"{int(anchor_orphan_seconds())} s, and a later `{_resume_command(root)}` reconciles the record "
        f"(fail-closed) -- nothing was reconciled",
        evidence={"job_id": job_id, "tagged_processes": entries},
    )


def anchor_orphan_seconds() -> float:
    """The stdin anchor's orphan lifetime (``controller.anchor``), read at
    call time."""
    return worker.anchor_module.ANCHOR_ORPHAN_SECONDS


def _reattach(runtime_root: Path, root: Path, record: JobRecord, streams: Mapping, *, classify: bool,
              drain_detach_seconds: float | None = None) -> None:
    """Run :func:`worker.reattach` for ``record``, persisting each
    ``on_state_change`` exactly as ``execute_step`` does (the record stays
    ``LAUNCHED``), then the drain detach, or ``COMPLETED`` (after
    ``ENDED``) when the stream was classified."""
    job_id = record["job_id"]
    current = {"record": record}

    def on_state_change(state: str, details: Mapping) -> None:
        before = current["record"]
        state_changed = (before.get("worker_state") or {}).get("state") != state
        updated = _with_worker_state(before, state, details)
        current["record"] = _persist(runtime_root, job_id, updated, event=_worker_state_event(state),
                                     details=_worker_state_event_details(updated, details,
                                                                         state_changed=state_changed))

    worker_state = record.get("worker_state") if isinstance(record.get("worker_state"), Mapping) else {}
    owned = worker_state.get("owned_processes")
    settled = record.get("settled_wakeups")
    result = worker.reattach(
        stdout_path=streams["stdout_path"], stderr_path=streams["stderr_path"],
        worker_process=record["worker_process"], anchor=record.get("worker_anchor"),
        ownership_tag=record.get("ownership_tag"), state=worker_state.get("state"),
        owned_processes=owned if isinstance(owned, list) else (),
        owned_processes_seen_count=worker_state.get("owned_processes_seen_count"),
        ending_offset=record.get("ending_offset"),
        wakeup_overdue_declared_at=record.get("wakeup_overdue_declared_at"),
        command_lifecycle_overdue_declared_at=record.get("command_lifecycle_overdue_declared_at"),
        command_lifecycle_overdue_command_uuid=record.get("command_lifecycle_overdue_command_uuid"),
        settled_wakeups=settled if isinstance(settled, list) else (),
        on_state_change=on_state_change, classify=classify, drain_detach_seconds=drain_detach_seconds,
    )
    record = current["record"]
    if isinstance(result, worker.DrainDetached):
        _record_drain_detached(runtime_root, root, job_id, record, result,
                               worker_pid=record["worker_process"].get("pid"))
    if result is None:
        return
    if (record.get("worker_state") or {}).get("state") != worker.ENDED:
        record = _with_worker_state(record, worker.ENDED, {})
    completed_at = _now()
    block = _telemetry_block(record, streams, completed_at=completed_at, reattached=True)
    record = {
        **record,
        "status": STATUS_COMPLETED,
        "worker": _worker_dict(result, stdout_path=streams["stdout_path"], stderr_path=streams["stderr_path"]),
        "worker_outcome": result.outcome,
        "telemetry": block,
        "updated_at": completed_at,
    }
    _persist(runtime_root, job_id, record, event="completed",
             details={"outcome": result.outcome, "exit_code": result.returncode, "reattached": True,
                      "telemetry": telemetry.completed_summary(block)})


def _telemetry_block(record: Mapping, streams: Mapping, *, completed_at: str,
                     spawned_at: str | None = None, reattached: bool = False) -> dict:
    """The completed record's ``telemetry`` block (settings-and-telemetry
    CP3, plan Design C), and the failure boundary (I6): it never raises an
    ``Exception``. A read or computation that fails gives
    :func:`telemetry.failed_block` -- no figures, one ``telemetry_failed``
    problem -- so the completion write it precedes carries the status,
    outcome and event it would carry without telemetry. ``KeyboardInterrupt``
    and ``SystemExit`` are not caught.

    The block is :func:`telemetry.session_block` of the worker's stdout,
    plus ``job_seconds`` (``created_at`` to ``completed_at``),
    ``worker_seconds`` and :func:`telemetry.dimensions`. The worker ran
    from the ``on_spawn`` flush (``spawned_at``; on the re-attach path the
    ``worker_spawned`` event's time, ``None`` when that line is missing) to
    its exit: the drain's ``direct_child_exited_at`` when its process group
    outlived it, else ``completed_at`` live -- ``launch`` returns at the
    exit -- and the stdout's last write on re-attach, where the worker may
    have exited long before this Controller looked."""
    try:
        block = telemetry.session_block(telemetry.read_worker_stdout(streams["stdout_path"]))
        drain = record.get("worker_group_drain")
        exited_at = drain.get("direct_child_exited_at") if isinstance(drain, Mapping) else None
        if reattached:
            events_path = streams.get("events_path") or str(Path(streams["stdout_path"]).parent / "events.jsonl")
            spawned_at = telemetry.first_event_time(telemetry.read_events(events_path), "worker_spawned")
            exited_at = exited_at or telemetry.mtime_time(streams["stdout_path"])
        else:
            exited_at = exited_at or completed_at
        block.update(
            job_seconds=telemetry.seconds_between(record.get("created_at"), completed_at),
            worker_seconds=telemetry.seconds_between(spawned_at, exited_at),
            **telemetry.dimensions(record),
        )
        return block
    except Exception as exc:  # the failure boundary: never raised into the completion write
        return telemetry.failed_block(exc)


def _record_drain_detached(runtime_root: Path, root: Path, job_id: str, record: JobRecord,
                           result: worker.DrainDetached, *, worker_pid: Any) -> None:
    """A :class:`worker.DrainDetached` (plan C step 3, D): the record stays
    ``LAUNCHED`` at ``DRAINING`` with ``drain_detached_at``, the
    ``worker_drain_detached`` event follows it -- both while the caller
    still holds the job's supervisor lock -- then
    :class:`~controller.errors.OwnedWorkDetachedError` (exit 45), whose
    unwinding releases that lock. The anchor keeps the lifecycle lock."""
    record = _with_worker_state(record, worker.DRAINING, {
        **result.worker_state, "supervisor_facts": result.supervisor_facts,
    })
    # Settings-and-telemetry CP2: the bound this drain applied, next to
    # `drain_detached_at`, so every message printing it -- this one and
    # `status`/`follow`'s, in any later invocation -- prints the real one.
    record = {**record, "drain_detached_at": result.drain_detached_at,
              "drain_detach_seconds": result.drain_detach_seconds}
    remaining = [{"pid": entry.get("pid"), "cmdline": entry.get("cmdline")} for entry in result.remaining]
    _persist(runtime_root, job_id, record, event="worker_drain_detached", details={
        "drain_detached_at": result.drain_detached_at, "remaining": remaining,
    })
    raise OwnedWorkDetachedError(
        f"worker pid {worker_pid} exited, but {len(remaining)} owned "
        f"process(es) are still running after {applied_drain_bound(record)} s: "
        + ", ".join(f"{entry['pid']} ({entry['cmdline']})" for entry in remaining)
        + f" -- job {job_id} stays held (LAUNCHED, DRAINING); either run "
        f"`{_resume_command(root)}` to re-attach and keep draining, or end them, "
        f"then run it",
        evidence={"job_id": job_id, "drain_detached_at": result.drain_detached_at,
                  "remaining": list(result.remaining)},
    )


def applied_drain_bound(record: Mapping) -> Any:
    """The drain bound ``record``'s detached drain applied: its recorded
    ``drain_detach_seconds``, or, for a record written before the field
    existed, :data:`worker.DRAIN_DETACH_SECONDS`."""
    recorded = record.get("drain_detach_seconds")
    if isinstance(recorded, (int, float)) and not isinstance(recorded, bool):
        return recorded
    return worker.DRAIN_DETACH_SECONDS


def _abandon_supervision(managed_repo: Any, *, identity: Any, runtime_root: Path, path: Path,
                         acknowledge: bool) -> None:
    """Phase 1 of ``resume --abandon`` (plan E), for the named record only,
    when it carries ``worker_state``: under its supervisor lock, refuse
    while its worker or any owned process is ``active`` (exit 45, naming
    the pids and how to end them), or ``unverifiable`` without
    ``acknowledge``; once both are verified gone, end a leftover stdin
    anchor (identity-checked ``SIGKILL``: Controller infrastructure, not
    work), before any lifecycle-lock attempt."""
    root = managed_repo.root
    job_id = path.stem
    candidate = _supervision_candidate(path, managed_repo, identity, valid=False)
    if candidate is None:
        return
    with _supervisor_lock(runtime_root, job_id, record=candidate, nothing_done="nothing was abandoned"):
        record = _supervision_candidate(path, managed_repo, identity, valid=False)
        if record is None:
            return
        worker_process = record.get("worker_process")
        if worker_process is not None:
            assessment = worker.assess_worker_liveness(worker_process if isinstance(worker_process, Mapping) else {})
            if assessment.verdict == worker.ACTIVE:
                raise LifecycleWorkerActiveError(
                    f"`resume --abandon {job_id!r}` refused, and no flag overrides it: "
                    f"{_active_guidance(job_id, assessment, root)} Follow it: "
                    f"`{follow_command(runtime_root, root)}`",
                    evidence={"job_id": job_id, "worker_liveness": assessment.to_dict()},
                )
            if assessment.verdict == worker.UNVERIFIABLE and not acknowledge:
                raise LifecycleWorkerUnverifiableError(
                    f"`resume --abandon {job_id!r}` refused without --acknowledge-unverifiable-worker: "
                    f"{_unverifiable_guidance(job_id, assessment, root)}",
                    evidence={"job_id": job_id, "worker_liveness": assessment.to_dict()},
                )
        entries, verifiable = _scan_record_owned(record)
        if entries:
            raise LifecycleWorkerActiveError(
                f"`resume --abandon {job_id!r}` refused, and no flag overrides it: "
                f"{_owned_guidance(job_id, entries, root, then=_abandon_command(job_id, root))}",
                evidence={"job_id": job_id, "owned_processes": entries},
            )
        if not verifiable and not acknowledge:
            raise LifecycleWorkerUnverifiableError(
                f"`resume --abandon {job_id!r}` refused without --acknowledge-unverifiable-worker: job "
                f"{job_id}'s owned processes cannot be verified from here (/proc could not be listed, or a "
                f"same-uid process's stat could not be read). If they are gone, state so with "
                f"`{_abandon_command(job_id, root, acknowledge=True)}`.",
                evidence={"job_id": job_id},
            )
        anchor = record.get("worker_anchor")
        if isinstance(anchor, Mapping) and not worker.end_recorded_process(anchor):
            raise LifecycleWorkerActiveError(
                f"`resume --abandon {job_id!r}` could not end job {job_id}'s stdin anchor (pid "
                f"{anchor.get('pid')}), which holds the lifecycle lock -- nothing was abandoned",
                evidence={"job_id": job_id, "worker_anchor": dict(anchor)},
            )


_JOB_ID_CONSTRAINT = (
    "JOB_ID must name a pending job file directly: the non-empty, bare stem (no '/', no NUL, not '.' "
    "or '..') of a regular file <runtime>/jobs/<JOB_ID>.json -- not a symlink, and not under "
    "jobs/abandoned/"
)


def _abandon_path(runtime_root: Path, managed_repo: Any, identity: Any, job_id: str) -> Path:
    """The ``JOB_ID`` constraint, checked before the file is read or anything
    is written: ``jobs/<JOB_ID>.json`` must be one of the entries the
    ``jobs/*.json`` scan yields, a regular file (not a symlink) whose
    resolved parent is ``jobs/`` itself. ``runtime.write_json`` contains
    writes to the runtime *root*, not to ``jobs/``, so without this a
    ``JOB_ID`` of ``../identity`` would replace the runtime's own
    ``identity.json``."""
    jobs_dir = Path(runtime_root) / "jobs"

    def refuse(detail: str) -> JobAbandonRefusedError:
        stems = sorted(entry.job_id for entry in pending_reconciliation_jobs(runtime_root, managed_repo, identity))
        return JobAbandonRefusedError(
            f"`resume --abandon {job_id!r}` refused: {detail}. {_JOB_ID_CONSTRAINT}. Pending job files "
            f"for {managed_repo.root}: {', '.join(stems) if stems else 'none'}",
            evidence={"job_id": job_id, "pending_job_ids": stems},
        )

    if not job_id or "/" in job_id or "\0" in job_id or job_id in (".", ".."):
        raise refuse("it is not a bare job-file stem")
    path = jobs_dir / f"{job_id}.json"
    if path not in _job_file_paths(runtime_root):
        raise refuse(f"there is no job file {path}")
    if _job_entry_kind(path) != _JOB_ENTRY_REGULAR:
        raise refuse(f"{path} is not a regular file (a symlink, directory, FIFO or other special file is "
                     f"never read or replaced -- remove it by hand)")
    if path.resolve().parent != jobs_dir.resolve():
        raise refuse(f"{path} does not resolve directly under {jobs_dir}")
    return path


def abandon(
    managed_repo: Any, *, identity: Any, runtime: Path, job_id: str,
    acknowledge_unverifiable_worker: bool = False,
) -> JobRecord:
    """``workflow-controller resume --abandon JOB_ID
    [--acknowledge-unverifiable-worker]``: the operator disposition for
    every pending job file ``resume`` cannot reconcile, and for a record
    whose worker is ``unverifiable``. Runs under the lifecycle lock (none
    for a target root that no longer resolves) and launches nothing. Writes
    only the Controller's own runtime, through ``runtime.write_json``/
    ``runtime.write_bytes``. Returns the terminal record it wrote.

    - **Marked ``FAILED`` in place**: a parseable record with a known
      ``schema_version``, for this target, whose status is not terminal --
      ``reconciliation_evidence: {"code": "OperatorAbandoned",
      "abandoned_status", "validity"}`` (``validate_record``'s failure
      reason, recorded as evidence only, never to decide), plus
      ``worker_liveness`` for a record carrying ``worker_process``.
    - **Replaced**: a file that does not parse as a JSON object, or a
      record with an unknown ``schema_version`` (no field can be trusted):
      its bytes are copied unchanged to ``jobs/abandoned/<file name>``,
      then it is replaced by a minimal terminal record with a ``null``
      ``target_repo`` -- from any target, since its target cannot be read.
    - **Refused**: a ``JOB_ID`` breaking the constraint, a file whose bytes
      cannot be read (nothing can be set aside, so it is cleared by hand), a
      terminal record, another target's record, or a newer generation's
      record (:class:`~controller.errors.JobAbandonRefusedError`, exit 20); a
      recorded worker that is ``active``
      (:class:`~controller.errors.LifecycleWorkerActiveError`, exit 45 --
      no flag overrides it); or one that is ``unverifiable`` without
      ``acknowledge_unverifiable_worker``
      (:class:`~controller.errors.LifecycleWorkerUnverifiableError`, exit
      45).

    **Phase 1** (worker-lifecycle-ownership CP5, plan E): for a record
    carrying ``worker_state``, :func:`_abandon_supervision` runs first,
    under the job's supervisor lock and before the lifecycle lock -- it
    refuses while the worker or any owned process lives and ends a leftover
    anchor, so ``abandon`` with only an orphaned anchor left succeeds at
    once."""
    path = _abandon_path(runtime, managed_repo, identity, job_id)
    if not managed_repo.root.is_dir():
        return _abandon_locked(managed_repo, identity=identity, runtime_root=runtime, path=path,
                               acknowledge=acknowledge_unverifiable_worker)
    # Worker-lifecycle-ownership CP5 (plan E): phase 1 before the lifecycle
    # lock, which a leftover anchor may still hold.
    _abandon_supervision(managed_repo, identity=identity, runtime_root=runtime, path=path,
                         acknowledge=acknowledge_unverifiable_worker)
    with _acquire_lifecycle_lock(runtime, managed_repo):
        return _abandon_locked(managed_repo, identity=identity, runtime_root=runtime, path=path,
                               acknowledge=acknowledge_unverifiable_worker)


def _abandon_locked(managed_repo: Any, *, identity: Any, runtime_root: Path, path: Path,
                    acknowledge: bool) -> JobRecord:
    root = managed_repo.root
    job_id = path.stem
    if not _is_regular_job_file(path):
        raise JobAbandonRefusedError(
            f"`resume --abandon {job_id!r}` refused: {path} is no longer a regular file",
            evidence={"job_id": job_id},
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise JobAbandonRefusedError(
            f"`resume --abandon {job_id!r}` refused: {path} cannot be read ({exc}), so its bytes cannot "
            f"be set aside under jobs/abandoned/ -- {_unreadable_clearing(path)}",
            evidence={"job_id": job_id, "error": str(exc), "clearing_command": _unreadable_clearing(path)},
        ) from exc
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        record = None
    if isinstance(record, dict):
        generation = _newer_generation(record, identity)
        if generation is not None:
            raise JobAbandonRefusedError(
                f"`resume --abandon {job_id!r}` refused: job {job_id} was written by Controller "
                f"generation {generation}, newer than this one ({identity.generation}); it is left "
                f"untouched, and `{_newer_generation_clearing(generation, root)}` clears it",
                evidence={"job_id": job_id, "record_generation": generation,
                          "running_generation": identity.generation},
            )
        if record.get("schema_version") == SCHEMA_VERSION:
            return _abandon_mark_failed(managed_repo, identity=identity, runtime_root=runtime_root,
                                        job_id=job_id, record=record, acknowledge=acknowledge)
    return _abandon_replace(runtime_root, path, raw,
                            prior_event_seq=_event_seq(record) if isinstance(record, dict) else None)


def _abandon_mark_failed(managed_repo: Any, *, identity: Any, runtime_root: Path, job_id: str,
                         record: dict, acknowledge: bool) -> JobRecord:
    root = managed_repo.root
    if record.get("target_repo") != str(root):
        raise JobAbandonRefusedError(
            f"`resume --abandon {job_id!r}` refused: job {job_id} belongs to target "
            f"{record.get('target_repo')!r}, not {root}",
            evidence={"job_id": job_id, "target_repo": record.get("target_repo")},
        )
    status = record.get("status")
    if isinstance(status, str) and status in TERMINAL_STATUSES:
        raise JobAbandonRefusedError(
            f"`resume --abandon {job_id!r}` refused: job {job_id} is already terminal ({status}) -- "
            f"it is history, not a pending job",
            evidence={"job_id": job_id, "status": status},
        )
    worker_liveness = None
    worker_process = record.get("worker_process")
    if worker_process is not None:
        assessment = worker.assess_worker_liveness(
            worker_process if isinstance(worker_process, Mapping) else {},
        )
        if assessment.verdict == worker.ACTIVE:
            raise LifecycleWorkerActiveError(
                f"`resume --abandon {job_id!r}` refused, and no flag overrides it: "
                f"{_active_guidance(job_id, assessment, root)} Follow it: `{follow_command(runtime_root, root)}`",
                evidence={"job_id": job_id, "worker_liveness": assessment.to_dict()},
            )
        if assessment.verdict == worker.UNVERIFIABLE and not acknowledge:
            raise LifecycleWorkerUnverifiableError(
                f"`resume --abandon {job_id!r}` refused without --acknowledge-unverifiable-worker: "
                f"{_unverifiable_guidance(job_id, assessment, root)}",
                evidence={"job_id": job_id, "worker_liveness": assessment.to_dict()},
            )
        worker_liveness = {**assessment.to_dict(), "acknowledged": assessment.verdict == worker.UNVERIFIABLE}
    validity = _validity_or_none(record, managed_repo, identity)
    now = _now()
    abandoned: JobRecord = {
        **record,
        "status": STATUS_FAILED,
        "transition_verified": False,
        "reconciliation_evidence": {
            "code": OPERATOR_ABANDONED_CODE,
            "abandoned_status": status,
            "validity": (
                "validate_record could not read the record's fields" if validity is None
                else (None if validity.valid else validity.reason)
            ),
        },
        "reconciled_at": now,
        "updated_at": now,
    }
    if worker_liveness is not None:
        abandoned["worker_liveness"] = worker_liveness
    return _persist(runtime_root, job_id, abandoned, event="abandoned",
                    details={"abandoned_status": status})


def _abandon_replace(runtime_root: Path, path: Path, raw: bytes, *,
                     prior_event_seq: int | None = None) -> JobRecord:
    """Copy the file's original bytes unchanged to ``jobs/abandoned/``
    (outside the ``jobs/*.json`` scan), then replace it with a minimal
    terminal record. An existing copy of the same name is never
    overwritten. ``prior_event_seq`` is the replaced object's own
    ``event_seq``, when it had a positive integer one, so the job's event
    log continues rather than repeating a ``seq``; nothing else from an
    untrusted record is carried."""
    copy_name = path.name
    if (Path(runtime_root) / "jobs" / "abandoned" / copy_name).exists():
        copy_name = f"{path.name}.{_new_job_id()}"
    original = f"jobs/abandoned/{copy_name}"
    runtime.write_bytes(runtime_root, original, raw)
    now = _now()
    minimal: JobRecord = {
        "schema_version": SCHEMA_VERSION,
        "job_id": path.stem,
        "target_repo": None,
        "status": STATUS_FAILED,
        "reconciliation_evidence": {"code": OPERATOR_ABANDONED_CODE, "original": original},
        "reconciled_at": now,
        "updated_at": now,
    }
    if prior_event_seq is not None:
        minimal["event_seq"] = prior_event_seq
    return _persist(runtime_root, path.stem, minimal, event="abandoned", details={"original": original})


# ---------------------------------------------------------------------------
# Record assembly.
# ---------------------------------------------------------------------------


def _new_job_id() -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(4)}"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _selected_action_dict(decision: Decision) -> dict:
    """``selected_action``. ``task_addendum`` (automatic-lifecycle-
    orchestration CP4B, additive) records the addendum the worker's task
    carried after the command, ``None`` when the task was the bare
    command; ``command`` stays the bare command every reader keys on."""
    return {
        "kind": "slash_command",
        "command": decision.action.command if decision.action is not None else None,
        "task_addendum": decision.action.task_addendum if decision.action is not None else None,
        "automatic": decision.automatic,
        "declined": decision.declined,
        "reason": decision.reason,
        "evidence": list(decision.evidence),
    }


def worker_task(action: Any) -> str:
    """The task a worker is launched with: ``action.command`` when it
    carries no ``task_addendum``, else ``f"{command}\\n\\n{task_addendum}"``
    (CP4B). ``worker.launch``'s user-only scan sees this whole string."""
    if action.task_addendum is None:
        return action.command
    return f"{action.command}\n\n{action.task_addendum}"


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
        # Additive (release-runtime-observability CP2): `validate_record`
        # reads none of it, so a record without it stays valid.
        "controller_runtime": identity.runtime_record(ident),
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
        # CP4 (worker-lifecycle-ownership): why the outcome is what it is,
        # and what the supervisor owned (plan C step 4, D).
        "stream_diagnosis": result.stream_diagnosis,
        "owned_processes_seen": result.owned_processes_seen,
    }


#: The ``worker_state`` fields the record keeps from ``on_state_change``'s
#: ``details`` (plan D): presentation data, rewritten at each flush, never
#: an input to the stream state (replay rebuilds that).
_WORKER_STATE_DETAIL_FIELDS = ("turns", "waiting_on", "owned_processes", "owned_processes_seen_count",
                               "excluded_processes", "scan")


def _with_worker_state(record: JobRecord, state: str, details: Mapping) -> JobRecord:
    """``record`` with ``worker_state`` at ``state`` (``since`` kept while
    the state is unchanged) and the supervisor facts ``details`` carries
    once the session was ended (``ending_offset``, the two breach
    declarations, ``settled_wakeups``) lifted to the record's top level,
    where ``validate_record`` and a re-attaching ``resume`` read them."""
    previous = record.get("worker_state") if isinstance(record.get("worker_state"), Mapping) else {}
    since = previous.get("since") if previous.get("state") == state and previous.get("since") else _now()
    worker_state = {key: previous[key] for key in _WORKER_STATE_DETAIL_FIELDS if key in previous}
    worker_state.update({key: details[key] for key in _WORKER_STATE_DETAIL_FIELDS if key in details})
    updated = {**record, "worker_state": {"state": state, "since": since, **worker_state}, "updated_at": _now()}
    facts = details.get("supervisor_facts") if isinstance(details.get("supervisor_facts"), Mapping) else {}
    ending_offset = details.get("ending_offset", facts.get("ending_offset"))
    if ending_offset is not None:
        updated["ending_offset"] = ending_offset
    for key in ("wakeup_overdue_declared_at", "command_lifecycle_overdue_declared_at",
                "command_lifecycle_overdue_command_uuid"):
        if facts.get(key) is not None:
            updated[key] = facts[key]
    if facts.get("settled_wakeups"):
        updated["settled_wakeups"] = list(facts["settled_wakeups"])
    return updated


def _worker_state_event(state: str) -> str:
    """``worker_running``, ``worker_waiting``, ``worker_ending``,
    ``worker_draining`` or ``worker_ended`` (plan D)."""
    return f"worker_{state.lower()}"


def _worker_state_event_details(record: JobRecord, details: Mapping, *, state_changed: bool) -> dict:
    """A ``worker_<state>`` event's compact ``details``: whether the state
    itself changed (``False`` for a flush of what the worker waits on or
    owns), the turn count, what it waits on by id, how many processes it
    owns, and ``ending_offset`` once the session was ended."""
    waiting_on = details.get("waiting_on") if isinstance(details.get("waiting_on"), Mapping) else {}
    compact = {
        "state_changed": state_changed,
        "turns": details.get("turns"),
        "tasks": [task.get("task_id") for task in waiting_on.get("tasks") or []],
        "wakeups": [{"tool_use_id": w.get("tool_use_id"), "state": w.get("state")}
                    for w in waiting_on.get("wakeups") or []],
        "command_lifecycles": [b.get("command_uuid") for b in waiting_on.get("command_lifecycles") or []],
        "owned_processes": len(details.get("owned_processes") or []),
    }
    if record.get("ending_offset") is not None:
        compact["ending_offset"] = record["ending_offset"]
    return compact


# ---------------------------------------------------------------------------
# I/O -- defined here (module scope), never inside `execute_step`, so the
# real `controller.runtime` import is always what they call. See this
# module's own docstring, "A deliberate name shadow".
# ---------------------------------------------------------------------------


#: The version every lifecycle event line carries as ``v``
#: (``workflow-controller-release-runtime-observability`` CP5).
EVENT_LOG_VERSION = 1


def _persist(runtime_root: Path, job_id: str, record: JobRecord, *, event: str,
             details: Mapping | None = None) -> JobRecord:
    """Write the job record, then append ``event`` to
    ``jobs/<job_id>/events.jsonl``, and return the record written.

    ``event_seq`` (additive, CP5) is incremented on the record before the
    write -- a record without it starts at ``1`` -- and the event carries
    that value as ``seq``, so ``resume`` and ``--abandon`` continue the
    sequence from the record alone, never reading the log. The record write
    is the authority and raises as before; the append after it is
    best-effort (``runtime.append_jsonl_best_effort``), so a lost line is a
    gap in ``seq``, never a repeat and never a lifecycle failure. Every
    caller keeps the returned record, so the next write continues from it."""
    seq = (_event_seq(record) or 0) + 1
    record = {**record, "event_seq": seq}
    runtime.write_json(runtime_root, f"jobs/{job_id}.json", record)
    runtime.append_jsonl_best_effort(runtime_root, f"jobs/{job_id}/events.jsonl", {
        "v": EVENT_LOG_VERSION, "seq": seq, "at": _now(), "job_id": job_id, "event": event,
        **(details or {}),
    })
    return record


def _event_seq(record: Mapping) -> int | None:
    """``record``'s ``event_seq`` when it is a positive integer, else
    ``None``. ``validate_record`` does not read the field, so a malformed
    value must not raise here, inside ``resume`` or ``--abandon``; the
    sequence restarts at ``1`` instead."""
    value = record.get("event_seq")
    return value if type(value) is int and value > 0 else None


def _final_event(status: str) -> str:
    """The job event a final (or no-launch) status is recorded under."""
    return status.lower()


def _reconciled_details(record: JobRecord) -> dict:
    """The ``reconciled`` event's details: the new status and, when the
    reconciliation recorded one, its evidence code."""
    evidence_block = record.get("reconciliation_evidence")
    code = evidence_block.get("code") if isinstance(evidence_block, Mapping) else None
    return {"status": record.get("status"), "code": code}


# ---------------------------------------------------------------------------
# Run records (`workflow-controller-release-runtime-observability` CP5):
# `runs/<run_id>.json` and `runs/<run_id>/events.jsonl`, written by the one
# process that owns the run. Both are best-effort and read by no lifecycle
# decision.
# ---------------------------------------------------------------------------

RUN_SCHEMA_VERSION = 1
RUN_STATE_RUNNING = "running"
RUN_STATE_ENDED = "ended"
RUN_STATE_INTERRUPTED = "interrupted"


class RunRecord:
    """One ``step``/``run`` invocation's run record and event log. The
    per-run ``seq`` is held here, in the owning process; nothing else
    appends to the log. Every write is best-effort."""

    def __init__(self, runtime_root: Path, *, command: str, target_repo: str, max_steps: int | None,
                 ident: Any) -> None:
        self.runtime_root = runtime_root
        self.run_id = _new_job_id()
        self._seq = 0
        now = _now()
        self.record: dict = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": self.run_id,
            "command": command,
            "target_repo": target_repo,
            "max_steps": max_steps,
            "controller_process": _controller_process(),
            "controller_runtime": identity.runtime_record(ident),
            "state": RUN_STATE_RUNNING,
            "exit_code": None,
            "job_ids": [],
            "current_job_id": None,
            "started_at": now,
            "updated_at": now,
            "ended_at": None,
        }
        self._flush()
        self.event("run_started")

    def _flush(self) -> None:
        self.record["updated_at"] = _now()
        runtime.write_json_best_effort(self.runtime_root, f"runs/{self.run_id}.json", self.record)

    def event(self, event: str, **details: Any) -> None:
        self._seq += 1
        runtime.append_jsonl_best_effort(self.runtime_root, f"runs/{self.run_id}/events.jsonl", {
            "v": EVENT_LOG_VERSION, "seq": self._seq, "at": _now(), "run_id": self.run_id, "event": event,
            **details,
        })

    def job_started(self, job_id: str) -> None:
        self.record["job_ids"] = [*self.record["job_ids"], job_id]
        self.record["current_job_id"] = job_id
        self._flush()
        self.event("job_started", job_id=job_id)

    def job_ended(self, job_id: str, status: str) -> None:
        self.record["current_job_id"] = None
        self._flush()
        self.event("job_ended", job_id=job_id, status=status)

    def close(self, exit_code: int) -> None:
        self.record.update(state=RUN_STATE_ENDED, exit_code=exit_code, ended_at=_now())
        self._flush()
        self.event("run_ended", exit_code=exit_code)
        _OPEN_RUNS.pop(self.run_id, None)

    def interrupt(self) -> None:
        """Ctrl-C: ``current_job_id`` is kept -- its worker may still run."""
        self.record.update(state=RUN_STATE_INTERRUPTED, exit_code=None, ended_at=_now())
        self._flush()
        self.event("run_interrupted")
        _OPEN_RUNS.pop(self.run_id, None)

    def discard(self) -> None:
        """Unregister without writing: the record stays ``running``."""
        _OPEN_RUNS.pop(self.run_id, None)


def _controller_process() -> dict | None:
    """This process's identity, as ``worker.capture_worker_process`` records
    a worker's -- ``None`` if it cannot be read, since the run record is
    best-effort and must never stop the run it describes."""
    try:
        return worker.capture_worker_process(os.getpid()).to_dict()
    except Exception:  # noqa: BLE001 -- best-effort by contract
        return None


#: Runs open in this process, by id -- how :func:`execute_step`'s
#: ``run_id`` reaches the run whose log it mirrors job events into.
_OPEN_RUNS: dict[str, RunRecord] = {}


def open_run(runtime_root: Path, *, command: str, target_repo: str, max_steps: int | None,
             ident: Any) -> RunRecord:
    """Create and register a run record (state ``running``). Never raises
    for a failed write: the record is best-effort."""
    run = RunRecord(runtime_root, command=command, target_repo=target_repo, max_steps=max_steps, ident=ident)
    _OPEN_RUNS[run.run_id] = run
    return run


def _run_job_started(run_id: str | None, job_id: str) -> None:
    run = _OPEN_RUNS.get(run_id) if run_id is not None else None
    if run is not None:
        run.job_started(job_id)


def _run_job_ended(run_id: str | None, job_id: str, status: str) -> None:
    run = _OPEN_RUNS.get(run_id) if run_id is not None else None
    if run is not None:
        run.job_ended(job_id, status)


def _pending_handoff(runtime_root: Path) -> dict | None:
    return runtime.read_json(runtime_root / "handoff.json")


#: The ``worker_streams.format`` every ``LAUNCHED`` record written since
#: ``workflow-controller-release-runtime-observability`` CP4 carries.
WORKER_STREAM_FORMAT = "stream-json"


def _create_worker_streams(runtime_root: Path, job_id: str) -> dict:
    """Create ``jobs/<job_id>/worker.stdout`` and ``worker.stderr`` empty
    (``runtime.create_log_file``: contained, ``O_EXCL``, mode ``0o600``)
    before the worker is spawned, and return the record's
    ``worker_streams`` block, so a follower can find the logs before the
    worker writes anything. ``events_path`` names the job's lifecycle event
    log beside them. An ``OSError`` is wrapped in
    :class:`~controller.errors.WorkerLaunchError`: no worker exists yet, so
    the caller records ``WorkerNotStarted``, exactly as for a ``Popen``
    failure."""
    paths = {}
    for key, name in (("stdout_path", "worker.stdout"), ("stderr_path", "worker.stderr")):
        rel = f"jobs/{job_id}/{name}"
        try:
            paths[key] = str(runtime.create_log_file(runtime_root, rel))
        except OSError as exc:
            raise WorkerLaunchError(
                f"could not create the worker's stream file {rel} under {runtime_root}: {exc}",
                evidence={"runtime_root": str(runtime_root), "path": rel, "os_error": str(exc)},
            ) from exc
    return {
        "format": WORKER_STREAM_FORMAT,
        **paths,
        "events_path": str(Path(paths["stdout_path"]).parent / "events.jsonl"),
    }


def _announce_group_drain(pid: int, remaining_pids: list[int]) -> None:
    """The drain line. An ``OSError`` writing it is ignored: a closed
    stderr must not end the worker's group (``on_group_drain``'s own
    contract would)."""
    if remaining_pids:
        waiting = (f"waiting for {len(remaining_pids)} process(es) still in its process group: "
                   f"{' '.join(str(member) for member in remaining_pids)}")
    else:
        waiting = "waiting for an unknown number of processes still in its process group"
    try:
        print(f"worker pid {pid} exited; {waiting}", file=sys.stderr, flush=True)
    except OSError:
        pass


def _no_launch_record(
    runtime_root: Path, managed_repo: Any, ident: Any, work_item_id: str | None, observed_phase: Any,
    pre_state: dict, decision: Decision, *, status: str, run_id: str | None = None,
) -> JobRecord:
    """The single-flush record for every outcome that never reaches a
    worker launch: ``GATE_BLOCKED``, ``DECLINED``, ``HANDOFF_PENDING``.
    Its one event is the status itself, carrying the decision's reason;
    a run mirrors it as a job that started and ended at once."""
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
    if run_id is not None:
        record["run_id"] = run_id
    details: dict = {"reason": decision.reason}
    if status == STATUS_GATE_BLOCKED:
        details["what_is_required"] = decision.gate.what_is_required
    _run_job_started(run_id, job_id)
    record = _persist(runtime_root, job_id, record, event=_final_event(status), details=details)
    _run_job_ended(run_id, job_id, status)
    return record


#: ``reconciliation_evidence.code`` of a job whose worker left the bound
#: milestone branch, or rewrote it (trunk-branch-pr-release-orchestration
#: CP8).
BRANCH_INVARIANT_VIOLATED_CODE = "BranchInvariantViolated"


def _branch_binding_block(binding: Mapping, pre_step_tip: str | None) -> dict:
    return {"work_item_id": binding["work_item_id"], "branch": binding["branch"], "pre_step_tip": pre_step_tip}


def _worker_disallowed_tools(route: routing.ResolvedRoute, binding: Mapping | None) -> tuple[str, ...]:
    return routing.worker_disallowed_tools(route, branch_bound=binding is not None)


def _branch_invariant_violation(root: Path, runtime_root: Path, record: Mapping) -> dict | None:
    """The post-step verification of a job launched under a binding (its
    record's ``branch_binding``): ``None`` when it holds or no binding
    governed the job, else the ``BranchInvariantViolated`` evidence. A
    missing pre-step tip is itself a violation: there is nothing the new
    tip can be shown to descend from."""
    block = record.get("branch_binding")
    if block is None:
        return None
    ctx = milestone_branch.Context(repo_root=root, runtime_root=runtime_root)
    try:
        if block.get("pre_step_tip") is None:
            raise BranchInvariantViolatedError(
                f"the job recorded no pre-step tip for {block.get('branch')}", evidence=dict(block))
        milestone_branch.verify_post_step(ctx, block, block["pre_step_tip"])
    except ControllerError as exc:  # an undecidable Git read fails closed too (I9)
        return {"code": BRANCH_INVARIANT_VIOLATED_CODE, "error": type(exc).__name__, "message": exc.message,
                "evidence": exc.evidence}
    return None


def _branch_gate_record(runtime_root: Path, managed_repo: Any, ident: Any, snapshot: Any,
                        gate: milestone_branch.Gate, *, run_id: str | None) -> JobRecord:
    """A repository-preflight gate (CP8), recorded through
    :func:`_no_launch_record` as ``GATE_BLOCKED``: the gate's work item and
    its Workflow phase when the state has it (``NO_PHASE`` otherwise, as for
    ``bound_item_missing`` or a trunk gate), and the preflight's
    :class:`~controller.decision.HumanGate`, phase ``MILESTONE_BRANCH``."""
    view = snapshot.work_items.get(gate.work_item_id) if gate.work_item_id is not None else None
    work_item = target_state.NoWorkItemYet if view is None else view
    phase = NO_PHASE if view is None else view.phase
    decision = Decision(
        observed_phase=phase,
        evidence=(f"repository preflight: {gate.code} ({gate.branch})",
                  *(f"exit: {exit_}" for exit_ in gate.exits)),
        action=None, automatic=False, gate=branch_human_gate(str(managed_repo.root), gate),
        declined=False, reason=gate.message,
    )
    pre_state = _capture_pre_state(managed_repo, snapshot, work_item)
    return _no_launch_record(runtime_root, managed_repo, ident, gate.work_item_id, phase, pre_state, decision,
                             status=STATUS_GATE_BLOCKED, run_id=run_id)


def _protocol_block(decision: Decision) -> dict:
    """The job record's optional ``protocol`` block (plan D.1), written only
    for a protocol job: the decision as received, its state identity, the
    release and protocol version and the managed-script digest map it was
    made under (the reference for that job's own verification and
    ``resume``), and ``reconcile``, filled when the outcome is."""
    info = decision.protocol
    document = dict(info.document)
    return {
        "decision": document,
        "envelope_digest": hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "action_id": info.action_id,
        "state_identity": info.state_identity,
        "workflow_release": info.release,
        "protocol_version": info.protocol_version,
        "script_sha256": dict(info.script_digests or {}),
        "decided_ns": time.time_ns(),
        "reconcile": None,
    }


def _unstable_gate(managed_repo: Any, work_item: Any, decision: Decision, seen: list,
                   advisories: tuple[str, ...] = ()) -> Decision:
    """The ``decision_unstable`` gate after :data:`protocol_decision.
    MAX_DECISIONS_PER_STEP` stale answers in one step, naming the last two
    state identities (or, for ``plan.start``, the last two
    ``snapshot.work_item_ids``)."""
    last = [value for value in seen[-2:]]
    what = "work item ids" if decision.protocol.state_identity is None else "state identities"
    return protocol_decision.gate_for(
        managed_repo, work_item, decision, protocol_decision.DECISION_UNSTABLE,
        f"the Workflow's state changed under {len(seen)} consecutive decisions, so none was launched; the last "
        f"{what} were {' and '.join(str(value) for value in last)}. Something else is writing the target "
        "repository's state; stop it, then re-run", advisories=advisories)


def _decide_protocol_current(
    managed_repo: Any, *, runtime: Path, snapshot: Any, work_item: Any, work_item_id: str | None,
    pre_state: dict, base: str | None,
) -> tuple[Decision, Any, dict]:
    """The protocol path's decision, current (plan D.2.1): ``(decision,
    work_item, pre_state)``. The Workflow's ``verify`` runs first (plan A.3):
    an unhealthy repository is the ``workflow_unhealthy`` gate and nothing is
    decided. An ``automatic`` decision is checked against the
    Workflow before anything is recorded and, when stale, discarded and
    re-decided from a fresh read -- at most
    :data:`protocol_decision.MAX_DECISIONS_PER_STEP` decisions per step,
    after which the answer is the ``decision_unstable`` gate. The loop guard
    (:func:`_no_progress_gate`) then applies to the decision that stands.
    Every other disposition is a gate or a no-action answer that launches
    nothing, so it is returned as the Workflow gave it."""
    # Plan A.3: the Workflow's own `verify` is the step's protocol preflight;
    # an unhealthy answer is a gate, and nothing is decided.
    unhealthy, advisories = protocol_decision.preflight(managed_repo, work_item)
    if unhealthy is not None:
        return unhealthy, work_item, pre_state
    seen: list = []
    for _attempt in range(protocol_decision.MAX_DECISIONS_PER_STEP):
        decision = protocol_decision.decide(managed_repo, work_item, base=base, advisories=advisories)
        if decision.protocol is None or not decision.automatic:
            return decision, work_item, pre_state
        info = decision.protocol
        seen.append(info.state_identity if info.state_identity is not None
                    else tuple((info.document.get("snapshot") or {}).get("work_item_ids") or ()))
        if protocol_decision.check_currency(managed_repo, decision).current:
            gate = _no_progress_gate(runtime, managed_repo, work_item, decision, advisories=advisories)
            return (decision if gate is None else gate), work_item, pre_state
        snapshot = target_state.read(managed_repo)
        work_item = target_state.select_work_item(snapshot, work_item_id=work_item_id)
        pre_state = _capture_pre_state(managed_repo, snapshot, work_item)
    return _unstable_gate(managed_repo, work_item, decision, seen, advisories), work_item, pre_state


def _protocol_job_class(record: Any, target_repo: str, work_item_id: str | None, action_id: str) -> tuple | None:
    """For the loop guard: ``((created_at, decided_ns, job_id), counted, end_identity,
    start_identity)``
    of a record that is a protocol job of this repository for this ``(work
    item, action id)`` pair, else ``None``. ``counted`` is ``None`` for a job
    that is neither a ``no_progress`` nor a reset (a ``FAILED`` job, an
    interrupted one, one with no reconcile class yet), else the reconcile
    class; ``end_identity`` is the state identity the job's reconcile found
    the work item at when it ended, ``start_identity`` the one it was decided
    at; either is ``None`` when the record keeps none."""
    if not isinstance(record, dict) or record.get("target_repo") != target_repo:
        return None
    block = record.get("protocol")
    if not isinstance(block, dict) or block.get("action_id") != action_id \
            or record.get("work_item_id") != work_item_id:
        return None
    decided_ns = block.get("decided_ns")
    key = (str(record.get("created_at")), decided_ns if isinstance(decided_ns, int) else 0,
           str(record.get("job_id")))
    reconcile = block.get("reconcile")
    reconcile_class = reconcile.get("class") if isinstance(reconcile, dict) else None
    reconcile_to = reconcile.get("to") if isinstance(reconcile, dict) else None
    end_identity = reconcile_to.get("state_identity") if isinstance(reconcile_to, dict) else None
    if record.get("status") == STATUS_FINISHED and isinstance(reconcile_class, str):
        return key, reconcile_class, end_identity, block.get("state_identity")
    return key, None, None, None


def _no_progress_gate(runtime_root: Path, managed_repo: Any, work_item: Any, decision: Decision,
                      advisories: tuple[str, ...] = ()) -> Decision | None:
    """The loop guard of a protocol target (plan D.3): the ``no_progress_repeated``
    gate when the two most recent protocol jobs for this ``(work item,
    action id)`` pair both ended ``no_progress``, else ``None``. A job that
    made progress or reached a gate resets the count; a ``FAILED`` or
    interrupted job, or one not yet reconciled, is skipped -- neither counted
    nor a reset. Keyed on the pair, never on the state identity; but a streak
    ends once the work item has moved since a counted job ended (the next
    job was decided at, or the current decision has, a state identity that
    differs from the one that job's reconcile left), because that change did
    not come from the counted jobs themselves; the two jobs counted must
    form one contiguous streak."""
    info = decision.protocol
    if info.action_id is None:
        return None
    work_item_id = None if work_item is target_state.NoWorkItemYet else work_item.work_item_id
    if info.action_id == protocol_decision.PLAN_START:
        work_item_id = None
    jobs_dir = Path(runtime_root) / "jobs"
    try:
        paths = sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []
    except OSError:
        return None
    found = []
    for path in paths:
        if not _is_regular_job_file(path):
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        classified = _protocol_job_class(record, str(managed_repo.root), work_item_id, info.action_id)
        if classified is not None and classified[1] is not None:
            found.append((classified[0], classified[1], classified[2], classified[3]))
    recent = sorted(found, reverse=True)[:2]
    if len(recent) < 2 or any(reconcile_class != "no_progress" for _key, reconcile_class, _end, _start in recent):
        return None
    last_end = recent[0][2]
    if info.state_identity is not None and last_end is not None and last_end != info.state_identity:
        return None
    # The two must be one contiguous streak: the newer job was decided where the older one ended.
    older_end, newer_start = recent[1][2], recent[0][3]
    if older_end is not None and newer_start is not None and older_end != newer_start:
        return None
    jobs = " and ".join(key[2] for key, _class, _end, _start in recent)
    return protocol_decision.gate_for(
        managed_repo, work_item, decision, protocol_decision.NO_PROGRESS_REPEATED,
        f"the last two jobs for {info.action_id}"
        + ("" if work_item_id is None else f" on {work_item_id}")
        + f" ({jobs}) both ended without progress, so a third is not launched; read their records, then "
        "change the work item (take the action by hand, or fix what stops it) and the count restarts",
        advisories=advisories)


def _worker_route(decision: Decision, work_item: Any, options: routing.RoutingOptions) -> routing.ResolvedRoute:
    """The launched worker's route (automatic-lifecycle-orchestration CP6):
    its role, from durable state only (the observed phase, the selected
    command and the work item's ``registry_complete``; a ``NoWorkItemYet``
    bootstrap has none), resolved against the operator's overrides."""
    if decision.protocol is not None:
        # A protocol job is routed by its action id's route key, never by a
        # command stem (orchestration-protocol-v1 C.3).
        return options.resolve(decision.protocol.route)
    registry_complete = None if work_item is target_state.NoWorkItemYet else work_item.registry_complete
    role = routing.role_for(decision.observed_phase, decision.action.command, registry_complete)
    return options.resolve(role)


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
    routing: routing.RoutingOptions = routing.NO_OVERRIDES,
    run_id: str | None = None,
    drain_detach_seconds: float | None = None,
    controller_settings: Mapping | None = None,
    auto_merge: bool = True,
    wait_seconds: int = 0,
    poll_seconds: int = 30,
    on_wait: Callable[[str, str], None] | None = None,
) -> JobRecord | Decision:
    """Execute (at most) one Controller job against ``managed_repo``, under
    the target worktree's lifecycle lock (automatic-lifecycle-orchestration
    CP5), which covers the whole call.

    The lock is taken first: held ->
    :class:`~controller.errors.LifecycleWorkerActiveError` (exit 45),
    naming the lock and how to find its holders, and nothing is decided,
    recorded or launched. Then, before deciding,
    :class:`~controller.errors.PendingJobReconciliationError` (exit 20)
    refuses while any job file ``resume`` would reconcile or raise on for
    this target is pending (:func:`pending_reconciliation_jobs`). Only then
    does :func:`_execute_step_locked` run the nine job-execution steps;
    the worker inherits the lock's descriptor, so an orphaned worker keeps
    the lock until it really exits.

    ``routing`` (automatic-lifecycle-orchestration CP6) is the operator's
    routing overrides; the default is the built-in routing alone. The
    launched worker's resolved route is recorded as ``worker_route`` from
    the ``PLANNED`` flush on, and its model, effort and disallowed tools
    reach the worker's command line.

    ``run_id`` (``workflow-controller-release-runtime-observability`` CP5)
    names the ``step``/``run`` invocation this job belongs to. It is
    recorded as the job record's additive ``run_id``, and when that run is
    open in this process (:func:`open_run`) the job's start and end are
    mirrored into its log. Every job-record write also appends its named
    event to ``jobs/<job_id>/events.jsonl`` (:func:`_persist`).

    ``drain_detach_seconds`` (settings-and-telemetry CP2) bounds the
    launched worker's owned-process drain (``None``:
    :data:`worker.DRAIN_DETACH_SECONDS`). ``controller_settings``, when
    given, is recorded as the launched job's optional
    ``controller_settings`` block (the settings file's path and digest, and
    each effective value with its source).

    ``auto_merge``, ``wait_seconds`` and ``poll_seconds`` (auto-merge-
    release-wait A.2) are the ``merge.*`` settings the repository preflight
    reads through its :class:`~controller.milestone_branch.Context`.
    ``wait_seconds`` above 0 (``run`` only, E.2) lets the preflight poll a
    waitable gate (:func:`~controller.milestone_branch.waiting_preflight`)
    under the lock this call holds; ``on_wait(code, deadline)`` is called
    once per gate code it waits on. Only the final outcome is recorded."""
    with _acquire_lifecycle_lock(runtime, managed_repo) as lifecycle_lock:
        _refuse_pending_reconciliation(runtime, managed_repo, identity)
        return _execute_step_locked(
            managed_repo, work_item_id=work_item_id, identity=identity, runtime=runtime,
            permission_mode=permission_mode, timeout=timeout, claude_bin=claude_bin,
            lifecycle_lock=lifecycle_lock, routing=routing, run_id=run_id,
            drain_detach_seconds=drain_detach_seconds, controller_settings=controller_settings,
            auto_merge=auto_merge, wait_seconds=wait_seconds, poll_seconds=poll_seconds, on_wait=on_wait,
        )


def closed_out_decision(binding: Mapping[str, Any]) -> Decision:
    """The no-action outcome of a step that closed out ``binding`` (the
    ``CLOSED`` record) after its release wait (auto-merge-release-wait
    D.4): reason :data:`REASON_CLOSED_OUT_RELEASED`, whose only evidence
    line names the release. The next step starts from the trunk and plans
    the next milestone."""
    return Decision(
        observed_phase=milestone_branch.MILESTONE_COMPLETE,
        evidence=(milestone_branch.release_text(binding["release"]),),
        action=None, automatic=False, gate=None, declined=False, reason=REASON_CLOSED_OUT_RELEASED,
    )


def acknowledge_milestone_binding(managed_repo: Any, *, runtime: Path, work_item_id: str,
                                  disposition: str) -> dict:
    """``milestone-binding --new-pr``/``--abandon`` (trunk-branch-pr-release-
    orchestration CP8, TBR-R4-001): under the target's lifecycle lock, like
    ``step``, :func:`controller.milestone_branch.acknowledge`. It writes no
    job record, launches no worker and touches no ref or pull request;
    returns the binding record it wrote."""
    with _acquire_lifecycle_lock(runtime, managed_repo):
        ctx = milestone_branch.Context(repo_root=managed_repo.root, runtime_root=runtime)
        return milestone_branch.acknowledge(ctx, work_item_id, disposition)


def _announce_orphaned_worker(worker_process: worker.WorkerProcess, root: Path, runtime_root: Path, *,
                              drained: bool = False, anchor: worker.WorkerProcess | None = None) -> None:
    """The Ctrl-C line: the Controller does not forward the interrupt to the
    worker, which runs in its own session and keeps running headless,
    holding the worktree. Ending it is the operator's decision.

    ``drained`` (release-runtime-observability CP6) is set once
    ``on_group_drain`` has flushed: pid P has already exited and only its
    process group's other members remain, so the line says that instead.
    Either way it is followed by the ``follow`` command."""
    pid, pgid = worker_process.pid, worker_process.pgid
    if drained:
        line = (f"workflow-controller: interrupted -- worker pid {pid} exited; its process group {pgid} "
                f"still has members running in their own session and holding the worktree {root}; wait "
                f"for them, or end the group (`kill -TERM -- -{pgid}`), then run `{_resume_command(root)}`")
    else:
        line = (f"workflow-controller: interrupted -- the worker (pid {pid}, process group {pgid}) keeps "
                f"running in its own session and holds the worktree {root}; wait for it, or end the group "
                f"(`kill -TERM -- -{pgid}`), then run `{_resume_command(root)}`")
    print(line, file=sys.stderr)
    if anchor is not None:
        # Worker-lifecycle-ownership CP5 (plan C, "Interruption"): nothing
        # was killed; the anchor keeps the worker's stdin and the lifecycle
        # lock, and `resume` re-attaches.
        print(f"workflow-controller: its stdin anchor (pid {anchor.pid}) keeps the session open and holds "
              f"the lifecycle lock; `{_resume_command(root)}` re-attaches to the job and supervises it to "
              f"its end", file=sys.stderr)
    print(f"workflow-controller: follow it: `{follow_command(runtime_root, root)}`", file=sys.stderr)


def _execute_step_locked(
    managed_repo: Any,
    *,
    work_item_id: str | None,
    identity: Any,
    runtime: Path,
    permission_mode: str,
    timeout: float | None,
    claude_bin: str | None,
    lifecycle_lock: lock.LifecycleLock,
    routing: routing.RoutingOptions,
    run_id: str | None = None,
    drain_detach_seconds: float | None = None,
    controller_settings: Mapping | None = None,
    auto_merge: bool = True,
    wait_seconds: int = 0,
    poll_seconds: int = 30,
    on_wait: Callable[[str, str], None] | None = None,
) -> JobRecord | Decision:
    """:func:`execute_step`'s nine steps, run under ``lifecycle_lock``.

    ``identity`` is an already-resolved
    ``controller.identity.ControllerIdentity`` (typically
    ``controller.identity.current()``, resolved once by the caller);
    ``runtime`` is the resolved runtime-root ``Path``. Both are accepted
    pre-resolved, per the plan's own declared signature, rather than
    re-derived here.

    Returns a :class:`~controller.decision.Decision` for the no-action
    class (``LEGACY_READY``, ``MILESTONE_COMPLETE``, and a close-out after
    a release wait, :func:`closed_out_decision`) -- nothing ran, no gate
    is open, nothing was declined, so no job record is written at all and
    the jobs directory is left unchanged. Every other reachable
    outcome returns a :data:`JobRecord` (a plain, JSON-serialisable
    ``dict`` -- exactly what was persisted): ``GATE_BLOCKED``,
    ``DECLINED`` or ``HANDOFF_PENDING`` for the no-launch class, or --
    once a worker actually launches -- one of ``FINISHED`` (step 8's
    verification rule held), ``FAILED`` (it did not, carrying
    ``reconciliation_evidence``), or ``INCOMPLETE`` (step 9: a legal
    effect of the action that is never its completion -- unreachable for
    every one of Generation 1's own eighteen automatic actions, see
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

    # Step 1b (trunk-branch-pr-release-orchestration CP8): the repository
    # preflight, under the lifecycle lock, after the state read and before
    # `decide`. With no binding record and no policy at HEAD it makes its
    # two probe calls and returns `Proceed()`, and nothing below changes
    # (I1). Otherwise it may have bound, switched or closed out, so the
    # state is read again; a gate is recorded GATE_BLOCKED like any other
    # (exit 10), and a refusal raises (exit 20).
    preflight_events: list[str] = []
    branch_ctx = milestone_branch.Context(repo_root=managed_repo.root, runtime_root=runtime,
                                          events=preflight_events, auto_merge=auto_merge,
                                          wait_seconds=wait_seconds, poll_seconds=poll_seconds)
    preflight = milestone_branch.waiting_preflight(branch_ctx, requested_work_item_id=work_item_id,
                                                   on_wait=on_wait)
    # Step 1c (workflow-controller-workflow-2-6-integration CP3, I3): the
    # installed Workflow release must still be the admitted one -- checked
    # here, after the preflight (close-out switches to the trunk, which may
    # run another release) and before a gate is recorded, the pre-state is
    # captured or `decide` runs, since each can resolve the feedback path
    # under the admitted release's contract. A mismatch or an unreadable
    # manifest refuses (exit 20), naming what the preflight did.
    _refuse_changed_release(managed_repo, preflight, preflight_events)
    if isinstance(preflight, milestone_branch.Gate):
        return _branch_gate_record(runtime, managed_repo, identity, target_state.read(managed_repo), preflight,
                                   run_id=run_id)
    if preflight.stop:
        return closed_out_decision(preflight.binding)
    binding = preflight.binding
    if binding is not None or preflight.action != "none":
        snapshot = target_state.read(managed_repo)
    if preflight.work_item_override is not None:
        work_item_id = preflight.work_item_override

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
    # `last_apply_job` (automatic-lifecycle-orchestration CP4B) is the job
    # history the "2.2" APPLYING_REVIEW_FEEDBACK relaunch bound reads -- the
    # same read `cli`'s `explain` makes, so both reach the same decision.
    # A protocol target never enters `evidence.decide`: the Workflow's own
    # `next-action` decides (orchestration-protocol-v1 C.1).
    if managed_repo.target_protocol is not None:
        decision, work_item, pre_state = _decide_protocol_current(
            managed_repo, runtime=runtime, snapshot=snapshot, work_item=work_item, work_item_id=work_item_id,
            pre_state=pre_state, base=preflight.base)
        is_bootstrap = work_item is target_state.NoWorkItemYet
        resolved_work_item_id = None if is_bootstrap else work_item.work_item_id
        governing_workflow_version = None if is_bootstrap else work_item.governing_workflow_version
    elif is_bootstrap:
        decision = decide_no_work_item(managed_repo, base=preflight.base)
    else:
        decision = evidence.decide(
            managed_repo, snapshot, work_item,
            last_apply_job=last_launched_apply_job_view(runtime, managed_repo.root, resolved_work_item_id),
        )

    if decision.gate is not None:
        return _no_launch_record(
            runtime, managed_repo, identity, resolved_work_item_id, decision.observed_phase, pre_state,
            decision, status=STATUS_GATE_BLOCKED, run_id=run_id,
        )
    if decision.declined:
        return _no_launch_record(
            runtime, managed_repo, identity, resolved_work_item_id, decision.observed_phase, pre_state,
            decision, status=STATUS_DECLINED, run_id=run_id,
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
            decision, status=STATUS_HANDOFF_PENDING, run_id=run_id,
        )

    # Defensive: by construction, every branch above that could return
    # already has. Reaching here with a non-automatic decision (or no
    # action) is an invariant violation, never ordinary control flow.
    if not decision.automatic or decision.action is None:
        raise HumanGateError(
            "execute_step reached the launch guard with a non-automatic decision",
            evidence={"observed_phase": decision.observed_phase},
        )

    # CP6: the worker's route, resolved before anything is recorded, so an
    # unroutable action leaves no record behind.
    route = _worker_route(decision, work_item, routing)

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
        # CP5: written under the lock, so any worker this record spawns
        # inherits its descriptor -- the lock then proves no process from
        # this job still holds it.
        "lifecycle_lock": {"path": str(lifecycle_lock.path)},
        # CP6: the resolved route -- role, model, effort, single-agent, and
        # where each field came from -- which the launch below applies.
        # Settings-and-telemetry CP2: `config_source` says which routing
        # config was in force (the settings file's section, --routing-config,
        # or none).
        "worker_route": {**route.to_record(), "config_source": routing.config_source},
        "created_at": now,
        "updated_at": now,
    }
    if run_id is not None:
        record["run_id"] = run_id
    if decision.protocol is not None:
        # Orchestration-protocol-v1 D.1: optional, additive; absent for a
        # legacy job, whose record stays byte-identical.
        record["protocol"] = _protocol_block(decision)
    if controller_settings is not None:
        # Settings-and-telemetry CP2: optional, additive (SCHEMA_VERSION
        # stays 1); readers tolerate its absence.
        record["controller_settings"] = dict(controller_settings)
    if binding is not None:
        # CP8 (trunk-branch-pr-release-orchestration): the governing
        # binding and the pre-step tip the post-step verification checks
        # against, here and at `resume`. Absent without a binding (I1).
        record["branch_binding"] = _branch_binding_block(binding, pre_state["target_head"])
    _run_job_started(run_id, job_id)
    record = _persist(runtime, job_id, record, event="planned",
                      details={"command": decision.action.command})

    # Worker-lifecycle-ownership CP3: the job's supervisor lock, taken
    # before the pre-spawn LAUNCHED write and held until `_launch_job` has
    # made its last write to this record (the terminal flush, or the exit-45
    # path after a drain detach); an exception releases it as it unwinds.
    with _supervisor_lock(runtime, job_id) as supervisor_lock_path:
        return _launch_job(
            managed_repo, runtime=runtime, run_id=run_id, job_id=job_id, record=record,
            decision=decision, route=route, binding=binding, pre_state=pre_state,
            resolved_work_item_id=resolved_work_item_id, governing_workflow_version=governing_workflow_version,
            permission_mode=permission_mode, timeout=timeout, claude_bin=claude_bin,
            lifecycle_lock=lifecycle_lock, supervisor_lock_path=supervisor_lock_path,
            drain_detach_seconds=drain_detach_seconds,
        )


#: How long a Controller retries a job's supervisor lock before it reports
#: another holder (plan E): the anchor's orphan-lifetime check holds it for
#: an instant, and must never read as an attached Controller.
SUPERVISOR_LOCK_RETRY_SECONDS = 1.0


def supervisor_lock_rel_path(job_id: str) -> str:
    """``jobs/<job_id>/supervisor.lock``, relative to the runtime root."""
    return f"jobs/{job_id}/supervisor.lock"


#: The supervisor-lock probe's three answers (plan E): a Controller holds
#: the job's supervisor lock, none does, or ``/proc/locks`` cannot tell.
SUPERVISOR_ATTACHED = "attached"
SUPERVISOR_UNATTACHED = "unattached"
SUPERVISOR_UNKNOWN = "unknown"


def _is_recorded_anchor(pid: int, record: Mapping) -> bool:
    """Whether ``pid`` is the record's own ``worker_anchor`` (its pid, with
    the start ticks still matching)."""
    anchor = record.get("worker_anchor")
    if not isinstance(anchor, Mapping) or anchor.get("pid") != pid:
        return False
    return worker.identity_alive(pid, anchor.get("start_ticks")) is True


def supervisor_probe(runtime_root: Path, record: Mapping) -> tuple[str, list[int]]:
    """Whether a Controller is attached to ``record``'s job (plan E): the
    read-only ``/proc/locks`` probe of ``jobs/<job_id>/supervisor.lock``
    (``lock.probe_file_lock``), as ``(answer, holder pids)``. A holder that
    is the record's own ``worker_anchor`` -- its momentary orphan-lifetime
    check -- is discounted, so it reads ``unattached``, never as an attached
    Controller. For presentation and for naming a holder only: whether
    ``resume``/``--abandon`` may act is decided by the retried acquisition
    (:func:`_supervisor_lock`), never by this probe."""
    job_id = record.get("job_id")
    if not isinstance(job_id, str) or not job_id or "/" in job_id or job_id in (".", ".."):
        return SUPERVISOR_UNKNOWN, []
    answer, holders = lock.probe_file_lock(Path(runtime_root) / supervisor_lock_rel_path(job_id))
    if answer == lock.UNKNOWN:
        return SUPERVISOR_UNKNOWN, []
    controllers = [pid for pid in holders if not _is_recorded_anchor(pid, record)]
    return (SUPERVISOR_ATTACHED, controllers) if controllers else (SUPERVISOR_UNATTACHED, [])


@contextlib.contextmanager
def _supervisor_lock(runtime_root: Path, job_id: str, *, record: Mapping | None = None,
                     nothing_done: str = "nothing was launched"):
    """Hold the job's supervisor lock (plan E) and yield its path. The
    descriptor is ``O_CLOEXEC`` and never passed on, so the lock dies with
    this Controller. Held by another process for longer than
    :data:`SUPERVISOR_LOCK_RETRY_SECONDS`, it is
    :class:`~controller.errors.LifecycleWorkerActiveError` (exit 45), naming
    the attached Controller's pid from :func:`supervisor_probe` (``record``,
    when given, lets it discount the job's own anchor)."""
    rel_path = supervisor_lock_rel_path(job_id)
    fd = runtime.open_lock_file(runtime_root, rel_path)
    try:
        deadline = time.monotonic() + SUPERVISOR_LOCK_RETRY_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    answer, holders = supervisor_probe(runtime_root, {**(record or {}), "job_id": job_id})
                    named = (f" (pid {', '.join(str(pid) for pid in holders)})" if holders
                             else " (its pid could not be read from /proc/locks)")
                    raise LifecycleWorkerActiveError(
                        f"job {job_id}'s supervisor lock {runtime_root / rel_path} is held by another "
                        f"Controller{named}, which is attached to the job and supervising it -- "
                        f"{nothing_done}",
                        evidence={"job_id": job_id, "supervisor_lock": str(runtime_root / rel_path),
                                  "supervisor": answer, "supervisor_pids": holders},
                    ) from None
                time.sleep(0.05)
        yield runtime_root / rel_path
    finally:
        os.close(fd)


def _launch_job(
    managed_repo: Any,
    *,
    runtime: Path,
    run_id: str | None,
    job_id: str,
    record: JobRecord,
    decision: Decision,
    route: routing.ResolvedRoute,
    binding: Mapping | None,
    pre_state: dict,
    resolved_work_item_id: str | None,
    governing_workflow_version: str | None,
    permission_mode: str,
    timeout: float | None,
    claude_bin: str | None,
    lifecycle_lock: lock.LifecycleLock,
    supervisor_lock_path: Path,
    drain_detach_seconds: float | None = None,
) -> JobRecord:
    """Steps 4 (from the LAUNCHED flush) to 9 of :func:`execute_step`,
    under the job's supervisor lock."""
    if decision.protocol is not None:
        # No release contract names a protocol job's phases: the Workflow's
        # own `reconcile` judges the outcome (CP5).
        expected_transition = {"from": phase_to_wire(decision.observed_phase), "to_any_of": []}
    else:
        expected_transition = _expected_transition(decision.observed_phase, governing_workflow_version, decision)
    record = {
        **record,
        "expected_transition": expected_transition,
        "status": STATUS_LAUNCHED,
        # Worker-lifecycle-ownership CP3: a new-shape job from the first
        # LAUNCHED write on (plan A, round 1's O1). The tag is the job id,
        # known before `Popen`, so a Controller lost between spawn and
        # `on_spawn` still leaves a record whose tagged processes can be
        # found.
        "ownership_tag": job_id,
        "worker_state": {"state": worker.STARTING, "since": _now()},
    }

    def not_started(reconciliation_evidence: dict, error: str) -> JobRecord:
        failed = _persist(runtime, job_id, {
            **record,
            "status": STATUS_FAILED,
            "transition_verified": False,
            "reconciliation_evidence": reconciliation_evidence,
            "updated_at": _now(),
        }, event="worker_not_started", details={"error": error})
        _run_job_ended(run_id, job_id, STATUS_FAILED)
        return failed

    def worker_not_started(exc: ControllerError) -> None:
        # CP5: a worker that never started is terminal at once -- the
        # Controller knows for certain no worker exists, so the record is
        # FAILED (`WorkerNotStarted`, which the apply relaunch bound never
        # counts) and needs no `resume`. The exit code is today's.
        not_started({
            "code": WORKER_NOT_STARTED_CODE, "error": type(exc).__name__,
            "message": exc.message, "evidence": exc.evidence,
        }, type(exc).__name__)

    # CP4 (release-runtime-observability): the worker writes its own
    # stdout/stderr into these files, created before the LAUNCHED flush so
    # the record names them before the worker produces anything.
    try:
        worker_streams = _create_worker_streams(runtime, job_id)
    except (WorkerLaunchError, RuntimeContainmentError) as exc:
        worker_not_started(exc)
        raise
    record = {**record, "worker_streams": worker_streams, "updated_at": _now()}
    record = _persist(runtime, job_id, record, event="launched")

    # Step 5: launch the worker and wait synchronously -- with no limit
    # unless the operator set one (CP5, "Time is not termination"). The
    # task is the selected command, plus its `task_addendum` when the
    # decision carries one (CP4B; recorded above as
    # `selected_action.task_addendum`). The worker inherits the lifecycle
    # lock's descriptor (CP5), and `on_spawn` flushes its process identity
    # into the still-LAUNCHED record -- a second LAUNCHED write, after
    # `Popen` and before the wait -- so a Controller that dies mid-job
    # leaves the group to wait on or end on disk.
    resolved_timeout = DEFAULT_WORKER_TIMEOUT if timeout is None else timeout
    spawned: dict[str, Any] = {}

    def on_spawn(worker_process: worker.WorkerProcess, *, anchor: worker.WorkerProcess | None = None,
                 ownership_tag: str | None = None) -> None:
        nonlocal record
        spawned["worker_process"] = worker_process
        spawned["anchor"] = anchor
        record = {**record, "worker_process": worker_process.to_dict(), "updated_at": _now()}
        # CP3 (worker-lifecycle-ownership): the stdin anchor, which holds
        # the lifecycle lock for the owned lifetime, is recorded in the same
        # flush (I3). The `None` defaults keep a `launch` double that passes
        # the process alone working.
        if anchor is not None:
            record["worker_anchor"] = anchor.to_dict()
        if ownership_tag is not None:
            record["ownership_tag"] = ownership_tag
        # The record write may raise (`launch` then ends the group); the
        # `worker_spawned` append after it never does -- it is best-effort.
        record = _persist(runtime, job_id, record, event="worker_spawned",
                          details={"pid": worker_process.pid, "pgid": worker_process.pgid})
        spawned["flushed"] = True
        spawned["spawned_at"] = record["updated_at"]

    def on_group_drain(pid: int, remaining_pids: list[int]) -> None:
        # CP4 (release-runtime-observability): the worker exited but its
        # process group has not emptied. Authoritative like the on_spawn
        # flush; the list is captured once, at drain start.
        nonlocal record
        record = {
            **record,
            "worker_group_drain": {"direct_child_exited_at": _now(), "remaining_pids": list(remaining_pids)},
            "updated_at": _now(),
        }
        record = _persist(runtime, job_id, record, event="worker_exited",
                          details={"pid": pid, "remaining_pids": list(remaining_pids)})
        spawned["drained"] = True
        _announce_group_drain(pid, remaining_pids)

    def on_state_change(state: str, details: Mapping) -> None:
        # CP4 (worker-lifecycle-ownership, plan D): every supervisor
        # transition -- and every change in what the worker waits on or
        # owns (at most one per scan) -- is flushed into the still-LAUNCHED
        # record, authoritative like the on_spawn flush, then appended as
        # the state's event. The record raising ends every owned process
        # (`launch`'s contract) and propagates.
        nonlocal record
        state_changed = (record.get("worker_state") or {}).get("state") != state
        record = _with_worker_state(record, state, details)
        record = _persist(runtime, job_id, record, event=_worker_state_event(state),
                          details=_worker_state_event_details(record, details, state_changed=state_changed))

    if decision.protocol is not None:
        # Orchestration-protocol-v1 D.2.2: the last step before the spawn --
        # the same currency check as before the PLANNED write, the stored
        # decision being the one checked (I5). A stale answer leaves a
        # terminal FAILED record, no worker, nothing for `resume`.
        try:
            currency = protocol_decision.check_currency(managed_repo, decision)
        except ControllerError as exc:
            worker_not_started(exc)
            raise
        if not currency.current:
            return not_started({
                "code": protocol_decision.DECISION_STALE_AT_LAUNCH, "gate": protocol_decision.DECISION_UNSTABLE,
                "message": f"the decision was no longer the Workflow's answer immediately before the spawn "
                           f"({currency.reason}); no worker was started, and the next run re-decides",
                "evidence": currency.evidence(),
            }, protocol_decision.DECISION_STALE_AT_LAUNCH)

    try:
        result = worker.launch(
            worker_task(decision.action),
            cwd=managed_repo.root,
            permission_mode=permission_mode,
            timeout=resolved_timeout,
            stdout_path=worker_streams["stdout_path"],
            stderr_path=worker_streams["stderr_path"],
            claude_bin=claude_bin,
            pass_fds=(lifecycle_lock.fd,),
            on_spawn=on_spawn,
            on_group_drain=on_group_drain,
            model=route.model,
            effort=route.effort,
            disallowed_tools=_worker_disallowed_tools(route, binding),
            ownership_tag=job_id,
            supervisor_lock_path=supervisor_lock_path,
            on_state_change=on_state_change,
            drain_detach_seconds=drain_detach_seconds,
        )
    except (UserOnlyCommandError, WorkerLaunchError) as exc:
        if "worker_process" not in spawned:
            worker_not_started(exc)
        raise
    except KeyboardInterrupt:
        # CP5: Ctrl-C ends only the Controller. After the worker_process
        # flush the record stays LAUNCHED with it, so `resume` applies the
        # liveness verdict later; the interrupt propagates unchanged.
        if spawned.get("flushed"):
            _announce_orphaned_worker(spawned["worker_process"], managed_repo.root, runtime,
                                      drained=spawned.get("drained", False), anchor=spawned.get("anchor"))
        raise

    if isinstance(result, worker.DrainDetached):
        # CP4 (worker-lifecycle-ownership, plan D): owned processes outlived
        # the worker past the drain bound, and nothing was ended. The record
        # stays LAUNCHED at DRAINING with `drain_detached_at`, and the
        # `worker_drain_detached` event follows it -- both while this
        # Controller still holds the job's supervisor lock, which the
        # raise releases as it unwinds `_supervisor_lock`. The anchor keeps
        # the lifecycle lock, so the job stays held until `resume`.
        _record_drain_detached(runtime, managed_repo.root, job_id, record, result,
                               worker_pid=spawned["worker_process"].pid)

    # Step 6: record the worker result. The streams are already on disk --
    # the worker wrote them itself.
    # CP4 (worker-lifecycle-ownership, plan D): COMPLETED is written only
    # after `launch` returned a result, which it does only at ENDED. A
    # `launch` double that never called `on_state_change` leaves the
    # pre-spawn STARTING, so the state is completed here (I2).
    if (record.get("worker_state") or {}).get("state") != worker.ENDED:
        record = _with_worker_state(record, worker.ENDED, {})
    # Settings-and-telemetry CP3: the session's telemetry, built through the
    # failure boundary before the one completion write, whose other fields
    # never read it (I6).
    completed_at = _now()
    block = _telemetry_block(record, worker_streams, completed_at=completed_at,
                             spawned_at=spawned.get("spawned_at"))
    record = {
        **record,
        "status": STATUS_COMPLETED,
        "worker": _worker_dict(result, stdout_path=worker_streams["stdout_path"],
                               stderr_path=worker_streams["stderr_path"]),
        "worker_outcome": result.outcome,
        "telemetry": block,
        "updated_at": completed_at,
    }
    record = _persist(runtime, job_id, record, event="completed",
                      details={"outcome": result.outcome, "exit_code": result.returncode,
                               "telemetry": telemetry.completed_summary(block)})

    if decision.protocol is not None:
        # Orchestration-protocol-v1 E.1: the Workflow's own `reconcile`
        # judges the outcome, after the release identity is re-checked.
        fields = _protocol_resolution(record, managed_repo=managed_repo, runtime_root=runtime,
                                      worker_outcome=result.outcome)
        if fields["status"] == STATUS_FINISHED:
            violation = _branch_invariant_violation(managed_repo.root, runtime, record)
            if violation is not None:
                fields = {**fields, "status": STATUS_FAILED, "transition_verified": False,
                          "reconciliation_evidence": violation}
        record = {**record, **fields, "updated_at": _now()}
        record = _persist(runtime, job_id, record, event=_final_event(record["status"]), details={
            "observed_phase_after": record["observed_phase_after"],
            "transition_verified": record.get("transition_verified", False),
        })
        _run_job_ended(run_id, job_id, record["status"])
        return record

    # Step 7 (CP6B): re-read the target repository's Workflow state fresh
    # -- never the `snapshot`/`work_item` captured before the worker ran
    # (see this module's own docstring, "CP6B's own scope note on step
    # 7's 'fresh managed_repo.inspect'"). `_observe_post_phase` (this
    # module's shared helper, also `resume`'s own CP7 read) is the
    # bootstrap-aware read: a row-7 (`NoWorkItemYet`) job has no
    # `WorkItemView` to re-select by id -- `resolved_work_item_id` is
    # `None` for it -- so its own post-read is a direct key-set comparison
    # against `pre_state["pre_work_item_keys"]`, never a second
    # `select_work_item(work_item_id=None)` call: that call's own "more
    # than one candidate" branch is `AmbiguousWorkItemError` (CP3), which
    # would crash `execute_step` on exactly the two-key case this
    # checkpoint's own declared row-7 cases require to fail *closed*
    # instead (`observed_phase_after=NO_PHASE`, never a member of any
    # row's `to_any_of`, so verification fails with the ordinary
    # `phase_not_in_to_any_of` reason). Row 7's own predicate
    # (`_predicate_row7_new_work_item_created`) performs the identical
    # key-set comparison independently, at verification time -- the two
    # checks are deliberately redundant (defence in depth), not merged.
    observed_phase_after = _observe_post_phase(managed_repo, resolved_work_item_id, pre_state)

    outcome_row = _expected_outcome_for(decision.observed_phase, governing_workflow_version, decision)

    # Step 9: INCOMPLETE takes precedence over step 8's own "otherwise" --
    # a phase that is a legal *effect* of the action but never a
    # *completion* of it. Empty for every one of Generation 1's eighteen rows
    # (see `_INCOMPLETE_EFFECT_PHASES`'s own docstring); checked first so
    # a future generation's row can populate it without this call site
    # changing.
    incomplete_effect_phases = _INCOMPLETE_EFFECT_PHASES.get(
        (decision.observed_phase, governing_workflow_version, decision.action.command.split()[0]),
        frozenset(),
    )

    # Step 8: the verification rule, stated in full and in one form.
    # CP3 (workflow-2-6-integration): verified under the release the record
    # carries, exactly as `resume` would verify it.
    verified, verification_evidence = _verify_transition(
        root=managed_repo.root, work_item_id=resolved_work_item_id, outcome=outcome_row,
        pre_state=pre_state, observed_phase_after=observed_phase_after,
        worker_outcome=result.outcome, release=record["target_workflow_version"],
    )

    # CP8 (trunk-branch-pr-release-orchestration): the post-step branch
    # verification, before the job can be FINISHED. A violation fails the
    # job whatever the transition did; the Controller never repairs it.
    branch_violation = _branch_invariant_violation(managed_repo.root, runtime, record)

    if branch_violation is not None:
        verified, final_status, verification_evidence = False, STATUS_FAILED, branch_violation
    elif verified:
        final_status = STATUS_FINISHED
    elif observed_phase_after in incomplete_effect_phases:
        final_status = STATUS_INCOMPLETE
    else:
        final_status = STATUS_FAILED

    record = {
        **record,
        "status": final_status,
        "observed_phase_after": phase_to_wire(observed_phase_after),
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
            "observed_phase": phase_to_wire(observed_phase_after),
            "worker_stdout": result.stdout,
        }
    record = _persist(runtime, job_id, record, event=_final_event(final_status), details={
        "observed_phase_after": record["observed_phase_after"], "transition_verified": verified,
    })
    _run_job_ended(run_id, job_id, final_status)
    return record
