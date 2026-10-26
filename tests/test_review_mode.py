"""`workflow-controller-review-modes` CP1: the pure choice of ``review_mode``
(D1-D4) over the real ``gate_policy`` captures of ``tests/verify_captures``,
plus the ``review.mode`` row, the ``--review-mode`` flag and the job's
consultation of ``choose``."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, harness, job, review, review_mode, routing, worker  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
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
        self.assertNotIn(review_mode.HARNESS_FAMILY["codex"], review_mode.worker_families())


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

    def test_the_codex_review_record_carries_the_adapter_name_and_the_reviewer_source(self) -> None:
        """routing-harness D7: ``worker_route.harness`` is the adapter name for a review too; the ``review``
        block keeps the reviewer vocabulary."""
        record = self.mode_step("cross-model")
        self.assertEqual((record["worker_route"]["harness"], record["worker_route"]["sources"]["harness"]),
                         ("codex", "reviewer.harness"))
        self.assertEqual(record["review"]["harness"], "codex")

    def test_the_same_provider_review_record_names_claude_code_and_the_review_mode(self) -> None:
        same = {**reviewer("claude"), "operator_home": self.reviewer["operator_home"]}
        lowered = review_mode.Signal("warn", ("plan_approval.require",), False, False)
        double = self.double = rj.LaunchDouble(self, self.life)
        forbidden = mock.Mock(side_effect=AssertionError("no Claude worker may launch"))
        with mock.patch.object(worker, "launch", forbidden), mock.patch.object(review, "launch_review", double), \
                mock.patch.object(job, "_review_mode_signal", return_value=lowered):
            record = job.execute_step(
                self.life.inspect(), identity=rj.IDENTITY, runtime=self.runtime_root, reviewer=None,
                review_mode_options={"mode": "auto", "source": "default", "same_provider": same})
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual((record["worker_route"]["harness"], record["worker_route"]["sources"]["harness"]),
                         ("claude-code", "review.mode"))
        self.assertEqual(record["review"]["harness"], "claude")

    def test_a_lowered_choice_without_same_provider_options_is_the_gate(self) -> None:
        lowered = review_mode.Signal("warn", ("plan_approval.require",), False, False)
        with mock.patch.object(job, "_review_mode_signal", return_value=lowered):
            record = self.mode_step(None, reviewer_options=None)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(record))
        self.assertEqual(self.double.calls, [])

    def test_the_brief_reaches_the_prompt_of_both_reviewers_through_the_real_launch(self) -> None:
        """The job runner's real ``launch_review`` builds the prompt: a harness that records it and then
        refuses shows what each reviewer would have been told."""
        prompts = {}

        def recording(name):
            def attempt(ctx, prompt, on_spawn):
                prompts[name] = prompt
                raise ReviewLaunchRefusedError("recorded", evidence={"check": "recorded"})
            return dataclasses.replace(
                review.HARNESSES[name], attempt=attempt, build_home=lambda layout, operator_home: None,
                environment=lambda layout, binary, job_id, inherited: {})

        lowered = review_mode.Signal("warn", ("plan_approval.require",), False, False)
        harnesses = {name: recording(name) for name in ("codex", "claude")}
        same = {**reviewer("claude"), "brief": "Same-provider bar.", "operator_home": self.reviewer["operator_home"]}
        for name, signal, options, brief, mode_options in (
                ("codex", None, {**self.reviewer, "brief": "Codex bar."}, "Codex bar.", {"mode": "cross-model"}),
                ("claude", lowered, None, "Same-provider bar.",
                 {"mode": "auto", "source": "default", "same_provider": same})):
            with self.subTest(reviewer=name), mock.patch.dict(review.HARNESSES, harnesses), \
                    mock.patch.object(review, "resolve_binary", lambda binary: Path(sys.executable)), \
                    mock.patch.object(job, "_review_mode_signal", return_value=signal):
                prompts.clear()
                job.execute_step(self.life.inspect(), identity=rj.IDENTITY, runtime=self.runtime_root,
                                 reviewer=options, review_mode_options=mode_options)
                self.assertEqual(list(prompts), [name])
                self.assertIn(brief, prompts[name])
                self.assertNotIn(review.DEFAULT_BRIEF, prompts[name])

    def test_the_round_paragraph_reaches_the_harness_of_both_stages_and_reviewers_and_the_retry(self) -> None:
        """Through the real ``launch_review``: a harness that records every prompt it is handed shows that the
        round paragraph is there on a verified later external round, on the retry too, and never on a known
        first one, even with a qualifying changes file."""
        prompts: list[str] = []
        bad_header = "# Review Decision\n\nStatus: REVISE\n"  # a header mismatch: the job retries once

        def recording(name):
            def attempt(ctx, prompt, on_spawn):
                prompts.append(prompt)
                return review.Attempt(review.RunResult(0, False, b"", b"", os.getpid(), os.getpid()), bad_header)
            return dataclasses.replace(
                review.HARNESSES[name], attempt=attempt, build_home=lambda layout, operator_home: None,
                environment=lambda layout, binary, job_id, inherited: {})

        def write_changes() -> str:
            bundle = self.life.root / job.protocol.resolve_artifact(self.life.root, rj.WID, "review_bundle").path
            manifest = review.parse_manifest((bundle / "MANIFEST.md").read_text())
            path = bundle.parent / review.CHANGES_FILE_NAME
            path.write_text(f"# CHANGES\nreview_content_id: {manifest['review_content_id']}\n\n"
                            "## LOCAL_MODEL_PLAN_REVIEW\n\nx\n\n## MANUAL_EXTERNAL_PLAN_REVIEW\n\nplan diff\n\n"
                            "## LOCAL_MODEL_IMPLEMENTATION_REVIEW\n\nx\n\n"
                            "## MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW\n\nimplementation diff\n")
            return str(path.relative_to(self.life.root))

        def matrix(stage: str) -> None:
            rel = write_changes()
            lowered = review_mode.Signal("warn", (review_mode.STAGE_FIELD[stage],), False, False)
            harnesses = {name: recording(name) for name in ("codex", "claude")}
            same = {**reviewer("claude"), "operator_home": self.reviewer["operator_home"]}
            for name, signal_, options, mode_options in (
                    ("codex", None, self.reviewer, {"mode": "cross-model"}),
                    ("claude", lowered, None, {"mode": "auto", "source": "default", "same_provider": same})):
                for external, expect in ((0, False), (2, True)):
                    with self.subTest(stage=stage, reviewer=name, external=external), \
                            mock.patch.dict(review.HARNESSES, harnesses), \
                            mock.patch.object(review, "resolve_binary", lambda binary: Path(sys.executable)), \
                            mock.patch.object(job, "_review_mode_signal", return_value=signal_), \
                            mock.patch.object(job.review_rounds, "split_rounds",
                                              return_value=job.review_rounds.SplitRounds(1, external)):
                        prompts.clear()
                        job.execute_step(self.life.inspect(), identity=rj.IDENTITY, runtime=self.runtime_root,
                                         reviewer=options, review_mode_options=mode_options)
                        self.assertEqual(len(prompts), 2, "the first attempt and the one retry")
                        for prompt in prompts:
                            self.assertEqual(f"`{rel}`" in prompt, expect)
                            self.assertEqual(f"external round {external + 1} of this stage" in prompt, expect)
                            self.assertEqual(f"`## MANUAL_EXTERNAL_{stage.upper()}_REVIEW` section" in prompt, expect)

        matrix("plan")
        self.assertEqual(self.mode_step("cross-model")["status"], job.STATUS_FINISHED)
        self.life.run_until("AWAITING_PLAN_APPROVAL")
        self.life.user_step(rj._APPROVE_PLAN)
        self.life.run_until("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        matrix("implementation")

    def test_an_unreadable_verify_is_no_signal(self) -> None:
        from controller.errors import ControllerError
        with mock.patch.object(job.protocol, "verify", side_effect=ControllerError("down")):
            self.assertIsNone(job._review_mode_signal(self.life.inspect()))


if __name__ == "__main__":
    unittest.main()


WORKER_ROLES = sorted(role for role, adapter_role in routing.ADAPTER_ROLE.items() if adapter_role == harness.WORKER)
SIGNALS = (None, "lowering_only", "no_policy", "lowering_plus_invalid_file")


def codex_workers(roles: list[str]) -> tuple[mock._patch, routing.RoutingOptions]:
    """A registry whose ``codex`` also takes the worker role (with Claude's models and efforts so every route
    validates), and the routing sending ``roles`` to it."""
    patched = dataclasses.replace(
        harness.CODEX, roles=(harness.WORKER, harness.REVIEWER), models=harness.CLAUDE_CODE.models,
        efforts=harness.CLAUDE_CODE.efforts)
    registry = {harness.CLAUDE_CODE.name: harness.CLAUDE_CODE, patched.name: patched}
    patch = mock.patch.object(harness, "ADAPTERS", registry)
    patch.start()
    try:
        options = routing.RoutingOptions(cli_role_harnesses={role: "codex" for role in roles})
    except BaseException:
        patch.stop()
        raise
    return patch, options


class WorkerFamiliesTest(unittest.TestCase):
    """Reviewer-family D1-D5: the workers' family is read from the routing."""

    def test_the_built_in_routing_is_anthropic(self) -> None:
        self.assertEqual(review_mode.worker_families(), frozenset({"anthropic"}))
        self.assertEqual(review_mode.worker_families(routing.NO_OVERRIDES), frozenset({"anthropic"}))
        self.assertEqual(review_mode.worker_families(), frozenset({harness.worker_adapter().family}))

    def test_a_derived_set_changes_no_outcome_with_the_registry_as_it_is(self) -> None:
        derived = review_mode.worker_families(routing.RoutingOptions(
            cli_role_harnesses={role: "claude-code" for role in WORKER_ROLES}))
        self.assertEqual(derived, review_mode.worker_families())
        for mode in MODES:
            for reviewer_options in (None, reviewer("claude"), reviewer("codex")):
                self.assertEqual(review_mode.needs_signal(mode, reviewer_options),
                                 review_mode.needs_signal(mode, reviewer_options, derived))
                self.assertEqual(review_mode.distinct_harness(reviewer_options),
                                 review_mode.distinct_harness(reviewer_options, derived))
                for stage in STAGES:
                    for name in SIGNALS:
                        sig = None if name is None else signal(name)
                        self.assertEqual(choose(mode, reviewer_options, stage, sig),
                                         choose(mode, reviewer_options, stage, sig, derived))

    def test_openai_workers_make_a_claude_reviewer_cross_model(self) -> None:
        patch, options = codex_workers(WORKER_ROLES)
        self.addCleanup(patch.stop)
        workers = review_mode.worker_families(options)
        self.assertEqual(workers, frozenset({"openai"}))
        self.assertEqual(review_mode.distinct_harness(reviewer("claude"), workers), "claude")
        self.assertIsNone(review_mode.distinct_harness(reviewer("codex"), workers))
        chosen = choose(MODE_CROSS_MODEL, reviewer("claude"), "plan", None, workers)
        self.assertEqual((chosen.kind, chosen.harness), ("review", "claude"))
        refused = choose(MODE_CROSS_MODEL, reviewer("codex"), "plan", None, workers)
        self.assertEqual(refused.kind, "refused")
        self.assertIn("the workers' own provider", refused.reason)

    def test_mixed_families_stop_in_a_named_way(self) -> None:
        patch, options = codex_workers(WORKER_ROLES[:1])
        self.addCleanup(patch.stop)
        workers = review_mode.worker_families(options)
        self.assertEqual(workers, frozenset({"anthropic", "openai"}))
        for mode in (MODE_AUTO, MODE_CROSS_MODEL, MODE_SINGLE_PROVIDER):
            with self.subTest(mode):
                chosen = choose(mode, reviewer("codex"), "plan", signal("lowering_only"), workers)
                self.assertEqual(chosen.kind, "refused")
                self.assertIn("more than one family (anthropic, openai)", chosen.reason)
                self.assertFalse(review_mode.needs_signal(mode, None, workers))
        self.assertEqual(choose(MODE_MANUAL, reviewer("codex"), "plan", None, workers).kind, "gate")

    def test_the_job_follows_the_routing(self) -> None:
        patch, options = codex_workers(WORKER_ROLES)
        self.addCleanup(patch.stop)
        with mock.patch.object(job, "_external_review_stage", return_value=("plan", "x")):
            default = job._choose_review(None, None, reviewer("claude"), {"mode": "cross-model"})
            routed = job._choose_review(None, None, reviewer("claude"), {"mode": "cross-model"},
                                        route_options=options)
        self.assertEqual(default[1].kind, "refused")
        self.assertEqual((routed[1].kind, routed[1].harness), ("review", "claude"))

    def test_no_module_reads_the_removed_constant(self) -> None:
        for path in (Path(__file__).resolve().parent.parent / "controller").rglob("*.py"):
            self.assertNotIn("WORKER_FAMILY", path.read_text(), path.name)
