"""Contract tests for ``controller.notify``
(``workflow-controller-signalhub-notifications`` CP1). No real network and no
real home: every transport is ``tests.fake_signalhub`` on 127.0.0.1, every
path a temporary directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import tests
from controller import notify
from controller.notify import (
    CONFLICT, DISABLED, DROPPED, DUPLICATE, FAILED, NOT_CONFIGURED, REJECTED, SENT, SKIPPED,
    Credentials, NotifierConfig,
)
from tests.fake_signalhub import FakeSignalHub, Step

REPO = Path(__file__).resolve().parent.parent
CANARY = "shpk1_CANARY-do-not-log-0123456789"


def make_event(ident: str = "one", event_type: str = "step_done", **kwargs) -> notify.Event:
    return notify.build_event(event_type, repository="repo", ident=ident, title=f"title {ident}", **kwargs)


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.root = self.tmp / "runtime"
        self.root.mkdir()
        self.hub = FakeSignalHub().start()
        self.addCleanup(self.hub.stop)
        self.sleeps: list[float] = []

    def notifier(self, *, key: str | None = CANARY, url: str | None = None, **config) -> notify.Notifier:
        cred = Credentials(url=url or self.hub.url, url_source="test", key=key, key_source="test") if key else \
            Credentials(url=url or self.hub.url, url_source="test", tried=["a", "b"])
        return notify.Notifier(NotifierConfig(**config), runtime_root=self.root, credentials=cred,
                               sleeper=self.sleeps.append, clock=lambda: "2026-10-12T00:00:00Z")

    def lines(self, run_id: str | None = None) -> list[dict]:
        path = self.root / (f"runs/{run_id}/notifications.jsonl" if run_id else "notifications.jsonl")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def statuses(self, run_id: str | None = None) -> list[str]:
        return [line["status"] for line in self.lines(run_id)]

    def send(self, notifier: notify.Notifier, *events: notify.Event, **kwargs) -> None:
        for event in events:
            notifier.emit(event, **kwargs)
        self.assertTrue(notifier.flush(10))


class ResolutionTest(unittest.TestCase):
    def test_every_event_has_a_kind_default(self) -> None:
        expect = {"state": ("INFO", "LOW"), "info": ("INFO", "NORMAL"),
                  "gate": ("ACTION_REQUIRED", "HIGH"), "blocker": ("BLOCKED", "CRITICAL")}
        for name, kind in notify.EVENT_TABLE.items():
            r = notify.resolve(name)
            self.assertEqual((r.category, r.severity), expect[kind], name)
            self.assertEqual(r.enabled, name not in ("usage_released", "usage_reservation_expired"), name)

    def test_event_entry_over_kind_entry_over_default_field_by_field(self) -> None:
        clean, ignored = notify.validate_events_mapping({
            "kinds": {"state": {"severity": "high"}, "info": {"enabled": False}},
            "events": {"step_done": {"category": "COMPLETED"}, "milestone_accepted": {"enabled": True, "severity": "CRITICAL"}},
        })
        self.assertEqual(ignored, [])
        r = notify.resolve("step_done", clean)
        self.assertEqual((r.enabled, r.category, r.severity), (True, "COMPLETED", "HIGH"))
        self.assertEqual(r.sources, {"enabled": "default", "category": "event", "severity": "kind"})
        r = notify.resolve("milestone_accepted", clean)
        self.assertEqual((r.enabled, r.category, r.severity), (True, "INFO", "CRITICAL"))
        self.assertFalse(notify.resolve("pr_merged", clean).enabled)
        self.assertTrue(notify.resolve("usage_released", {"events": {"usage_released": {"enabled": True}}}).enabled)

    def test_bad_known_value_is_an_error_and_unknown_names_are_ignored(self) -> None:
        for bad in ({"kinds": {"state": {"severity": "LOUD"}}}, {"events": {"step_done": {"enabled": "yes"}}},
                    {"events": {"step_done": {"category": 3}}}, {"events": []}, "x"):
            with self.assertRaises(notify.EventsMappingError, msg=bad):
                notify.validate_events_mapping(bad)
        clean, ignored = notify.validate_events_mapping({
            "events": {"no_such_event": {"enabled": False}, "step_done": {"colour": "red"}},
            "kinds": {"weird": {}}, "extra": 1})
        self.assertEqual(sorted(ignored), ["events.no_such_event", "events.step_done.colour", "extra", "kinds.weird"])
        self.assertEqual(clean, {"kinds": {}, "events": {"step_done": {}}})

    def test_underscore_fields_are_ignored_silently(self) -> None:
        clean, ignored = notify.validate_events_mapping({
            "_help": "x", "kinds": {"state": {"enabled": True, "_what": "y"}},
            "events": {"_note": 1, "step_done": {"_kind": "state", "_what": "a step", "severity": "NORMAL"}}})
        self.assertEqual(ignored, [])
        self.assertEqual(clean["events"], {"step_done": {"severity": "NORMAL"}})


class BodyTest(unittest.TestCase):
    def test_golden_request_shape(self) -> None:
        event = notify.build_event(
            "user_gate", repository="repo", ident="x", work_item="wi-1", title="Approve the plan",
            message="Run /satisfy-gate", link="https://example.test/pr/1", occurred_at="2026-10-12T00:00:00Z",
            metadata={"gate": "plan"})
        self.assertEqual(event.body, {
            "category": "ACTION_REQUIRED", "severity": "HIGH", "title": "Approve the plan",
            "context": "repo/wi-1", "message": "Run /satisfy-gate", "link": "https://example.test/pr/1",
            "occurredAt": "2026-10-12T00:00:00Z",
            "metadata": {"lane": "workflow-controller", "repository": "repo", "kind": "gate",
                         "event_type": "user_gate", "work_item": "wi-1", "gate": "plan"}})

    def test_limits_and_context_rule(self) -> None:
        event = notify.build_event("step_done", repository="repo", ident="x", title="t" * 500, message="m" * 9000)
        self.assertEqual(len(event.body["title"]), 200)
        self.assertEqual(len(event.body["message"]), 4000)
        self.assertEqual(notify.sanitize_context(".worktree"), "worktree")
        self.assertEqual(notify.sanitize_context("..."), "workflow-controller")
        self.assertEqual(notify.sanitize_context("my repo"), "my-repo")
        self.assertEqual(len(notify.sanitize_context("a" * 500)), 200)
        self.assertEqual(notify.repository_name("/tmp/.hidden"), "hidden")
        self.assertEqual(notify.context_for("repo", "wi"), "repo/wi")

    def test_body_is_a_function_of_the_identity(self) -> None:
        a = make_event("same", work_item="wi")
        b = make_event("same", work_item="wi")
        self.assertEqual(a, b)
        self.assertEqual(json.dumps(a.body, sort_keys=True), json.dumps(b.body, sort_keys=True))

    def test_keys(self) -> None:
        self.assertEqual(notify.idempotency_key("step_done", "repo", "job-1"), "wc-step_done-repo-job-1")
        self.assertRegex(notify.idempotency_key("step_done", "repo", "a b/c"), r"^[A-Za-z0-9._:-]+$")
        long = notify.idempotency_key("step_done", "repo", "x" * 400)
        self.assertLessEqual(len(long), 128)
        self.assertEqual(long, notify.idempotency_key("step_done", "repo", "x" * 400))
        # The same release tag in two repositories gets two keys.
        self.assertNotEqual(notify.idempotency_key("release_published", "a", "v1.9.0"),
                            notify.idempotency_key("release_published", "b", "v1.9.0"))

    def test_lifecycle_gate_identity(self) -> None:
        base = ("wi", "AWAITING", 7, "gate_x")
        ident = notify.lifecycle_gate_identity(*base)
        self.assertEqual(ident, notify.lifecycle_gate_identity(*base))
        for changed in (("wi", "OTHER", 7, "gate_x"), ("wi", "AWAITING", 8, "gate_x"),
                        ("other", "AWAITING", 7, "gate_x"), ("wi", "AWAITING", 7, "gate_y")):
            self.assertNotEqual(ident, notify.lifecycle_gate_identity(*changed))

    def test_branch_gate_identity_changes_with_each_component(self) -> None:
        base = ("wi", 1, "checks_failing", ("head", "at", 1))
        ident = notify.branch_gate_identity(*base)
        self.assertEqual(ident, notify.branch_gate_identity(*base))
        for changed in (("wi2", 1, "checks_failing", base[3]), ("wi", 2, "checks_failing", base[3]),
                        ("wi", 1, "merge_held", base[3]), ("wi", 1, "checks_failing", ("head2", "at", 1)),
                        ("wi", 1, "checks_failing", ("head", "at2", 1)), ("wi", 1, "checks_failing", ("head", "at", 2))):
            self.assertNotEqual(ident, notify.branch_gate_identity(*changed))
        # release_failed: the binding event's `at` separates A -> B -> A.
        keys = {notify.branch_gate_identity("wi", 1, "release_failed", at) for at in ("t1", "t2", "t3")}
        self.assertEqual(len(keys), 3)

    def test_usage_paused_identity_ignores_reason_and_percentages(self) -> None:
        base = notify.usage_paused_identity("claude", "five_hour", "repo", "T0")
        self.assertEqual(base, notify.usage_paused_identity("claude", "five_hour", "repo", "T0"))
        for changed in (("codex", "five_hour", "repo", "T0"), ("claude", "weekly", "repo", "T0"),
                        ("claude", "five_hour", "other", "T0"), ("claude", "five_hour", "repo", "T1")):
            self.assertNotEqual(base, notify.usage_paused_identity(*changed))
        self.assertNotEqual(notify.usage_paused_identity("claude", "run_cap", "repo", None, "run-1"),
                            notify.usage_paused_identity("claude", "run_cap", "repo", None, "run-2"))
        self.assertEqual(notify.usage_paused_identity("claude", "weekly", "repo", "T0", "run-1"),
                         notify.usage_paused_identity("claude", "weekly", "repo", "T0", "run-2"))


class SenderTest(Case):
    def test_sent_and_request_shape(self) -> None:
        n = self.notifier()
        self.send(n, make_event("a"))
        self.assertEqual(len(self.hub.requests), 1)
        request = self.hub.requests[0]
        self.assertEqual((request["method"], request["path"]), ("POST", "/api/v1/events"))
        self.assertEqual(request["authorization"], f"Bearer {CANARY}")
        self.assertEqual(request["idempotency_key"], "wc-step_done-repo-a")
        self.assertEqual(request["content_type"], "application/json")
        self.assertEqual(self.statuses(), [SENT])
        self.assertEqual(self.lines()[0]["idempotency_key"], "wc-step_done-repo-a")

    def test_retryable_statuses_back_off_then_succeed(self) -> None:
        for status in (429, 500, 502, 503):
            self.hub.requests.clear()
            self.sleeps.clear()
            self.hub.queue(status, status)
            n = self.notifier(attempts=3, retry_seconds=2)
            self.send(n, make_event(f"r{status}"))
            self.assertEqual(len(self.hub.requests), 3, status)
            self.assertEqual(self.sleeps, [2, 4], status)
            self.assertEqual(self.lines()[-1]["status"], SENT, status)

    def test_exhausted_attempts_are_failed(self) -> None:
        self.hub.queue(500, 500, 500)
        n = self.notifier(attempts=3)
        self.send(n, make_event("e"))
        self.assertEqual(self.statuses(), [FAILED])
        self.assertEqual(len(self.hub.requests), 3)

    def test_other_4xx_is_a_rejection_and_not_retried(self) -> None:
        for status in (400, 401, 403):
            self.hub.requests.clear()
            self.hub.queue(status)
            n = self.notifier(attempts=3)
            self.send(n, make_event(f"x{status}"))
            self.assertEqual(len(self.hub.requests), 1, status)
            self.assertEqual(self.lines()[-1]["status"], REJECTED, status)

    def test_422_is_conflict_not_duplicate_not_rejected_and_not_retried(self) -> None:
        n = self.notifier(attempts=3)
        first = make_event("c", message="one")
        self.send(n, first)
        different = notify.build_event("step_done", repository="repo", ident="c", title="other title")
        n2 = self.notifier(attempts=3)
        self.send(n2, different)
        self.assertEqual(self.statuses(), [SENT, CONFLICT])
        self.assertEqual(len(self.hub.requests), 2)

    def test_422_on_a_recovered_event_is_duplicate_from_the_server(self) -> None:
        self.send(self.notifier(), make_event("rec", message="one"))
        n = self.notifier()
        self.send(n, notify.build_event("step_done", repository="repo", ident="rec", title="changed"), recovered=True)
        last = self.lines()[-1]
        self.assertEqual((last["status"], last["source"]), (DUPLICATE, "server"))

    def test_identical_body_again_is_200_and_recorded_sent(self) -> None:
        self.send(self.notifier(), make_event("same"))
        self.send(self.notifier(), make_event("same"))
        self.assertEqual(self.statuses(), [SENT, SENT])
        self.assertEqual(self.lines()[1]["http_status"], 200)

    def test_connection_reset_and_unreachable_are_retried(self) -> None:
        self.hub.queue(Step(None))
        n = self.notifier(attempts=2)
        self.send(n, make_event("reset"))
        self.assertEqual(self.statuses(), [SENT])
        dead = self.notifier(url="http://127.0.0.1:1", attempts=2)
        self.send(dead, make_event("dead"))
        self.assertEqual(self.lines()[-1]["status"], FAILED)
        self.assertIn("unreachable", self.lines()[-1]["detail"])

    def test_timeout_is_a_bounded_failure(self) -> None:
        self.hub.queue(Step(200, delay=1.5))
        n = self.notifier(attempts=1, timeout_seconds=1)
        started = time.monotonic()
        self.send(n, make_event("slow"))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.statuses(), [FAILED])

    def test_in_process_duplicate_key_is_dropped_before_sending(self) -> None:
        n = self.notifier()
        self.send(n, make_event("k"), make_event("k"))
        self.assertEqual(len(self.hub.requests), 1)
        dup = [line for line in self.lines() if line["status"] == DUPLICATE]
        self.assertEqual([line["source"] for line in dup], ["process"])

    def test_breaker_opens_after_three_exhausted_events_and_closes_after_300_s(self) -> None:
        clock = [1000.0]
        cred = Credentials(url=self.hub.url, url_source="t", key=CANARY, key_source="t")
        n = notify.Notifier(NotifierConfig(attempts=1), runtime_root=self.root, credentials=cred,
                            sleeper=self.sleeps.append, monotonic=lambda: clock[0])
        self.hub.queue(500, 500, 500)
        self.send(n, *(make_event(f"b{i}") for i in range(3)))
        self.send(n, make_event("b3"), make_event("b4"))
        self.assertEqual(self.statuses(), [FAILED] * 3 + [SKIPPED] * 2)
        self.assertEqual(len(self.hub.requests), 3)
        clock[0] += 301
        n._flush_deadline = None  # a new process-wide flush window for the fake clock
        self.send(n, make_event("b5"))
        self.assertEqual(self.statuses()[-1], SENT)

    def test_full_queue_drops_the_new_event_with_one_line(self) -> None:
        self.hub.queue(Step(200, delay=1.0))
        n = self.notifier(attempts=1, timeout_seconds=5)
        n.emit(make_event("first"))
        time.sleep(0.2)  # the sender holds `first` in flight; the queue is empty again
        for i in range(notify.QUEUE_LIMIT):
            n.emit(make_event(f"q{i}"))
        n.emit(make_event("overflow"))
        dropped = [line for line in self.lines() if line["status"] == DROPPED]
        self.assertEqual([line["idempotency_key"] for line in dropped], ["wc-step_done-repo-overflow"])
        self.assertTrue(n.flush(60))

    def test_flush_times_out_without_a_per_event_line_and_counts_pending(self) -> None:
        self.hub.queue(Step(200, delay=1.5))
        n = self.notifier(attempts=1, timeout_seconds=5)
        n.emit(make_event("hung"))
        started = time.monotonic()
        self.assertFalse(n.flush(0.3))
        self.assertLess(time.monotonic() - started, 1.2)
        self.assertEqual(self.lines(), [])
        self.assertEqual(n.counts().get("pending"), 1)
        self.assertTrue(n.flush(10) or True)

    def test_flush_shares_one_deadline_and_interrupted_flush_is_short(self) -> None:
        self.hub.queue(Step(200, delay=2.0))
        n = self.notifier(attempts=1, timeout_seconds=1)
        n.emit(make_event("hung2"))
        started = time.monotonic()
        self.assertFalse(n.flush(0.4))
        self.assertFalse(n.flush(0.4))  # the second waits only for what remains of the first deadline
        self.assertLess(time.monotonic() - started, 0.9)
        n2 = self.notifier(attempts=1, timeout_seconds=30)
        self.hub.queue(Step(200, delay=2.0))
        n2.emit(make_event("hung3"))
        started = time.monotonic()
        self.assertFalse(n2.flush(interrupted=True))
        self.assertLess(time.monotonic() - started, notify.INTERRUPTED_FLUSH_SECONDS + 0.5)

    def test_an_exception_in_the_sender_is_recorded_and_the_process_is_unaffected(self) -> None:
        n = self.notifier()
        n._opener = lambda request, timeout: (_ for _ in ()).throw(RuntimeError("boom"))
        self.send(n, make_event("boom"), make_event("after"))
        self.assertEqual(self.statuses(), [FAILED, FAILED])
        self.assertIn("RuntimeError", self.lines()[0]["detail"])

    def test_emit_never_raises(self) -> None:
        n = self.notifier()
        n.emit(object())  # type: ignore[arg-type]
        notify.emit(make_event("none-installed"))
        self.assertTrue(notify.flush())

    def test_the_api_key_is_in_no_file_the_test_creates(self) -> None:
        self.hub.queue(500, 400)
        n = self.notifier(attempts=2)
        self.send(n, make_event("k1"), make_event("k2"))
        n.note_run_opened("run-1")
        for path in self.tmp.rglob("*"):
            if path.is_file():
                self.assertNotIn(CANARY.encode(), path.read_bytes(), str(path))
        self.assertNotIn("Authorization", "".join(json.dumps(line) for line in self.lines()))

    def test_disabled_event_type_is_recorded_and_not_sent(self) -> None:
        events, _ = notify.validate_events_mapping({"events": {"step_done": {"enabled": False}}})
        n = self.notifier(events=events)
        self.send(n, make_event("off"), make_event("on", event_type="user_gate"))
        self.assertEqual(self.statuses(), [DISABLED, SENT])
        self.assertEqual(len(self.hub.requests), 1)
        self.assertEqual(self.hub.requests[0]["body"]["severity"], "HIGH")

    def test_usage_released_is_off_by_default(self) -> None:
        n = self.notifier()
        self.send(n, make_event("u", event_type="usage_released"))
        self.assertEqual(self.hub.requests, [])
        self.assertEqual(self.statuses(), [DISABLED])


class InertAndRunLogTest(Case):
    def test_unconfigured_is_inert_after_one_line_naming_what_it_tried(self) -> None:
        n = self.notifier(key=None)
        self.assertEqual(n.inert_reason, NOT_CONFIGURED)
        self.send(n, make_event("a"), make_event("b"))
        self.assertEqual(self.statuses(), [NOT_CONFIGURED])
        self.assertIn("a, b", self.lines()[0]["detail"])
        self.assertEqual(self.hub.requests, [])
        self.assertFalse(n.note_run_opened("run-1"))
        self.assertEqual(self.lines("run-1"), [])

    def test_master_switch_off_is_inert_with_a_disabled_line(self) -> None:
        n = self.notifier(enabled=False)
        self.send(n, make_event("a"))
        self.assertEqual(self.statuses(), [DISABLED])
        self.assertFalse(n.note_run_opened("run-1"))
        self.assertEqual(self.hub.requests, [])

    def test_notifier_active_is_written_per_run_only_when_configured(self) -> None:
        n = self.notifier()
        self.assertTrue(n.note_run_opened("run-1"))
        self.assertTrue(n.note_run_opened("run-2"))
        self.assertEqual(self.statuses("run-1"), ["notifier_active"])
        self.assertEqual(self.statuses("run-2"), ["notifier_active"])
        self.assertNotIn(CANARY, json.dumps(self.lines("run-1")))
        self.send(n, make_event("x"), run_id="run-1")
        self.assertEqual(self.statuses("run-1"), ["notifier_active", SENT])
        self.assertEqual(self.lines(), [])

    def test_outcome_lines_go_to_the_named_run_log(self) -> None:
        n = self.notifier()
        self.send(n, make_event("a"), run_id="run-9")
        self.assertEqual(self.statuses("run-9"), [SENT])


class CredentialsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.conf = self.tmp / "signalhub"
        self.conf.mkdir()

    def key_file(self, name: str, text: str = CANARY + "\n", mode: int = 0o600) -> Path:
        path = self.tmp / name
        path.write_text(text)
        path.chmod(mode)
        return path

    def find(self, env: dict | None = None, **kwargs) -> Credentials:
        base = {"SIGNALHUB_CONFIG_DIR": str(self.conf)}
        base.update(env or {})
        return notify.find_credentials(env=base, **kwargs)

    def test_url_precedence(self) -> None:
        (self.conf / "url").write_text("http://from-file\nsecond line\n")
        self.assertEqual((self.find().url, self.find().url_source), ("http://from-file", str(self.conf / "url")))
        self.assertEqual(self.find(url_setting="http://setting").url, "http://setting")
        self.assertEqual(self.find({"SIGNALHUB_URL": "http://env"}, url_setting="http://setting").url, "http://env")

    def test_key_precedence(self) -> None:
        (self.conf / "api-key").write_text("fallback-key\n")
        setting = self.key_file("setting.key", "setting-key\n")
        envfile = self.key_file("env.key", "envfile-key\n")
        self.assertEqual(self.find().key, "fallback-key")
        self.assertEqual(self.find(api_key_file_setting=str(setting)).key, "setting-key")
        self.assertEqual(self.find({"SIGNALHUB_API_KEY_FILE": str(envfile)}, api_key_file_setting=str(setting)).key,
                         "envfile-key")
        self.assertEqual(self.find({"SIGNALHUB_API_KEY": "direct", "SIGNALHUB_API_KEY_FILE": str(envfile)},
                                   api_key_file_setting=str(setting)).key, "direct")

    def test_a_missing_key_file_falls_through_and_the_tried_list_names_the_paths(self) -> None:
        cred = self.find({"SIGNALHUB_API_KEY_FILE": str(self.tmp / "missing.key")},
                         api_key_file_setting=str(self.tmp / "also-missing.key"))
        self.assertFalse(cred.configured)
        joined = "\n".join(cred.tried)
        for expected in ("$SIGNALHUB_URL", "$SIGNALHUB_API_KEY", "missing.key", "also-missing.key",
                         str(self.conf / "api-key"), str(self.conf / "url")):
            self.assertIn(expected, joined)
        self.assertNotIn(CANARY, joined)

    def test_kill_switch(self) -> None:
        self.assertFalse(self.find().disabled)
        (self.conf / "disabled").write_text("")
        cred = self.find()
        self.assertTrue(cred.disabled)
        self.assertEqual(cred.disabled_path, str(self.conf / "disabled"))

    def test_group_readable_key_is_used_but_warned_about(self) -> None:
        path = self.key_file("loose.key", mode=0o640)
        cred = self.find(api_key_file_setting=str(path))
        self.assertEqual(cred.key, CANARY)
        self.assertEqual(len(cred.warnings), 1)
        self.assertNotIn(CANARY, cred.warnings[0])
        self.assertEqual(self.find(api_key_file_setting=str(self.key_file("tight.key"))).warnings, [])

    def test_url_userinfo_is_redacted(self) -> None:
        self.assertEqual(notify.redact_url("https://user:pw@host/x"), "https://host/x")
        self.assertEqual(notify.redact_url("http://host:80/x"), "http://host:80/x")

    def test_credentials_repr_hides_the_key(self) -> None:
        self.assertNotIn(CANARY, repr(self.find({"SIGNALHUB_API_KEY": CANARY})))


class IsolationGuardTest(unittest.TestCase):
    def test_a_fresh_interpreter_resolves_no_url_and_no_key(self) -> None:
        env = dict(os.environ)
        code = (
            "import json, tests; from controller import notify; c = notify.find_credentials();"
            "print(json.dumps({'configured': c.configured, 'disabled': c.disabled,"
            " 'tried': c.tried, 'dir': str(notify.config_dir())}))"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, check=True)
        result = json.loads(out.stdout)
        self.assertFalse(result["configured"])
        self.assertFalse(result["disabled"])
        self.assertTrue(result["dir"].startswith(os.path.join(tempfile.gettempdir(), "workflow-controller-tests-signalhub-")))
        home_conf = str(Path.home() / ".config" / "signalhub")
        for tried in result["tried"]:
            self.assertNotIn(home_conf, tried)

    def test_the_environment_is_scrubbed(self) -> None:
        for name in ("SIGNALHUB_URL", "SIGNALHUB_API_KEY", "SIGNALHUB_API_KEY_FILE"):
            self.assertNotIn(name, os.environ)
        self.assertEqual(os.environ["SIGNALHUB_CONFIG_DIR"], tests.ISOLATED_SIGNALHUB_CONFIG_DIR)
        self.assertEqual(os.listdir(tests.ISOLATED_SIGNALHUB_CONFIG_DIR), [])


class InstalledNotifierTest(Case):
    def tearDown(self) -> None:
        notify.uninstall()

    def test_no_notifier_is_a_noop_and_install_routes_emit(self) -> None:
        notify.uninstall()
        notify.emit(make_event("none"))
        self.assertEqual(notify.summary(), {})
        self.assertFalse(notify.note_run_opened("r"))
        n = self.notifier()
        notify._installed = n
        notify.emit(make_event("installed"), run_id="run-1")
        self.assertTrue(notify.flush(10))
        self.assertEqual(notify.summary(), {SENT: 1})
        self.assertEqual(self.statuses("run-1"), [SENT])


if __name__ == "__main__":
    unittest.main()
