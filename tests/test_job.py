"""Tests for job execution, part 1 (CP6, ``controller.job``).

Covers exactly what CP6 itself owns -- steps 1-6 of ``execute_step``: the
no-action class returning a bare ``Decision`` with no job record written;
the positive launch guard (``GATE_BLOCKED``/``DECLINED``/
``HANDOFF_PENDING`` never spawn a worker); the two-flush
``PLANNED``/``LAUNCHED`` write, durable *before* the worker is spawned;
the ``PLANNED``-flush record content, asserted two-sidedly; and the final
``COMPLETED`` record's content, including the identity block and the
captured worker result.

CP6B's own post-state verification (``FINISHED``/``FAILED``/
``INCOMPLETE``, ``transition_verified``, ``observed_phase_after``) is out
of this checkpoint's scope and is not asserted here.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job  # noqa: E402
from controller.decision import Decision  # noqa: E402
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT, SOURCE_KIND_WORKTREE  # noqa: E402
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

FAKE_WORKTREE_IDENTITY = ControllerIdentity(
    generation=7,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_WORKTREE,
    source_commit=None,
    tree_digest="c" * 64,
    generation_source="worktree",
    pinned_at="2024-01-01T00:00:00Z",
)


def _build_target(
    tmp_root: Path, *, phase: str, governing_workflow_version: str | None = "2.1",
    work_item_id: str = "wi-1", **work_item_overrides,
):
    root = tmp_root / "target"
    fixtures.build_target_git_repo(root)
    (root / "README.md").write_text("target fixture\n")
    fixtures.commit_all(root, "initial")

    entry = {
        "work_item_type": "product",
        "work_item_kind": "product",
        "work_item_id": work_item_id,
        "governing_workflow_version": governing_workflow_version,
        "phase": phase,
        "plan_revision": 1,
        "implementation_revision": None,
        "state_revision": 1,
        "checkpoints": {},
        "current_bundle_id": None,
        "last_completed_checkpoint_id": None,
        "base_commit": fixtures.current_head(root),
        "parent_work_item_id": None,
    }
    entry.update(work_item_overrides)
    state = {
        "schema_version": 1,
        "active_work_item_id": work_item_id,
        "work_items": {work_item_id: entry},
    }
    fixtures.write_workflow_state(root, state)
    return fixtures.build_target_managed_repository(root)


class _WriteSpy:
    """Records every ``(rel_path, obj)`` pair passed to
    ``controller.job.runtime.write_json`` while installed, and still
    performs the real write -- CP6's own "spy on the single durable-write
    path" technique, matching CP1's own containment guard."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._original = job.runtime.write_json

    def __enter__(self) -> "_WriteSpy":
        def spy(runtime_root, rel_path, obj):
            self.calls.append((str(rel_path), copy.deepcopy(obj)))
            return self._original(runtime_root, rel_path, obj)

        job.runtime.write_json = spy
        return self

    def __exit__(self, *exc) -> None:
        job.runtime.write_json = self._original

    def statuses(self) -> list[str]:
        return [obj.get("status") for _rel, obj in self.calls if "jobs/" in _rel]


class NoActionClassTest(unittest.TestCase):
    """LEGACY_READY / MILESTONE_COMPLETE: ``execute_step`` returns the bare
    ``Decision``, writes no job record, and leaves the jobs directory
    unchanged."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)

    def _assert_no_record(self, phase: str) -> None:
        managed_repo = _build_target(self.tmp_root / phase, phase=phase, governing_workflow_version=None)
        result = job.execute_step(
            managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
        )
        self.assertIsInstance(result, Decision)
        self.assertEqual(result.observed_phase, phase)
        jobs_dir = self.runtime_root / "jobs"
        self.assertFalse(jobs_dir.is_dir() and any(jobs_dir.iterdir()))

    def test_legacy_ready_returns_decision_no_record(self) -> None:
        self._assert_no_record("LEGACY_READY")

    def test_milestone_complete_returns_decision_no_record(self) -> None:
        self._assert_no_record("MILESTONE_COMPLETE")


class NoLaunchRecordTest(unittest.TestCase):
    """The positive launch guard: a manual-gate phase, a declined
    report-only phase, and a pending handoff each write a single-flush
    record and spawn no worker process at all."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)

    def _diag_path(self) -> Path:
        return self.tmp_root / "diag.json"

    def _no_process_spawned(self) -> None:
        self.assertFalse(self._diag_path().exists())

    def test_gate_phase_writes_gate_blocked_no_worker_spawned(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="APPLYING_REVIEW_FEEDBACK")
        import os
        old = os.environ.get("FAKE_CLAUDE_DIAG_FILE")
        os.environ["FAKE_CLAUDE_DIAG_FILE"] = str(self._diag_path())
        try:
            record = job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE),
            )
        finally:
            if old is None:
                os.environ.pop("FAKE_CLAUDE_DIAG_FILE", None)
            else:
                os.environ["FAKE_CLAUDE_DIAG_FILE"] = old
        self.assertIsInstance(record, dict)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertIsNotNone(record["human_gate_pending"])
        self.assertEqual(record["human_gate_pending"]["phase"], "APPLYING_REVIEW_FEEDBACK")
        self.assertFalse(record["handoff_pending"])
        self.assertNotIn("worker", record)
        self._no_process_spawned()

    def test_declined_phase_writes_declined_no_worker_spawned(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="IMPLEMENTING")
        import os
        old = os.environ.get("FAKE_CLAUDE_DIAG_FILE")
        os.environ["FAKE_CLAUDE_DIAG_FILE"] = str(self._diag_path())
        try:
            record = job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE),
            )
        finally:
            if old is None:
                os.environ.pop("FAKE_CLAUDE_DIAG_FILE", None)
            else:
                os.environ["FAKE_CLAUDE_DIAG_FILE"] = old
        self.assertEqual(record["status"], job.STATUS_DECLINED)
        self.assertIsNone(record["human_gate_pending"])
        self.assertFalse(record["handoff_pending"])
        self.assertTrue(record["selected_action"]["declined"])
        self.assertIsNotNone(record["selected_action"]["command"])
        self.assertNotIn("worker", record)
        self._no_process_spawned()

    def test_pending_handoff_writes_handoff_pending_no_worker_spawned(self) -> None:
        # PLANNING is otherwise automatic -- the handoff check must still
        # win over launching.
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        (self.runtime_root / "handoff.json").write_text(
            json.dumps({"schema_version": 1, "reason": "generation handoff pending"}) + "\n"
        )
        import os
        old = os.environ.get("FAKE_CLAUDE_DIAG_FILE")
        os.environ["FAKE_CLAUDE_DIAG_FILE"] = str(self._diag_path())
        try:
            record = job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE),
            )
        finally:
            if old is None:
                os.environ.pop("FAKE_CLAUDE_DIAG_FILE", None)
            else:
                os.environ["FAKE_CLAUDE_DIAG_FILE"] = old
        self.assertEqual(record["status"], job.STATUS_HANDOFF_PENDING)
        self.assertTrue(record["handoff_pending"])
        self.assertIsNone(record["human_gate_pending"])
        self.assertTrue(record["selected_action"]["automatic"])
        self._no_process_spawned()


class LaunchPathTest(unittest.TestCase):
    """The automatic-launch path: PLANNING ("2.1") -> /milestone-plan,
    which needs no ``.ai-review/`` evidence read at all -- the minimal
    fixture that reaches step 4."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)
        self.managed_repo = _build_target(
            self.tmp_root, phase="PLANNING", governing_workflow_version="2.1",
        )
        self._orig_job_id = job._new_job_id
        job._new_job_id = lambda: "fixed-job-id"
        self.addCleanup(setattr, job, "_new_job_id", self._orig_job_id)

    def test_launched_record_present_on_disk_before_worker_starts(self) -> None:
        expected_record_path = self.runtime_root / "jobs" / "fixed-job-id.json"
        import os
        env = {"FAKE_CLAUDE_REQUIRE_FILE": str(expected_record_path)}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            record = job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        # If the record were not durable before the worker ran,
        # fake_claude.py would have exited 91 and the outcome would be
        # FAILURE rather than SUCCESS.
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["status"], job.STATUS_COMPLETED)

    def test_write_sequence_prefix_is_planned_launched_completed(self) -> None:
        with _WriteSpy() as spy:
            job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        statuses = spy.statuses()
        self.assertEqual(statuses[:3], ["PLANNED", "LAUNCHED", "COMPLETED"])

    def test_planned_flush_carries_and_omits_the_right_fields(self) -> None:
        with _WriteSpy() as spy:
            job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        planned = next(obj for _rel, obj in spy.calls if obj.get("status") == "PLANNED")

        self.assertEqual(planned["schema_version"], job.SCHEMA_VERSION)
        self.assertEqual(planned["controller_generation"], FAKE_IDENTITY.generation)
        self.assertEqual(planned["target_repo"], str(self.managed_repo.root))
        self.assertEqual(planned["work_item_id"], "wi-1")
        self.assertEqual(planned["status"], "PLANNED")
        self.assertIn("selected_action", planned)
        self.assertEqual(set(planned["pre_state"]), job.PRE_STATE_FIELDS)

        for absent_field in (
            "worker_outcome", "worker", "observed_phase_after", "transition_verified",
            "expected_transition",
        ):
            self.assertNotIn(absent_field, planned, f"{absent_field!r} must be absent at PLANNED")

    def test_launched_flush_adds_only_expected_transition(self) -> None:
        with _WriteSpy() as spy:
            job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        planned = next(obj for _rel, obj in spy.calls if obj.get("status") == "PLANNED")
        launched = next(obj for _rel, obj in spy.calls if obj.get("status") == "LAUNCHED")

        self.assertIn("expected_transition", launched)
        self.assertNotIn("expected_transition", planned)
        self.assertEqual(launched["expected_transition"]["from"], "PLANNING")
        self.assertEqual(
            set(launched["expected_transition"]["to_any_of"]), {"AWAITING_LOCAL_PLAN_REVIEW"},
        )
        for absent_field in ("worker_outcome", "worker", "observed_phase_after", "transition_verified"):
            self.assertNotIn(absent_field, launched)

    def test_completed_record_contains_every_schema_field_cp6_owns(self) -> None:
        record = job.execute_step(
            self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
            claude_bin=str(FAKE_CLAUDE), timeout=10,
        )
        expected_keys = {
            "schema_version", "job_id", "controller_generation", "controller_source_commit",
            "controller_source_tree_digest", "target_repo", "target_workflow_version",
            "work_item_id", "observed_phase_before", "pre_state", "selected_action",
            "expected_transition", "worker", "worker_outcome", "status", "human_gate_pending",
            "handoff_pending", "created_at", "updated_at",
        }
        self.assertEqual(set(record.keys()), expected_keys)
        self.assertEqual(record["status"], job.STATUS_COMPLETED)
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["worker"]["session_id"], "fake-session-id")
        self.assertTrue(record["worker"]["stdout_path"])
        self.assertTrue(record["worker"]["stderr_path"])
        self.assertTrue(Path(record["worker"]["stdout_path"]).is_file())
        self.assertTrue(Path(record["worker"]["stderr_path"]).is_file())

    def test_controller_source_identity_recorded_from_identity_including_worktree_none_commit(self) -> None:
        record = job.execute_step(
            self.managed_repo, identity=FAKE_WORKTREE_IDENTITY, runtime=self.runtime_root,
            claude_bin=str(FAKE_CLAUDE), timeout=10,
        )
        self.assertEqual(record["controller_source_commit"], None)
        self.assertEqual(record["controller_source_tree_digest"], FAKE_WORKTREE_IDENTITY.tree_digest)
        self.assertEqual(record["controller_generation"], FAKE_WORKTREE_IDENTITY.generation)


if __name__ == "__main__":
    unittest.main()
