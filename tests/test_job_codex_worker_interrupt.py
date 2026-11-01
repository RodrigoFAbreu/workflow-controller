"""`workflow-controller-codex-worker-ownership` CP2: the interrupts of a Codex worker run, and a killed Controller (H4c2).

Offline. A ``KeyboardInterrupt`` before the spawn leaves the record ``LAUNCHED`` (D8) and ``resume`` ends it
``INTERRUPTED``. A Controller is a child Python process (``ChildController`` below, run through ``unittest``) that drives
``job.execute_step`` with the H4c1 fixture adapter and the fake Codex, which hangs with a descendant and a refreshed
credential; the test waits on the fake's readiness file, never a sleep, and kills that Controller with ``SIGINT`` (the
launcher reaps) and with ``SIGKILL`` (an orphan the next ``resume`` must reap). Each test has its own job id, so its own
ownership tag (a tag shared across parallel shards is the open flake #259). Nothing real reaches the branch.
"""

from __future__ import annotations

import functools
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_worker_sandbox as ws, job, worker  # noqa: E402
from tests import fake_codex, fixtures, process_fixtures  # noqa: E402
from tests.test_job import FAKE_IDENTITY  # noqa: E402
from tests.test_job_codex_worker import JobFixture  # noqa: E402

STATE_VAR = "H4C2_CHILD_STATE"
JOB_VAR = "H4C2_CHILD_JOB"
CHILD = "tests.test_job_codex_worker_interrupt.ChildController.test_run"


class ChildController(JobFixture):
    """The Controller of the process tests below. It does nothing unless a parent test started it with the two
    variables set: it scripts a fake Codex that hangs, then runs the job and blocks until it is killed."""

    def next_id(self) -> str:
        return os.environ[JOB_VAR]

    def setUp(self) -> None:
        # The fixture's folders are the parent test's evidence after the Controller is killed: keep them
        # (the parent's own temporary directory removes them).
        self.enterContext(mock.patch.object(tempfile, "TemporaryDirectory",
                                            functools.partial(tempfile.TemporaryDirectory, delete=False)))
        super().setUp()

    def test_run(self) -> None:
        state = os.environ.get(STATE_VAR)
        if not state:
            self.skipTest("started only by the interrupt tests")
        state = Path(state)
        ready = state / "ready"
        scenario = {"log": str(self.log), "exec": {"verdict": "hang", "descendant": True, "refresh_auth": True},
                    "hang_seconds": 120, "orphan_seconds": 120, "ready_file": str(ready)}
        fake_codex.write_binary(self.package / "bin", scenario, name="codex.js")
        (state / "state.json.tmp").write_text(json.dumps({"runtime": str(self.runtime), "root": str(self.managed.root)}))
        os.replace(state / "state.json.tmp", state / "state.json")
        self.run_step(timeout=300)


class BeforeTheSpawnTest(JobFixture):
    def resume(self) -> list[dict]:
        return job.resume(self.managed, identity=FAKE_IDENTITY, runtime=self.runtime)

    def interrupt(self, **patches) -> dict:
        with mock.patch.multiple(job, **patches), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        record = self.only_record()
        self.assertEqual((record["status"], record["worker_state"]["state"]), (job.STATUS_LAUNCHED, worker.STARTING))
        self.assertNotIn("worker_process", record)
        return record

    def test_an_interrupt_in_the_tree_preparer_leaves_the_block_and_resume_ends_it_interrupted(self) -> None:
        def prepare(managed, layout):
            raise KeyboardInterrupt

        record = self.interrupt(CODEX_WORKTREE_PREPARER=prepare)
        self.assertEqual(record["codex_worker"]["operator_home"], str(self.operator))
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(self.only_record()["status"], job.STATUS_INTERRUPTED)
        self.assert_no_copy()

    def test_an_interrupt_before_the_blocks_persist_leaves_no_block_and_resume_ends_it_interrupted(self) -> None:
        def resolve():
            raise KeyboardInterrupt

        record = self.interrupt(CODEX_HOME_RESOLVER=resolve)
        self.assertNotIn("codex_worker", record)
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(self.only_record()["status"], job.STATUS_INTERRUPTED)


class KilledControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.job_id = f"{self.id().rsplit('.', 1)[-1][-40:]}-{os.getpid()}"
        self.state = self.tmp / "state"
        self.state.mkdir()
        scratch = self.tmp / "child-tmp"
        scratch.mkdir()
        env = {**os.environ, "TMPDIR": str(scratch), STATE_VAR: str(self.state), JOB_VAR: self.job_id}
        env.pop("PYTHONPATH", None)
        self.controller = subprocess.Popen(
            [sys.executable, "-B", "-m", "unittest", CHILD], cwd=REPO_ROOT, env=env, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.end_everything)
        self.assertTrue(process_fixtures.wait_until((self.state / "ready").exists, timeout=60.0),
                        "the fake Codex never announced readiness")
        paths = json.loads((self.state / "state.json").read_text())
        self.runtime, self.root = Path(paths["runtime"]), Path(paths["root"])
        self.managed = fixtures.build_target_managed_repository(self.root)
        self.assertTrue(process_fixtures.wait_until(lambda: len(self.tagged()) >= 2, timeout=30.0),
                        "the fake Codex and its descendant never both carried the tag")
        self.assertTrue(process_fixtures.wait_until(lambda: "worker_process" in self.record(), timeout=30.0))
        self.layout = ws.WorkerLayout(self.runtime, self.job_id)

    def end_everything(self) -> None:
        process_fixtures.kill_group(self.controller.pid)
        for pid in self.tagged():
            process_fixtures.end_pid(pid)
        try:
            self.controller.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def tagged(self) -> list[int]:
        return process_fixtures.tagged_pids(self.job_id)

    def record(self) -> dict:
        try:
            (path,) = (self.runtime / "jobs").glob("*.json")
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return {}

    def resume(self) -> list[dict]:
        return job.resume(self.managed, identity=FAKE_IDENTITY, runtime=self.runtime)

    def test_a_controller_killed_with_sigint_leaves_no_tagged_process_and_resume_reconciles_it(self) -> None:
        self.assertTrue((self.layout.codex_home / "auth.json").exists())
        os.kill(self.controller.pid, signal.SIGINT)
        self.controller.wait(timeout=60)
        self.assertTrue(process_fixtures.wait_until(lambda: not self.tagged(), timeout=30.0), self.tagged())
        self.assertFalse((self.layout.codex_home / "auth.json").exists())
        record = self.record()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        self.assertIn("worker_process", record)
        self.assertEqual(worker.assess_worker_liveness(record["worker_process"]).verdict, worker.INACTIVE)
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(self.tagged(), [])

    def test_a_controller_killed_with_sigkill_leaves_an_orphan_that_resume_ends_with_its_children(self) -> None:
        orphans = self.tagged()
        self.assertGreaterEqual(len(orphans), 2)
        os.kill(self.controller.pid, signal.SIGKILL)
        self.controller.wait(timeout=60)
        self.assertEqual(sorted(self.tagged()), sorted(orphans), "the orphan did not survive its Controller")
        self.assertTrue((self.layout.codex_home / "auth.json").exists())
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertTrue(process_fixtures.wait_until(lambda: not self.tagged(), timeout=30.0), self.tagged())
        self.assertFalse((self.layout.codex_home / "auth.json").exists())
        self.assertEqual(self.record()["status"], job.STATUS_INTERRUPTED)


if __name__ == "__main__":
    unittest.main()
