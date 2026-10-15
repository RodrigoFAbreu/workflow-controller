"""``workflow-controller-usage-limit-mid-job`` CP3: the command-line mapping of an ``INTERRUPTED`` job. The wait
and the repeat are driven end to end by ``scenario-usage-limit-mid-job-waits-and-repeats``."""

from __future__ import annotations

import unittest
import unittest.mock

from controller import cli, job
from tests import test_lifecycle_orchestration as lo

FACT = {"resets_at": 1790287800, "rate_limit_type": "five_hour", "window": "five_hour"}


class InterruptedJobMappingTest(lo._LifecycleTestCase):
    def _lc(self, name: str):
        return self.seed(name, lo.IMPLEMENTING)

    def _interrupted(self, **extra):
        record = {"status": job.STATUS_INTERRUPTED, "job_id": "job-x", **extra}
        return unittest.mock.patch.object(cli.job, "execute_step", return_value=record)

    def test_step_with_the_fact_exits_17_and_names_the_reset(self) -> None:
        lc = self._lc("fact")
        with self._interrupted(usage_limit=FACT):
            result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("usage limit reached mid-job", result.stderr)
        self.assertIn("1790287800", result.stderr)
        self.assertNotIn("nothing was started", result.stderr)

    def test_an_interrupted_job_without_the_fact_exits_40_and_does_not_raise(self) -> None:
        for command in ("step", "run"):
            with self.subTest(command=command):
                lc = self._lc(f"nofact-{command}")
                with self._interrupted():
                    result = self.cli(lc, command)
                self.assertEqual(result.code, cli.EXIT_INTERRUPTED)
                self.assertIn("job-x was interrupted", result.stderr)
                self.assertIn("a new `step` repeats it", result.stderr)

    def test_the_wait_interruption_message_follows_the_hold(self) -> None:
        plain = cli.UsageWaitInterrupted("usage_paused", "T").message()
        mid = cli.UsageWaitInterrupted("usage_paused", "T", mid_job=True).message()
        self.assertTrue(plain.endswith("nothing was started"))
        self.assertNotIn("nothing was started", mid)
        self.assertIn("the worker ran and changed nothing", mid)
        self.assertIn("a new `step` or `run` repeats the step", mid)

    def test_resume_names_the_reset_and_keeps_exit_40(self) -> None:
        lc = self._lc("resume")
        record = {"status": job.STATUS_INTERRUPTED, "job_id": "job-r", "reconciled_this_call": True,
                  "usage_limit": FACT}
        with unittest.mock.patch.object(cli.job, "resume", return_value=[record]):
            result = self.cli(lc, "resume")
        self.assertEqual(result.code, cli.EXIT_INTERRUPTED)
        self.assertIn("job-r reached the usage limit and changed nothing", result.stderr)
        self.assertIn("1790287800", result.stderr)


class MidJobHoldTest(unittest.TestCase):
    def test_the_hold_is_a_waitable_five_hour_mid_job_hold_at_the_reset(self) -> None:
        hold = cli._mid_job_hold({"job_id": "j"}, FACT)
        self.assertTrue(hold.mid_job and hold.waitable)
        self.assertEqual((hold.provider, hold.window, hold.resume_at), ("claude", "five_hour", 1790287800))
        unmapped = cli._mid_job_hold({"job_id": "j"}, {**FACT, "window": None})
        self.assertEqual(unmapped.window, "unknown")


if __name__ == "__main__":
    unittest.main()
