"""Tests for ``controller.decision`` (CP4, ``REQ-T6``/``REQ-T7``).

Every test here is one the plan's own "Tests" list under "CP4 / CP4B --
Next-action decision engine and human-gate classification" tags ``(CP4)``
-- satisfiable without any of CP4B's evidence reads, per that section's own
framing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import decision, target_state
from controller.errors import NoSupportedActionError, UnknownPhaseError
from tests import fixtures

#: A hand-copied set of the twenty phase names, independent of
#: ``decision.KNOWN_PHASES`` itself -- the two-directional equality below
#: is only meaningful if this copy was typed independently.
_HAND_COPIED_TWENTY_PHASES = {
    "PLANNING",
    "SELF_REVIEWING_PLAN",
    "AWAITING_EXTERNAL_PLAN_REVIEW",
    "REVISING_PLAN",
    "IMPLEMENTING",
    "SELF_REVIEWING_IMPLEMENTATION",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK",
    "AWAITING_FUNCTIONAL_REVIEW",
    "FIXING_FUNCTIONAL_FINDINGS",
    "AWAITING_USER_ACCEPTANCE",
    "MILESTONE_COMPLETE",
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
    "AWAITING_TECHNICAL_APPROVAL",
    "LEGACY_READY",
    "AMENDING_PLAN",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
}

#: The four user-only command file stems, named explicitly rather than
#: derived, so the denylist test below has an independent expectation to
#: check the live derivation against.
_EXPECTED_USER_ONLY = {
    "approve-review", "accept-milestone", "recover-implementation-provenance",
    "request-plan-amendment",
}

_EXPECTED_SELECTED = {"milestone-plan", "review-plan", "record-manual-plan-review",
                       "apply-plan-review"}

_EXPECTED_NOT_SELECTED = {
    "review-implementation", "review-functional", "milestone-implement",
    "apply-implementation-review", "apply-functional-review", "prepare-functional-review",
    "record-manual-implementation-review", "bootstrap-workflow-v2", "prepare-review",
}


class KnownPhaseSetTest(unittest.TestCase):
    def test_two_directional_equality_against_hand_copied_twenty(self) -> None:
        self.assertEqual(decision.KNOWN_PHASES, _HAND_COPIED_TWENTY_PHASES)
        self.assertEqual(len(decision.KNOWN_PHASES), 20)

    def test_two_directional_equality_against_target_state_known_phases(self) -> None:
        # decision.py must not import target_state (dependency graph), so
        # this cross-check lives in the test, not the module.
        self.assertEqual(decision.KNOWN_PHASES, target_state.KNOWN_PHASES)


class TableDrivenPhaseDecisionTest(unittest.TestCase):
    """One case per known phase, asserting the expected action/automatic
    flag/reason-shape -- a total table, not a spot check."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def _decide(self, phase: str, **overrides):
        work_item = fixtures.build_work_item_view(phase=phase, **overrides)
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_every_known_phase_is_decided_without_raising_or_raises_no_supported_action(self) -> None:
        for phase in sorted(decision.KNOWN_PHASES):
            with self.subTest(phase=phase):
                if phase in decision.VOCABULARY_PHASES:
                    with self.assertRaises(NoSupportedActionError):
                        self._decide(phase)
                else:
                    result = self._decide(phase)
                    self.assertEqual(result.observed_phase, phase)
                    self.assertTrue(result.reason)

    def test_planning_is_automatic_for_both_governing_versions(self) -> None:
        for version in ("1", "2.1"):
            with self.subTest(governing_workflow_version=version):
                result = self._decide("PLANNING", governing_workflow_version=version)
                self.assertTrue(result.automatic)
                self.assertFalse(result.declined)
                self.assertIsNone(result.gate)
                self.assertEqual(result.action.command, "/milestone-plan wi-1")

    def test_revising_plan_is_automatic(self) -> None:
        result = self._decide("REVISING_PLAN")
        self.assertTrue(result.automatic)
        self.assertEqual(result.action.command, "/apply-plan-review wi-1")

    def test_legacy_ready_yields_no_action_no_gate_no_error(self) -> None:
        result = self._decide("LEGACY_READY")
        self.assertIsNone(result.action)
        self.assertIsNone(result.gate)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)

    def test_milestone_complete_yields_no_action_no_gate_no_error(self) -> None:
        result = self._decide("MILESTONE_COMPLETE")
        self.assertIsNone(result.action)
        self.assertIsNone(result.gate)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)

    def test_every_vocabulary_phase_raises_no_supported_action_error(self) -> None:
        for phase in sorted(decision.VOCABULARY_PHASES):
            with self.subTest(phase=phase):
                with self.assertRaises(NoSupportedActionError):
                    self._decide(phase)

    def test_unknown_phase_string_is_refused_defensively(self) -> None:
        work_item = fixtures.build_work_item_view(phase="SOME_FUTURE_PHASE")
        with self.assertRaises(NoSupportedActionError):
            decision.decide(self.managed_repo, snapshot=None, work_item=work_item)


class ScopeAssertionTest(unittest.TestCase):
    """The scope assertion restated against a subject that can fail (round
    12's B1): ``automatic=False`` at all eight report-only phases (revision
    64 widens this from five to eight), ``automatic=True`` at the automatic
    phases -- both directions."""

    REPORT_ONLY_EIGHT = {
        "IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION",
        "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK",
        "AWAITING_FUNCTIONAL_REVIEW", "AMENDING_PLAN",
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
    }

    AUTOMATIC_PHASES = {
        "PLANNING", "REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW",
        "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_EXTERNAL_PLAN_REVIEW",
    }

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def _decide(self, phase: str):
        work_item = fixtures.build_work_item_view(phase=phase)
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_report_only_eight_are_all_non_automatic(self) -> None:
        self.assertEqual(self.REPORT_ONLY_EIGHT, decision.REPORT_ONLY_PHASES)
        for phase in sorted(self.REPORT_ONLY_EIGHT):
            with self.subTest(phase=phase):
                self.assertFalse(self._decide(phase).automatic)

    def test_automatic_phases_are_all_automatic(self) -> None:
        for phase in sorted(self.AUTOMATIC_PHASES):
            with self.subTest(phase=phase):
                self.assertTrue(self._decide(phase).automatic)

    def test_no_automatic_phase_is_also_report_only(self) -> None:
        self.assertEqual(set(), self.AUTOMATIC_PHASES & self.REPORT_ONLY_EIGHT)


class TwoShapeAssertionTest(unittest.TestCase):
    """Round 10's B1, widened by revision 64: the four gate-bearing
    report-only phases yield ``gate=HumanGate(...)``, ``action=None``; the
    four declined phases (``IMPLEMENTING``, ``SELF_REVIEWING_IMPLEMENTATION``,
    ``AMENDING_PLAN``, ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW``) yield
    ``declined=True``, a populated ``action``, ``gate=None``. Neither shape
    may be reported as the other."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def _decide(self, phase: str):
        work_item = fixtures.build_work_item_view(phase=phase)
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_gate_bearing_phases_have_no_action_and_a_populated_gate(self) -> None:
        for phase in sorted(decision.GATE_REPORT_PHASES):
            with self.subTest(phase=phase):
                result = self._decide(phase)
                self.assertIsNone(result.action)
                self.assertIsNotNone(result.gate)
                self.assertFalse(result.automatic)
                self.assertFalse(result.declined)
                self.assertEqual(result.gate.phase, phase)

    def test_declined_phases_have_a_populated_action_and_no_gate(self) -> None:
        for phase in sorted(decision.DECLINED_PHASES):
            with self.subTest(phase=phase):
                result = self._decide(phase)
                self.assertIsNotNone(result.action)
                self.assertIsNone(result.gate)
                self.assertFalse(result.automatic)
                self.assertTrue(result.declined)

    def test_a_declined_phase_is_never_reported_as_a_gate(self) -> None:
        for phase in sorted(decision.DECLINED_PHASES):
            self.assertNotIn(phase, decision.GATE_REPORT_PHASES)

    def test_a_gate_phase_is_never_reported_as_declined(self) -> None:
        for phase in sorted(decision.GATE_REPORT_PHASES):
            self.assertNotIn(phase, decision.DECLINED_PHASES)


class UserOnlyDenylistTest(unittest.TestCase):
    """The four-command user-only denylist, derived as the union of the
    qualified-literal recogniser and the front-matter-flag recogniser,
    asserted two-directionally against a set computed fresh from the real
    seventeen files at test time."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.commands_dir = fixtures.copy_real_commands_dir(Path(self._tmp.name) / "commands")

    def test_derivation_matches_the_expected_four_exactly(self) -> None:
        derived = decision.derive_user_only_commands(self.commands_dir)
        self.assertEqual(derived, frozenset(_EXPECTED_USER_ONLY))

    def test_recover_implementation_provenance_named_explicitly(self) -> None:
        derived = decision.derive_user_only_commands(self.commands_dir)
        self.assertIn("recover-implementation-provenance", derived)

    def test_request_plan_amendment_named_explicitly(self) -> None:
        derived = decision.derive_user_only_commands(self.commands_dir)
        self.assertIn("request-plan-amendment", derived)

    def test_front_matter_flag_recogniser_alone_yields_three_and_omits_recover(self) -> None:
        """Pins round 9's own gap: deriving from
        ``disable-model-invocation: true`` alone misses
        ``recover-implementation-provenance.md``, which carries only the
        guard literal."""
        proxy_derived = {
            path.stem for path in self.commands_dir.glob("*.md")
            if decision.carries_disable_model_invocation_flag(path.read_text())
        }
        self.assertEqual(
            proxy_derived, {"approve-review", "accept-milestone", "request-plan-amendment"},
        )
        self.assertNotEqual(proxy_derived, _EXPECTED_USER_ONLY)
        self.assertNotIn("recover-implementation-provenance", proxy_derived)

    def test_guard_literal_recogniser_alone_yields_three_and_omits_request_plan_amendment(
        self,
    ) -> None:
        """Pins round 63's ``B6``: deriving from the qualified guard
        literal alone misses ``request-plan-amendment.md``, which declares
        itself user-only procedurally and in its front matter, never
        through a ``workflow_state.validate_...confirmation`` call."""
        guard_derived = {
            path.stem for path in self.commands_dir.glob("*.md")
            if decision.carries_user_confirmation_guard(path.read_text())
        }
        self.assertEqual(
            guard_derived,
            {"approve-review", "accept-milestone", "recover-implementation-provenance"},
        )
        self.assertNotEqual(guard_derived, _EXPECTED_USER_ONLY)
        self.assertNotIn("request-plan-amendment", guard_derived)

    def test_discriminating_false_negative_surface_form_is_still_counted(self) -> None:
        """A guard introduced in a surface form none of the live three use
        at all -- plain running prose, no bold span, no numbered-step
        heading -- calling a distinct ``workflow_state.validate_...
        confirmation`` name must still be counted."""
        fixtures.write_command_file(
            self.commands_dir, "synthetic-plain-prose",
            "This command quietly calls workflow_state.validate_synthetic_confirmation "
            "before doing anything else, in a sentence with no markdown structure at all.\n",
        )
        derived = decision.derive_user_only_commands(self.commands_dir)
        self.assertIn("synthetic-plain-prose", derived)

    def test_discriminating_false_positive_citation_is_not_counted(self) -> None:
        """A file whose only occurrence is a bare-identifier citation of
        another command's guard (``recover-implementation-provenance.md:79``'s
        own shape) carries no guard of its own and must not be counted --
        and is not one of the two static command-file sets either, so it
        must be added to one to stay classifiable."""
        text = (
            "This command's step 4 behaves identically in spirit to "
            "`/approve-review`'s own `validate_user_confirmation` guard, but never "
            "calls it.\n"
        )
        fixtures.write_command_file(self.commands_dir, "milestone-plan", text)
        # milestone-plan.md is overwritten in place (a SELECTED_COMMANDS
        # member), so classification still succeeds and the guard must not
        # have been detected from the bare citation.
        derived = decision.derive_user_only_commands(self.commands_dir)
        self.assertNotIn("milestone-plan", derived)


class CommandFilePartitionTest(unittest.TestCase):
    """The command-file partition property (revision 10, round 9's B1):
    enumerate every ``*.md`` under the real ``.claude/commands/`` and
    assert the partition into selected / deliberately-not-selected /
    user-only is total and disjoint."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.commands_dir = fixtures.copy_real_commands_dir(Path(self._tmp.name) / "commands")

    def test_partition_is_total_and_matches_expected_three_sets(self) -> None:
        classification = decision.classify_command_files(self.commands_dir)
        on_disk = {p.stem for p in self.commands_dir.glob("*.md")}
        self.assertEqual(set(classification), on_disk)
        self.assertEqual(len(on_disk), 17)

        selected = {stem for stem, cat in classification.items()
                    if cat == decision.CATEGORY_SELECTED}
        not_selected = {stem for stem, cat in classification.items()
                         if cat == decision.CATEGORY_DELIBERATELY_NOT_SELECTED}
        user_only = {stem for stem, cat in classification.items()
                     if cat == decision.CATEGORY_USER_ONLY}

        self.assertEqual(selected, _EXPECTED_SELECTED)
        self.assertEqual(not_selected, _EXPECTED_NOT_SELECTED)
        self.assertEqual(user_only, _EXPECTED_USER_ONLY)
        self.assertEqual(len(selected) + len(not_selected) + len(user_only), 17)
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(not_selected), 9)
        self.assertEqual(len(user_only), 4)
        # disjoint by construction: each stem appears in exactly one set
        self.assertEqual(selected & not_selected, set())
        self.assertEqual(selected & user_only, set())
        self.assertEqual(not_selected & user_only, set())

    def test_an_eighteenth_unclassifiable_command_file_fails_the_suite(self) -> None:
        fixtures.write_command_file(
            self.commands_dir, "some-new-command",
            "A brand-new command file the reference release never shipped, carrying "
            "no user-confirmation guard, no disable-model-invocation flag, and not "
            "named in either static set.\n",
        )
        with self.assertRaises(NoSupportedActionError):
            decision.classify_command_files(self.commands_dir)

    def test_front_matter_flag_alone_classifies_a_file_user_only(self) -> None:
        """A file that carries only the front-matter flag (never the guard
        literal) is still ``user_only`` -- pins recogniser 2's own half of
        the union, independent of recogniser 1."""
        fixtures.write_command_file(
            self.commands_dir, "synthetic-flag-only",
            "---\ndescription: synthetic\ndisable-model-invocation: true\n---\n\n"
            "This command declares itself user-only in its front matter alone.\n",
        )
        classification = decision.classify_command_files(self.commands_dir)
        self.assertEqual(classification["synthetic-flag-only"], decision.CATEGORY_USER_ONLY)

    def test_flag_mentioned_only_in_the_body_is_not_a_declaration(self) -> None:
        """A file whose body *discusses* the flag -- never its own front
        matter -- does not carry it (the location-based rule, not a
        semantic one)."""
        fixtures.write_command_file(
            self.commands_dir, "milestone-plan",
            "This command's prose discusses disable-model-invocation: true as a "
            "concept without ever declaring it in its own front matter.\n",
        )
        # milestone-plan.md is overwritten in place (a SELECTED_COMMANDS
        # member, with no front matter at all in this fixture text), so
        # classification still succeeds and the flag must not have been
        # detected from the body mention.
        classification = decision.classify_command_files(self.commands_dir)
        self.assertEqual(classification["milestone-plan"], decision.CATEGORY_SELECTED)


class DecideNeverReturnsUserOnlyCommandTest(unittest.TestCase):
    """``decide()`` never returns any of the four user-only commands as
    its ``action``, for any of the twenty phases -- a total assertion."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        self.commands_dir = fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def test_no_action_names_a_user_only_command_for_any_known_phase(self) -> None:
        user_only = decision.derive_user_only_commands(self.commands_dir)
        for phase in sorted(decision.KNOWN_PHASES):
            with self.subTest(phase=phase):
                work_item = fixtures.build_work_item_view(phase=phase)
                try:
                    result = decision.decide(self.managed_repo, snapshot=None, work_item=work_item)
                except NoSupportedActionError:
                    continue
                if result.action is not None:
                    stem = result.action.command.split()[0].lstrip("/")
                    self.assertNotIn(stem, user_only)


class HumanGateShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def test_awaiting_plan_approval_is_a_gate_naming_the_user_only_resume_command(self) -> None:
        work_item = fixtures.build_work_item_view(phase="AWAITING_PLAN_APPROVAL")
        result = decision.decide(self.managed_repo, snapshot=None, work_item=work_item)
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertIsNotNone(result.gate)
        self.assertEqual(result.gate.safe_resume_command, "/approve-review plan wi-1")
        self.assertEqual(result.gate.work_item_id, "wi-1")
        self.assertEqual(result.gate.repository, str(self.managed_repo.root))

    def test_awaiting_manual_external_implementation_review_is_a_gate(self) -> None:
        work_item = fixtures.build_work_item_view(
            phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            governing_workflow_version="2.2",
        )
        result = decision.decide(self.managed_repo, snapshot=None, work_item=work_item)
        self.assertIsNone(result.action)
        self.assertFalse(result.automatic)
        self.assertFalse(result.declined)
        self.assertIsNotNone(result.gate)
        self.assertEqual(
            result.gate.safe_resume_command, "/record-manual-implementation-review wi-1",
        )


class Revision64PhaseWideningTest(unittest.TestCase):
    """The three phases the 2.5.1 baseline widening adds (revision 64,
    round 63's ``B6``), each assigned by the declined-vs-gate criterion
    stated in "The `Decision` shape for a report-only phase"."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def _decide(self, phase: str, **overrides):
        work_item = fixtures.build_work_item_view(phase=phase, **overrides)
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_amending_plan_is_declined_naming_milestone_plan(self) -> None:
        result = self._decide("AMENDING_PLAN")
        self.assertTrue(result.declined)
        self.assertFalse(result.automatic)
        self.assertIsNone(result.gate)
        self.assertIsNotNone(result.action)
        self.assertEqual(result.action.command, "/milestone-plan wi-1")

    def test_awaiting_local_implementation_review_is_declined_naming_review_implementation(
        self,
    ) -> None:
        result = self._decide(
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", governing_workflow_version="2.2",
        )
        self.assertTrue(result.declined)
        self.assertFalse(result.automatic)
        self.assertIsNone(result.gate)
        self.assertIsNotNone(result.action)
        self.assertEqual(result.action.command, "/review-implementation wi-1")

    def test_awaiting_manual_external_implementation_review_is_a_gate_not_declined(self) -> None:
        result = self._decide(
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", governing_workflow_version="2.2",
        )
        self.assertFalse(result.declined)
        self.assertFalse(result.automatic)
        self.assertIsNotNone(result.gate)
        self.assertIsNone(result.action)

    def test_all_three_are_members_of_known_phases_and_report_only(self) -> None:
        for phase in (
            "AMENDING_PLAN", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
        ):
            with self.subTest(phase=phase):
                self.assertIn(phase, decision.KNOWN_PHASES)
                self.assertIn(phase, decision.REPORT_ONLY_PHASES)


class DecideNoWorkItemTest(unittest.TestCase):
    """``decide_no_work_item`` (revision 63, B2, ``REQ-40``): the distinct,
    unconditional pre-phase entry point for a ``NoWorkItemYet`` target."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)

    def test_returns_no_phase_and_bare_milestone_plan(self) -> None:
        result = decision.decide_no_work_item(self.managed_repo)
        self.assertIs(result.observed_phase, decision.NO_PHASE)
        self.assertEqual(result.action.command, "/milestone-plan")
        self.assertTrue(result.automatic)
        self.assertIsNone(result.gate)
        self.assertFalse(result.declined)
        self.assertTrue(result.reason)

    def test_no_phase_is_never_a_known_phase_and_never_none(self) -> None:
        self.assertNotIn(decision.NO_PHASE, decision.KNOWN_PHASES)
        self.assertIsNotNone(decision.NO_PHASE)

    def test_no_phase_wire_form_is_the_reserved_literal(self) -> None:
        self.assertEqual(decision.NO_PHASE_WIRE, "__NO_PHASE__")
        self.assertNotIn(decision.NO_PHASE_WIRE, decision.KNOWN_PHASES)

    def test_target_state_re_exports_the_same_canonical_sentinel(self) -> None:
        # target_state cannot define its own NO_PHASE -- decision sits
        # earlier in the dependency order, and CP6/CP7's `is NO_PHASE`
        # comparisons only work if there is exactly one instance.
        self.assertIs(target_state.NO_PHASE, decision.NO_PHASE)
        self.assertEqual(target_state.NO_PHASE_WIRE, decision.NO_PHASE_WIRE)


class PhaseWireRoundTripTest(unittest.TestCase):
    """``phase_to_wire``/``phase_from_wire`` (revision 64, "NO_PHASE's
    durable form"): the single writer/reader pair every one of
    ``pre_state.phase``, ``observed_phase_before`` and a job record's
    ``expected_transition.from`` is written and read through (CP6/CP7)."""

    def test_writer_maps_no_phase_to_the_reserved_literal(self) -> None:
        self.assertEqual(decision.phase_to_wire(decision.NO_PHASE), decision.NO_PHASE_WIRE)

    def test_writer_maps_every_real_phase_to_its_own_name(self) -> None:
        for phase in sorted(decision.KNOWN_PHASES):
            with self.subTest(phase=phase):
                self.assertEqual(decision.phase_to_wire(phase), phase)

    def test_reader_maps_the_reserved_literal_back_to_no_phase(self) -> None:
        self.assertIs(decision.phase_from_wire(decision.NO_PHASE_WIRE), decision.NO_PHASE)

    def test_reader_maps_every_real_phase_to_itself(self) -> None:
        for phase in sorted(decision.KNOWN_PHASES):
            with self.subTest(phase=phase):
                self.assertEqual(decision.phase_from_wire(phase), phase)

    def test_round_trip_is_the_identity_for_every_domain_member(self) -> None:
        self.assertIs(decision.phase_from_wire(decision.phase_to_wire(decision.NO_PHASE)), decision.NO_PHASE)
        for phase in sorted(decision.KNOWN_PHASES):
            with self.subTest(phase=phase):
                self.assertEqual(decision.phase_from_wire(decision.phase_to_wire(phase)), phase)

    def test_reader_refuses_null_bare_none_string_and_unrecognised_strings(self) -> None:
        # The total, fail-closed half of the round-trip rule: nothing
        # outside the domain is ever guessed at as "no phase" (revision
        # 64's own "why a reserved string rather than null").
        for bad in (None, "None", "not-a-phase", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(UnknownPhaseError):
                    decision.phase_from_wire(bad)


if __name__ == "__main__":
    unittest.main()
