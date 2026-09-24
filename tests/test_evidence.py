"""Tests for ``controller.evidence`` (CP4B, ``REQ-T6``/``REQ-T7``).

Every test here is one the plan's own "Tests" list under "CP4 / CP4B --
Next-action decision engine and human-gate classification" tags
``(CP4B)`` -- the evidence-reading disambiguations CP4's own placeholder
rows explicitly defer to this checkpoint, plus the withdrawn-bundle
outcome and the two evidence-driven report-only phases.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import decision, evidence
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
        # The implementation-stage labels (automatic-lifecycle-orchestration
        # CP1) are absent from a plan-stage manifest, so each reads None.
        self.assertEqual(evidence.read_manifest_fields(self.root, Path("b")), {
            "bundle_id": "b" * 64, "generation_head": "0" * 40,
            "stage": "plan", "work_item_id": "wi-1", "plan_revision": "3", "_exists": True,
            "review_content_id": None, "implementation_revision": None,
            "reviewed_implementation_head": None, "worktree_root": None,
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

    # `test_2_2_item_reports_identically_to_2_1_at_this_reused_terminal_phase`
    # is replaced (automatic-lifecycle-orchestration CP4): "2.2" is now
    # ledger-, feedback- and pin-aware (`AwaitingExternalImplementationReviewTwoStageTest`
    # below), and the "1"/"2.1" half of that comparison is kept, byte for
    # byte, by `ExternalImplementationReviewGoldenTest`.


_REVIEWED_HEAD = "1" * 40
_LOCAL_APPROVE_LEDGER = fixtures.implementation_review_ledger("c" * 64, local_bundle_id="b" * 64)
_COMPLETE_LEDGER = fixtures.implementation_review_ledger(
    "c" * 64, local_bundle_id="b" * 64, manual_bundle_id="b" * 64,
)
_MANUAL_ROLE = "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
_LOCAL_ROLE = "LOCAL_MODEL_IMPLEMENTATION_REVIEW"


def _implementation_work_item(phase: str, **overrides):
    """A ``"2.2"`` work item at ``phase`` whose implementation round matches
    :func:`fixtures.write_implementation_bundle`'s default bundle."""
    fields = dict(
        phase=phase, governing_workflow_version="2.2", base_commit="0" * 40, implementation_revision=1,
        reviewed_implementation_head=_REVIEWED_HEAD, implementation_review_stages=_LOCAL_APPROVE_LEDGER,
    )
    fields.update(overrides)
    return fixtures.build_work_item_view(**fields)


class AwaitingManualExternalImplementationReviewTest(unittest.TestCase):
    """The ``"2.2"`` manual stage (automatic-lifecycle-orchestration CP4):
    no verdict, a local-role verdict, an inadmissible verdict and a
    ``BLOCK`` gate; an admissible ``APPROVE``/``REVISE`` is ingested
    automatically through ``/record-manual-implementation-review``, as the
    plan stage ingests through ``/record-manual-plan-review``. No
    legacy-cased role alias, unlike the plan stage. Every fixture carries a
    coherent (flat-layout) implementation bundle, so the implementation-
    bundle gate in front of the handler passes."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.bundle = fixtures.write_implementation_bundle(
            self.root, "wi-1", 1, scoped=False, reviewed_implementation_head=_REVIEWED_HEAD,
        )
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, **overrides):
        work_item = _implementation_work_item("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", **overrides)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _feedback(self, status: str, role: str | None = _MANUAL_ROLE, **fields) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/feedback",
            fixtures.build_review_feedback_text(status=status, reviewer_role=role, **fields),
        )

    def _assert_gate_never_launches(self, result) -> None:
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertIsNotNone(result.gate)

    def test_no_feedback_names_handing_the_bundle_to_a_reviewer(self) -> None:
        result = self._decide()
        self._assert_gate_never_launches(result)
        self.assertIn("manual external reviewer", result.gate.what_is_required)
        self.assertEqual(result.gate.safe_resume_command,
                         "/record-manual-implementation-review wi-1")
        # The "At every stop" values the user must hand over.
        self.assertIn(".ai-review/current", result.gate.what_is_required)
        self.assertIn(f"bundle_id {'b' * 64}", result.gate.what_is_required)
        self.assertIn(f"review_content_id {'c' * 64} from the ledger", result.gate.what_is_required)
        self.assertIn(_MANUAL_ROLE, result.gate.what_is_required)

    def test_local_role_feedback_does_not_satisfy_the_arrival_test(self) -> None:
        """A local-role verdict at the manual phase is never admitted, even
        an otherwise perfectly bound ``APPROVE``."""
        self._feedback("APPROVE", role=_LOCAL_ROLE)
        result = self._decide()
        self._assert_gate_never_launches(result)
        self.assertIn("upload", result.gate.what_is_required)
        self.assertIn(_LOCAL_ROLE, result.reason)

    def test_lowercase_role_is_not_accepted_unlike_the_plan_stage(self) -> None:
        """No legacy-cased alias at this stage: an exact-spelling mismatch
        is refused, mirroring ``validate_manual_implementation_review_
        preconditions`` -- unlike ``AwaitingManualExternalPlanReviewTest``'s
        own ``test_legacy_lowercase_role_is_accepted``."""
        self._feedback("APPROVE", role="manual_external_implementation_review")
        result = self._decide()
        self._assert_gate_never_launches(result)
        self.assertIn("upload", result.gate.what_is_required.lower())

    def test_block_is_a_gate_naming_explicit_resolution(self) -> None:
        self._feedback("BLOCK")
        result = self._decide()
        self._assert_gate_never_launches(result)
        self.assertIn("resolves", result.gate.what_is_required)
        self.assertEqual(result.gate.safe_resume_command, "/record-manual-implementation-review wi-1")

    def test_admissible_approve_is_ingested_automatically(self) -> None:
        """Was ``test_verdict_on_file_names_record_manual_implementation_review_and_never_launches``
        (report-only at every sub-case before CP4). With admissible fixtures
        -- a coherent manifest, ``REVIEW_REQUEST.md`` stating its content
        id, a local ``APPROVE`` in the ledger -- the verdict is ingested by
        the automatic ``/record-manual-implementation-review``."""
        self._feedback("APPROVE")
        result = self._decide()
        self.assertTrue(result.automatic, result.reason)
        self.assertFalse(result.declined)
        self.assertIsNone(result.gate)
        self.assertEqual(result.action.command, "/record-manual-implementation-review wi-1")
        self.assertEqual(result.evidence, ("admissible manual-stage Status: APPROVE",))

    def test_admissible_revise_is_ingested_automatically(self) -> None:
        """Was ``test_revise_status_also_names_record_manual_implementation_review``."""
        self._feedback("REVISE")
        result = self._decide()
        self.assertTrue(result.automatic, result.reason)
        self.assertEqual(result.action.command, "/record-manual-implementation-review wi-1")

    def test_inadmissible_verdict_names_the_command_and_never_launches(self) -> None:
        """The original "names ``/record-manual-implementation-review``,
        never launches" shape, re-pinned for an inadmissible verdict: here
        the ledger records no local ``APPROVE``."""
        self._feedback("APPROVE")
        result = self._decide(implementation_review_stages={"review_content_id": "c" * 64})
        self._assert_gate_never_launches(result)
        self.assertEqual(result.gate.safe_resume_command, "/record-manual-implementation-review wi-1")
        self.assertIn("not ingestible", result.gate.what_is_required)
        self.assertIn("local approval", result.gate.what_is_required)
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/feedback/REVIEW_FEEDBACK.md")))

    def test_inadmissible_verdict_names_every_failing_clause(self) -> None:
        self._feedback("APPROVE", reviewed_base_commit="9" * 40, reviewed_content_id="d" * 64)
        result = self._decide()
        self._assert_gate_never_launches(result)
        for clause in ("Reviewed base commit", "review_content_id"):
            self.assertIn(clause, result.gate.what_is_required)

    def test_revise_bound_to_another_bundle_gates_and_says_why(self) -> None:
        self._feedback("REVISE", reviewed_bundle_id="a" * 64)
        result = self._decide()
        self._assert_gate_never_launches(result)
        self.assertIn("Reviewed bundle ID", result.gate.what_is_required)
        self.assertIn("/apply-implementation-review step 1", result.gate.what_is_required)
        self.assertIn("assert_feedback_matches_bundle", result.gate.what_is_required)

    def test_approve_bound_to_another_bundle_stays_advisory_and_automatic(self) -> None:
        self._feedback("APPROVE", reviewed_bundle_id="a" * 64)
        result = self._decide()
        self.assertTrue(result.automatic, result.reason)
        self.assertEqual(result.action.command, "/record-manual-implementation-review wi-1")
        self.assertEqual(len(result.evidence), 2)
        self.assertIn("advisory only", result.evidence[1])
        self.assertIn("advisory only", result.reason)

    def test_review_request_disagreeing_with_the_manifest_gates(self) -> None:
        (self.root / self.bundle / "REVIEW_REQUEST.md").write_text("review_content_id: " + "d" * 64 + "\n")
        self._feedback("APPROVE")
        result = self._decide()
        self._assert_gate_never_launches(result)
        self.assertIn("REVIEW_REQUEST.md review_content_id", result.gate.what_is_required)

    def test_bundle_bearing_phase_reports_a_withdrawn_bundle_ahead_of_the_ordinary_row(self) -> None:
        """Assertions unchanged by CP4; the text at this ``"2.2"`` phase is
        now the marker-clearing clause plus the recovery steps
        (``ImplementationRejectedMarkerGateTest``)."""
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=False, detail="finalize failed")
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertIn("finalize failed", result.gate.what_is_required)
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/REJECTED")))

    def test_an_incoherent_bundle_gates_ahead_of_an_admissible_verdict(self) -> None:
        self._feedback("APPROVE")
        result = self._decide(implementation_revision=2)
        self._assert_gate_never_launches(result)
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/current/MANIFEST.md")))
        self.assertIn("implementation_revision", result.gate.what_is_required)
        _assert_regeneration_steps(self, result, "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")


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

    def test_unconsumed_findings_are_declined(self) -> None:
        """automatic-lifecycle-orchestration CP3: the handler still
        *selects* ``/apply-functional-review``, but no ExpectedOutcome row
        declares it, so the general dispatch rule declines it (it used to be
        automatic, and ``execute_step`` then crashed on the missing row)."""
        self._commit_checklist_evidence("wi-1", 1)
        fixtures.write_review_feedback(  # reuse the writer; FUNCTIONAL_REVIEW.md has no fields
            self.root, ".ai-review/feedback", "some findings\n",
        )
        (self.root / ".ai-review" / "feedback" / "FUNCTIONAL_REVIEW.md").write_text("findings\n")
        for version in ("1", "2.1", "2.2"):
            with self.subTest(governing_workflow_version=version):
                result = self._decide(governing_workflow_version=version)
                self.assertFalse(result.automatic)
                self.assertTrue(result.declined)
                self.assertIsNone(result.gate)
                self.assertEqual(result.action.command, "/apply-functional-review wi-1")
                self.assertEqual(result.evidence, ("unconsumed FUNCTIONAL_REVIEW.md findings",))
                self.assertEqual(
                    result.reason,
                    decision.uniform_decline_reason(
                        "AWAITING_FUNCTIONAL_REVIEW", version, "/apply-functional-review wi-1",
                    ),
                )

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


# ---------------------------------------------------------------------------
# Automatic-lifecycle-orchestration CP1: implementation-stage evidence
# readers. None of these is wired into a decision yet.
# ---------------------------------------------------------------------------

_WI = "wi-1"
_STATE_REL = Path("docs/ai-workflow/WORKFLOW_STATE.json")


def _write_state(root: Path, work_items: dict) -> None:
    fixtures.write_workflow_state(root, {"schema_version": 1, "active_work_item_id": None,
                                         "work_items": work_items})


def _commit(root: Path, subject: str, *, trailers: tuple[str, ...] = ()) -> str:
    fixtures.run(["git", "add", "-A"], cwd=root)
    args = ["git", "commit", "-q", "--allow-empty", "-m", subject]
    if trailers:
        args += ["-m", "\n".join(trailers)]
    fixtures.run(args, cwd=root)
    return fixtures.current_head(root)


def _record_trailers(work_item_id: str = _WI, revision: int = 2, *, supersedes: str | None = None,
                     extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    lines = (f"Workflow-Bundle-Generation-Record: {work_item_id}/{revision}",
             f"Workflow-Work-Item: {work_item_id}")
    if supersedes is not None:
        lines += (f"Workflow-Supersedes: {supersedes}",)
    return lines + extra


class ImplementationStageManifestLabelsTest(unittest.TestCase):
    """The four implementation-stage labels read from a manifest in the
    shape ``workflow_fingerprint.render_manifest_md_implementation_stage``
    writes -- a literal copy of that shape, never built through
    ``fixtures`` (which would confirm the reader against itself)."""

    _REAL_SHAPE = (
        "# Bundle Manifest\n"
        "\n"
        "stage: implementation\n"
        "bundle_id: " + "b" * 64 + "\n"
        "review_content_id: " + "c" * 64 + "\n"
        "work_item_id: wi-1\n"
        "work_item_type: product\n"
        "base_commit: " + "0" * 40 + "\n"
        "reviewed_implementation_head: " + "1" * 40 + "\n"
        "implementation_revision: 3\n"
        "worktree_root: /srv/target\n"
        "generation_head: " + "2" * 40 + "\n"
        "\n"
        "## Protected paths\n"
        "- `controller/job.py` — protected\n"
    )

    def test_implementation_stage_lines_are_read(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.write_manifest(root, "b", self._REAL_SHAPE)
            self.assertEqual(evidence.read_manifest_fields(root, Path("b")), {
                "stage": "implementation", "bundle_id": "b" * 64, "review_content_id": "c" * 64,
                "work_item_id": "wi-1", "plan_revision": None,
                "reviewed_implementation_head": "1" * 40, "implementation_revision": "3",
                "worktree_root": "/srv/target", "generation_head": "2" * 40, "_exists": True,
            })

    def test_fixture_builder_is_byte_identical_without_the_new_labels(self) -> None:
        self.assertEqual(
            fixtures.build_manifest_text(bundle_id="c" * 64, generation_head="1" * 40, stage="plan",
                                         work_item_id="wi-1", plan_revision=2),
            "# Bundle manifest\n\nstage: plan\nbundle_id: " + "c" * 64 + "\nwork_item_id: wi-1\n"
            "plan_revision: 2\ngeneration_head: " + "1" * 40 + "\n\n## Protected paths\n\n",
        )


class ImplementationBundleCoherenceTest(unittest.TestCase):
    """One failing case per clause, each against an otherwise coherent
    scoped implementation bundle, plus the positive case."""

    BUNDLE = Path(".ai-review/wi-1/current")

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        self.head = fixtures.current_head(self.root)

    def _write(self, **overrides) -> None:
        fields = dict(reviewed_implementation_head="1" * 40)
        fields.update(overrides)
        revision = fields.pop("implementation_revision", 3)
        fixtures.write_implementation_manifest(self.root, _WI, revision, **fields)

    def _work_item(self, **overrides):
        fields = dict(phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW", governing_workflow_version="2.2",
                      implementation_revision=3, reviewed_implementation_head="1" * 40)
        fields.update(overrides)
        return fixtures.build_work_item_view(**fields)

    def _check(self, *, require: bool = True, head: str | None = "live", **overrides):
        current_head = self.head if head == "live" else head
        return evidence.implementation_bundle_coherence(
            self.root, self._work_item(**overrides), current_head,
            require_current_generation_head=require,
        )

    def _assert_incoherent(self, clause: str, *fragments: str, **kwargs) -> None:
        coherent, observed_clause, detail = self._check(**kwargs)
        self.assertFalse(coherent)
        self.assertEqual(observed_clause, clause, detail)
        self.assertIn(clause, evidence.IMPLEMENTATION_BUNDLE_CLAUSES)
        for fragment in fragments:
            self.assertIn(fragment, detail)

    def test_coherent_bundle(self) -> None:
        self._write()
        coherent, clause, detail = self._check()
        self.assertTrue(coherent, detail)
        self.assertIsNone(clause)
        self.assertIn("implementation_revision 3", detail)
        self.assertIn(self.head, detail)

    def test_absent_current_directory(self) -> None:
        (self.root / ".ai-review" / _WI).mkdir(parents=True)
        self._assert_incoherent("absent", str(self.BUNDLE), "absent")

    def test_current_directory_without_a_manifest(self) -> None:
        (self.root / self.BUNDLE).mkdir(parents=True)
        self._assert_incoherent("absent", "MANIFEST.md", "missing or unreadable")

    def test_flat_layout_bundle_is_not_read_once_the_work_item_is_on_the_scoped_layout(self) -> None:
        """Scoped-else-flat is decided by the work item's own root
        directory: once ``.ai-review/wi-1/`` exists (every ``"2.2"`` item
        has it from its plan stage), an otherwise coherent flat bundle is
        not this work item's bundle."""
        (self.root / ".ai-review" / _WI).mkdir(parents=True)
        fixtures.write_implementation_manifest(
            self.root, _WI, 3, scoped=False, reviewed_implementation_head="1" * 40,
        )
        self._assert_incoherent("absent", str(self.BUNDLE))

    def test_flat_layout_bundle_is_read_when_no_scoped_root_exists(self) -> None:
        fixtures.write_implementation_manifest(
            self.root, _WI, 3, scoped=False, reviewed_implementation_head="1" * 40,
        )
        coherent, clause, detail = self._check()
        self.assertTrue(coherent, detail)
        self.assertIn(".ai-review/current/MANIFEST.md", detail)

    def test_plan_stage_manifest(self) -> None:
        self._write(stage="plan")
        self._assert_incoherent("stage", "manifest stage 'plan'")

    def test_wrong_work_item(self) -> None:
        fixtures.write_manifest(self.root, self.BUNDLE, fixtures.build_implementation_manifest_text(
            "wi-2", 3, worktree_root=fixtures.target_worktree_root(self.root),
            generation_head=self.head,
        ))
        self._assert_incoherent("work_item_id", "manifest work_item_id 'wi-2'")

    def test_missing_bundle_id(self) -> None:
        self._write(bundle_id=None)
        self._assert_incoherent("bundle_id", "bundle_id is missing")

    def test_different_worktree_root(self) -> None:
        self._write(worktree_root="/elsewhere/target")
        self._assert_incoherent("worktree_root", "'/elsewhere/target'",
                                fixtures.target_worktree_root(self.root))

    def test_missing_worktree_root_line(self) -> None:
        self._write(worktree_root=None)
        self._assert_incoherent("worktree_root", "manifest worktree_root None")

    def test_stale_implementation_revision(self) -> None:
        self._write(implementation_revision=2)
        self._assert_incoherent(
            "implementation_revision", "manifest implementation_revision 2 != state implementation_revision 3",
        )

    def test_non_decimal_implementation_revision(self) -> None:
        for bad in ("abc", "+3", "3.0", "0x3"):
            with self.subTest(value=bad):
                self._write(implementation_revision=bad)
                self._assert_incoherent("implementation_revision", repr(bad), "not an integer")

    def test_missing_implementation_revision_line(self) -> None:
        self._write(implementation_revision=None)
        self._assert_incoherent("implementation_revision", "manifest implementation_revision is missing")

    def test_wrong_reviewed_implementation_head(self) -> None:
        self._write(reviewed_implementation_head="9" * 40)
        self._assert_incoherent("reviewed_implementation_head", "9" * 40, "1" * 40)

    def test_generation_head_behind_head(self) -> None:
        self._write()
        (self.root / "later.txt").write_text("a commit after generation\n")
        new_head = fixtures.commit_all(self.root, "later")
        self.head = new_head
        self._assert_incoherent("generation_head", new_head)

    def test_generation_head_is_not_required_at_applying_review_feedback(self) -> None:
        """The flag ``APPLYING_REVIEW_FEEDBACK`` passes: ``HEAD`` legitimately
        runs ahead of ``generation_head`` there."""
        self._write(generation_head="9" * 40)
        coherent, clause, detail = self._check(require=False)
        self.assertTrue(coherent, detail)
        self.assertIsNone(clause)
        self.assertNotIn("generation_head", detail)

    def test_state_side_none_is_incoherent(self) -> None:
        self._write()
        cases = (
            ("implementation_revision", {"implementation_revision": None},
             "state implementation_revision is None"),
            ("implementation_revision", {"implementation_revision": True},
             "state implementation_revision is True"),
            ("reviewed_implementation_head", {"reviewed_implementation_head": None},
             "state reviewed_implementation_head is None"),
            ("generation_head", {"head": None}, "HEAD could not be read"),
        )
        for clause, overrides, fragment in cases:
            with self.subTest(clause=clause, overrides=overrides):
                self._assert_incoherent(clause, fragment, **overrides)

    def test_unreadable_worktree_root_is_incoherent(self) -> None:
        self._write()
        from unittest import mock

        with mock.patch.object(evidence, "target_worktree_root", return_value=None):
            self._assert_incoherent("worktree_root", "could not be read")

    def test_clause_codes_are_the_documented_set(self) -> None:
        self.assertEqual(evidence.IMPLEMENTATION_BUNDLE_CLAUSES, (
            "absent", "stage", "work_item_id", "bundle_id", "worktree_root",
            "implementation_revision", "reviewed_implementation_head", "generation_head",
        ))


class ReadReviewRequestFieldsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "b").mkdir()

    def _read(self, text: str | None):
        if text is not None:
            (self.root / "b" / "REVIEW_REQUEST.md").write_text(text)
        return evidence.read_review_request_fields(self.root, Path("b"))

    def test_absent_file(self) -> None:
        self.assertEqual(self._read(None), {"_exists": False, "review_content_ids": ()})

    def test_statement_anywhere_in_the_file_is_read(self) -> None:
        text = "# Review request\n\n## Scope\n\nreview_content_id: " + "c" * 64 + "\n"
        self.assertEqual(self._read(text)["review_content_ids"], ("c" * 64,))

    def test_repeated_equal_statements_collapse_and_disagreeing_ones_do_not(self) -> None:
        same = "review_content_id: " + "c" * 64 + "\nreview_content_id: " + "c" * 64 + "\n"
        self.assertEqual(self._read(same)["review_content_ids"], ("c" * 64,))
        differ = "review_content_id: " + "c" * 64 + "\nreview_content_id: " + "d" * 64 + "\n"
        self.assertEqual(self._read(differ)["review_content_ids"], ("c" * 64, "d" * 64))

    def test_only_the_generator_pattern_counts(self) -> None:
        for text in ("  review_content_id: " + "c" * 64 + "\n",        # indented
                     "review_content_id: " + "C" * 64 + "\n",          # upper-case hex
                     "review_content_id: " + "c" * 63 + "\n",          # short
                     "review_content_id: `" + "c" * 64 + "`\n"):       # decorated
            with self.subTest(text=text):
                result = self._read(text)
                self.assertTrue(result["_exists"])
                self.assertEqual(result["review_content_ids"], ())


class ReadImplementationReviewLedgerTest(unittest.TestCase):
    def _read(self, stages):
        return evidence.read_implementation_review_ledger(
            fixtures.build_work_item_view(implementation_review_stages=stages),
        )

    @staticmethod
    def _stage(**overrides):
        stage = {"bundle_id": "b" * 64, "verdict": "APPROVE", "round": 1,
                 "completed_at": "2026-09-23T00:00:00Z"}
        stage.update(overrides)
        return stage

    def test_absent_ledger_is_empty_not_malformed(self) -> None:
        self.assertEqual(self._read(None), evidence.LedgerView(None, None, None, None))

    def test_local_approve_only(self) -> None:
        view = self._read({"review_content_id": "c" * 64,
                           "LOCAL_MODEL_IMPLEMENTATION_REVIEW": self._stage(),
                           "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None})
        self.assertEqual(view, evidence.LedgerView(
            "c" * 64, {"verdict": "APPROVE", "bundle_id": "b" * 64, "round": 1}, None, None,
        ))

    def test_both_stages(self) -> None:
        view = self._read({"review_content_id": "c" * 64,
                           "LOCAL_MODEL_IMPLEMENTATION_REVIEW": self._stage(),
                           "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": self._stage(bundle_id="a" * 64, round=2),
                           "SOME_FUTURE_KEY": {"ignored": True}})
        self.assertEqual(view.manual, {"verdict": "APPROVE", "bundle_id": "a" * 64, "round": 2})
        self.assertIsNone(view.malformed)

    def test_malformed_ledger_reads_as_no_stage_recorded_per_shape(self) -> None:
        good_local = self._stage()
        cases = {
            "ledger is a list": [good_local],
            "ledger is a string": "APPROVE",
            "no review_content_id": {"LOCAL_MODEL_IMPLEMENTATION_REVIEW": good_local},
            "non-string review_content_id": {"review_content_id": 7,
                                             "LOCAL_MODEL_IMPLEMENTATION_REVIEW": good_local},
            "empty review_content_id": {"review_content_id": "",
                                        "LOCAL_MODEL_IMPLEMENTATION_REVIEW": good_local},
            "stage is not an object": {"review_content_id": "c" * 64,
                                       "LOCAL_MODEL_IMPLEMENTATION_REVIEW": "APPROVE"},
            "stage verdict is not APPROVE": {"review_content_id": "c" * 64,
                                             "LOCAL_MODEL_IMPLEMENTATION_REVIEW": self._stage(verdict="REVISE")},
            "stage has no bundle_id": {"review_content_id": "c" * 64,
                                       "LOCAL_MODEL_IMPLEMENTATION_REVIEW": self._stage(bundle_id=None)},
            "stage round is a bool": {"review_content_id": "c" * 64,
                                      "LOCAL_MODEL_IMPLEMENTATION_REVIEW": self._stage(round=True)},
            "stage round is a string": {"review_content_id": "c" * 64,
                                        "LOCAL_MODEL_IMPLEMENTATION_REVIEW": self._stage(round="1")},
            "manual stage malformed": {"review_content_id": "c" * 64,
                                       "LOCAL_MODEL_IMPLEMENTATION_REVIEW": good_local,
                                       "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": ["APPROVE"]},
            "manual without local": {"review_content_id": "c" * 64,
                                     "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": good_local},
        }
        for name, stages in cases.items():
            with self.subTest(shape=name):
                view = self._read(stages)
                self.assertIsNone(view.review_content_id)
                self.assertIsNone(view.local)
                self.assertIsNone(view.manual)
                self.assertTrue(view.malformed)


class CommittedStateReadTest(unittest.TestCase):
    """``committed_work_item``/``committed_checkpoint_statuses`` against a
    real repository whose committed values differ from the working tree's."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))

    def _entry(self, phase: str, checkpoints: dict) -> dict:
        return {"work_item_id": _WI, "phase": phase, "checkpoints": checkpoints}

    def test_committed_values_win_over_uncommitted_working_tree_values(self) -> None:
        _write_state(self.root, {_WI: self._entry(
            "IMPLEMENTING", {"CP1": {"status": "COMPLETE"}, "CP2": {"status": "IN_PROGRESS"}},
        )})
        _commit(self.root, "checkpoint CP1")
        # Uncommitted: CP2 completes and the phase moves, in the working tree only.
        _write_state(self.root, {_WI: self._entry(
            "SELF_REVIEWING_IMPLEMENTATION", {"CP1": {"status": "COMPLETE"}, "CP2": {"status": "COMPLETE"}},
        )})
        self.assertEqual(evidence.committed_work_item(self.root, _WI)["phase"], "IMPLEMENTING")
        self.assertEqual(evidence.committed_checkpoint_statuses(self.root), {
            (_WI, "CP1"): "COMPLETE", (_WI, "CP2"): "IN_PROGRESS",
        })
        _commit(self.root, "checkpoint CP2")
        self.assertEqual(evidence.committed_work_item(self.root, _WI)["phase"],
                         "SELF_REVIEWING_IMPLEMENTATION")
        self.assertEqual(evidence.committed_checkpoint_statuses(self.root)[(_WI, "CP2")], "COMPLETE")
        self.assertEqual(evidence.committed_work_item(self.root, _WI, rev="HEAD^")["phase"],
                         "IMPLEMENTING")
        self.assertEqual(evidence.committed_checkpoint_statuses(self.root, rev="HEAD^")[(_WI, "CP2")],
                         "IN_PROGRESS")

    def test_statuses_are_keyed_by_work_item_and_checkpoint(self) -> None:
        _write_state(self.root, {
            _WI: self._entry("IMPLEMENTING", {"CP1": {"status": "COMPLETE"}}),
            "wi-2": {"work_item_id": "wi-2", "phase": "IMPLEMENTING",
                     "checkpoints": {"CP1": {"status": "IN_PROGRESS"}}},
        })
        _commit(self.root, "two items")
        self.assertEqual(evidence.committed_checkpoint_statuses(self.root), {
            (_WI, "CP1"): "COMPLETE", ("wi-2", "CP1"): "IN_PROGRESS",
        })

    def test_malformed_entries_are_skipped_never_read_as_complete(self) -> None:
        _write_state(self.root, {
            _WI: {"work_item_id": _WI, "phase": "IMPLEMENTING",
                  "checkpoints": {"CP1": "COMPLETE", "CP2": {"status": 1}, "CP3": {"status": "COMPLETE"}}},
            "wi-2": {"work_item_id": "wi-2", "checkpoints": ["CP1"]},
            "wi-3": "not an object",
        })
        _commit(self.root, "malformed entries")
        self.assertEqual(evidence.committed_checkpoint_statuses(self.root), {(_WI, "CP3"): "COMPLETE"})
        self.assertIsNone(evidence.committed_work_item(self.root, "wi-3"))

    def test_unreadable_committed_state_is_none(self) -> None:
        # Never committed at HEAD: present only in the working tree.
        _write_state(self.root, {_WI: self._entry("IMPLEMENTING", {})})
        self.assertIsNone(evidence.committed_work_item(self.root, _WI))
        self.assertIsNone(evidence.committed_checkpoint_statuses(self.root))
        # Committed but not JSON / not the expected shape.
        for text in ("{not json", "[1, 2]", '{"work_items": []}'):
            with self.subTest(text=text):
                fixtures.write_workflow_state_raw(self.root, text)
                _commit(self.root, "unreadable state")
                self.assertIsNone(evidence.committed_work_item(self.root, _WI))
                self.assertIsNone(evidence.committed_checkpoint_statuses(self.root))

    def test_absent_work_item_and_unknown_revision_are_none(self) -> None:
        _write_state(self.root, {_WI: self._entry("IMPLEMENTING", {})})
        _commit(self.root, "state")
        self.assertIsNone(evidence.committed_work_item(self.root, "wi-9"))
        self.assertIsNone(evidence.committed_work_item(self.root, _WI, rev="no-such-rev"))
        self.assertIsNone(evidence.committed_checkpoint_statuses(self.root, rev="no-such-rev"))

    def test_repository_without_commits_is_none(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        fixtures.build_target_git_repo(empty)
        self.assertIsNone(evidence.committed_work_item(empty, _WI))
        self.assertIsNone(evidence.committed_checkpoint_statuses(empty))


class BundleGenerationRecordRoleTest(unittest.TestCase):
    ORDINARY = {"Workflow-Bundle-Generation-Record": "wi-1/2", "Workflow-Work-Item": "wi-1"}

    def test_ordinary_and_recovered_roles(self) -> None:
        self.assertEqual(evidence.bundle_generation_record_role(self.ORDINARY, _WI), "ordinary")
        recovered = dict(self.ORDINARY, **{"Workflow-Supersedes": "a" * 40})
        self.assertEqual(evidence.bundle_generation_record_role(recovered, _WI), "recovered")

    def test_any_other_trailer_set_has_no_role(self) -> None:
        for trailers in (
            {},
            None,
            {"Workflow-Work-Item": "wi-1"},
            {"Workflow-Bundle-Generation-Record": "wi-1/2"},
            dict(self.ORDINARY, **{"Co-Authored-By": "someone"}),
            dict(self.ORDINARY, **{"Workflow-Checkpoint": "CP1"}),
        ):
            with self.subTest(trailers=trailers):
                self.assertIsNone(evidence.bundle_generation_record_role(trailers, _WI))

    def test_a_record_naming_another_work_item_has_no_role_here(self) -> None:
        for trailers in (
            {"Workflow-Bundle-Generation-Record": "wi-2/2", "Workflow-Work-Item": "wi-2"},
            {"Workflow-Bundle-Generation-Record": "wi-2/2", "Workflow-Work-Item": "wi-1"},
            {"Workflow-Bundle-Generation-Record": "wi-1/2", "Workflow-Work-Item": "wi-2"},
            {"Workflow-Bundle-Generation-Record": "wi-1", "Workflow-Work-Item": "wi-1"},
            {"Workflow-Bundle-Generation-Record": "wi-10/2", "Workflow-Work-Item": "wi-1"},
        ):
            with self.subTest(trailers=trailers):
                self.assertIsNone(evidence.bundle_generation_record_role(trailers, _WI))


class GenerationRecordViewTest(unittest.TestCase):
    """``generation_record_view`` against a real repository whose commits
    carry this work item's committed phase and real trailers."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))

    def _state_commit(self, phase: str, subject: str, *, trailers: tuple[str, ...] = (),
                      work_item_id: str = _WI) -> str:
        _write_state(self.root, {work_item_id: {"work_item_id": work_item_id, "phase": phase}})
        return _commit(self.root, subject, trailers=trailers)

    def _view(self, manifest_generation_head: str | None):
        return evidence.generation_record_view(self.root, _WI, manifest_generation_head)

    def test_ordinary_record_whose_parent_records_applying_review_feedback(self) -> None:
        previous = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T0",
                                      trailers=_record_trailers(revision=1))
        self._state_commit("APPLYING_REVIEW_FEEDBACK", "pending review-stage write",
                           trailers=("Workflow-Work-Item: wi-1",))
        record = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T",
                                    trailers=_record_trailers(revision=2))
        view = self._view(previous)
        self.assertEqual(view.head, record)
        self.assertEqual(view.head_role, "ordinary")
        self.assertEqual(view.head_phase, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(view.parent_phase, "APPLYING_REVIEW_FEEDBACK")
        self.assertEqual(view.newer_records, (record,))
        self.assertEqual(view.latest_record_parent_phase, "APPLYING_REVIEW_FEEDBACK")

    def test_malformed_ordinary_record_parent_records_the_same_phase(self) -> None:
        previous = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T0",
                                      trailers=_record_trailers(revision=1))
        # The literal worker: no separate pending-write commit before T.
        self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T",
                           trailers=_record_trailers(revision=2))
        view = self._view(previous)
        self.assertEqual(view.head_role, "ordinary")
        self.assertEqual(view.head_phase, view.parent_phase)
        self.assertEqual(view.head_phase, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

    def test_recovered_role_record(self) -> None:
        previous = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T0",
                                      trailers=_record_trailers(revision=2))
        self._state_commit("APPLYING_REVIEW_FEEDBACK", "pending write",
                           trailers=("Workflow-Work-Item: wi-1",))
        record = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "same_content T",
                                    trailers=_record_trailers(revision=2, supersedes=previous))
        view = self._view(previous)
        self.assertEqual(view.head_role, "recovered")
        self.assertEqual(view.newer_records, (record,))

    def test_excluded_only_commit_after_the_record(self) -> None:
        previous = self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "checkpoint")
        record = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T",
                                    trailers=_record_trailers(revision=1))
        (self.root / "docs" / "notes.md").write_text("an excluded-only edit\n")
        excluded = _commit(self.root, "excluded-only")
        view = self._view(previous)
        self.assertEqual(view.head, excluded)
        self.assertIsNone(view.head_role)
        self.assertEqual(view.head_phase, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(view.parent_phase, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(view.newer_records, (record,))
        self.assertEqual(view.latest_record_parent_phase, "SELF_REVIEWING_IMPLEMENTATION")

    def test_newer_records_are_newest_first_and_exclude_the_generation_head_itself(self) -> None:
        first = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T1",
                                   trailers=_record_trailers(revision=1))
        self._state_commit("APPLYING_REVIEW_FEEDBACK", "pending write",
                           trailers=("Workflow-Work-Item: wi-1",))
        second = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T2",
                                    trailers=_record_trailers(revision=2))
        self._state_commit("APPLYING_REVIEW_FEEDBACK", "pending write 2",
                           trailers=("Workflow-Work-Item: wi-1",))
        third = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T3",
                                   trailers=_record_trailers(revision=3))
        self.assertEqual(self._view(first).newer_records, (third, second))
        self.assertEqual(self._view(third).newer_records, ())
        self.assertIsNone(self._view(third).latest_record_parent_phase)

    def test_non_ancestor_or_absent_generation_head_has_no_newer_records(self) -> None:
        base = self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "checkpoint")
        fixtures.run(["git", "checkout", "-q", "-b", "side"], cwd=self.root)
        side = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "side T",
                                  trailers=_record_trailers(revision=1))
        fixtures.run(["git", "checkout", "-q", "-"], cwd=self.root)
        self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T",
                           trailers=_record_trailers(revision=1))
        for generation_head in (side, "9" * 40, None, "", "HEAD~1", "--output=x", base[:12]):
            with self.subTest(generation_head=generation_head):
                view = self._view(generation_head)
                self.assertIsNone(view.newer_records)
                self.assertIsNone(view.latest_record_parent_phase)
                self.assertEqual(view.head_role, "ordinary")
        self.assertIsNotNone(self._view(base).newer_records)

    def test_a_record_naming_another_work_item_is_ignored(self) -> None:
        previous = self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "checkpoint")
        _write_state(self.root, {
            _WI: {"work_item_id": _WI, "phase": "SELF_REVIEWING_IMPLEMENTATION"},
            "wi-2": {"work_item_id": "wi-2", "phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"},
        })
        _commit(self.root, "other item's T", trailers=_record_trailers("wi-2", 1))
        view = self._view(previous)
        self.assertIsNone(view.head_role)
        self.assertEqual(view.newer_records, ())

    def test_a_record_with_an_extra_trailer_has_no_role_but_is_still_a_record(self) -> None:
        """Workflow's own discovery pairs only ``Workflow-Work-Item`` with a
        generation-record value, whatever else the trailer set holds; the
        role is a separate, stricter classification."""
        previous = self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "checkpoint")
        record = self._state_commit("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "T",
                                    trailers=_record_trailers(revision=1, extra=("Co-Authored-By: x",)))
        view = self._view(previous)
        self.assertIsNone(view.head_role)
        self.assertEqual(view.newer_records, (record,))

    def test_first_commit_has_no_parent_phase(self) -> None:
        empty = Path(self._tmp.name) / "fresh"
        fixtures.build_target_git_repo(empty)
        _write_state(empty, {_WI: {"work_item_id": _WI, "phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}})
        _commit(empty, "T", trailers=_record_trailers(revision=1))
        view = evidence.generation_record_view(empty, _WI, None)
        self.assertEqual(view.head_role, "ordinary")
        self.assertEqual(view.head_phase, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertIsNone(view.parent_phase)

    def test_repository_without_commits(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        fixtures.build_target_git_repo(empty)
        self.assertEqual(evidence.generation_record_view(empty, _WI, None),
                         evidence.GenerationRecordView(None, None, None, None, None, None))


class ManualImplementationStageAdmissibilityTest(unittest.TestCase):
    """``evaluate_manual_implementation_stage_admissibility``: each clause
    failing independently against an otherwise admissible verdict."""

    HEAD = "2" * 40
    ROOT = "/srv/target"

    def _inputs(self, *, status: str = "APPROVE"):
        feedback = {
            "status": status, "reviewer_role": "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            "reviewed_bundle_id": "b" * 64, "reviewed_base_commit": "0" * 40,
            "work_item": _WI, "reviewed_content_id": "c" * 64,
        }
        manifest = {
            "bundle_id": "b" * 64, "review_content_id": "c" * 64, "generation_head": self.HEAD,
            "worktree_root": self.ROOT, "stage": "implementation", "work_item_id": _WI,
            "_exists": True,
        }
        review_request = {"_exists": True, "review_content_ids": ("c" * 64,)}
        ledger = {
            "review_content_id": "c" * 64,
            "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {"bundle_id": "b" * 64, "verdict": "APPROVE",
                                                  "round": 1, "completed_at": "t"},
            "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None,
        }
        return feedback, manifest, review_request, ledger

    def _evaluate(self, feedback, manifest, review_request, ledger, *, head: str | None = HEAD,
                  root: str | None = ROOT):
        work_item = fixtures.build_work_item_view(
            phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", governing_workflow_version="2.2",
            base_commit="0" * 40, implementation_review_stages=ledger,
        )
        return evidence.evaluate_manual_implementation_stage_admissibility(
            feedback=feedback, manifest=manifest, review_request=review_request,
            work_item=work_item, current_head=head, current_worktree_root=root,
        )

    def test_admissible_for_each_status(self) -> None:
        for status in ("APPROVE", "REVISE", "BLOCK"):
            with self.subTest(status=status):
                result = self._evaluate(*self._inputs(status=status))
                self.assertTrue(result.admissible, result.failure_summary())
                self.assertEqual(result.advisories, ())

    def test_each_clause_fails_independently(self) -> None:
        def feedback(**changes):
            def mutate(f, m, r, l):
                f.update(changes)
            return mutate

        def manifest(**changes):
            def mutate(f, m, r, l):
                m.update(changes)
            return mutate

        def ledger_local(value):
            def mutate(f, m, r, l):
                l["LOCAL_MODEL_IMPLEMENTATION_REVIEW"] = value
            return mutate

        def ledger_manual(value):
            def mutate(f, m, r, l):
                l["MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"] = value
            return mutate

        def request(values, exists=True):
            def mutate(f, m, r, l):
                r.update({"_exists": exists, "review_content_ids": values})
            return mutate

        cases = (
            ("local role", feedback(reviewer_role="LOCAL_MODEL_IMPLEMENTATION_REVIEW"), "Reviewer role"),
            ("lower-case role", feedback(reviewer_role="manual_external_implementation_review"),
             "Reviewer role"),
            ("no role", feedback(reviewer_role=None), "Reviewer role"),
            ("unparseable status", feedback(status="MAYBE"), "Status"),
            ("no bundle id line", feedback(reviewed_bundle_id=None), "Reviewed bundle ID"),
            ("no base commit line", feedback(reviewed_base_commit=None), "Reviewed base commit"),
            ("no work item line", feedback(work_item=None), "Work item"),
            ("other work item", feedback(work_item="wi-2"), "Work item"),
            ("other base commit", feedback(reviewed_base_commit="9" * 40), "Reviewed base commit"),
            ("feedback content id stale", feedback(reviewed_content_id="d" * 64), "review_content_id"),
            ("feedback content id absent", feedback(reviewed_content_id=None), "review_content_id"),
            ("manifest content id differs", manifest(review_content_id="d" * 64), None),
            ("no local approval", ledger_local(None), "local approval"),
            ("manual already recorded", ledger_manual(
                {"bundle_id": "b" * 64, "verdict": "APPROVE", "round": 1, "completed_at": "t"}),
             "manual stage"),
            ("stale generation_head", manifest(generation_head="9" * 40), "generation_head"),
            ("no generation_head", manifest(generation_head=None), "generation_head"),
            ("other worktree_root", manifest(worktree_root="/elsewhere"), "worktree_root"),
            ("no worktree_root", manifest(worktree_root=None), "worktree_root"),
            ("REVIEW_REQUEST.md states another id", request(("d" * 64,)),
             "REVIEW_REQUEST.md review_content_id"),
            ("REVIEW_REQUEST.md states two ids", request(("c" * 64, "d" * 64)),
             "REVIEW_REQUEST.md review_content_id"),
            ("REVIEW_REQUEST.md states none", request(()), "REVIEW_REQUEST.md review_content_id"),
            ("REVIEW_REQUEST.md missing", request((), exists=False), "REVIEW_REQUEST.md review_content_id"),
        )
        for name, mutate, clause in cases:
            with self.subTest(case=name):
                inputs = self._inputs()
                mutate(*inputs)
                result = self._evaluate(*inputs)
                self.assertFalse(result.admissible)
                failing = {failure.clause for failure in result.failures}
                if clause is None:
                    # A manifest whose content id moved disagrees with both
                    # the ledger and REVIEW_REQUEST.md, so two clauses fail.
                    self.assertEqual(failing, {"ledger review_content_id",
                                               "REVIEW_REQUEST.md review_content_id"})
                else:
                    self.assertEqual(failing, {clause}, result.failure_summary())

    def test_live_head_and_worktree_root_are_required(self) -> None:
        for kwargs, clause in (({"head": None}, "generation_head"), ({"root": None}, "worktree_root")):
            with self.subTest(clause=clause):
                result = self._evaluate(*self._inputs(), **kwargs)
                self.assertEqual({f.clause for f in result.failures}, {clause})

    def test_malformed_ledger_fails_closed(self) -> None:
        feedback, manifest, review_request, ledger = self._inputs()
        ledger["LOCAL_MODEL_IMPLEMENTATION_REVIEW"]["verdict"] = "REVISE"
        result = self._evaluate(feedback, manifest, review_request, ledger)
        self.assertFalse(result.admissible)
        self.assertIn("local approval", {f.clause for f in result.failures})
        self.assertIn("ledger malformed", result.failure_summary())

    def test_approve_bundle_id_mismatch_is_advisory(self) -> None:
        feedback, manifest, review_request, ledger = self._inputs(status="APPROVE")
        feedback["reviewed_bundle_id"] = "a" * 64
        result = self._evaluate(feedback, manifest, review_request, ledger)
        self.assertTrue(result.admissible, result.failure_summary())
        self.assertEqual(len(result.advisories), 1)
        self.assertIn("advisory only", result.advisories[0])

    def test_block_bundle_id_mismatch_is_advisory(self) -> None:
        feedback, manifest, review_request, ledger = self._inputs(status="BLOCK")
        feedback["reviewed_bundle_id"] = "a" * 64
        result = self._evaluate(feedback, manifest, review_request, ledger)
        self.assertTrue(result.admissible, result.failure_summary())
        self.assertEqual(len(result.advisories), 1)

    def test_revise_bundle_id_mismatch_is_hard(self) -> None:
        feedback, manifest, review_request, ledger = self._inputs(status="REVISE")
        feedback["reviewed_bundle_id"] = "a" * 64
        result = self._evaluate(feedback, manifest, review_request, ledger)
        self.assertFalse(result.admissible)
        self.assertEqual({f.clause for f in result.failures}, {"Reviewed bundle ID"})
        self.assertIn("/apply-implementation-review step 1", result.failure_summary())
        self.assertEqual(result.advisories, ())

    def test_revise_against_a_manifest_without_bundle_id_is_hard(self) -> None:
        feedback, manifest, review_request, ledger = self._inputs(status="REVISE")
        manifest["bundle_id"] = None
        result = self._evaluate(feedback, manifest, review_request, ledger)
        self.assertEqual({f.clause for f in result.failures}, {"Reviewed bundle ID"})


class ApplyImplementationReviewAdmissibilityTest(unittest.TestCase):
    """``evaluate_apply_implementation_review_admissibility``: the command's
    own step-1 clauses plus the Controller's narrower role/``Status`` scope."""

    def _feedback(self, **overrides):
        feedback = {
            "status": "REVISE", "reviewer_role": "LOCAL_MODEL_IMPLEMENTATION_REVIEW",
            "reviewed_bundle_id": "b" * 64, "reviewed_base_commit": "0" * 40,
            "work_item": _WI, "reviewed_content_id": "c" * 64,
        }
        feedback.update(overrides)
        return feedback

    def _evaluate(self, feedback, *, manifest_bundle_id: str | None = "b" * 64,
                  generation_head: str = "2" * 40, current_head: str | None = "2" * 40):
        work_item = fixtures.build_work_item_view(
            phase="APPLYING_REVIEW_FEEDBACK", governing_workflow_version="2.2", base_commit="0" * 40,
        )
        manifest = {"bundle_id": manifest_bundle_id, "generation_head": generation_head,
                    "stage": "implementation", "work_item_id": _WI, "_exists": True}
        return evidence.evaluate_apply_implementation_review_admissibility(
            feedback=feedback, manifest=manifest, work_item=work_item, current_head=current_head,
        )

    def test_revise_from_either_two_stage_role_is_admissible(self) -> None:
        for role in ("LOCAL_MODEL_IMPLEMENTATION_REVIEW", "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"):
            with self.subTest(role=role):
                result = self._evaluate(self._feedback(reviewer_role=role))
                self.assertTrue(result.admissible, result.failure_summary())

    def test_there_is_no_generation_head_clause(self) -> None:
        """``HEAD`` is legitimately ahead of ``generation_head`` here."""
        result = self._evaluate(self._feedback(), generation_head="9" * 40, current_head="2" * 40)
        self.assertTrue(result.admissible, result.failure_summary())
        result = self._evaluate(self._feedback(), current_head=None)
        self.assertTrue(result.admissible, result.failure_summary())

    def test_each_clause_fails_independently(self) -> None:
        cases = (
            ("no bundle id line", self._feedback(reviewed_bundle_id=None), {}, "Reviewed bundle ID"),
            ("no base commit line", self._feedback(reviewed_base_commit=None), {}, "Reviewed base commit"),
            ("no work item line", self._feedback(work_item=None), {}, "Work item"),
            ("bundle mismatch", self._feedback(reviewed_bundle_id="a" * 64), {}, "Reviewed bundle ID"),
            ("manifest without bundle id", self._feedback(), {"manifest_bundle_id": None},
             "Reviewed bundle ID"),
            ("other work item", self._feedback(work_item="wi-2"), {}, "Work item"),
            ("other base commit", self._feedback(reviewed_base_commit="9" * 40), {}, "Reviewed base commit"),
            ("plan-stage role", self._feedback(reviewer_role="LOCAL_MODEL_PLAN_REVIEW"), {}, "Reviewer role"),
            ("lower-case role", self._feedback(reviewer_role="local_model_implementation_review"), {},
             "Reviewer role"),
            ("no role (a \"2.1\"-shaped verdict)", self._feedback(reviewer_role=None), {}, "Reviewer role"),
            ("unparseable status", self._feedback(status="MAYBE"), {}, "Status"),
            ("no status", self._feedback(status=None), {}, "Status"),
        )
        for name, feedback, kwargs, clause in cases:
            with self.subTest(case=name):
                result = self._evaluate(feedback, **kwargs)
                self.assertFalse(result.admissible)
                self.assertEqual({f.clause for f in result.failures}, {clause}, result.failure_summary())

    def test_approve_is_refused_as_human_territory_not_as_incoherent(self) -> None:
        result = self._evaluate(self._feedback(status="APPROVE",
                                               reviewer_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))
        self.assertFalse(result.admissible)
        self.assertEqual({f.clause for f in result.failures}, {"Status"})
        summary = result.failure_summary()
        self.assertIn("late fix", summary)
        self.assertIn("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", summary)
        self.assertIn("human territory", summary)
        self.assertNotIn("incoherent", summary)

    def test_block_is_refused_for_explicit_user_resolution(self) -> None:
        result = self._evaluate(self._feedback(status="BLOCK"))
        self.assertFalse(result.admissible)
        self.assertEqual({f.clause for f in result.failures}, {"Status"})
        summary = result.failure_summary()
        self.assertIn("changed after the transition", summary)
        self.assertIn("A7", summary)
        self.assertIn("explicit user resolution", summary)


# ---------------------------------------------------------------------------
# Automatic-lifecycle-orchestration CP4: the implementation-review decision
# handlers, the implementation-bundle gate and the REJECTED gate's branches.
# ---------------------------------------------------------------------------


def _assert_in_order(test: unittest.TestCase, text: str, needles: list[str]) -> None:
    position = -1
    for needle in needles:
        found = text.find(needle, position + 1)
        test.assertGreater(found, position, f"{needle!r} missing or out of order in {text!r}")
        position = found


def _assert_regeneration_steps(test: unittest.TestCase, result, phase: str) -> None:
    """The implementation-bundle gate's third case (CP4B): the ordered
    recovery steps in both fields, ending in the generator with its
    preflight clause -- never the bare ``_regeneration_command``, never
    ``/recover-implementation-provenance``, and never launching."""
    gate = result.gate
    test.assertIsNone(result.action)
    test.assertFalse(result.automatic)
    test.assertIn("before any implementation review runs, perform in order: ", gate.what_is_required)
    for text in (gate.what_is_required, gate.safe_resume_command):
        test.assertIn("IMPLEMENTATION_SUMMARY.md", text)
        test.assertIn("run scripts/prepare-ai-review.sh", text)
        test.assertIn("refuses at preflight", text)
        test.assertNotIn("/recover-implementation-provenance", text)
    test.assertNotEqual(gate.safe_resume_command, evidence._regeneration_command(phase, "wi-1"))
    test.assertNotEqual(gate.safe_resume_command, "workflow-controller explain --work-item wi-1")


def _assert_provenance_only(test: unittest.TestCase, result, *, head: str, paths: tuple[str, ...]) -> None:
    """The implementation-bundle gate's second case (CP4B, round 2's O3):
    the intervening commits listed as evidence, oldest first, with their
    paths, and ``/recover-implementation-provenance`` named only on the
    excluded-only condition -- in both fields, never automatically."""
    gate = result.gate
    test.assertIsNone(result.action)
    test.assertFalse(result.automatic)
    test.assertIn(f"is behind the target's committed HEAD ({head})", gate.what_is_required)
    test.assertIn(head, " ".join(result.evidence[1:]))
    for path in paths:
        test.assertIn(path, " ".join(result.evidence[1:]))
        test.assertIn(path, gate.what_is_required)
    _assert_in_order(test, gate.what_is_required, [
        "if every listed commit changes only paths the implementation stage excludes",
        'workflow_fingerprint.artifacts_path_for_work_item("wi-1")',
        "a human runs /recover-implementation-provenance",
        "Otherwise the listed content is not what was reviewed, and that command refuses",
        "revert those commits, or carry the change through a review round",
    ])
    test.assertTrue(gate.safe_resume_command.startswith("/recover-implementation-provenance wi-1 -- only if "))
    test.assertIn("changes only paths the implementation stage excludes", gate.safe_resume_command)
    test.assertIn("revert those commits, or carry the change through a review round", gate.safe_resume_command)
    test.assertNotIn("prepare-ai-review.sh", gate.safe_resume_command)


class ExternalImplementationReviewGoldenTest(unittest.TestCase):
    """``"1"``/``"2.1"`` ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` is
    byte-identical to its pre-CP4 decisions: every case of
    ``tests/golden/external_implementation_review_decisions.json``
    (generated from CP3's unchanged code) re-derives byte-equal, with no
    permitted difference at all."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.golden import generate_external_implementation_review_decisions as golden

        cls.golden = golden
        cls.checked_in = golden.GOLDEN_PATH.read_text()
        cls.derived_cases = golden.derive_cases()

    def test_every_case_reproduces_from_the_code_as_it_stands(self) -> None:
        expected = self.golden.load_cases(self.checked_in)
        self.assertEqual(sorted(expected), sorted(self.derived_cases))
        moved = [key for key in sorted(expected) if expected[key] != self.derived_cases[key]]
        self.assertEqual(moved, [], f"\"1\"/\"2.1\" decisions moved: {moved[:10]}")

    def test_normalised_rederivation_is_byte_equal_to_the_golden(self) -> None:
        self.assertEqual(self.golden.render_document(self.derived_cases), self.checked_in)

    def test_coverage_is_every_scenario_at_both_versions(self) -> None:
        import json

        document = json.loads(self.checked_in)
        expected_pairs = {("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "1"),
                          ("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "2.1")}
        self.assertEqual({tuple(pair) for pair in document["phase_versions"]}, expected_pairs)
        self.assertEqual(set(self.golden.PHASE_VERSIONS), expected_pairs)
        from tests.golden.generate_plan_stage_decisions import case_key

        self.assertEqual(set(document["cases"]), {
            case_key(scenario_id, phase, version)
            for scenario_id, _setup, _overrides in self.golden.SCENARIOS for phase, version in expected_pairs
        })

    def test_the_matrix_exercises_every_row_of_the_legacy_handler(self) -> None:
        bodies = list(self.golden.load_cases(self.checked_in).values())
        self.assertFalse([body for body in bodies if "raises" in body])
        resumes = {body["gate"]["safe_resume_command"] for body in bodies}
        self.assertEqual(resumes, {
            "/apply-implementation-review wi-1", "/approve-review implementation wi-1",
            "/recover-implementation-provenance wi-1", "scripts/prepare-ai-review.sh <base-sha> post-fix wi-1",
        })
        self.assertTrue(all(not body["automatic"] and not body["declined"] for body in bodies))


class AwaitingExternalImplementationReviewTwoStageTest(unittest.TestCase):
    """``"2.2"`` ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` (CP4): reached
    only through a recorded manual ``APPROVE``, so it names the user-only
    ``/approve-review implementation`` only when every clause of
    ``technical_approval_gate_reachable`` this Controller can see holds --
    the inverted revision-1 test (I3)."""

    PHASE = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        fixtures.write_implementation_bundle(self.root, "wi-1", 1, reviewed_implementation_head=_REVIEWED_HEAD)
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, **overrides):
        overrides.setdefault("implementation_review_stages", _COMPLETE_LEDGER)
        work_item = _implementation_work_item(self.PHASE, **overrides)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _feedback(self, status: str | None, role: str | None = _MANUAL_ROLE, **fields) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(status=status, reviewer_role=role, **fields),
        )

    def _assert_not_named(self, result, *fragments: str) -> None:
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertIsNotNone(result.gate)
        self.assertNotEqual(result.gate.safe_resume_command, "/approve-review implementation wi-1")
        self.assertEqual(result.gate.safe_resume_command, "workflow-controller explain --work-item wi-1")
        self.assertIn("/approve-review implementation would refuse", result.gate.what_is_required)
        for fragment in fragments:
            self.assertIn(fragment, result.gate.what_is_required)

    def test_complete_ledger_and_an_approve_on_file_names_approve_review(self) -> None:
        self._feedback("APPROVE")
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertEqual(result.gate.safe_resume_command, "/approve-review implementation wi-1")
        self.assertIn("both implementation-review stages approved the current content",
                      result.gate.what_is_required)
        self.assertIn("user-only", result.gate.what_is_required)

    def test_a_revise_status_also_reaches_the_approval_gate(self) -> None:
        """``approval_gate_reachable`` admits ``REVISE`` as well as
        ``APPROVE`` -- here a ``REVISE`` that is not a later manual verdict
        on the current content (no reviewer role)."""
        self._feedback("REVISE", role=None)
        result = self._decide()
        self.assertEqual(result.gate.safe_resume_command, "/approve-review implementation wi-1")

    def test_absent_feedback_does_not_name_approve_review(self) -> None:
        self._assert_not_named(self._decide(), "no REVIEW_FEEDBACK.md is on file")

    def test_a_block_status_does_not_name_approve_review(self) -> None:
        self._feedback("BLOCK", role=None)
        self._assert_not_named(self._decide(), "Status is 'BLOCK'", "approval_gate_reachable")

    def test_a_pin_on_the_manifest_bundle_does_not_name_approve_review(self) -> None:
        self._feedback("APPROVE")
        pins = ({"bundle_id": "b" * 64, "review_content_id": "c" * 64, "recorded_at": "t"},)
        self._assert_not_named(self._decide(technical_review_block_pins=pins),
                               "technical_review_block_pins", "b" * 64)

    def test_a_pin_on_another_bundle_does_not_block_the_gate(self) -> None:
        self._feedback("APPROVE")
        pins = ({"bundle_id": "a" * 64, "review_content_id": "c" * 64, "recorded_at": "t"},)
        result = self._decide(technical_review_block_pins=pins)
        self.assertEqual(result.gate.safe_resume_command, "/approve-review implementation wi-1")

    def test_a_half_empty_ledger_does_not_name_approve_review(self) -> None:
        self._feedback("APPROVE")
        self._assert_not_named(self._decide(implementation_review_stages=_LOCAL_APPROVE_LEDGER),
                               f"no {_MANUAL_ROLE} APPROVE")
        self._assert_not_named(self._decide(implementation_review_stages=None),
                               f"no {_LOCAL_ROLE} APPROVE", f"no {_MANUAL_ROLE} APPROVE")

    def test_a_ledger_for_other_content_does_not_name_approve_review(self) -> None:
        self._feedback("APPROVE")
        ledger = fixtures.implementation_review_ledger(
            "d" * 64, local_bundle_id="b" * 64, manual_bundle_id="b" * 64,
        )
        self._assert_not_named(self._decide(implementation_review_stages=ledger),
                               "is not the current content's")

    def test_a_malformed_ledger_does_not_name_approve_review(self) -> None:
        self._feedback("APPROVE")
        self._assert_not_named(self._decide(implementation_review_stages=["not", "an", "object"]),
                               "ledger is malformed")

    def test_a_later_manual_verdict_on_the_current_content_is_a_gate_never_automatic(self) -> None:
        for status in ("REVISE", "BLOCK"):
            with self.subTest(status=status):
                self._feedback(status)
                result = self._decide()
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                self.assertFalse(result.declined)
                self.assertIn("a later manual verdict is on file", result.gate.what_is_required)
                self.assertIn("re-enters APPLYING_REVIEW_FEEDBACK", result.gate.what_is_required)
                self.assertIn("a human decides whether to reopen remediation", result.gate.what_is_required)
                self.assertEqual(result.gate.safe_resume_command, "/apply-implementation-review wi-1")

    def test_a_mis_cased_later_manual_verdict_never_falls_through_to_naming_approval(self) -> None:
        self._feedback("REVISE", role="manual_external_implementation_review")
        result = self._decide()
        self.assertIn("a later manual verdict is on file", result.gate.what_is_required)
        self.assertNotEqual(result.gate.safe_resume_command, "/approve-review implementation wi-1")

    def test_a_manual_revise_on_other_content_is_not_the_later_verdict_gate(self) -> None:
        self._feedback("REVISE", reviewed_content_id="d" * 64)
        result = self._decide()
        self.assertNotIn("a later manual verdict", result.gate.what_is_required)

    def test_an_incoherent_bundle_gates_ahead_of_the_handler(self) -> None:
        self._feedback("APPROVE")
        result = self._decide(implementation_revision=2)
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/current/MANIFEST.md")))
        self.assertIn("implementation_revision", result.gate.what_is_required)
        _assert_regeneration_steps(self, result, self.PHASE)

    def test_a_generation_head_only_failure_names_the_conditional_provenance_recovery(self) -> None:
        """Was ``..._keeps_the_provenance_text`` (CP4's first form). CP4B's
        final form lists the intervening commits and names
        ``/recover-implementation-provenance`` only on the condition that
        they are excluded-only (round 2's O3)."""
        self._feedback("APPROVE")
        (self.root / "later.txt").write_text("a commit after generation\n")
        head = fixtures.commit_all(self.root, "later")
        result = self._decide()
        _assert_provenance_only(self, result, head=head, paths=("later.txt",))


class AwaitingLocalImplementationReviewTest(unittest.TestCase):
    """``"2.2"`` ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW`` (CP4): automatic
    ``/review-implementation`` behind the ``REJECTED``, implementation-bundle
    and local-``BLOCK`` gates. Replaces ``tests/test_decision.py``'s
    ``test_awaiting_local_implementation_review_is_declined_naming_review_implementation``
    at the ``evidence.decide`` level."""

    PHASE = "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, **overrides):
        work_item = _implementation_work_item(self.PHASE, implementation_review_stages=None, **overrides)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _bundle(self, **kwargs) -> Path:
        kwargs.setdefault("reviewed_implementation_head", _REVIEWED_HEAD)
        return fixtures.write_implementation_bundle(self.root, "wi-1", 1, **kwargs)

    def _feedback(self, status: str, role: str | None = _LOCAL_ROLE, **fields) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(status=status, reviewer_role=role, **fields),
        )

    def test_coherent_bundle_is_automatic_at_2_2(self) -> None:
        self._bundle()
        result = self._decide()
        self.assertTrue(result.automatic, result.reason)
        self.assertFalse(result.declined)
        self.assertIsNone(result.gate)
        self.assertEqual(result.action.command, "/review-implementation wi-1")

    def test_a_previous_rounds_non_block_verdict_does_not_gate(self) -> None:
        self._bundle()
        for status in ("APPROVE", "REVISE"):
            with self.subTest(status=status):
                self._feedback(status)
                self.assertTrue(self._decide().automatic)

    def test_without_a_coherent_bundle_it_is_the_coherence_gate(self) -> None:
        def absent() -> None:
            (self.root / ".ai-review" / "wi-1").mkdir(parents=True)

        def stale_revision() -> None:
            fixtures.write_implementation_bundle(
                self.root, "wi-1", 2, reviewed_implementation_head=_REVIEWED_HEAD,
            )

        cases = (
            ("absent", absent),
            ("stage", lambda: self._bundle(stage="plan")),
            ("implementation_revision", stale_revision),
            ("reviewed_implementation_head", lambda: self._bundle(reviewed_implementation_head="9" * 40)),
            ("worktree_root", lambda: self._bundle(worktree_root="/elsewhere")),
        )
        for clause, setup in cases:
            with self.subTest(clause=clause):
                shutil.rmtree(self.root / ".ai-review", ignore_errors=True)
                setup()
                result = self._decide()
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                self.assertIn(f"({clause})", result.evidence[0])
                self.assertIn(f"({clause}: ", result.gate.what_is_required)
                _assert_regeneration_steps(self, result, self.PHASE)

    def test_a_generation_head_behind_head_names_recover_implementation_provenance(self) -> None:
        self._bundle()
        (self.root / "later.txt").write_text("a commit after generation\n")
        head = fixtures.commit_all(self.root, "later")
        result = self._decide()
        self.assertFalse(result.automatic)
        _assert_provenance_only(self, result, head=head, paths=("later.txt",))

    def test_a_local_block_is_a_gate(self) -> None:
        self._bundle()
        self._feedback("BLOCK")
        result = self._decide()
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertIn("explicit user resolution is required before any further command runs",
                      result.gate.what_is_required)
        self.assertEqual(result.gate.safe_resume_command, "/review-implementation wi-1")
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/feedback/REVIEW_FEEDBACK.md")))

    def test_the_block_rule_is_fail_closed_on_role_case_and_binding(self) -> None:
        """Mirrors the plan-stage any-local-``BLOCK`` rule: a lower-case
        role or a missing binding line never turns a ``BLOCK`` into a
        launch."""
        self._bundle()
        for kwargs in ({"role": "local_model_implementation_review"}, {"reviewed_base_commit": None},
                       {"reviewed_bundle_id": None, "work_item": None}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                self._feedback("BLOCK", **kwargs)
                result = self._decide()
                self.assertFalse(result.automatic)
                self.assertIsNotNone(result.gate)

    def test_a_manual_role_block_does_not_gate_the_local_stage(self) -> None:
        self._bundle()
        self._feedback("BLOCK", role=_MANUAL_ROLE)
        self.assertTrue(self._decide().automatic)

    def test_the_bundle_gate_is_checked_ahead_of_the_block_gate(self) -> None:
        self._bundle(stage="plan")
        self._feedback("BLOCK")
        result = self._decide()
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/current/MANIFEST.md")))

    def test_rejected_wins_over_staleness(self) -> None:
        self._bundle(stage="plan")
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="step rename failed")
        result = self._decide()
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/REJECTED")))

    def test_other_versions_get_no_bundle_gate_and_are_declined(self) -> None:
        """The phase exists only at ``"2.2"``; elsewhere the dispatch rule
        declines the selection (no row), and no bundle gate runs."""
        for version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=version):
                result = self._decide(governing_workflow_version=version)
                self.assertTrue(result.declined)
                self.assertIsNone(result.gate)
                self.assertEqual(result.action.command, "/review-implementation wi-1")


class ApplyingReviewFeedbackCorrectedGateTest(unittest.TestCase):
    """``APPLYING_REVIEW_FEEDBACK`` in CP4: the corrected gate at every
    version (CP4B adds the ``"2.2"`` automatic path in front of it), behind
    the ``"2.2"``-only implementation-bundle gate, which never requires a
    current ``generation_head`` here."""

    PHASE = "APPLYING_REVIEW_FEEDBACK"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _decide(self, version: str, **overrides):
        work_item = _implementation_work_item(self.PHASE, governing_workflow_version=version, **overrides)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _assert_corrected_gate(self, result) -> None:
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertEqual(result.gate.safe_resume_command, "/apply-implementation-review wi-1")
        self.assertIn("/apply-implementation-review is legal from this phase", result.gate.what_is_required)
        self.assertIn("it skips its own entry transition", result.gate.what_is_required)
        self.assertIn("in progress or was interrupted", result.gate.what_is_required)
        self.assertNotIn("no Workflow command can legally run", result.gate.what_is_required)
        self.assertNotIn("no Workflow command can legally run", result.reason)

    def test_1_and_2_1_reach_the_corrected_gate_with_an_absent_or_incoherent_bundle(self) -> None:
        for version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=version, bundle="absent"):
                self._assert_corrected_gate(self._decide(version))
        fixtures.write_implementation_bundle(self.root, "wi-1", 1, stage="plan")
        for version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=version, bundle="incoherent"):
                self._assert_corrected_gate(self._decide(version))

    def test_2_2_with_a_coherent_bundle_reaches_the_corrected_gate(self) -> None:
        """Even with ``HEAD`` ahead of ``generation_head``: the pending
        review-stage state commit and fix commits land before the next
        generation record."""
        fixtures.write_implementation_bundle(self.root, "wi-1", 1, reviewed_implementation_head=_REVIEWED_HEAD)
        (self.root / "fix.txt").write_text("a review fix\n")
        fixtures.commit_all(self.root, "fix")
        self._assert_corrected_gate(self._decide("2.2"))

    def test_2_2_with_an_incoherent_bundle_is_the_bundle_gate(self) -> None:
        fixtures.write_implementation_bundle(self.root, "wi-1", 1, reviewed_implementation_head="9" * 40)
        result = self._decide("2.2")
        self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/current/MANIFEST.md")))
        self.assertIn("reviewed_implementation_head", result.gate.what_is_required)

    def test_rejected_wins_over_staleness(self) -> None:
        fixtures.write_implementation_bundle(self.root, "wi-1", 1, stage="plan")
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="step rename failed")
        for version in ("1", "2.1", "2.2"):
            with self.subTest(governing_workflow_version=version):
                result = self._decide(version)
                self.assertEqual(result.gate.artifact_path, str(Path(".ai-review/wi-1/REJECTED")))


class ImplementationRejectedMarkerGateTest(unittest.TestCase):
    """The ``REJECTED`` gate's text by version and phase (CP4, round 2's I3
    and round 4's O1): the ``"2.2"`` implementation-bundle-consuming phases
    carry the marker-clearing clause and the ordered recovery steps;
    ``"1"``/``"2.1"`` ``APPLYING_REVIEW_FEEDBACK`` names no generator at
    all. ``StalePlanBundleGateTest.test_rejected_marker_gate_elsewhere_is_unchanged``
    stays the byte-identical pin for branch 4."""

    BASE = "a" * 40
    BUNDLE = Path(".ai-review/wi-1/current")
    CLEAR = [
        "resolve the failure the REJECTED marker at .ai-review/wi-1/REJECTED records (step rename failed)",
        "do not delete surviving author files unless that specific failure requires it",
        "a successful generation then clears the marker itself",
    ]

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        fixtures.copy_real_commands_dir(self.root / ".claude" / "commands")
        self.managed_repo = fixtures.build_target_managed_repository(self.root)
        fixtures.write_rejected_marker(self.root, "wi-1", scoped=True, detail="step rename failed")

    def _decide(self, phase: str, version: str, **overrides):
        work_item = _implementation_work_item(
            phase, governing_workflow_version=version, base_commit=self.BASE, **overrides,
        )
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _steps_needles(self, *, verb: str) -> list[str]:
        return [
            f"{verb} {self.BUNDLE / 'IMPLEMENTATION_SUMMARY.md'}", "`implementation_revision: 1`",
            "assert_stage_completeness",
            f"{verb} {self.BUNDLE / 'REVIEW_REQUEST.md'}", "`review_content_id: <hex>`",
            "workflow_state.approval_review_content_id(repo_root, stage=\"implementation\", "
            f"base_commit=\"{self.BASE}\", head=\"HEAD\", work_item_type=\"product\", work_item_id=\"wi-1\", "
            "artifacts_path=workflow_fingerprint.artifacts_path_for_work_item(\"wi-1\"))",
            f"{verb} {self.BUNDLE / 'TEST_RESULTS.md'}",
            f"scripts/prepare-ai-review.sh {self.BASE} implementation wi-1",
            f"scripts/prepare-ai-review.sh {self.BASE} post-fix wi-1",
            "refuses at preflight",
        ]

    def test_each_2_2_implementation_bundle_consuming_phase_carries_the_recovery_steps(self) -> None:
        fixtures.write_implementation_bundle(self.root, "wi-1", 1, reviewed_implementation_head=_REVIEWED_HEAD)
        self.assertEqual(evidence.IMPLEMENTATION_BUNDLE_CONSUMING_PHASES, {
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            "APPLYING_REVIEW_FEEDBACK", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        })
        for phase in sorted(evidence.IMPLEMENTATION_BUNDLE_CONSUMING_PHASES):
            with self.subTest(phase=phase):
                result = self._decide(phase, "2.2")
                gate = result.gate
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                self.assertEqual(gate.artifact_path, str(Path(".ai-review/wi-1/REJECTED")))
                for text in (gate.what_is_required, gate.safe_resume_command):
                    _assert_in_order(self, text, self.CLEAR + self._steps_needles(verb="refresh"))
                    self.assertTrue("1. refresh" in text and "0. " not in text, text)
                self.assertIn("then, before any implementation review runs, perform in order: 1. ",
                              gate.what_is_required)
                self.assertTrue(gate.what_is_required.startswith(
                    "the current bundle was withdrawn (step rename failed); "))
                self.assertNotEqual(gate.safe_resume_command, evidence._regeneration_command(phase, "wi-1"))

    def test_a_withdrawn_bundle_asks_for_writes_and_names_the_quarantine(self) -> None:
        quarantine = self.root / ".ai-review" / "wi-1" / "current.rejected-0123abcd"
        quarantine.mkdir(parents=True)
        for phase, source in (
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "restoring the previous round's author files from"),
            ("APPLYING_REVIEW_FEEDBACK",
             "restoring the author files of the bundle REVIEW_FEEDBACK.md reviewed from"),
        ):
            with self.subTest(phase=phase):
                gate = self._decide(phase, "2.2").gate
                for text in (gate.what_is_required, gate.safe_resume_command):
                    _assert_in_order(self, text, [
                        f"0. write {self.BUNDLE / 'CONTEXT_FILES.txt'}", source,
                        ".ai-review/wi-1/current.rejected-0123abcd/",
                        f"1. write {self.BUNDLE / 'IMPLEMENTATION_SUMMARY.md'}",
                        f"2. write {self.BUNDLE / 'REVIEW_REQUEST.md'}", "Review request format",
                        f"3. write {self.BUNDLE / 'TEST_RESULTS.md'}",
                        "4. run scripts/prepare-ai-review.sh",
                    ])
                    self.assertNotIn("refresh", text)

    def test_a_zero_byte_author_file_asks_for_writes(self) -> None:
        bundle = fixtures.write_implementation_bundle(
            self.root, "wi-1", 1, reviewed_implementation_head=_REVIEWED_HEAD,
        )
        (bundle / "IMPLEMENTATION_SUMMARY.md").write_bytes(b"")
        gate = self._decide("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2").gate
        _assert_in_order(self, gate.safe_resume_command, [
            "0. write", "1. write", "IMPLEMENTATION_SUMMARY.md",
        ])

    def test_1_and_2_1_applying_review_feedback_names_no_generator(self) -> None:
        quarantine = self.root / ".ai-review" / "wi-1" / "current.rejected-0123abcd"
        quarantine.mkdir(parents=True)
        for version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=version):
                result = self._decide("APPLYING_REVIEW_FEEDBACK", version)
                gate = result.gate
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                _assert_in_order(self, gate.what_is_required, self.CLEAR + [
                    "/apply-implementation-review is legal from this phase",
                    "its step 1 (assert_bundle_not_rejected) refuses until a successful generation "
                    "clears the marker",
                    ".ai-review/wi-1/current.rejected-0123abcd/",
                    "reruns /apply-implementation-review only if the regenerated bundle_id equals the "
                    "feedback's Reviewed bundle ID",
                ])
                self.assertIn("the bundle REVIEW_FEEDBACK.md reviewed was withdrawn", gate.what_is_required)
                self.assertEqual(gate.safe_resume_command, "workflow-controller explain --work-item wi-1")
                for text in (gate.what_is_required, gate.safe_resume_command):
                    self.assertNotIn("prepare-ai-review.sh", text)
                    self.assertNotIn(evidence._regeneration_command("APPLYING_REVIEW_FEEDBACK", "wi-1"), text)

    def test_1_and_2_1_applying_review_feedback_without_a_quarantine_still_names_the_source(self) -> None:
        gate = self._decide("APPLYING_REVIEW_FEEDBACK", "2.1").gate
        self.assertIn("restores that bundle from the newest current.rejected-* quarantine and "
                      "regenerates it", gate.what_is_required)

    def test_1_and_2_1_external_implementation_review_keeps_the_bare_text(self) -> None:
        """Branch 4, byte-identical (also pinned by the golden)."""
        for version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=version):
                gate = self._decide("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", version).gate
                regen = evidence._regeneration_command("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "wi-1")
                self.assertEqual(gate.safe_resume_command, regen)


class ImplementationGeneratorStageTest(unittest.TestCase):
    """The generator ``<stage>`` the recovery steps name, derived from the
    committed phase of the parent of this work item's most recent
    generation-record commit -- never from job records -- against a real
    temporary git repository. (CP4B's recovery gate reuses the helper and
    adds its own per-case tests.)"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        self.base = fixtures.current_head(self.root)

    def _commit_phase(self, phase: str, *, record: bool = False, revision: int = 1,
                      supersedes: bool = False) -> str:
        _write_state(self.root, {_WI: {"phase": phase, "checkpoints": {}}})
        trailers = (
            _record_trailers(revision=revision, supersedes="x" * 40 if supersedes else None) if record else ()
        )
        return _commit(self.root, f"{phase}{' record' if record else ''}", trailers=trailers)

    def _stages(self, generation_head: str | None):
        if generation_head is not None:
            fixtures.write_implementation_manifest(self.root, _WI, 1, generation_head=generation_head)
        work_item = _implementation_work_item("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", base_commit=self.base)
        return evidence.implementation_generator_stages(self.root, work_item)

    def test_a_final_pass_record_at_generation_head_is_implementation(self) -> None:
        self._commit_phase("SELF_REVIEWING_IMPLEMENTATION")
        record = self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", record=True)
        self.assertEqual(self._stages(record), ("implementation",))

    def test_a_post_fix_record_at_generation_head_is_post_fix(self) -> None:
        self._commit_phase("APPLYING_REVIEW_FEEDBACK")
        record = self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", record=True, revision=2)
        self.assertEqual(self._stages(record), ("post-fix",))

    def test_a_newer_record_after_generation_head_decides(self) -> None:
        """The generator failed after the round's record ``T``: the manifest
        is the previous round's, and ``T``'s parent decides."""
        self._commit_phase("SELF_REVIEWING_IMPLEMENTATION")
        first = self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", record=True)
        self._commit_phase("APPLYING_REVIEW_FEEDBACK")
        self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", record=True, revision=2)
        self.assertEqual(self._stages(first), ("post-fix",))

    def test_a_same_content_record_is_post_fix(self) -> None:
        self._commit_phase("SELF_REVIEWING_IMPLEMENTATION")
        first = self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", record=True)
        self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", record=True, supersedes=True)
        self.assertEqual(self._stages(first), ("post-fix",))

    def test_no_record_or_no_manifest_or_a_non_ancestor_names_both(self) -> None:
        self.assertEqual(self._stages(None), ("implementation", "post-fix"))
        self.assertEqual(self._stages(self.base), ("implementation", "post-fix"))
        self.assertEqual(self._stages("9" * 40), ("implementation", "post-fix"))



# ---------------------------------------------------------------------------
# Automatic-lifecycle-orchestration CP4B: the implementation-bundle gate's
# generation-record-aware recovery, and the "2.2" APPLYING_REVIEW_FEEDBACK
# automatic path with its pending-write addendum and relaunch bound.
# ---------------------------------------------------------------------------


def _ignored_ai_review_target(tmp_root: Path) -> tuple[Path, str]:
    """:func:`_make_target` plus ``.ai-review/`` ignored the way a real
    installation ignores it, so a commit's changed paths never include
    bundle files. Returns ``(root, base_commit)``."""
    root = _make_target(tmp_root)
    (root / ".gitignore").write_text(".ai-review/\n")
    return root, fixtures.commit_all(root, "ignore .ai-review/")


class ImplementationBundleRecoveryGateTest(unittest.TestCase):
    """The ``"2.2"`` implementation-bundle gate, final form (CP4B), per
    ordered case, against a real temporary git repository whose commits
    carry this work item's committed phase and real generation-record
    trailers: a malformed ordinary ``T`` first, then the provenance-only
    case, then the ordered regeneration steps -- whose generator ``<stage>``
    comes from ``T``'s parent's committed phase, never from job records."""

    PHASE = "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, self.base = _ignored_ai_review_target(Path(self._tmp.name))
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _state_commit(self, phase: str, subject: str, *, trailers: tuple[str, ...] = ()) -> str:
        _write_state(self.root, {_WI: {"work_item_id": _WI, "phase": phase}})
        return _commit(self.root, subject, trailers=trailers)

    def _decide(self, phase: str = PHASE, **overrides):
        overrides.setdefault("base_commit", self.base)
        work_item = _implementation_work_item(phase, **overrides)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def _first_round(self) -> str:
        """``/milestone-implement``'s final pass: step 1f's checkpoint commit
        records ``SELF_REVIEWING_IMPLEMENTATION``, then step 4's record
        ``T0`` (implementation_revision 1), whose generation completed.
        Returns ``T0``."""
        self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "Implement CP1 (step 1f)")
        t0 = self._state_commit(self.PHASE, "T0", trailers=_record_trailers(revision=1))
        fixtures.write_implementation_bundle(
            self.root, _WI, 1, reviewed_implementation_head=_REVIEWED_HEAD, generation_head=t0,
        )
        return t0

    def _pending_write(self) -> str:
        return self._state_commit(
            "APPLYING_REVIEW_FEEDBACK", "Record the review-stage state write",
            trailers=("Workflow-Work-Item: wi-1",),
        )

    def _assert_stage(self, result, stage: str) -> None:
        other = "implementation" if stage == "post-fix" else "post-fix"
        for text in (result.gate.what_is_required, result.gate.safe_resume_command):
            self.assertIn(
                f"run scripts/prepare-ai-review.sh {self.base} {stage} wi-1 -- if this refuses at preflight",
                text,
            )
            self.assertNotIn(f"scripts/prepare-ai-review.sh {self.base} {other} wi-1", text)

    def test_a_malformed_ordinary_record_is_the_malformed_record_gate_never_the_steps(self) -> None:
        self._first_round()
        # The literal worker: no separate commit of the pending
        # review-stage write before its generation-record commit.
        record = self._state_commit(self.PHASE, "T (literal worker)", trailers=_record_trailers(revision=2))
        result = self._decide(implementation_revision=2)
        gate = result.gate
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertIn("(implementation_revision)", result.evidence[0])
        self.assertIn(record, result.evidence[1])
        _assert_in_order(self, gate.what_is_required, [
            f"HEAD ({record}) is this work item's ordinary-role bundle-generation-record commit",
            f"committed phase at HEAD^ ({self.PHASE}) equals its committed phase at HEAD ({self.PHASE})",
            "validate_bundle_generation_record_commit rejects it (OPUS-R101-001)",
            "No Workflow command repairs this",
            "the pending review-stage state write lands alone, in its own commit, before the "
            "generation-record commit",
            "/apply-implementation-review step 7",
        ])
        self.assertEqual(gate.safe_resume_command, "workflow-controller explain --work-item wi-1")
        for text in (gate.what_is_required, gate.safe_resume_command):
            self.assertNotIn("prepare-ai-review.sh", text)
            self.assertNotIn("/recover-implementation-provenance", text)

    def test_a_malformed_record_is_reported_even_when_only_generation_head_is_stale(self) -> None:
        """The same-revision shape (only ``generation_head`` lags behind a
        newer record, as after a ``same_content`` round) is case 3's -- unless
        that newer record is a malformed ordinary ``T``, which case 1 reports
        first, never the regeneration steps."""
        self._first_round()
        self._state_commit(self.PHASE, "T (literal worker)", trailers=_record_trailers(revision=1))
        result = self._decide()
        self.assertIn("(generation_head)", result.evidence[0])
        self.assertIn("OPUS-R101-001", result.gate.what_is_required)
        self.assertNotIn("prepare-ai-review.sh", result.gate.safe_resume_command)

    def test_an_excluded_only_commit_after_the_record_names_the_conditional_provenance_recovery(self) -> None:
        self._first_round()
        (self.root / "docs" / "notes.md").write_text("an excluded-only edit\n")
        first = _commit(self.root, "Excluded-only notes")
        (self.root / "docs" / "more.md").write_text("another excluded-only edit\n")
        later = _commit(self.root, "More excluded-only notes")
        for phase in sorted(evidence.PROVENANCE_RECOVERY_LEGAL_PHASES):
            with self.subTest(phase=phase):
                result = self._decide(phase)
                _assert_provenance_only(self, result, head=later, paths=("docs/notes.md", "docs/more.md"))
                self.assertIn("(generation_head)", result.evidence[0])
                _assert_in_order(self, " | ".join(result.evidence[1:]), [
                    first, "'Excluded-only notes'", "docs/notes.md",
                    later, "'More excluded-only notes'", "docs/more.md",
                ])

    def test_provenance_recovery_is_named_only_at_the_phases_workflow_admits_it_from(self) -> None:
        scripts_dir = fixtures.REPO_ROOT / "scripts"
        sys.path.insert(0, str(scripts_dir))
        import workflow_state as real_workflow_state  # noqa: PLC0415

        self.assertEqual(
            evidence.PROVENANCE_RECOVERY_LEGAL_PHASES,
            real_workflow_state.bundle_generation_recovered_role_legal_committed_phases("2.2"),
        )
        self.assertNotIn("APPLYING_REVIEW_FEEDBACK", evidence.PROVENANCE_RECOVERY_LEGAL_PHASES)
        # Unreachable through `decide` (the clause is never evaluated at
        # APPLYING_REVIEW_FEEDBACK), but the gate itself never names the
        # command at a phase it is not legal from.
        self._first_round()
        (self.root / "docs" / "notes.md").write_text("an excluded-only edit\n")
        head = _commit(self.root, "Excluded-only notes")
        work_item = _implementation_work_item("APPLYING_REVIEW_FEEDBACK", base_commit=self.base)
        result = evidence._implementation_bundle_gate(
            self.root, work_item, "generation_head", "manifest generation_head != HEAD", head,
        )
        _assert_regeneration_steps(self, result, "APPLYING_REVIEW_FEEDBACK")

    def test_a_same_content_record_whose_generator_failed_regenerates_post_fix_never_provenance(self) -> None:
        """I4's regression: ``record_bundle_generation(...,
        outcome="same_content")`` leaves ``implementation_revision``/
        ``reviewed_implementation_head`` unchanged, so only ``generation_head``
        lags, ``HEAD`` is the new ``T`` itself, and
        ``/recover-implementation-provenance`` would refuse ("nothing to
        recover")."""
        t0 = self._first_round()
        self._pending_write()
        record = self._state_commit(
            self.PHASE, "same_content T", trailers=_record_trailers(revision=1, supersedes=t0),
        )
        result = self._decide()
        self.assertIn("(generation_head)", result.evidence[0])
        self.assertIn(record, result.evidence[1])
        _assert_regeneration_steps(self, result, self.PHASE)
        self._assert_stage(result, "post-fix")
        self.assertTrue(result.gate.safe_resume_command.startswith("1. refresh"))

    def test_an_ordinary_post_fix_record_whose_generator_failed_regenerates_post_fix(self) -> None:
        self._first_round()
        self._pending_write()
        (self.root / "fix.txt").write_text("a review fix\n")
        _commit(self.root, "Apply a review fix")
        self._state_commit(self.PHASE, "T", trailers=_record_trailers(revision=2))
        result = self._decide(implementation_revision=2)
        self.assertIn("(implementation_revision)", result.evidence[0])
        self.assertNotIn("OPUS-R101-001", result.gate.what_is_required)
        _assert_regeneration_steps(self, result, self.PHASE)
        self._assert_stage(result, "post-fix")

    def test_a_final_pass_record_whose_generator_failed_regenerates_implementation(self) -> None:
        with self.subTest(round="first, no manifest at all"):
            self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "Enter SELF_REVIEWING_IMPLEMENTATION (step 2)")
            self._state_commit(self.PHASE, "T", trailers=_record_trailers(revision=1))
            result = self._decide()
            self.assertIn("(absent)", result.evidence[0])
            _assert_regeneration_steps(self, result, self.PHASE)
            self._assert_stage(result, "implementation")
            self.assertTrue(result.gate.safe_resume_command.startswith("0. write"))
        with self.subTest(round="after a plan re-approval, over the previous round's manifest"):
            t0 = self._state_commit(self.PHASE, "T0", trailers=_record_trailers(revision=1))
            fixtures.write_implementation_bundle(
                self.root, _WI, 1, reviewed_implementation_head=_REVIEWED_HEAD, generation_head=t0,
            )
            self._state_commit("IMPLEMENTING", "Approve the amended plan")
            self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "Implement CP9 (step 1f)")
            self._state_commit(self.PHASE, "T1", trailers=_record_trailers(revision=2))
            result = self._decide(implementation_revision=2)
            self.assertIn("(implementation_revision)", result.evidence[0])
            self._assert_stage(result, "implementation")

    def test_no_record_or_a_non_ancestor_generation_head_names_both_forms(self) -> None:
        def assert_both(result) -> None:
            _assert_regeneration_steps(self, result, self.PHASE)
            for text in (result.gate.what_is_required, result.gate.safe_resume_command):
                _assert_in_order(self, text, [
                    f"run scripts/prepare-ai-review.sh {self.base} implementation wi-1 if this work "
                    "item's most recent generation-record commit is /milestone-implement's",
                    f"scripts/prepare-ai-review.sh {self.base} post-fix wi-1 if it is "
                    "/apply-implementation-review's",
                    "this Controller cannot derive which",
                ])

        with self.subTest(case="no generation-record commit"):
            self._state_commit(self.PHASE, "a phase commit with no record trailer")
            assert_both(self._decide())
        with self.subTest(case="a non-ancestor generation_head, even with HEAD a record"):
            fixtures.run(["git", "checkout", "-q", "-b", "side"], cwd=self.root)
            side = self._state_commit(self.PHASE, "side T", trailers=_record_trailers(revision=1))
            fixtures.run(["git", "checkout", "-q", "-"], cwd=self.root)
            self._state_commit("SELF_REVIEWING_IMPLEMENTATION", "Implement CP1 (step 1f)")
            self._state_commit(self.PHASE, "T", trailers=_record_trailers(revision=1))
            fixtures.write_implementation_bundle(
                self.root, _WI, 1, reviewed_implementation_head=_REVIEWED_HEAD, generation_head=side,
            )
            result = self._decide()
            self.assertIn("(generation_head)", result.evidence[0])
            assert_both(result)


class ApplyingReviewFeedbackAutomaticPathTest(unittest.TestCase):
    """``"2.2"`` ``APPLYING_REVIEW_FEEDBACK`` (CP4B): an admissible
    two-stage ``REVISE`` launches ``/apply-implementation-review``, with the
    pinned pending-write addendum exactly while ``HEAD`` does not yet record
    the phase; anything else is CP4's corrected gate naming why, and an
    unverified earlier attempt against the same bundle is the relaunch-bound
    gate."""

    PHASE = "APPLYING_REVIEW_FEEDBACK"
    JOB_ID = "20260923T120000Z-0123abcd"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, _base = _ignored_ai_review_target(Path(self._tmp.name))
        self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        fixtures.write_implementation_bundle(self.root, _WI, 1, reviewed_implementation_head=_REVIEWED_HEAD)
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _commit_phase(self, phase: str, work_item_id: str = _WI) -> str:
        _write_state(self.root, {work_item_id: {"work_item_id": work_item_id, "phase": phase}})
        return _commit(self.root, f"Record {phase}")

    def _feedback(self, status: str | None = "REVISE", role: str | None = _LOCAL_ROLE, **fields) -> None:
        fixtures.write_review_feedback(
            self.root, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(status=status, reviewer_role=role, **fields),
        )

    def _decide(self, *, last_apply_job=None, **overrides):
        work_item = _implementation_work_item(self.PHASE, **overrides)
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item,
                               last_apply_job=last_apply_job)

    def _job(self, status: str = "FAILED", bundle_id: str | None = "b" * 64, **fields):
        view = dict(job_id=self.JOB_ID, command="/apply-implementation-review",
                    from_phase=self.PHASE, status=status, pre_bundle_manifest_bundle_id=bundle_id)
        view.update(fields)
        return evidence.LaunchedJobView(**view)

    def _assert_launch(self, result, *, addendum_phase: str | None) -> None:
        self.assertTrue(result.automatic, result.reason)
        self.assertFalse(result.declined)
        self.assertIsNone(result.gate)
        self.assertEqual(result.action.command, "/apply-implementation-review wi-1")
        if addendum_phase is None:
            self.assertIsNone(result.action.task_addendum)
        else:
            self.assertEqual(result.action.task_addendum,
                             evidence.pending_review_stage_write_addendum("wi-1", addendum_phase))

    def _assert_corrected_gate(self, result, *fragments: str) -> None:
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertEqual(result.gate.safe_resume_command, "/apply-implementation-review wi-1")
        self.assertIn("/apply-implementation-review is legal from this phase", result.gate.what_is_required)
        self.assertIn("this Controller does not run it automatically", result.gate.what_is_required)
        # `<feedback_dir>` resolves scoped-else-flat on its own existence.
        self.assertTrue(result.gate.artifact_path.endswith(str(Path("feedback/REVIEW_FEEDBACK.md"))))
        for fragment in fragments:
            self.assertIn(fragment, result.gate.what_is_required)

    def test_an_admissible_revise_with_the_write_pending_launches_with_the_addendum(self) -> None:
        for role in (_LOCAL_ROLE, _MANUAL_ROLE):
            with self.subTest(role=role):
                self._feedback(role=role)
                result = self._decide()
                self._assert_launch(result, addendum_phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
                self.assertIn("`HEAD` records `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`",
                              result.action.task_addendum)
                self.assertIn("`Workflow-Work-Item: wi-1`", result.action.task_addendum)
                self.assertIn("committed phase at HEAD: AWAITING_LOCAL_IMPLEMENTATION_REVIEW", result.evidence)

    def test_head_already_recording_the_phase_launches_the_bare_command(self) -> None:
        self._commit_phase(self.PHASE)
        self._feedback()
        self._assert_launch(self._decide(), addendum_phase=None)

    def test_an_unreadable_committed_phase_gates(self) -> None:
        self._commit_phase("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", work_item_id="another-item")
        self._feedback()
        self._assert_corrected_gate(self._decide(), "committed phase at HEAD cannot be read")

    def test_inadmissible_or_absent_feedback_is_the_corrected_gate_naming_why(self) -> None:
        cases = (
            ("absent", None, "no REVIEW_FEEDBACK.md is on file"),
            ("APPROVE", dict(status="APPROVE"), "APPROVE is on file"),
            ("BLOCK", dict(status="BLOCK"), "BLOCK is on file"),
            ("wrong role", dict(role="LOCAL_MODEL_PLAN_REVIEW"), "Reviewer role"),
            ("no role", dict(role=None), "Reviewer role"),
            ("bundle mismatch", dict(reviewed_bundle_id="a" * 64), "Reviewed bundle ID"),
            ("other work item", dict(work_item="wi-2"), "Work item"),
        )
        for name, feedback, fragment in cases:
            with self.subTest(case=name):
                shutil.rmtree(self.root / ".ai-review" / "wi-1" / "feedback", ignore_errors=True)
                if feedback is not None:
                    self._feedback(**feedback)
                self._assert_corrected_gate(self._decide(), fragment)

    def test_an_unverified_earlier_attempt_against_the_same_bundle_is_the_relaunch_bound(self) -> None:
        self._feedback()
        for status in ("FAILED", "INTERRUPTED", "INCOMPLETE"):
            with self.subTest(status=status):
                result = self._decide(last_apply_job=self._job(status))
                gate = result.gate
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                self.assertFalse(result.declined)
                _assert_in_order(self, gate.what_is_required, [
                    f"job {self.JOB_ID}, ended {status} against the same bundle (bundle_id {'b' * 64})",
                    "never relaunches an attempt that did not verify",
                    "If that attempt passed its own step 4",
                    "every retry would refuse at step 1 (assert_feedback_matches_bundle)",
                    f"restores the edited bundle file from the bundle's archive "
                    f"({Path('.ai-review/wi-1/review-bundle.tar.gz')})",
                    "in a supervised session", "or completes the round by hand",
                ])
                self.assertEqual(gate.safe_resume_command, "workflow-controller explain --work-item wi-1")
                self.assertIn(self.JOB_ID, result.evidence[0])

    def test_a_null_recorded_bundle_counts_as_the_same_bundle(self) -> None:
        self._feedback()
        result = self._decide(last_apply_job=self._job(bundle_id=None))
        self.assertFalse(result.automatic)
        self.assertIn("counts as the same one -- fail closed", result.gate.what_is_required)

    def test_no_bound_after_a_verified_attempt_another_bundle_or_another_phase(self) -> None:
        self._feedback()
        for name, job in (
            ("none", None),
            ("FINISHED", self._job("FINISHED")),
            ("another bundle", self._job(bundle_id="a" * 64)),
            ("another phase", self._job(from_phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW")),
            ("another command", self._job(command="/review-implementation")),
        ):
            with self.subTest(case=name):
                self._assert_launch(self._decide(last_apply_job=job),
                                    addendum_phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

    def test_admissibility_is_checked_ahead_of_the_relaunch_bound(self) -> None:
        self._feedback(status="APPROVE")
        self._assert_corrected_gate(self._decide(last_apply_job=self._job()), "APPROVE is on file")

    def test_the_relaunch_bound_helper(self) -> None:
        self.assertFalse(evidence.relaunch_bound_applies(None, "b" * 64))
        self.assertTrue(evidence.relaunch_bound_applies(self._job(), "b" * 64))
        self.assertTrue(evidence.relaunch_bound_applies(self._job(bundle_id=None), "b" * 64))
        self.assertFalse(evidence.relaunch_bound_applies(self._job(), "a" * 64))
        self.assertFalse(evidence.relaunch_bound_applies(self._job("FINISHED"), "b" * 64))

    def test_1_and_2_1_keep_the_static_corrected_gate_even_with_an_admissible_revise(self) -> None:
        self._feedback()
        for version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=version):
                work_item = _implementation_work_item(self.PHASE, governing_workflow_version=version)
                result = evidence.decide(self.managed_repo, snapshot=None, work_item=work_item,
                                         last_apply_job=self._job())
                self.assertEqual(result, decision.decide(self.managed_repo, snapshot=None, work_item=work_item))
                self.assertIsNone(result.action)
                self.assertEqual(result.gate.safe_resume_command, "/apply-implementation-review wi-1")


class PendingReviewStageWriteAddendumTest(unittest.TestCase):
    """The row-18 task addendum ("The pending review-stage state write",
    item 1): pinned byte-for-byte, naming no user-only command, and
    accepted by the worker's own user-only scan when formatted."""

    PINNED = (
        "Controller note (pending review-stage state write): `docs/ai-workflow/WORKFLOW_STATE.json` "
        "carries this work item's uncommitted review-stage write (working-tree phase "
        "`APPLYING_REVIEW_FEEDBACK`; `HEAD` records `{committed_phase}`). Before any other commit this "
        "command makes, commit that pending change alone: stage exactly "
        "`docs/ai-workflow/WORKFLOW_STATE.json`, unmodified from what the review-stage writer "
        "produced, with a message whose final paragraph is the single trailer "
        "`Workflow-Work-Item: {work_item_id}` and no other Workflow trailer. This is the same shape "
        "`/milestone-implement` step 2 uses for its state-only transition commit. Without it, step 7's "
        "generation-record commit has no phase change in its own diff, and "
        "`validate_bundle_generation_record_commit` rejects it (`OPUS-R101-001`)."
    )

    def test_pinned_byte_for_byte(self) -> None:
        self.assertEqual(evidence.PENDING_REVIEW_STAGE_WRITE_ADDENDUM, self.PINNED)

    def test_formatted_with_the_work_item_and_committed_phase(self) -> None:
        text = evidence.pending_review_stage_write_addendum("wi-9", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(text, self.PINNED.format(
            work_item_id="wi-9", committed_phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
        ))
        self.assertNotIn("{", text)

    def test_names_no_user_only_command_and_passes_the_worker_scan(self) -> None:
        from controller import worker

        formatted = evidence.pending_review_stage_write_addendum("wi-1", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        for name in worker.USER_ONLY_COMMANDS:
            with self.subTest(command=name):
                self.assertNotIn(name, evidence.PENDING_REVIEW_STAGE_WRITE_ADDENDUM)
        worker._assert_not_user_only(f"/apply-implementation-review wi-1\n\n{formatted}")



class UncommittedImplementationStateGateTest(unittest.TestCase):
    """The durable-state gate at ``IMPLEMENTING``/
    ``SELF_REVIEWING_IMPLEMENTATION`` (the approved plan's CP7 scenario 3),
    against a real repository whose committed state differs from the
    working tree's: a checkpoint ``COMPLETE`` or the
    ``SELF_REVIEWING_IMPLEMENTATION`` transition recorded only in the
    working tree replaces the automatic ``/milestone-implement`` selection
    with a gate, while a committed, durable state still selects it."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _make_target(Path(self._tmp.name))
        self.managed_repo = fixtures.build_target_managed_repository(self.root)

    def _state(self, phase: str, checkpoints: dict, *, others: dict | None = None) -> None:
        _write_state(self.root, {_WI: {"work_item_id": _WI, "phase": phase, "checkpoints": checkpoints},
                                 **(others or {})})

    def _decide(self, phase: str, checkpoints: dict, version: str = "2.2", **overrides):
        """The decision for the working tree's ``phase``/``checkpoints``
        (also written to disk, uncommitted)."""
        self._state(phase, checkpoints)
        overrides.setdefault("plan_approval", {"status": "CURRENT"})
        work_item = fixtures.build_work_item_view(
            phase=phase, governing_workflow_version=version, checkpoints=checkpoints, **overrides,
        )
        return evidence.decide(self.managed_repo, snapshot=None, work_item=work_item), work_item

    def _assert_gate(self, result, phase: str, facts: tuple[str, ...]) -> None:
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertEqual(result.observed_phase, phase)
        self.assertEqual(result.evidence, facts)
        gate = result.gate
        self.assertEqual(gate.phase, phase)
        self.assertEqual(gate.work_item_id, _WI)
        self.assertEqual(gate.repository, str(self.root))
        self.assertEqual(gate.artifact_path, str(self.root / _STATE_REL))
        self.assertEqual(gate.safe_resume_command, f"workflow-controller explain --work-item {_WI}")
        self.assertTrue(gate.what_is_required.startswith(
            f"the working tree's {_STATE_REL} records state that HEAD does not durably record: "
            + "; ".join(facts) + ". "
        ), gate.what_is_required)
        self.assertIn("commit the completion as that step would have, with its trailers, or discard it",
                      gate.what_is_required)
        self.assertIn("HEAD does not durably record", result.reason)

    def _assert_selected(self, result, phase: str) -> None:
        self.assertTrue(result.automatic)
        self.assertIsNone(result.gate)
        self.assertEqual(result.observed_phase, phase)
        self.assertEqual(result.action.command, f"/milestone-implement {_WI}")

    def test_a_committed_durable_state_still_selects_milestone_implement(self) -> None:
        complete = {"status": "COMPLETE"}
        cases = (
            ("IMPLEMENTING", {"CP1": complete}, {"CP1": complete}),
            # Step 1d's uncommitted IN_PROGRESS is the dirty-resume state
            # the command itself resolves, never a fact here.
            ("IMPLEMENTING", {"CP1": complete}, {"CP1": complete, "CP2": {"status": "IN_PROGRESS"}}),
            ("SELF_REVIEWING_IMPLEMENTATION", {"CP1": complete, "CP2": complete},
             {"CP1": complete, "CP2": complete}),
        )
        for version in ("2.1", "2.2"):
            for phase, committed, working in cases:
                with self.subTest(version=version, phase=phase, working=working):
                    self._state(phase, committed)
                    _commit(self.root, "committed state")
                    result, work_item = self._decide(phase, working, version)
                    self._assert_selected(result, phase)
                    self.assertEqual(evidence.uncommitted_implementation_state(self.root, work_item), ())

    def test_an_uncommitted_checkpoint_completion_gates(self) -> None:
        self._state("IMPLEMENTING", {"CP1": {"status": "IN_PROGRESS"}})
        _commit(self.root, "CP1 started")
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                result, _ = self._decide("IMPLEMENTING", {"CP1": {"status": "COMPLETE"}}, version)
                self._assert_gate(result, "IMPLEMENTING", (
                    "checkpoint 'CP1' is COMPLETE in the working tree's WORKFLOW_STATE.json, but its status "
                    "in the state committed at HEAD is 'IN_PROGRESS'",
                ))

    def test_an_uncommitted_self_review_transition_gates(self) -> None:
        complete = {"CP1": {"status": "COMPLETE"}, "CP2": {"status": "COMPLETE"}}
        self._state("IMPLEMENTING", complete)
        _commit(self.root, "every checkpoint complete")
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                result, _ = self._decide("SELF_REVIEWING_IMPLEMENTATION", complete, version)
                self._assert_gate(result, "SELF_REVIEWING_IMPLEMENTATION", (
                    "the working tree's phase is 'SELF_REVIEWING_IMPLEMENTATION', but the committed phase at "
                    "HEAD is 'IMPLEMENTING'",
                ))

    def test_an_uncommitted_last_checkpoint_names_the_checkpoint_then_the_phase(self) -> None:
        self._state("IMPLEMENTING", {"CP1": {"status": "COMPLETE"}})
        _commit(self.root, "CP1 complete")
        result, _ = self._decide(
            "SELF_REVIEWING_IMPLEMENTATION", {"CP1": {"status": "COMPLETE"}, "CP2": {"status": "COMPLETE"}},
        )
        self._assert_gate(result, "SELF_REVIEWING_IMPLEMENTATION", (
            "checkpoint 'CP2' is COMPLETE in the working tree's WORKFLOW_STATE.json, but its status in the "
            "state committed at HEAD is absent",
            "the working tree's phase is 'SELF_REVIEWING_IMPLEMENTATION', but the committed phase at HEAD is "
            "'IMPLEMENTING'",
        ))

    def test_another_work_items_committed_status_never_counts(self) -> None:
        self._state("IMPLEMENTING", {}, others={
            "wi-2": {"work_item_id": "wi-2", "phase": "IMPLEMENTING",
                     "checkpoints": {"CP1": {"status": "COMPLETE"}}},
        })
        _commit(self.root, "another item's CP1")
        result, _ = self._decide("IMPLEMENTING", {"CP1": {"status": "COMPLETE"}})
        self._assert_gate(result, "IMPLEMENTING", (
            "checkpoint 'CP1' is COMPLETE in the working tree's WORKFLOW_STATE.json, but its status in the "
            "state committed at HEAD is absent",
        ))

    def test_an_unreadable_committed_state_fails_closed(self) -> None:
        # WORKFLOW_STATE.json was never committed: HEAD records nothing.
        result, work_item = self._decide("SELF_REVIEWING_IMPLEMENTATION", {"CP1": {"status": "COMPLETE"}})
        facts = (
            "checkpoint 'CP1' is COMPLETE in the working tree's WORKFLOW_STATE.json, but its status in the "
            "state committed at HEAD is unreadable (WORKFLOW_STATE.json cannot be read at HEAD)",
            "the working tree's phase is 'SELF_REVIEWING_IMPLEMENTATION', but the committed phase at HEAD is None",
        )
        self._assert_gate(result, "SELF_REVIEWING_IMPLEMENTATION", facts)
        self.assertEqual(evidence.uncommitted_implementation_state(self.root, work_item), facts)

    def test_it_replaces_only_a_launching_selection(self) -> None:
        """A decision that launches nothing passes through unchanged: the
        plan-approval gate (CP3) keeps its precedence, and ``"1"``, which
        has no ``ExpectedOutcome`` row, stays declined."""
        self._state("IMPLEMENTING", {})
        _commit(self.root, "nothing complete")
        uncommitted = {"CP1": {"status": "COMPLETE"}}
        for phase in sorted(evidence.MILESTONE_IMPLEMENT_PHASES):
            with self.subTest(phase=phase, case="plan approval absent"):
                result, _ = self._decide(phase, uncommitted, plan_approval=None)
                self.assertIsNone(result.action)
                self.assertIn("entry validation (step 1a)", result.gate.what_is_required)
                self.assertEqual(result.evidence, ("plan_approval: absent",))
            with self.subTest(phase=phase, case='"1"'):
                result, _ = self._decide(phase, uncommitted, "1")
                self.assertTrue(result.declined)
                self.assertFalse(result.automatic)
                self.assertIsNone(result.gate)
                self.assertEqual(result.action.command, f"/milestone-implement {_WI}")

    def test_the_gated_phases_are_the_milestone_implement_phases(self) -> None:
        self.assertEqual(evidence.MILESTONE_IMPLEMENT_PHASES,
                         frozenset({"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"}))
        self.assertEqual(
            {phase for (phase, _version, token) in decision.AUTOMATIC_TRIPLES if token == "/milestone-implement"},
            evidence.MILESTONE_IMPLEMENT_PHASES,
        )


if __name__ == "__main__":
    unittest.main()
