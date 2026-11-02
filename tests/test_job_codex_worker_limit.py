"""`workflow-controller-codex-worker-usage` CP2: a Codex usage limit reached mid-job, held and repeated (H4d, D3, D4).

Offline, through ``job.execute_step`` with the H4c1 fixture adapter and launcher and the scripted fake Codex, and through
the ``run`` loop's mapping with ``execute_step`` replaced. Each test takes its own job id from the fixture's counter, so
no ownership tag is shared across parallel shards (the open flake #259). Nothing real reaches the branch.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli, codex_worker as cw, job, usage, worker  # noqa: E402
from controller.errors import UnknownHarnessError  # noqa: E402
from tests import test_lifecycle_orchestration as lo  # noqa: E402
from tests.test_job_codex_worker import FIXTURE, JobFixture  # noqa: E402

FACT = {"resets_at": 1790287800, "rate_limit_type": None, "window": "five_hour"}


class LimitedJobTest(JobFixture):
    def test_a_limited_job_that_changed_nothing_is_interrupted_with_the_fact(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(record["worker"]["subtype"], cw.SUBTYPE_LIMIT)
        fact = job.usage_limit_of(record)
        self.assertEqual((fact["window"], fact["rate_limit_type"]), ("five_hour", None))
        self.assertEqual(record["usage_limit"]["resets_at"], fact["resets_at"])
        self.assert_no_copy()

    def test_a_limited_job_is_accounted(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        asked = []

        def gate(route, **kwargs):
            asked.append(kwargs)
            admission = usage.admit(
                self.runtime, provider=usage.PROVIDER_CODEX, role=route.role, model=route.model,
                repository=str(self.managed.root), run_id=None, limits=usage.Limits(provider=usage.PROVIDER_CODEX),
                now=job.usage_now(), readings=[])
            return admission.token

        record = self.run_step(usage_gate=gate)
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(record["usage"]["accounting"], "done")
        snap = usage.snapshot(self.runtime, now=job.usage_now())
        self.assertEqual(snap["reservations"], {})
        self.assertEqual(usage.current_readings(snap, usage.PROVIDER_CODEX)[usage.FIVE_HOUR]["percent"], 100.0)

    def test_a_limited_job_that_moved_the_phase_is_failed_naming_the_limit(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        with mock.patch.object(job, "_limit_moved_nothing", return_value=False):
            record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["usage_limit"]["window"], "five_hour")

    def test_a_limit_with_a_breach_carries_no_fact(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        with mock.patch.object(cw, "_breach", return_value=["a breach"]):
            record = self.run_step()
        self.assertEqual(record["worker"]["subtype"], cw.SUBTYPE_BREACH)
        self.assertNotIn("usage_limit", record["worker"]["stream_diagnosis"])
        self.assertNotEqual(record["status"], job.STATUS_INTERRUPTED)

    def test_resume_has_nothing_to_do_with_a_limited_record(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        record = self.run_step()
        resumed = job.resume(self.managed, identity=job_identity(), runtime=self.runtime)
        self.assertEqual([r for r in resumed if r["job_id"] == record["job_id"] and r.get("reconciled_this_call")], [])
        self.assertEqual(self.only_record()["status"], job.STATUS_INTERRUPTED)


def job_identity():
    from tests.test_job import FAKE_IDENTITY
    return FAKE_IDENTITY


class MidJobHoldProviderTest(unittest.TestCase):
    def test_a_record_with_no_worker_route_or_a_claude_harness_is_claude(self) -> None:
        self.assertEqual(cli._mid_job_hold({"job_id": "j"}, FACT).provider, "claude")
        self.assertEqual(cli._mid_job_hold({"job_id": "j", "worker_route": {}}, FACT).provider, "claude")
        self.assertEqual(cli._mid_job_hold({"job_id": "j", "worker_route": {"harness": "claude-code"}}, FACT).provider,
                         "claude")

    def test_the_codex_harness_is_codex(self) -> None:
        hold = cli._mid_job_hold({"job_id": "j", "worker_route": {"harness": "codex"}}, FACT)
        self.assertEqual((hold.provider, hold.window, hold.mid_job, hold.waitable), ("codex", "five_hour", True, True))

    def test_an_unregistered_harness_raises_unchanged(self) -> None:
        with self.assertRaises(UnknownHarnessError):
            cli._mid_job_hold({"job_id": "j", "worker_route": {"harness": "no-such"}}, FACT)


class RunAndStepTest(lo._LifecycleTestCase):
    def _limited(self):
        record = {"status": job.STATUS_INTERRUPTED, "job_id": "job-c", "usage_limit": FACT,
                  "worker_route": {"harness": "codex"}}
        return mock.patch.object(cli.job, "execute_step", return_value=record)

    def test_step_exits_17_naming_the_reset_without_waiting(self) -> None:
        lc = self.seed("codex-step", lo.IMPLEMENTING)
        with self._limited(), mock.patch.object(cli, "_usage_wait") as wait:
            result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("usage limit reached mid-job", result.stderr)
        self.assertIn("1790287800", result.stderr)
        wait.assert_not_called()

    def test_run_waits_with_the_codex_provider_repeats_and_stops_at_the_third_attempt(self) -> None:
        lc = self.seed("codex-run", lo.IMPLEMENTING)
        holds = []

        def wait(args, runtime_root, run, target, hold, usage_pause):
            holds.append(hold)
            return True

        with self._limited() as step, mock.patch.object(cli, "_usage_wait", wait), \
                mock.patch.object(cli, "_usage_paused") as paused:
            result = self.cli(lc, "run")
        self.assertEqual(result.code, cli.EXIT_USAGE_PAUSED)
        self.assertEqual(step.call_count, cli.USAGE_LIMIT_MAX_ATTEMPTS)
        self.assertEqual([h.provider for h in holds], ["codex"] * (cli.USAGE_LIMIT_MAX_ATTEMPTS - 1))
        self.assertEqual(paused.call_args.args[-1].provider, "codex")
        self.assertIn(cli.USAGE_LIMIT_STOP_PREFIX, result.stderr)
        self.assertIn("the last 3 attempts all reached the limit", result.stderr)


if __name__ == "__main__":
    unittest.main()
