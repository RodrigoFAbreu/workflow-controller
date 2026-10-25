"""`workflow-controller-codex-reviewer` CP3: the review job.

At a configured ``external_gate`` of row 14 or 28 ``job.execute_step`` runs the
review itself (``job._run_review_job``) instead of recording ``GATE_BLOCKED``:
usage admission by provider, a job record of its own kind (schema 2), durable
launch intent before the spawn, the one bounded retry (CP2's ``launch_review``),
ingest through ``protocol.record_external_result`` against a real 2.7.0 Workflow,
the FINISHED/FAILED/INCOMPLETE mapping and the ``resume`` review-job branch.

``review.launch_review`` is replaced by a double in the job-level tests (CP2
tests the real one against ``tests/fake_codex.py``); the replay tests start a
real, tagged process group of their own. The registry is patched (the real one
is empty until D12's pre-step is recorded). No test spends Codex allowance or
makes a connection.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, handoff, job, protocol, review, settings, usage, worker  # noqa: E402
from controller.anchor import OWNERSHIP_VAR  # noqa: E402
from controller.errors import (  # noqa: E402
    JobAbandonRefusedError, PendingJobReconciliationError, ReviewLaunchRefusedError, WorkflowProtocolRefusedError,
)
from tests import fake_codex, process_fixtures  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_protocol_lifecycle import _APPROVE_PLAN, WID, Lifecycle  # noqa: E402

FIVE, WEEKLY = usage.FIVE_HOUR, usage.WEEKLY


def reviewer(tmp: Path, **extra) -> dict:
    home = tmp / "operator-home"
    home.mkdir(exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"tokens": {"refresh_token": "orig"}}))
    return {"harness": "codex", "model": "gpt-6.1-sol", "effort": "high", "timeout_seconds": 600,
            "binary": "codex", "operator_home": str(home), **extra}


def verdict_text(kw: dict, status: str = "APPROVE", *, wrong: str | None = None) -> str:
    expected = review.expected_headers(kw["manifest"], kw["stage"], kw["model"],
                                       review.HARNESSES[kw.get("harness_name", "codex")].verdict_family)
    if wrong is not None:
        expected[wrong] = expected[wrong][:-1] + ("1" if expected[wrong][-1] == "0" else "0")
    lines = ["# Review Decision", "", f"Status: {status}", ""] + [f"{k} {v}" for k, v in expected.items()]
    return "\n".join([*lines, "", "## Blocking findings", "", "None.", ""])


class LaunchDouble:
    """A ``review.launch_review`` double: records its calls and what the job
    record said at the moment of the call and of the spawn, then returns (or
    raises) what its scenario says."""

    def __init__(self, test: unittest.TestCase, life: Lifecycle, **scenario) -> None:
        self.test = test
        self.life = life
        self.scenario = scenario
        self.calls: list[dict] = []
        self.at_call: dict = {}
        self.at_spawn: dict = {}

    def record_on_disk(self, job_id: str) -> dict:
        return json.loads((self.life.runtime / "jobs" / f"{job_id}.json").read_text())

    def __call__(self, **kw) -> dict:
        self.calls.append(kw)
        self.at_call = self.record_on_disk(kw["job_id"])
        scenario = self.scenario
        if scenario.get("refuse_before_spawn"):
            raise ReviewLaunchRefusedError("a feature is enabled that is not allowlisted",
                                           evidence={"check": "features", "entry": "browser_use"})
        if scenario.get("spawn", True):
            kw["on_spawn"](os.getpid(), os.getpid())
            self.at_spawn = self.record_on_disk(kw["job_id"])
        if "raise" in scenario:
            kw["out"].update(readings=scenario.get("readings", []), first_readings=scenario.get("first_readings", []),
                             credentials="unchanged",
                             verdicts=scenario.get("verdicts", []), subagents=scenario.get("subagents", []),
                             **({"telemetry": scenario["telemetry"]} if "telemetry" in scenario else {}))
            raise scenario["raise"]
        outcome = scenario.get("outcome", "verdict")
        result = {"status": None, "verdict": None, "check": None, "outcome": outcome, "credentials": "unchanged",
                  "detail": scenario.get("detail"), "readings": scenario.get("readings", []),
                  "first_readings": scenario.get("first_readings", []),
                  "verdicts": scenario.get("verdicts", []), "subagents": scenario.get("subagents", [])}
        if "telemetry" in scenario:
            result["telemetry"] = scenario["telemetry"]
        if outcome == "verdict":
            result.update(status=scenario.get("status", "APPROVE"), verdict=verdict_text(
                kw, scenario.get("status", "APPROVE"), wrong=scenario.get("wrong")))
        if outcome == "mismatch":
            result["check"] = review.verify_verdict(verdict_text(kw, wrong="Reviewed bundle ID:"),
                                                    review.expected_headers(kw["manifest"], kw["stage"], kw["model"],
                                                                        review.HARNESSES[kw["harness_name"]].verdict_family))
        if "while_running" in scenario:
            scenario["while_running"]()
        return result


class _ReviewCase(unittest.TestCase):
    def setUp(self) -> None:
        patch = mock.patch.dict(review.HARNESSES, {"codex": review.CODEX})
        patch.start()
        self.addCleanup(patch.stop)
        self.now = time.time() - 600
        self.enterContext(mock.patch.object(job, "usage_now", lambda: self.now))
        self.life = Lifecycle(self, "2.9.0")
        self.reviewer = reviewer(self.life.td)
        self.life.run_until("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.addCleanup(process_fixtures.reap_recorded_workers, self.life.runtime)

    @property
    def runtime_root(self) -> Path:
        return self.life.runtime

    def gate(self, limits: usage.Limits | None = None, calls: list | None = None):
        limits = limits or usage.Limits()

        def gate(route, provider=usage.PROVIDER_CLAUDE):
            if calls is not None:
                calls.append((provider, route.role, route.model))
            admission = usage.admit(self.runtime_root, provider=provider, role=route.role, model=route.model,
                                    repository=str(self.life.root), run_id="run-1",
                                    limits=usage.Limits(provider=provider) if limits is None else limits, now=self.now)
            return admission.hold if admission.hold is not None else admission.token

        return gate

    def step(self, double: LaunchDouble | None = None, *, reviewer_options="default", usage_gate=None, **scenario):
        double = double or LaunchDouble(self, self.life, **scenario)
        options = self.reviewer if reviewer_options == "default" else reviewer_options
        forbidden = mock.Mock(side_effect=AssertionError("no Claude worker may launch for an external review"))
        with mock.patch.object(worker, "launch", forbidden), mock.patch.object(review, "launch_review", double):
            result = job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                      reviewer=options, usage_gate=usage_gate)
        self.double = double
        return result

    def records(self) -> list[dict]:
        """The review records (the lifecycle's earlier Claude jobs are not these)."""
        return [r for r in self.all_records() if r.get("kind") == "review"]

    def all_records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.runtime_root / "jobs").glob("*.json"))]

    def shared(self) -> dict:
        return usage.load_record(self.runtime_root)

    def codex_reading(self, five: float, *, observed: float | None = None) -> list[dict]:
        at = self.now + 10 if observed is None else observed
        return [usage.make_reading("codex", FIVE, five, self.now + 3600, at, "test"),
                usage.make_reading("codex", WEEKLY, 5.0, self.now + 7 * 86400, at, "test")]


class GateSelectionTest(unittest.TestCase):
    """D2: only rows 14 and 28, selected by action id **and** snapshot phase."""

    @staticmethod
    def decision(disposition="external_gate", action_id="plan.review.external",
                 phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", snapshot=True):
        document = {"snapshot": {"phase": phase}} if snapshot else {}
        return SimpleNamespace(protocol=SimpleNamespace(disposition=disposition, action_id=action_id, document=document))

    def test_rows_14_and_28_are_couriered(self) -> None:
        self.assertEqual(job._external_review_stage(self.decision()), ("plan", "plan_review_verdict"))
        got = job._external_review_stage(self.decision(action_id="implementation.review.external",
                                                       phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"))
        self.assertEqual(got, ("implementation", "implementation_review_verdict"))

    def test_the_other_rows_with_the_same_action_ids_stay_gates(self) -> None:
        # Row 21 (plan.review.external at AWAITING_PLAN_APPROVAL for "1"), rows 31a and 35
        # (implementation.review.external in other phases): feedback-only rows.
        for action_id, phase in (
            ("plan.review.external", "AWAITING_EXTERNAL_PLAN_REVIEW"),
            ("plan.review.external", "AWAITING_PLAN_APPROVAL"),
            ("implementation.review.external", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"),
            ("implementation.review.external", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"),
            ("plan.review.external", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"),
            ("implementation.review.external", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"),
        ):
            with self.subTest(action_id=action_id, phase=phase):
                self.assertIsNone(job._external_review_stage(self.decision(action_id=action_id, phase=phase)))

    def test_unset_harness_no_snapshot_other_dispositions_and_other_actions_stay_gates(self) -> None:
        # No reviewer configured: the gate is recognised, but no review is chosen.
        target, choice = job._choose_review(None, self.decision(), None, {"mode": "manual"})
        self.assertEqual((target, choice.kind), (("plan", "plan_review_verdict"), "gate"))
        self.assertIsNone(job._choose_review(None, self.decision(snapshot=False), None, {"mode": "manual"}))
        self.assertIsNone(job._external_review_stage(self.decision(snapshot=False)))
        self.assertIsNone(job._external_review_stage(self.decision(disposition="human_gate")))
        self.assertIsNone(job._external_review_stage(self.decision(action_id="plan.approve")))
        self.assertIsNone(job._external_review_stage(SimpleNamespace(protocol=None)))


class UnconfiguredAndRefusalTest(_ReviewCase):
    def test_an_unset_harness_keeps_todays_gate_and_exit_class(self) -> None:
        record = self.step(reviewer_options=None)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("kind", record)
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(self.double.calls, [])

    def test_an_unknown_harness_refuses_and_records_nothing(self) -> None:
        with self.assertRaises(ReviewLaunchRefusedError) as caught:
            self.step(reviewer_options={**self.reviewer, "harness": "gemini"})
        self.assertEqual(caught.exception.evidence["check"], "harness")
        self.assertEqual(self.records(), [])

    def test_an_unregistered_harness_refuses_like_an_unknown_one(self) -> None:
        with mock.patch.dict(review.HARNESSES, clear=True), self.assertRaises(ReviewLaunchRefusedError):
            self.step()
        self.assertEqual(self.records(), [])


class ExternalCapTest(_ReviewCase):
    """``workflow-controller-round-caps`` D2: the external cap, checked where
    the step has already chosen to run a review. The real 2.9.0 Workflow gives
    no split counts, so ``split_rounds`` is patched to give them."""

    def counts(self, local: int, external: int):
        return mock.patch.object(job.review_rounds, "split_rounds",
                                 return_value=job.review_rounds.SplitRounds(local, external))

    def assert_capped(self, record: dict) -> None:
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record)
        self.assertTrue(record["human_gate_pending"]["what_is_required"].startswith("review_round_cap_reached: "))
        self.assertIn("the external count 4 has reached the external round cap of 4",
                      record["human_gate_pending"]["what_is_required"])
        self.assertEqual(self.double.calls, [])
        self.assertEqual(self.records(), [], "no review job record")

    def test_the_external_count_at_the_cap_launches_no_review(self) -> None:
        calls: list = []
        with self.counts(1, 4):
            record = self.step(usage_gate=self.gate(calls=calls))
        self.assert_capped(record)
        self.assertEqual(calls, [], "no usage reservation")

    def test_below_the_cap_and_without_split_counts_the_review_launches(self) -> None:
        with self.counts(1, 3):
            self.assertEqual(self.step()["status"], job.STATUS_FINISHED)

    def test_without_split_counts_the_review_launches_as_before(self) -> None:
        self.assertEqual(self.step()["status"], job.STATUS_FINISHED)

    def test_a_manual_mode_or_no_reviewer_keeps_the_ordinary_external_gate(self) -> None:
        with self.counts(1, 4):
            unset = self.step(reviewer_options=None)
            manual = job.execute_step(
                self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root, reviewer=self.reviewer,
                review_mode_options={"mode": "manual", "source": "file"})
        for record in (unset, manual):
            self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
            self.assertNotIn("review_round_cap_reached", record["human_gate_pending"]["what_is_required"])

    def test_explain_prints_the_same_gate(self) -> None:
        from tests.test_cli import _Args
        args = _Args(str(self.life.root), workflow_manager=str(self.life.stub))
        args.effective_settings = settings.resolve(
            settings.empty(self.life.td / "settings.toml"), cli={"reviewer.harness": "codex"})
        out = io.StringIO()
        with self.counts(1, 4), contextlib.redirect_stdout(out):
            cli.cmd_explain(args, self.runtime_root, IDENTITY)
        self.assertIn("review_round_cap_reached: ", out.getvalue())
        self.assertNotIn("review mode", out.getvalue().lower().replace("review_mode", ""))


class HappyPathTest(_ReviewCase):
    def test_an_exact_verdict_is_ingested_and_the_phase_advances_with_no_claude_worker(self) -> None:
        real = protocol.record_external_result
        seen: list = []

        def spy(*args, **kwargs):
            seen.append((args, kwargs))
            return real(*args, **kwargs)

        with mock.patch.object(protocol, "record_external_result", spy):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record)
        self.assertEqual(self.life.phase(), "AWAITING_PLAN_APPROVAL")
        self.assertEqual(record["observed_phase_after"], "AWAITING_PLAN_APPROVAL")
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["kind"], "review")
        self.assertEqual(record["schema_version"], job.REVIEW_SCHEMA_VERSION)
        self.assertEqual(record["worker_route"]["harness"], "codex")
        self.assertEqual((record["worker_route"]["model"], record["worker_route"]["effort"]), ("gpt-6.1-sol", "high"))
        self.assertEqual(record["review"]["ingested"], "recorded")
        self.assertNotIn("telemetry", record["review"], "a Codex review keeps no figures")
        [(args, kwargs)] = seen
        self.assertEqual(args[2], "plan_review_verdict")
        self.assertEqual(kwargs["run_ref"], f"codex:{record['job_id']}")
        self.assertNotIn("protocol", record)
        # the verdict file the Controller handed over is gone, and so is the copy
        self.assertEqual(list((self.runtime_root / "reviews").glob("*-verdict.md")), [])

    def test_the_prompt_inputs_are_the_bundles_manifest_values(self) -> None:
        self.step()
        [kw] = self.double.calls
        self.assertEqual(kw["stage"], "plan")
        self.assertEqual(set(kw["manifest"]) >= {"bundle_id", "review_content_id", "plan_revision"}, True)
        self.assertEqual((kw["harness_name"], kw["model"], kw["effort"], kw["timeout"]), ("codex", "gpt-6.1-sol", "high", 600.0))
        self.assertTrue(kw["bundle_dir"].endswith("/current") or "plan" in kw["bundle_dir"])

    def test_both_stages_in_one_lifecycle_and_run_continues_to_the_next_step(self) -> None:
        self.assertEqual(self.step()["status"], job.STATUS_FINISHED)
        self.life.run_until("AWAITING_PLAN_APPROVAL")
        self.life.user_step(_APPROVE_PLAN)
        self.life.run_until("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        implementation = self.step()
        self.assertEqual(implementation["status"], job.STATUS_FINISHED, implementation)
        self.assertEqual(implementation["review"]["stage"], "implementation")
        self.assertEqual(self.double.calls[0]["stage"], "implementation")
        self.assertEqual(self.life.phase(), "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

    def test_launch_intent_is_flushed_before_the_spawn_and_the_process_evidence_after_it(self) -> None:
        self.step()
        before, after = self.double.at_call, self.double.at_spawn
        self.assertEqual(before["status"], job.STATUS_LAUNCHED)
        self.assertTrue(before["review"]["launched"])
        self.assertEqual(before["ownership_tag"], before["job_id"])
        self.assertNotIn("worker_process", before)
        self.assertIn("worker_process", after)
        self.assertNotIn("worker_state", after)


VERDICTS = [
    {"attempt": 1, "sha256": "a" * 64, "status": "APPROVE", "disposition": "not-ingested: header mismatch, retried once",
     "path": "reviews/j-verdict-1.md"},
    {"attempt": 2, "sha256": "b" * 64, "status": "REVISE", "disposition": "returned", "path": "reviews/j-verdict-2.md"},
]
SUBAGENTS = [{"rollout": "2026/10/15/rollout-x.jsonl", "total_tokens": 43234}]


class VerdictEvidenceTest(_ReviewCase):
    """workflow#28 condition 2: every verdict the courier obtains is recorded in
    the job evidence with its digest, ``Status:`` and what became of it; and a
    sub-agent spawn is reported with its usage (I10)."""

    def test_an_ingested_verdict_and_the_one_it_replaced_are_both_in_the_record(self) -> None:
        record = self.step(verdicts=VERDICTS, subagents=SUBAGENTS)
        self.assertEqual(record["status"], job.STATUS_FINISHED, record)
        self.assertEqual([(v["attempt"], v["sha256"], v["status"], v["disposition"]) for v in record["review"]["verdicts"]],
                         [(1, "a" * 64, "APPROVE", "not-ingested: header mismatch, retried once"),
                          (2, "b" * 64, "REVISE", "ingested")])
        self.assertEqual(record["review"]["subagents"], SUBAGENTS)

    def test_a_claude_reviews_cost_and_tokens_are_in_the_record_and_a_codex_review_has_none(self) -> None:
        figures = {"version": 1, "results": 2, "cost_usd": 0.02,
                   "tokens": {"input": 24, "output": 600, "cache_creation": 100, "cache_read": 2000}}
        with self.assertRaises(RuntimeError):
            self.step(**{"raise": RuntimeError("late"), "telemetry": figures})
        self.assertEqual(self.records()[-1]["review"]["telemetry"], figures)
        record = self.step(telemetry=figures)
        self.assertEqual(record["review"]["telemetry"], figures)

    def test_a_verdict_the_workflow_refuses_is_recorded_as_refused(self) -> None:
        refusal = protocol.Refusal("refused", "DistinctReviewerModelsRequiredError: nope", False, None)
        refused = protocol.ExternalResult("refused", None, None, None, None, None, None, None, refusal, {})
        with mock.patch.object(protocol, "record_external_result", return_value=refused):
            record = self.step(verdicts=VERDICTS)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["review"]["verdicts"][1]["disposition"], "refused by the Workflow: refused")

    def test_a_verdict_voided_by_a_changed_state_identity_is_recorded_as_void(self) -> None:
        real = protocol.next_action
        moved = {"on": False}

        def wrapped(*args, **kwargs):
            if moved["on"] and kwargs.get("expect_state_identity") is not None:
                refusal = {"code": "stale_decision", "message": "stale", "retryable": True, "native": None}
                raise WorkflowProtocolRefusedError("stale", evidence={"reason": "protocol_refused", "refusal": refusal})
            return real(*args, **kwargs)

        with mock.patch.object(protocol, "next_action", wrapped):
            record = self.step(verdicts=VERDICTS, while_running=lambda: moved.update(on=True))
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertEqual(record["review"]["verdicts"][1]["status"], "REVISE")
        self.assertTrue(record["review"]["verdicts"][1]["disposition"].startswith("void:"))

    def test_verdicts_the_harness_obtained_survive_a_refused_or_interrupted_launch(self) -> None:
        with self.assertRaises(RuntimeError):
            self.step(**{"raise": RuntimeError("late"), "verdicts": VERDICTS, "subagents": SUBAGENTS})
        with self.assertRaises(KeyboardInterrupt):
            self.step(**{"raise": KeyboardInterrupt(), "verdicts": VERDICTS, "subagents": SUBAGENTS})
        for record in self.records():
            self.assertEqual(len(record["review"]["verdicts"]), 2)
            self.assertEqual(record["review"]["subagents"], SUBAGENTS)

    def test_the_failure_message_and_the_step_event_name_the_sub_agent_spawn(self) -> None:
        note = job.review_subagent_note({"subagents": SUBAGENTS})
        self.assertIn("1 sub-agent (43,234 tokens)", note)
        self.assertEqual(job.review_subagent_note({"subagents": []}), "")
        failed = self.step(outcome="failed", detail="boom", subagents=SUBAGENTS + SUBAGENTS)
        self.assertIn("2 sub-agents (86,468 tokens)", job._review_message(failed))
        self.assertNotIn("sub-agent", job._review_message(self.step(outcome="empty")))
        finished = self.step(subagents=SUBAGENTS)
        [event] = [e for e in job.derive_job_event(finished) if e.event_type == "step_done"]
        self.assertIn("43,234 tokens", event.message)


class OutcomeMappingTest(_ReviewCase):
    def check(self, status, code, **scenario):
        record = self.step(**scenario)
        self.assertEqual(record["status"], status, record)
        self.assertEqual(record["reconciliation_evidence"]["code"], code)
        self.assertEqual(self.life.phase(), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")  # the gate stays open
        return record

    def test_a_second_header_mismatch_is_failed_with_the_diff(self) -> None:
        record = self.check(job.STATUS_FAILED, job.REVIEW_HEADER_MISMATCH_CODE, outcome="mismatch")
        self.assertEqual([m["label"] for m in record["reconciliation_evidence"]["mismatches"]],
                         ["Reviewed bundle ID:"])

    def test_a_status_changed_by_the_header_retry_is_failed_with_the_gate_open(self) -> None:
        record = self.check(job.STATUS_FAILED, job.REVIEW_STATUS_CHANGED_CODE, outcome="status_changed",
                            detail="the two attempts disagreed: the first verdict said REVISE, the retry APPROVE; "
                                   "both verdicts are in the record")
        self.assertIn("disagreed", record["reconciliation_evidence"]["message"])

    def test_limit_timeout_empty_and_a_changed_repository_are_incomplete(self) -> None:
        self.check(job.STATUS_INCOMPLETE, job.REVIEW_LIMIT_CODE, outcome="limit", detail="429 usage limit reached")
        self.check(job.STATUS_INCOMPLETE, job.REVIEW_TIMEOUT_CODE, outcome="timeout")
        self.check(job.STATUS_INCOMPLETE, job.REVIEW_EMPTY_CODE, outcome="empty")
        self.check(job.STATUS_INCOMPLETE, job.REVIEW_CHANGED_CODE, outcome="changed", detail="changed: head")

    def test_a_failure_without_limit_text_is_failed_never_incomplete(self) -> None:
        self.check(job.STATUS_FAILED, job.REVIEW_FAILED_CODE, outcome="failed", detail="boom")

    def test_the_workflows_refusal_is_failed_with_its_code_and_leaves_the_gate_open(self) -> None:
        refusal = protocol.Refusal("refused", "DistinctReviewerModelsRequiredError: nope", False, None)
        refused = protocol.ExternalResult("refused", None, None, None, None, None, None, None, refusal, {})
        with mock.patch.object(protocol, "record_external_result", return_value=refused):
            record = self.check(job.STATUS_FAILED, "refused")
        self.assertIn("DistinctReviewerModels", record["reconciliation_evidence"]["message"])

    def test_a_not_applicable_answer_is_failed_too(self) -> None:
        refusal = protocol.Refusal("not_applicable", "the stage is already recorded", False, None)
        answer = protocol.ExternalResult("not_applicable", None, None, None, None, None, None, None, refusal, {})
        with mock.patch.object(protocol, "record_external_result", return_value=answer):
            self.check(job.STATUS_FAILED, "not_applicable")

    def test_a_state_that_moved_during_the_review_discards_the_verdict(self) -> None:
        real = protocol.next_action
        moved = {"on": False}

        def wrapped(*args, **kwargs):
            if moved["on"] and kwargs.get("expect_state_identity") is not None:
                refusal = {"code": "stale_decision", "message": "stale", "retryable": True, "native": None}
                raise WorkflowProtocolRefusedError("stale", evidence={"reason": "protocol_refused", "refusal": refusal})
            return real(*args, **kwargs)

        record = None
        with mock.patch.object(protocol, "next_action", wrapped):
            record = self.check(job.STATUS_INCOMPLETE, job.REVIEW_STATE_CHANGED_CODE,
                                while_running=lambda: moved.update(on=True))
        self.assertNotIn("ingested", record["review"])

    def test_a_gate_that_is_stale_before_the_spawn_is_a_released_failed_record(self) -> None:
        stale = SimpleNamespace(current=False, reason="stale_decision", evidence=lambda: {"reason": "stale_decision"})
        token_gate = self.gate()
        with mock.patch.object(job.protocol_decision, "check_currency", return_value=stale):
            record = self.step(usage_gate=token_gate)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.protocol_decision.DECISION_STALE_AT_LAUNCH)
        self.assertEqual(self.double.calls, [])
        self.assertTrue(job.never_launched(record))
        self.assertEqual(self.shared()["ledger"], [])  # released, no charge
        self.assertEqual(record["usage"]["accounting"], "done")


class UsageTest(_ReviewCase):
    def test_the_job_is_admitted_as_a_codex_job_and_a_hold_records_nothing(self) -> None:
        calls: list = []
        usage.record_reading(self.runtime_root, self.codex_reading(99.0))
        held = self.step(usage_gate=self.gate(calls=calls))
        self.assertIsInstance(held, job.UsageHold)
        self.assertEqual(held.provider, usage.PROVIDER_CODEX)
        self.assertEqual(calls, [("codex", "external-review", "gpt-6.1-sol")])
        self.assertEqual(self.records(), [])
        self.assertEqual(self.double.calls, [])

    def test_a_measured_delta_is_charged_when_the_job_completes(self) -> None:
        usage.record_reading(self.runtime_root, self.codex_reading(10.0, observed=self.now))
        record = self.step(usage_gate=self.gate(), readings=self.codex_reading(14.0))
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["usage"]["accounting"], "done")
        self.assertEqual(record["usage"]["provider"], "codex")
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["provider"], "codex")
        self.assertEqual(entry["five_hour"], 4.0)
        self.assertNotEqual(entry["delta"], "unknown")
        self.assertEqual(self.shared()["reservations"], {})

    def test_a_limit_is_completed_as_ok_by_the_measured_delta_never_released(self) -> None:
        usage.record_reading(self.runtime_root, self.codex_reading(10.0, observed=self.now))
        record = self.step(usage_gate=self.gate(), outcome="limit", detail="429", readings=self.codex_reading(99.0))
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["five_hour"], 89.0)

    def test_no_reading_is_an_unknown_delta_charged_at_the_forecast(self) -> None:
        record = self.step(usage_gate=self.gate(), outcome="empty")
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["delta"], "unknown")

    def test_a_boundary_refusal_before_the_exec_spawn_is_failed_and_released(self) -> None:
        record = self.step(usage_gate=self.gate(), refuse_before_spawn=True)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.REVIEW_REFUSED_CODE)
        self.assertEqual(record["reconciliation_evidence"]["evidence"]["entry"], "browser_use")
        self.assertTrue(record["review"]["not_started"])
        self.assertTrue(job.never_launched(record))
        self.assertEqual(self.shared()["ledger"], [])
        self.assertEqual(self.shared()["reservations"], {})

    def test_a_fault_before_the_spawn_is_a_released_failed_record_and_propagates(self) -> None:
        double = LaunchDouble(self, self.life, **{"spawn": False, "raise": RuntimeError("the identity read failed")})
        with self.assertRaises(RuntimeError):
            self.step(double, usage_gate=self.gate())
        [record] = self.records()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertTrue(record["review"]["not_started"])
        self.assertEqual(self.shared()["ledger"], [])
        self.assertEqual(self.shared()["reservations"], {})

    def test_a_fault_after_the_spawn_keeps_the_charge(self) -> None:
        double = LaunchDouble(self, self.life, **{"raise": RuntimeError("late")})
        with self.assertRaises(RuntimeError):
            self.step(double, usage_gate=self.gate())
        [record] = self.records()
        self.assertFalse(record["review"].get("not_started"))
        self.assertEqual(len(self.shared()["ledger"]), 1)

    def test_ctrl_c_before_the_exec_spawned_releases_the_reservation_uncharged(self) -> None:
        usage.record_reading(self.runtime_root, self.codex_reading(10.0, observed=self.now))
        double = LaunchDouble(self, self.life, **{"raise": KeyboardInterrupt(), "spawn": False})
        with self.assertRaises(KeyboardInterrupt):
            self.step(double, usage_gate=self.gate())
        [record] = self.records()
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertIs(record["review"]["not_started"], True)
        self.assertEqual(self.shared()["ledger"], [])

    def test_ctrl_c_ends_the_record_incomplete_completes_the_reservation_and_propagates(self) -> None:
        usage.record_reading(self.runtime_root, self.codex_reading(10.0, observed=self.now))
        double = LaunchDouble(self, self.life, **{"raise": KeyboardInterrupt(), "readings": self.codex_reading(12.0)})
        with self.assertRaises(KeyboardInterrupt):
            self.step(double, usage_gate=self.gate())
        [record] = self.records()
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.REVIEW_INTERRUPTED_CODE)
        self.assertEqual(record["usage"]["accounting"], "done")
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["five_hour"], 2.0)


class RecordShapeTest(_ReviewCase):
    def test_a_review_record_is_schema_2_and_a_1_9_0_reader_refuses_it(self) -> None:
        record = self.step()
        self.assertEqual(record["schema_version"], 2)
        # the 1.9.0 `validate_record` case 1 is `schema_version != 1`
        self.assertNotEqual(record["schema_version"], 1)

    def test_this_release_validates_review_records_for_their_own_kind_only(self) -> None:
        record = self.step(outcome="empty")
        target = self.life.inspect()
        valid = job.validate_record(record, managed_repo=target, identity=IDENTITY)
        self.assertTrue(valid.valid, valid)
        legacy_v2 = {k: v for k, v in record.items() if k != "kind"}
        bad = job.validate_record(legacy_v2, managed_repo=target, identity=IDENTITY)
        self.assertEqual((bad.valid, bad.reason), (False, "unknown_schema_version"))
        v1_review = {**record, "schema_version": 1}
        bad = job.validate_record(v1_review, managed_repo=target, identity=IDENTITY)
        self.assertEqual((bad.valid, bad.reason), (False, "review_record_malformed"))
        no_route = {**record, "worker_route": {"role": "x"}}
        self.assertEqual(job.validate_record(no_route, managed_repo=target, identity=IDENTITY).reason,
                         "review_record_malformed")

    def test_never_launched_follows_launch_intent_not_expected_transition(self) -> None:
        base = {"kind": "review", "schema_version": 2, "status": job.STATUS_INTERRUPTED}
        self.assertTrue(job.never_launched({**base, "review": {"launched": False}}))
        self.assertTrue(job.never_launched({**base, "review": {"launched": True, "not_started": True}}))
        self.assertFalse(job.never_launched({**base, "review": {"launched": True}}))  # no worker_process needed
        self.assertFalse(job.never_launched({**base, "review": "unreadable"}))
        # a Claude record is judged as before
        self.assertTrue(job.never_launched({"status": job.STATUS_INTERRUPTED}))

    def test_observers_and_the_apply_view_skip_a_review_record(self) -> None:
        self.step()
        self.assertIsNone(job.last_launched_apply_job_view(self.runtime_root, self.life.root, WID))


class NotificationTest(_ReviewCase):
    def test_a_failed_or_incomplete_review_names_the_open_gate_and_the_manual_route(self) -> None:
        record = self.step(outcome="limit")
        [event] = job.derive_job_event(record)
        self.assertEqual(event.event_type, "lane_blocked")
        self.assertIn("/record-manual-plan-review", event.message)
        self.assertIn("stays open", event.message)
        self.assertIn(job.REVIEW_LIMIT_CODE, event.message)

    def test_no_user_gate_is_derived_for_a_review_the_controller_runs(self) -> None:
        record = self.step()
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["step_done"])


class RetainedReadingsTest(_ReviewCase):
    def rollout(self, home: Path, five: float, observed: str = "2026-10-09T21:50:53Z",
                resets: int = 4102444800) -> None:
        path = home / "sessions" / "2026" / "10" / "09" / "rollout-x.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"timestamp": observed, "type": "event_msg", "payload": {"rate_limits": {
            "primary": {"used_percent": five, "window_minutes": 300, "resets_at": resets},
            "secondary": {"used_percent": 1.0, "window_minutes": 10080, "resets_at": resets}}}}) + "\n")

    def effective(self):
        loaded = settings.SettingsFile(path=self.life.td / "settings.json", exists=False, sha256=None, values={},
                                       routing_config=None, unknown=(), table_generation=settings.TABLE_GENERATION)
        return settings.resolve(loaded)

    def cli_gate(self, operator_home: Path):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(operator_home)}):
            gate = cli._usage_gate(self.runtime_root, self.life.inspect(), None, self.effective(), None)
            return gate

    def test_the_operators_home_and_every_retained_review_home_are_merged_on_every_attempt(self) -> None:
        operator = self.life.td / "op"
        retained = self.runtime_root / "reviews" / "old-job" / "codex-home"
        route = SimpleNamespace(role="external-review", model="gpt-6.1-sol")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(operator)}):
            gate = cli._usage_gate(self.runtime_root, self.life.inspect(), None, self.effective(), None)
            # the retained review recorded 99%; the operator's home holds an older, lower reading
            self.rollout(operator, 20.0, observed="2026-10-08T00:00:00Z")
            self.rollout(retained, 99.0)
            hold = gate(route, provider=usage.PROVIDER_CODEX)
            self.assertIsInstance(hold, usage.Hold)
            self.assertEqual(hold.provider, usage.PROVIDER_CODEX)
            # a Claude reading is a different provider: a Claude admission is untouched
            self.assertIsInstance(gate(SimpleNamespace(role="milestone-plan", model="opus")), str)

    def test_readings_are_read_afresh_on_every_admission_attempt(self) -> None:
        operator = self.life.td / "op"
        route = SimpleNamespace(role="external-review", model="gpt-6.1-sol")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(operator)}):
            gate = cli._usage_gate(self.runtime_root, self.life.inspect(), None, self.effective(), None)
            self.assertIsInstance(gate(route, provider=usage.PROVIDER_CODEX), str)  # nothing recorded yet
            self.rollout(operator, 99.0)  # allowance spent outside the Controller since
            self.assertIsInstance(gate(route, provider=usage.PROVIDER_CODEX), usage.Hold)

    def test_a_codex_hold_names_the_provider_and_the_codex_cap_settings(self) -> None:
        hold = job.UsageHold(provider="codex", window="repository_cap", percent=90.0, forecast=8.0, resume_at=None,
                             reason="r", waitable=False, resets_at=None)
        text = cli.usage_hold_message(hold)
        self.assertIn("Codex usage budget", text)
        self.assertIn("usage.codex_repository_cap_percent", text)
        self.assertIn("usage.codex_pause_at_percent", text)
        claude = cli.usage_hold_message(job.UsageHold(**{**hold.__dict__, "provider": "claude"}))
        self.assertIn("usage.repository_cap_percent", claude)
        self.assertNotIn("Codex", claude)

    def test_read_codex_homes_takes_the_newest_per_window_and_ignores_a_passed_reset(self) -> None:
        a, b = self.life.td / "a", self.life.td / "b"
        self.rollout(a, 30.0)
        self.rollout(b, 60.0, observed="2026-10-09T22:50:53Z")
        merged = {r["window"]: r["percent"] for r in usage.read_codex_homes([a, b, self.life.td / "none"])}
        self.assertEqual(merged[FIVE], 60.0)

    def test_the_retained_sessions_are_swept_once_their_windows_have_reset(self) -> None:
        live = self.runtime_root / "reviews" / "live" / "codex-home"
        spent = self.runtime_root / "reviews" / "spent" / "codex-home"
        running = self.runtime_root / "reviews" / "running"
        self.rollout(live, 50.0, resets=4102444800)
        self.rollout(spent, 50.0, resets=1000000000)
        (running / "tree").mkdir(parents=True)
        self.rollout(running / "codex-home", 50.0, resets=1000000000)
        removed = review.sweep_retained(self.runtime_root, now=time.time())
        self.assertEqual(removed, ["spent"])
        self.assertTrue((live / "sessions").is_dir())
        self.assertTrue((running / "tree").is_dir())  # a job that still has its copy is never touched
        self.assertEqual(review.retained_codex_homes(self.runtime_root), [live, running / "codex-home"])


class FakeCodexEndToEndTest(_ReviewCase):
    """The job against the real `review.launch_review` and `tests/fake_codex.py`
    (CP2's harness, the registry patched): the end readings come from the job's
    own minimal home, read before it is settled, and the job's files are gone."""

    def setUp(self) -> None:
        super().setUp()
        self.log = self.life.td / "fake.log"
        self.system = self.life.td / "etc-codex"
        self.system.mkdir()
        self.real = review.launch_review

    def baseline(self, five: float) -> None:
        """The reading an admission started from, in the windows the fake's rollout reports (it resets an hour and a
        day after the real clock; the test's usage clock runs ten minutes behind)."""
        real = time.time()
        usage.record_reading(self.runtime_root, [
            usage.make_reading("codex", FIVE, five, real + 3600, self.now, "test"),
            usage.make_reading("codex", WEEKLY, 1.0, real + 86400, self.now, "test")])

    def launch(self, scenario: dict):
        scenario = {"log": str(self.log), "system_dir": str(self.system), "orphan_seconds": 30, **scenario}
        binary = fake_codex.write_binary(self.life.td / "bin", scenario)
        test = self

        def run(**kw):
            return test.real(**{**kw, "binary_name": str(binary), "system_dir": test.system,
                                "inherited_env": {"PATH": "/evil", "OPENAI_API_KEY": "evil"}})
        return run

    def step_real(self, scenario: dict, **kwargs):
        with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError("no Claude worker"))), \
                mock.patch.object(review, "launch_review", self.launch(scenario)):
            return job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                    reviewer=self.reviewer, **kwargs)

    def test_a_real_review_ingests_charges_the_measured_delta_from_the_jobs_own_home_and_leaves_only_sessions(self) -> None:
        self.baseline(1.0)
        record = self.step_real({"rate_limits": {"primary": 14.0, "secondary": 6.0}}, usage_gate=self.gate())
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(self.life.phase(), "AWAITING_PLAN_APPROVAL")
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["five_hour"], 13.0)
        base = self.runtime_root / "reviews" / record["job_id"]
        self.assertEqual(sorted(p.name for p in base.iterdir()), ["codex-home"])
        self.assertEqual(sorted(p.name for p in (base / "codex-home").iterdir()), ["sessions"])
        # the next admission sees the retained rollout beside the operator's home
        self.assertEqual([r["percent"] for r in usage.read_codex_homes(review.retained_codex_homes(self.runtime_root))
                          if r["window"] == FIVE], [14.0])

    def test_a_limit_during_the_job_ends_incomplete_and_the_retained_rollout_holds_the_next_admission(self) -> None:
        self.baseline(1.0)
        record = self.step_real({"exec": {"verdict": "limit", "exit": 1}, "rate_limits": {"primary": 99.0,
                                                                                             "secondary": 6.0}},
                                usage_gate=self.gate())
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE, record.get("reconciliation_evidence"))
        self.assertEqual(record["reconciliation_evidence"]["code"], job.REVIEW_LIMIT_CODE)
        self.assertEqual(self.life.phase(), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(self.shared()["ledger"][0]["five_hour"], 98.0)
        # a second `run`: the operator's home is empty, the retained review home holds the 99%
        operator = self.life.td / "empty-operator"
        effective = settings.resolve(settings.SettingsFile(
            path=self.life.td / "s.json", exists=False, sha256=None, values={}, routing_config=None, unknown=(),
            table_generation=settings.TABLE_GENERATION))
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(operator)}):
            gate = cli._usage_gate(self.runtime_root, self.life.inspect(), None, effective, None)
            held = gate(SimpleNamespace(role="external-review", model="gpt-6.1-sol"), provider=usage.PROVIDER_CODEX)
        self.assertIsInstance(held, usage.Hold)


class CliTest(_ReviewCase):
    """`cli`: the settings reach `execute_step`, INCOMPLETE is exit 35 and Ctrl-C
    propagates as the documented exit 130 (D8, D9), and a FINISHED review lets `run` go on."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(settings.process_defaults({}))
        self.enterContext(mock.patch.object(handoff, "detect", lambda ident, origin: None))
        self.enterContext(mock.patch.object(cli, "_inspect_target", lambda args: self.life.inspect()))
        self.addCleanup(setattr, cli, "_open_run", None)
        self.settings_path = self.life.td / "config" / "settings.json"

    def args(self, reviewer_settings: dict | None, **attrs):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        body = {} if reviewer_settings is None else {"reviewer": reviewer_settings}
        self.settings_path.write_text(json.dumps(body))
        args = argparse.Namespace(
            repo=str(self.life.root), work_item=None, json=False, workflow_manager=None, permission_mode=None,
            timeout=60, claude_binary="claude", max_steps=None, pause_file=None, settings=str(self.settings_path),
            follow=False, usage_cap=None)
        for name, value in attrs.items():
            setattr(args, name, value)
        cli._apply_settings(args, write=True)
        return args

    def one_step(self, args, **scenario):
        double = self.double = LaunchDouble(self, self.life, **scenario)
        ident = IDENTITY
        with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError("no Claude worker"))), \
                mock.patch.object(review, "launch_review", double):
            return cli._run_one_step(args, self.runtime_root, ident, self.life.inspect())

    def test_the_settings_become_the_reviewer_options(self) -> None:
        self.assertIsNone(cli._reviewer_options(cli._effective(self.args(None))))
        options = cli._reviewer_options(cli._effective(self.args({"harness": "codex", "binary": "/x/codex"})))
        self.assertEqual({k: options[k] for k in ("harness", "model", "effort", "timeout_seconds", "binary")}, {
            "harness": "codex", "model": "gpt-6.1-sol", "effort": "high", "timeout_seconds": 3600,
            "binary": "/x/codex"})

    def brief_options(self, file_path: Path):
        """The effective settings read from ``file_path`` (any place), as the reviewer options see them."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps({"reviewer": {"harness": "codex", "brief": "Only report crashes."}}))
        return settings.resolve(settings.load(file_path))

    def test_the_brief_reaches_both_reviewers_and_the_real_prompt(self) -> None:
        effective = self.brief_options(self.settings_path)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            codex = cli._reviewer_options(effective, self.life.root, self.runtime_root)
            claude = cli._review_mode_options(effective, self.life.root, self.runtime_root)["same_provider"]
        self.assertEqual((codex["brief"], claude["brief"]), ("Only report crashes.",) * 2)
        self.assertEqual(stderr.getvalue(), "")
        code, _result = self.one_step(self.args({"harness": "codex", "brief": "Only report crashes."}))
        self.assertEqual(code, cli.EXIT_OK)
        call = self.double.calls[0]
        self.assertEqual(call["brief"], "Only report crashes.")
        prompt = review.build_prompt(call["stage"], review.expected_headers(
            call["manifest"], call["stage"], call["model"], review.harness(call["harness_name"]).verdict_family),
            bundle_dir=call["bundle_dir"], brief=call["brief"])
        self.assertIn("Only report crashes.", prompt)
        self.assertNotIn(review.DEFAULT_BRIEF, prompt)

    def test_an_unset_brief_is_none_so_the_default_applies(self) -> None:
        code, _result = self.one_step(self.args({"harness": "codex"}))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIsNone(self.double.calls[0]["brief"])

    def test_a_settings_file_the_branch_could_have_written_is_not_the_operators_word(self) -> None:
        root, runtime = self.life.root, self.runtime_root
        link_dir = self.life.td / "link"
        link_dir.symlink_to(root, target_is_directory=True)
        places = {
            "repository": root / "settings.json", "ai-review": root / ".ai-review" / "s.json",
            "reviews": runtime / "reviews" / "j1" / "s.json", "symlink": link_dir / "s.json",
            "dotdot": self.life.td / "config" / ".." / "repo" / "d.json"}
        for name, path in places.items():
            with self.subTest(place=name):
                effective = self.brief_options(path)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    codex = cli._reviewer_options(effective, root, runtime)
                    claude = cli._review_mode_options(effective, root, runtime)["same_provider"]
                self.assertEqual((codex["brief"], claude["brief"]), (None, None))
                self.assertEqual(stderr.getvalue().count("warning:"), 2)
                self.assertIn("reviewer.brief is ignored", stderr.getvalue())
                self.assertEqual(effective["reviewer.harness"], "codex")  # the other rows are still read

    def test_a_settings_file_outside_them_is_honoured(self) -> None:
        effective = self.brief_options(self.life.td / "elsewhere" / "s.json")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            options = cli._reviewer_options(effective, self.life.root, self.runtime_root)
        self.assertEqual((options["brief"], stderr.getvalue()), ("Only report crashes.", ""))

    def test_a_hand_built_reviewer_dict_without_a_brief_means_the_default(self) -> None:
        reviewer = {"harness": "codex", "model": "gpt-6.1-sol", "effort": "high", "timeout_seconds": 3600,
                    "binary": "codex", "operator_home": str(self.life.td / "home")}
        args = self.args({"harness": "codex"})
        double = LaunchDouble(self, self.life)
        with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError("no Claude worker"))), \
                mock.patch.object(review, "launch_review", double), \
                mock.patch.object(cli, "_reviewer_options", lambda *a, **k: reviewer):
            code, _result = cli._run_one_step(args, self.runtime_root, IDENTITY, self.life.inspect())
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIsNone(double.calls[0]["brief"])

    def test_an_unconfigured_run_step_still_stops_at_the_gate_with_exit_10(self) -> None:
        code, result = self.one_step(self.args(None))
        self.assertEqual((code, result["status"]), (cli.EXIT_GATE, job.STATUS_GATE_BLOCKED))
        self.assertEqual(self.double.calls, [])

    def test_a_configured_step_runs_the_review_and_exits_ok(self) -> None:
        code, result = self.one_step(self.args({"harness": "codex"}))
        self.assertEqual((code, result["status"]), (cli.EXIT_OK, job.STATUS_FINISHED))
        self.assertEqual(self.life.phase(), "AWAITING_PLAN_APPROVAL")

    def test_a_limit_a_timeout_and_an_empty_run_exit_35_and_a_refused_ingest_exits_30(self) -> None:
        for outcome in ("limit", "timeout", "empty"):
            with self.subTest(outcome=outcome):
                code, result = self.one_step(self.args({"harness": "codex"}), outcome=outcome)
                self.assertEqual((code, result["status"]), (cli.EXIT_INCOMPLETE, job.STATUS_INCOMPLETE))
        code, result = self.one_step(self.args({"harness": "codex"}), outcome="mismatch")
        self.assertEqual(code, cli.EXIT_WORKER_FAILED)

    def test_ctrl_c_in_a_review_propagates_for_the_documented_exit_130(self) -> None:
        args = self.args({"harness": "codex"})
        double = LaunchDouble(self, self.life, **{"raise": KeyboardInterrupt()})
        stderr = io.StringIO()
        with mock.patch.object(worker, "launch", mock.Mock()), mock.patch.object(review, "launch_review", double), \
                mock.patch.object(cli, "require_pinned_execution", lambda: None), \
                mock.patch.object(cli, "_dispatch", lambda a, argv: cli.cmd_step(args, self.runtime_root, IDENTITY)), \
                contextlib.redirect_stderr(stderr):
            # the interrupt propagates unchanged (the interpreter's own exit is the documented 130, as for
            # a Claude job's Ctrl-C), not a Controller exit code
            with self.assertRaises(KeyboardInterrupt):
                cli.main(["step", str(self.life.root)])
        [record] = self.records()
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)


class ReplayTest(_ReviewCase):
    """D9: a Controller killed mid-review leaves a live Codex group in its own
    session. `resume` ends it by ownership tag and start ticks, copies the
    credentials back, removes the copy and settles the reservation."""

    def spawn_codex(self, job_id: str) -> subprocess.Popen:
        env = {**os.environ, OWNERSHIP_VAR: job_id}
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], env=env,
                                start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        return proc

    def killed_controller(self, *, flush_process: bool) -> tuple[dict, subprocess.Popen]:
        """Run a review whose Controller dies right after the spawn (before or
        after the `worker_process` flush)."""
        procs: list = []
        test = self

        class Dies(BaseException):
            pass

        def launch(**kw):
            home = review.JobLayout(kw["runtime_root"], kw["job_id"])
            (home.codex_home).mkdir(parents=True, exist_ok=True)
            (home.codex_home / "auth.json").write_text(json.dumps({"tokens": {"refresh_token": "rotated"}}))
            (home.tree).mkdir(parents=True, exist_ok=True)
            proc = test.spawn_codex(kw["job_id"])
            procs.append(proc)
            if flush_process:
                kw["on_spawn"](proc.pid, proc.pid)
            raise Dies()

        with mock.patch.object(worker, "launch", mock.Mock()), mock.patch.object(review, "launch_review", launch):
            with self.assertRaises(Dies):
                job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                 reviewer=self.reviewer, usage_gate=self.gate())
        [record] = self.records()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        return record, procs[0]

    def resume(self) -> list[dict]:
        """`resume`'s report, for the review records (the lifecycle's earlier jobs are history)."""
        return [r for r in job.resume(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root)
                if r.get("kind") == "review"]

    def assert_replay(self, record: dict, proc: subprocess.Popen) -> dict:
        self.assertIsNone(proc.poll())
        [resumed] = self.resume()
        self.assertEqual(resumed["job_id"], record["job_id"])
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertTrue(resumed["reconciled_this_call"])
        self.assertEqual(resumed["reconciliation_evidence"]["code"], job.REVIEW_INTERRUPTED_CODE)
        proc.wait(timeout=10)  # the group is dead
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        layout = review.JobLayout(self.runtime_root, record["job_id"])
        self.assertFalse(layout.tree.exists())  # the disposable copy is removed
        self.assertFalse((layout.codex_home / "auth.json").exists())
        # the lifecycle lock is free: the Codex group never held it
        with job._acquire_lifecycle_lock(self.runtime_root, self.life.inspect()):
            pass
        # settled at the forecast, not released
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["delta"], "unknown")
        self.assertEqual(self.shared()["reservations"], {})
        after = self.records()[0]
        self.assertEqual(after["usage"]["accounting"], "done")
        return after

    def test_a_killed_controller_with_the_codex_group_alive_is_resumed(self) -> None:
        record, proc = self.killed_controller(flush_process=True)
        self.assertIn("worker_process", record)
        # `step` refuses while the record is pending
        with self.assertRaises(PendingJobReconciliationError):
            self.step()
        self.assert_replay(record, proc)
        # a second resume settles nothing further
        self.assertEqual(len(self.resume()), 1)
        self.assertEqual(len(self.shared()["ledger"]), 1)

    def test_a_controller_killed_between_the_spawn_and_the_process_flush_is_still_found_by_tag(self) -> None:
        record, proc = self.killed_controller(flush_process=False)
        self.assertNotIn("worker_process", record)
        self.assertTrue(record["review"]["launched"])
        self.assert_replay(record, proc)
        self.assertEqual(len(self.resume()), 1)
        self.assertEqual(len(self.shared()["ledger"]), 1)

    def test_a_record_that_never_launched_is_released_with_no_charge(self) -> None:
        record, proc = self.killed_controller(flush_process=False)
        proc.kill()
        proc.wait()
        path = self.runtime_root / "jobs" / f"{record['job_id']}.json"
        path.write_text(json.dumps({**record, "status": job.STATUS_PLANNED,
                                    "review": {**record["review"], "launched": False}}))
        [resumed] = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(self.shared()["ledger"], [])
        self.assertEqual(self.shared()["reservations"], {})

    def test_the_credentials_are_copied_back_on_the_resume_branch(self) -> None:
        operator = Path(self.reviewer["operator_home"])
        digest = review._sha256_file(operator / "auth.json")
        record, proc = self.killed_controller(flush_process=True)
        self.assertEqual(record["review"]["captured_digest"], digest)
        [resumed] = self.resume()
        self.assertEqual(resumed["review"]["credentials"], "copied")
        self.assertEqual(json.loads((operator / "auth.json").read_text()), {"tokens": {"refresh_token": "rotated"}})

    def test_a_verdict_the_dead_run_left_is_kept_by_the_resume_branch(self) -> None:
        record, proc = self.killed_controller(flush_process=True)
        layout = review.JobLayout(self.runtime_root, record["job_id"])
        layout.verdict.write_text("Status: REVISE\n")
        [resumed] = self.resume()
        [entry] = resumed["review"]["verdicts"]
        self.assertEqual((entry["status"], entry["disposition"]), ("REVISE", "not-ingested: interrupted"))
        self.assertEqual((self.runtime_root / entry["path"]).read_text(), "Status: REVISE\n")
        self.assertFalse(layout.verdict.exists())

    def test_a_changed_operator_file_is_never_overwritten_by_the_resume_branch(self) -> None:
        operator = Path(self.reviewer["operator_home"])
        record, proc = self.killed_controller(flush_process=True)
        (operator / "auth.json").write_text(json.dumps({"tokens": {"refresh_token": "interactive"}}))
        [resumed] = self.resume()
        self.assertEqual(resumed["review"]["credentials"], "operator-changed")
        self.assertEqual(json.loads((operator / "auth.json").read_text()), {"tokens": {"refresh_token": "interactive"}})

    def test_an_expired_deadline_ends_the_replayed_record_incomplete(self) -> None:
        record, proc = self.killed_controller(flush_process=True)
        path = self.runtime_root / "jobs" / f"{record['job_id']}.json"
        path.write_text(json.dumps({**record, "review": {**record["review"], "launched_at": "2020-01-01T00:00:00Z"}}))
        [resumed] = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INCOMPLETE)
        self.assertEqual(resumed["reconciliation_evidence"]["code"], job.REVIEW_TIMEOUT_CODE)

    def test_abandon_uses_the_same_branch(self) -> None:
        record, proc = self.killed_controller(flush_process=True)
        abandoned = job.abandon(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                job_id=record["job_id"])
        self.assertEqual(abandoned["status"], job.STATUS_INTERRUPTED)
        proc.wait(timeout=10)
        with self.assertRaises(JobAbandonRefusedError):
            job.abandon(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root, job_id=record["job_id"])

    def test_a_group_that_cannot_be_judged_is_refused_not_guessed(self) -> None:
        record, proc = self.killed_controller(flush_process=True)
        unverifiable = worker.LivenessAssessment(worker.UNVERIFIABLE, "same_boot", "cannot tell", None, {}, {})
        with mock.patch.object(worker, "assess_worker_liveness", return_value=unverifiable):
            with self.assertRaises(Exception) as caught:
                self.resume()
        self.assertEqual(type(caught.exception).__name__, "LifecycleWorkerUnverifiableError")
        self.assertIsNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
