"""`workflow-controller-codex-worker-job-record` CP1: the pure record helpers for a Codex worker run
(`controller.codex_worker_job`). Hand-built `WorkerResult` values; no process, no file.
"""

from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_worker, codex_worker_job as cwj, routing, usage_record, worker  # noqa: E402

SUBTYPES = (codex_worker.SUBTYPE_SUCCESS, codex_worker.SUBTYPE_TIMEOUT, codex_worker.SUBTYPE_SIGNAL,
            codex_worker.SUBTYPE_LIMIT, codex_worker.SUBTYPE_EXIT, codex_worker.SUBTYPE_NO_MESSAGE,
            codex_worker.SUBTYPE_BREACH)
ROUTE = routing.Route(model="gpt-test", effort="high", single_agent=False)
USAGE = {"input_tokens": 1000, "cached_input_tokens": 400, "cache_write_input_tokens": 0, "output_tokens": 50,
         "reasoning_output_tokens": 10, "total_tokens": 1050}


def make_result(subtype: str = "success", *, rollouts: list | None = None, stdout: str = "out", stderr: str = "err",
                duration_ms: int = 1234) -> worker.WorkerResult:
    outcome = worker.SUCCESS if subtype == "success" else worker.FAILURE
    diagnosis = {"harness": "codex", "readings": [], "subagents": [], "usage_rollouts": rollouts or [],
                 "credentials": None, "breach": [], "detail": "", "subtype": subtype}
    return worker.WorkerResult(
        outcome=outcome, returncode=0, session_id=None, is_error=outcome != worker.SUCCESS, subtype=subtype,
        terminal_reason=None, stop_reason=None, result="done", num_turns=None, permission_denials=None,
        total_cost_usd=None, duration_ms=duration_ms, stdout=stdout, stderr=stderr, raw_json=None,
        stream_diagnosis=diagnosis, owned_processes_seen=None)


class LaunchOptionsTest(unittest.TestCase):
    def options(self) -> dict:
        return cwj.launch_options(runtime_root=Path("/r"), job_id="j1", operator_home=Path("/h/.codex"),
                                  cwd=Path("/r/worker/j1/worktree"), timeout=None, on_spawn=lambda pid, pgid: None,
                                  route=ROUTE)

    def test_exactly_the_eight_keys_of_the_plan(self) -> None:
        self.assertEqual(set(self.options()), {"runtime_root", "job_id", "operator_home", "cwd", "timeout", "on_spawn",
                                               "model", "effort"})

    def test_model_and_effort_are_the_routes(self) -> None:
        options = self.options()
        self.assertEqual((options["model"], options["effort"]), ("gpt-test", "high"))

    def test_a_missing_route_value_is_passed_as_routed(self) -> None:
        inherit = routing.Route(model=None, effort=None, single_agent=False)
        options = cwj.launch_options(runtime_root=Path("/r"), job_id="j", operator_home=Path("/h"), cwd=Path("/c"),
                                     timeout=5.0, on_spawn=lambda pid, pgid: None, route=inherit)
        self.assertEqual((options["model"], options["effort"], options["timeout"]), (None, None, 5.0))

    def test_binds_to_the_launchers_own_signature(self) -> None:
        inspect.signature(codex_worker.launch_codex_worker).bind("/x", **self.options())


class StreamTextsTest(unittest.TestCase):
    def test_each_of_the_seven_subtypes_gives_the_decoded_streams(self) -> None:
        for subtype in SUBTYPES:
            with self.subTest(subtype=subtype):
                result = make_result(subtype, stdout=f"stdout of {subtype}", stderr=f"stderr of {subtype}")
                self.assertEqual(cwj.stream_texts(result), (f"stdout of {subtype}", f"stderr of {subtype}"))

    def test_empty_streams_are_empty_text(self) -> None:
        self.assertEqual(cwj.stream_texts(make_result(stdout="", stderr="")), ("", ""))


class UsageBuilderTest(unittest.TestCase):
    def test_one_root_rollout_builds_a_derived_record_with_the_tokens(self) -> None:
        record = cwj.usage_builder(make_result(rollouts=[{"subagent": False, "usage": USAGE}]), ROUTE)()
        self.assertEqual((record.input, record.cache_read, record.output, record.reasoning), (600, 400, 50, 10))
        self.assertEqual((record.harness, record.model, record.wall_ms), ("codex", "gpt-test", 1234))
        self.assertEqual(record.quality["input"], usage_record.DERIVED)

    def test_no_rollout_builds_the_unknown_record(self) -> None:
        record = cwj.usage_builder(make_result(rollouts=[]), ROUTE)()
        self.assertEqual(set(record.quality.values()), {usage_record.UNKNOWN})
        self.assertIsNone(record.cost)

    def test_a_result_with_no_diagnosis_builds_the_unknown_record(self) -> None:
        bare = make_result()
        bare = type(bare)(**{**bare.__dict__, "stream_diagnosis": None})
        self.assertEqual(set(cwj.usage_builder(bare, ROUTE)().quality.values()), {usage_record.UNKNOWN})

    def test_nothing_is_built_until_the_builder_is_called(self) -> None:
        cwj.usage_builder(make_result(rollouts=[{"bad": 1}]), ROUTE)

    def test_a_builder_that_raises_propagates_unchanged(self) -> None:
        class Broken(dict):  # a Mapping whose read fails: a non-Mapping rollout is skipped, not an error (CU3c D6)
            def get(self, key, default=None):
                raise AttributeError(key)

        build = cwj.usage_builder(make_result(rollouts=[Broken()]), ROUTE)
        with self.assertRaises(AttributeError):
            build()


def reading(window: str, percent: float, resets_at: float) -> dict:
    return {"provider": "codex", "window": window, "percent": percent, "resets_at": resets_at, "observed_at": 1.0,
            "source": "t"}


class LimitFactTest(unittest.TestCase):
    def test_a_window_at_100_percent_gives_the_fact_with_a_null_rate_limit_type(self) -> None:
        fact = cwj.limit_fact([reading("five_hour", 100.0, 5000), reading("weekly", 40.0, 9000)])
        self.assertEqual(fact, {"resets_at": 5000, "window": "five_hour", "rate_limit_type": None})

    def test_over_100_percent_counts(self) -> None:
        self.assertEqual(cwj.limit_fact([reading("weekly", 103.0, 7000)])["window"], "weekly")

    def test_of_several_full_windows_the_latest_reset_wins(self) -> None:
        fact = cwj.limit_fact([reading("weekly", 100.0, 5000), reading("five_hour", 100.0, 6000)])
        self.assertEqual((fact["window"], fact["resets_at"]), ("five_hour", 6000))

    def test_on_a_tie_the_weekly_window_wins_in_either_order(self) -> None:
        both = [reading("five_hour", 100.0, 5000), reading("weekly", 100.0, 5000)]
        for readings in (both, both[::-1]):
            self.assertEqual(cwj.limit_fact(readings)["window"], "weekly")

    def test_no_full_window_non_finite_reset_or_no_readings_gives_none(self) -> None:
        self.assertIsNone(cwj.limit_fact([reading("five_hour", 99.9, 5000)]))
        self.assertIsNone(cwj.limit_fact([reading("five_hour", 100.0, float("inf"))]))
        self.assertIsNone(cwj.limit_fact([reading("five_hour", 100.0, float("nan"))]))
        self.assertIsNone(cwj.limit_fact([{"window": "five_hour", "percent": 100.0}]))
        self.assertIsNone(cwj.limit_fact([]))
        self.assertIsNone(cwj.limit_fact(None))


class ModuleShapeTest(unittest.TestCase):
    def test_the_module_imports_nothing_from_job_and_writes_nothing(self) -> None:
        source = (REPO_ROOT / "controller" / "codex_worker_job.py").read_text("utf-8")
        tree = ast.parse(source)
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names}
        self.assertNotIn("job", imported)
        self.assertEqual(imported, {"Callable", "Iterable", "Mapping", "Path", "Any", "usage", "usage_record", "worker", "annotations"})
        for forbidden in ("open(", ".write", "subprocess", "Popen"):
            self.assertNotIn(forbidden, source)



class WorkflowMetadataOptionTest(unittest.TestCase):
    """``workflow-controller-h4f`` D4b: the ninth launch option is present only when true."""

    def options(self, **kw) -> dict:
        return cwj.launch_options(runtime_root=Path("/r"), job_id="j1", operator_home=Path("/h/.codex"),
                                  cwd=Path("/r/worker/j1/worktree"), timeout=None, on_spawn=lambda pid, pgid: None,
                                  route=ROUTE, **kw)

    def test_the_default_set_stays_exactly_the_eight(self) -> None:
        self.assertEqual(len(self.options()), 8)
        self.assertEqual(set(self.options(workflow_metadata=False)), set(self.options()))

    def test_the_flag_adds_the_ninth_key_and_nothing_else(self) -> None:
        flagged = self.options(workflow_metadata=True)
        self.assertEqual(set(flagged) - set(self.options()), {"workflow_metadata"})
        self.assertIs(flagged["workflow_metadata"], True)
        inspect.signature(codex_worker.launch_codex_worker).bind("/x", **flagged)


if __name__ == "__main__":
    unittest.main()
