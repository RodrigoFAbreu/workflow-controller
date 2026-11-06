"""`workflow-controller-codex-worker-ownership` CP1: a Codex worker record, and what `resume` does with it (H4c2).

Offline, through ``job.execute_step`` (to build the record) and ``job.resume``: the H4c1 fixture adapter and launcher,
and real test-owned processes (``process_fixtures.spawn_tagged_worker``) standing in for the Codex worker and its
nested ``bwrap`` child. Each test has its own job id, so its own ownership tag (a tag shared across parallel shards is
the open flake #259). Nothing real reaches the branch.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import (  # noqa: E402
    cli, codex_worker as cw, codex_worker_landing, codex_worker_sandbox as ws, codex_worker_tree, gitrepo, job,
    managed_repo, protocol, review, worker,
)
from controller.errors import (  # noqa: E402
    DriftedInstallationError, GitDirectoryUnresolvableError, GitOperationError, LifecycleWorkerActiveError, LifecycleWorkerUnverifiableError,
    PendingJobReconciliationError, StaleJobRecordError,
)
from tests import fixtures, process_fixtures  # noqa: E402
from tests.test_job import FAKE_IDENTITY  # noqa: E402
from tests.test_job_codex_worker import JobFixture  # noqa: E402
from tests.test_job_codex_worker_landing import STATE, LandingFixture, git  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_protocol_outcome import WID, _CHECKPOINT_CODE, _Env  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OrphanFixture(JobFixture):
    """A fixture whose first job is interrupted after the spawn of a real tagged worker, leaving a ``LAUNCHED``
    Codex record, a credential copy with a refreshed credential and live processes of the job's tag."""

    nested = True
    spawn = True

    def next_id(self) -> str:
        self.ids += 1
        return f"{self.id().rsplit('.', 1)[-1][-40:]}-{os.getpid()}-{self.ids}"

    def launch_that(self):
        def launch(task, **options):
            layout = ws.WorkerLayout(options["runtime_root"], options["job_id"])
            layout.codex_home.mkdir(parents=True, exist_ok=True)
            (layout.codex_home / "auth.json").write_text(json.dumps({"tokens": "rotated-by-the-worker"}))
            if self.spawn:
                self.leader, self.child = process_fixtures.spawn_tagged_worker(
                    self, options["job_id"], self.tmp / "orphan", nested=self.nested)
                options["on_spawn"](self.leader.pid, self.leader.pid)
            raise KeyboardInterrupt

        return mock.patch.object(cw, "launch_codex_worker", launch)

    def orphan(self) -> dict:
        """The pending record of an interrupted Codex run."""
        with self.launch_that(), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        return self.only_record()

    def layout_of(self, record: dict) -> ws.WorkerLayout:
        return ws.WorkerLayout(self.runtime, record["job_id"])

    def resume(self, managed=None) -> list[dict]:
        return job.resume(managed or self.managed, identity=FAKE_IDENTITY, runtime=self.runtime)

    def alive(self, pid: int | None) -> bool:
        stat = process_fixtures.read_stat(pid) if pid else None
        return stat is not None and stat[0] not in ("Z", "X", "x")

    def end_and_reap(self) -> None:
        process_fixtures.kill_group(self.leader.pid)
        process_fixtures.end_pid(self.child)
        self.leader.wait(timeout=10)
        self.assertTrue(process_fixtures.wait_until(lambda: not self.alive(self.child)))

    def replace(self, record: dict) -> None:
        (self.runtime / "jobs" / f"{record['job_id']}.json").write_text(json.dumps(record))

    def rewrite(self, record: dict, **changes) -> None:
        path = self.runtime / "jobs" / f"{record['job_id']}.json"
        path.write_text(json.dumps({**record, **changes}))


class RecordTest(OrphanFixture):
    spawn = False

    def test_the_block_is_persisted_before_the_tree_is_prepared_and_validates(self) -> None:
        seen = {}

        def prepare(managed, layout, base):
            seen["record"] = self.only_record()
            raise KeyboardInterrupt

        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", prepare), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        before = seen["record"]
        self.assertNotIn("worker_process", before)
        self.assertEqual(before["codex_worker"], {
            "operator_home": str(self.operator), "captured_digest": sha(self.operator / "auth.json"),
            "lock_inherited": False})
        record = self.only_record()
        self.assertEqual((record["status"], record["worker_state"]["state"]), (job.STATUS_LAUNCHED, worker.STARTING))
        self.assertTrue(job.validate_record(record, managed_repo=self.managed, identity=FAKE_IDENTITY).valid)

    def test_the_clone_block_is_in_the_record_before_the_spawn_and_validates_whatever_its_shape(self) -> None:
        record = self.orphan()  # spawn is False here: the launch fake raises before ``on_spawn``
        self.assertNotIn("worker_process", record)
        clone = record["codex_worker"]["clone"]
        layout = self.layout_of(record)
        self.assertEqual(clone["path"], str(layout.worktree))
        self.assertEqual(set(clone), {"path", "branch", "base", "guard"})
        self.assertTrue(job.validate_record(record, managed_repo=self.managed, identity=FAKE_IDENTITY).valid)
        for malformed in ("garbage", {"path": 3}, {}, None):
            block = {**record["codex_worker"], "clone": malformed}
            self.assertTrue(job.validate_record({**record, "codex_worker": block}, managed_repo=self.managed,
                                                identity=FAKE_IDENTITY).valid, malformed)

    def test_a_run_through_the_launcher_carries_the_block_and_the_same_operator_home(self) -> None:
        record = self.run_step()
        self.assertEqual(record["codex_worker"]["operator_home"], str(self.operator))
        self.assertIs(record["codex_worker"]["lock_inherited"], False)
        (options,) = self.launcher.calls
        self.assertEqual(options["operator_home"], self.operator)

    def test_the_predicate_is_the_block_and_never_a_harness_name(self) -> None:
        record = self.run_step()
        self.assertEqual(record["worker_route"]["harness"], "codex-fixture")
        self.assertTrue(job._is_codex_worker_record(record))
        claude = {key: value for key, value in record.items() if key != "codex_worker"}
        self.assertFalse(job._is_codex_worker_record(claude))
        self.assertNotIn("codex_worker", claude)
        for kind in (job.REVIEW_KIND, job.FUNCTIONAL_KIND):
            self.assertFalse(job._is_codex_worker_record({**record, "kind": kind, "schema_version": job.REVIEW_SCHEMA_VERSION}), kind)


class VerdictRowsTest(OrphanFixture):
    def test_active_ends_the_group_and_the_nested_child_settles_the_credentials_and_reconciles(self) -> None:
        record = self.orphan()
        self.assertTrue(self.alive(self.leader.pid) and self.alive(self.child))
        self.assertTrue((self.layout_of(record).codex_home / "auth.json").exists())
        self.assertEqual(process_fixtures.tagged_pids(record["job_id"]).count(self.child), 1)
        (resumed,) = self.resume()
        self.leader.wait(timeout=10)
        self.assertTrue(process_fixtures.wait_until(lambda: not self.alive(self.child)))
        self.assertEqual(process_fixtures.tagged_pids(record["job_id"]), [])
        self.assertFalse((self.layout_of(record).codex_home / "auth.json").exists())
        self.assertIn("rotated-by-the-worker", (self.operator / "auth.json").read_text())
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertNotIn("worker", resumed)
        self.assertEqual(self.only_record()["status"], job.STATUS_INTERRUPTED)

    def test_inactive_with_nothing_owned_reconciles_and_touches_no_process(self) -> None:
        record = self.orphan()
        self.end_and_reap()
        with mock.patch.object(worker, "_kill_drained_group") as kill:
            (resumed,) = self.resume()
        kill.assert_not_called()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertFalse((self.layout_of(record).codex_home / "auth.json").exists())

    def test_unverifiable_is_refused_before_anything_is_ended_or_written(self) -> None:
        record = self.orphan()
        recorded = dict(record["worker_process"], pid_namespace="pid:[4026539999]")
        self.rewrite(record, worker_process=recorded)
        before = self.only_record()
        with self.assertRaises(LifecycleWorkerUnverifiableError) as raised:
            self.resume()
        # a LifecycleWorkerActiveError subclass: the CLI maps it to exit 45, never the blanket 20
        self.assertIsInstance(raised.exception, LifecycleWorkerActiveError)
        self.assertTrue(self.alive(self.leader.pid) and self.alive(self.child))
        self.assertEqual(self.only_record(), before)
        self.assertTrue((self.layout_of(record).codex_home / "auth.json").exists())

    def test_an_unlistable_proc_with_no_live_leader_ends_nothing_and_reconciles_nothing(self) -> None:
        record = self.orphan()
        self.end_and_reap()
        before = self.only_record()
        with mock.patch.object(worker, "scan_recorded_owned_processes", return_value=([], False)), \
                mock.patch.object(worker, "_kill_drained_group") as kill:
            with self.assertRaises(LifecycleWorkerUnverifiableError):
                self.resume()
        kill.assert_not_called()
        self.assertEqual(self.only_record(), before)
        self.assertTrue((self.layout_of(record).codex_home / "auth.json").exists())

    def test_an_unlistable_proc_with_a_live_leader_ends_the_group_then_refuses_and_a_second_resume_reconciles(
            self) -> None:
        record = self.orphan()
        before = self.only_record()
        real = worker._kill_drained_group
        with mock.patch.object(worker, "scan_recorded_owned_processes", return_value=([], False)), \
                mock.patch.object(worker, "_kill_drained_group", wraps=real) as kill:
            with self.assertRaises(LifecycleWorkerUnverifiableError):
                self.resume()
        kill.assert_called_once_with(self.leader.pid)
        self.leader.wait(timeout=10)
        self.assertEqual(self.only_record(), before)
        self.assertTrue((self.layout_of(record).codex_home / "auth.json").exists())
        (resumed,) = self.resume()  # /proc listable again; the leader is gone, the nested child is tag-scanned
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(process_fixtures.tagged_pids(record["job_id"]), [])
        self.assertFalse((self.layout_of(record).codex_home / "auth.json").exists())

    def test_no_worker_process_and_a_tagged_process_alive_ends_it(self) -> None:
        record = self.orphan()
        (path,) = (self.runtime / "jobs").glob("*.json")
        stored = json.loads(path.read_text())
        stored.pop("worker_process", None)
        path.write_text(json.dumps(stored))
        (resumed,) = self.resume()
        self.leader.wait(timeout=10)
        self.assertTrue(process_fixtures.wait_until(lambda: not self.alive(self.child)))
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)

    def test_no_worker_process_and_nothing_alive_is_reconciled(self) -> None:
        record = self.orphan()
        self.end_and_reap()
        (path,) = (self.runtime / "jobs").glob("*.json")
        stored = json.loads(path.read_text())
        stored.pop("worker_process", None)
        path.write_text(json.dumps(stored))
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(resumed["job_id"], record["job_id"])

    def test_phase_one_never_re_attaches_to_a_codex_record(self) -> None:
        self.orphan()
        with mock.patch.object(worker, "reattach", side_effect=AssertionError("re-attached")) as reattach:
            (resumed,) = self.resume()
        reattach.assert_not_called()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)

    def test_the_credential_is_not_copied_over_an_operator_file_that_moved(self) -> None:
        record = self.orphan()
        (self.operator / "auth.json").write_text(json.dumps({"tokens": "the-operator-logged-in-again"}))
        (resumed,) = self.resume()
        self.assertIn("the-operator-logged-in-again", (self.operator / "auth.json").read_text())
        self.assertFalse((self.layout_of(record).codex_home / "auth.json").exists())
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)

    def test_step_is_refused_while_the_pending_record_is_unreconciled_and_held(self) -> None:
        record = self.orphan()
        recorded = dict(record["worker_process"], pid_namespace="pid:[4026539999]")
        self.rewrite(record, worker_process=recorded)
        with self.assertRaises(PendingJobReconciliationError) as raised:
            self.run_step()
        self.assertIn("pending", str(raised.exception).lower())
        self.assertEqual(len(list((self.runtime / "jobs").glob("*.json"))), 1)
        self.assertTrue(self.alive(self.leader.pid))


class DriftedTest(OrphanFixture):
    def drifted(self):
        target = SimpleNamespace(**{key: getattr(self.managed, key) for key in dir(self.managed)
                                    if not key.startswith("__") and not callable(getattr(self.managed, key))})
        target.drift = DriftedInstallationError("drifted", evidence={})
        return target

    def test_a_lone_codex_record_not_admitted_is_refused_before_any_write_and_the_orphan_keeps_running(self) -> None:
        record = self.orphan()
        before = self.only_record()
        with self.assertRaises(DriftedInstallationError):
            self.resume(self.drifted())
        self.assertTrue(self.alive(self.leader.pid) and self.alive(self.child))
        self.assertEqual(self.only_record(), before)
        self.assertTrue((self.layout_of(record).codex_home / "auth.json").exists())
        self.assertEqual(process_fixtures.tagged_pids(record["job_id"]).count(self.child), 1)


class ProtocolCodexTest(unittest.TestCase):
    """A protocol Codex record: `resume` ends the group, then the Workflow's own `reconcile` judges it; under a
    drifted installation the drift rows apply."""

    def setUp(self) -> None:
        self.fixture = OrphanFixture("run_step")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.env = _Env(self)
        self.fixture.managed = self.env.inspect()
        self.fixture.runtime = self.env.runtime
        self.fixture.next_id = lambda: f"protocol-codex-{self.id().rsplit('.', 1)[-1][-30:]}-{os.getpid()}"

    def orphan(self) -> dict:
        fixture = self.fixture
        fixture.record()
        with fixture.launch_that(), self.assertRaises(KeyboardInterrupt):
            job.execute_step(self.env.inspect(), identity=IDENTITY, runtime=self.env.runtime,
                             routing=fixture.options, timeout=30)
        (record,) = self.env.records()
        self.assertIn("protocol", record)
        return record

    def test_a_protocol_codex_record_reaches_the_workflows_own_reconcile_after_the_group_is_ended(self) -> None:
        record = self.orphan()
        fixture = self.fixture
        calls = []
        real = protocol.reconcile

        def reconcile(*args, **kwargs):
            calls.append(fixture.alive(fixture.leader.pid))
            return real(*args, **kwargs)

        with mock.patch.object(protocol, "reconcile", reconcile):
            (resumed,) = job.resume(self.env.inspect(), identity=IDENTITY, runtime=self.env.runtime)
        self.assertEqual(calls, [False])
        self.assertEqual(resumed["job_id"], record["job_id"])
        self.assertIn(resumed["status"], (job.STATUS_INTERRUPTED, job.STATUS_FINISHED))
        self.assertEqual(process_fixtures.tagged_pids(record["job_id"]), [])

    def drifted(self, *, change_a_script: bool = True):
        script = self.env.root / "scripts" / "workflow_state.py"
        if change_a_script:
            script.write_text("# changed after the job was decided\n" + script.read_text())
        stub = fixtures.write_stub_workflow_manager(self.env.td / "drifted-manager", release="2.7.0", verify_exit=1)
        target = managed_repo.inspect_for_resume(self.env.root, manager_bin=str(stub))
        self.assertIsNotNone(target.drift)
        return target

    def test_an_admitted_record_with_an_orphan_alive_is_held_and_nothing_is_ended(self) -> None:
        record = self.orphan()
        fixture = self.fixture
        target = self.drifted()
        (held,) = job.resume(target, identity=IDENTITY, runtime=self.env.runtime)
        self.assertIn("resume_marked", held)
        self.assertEqual(self.env.records()[0]["status"], job.STATUS_LAUNCHED)
        self.assertTrue(fixture.alive(fixture.leader.pid) and fixture.alive(fixture.child))
        self.assertTrue((fixture.layout_of(record).codex_home / "auth.json").exists())

    def test_a_protocol_record_not_admitted_is_refused_before_any_write_and_the_orphan_keeps_running(self) -> None:
        record = self.orphan()
        fixture = self.fixture
        before = self.env.records()
        target = self.drifted(change_a_script=False)
        with self.assertRaises(DriftedInstallationError):
            job.resume(target, identity=IDENTITY, runtime=self.env.runtime)
        self.assertEqual(self.env.records(), before)
        self.assertTrue(fixture.alive(fixture.leader.pid) and fixture.alive(fixture.child))
        self.assertTrue((fixture.layout_of(record).codex_home / "auth.json").exists())
        self.assertEqual(process_fixtures.tagged_pids(record["job_id"]).count(fixture.child), 1)

    def test_an_admitted_record_with_nothing_alive_is_failed_release_changed_with_the_copy_removed(self) -> None:
        record = self.orphan()
        fixture = self.fixture
        fixture.end_and_reap()
        target = self.drifted()
        with mock.patch.object(protocol, "run", side_effect=AssertionError("a Workflow script ran")):
            (failed,) = job.resume(target, identity=IDENTITY, runtime=self.env.runtime)
        self.assertEqual(failed["status"], job.STATUS_FAILED)
        self.assertEqual(failed["reconciliation_evidence"]["reason"], "workflow_release_changed")
        self.assertFalse((fixture.layout_of(record).codex_home / "auth.json").exists())
        self.assertIn("rotated-by-the-worker", (fixture.operator / "auth.json").read_text())


class AbandonTest(OrphanFixture):
    """``resume --abandon`` of a Codex record follows Claude's rules row by row (H4c2, D7), one test per row."""

    def abandon(self, record: dict, *, acknowledge: bool = False, managed=None) -> dict:
        return job.abandon(managed or self.managed, identity=FAKE_IDENTITY, runtime=self.runtime,
                           job_id=record["job_id"], acknowledge_unverifiable_worker=acknowledge)

    def assert_abandoned(self, record: dict, abandoned: dict) -> None:
        self.assertEqual(abandoned["status"], job.STATUS_FAILED)
        self.assertEqual(abandoned["reconciliation_evidence"]["code"], "OperatorAbandoned")
        self.assertEqual(self.only_record()["status"], job.STATUS_FAILED)
        self.assertFalse((self.layout_of(record).codex_home / "auth.json").exists())

    def test_an_active_worker_is_refused_naming_resume_and_nothing_is_ended_or_written(self) -> None:
        record = self.orphan()
        before = self.only_record()
        with self.assertRaises(LifecycleWorkerActiveError) as raised:
            self.abandon(record)
        message = raised.exception.message
        self.assertIn("no flag overrides it", message)
        self.assertIn("workflow-controller resume", message)
        self.assertNotIn("Follow it", message)
        self.assertTrue(self.alive(self.leader.pid) and self.alive(self.child))
        self.assertEqual(self.only_record(), before)
        self.assertTrue((self.layout_of(record).codex_home / "auth.json").exists())

    def test_a_tag_scan_only_record_with_an_owned_process_alive_is_refused_naming_resume(self) -> None:
        record = self.orphan()
        self.end_and_reap_leader_only()
        stored = {key: value for key, value in self.only_record().items() if key != "worker_process"}
        self.replace(stored)
        before = self.only_record()
        with self.assertRaises(LifecycleWorkerActiveError) as raised:
            self.abandon(record)
        message = raised.exception.message
        self.assertIn("ends them", message)
        self.assertIn(f"`workflow-controller resume {self.managed.root}`", message)
        self.assertIn(f"then run `workflow-controller resume --abandon {record['job_id']} {self.managed.root}`", message)
        self.assertNotIn("worker has ended", message)
        self.assertTrue(self.alive(self.child))
        self.assertEqual(self.only_record(), before)

    def end_and_reap_leader_only(self) -> None:
        process_fixtures.kill_group(self.leader.pid)
        self.leader.wait(timeout=10)

    def test_a_missing_root_refuses_a_live_record_by_hand_and_resume_ends_nothing(self) -> None:
        record = self.orphan()
        shutil.rmtree(self.managed.root)
        with self.assertRaises(LifecycleWorkerActiveError) as raised:
            self.abandon(record)
        message = raised.exception.message
        pid, pgid = record["worker_process"]["pid"], record["worker_process"]["pgid"]
        self.assertIn(f"pid {pid}, process group {pgid}", message)
        self.assertIn(f"kill -TERM -- -{pgid}", message)
        self.assertIn(f"then run `workflow-controller resume --abandon {record['job_id']} {self.managed.root}`", message)
        self.assertNotIn("Follow it", message)
        self.assertNotIn(f"`workflow-controller resume {self.managed.root}`", message)
        with self.assertRaises(StaleJobRecordError) as stale:
            self.resume()
        self.assertIn(f"resume --abandon {record['job_id']}", str(stale.exception))
        self.assertTrue(self.alive(self.leader.pid) and self.alive(self.child))

    def test_unverifiable_is_refused_without_the_acknowledgement_and_abandoned_with_it(self) -> None:
        record = self.orphan()
        self.end_and_reap()
        recorded = dict(record["worker_process"], pid_namespace="pid:[4026539999]")
        self.rewrite(record, worker_process=recorded)
        before = self.only_record()
        with self.assertRaises(LifecycleWorkerUnverifiableError):
            self.abandon(record)
        self.assertEqual(self.only_record(), before)
        self.assertTrue((self.layout_of(record).codex_home / "auth.json").exists())
        self.assert_abandoned(record, self.abandon(record, acknowledge=True))
        self.assertIn("rotated-by-the-worker", (self.operator / "auth.json").read_text())

    def test_inactive_is_marked_failed_with_the_credential_copy_gone(self) -> None:
        record = self.orphan()
        self.end_and_reap()
        abandoned = self.abandon(record)
        self.assert_abandoned(record, abandoned)
        self.assertEqual(abandoned["reconciliation_evidence"]["abandoned_status"], job.STATUS_LAUNCHED)
        self.assertIn("rotated-by-the-worker", (self.operator / "auth.json").read_text())

    def test_a_missing_root_with_a_dead_worker_is_marked_failed_and_settles_the_credentials(self) -> None:
        record = self.orphan()
        self.end_and_reap()
        shutil.rmtree(self.managed.root)
        self.assert_abandoned(record, self.abandon(record))


class KilledLandingFixture(LandingFixture):
    """A job whose landing is killed (a ``KeyboardInterrupt``, which no ``except Exception`` of the job catches) at a
    stage of D7's windows, leaving the ``COMPLETED`` record a lost Controller would leave."""

    def setUp(self) -> None:
        super().setUp()
        self.phase = self.expected_phase()
        self.after = lambda clone: self.commit_in(clone, {STATE: self.state_with_phase(self.phase)}, "transition")
        self.enterContext(mock.patch.object(gitrepo, "landing_fetch", wraps=gitrepo.landing_fetch))
        self.fetches = self.merges = 0

    def killing_lander(self, stage: str):
        def lander(managed_repo, prepared, runtime_root, job_id, *, worker_breach, on_intent):
            layout = ws.WorkerLayout(runtime_root, job_id)
            if stage == "before_intent":
                raise KeyboardInterrupt
            tip = gitrepo.ref_commit(prepared.path, f"refs/heads/{prepared.branch}")
            on_intent(tip)
            if stage == "before_fetch":
                raise KeyboardInterrupt
            gitrepo.landing_fetch(managed_repo.root, prepared.path, prepared.branch, job_id,
                                  runner=codex_worker_landing._runner(layout.base, fetch=True))
            if stage == "after_fetch":
                raise KeyboardInterrupt
            gitrepo.landing_merge(managed_repo.root, tip, runner=codex_worker_landing._runner(layout.base))
            raise KeyboardInterrupt

        return lander

    def killed(self, stage: str) -> dict:
        with mock.patch.object(job, "CODEX_LANDER", self.killing_lander(stage)), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_COMPLETED)
        return record

    def resume(self, managed=None) -> dict:
        with mock.patch.object(gitrepo, "landing_fetch", side_effect=AssertionError("resume fetched")), \
                mock.patch.object(gitrepo, "landing_merge", side_effect=AssertionError("resume merged")):
            (record,) = job.resume(managed or self.managed, identity=FAKE_IDENTITY, runtime=self.runtime)
        return record

    def abandon(self, record: dict, managed=None) -> dict:
        return job.abandon(managed or self.managed, identity=FAKE_IDENTITY, runtime=self.runtime,
                           job_id=record["job_id"])

    def rewrite(self, **changes) -> dict:
        record = self.only_record()
        record.update(changes)
        (self.runtime / "jobs" / f"{record['job_id']}.json").write_text(json.dumps(record))
        return record

    def refs(self) -> str:
        return git(self.managed.root, "for-each-ref", "refs/controller")

    def assert_refused_clone_kept(self, resumed: dict, cause: str, *, moved=False) -> dict:
        self.assertEqual(resumed["status"], job.STATUS_FAILED)
        evidence = resumed["reconciliation_evidence"]
        self.assertEqual((evidence["code"], evidence["cause"]), (job.CODEX_LANDING_REFUSED_CODE, cause))
        block = resumed["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["cause"], block["clone_kept"]), ("refused", cause, True))
        self.assertEqual(block["moved"], None if cause == "landing_unreadable" else moved)
        self.assertEqual(evidence["clone"], str(ws.WorkerLayout(self.runtime, "job1").worktree))
        self.assertTrue(self.clone_kept())
        self.assert_only_tree_and_sessions()
        self.assertEqual(self.refs(), "")
        return block


class WindowsTest(KilledLandingFixture):
    def test_killed_before_the_fetch_resume_ends_the_landing_interrupted_and_nothing_landed(self) -> None:
        self.killed("before_fetch")
        resumed = self.resume()
        self.assert_refused_clone_kept(resumed, "landing_interrupted")
        self.assertEqual(self.branch_tip(), self.base)

    def test_killed_after_the_fetch_the_leftover_landing_ref_is_removed(self) -> None:
        self.killed("after_fetch")
        self.assertNotEqual(self.refs(), "")
        resumed = self.resume()
        self.assert_refused_clone_kept(resumed, "landing_interrupted")
        self.assertEqual(self.branch_tip(), self.base)

    def test_killed_after_the_merge_resume_records_landed_recovered_and_removes_the_tree(self) -> None:
        self.killed("after_merge")
        resumed = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        block = resumed["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["recovered"], block["clone_kept"]), ("landed", True, False))
        self.assertEqual(block["tip"], self.branch_tip())
        self.assertEqual(self.refs(), "")
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_the_branch_at_neither_commit_is_landing_unverified_and_the_clone_is_kept(self) -> None:
        self.killed("after_fetch")
        fixtures.commit_all(self.managed.root, "elsewhere", allow_empty=True)
        resumed = self.resume()
        self.assert_refused_clone_kept(resumed, "landing_unverified", moved=True)

    def test_the_branch_at_base_with_the_workers_content_staged_is_merge_left_tree_dirty(self) -> None:
        self.killed("before_fetch")
        (self.managed.root / "half.txt").write_text("half\n")
        git(self.managed.root, "add", "half.txt")
        resumed = self.resume()
        block = self.assert_refused_clone_kept(resumed, "merge_left_tree_dirty")
        self.assertTrue(any("half.txt" in path for path in block["dirty"]), block["dirty"])
        self.assertEqual(git(self.managed.root, "status", "--porcelain"), "A  half.txt")  # nothing repaired

    def test_no_intent_block_and_a_dirty_tree_is_landing_interrupted_with_the_paths(self) -> None:
        self.killed("before_intent")
        (self.managed.root / "mine.txt").write_text("mine\n")
        resumed = self.resume()
        block = self.assert_refused_clone_kept(resumed, "landing_interrupted")
        self.assertEqual(block["dirty"], ["mine.txt"])
        self.assertTrue((self.managed.root / "mine.txt").exists())

    def test_a_range_that_removes_an_ignore_rule_is_landing_unverified_never_landed(self) -> None:
        (self.managed.root / ".gitignore").write_text("ign/\n")
        fixtures.commit_all(self.managed.root, "ignore")
        (self.managed.root / "ign").mkdir()
        (self.managed.root / "ign" / "x").write_text("ignored\n")
        self.base = git(self.managed.root, "rev-parse", "HEAD")
        self.after = lambda clone: self.commit_in(clone, {".gitignore": "\n"}, "unignore")
        self.killed("after_merge")
        resumed = self.resume()
        self.assert_refused_clone_kept(resumed, "landing_unverified", moved=True)
        self.assertEqual(self.branch_tip(), resumed["codex_worker"]["landing"]["tip"])

    def test_a_kill_between_the_removal_of_the_tree_and_the_terminal_write_is_finished_by_resume(self) -> None:
        with mock.patch.object(job, "_observe_post_phase", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            self.run_step()
        record = self.only_record()
        self.assertEqual((record["status"], record["codex_worker"]["landing"]["outcome"]),
                         (job.STATUS_COMPLETED, "landed"))
        self.assertFalse((self.job_dir() / "tree").exists())
        resumed = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_a_final_block_with_the_tree_still_there_is_removed_by_resume(self) -> None:
        real = job._settle_codex_worker_dir

        def settle(runtime_root, job_id, *, keep_tree):
            raise KeyboardInterrupt

        with mock.patch.object(job, "_settle_codex_worker_dir", settle), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        self.assertTrue(self.clone_kept())
        resumed = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assertFalse((self.job_dir() / "tree").exists())
        del real

    def test_a_final_nothing_block_goes_to_the_reconciliation_and_the_tree_is_removed(self) -> None:
        self.after = None
        with mock.patch.object(job, "_observe_post_phase", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            self.run_step()
        self.assertEqual(self.only_record()["codex_worker"]["landing"]["outcome"], "nothing")
        resumed = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_FAILED)
        self.assertEqual(resumed["reconciliation_evidence"]["reason"], "phase_not_in_to_any_of")
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_a_failed_removal_of_the_tree_is_recorded_as_tree_left(self) -> None:
        self.killed("after_merge")
        real = job.runtime.remove_tree

        def remove(root, rel, **kw):
            if str(rel).endswith("/tree"):
                raise OSError("busy")
            return real(root, rel, **kw)

        with mock.patch.object(job.runtime, "remove_tree", remove):
            resumed = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_FINISHED)
        block = resumed["codex_worker"]["landing"]
        self.assertTrue(block["clone_kept"])
        self.assertIn("busy", block["tree_left"]["reason"])


class LaunchedLandingTest(KilledLandingFixture):
    def test_a_kill_between_prepare_and_the_clone_block_ends_interrupted_naming_the_derived_path(self) -> None:
        def prepare(managed, layout, base):
            codex_worker_tree.prepare(managed, layout, base)
            raise KeyboardInterrupt

        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", prepare), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        record = self.only_record()
        self.assertNotIn("clone", record["codex_worker"])
        resumed = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(resumed["reconciliation_evidence"]["clone"], str(ws.WorkerLayout(self.runtime, "job1").worktree))
        self.assertTrue(self.clone_kept())

    def test_a_launched_record_whose_worker_committed_does_not_land_and_names_the_clone(self) -> None:
        def launch(task, **options):
            self.after(Path(options["cwd"]))
            self.populate_job_dir()
            raise KeyboardInterrupt

        with mock.patch.object(cw, "launch_codex_worker", launch), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        self.assertEqual(self.only_record()["status"], job.STATUS_LAUNCHED)
        resumed = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(resumed["reconciliation_evidence"]["clone"], resumed["codex_worker"]["clone"]["path"])
        self.assertEqual(self.branch_tip(), self.base)
        self.assertTrue(self.clone_kept())
        self.assert_only_tree_and_sessions()
        self.assertNotIn("landing", resumed["codex_worker"])


class AbandonLandingTest(KilledLandingFixture):
    def assert_abandoned_with_block(self, abandoned: dict, cause: str) -> None:
        self.assertEqual(abandoned["status"], job.STATUS_FAILED)
        evidence = abandoned["reconciliation_evidence"]
        self.assertEqual(evidence["code"], "OperatorAbandoned")
        self.assertEqual((evidence["landing_cause"], evidence["clone"]),
                         (cause, str(ws.WorkerLayout(self.runtime, "job1").worktree)))
        block = abandoned["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["cause"], block["clone_kept"]), ("refused", cause, True))
        self.assertTrue(self.clone_kept())

    def test_abandon_deletes_the_landing_ref_and_adds_the_final_block(self) -> None:
        record = self.killed("after_fetch")
        self.assertNotEqual(self.refs(), "")
        self.assert_abandoned_with_block(self.abandon(record), "landing_interrupted")
        self.assertEqual(self.refs(), "")
        self.assertEqual(self.branch_tip(), self.base)

    def test_abandon_of_a_launched_record_keeps_only_the_tree_and_the_sessions(self) -> None:
        """O1 of round 3: only a ``COMPLETED`` record had its job directory settled."""
        def launch(task, **options):
            self.populate_job_dir()
            raise KeyboardInterrupt

        with mock.patch.object(cw, "launch_codex_worker", launch), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        record = self.only_record()
        abandoned = self.abandon(record)
        self.assertEqual(abandoned["status"], job.STATUS_FAILED)
        self.assert_only_tree_and_sessions()

    def test_abandon_after_a_merge_that_holds_removes_the_tree_and_keeps_the_code(self) -> None:
        record = self.killed("after_merge")
        abandoned = self.abandon(record)
        self.assertEqual(abandoned["reconciliation_evidence"]["code"], "OperatorAbandoned")
        self.assertNotIn("landing_cause", abandoned["reconciliation_evidence"])
        self.assertEqual(abandoned["codex_worker"]["landing"]["outcome"], "landed")
        self.assertFalse((self.job_dir() / "tree").exists())

    def test_abandon_over_a_missing_managed_root_ends_with_landing_unreadable_and_no_exception(self) -> None:
        record = self.killed("after_fetch")
        shutil.rmtree(self.managed.root)
        self.assert_abandoned_with_block(self.abandon(record), "landing_unreadable")

    def test_abandon_over_a_malformed_clone_or_landing_block_ends_with_landing_unreadable(self) -> None:
        record = self.killed("after_fetch")
        block = record["codex_worker"]
        for name, changed in (("clone", {**block, "clone": "garbage"}), ("landing", {**block, "landing": "garbage"})):
            with self.subTest(block=name):
                self.rewrite(codex_worker=changed, status=job.STATUS_COMPLETED)
                abandoned = self.abandon(record)
                self.assertEqual(abandoned["reconciliation_evidence"]["code"], "OperatorAbandoned")
                self.assertEqual(abandoned["codex_worker"]["landing"]["cause"], "landing_unreadable")
                self.rewrite(status=job.STATUS_COMPLETED)

    def test_a_resume_over_a_missing_root_refuses_with_nothing_written_and_abandon_then_ends_it(self) -> None:
        record = self.killed("after_fetch")
        before = self.only_record()
        shutil.rmtree(self.managed.root)
        with self.assertRaises(StaleJobRecordError):
            job.resume(self.managed, identity=FAKE_IDENTITY, runtime=self.runtime)
        self.assertEqual(self.only_record(), before)
        self.assert_abandoned_with_block(self.abandon(record), "landing_unreadable")


class UnreadableTest(KilledLandingFixture):
    def test_a_garbage_git_index_ends_failed_landing_unreadable_with_the_clone_kept(self) -> None:
        self.killed("before_fetch")
        (self.managed.root / ".git" / "index").write_bytes(b"garbage")
        resumed = self.resume()
        self.assert_refused_clone_kept(resumed, "landing_unreadable")

    def test_a_malformed_clone_block_and_a_malformed_landing_block_end_landing_unreadable(self) -> None:
        record = self.killed("before_fetch")
        block = record["codex_worker"]
        for name, changed in (("clone", {**block, "clone": {"path": 3}}), ("landing", {**block, "landing": [1]})):
            with self.subTest(block=name):
                self.rewrite(codex_worker=changed, status=job.STATUS_COMPLETED)
                resumed = self.resume()
                self.assertEqual(resumed["status"], job.STATUS_FAILED)
                self.assertEqual(resumed["reconciliation_evidence"]["cause"], "landing_unreadable")
                self.assertTrue(self.clone_kept())
                # The guide's `rm -rf` names this path, so it must be the clone's even when the block's is unusable.
                worktree = str(ws.WorkerLayout(self.runtime, "job1").worktree)
                self.assertEqual(resumed["reconciliation_evidence"]["clone"], worktree)
                self.assertEqual(resumed["codex_worker"]["landing"]["clone"], worktree)
                self.rewrite(status=job.STATUS_COMPLETED, reconciliation_evidence=None)

    def test_a_failed_ref_delete_names_the_ref_and_changes_no_cause(self) -> None:
        self.killed("after_fetch")
        with mock.patch.object(gitrepo, "landing_delete_ref", side_effect=GitOperationError("denied")):
            resumed = self.resume()
        block = resumed["codex_worker"]["landing"]
        self.assertEqual((block["cause"], block["ref_left"], block["ref"]),
                         ("landing_interrupted", True, "refs/controller/landing/job1"))

    def test_a_removed_git_directory_refuses_resume_and_abandon_with_nothing_written(self) -> None:
        record = self.killed("before_fetch")
        before = self.only_record()
        for what in (".git", ".git/objects"):
            with self.subTest(removed=what):
                aside = self.tmp / "aside"
                shutil.move(str(self.managed.root / what), str(aside))
                try:
                    with self.assertRaises(GitDirectoryUnresolvableError):
                        job.resume(self.managed, identity=FAKE_IDENTITY, runtime=self.runtime)
                    with self.assertRaises(GitDirectoryUnresolvableError):
                        self.abandon(record)
                    self.assertEqual(self.only_record(), before)
                finally:
                    shutil.move(str(aside), str(self.managed.root / what))

    def test_the_restored_git_directory_lets_abandon_end_the_record(self) -> None:
        record = self.killed("before_fetch")
        aside = self.tmp / "aside"
        shutil.move(str(self.managed.root / ".git"), str(aside))
        with self.assertRaises(GitDirectoryUnresolvableError):
            self.abandon(record)
        shutil.move(str(aside), str(self.managed.root / ".git"))
        abandoned = self.abandon(record)
        self.assertEqual(abandoned["reconciliation_evidence"]["code"], "OperatorAbandoned")
        self.assertEqual(abandoned["reconciliation_evidence"]["landing_cause"], "landing_interrupted")


class ProtocolKilledLandingTest(unittest.TestCase):
    """A protocol Codex record killed in the landing: the drifted ending, and the command's own refusals."""

    def setUp(self) -> None:
        self.fixture = KilledLandingFixture("run_step")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.env = _Env(self, "ready", implementing=True)
        self.fixture.managed = self.env.inspect()
        self.fixture.runtime = self.env.runtime
        self.fixture.record()
        self.fixture.after = lambda clone: fixtures.run_workflow_python(clone, _CHECKPOINT_CODE, WID, "all")
        with mock.patch.object(job, "CODEX_LANDER", self.fixture.killing_lander("after_fetch")), \
                self.assertRaises(KeyboardInterrupt):
            job.execute_step(self.env.inspect(), identity=IDENTITY, runtime=self.env.runtime,
                             routing=self.fixture.options, timeout=30)
        (self.record,) = self.env.records()
        self.assertIn("protocol", self.record)

    def drifted(self):
        script = self.env.root / "scripts" / "workflow_state.py"
        script.write_text("# changed after the job was decided\n" + script.read_text())
        stub = fixtures.write_stub_workflow_manager(self.env.td / "drifted-manager", release="2.7.0", verify_exit=1)
        target = managed_repo.inspect_for_resume(self.env.root, manager_bin=str(stub))
        self.assertIsNotNone(target.drift)
        return target

    def refs(self) -> str:
        return git(self.env.root, "for-each-ref", "refs/controller")

    def test_a_drifted_resume_keeps_its_status_and_code_and_adds_the_final_block(self) -> None:
        self.assertNotEqual(self.refs(), "")
        with mock.patch.object(gitrepo, "landing_merge", side_effect=AssertionError("merged")):
            (resumed,) = job.resume(self.drifted(), identity=IDENTITY, runtime=self.env.runtime)
        self.assertEqual(resumed["status"], job.STATUS_FAILED)
        evidence = resumed["reconciliation_evidence"]
        self.assertEqual(evidence["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
        self.assertEqual((evidence["landing_cause"], evidence["clone"]),
                         ("merge_left_tree_dirty", resumed["codex_worker"]["clone"]["path"]))
        # the drift itself (an edited managed script) leaves the tree changed, which window (a) reports
        self.assertEqual(resumed["codex_worker"]["landing"]["cause"], "merge_left_tree_dirty")
        self.assertEqual(resumed["codex_worker"]["landing"]["dirty"], [" M scripts/workflow_state.py"])
        self.assertEqual(self.refs(), "")

    def test_a_drifted_resume_over_a_malformed_block_ends_release_changed_with_landing_unreadable(self) -> None:
        block = {**self.record["codex_worker"], "clone": "garbage"}
        path = self.env.runtime / "jobs" / f"{self.record['job_id']}.json"
        path.write_text(json.dumps({**self.record, "codex_worker": block}))
        (resumed,) = job.resume(self.drifted(), identity=IDENTITY, runtime=self.env.runtime)
        self.assertEqual(resumed["reconciliation_evidence"]["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
        self.assertEqual(resumed["reconciliation_evidence"]["landing_cause"], "landing_unreadable")

    def test_a_drifted_resume_with_a_garbage_git_index_ends_release_changed_with_landing_unreadable(self) -> None:
        target = self.drifted()
        (self.env.root / ".git" / "index").write_bytes(b"garbage")
        (resumed,) = job.resume(target, identity=IDENTITY, runtime=self.env.runtime)
        self.assertEqual(resumed["reconciliation_evidence"]["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
        self.assertEqual(resumed["reconciliation_evidence"]["landing_cause"], "landing_unreadable")

    def command(self, *argv: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with mock.patch.object(cli, "_dispatch", lambda args, raw: cli.cmd_resume(args, self.env.runtime, IDENTITY)), \
                mock.patch.object(cli, "require_pinned_execution"), contextlib.redirect_stderr(stderr), \
                contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--workflow-manager", str(self.env.stub), "resume", "--abandon", self.record["job_id"],
                             str(self.env.root), *argv])
        return code, stderr.getvalue()

    def test_the_command_refuses_a_removed_git_directory_and_a_removed_root_with_exit_20(self) -> None:
        before = (self.env.runtime / "jobs" / f"{self.record['job_id']}.json").read_text()
        aside = self.env.td / "aside"
        shutil.move(str(self.env.root / ".git"), str(aside))
        code, error = self.command()
        self.assertEqual(code, cli.EXIT_FAIL_CLOSED, error)
        self.assertEqual((self.env.runtime / "jobs" / f"{self.record['job_id']}.json").read_text(), before)
        moved = self.env.td / "moved-root"
        shutil.move(str(self.env.root), str(moved))
        code, error = self.command()
        self.assertEqual(code, cli.EXIT_FAIL_CLOSED, error)
        self.assertEqual((self.env.runtime / "jobs" / f"{self.record['job_id']}.json").read_text(), before)
        shutil.move(str(moved), str(self.env.root))
        shutil.move(str(aside), str(self.env.root / ".git"))
        code, error = self.command()
        self.assertEqual(code, cli.EXIT_OK, error)
        ended = json.loads((self.env.runtime / "jobs" / f"{self.record['job_id']}.json").read_text())
        self.assertEqual(ended["reconciliation_evidence"]["code"], "OperatorAbandoned")


class UnchangedTest(unittest.TestCase):
    def test_the_group_label_of_a_review_record_is_the_text_it_always_had(self) -> None:
        for harness_name, display in ((review.CODEX.name, review.CODEX.display),
                                      (review.CLAUDE.name, review.CLAUDE.display)):
            record = {"kind": job.REVIEW_KIND, "review": {"harness": harness_name}}
            self.assertEqual(job._group_label(record), f"{display} review")
        self.assertEqual(job._group_label({"kind": job.FUNCTIONAL_KIND}), f"{review.CLAUDE.display} review")
        self.assertEqual(job._group_label({"codex_worker": {}}), "Codex worker")


if __name__ == "__main__":
    unittest.main()
