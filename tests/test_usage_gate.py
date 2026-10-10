"""Tests for the usage budget's gate, reservation and run loop
(``workflow-controller-usage-budget`` CP3, plan
``docs/ai-workflow/CONTROLLER_USAGE_BUDGET_PLAN.md`` D6, D7, D10, R2, R3,
R6, R7).

``controller.job`` admits a job before its record exists (a hold is a
``UsageHold``, nothing recorded), binds and records its reservation, renews
it on its own timer while the worker runs, and accounts for it on every
completion path -- live, re-attached and replayed after a crash, with the
real ``never_launched`` predicate. ``controller.cli``'s ``run`` waits with
no lock held and re-enters the whole boundary without consuming a step, and
exit 17 says why it stopped.

Every worker is ``tests/fake_claude.py``; every clock is injected
(``job.usage_now``, ``cli._usage_sleep``); every runtime root is a temporary
directory. No test calls the real ``claude``, reads ``~/.codex`` or the
operator's runtime root.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from controller import cli, job, lock, runtime, settings, usage, worker
from controller.errors import WorkerLaunchError
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT
from tests import fake_claude, fixtures, process_fixtures
from tests import test_job

FIVE, WEEKLY = usage.FIVE_HOUR, usage.WEEKLY
FAKE_IDENTITY = test_job.FAKE_IDENTITY
DAY = 86400.0


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class _UsageCase(test_job._LifecycleCase):
    """A ``"2.1"`` ``PLANNING`` target, a runtime root and an injected usage
    clock ten minutes behind the wall clock, so a worker stream written now
    is newer than any admission (a ``measured`` delta needs that)."""

    def setUp(self) -> None:
        super().setUp()
        self.now = time.time() - 600
        self.enterContext(unittest.mock.patch.object(job, "usage_now", lambda: self.now))
        self.addCleanup(process_fixtures.reap_recorded_workers, self.runtime_root)

    # -- the shared record ---------------------------------------------------

    def shared(self) -> dict:
        return usage.load_record(self.runtime_root)

    def events(self) -> list[dict]:
        return _lines(self.runtime_root / usage.EVENTS_FILE)

    def reading(self, five: float, five_in: float = 3600, weekly: float | None = None,
                weekly_in: float = 7 * DAY) -> None:
        readings = [usage.make_reading("claude", FIVE, five, self.now + five_in, self.now, "test")]
        if weekly is not None:
            readings.append(usage.make_reading("claude", WEEKLY, weekly, self.now + weekly_in, self.now, "test"))
        usage.record_reading(self.runtime_root, readings)

    def rate_limit(self, five: float, five_in: float = 3600, weekly: float | None = None,
                   weekly_in: float = 7 * DAY) -> dict:
        return fake_claude.rate_limit(five, self.now + five_in, weekly,
                                      None if weekly is None else self.now + weekly_in)

    def stream_env(self, *events: dict) -> dict:
        return {"FAKE_CLAUDE_STDOUT": fake_claude.stream_text([*events, *fake_claude.default_events()])}

    # -- steps -----------------------------------------------------------------

    def gate(self, limits: usage.Limits | None = None, run_id: str = "run-1"):
        limits = limits or usage.Limits()

        def gate(route):
            admission = usage.admit(self.runtime_root, provider="claude", role=route.role, model=route.model,
                                    repository=str(self.root), run_id=run_id, limits=limits, now=self.now)
            return admission.hold if admission.hold is not None else admission.token

        return gate

    def ustep(self, env: dict | None = None, limits: usage.Limits | None = None, **kwargs):
        with unittest.mock.patch.dict("os.environ", env or {}):
            return self._step(usage_gate=self.gate(limits), timeout=kwargs.pop("timeout", 60), **kwargs)

    def record_on_disk(self) -> dict:
        [record] = self._records()
        return record

    def assert_no_record(self) -> None:
        jobs = self.runtime_root / "jobs"
        self.assertEqual(sorted(jobs.glob("*.json")) if jobs.is_dir() else [], [])

    def resume(self) -> list[dict]:
        return job.resume(self.managed_repo, identity=FAKE_IDENTITY, runtime=self.runtime_root)


# ---------------------------------------------------------------------------
# The gate and the live completion path
# ---------------------------------------------------------------------------


class GateAndLiveAccountingTest(_UsageCase):

    def test_a_job_under_the_threshold_records_its_usage_block_and_a_measured_ledger_entry(self) -> None:
        self.reading(10)
        order: list[str] = []
        bind, persist = usage.bind_job, job._persist

        def spy_bind(*a, **k):
            order.append("bind")
            return bind(*a, **k)

        def spy_persist(runtime_root, job_id, record, *, event, details=None):
            order.append(event)
            return persist(runtime_root, job_id, record, event=event, details=details)

        with test_job._WriteSpy() as writes, \
                unittest.mock.patch.object(job.usage, "bind_job", spy_bind), \
                unittest.mock.patch.object(job, "_persist", spy_persist):
            record = self.ustep(self.stream_env(self.rate_limit(14)))
        # Bound before the record is published (D6, Binding order).
        self.assertLess(order.index("bind"), order.index("planned"))
        job_writes = [obj for rel, obj in writes.calls if rel.startswith("jobs/")]
        planned = next(w for w in job_writes if w["status"] == job.STATUS_PLANNED)
        completed = next(w for w in job_writes if w["status"] == job.STATUS_COMPLETED)
        token = record["usage"]["token"]
        self.assertEqual(planned["usage"], {
            "token": token, "provider": "claude", "role": record["worker_route"]["role"],
            "model": record["worker_route"]["model"], "repository": str(self.root), "run_id": "run-1",
            "started_at": self.now, "window_resets_at": self.now + 3600, "five_hour_start": 10.0,
            "weekly_start": None, "reserved_five_hour": 8.0, "reserved_weekly": 1.0, "accounting": "open"})
        # The COMPLETED write carries the end figures, pending (D6).
        self.assertEqual(completed["usage"]["accounting"], "pending")
        self.assertEqual((completed["usage"]["five_hour_end"], completed["usage"]["end_window_resets_at"]),
                         (14.0, self.now + 3600))
        self.assertEqual(job.validate_record(completed, managed_repo=self.managed_repo, identity=FAKE_IDENTITY),
                         job.VALID)
        # The terminal record, accounted.
        self.assertIn(record["status"], job.TERMINAL_STATUSES)
        block = record["usage"]
        self.assertEqual((block["accounting"], block["delta"], block["charged"], block["five_hour"]),
                         ("done", "measured", 4.0, 4.0))
        self.assertEqual(record, self.record_on_disk())
        self.assertEqual(job.validate_record(record, managed_repo=self.managed_repo, identity=FAKE_IDENTITY),
                         job.VALID)
        shared = self.shared()
        [entry] = shared["ledger"]
        self.assertEqual((entry["id"], entry["delta"], entry["charged"]), (token, "measured", 4.0))
        self.assertEqual(shared["reservations"], {})
        self.assertIn(token, shared["settled"])
        self.assertEqual(usage.run_spent(shared, "claude", "run-1"), 4.0)
        # The end reading was recorded in the shared record.
        self.assertEqual(shared["readings"]["claude"][FIVE]["percent"], 14.0)
        events = [e["event"] for e in _lines(self.runtime_root / "jobs" / record["job_id"] / "events.jsonl")]
        self.assertEqual(events[-1], "usage_accounted")

    def test_a_stream_with_no_rate_limit_event_is_an_unknown_delta_charged_at_the_forecast(self) -> None:
        self.reading(10)
        record = self.ustep()
        self.assertEqual((record["usage"]["delta"], record["usage"]["charged"], record["usage"]["five_hour"]),
                         ("unknown", 8.0, None))
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry["charged"]), ("unknown", 8.0))
        # Excluded from the forecast mean: the default stands.
        self.assertEqual(usage.forecast(self.shared(), "claude", entry["role"], entry["model"], usage.Limits()),
                         (8.0, 1.0))

    def test_a_hold_returns_a_usage_hold_and_records_nothing(self) -> None:
        self.reading(80)
        result = self.ustep()
        self.assertIsInstance(result, job.UsageHold)
        self.assertEqual((result.window, result.waitable, result.resume_at), (FIVE, True, self.now + 3600))
        self.assert_no_record()
        self.assertEqual(test_job._read_lines(self.invocations), [], "no worker ran")
        self.assertEqual(self.shared()["reservations"], {})
        lock.acquire_lifecycle_lock(self.root).release()  # released on the way out

    def test_a_cap_dominates_a_simultaneous_window_hold(self) -> None:
        self.reading(90)
        result = self.ustep(limits=usage.Limits(run_cap_percent=5))
        self.assertIsInstance(result, job.UsageHold)
        self.assertEqual((result.window, result.waitable, result.resume_at), ("run_cap", False, None))
        self.assert_no_record()

    def test_a_launch_failure_after_admission_releases_not_started(self) -> None:
        with self.assertRaises(WorkerLaunchError):
            self._step(usage_gate=self.gate(), claude_bin=str(self.tmp_root / "no-such-claude"))
        record = self.record_on_disk()
        self.assertEqual(record["reconciliation_evidence"]["code"], job.WORKER_NOT_STARTED_CODE)
        self.assertEqual(record["usage"]["accounting"], "done")
        shared = self.shared()
        self.assertEqual((shared["reservations"], shared["lapsed"], shared["settled"], shared["ledger"]),
                         ({}, {}, {}, []))
        self.assertEqual(usage.run_spent(shared, "claude", "run-1"), 0.0)

    def test_a_record_that_is_never_published_releases_its_reservation(self) -> None:
        persist = job._persist

        def failing(runtime_root, job_id, record, *, event, details=None):
            if event == "planned":
                raise RuntimeError("injected before the PLANNED write")
            return persist(runtime_root, job_id, record, event=event, details=details)

        with unittest.mock.patch.object(job, "_persist", failing), self.assertRaises(RuntimeError):
            self.ustep()
        self.assert_no_record()
        shared = self.shared()
        self.assertEqual((shared["reservations"], shared["settled"], shared["ledger"]), ({}, {}, []))

    def test_without_a_gate_nothing_is_reserved_or_recorded(self) -> None:
        record = self._step(timeout=60)
        self.assertNotIn("usage", record)
        self.assertFalse((self.runtime_root / usage.USAGE_FILE).exists())


# ---------------------------------------------------------------------------
# Crash-safe replay, with the real `never_launched`
# ---------------------------------------------------------------------------


class ReplayTest(_UsageCase):

    def test_a_crash_after_the_terminal_write_and_before_complete_is_charged_once_by_resume(self) -> None:
        self.reading(10)
        with unittest.mock.patch.object(job, "_usage_account", side_effect=RuntimeError("crash")), \
                self.assertRaises(RuntimeError):
            self.ustep(self.stream_env(self.rate_limit(13)))
        record = self.record_on_disk()
        self.assertIn(record["status"], job.TERMINAL_STATUSES)
        self.assertEqual(record["usage"]["accounting"], "pending")
        [reported] = self.resume()
        self.assertEqual((reported["usage"]["accounting"], reported["usage"]["delta"], reported["usage"]["charged"]),
                         ("done", "measured", 3.0))
        self.resume()
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry["charged"]), ("measured", 3.0))
        self.assertEqual(usage.run_spent(self.shared(), "claude", "run-1"), 3.0)

    def test_a_crash_between_the_completed_write_and_the_terminal_write_is_measured_by_resume(self) -> None:
        self.reading(10)
        with unittest.mock.patch.object(job, "_observe_post_phase", side_effect=RuntimeError("crash")), \
                self.assertRaises(RuntimeError):
            self.ustep(self.stream_env(self.rate_limit(15)))
        record = self.record_on_disk()
        self.assertEqual((record["status"], record["usage"]["accounting"]), (job.STATUS_COMPLETED, "pending"))
        self.assertEqual(self.shared()["ledger"], [])
        [reported] = self.resume()
        self.assertIn(reported["status"], job.TERMINAL_STATUSES)
        self.assertTrue(reported["reconciled_this_call"])
        self.assertEqual((reported["usage"]["accounting"], reported["usage"]["delta"]), ("done", "measured"))
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry["charged"]), ("measured", 5.0))

    def _crash_before_done(self) -> str:
        """One step whose `accounting: "done"` write crashes after the
        shared-record write: returns the token."""
        self.reading(10)
        mark = job._mark_usage_done
        calls: list[int] = []

        def crashing(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("crash before done")
            return mark(*a, **k)

        with unittest.mock.patch.object(job, "_mark_usage_done", crashing), self.assertRaises(RuntimeError):
            self.ustep(self.stream_env(self.rate_limit(12)))
        record = self.record_on_disk()
        self.assertEqual(record["usage"]["accounting"], "pending")
        self.assertEqual(len(self.shared()["ledger"]), 1)
        return record["usage"]["token"]

    def test_a_crash_between_the_shared_write_and_done_then_resume_charges_nothing_more(self) -> None:
        token = self._crash_before_done()
        [reported] = self.resume()
        self.assertEqual(reported["usage"]["accounting"], "done")
        self.assertEqual(len(self.shared()["ledger"]), 1)
        self.assertEqual(usage.run_spent(self.shared(), "claude", "run-1"), 2.0)
        self.assertNotIn("usage_accounting_orphan", [e["event"] for e in self.events()])
        self.assertIn(token, self.shared()["settled"])

    def test_then_a_pruned_settled_entry_is_an_orphan_no_op_never_a_charge(self) -> None:
        token = self._crash_before_done()
        shared = self.shared()
        del shared["settled"][token]  # aged past its 90-day retention
        runtime.write_json(self.runtime_root, usage.USAGE_FILE, shared)
        [reported] = self.resume()
        self.assertEqual(reported["usage"]["accounting"], "done")
        self.assertEqual(len(self.shared()["ledger"]), 1)
        self.assertEqual(usage.run_spent(self.shared(), "claude", "run-1"), 2.0)
        [orphan] = [e for e in self.events() if e["event"] == "usage_accounting_orphan"]
        self.assertEqual(orphan["token"], token)

    def test_a_crash_during_replay_and_a_second_replay_charge_exactly_once(self) -> None:
        self.reading(10)
        with unittest.mock.patch.object(job, "_usage_account", side_effect=RuntimeError("crash")), \
                self.assertRaises(RuntimeError):
            self.ustep(self.stream_env(self.rate_limit(13)))
        with unittest.mock.patch.object(job, "_mark_usage_done", side_effect=RuntimeError("crash in the sweep")), \
                self.assertRaises(RuntimeError):
            job._settle_usage(self.runtime_root)
        self.assertEqual(len(self.shared()["ledger"]), 1)
        job._settle_usage(self.runtime_root)
        self.resume()
        self.assertEqual(len(self.shared()["ledger"]), 1)
        self.assertEqual(self.record_on_disk()["usage"]["accounting"], "done")
        self.assertEqual(usage.run_spent(self.shared(), "claude", "run-1"), 3.0)

    def test_a_crashed_planned_record_is_released_not_started_through_resume(self) -> None:
        persist = job._persist

        def crash(runtime_root, job_id, record, *, event, details=None):
            if event == "launched":
                raise KeyboardInterrupt  # the Controller dies before the LAUNCHED write
            return persist(runtime_root, job_id, record, event=event, details=details)

        with unittest.mock.patch.object(job, "_persist", crash), self.assertRaises(KeyboardInterrupt):
            self.ustep()
        self.assertEqual(self.record_on_disk()["status"], job.STATUS_PLANNED)
        self.assertEqual(len(self.shared()["reservations"]), 1)
        [reported] = self.resume()
        self.assertEqual(reported["status"], job.STATUS_INTERRUPTED)
        self.assertNotIn("expected_transition", reported)
        self.assertEqual(reported["usage"]["accounting"], "done")
        shared = self.shared()
        self.assertEqual((shared["reservations"], shared["lapsed"], shared["settled"], shared["ledger"]),
                         ({}, {}, {}, []))

    def test_a_reconciled_launched_record_is_charged_unknown_and_never_released(self) -> None:
        """The structural (i) the CP1 dispatch test cannot reach: a record
        that was ``LAUNCHED`` (it carries ``expected_transition``), crashed
        with no end figures and reconciled to ``INTERRUPTED`` by
        ``_reconcile_launched`` row 3, is charged at the forecast."""
        persist = job._persist

        def crash(runtime_root, job_id, record, *, event, details=None):
            if event == "worker_spawned":
                raise RuntimeError("the spawn flush fails")  # launch ends the group
            return persist(runtime_root, job_id, record, event=event, details=details)

        with unittest.mock.patch.object(job, "_persist", crash), self.assertRaises(RuntimeError):
            self.ustep()
        record = self.record_on_disk()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        self.assertIn("expected_transition", record)
        [reported] = self.resume()
        self.assertEqual(reported["status"], job.STATUS_INTERRUPTED)
        self.assertIn("expected_transition", reported)
        self.assertFalse(job.never_launched(reported))
        self.assertEqual(reported["usage"]["accounting"], "done")
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry["charged"]), ("unknown", 8.0))
        self.assertEqual(usage.run_spent(self.shared(), "claude", "run-1"), 8.0)

    def _not_started_then_crash(self) -> Path:
        """A ``WorkerNotStarted`` record whose in-process release a crash
        prevented: its reservation is still in the shared record."""
        with unittest.mock.patch.object(job, "_usage_account", side_effect=RuntimeError("crash")), \
                self.assertRaises(WorkerLaunchError):
            self._step(usage_gate=self.gate(), claude_bin=str(self.tmp_root / "no-such-claude"))
        self.assertEqual(len(self.shared()["reservations"]), 1)
        [path] = sorted((self.runtime_root / "jobs").glob("*.json"))
        return path

    def test_the_sweep_releases_only_structurally_never_launched_failures(self) -> None:
        cases = (
            ("WorkerNotStarted", None, "not_started"),
            ("decision_stale_at_launch", None, "not_started"),
            ("SomethingElse", None, "unknown"),
            # No `expected_transition`, but neither INTERRUPTED nor a (ii) FAILED.
            ("WorkerNotStarted", job.STATUS_INCOMPLETE, "unknown"),
        )
        for code, status, expected in cases:
            with self.subTest(code=code, status=status):
                before = usage.run_spent(self.shared(), "claude", "run-1") \
                    if (self.runtime_root / usage.USAGE_FILE).exists() else 0.0
                ledger_before = len(self.shared()["ledger"]) if (self.runtime_root / usage.USAGE_FILE).exists() else 0
                path = self._not_started_then_crash()
                record = json.loads(path.read_text())
                record["reconciliation_evidence"]["code"] = code
                if status is not None:
                    record["status"] = status
                    del record["expected_transition"]
                runtime.write_json(self.runtime_root, f"jobs/{path.name}", record)
                [settled] = job._settle_usage(self.runtime_root)
                self.assertEqual(settled.action, expected)
                after = usage.run_spent(self.shared(), "claude", "run-1")
                self.assertEqual(after - before, 0.0 if expected == "not_started" else 8.0)
                self.assertEqual(len(self.shared()["ledger"]) - ledger_before, 0 if expected == "not_started" else 1)
                self.assertEqual(json.loads(path.read_text())["usage"]["accounting"], "done")
                path.unlink()

    def test_a_bound_reservation_with_no_record_is_released_only_once_lapsed(self) -> None:
        admission = usage.admit(self.runtime_root, provider="claude", role="r", model="m",
                                repository=str(self.root), run_id="run-1", limits=usage.Limits(), now=self.now)
        usage.bind_job(self.runtime_root, admission.token, "20260101T000000Z-deadbeef", now=self.now)
        self.assertEqual(job._settle_usage(self.runtime_root), [])
        self.assertIn(admission.token, self.shared()["reservations"])
        self.now += usage.Limits().reservation_seconds + 1
        [settled] = job._settle_usage(self.runtime_root)
        self.assertEqual(settled.action, "not_started")
        shared = self.shared()
        self.assertEqual((shared["reservations"], shared["lapsed"], shared["settled"], shared["ledger"]),
                         ({}, {}, {}, []))


# ---------------------------------------------------------------------------
# Renewal through the real supervision path
# ---------------------------------------------------------------------------


class _Stepper:
    """The renewal ticker's injected ``wait``: one renewal per ``step()``,
    and ``True`` (stop) once ``done`` is set."""

    def __init__(self) -> None:
        self.allowed = threading.Semaphore(0)
        self.done = threading.Event()
        self.waits = 0

    def __call__(self, seconds: float) -> bool:
        self.waits += 1
        while not self.done.is_set():
            if self.allowed.acquire(timeout=0.02):
                return False
        return True


class RenewalTest(_UsageCase):

    def setUp(self) -> None:
        super().setUp()
        self.stepper = _Stepper()
        self.enterContext(unittest.mock.patch.object(job, "usage_ticker_wait", self.stepper))

    def _tick(self) -> None:
        """One renewal at the current clock, waited for."""
        before = self._renewed_at()
        self.stepper.allowed.release()
        self.assertTrue(process_fixtures.wait_until(lambda: self._renewed_at() != before, timeout=10),
                        "the ticker never renewed")

    def _renewed_at(self):
        shared = self.shared()
        return [r["renewed_at"] for r in shared["reservations"].values()]

    def _start(self, env: dict, limits: usage.Limits | None = None) -> tuple[threading.Thread, dict]:
        outcome: dict = {}

        def run() -> None:
            try:
                with unittest.mock.patch.dict("os.environ", env):
                    outcome["record"] = self._step(usage_gate=self.gate(limits), timeout=120)
            except BaseException as exc:  # noqa: BLE001 -- surfaced by the test
                outcome["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 30)
        self.assertTrue(process_fixtures.wait_until(
            lambda: bool(self._records()) and "worker_process" in self._records()[0], timeout=20),
            "the worker was never recorded")
        return thread, outcome

    def _finish(self, thread: threading.Thread, outcome: dict) -> dict:
        self.stepper.done.set()
        self.release.touch()
        thread.join(60)
        self.assertNotIn("error", outcome)
        return outcome["record"]

    def _concurrent(self, limits: usage.Limits | None = None) -> usage.Admission:
        return usage.admit(self.runtime_root, provider="claude", role="other", model=None, repository="/other",
                           run_id="run-2", limits=limits or usage.Limits(), now=self.now)

    def test_a_long_running_turn_keeps_its_reservation_and_a_concurrent_admission_is_held(self) -> None:
        self.reading(75, five_in=5 * 3600)
        thread, outcome = self._start({"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release)})
        for _ in range(3):  # 9000 s in all: past the 7200 s lease, no state change
            self.now += 3000
            self._tick()
        self.assertEqual(len(self.shared()["reservations"]), 1)
        self.assertEqual(self.shared()["lapsed"], {})
        held = self._concurrent()
        self.assertIsNotNone(held.hold)
        self.assertEqual(held.hold.window, FIVE)
        record = self._finish(thread, outcome)
        self.assertEqual(record["usage"]["accounting"], "done")
        self.assertEqual(len(self.shared()["ledger"]), 1)

    def test_a_stalled_ticker_lets_the_reservation_lapse(self) -> None:
        self.reading(75, five_in=5 * 3600)
        thread, outcome = self._start({"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release)})
        self.now += 9000
        admitted = self._concurrent()
        self.assertIsNone(admitted.hold, "with no renewal the lease lapsed and freed the headroom")
        self.assertEqual(len(self.shared()["lapsed"]), 1)
        record = self._finish(thread, outcome)
        # Accounted from the lapsed entry, exactly once.
        self.assertEqual(record["usage"]["accounting"], "done")
        self.assertEqual([e["id"] for e in self.shared()["ledger"]], [record["usage"]["token"]])

    def test_a_five_hour_reset_while_the_worker_runs_rolls_over_and_completes_after_reset(self) -> None:
        limits = usage.Limits(default_job_weekly_percent=3)
        self.reading(50, five_in=100, weekly=90)
        # The worker's stream ends in the new five-hour window, at 5%.
        new_window = self.now + 100 + 5 * 3600
        events = [fake_claude.rate_limit(5, new_window, 92, self.now + 7 * DAY)]
        env = {"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release), **self.stream_env(*events)}
        thread, outcome = self._start(env, limits)
        self.now += 200  # past the five-hour reset
        self._tick()
        [reservation] = self.shared()["reservations"].values()
        self.assertEqual(reservation["rolled_over"], 1)
        self.assertEqual(reservation[FIVE]["amount"], 8.0)
        self.assertEqual(reservation[WEEKLY]["window_resets_at"], self.now - 200 + 7 * DAY)
        held = self._concurrent(limits)
        self.assertEqual(held.hold.window, WEEKLY)  # 90 + 3 reserved + 3 >= 95
        record = self._finish(thread, outcome)
        self.assertEqual((record["usage"]["delta"], record["usage"]["charged"]), ("after_reset", 8.0))


# ---------------------------------------------------------------------------
# `_reattach` (R7): a recovered job is accounted like a live one
# ---------------------------------------------------------------------------


class ReattachAccountingTest(unittest.TestCase):

    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "runtime"
        self.job_dir = self.runtime_root / "jobs" / "j-1"
        self.job_dir.mkdir(parents=True)
        self.now = time.time() - 600
        self.enterContext(unittest.mock.patch.object(job, "usage_now", lambda: self.now))
        self.streams = {"stdout_path": str(self.job_dir / "worker.stdout"),
                        "stderr_path": str(self.job_dir / "worker.stderr"),
                        "events_path": str(self.job_dir / "events.jsonl")}
        (self.job_dir / "worker.stderr").write_text("")
        self.window_resets_at = self.now + 4 * 3600

    def _admit(self) -> str:
        usage.record_reading(self.runtime_root, [usage.make_reading("claude", FIVE, 10, self.window_resets_at,
                                                                     self.now, "test")])
        admission = usage.admit(self.runtime_root, provider="claude", role="planner", model="opus",
                                repository="/repo", run_id="run-1", limits=usage.Limits(), now=self.now)
        usage.bind_job(self.runtime_root, admission.token, "j-1", now=self.now)
        return admission.token

    def _record(self, token: str) -> dict:
        shared = usage.load_record(self.runtime_root)
        block = (job._usage_start_block(self.runtime_root, token) if token in shared["reservations"]
                 else {"token": token, "accounting": "open", "run_id": "run-1", "repository": "/repo"})
        record = {"schema_version": 1, "job_id": "j-1", "status": job.STATUS_LAUNCHED,
                  "created_at": "2026-01-01T00:00:00Z", "worker_process": {"pid": 1},
                  "worker_streams": self.streams, "worker_state": {"state": worker.ENDED},
                  "expected_transition": {"from": "PLANNING", "to_any_of": []}, "usage": block}
        runtime.write_json(self.runtime_root, "jobs/j-1.json", record)
        return record

    def _reattach(self, record: dict, *, alive: bool, end: float | None = 13) -> dict:
        events = [fake_claude.rate_limit(end, self.window_resets_at)] if end is not None else []
        Path(self.streams["stdout_path"]).write_text(
            fake_claude.stream_text([*events, fake_claude.result_event()]))
        result = worker.WorkerResult(
            outcome="SUCCESS", returncode=None, session_id="s", is_error=False, subtype="success",
            terminal_reason=None, stop_reason=None, result="ok", num_turns=1, permission_denials=[],
            total_cost_usd=0.0, duration_ms=1, stdout="", stderr="", raw_json={})
        with unittest.mock.patch.object(job.worker, "reattach", return_value=result):
            job._reattach(self.runtime_root, Path("/repo"), record, self.streams, classify=True, alive=alive)
        return json.loads((self.runtime_root / "jobs" / "j-1.json").read_text())

    def test_a_live_job_whose_reservation_lapsed_is_revived_and_measured(self) -> None:
        token = self._admit()
        record = self._record(token)
        self.now += usage.Limits().reservation_seconds + 60  # a Controller crash longer than the lease
        self.assertIn(token, usage.snapshot(self.runtime_root, now=self.now)["lapsed"])
        written = self._reattach(record, alive=True)
        self.assertEqual(written["status"], job.STATUS_COMPLETED)
        self.assertEqual((written["usage"]["accounting"], written["usage"]["delta"]), ("done", "measured"))
        shared = usage.load_record(self.runtime_root)
        self.assertEqual([e["id"] for e in shared["ledger"]], [token])
        self.assertEqual((shared["reservations"], shared["lapsed"]), ({}, {}))
        self.assertIn(token, shared["settled"])

    def test_a_lapsed_reservation_is_charged_from_the_lapsed_entry_with_the_blocks_end_figures(self) -> None:
        token = self._admit()
        record = self._record(token)
        self.now += usage.Limits().reservation_seconds + 60
        written = self._reattach(record, alive=False)
        self.assertEqual(written["usage"]["five_hour_end"], 13.0)
        [entry] = usage.load_record(self.runtime_root)["ledger"]
        self.assertEqual((entry["id"], entry["delta"], entry["charged"]), (token, "measured", 3.0))
        # Then phase 2 spreads the record: the terminal write is already done.
        self.assertEqual(job._settle_usage(self.runtime_root), [])

    def test_a_job_whose_reservation_is_gone_is_a_recorded_no_op(self) -> None:
        record = self._record("f" * 32)
        runtime.write_json(self.runtime_root, usage.USAGE_FILE, usage._empty_record())
        written = self._reattach(record, alive=True)
        self.assertEqual(written["usage"]["accounting"], "done")
        shared = usage.load_record(self.runtime_root)
        self.assertEqual((shared["ledger"], shared["spend"]["run"]), ([], {}))
        self.assertIn("usage_accounting_orphan", [e["event"] for e in _lines(self.runtime_root / usage.EVENTS_FILE)])

    def test_an_abandoned_then_pruned_token_is_never_charged_twice(self) -> None:
        token = self._admit()
        record = self._record(token)
        self.now += usage.Limits().reservation_seconds + usage.ABANDON_AFTER_SECONDS + 60
        shared = usage.snapshot(self.runtime_root, now=self.now)  # lapsed, then abandoned at its forecast
        self.assertEqual(shared["ledger"][-1]["delta"], "abandoned")
        del shared["settled"][token]  # the settled entry pruned past 90 days, run spend retained
        runtime.write_json(self.runtime_root, usage.USAGE_FILE, shared)
        spent = usage.run_spent(shared, "claude", "run-1")
        repo_spent = usage.repository_spent(shared, "claude", "/repo", self.now)
        self.assertEqual(spent, 8.0)
        written = self._reattach(record, alive=True)
        self.assertEqual(written["usage"]["accounting"], "done")
        after = usage.load_record(self.runtime_root)
        self.assertEqual(usage.run_spent(after, "claude", "run-1"), spent)
        self.assertEqual(usage.repository_spent(after, "claude", "/repo", self.now), repo_spent)
        self.assertEqual([e["delta"] for e in after["ledger"]], ["abandoned"])
        # The retained run total still holds its cap for continuing work.
        held = usage.admit(self.runtime_root, provider="claude", role="planner", model="opus", repository="/repo",
                           run_id="run-1", limits=usage.Limits(run_cap_percent=10), now=self.now)
        self.assertEqual(held.hold.window, "run_cap")
        # And the resume dispatch over the terminal record charges nothing.
        terminal = {**written, "status": job.STATUS_FINISHED,
                    "usage": {**written["usage"], "accounting": "pending"}}
        runtime.write_json(self.runtime_root, "jobs/j-1.json", terminal)
        [dispatched] = job._usage_dispatch(self.runtime_root, [terminal])
        self.assertEqual(dispatched["usage"]["accounting"], "done")
        self.assertEqual(usage.run_spent(usage.load_record(self.runtime_root), "claude", "run-1"), spent)


class NeverLaunchedPredicateTest(unittest.TestCase):
    """``job.never_launched`` on what the code writes (D6)."""

    def test_the_structural_cases(self) -> None:
        stale = {"status": job.STATUS_FAILED, "expected_transition": {},
                 "reconciliation_evidence": {"code": "decision_stale_at_launch"}}
        cases = (
            ({"status": job.STATUS_INTERRUPTED}, True),
            ({"status": job.STATUS_INTERRUPTED, "expected_transition": {}}, False),
            ({"status": job.STATUS_FAILED, "expected_transition": {},
              "reconciliation_evidence": {"code": job.WORKER_NOT_STARTED_CODE}}, True),
            (stale, True),
            ({"status": job.STATUS_FAILED, "reconciliation_evidence": {"code": "TransitionNotObservedError"}}, False),
            ({"status": job.STATUS_FAILED, "worker_outcome": "worker_not_started"}, False),
            ({"status": job.STATUS_FINISHED}, False),
        )
        for record, expected in cases:
            with self.subTest(record=record):
                self.assertIs(job.never_launched(record), expected)


# ---------------------------------------------------------------------------
# `cli`: the gate builder, the run loop's pause and exit 17
# ---------------------------------------------------------------------------


class _CliUsageCase(_UsageCase):
    """``cmd_run``/``cmd_step`` against the real target and the fake
    worker, with a real origin checkout (``handoff.detect``), the settings
    resolved as ``main`` resolves them, and an injected sleeper that moves
    the usage clock."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(settings.process_defaults({}))
        self.addCleanup(setattr, cli, "_open_run", None)
        self.origin = fixtures.build_checkout(self.tmp_root / "origin", generation=1)
        self.ident = ControllerIdentity(
            generation=1, source_root=self.origin, origin_source_root=self.origin,
            source_kind=SOURCE_KIND_COMMIT, source_commit=fixtures.current_head(self.origin),
            tree_digest="d" * 64, generation_source="head", pinned_at="2024-01-01T00:00:00Z",
            version=fixtures.CONTROLLER_VERSION)
        import controller.identity as identity_module
        self.enterContext(unittest.mock.patch.object(identity_module, "pin", lambda: self.ident))
        self.enterContext(unittest.mock.patch.object(identity_module, "current", lambda: self.ident))
        self.enterContext(unittest.mock.patch.object(cli, "_inspect_target", lambda args: self.managed_repo))
        self.settings_path = self.tmp_root / "config" / "settings.json"
        self.sleeps: list[float] = []
        self.on_sleep = None  # called once, at the first slice of a pause
        self.enterContext(unittest.mock.patch.object(cli, "_usage_sleep", self._sleep))

    def _sleep(self, seconds: float) -> None:
        # No lock is held during a pause: the lifecycle lock is free.
        lock.acquire_lifecycle_lock(self.root).release()
        self.assert_no_record()
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            hook, self.on_sleep = self.on_sleep, None
            hook()
        self.now += seconds

    def args(self, usage_settings: dict | None = None, **attrs):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps({"usage": {"resume_grace_seconds": 1, **(usage_settings or {})}}))
        args = argparse.Namespace(
            repo=str(self.root), work_item=None, json=False, workflow_manager=None, permission_mode=None,
            timeout=60, claude_binary=str(test_job.FAKE_CLAUDE), max_steps=None, pause_file=None,
            settings=str(self.settings_path), follow=False, usage_cap=None)
        for name, value in attrs.items():
            setattr(args, name, value)
        cli._apply_settings(args, write=True)
        return args

    def run_cmd(self, args) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.cmd_run(args, self.runtime_root, self.ident)
        return code, stderr.getvalue()

    def run_events(self) -> list[dict]:
        return _lines(self.runtime_root / "runs" / cli._open_run.run_id / "events.jsonl")

    def names(self) -> list[str]:
        return [e["event"] for e in self.run_events()]


class RunLoopTest(_CliUsageCase):

    def test_a_run_under_the_threshold_is_untouched_and_accounted(self) -> None:
        self.reading(10)
        code, _stderr = self.run_cmd(self.args(max_steps=1))
        self.assertNotEqual(code, cli.EXIT_USAGE_PAUSED)
        record = self.record_on_disk()
        self.assertEqual(record["usage"]["accounting"], "done")
        self.assertEqual(record["usage"]["run_id"], cli._open_run.run_id)
        self.assertEqual(record["controller_settings"]["values"]["usage.resume_grace_seconds"], 1)
        self.assertEqual(len(self.shared()["ledger"]), 1)
        self.assertNotIn("usage_paused", self.names())
        self.assertEqual(self.sleeps, [])

    def test_a_pause_waits_with_no_lock_then_resumes_and_still_runs_its_one_step(self) -> None:
        self.reading(80, five_in=5)
        start = self.now
        code, _stderr = self.run_cmd(self.args(max_steps=1))
        # `--max-steps 1` that paused once still ran its step.
        self.assertNotIn(code, (cli.EXIT_USAGE_PAUSED, cli.EXIT_MAX_STEPS))
        self.assertEqual(len(self._records()), 1)
        names = self.names()
        self.assertEqual(names.count("usage_paused"), 1)
        self.assertLess(names.index("usage_paused"), names.index("usage_resumed"))
        self.assertLess(names.index("usage_resumed"), names.index("job_started"))
        paused = next(e for e in self.run_events() if e["event"] == "usage_paused")
        self.assertEqual((paused["window"], paused["waiting_until"], paused["preflight_events"]),
                         (FIVE, start + 5 + 1, []))
        self.assertEqual(sum(self.sleeps), 6)
        self.assertTrue(all(s <= cli.USAGE_WAIT_SLICE_SECONDS for s in self.sleeps))
        # The same events in the shared event file.
        self.assertEqual([e["event"] for e in self.events() if e["event"].startswith("usage_")],
                         ["usage_paused", "usage_resumed"])

    def test_a_second_hold_on_the_same_step_shares_one_wait_budget(self) -> None:
        self.reading(80, five_in=5)
        # Woken, the next window is already at 90%: held again for 10 s more.
        self.on_sleep = lambda: self.reading(90, five_in=15)
        code, _stderr = self.run_cmd(self.args({"max_wait_seconds": 60}, max_steps=1))
        self.assertNotEqual(code, cli.EXIT_USAGE_PAUSED)
        names = self.names()
        self.assertEqual(names.count("usage_paused"), 2)
        self.assertEqual(names.count("usage_resumed"), 1)
        self.assertGreater(names.index("usage_resumed"), max(i for i, n in enumerate(names) if n == "usage_paused"))
        self.assertEqual(len(self._records()), 1)

    def test_a_second_hold_beyond_the_budget_stops_with_17(self) -> None:
        self.reading(80, five_in=5)
        self.on_sleep = lambda: self.reading(90, five_in=15)
        code, stderr = self.run_cmd(self.args({"max_wait_seconds": 12}, max_steps=1))
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("usage.max_wait_seconds, 12 s", stderr)
        self.assertIn("a timed pause", stderr)
        self.assert_no_record()
        self.assertNotIn("usage_resumed", self.names())

    def test_a_wait_beyond_max_wait_seconds_stops_with_17_and_starts_nothing(self) -> None:
        self.reading(80, five_in=3600)
        code, stderr = self.run_cmd(self.args({"max_wait_seconds": 60}))
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assert_no_record()
        self.assertEqual(self.sleeps, [])
        self.assertIn("run again after the resume time", stderr)
        self.assertIn(f"(epoch {int(self.now + 3600)})", stderr)
        [paused] = [e for e in self.run_events() if e["event"] == "usage_paused"]
        self.assertIsNone(paused["waiting_until"])

    def test_a_handoff_installed_during_the_wait_exits_50_with_no_job(self) -> None:
        self.reading(80, five_in=5)

        def bump() -> None:
            (self.origin / "controller" / "GENERATION.json").write_text(
                json.dumps({"schema_version": 1, "generation": 2}) + "\n")
            fixtures.commit_all(self.origin, "bump generation")

        self.on_sleep = bump
        code, _stderr = self.run_cmd(self.args())
        self.assertEqual(code, cli.EXIT_HANDOFF_PENDING)
        self.assert_no_record()
        self.assertIn("handoff_detected", self.names())
        self.assertNotIn("usage_resumed", self.names())

    def test_a_phase_advanced_by_hand_during_the_pause_is_decided_afresh(self) -> None:
        self.reading(80, five_in=5)

        def advance() -> None:
            state = json.loads((self.root / test_job.STATE_REL).read_text())
            state["work_items"]["wi-1"]["phase"] = "IMPLEMENTING"
            fixtures.write_workflow_state(self.root, state)

        self.on_sleep = advance
        code, _stderr = self.run_cmd(self.args())
        self.assertEqual(code, cli.EXIT_GATE)
        self.assertEqual(self.record_on_disk()["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("usage_resumed", self.names())

    def test_a_new_automatic_decision_after_the_pause_launches_after_usage_resumed(self) -> None:
        self.reading(80, five_in=5)

        def switch() -> None:
            state = json.loads((self.root / test_job.STATE_REL).read_text())
            state["work_items"]["wi-2"] = {**state["work_items"]["wi-1"], "work_item_id": "wi-2"}
            state["active_work_item_id"] = "wi-2"
            fixtures.write_workflow_state(self.root, state)

        self.on_sleep = switch
        self.run_cmd(self.args(max_steps=1))
        record = self.record_on_disk()
        self.assertEqual(record["selected_action"]["command"], "/milestone-plan wi-2")
        self.assertLess(self.names().index("usage_resumed"), self.names().index("job_started"))

    def test_ctrl_c_during_a_pause_leaves_no_record(self) -> None:
        self.reading(80, five_in=5)

        def interrupt() -> None:
            raise KeyboardInterrupt

        self.on_sleep = interrupt
        args = self.args()
        stderr = io.StringIO()
        with unittest.mock.patch.object(cli, "_dispatch", lambda a, argv: cli.cmd_run(args, self.runtime_root,
                                                                                      self.ident)), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["run", str(self.root)]), cli.SIGINT_EXIT_STATUS)
        self.assertIn("interrupted while paused for the usage budget", stderr.getvalue())
        self.assert_no_record()


class ExitSeventeenTest(_CliUsageCase):

    def test_a_run_cap_stops_with_17_and_says_no_reset_lifts_it(self) -> None:
        code, stderr = self.run_cmd(self.args(usage_cap=5))
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assert_no_record()
        self.assertIn("no reset will lift this; raise usage.run_cap_percent or start a new run", stderr)
        self.assertEqual(self.sleeps, [])

    def test_a_repository_cap_names_the_window_reset_and_that_a_new_run_does_not_clear_it(self) -> None:
        self.reading(10, five_in=3600)
        code, stderr = self.run_cmd(self.args({"repository_cap_percent": 5}))
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("a new run does not clear this repository's spending", stderr)
        self.assertIn(f"run again after the window resets at", stderr)
        self.assertIn(f"(epoch {int(self.now + 3600)})", stderr)
        self.assertIn("usage.repository_cap_percent", stderr)

    def test_outstanding_work_is_named_when_it_holds_the_cap(self) -> None:
        usage.admit(self.runtime_root, provider="claude", role="other", model=None, repository=str(self.root),
                    run_id="other-run", limits=usage.Limits(), now=self.now)
        code, stderr = self.run_cmd(self.args({"repository_cap_percent": 12}))
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("outstanding work", stderr)

    def test_step_never_waits_and_exits_17(self) -> None:
        self.reading(80, five_in=5)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.cmd_step(self.args(), self.runtime_root, self.ident)
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assert_no_record()
        self.assertEqual(self.sleeps, [])
        self.assertIn("a timed pause: run again after the resume time", stderr.getvalue())
        self.assertEqual([e["event"] for e in self.run_events() if e["event"].startswith("usage_")],
                         ["usage_paused"])

    def test_usage_enabled_false_does_nothing(self) -> None:
        self.reading(99, five_in=5)
        args = self.args({"enabled": False}, max_steps=1)
        self.assertIsNone(cli._usage_gate(self.runtime_root, self.managed_repo, None, cli._effective(args), None))
        self.run_cmd(args)
        record = self.record_on_disk()
        self.assertNotIn("usage", record)
        self.assertEqual(self.shared()["reservations"], {})
        self.assertEqual(self.shared()["ledger"], [])
        self.assertNotIn("usage_paused", self.names())


if __name__ == "__main__":
    unittest.main()
