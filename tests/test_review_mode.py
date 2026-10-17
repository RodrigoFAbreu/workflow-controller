"""`workflow-controller-review-modes` CP1: the pure choice of ``review_mode``
(D1-D4) over the real ``gate_policy`` captures of ``tests/verify_captures``,
plus the ``review.mode`` row, the ``--review-mode`` flag and the job's
consultation of ``choose``."""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, job, review, review_mode, worker  # noqa: E402
from tests import test_review_job as rj  # noqa: E402
from controller.review_mode import (  # noqa: E402
    MODE_AUTO, MODE_CROSS_MODEL, MODE_MANUAL, MODE_SINGLE_PROVIDER, choose, read_signal, signal_of_checks,
)

CAPTURES = Path(__file__).resolve().parent / "verify_captures"
STAGES = ("plan", "implementation")
MODES = (MODE_AUTO, MODE_CROSS_MODEL, MODE_SINGLE_PROVIDER, MODE_MANUAL)


def capture(name: str) -> dict:
    return json.loads((CAPTURES / f"{name}.json").read_text())


def signal(name: str) -> review_mode.Signal:
    check = capture(name)
    return read_signal(check["status"], check["detail"])


def reviewer(harness: str) -> dict:
    return {"harness": harness, "model": "m", "effort": "e", "timeout_seconds": 60, "binary": harness}


class CapturesTest(unittest.TestCase):
    def test_the_captures_are_what_the_vendored_workflow_reports(self) -> None:
        import subprocess
        done = subprocess.run([sys.executable, "-I", str(CAPTURES / "generate.py"), "--check"],
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))


class SignalTest(unittest.TestCase):
    def test_a_lowering_alone_names_both_require_fields(self) -> None:
        sig = signal("lowering_only")
        self.assertEqual((sig.status, sig.other, sig.label_clause), ("warn", False, False))
        self.assertEqual(sig.lowered, ("plan_approval.require", "technical_approval.require"))
        for stage in STAGES:
            self.assertTrue(sig.holds(stage))

    def test_an_unreferenced_runs_clause_leaves_the_signal(self) -> None:
        sig = signal("lowering_plus_unreferenced_runs")
        self.assertFalse(sig.other)
        self.assertEqual(sig.key("plan"), signal("lowering_only").key("plan"))
        self.assertTrue(sig.holds("plan"))

    def test_every_other_source_cancels_the_signal_for_both_stages(self) -> None:
        for name, status in (("lowering_plus_file_tightened", "warn"),
                             ("lowering_plus_floor_and_reverted_file", "warn"),
                             ("lowering_plus_invalid_file", "fail")):
            with self.subTest(name):
                sig = signal(name)
                self.assertEqual(sig.status, status)
                self.assertTrue(sig.other)
                for stage in STAGES:
                    self.assertFalse(sig.holds(stage))

    def test_the_captures_carry_the_lowering_clause_the_cancelled_sources_hide(self) -> None:
        """D3's reason for the allow-list: an invalid file and a reverted
        file still name the lowering."""
        for name in ("lowering_plus_floor_and_reverted_file", "lowering_plus_invalid_file"):
            self.assertIn("plan_approval.require", signal(name).lowered)

    def test_no_policy_is_no_signal(self) -> None:
        sig = signal("no_policy")
        self.assertEqual((sig.status, sig.lowered), ("pass", ()))
        for stage in STAGES:
            self.assertFalse(sig.holds(stage))

    def test_an_unrecognised_clause_cancels_it(self) -> None:
        detail = capture("lowering_only")["detail"] + "; a clause a later Workflow adds"
        sig = read_signal("warn", detail)
        self.assertTrue(sig.other)
        self.assertFalse(sig.holds("plan"))

    def test_the_lowering_of_one_field_holds_for_its_stage_only(self) -> None:
        detail = ("gate-lowering event: the adoption 3fa86993e05f (commit 2f9b33400c42) "
                  "lowers technical_approval.require")
        sig = read_signal("warn", detail)
        self.assertEqual([sig.holds(stage) for stage in STAGES], [False, True])

    def test_the_label_clause_is_allow_listed_and_keyed(self) -> None:
        detail = (capture("lowering_only")["detail"] + "; the adoption's recorded lowered label "
                  "['plan_approval.human'] disagrees with the recomputed lowering ['plan_approval.require']")
        sig = read_signal("warn", detail)
        self.assertTrue(sig.label_clause)
        self.assertFalse(sig.other)
        self.assertNotEqual(sig.key("plan"), signal("lowering_only").key("plan"))

    def test_the_key_changes_with_the_status_and_with_another_clause(self) -> None:
        keys = {signal(name).key("plan") for name in (
            "lowering_only", "lowering_plus_file_tightened", "lowering_plus_invalid_file", "no_policy")}
        self.assertEqual(len(keys), 4)

    def test_signal_of_checks_finds_the_gate_policy_check(self) -> None:
        check = capture("lowering_only")
        checks = [SimpleNamespace(id="state_valid", status="pass", detail="ok"), SimpleNamespace(**check)]
        self.assertTrue(signal_of_checks(checks).holds("plan"))
        self.assertIsNone(signal_of_checks(checks[:1]))


class ChooseTest(unittest.TestCase):
    def test_nothing_set_is_the_gate_in_every_stage(self) -> None:
        for stage in STAGES:
            for name in ("no_policy",):
                self.assertEqual(choose(MODE_AUTO, None, stage, signal(name)).kind, "gate")
            self.assertEqual(choose(MODE_AUTO, None, stage, None).kind, "gate")

    def test_manual_is_the_gate_whatever_else_is_configured(self) -> None:
        for stage in STAGES:
            for rev in (None, reviewer("codex"), reviewer("claude")):
                self.assertEqual(choose(MODE_MANUAL, rev, stage, signal("lowering_only")).kind, "gate")

    def test_auto_with_a_distinct_harness_runs_it_whatever_the_signal(self) -> None:
        for stage in STAGES:
            for sig in (None, signal("lowering_only")):
                got = choose(MODE_AUTO, reviewer("codex"), stage, sig)
                self.assertEqual((got.kind, got.harness, got.lowered), ("review", "codex", False))

    def test_auto_falls_back_to_a_same_provider_review_only_on_the_signal(self) -> None:
        for stage in STAGES:
            for rev in (None, reviewer("claude")):
                got = choose(MODE_AUTO, rev, stage, signal("lowering_only"))
                self.assertEqual((got.kind, got.harness, got.lowered), ("review", "claude", True))
                for name in ("no_policy", "lowering_plus_file_tightened", "lowering_plus_invalid_file",
                             "lowering_plus_floor_and_reverted_file"):
                    self.assertEqual(choose(MODE_AUTO, rev, stage, signal(name)).kind, "gate", name)
                self.assertEqual(choose(MODE_AUTO, rev, stage, None).kind, "gate")

    def test_a_claude_reviewer_harness_is_not_a_cross_model_one(self) -> None:
        self.assertIsNone(review_mode.distinct_harness(reviewer("claude")))
        self.assertEqual(review_mode.distinct_harness(reviewer("codex")), "codex")
        self.assertIsNone(review_mode.distinct_harness(None))

    def test_cross_model_needs_a_distinct_harness(self) -> None:
        for stage in STAGES:
            got = choose(MODE_CROSS_MODEL, reviewer("codex"), stage, None)
            self.assertEqual((got.kind, got.harness, got.lowered), ("review", "codex", False))
            for rev, words in ((None, "reviewer.harness is unset"),
                               (reviewer("claude"), "the workers' own provider")):
                got = choose(MODE_CROSS_MODEL, rev, stage, signal("lowering_only"))
                self.assertEqual(got.kind, "refused")
                self.assertIn(words, got.reason)
                self.assertIn("reviewer.harness", got.reason)
                self.assertIn("--review-mode manual", got.reason)

    def test_single_provider_needs_the_signal(self) -> None:
        for stage in STAGES:
            for rev in (None, reviewer("codex"), reviewer("claude")):
                got = choose(MODE_SINGLE_PROVIDER, rev, stage, signal("lowering_only"))
                self.assertEqual((got.kind, got.harness, got.lowered), ("review", "claude", True))
                for name in ("no_policy", "lowering_plus_file_tightened", "lowering_plus_invalid_file",
                             "lowering_plus_floor_and_reverted_file"):
                    got = choose(MODE_SINGLE_PROVIDER, rev, stage, signal(name))
                    self.assertEqual(got.kind, "refused", name)
                    self.assertIn("single-provider", got.reason)
                self.assertEqual(choose(MODE_SINGLE_PROVIDER, rev, stage, None).kind, "refused")

    def test_the_signal_is_read_only_where_it_can_matter(self) -> None:
        self.assertFalse(review_mode.needs_signal(MODE_AUTO, reviewer("codex")))
        self.assertFalse(review_mode.needs_signal(MODE_MANUAL, None))
        self.assertFalse(review_mode.needs_signal(MODE_CROSS_MODEL, None))
        self.assertTrue(review_mode.needs_signal(MODE_AUTO, None))
        self.assertTrue(review_mode.needs_signal(MODE_AUTO, reviewer("claude")))
        self.assertTrue(review_mode.needs_signal(MODE_SINGLE_PROVIDER, reviewer("codex")))

    def test_one_stage_lowered_leaves_the_other_at_the_gate(self) -> None:
        sig = read_signal("warn", "gate-lowering event: the adoption 3fa86993e05f (commit 2f9b33400c42) "
                                  "lowers plan_approval.require")
        self.assertEqual(choose(MODE_AUTO, None, "plan", sig).kind, "review")
        self.assertEqual(choose(MODE_AUTO, None, "implementation", sig).kind, "gate")


class FamilyTest(unittest.TestCase):
    def test_a_registered_harness_is_in_the_family_table(self) -> None:
        for name in review.HARNESSES:
            self.assertIn(name, review_mode.HARNESS_FAMILY)
        self.assertEqual(review_mode.HARNESS_FAMILY["codex"], "openai")
        self.assertNotEqual(review_mode.HARNESS_FAMILY["codex"], review_mode.WORKER_FAMILY)


class FlagTest(unittest.TestCase):
    def parse(self, *argv: str):
        return cli.build_parser().parse_args(list(argv))

    def test_a_bad_value_exits_2_and_a_good_one_parses(self) -> None:
        for command in ("step", "run"):
            self.assertIsNone(self.parse(command, "repo").review_mode)
            for mode in MODES:
                self.assertEqual(self.parse(command, "repo", "--review-mode", mode).review_mode, mode)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                self.parse(command, "repo", "--review-mode", "sometimes")
            self.assertEqual(ctx.exception.code, 2)

    def test_the_flag_wins_over_the_file(self) -> None:
        from controller import settings
        args = self.parse("step", "repo", "--review-mode", "manual")
        cli_values, _ = cli._settings_cli_overrides(args)
        self.assertEqual(cli_values["review.mode"], "manual")
        loaded = settings.load(Path("/nonexistent/none.json"))
        effective = settings.resolve(loaded, cli=cli_values)
        options = cli._review_mode_options(effective)
        self.assertEqual((options["mode"], options["source"]), ("manual", "cli"))
        effective = settings.resolve(loaded, cli={"review.mode": None})
        options = cli._review_mode_options(effective)
        self.assertEqual((options["mode"], options["source"]), ("auto", "default"))
        # the same-provider reviewer reads only the same_provider rows and the shared deadline (D1)
        self.assertEqual(options["same_provider"]["harness"], "claude")
        self.assertEqual(options["same_provider"]["binary"], "claude")


class JobChoiceTest(rj._ReviewCase):
    """``job.execute_step`` consults ``choose`` at an external gate of row 14
    (a real Workflow at ``AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW``)."""

    def mode_step(self, mode, reviewer_options="default", **scenario):
        self.signal_reads = []
        real = job._review_mode_signal

        def spy(managed_repo):
            self.signal_reads.append(1)
            return real(managed_repo)

        with mock.patch.object(job, "_review_mode_signal", spy):
            options = self.reviewer if reviewer_options == "default" else reviewer_options
            double = self.double = rj.LaunchDouble(self, self.life, **scenario)
            forbidden = mock.Mock(side_effect=AssertionError("no Claude worker may launch"))
            with mock.patch.object(worker, "launch", forbidden), mock.patch.object(review, "launch_review", double):
                return job.execute_step(
                    self.life.inspect(), identity=rj.IDENTITY, runtime=self.runtime_root, reviewer=options,
                    review_mode_options=None if mode is None else {"mode": mode, "source": "cli"})

    def assert_refused(self, record, words) -> None:
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])
        self.assertEqual(self.records(), [])
        text = json.dumps(record)
        self.assertIn(review_mode.REVIEW_MODE_UNAVAILABLE, text)
        self.assertIn(words, text)

    def test_the_default_is_todays_gate_and_a_configured_harness_still_reviews(self) -> None:
        record = self.mode_step(None, reviewer_options=None)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(record))
        self.assertEqual(self.double.calls, [])
        self.assertEqual(self.signal_reads, [1])  # auto with nothing set asks verify for the signal, once
        self.assertEqual(self.mode_step(None)["status"], job.STATUS_FINISHED)
        self.assertEqual(self.signal_reads, [])  # a distinct harness never asks

    def test_manual_is_the_gate_with_a_harness_configured(self) -> None:
        record = self.mode_step("manual")
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])
        self.assertEqual(self.signal_reads, [])
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(record))

    def test_cross_model_without_a_distinct_harness_is_the_refusal(self) -> None:
        self.assert_refused(self.mode_step("cross-model", reviewer_options=None), "reviewer.harness is unset")
        self.assert_refused(self.mode_step("cross-model", reviewer_options={**self.reviewer, "harness": "claude"}),
                            "the workers' own provider")

    def test_cross_model_with_codex_runs_it(self) -> None:
        self.assertEqual(self.mode_step("cross-model")["status"], job.STATUS_FINISHED)
        self.assertEqual(len(self.double.calls), 1)

    def test_single_provider_without_the_signal_is_refused_before_launch(self) -> None:
        self.assert_refused(self.mode_step("single-provider", reviewer_options=None), "single-provider")
        self.assert_refused(self.mode_step("single-provider"), "single-provider")
        self.assertEqual(self.signal_reads, [1])

    def test_a_claude_harness_in_auto_stays_the_gate_without_the_signal(self) -> None:
        record = self.mode_step("auto", reviewer_options={**self.reviewer, "harness": "claude"})
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])

    def test_a_lowered_choice_without_same_provider_options_is_the_gate(self) -> None:
        lowered = review_mode.Signal("warn", ("plan_approval.require",), False, False)
        with mock.patch.object(job, "_review_mode_signal", return_value=lowered):
            record = self.mode_step(None, reviewer_options=None)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(record))
        self.assertEqual(self.double.calls, [])

    def test_an_unreadable_verify_is_no_signal(self) -> None:
        from controller.errors import ControllerError
        with mock.patch.object(job.protocol, "verify", side_effect=ControllerError("down")):
            self.assertIsNone(job._review_mode_signal(self.life.inspect()))


if __name__ == "__main__":
    unittest.main()
