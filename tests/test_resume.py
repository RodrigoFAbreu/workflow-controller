"""Tests for durable resume (CP7, ``controller.job.resume``).

Covers exactly what CP7 owns: the named four-case validation pass
(:func:`controller.job.validate_record`), the closed reconciliation table
over ``PLANNED``/``LAUNCHED``/``COMPLETED`` records, the terminal-record
carve-out (surfaced marked, never raised, never rewritten), and the
"never relaunch" property -- ``resume`` contains no call to
``controller.worker.launch`` at all.

Most fixtures here build a raw job-record ``dict`` directly and write it to
``<runtime>/jobs/<job_id>.json`` -- ``resume`` reconciles *history*, so its
own tests construct that history directly rather than always driving it
through a real ``execute_step`` + fake-worker run (``tests/test_job.py``'s
and ``tests/test_job_validation.py``'s own technique). One end-to-end test
at the bottom does drive a real interrupted worker through ``execute_step``
and then ``resume``, per the plan's own declared "end-to-end interruption
test".
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, job, lock, worker  # noqa: E402
from controller import runtime as runtime_module  # noqa: E402
from controller.errors import (  # noqa: E402
    GitDirectoryUnresolvableError,
    JobAbandonRefusedError,
    LifecycleWorkerActiveError,
    LifecycleWorkerUnverifiableError,
    PendingJobReconciliationError,
    StaleJobRecordError,
    UnreconcilableJobError,
)
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT  # noqa: E402
from tests import fake_claude, fixtures, process_fixtures  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"

FAKE_IDENTITY = ControllerIdentity(
    generation=7,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_COMMIT,
    source_commit="a" * 40,
    tree_digest="b" * 64,
    generation_source="head",
    pinned_at="2024-01-01T00:00:00Z",
)

_OMIT = object()


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _build_target(
    tmp_root: Path, *, phase: str, governing_workflow_version: str | None = "2.1",
    work_item_id: str = "wi-1", **overrides,
):
    root = tmp_root / "target"
    fixtures.build_target_git_repo(root)
    (root / "README.md").write_text("target fixture\n")
    fixtures.commit_all(root, "initial")
    entry = {
        "work_item_type": "product", "work_item_kind": "product", "work_item_id": work_item_id,
        "governing_workflow_version": governing_workflow_version, "phase": phase,
        "plan_revision": 1, "implementation_revision": None, "state_revision": 1,
        "checkpoints": {}, "current_bundle_id": None, "last_completed_checkpoint_id": None,
        "base_commit": fixtures.current_head(root), "parent_work_item_id": None,
    }
    entry.update(overrides)
    state = {"schema_version": 1, "active_work_item_id": work_item_id, "work_items": {work_item_id: entry}}
    fixtures.write_workflow_state(root, state)
    return fixtures.build_target_managed_repository(root)


def _set_target_phase(root: Path, work_item_id: str, phase: str) -> None:
    import json as _json
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = _json.loads(state_path.read_text())
    state["work_items"][work_item_id]["phase"] = phase
    fixtures.write_workflow_state(root, state)


def _bump_target_revisions(root: Path, work_item_id: str) -> None:
    import json as _json
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = _json.loads(state_path.read_text())
    entry = state["work_items"][work_item_id]
    entry["plan_revision"] = (entry.get("plan_revision") or 0) + 1
    entry["state_revision"] = (entry.get("state_revision") or 0) + 1
    fixtures.write_workflow_state(root, state)


def _add_work_item(root: Path, work_item_id: str, *, phase: str) -> None:
    """Adds a brand-new ``work_items`` entry alongside whatever
    :func:`_build_target` already wrote -- row 7's own bootstrap action,
    materialised directly rather than through a fake-worker env override,
    since these fixtures simulate an already-completed post-state rather
    than driving a real worker."""
    import json as _json
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = _json.loads(state_path.read_text())
    state["work_items"][work_item_id] = {
        "work_item_type": "product", "work_item_kind": "product", "work_item_id": work_item_id,
        "governing_workflow_version": "2.1", "phase": phase, "plan_revision": 1,
        "implementation_revision": None, "state_revision": 1, "checkpoints": {},
        "current_bundle_id": None, "last_completed_checkpoint_id": None,
        "base_commit": None, "parent_work_item_id": None,
    }
    fixtures.write_workflow_state(root, state)


def _pre_state(*, phase: str, governing_workflow_version: str | None, target_head=None, **overrides):
    base = {
        "phase": phase, "governing_workflow_version": governing_workflow_version,
        "target_head": target_head, "state_revision": 1, "plan_revision": 1,
        "implementation_revision": None, "last_completed_checkpoint_id": None, "checkpoints": {},
        "bundle_id": None, "bundle_manifest_readable": False, "bundle_manifest_bundle_id": None,
        "bundle_manifest_generation_head": None,
        "bundle_generated_digest": None, "rejected_marker_present": False, "child_work_item_ids": [],
        "functional_review_consumed_blob": None, "functional_checklist_evidence": None,
    }
    base.update(overrides)
    return base


def _record(
    *, job_id: str, target_repo: str, status: str, phase: str,
    governing_workflow_version: str | None = "2.1", command: str = "/milestone-plan",
    work_item_id: str | None = "wi-1", schema_version=job.SCHEMA_VERSION,
    controller_generation=FAKE_IDENTITY.generation, declined: bool = False,
    pre_state_overrides: dict | None = None, worker_outcome=_OMIT,
    expected_transition=_OMIT,
) -> dict:
    pre_state = _pre_state(
        phase=phase, governing_workflow_version=governing_workflow_version,
        **(pre_state_overrides or {}),
    )
    record = {
        "schema_version": schema_version,
        "job_id": job_id,
        "controller_generation": controller_generation,
        "controller_source_commit": "a" * 40,
        "controller_source_tree_digest": "b" * 64,
        "target_repo": target_repo,
        "target_workflow_version": "2.3.1",
        "work_item_id": work_item_id,
        "observed_phase_before": phase,
        "pre_state": pre_state,
        "selected_action": {
            "kind": "slash_command", "command": command, "automatic": True,
            "declined": declined, "reason": "ordinary case", "evidence": [],
        },
        "status": status,
        "human_gate_pending": None,
        "handoff_pending": False,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    if worker_outcome is not _OMIT:
        record["worker_outcome"] = worker_outcome
    if expected_transition is not _OMIT:
        record["expected_transition"] = expected_transition
    return record


def _write_record(runtime_root: Path, record: dict) -> None:
    runtime_module.write_json(runtime_root, f"jobs/{record['job_id']}.json", record)


def _read_record(runtime_root: Path, job_id: str) -> dict:
    return runtime_module.read_json(runtime_root / "jobs" / f"{job_id}.json")


class _ResumeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)


# ---------------------------------------------------------------------------
# Validation pass -- case 1 (uninterpretable record).
# ---------------------------------------------------------------------------


class Case1UninterpretableTest(_ResumeTestCase):
    def test_unknown_schema_version_on_non_terminal_record_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_PLANNED,
            phase="PLANNING", schema_version=99,
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(ctx.exception.evidence["job_id"], "j1")

    def test_unknown_schema_version_on_terminal_record_is_surfaced_unreadable(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_DECLINED,
            phase="PLANNING", schema_version=99, declined=True,
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), 1)
        marked = results[0]["resume_marked"]
        self.assertEqual(marked["outcome"], "unreadable")
        self.assertEqual(marked["case"], 1)
        # The record on disk must be left completely untouched.
        on_disk = _read_record(self.runtime_root, "j1")
        self.assertNotIn("resume_marked", on_disk)
        self.assertEqual(on_disk["status"], job.STATUS_DECLINED)

    def test_newer_controller_generation_on_completed_preempts_the_table(self) -> None:
        """Even when the post-state plainly satisfies step 8's rule, a
        record from a newer generation raises rather than being written
        FINISHED -- case 1 preempts the reconciliation table entirely."""
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_COMPLETED,
            phase="PLANNING", command="/milestone-plan", controller_generation=99,
            worker_outcome="SUCCESS",
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_newer_controller_generation_on_planned_raises_rather_than_interrupted(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_PLANNED,
            phase="PLANNING", controller_generation=99,
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_terminal_record_failing_case1_never_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_FINISHED,
            phase="PLANNING", controller_generation=99,
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["resume_marked"]["outcome"], "unreadable")

    def test_unrecognised_status_with_newer_generation_raises_fail_closed(self) -> None:
        """Terminality of an unrecognised status cannot be determined; the
        carve-out's premise does not hold for a newer generation's record,
        so it raises rather than being surfaced unreadable."""
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status="SOME_FUTURE_STATUS",
            phase="PLANNING", controller_generation=99,
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_unknown_schema_version_with_absent_status_raises_fail_closed(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_PLANNED,
            phase="PLANNING", schema_version=None,
        )
        record.pop("status")
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)


# ---------------------------------------------------------------------------
# Validation pass -- case 2 (unresolvable subject).
# ---------------------------------------------------------------------------


class Case2UnresolvableSubjectTest(_ResumeTestCase):
    def test_launched_record_naming_a_deleted_target_raises_and_aborts(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING",
        )
        _write_record(self.runtime_root, record)
        shutil.rmtree(managed_repo.root)
        with self.assertRaises(StaleJobRecordError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(ctx.exception.evidence["job_id"], "j1")

    def test_work_item_id_absent_from_target_state_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", work_item_id="wi-1")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", work_item_id="wi-ghost",
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_terminal_record_with_deleted_target_is_surfaced_unreadable_never_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_FAILED,
            phase="PLANNING",
        )
        _write_record(self.runtime_root, record)
        shutil.rmtree(managed_repo.root)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["resume_marked"]["outcome"], "unreadable")
        self.assertEqual(results[0]["resume_marked"]["case"], 2)

    def test_null_work_item_id_row7_record_is_valid_case2_and_reaches_the_table(self) -> None:
        """Revision 64, round 63's `B2`: a `null` `work_item_id` is not an
        unresolvable subject -- it is row 7's own declared literal, since
        frozen Workflow alone derives a bootstrap work item's id. A record
        carrying it, whose `expected_transition.from` agrees (the NO_PHASE
        wire literal), must reach the reconciliation table rather than
        abort the whole `resume` call with `StaleJobRecordError`."""
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", work_item_id="wi-existing")
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="__NO_PHASE__", governing_workflow_version=None, command="/milestone-plan",
            work_item_id=None,
            expected_transition={"from": "__NO_PHASE__", "to_any_of": ["AWAITING_LOCAL_PLAN_REVIEW"]},
            pre_state_overrides={"target_head": head, "pre_work_item_keys": ["wi-existing"]},
        )
        _write_record(self.runtime_root, record)
        # Never StaleJobRecordError from case 2 -- it reaches reconciliation,
        # which in this fixture (zero new `work_items` keys, phase and head
        # both unchanged) is `INTERRUPTED`, never an abort.
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)

    def test_null_work_item_id_with_real_phase_expected_transition_raises(self) -> None:
        """The other half of the stated pair (revision 64, round 63's
        `B2`): `work_item_id` is null but `expected_transition.from` names
        a real phase, not the NO_PHASE wire literal -- the two disagree,
        and the record is refused, naming both fields, rather than
        guessed at."""
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", governing_workflow_version="2.1", command="/milestone-plan",
            work_item_id=None,
            expected_transition={"from": "PLANNING", "to_any_of": ["AWAITING_LOCAL_PLAN_REVIEW"]},
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertIsNone(ctx.exception.evidence["work_item_id"])
        self.assertEqual(ctx.exception.evidence["expected_transition_from"], "PLANNING")


# ---------------------------------------------------------------------------
# Validation pass -- case 3 (worker_outcome disagrees with the owning step).
# ---------------------------------------------------------------------------


class Case3WorkerOutcomeDisagreementTest(_ResumeTestCase):
    def test_launched_with_recognised_outcome_success_raises_never_finished(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", worker_outcome="SUCCESS",
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_launched_with_failure_outcome_and_phase_moved_into_to_any_of_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", worker_outcome="FAILURE",
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_launched_with_unrecognised_outcome_partial_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", worker_outcome="PARTIAL",
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_completed_with_absent_worker_outcome_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_COMPLETED,
            phase="PLANNING",
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_completed_with_unrecognised_worker_outcome_raises(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_COMPLETED,
            phase="PLANNING", worker_outcome="PARTIAL",
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)


# ---------------------------------------------------------------------------
# Validation pass -- case 4 (derived-field disagreement).
# ---------------------------------------------------------------------------


class Case4DerivedFieldDisagreementTest(_ResumeTestCase):
    def test_non_terminal_record_with_declined_true_raises_and_aborts(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_PLANNED,
            phase="PLANNING", declined=True,
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def test_terminal_declined_status_with_declined_false_is_surfaced_malformed(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_DECLINED,
            phase="PLANNING", declined=False,
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        marked = results[0]["resume_marked"]
        self.assertEqual(marked["outcome"], "malformed")
        self.assertEqual(marked["case"], 4)
        self.assertEqual(marked["reason"], "declined_status_without_flag")

    def test_unrecognised_status_with_declined_true_raises_from_unknown_status_row_not_case4(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status="PARTIALLY_DONE",
            phase="PLANNING", declined=True,
        )
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        # Reached via the closed table's own last row, not case 4 -- proven
        # by validate_record itself never flagging this record as invalid.
        validity = job.validate_record(record, managed_repo=managed_repo, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid)
        self.assertIn("PARTIALLY_DONE", ctx.exception.message)


# ---------------------------------------------------------------------------
# The reconciliation table's own unknown-status row.
# ---------------------------------------------------------------------------


class BaseVersionRecordShapeTest(_ResumeTestCase):
    """Release-runtime-observability CP2: `controller_runtime` is additive.
    `_record` builds the base version's shape, which never carried it."""

    def test_a_record_without_controller_runtime_validates_and_resumes(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_PLANNED,
            phase="PLANNING",
        )
        self.assertNotIn("controller_runtime", record)
        validity = job.validate_record(record, managed_repo=managed_repo, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid, validity)
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual([r["job_id"] for r in results], ["j1"])
        self.assertNotIn("controller_runtime", _read_record(self.runtime_root, "j1"))


class UnknownStatusRowTest(_ResumeTestCase):
    def test_first_flush_record_missing_status_raises_from_unknown_status_row(self) -> None:
        """A well-formed first-flush record whose only defect is an absent
        `status`: every validation-pass case returns VALID, so this is a
        reader-side property of the dispatch itself, never surfaced as
        terminal and never reconciled as PLANNED."""
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_PLANNED,
            phase="PLANNING",
        )
        record.pop("status")
        validity = job.validate_record(record, managed_repo=managed_repo, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid, validity)
        _write_record(self.runtime_root, record)
        with self.assertRaises(StaleJobRecordError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)


# ---------------------------------------------------------------------------
# Terminal records: reported, never reconciled, never rewritten.
# ---------------------------------------------------------------------------


class TerminalRecordsTest(_ResumeTestCase):
    def test_every_terminal_status_is_returned_verbatim_and_left_on_disk(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        for i, status in enumerate(sorted(job.TERMINAL_STATUSES)):
            record = _record(
                job_id=f"j{i}", target_repo=str(managed_repo.root), status=status,
                phase="PLANNING", declined=(status == job.STATUS_DECLINED),
                worker_outcome="SUCCESS" if status in (job.STATUS_FINISHED, job.STATUS_FAILED) else _OMIT,
            )
            _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), len(job.TERMINAL_STATUSES))
        for result in results:
            self.assertNotIn("resume_marked", result)
            self.assertNotIn("reconciled_at", result)


# ---------------------------------------------------------------------------
# Row 1: PLANNED -> INTERRUPTED, unconditionally.
# ---------------------------------------------------------------------------


class ReconcilePlannedTest(_ResumeTestCase):
    def test_planned_reconciles_to_interrupted_and_is_persisted(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_PLANNED,
            phase="PLANNING",
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)
        self.assertIn("reconciled_at", results[0])
        on_disk = _read_record(self.runtime_root, "j1")
        self.assertEqual(on_disk["status"], job.STATUS_INTERRUPTED)


# ---------------------------------------------------------------------------
# Rows 2-4: LAUNCHED.
# ---------------------------------------------------------------------------


class ReconcileLaunchedTest(_ResumeTestCase):
    def test_already_succeeded_reconciles_to_finished_never_relaunches(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        fixtures.write_plan_manifest(managed_repo.root, "wi-1", 1)
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        with _NeverLaunches():
            results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])
        self.assertEqual(results[0]["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")

    def test_2_2_planning_launched_record_reconciles_to_finished_never_relaunches(self) -> None:
        """CP2's own resume-path proof (`workflow-controller-protocol-2-2-
        compatibility`): the second, independent call site the CP1 gap
        could have hit -- `resume`'s own `_expected_outcome_for_record`
        lookup, via a job record's persisted `(phase,
        governing_workflow_version, command)` triple -- reconciles a
        `"2.2"`-governed `LAUNCHED` `PLANNING` record correctly rather
        than raising."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.2",
        )
        fixtures.write_plan_manifest(managed_repo.root, "wi-1", 1)
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", governing_workflow_version="2.2",
            pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        with _NeverLaunches():
            results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])
        self.assertEqual(results[0]["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")

    def test_nothing_happened_reconciles_to_interrupted(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)

    def test_phase_unchanged_but_head_moved_is_unreconcilable(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        (managed_repo.root / "unrelated.txt").write_text("something the worker cannot account for\n")
        fixtures.commit_all(managed_repo.root, "unrelated commit")
        with self.assertRaises(UnreconcilableJobError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(ctx.exception.evidence["pre_phase"], "PLANNING")

    def test_phase_moved_outside_to_any_of_is_unreconcilable(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        _set_target_phase(managed_repo.root, "wi-1", "SELF_REVIEWING_IMPLEMENTATION")
        with self.assertRaises(UnreconcilableJobError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(ctx.exception.evidence["observed_phase_after"], "SELF_REVIEWING_IMPLEMENTATION")

    def test_own_row_no_op_worker_negative_row5_predicate_not_consulted_weakly(self) -> None:
        """Row 5's own no-op-worker instantiation: `plan_revision`/
        `state_revision` are deliberately advanced -- proving the *stronger*
        `bundle_generated_digest` predicate is what is consulted, not a
        weaker "did anything change" test."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
        )
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
            command="/apply-plan-review",
            pre_state_overrides={"target_head": head, "bundle_generated_digest": None},
        )
        _write_record(self.runtime_root, record)
        _bump_target_revisions(managed_repo.root, "wi-1")
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)

    def test_own_row_no_op_worker_positive_row5_bundle_regenerated_finishes(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
        )
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
            command="/apply-plan-review",
            pre_state_overrides={"target_head": head, "bundle_generated_digest": None},
        )
        _write_record(self.runtime_root, record)
        changed_files = managed_repo.root / ".ai-review" / "wi-1" / "current" / "CHANGED_FILES.txt"
        changed_files.parent.mkdir(parents=True, exist_ok=True)
        changed_files.write_text("generated: round 2\nREADME.md\n")
        fixtures.write_plan_manifest(managed_repo.root, "wi-1", 1)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])

    def test_a_subsequent_step_runs_normally_after_interrupted_reconciliation(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        fixtures.copy_real_commands_dir(managed_repo.root / ".claude" / "commands")
        result = job.execute_step(
            managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
            claude_bin=str(FAKE_CLAUDE), timeout=10,
        )
        self.assertIsInstance(result, dict)
        self.assertNotEqual(result["job_id"], "j1")


class RowPredicateResumePathTest(_ResumeTestCase):
    """The predicate clause's own trigger on the resume path is keyed on
    the row's own ``_row_branch``, byte-identical to step 8's own rule
    (CP6B's revision-65/66 repair) -- never on raw phase equality alone,
    which leaves row 7's predicate unreachable (its ``from_phase`` is
    ``NO_PHASE``, never equal to a real observed phase) and, if
    implemented the other way (evaluating a predicate unconditionally),
    incorrectly re-consults row 3's own ``BLOCK``-specific predicate on
    its ``APPROVE``/``REVISE`` observations (revision 64's ``B1``,
    revision 66's ``B1``/``I2``)."""

    def test_row7_zero_new_keys_with_a_coincidentally_matching_pre_existing_phase_does_not_finish(self) -> None:
        """Revision 64's `B1`: a LAUNCHED row-7 record whose target's
        post-phase is `AWAITING_LOCAL_PLAN_REVIEW` but whose `work_items`
        gained **zero** new keys must not reconcile to `FINISHED` -- it is
        the `LAUNCHED` phase-unchanged-equivalent branch, `INTERRUPTED`.
        The one pre-existing work item's own phase coincidentally matches
        row 7's `to_any_of`, exactly what a bug reading `observed_phase_after`
        off `select_work_item(work_item_id=None)` (rather than the
        key-set diff `_observe_post_phase` performs) would report and
        verify against."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", work_item_id="wi-existing",
        )
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="__NO_PHASE__", governing_workflow_version=None, command="/milestone-plan",
            work_item_id=None,
            expected_transition={"from": "__NO_PHASE__", "to_any_of": ["AWAITING_LOCAL_PLAN_REVIEW"]},
            pre_state_overrides={"target_head": head, "pre_work_item_keys": ["wi-existing"]},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)

    def test_row7_exactly_one_new_key_at_the_expected_phase_finishes(self) -> None:
        """Sanity companion to the case above: the true positive still
        verifies -- the key-set diff finds exactly one new key at
        `AWAITING_LOCAL_PLAN_REVIEW`, row 7's own predicate (also
        consulted, redundantly -- defence in depth) agrees, and the
        record reconciles to `FINISHED`, never relaunched."""
        managed_repo = _build_target(
            self.tmp_root, phase="MILESTONE_COMPLETE", work_item_id="wi-existing",
        )
        head = fixtures.current_head(managed_repo.root)
        _add_work_item(managed_repo.root, "wi-new", phase="AWAITING_LOCAL_PLAN_REVIEW")
        fixtures.write_plan_manifest(managed_repo.root, "wi-new", 1)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="__NO_PHASE__", governing_workflow_version=None, command="/milestone-plan",
            work_item_id=None,
            expected_transition={"from": "__NO_PHASE__", "to_any_of": ["AWAITING_LOCAL_PLAN_REVIEW"]},
            pre_state_overrides={"target_head": head, "pre_work_item_keys": ["wi-existing"]},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])

    def test_row3_approve_observation_reconciles_to_finished_without_consulting_predicate(self) -> None:
        """Revision 66's `B1`/`I2`: row 3's predicate (a current-round
        `REVIEW_FEEDBACK.md` declaring `Status: BLOCK`) must not be
        consulted on the `APPROVE` observation -- no such file exists on
        disk at all, and the record still reconciles to `FINISHED`."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", work_item_id="wi-1",
        )
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.1",
            command="/review-plan", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])

    def test_row3_revise_observation_reconciles_to_finished_without_consulting_predicate(self) -> None:
        """The `REVISING_PLAN` (`REVISE`) half of the same pair."""
        managed_repo = _build_target(
            self.tmp_root, phase="REVISING_PLAN", work_item_id="wi-1",
        )
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.1",
            command="/review-plan", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])


class _NeverLaunches:
    """Structural proof that `resume` never spawns a worker: patches
    `controller.worker.launch` to raise if it is ever called while
    installed."""

    def __enter__(self) -> "_NeverLaunches":
        self._original = job.worker.launch

        def _forbidden(*_args, **_kwargs):
            raise AssertionError("resume() must never launch a worker")

        job.worker.launch = _forbidden
        return self

    def __exit__(self, *exc) -> None:
        job.worker.launch = self._original


# ---------------------------------------------------------------------------
# Rows 2/5: COMPLETED.
# ---------------------------------------------------------------------------


class ReconcileCompletedTest(_ResumeTestCase):
    def test_completed_with_failing_rule_reconciles_to_failed_not_finished(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_COMPLETED,
            phase="PLANNING", worker_outcome="SUCCESS",
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertFalse(results[0]["transition_verified"])
        ev = results[0]["reconciliation_evidence"]
        self.assertEqual(ev["code"], "TransitionNotObservedError")
        self.assertEqual(ev["reason"], "phase_not_in_to_any_of")

    def test_completed_with_failure_outcome_and_matching_poststate_reconciles_to_failed(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_COMPLETED,
            phase="PLANNING", worker_outcome="FAILURE",
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        ev = results[0]["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "worker_outcome")
        self.assertEqual(ev["worker_outcome"], "FAILURE")
        self.assertEqual(ev["observed_phase"], "AWAITING_LOCAL_PLAN_REVIEW")

    def test_completed_with_ambiguous_outcome_and_matching_poststate_reconciles_to_failed(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_COMPLETED,
            phase="PLANNING", worker_outcome="AMBIGUOUS",
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        ev = results[0]["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "worker_outcome")
        self.assertEqual(ev["worker_outcome"], "AMBIGUOUS")

    def _row3_block_target(self, *, feedback: bool):
        """A `"2.1"` item at ``AWAITING_LOCAL_PLAN_REVIEW`` in Workflow's real
        shape (``current_bundle_id: null``), whose plan manifest carries the
        bundle id -- plus, with ``feedback``, a genuine local ``BLOCK``
        bound to it."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.1",
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=fixtures.current_head(root)),
        )
        if feedback:
            fixtures.write_review_feedback(
                root, ".ai-review/wi-1/feedback",
                fixtures.build_review_feedback_text(
                    status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id="b" * 64,
                ),
            )
        return managed_repo

    def _row3_block_record(self, root: Path, *, pre_state_overrides: dict) -> dict:
        return _record(
            job_id="j1", target_repo=str(root), status=job.STATUS_COMPLETED,
            phase="AWAITING_LOCAL_PLAN_REVIEW", command="/review-plan", worker_outcome="SUCCESS",
            pre_state_overrides=pre_state_overrides,
        )

    def test_completed_with_predicate_row_unsatisfied_reconciles_to_failed(self) -> None:
        managed_repo = self._row3_block_target(feedback=False)
        record = self._row3_block_record(
            managed_repo.root,
            pre_state_overrides={"bundle_manifest_bundle_id": "b" * 64, "bundle_manifest_readable": True},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertEqual(results[0]["reconciliation_evidence"]["reason"], "predicate_not_satisfied")

    def test_completed_genuine_block_bound_to_the_manifest_bundle_reconciles_to_finished(self) -> None:
        """CP2's row-3 fix on the resume path: ``FAILED`` at base, where the
        predicate compared against ``current_bundle_id``."""
        managed_repo = self._row3_block_target(feedback=True)
        record = self._row3_block_record(
            managed_repo.root,
            pre_state_overrides={"bundle_manifest_bundle_id": "b" * 64, "bundle_manifest_readable": True},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])

    def test_completed_record_written_before_the_field_existed_is_not_satisfied(self) -> None:
        """A pre-milestone record lacks ``bundle_manifest_bundle_id``: the
        predicate treats the absence as "not satisfied" (fail closed), even
        with a genuine ``BLOCK`` on file and the old ``bundle_id`` set."""
        managed_repo = self._row3_block_target(feedback=True)
        record = self._row3_block_record(
            managed_repo.root,
            pre_state_overrides={"bundle_id": "b" * 64, "bundle_manifest_readable": True},
        )
        del record["pre_state"]["bundle_manifest_bundle_id"]
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertEqual(results[0]["reconciliation_evidence"]["reason"], "predicate_not_satisfied")

    def test_completed_that_actually_succeeded_reconciles_to_finished(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        fixtures.write_plan_manifest(managed_repo.root, "wi-1", 1)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_COMPLETED,
            phase="PLANNING", worker_outcome="SUCCESS",
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])


# ---------------------------------------------------------------------------
# Loading scope: "for this target repository" only.
# ---------------------------------------------------------------------------


class TargetScopingTest(_ResumeTestCase):
    def test_records_for_a_different_target_repository_are_left_untouched(self) -> None:
        managed_repo_a = _build_target(self.tmp_root / "a", phase="PLANNING")
        managed_repo_b = _build_target(self.tmp_root / "b", phase="PLANNING")

        record_a = _record(
            job_id="ja", target_repo=str(managed_repo_a.root), status=job.STATUS_PLANNED,
            phase="PLANNING",
        )
        # b's own record is deliberately unresolvable (deleted target) --
        # if resume(a) ever touched it, it would raise.
        record_b = _record(
            job_id="jb", target_repo=str(managed_repo_b.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING",
        )
        _write_record(self.runtime_root, record_a)
        _write_record(self.runtime_root, record_b)
        shutil.rmtree(managed_repo_b.root)

        results = job.resume(managed_repo_a, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["job_id"], "ja")
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)
        # b's own record on disk is untouched.
        on_disk_b = _read_record(self.runtime_root, "jb")
        self.assertEqual(on_disk_b["status"], job.STATUS_LAUNCHED)


# ---------------------------------------------------------------------------
# Coverage: every non-terminal status has a reachable row and a real
# writer.
# ---------------------------------------------------------------------------


class _WriteSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._original = job.runtime.write_json

    def __enter__(self) -> "_WriteSpy":
        def spy(runtime_root, rel_path, obj):
            self.calls.append((str(rel_path), obj))
            return self._original(runtime_root, rel_path, obj)

        job.runtime.write_json = spy
        return self

    def __exit__(self, *exc) -> None:
        job.runtime.write_json = self._original

    def statuses(self) -> list[str]:
        return [obj.get("status") for _rel, obj in self.calls if "jobs/" in _rel]


class NonTerminalCoverageTest(_ResumeTestCase):
    def test_the_three_non_terminal_statuses_are_exactly_planned_launched_completed(self) -> None:
        self.assertEqual(job.NON_TERMINAL_STATUSES, {job.STATUS_PLANNED, job.STATUS_LAUNCHED, job.STATUS_COMPLETED})
        self.assertEqual(job.NON_TERMINAL_STATUSES | job.TERMINAL_STATUSES, {
            job.STATUS_PLANNED, job.STATUS_LAUNCHED, job.STATUS_COMPLETED, job.STATUS_FINISHED,
            job.STATUS_FAILED, job.STATUS_INTERRUPTED, job.STATUS_INCOMPLETE, job.STATUS_GATE_BLOCKED,
            job.STATUS_DECLINED, job.STATUS_HANDOFF_PENDING,
        })

    def test_a_real_execute_step_run_writes_all_three_non_terminal_statuses(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        with _WriteSpy() as spy:
            job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        statuses = spy.statuses()
        for status in job.NON_TERMINAL_STATUSES:
            self.assertIn(status, statuses, statuses)


# ---------------------------------------------------------------------------
# End-to-end interruption tests: a real worker whose Controller is
# SIGKILLed mid-run, then resume. Automatic-lifecycle-orchestration CP5
# gives them the orphan case's shape: the record carries `lifecycle_lock`
# and `worker_process`, and the orphaned fake worker still holds the
# inherited lock, so `resume` refuses (exit 45) until the worker is
# released and gone, and only then reconciles the record.
# ---------------------------------------------------------------------------

#: A child Controller: one real `execute_step` against `argv[2]`, with the
#: default (unbounded) worker timeout.
_CHILD_STEP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pathlib import Path; from controller import job; "
    "from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT; "
    "ident = ControllerIdentity(generation=7, source_root=Path('/fake'), "
    "origin_source_root=Path('/fake'), source_kind=SOURCE_KIND_COMMIT, "
    "source_commit='a'*40, tree_digest='b'*64, generation_source='head', "
    "pinned_at='2024-01-01T00:00:00Z'); "
    "from controller.managed_repo import ManagedRepository; "
    "mr = ManagedRepository(root=Path(sys.argv[2]), manifest={}, "
    "workflow_version='2.3.1', profile='full', "
    "verify={'returncode': 0, 'stdout': '', 'stderr': ''}, "
    "status={'returncode': 0, 'stdout': '', 'stderr': ''}); "
    "job.execute_step(mr, identity=ident, runtime=Path(sys.argv[3]), claude_bin=sys.argv[4])"
)


class _OrphanWorkerCase(_ResumeTestCase):
    """Starts a child Controller whose fake worker hangs until
    ``self.release`` exists, and ``SIGKILL``s the Controller only once the
    record on disk carries ``worker_process`` (round 3, O2): a kill before
    ``Popen`` leaves no worker, and one before the flush leaves no group to
    wait on. The cleanup, registered before the child starts, creates the
    release file and kills every recorded worker group, re-reading the job
    files when it runs -- so no orphan outlives even a failed assertion."""

    def setUp(self) -> None:
        super().setUp()
        self.release = self.tmp_root / "release"
        self.invocations = self.tmp_root / "invocations"
        self._child: subprocess.Popen | None = None
        self.addCleanup(self._end_orphans)

    def _end_orphans(self) -> None:
        self.release.touch()
        if self._child is not None and self._child.poll() is None:
            self._child.kill()
            self._child.wait(timeout=10)
        for path in (self.runtime_root / "jobs").glob("*.json"):
            try:
                worker_process = json.loads(path.read_text()).get("worker_process") or {}
            except (OSError, ValueError, AttributeError):
                continue
            process_fixtures.kill_group(worker_process.get("pgid"))
        # Every fake the invocation counter saw (its pid is its pgid), in
        # case one started before its record carried `worker_process`.
        if self.invocations.exists():
            for line in self.invocations.read_text().splitlines():
                process_fixtures.kill_group(int(line))

    def _record_with_worker_process(self) -> dict | None:
        for path in (self.runtime_root / "jobs").glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if record.get("status") == job.STATUS_LAUNCHED and "worker_process" in record:
                return record
        return None

    def _orphan_worker(self, root: Path, *, env: dict | None = None) -> dict:
        self._child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_STEP, str(fixtures.REPO_ROOT), str(root), str(self.runtime_root),
             str(FAKE_CLAUDE)],
            env={**os.environ, "FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release),
                 "FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations), **(env or {})},
        )
        appeared = process_fixtures.wait_until(
            lambda: self._record_with_worker_process() is not None or self._child.poll() is not None,
            timeout=30,
        )
        record = self._record_with_worker_process()
        self.assertTrue(appeared and record is not None, "the LAUNCHED record never carried worker_process")
        self._child.send_signal(signal.SIGKILL)
        self._child.wait(timeout=10)
        # The orphaned worker is running on its own now (its start may
        # trail the flush, which happens as soon as `Popen` returns).
        self.assertTrue(process_fixtures.wait_until(lambda: self._invocation_count() >= 1),
                        "the orphaned worker never started")
        return record

    def _await_worker_gone(self, record: dict, root: Path) -> None:
        pgid = record["worker_process"]["pgid"]

        def gone() -> bool:
            if process_fixtures.group_has_running_member(pgid):
                return False
            try:
                lock.acquire_lifecycle_lock(root).release()
            except LifecycleWorkerActiveError:
                return False
            return True

        self.assertTrue(process_fixtures.wait_until(gone, timeout=30), "the released worker never ended")

    def _assert_no_worker_outlived(self, record: dict) -> None:
        pgid = record["worker_process"]["pgid"]

        def reaped() -> bool:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return True
            return False

        process_fixtures.wait_until(reaped, timeout=15)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)

    def _invocation_count(self) -> int:
        return len(self.invocations.read_text().splitlines()) if self.invocations.exists() else 0


class EndToEndInterruptionTest(_OrphanWorkerCase):
    def test_a_worker_killed_mid_run_leaves_a_launched_record_that_resume_reconciles(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        fixtures.copy_real_commands_dir(managed_repo.root / ".claude" / "commands")
        record = self._orphan_worker(managed_repo.root)

        job_files = list((self.runtime_root / "jobs").glob("*.json"))
        self.assertEqual(len(job_files), 1)
        on_disk = runtime_module.read_json(job_files[0])
        self.assertEqual(on_disk["status"], job.STATUS_LAUNCHED)
        self.assertIn("lifecycle_lock", on_disk)
        self.assertIn("worker_process", on_disk)

        # The orphaned worker still holds the inherited lock: resume refuses.
        with self.assertRaises(LifecycleWorkerActiveError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertIn(f"process group {record['worker_process']['pgid']}", ctx.exception.message)
        still = runtime_module.read_json(job_files[0])
        self.assertEqual(still["status"], job.STATUS_LAUNCHED)
        self.assertEqual(still["worker_process"], record["worker_process"])
        self.assertEqual(still["lifecycle_lock"], record["lifecycle_lock"])

        self.release.touch()
        self._await_worker_gone(record, managed_repo.root)
        # Release-runtime-observability CP4: the orphan wrote its whole
        # stream into the file named before spawn, with no Controller alive.
        written = [json.loads(line) for line in
                   Path(on_disk["worker_streams"]["stdout_path"]).read_text().splitlines()]
        self.assertEqual([event["type"] for event in written],
                         [event["type"] for event in fake_claude.default_events()])
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)
        self._assert_no_worker_outlived(record)


class BootstrapEndToEndInterruptionTest(_OrphanWorkerCase):
    """The declared row-7 fixture (revision 64, round 63's `B2`): "written
    to disk by a real `execute_step` against a zero-work-item target and
    re-read from there rather than hand-built" -- `validate_record`'s case
    2 returns `VALID` for the null `work_item_id`, the record reaches the
    reconciliation table, and the call does not abort."""

    def test_a_bootstrap_worker_killed_mid_run_leaves_a_null_work_item_id_record_that_resume_reconciles(self) -> None:
        root = self.tmp_root / "target"
        fixtures.build_target_git_repo(root)
        (root / "README.md").write_text("target fixture\n")
        fixtures.commit_all(root, "initial")
        fixtures.write_workflow_state(root, {
            "schema_version": 1, "active_work_item_id": None, "work_items": {},
        })
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")
        managed_repo = fixtures.build_target_managed_repository(root)
        record = self._orphan_worker(root)

        job_files = list((self.runtime_root / "jobs").glob("*.json"))
        self.assertEqual(len(job_files), 1)
        on_disk = runtime_module.read_json(job_files[0])
        self.assertEqual(on_disk["status"], job.STATUS_LAUNCHED)
        self.assertIsNone(on_disk["work_item_id"])
        self.assertEqual(on_disk["expected_transition"]["from"], "__NO_PHASE__")
        self.assertIn("lifecycle_lock", on_disk)
        self.assertIn("worker_process", on_disk)

        with self.assertRaises(LifecycleWorkerActiveError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        still = runtime_module.read_json(job_files[0])
        self.assertEqual(still["status"], job.STATUS_LAUNCHED)
        self.assertIsNone(still["work_item_id"])

        self.release.touch()
        self._await_worker_gone(record, root)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)
        self._assert_no_worker_outlived(record)


class OrphanWorkerTest(_OrphanWorkerCase):
    """The orphan case: while the orphaned worker lives, ``step`` and
    ``resume`` both refuse (exit 45) and the record stays ``LAUNCHED`` with
    both fields; once it is released and gone, ``resume`` reconciles it
    ``INTERRUPTED``, and the next ``step`` launches exactly one worker."""

    def test_step_and_resume_refuse_until_the_orphan_ends(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        record = self._orphan_worker(managed_repo.root)
        self.assertEqual(self._invocation_count(), 1)

        with self.assertRaises(LifecycleWorkerActiveError):
            job.execute_step(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                             claude_bin=str(FAKE_CLAUDE))
        with self.assertRaises(LifecycleWorkerActiveError):
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        on_disk = _read_record(self.runtime_root, record["job_id"])
        self.assertEqual(on_disk["status"], job.STATUS_LAUNCHED)
        self.assertEqual(on_disk["worker_process"], record["worker_process"])
        self.assertIn("lifecycle_lock", on_disk)
        self.assertEqual(self._invocation_count(), 1)

        self.release.touch()
        self._await_worker_gone(record, managed_repo.root)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual([r["status"] for r in results], [job.STATUS_INTERRUPTED])

        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}):
            job.execute_step(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                             claude_bin=str(FAKE_CLAUDE), timeout=10)
        self.assertEqual(self._invocation_count(), 2)
        self._assert_no_worker_outlived(record)


class UnreconcilableOrphanTest(_OrphanWorkerCase):
    """Round 1's I2: the released orphan commits a state change that moves
    the phase and fails the row's postcondition -- a generation-record
    commit T with no regenerated bundle. ``resume`` persists the record
    ``FAILED`` (``UnreconcilableJobError``) before raising, a second
    ``resume`` passes it as terminal history, and the next ``step`` reaches
    the evidence-based decision (CP4B's regeneration gate) and launches
    nothing."""

    def test_the_record_becomes_failed_and_the_next_step_decides_from_evidence(self) -> None:
        managed_repo = fixtures.build_implementation_target(self.tmp_root, phase="SELF_REVIEWING_IMPLEMENTATION")
        root = managed_repo.root
        state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"]["wi-1"].update(
            phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW", implementation_revision=1,
            reviewed_implementation_head=fixtures.current_head(root),
        )
        record = self._orphan_worker(root, env={
            "FAKE_CLAUDE_WRITES": json.dumps([{"path": str(state_path), "text": json.dumps(state, indent=2) + "\n"}]),
            "FAKE_CLAUDE_GIT_COMMIT": "Record implementation bundle generation (no bundle follows)",
        })
        self.assertEqual(record["selected_action"]["command"], "/milestone-implement wi-1")
        head_before = fixtures.current_head(root)
        self.release.touch()
        self._await_worker_gone(record, root)
        self.assertNotEqual(fixtures.current_head(root), head_before, "the orphan's commit never landed")

        with self.assertRaises(UnreconcilableJobError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertIn("postcondition not satisfied", ctx.exception.message)
        self.assertIn("now FAILED", ctx.exception.message)
        on_disk = _read_record(self.runtime_root, record["job_id"])
        self.assertEqual(on_disk["status"], job.STATUS_FAILED)
        self.assertFalse(on_disk["transition_verified"])
        self.assertEqual(on_disk["reconciliation_evidence"]["code"], "UnreconcilableJobError")
        self.assertIn("postcondition_detail", on_disk["reconciliation_evidence"])
        self.assertEqual(on_disk["observed_phase_after"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

        again = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual([r["status"] for r in again], [job.STATUS_FAILED])
        self.assertNotIn("reconciled_this_call", again[0])

        never = self.tmp_root / "never-created"
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_REQUIRE_FILE": str(never),
                                                     "FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}):
            gated = job.execute_step(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                                     claude_bin=str(FAKE_CLAUDE), timeout=10)
        self.assertEqual(gated["status"], job.STATUS_GATE_BLOCKED)
        self.assertIn("generate", gated["human_gate_pending"]["what_is_required"].lower())
        self.assertEqual(self._invocation_count(), 1, "the gate launches nothing")
        self._assert_no_worker_outlived(record)


# ---------------------------------------------------------------------------
# CP3 (`workflow-controller-worker-execution-hardening`) -- the plan-bundle
# postcondition on both resume paths, per row group.
# ---------------------------------------------------------------------------


#: ``(from_phase, governing_workflow_version, command, post_phase)`` for
#: every postcondition-bearing row with a real work item (row 7 below).
_POSTCONDITION_ROWS = (
    ("PLANNING", "2.1", "/milestone-plan", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("PLANNING", "2.2", "/milestone-plan", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("PLANNING", "1", "/milestone-plan", "AWAITING_EXTERNAL_PLAN_REVIEW"),
    ("REVISING_PLAN", "2.1", "/apply-plan-review", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("REVISING_PLAN", "2.2", "/apply-plan-review", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review", "AWAITING_EXTERNAL_PLAN_REVIEW"),
)


class PlanBundlePostconditionResumeTest(_ResumeTestCase):
    """The postcondition evaluated identically on resume: a target already
    published at ``plan_revision: 2`` whose plan bundle is either coherent
    (revision 2 -> ``FINISHED``) or still the previous round's (revision 1
    -> ``COMPLETED``: ``FAILED``; ``LAUNCHED`` with moved state:
    ``UnreconcilableJobError`` carrying ``postcondition_detail``)."""

    def _seed(self, name: str, from_phase: str, version: str, command: str, post_phase: str,
              *, status: str, manifest_revision: int):
        managed_repo = _build_target(
            self.tmp_root / name, phase=post_phase, governing_workflow_version=version, plan_revision=2,
        )
        root = managed_repo.root
        head = fixtures.current_head(root)
        fixtures.write_plan_manifest(root, "wi-1", manifest_revision)
        if from_phase == "AWAITING_EXTERNAL_PLAN_REVIEW":
            # Row 5's own digest predicate holds (the bundle's generated
            # files changed), so the postcondition is the clause under test.
            changed = root / ".ai-review" / "wi-1" / "current" / "CHANGED_FILES.txt"
            changed.write_text("generated: round 2\nREADME.md\n")
        runtime_root = self.tmp_root / name / "runtime"
        runtime_root.mkdir(parents=True)
        extra = {"worker_outcome": "SUCCESS"} if status == job.STATUS_COMPLETED else {}
        record = _record(
            job_id="j1", target_repo=str(root), status=status, phase=from_phase,
            governing_workflow_version=version, command=command,
            pre_state_overrides={"target_head": head, "bundle_generated_digest": None},
            **extra,
        )
        _write_record(runtime_root, record)
        return managed_repo, runtime_root

    def test_completed_coherent_finishes_and_stale_fails(self) -> None:
        for i, (from_phase, version, command, post_phase) in enumerate(_POSTCONDITION_ROWS):
            with self.subTest(from_phase=from_phase, version=version):
                managed_repo, runtime_root = self._seed(
                    f"c-ok-{i}", from_phase, version, command, post_phase,
                    status=job.STATUS_COMPLETED, manifest_revision=2,
                )
                results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                self.assertEqual(results[0]["status"], job.STATUS_FINISHED, results[0])

                managed_repo, runtime_root = self._seed(
                    f"c-stale-{i}", from_phase, version, command, post_phase,
                    status=job.STATUS_COMPLETED, manifest_revision=1,
                )
                results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                self.assertEqual(results[0]["status"], job.STATUS_FAILED)
                self.assertFalse(results[0]["transition_verified"])
                ev = results[0]["reconciliation_evidence"]
                self.assertEqual(ev["reason"], "postcondition_not_satisfied")
                self.assertEqual(
                    ev["postcondition_detail"], "manifest plan_revision 1 != state plan_revision 2",
                )
                self.assertEqual(_read_record(runtime_root, "j1")["status"], job.STATUS_FAILED)

    def test_launched_coherent_finishes_never_relaunches(self) -> None:
        for i, (from_phase, version, command, post_phase) in enumerate(_POSTCONDITION_ROWS):
            with self.subTest(from_phase=from_phase, version=version):
                managed_repo, runtime_root = self._seed(
                    f"l-ok-{i}", from_phase, version, command, post_phase,
                    status=job.STATUS_LAUNCHED, manifest_revision=2,
                )
                with _NeverLaunches():
                    results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
                self.assertTrue(results[0]["transition_verified"])

    def test_launched_stale_with_moved_phase_is_unreconcilable_naming_the_postcondition(self) -> None:
        moved = [row for row in _POSTCONDITION_ROWS if row[0] != row[3]]
        self.assertEqual(len(moved), 5)
        for i, (from_phase, version, command, post_phase) in enumerate(moved):
            with self.subTest(from_phase=from_phase, version=version):
                managed_repo, runtime_root = self._seed(
                    f"l-stale-{i}", from_phase, version, command, post_phase,
                    status=job.STATUS_LAUNCHED, manifest_revision=1,
                )
                with self.assertRaises(UnreconcilableJobError) as ctx:
                    job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                ev = ctx.exception.evidence
                self.assertEqual(ev["pre_phase"], from_phase)
                self.assertEqual(ev["observed_phase_after"], post_phase)
                self.assertEqual(
                    ev["postcondition_detail"], "manifest plan_revision 1 != state plan_revision 2",
                )
                self.assertIn("postcondition not satisfied", str(ctx.exception))

    def test_launched_self_loop_stale_with_unchanged_phase_and_head_is_interrupted(self) -> None:
        """The `"1"` self-loop row: phase and HEAD both unchanged, so a
        failed postcondition keeps row 3's ``INTERRUPTED`` (a fresh `step`
        may retry) -- "Scope judgments", `LAUNCHED` bullet."""
        managed_repo, runtime_root = self._seed(
            "l-self-loop", "AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review",
            "AWAITING_EXTERNAL_PLAN_REVIEW", status=job.STATUS_LAUNCHED, manifest_revision=1,
        )
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)

    def test_launched_moved_state_failing_another_clause_carries_no_postcondition_detail(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        head = fixtures.current_head(managed_repo.root)
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=job.STATUS_LAUNCHED,
            phase="PLANNING", pre_state_overrides={"target_head": head},
        )
        _write_record(self.runtime_root, record)
        _set_target_phase(managed_repo.root, "wi-1", "SELF_REVIEWING_IMPLEMENTATION")
        with self.assertRaises(UnreconcilableJobError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertNotIn("postcondition_detail", ctx.exception.evidence)


class PlanBundlePostconditionRowSevenResumeTest(_ResumeTestCase):
    """Row 7 on both resume paths: the new work item is the single new
    key; its own plan bundle decides."""

    def _seed(self, *, status: str, manifest_revision: int | None):
        managed_repo = _build_target(self.tmp_root, phase="MILESTONE_COMPLETE", work_item_id="wi-existing")
        head = fixtures.current_head(managed_repo.root)
        _add_work_item(managed_repo.root, "wi-new", phase="AWAITING_LOCAL_PLAN_REVIEW")
        if manifest_revision is not None:
            fixtures.write_plan_manifest(managed_repo.root, "wi-new", manifest_revision)
        extra = {"worker_outcome": "SUCCESS"} if status == job.STATUS_COMPLETED else {}
        record = _record(
            job_id="j1", target_repo=str(managed_repo.root), status=status,
            phase="__NO_PHASE__", governing_workflow_version=None, command="/milestone-plan",
            work_item_id=None,
            expected_transition={"from": "__NO_PHASE__", "to_any_of": ["AWAITING_LOCAL_PLAN_REVIEW"]},
            pre_state_overrides={"target_head": head, "pre_work_item_keys": ["wi-existing"]},
            **extra,
        )
        _write_record(self.runtime_root, record)
        return managed_repo

    def test_completed_stale_fails(self) -> None:
        managed_repo = self._seed(status=job.STATUS_COMPLETED, manifest_revision=None)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertEqual(results[0]["reconciliation_evidence"]["reason"], "postcondition_not_satisfied")

    def test_completed_coherent_finishes(self) -> None:
        managed_repo = self._seed(status=job.STATUS_COMPLETED, manifest_revision=1)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)

    def test_launched_stale_is_unreconcilable_naming_the_postcondition(self) -> None:
        managed_repo = self._seed(status=job.STATUS_LAUNCHED, manifest_revision=0)
        with self.assertRaises(UnreconcilableJobError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(
            ctx.exception.evidence["postcondition_detail"], "manifest plan_revision 0 != state plan_revision 1",
        )


# ---------------------------------------------------------------------------
# Worker-execution hardening CP5 -- the observed partial `/apply-plan-review`
# (RepFlow) on both resume paths: state already published at plan revision
# 11, current plan bundle still at 10.
# ---------------------------------------------------------------------------


_PARTIAL_APPLY_DETAIL = "manifest plan_revision 10 != state plan_revision 11"


class PartialApplyPlanReviewResumeTest(_ResumeTestCase):
    def _seed(self, *, status: str, manifest_revision: int = 10, version: str = "2.2",
              post_phase: str = "AWAITING_LOCAL_PLAN_REVIEW", from_phase: str = "REVISING_PLAN"):
        """The target as the interrupted worker left it, plus a job record
        for that worker (``pre_state`` at revision 10, the unchanged HEAD)."""
        managed_repo = _build_target(
            self.tmp_root, phase=post_phase, governing_workflow_version=version, plan_revision=11,
        )
        root = managed_repo.root
        head = fixtures.current_head(root)
        fixtures.write_plan_manifest(root, "wi-1", manifest_revision, generation_head=head)
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(status="REVISE", reviewed_base_commit=head),
        )
        extra = {"worker_outcome": "SUCCESS"} if status == job.STATUS_COMPLETED else {}
        record = _record(
            job_id="j1", target_repo=str(root), status=status, phase=from_phase,
            governing_workflow_version=version, command="/apply-plan-review wi-1",
            pre_state_overrides={"target_head": head, "plan_revision": 10}, **extra,
        )
        _write_record(self.runtime_root, record)
        return managed_repo

    def test_completed_record_becomes_failed_never_finished(self) -> None:
        managed_repo = self._seed(status=job.STATUS_COMPLETED)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertFalse(results[0]["transition_verified"])
        ev = results[0]["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "postcondition_not_satisfied")
        self.assertEqual(ev["postcondition_detail"], _PARTIAL_APPLY_DETAIL)
        self.assertEqual(_read_record(self.runtime_root, "j1")["status"], job.STATUS_FAILED)

    def test_launched_record_is_unreconcilable_never_finished_or_interrupted(self) -> None:
        managed_repo = self._seed(status=job.STATUS_LAUNCHED)
        with _NeverLaunches(), self.assertRaises(UnreconcilableJobError) as ctx:
            job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        ev = ctx.exception.evidence
        self.assertEqual(ev["pre_phase"], "REVISING_PLAN")
        self.assertEqual(ev["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(ev["postcondition_detail"], _PARTIAL_APPLY_DETAIL)
        self.assertEqual(_read_record(self.runtime_root, "j1")["status"], job.STATUS_LAUNCHED)

    def test_launched_positive_control_revision_11_manifest_finishes(self) -> None:
        managed_repo = self._seed(status=job.STATUS_LAUNCHED, manifest_revision=11)
        with _NeverLaunches():
            results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
        self.assertTrue(results[0]["transition_verified"])

    def test_1_self_loop_unchanged_phase_and_head_is_interrupted_then_retries_apply(self) -> None:
        """The `"1"` item whose worker published revision 11 but left the
        revision-10 bundle and the round's feedback: row 3's
        ``INTERRUPTED``, and the next decision is the automatic
        ``/apply-plan-review`` retry, not a gate ("Scope judgments")."""
        from controller import evidence, target_state

        managed_repo = self._seed(
            status=job.STATUS_LAUNCHED, version="1",
            post_phase="AWAITING_EXTERNAL_PLAN_REVIEW", from_phase="AWAITING_EXTERNAL_PLAN_REVIEW",
        )
        with _NeverLaunches():
            results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)

        snapshot = target_state.read(managed_repo)
        work_item = target_state.select_work_item(snapshot, work_item_id=None)
        decision = evidence.decide(managed_repo, snapshot, work_item)
        self.assertIsNone(decision.gate)
        self.assertTrue(decision.automatic)
        self.assertEqual(decision.action.command, "/apply-plan-review wi-1")



# ---------------------------------------------------------------------------
# `workflow-controller-automatic-lifecycle-orchestration` CP2 -- rows 12-18
# on both resume paths. The pre-state is captured for real from the seeded
# target (`job._capture_pre_state`), then the scripted worker effect is
# applied -- exactly what a worker would have left behind -- and the record
# is reconciled: the same verification `execute_step` applies.
# ---------------------------------------------------------------------------

_IMPL_B = "b" * 64
_IMPL_C = "c" * 64
_IMPL_REVIEWED_HEAD = "1" * 40
_LOCAL_ROLE = "LOCAL_MODEL_IMPLEMENTATION_REVIEW"
_MANUAL_ROLE = "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"


def _feedback(status: str, role: str, **kwargs) -> str:
    kwargs.setdefault("reviewed_bundle_id", _IMPL_B)
    return fixtures.build_review_feedback_text(status=status, reviewer_role=role, **kwargs)


#: ``name -> (phase, version, command, seed kwargs, review_stage, pasted
#: feedback, effect, expected COMPLETED outcome)``, where the expected
#: outcome is ``("FINISHED", None)`` or ``(reason, postcondition detail
#: fragment or None)``.
_IMPL_CASES = {
    "row 13 committed checkpoint": (
        "IMPLEMENTING", "2.2", "/milestone-implement",
        dict(checkpoints={"CP1": {"status": "IN_PROGRESS"}, "CP2": {"status": "PENDING"}}), False, None,
        fixtures.complete_checkpoint_effect("CP1"), ("FINISHED", None),
    ),
    "row 12 uncommitted checkpoint, HEAD moved": (
        "IMPLEMENTING", "2.1", "/milestone-implement",
        dict(checkpoints={"CP1": {"status": "IN_PROGRESS"}, "CP2": {"status": "PENDING"}}), False, None,
        fixtures.complete_checkpoint_effect("CP1", commit="product"), ("predicate_not_satisfied", None),
    ),
    "row 13 nothing done": (
        "IMPLEMENTING", "2.2", "/milestone-implement",
        dict(checkpoints={"CP1": {"status": "IN_PROGRESS"}, "CP2": {"status": "PENDING"}}), False, None,
        None, ("predicate_not_satisfied", None),
    ),
    "row 13 uncommitted self-review entry": (
        "IMPLEMENTING", "2.2", "/milestone-implement",
        dict(checkpoints={"CP1": {"status": "COMPLETE"}, "CP2": {"status": "IN_PROGRESS"}}), False, None,
        fixtures.complete_checkpoint_effect("CP2", phase="SELF_REVIEWING_IMPLEMENTATION", commit="product"),
        ("postcondition_not_satisfied", "the checkpoint completion is uncommitted"),
    ),
    "row 15 coherent generation": (
        "SELF_REVIEWING_IMPLEMENTATION", "2.2", "/milestone-implement", {}, False, None,
        fixtures.generation_effect("AWAITING_LOCAL_IMPLEMENTATION_REVIEW"), ("FINISHED", None),
    ),
    "row 14 stale generation": (
        "SELF_REVIEWING_IMPLEMENTATION", "2.1", "/milestone-implement", {}, False, None,
        fixtures.generation_effect("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", manifest_revision=0),
        ("postcondition_not_satisfied", "manifest implementation_revision 0 != state implementation_revision 1"),
    ),
    "row 16 local approve": (
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation", {}, True, None,
        fixtures.review_writes_effect(
            feedback=_feedback("APPROVE", _LOCAL_ROLE), phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            implementation_review_stages=fixtures.implementation_review_ledger(_IMPL_C, local_bundle_id=_IMPL_B),
        ),
        ("FINISHED", None),
    ),
    "row 16 local approve on other content": (
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation", {}, True, None,
        fixtures.review_writes_effect(
            feedback=_feedback("APPROVE", _LOCAL_ROLE), phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            implementation_review_stages=fixtures.implementation_review_ledger("d" * 64, local_bundle_id=_IMPL_B),
        ),
        ("postcondition_not_satisfied", "ledger review_content_id"),
    ),
    "row 16 local block": (
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation", {}, True, None,
        fixtures.review_writes_effect(feedback=_feedback("BLOCK", _LOCAL_ROLE)), ("FINISHED", None),
    ),
    "row 16 no verdict": (
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation", {}, True, None,
        None, ("predicate_not_satisfied", None),
    ),
    "row 17 manual approve": (
        "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2", "/record-manual-implementation-review",
        dict(implementation_review_stages=fixtures.implementation_review_ledger(_IMPL_C, local_bundle_id=_IMPL_B)),
        True, _feedback("APPROVE", _MANUAL_ROLE),
        fixtures.review_writes_effect(
            phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            implementation_review_stages=fixtures.implementation_review_ledger(
                _IMPL_C, local_bundle_id=_IMPL_B, manual_bundle_id=_IMPL_B,
            ),
        ),
        ("FINISHED", None),
    ),
    "row 17 manual approve without the ledger write": (
        "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2", "/record-manual-implementation-review",
        dict(implementation_review_stages=fixtures.implementation_review_ledger(_IMPL_C, local_bundle_id=_IMPL_B)),
        True, _feedback("APPROVE", _MANUAL_ROLE),
        fixtures.review_writes_effect(phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"),
        ("postcondition_not_satisfied", "the ledger does not record MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"),
    ),
    "row 18 ordinary post-fix": (
        "APPLYING_REVIEW_FEEDBACK", "2.2", "/apply-implementation-review", {}, True,
        _feedback("REVISE", _LOCAL_ROLE),
        fixtures.generation_effect("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", revision=2, fix_commit=True),
        ("FINISHED", None),
    ),
    "row 18 no generation": (
        "APPLYING_REVIEW_FEEDBACK", "2.2", "/apply-implementation-review", {}, True,
        _feedback("REVISE", _LOCAL_ROLE),
        fixtures.review_writes_effect(phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW"),
        ("postcondition_not_satisfied", "no bundle generation ran in this job"),
    ),
}


class ImplementationStageResumeTest(_ResumeTestCase):
    """CP2's execute/resume parity: every row-12-18 case reconciles on
    ``COMPLETED`` exactly as ``execute_step`` verifies it, and on
    ``LAUNCHED`` to ``FINISHED`` when verified, ``INTERRUPTED`` when phase
    and ``HEAD`` are both unchanged, and ``UnreconcilableJobError`` --
    carrying ``postcondition_detail`` when the postcondition is the failing
    clause -- when state moved without verifying."""

    def _seed(self, name: str, case: str, *, status: str, drop_manifest_bundle_id: bool = False):
        from controller import target_state

        phase, version, command, seed_kwargs, review_stage, pasted, effect, _expected = _IMPL_CASES[case]
        case_root = self.tmp_root / name
        overrides = dict(seed_kwargs)
        if review_stage:
            overrides.setdefault("implementation_revision", 1)
            overrides.setdefault("reviewed_implementation_head", _IMPL_REVIEWED_HEAD)
        managed_repo = fixtures.build_implementation_target(
            case_root, phase=phase, governing_workflow_version=version, **overrides,
        )
        root = managed_repo.root
        if review_stage:
            fixtures.write_implementation_manifest(root, "wi-1", 1, reviewed_implementation_head=_IMPL_REVIEWED_HEAD)
        if pasted is not None:
            fixtures.write_review_feedback(root, ".ai-review/wi-1/feedback", pasted)
        snapshot = target_state.read(managed_repo)
        work_item = target_state.select_work_item(snapshot, work_item_id="wi-1")
        pre_state = job._durable_pre_state(job._capture_pre_state(managed_repo, snapshot, work_item))
        if effect is not None:
            effect(root)
        runtime_root = case_root / "runtime"
        runtime_root.mkdir(parents=True)
        extra = {"worker_outcome": "SUCCESS"} if status == job.STATUS_COMPLETED else {}
        overrides = {k: v for k, v in pre_state.items() if k not in ("phase", "governing_workflow_version")}
        record = _record(
            job_id="j1", target_repo=str(root), status=status, phase=phase,
            governing_workflow_version=version, command=f"{command} wi-1", pre_state_overrides=overrides,
            **extra,
        )
        if drop_manifest_bundle_id:
            del record["pre_state"]["bundle_manifest_bundle_id"]
        _write_record(runtime_root, record)
        return managed_repo, runtime_root

    def test_completed_records_reconcile_exactly_as_execute_step_verifies(self) -> None:
        for i, (case, spec) in enumerate(_IMPL_CASES.items()):
            reason, detail = spec[-1]
            with self.subTest(case=case):
                managed_repo, runtime_root = self._seed(f"c-{i}", case, status=job.STATUS_COMPLETED)
                with _NeverLaunches():
                    results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                result = results[0]
                self.assertTrue(result["reconciled_this_call"])
                if reason == "FINISHED":
                    self.assertEqual(result["status"], job.STATUS_FINISHED, result.get("reconciliation_evidence"))
                    self.assertTrue(result["transition_verified"])
                    continue
                self.assertEqual(result["status"], job.STATUS_FAILED)
                ev = result["reconciliation_evidence"]
                self.assertEqual(ev["reason"], reason, ev)
                if detail is None:
                    self.assertNotIn("postcondition_detail", ev)
                else:
                    self.assertIn(detail, ev["postcondition_detail"])
                self.assertEqual(_read_record(runtime_root, "j1")["status"], job.STATUS_FAILED)

    def test_launched_records_reconcile_to_finished_interrupted_or_unreconcilable(self) -> None:
        for i, (case, spec) in enumerate(_IMPL_CASES.items()):
            phase = spec[0]
            reason, detail = spec[-1]
            with self.subTest(case=case):
                managed_repo, runtime_root = self._seed(f"l-{i}", case, status=job.STATUS_LAUNCHED)
                root = managed_repo.root
                record = _read_record(runtime_root, "j1")
                observed_phase = fixtures.state_entry(root)["phase"]
                phase_unchanged = observed_phase == phase
                head_unchanged = fixtures.current_head(root) == record["pre_state"]["target_head"]
                if reason == "FINISHED":
                    with _NeverLaunches():
                        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                    self.assertEqual(results[0]["status"], job.STATUS_FINISHED)
                    continue
                if phase_unchanged and head_unchanged:
                    with _NeverLaunches():
                        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                    self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)
                    continue
                with self.assertRaises(UnreconcilableJobError) as ctx:
                    job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                ev = ctx.exception.evidence
                self.assertEqual(ev["pre_phase"], phase)
                self.assertEqual(ev["observed_phase_after"], observed_phase)
                if detail is None:
                    self.assertNotIn("postcondition_detail", ev)
                else:
                    self.assertIn(detail, ev["postcondition_detail"])
                    self.assertIn("postcondition not satisfied", str(ctx.exception))
                self.assertEqual(_read_record(runtime_root, "j1")["status"], job.STATUS_LAUNCHED)

    def test_the_launched_cases_cover_every_reconciliation_outcome(self) -> None:
        """The table above exercises all three ``LAUNCHED`` outcomes,
        including the moved-state postcondition failure carrying its
        detail."""
        outcomes = set()
        for i, (case, spec) in enumerate(_IMPL_CASES.items()):
            managed_repo, runtime_root = self._seed(f"cover-{i}", case, status=job.STATUS_LAUNCHED)
            try:
                results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
            except UnreconcilableJobError as exc:
                outcomes.add("unreconcilable+detail" if "postcondition_detail" in exc.evidence else "unreconcilable")
            else:
                outcomes.add(results[0]["status"])
        self.assertEqual(
            outcomes,
            {job.STATUS_FINISHED, job.STATUS_INTERRUPTED, "unreconcilable", "unreconcilable+detail"},
        )

    def test_a_pre_milestone_row_16_block_record_is_not_satisfied(self) -> None:
        """A record lacking ``bundle_manifest_bundle_id`` (written before
        CP2) fails closed even with a genuine current ``BLOCK`` on file."""
        managed_repo, runtime_root = self._seed(
            "pre-milestone", "row 16 local block", status=job.STATUS_COMPLETED, drop_manifest_bundle_id=True,
        )
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertEqual(results[0]["reconciliation_evidence"]["reason"], "predicate_not_satisfied")


# ---------------------------------------------------------------------------
# workflow-controller-automatic-lifecycle-orchestration CP5 -- job
# dispositions: the terminal disposition, `resume --abandon`, and the
# liveness verdict `resume` and `--abandon` apply.
# ---------------------------------------------------------------------------


class _DispositionCase(_ResumeTestCase):
    """A ``"2.1"`` ``PLANNING`` target whose ``HEAD`` a hand-built
    ``LAUNCHED`` record captured, so ``resume`` reconciles it
    ``INTERRUPTED`` unless something moves."""

    def setUp(self) -> None:
        super().setUp()
        self.managed_repo = _build_target(self.tmp_root, phase="PLANNING")
        self.root = self.managed_repo.root
        self.head = fixtures.current_head(self.root)
        self.invocations = self.tmp_root / "invocations"

    def _launched(self, job_id: str, **fields) -> dict:
        record = _record(
            job_id=job_id, target_repo=str(self.root), status=job.STATUS_LAUNCHED, phase="PLANNING",
            pre_state_overrides={"target_head": self.head},
        )
        record.update(fields)
        return record

    def _with_worker(self, job_id: str, worker_process: dict, **fields) -> dict:
        return self._launched(job_id, lifecycle_lock={"path": str(lock.resolve_git_dir(self.root))},
                              worker_process=worker_process, **fields)

    def _step(self, managed_repo=None) -> dict:
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}):
            return job.execute_step(managed_repo or self.managed_repo, identity=FAKE_IDENTITY,
                                    runtime=self.runtime_root, claude_bin=str(FAKE_CLAUDE), timeout=10)

    def _launches(self) -> int:
        return len(self.invocations.read_text().splitlines()) if self.invocations.exists() else 0

    def _resume(self, managed_repo=None) -> list[dict]:
        return job.resume(managed_repo or self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)

    def _abandon(self, job_id: str, *, acknowledge: bool = False, managed_repo=None) -> dict:
        return job.abandon(managed_repo or self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                           job_id=job_id, acknowledge_unverifiable_worker=acknowledge)

    def _bytes(self, job_id: str) -> bytes:
        return (self.runtime_root / "jobs" / f"{job_id}.json").read_bytes()


class PreMilestoneUnreconcilableRecordTest(_DispositionCase):
    """A record written before this milestone (no ``lifecycle_lock``) that
    is unreconcilable: ``resume`` raises and leaves it ``LAUNCHED``, as
    before, naming ``--abandon``; ``step`` refuses; ``resume --abandon``
    marks it ``FAILED`` (``OperatorAbandoned``), and the next ``step``
    proceeds."""

    def test_abandon_is_the_way_out(self) -> None:
        _write_record(self.runtime_root, self._launched("j1"))
        (self.root / "unrelated.txt").write_text("something the worker cannot account for\n")
        fixtures.commit_all(self.root, "unrelated commit")

        with self.assertRaises(UnreconcilableJobError) as ctx:
            self._resume()
        self.assertIn(f"workflow-controller resume --abandon j1 {self.root}", ctx.exception.message)
        on_disk = _read_record(self.runtime_root, "j1")
        self.assertEqual(on_disk["status"], job.STATUS_LAUNCHED)
        self.assertNotIn("reconciliation_evidence", on_disk)

        with self.assertRaises(PendingJobReconciliationError):
            self._step()
        self.assertEqual(self._launches(), 0)

        abandoned = self._abandon("j1")
        self.assertEqual(abandoned["status"], job.STATUS_FAILED)
        self.assertEqual(abandoned["reconciliation_evidence"],
                         {"code": "OperatorAbandoned", "abandoned_status": "LAUNCHED", "validity": None})
        self.assertNotIn("worker_liveness", abandoned)
        self.assertEqual(_read_record(self.runtime_root, "j1"), abandoned)
        self._step()
        self.assertEqual(self._launches(), 1)

    def test_abandon_refuses_a_terminal_record_another_target_and_an_active_worker(self) -> None:
        _write_record(self.runtime_root, self._launched("j-terminal", status=job.STATUS_FAILED))
        _write_record(self.runtime_root, self._launched("j-other", target_repo="/somewhere/else"))
        sleeper = process_fixtures.spawn_sleeper(self)
        _write_record(self.runtime_root, self._with_worker(
            "j-active", process_fixtures.worker_process_dict(sleeper.pid)))
        before = {name: self._bytes(name) for name in ("j-terminal", "j-other", "j-active")}

        with self.assertRaises(JobAbandonRefusedError) as ctx:
            self._abandon("j-terminal")
        self.assertIn("already terminal", ctx.exception.message)
        with self.assertRaises(JobAbandonRefusedError) as ctx:
            self._abandon("j-other")
        self.assertIn("belongs to target '/somewhere/else'", ctx.exception.message)
        for acknowledge in (False, True):
            with self.subTest(acknowledge=acknowledge):
                with self.assertRaises(LifecycleWorkerActiveError) as ctx:
                    self._abandon("j-active", acknowledge=acknowledge)
                self.assertNotIsInstance(ctx.exception, LifecycleWorkerUnverifiableError)
                self.assertIn(f"process group {sleeper.pid}", ctx.exception.message)
                self.assertIn("no flag overrides it", ctx.exception.message)
        self.assertEqual({name: self._bytes(name) for name in before}, before)


class AbandonBeyondPreMilestoneRecordsTest(_DispositionCase):
    """``--abandon`` for every pending job file ``resume`` cannot
    reconcile (round 2, O2), and the ``JOB_ID`` constraint (round 3, O5)."""

    def test_a_record_whose_work_item_vanished(self) -> None:
        _write_record(self.runtime_root, self._launched("j-vanished", work_item_id="gone"))
        with self.assertRaises(PendingJobReconciliationError) as ctx:
            self._step()
        self.assertIn(f"workflow-controller resume --abandon j-vanished {self.root}", ctx.exception.message)
        with self.assertRaises(StaleJobRecordError) as ctx:
            self._resume()
        self.assertIn(f"workflow-controller resume --abandon j-vanished {self.root}", ctx.exception.message)

        abandoned = self._abandon("j-vanished")
        self.assertEqual(abandoned["status"], job.STATUS_FAILED)
        self.assertEqual(abandoned["reconciliation_evidence"]["validity"], "work_item_absent")
        results = self._resume()
        self.assertEqual([r["job_id"] for r in results], ["j-vanished"])
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertEqual(results[0]["resume_marked"]["outcome"], "unreadable")
        self._step()
        self.assertEqual(self._launches(), 1)

    def test_a_record_whose_status_is_outside_the_enumeration(self) -> None:
        _write_record(self.runtime_root, self._launched("j-weird", status="WEIRD"))
        with self.assertRaises(PendingJobReconciliationError) as ctx:
            self._step()
        self.assertIn(f"workflow-controller resume --abandon j-weird {self.root}", ctx.exception.message)
        with self.assertRaises(StaleJobRecordError) as ctx:
            self._resume()
        self.assertIn(f"workflow-controller resume --abandon j-weird {self.root}", ctx.exception.message)
        abandoned = self._abandon("j-weird")
        self.assertEqual(abandoned["reconciliation_evidence"]["abandoned_status"], "WEIRD")
        self.assertEqual([r["status"] for r in self._resume()], [job.STATUS_FAILED])
        self._step()
        self.assertEqual(self._launches(), 1)

    def test_an_unparseable_file_is_set_aside_and_replaced_from_any_target(self) -> None:
        jobs_dir = self.runtime_root / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        original = b"{not json at all \xff"
        (jobs_dir / "garbage.json").write_bytes(original)
        with self.assertRaises(PendingJobReconciliationError) as ctx:
            self._step()
        self.assertIn(f"workflow-controller resume --abandon garbage {self.root}", ctx.exception.message)
        with self.assertRaises(StaleJobRecordError) as ctx:
            self._resume()
        self.assertIn("resume --abandon garbage", ctx.exception.message)

        other = _build_target(self.tmp_root / "other", phase="PLANNING")
        replaced = self._abandon("garbage", managed_repo=other)
        self.assertEqual((jobs_dir / "abandoned" / "garbage.json").read_bytes(), original)
        self.assertEqual(replaced["job_id"], "garbage")
        self.assertIsNone(replaced["target_repo"])
        self.assertEqual(replaced["status"], job.STATUS_FAILED)
        self.assertEqual(replaced["reconciliation_evidence"],
                         {"code": "OperatorAbandoned", "original": "jobs/abandoned/garbage.json"})
        self.assertEqual(json.loads((jobs_dir / "garbage.json").read_text()), replaced)

        for managed_repo in (self.managed_repo, other):
            self.assertEqual(self._resume(managed_repo), [])
            self._step(managed_repo)
        self.assertEqual(self._launches(), 2)

    def test_an_unreadable_file_is_named_for_clearing_by_hand_everywhere(self) -> None:
        """A regular job file whose bytes cannot be read: ``--abandon``
        cannot set them aside, so the pending refusal, ``resume`` and
        ``--abandon`` each name the manual disposition, and ``--abandon``
        refuses cleanly (``JobAbandonRefusedError``, exit 20) instead of
        raising ``PermissionError``."""
        _write_record(self.runtime_root, self._launched("j-locked"))
        path = self.runtime_root / "jobs" / "j-locked.json"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        try:
            path.read_bytes()
        except PermissionError:
            pass
        else:
            self.skipTest("running as root: chmod 000 does not make a file unreadable")
        clearing = f"make {path} readable again, or remove it by hand"

        with self.assertRaises(PendingJobReconciliationError) as ctx:
            self._step()
        self.assertIn(clearing, ctx.exception.message)
        self.assertNotIn("--abandon j-locked", ctx.exception.message)
        self.assertEqual(ctx.exception.evidence["pending_jobs"],
                         [{"job_id": "j-locked", "status": None, "clearing_command": clearing}])
        with self.assertRaises(StaleJobRecordError) as ctx:
            self._resume()
        self.assertIn(clearing, ctx.exception.message)
        self.assertNotIn("--abandon j-locked", ctx.exception.message)
        with self.assertRaises(JobAbandonRefusedError) as ctx:
            self._abandon("j-locked")
        self.assertIn(clearing, ctx.exception.message)
        self.assertFalse((self.runtime_root / "jobs" / "abandoned").exists())
        self.assertEqual(self._launches(), 0)

        # Made readable again, it is an ordinary pending record.
        path.chmod(0o600)
        self.assertEqual([r["status"] for r in self._resume()], [job.STATUS_INTERRUPTED])

    def test_an_unknown_schema_version_is_replaced(self) -> None:
        _write_record(self.runtime_root, self._launched("j-schema", schema_version=99))
        original = self._bytes("j-schema")
        replaced = self._abandon("j-schema")
        self.assertEqual((self.runtime_root / "jobs" / "abandoned" / "j-schema.json").read_bytes(), original)
        self.assertIsNone(replaced["target_repo"])

    def test_a_newer_generation_record_is_refused_and_named_as_that_generations(self) -> None:
        _write_record(self.runtime_root, self._launched("j-newer", controller_generation=99))
        before = self._bytes("j-newer")
        with self.assertRaises(PendingJobReconciliationError) as ctx:
            self._step()
        self.assertIn("Controller generation 99", ctx.exception.message)
        self.assertNotIn("--abandon", ctx.exception.message)
        with self.assertRaises(StaleJobRecordError) as ctx:
            self._resume()
        self.assertIn("Controller generation 99", ctx.exception.message)
        self.assertNotIn("--abandon", ctx.exception.message)
        with self.assertRaises(JobAbandonRefusedError) as ctx:
            self._abandon("j-newer")
        self.assertIn("generation 99", ctx.exception.message)
        self.assertEqual(self._bytes("j-newer"), before)

    def _runtime_snapshot(self) -> dict:
        snapshot = {}
        for path in sorted(self.runtime_root.rglob("*")):
            rel = str(path.relative_to(self.runtime_root))
            if path.is_symlink():
                snapshot[rel] = ("link", os.readlink(path))
            elif path.is_file():
                snapshot[rel] = ("file", path.read_bytes())
            else:
                snapshot[rel] = ("dir", None)
        return snapshot

    def test_the_job_id_constraint_refuses_before_anything_is_read_or_written(self) -> None:
        (self.runtime_root / "identity.json").write_text('{"schema_version": 1}\n')
        (self.runtime_root / "handoff.json").write_text('{"pending": true}\n')
        _write_record(self.runtime_root, self._launched("real"))
        abandoned_dir = self.runtime_root / "jobs" / "abandoned"
        abandoned_dir.mkdir(parents=True)
        (abandoned_dir / "old.json").write_text("{not json")
        (self.runtime_root / "jobs" / "link.json").symlink_to(self.runtime_root / "identity.json")
        before = self._runtime_snapshot()
        for job_id in ("../identity", "../handoff", "abandoned/old", str(self.runtime_root / "jobs" / "real"),
                       "/etc/passwd", ".", "..", "", "no-such-stem", "link", "real\0x"):
            with self.subTest(job_id=job_id):
                with self.assertRaises(JobAbandonRefusedError) as ctx:
                    self._abandon(job_id)
                self.assertIn("JOB_ID must name a pending job file directly", ctx.exception.message)
                self.assertIn("real", ctx.exception.evidence["pending_job_ids"])
                self.assertEqual(self._runtime_snapshot(), before)


class ZombieWorkerResumeTest(_DispositionCase):
    """Round 4, I1: a recorded group made only of unreaped zombies is not
    running. It never makes a record ``active``: ``resume`` reconciles it
    and ``--abandon`` accepts it with no flag."""

    def test_an_unreaped_zombie_leader_and_a_zombie_only_group(self) -> None:
        for mode in ("leader", "member"):
            with self.subTest(mode=mode):
                group = process_fixtures.ZombieGroup(self, mode)
                info = group.info
                zombie_pid = info.get("member_pid", info["pid"])
                self.assertEqual(process_fixtures.read_stat(zombie_pid)[0], "Z")
                os.killpg(info["pgid"], 0)  # the premise: killpg succeeds on a zombie group
                worker_process = process_fixtures.worker_process_dict(info["pid"], start_ticks=info["start_ticks"])
                self.assertEqual(worker.classify_worker_liveness(worker_process), worker.INACTIVE)
                _write_record(self.runtime_root, self._with_worker(f"z-{mode}", worker_process))
                _write_record(self.runtime_root, self._with_worker(f"z-{mode}-copy", worker_process))
                abandoned = self._abandon(f"z-{mode}-copy")
                self.assertEqual(abandoned["worker_liveness"]["verdict"], worker.INACTIVE)
                self.assertFalse(abandoned["worker_liveness"]["acknowledged"])
                results = {r["job_id"]: r for r in self._resume()}
                self.assertEqual(results[f"z-{mode}"]["status"], job.STATUS_INTERRUPTED)
                self.assertNotIn("resume_marked", results[f"z-{mode}"])


class BootKeyedLivenessResumeTest(_DispositionCase):
    """Round 3, I1: the verdict is keyed on ``boot_id`` first. The recorded
    group is a real, live, test-owned sleeper, so every case whose verdict
    must not consult it proves that it does not."""

    NAMESPACE = worker.read_process_context()["pid_namespace"]
    HERE = {"boot_id": "boot-now", "pid_namespace": NAMESPACE, "hostname": "host-a", "machine_id": "m-a"}

    def _record_for(self, job_id: str, pid: int, **recorded_context) -> dict:
        context = {**self.HERE, **recorded_context}
        return self._with_worker(job_id, process_fixtures.worker_process_dict(pid, context=context))

    def _now(self, **current):
        return unittest.mock.patch.object(worker, "read_process_context", return_value={**self.HERE, **current})

    def test_another_boot_of_the_same_host_reconciles_with_no_operator_step(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        _write_record(self.runtime_root, self._record_for("j-reboot", sleeper.pid, boot_id="boot-old"))
        _write_record(self.runtime_root, self._record_for("j-reboot-copy", sleeper.pid, boot_id="boot-old"))
        with self._now():
            abandoned = self._abandon("j-reboot-copy")
            results = {r["job_id"]: r for r in self._resume()}
        self.assertEqual(abandoned["worker_liveness"]["verdict"], worker.INACTIVE)
        self.assertEqual(results["j-reboot"]["status"], job.STATUS_INTERRUPTED)
        self.assertIsNone(sleeper.poll())

    def test_an_unreadable_boot_id_runs_the_process_test(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        gone = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        gone.wait(timeout=10)
        for label, recorded, current in (("recorded null", {"boot_id": None}, {}),
                                         ("unreadable now", {}, {"boot_id": None})):
            with self.subTest(case=label):
                for path in (self.runtime_root / "jobs").glob("*.json"):
                    path.unlink()
                _write_record(self.runtime_root, self._record_for("j-dead", gone.pid, **recorded))
                _write_record(self.runtime_root, self._record_for("j-live", sleeper.pid, **recorded))
                with self._now(**current):
                    results = {r["job_id"]: r for r in self._resume()}
                    self.assertEqual(results["j-dead"]["status"], job.STATUS_INTERRUPTED)
                    held = results["j-live"]
                    self.assertEqual(held["status"], job.STATUS_LAUNCHED)
                    self.assertEqual(held["resume_marked"]["outcome"], job.RESUME_WORKER_UNVERIFIABLE)
                    self.assertNotIn("reconciled_this_call", held)
                    self.assertNotIn(str(sleeper.pid), held["resume_marked"]["reason"])
                    self.assertEqual(_read_record(self.runtime_root, "j-live")["status"], job.STATUS_LAUNCHED)
                    with self.assertRaises(LifecycleWorkerUnverifiableError):
                        self._abandon("j-live")
                    abandoned = self._abandon("j-live", acknowledge=True)
                self.assertEqual(abandoned["status"], job.STATUS_FAILED)
                self.assertTrue(abandoned["worker_liveness"]["acknowledged"])

    def test_another_host_is_unverifiable_and_needs_the_acknowledgement(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        _write_record(self.runtime_root, self._record_for(
            "j-host", sleeper.pid, boot_id="boot-there", hostname="host-b", machine_id="m-b"))
        with self._now():
            [held] = self._resume()
            reason = held["resume_marked"]["reason"]
            self.assertEqual(held["resume_marked"]["outcome"], job.RESUME_WORKER_UNVERIFIABLE)
            for fragment in ("hostname='host-b'", "hostname='host-a'", "machine_id='m-b'", "machine_id='m-a'",
                             "boot_id='boot-there'", "boot_id='boot-now'",
                             f"resume --abandon j-host --acknowledge-unverifiable-worker {self.root}"):
                self.assertIn(fragment, reason)
            self.assertNotIn(str(sleeper.pid), reason)
            with self.assertRaises(LifecycleWorkerUnverifiableError) as ctx:
                self._abandon("j-host")
            self.assertIn("--acknowledge-unverifiable-worker", ctx.exception.message)
            self.assertNotIn(str(sleeper.pid), ctx.exception.message)
            abandoned = self._abandon("j-host", acknowledge=True)
        self.assertEqual(abandoned["status"], job.STATUS_FAILED)
        self.assertEqual(abandoned["reconciliation_evidence"]["code"], "OperatorAbandoned")
        liveness = abandoned["worker_liveness"]
        self.assertEqual(liveness["verdict"], worker.UNVERIFIABLE)
        self.assertEqual(liveness["recorded"]["hostname"], "host-b")
        self.assertEqual(liveness["current"]["hostname"], "host-a")
        self._step()
        self.assertEqual(self._launches(), 1)

    def test_a_host_is_its_hostname_and_machine_id_together(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        for label, recorded, current in (
            ("different machine id", {"boot_id": "boot-old", "machine_id": "m-b"}, {}),
            ("machine id recorded null", {"boot_id": "boot-old", "machine_id": None}, {}),
            ("machine id unreadable now", {"boot_id": "boot-old"}, {"machine_id": None}),
        ):
            with self.subTest(case=label):
                for path in (self.runtime_root / "jobs").glob("*.json"):
                    path.unlink()
                _write_record(self.runtime_root, self._record_for("j-m", sleeper.pid, **recorded))
                with self._now(**current):
                    self.assertEqual(worker.classify_worker_liveness(
                        _read_record(self.runtime_root, "j-m")["worker_process"]), worker.UNVERIFIABLE)
                    [held] = self._resume()
                    self.assertEqual(held["resume_marked"]["outcome"], job.RESUME_WORKER_UNVERIFIABLE)
                    with self.assertRaises(LifecycleWorkerUnverifiableError):
                        self._abandon("j-m")


class NoKillAdviceAcrossBootsTest(_DispositionCase):
    """The lock-acquisition exit-45 message names the recorded pgid only
    for an ``active`` verdict -- from ``step``, ``resume`` and ``--abandon``
    alike. After a reboot the recorded number may name an unrelated group."""

    def _refusals(self) -> list[str]:
        messages = []
        held = lock.acquire_lifecycle_lock(self.root)
        try:
            for call in (self._step, self._resume, lambda: self._abandon("j-recorded")):
                with self.assertRaises(LifecycleWorkerActiveError) as ctx:
                    call()
                messages.append(ctx.exception.message)
        finally:
            held.release()
        return messages

    def test_a_changed_boot_names_no_pgid_and_an_active_worker_does(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        here = worker.read_process_context()
        _write_record(self.runtime_root, self._with_worker(
            "j-recorded", process_fixtures.worker_process_dict(sleeper.pid, context={**here, "boot_id": "boot-old"})))
        for message in self._refusals():
            self.assertNotIn(str(sleeper.pid), message)
            self.assertIn("is not named", message)
            self.assertIn("holds the lifecycle lock", message)
        _write_record(self.runtime_root, self._with_worker(
            "j-recorded", process_fixtures.worker_process_dict(sleeper.pid)))
        for message in self._refusals():
            self.assertIn(f"process group {sleeper.pid}", message)


class ResumeLockScopeTest(_DispositionCase):
    """``resume`` takes no lock on a root that no longer resolves (the
    ``Case2UnresolvableSubjectTest`` pair, unchanged), and a root that
    exists but is no longer a git repository is an error, with nothing
    reconciled."""

    def test_a_root_that_is_no_longer_a_git_repository_is_exit_20_with_nothing_reconciled(self) -> None:
        _write_record(self.runtime_root, self._launched("j1"))
        before = self._bytes("j1")
        shutil.rmtree(self.root / ".git")
        with self.assertRaises(GitDirectoryUnresolvableError):
            self._resume()
        with self.assertRaises(GitDirectoryUnresolvableError):
            self._abandon("j1")
        self.assertEqual(self._bytes("j1"), before)



# ---------------------------------------------------------------------------
# Release-runtime-observability CP5: `resume` and `--abandon` continue the
# job's event sequence from the record's `event_seq`, never reading the log,
# and nothing that decides reads `runs/` or `jobs/<id>/`.
# ---------------------------------------------------------------------------


def _events(runtime_root: Path, job_id: str) -> list[dict]:
    path = runtime_root / "jobs" / job_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class ReconciliationEventTest(_DispositionCase):
    def test_a_planned_record_without_event_seq_reconciles_as_seq_one(self) -> None:
        _write_record(self.runtime_root, _record(job_id="j1", target_repo=str(self.root),
                                                 status=job.STATUS_PLANNED, phase="PLANNING"))
        [result] = self._resume()
        self.assertEqual(result["status"], job.STATUS_INTERRUPTED)
        [event] = _events(self.runtime_root, "j1")
        self.assertEqual({k: event[k] for k in ("v", "seq", "job_id", "event", "status", "code")},
                         {"v": 1, "seq": 1, "job_id": "j1", "event": "reconciled",
                          "status": job.STATUS_INTERRUPTED, "code": None})
        self.assertEqual(_read_record(self.runtime_root, "j1")["event_seq"], 1)

    def test_a_launched_record_continues_from_its_event_seq(self) -> None:
        _write_record(self.runtime_root, self._launched("j1", event_seq=4))
        [result] = self._resume()
        self.assertEqual(result["status"], job.STATUS_INTERRUPTED)
        self.assertEqual([(e["seq"], e["event"], e["status"]) for e in _events(self.runtime_root, "j1")],
                         [(5, "reconciled", job.STATUS_INTERRUPTED)])
        self.assertEqual(_read_record(self.runtime_root, "j1")["event_seq"], 5)

    def test_an_unreconcilable_orphan_marked_failed_records_its_code(self) -> None:
        _write_record(self.runtime_root, self._launched(
            "j1", event_seq=2, lifecycle_lock={"path": str(lock.resolve_git_dir(self.root))}))
        (self.root / "moved.txt").write_text("x\n")
        fixtures.commit_all(self.root, "move HEAD")
        _set_target_phase(self.root, "wi-1", "AWAITING_LOCAL_PLAN_REVIEW")
        with self.assertRaises(UnreconcilableJobError):
            self._resume()
        [event] = _events(self.runtime_root, "j1")
        self.assertEqual((event["seq"], event["status"], event["code"]),
                         (3, job.STATUS_FAILED, "UnreconcilableJobError"))

    def test_a_malformed_event_seq_restarts_at_one_and_never_raises(self) -> None:
        for job_id, value in (("j-str", "3"), ("j-neg", -2), ("j-bool", True), ("j-list", [1])):
            with self.subTest(event_seq=value):
                _write_record(self.runtime_root, self._launched(job_id, event_seq=value))
        results = self._resume()
        self.assertEqual({r["status"] for r in results}, {job.STATUS_INTERRUPTED})
        for job_id in ("j-str", "j-neg", "j-bool", "j-list"):
            self.assertEqual([e["seq"] for e in _events(self.runtime_root, job_id)], [1])

    def test_abandon_marking_failed_continues_the_sequence(self) -> None:
        _write_record(self.runtime_root, self._launched("j1", event_seq=3))
        record = self._abandon("j1")
        self.assertEqual(record["event_seq"], 4)
        [event] = _events(self.runtime_root, "j1")
        self.assertEqual((event["seq"], event["event"], event["abandoned_status"]),
                         (4, "abandoned", job.STATUS_LAUNCHED))

    def test_abandon_replacing_carries_only_a_valid_event_seq(self) -> None:
        jobs_dir = self.runtime_root / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        cases = {
            "unparseable": ("{", 1),
            "unknown-schema": (json.dumps({"schema_version": 99, "event_seq": 7}), 8),
            "bad-seq": (json.dumps({"schema_version": 99, "event_seq": True}), 1),
        }
        for job_id, (text, expected) in cases.items():
            with self.subTest(job_id=job_id):
                (jobs_dir / f"{job_id}.json").write_text(text)
                record = self._abandon(job_id)
                self.assertEqual(record["event_seq"], expected)
                [event] = _events(self.runtime_root, job_id)
                self.assertEqual((event["seq"], event["event"]), (expected, "abandoned"))
                self.assertEqual(event["original"], record["reconciliation_evidence"]["original"])


_CHILD_ABANDON = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pathlib import Path; from controller import job; "
    "from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT; "
    "ident = ControllerIdentity(generation=7, source_root=Path('/fake'), "
    "origin_source_root=Path('/fake'), source_kind=SOURCE_KIND_COMMIT, "
    "source_commit='a'*40, tree_digest='b'*64, generation_source='head', "
    "pinned_at='2024-01-01T00:00:00Z'); "
    "from controller.managed_repo import ManagedRepository; "
    "mr = ManagedRepository(root=Path(sys.argv[2]), manifest={}, "
    "workflow_version='2.3.1', profile='full', "
    "verify={'returncode': 0, 'stdout': '', 'stderr': ''}, "
    "status={'returncode': 0, 'stdout': '', 'stderr': ''}); "
    "job.abandon(mr, identity=ident, runtime=Path(sys.argv[3]), job_id=sys.argv[4])"
)


class CrossProcessEventSeqTest(_OrphanWorkerCase):
    """``execute_step`` is killed in one process, ``resume`` reconciles its
    job in a second, and ``--abandon`` disposes of a second orphan in a
    third: each job's log is ``seq`` ``1..n`` with no repeat, continuing
    from the record's ``event_seq``."""

    def _orphan_then_wait(self, managed_repo) -> dict:
        record = self._orphan_worker(managed_repo.root)
        self.release.touch()
        self._await_worker_gone(record, managed_repo.root)
        self.release.unlink()
        return record

    def test_seq_continues_across_processes(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        first = self._orphan_then_wait(managed_repo)
        self.assertEqual(first["event_seq"], 3)
        [result] = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(result["status"], job.STATUS_INTERRUPTED)

        second = self._orphan_then_wait(managed_repo)
        subprocess.run([sys.executable, "-c", _CHILD_ABANDON, str(fixtures.REPO_ROOT), str(managed_repo.root),
                        str(self.runtime_root), second["job_id"]], check=True, timeout=60)

        for record, last in ((first, "reconciled"), (second, "abandoned")):
            log = _events(self.runtime_root, record["job_id"])
            self.assertEqual([e["event"] for e in log], ["planned", "launched", "worker_spawned", last])
            self.assertEqual([e["seq"] for e in log], [1, 2, 3, 4])
            self.assertEqual(_read_record(self.runtime_root, record["job_id"])["event_seq"], 4)
        self.assertEqual(_read_record(self.runtime_root, second["job_id"])["status"], job.STATUS_FAILED)


class ObservationPathsNeverReadTest(_DispositionCase):
    """``resume``, ``pending_reconciliation_jobs``, ``_classify_jobs`` and
    ``abandon`` never read anything under ``runs/`` or ``jobs/<id>/`` --
    asserted by a spy on every read path, with both trees populated."""

    def _populate_observation_trees(self, job_ids: list[str]) -> None:
        for job_id in job_ids:
            events = self.runtime_root / "jobs" / job_id / "events.jsonl"
            events.parent.mkdir(parents=True, exist_ok=True)
            events.write_text('{"v": 1, "seq": 99}\n')
        runs = self.runtime_root / "runs"
        (runs / "r1").mkdir(parents=True)
        (runs / "r1.json").write_text('{"state": "running"}\n')
        (runs / "r1" / "events.jsonl").write_text('{"v": 1, "seq": 1}\n')

    def _spied_reads(self):
        reads: list[str] = []
        real_open, real_os_open = open, os.open
        real_read_bytes, real_read_text, real_path_open = Path.read_bytes, Path.read_text, Path.open

        def spy_open(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and not any(c in mode for c in "wax+"):
                reads.append(str(file))
            return real_open(file, mode, *args, **kwargs)

        def spy_os_open(path, flags, *args, **kwargs):
            if not flags & (os.O_WRONLY | os.O_RDWR):
                reads.append(str(path))
            return real_os_open(path, flags, *args, **kwargs)

        def spy_read_bytes(self_path):
            reads.append(str(self_path))
            return real_read_bytes(self_path)

        def spy_read_text(self_path, *args, **kwargs):
            reads.append(str(self_path))
            return real_read_text(self_path, *args, **kwargs)

        def spy_path_open(self_path, mode="r", *args, **kwargs):
            if not any(c in mode for c in "wax+"):
                reads.append(str(self_path))
            return real_path_open(self_path, mode, *args, **kwargs)

        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch("builtins.open", spy_open))
        stack.enter_context(unittest.mock.patch("os.open", spy_os_open))
        stack.enter_context(unittest.mock.patch.object(Path, "read_bytes", spy_read_bytes))
        stack.enter_context(unittest.mock.patch.object(Path, "read_text", spy_read_text))
        stack.enter_context(unittest.mock.patch.object(Path, "open", spy_path_open))
        return stack, reads

    def _observation_reads(self, reads: list[str]) -> list[str]:
        runtime_root = self.runtime_root.resolve()
        hits = []
        for path in reads:
            try:
                parts = Path(path).resolve().relative_to(runtime_root).parts
            except ValueError:
                continue
            if parts[0] == "runs" or (parts[0] == "jobs" and len(parts) > 2 and parts[1] != "abandoned"):
                hits.append(path)
        return hits

    def test_no_decision_reads_under_runs_or_a_job_directory(self) -> None:
        _write_record(self.runtime_root, self._launched("j1", event_seq=2))
        _write_record(self.runtime_root, self._launched("j2", event_seq=2))
        _write_record(self.runtime_root, _record(job_id="j3", target_repo=str(self.root),
                                                 status=job.STATUS_FINISHED, phase="PLANNING"))
        self._populate_observation_trees(["j1", "j2", "j3"])
        calls = {
            "pending_reconciliation_jobs":
                lambda: job.pending_reconciliation_jobs(self.runtime_root, self.managed_repo, FAKE_IDENTITY),
            "_classify_jobs": lambda: cli._classify_jobs(self.runtime_root),
            "abandon": lambda: self._abandon("j2"),
            "resume": self._resume,
        }
        for name, call in calls.items():
            with self.subTest(call=name):
                stack, reads = self._spied_reads()
                with stack:
                    call()
                self.assertTrue(reads, "the spy saw no read at all")
                self.assertEqual(self._observation_reads(reads), [])
        self.assertEqual(_events(self.runtime_root, "j1")[-1]["seq"], 3, "continued from the record, not the log")


if __name__ == "__main__":
    unittest.main()
