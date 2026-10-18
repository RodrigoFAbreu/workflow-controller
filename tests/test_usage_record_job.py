"""The usage record on job records (workflow-controller-adapters-1, CP4; D6, D8, D9, D9a).

``usage_record`` is written beside ``telemetry`` on the two worker completion paths and, for a
review job, by the one terminal write on every status in which an attempt ran. It only adds: no
status, outcome, printed line or existing block changes, and a build that raises is caught at
the job boundary as ``usage_record_failed``.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, identity, job, review, usage_record  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests import (fixtures, test_cli, test_job, test_review_claude, test_review_job, test_review_mode,  # noqa: E402
                   test_review_same_provider)
from tests.test_job import _telemetry_end_turn  # noqa: E402

CONTRACT = Path(__file__).resolve().parent / "harness_contract" / "codex"

#: The top-level keys that differ between two runs of the same scenario, or that the record adds.
_VOLATILE = ("job_id", "created_at", "updated_at", "event_seq", "ownership_tag", "worker_process", "worker_anchor",
             "worker_streams", "worker_state", "ending_offset", "lifecycle_lock", "run_id", "usage_record",
             "usage_record_failed")


def _stable(record: dict) -> dict:
    review_block = {k: v for k, v in (record.get("review") or {}).items() if k not in ("launched_at",)}
    return {**{k: v for k, v in record.items() if k not in _VOLATILE}, "review": review_block}


def _multi_rollouts() -> list[dict]:
    """The derived multi-rollout sample, as ``review.scan_rollouts`` reads it from a home."""
    groups: dict[str, list[dict]] = {}
    for line in (CONTRACT / "rollouts_multi.jsonl").read_text().splitlines():
        event = json.loads(line)
        if "rollout" in event:
            groups.setdefault(event["rollout"], []).append(event)
    return [usage_record.parse_rollout(events) for events in groups.values()]


def _thinking_turn(index: int, turns: int, thinking: int) -> dict:
    turn = _telemetry_end_turn(index, turns)
    turn["usage"] = {**turn["usage"], "output_tokens_details": {"thinking_tokens": thinking}}
    return turn


class ScanKeepsRolloutFiguresTest(unittest.TestCase):
    """CP4: ``RolloutScan`` carries each rollout's last ``token_count`` figures, additively."""

    def _home(self, rollouts: list[tuple[str, list[dict]]]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        day = home / "sessions" / "2026" / "10" / "18"
        day.mkdir(parents=True)
        for name, events in rollouts:
            (day / f"rollout-{name}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        return home

    def _sample(self) -> list[tuple[str, list[dict]]]:
        groups: dict[str, list[dict]] = {}
        for line in (CONTRACT / "rollouts_multi.jsonl").read_text().splitlines():
            event = json.loads(line)
            if "rollout" in event:
                groups.setdefault(event["rollout"], []).append({k: v for k, v in event.items() if k != "rollout"})
        return list(groups.items())

    def test_every_rollout_is_kept_once_and_the_subagents_list_is_unchanged(self) -> None:
        scan = review.scan_rollouts(self._home(self._sample()))
        self.assertEqual(len(scan.rollouts), 3)
        self.assertEqual(sorted(r["subagent"] for r in scan.rollouts), [False, False, True])
        self.assertEqual(len(scan.subagents), 1)
        self.assertEqual(set(scan.subagents[0]), {"rollout", "total_tokens"})
        record = usage_record.from_codex_rollouts(scan.rollouts, model="m")
        direct = usage_record.from_codex_rollouts(_multi_rollouts(), model="m")
        self.assertEqual(record.to_dict(), direct.to_dict())

    def test_a_rollout_without_token_count_keeps_no_usage(self) -> None:
        home = self._home([("a", [{"type": "session_meta", "payload": {"source": {}}}])])
        self.assertEqual(review.scan_rollouts(home).rollouts, [{"subagent": False, "usage": None}])

    def test_the_end_of_job_scan_stores_the_figures_where_obtained_does_not_copy_them(self) -> None:
        home_root = tempfile.TemporaryDirectory()
        self.addCleanup(home_root.cleanup)
        base = Path(home_root.name)
        scan = review.RolloutScan(rollouts=[{"subagent": False, "usage": {"input_tokens": 1}}])
        result = {"subagents": [], "verdicts": []}
        selected = review.CODEX
        layout = review.JobLayout(base, "j")
        with unittest.mock.patch.object(review, "scan_rollouts", return_value=scan), \
                unittest.mock.patch.object(review, "retain_pending_verdict"), \
                unittest.mock.patch.object(review, "settle_job_dir"):
            review._settle(result, base, layout, base, None, selected)
        self.assertEqual(result["usage_rollouts"], scan.rollouts)
        self.assertNotIn("usage_rollouts", job._obtained(result))
        self.assertNotIn("usage_reasoning", job._obtained({**result, "usage_reasoning": 3}))

    def test_a_claude_attempts_reasoning_is_summed_across_attempts(self) -> None:
        result: dict = {}
        review._add_reasoning(result, None)
        self.assertNotIn("usage_reasoning", result)
        review._add_reasoning(result, 211)
        review._add_reasoning(result, None)
        review._add_reasoning(result, 100)
        self.assertEqual(result["usage_reasoning"], 311)
        self.assertEqual(review.result_reasoning({"usage": {"output_tokens_details": {"thinking_tokens": 7}}}), 7)
        self.assertIsNone(review.result_reasoning({"usage": {}}))
        self.assertIsNone(review.result_reasoning(None))


class _UsageDouble(test_review_job.LaunchDouble):
    """The launch double, leaving the figures the real ``launch_review`` leaves in ``out``."""

    def __init__(self, test, life, *, usage_rollouts=None, usage_reasoning=None, usage_reasoning_failed=None,
                 **scenario) -> None:
        super().__init__(test, life, **scenario)
        self.extra = {k: v for k, v in (("usage_rollouts", usage_rollouts), ("usage_reasoning", usage_reasoning),
                                        ("usage_reasoning_failed", usage_reasoning_failed)) if v is not None}

    def __call__(self, **kw):
        kw["out"].update(self.extra)
        return super().__call__(**kw)


class _JobCase(test_review_job._ReviewCase):
    def run_step(self, **scenario):
        extras = {k: scenario.pop(k) for k in ("usage_rollouts", "usage_reasoning", "usage_reasoning_failed")
                  if k in scenario}
        return self.step(_UsageDouble(self, self.life, **extras, **scenario))


class CodexReviewRecordTest(_JobCase):
    def test_a_completed_review_has_a_record_from_the_scan_and_an_unchanged_review_block(self) -> None:
        record = self.run_step(usage_rollouts=_multi_rollouts())
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        built = record["usage_record"]
        expected = usage_record.from_codex_rollouts(_multi_rollouts(), model="gpt-6.1-sol").to_dict()
        self.assertEqual({k: v for k, v in built.items() if k != "wall_ms"},
                         {k: v for k, v in expected.items() if k != "wall_ms"})
        self.assertEqual((built["harness"], built["model"]), ("codex", "gpt-6.1-sol"))
        self.assertNotIn("usage_record_failed", record)
        self.assertFalse([k for k in record["review"] if k.startswith("usage")])

    def test_every_terminal_status_in_which_an_attempt_ran_has_a_record(self) -> None:
        for name, scenario in (("empty", {"outcome": "empty"}), ("limit", {"outcome": "limit"}),
                               ("mismatch", {"outcome": "mismatch"}), ("timeout", {"outcome": "timeout"}),
                               ("changed", {"outcome": "changed"})):
            with self.subTest(name):
                record = self.run_step(usage_rollouts=_multi_rollouts(), **scenario)
                self.assertIn("usage_record", record, record["status"])

    def test_a_refusal_after_the_spawn_keeps_the_first_runs_figures(self) -> None:
        refusal = ReviewLaunchRefusedError("the retry's re-check refused", evidence={"check": "features"})
        record = self.run_step(usage_rollouts=_multi_rollouts(), **{"raise": refusal})
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["review"]["outcome"], "refused")
        self.assertEqual(record["usage_record"]["input"], usage_record.from_codex_rollouts(
            _multi_rollouts(), model="gpt-6.1-sol").input)

    def test_an_interrupt_after_the_spawn_has_a_record(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            self.run_step(usage_rollouts=_multi_rollouts(), **{"raise": KeyboardInterrupt()})
        [record] = self.records()
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertIn("usage_record", record)

    def test_a_fault_after_the_spawn_has_a_record(self) -> None:
        with self.assertRaises(RuntimeError):
            self.run_step(usage_rollouts=_multi_rollouts(), **{"raise": RuntimeError("boom")})
        [record] = self.records()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertIn("usage_record", record)

    def test_a_job_that_ended_before_any_attempt_has_no_record(self) -> None:
        record = self.run_step(refuse_before_spawn=True)
        self.assertTrue(record["review"]["not_started"])
        self.assertNotIn("usage_record", record)
        self.assertNotIn("usage_record_failed", record)
        with self.assertRaises(KeyboardInterrupt):
            self.run_step(spawn=False, **{"raise": KeyboardInterrupt()})
        self.assertNotIn("usage_record", self.records()[-1])

    def test_rollouts_that_leave_no_figure_give_a_record_with_every_figure_unknown(self) -> None:
        record = self.run_step(usage_rollouts=[{"subagent": False, "usage": None}])
        built = record["usage_record"]
        self.assertTrue(all(built["quality"][g] == "unknown" for g in usage_record.GROUPS))
        self.assertTrue(all(built[g] is None for g in usage_record.GROUPS))
        self.assertIsNone(built["cost"])

    def test_a_job_without_the_key_still_reads(self) -> None:
        # a record from before this release has neither key, and the readers take it as it is
        record = self.run_step(outcome="empty")
        legacy = {k: v for k, v in record.items() if k not in ("usage_record", "usage_record_failed")}
        self.assertTrue(job.validate_record(legacy, managed_repo=self.life.inspect(),
                                            identity=test_review_job.IDENTITY).valid)


class ClaudeReviewRecordTest(test_review_same_provider.SameProviderCase):
    """A same-provider (Claude) review job, run through the lowered-policy path."""

    TELEMETRY = {"version": 1, "results": 2, "cost_usd": 0.5,
                 "tokens": {"input": 30, "output": 700, "cache_creation": 40, "cache_read": 500}}

    def run_claude(self, *, usage_reasoning=None, usage_reasoning_failed=None, **scenario):
        double = _UsageDouble(self, self.life, usage_reasoning=usage_reasoning,
                              usage_reasoning_failed=usage_reasoning_failed, **scenario)
        options = {"mode": "auto", "source": "cli", "same_provider": self.same}
        forbidden = unittest.mock.Mock(side_effect=AssertionError("no Claude worker may launch"))
        with unittest.mock.patch.object(job, "_review_mode_signal", return_value=test_review_mode.signal("lowering_only")), \
                unittest.mock.patch.object(job.worker, "launch", forbidden), \
                unittest.mock.patch.object(review, "launch_review", double):
            return job.execute_step(self.life.inspect(), identity=test_review_job.IDENTITY, runtime=self.runtime_root,
                                    reviewer=None, review_mode_options=options)

    def test_reasoning_and_figures_reach_the_record_while_review_telemetry_is_unchanged(self) -> None:
        record = self.run_claude(telemetry=dict(self.TELEMETRY), usage_reasoning=311)
        built = record["usage_record"]
        self.assertEqual((built["harness"], built["model"]), ("claude-code", "claude-opus-5-5"))
        self.assertEqual((built["input"], built["cache_read"], built["cache_write"], built["output"]),
                         (30, 500, 40, 700))
        self.assertEqual((built["reasoning"], built["quality"]["reasoning"]), (311, "reported"))
        self.assertEqual((built["cost"], built["cost_source"]), (0.5, "reported"))
        self.assertEqual(record["review"]["telemetry"], self.TELEMETRY)

    def test_a_failed_reasoning_reading_changes_nothing_but_the_record(self) -> None:
        failed = self.run_claude(telemetry=dict(self.TELEMETRY), usage_reasoning=311,
                                 usage_reasoning_failed="RuntimeError: injected")
        self.assertNotIn("usage_record", failed)
        self.assertEqual(failed["usage_record_failed"], "RuntimeError: injected")
        self.assertEqual(failed["status"], job.STATUS_FINISHED)
        self.assertEqual(failed["review"]["telemetry"], self.TELEMETRY)
        self.assertNotIn("usage_reasoning_failed", failed["review"])

    def test_absent_reasoning_is_unknown(self) -> None:
        built = self.run_claude(telemetry=dict(self.TELEMETRY))["usage_record"]
        self.assertEqual((built["reasoning"], built["quality"]["reasoning"]), (None, "unknown"))
        self.assertEqual(built["output"], 700)

    def test_a_run_that_printed_no_result_has_a_record_with_every_figure_unknown(self) -> None:
        built = self.run_claude(outcome="empty")["usage_record"]
        self.assertTrue(all(built[g] is None for g in usage_record.GROUPS))


class ReasoningFailureBoundaryTest(unittest.TestCase):
    """A real ``launch_review`` against the fake Claude: a failed reasoning reading leaves the review alone."""

    def setUp(self) -> None:
        # borrow the fake-Claude launch fixture without re-running its own tests
        self.fixture = test_review_claude.FakeLaunchTest("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_a_reasoning_extraction_failure_leaves_the_review_alone_and_is_reported(self) -> None:
        control = self.fixture.launch(job="j-ok")
        with unittest.mock.patch.object(usage_record, "reasoning_tokens", side_effect=RuntimeError("injected")):
            failed = self.fixture.launch(job="j-bad")
        for key in ("outcome", "status", "verdict", "telemetry", "credentials"):
            self.assertEqual(failed[key], control[key], key)
        self.assertEqual(failed["outcome"], "verdict")
        self.assertEqual(failed["usage_reasoning_failed"], "RuntimeError: injected")
        self.assertNotIn("usage_reasoning_failed", control)
        # the refusal paths read the same figure: a refused run keeps its figures and reports the failure
        with unittest.mock.patch.object(usage_record, "reasoning_tokens", side_effect=RuntimeError("injected")):
            self.assertEqual(review.result_reasoning({"usage": {}}), review.UsageReadingFailed("RuntimeError: injected"))


class FailureBoundaryTest(_JobCase):
    def test_an_injected_converter_failure_changes_nothing_else(self) -> None:
        control = self.run_step(outcome="empty", usage_rollouts=_multi_rollouts())
        with unittest.mock.patch.object(usage_record, "from_codex_rollouts", side_effect=RuntimeError("injected")):
            failed = self.run_step(outcome="empty", usage_rollouts=_multi_rollouts())
        self.assertIn("usage_record", control)
        self.assertNotIn("usage_record", failed)
        self.assertEqual(failed["usage_record_failed"], "RuntimeError: injected")
        self.assertEqual(failed["status"], control["status"])
        self.assertEqual(_stable(failed), _stable(control))
        self.assertEqual(failed["reconciliation_evidence"], control["reconciliation_evidence"])
        self.assertTrue(control["reconciliation_evidence"]["message"])  # the comparison above is not of two empties

    def test_keyboard_interrupt_is_not_swallowed(self) -> None:
        def build():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            job._usage_fields(build)


class WorkerRecordTest(test_job._StreamingCase):
    """The two worker completion paths."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(test_job.process_fixtures.reap_recorded_workers, self.runtime_root)

    def _completed(self, turns: list) -> dict:
        record, _writes = self._spied_step(env={"FAKE_CLAUDE_TURNS": json.dumps(turns)}, timeout=60)
        return record

    def test_a_completed_worker_job_has_a_record_equal_to_its_telemetry_block(self) -> None:
        turns = [[{"step": "bash_bg", "id": "a", "seconds": 0.2}, _thinking_turn(0, 3, 40)], [_thinking_turn(1, 2, 5)]]
        record = self._completed(turns)
        block, built = record["telemetry"], record["usage_record"]
        self.assertEqual(built["harness"], "claude-code")
        self.assertEqual((built["input"], built["output"], built["cache_read"], built["cache_write"]),
                         (block["tokens"]["input"], block["tokens"]["output"], block["tokens"]["cache_read"],
                          block["tokens"]["cache_creation"]))
        self.assertEqual(built["cost"], block["cost_usd"])
        self.assertEqual(built["turns"], block["turns"])
        self.assertEqual((built["reasoning"], built["quality"]["reasoning"]), (45, "reported"))
        self.assertNotIn("reasoning", block)

    def test_absent_reasoning_is_unknown(self) -> None:
        record = self._completed([[{"step": "text"}, _telemetry_end_turn(0, 3)]])
        self.assertEqual(record["usage_record"]["quality"]["reasoning"], "unknown")
        self.assertIn("usage_record", record)

    def test_an_injected_failure_changes_nothing_else(self) -> None:
        turns = [[{"step": "text"}, _telemetry_end_turn(0, 3)]]
        baseline = self._completed(turns)
        with unittest.mock.patch.object(usage_record, "from_claude_results", side_effect=RuntimeError("injected")):
            record = self._completed(turns)
        self.assertNotIn("usage_record", record)
        self.assertEqual(record["usage_record_failed"], "RuntimeError: injected")
        keep = lambda r: {k: v for k, v in r.items() if k not in (  # noqa: E731
            "usage_record", "usage_record_failed", "updated_at", "created_at", "job_id", "worker", "worker_process",
            "worker_anchor", "worker_streams", "ownership_tag", "worker_state", "event_seq", "ending_offset",
            "lifecycle_lock", "run_id")}
        wall = lambda r: {**keep(r), "telemetry": {**r["telemetry"], "job_seconds": None, "worker_seconds": None}}  # noqa: E731
        self.assertEqual(wall(record), wall(baseline))  # only the two wall-clock leaves are normalised
        self.assertTrue(baseline["telemetry"]["tokens"]["output"])  # the rest of the telemetry block is compared above
        self.assertEqual(record["status"], baseline["status"])

    def test_a_record_has_a_usage_record_exactly_when_it_has_a_telemetry_block(self) -> None:
        record = self._completed([[{"step": "text"}, _telemetry_end_turn(0, 3)]])
        self.assertEqual("usage_record" in record, "telemetry" in record)


class ReattachRecordTest(test_job.ReattachTelemetryTest):  # inherits the existing tests too
    """The re-attach completion path builds the record beside its telemetry block."""

    def test_the_reattached_record(self) -> None:
        record, _event = self._reattach()
        self.assertIn("usage_record", record)
        built, block = record["usage_record"], record["telemetry"]
        self.assertEqual((built["output"], built["turns"]), (block["tokens"]["output"], block["turns"]))

    def test_an_unreadable_stream_gives_the_failed_key_and_the_failed_block(self) -> None:
        self.stdout.unlink()
        record, _event = self._reattach()
        self.assertTrue(record["telemetry"]["failed"])
        self.assertTrue(record["usage_record_failed"].startswith("FileNotFoundError: "))
        self.assertNotIn("usage_record", record)


class ResumeJsonTest(unittest.TestCase):
    """``resume --json`` prints the durable record, so the new keys show there and only there (D6)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = test_cli._build_managed_target(self.root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        self.manager = fixtures.write_stub_workflow_manager(self.root / "workflow-manager")
        for name in ("pin", "current"):
            patch = unittest.mock.patch.object(identity, name, lambda: test_cli.FAKE_IDENTITY)
            patch.start()
            self.addCleanup(patch.stop)

    def _print(self, record: dict) -> tuple[int, list]:
        args = test_cli._Args(str(self.repo), json_out=True, workflow_manager=str(self.manager))
        buf = io.StringIO()
        with unittest.mock.patch.object(job, "resume", return_value=[dict(record)]), contextlib.redirect_stdout(buf):
            code = cli.cmd_resume(args, self.root / "runtime", test_cli.FAKE_IDENTITY)
        return code, json.loads(buf.getvalue())

    def test_the_keys_print_and_a_record_without_them_prints_exactly_as_before(self) -> None:
        base = {"job_id": "j1", "status": job.STATUS_FINISHED, "reconciled_this_call": True}
        plain_code, plain = self._print(base)
        with_record_code, with_record = self._print({**base, "usage_record": {"input": 1}})
        failed_code, failed = self._print({**base, "usage_record_failed": "RuntimeError: x"})
        self.assertEqual(plain, [{"job_id": "j1", "status": job.STATUS_FINISHED}])
        self.assertEqual(with_record, [{"job_id": "j1", "status": job.STATUS_FINISHED, "usage_record": {"input": 1}}])
        self.assertEqual(failed, [{"job_id": "j1", "status": job.STATUS_FINISHED,
                                   "usage_record_failed": "RuntimeError: x"}])
        self.assertEqual(plain_code, with_record_code)
        self.assertEqual(plain_code, failed_code)


if __name__ == "__main__":
    unittest.main()
