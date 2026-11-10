"""A process-group id that is not a worker group never reaches ``os.killpg`` with a signal.

``os.killpg(1, SIGKILL)`` is ``kill(-1, SIGKILL)``: it signals every process the user may signal and ends the
desktop session. A test that records ``{"pid": 1, "pgid": 1}`` for a live review, or a damaged job record, used to
reach it through ``job._end_review_group`` -> ``worker._kill_drained_group``. Every test here replaces ``os.killpg``
and ``os.kill`` with a recorder, so none of them can send a real signal.
"""

from __future__ import annotations

import os
import signal
import unittest
from unittest import mock

import tests
from controller import job, review, runtime, worker

UNSAFE = (None, True, False, -1, 0, 1)


class SignalableGroupTest(unittest.TestCase):
    def test_only_an_integer_above_one_that_is_not_our_own_group_is_signalable(self) -> None:
        for bad in (*UNSAFE, "4242", 4242.0, os.getpgrp()):
            with self.subTest(bad=bad):
                self.assertFalse(runtime.signalable_group(bad))
        for good in (2, 4242, os.getpgrp() + 100000):
            with self.subTest(good=good):
                self.assertTrue(runtime.signalable_group(good))


class UnsafeGroupNeverReachesTheOsTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(os, "killpg")
        self.killpg = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(os, "kill")
        self.kill = patcher.start()
        self.addCleanup(patcher.stop)

    def test_kill_drained_group_refuses_every_unsafe_id(self) -> None:
        for bad in (*UNSAFE, os.getpgrp()):
            with self.subTest(bad=bad):
                worker._kill_drained_group(bad)
        self.killpg.assert_not_called()

    def test_kill_drained_group_still_ends_a_worker_group(self) -> None:
        worker._kill_drained_group(424242)
        self.killpg.assert_called_once_with(424242, signal.SIGKILL)

    def test_kill_process_group_refuses_a_leader_whose_group_is_unsafe(self) -> None:
        for bad in (1, 0, os.getpgrp()):
            with self.subTest(bad=bad), mock.patch.object(os, "getpgid", return_value=bad):
                worker._kill_process_group(99999)
        self.killpg.assert_not_called()

    def test_kill_process_group_still_ends_a_worker_group(self) -> None:
        with mock.patch.object(os, "getpgid", return_value=424242):
            worker._kill_process_group(424242)
        self.killpg.assert_called_once_with(424242, signal.SIGKILL)

    def test_the_killpg_seam_refuses_a_real_signal_to_an_unsafe_group_but_not_the_probe(self) -> None:
        for bad in (1, 0, -1):
            with self.subTest(bad=bad):
                with self.assertRaises(ProcessLookupError):
                    worker._killpg(bad, signal.SIGKILL)
        self.killpg.assert_not_called()
        worker._killpg(1, 0)
        self.killpg.assert_called_once_with(1, 0)

    def test_review_kill_group_refuses_every_unsafe_id_and_ends_a_real_one(self) -> None:
        for bad in (*UNSAFE, os.getpgrp()):
            with self.subTest(bad=bad):
                review._kill_group(bad)
        self.killpg.assert_not_called()
        review._kill_group(424242)
        self.killpg.assert_called_once_with(424242, signal.SIGKILL)

    def test_end_review_group_of_a_record_with_a_fake_group_sends_nothing(self) -> None:
        for bad in (1, 0, -1, True):
            with self.subTest(pgid=bad):
                record = {"job_id": "j1", "worker_process": {"pid": 1, "pgid": bad}}
                active = worker.LivenessAssessment(verdict=worker.ACTIVE, row="same_boot", reason="test",
                                                   process=None, recorded={"pid": 1, "pgid": bad}, current={})
                with mock.patch.object(worker, "assess_worker_liveness", return_value=active), \
                        mock.patch.object(job, "_scan_record_owned", return_value=([], True)):
                    job._end_review_group(record, mock.sentinel.root)
        self.killpg.assert_not_called()
        self.kill.assert_not_called()

    def test_end_review_group_of_a_record_with_a_real_group_ends_it(self) -> None:
        record = {"job_id": "j1", "worker_process": {"pid": 424242, "pgid": 424242}}
        active = worker.LivenessAssessment(verdict=worker.ACTIVE, row="same_boot", reason="test",
                                           process=None, recorded={"pid": 424242, "pgid": 424242}, current={})
        with mock.patch.object(worker, "assess_worker_liveness", return_value=active), \
                mock.patch.object(job, "_scan_record_owned", return_value=([], True)):
            job._end_review_group(record, mock.sentinel.root)
        self.killpg.assert_called_once_with(424242, signal.SIGKILL)


class TestProcessSignalGuardTest(unittest.TestCase):
    """``tests/__init__.py`` makes ``os.kill``/``os.killpg`` refuse a real signal to pid or group 1, 0 or -1.
    The guard is tested through a recorder in place of the real call, never by signalling for real."""

    def guarded(self, name: str = "kill"):
        calls: list[tuple[int, int]] = []
        return tests._guard_signal(lambda target, sig: calls.append((target, sig)), name), calls

    def test_a_real_signal_to_an_unsafe_target_is_refused_before_it_reaches_the_call(self) -> None:
        for name in ("kill", "killpg"):
            for target in tests.UNSAFE_SIGNAL_TARGETS:
                with self.subTest(name=name, target=target):
                    guarded, calls = self.guarded(name)
                    with self.assertRaises(AssertionError):
                        guarded(target, signal.SIGKILL)
                    self.assertEqual(calls, [])

    def test_the_liveness_probe_and_an_ordinary_target_pass_through(self) -> None:
        guarded, calls = self.guarded()
        guarded(1, 0)
        guarded(424242, signal.SIGKILL)
        guarded(-424242, signal.SIGTERM)
        self.assertEqual(calls, [(1, 0), (424242, signal.SIGKILL), (-424242, signal.SIGTERM)])

    def test_every_test_process_has_the_guard_installed(self) -> None:
        for fn in (os.kill, os.killpg):
            self.assertTrue(hasattr(fn, "__wrapped__"), fn)


if __name__ == "__main__":
    unittest.main()
