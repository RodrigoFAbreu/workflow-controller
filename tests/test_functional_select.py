"""The functional tester's chain is walked before the spawn (``workflow-controller-codex-tester-fallback`` CP1,
D1-D5).

``route_select`` is real; ``launch_codex_functional`` and ``review.launch_functional`` are fakes, the binary lookup
and the build record are patched, and the usage gate is the recorded one of ``TesterLifecycle``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import codex_build, functional_review, harness, job, review, review_mode, routing, telemetry, usage  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests.test_functional_codex_route import CODEX_ROUTE, CodexLifecycle  # noqa: E402
from tests.test_protocol_job import IDENTITY  # noqa: E402

CLAUDE_MODEL = "claude-sonnet-5-5"
CLAUDE_FIGURES = {"results": 1, "turns": 3, "problems": []}  # what a Claude tester's stream leaves in ``out``


def chain_options(first: str = "claude-code", second: str = "codex", third: str | None = None) -> routing.RoutingOptions:
    models = {"claude-code": CLAUDE_MODEL, "codex": "gpt-6.1-sol"}
    fallback = [{"harness": second, "model": models[second], "effort": "high"}]
    if third is not None:
        fallback.append({"harness": third, "model": CLAUDE_MODEL if third == "claude-code" else models[third],
                         "effort": "high"})
    config = routing.validate_routing_mapping(
        {"default": {"model": CLAUDE_MODEL},
         "roles": {"functional-tester": {"harness": first, "model": models[first], "effort": "high",
                                         "fallback": fallback}}},
        path="t", where="", require_schema_version=False, unknown=routing.UNKNOWN_IGNORE)[0]
    return routing.RoutingOptions(config=config)


class _Case(unittest.TestCase):
    verified = True
    missing: frozenset = frozenset()

    def life(self, **kwargs) -> CodexLifecycle:
        life = CodexLifecycle(self, **kwargs)
        life.to_38e()
        life.plan = [("flow-1", "passed", None)]
        return life

    def resolve(self, name: str) -> Path:
        if name in self.missing:
            raise ReviewLaunchRefusedError(f"cannot find {name}")
        return Path("/bin") / name

    def step(self, life: CodexLifecycle, route, *, gate="recorded") -> dict:
        admitted = life.usage_gate() if gate == "recorded" else gate
        build = mock.Mock(ok=self.verified, reason="no record", digest="d")
        with mock.patch.object(review, "resolve_binary", self.resolve), \
                mock.patch.object(codex_build, "is_verified", return_value=build):
            return life.step(gate=admitted, route=route)


class SelectionTest(_Case):
    def test_a_one_entry_chain_is_unchanged(self) -> None:
        life = self.life()
        record = self.step(life, routing.NO_OVERRIDES)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertNotIn("selection", record["worker_route"])
        self.assertNotIn("selection", record["functional"])
        (launch,) = life.claude_launches
        self.assertEqual(sorted(launch), sorted([
            "candidate", "flows", "runtime_root", "job_id", "work_item_id", "operator_home", "model", "effort",
            "timeout", "binary_name", "on_spawn", "out"]))

    def test_a_codex_one_entry_chain_is_unchanged(self) -> None:
        life = self.life()
        record = self.step(life, CODEX_ROUTE)
        self.assertNotIn("selection", record["worker_route"])
        self.assertEqual(life.providers, [usage.PROVIDER_CODEX])

    def test_the_first_usable_entry_is_chosen_at_position_zero(self) -> None:
        life = self.life()
        record = self.step(life, chain_options())
        self.assertEqual(record["functional"]["harness"], "claude")
        self.assertEqual(record["worker_route"]["selection"], {"position": 0, "length": 2, "skipped": []})
        self.assertEqual(len(life.claude_launches), 1)

    def test_a_claude_entry_without_a_binary_falls_through_to_codex(self) -> None:
        life = self.life()
        self.missing = frozenset({"claude"})
        record = self.step(life, chain_options())
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(record["functional"]["harness"], "codex")
        self.assertEqual(record["worker_route"]["harness"], "codex")
        self.assertEqual(record["worker_route"]["selection"], {
            "position": 1, "length": 2,
            "skipped": [{"position": 0, "harness": "claude-code", "cause": "launcher_unavailable"}]})
        self.assertEqual(record["functional"]["selection"], record["worker_route"]["selection"]["skipped"])
        self.assertEqual(life.claude_launches, [])
        self.assertEqual(len(life.launches), 1)

    def test_a_chosen_codex_entry_adds_the_selection_to_its_telemetry_block(self) -> None:
        life = self.life()
        self.missing = frozenset({"claude"})
        block = self.step(life, chain_options())["telemetry"]
        self.assertEqual((block["harness"], block["chain_position"], block["chain_skipped"]),
                         ("codex", 1, ["launcher_unavailable"]))

    def test_a_chosen_claude_entry_adds_the_selection_to_its_telemetry_rows(self) -> None:
        life = self.life()
        self.missing = frozenset({"codex-bin"})
        life.extra = {"telemetry": dict(CLAUDE_FIGURES)}
        record = self.codex_first(life)
        self.assertEqual(record["functional"]["harness"], "claude")
        (row,) = telemetry.rows([record])
        self.assertEqual((row["harness"], row["telemetry"]["chain_position"], row["telemetry"]["chain_skipped"]),
                         ("claude-code", 1, ["launcher_unavailable"]))

    def test_a_first_choice_claude_entry_reports_position_zero_and_no_skips(self) -> None:
        life = self.life()
        life.extra = {"telemetry": dict(CLAUDE_FIGURES)}
        record = self.step(life, chain_options())
        (row,) = telemetry.rows([record])
        self.assertEqual((row["telemetry"]["chain_position"], row["telemetry"]["chain_skipped"]), (0, []))

    def test_a_one_entry_claude_record_has_no_selection_dimensions(self) -> None:
        life = self.life()
        life.extra = {"telemetry": dict(CLAUDE_FIGURES)}
        record = self.step(life, routing.NO_OVERRIDES)
        self.assertNotIn("chain_position", record["telemetry"])
        self.assertNotIn("chain_skipped", record["telemetry"])

    def codex_first(self, life: CodexLifecycle) -> dict:
        return self.step(life, chain_options("codex", "claude-code"))

    def test_a_codex_entry_without_options_falls_through(self) -> None:
        life = self.life(with_entry=False)
        record = self.codex_first(life)
        self.assertEqual(record["functional"]["harness"], "claude")
        self.assertEqual(record["worker_route"]["selection"]["position"], 1)

    def test_a_codex_entry_without_a_binary_falls_through(self) -> None:
        life = self.life()
        self.missing = frozenset({"codex-bin"})
        record = self.codex_first(life)
        self.assertEqual(record["functional"]["harness"], "claude")
        self.assertEqual(record["worker_route"]["selection"]["skipped"][0]["harness"], "codex")

    def test_a_codex_entry_without_a_login_falls_through(self) -> None:
        life = self.life()
        (life.codex_home / "auth.json").unlink()
        record = self.codex_first(life)
        self.assertEqual(record["functional"]["harness"], "claude")
        self.assertEqual(record["worker_route"]["selection"]["skipped"],
                         [{"position": 0, "harness": "codex", "cause": "launcher_unavailable"}])

    def test_a_codex_entry_with_an_unverified_build_falls_through(self) -> None:
        life = self.life()
        self.verified = False
        record = self.codex_first(life)
        self.assertEqual(record["functional"]["harness"], "claude")

    def test_a_held_claude_provider_skips_its_later_entries_without_a_second_admission(self) -> None:
        life = self.life()
        calls: list[str] = []
        real = life.usage_gate()

        def gate(route, provider):
            calls.append(provider)
            return mock.Mock(spec=usage.Hold, provider=provider) if provider == usage.PROVIDER_CLAUDE else real(
                route, provider=provider)

        record = self.step(life, chain_options("claude-code", "claude-code", "codex"), gate=gate)
        self.assertEqual(calls, [usage.PROVIDER_CLAUDE, usage.PROVIDER_CODEX])
        self.assertEqual(record["functional"]["harness"], "codex")
        self.assertEqual([s["cause"] for s in record["worker_route"]["selection"]["skipped"]],
                         ["usage_held", "usage_held"])

    def test_every_entry_unavailable_is_one_gate_record_and_nothing_else(self) -> None:
        life = self.life()
        self.missing = frozenset({"claude", "codex-bin"})
        record = self.step(life, chain_options())
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        block = record["functional"]
        self.assertEqual((block["gate"], block["cause"]), (functional_review.GATE_TESTER_UNAVAILABLE, "no_binary"))
        self.assertIn("claude", block["detail"])
        self.assertEqual([s["harness"] for s in block["selection"]], ["claude-code", "codex"])
        self.assertNotIn("not_started", block)
        self.assertEqual((life.launches, life.tester_records(), life.reservations()), ([], [], {}))

    def test_every_entry_held_returns_the_usage_hold_and_writes_nothing(self) -> None:
        life = self.life()
        hold = usage.Hold(provider=usage.PROVIDER_CLAUDE, window="five_hour", percent=99.0, forecast=99.0,
                          resume_at=None, reason="cap", waitable=False)
        result = self.step(life, chain_options(), gate=lambda route, provider: hold)
        self.assertIsInstance(result, job.UsageHold)
        self.assertEqual((life.launches, life.tester_records(), life.reservations()), ([], [], {}))

    def test_a_refusal_inside_the_chosen_launch_never_tries_the_next_entry(self) -> None:
        life = self.life()
        life.raises = functional_review.unavailable(functional_review.UNAVAILABLE_ENFORCEMENT, "bridge refused")
        record = self.step(life, chain_options("claude-code", "codex"))
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(record["functional"]["gate"], functional_review.GATE_TESTER_UNAVAILABLE)
        self.assertTrue(record["functional"]["not_started"])
        self.assertEqual(len(life.launches), 1)
        self.assertEqual(life.claude_launches, life.launches)


class IndependenceTest(unittest.TestCase):
    def test_a_second_entry_codex_tester_changes_no_worker_family(self) -> None:
        options = chain_options()
        self.assertEqual(review_mode.worker_families(options), review_mode.worker_families(routing.NO_OVERRIDES))
        self.assertNotIn(harness.get("codex").family, review_mode.worker_families(options))

    def test_a_second_entry_codex_tester_leaves_the_reviewer_choice_unchanged(self) -> None:
        reviewer = {"harness": "codex"}
        before = review_mode.distinct_harness(reviewer, review_mode.worker_families(routing.NO_OVERRIDES))
        after = review_mode.distinct_harness(reviewer, review_mode.worker_families(chain_options()))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
