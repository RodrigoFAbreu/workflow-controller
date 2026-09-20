"""Tests for ``controller.evidence`` (CP4B, ``REQ-T6``/``REQ-T7``).

Every test here is one the plan's own "Tests" list under "CP4 / CP4B --
Next-action decision engine and human-gate classification" tags
``(CP4B)`` -- the evidence-reading disambiguations CP4's own placeholder
rows explicitly defer to this checkpoint, plus the withdrawn-bundle
outcome and the two evidence-driven report-only phases.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import evidence
from tests import fixtures


def _make_target(tmp_root: Path) -> Path:
    root = tmp_root / "target"
    fixtures.build_target_git_repo(root)
    (root / "README.md").write_text("target fixture\n")
    fixtures.commit_all(root, "initial")
    return root


class ProvenanceBlockTest(unittest.TestCase):
    def test_block_stops_before_first_heading(self) -> None:
        text = "Status: APPROVE\nWork item: wi-1\n\n## Blocking findings\n\nStatus: quoted prose\n"
        self.assertEqual(evidence.provenance_block(text), "Status: APPROVE\nWork item: wi-1\n\n")

    def test_labelled_line_ignores_quotation_after_heading(self) -> None:
        text = fixtures.build_review_feedback_text(status="APPROVE")
        self.assertEqual(evidence.read_labelled_line(text, "Status:"), "APPROVE")
        # The body quotes "Status:" as prose after the heading -- the reader
        # must not pick that occurrence up as a second, disagreeing value.
        self.assertIn("Status: this is prose", text)

    def test_missing_label_is_none_never_a_default(self) -> None:
        text = "# doc\n\n## Blocking findings\n"
        self.assertIsNone(evidence.read_labelled_line(text, "Status:"))


class BundleDirResolutionTest(unittest.TestCase):
    """Round 3's I3 / round 6's I2: the plan-stage rule is unconditional
    (no existence gate); every other phase is scoped-else-flat."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))

    def test_plan_stage_phase_resolves_scoped_with_no_existence_gate(self) -> None:
        # No .ai-review/wi-1/ directory exists at all.
        for phase in sorted(evidence.PLAN_STAGE_PHASES):
            with self.subTest(phase=phase):
                bundle_dir = evidence.resolve_bundle_dir(self.root, "wi-1", phase=phase)
                self.assertEqual(bundle_dir, Path(".ai-review/wi-1/current"))

    def test_implementation_stage_phase_resolves_flat_with_no_scoped_directory(self) -> None:
        bundle_dir = evidence.resolve_bundle_dir(
            self.root, "wi-1", phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        )
        self.assertEqual(bundle_dir, Path(".ai-review/current"))

    def test_implementation_stage_phase_resolves_scoped_once_the_root_directory_exists(self) -> None:
        (self.root / ".ai-review" / "wi-1").mkdir(parents=True)
        bundle_dir = evidence.resolve_bundle_dir(
            self.root, "wi-1", phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        )
        self.assertEqual(bundle_dir, Path(".ai-review/wi-1/current"))


class FeedbackDirResolutionTest(unittest.TestCase):
    """`resolve_feedback_dir` is keyed on the feedback leaf's own
    existence, never on the work item's root directory the way
    `resolve_bundle_dir`/`resolve_rejected_marker_path` are
    (`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location": "`feedback/`
    ... keyed on `.ai-review/<work_item_id>/feedback/`'s own existence").
    `test_resolves_flat_when_work_item_root_exists_but_feedback_subdir_does_not`
    is the regression case: a work item that already has a scoped bundle
    round (`.ai-review/<work_item_id>/` exists, e.g. holding `current/`)
    but has never had a scoped `feedback/` round of its own must still
    resolve flat, matching `scripts/workflow_fingerprint.py`'s own
    `resolve_feedback_dir` exactly -- before this fix, this function
    returned the scoped path instead, disagreeing with the resolver every
    real Workflow command actually uses."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))

    def test_resolves_flat_when_no_scoped_layout_exists(self) -> None:
        # No .ai-review/wi-1/ directory exists at all.
        self.assertEqual(evidence.resolve_feedback_dir(self.root, "wi-1"),
                          Path(".ai-review/feedback"))

    def test_resolves_flat_when_work_item_root_exists_but_feedback_subdir_does_not(self) -> None:
        (self.root / ".ai-review" / "wi-1" / "current").mkdir(parents=True)
        self.assertEqual(evidence.resolve_feedback_dir(self.root, "wi-1"),
                          Path(".ai-review/feedback"))

    def test_resolves_scoped_once_the_feedback_subdir_itself_exists(self) -> None:
        (self.root / ".ai-review" / "wi-1" / "feedback").mkdir(parents=True)
        self.assertEqual(evidence.resolve_feedback_dir(self.root, "wi-1"),
                          Path(".ai-review/wi-1/feedback"))


class RejectedMarkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))

    def test_absent_marker_is_not_rejected(self) -> None:
        rejected, detail = evidence.rejected_marker_detail(self.root, "wi-1")
        self.assertFalse(rejected)
        self.assertIsNone(detail)

    def test_scoped_marker_is_rejected_with_its_detail(self) -> None:
        (self.root / ".ai-review" / "wi-1").mkdir(parents=True)
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="stale summary")
        rejected, detail = evidence.rejected_marker_detail(self.root, "wi-1")
        self.assertTrue(rejected)
        self.assertEqual(detail, "stale summary")

    def test_flat_layout_marker_is_rejected_via_the_stage_less_rule(self) -> None:
        """The marker follows the stage-less scoped-else-flat rule, unlike
        the plan-stage ``<bundle_dir>`` beside it: a flat-layout target
        (no ``.ai-review/<work_item_id>/``) with ``.ai-review/REJECTED``
        present is still caught."""
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=False)
        rejected, detail = evidence.rejected_marker_detail(self.root, "wi-1")
        self.assertTrue(rejected)
        self.assertEqual(evidence.resolve_rejected_marker_path(self.root, "wi-1"),
                          Path(".ai-review/REJECTED"))


class DecideWithdrawnBundleTest(unittest.TestCase):
    """A REJECTED marker yields the withdrawn-bundle outcome ahead of
    every other row."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")

    def test_awaiting_local_plan_review_reports_withdrawn_ahead_of_the_ordinary_row(self) -> None:
        (self.root / ".ai-review" / "wi-1").mkdir(parents=True)
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="finalize failed")
        managed_repo = fixtures.build_target_managed_repository(self.root)
        work_item = fixtures.build_work_item_view(phase="AWAITING_LOCAL_PLAN_REVIEW")
        result = evidence.decide(managed_repo, snapshot=None, work_item=work_item)
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertIsNotNone(result.gate)
        self.assertIn("finalize failed", result.gate.what_is_required)
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/REJECTED")))

    def test_flat_layout_withdrawn_bundle_is_caught_too(self) -> None:
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=False)
        managed_repo = fixtures.build_target_managed_repository(self.root)
        work_item = fixtures.build_work_item_view(phase="AWAITING_PLAN_APPROVAL")
        result = evidence.decide(managed_repo, snapshot=None, work_item=work_item)
        self.assertIsNotNone(result.gate)
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/REJECTED")))

    def test_non_bundle_bearing_phase_is_unaffected_by_a_rejected_marker(self) -> None:
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=False)
        managed_repo = fixtures.build_target_managed_repository(self.root)
        work_item = fixtures.build_work_item_view(phase="PLANNING")
        result = evidence.decide(managed_repo, snapshot=None, work_item=work_item)
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/milestone-plan wi-1")


class AwaitingLocalPlanReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self):
        work_item = fixtures.build_work_item_view(phase="AWAITING_LOCAL_PLAN_REVIEW")
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_no_feedback_is_automatic(self) -> None:
        result = self._decide()
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/review-plan wi-1")

    def test_block_verdict_is_a_gate(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW"),
        )
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertIsNotNone(result.gate)

    def test_local_stage_exemption_block_missing_base_commit_still_gates(self) -> None:
        """The polarity check: applying the admissibility rule uniformly
        here would turn this stop into a launch."""
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(
                status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW",
                reviewed_base_commit=None,
            ),
        )
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_loop_closure_removing_the_block_makes_it_automatic_again(self) -> None:
        feedback_path = fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW"),
        )
        self.assertFalse(self._decide().automatic)
        feedback_path.write_text(
            fixtures.build_review_feedback_text(status="APPROVE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW")
        )
        result = self._decide()
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/review-plan wi-1")


class AwaitingManualExternalPlanReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.head = fixtures.current_head(self.root)
        fixtures.write_manifest(
            self.root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=self.head),
        )
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _work_item(self, **overrides):
        defaults = dict(
            phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
            base_commit="0" * 40,
            plan_review_stages={
                "review_content_id": "c" * 64,
                "LOCAL_MODEL_PLAN_REVIEW": {"verdict": "APPROVE", "bundle_id": "b" * 64},
            },
        )
        defaults.update(overrides)
        return fixtures.build_work_item_view(**defaults)

    def _decide(self, work_item):
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_no_feedback_is_a_gate(self) -> None:
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)
        self.assertEqual(result.gate.safe_resume_command, "/record-manual-plan-review wi-1")

    def test_local_role_feedback_does_not_satisfy_the_arrival_test(self) -> None:
        """Round 3's B1 negative assertion: a LOCAL_MODEL_PLAN_REVIEW file
        whose Reviewed bundle ID matches the current bundle must still
        gate, never launch /record-manual-plan-review."""
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_admissible_manual_role_approve_is_automatic(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        result = self._decide(self._work_item())
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/record-manual-plan-review wi-1")

    def test_legacy_lowercase_role_is_accepted(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewer_role="manual_external_plan_review",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        result = self._decide(self._work_item())
        self.assertTrue(result.automatic)

    def test_missing_reviewed_base_commit_is_hard_ext_plan_r38_b1(self) -> None:
        """Round 38's EXT-PLAN-R38-B1 discriminating fixture: manual-role
        REVISE, current review_content_id and bundle_id, no Reviewed base
        commit line -- must not be admitted."""
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit=None,
                reviewed_content_id="c" * 64,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)
        self.assertIn("Reviewed base commit", result.gate.what_is_required)

    def test_advisory_bundle_id_mismatch_stays_automatic_ext_plan_r38_i1(self) -> None:
        """A wrapper-only regeneration: bundle_id differs, every hard
        clause holds -- automatic, and the mismatch is reported."""
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="a" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        result = self._decide(self._work_item())
        self.assertTrue(result.automatic)
        self.assertIn("bundle_id mismatch", result.reason)

    def test_stale_review_content_id_is_hard(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="stale" + "0" * 59,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_no_current_local_approval_is_hard(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        work_item = self._work_item(plan_review_stages={"review_content_id": "c" * 64})
        result = self._decide(work_item)
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_stale_generation_head_is_hard(self) -> None:
        fixtures.write_manifest(
            self.root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head="9" * 40),
        )
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_block_is_a_gate(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="BLOCK", reviewer_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                reviewed_content_id="c" * 64,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)


class AwaitingExternalPlanReviewV1Test(unittest.TestCase):
    """The ``"1"``-governed disambiguation: three sub-cases."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.head = fixtures.current_head(self.root)
        fixtures.write_manifest(
            self.root, ".ai-review/wi-1/current",
            fixtures.build_manifest_text(bundle_id="b" * 64, generation_head=self.head),
        )
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _work_item(self, **overrides):
        defaults = dict(
            phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
            base_commit="0" * 40,
        )
        defaults.update(overrides)
        return fixtures.build_work_item_view(**defaults)

    def _decide(self, work_item):
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_no_feedback_is_a_gate(self) -> None:
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_approve_is_a_gate_naming_the_user_only_resume_command(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertIsNotNone(result.gate)
        self.assertEqual(result.gate.safe_resume_command, "/approve-review plan wi-1")

    def test_revise_with_admissible_binding_is_automatic(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
            ),
        )
        result = self._decide(self._work_item())
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/apply-plan-review wi-1")

    def test_bundle_id_mismatch_is_hard_here_unlike_the_manual_column(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="a" * 64, reviewed_base_commit="0" * 40,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_missing_work_item_field_is_hard(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="b" * 64, reviewed_base_commit="0" * 40,
                work_item=None,
            ),
        )
        result = self._decide(self._work_item())
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)


class AwaitingExternalImplementationReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.head = fixtures.current_head(self.root)
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, **overrides):
        defaults = dict(phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", base_commit="0" * 40)
        defaults.update(overrides)
        work_item = fixtures.build_work_item_view(**defaults)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_no_feedback_names_handing_the_bundle_to_a_reviewer(self) -> None:
        fixtures.write_manifest(
            self.root, ".ai-review/current", fixtures.build_manifest_text(generation_head=self.head),
        )
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertIn("external reviewer", result.gate.what_is_required)

    def test_revise_names_apply_implementation_review(self) -> None:
        fixtures.write_manifest(
            self.root, ".ai-review/current", fixtures.build_manifest_text(generation_head=self.head),
        )
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(status="REVISE"),
        )
        result = self._decide()
        self.assertEqual(result.gate.safe_resume_command, "/apply-implementation-review wi-1")

    def test_approve_names_the_user_only_approve_review(self) -> None:
        fixtures.write_manifest(
            self.root, ".ai-review/current", fixtures.build_manifest_text(generation_head=self.head),
        )
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(status="APPROVE"),
        )
        result = self._decide()
        self.assertEqual(result.gate.safe_resume_command, "/approve-review implementation wi-1")

    def test_stale_generation_head_names_recover_implementation_provenance(self) -> None:
        fixtures.write_manifest(
            self.root, ".ai-review/current", fixtures.build_manifest_text(generation_head="9" * 40),
        )
        result = self._decide()
        self.assertEqual(result.gate.safe_resume_command, "/recover-implementation-provenance wi-1")
        self.assertIn("refuses", result.gate.what_is_required)


class AwaitingManualExternalImplementationReviewTest(unittest.TestCase):
    """Revision 64's own three-way sub-case (CP4B's fourth evidence-needing
    phase): report-only at every sub-case, and no legacy-cased role alias,
    unlike its plan-stage counterpart above."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, **overrides):
        defaults = dict(
            phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            base_commit="0" * 40, governing_workflow_version="2.2",
        )
        defaults.update(overrides)
        work_item = fixtures.build_work_item_view(**defaults)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_no_feedback_names_handing_the_bundle_to_a_reviewer(self) -> None:
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertIn("manual external reviewer", result.gate.what_is_required)
        self.assertEqual(result.gate.safe_resume_command,
                          "/record-manual-implementation-review wi-1")

    def test_local_role_feedback_does_not_satisfy_the_arrival_test(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="LOCAL_MODEL_IMPLEMENTATION_REVIEW",
            ),
        )
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)

    def test_lowercase_role_is_not_accepted_unlike_the_plan_stage(self) -> None:
        """No legacy-cased alias at this stage: an exact-spelling mismatch
        is refused, mirroring ``validate_manual_implementation_review_
        preconditions`` -- unlike ``AwaitingManualExternalPlanReviewTest``'s
        own ``test_legacy_lowercase_role_is_accepted``."""
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="manual_external_implementation_review",
            ),
        )
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertIsNotNone(result.gate)
        self.assertIn("upload", result.gate.what_is_required.lower())

    def test_block_is_a_gate_naming_explicit_resolution(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(
                status="BLOCK", reviewer_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            ),
        )
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertIn("resolves", result.gate.what_is_required)

    def test_verdict_on_file_names_record_manual_implementation_review_and_never_launches(self) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(
                status="APPROVE", reviewer_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            ),
        )
        result = self._decide()
        # Report-only at every sub-case: unlike the plan-stage gate, an
        # admissible verdict here still never becomes an automatic action.
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertEqual(result.gate.safe_resume_command,
                          "/record-manual-implementation-review wi-1")

    def test_bundle_bearing_phase_reports_a_withdrawn_bundle_ahead_of_the_ordinary_row(self) -> None:
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=False, detail="finalize failed")
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertIn("finalize failed", result.gate.what_is_required)
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/REJECTED")))


class AwaitingFunctionalReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.base_commit = fixtures.current_head(self.root)
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, **overrides):
        defaults = dict(
            phase="AWAITING_FUNCTIONAL_REVIEW", base_commit=self.base_commit,
            implementation_revision=1,
        )
        defaults.update(overrides)
        work_item = fixtures.build_work_item_view(**defaults)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _commit_checklist_evidence(self, work_item_id: str, implementation_revision: int) -> str:
        (self.root / "docs" / "ACTIVE_MILESTONE.md").parent.mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "ACTIVE_MILESTONE.md").write_text("checklist content\n")
        blob = fixtures.run(
            ["git", "hash-object", "--", "docs/ACTIVE_MILESTONE.md"], cwd=self.root,
        ).stdout.strip()
        fixtures.run(["git", "add", "-A"], cwd=self.root)
        message = (
            f"checklist\n\nWorkflow-Functional-Checklist: "
            f"{work_item_id}/{implementation_revision}/{blob}\n"
            f"Workflow-Work-Item: {work_item_id}\n"
        )
        fixtures.run(["git", "commit", "-q", "-m", message], cwd=self.root)
        return fixtures.current_head(self.root)

    def test_no_checklist_evidence_names_prepare_functional_review(self) -> None:
        result = self._decide()
        self.assertEqual(result.gate.safe_resume_command, "/prepare-functional-review wi-1")

    def test_checklist_current_and_no_findings_asks_for_manual_testing(self) -> None:
        self._commit_checklist_evidence("wi-1", 1)
        result = self._decide()
        self.assertEqual(result.gate.safe_resume_command, "/apply-functional-review wi-1")
        self.assertIn("manual functional", result.gate.what_is_required)

    def test_unconsumed_findings_are_automatic(self) -> None:
        self._commit_checklist_evidence("wi-1", 1)
        fixtures.write_review_feedback(  # reuse the writer; FUNCTIONAL_REVIEW.md has no fields
            self.root, ".ai-review/feedback", "some findings\n",
        )
        (self.root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.md").write_text("findings\n")
        result = self._decide()
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/apply-functional-review wi-1")

    def test_consumed_findings_with_no_incomplete_children_names_accept_milestone(self) -> None:
        self._commit_checklist_evidence("wi-1", 1)
        findings_path = self.root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.md"
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_text("all clean\n")
        blob = fixtures.run(
            ["git", "hash-object", "--", str(findings_path)], cwd=self.root,
        ).stdout.strip()
        marker_path = self.root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.consumed"
        marker_path.write_text(blob + "\n")
        result = self._decide()
        self.assertEqual(result.gate.safe_resume_command, "/accept-milestone wi-1")

    def test_consumed_findings_with_incomplete_children_gates_on_that_instead(self) -> None:
        self._commit_checklist_evidence("wi-1", 1)
        findings_path = self.root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.md"
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_text("all clean\n")
        blob = fixtures.run(
            ["git", "hash-object", "--", str(findings_path)], cwd=self.root,
        ).stdout.strip()
        marker_path = self.root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.consumed"
        marker_path.write_text(blob + "\n")
        result = self._decide(incomplete_children=("wi-1-remediation-1",))
        self.assertIsNone(result.action)
        self.assertIn("wi-1-remediation-1", result.gate.what_is_required)
        self.assertNotEqual(result.gate.safe_resume_command, "/accept-milestone wi-1")


if __name__ == "__main__":
    unittest.main()
