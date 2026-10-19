"""The usage record and its converters (workflow-controller-adapters-1, CP3)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from controller import usage_record as ur
from controller import worker_stream as ws

HERE = Path(__file__).resolve().parent
CONTRACT = HERE / "harness_contract"
REPO_ROOT = HERE.parent

#: A table with prices chosen to make every group's weight distinct.
TABLE = {"version": 7, "currency": "USD",
         "models": {"m": {"input": 1.0, "cache_read": 0.1, "cache_write": 2.0, "output": 10.0}}}


def _results(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            out.append(event)
    return out


def _streams() -> list[Path]:
    return sorted(p for p in [*CONTRACT.glob("*.jsonl"), *(CONTRACT / "claude").glob("stream_*.jsonl")]
                  if _results(p))


def _rollouts(path: Path) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for line in path.read_text().splitlines():
        event = json.loads(line)
        if "rollout" in event:
            groups.setdefault(event["rollout"], []).append(event)
    return [ur.parse_rollout(events) for events in groups.values()]


class ClaudeConverterTest(unittest.TestCase):
    def test_a_record_per_captured_stream_equals_the_telemetry_block(self) -> None:
        streams = _streams()
        self.assertGreaterEqual(len(streams), 3)
        for path in streams:
            results = _results(path)
            block = ws.session_telemetry(results)
            record = ur.from_claude_results(results)
            with self.subTest(path.name):
                tokens = block["tokens"]
                self.assertEqual(record.input, tokens["input"])
                self.assertEqual(record.cache_read, tokens["cache_read"])
                self.assertEqual(record.cache_write, tokens["cache_creation"])
                self.assertEqual(record.output, tokens["output"])
                self.assertEqual(record.turns, block["turns"])
                self.assertEqual(record.wall_ms, block["duration_ms"])
                self.assertEqual(record.cost, block["cost_usd"])
                self.assertEqual(record.cost_source, ur.REPORTED if block["cost_usd"] is not None else ur.UNKNOWN)

    def test_reasoning_is_the_thinking_tokens_field(self) -> None:
        record = ur.from_claude_results(_results(CONTRACT / "claude" / "stream_opus_high_run6.jsonl"))
        self.assertEqual((record.reasoning, record.output), (211, 716))
        self.assertEqual(record.quality["reasoning"], ur.REPORTED)
        two = ur.from_claude_results(_results(CONTRACT / "job_2857a730_two_results.jsonl"))
        self.assertEqual(two.reasoning, 45729 + 1426)

    def test_absent_reasoning_is_unknown_and_the_block_is_unchanged(self) -> None:
        results = _results(CONTRACT / "claude" / "stream_opus_high_run6.jsonl")
        before = json.dumps(ws.session_telemetry(results), sort_keys=True)
        stripped = json.loads(json.dumps(results))
        for event in stripped:
            del event["usage"]["output_tokens_details"]
        record = ur.from_claude_results(stripped)
        self.assertIsNone(record.reasoning)
        self.assertEqual(record.quality["reasoning"], ur.UNKNOWN)
        self.assertEqual(json.dumps(ws.session_telemetry(results), sort_keys=True), before)
        self.assertNotIn("reasoning", json.dumps(ws.session_telemetry(results)["tokens"]))

    def test_a_stream_with_no_result_invents_nothing(self) -> None:
        record = ur.from_claude_results([])
        self.assertTrue(all(getattr(record, g) is None for g in ur.GROUPS))
        self.assertEqual(set(record.quality.values()), {ur.UNKNOWN})
        self.assertIsNone(record.cost)
        self.assertEqual(record.cost_source, ur.UNKNOWN)

    def test_the_model_comes_from_the_single_model_usage_key(self) -> None:
        record = ur.from_claude_results(_results(CONTRACT / "claude" / "stream_opus_high_run6.jsonl"))
        self.assertEqual((record.harness, record.provider, record.model), ("claude-code", "anthropic", "claude-opus-5-5"))

    def test_rebuilt_identically_from_the_same_stream(self) -> None:
        results = _results(CONTRACT / "job_2857a730_two_results.jsonl")
        self.assertEqual(ur.from_claude_results(results).to_dict(), ur.from_claude_results(results).to_dict())


class CodexConverterTest(unittest.TestCase):
    def test_the_captured_excerpt_gives_derived_figures(self) -> None:
        events = [json.loads(line) for line in (CONTRACT / "codex" / "rollout_excerpt.jsonl").read_text().splitlines()]
        record = ur.from_codex_rollouts([ur.parse_rollout(events)], model="m", table=TABLE)
        self.assertEqual((record.input, record.cache_read, record.cache_write, record.output, record.reasoning),
                         (10436, 0, 0, 5, 0))
        self.assertEqual(set(record.quality.values()), {ur.DERIVED})
        self.assertEqual((record.harness, record.provider), ("codex", "openai"))
        self.assertAlmostEqual(record.cost, 10436 * 1.0 + 5 * 10.0)
        self.assertEqual(record.cost_source, ur.DERIVED)

    def test_a_rollout_without_token_count_gives_all_unknown(self) -> None:
        record = ur.from_codex_rollouts([ur.parse_rollout([{"type": "session_meta", "payload": {"source": "exec"}}])],
                                        model="m", table=TABLE)
        self.assertTrue(all(getattr(record, g) is None for g in ur.GROUPS))
        self.assertEqual(set(record.quality.values()), {ur.UNKNOWN})
        self.assertIsNone(record.cost)

    def test_the_multi_rollout_sample_sums_roots_and_keeps_the_subagent_apart(self) -> None:
        path = CONTRACT / "codex" / "rollouts_multi.jsonl"
        self.assertIn("DERIVED", path.read_text().splitlines()[0])
        rollouts = _rollouts(path)
        self.assertEqual(sorted(r["subagent"] for r in rollouts), [False, False, True])
        record = ur.from_codex_rollouts(rollouts, model="m", table=TABLE)
        # last token_count of each root: (10436-4000)+(8000-2000), cached 4000+2000, output 205+300, reasoning 60+100
        self.assertEqual((record.input, record.cache_read, record.cache_write, record.output, record.reasoning),
                         (12436, 6000, 0, 505, 160))
        self.assertEqual(record.subagent_total_tokens, 3100)
        # the four priced groups once each; the reasoning count is inside output and not charged again
        self.assertAlmostEqual(record.cost, 12436 * 1.0 + 6000 * 0.1 + 0 * 2.0 + 505 * 10.0)

    def test_a_positive_cache_write_keeps_the_figure_and_leaves_input_and_cost_unknown(self) -> None:
        usage = {"input_tokens": 100, "cached_input_tokens": 10, "cache_write_input_tokens": 30,
                 "output_tokens": 5, "reasoning_output_tokens": 2, "total_tokens": 105}
        record = ur.from_codex_rollouts([{"subagent": False, "usage": usage}], model="m", table=TABLE)
        self.assertEqual(record.cache_write, 30)
        self.assertIsNone(record.input)
        self.assertEqual(record.quality["input"], ur.UNKNOWN)
        self.assertIsNone(record.cost)
        self.assertEqual(record.cost_source, ur.UNKNOWN)

    def test_a_cached_figure_above_input_leaves_input_unknown(self) -> None:
        usage = {"input_tokens": 10, "cached_input_tokens": 20, "cache_write_input_tokens": 0,
                 "output_tokens": 5, "reasoning_output_tokens": 0, "total_tokens": 15}
        record = ur.from_codex_rollouts([{"subagent": False, "usage": usage}], model="m", table=TABLE)
        self.assertIsNone(record.input)
        self.assertEqual(record.cache_read, 20)
        self.assertIsNone(record.cost)

    def test_a_model_outside_the_table_has_no_cost(self) -> None:
        events = [json.loads(line) for line in (CONTRACT / "codex" / "rollout_excerpt.jsonl").read_text().splitlines()]
        record = ur.from_codex_rollouts([ur.parse_rollout(events)], model="other", table=TABLE)
        self.assertIsNone(record.cost)
        self.assertEqual(record.cost_source, ur.UNKNOWN)
        self.assertEqual(record.quality["input"], ur.DERIVED)


class PriceTableTest(unittest.TestCase):
    def test_the_table_version_and_currency_are_kept_in_the_record(self) -> None:
        record = ur.from_claude_results([], table=TABLE)
        self.assertEqual((record.price_table_version, record.currency), (7, "USD"))

    def test_a_changed_table_does_not_alter_a_built_record(self) -> None:
        usage = {"input_tokens": 100, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                 "output_tokens": 5, "reasoning_output_tokens": 0, "total_tokens": 105}
        record = ur.from_codex_rollouts([{"subagent": False, "usage": usage}], model="m", table=TABLE)
        before = record.to_dict()
        table = json.loads(json.dumps(TABLE))
        table["version"], table["models"]["m"]["input"] = 8, 99.0
        self.assertEqual(record.to_dict(), before)
        self.assertNotEqual(ur.from_codex_rollouts([{"subagent": False, "usage": usage}], model="m",
                                                   table=table).to_dict(), before)

    def test_the_shipped_table_is_versioned(self) -> None:
        table = ur.load()
        self.assertEqual((table["version"], table["currency"]), (1, "USD"))
        self.assertIsInstance(table["models"], dict)

    def test_the_table_loads_from_a_copied_package_layout(self) -> None:
        # a copy of the package imported on its own: the default resource path resolves beside the module
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(REPO_ROOT / "controller", Path(tmp) / "controller", ignore=shutil.ignore_patterns("__pycache__"))
            code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from controller import usage_record as u; "
                    "print(u.__file__); print(json.dumps(u.load(), sort_keys=True))")
            done = subprocess.run([sys.executable, "-I", "-c", code, tmp], capture_output=True, text=True, check=True)
        copied, table = done.stdout.splitlines()
        self.assertTrue(copied.startswith(tmp))
        self.assertEqual(json.loads(table), ur.load())

    def test_the_table_is_package_data(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text()
        self.assertIn('"prices.json"', text)


if __name__ == "__main__":
    unittest.main()
