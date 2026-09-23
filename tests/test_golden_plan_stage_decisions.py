"""The plan-stage decision golden (``docs/ai-workflow/
CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md``, CP1's first act,
asserted again by CP3 item 1).

``tests/golden/plan_stage_decisions.json`` was generated before any other
change of that milestone, from the base commit's unchanged
``controller/decision.py``/``controller/evidence.py``, by
``tests/golden/generate_plan_stage_decisions.py``. This test re-derives
every decision from the code as it stands, normalises it with the
generator's own :func:`normalise`, and requires the result to be
byte-equal to the checked-in file -- so no later change can move a
plan-stage decision without this test failing.

**The single permitted difference** (CP3 item 1) is ``reason`` on the
declined ``AMENDING_PLAN`` decisions: the general automatic-dispatch rule's
uniform decline wording replaces Generation 1's "revision 10's scope"
wording there. :func:`_revert_permitted_difference` names that exception
explicitly -- it accepts the difference only where the golden holds the
base wording and the re-derivation holds exactly the uniform reason, with
every other field unchanged -- and nothing else may differ.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import decision  # noqa: E402
from tests.golden import generate_plan_stage_decisions as golden  # noqa: E402

#: The one phase whose golden decisions may differ, and only in ``reason``.
_PERMITTED_DIFFERENCE_PHASE = "AMENDING_PLAN"

#: The base commit's decline wording for that phase, as the golden holds it.
_BASE_AMENDING_PLAN_REASON = (
    "AMENDING_PLAN is automation-safe -- /milestone-plan wi-1 is model-invocable and on "
    "neither denylist -- but Generation 1's own scope (revision 10) reports it rather than "
    "launching it"
)


def _revert_permitted_difference(
    expected: dict[str, dict], derived: dict[str, dict],
) -> tuple[dict[str, dict], list[str]]:
    """``derived`` with the permitted difference reverted to the golden's
    own value, plus the keys it was reverted at. A case is reverted only
    when it is at :data:`_PERMITTED_DIFFERENCE_PHASE`, declined on both
    sides, identical except for ``reason``, and its two reasons are
    exactly the base wording and the uniform decline reason for that
    ``(phase, version)``; any other difference is left in place, so the
    comparisons below fail on it."""
    reverted = dict(derived)
    keys: list[str] = []
    for key, body in derived.items():
        _scenario, phase, version = key.split(" | ")
        golden_body = expected.get(key)
        if phase != _PERMITTED_DIFFERENCE_PHASE or golden_body is None or golden_body == body:
            continue
        others_equal = {k: v for k, v in body.items() if k != "reason"} == {
            k: v for k, v in golden_body.items() if k != "reason"
        }
        if (
            others_equal and body["declined"] and golden_body["declined"]
            and golden_body["reason"] == _BASE_AMENDING_PLAN_REASON
            and body["reason"] == decision.uniform_decline_reason(phase, version, body["action_command"])
        ):
            reverted[key] = golden_body
            keys.append(key)
    return reverted, keys


#: CP3 item 1's coverage list, restated independently of the generator so
#: a narrowed ``PHASE_VERSIONS`` cannot silently shrink the golden.
_EXPECTED_PHASE_VERSIONS = {
    ("PLANNING", "1"), ("PLANNING", "2.1"), ("PLANNING", "2.2"),
    ("AWAITING_PLAN_APPROVAL", "1"), ("AWAITING_PLAN_APPROVAL", "2.1"),
    ("AWAITING_PLAN_APPROVAL", "2.2"),
    ("AMENDING_PLAN", "1"), ("AMENDING_PLAN", "2.1"), ("AMENDING_PLAN", "2.2"),
    ("REVISING_PLAN", "2.1"), ("REVISING_PLAN", "2.2"),
    ("AWAITING_LOCAL_PLAN_REVIEW", "2.1"), ("AWAITING_LOCAL_PLAN_REVIEW", "2.2"),
    ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.1"),
    ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "2.2"),
    ("AWAITING_EXTERNAL_PLAN_REVIEW", "1"),
}


class PlanStageDecisionGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checked_in = golden.GOLDEN_PATH.read_text()
        cls.raw_derived_cases = golden.derive_cases()
        cls.derived_cases, cls.permitted_keys = _revert_permitted_difference(
            golden.load_cases(cls.checked_in), cls.raw_derived_cases,
        )
        cls.derived_text = golden.render_document(cls.derived_cases)

    def test_the_permitted_difference_is_exactly_every_amending_plan_case(self) -> None:
        """The exception is neither vacuous nor wider than named: every
        ``AMENDING_PLAN`` case now carries the uniform reason, and no other
        case was reverted."""
        expected = golden.load_cases(self.checked_in)
        amending = sorted(key for key in expected if key.split(" | ")[1] == _PERMITTED_DIFFERENCE_PHASE)
        self.assertTrue(amending)
        self.assertEqual(sorted(self.permitted_keys), amending)
        for key in amending:
            _scenario, phase, version = key.split(" | ")
            self.assertEqual(
                self.raw_derived_cases[key]["reason"],
                decision.uniform_decline_reason(phase, version, "/milestone-plan wi-1"),
            )

    def test_every_case_reproduces_from_the_code_as_it_stands(self) -> None:
        """Per-case comparison first, so a failure names the decisions that
        moved rather than only reporting two large texts as unequal."""
        expected = golden.load_cases(self.checked_in)
        self.assertEqual(sorted(expected), sorted(self.derived_cases))
        moved = [key for key in sorted(expected) if expected[key] != self.derived_cases[key]]
        self.assertEqual(moved, [], f"plan-stage decisions moved: {moved[:10]}")

    def test_normalised_rederivation_is_byte_equal_to_the_golden(self) -> None:
        self.assertEqual(self.derived_text, self.checked_in)

    def test_coverage_is_every_scenario_at_every_reachable_phase_and_version(self) -> None:
        document = json.loads(self.checked_in)
        self.assertEqual({tuple(pair) for pair in document["phase_versions"]}, _EXPECTED_PHASE_VERSIONS)
        self.assertEqual(set(golden.PHASE_VERSIONS), _EXPECTED_PHASE_VERSIONS)
        expected_keys = {
            golden.case_key(scenario_id, phase, version)
            for scenario_id, _setup, _overrides in golden.SCENARIOS
            for phase, version in _EXPECTED_PHASE_VERSIONS
        }
        self.assertEqual(set(document["cases"]), expected_keys)
        self.assertEqual(set(document["cases"].values()), set(document["decisions"]))

    def test_the_matrix_exercises_every_decision_shape(self) -> None:
        """The golden is only as strong as its matrix: it must hold
        automatic, gated and declined decisions, gates carrying the
        stale-bundle recovery steps and the REJECTED marker, and no raise."""
        cases = golden.load_cases(self.checked_in)
        bodies = list(cases.values())
        self.assertFalse([key for key, body in cases.items() if "raises" in body])
        self.assertTrue(any(body["automatic"] for body in bodies))
        self.assertTrue(any(body["declined"] for body in bodies))
        self.assertTrue(any(body["gate"] is not None for body in bodies))
        gates = [body["gate"] for body in bodies if body["gate"] is not None]
        self.assertTrue(any("scripts/prepare-ai-review.sh <SHA> plan wi-1" in gate["safe_resume_command"]
                            for gate in gates))
        self.assertTrue(any("REJECTED" in (gate["artifact_path"] or "") for gate in gates))
        self.assertTrue(any("current.rejected-aaaa/" in gate["safe_resume_command"] for gate in gates))

    def test_golden_holds_no_unnormalised_root_or_sha(self) -> None:
        self.assertNotIn("/tmp", self.checked_in)
        self.assertIsNone(re.search(r"(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])", self.checked_in))
        self.assertIn("<ROOT>", self.checked_in)
        self.assertIn("<SHA>", self.checked_in)


class NormaliseTest(unittest.TestCase):
    """The one normalisation function the generator and the test share."""

    def test_root_and_forty_hex_tokens_are_replaced(self) -> None:
        sha = "0123456789abcdef0123456789abcdef01234567"
        value = {"repository": "/tmp/x/target", "text": [f"at /tmp/x/target/.ai-review ({sha})"]}
        self.assertEqual(
            golden.normalise(value, ("/tmp/x/target",)),
            {"repository": "<ROOT>", "text": ["at <ROOT>/.ai-review (<SHA>)"]},
        )

    def test_fixture_constant_ids_of_other_lengths_stay_literal(self) -> None:
        for literal in ("b" * 64, "stale" + "0" * 59, "c" * 39, "d" * 41):
            with self.subTest(literal=literal):
                self.assertEqual(golden.normalise(literal, ()), literal)

    def test_longest_root_wins(self) -> None:
        self.assertEqual(golden.normalise("/a/b/c", ("/a/b", "/a/b/c")), "<ROOT>")

    def test_non_strings_pass_through(self) -> None:
        self.assertEqual(golden.normalise({"n": 1, "b": True, "z": None}, ("/r",)),
                         {"n": 1, "b": True, "z": None})

    def test_document_round_trips_through_the_deduplicated_form(self) -> None:
        cases = {"s | P | 1": {"reason": "x"}, "t | P | 1": {"reason": "x"}, "u | P | 1": {"reason": "y"}}
        text = golden.render_document(cases)
        self.assertEqual(golden.load_cases(text), cases)
        self.assertEqual(len(json.loads(text)["decisions"]), 2)


if __name__ == "__main__":
    unittest.main()
