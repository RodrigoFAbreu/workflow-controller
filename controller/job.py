"""Job execution, part 1: durable Controller-owned job records, pre-state
capture, persist-before-launch, worker launch and result recording
(capabilities 5, 7, 8, ``docs/ACTIVE_MILESTONE.md``; CP6,
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP6 -- Job execution, part
1: records, pre-state, persist-before-launch").

:func:`execute_step` is this module's own entry point, and it is **one**
function that spans all nine of the plan's job-execution steps -- **CP6
owns steps 1-6** (inspect and capture pre-state; decide; check for a
pending generation handoff; write the job record in two flushes,
``PLANNED`` then ``LAUNCHED``, before the worker is spawned; launch the
worker; record its result as ``COMPLETED``). CP6B (a later checkpoint)
*extends this same function* with steps 7-9 (a fresh post-state re-read,
``expected_transition`` verification, and ``FINISHED``/``INCOMPLETE``) --
it does not add a second function. This module's CP6 slice therefore ends
at a ``COMPLETED`` record; nothing here decides whether the action
actually succeeded.

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
"""

from __future__ import annotations

import datetime
import hashlib
import json
import secrets
import subprocess
from pathlib import Path
from typing import Any

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

#: The six automatic `(from_phase, governing_workflow_version, action)`
#: triples the combined CP4/CP4B decision engine can ever produce,
#: transcribed from the plan's own `ExpectedOutcome` table (CP6B section,
#: "CP6B -- Job execution, part 2") -- `to_any_of` only. CP6 needs no more
#: than this to populate `expected_transition` at the `LAUNCHED` flush;
#: the predicate/`writer_calls` columns that table also carries are CP6B's
#: own concern (post-state *verification*, not pre-launch recording).
_EXPECTED_TO_ANY_OF: dict[tuple[str, str | None, str], frozenset[str]] = {
    ("PLANNING", "2.1", "/milestone-plan"): frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
    ("PLANNING", "1", "/milestone-plan"): frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}),
    ("AWAITING_LOCAL_PLAN_REVIEW", "2.1", "/review-plan"): frozenset({
        "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW",
    }),
    ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.1", "/record-manual-plan-review"): frozenset({
        "AWAITING_PLAN_APPROVAL", "REVISING_PLAN",
    }),
    ("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review"): frozenset({
        "AWAITING_EXTERNAL_PLAN_REVIEW",
    }),
    ("REVISING_PLAN", "2.1", "/apply-plan-review"): frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
}


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
# expected_transition (step 4's LAUNCHED-only addition).
# ---------------------------------------------------------------------------


def _expected_transition(work_item: Any, decision: Decision) -> dict:
    """``{"from": <phase>, "to_any_of": [...]}`` for the automatic
    ``decision`` about to be launched, looked up from the closed six-row
    table transcribed in :data:`_EXPECTED_TO_ANY_OF`. The combined
    CP4/CP4B decision engine can only ever produce an automatic
    ``Decision`` for one of these six triples, so a miss here is an
    invariant violation, never an ordinary control-flow path (the same
    shape as ``cli._reexec``'s own ``AssertionError`` after
    ``os.execve``)."""
    command_token = decision.action.command.split()[0]
    key = (work_item.phase, work_item.governing_workflow_version, command_token)
    to_any_of = _EXPECTED_TO_ANY_OF.get(key)
    if to_any_of is None:
        raise AssertionError(
            f"decide() produced an automatic action with no known expected transition: "
            f"phase={work_item.phase!r} governing_workflow_version="
            f"{work_item.governing_workflow_version!r} command={decision.action.command!r}"
        )
    return {"from": work_item.phase, "to_any_of": sorted(to_any_of)}


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
# execute_step -- CP6 owns steps 1-6.
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
    ``dict`` -- exactly what was persisted), ending, for CP6's own slice
    of this function, at one of ``GATE_BLOCKED``, ``DECLINED``,
    ``HANDOFF_PENDING`` or ``COMPLETED``.
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
    return record
