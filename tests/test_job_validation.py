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

#: This repository's own checkout -- itself a frozen Workflow
#: installation (2.5.1 since revision 64's baseline update), per
#: ``tests/fixtures.copy_real_commands_dir``'s own docstring -- is what
#: property 5 checks its declared branches/calls against.
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
    """Eleven rows -- six matching CP4's own six automatic triples after
    revision 10's narrowing, plus row 7 (revision 63's B2, the
    `NoWorkItemYet` bootstrap, deliberately not an eighth CP4 triple since
    `NO_PHASE` is never a phase CP4's own dispatch table is keyed on), plus
    four `"2.2"` plan-review rows added by the
    `workflow-controller-protocol-2-2-compatibility` milestone's CP1 (each
    byte-identical to its `"2.1"` counterpart except for
    `governing_version`, since the two-stage plan-review protocol does not
    branch on `"2.1"` vs. `"2.2"`); the real table passes every structural
    property, and each property's own negative instantiation fails
    construction (a property that cannot fail is not a property)."""

    def test_eleven_rows(self) -> None:
        self.assertEqual(len(job.EXPECTED_OUTCOMES), 11)

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

    def test_writer_calls_disagreeing_on_branch_fails(self) -> None:
        """Property 3's total, fail-closed derivation (revision 65/66,
        step 8's own repair): a row whose ``WriterCall``s disagree on
        ``branch`` fails construction, naming the row and both values --
        a property that cannot fail is not a property, and every real row
        today declares exactly one ``WriterCall`` so this is otherwise
        untested."""
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        wc1 = row3.writer_calls[0]
        wc2 = dataclasses.replace(wc1, branch=job.BranchSpec(kind="bullet", label="APPROVE"))
        broken = dataclasses.replace(row3, writer_calls=(wc1, wc2))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("disagree on branch" in v for v in violations), violations)

    def test_row_branch_helper_raises_on_disagreement(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        wc1 = row3.writer_calls[0]
        wc2 = dataclasses.replace(wc1, branch=job.BranchSpec(kind="bullet", label="APPROVE"))
        broken = dataclasses.replace(row3, writer_calls=(wc1, wc2))
        with self.assertRaises(AssertionError):
            job._row_branch(broken)

    def test_row_with_no_writer_calls_at_all_fails(self) -> None:
        """The *other* shape that leaves property 3's derivation
        non-total: zero ``writer_calls`` derives an empty branch set
        rather than a disagreeing one, so the ``len(...) > 1`` half alone
        admits it -- and ``_row_branch`` then raises ``AssertionError`` at
        execution time on a row validation called well-formed, which is
        exactly the "validated here, crashes there" split property 3's own
        "total, fail-closed derivation" clause exists to close. Property 5
        does not catch it either (its per-``WriterCall`` loop simply does
        not run), so this property is the only place it can be reported."""
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        broken = dataclasses.replace(row3, writer_calls=())
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("no writer_calls" in v for v in violations), violations)
        # ... and the helper it protects really does raise on that row.
        with self.assertRaises(AssertionError):
            job._row_branch(broken)
        # The negative direction: property 5 is silent about the same row,
        # which is why property 3 has to speak.
        self.assertEqual(
            job.property_declaration_against_artifact_violations(REPO_ROOT, (broken,)), [],
        )

    def test_real_table_rows_each_derive_a_single_consistent_branch(self) -> None:
        for eo in job.EXPECTED_OUTCOMES:
            self.assertEqual(job._row_branch(eo), eo.writer_calls[0].branch)

    def test_postcondition_without_postcondition_phases_fails(self) -> None:
        """CP3's column shape: ``postcondition`` set with empty
        ``postcondition_phases`` fails construction."""
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(eo, postcondition_phases=frozenset())
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("non-empty iff postcondition is set" in v for v in violations), violations)

    def test_postcondition_phases_without_postcondition_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(eo, postcondition=None)
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("non-empty iff postcondition is set" in v for v in violations), violations)

    def test_postcondition_phases_outside_to_any_of_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(eo, postcondition_phases=frozenset({"REVISING_PLAN"}))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("are not members of to_any_of" in v for v in violations), violations)

    def test_postcondition_is_attached_to_exactly_the_plan_bundle_producing_rows(self) -> None:
        """CP3's attachment list: every row whose action publishes a plan
        revision and generates a plan bundle, on its plan-review-awaiting
        destination -- and no `/review-plan`/`/record-manual-plan-review`
        row (neither generates a bundle)."""
        attached = {
            (eo.from_phase, eo.governing_version, eo.action): eo.postcondition_phases
            for eo in job.EXPECTED_OUTCOMES if eo.postcondition is not None
        }
        self.assertEqual(attached, {
            ("PLANNING", "2.1", "/milestone-plan"): frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
            ("PLANNING", "2.2", "/milestone-plan"): frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
            ("PLANNING", "1", "/milestone-plan"): frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}),
            ("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review"):
                frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}),
            ("REVISING_PLAN", "2.1", "/apply-plan-review"): frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
            ("REVISING_PLAN", "2.2", "/apply-plan-review"): frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
            (job.NO_PHASE, None, "/milestone-plan"): frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}),
        })
        for eo in job.EXPECTED_OUTCOMES:
            if eo.postcondition is not None:
                self.assertIs(eo.postcondition, job._postcondition_plan_bundle_coherent)

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

    # -- the four "2.2" rows CP1 added (workflow-controller-protocol-2-2-
    # compatibility), each proven directly against evidence.decide() here
    # rather than only transitively through TwoPointTwoPlanReviewTransitionTest's
    # full execute_step path, exactly like every other row in this class.

    def test_row1_planning_22_reaches_milestone_plan(self) -> None:
        wi = fixtures.build_work_item_view(phase="PLANNING", governing_workflow_version="2.2")
        self._assert_reachable(wi, "/milestone-plan")

    def test_row3_awaiting_local_plan_review_22_reaches_review_plan(self) -> None:
        wi = fixtures.build_work_item_view(phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.2")
        self._assert_reachable(wi, "/review-plan")

    def test_row4_awaiting_manual_external_plan_review_22_reaches_record_manual_plan_review(self) -> None:
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
            phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", governing_workflow_version="2.2",
            base_commit="0" * 40,
            plan_review_stages={
                "review_content_id": "c" * 64,
                "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
            },
        )
        result = evidence.decide(managed_repo, snapshot=None, work_item=wi)
        self.assertTrue(result.automatic, result.reason)
        self.assertEqual(result.action.command.split()[0], "/record-manual-plan-review")

    def test_row6_revising_plan_22_reaches_apply_plan_review(self) -> None:
        wi = fixtures.build_work_item_view(phase="REVISING_PLAN", governing_workflow_version="2.2")
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
        env.update(fixtures.fake_worker_plan_manifest_env(managed_repo.root, "wi-1", 1))
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
        env.update(fixtures.fake_worker_plan_manifest_env(managed_repo.root, "wi-1", 1))
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
            fixtures.build_manifest_text(
                bundle_id="b" * 64, generation_head=head, stage="plan", work_item_id="wi-1", plan_revision=1,
            ),
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


class TwoPointTwoPlanReviewTransitionTest(unittest.TestCase):
    """The `"2.2"` analogues of :class:`TransitionVerificationTest`'s own
    rows 1/3/4/6 (`workflow-controller-protocol-2-2-compatibility`'s CP2):
    each of CP1's four new `"2.2"` `EXPECTED_OUTCOMES` rows, driven end to
    end through the real `execute_step`, reconciling to every member of
    its own `to_any_of` set -- the exact rows a `"2.2"`-governed item
    crashed on before CP1 (`AssertionError: decide() produced an
    automatic action with no known expected transition`)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir()

    def test_row1_planning_reaches_awaiting_local_plan_review(self) -> None:
        managed_repo = _build_target(self.tmp_root, phase="PLANNING", governing_workflow_version="2.2")
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_LOCAL_PLAN_REVIEW")
        env.update(fixtures.fake_worker_plan_manifest_env(managed_repo.root, "wi-1", 1))
        with _WriteSpy() as spy:
            record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(spy.statuses(), ["PLANNED", "LAUNCHED", "COMPLETED", "FINISHED"])

    def test_row3_ordinary_reaches_awaiting_manual_external_plan_review(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.2",
        )
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

    def test_row3_revise_reaches_revising_plan(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.2",
        )
        env = _write_state_phase_env(managed_repo.root, "wi-1", "REVISING_PLAN")
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "REVISING_PLAN")

    def test_row3_block_verdict_with_evidence_predicate_satisfied_is_finished_not_failed(self) -> None:
        """Row 3's own `"2.2"` positive predicate case, mirroring
        :meth:`TransitionVerificationTest.
        test_block_verdict_with_evidence_predicate_satisfied_is_finished_not_failed`
        exactly, but for a `"2.2"`-governed item -- a `/review-plan`
        worker that stays at the pre-phase while writing a current-round
        `Status: BLOCK` feedback verifies."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.2",
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

    def test_row4_awaiting_manual_external_plan_review_reaches_awaiting_plan_approval(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", governing_workflow_version="2.2",
            base_commit="0" * 40, current_bundle_id="b" * 64,
            plan_review_stages={
                "review_content_id": "c" * 64,
                "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
            },
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=fixtures.current_head(root)),
        )
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_PLAN_APPROVAL")
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_PLAN_APPROVAL")

    def test_row4_revise_reaches_revising_plan(self) -> None:
        """Row 4's other `to_any_of` member -- a `Status: REVISE` manual
        external plan verdict routes the same phase to `REVISING_PLAN`
        instead of `AWAITING_PLAN_APPROVAL`. This is the seventh member
        across the four `"2.2"` rows' `to_any_of` sets; the other six are
        covered by this class's other six tests, one-to-one."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", governing_workflow_version="2.2",
            base_commit="0" * 40, current_bundle_id="b" * 64,
            plan_review_stages={
                "review_content_id": "c" * 64,
                "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
            },
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=fixtures.current_head(root)),
        )
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        env = _write_state_phase_env(managed_repo.root, "wi-1", "REVISING_PLAN")
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "REVISING_PLAN")

    def test_row6_revising_plan_reaches_awaiting_local_plan_review(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="REVISING_PLAN", governing_workflow_version="2.2",
        )
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_LOCAL_PLAN_REVIEW")
        env.update(fixtures.fake_worker_plan_manifest_env(managed_repo.root, "wi-1", 1))
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")


# ---------------------------------------------------------------------------
# Row 7 (the `NoWorkItemYet` bootstrap, revision 63's B2) through steps
# 7-9: this checkpoint's own revalidation. CP6's own steps 1-6 (durable
# PLANNED/LAUNCHED/COMPLETED flushes for this row, without crashing) are
# `tests/test_job.py::BootstrapRowSevenTest`'s scope, not this file's.
# ---------------------------------------------------------------------------


def _build_bootstrap_target(tmp_root: Path):
    root = tmp_root / "target"
    fixtures.build_target_git_repo(root)
    (root / "README.md").write_text("target fixture\n")
    fixtures.commit_all(root, "initial")
    fixtures.write_workflow_state(root, {
        "schema_version": 1, "active_work_item_id": None, "work_items": {},
    })
    fixtures.copy_real_commands_dir(root / ".claude" / "commands")
    return root, fixtures.build_target_managed_repository(root)


def _write_new_work_items_env(root: Path, *new_ids: str, phase: str = "AWAITING_LOCAL_PLAN_REVIEW") -> dict[str, str]:
    """``FAKE_CLAUDE_WRITE_PATH``/``FAKE_CLAUDE_WRITE_TEXT`` env overrides
    that make the fake worker create ``new_ids`` as brand-new
    ``work_items`` entries -- row 7's own bootstrap action, simulated the
    same way ``_write_state_phase_env`` simulates an ordinary phase
    edit."""
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = json.loads(state_path.read_text())
    for new_id in new_ids:
        state["work_items"][new_id] = {
            "work_item_type": "product",
            "work_item_kind": "product",
            "work_item_id": new_id,
            "governing_workflow_version": "2.1",
            "phase": phase,
            "plan_revision": 1,
            "implementation_revision": None,
            "state_revision": 1,
            "checkpoints": {},
            "current_bundle_id": None,
            "last_completed_checkpoint_id": None,
            "base_commit": None,
            "parent_work_item_id": None,
        }
    return {
        "FAKE_CLAUDE_WRITE_PATH": str(state_path),
        "FAKE_CLAUDE_WRITE_TEXT": json.dumps(state, indent=2) + "\n",
    }


class BootstrapRowSevenVerificationTest(unittest.TestCase):
    """Step 8's rule, keyed on row 7's own branch (``None`` -- its single
    ``WriterCall`` declares no branch -- so its predicate is evaluated
    **unconditionally**, never gated on ``NO_PHASE`` equalling a real
    observed phase, which it never can): exactly one new ``work_items``
    key, at ``AWAITING_LOCAL_PLAN_REVIEW``, verifies; zero or two new
    keys must not -- the exact "zero-key and two-key worker" cases the
    plan's own CP6B section names as required (revision 65's repair
    note)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir()
        self.root, self.managed_repo = _build_bootstrap_target(self.tmp_root)

    def test_exactly_one_new_work_item_verifies_and_reaches_finished(self) -> None:
        env = _write_new_work_items_env(self.root, "wi-new")
        env.update(fixtures.fake_worker_plan_manifest_env(self.root, "wi-new", 1))
        record = _run(self.managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertNotIn("reconciliation_evidence", record)

    def test_zero_new_work_items_does_not_verify(self) -> None:
        record = _run(self.managed_repo, self.runtime_root)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["code"], "TransitionNotObservedError")
        self.assertEqual(ev["reason"], "phase_not_in_to_any_of")
        self.assertEqual(ev["observed_phase"], "__NO_PHASE__")

    def test_two_new_work_items_does_not_verify(self) -> None:
        env = _write_new_work_items_env(self.root, "wi-new-1", "wi-new-2")
        record = _run(self.managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "phase_not_in_to_any_of")
        self.assertEqual(ev["observed_phase"], "__NO_PHASE__")


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


# ---------------------------------------------------------------------------
# CP3 (`workflow-controller-worker-execution-hardening`) -- the plan-bundle
# postcondition, end to end through `execute_step`, per row group.
# ---------------------------------------------------------------------------


#: One representative per postcondition-bearing row: ``(from_phase,
#: governing_workflow_version, post_phase)``. Row 7 (`NO_PHASE`) is covered
#: by :class:`PlanBundlePostconditionRowSevenTest` below.
_POSTCONDITION_ROWS = (
    ("PLANNING", "2.1", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("PLANNING", "2.2", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("PLANNING", "1", "AWAITING_EXTERNAL_PLAN_REVIEW"),
    ("REVISING_PLAN", "2.1", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("REVISING_PLAN", "2.2", "AWAITING_LOCAL_PLAN_REVIEW"),
    ("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "AWAITING_EXTERNAL_PLAN_REVIEW"),
)


def _seed_postcondition_row(tmp_root: Path, from_phase: str, version: str):
    """A target at ``from_phase`` whose current plan bundle is coherent
    with its own ``plan_revision: 1`` -- plus, for the `"1"`
    `/apply-plan-review` row, the `REVISE` feedback that selects it."""
    managed_repo = _build_target(tmp_root, phase=from_phase, governing_workflow_version=version)
    root = managed_repo.root
    fixtures.write_plan_manifest(root, "wi-1", 1, generation_head=fixtures.current_head(root))
    if from_phase == "AWAITING_EXTERNAL_PLAN_REVIEW":
        fixtures.write_review_feedback(
            root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="b" * 64,
                reviewed_base_commit=fixtures.current_head(root),
            ),
        )
    return managed_repo


def _publishing_worker_env(root: Path, from_phase: str, post_phase: str, *, manifest_revision: int | None):
    """A fake worker that publishes ``plan_revision: 2`` at ``post_phase``
    (the state half of the command) and, when ``manifest_revision`` is not
    ``None``, writes a plan manifest at that revision (the generator half).
    For the `"1"` `/apply-plan-review` row it also regenerates
    ``CHANGED_FILES.txt`` so row 5's own digest predicate holds and the
    postcondition is the only clause under test."""
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = json.loads(state_path.read_text())
    state["work_items"]["wi-1"]["phase"] = post_phase
    state["work_items"]["wi-1"]["plan_revision"] = 2
    writes = [{"path": str(state_path), "text": json.dumps(state, indent=2) + "\n"}]
    current = root / ".ai-review" / "wi-1" / "current"
    if manifest_revision is not None:
        writes.append({
            "path": str(current / "MANIFEST.md"),
            "text": fixtures.build_plan_manifest_text(
                "wi-1", manifest_revision, generation_head=fixtures.current_head(root),
            ),
        })
    if from_phase == "AWAITING_EXTERNAL_PLAN_REVIEW":
        writes.append({"path": str(current / "CHANGED_FILES.txt"), "text": "generated: round 2\nREADME.md\n"})
    return {"FAKE_CLAUDE_WRITES": json.dumps(writes)}


class PlanBundlePostconditionExecuteTest(unittest.TestCase):
    """`execute_step` per postcondition-bearing row: ``FINISHED`` when the
    worker leaves a plan bundle coherent with the published revision,
    ``FAILED`` (``postcondition_not_satisfied``, naming both revisions)
    when the bundle is still the previous revision's."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

    def _execute(self, name: str, from_phase: str, version: str, post_phase: str, manifest_revision):
        managed_repo = _seed_postcondition_row(self.tmp_root / name, from_phase, version)
        runtime_root = self.tmp_root / name / "runtime"
        runtime_root.mkdir(parents=True)
        env = _publishing_worker_env(
            managed_repo.root, from_phase, post_phase, manifest_revision=manifest_revision,
        )
        return _run(managed_repo, runtime_root, env_overrides=env)

    def test_coherent_manifest_finishes(self) -> None:
        for i, (from_phase, version, post_phase) in enumerate(_POSTCONDITION_ROWS):
            with self.subTest(from_phase=from_phase, version=version):
                record = self._execute(f"ok-{i}", from_phase, version, post_phase, 2)
                self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
                self.assertTrue(record["transition_verified"])
                self.assertEqual(record["observed_phase_after"], post_phase)

    def test_stale_manifest_fails_with_postcondition_not_satisfied(self) -> None:
        for i, (from_phase, version, post_phase) in enumerate(_POSTCONDITION_ROWS):
            with self.subTest(from_phase=from_phase, version=version):
                record = self._execute(f"stale-{i}", from_phase, version, post_phase, None)
                self.assertEqual(record["status"], job.STATUS_FAILED)
                self.assertFalse(record["transition_verified"])
                self.assertEqual(record["observed_phase_after"], post_phase)
                ev = record["reconciliation_evidence"]
                self.assertEqual(ev["code"], "TransitionNotObservedError")
                self.assertEqual(ev["reason"], "postcondition_not_satisfied")
                self.assertEqual(ev["observed_phase"], post_phase)
                self.assertEqual(
                    ev["postcondition_detail"], "manifest plan_revision 1 != state plan_revision 2",
                )

    def test_other_failure_reasons_carry_no_postcondition_detail(self) -> None:
        managed_repo = _seed_postcondition_row(self.tmp_root / "nothing", "PLANNING", "2.2")
        runtime_root = self.tmp_root / "nothing" / "runtime"
        runtime_root.mkdir(parents=True)
        record = _run(managed_repo, runtime_root)
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "phase_not_in_to_any_of")
        self.assertNotIn("postcondition_detail", ev)


class PlanBundlePostconditionRowSevenTest(unittest.TestCase):
    """Row 7 (`NoWorkItemYet`): the postcondition resolves the new work
    item as the single new ``work_items`` key, never ``work_item_id``
    (which is ``None``); zero or two new keys are "not satisfied"."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir()
        self.root, self.managed_repo = _build_bootstrap_target(self.tmp_root)

    def test_new_work_item_without_a_plan_bundle_fails_postcondition(self) -> None:
        env = _write_new_work_items_env(self.root, "wi-new")
        record = _run(self.managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "postcondition_not_satisfied")
        self.assertIn(".ai-review/wi-new/current", ev["postcondition_detail"])

    def test_new_work_item_with_a_stale_plan_bundle_fails_postcondition(self) -> None:
        env = _write_new_work_items_env(self.root, "wi-new")
        env.update(fixtures.fake_worker_plan_manifest_env(self.root, "wi-new", 0))
        record = _run(self.managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], "postcondition_not_satisfied")
        self.assertEqual(ev["postcondition_detail"], "manifest plan_revision 0 != state plan_revision 1")

    def test_postcondition_with_zero_or_two_new_keys_is_not_satisfied(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-a", 1)
        pre_state = {"pre_work_item_keys": []}
        satisfied, detail = job._postcondition_plan_bundle_coherent(self.root, None, pre_state)
        self.assertFalse(satisfied)
        self.assertIn("found 0", detail)

        state_path = self.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        env = _write_new_work_items_env(self.root, "wi-a", "wi-b")
        state_path.write_text(env["FAKE_CLAUDE_WRITE_TEXT"])
        fixtures.write_plan_manifest(self.root, "wi-b", 1)
        satisfied, detail = job._postcondition_plan_bundle_coherent(self.root, None, pre_state)
        self.assertFalse(satisfied)
        self.assertIn("found 2", detail)

        # Positive control: the same two-key state, with one key already
        # pre-existing, resolves the single new key and is satisfied.
        satisfied, _detail = job._postcondition_plan_bundle_coherent(
            self.root, None, {"pre_work_item_keys": ["wi-a"]},
        )
        self.assertTrue(satisfied)

    def test_postcondition_with_an_unreadable_post_state_is_not_satisfied(self) -> None:
        (self.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text("{not json")
        satisfied, detail = job._postcondition_plan_bundle_coherent(self.root, "wi-1", {})
        self.assertFalse(satisfied)
        self.assertIn("post-state could not be read", detail)

    def test_postcondition_for_an_absent_work_item_is_not_satisfied(self) -> None:
        satisfied, detail = job._postcondition_plan_bundle_coherent(self.root, "wi-missing", {})
        self.assertFalse(satisfied)
        self.assertIn("absent from the post-state", detail)


if __name__ == "__main__":
    unittest.main()
