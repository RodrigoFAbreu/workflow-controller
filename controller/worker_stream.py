"""The worker stream state machine
(``workflow-controller-worker-lifecycle-ownership`` CP2).

A pure, incremental reader of a worker's ``--output-format stream-json``
stdout (``docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md``,
design B). It performs no I/O and never reads a clock: its state is a
function of the stream's complete lines and of the supervisor facts it is
given, so replaying a stream with the same facts reaches the same state.

:class:`WorkerStream` tracks

- turns: a turn opens at ``system/init``, or at an ``assistant``/``user``
  event while no turn is open, and closes at ``result``. Its open time is the
  ``timestamp`` of its first timestamped event;
- every ``result`` event, in order;
- owned background tasks: a task is open exactly when the latest
  ``system/background_tasks_changed`` list names it and it has no terminal
  status yet. A ``task_started`` task that no list names is a foreground
  tool call that ran long, not owned work;
- wakeups: every successful ``ScheduleWakeup``, keyed by its ``tool_use``
  id, ``pending`` -> ``fire_matched`` -> ``settled``. Only ``settled`` is not
  owned work;
- ``command_lifecycle`` brackets, correlated by ``command_uuid`` only. A
  *regular* bracket around one non-first turn that opens at or after a
  ``pending`` wakeup's due time (less :data:`WAKEUP_SKEW_SECONDS`) matches
  the earliest-due such wakeup at its ``completed``; anything irregular is a
  sticky anomaly and matches nothing. A ``result``'s ``origin`` is recorded
  and never read.

:func:`classify` runs once, after the worker has exited, and returns
``(outcome, terminal_result, stream_diagnosis)`` by the plan's nine ordered
rows, keeping the closed four outcomes of ``controller.worker``.

:func:`session_telemetry` (settings-and-telemetry CP3) reduces the
``result`` events to the session's totals; it reads nothing else and never
steers the classification.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import math
import re
from collections.abc import Iterable

SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
AMBIGUOUS = "AMBIGUOUS"
INTERRUPTED = "INTERRUPTED"

#: The two classification modes. ``print`` is every ``claude -p <task>``
#: stream (and every launch until CP3 switches ``worker.launch``);
#: ``streaming`` is a stream-json-input session the Controller ends itself.
PRINT = "print"
STREAMING = "streaming"
MODES = (PRINT, STREAMING)

#: How far before its due time a bracketed turn may open and still match a
#: wakeup (B, condition 6).
WAKEUP_SKEW_SECONDS = 5

#: The harness's clamp on ``delaySeconds``, used for the due time when the
#: ``tool_result`` states no ``in Ns`` of its own.
WAKEUP_MIN_DELAY_SECONDS = 60
WAKEUP_MAX_DELAY_SECONDS = 3600

#: Wakeup states.
PENDING = "pending"
FIRE_MATCHED = "fire_matched"
SETTLED = "settled"

#: Bracket states.
OPEN = "open"
CLOSED_REGULAR = "closed_regular"
IRREGULAR = "irregular"

#: Task statuses after which a task is no longer open.
TERMINAL_TASK_STATUSES = frozenset({"completed", "killed", "stopped", "failed"})
#: The terminal statuses that count as a kill for row 7.
KILL_TASK_STATUSES = frozenset({"killed", "stopped"})

#: Event kinds B reads nothing from and does not report as unknown: they
#: depend on the model, the account and the machine (the six kinds CP1's
#: replay comparison drops).
NON_STRUCTURAL_EVENTS = frozenset({
    ("system", "thinking_tokens"),
    ("rate_limit_event", None),
    ("system", "task_progress"),
    ("tool_progress", None),
    ("system", "commands_changed"),
    ("system", "vcs_state_changed"),
})

_KNOWN_SYSTEM_SUBTYPES = frozenset({
    "init", "background_tasks_changed", "task_started", "task_updated", "task_notification",
})

_REQUIRED_RESULT_FIELDS = ("session_id", "is_error")

_HARNESS_STATED_DELAY = re.compile(r"\(in (\d+)s\)")

# Terminal classification reasons, by row.
REASON_TIMEOUT = "timeout"
REASON_SIGNAL = "signal"
REASON_EXIT_STATUS = "exit_status"
REASON_MALFORMED_LINE = "malformed_line"
REASON_NO_RESULT = "no_result"
REASON_LIFECYCLE_IRREGULAR = "command_lifecycle_irregular"
REASON_WAKEUP_NOT_DELIVERED = "wakeup_not_delivered"
REASON_LIFECYCLE_UNTERMINATED = "command_lifecycle_unterminated"
REASON_STDIN_CLOSED_WHILE_WAITING = "stdin_closed_while_waiting"
REASON_EXITED_MID_TURN = "exited_mid_turn"
REASON_OWNED_WORK_KILLED = "owned_work_killed_at_exit"
REASON_RESULT_IS_ERROR = "result_is_error"
REASON_RESULT_INCOMPLETE = "result_incomplete"
REASON_QUIESCENT = "quiescent_terminal_turn"

# Anomaly kinds (B, "Irregular bracket"); every one is sticky row 3.
DUPLICATE_STARTED = "duplicate_started"
STARTED_MID_TURN = "started_mid_turn"
OVERLAPPING_BRACKETS = "overlapping_brackets"
NO_BRACKETED_TURN = "no_bracketed_turn"
MULTIPLE_BRACKETED_TURNS = "multiple_bracketed_turns"
BRACKETED_FIRST_TURN = "bracketed_first_turn"
BRACKETED_TURN_OPEN_TIME_UNKNOWN = "bracketed_turn_open_time_unknown"
UNMATCHED_BRACKET = "unmatched_bracket"
COMPLETED_WITHOUT_STARTED = "completed_without_started"
MALFORMED_LIFECYCLE_EVENT = "malformed_lifecycle_event"
WAKEUP_COUNT_MISMATCH = "wakeup_count_mismatch"


@dataclasses.dataclass(frozen=True)
class SupervisorFacts:
    """What only the supervisor knows, and the stream cannot show.

    ``ending_offset`` is the byte offset of the stream consumed when the
    supervisor ended the session (``ENDING``). The two ``*_declared_at``
    facts are the supervisor's own breach declarations (C), with the
    stalled bracket's ``command_uuid`` beside the second, and
    ``settled_wakeups`` names the ``fire_matched`` wakeups (by ``tool_use``
    id) whose settle window ran out."""

    timed_out: bool = False
    ending_offset: int | None = None
    wakeup_overdue_declared_at: str | None = None
    command_lifecycle_overdue_declared_at: str | None = None
    settled_wakeups: tuple[str, ...] = ()
    command_lifecycle_overdue_command_uuid: str | None = None

    def to_dict(self) -> dict:
        return {
            "timed_out": self.timed_out,
            "ending_offset": self.ending_offset,
            "wakeup_overdue_declared_at": self.wakeup_overdue_declared_at,
            "command_lifecycle_overdue_declared_at": self.command_lifecycle_overdue_declared_at,
            "command_lifecycle_overdue_command_uuid": self.command_lifecycle_overdue_command_uuid,
            "settled_wakeups": list(self.settled_wakeups),
        }


def _parse_timestamp(value: object) -> float | None:
    """Epoch seconds of an event's ISO-8601 ``timestamp``, or ``None``.
    Parsing a stated time reads no clock."""
    if not isinstance(value, str):
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _content_blocks(event: dict) -> list:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _tool_result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict) and isinstance(part.get("text"), str))
    return ""


@dataclasses.dataclass
class _Turn:
    index: int
    open_offset: int
    open_time: float | None = None
    close_offset: int | None = None


@dataclasses.dataclass
class _Task:
    task_id: str
    task_type: object = None
    description: object = None
    listed: bool = False
    status: str | None = None
    status_offset: int | None = None


@dataclasses.dataclass
class _Wakeup:
    tool_use_id: str
    scheduled_offset: int
    scheduled_at: float | None
    delay_seconds: object
    due: float | None
    due_source: str | None
    scheduled_for: object
    state: str = PENDING
    matched_by: str | None = None
    settled_by: str | None = None
    settle_detail: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class _Bracket:
    command_uuid: str
    started_offset: int
    completed_offset: int | None = None
    state: str = OPEN
    anomalies: list = dataclasses.field(default_factory=list)
    turns_opened: list = dataclasses.field(default_factory=list)
    matched_wakeup: str | None = None
    provisional_fixed: bool = False
    provisional: str | None = None

    @property
    def broken(self) -> bool:
        return bool(self.anomalies)


class WorkerStream:
    """The incremental state of one worker stream.

    :meth:`feed` takes raw stdout bytes in any chunking and consumes only
    complete lines; :meth:`finish` consumes a final unterminated line once
    the stream has reached EOF. :meth:`settle_wakeups` applies the
    supervisor's ``settled_wakeups`` fact."""

    def __init__(self) -> None:
        self._buffer = b""
        self.offset = 0
        self.line_count = 0
        self.turns: list[_Turn] = []
        self.turn_open = False
        self.results: list[dict] = []
        self.malformed_lines: list[dict] = []
        self.unknown_events: dict[tuple, dict] = {}
        self._listed: set[str] = set()
        self.tasks: dict[str, _Task] = {}
        self.wakeups: dict[str, _Wakeup] = {}
        self._schedule_calls: dict[str, tuple[dict, int]] = {}
        self.wakeup_stops: list[dict] = []
        self.brackets: list[_Bracket] = []
        self._open_brackets: dict[str, _Bracket] = {}
        self._uuids_seen: set[str] = set()
        self.anomalies: list[dict] = []
        self.ignored_settlements: list[dict] = []

    # -- input ---------------------------------------------------------

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while True:
            end = self._buffer.find(b"\n")
            if end < 0:
                return
            raw, self._buffer = self._buffer[:end], self._buffer[end + 1:]
            self._consume(raw, end + 1)

    def finish(self) -> None:
        """Consume a final line with no terminating newline (EOF only)."""
        if self._buffer:
            raw, self._buffer = self._buffer, b""
            self._consume(raw, len(raw))

    def _consume(self, raw: bytes, length: int) -> None:
        offset = self.offset
        self.offset += length
        line_number = self.line_count
        self.line_count += 1
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            event = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            event = None
        if not isinstance(event, dict):
            self.malformed_lines.append({"offset": offset, "line": line_number})
            return
        self._event(event, offset)

    # -- events --------------------------------------------------------

    def _event(self, event: dict, offset: int) -> None:
        kind = event.get("type")
        subtype = event.get("subtype") if kind == "system" else None
        if (kind, subtype) in NON_STRUCTURAL_EVENTS:
            return
        if kind == "system" and subtype == "init":
            self._open_turn(offset)
        elif kind in ("assistant", "user") and not self.turn_open:
            self._open_turn(offset)
        if self.turn_open:
            turn = self.turns[-1]
            if turn.open_time is None:
                turn.open_time = _parse_timestamp(event.get("timestamp"))
        if kind == "system" and subtype in _KNOWN_SYSTEM_SUBTYPES:
            self._system(subtype, event, offset)
        elif kind == "assistant":
            self._assistant(event, offset)
        elif kind == "user":
            self._user(event, offset)
        elif kind == "result":
            self._result(event, offset)
        elif kind == "command_lifecycle":
            self._lifecycle(event, offset)
        else:
            key = (kind if isinstance(kind, str) else repr(kind),
                   subtype if isinstance(subtype, str) or subtype is None else repr(subtype))
            entry = self.unknown_events.setdefault(
                key, {"type": key[0], "subtype": key[1], "count": 0, "first_offset": offset})
            entry["count"] += 1

    def _open_turn(self, offset: int) -> None:
        turn = _Turn(index=len(self.turns), open_offset=offset)
        self.turns.append(turn)
        self.turn_open = True
        for bracket in self._open_brackets.values():
            bracket.turns_opened.append(turn.index)

    def _system(self, subtype: str, event: dict, offset: int) -> None:
        if subtype == "background_tasks_changed":
            listed = set()
            tasks = event.get("tasks")
            for entry in tasks if isinstance(tasks, list) else []:
                if not isinstance(entry, dict) or not isinstance(entry.get("task_id"), str):
                    continue
                task = self._task(entry["task_id"])
                task.listed = True
                task.task_type = entry.get("task_type", task.task_type)
                task.description = entry.get("description", task.description)
                listed.add(task.task_id)
            self._listed = listed
            return
        task_id = event.get("task_id")
        if not isinstance(task_id, str):
            return
        task = self._task(task_id)
        if subtype == "task_started":
            if task.task_type is None:
                task.task_type = event.get("task_type")
            if task.description is None:
                task.description = event.get("description")
            return
        status = event.get("patch", {}).get("status") if subtype == "task_updated" \
            and isinstance(event.get("patch"), dict) else event.get("status")
        if isinstance(status, str) and status in TERMINAL_TASK_STATUSES:
            # The first terminal status fixes when the task stopped being
            # open; a later one (the notification after the update) only
            # refines the status name.
            if task.status is None:
                task.status_offset = offset
            if task.status is None or status in KILL_TASK_STATUSES:
                task.status = status

    def _task(self, task_id: str) -> _Task:
        return self.tasks.setdefault(task_id, _Task(task_id=task_id))

    def _assistant(self, event: dict, offset: int) -> None:
        for block in _content_blocks(event):
            if block.get("type") == "tool_use" and block.get("name") == "ScheduleWakeup" \
                    and isinstance(block.get("id"), str):
                tool_input = block.get("input")
                self._schedule_calls[block["id"]] = (tool_input if isinstance(tool_input, dict) else {}, offset)

    def _user(self, event: dict, offset: int) -> None:
        for block in _content_blocks(event):
            if block.get("type") != "tool_result":
                continue
            call = self._schedule_calls.pop(block.get("tool_use_id"), None)
            if call is None:
                continue
            tool_input, _ = call
            tool_use_id = block["tool_use_id"]
            if block.get("is_error"):
                if tool_input.get("stop") is True:
                    self.wakeup_stops.append({"tool_use_id": tool_use_id, "offset": offset,
                                              "is_error": True, "settled": []})
                continue
            tool_use_result = event.get("tool_use_result")
            if tool_input.get("stop") is True:
                self._stop(tool_use_id, tool_use_result, offset)
            else:
                self._schedule(tool_use_id, tool_input, block, event, tool_use_result, offset)

    def _schedule(self, tool_use_id: str, tool_input: dict, block: dict, event: dict,
                  tool_use_result: object, offset: int) -> None:
        scheduled_at = _parse_timestamp(event.get("timestamp"))
        delay = tool_input.get("delaySeconds")
        stated = _HARNESS_STATED_DELAY.search(_tool_result_text(block))
        due = due_source = None
        if scheduled_at is not None:
            if stated:
                due, due_source = scheduled_at + int(stated.group(1)), "harness_stated"
            elif isinstance(delay, (int, float)) and not isinstance(delay, bool):
                clamped = min(max(delay, WAKEUP_MIN_DELAY_SECONDS), WAKEUP_MAX_DELAY_SECONDS)
                due, due_source = scheduled_at + clamped, "clamp"
        scheduled_for = tool_use_result.get("scheduledFor") if isinstance(tool_use_result, dict) else None
        self.wakeups[tool_use_id] = _Wakeup(
            tool_use_id=tool_use_id, scheduled_offset=offset, scheduled_at=scheduled_at,
            delay_seconds=delay, due=due, due_source=due_source, scheduled_for=scheduled_for,
        )

    def _candidate(self, open_time: float) -> _Wakeup | None:
        """Condition 6: the earliest-due ``pending`` wakeup whose due time
        less the skew is at or before ``open_time``."""
        admitted = [w for w in self.wakeups.values()
                    if w.state == PENDING and w.due is not None and w.due - WAKEUP_SKEW_SECONDS <= open_time]
        return min(admitted, key=lambda w: (w.due, w.scheduled_offset), default=None)

    def _stop(self, tool_use_id: str, tool_use_result: object, offset: int) -> None:
        bracket = self._provisional_bracket()
        if bracket is not None:
            bracket.provisional_fixed = True
            candidate = self._candidate(self.turns[-1].open_time)
            bracket.provisional = candidate.tool_use_id if candidate else None
        expected = sum(1 for w in self.wakeups.values()
                       if w.state == PENDING and not (bracket and w.tool_use_id == bracket.provisional))
        reported = tool_use_result.get("cancelledWakeups") if isinstance(tool_use_result, dict) else None
        compared = _is_int(reported)
        stop = {"tool_use_id": tool_use_id, "offset": offset, "is_error": False,
                "cancelled_wakeups": reported, "expected_count": expected, "compared": compared,
                "command_uuid": bracket.command_uuid if bracket else None,
                "provisional_match": bracket.provisional if bracket else None, "settled": []}
        if compared and reported != expected:
            inside = next(iter(self._open_brackets)) if len(self._open_brackets) == 1 else None
            self._anomaly(WAKEUP_COUNT_MISMATCH, inside, offset,
                          expected_count=expected, cancelled_wakeups=reported)
        for wakeup in self.wakeups.values():
            if wakeup.state == SETTLED:
                continue
            wakeup.state = SETTLED
            wakeup.settled_by = "stop"
            wakeup.settle_detail = {"stop_tool_use_id": tool_use_id, "cancelled_wakeups": reported,
                                    "expected_count": expected}
            if bracket is not None and wakeup.tool_use_id == bracket.provisional:
                wakeup.settle_detail["command_uuid"] = bracket.command_uuid
            stop["settled"].append(wakeup.tool_use_id)
        self.wakeup_stops.append(stop)

    def _provisional_bracket(self) -> _Bracket | None:
        """The one open bracket whose provisional match a stop arriving now
        fixes ("A stop inside a bracket", step 1), or ``None``."""
        if len(self._open_brackets) != 1 or not self.turn_open:
            return None
        bracket = next(iter(self._open_brackets.values()))
        turn = self.turns[-1]
        if bracket.broken or bracket.provisional_fixed or bracket.turns_opened != [turn.index]:
            return None
        if turn.index == 0 or turn.open_time is None:
            return None
        return bracket

    def _result(self, event: dict, offset: int) -> None:
        self.results.append({"event": event, "offset": offset, "end_offset": self.offset})
        if self.turn_open:
            self.turns[-1].close_offset = offset
            self.turn_open = False

    # -- command_lifecycle ---------------------------------------------

    def _anomaly(self, kind: str, command_uuid: str | None, offset: int, **extra) -> None:
        self.anomalies.append({"kind": kind, "command_uuid": command_uuid, "offset": offset, **extra})

    def _mark(self, bracket: _Bracket, kind: str, offset: int) -> None:
        if kind not in bracket.anomalies:
            bracket.anomalies.append(kind)
            self._anomaly(kind, bracket.command_uuid, offset)

    def _lifecycle(self, event: dict, offset: int) -> None:
        command_uuid, state = event.get("command_uuid"), event.get("state")
        if not isinstance(command_uuid, str) or not command_uuid or state not in ("started", "completed"):
            self._anomaly(MALFORMED_LIFECYCLE_EVENT,
                          command_uuid if isinstance(command_uuid, str) and command_uuid else None, offset)
            return
        if state == "started":
            self._started(command_uuid, offset)
        else:
            self._completed(command_uuid, offset)

    def _started(self, command_uuid: str, offset: int) -> None:
        if command_uuid in self._open_brackets:
            self._mark(self._open_brackets[command_uuid], DUPLICATE_STARTED, offset)
            return
        bracket = _Bracket(command_uuid=command_uuid, started_offset=offset)
        if command_uuid in self._uuids_seen:
            # A reused uuid opens a bracket that is irregular from the start;
            # its own completed(X) then closes it with no further anomaly.
            self._mark(bracket, DUPLICATE_STARTED, offset)
        self._uuids_seen.add(command_uuid)
        if self.turn_open:
            self._mark(bracket, STARTED_MID_TURN, offset)
        for other in self._open_brackets.values():
            self._mark(other, OVERLAPPING_BRACKETS, offset)
            self._mark(bracket, OVERLAPPING_BRACKETS, offset)
        self._open_brackets[command_uuid] = bracket
        self.brackets.append(bracket)

    def _completed(self, command_uuid: str, offset: int) -> None:
        bracket = self._open_brackets.pop(command_uuid, None)
        if bracket is None:
            self._uuids_seen.add(command_uuid)
            self._anomaly(COMPLETED_WITHOUT_STARTED, command_uuid, offset)
            return
        bracket.completed_offset = offset
        if not bracket.broken:
            turns = bracket.turns_opened
            turn = self.turns[turns[0]] if len(turns) == 1 else None
            if len(turns) > 1:
                self._mark(bracket, MULTIPLE_BRACKETED_TURNS, offset)
            elif turn is None or turn.close_offset is None:
                self._mark(bracket, NO_BRACKETED_TURN, offset)
            elif turn.index == 0:
                self._mark(bracket, BRACKETED_FIRST_TURN, offset)
            elif turn.open_time is None:
                self._mark(bracket, BRACKETED_TURN_OPEN_TIME_UNKNOWN, offset)
            elif bracket.provisional is not None:
                bracket.matched_wakeup = bracket.provisional
            else:
                wakeup = self._candidate(turn.open_time)
                if wakeup is None:
                    self._mark(bracket, UNMATCHED_BRACKET, offset)
                else:
                    wakeup.state = FIRE_MATCHED
                    wakeup.matched_by = command_uuid
                    bracket.matched_wakeup = wakeup.tool_use_id
        bracket.state = IRREGULAR if bracket.broken else CLOSED_REGULAR

    # -- supervisor facts ----------------------------------------------

    def settle_wakeups(self, tool_use_ids: Iterable[str]) -> None:
        """Apply ``settled_wakeups``: each named ``fire_matched`` wakeup is
        settled by its window. A name that is unknown, ``pending`` or already
        settled settles nothing and is recorded as ignored."""
        for tool_use_id in tool_use_ids:
            wakeup = self.wakeups.get(tool_use_id)
            if wakeup is None or wakeup.state != FIRE_MATCHED:
                self.ignored_settlements.append({
                    "tool_use_id": tool_use_id,
                    "wakeup_state": wakeup.state if wakeup else None,
                })
                continue
            wakeup.state = SETTLED
            wakeup.settled_by = "settle_window"
            wakeup.settle_detail = {"command_uuid": wakeup.matched_by}

    # -- queries -------------------------------------------------------

    def open_tasks(self) -> list[str]:
        return [task_id for task_id in sorted(self._listed) if self.tasks[task_id].status is None]

    def owned_work(self) -> dict:
        return {
            "tasks": self.open_tasks(),
            "pending_wakeups": [w.tool_use_id for w in self.wakeups.values() if w.state == PENDING],
            "fire_matched_wakeups": [w.tool_use_id for w in self.wakeups.values() if w.state == FIRE_MATCHED],
            "open_brackets": list(self._open_brackets),
        }

    def has_owned_work(self) -> bool:
        return any(self.owned_work().values())

    def quiescent(self) -> bool:
        if self.turn_open or not self.results or self.has_owned_work():
            return False
        queued = self.results[-1]["event"].get("queued_turn_count")
        return queued is None or queued == 0

    def snapshot(self) -> dict:
        """The state as plain data, with no stream offsets: what two
        feedings of the same events must agree on."""
        return {
            "turn_open": self.turn_open,
            "turns": len(self.turns),
            "turn_open_times": [t.open_time for t in self.turns],
            "result_count": len(self.results),
            "owned_work": self.owned_work(),
            "quiescent": self.quiescent(),
            "tasks": {t.task_id: (t.listed, t.status) for t in self.tasks.values()},
            "wakeups": {w.tool_use_id: (w.state, w.due, w.due_source, w.matched_by, w.settled_by)
                        for w in self.wakeups.values()},
            "brackets": [(b.command_uuid, b.state, list(b.anomalies), b.matched_wakeup, b.provisional)
                         for b in self.brackets],
            "anomalies": [a["kind"] for a in self.anomalies],
            "malformed_lines": len(self.malformed_lines),
        }

    # -- diagnosis -----------------------------------------------------

    def diagnosis(self, *, ending: int | None) -> dict:
        def ended(offset: int | None) -> str | None:
            if offset is None or ending is None:
                return None
            return "after_end" if offset >= ending else "before_end"

        return {
            "turns": len(self.turns),
            "turn_open_at_exit": self.turn_open,
            "result_count": len(self.results),
            "results": [{
                "offset": r["offset"],
                "queued_turn_count": r["event"].get("queued_turn_count"),
                "terminal_reason": r["event"].get("terminal_reason"),
                "origin": r["event"].get("origin"),
            } for r in self.results],
            "tasks_seen": [{
                "task_id": t.task_id, "task_type": t.task_type, "description": t.description,
                "listed": t.listed, "final_status": t.status, "status_offset": t.status_offset,
                "ended": ended(t.status_offset),
            } for t in self.tasks.values()],
            "wakeups_seen": [{
                "tool_use_id": w.tool_use_id, "scheduled_offset": w.scheduled_offset,
                "scheduled_at": _iso(w.scheduled_at), "delay_seconds": w.delay_seconds,
                "due_at": _iso(w.due), "due_source": w.due_source, "scheduled_for": w.scheduled_for,
                "state": w.state, "matched_by": w.matched_by, "settled_by": w.settled_by,
                **w.settle_detail,
            } for w in self.wakeups.values()],
            "wakeup_stops": [dict(stop) for stop in self.wakeup_stops],
            "command_lifecycles": [{
                "command_uuid": b.command_uuid, "started_offset": b.started_offset,
                "completed_offset": b.completed_offset, "state": b.state,
                "anomalies": list(b.anomalies),
                "turn_index": b.turns_opened[0] if len(b.turns_opened) == 1 else None,
                "turn_open_time": _iso(self.turns[b.turns_opened[0]].open_time)
                if len(b.turns_opened) == 1 else None,
                "turn_origin": self._turn_origin(b),
                "matched_wakeup": b.matched_wakeup,
                "provisional_match": b.provisional,
            } for b in self.brackets],
            "command_lifecycle_anomalies": [dict(a) for a in self.anomalies],
            "malformed_lines": list(self.malformed_lines),
            "unknown_events": list(self.unknown_events.values()),
            "ignored_settlements": list(self.ignored_settlements),
            "owned_work_at_exit": self.owned_work(),
        }

    def _turn_origin(self, bracket: _Bracket) -> object:
        """The bracketed turn's ``result`` ``origin``, recorded only."""
        if len(bracket.turns_opened) != 1:
            return None
        close = self.turns[bracket.turns_opened[0]].close_offset
        for result in self.results:
            if result["offset"] == close:
                return result["event"].get("origin")
        return None


def read_stream(data: bytes | str, facts: SupervisorFacts | None = None) -> WorkerStream:
    """The whole-stream state: every line of ``data`` (the final one even
    without a newline), then the supervisor's ``settled_wakeups``."""
    stream = WorkerStream()
    stream.feed(data.encode("utf-8") if isinstance(data, str) else data)
    stream.finish()
    if facts is not None:
        stream.settle_wakeups(facts.settled_wakeups)
    return stream


def classify(
    data: bytes | str,
    *,
    mode: str,
    facts: SupervisorFacts | None = None,
    returncode: int | None,
) -> tuple[str, dict | None, dict]:
    """The terminal classification of an exited worker's complete stream.

    ``returncode`` ``None`` means the exit status is unknown (a re-attach),
    which skips rows 1-2 on the exit status only. Returns ``(outcome,
    terminal_result, stream_diagnosis)``; ``terminal_result`` is the last
    ``result`` event, or ``None`` when there is none."""
    if mode not in MODES:
        raise ValueError(f"unknown stream mode {mode!r}")
    facts = facts or SupervisorFacts()
    stream = read_stream(data, facts)
    last = stream.results[-1] if stream.results else None
    terminal = last["event"] if last else None

    if mode == STREAMING and facts.ending_offset is not None:
        ending = facts.ending_offset
    else:
        # print mode, or a streaming session the supervisor never ended:
        # the last result stands in for the end.
        ending = last["end_offset"] if last else 0

    known = returncode is not None
    killed_after_end = [t.task_id for t in stream.tasks.values()
                        if t.status in KILL_TASK_STATUSES and t.status_offset is not None
                        and t.status_offset >= ending]

    matched: list[tuple[str, str]] = []
    if facts.timed_out:
        matched.append((INTERRUPTED, REASON_TIMEOUT))
    elif known and returncode < 0:
        matched.append((INTERRUPTED, REASON_SIGNAL))
    if known and returncode != 0:
        matched.append((FAILURE, REASON_EXIT_STATUS))
    if stream.malformed_lines:
        matched.append((AMBIGUOUS, REASON_MALFORMED_LINE))
    if not stream.results:
        matched.append((AMBIGUOUS, REASON_NO_RESULT))
    if stream.anomalies:
        matched.append((AMBIGUOUS, REASON_LIFECYCLE_IRREGULAR))
    if facts.wakeup_overdue_declared_at is not None:
        matched.append((AMBIGUOUS, REASON_WAKEUP_NOT_DELIVERED))
    if facts.command_lifecycle_overdue_declared_at is not None:
        matched.append((AMBIGUOUS, REASON_LIFECYCLE_UNTERMINATED))
    if mode == STREAMING and facts.ending_offset is None:
        matched.append((AMBIGUOUS, REASON_STDIN_CLOSED_WHILE_WAITING))
    if stream.turn_open:
        matched.append((AMBIGUOUS, REASON_EXITED_MID_TURN))
    if killed_after_end or stream.has_owned_work():
        matched.append((AMBIGUOUS, REASON_OWNED_WORK_KILLED))
    if terminal is not None:
        if not all(field in terminal for field in _REQUIRED_RESULT_FIELDS):
            matched.append((AMBIGUOUS, REASON_RESULT_INCOMPLETE))
        elif terminal["is_error"]:
            matched.append((FAILURE, REASON_RESULT_IS_ERROR))
    matched.append((SUCCESS, REASON_QUIESCENT))

    outcome, reason = matched[0]
    diagnosis = {
        "mode": mode,
        "reason": reason,
        "secondary_reasons": [r for _, r in matched[1:] if r != REASON_QUIESCENT],
        "exit_status_known": known,
        "exit_status": returncode,
        "supervisor_facts": facts.to_dict(),
        "ending_point": ending,
        "tasks_killed_after_end": killed_after_end,
        **stream.diagnosis(ending=ending),
    }
    return outcome, terminal, diagnosis


# ---------------------------------------------------------------------------
# Telemetry v0 (workflow-controller-settings-and-telemetry CP3, plan
# Design C, I6, I7).
# ---------------------------------------------------------------------------

#: The ``telemetry`` block's own version.
TELEMETRY_VERSION = 1

#: ``usage`` field -> ``tokens`` key: summed over every result (per turn).
_USAGE_TOKENS = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_creation_input_tokens", "cache_creation"),
    ("cache_read_input_tokens", "cache_read"),
)

#: ``modelUsage[<model>]`` field -> ``models[<model>]`` key: cumulative, so
#: the maximum over every result.
_MODEL_FIELDS = (
    ("inputTokens", "input"),
    ("outputTokens", "output"),
    ("cacheCreationInputTokens", "cache_creation"),
    ("cacheReadInputTokens", "cache_read"),
    ("costUSD", "cost_usd"),
)

PROBLEM_NO_RESULT = "no_result"
PROBLEM_MISSING_FIELD = "missing_field"
PROBLEM_INVALID_FIELD = "invalid_field"
PROBLEM_CUMULATIVE_NOT_ADVANCED = "cumulative_not_advanced"

_ABSENT = object()


def _number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class _Figure:
    """One reduced figure: ``None`` from the first contribution that is
    absent or not a number on, with one problem entry per such
    contribution (I6)."""

    def __init__(self, reduce, field: str, problems: list) -> None:
        self.reduce, self.field, self.problems = reduce, field, problems
        self.value: float | int | None = None
        self.valid = True

    def add(self, value: object, index: int) -> None:
        if value is _ABSENT or not _number(value):
            kind = PROBLEM_MISSING_FIELD if value is _ABSENT else PROBLEM_INVALID_FIELD
            self.problems.append({"kind": kind, "field": self.field, "result_index": index})
            self.valid = False
            return
        self.value = value if self.value is None else self.reduce(self.value, value)

    def final(self) -> float | int | None:
        return self.value if self.valid else None


def _get(mapping: object, key: str) -> object:
    return mapping.get(key, _ABSENT) if isinstance(mapping, dict) else _ABSENT


def _cumulative_projection(event: dict) -> tuple:
    """A result's cumulative figures, as compared for
    ``cumulative_not_advanced``: cost, API time and the per-model fields."""
    usage = event.get("modelUsage")
    models = tuple(sorted(
        (str(model), tuple(_get(fields, name) if isinstance(fields, dict) else _ABSENT
                           for name, _key in _MODEL_FIELDS))
        for model, fields in usage.items()
    )) if isinstance(usage, dict) else None
    return (event.get("total_cost_usd"), event.get("duration_api_ms"), models)


def _usage_nonzero(event: dict) -> bool:
    usage = event.get("usage")
    return isinstance(usage, dict) and any(
        _number(usage.get(name)) and usage.get(name) != 0 for name, _key in _USAGE_TOKENS)


def session_telemetry(results: Iterable[dict]) -> dict:
    """The session totals of ``results``, the ``result`` events in stream
    order (plan Design C, I7).

    Turns, ``duration_ms`` and the ``usage`` tokens are per turn, so they
    are summed. Cost, API time and every ``modelUsage`` field are
    cumulative, so each is the **maximum** over all results -- never the
    last result's value, which a subagent handback (``origin.kind:
    "peer"``) can repeat unchanged or lower. A result with non-zero
    ``usage`` whose cumulative figures equal the previous result's adds a
    ``cumulative_not_advanced`` problem; it is reported, not corrected.

    A contribution that is absent or not a number makes its figure
    ``None`` and adds a problem entry (I6); a session with no result has
    ``None`` figures and a ``no_result`` problem. Pure: it never raises on
    any shape of event list."""
    events = [event if isinstance(event, dict) else {} for event in results]
    problems: list[dict] = []
    block: dict = {"version": TELEMETRY_VERSION, "results": len(events)}
    if not events:
        problems.append({"kind": PROBLEM_NO_RESULT})
        block.update(turns=None, duration_ms=None, duration_api_ms=None, cost_usd=None,
                     tokens={key: None for _name, key in _USAGE_TOKENS}, models=None, problems=problems)
        return block

    def total(a, b):
        return a + b

    turns = _Figure(total, "num_turns", problems)
    duration = _Figure(total, "duration_ms", problems)
    api = _Figure(max, "duration_api_ms", problems)
    cost = _Figure(max, "total_cost_usd", problems)
    tokens = {key: _Figure(total, f"usage.{name}", problems) for name, key in _USAGE_TOKENS}
    models: dict[str, dict[str, _Figure]] = {}
    models_valid = True
    previous = None
    for index, event in enumerate(events):
        turns.add(event.get("num_turns", _ABSENT), index)
        duration.add(event.get("duration_ms", _ABSENT), index)
        api.add(event.get("duration_api_ms", _ABSENT), index)
        cost.add(event.get("total_cost_usd", _ABSENT), index)
        usage = event.get("usage", _ABSENT)
        if not isinstance(usage, dict):
            problems.append({"kind": PROBLEM_MISSING_FIELD if usage is _ABSENT else PROBLEM_INVALID_FIELD,
                             "field": "usage", "result_index": index})
            for figure in tokens.values():
                figure.valid = False
        else:
            for name, key in _USAGE_TOKENS:
                tokens[key].add(usage.get(name, _ABSENT), index)
        model_usage = event.get("modelUsage", _ABSENT)
        if not isinstance(model_usage, dict):
            problems.append({"kind": PROBLEM_MISSING_FIELD if model_usage is _ABSENT else PROBLEM_INVALID_FIELD,
                             "field": "modelUsage", "result_index": index})
            models_valid = False
        else:
            for model, fields in model_usage.items():
                figures = models.setdefault(str(model), {
                    key: _Figure(max, f"modelUsage.{model}.{name}", problems) for name, key in _MODEL_FIELDS})
                if not isinstance(fields, dict):
                    problems.append({"kind": PROBLEM_INVALID_FIELD, "field": f"modelUsage.{model}",
                                     "result_index": index})
                    for figure in figures.values():
                        figure.valid = False
                    continue
                for name, key in _MODEL_FIELDS:
                    figures[key].add(fields.get(name, _ABSENT), index)
        projection = _cumulative_projection(event)
        if previous is not None and _usage_nonzero(event) and projection == previous:
            problems.append({"kind": PROBLEM_CUMULATIVE_NOT_ADVANCED, "result_index": index})
        previous = projection

    block.update(
        turns=turns.final(),
        duration_ms=duration.final(),
        duration_api_ms=api.final(),
        cost_usd=cost.final(),
        tokens={key: figure.final() for key, figure in tokens.items()},
        models={model: {key: figure.final() for key, figure in figures.items()}
                for model, figures in sorted(models.items())} if models_valid else None,
        problems=problems,
    )
    return block
