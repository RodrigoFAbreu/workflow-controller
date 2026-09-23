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


class ReadFeedbackFieldsRealArtifactShapeTest(unittest.TestCase):
    """CP2/REQ-4 (workflow-controller-gen1-correctness-hardening): the
    label the code reads for ``reviewed_content_id`` must match what a
    real plan-stage ``REVIEW_FEEDBACK.md`` actually carries, not a label
    ``tests/fixtures.py`` happens to agree with by construction. This
    text is a literal copy of the real, plan-stage-shaped
    ``.ai-review/feedback/REVIEW_FEEDBACK.md`` produced by this work
    item's own round-1 plan review (``Reviewer role:
    MANUAL_EXTERNAL_PLAN_REVIEW``) -- never built through
    ``fixtures.build_review_feedback_text``, which would make this test
    self-confirming against the recogniser's own literal instead of
    against the real artifact contract."""

    _REAL_FEEDBACK_TEXT = (
        "# Review Decision\n"
        "\n"
        "Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW\n"
        "Status: APPROVE\n"
        "\n"
        "Reviewed bundle ID: 361c4fbd0194ca935d008255a580eb6e16a5a2133ea6e044f2c824db2c2f250b\n"
        "Reviewed base commit: 398caa13f26233b338ca1573a9dc4b3a576a8e50\n"
        "Work item: workflow-controller-gen1-correctness-hardening\n"
        "Reviewed review content ID: b80a6044ddbb3613ade692fa97bac00d11b567f7dfd41e468031e235ffe45c06\n"
        "Round: 1\n"
        "Completed at: 2026-09-20T12:15:00Z\n"
        "\n"
        "## Verdict\n"
        "\n"
        "APPROVE.\n"
    )

    def test_reviewed_content_id_resolves_from_the_real_plan_stage_label(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            feedback_dir = Path("feedback")
            (root / feedback_dir).mkdir(parents=True)
            (root / feedback_dir / "REVIEW_FEEDBACK.md").write_text(self._REAL_FEEDBACK_TEXT)

            fields = evidence.read_feedback_fields(root, feedback_dir)

            self.assertEqual(fields["reviewer_role"], "MANUAL_EXTERNAL_PLAN_REVIEW")
            self.assertEqual(
                fields["reviewed_content_id"],
                "b80a6044ddbb3613ade692fa97bac00d11b567f7dfd41e468031e235ffe45c06",
            )


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


class ReadManifestFieldsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_identity_lines_are_read_additively(self) -> None:
        fixtures.write_manifest(self.root, "b", fixtures.build_manifest_text(
            bundle_id="b" * 64, generation_head="0" * 40,
            stage="plan", work_item_id="wi-1", plan_revision=3,
        ))
        self.assertEqual(evidence.read_manifest_fields(self.root, Path("b")), {
            "bundle_id": "b" * 64, "generation_head": "0" * 40,
            "stage": "plan", "work_item_id": "wi-1", "plan_revision": "3", "_exists": True,
        })

    def test_absent_lines_and_absent_file_read_as_none(self) -> None:
        fixtures.write_manifest(self.root, "b", fixtures.build_manifest_text())
        fields = evidence.read_manifest_fields(self.root, Path("b"))
        self.assertTrue(fields["_exists"])
        self.assertIsNone(fields["stage"])
        self.assertIsNone(fields["work_item_id"])
        self.assertIsNone(fields["plan_revision"])
        missing = evidence.read_manifest_fields(self.root, Path("nowhere"))
        self.assertFalse(missing["_exists"])
        self.assertTrue(all(missing[k] is None for k in missing if k != "_exists"))

    def test_fixture_output_is_unchanged_when_identity_lines_are_not_requested(self) -> None:
        self.assertEqual(
            fixtures.build_manifest_text(bundle_id="c" * 64, generation_head="1" * 40),
            "# Bundle manifest\n\nbundle_id: " + "c" * 64 + "\ngeneration_head: " + "1" * 40
            + "\n\n## Protected paths\n\n",
        )


class PlanBundleCoherenceTest(unittest.TestCase):
    """CP2: the single plan-bundle coherence reader -- one test per
    clause, each failing independently against an otherwise coherent
    manifest."""

    BUNDLE = Path(".ai-review/wi-1/current")

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, **overrides) -> None:
        fields = {"bundle_id": "b" * 64, "stage": "plan", "work_item_id": "wi-1", "plan_revision": 11}
        fields.update(overrides)
        fixtures.write_manifest(self.root, self.BUNDLE, fixtures.build_manifest_text(**fields))

    def _assert_incoherent(self, plan_revision, *fragments: str) -> None:
        coherent, detail = evidence.plan_bundle_coherence(self.root, "wi-1", plan_revision)
        self.assertFalse(coherent)
        for fragment in fragments:
            self.assertIn(fragment, detail)

    def test_coherent_manifest(self) -> None:
        self._write()
        coherent, detail = evidence.plan_bundle_coherence(self.root, "wi-1", 11)
        self.assertTrue(coherent)
        self.assertIn("plan_revision 11", detail)

    def test_missing_manifest(self) -> None:
        (self.root / self.BUNDLE).mkdir(parents=True)
        self._assert_incoherent(11, "MANIFEST.md", "missing or unreadable")

    def test_withdrawn_current_directory(self) -> None:
        """A completed withdrawal renames ``current/`` away and removes its
        own marker, leaving only the quarantine sibling behind."""
        self._write()
        (self.root / self.BUNDLE).rename(self.root / ".ai-review/wi-1/current.rejected-abc")
        self._assert_incoherent(11, "absent", "withdrawn")

    def test_implementation_stage_manifest(self) -> None:
        self._write(stage="implementation")
        self._assert_incoherent(11, "manifest stage 'implementation'")

    def test_missing_stage_line(self) -> None:
        self._write(stage=None)
        self._assert_incoherent(11, "manifest stage None")

    def test_wrong_work_item_id(self) -> None:
        self._write(work_item_id="wi-2")
        self._assert_incoherent(11, "manifest work_item_id 'wi-2'")

    def test_missing_plan_revision(self) -> None:
        self._write(plan_revision=None)
        self._assert_incoherent(11, "plan_revision is missing")

    def test_non_integer_plan_revision(self) -> None:
        for bad in ("abc", "+11", "1_1", "11.0"):
            with self.subTest(value=bad):
                self._write(plan_revision=bad)
                self._assert_incoherent(11, repr(bad), "not an integer")

    def test_stale_plan_revision(self) -> None:
        """The observed RepFlow shape: state at revision 11, bundle at 10."""
        self._write(plan_revision=10)
        self._assert_incoherent(11, "manifest plan_revision 10 != state plan_revision 11")

    def test_missing_bundle_id(self) -> None:
        self._write(bundle_id=None)
        self._assert_incoherent(11, "bundle_id is missing")

    def test_state_plan_revision_none_fails_closed(self) -> None:
        self._write()
        self._assert_incoherent(None, "state plan_revision is None")


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
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
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
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=self.head),
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
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head="9" * 40),
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
            fixtures.build_plan_manifest_text("wi-1", 1, generation_head=self.head),
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

    def test_2_2_item_reports_identically_to_2_1_at_this_reused_terminal_phase(self) -> None:
        """`MILESTONE_WORKFLOW.md:425-434`'s "byte-for-byte" reuse claim,
        made concrete: this terminal phase is reached by a "2.2" item only
        once both its `implementation_review_stages` ledger stages
        (`LOCAL_MODEL_IMPLEMENTATION_REVIEW` and
        `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`) already read `APPROVE` --
        but `_decide_awaiting_external_implementation_review` consults
        neither `governing_workflow_version` nor that ledger at all, only
        `MANIFEST.md`/`REVIEW_FEEDBACK.md` on disk, so a "2.2" item and a
        "2.1"/"1" item in the identical bundle state must decide
        identically. `AwaitingExternalImplementationReviewTest` had no
        `governing_workflow_version="2.2"` fixture anywhere before this."""
        fixtures.write_manifest(
            self.root, ".ai-review/current", fixtures.build_manifest_text(generation_head=self.head),
        )
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(status="APPROVE"),
        )
        result_1 = self._decide(governing_workflow_version="1")
        result_21 = self._decide(governing_workflow_version="2.1")
        result_22 = self._decide(governing_workflow_version="2.2")
        for other in (result_21, result_22):
            self.assertEqual(result_1.automatic, other.automatic)
            self.assertEqual(result_1.declined, other.declined)
            self.assertEqual(result_1.action, other.action)
            self.assertEqual(result_1.gate.what_is_required, other.gate.what_is_required)
            self.assertEqual(result_1.gate.safe_resume_command, other.gate.safe_resume_command)
            self.assertEqual(result_1.gate.artifact_path, other.gate.artifact_path)
        self.assertEqual(result_22.gate.safe_resume_command, "/approve-review implementation wi-1")


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

    def test_revise_status_also_names_record_manual_implementation_review(self) -> None:
        """CP4's own genuinely-uncovered sub-case: ``Status: REVISE`` on
        file, admissible role. Falls into the same non-BLOCK branch as
        ``APPROVE`` above (``_decide_awaiting_manual_external_implementation_
        review`` only special-cases ``BLOCK``) -- report-only here, unlike
        the plan stage's own REVISE handling, which is automatic."""
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewer_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            ),
        )
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertEqual(result.gate.safe_resume_command,
                          "/record-manual-implementation-review wi-1")


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



class StalePlanBundleGateTest(unittest.TestCase):
    """CP4 (worker-execution hardening): at the two phases whose automatic
    actions consume the current plan bundle, a bundle incoherent with the
    state's ``plan_revision`` gates -- and the gate advertises the ordered,
    executable recovery, never the bare generator."""

    BASE = "a" * 40
    GATED = ("AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
    BUNDLE = Path(".ai-review/wi-1/current")

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, phase: str, plan_revision: int = 2, **overrides):
        work_item = fixtures.build_work_item_view(
            phase=phase, governing_workflow_version="2.2", plan_revision=plan_revision,
            base_commit=self.BASE, **overrides,
        )
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _write_author_files(self) -> None:
        for name in ("REVIEW_REQUEST.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt"):
            (self.root / self.BUNDLE / name).write_text("previous round\n")

    def _assert_in_order(self, text: str, needles: list[str]) -> None:
        position = -1
        for needle in needles:
            found = text.find(needle, position + 1)
            self.assertGreater(found, position, f"{needle!r} missing or out of order in {text!r}")
            position = found

    def _assert_executable_recovery(self, gate, phase: str) -> None:
        needles = [
            "REVIEW_REQUEST.md", "review_content_id",
            "compute_review_content_id_plan_stage_for_work_item",
            "TEST_RESULTS.md", "stage: plan (revision 2)", "head:",
            f"scripts/prepare-ai-review.sh {self.BASE} plan wi-1",
            "refuses at preflight",
        ]
        self._assert_in_order(gate.safe_resume_command, needles)
        self._assert_in_order(gate.what_is_required, needles)
        self.assertNotEqual(gate.safe_resume_command, evidence._regeneration_command(phase, "wi-1"))

    def test_stale_manifest_gates_at_both_phases(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        self._write_author_files()
        for phase in self.GATED:
            with self.subTest(phase=phase):
                result = self._decide(phase)
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                self.assertFalse(result.declined)
                self.assertEqual(result.gate.artifact_path, str(self.BUNDLE / "MANIFEST.md"))
                self.assertIn("manifest plan_revision 1 != state plan_revision 2",
                              result.gate.what_is_required)
                self._assert_executable_recovery(result.gate, phase)
                self.assertIn("refresh", result.gate.safe_resume_command)
                self.assertNotIn("0. ", result.gate.safe_resume_command)
                self.assertNotIn("Review request format", result.gate.safe_resume_command)

    def test_coherent_manifest_leaves_the_decision_unchanged(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 2)
        result = self._decide("AWAITING_LOCAL_PLAN_REVIEW")
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/review-plan wi-1")
        result = self._decide("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(result.gate.safe_resume_command, "/record-manual-plan-review wi-1")

    def test_staleness_is_checked_ahead_of_the_local_block_gate(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(status="BLOCK", reviewer_role="LOCAL_MODEL_PLAN_REVIEW"),
        )
        result = self._decide("AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(result.gate.artifact_path, str(self.BUNDLE / "MANIFEST.md"))

    def test_rejected_marker_wins_over_staleness(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="step rename failed")
        for phase in self.GATED:
            with self.subTest(phase=phase):
                result = self._decide(phase)
                self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/REJECTED")))

    def test_rejected_marker_gate_at_the_gated_phases_carries_the_recovery_steps(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        self._write_author_files()
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="step rename failed")
        for phase in self.GATED:
            with self.subTest(phase=phase):
                gate = self._decide(phase).gate
                self.assertIn("step rename failed", gate.safe_resume_command)
                self.assertIn("step rename failed", gate.what_is_required)
                self._assert_executable_recovery(gate, phase)
                # Manual-external implementation review round 1, O1: the
                # marker's surviving path can be current/ itself, so the
                # text must never read as an instruction to delete it.
                for text in (gate.safe_resume_command, gate.what_is_required):
                    self.assertNotIn("cleared first", text)
                    self.assertIn("do not delete surviving author files", text)
                    self.assertIn("a successful generation then clears the marker itself", text)

    def test_rejected_marker_gate_elsewhere_is_unchanged(self) -> None:
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="step rename failed")
        for phase, version in (
            ("AWAITING_EXTERNAL_PLAN_REVIEW", "1"),
            ("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.1"),
        ):
            with self.subTest(phase=phase):
                work_item = fixtures.build_work_item_view(
                    phase=phase, governing_workflow_version=version, base_commit=self.BASE,
                )
                result = evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)
                regen = evidence._regeneration_command(phase, "wi-1")
                self.assertEqual(result.gate.safe_resume_command, regen)
                self.assertEqual(
                    result.gate.what_is_required,
                    f"the current bundle was withdrawn (step rename failed); regenerate it with "
                    f"{regen} before any further command runs",
                )
                self.assertEqual(
                    result.evidence,
                    ("REJECTED marker present at .ai-review/wi-1/REJECTED: step rename failed",),
                )

    def test_withdrawn_bundle_asks_for_writes_and_names_the_quarantine(self) -> None:
        quarantine = self.root / ".ai-review" / "wi-1" / "current.rejected-0123abcd"
        quarantine.mkdir(parents=True)
        (quarantine / "REVIEW_REQUEST.md").write_text("previous round\n")
        for phase in self.GATED:
            with self.subTest(phase=phase):
                gate = self._decide(phase).gate
                self.assertIn("is absent (withdrawn or never generated)", gate.what_is_required)
                self._assert_executable_recovery(gate, phase)
                for text in (gate.safe_resume_command, gate.what_is_required):
                    self._assert_in_order(text, [
                        f"0. write {self.BUNDLE / 'CONTEXT_FILES.txt'}",
                        ".ai-review/wi-1/current.rejected-0123abcd/",
                        f"1. write {self.BUNDLE / 'REVIEW_REQUEST.md'}",
                        "Review request format",
                        f"2. write {self.BUNDLE / 'TEST_RESULTS.md'}",
                        "3. run scripts/prepare-ai-review.sh",
                    ])
                    self.assertNotIn("refresh", text)

    def test_newest_quarantine_directory_is_named(self) -> None:
        import os

        older = self.root / ".ai-review" / "wi-1" / "current.rejected-zzzz"
        newer = self.root / ".ai-review" / "wi-1" / "current.rejected-aaaa"
        older.mkdir(parents=True)
        newer.mkdir()
        os.utime(older, ns=(1_000_000_000, 1_000_000_000))
        os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
        gate = self._decide("AWAITING_LOCAL_PLAN_REVIEW").gate
        self.assertIn("current.rejected-aaaa/", gate.safe_resume_command)
        self.assertNotIn("current.rejected-zzzz", gate.safe_resume_command)

    def test_first_round_shape_with_absent_author_files_asks_for_writes(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        for phase in self.GATED:
            with self.subTest(phase=phase):
                gate = self._decide(phase).gate
                self._assert_executable_recovery(gate, phase)
                self._assert_in_order(gate.safe_resume_command, [
                    f"0. write {self.BUNDLE / 'CONTEXT_FILES.txt'}",
                    f"1. write {self.BUNDLE / 'REVIEW_REQUEST.md'}", "Review request format",
                    f"2. write {self.BUNDLE / 'TEST_RESULTS.md'}",
                ])
                self.assertNotIn("current.rejected-", gate.safe_resume_command)

    def test_one_missing_author_file_is_enough_for_the_write_variant(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        self._write_author_files()
        (self.root / self.BUNDLE / "CONTEXT_FILES.txt").unlink()
        gate = self._decide("AWAITING_LOCAL_PLAN_REVIEW").gate
        self.assertTrue(gate.safe_resume_command.startswith("0. write "))

    def test_zero_byte_generator_stubs_ask_for_writes_and_name_the_quarantine(self) -> None:
        """Manual-external implementation review round 1, ``I1``: a later
        generator run that refused or withdrew after ``prepare-ai-review.sh``
        created its empty author-file stubs leaves ``current/`` present, all
        three author files present but zero bytes, no ``MANIFEST.md``, and
        the previous round's real author files only in the quarantine. The
        stubs carry nothing to refresh, so this is the "write" variant --
        step 0 restoring ``CONTEXT_FILES.txt`` from the quarantine, the
        request format named -- never "refresh"."""
        quarantine = self.root / ".ai-review" / "wi-1" / "current.rejected-0123abcd"
        quarantine.mkdir(parents=True)
        for name in ("REVIEW_REQUEST.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt"):
            (quarantine / name).write_text("previous round\n")
        bundle = self.root / self.BUNDLE
        bundle.mkdir(parents=True)
        for name in ("REVIEW_REQUEST.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt"):
            (bundle / name).write_bytes(b"")
        self.assertFalse((bundle / "MANIFEST.md").exists())
        for phase in self.GATED:
            with self.subTest(phase=phase):
                result = self._decide(phase)
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                gate = result.gate
                self.assertIn("MANIFEST.md is missing or unreadable", gate.what_is_required)
                self._assert_executable_recovery(gate, phase)
                for text in (gate.safe_resume_command, gate.what_is_required):
                    self._assert_in_order(text, [
                        f"0. write {self.BUNDLE / 'CONTEXT_FILES.txt'}",
                        ".ai-review/wi-1/current.rejected-0123abcd/",
                        f"1. write {self.BUNDLE / 'REVIEW_REQUEST.md'}",
                        "Review request format",
                        f"2. write {self.BUNDLE / 'TEST_RESULTS.md'}",
                        "Review request format",
                        "3. run scripts/prepare-ai-review.sh",
                    ])
                    self.assertNotIn("refresh", text)

    def test_one_zero_byte_author_file_is_enough_for_the_write_variant(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        self._write_author_files()
        (self.root / self.BUNDLE / "CONTEXT_FILES.txt").write_bytes(b"")
        gate = self._decide("AWAITING_LOCAL_PLAN_REVIEW").gate
        self.assertTrue(gate.safe_resume_command.startswith("0. write "))
        self.assertNotIn("refresh", gate.safe_resume_command)

    def test_base_commit_placeholder_when_the_state_carries_none(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        work_item = fixtures.build_work_item_view(
            phase="AWAITING_LOCAL_PLAN_REVIEW", plan_revision=2, base_commit=None,
        )
        gate = evidence.decide(self.managed_repo, snapshot=None, work_item=work_item).gate
        self.assertIn("scripts/prepare-ai-review.sh <base-sha> plan wi-1", gate.safe_resume_command)

    def test_external_plan_review_v1_is_not_gated_on_staleness(self) -> None:
        fixtures.write_plan_manifest(
            self.root, "wi-1", 1, generation_head=fixtures.current_head(self.root),
        )
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(
                status="REVISE", reviewed_bundle_id="b" * 64, reviewed_base_commit=self.BASE,
            ),
        )
        work_item = fixtures.build_work_item_view(
            phase="AWAITING_EXTERNAL_PLAN_REVIEW", governing_workflow_version="1",
            plan_revision=2, base_commit=self.BASE,
        )
        result = evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)
        self.assertTrue(result.automatic, result.reason)
        self.assertEqual(result.action.command, "/apply-plan-review wi-1")

    def test_implementation_stage_phases_are_not_gated_on_staleness(self) -> None:
        fixtures.write_plan_manifest(self.root, "wi-1", 1)
        for phase in ("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                      "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"):
            with self.subTest(phase=phase):
                result = self._decide(phase, implementation_revision=1)
                self.assertFalse(any("plan bundle is not coherent" in line for line in result.evidence))
                self.assertNotIn("before any plan review runs", result.reason)
                if result.gate is not None:
                    self.assertNotIn("before any plan review runs", result.gate.what_is_required)


if __name__ == "__main__":
    unittest.main()
