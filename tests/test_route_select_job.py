"""Selection of a fallback chain entry at the worker launch call site, the
record's ``selection`` block and the telemetry dimensions (H9b,
``workflow-controller-fallback-select`` D1, D4 to D6, D8). Every worker is
``tests/fake_claude.py``; a second worker harness comes from a patched
registry, as in H2, H9a and H10."""

from __future__ import annotations

import dataclasses
import json
import unittest
import unittest.mock
from collections.abc import Mapping

from controller import harness, job, routing, telemetry, usage, worker_launch
from controller.errors import HarnessRoleError
from tests import test_route_select as trs
from tests import test_usage_gate as tug

ROLE = routing.MILESTONE_PLAN
CLAUDE = harness.CLAUDE_CODE.name
OTHER = "gemini"  # a second worker harness, family google: the codex provider


def chain_options(*fallback: Mapping, first: Mapping | None = None) -> routing.RoutingOptions:
    entry = dict(first or {"model": "claude-opus-5-5", "effort": "medium"})
    entry["fallback"] = [dict(f) for f in fallback]
    config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": {ROLE: entry}}), path="r.json")
    return routing.RoutingOptions(config=config)


SECOND_CLAUDE = {"harness": CLAUDE, "model": "claude-sonnet-5-5"}
SECOND_OTHER = {"harness": OTHER, "model": "claude-sonnet-5-5"}


class _Launcher:
    """Delegates to the fake claude through ``worker.launch``; counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def launch(self, task, **options):
        self.calls += 1
        return worker_launch.ClaudeCodeLauncher().launch(task, **options)


class _ChainCase(tug._UsageCase):
    def setUp(self) -> None:
        super().setUp()
        extra = dataclasses.replace(harness.CODEX, name=OTHER, reviewer_name=None, family="google",
                                    roles=(harness.WORKER,), models=harness.CLAUDE_CODE.models,
                                    efforts=harness.CLAUDE_CODE.efforts)
        self.enterContext(unittest.mock.patch.object(
            harness, "ADAPTERS", {CLAUDE: harness.CLAUDE_CODE, OTHER: extra}))
        self.second = _Launcher()
        self.enterContext(unittest.mock.patch.object(
            worker_launch, "LAUNCHERS", {**worker_launch.LAUNCHERS, OTHER: self.second}))
        self.gate_calls: list[tuple[str, str]] = []

    def pgate(self, limits: usage.Limits | None = None):
        """The real budget, per provider: ``usage_gate(route, provider=...)``."""
        limits = limits or usage.Limits()

        def gate(route, provider=usage.PROVIDER_CLAUDE):
            self.gate_calls.append((route.model, provider))
            admission = usage.admit(self.runtime_root, provider=provider, role=route.role, model=route.model,
                                    repository=str(self.root), run_id="run-1", limits=limits, now=self.now)
            return admission.hold if admission.hold is not None else admission.token

        return gate

    def cstep(self, options: routing.RoutingOptions, gate=None):
        return self._step(routing=options, usage_gate=gate, timeout=60)


class ChainStepTest(_ChainCase):
    def test_a_role_without_a_chain_records_no_selection(self) -> None:
        record = self._step(timeout=60)
        self.assertNotIn("selection", record["worker_route"])

    def test_a_claude_only_chain_with_the_account_free_chooses_position_zero(self) -> None:
        self.reading(10)
        record = self.cstep(chain_options(SECOND_CLAUDE), self.pgate())
        self.assertEqual(record["worker_route"]["selection"], {"position": 0, "length": 2, "skipped": []})
        self.assertEqual(record["worker_route"]["model"], "claude-opus-5-5")

    def test_a_held_claude_account_falls_back_to_the_second_harness(self) -> None:
        self.reading(99)
        record = self.cstep(chain_options(SECOND_OTHER), self.pgate())
        route = record["worker_route"]
        self.assertEqual((route["harness"], route["model"]), (OTHER, "claude-sonnet-5-5"))
        self.assertEqual(route["selection"], {
            "position": 1, "length": 2,
            "skipped": [{"position": 0, "harness": CLAUDE, "cause": "usage_held"}]})
        self.assertEqual(self.second.calls, 1)
        self.assertEqual(record["usage"]["provider"], usage.PROVIDER_CODEX)
        self.assertEqual([c for c in self.gate_calls if c[1] == usage.PROVIDER_CLAUDE],
                         [("claude-opus-5-5", usage.PROVIDER_CLAUDE)])

    def test_a_held_account_with_only_same_account_entries_holds_as_today(self) -> None:
        self.reading(99)
        result = self.cstep(chain_options(SECOND_CLAUDE), self.pgate())
        single = self._step(usage_gate=self.pgate(), timeout=60)
        self.assertIsInstance(result, job.UsageHold)
        self.assertEqual(dataclasses.asdict(result), dataclasses.asdict(single))
        self.assertEqual(self.gate_calls.count(("claude-sonnet-5-5", "claude")), 0)
        self.assert_no_record()
        self.assertEqual(self.shared()["reservations"], {})

    def test_every_entry_held_reports_the_earliest_waitable_resume(self) -> None:
        def hold(provider: str, resume_at: float) -> usage.Hold:
            return usage.Hold(provider=provider, window=usage.FIVE_HOUR, percent=99.0, forecast=99.0,
                              resume_at=resume_at, reason="held", waitable=True)

        holds = {usage.PROVIDER_CLAUDE: hold("claude", 900.0), usage.PROVIDER_CODEX: hold("codex", 300.0)}

        def gate(route, provider=usage.PROVIDER_CLAUDE):
            return holds[provider]

        result = self.cstep(chain_options(SECOND_OTHER), gate)
        self.assertIsInstance(result, job.UsageHold)
        self.assertEqual(result.resume_at, 300.0)
        self.assert_no_record()

    def test_every_launcher_unavailable_raises_the_first_entrys_error(self) -> None:
        with unittest.mock.patch.object(worker_launch, "LAUNCHERS", {}), self.assertRaises(HarnessRoleError):
            self.cstep(chain_options(SECOND_OTHER), self.pgate())
        self.assert_no_record()

    def test_an_unavailable_first_entry_is_skipped(self) -> None:
        launchers = {OTHER: self.second}
        with unittest.mock.patch.object(worker_launch, "LAUNCHERS", launchers):
            record = self.cstep(chain_options(SECOND_OTHER), self.pgate())
        self.assertEqual(record["worker_route"]["selection"]["skipped"],
                         [{"position": 0, "harness": CLAUDE, "cause": "launcher_unavailable"}])

    def test_a_worker_that_fails_after_it_started_is_not_retried_on_another_entry(self) -> None:
        self.reading(10)
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_EXIT": "1"}):
            record = self.cstep(chain_options(SECOND_OTHER), self.pgate())
        self.assertEqual(record["worker_route"]["selection"]["position"], 0)
        self.assertEqual(self.second.calls, 0)
        self.assertEqual(len(self._records()), 1)
        second = self.cstep(chain_options(SECOND_OTHER), self.pgate())
        self.assertEqual(second["worker_route"]["selection"]["position"], 0)

    def test_resume_never_selects(self) -> None:
        self.reading(10)
        self.cstep(chain_options(SECOND_OTHER), self.pgate())
        with unittest.mock.patch.object(job.route_select, "select", side_effect=AssertionError("selected")):
            self.resume()


class TelemetryDimensionsTest(unittest.TestCase):
    def record(self, **route) -> dict:
        return {"worker_route": {"role": ROLE, "model": "m", "effort": "high", **route}}

    def test_a_record_with_a_selection_reports_the_chain_dimensions(self) -> None:
        dims = telemetry.dimensions(self.record(harness=OTHER, selection={
            "position": 1, "length": 2, "skipped": [{"position": 0, "harness": CLAUDE, "cause": "usage_held"}]}))
        self.assertEqual((dims["chain_position"], dims["chain_skipped"], dims["harness"]), (1, ["usage_held"], OTHER))

    def test_a_record_without_one_reports_what_it_always_did(self) -> None:
        dims = telemetry.dimensions(self.record())
        self.assertEqual(set(dims), {"role", "model", "effort", "harness", "workflow_version", "controller_version"})
        self.assertEqual(dims["harness"], telemetry.HARNESS)

    def test_a_courier_review_record_keeps_the_hard_coded_harness(self) -> None:
        record = {**self.record(harness="codex"), "kind": "review", "review": {"action_id": "a"}}
        self.assertTrue(telemetry.is_courier_job(record))
        self.assertEqual(telemetry.dimensions(record)["harness"], telemetry.HARNESS)


if __name__ == "__main__":
    unittest.main()
