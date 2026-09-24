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

import ast
import contextlib
import copy
import dataclasses
import errno
import io
import json
import os
import signal
import stat
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, decision, evidence, identity, job, lock, routing, target_state, worker  # noqa: E402
from controller.errors import (  # noqa: E402
    LifecycleWorkerActiveError,
    PendingJobReconciliationError,
    UserOnlyCommandError,
    WorkerLaunchError,
)
from controller.decision import Action, NO_PHASE, Decision, phase_from_wire  # noqa: E402
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT, SOURCE_KIND_WORKTREE  # noqa: E402
from tests import fake_claude, fixtures, process_fixtures  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
REPO_ROOT = Path(__file__).resolve().parent.parent

FAKE_IDENTITY = ControllerIdentity(
    generation=7,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_COMMIT,
    source_commit="a" * 40,
    tree_digest="b" * 64,
    generation_source="head",
    pinned_at="2024-01-01T00:00:00Z", version="1.1.1",
)

FAKE_WORKTREE_IDENTITY = ControllerIdentity(
    generation=7,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_WORKTREE,
    source_commit=None,
    tree_digest="c" * 64,
    generation_source="worktree",
    pinned_at="2024-01-01T00:00:00Z", version="1.1.1",
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

    def _execute_with_diag(self, managed_repo):
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_DIAG_FILE": str(self._diag_path())}):
            return job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE),
            )

    def test_implementing_without_a_current_plan_approval_writes_gate_blocked(self) -> None:
        """automatic-lifecycle-orchestration CP3: ``"2.1"`` ``IMPLEMENTING``
        with ``plan_approval`` absent used to be ``DECLINED``; it is now the
        plan-approval gate, because ``/milestone-implement``'s step 1a
        would refuse."""
        managed_repo = _build_target(self.tmp_root, phase="IMPLEMENTING", plan_approval=None)
        record = self._execute_with_diag(managed_repo)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        gate = record["human_gate_pending"]
        self.assertEqual(gate["phase"], "IMPLEMENTING")
        self.assertIn("step 1a", gate["what_is_required"])
        self.assertEqual(gate["safe_resume_command"], "workflow-controller explain --work-item wi-1")
        self.assertFalse(record["handoff_pending"])
        self.assertIsNone(record["selected_action"]["command"])
        self.assertNotIn("worker", record)
        self._no_process_spawned()

    def test_declined_phase_writes_declined_no_worker_spawned(self) -> None:
        """Re-pinned at ``"1"`` ``IMPLEMENTING``, which has no
        ``ExpectedOutcome`` row: the general dispatch rule declines it."""
        managed_repo = _build_target(self.tmp_root, phase="IMPLEMENTING", governing_workflow_version="1")
        record = self._execute_with_diag(managed_repo)
        self.assertEqual(record["status"], job.STATUS_DECLINED)
        self.assertIsNone(record["human_gate_pending"])
        self.assertFalse(record["handoff_pending"])
        self.assertTrue(record["selected_action"]["declined"])
        self.assertEqual(record["selected_action"]["command"], "/milestone-implement wi-1")
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


def _only_job_record(runtime_root: Path) -> dict:
    files = sorted((runtime_root / "jobs").glob("*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text())


class DispatchRuleDeclineTest(unittest.TestCase):
    """automatic-lifecycle-orchestration CP3: every selection the general
    dispatch rule declines reaches ``execute_step`` as an ordinary
    ``DECLINED`` record. At the base commit each case below raised a bare
    ``AssertionError`` from ``_expected_outcome_for``, after flushing a
    ``PLANNED`` record."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

    def _execute(self, managed_repo, runtime_root: Path):
        never = self.tmp_root / "never-created"
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_REQUIRE_FILE": str(never)}):
            return job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root, claude_bin=str(FAKE_CLAUDE),
            )

    def _assert_declined_record(self, record: dict, runtime_root: Path, *, phase: str,
                                version: str | None, command: str) -> None:
        self.assertEqual(record["status"], job.STATUS_DECLINED)
        self.assertTrue(record["selected_action"]["declined"])
        self.assertFalse(record["selected_action"]["automatic"])
        self.assertEqual(record["selected_action"]["command"], command)
        self.assertEqual(
            record["selected_action"]["reason"], decision.uniform_decline_reason(phase, version, command),
        )
        self.assertNotIn("worker", record)
        self.assertNotIn("expected_transition", record)
        # The one record on disk is the terminal DECLINED one -- no
        # PLANNED record was flushed first.
        self.assertEqual(_only_job_record(runtime_root)["status"], job.STATUS_DECLINED)

    def test_unconsumed_functional_review_findings_are_declined_not_a_crash(self) -> None:
        """The regression: ``/apply-functional-review`` has no row."""
        for version in ("1", "2.1", "2.2"):
            with self.subTest(governing_workflow_version=version):
                case_root = self.tmp_root / f"functional-{version}"
                managed_repo = _build_target(
                    case_root, phase="AWAITING_FUNCTIONAL_REVIEW", governing_workflow_version=version,
                    implementation_revision=1,
                )
                root = managed_repo.root
                (root / "docs" / "ACTIVE_MILESTONE.md").write_text("checklist content\n")
                blob = fixtures.run(
                    ["git", "hash-object", "--", "docs/ACTIVE_MILESTONE.md"], cwd=root,
                ).stdout.strip()
                fixtures.run(["git", "add", "docs/ACTIVE_MILESTONE.md"], cwd=root)
                fixtures.run([
                    "git", "commit", "-q", "-m",
                    f"checklist\n\nWorkflow-Functional-Checklist: wi-1/1/{blob}\n"
                    "Workflow-Work-Item: wi-1\n",
                ], cwd=root)
                findings = root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.md"
                findings.parent.mkdir(parents=True, exist_ok=True)
                findings.write_text("findings\n")

                runtime_root = case_root / "runtime"
                record = self._execute(managed_repo, runtime_root)
                self._assert_declined_record(
                    record, runtime_root, phase="AWAITING_FUNCTIONAL_REVIEW", version=version,
                    command="/apply-functional-review wi-1",
                )
                self.assertEqual(record["selected_action"]["evidence"],
                                 ["unconsumed FUNCTIONAL_REVIEW.md findings"])

    # -- CP3 item 6: the combinations no Workflow writer reaches.

    _LOCAL_APPROVE_LEDGER = {
        "review_content_id": "c" * 64,
        "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
    }

    def _no_evidence(self, root: Path) -> None:
        pass

    def _coherent_plan_manifest(self, root: Path) -> None:
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=fixtures.current_head(root)),
        )

    def _admissible_manual_approve(self, root: Path) -> None:
        self._coherent_plan_manifest(root)
        fixtures.write_review_feedback(root, ".ai-review/wi-1/feedback", fixtures.build_review_feedback_text(
            status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW", reviewed_bundle_id="b" * 64,
            reviewed_base_commit="0" * 40, reviewed_content_id="c" * 64,
        ))

    def _admissible_revise(self, root: Path) -> None:
        self._coherent_plan_manifest(root)
        fixtures.write_review_feedback(root, ".ai-review/wi-1/feedback", fixtures.build_review_feedback_text(
            status="REVISE", reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
        ))

    def _unreachable_cases(self):
        """``(phase, version, command, evidence setup)`` for every
        combination the plan's "Decisions that change at combinations no
        Workflow writer reaches" names: each selects an action that was
        automatic at base."""
        by_phase = {
            "PLANNING": ("/milestone-plan wi-1", self._no_evidence),
            "REVISING_PLAN": ("/apply-plan-review wi-1", self._no_evidence),
            "AWAITING_LOCAL_PLAN_REVIEW": ("/review-plan wi-1", self._coherent_plan_manifest),
            "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": ("/record-manual-plan-review wi-1",
                                                     self._admissible_manual_approve),
            "AWAITING_EXTERNAL_PLAN_REVIEW": ("/apply-plan-review wi-1", self._admissible_revise),
        }
        pairs = [
            ("REVISING_PLAN", "1"), ("AWAITING_LOCAL_PLAN_REVIEW", "1"),
            ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "1"),
            ("AWAITING_EXTERNAL_PLAN_REVIEW", "2.1"), ("AWAITING_EXTERNAL_PLAN_REVIEW", "2.2"),
        ] + [(phase, None) for phase in by_phase]
        for phase, version in pairs:
            command, setup = by_phase[phase]
            yield phase, version, command, setup

    def test_unreachable_combinations_are_declined_by_decide_and_by_execute_step(self) -> None:
        cases = list(self._unreachable_cases())
        self.assertEqual(len(cases), 10)
        for index, (phase, version, command, setup) in enumerate(cases):
            with self.subTest(phase=phase, governing_workflow_version=version):
                case_root = self.tmp_root / f"unreachable-{index}"
                managed_repo = _build_target(
                    case_root, phase=phase, governing_workflow_version=version, base_commit="0" * 40,
                    plan_review_stages=self._LOCAL_APPROVE_LEDGER,
                )
                setup(managed_repo.root)
                self.assertNotIn((phase, version, command.split()[0]), decision.AUTOMATIC_TRIPLES)

                snapshot = target_state.read(managed_repo)
                work_item = target_state.select_work_item(snapshot, work_item_id="wi-1")
                result = evidence.decide(managed_repo, snapshot, work_item)
                self.assertTrue(result.declined, result.reason)
                self.assertFalse(result.automatic)
                self.assertEqual(result.action.command, command)
                self.assertEqual(result.reason, decision.uniform_decline_reason(phase, version, command))

                runtime_root = case_root / "runtime"
                record = self._execute(managed_repo, runtime_root)
                self._assert_declined_record(record, runtime_root, phase=phase, version=version,
                                             command=command)

    def test_each_case_selects_its_action_automatically_when_a_triple_is_declared(self) -> None:
        """The same fixtures at a combination the rule does launch: the
        declines above are the rule's, not a fixture that gates."""
        reachable = {
            "PLANNING": "2.1", "REVISING_PLAN": "2.2", "AWAITING_LOCAL_PLAN_REVIEW": "2.2",
            "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": "2.1", "AWAITING_EXTERNAL_PLAN_REVIEW": "1",
        }
        seen = set()
        for index, (phase, _version, command, setup) in enumerate(self._unreachable_cases()):
            if phase in seen:
                continue
            seen.add(phase)
            with self.subTest(phase=phase):
                case_root = self.tmp_root / f"reachable-{index}"
                managed_repo = _build_target(
                    case_root, phase=phase, governing_workflow_version=reachable[phase],
                    base_commit="0" * 40, plan_review_stages=self._LOCAL_APPROVE_LEDGER,
                )
                setup(managed_repo.root)
                snapshot = target_state.read(managed_repo)
                work_item = target_state.select_work_item(snapshot, work_item_id="wi-1")
                result = evidence.decide(managed_repo, snapshot, work_item)
                self.assertTrue(result.automatic, result.reason)
                self.assertEqual(result.action.command, command)
        self.assertEqual(seen, set(reachable))


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
            with _WriteSpy() as spy:
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
        # FAILURE rather than SUCCESS. Checked against the intermediate
        # COMPLETED flush -- CP6's own slice -- since CP6B's own
        # post-state verification (this fake worker never actually
        # performs the target repository's real phase transition, so the
        # record's final status is FAILED, asserted in its own suite,
        # tests/test_job_validation.py) is out of this test's scope.
        completed = next(obj for _rel, obj in spy.calls if obj.get("status") == "COMPLETED")
        self.assertEqual(completed["worker_outcome"], "SUCCESS")
        self.assertIsInstance(record, dict)

    def test_an_ordinary_launch_is_the_bare_command_with_a_null_addendum(self) -> None:
        diag = self.tmp_root / "diag.json"
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_DIAG_FILE": str(diag)}):
            record = job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        self.assertEqual(json.loads(diag.read_text())["argv"][1], "/milestone-plan wi-1")
        self.assertIn("task_addendum", record["selected_action"])
        self.assertIsNone(record["selected_action"]["task_addendum"])

    def test_write_sequence_prefix_is_planned_launched_completed(self) -> None:
        with _WriteSpy() as spy:
            job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        statuses = spy.statuses()
        # Automatic-lifecycle-orchestration CP5: the `on_spawn` flush of
        # `worker_process` is a second LAUNCHED write -- after `Popen`, and
        # after the first LAUNCHED write, so a crash between the two leaves
        # a LAUNCHED record carrying `lifecycle_lock`.
        self.assertEqual(statuses[:4], ["PLANNED", "LAUNCHED", "LAUNCHED", "COMPLETED"])

    def test_the_second_launched_write_adds_only_the_worker_process(self) -> None:
        """CP5: the two LAUNCHED writes differ only by ``worker_process``
        (the spawn-time process identity), ``updated_at`` and
        (release-runtime-observability CP5) the incremented ``event_seq``."""
        with _WriteSpy() as spy:
            job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        launched = [obj for _rel, obj in spy.calls if obj.get("status") == "LAUNCHED"]
        self.assertEqual(len(launched), 2)
        first, second = launched
        self.assertNotIn("worker_process", first)
        self.assertEqual(
            {key for key in set(first) | set(second) if first.get(key) != second.get(key)} - {"updated_at"},
            {"worker_process", "event_seq"},
        )
        self.assertEqual(second["event_seq"], first["event_seq"] + 1)
        worker_process = second["worker_process"]
        self.assertEqual(
            set(worker_process),
            {"pid", "pgid", "start_ticks", "boot_id", "pid_namespace", "hostname", "machine_id"},
        )
        self.assertEqual(worker_process["pgid"], worker_process["pid"])
        self.assertIsInstance(worker_process["start_ticks"], int)
        self.assertEqual(first["lifecycle_lock"], second["lifecycle_lock"])

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
            "expected_transition", "worker_process",
        ):
            self.assertNotIn(absent_field, planned, f"{absent_field!r} must be absent at PLANNED")
        # CP5: written under the lock, naming the target's own git directory.
        self.assertEqual(planned["lifecycle_lock"], {"path": str((self.managed_repo.root / ".git").resolve())})

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
        # CP6B's own steps 7-9 always run after step 6's COMPLETED flush
        # (execute_step is one function spanning all nine steps), so this
        # checks the intermediate COMPLETED write via the spy -- CP6's own
        # slice -- rather than execute_step's own return value, which is
        # now the terminal FINISHED/FAILED/INCOMPLETE record.
        with _WriteSpy() as spy:
            job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        record = next(obj for _rel, obj in spy.calls if obj.get("status") == "COMPLETED")
        expected_keys = {
            "schema_version", "job_id", "controller_generation", "controller_source_commit",
            "controller_source_tree_digest", "target_repo", "target_workflow_version",
            "work_item_id", "observed_phase_before", "pre_state", "selected_action",
            "expected_transition", "worker", "worker_outcome", "status", "human_gate_pending",
            "handoff_pending", "created_at", "updated_at",
            # Automatic-lifecycle-orchestration CP5.
            "lifecycle_lock", "worker_process",
            # Automatic-lifecycle-orchestration CP6.
            "worker_route",
            # Release-runtime-observability CP2.
            "controller_runtime",
            # Release-runtime-observability CP4.
            "worker_streams",
            # Release-runtime-observability CP5.
            "event_seq",
        }
        self.assertEqual(set(record.keys()), expected_keys)
        self.assertEqual(record["status"], job.STATUS_COMPLETED)
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["worker"]["session_id"], "fake-session-id")
        self.assertTrue(record["worker"]["stdout_path"])
        self.assertTrue(record["worker"]["stderr_path"])
        self.assertTrue(Path(record["worker"]["stdout_path"]).is_file())
        self.assertTrue(Path(record["worker"]["stderr_path"]).is_file())

    def test_controller_runtime_block_carries_every_field(self) -> None:
        record = job.execute_step(
            self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
            claude_bin=str(FAKE_CLAUDE), timeout=10,
        )
        block = record["controller_runtime"]
        self.assertEqual(set(block), {
            "runtime_kind", "version", "source_kind", "source_commit", "tree_digest",
            "package_digest", "build_origin", "release_tag", "generation",
        })
        self.assertEqual(block, identity.runtime_record(FAKE_IDENTITY))
        self.assertEqual(block["tree_digest"], record["controller_source_tree_digest"])

    def test_controller_source_identity_recorded_from_identity_including_worktree_none_commit(self) -> None:
        record = job.execute_step(
            self.managed_repo, identity=FAKE_WORKTREE_IDENTITY, runtime=self.runtime_root,
            claude_bin=str(FAKE_CLAUDE), timeout=10,
        )
        self.assertEqual(record["controller_source_commit"], None)
        self.assertEqual(record["controller_source_tree_digest"], FAKE_WORKTREE_IDENTITY.tree_digest)
        self.assertEqual(record["controller_generation"], FAKE_WORKTREE_IDENTITY.generation)


class BootstrapRowSevenTest(unittest.TestCase):
    """Row 7 (revision 63's B2, ``REQ-40``): a target with **zero**
    non-terminal work items and no explicit ``--work-item`` resolves to
    ``target_state.NoWorkItemYet``, not ``AmbiguousWorkItemError``. CP6's
    own steps 1-6 must capture and durably flush a ``PLANNED``/
    ``LAUNCHED``/``COMPLETED`` record for this target, wire-mapping every
    "no phase" field through the single declared writer
    (``controller.decision.phase_to_wire``) -- never a synthetic phase
    string, never Python's own ``None``.

    CP6B's own steps 7-9 now carry this same row the rest of the way
    (``tests/test_job_validation.py`` owns those assertions --
    ``FINISHED``/``FAILED``, ``transition_verified``,
    ``observed_phase_after`` -- per this checkpoint's own plan-declared
    file split); every test below asserts only against the fields CP6's
    own steps 1-6 wrote, which steps 7-9 never overwrite, read back off
    disk -- never from process memory, since that is the whole reason the
    round-trip case reads the persisted record back off disk rather than
    the in-process value."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)
        root = self.tmp_root / "target"
        fixtures.build_target_git_repo(root)
        (root / "README.md").write_text("target fixture\n")
        fixtures.commit_all(root, "initial")
        fixtures.write_workflow_state(root, {
            "schema_version": 1, "active_work_item_id": None, "work_items": {},
        })
        self.managed_repo = fixtures.build_target_managed_repository(root)
        self._orig_job_id = job._new_job_id
        job._new_job_id = lambda: "fixed-job-id"
        self.addCleanup(setattr, job, "_new_job_id", self._orig_job_id)

    def _run_far_enough_and_read_back(self) -> dict:
        # No FAKE_CLAUDE_WRITE_PATH/TEXT override: the fake worker creates
        # no new work item, so row 7's own post-state check (CP6B) does
        # not verify -- FAILED, never a crash. This class asserts only
        # against the CP6-owned fields below, which that outcome leaves
        # untouched.
        job.execute_step(
            self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
            claude_bin=str(FAKE_CLAUDE), timeout=10,
        )
        # A fresh, process-level read off disk -- never the in-process
        # value execute_step itself held.
        job_path = self.runtime_root / "jobs" / "fixed-job-id.json"
        return json.loads(job_path.read_text())

    def test_planned_launched_completed_flushes_are_durable_and_do_not_crash(self) -> None:
        record = self._run_far_enough_and_read_back()
        # CP6B's own step 8/9 (zero new work items -> not verified) leaves
        # the final status FAILED; the CP6-owned fields below are exactly
        # what the COMPLETED flush wrote, unmodified by that outcome.
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertIsNone(record["work_item_id"])
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["selected_action"]["command"], "/milestone-plan")

    def test_pre_work_item_keys_is_a_pre_state_field_of_the_eighteen(self) -> None:
        # Seventeen until `workflow-controller-automatic-lifecycle-
        # orchestration` CP2 added `bundle_manifest_bundle_id`.
        record = self._run_far_enough_and_read_back()
        self.assertEqual(len(job.PRE_STATE_FIELDS), 18)
        self.assertEqual(set(record["pre_state"]), job.PRE_STATE_FIELDS)
        self.assertEqual(record["pre_state"]["pre_work_item_keys"], [])

    def test_bootstrap_bundle_manifest_bundle_id_is_none(self) -> None:
        record = self._run_far_enough_and_read_back()
        self.assertIn("bundle_manifest_bundle_id", record["pre_state"])
        self.assertIsNone(record["pre_state"]["bundle_manifest_bundle_id"])

    def test_no_phase_round_trips_identically_across_all_three_fields(self) -> None:
        record = self._run_far_enough_and_read_back()
        self.assertEqual(record["pre_state"]["phase"], "__NO_PHASE__")
        self.assertEqual(record["observed_phase_before"], "__NO_PHASE__")
        self.assertEqual(record["expected_transition"]["from"], "__NO_PHASE__")
        for wire in (
            record["pre_state"]["phase"],
            record["observed_phase_before"],
            record["expected_transition"]["from"],
        ):
            self.assertIs(phase_from_wire(wire), NO_PHASE)
        self.assertEqual(
            set(record["expected_transition"]["to_any_of"]), {"AWAITING_LOCAL_PLAN_REVIEW"},
        )

    def test_no_phase_reader_refuses_null_and_bare_none_string(self) -> None:
        # The total, fail-closed half of the round-trip rule: a fixture
        # record carrying "phase": null or "phase": "None" must be
        # refused, never silently read as NO_PHASE.
        from controller.errors import UnknownPhaseError
        with self.assertRaises(UnknownPhaseError):
            phase_from_wire(None)
        with self.assertRaises(UnknownPhaseError):
            phase_from_wire("None")


class PredicateRow3RoleNormalizationTest(unittest.TestCase):
    """CP3: ``_predicate_row3_block_feedback_current``'s ``Reviewer
    role:`` comparison must agree with ``evidence._normalize_role``
    (the comparison ``evidence.py``'s manual-stage admissibility check
    already applies), exercised through the real on-disk read path
    (``evidence.read_feedback_fields``) rather than a pure in-memory
    string comparison -- a legacy-spelling role line that the two readers
    previously disagreed on (plan review round 1, finding O2). Built as a
    local literal, not through ``tests/fixtures.py``'s
    ``build_review_feedback_text``/``write_review_feedback`` (CP2 rewrites
    those in the same revision window; plan review round 1, finding I5)."""

    def test_legacy_lowercase_role_spelling_is_recognized(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle_id = "a" * 64
            feedback_dir = Path(".ai-review/feedback")
            (root / feedback_dir).mkdir(parents=True)
            (root / feedback_dir / "REVIEW_FEEDBACK.md").write_text(
                "Status: BLOCK\n"
                "Reviewer role: local_model_plan_review\n"
                f"Reviewed bundle ID: {bundle_id}\n"
            )
            pre_state = {"bundle_manifest_readable": True, "bundle_manifest_bundle_id": bundle_id}
            self.assertTrue(
                job._predicate_row3_block_feedback_current(root, "wi-1", pre_state)
            )


class BundleManifestBundleIdCaptureTest(unittest.TestCase):
    """CP2 (`workflow-controller-automatic-lifecycle-orchestration`): the
    pre-state records the phase-resolved bundle ``MANIFEST.md``'s own
    ``bundle_id`` as ``bundle_manifest_bundle_id`` -- never
    ``current_bundle_id``, which stays report-only ``bundle_id`` -- and
    ``None`` when no manifest is readable."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)

    def _pre_state(self, managed_repo) -> dict:
        with _WriteSpy() as spy:
            job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        return spy.calls[0][1]["pre_state"]

    def test_plan_stage_manifest_bundle_id_is_captured(self) -> None:
        managed_repo = _build_target(
            self.tmp_root / "plan", phase="AWAITING_LOCAL_PLAN_REVIEW", current_bundle_id=None,
        )
        fixtures.write_plan_manifest(managed_repo.root, "wi-1", 1, bundle_id="d" * 64)
        pre_state = self._pre_state(managed_repo)
        self.assertEqual(pre_state["bundle_manifest_bundle_id"], "d" * 64)
        self.assertIsNone(pre_state["bundle_id"])

    def test_implementation_stage_manifest_bundle_id_is_captured(self) -> None:
        managed_repo = _build_target(
            self.tmp_root / "impl", phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            governing_workflow_version="2.2",
        )
        fixtures.write_implementation_manifest(managed_repo.root, "wi-1", 1, bundle_id="e" * 64)
        pre_state = self._pre_state(managed_repo)
        self.assertEqual(pre_state["bundle_manifest_bundle_id"], "e" * 64)

    def test_no_readable_manifest_captures_none(self) -> None:
        managed_repo = _build_target(self.tmp_root / "none", phase="PLANNING")
        pre_state = self._pre_state(managed_repo)
        self.assertIn("bundle_manifest_bundle_id", pre_state)
        self.assertIsNone(pre_state["bundle_manifest_bundle_id"])
        self.assertFalse(pre_state["bundle_manifest_readable"])


class MissingGoverningVersionRowRegressionTest(unittest.TestCase):
    """Permanent regression proof for CP1's own fix
    (`workflow-controller-protocol-2-2-compatibility`'s CP2): reproduced
    from data, not from reverting a commit, so it keeps proving the fix
    is load-bearing even as :data:`job.EXPECTED_OUTCOMES` grows further.
    Shrinks ``_EXPECTED_OUTCOMES_BY_KEY`` down to exactly the seven
    entries that existed before CP1 (every plan-stage entry whose own
    ``governing_version`` is not ``"2.2"`` -- the implementation-stage rows
    `workflow-controller-automatic-lifecycle-orchestration`'s CP2 added,
    `"2.1"` ones included, postdate CP1 too) and asserts that
    ``_expected_outcome_for`` raises ``AssertionError`` for a `"2.2"`
    `PLANNING` `/milestone-plan` decision against that patched table --
    the exact crash shape Controller hit before CP1 added the `"2.2"`
    plan-review rows."""

    def test_pre_cp1_table_raises_on_a_2_2_planning_decision(self) -> None:
        implementation_stage_actions = {
            "/milestone-implement", "/review-implementation", "/record-manual-implementation-review",
            "/apply-implementation-review",
        }
        pre_cp1_outcomes = {
            key: outcome
            for key, outcome in job._EXPECTED_OUTCOMES_BY_KEY.items()
            if outcome.governing_version != "2.2" and outcome.action not in implementation_stage_actions
        }
        self.assertEqual(len(pre_cp1_outcomes), 7)
        decision = Decision(
            observed_phase="PLANNING", evidence=(), action=Action(command="/milestone-plan wi-1"),
            automatic=True, gate=None, declined=False, reason="test fixture",
        )
        with unittest.mock.patch.dict(
            job._EXPECTED_OUTCOMES_BY_KEY, pre_cp1_outcomes, clear=True,
        ):
            with self.assertRaises(AssertionError):
                job._expected_outcome_for("PLANNING", "2.2", decision)


class DefaultPermissionModeTest(unittest.TestCase):
    """Worker-execution hardening CP1: lifecycle workers default to
    ``auto``, and the mode ``execute_step`` receives -- default or
    explicit -- is exactly what reaches the real worker subprocess argv."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)

    def test_default_permission_mode_is_auto(self) -> None:
        import inspect
        self.assertEqual(job.DEFAULT_PERMISSION_MODE, "auto")
        self.assertEqual(
            inspect.signature(job.execute_step).parameters["permission_mode"].default, "auto",
        )

    def _observed_argv(self, name: str, **kwargs) -> list[str]:
        managed_repo = _build_target(
            self.tmp_root / name, phase="PLANNING", governing_workflow_version="2.1",
        )
        diag = self.tmp_root / f"{name}-diag.json"
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_DIAG_FILE": str(diag)}):
            job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10, **kwargs,
            )
        return json.loads(diag.read_text())["argv"]

    def _mode_in(self, argv: list[str]) -> str:
        return argv[argv.index("--permission-mode") + 1]

    def test_worker_argv_carries_auto_by_default(self) -> None:
        self.assertEqual(self._mode_in(self._observed_argv("default")), "auto")

    def test_worker_argv_carries_an_explicit_mode_unchanged(self) -> None:
        argv = self._observed_argv("explicit", permission_mode="acceptEdits")
        self.assertEqual(self._mode_in(argv), "acceptEdits")


class WorkerRouteTest(unittest.TestCase):
    """Automatic-lifecycle-orchestration CP6: ``execute_step`` derives the
    worker's role from ``(phase, command, registry_complete)``, records the
    resolved route as ``worker_route`` from the ``PLANNED`` flush on, and
    hands its model, effort and disallowed tools to the worker, whose argv
    ``tests/fake_claude.py``'s diagnostic file records."""

    SESSION_REUSE_FLAGS = frozenset({"--resume", "-r", "--continue", "-c", "--fork-session", "--session-id"})

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self._runs = 0

    def _execute(self, managed_repo, *, forced: tuple[str, str] | None = None,
                 options: "routing.RoutingOptions | None" = None) -> tuple[dict, list[str], list[dict]]:
        self._runs += 1
        runtime_root = self.tmp_root / f"runtime-{self._runs}"
        diag = self.tmp_root / f"diag-{self._runs}.json"
        kwargs = {} if options is None else {"routing": options}
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_DIAG_FILE": str(diag)}))
            if forced is not None:
                stack.enter_context(fixtures.forced_automatic_action(*forced))
            stack.enter_context(spy := _WriteSpy())
            record = job.execute_step(
                managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10, **kwargs,
            )
        writes = [obj for rel, obj in spy.calls if rel.startswith("jobs/")]
        return record, json.loads(diag.read_text())["argv"], writes

    def _implementation_target(self, name: str, phase: str, **kwargs):
        return fixtures.build_implementation_target(self.tmp_root / name, phase=phase, **kwargs)

    def _assert_argv_carries(self, argv: list[str], route: dict) -> None:
        self.assertFalse(self.SESSION_REUSE_FLAGS & set(argv), argv)
        for flag, value in (("--model", route["model"]), ("--effort", route["effort"])):
            if value is None:
                self.assertNotIn(flag, argv)
            else:
                self.assertEqual(argv[argv.index(flag) + 1], value)
        if route["single_agent"]:
            self.assertEqual(argv[-2:], ["--disallowedTools", ",".join(routing.SUBAGENT_TOOLS)])
        else:
            self.assertNotIn("--disallowedTools", argv)

    def test_the_planned_flush_records_the_route_and_every_later_write_keeps_it(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        record, argv, writes = self._execute(managed_repo)
        expected = {
            "role": "milestone-plan", "model": None, "effort": None, "single_agent": False,
            "fresh_session": True, "sources": {"model": "inherit", "effort": "inherit"},
        }
        self.assertEqual(writes[0]["status"], job.STATUS_PLANNED)
        for write in writes:
            self.assertEqual(write["worker_route"], expected, write["status"])
        self.assertEqual(record["worker_route"], expected)
        # An inherit role passes neither flag, and no disallow list.
        self.assertEqual(argv, ["-p", "/milestone-plan wi-1", "--output-format", "stream-json",
                                "--verbose", "--permission-mode", "auto"])

    def test_each_role_reaches_the_record_and_the_worker_argv(self) -> None:
        opus = ("claude-opus-5-5", "xhigh")
        cases = (
            # (name, phase, command, target overrides, role, (model, effort), single-agent)
            ("review-plan", "AWAITING_LOCAL_PLAN_REVIEW", "/review-plan", {}, "review-plan", opus, True),
            ("apply-plan-review", "REVISING_PLAN", "/apply-plan-review", {}, "apply-plan-review", opus, False),
            ("record-plan", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "/record-manual-plan-review", {},
             "record-manual-plan-review", (None, None), False),
            ("checkpoint", "IMPLEMENTING", "/milestone-implement", {"checkpoints": {}},
             "milestone-implement", opus, False),
            ("final-pass", "IMPLEMENTING", "/milestone-implement", {}, "milestone-implement-self-review",
             opus, True),
            ("self-review", "SELF_REVIEWING_IMPLEMENTATION", "/milestone-implement", {},
             "milestone-implement-self-review", opus, True),
            ("review-impl", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "/review-implementation", {},
             "review-implementation", opus, True),
            ("apply-impl", "APPLYING_REVIEW_FEEDBACK", "/apply-implementation-review", {},
             "apply-implementation-review", opus, False),
            ("record-impl", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "/record-manual-implementation-review",
             {}, "record-manual-implementation-review", (None, None), False),
        )
        for name, phase, command, overrides, role, (model, effort), single_agent in cases:
            with self.subTest(case=name):
                managed_repo = self._implementation_target(name, phase, **overrides)
                record, argv, writes = self._execute(managed_repo, forced=(phase, command))
                route = writes[0]["worker_route"]
                self.assertEqual(writes[0]["status"], job.STATUS_PLANNED)
                self.assertEqual(
                    (route["role"], route["model"], route["effort"], route["single_agent"], route["fresh_session"]),
                    (role, model, effort, single_agent, True),
                )
                source = "default" if model is not None else "inherit"
                self.assertEqual(route["sources"], {"model": source, "effort": source})
                self.assertEqual(record["worker_route"], route)
                self.assertEqual(argv[1], f"{command} wi-1")
                self._assert_argv_carries(argv, route)

    def test_the_sources_name_the_winning_level(self) -> None:
        config = routing.parse_routing_config(json.dumps({
            "schema_version": 1, "default": {"effort": "e-config-default"},
            "roles": {"review-plan": {"model": "m-config-role"}},
        }), path="<test>")
        cases = (
            (routing.NO_OVERRIDES, ("claude-opus-5-5", "default"), ("xhigh", "default")),
            (routing.RoutingOptions(cli_role_models={"review-plan": "m-role"}, cli_effort="e-cli"),
             ("m-role", "role-cli"), ("e-cli", "cli")),
            (routing.RoutingOptions(cli_model="m-cli", cli_role_efforts={"review-plan": "e-role"}),
             ("m-cli", "cli"), ("e-role", "role-cli")),
            (routing.RoutingOptions(config=config), ("m-config-role", "config-role"),
             ("e-config-default", "config-default")),
            (routing.RoutingOptions(cli_role_models={"apply-plan-review": "other"}, config=config),
             ("m-config-role", "config-role"), ("e-config-default", "config-default")),
        )
        for index, (options, (model, model_source), (effort, effort_source)) in enumerate(cases):
            with self.subTest(case=index):
                managed_repo = self._implementation_target(f"sources-{index}", "AWAITING_LOCAL_PLAN_REVIEW")
                record, argv, _writes = self._execute(
                    managed_repo, forced=("AWAITING_LOCAL_PLAN_REVIEW", "/review-plan"), options=options,
                )
                route = record["worker_route"]
                self.assertEqual((route["model"], route["effort"]), (model, effort))
                self.assertEqual(route["sources"], {"model": model_source, "effort": effort_source})
                self.assertTrue(route["single_agent"])
                self._assert_argv_carries(argv, route)

    def test_an_unroutable_action_writes_no_record_and_launches_nothing(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        runtime_root = self.tmp_root / "runtime-unroutable"
        invocations = self.tmp_root / "invocations"
        env = {"FAKE_CLAUDE_INVOCATIONS_FILE": str(invocations)}
        with unittest.mock.patch.dict("os.environ", env), \
                fixtures.forced_automatic_action("PLANNING", "/prepare-review"), \
                self.assertRaises(AssertionError):
            job.execute_step(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
                             claude_bin=str(FAKE_CLAUDE), timeout=10)
        self.assertEqual(list((runtime_root / "jobs").glob("*.json")) if (runtime_root / "jobs").exists() else [], [])
        self.assertFalse(invocations.exists())


# ---------------------------------------------------------------------------
# Worker-execution hardening CP5 -- the observed partial `/apply-plan-review`
# (RepFlow): state published at plan revision 11, bundle still at 10.
# ---------------------------------------------------------------------------


def seed_partial_apply_plan_review(tmp_root: Path):
    """A `"2.2"` work item at ``REVISING_PLAN``, ``plan_revision: 10``, whose
    current plan bundle is a complete revision-10 bundle (manifest plus the
    three author files) and whose feedback is the `REVISE` round that
    selected ``/apply-plan-review``."""
    managed_repo = _build_target(
        tmp_root, phase="REVISING_PLAN", governing_workflow_version="2.2", plan_revision=10,
    )
    root = managed_repo.root
    head = fixtures.current_head(root)
    fixtures.write_plan_manifest(root, "wi-1", 10, generation_head=head)
    current = root / ".ai-review" / "wi-1" / "current"
    (current / "REVIEW_REQUEST.md").write_text("stage: plan\nreview_content_id: " + "c" * 64 + "\n")
    (current / "TEST_RESULTS.md").write_text(f"stage: plan (revision 10)\nhead: {head}\n")
    (current / "CONTEXT_FILES.txt").write_text("docs/ai-workflow/REVIEW_PROTOCOL.md\n")
    fixtures.write_review_feedback(
        root, ".ai-review/wi-1/feedback",
        fixtures.build_review_feedback_text(
            status="REVISE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW",
            reviewed_base_commit=head,
        ),
    )
    return managed_repo


def partial_apply_plan_review_worker_env(root: Path, *, manifest_revision: int | None = None) -> dict[str, str]:
    """A fake worker performing only the state half of `/apply-plan-review`
    step 5 (``publish_plan_revision``: ``plan_revision: 11``, phase
    ``AWAITING_LOCAL_PLAN_REVIEW``) and leaving the bundle untouched -- or,
    with ``manifest_revision``, also writing a manifest at that revision
    (the positive control)."""
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = json.loads(state_path.read_text())
    entry = state["work_items"]["wi-1"]
    entry.update(phase="AWAITING_LOCAL_PLAN_REVIEW", plan_revision=11, state_revision=entry["state_revision"] + 1)
    writes = [{"path": str(state_path), "text": json.dumps(state, indent=2) + "\n"}]
    if manifest_revision is not None:
        writes.append({
            "path": str(root / ".ai-review" / "wi-1" / "current" / "MANIFEST.md"),
            "text": fixtures.build_plan_manifest_text("wi-1", manifest_revision),
        })
    return {"FAKE_CLAUDE_WRITES": json.dumps(writes)}


#: The ``postcondition_detail`` naming both revisions of the observed case.
PARTIAL_APPLY_DETAIL = "manifest plan_revision 10 != state plan_revision 11"


class PartialApplyPlanReviewExecuteTest(unittest.TestCase):
    """The RepFlow case through ``execute_step``: ``FAILED`` (never
    ``FINISHED``) on the worker's own job, then ``GATE_BLOCKED`` with CP4's
    recovery steps -- and no worker -- on the next one."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)
        self.managed_repo = seed_partial_apply_plan_review(self.tmp_root)
        self.root = self.managed_repo.root

    def _execute(self, env: dict[str, str]):
        with unittest.mock.patch.dict("os.environ", env):
            return job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )

    def test_state_published_bundle_stale_fails_with_postcondition_not_satisfied(self) -> None:
        record = self._execute(partial_apply_plan_review_worker_env(self.root))
        self.assertEqual(record["selected_action"]["command"], "/apply-plan-review wi-1")
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "postcondition_not_satisfied")
        self.assertEqual(ev["postcondition_detail"], PARTIAL_APPLY_DETAIL)

    def test_next_step_gates_with_the_recovery_steps_and_launches_no_worker(self) -> None:
        self._execute(partial_apply_plan_review_worker_env(self.root))
        never = self.tmp_root / "never-exists"
        with _WriteSpy() as spy:
            record = self._execute({"FAKE_CLAUDE_REQUIRE_FILE": str(never), "FAKE_CLAUDE_WRITES": ""})
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(job.STATUS_LAUNCHED, spy.statuses())
        self.assertNotIn("worker", record)
        self.assertNotIn("worker_outcome", record)
        self.assertEqual(record["observed_phase_before"], "AWAITING_LOCAL_PLAN_REVIEW")

        gate = record["human_gate_pending"]
        base_commit = json.loads(
            (self.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").read_text()
        )["work_items"]["wi-1"]["base_commit"]
        steps = gate["safe_resume_command"].split("; ")
        self.assertEqual([s.split(" ", 1)[0] for s in steps], ["1.", "2.", "3."])
        self.assertTrue(steps[0].startswith("1. refresh .ai-review/wi-1/current/REVIEW_REQUEST.md"))
        self.assertIn("compute_review_content_id_plan_stage_for_work_item", steps[0])
        self.assertTrue(steps[1].startswith("2. refresh .ai-review/wi-1/current/TEST_RESULTS.md"))
        self.assertIn("`stage: plan (revision 11)`", steps[1])
        self.assertTrue(steps[2].startswith(f"3. run scripts/prepare-ai-review.sh {base_commit} plan wi-1"))
        self.assertNotEqual(gate["safe_resume_command"], "scripts/prepare-ai-review.sh <base-sha> plan wi-1")
        self.assertIn(PARTIAL_APPLY_DETAIL, gate["what_is_required"])
        self.assertIn(" ".join(steps), gate["what_is_required"])

    def test_positive_control_revision_11_manifest_finishes(self) -> None:
        record = self._execute(partial_apply_plan_review_worker_env(self.root, manifest_revision=11))
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertTrue(record["transition_verified"])

    def test_pre_fix_table_without_the_postcondition_verifies_the_same_scenario(self) -> None:
        """The durable pre-fix demonstration: strip only this row's
        postcondition and the identical run is ``FINISHED`` -- so the
        postcondition is what makes the scenario above fail closed."""
        key = ("REVISING_PLAN", "2.2", "/apply-plan-review")
        row = job._EXPECTED_OUTCOMES_BY_KEY[key]
        self.assertTrue(row.postconditions)
        pre_fix = dataclasses.replace(row, postconditions=())
        with unittest.mock.patch.dict(job._EXPECTED_OUTCOMES_BY_KEY, {key: pre_fix}):
            record = self._execute(partial_apply_plan_review_worker_env(self.root))
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertIs(job._EXPECTED_OUTCOMES_BY_KEY[key], row)



# ---------------------------------------------------------------------------
# Automatic-lifecycle-orchestration CP4B: the apply relaunch bound's job
# history, and the "2.2" APPLYING_REVIEW_FEEDBACK launch with its task
# addendum.
# ---------------------------------------------------------------------------

_APPLY_PHASE = "APPLYING_REVIEW_FEEDBACK"


def _apply_job_record(
    target_repo: str | None, *, job_id: str, status: str, created_at: str, bundle_id: str | None = "b" * 64,
    work_item_id: str = "wi-1", from_phase: str = _APPLY_PHASE,
    command: str = "/apply-implementation-review wi-1", launched: bool = True, code: str | None = None,
) -> dict:
    """A job record as ``execute_step`` writes one for a launched apply (or,
    with ``launched=False``, one that never reached ``LAUNCHED``), carrying
    only what ``last_launched_apply_job_view`` reads plus the identity
    fields."""
    record = {
        "schema_version": job.SCHEMA_VERSION, "job_id": job_id, "controller_generation": 7,
        "target_repo": target_repo, "work_item_id": work_item_id, "observed_phase_before": from_phase,
        "pre_state": {"phase": from_phase, "bundle_manifest_bundle_id": bundle_id},
        "selected_action": {
            "kind": "slash_command", "command": command, "task_addendum": None, "automatic": True,
            "declined": False, "reason": "seeded", "evidence": [],
        },
        "status": status, "human_gate_pending": None, "handoff_pending": False,
        "created_at": created_at, "updated_at": created_at,
    }
    if launched:
        record["expected_transition"] = {"from": from_phase, "to_any_of": ["AWAITING_LOCAL_IMPLEMENTATION_REVIEW"]}
    if code is not None:
        record["reconciliation_evidence"] = {"code": code, "error": "WorkerLaunchError"}
    return record


def _seed_job(runtime_root: Path, record: dict, *, name: str | None = None) -> Path:
    jobs_dir = runtime_root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / f"{name or record['job_id']}.json"
    path.write_text(json.dumps(record))
    return path


class LastLaunchedApplyJobViewTest(unittest.TestCase):
    """``job.last_launched_apply_job_view`` (CP4B, "Where the job history
    comes from"): J is the most recent terminal ``/apply-implementation-
    review`` record this Controller launched for the work item from
    ``APPLYING_REVIEW_FEEDBACK``, never a non-terminal or never-started one,
    read tolerantly and without ever raising on a job file (round 3's O5)."""

    TARGET = "/targets/repo"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime_root = Path(self._tmp.name) / "runtime"

    def _view(self, work_item_id: str | None = "wi-1"):
        return job.last_launched_apply_job_view(self.runtime_root, Path(self.TARGET), work_item_id)

    def _seed(self, job_id: str, status: str, created_at: str, **fields) -> None:
        _seed_job(self.runtime_root, _apply_job_record(
            self.TARGET, job_id=job_id, status=status, created_at=created_at, **fields,
        ))

    def test_no_runtime_no_jobs_or_no_work_item_is_none(self) -> None:
        self.assertIsNone(self._view())
        (self.runtime_root / "jobs").mkdir(parents=True)
        self.assertIsNone(self._view())
        self._seed("j1", job.STATUS_FAILED, "2026-01-01T00:00:00Z")
        self.assertIsNone(self._view(None))

    def test_a_terminal_launched_apply_record_is_read(self) -> None:
        self._seed("j1", job.STATUS_FAILED, "2026-01-01T00:00:00Z")
        self.assertEqual(self._view(), evidence.LaunchedJobView(
            job_id="j1", command="/apply-implementation-review", from_phase=_APPLY_PHASE,
            status=job.STATUS_FAILED, pre_bundle_manifest_bundle_id="b" * 64,
        ))

    def test_the_most_recent_candidate_wins(self) -> None:
        self._seed("zz-older", job.STATUS_FAILED, "2026-01-01T00:00:00Z")
        self._seed("aa-newer", job.STATUS_FINISHED, "2026-01-02T00:00:00Z")
        self.assertEqual(self._view().job_id, "aa-newer")
        self._seed("mm-newest", job.STATUS_INTERRUPTED, "2026-01-03T00:00:00Z")
        self.assertEqual(self._view().status, job.STATUS_INTERRUPTED)

    def test_a_non_terminal_record_is_never_j(self) -> None:
        """Round 4's O3: a record still ``LAUNCHED`` (for example, the
        Controller was killed during the apply and the worker is orphaned)
        is never J, so neither ``explain`` nor ``step`` advises a rerun while
        it may still be editing the worktree."""
        for status in (job.STATUS_PLANNED, job.STATUS_LAUNCHED, job.STATUS_COMPLETED):
            with self.subTest(status=status):
                self._seed("newest", status, "2026-01-09T00:00:00Z")
                self.assertIsNone(self._view())
        self._seed("earlier", job.STATUS_FAILED, "2026-01-01T00:00:00Z")
        self.assertEqual(self._view().job_id, "earlier")

    def test_a_worker_not_started_record_is_skipped_so_an_earlier_attempt_counts(self) -> None:
        self._seed("not-started", job.STATUS_FAILED, "2026-01-09T00:00:00Z", code=job.WORKER_NOT_STARTED_CODE)
        self.assertIsNone(self._view())
        self._seed("earlier", job.STATUS_FAILED, "2026-01-01T00:00:00Z")
        self.assertEqual(self._view().job_id, "earlier")

    def test_non_candidates_are_skipped_without_raising(self) -> None:
        jobs_dir = self.runtime_root / "jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "unparseable.json").write_text("{not json")
        (jobs_dir / "binary.json").write_bytes(b"\xff\xfe\x00")
        (jobs_dir / "a-list.json").write_text("[1, 2]")
        (jobs_dir / "a-directory.json").mkdir()
        # `resume --abandon`'s minimal record: no field of it names a target.
        (jobs_dir / "abandoned.json").write_text(json.dumps({
            "job_id": "abandoned", "target_repo": None, "status": job.STATUS_FAILED,
            "reconciliation_evidence": {"code": "OperatorAbandoned"},
        }))
        later = "2026-01-09T00:00:00Z"
        for name, fields in (
            ("other-target", dict(target_repo_override="/targets/other")),
            ("other-item", dict(work_item_id="wi-2")),
            ("other-phase", dict(from_phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW")),
            ("other-command", dict(command="/review-implementation wi-1")),
            ("never-launched", dict(launched=False, status=job.STATUS_GATE_BLOCKED)),
            ("unknown-status", dict(status="EXOTIC")),
        ):
            target = fields.pop("target_repo_override", self.TARGET)
            status = fields.pop("status", job.STATUS_FAILED)
            _seed_job(self.runtime_root, _apply_job_record(
                target, job_id=name, status=status, created_at=later, **fields,
            ))
        for name, mutate in (
            ("no-job-id", lambda r: r.pop("job_id")),
            ("no-created-at", lambda r: r.pop("created_at")),
            ("no-pre-state", lambda r: r.update(pre_state=None)),
            ("no-selected-action", lambda r: r.update(selected_action="x")),
            ("empty-command", lambda r: r["selected_action"].update(command="  ")),
            ("transition-not-object", lambda r: r.update(expected_transition=["from"])),
        ):
            record = _apply_job_record(self.TARGET, job_id=name, status=job.STATUS_FAILED, created_at=later)
            mutate(record)
            _seed_job(self.runtime_root, record, name=name)
        self.assertIsNone(self._view())
        self._seed("the-one", job.STATUS_FAILED, "2026-01-01T00:00:00Z")
        self.assertEqual(self._view().job_id, "the-one")

    def test_a_record_without_the_pre_state_bundle_id_reads_as_none(self) -> None:
        record = _apply_job_record(self.TARGET, job_id="pre-cp2", status=job.STATUS_FAILED,
                                   created_at="2026-01-01T00:00:00Z")
        del record["pre_state"]["bundle_manifest_bundle_id"]
        _seed_job(self.runtime_root, record)
        view = self._view()
        self.assertEqual(view.job_id, "pre-cp2")
        self.assertIsNone(view.pre_bundle_manifest_bundle_id)
        self.assertTrue(evidence.relaunch_bound_applies(view, "b" * 64))


class ApplyingReviewFeedbackExecuteTest(unittest.TestCase):
    """``execute_step`` at ``"2.2"`` ``APPLYING_REVIEW_FEEDBACK`` (CP4B),
    against a real target whose ``HEAD`` records the previous round's
    ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` and whose working tree holds
    the review-stage writer's uncommitted ``APPLYING_REVIEW_FEEDBACK`` plus
    an admissible local ``REVISE``. A launching case runs
    ``tests/fake_claude.py`` as a no-op (its diagnostic file records the
    task); a gated case runs it fail-if-invoked."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.managed_repo = fixtures.build_implementation_target(
            self.tmp_root, phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW", implementation_revision=1,
            reviewed_implementation_head="1" * 40,
        )
        self.root = self.managed_repo.root
        fixtures.write_implementation_bundle(self.root, "wi-1", 1, reviewed_implementation_head="1" * 40)
        fixtures.write_review_feedback(self.root, ".ai-review/wi-1/feedback", fixtures.build_review_feedback_text(
            status="REVISE", reviewer_role="LOCAL_MODEL_IMPLEMENTATION_REVIEW",
            reviewed_base_commit=fixtures.state_entry(self.root)["base_commit"],
        ))
        fixtures.update_workflow_state(self.root, "wi-1", phase=_APPLY_PHASE)
        self.addendum = evidence.pending_review_stage_write_addendum(
            "wi-1", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
        )
        self._runs = 0

    def _execute(self, *, seeded: tuple[dict, ...] = (), launches: bool) -> tuple[dict, Path, Path]:
        self._runs += 1
        runtime_root = self.tmp_root / f"runtime-{self._runs}"
        for record in seeded:
            _seed_job(runtime_root, record)
        diag = self.tmp_root / f"diag-{self._runs}.json"
        env = {"FAKE_CLAUDE_DIAG_FILE": str(diag)}
        if not launches:
            env["FAKE_CLAUDE_REQUIRE_FILE"] = str(self.tmp_root / "never-created")
        with unittest.mock.patch.dict("os.environ", env):
            record = job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        self.assertEqual(diag.exists(), launches)
        self.assertEqual(record["selected_action"]["automatic"], launches)
        return record, diag, runtime_root

    def _record(self, job_id: str, status: str, created_at: str = "2026-01-01T00:00:00Z", **fields) -> dict:
        return _apply_job_record(str(self.root), job_id=job_id, status=status, created_at=created_at, **fields)

    def test_the_pending_write_addendum_is_in_the_launched_task_and_recorded(self) -> None:
        record, diag, _runtime = self._execute(launches=True)
        argv = json.loads(diag.read_text())["argv"]
        self.assertEqual(argv[1], f"/apply-implementation-review wi-1\n\n{self.addendum}")
        self.assertEqual(record["selected_action"]["command"], "/apply-implementation-review wi-1")
        self.assertEqual(record["selected_action"]["task_addendum"], self.addendum)
        self.assertEqual(record["expected_transition"]["from"], _APPLY_PHASE)
        # A no-op worker regenerates nothing, so nothing verifies.
        self.assertEqual(record["status"], job.STATUS_FAILED)

    def test_head_recording_the_phase_launches_the_bare_command(self) -> None:
        fixtures.commit_paths(self.root, "Record the review-stage state write", "docs/ai-workflow/WORKFLOW_STATE.json")
        record, diag, _runtime = self._execute(launches=True)
        self.assertEqual(json.loads(diag.read_text())["argv"][1], "/apply-implementation-review wi-1")
        self.assertIsNone(record["selected_action"]["task_addendum"])

    def test_an_unverified_earlier_attempt_is_the_relaunch_bound_and_nothing_launches(self) -> None:
        for status in (job.STATUS_FAILED, job.STATUS_INTERRUPTED, job.STATUS_INCOMPLETE):
            with self.subTest(status=status):
                record, _diag, _runtime = self._execute(
                    seeded=(self._record("earlier-apply", status),), launches=False,
                )
                self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
                gate = record["human_gate_pending"]
                self.assertIn(f"job earlier-apply, ended {status}", gate["what_is_required"])
                self.assertEqual(gate["safe_resume_command"], "workflow-controller explain --work-item wi-1")

    def test_a_null_recorded_bundle_is_the_bound(self) -> None:
        record, _diag, _runtime = self._execute(
            seeded=(self._record("earlier-apply", job.STATUS_FAILED, bundle_id=None),), launches=False,
        )
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)

    def test_no_bound_after_a_finished_attempt_another_phase_or_another_bundle(self) -> None:
        """The last case is round 2's O1: a human completed the apply and
        the next local review outside this Controller, and a later
        ``REVISE`` returned the item here with that failed job still the
        most recent apply job. The manifest is new, so the bound lapsed."""
        for name, seeded in (
            ("FINISHED", self._record("earlier-apply", job.STATUS_FINISHED)),
            ("another phase", self._record(
                "earlier-review", job.STATUS_FAILED, from_phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                command="/review-implementation wi-1",
            )),
            ("another bundle", self._record("earlier-apply", job.STATUS_FAILED, bundle_id="a" * 64)),
        ):
            with self.subTest(case=name):
                record, diag, _runtime = self._execute(seeded=(seeded,), launches=True)
                self.assertEqual(record["selected_action"]["command"], "/apply-implementation-review wi-1")
                self.assertEqual(record["selected_action"]["task_addendum"], self.addendum)

    def test_a_worker_not_started_record_is_skipped_but_an_earlier_attempt_behind_it_counts(self) -> None:
        not_started = self._record(
            "not-started", job.STATUS_FAILED, created_at="2026-01-09T00:00:00Z",
            code=job.WORKER_NOT_STARTED_CODE,
        )
        with self.subTest(case="alone"):
            self._execute(seeded=(not_started,), launches=True)
        with self.subTest(case="in front of an earlier real attempt"):
            record, _diag, _runtime = self._execute(
                seeded=(not_started, self._record("real-attempt", job.STATUS_FAILED)), launches=False,
            )
            self.assertIn("job real-attempt, ended FAILED", record["human_gate_pending"]["what_is_required"])

    def test_a_non_terminal_record_is_never_j(self) -> None:
        """CP4B: J is a terminal record only. Automatic-lifecycle-
        orchestration CP5 changes what ``execute_step`` does beside such a
        record: it is pending reconciliation, so ``step`` refuses before
        deciding and launches nothing (it used to decide around it). The
        job history the decision reads still never takes it as J."""
        pending = self._record("still-launched", job.STATUS_LAUNCHED, created_at="2026-01-09T00:00:00Z")
        earlier = self._record("earlier-apply", job.STATUS_FAILED)
        for case, seeded, expected_j in (
            ("alone", (pending,), None),
            ("in front of an earlier terminal attempt", (pending, earlier), "earlier-apply"),
        ):
            with self.subTest(case=case):
                self._runs += 1
                runtime_root = self.tmp_root / f"runtime-{self._runs}"
                for record in seeded:
                    _seed_job(runtime_root, record)
                view = job.last_launched_apply_job_view(runtime_root, self.root, "wi-1")
                self.assertEqual(None if view is None else view.job_id, expected_j)
                diag = self.tmp_root / f"diag-{self._runs}.json"
                env = {"FAKE_CLAUDE_DIAG_FILE": str(diag),
                       "FAKE_CLAUDE_REQUIRE_FILE": str(self.tmp_root / "never-created")}
                with unittest.mock.patch.dict("os.environ", env):
                    with self.assertRaises(PendingJobReconciliationError) as ctx:
                        job.execute_step(
                            self.managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
                            claude_bin=str(FAKE_CLAUDE), timeout=10,
                        )
                self.assertIn("job still-launched (LAUNCHED", ctx.exception.message)
                self.assertIn(f"workflow-controller resume {self.root}", ctx.exception.message)
                self.assertFalse(diag.exists(), "no worker may start beside a pending job")

    def test_the_unverified_job_this_step_leaves_is_the_next_steps_bound(self) -> None:
        """State 7's shape at the unit level: the first ``step`` launches
        and lands ``FAILED`` (no-op worker); the next ``step`` against the
        same, unchanged bundle is the relaunch-bound gate naming that job."""
        first, _diag, runtime_root = self._execute(launches=True)
        self.assertEqual(first["pre_state"]["bundle_manifest_bundle_id"], "b" * 64)
        env = {"FAKE_CLAUDE_REQUIRE_FILE": str(self.tmp_root / "never-created")}
        with unittest.mock.patch.dict("os.environ", env):
            second = job.execute_step(
                self.managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
                claude_bin=str(FAKE_CLAUDE), timeout=10,
            )
        self.assertEqual(second["status"], job.STATUS_GATE_BLOCKED)
        self.assertIn(f"job {first['job_id']}, ended FAILED", second["human_gate_pending"]["what_is_required"])


class WorkerTaskTest(unittest.TestCase):
    """``job.worker_task``: the bare command unless the action carries a
    ``task_addendum`` (CP4B)."""

    def test_bare_command_without_an_addendum(self) -> None:
        self.assertEqual(job.worker_task(Action(command="/milestone-plan wi-1")), "/milestone-plan wi-1")

    def test_command_blank_line_addendum(self) -> None:
        self.assertEqual(
            job.worker_task(Action(command="/apply-implementation-review wi-1", task_addendum="note")),
            "/apply-implementation-review wi-1\n\nnote",
        )


# ---------------------------------------------------------------------------
# workflow-controller-automatic-lifecycle-orchestration CP5 -- worker
# lifecycle and concurrency, at the `execute_step` level.
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


class _LifecycleCase(unittest.TestCase):
    """A ``"2.1"`` ``PLANNING`` target (the minimal automatic launch), a
    runtime root, and cleanup that ends every worker group a record names."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)
        self.managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        self.root = self.managed_repo.root
        self.release = self.tmp_root / "release"
        self.invocations = self.tmp_root / "invocations"
        # Every fake this case starts appends its pid here, so the cleanup
        # can end it whatever the test did.
        env = unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._end_recorded_workers)

    def _end_recorded_workers(self) -> None:
        """Releases every fake, then kills each worker group a record names
        and each one the invocation counter saw (its pid is its pgid), so
        none outlives even a failure before the ``worker_process`` flush."""
        self.release.touch()
        for path in (self.runtime_root / "jobs").glob("*.json"):
            try:
                worker_process = json.loads(path.read_text()).get("worker_process") or {}
            except (OSError, ValueError, AttributeError):
                continue
            process_fixtures.kill_group(worker_process.get("pgid"))
        for line in _read_lines(self.invocations):
            process_fixtures.kill_group(int(line))

    def _step(self, **kwargs):
        return job.execute_step(
            self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
            claude_bin=kwargs.pop("claude_bin", str(FAKE_CLAUDE)), **kwargs,
        )

    def _records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.runtime_root / "jobs").glob("*.json"))]


class InProcessConcurrencyTest(_LifecycleCase):
    """While one ``execute_step`` runs a hanging worker, a second one raises
    ``LifecycleWorkerActiveError``, ``cli.main`` maps it to 45 (not 20), and
    only one worker ever ran."""

    def _start_first_step(self) -> tuple[threading.Thread, dict]:
        outcome: dict = {}

        def run() -> None:
            try:
                outcome["record"] = self._step()
            except BaseException as exc:  # noqa: BLE001 -- surfaced by the test
                outcome["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 30)
        self.assertTrue(process_fixtures.wait_until(lambda: len(_read_lines(self.invocations)) == 1),
                        "the first worker never started")
        return thread, outcome

    def test_a_second_step_refuses_with_exit_45_and_launches_nothing(self) -> None:
        env = {"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release),
               "FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}
        with unittest.mock.patch.dict("os.environ", env):
            thread, outcome = self._start_first_step()
            with self.assertRaises(LifecycleWorkerActiveError) as ctx:
                self._step()
            message = ctx.exception.message
            self.assertIn(str(lock.resolve_git_dir(self.root)), message)
            self.assertIn("fuser -v", message)
            self.assertIn("lsof +d", message)
            # The first job's worker is recorded and running: named, as the
            # group to wait for or end.
            worker_process = self._records()[0]["worker_process"]
            self.assertIn(f"process group {worker_process['pgid']}", message)
            self.assertIn("is a process that inherited the descriptor", message)

            def second_step_via_cli(args, argv):
                return self._step()

            stderr = io.StringIO()
            with unittest.mock.patch.object(cli, "_dispatch", second_step_via_cli), \
                    contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(["step", str(self.root)]), cli.EXIT_WORKER_ACTIVE)
            self.assertIn("holds the lifecycle lock", stderr.getvalue())
            self.assertEqual(len(_read_lines(self.invocations)), 1)
            self.release.touch()
            thread.join(30)
        self.assertNotIn("error", outcome)
        self.assertEqual(len(_read_lines(self.invocations)), 1)
        self.assertEqual(len(self._records()), 1, "the refused steps wrote no job record")

    def test_a_sigstopped_worker_counts_as_active_and_its_exit_releases_the_lock(self) -> None:
        env = {"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release),
               "FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}
        with unittest.mock.patch.dict("os.environ", env):
            thread, outcome = self._start_first_step()
            self.assertTrue(process_fixtures.wait_until(lambda: "worker_process" in self._records()[0]))
            worker_process = self._records()[0]["worker_process"]
            os.killpg(worker_process["pgid"], signal.SIGSTOP)
            try:
                self.assertTrue(process_fixtures.wait_until(
                    lambda: (process_fixtures.read_stat(worker_process["pid"]) or ("?",))[0] == "T"))
                self.assertEqual(worker.classify_worker_liveness(worker_process), worker.ACTIVE)
                with self.assertRaises(LifecycleWorkerActiveError):
                    self._step()
            finally:
                os.killpg(worker_process["pgid"], signal.SIGCONT)
            self.release.touch()
            thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", outcome)
        lock.acquire_lifecycle_lock(self.root).release()  # free once the worker exited


class WorkerNotStartedTest(_LifecycleCase):
    """A worker that never started is terminal at once (round 3, O3): the
    record is ``FAILED`` (``WorkerNotStarted``), the error re-raises with
    today's exit code, and the next ``step`` proceeds with no ``resume``."""

    def _assert_not_started(self, error_class: str) -> None:
        records = self._records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        self.assertEqual(record["reconciliation_evidence"]["code"], job.WORKER_NOT_STARTED_CODE)
        self.assertEqual(record["reconciliation_evidence"]["error"], error_class)
        self.assertNotIn("worker_process", record)
        self.assertIn("lifecycle_lock", record)

    def _assert_next_step_launches_exactly_one(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}):
            record = self._step(timeout=10)
        self.assertEqual(len(_read_lines(self.invocations)), 1)
        self.assertIn("worker", record)

    def test_a_nonexistent_claude_bin_is_worker_launch_error_and_a_failed_record(self) -> None:
        with self.assertRaises(WorkerLaunchError):
            self._step(claude_bin=str(self.tmp_root / "no-such-claude"))
        self._assert_not_started("WorkerLaunchError")
        stderr = io.StringIO()
        with unittest.mock.patch.object(cli, "_dispatch", lambda args, argv: self._step(
                claude_bin=str(self.tmp_root / "no-such-claude"))), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["step", str(self.root)]), cli.EXIT_FAIL_CLOSED)
        self._assert_next_step_launches_exactly_one()

    def test_a_user_only_task_is_user_only_command_error_and_a_failed_record(self) -> None:
        decision_ = Decision(
            observed_phase="PLANNING", evidence=(),
            action=Action(command="/milestone-plan wi-1", task_addendum="then run /approve-review plan"),
            automatic=True, gate=None, declined=False, reason="a patched decision with a user-only task",
        )
        with unittest.mock.patch.object(job.evidence, "decide", return_value=decision_):
            with self.assertRaises(UserOnlyCommandError):
                self._step()
        self._assert_not_started("UserOnlyCommandError")
        self._assert_next_step_launches_exactly_one()


class PendingJobReconciliationRefusalTest(_LifecycleCase):
    """No launch over an unreconciled job: under the lock, before deciding."""

    def _seed(self, name: str, record) -> Path:
        jobs_dir = self.runtime_root / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        path = jobs_dir / f"{name}.json"
        path.write_text(record if isinstance(record, str) else json.dumps(record))
        return path

    def _launched(self, job_id: str, **overrides) -> dict:
        record = {
            "schema_version": job.SCHEMA_VERSION, "job_id": job_id, "controller_generation": 7,
            "target_repo": str(self.root), "work_item_id": "wi-1", "observed_phase_before": "PLANNING",
            "pre_state": {"phase": "PLANNING", "governing_workflow_version": "2.1"},
            "selected_action": {"kind": "slash_command", "command": "/milestone-plan wi-1",
                                "automatic": True, "declined": False, "reason": "seeded", "evidence": []},
            "expected_transition": {"from": "PLANNING", "to_any_of": ["AWAITING_LOCAL_PLAN_REVIEW"]},
            "status": job.STATUS_LAUNCHED, "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        record.update(overrides)
        return record

    def _assert_refused(self, *fragments: str) -> PendingJobReconciliationError:
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}):
            with self.assertRaises(PendingJobReconciliationError) as ctx:
                self._step(timeout=10)
        self.assertEqual(_read_lines(self.invocations), [], "nothing may launch")
        for fragment in fragments:
            self.assertIn(fragment, ctx.exception.message)
        return ctx.exception

    def test_a_non_terminal_record_with_a_free_lock_refuses_naming_resume(self) -> None:
        self._seed("j-launched", self._launched("j-launched"))
        error = self._assert_refused("job j-launched (LAUNCHED", f"workflow-controller resume {self.root}")
        self.assertEqual(error.evidence["pending_jobs"], [{
            "job_id": "j-launched", "status": "LAUNCHED",
            "clearing_command": f"workflow-controller resume {self.root}",
        }])
        self.assertEqual(len(self._records()), 1, "the refusal writes no job record")

    def test_every_non_terminal_status_and_an_unknown_one_refuse(self) -> None:
        for status, command in (
            (job.STATUS_PLANNED, f"workflow-controller resume {self.root}"),
            (job.STATUS_COMPLETED, f"workflow-controller resume --abandon j-{job.STATUS_COMPLETED} {self.root}"),
            ("WEIRD", f"workflow-controller resume --abandon j-WEIRD {self.root}"),
        ):
            with self.subTest(status=status):
                for path in (self.runtime_root / "jobs").glob("*.json"):
                    path.unlink()
                record = self._launched(f"j-{status}", status=status)
                if status == job.STATUS_PLANNED:
                    del record["expected_transition"]
                self._seed(f"j-{status}", record)
                # A COMPLETED record without a worker_outcome fails
                # validate_record case 3, so `resume` cannot reconcile it.
                self._assert_refused(f"job j-{status}", command)

    def test_an_unparseable_file_or_a_parseable_non_object_refuses_naming_abandon(self) -> None:
        for name, text in (("garbage", "{not json"), ("a-list", "[1, 2, 3]")):
            with self.subTest(name=name):
                for path in (self.runtime_root / "jobs").glob("*.json"):
                    path.unlink()
                self._seed(name, text)
                self._assert_refused(f"job {name} (unreadable", f"resume --abandon {name} {self.root}")

    def test_a_non_regular_entry_refuses_naming_its_manual_removal(self) -> None:
        """Round 1's O2 of the manual external plan review: never opened
        (a FIFO would block), and named for removal by hand, because
        ``--abandon`` replaces only regular files."""
        jobs_dir = self.runtime_root / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        for name, make in (
            ("a-directory", lambda p: p.mkdir()),
            ("a-fifo", lambda p: os.mkfifo(p)),
            ("a-dangling-link", lambda p: p.symlink_to(self.tmp_root / "nowhere")),
        ):
            with self.subTest(name=name):
                for entry in jobs_dir.iterdir():
                    if entry.is_dir() and not entry.is_symlink():
                        entry.rmdir()
                    else:
                        entry.unlink()
                make(jobs_dir / f"{name}.json")
                self._assert_refused(f"job {name} (unreadable", f"remove {jobs_dir / (name + '.json')} by hand")
                pending = job.pending_reconciliation_jobs(self.runtime_root, self.managed_repo, FAKE_IDENTITY)
                self.assertEqual([entry.job_id for entry in pending], [name])

    def test_a_newer_generation_record_names_that_generation_not_abandon(self) -> None:
        self._seed("j-newer", self._launched("j-newer", controller_generation=99))
        error = self._assert_refused("Controller generation 99")
        self.assertNotIn("--abandon", error.message)

    def test_a_record_failing_validate_record_names_abandon(self) -> None:
        self._seed("j-vanished", self._launched("j-vanished", work_item_id="gone"))
        self._assert_refused("work_item_absent", f"resume --abandon j-vanished {self.root}")

    def test_terminal_records_other_targets_and_jobs_abandoned_are_not_pending(self) -> None:
        self._seed("j-finished", self._launched("j-finished", status=job.STATUS_FAILED))
        self._seed("j-other", self._launched("j-other", target_repo="/somewhere/else"))
        abandoned = self.runtime_root / "jobs" / "abandoned"
        abandoned.mkdir(parents=True)
        (abandoned / "old.json").write_text("{not json")
        self.assertEqual(job.pending_reconciliation_jobs(self.runtime_root, self.managed_repo, FAKE_IDENTITY), [])
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_INVOCATIONS_FILE": str(self.invocations)}):
            self._step(timeout=10)
        self.assertEqual(len(_read_lines(self.invocations)), 1)


class BaseVersionPlannedRecordTest(unittest.TestCase):
    """The ``PLANNED`` record the base version's ``/apply-functional-review``
    crash left behind (Migration): ``step`` refuses on it, ``resume``
    reconciles it ``INTERRUPTED`` (row 1), and the next ``step`` declines
    (CP3)."""

    def test_step_refuses_then_resume_reconciles_then_step_declines(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            managed_repo = _build_target(
                tmp_root, phase="AWAITING_FUNCTIONAL_REVIEW", governing_workflow_version="2.2",
                implementation_revision=1,
            )
            root = managed_repo.root
            (root / "docs" / "ACTIVE_MILESTONE.md").write_text("checklist content\n")
            blob = fixtures.run(["git", "hash-object", "--", "docs/ACTIVE_MILESTONE.md"], cwd=root).stdout.strip()
            fixtures.run(["git", "add", "docs/ACTIVE_MILESTONE.md"], cwd=root)
            fixtures.run(["git", "commit", "-q", "-m",
                          f"checklist\n\nWorkflow-Functional-Checklist: wi-1/1/{blob}\nWorkflow-Work-Item: wi-1\n"],
                         cwd=root)
            findings = root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.md"
            findings.parent.mkdir(parents=True, exist_ok=True)
            findings.write_text("findings\n")
            runtime_root = tmp_root / "runtime"
            _seed_job(runtime_root, {
                "schema_version": job.SCHEMA_VERSION, "job_id": "base-crash", "controller_generation": 7,
                "target_repo": str(root), "work_item_id": "wi-1",
                "observed_phase_before": "AWAITING_FUNCTIONAL_REVIEW",
                "pre_state": {"phase": "AWAITING_FUNCTIONAL_REVIEW", "governing_workflow_version": "2.2"},
                "selected_action": {"kind": "slash_command", "command": "/apply-functional-review wi-1",
                                    "automatic": True, "declined": False, "reason": "base", "evidence": []},
                "status": job.STATUS_PLANNED, "human_gate_pending": None, "handoff_pending": False,
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            })
            never = tmp_root / "never-created"
            with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_REQUIRE_FILE": str(never)}):
                with self.assertRaises(PendingJobReconciliationError) as ctx:
                    job.execute_step(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
                                     claude_bin=str(FAKE_CLAUDE))
                self.assertIn("job base-crash (PLANNED", ctx.exception.message)
                results = job.resume(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root)
                self.assertEqual([r["status"] for r in results], [job.STATUS_INTERRUPTED])
                record = job.execute_step(managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
                                          claude_bin=str(FAKE_CLAUDE))
            self.assertEqual(record["status"], job.STATUS_DECLINED)
            self.assertEqual(record["selected_action"]["command"], "/apply-functional-review wi-1")


class SpawnFlushFailureTest(_LifecycleCase):
    """The ``on_spawn`` failure path (round 4, O4): a process exists, so the
    failure is never ``WorkerLaunchError``/``WorkerNotStarted``. The group
    is killed and reaped, the original error propagates unchanged, and the
    record stays ``LAUNCHED`` with ``lifecycle_lock`` and no
    ``worker_process`` -- so the next ``step`` refuses and ``resume``
    reconciles it."""

    def test_the_flush_error_propagates_the_worker_is_ended_and_the_record_stays_launched(self) -> None:
        real_write_json = job.runtime.write_json
        failure = OSError(errno.EIO, "injected flush failure")
        captured: dict = {}

        def failing_write_json(runtime_root, rel_path, obj):
            if obj.get("status") == job.STATUS_LAUNCHED and "worker_process" in obj:
                captured["worker_process"] = obj["worker_process"]
                raise failure
            return real_write_json(runtime_root, rel_path, obj)

        env = {"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release)}
        with unittest.mock.patch.dict("os.environ", env), \
                unittest.mock.patch.object(job.runtime, "write_json", failing_write_json):
            with self.assertRaises(OSError) as ctx:
                self._step()
        self.assertIs(ctx.exception, failure)
        self.assertNotIsInstance(ctx.exception, WorkerLaunchError)
        pid = captured["worker_process"]["pid"]
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)  # killed *and* reaped before the error propagated
        [record] = self._records()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        self.assertIn("lifecycle_lock", record)
        self.assertNotIn("worker_process", record)
        self.assertNotIn("reconciliation_evidence", record)

        with self.assertRaises(PendingJobReconciliationError):
            self._step(timeout=10)
        results = job.resume(self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)
        self.assertEqual([r["status"] for r in results], [job.STATUS_INTERRUPTED])


class CtrlCTest(_LifecycleCase):
    """Ctrl-C ends only the Controller: after the ``worker_process`` flush,
    a ``KeyboardInterrupt`` writes one stderr line naming the worker's pid
    and pgid and ``workflow-controller resume``, then propagates unchanged;
    the record stays ``LAUNCHED`` and the worker keeps running."""

    def test_the_interrupt_names_the_worker_and_propagates(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        interrupt = KeyboardInterrupt()

        def launch(task, *, on_spawn, **kwargs):
            on_spawn(worker.capture_worker_process(sleeper.pid))
            raise interrupt

        stderr = io.StringIO()
        with unittest.mock.patch.object(job.worker, "launch", launch), contextlib.redirect_stderr(stderr):
            with self.assertRaises(KeyboardInterrupt) as ctx:
                self._step()
        self.assertIs(ctx.exception, interrupt)
        lines = stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn(f"the worker (pid {sleeper.pid}, process group {sleeper.pid}) keeps running", lines[0])
        self.assertIn(f"workflow-controller resume {self.root}", lines[0])
        self.assertIn(f"`workflow-controller --runtime-dir {self.runtime_root} follow {self.root}`", lines[1])
        [record] = self._records()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        self.assertEqual(record["worker_process"]["pid"], sleeper.pid)
        self.assertIsNone(sleeper.poll(), "the Controller must not end the worker")

    def test_an_interrupt_during_the_group_drain_says_the_worker_exited(self) -> None:
        # CP6 (release-runtime-observability): Ctrl-C in the drain wait,
        # after `on_group_drain` flushed -- pid P is gone, its group is not.
        sleeper = process_fixtures.spawn_sleeper(self)
        interrupt = KeyboardInterrupt()

        def launch(task, *, on_spawn, on_group_drain, **kwargs):
            on_spawn(worker.capture_worker_process(sleeper.pid))
            on_group_drain(sleeper.pid, [sleeper.pid + 1])
            raise interrupt

        stderr = io.StringIO()
        with unittest.mock.patch.object(job.worker, "launch", launch), contextlib.redirect_stderr(stderr):
            with self.assertRaises(KeyboardInterrupt) as ctx:
                self._step()
        self.assertIs(ctx.exception, interrupt)
        [interrupted] = [line for line in stderr.getvalue().splitlines() if "interrupted --" in line]
        pid = sleeper.pid
        self.assertIn(f"worker pid {pid} exited; its process group {pid} still has members running", interrupted)
        self.assertIn(f"`kill -TERM -- -{pid}`", interrupted)
        self.assertIn(f"workflow-controller resume {self.root}", interrupted)
        self.assertNotIn("keeps running", interrupted)
        self.assertNotIn(f"the worker (pid {pid}, process group {pid})", interrupted)
        self.assertIn(f"`workflow-controller --runtime-dir {self.runtime_root} follow {self.root}`",
                      stderr.getvalue())
        [record] = self._records()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)

    def test_an_interrupt_before_the_flush_prints_nothing(self) -> None:
        def launch(task, **kwargs):
            raise KeyboardInterrupt()

        stderr = io.StringIO()
        with unittest.mock.patch.object(job.worker, "launch", launch), contextlib.redirect_stderr(stderr):
            with self.assertRaises(KeyboardInterrupt):
                self._step()
        self.assertEqual(stderr.getvalue(), "")


class UnboundedDefaultTimeoutTest(_LifecycleCase):
    """Time is not termination: ``DEFAULT_WORKER_TIMEOUT`` is ``None``, and a
    worker silent for longer than a (patched) former default is still
    waited for, with no kill path run."""

    def test_the_default_is_no_limit_and_a_long_silent_worker_is_waited_for(self) -> None:
        self.assertIsNone(job.DEFAULT_WORKER_TIMEOUT)
        seen: dict = {}
        real_launch = job.worker.launch

        def spy_launch(task, **kwargs):
            seen["timeout"] = kwargs["timeout"]
            return real_launch(task, **kwargs)

        timer = threading.Timer(1.0, self.release.touch)
        self.addCleanup(timer.cancel)
        env = {"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release)}
        with unittest.mock.patch.dict("os.environ", env), \
                unittest.mock.patch.object(job.worker, "launch", spy_launch), \
                unittest.mock.patch.object(job.worker, "_kill_process_group",
                                           side_effect=AssertionError("no kill path may run")):
            timer.start()
            record = self._step()  # no timeout: the former default no longer applies
        self.assertIsNone(seen["timeout"])
        self.assertEqual(record["worker_outcome"], "SUCCESS")


class ProseOverStateTest(_LifecycleCase):
    """Durable state over prose: a ``SUCCESS`` worker whose ``result``
    claims the transition over an unchanged state is ``FAILED``, and no
    code reads ``WorkerResult.result``/``raw_json`` except ``_worker_dict``."""

    def test_a_success_claim_over_an_unchanged_state_fails(self) -> None:
        body = fake_claude.stream_text([fake_claude.result_event(
            session_id="s",
            result="Done: the work item is now at AWAITING_LOCAL_PLAN_REVIEW and the bundle is generated.",
        )])
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_STDOUT": body}):
            record = self._step(timeout=10)
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["observed_phase_after"], "PLANNING")

    def test_worker_result_prose_feeds_only_the_report(self) -> None:
        readers = []
        for path in sorted((REPO_ROOT / "controller").glob("*.py")):
            tree = ast.parse(path.read_text())
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.Attribute) and node.attr in ("result", "raw_json"):
                        readers.append((path.name, func.name, node.attr))
        self.assertEqual(sorted(set(readers)), [("job.py", "_worker_dict", "result")])


class _StreamingCase(_LifecycleCase):
    """The descendant fixture and write spy shared by the CP4 streaming
    tests and the CP5 worker-event tests."""

    def setUp(self) -> None:
        super().setUp()
        self.descendant = self.tmp_root / "descendant.json"
        self.addCleanup(self._end_descendant)

    def _end_descendant(self) -> None:
        try:
            pid = json.loads(self.descendant.read_text())["pid"]
        except (OSError, ValueError, KeyError):
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)

    def _descendant_env(self, spec: str) -> dict:
        return {"FAKE_CLAUDE_DESCENDANT": spec, "FAKE_CLAUDE_DESCENDANT_FILE": str(self.descendant)}

    def _spied_step(self, env: dict | None = None, **kwargs) -> tuple[dict, list[tuple[float, dict]]]:
        """Run one step, recording ``(time.time(), record)`` for every job
        record write, and every stream file's mode at the first
        ``LAUNCHED`` write."""
        writes: list[tuple[float, dict]] = []
        real_write_json = job.runtime.write_json

        def spy(runtime_root, rel_path, obj):
            if str(rel_path).startswith("jobs/") and obj.get("status") == job.STATUS_LAUNCHED \
                    and not any(w.get("status") == job.STATUS_LAUNCHED for _t, w in writes):
                streams = obj.get("worker_streams") or {}
                self.stream_modes = {key: stat.S_IMODE(os.stat(streams[key]).st_mode)
                                     for key in ("stdout_path", "stderr_path") if key in streams}
                self.invocations_at_launched = len(_read_lines(self.invocations))
            writes.append((time.time(), copy.deepcopy(obj)))
            return real_write_json(runtime_root, rel_path, obj)

        with unittest.mock.patch.dict("os.environ", env or {}), \
                unittest.mock.patch.object(job.runtime, "write_json", spy):
            record = self._step(**kwargs)
        return record, writes


class StreamingJobTest(_StreamingCase):
    """``workflow-controller-release-runtime-observability`` CP4: the
    worker's stream files are created before spawn and named in the
    ``LAUNCHED`` record, and the group drain is recorded and announced."""

    def test_worker_streams_is_in_the_first_launched_flush_before_spawn(self) -> None:
        record, writes = self._spied_step(timeout=10)
        launched = [w for _t, w in writes if w.get("status") == job.STATUS_LAUNCHED]
        first = launched[0]
        self.assertNotIn("worker_process", first)
        self.assertEqual(self.invocations_at_launched, 0, "the worker ran before the LAUNCHED flush")
        streams = first["worker_streams"]
        job_dir = (self.runtime_root / "jobs" / record["job_id"]).resolve()
        self.assertEqual(streams, {
            "format": "stream-json",
            "stdout_path": str(job_dir / "worker.stdout"),
            "stderr_path": str(job_dir / "worker.stderr"),
            "events_path": str(job_dir / "events.jsonl"),
        })
        self.assertEqual(self.stream_modes, {"stdout_path": 0o600, "stderr_path": 0o600})
        self.assertEqual(record["worker_streams"], streams)
        self.assertEqual(record["worker"]["stdout_path"], streams["stdout_path"])
        self.assertEqual(record["worker"]["stderr_path"], streams["stderr_path"])
        written = [json.loads(line) for line in Path(streams["stdout_path"]).read_text().splitlines()]
        self.assertEqual([event["type"] for event in written],
                         [event["type"] for event in fake_claude.default_events()])
        self.assertEqual(written[0]["cwd"], str(self.root))
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertNotIn("worker_group_drain", record)
        self.assertFalse(any("worker_group_drain" in w for _t, w in writes))

    def test_a_stream_file_creation_failure_is_worker_not_started(self) -> None:
        def preexisting(job_id: str) -> None:
            job_dir = self.runtime_root / "jobs" / job_id
            job_dir.mkdir(parents=True)
            (job_dir / "worker.stdout").write_text("")

        preexisting("20260101T000000Z-00000001")
        with unittest.mock.patch.object(job, "_new_job_id", return_value="20260101T000000Z-00000001"):
            with self.assertRaises(WorkerLaunchError) as ctx:
                self._step(timeout=10)
        [record] = self._records()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        self.assertNotIn("worker_streams", record)
        self.assertEqual(record["reconciliation_evidence"], {
            "code": job.WORKER_NOT_STARTED_CODE, "error": "WorkerLaunchError",
            "message": ctx.exception.message, "evidence": ctx.exception.evidence,
        })
        self.assertIn("worker.stdout", ctx.exception.message)
        self.assertEqual(_read_lines(self.invocations), [], "a worker was invoked")

        preexisting("20260101T000000Z-00000002")
        stderr = io.StringIO()
        with unittest.mock.patch.object(job, "_new_job_id", return_value="20260101T000000Z-00000002"), \
                unittest.mock.patch.object(cli, "_dispatch", lambda args, argv: self._step(timeout=10)), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["step", str(self.root)]), cli.EXIT_FAIL_CLOSED)
        lines = stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("error: "), lines)
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(_read_lines(self.invocations), [])

    def test_a_same_group_descendant_is_drained_visibly_and_verification_waits_for_it(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            record, writes = self._spied_step(env=self._descendant_env("group-closed:2"), timeout=30)
        descendant = json.loads(self.descendant.read_text())
        self.assertIn("exited_at", descendant)
        pid = record["worker_process"]["pid"]
        lines = stderr.getvalue().splitlines()
        self.assertEqual(lines, [
            f"worker pid {pid} exited; waiting for 1 process(es) still in its process group: {descendant['pid']}",
        ])
        drained = [w for _t, w in writes if "worker_group_drain" in w]
        self.assertEqual(drained[0]["status"], job.STATUS_LAUNCHED)
        self.assertEqual(drained[0]["worker_group_drain"]["remaining_pids"], [descendant["pid"]])
        self.assertIsInstance(drained[0]["worker_group_drain"]["direct_child_exited_at"], str)
        completed_at = next(t for t, w in writes if w.get("status") == job.STATUS_COMPLETED)
        self.assertGreater(completed_at, descendant["exited_at"])
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["worker_group_drain"], drained[0]["worker_group_drain"])

    def test_a_timeout_during_the_drain_is_interrupted_with_the_real_exit_code(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            record, _writes = self._spied_step(env=self._descendant_env("group-closed:30"), timeout=3)
        descendant = json.loads(self.descendant.read_text())
        self.assertNotIn("exited_at", descendant)

        def gone() -> bool:
            stat_ = process_fixtures.read_stat(descendant["pid"])
            return stat_ is None or stat_[0] == "Z"

        self.assertTrue(process_fixtures.wait_until(gone, timeout=5))
        self.assertEqual(record["worker_outcome"], "INTERRUPTED")
        self.assertEqual(record["worker"]["exit_code"], 0)

    def test_a_drain_line_stderr_oserror_does_not_end_the_group(self) -> None:
        class BrokenStderr(io.StringIO):
            def write(self, text):
                raise OSError(errno.EPIPE, "Broken pipe")

        with contextlib.redirect_stderr(BrokenStderr()):
            record, _writes = self._spied_step(env=self._descendant_env("group-closed:1"), timeout=30)
        self.assertIn("exited_at", json.loads(self.descendant.read_text()))
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertIn("worker_group_drain", record)

    def test_a_setsid_descendant_holding_the_lock_is_not_waited_for_and_the_next_step_exits_45(self) -> None:
        started = time.monotonic()
        record, _writes = self._spied_step(env=self._descendant_env("setsid:5"), timeout=30)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertNotIn("worker_group_drain", record)
        self.assertNotIn("exited_at", json.loads(self.descendant.read_text()))
        with self.assertRaises(LifecycleWorkerActiveError):
            self._step(timeout=10)
        stderr = io.StringIO()
        with unittest.mock.patch.object(cli, "_dispatch", lambda args, argv: self._step(timeout=10)), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["step", str(self.root)]), cli.EXIT_WORKER_ACTIVE)



# ---------------------------------------------------------------------------
# Release-runtime-observability CP5: every job-record write appends its named
# event to `jobs/<job_id>/events.jsonl`, with `seq` equal to the record's
# `event_seq` at that write.
# ---------------------------------------------------------------------------


def _job_events(runtime_root: Path, job_id: str) -> list[dict]:
    path = runtime_root / "jobs" / job_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class _EventLogAssertions:
    def _assert_log(self, runtime_root: Path, record: dict, events: list[str]) -> list[dict]:
        """The job's log is exactly ``events``, in order, ``seq`` ``1..n``
        with the last equal to the record's ``event_seq``, every line in the
        declared shape, and the final event agreeing with the status."""
        log = _job_events(runtime_root, record["job_id"])
        self.assertEqual([e["event"] for e in log], events)
        self.assertEqual([e["seq"] for e in log], list(range(1, len(events) + 1)))
        self.assertEqual(record["event_seq"], len(events))
        for line in log:
            self.assertEqual(line["v"], 1)
            self.assertEqual(line["job_id"], record["job_id"])
            self.assertRegex(line["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertNotIn("follow", line)
        self.assertNotIn("follow", record)
        final = log[-1]["event"]
        if final == "reconciled":
            self.assertEqual(log[-1]["status"], record["status"])
        elif final in ("worker_not_started", "abandoned"):
            self.assertEqual(record["status"], job.STATUS_FAILED)
        else:
            self.assertEqual(final, record["status"].lower())
        return log


class _SeqSpy:
    """Records ``(event_seq, status)`` at every job-record write, so each
    event's ``seq`` can be matched to the record written just before it."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, str]] = []
        self._original = job.runtime.write_json

    def __enter__(self) -> "_SeqSpy":
        def spy(runtime_root, rel_path, obj):
            if str(rel_path).startswith("jobs/"):
                self.writes.append((obj.get("event_seq"), obj.get("status")))
            return self._original(runtime_root, rel_path, obj)

        self._patch = unittest.mock.patch.object(job.runtime, "write_json", spy)
        self._patch.start()
        return self

    def __exit__(self, *exc) -> None:
        self._patch.stop()


class JobEventLogPathTest(_EventLogAssertions, _LifecycleCase):
    """The launch paths on the minimal ``PLANNING`` target."""

    def test_a_failed_verification_appends_each_write_in_order(self) -> None:
        with _SeqSpy() as spy:
            record = self._step(timeout=10)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        log = self._assert_log(self.runtime_root, record,
                               ["planned", "launched", "worker_spawned", "completed", "failed"])
        self.assertEqual([seq for seq, _status in spy.writes], [1, 2, 3, 4, 5])
        self.assertEqual(log[0]["command"], "/milestone-plan wi-1")
        self.assertEqual((log[2]["pid"], log[2]["pgid"]),
                         (record["worker_process"]["pid"], record["worker_process"]["pgid"]))
        self.assertEqual((log[3]["outcome"], log[3]["exit_code"]), ("SUCCESS", 0))
        self.assertEqual((log[4]["observed_phase_after"], log[4]["transition_verified"]), ("PLANNING", False))
        self.assertEqual(Path(record["worker_streams"]["events_path"]),
                         (self.runtime_root / "jobs" / record["job_id"] / "events.jsonl").resolve())

    def test_a_declined_decision_is_one_declined_event(self) -> None:
        declined = Decision(observed_phase="PLANNING", evidence=(), action=None, automatic=False,
                            gate=None, declined=True, reason="a patched decline")
        with unittest.mock.patch.object(job.evidence, "decide", return_value=declined):
            record = self._step()
        [line] = self._assert_log(self.runtime_root, record, ["declined"])
        self.assertEqual(line["reason"], "a patched decline")

    def test_a_pending_handoff_is_one_handoff_pending_event(self) -> None:
        (self.runtime_root / "handoff.json").write_text("{}\n")
        record = self._step()
        self.assertEqual(record["status"], job.STATUS_HANDOFF_PENDING)
        self._assert_log(self.runtime_root, record, ["handoff_pending"])

    def test_worker_not_started_follows_planned(self) -> None:
        with self.assertRaises(WorkerLaunchError):
            self._step(claude_bin=str(self.tmp_root / "no-such-claude"))
        [record] = self._records()
        log = self._assert_log(self.runtime_root, record, ["planned", "launched", "worker_not_started"])
        self.assertEqual(log[-1]["error"], "WorkerLaunchError")
        self.assertEqual(record["status"], job.STATUS_FAILED)

    def test_a_stream_file_failure_is_worker_not_started_after_planned(self) -> None:
        job_id = "20260101T000000Z-0000000a"
        (self.runtime_root / "jobs" / job_id).mkdir(parents=True)
        (self.runtime_root / "jobs" / job_id / "worker.stdout").write_text("")
        with unittest.mock.patch.object(job, "_new_job_id", return_value=job_id), \
                self.assertRaises(WorkerLaunchError):
            self._step(timeout=10)
        [record] = self._records()
        self._assert_log(self.runtime_root, record, ["planned", "worker_not_started"])

    def test_a_record_without_event_seq_starts_at_one_and_stays_valid(self) -> None:
        """A record in the base version's shape (no ``event_seq``) validates,
        and its first new event is ``seq`` ``1``."""
        record = self._step(timeout=10)
        base = {key: value for key, value in record.items() if key != "event_seq"}
        (self.runtime_root / "jobs" / record["job_id"] / "events.jsonl").unlink()
        self.assertTrue(job.validate_record(base, managed_repo=self.managed_repo, identity=FAKE_IDENTITY).valid)
        written = job._persist(self.runtime_root, record["job_id"], base, event="reconciled",
                               details=job._reconciled_details(base))
        self.assertEqual(written["event_seq"], 1)
        self.assertEqual([e["seq"] for e in _job_events(self.runtime_root, record["job_id"])], [1])
        self.assertTrue(job.validate_record(written, managed_repo=self.managed_repo,
                                            identity=FAKE_IDENTITY).valid)

    def test_a_keyboard_interrupt_from_the_append_propagates_after_the_record_write(self) -> None:
        record = {"job_id": "j", "status": job.STATUS_PLANNED}
        with unittest.mock.patch.object(job.runtime, "append_jsonl", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                job._persist(self.runtime_root, "j", record, event="planned")
        self.assertEqual(json.loads((self.runtime_root / "jobs" / "j.json").read_text())["event_seq"], 1)


class JobEventLogVerifiedPathTest(_EventLogAssertions, unittest.TestCase):
    """``FINISHED`` and ``GATE_BLOCKED``, on the partial-apply fixture."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)
        self.managed_repo = seed_partial_apply_plan_review(self.tmp_root)

    def _execute(self, env: dict[str, str]):
        with unittest.mock.patch.dict("os.environ", env):
            return job.execute_step(self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root,
                                    claude_bin=str(FAKE_CLAUDE), timeout=10)

    def test_a_finished_job(self) -> None:
        record = self._execute(partial_apply_plan_review_worker_env(self.managed_repo.root, manifest_revision=11))
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        log = self._assert_log(self.runtime_root, record,
                               ["planned", "launched", "worker_spawned", "completed", "finished"])
        self.assertEqual(log[-1]["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertTrue(log[-1]["transition_verified"])

    def test_a_gate(self) -> None:
        self._execute(partial_apply_plan_review_worker_env(self.managed_repo.root))
        record = self._execute({"FAKE_CLAUDE_REQUIRE_FILE": str(self.tmp_root / "never"), "FAKE_CLAUDE_WRITES": ""})
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        [line] = self._assert_log(self.runtime_root, record, ["gate_blocked"])
        self.assertEqual(line["what_is_required"], record["human_gate_pending"]["what_is_required"])
        self.assertIn("reason", line)


class WorkerEventBestEffortTest(_EventLogAssertions, _StreamingCase):
    """The ``worker_spawned``/``worker_exited`` appends run inside
    ``worker.launch``'s callbacks, whose exceptions are fatal to the worker
    -- so a failure of either is swallowed and the job ends as unforced."""

    def _failing_event(self, event: str, error: BaseException):
        real = job.runtime.append_jsonl

        def append(runtime_root, rel_path, obj):
            if obj.get("event") == event:
                raise error
            return real(runtime_root, rel_path, obj)

        return unittest.mock.patch.object(job.runtime, "append_jsonl", append)

    def test_worker_exited_follows_worker_spawned_and_precedes_completed(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            record, writes = self._spied_step(env=self._descendant_env("group-closed:1"), timeout=30)
        log = self._assert_log(self.runtime_root, record,
                               ["planned", "launched", "worker_spawned", "worker_exited", "completed", "failed"])
        exited = log[3]
        drained = next(w for _t, w in writes if "worker_group_drain" in w)
        self.assertEqual(exited["seq"], drained["event_seq"])
        self.assertEqual(exited["pid"], record["worker_process"]["pid"])
        self.assertEqual(exited["remaining_pids"], record["worker_group_drain"]["remaining_pids"])

    def test_a_failing_worker_exited_append_changes_nothing(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            unforced, _ = self._spied_step(env=self._descendant_env("group-closed:1"), timeout=30)
        self.descendant.unlink()
        stderr = io.StringIO()
        with self._failing_event("worker_exited", OSError(errno.EIO, "injected")), \
                unittest.mock.patch.object(job.runtime, "_best_effort_warned", False), \
                contextlib.redirect_stderr(stderr):
            forced, _ = self._spied_step(env=self._descendant_env("group-closed:1"), timeout=30)
        self.assertEqual((forced["status"], forced["worker_outcome"], forced["event_seq"]),
                         (unforced["status"], unforced["worker_outcome"], unforced["event_seq"]))
        self.assertEqual([e["seq"] for e in _job_events(self.runtime_root, forced["job_id"])], [1, 2, 3, 5, 6])
        self.assertEqual(sum("warning: could not write" in l for l in stderr.getvalue().splitlines()), 1)

    def test_a_failing_worker_spawned_append_neither_kills_nor_changes_the_job(self) -> None:
        unforced, _ = self._spied_step(timeout=10)
        for error in (OSError(errno.EIO, "injected"), TypeError("injected non-JSON detail")):
            with self.subTest(error=type(error).__name__):
                with self._failing_event("worker_spawned", error), \
                        unittest.mock.patch.object(job.runtime, "_best_effort_warned", False), \
                        unittest.mock.patch.object(job.worker, "_kill_process_group",
                                                   side_effect=AssertionError("no kill-and-reap may run")), \
                        contextlib.redirect_stderr(io.StringIO()):
                    forced, _ = self._spied_step(timeout=10)
                self.assertEqual((forced["status"], forced["worker_outcome"], forced["event_seq"]),
                                 (unforced["status"], unforced["worker_outcome"], unforced["event_seq"]))
                self.assertEqual(forced["worker"]["exit_code"], 0)
                self.assertEqual([e["event"] for e in _job_events(self.runtime_root, forced["job_id"])],
                                 ["planned", "launched", "completed", "failed"])


if __name__ == "__main__":
    unittest.main()
