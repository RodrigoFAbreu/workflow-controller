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
from typing import Any, Callable

from controller import evidence, runtime, target_state, worker
from controller.decision import Decision
from controller.errors import HumanGateError

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
#: schema and never captured, round 8's B3). Only three of these sixteen
#: are ever a predicate *input* (`bundle_id`, `bundle_manifest_readable`,
#: `bundle_generated_digest` -- CP6B's own concern); the other thirteen are
#: report data for `inspect`/`explain`/`status` and for a later
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
    three-of-sixteen note), ``None`` when the file does not exist."""
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
    gone and this record is the entire input."""
    root = managed_repo.root
    work_item_id = work_item.work_item_id
    phase = work_item.phase

    bundle_dir = evidence.resolve_bundle_dir(root, work_item_id, phase=phase)
    manifest = evidence.read_manifest_fields(root, bundle_dir)
    rejected_present, _detail = evidence.rejected_marker_detail(root, work_item_id)
    target_head = _current_head(root)
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
    """One row of the plan's own `ExpectedOutcome` table."""

    from_phase: str
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


#: The six rows, transcribed verbatim from the plan's own table (CP6B,
#: "An `ExpectedOutcome` is data, not prose").
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
)

_EXPECTED_OUTCOMES_BY_KEY: dict[tuple[str, str | None, str], ExpectedOutcome] = {
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
      on a predicate-bearing row must be ``WRITER_KIND_COMPLETION``.
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


def _expected_outcome_for(work_item: Any, decision: Decision) -> ExpectedOutcome:
    """The single :data:`EXPECTED_OUTCOMES` row an automatic ``decision``
    about to be launched corresponds to. The combined CP4/CP4B decision
    engine can only ever produce an automatic ``Decision`` for one of
    these six triples, so a miss here is an invariant violation, never an
    ordinary control-flow path (the same shape as ``cli._reexec``'s own
    ``AssertionError`` after ``os.execve``)."""
    command_token = decision.action.command.split()[0]
    key = (work_item.phase, work_item.governing_workflow_version, command_token)
    outcome = _EXPECTED_OUTCOMES_BY_KEY.get(key)
    if outcome is None:
        raise AssertionError(
            f"decide() produced an automatic action with no known expected transition: "
            f"phase={work_item.phase!r} governing_workflow_version="
            f"{work_item.governing_workflow_version!r} command={decision.action.command!r}"
        )
    return outcome


def _expected_transition(work_item: Any, decision: Decision) -> dict:
    """``{"from": <phase>, "to_any_of": [...]}`` for the automatic
    ``decision`` about to be launched (step 4's own ``LAUNCHED``-only
    addition)."""
    outcome = _expected_outcome_for(work_item, decision)
    return {"from": work_item.phase, "to_any_of": sorted(outcome.to_any_of)}


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
#: not one of :data:`EXPECTED_OUTCOMES`' own six rows). Kept as a per-row
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


def _identity_block(managed_repo: Any, ident: Any, work_item: Any, job_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "controller_generation": ident.generation,
        "controller_source_commit": ident.source_commit,
        "controller_source_tree_digest": ident.tree_digest,
        "target_repo": str(managed_repo.root),
        "target_workflow_version": managed_repo.workflow_version,
        "work_item_id": work_item.work_item_id,
        "observed_phase_before": work_item.phase,
    }


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
    runtime_root: Path, managed_repo: Any, ident: Any, work_item: Any, pre_state: dict,
    decision: Decision, *, status: str,
) -> JobRecord:
    """The single-flush record for every outcome that never reaches a
    worker launch: ``GATE_BLOCKED``, ``DECLINED``, ``HANDOFF_PENDING``."""
    job_id = _new_job_id()
    now = _now()
    record: JobRecord = {
        **_identity_block(managed_repo, ident, work_item, job_id),
        "pre_state": pre_state,
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
    every one of Generation 1's own six automatic actions, see
    :data:`_INCOMPLETE_EFFECT_PHASES`). ``COMPLETED`` is never this
    function's own return value; it is an intermediate, durable flush
    step 6 always makes before steps 7-9 run.
    """
    # Step 1: inspect the repository and read Workflow state (already done
    # by the caller producing `managed_repo`); read Workflow state and
    # capture the pre-state.
    snapshot = target_state.read(managed_repo)
    work_item = target_state.select_work_item(snapshot, work_item_id=work_item_id)
    pre_state = _capture_pre_state(managed_repo, snapshot, work_item)

    # Step 2: decide. The launch guard is positive and total: a worker is
    # launched only when `decision.automatic` is True -- never inferred
    # from the *absence* of a gate or a decline, which is exactly the hole
    # a negative guard reopens every time a new non-launching outcome is
    # added (round 11's B1).
    decision = evidence.decide(managed_repo, snapshot, work_item)

    if decision.gate is not None:
        return _no_launch_record(
            runtime, managed_repo, identity, work_item, pre_state, decision,
            status=STATUS_GATE_BLOCKED,
        )
    if decision.declined:
        return _no_launch_record(
            runtime, managed_repo, identity, work_item, pre_state, decision,
            status=STATUS_DECLINED,
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
            runtime, managed_repo, identity, work_item, pre_state, decision,
            status=STATUS_HANDOFF_PENDING,
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
        **_identity_block(managed_repo, identity, work_item, job_id),
        "pre_state": pre_state,
        "selected_action": _selected_action_dict(decision),
        "status": STATUS_PLANNED,
        "human_gate_pending": None,
        "handoff_pending": False,
        "created_at": now,
        "updated_at": now,
    }
    _persist(runtime, job_id, record)

    expected_transition = _expected_transition(work_item, decision)
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
    # 7's 'fresh managed_repo.inspect'").
    post_snapshot = target_state.read(managed_repo)
    post_work_item = target_state.select_work_item(post_snapshot, work_item_id=work_item.work_item_id)
    observed_phase_after = post_work_item.phase

    outcome_row = _expected_outcome_for(work_item, decision)

    # Step 9: INCOMPLETE takes precedence over step 8's own "otherwise" --
    # a phase that is a legal *effect* of the action but never a
    # *completion* of it. Empty for every one of Generation 1's six rows
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
