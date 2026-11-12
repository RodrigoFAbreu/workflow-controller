"""`workflow-controller-codex-worker-usage` CP1: a Codex worker job admitted, reserved and accounted (H4d, D1, D2).

Offline, through ``job.execute_step`` with the H4c1 fixture adapter and launcher and the scripted fake Codex, under a
real gate (``usage.admit``) over the test's own runtime. Each test takes its own job id from the fixture's counter, so
no ownership tag is shared across parallel shards (the open flake #259). Nothing real reaches the branch.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_worker as cw, harness, job, review, routing, usage, worker  # noqa: E402
from controller.errors import WorkerLaunchError  # noqa: E402
from tests.test_job_codex_worker import FIXTURE, JobFixture  # noqa: E402
from tests.test_job_codex_worker_resume import OrphanFixture  # noqa: E402


class GateFixture(JobFixture):
    """A fixture whose ``run_step`` is gated by the real ``usage.admit`` over the test's runtime."""

    def setUp(self) -> None:
        super().setUp()
        self.asked: list[tuple] = []
        self.seed: list = []

    def gate(self, route, *args, **kwargs):
        self.asked.append((route.harness, args, kwargs))
        provider = kwargs.get("provider", usage.PROVIDER_CLAUDE)
        admission = usage.admit(
            self.runtime, provider=provider, role=route.role, model=route.model, repository=str(self.managed.root),
            run_id=None, limits=usage.Limits(provider=provider), now=job.usage_now(), readings=self.seed)
        return admission.hold if admission.hold is not None else admission.token

    def run_gated(self, **kw):
        return self.run_step(usage_gate=self.gate, **kw)

    def snapshot(self) -> dict:
        return usage.snapshot(self.runtime, now=job.usage_now())

    def chain_options(self) -> routing.RoutingOptions:
        roles = {role: {"harness": FIXTURE, "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT,
                        "fallback": [{"harness": "claude-code", "model": "opus"}]}
                 for role in sorted(routing.ROLES)
                 if routing.ADAPTER_ROLE[role] in (harness.WORKER, harness.IMPLEMENTER)}
        config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<test>")
        return routing.RoutingOptions(config=config, config_source="settings")


class AdmissionTest(GateFixture):
    def test_a_codex_job_asks_the_gate_once_with_the_codex_provider(self) -> None:
        self.run_gated()
        self.assertEqual(self.asked, [(FIXTURE, (), {"provider": usage.PROVIDER_CODEX})])

    def test_a_claude_launcher_keeps_the_unchanged_gate_call_with_the_route_alone(self) -> None:
        gate = mock.Mock(return_value=usage.Hold(
            provider=usage.PROVIDER_CLAUDE, window=usage.FIVE_HOUR, percent=99.0, forecast=8.0, resume_at=None,
            reason="x", waitable=False))
        with mock.patch.object(job, "_is_codex_launcher", return_value=False):
            self.run_step(usage_gate=gate)
        (call,) = gate.call_args_list
        self.assertEqual(len(call.args), 1)
        self.assertEqual(call.kwargs, {})

    def test_a_hold_returns_before_any_record_reservation_or_process(self) -> None:
        self.seed = [usage.make_reading(usage.PROVIDER_CODEX, usage.FIVE_HOUR, 99.0, time.time() + 3600,
                                        time.time(), "test")]
        held = self.run_gated()
        self.assertIsInstance(held, job.UsageHold)
        self.assertEqual(held.provider, usage.PROVIDER_CODEX)
        self.assertEqual(list((self.runtime / "jobs").glob("*.json")), [])
        self.assertEqual(self.snapshot()["reservations"], {})
        self.assertEqual(self.execs(), [])
        self.assert_no_copy()


class AccountingTest(GateFixture):
    def test_an_admitted_job_is_accounted_from_its_own_home_with_a_claude_shaped_block(self) -> None:
        record = self.run_gated()
        block = record["usage"]
        self.assertEqual(block["accounting"], "done")
        self.assertEqual(record["job_id"], "job1")
        self.assertEqual((block["five_hour_end"], block["weekly_end"]), (7.0, 3.0))
        self.assertLessEqual(set(job._usage_end_fields({}, {}, {})) | {"token", "accounting"}, set(block) | {"token"})
        snap = self.snapshot()
        self.assertEqual(snap["reservations"], {})
        (entry,) = snap["ledger"]
        self.assertEqual((entry["provider"], entry["id"]), (usage.PROVIDER_CODEX, block["token"]))

    def test_the_reservation_is_bound_to_the_job_before_the_launch(self) -> None:
        bound = []
        real = job.usage.bind_job

        def bind(runtime, token, job_id, *, now):
            bound.append(job_id)
            return real(runtime, token, job_id, now=now)

        with mock.patch.object(job.usage, "bind_job", bind):
            record = self.run_gated()
        self.assertEqual(bound, [record["job_id"]])

    def test_a_second_admission_after_a_finished_job_sees_its_end_percent(self) -> None:
        self.run_gated()
        stored = usage.current_readings(self.snapshot(), usage.PROVIDER_CODEX)
        self.assertEqual(stored[usage.FIVE_HOUR]["percent"], 7.0)

    def test_the_reservation_is_renewed_while_the_process_runs_and_the_ticker_is_stopped(self) -> None:
        events: list[str] = []
        ticker = mock.Mock()
        ticker.start.side_effect = lambda: events.append("start")
        ticker.stop.side_effect = lambda: events.append("stop")
        with mock.patch.object(job, "_usage_ticker", return_value=ticker):
            self.run_gated()
        self.assertEqual(events, ["start", "stop"])

    def test_a_job_that_never_started_is_released_with_no_charge(self) -> None:
        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", None), self.assertRaises(WorkerLaunchError):
            self.run_gated()
        record = self.assert_not_started("WorkerLaunchError")
        self.assertEqual(record["usage"]["accounting"], "done")
        snap = self.snapshot()
        self.assertEqual((snap["reservations"], snap["ledger"]), ({}, []))

    def test_a_run_failing_after_the_spawn_is_charged_as_unknown(self) -> None:
        def launch(task, **options):
            options["on_spawn"](os.getpid(), os.getpgid(0))
            raise RuntimeError("the worker broke")

        with mock.patch.object(cw, "launch_codex_worker", launch), self.assertRaises(RuntimeError):
            self.run_gated()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["usage"]["accounting"], "done")
        (entry,) = self.snapshot()["ledger"]
        self.assertEqual((entry["provider"], entry["delta"]), (usage.PROVIDER_CODEX, "unknown"))

    def test_a_chain_selecting_the_codex_entry_carries_a_token_and_is_accounted(self) -> None:
        self.options = self.chain_options()
        record = self.run_gated()
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)
        self.assertEqual(record["usage"]["accounting"], "done")
        self.assertEqual(self.asked[0][2], {"provider": usage.PROVIDER_CODEX})
        (entry,) = self.snapshot()["ledger"]
        self.assertEqual(entry["provider"], usage.PROVIDER_CODEX)


class ResumeSettlesTest(OrphanFixture, GateFixture):
    """An orphaned Codex record that holds a reservation is settled by ``resume`` (the shared accounting rule)."""

    def run_step(self, **kw):
        kw.setdefault("usage_gate", self.gate)
        return super().run_step(**kw)

    def test_resume_ends_the_orphan_and_settles_its_reservation_as_unknown(self) -> None:
        record = self.orphan()
        self.assertEqual(record["usage"]["accounting"], "open")
        self.assertEqual(len(self.snapshot()["reservations"]), 1)
        (resumed,) = self.resume()
        self.leader.wait(timeout=10)
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(self.only_record()["usage"]["accounting"], "done")
        snap = self.snapshot()
        self.assertEqual(snap["reservations"], {})
        (entry,) = snap["ledger"]
        self.assertEqual((entry["provider"], entry["delta"]), (usage.PROVIDER_CODEX, "unknown"))


if __name__ == "__main__":
    unittest.main()
