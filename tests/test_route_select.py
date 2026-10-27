"""The pure selection of a fallback chain entry (H9b, ``workflow-controller-
fallback-select`` D2 to D4, D7). No sandbox and no real ``claude``: every probe
is a function in the test."""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import harness, review_mode, route_select, routing, usage  # noqa: E402
from controller.errors import HarnessRoleError, UnknownHarnessError  # noqa: E402

CLAUDE = harness.CLAUDE_CODE.name
CODEX = harness.CODEX.name


def entry(harness_name: str = CLAUDE, model: str = "claude-sonnet-5-5") -> routing.ResolvedRoute:
    return routing.ResolvedRoute(
        role="milestone-implement", model=model, effort="high", single_agent=True, model_source="t", effort_source="t",
        harness=harness_name, harness_source="t")


@dataclasses.dataclass(frozen=True)
class FakeHold:
    resume_at: float | None
    waitable: bool = True


class Probes:
    """Records every admission; ``holds`` maps a provider to its hold."""

    def __init__(self, holds: dict | None = None, token: str | None = "tok", unavailable: set | None = None) -> None:
        self.holds = holds or {}
        self.token = token
        self.bad = unavailable or set()
        self.admitted: list[tuple[str, str]] = []

    def unavailable(self, route: routing.ResolvedRoute):
        return HarnessRoleError(f"no launcher {route.model}") if route.model in self.bad else None

    def admit(self, route: routing.ResolvedRoute, provider: str):
        self.admitted.append((route.model, provider))
        return self.holds.get(provider, self.token)


def registry() -> mock._patch:
    """``codex`` also takes the worker role, and a third harness of its own
    family-less name exists to prove an added adapter does not raise."""
    codex = dataclasses.replace(harness.CODEX, roles=(harness.WORKER, harness.REVIEWER),
                                models=harness.CLAUDE_CODE.models, efforts=harness.CLAUDE_CODE.efforts)
    extra = dataclasses.replace(harness.CODEX, name="gemini", reviewer_name=None, family="google",
                                roles=(harness.WORKER,))
    return mock.patch.object(harness, "ADAPTERS", {CLAUDE: harness.CLAUDE_CODE, codex.name: codex, extra.name: extra})


class SelectTest(unittest.TestCase):
    def run_select(self, chain, probes: Probes) -> route_select.Selection:
        with registry():
            return route_select.select(chain, probes.unavailable, probes.admit)

    def test_a_one_entry_chain_is_chosen_at_position_zero(self) -> None:
        probes = Probes()
        sel = self.run_select([entry()], probes)
        self.assertEqual((sel.outcome, sel.position, sel.skipped, sel.token), (route_select.CHOSEN, 0, (), "tok"))
        self.assertEqual(probes.admitted, [("claude-sonnet-5-5", "claude")])

    def test_the_first_admitted_entry_is_chosen_and_later_ones_are_not_probed(self) -> None:
        probes = Probes()
        sel = self.run_select([entry(model="a"), entry(CODEX, "b")], probes)
        self.assertEqual(sel.position, 0)
        self.assertEqual(probes.admitted, [("a", "claude")])

    def test_a_held_provider_falls_to_an_entry_of_another_provider(self) -> None:
        probes = Probes(holds={"claude": FakeHold(100.0)})
        sel = self.run_select([entry(model="a"), entry(CODEX, "b")], probes)
        self.assertEqual((sel.outcome, sel.position, sel.route.model), (route_select.CHOSEN, 1, "b"))
        self.assertEqual(sel.skipped, ((0, CLAUDE, route_select.CAUSE_USAGE_HELD),))
        self.assertEqual(probes.admitted, [("a", "claude"), ("b", "codex")])

    def test_a_later_entry_of_a_held_provider_is_skipped_without_an_admission(self) -> None:
        probes = Probes(holds={"claude": FakeHold(100.0)})
        sel = self.run_select([entry(model="a"), entry(model="b")], probes)
        self.assertEqual(sel.outcome, route_select.HELD)
        self.assertEqual(probes.admitted, [("a", "claude")])
        self.assertEqual([s[2] for s in sel.skipped], [route_select.CAUSE_USAGE_HELD] * 2)

    def test_an_unavailable_launcher_is_skipped_with_no_admission(self) -> None:
        probes = Probes(unavailable={"a"})
        sel = self.run_select([entry(model="a"), entry(model="b")], probes)
        self.assertEqual((sel.position, sel.skipped), (1, ((0, CLAUDE, route_select.CAUSE_LAUNCHER_UNAVAILABLE),)))
        self.assertEqual(probes.admitted, [("b", "claude")])

    def test_every_entry_held_reports_the_earliest_waitable_hold(self) -> None:
        late, early = FakeHold(900.0), FakeHold(300.0)
        sel = self.run_select([entry(model="a"), entry(CODEX, "b")], Probes(holds={"claude": late, "codex": early}))
        self.assertIs(sel.hold, early)

    def test_only_caps_report_the_first_hold(self) -> None:
        first, second = FakeHold(None, False), FakeHold(None, False)
        sel = self.run_select([entry(model="a"), entry(CODEX, "b")], Probes(holds={"claude": first, "codex": second}))
        self.assertIs(sel.hold, first)

    def test_a_waitable_hold_beats_a_cap(self) -> None:
        cap, timed = FakeHold(None, False), FakeHold(500.0)
        sel = self.run_select([entry(model="a"), entry(CODEX, "b")], Probes(holds={"claude": cap, "codex": timed}))
        self.assertIs(sel.hold, timed)

    def test_every_launcher_unavailable_carries_the_first_error(self) -> None:
        probes = Probes(unavailable={"a", "b"})
        sel = self.run_select([entry(model="a"), entry(model="b")], probes)
        self.assertEqual(sel.outcome, route_select.UNAVAILABLE)
        self.assertEqual(str(sel.error), "no launcher a")
        self.assertEqual(probes.admitted, [])

    def test_equal_resume_times_report_the_earlier_position(self) -> None:
        one, two = FakeHold(300.0), FakeHold(300.0)
        sel = self.run_select([entry(model="a"), entry(CODEX, "b")], Probes(holds={"claude": one, "codex": two}))
        self.assertIs(sel.hold, one)

    def test_a_waitable_hold_without_a_resume_is_not_waitable(self) -> None:
        odd, timed = FakeHold(None, True), FakeHold(900.0)
        sel = self.run_select([entry(model="a"), entry(CODEX, "b")], Probes(holds={"claude": odd, "codex": timed}))
        self.assertIs(sel.hold, timed)

    def test_a_mix_of_unavailable_and_held_reports_the_hold(self) -> None:
        held = FakeHold(300.0)
        probes = Probes(holds={"claude": held}, unavailable={"a", "c"})
        sel = self.run_select([entry(model="a"), entry(model="b"), entry(model="c")], probes)
        self.assertEqual(sel.outcome, route_select.HELD)
        self.assertIs(sel.hold, held)
        self.assertEqual([s[2] for s in sel.skipped], [route_select.CAUSE_LAUNCHER_UNAVAILABLE,
                                                       route_select.CAUSE_USAGE_HELD,
                                                       route_select.CAUSE_LAUNCHER_UNAVAILABLE])

    def test_a_disabled_budget_chooses_the_first_available_entry_with_no_token(self) -> None:
        probes = Probes(token=None, unavailable={"a"})
        sel = self.run_select([entry(model="a"), entry(model="b")], probes)
        self.assertEqual((sel.outcome, sel.position, sel.token), (route_select.CHOSEN, 1, None))


class ProviderTest(unittest.TestCase):
    def test_constants_match_the_budget(self) -> None:
        self.assertEqual({route_select._PROVIDER_CLAUDE, route_select._PROVIDER_CODEX}, set(usage.PROVIDERS))
        self.assertEqual(route_select._PROVIDER_CLAUDE, usage.PROVIDER_CLAUDE)
        self.assertEqual(route_select._PROVIDER_CODEX, usage.PROVIDER_CODEX)

    def test_every_registered_adapter_maps_to_a_provider_like_the_review_job(self) -> None:
        for adapter in harness.ADAPTERS.values():
            expected = usage.PROVIDER_CLAUDE if adapter.family == review_mode.CLAUDE_FAMILY else usage.PROVIDER_CODEX
            self.assertEqual(route_select.usage_provider(adapter.name), expected)
            self.assertIn(route_select.usage_provider(adapter.name), usage.PROVIDERS)

    def test_an_added_adapter_does_not_raise(self) -> None:
        with registry():
            self.assertEqual(route_select.usage_provider("gemini"), usage.PROVIDER_CODEX)

    def test_an_unknown_harness_raises_the_registrys_error(self) -> None:
        with self.assertRaises(UnknownHarnessError):
            route_select.usage_provider("nope")


class FamilyRuleTest(unittest.TestCase):
    def test_a_chosen_entrys_family_is_in_the_worker_families(self) -> None:
        with registry():
            config = routing.RoutingOptions(config=routing.validate_routing_mapping(
                {"default": {"model": "claude-sonnet-5-5"},
                 "roles": {"milestone-implement": {"model": "claude-sonnet-5-5",
                                                 "fallback": [{"harness": CODEX, "model": "claude-sonnet-5-5"}]}}},
                path="t", where="", require_schema_version=False, unknown=routing.UNKNOWN_IGNORE)[0])
            chain = config.chain("milestone-implement")
            self.assertEqual(len(chain), 2)
            families = review_mode.worker_families(config)
            for holds in ({}, {"claude": FakeHold(1.0)}):
                probes = Probes(holds=holds)
                sel = route_select.select(chain, probes.unavailable, probes.admit)
                self.assertEqual(sel.outcome, route_select.CHOSEN)
                self.assertIn(harness.get(sel.route.harness).family, families)


if __name__ == "__main__":
    unittest.main()
