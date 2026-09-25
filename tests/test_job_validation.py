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
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import evidence, job  # noqa: E402
from controller.decision import Action, Decision  # noqa: E402
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
    pinned_at="2024-01-01T00:00:00Z", version=fixtures.CONTROLLER_VERSION,
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


def assert_launched_status_sequence(test: unittest.TestCase, statuses: list[str], final: str) -> None:
    """``PLANNED -> LAUNCHED (x2+) -> COMPLETED -> final``
    (worker-lifecycle-ownership plan D): the record stays ``LAUNCHED``
    through every ``worker_state`` flush, so the number of ``LAUNCHED``
    writes is at least the pre-spawn and ``on_spawn`` pair, never fixed."""
    collapsed = [status for index, status in enumerate(statuses) if index == 0 or statuses[index - 1] != status]
    test.assertEqual(collapsed, ["PLANNED", "LAUNCHED", "COMPLETED", final], statuses)
    test.assertGreaterEqual(statuses.count("LAUNCHED"), 2, statuses)


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

    def test_eighteen_rows(self) -> None:
        # Eleven until `workflow-controller-automatic-lifecycle-orchestration`
        # CP2 added the seven implementation-stage rows (12-18).
        self.assertEqual(len(job.EXPECTED_OUTCOMES), 18)

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

    def test_postconditions_entry_with_an_empty_phase_set_fails(self) -> None:
        """CP2's per-phase shape (the successor of CP3's "non-empty iff
        set" rule): an entry whose phase set is empty can never apply."""
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(eo, postconditions=((frozenset(), job._postcondition_plan_bundle_coherent),))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("declares an empty phase set" in v for v in violations), violations)

    def test_postconditions_entry_without_a_callable_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(eo, postconditions=((frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}), None),))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("declares no callable postcondition" in v for v in violations), violations)

    def test_postconditions_entry_that_is_not_a_pair_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(eo, postconditions=(job._postcondition_plan_bundle_coherent,))
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("is not a (frozenset of phases, postcondition) pair" in v for v in violations),
                        violations)

    def test_postconditions_stray_phase_outside_to_any_of_fails(self) -> None:
        eo = job.EXPECTED_OUTCOMES[0]
        broken = dataclasses.replace(
            eo, postconditions=((frozenset({"REVISING_PLAN"}), job._postcondition_plan_bundle_coherent),),
        )
        violations = job.property_table_violations((broken,))
        self.assertTrue(any("are not members of to_any_of" in v for v in violations), violations)

    def test_postconditions_overlapping_phase_sets_fail(self) -> None:
        row16 = job._EXPECTED_OUTCOMES_BY_KEY[
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation")
        ]
        (approve_phases, approve), (revise_phases, revise) = row16.postconditions
        broken = dataclasses.replace(row16, postconditions=(
            (approve_phases, approve), (revise_phases | approve_phases, revise),
        ))
        violations = job.property_table_violations((broken,))
        self.assertTrue(
            any("overlap on ['AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW']" in v for v in violations),
            violations,
        )
        # The real row's two disjoint sets are clean.
        self.assertEqual(job.property_table_violations((row16,)), [])

    def test_a_row_may_declare_no_postconditions(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        self.assertEqual(row3.postconditions, ())
        self.assertEqual(job.property_table_violations((row3,)), [])

    def test_postconditions_are_attached_exactly_as_the_plan_declares(self) -> None:
        """Every row's per-phase postconditions: the plan-bundle-producing
        rows' single entry (CP3 of the previous milestone, migrated
        unchanged), no `/review-plan`/`/record-manual-plan-review` entry
        (neither generates a bundle), and CP2's implementation-stage rows
        12-18."""
        attached = {
            (eo.from_phase, eo.governing_version, eo.action): dict(eo.postconditions)
            for eo in job.EXPECTED_OUTCOMES if eo.postconditions
        }
        plan = job._postcondition_plan_bundle_coherent
        coherent = job._postcondition_implementation_bundle_coherent
        self_review = job._postcondition_self_review_entered_durably
        self.assertEqual(attached, {
            ("PLANNING", "2.1", "/milestone-plan"): {frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}): plan},
            ("PLANNING", "2.2", "/milestone-plan"): {frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}): plan},
            ("PLANNING", "1", "/milestone-plan"): {frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}): plan},
            ("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review"):
                {frozenset({"AWAITING_EXTERNAL_PLAN_REVIEW"}): plan},
            ("REVISING_PLAN", "2.1", "/apply-plan-review"): {frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}): plan},
            ("REVISING_PLAN", "2.2", "/apply-plan-review"): {frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}): plan},
            (job.NO_PHASE, None, "/milestone-plan"): {frozenset({"AWAITING_LOCAL_PLAN_REVIEW"}): plan},
            ("IMPLEMENTING", "2.1", "/milestone-implement"): {
                frozenset({"SELF_REVIEWING_IMPLEMENTATION"}): self_review,
                frozenset({"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"}): coherent,
            },
            ("IMPLEMENTING", "2.2", "/milestone-implement"): {
                frozenset({"SELF_REVIEWING_IMPLEMENTATION"}): self_review,
                frozenset({"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}): coherent,
            },
            ("SELF_REVIEWING_IMPLEMENTATION", "2.1", "/milestone-implement"):
                {frozenset({"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"}): coherent},
            ("SELF_REVIEWING_IMPLEMENTATION", "2.2", "/milestone-implement"):
                {frozenset({"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}): coherent},
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation"): {
                frozenset({"AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"}):
                    job._postcondition_local_implementation_approve_recorded,
                frozenset({"APPLYING_REVIEW_FEEDBACK"}): job._postcondition_local_implementation_revise_recorded,
            },
            ("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2", "/record-manual-implementation-review"): {
                frozenset({"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"}):
                    job._postcondition_manual_implementation_approve_recorded,
                frozenset({"APPLYING_REVIEW_FEEDBACK"}): job._postcondition_manual_implementation_revise_recorded,
            },
            ("APPLYING_REVIEW_FEEDBACK", "2.2", "/apply-implementation-review"):
                {frozenset({"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}): job._postcondition_implementation_bundle_regenerated},
        })

    def test_implementation_stage_rows_match_the_plan_table(self) -> None:
        """Rows 12-18 (CP2): exact `to_any_of`, predicate, inputs and
        writer call per row; no `"1"` implementation-stage row."""
        rows = {
            (eo.from_phase, eo.governing_version, eo.action): eo for eo in job.EXPECTED_OUTCOMES[11:]
        }
        self.assertEqual(len(rows), 7)
        expected = {
            ("IMPLEMENTING", "2.1", "/milestone-implement"): (
                {"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"},
                job._predicate_checkpoint_completed_durably, {"checkpoints", "target_head"},
                ("complete_checkpoint", "milestone-implement.md", job.BranchSpec("step", "1f")),
            ),
            ("IMPLEMENTING", "2.2", "/milestone-implement"): (
                {"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"},
                job._predicate_checkpoint_completed_durably, {"checkpoints", "target_head"},
                ("complete_checkpoint", "milestone-implement.md", job.BranchSpec("step", "1f")),
            ),
            ("SELF_REVIEWING_IMPLEMENTATION", "2.1", "/milestone-implement"): (
                {"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"}, None, set(),
                ("record_bundle_generation", "milestone-implement.md", job.BranchSpec("step", "4")),
            ),
            ("SELF_REVIEWING_IMPLEMENTATION", "2.2", "/milestone-implement"): (
                {"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}, None, set(),
                ("record_bundle_generation", "milestone-implement.md", job.BranchSpec("step", "4")),
            ),
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation"): (
                {"AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK",
                 "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"},
                job._predicate_local_implementation_block_current, {"bundle_manifest_bundle_id"},
                ("record_local_implementation_review", "review-implementation.md",
                 job.BranchSpec("bullet", "BLOCK")),
            ),
            ("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2", "/record-manual-implementation-review"): (
                {"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK"}, None, set(),
                ("record_manual_implementation_review", "record-manual-implementation-review.md",
                 job.BranchSpec("step", "7")),
            ),
            ("APPLYING_REVIEW_FEEDBACK", "2.2", "/apply-implementation-review"): (
                {"AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}, None, set(),
                ("record_bundle_generation", "apply-implementation-review.md", job.BranchSpec("step", "7")),
            ),
        }
        self.assertEqual(set(rows), set(expected))
        for key, (to_any_of, predicate, inputs, (function, file, branch)) in expected.items():
            with self.subTest(key=key):
                eo = rows[key]
                self.assertEqual(eo.to_any_of, frozenset(to_any_of))
                self.assertIs(eo.predicate, predicate)
                self.assertEqual(eo.predicate_inputs, frozenset(inputs))
                (wc,) = eo.writer_calls
                self.assertEqual((wc.function, wc.file, wc.branch, wc.kind),
                                 (function, file, branch, job.WRITER_KIND_COMPLETION))
                expected_trailing = {"committed_checkpoint_status", "release_checkpoint"} \
                    if function == "complete_checkpoint" else set()
                self.assertEqual({fn for fn, _why in wc.trailing_calls}, expected_trailing)
        self.assertFalse(any(eo.governing_version == "1" for eo in rows.values()))

    def test_incomplete_effect_phases_stays_empty_with_the_implementation_rows(self) -> None:
        self.assertEqual(job._INCOMPLETE_EFFECT_PHASES, {})

    def test_record_completeness_negative_input_outside_pre_state_fields_fails(self) -> None:
        row3 = next(eo for eo in job.EXPECTED_OUTCOMES if eo.action == "/review-plan")
        broken = dataclasses.replace(row3, predicate_inputs=frozenset({"not_a_real_field"}))
        violations = job.property_record_completeness_violations((broken,))
        self.assertTrue(any("not_a_real_field" in v for v in violations), violations)

    def test_record_completeness_is_clean_with_the_new_field_and_fails_without_it(self) -> None:
        """CP2: rows 3/3' and 16 read ``bundle_manifest_bundle_id``, a
        member of ``PRE_STATE_FIELDS``; without it the property fails."""
        self.assertIn("bundle_manifest_bundle_id", job.PRE_STATE_FIELDS)
        self.assertEqual(job.property_record_completeness_violations(), [])
        without = job.PRE_STATE_FIELDS - {"bundle_manifest_bundle_id"}
        violations = job.property_record_completeness_violations(job.EXPECTED_OUTCOMES, without)
        self.assertEqual(violations, ["predicate_input 'bundle_manifest_bundle_id' is not a member of PRE_STATE_FIELDS"])

    def test_row3_predicate_inputs_name_the_manifest_bundle_id_not_current_bundle_id(self) -> None:
        for eo in job.EXPECTED_OUTCOMES:
            if eo.action == "/review-plan":
                self.assertEqual(eo.predicate_inputs, frozenset({"bundle_manifest_bundle_id"}))


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


    # -- CP2 (`workflow-controller-automatic-lifecycle-orchestration`):
    # the `trailing_calls` allowlist, and the new rows' located spans.

    _ROW12_KEY = ("IMPLEMENTING", "2.2", "/milestone-implement")

    def _row12_with_trailing(self, trailing_calls):
        row12 = job._EXPECTED_OUTCOMES_BY_KEY[self._ROW12_KEY]
        wc = dataclasses.replace(row12.writer_calls[0], trailing_calls=trailing_calls)
        return dataclasses.replace(row12, writer_calls=(wc,))

    def test_step_1f_trailing_calls_are_exactly_the_two_the_frozen_text_makes(self) -> None:
        row12 = job._EXPECTED_OUTCOMES_BY_KEY[self._ROW12_KEY]
        self.assertEqual(
            [fn for fn, _why in row12.writer_calls[0].trailing_calls],
            ["committed_checkpoint_status", "release_checkpoint"],
        )
        for _fn, why in row12.writer_calls[0].trailing_calls:
            self.assertTrue(why.strip())
        self.assertEqual(job.property_declaration_against_artifact_violations(REPO_ROOT, (row12,)), [])

    def test_an_undeclared_trailing_write_still_fails(self) -> None:
        """Without the allowlist, step 1f's two later calls are reported;
        allowlisting one leaves the other reported -- the property is
        never weakened to pass."""
        violations = job.property_declaration_against_artifact_violations(
            REPO_ROOT, (self._row12_with_trailing(()),),
        )
        self.assertEqual(len(violations), 2, violations)
        self.assertTrue(any("'committed_checkpoint_status' occurs after it" in v for v in violations))
        self.assertTrue(any("'release_checkpoint' occurs after it" in v for v in violations))

        only_read = self._row12_with_trailing((("committed_checkpoint_status", "a read"),))
        violations = job.property_declaration_against_artifact_violations(REPO_ROOT, (only_read,))
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("a further durable write to 'release_checkpoint'", violations[0])

    def test_a_trailing_calls_entry_absent_from_the_span_is_a_violation(self) -> None:
        stale = self._row12_with_trailing(
            job._STEP_1F_TRAILING_CALLS + (("record_bundle_generation", "not in step 1f at all"),),
        )
        violations = job.property_declaration_against_artifact_violations(REPO_ROOT, (stale,))
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("trailing_calls names 'record_bundle_generation'", violations[0])
        self.assertIn("stale allowlist entry", violations[0])

    def test_a_trailing_calls_entry_only_before_the_declared_call_is_a_violation(self) -> None:
        """`owner_mutation` occurs inside step 1f's span, but *before*
        `complete_checkpoint`: the allowlist covers only calls after it."""
        before = self._row12_with_trailing(
            job._STEP_1F_TRAILING_CALLS + (("owner_mutation", "precedes the declared call"),),
        )
        violations = job.property_declaration_against_artifact_violations(REPO_ROOT, (before,))
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("trailing_calls names 'owner_mutation'", violations[0])

    def test_trailing_calls_misuse_is_a_table_violation(self) -> None:
        cases = {
            "carries no justification": (("committed_checkpoint_status", "  "),),
            "names the declared call itself": (("complete_checkpoint", "self"),),
            "names 'release_checkpoint' twice": (
                ("release_checkpoint", "one"), ("release_checkpoint", "two"),
            ),
            "is not a function identifier": (("release_checkpoint(", "bad name"),),
            "is not a (function, justification) pair": (("release_checkpoint",),),
        }
        for needle, trailing in cases.items():
            with self.subTest(needle=needle):
                violations = job.property_table_violations((self._row12_with_trailing(trailing),))
                self.assertTrue(any(needle in v for v in violations), violations)
        self.assertEqual(
            job.property_table_violations((self._row12_with_trailing(job._STEP_1F_TRAILING_CALLS),)), [],
        )

    def test_row16_block_bullet_is_located_inside_a6_not_the_advisory_branch(self) -> None:
        """The first-match ``bullet`` locator lands in
        `review-implementation.md`'s own A6 write set: no `BLOCK` bullet
        precedes it in the advisory branch, so no ``within`` qualifier is
        needed (and none is added speculatively)."""
        row16 = job._EXPECTED_OUTCOMES_BY_KEY[
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2", "/review-implementation")
        ]
        lines = (REPO_ROOT / ".claude" / "commands" / "review-implementation.md").read_text().splitlines()
        start, _end = job._branch_span(lines, row16.writer_calls[0].branch)
        a6 = next(i for i, line in enumerate(lines) if line.startswith("A6."))
        a7 = next(i for i, line in enumerate(lines) if line.startswith("A7."))
        self.assertTrue(a6 < start < a7, (a6, start, a7))
        self.assertIn('verdict="BLOCK"', lines[start + 1])

    def test_new_rows_locate_their_declared_step_spans(self) -> None:
        for key, first_line in (
            (("IMPLEMENTING", "2.1", "/milestone-implement"), "1f. "),
            (("SELF_REVIEWING_IMPLEMENTATION", "2.2", "/milestone-implement"), "4. "),
            (("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2", "/record-manual-implementation-review"),
             "7. "),
            (("APPLYING_REVIEW_FEEDBACK", "2.2", "/apply-implementation-review"), "7. "),
        ):
            with self.subTest(key=key):
                wc = job._EXPECTED_OUTCOMES_BY_KEY[key].writer_calls[0]
                lines = (REPO_ROOT / ".claude" / "commands" / wc.file).read_text().splitlines()
                start, _end = job._branch_span(lines, wc.branch)
                self.assertTrue(lines[start].startswith(first_line), lines[start])


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
        root, managed_repo = self._managed_repo()
        if work_item.phase in evidence.PLAN_BUNDLE_CONSUMING_PHASES:
            fixtures.write_plan_manifest(root, work_item.work_item_id, work_item.plan_revision)
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
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=head),
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
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=head),
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
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=head),
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
        # step 9 appends through). Automatic-lifecycle-orchestration CP5:
        # the `on_spawn` flush of `worker_process` is a second LAUNCHED
        # write, after `Popen` and before the wait.
        assert_launched_status_sequence(self, spy.statuses(), "FINISHED")

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
        reported as success, never failure. The feedback is bound to the
        plan manifest's own ``bundle_id``; the state's
        ``current_bundle_id`` stays ``null``, as no Workflow writer ever
        sets it (CP2)."""
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.1",
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=fixtures.current_head(root)),
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
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=fixtures.current_head(root)),
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
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=head),
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
        # CP5: the second LAUNCHED write is the `worker_process` flush.
        assert_launched_status_sequence(self, spy.statuses(), "FINISHED")

    def test_row3_ordinary_reaches_awaiting_manual_external_plan_review(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.2",
        )
        fixtures.write_plan_manifest(managed_repo.root, "wi-1", 1)
        env = _write_state_phase_env(managed_repo.root, "wi-1", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        record = _run(managed_repo, self.runtime_root, env_overrides=env)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

    def test_row3_revise_reaches_revising_plan(self) -> None:
        managed_repo = _build_target(
            self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version="2.2",
        )
        fixtures.write_plan_manifest(managed_repo.root, "wi-1", 1)
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
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=fixtures.current_head(root)),
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
            base_commit="0" * 40,
            plan_review_stages={
                "review_content_id": "c" * 64,
                "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
            },
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=fixtures.current_head(root)),
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
            base_commit="0" * 40,
            plan_review_stages={
                "review_content_id": "c" * 64,
                "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
            },
        )
        root = managed_repo.root
        fixtures.write_manifest(
            root, ".ai-review/wi-1/current",
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=fixtures.current_head(root)),
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



# ---------------------------------------------------------------------------
# `workflow-controller-automatic-lifecycle-orchestration` CP2 -- the plan-
# stage `BLOCK`-predicate bundle-id fix (rows 3/3').
# ---------------------------------------------------------------------------


class Row3BlockPredicateBundleIdRegressionTest(unittest.TestCase):
    """The real Workflow shape -- ``current_bundle_id: null`` (no writer
    ever sets it) and a genuine local ``BLOCK`` bound to the plan
    manifest's own ``bundle_id`` -- verifies ``FINISHED``; at base the
    predicate compared against ``current_bundle_id`` and recorded it
    ``FAILED``. A feedback file missing its binding line no longer
    satisfies it (``None == None`` at base)."""

    MANIFEST_BUNDLE_ID = "d" * 64

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

    def _execute(self, name: str, version: str, *, reviewed_bundle_id, write_manifest: bool = True):
        """``write_manifest=False`` would make the real ``decide`` gate on
        the absent plan bundle, so that one case pins the ``/review-plan``
        selection to reach the predicate at all."""
        managed_repo = _build_target(
            self.tmp_root / name, phase="AWAITING_LOCAL_PLAN_REVIEW", governing_workflow_version=version,
            current_bundle_id=None,
        )
        root = managed_repo.root
        if write_manifest:
            fixtures.write_plan_manifest(
                root, "wi-1", 1, bundle_id=self.MANIFEST_BUNDLE_ID, generation_head=fixtures.current_head(root),
            )
        feedback_path = root / ".ai-review" / "wi-1" / "feedback" / "REVIEW_FEEDBACK.md"
        env = {
            "FAKE_CLAUDE_WRITE_PATH": str(feedback_path),
            "FAKE_CLAUDE_WRITE_TEXT": fixtures.build_review_feedback_text(
                status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id=reviewed_bundle_id,
            ),
        }
        runtime_root = self.tmp_root / name / "runtime"
        runtime_root.mkdir(parents=True)
        if write_manifest:
            return _run(managed_repo, runtime_root, env_overrides=env)
        with fixtures.forced_automatic_action("AWAITING_LOCAL_PLAN_REVIEW", "/review-plan"):
            return _run(managed_repo, runtime_root, env_overrides=env)

    def test_real_shape_block_bound_to_the_manifest_bundle_finishes(self) -> None:
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                record = self._execute(f"real-{version}", version, reviewed_bundle_id=self.MANIFEST_BUNDLE_ID)
                self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
                self.assertTrue(record["transition_verified"])
                self.assertIsNone(record["pre_state"]["bundle_id"])
                self.assertEqual(record["pre_state"]["bundle_manifest_bundle_id"], self.MANIFEST_BUNDLE_ID)

    def test_block_feedback_missing_its_binding_line_is_not_satisfied(self) -> None:
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                record = self._execute(f"unbound-{version}", version, reviewed_bundle_id=None)
                self.assertEqual(record["status"], job.STATUS_FAILED)
                self.assertEqual(record["reconciliation_evidence"]["reason"], "predicate_not_satisfied")

    def test_block_bound_to_another_bundle_is_not_satisfied(self) -> None:
        record = self._execute("other", "2.2", reviewed_bundle_id="e" * 64)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "predicate_not_satisfied")

    def test_no_pre_state_manifest_is_not_satisfied_even_for_an_unbound_feedback(self) -> None:
        record = self._execute("no-manifest", "2.2", reviewed_bundle_id=None, write_manifest=False)
        self.assertIsNone(record["pre_state"]["bundle_manifest_bundle_id"])
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "predicate_not_satisfied")

    def test_predicate_reads_only_the_manifest_side_and_fails_closed_on_absence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.write_review_feedback(
                root, ".ai-review/feedback",
                fixtures.build_review_feedback_text(
                    status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id="b" * 64,
                ),
            )
            predicate = job._predicate_row3_block_feedback_current
            self.assertTrue(predicate(root, "wi-1", {"bundle_manifest_bundle_id": "b" * 64}))
            # The pre-CP2 input alone no longer satisfies it, and a record
            # written before the field existed is "not satisfied".
            self.assertFalse(predicate(root, "wi-1", {"bundle_id": "b" * 64, "bundle_manifest_readable": True}))
            self.assertFalse(predicate(root, "wi-1", {"bundle_manifest_bundle_id": None}))
            self.assertFalse(predicate(root, "wi-1", {"bundle_manifest_bundle_id": ""}))


# ---------------------------------------------------------------------------
# `workflow-controller-automatic-lifecycle-orchestration` CP2 -- rows 12-18
# through the real `execute_step`. `evidence.decide` does not select these
# actions until CP3's dispatch rule, so each test pins the selected action
# with a patched `decide`; the worker is `fake_claude.py`, preceded by a
# scripted side effect that performs the Workflow writers' own state
# writes, commits and bundle generation -- or deliberately fails to.
# ---------------------------------------------------------------------------

_B = "b" * 64  # the fixture manifest's bundle_id
_C = "c" * 64  # the fixture manifest's review_content_id
_REVIEWED_HEAD = "1" * 40  # the seeded reviewed_implementation_head
_LOCAL = "LOCAL_MODEL_IMPLEMENTATION_REVIEW"
_MANUAL = "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
_REVIEW_PHASE = {"2.1": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.2": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}


def local_feedback(status: str, **kwargs) -> str:
    kwargs.setdefault("reviewer_role", _LOCAL)
    kwargs.setdefault("reviewed_bundle_id", _B)
    return fixtures.build_review_feedback_text(status=status, **kwargs)


class _ImplementationRowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self._counter = 0

    def target(self, phase: str, version: str = "2.2", **kwargs):
        self._counter += 1
        self._case_root = self.tmp_root / f"case-{self._counter}"
        return fixtures.build_implementation_target(
            self._case_root, phase=phase, governing_workflow_version=version, **kwargs,
        )

    def execute(self, managed_repo, phase: str, command: str, effect=None) -> dict:
        runtime_root = self._case_root / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        with fixtures.forced_automatic_action(phase, command), fixtures.scripted_worker(effect):
            return _run(managed_repo, runtime_root)

    def assert_finished(self, record: dict, observed_phase: str) -> None:
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], observed_phase)

    def assert_failed(self, record: dict, reason: str, *, detail: str | None = None) -> dict:
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["code"], "TransitionNotObservedError")
        self.assertEqual(ev["reason"], reason, ev)
        if reason == "postcondition_not_satisfied":
            self.assertIn(detail, ev["postcondition_detail"])
        else:
            self.assertNotIn("postcondition_detail", ev)
        return ev


class CheckpointRowsExecuteTest(_ImplementationRowTestCase):
    """Rows 12/13: ``/milestone-implement`` from ``IMPLEMENTING``."""

    def _in_progress_target(self, version: str, *, done: tuple[str, ...] = ()):
        checkpoints = {"CP1": {"status": "IN_PROGRESS"}, "CP2": {"status": "PENDING"}}
        for checkpoint_id in done:
            checkpoints[checkpoint_id] = {"status": "COMPLETE"}
        if done:
            checkpoints["CP2"] = {"status": "IN_PROGRESS"}
        return self.target("IMPLEMENTING", version, checkpoints=checkpoints)

    def test_a_committed_checkpoint_completion_self_loop_finishes(self) -> None:
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                managed_repo = self._in_progress_target(version)
                record = self.execute(managed_repo, "IMPLEMENTING", "/milestone-implement",
                                      fixtures.complete_checkpoint_effect("CP1"))
                self.assert_finished(record, "IMPLEMENTING")
                self.assertEqual(record["pre_state"]["checkpoints"]["CP1"]["status"], "IN_PROGRESS")

    def test_an_uncommitted_checkpoint_completion_fails_the_predicate(self) -> None:
        for version, commit in (("2.1", "none"), ("2.2", "none"), ("2.2", "product")):
            with self.subTest(version=version, commit=commit):
                managed_repo = self._in_progress_target(version)
                record = self.execute(managed_repo, "IMPLEMENTING", "/milestone-implement",
                                      fixtures.complete_checkpoint_effect("CP1", commit=commit))
                self.assert_failed(record, "predicate_not_satisfied")

    def test_a_worker_that_changes_nothing_fails_the_predicate(self) -> None:
        managed_repo = self._in_progress_target("2.2")
        record = self.execute(managed_repo, "IMPLEMENTING", "/milestone-implement")
        self.assert_failed(record, "predicate_not_satisfied")

    def test_the_last_checkpoint_entering_self_review_durably_finishes(self) -> None:
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                managed_repo = self._in_progress_target(version, done=("CP1",))
                record = self.execute(
                    managed_repo, "IMPLEMENTING", "/milestone-implement",
                    fixtures.complete_checkpoint_effect("CP2", phase="SELF_REVIEWING_IMPLEMENTATION"),
                )
                self.assert_finished(record, "SELF_REVIEWING_IMPLEMENTATION")

    def test_an_uncommitted_last_checkpoint_completion_fails_the_postcondition(self) -> None:
        managed_repo = self._in_progress_target("2.2", done=("CP1",))
        record = self.execute(
            managed_repo, "IMPLEMENTING", "/milestone-implement",
            fixtures.complete_checkpoint_effect("CP2", phase="SELF_REVIEWING_IMPLEMENTATION", commit="product"),
        )
        self.assert_failed(
            record, "postcondition_not_satisfied",
            detail="not every registry checkpoint is COMPLETE in the state committed at HEAD",
        )

    def test_an_uncommitted_self_reviewing_implementation_fails_the_postcondition(self) -> None:
        """The checkpoint completion is committed, but the phase change
        only reached the working tree."""
        managed_repo = self._in_progress_target("2.2", done=("CP1",))

        def effect(root: Path) -> None:
            fixtures.complete_checkpoint_effect("CP2")(root)
            fixtures.update_workflow_state(root, "wi-1", phase="SELF_REVIEWING_IMPLEMENTATION")

        record = self.execute(managed_repo, "IMPLEMENTING", "/milestone-implement", effect)
        self.assert_failed(
            record, "postcondition_not_satisfied",
            detail="the committed phase at HEAD is 'IMPLEMENTING', not 'SELF_REVIEWING_IMPLEMENTATION'",
        )

    def test_the_no_checkpoint_partial_run_stopping_after_step_2_finishes(self) -> None:
        """Every checkpoint already ``COMPLETE`` (a plan re-approval): step
        2 commits the transition alone, and the run stops there -- a legal
        partial run, with no checkpoint newly completed."""
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                managed_repo = self.target("IMPLEMENTING", version)

                def effect(root: Path) -> None:
                    fixtures.update_workflow_state(root, "wi-1", phase="SELF_REVIEWING_IMPLEMENTATION")
                    fixtures.commit_paths(root, "Enter SELF_REVIEWING_IMPLEMENTATION",
                                          "docs/ai-workflow/WORKFLOW_STATE.json")

                record = self.execute(managed_repo, "IMPLEMENTING", "/milestone-implement", effect)
                self.assert_finished(record, "SELF_REVIEWING_IMPLEMENTATION")

    def test_the_no_checkpoint_path_through_generation_checks_the_bundle(self) -> None:
        for version in ("2.1", "2.2"):
            review_phase = _REVIEW_PHASE[version]
            with self.subTest(version=version, bundle="coherent"):
                managed_repo = self.target("IMPLEMENTING", version)
                record = self.execute(managed_repo, "IMPLEMENTING", "/milestone-implement",
                                      fixtures.generation_effect(review_phase, enter_self_review=True))
                self.assert_finished(record, review_phase)
            with self.subTest(version=version, bundle="stale"):
                managed_repo = self.target("IMPLEMENTING", version)
                record = self.execute(
                    managed_repo, "IMPLEMENTING", "/milestone-implement",
                    fixtures.generation_effect(review_phase, enter_self_review=True, manifest_revision=0),
                )
                self.assert_failed(
                    record, "postcondition_not_satisfied",
                    detail="manifest implementation_revision 0 != state implementation_revision 1",
                )


class CheckpointPredicateUnitTest(_ImplementationRowTestCase):
    """``_predicate_checkpoint_completed_durably`` clause by clause, against
    a real repository: each clause is load-bearing on its own."""

    def _repo(self):
        managed_repo = self.target(
            "IMPLEMENTING", checkpoints={"CP1": {"status": "COMPLETE"}, "CP2": {"status": "PENDING"}},
        )
        return managed_repo.root

    def test_a_completion_already_committed_before_the_job_needs_head_to_move(self) -> None:
        """``HEAD`` already records CP1 ``COMPLETE`` while the pre-state
        (the working tree) did not: a worker that only rewrites the working
        tree leaves ``HEAD`` where it was, and does not verify."""
        root = self._repo()
        head = fixtures.current_head(root)
        pre_state = {"target_head": head, "checkpoints": {"CP1": {"status": "IN_PROGRESS"}}}
        self.assertFalse(job._predicate_checkpoint_completed_durably(root, "wi-1", pre_state))
        # Positive control: the same facts with HEAD having moved since.
        moved = {**pre_state, "target_head": "0" * 40}
        self.assertTrue(job._predicate_checkpoint_completed_durably(root, "wi-1", moved))

    def test_a_checkpoint_already_complete_in_the_pre_state_does_not_count(self) -> None:
        root = self._repo()
        pre_state = {"target_head": "0" * 40, "checkpoints": {"CP1": {"status": "COMPLETE"}}}
        self.assertFalse(job._predicate_checkpoint_completed_durably(root, "wi-1", pre_state))

    def test_absent_or_malformed_inputs_are_not_satisfied(self) -> None:
        root = self._repo()
        for pre_state in (
            {"checkpoints": {}},
            {"target_head": "0" * 40},
            {"target_head": None, "checkpoints": {}},
            {"target_head": "0" * 40, "checkpoints": ["CP1"]},
        ):
            with self.subTest(pre_state=pre_state):
                self.assertFalse(job._predicate_checkpoint_completed_durably(root, "wi-1", pre_state))

    def test_an_unreadable_post_state_is_not_satisfied(self) -> None:
        root = self._repo()
        (root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text("{not json")
        pre_state = {"target_head": "0" * 40, "checkpoints": {}}
        self.assertFalse(job._predicate_checkpoint_completed_durably(root, "wi-1", pre_state))

    def test_the_self_review_postcondition_reads_an_unreadable_post_state_as_not_satisfied(self) -> None:
        root = self._repo()
        (root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text("{not json")
        satisfied, detail = job._postcondition_self_review_entered_durably(root, "wi-1", {})
        self.assertFalse(satisfied)
        self.assertIn("post-state could not be read", detail)


class GenerationRowsExecuteTest(_ImplementationRowTestCase):
    """Rows 14/15: ``/milestone-implement`` from
    ``SELF_REVIEWING_IMPLEMENTATION`` (step 4)."""

    def _execute(self, version: str, effect) -> dict:
        managed_repo = self.target("SELF_REVIEWING_IMPLEMENTATION", version)
        return self.execute(managed_repo, "SELF_REVIEWING_IMPLEMENTATION", "/milestone-implement", effect)

    def test_a_coherent_bundle_finishes(self) -> None:
        for version, review_phase in _REVIEW_PHASE.items():
            with self.subTest(version=version):
                record = self._execute(version, fixtures.generation_effect(review_phase))
                self.assert_finished(record, review_phase)

    def test_each_bundle_clause_fails_the_postcondition(self) -> None:
        cases = {
            "stale revision": (
                dict(manifest_revision=0),
                "manifest implementation_revision 0 != state implementation_revision 1",
            ),
            "wrong reviewed_implementation_head": (
                dict(manifest_reviewed_head="2" * 40),
                f"manifest reviewed_implementation_head {'2' * 40!r} != state reviewed_implementation_head",
            ),
            "generation_head behind HEAD": (
                dict(manifest_generation_head=lambda reviewed_head, record: reviewed_head),
                "!= target HEAD",
            ),
            "missing manifest": (dict(manifest=False), "is absent (withdrawn or never generated)"),
            "REJECTED marker": (dict(rejected=True), "REJECTED marker at .ai-review/wi-1/REJECTED is present"),
        }
        for version, review_phase in _REVIEW_PHASE.items():
            for name, (kwargs, detail) in cases.items():
                with self.subTest(version=version, clause=name):
                    record = self._execute(version, fixtures.generation_effect(review_phase, **kwargs))
                    self.assertEqual(record["observed_phase_after"], review_phase)
                    self.assert_failed(record, "postcondition_not_satisfied", detail=detail)

    def test_a_worker_that_stops_before_generation_fails_the_phase_clause(self) -> None:
        record = self._execute("2.2", None)
        self.assert_failed(record, "phase_not_in_to_any_of")


class _ReviewStageTarget:
    """A `"2.2"` item at an implementation-review phase: revision 1, a
    committed state, and a coherent implementation manifest
    (``bundle_id`` ``_B``, ``review_content_id`` ``_C``) generated at the
    seeded ``HEAD``."""

    @staticmethod
    def build(case: _ImplementationRowTestCase, phase: str, *, feedback: str | None = None, **overrides):
        managed_repo = case.target(
            phase, "2.2", implementation_revision=1, reviewed_implementation_head=_REVIEWED_HEAD, **overrides,
        )
        root = managed_repo.root
        fixtures.write_implementation_manifest(root, "wi-1", 1, reviewed_implementation_head=_REVIEWED_HEAD)
        if feedback is not None:
            fixtures.write_review_feedback(root, ".ai-review/wi-1/feedback", feedback)
        return managed_repo


class LocalImplementationReviewRowExecuteTest(_ImplementationRowTestCase):
    """Row 16: `"2.2"` ``/review-implementation`` from
    ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW``."""

    PHASE = "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"

    def _execute(self, effect) -> dict:
        managed_repo = _ReviewStageTarget.build(self, self.PHASE)
        return self.execute(managed_repo, self.PHASE, "/review-implementation", effect)

    def _approve(self, *, ledger=None, **feedback_kwargs):
        return fixtures.review_writes_effect(
            feedback=local_feedback("APPROVE", **feedback_kwargs),
            phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            implementation_review_stages=ledger or fixtures.implementation_review_ledger(_C, local_bundle_id=_B),
        )

    def test_local_approve_recorded_finishes(self) -> None:
        record = self._execute(self._approve())
        self.assert_finished(record, "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(record["pre_state"]["bundle_manifest_bundle_id"], _B)

    def test_local_approve_with_the_content_id_under_another_label_finishes(self) -> None:
        record = self._execute(self._approve(
            reviewed_content_id=None, extra_lines=(f"Review content ID: {_C}",),
        ))
        self.assert_finished(record, "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")

    def test_each_local_approve_clause_fails_the_postcondition(self) -> None:
        cases = {
            "ledger bound to other content": (
                self._approve(ledger=fixtures.implementation_review_ledger("d" * 64, local_bundle_id=_B)),
                f"ledger review_content_id {'d' * 64!r} != manifest review_content_id {_C!r}",
            ),
            "ledger bound to another bundle": (
                self._approve(ledger=fixtures.implementation_review_ledger(_C, local_bundle_id="e" * 64)),
                f"ledger LOCAL_MODEL_IMPLEMENTATION_REVIEW bundle_id {'e' * 64!r} != manifest bundle_id",
            ),
            "no ledger stage": (
                self._approve(ledger={"review_content_id": _C}),
                "the ledger records no LOCAL_MODEL_IMPLEMENTATION_REVIEW stage",
            ),
            "manual stage already recorded": (
                self._approve(ledger=fixtures.implementation_review_ledger(
                    _C, local_bundle_id=_B, manual_bundle_id=_B,
                )),
                "the ledger already records MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            ),
            "feedback bound to another bundle": (
                self._approve(reviewed_bundle_id="e" * 64),
                f"feedback Reviewed bundle ID {'e' * 64!r} != manifest bundle_id {_B!r}",
            ),
            "wrong role": (
                self._approve(reviewer_role=_MANUAL),
                f"feedback Reviewer role {_MANUAL!r} != {_LOCAL!r}",
            ),
            "wrong status": (
                fixtures.review_writes_effect(
                    feedback=local_feedback("REVISE"), phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                    implementation_review_stages=fixtures.implementation_review_ledger(_C, local_bundle_id=_B),
                ),
                "feedback Status 'REVISE' != 'APPROVE'",
            ),
        }
        for name, (effect, detail) in cases.items():
            with self.subTest(clause=name):
                record = self._execute(effect)
                self.assert_failed(record, "postcondition_not_satisfied", detail=detail)

    def test_local_revise_recorded_finishes(self) -> None:
        record = self._execute(fixtures.review_writes_effect(feedback=local_feedback("REVISE"), phase="APPLYING_REVIEW_FEEDBACK"))
        self.assert_finished(record, "APPLYING_REVIEW_FEEDBACK")

    def test_each_local_revise_clause_fails_the_postcondition(self) -> None:
        cases = {
            "feedback bound to another bundle": (
                local_feedback("REVISE", reviewed_bundle_id="e" * 64),
                f"feedback Reviewed bundle ID {'e' * 64!r} != the pre-state manifest bundle_id {_B!r}",
            ),
            "wrong role": (
                local_feedback("REVISE", reviewer_role=_MANUAL), f"feedback Reviewer role {_MANUAL!r}",
            ),
            "wrong work item": (local_feedback("REVISE", work_item="wi-other"), "feedback Work item 'wi-other'"),
            "no feedback": (None, "no REVIEW_FEEDBACK.md is on file"),
        }
        for name, (feedback, detail) in cases.items():
            with self.subTest(clause=name):
                record = self._execute(fixtures.review_writes_effect(feedback=feedback, phase="APPLYING_REVIEW_FEEDBACK"))
                self.assert_failed(record, "postcondition_not_satisfied", detail=detail)

    def test_local_block_bound_to_the_pre_state_bundle_finishes(self) -> None:
        record = self._execute(fixtures.review_writes_effect(feedback=local_feedback("BLOCK")))
        self.assert_finished(record, self.PHASE)

    def test_block_self_loop_without_current_block_evidence_fails_the_predicate(self) -> None:
        cases = {
            "no feedback": None,
            "another bundle": local_feedback("BLOCK", reviewed_bundle_id="e" * 64),
            "no binding line": local_feedback("BLOCK", reviewed_bundle_id=None),
            "plan-stage role": local_feedback("BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW"),
            "not BLOCK": local_feedback("REVISE"),
        }
        for name, feedback in cases.items():
            with self.subTest(case=name):
                record = self._execute(fixtures.review_writes_effect(feedback=feedback))
                self.assert_failed(record, "predicate_not_satisfied")


class ManualImplementationReviewRowExecuteTest(_ImplementationRowTestCase):
    """Row 17: `"2.2"` ``/record-manual-implementation-review`` from
    ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW``, with the human's
    pasted verdict already on file."""

    PHASE = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"

    def _execute(self, pasted: str, effect) -> dict:
        managed_repo = _ReviewStageTarget.build(
            self, self.PHASE, feedback=pasted,
            implementation_review_stages=fixtures.implementation_review_ledger(_C, local_bundle_id=_B),
        )
        return self.execute(managed_repo, self.PHASE, "/record-manual-implementation-review", effect)

    @staticmethod
    def _manual(status: str, **kwargs) -> str:
        kwargs.setdefault("reviewer_role", _MANUAL)
        kwargs.setdefault("reviewed_bundle_id", _B)
        return fixtures.build_review_feedback_text(status=status, **kwargs)

    @staticmethod
    def _approve_write(manual_bundle_id=_B, content_id=_C):
        return fixtures.review_writes_effect(
            phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            implementation_review_stages=fixtures.implementation_review_ledger(
                content_id, local_bundle_id=_B, manual_bundle_id=manual_bundle_id,
            ),
        )

    def test_manual_approve_recorded_finishes(self) -> None:
        record = self._execute(self._manual("APPROVE"), self._approve_write())
        self.assert_finished(record, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

    def test_manual_approve_recorded_verbatim_for_an_advisory_bundle_mismatch_finishes(self) -> None:
        record = self._execute(
            self._manual("APPROVE", reviewed_bundle_id="e" * 64), self._approve_write(manual_bundle_id="e" * 64),
        )
        self.assert_finished(record, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

    def test_each_manual_approve_clause_fails_the_postcondition(self) -> None:
        cases = {
            "ledger not written": (
                fixtures.review_writes_effect(phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"),
                "the ledger does not record MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            ),
            "ledger bound to other content": (
                self._approve_write(content_id="d" * 64),
                f"ledger review_content_id {'d' * 64!r} != manifest review_content_id {_C!r}",
            ),
            "manual bundle id is not the feedback's": (
                self._approve_write(manual_bundle_id="e" * 64),
                f"ledger MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW bundle_id {'e' * 64!r} != feedback "
                f"Reviewed bundle ID {_B!r}",
            ),
        }
        for name, (effect, detail) in cases.items():
            with self.subTest(clause=name):
                record = self._execute(self._manual("APPROVE"), effect)
                self.assert_failed(record, "postcondition_not_satisfied", detail=detail)

    def test_manual_revise_recorded_finishes(self) -> None:
        record = self._execute(self._manual("REVISE"), fixtures.review_writes_effect(phase="APPLYING_REVIEW_FEEDBACK"))
        self.assert_finished(record, "APPLYING_REVIEW_FEEDBACK")

    def test_each_manual_revise_clause_fails_the_postcondition(self) -> None:
        cases = {
            "ledger written on REVISE": (
                self._manual("REVISE"),
                fixtures.review_writes_effect(
                    phase="APPLYING_REVIEW_FEEDBACK",
                    implementation_review_stages=fixtures.implementation_review_ledger(
                        _C, local_bundle_id=_B, manual_bundle_id=_B,
                    ),
                ),
                "which a REVISE never writes",
            ),
            "wrong role": (
                self._manual("REVISE", reviewer_role=_LOCAL),
                fixtures.review_writes_effect(phase="APPLYING_REVIEW_FEEDBACK"),
                f"feedback Reviewer role {_LOCAL!r} != {_MANUAL!r}",
            ),
            "wrong status": (
                self._manual("APPROVE"),
                fixtures.review_writes_effect(phase="APPLYING_REVIEW_FEEDBACK"),
                "feedback Status 'APPROVE' != 'REVISE'",
            ),
        }
        for name, (pasted, effect, detail) in cases.items():
            with self.subTest(clause=name):
                record = self._execute(pasted, effect)
                self.assert_failed(record, "postcondition_not_satisfied", detail=detail)

    def test_a_block_left_in_place_fails_the_phase_clause(self) -> None:
        """A manual ``BLOCK`` is a no-op the Controller never ingests; a
        job that ends where it started does not verify."""
        record = self._execute(self._manual("BLOCK"), None)
        self.assert_failed(record, "phase_not_in_to_any_of")


class ApplyImplementationReviewRowExecuteTest(_ImplementationRowTestCase):
    """Row 18: `"2.2"` ``/apply-implementation-review`` from
    ``APPLYING_REVIEW_FEEDBACK`` (step 7's post-fix regeneration)."""

    PHASE = "APPLYING_REVIEW_FEEDBACK"

    def _execute(self, effect) -> dict:
        managed_repo = _ReviewStageTarget.build(self, self.PHASE, feedback=local_feedback("REVISE"))
        return self.execute(managed_repo, self.PHASE, "/apply-implementation-review", effect)

    def test_an_ordinary_post_fix_generation_finishes(self) -> None:
        record = self._execute(fixtures.generation_effect(
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", revision=2, fix_commit=True,
        ))
        self.assert_finished(record, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

    def test_a_same_content_generation_whose_revision_does_not_move_finishes(self) -> None:
        record = self._execute(fixtures.generation_effect("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", same_content=True))
        self.assert_finished(record, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(fixtures.state_entry(self._case_root / "target")["implementation_revision"], 1)

    def test_a_phase_flip_with_no_generation_fails_the_postcondition(self) -> None:
        """The same_content-shaped case the revision clauses cannot see:
        revision, reviewed head and ``HEAD`` all unchanged, so the old
        bundle still reads coherent -- only the ``generation_head`` clause
        catches it."""
        record = self._execute(fixtures.review_writes_effect(phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW"))
        self.assert_failed(record, "postcondition_not_satisfied", detail="no bundle generation ran in this job")

    def test_a_published_revision_without_a_regenerated_bundle_fails_the_postcondition(self) -> None:
        record = self._execute(fixtures.generation_effect(
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", revision=2, fix_commit=True, manifest=False,
        ))
        self.assert_failed(
            record, "postcondition_not_satisfied",
            detail="manifest implementation_revision 1 != state implementation_revision 2",
        )

    def test_a_rejected_regeneration_fails_the_postcondition(self) -> None:
        record = self._execute(fixtures.generation_effect(
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", revision=2, fix_commit=True, rejected=True,
        ))
        self.assert_failed(record, "postcondition_not_satisfied", detail="REJECTED marker")

    def test_a_worker_that_stays_at_applying_review_feedback_fails_the_phase_clause(self) -> None:
        record = self._execute(None)
        self.assert_failed(record, "phase_not_in_to_any_of")

    def test_the_pre_state_records_the_bundle_the_attempt_started_from(self) -> None:
        record = self._execute(None)
        self.assertEqual(record["pre_state"]["bundle_manifest_bundle_id"], _B)
        self.assertEqual(
            record["pre_state"]["bundle_manifest_generation_head"],
            record["pre_state"]["target_head"],
        )



# ---------------------------------------------------------------------------
# Worker-lifecycle-ownership CP4 (plan D): validation case 3 covers
# `worker_state` and the supervisor facts flushed with ENDING.
# ---------------------------------------------------------------------------


class WorkerStateValidationTest(unittest.TestCase):
    """A ``LAUNCHED``/``COMPLETED`` record's ``worker_state`` names a member
    of ``WORKER_STATES`` (``ENDED`` when ``COMPLETED``); ``ending_offset``
    appears only at ``ENDING``/``DRAINING``/``ENDED``; the three facts
    flushed with ``ENDING`` need ``ending_offset``; the bracket declaration
    names its ``command_uuid``; ``settled_wakeups`` is a list of distinct
    non-empty strings. Every breach is case 3 (``StaleJobRecordError``)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        tmp_root = Path(cls._tmp.name)
        cls.managed_repo = _build_target(tmp_root, phase="PLANNING")
        runtime_root = tmp_root / "runtime"
        runtime_root.mkdir()
        with _WriteSpy() as spy:
            _run(cls.managed_repo, runtime_root)
        records = [obj for rel, obj in spy.calls if "jobs/" in rel]
        cls.launched = copy.deepcopy([r for r in records if r.get("status") == job.STATUS_LAUNCHED][-1])
        cls.completed = copy.deepcopy(next(r for r in records if r.get("status") == job.STATUS_COMPLETED))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _validity(self, record: dict) -> job.Validity:
        return job.validate_record(record, managed_repo=self.managed_repo, identity=FAKE_IDENTITY)

    def _assert_breach(self, record: dict, reason: str) -> None:
        validity = self._validity(record)
        self.assertFalse(validity.valid)
        self.assertEqual((validity.case, validity.code, validity.reason), (3, "StaleJobRecordError", reason))
        self.assertFalse(validity.terminal)

    def test_the_real_records_are_valid(self) -> None:
        self.assertEqual(self.launched["worker_state"]["state"], "ENDED")
        self.assertIsInstance(self.launched["ending_offset"], int)
        self.assertEqual(self._validity(self.launched), job.VALID)
        self.assertEqual(self.completed["worker_state"]["state"], "ENDED")
        self.assertEqual(self._validity(self.completed), job.VALID)

    def test_every_worker_state_is_accepted_on_a_launched_record(self) -> None:
        for state in ("STARTING", "RUNNING", "WAITING", "ENDING", "DRAINING", "ENDED"):
            with self.subTest(state=state):
                record = copy.deepcopy(self.launched)
                record["worker_state"]["state"] = state
                if state not in ("ENDING", "DRAINING", "ENDED"):
                    record.pop("ending_offset")
                self.assertEqual(self._validity(record), job.VALID)

    def test_a_record_without_worker_state_takes_todays_path(self) -> None:
        for base in (self.launched, self.completed):
            record = {k: v for k, v in base.items() if k not in ("worker_state", "ending_offset")}
            self.assertEqual(self._validity(record), job.VALID)

    def test_an_unknown_or_malformed_worker_state_is_refused(self) -> None:
        for value in ({"state": "SLEEPING"}, {"state": None}, "WAITING", None):
            for base in (self.launched, self.completed):
                with self.subTest(value=value, status=base["status"]):
                    self._assert_breach({**base, "worker_state": value}, "worker_state_invalid")

    def test_a_completed_record_must_say_ended(self) -> None:
        for state in ("STARTING", "RUNNING", "WAITING", "ENDING", "DRAINING"):
            with self.subTest(state=state):
                record = copy.deepcopy(self.completed)
                record["worker_state"]["state"] = state
                self._assert_breach(record, "worker_state_not_ended")

    def test_ending_offset_only_once_the_session_was_ended(self) -> None:
        for state in ("STARTING", "RUNNING", "WAITING"):
            with self.subTest(state=state):
                record = copy.deepcopy(self.launched)
                record["worker_state"]["state"] = state
                self._assert_breach(record, "ending_offset_before_ending")
        # ... and DRAINING/ENDED without one is a lost anchor, not a breach.
        for state in ("DRAINING", "ENDED"):
            record = copy.deepcopy(self.launched)
            record["worker_state"]["state"] = state
            record.pop("ending_offset")
            self.assertEqual(self._validity(record), job.VALID)

    def test_the_ending_facts_need_ending_offset(self) -> None:
        facts = {
            "wakeup_overdue_declared_at": {"wakeup_overdue_declared_at": "2026-01-01T00:00:00Z"},
            "command_lifecycle_overdue_declared_at": {
                "command_lifecycle_overdue_declared_at": "2026-01-01T00:00:00Z",
                "command_lifecycle_overdue_command_uuid": "c-1"},
            "settled_wakeups": {"settled_wakeups": ["toolu_1"]},
        }
        for name, fields in facts.items():
            with self.subTest(fact=name):
                with_offset = {**self.launched, **fields}
                self.assertEqual(self._validity(with_offset), job.VALID)
                without = {k: v for k, v in with_offset.items() if k != "ending_offset"}
                without["worker_state"] = {**without["worker_state"], "state": "DRAINING"}
                self._assert_breach(without, "ending_fact_without_ending_offset")
        # An empty settled_wakeups carries nothing.
        empty = {k: v for k, v in self.launched.items() if k != "ending_offset"}
        empty["settled_wakeups"] = []
        empty["worker_state"] = {**empty["worker_state"], "state": "DRAINING"}
        self.assertEqual(self._validity(empty), job.VALID)

    def test_the_bracket_declaration_names_its_command_uuid(self) -> None:
        for command_uuid in (None, "", 7):
            with self.subTest(command_uuid=command_uuid):
                record = {**self.launched, "command_lifecycle_overdue_declared_at": "2026-01-01T00:00:00Z",
                          "command_lifecycle_overdue_command_uuid": command_uuid}
                self._assert_breach(record, "command_lifecycle_overdue_without_command_uuid")

    def test_settled_wakeups_is_a_list_of_distinct_non_empty_strings(self) -> None:
        for value in (["a", "a"], ["a", ""], [1], "a"):
            with self.subTest(value=value):
                self._assert_breach({**self.launched, "settled_wakeups": value}, "settled_wakeups_invalid")

    def test_a_terminal_record_is_not_judged_on_worker_state(self) -> None:
        record = {**self.completed, "status": job.STATUS_FAILED, "worker_state": {"state": "RUNNING"}}
        self.assertEqual(self._validity(record), job.VALID)


if __name__ == "__main__":
    unittest.main()
