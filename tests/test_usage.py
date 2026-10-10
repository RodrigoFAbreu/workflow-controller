"""Tests for ``controller.usage`` (``workflow-controller-usage-budget`` CP1):
the Claude and Codex readers, the deterministic merge, the shared record's
reservations (rollover, renewal, lapse, abandonment), the ledger, spend and
outstanding liabilities, atomic admission, the forecast, the pure decision and
the crash-safe replay sweep. Every path is a temporary directory; no test
reads ``~/.codex`` or the operator's runtime root.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

import tests
from controller import runtime, usage
from controller.errors import UsageRecordError
from controller.usage import FIVE_HOUR, WEEKLY, GO, Hold, Limits

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "golden"
CONTRACT = REPO / "tests" / "harness_contract"

NOW = 1_790_000_000.0
H = 3600.0
FIVE_RESETS = NOW + 3 * H
WEEK_RESETS = NOW + 3 * 86400


def reading(window: str, percent: float, resets_at: float, observed_at: float = NOW, provider: str = "claude") -> dict:
    return usage.make_reading(provider, window, percent, resets_at, observed_at, "test")


def seed(root: Path, five: float = 0.0, weekly: float = 0.0, provider: str = "claude", now: float = NOW) -> None:
    usage.record_reading(root, [reading(FIVE_HOUR, five, FIVE_RESETS, now, provider),
                                reading(WEEKLY, weekly, WEEK_RESETS, now, provider)])


def admit(root: Path, *, now: float = NOW, limits: Limits | None = None, provider: str = "claude", role: str = "implement",
          model: str = "opus", repository: str | None = "/repo", run_id: str | None = "run-1", readings=()):
    return usage.admit(root, provider=provider, role=role, model=model, repository=repository, run_id=run_id,
                       limits=limits or Limits(provider=provider), now=now, readings=readings)


def end(five: float, weekly: float, now: float = NOW + 600, five_resets: float = FIVE_RESETS,
        week_resets: float = WEEK_RESETS) -> dict:
    return {FIVE_HOUR: reading(FIVE_HOUR, five, five_resets, now), WEEKLY: reading(WEEKLY, weekly, week_resets, now)}


class TempRootCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def record(self) -> dict:
        return usage.load_record(self.root)


def stream(*events: dict) -> bytes:
    return b"".join(json.dumps(e).encode() + b"\n" for e in events)


def rate_event(five: float | None = None, weekly: float | None = None, status: str = "allowed",
               kind: str = "five_hour", five_resets: int = 1000, week_resets: int = 2000) -> dict:
    windows = {}
    if five is not None:
        windows["five_hour"] = {"utilization": five, "resetsAt": five_resets}
    if weekly is not None:
        windows["seven_day"] = {"utilization": weekly, "resetsAt": week_resets}
    return {"type": "rate_limit_event", "rate_limit_info": {"status": status, "rateLimitType": kind,
                                                            "unifiedWindows": windows}}


class ClaudeReaderTest(unittest.TestCase):
    def test_real_captured_streams(self) -> None:
        got = usage.read_claude_stream(GOLDEN / "claude_stream_json_2.1.281.jsonl")
        self.assertEqual([(r["window"], r["percent"], r["resets_at"]) for r in got],
                         [(FIVE_HOUR, 12.0, 1790256000), (WEEKLY, 2.0, 1790823600)])
        got = usage.read_claude_stream(CONTRACT / "job_5d4a976a_implementing.jsonl")
        self.assertEqual({r["window"] for r in got}, {FIVE_HOUR, WEEKLY})
        for r in got:
            self.assertEqual(r["provider"], "claude")
            self.assertTrue(0 <= r["percent"] <= 100)

    def test_rejected_golden_reads_100_for_the_named_window(self) -> None:
        got = {r["window"]: r for r in usage.read_claude_stream(GOLDEN / "claude_stream_usage_limit_rejected.jsonl")}
        self.assertEqual(got[FIVE_HOUR]["percent"], 100.0)
        self.assertEqual(got[FIVE_HOUR]["resets_at"], 1790287800)
        self.assertEqual(got[WEEKLY]["percent"], 14.0)

    def test_rejected_reads_from_structured_fields_only(self) -> None:
        event = rate_event(five=0.3, status="allowed", kind="five_hour")
        event["note"] = "You've hit your session limit"
        event["rate_limit_info"]["note"] = "rejected"
        self.assertEqual(usage.read_claude_stream(stream(event))[0]["percent"], 30.0)

    def test_seven_day_rejection_holds_only_the_weekly_window(self) -> None:
        got = {r["window"]: r for r in usage.read_claude_stream(
            stream(rate_event(five=0.2, weekly=0.5, status="rejected", kind="seven_day")))}
        self.assertEqual(got[WEEKLY]["percent"], 100.0)
        self.assertEqual(got[FIVE_HOUR]["percent"], 20.0)

    def test_five_hour_rejection_holds_only_the_five_hour_window(self) -> None:
        got = {r["window"]: r for r in usage.read_claude_stream(
            stream(rate_event(five=0.2, weekly=0.5, status="rejected", kind="five_hour")))}
        self.assertEqual(got[FIVE_HOUR]["percent"], 100.0)
        self.assertEqual(got[WEEKLY]["percent"], 50.0)

    def test_an_event_naming_neither_window_leaves_both_to_utilization(self) -> None:
        got = {r["window"]: r for r in usage.read_claude_stream(
            stream(rate_event(five=0.2, weekly=0.5, status="rejected", kind="seven_day_opus")))}
        self.assertEqual((got[FIVE_HOUR]["percent"], got[WEEKLY]["percent"]), (20.0, 50.0))

    def test_last_event_wins_and_a_partial_last_line_is_skipped(self) -> None:
        data = stream(rate_event(five=0.1), rate_event(five=0.4)) + b'{"type":"rate_limit_ev'
        self.assertEqual(usage.read_claude_stream(data)[0]["percent"], 40.0)
        first, last = usage.read_claude_stream_span(data)
        self.assertEqual((first[FIVE_HOUR]["percent"], last[FIVE_HOUR]["percent"]), (10.0, 40.0))

    def test_no_event_is_no_reading(self) -> None:
        self.assertEqual(usage.read_claude_stream(b'{"type":"result"}\n'), [])

    def test_observed_at_is_the_files_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker.stdout"
            path.write_bytes(stream(rate_event(five=0.1)))
            import os
            os.utime(path, (NOW, NOW))
            self.assertEqual(usage.read_claude_stream(path)[0]["observed_at"], NOW)

    def test_an_expired_window_is_zero_percent(self) -> None:
        r = reading(FIVE_HOUR, 90, NOW - 1)
        self.assertEqual(usage.window_percent(r, NOW), 0.0)
        self.assertEqual(usage.window_percent(reading(FIVE_HOUR, 90, NOW + 1), NOW), 90)
        self.assertIsNone(usage.window_percent(None, NOW))


class CodexReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def rollout(self, name: str, lines: list, mtime: float) -> Path:
        path = self.home / "sessions" / "2026" / "10" / "09" / f"rollout-{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(l if isinstance(l, str) else json.dumps(l) + "\n" for l in lines))
        import os
        os.utime(path, (mtime, mtime))
        return path

    @staticmethod
    def limits(stamp: str, primary: float, secondary: float, p_resets: int = 1791585389,
               s_resets: int = 1792024074) -> dict:
        return {"timestamp": stamp, "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": primary, "window_minutes": 300, "resets_at": p_resets},
            "secondary": {"used_percent": secondary, "window_minutes": 10080, "resets_at": s_resets}}}}

    def test_primary_and_secondary_by_window_minutes(self) -> None:
        self.rollout("a", [self.limits("2026-10-09T21:50:53.039Z", 36.0, 17.0)], 100)
        got = {r["window"]: r for r in usage.read_codex_home(self.home)}
        self.assertEqual((got[FIVE_HOUR]["percent"], got[WEEKLY]["percent"]), (36.0, 17.0))
        self.assertEqual(got[FIVE_HOUR]["provider"], "codex")
        self.assertEqual(got[FIVE_HOUR]["observed_at"], 1791582653.039)

    def test_windows_are_told_apart_by_minutes_not_names(self) -> None:
        swapped = {"timestamp": "2026-10-09T21:50:53Z", "payload": {"rate_limits": {
            "primary": {"used_percent": 5.0, "window_minutes": 10080, "resets_at": 1792024074},
            "secondary": {"used_percent": 9.0, "window_minutes": 300, "resets_at": 1791585389}}}}
        self.rollout("a", [swapped], 100)
        got = {r["window"]: r["percent"] for r in usage.read_codex_home(self.home)}
        self.assertEqual(got, {FIVE_HOUR: 9.0, WEEKLY: 5.0})

    def test_partial_last_line_no_rate_limits_and_empty_home(self) -> None:
        self.assertEqual(usage.read_codex_home(self.home), [])
        self.rollout("a", [self.limits("2026-10-09T21:00:00Z", 10.0, 3.0), {"timestamp": "2026-10-09T22:00:00Z"},
                           '{"timestamp": "2026-10-09T23:00:00Z", "payload": {"rate_li'], 100)
        self.assertEqual({r["window"]: r["percent"] for r in usage.read_codex_home(self.home)},
                         {FIVE_HOUR: 10.0, WEEKLY: 3.0})

    def test_a_file_with_no_rate_limits_is_no_reading(self) -> None:
        self.rollout("a", [{"timestamp": "2026-10-09T22:00:00Z", "payload": {"type": "message"}}], 100)
        self.assertEqual(usage.read_codex_home(self.home), [])

    def test_an_older_file_holding_the_fresher_timestamp_wins(self) -> None:
        self.rollout("old", [self.limits("2026-10-09T23:00:00Z", 50.0, 20.0)], 100)
        self.rollout("new", [self.limits("2026-10-09T21:00:00Z", 30.0, 10.0)], 200)
        got = {r["window"]: r["percent"] for r in usage.read_codex_home(self.home)}
        self.assertEqual(got, {FIVE_HOUR: 50.0, WEEKLY: 20.0})

    def test_only_the_newest_three_files_are_scanned(self) -> None:
        self.rollout("oldest", [self.limits("2026-10-09T23:59:00Z", 99.0, 99.0)], 1)
        for i in range(3):
            self.rollout(f"n{i}", [self.limits("2026-10-09T20:00:00Z", 10.0 + i, 5.0)], 100 + i)
        got = {r["window"]: r["percent"] for r in usage.read_codex_home(self.home)}
        self.assertEqual(got[FIVE_HOUR], 12.0)

    def test_weekly_jitter_pair_is_one_window(self) -> None:
        self.rollout("a", [self.limits("2026-10-09T21:00:00Z", 10.0, 17.0, s_resets=1792024075),
                           self.limits("2026-10-09T22:00:00Z", 11.0, 18.0, s_resets=1792024074)], 100)
        weekly = [r for r in usage.read_codex_home(self.home) if r["window"] == WEEKLY][0]
        self.assertEqual((weekly["percent"], weekly["resets_at"]), (18.0, 1792024075))


class MergeTest(unittest.TestCase):
    def test_codex_jitter_pair_newer_higher_wins_and_resets_never_go_back(self) -> None:
        older = reading(WEEKLY, 17.0, 1792024075, 100.0, "codex")
        newer = reading(WEEKLY, 18.0, 1792024074, 200.0, "codex")
        merged = usage.merge_reading(older, newer)
        self.assertEqual((merged["percent"], merged["resets_at"]), (18.0, 1792024075))

    def test_a_delayed_claude_stream_with_an_older_window_never_replaces_a_newer_one(self) -> None:
        stored = reading(FIVE_HOUR, 5.0, FIVE_RESETS + 5 * H, NOW)
        delayed = reading(FIVE_HOUR, 99.0, FIVE_RESETS, NOW + 999)
        self.assertEqual(usage.merge_reading(stored, delayed), stored)

    def test_a_delayed_same_window_writer_with_a_lower_percent_never_replaces_a_higher_one(self) -> None:
        stored = reading(FIVE_HOUR, 60.0, FIVE_RESETS, NOW)
        delayed = reading(FIVE_HOUR, 40.0, FIVE_RESETS, NOW + 500)
        self.assertEqual(usage.merge_reading(stored, delayed)["percent"], 60.0)

    def test_a_tie_takes_the_newer_observation(self) -> None:
        a, b = reading(FIVE_HOUR, 60.0, FIVE_RESETS, NOW), reading(FIVE_HOUR, 60.0, FIVE_RESETS, NOW + 5)
        self.assertEqual(usage.merge_reading(a, b)["observed_at"], NOW + 5)
        self.assertEqual(usage.merge_reading(b, a)["observed_at"], NOW + 5)

    def test_a_stored_reading_without_an_observation_time_yields_to_an_equal_percent_one(self) -> None:
        incoming = reading(FIVE_HOUR, 10.0, FIVE_RESETS, NOW)
        for stored_at in (None, "missing"):
            stored = reading(FIVE_HOUR, 10.0, FIVE_RESETS, stored_at)
            if stored_at == "missing":
                del stored["observed_at"]
            with self.subTest(stored_at=stored_at):
                self.assertEqual(usage.merge_reading(stored, incoming), incoming)
                self.assertEqual(usage.merge_reading(incoming, stored), incoming)

    def test_a_window_rollover_takes_the_later_window(self) -> None:
        stored = reading(FIVE_HOUR, 90.0, FIVE_RESETS, NOW)
        later = reading(FIVE_HOUR, 2.0, FIVE_RESETS + 5 * H, NOW - 50)
        self.assertEqual(usage.merge_reading(stored, later)["percent"], 2.0)

    def test_none_stored(self) -> None:
        r = reading(FIVE_HOUR, 1.0, FIVE_RESETS)
        self.assertEqual(usage.merge_reading(None, r), r)

    def test_two_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            barrier = threading.Barrier(2)

            def write(percent: float) -> None:
                barrier.wait()
                usage.record_reading(root, [reading(FIVE_HOUR, percent, FIVE_RESETS)])

            threads = [threading.Thread(target=write, args=(p,)) for p in (30.0, 70.0)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(usage.load_record(root)["readings"]["claude"][FIVE_HOUR]["percent"], 70.0)

    def test_an_unreadable_record_is_refused_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / usage.USAGE_FILE).write_text("{not json")
            with self.assertRaises(UsageRecordError):
                usage.record_reading(root, [reading(FIVE_HOUR, 1.0, FIVE_RESETS)])
            (root / usage.USAGE_FILE).write_text(json.dumps({"version": 99}))
            with self.assertRaises(UsageRecordError):
                usage.load_record(root)


def decide(five_pct=None, week_pct=None, *, reserved=(0.0, 0.0), forecast=(8.0, 1.0), settings=None,
           run=(0.0, 0.0), repo=(0.0, 0.0), now=NOW, notes=None, five_resets=FIVE_RESETS):
    readings = {}
    if five_pct is not None:
        readings[FIVE_HOUR] = reading(FIVE_HOUR, five_pct, five_resets)
    if week_pct is not None:
        readings[WEEKLY] = reading(WEEKLY, week_pct, WEEK_RESETS)
    reservations = [{FIVE_HOUR: {"amount": reserved[0]}, WEEKLY: {"amount": reserved[1]}}]
    return usage.evaluate("claude", readings, reservations, settings or Limits(), forecast, run[0], run[1],
                          repo[0], repo[1], now, notes=notes)


class EvaluateTest(unittest.TestCase):
    def test_go_under_every_threshold(self) -> None:
        self.assertEqual(decide(50, 50), GO)

    def test_five_hour_rule(self) -> None:
        hold = decide(77, 0)
        self.assertEqual(hold, decide(77, 0))
        self.assertIsInstance(hold, Hold)
        self.assertEqual((hold.window, hold.resume_at, hold.waitable), (FIVE_HOUR, FIVE_RESETS, True))
        self.assertEqual(decide(76.9, 0), GO)

    def test_weekly_rule_uses_its_own_forecast_and_threshold(self) -> None:
        hold = decide(0, 94, forecast=(1.0, 1.0))
        self.assertEqual((hold.window, hold.resume_at), (WEEKLY, WEEK_RESETS))
        self.assertEqual(decide(0, 93, forecast=(1.0, 1.0)), GO)

    def test_reservations_count_per_window(self) -> None:
        self.assertEqual(decide(70, 0, reserved=(8.0, 0.0)).window, FIVE_HOUR)
        self.assertEqual(decide(0, 90, reserved=(0.0, 4.0), forecast=(1.0, 4.0)).window, WEEKLY)

    def test_run_cap_is_not_waitable_and_has_no_resume_time(self) -> None:
        hold = decide(0, 0, settings=Limits(run_cap_percent=20), run=(10.0, 3.0))
        self.assertEqual((hold.window, hold.waitable, hold.resume_at), ("run_cap", False, None))
        self.assertTrue(hold.outstanding)
        self.assertEqual(decide(0, 0, settings=Limits(run_cap_percent=21), run=(10.0, 3.0)), GO)

    def test_repository_cap_carries_the_reset_for_the_message_only(self) -> None:
        hold = decide(0, 0, settings=Limits(repository_cap_percent=20), repo=(10.0, 8.0))
        self.assertEqual((hold.window, hold.waitable, hold.resume_at, hold.resets_at),
                         ("repository_cap", False, None, FIVE_RESETS))

    def test_a_cap_dominates_a_timed_hold_and_the_latest_timed_hold_wins(self) -> None:
        both = decide(90, 0, settings=Limits(run_cap_percent=1), run=(5.0, 0.0))
        self.assertEqual(both.window, "run_cap")
        timed = decide(90, 99)
        self.assertEqual(timed.window, WEEKLY)

    def test_a_missing_reading_disables_only_its_own_check(self) -> None:
        notes: list = []
        hold = decide(None, 99, notes=notes)
        self.assertEqual(hold.window, WEEKLY)
        self.assertEqual(notes, ["no reading: claude five_hour"])
        self.assertEqual(decide(None, None), GO)
        capped = decide(None, None, settings=Limits(run_cap_percent=5), run=(0.0, 0.0))
        self.assertEqual(capped.window, "run_cap")

    def test_a_reading_older_than_its_reset_is_zero_percent(self) -> None:
        self.assertEqual(decide(99, 0, five_resets=NOW - 1), GO)

    def test_a_forecast_that_cannot_fit_even_an_empty_window_is_not_waited_on(self) -> None:
        hold = decide(99, 0, five_resets=NOW - 1, forecast=(90.0, 1.0))
        self.assertFalse(hold.waitable)
        self.assertIsNone(hold.resume_at)

    def test_precedence_is_independent_of_the_provider(self) -> None:
        codex = Limits(provider="codex", pause_at_percent=50)
        self.assertEqual(usage.evaluate("codex", {FIVE_HOUR: reading(FIVE_HOUR, 45, FIVE_RESETS, provider="codex")},
                                        [], codex, (8.0, 1.0), 0, 0, 0, 0, NOW).provider, "codex")


class LimitsTest(unittest.TestCase):
    def test_defaults_and_providers(self) -> None:
        self.assertEqual(usage.limits_from_values({}, "claude"), Limits())
        values = {"usage.pause_at_percent": 70, "usage.codex_pause_at_percent": 60, "usage.reservation_seconds": 600,
                  "usage.codex_run_cap_percent": 12, "usage.run_cap_percent": 30}
        claude, codex = usage.limits_from_values(values, "claude"), usage.limits_from_values(values, "codex")
        self.assertEqual((claude.pause_at_percent, codex.pause_at_percent), (70, 60))
        self.assertEqual((claude.run_cap_percent, codex.run_cap_percent), (30, 12))
        self.assertEqual((claude.reservation_seconds, codex.reservation_seconds), (600, 600))
        self.assertEqual(usage.limits_from_values(values, "claude", run_cap=5).run_cap_percent, 5)


class AdmissionTest(TempRootCase):
    def test_two_lanes_cannot_both_pass_on_the_same_headroom(self) -> None:
        seed(self.root, five=70)
        first, second = admit(self.root), admit(self.root, run_id="run-2")
        self.assertIsNotNone(first.token)
        self.assertIsNone(second.token)
        self.assertEqual((second.hold.window, round(second.hold.percent + 8)), (FIVE_HOUR, 78))
        self.assertIn(first.token, self.record()["reservations"])
        self.assertEqual(len(self.record()["reservations"]), 1)

    def test_a_hold_writes_no_reservation(self) -> None:
        seed(self.root, five=99)
        self.assertIsNone(admit(self.root).token)
        self.assertEqual(self.record()["reservations"], {})

    def test_concurrent_admissions_cannot_both_pass(self) -> None:
        seed(self.root, five=70)
        barrier, results = threading.Barrier(4), []

        def go(i: int) -> None:
            barrier.wait()
            results.append(admit(self.root, run_id=f"r{i}").token)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len([t for t in results if t]), 1)

    def test_admission_with_no_reading_goes_and_records_a_note(self) -> None:
        admission = admit(self.root)
        self.assertIsNotNone(admission.token)
        self.assertIn("no reading: claude five_hour", admission.notes)
        reservation = self.record()["reservations"][admission.token]
        self.assertIsNone(reservation[FIVE_HOUR]["start"] if reservation[FIVE_HOUR]["window_resets_at"] is None
                          else None)

    def test_admission_merges_supplied_readings_first(self) -> None:
        admission = admit(self.root, readings=[reading(FIVE_HOUR, 90, FIVE_RESETS)])
        self.assertIsNone(admission.token)

    def test_the_reservation_carries_start_baseline_and_forecast(self) -> None:
        seed(self.root, five=10, weekly=20)
        token = admit(self.root).token
        r = self.record()["reservations"][token]
        self.assertEqual((r[FIVE_HOUR]["amount"], r[FIVE_HOUR]["start"], r[FIVE_HOUR]["window_resets_at"]),
                         (8.0, 10.0, FIVE_RESETS))
        self.assertEqual((r[WEEKLY]["amount"], r[WEEKLY]["start"]), (1.0, 20.0))
        self.assertEqual(r["baseline"][FIVE_HOUR], {"start": 10.0, "window_resets_at": FIVE_RESETS})
        self.assertEqual(r["expires_at"], NOW + 7200)

    def test_weekly_forecast_hold_for_each_provider(self) -> None:
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                root = self.root / provider
                root.mkdir()
                seed(root, five=0, weekly=94, provider=provider)
                a = admit(root, provider=provider, limits=Limits(provider=provider, default_job_weekly_percent=2))
                self.assertEqual(a.hold.window, WEEKLY)

    def test_independent_provider_caps(self) -> None:
        seed(self.root, five=50, provider="claude")
        seed(self.root, five=50, provider="codex")
        claude = admit(self.root, provider="claude", limits=Limits(provider="claude", pause_at_percent=55))
        codex = admit(self.root, provider="codex", limits=Limits(provider="codex", pause_at_percent=90))
        self.assertIsNone(claude.token)
        self.assertIsNotNone(codex.token)


class RolloverLapseRenewTest(TempRootCase):
    def test_a_five_hour_reset_rolls_the_five_hour_component_only(self) -> None:
        seed(self.root, five=10, weekly=90)
        limits = Limits()
        holder = admit(self.root, limits=limits, repository="/a", run_id="a").token
        after = FIVE_RESETS + 60
        usage.record_reading(self.root, [reading(FIVE_HOUR, 5, FIVE_RESETS + 5 * H, after),
                                         reading(WEEKLY, 90, WEEK_RESETS, after)])
        # The holder still holds its five-hour forecast in the new window and its weekly forecast in the old one.
        usage.renew(self.root, holder, reservation_seconds=7200, now=after)
        r = self.record()["reservations"][holder]
        self.assertEqual(r[FIVE_HOUR]["amount"], 8.0)
        self.assertEqual((r[FIVE_HOUR]["start"], r[FIVE_HOUR]["window_resets_at"]), (5.0, FIVE_RESETS + 5 * H))
        self.assertEqual((r[WEEKLY]["start"], r[WEEKLY]["window_resets_at"]), (90.0, WEEK_RESETS))
        self.assertEqual(r["rolled_over"], 1)
        self.assertEqual(r["baseline"][FIVE_HOUR]["window_resets_at"], FIVE_RESETS)
        # Weekly 90 + holder 4 + this 4 = 98 >= 95: still held on the weekly window after the five-hour reset.
        weekly = Limits(default_job_weekly_percent=4)
        holder2 = usage.renew(self.root, holder, reservation_seconds=7200, now=after)
        self.assertEqual(holder2, usage.RENEWED)
        record = self.record()
        record["reservations"][holder][WEEKLY]["amount"] = 4.0
        runtime.write_json(self.root, usage.USAGE_FILE, record)
        second = admit(self.root, limits=weekly, now=after, run_id="b")
        self.assertEqual(second.hold.window, WEEKLY)

    def test_a_weekly_reset_rolls_only_the_weekly_component(self) -> None:
        seed(self.root, five=10, weekly=50)
        holder = admit(self.root).token
        after = WEEK_RESETS + 1
        usage.record_reading(self.root, [reading(FIVE_HOUR, 20, after + 4 * H, after), reading(WEEKLY, 1, WEEK_RESETS + 7 * 86400, after)])
        record = usage.snapshot(self.root, now=after)
        r = record["reservations"].get(holder) or record["lapsed"][holder]
        self.assertEqual(r[WEEKLY]["window_resets_at"], WEEK_RESETS + 7 * 86400)
        self.assertEqual(r[WEEKLY]["start"], 1.0)

    def test_a_rollover_without_a_reading_advances_the_old_window(self) -> None:
        seed(self.root, five=10)
        holder = admit(self.root).token
        record = usage.snapshot(self.root, now=FIVE_RESETS + 10)
        comp = (record["reservations"].get(holder) or record["lapsed"][holder])[FIVE_HOUR]
        self.assertEqual((comp["start"], comp["window_resets_at"]), (0.0, FIVE_RESETS + 5 * H))

    def test_an_expired_lease_lapses_but_keeps_the_cap_liability(self) -> None:
        seed(self.root, five=10)
        limits = Limits(reservation_seconds=60, run_cap_percent=20, repository_cap_percent=20)
        token = admit(self.root, limits=limits).token
        later = NOW + 61
        record = usage.snapshot(self.root, now=later)
        self.assertNotIn(token, record["reservations"])
        self.assertEqual(record["lapsed"][token]["reason"], "expired")
        # no longer counts for the account windows
        self.assertEqual(admit(self.root, limits=Limits(), now=later, run_id="other", repository="/other").hold, None)
        self.assertEqual(usage.run_outstanding(record, "claude", "run-1"), 8.0)
        self.assertEqual(usage.repository_outstanding(record, "claude", "/repo", later), 8.0)
        # but still counts for the run cap: spent 0 + outstanding 8 + forecast 8 = 16 <= 20 ... then 8 more breaks it
        self.assertEqual(admit(self.root, limits=limits, now=later).hold, None)
        self.assertEqual(admit(self.root, limits=limits, now=later).hold.window, "run_cap")

    def test_the_expiry_is_an_event(self) -> None:
        seed(self.root)
        admit(self.root, limits=Limits(reservation_seconds=60))
        admit(self.root, now=NOW + 120, run_id="x")
        events = [json.loads(l) for l in (self.root / usage.EVENTS_FILE).read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["usage_reservation_expired"])
        self.assertEqual(set(events[0]), {"event", "at", "provider", "window", "percent", "forecast", "resume_at",
                                          "reason", "token", "repository", "run_id"})

    def test_renew_extends_a_live_lease(self) -> None:
        seed(self.root)
        token = admit(self.root, limits=Limits(reservation_seconds=100)).token
        self.assertEqual(usage.renew(self.root, token, reservation_seconds=100, now=NOW + 90), usage.RENEWED)
        r = self.record()["reservations"][token]
        self.assertEqual((r["renewed_at"], r["expires_at"]), (NOW + 90, NOW + 190))

    def test_renew_revives_a_lapsed_reservation_with_a_fresh_lease(self) -> None:
        seed(self.root)
        token = admit(self.root, limits=Limits(reservation_seconds=60)).token
        later = NOW + 600
        self.assertEqual(usage.renew(self.root, token, reservation_seconds=60, now=later), usage.REVIVED)
        record = self.record()
        self.assertIn(token, record["reservations"])
        self.assertNotIn(token, record["lapsed"])
        self.assertEqual(record["reservations"][token]["expires_at"], later + 60)
        self.assertNotIn("reason", record["reservations"][token])

    def test_renew_on_an_unknown_or_settled_token_revives_nothing(self) -> None:
        seed(self.root)
        self.assertEqual(usage.renew(self.root, "nope", reservation_seconds=60, now=NOW), usage.UNKNOWN)
        token = admit(self.root).token
        usage.complete(self.root, token, end(12, 3), usage.OUTCOME_OK, now=NOW + 600)
        self.assertEqual(usage.renew(self.root, token, reservation_seconds=60, now=NOW + 700), usage.UNKNOWN)
        self.assertEqual(self.record()["reservations"], {})

    def test_a_job_that_outlives_the_lease_keeps_its_reservation_when_renewed(self) -> None:
        seed(self.root, five=70)
        limits = Limits(reservation_seconds=100)
        token = admit(self.root, limits=limits).token
        for step in range(1, 10):
            usage.renew(self.root, token, reservation_seconds=100, now=NOW + 50 * step)
        late = NOW + 450
        self.assertIn(token, usage.snapshot(self.root, now=late)["reservations"])
        self.assertIsNone(admit(self.root, limits=limits, now=late, run_id="other").token)

    def test_a_lapsed_reservation_is_abandoned_after_seven_days_at_its_forecast(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root, limits=Limits(reservation_seconds=60)).token
        later = NOW + 8 * 86400
        usage.record_reading(self.root, [reading(FIVE_HOUR, 1, later + H, later), reading(WEEKLY, 1, later + 86400, later)])
        usage.renew(self.root, "nobody", reservation_seconds=60, now=later)  # any write maintains the record
        record = self.record()
        self.assertNotIn(token, record["lapsed"])
        self.assertIn(token, record["settled"])
        entry = record["ledger"][-1]
        self.assertEqual((entry["delta"], entry["charged"], entry["five_hour"], entry["weekly"]), ("abandoned", 8.0, None, None))
        self.assertEqual(usage.run_spent(record, "claude", "run-1"), 8.0)
        self.assertEqual(usage.forecast(record, "claude", "implement", "opus", Limits()), (8.0, 1.0))
        self.assertEqual(usage.complete(self.root, token, end(5, 5, later), usage.OUTCOME_OK, now=later).reason,
                         "already_settled")

    def test_bind_job_writes_the_job_id(self) -> None:
        seed(self.root)
        token = admit(self.root).token
        self.assertTrue(usage.bind_job(self.root, token, "job-1", now=NOW))
        self.assertEqual(self.record()["reservations"][token]["job_id"], "job-1")
        self.assertFalse(usage.bind_job(self.root, "nope", "job-2", now=NOW))


class RenewalTickerTest(unittest.TestCase):
    def test_renews_once_per_step_survives_errors_and_stops_idempotently(self) -> None:
        steps = threading.Semaphore(0)
        done = threading.Event()
        calls: list = []
        errors: list = []

        def wait(seconds: float) -> bool:
            done.wait(0.01)
            return not steps.acquire(timeout=5) or stopped.is_set()

        stopped = threading.Event()

        def renew() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")

        ticker = usage.RenewalTicker(renew, 30, wait, on_error=errors.append)
        ticker.start()
        ticker.start()
        steps.release()
        steps.release()
        for _ in range(500):
            if len(calls) >= 2:
                break
            done.wait(0.01)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(errors), 1)
        stopped.set()
        steps.release()
        ticker.stop()
        ticker.stop()

    def test_interval(self) -> None:
        self.assertEqual(usage.renewal_interval(7200), 60)
        self.assertEqual(usage.renewal_interval(100), 25)


class CompleteTest(TempRootCase):
    def test_measured_delta_and_the_ledger_entry(self) -> None:
        seed(self.root, five=10, weekly=20)
        token = admit(self.root).token
        result = usage.complete(self.root, token, end(16, 21), usage.OUTCOME_OK, now=NOW + 600)
        self.assertEqual(result.status, usage.COMPLETE_CHARGED)
        entry = result.entry
        self.assertEqual((entry["delta"], entry["five_hour"], entry["weekly"], entry["charged"]),
                         ("measured", 6.0, 1.0, 6.0))
        self.assertEqual((entry["five_hour_after_reset"], entry["weekly_after_reset"]), (None, None))
        self.assertEqual((entry["reserved_five_hour"], entry["reserved_weekly"]), (8.0, 1.0))
        self.assertEqual((entry["start_window_resets_at"], entry["end_window_resets_at"]), (FIVE_RESETS, FIVE_RESETS))
        self.assertEqual((entry["id"], entry["provider"], entry["role"], entry["model"], entry["repository"],
                          entry["run_id"], entry["started_at"], entry["finished_at"]),
                         (token, "claude", "implement", "opus", "/repo", "run-1", NOW, NOW + 600))
        record = self.record()
        self.assertEqual(record["reservations"], {})
        self.assertIn(token, record["settled"])
        self.assertEqual(usage.run_spent(record, "claude", "run-1"), 6.0)
        self.assertEqual(usage.repository_spent(record, "claude", "/repo", NOW + 600), 6.0)
        self.assertEqual(record["readings"]["claude"][FIVE_HOUR]["percent"], 16.0)

    def test_a_missing_end_reading_is_unknown_and_charged_at_the_forecast(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        entry = usage.complete(self.root, token, {}, usage.OUTCOME_OK, now=NOW + 600).entry
        self.assertEqual((entry["delta"], entry["five_hour"], entry["charged"], entry["end_window_resets_at"]),
                         ("unknown", None, 8.0, None))

    def test_an_end_reading_no_newer_than_the_start_is_unknown(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        stale = {FIVE_HOUR: reading(FIVE_HOUR, 12, FIVE_RESETS, NOW)}
        entry = usage.complete(self.root, token, stale, usage.OUTCOME_OK, now=NOW + 600).entry
        self.assertEqual((entry["delta"], entry["charged"]), ("unknown", 8.0))

    def test_after_reset_is_charged_at_least_the_forecast(self) -> None:
        seed(self.root, five=10, weekly=20)
        token = admit(self.root).token
        after = FIVE_RESETS + 60
        new_end = end(3, 21, after, five_resets=FIVE_RESETS + 5 * H)
        entry = usage.complete(self.root, token, new_end, usage.OUTCOME_OK, now=after).entry
        self.assertEqual((entry["delta"], entry["five_hour"], entry["five_hour_after_reset"], entry["charged"]),
                         ("after_reset", None, 3.0, 8.0))
        self.assertEqual(entry["weekly"], 1.0)
        # repository spend: the job began in an earlier window, so it counts its unfloored after-reset figure
        self.assertEqual(usage.repository_spent(self.record(), "claude", "/repo", after), 3.0)
        self.assertEqual(usage.run_spent(self.record(), "claude", "run-1"), 8.0)

    def test_a_manual_worker_spanning_a_reset_is_rolled_over_then_after_reset(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        after = FIVE_RESETS + 60
        usage.record_reading(self.root, [reading(FIVE_HOUR, 2, FIVE_RESETS + 5 * H, after)])
        usage.renew(self.root, token, reservation_seconds=7200, now=after)
        entry = usage.complete(self.root, token, end(4, 21, after + 5, five_resets=FIVE_RESETS + 5 * H),
                               usage.OUTCOME_OK, now=after + 5).entry
        self.assertEqual((entry["delta"], entry["charged"]), ("after_reset", 8.0))

    def test_an_unknown_delta_that_began_earlier_places_the_forecast_in_the_repository(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        after = FIVE_RESETS + 60
        usage.record_reading(self.root, [reading(FIVE_HOUR, 2, FIVE_RESETS + 5 * H, after)])
        usage.complete(self.root, token, {}, usage.OUTCOME_OK, now=after)
        self.assertEqual(usage.repository_spent(self.record(), "claude", "/repo", after), 8.0)

    def test_complete_is_idempotent_by_token(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        usage.complete(self.root, token, end(16, 21), usage.OUTCOME_OK, now=NOW + 600)
        again = usage.complete(self.root, token, end(30, 30), usage.OUTCOME_OK, now=NOW + 700)
        self.assertEqual((again.status, again.reason), (usage.COMPLETE_NOOP, "already_settled"))
        record = self.record()
        self.assertEqual(len(record["ledger"]), 1)
        self.assertEqual(usage.run_spent(record, "claude", "run-1"), 6.0)

    def test_an_unknown_token_is_a_recorded_no_op_even_with_a_usage_block(self) -> None:
        seed(self.root)
        block = {"accounting": "pending", "five_hour_end": 50, "weekly_end": 50, "charged": 40}
        result = usage.complete(self.root, "ghost", None, usage.OUTCOME_OK, now=NOW, usage_block=block)
        self.assertEqual((result.status, result.reason), (usage.COMPLETE_NOOP, "unknown_token"))
        record = self.record()
        self.assertEqual((record["ledger"], record["spend"]["run"]), ([], {}))
        events = [json.loads(l)["event"] for l in (self.root / usage.EVENTS_FILE).read_text().splitlines()]
        self.assertEqual(events, ["usage_complete_unknown_token"])

    def test_a_settled_token_is_a_silent_no_op_with_its_own_event(self) -> None:
        seed(self.root)
        token = admit(self.root).token
        usage.complete(self.root, token, end(12, 3), usage.OUTCOME_OK, now=NOW + 60)
        usage.complete(self.root, token, None, usage.OUTCOME_OK, now=NOW + 70, usage_block={"five_hour_end": 99})
        events = [json.loads(l)["event"] for l in (self.root / usage.EVENTS_FILE).read_text().splitlines()]
        self.assertEqual(events, ["usage_complete_already_settled"])

    def test_the_usage_block_supplies_only_the_end_figures(self) -> None:
        seed(self.root, five=10, weekly=20)
        token = admit(self.root).token
        block = {"five_hour_end": 15.0, "weekly_end": 22.0, "end_window_resets_at": FIVE_RESETS, "weekly_end_resets_at": WEEK_RESETS,
                 "end_observed_at": NOW + 300, "five_hour_start": 99.0}
        entry = usage.complete(self.root, token, None, usage.OUTCOME_OK, now=NOW + 600, usage_block=block).entry
        # the baseline is the reservation's (10), never the block's
        self.assertEqual((entry["delta"], entry["five_hour"], entry["weekly"]), ("measured", 5.0, 2.0))

    def test_not_started_releases_with_no_ledger_spend_or_settled_entry(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        result = usage.complete(self.root, token, None, usage.OUTCOME_NOT_STARTED, now=NOW + 5)
        self.assertEqual(result.status, usage.COMPLETE_NOT_STARTED)
        record = self.record()
        self.assertEqual((record["reservations"], record["ledger"], record["settled"]), ({}, [], {}))
        self.assertEqual(usage.run_spent(record, "claude", "run-1"), 0.0)

    def test_complete_on_a_lapsed_token_writes_the_entry_exactly_once(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root, limits=Limits(reservation_seconds=60)).token
        later = NOW + 600
        first = usage.complete(self.root, token, end(14, 21, later), usage.OUTCOME_OK, now=later)
        self.assertEqual(first.status, usage.COMPLETE_CHARGED)
        usage.complete(self.root, token, end(14, 21, later), usage.OUTCOME_OK, now=later + 1)
        self.assertEqual(len(self.record()["ledger"]), 1)

    def test_stream_start_lowers_the_baseline(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        start = {FIVE_HOUR: reading(FIVE_HOUR, 7, FIVE_RESETS, NOW - 100)}
        entry = usage.complete(self.root, token, end(16, 3), usage.OUTCOME_OK, now=NOW + 600, stream_start=start).entry
        self.assertEqual(entry["five_hour"], 9.0)

    def test_the_repository_total_is_per_window(self) -> None:
        seed(self.root, five=0)
        for i in range(2):
            token = admit(self.root, run_id=f"r{i}").token
            usage.complete(self.root, token, end(4 * (i + 1), 1, NOW + 600 + i), usage.OUTCOME_OK, now=NOW + 600 + i)
        self.assertEqual(usage.repository_spent(self.record(), "claude", "/repo", NOW + 700), 8.0)
        self.assertEqual(usage.repository_spent(self.record(), "claude", "/repo", FIVE_RESETS + 1), 0.0)
        # a delayed writer for an older window is ignored, a newer window replaces
        record = self.record()
        usage._charge_repository(record, "claude", "/repo", FIVE_RESETS - 5 * H, 50.0, NOW + 700)
        self.assertEqual(usage.repository_spent(record, "claude", "/repo", NOW + 700), 8.0)
        usage._charge_repository(record, "claude", "/repo", FIVE_RESETS + 5 * H, 2.0, NOW + 700)
        self.assertEqual(record["spend"]["repository"]["claude"]["/repo"]["charged"], 2.0)


class ReplayEquivalenceTest(TempRootCase):
    """Review round 3 (I1-I3): an unmeasured completion after a reset keeps its
    repository liability, and a replay from the job record's durable figures
    charges exactly what the uninterrupted completion did."""

    def _admit_and_roll(self, **limits):
        seed(self.root, five=10, weekly=2)
        token = admit(self.root, limits=Limits(repository_cap_percent=10, reservation_seconds=5 * H, **limits)).token
        after = FIVE_RESETS + 60
        usage.renew(self.root, token, reservation_seconds=5 * H, now=after)
        self.assertEqual(usage.repository_outstanding(self.record(), "claude", "/repo", after), 8.0)
        return token, after

    def _assert_liability_replaced(self, after: float) -> None:
        record = self.record()
        self.assertEqual(usage.repository_outstanding(record, "claude", "/repo", after), 0.0)
        self.assertEqual(usage.repository_spent(record, "claude", "/repo", after), 8.0)
        second = admit(self.root, now=after, limits=Limits(repository_cap_percent=10), run_id="run-2")
        self.assertEqual(second.hold.window, "repository_cap")

    def test_an_unknown_completion_after_a_reset_charges_the_current_window(self) -> None:
        token, after = self._admit_and_roll()
        entry = usage.complete(self.root, token, {}, usage.OUTCOME_OK, now=after).entry
        self.assertEqual(entry["delta"], "unknown")
        self._assert_liability_replaced(after)

    def test_a_cached_expired_end_reading_charges_the_current_window(self) -> None:
        token, after = self._admit_and_roll()
        cached = {FIVE_HOUR: reading(FIVE_HOUR, 12, FIVE_RESETS, NOW)}
        entry = usage.complete(self.root, token, cached, usage.OUTCOME_OK, now=after).entry
        self.assertEqual(entry["delta"], "unknown")
        self._assert_liability_replaced(after)

    def _both(self, first, last, block_extra=None, **seed_args):
        """``(normal entry, replay entry)`` for the same job."""
        entries = []
        for replay in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                seed(root, **seed_args)
                token = admit(root).token
                if replay:
                    block = {"five_hour_end": last[FIVE_HOUR]["percent"], "end_window_resets_at": last[FIVE_HOUR]["resets_at"],
                             "end_observed_at": last[FIVE_HOUR]["observed_at"],
                             "weekly_end": last[WEEKLY]["percent"], "weekly_end_resets_at": last[WEEKLY]["resets_at"]}
                    for window, prefix in ((FIVE_HOUR, "five_hour"), (WEEKLY, "weekly")):
                        if window in first:
                            block[f"{prefix}_first"] = first[window]["percent"]
                            block[f"{prefix}_first_resets_at"] = first[window]["resets_at"]
                    result = usage.complete(root, token, None, usage.OUTCOME_OK, now=NOW + 600, usage_block=block)
                else:
                    result = usage.complete(root, token, last, usage.OUTCOME_OK, now=NOW + 600, stream_start=first)
                entries.append(result.entry)
        return entries

    def test_replay_keeps_the_streams_lower_starting_reading(self) -> None:
        first = {FIVE_HOUR: reading(FIVE_HOUR, 10, FIVE_RESETS, NOW + 10)}
        normal, replay = self._both(first, end(25, 21), five=20, weekly=20)
        self.assertEqual(normal["charged"], 15.0)
        self.assertEqual(replay["charged"], normal["charged"])
        self.assertEqual((replay["five_hour"], replay["delta"]), (normal["five_hour"], normal["delta"]))

    def test_replay_identifies_an_independent_weekly_reset(self) -> None:
        last = end(12, 3, week_resets=WEEK_RESETS + 7 * 86400)
        normal, replay = self._both({}, last, five=10, weekly=2)
        self.assertEqual((normal["weekly"], normal["weekly_after_reset"]), (None, 3.0))
        for key in ("five_hour", "weekly", "five_hour_after_reset", "weekly_after_reset", "charged", "delta"):
            self.assertEqual(replay[key], normal[key], key)

    def test_an_unknown_completion_with_an_expired_shared_reading_keeps_the_window_for_later_charges(self) -> None:
        seed(self.root, five=10, weekly=2)
        token = admit(self.root, limits=Limits(repository_cap_percent=18, reservation_seconds=5 * H)).token
        after = FIVE_RESETS + 600
        usage.complete(self.root, token, {}, usage.OUTCOME_OK, now=after)
        entry = self.record()["spend"]["repository"]["claude"]["/repo"]
        self.assertTrue(usage.same_window(entry["window_resets_at"], FIVE_RESETS + 5 * H))
        # a later worker supplies the actual current window and measures 8%
        window = FIVE_RESETS + 5 * H
        token2 = admit(self.root, now=after, run_id="run-2", limits=Limits(repository_cap_percent=100),
                       readings=[reading(FIVE_HOUR, 0, window, after), reading(WEEKLY, 2, WEEK_RESETS, after)]).token
        usage.complete(self.root, token2, {FIVE_HOUR: reading(FIVE_HOUR, 8, window, after + 60),
                                           WEEKLY: reading(WEEKLY, 3, WEEK_RESETS, after + 60)},
                       usage.OUTCOME_OK, now=after + 60)
        self.assertEqual(usage.repository_spent(self.record(), "claude", "/repo", after + 60), 16.0)
        third = admit(self.root, now=after + 60, run_id="run-3", limits=Limits(repository_cap_percent=18))
        self.assertEqual(third.hold.window, "repository_cap")

    def _lapse_before_the_reset(self) -> tuple[str, float]:
        """A token whose reservation lapsed before the five-hour reset, and a
        time after that reset."""
        seed(self.root, five=10, weekly=2)
        token = admit(self.root, limits=Limits(repository_cap_percent=18, reservation_seconds=H)).token
        # another lane's admission, elsewhere, lapses the first reservation
        admit(self.root, now=FIVE_RESETS - H, run_id="run-other", repository="/other")
        self.assertIn(token, self.record()["lapsed"])
        return token, FIVE_RESETS + 600

    def _assert_later_measured_charge_adds(self, after: float) -> None:
        entry = self.record()["spend"]["repository"]["claude"]["/repo"]
        self.assertTrue(usage.same_window(entry["window_resets_at"], FIVE_RESETS + 5 * H))
        window = FIVE_RESETS + 5 * H
        token2 = admit(self.root, now=after, run_id="run-2", limits=Limits(repository_cap_percent=100),
                       readings=[reading(FIVE_HOUR, 0, window, after), reading(WEEKLY, 2, WEEK_RESETS, after)]).token
        usage.complete(self.root, token2, {FIVE_HOUR: reading(FIVE_HOUR, 8, window, after + 60),
                                           WEEKLY: reading(WEEKLY, 3, WEEK_RESETS, after + 60)},
                       usage.OUTCOME_OK, now=after + 60)
        self.assertEqual(usage.repository_spent(self.record(), "claude", "/repo", after + 60), 16.0)
        third = admit(self.root, now=after + 60, run_id="run-3", limits=Limits(repository_cap_percent=18))
        self.assertEqual(third.hold.window, "repository_cap")

    def test_an_unknown_completion_of_a_lapsed_reservation_keeps_the_current_window(self) -> None:
        token, after = self._lapse_before_the_reset()
        usage.complete(self.root, token, {}, usage.OUTCOME_OK, now=after)
        self._assert_later_measured_charge_adds(after)

    def test_a_replayed_lapsed_reservation_keeps_the_current_window(self) -> None:
        token, after = self._lapse_before_the_reset()
        usage.bind_job(self.root, token, "j1", now=NOW)
        jobs = {"j1": {"status": "FAILED", "expected_transition": {}, "usage": {"accounting": "open"}}}
        settled = usage.settle_pending(self.root, jobs.get, lambda r: "expected_transition" not in r, now=after,
                                       is_terminal=lambda r: r["status"] == "FAILED")
        self.assertEqual([s.action for s in settled], ["unknown"])
        self._assert_later_measured_charge_adds(after)

    def test_a_legacy_block_without_the_weekly_identity_is_not_a_weekly_sample(self) -> None:
        legacy = {"five_hour_end": 12.0, "end_window_resets_at": FIVE_RESETS, "end_observed_at": NOW + 600,
                  "weekly_end": 20.0}
        seed(self.root, five=10, weekly=2)
        token = admit(self.root).token
        entry = usage.complete(self.root, token, None, usage.OUTCOME_OK, now=NOW + 600, usage_block=legacy).entry
        self.assertIsNone(entry["weekly"])
        self.assertIsNone(entry["weekly_after_reset"])
        self.assertEqual(entry["five_hour"], 2.0)
        self.assertEqual(usage.forecast(self.record(), "claude", "implement", "opus", Limits())[1],
                         Limits().default_job_weekly_percent)


class MalformedRecordTest(TempRootCase):
    def _write(self, text: str) -> Path:
        path = self.root / usage.USAGE_FILE
        path.write_text(text)
        return path

    @staticmethod
    def _nested(good: dict, token: str, change) -> dict:
        raw = json.loads(json.dumps(good))
        change(raw["reservations"][token])
        return raw

    def test_structurally_corrupt_version_2_records_are_unreadable(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        good = self.record()
        corrupt = [
            {"version": 2, "spend": []},
            {"version": 2, "readings": []},
            {"version": 2, "ledger": {}},
            {"version": 2, "reservations": {"t": 5}},
            {"version": 2, "settled": {"t": "now"}},
            {"version": 2, "spend": {"run": {"claude": {"r": []}}}},
            {"version": 2, "readings": {"claude": {FIVE_HOUR: {"percent": "x"}}}},
            {**good, "reservations": {token: {**good["reservations"][token], "baseline": {}}}},
            {**good, "reservations": {token: {k: v for k, v in good["reservations"][token].items() if k != FIVE_HOUR}}},
            self._nested(good, token, lambda r: r.update({FIVE_HOUR: {"amount": 8}})),
            self._nested(good, token, lambda r: r["baseline"].update({FIVE_HOUR: {}})),
            self._nested(good, token, lambda r: r.update({"run_id": ["x"]})),
            self._nested(good, token, lambda r: r.update({"repository": 5})),
            {**good, "ledger": [{"provider": "claude", "finished_at": NOW, "five_hour": "9"}]},
            {**good, "ledger": [{"provider": "claude", "finished_at": NOW, "model": []}]},
        ]
        for raw in corrupt:
            with self.subTest(raw=raw):
                text = json.dumps(raw)
                path = self._write(text)
                with self.assertRaises(UsageRecordError):
                    usage.load_record(self.root)
                with self.assertRaises(UsageRecordError):
                    usage.complete(self.root, token, {}, usage.OUTCOME_OK, now=NOW)
                self.assertEqual(path.read_text(), text)

    def _stored_reading(self, change) -> dict:
        """The record after ``change`` mutated its stored five-hour reading."""
        raw = json.loads((self.root / usage.USAGE_FILE).read_text())
        change(raw["readings"]["claude"][FIVE_HOUR])
        return raw

    def test_an_unstamped_shared_reading_is_not_shown_newer_so_the_forecast_is_charged(self) -> None:
        for variant in ("missing", "null"):
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory() as tmp:
                    self.root = Path(tmp)
                    seed(self.root, five=10)
                    token = admit(self.root).token
                    raw = self._stored_reading(lambda r: r.pop("observed_at") if variant == "missing"
                                               else r.update({"observed_at": None}))
                    (self.root / usage.USAGE_FILE).write_text(json.dumps(raw))
                    readings = list(usage.current_readings(usage.snapshot(self.root, now=NOW + 60), "claude").values())
                    entry = usage.complete(self.root, token, readings, usage.OUTCOME_OK, now=NOW + 60).entry
                    self.assertEqual((entry["delta"], entry["charged"]), ("unknown", 8.0))
                    self.assertEqual(usage.run_spent(self.record(), "claude", "run-1"), 8.0)
                    again = usage.complete(self.root, token, readings, usage.OUTCOME_OK, now=NOW + 61)
                    self.assertEqual((again.status, again.reason), (usage.COMPLETE_NOOP, "already_settled"))
                    self.assertEqual(usage.run_spent(self.record(), "claude", "run-1"), 8.0)

    def test_a_shared_reading_claiming_the_job_record_source_is_not_measured_untimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.root = Path(tmp)
            seed(self.root, five=10)
            token = admit(self.root).token
            raw = self._stored_reading(lambda r: (r.pop("observed_at"), r.update({"source": "job-record"})))
            (self.root / usage.USAGE_FILE).write_text(json.dumps(raw))
            self.assertEqual(self.record()["readings"]["claude"][FIVE_HOUR]["source"], "shared-record")
            readings = list(usage.current_readings(usage.snapshot(self.root, now=NOW + 60), "claude").values())
            entry = usage.complete(self.root, token, readings, usage.OUTCOME_OK, now=NOW + 60).entry
            self.assertEqual((entry["delta"], entry["charged"]), ("unknown", 8.0))

    def test_a_job_record_reading_without_a_time_is_still_measured(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        block = {"five_hour_end": 14.0, "end_window_resets_at": FIVE_RESETS}
        entry = usage.complete(self.root, token, None, usage.OUTCOME_OK, now=NOW + 60, usage_block=block).entry
        self.assertEqual((entry["delta"], entry["charged"]), ("measured", 4.0))

    def test_a_stored_reading_identity_is_the_keys_it_is_stored_under(self) -> None:
        for change in (lambda r: r.pop("window"), lambda r: r.update({"window": None}),
                       lambda r: r.update({"window": ["x"]}), lambda r: r.update({"provider": ["x"]}),
                       lambda r: r.pop("provider")):
            seed(self.root, five=10)
            token = admit(self.root).token
            (self.root / usage.USAGE_FILE).write_text(json.dumps(self._stored_reading(change)))
            readings = usage.load_record(self.root)["readings"]["claude"]
            self.assertEqual((readings[FIVE_HOUR]["provider"], readings[FIVE_HOUR]["window"]), ("claude", FIVE_HOUR))
            snap = usage.snapshot(self.root, now=NOW + 60)
            result = usage.complete(self.root, token, list(usage.current_readings(snap, "claude").values()),
                                    usage.OUTCOME_OK, now=NOW + 60)
            self.assertEqual(result.status, usage.COMPLETE_CHARGED)
            (self.root / usage.USAGE_FILE).unlink()

    def test_oversized_or_non_finite_figures_are_an_unreadable_record(self) -> None:
        seed(self.root, five=10)
        token = admit(self.root).token
        good = self.record()
        corrupt = [
            self._stored_reading(lambda r: r.update({"observed_at": 10 ** 400})),
            self._stored_reading(lambda r: r.update({"resets_at": 1_790_000_000_000})),
            self._stored_reading(lambda r: r.update({"percent": float("inf")})),
            self._stored_reading(lambda r: r.update({"percent": float("nan")})),
            {**good, "ledger": [{"provider": "claude", "finished_at": NOW, "five_hour": 10 ** 400}]},
        ]
        for raw in corrupt:
            with self.subTest(raw=raw):
                text = json.dumps(raw)
                path = self._write(text)
                with self.assertRaises(UsageRecordError):
                    usage.load_record(self.root)
                with self.assertRaises(UsageRecordError):
                    usage.complete(self.root, token, end(16, 21), usage.OUTCOME_OK, now=NOW + 600)
                with self.assertRaises(UsageRecordError):
                    admit(self.root, now=NOW + 600)
                self.assertEqual(path.read_text(), text)

    def test_a_valid_record_still_loads(self) -> None:
        seed(self.root, five=10)
        admit(self.root)
        usage.complete(self.root, next(iter(self.record()["reservations"])), end(14, 2), usage.OUTCOME_OK, now=NOW + 600)
        self.assertEqual(usage.load_record(self.root)["version"], usage.RECORD_VERSION)


class ForecastTest(TempRootCase):
    def complete_job(self, five: float, weekly: float, i: int, role: str = "implement") -> None:
        token = admit(self.root, role=role, run_id=f"r{i}", repository=None).token
        usage.complete(self.root, token, end(five, weekly, NOW + 10 * i + 1), usage.OUTCOME_OK, now=NOW + 10 * i + 1)

    def test_default_without_history(self) -> None:
        self.assertEqual(usage.forecast(self.record(), "claude", "implement", "opus", Limits()), (8.0, 1.0))
        custom = Limits(default_job_percent=3, default_job_weekly_percent=0.5)
        self.assertEqual(usage.forecast(self.record(), "claude", "implement", "opus", custom), (3.0, 0.5))

    def test_mean_of_measured_history_per_role_and_model(self) -> None:
        seed(self.root, five=0, weekly=0)
        usage.record_reading(self.root, [])
        self.complete_job(4, 1, 1)
        record = self.record()
        # the first job: start 0 -> end 4 (five) and 1 (weekly)
        self.assertEqual(usage.forecast(record, "claude", "implement", "opus", Limits()), (4.0, 1.0))
        self.assertEqual(usage.forecast(record, "claude", "review-plan", "opus", Limits()), (8.0, 1.0))
        self.assertEqual(usage.forecast(record, "codex", "implement", "opus", Limits()), (8.0, 1.0))

    def test_unknown_after_reset_and_abandoned_entries_never_enter_the_mean(self) -> None:
        record = usage._empty_record()
        base = {"provider": "claude", "role": "r", "model": "m", "finished_at": NOW}
        record["ledger"] = [
            {**base, "five_hour": 2.0, "weekly": 0.5, "delta": "measured"},
            {**base, "five_hour": None, "weekly": None, "delta": "unknown"},
            {**base, "five_hour": None, "weekly": None, "delta": "after_reset"},
            {**base, "five_hour": None, "weekly": None, "delta": "abandoned"},
            {**base, "five_hour": 4.0, "weekly": None, "delta": "measured"},
        ]
        self.assertEqual(usage.forecast(record, "claude", "r", "m", Limits()), (3.0, 0.5))

    def test_only_the_last_twenty_count(self) -> None:
        record = usage._empty_record()
        base = {"provider": "claude", "role": "r", "model": "m", "finished_at": NOW, "weekly": None}
        record["ledger"] = [{**base, "five_hour": 100.0}] * 5 + [{**base, "five_hour": 2.0}] * 20
        self.assertEqual(usage.forecast(record, "claude", "r", "m", Limits())[0], 2.0)

    def test_a_forecast_history_changes_admission(self) -> None:
        seed(self.root, five=0, weekly=0)
        self.complete_job(30, 2, 1)
        seed(self.root, five=60, weekly=2)
        admission = admit(self.root)
        self.assertEqual(admission.forecast, (30.0, 2.0))
        self.assertIsNotNone(admission.hold)


class OutstandingAndRetentionTest(TempRootCase):
    def test_two_manual_workers_share_a_repository_cap(self) -> None:
        seed(self.root, five=0)
        limits = Limits(repository_cap_percent=20)
        # repository spend 10
        token = admit(self.root, limits=limits, run_id="x", role="seed").token
        usage.complete(self.root, token, end(10, 1, NOW + 5), usage.OUTCOME_OK, now=NOW + 5)
        first = admit(self.root, limits=limits, run_id="a", now=NOW + 10)
        second = admit(self.root, limits=limits, run_id="b", now=NOW + 10)
        self.assertIsNotNone(first.token)
        self.assertEqual(second.hold.window, "repository_cap")
        self.assertEqual(round(second.hold.percent + second.hold.forecast), 26)
        self.assertTrue(second.hold.outstanding)

    def test_two_workers_share_a_run_cap(self) -> None:
        seed(self.root, five=0)
        limits = Limits(run_cap_percent=10)
        self.assertIsNotNone(admit(self.root, limits=limits).token)
        self.assertEqual(admit(self.root, limits=limits).hold.window, "run_cap")

    def test_complete_replaces_the_liability_by_the_charge_in_one_write(self) -> None:
        seed(self.root, five=0)
        limits = Limits(run_cap_percent=100)
        token = admit(self.root, limits=limits).token
        record = self.record()
        self.assertEqual((usage.run_outstanding(record, "claude", "run-1"), usage.run_spent(record, "claude", "run-1")),
                         (8.0, 0.0))
        usage.complete(self.root, token, end(3, 1, NOW + 5), usage.OUTCOME_OK, now=NOW + 5)
        record = self.record()
        self.assertEqual((usage.run_outstanding(record, "claude", "run-1"), usage.run_spent(record, "claude", "run-1")),
                         (0.0, 3.0))

    def test_the_run_total_survives_a_reset_and_ledger_eviction(self) -> None:
        seed(self.root, five=0)
        token = admit(self.root).token
        usage.complete(self.root, token, end(6, 1, NOW + 5), usage.OUTCOME_OK, now=NOW + 5)
        after = FIVE_RESETS + 10
        record = usage.snapshot(self.root, now=after)
        self.assertEqual(usage.run_spent(record, "claude", "run-1"), 6.0)
        record = self.record()
        record["ledger"] = []
        runtime.write_json(self.root, usage.USAGE_FILE, record)
        self.assertEqual(usage.run_spent(self.record(), "claude", "run-1"), 6.0)

    def test_ledger_retention_by_count_and_age_does_not_move_spend_or_settled(self) -> None:
        record = usage._empty_record()
        record["ledger"] = [{"provider": "claude", "finished_at": NOW - 31 * 86400, "id": "old"}] + [
            {"provider": "claude", "finished_at": NOW, "id": f"e{i}"} for i in range(1005)]
        record["settled"] = {"tok": NOW - 31 * 86400}
        record["spend"]["run"] = {"claude": {"run-1": {"charged": 9.0, "updated_at": NOW}}}
        usage._prune(record, NOW)
        self.assertEqual(len(record["ledger"]), usage.LEDGER_MAX_ENTRIES)
        self.assertNotIn("old", [e["id"] for e in record["ledger"]])
        self.assertIn("tok", record["settled"])
        self.assertEqual(usage.run_spent(record, "claude", "run-1"), 9.0)

    def test_settled_is_kept_ninety_days(self) -> None:
        record = usage._empty_record()
        record["settled"] = {"fresh": NOW - 89 * 86400, "old": NOW - 91 * 86400}
        usage._prune(record, NOW)
        self.assertEqual(set(record["settled"]), {"fresh"})

    def test_idle_run_totals_are_dropped_unless_work_is_outstanding_or_refreshed(self) -> None:
        record = usage._empty_record()
        old = NOW - 91 * 86400
        record["spend"]["run"] = {"claude": {"idle": {"charged": 5.0, "updated_at": old},
                                              "busy": {"charged": 5.0, "updated_at": old},
                                              "fresh": {"charged": 5.0, "updated_at": NOW}}}
        record["reservations"] = {"t": {"provider": "claude", "run_id": "busy"}}
        usage._prune(record, NOW)
        self.assertEqual(set(record["spend"]["run"]["claude"]), {"busy", "fresh"})

    def test_admission_and_renewal_refresh_a_runs_activity(self) -> None:
        seed(self.root, five=0)
        token = admit(self.root, run_id="long").token
        record = self.record()
        record["spend"]["run"]["claude"]["long"]["updated_at"] = NOW - 80 * 86400
        runtime.write_json(self.root, usage.USAGE_FILE, record)
        later = NOW + 20 * 86400
        usage.renew(self.root, token, reservation_seconds=60, now=later)
        self.assertEqual(self.record()["spend"]["run"]["claude"]["long"]["updated_at"], later)

    def test_a_reused_expired_run_id_starts_at_zero(self) -> None:
        record = usage._empty_record()
        record["spend"]["run"] = {"claude": {"run-1": {"charged": 9.0, "updated_at": NOW - 100 * 86400}}}
        usage._prune(record, NOW)
        self.assertEqual(usage.run_spent(record, "claude", "run-1"), 0.0)

    def test_a_new_window_replaces_the_repository_total(self) -> None:
        record = usage._empty_record()
        usage._charge_repository(record, "claude", "/r", FIVE_RESETS, 5.0, NOW)
        usage._charge_repository(record, "claude", "/r", FIVE_RESETS + 30, 2.0, NOW)
        self.assertEqual(usage.repository_spent(record, "claude", "/r", NOW), 7.0)
        usage._charge_repository(record, "claude", "/r", FIVE_RESETS + 5 * H, 1.0, NOW)
        self.assertEqual(usage.repository_spent(record, "claude", "/r", NOW), 1.0)


def job_record(status: str, accounting: str | None, *, launched: bool = False, block: dict | None = None) -> dict:
    record: dict = {"status": status}
    if launched:
        record["expected_transition"] = {}
    if accounting is not None:
        record["usage"] = {"accounting": accounting, **(block or {})}
    return record


TERMINAL = {"FINISHED", "FAILED", "INTERRUPTED", "INCOMPLETE", "GATE_BLOCKED", "DECLINED", "HANDOFF_PENDING"}


class SweepTest(TempRootCase):
    def setUp(self) -> None:
        super().setUp()
        seed(self.root, five=10, weekly=20)
        self.jobs: dict = {}

    def bound(self, job_id: str, record: dict | None, **kw) -> str:
        token = admit(self.root, run_id=job_id, **kw).token
        usage.bind_job(self.root, token, job_id, now=NOW)
        if record is not None:
            self.jobs[job_id] = record
        return token

    def sweep(self, now: float = NOW + 600, never_launched=lambda r: "expected_transition" not in r):
        return usage.settle_pending(self.root, self.jobs.get, never_launched, now=now,
                                    is_terminal=lambda r: r["status"] in TERMINAL)

    def test_a_pending_terminal_record_is_charged_once_even_if_swept_twice(self) -> None:
        block = {"five_hour_end": 16.0, "weekly_end": 21.0, "end_window_resets_at": FIVE_RESETS, "weekly_end_resets_at": WEEK_RESETS,
                 "end_observed_at": NOW + 300}
        token = self.bound("j1", job_record("FINISHED", "pending", launched=True, block=block))
        settled = self.sweep()
        self.assertEqual([(s.token, s.job_id, s.action) for s in settled], [(token, "j1", "charged")])
        self.assertEqual(self.sweep(), [])
        record = self.record()
        self.assertEqual(len(record["ledger"]), 1)
        self.assertEqual((record["ledger"][0]["delta"], usage.run_spent(record, "claude", "j1")), ("measured", 6.0))

    def test_a_crash_during_the_sweep_followed_by_a_second_sweep_charges_once(self) -> None:
        block = {"five_hour_end": 16.0, "weekly_end": 21.0, "end_window_resets_at": FIVE_RESETS}
        token = self.bound("j1", job_record("FAILED", "pending", launched=True, block=block))
        usage.complete(self.root, token, None, usage.OUTCOME_OK, now=NOW + 500, usage_block=self.jobs["j1"]["usage"])
        # the record still says pending (the crash came before "done"): the shared record has no reservation now
        self.assertEqual(self.sweep(), [])
        self.assertEqual(len(self.record()["ledger"]), 1)
        self.assertEqual(usage.token_state(self.root, token, now=NOW + 600), "settled")

    def test_a_never_launched_record_is_released_without_a_charge(self) -> None:
        token = self.bound("j1", job_record("INTERRUPTED", "open"))
        settled = self.sweep()
        self.assertEqual([(s.token, s.action) for s in settled], [(token, "not_started")])
        record = self.record()
        self.assertEqual((record["ledger"], record["settled"], usage.run_spent(record, "claude", "j1")), ([], {}, 0.0))

    def test_an_open_launched_record_is_an_unknown_charge_at_the_forecast(self) -> None:
        self.bound("j1", job_record("INTERRUPTED", "open", launched=True))
        settled = self.sweep()
        self.assertEqual(settled[0].action, "unknown")
        record = self.record()
        self.assertEqual((record["ledger"][0]["delta"], usage.run_spent(record, "claude", "j1")), ("unknown", 8.0))

    def test_dispatch_with_an_injected_predicate(self) -> None:
        a = self.bound("accepted", job_record("FAILED", "open"))
        b = self.bound("rejected", job_record("FAILED", "open", launched=True))
        c = self.bound("running", job_record("LAUNCHED", "open", launched=True))
        settled = {s.job_id: s.action for s in self.sweep(never_launched=lambda r: r is self.jobs["accepted"])}
        self.assertEqual(settled, {"accepted": "not_started", "rejected": "unknown"})
        record = self.record()
        self.assertIn(c, record["reservations"])
        self.assertEqual({e["id"] for e in record["ledger"]}, {b})
        self.assertNotIn(a, record["settled"])

    def test_a_done_record_is_left_alone(self) -> None:
        self.bound("j1", job_record("FINISHED", "done"))
        self.assertEqual(self.sweep(), [])

    def test_a_non_terminal_record_is_left_to_supervision(self) -> None:
        token = self.bound("j1", job_record("COMPLETED", "pending", block={"five_hour_end": 50.0}))
        self.assertEqual(self.sweep(), [])
        self.assertIn(token, self.record()["reservations"])

    def test_a_reservation_bound_to_a_job_with_no_record_is_released_only_once_lapsed(self) -> None:
        token = self.bound("ghost", None)
        self.assertEqual(self.sweep(now=NOW + 60), [])
        self.assertIn(token, self.record()["reservations"])
        later = NOW + 7200 + 10
        settled = self.sweep(now=later)
        self.assertEqual([(s.token, s.action) for s in settled], [(token, "not_started")])
        record = self.record()
        self.assertEqual((record["reservations"], record["lapsed"], record["ledger"]), ({}, {}, []))

    def test_a_lapsed_reservation_is_served_by_the_sweep_with_the_blocks_end_figures(self) -> None:
        block = {"five_hour_end": 14.0, "weekly_end": 21.0, "end_window_resets_at": FIVE_RESETS}
        token = self.bound("j1", job_record("FINISHED", "pending", launched=True, block=block))
        later = NOW + 4 * 3600  # past the 2 h lease, inside the window
        self.assertIn(token, usage.snapshot(self.root, now=later)["lapsed"])
        self.assertEqual(self.sweep(now=later)[0].action, "charged")
        self.assertEqual(self.record()["ledger"][0]["delta"], "measured")

    def test_token_state_dispatch(self) -> None:
        token = self.bound("j1", None)
        self.assertEqual(usage.token_state(self.root, token, now=NOW), "present")
        self.assertEqual(usage.token_state(self.root, "ghost", now=NOW), "orphan")
        usage.complete(self.root, token, None, usage.OUTCOME_OK, now=NOW + 5)
        self.assertEqual(usage.token_state(self.root, token, now=NOW + 6), "settled")

    def test_a_pruned_settled_entry_falls_to_the_no_op_never_a_second_charge(self) -> None:
        block = {"five_hour_end": 16.0, "weekly_end": 21.0, "end_window_resets_at": FIVE_RESETS}
        token = self.bound("j1", job_record("FINISHED", "pending", launched=True, block=block))
        self.sweep()
        late = NOW + 95 * 86400
        usage.renew(self.root, "nobody", reservation_seconds=60, now=late)
        self.assertEqual(usage.token_state(self.root, token, now=late), "orphan")
        result = usage.complete(self.root, token, None, usage.OUTCOME_OK, now=late, usage_block=self.jobs["j1"]["usage"])
        self.assertEqual(result.status, usage.COMPLETE_NOOP)
        self.assertEqual(len(self.record()["ledger"]), 0)  # the 30-day ledger is pruned too; nothing new was written


class SnapshotTest(TempRootCase):
    def test_snapshot_does_not_write(self) -> None:
        seed(self.root)
        admit(self.root, limits=Limits(reservation_seconds=60))
        before = (self.root / usage.USAGE_FILE).read_bytes()
        usage.snapshot(self.root, now=NOW + 600)
        self.assertEqual((self.root / usage.USAGE_FILE).read_bytes(), before)

    def test_load_of_an_absent_record_is_empty(self) -> None:
        self.assertEqual(self.record(), usage._empty_record())


class CodexHomeIsolationTest(unittest.TestCase):
    """The suite's ``CODEX_HOME`` guard (CP3, D1): ``tests/__init__.py``
    points it at an empty temporary directory beside the ``XDG_CONFIG_HOME``
    redirection, so a Codex reading resolved the way the ``usage``
    subcommand resolves it (``$CODEX_HOME``, else ``~/.codex``) never
    reaches the operator's sessions."""

    def test_codex_home_is_the_isolated_directory(self) -> None:
        resolved = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        self.assertEqual(resolved, Path(tests.ISOLATED_CODEX_HOME))
        self.assertNotEqual(resolved.resolve(), (Path.home() / ".codex").resolve())
        self.assertTrue(resolved.is_dir())
        self.assertEqual(list(resolved.iterdir()), [])
        self.assertEqual(usage.read_codex_home(resolved), [])

    def test_a_subprocess_inherits_it(self) -> None:
        self.assertEqual(dict(os.environ)["CODEX_HOME"], tests.ISOLATED_CODEX_HOME)


if __name__ == "__main__":
    unittest.main()
