"""The stale check, the job record and the loop guard of a protocol target
(workflow-controller-orchestration-protocol-v1 CP4, plan Design D): the
``protocol`` block of the job record, the currency check before the
``PLANNED`` write and again immediately before the spawn, the
``no_progress_repeated`` guard and the per-step identity re-check.

These tests replace ``worker.launch`` with a double that records the call and
stops: nothing here reads an outcome (that is ``tests/test_protocol_outcome.py``).
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import gitrepo, job, managed_repo, protocol, protocol_decision, review_rounds, target_state, worker  # noqa: E402
from controller.errors import WorkflowProtocolRefusedError, WorkflowReleaseChangedError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402


class _Stopped(Exception):
    """Raised by the ``worker.launch`` double: the spawn was reached."""


def _stale() -> WorkflowProtocolRefusedError:
    refusal = {"code": "stale_decision", "message": "stale", "retryable": True, "native": None}
    return WorkflowProtocolRefusedError("stale", evidence={"reason": "protocol_refused", "refusal": refusal})


class _Target:
    """A committed 2.7.0 target (a ``demo`` work item at ``PLANNING``, or none)
    and a runtime root, plus a harness that runs one step."""

    def __init__(self, test: unittest.TestCase, *, with_item: bool = True, release: str = "2.7.0") -> None:
        self.test = test
        self.td = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__("shutil").rmtree(self.td, ignore_errors=True))
        self.root = self.td / "repo"
        if with_item:
            fixtures.seed_workflow_item(self.root, "2.7.0", "publish", work_item_id="demo")
        else:
            fixtures.git_init(self.root)
            fixtures.install_workflow_release(self.root, "2.7.0")
            docs = self.root / "docs" / "ai-workflow"
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "WORKFLOW_CONFIG.json").write_text(json.dumps(
                {"schema_version": 1, "default_workflow_version": "2.2", "supported_versions": ["1", "2.1", "2.2"]}))
            (docs / "WORKFLOW_STATE.json").write_text(json.dumps(
                {"schema_version": 1, "active_work_item_id": None, "work_items": {}}))
            fixtures.commit_all(self.root, "seed")
        if release != "2.7.0":
            self.retag(release)
        self.stub = fixtures.write_stub_workflow_manager(self.td / "workflow-manager", release=release)
        self.runtime = self.td / "runtime"
        self.runtime.mkdir()

    def retag(self, release: str) -> None:
        """Move the installed Workflow to a stand-in ``release`` (its record and
        its protocol script), committed, as the Manager lane's update would."""
        record = self.root / ".workflow-manager" / "installation.json"
        text = record.read_text()
        record.write_text(text.replace('"workflow_version": "2.7.0"', f'"workflow_version": "{release}"')
                          if '"workflow_version": "2.7.0"' in text else
                          text.replace(f'"workflow_version": "{self.release()}"', f'"workflow_version": "{release}"'))
        script = self.root / "scripts" / "workflow_protocol.py"
        script.write_text(script.read_text().replace(f'WORKFLOW_RELEASE = "{self.release(script=True)}"',
                                                     f'WORKFLOW_RELEASE = "{release}"'))
        fixtures.commit_all(self.root, f"release {release}")
        self.stub = fixtures.write_stub_workflow_manager(self.td / "workflow-manager", release=release)

    def release(self, *, script: bool = False) -> str:
        if script:
            text = (self.root / "scripts" / "workflow_protocol.py").read_text()
            return text.split('WORKFLOW_RELEASE = "', 1)[1].split('"', 1)[0]
        return json.loads((self.root / ".workflow-manager" / "installation.json").read_text())["workflow_version"]

    def inspect(self):
        return managed_repo.inspect(self.root, manager_bin=str(self.stub))

    def records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.runtime / "jobs").glob("*.json"))]

    def step(self, target=None, *, launch=None, next_action=None):
        """One ``execute_step`` with the protocol launch on. ``launch`` is the
        ``worker.launch`` double (default: record the call and stop);
        ``next_action`` wraps ``protocol.next_action`` (called with the real
        function as its first argument). Returns ``(result, launches)``."""
        target = target or self.inspect()
        launches: list = []

        def launch_double(*args, **kwargs):
            launches.append((args, kwargs))
            raise _Stopped

        real = protocol.next_action
        calls: list = []

        def wrapped(*args, **kwargs):
            calls.append((args, kwargs))
            if next_action is None:
                return real(*args, **kwargs)
            return next_action(real, len(calls), *args, **kwargs)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(worker, "launch", launch or launch_double))
            stack.enter_context(mock.patch.object(protocol, "next_action", wrapped))
            try:
                result = job.execute_step(target, identity=IDENTITY, runtime=self.runtime)
            except _Stopped:
                result = None
        self.calls = calls
        return result, launches


class RecordBlockTest(unittest.TestCase):
    def test_a_protocol_job_records_its_decision_identity_release_and_digests(self) -> None:
        target = _Target(self)
        result, launches = target.step()
        self.assertIsNone(result)
        self.assertEqual(len(launches), 1)
        (record,) = target.records()
        block = record["protocol"]
        decision = protocol.next_action(target.root, "demo")
        self.assertEqual(block["decision"], decision.raw)
        self.assertEqual(block["state_identity"], decision.basis.state_identity)
        self.assertEqual(block["workflow_release"], "2.7.0")
        self.assertEqual(block["protocol_version"], "1.0")
        self.assertEqual(block["script_sha256"], dict(target.inspect().script_digests))
        self.assertEqual(block["action_id"], decision.action.id)
        self.assertIsNone(block["reconcile"])
        self.assertIsInstance(block["decided_ns"], int)
        self.assertEqual(len(block["envelope_digest"]), 64)
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)


class CurrencyCheckTest(unittest.TestCase):
    def test_a_plan_start_launch_passes_both_currency_checks(self) -> None:
        target = _Target(self, with_item=False)
        result, launches = target.step()
        self.assertEqual(len(launches), 1)
        # decide, the check before PLANNED, the check before the spawn.
        self.assertEqual([args[1] if len(args) > 1 else None for args, _ in target.calls], [None, None, None])
        (record,) = target.records()
        self.assertEqual(record["protocol"]["action_id"], "plan.start")
        self.assertIsNone(record["work_item_id"])

    def test_an_item_decision_is_checked_with_its_state_identity_twice(self) -> None:
        target = _Target(self)
        target.step()
        identities = [kwargs.get("expect_state_identity") for _args, kwargs in target.calls]
        self.assertEqual(len(identities), 3)
        self.assertIsNone(identities[0])
        self.assertEqual(identities[1], identities[2])
        self.assertIsNotNone(identities[1])

    def test_a_work_item_created_between_decision_and_launch_re_decides(self) -> None:
        target = _Target(self, with_item=False)

        def next_action(real, n, *args, **kwargs):
            answer = real(*args, **kwargs)
            if n == 2:  # the check before PLANNED sees a work item that was created meanwhile
                raw = json.loads(json.dumps(answer.raw))
                raw["snapshot"]["work_item_ids"] = ["created-meanwhile"]
                return protocol.parse_decision(raw)
            return answer

        result, launches = target.step(next_action=next_action)
        self.assertEqual(len(launches), 1)
        self.assertEqual(len(target.calls), 5)  # decide, stale check, re-decide, check, pre-spawn check
        self.assertEqual(len(target.records()), 1)

    def test_a_stale_identity_before_the_planned_write_re_decides_and_writes_no_record(self) -> None:
        target = _Target(self)

        def next_action(real, n, *args, **kwargs):
            if n == 2:
                raise _stale()
            return real(*args, **kwargs)

        result, launches = target.step(next_action=next_action)
        self.assertEqual(len(launches), 1)
        self.assertEqual(len(target.records()), 1)

    def test_three_stale_answers_are_the_decision_unstable_gate_naming_the_last_two_identities(self) -> None:
        target = _Target(self)
        identities = iter(["a" * 64, "b" * 64, "c" * 64])

        def next_action(real, n, *args, **kwargs):
            if kwargs.get("expect_state_identity") is not None:
                raise _stale()
            answer = real(*args, **kwargs)
            raw = json.loads(json.dumps(answer.raw))
            raw["basis"]["state_identity"] = next(identities)
            return protocol.parse_decision(raw)

        result, launches = target.step(next_action=next_action)
        self.assertEqual(launches, [])
        self.assertEqual(result["status"], job.STATUS_GATE_BLOCKED)
        text = result["human_gate_pending"]["what_is_required"]
        self.assertIn(protocol_decision.DECISION_UNSTABLE, text)
        self.assertIn("b" * 64, text)
        self.assertIn("c" * 64, text)
        self.assertNotIn("a" * 64, text)
        self.assertEqual(len(target.calls), 6)
        self.assertEqual([r["status"] for r in target.records()], [job.STATUS_GATE_BLOCKED])

    def test_three_stale_plan_start_answers_name_the_last_two_work_item_id_lists(self) -> None:
        target = _Target(self, with_item=False)
        listings = iter([["w1"], ["w1", "w2"], ["w1", "w2", "w3"]])

        def next_action(real, n, *args, **kwargs):
            answer = real(*args, **kwargs)
            raw = json.loads(json.dumps(answer.raw))
            raw["snapshot"]["work_item_ids"] = next(listings) if n % 2 == 1 else ["x"]
            return protocol.parse_decision(raw)

        result, launches = target.step(next_action=next_action)
        self.assertEqual(launches, [])
        text = result["human_gate_pending"]["what_is_required"]
        self.assertIn("work item ids", text)
        self.assertIn(protocol_decision.DECISION_UNSTABLE, text)

    def test_a_state_change_between_decision_and_launch_is_a_terminal_failed_record_with_no_worker(self) -> None:
        target = _Target(self)

        def next_action(real, n, *args, **kwargs):
            if n == 3:  # the pre-spawn check
                raise _stale()
            return real(*args, **kwargs)

        result, launches = target.step(next_action=next_action)
        self.assertEqual(launches, [])
        (record,) = target.records()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], protocol_decision.DECISION_STALE_AT_LAUNCH)
        self.assertEqual(record["reconciliation_evidence"]["gate"], protocol_decision.DECISION_UNSTABLE)
        self.assertNotIn("worker_process", record)
        self.assertEqual(job.resume(target.inspect(), identity=IDENTITY, runtime=target.runtime)[0]["status"],
                         job.STATUS_FAILED)

    def test_the_same_identity_with_a_different_action_at_the_pre_spawn_check_is_a_terminal_failed_record(self) -> None:
        target = _Target(self)

        def next_action(real, n, *args, **kwargs):
            answer = real(*args, **kwargs)
            if n == 3:
                raw = json.loads(json.dumps(answer.raw))
                raw["action"]["id"] = "plan.apply_review"
                return protocol.parse_decision(raw)
            return answer

        result, launches = target.step(next_action=next_action)
        self.assertEqual(launches, [])
        (record,) = target.records()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["code"], protocol_decision.DECISION_STALE_AT_LAUNCH)
        self.assertEqual(evidence["evidence"]["action_id"], "plan.apply_review")
        self.assertEqual(evidence["evidence"]["reason"], "answer_changed")

    def test_a_stale_abort_after_the_planned_write_is_resumed_without_error(self) -> None:
        target = _Target(self)
        with mock.patch.object(job, "_launch_job", side_effect=RuntimeError("aborted")):
            with self.assertRaises(RuntimeError):
                job.execute_step(target.inspect(), identity=IDENTITY, runtime=target.runtime)
        (planned,) = target.records()
        self.assertEqual(planned["status"], job.STATUS_PLANNED)
        self.assertIn("protocol", planned)
        resumed = job.resume(target.inspect(), identity=IDENTITY, runtime=target.runtime)
        self.assertEqual(resumed[0]["status"], job.STATUS_INTERRUPTED)


class ExecutedBytesBindingTest(unittest.TestCase):
    def test_a_script_replaced_after_the_identity_check_is_not_decided_under(self) -> None:
        target = _Target(self)
        admitted = target.inspect()
        script = target.root / "scripts" / "workflow_state.py"
        script.write_text(script.read_text() + "\n# replaced after the identity check\n")
        item = type("W", (), {"work_item_id": "demo"})()
        for call in (lambda: protocol_decision.decide(admitted, item),
                     lambda: protocol_decision.health_gate(admitted, item)):
            with self.subTest(call=call), self.assertRaises(WorkflowReleaseChangedError) as raised:
                call()
            self.assertEqual(raised.exception.evidence["changed_scripts"], ["scripts/workflow_state.py"])

    def test_a_script_replaced_between_the_identity_check_and_reconcile_fails_the_job(self) -> None:
        target = _Target(self)
        target.step()
        (record,) = target.records()
        repo = target.inspect()
        script = target.root / "scripts" / "workflow_state.py"
        script.write_text(script.read_text() + "\n# replaced after the identity check\n")
        with mock.patch.object(job, "_protocol_release_change", return_value=None), \
                mock.patch.object(protocol, "_envelope", side_effect=AssertionError("a script ran")):
            fields = job._protocol_resolution(record, managed_repo=repo, runtime_root=target.runtime,
                                              worker_outcome="SUCCESS")
        self.assertEqual(fields["status"], job.STATUS_FAILED)
        self.assertFalse(fields["transition_verified"])
        self.assertEqual(fields["reconciliation_evidence"]["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
        self.assertEqual(fields["reconciliation_evidence"]["workflow_error"]["evidence"]["changed_scripts"],
                         ["scripts/workflow_state.py"])


class IdentityRecheckTest(unittest.TestCase):
    def _refused(self, target: _Target, admitted) -> WorkflowReleaseChangedError:
        with self.assertRaises(WorkflowReleaseChangedError) as caught:
            target.step(admitted)
        self.assertEqual(target.records(), [])
        return caught.exception

    def test_a_managed_script_edited_after_inspect_is_refused(self) -> None:
        for name in ("workflow_protocol.py", "workflow_state.py"):
            with self.subTest(script=name):
                target = _Target(self)
                admitted = target.inspect()
                script = target.root / "scripts" / name
                script.write_text(script.read_text() + "\n# edited\n")
                exc = self._refused(target, admitted)
                self.assertEqual(exc.code, "WORKFLOW_RELEASE_CHANGED")
                self.assertIn(f"scripts/{name}", exc.evidence["changed_scripts"])

    def test_a_changed_describe_release_is_refused(self) -> None:
        target = _Target(self)
        admitted = target.inspect()
        script = target.root / "scripts" / "workflow_protocol.py"
        script.write_text(script.read_text().replace('WORKFLOW_RELEASE = "2.7.0"', 'WORKFLOW_RELEASE = "2.7.1"'))
        exc = self._refused(target, admitted)
        self.assertEqual(exc.evidence["installed_protocol_release"], "2.7.1")

    def test_a_non_workflow_script_edit_is_not_refused(self) -> None:
        target = _Target(self)
        admitted = target.inspect()
        (target.root / "scripts" / "extra.py").write_text("print('mine')\n")
        result, launches = target.step(admitted)
        self.assertEqual(len(launches), 1)

    def test_a_script_that_no_longer_answers_is_refused_as_a_changed_release(self) -> None:
        target = _Target(self)
        admitted = target.inspect()
        (target.root / "scripts" / "workflow_protocol.py").write_text("raise SystemExit(3)\n")
        exc = self._refused(target, admitted)
        self.assertIn("protocol_error", exc.evidence)

    def test_a_release_change_between_invocations_admits_a_fresh_run_and_fails_the_old_jobs_resume(self) -> None:
        target = _Target(self)
        first, launches = target.step()
        self.assertEqual(len(launches), 1)
        (old,) = target.records()
        self.assertEqual(old["target_workflow_version"], "2.7.0")
        target.retag("2.7.1")
        # A fresh run: inspect derives the new identity; the job is launched and recorded under it.
        with mock.patch.object(job, "pending_reconciliation_jobs", return_value=[]):
            target.step()
        new = [r for r in target.records() if r["job_id"] != old["job_id"]]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["protocol"]["workflow_release"], "2.7.1")
        self.assertEqual(new[0]["target_workflow_version"], "2.7.1")
        # A resume of the old job, recorded under 2.7.0, is FAILED workflow_release_changed.
        resumed = {r["job_id"]: r for r in job.resume(target.inspect(), identity=IDENTITY, runtime=target.runtime)}
        failed = resumed[old["job_id"]]
        self.assertEqual(failed["status"], job.STATUS_FAILED)
        self.assertEqual(failed["reconciliation_evidence"]["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)


def _write_job(runtime: Path, root: Path, n: int, *, action: str = "implementation.checkpoint", status: str,
               reconcile: str | None, work_item: str | None = "demo", identity: str = "i",
               end_identity: str | None = None, second: int | None = None, suffix: str | None = None,
               decided_ns: int | None = None) -> str:
    jobs = runtime / "jobs"
    jobs.mkdir(exist_ok=True)
    job_id = f"20260101T0000{n if second is None else second:02d}-{suffix or f'job{n:02d}'}"
    record = {
        "job_id": job_id, "target_repo": str(root), "work_item_id": work_item, "status": status,
        "created_at": f"2026-01-01T00:00:{n if second is None else second:02d}Z",
        "protocol": {"action_id": action, "state_identity": identity,
                     "reconcile": None if reconcile is None else {
                         "class": reconcile, "to": None if end_identity is None else {"state_identity": end_identity}}},
    }
    if decided_ns is not None:
        record["protocol"]["decided_ns"] = decided_ns
    (jobs / f"{job_id}.json").write_text(json.dumps(record))
    return job_id


class ReviewCapGateTest(unittest.TestCase):
    """``job._review_cap_gate`` (review-convergence CP2, D2-D4): the gate over
    the capped actions, from the Workflow's round or the stored records."""

    def _gate(self, target: _Target, action: str, snapshot: dict, cap=4, work_item: str | None = "demo"):
        repo = target.inspect()
        item = target_state.NoWorkItemYet if work_item is None else type("W", (), {"work_item_id": work_item})()
        info = type("P", (), {"action_id": action, "document": {"snapshot": snapshot}})()
        decision = type("D", (), {"protocol": info})()
        with mock.patch.object(protocol_decision, "gate_for", side_effect=lambda *a, **k: a[3:]):
            return job._review_cap_gate(target.runtime, repo, item, decision,
                                        cap if isinstance(cap, review_rounds.RoundCaps) else review_rounds.RoundCaps(6, cap))

    def test_the_boundary_for_each_stage_and_action(self) -> None:
        target = _Target(self)
        for stage in ("plan", "implementation"):
            with self.subTest(stage=stage):
                at = lambda n: {"review_round": {stage: n}}  # noqa: E731
                self.assertIsNone(self._gate(target, f"{stage}.apply_review", at(3)))
                code, text = self._gate(target, f"{stage}.apply_review", at(4))
                self.assertEqual(code, protocol_decision.REVIEW_ROUND_CAP_REACHED)
                self.assertIsNone(self._gate(target, f"{stage}.review.local", at(4)))
                self.assertIsNotNone(self._gate(target, f"{stage}.review.local", at(5)))
                self.assertIn(f"{stage} review round", text)

    def test_the_text_names_the_three_options_in_order_and_that_the_cap_is_per_command(self) -> None:
        target = _Target(self)
        _code, text = self._gate(target, "plan.apply_review", {"review_round": {"plan": 4}})
        self.assertIn("round 4 (given by the Workflow; the Workflow gave no split counts, so this single number is checked against the external cap)", text)
        self.assertIn("reached the external round cap of 4 (review.external_round_cap)", text)
        self.assertNotIn("counted from the Controller's job records", text)
        self.assertIn("cap of 4", text)
        positions = [text.index(part) for part in ("(1) raise the cap", "(2) accept at the cap with /approve-review",
                                                   "(3) withdraw and re-scope with /milestone-plan demo")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("one command", text)
        self.assertIn("never accepts at the cap itself", text)

    def test_other_actions_a_missing_item_and_a_raised_cap_are_not_stopped(self) -> None:
        target = _Target(self)
        snapshot = {"review_round": {"plan": 9, "implementation": 9}}
        for action in ("plan.review.external", "implementation.checkpoint", "implementation.satisfy"):
            self.assertIsNone(self._gate(target, action, snapshot))
        self.assertIsNone(self._gate(target, "plan.apply_review", snapshot, work_item=None))
        self.assertIsNone(self._gate(target, "plan.apply_review", {"review_round": {"plan": 4}}, cap=6))

    def _split(self, action: str, local, external, caps=review_rounds.RoundCaps(6, 4), stage: str = "plan"):
        snapshot = {"review_round": {f"{stage}_local": local, f"{stage}_external": external}}
        return self._gate(_Target(self), action, snapshot, caps)

    def test_split_counts_stop_an_apply_at_either_cap_and_name_the_one_that_fired(self) -> None:
        local = self._split("plan.apply_review", 6, 1)
        self.assertEqual(local[0], protocol_decision.REVIEW_ROUND_CAP_REACHED)
        self.assertIn("local round 6 and external round 1 (given by the Workflow as split counts)", local[1])
        self.assertIn("the local count 6 has reached the local round cap of 6 (review.local_round_cap)", local[1])
        self.assertIn("--review-local-round-cap N", local[1])
        external = self._split("plan.apply_review", 2, 4)
        self.assertIn("the external count 4 has reached the external round cap of 4 (review.external_round_cap)",
                      external[1])
        self.assertIn("--review-external-round-cap N", external[1])
        self.assertIsNone(self._split("plan.apply_review", 5, 3))

    def test_split_counts_stop_a_local_review_at_the_local_cap_only(self) -> None:
        for stage in ("plan", "implementation"):
            self.assertIsNotNone(self._split(f"{stage}.review.local", 6, 0, stage=stage))
            self.assertIsNone(self._split(f"{stage}.review.local", 5, 9, stage=stage))

    def test_one_null_count_is_the_fallback_with_the_single_number_named(self) -> None:
        target = _Target(self)
        snapshot = {"review_round": {"plan": 4, "plan_local": 1, "plan_external": None}}
        _code, text = self._gate(target, "plan.apply_review", snapshot)
        self.assertIn("the Workflow gave no split counts, so this single number is checked against the external cap",
                      text)
        self.assertIn("round 4", text)

    def test_the_external_review_cap_gate_needs_split_counts_and_an_external_review(self) -> None:
        target = _Target(self)
        repo, item = target.inspect(), type("W", (), {"work_item_id": "demo"})()

        def gate(action, snapshot, work_item=item):
            info = type("P", (), {"action_id": action, "document": {"snapshot": snapshot}})()
            with mock.patch.object(protocol_decision, "gate_for", side_effect=lambda *a, **k: a[3:]):
                return job.external_review_cap_gate(repo, work_item, type("D", (), {"protocol": info})(),
                                                    review_rounds.RoundCaps(6, 4))

        counts = lambda n: {"review_round": {"plan_local": 1, "plan_external": n}}  # noqa: E731
        code, text = gate("plan.review.external", counts(4))
        self.assertEqual(code, protocol_decision.REVIEW_ROUND_CAP_REACHED)
        self.assertIn("external round cap of 4", text)
        self.assertIsNone(gate("plan.review.external", counts(3)))
        self.assertIsNone(gate("plan.review.external", {"review_round": {"plan": 9}}))
        self.assertIsNone(gate("plan.apply_review", counts(9)))
        self.assertIsNone(gate("plan.review.external", counts(9), work_item=target_state.NoWorkItemYet))

    def test_a_legacy_decision_is_not_capped(self) -> None:
        target = _Target(self)
        legacy = type("D", (), {"protocol": None})()
        self.assertIsNone(job._review_cap_gate(target.runtime, target.inspect(),
                                               type("W", (), {"work_item_id": "demo"})(), legacy, review_rounds.RoundCaps(1, 1)))

    def test_an_absent_round_counts_from_the_records_and_says_so(self) -> None:
        target = _Target(self)
        for n, revision in enumerate((1, 2, 3), 1):
            path = target.runtime / "jobs"
            path.mkdir(exist_ok=True)
            job_id = f"20260101T00000{n}-impl{n}"
            (path / f"{job_id}.json").write_text(json.dumps({
                "job_id": job_id, "target_repo": str(target.root), "work_item_id": "demo",
                "status": job.STATUS_FINISHED, "created_at": f"2026-01-01T00:00:0{n}Z",
                "pre_state": {"phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "implementation_revision": revision}}))
        snapshot = {"phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "implementation_revision": 4}
        _code, text = self._gate(target, "implementation.apply_review", snapshot)
        self.assertIn("round 4 (counted from the Controller's job records", text)
        _code, text = self._gate(target, "implementation.apply_review", snapshot, cap=5) or (None, "")
        self.assertEqual(text, "")  # round 4 is below cap 5: launches

    def test_a_plan_round_from_the_records_says_the_workflows_count_may_be_higher(self) -> None:
        target = _Target(self)
        snapshot = {"phase": "AWAITING_LOCAL_PLAN_REVIEW", "plan_revision": 1}
        _code, text = self._gate(target, "plan.apply_review", snapshot, cap=1)
        self.assertIn("the Workflow's own count may be higher", text)

    def test_a_gate_blocked_record_is_not_counted_and_nothing_is_written(self) -> None:
        target = _Target(self)
        jobs = target.runtime / "jobs"
        jobs.mkdir(exist_ok=True)
        (jobs / "20260101T000001-gate.json").write_text(json.dumps({
            "job_id": "20260101T000001-gate", "target_repo": str(target.root), "work_item_id": "demo",
            "status": job.STATUS_GATE_BLOCKED, "created_at": "2026-01-01T00:00:01Z",
            "pre_state": {"phase": "AWAITING_LOCAL_PLAN_REVIEW", "plan_revision": 7}}))
        state = target.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        before = (state.read_bytes(), sorted(p.name for p in jobs.iterdir()))
        self.assertIsNone(self._gate(target, "plan.apply_review", {"phase": "AWAITING_LOCAL_PLAN_REVIEW",
                                                                   "plan_revision": 1}))
        self.assertEqual(before, (state.read_bytes(), sorted(p.name for p in jobs.iterdir())))

    def test_the_loop_guard_takes_precedence_and_the_cap_is_threaded(self) -> None:
        target = _Target(self)
        info = type("P", (), {"action_id": "plan.apply_review", "state_identity": "s", "document": {}})()
        decision = type("D", (), {"protocol": info, "automatic": True})()
        item = type("W", (), {"work_item_id": "demo"})()
        loop, cap = object(), object()
        for guard_result, expected in ((loop, loop), (None, cap)):
            with mock.patch.object(protocol_decision, "decide_after_preflight", return_value=(decision, ())), \
                    mock.patch.object(protocol_decision, "check_currency",
                                      return_value=type("C", (), {"current": True})()), \
                    mock.patch.object(job, "_no_progress_gate", return_value=guard_result), \
                    mock.patch.object(job, "_review_cap_gate", return_value=cap) as cap_gate:
                got, _item, _pre = job._decide_protocol_current(
                    target.inspect(), runtime=target.runtime, snapshot=None, work_item=item, work_item_id="demo",
                    pre_state={}, base=None, review_round_caps=review_rounds.RoundCaps(6, 6))
            self.assertIs(got, expected)
            if guard_result is None:
                self.assertEqual(cap_gate.call_args.args[4], review_rounds.RoundCaps(6, 6))
            else:
                cap_gate.assert_not_called()


class LoopGuardTest(unittest.TestCase):
    def _gate(self, target: _Target, action: str = "implementation.checkpoint", work_item: str | None = "demo",
              state_identity: str | None = None):
        repo = target.inspect()
        item = target_state.NoWorkItemYet if work_item is None else type("W", (), {"work_item_id": work_item})()
        decision = type("D", (), {"protocol": type("P", (), {"action_id": action, "state_identity": state_identity})()})()
        with mock.patch.object(protocol_decision, "gate_for", side_effect=lambda *a, **k: a[3:]):
            return job._no_progress_gate(target.runtime, repo, item, decision)

    def _jobs(self, target: _Target, *shapes) -> list[str]:
        return [_write_job(target.runtime, target.root, n, status=status, reconcile=cls, **kw)
                for n, (status, cls, kw) in enumerate(shapes, 1)]

    def _jobs_more(self, target: _Target, start: int, *shapes) -> None:
        for n, (status, cls, kw) in enumerate(shapes, start):
            _write_job(target.runtime, target.root, n, status=status, reconcile=cls, **kw)

    def test_two_no_progress_jobs_trip_the_guard_naming_both(self) -> None:
        target = _Target(self)
        ids = self._jobs(target, (job.STATUS_FINISHED, "no_progress", {}), (job.STATUS_FINISHED, "no_progress", {}))
        code, text = self._gate(target)
        self.assertEqual(code, protocol_decision.NO_PROGRESS_REPEATED)
        for job_id in ids:
            self.assertIn(job_id, text)

    def test_the_guard_carries_the_preflight_advisories(self) -> None:
        target = _Target(self)
        self._jobs(target, (job.STATUS_FINISHED, "no_progress", {}), (job.STATUS_FINISHED, "no_progress", {}))
        repo = target.inspect()
        item = type("W", (), {"work_item_id": "demo"})()
        decision = type("D", (), {"protocol": type("P", (), {"action_id": "implementation.checkpoint",
                                                             "state_identity": None})()})()
        advisories = ("verify gate_policy: warn: policy not adopted",)
        with mock.patch.object(protocol_decision, "gate_for", side_effect=lambda *a, **k: k) as gate_for:
            got = job._no_progress_gate(target.runtime, repo, item, decision, advisories=advisories)
        self.assertEqual(gate_for.call_count, 1)
        self.assertEqual(got, {"advisories": advisories})

    def test_a_progressing_pair_does_not_trip_it(self) -> None:
        target = _Target(self)
        self._jobs(target, (job.STATUS_FINISHED, "progress", {}), (job.STATUS_FINISHED, "progress", {}))
        self.assertIsNone(self._gate(target))

    def test_progress_or_a_gate_between_resets_the_count(self) -> None:
        for reset in ("progress", "gate_reached"):
            with self.subTest(reset=reset):
                target = _Target(self)
                self._jobs(target, (job.STATUS_FINISHED, "no_progress", {}), (job.STATUS_FINISHED, reset, {}),
                           (job.STATUS_FINISHED, "no_progress", {}))
                self.assertIsNone(self._gate(target))

    def test_a_failed_job_between_two_no_progress_jobs_is_skipped(self) -> None:
        target = _Target(self)
        self._jobs(target, (job.STATUS_FINISHED, "no_progress", {}), (job.STATUS_FAILED, None, {}),
                   (job.STATUS_FINISHED, "no_progress", {}))
        self.assertIsNotNone(self._gate(target))

    def test_an_interrupted_no_progress_job_between_is_skipped(self) -> None:
        target = _Target(self)
        self._jobs(target, (job.STATUS_FINISHED, "no_progress", {}), (job.STATUS_INTERRUPTED, "no_progress", {}),
                   (job.STATUS_FINISHED, "no_progress", {}))
        self.assertIsNotNone(self._gate(target))

    def test_the_count_is_per_pair_and_the_state_identity_is_not_part_of_the_key(self) -> None:
        target = _Target(self)
        self._jobs(target, (job.STATUS_FINISHED, "no_progress", {"identity": "x"}),
                   (job.STATUS_FINISHED, "progress", {"action": "implementation.self_review"}),
                   (job.STATUS_FINISHED, "no_progress", {"identity": "y"}))
        self.assertIsNotNone(self._gate(target))
        self.assertIsNone(self._gate(target, action="implementation.self_review"))
        self.assertIsNone(self._gate(target, work_item="other"))

    def test_same_second_jobs_are_ordered_by_their_decision_time_not_their_random_suffix(self) -> None:
        target = _Target(self)
        kw = {"second": 5, "end_identity": None}
        _write_job(target.runtime, target.root, 1, status=job.STATUS_FINISHED, reconcile="no_progress",
                   identity="s1", suffix="ffffffff", decided_ns=1, **{**kw, "end_identity": "s2"})
        _write_job(target.runtime, target.root, 2, status=job.STATUS_FINISHED, reconcile="no_progress",
                   identity="s2", suffix="00000000", decided_ns=2, **{**kw, "end_identity": "s3"})
        self.assertIsNotNone(self._gate(target, state_identity="s3"))

    def test_a_streak_ends_once_the_work_item_moved_since_the_last_counted_job(self) -> None:
        target = _Target(self)
        self._jobs(target, (job.STATUS_FINISHED, "no_progress", {"identity": "s1", "end_identity": "s1"}),
                   (job.STATUS_FINISHED, "no_progress", {"identity": "s1", "end_identity": "s1"}))
        self.assertIsNotNone(self._gate(target, state_identity="s1"))
        # A checkpoint completed by hand: the next implementation.checkpoint is at another identity.
        self.assertIsNone(self._gate(target, state_identity="s2"))

    def test_a_no_progress_job_after_an_out_of_band_move_starts_a_new_count(self) -> None:
        target = _Target(self)
        self._jobs(target, (job.STATUS_FINISHED, "no_progress", {"identity": "s1", "end_identity": "s1"}),
                   (job.STATUS_FINISHED, "no_progress", {"identity": "s1", "end_identity": "s1"}),
                   (job.STATUS_FINISHED, "no_progress", {"identity": "s2", "end_identity": "s2"}))
        self.assertIsNone(self._gate(target, state_identity="s2"))
        self._jobs_more(target, 4, (job.STATUS_FINISHED, "no_progress", {"identity": "s2", "end_identity": "s2"}))
        self.assertIsNotNone(self._gate(target, state_identity="s2"))

    def test_plan_start_is_keyed_on_no_work_item(self) -> None:
        target = _Target(self, with_item=False)
        self._jobs(target, (job.STATUS_FINISHED, "no_progress", {"action": "plan.start", "work_item": None}),
                   (job.STATUS_FINISHED, "no_progress", {"action": "plan.start", "work_item": None}))
        self.assertIsNotNone(self._gate(target, action="plan.start", work_item=None))

    def test_another_targets_jobs_are_not_counted(self) -> None:
        target = _Target(self)
        other = Path("/elsewhere")
        for n in (1, 2):
            _write_job(target.runtime, other, n, status=job.STATUS_FINISHED, reconcile="no_progress")
        self.assertIsNone(self._gate(target))

    def test_a_step_gates_on_a_repeated_pair_and_records_the_gate_without_a_worker(self) -> None:
        target = _Target(self)
        action = protocol.next_action(target.root, "demo").action.id
        for n in (1, 2):
            _write_job(target.runtime, target.root, n, action=action, status=job.STATUS_FINISHED,
                       reconcile="no_progress")
        result, launches = target.step()
        self.assertEqual(launches, [])
        self.assertEqual(result["status"], job.STATUS_GATE_BLOCKED)
        self.assertIn(protocol_decision.NO_PROGRESS_REPEATED, result["human_gate_pending"]["what_is_required"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# The functional tester step (workflow-controller-automated-functional-review CP4): row 38e answered by the
# vendored Workflow 2.12.0 itself, the sandbox and the model replaced by a fake tester that writes a chosen
# ``RESULTS.json`` (the sandbox is CP3's, the enforcement CP2's).
# ---------------------------------------------------------------------------

import io  # noqa: E402

from controller import functional_review, review, usage  # noqa: E402
from controller import runtime as controller_runtime  # noqa: E402
from tests import test_protocol_lifecycle as lifecycle  # noqa: E402


class TesterLifecycle(lifecycle.GateLifecycle):
    """A ``GateLifecycle`` stopped at row 38e, whose ``review.launch_functional`` is a fake tester."""

    FLOW = "1. Exercise the feature.\\n"

    def __init__(self, test: unittest.TestCase, flows: int = 1, checklist: str | None = None) -> None:
        super().__init__(test)
        self.functional = {"automated": True, "timeout_seconds": 600, "operator_home": str(self.td / "claude-home"),
                           "binary": "claude"}
        (self.td / "claude-home").mkdir()
        test.addCleanup(self.remove_short_folders)  # registered after the tree's own cleanup, so it runs first
        #: What the fake tester writes: a list of ``(flow_id, status, reason)``, or a callable ``(kwargs, outdir)``.
        self.plan: object = []
        self.outcome = "ran"
        self.extra: dict = {}
        self.launches: list[dict] = []
        self.task_texts: list[str] = []
        self.raises: Exception | None = None
        self.numbered = checklist if checklist is not None else "".join(
            f"{n}. Exercise flow {n}.\\n" for n in range(1, flows + 1))

    def remove_short_folders(self) -> None:
        """Remove the short ``TMPDIR`` of every sandbox the cycle kept (they live outside the runtime tree)."""
        for record in self.runtime.glob(f"{functional_review.SANDBOX_AREA}/*/{functional_review.TMP_RECORD}"):
            functional_review.remove_short_tmp(record.parent)

    def effect(self, task: str) -> None:
        self.task_texts.append(task)
        words = task.lstrip("/").split()
        if words[0] == "prepare-functional-review":
            self.tasks.append(words[0])
            self.workflow(lifecycle._FUNCTIONAL_CHECKLIST.replace(self.FLOW, self.numbered), lifecycle.WID)
            return
        if words[0] == "apply-functional-review":
            self.tasks.append(words[0])  # the applier's own effect is the lifecycle scenarios' (CP5)
            return
        super().effect(task)

    SNAPSHOT_ATTRS = (*lifecycle.GateLifecycle.SNAPSHOT_ATTRS, "task_texts")

    def snapshot_inputs(self) -> tuple:
        # The checklist the fake worker writes at ``functional.prepare`` is part of the repository the drive builds.
        return (*super().snapshot_inputs(), self.numbered)

    def rebase(self, td: Path) -> None:
        super().rebase(td)
        self.functional["operator_home"] = str(td / "claude-home")

    def to_38e(self) -> None:
        self.reach("to_38e", self._to_38e)

    def _to_38e(self) -> None:
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        self.run_until("IMPLEMENTING")
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation")
        self.run_until("AWAITING_FUNCTIONAL_REVIEW")
        self.test.assertEqual(self.step()["status"], job.STATUS_FINISHED)  # functional.prepare
        self.test.assertEqual(protocol.next_action(self.root, lifecycle.WID).raw["row"], "38e")

    def fake_tester(self, **kwargs) -> dict:
        self.launches.append(kwargs)
        if self.raises is not None:
            raise self.raises
        job_id = kwargs["job_id"]
        sandbox = kwargs["runtime_root"] / functional_review.SANDBOX_AREA / job_id
        outdir = sandbox / "out"
        outdir.mkdir(parents=True)
        if callable(self.plan):
            self.plan(kwargs, outdir)
        else:
            entries = []
            for flow_id, status, reason in self.plan:
                (outdir / f"{flow_id}.log").write_text(f"log of {flow_id}\n")
                entry = {"flow_id": flow_id, "status": status, "summary": f"{flow_id} {status}", "log": f"{flow_id}.log"}
                if reason is not None:
                    entry["reason"] = reason
                entries.append(entry)
            (outdir / functional_review.RESULTS_FILE).write_text(json.dumps(entries))
        result = {"outcome": self.outcome, "breach": [], "unbound": [], "fake_calls": {}, "output_dir": str(outdir),
                  "sandbox": str(sandbox), "keep": False, "credentials": None, "detail": None, "readings": [],
                  "first_readings": [], "head": kwargs["candidate"].head, "writable": ["__pycache__/"],
                  "refused": [{"pattern": "*.egg-info/", "reason": "a glob"}], **self.extra}
        kwargs["out"].update(result)
        return result

    def step(self, *, gate=None, functional="default") -> dict:
        self.rows.append(protocol.next_action(self.root, lifecycle.WID).raw["row"])

        def launch(task, *, cwd, **kwargs):
            self.effect(task)
            return lifecycle._result(self.report)

        with mock.patch.object(worker, "launch", launch), mock.patch.object(review, "launch_functional", self.fake_tester):
            return job.execute_step(self.inspect(), identity=IDENTITY, runtime=self.runtime, usage_gate=gate,
                                    functional=self.functional if functional == "default" else functional)

    def tester_records(self) -> list[dict]:
        return [r for r in self.job_records() if r.get("kind") == functional_review.JOB_KIND]

    def job_records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.runtime / "jobs").glob("*.json"))]

    def row(self) -> str:
        return protocol.next_action(self.root, lifecycle.WID).raw["row"]

    def evidence(self) -> dict:
        return fixtures.state_entry(self.root, lifecycle.WID).get("gate_evidence", {}).get("functional", {})

    def findings_path(self) -> Path:
        return self.root / protocol.resolve_artifact(self.root, lifecycle.WID, "functional_review").path

    def head(self) -> str:
        return gitrepo.head_state(self.root).commit

    def usage_gate(self):
        def gate(route, provider=usage.PROVIDER_CLAUDE):
            admission = usage.admit(self.runtime, provider=provider, role=route.role, model=route.model,
                                    repository=str(self.root), run_id="run-1", limits=usage.Limits(provider=provider),
                                    now=time.time())
            return admission.hold if admission.hold is not None else admission.token

        return gate

    def reservations(self) -> dict:
        return usage.load_record(self.runtime)["reservations"]


class _TesterCase(unittest.TestCase):
    def life(self, **kwargs) -> TesterLifecycle:
        life = TesterLifecycle(self, **kwargs)
        life.to_38e()
        return life


class OutcomesTest(_TesterCase):
    def test_every_flow_passing_records_the_evidence_and_the_workflow_moves_on(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        record = life.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual((record["kind"], record["functional"]["outcome"], record["functional"]["recorded"]),
                         ("functional_test", "recorded", ["flow-1"]))
        self.assertTrue(record["transition_verified"])
        stored = life.evidence()["flow-1"]
        self.assertEqual((stored["status"], stored["head"], stored["run_ref"]),
                         ("passed", life.head(), f"controller:{record['job_id']}"))
        self.assertEqual(stored["summary"]["checklist_blob"], record["functional"]["checklist_blob"])
        self.assertTrue(stored["reporter"].startswith("workflow-controller "))
        self.assertEqual(life.row(), "38h")
        self.assertFalse(life.findings_path().exists())
        # The tester ran in a sandbox the step settled, with the closed settings of the role.
        [call] = life.launches
        self.assertEqual((call["model"], call["effort"], call["timeout"]), ("claude-sonnet-5-5", "medium", 600.0))
        self.assertEqual(record["worker_route"]["role"], "functional-tester")
        self.assertEqual(record["worker_route"]["harness"], "claude-code")
        self.assertEqual(record["worker_route"]["sources"]["harness"], "default")
        events = [e.event_type for e in job.derive_job_event(record)]
        self.assertEqual(events, ["step_done"])

    def test_a_defect_is_written_as_findings_and_the_next_decision_is_the_applier(self) -> None:
        life = self.life(flows=2)
        life.plan = [("flow-1", "passed", None), ("flow-2", "failed", "defect")]
        record = life.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(record["functional"]["outcome"], "findings")
        text = life.findings_path().read_text()
        self.assertIn(f"Tested head: {life.head()}", text)
        self.assertIn(f"Checklist blob: {record['functional']['checklist_blob']}", text)
        self.assertIn(f"Run: controller:{record['job_id']}", text)
        self.assertIn("flow-2", text)
        self.assertNotIn("flow-1 (", text)
        self.assertEqual(set(life.evidence()), {"flow-1", "flow-2"})
        self.assertEqual(life.row(), "38")
        launched = len(life.tasks)
        self.assertEqual(life.step()["status"], job.STATUS_FINISHED)
        self.assertEqual(life.tasks[launched:], ["apply-functional-review"])

    def test_identical_failure_text_at_a_new_head_is_new_findings_the_workflow_does_not_skip(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "failed", "defect")]
        record = life.step()
        entries = [functional_review.Entry("flow-1", "failed", "flow-1 failed", "defect", Path("flow-1.log"),
                                           "0" * 64, log_content=b"log of flow-1\n")]
        fixtures.run(["git", "commit", "-q", "--allow-empty", "-m", "a fix"], cwd=life.root)
        flows = functional_review.candidate_flows(functional_review.capture_candidate(life.root, life.head()))
        later = functional_review.findings_text(entries, flows, head=life.head(), checklist_blob="b" * 40, job_id="job-9")
        life.workflow("fingerprint.mark_functional_review_consumed(Path.cwd(), sys.argv[1])", lifecycle.WID)
        check = "fingerprint.assert_functional_review_not_already_consumed(Path.cwd(), sys.argv[1])"
        self.assertNotEqual(life.findings_path().read_text(), later)
        with self.assertRaises(Exception):  # the same bytes are refused as already applied ...
            life.workflow(check, lifecycle.WID)
        life.findings_path().write_text(later)  # ... the same failure under the new head's header is not
        life.workflow(check, lifecycle.WID)

    def test_an_environment_limit_is_recorded_failed_without_findings_and_stops_for_a_person(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "failed", "environment")]
        record = life.step()
        self.assertEqual((record["status"], record["functional"]["outcome"]),
                         (job.STATUS_GATE_BLOCKED, "environment_limited"))
        self.assertEqual(record["functional"]["gate"], "flows_not_runnable")
        stored = life.evidence()["flow-1"]
        self.assertEqual((stored["status"], stored["summary"]["reason"]), ("failed", "environment"))
        self.assertFalse(life.findings_path().exists())
        gate = record["human_gate_pending"]["what_is_required"]
        self.assertIn("flow-1", gate)
        self.assertIn("*.egg-info/", gate)  # the writable-location patterns the sandbox refused
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["review_wait"])
        # Nothing is launched next, and the run does not count towards the bound.
        self.assertEqual(life.row(), "38i")
        self.assertEqual(review_rounds.functional_rounds(life.tester_records(), str(life.root), lifecycle.WID), 0)

    def test_a_flow_the_tester_left_out_is_an_environment_limit_with_a_valid_log_digest(self) -> None:
        life = self.life(flows=2)
        life.plan = [("flow-1", "passed", None)]
        record = life.step()
        self.assertEqual(record["functional"]["outcome"], "environment_limited")
        left = life.evidence()["flow-2"]
        self.assertEqual((left["status"], left["summary"]["reason"]), ("failed", "environment"))
        self.assertIn("left this flow out", left["summary"]["text"])
        self.assertEqual(len(left["log_digest"]), 64)

    def test_a_tester_cut_at_the_timeout_leaves_the_unrun_flows_as_environment(self) -> None:
        life = self.life(flows=2)
        life.plan = [("flow-1", "passed", None)]
        life.outcome = "timeout"
        record = life.step()
        self.assertEqual(record["functional"]["outcome"], "environment_limited")
        self.assertIn("ended at the timeout", life.evidence()["flow-2"]["summary"]["text"])
        self.assertEqual(life.row(), "38i")

    def test_a_mixed_run_writes_findings_for_the_defect_flows_only(self) -> None:
        life = self.life(flows=3)
        life.plan = [("flow-1", "failed", "defect"), ("flow-2", "failed", "environment"), ("flow-3", "passed", None)]
        record = life.step()
        self.assertEqual(record["functional"]["outcome"], "findings")
        text = life.findings_path().read_text()
        self.assertIn("## flow-1", text)
        self.assertNotIn("flow-2 (", text)
        self.assertEqual(set(life.evidence()), {"flow-1", "flow-2", "flow-3"})
        self.assertEqual(review_rounds.functional_rounds(life.tester_records(), str(life.root), lifecycle.WID), 1)

    def test_functional_automated_false_keeps_the_old_stop(self) -> None:
        life = self.life()
        for functional in (dict(life.functional, automated=False), None):
            with self.subTest(functional=functional):
                record = life.step(functional=functional)
                self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
                self.assertNotIn("kind", record)
        self.assertEqual(life.launches, [])


class ThePreLaunchStopsTest(_TesterCase):
    def test_a_checklist_with_no_recognised_flow_stops_before_any_job_sandbox_or_reservation(self) -> None:
        life = self.life(checklist="Check it works somehow.\\n")
        record = life.step(gate=life.usage_gate())
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(record["functional"]["gate"], "checklist_unrecognised")
        self.assertIn("no flow", record["human_gate_pending"]["what_is_required"])
        self.assertEqual((life.launches, life.tester_records(), life.reservations()), ([], [], {}))
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["review_wait"])

    def test_a_repeat_at_the_tested_head_is_unchanged_and_one_blocker_not_a_second_run(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        real = protocol.record_external_result

        def swallow(*args, **kwargs):  # the Workflow still answers 38e, as it would for a policy the checklist cannot meet
            return real(*args, **kwargs).__class__(
                "recorded", "functional", None, None, None, None, None, None, None, {}, flow_id="flow-1")

        with mock.patch.object(protocol, "record_external_result", swallow):
            first = life.step()
        self.assertEqual(first["functional"]["outcome"], "recorded")
        self.assertEqual(life.row(), "38h")  # (the swallowed call still reached the Workflow)
        # Put the Workflow back at row 38e by dropping the stored evidence, as an unmet requirement would.
        life.workflow("""
state = json.loads((Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
state["work_items"][sys.argv[1]]["gate_evidence"]["functional"] = {}
ws.state_transaction(Path.cwd(), lambda s: state)
""", lifecycle.WID)
        self.assertEqual(life.row(), "38e")
        record = life.step()
        self.assertEqual(len(life.launches), 1)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(record["functional"]["gate"], "functional_unchanged")
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["lane_blocked"])
        self.assertIn("Workflow", record["human_gate_pending"]["what_is_required"])

    def test_a_symlink_the_tester_plants_at_a_not_run_log_name_redirects_no_controller_write(self) -> None:
        life = self.life(flows=2)
        victim = life.runtime / "jobs" / "other-job.json"
        controller_runtime.write_json(life.runtime, "jobs/other-job.json", {"keep": "me"})
        before = victim.read_bytes()

        def plan(kwargs, outdir):
            (outdir / "flow-1.log").write_text("log of flow-1\n")
            (outdir / "flow-2.not-run.log").symlink_to(victim)
            (outdir / functional_review.RESULTS_FILE).write_text(json.dumps([
                {"flow_id": "flow-1", "status": "passed", "summary": "ok", "log": "flow-1.log"}]))

        life.plan = plan
        record = life.step()
        self.assertEqual(record["functional"]["outcome"], "environment_limited")  # flow-2 was left out
        self.assertEqual(victim.read_bytes(), before)
        self.assertTrue((life.runtime / "jobs" / record["job_id"] / "functional-not-run" / "flow-2.not-run.log").is_file())

    def test_explain_lists_the_gitignore_patterns_the_derivation_refused(self) -> None:
        life = self.life()
        ignore = life.root / ".gitignore"
        ignore.write_text(ignore.read_text() + "*.egg-info/\n")
        subprocess.run(["git", "-C", str(life.root), "commit", "-qam", "ignore egg-info"], check=True)
        refused = life.explain()["functional"]["refused"]
        self.assertIn("*.egg-info/", [item["pattern"] for item in refused])
        self.assertIn("*.egg-info/", life.explain_text())
        self.assertIn("does not make writable", life.explain_text())

    def test_the_round_bound_stops_the_fourth_tester_run_and_names_the_remedy(self) -> None:
        life = self.life()
        for n in range(functional_review.ROUND_BOUND):
            runtime_record = {
                "job_id": f"old-{n}", "kind": "functional_test", "target_repo": str(life.root),
                "work_item_id": lifecycle.WID, "created_at": f"2026-10-0{n + 1}T00:00:00Z", "status": "FINISHED",
                "functional": {"outcome": "findings", "launched": True, "head": f"{n}" * 40, "checklist_blob": "x"}}
            controller_runtime.write_json(life.runtime, f"jobs/old-{n}.json", runtime_record)
        record = life.step(gate=life.usage_gate())
        self.assertEqual(record["functional"]["gate"], "functional_rounds_exhausted")
        text = record["human_gate_pending"]["what_is_required"]
        self.assertIn("functional.automated", text)
        self.assertIn("by hand", text)
        self.assertEqual((life.launches, life.reservations()), ([], {}))

    def test_explain_equals_what_the_step_then_does(self) -> None:
        life = self.life(flows=2)
        before = life.explain()["functional"]
        self.assertEqual((before["next"], before["flows"], before["head"]), ("run", ["flow-1", "flow-2"], life.head()))
        self.assertIn("functional review: the next step runs the tester", life.explain_text())
        life.plan = [("flow-1", "passed", None), ("flow-2", "failed", "defect")]
        record = life.step()
        after = life.explain()
        self.assertEqual(record["functional"]["outcome"], "findings")
        self.assertNotIn("functional", after)  # row 38 now: the applier, not the tester
        bad = self.life(checklist="No flows.\\n")
        self.assertEqual(bad.explain()["functional"]["next"], "checklist_unrecognised")
        self.assertEqual(bad.step()["functional"]["gate"], "checklist_unrecognised")


class TheFaultStopsTest(_TesterCase):
    def gate_of(self, record: dict) -> str:
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record)
        return record["functional"]["gate"]

    def test_each_cause_of_an_unavailable_tester_is_a_review_wait_with_nothing_recorded(self) -> None:
        for cause in (functional_review.UNAVAILABLE_NO_BINARY, functional_review.UNAVAILABLE_ROLE_REFUSED,
                      functional_review.UNAVAILABLE_ENFORCEMENT, functional_review.UNAVAILABLE_CLONE_MISMATCH):
            with self.subTest(cause=cause):
                life = self.life()
                life.raises = functional_review.unavailable(cause, f"cause {cause}")
                record = life.step(gate=life.usage_gate())
                self.assertEqual(self.gate_of(record), "functional_tester_unavailable")
                self.assertIn(f"cause {cause}", record["human_gate_pending"]["what_is_required"])
                self.assertEqual(life.evidence(), {})
                self.assertEqual(life.reservations(), {})  # released: nothing started
                self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["review_wait"])

    def test_a_breach_records_nothing_and_keeps_the_sandbox(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        life.extra = {"breach": ["worktree digest"], "keep": True}
        record = life.step(gate=life.usage_gate())
        self.assertEqual(self.gate_of(record), "isolation_breach")
        self.assertIn("kept at", record["human_gate_pending"]["what_is_required"])
        self.assertEqual((life.evidence(), life.reservations()), ({}, {}))
        self.assertTrue(Path(record["functional"]["sandbox"]).is_dir())

    def test_an_unbound_clone_records_nothing_even_for_a_passed_flow(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        life.extra = {"unbound": ["app/a.py: sha256 differs"], "keep": True}
        record = life.step()
        self.assertEqual(self.gate_of(record), "tested_bytes_unbound")
        self.assertEqual(life.evidence(), {})
        self.assertEqual(life.row(), "38e")

    def test_unreadable_results_record_nothing(self) -> None:
        life = self.life()
        life.plan = lambda kwargs, outdir: None  # no RESULTS.json
        record = life.step(gate=life.usage_gate())
        self.assertEqual(self.gate_of(record), "results_invalid")
        self.assertEqual((life.evidence(), life.reservations()), ({}, {}))

    def test_unreadable_results_show_the_testers_final_message(self) -> None:
        life = self.life()
        life.plan = lambda kwargs, outdir: None  # no RESULTS.json
        life.extra = {"tester_message": "Sandbox is required but failed to initialize: no bridge sockets"}
        record = life.step(gate=life.usage_gate())
        self.assertEqual(self.gate_of(record), "results_invalid")
        required = record["human_gate_pending"]["what_is_required"]
        self.assertIn("RESULTS.json was not written", required)
        self.assertIn("Sandbox is required but failed to initialize", required)
        self.assertIn("failed to initialize", record["functional"]["tester_message"])

    def test_a_refusal_after_two_entries_keeps_those_two_and_names_the_workflow_code(self) -> None:
        life = self.life(flows=3)
        life.plan = [("flow-1", "passed", None), ("flow-2", "passed", None), ("flow-3", "passed", None)]
        real = protocol.record_external_result
        calls: list = []

        def third_refused(*args, **kwargs):
            calls.append(args)
            if len(calls) == 3:
                raise job.ControllerError("evidence_malformed: log_digest must be 64 hex")
            return real(*args, **kwargs)

        with mock.patch.object(protocol, "record_external_result", third_refused):
            record = life.step()
        self.assertEqual(self.gate_of(record), "recording_refused")
        self.assertEqual(record["functional"]["recorded"], ["flow-1", "flow-2"])
        self.assertIn("evidence_malformed", record["human_gate_pending"]["what_is_required"])
        self.assertEqual(set(life.evidence()), {"flow-1", "flow-2"})

    def test_a_usage_limit_is_the_ordinary_incomplete_outcome_and_the_run_is_charged(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        life.outcome = "limit"
        life.extra = {"detail": "rejected"}
        record = life.step(gate=life.usage_gate())
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.FUNCTIONAL_LIMIT_CODE)
        self.assertEqual((life.evidence(), life.reservations()), ({}, {}))

    def test_a_fault_after_the_run_ends_the_record_failed_and_releases_the_reservation(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "failed", "defect")]

        def refused(*args, **kwargs):
            raise job.ControllerError("the functional_review artifact cannot be resolved")

        with mock.patch.object(protocol, "resolve_artifact", refused), self.assertRaises(job.ControllerError):
            life.step(gate=life.usage_gate())
        [record] = life.tester_records()
        self.assertEqual((record["status"], record["functional"]["outcome"]), (job.STATUS_FAILED, "error"))
        self.assertEqual(record["reconciliation_evidence"]["code"], "ControllerError")
        self.assertEqual(life.reservations(), {})
        self.assertEqual(job.pending_reconciliation_jobs(life.runtime, life.inspect(), IDENTITY), [])

    def test_the_reservation_is_released_on_a_fault_and_on_a_clean_run(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]
        record = life.step(gate=life.usage_gate())
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(life.reservations(), {})
        self.assertEqual(record["usage"]["accounting"], "done")


class TheRowThirtySevenNoteTest(unittest.TestCase):
    def test_the_checklist_writer_is_told_the_sandbox_limits(self) -> None:
        life = TesterLifecycle(self)
        life.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        life.run_until("IMPLEMENTING")
        life.manual_gate("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation")
        life.run_until("AWAITING_FUNCTIONAL_REVIEW")
        life.step()
        [task] = [t for t in life.task_texts if t.startswith("/prepare-functional-review")]
        self.assertIn(functional_review.sandbox_limits_note(), task)


class AFunctionalRecordIsResumableTest(_TesterCase):
    def test_a_tester_record_left_launched_by_a_dead_controller_is_reconciled_interrupted(self) -> None:
        life = self.life()
        life.plan = [("flow-1", "passed", None)]

        class Dies(BaseException):
            pass

        life.raises = Dies()
        with self.assertRaises(Dies):
            life.step()
        [record] = life.tester_records()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)  # a crash: nothing ended the record
        self.assertTrue(job.validate_record(record, managed_repo=life.inspect(), identity=IDENTITY).valid)
        results = job.resume(life.inspect(), identity=IDENTITY, runtime=life.runtime)
        [reconciled] = [r for r in results if r.get("kind") == "functional_test"]
        self.assertIn(reconciled["status"], (job.STATUS_INTERRUPTED, job.STATUS_INCOMPLETE))
        self.assertEqual(reconciled["reconciliation_evidence"]["code"], job.FUNCTIONAL_INTERRUPTED_CODE)


class ReviewWaitProductionPathTest(unittest.TestCase):
    """Notification-fix D7 (round 7 I1): the Workflow's own protocol gates, written by the production
    ``run_job`` fall-through, derive the event the plan says -- never a hand-built record."""

    def gate_records(self, life, stage: str, phase: str) -> tuple[dict, dict]:
        """The wait and, after a BLOCK verdict is on file, the BLOCK-resolution gate at ``phase``."""
        life.run_until(phase)
        wait = life.step()
        life.user_step(lifecycle._AUDITED_REVIEW, stage, "BLOCK", "manual")
        return wait, life.step()

    def assert_wait_and_block(self, wait: dict, block: dict, wait_row: str, block_row: str, action: str) -> None:
        for record in (wait, block):
            self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
            self.assertNotIn("protocol", record)  # the block `resume` reads as a launched protocol job is not added
        self.assertEqual(wait["protocol_gate"], {"row": wait_row, "disposition": "external_gate", "action_id": action})
        self.assertEqual(block["protocol_gate"]["row"], block_row)
        self.assertEqual(block["protocol_gate"]["disposition"], "human_gate")
        self.assertEqual(block["protocol_gate"]["action_id"], "review.resolve_block")
        [waiting] = job.derive_job_event(wait)
        self.assertEqual((waiting.event_type, waiting.title), ("review_wait", "Waiting for the external review: demo"))
        self.assertEqual([e.event_type for e in job.derive_job_event(block)], ["user_gate"])

    def test_the_plan_review_wait_is_a_review_wait_and_its_block_gate_a_user_gate(self) -> None:
        life = lifecycle.GateLifecycle(self)
        wait, block = self.gate_records(life, "plan", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assert_wait_and_block(wait, block, "14", "11", "plan.review.external")

    def test_the_implementation_review_wait_is_a_review_wait_and_its_block_gate_a_user_gate(self) -> None:
        life = lifecycle.GateLifecycle(self)
        life.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        life.run_until("IMPLEMENTING")
        wait, block = self.gate_records(life, "implementation", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assert_wait_and_block(wait, block, "28", "25", "implementation.review.external")

    def test_a_record_written_before_this_change_is_a_user_gate(self) -> None:
        life = lifecycle.GateLifecycle(self)
        life.run_until("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        record = life.step()
        legacy = {k: v for k, v in record.items() if k != "protocol_gate"}
        self.assertEqual([e.event_type for e in job.derive_job_event(legacy)], ["user_gate"])


class BlockedDispositionProductionPathTest(unittest.TestCase):
    """Notification-fix D7 (implementation round 1 I1): a gate the Workflow itself disposes ``blocked`` is written
    by the production fall-through with ``protocol_gate`` and no ``protocol`` block, and derives what it derived
    at the base commit -- a ``user_gate``, never a ``review_wait``."""

    def test_a_blocked_disposition_gate_is_a_user_gate_without_a_protocol_block(self) -> None:
        life = lifecycle.GateLifecycle(self)
        life.run_until("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        real = protocol.next_action

        def blocked(*args, **kwargs):
            answer = real(*args, **kwargs)
            raw = dict(answer.raw, row="22", disposition="blocked", action=None, satisfied_by=None,
                       reason=dict(answer.raw["reason"], code="plan_not_approved", text="the plan is not approved"))
            return protocol.parse_decision(raw)

        with mock.patch.object(protocol, "next_action", blocked):
            record = life.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record)
        self.assertNotIn("protocol", record)
        self.assertEqual(record["protocol_gate"]["disposition"], "blocked")
        [event] = job.derive_job_event(record)
        self.assertEqual(event.event_type, "user_gate")
        self.assertIsNone(event.supersedes)


from controller import notify  # noqa: E402
from tests import test_notifications_wiring as wiring, test_review_job as rj  # noqa: E402
from tests.fake_signalhub import FakeSignalHub  # noqa: E402


class ControllerCodedGateOverARow14DecisionTest(rj._ReviewCase):
    """Notification-fix D7: the Controller-coded gates over a row-14 decision (the review-mode refusal, the
    external round cap) are written without ``protocol_gate``, so they stay ``user_gate``; the plain wait of the
    same row is the ``review_wait``."""

    def assert_user_gate(self, record: dict) -> None:
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record)
        self.assertNotIn("protocol_gate", record)
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["user_gate"])

    def test_the_review_mode_refusal_is_a_user_gate(self) -> None:
        with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError("no worker"))):
            record = job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                      reviewer=None, review_mode_options={"mode": "cross-model", "source": "cli"})
        self.assertIn("review_mode_unavailable", record["human_gate_pending"]["what_is_required"])
        self.assert_user_gate(record)

    def test_the_external_round_cap_is_a_user_gate(self) -> None:
        with mock.patch.object(job.review_rounds, "split_rounds", return_value=job.review_rounds.SplitRounds(1, 4)):
            record = self.step()
        self.assertTrue(record["human_gate_pending"]["what_is_required"].startswith("review_round_cap_reached: "))
        self.assert_user_gate(record)

    def test_the_plain_wait_of_the_same_row_is_a_review_wait(self) -> None:
        record = self.step(reviewer_options=None)
        self.assertEqual(record["protocol_gate"]["row"], "14")
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["review_wait"])


class ReviewWaitLiveUpgradeTest(unittest.TestCase):
    """Notification-fix D3 (round 9 I3, round 10 I4): after the upgrade, a production ``step`` in a new run that
    meets the unchanged row-14 gate sends no second notice for a stop whose old ``user_gate`` the server holds,
    and one ``review_wait`` for a stop whose old notice failed, was rejected or was never sent."""

    def setUp(self) -> None:
        self.life = lifecycle.GateLifecycle(self)
        self.life.run_until("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.hub = FakeSignalHub().start()
        self.addCleanup(self.hub.stop)

    def step(self, *, before_this_change: bool = False) -> dict:
        """One production step in a run of its own, under a fresh notifier (a new Controller invocation);
        ``before_this_change`` derives as the released Controller did (no ``review_wait``)."""
        wiring.install_notifier(self, self.hub, self.life.runtime)
        run = job.open_run(self.life.runtime, command="step", target_repo=str(self.life.root), max_steps=None,
                           ident=IDENTITY)
        self.addCleanup(run.discard)
        old = mock.patch.object(job, "_review_wait_title", return_value=None) if before_this_change \
            else contextlib.nullcontext()
        with old:
            record = job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.life.runtime,
                                      run_id=run.run_id)
        self.assertTrue(notify.flush(10))
        notify.uninstall()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record)
        return record

    def requested(self) -> list[str]:
        return [r["body"]["metadata"]["event_type"] for r in self.hub.requests]

    def test_an_old_user_gate_the_server_holds_is_never_followed_by_a_review_wait(self) -> None:
        first = self.step(before_this_change=True)
        self.assertEqual((self.requested(), len(self.hub.events)), (["user_gate"], 1))
        second = self.step()
        self.assertNotEqual(second["job_id"], first["job_id"])
        self.assertEqual(job.derive_job_event(second)[0].ident, job.derive_job_event(first)[0].ident)
        self.assertEqual((self.requested(), len(self.hub.events)), (["user_gate"], 1))

    def test_a_never_sent_stop_is_one_review_wait_and_a_later_step_stores_nothing_more(self) -> None:
        self.step()
        self.assertEqual((self.requested(), len(self.hub.events)), (["review_wait"], 1))
        self.assertEqual(self.hub.requests[0]["body"]["severity"], "LOW")
        self.step()
        self.assertEqual(set(self.requested()), {"review_wait"})
        self.assertEqual(len(self.hub.events), 1)

    def test_a_failed_or_rejected_old_user_gate_does_not_veto_and_recovery_replays_nothing(self) -> None:
        for status, scripted in (("failed", None), ("rejected", 401)):
            with self.subTest(status=status):
                self.setUp()
                self.hub.queue(scripted)
                first = self.step(before_this_change=True)
                self.assertEqual((self.requested(), self.hub.events), (["user_gate"], {}))
                log = self.life.runtime / "runs" / first["run_id"] / notify.NOTIFICATIONS_LOG
                self.assertIn(status, [json.loads(line)["status"] for line in log.read_text().splitlines()])
                self.step()  # the service or the credentials are restored
                self.assertEqual(self.requested(), ["user_gate", "review_wait"])
                self.assertEqual(len(self.hub.events), 1)
                self.hub.requests.clear()
                wiring.install_notifier(self, self.hub, self.life.runtime)
                job.resume(self.life.inspect(), identity=IDENTITY, runtime=self.life.runtime)
                self.assertTrue(notify.flush(10))
                notify.uninstall()
                self.assertEqual(self.requested(), [])
