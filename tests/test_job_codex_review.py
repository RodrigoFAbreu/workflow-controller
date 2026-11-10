"""`workflow-controller-h4e2b` CP2: a routed Codex review run as a job (`controller.job`, `controller.codex_review_role`).

Offline, through ``job.execute_step``: the managed repository is the recorder tests' fixture (the installed Workflow, a
generated bundle, the work item at ``AWAITING_LOCAL_PLAN_REVIEW`` or ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW``), the
route sends the review role to ``codex``, ``run_review`` and the recorder are the real ones and the worker is the
scripted fake Codex behind the real launcher (as ``tests/test_codex_review_role.py`` does). D3's table has one test per
ending, D4's recovery sites one test per site and stage.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_review_role as crr, codex_worker as cw, codex_worker_job, job, managed_repo  # noqa: E402
from controller import review, review_recorder, routing, usage, worker  # noqa: E402
from controller.errors import WorkflowQueryError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_codex_review_role import ARMOUR, RoleCase, message  # noqa: E402
from tests.test_job import FAKE_IDENTITY  # noqa: E402
from tests.test_review_recorder import FEEDBACK, STATE, WID  # noqa: E402

#: The fake process group of a "live" review in these tests. Never 1: ``os.killpg(1, sig)`` is ``kill(-1, sig)`` and ends
#: the whole desktop session. Every test that can reach a signal also replaces ``os.killpg`` and ``os.kill``.
FAKE_GROUP = 424242

REAL_LAUNCH = cw.launch_codex_worker
REAL_RECORD = review_recorder.record
REAL_RUN_REVIEW = crr.run_review


class ReviewJobCase(RoleCase):
    """A fixture of ``STAGE`` with the review role routed to ``codex``, driven through ``execute_step``."""

    ROLE = routing.REVIEW_PLAN

    @classmethod
    def setUpClass(cls) -> None:
        # The recorder's implementation fixture leaves CP1 COMPLETE with no checkpoint commit, which the Workflow's
        # `verify` calls unhealthy; the job needs a healthy target, so its "implemented" commit carries the trailers.
        real = fixtures.commit_all

        def commit_all(root, subject, *args, **kw):
            if subject == "implemented":
                subject = f"implemented\n\nWorkflow-Checkpoint: CP1\nWorkflow-Work-Item: {WID}"
            return real(root, subject, *args, **kw)

        with mock.patch.object(fixtures, "commit_all", commit_all):
            super().setUpClass()

    def reset(self) -> None:
        """A second job in one test: a fresh runtime of jobs and workers, the first job's id again."""
        shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
        shutil.rmtree(self.layout.base, ignore_errors=True)
        self.ids = 0
        self.launched.clear()

    def setUp(self) -> None:
        super().setUp()
        self.stub = fixtures.write_stub_workflow_manager(self.tmp / "workflow-manager", release=self.release())
        self.target = managed_repo.inspect(self.root, manager_bin=str(self.stub))
        self.options = self.routing_options()
        self.enterContext(mock.patch.object(job, "CODEX_HOME_RESOLVER", lambda: self.operator))
        self.enterContext(mock.patch.object(cw, "launch_codex_worker", self.fake_launch))
        self.ids = 0
        self.enterContext(mock.patch.object(job, "_new_job_id", self.next_id))
        self.worker_reconciler = self.enterContext(mock.patch.object(
            job, "_reconcile_codex_worker", side_effect=AssertionError("a review record reached the worker reconciler")))
        self.worker_runner = self.enterContext(mock.patch.object(
            job, "_run_codex_worker", side_effect=AssertionError("the Codex worker's branch ran for a review")))

    # -- fixture -------------------------------------------------------------

    def release(self) -> str:
        return json.loads((self.root / ".workflow-manager" / "installation.json").read_text())["workflow_version"]

    def next_id(self) -> str:
        self.ids += 1
        return f"job{self.ids}"

    def routing_options(self, *, fallback: bool = False) -> routing.RoutingOptions:
        entry = {"harness": "codex", "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT}
        if fallback:
            entry["fallback"] = [{"harness": "claude-code", "model": "opus"}]
        config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": {self.ROLE: entry}}),
                                              path="<test>")
        return routing.RoutingOptions(config=config, config_source="settings")

    def fake_launch(self, task, *, runtime_root, job_id, operator_home, cwd, timeout, model, effort, prompt,
                    on_spawn=None, **rest):
        self.launched.append({"task": task, "prompt": prompt, "cwd": cwd})
        return REAL_LAUNCH(
            task, runtime_root=runtime_root, job_id=job_id, operator_home=operator_home, cwd=cwd, timeout=timeout,
            on_spawn=on_spawn, model=model, effort=effort, prompt=prompt, binary_name=str(self.binary),
            system_dir=self.system, real_home=self.real_home, run=self.controls(job_id))

    def step(self, **kw):
        if not self.recorded:
            self.record()
        kw.setdefault("timeout", 30)
        return job.execute_step(self.target, identity=FAKE_IDENTITY, runtime=self.runtime, routing=self.options, **kw)

    def only_record(self) -> dict:
        (path,) = sorted((self.runtime / "jobs").glob("*.json"))
        return json.loads(path.read_text())

    def job_layout(self, job_id: str = "job1"):
        return crr.codex_worker_sandbox.WorkerLayout(self.runtime, job_id)

    def refs(self) -> str:
        return self.snapshot()[2]

    # -- assertions ----------------------------------------------------------

    def assert_nothing_recorded(self, before: tuple) -> None:
        """The managed repository is as it was: state, tree, refs and feedback; and the clone is gone."""
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.layout.worktree.exists())
        self.assertNotIn("landing", self.refs())

    def assert_no_armour(self) -> None:
        for path in (self.runtime / "jobs").rglob("*"):
            if path.is_file():
                self.assertNotIn("PRIVATE KEY", path.read_text(errors="replace"), str(path))

    def assert_valid(self, record: dict) -> None:
        validity = job.validate_record(record, managed_repo=self.target, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid, validity)


class RowTests:
    """Tests that hold for either stage: D3's table, one test per ending."""

    def test_a_routed_review_ends_finished_with_the_verdict_recorded(self) -> None:
        self.script(message=message("APPROVE"))
        record = self.step()
        self.assertEqual((record["status"], record["transition_verified"]), (job.STATUS_FINISHED, True))
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)
        self.assertEqual(record["worker_route"]["harness"], "codex")
        block = record["codex_review"]
        self.assertEqual((block["stage"], block["result"]["code"]), ("recorded", crr.RECORDED))
        self.assertTrue((self.root / FEEDBACK).is_file())
        self.assertNotEqual(record["observed_phase_after"], record["pre_state"]["phase"])
        self.assertEqual(record["usage_record"]["harness"], "codex")
        self.assertEqual(record["worker_streams"]["format"], "text")
        # a review is not a worker: no worker block, no landing, no ref, no clone, no review-kind record
        self.assertNotIn("codex_worker", record)
        self.assertNotIn("kind", record)
        self.assertNotIn("landing", self.refs())
        self.assertFalse(self.layout.worktree.exists())
        self.assertEqual(len(self.launched), 1)
        self.assertTrue(job._is_codex_review_record(record))
        self.assertFalse(job._is_codex_worker_record(record))
        self.assert_valid(record)
        self.worker_runner.assert_not_called()

    def test_the_reviewer_brief_reaches_the_prompt(self) -> None:
        self.script(message=message("APPROVE"))
        self.step(reviewer={"brief": "Look hard at the migration."})
        self.assertIn("Look hard at the migration.", self.launched[0]["prompt"])

    def test_the_model_and_effort_are_the_routes(self) -> None:
        self.script(message=message("APPROVE"))
        seen = {}

        def run(*args, **kw):
            seen.update(model=kw["model"], effort=kw["effort"], stage=kw["stage"], base=kw["base"])
            return REAL_RUN_REVIEW(*args, **kw)

        with mock.patch.object(job.codex_review_role, "run_review", run):
            self.step()
        self.assertEqual((seen["model"], seen["effort"], seen["stage"]),
                         (review.DEFAULT_MODEL, review.DEFAULT_EFFORT, self.STAGE))
        self.assertEqual(seen["base"], self.base)

    def test_a_decision_with_no_protocol_block_starts_nothing(self) -> None:
        self.record()
        record = {"job_id": "jobx", "status": job.STATUS_LAUNCHED, "work_item_id": WID}
        decision = types.SimpleNamespace(protocol=None)
        route = types.SimpleNamespace(role=self.ROLE, harness="codex", model="m", effort="e")
        ended = job._run_codex_review(
            self.target, runtime_root=self.runtime, run_id=None, job_id="jobx", record=record, decision=decision,
            route=route, timeout=5, pre_state={"target_head": self.base}, worker_streams={}, usage_block=None,
            usage_end=None, ticker=None, brief=None)
        self.assertEqual(ended["status"], job.STATUS_FAILED)
        evidence = ended["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["error"]),
                         (job.WORKER_NOT_STARTED_CODE, "CodexReviewNeedsProtocolDecision"))
        self.assertEqual(self.launched, [])
        self.assertNotIn("worker_process", ended)

    def test_record_pending_is_judged_by_the_workflow_and_names_the_pending_commit(self) -> None:
        self.script(message=message("APPROVE"))

        def pending(*args, **kw):
            real = REAL_RECORD(*args, **kw)
            return dataclasses.replace(real, ok=False, cause="recorder_commit_pending", workflow_class="Pending",
                                       message="the entry awaits its commit")

        with mock.patch.object(review_recorder, "record", pending):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.CODEX_REVIEW_COMMIT_PENDING_CODE)
        self.assertEqual(record["codex_review"]["result"]["code"], crr.RECORD_PENDING)
        self.assertTrue((self.root / FEEDBACK).is_file())
        self.assertFalse(self.layout.worktree.exists())
        self.assert_valid(record)

    def test_not_started_is_failed_with_the_cause_and_no_process(self) -> None:
        self.script(message=message("APPROVE"))
        refused = review_recorder.RecorderResult(False, "recorder_identity_mismatch", None, "mismatch", {})
        before = self.snapshot()
        with mock.patch.object(review_recorder, "check", return_value=refused):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["error"]),
                         (job.WORKER_NOT_STARTED_CODE, "recorder_identity_mismatch"))
        self.assertNotIn("worker_process", record)
        self.assertEqual((self.launched, self.execs()), ([], []))
        self.assertTrue(job.never_launched(record))
        self.assert_nothing_recorded(before)
        self.assert_valid(record)

    def test_a_failed_worker_is_failed_with_the_launchers_subtype(self) -> None:
        self.script(message="", exit=1)
        before = self.snapshot()
        record = self.step()
        self.assertEqual((record["status"], record["worker_outcome"]), (job.STATUS_FAILED, worker.FAILURE))
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["reason"], "worker_outcome")
        self.assertEqual(evidence["codex_review"], {"code": crr.WORKER_FAILED, "cause": "non_zero_exit"})
        self.assertEqual(record["worker"]["subtype"], "non_zero_exit")
        self.assert_nothing_recorded(before)
        self.assert_valid(record)

    def test_a_usage_limit_keeps_its_fact_and_takes_the_wait_path(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        before = self.snapshot()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED)
        fact = job.usage_limit_of(record)
        self.assertIsNotNone(fact)
        self.assertEqual(record["usage_limit"], fact)
        self.assert_nothing_recorded(before)
        self.assert_valid(record)

    def test_a_sandbox_breach_is_failed(self) -> None:
        self.script(message=message("APPROVE"),
                    write_outside=[str(self.layout.worktree / ".git" / "hooks" / "post-merge")])
        before = self.snapshot()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["cause"]), (crr.WORKER_FAILED, "sandbox_breach"))
        self.assertNotIn("usage_limit", record)
        self.assert_nothing_recorded(before)
        self.assert_valid(record)

    def test_a_refused_ending_names_the_cause_and_holds_no_armour(self) -> None:
        self.script(message=message("APPROVE", extra=f" {ARMOUR}"))
        before = self.snapshot()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["cause"]), (crr.REFUSED, "credential_in_verdict"))
        self.assertEqual(record["worker"]["result_excerpt"] or "", "")
        self.assertEqual(Path(record["worker_streams"]["stdout_path"]).read_text(), "")
        self.assert_no_armour()
        self.assert_nothing_recorded(before)
        self.assert_valid(record)

    def test_a_changed_state_identity_is_failed_and_nothing_is_recorded(self) -> None:
        self.script(message=message("APPROVE"))

        def changed(*args, **kw):
            return REAL_RUN_REVIEW(*args, **{**kw, "state_identity": "not-the-identity"})

        before = self.snapshot()
        with mock.patch.object(job.codex_review_role, "run_review", changed):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], crr.STATE_IDENTITY_CHANGED)
        self.assertNotIn("verdict", record["reconciliation_evidence"])
        self.assert_nothing_recorded(before)
        self.assert_valid(record)

    def test_a_workflow_release_change_is_failed_with_its_reason(self) -> None:
        self.script(message=message("APPROVE"))

        def change(task, **kw):
            result = self.fake_launch(task, **kw)
            script = self.root / "scripts" / "workflow_state.py"
            script.write_text(script.read_text() + "\n# changed\n")
            return result

        with mock.patch.object(cw, "launch_codex_worker", change):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
        self.assertFalse((self.root / FEEDBACK).exists())
        self.assertFalse(self.layout.worktree.exists())
        self.assert_valid(record)

    def test_a_refused_record_keeps_the_one_verdict_file_at_mode_0600(self) -> None:
        self.script(message=message("APPROVE"))
        calls = {"n": 0}

        def refused(*args, **kw):
            calls["n"] += 1
            raise WorkflowQueryError("timed out", evidence={})

        with mock.patch.object(review_recorder, "record", refused):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["cause"]), (crr.RECORD_REFUSED, "WorkflowQueryError"))
        self.assertIn("state_is_the_truth", evidence)
        kept = evidence["verdict_file"]
        path = self.runtime / "reviews" / "job1-verdict.md"
        self.assertEqual(kept["path"], str(path))
        self.assertEqual((path.stat().st_mode & 0o777, kept["mode"]), (0o600, "0600"))
        self.assertEqual(kept["sha256"], crr.hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(sorted(p.name for p in self.runtime.rglob("*verdict*")), ["job1-verdict.md"])
        self.assertNotIn("verdict", evidence)
        self.assertEqual(calls["n"], 1)
        self.assertFalse(self.layout.worktree.exists())
        self.assert_valid(record)

    def test_a_refused_record_leaves_the_verdict_text_in_no_file_but_the_one_verdict_file(self) -> None:
        """Review I1: the returned record, every runtime file and every event hold the verdict marker only in
        ``reviews/<job>-verdict.md`` (0600): not in ``worker.result_excerpt``, the streams or ``last-message.md``."""
        marker = "UNIQUE-VERDICT-MARKER-4f1c9"
        self.script(message=message("APPROVE", extra=" " + marker))

        def refused(*args, **kw):
            raise WorkflowQueryError("timed out", evidence={})

        with mock.patch.object(review_recorder, "record", refused):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertNotIn(marker, json.dumps(record, default=str))
        holders = [p for p in self.runtime.rglob("*") if p.is_file() and marker.encode() in p.read_bytes()]
        self.assertEqual([p.relative_to(self.runtime).as_posix() for p in holders], ["reviews/job1-verdict.md"])
        self.assertEqual(holders[0].stat().st_mode & 0o777, 0o600)


class UsageTests:
    """The usage gate of a routed review: the Codex provider on both paths, a hold leaves no record."""

    def gate(self, route, *args, **kwargs):
        self.asked.append((route.harness, kwargs))
        provider = kwargs.get("provider", usage.PROVIDER_CLAUDE)
        admission = usage.admit(
            self.runtime, provider=provider, role=route.role, model=route.model, repository=str(self.root),
            run_id=None, limits=usage.Limits(provider=provider), now=job.usage_now(), readings=self.seed)
        return admission.hold if admission.hold is not None else admission.token

    def snap(self) -> dict:
        return usage.snapshot(self.runtime, now=job.usage_now())

    def test_the_single_route_asks_the_gate_with_the_codex_provider_and_is_accounted(self) -> None:
        self.asked, self.seed = [], []
        self.script(message=message("APPROVE"))
        record = self.step(usage_gate=self.gate)
        self.assertEqual(self.asked, [("codex", {"provider": usage.PROVIDER_CODEX})])
        self.assertEqual(record["usage"]["accounting"], "done")
        (entry,) = self.snap()["ledger"]
        self.assertEqual(entry["provider"], usage.PROVIDER_CODEX)
        self.assertEqual(self.snap()["reservations"], {})

    def test_the_chain_path_asks_the_gate_with_the_codex_provider(self) -> None:
        self.asked, self.seed = [], []
        self.options = self.routing_options(fallback=True)
        self.script(message=message("APPROVE"))
        record = self.step(usage_gate=self.gate)
        self.assertEqual(self.asked[0], ("codex", {"provider": usage.PROVIDER_CODEX}))
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["usage"]["accounting"], "done")
        self.assertEqual(record["worker_route"]["selection"]["position"], 0)

    def test_a_claude_route_keeps_the_unchanged_gate_call(self) -> None:
        gate = mock.Mock(return_value=usage.Hold(
            provider=usage.PROVIDER_CLAUDE, window=usage.FIVE_HOUR, percent=99.0, forecast=8.0, resume_at=None,
            reason="x", waitable=False))
        self.options = routing.NO_OVERRIDES
        held = self.step(usage_gate=gate)
        self.assertIsInstance(held, job.UsageHold)
        (call,) = gate.call_args_list
        self.assertEqual((len(call.args), call.kwargs), (1, {}))

    def test_a_hold_leaves_no_record_no_reservation_and_no_process(self) -> None:
        self.asked = []
        self.seed = [usage.make_reading(usage.PROVIDER_CODEX, usage.FIVE_HOUR, 99.0, time.time() + 3600,
                                        time.time(), "test")]
        self.script(message=message("APPROVE"))
        before = self.snapshot()
        held = self.step(usage_gate=self.gate)
        self.assertIsInstance(held, job.UsageHold)
        self.assertEqual(held.provider, usage.PROVIDER_CODEX)
        self.assertEqual(list((self.runtime / "jobs").glob("*.json")), [])
        self.assertEqual(self.snap()["reservations"], {})
        self.assertEqual((self.launched, self.execs()), ([], []))
        self.assertEqual(self.snapshot(), before)


class RecoveryTests:
    """D4: every recovery site of a ``codex_review`` record calls ``recover``; nothing records again."""

    STAGES = ("preparing", "recording", "recorded")

    def interrupt_at(self, stage: str, *, move: bool = False, refused: bool = False) -> dict:
        """Run the job and interrupt it (``KeyboardInterrupt``) once the block is at ``stage``, so the record stays
        pending. ``move`` lets the recorder write before the interrupt; ``refused`` makes it fail first."""
        self.script(message=message("APPROVE"))
        patches = []
        if stage == "preparing":
            patches.append(mock.patch.object(crr, "collect_inputs", side_effect=KeyboardInterrupt))
        elif stage == "recording":
            def record(*args, **kw):
                if move:
                    REAL_RECORD(*args, **kw)
                raise KeyboardInterrupt
            patches.append(mock.patch.object(review_recorder, "record", record))
        else:
            if refused:
                patches.append(mock.patch.object(review_recorder, "record",
                                                 side_effect=WorkflowQueryError("timed out", evidence={})))
            patches.append(mock.patch.object(job, "_codex_review_fields", side_effect=KeyboardInterrupt))
        with self.assertRaises(KeyboardInterrupt):
            from contextlib import ExitStack
            with ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                self.step()
        record = self.only_record()
        self.assertIn(record["status"], (job.STATUS_LAUNCHED, job.STATUS_COMPLETED))
        self.assertEqual(record["codex_review"]["stage"], stage)
        # a clone as an interrupted run could have left it: recovery must remove it and name none
        self.layout.worktree.mkdir(parents=True, exist_ok=True)
        return record

    def spy(self):
        spy = mock.Mock(side_effect=REAL_RECORD)
        self.enterContext(mock.patch.object(review_recorder, "record", spy))
        return spy

    def assert_recovered(self, record: dict, status: str, code: str, spy) -> dict:
        self.assertEqual(record["status"], status, record.get("reconciliation_evidence"))
        self.assertFalse(self.layout.worktree.exists())
        evidence = record.get("reconciliation_evidence")
        if evidence is not None:
            self.assertNotIn("clone", evidence)
        if code:
            self.assertEqual(evidence["code"], code)
        spy.assert_not_called()
        self.worker_reconciler.assert_not_called()
        self.assert_valid(record)
        return record

    def resumed(self) -> dict:
        (record,) = job.resume(self.target, identity=FAKE_IDENTITY, runtime=self.runtime)
        return record

    # -- resume ----------------------------------------------------------------

    def test_resume_of_a_preparing_block_is_interrupted_and_reviews_again_later(self) -> None:
        self.interrupt_at("preparing")
        spy = self.spy()
        record = self.assert_recovered(self.resumed(), job.STATUS_INTERRUPTED, crr.NOT_RECORDED, spy)
        self.assertIn("reviews again", record["reconciliation_evidence"]["message"])

    def test_resume_of_a_recording_block_takes_the_workflows_judgement(self) -> None:
        with self.subTest("nothing moved"):
            self.interrupt_at("recording")
            spy = self.spy()
            self.assert_recovered(self.resumed(), job.STATUS_INTERRUPTED, None, spy)

    def test_resume_of_a_recording_block_whose_state_moved_is_finished(self) -> None:
        self.interrupt_at("recording", move=True)
        spy = self.spy()
        record = self.assert_recovered(self.resumed(), job.STATUS_FINISHED, None, spy)
        self.assertTrue(record["transition_verified"])
        self.assertTrue((self.root / FEEDBACK).is_file())

    def test_resume_of_a_recorded_block_takes_the_blocks_result(self) -> None:
        self.interrupt_at("recorded")
        spy = self.spy()
        record = self.assert_recovered(self.resumed(), job.STATUS_FINISHED, None, spy)
        self.assertTrue(record["transition_verified"])

    def test_resume_of_a_recorded_refusal_keeps_the_refusal(self) -> None:
        self.interrupt_at("recorded", refused=True)
        spy = self.spy()
        record = self.assert_recovered(self.resumed(), job.STATUS_FAILED, crr.RECORD_REFUSED, spy)
        self.assertEqual(record["reconciliation_evidence"]["verdict_file"]["mode"], "0600")

    def test_an_unknown_stage_is_a_recovery_error(self) -> None:
        record = self.interrupt_at("preparing")
        broken = {**record, "codex_review": {"stage": "mystery"}}
        job._persist(self.runtime, record["job_id"], broken, event="test")
        with self.assertRaises(crr.CodexReviewRecoveryError):
            self.resumed()

    def active(self):
        return worker.LivenessAssessment(verdict=worker.ACTIVE, row="same_boot", reason="test", process=None,
                                         recorded={"pid": FAKE_GROUP, "pgid": FAKE_GROUP}, current={})

    def test_resume_phase_one_never_re_attaches_to_a_review_whose_process_is_alive(self) -> None:
        record = self.interrupt_at("preparing")
        job._persist(self.runtime, record["job_id"], {**record, "worker_process": {"pid": FAKE_GROUP, "pgid": FAKE_GROUP}},
                     event="test")
        with mock.patch.object(worker, "assess_worker_liveness", return_value=self.active()), \
                mock.patch.object(worker, "reattach", side_effect=AssertionError("re-attached")) as reattach, \
                mock.patch.object(os, "killpg") as killpg, mock.patch.object(os, "kill") as kill:
            self.resumed()
        reattach.assert_not_called()
        self.assertNotIn(1, [call.args[0] for call in killpg.call_args_list])   # killpg(1, ...) ends the session
        self.assertNotIn(1, [call.args[0] for call in kill.call_args_list])

    def test_abandon_of_a_review_whose_process_is_alive_gives_the_codex_guidance(self) -> None:
        record = self.interrupt_at("preparing")
        job._persist(self.runtime, record["job_id"], {**record, "worker_process": {"pid": FAKE_GROUP, "pgid": FAKE_GROUP}},
                     event="test")
        with mock.patch.object(worker, "assess_worker_liveness", return_value=self.active()), \
                mock.patch.object(os, "killpg"), mock.patch.object(os, "kill"):
            with self.assertRaises(job.LifecycleWorkerActiveError) as raised:
                job.abandon(self.target, identity=FAKE_IDENTITY, runtime=self.runtime, job_id=record["job_id"])
        self.assertIn("ends a Codex worker", str(raised.exception))
        self.assertNotIn("Follow it", str(raised.exception))

    # -- abandon ---------------------------------------------------------------

    def test_abandon_of_each_stage_is_the_operators_failure_and_leaves_no_clone(self) -> None:
        for n, stage in enumerate(self.STAGES):
            with self.subTest(stage):
                if n:
                    self.reset()
                record = self.interrupt_at(stage)
                spy = self.spy()
                abandoned = job.abandon(self.target, identity=FAKE_IDENTITY, runtime=self.runtime,
                                        job_id=record["job_id"])
                self.assert_recovered(abandoned, job.STATUS_FAILED, job.OPERATOR_ABANDONED_CODE, spy)
                self.assertEqual(abandoned["reconciliation_evidence"]["codex_review"]["stage"], stage)

    # -- drift -----------------------------------------------------------------

    def test_a_drifted_release_of_each_stage_is_failed_with_its_reason_and_leaves_no_clone(self) -> None:
        for n, stage in enumerate(self.STAGES):
            with self.subTest(stage):
                if n:
                    self.reset()
                record = self.interrupt_at(stage)
                spy = self.spy()
                ended = job._resume_drifted_protocol(record, managed_repo=self.target, runtime_root=self.runtime)
                self.assertEqual(ended["status"], job.STATUS_FAILED)
                self.assertEqual(ended["reconciliation_evidence"]["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
                self.assertEqual(ended["reconciliation_evidence"]["codex_review"]["stage"], stage)
                self.assertFalse(self.layout.worktree.exists())
                self.assertNotIn("clone", ended["reconciliation_evidence"])
                spy.assert_not_called()
                self.worker_reconciler.assert_not_called()

    # -- the post-spawn exception handler ----------------------------------------

    def run_raising(self, stage: str) -> dict:
        self.script(message=message("APPROVE"))
        if stage == "preparing":
            patch = mock.patch.object(crr, "collect_inputs", side_effect=RuntimeError("fault"))
        elif stage == "recording":
            patch = mock.patch.object(review_recorder, "record", side_effect=RuntimeError("fault"))
        else:
            patch = mock.patch.object(codex_worker_job, "stream_texts", side_effect=RuntimeError("fault"))
        with patch, self.assertRaises(RuntimeError):
            self.step()
        return self.only_record()

    def test_the_exception_handler_recovers_a_preparing_block(self) -> None:
        record = self.run_raising("preparing")
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(record["reconciliation_evidence"]["code"], crr.NOT_RECORDED)
        self.assertFalse(self.layout.worktree.exists())
        self.worker_reconciler.assert_not_called()
        self.assert_valid(record)

    def test_the_exception_handler_recovers_a_recording_block(self) -> None:
        record = self.run_raising("recording")
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED)
        self.assertFalse(self.layout.worktree.exists())
        self.assert_valid(record)

    def test_the_exception_handler_recovers_a_recorded_block(self) -> None:
        record = self.run_raising("recorded")
        self.assertEqual(record["codex_review"]["stage"], "recorded")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertFalse(self.layout.worktree.exists())
        self.assertTrue((self.root / FEEDBACK).is_file())
        self.assert_valid(record)

    def test_an_exception_after_the_terminal_write_is_never_recovered_again(self) -> None:
        # The handler recovers only a record that stopped part-way: once the terminal record is written, a later
        # exception (here the run's end mirror) re-raises and leaves that one terminal record as it is.
        self.script(message=message("APPROVE"))
        real_recover = crr.recover
        with mock.patch.object(job, "_run_job_ended", side_effect=RuntimeError("fault")), \
                mock.patch.object(crr, "recover", side_effect=real_recover) as recover, \
                self.assertRaises(RuntimeError):
            self.step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertNotIn("reconciled_at", record)
        recover.assert_not_called()
        self.assertFalse(self.layout.worktree.exists())
        self.assert_valid(record)


class PlanJobTests(RowTests, UsageTests, RecoveryTests, ReviewJobCase):
    STAGE = "plan"
    ROLE = routing.REVIEW_PLAN


class ImplementationJobTests(RowTests, UsageTests, ReviewJobCase):
    STAGE = "implementation"
    ROLE = routing.REVIEW_IMPLEMENTATION


class RecordKindTests(unittest.TestCase):
    def test_the_block_not_the_adapters_name_identifies_a_review_record(self) -> None:
        self.assertTrue(job._is_codex_review_record({"codex_review": {"stage": "preparing"}}))
        self.assertFalse(job._is_codex_review_record({"codex_worker": {}}))
        self.assertFalse(job._is_codex_review_record({"kind": job.REVIEW_KIND, "schema_version": job.REVIEW_SCHEMA_VERSION,
                                                      "codex_review": {}}))
        self.assertFalse(job._is_codex_worker_record({"codex_review": {"stage": "recorded"}}))

    def test_the_review_branch_is_keyed_on_the_routes_role_and_harness(self) -> None:
        # D1: the role and the harness choose the review branch, never the launcher's type alone.
        def route(role: str, harness_name: str) -> routing.ResolvedRoute:
            return routing.ResolvedRoute(role=role, model=None, effort=None, single_agent=False,
                                         model_source="default", effort_source="default", harness=harness_name)
        for role in routing.LOCAL_REVIEW_ROLES:
            self.assertTrue(job._is_codex_review_route(route(role, "codex")))
            self.assertFalse(job._is_codex_review_route(route(role, "claude-code")))
            self.assertFalse(job._is_codex_review_route(route(role, "claude")))
        for role in routing.ADAPTER_ROLE:
            if role not in routing.LOCAL_REVIEW_ROLES:
                self.assertFalse(job._is_codex_review_route(route(role, "codex")), role)


if __name__ == "__main__":
    unittest.main()
