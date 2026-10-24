"""The whole-catalogue determinism check (``workflow-controller-ci-time`` D2).

Two complete runs of every registered scenario against one HEAD agree on every
normalised summary, and every record passes the Workflow's real ingestion. The
two passes run at the same time, each in a clone of its own
(``workflow-controller-determinism-parallel`` D1). The
class sits in a module of its own so that CI can place it by module: it runs in
``main.yml`` after the merge and gates the release, not on every pull request
(``tools/test_shards.py``'s ``CI_PLACEMENT``). A pull request keeps the
three-flow subset in ``tests/test_run_scenarios.py``. A local ``unittest
discover`` still runs this module.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import process_fixtures as pf
from tests import test_run_scenarios as base
from tests.scenarios import kit


class CatalogueDeterminismTest(base.CloneCase):
    """D9, whole catalogue: two complete runs against one HEAD agree on every summary."""

    @classmethod
    def setUpClass(cls) -> None:
        # The base class skips whenever the sandbox is unavailable, and a run that only skips exits 0. Under CI an
        # unavailable sandbox must fail the run instead (as the scenario atoms do), locally it still skips.
        decision, reason = kit.isolation_gate(kit.probe_isolation(), os.environ)
        if decision == "skip":
            raise unittest.SkipTest(reason)
        if decision == "fail":
            raise AssertionError(reason)
        super().setUpClass()
        # Pass B runs in a second clone with the same HEAD, so the two runs never contend for one checkout's index.
        # Neither clone's path contains the other's, because the bounded runner's backstop sweep tags by path.
        cls.second = cls.base / "pass-b"
        base._git(cls.base, "clone", "-q", "--no-hardlinks", str(cls.clone), str(cls.second))

    def complete_run(self, clone: Path) -> dict:
        folder = Path(tempfile.mkdtemp(prefix="run-scenarios-cat-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = self.runner("run", output=folder, module=False, clone=clone, timeout=base.PASS_DEADLINE)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return self.records(folder)

    def test_two_complete_runs_agree_on_every_normalised_summary(self) -> None:
        base._git(self.second, "reset", "-q", "--hard", self.head)
        records, other = base.run_concurrently([lambda: self.complete_run(self.clone),
                                                lambda: self.complete_run(self.second)])
        # D4: every record of every registered flow and variant passes the Workflow's real ingestion, not only the
        # selftest records IngestionTest feeds it.
        self.assert_ingested(records)
        first = {k: v["summary"] for k, v in records.items()}
        second = {k: v["summary"] for k, v in other.items()}
        self.assertGreaterEqual(len(first), 15)
        self.assertFalse([name for name in first if name.startswith("selftest")])
        self.assertEqual(first, second)


class RunConcurrentlyTest(unittest.TestCase):
    """D4: the passes really overlap, results keep job order, and a failure is raised only after every job finished."""

    def test_two_jobs_overlap_in_time(self) -> None:
        barrier = threading.Barrier(2, timeout=10)

        def job(value):
            barrier.wait()  # both must be running at once to pass
            return value

        self.assertEqual(base.run_concurrently([lambda: job("a"), lambda: job("b")]), ["a", "b"])

    def test_results_come_back_in_job_order(self) -> None:
        slow_first = [lambda: (time.sleep(0.3), "first")[1], lambda: "second"]
        self.assertEqual(base.run_concurrently(slow_first), ["first", "second"])

    def test_a_failure_of_either_or_both_jobs_is_raised_after_both_finished(self) -> None:
        for fails in ((True, False), (False, True), (True, True)):
            finished = []

            def job(index, fail=fails):
                time.sleep(0.2 if index else 0)
                finished.append(index)
                if fail[index]:
                    raise RuntimeError(f"job {index}")
                return index

            with self.subTest(fails=fails):
                with self.assertRaises(RuntimeError) as caught:
                    base.run_concurrently([lambda: job(0), lambda: job(1)])
                self.assertEqual(str(caught.exception), f"job {fails.index(True)}")
                self.assertEqual(sorted(finished), [0, 1])  # nothing was still running when it was raised


STALLED = r"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests.scenarios import kit

sleep, timeout = shutil.which("sleep"), shutil.which("timeout")
scratch = Path(tempfile.mkdtemp(prefix="scn-stalled-"))  # under the pass-owned TMPDIR, like kit.run_scenario's
env = kit.build_env(scratch)
same = subprocess.Popen([sleep, "600"], env=env)
other = subprocess.Popen([timeout, "-s", "KILL", "900", sleep, "601"], env=env, start_new_session=True)
Path(sys.argv[2]).write_text(f"{same.pid} {other.pid} {scratch}")
subprocess.Popen([sleep, "602"], env=env).wait()
"""


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:  # a zombie that an outside parent has yet to reap is dead too
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


class RunBoundedTest(unittest.TestCase):
    """D2/D4: a stalled pass fails within its bound and leaves no descendant behind, in any session."""

    DEADLINE, GRACE = 3, 2
    BOUND = DEADLINE + GRACE + base.SWEEP_BOUND

    def stalled(self, reaper: str = base.REAPER_SOURCE):
        folder = Path(tempfile.mkdtemp(prefix="bounded-test-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        record = folder / "pids"
        argv = [sys.executable, "-c", STALLED, str(base.REPO_ROOT), str(record)]
        return argv, record

    def new_zombies(self, before: "set[int]") -> "set[int]":
        """Zombie children of the test process now that were not there before the call (a set comparison: a zombie an
        earlier test left is not this call's, and an older one reaped during the call must not hide a new one)."""
        return pf.zombie_children() - before

    def assert_nothing_left(self, record: Path) -> None:
        same, other, scratch = record.read_text().split(" ", 2)
        self.assertTrue(_gone(int(same)), "the descendant in the child's group survived")
        self.assertTrue(_gone(int(other)), "the descendant in a session of its own survived")
        self.assertEqual(kit.tagged_pids(scratch), [], "a process carrying the scenario scratch path survived")

    def test_a_stalled_child_and_both_descendants_are_gone_when_it_fails(self) -> None:
        argv, record = self.stalled()
        before = pf.zombie_children()
        started = time.monotonic()
        with self.assertRaises(base.PassFailure) as caught:
            base.run_bounded(argv, cwd=base.REPO_ROOT, env=dict(os.environ), deadline=self.DEADLINE,
                             grace=self.GRACE, name="stalled")
        self.assertLess(time.monotonic() - started, self.BOUND)
        self.assertIn("stalled did not finish within its 3-second deadline", str(caught.exception))
        self.assert_nothing_left(record)
        self.assertEqual(self.new_zombies(before), set(), "a descendant of the stalled pass was left a zombie")

    def test_an_unresponsive_reaper_is_swept_by_the_pass_root_alone(self) -> None:
        patched = base.REAPER_SOURCE.replace("signal.signal(signal.SIGTERM, on_term)",
                                             "signal.signal(signal.SIGTERM, signal.SIG_IGN)")
        self.assertNotEqual(patched, base.REAPER_SOURCE)
        argv, record = self.stalled()
        before = pf.zombie_children()
        started = time.monotonic()
        with self.assertRaises(base.PassFailure) as caught:
            # no clone or output tag: only the pass-owned temporary root can find the other-session descendant
            base.run_bounded(argv, cwd=base.REPO_ROOT, env=dict(os.environ), deadline=self.DEADLINE,
                             grace=self.GRACE, name="stalled", reaper=patched)
        self.assertLess(time.monotonic() - started, self.BOUND)
        self.assert_nothing_left(record)
        self.assertEqual(self.new_zombies(before), set(), "a descendant of the dead reaper was left a zombie")
        # 137 when the descendants are killed first: the reaper's wait on the real command returns -9 and it ends itself
        # with 128 + 9 before ``proc.kill()`` runs; -9 when the reaper is killed first. Any non-zero value passes; a 0
        # means a sweep reaped the reaper behind ``Popen``'s back (``Popen`` then gets ECHILD and records 0).
        self.assertIn(caught.exception.reaper_returncode, {137, -9})

    def test_a_child_that_exits_leaves_nothing_behind(self) -> None:
        argv, record = self.stalled()
        argv[2] = STALLED.replace("subprocess.Popen([sleep, \"602\"], env=env).wait()", "")
        before = pf.zombie_children()
        done = base.run_bounded(argv, cwd=base.REPO_ROOT, env=dict(os.environ), deadline=self.DEADLINE,
                                grace=self.GRACE)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assert_nothing_left(record)
        self.assertEqual(self.new_zombies(before), set(), "a descendant of the clean pass was left a zombie")

    def test_the_subreaper_scope_is_counted_and_restores_the_previous_value(self) -> None:
        previous = base._subreaper_value()
        entered, release = threading.Event(), threading.Event()
        seen = []

        def other() -> None:
            with base.child_subreaper():
                entered.set()
                release.wait(10)

        thread = threading.Thread(target=other)
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            with base.child_subreaper():
                seen.append(base._subreaper_value())
            seen.append(base._subreaper_value())  # the first scope is still open
        finally:
            release.set()
            thread.join()
        self.assertEqual(seen, [1, 1])
        self.assertEqual(base._subreaper_value(), previous)

    def test_a_stalled_pass_beside_a_terminating_one_fails_the_helper_after_it_is_reaped(self) -> None:
        argv, record = self.stalled()
        folders = [Path(tempfile.mkdtemp(prefix="bounded-folder-")) for _ in range(2)]
        for folder in folders:
            self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        sibling = []

        def stall():
            return base.run_bounded(argv, cwd=base.REPO_ROOT, env=dict(os.environ), deadline=self.DEADLINE,
                                    grace=self.GRACE, tags=tuple(map(str, folders)), name="stalled")

        def finish():
            done = base.run_bounded([sys.executable, "-c", "print('done')"], cwd=base.REPO_ROOT,
                                    env=dict(os.environ), deadline=self.DEADLINE, grace=self.GRACE)
            sibling.append(done.stdout.strip())

        with self.assertRaises(base.PassFailure):
            base.run_concurrently([stall, finish])
        self.assertEqual(sibling, ["done"])
        self.assert_nothing_left(record)  # reaped before the helper raised
        self.assertTrue(all(folder.exists() for folder in folders))  # still there until the cleanups run
        self.doCleanups()
        self.assertFalse(any(folder.exists() for folder in folders))


class IsolationGateTest(unittest.TestCase):
    """The class cannot pass by skipping on CI: an unavailable sandbox fails there and skips only locally."""

    UNAVAILABLE = kit.Probe(False, "unshare", "no user namespaces")

    def set_up_class(self, env: dict) -> None:
        with mock.patch.object(kit, "probe_isolation", return_value=self.UNAVAILABLE), \
                mock.patch.dict(os.environ, env, clear=True):
            CatalogueDeterminismTest.setUpClass()

    def test_an_unavailable_sandbox_fails_under_ci(self) -> None:
        with self.assertRaises(AssertionError) as caught:
            self.set_up_class({"CI": "true"})
        self.assertIn("unshare", str(caught.exception))

    def test_an_unavailable_sandbox_skips_outside_ci(self) -> None:
        with self.assertRaises(unittest.SkipTest):
            self.set_up_class({})


if __name__ == "__main__":
    unittest.main()
