"""Reviewer independence (H10): the mixed-lane stop names the colliding pair.

The stop itself is ``review_mode.choose``'s (H2); only its text changes."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, review_mode, routing, worker, review  # noqa: E402
from controller.review_mode import GATE, MODE_MANUAL  # noqa: E402
from tests import test_review_job as rj  # noqa: E402
from tests.test_review_mode import MODES, WORKER_ROLES, codex_workers, reviewer  # noqa: E402

LANE = Path(__file__).resolve().parent / "fixtures" / "lane_routing.json"
STAGES = ("plan", "implementation")


def choose_review(options, reviewer_options, mode, stage, signal_reader=None):
    """``job._choose_review`` at a gate of ``stage``, with the gate lookup patched."""
    reader = signal_reader or mock.Mock(return_value=None)
    with mock.patch.object(job, "_external_review_stage", return_value=(stage, "external")), \
            mock.patch.object(job, "_review_mode_signal", reader):
        chosen = job._choose_review(
            None, None, reviewer_options, {"mode": mode, "source": "cli"}, route_options=options)
    return chosen[1], reader


class UnchangedLanesTest(unittest.TestCase):
    """D6: nothing that loads today changes."""

    def test_one_worker_family_yields_what_choose_yields(self) -> None:
        lanes = (routing.NO_OVERRIDES, routing.RoutingOptions(config=routing.load_routing_config(LANE)))
        for options in lanes:
            workers = review_mode.worker_families(options)
            self.assertEqual(len(workers), 1)
            for reviewer_options in (None, reviewer("claude"), reviewer("codex")):
                for mode in MODES:
                    for stage in STAGES:
                        with mock.patch.object(review_mode, "mixed_lane_reason") as text:
                            choice, _ = choose_review(options, reviewer_options, mode, stage)
                        text.assert_not_called()
                        self.assertEqual(choice, review_mode.choose(mode, reviewer_options, stage, None, workers))


class MixedLaneTextTest(unittest.TestCase):
    def setUp(self) -> None:
        patch, self.options = codex_workers([routing.REVIEW_PLAN])
        self.addCleanup(patch.stop)

    def test_the_lane_is_mixed(self) -> None:
        self.assertEqual(review_mode.worker_families(self.options), frozenset({"anthropic", "openai"}))

    def test_only_the_reason_differs_from_choose(self) -> None:
        workers = review_mode.worker_families(self.options)
        for mode in (review_mode.MODE_AUTO, review_mode.MODE_CROSS_MODEL, review_mode.MODE_SINGLE_PROVIDER):
            choice, reader = choose_review(self.options, reviewer("codex"), mode, "plan")
            plain = review_mode.choose(mode, reviewer("codex"), "plan", None, workers)
            self.assertEqual(choice.kind, "refused")
            self.assertEqual((choice.harness, choice.lowered, choice.signal),
                             (plain.harness, plain.lowered, plain.signal))
            self.assertNotEqual(choice.reason, plain.reason)
            self.assertTrue(choice.reason.startswith(
                "the worker roles are routed to harnesses of more than one family"))
            for word in ("review-plan", "codex", "openai", "reviewer.harness", "--review-mode manual"):
                self.assertIn(word, choice.reason)
            reader.assert_not_called()

    def test_manual_is_the_ordinary_gate_and_never_calls_the_text(self) -> None:
        with mock.patch.object(review_mode, "mixed_lane_reason") as text:
            choice, _ = choose_review(self.options, reviewer("codex"), MODE_MANUAL, "plan")
        self.assertEqual(choice, GATE)
        text.assert_not_called()

    def test_each_stage_reports_its_own_pair(self) -> None:
        collision = review_mode.mixed_lane_reason(self.options, reviewer("codex"), "plan")
        self.assertIn("of one family", collision)
        self.assertIn("review-plan runs on codex (openai)", collision)
        distinct = review_mode.mixed_lane_reason(self.options, reviewer("codex"), "implementation")
        self.assertIn("review-implementation runs on claude-code (anthropic)", distinct)
        self.assertIn("that pair is distinct", distinct)
        self.assertNotIn("of one family and", distinct)

    def test_an_unset_reviewer_names_the_local_role_only(self) -> None:
        text = review_mode.mixed_lane_reason(self.options, None, "plan")
        self.assertIn("review-plan runs on codex (openai); reviewer.harness is unset", text)
        self.assertNotIn("that pair is distinct", text)
        self.assertNotIn("of one family and", text)

    def test_an_unregistered_reviewer_is_named_as_such(self) -> None:
        text = review_mode.mixed_lane_reason(self.options, reviewer("nosuch"), "plan")
        self.assertIn("reviewer.harness is nosuch (not a registered reviewer)", text)
        self.assertNotIn("None", text)


class MixedLaneJobTest(rj._ReviewCase):
    """Through ``job``: the report and a real ``execute_step``."""

    def setUp(self) -> None:
        super().setUp()
        patch, self.options = codex_workers([routing.REVIEW_PLAN])
        self.addCleanup(patch.stop)

    def step(self, mode):
        double = self.double = rj.LaunchDouble(self, self.life)
        forbidden = mock.Mock(side_effect=AssertionError("no Claude worker may launch"))
        with mock.patch.object(worker, "launch", forbidden), mock.patch.object(review, "launch_review", double):
            return job.execute_step(
                self.life.inspect(), identity=rj.IDENTITY, runtime=self.runtime_root, reviewer=self.reviewer,
                routing=self.options, review_mode_options={"mode": mode, "source": "cli"})

    def test_the_step_stops_with_the_new_text_and_launches_nothing(self) -> None:
        record = self.step(review_mode.MODE_CROSS_MODEL)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])
        text = json.dumps(record)
        self.assertIn(review_mode.REVIEW_MODE_UNAVAILABLE, text)
        self.assertIn("review-plan runs on codex (openai)", text)

    def test_manual_reaches_the_ordinary_gate(self) -> None:
        record = self.step(MODE_MANUAL)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(record))
        self.assertEqual(self.double.calls, [])

    def test_explain_reports_the_text_as_the_reason(self) -> None:
        from controller import protocol_decision, target_state
        target = self.life.inspect()
        item = target_state.select_work_item(target_state.read(target), work_item_id=rj.WID)
        decision, _ = protocol_decision.decide_after_preflight(target, item)
        report = job.review_mode_report(self.runtime_root, target, rj.WID, decision, self.reviewer,
                                        {"mode": "auto", "source": "cli"}, route_options=self.options)
        self.assertEqual(report["choice"], "refused")
        self.assertIn("review-plan runs on codex", report["reason"])
        manual = job.review_mode_report(self.runtime_root, target, rj.WID, decision, self.reviewer,
                                        {"mode": MODE_MANUAL, "source": "cli"}, route_options=self.options)
        self.assertEqual(manual["choice"], "gate")


if __name__ == "__main__":
    unittest.main()
