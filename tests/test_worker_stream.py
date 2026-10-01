"""The worker stream state machine (``controller/worker_stream.py``,
``workflow-controller-worker-lifecycle-ownership`` CP2; plan design B and
CP2's Tests list).

- :class:`FixtureClassificationTest`: every CP1 fixture classifies as B's
  table says, with the P5/P9/P12 wakeup and bracket states pinned line by
  line, and B's state unchanged by the six non-structural kinds CP1's
  replay comparison drops.
- :class:`RowPrecedenceTest` / :class:`PositionalKillTest`: supervisor rows
  before structural ones, and row 7 is positional.
- :class:`WakeupTimeTest` / :class:`OriginTest`: the due time's sources, no
  clock, and ``origin`` never evidence.
- :class:`BracketCorrelationTest` / :class:`WrongMatchTest`: bracket
  anomalies built by editing the P5 fixture in memory, and round 9's
  dangerous sequence.
- :class:`SettlementTest`: stops (inside and outside a bracket), counts and
  ``settled_wakeups``.
- :class:`StateTest`: tasks, ``queued_turn_count``, quiescence, and
  incremental parsing at any chunking.
"""

from __future__ import annotations

import datetime
import json
import random
import types
import unittest
import unittest.mock
from pathlib import Path

from controller import worker_stream as ws

CONTRACT_DIR = Path(__file__).resolve().parent / "harness_contract"

FIXTURES = (
    "job_12a9f268_applying_review_feedback", "job_2857a730_two_results", "job_5d4a976a_implementing",
    "job_6082a90b_self_reviewing", "job_66988e17_self_reviewing", "job_8b244f42_implementing",
    "job_cb43fe49_self_reviewing",
    "p1_print_mode_background_bash", "p2_escaped_descendants", "p2_escaped_descendants_subreaper",
    "p3_streaming_background_bash", "p4_monitor", "p5_p11_wakeup_fires", "p6_p10_slash_command",
    "p8_task_stop", "p8_monitor_timeout", "p9_wakeup_cancel", "p11_subagent_handback",
    "p12_stop_inside_fire_single", "p12_stop_inside_fire_nested",
)
#: The six observed failures (Investigation), each with the tasks killed at exit.
FAILED_JOBS = {
    "job_8b244f42_implementing": {"b2o2rkery", "bxc45j6ci"},
    "job_5d4a976a_implementing": {"b7ut3yyj7"},
    "job_66988e17_self_reviewing": None,
    "job_6082a90b_self_reviewing": None,
    "job_cb43fe49_self_reviewing": None,
    "job_12a9f268_applying_review_feedback": None,
}
#: The print-mode streams (the imported jobs, P1, P2); the rest are streaming.
PRINT_FIXTURES = tuple(n for n in FIXTURES if n.startswith(("job_", "p1_", "p2_")))

# p5_p11_wakeup_fires, 0-based lines.
ALPHA = "toolu_01MmH8cR1Yvs6YitcLW96cJv"
BRAVO = "toolu_01THbmXUTbAcfJKqyCRVcAYc"
X_UUID = "2cde4b9e-e72f-4d98-b485-50046d4f8697"
Y_UUID = "6ff491e4-4604-4adc-9345-a5e874f9e79f"


def raw_lines(name: str) -> list[bytes]:
    return (CONTRACT_DIR / f"{name}.jsonl").read_bytes().splitlines(keepends=True)


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in raw_lines(name)]


def text(events: list) -> str:
    return "".join((e if isinstance(e, str) else json.dumps(e)) + "\n" for e in events)


def offset_of(events: list, index: int) -> int:
    """The byte offset at which ``events[index]`` starts in ``text(events)``."""
    return len(text(events[:index]).encode())


def stream_of(events: list, facts: ws.SupervisorFacts | None = None) -> ws.WorkerStream:
    return ws.read_stream(text(events), facts)


def classify(events: list, *, mode: str = ws.STREAMING, ending: int | None | str = "end",
             returncode: int | None = 0, **facts) -> tuple[str, dict | None, dict]:
    """Classify ``events``; in streaming mode ``ending="end"`` is a
    supervisor that ended the session after the last line."""
    data = text(events)
    if ending == "end":
        ending = len(data.encode()) if mode == ws.STREAMING else None
    return ws.classify(data, mode=mode, returncode=returncode,
                       facts=ws.SupervisorFacts(ending_offset=ending, **facts))


def by_id(diagnosis: dict, key: str, field: str, value: str) -> dict:
    return next(entry for entry in diagnosis[key] if entry[field] == value)


def kinds(stream_or_diagnosis) -> list[str]:
    if isinstance(stream_or_diagnosis, ws.WorkerStream):
        return [a["kind"] for a in stream_or_diagnosis.anomalies]
    return [a["kind"] for a in stream_or_diagnosis["command_lifecycle_anomalies"]]


def states_by_line(events: list) -> list[ws.WorkerStream]:
    """Not a list of copies: yields the stream after each line, for checks."""
    stream = ws.WorkerStream()
    for event in events:
        stream.feed(text([event]).encode())
        yield stream


# -- synthetic events --------------------------------------------------

T0 = 1_790_330_000.0


def ts(seconds: float) -> str:
    return ws._iso(T0 + seconds)


def init() -> dict:
    return {"type": "system", "subtype": "init", "session_id": "s"}


def say(seconds: float | None, words: str = "ok") -> dict:
    event = {"type": "assistant", "message": {"content": [{"type": "text", "text": words}]}}
    if seconds is not None:
        event["timestamp"] = ts(seconds)
    return event


def result(**fields) -> dict:
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": "s",
            "queued_turn_count": 0, **fields}


def tool_use(tool_id: str, seconds: float, name: str, tool_input: dict) -> dict:
    return {"type": "assistant", "timestamp": ts(seconds),
            "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]}}


def tool_result(tool_id: str, seconds: float, content: str, *, is_error: bool = False,
                tool_use_result: object = None) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    event = {"type": "user", "timestamp": ts(seconds), "message": {"content": [block]}}
    if tool_use_result is not None:
        event["tool_use_result"] = tool_use_result
    return event


def schedule(tool_id: str, seconds: float, *, stated: int | None = 100, delay: object = 60,
             error: bool = False) -> list[dict]:
    """A ``ScheduleWakeup`` pair; ``stated`` is the harness's ``in Ns``."""
    words = f"Next wakeup scheduled for 10:00:00 (in {stated}s)." if stated is not None else "Scheduled."
    return [
        tool_use(tool_id, seconds, "ScheduleWakeup", {"delaySeconds": delay, "prompt": "p", "reason": "r",
                                                      "noop": False}),
        tool_result(tool_id, seconds, words, is_error=error, tool_use_result=None if error else {
            "scheduledFor": int((T0 + seconds + (stated or 60)) * 1000), "clampedDelaySeconds": 60,
            "wasClamped": False}),
    ]


_MISSING = object()


def stop(tool_id: str, seconds: float, cancelled: object = 0, *, error: bool = False) -> list[dict]:
    outcome = {"scheduledFor": 0, "clampedDelaySeconds": 0, "wasClamped": False, "stopped": True}
    if cancelled is not _MISSING:
        outcome["cancelledWakeups"] = cancelled
    return [
        tool_use(tool_id, seconds, "ScheduleWakeup", {"stop": True}),
        tool_result(tool_id, seconds, "Loop stopped.", is_error=error, tool_use_result=None if error else outcome),
    ]


def lc(command_uuid: object, state: object = _MISSING) -> dict:
    event = {"type": "command_lifecycle", "uuid": "e", "session_id": "s"}
    if command_uuid is not _MISSING:
        event["command_uuid"] = command_uuid
    if state is not _MISSING:
        event["state"] = state
    return event


def tasks_changed(*task_ids: str) -> dict:
    return {"type": "system", "subtype": "background_tasks_changed",
            "tasks": [{"task_id": t, "task_type": "local_bash", "description": f"task {t}"} for t in task_ids]}


def task_end(task_id: str, status: str = "completed") -> list[dict]:
    notified = "stopped" if status == "killed" else status
    return [{"type": "system", "subtype": "task_updated", "task_id": task_id, "patch": {"status": status}},
            {"type": "system", "subtype": "task_notification", "task_id": task_id, "status": notified}]


def turn(seconds: float | None, *body: dict, **result_fields) -> list[dict]:
    return [init(), say(seconds), *body, result(**result_fields)]


def fire(command_uuid: str, seconds: float | None, *body: dict) -> list[dict]:
    return [lc(command_uuid, "started"), *turn(seconds, *body), lc(command_uuid, "completed")]


def dangerous_prefix(*, spurious_at: float = 150, second_wakeup: bool = False,
                     stop_in_spurious: object = None) -> list[dict]:
    """Round 9's I1: ``W`` scheduled (due at 100 s), a task that is the only
    other owned work, the task's completion, and a regular-shaped bracket
    around that completion turn opening at ``spurious_at``."""
    first = [*schedule("W", 0, stated=100)]
    if second_wakeup:
        first += schedule("W2", 1, stated=119)
    body = stop("S1", spurious_at, stop_in_spurious) if stop_in_spurious is not None else []
    return [
        *turn(0, *first, tasks_changed("t1")),
        tasks_changed(), *task_end("t1"),
        *fire("spurious", spurious_at, *body),
    ]


# ---------------------------------------------------------------------------


class FixtureClassificationTest(unittest.TestCase):

    def test_the_six_observed_failures_are_owned_work_killed_at_exit(self) -> None:
        for name, killed in FAILED_JOBS.items():
            with self.subTest(name):
                outcome, terminal, diagnosis = ws.classify(
                    b"".join(raw_lines(name)), mode=ws.PRINT, returncode=0)
                self.assertEqual(outcome, ws.AMBIGUOUS)
                self.assertEqual(diagnosis["reason"], ws.REASON_OWNED_WORK_KILLED)
                self.assertEqual(diagnosis["command_lifecycle_anomalies"], [])
                self.assertIsNotNone(terminal)
                ids = diagnosis["tasks_killed_after_end"]
                self.assertTrue(ids)
                if killed is not None:
                    self.assertEqual(set(ids), killed)
                for task_id in ids:
                    task = by_id(diagnosis, "tasks_seen", "task_id", task_id)
                    self.assertIn(task["final_status"], ("killed", "stopped"))
                    self.assertEqual(task["ended"], "after_end")
                    self.assertIsInstance(task["description"], str)

    def test_8b244f42_holds_a_pending_wakeup_at_exit_with_the_harness_stated_due_time(self) -> None:
        _, _, diagnosis = ws.classify(b"".join(raw_lines("job_8b244f42_implementing")),
                                      mode=ws.PRINT, returncode=0)
        [wakeup] = diagnosis["wakeups_seen"]
        self.assertEqual(wakeup["state"], ws.PENDING)
        self.assertEqual(wakeup["due_source"], "harness_stated")
        self.assertEqual(wakeup["scheduled_at"], "2026-09-25T10:12:02.518Z")
        self.assertEqual(wakeup["due_at"], "2026-09-25T10:32:59.518Z")  # "in 1257s"
        self.assertEqual(diagnosis["owned_work_at_exit"]["pending_wakeups"], [wakeup["tool_use_id"]])

    def test_2857a730_is_success_with_two_results(self) -> None:
        outcome, terminal, diagnosis = ws.classify(
            b"".join(raw_lines("job_2857a730_two_results")), mode=ws.PRINT, returncode=0)
        self.assertEqual((outcome, diagnosis["reason"]), (ws.SUCCESS, ws.REASON_QUIESCENT))
        self.assertEqual(diagnosis["result_count"], 2)
        self.assertEqual(terminal["origin"]["kind"], "peer")
        self.assertEqual(diagnosis["results"][1]["origin"]["kind"], "peer")

    def test_5d4a976a_mid_session_monitor_stop_ends_before_the_last_result(self) -> None:
        _, _, diagnosis = ws.classify(b"".join(raw_lines("job_5d4a976a_implementing")),
                                      mode=ws.PRINT, returncode=0)
        monitor = by_id(diagnosis, "tasks_seen", "task_id", "bw1ao4690")
        self.assertEqual((monitor["final_status"], monitor["ended"]), ("stopped", "before_end"))
        suite = by_id(diagnosis, "tasks_seen", "task_id", "b7ut3yyj7")
        self.assertEqual((suite["final_status"], suite["ended"]), ("stopped", "after_end"))
        self.assertEqual(diagnosis["tasks_killed_after_end"], ["b7ut3yyj7"])

    def test_p3_and_p4_are_success_after_a_waiting_phase(self) -> None:
        for name in ("p3_streaming_background_bash", "p4_monitor"):
            with self.subTest(name):
                events = load(name)
                waited = [not s.turn_open and s.results and s.owned_work()["tasks"]
                          for s in states_by_line(events)]
                self.assertTrue(any(waited))
                outcome, _, diagnosis = classify(events)
                self.assertEqual((outcome, diagnosis["reason"]), (ws.SUCCESS, ws.REASON_QUIESCENT))

    def test_p5_wakeups_match_only_at_their_brackets_completed_and_stay_owned(self) -> None:
        events = load("p5_p11_wakeup_fires")
        stream = ws.WorkerStream()
        states = {}
        for index, event in enumerate(events):
            stream.feed(text([event]).encode())
            states[index] = (stream.wakeups[ALPHA].state if ALPHA in stream.wakeups else None,
                             stream.wakeups[BRAVO].state if BRAVO in stream.wakeups else None,
                             stream.quiescent())
        self.assertEqual(states[6][0], ws.PENDING)
        self.assertEqual(states[23][0], ws.PENDING)       # the fire turn's result
        self.assertEqual(states[24][0], ws.FIRE_MATCHED)  # completed(2cde4b9e-...)
        self.assertEqual(states[31][1], ws.PENDING)
        self.assertEqual(states[32][1], ws.FIRE_MATCHED)  # completed(6ff491e4-...)
        self.assertFalse(any(q for _, _, q in states.values()))
        self.assertEqual(stream.anomalies, [])
        self.assertEqual([(b.command_uuid, b.state, b.matched_wakeup) for b in stream.brackets],
                         [(X_UUID, ws.CLOSED_REGULAR, ALPHA), (Y_UUID, ws.CLOSED_REGULAR, BRAVO)])
        stream.settle_wakeups([ALPHA])
        self.assertFalse(stream.quiescent())
        stream.settle_wakeups([BRAVO])
        self.assertTrue(stream.quiescent())
        outcome, _, diagnosis = classify(events, settled_wakeups=(ALPHA, BRAVO))
        self.assertEqual(outcome, ws.SUCCESS)
        self.assertEqual({w["settled_by"] for w in diagnosis["wakeups_seen"]}, {"settle_window"})

    def test_p9_settles_by_stop_with_agreeing_counts(self) -> None:
        outcome, _, diagnosis = classify(load("p9_wakeup_cancel"))
        self.assertEqual(outcome, ws.SUCCESS)
        self.assertEqual(diagnosis["command_lifecycle_anomalies"], [])
        [stop_entry] = diagnosis["wakeup_stops"]
        self.assertEqual((stop_entry["cancelled_wakeups"], stop_entry["expected_count"]), (1, 1))
        [wakeup] = diagnosis["wakeups_seen"]  # line 7's rejected call is no wakeup
        self.assertEqual((wakeup["state"], wakeup["settled_by"]), (ws.SETTLED, "stop"))

    def test_the_p12_fixtures_settle_by_a_stop_inside_the_fire_with_no_anomaly(self) -> None:
        for name, expected in (("p12_stop_inside_fire_single", 0), ("p12_stop_inside_fire_nested", 1)):
            with self.subTest(name):
                events = load(name)
                alpha = next(b["id"] for e in events if e["type"] == "assistant"
                             for b in e["message"]["content"] if b.get("type") == "tool_use")
                completed = next(i for i, e in enumerate(events)
                                 if e["type"] == "command_lifecycle" and e["state"] == "completed")
                stream = stream_of(events[:completed + 1])
                self.assertEqual(stream.anomalies, [])
                self.assertTrue(stream.quiescent())
                [bracket] = stream.brackets
                self.assertEqual((bracket.state, bracket.provisional, bracket.matched_wakeup),
                                 (ws.CLOSED_REGULAR, alpha, alpha))
                [stop_entry] = stream.wakeup_stops
                self.assertEqual(stop_entry["expected_count"], expected)
                self.assertEqual(stop_entry["cancelled_wakeups"], expected)
                self.assertEqual(stop_entry["command_uuid"], bracket.command_uuid)
                self.assertEqual({w.state for w in stream.wakeups.values()}, {ws.SETTLED})
                self.assertEqual({w.settled_by for w in stream.wakeups.values()}, {"stop"})
                self.assertEqual(stream.wakeups[alpha].settle_detail["command_uuid"], bracket.command_uuid)
                outcome, _, diagnosis = classify(events)
                self.assertEqual((outcome, diagnosis["reason"]), (ws.SUCCESS, ws.REASON_QUIESCENT))

    def test_dropping_the_six_non_structural_kinds_changes_no_state_or_classification(self) -> None:
        """CP1's replay comparison drops these kinds; this is what makes that
        safe (deferred from CP1 to here)."""
        def dropped(event: dict) -> bool:
            key = (event.get("type"), event.get("subtype") if event.get("type") == "system" else None)
            return key in ws.NON_STRUCTURAL_EVENTS

        def summary(outcome: str, diagnosis: dict) -> tuple:
            return (outcome, diagnosis["reason"], diagnosis["secondary_reasons"], kinds(diagnosis),
                    diagnosis["tasks_killed_after_end"])

        for name in FIXTURES:
            with self.subTest(name):
                events = load(name)
                kept = [e for e in events if not dropped(e)]
                self.assertLess(len(kept), len(events))
                self.assertEqual(stream_of(kept).snapshot(), stream_of(events).snapshot())
                mode = ws.PRINT if name in PRINT_FIXTURES else ws.STREAMING
                self.assertEqual(summary(*classify(kept, mode=mode)[::2]),
                                 summary(*classify(events, mode=mode)[::2]))

    def test_only_the_wakeup_fixtures_hold_brackets_and_none_holds_an_anomaly(self) -> None:
        for name in FIXTURES:
            with self.subTest(name):
                stream = stream_of(load(name))
                self.assertEqual(stream.anomalies, [])
                self.assertEqual(stream.malformed_lines, [])
                self.assertEqual(stream.unknown_events, {})
                self.assertEqual(bool(stream.brackets), name.startswith(("p5_", "p12_")))

    def test_print_mode_never_gives_stdin_closed_while_waiting(self) -> None:
        for name in FIXTURES:
            with self.subTest(name):
                _, _, diagnosis = classify(load(name), mode=ws.PRINT)
                self.assertNotIn(ws.REASON_STDIN_CLOSED_WHILE_WAITING,
                                 [diagnosis["reason"], *diagnosis["secondary_reasons"]])


class RowPrecedenceTest(unittest.TestCase):

    def test_an_overdue_wakeup_whose_ending_killed_a_task_is_wakeup_not_delivered(self) -> None:
        events = turn(0, *schedule("W", 0), tasks_changed("t1"))
        ending = offset_of(events, len(events))
        events += [tasks_changed(), *task_end("t1", "killed")]
        outcome, _, diagnosis = classify(events, ending=ending, wakeup_overdue_declared_at=ts(500))
        self.assertEqual((outcome, diagnosis["reason"]), (ws.AMBIGUOUS, ws.REASON_WAKEUP_NOT_DELIVERED))
        self.assertIn(ws.REASON_OWNED_WORK_KILLED, diagnosis["secondary_reasons"])

    def test_a_streaming_eof_with_no_ending_offset_is_stdin_closed_while_waiting(self) -> None:
        killed = [*turn(0, tasks_changed("t1")), tasks_changed(), *task_end("t1", "killed")]
        pending = turn(0, *schedule("W", 0))
        for name, events in (("killed task", killed), ("pending wakeup", pending)):
            with self.subTest(name):
                outcome, _, diagnosis = classify(events, ending=None)
                self.assertEqual((outcome, diagnosis["reason"]),
                                 (ws.AMBIGUOUS, ws.REASON_STDIN_CLOSED_WHILE_WAITING))
                self.assertEqual(diagnosis["secondary_reasons"], [ws.REASON_OWNED_WORK_KILLED])

    def test_an_overdue_second_wakeup_with_an_unsettled_matched_one_records_it_as_secondary(self) -> None:
        events = [*turn(0, *schedule("W", 0, stated=100)), *fire("fw", 100, *schedule("W2", 101))]
        stream = stream_of(events)
        self.assertEqual(stream.wakeups["W"].state, ws.FIRE_MATCHED)
        outcome, _, diagnosis = classify(events, wakeup_overdue_declared_at=ts(600))
        self.assertEqual(diagnosis["reason"], ws.REASON_WAKEUP_NOT_DELIVERED)
        self.assertEqual(diagnosis["secondary_reasons"], [ws.REASON_OWNED_WORK_KILLED])
        self.assertEqual(diagnosis["owned_work_at_exit"]["fire_matched_wakeups"], ["W"])

    def test_an_unterminated_bracket_fact_is_row_4_with_a_killed_task_secondary(self) -> None:
        events = [*turn(0, tasks_changed("t1")), lc("open", "started")]
        ending = offset_of(events, len(events))
        events += [tasks_changed(), *task_end("t1", "killed")]
        outcome, _, diagnosis = classify(events, ending=ending, command_lifecycle_overdue_declared_at=ts(400))
        self.assertEqual((outcome, diagnosis["reason"]), (ws.AMBIGUOUS, ws.REASON_LIFECYCLE_UNTERMINATED))
        self.assertEqual(diagnosis["secondary_reasons"], [ws.REASON_OWNED_WORK_KILLED])

    def test_timeout_signal_and_exit_status_come_first(self) -> None:
        clean = turn(0)
        self.assertEqual(classify(clean, timed_out=True)[2]["reason"], ws.REASON_TIMEOUT)
        self.assertEqual(classify(clean, returncode=-9)[2]["reason"], ws.REASON_SIGNAL)
        self.assertEqual(classify(clean, returncode=3)[:1], (ws.FAILURE,))
        unknown = classify(clean, returncode=None)[2]
        self.assertEqual((unknown["reason"], unknown["exit_status_known"]), (ws.REASON_QUIESCENT, False))


class PositionalKillTest(unittest.TestCase):

    def test_worker_initiated_stops_before_the_end_are_success(self) -> None:
        for name in ("p8_task_stop", "p8_monitor_timeout"):
            with self.subTest(name):
                outcome, _, diagnosis = classify(load(name))
                self.assertEqual(outcome, ws.SUCCESS)
                [task] = diagnosis["tasks_seen"]
                self.assertEqual((task["final_status"], task["ended"]), ("stopped", "before_end"))

    def test_the_same_stop_after_the_ending_offset_is_owned_work_killed_at_exit(self) -> None:
        for name in ("p8_task_stop", "p8_monitor_timeout"):
            with self.subTest(name):
                events = load(name)
                killed = next(i for i, e in enumerate(events) if e.get("subtype") == "task_updated")
                outcome, _, diagnosis = classify(events, ending=offset_of(events, killed))
                self.assertEqual((outcome, diagnosis["reason"]), (ws.AMBIGUOUS, ws.REASON_OWNED_WORK_KILLED))


class WakeupTimeTest(unittest.TestCase):

    def test_the_due_time_is_harness_stated_when_present_and_the_clamp_otherwise(self) -> None:
        stream = stream_of(turn(0, *schedule("stated", 0, stated=1257, delay=1200),
                                *schedule("short", 1, stated=None, delay=30),
                                *schedule("long", 2, stated=None, delay=5000),
                                *schedule("plain", 3, stated=None, delay=120)))
        self.assertEqual({k: (w.due - T0, w.due_source) for k, w in stream.wakeups.items()}, {
            "stated": (1257, "harness_stated"), "short": (61, "clamp"),
            "long": (3602, "clamp"), "plain": (123, "clamp"),
        })
        self.assertEqual(stream.wakeups["stated"].scheduled_for, int((T0 + 1257) * 1000))

    def test_a_bracketed_turn_opening_30_s_before_the_due_time_matches_nothing(self) -> None:
        stream = stream_of([*turn(0, *schedule("W", 0, stated=100)), *fire("early", 70)])
        self.assertEqual(kinds(stream), [ws.UNMATCHED_BRACKET])
        self.assertEqual(stream.wakeups["W"].state, ws.PENDING)

    def test_a_bracketed_turn_with_no_timestamped_event_is_open_time_unknown(self) -> None:
        stream = stream_of([*turn(0, *schedule("W", 0)), *fire("blind", None)])
        self.assertEqual(kinds(stream), [ws.BRACKETED_TURN_OPEN_TIME_UNKNOWN])
        self.assertEqual(stream.wakeups["W"].state, ws.PENDING)

    def test_the_state_never_reads_a_clock(self) -> None:
        def no_clock(*_args, **_kwargs):
            raise AssertionError("the stream state read a clock")

        class NoClockDatetime(datetime.datetime):
            now = classmethod(no_clock)
            utcnow = classmethod(no_clock)
            today = classmethod(no_clock)

        stub = types.SimpleNamespace(datetime=NoClockDatetime, timezone=datetime.timezone)
        with unittest.mock.patch.object(ws, "datetime", stub), \
                unittest.mock.patch("time.time", no_clock), \
                unittest.mock.patch("time.time_ns", no_clock), \
                unittest.mock.patch("time.monotonic", no_clock), \
                unittest.mock.patch("time.perf_counter", no_clock):
            for name in ("p5_p11_wakeup_fires", "p12_stop_inside_fire_nested", "job_8b244f42_implementing"):
                outcome, _, diagnosis = classify(load(name), settled_wakeups=(ALPHA, BRAVO))
                self.assertIn(outcome, (ws.SUCCESS, ws.AMBIGUOUS))
                self.assertTrue(diagnosis["wakeups_seen"])


class OriginTest(unittest.TestCase):

    ORIGINS = {
        "no origin": None,
        "task-notification": {"kind": "task-notification"},
        "peer": {"kind": "peer", "from": "a1", "senderTaskId": "a1", "body": "x", "handback": True},
        "unknown kind": {"kind": "something-new"},
    }

    def test_an_unbracketed_turn_after_the_due_time_resolves_nothing_whatever_its_origin(self) -> None:
        for name, origin in self.ORIGINS.items():
            with self.subTest(name):
                fields = {} if origin is None else {"origin": origin}
                # Shaped like P11's fire turn: system/init, no opening user
                # event, an assistant event, a result.
                stream = stream_of([*turn(0, *schedule("W", 0, stated=100)), *turn(160, **fields)])
                self.assertEqual(stream.wakeups["W"].state, ws.PENDING)
                self.assertFalse(stream.quiescent())
                self.assertEqual(stream.anomalies, [])

    def test_origins_injected_into_p5s_fire_results_change_nothing(self) -> None:
        baseline = load("p5_p11_wakeup_fires")
        expected = (stream_of(baseline).snapshot(), classify(baseline)[0])
        for name, origin in self.ORIGINS.items():
            if origin is None:
                continue
            with self.subTest(name):
                events = load("p5_p11_wakeup_fires")
                for index in (23, 31):
                    self.assertEqual(events[index]["type"], "result")
                    events[index]["origin"] = origin
                self.assertEqual((stream_of(events).snapshot(), classify(events)[0]), expected)

    def test_the_first_turn_never_matches_and_a_bracket_around_it_is_bracketed_first_turn(self) -> None:
        first = [init(), say(1000), *schedule("W", 0, stated=60), result()]
        unbracketed = stream_of(first)
        self.assertEqual(unbracketed.wakeups["W"].state, ws.PENDING)
        bracketed = stream_of([lc("first", "started"), *first, lc("first", "completed")])
        self.assertEqual(kinds(bracketed), [ws.BRACKETED_FIRST_TURN])
        self.assertEqual(bracketed.wakeups["W"].state, ws.PENDING)


class BracketCorrelationTest(unittest.TestCase):
    """Each case is the P5 fixture edited in memory; every anomaly is row 3."""

    def p5(self) -> list[dict]:
        return load("p5_p11_wakeup_fires")

    def assert_row_3(self, events: list) -> None:
        outcome, _, diagnosis = classify(events, settled_wakeups=(ALPHA, BRAVO))
        self.assertEqual((outcome, diagnosis["reason"]), (ws.AMBIGUOUS, ws.REASON_LIFECYCLE_IRREGULAR))

    def test_a_missing_started_is_completed_without_started_and_the_wakeup_stays_pending(self) -> None:
        events = self.p5()
        del events[13]
        stream = stream_of(events[:24])  # through ALPHA's completed
        self.assertEqual(kinds(stream), [ws.COMPLETED_WITHOUT_STARTED])
        self.assertEqual(stream.wakeups[ALPHA].state, ws.PENDING)
        self.assert_row_3(events)

    def test_a_missing_completed_leaves_the_bracket_open_and_owned(self) -> None:
        events = self.p5()
        del events[32]
        stream = stream_of(events)
        self.assertEqual(stream.anomalies, [])
        self.assertEqual(stream.owned_work()["open_brackets"], [Y_UUID])
        self.assertFalse(stream.quiescent())
        self.assertEqual(classify(events, ending=None)[2]["reason"], ws.REASON_STDIN_CLOSED_WHILE_WAITING)
        diagnosis = classify(events, settled_wakeups=(ALPHA,))[2]
        self.assertEqual(diagnosis["reason"], ws.REASON_OWNED_WORK_KILLED)
        self.assertEqual(diagnosis["command_lifecycle_anomalies"], [])

    def test_a_duplicated_started_while_open_and_after_completed(self) -> None:
        while_open = self.p5()
        while_open.insert(14, dict(while_open[13]))
        after = self.p5() + [lc(X_UUID, "started"), lc(X_UUID, "completed")]
        for name, events in (("while open", while_open), ("after completed", after)):
            with self.subTest(name):
                stream = stream_of(events)
                self.assertEqual(kinds(stream), [ws.DUPLICATE_STARTED])
                self.assert_row_3(events)
        # Through X's completed (shifted by the inserted line): X matched nothing.
        self.assertEqual(stream_of(while_open[:26]).wakeups[ALPHA].state, ws.PENDING)
        self.assertEqual([b.state for b in stream_of(after).brackets][-1], ws.IRREGULAR)

    def test_a_duplicated_completed_is_completed_without_started(self) -> None:
        events = self.p5()
        events.insert(25, dict(events[24]))
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.COMPLETED_WITHOUT_STARTED])
        self.assertEqual(stream.wakeups[ALPHA].state, ws.FIRE_MATCHED)
        self.assert_row_3(events)

    def test_a_command_uuid_reused_by_a_later_fire(self) -> None:
        events = self.p5()
        for index in (25, 32):
            events[index]["command_uuid"] = X_UUID
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.DUPLICATE_STARTED])
        self.assertEqual(stream.wakeups[BRAVO].state, ws.PENDING)
        self.assert_row_3(events)

    def test_completed_before_started(self) -> None:
        events = self.p5()
        events.insert(13, events.pop(24))
        stream = stream_of(events)
        self.assertEqual(kinds(stream)[:2], [ws.COMPLETED_WITHOUT_STARTED, ws.DUPLICATE_STARTED])
        self.assertEqual(stream.wakeups[ALPHA].state, ws.PENDING)
        self.assert_row_3(events)

    def test_a_completed_delayed_past_the_next_turns_opening(self) -> None:
        events = self.p5()[:25]
        events[24:24] = turn(None)  # a turn between ALPHA's result and its completed
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.MULTIPLE_BRACKETED_TURNS])
        self.assertEqual(stream.wakeups[ALPHA].state, ws.PENDING)
        self.assert_row_3(events)

    def test_a_completed_delayed_but_in_order_is_regular_and_matches_only_at_completed(self) -> None:
        events = self.p5()
        events[24:24] = [tasks_changed(), {"type": "rate_limit_event"}, {"type": "system", "subtype": "status"}]
        stream = ws.WorkerStream()
        for index, event in enumerate(events):
            stream.feed(text([event]).encode())
            if index in (24, 25, 26):
                self.assertEqual(stream.wakeups[ALPHA].state, ws.PENDING)
        self.assertEqual(stream.wakeups[ALPHA].state, ws.FIRE_MATCHED)
        self.assertEqual(stream.anomalies, [])

    def test_started_inside_an_open_turn(self) -> None:
        events = self.p5()
        events.insert(15, events.pop(13))  # after the fire turn's system/init
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.STARTED_MID_TURN])
        self.assertEqual(stream.brackets[0].state, ws.IRREGULAR)
        self.assert_row_3(events)

    def test_two_overlapping_brackets_are_both_irregular_and_both_owned(self) -> None:
        events = self.p5()[:25]
        events.insert(14, lc("Z", "started"))
        events.append(lc("Z", "completed"))
        stream = ws.WorkerStream()
        for index, event in enumerate(events):
            stream.feed(text([event]).encode())
            if index == 25:  # X completed (shifted by Z's started), Z still open
                self.assertEqual(stream.owned_work()["open_brackets"], ["Z"])
                self.assertFalse(stream.quiescent())
        self.assertEqual(kinds(stream), [ws.OVERLAPPING_BRACKETS, ws.OVERLAPPING_BRACKETS])
        self.assertEqual({b.state for b in stream.brackets}, {ws.IRREGULAR})
        self.assertIsNone(stream.brackets[0].matched_wakeup)
        self.assertEqual(stream.wakeups[ALPHA].state, ws.PENDING)
        self.assert_row_3(events)

    def test_a_pair_with_no_turn_and_a_pair_around_two_turns(self) -> None:
        empty = self.p5() + [lc("Z", "started"), lc("Z", "completed")]
        self.assertEqual(kinds(stream_of(empty)), [ws.NO_BRACKETED_TURN])
        two = self.p5() + [lc("Z", "started"), *turn(None), *turn(None), lc("Z", "completed")]
        self.assertEqual(kinds(stream_of(two)), [ws.MULTIPLE_BRACKETED_TURNS])
        self.assert_row_3(empty)
        self.assert_row_3(two)

    def test_a_bracket_around_a_task_completion_turn_with_no_wakeup_is_unmatched(self) -> None:
        events = load("p3_streaming_background_bash")
        events.insert(16, lc("T", "started"))
        events.append(lc("T", "completed"))
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.UNMATCHED_BRACKET])
        self.assert_row_3(events)

    def test_a_malformed_lifecycle_event_opens_and_closes_nothing(self) -> None:
        cases = {
            "no command_uuid": lc(_MISSING, "started"),
            "a non-string command_uuid": lc(7, "started"),
            "an empty command_uuid": lc("", "started"),
            "state running": lc("Z", "running"),
            "no state": lc("Z"),
        }
        for name, event in cases.items():
            with self.subTest(name):
                events = self.p5()
                events.insert(24, event)
                stream = stream_of(events)
                self.assertEqual(kinds(stream), [ws.MALFORMED_LIFECYCLE_EVENT])
                self.assertEqual([b.command_uuid for b in stream.brackets], [X_UUID, Y_UUID])
                self.assertEqual({b.state for b in stream.brackets}, {ws.CLOSED_REGULAR})
                self.assert_row_3(events)

    def test_an_early_anomaly_is_sticky_with_the_later_rows_secondary(self) -> None:
        events = [lc("Z", "completed"), *turn(0)]
        outcome, _, diagnosis = classify(events)
        self.assertEqual((outcome, diagnosis["reason"]), (ws.AMBIGUOUS, ws.REASON_LIFECYCLE_IRREGULAR))
        self.assertEqual(diagnosis["secondary_reasons"], [])
        diagnosis = classify(events, ending=None)[2]
        self.assertEqual(diagnosis["reason"], ws.REASON_LIFECYCLE_IRREGULAR)
        self.assertEqual(diagnosis["secondary_reasons"], [ws.REASON_STDIN_CLOSED_WHILE_WAITING])
        self.assertEqual(diagnosis["command_lifecycle_anomalies"][0]["offset"], 0)

    def test_three_sequential_brackets_match_three_wakeups_in_due_order(self) -> None:
        events = [
            *turn(0, *schedule("A", 0, stated=100), *schedule("B", 1, stated=199), *schedule("C", 2, stated=298)),
            *fire("f1", 100), *fire("f2", 200), *fire("f3", 300),
        ]
        stream = stream_of(events)
        self.assertEqual([(b.command_uuid, b.matched_wakeup) for b in stream.brackets],
                         [("f1", "A"), ("f2", "B"), ("f3", "C")])
        self.assertEqual(stream.anomalies, [])

    def test_one_bracket_after_two_due_times_matches_the_earliest_and_leaves_the_other(self) -> None:
        stream = stream_of([*turn(0, *schedule("A", 0, stated=150), *schedule("B", 1, stated=99)),
                            *fire("f", 200)])
        self.assertEqual(stream.brackets[0].matched_wakeup, "B")
        self.assertEqual((stream.wakeups["A"].state, stream.wakeups["B"].state), (ws.PENDING, ws.FIRE_MATCHED))

    def test_open_brackets_are_owned_work_at_every_chunking(self) -> None:
        """Not quiescent between started(X) and the turn's system/init, nor
        between the fire turn's result and completed(X)."""
        data = text(self.p5()).encode()
        line_ends = [i + 1 for i, byte in enumerate(data) if byte == ord("\n")]
        guarded = set(range(13, 14)) | set(range(23, 24)) | set(range(31, 32))
        rng = random.Random(7)
        for size in (1, 5, 64, 1000, len(data), None):
            with self.subTest(size=size):
                stream, position = ws.WorkerStream(), 0
                while position < len(data):
                    step = size or rng.randint(1, 400)
                    stream.feed(data[position:position + step])
                    position += step
                    last_line = sum(1 for end in line_ends if end <= stream.offset) - 1
                    if last_line in guarded:
                        self.assertTrue(stream.owned_work()["open_brackets"])
                        self.assertFalse(stream.quiescent())


class WrongMatchTest(unittest.TestCase):
    """Round 9's I1: a regular-shaped bracket around some other turn after
    ``W``'s due time matches ``W``, which stays owned."""

    def test_the_spurious_match_leaves_w_owned_so_the_worker_is_never_quiescent(self) -> None:
        events = dangerous_prefix()
        spurious_completed = len(events) - 1
        for index, stream in enumerate(states_by_line(events)):
            if index >= spurious_completed:
                self.assertEqual(stream.wakeups["W"].state, ws.FIRE_MATCHED)
                self.assertEqual(stream.owned_work()["fire_matched_wakeups"], ["W"])
            if index >= 1:
                self.assertFalse(stream.quiescent())

    def test_the_real_fire_then_is_unmatched_bracket(self) -> None:
        events = [*dangerous_prefix(), *fire("real", 200)]
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.UNMATCHED_BRACKET])
        outcome, _, diagnosis = classify(events, settled_wakeups=("W",))
        self.assertEqual((outcome, diagnosis["reason"]), (ws.AMBIGUOUS, ws.REASON_LIFECYCLE_IRREGULAR))

    def test_a_stop_then_reporting_the_still_held_wakeup_is_a_count_mismatch(self) -> None:
        events = [*dangerous_prefix(), *turn(160, *stop("S", 160, 1))]
        outcome, _, diagnosis = classify(events)
        self.assertEqual((outcome, diagnosis["reason"]), (ws.AMBIGUOUS, ws.REASON_LIFECYCLE_IRREGULAR))
        [anomaly] = diagnosis["command_lifecycle_anomalies"]
        self.assertEqual((anomaly["kind"], anomaly["expected_count"], anomaly["cancelled_wakeups"]),
                         (ws.WAKEUP_COUNT_MISMATCH, 0, 1))
        wakeup = by_id(diagnosis, "wakeups_seen", "tool_use_id", "W")
        self.assertEqual((wakeup["state"], wakeup["settled_by"], wakeup["cancelled_wakeups"],
                          wakeup["expected_count"]), (ws.SETTLED, "stop", 1, 0))

    def test_the_settle_fact_alone_makes_the_prefix_quiescent(self) -> None:
        stream = stream_of(dangerous_prefix(), ws.SupervisorFacts(settled_wakeups=("W",)))
        self.assertTrue(stream.quiescent())
        self.assertEqual(stream.wakeups["W"].settled_by, "settle_window")

    def test_with_a_second_due_wakeup_the_real_fire_matches_it(self) -> None:
        events = [*dangerous_prefix(second_wakeup=True), *fire("real", 200)]
        stream = stream_of(events)
        self.assertEqual(stream.anomalies, [])
        self.assertEqual([b.matched_wakeup for b in stream.brackets], ["W", "W2"])
        stream.settle_wakeups(["W"])
        self.assertFalse(stream.quiescent())
        stream.settle_wakeups(["W2"])
        self.assertTrue(stream.quiescent())

    def test_the_twin_before_the_due_time_is_unmatched_and_the_real_fire_matches(self) -> None:
        events = [*dangerous_prefix(spurious_at=90), *fire("real", 200)]
        stream = ws.WorkerStream()
        prefix = dangerous_prefix(spurious_at=90)
        stream.feed(text(prefix).encode())
        self.assertEqual(kinds(stream), [ws.UNMATCHED_BRACKET])
        self.assertEqual(stream.wakeups["W"].state, ws.PENDING)
        stream.feed(text(events[len(prefix):]).encode())
        self.assertEqual(stream.brackets[-1].matched_wakeup, "W")
        self.assertEqual(classify(events, settled_wakeups=("W",))[2]["reason"], ws.REASON_LIFECYCLE_IRREGULAR)


class SettlementTest(unittest.TestCase):

    def test_a_stop_after_a_clean_fire_settles_the_matched_wakeup(self) -> None:
        events = [*turn(0, *schedule("W", 0, stated=100)), *fire("f", 101), *turn(130, *stop("S", 130, 0))]
        stream = stream_of(events)
        self.assertEqual(stream.anomalies, [])
        self.assertEqual((stream.wakeups["W"].state, stream.wakeups["W"].settled_by), (ws.SETTLED, "stop"))
        self.assertTrue(stream.quiescent())

    def _p5_stop_inside_alpha(self, *, nested: bool, cancelled: object) -> list[dict]:
        events = load("p5_p11_wakeup_fires")[:25]  # BRAVO's bracket removed
        stop_pair = stop("toolu_stop", 0, cancelled)
        for event in stop_pair:
            event["timestamp"] = "2026-09-25T15:38:03.500Z"
        events[20:20] = stop_pair  # after BRAVO's schedule (18-19), before ALPHA's result
        if not nested:
            del events[18:20]
        return events

    def test_a_stop_inside_a_fire_turn_settles_with_its_provisional_match(self) -> None:
        for nested, cancelled in ((True, 1), (False, 0)):
            with self.subTest(nested=nested):
                events = self._p5_stop_inside_alpha(nested=nested, cancelled=cancelled)
                stream = stream_of(events)
                self.assertEqual(stream.anomalies, [])
                [bracket] = stream.brackets
                self.assertEqual((bracket.state, bracket.provisional, bracket.matched_wakeup),
                                 (ws.CLOSED_REGULAR, ALPHA, ALPHA))
                self.assertEqual(stream.wakeup_stops[0]["expected_count"], cancelled)
                self.assertEqual((stream.wakeups[ALPHA].settled_by,
                                  stream.wakeups[ALPHA].settle_detail["command_uuid"]), ("stop", X_UUID))
                if nested:
                    self.assertEqual(stream.wakeups[BRAVO].settled_by, "stop")
                    self.assertNotIn("command_uuid", stream.wakeups[BRAVO].settle_detail)
                self.assertTrue(stream.quiescent())
                self.assertEqual(classify(events)[0], ws.SUCCESS)

    def test_a_harness_still_counting_the_running_wakeup_is_a_count_mismatch(self) -> None:
        events = self._p5_stop_inside_alpha(nested=False, cancelled=1)
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.WAKEUP_COUNT_MISMATCH])
        self.assertEqual(stream.anomalies[0]["command_uuid"], X_UUID)
        self.assertEqual(stream.brackets[0].state, ws.CLOSED_REGULAR)
        self.assertEqual(classify(events)[2]["reason"], ws.REASON_LIFECYCLE_IRREGULAR)

    def test_the_wrong_match_variant_has_no_candidate_and_no_mismatch_but_is_unmatched(self) -> None:
        events = [*dangerous_prefix(), *fire("real", 200, *stop("S", 200, 0))]
        stream = stream_of(events)
        self.assertEqual(kinds(stream), [ws.UNMATCHED_BRACKET])
        self.assertEqual(stream.wakeup_stops[0]["expected_count"], 0)
        self.assertIsNone(stream.brackets[-1].provisional)
        self.assertEqual(classify(events)[2]["reason"], ws.REASON_LIFECYCLE_IRREGULAR)

    def test_the_spurious_bracket_variant_is_a_count_mismatch(self) -> None:
        events = dangerous_prefix(stop_in_spurious=1)
        stream = stream_of(events)
        self.assertEqual(stream.brackets[0].provisional, "W")
        self.assertEqual(kinds(stream), [ws.WAKEUP_COUNT_MISMATCH])
        self.assertEqual(stream.wakeup_stops[0]["expected_count"], 0)

    def test_a_stop_inside_an_already_broken_bracket_fixes_no_provisional_match(self) -> None:
        second_turn = [*turn(0, *schedule("W", 0, stated=100)), lc("b", "started"), *turn(None),
                       *turn(120, *stop("S", 120, 0)), lc("b", "completed")]
        second_bracket = [*turn(0, *schedule("W", 0, stated=100)), lc("b", "started"), lc("c", "started"),
                          *turn(120, *stop("S", 120, 0)), lc("b", "completed"), lc("c", "completed")]
        for name, events in (("second turn", second_turn), ("second open bracket", second_bracket)):
            with self.subTest(name):
                stream = stream_of(events)
                self.assertIsNone(stream.brackets[0].provisional)
                self.assertEqual(stream.wakeup_stops[0]["expected_count"], 1)
                self.assertIn(ws.WAKEUP_COUNT_MISMATCH, kinds(stream))

    def test_the_count_comparison(self) -> None:
        base = turn(0, *schedule("W", 0, stated=100))
        cases = {"higher": (2, True), "lower": (0, True), "equal": (1, False),
                 "missing": (_MISSING, False), "non-integer": ("1", False), "boolean": (True, False),
                 "float": (1.0, False)}
        for name, (cancelled, mismatch) in cases.items():
            with self.subTest(name):
                stream = stream_of([*base, *turn(10, *stop("S", 10, cancelled))])
                self.assertEqual(kinds(stream), [ws.WAKEUP_COUNT_MISMATCH] if mismatch else [])
                self.assertEqual(stream.wakeups["W"].state, ws.SETTLED)
                self.assertEqual(stream.wakeup_stops[0]["compared"], cancelled in (0, 1, 2) and
                                 not isinstance(cancelled, (bool, float)))

    def test_an_is_error_stop_settles_nothing(self) -> None:
        stream = stream_of([*turn(0, *schedule("W", 0)), *turn(10, *stop("S", 10, error=True))])
        self.assertEqual(stream.wakeups["W"].state, ws.PENDING)
        self.assertEqual(stream.wakeup_stops[0]["is_error"], True)

    def test_a_successful_stop_settles_pending_and_fire_matched_alike(self) -> None:
        stream = stream_of([*turn(0, *schedule("A", 0, stated=100)), *fire("f", 100, *schedule("B", 101)),
                            *turn(110, *stop("S", 110, 1))])
        self.assertEqual({w.state for w in stream.wakeups.values()}, {ws.SETTLED})
        self.assertEqual(stream.anomalies, [])

    def test_settled_wakeups_naming_a_pending_or_unknown_wakeup_is_ignored(self) -> None:
        events = turn(0, *schedule("W", 0))
        stream = stream_of(events, ws.SupervisorFacts(settled_wakeups=("W", "nope")))
        self.assertEqual(stream.wakeups["W"].state, ws.PENDING)
        diagnosis = classify(events, settled_wakeups=("W", "nope"))[2]
        self.assertEqual(diagnosis["ignored_settlements"],
                         [{"tool_use_id": "W", "wakeup_state": ws.PENDING},
                          {"tool_use_id": "nope", "wakeup_state": None}])

    def test_an_unsettled_matched_wakeup_at_exit_is_row_7(self) -> None:
        events = [*turn(0, *schedule("W", 0, stated=100)), *fire("f", 100)]
        self.assertEqual(classify(events)[2]["reason"], ws.REASON_OWNED_WORK_KILLED)
        diagnosis = classify(events, ending=None)[2]
        self.assertEqual((diagnosis["reason"], diagnosis["secondary_reasons"]),
                         (ws.REASON_STDIN_CLOSED_WHILE_WAITING, [ws.REASON_OWNED_WORK_KILLED]))
        self.assertEqual(classify(events, settled_wakeups=("W",))[0], ws.SUCCESS)

    def test_an_errored_schedule_is_not_pending(self) -> None:
        stream = stream_of(turn(0, *schedule("W", 0, error=True)))
        self.assertEqual(stream.wakeups, {})
        self.assertTrue(stream.quiescent())


class StateTest(unittest.TestCase):

    def test_2857a730s_unlisted_foreground_tasks_never_make_the_worker_wait(self) -> None:
        events = load("job_2857a730_two_results")
        listed_ever: set[str] = set()
        for event in events:
            if event.get("subtype") == "background_tasks_changed":
                listed_ever |= {t["task_id"] for t in event["tasks"]}
        started = {e["task_id"] for e in events if e.get("subtype") == "task_started"}
        self.assertEqual(len(started - listed_ever), 20)
        for stream in states_by_line(events):
            self.assertLessEqual(set(stream.owned_work()["tasks"]), listed_ever)

    def test_a_listed_task_stays_open_until_a_terminal_status_or_a_list_without_it(self) -> None:
        stream = stream_of([*turn(0, tasks_changed("a", "b"))])
        self.assertEqual(stream.open_tasks(), ["a", "b"])
        stream.feed(text([task_end("a")[0]]).encode())
        self.assertEqual(stream.open_tasks(), ["b"])
        stream.feed(text([tasks_changed("a")]).encode())
        self.assertEqual(stream.open_tasks(), [])
        self.assertTrue(stream.quiescent())

    def test_a_queued_turn_is_not_quiescent_and_result_fields_are_carried(self) -> None:
        events = turn(0, queued_turn_count=1, terminal_reason="api_error", origin={"kind": "peer"})
        self.assertFalse(stream_of(events).quiescent())
        self.assertTrue(stream_of(turn(0, queued_turn_count=0)).quiescent())
        [entry] = classify(events)[2]["results"]
        self.assertEqual((entry["queued_turn_count"], entry["terminal_reason"], entry["origin"]),
                         (1, "api_error", {"kind": "peer"}))

    def test_quiescent_is_false_while_anything_is_open(self) -> None:
        cases = {
            "a turn": [init(), say(0)],
            "a task": turn(0, tasks_changed("t")),
            "a pending wakeup": turn(0, *schedule("W", 0)),
            "a fire_matched wakeup": [*turn(0, *schedule("W", 0, stated=100)), *fire("f", 100)],
            "a bracket": [*turn(0), lc("f", "started")],
            "no result yet": [init()],
        }
        for name, events in cases.items():
            with self.subTest(name):
                stream = stream_of([*events, {"type": "brand_new_event"}, {"type": "system", "subtype": "new"}])
                self.assertFalse(stream.quiescent())
                self.assertEqual(len(stream.unknown_events), 2)

    def test_incremental_and_whole_stream_parsing_agree_at_any_chunking(self) -> None:
        rng = random.Random(11)
        for name in FIXTURES:
            with self.subTest(name):
                data = b"".join(raw_lines(name))
                whole = ws.read_stream(data).snapshot()
                for _ in range(3):
                    stream, position = ws.WorkerStream(), 0
                    while position < len(data):
                        step = rng.randint(1, 2000)
                        stream.feed(data[position:position + step])
                        position += step
                    stream.finish()
                    self.assertEqual(stream.snapshot(), whole)

    def test_a_partial_trailing_line_is_never_consumed_before_eof(self) -> None:
        data = text(turn(0)).encode()
        stream = ws.WorkerStream()
        stream.feed(data[:-1])  # the result line without its newline
        self.assertEqual(stream.results, [])
        self.assertEqual(stream.offset, len(data) - len(data.splitlines()[-1]) - 1)
        stream.feed(b"\n")
        self.assertEqual(len(stream.results), 1)
        unterminated = ws.WorkerStream()
        unterminated.feed(data[:-1])
        unterminated.finish()
        self.assertEqual(len(unterminated.results), 1)

    def test_a_multibyte_character_split_across_chunks_is_consumed_whole(self) -> None:
        data = text([init(), say(0, "café — ok"), result()]).encode()
        for split in range(1, len(data)):
            stream = ws.WorkerStream()
            stream.feed(data[:split])
            stream.feed(data[split:])
            self.assertEqual((stream.malformed_lines, len(stream.results)), ([], 1))

    def test_the_mode_is_explicit(self) -> None:
        with self.assertRaises(ValueError):
            ws.classify("", mode="guess", returncode=0)



def _telemetry_result(*, turns=1, duration=10, api=100, cost=1.0, tokens=(1, 2, 3, 4), models=None, **extra):
    """A ``result`` event carrying every figure telemetry reads."""
    event = {"type": "result", "subtype": "success", "is_error": False, "session_id": "s",
             "num_turns": turns, "duration_ms": duration, "duration_api_ms": api, "total_cost_usd": cost,
             "usage": dict(zip(("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                "cache_read_input_tokens"), tokens)),
             "modelUsage": models if models is not None else {
                 "m": {"inputTokens": 10, "outputTokens": 20, "cacheCreationInputTokens": 30,
                       "cacheReadInputTokens": 40, "costUSD": cost}},
             **extra}
    return event


class SessionTelemetryTest(unittest.TestCase):
    """``session_telemetry`` (settings-and-telemetry CP3, plan Design C, I6,
    I7): session totals over every result."""

    def test_the_two_result_contract_fixture_by_hand(self) -> None:
        stream = ws.read_stream((CONTRACT_DIR / "job_2857a730_two_results.jsonl").read_bytes())
        block = ws.session_telemetry([r["event"] for r in stream.results])
        self.assertEqual(block["results"], 2)
        self.assertEqual(block["turns"], 127 + 8)
        self.assertEqual(block["duration_ms"], 2224988 + 288682)
        # The summed per-turn usage of both results.
        self.assertEqual(block["tokens"], {"input": 244 + 18, "output": 174346 + 10729,
                                           "cache_creation": 454660 + 18641,
                                           "cache_read": 33010876 + 3800308})
        # The cumulative figures: the maximum (both results carry the same).
        self.assertEqual(block["cost_usd"], 24.249237599999997)
        self.assertEqual(block["duration_api_ms"], 2719274)
        self.assertEqual(block["models"]["claude-opus-5-5"]["output"], 326896)
        self.assertEqual(block["models"]["claude-opus-5-5"]["cost_usd"], 24.249237599999997)
        # The peer handback repeats the cumulative figures: reported once.
        self.assertEqual(block["problems"], [{"kind": ws.PROBLEM_CUMULATIVE_NOT_ADVANCED, "result_index": 1}])

    def test_a_lower_later_cumulative_figure_keeps_the_maximum(self) -> None:
        high = {"m": {"inputTokens": 9, "outputTokens": 90, "cacheCreationInputTokens": 900,
                      "cacheReadInputTokens": 9000, "costUSD": 5.0}}
        low = {"m": {"inputTokens": 1, "outputTokens": 10, "cacheCreationInputTokens": 100,
                     "cacheReadInputTokens": 1000, "costUSD": 2.0}}
        block = ws.session_telemetry([_telemetry_result(api=500, cost=5.0, models=high),
                                      _telemetry_result(api=200, cost=2.0, models=low)])
        self.assertEqual(block["cost_usd"], 5.0)
        self.assertEqual(block["duration_api_ms"], 500)
        self.assertEqual(block["models"], {"m": {"input": 9, "output": 90, "cache_creation": 900,
                                                 "cache_read": 9000, "cost_usd": 5.0}})
        # Not the last result's values, and not "not advanced": they changed.
        self.assertEqual(block["problems"], [])

    def test_per_field_maximum_across_models_and_results(self) -> None:
        first = {"a": {"inputTokens": 5, "outputTokens": 1, "cacheCreationInputTokens": 1,
                       "cacheReadInputTokens": 1, "costUSD": 1.0}}
        second = {"a": {"inputTokens": 1, "outputTokens": 7, "cacheCreationInputTokens": 1,
                        "cacheReadInputTokens": 1, "costUSD": 1.5},
                  "b": {"inputTokens": 2, "outputTokens": 2, "cacheCreationInputTokens": 2,
                        "cacheReadInputTokens": 2, "costUSD": 0.5}}
        block = ws.session_telemetry([_telemetry_result(cost=1.0, models=first),
                                      _telemetry_result(cost=2.0, models=second)])
        self.assertEqual(block["models"]["a"], {"input": 5, "output": 7, "cache_creation": 1, "cache_read": 1,
                                                "cost_usd": 1.5})
        self.assertEqual(block["models"]["b"]["cost_usd"], 0.5)

    def test_four_results_sum_and_advance(self) -> None:
        results = [_telemetry_result(turns=t, duration=10 * t, api=100 * (i + 1), cost=float(i + 1),
                                     tokens=(t, t, t, t)) for i, t in enumerate((107, 18, 11, 8))]
        block = ws.session_telemetry(results)
        self.assertEqual((block["results"], block["turns"], block["duration_ms"]), (4, 144, 1440))
        self.assertEqual((block["cost_usd"], block["duration_api_ms"]), (4.0, 400))
        self.assertEqual(block["tokens"], {"input": 144, "output": 144, "cache_creation": 144, "cache_read": 144})

    def test_not_advanced_needs_non_zero_usage(self) -> None:
        same = _telemetry_result()
        idle = _telemetry_result(tokens=(0, 0, 0, 0))
        block = ws.session_telemetry([same, idle])
        self.assertEqual(block["problems"], [])
        block = ws.session_telemetry([same, dict(same), dict(same)])
        self.assertEqual([p["result_index"] for p in block["problems"]], [1, 2])

    def test_malformed_usage_gives_null_figures_and_problems(self) -> None:
        cases = {
            "usage missing": ({k: v for k, v in _telemetry_result().items() if k != "usage"},
                              ("tokens",), {"kind": "missing_field", "field": "usage", "result_index": 1}),
            "usage not an object": (_telemetry_result(usage=[1]), ("tokens",),
                                    {"kind": "invalid_field", "field": "usage", "result_index": 1}),
            "a token not a number": (_telemetry_result(usage={"input_tokens": "7", "output_tokens": 1,
                                                              "cache_creation_input_tokens": 1,
                                                              "cache_read_input_tokens": 1}),
                                     ("tokens.input",),
                                     {"kind": "invalid_field", "field": "usage.input_tokens", "result_index": 1}),
            "cost missing": ({k: v for k, v in _telemetry_result().items() if k != "total_cost_usd"},
                             ("cost_usd",), {"kind": "missing_field", "field": "total_cost_usd", "result_index": 1}),
            "turns a boolean": (_telemetry_result(turns=True), ("turns",),
                                {"kind": "invalid_field", "field": "num_turns", "result_index": 1}),
            "modelUsage missing": ({k: v for k, v in _telemetry_result().items() if k != "modelUsage"},
                                   ("models",), {"kind": "missing_field", "field": "modelUsage", "result_index": 1}),
            "a model not an object": (_telemetry_result(models={"m": 3}), ("models.m",),
                                      {"kind": "invalid_field", "field": "modelUsage.m", "result_index": 1}),
        }
        for name, (bad, nulled, problem) in cases.items():
            with self.subTest(case=name):
                block = ws.session_telemetry([_telemetry_result(), bad])
                self.assertIn(problem, block["problems"])
                for path in nulled:
                    value = block
                    for key in path.split("."):
                        value = value[key]
                    if isinstance(value, dict):
                        self.assertEqual(set(value.values()), {None}, path)
                    else:
                        self.assertIsNone(value, path)
                # The figures the bad result does not touch stay computed.
                if "turns" not in nulled:
                    self.assertEqual(block["turns"], 2)

    def test_no_result_and_junk_never_raise(self) -> None:
        block = ws.session_telemetry([])
        self.assertEqual((block["results"], block["turns"], block["cost_usd"], block["models"]), (0, None, None, None))
        self.assertEqual(block["problems"], [{"kind": ws.PROBLEM_NO_RESULT}])
        block = ws.session_telemetry(["junk", None, {"usage": {"input_tokens": float("nan")}}])
        self.assertEqual(block["results"], 3)
        self.assertIsNone(block["turns"])
        json.dumps(block, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
