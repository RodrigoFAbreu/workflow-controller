"""Tests for job execution, part 2 (CP6B, ``controller.job``).

Covers exactly what CP6B owns: steps 7-9 of ``execute_step`` (the fresh
post-state re-read, the ``transition_verified`` rule stated in full at
step 8, and ``FINISHED``/``FAILED``/``INCOMPLETE`` recording), plus the
six properties the plan's own ``ExpectedOutcome`` table states (CP6B,
"CP6B -- Job execution, part 2", "Six properties are asserted over this
table, not per row"): coverage, pair-keyed writer reachability, predicate
presence and validity, record completeness, declaration against artifact,
and completion.

CP6's own steps 1-6 are covered by ``tests/test_job.py`` and are not
re-asserted here.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import evidence, job  # noqa: E402
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

#: This repository's own checkout -- itself a frozen Workflow v2.3.1
#: installation, per ``tests/fixtures.copy_real_commands_dir``'s own
#: docstring -- is what property 5 checks its declared branches/calls
#: against.
REPO_ROOT = fixtures.REPO_ROOT


class _WriteSpy:
    """Records every ``(rel_path, obj)`` pair passed to
    ``controller.job.runtime.write_json`` while installed, and still
    performs the real write -- the same technique ``tests/test_job.py``
    uses."""

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


def _build_target(
    tmp_root: Path, *, phase: str, governing_workflow_version: str | None = "2.1",
    work_item_id: str = "wi-1", copy_commands: bool = True, **work_item_overrides,
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
    if copy_commands:
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")
    return fixtures.build_target_managed_repository(root)


def _run(managed_repo, runtime_root: Path, *, env_overrides: dict | None = None, timeout=10):
    env_overrides = dict(env_overrides or {})
    old = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    try:
        return job.execute_step(
            managed_repo, identity=FAKE_IDENTITY, runtime=runtime_root,
            claude_bin=str(FAKE_CLAUDE), timeout=timeout,
        )
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _write_state_phase_env(root: Path, work_item_id: str, new_phase: str) -> dict[str, str]:
    """``FAKE_CLAUDE_WRITE_PATH``/``FAKE_CLAUDE_WRITE_TEXT`` env overrides
    that make the fake worker itself perform ``WORKFLOW_STATE.json``'s own
    phase edit -- "a fake worker that performs the expected state edit"
    (CP6B's own tests list)."""
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = json.loads(state_path.read_text())
    state["work_items"][work_item_id]["phase"] = new_phase
    return {
        "FAKE_CLAUDE_WRITE_PATH": str(state_path),
        "FAKE_CLAUDE_WRITE_TEXT": json.dumps(state, indent=2) + "\n",
    }


# ---------------------------------------------------------------------------
# Properties 1, 3, 4, 6 -- structural checks over EXPECTED_OUTCOMES itself.
# ---------------------------------------------------------------------------


class ExpectedOutcomesTableStructureTest(unittest.TestCase):
    """Six rows, matching CP4's own six automatic triples after revision
    10's narrowing; the real table passes every structural property, and
    each property's own negative instantiation fails construction (a
    property that cannot fail is not a property)."""

    def test_six_rows(self) -> None:
        self.assertEqual(len(job.EXPECTED_OUTCOMES), 6)

    def test_real_table_passes_coverage_and_predicate_validity(self) -> None:
        self.assertEqual(job.property_table_violations(), [])

    def test_real_table_passes_record_completeness(self) -> None:
        self.assertEqual(job.property_record_completeness_violations(), [])

    def test_coverage_negative_duplicate_triple_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]
        violations = job.property_table_violations((eo, eo))
        self.assertTrue(any("duplicate" in v for v in violations), violations)

    def test_own_phase_reachable_without_predicate_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]  # row 1: predicate=None today
        broken = dataclasses.replace(eo, to_any_of=frozenset({eo.from_phase}))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("declares no predicate" in v for v in violations), violations)

    def test_predicate_without_own_phase_reachability_fails(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        broken = dataclasses.replace(row3, to_any_of=frozenset({"SOME_OTHER_PHASE"}))
        violations = job.property_table_violations((broken,))
        self.assertTrue(
            any("predicate declared but to_any_of never contains from_phase" in v for v in violations),
            violations,
        )

    def test_predicate_inputs_without_predicate_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(eo, predicate_inputs=frozenset({"bundle_id"}))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("predicate_inputs declared with no predicate" in v for v in violations), violations)

    def test_predicate_bearing_row_with_non_completion_writer_kind_fails(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        bad_wc = dataclasses.replace(row3.writer_calls[0], kind=job.WRITER_KIND_CONDITIONAL)
        broken = dataclasses.replace(row3, writer_calls=(bad_wc,))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("non-COMPLETION" in v for v in violations), violations)

    def test_record_completeness_negative_input_outside_pre_state_fields_fails(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        broken = dataclasses.replace(row3, predicate_inputs=frozenset({"not_a_real_field"}))
        violations = job.property_record_completeness_violations((broken,))
        self.assertTrue(any("not_a_real_field" in v for v in violations), violations)


# ---------------------------------------------------------------------------
# Property 5 -- declaration against artifact.
# ---------------------------------------------------------------------------


class DeclarationAgainstArtifactTest(unittest.TestCase):
    """CP6B's own resolution of "row 5 stays unresolved": row 5's branch
    is restated to point at step 5's own text directly (a locatable
    numbered-step span), never a "steps N-M execute" cross-reference."""

    def test_real_table_passes_against_the_frozen_command_files(self) -> None:
        self.assertEqual(job.property_declaration_against_artifact_violations(REPO_ROOT), [])

    def test_row5_branch_is_step5s_own_span(self) -> None:
        row5 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/apply-plan-review"
                    and eo.governing_version == "1")
        wc = row5.writer_calls[0]
        self.assertEqual(wc.branch.kind, "step")
        self.assertEqual(wc.branch.label, "5")

    def test_row3_branch_is_the_block_bullet(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        wc = row3.writer_calls[0]
        self.assertEqual(wc.branch.kind, "bullet")
        self.assertEqual(wc.branch.label, "BLOCK")

    def test_unlocatable_branch_fails_naming_the_row_and_branch(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        bad_branch = job.BranchSpec(kind="bullet", label="NO_SUCH_VERDICT")
        bad_wc = dataclasses.replace(row3.writer_calls[0], branch=bad_branch)
        broken = dataclasses.replace(row3, writer_calls=(bad_wc,))
        violations = job.property_declaration_against_artifact_violations(REPO_ROOT, (broken,))
        self.assertTrue(any("declared branch" in v and "not found" in v for v in violations), violations)

    def test_call_not_found_in_branch_fails(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        bad_wc = dataclasses.replace(row3.writer_calls[0], match_text="no_such_writer_call_at_all(")
        broken = dataclasses.replace(row3, writer_calls=(bad_wc,))
        violations = job.property_declaration_against_artifact_violations(REPO_ROOT, (broken,))
        self.assertTrue(any("no call found" in v for v in violations), violations)

    def test_a_further_different_durable_write_after_the_call_fails(self) -> None:
        # milestone-plan.md calls several workflow_state.* writers before
        # its own final `publish_plan_revision` -- pointing this row's own
        # declared call at an *earlier* one (`generate_registry`) means a
        # later, *different* writer call genuinely follows it in the same
        # (branch=None, whole-file) span, which the property must catch.
        row1 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/milestone-plan"
                    and eo.governing_version == "2.1")
        bad_wc = dataclasses.replace(
            row1.writer_calls[0], function="generate_registry", match_text="generate_registry(",
        )
        broken = dataclasses.replace(row1, writer_calls=(bad_wc,))
        violations = job.property_declaration_against_artifact_violations(REPO_ROOT, (broken,))
        self.assertTrue(any("a further durable write" in v for v in violations), violations)

    def test_unreadable_file_fails_naming_the_row_and_file(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        bad_wc = dataclasses.replace(row3.writer_calls[0], file="no-such-command-file.md")
        broken = dataclasses.replace(row3, writer_calls=(bad_wc,))
        violations = job.property_declaration_against_artifact_violations(REPO_ROOT, (broken,))
        self.assertTrue(any("could not be read" in v for v in violations), violations)


# ---------------------------------------------------------------------------
# Property 2 -- pair-keyed writer reachability, against the real decision
# engine (controller.evidence.decide), not a second declaration.
# ---------------------------------------------------------------------------


class PairKeyedReachabilityTest(unittest.TestCase):
    """Every EXPECTED_OUTCOMES row's own ``(from_phase,
    governing_version)``, decided for real with the ordinary-case fixture,
    yields ``automatic=True`` and the row's own declared ``action``."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

    def _managed_repo(self):
        root = self.tmp_root / "target"
        fixtures.build_target_git_repo(root)
        (root / "README.md").write_text("target fixture\n")
        fixtures.commit_all(root, "initial")
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")
        return root, fixtures.build_target_managed_repository(root)

    def _assert_reachable(self, work_item, expected_command_token: str) -> None:
        _root, managed_repo = self._managed_repo()
        result = evidence.decide(managed_repo, snapshot=None, work_item=work_item)
        self.assertTrue(result.automatic, result.reason)
        self.assertEqual(result.action.command.split()[0], expected_command_token)

    def test_row1_planning_21_reaches_milestone_plan(self) -> None:
        wi = fixtures.build_work_item_view(phase="PLANNING", governing_workflow_version="2.1")
        self._assert_reachable(wi, "/milestone-plan")

    def test_row2_planning_1_reaches_milestone_plan(self) -> None:
        wi = fixtures.build_work_item_view(phase="PLANNING", governing_workflow_version="1")
        self._assert_reachable(wi, "/milestone-plan")

    def test_row3_awaiting_local_plan_review_reaches_review_plan(self) -> None:
        wi = fixtures.build_work_item_view(phase="AWAITING_LOCAL_PLAN_REVIEW")
        self._assert_reachable(wi, "/review-plan")

    def test_row4_awaiting_manual_external_plan_review_reaches_record_manual_plan_review(self) -> None:
        root, managed_repo = self._managed_repo()
        head = fixtures.current_head(root)
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=head),
        )
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        wi = fixtures.build_work_item_view(
            phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", base_commit="0" * 40,
            plan_review_stages={
                "review_content_id": "c" * 64,
                "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
            },
        )
        result = evidence.decide(managed_repo, snapshot=None, work_item=wi)
        self.assertTrue(result.automatic, result.reason)
        self.assertEqual(result.action.command.split()[0], "/record-manual-plan-review")

    def test_row5_awaiting_external_plan_review_v1_reaches_apply_plan_review(self) -> None:
        root, managed_repo = self._managed_repo()
        head = fixtures.current_head(root)
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=head),
        )
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
            ),
        )
        wi = fixtures.build_work_item_view(
            phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
            base_commit="0" * 40,
        )
        result = evidence.decide(managed_repo, snapshot=None, work_item=wi)
        self.assertTrue(result.automatic, result.reason)
        self.assertEqual(result.action.command.split()[0], "/apply-plan-review")

    def test_row6_revising_plan_reaches_apply_plan_review(self) -> None:
        wi = fixtures.build_work_item_view(phase="REVISING_PLAN")
        self._assert_reachable(wi, "/apply-plan-review")


# ---------------------------------------------------------------------------
# Steps 7-9, end to end: the transition_verified rule (step 8) and
# FINISHED/FAILED/INCOMPLETE recording (step 9), through the real
# execute_step -- a fake worker (fake_claude.py) standing in for the
# spawned Claude process, per this repository's own established
# technique (tests/test_job.py).
# ---------------------------------------------------------------------------


class TransitionVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir()

    def test_successful_worker_reaches_finished_with_exact_status_sequence(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_LOCAL_PLAN_REVIEW")
        with _WriteSpy() as spy:
            record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertNotIn("reconciliation_evidence", record)
        # CP6 asserts the first three as a prefix; CP6B owns this
        # extension to the fourth (the same `runtime.write_json` path
        # step 9 appends through).
        self.assertEqual(spy.statuses(), ["PLANNED", "LAUNCHED", "COMPLETED", "FINISHED"])

    def test_worker_that_changes_nothing_fails_with_transition_not_observed(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        record = _run(managed_repo, self.runtime_root)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["code"], "TransitionNotObservedError")
        self.assertEqual(ev["reason"], "phase_not_in_to_any_of")
        self.assertEqual(ev["observed_phase"], "PLANNING")
        self.assertEqual(ev["expected_to_any_of"], ["AWAITING_LOCAL_PLAN_REVIEW"])

    def test_worker_that_moves_the_phase_somewhere_else_fails_naming_both_phases(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        env = _write_state_phase_env(managed_repo.root, "wi-1", "SELF_REVIEWING_IMPLEMENTATION")
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "phase_not_in_to_any_of")
        self.assertEqual(ev["observed_phase"], "SELF_REVIEWING_IMPLEMENTATION")
        self.assertEqual(ev["expected_to_any_of"], ["AWAITING_LOCAL_PLAN_REVIEW"])

    def test_ambiguous_worker_is_not_verified_even_when_poststate_matches(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_LOCAL_PLAN_REVIEW")
        env["FAKE_CLAUDE_STDOUT"] = "not valid JSON at all"
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        self.assertEqual(record["worker_outcome"], "AMBIGUOUS")
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "worker_outcome")

    def test_interrupted_worker_with_completed_transition_is_verified(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_LOCAL_PLAN_REVIEW")
        env["FAKE_CLAUDE_SELF_TERM"] = "1"
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["worker_outcome"], "INTERRUPTED")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])

    def test_block_verdict_with_evidence_predicate_satisfied_is_finished_not_failed(self) -> None:
        """Row 3's own positive predicate case: a `/review-plan` worker
        that stays at the pre-phase but writes a current-round `Status:
        BLOCK` feedback verifies -- a BLOCK-shaped no-transition outcome
        reported as success, never failure."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.1",
            current_bundle_id="b" * 64,
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=fixtures.current_head(root)),
        )
        feedback_path = root / ".ai-review" / "wi-1" / "feedback" / "REVIEW_FEEDBACK.md"
        feedback_text = fixtures.build_review_feedback_text(
            status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW",
            reviewed_bundle_id="b" * 64,
        )
        env = {"FAKE_CLAUDE_WRITE_PATH": str(feedback_path), "FAKE_CLAUDE_WRITE_TEXT": feedback_text}
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")

    def test_block_verdict_case_with_no_feedback_written_fails_predicate(self) -> None:
        """Row 3's own negative predicate case: a worker that exits 0
        writing nothing, while staying at the pre-phase, does not
        verify."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.1",
            current_bundle_id="b" * 64,
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=fixtures.current_head(root)),
        )
        record = _run(managed_repo, self.runtime_root)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "predicate_not_satisfied")

    def test_row5_bundle_regenerated_verifies(self) -> None:
        """Row 5's own positive predicate case: a `"1"`-governed
        `/apply-plan-review` worker whose regeneration actually changes
        `bundle_generated_digest` verifies, staying at the pre-phase."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
        )
        root = managed_repo.root
        head = fixtures.current_head(root)
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=head),
        )
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="b" * 64, reviewed_base_commit=head,
            ),
        )
        changed_files_path = root / ".ai-review" / "wi-1" / "current" / "CHANGED_FILES.txt"
        env = {
            "FAKE_CLAUDE_WRITE_PATH": str(changed_files_path),
            "FAKE_CLAUDE_WRITE_TEXT": "generated: round 2\nREADME.md\n",
        }
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_EXTERNAL_PLAN_REVIEW")

    def test_row5_mid_action_case_unchanged_bundle_fails_predicate(self) -> None:
        """Row 5's own mid-action negative case: a `"1"` worker that
        exits 0 without ever completing the regeneration (the bundle's
        own generated digest is unchanged) must not verify."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
        )
        root = managed_repo.root
        head = fixtures.current_head(root)
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=head),
        )
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="b" * 64, reviewed_base_commit=head,
            ),
        )
        record = _run(managed_repo, self.runtime_root)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "predicate_not_satisfied")


class IncompleteEffectPhaseTest(unittest.TestCase):
    """`INCOMPLETE` is a defined, terminal status (step 9) that takes
    precedence over step 8's own "otherwise" -- unreachable through any
    of Generation 1's own six ``EXPECTED_OUTCOMES`` rows (revision 10's
    narrowing removed its sole producer, `/apply-implementation-review`
    reaching `APPLYING_REVIEW_FEEDBACK`), so this exercises the mechanism
    directly against a synthetic effect-only landing, mirroring the
    module's own real dispatch key exactly."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir()

    def test_landing_at_a_declared_effect_only_phase_is_incomplete_not_failed(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.1")
        env = _write_state_phase_env(managed_repo.root, "wi-1", "SELF_REVIEWING_PLAN")
        key = ("PLANNING", "2.1", "/milestone-plan")
        original = job._INCOMPLETE_EFFECT_PHASES
        job._INCOMPLETE_EFFECT_PHASES = {**original, key: frozenset({"SELF_REVIEWING_PLAN"})}
        try:
            record = _run(managed_repo, self.runtime_root, env_overrides=env)
        finally:
            job._INCOMPLETE_EFFECT_PHASES = original
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertFalse(record["transition_verified"])
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["code"], "IncompleteEffectPhase")
        self.assertEqual(ev["observed_phase"], "SELF_REVIEWING_PLAN")
        self.assertIn("worker_stdout", ev)

    def test_no_generation_1_row_declares_an_effect_only_phase(self) -> None:
        self.assertEqual(job._INCOMPLETE_EFFECT_PHASES, {})


if __name__ == "__main__":
    unittest.main()
