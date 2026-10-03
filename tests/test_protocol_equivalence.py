"""The equivalence comparison of plan C.7
(workflow-controller-orchestration-protocol-v1 CP3): the Workflow's own
``next-action`` against 1.6.0's ``evidence.decide`` on the same fixture
repositories, every difference one of the seven observable ones (D1-D7)
asserted by name, and the checked-in table
``tests/golden/protocol_vs_legacy_differences.json`` holding exactly those.

The non-observable notes N1-N4 are not rows of the table; each is tested where
it lives (:class:`NonObservableNotesTest`, and CP5 for N2).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import protocol_decision  # noqa: E402
from tests.golden import generate_protocol_vs_legacy_differences as generator  # noqa: E402

#: What each named difference is, as measured: the legacy and protocol kinds,
#: the protocol's command (and row) when it launches. Asserted per scenario.
EXPECTED_SHAPE = {
    "D1": {"legacy": ("launch", "/apply-plan-review"), "protocol": ("launch", "/milestone-plan"), "rows": {"9"}},
    "D2": {"legacy": ("launch", "/milestone-plan"), "protocol": ("non-launch", None), "rows": {"6a"}},
    "D3": {"legacy": ("non-launch", None), "protocol": ("launch", "/milestone-plan"), "rows": {"7"}},
    "D4": {"legacy": ("non-launch", None), "protocol": ("launch", "/apply-implementation-review"), "rows": {"36"}},
    "D5": {"legacy": ("non-launch", None), "protocol": ("launch", "/apply-implementation-review"),
           "rows": {"31", "32"}},
    "D6": {"legacy": ("non-launch", None), "protocol": ("launch", "/prepare-functional-review"), "rows": {"37"}},
    "D7": {"legacy": ("non-launch", None), "protocol": ("launch", "/apply-functional-review"), "rows": {"38"}},
}


class EquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = generator.derive()
        cls.scenarios = cls.document["scenarios"]

    def test_every_difference_is_one_of_the_seven_and_named(self) -> None:
        for scenario_id, measured in self.scenarios.items():
            with self.subTest(scenario=scenario_id):
                expected = measured["expected_difference"]
                self.assertEqual(measured["differs"], expected is not None,
                                 f"{scenario_id}: legacy {measured['legacy']}, protocol {measured['protocol']}")

    def test_each_named_difference_has_the_measured_shape_of_its_class(self) -> None:
        for scenario_id, measured in self.scenarios.items():
            expected = measured["expected_difference"]
            if expected is None:
                continue
            with self.subTest(scenario=scenario_id, difference=expected):
                shape = EXPECTED_SHAPE[expected]
                self.assertEqual((measured["legacy"]["kind"], measured["legacy"]["command"]), shape["legacy"])
                self.assertEqual((measured["protocol"]["kind"], measured["protocol"]["command"]), shape["protocol"])
                self.assertIn(measured["protocol"]["row"], shape["rows"])

    def test_the_table_holds_exactly_the_seven_differences_each_with_a_scenario(self) -> None:
        differences = self.document["differences"]
        self.assertEqual(list(differences), ["D1", "D2", "D3", "D4", "D5", "D6", "D7"])
        for key, entry in differences.items():
            with self.subTest(difference=key):
                self.assertTrue(entry["scenarios"], f"{key} was never measured")
                self.assertEqual(entry["class"], generator.DIFFERENCES[key])
        measured = {sid for sid, m in self.scenarios.items() if m["differs"]}
        named = {sid for entry in differences.values() for sid in entry["scenarios"]}
        self.assertEqual(measured, named)

    def test_the_three_new_automatic_launches_are_in_the_table(self) -> None:
        launched = {m["protocol"]["action_id"] for m in self.scenarios.values() if m["differs"]
                    and m["legacy"]["kind"] == "non-launch"}
        self.assertLessEqual({"implementation.apply_review", "functional.prepare", "functional.apply_findings"},
                             launched)

    def test_requisite_scenarios_by_name(self) -> None:
        by = self.scenarios
        # D1: REVISING_PLAN with no feedback goes from /apply-plan-review to /milestone-plan.
        for scenario_id in ("revising_no_feedback_2.2", "revising_no_feedback_2.1"):
            self.assertEqual((by[scenario_id]["legacy"]["command"], by[scenario_id]["protocol"]["command"]),
                             ("/apply-plan-review", "/milestone-plan"))
        # A REVISE feedback that applies is not a difference: both apply it.
        applied = by["revising_with_revise_feedback_2.2"]
        self.assertEqual((applied["legacy"]["command"], applied["protocol"]["command"]),
                         ("/apply-plan-review", "/apply-plan-review"))
        # D3, D5, D6, D7 and D4, each by the family it names.
        self.assertEqual(by["amending_2.2"]["expected_difference"], "D3")
        self.assertEqual(by["external_review_current_block_2.1"]["protocol"]["row"], "31")
        self.assertEqual(by["functional_no_checklist_2.2"]["protocol"]["action_id"], "functional.prepare")
        self.assertEqual(by["functional_unconsumed_findings_2.2"]["protocol"]["action_id"], "functional.apply_findings")
        self.assertEqual(by["applying_review_feedback_2.1"]["protocol"]["action_id"], "implementation.apply_review")

    def test_a_non_launch_on_both_sides_is_never_a_difference(self) -> None:
        for scenario_id in ("plan_approval_2.2", "plan_approval_v1", "implementing_v1", "revising_v1",
                            "functional_checklist_current_2.2", "external_review_current_approve_v1",
                            "manual_external_review_2.2"):
            with self.subTest(scenario=scenario_id):
                self.assertEqual((self.scenarios[scenario_id]["legacy"]["kind"],
                                  self.scenarios[scenario_id]["protocol"]["kind"]), ("non-launch", "non-launch"))
                self.assertFalse(self.scenarios[scenario_id]["differs"])

    def test_no_decision_launches_a_user_only_command_on_either_side(self) -> None:
        for scenario_id, measured in self.scenarios.items():
            with self.subTest(scenario=scenario_id):
                self.assertFalse(measured["legacy"]["launches_user_only"])
                self.assertFalse(measured["protocol"]["launches_user_only"])

    def test_every_launch_command_has_a_protocol_action(self) -> None:
        commands = {f"/{entry.command}" for entry in protocol_decision.PROTOCOL_ACTIONS.values()}
        for scenario_id, measured in self.scenarios.items():
            if measured["protocol"]["kind"] == "launch":
                with self.subTest(scenario=scenario_id):
                    self.assertIn(measured["protocol"]["command"], commands)

    def test_the_scope_names_the_functional_review_and_external_review_families(self) -> None:
        families = {m["family"] for m in self.scenarios.values()}
        self.assertLessEqual({"plan", "functional", "external_implementation_review",
                              "external_implementation_review_golden", "implementation"}, families)
        functional = {sid for sid, m in self.scenarios.items() if m["family"] == "functional"}
        self.assertEqual(functional, {"functional_no_checklist_2.2", "functional_checklist_current_2.2",
                                      "functional_unconsumed_findings_2.2"})

    def test_the_golden_sweep_covers_every_reproducible_case_at_both_versions(self) -> None:
        from tests.golden import generate_external_implementation_review_decisions as external
        golden_ids = {sid for sid, _setup, _overrides in external.SCENARIOS}
        swept = {sid.removeprefix("golden:").split("|")[0] for sid in self.scenarios if sid.startswith("golden:")}
        self.assertEqual(golden_ids - set(generator.EXCLUDED_EXTERNAL_CASES), swept)
        for scenario_id in self.scenarios:
            if scenario_id.startswith("golden:"):
                self.assertFalse(self.scenarios[scenario_id]["differs"], scenario_id)
        self.assertEqual({sid.split("|")[1] for sid in self.scenarios if sid.startswith("golden:")}, {"1", "2.1"})

    def test_the_excluded_golden_cases_are_refused_by_the_workflow_for_real(self) -> None:
        for case in generator.EXCLUDED_EXTERNAL_CASES:
            with self.subTest(case=case):
                self.assertEqual(generator.refusal_of_excluded_case(case), "state_invalid")

    def test_the_checked_in_table_is_what_the_comparison_measures(self) -> None:
        self.assertEqual(generator.GOLDEN_PATH.read_text(), json.dumps(self.document, sort_keys=True, indent=2) + "\n")


class NonObservableNotesTest(unittest.TestCase):
    """N1 and N4: a blocked decision's gate carries the Workflow's reason,
    remedy and alternatives verbatim, where 1.6.0 gave its own text."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        from types import SimpleNamespace
        from controller import protocol, target_state
        from tests import fixtures
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.gates = {}
        for name, phase, version, fields in (
            ("n1_missing_bundle", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "1", {"implementation_revision": 1}),
            ("n4_plan_approval_v1", "AWAITING_PLAN_APPROVAL", "1", {}),
        ):
            directory = Path(cls._tmp.name) / name
            directory.mkdir()
            root = generator.build(str(directory), phase, version, layout=None, **fields)
            fixtures.install_workflow_release(root, "2.7.0")
            managed = fixtures.build_target_managed_repository(root)
            item = target_state.select_work_item(target_state.read(managed), work_item_id=generator.WID)
            stub = SimpleNamespace(root=root, target_protocol=generator.TARGET_PROTOCOL, script_digests=fixtures.admitted_script_digests(root),
                                   workflow_version="2.7.0")
            cls.gates[name] = (protocol_decision.decide(stub, item), protocol.next_action(root, generator.WID))

    def test_n1_a_missing_bundle_is_a_blocked_gate_with_the_workflows_reason_and_remedy(self) -> None:
        got, answer = self.gates["n1_missing_bundle"]
        self.assertFalse(got.automatic)
        self.assertIsNone(got.action)
        self.assertEqual(answer.disposition, "blocked")
        text = got.gate.what_is_required
        self.assertIn(answer.reason.code, text)
        self.assertIn(answer.reason.text, text)
        self.assertIn(answer.reason.remedy, text)

    def test_n4_the_plan_approval_gate_at_version_1_is_blocked_with_the_catalogue_reason(self) -> None:
        got, answer = self.gates["n4_plan_approval_v1"]
        self.assertFalse(got.automatic)
        self.assertEqual((answer.row, answer.disposition), ("4", "blocked"))
        self.assertIn("phase_not_legal_for_governing_version", got.gate.what_is_required)


if __name__ == "__main__":
    unittest.main()
