"""The stale check, the job record and the loop guard of a protocol target
(workflow-controller-orchestration-protocol-v1 CP4, plan Design D): the
``protocol`` block of the job record, the currency check before the
``PLANNED`` write and again immediately before the spawn, the
``no_progress_repeated`` guard and the per-step identity re-check.

A protocol launch is off until CP5 (``job.PROTOCOL_LAUNCH_ENABLED``), so these
tests switch it on and replace ``worker.launch`` with a double that records the
call and stops: nothing here reads an outcome.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, managed_repo, protocol, protocol_decision, target_state, worker  # noqa: E402
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
            stack.enter_context(mock.patch.object(job, "PROTOCOL_LAUNCH_ENABLED", True))
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
        with mock.patch.object(job, "_launch_job", side_effect=RuntimeError("aborted")), \
                mock.patch.object(job, "PROTOCOL_LAUNCH_ENABLED", True):
            with self.assertRaises(RuntimeError):
                job.execute_step(target.inspect(), identity=IDENTITY, runtime=target.runtime)
        (planned,) = target.records()
        self.assertEqual(planned["status"], job.STATUS_PLANNED)
        self.assertIn("protocol", planned)
        resumed = job.resume(target.inspect(), identity=IDENTITY, runtime=target.runtime)
        self.assertEqual(resumed[0]["status"], job.STATUS_INTERRUPTED)


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
               reconcile: str | None, work_item: str | None = "demo", identity: str = "i") -> str:
    jobs = runtime / "jobs"
    jobs.mkdir(exist_ok=True)
    job_id = f"20260101T0000{n:02d}-job{n:02d}"
    record = {
        "job_id": job_id, "target_repo": str(root), "work_item_id": work_item, "status": status,
        "created_at": f"2026-01-01T00:00:{n:02d}Z",
        "protocol": {"action_id": action, "state_identity": identity,
                     "reconcile": None if reconcile is None else {"class": reconcile}},
    }
    (jobs / f"{job_id}.json").write_text(json.dumps(record))
    return job_id


class LoopGuardTest(unittest.TestCase):
    def _gate(self, target: _Target, action: str = "implementation.checkpoint", work_item: str | None = "demo"):
        repo = target.inspect()
        item = target_state.NoWorkItemYet if work_item is None else type("W", (), {"work_item_id": work_item})()
        decision = type("D", (), {"protocol": type("P", (), {"action_id": action})()})()
        with mock.patch.object(protocol_decision, "gate_for", side_effect=lambda *a: a[3:]):
            return job._no_progress_gate(target.runtime, repo, item, decision)

    def _jobs(self, target: _Target, *shapes) -> list[str]:
        return [_write_job(target.runtime, target.root, n, status=status, reconcile=cls, **kw)
                for n, (status, cls, kw) in enumerate(shapes, 1)]

    def test_two_no_progress_jobs_trip_the_guard_naming_both(self) -> None:
        target = _Target(self)
        ids = self._jobs(target, (job.STATUS_FINISHED, "no_progress", {}), (job.STATUS_FINISHED, "no_progress", {}))
        code, text = self._gate(target)
        self.assertEqual(code, protocol_decision.NO_PROGRESS_REPEATED)
        for job_id in ids:
            self.assertIn(job_id, text)

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
