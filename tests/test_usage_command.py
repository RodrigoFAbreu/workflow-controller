"""Tests for the ``usage`` subcommand and the manual-worker gate
(``workflow-controller-usage-budget`` CP4, plan
``docs/ai-workflow/CONTROLLER_USAGE_BUDGET_PLAN.md`` D8, D9, R8).

``usage`` shows both providers' windows and gives a manual worker (a lane
script, a Codex session) the Controller's forecast-and-reservation gate:
``--check``, ``--wait`` (re-evaluating after every wait), ``--reserve``,
``--renew`` and ``--release`` (optionally measured from a ``--stream``).

Every clock is injected (``job.usage_now``, ``cli._usage_sleep``); the runtime
root, the settings file, the Codex home and the repositories are temporary
directories. No test calls the real ``claude``, reads ``~/.codex`` or the
operator's runtime root.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from controller import cli, job, observe, usage
from tests import fake_claude, fixtures

FIVE, WEEKLY = usage.FIVE_HOUR, usage.WEEKLY
NOW = 1_790_000_000.0
DAY = 86400.0


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _git_repo(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir()
    return fixtures.git_init(path).resolve()


class _UsageCommandCase(unittest.TestCase):
    """A temporary runtime root and settings file, an injected clock that
    ``cli._usage_sleep`` advances, and ``run`` to invoke ``cli.main``."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.runtime_root = self.tmp / "runtime"
        self.settings_path = self.tmp / "settings.json"
        self.codex_home = self.tmp / "codex"
        self.now = NOW
        self.sleeps: list[float] = []
        self.on_sleep = None
        self.enterContext(unittest.mock.patch.object(job, "usage_now", lambda: self.now))
        self.enterContext(unittest.mock.patch.object(cli, "_usage_sleep", self._sleep))
        self.enterContext(unittest.mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}))
        self.repo = _git_repo(self.tmp, "repo")

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            hook, self.on_sleep = self.on_sleep, None
            hook()
        self.now += seconds

    def configure(self, **usage_settings) -> None:
        self.settings_path.write_text(json.dumps({"usage": {"resume_grace_seconds": 1, **usage_settings}}))

    def run_cmd(self, *argv: str) -> tuple[int, str, str]:
        if not self.settings_path.exists():
            self.configure()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--runtime-dir", str(self.runtime_root), "--settings", str(self.settings_path),
                             *argv])
        return code, out.getvalue(), err.getvalue()

    # -- the shared record ---------------------------------------------------

    def shared(self) -> dict:
        return usage.load_record(self.runtime_root)

    def events(self) -> list[dict]:
        return _lines(self.runtime_root / usage.EVENTS_FILE)

    def event_names(self) -> list[str]:
        return [e["event"] for e in self.events()]

    def reading(self, five: float, five_in: float = 3600, weekly: float | None = None,
                weekly_in: float = 7 * DAY, provider: str = "claude", observed: float | None = None) -> None:
        # The resets are offsets from NOW, not from the advancing clock, so a
        # later reading of the same window names the same reset.
        stamp = self.now if observed is None else observed
        readings = [usage.make_reading(provider, FIVE, five, NOW + five_in, stamp, "test")]
        if weekly is not None:
            readings.append(usage.make_reading(provider, WEEKLY, weekly, NOW + weekly_in, stamp, "test"))
        usage.record_reading(self.runtime_root, readings)

    def codex_rollout(self, primary: float, secondary: float, *, five_in: float = 3600,
                      weekly_in: float = 7 * DAY) -> None:
        path = self.codex_home / "sessions" / "2026" / "10" / "10" / "rollout-a.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = "2026-10-10T00:00:00Z"
        path.write_text(json.dumps({"timestamp": stamp, "type": "event_msg", "payload": {
            "type": "token_count", "rate_limits": {
                "primary": {"used_percent": primary, "window_minutes": 300,
                            "resets_at": int(NOW + five_in)},
                "secondary": {"used_percent": secondary, "window_minutes": 10080,
                              "resets_at": int(NOW + weekly_in)}}}}) + "\n")
        os.utime(path, (self.now, self.now))

    def reserve_token(self, *argv: str) -> str:
        code, out, err = self.run_cmd("usage", "--check", "--reserve", *argv)
        self.assertEqual(code, cli.EXIT_OK, err)
        return out.strip()

    def stream_file(self, name: str, *events: dict) -> Path:
        path = self.tmp / name
        path.write_text(fake_claude.stream_text(list(events)))
        os.utime(path, (self.now + 60, self.now + 60))
        return path


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


class UsageParserTest(_UsageCommandCase):

    def refused(self, *argv: str) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            cli.main(["--runtime-dir", str(self.runtime_root), "usage", *argv])
        self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)
        return err.getvalue()

    def test_usage_is_a_declared_command_and_never_pins(self) -> None:
        self.assertIn("usage", cli.ALL_COMMANDS)
        self.assertIn("usage", cli.READ_ONLY_COMMANDS)

    def test_check_and_wait_are_exclusive(self) -> None:
        self.assertIn("not allowed with", self.refused("--check", "--wait"))

    def test_reserve_needs_check_or_wait(self) -> None:
        self.assertIn("--reserve needs --check or --wait", self.refused("--reserve"))

    def test_renew_and_release_stand_alone(self) -> None:
        self.assertIn("mutually exclusive", self.refused("--renew", "a", "--release", "b"))
        self.assertIn("cannot be combined", self.refused("--renew", "a", "--check"))
        self.assertIn("cannot be combined", self.refused("--release", "a", "--wait"))

    def test_stream_and_outcome_belong_to_release(self) -> None:
        self.assertIn("only with --release", self.refused("--stream", "x"))
        self.assertIn("only with --release", self.refused("--outcome", "not_started"))

    def test_a_gate_covers_one_provider(self) -> None:
        self.assertIn("one provider", self.refused("--check", "--provider", "all"))

    def test_the_cap_flags_are_bounded_integers(self) -> None:
        self.assertIn("must be from 1 to 100", self.refused("--usage-cap", "0"))
        self.assertIn("invalid percentage", self.refused("--usage-codex-cap", "x"))

    def test_json_is_accepted_before_or_after_the_subcommand(self) -> None:
        before = self.run_cmd("--json", "usage")
        after = self.run_cmd("usage", "--json")
        self.assertEqual(before[0], cli.EXIT_OK)
        self.assertEqual(json.loads(before[1])["providers"].keys(), json.loads(after[1])["providers"].keys())


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


class UsageShowTest(_UsageCommandCase):

    def test_text_lists_both_providers_windows_reading_age_reservations_and_forecast(self) -> None:
        self.reading(30, weekly=10, observed=self.now - 90)
        self.codex_rollout(12, 4)
        token = self.reserve_token("--role", "implement", "--model", "opus")
        code, out, err = self.run_cmd("usage")
        self.assertEqual((code, err), (cli.EXIT_OK, ""))
        self.assertIn("claude:", out)
        self.assertIn("codex:", out)
        self.assertIn("five-hour: 30% (threshold 85%)", out)
        self.assertIn("weekly: 10% (threshold 95%)", out)
        self.assertIn("read 90 s ago from test", out)
        self.assertIn("five-hour: 12% (threshold 85%)", out)
        self.assertIn("reservations: 1 live, 0 lapsed", out)
        self.assertIn(f"{token[:8]} live implement/opus 8% five-hour", out)
        self.assertIn("forecast: the provider default (no jobs recorded)", out)
        self.assertIn("repository cap: not evaluated (no --repo)", out)
        self.assertIn("run cap: not evaluated (no --run-id)", out)

    def test_json_is_pinned(self) -> None:
        self.reading(30, weekly=10, observed=self.now - 90)
        token = self.reserve_token("--role", "plan", "--model", "m", "--repo", str(self.repo), "--run-id", "r1")
        code, out, _err = self.run_cmd("--json", "usage", "--provider", "claude", "--repo", str(self.repo),
                                       "--run-id", "r1", "--role", "plan", "--model", "m")
        self.assertEqual(code, cli.EXIT_OK)
        view = json.loads(out)
        self.assertEqual(sorted(view), ["at", "providers"])
        self.assertEqual(view["at"], self.now)
        claude = view["providers"]["claude"]
        self.assertEqual(sorted(claude), ["forecast", "forecasts", "repository", "reservations", "run", "thresholds",
                                         "windows"])
        self.assertEqual(claude["windows"][FIVE], {
            "percent": 30, "stored_percent": 30, "resets_at": self.now + 3600, "observed_at": self.now - 90,
            "age_seconds": 90, "source": "test"})
        self.assertEqual(claude["thresholds"], {FIVE: 85, WEEKLY: 95})
        self.assertEqual(claude["forecast"], {FIVE: 8, WEEKLY: 1})
        self.assertEqual(claude["forecasts"], [])
        [reservation] = claude["reservations"]
        self.assertEqual(reservation, {
            "token": token, "state": "live", "role": "plan", "model": "m", "repository": str(self.repo),
            "run_id": "r1", "job_id": None, "expires_at": self.now + 7200, "five_hour": 8, "weekly": 1})
        self.assertEqual(claude["run"], {"run_id": "r1", "cap": None, "spent": 0, "outstanding": 8})
        self.assertEqual(claude["repository"], {"repository": str(self.repo), "cap": None, "spent": 0,
                                                "outstanding": 8})

    def test_a_stored_reading_without_an_observation_time_still_renders(self) -> None:
        # ``load_record`` admits a reading whose ``observed_at`` is absent or
        # null, so the view (the command run to inspect a suspect record) must
        # show the age as unknown, never a traceback.
        for shape in ("absent", "null"):
            with self.subTest(shape=shape):
                self.reading(30, weekly=10)
                path = self.runtime_root / usage.USAGE_FILE
                record = json.loads(path.read_text())
                reading = record["readings"]["claude"][FIVE]
                if shape == "absent":
                    del reading["observed_at"]
                else:
                    reading["observed_at"] = None
                path.write_text(json.dumps(record))
                usage.load_record(self.runtime_root)
                code, out, err = self.run_cmd("--json", "usage", "--provider", "claude")
                self.assertEqual(code, cli.EXIT_OK, err)
                window = json.loads(out)["providers"]["claude"]["windows"][FIVE]
                self.assertEqual((window["observed_at"], window["age_seconds"]), (None, None))
                code, text, err = self.run_cmd("usage", "--provider", "claude")
                self.assertEqual(code, cli.EXIT_OK, err)
                self.assertIn("read at an unknown time from test", text)

    def test_provider_selects_one_provider(self) -> None:
        code, out, _err = self.run_cmd("--json", "usage", "--provider", "codex")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(list(json.loads(out)["providers"]), ["codex"])

    def test_the_forecast_per_role_and_model_comes_from_the_ledger(self) -> None:
        self.reading(10, weekly=1)
        token = self.reserve_token("--role", "plan", "--model", "m")
        self.now += 600
        self.reading(16, weekly=2)
        self.assertEqual(self.run_cmd("usage", "--release", token)[0], cli.EXIT_OK)
        code, out, _err = self.run_cmd("--json", "usage", "--provider", "claude")
        [row] = json.loads(out)["providers"]["claude"]["forecasts"]
        self.assertEqual((row["role"], row["model"], row["five_hour"], row["weekly"], row["samples"]),
                         ("plan", "m", 6, 1, 1))
        _c, text, _e = self.run_cmd("usage", "--provider", "claude")
        self.assertIn("plan/m: 6.0% five-hour, 1.0% weekly from 1 measured jobs", text)

    def test_the_view_writes_only_the_shared_record(self) -> None:
        self.run_cmd("usage")
        names = sorted(p.name for p in self.runtime_root.iterdir() if p.name != "usage.lock")
        self.assertEqual([n for n in names if n not in ("usage.json",)], [])

    def test_the_default_codex_home_is_never_the_operators(self) -> None:
        self.assertEqual(cli._codex_home(), self.codex_home)
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_HOME")
            self.assertEqual(cli._codex_home(), Path.home() / ".codex")


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


class UsageUncalendarTimeTest(_UsageCommandCase):
    """An accepted timestamp beyond the calendar shows as the bare epoch,
    never a traceback, in the view, the check and the hold message."""

    def test_a_timestamp_at_either_end_renders_as_the_epoch(self) -> None:
        for resets_at in (usage._NUMBER_LIMIT, -usage._NUMBER_LIMIT):
            with self.subTest(resets_at=resets_at):
                (self.runtime_root / usage.USAGE_FILE).unlink(missing_ok=True)
                usage.record_reading(self.runtime_root, [
                    usage.make_reading("claude", FIVE, 95, resets_at, self.now, "test")])
                self.assertEqual(self.shared()["readings"]["claude"][FIVE]["resets_at"], resets_at)
                code, out, err = self.run_cmd("usage")
                self.assertEqual((code, err), (cli.EXIT_OK, ""))
                self.assertIn(f"epoch {resets_at}", out)
                code, out, err = self.run_cmd("usage", "--json")
                self.assertEqual((code, err), (cli.EXIT_OK, ""))
                code, out, err = self.run_cmd("usage", "--check", "--provider", "claude")
                self.assertNotIn("Traceback", err)

    def test_the_epoch_text_never_raises(self) -> None:
        for epoch in (usage._NUMBER_LIMIT, -usage._NUMBER_LIMIT, 0):
            self.assertIn(f"epoch {epoch}", cli._local_time(epoch))
            self.assertIn(f"epoch {epoch}", observe._epoch_text(epoch))


class UsageCheckTest(_UsageCommandCase):

    def test_check_exits_zero_on_go_and_reserves_nothing(self) -> None:
        self.reading(10)
        code, out, err = self.run_cmd("usage", "--check")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(out.strip(), "go")
        self.assertIn("repository cap not evaluated (no --repo)", err)
        self.assertIn("run cap not evaluated (no --run-id)", err)
        self.assertEqual(self.shared()["reservations"], {})
        self.assertEqual(self.event_names(), [])

    def test_check_exits_17_on_a_claude_hold(self) -> None:
        self.reading(80, five_in=3600)
        code, out, err = self.run_cmd("usage", "--check")
        self.assertEqual((code, out), (cli.EXIT_USAGE_PAUSED, ""))
        self.assertIn("a timed pause: run again after the resume time", err)
        self.assertIn(f"(epoch {int(self.now + 3600)})", err)
        self.assertEqual(self.event_names(), ["usage_paused"])

    def test_check_exits_17_on_a_codex_hold_and_go_on_the_claude_side(self) -> None:
        self.reading(10)
        self.codex_rollout(80, 4)
        self.assertEqual(self.run_cmd("usage", "--check", "--provider", "codex")[0], cli.EXIT_USAGE_PAUSED)
        self.assertEqual(self.run_cmd("usage", "--check", "--provider", "claude")[0], cli.EXIT_OK)
        self.assertEqual(self.run_cmd("usage", "--check")[0], cli.EXIT_OK)
        [paused] = self.events()
        self.assertEqual((paused["event"], paused["provider"], paused["window"]), ("usage_paused", "codex", FIVE))

    def test_a_codex_go_when_its_reading_is_low(self) -> None:
        self.codex_rollout(10, 4)
        self.assertEqual(self.run_cmd("usage", "--check", "--provider", "codex")[0], cli.EXIT_OK)
        self.assertEqual(self.shared()["readings"]["codex"][FIVE]["percent"], 10)

    def test_a_missing_reading_does_not_hold_and_is_reported(self) -> None:
        code, _out, err = self.run_cmd("usage", "--check")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no reading: claude five_hour", err)

    def test_json_check_is_pinned(self) -> None:
        self.reading(80, five_in=3600)
        code, out, _err = self.run_cmd("--json", "usage", "--check")
        payload = json.loads(out)
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertEqual(sorted(payload), ["decision", "forecast", "hold", "message", "notes", "provider", "token",
                                           "waited_seconds"])
        self.assertEqual((payload["decision"], payload["provider"], payload["token"]), ("hold", "claude", None))
        self.assertEqual(payload["forecast"], {FIVE: 8, WEEKLY: 1})
        self.assertEqual((payload["hold"]["window"], payload["hold"]["waitable"], payload["hold"]["resume_at"]),
                         (FIVE, True, self.now + 3600))
        self.assertIn("a timed pause", payload["message"])
        _c, ok, _e = self.run_cmd("--json", "usage", "--check", "--provider", "codex")
        self.assertEqual(json.loads(ok)["decision"], "go")

    def test_check_reserve_prints_only_the_token_on_stdout(self) -> None:
        self.reading(10)
        code, out, _err = self.run_cmd("usage", "--check", "--reserve", "--role", "r", "--model", "m")
        self.assertEqual(code, cli.EXIT_OK)
        [token] = self.shared()["reservations"]
        self.assertEqual(out, token + "\n")
        reservation = self.shared()["reservations"][token]
        self.assertEqual((reservation["role"], reservation["model"], reservation["provider"]), ("r", "m", "claude"))

    def test_disabled_budget_evaluates_and_reserves_nothing(self) -> None:
        self.configure(enabled=False)
        self.reading(99)
        code, out, err = self.run_cmd("usage", "--check", "--reserve")
        self.assertEqual((code, out), (cli.EXIT_OK, ""))
        self.assertIn("usage.enabled is false", err)
        self.assertEqual(self.shared()["reservations"], {})

    def test_a_held_check_reserves_nothing(self) -> None:
        self.reading(80)
        self.run_cmd("usage", "--check", "--reserve")
        self.assertEqual(self.shared()["reservations"], {})


# ---------------------------------------------------------------------------
# --wait
# ---------------------------------------------------------------------------


class UsageWaitTest(_UsageCommandCase):

    def test_wait_sleeps_to_the_resume_time_plus_grace_then_exits_zero_and_resumes(self) -> None:
        self.reading(80, five_in=5)
        start = self.now
        code, out, err = self.run_cmd("usage", "--wait")
        self.assertEqual((code, out.strip()), (cli.EXIT_OK, "go"))
        self.assertEqual(sum(self.sleeps), 6)
        self.assertTrue(all(s <= cli.USAGE_WAIT_SLICE_SECONDS for s in self.sleeps))
        self.assertEqual(self.now, start + 6)
        self.assertEqual(self.event_names(), ["usage_paused", "usage_resumed"])
        self.assertIn("waiting until", err)

    def test_wait_re_evaluates_after_the_sleep_and_waits_again(self) -> None:
        self.reading(80, five_in=5)
        start = self.now
        # While it sleeps another lane's job consumes the next window to 90%
        # and resets in 15 s: the first wake-up is held again.
        self.on_sleep = lambda: self.reading(90, five_in=15)
        code, out, _err = self.run_cmd("usage", "--wait")
        self.assertEqual((code, out.strip()), (cli.EXIT_OK, "go"))
        self.assertEqual(self.now, start + 16)
        self.assertEqual(self.event_names(), ["usage_paused", "usage_paused", "usage_resumed"])

    def test_usage_resumed_is_never_emitted_for_a_wake_up_that_is_held_again(self) -> None:
        self.reading(80, five_in=5)
        self.on_sleep = lambda: self.reading(90, five_in=15)
        self.configure(max_wait_seconds=12)
        code, _out, err = self.run_cmd("usage", "--wait")
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("usage.max_wait_seconds, 12 s", err)
        self.assertEqual(self.event_names(), ["usage_paused", "usage_paused"])

    def test_a_wait_beyond_max_wait_seconds_exits_17_without_sleeping(self) -> None:
        self.configure(max_wait_seconds=60)
        self.reading(80, five_in=3600)
        code, _out, err = self.run_cmd("usage", "--wait")
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertEqual(self.sleeps, [])
        self.assertIn("usage.max_wait_seconds, 60 s", err)
        self.assertEqual(self.event_names(), ["usage_paused"])

    def test_a_cap_exits_17_at_once_and_says_so(self) -> None:
        code, _out, err = self.run_cmd("usage", "--wait", "--usage-cap", "5", "--run-id", "r1")
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertEqual(self.sleeps, [])
        self.assertIn("no reset will lift this; raise usage.run_cap_percent or start a new run", err)
        [paused] = self.events()
        self.assertEqual((paused["window"], paused["run_id"], paused["resume_at"]), ("run_cap", "r1", None))

    def test_wait_reserve_prints_a_token_written_in_the_final_admission(self) -> None:
        self.reading(80, five_in=5)
        code, out, _err = self.run_cmd("usage", "--wait", "--reserve")
        self.assertEqual(code, cli.EXIT_OK)
        [token] = self.shared()["reservations"]
        self.assertEqual(out, token + "\n")
        resumed = next(e for e in self.events() if e["event"] == "usage_resumed")
        self.assertEqual(resumed["token"], token)

    def test_wait_reserve_while_another_lane_holds_the_headroom_waits_for_its_release(self) -> None:
        self.reading(70, five_in=3600)
        other = self.reserve_token("--role", "other")
        self.assertEqual(self.run_cmd("usage", "--check", "--reserve")[0], cli.EXIT_USAGE_PAUSED)

        def other_finishes() -> None:
            self.assertEqual(self.run_cmd("usage", "--release", other, "--outcome", "not_started")[0], cli.EXIT_OK)

        # Held by the other lane's reservation on a window that resets in an
        # hour; the other lane releases during the sleep, so the re-evaluation
        # at the wake-up (after the reset) goes.
        self.configure(max_wait_seconds=7200)
        self.on_sleep = other_finishes
        code, out, _err = self.run_cmd("usage", "--wait", "--reserve")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(len(self.shared()["reservations"]), 1)
        self.assertEqual(out.strip(), next(iter(self.shared()["reservations"])))

    def test_ctrl_c_during_a_wait_is_one_line_and_reserves_nothing(self) -> None:
        self.reading(80, five_in=5)

        def interrupt(_seconds: float) -> None:
            raise KeyboardInterrupt

        with unittest.mock.patch.object(cli, "_usage_sleep", interrupt):
            code, _out, err = self.run_cmd("usage", "--wait", "--reserve")
        self.assertEqual(code, cli.SIGINT_EXIT_STATUS)
        self.assertIn("interrupted while paused for the usage budget", err)
        self.assertEqual(self.shared()["reservations"], {})

    def test_two_concurrent_reserving_callers_cannot_both_pass(self) -> None:
        self.reading(70, five_in=3600)
        self.configure()
        results: list[tuple[int, str]] = []
        barrier = threading.Barrier(2)
        out, err = io.StringIO(), io.StringIO()

        def caller() -> None:
            barrier.wait()
            code = cli.main(["--runtime-dir", str(self.runtime_root), "--settings", str(self.settings_path),
                             "usage", "--check", "--reserve"])
            results.append((code, ""))

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            threads = [threading.Thread(target=caller) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(sorted(code for code, _ in results), [cli.EXIT_OK, cli.EXIT_USAGE_PAUSED])
        self.assertEqual(len(self.shared()["reservations"]), 1)


# ---------------------------------------------------------------------------
# Context: role, model, repository, run; independent provider caps
# ---------------------------------------------------------------------------


class UsageContextTest(_UsageCommandCase):

    def test_the_forecast_is_chosen_by_role_and_model(self) -> None:
        self.reading(10, weekly=1)
        level = 10
        for role, delta in (("small", 2), ("big", 40)):
            token = self.reserve_token("--role", role, "--model", "m")
            self.now += 60
            level += delta
            self.reading(level, weekly=2)
            self.run_cmd("usage", "--release", token)
        record = self.shared()
        self.assertEqual({e["role"]: e["five_hour"] for e in record["ledger"]}, {"small": 2, "big": 40})
        code, out, _err = self.run_cmd("--json", "usage", "--check", "--role", "big", "--model", "m")
        self.assertEqual(json.loads(out)["forecast"][FIVE], 40)
        code, out, _err = self.run_cmd("--json", "usage", "--check", "--role", "small", "--model", "m")
        self.assertEqual(json.loads(out)["forecast"][FIVE], 2)
        _c, out, _e = self.run_cmd("--json", "usage", "--check", "--role", "other", "--model", "m")
        self.assertEqual(json.loads(out)["forecast"][FIVE], 8)

    def test_a_repository_cap_needs_repo_and_counts_outstanding_work_of_the_same_repository(self) -> None:
        self.configure(repository_cap_percent=20)
        self.reading(10)
        first = self.reserve_token("--repo", str(self.repo))
        self.assertTrue(first)
        # 0 spent + 8 outstanding + 8 forecast = 16 <= 20: a second is admitted.
        self.assertTrue(self.reserve_token("--repo", str(self.repo)))
        # 16 outstanding + 8 = 24 > 20: held, and it says outstanding work.
        code, _out, err = self.run_cmd("usage", "--check", "--repo", str(self.repo))
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("exceeds the repository cap 20%", err)
        self.assertIn("outstanding work", err)
        # Another repository, and no --repo, are not held by this cap.
        other = _git_repo(self.tmp, "other")
        self.assertEqual(self.run_cmd("usage", "--check", "--repo", str(other))[0], cli.EXIT_OK)
        code, _out, err = self.run_cmd("usage", "--check")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("repository cap not evaluated (no --repo)", err)

    def test_an_unreleased_token_that_lapsed_still_counts_against_the_repository_cap(self) -> None:
        self.configure(repository_cap_percent=12, reservation_seconds=60)
        self.reading(10)
        self.reserve_token("--repo", str(self.repo))
        self.now += 600  # the lease lapsed; the holder never came back
        code, _out, err = self.run_cmd("usage", "--check", "--repo", str(self.repo))
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("outstanding work", err)
        self.assertEqual(len(self.shared()["lapsed"]), 1)

    def test_two_workers_on_one_run_cannot_both_pass_the_run_cap(self) -> None:
        self.reading(10)
        self.reserve_token("--run-id", "r1", "--usage-cap", "10")
        code, _out, err = self.run_cmd("usage", "--check", "--run-id", "r1", "--usage-cap", "10")
        self.assertEqual(code, cli.EXIT_USAGE_PAUSED)
        self.assertIn("run cap 10%", err)
        # Another run, and an invocation naming no run, are not held.
        self.assertEqual(self.run_cmd("usage", "--check", "--run-id", "r2", "--usage-cap", "10")[0], cli.EXIT_OK)
        code, _out, err = self.run_cmd("usage", "--check", "--usage-cap", "10")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("run cap not evaluated (no --run-id)", err)

    def test_the_provider_caps_are_independent(self) -> None:
        self.reading(10)
        self.codex_rollout(10, 4)
        self.reserve_token("--run-id", "r1", "--usage-cap", "10")
        # Claude's run is at its cap; Codex's spend and cap are its own.
        self.assertEqual(self.run_cmd("usage", "--check", "--run-id", "r1", "--usage-cap", "10")[0],
                         cli.EXIT_USAGE_PAUSED)
        self.assertEqual(self.run_cmd("usage", "--check", "--provider", "codex", "--run-id", "r1",
                                      "--usage-cap", "10")[0], cli.EXIT_OK)
        self.assertEqual(self.run_cmd("usage", "--check", "--provider", "codex", "--run-id", "r1",
                                      "--usage-codex-cap", "5")[0], cli.EXIT_USAGE_PAUSED)
        code, _out, err = self.run_cmd("usage", "--check", "--provider", "codex", "--run-id", "r1",
                                       "--usage-codex-cap", "5")
        self.assertIn("the run's codex spend", err)

    def test_the_codex_settings_govern_the_codex_gate(self) -> None:
        self.configure(codex_pause_at_percent=20)
        self.codex_rollout(15, 4)
        self.reading(15)
        self.assertEqual(self.run_cmd("usage", "--check", "--provider", "codex")[0], cli.EXIT_USAGE_PAUSED)
        self.assertEqual(self.run_cmd("usage", "--check", "--provider", "claude")[0], cli.EXIT_OK)

    def test_a_repo_that_is_not_a_repository_is_refused(self) -> None:
        code, _out, err = self.run_cmd("usage", "--check", "--repo", str(self.tmp))
        self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
        self.assertIn("not a Git repository", err)


# ---------------------------------------------------------------------------
# --renew and --release
# ---------------------------------------------------------------------------


class UsageReleaseTest(_UsageCommandCase):

    def test_a_stream_is_refused_for_a_codex_token(self) -> None:
        self.codex_rollout(10, 1)
        token = self.reserve_token("--provider", "codex", "--role", "plan", "--model", "m")
        stream = self.stream_file("s.jsonl", *fake_claude.default_events())
        code, _out, err = self.run_cmd("usage", "--release", token, "--stream", str(stream))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("is a codex reservation", err)
        self.assertIn(token, self.shared()["reservations"])

    def test_release_records_the_ledger_entry_and_spend_and_is_idempotent(self) -> None:
        self.reading(10, weekly=1)
        token = self.reserve_token("--role", "plan", "--model", "m", "--repo", str(self.repo), "--run-id", "r1")
        self.now += 120
        self.reading(15, weekly=2)
        code, out, _err = self.run_cmd("usage", "--release", token)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("released; measured delta, charged 5.0%", out)
        record = self.shared()
        [entry] = record["ledger"]
        self.assertEqual((entry["id"], entry["delta"], entry["five_hour"], entry["charged"]),
                         (token, "measured", 5, 5))
        self.assertEqual(record["spend"]["run"]["claude"]["r1"]["charged"], 5)
        self.assertEqual(record["reservations"], {})
        self.assertIn(token, record["settled"])
        # A second release changes nothing and still exits 0.
        code, out, _err = self.run_cmd("usage", "--release", token)
        self.assertEqual((code, out.strip()), (cli.EXIT_OK, "already accounted; nothing changed"))
        self.assertEqual(self.shared(), record)
        self.assertEqual(self.event_names().count("usage_released"), 1)

    def test_a_release_with_no_newer_reading_is_unknown_and_charged_at_least_the_forecast(self) -> None:
        self.reading(10, weekly=1)
        token = self.reserve_token("--role", "plan", "--model", "m", "--run-id", "r1")
        self.now += 120
        code, out, _err = self.run_cmd("usage", "--release", token)
        self.assertEqual(code, cli.EXIT_OK)
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry["five_hour"], entry["charged"]), ("unknown", None, 8))
        self.assertIn("unknown delta, charged 8.0%", out)
        self.assertEqual(self.shared()["spend"]["run"]["claude"]["r1"]["charged"], 8)
        # Excluded from the forecast mean: the role keeps the provider default.
        _c, check, _e = self.run_cmd("--json", "usage", "--check", "--role", "plan", "--model", "m")
        self.assertEqual(json.loads(check)["forecast"][FIVE], 8)

    def test_release_with_a_stream_measures_the_workers_own_readings(self) -> None:
        self.reading(10, weekly=1)
        token = self.reserve_token("--role", "plan", "--model", "m")
        stream = self.stream_file(
            "worker.stdout", fake_claude.rate_limit(10, self.now + 3600, 1, self.now + 7 * DAY),
            fake_claude.rate_limit(19, self.now + 3600, 2, self.now + 7 * DAY))
        code, out, _err = self.run_cmd("usage", "--release", token, "--stream", str(stream))
        self.assertEqual(code, cli.EXIT_OK, out)
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry["five_hour"], entry["weekly"], entry["charged"]),
                         ("measured", 9, 1, 9))
        released = next(e for e in self.events() if e["event"] == "usage_released")
        self.assertEqual((released["percent"], released["forecast"], released["reason"]), (9, 8, "measured"))

    def test_a_missing_stream_is_refused_and_accounts_nothing(self) -> None:
        self.reading(10)
        token = self.reserve_token()
        code, _out, err = self.run_cmd("usage", "--release", token, "--stream", str(self.tmp / "absent"))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("is not a readable file", err)
        self.assertIn(token, self.shared()["reservations"])

    def test_release_on_a_lapsed_token_still_records(self) -> None:
        self.configure(reservation_seconds=60)
        self.reading(10, weekly=1)
        token = self.reserve_token("--run-id", "r1")
        self.now += 600
        self.reading(14, weekly=1)
        self.assertEqual(self.run_cmd("usage", "--release", token)[0], cli.EXIT_OK)
        record = self.shared()
        self.assertEqual(record["lapsed"], {})
        [entry] = record["ledger"]
        self.assertEqual((entry["id"], entry["delta"], entry["charged"]), (token, "measured", 4))
        self.assertIn("usage_reservation_expired", self.event_names())
        self.assertEqual(self.event_names().count("usage_released"), 1)

    def test_release_after_a_five_hour_reset_is_after_reset_and_charged_at_least_the_forecast(self) -> None:
        self.reading(10, five_in=3600, weekly=1)
        token = self.reserve_token("--run-id", "r1")
        self.now += 4000  # the window reset; the worker consumed 3% of the new one
        self.reading(3, five_in=3600 + 5 * 3600, weekly=2)
        self.assertEqual(self.run_cmd("usage", "--release", token)[0], cli.EXIT_OK)
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry["five_hour"], entry["five_hour_after_reset"], entry["charged"]),
                         ("after_reset", None, 3, 8))
        self.assertEqual(self.shared()["spend"]["run"]["claude"]["r1"]["charged"], 8)

    def test_not_started_releases_without_a_charge_or_ledger_entry(self) -> None:
        self.reading(10)
        token = self.reserve_token("--run-id", "r1")
        code, out, _err = self.run_cmd("usage", "--release", token, "--outcome", "not_started")
        self.assertEqual((code, out.strip()), (cli.EXIT_OK, "released; nothing charged"))
        record = self.shared()
        self.assertEqual((record["reservations"], record["ledger"], record["settled"]), ({}, [], {}))
        # Admission refreshed the run's activity; nothing was charged.
        self.assertEqual(record["spend"]["run"]["claude"]["r1"]["charged"], 0)
        released = next(e for e in self.events() if e["event"] == "usage_released")
        self.assertEqual((released["token"], released["reason"], released["run_id"]), (token, "not_started", "r1"))

    def test_an_unknown_token_exits_1_and_changes_nothing(self) -> None:
        self.reading(10)
        before = self.shared()
        code, _out, err = self.run_cmd("usage", "--release", "nosuchtoken")
        self.assertEqual(code, cli.EXIT_TOKEN_UNKNOWN)
        self.assertIn("the token nosuchtoken is unknown; nothing was accounted", err)
        self.assertEqual(self.shared()["ledger"], before["ledger"])
        self.assertNotIn("usage_released", self.event_names())

    def test_a_codex_release_measures_from_the_codex_home(self) -> None:
        self.codex_rollout(10, 4)
        token = self.reserve_token("--provider", "codex", "--role", "review", "--model", "gpt")
        self.now += 120
        self.codex_rollout(16, 5)
        self.assertEqual(self.run_cmd("usage", "--release", token)[0], cli.EXIT_OK)
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["provider"], entry["delta"], entry["five_hour"], entry["weekly"]),
                         ("codex", "measured", 6, 1))

    def test_an_unreleased_token_lapses_and_is_abandoned_at_its_forecast(self) -> None:
        self.configure(reservation_seconds=60)
        self.reading(10)
        token = self.reserve_token("--run-id", "r1")
        self.now += 8 * DAY
        self.reading(10, five_in=8 * DAY + 3600)
        self.run_cmd("usage", "--check")
        record = self.shared()
        self.assertEqual((record["reservations"], record["lapsed"]), ({}, {}))
        [entry] = record["ledger"]
        self.assertEqual((entry["id"], entry["delta"], entry["charged"]), (token, "abandoned", 8))
        self.assertEqual(self.run_cmd("usage", "--release", token)[0], cli.EXIT_OK)
        self.assertEqual(len(self.shared()["ledger"]), 1)


class UsageRenewTest(_UsageCommandCase):

    def test_renew_keeps_a_long_workers_reservation_past_its_lease(self) -> None:
        self.configure(reservation_seconds=100)
        self.reading(70, five_in=7 * 3600)
        token = self.reserve_token()
        for _ in range(5):
            self.now += 60
            code, out, _err = self.run_cmd("usage", "--renew", token)
            self.assertEqual((code, out.strip()), (cli.EXIT_OK, usage.RENEWED))
        self.assertIn(token, self.shared()["reservations"])
        self.assertEqual(self.shared()["reservations"][token]["expires_at"], self.now + 100)
        # A second lane is still held on the headroom the long worker holds.
        self.assertEqual(self.run_cmd("usage", "--check")[0], cli.EXIT_USAGE_PAUSED)

    def test_without_renewal_the_reservation_lapses(self) -> None:
        self.configure(reservation_seconds=100)
        self.reading(70, five_in=7 * 3600)
        token = self.reserve_token()
        self.now += 300
        self.assertEqual(self.run_cmd("usage", "--check")[0], cli.EXIT_OK)
        self.assertIn(token, self.shared()["lapsed"])

    def test_renew_revives_a_lapsed_reservation(self) -> None:
        self.configure(reservation_seconds=100)
        self.reading(70, five_in=7 * 3600)
        token = self.reserve_token()
        self.now += 300
        self.run_cmd("usage", "--check")
        code, out, _err = self.run_cmd("usage", "--renew", token)
        self.assertEqual((code, out.strip()), (cli.EXIT_OK, usage.REVIVED))
        record = self.shared()
        self.assertIn(token, record["reservations"])
        self.assertNotIn(token, record["lapsed"])
        self.assertEqual(self.run_cmd("usage", "--check")[0], cli.EXIT_USAGE_PAUSED)

    def test_renew_of_an_unknown_or_settled_token_exits_1_with_the_message(self) -> None:
        self.reading(10)
        token = self.reserve_token()
        self.run_cmd("usage", "--release", token)
        for named in ("nosuchtoken", token):
            code, _out, err = self.run_cmd("usage", "--renew", named)
            self.assertEqual(code, cli.EXIT_TOKEN_UNKNOWN)
            self.assertIn(f"the token {named} is unknown or already accounted", err)
            self.assertIn("`usage --release` cannot account an unknown manual token", err)
        self.assertNotIn(token, self.shared()["reservations"])
        code, out, _err = self.run_cmd("--json", "usage", "--renew", "nosuchtoken")
        self.assertEqual((code, json.loads(out)["result"]), (cli.EXIT_TOKEN_UNKNOWN, usage.UNKNOWN))


# ---------------------------------------------------------------------------
# The durable event file
# ---------------------------------------------------------------------------


class UsageEventsTest(_UsageCommandCase):

    FIELDS = ["at", "event", "forecast", "percent", "provider", "reason", "repository", "resume_at", "run_id",
              "token", "window"]

    def test_paused_resumed_and_released_carry_the_documented_fields(self) -> None:
        self.reading(80, five_in=5)
        token = self.reserve_token("--repo", str(self.repo), "--run-id", "r1") if False else None
        code, out, _err = self.run_cmd("usage", "--wait", "--reserve", "--repo", str(self.repo), "--run-id", "r1")
        self.assertEqual(code, cli.EXIT_OK)
        token = out.strip()
        self.now += 60
        self.reading(12, five_in=3600)
        self.run_cmd("usage", "--release", token)
        paused, resumed, released = self.events()
        for event in (paused, resumed, released):
            self.assertEqual(sorted(event), self.FIELDS)
        self.assertEqual((paused["event"], paused["provider"], paused["window"], paused["percent"],
                          paused["forecast"], paused["resume_at"], paused["repository"], paused["run_id"],
                          paused["token"]),
                         ("usage_paused", "claude", FIVE, 80, 8, NOW + 5, str(self.repo), "r1", None))
        self.assertIn("reaches the 85% threshold", paused["reason"])
        self.assertEqual((resumed["event"], resumed["token"], resumed["repository"], resumed["run_id"]),
                         ("usage_resumed", token, str(self.repo), "r1"))
        self.assertEqual((released["event"], released["token"], released["repository"], released["run_id"]),
                         ("usage_released", token, str(self.repo), "r1"))
        self.assertEqual(paused["at"], "2026-09-21T14:13:20+00:00")

    def test_usage_resumed_follows_the_final_go_only(self) -> None:
        self.reading(80, five_in=5)
        seen: list[list[str]] = []
        self.on_sleep = lambda: seen.append(self.event_names())
        self.run_cmd("usage", "--wait")
        self.assertEqual(seen, [["usage_paused"]])
        self.assertEqual(self.event_names(), ["usage_paused", "usage_resumed"])

    def test_a_check_that_goes_after_no_hold_emits_no_event(self) -> None:
        self.reading(10)
        self.run_cmd("usage", "--check", "--reserve")
        self.assertEqual(self.event_names(), [])


class AdmitWithoutReservingTest(unittest.TestCase):

    def test_reserve_false_is_a_go_without_a_token_and_writes_no_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage.record_reading(root, [usage.make_reading("claude", FIVE, 10, NOW + 3600, NOW, "t")])
            admission = usage.admit(root, provider="claude", role=None, model=None, repository=None, run_id=None,
                                    limits=usage.Limits(), now=NOW, reserve=False)
            self.assertEqual((admission.token, admission.hold), (None, None))
            self.assertEqual(usage.load_record(root)["reservations"], {})
            reserved = usage.admit(root, provider="claude", role=None, model=None, repository=None, run_id=None,
                                   limits=usage.Limits(), now=NOW)
            self.assertIsNotNone(reserved.token)


if __name__ == "__main__":
    unittest.main()
