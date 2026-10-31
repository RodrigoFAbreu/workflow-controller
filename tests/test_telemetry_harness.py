"""Tests for the harness in telemetry (``workflow-controller-harness-telemetry``,
H11): the ``harness`` row key, the three harness groupings, and a courier
review record reading its route's harness."""

from __future__ import annotations

import json
import unittest

from controller import observe, telemetry
from tests.test_telemetry import FAILED, _block, _RuntimeCase


class _HarnessCase(_RuntimeCase):
    def route(self, harness=None, **extra) -> dict:
        route = {"role": "review-plan", "model": "opus", "effort": "high", **extra}
        if harness is not None:
            route["harness"] = harness
        return route

    def courier(self, job_id: str, harness=None, **fields) -> dict:
        return self.job(job_id, kind="review", review={"action_id": "plan.review.external"},
                        worker_route=self.route(harness), **fields)

    def by_id(self) -> dict:
        return {r["job_id"]: r for r in telemetry.rows(observe.list_jobs(self.runtime_root))}


class HarnessRowTest(_HarnessCase):
    def test_a_worker_row_reads_its_routes_harness_or_the_worker_adapters(self) -> None:
        no_harness = {k: v for k, v in _block().items() if k != "harness"}
        self.job("j-1", telemetry=no_harness, worker_route=self.route("claude-code"))
        self.job("j-2", telemetry=no_harness, worker_route=self.route("gemini"))
        self.job("j-3", telemetry=no_harness, worker_route=self.route())
        rows = self.by_id()
        self.assertEqual([rows[j]["harness"] for j in ("j-1", "j-2", "j-3")],
                         ["claude-code", "gemini", telemetry.HARNESS])
        self.assertEqual(telemetry.HARNESS, "claude-code")

    def test_a_functional_review_row_reads_claude_code(self) -> None:
        no_harness = {k: v for k, v in _block().items() if k != "harness"}
        self.job("j-f", kind="functional", telemetry=no_harness, worker_route=self.route("claude-code"))
        self.assertEqual(self.by_id()["j-f"]["harness"], "claude-code")

    def test_a_functional_row_reads_the_harness_of_either_tester(self) -> None:
        # A Codex tester's block is a session of no stream (C10g-3c2 D7): its row exists and shows no figures.
        codex = {**_block(results=0), "harness": "codex", "problems": [{"kind": "no_stream", "path": None}]}
        self.job("j-fc", kind="functional", telemetry=codex, worker_route=self.route("codex"))
        self.job("j-fl", kind="functional", telemetry=_block(), worker_route=self.route("claude-code"))
        rows = self.by_id()
        self.assertEqual((rows["j-fc"]["harness"], rows["j-fl"]["harness"]), ("codex", "claude-code"))
        self.assertTrue(telemetry.unavailable(codex))

    def test_a_courier_row_reads_its_routes_harness(self) -> None:
        self.courier("j-codex", "codex")
        self.courier("j-claude", "claude-code")
        self.courier("j-old")
        rows = self.by_id()
        self.assertEqual([rows[j]["harness"] for j in ("j-codex", "j-claude", "j-old")],
                         ["codex", "claude-code", "claude-code"])

    def test_a_recorded_block_that_names_a_harness_wins(self) -> None:
        self.job("j-w", telemetry=_block(harness="recorded"), worker_route=self.route("claude-code"))
        self.assertEqual(self.by_id()["j-w"]["harness"], "recorded")


class HarnessGroupingTest(_HarnessCase):
    def setUp(self) -> None:
        super().setUp()
        self.job("j-1", telemetry=_block(turns=2, cost=1.0), created_at="2026-01-01T00:00:00Z")
        self.job("j-2", telemetry=_block(turns=4, cost=3.0, model="sonnet"), created_at="2026-01-02T00:00:00Z")
        self.courier("j-3", "codex", created_at="2026-01-03T00:00:00Z")
        self.courier("j-4", "codex", created_at="2026-01-04T00:00:00Z")
        self.job("j-5", telemetry=dict(FAILED), created_at="2026-01-05T00:00:00Z")

    def groups(self, by: str) -> list[dict]:
        return telemetry.groups(telemetry.rows(observe.list_jobs(self.runtime_root)), by)

    def test_by_harness_counts_codex_reviews_as_unavailable(self) -> None:
        grouped = {g["key"]["harness"]: g for g in self.groups("harness")}
        self.assertEqual(list(grouped), ["claude-code", "codex"])
        self.assertEqual((grouped["codex"]["jobs"], grouped["codex"]["telemetry_unavailable"]), (2, 2))
        self.assertIsNone(grouped["codex"]["cost_usd"]["total"])
        self.assertEqual((grouped["claude-code"]["jobs"], grouped["claude-code"]["telemetry_unavailable"]), (3, 1))
        self.assertEqual(grouped["claude-code"]["cost_usd"], {"total": 4.0, "mean": 2.0, "jobs": 2})

    def test_by_role_harness_and_role_harness_model(self) -> None:
        self.assertEqual([g["key"] for g in self.groups("role,harness")],
                         [{"role": "review-plan", "harness": "claude-code"},
                          {"role": "review-plan", "harness": "codex"}])
        self.assertEqual([(g["key"], g["jobs"]) for g in self.groups("role,harness,model")],
                         [({"role": "review-plan", "harness": "claude-code", "model": "opus"}, 2),
                          ({"role": "review-plan", "harness": "claude-code", "model": "sonnet"}, 1),
                          ({"role": "review-plan", "harness": "codex", "model": "opus"}, 2)])

    def test_text_and_json_output(self) -> None:
        code, out, _ = self.cli("telemetry", "--by", "harness")
        self.assertEqual(code, 0)
        self.assertIn("grouped by harness", out)
        self.assertIn("harness=codex: 2 job(s), telemetry unavailable 2", out)
        self.assertIn("harness=claude-code: 3 job(s), telemetry unavailable 1", out)
        payload = json.loads(self.cli("--json", "telemetry", "--by", "harness")[1])
        self.assertEqual(payload["by"], "harness")
        self.assertEqual({row["harness"] for row in payload["rows"]}, {"claude-code", "codex"})
        self.assertEqual([g["key"] for g in payload["groups"]], [{"harness": "claude-code"}, {"harness": "codex"}])

    def test_help_lists_the_groupings_and_an_unknown_one_is_a_usage_error(self) -> None:
        code, out, _ = self.cli("telemetry", "--help")
        self.assertEqual(code, 0)
        self.assertIn("role,harness,model", out.replace("\n", " ").replace("  ", " "))
        self.assertEqual(self.cli("telemetry", "--by", "harness,role")[0], 2)


if __name__ == "__main__":
    unittest.main()
