"""The outcome of a protocol job (workflow-controller-orchestration-protocol-v1
CP5, plan Design E): the Workflow's own ``reconcile`` judges it, on launch and
on ``resume``, after the release identity is re-checked.

A fake worker is a ``worker.launch`` double that makes the repository
changes the action would and returns a classified result; ``reconcile``
itself is the real 2.7.0 script unless a test replaces it.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, managed_repo, protocol, worker  # noqa: E402
from controller.errors import DriftedInstallationError, WorkflowProtocolFailedError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402

WID = "demo"

_REVISE_CODE = """
import datetime, json
root = Path.cwd()
wid = sys.argv[1]
bundle_dir = root / fingerprint.resolve_bundle_dir(root, wid, stage="plan")
bundle_id = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["bundle_id"]
rcid, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ws.state_transaction(root, lambda s: ws.record_local_plan_review(
    s, wid, verdict="REVISE", bundle_id=bundle_id, review_content_id=rcid, round=1, now=now))
"""

_CHECKPOINT_CODE = """
import datetime, json, subprocess
root = Path.cwd()
wid, mode = sys.argv[1], sys.argv[2]
def git(*args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
registry = json.loads((root / f"docs/ai-workflow/registry/{wid}-registry.json").read_text())
if mode == "nothing":
    raise SystemExit(0)
head = git("rev-parse", "HEAD")
ws.state_transaction(root, lambda s: ws.transition_checkpoint_in_progress(s, wid, "CP1", start_commit=head, now=now))
(root / "CP1.txt").write_text("CP1 implemented\\n")
trailers = f"\\n\\nWorkflow-Checkpoint: CP1\\nWorkflow-Work-Item: {wid}\\n"
if mode == "unrelated":
    (root / "other.txt").write_text("unrelated\\n")
    git("add", "other.txt")
    git("commit", "-q", "-m", "An unrelated commit")
    raise SystemExit(0)
if mode == "trailer_only":
    git("add", "CP1.txt")
    git("commit", "-q", "-m", "Implement CP1" + trailers)
ws.state_transaction(root, lambda s: ws.complete_checkpoint(s, wid, "CP1", registry, now=now, repo_root=root))
if mode == "all":
    git("add", "-A")
    git("commit", "-q", "-m", "Implement CP1" + trailers)
"""


def _result(outcome: str = "SUCCESS") -> worker.WorkerResult:
    return worker.WorkerResult(
        outcome=outcome, returncode=0 if outcome == "SUCCESS" else 1, session_id="s", is_error=False,
        subtype="success", terminal_reason=None, stop_reason=None, result="ok", num_turns=1,
        permission_denials=[], total_cost_usd=0.0, duration_ms=1, stdout="", stderr="", raw_json={})


class _Env:
    """A committed 2.7.0 target for one work item, and a runtime root."""

    def __init__(self, test: unittest.TestCase, stage: str = "publish", *, implementing: bool = False) -> None:
        self.test = test
        self.td = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__("shutil").rmtree(self.td, ignore_errors=True))
        self.root = self.td / "repo"
        fixtures.seed_workflow_item(self.root, "2.7.0", stage, work_item_id=WID)
        if implementing:
            self._approve_plan()
        self.stub = fixtures.write_stub_workflow_manager(self.td / "workflow-manager", release="2.7.0")
        self.runtime = self.td / "runtime"
        self.runtime.mkdir()

    def _approve_plan(self) -> None:
        """Move the seeded (bound) item to ``IMPLEMENTING`` with a current plan
        approval whose commit carries the trailers the Workflow discovers."""
        path = self.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        state = json.loads(path.read_text())
        item = state["work_items"][WID]
        rcid = item["plan_review_binding"]["published"]["review_content_id"]
        item.update(phase="IMPLEMENTING", current_bundle_id=None, checkpoints={}, plan_approval={
            "status": "CURRENT", "basis": "EXTERNAL_APPROVE", "reviewed_bundle_id": "a" * 64,
            "approved_review_content_id": rcid, "review_content_manifest": [], "user_confirmation": "approved",
            "reviewed_content_commit": None, "waived_guarantees": []})
        del item["plan_review_binding"]
        path.write_text(json.dumps(state, indent=2) + "\n")
        fixtures.run(["git", "add", "-A"], cwd=self.root)
        fixtures.run(["git", "commit", "-q", "-m",
                      f"approve plan\n\nWorkflow-Plan-Approval: {rcid}\nWorkflow-Work-Item: {WID}\n"],
                     cwd=self.root)

    def inspect(self):
        return managed_repo.inspect(self.root, manager_bin=str(self.stub))

    def records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.runtime / "jobs").glob("*.json"))]

    def workflow(self, code: str, *args: str) -> None:
        fixtures.run_workflow_python(self.root, code, *args)

    def revise(self) -> None:
        self.workflow(_REVISE_CODE, WID)

    def checkpoint(self, mode: str) -> None:
        self.workflow(_CHECKPOINT_CODE, WID, mode)

    def step(self, effect=None, outcome: str = "SUCCESS", *, reconcile=None):
        """One ``execute_step``; ``effect(env)`` runs as the worker's work."""
        def launch(task, *, cwd, **kwargs):
            if effect is not None:
                effect(self)
            return _result(outcome)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(worker, "launch", launch))
            if reconcile is not None:
                stack.enter_context(mock.patch.object(protocol, "reconcile", reconcile))
            return job.execute_step(self.inspect(), identity=IDENTITY, runtime=self.runtime)

    def edit_script(self, name: str = "workflow_state.py") -> None:
        path = self.root / "scripts" / name
        path.write_text(path.read_text() + "\n# edited by the worker\n")


def _no_reconcile(*args, **kwargs):
    raise AssertionError("reconcile was called")


def _reconciliation(outcome: str, *, invalid=(), to_phase: str = "REVISING_PLAN", next_=None):
    return protocol.Reconciliation(
        outcome=outcome, from_=None, to=protocol.PhaseIdentity(to_phase, "f" * 64), evidence={},
        invalid_reasons=tuple(invalid), basis=None, next=next_, raw={})


class ClassToResultTest(unittest.TestCase):
    def test_no_progress_after_a_successful_worker_is_a_terminal_finished_job_with_progress_none(self) -> None:
        env = _Env(self)
        record = env.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["protocol"]["progress"], "none")
        self.assertEqual(record["protocol"]["reconcile"]["class"], "no_progress")
        self.assertNotIn("reconciliation_evidence", record)
        self.assertEqual(job.pending_reconciliation_jobs(env.runtime, env.inspect(), IDENTITY), [])

    def test_no_progress_loops_are_counted_by_the_guard_without_a_resume_between_steps(self) -> None:
        env = _Env(self)
        first = env.step()
        second = env.step()
        self.assertEqual([first["status"], second["status"]], [job.STATUS_FINISHED] * 2)
        third = env.step()
        self.assertEqual(third["status"], job.STATUS_GATE_BLOCKED)
        self.assertIn("no_progress_repeated", json.dumps(third["human_gate_pending"]))
        self.assertEqual(len(env.records()), 3)

    def test_progress_is_verified_and_stores_the_next_decision(self) -> None:
        env = _Env(self, "ready")
        record = env.step(lambda e: e.revise())
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], "REVISING_PLAN")
        block = record["protocol"]["reconcile"]
        self.assertEqual(block["class"], "progress")
        self.assertEqual(block["next"]["action"]["id"], "plan.author")
        self.assertNotIn("progress", record["protocol"])

    def test_an_interrupted_worker_that_progressed_is_verified(self) -> None:
        env = _Env(self, "ready")
        record = env.step(lambda e: e.revise(), "INTERRUPTED")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["protocol"]["reconcile"]["class"], "progress")

    def test_an_interrupted_worker_that_did_nothing_is_interrupted_and_not_counted_as_finished(self) -> None:
        env = _Env(self)
        record = env.step(None, "INTERRUPTED")
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(record["protocol"]["reconcile"]["class"], "no_progress")
        self.assertNotIn("progress", record["protocol"])

    def test_gate_reached_is_verified(self) -> None:
        env = _Env(self)
        record = env.step(None, reconcile=lambda *a, **k: _reconciliation("gate_reached", to_phase="PLANNING"))
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["protocol"]["reconcile"]["class"], "gate_reached")

    def test_a_failed_or_timed_out_worker_is_failed_and_reconcile_is_not_called(self) -> None:
        for outcome in ("FAILURE", "AMBIGUOUS"):
            with self.subTest(outcome=outcome):
                env = _Env(self)
                record = env.step(None, outcome, reconcile=_no_reconcile)
                self.assertEqual(record["status"], job.STATUS_FAILED)
                self.assertEqual(record["reconciliation_evidence"]["reason"], "worker_outcome")
                self.assertEqual(record["reconciliation_evidence"]["worker_outcome"], outcome)
                self.assertIsNone(record["protocol"]["reconcile"])


class InvalidAndFailingTest(unittest.TestCase):
    def test_a_decision_the_worker_left_illegal_is_invalid_with_the_reasons_recorded_verbatim(self) -> None:
        env = _Env(self)

        def jump(e: _Env) -> None:
            fixtures.update_workflow_state(e.root, WID, phase="AWAITING_LOCAL_PLAN_REVIEW")
        record = env.step(jump)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["reason"], "reconcile_invalid")
        self.assertEqual(evidence["reconcile_class"], "invalid")
        self.assertTrue(evidence["invalid_reasons"])
        self.assertEqual(evidence["invalid_reasons"], record["protocol"]["reconcile"]["invalid_reasons"])
        self.assertEqual(record["protocol"]["reconcile"]["class"], "invalid")

    def test_each_reason_reconcile_gives_is_kept_verbatim(self) -> None:
        env = _Env(self)
        reasons = (("illegal_edge", "x -> y"), ("checkpoint_completion_unproven", "no trailer"))
        record = env.step(None, reconcile=lambda *a, **k: _reconciliation("invalid", invalid=reasons))
        self.assertEqual(record["reconciliation_evidence"]["invalid_reasons"],
                         [{"code": code, "text": text} for code, text in reasons])

    def test_a_reconcile_that_fails_is_workflow_protocol_failed_not_reconcile_invalid(self) -> None:
        env = _Env(self)

        def failing(*args, **kwargs):
            raise WorkflowProtocolFailedError("no answer", evidence={"reason": "protocol_no_document"})
        record = env.step(None, reconcile=failing)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "workflow_protocol_failed")
        self.assertEqual(record["reconciliation_evidence"]["workflow_error"]["evidence"]["reason"],
                         "protocol_no_document")

    def test_an_unknown_class_is_workflow_protocol_failed(self) -> None:
        env = _Env(self)
        record = env.step(None, reconcile=lambda *a, **k: _reconciliation("surprise"))
        self.assertEqual(record["reconciliation_evidence"]["reason"], "workflow_protocol_failed")


class ReleaseIdentityTest(unittest.TestCase):
    def test_a_worker_that_edits_a_managed_script_ends_failed_and_reconcile_is_never_called(self) -> None:
        for name in ("workflow_protocol.py", "workflow_state.py"):
            with self.subTest(script=name):
                env = _Env(self)
                record = env.step(lambda e, name=name: e.edit_script(name), reconcile=_no_reconcile)
                self.assertEqual(record["status"], job.STATUS_FAILED)
                evidence = record["reconciliation_evidence"]
                self.assertEqual(evidence["reason"], "workflow_release_changed")
                self.assertIn(f"scripts/{name}", evidence["workflow_error"]["evidence"]["changed_scripts"])

    def test_a_failed_worker_that_also_changed_a_script_ends_with_the_one_release_reason(self) -> None:
        env = _Env(self)
        record = env.step(lambda e: e.edit_script(), "FAILURE", reconcile=_no_reconcile)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "workflow_release_changed")

    def test_a_non_workflow_script_edit_still_reconciles(self) -> None:
        env = _Env(self)
        extra = env.root / "scripts" / "extra.py"
        extra.write_text("print('mine')\n")
        fixtures.commit_all(env.root, "add extra")
        record = env.step(lambda e: extra.write_text("print('edited')\n"))
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertNotIn("reconciliation_evidence", record)


class CheckpointCompletionTest(unittest.TestCase):
    """Plan E.3: the Controller's committed-state fact for a checkpoint, on a
    2.7.0 fixture, for each 1.6.0 detail."""

    def _run(self, mode: str) -> dict:
        env = _Env(self, "ready", implementing=True)
        self.assertEqual(protocol.next_action(env.root, WID).action.id, "implementation.checkpoint")
        return env.step(lambda e: e.checkpoint(mode))

    def test_a_committed_completion_is_progress(self) -> None:
        record = self._run("all")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["protocol"]["reconcile"]["class"], "progress")

    def test_completion_not_committed_at_head_and_last_completed_not_committed(self) -> None:
        # A trailer commit exists, `COMPLETE` and the pointer only in the working tree: the protocol says
        # `progress`; the Controller's fact fails the job (both 1.6.0 details map to this one reason).
        record = self._run("trailer_only")
        self.assertEqual(record["protocol"]["reconcile"]["class"], "progress")
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["reason"], "completion_not_committed_at_head")
        self.assertTrue(any("CP1" in fact for fact in evidence["facts"]))

    def test_head_unchanged_and_no_newly_completed_checkpoint_are_never_progress(self) -> None:
        for mode in ("nothing", "unrelated"):
            with self.subTest(mode=mode):
                record = self._run(mode)
                self.assertNotEqual(record["protocol"]["reconcile"]["class"], "progress")
                self.assertNotIn("completion_not_committed_at_head", json.dumps(record))

    def test_the_two_capture_details_exist_only_on_the_legacy_path(self) -> None:
        # `pre_state_incomplete` and `state_unreadable` are Controller capture facts: the protocol branch
        # never produces or consults them, whatever the pre-state holds.
        env = _Env(self, "ready", implementing=True)

        def break_pre_state(e: _Env) -> None:
            e.checkpoint("all")
        launched = {}
        real = job._durable_pre_state

        def thin(pre_state):
            durable = real(pre_state)
            launched["thin"] = True
            durable.pop("checkpoints", None)
            return durable
        with mock.patch.object(job, "_durable_pre_state", thin):
            record = env.step(break_pre_state)
        self.assertTrue(launched)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertNotIn("predicate_detail", json.dumps(record.get("reconciliation_evidence", {})))
        source = Path(job.__file__).read_text()
        protocol_section = source[source.index("def _protocol_resolution"):source.index("def _reconcile_completed")]
        for detail in ("pre_state_incomplete", "state_unreadable", "CHECKPOINT_PROGRESS_DETAILS"):
            self.assertNotIn(detail, protocol_section)


class ResumeTest(unittest.TestCase):
    def _crash_after(self, env: _Env, effect, point: str):
        """Run one step and abort the Controller at ``point``: ``launched``
        (the worker ran, nothing recorded after the spawn) or ``completed``
        (the ``COMPLETED`` write landed, the outcome was not judged)."""
        def launch(task, *, cwd, **kwargs):
            if effect is not None:
                effect(env)
            if point == "launched":
                raise KeyboardInterrupt
            return _result()

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(worker, "launch", launch))
            if point == "completed":
                stack.enter_context(mock.patch.object(job, "_protocol_resolution", side_effect=KeyboardInterrupt))
            with self.assertRaises(KeyboardInterrupt):
                job.execute_step(env.inspect(), identity=IDENTITY, runtime=env.runtime)
        (record,) = env.records()
        return record

    def test_a_launched_job_is_reconciled_from_the_stored_decision(self) -> None:
        env = _Env(self, "ready")
        record = self._crash_after(env, lambda e: e.revise(), "launched")
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        (resumed,) = job.resume(env.inspect(), identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_FINISHED)
        self.assertEqual(resumed["protocol"]["reconcile"]["class"], "progress")
        self.assertTrue(resumed["reconciled_this_call"])

    def test_a_launched_job_that_did_nothing_is_interrupted(self) -> None:
        env = _Env(self)
        self._crash_after(env, None, "launched")
        (resumed,) = job.resume(env.inspect(), identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)

    def test_a_completed_job_is_reconciled_from_the_stored_decision(self) -> None:
        env = _Env(self, "ready")
        record = self._crash_after(env, lambda e: e.revise(), "completed")
        self.assertEqual(record["status"], job.STATUS_COMPLETED)
        (resumed,) = job.resume(env.inspect(), identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_FINISHED)

    def test_a_completed_no_progress_job_is_finished_with_progress_none(self) -> None:
        env = _Env(self)
        self._crash_after(env, None, "completed")
        (resumed,) = job.resume(env.inspect(), identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_FINISHED)
        self.assertEqual(resumed["protocol"]["progress"], "none")

    def test_a_resumed_job_whose_script_changed_is_failed_with_reconcile_never_called(self) -> None:
        env = _Env(self, "ready")
        self._crash_after(env, lambda e: (e.revise(), e.edit_script()), "completed")
        with mock.patch.object(protocol, "reconcile", _no_reconcile):
            (resumed,) = job.resume(env.inspect(), identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_FAILED)
        self.assertEqual(resumed["reconciliation_evidence"]["reason"], "workflow_release_changed")

    def test_a_non_workflow_script_edit_still_reconciles_on_resume(self) -> None:
        env = _Env(self)
        extra = env.root / "scripts" / "extra.py"
        extra.write_text("print('mine')\n")
        fixtures.commit_all(env.root, "add extra")
        self._crash_after(env, lambda e: extra.write_text("print('edited')\n"), "completed")
        (resumed,) = job.resume(env.inspect(), identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_FINISHED)


class DriftedResumeTest(unittest.TestCase):
    """E.2: under a drifted installation `resume` may end a pending protocol
    job whose managed-file digest map differs from its record's, and nothing
    else, running no Workflow script."""

    def _pending(self, env: _Env) -> dict:
        return ResumeTest()._crash_after(env, None, "completed")

    def _resume_cli(self, env: _Env):
        target = managed_repo.inspect_for_resume(env.root, manager_bin=str(self._drifted_stub(env)))
        return target

    def _drifted_stub(self, env: _Env) -> Path:
        return fixtures.write_stub_workflow_manager(env.td / "drifted-manager", release="2.7.0", verify_exit=1)

    def test_a_changed_map_marks_the_pending_job_failed_without_running_a_script(self) -> None:
        env = _Env(self)
        record = self._pending(env)
        marker = env.td / "marker"
        script = env.root / "scripts" / "workflow_state.py"
        script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")\n'
                          + script.read_text().replace('WORKFLOW_RELEASE = "2.7.0"', 'WORKFLOW_RELEASE = "9.9.9"'))
        target = self._resume_cli(env)
        self.assertIsNotNone(target.drift)
        with mock.patch.object(protocol, "run", side_effect=AssertionError("a Workflow script ran")), \
                mock.patch.object(protocol, "reconcile", _no_reconcile):
            (resumed,) = job.resume(target, identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_FAILED)
        self.assertEqual(resumed["reconciliation_evidence"]["reason"], "workflow_release_changed")
        self.assertFalse(marker.exists())
        self.assertEqual(env.records()[0]["status"], job.STATUS_FAILED)
        self.assertEqual(env.records()[0]["job_id"], record["job_id"])

    def test_an_equal_map_re_raises_and_leaves_the_record_untouched(self) -> None:
        env = _Env(self)
        self._pending(env)
        before = env.records()
        target = self._resume_cli(env)
        with self.assertRaises(DriftedInstallationError):
            job.resume(target, identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(env.records(), before)

    def test_nothing_pending_re_raises(self) -> None:
        env = _Env(self)
        target = self._resume_cli(env)
        with self.assertRaises(DriftedInstallationError):
            job.resume(target, identity=IDENTITY, runtime=env.runtime)

    def test_a_pending_legacy_record_under_drift_is_still_refused(self) -> None:
        env = _Env(self)
        record = self._pending(env)
        legacy = {key: value for key, value in record.items() if key != "protocol"}
        path = env.runtime / "jobs" / f"{record['job_id']}.json"
        path.write_text(json.dumps(legacy))
        target = self._resume_cli(env)
        with self.assertRaises(DriftedInstallationError):
            job.resume(target, identity=IDENTITY, runtime=env.runtime)
        self.assertEqual(json.loads(path.read_text()), legacy)


class CliResumeDriftTest(unittest.TestCase):
    """The same end to end through ``cli.cmd_resume``, with the Manager's
    ``verify`` reporting drift."""

    def setUp(self) -> None:
        import controller.identity as identity_module
        from tests.test_cli import _Args
        self._args_class = _Args
        originals = (identity_module.pin, identity_module.current)
        identity_module.pin = identity_module.current = lambda: IDENTITY
        self.addCleanup(lambda: (setattr(identity_module, "pin", originals[0]),
                                 setattr(identity_module, "current", originals[1])))

    def _resume(self, env: _Env):
        from controller import cli
        stub = fixtures.write_stub_workflow_manager(env.td / "drifted-manager", release="2.7.0", verify_exit=1)
        args = self._args_class(str(env.root), workflow_manager=str(stub))
        with contextlib.redirect_stdout(__import__("io").StringIO()):
            return cli.cmd_resume(args, env.runtime, IDENTITY)

    def test_a_pending_job_whose_worker_edited_a_managed_script_is_failed_and_no_script_runs(self) -> None:
        env = _Env(self)
        ResumeTest()._crash_after(env, lambda e: e.edit_script(), "completed")
        marker = env.td / "marker"
        script = env.root / "scripts" / "workflow_state.py"
        script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
                          + script.read_text().replace('WORKFLOW_RELEASE = "2.7.0"', 'WORKFLOW_RELEASE = "9.9.9"'))
        with mock.patch.object(protocol, "run", side_effect=AssertionError("a Workflow script ran")), \
                mock.patch.object(protocol, "reconcile", _no_reconcile):
            self._resume(env)
        (record,) = env.records()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "workflow_release_changed")
        self.assertFalse(marker.exists())

    def test_a_drifted_installation_whose_map_equals_the_records_is_still_refused(self) -> None:
        env = _Env(self)
        ResumeTest()._crash_after(env, None, "completed")
        before = env.records()
        with self.assertRaises(DriftedInstallationError):
            self._resume(env)
        self.assertEqual(env.records(), before)


if __name__ == "__main__":
    unittest.main()
