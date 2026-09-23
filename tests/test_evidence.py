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


if __name__ == "__main__":
    unittest.main()
