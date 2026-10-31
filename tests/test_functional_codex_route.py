"""The functional job runs the harness its route names (``workflow-controller-codex-tester-routing`` CP2, D2-D4).

``TesterLifecycle`` supplies the real ``execute_step`` path on a vendored Workflow; here the Codex launch is its
fake tester, so what is asserted is the dispatch: the usage provider, the record's harness, the credential digest,
what the launch receives, the pre-launch stop of D3, and ``_reconcile_functional`` following the record.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import codex_tester, functional_review, job, review, routing, telemetry, usage, worker  # noqa: E402
from tests.test_protocol_job import IDENTITY, TesterLifecycle  # noqa: E402
from tests import test_protocol_job  # noqa: E402

CODEX_ROUTE = routing.RoutingOptions(
    cli_role_harnesses={"functional-tester": "codex"}, cli_role_models={"functional-tester": "gpt-6.1-sol"},
    cli_role_efforts={"functional-tester": "high"})


class CodexLifecycle(TesterLifecycle):
    def __init__(self, test: unittest.TestCase, *, with_entry: bool = True) -> None:
        super().__init__(test)
        self.codex_home = self.td / "codex-home"
        self.codex_home.mkdir()
        (self.codex_home / "auth.json").write_text('{"token": "t"}')
        if with_entry:
            self.functional["harnesses"] = {
                "claude-code": {"operator_home": self.functional["operator_home"], "binary": "claude"},
                "codex": {"operator_home": str(self.codex_home), "binary": "codex-bin"},
            }
        self.providers: list[str] = []
        self.claude_launches: list[dict] = []

    def rebase(self, td: Path) -> None:
        super().rebase(td)
        self.codex_home = td / "codex-home"
        entries = self.functional.get("harnesses")
        if entries:
            entries["claude-code"]["operator_home"] = str(td / "claude-home")
            entries["codex"]["operator_home"] = str(self.codex_home)

    def step(self, *, gate=None, functional="default", route=CODEX_ROUTE) -> dict:
        self.rows.append(test_protocol_job.protocol.next_action(self.root, test_protocol_job.lifecycle.WID).raw["row"])
        inner = gate

        def counted(route_, provider=usage.PROVIDER_CLAUDE):
            self.providers.append(provider)
            return inner(route_, provider=provider)

        def launch(task, *, cwd, **kwargs):
            self.effect(task)
            return test_protocol_job.lifecycle._result(self.report)

        def claude(**kwargs):
            self.claude_launches.append(kwargs)
            return self.fake_tester(**kwargs)

        with mock.patch.object(worker, "launch", launch), \
                mock.patch.object(codex_tester, "launch_codex_functional", self.fake_tester), \
                mock.patch.object(review, "launch_functional", claude):
            return job.execute_step(self.inspect(), identity=IDENTITY, runtime=self.runtime, routing=route,
                                    usage_gate=counted if inner is not None else None,
                                    functional=self.functional if functional == "default" else functional)


class _Case(unittest.TestCase):
    def life(self, **kwargs) -> CodexLifecycle:
        life = CodexLifecycle(self, **kwargs)
        life.to_38e()
        return life


class CodexRouteTest(_Case):
    def test_the_codex_route_runs_the_codex_launch_with_the_codex_provider_and_home(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        record = life.step(gate=life.usage_gate())
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(life.providers, [usage.PROVIDER_CODEX])
        self.assertEqual(record["functional"]["harness"], "codex")
        self.assertEqual(record["worker_route"]["harness"], "codex")
        self.assertEqual(record["functional"]["captured_digest"], review._sha256_file(life.codex_home / "auth.json"))
        self.assertEqual(record["functional"]["operator_home"], str(life.codex_home))
        (launch,) = life.launches
        self.assertEqual((launch["model"], launch["effort"], launch["binary_name"], launch["operator_home"]),
                         ("gpt-6.1-sol", "high", "codex-bin", life.codex_home))
        self.assertEqual(life.claude_launches, [])
        self.assertEqual(record["functional"]["outcome"], "recorded")

    def test_a_codex_refusal_ends_at_the_gate_not_started(self) -> None:
        life = self.life()
        life.raises = functional_review.unavailable(functional_review.UNAVAILABLE_ENFORCEMENT, "not verified")
        record = life.step(gate=life.usage_gate())
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(record["functional"]["gate"], functional_review.GATE_TESTER_UNAVAILABLE)
        self.assertTrue(record["functional"]["not_started"])

    def test_options_without_the_codex_entry_end_before_any_usage_or_record(self) -> None:
        life = self.life(with_entry=False)
        calls: list[str] = []
        gate = life.usage_gate()
        record = life.step(gate=lambda route, provider: calls.append(provider) or gate(route, provider=provider))
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        block = record["functional"]
        self.assertEqual((block["gate"], block["cause"]), (functional_review.GATE_TESTER_UNAVAILABLE, "no_binary"))
        self.assertIn("harnesses", block["detail"])
        self.assertNotIn("not_started", block)
        self.assertEqual((calls, life.launches, life.tester_records(), life.reservations()), ([], [], [], {}))

    def test_the_claude_branch_is_called_with_todays_keyword_arguments(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        record = life.step(route=routing.NO_OVERRIDES)
        self.assertEqual(record["functional"]["harness"], "claude")
        (launch,) = life.claude_launches
        self.assertEqual(sorted(launch), sorted([
            "candidate", "flows", "runtime_root", "job_id", "work_item_id", "operator_home", "model", "effort",
            "timeout", "binary_name", "on_spawn", "out"]))
        self.assertEqual((launch["binary_name"], launch["operator_home"]),
                         ("claude", Path(life.functional["operator_home"])))
        self.assertEqual(life.launches, life.claude_launches)

    def test_a_harness_with_no_launch_is_refused_by_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "'other'"):
            job._functional_launch("other", {"operator_home": "/h", "binary": "claude"})

    def test_a_caller_without_harnesses_still_runs_claude_from_the_top_level_keys(self) -> None:
        life = self.life(with_entry=False)
        life.plan = [("flow-1", "passed", None)]
        record = life.step(route=routing.NO_OVERRIDES)
        self.assertEqual(record["functional"]["harness"], "claude")
        self.assertEqual(record["status"], job.STATUS_FINISHED)


class ReconcileTest(unittest.TestCase):
    def reconcile(self, harness: str, *, credential: bool):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            selected = review.CODEX if harness == "codex" else review.CLAUDE
            layout = review.JobLayout(runtime, "job-1", area=functional_review.SANDBOX_AREA)
            if credential:
                (layout.base / selected.home_name).mkdir(parents=True)
                (layout.base / selected.home_name / selected.credential_file).write_text("{}")
            record = {"job_id": "job-1", "status": job.STATUS_LAUNCHED, "target_repo": raw, "functional": {
                "harness": harness, "captured_digest": "d", "operator_home": raw}}
            with mock.patch.object(job, "_end_review_group"), \
                    mock.patch.object(job, "_persist", lambda root, jid, rec, **kw: rec), \
                    mock.patch.object(review, "copy_back_credentials", return_value="copied") as copy:
                result = job._reconcile_functional(record, runtime_root=runtime)
            return result["functional"]["credentials"], copy

    def test_a_codex_record_without_its_credential_leaves_none(self) -> None:
        credentials, copy = self.reconcile("codex", credential=False)
        self.assertIsNone(credentials)
        copy.assert_not_called()

    def test_a_codex_record_copies_the_codex_credential_back(self) -> None:
        credentials, copy = self.reconcile("codex", credential=True)
        self.assertEqual(credentials, "copied")
        self.assertIs(copy.call_args.args[3], review.CODEX)

    def test_a_claude_record_copies_the_claude_credential_back(self) -> None:
        credentials, copy = self.reconcile("claude", credential=True)
        self.assertIs(copy.call_args.args[3], review.CLAUDE)


ROLLOUTS = [{"subagent": False, "usage": {"input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 50,
                                          "reasoning_output_tokens": 10, "total_tokens": 1050}}]


class UsageAndTelemetryTest(_Case):
    def codex(self, **extra):
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        life.extra = extra
        return life, life.step(gate=life.usage_gate())

    def test_the_rollouts_give_the_job_a_usage_record_with_their_tokens(self) -> None:
        _, record = self.codex(usage_rollouts=ROLLOUTS)
        figures = record["usage_record"]
        self.assertEqual((figures["harness"], figures["cache_read"], figures["output"], figures["input"] is None),
                         ("codex", 200, 50, True))

    def test_a_launch_without_rollouts_gives_the_empty_record(self) -> None:
        _, record = self.codex(usage_rollouts=[])
        figures = record["usage_record"]
        self.assertEqual((figures["input"], figures["output"], figures["cost"]), (None, None, None))
        _, absent = self.codex()
        self.assertEqual(absent["usage_record"], figures)

    def test_a_launched_codex_tester_has_a_telemetry_block_of_its_harness(self) -> None:
        _, record = self.codex(usage_rollouts=ROLLOUTS)
        block = record["telemetry"]
        self.assertEqual((block["harness"], block["role"], block["model"]), ("codex", "functional-tester", "gpt-6.1-sol"))
        self.assertNotIn("chain_position", block)
        self.assertEqual([p["kind"] for p in block["problems"]], ["no_stream"])
        self.assertTrue(telemetry.unavailable(block))

    def test_a_tester_that_was_not_started_has_no_block(self) -> None:
        life = self.life()
        life.raises = functional_review.unavailable(functional_review.UNAVAILABLE_ENFORCEMENT, "not verified")
        record = life.step(gate=life.usage_gate())
        self.assertNotIn("telemetry", record)

    def test_a_claude_tester_keeps_the_block_its_launch_left(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        life.extra = {"telemetry": {"version": 1, "results": 1, "marker": "claude"}}
        record = life.step(gate=life.usage_gate(), route=routing.NO_OVERRIDES)
        self.assertEqual(record["telemetry"]["marker"], "claude")
        self.assertNotIn("problems", record["telemetry"])


if __name__ == "__main__":
    unittest.main()
