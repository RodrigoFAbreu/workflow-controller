"""`workflow-controller-review-modes` CP3: the same-provider review job.

At an external gate of row 14 the step runs the Claude harness when
``review_mode.choose`` selects it (a lowered policy signal under ``auto``, or
an explicit ``single-provider``): admitted against the Claude budget, recorded
with ``review.mode`` and ``review.lowered``, handed to ``record-external-result``
verbatim with a ``claude:`` run reference, and, when the Workflow refuses the
approval for the distinct-model rule, ended ``INCOMPLETE`` with a sticky stop
(D6). Runs on the real vendored Workflow of ``tests/test_review_job.py``;
``review.launch_review`` is its double (the real one is tested against the fake
``claude`` in ``tests/test_review_claude.py``). No test spends allowance or
makes a connection.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, review, review_mode, usage, worker  # noqa: E402
from tests import test_review_job as rj  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_review_mode import signal  # noqa: E402

_LOCAL_PLAN_MODEL = """
root = Path.cwd()
wid = sys.argv[1]
def mutate(state):
    local = state["work_items"][wid]["plan_review_stages"]["LOCAL_MODEL_PLAN_REVIEW"]
    local.update(reviewer_model="anthropic/claude-opus-5-5", verdict_sha256="0" * 64, run_ref="session:test")
    return state
ws.state_transaction(root, mutate)
"""


class SameProviderCase(rj._ReviewCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.dict(review.HARNESSES, {"claude": review.CLAUDE}))
        home = self.life.td / "claude-home"
        home.mkdir()
        (home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "orig"}}))
        self.same = {"harness": "claude", "model": "claude-opus-5-5", "effort": "high", "timeout_seconds": 600,
                     "binary": "claude", "operator_home": str(home)}

    def mode_step(self, mode="auto", capture="lowering_only", *, reviewer_options=None, usage_gate=None, **scenario):
        options = {"mode": mode, "source": "cli", "same_provider": self.same}
        double = self.double = rj.LaunchDouble(self, self.life, **scenario)
        forbidden = mock.Mock(side_effect=AssertionError("no Claude worker may launch"))
        read = None if capture is None else signal(capture)
        with mock.patch.object(job, "_review_mode_signal", return_value=read), \
                mock.patch.object(worker, "launch", forbidden), mock.patch.object(review, "launch_review", double):
            return job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                    reviewer=reviewer_options, usage_gate=usage_gate, review_mode_options=options)

    def refuse_distinct(self):
        """A same-provider APPROVE the real Workflow refuses: the local stage is already of family anthropic."""
        self.life.user_step(_LOCAL_PLAN_MODEL)
        return self.mode_step()


class RunTest(SameProviderCase):
    def test_a_lowered_policy_runs_the_claude_review_and_the_workflow_records_it(self) -> None:
        calls: list = []
        record = self.mode_step(usage_gate=self.gate(calls=calls))
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(self.life.phase(), "AWAITING_PLAN_APPROVAL")
        [kw] = self.double.calls
        self.assertEqual((kw["harness_name"], kw["binary_name"], kw["model"], kw["effort"]),
                         ("claude", "claude", "claude-opus-5-5", "high"))
        self.assertEqual(kw["operator_home"], Path(self.same["operator_home"]))
        block = record["review"]
        self.assertEqual((block["harness"], block["mode"], block["mode_source"], block["lowered"]),
                         ("claude", "auto", "cli", True))
        self.assertEqual(block["signal_key"], ["warn", True, False, False])
        self.assertEqual(record["worker_route"]["harness"], "claude")
        self.assertEqual(record["worker_route"]["sources"]["model"], "reviewer.same_provider_model")
        # D8: admitted against the Claude budget, not the Codex one
        self.assertEqual([(provider, model) for provider, _, model in calls], [("claude", "claude-opus-5-5")])

    def test_the_verdict_is_handed_over_verbatim_with_a_claude_run_reference(self) -> None:
        real = job.protocol.record_external_result
        seen: list = []

        def spy(root, work_item, kind, path, **kwargs):
            seen.append((kind, Path(path).read_text(), kwargs["run_ref"]))
            return real(root, work_item, kind, path, **kwargs)

        with mock.patch.object(job.protocol, "record_external_result", spy):
            record = self.mode_step()
        [(kind, text, run_ref)] = seen
        self.assertEqual((kind, run_ref), ("plan_review_verdict", f"claude:{record['job_id']}"))
        self.assertIn("Reviewer model: anthropic/claude-opus-5-5", text)
        self.assertEqual(text, rj.verdict_text(self.double.calls[0]))  # byte for byte what the harness returned

    def test_a_revise_is_returned_to_the_workflow(self) -> None:
        record = self.mode_step(status="REVISE")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["review"]["verdict_status"], "REVISE")
        self.assertEqual(record["review"]["ingested"], "recorded")
        self.assertNotEqual(self.life.phase(), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

    def test_single_provider_runs_only_with_the_signal(self) -> None:
        refused = self.mode_step("single-provider", None)
        self.assertEqual(refused["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])
        self.assertIn("(1)", json.dumps(refused))
        self.assertNotIn("(2)", json.dumps(refused))  # the human gate does not release an explicit single-provider
        self.assertIn("--review-mode manual", json.dumps(refused))
        ran = self.mode_step("single-provider")
        self.assertEqual(ran["status"], job.STATUS_FINISHED)
        self.assertEqual((ran["review"]["mode"], ran["review"]["lowered"]), ("single-provider", True))

    def test_a_limit_mid_review_ends_the_job_incomplete_naming_claude(self) -> None:
        record = self.mode_step(outcome="limit", detail="rejected")
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.REVIEW_LIMIT_CODE)
        self.assertIn("The Claude plan review", job._review_message(record))

    def test_a_codex_review_records_the_mode_and_no_lowered_label(self) -> None:
        options = {"mode": "cross-model", "source": "file", "same_provider": self.same}
        double = rj.LaunchDouble(self, self.life)
        with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError)), \
                mock.patch.object(review, "launch_review", double):
            record = job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                      reviewer=self.reviewer, review_mode_options=options)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual((record["review"]["mode"], record["review"]["mode_source"]), ("cross-model", "file"))
        self.assertNotIn("lowered", record["review"])
        self.assertEqual(record["review"]["harness"], "codex")


class DistinctRefusalTest(SameProviderCase):
    def test_the_workflows_refusal_ends_the_job_incomplete_and_names_the_ways_out(self) -> None:
        record = self.refuse_distinct()
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE, record.get("reconciliation_evidence"))
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["code"], job.REVIEW_DISTINCT_MODEL_CODE)
        for words in ("(1)", "(2)", "(3)", "GATE_POLICY", "GATE_POLICY.json", "--review-mode cross-model"):
            self.assertIn(words, evidence["message"])
        self.assertEqual(self.life.phase(), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")  # nothing was recorded
        self.assertIn("The Claude plan review", job._review_message(record))

    def test_the_next_step_stops_without_launching_while_nothing_changed(self) -> None:
        self.refuse_distinct()
        for _ in range(2):  # repeatable
            record = self.mode_step()
            self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
            self.assertIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(record))
            self.assertEqual(self.double.calls, [])
        self.assertEqual(len(self.records()), 1)
        # an unrelated unreferenced_runs warning leaves the key, and so the stop, alone
        record = self.mode_step(capture="lowering_plus_unreferenced_runs")
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])

    def test_single_provider_stops_too_and_leaves_path_two_out(self) -> None:
        self.refuse_distinct()
        record = self.mode_step("single-provider")
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])
        self.assertNotIn("(2)", json.dumps(record))

    def test_a_mode_or_harness_that_selects_the_gate_or_a_distinct_provider_goes_ahead(self) -> None:
        self.refuse_distinct()
        manual = self.mode_step("manual")
        self.assertEqual(manual["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(manual))
        # a cancelled signal (the gate turned human: a file_tightened clause) releases auto to the ordinary gate
        gate = self.mode_step("auto", "lowering_plus_file_tightened")
        self.assertEqual(gate["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(gate))
        self.assertEqual(self.double.calls, [])
        # a distinct provider runs, at an unchanged state identity and signal
        codex = self.mode_step("auto", reviewer_options=self.reviewer)
        self.assertEqual(codex["status"], job.STATUS_FINISHED)
        self.assertEqual(self.double.calls[0]["harness_name"], "codex")

    def test_cross_model_with_codex_launches_after_the_refusal(self) -> None:
        self.refuse_distinct()
        record = self.mode_step("cross-model", reviewer_options=self.reviewer)
        self.assertEqual(record["status"], job.STATUS_FINISHED)

    def test_a_changed_state_identity_or_signal_key_releases_the_stop(self) -> None:
        self.refuse_distinct()
        root, wid = self.life.root, rj.WID
        key = signal("lowering_only").key("plan")
        [prior] = self.records()
        identity = prior["review"]["state_identity"]
        stop = lambda *a: job._sticky_review_stop(self.runtime_root, root, wid, *a)  # noqa: E731
        self.assertTrue(stop("plan", identity, key))
        self.assertFalse(stop("plan", "other-identity", key))
        self.assertFalse(stop("implementation", identity, key))
        self.assertFalse(stop("plan", identity, signal("lowering_plus_file_tightened").key("plan")))
        self.assertFalse(stop("plan", None, key))

    def test_a_later_review_job_supersedes_the_refusal(self) -> None:
        self.refuse_distinct()
        self.assertEqual(self.mode_step("auto", reviewer_options=self.reviewer)["status"], job.STATUS_FINISHED)
        [prior, later] = sorted(self.records(), key=lambda r: (r["created_at"], r["job_id"]))
        self.assertFalse(job._sticky_review_stop(
            self.runtime_root, self.life.root, rj.WID, "plan", prior["review"]["state_identity"],
            signal("lowering_only").key("plan")))


class RemediesTest(unittest.TestCase):
    def test_the_paths_named_follow_the_mode(self) -> None:
        auto = review_mode.remedies("auto", "implementation")
        self.assertIn("technical_approval.require", auto)
        self.assertIn("(2)", auto)
        single = review_mode.remedies("single-provider", "plan")
        self.assertNotIn("(2)", single)
        self.assertIn("--review-mode manual", single)
        self.assertNotIn("--review-mode manual", auto)


if __name__ == "__main__":
    unittest.main()
