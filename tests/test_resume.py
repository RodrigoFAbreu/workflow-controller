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

import shutil
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job  # noqa: E402
from controller import runtime as runtime_module  # noqa: E402
from controller.errors import StaleJobRecordError, UnreconcilableJobError  # noqa: E402
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT  # noqa: E402
from tests import fixtures  # noqa: E402

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
        "bundle_id": None, "bundle_manifest_readable": False, "bundle_manifest_generation_head": None,
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

    def test_completed_with_predicate_row_unsatisfied_reconciles_to_failed(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.1",
            current_bundle_id="b" * 64,
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=fixtures.current_head(root)),
        )
        record = _record(
            job_id="j1", target_repo=str(root), status=job.STATUS_COMPLETED,
            phase="AWAITING_LOCAL_PLAN_REVIEW", command="/review-plan", worker_outcome="SUCCESS",
            pre_state_overrides={"bundle_id": "b" * 64, "bundle_manifest_readable": True},
        )
        _write_record(self.runtime_root, record)
        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(results[0]["status"], job.STATUS_FAILED)
        self.assertEqual(results[0]["reconciliation_evidence"]["reason"], "predicate_not_satisfied")

    def test_completed_that_actually_succeeded_reconciles_to_finished(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
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
# End-to-end interruption test: a real worker, SIGKILLed mid-run, then
# resume.
# ---------------------------------------------------------------------------


class EndToEndInterruptionTest(_ResumeTestCase):
    def test_a_worker_killed_mid_run_leaves_a_launched_record_that_resume_reconciles(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        fixtures.copy_real_commands_dir(managed_repo.root / ".claude" / "commands")

        proc = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys, subprocess; sys.path.insert(0, sys.argv[1]); "
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
                "job.execute_step(mr, identity=ident, runtime=Path(sys.argv[3]), "
                "claude_bin=sys.argv[4], timeout=3600)",
                str(fixtures.REPO_ROOT), str(managed_repo.root), str(self.runtime_root),
                str(FAKE_CLAUDE),
            ],
            env={**__import__("os").environ, "FAKE_CLAUDE_HANG": "1"},
        )
        try:
            jobs_dir = self.runtime_root / "jobs"
            for _ in range(200):
                if jobs_dir.is_dir() and any(jobs_dir.glob("*.json")):
                    break
                import time
                time.sleep(0.05)
            self.assertTrue(jobs_dir.is_dir() and any(jobs_dir.glob("*.json")), "no job record appeared")
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        job_files = list(jobs_dir.glob("*.json"))
        self.assertEqual(len(job_files), 1)
        on_disk = runtime_module.read_json(job_files[0])
        self.assertEqual(on_disk["status"], job.STATUS_LAUNCHED)

        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)


class BootstrapEndToEndInterruptionTest(_ResumeTestCase):
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

        proc = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys, subprocess; sys.path.insert(0, sys.argv[1]); "
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
                "job.execute_step(mr, identity=ident, runtime=Path(sys.argv[3]), "
                "claude_bin=sys.argv[4], timeout=3600)",
                str(fixtures.REPO_ROOT), str(root), str(self.runtime_root), str(FAKE_CLAUDE),
            ],
            env={**__import__("os").environ, "FAKE_CLAUDE_HANG": "1"},
        )
        try:
            jobs_dir = self.runtime_root / "jobs"
            for _ in range(200):
                if jobs_dir.is_dir() and any(jobs_dir.glob("*.json")):
                    break
                import time
                time.sleep(0.05)
            self.assertTrue(jobs_dir.is_dir() and any(jobs_dir.glob("*.json")), "no job record appeared")
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        job_files = list(jobs_dir.glob("*.json"))
        self.assertEqual(len(job_files), 1)
        on_disk = runtime_module.read_json(job_files[0])
        self.assertEqual(on_disk["status"], job.STATUS_LAUNCHED)
        self.assertIsNone(on_disk["work_item_id"])
        self.assertEqual(on_disk["expected_transition"]["from"], "__NO_PHASE__")

        results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], job.STATUS_INTERRUPTED)


if __name__ == "__main__":
    unittest.main()
