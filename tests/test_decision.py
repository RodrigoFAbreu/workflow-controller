"""Tests for ``controller.decision`` (CP4, ``REQ-T6``/``REQ-T7``).

Every test here is one the plan's own "Tests" list under "CP4 / CP4B --
Next-action decision engine and human-gate classification" tags ``(CP4)``
-- satisfiable without any of CP4B's evidence reads, per that section's own
framing -- or, since ``workflow-controller-automatic-lifecycle-orchestration``
CP3, a test of the general automatic-dispatch rule (``AUTOMATIC_TRIPLES``,
``classify_selected_action``, the per-``(phase, version)`` expectation
tables that replaced the per-phase report-only/declined/gate sets).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import decision, target_state
from controller.managed_repo import VALIDATED_WORKFLOW_RELEASES
from controller.errors import MissingCommandsDirectoryError, NoSupportedActionError, UnknownPhaseError
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

#: The command files a selected action may be launched for
#: (automatic-lifecycle-orchestration CP3: the four plan-stage files plus the
#: four implementation-stage files rows 12-18 declare).
_EXPECTED_SELECTED = {
    "milestone-plan", "review-plan", "record-manual-plan-review", "apply-plan-review",
    "milestone-implement", "review-implementation", "apply-implementation-review",
    "record-manual-implementation-review",
}

_EXPECTED_NOT_SELECTED = {
    "review-functional", "apply-functional-review", "prepare-functional-review",
    "bootstrap-workflow-v2", "prepare-review",
}

#: Every version a work item can carry, plus ``None`` (no version at all).
_VERSIONS = ("1", "2.1", "2.2", None)

#: A plan approval ``/milestone-implement``'s step 1a accepts.
_CURRENT_PLAN_APPROVAL = {"status": "CURRENT"}

#: The command token ``decision.decide``'s own handler selects at each phase
#: that selects one (ordinary-case fixture, plan approval ``CURRENT``), stated
#: independently of the handlers so the expectation tables below have
#: something to check them against.
_SELECTED_TOKEN_BY_PHASE = {
    "PLANNING": "/milestone-plan",
    "REVISING_PLAN": "/apply-plan-review",
    "AWAITING_LOCAL_PLAN_REVIEW": "/review-plan",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": "/record-manual-plan-review",
    "AWAITING_EXTERNAL_PLAN_REVIEW": "/apply-plan-review",
    "AMENDING_PLAN": "/milestone-plan",
    "IMPLEMENTING": "/milestone-implement",
    "SELF_REVIEWING_IMPLEMENTATION": "/milestone-implement",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW": "/review-implementation",
}

#: The phases ``decision.decide`` reports as a gate at every version.
_GATE_PHASES = {
    "AWAITING_PLAN_APPROVAL", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK", "AWAITING_FUNCTIONAL_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
}

#: The phases with no action and no gate.
_NO_ACTION_PHASES = {"LEGACY_READY", "MILESTONE_COMPLETE"}

def _expected_automatic(phase: str, version: str | None) -> bool:
    """The rule, restated: a selected action launches iff its triple is
    declared. (CP3's interim ``_PHASES_AWAITING_EVIDENCE_HANDLER`` exception
    is gone since CP4B -- ``InterimSetRemovalTest``.)"""
    token = _SELECTED_TOKEN_BY_PHASE[phase]
    return (phase, version, token) in decision.AUTOMATIC_TRIPLES


class KnownPhaseSetTest(unittest.TestCase):
    def test_two_directional_equality_against_hand_copied_twenty(self) -> None:
        self.assertEqual(decision.KNOWN_PHASES, _HAND_COPIED_TWENTY_PHASES)
        self.assertEqual(len(decision.KNOWN_PHASES), 20)

    def test_target_state_re_exports_this_phase_list(self) -> None:
        # One phase list: target_state re-exports decision's, never a copy.
        self.assertIs(target_state.KNOWN_PHASES, decision.KNOWN_PHASES)
        self.assertIs(target_state.TERMINAL_PHASES, decision.TERMINAL_PHASES)


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
    12's B1), re-keyed by ``(phase, version)`` for the general
    automatic-dispatch rule (automatic-lifecycle-orchestration CP3): at
    every known phase and every version (``"1"``, ``"2.1"``, ``"2.2"``,
    ``None``), a selected action is automatic exactly when
    :data:`decision.AUTOMATIC_TRIPLES` declares it -- both directions."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def _decide(self, phase: str, version: str | None):
        work_item = fixtures.build_work_item_view(
            phase=phase, governing_workflow_version=version, plan_approval=_CURRENT_PLAN_APPROVAL,
        )
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_the_expectation_groups_partition_every_known_phase(self) -> None:
        groups = (
            set(_SELECTED_TOKEN_BY_PHASE), _GATE_PHASES, _NO_ACTION_PHASES,
            set(decision.VOCABULARY_PHASES),
        )
        self.assertEqual(set().union(*groups), set(decision.KNOWN_PHASES))
        self.assertEqual(sum(len(group) for group in groups), len(decision.KNOWN_PHASES))

    def test_every_phase_and_version_is_classified_by_automatic_triples_membership(self) -> None:
        for phase in sorted(_SELECTED_TOKEN_BY_PHASE):
            for version in _VERSIONS:
                with self.subTest(phase=phase, governing_workflow_version=version):
                    result = self._decide(phase, version)
                    expected = _expected_automatic(phase, version)
                    self.assertEqual(result.automatic, expected, result.reason)
                    self.assertEqual(result.declined, not expected)
                    self.assertIsNone(result.gate)
                    self.assertEqual(result.action.command, f"{_SELECTED_TOKEN_BY_PHASE[phase]} wi-1")

    def test_gate_and_no_action_phases_are_never_automatic_at_any_version(self) -> None:
        for phase in sorted(_GATE_PHASES | _NO_ACTION_PHASES):
            for version in _VERSIONS:
                with self.subTest(phase=phase, governing_workflow_version=version):
                    result = self._decide(phase, version)
                    self.assertFalse(result.automatic)
                    self.assertFalse(result.declined)
                    self.assertIsNone(result.action)
                    self.assertEqual(result.gate is not None, phase in _GATE_PHASES)

    def test_the_table_is_not_vacuous(self) -> None:
        """Spot checks of the table's own content, each one a combination
        the rule's effect table names."""
        for phase, version in (
            ("PLANNING", "1"), ("REVISING_PLAN", "2.2"), ("AWAITING_EXTERNAL_PLAN_REVIEW", "1"),
            ("IMPLEMENTING", "2.1"), ("SELF_REVIEWING_IMPLEMENTATION", "2.2"),
            # CP4: no longer interim -- its evidence gates run in
            # `controller.evidence` ahead of this (evidence-free) selection.
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.2"),
        ):
            with self.subTest(phase=phase, governing_workflow_version=version):
                self.assertTrue(self._decide(phase, version).automatic)
        for phase, version in (
            ("AWAITING_EXTERNAL_PLAN_REVIEW", "2.1"), ("REVISING_PLAN", "1"), ("PLANNING", None),
            ("IMPLEMENTING", "1"), ("AMENDING_PLAN", "2.2"),
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "2.1"),
        ):
            with self.subTest(phase=phase, governing_workflow_version=version):
                self.assertTrue(self._decide(phase, version).declined)


class TwoShapeAssertionTest(unittest.TestCase):
    """Round 10's B1, re-keyed by ``(phase, version)`` (CP3): at every
    combination a gate never carries an action and is never declined, a
    decline always carries an action and never a gate, and an automatic
    decision carries an action and no gate. Neither shape may be reported
    as the other."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def _decisions(self):
        for phase in sorted(decision.KNOWN_PHASES - decision.VOCABULARY_PHASES):
            for version in _VERSIONS:
                for plan_approval in (None, _CURRENT_PLAN_APPROVAL):
                    work_item = fixtures.build_work_item_view(
                        phase=phase, governing_workflow_version=version, plan_approval=plan_approval,
                    )
                    yield (phase, version, plan_approval is not None), decision.decide(
                        self.managed_repo, snapshot=None, work_item=work_item,
                    )

    def test_the_shape_invariant_holds_at_every_phase_and_version(self) -> None:
        declined, gated = set(), set()
        for key, result in self._decisions():
            with self.subTest(key=key):
                if result.gate is not None:
                    gated.add(key)
                    self.assertIsNone(result.action)
                    self.assertFalse(result.automatic)
                    self.assertFalse(result.declined)
                    self.assertEqual(result.gate.phase, key[0])
                elif result.declined:
                    declined.add(key)
                    self.assertIsNotNone(result.action)
                    self.assertFalse(result.automatic)
                elif result.automatic:
                    self.assertIsNotNone(result.action)
                else:
                    self.assertIsNone(result.action)
                    self.assertIn(key[0], _NO_ACTION_PHASES)
        self.assertTrue(declined)
        self.assertTrue(gated)
        self.assertEqual(declined & gated, set())


class ProtocolTwoTwoCompatibilityParityTest(unittest.TestCase):
    """CP4's own "1"/"2.1" parity half (``workflow-controller-protocol-2-
    2-compatibility``), rewritten for the general automatic-dispatch rule
    (automatic-lifecycle-orchestration CP3): version-independent
    classification no longer holds, by design. What holds instead is that
    each ``(phase, version)`` classification equals
    :data:`decision.AUTOMATIC_TRIPLES` membership -- a version with no
    declared row can never launch, and a declared row alone never turns a
    gate into a launch."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)
        fixtures.copy_real_commands_dir(root / ".claude" / "commands")

    def _decide(self, phase: str, version: str):
        work_item = fixtures.build_work_item_view(
            phase=phase, governing_workflow_version=version, plan_approval=_CURRENT_PLAN_APPROVAL,
        )
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_known_phases_unaffected_still_twenty_including_both_2_2_phases(self) -> None:
        self.assertEqual(len(decision.KNOWN_PHASES), 20)
        self.assertIn("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", decision.KNOWN_PHASES)
        self.assertIn("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", decision.KNOWN_PHASES)

    def test_selected_action_classification_equals_automatic_triples_membership(self) -> None:
        for phase in sorted(_SELECTED_TOKEN_BY_PHASE):
            for version in ("1", "2.1", "2.2"):
                with self.subTest(phase=phase, governing_workflow_version=version):
                    result = self._decide(phase, version)
                    classification = decision.classify_selected_action(
                        phase, version, result.action.command,
                    )
                    self.assertEqual(result.automatic, classification.automatic)
                    self.assertEqual(result.automatic, _expected_automatic(phase, version))

    def test_gate_classification_never_depends_on_triple_membership(self) -> None:
        """``APPLYING_REVIEW_FEEDBACK`` and
        ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` have ``"2.2"``
        triples, yet gate at every version: a triple is necessary for a
        launch, never sufficient."""
        for phase in sorted(_GATE_PHASES):
            for version in ("1", "2.1", "2.2"):
                with self.subTest(phase=phase, governing_workflow_version=version):
                    result = self._decide(phase, version)
                    self.assertIsNotNone(result.gate)
                    self.assertIsNone(result.action)
                    self.assertFalse(result.automatic)


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
        for release in sorted(VALIDATED_WORKFLOW_RELEASES):
            with self.subTest(release=release):
                commands_dir = fixtures.copy_real_commands_dir(
                    Path(self._tmp.name) / release / "commands", release=release,
                )
                derived = decision.derive_user_only_commands(commands_dir)
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
    user-only is total and disjoint -- for every admitted release."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.commands_dir = fixtures.copy_real_commands_dir(Path(self._tmp.name) / "commands")

    def test_partition_is_total_and_matches_expected_three_sets(self) -> None:
        for release in sorted(VALIDATED_WORKFLOW_RELEASES):
            with self.subTest(release=release):
                self._assert_partition(fixtures.copy_real_commands_dir(
                    Path(self._tmp.name) / release / "commands", release=release,
                ))

    def _assert_partition(self, commands_dir: Path) -> None:
        classification = decision.classify_command_files(commands_dir)
        on_disk = {p.stem for p in commands_dir.glob("*.md")}
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
        self.assertEqual(len(selected), 8)
        self.assertEqual(len(not_selected), 5)
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

    def test_missing_commands_directory_fails_closed(self) -> None:
        """`O1`, MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 1: a missing
        (or mis-pointed) ``commands_dir`` must refuse, not return ``{}`` --
        ``Path.glob`` on an absent directory is vacuously empty, which
        would otherwise be indistinguishable from a genuinely empty,
        existing one and weaker than this function's declared fail-closed
        contract."""
        missing = Path(self._tmp.name) / "does-not-exist"
        with self.assertRaises(MissingCommandsDirectoryError):
            decision.classify_command_files(missing)

    def test_commands_path_that_is_a_file_fails_closed(self) -> None:
        """The same refusal for a path that exists but is not a
        directory -- ``is_dir()`` is the exact check, not mere existence."""
        not_a_dir = Path(self._tmp.name) / "not-a-directory"
        not_a_dir.write_text("not a directory\n")
        with self.assertRaises(MissingCommandsDirectoryError):
            decision.classify_command_files(not_a_dir)

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

    def test_applying_review_feedback_names_apply_implementation_review_as_legal(self) -> None:
        """CP4's corrected gate, at every version: ``/apply-implementation-
        review`` skips its own entry transition when the phase is already
        ``APPLYING_REVIEW_FEEDBACK``, so it *is* legal from here -- the old
        "no Workflow command can legally run" text was wrong (regression)."""
        for version in ("1", "2.1", "2.2"):
            with self.subTest(governing_workflow_version=version):
                work_item = fixtures.build_work_item_view(
                    phase="APPLYING_REVIEW_FEEDBACK", governing_workflow_version=version,
                )
                result = decision.decide(self.managed_repo, snapshot=None, work_item=work_item)
                self.assertIsNone(result.action)
                self.assertFalse(result.automatic)
                self.assertFalse(result.declined)
                self.assertEqual(
                    result.gate.what_is_required,
                    "an implementation-review remediation is in progress or was interrupted; "
                    "/apply-implementation-review is legal from this phase (it skips its own entry "
                    "transition), so rerun it once the feedback on file is confirmed current",
                )
                self.assertEqual(result.gate.safe_resume_command, "/apply-implementation-review wi-1")
                for text in (result.gate.what_is_required, result.reason):
                    self.assertNotIn("no Workflow command can legally run", text)

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

    def test_awaiting_local_implementation_review_is_automatic_with_a_coherent_bundle_and_gated_without(
        self,
    ) -> None:
        """Was ``..._is_declined_naming_review_implementation`` (CP3's interim
        decline). Since CP4 the decision is ``evidence.decide``'s: automatic
        at ``"2.2"`` with a coherent implementation bundle, and the
        implementation-bundle coherence gate without one. The per-clause
        cases are ``tests/test_evidence.py``'s
        ``AwaitingLocalImplementationReviewTest``."""
        from controller import evidence

        root = self.managed_repo.root
        fixtures.build_target_git_repo(root)
        (root / "README.md").write_text("target fixture\n")
        fixtures.commit_all(root, "initial")
        work_item = fixtures.build_work_item_view(
            phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW", governing_workflow_version="2.2",
            implementation_revision=1, reviewed_implementation_head="1" * 40,
        )

        gated = evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)
        self.assertFalse(gated.automatic)
        self.assertFalse(gated.declined)
        self.assertIsNone(gated.action)
        self.assertIn("implementation bundle is not coherent", gated.evidence[0])
        # CP4B: the gate's final form answers an absent bundle with the
        # ordered recovery steps, never the bare generator.
        self.assertIn("before any implementation review runs, perform in order: 0. write",
                      gated.gate.what_is_required)
        self.assertIn("scripts/prepare-ai-review.sh", gated.gate.safe_resume_command)
        self.assertNotEqual(
            gated.gate.safe_resume_command,
            evidence._regeneration_command("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "wi-1"),
        )

        fixtures.write_implementation_bundle(root, "wi-1", 1, reviewed_implementation_head="1" * 40)
        result = evidence.decide(self.managed_repo, snapshot=None, work_item=work_item)
        self.assertTrue(result.automatic, result.reason)
        self.assertFalse(result.declined)
        self.assertIsNone(result.gate)
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
        """The ``KNOWN_PHASES`` half is unchanged. The report-only half
        under the general dispatch rule (CP3): ``AMENDING_PLAN`` is declined
        by the rule, because no row declares it at any version. The two
        implementation-review phases were members of the interim
        ``_PHASES_AWAITING_EVIDENCE_HANDLER`` until CP4, which installed
        their evidence handlers (and gates) in ``controller.evidence`` and
        took them out of the set: whether they launch is now the rule's
        call, behind those gates."""
        for phase in (
            "AMENDING_PLAN", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
        ):
            with self.subTest(phase=phase):
                self.assertIn(phase, decision.KNOWN_PHASES)
        for version in ("1", "2.1", "2.2"):
            with self.subTest(phase="AMENDING_PLAN", governing_workflow_version=version):
                self.assertNotIn(("AMENDING_PLAN", version, "/milestone-plan"), decision.AUTOMATIC_TRIPLES)
                result = self._decide("AMENDING_PLAN", governing_workflow_version=version)
                self.assertTrue(result.declined)
                self.assertEqual(
                    result.reason,
                    decision.uniform_decline_reason("AMENDING_PLAN", version, "/milestone-plan wi-1"),
                )
        from controller import evidence

        for phase in ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"):
            with self.subTest(phase=phase):
                self.assertIn(phase, evidence._EVIDENCE_HANDLERS)
                self.assertIn(phase, evidence.BUNDLE_BEARING_PHASES)
                self.assertIn(phase, evidence.IMPLEMENTATION_BUNDLE_CONSUMING_PHASES)
        # CP4B deleted the interim set altogether (`InterimSetRemovalTest`).
        self.assertFalse(hasattr(decision, "_PHASES_AWAITING_EVIDENCE_HANDLER"))


class AutomaticTriplesTest(unittest.TestCase):
    """CP3 item 2: :data:`decision.AUTOMATIC_TRIPLES` is held equal to the
    keys of ``controller.job.EXPECTED_OUTCOMES`` in both directions, so
    "has a declared, verifiable expected outcome" and "is launched" cannot
    drift apart, and every command it launches is a selected command
    file."""

    def test_equal_to_the_expected_outcome_keys_in_both_directions(self) -> None:
        from controller import job

        keys = {(eo.from_phase, eo.governing_version, eo.action) for eo in job.EXPECTED_OUTCOMES}
        self.assertEqual(set(decision.AUTOMATIC_TRIPLES) - keys, set())
        self.assertEqual(keys - set(decision.AUTOMATIC_TRIPLES), set())
        self.assertEqual(len(decision.AUTOMATIC_TRIPLES), len(job.EXPECTED_OUTCOMES))

    def test_command_stems_are_a_subset_of_selected_commands(self) -> None:
        stems = {token[1:] for (_phase, _version, token) in decision.AUTOMATIC_TRIPLES}
        self.assertLessEqual(stems, decision.SELECTED_COMMANDS)
        # Every triple holds the slash-prefixed token form, never a stem.
        for _phase, _version, token in decision.AUTOMATIC_TRIPLES:
            self.assertTrue(token.startswith("/") and " " not in token, token)

    def test_no_triple_names_a_user_only_command(self) -> None:
        with TemporaryDirectory() as tmp:
            user_only = decision.derive_user_only_commands(
                fixtures.copy_real_commands_dir(Path(tmp) / "commands"),
            )
        for _phase, _version, token in decision.AUTOMATIC_TRIPLES:
            self.assertNotIn(token[1:], user_only)

    def test_command_token_is_the_first_word(self) -> None:
        self.assertEqual(decision.command_token("/milestone-plan wi-1"), "/milestone-plan")
        self.assertEqual(decision.command_token("/milestone-plan"), "/milestone-plan")


class DispatchRuleTest(unittest.TestCase):
    """CP3's own tests of the rule itself: it, not the handler, launches."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)

    def _decide(self, phase: str, version: str | None, **overrides):
        work_item = fixtures.build_work_item_view(
            phase=phase, governing_workflow_version=version, **overrides,
        )
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_removing_a_triple_turns_the_same_decision_into_a_decline(self) -> None:
        before = self._decide("PLANNING", "2.1")
        self.assertTrue(before.automatic)
        reduced = decision.AUTOMATIC_TRIPLES - {("PLANNING", "2.1", "/milestone-plan")}
        with mock.patch.object(decision, "AUTOMATIC_TRIPLES", reduced):
            after = self._decide("PLANNING", "2.1")
        self.assertFalse(after.automatic)
        self.assertTrue(after.declined)
        self.assertIsNone(after.gate)
        self.assertEqual(after.action, before.action)
        self.assertEqual(after.observed_phase, before.observed_phase)
        self.assertEqual(
            after.reason, decision.uniform_decline_reason("PLANNING", "2.1", "/milestone-plan wi-1"),
        )

    def test_removing_the_bootstrap_triple_declines_the_bootstrap_too(self) -> None:
        self.assertTrue(decision.decide_no_work_item(self.managed_repo).automatic)
        reduced = decision.AUTOMATIC_TRIPLES - {(decision.NO_PHASE, None, "/milestone-plan")}
        with mock.patch.object(decision, "AUTOMATIC_TRIPLES", reduced):
            result = decision.decide_no_work_item(self.managed_repo)
        self.assertTrue(result.declined)
        self.assertFalse(result.automatic)

    def test_the_former_interim_phases_classify_by_the_rule_alone(self) -> None:
        """CP3's three interim phases, after CP4B: each implementation-review
        triple is automatic at ``"2.2"`` and declined, with the uniform
        reason, at ``"2.1"`` -- the rule alone decides, behind the evidence
        gates ``controller.evidence`` installs ahead of it."""
        for phase, token in (
            ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "/review-implementation"),
            ("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "/record-manual-implementation-review"),
            ("APPLYING_REVIEW_FEEDBACK", "/apply-implementation-review"),
        ):
            with self.subTest(phase=phase):
                self.assertIn((phase, "2.2", token), decision.AUTOMATIC_TRIPLES)
                classification = decision.classify_selected_action(phase, "2.2", f"{token} wi-1")
                self.assertTrue(classification.automatic)
                self.assertIsNone(classification.decline_reason)
                declined = decision.classify_selected_action(phase, "2.1", f"{token} wi-1")
                self.assertFalse(declined.automatic)
                self.assertEqual(
                    declined.decline_reason, decision.uniform_decline_reason(phase, "2.1", f"{token} wi-1"),
                )

    def test_the_uniform_reason_names_the_phase_version_and_command(self) -> None:
        reason = decision.uniform_decline_reason("AMENDING_PLAN", "2.2", "/milestone-plan wi-1")
        self.assertEqual(
            reason,
            "AMENDING_PLAN selects /milestone-plan wi-1, which is model-invocable, but no "
            'verifiable ExpectedOutcome is declared for (AMENDING_PLAN, "2.2", /milestone-plan), '
            "so this Controller reports it instead of launching it",
        )
        self.assertIn("(PLANNING, None, /milestone-plan)",
                      decision.uniform_decline_reason("PLANNING", None, "/milestone-plan wi-1"))

    def test_a_gate_or_no_action_decision_passes_through_the_rule_unchanged(self) -> None:
        for phase in ("AWAITING_PLAN_APPROVAL", "LEGACY_READY", "AMENDING_PLAN"):
            with self.subTest(phase=phase):
                result = self._decide(phase, "2.2")
                self.assertIs(decision.apply_dispatch_rule(result, "2.2"), result)


class ImplementingDispatchTest(unittest.TestCase):
    """CP3 item 3: ``IMPLEMENTING``/``SELF_REVIEWING_IMPLEMENTATION`` select
    ``/milestone-implement``, gating first on a plan approval step 1a
    would refuse."""

    PHASES = ("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION")

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "target"
        self.managed_repo = fixtures.build_target_managed_repository(root)

    def _decide(self, phase: str, version: str | None, plan_approval):
        work_item = fixtures.build_work_item_view(
            phase=phase, governing_workflow_version=version, plan_approval=plan_approval,
        )
        return decision.decide(self.managed_repo, snapshot=None, work_item=work_item)

    def test_2_1_and_2_2_with_a_current_approval_are_automatic(self) -> None:
        for phase in self.PHASES:
            for version in ("2.1", "2.2"):
                with self.subTest(phase=phase, governing_workflow_version=version):
                    result = self._decide(
                        phase, version, {"status": "CURRENT", "basis": "EXTERNAL_APPROVE"},
                    )
                    self.assertTrue(result.automatic, result.reason)
                    self.assertFalse(result.declined)
                    self.assertIsNone(result.gate)
                    self.assertEqual(result.action.command, "/milestone-implement wi-1")

    def test_version_1_is_declined_with_the_uniform_reason(self) -> None:
        for phase in self.PHASES:
            for plan_approval in (None, _CURRENT_PLAN_APPROVAL):
                with self.subTest(phase=phase, plan_approval=plan_approval):
                    result = self._decide(phase, "1", plan_approval)
                    self.assertTrue(result.declined)
                    self.assertFalse(result.automatic)
                    self.assertIsNone(result.gate)
                    self.assertEqual(result.action.command, "/milestone-implement wi-1")
                    self.assertEqual(
                        result.reason,
                        decision.uniform_decline_reason(phase, "1", "/milestone-implement wi-1"),
                    )

    def test_a_stale_or_absent_plan_approval_gates(self) -> None:
        for phase in self.PHASES:
            for version in ("2.1", "2.2"):
                for plan_approval in (
                    None, {"status": "STALE"}, {}, {"status": "current"}, ["CURRENT"], "CURRENT",
                ):
                    with self.subTest(phase=phase, governing_workflow_version=version,
                                      plan_approval=plan_approval):
                        result = self._decide(phase, version, plan_approval)
                        self.assertIsNone(result.action)
                        self.assertFalse(result.automatic)
                        self.assertFalse(result.declined)
                        gate = result.gate
                        self.assertIsNotNone(gate)
                        self.assertEqual(gate.phase, phase)
                        self.assertEqual(
                            gate.what_is_required,
                            "/milestone-implement's entry validation (step 1a) refuses on a "
                            "missing or stale plan approval",
                        )
                        self.assertEqual(gate.safe_resume_command,
                                         "workflow-controller explain --work-item wi-1")
                        self.assertTrue(result.evidence[0].startswith("plan_approval: "))

    def test_the_gate_never_names_a_user_only_command(self) -> None:
        with TemporaryDirectory() as tmp:
            user_only = decision.derive_user_only_commands(
                fixtures.copy_real_commands_dir(Path(tmp) / "commands"),
            )
        result = self._decide("IMPLEMENTING", "2.2", None)
        for stem in user_only:
            self.assertNotIn(stem, result.gate.safe_resume_command)


class PerPhaseSetRemovalTest(unittest.TestCase):
    """CP3 item 5: Generation 1's per-phase sets are gone, and no module
    references them, so no behaviour can depend on them."""

    NAMES = ("REPORT_ONLY_PHASES", "DECLINED_PHASES", "GATE_REPORT_PHASES", "_DECLINED_COMMAND_BY_PHASE")

    def test_decision_no_longer_defines_them(self) -> None:
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertFalse(hasattr(decision, name))

    def test_no_module_outside_decision_references_them(self) -> None:
        package = Path(decision.__file__).resolve().parent
        pattern = re.compile(r"\b(" + "|".join(self.NAMES) + r")\b")
        offenders = [
            str(path.relative_to(package.parent))
            for path in sorted(package.glob("*.py"))
            if path.name != "decision.py" and pattern.search(path.read_text())
        ]
        self.assertEqual(offenders, [])


class InterimSetRemovalTest(unittest.TestCase):
    """CP4B: CP3's interim ``_PHASES_AWAITING_EVIDENCE_HANDLER`` (and its
    own ``interim_decline_reason``) no longer exist, and no ``controller/``
    module references either, so no behaviour can depend on them."""

    NAMES = ("_PHASES_AWAITING_EVIDENCE_HANDLER", "interim_decline_reason")

    def test_decision_no_longer_defines_them(self) -> None:
        for name in self.NAMES:
            with self.subTest(name=name):
                self.assertFalse(hasattr(decision, name))

    def test_no_controller_module_references_them(self) -> None:
        package = Path(decision.__file__).resolve().parent
        pattern = re.compile(r"\b(" + "|".join(self.NAMES) + r")\b")
        offenders = [
            str(path.relative_to(package.parent))
            for path in sorted(package.glob("*.py")) if pattern.search(path.read_text())
        ]
        self.assertEqual(offenders, [])


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

    def test_an_explicit_base_names_the_one_argument_form(self) -> None:
        """Design F: the trunk tip a passed trunk start proved; the triple is
        still the bootstrap's, so the selection stays automatic."""
        tip = "0123456789abcdef" * 2 + "01234567"
        result = decision.decide_no_work_item(self.managed_repo, base=tip)
        self.assertIs(result.observed_phase, decision.NO_PHASE)
        self.assertEqual(result.action.command, f"/milestone-plan {tip}")
        self.assertEqual(decision.command_token(result.action.command), "/milestone-plan")
        self.assertTrue(result.automatic)
        self.assertIn(f"based on the trunk tip {tip}", result.reason)
        self.assertEqual(decision.decide_no_work_item(self.managed_repo, base=None),
                         decision.decide_no_work_item(self.managed_repo))

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



class NoMilestonePlanAtPlanReviewReadyPhaseTest(unittest.TestCase):
    """Invariant I4 (``workflow-controller-workflow-2-6-integration``, CP4):
    under Workflow 2.6.0, ``/milestone-plan <id>`` at a plan-review-ready
    phase withdraws the item and discards both recorded review stages, so
    no automatic triple selects it there, no decision path does, and no
    gate names it as its ``safe_resume_command``."""

    READY_PHASES = frozenset({
        "AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_PLAN_APPROVAL",
    })

    def test_the_ready_phases_are_workflow_2_6_0_s(self) -> None:
        from controller import evidence

        self.assertEqual(evidence.PLAN_REVIEW_READY_PHASES, self.READY_PHASES)
        self.assertEqual(
            fixtures.evaluate_in_workflow_release("2.6.0", "sorted(workflow_state.PLAN_REVIEW_READY_PHASES)"),
            sorted(self.READY_PHASES))
        self.assertEqual(
            fixtures.evaluate_in_workflow_release("2.6.0", "sorted(workflow_state.PLAN_REVIEW_NON_READY_PHASES)"),
            sorted(evidence.PLAN_REVIEW_NON_READY_PHASES))

    def test_no_automatic_triple_runs_milestone_plan_at_a_ready_phase(self) -> None:
        self.assertEqual([triple for triple in decision.AUTOMATIC_TRIPLES
                          if triple[0] in self.READY_PHASES and triple[2] == "/milestone-plan"], [])

    def test_the_phase_table_never_selects_it_at_a_ready_phase(self) -> None:
        for phase in sorted(self.READY_PHASES):
            for version in ("1", "2.1", "2.2"):
                with self.subTest(phase=phase, version=version):
                    work_item = fixtures.build_work_item_view(phase=phase, governing_workflow_version=version)
                    self._assert_not_milestone_plan(decision.decide(_RootOnly(), None, work_item))

    def test_no_decision_at_a_ready_phase_dispatches_or_advertises_it(self) -> None:
        """The decide sweep: every plan-stage golden scenario, at every ready
        phase and version a Workflow writer reaches, under both contracts --
        under 2.6.0 with every publication-status class and feedback layout
        the golden replays."""
        from tests.golden import generate_plan_stage_decisions as golden

        swept = 0
        for release in ("2.5.1", "2.6.0"):
            for key, body in golden.derive_cases(release).items():
                phase = key.split(" | ")[1]
                if phase not in self.READY_PHASES:
                    continue
                swept += 1
                with self.subTest(release=release, case=key):
                    self.assertNotIn("raises", body)
                    self.assertFalse((body["action_command"] or "").startswith("/milestone-plan"), body)
                    if body["gate"] is not None:
                        self.assertNotIn("/milestone-plan", body["gate"]["safe_resume_command"])
        self.assertGreater(swept, 1000)

    def _assert_not_milestone_plan(self, selected) -> None:
        if selected.action is not None:
            self.assertFalse(selected.action.command.startswith("/milestone-plan"), selected)
        if selected.gate is not None:
            self.assertNotIn("/milestone-plan", selected.gate.safe_resume_command)


class _RootOnly:
    """The one attribute ``decision.decide`` reads off a managed repository."""

    root = Path("/nonexistent-target")


if __name__ == "__main__":
    unittest.main()
