"""`workflow-controller-codex-tester-run`: the Codex tester's run (`controller.codex_tester`). Offline: the
tester is the scripted fake Codex; the launch controls use a fake runner, so no `app-server` starts.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_bridge as cb, codex_sandbox as cs, codex_tester as ct, functional_review as fr, review  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests import fake_codex  # noqa: E402
from tests.test_codex_bridge import BridgeFixture  # noqa: E402
from tests.test_codex_sandbox import FEATURES, NO_SERVERS, composed_for, result  # noqa: E402

import json  # noqa: E402

FLOWS = [fr.Flow("flow-1", "1", "run the thing")]


class TesterFixture(BridgeFixture):
    def setUp(self) -> None:
        super().setUp()
        self.log = self.tmp / "fake.log"

    def script(self, **exec_spec) -> None:
        """Replace the Codex entry the binary links to with the scripted fake."""
        scenario = {"log": str(self.log), "exec": exec_spec, "hang_seconds": 60, "orphan_seconds": 120}
        fake_codex.write_binary(self.package / "bin", scenario, name="codex.js")

    def bridge_for(self, **kw) -> cb.Bridge:
        return self.bridge(**kw)

    def controls(self, bridge: cb.Bridge):
        """A runner for the three launch controls that starts no process."""
        built = bridge.built
        answer = composed_for(built.layout, list(built.roots), model=built.model, effort=built.effort)
        body = (json.dumps({"id": 1, "result": {}}) + "\n" + json.dumps({"id": 2, "result": answer}) + "\n").encode()

        def run(command, **kw):
            kind = command[1]
            return result({"mcp": NO_SERVERS, "features": FEATURES}.get(kind, body))

        return run

    def run_tester(self, bridge: cb.Bridge, *, timeout: float = 20, run=None, **kw) -> dict:
        return ct.run_tester(bridge, FLOWS, binary=self.binary, operator_home=self.operator, timeout=timeout,
                             system_dir=self.system, run=run or self.controls(bridge), **kw)

    def execs(self) -> list[dict]:
        return [entry for entry in fake_codex.read_log(self.log) if entry["kind"] == "exec"]


class OutcomeTest(TesterFixture):
    def test_a_normal_run_is_ran_with_the_scripted_message(self) -> None:
        self.script(message="all flows passed")
        outcome = self.run_tester(self.bridge_for())
        self.assertEqual((outcome["outcome"], outcome["detail"]), (ct.RAN, ""))
        self.assertEqual((outcome["tester_message"], outcome["exit_code"]), ("all flows passed", 0))
        self.assertEqual(len(self.execs()), 1)

    def test_a_non_zero_exit_is_failed_with_the_output_tail(self) -> None:
        self.script(message="partial", exit=3, stderr="boom: transcript tail")
        outcome = self.run_tester(self.bridge_for())
        self.assertEqual(outcome["outcome"], ct.FAILED)
        self.assertIn("boom: transcript tail", outcome["detail"])
        self.assertEqual(outcome["exit_code"], 3)

    def test_the_detail_is_bounded(self) -> None:
        self.script(message="x", exit=1, stderr="e" * 5000)
        self.assertLessEqual(len(self.run_tester(self.bridge_for())["detail"]), ct.DETAIL_CHARS)

    def test_a_hang_past_the_deadline_is_timeout_and_leaves_no_process_of_the_tag(self) -> None:
        self.script(message="late", **{"verdict": "hang", "descendant": True})
        bridge = self.bridge_for()
        tag = bridge.built.environment[review.OWNERSHIP_VAR]
        start = time.monotonic()
        outcome = self.run_tester(bridge, timeout=2)
        self.assertEqual(outcome["outcome"], ct.TIMEOUT)
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(self.tagged(tag), [])

    def tagged(self, tag: str) -> list[int]:
        found = []
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    if f"{review.OWNERSHIP_VAR}={tag}".encode() in (entry / "environ").read_bytes().split(b"\0"):
                        found.append(int(entry.name))
                except OSError:
                    continue
        return found

    def test_a_limit_rollout_is_limit(self) -> None:
        self.script(verdict="limit")
        outcome = self.run_tester(self.bridge_for())
        self.assertEqual(outcome["outcome"], ct.LIMIT)

    def test_a_zero_exit_with_no_message_is_failed_with_the_stated_detail(self) -> None:
        for name, spec in {"absent": {"verdict": "none"}, "empty": {"message": ""}, "blank": {"message": " \n\t "}}.items():
            with self.subTest(name=name):
                self.script(**spec)
                outcome = self.run_tester(self.bridge_for(job_id=f"job-{name}"))
                self.assertEqual(outcome["outcome"], ct.FAILED)
                self.assertIn("no final message", outcome["detail"])

    def test_precedence_is_timeout_then_limit_then_exit_then_message(self) -> None:
        ok = review.RunResult(0, False, b"", b"", 1, 1)
        limit = review.RolloutScan(limit_error=True, limit_detail="d")
        self.assertEqual(ct.classify(review.RunResult(None, True, b"", b"", 1, 1), limit, "m", 5)[0], ct.TIMEOUT)
        self.assertEqual(ct.classify(review.RunResult(2, False, b"", b"", 1, 1), limit, "m", 5), (ct.LIMIT, "d"))
        self.assertEqual(ct.classify(review.RunResult(2, False, b"o", b"e", 1, 1), review.RolloutScan(), "m", 5)[0], ct.FAILED)
        self.assertEqual(ct.classify(ok, review.RolloutScan(limit=True), "m", 5), (ct.RAN, ""))
        self.assertEqual(ct.classify(ok, review.RolloutScan(), "", 5)[0], ct.FAILED)


class LaunchTest(TesterFixture):
    def test_the_prompt_on_standard_input_is_the_bridges_prompt(self) -> None:
        self.script(message="ok")
        bridge = self.bridge_for()
        with mock.patch.object(review, "run_bounded", wraps=review.run_bounded) as bounded:
            self.run_tester(bridge, timeout=30)
        self.assertEqual(bounded.call_args.kwargs["stdin_text"], cb.prompt(bridge, FLOWS, timeout_seconds=30))
        self.assertEqual(bounded.call_args.kwargs["cwd"], bridge.clone)
        self.assertEqual(bridge.built.argv[-1], "-")

    def test_a_routed_model_and_effort_run_and_reach_the_exec(self) -> None:
        self.script(message="ok")
        bridge = self.bridge_for(model="gpt-test", effort="low")
        self.assertEqual((bridge.built.model, bridge.built.effort), ("gpt-test", "low"))
        self.assertEqual(self.run_tester(bridge)["outcome"], ct.RAN)
        entry = self.execs()[0]
        self.assertEqual((entry["model"], entry["effort"]), ("gpt-test", "low"))

    def test_a_built_whose_model_or_effort_does_not_reproduce_its_argv_is_refused_before_any_exec(self) -> None:
        self.script(message="ok")
        bridge = self.bridge_for(model="gpt-test", effort="low")
        for change in ({"model": review.DEFAULT_MODEL}, {"effort": review.DEFAULT_EFFORT}):
            with self.subTest(change=change):
                bad = cb.dataclasses.replace(bridge, built=cb.dataclasses.replace(bridge.built, **change))
                with self.assertRaises(ReviewLaunchRefusedError):
                    self.run_tester(bad)
        self.assertEqual(self.execs(), [])

    def test_a_binary_other_than_the_one_the_bridge_was_built_with_is_refused_before_any_control(self) -> None:
        self.script(message="ok")
        bridge = self.bridge_for()
        other = self.binary.parent / "other-codex"
        other.symlink_to(self.binary)
        with mock.patch.object(cs, "check_argv") as argv_check, self.assertRaises(ReviewLaunchRefusedError):
            ct.run_tester(bridge, FLOWS, binary=other, operator_home=self.operator, timeout=20,
                          system_dir=self.system, run=self.controls(bridge))
        argv_check.assert_not_called()
        self.assertEqual(self.execs(), [])

    def test_a_built_with_no_recorded_binary_is_not_constrained(self) -> None:
        self.script(message="ok")
        bridge = self.bridge_for()
        bare = cb.dataclasses.replace(bridge, built=cb.dataclasses.replace(bridge.built, binary=""))
        self.assertEqual(self.run_tester(bare)["outcome"], ct.RAN)

    def test_each_refused_control_starts_no_exec_and_propagates(self) -> None:
        self.script(message="ok")
        bridge = self.bridge_for()
        altered = bridge.built.argv[:-1] + ("--dangerously-bypass-approvals-and-sandbox", "-")
        cases = {
            "argv": lambda: self.run_tester(cb.dataclasses.replace(
                bridge, built=cb.dataclasses.replace(bridge.built, argv=altered))),
            "launch": lambda: self.run_tester(bridge, run=lambda c, **kw: result(b"zz   enabled   stdio\n")),
            "composed": lambda: self.run_tester(
                bridge, run=lambda c, **kw: result(NO_SERVERS if c[1] == "mcp" else FEATURES if c[1] == "features"
                                                   else b'{"id": 2, "result": {}}\n')),
        }
        for name, call in cases.items():
            with self.subTest(name=name), self.assertRaises(ReviewLaunchRefusedError):
                call()
        self.assertEqual(self.execs(), [])

    def test_the_controls_run_in_order(self) -> None:
        self.script(message="ok")
        bridge = self.bridge_for()
        order = []
        with mock.patch.object(cs, "check_argv", side_effect=lambda *a, **k: order.append("argv")), \
                mock.patch.object(cs, "check_launch", side_effect=lambda *a, **k: order.append("launch")), \
                mock.patch.object(cs, "check_composed", side_effect=lambda *a, **k: order.append("composed")):
            self.run_tester(bridge)
        self.assertEqual(order, ["argv", "launch", "composed"])



class SettleTest(TesterFixture):
    def copy(self, bridge: cb.Bridge) -> Path:
        return bridge.built.layout.codex_home / "auth.json"

    def test_a_rotated_credential_is_copied_back_and_one_not_rotated_is_unchanged(self) -> None:
        self.script(message="m", refresh_auth=True)
        outcome = self.run_tester(self.bridge_for())
        self.assertEqual(outcome["credentials"], "copied")
        self.assertIn("rotated-", (self.operator / "auth.json").read_text())
        self.script(message="m")
        self.assertEqual(self.run_tester(self.bridge_for(job_id="job2"))["credentials"], "unchanged")

    def test_an_operator_file_changed_meanwhile_is_reported_and_not_written(self) -> None:
        self.script(message="m", refresh_auth=True)
        bridge = self.bridge_for()
        (self.operator / "auth.json").write_text('{"token": "changed"}')
        self.assertEqual(self.run_tester(bridge)["credentials"], "operator-changed")
        self.assertEqual((self.operator / "auth.json").read_text(), '{"token": "changed"}')

    def test_the_credential_copy_is_gone_after_a_run_a_refusal_and_a_deadline(self) -> None:
        self.script(message="m")
        bridge = self.bridge_for()
        self.run_tester(bridge)
        self.assertFalse(self.copy(bridge).exists())
        refused = self.bridge_for(job_id="job2")
        with self.assertRaises(ReviewLaunchRefusedError):
            self.run_tester(refused, run=lambda c, **kw: result(b"zz   enabled   stdio\n"))
        self.assertFalse(self.copy(refused).exists())
        self.script(verdict="hang")
        hung = self.bridge_for(job_id="job3")
        self.assertEqual(self.run_tester(hung, timeout=2)["outcome"], ct.TIMEOUT)
        self.assertFalse(self.copy(hung).exists())

    def test_a_copy_back_failure_is_raised_only_when_nothing_else_is_in_flight(self) -> None:
        self.script(message="m")
        with mock.patch.object(review, "copy_back_credentials", side_effect=OSError("copy-back")):
            with self.assertRaisesRegex(OSError, "copy-back"):
                self.run_tester(self.bridge_for())
            refused = self.bridge_for(job_id="job2")
            with self.assertRaises(ReviewLaunchRefusedError):  # the refusal wins; the copy is still removed
                self.run_tester(refused, run=lambda c, **kw: result(b"zz   enabled   stdio\n"))
            self.assertFalse(self.copy(refused).exists())

    def test_a_failed_copy_back_does_not_skip_the_removal_of_the_copy(self) -> None:
        self.script(message="m")
        bridge = self.bridge_for()
        with mock.patch.object(review, "copy_back_credentials", side_effect=OSError("x")):
            with self.assertRaises(OSError):
                self.run_tester(bridge)
        self.assertFalse(self.copy(bridge).exists())

    def test_the_readings_and_a_subagents_tokens_are_in_the_result(self) -> None:
        self.script(message="m", subagent=777)
        outcome = self.run_tester(self.bridge_for())
        self.assertEqual({r["window"] for r in outcome["readings"]}, {"five_hour", "weekly"})
        self.assertEqual([s["total_tokens"] for s in outcome["subagents"]], [777])

    def test_the_result_holds_every_key_of_the_claude_launch(self) -> None:
        self.script(message="m")
        outcome = self.run_tester(self.bridge_for())
        self.assertEqual(set(outcome), {"outcome", "detail", "tester_message", "readings", "first_readings",
                                        "subagents", "credentials", "telemetry", "exit_code"})
        self.assertEqual((outcome["first_readings"], outcome["telemetry"], outcome["exit_code"]), ([], None, 0))

if __name__ == "__main__":
    unittest.main()