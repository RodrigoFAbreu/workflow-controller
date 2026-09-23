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
import dataclasses
import json
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import decision, evidence, job, target_state  # noqa: E402
from controller.decision import Action, NO_PHASE, Decision, phase_from_wire  # noqa: E402
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
        pending = self._record("still-launched", job.STATUS_LAUNCHED, created_at="2026-01-09T00:00:00Z")
        with self.subTest(case="alone"):
            self._execute(seeded=(pending,), launches=True)
        with self.subTest(case="in front of an earlier terminal attempt"):
            record, _diag, _runtime = self._execute(
                seeded=(pending, self._record("earlier-apply", job.STATUS_FAILED)), launches=False,
            )
            self.assertIn("job earlier-apply, ended FAILED", record["human_gate_pending"]["what_is_required"])

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


if __name__ == "__main__":
    unittest.main()
