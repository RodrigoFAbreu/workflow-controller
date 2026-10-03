"""The observation surface (``workflow-controller-release-runtime-observability``
CP6): read-only followers of the durable logs CP4 and CP5 write.

Everything here only reads: the run and job records, the lifecycle event
logs (``runs/<run_id>/events.jsonl``, ``jobs/<job_id>/events.jsonl``) and
the worker's own stream files (``jobs/<job_id>/worker.std{out,err}``).
Nothing here takes a lock, sends a signal or writes a file, and no
lifecycle module imports this one -- a follower's presence is recorded
nowhere, so a followed run and an unfollowed one leave the same durable
state.

- :class:`Tail` reads a growing file by offset and yields complete lines;
- :func:`normalise` maps a raw line to presentation events, and
  :func:`render_text`/:func:`render_json` print them. Thinking blocks are
  never rendered, not even as a marker;
- :func:`follow_run`/:func:`follow_job` multiplex a run's or a job's logs
  into a ``sink`` until the run or job ends;
- :func:`select_active`, :func:`active_runs` and :func:`active_jobs` are
  the discovery reads the ``follow`` command and ``status`` use;
- :func:`job_activity` (worker-lifecycle-ownership CP7) is the one presenter
  of a job carrying ``worker_state`` -- active, waiting (and on what),
  draining, unsupervised, pending reconciliation -- behind ``status``,
  ``inspect``, ``explain`` and ``follow``'s heartbeat, and
  :func:`stream_diagnosis`/:func:`diagnosis_lines` present an ended
  worker's harness-lifecycle diagnosis for ``explain``;
- :class:`FdSink` is ``step --follow``/``run --follow``'s renderer output:
  a private descriptor written with ``os.write``, which can never block
  the Controller's own stderr.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import select
import stat
import sys
import termios
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from controller import job, telemetry, worker

#: How often a follower polls its files.
POLL_SECONDS = 0.2

#: Silence, while a job is ``LAUNCHED``, before a heartbeat line is printed
#: (and between two heartbeats). Tests override it. The built-in default of
#: the ``follow.heartbeat_seconds`` setting, which :func:`follow_run` and
#: :func:`follow_job` take as ``heartbeat_seconds`` (settings-and-telemetry
#: CP2).
HEARTBEAT_SECONDS = 30.0

#: How many presentation events ``follow`` replays before going live
#: without ``--from-start``. The built-in default of the
#: ``follow.replay_events`` setting (``replay_events``).
REPLAY_EVENTS = 20

#: How long a follower keeps draining after the record it follows reads
#: ended, waiting for that record's final log line. The record is written
#: before its event is appended, and a best-effort append may never land.
FINAL_EVENT_GRACE_SECONDS = 2.0

TOOL_SUMMARY_LIMIT = 200
TOOL_RESULT_LINES = 20
WRAP_WIDTH = 100

#: The sources a presentation event carries.
SOURCE_RUN = "run"
SOURCE_JOB = "job"
SOURCE_WORKER = "worker"
SOURCE_STDERR = "stderr"
SOURCE_FOLLOW = "follow"

_FILE_PATH_TOOLS = frozenset({"Read", "Edit", "Write", "NotebookEdit"})
_PATTERN_TOOLS = frozenset({"Grep", "Glob"})
_THINKING_BLOCKS = frozenset({"thinking", "redacted_thinking"})


# ---------------------------------------------------------------------------
# Tail
# ---------------------------------------------------------------------------


class Tail:
    """An offset-based reader of one growing file. :meth:`read_lines`
    returns the complete lines appended since the last call and holds back
    a trailing partial line until its newline arrives. A missing file
    reads as nothing until it appears. The file is only ever opened for
    reading."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.offset = 0
        self._partial = b""

    def read_lines(self) -> list[str]:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            return []
        if not data:
            return []
        self.offset += len(data)
        *complete, self._partial = (self._partial + data).split(b"\n")
        return [line.decode("utf-8", "replace") for line in complete]

    def flush(self) -> list[str]:
        """The held-back partial line, as a line of its own -- for the final
        drain, once nothing more will be appended (a legacy
        ``worker.stdout`` is one JSON object with no trailing newline)."""
        partial, self._partial = self._partial, b""
        return [partial.decode("utf-8", "replace")] if partial.strip() else []

    def __iter__(self):
        """Poll every :data:`POLL_SECONDS`, forever, yielding complete
        lines."""
        while True:
            yield from self.read_lines()
            time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------------
# Normalising and rendering
# ---------------------------------------------------------------------------


def _parse_object(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _truncate(text: str, limit: int = TOOL_SUMMARY_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _tool_summary(name: Any, tool_input: Any) -> str:
    """``command`` for Bash, ``file_path`` for Read/Edit/Write, ``pattern``
    for Grep/Glob, otherwise compact JSON -- truncated to
    :data:`TOOL_SUMMARY_LIMIT` characters."""
    if isinstance(tool_input, Mapping):
        key = ("command" if name == "Bash" else "file_path" if name in _FILE_PATH_TOOLS
               else "pattern" if name in _PATTERN_TOOLS else None)
        if key is not None and isinstance(tool_input.get(key), str):
            return _truncate(tool_input[key])
    try:
        text = json.dumps(tool_input, separators=(",", ":"), sort_keys=True, default=str)
    except (TypeError, ValueError, RecursionError):
        text = repr(tool_input)
    return _truncate(text)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") if isinstance(block, Mapping) and block.get("type") == "text"
            else f"[{block.get('type')}]" if isinstance(block, Mapping) else str(block)
            for block in content
        )
    return "" if content is None else str(content)


def _worker_result(obj: Mapping) -> dict:
    return {
        "kind": "worker_result", "subtype": obj.get("subtype"), "is_error": obj.get("is_error"),
        "num_turns": obj.get("num_turns"), "total_cost_usd": obj.get("total_cost_usd"),
        "duration_ms": obj.get("duration_ms"),
    }


#: The harness's background-task events (worker-lifecycle-ownership CP7),
#: rendered as compact ``background task`` lines.
_TASK_SUBTYPES = frozenset({"task_started", "task_updated", "task_notification", "background_tasks_changed"})


def _background_task(obj: Mapping) -> dict:
    subtype = obj.get("subtype")
    if subtype == "background_tasks_changed":
        tasks = obj.get("tasks") if isinstance(obj.get("tasks"), list) else []
        return {"kind": "background_task", "event": "listed",
                "task_ids": [t.get("task_id") for t in tasks if isinstance(t, Mapping)]}
    patch = obj.get("patch") if isinstance(obj.get("patch"), Mapping) else {}
    return {"kind": "background_task", "event": subtype.removeprefix("task_"), "task_id": obj.get("task_id"),
            "status": patch.get("status") if subtype == "task_updated" else obj.get("status"),
            "description": obj.get("description") if subtype == "task_started" else None}


def _content_blocks(obj: Mapping) -> list:
    message = obj.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def _normalise_worker(obj: Mapping, tools: dict) -> list[dict]:
    kind = obj.get("type")
    if kind == "system" and obj.get("subtype") == "init":
        return [{"kind": "worker_init", "session_id": obj.get("session_id"), "model": obj.get("model"),
                 "permission_mode": obj.get("permissionMode", obj.get("permission_mode"))}]
    if kind == "system" and obj.get("subtype") in _TASK_SUBTYPES:
        return [_background_task(obj)]
    if kind == "command_lifecycle":
        return [{"kind": "harness_command", "command_uuid": obj.get("command_uuid"), "state": obj.get("state")}]
    if kind == "result":
        return [_worker_result(obj)]
    if kind is None and "session_id" in obj and "is_error" in obj:
        return [_worker_result(obj)]  # a legacy single-object `worker.stdout`
    if kind == "assistant":
        events = []
        for block in _content_blocks(obj):
            block_type = block.get("type") if isinstance(block, Mapping) else None
            if block_type in _THINKING_BLOCKS:
                continue  # never rendered, not even as a marker
            if block_type == "text":
                events.append({"kind": "text", "text": str(block.get("text", ""))})
            elif block_type == "tool_use":
                name = block.get("name")
                if isinstance(block.get("id"), str):
                    tools[block["id"]] = name
                events.append({"kind": "tool_use", "name": name,
                               "summary": _tool_summary(name, block.get("input"))})
            else:
                events.append({"kind": "unknown", "type": f"assistant/{block_type}"})
        return events
    if kind == "user":
        events = []
        for block in _content_blocks(obj):
            block_type = block.get("type") if isinstance(block, Mapping) else None
            if block_type != "tool_result":
                events.append({"kind": "unknown", "type": f"user/{block_type}"})
                continue
            lines = _result_text(block.get("content")).splitlines()
            events.append({
                "kind": "tool_result", "name": tools.get(block.get("tool_use_id")),
                "is_error": bool(block.get("is_error")), "lines": lines[:TOOL_RESULT_LINES],
                "more": max(0, len(lines) - TOOL_RESULT_LINES),
            })
        return events
    return [{"kind": "unknown", "type": kind if isinstance(kind, str) else None}]


def normalise(source: str, line: str, tools: dict | None = None) -> list[dict]:
    """``line`` from ``source`` (``run``/``job`` event logs, the ``worker``
    stream, the worker's ``stderr``) as presentation events: none for a
    thinking block, several for a message with several content blocks.
    ``tools`` maps ``tool_use`` ids to tool names across calls, so a
    ``tool_result`` names its tool. Never raises: a line that is not JSON
    is ``{"kind": "raw"}``, and an unknown one ``{"kind": "unknown",
    "type": ...}``."""
    tools = {} if tools is None else tools
    try:
        if source == SOURCE_STDERR:
            events = [{"kind": "stderr", "line": line}]
        else:
            obj = _parse_object(line)
            if obj is None:
                events = [{"kind": "raw", "line": line}]
            elif source in (SOURCE_RUN, SOURCE_JOB):
                details = {k: v for k, v in obj.items() if k not in ("v", "seq", "at", "event")}
                events = [{"kind": f"{source}_event", "event": obj.get("event"), "seq": obj.get("seq"),
                           "at": obj.get("at"), **details}]
            else:
                events = _normalise_worker(obj, tools)
    except Exception:  # noqa: BLE001 -- normalising must never stop a follower
        events = [{"kind": "raw", "line": line}]
    return [{"source": source, **event} for event in events]


def notice(message: str) -> dict:
    """A follower's own line (a heartbeat, an ending, a warning)."""
    return {"source": SOURCE_FOLLOW, "kind": "notice", "message": message}


def _parse_at(at: Any) -> datetime.datetime | None:
    """A recorded UTC time, as an aware UTC datetime: whole seconds
    (``%Y-%m-%dT%H:%M:%SZ``, the job records' own times) or with a
    fraction of up to six digits (``worker_stream._iso``'s millisecond
    ``due_at``). Anything else is ``None``."""
    if not isinstance(at, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ%z", "%Y-%m-%dT%H:%M:%S.%fZ%z"):
        try:
            return datetime.datetime.strptime(f"{at}+0000", fmt)
        except ValueError:
            continue
    return None


def _clock(at: Any) -> str:
    """``at`` (a recorded UTC time) in local time, or now."""
    moment = _parse_at(at)
    return (moment.astimezone() if moment else datetime.datetime.now()).strftime("%H:%M:%S")


def _transition(event: Mapping) -> str:
    verified = "verified" if event.get("transition_verified") else "not verified"
    return f"{event.get('observed_phase')} -> {event.get('observed_phase_after')} {verified}"


def _job_text(event: Mapping) -> str:
    name, job_id = event.get("event"), event.get("job_id")
    if name == "planned":
        return f"job {job_id} PLANNED ({event.get('command')})"
    if name == "launched":
        return (f"job {job_id} LAUNCHED ({event.get('command')}, {event.get('role')}, "
                f"{event.get('model')}/{event.get('effort')})")
    if name == "worker_spawned":
        return f"job {job_id} worker spawned: pid {event.get('pid')}, process group {event.get('pgid')}"
    if name == "worker_exited":
        pids = event.get("remaining_pids") or []
        return (f"job {job_id} worker pid {event.get('pid')} exited; waiting on process group: "
                f"{len(pids)} process(es) ({' '.join(str(p) for p in pids)})")
    if name == "completed":
        text = f"job {job_id} COMPLETED (worker {event.get('outcome')}, exit {event.get('exit_code')})"
        # Settings-and-telemetry CP3: the session totals, when the event
        # carries them (an event written before CP3 does not).
        if "telemetry" in event:
            text += f"; session: {telemetry.summary_text(event.get('telemetry'))}"
        return text
    if name in ("finished", "failed", "incomplete"):
        return f"job {job_id} {name.upper()} ({_transition(event)})"
    if name in ("gate_blocked", "declined", "handoff_pending"):
        required = event.get("what_is_required")
        return (f"gate: job {job_id} {name.upper()} -- {event.get('reason')}"
                + (f"; required: {required}" if required else ""))
    if name == "worker_not_started":
        return f"job {job_id} FAILED: worker not started ({event.get('error')})"
    if name == "reconciled":
        return f"job {job_id} reconciled: {event.get('status')} ({event.get('code')})"
    if name == "abandoned":
        return f"job {job_id} ABANDONED"
    if name in _WORKER_STATE_EVENTS:
        return _worker_state_text(event)
    if name == "worker_drain_detached":
        remaining = [entry for entry in event.get("remaining") or [] if isinstance(entry, Mapping)]
        return (f"job {job_id} worker drain detached: {len(remaining)} owned process(es) still running "
                f"({', '.join(str(entry.get('pid')) for entry in remaining)}) -- end them, then "
                f"workflow-controller resume {event.get('target_repo') or '<repo>'}")
    return f"job {job_id} {name}"


#: The ``worker_<state>`` job events (worker-lifecycle-ownership CP4).
_WORKER_STATE_EVENTS = frozenset(f"worker_{state.lower()}" for state in worker.WORKER_STATES)


def _worker_state_text(event: Mapping) -> str:
    state = str(event.get("event")).removeprefix("worker_").upper()
    head = f"job {event.get('job_id')} worker {state}" + ("" if event.get("state_changed", True) else " (update)")
    parts = []
    if isinstance(event.get("turns"), int):
        parts.append(f"turns {event['turns']}")
    tasks = [str(task) for task in event.get("tasks") or []]
    if tasks:
        parts.append(f"tasks {' '.join(tasks)}")
    wakeups = [w for w in event.get("wakeups") or [] if isinstance(w, Mapping)]
    if wakeups:
        parts.append("wakeups " + " ".join(f"{w.get('tool_use_id')}={w.get('state')}" for w in wakeups))
    brackets = [_uuid8(uuid) for uuid in event.get("command_lifecycles") or []]
    if brackets:
        parts.append(f"harness commands {' '.join(brackets)}")
    if event.get("owned_processes"):
        parts.append(f"owned processes {event['owned_processes']}")
    return head + (f": {', '.join(parts)}" if parts else "")


def _run_text(event: Mapping) -> str:
    name = event.get("event")
    if name == "run_started":
        return f"run {event.get('run_id')} started"
    if name == "step_started":
        return f"step {event.get('n')}"
    if name == "job_started":
        return f"job {event.get('job_id')} started"
    if name == "job_ended":
        return f"job {event.get('job_id')} ended: {event.get('status')}"
    if name == "handoff_detected":
        return "generation handoff detected"
    if name == "no_action":
        return f"no action at {event.get('observed_phase')}: {event.get('reason')}"
    if name == "waiting":
        return f"waiting at {event.get('gate')} until {event.get('deadline')}"
    if name == "run_ended":
        return f"run ended: exit {event.get('exit_code')}"
    if name == "run_interrupted":
        return "run interrupted"
    return f"run {name}"


def _body(event: Mapping) -> str:
    kind = event.get("kind")
    if kind == "worker_init":
        return (f"worker session {event.get('session_id')} model {event.get('model')} "
                f"permission {event.get('permission_mode')}")
    if kind == "text":
        return "\n".join(
            "\n".join(textwrap.wrap(line, WRAP_WIDTH, replace_whitespace=False, drop_whitespace=False) or [""])
            for line in str(event.get("text", "")).splitlines()
        )
    if kind == "tool_use":
        return f"tool {event.get('name')}: {event.get('summary')}"
    if kind == "tool_result":
        lines = list(event.get("lines") or [])
        head = f"result {event.get('name')} {'error' if event.get('is_error') else 'ok'}:"
        body = "\n".join([head + (f" {lines[0]}" if lines else ""), *lines[1:]])
        more = event.get("more") or 0
        return body + (f"\n... ({more} more lines)" if more else "")
    if kind == "worker_result":
        return (f"worker result: {event.get('subtype')}, is_error={event.get('is_error')}, "
                f"turns={event.get('num_turns')}, cost={event.get('total_cost_usd')}, "
                f"duration={event.get('duration_ms')}ms")
    if kind == "background_task":
        if event.get("event") == "listed":
            ids = [str(task_id) for task_id in event.get("task_ids") or []]
            return f"background tasks: {len(ids)} listed" + (f" ({' '.join(ids)})" if ids else "")
        if event.get("event") == "started":
            return f"background task {event.get('task_id')} started: {event.get('description')}"
        status = event.get("status")
        return f"background task {event.get('task_id')} {event.get('event')}" + (f": {status}" if status else "")
    if kind == "harness_command":
        return f"harness command {_uuid8(event.get('command_uuid'))} {event.get('state')}"
    if kind == "stderr":
        return f"stderr: {event.get('line')}"
    if kind == "job_event":
        return _job_text(event)
    if kind == "run_event":
        return _run_text(event)
    if kind == "notice":
        return str(event.get("message"))
    if kind == "raw":
        return str(event.get("line"))
    return f"unknown event {event.get('type')}"


def render_text(event: Mapping) -> str:
    """One line, or a short block whose continuation lines are indented,
    prefixed with local time and a source column."""
    prefix = f"{_clock(event.get('at'))} {str(event.get('source', '')):<6} "
    first, *rest = _body(event).split("\n")
    return "\n".join([prefix + first, *(" " * len(prefix) + line for line in rest)])


def render_json(event: Mapping) -> str:
    """One normalised object, on one line."""
    return json.dumps(dict(event), sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Record reads and liveness
# ---------------------------------------------------------------------------


def _read_record(path: Path) -> dict | None:
    """A run or job record, or ``None`` when it is missing or unreadable
    (a follower never fails on a record; the lifecycle owns them)."""
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def run_record_path(runtime_root: Path, run_id: str) -> Path:
    return Path(runtime_root) / "runs" / f"{run_id}.json"


def job_record_path(runtime_root: Path, job_id: str) -> Path:
    return Path(runtime_root) / "jobs" / f"{job_id}.json"


def read_run(runtime_root: Path, run_id: str) -> dict | None:
    return _read_record(run_record_path(runtime_root, run_id))


def read_job(runtime_root: Path, job_id: str) -> dict | None:
    return _read_record(job_record_path(runtime_root, job_id))


def _records(directory: Path) -> list[dict]:
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    return [record for record in (_read_record(path) for path in paths) if record is not None]


def list_runs(runtime_root: Path) -> list[dict]:
    """Every readable run record, oldest first."""
    runs = [r for r in _records(Path(runtime_root) / "runs") if isinstance(r.get("run_id"), str)]
    return sorted(runs, key=lambda r: (str(r.get("started_at")), r["run_id"]))


def list_jobs(runtime_root: Path) -> list[dict]:
    """Every readable job record, oldest first."""
    jobs = [r for r in _records(Path(runtime_root) / "jobs") if isinstance(r.get("job_id"), str)]
    return sorted(jobs, key=lambda r: (str(r.get("created_at")), r["job_id"]))


def controller_liveness(process: Any) -> str:
    """``active``, ``inactive`` or ``unverifiable`` for a run record's
    ``controller_process`` -- a **pid-level** check, never
    ``worker.assess_worker_liveness``'s process-group verdict: the
    Controller's own group can hold unrelated pipeline members (``| tee``)
    that outlive it. The recorded pid must exist with the recorded
    ``start_ticks``, in the same boot and pid namespace, and must not be a
    zombie. It reads through the same ``/proc`` readers the worker verdict
    uses."""
    if not isinstance(process, Mapping):
        return worker.UNVERIFIABLE
    pid, start_ticks = process.get("pid"), process.get("start_ticks")
    if not (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0):
        return worker.UNVERIFIABLE
    current = worker.read_process_context()
    same_host = (process.get("hostname") is not None and process.get("hostname") == current["hostname"]
                 and process.get("machine_id") is not None and process.get("machine_id") == current["machine_id"])
    recorded_boot, current_boot = process.get("boot_id"), current["boot_id"]
    if recorded_boot is not None and current_boot is not None and recorded_boot != current_boot:
        return worker.INACTIVE if same_host else worker.UNVERIFIABLE
    if recorded_boot is None or current_boot is None:
        return worker.UNVERIFIABLE
    if process.get("pid_namespace") is None or process.get("pid_namespace") != current["pid_namespace"]:
        return worker.UNVERIFIABLE
    root = worker._proc_root()
    if not worker._proc_numbers_own_namespace(root):
        return worker.UNVERIFIABLE
    try:
        proc_stat = worker._read_stat(root / str(pid) / "stat")
        if proc_stat is None:
            return worker.INACTIVE
        if isinstance(start_ticks, int) and proc_stat.start_ticks != start_ticks:
            return worker.INACTIVE  # the pid now names another process
        running = worker._is_running(root, pid, proc_stat)
    except worker._NoAnswer:
        return worker.UNVERIFIABLE
    if not running:
        return worker.INACTIVE
    return worker.ACTIVE if isinstance(start_ticks, int) else worker.UNVERIFIABLE


def worker_liveness(record: Mapping) -> str | None:
    """A job record's recorded worker's verdict (read-only), or ``None``
    when the record carries no ``worker_process``."""
    process = record.get("worker_process")
    if process is None:
        return None
    return worker.assess_worker_liveness(process if isinstance(process, Mapping) else {}).verdict


def _is_terminal(record: Mapping) -> bool:
    return record.get("status") in job.TERMINAL_STATUSES


def _controller_owns_job(runtime_root: Path, record: Mapping) -> bool:
    """Whether the job's own ``step``/``run`` is still running with a
    Controller that is not known to be gone -- that Controller, not the
    worker's exit, ends the job."""
    run_id = record.get("run_id")
    run = read_run(runtime_root, run_id) if isinstance(run_id, str) else None
    return (run is not None and run.get("state") == job.RUN_STATE_RUNNING
            and controller_liveness(run.get("controller_process")) != worker.INACTIVE)


# ---------------------------------------------------------------------------
# Following
# ---------------------------------------------------------------------------


def _minutes(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _since(at: Any) -> float | None:
    moment = _parse_at(at)
    return None if moment is None else (datetime.datetime.now(datetime.timezone.utc) - moment).total_seconds()


class _Follower:
    """The multiplexer behind :func:`follow_run` and :func:`follow_job`:
    the tails, the ``tool_use`` names, the replay, the heartbeat and the
    one-time warnings."""

    def __init__(self, runtime_root: Path, sink: Callable[[str], Any], *, json_output: bool,
                 stop: Any, heartbeat_seconds: float | None = None, replay_events: int | None = None) -> None:
        self.runtime_root = Path(runtime_root)
        self.heartbeat_seconds = HEARTBEAT_SECONDS if heartbeat_seconds is None else heartbeat_seconds
        self.replay_events = REPLAY_EVENTS if replay_events is None else replay_events
        self.sink = sink
        self.render = render_json if json_output else render_text
        self.stop = stop
        self.tails: list[tuple[str, str | None, Tail]] = []
        self.job_ids: list[str] = []
        self.tools: dict = {}
        self.warned: set[str] = set()
        self.last_event = time.monotonic()
        self.last_heartbeat = 0.0
        self.max_job_seq: dict[str, int] = {}
        self.run_events_seen: set[str] = set()

    # -- sources ----------------------------------------------------------

    def add_run(self, run_id: str) -> None:
        self.tails.append((SOURCE_RUN, None, Tail(self.runtime_root / "runs" / run_id / "events.jsonl")))

    def add_job(self, job_id: Any) -> None:
        if not isinstance(job_id, str) or job_id in self.job_ids:
            return
        self.job_ids.append(job_id)
        base = self.runtime_root / "jobs" / job_id
        self.tails.extend([
            (SOURCE_JOB, job_id, Tail(base / "events.jsonl")),
            (SOURCE_WORKER, job_id, Tail(base / "worker.stdout")),
            (SOURCE_STDERR, job_id, Tail(base / "worker.stderr")),
        ])

    def stopped(self) -> bool:
        return (self.stop is not None and self.stop.is_set()) or bool(getattr(self.sink, "disabled", False))

    # -- reading ----------------------------------------------------------

    def _events(self, source: str, job_id: str | None, lines: list[str]) -> list[dict]:
        events = []
        for line in lines:
            if not line.strip():
                continue
            for event in normalise(source, line, self.tools):
                if job_id is not None and "job_id" not in event:
                    event = {**event, "job_id": job_id}
                events.append(self._enrich(event))
        return events

    def _enrich(self, event: dict) -> dict:
        """Job events carry only what changed; ``launched`` and the final
        events are rendered with the command, route and starting phase the
        job record holds. Run events announce new jobs to follow."""
        if event.get("kind") == "run_event":
            self.run_events_seen.add(str(event.get("event")))
            if event.get("event") == "job_started":
                self.add_job(event.get("job_id"))
        if event.get("kind") != "job_event":
            return event
        job_id = event.get("job_id")
        if isinstance(event.get("seq"), int) and isinstance(job_id, str):
            self.max_job_seq[job_id] = max(self.max_job_seq.get(job_id, 0), event["seq"])
        if event.get("event") not in ("launched", "finished", "failed", "incomplete", "worker_drain_detached"):
            return event
        record = read_job(self.runtime_root, job_id) if isinstance(job_id, str) else None
        if record is None:
            return event
        if event.get("event") == "worker_drain_detached":
            return {"target_repo": record.get("target_repo"), **event}
        action = record.get("selected_action") if isinstance(record.get("selected_action"), Mapping) else {}
        route = record.get("worker_route") if isinstance(record.get("worker_route"), Mapping) else {}
        return {"command": action.get("command"), "role": route.get("role"), "model": route.get("model"),
                "effort": route.get("effort"), "observed_phase": record.get("observed_phase_before"), **event}

    def _read(self, source: str, job_id: str | None, tail: Tail, final: bool) -> list[dict]:
        return self._events(source, job_id, tail.read_lines() + (tail.flush() if final else []))

    def _read_jobs(self, job_id: Any, final: bool) -> list[dict]:
        """``job_id``'s logs (every job's, for ``None``) read now."""
        return [event for source, tail_job, tail in list(self.tails)
                if source != SOURCE_RUN and (job_id is None or tail_job == job_id)
                for event in self._read(source, tail_job, tail, final)]

    def poll(self, *, final: bool = False) -> list[dict]:
        """Every complete line appended since the last poll. A run event
        that ends a job (or the run) is preceded by what that job's (or
        every job's) logs hold by then, so a replay reads in order; a new
        job announced by a run event is read in the same poll. ``final``
        also returns each file's held-back partial line."""
        events: list[dict] = []
        for source, job_id, tail in list(self.tails):
            if source != SOURCE_RUN:
                continue
            for event in self._read(source, job_id, tail, final):
                name = event.get("event")
                if name == "job_ended":
                    events.extend(self._read_jobs(event.get("job_id"), final))
                elif name in ("run_ended", "run_interrupted"):
                    events.extend(self._read_jobs(None, final))
                events.append(event)
        events.extend(self._read_jobs(None, final))
        return events

    # -- output -----------------------------------------------------------

    def emit(self, events: list[dict]) -> None:
        for event in events:
            self.sink(self.render(event))
        if events:
            self.last_event = time.monotonic()

    def replay(self, events: list[dict], *, from_start: bool) -> None:
        if from_start:
            self.emit(events)
        elif self.replay_events > 0:
            self.emit(events[-self.replay_events:])

    def say(self, message: str) -> None:
        self.sink(self.render(notice(message)))

    def warn_once(self, key: str, message: str) -> None:
        if key not in self.warned:
            self.warned.add(key)
            self.say(message)

    def heartbeat(self, record: Mapping | None) -> None:
        """While ``record``'s job is ``LAUNCHED`` and nothing new arrived for
        ``heartbeat_seconds``: what the worker is doing. A record
        carrying ``worker_state`` (worker-lifecycle-ownership CP7) is
        described by :func:`job_activity` while it is not terminal --
        ``WAITING`` included."""
        if record is None:
            return
        tracked = "worker_state" in record and not _is_terminal(record)
        if record.get("status") != job.STATUS_LAUNCHED and not tracked:
            return
        now = time.monotonic()
        if now - self.last_event < self.heartbeat_seconds or now - self.last_heartbeat < self.heartbeat_seconds:
            return
        self.last_heartbeat = now
        if tracked:
            activity = job_activity(record, self.runtime_root, last_event_seconds=now - self.last_event)
            self.say(activity["text"])
            return
        drain = record.get("worker_group_drain")
        if isinstance(drain, Mapping):
            pids = [str(pid) for pid in drain.get("remaining_pids") or []]
            elapsed = _since(drain.get("direct_child_exited_at"))
            self.say(f"worker exited; waiting on process group: {len(pids)} process(es) at drain start "
                     f"({' '.join(pids)}), elapsed {_minutes(elapsed or 0)}")
            return
        process = record.get("worker_process") if isinstance(record.get("worker_process"), Mapping) else {}
        elapsed = _since(record.get("created_at"))
        self.say(f"worker running: pid {process.get('pid')}, elapsed {_minutes(elapsed or 0)}, "
                 f"last event {int(now - self.last_event)}s ago")

    def drain(self, until: Callable[[], bool]) -> None:
        """Read to EOF, and keep reading for up to
        :data:`FINAL_EVENT_GRACE_SECONDS` until ``until()`` holds (the
        final event has been seen); then flush the partial lines."""
        deadline = time.monotonic() + FINAL_EVENT_GRACE_SECONDS
        self.emit(self.poll())
        while not until() and time.monotonic() < deadline and not getattr(self.sink, "disabled", False):
            time.sleep(POLL_SECONDS / 4)
            self.emit(self.poll())
        self.emit(self.poll(final=True))


def follow_run(runtime_root: Path, run_id: str, sink: Callable[[str], Any], *, from_start: bool = False,
               stop: Any = None, json_output: bool = False, heartbeat_seconds: float | None = None,
               replay_events: int | None = None) -> None:
    """Follow run ``run_id`` into ``sink`` (called with each rendered line):
    the run's events, and each of its jobs' events, ``worker.stdout`` and
    ``worker.stderr``.

    Ends when the run record reads ``ended`` and every log is drained; when
    the recorded Controller process is gone while the record still reads
    ``running``; or, for an ``interrupted`` run, at once -- unless its
    current job's worker is still ``active``, which is then followed as
    :func:`follow_job` would. ``stop`` (a ``threading.Event``) ends it
    after one final drain. Without ``from_start`` only the last
    ``replay_events`` (``None``: :data:`REPLAY_EVENTS`) events already
    written are shown; ``heartbeat_seconds`` (``None``:
    :data:`HEARTBEAT_SECONDS`) is the heartbeat's silence."""
    follower = _Follower(runtime_root, sink, json_output=json_output, stop=stop,
                         heartbeat_seconds=heartbeat_seconds, replay_events=replay_events)
    follower.add_run(run_id)
    record = read_run(runtime_root, run_id) or {}
    for job_id in record.get("job_ids") or []:
        follower.add_job(job_id)
    follower.replay(follower.poll(), from_start=from_start)
    while True:
        record = read_run(runtime_root, run_id) or record
        for job_id in record.get("job_ids") or []:
            follower.add_job(job_id)
        if follower.stopped():
            follower.drain(lambda: True)
            return
        follower.emit(follower.poll())
        state = record.get("state")
        if state == job.RUN_STATE_ENDED:
            follower.drain(lambda: "run_ended" in follower.run_events_seen)
            return
        if state == job.RUN_STATE_INTERRUPTED:
            follower.drain(lambda: "run_interrupted" in follower.run_events_seen)
            current = record.get("current_job_id")
            job_record = read_job(runtime_root, current) if isinstance(current, str) else None
            if job_record is not None and _job_live(runtime_root, job_record):
                follower.say(f"run {run_id} was interrupted; worker for job {current} is still running; "
                             f"following the job")
                _follow_job_loop(follower, current)
                return
            follower.say(f"run {run_id} was interrupted")
            return
        liveness = controller_liveness(record.get("controller_process"))
        if liveness == worker.INACTIVE:
            follower.drain(lambda: True)
            follower.say("controller process is gone; run record was not closed")
            return
        if liveness == worker.UNVERIFIABLE:
            follower.warn_once("controller", f"the Controller of run {run_id} cannot be verified from here; "
                                             f"following until its run record ends")
        current = record.get("current_job_id")
        follower.heartbeat(read_job(runtime_root, current) if isinstance(current, str) else None)
        time.sleep(POLL_SECONDS)


def follow_job(runtime_root: Path, job_id: str, sink: Callable[[str], Any], *, from_start: bool = False,
               stop: Any = None, json_output: bool = False, heartbeat_seconds: float | None = None,
               replay_events: int | None = None) -> None:
    """Follow job ``job_id`` into ``sink``: its events, ``worker.stdout``
    and ``worker.stderr``. Ends, after draining every log, when the job
    record is terminal, or when it is not and its worker is no longer
    ``active`` (``worker exited; job <id> awaits resume``) -- unless the
    job's own ``step``/``run`` is still running, whose Controller then
    finishes it. An ``unverifiable`` worker keeps it following, with a
    one-time warning. A record carrying ``worker_state`` ends only at its
    terminal record, or once nothing but ``resume`` can advance it
    (:func:`_follow_tracked_job`). ``heartbeat_seconds`` and
    ``replay_events`` are :func:`follow_run`'s."""
    follower = _Follower(runtime_root, sink, json_output=json_output, stop=stop,
                         heartbeat_seconds=heartbeat_seconds, replay_events=replay_events)
    follower.add_job(job_id)
    follower.replay(follower.poll(), from_start=from_start)
    _follow_job_loop(follower, job_id)


def _follow_job_loop(follower: _Follower, job_id: str) -> None:
    runtime_root = follower.runtime_root
    follower.add_job(job_id)
    record: dict = {}
    while True:
        record = read_job(runtime_root, job_id) or record
        if follower.stopped():
            follower.drain(lambda: True)
            return
        follower.emit(follower.poll())
        if _is_terminal(record):
            seq = record.get("event_seq")
            follower.drain(lambda: not isinstance(seq, int) or follower.max_job_seq.get(job_id, 0) >= seq)
            return
        if "worker_state" in record and not _controller_owns_job(runtime_root, record):
            if _follow_tracked_job(follower, job_id, record):
                return
        elif not _controller_owns_job(runtime_root, record):
            verdict = worker_liveness(record)
            if verdict is None:
                follower.drain(lambda: True)
                follower.say(f"no worker is recorded for job {job_id}; job {job_id} awaits resume")
                return
            if verdict == worker.INACTIVE:
                follower.drain(lambda: True)
                follower.say(f"worker exited; job {job_id} awaits resume")
                return
            if verdict == worker.UNVERIFIABLE:
                follower.warn_once("worker", f"the worker of job {job_id} cannot be verified from here; "
                                             f"following until its job record ends")
        follower.heartbeat(record)
        time.sleep(POLL_SECONDS)


def _follow_tracked_job(follower: _Follower, job_id: str, record: Mapping) -> bool:
    """``follow``'s rule for a record carrying ``worker_state`` whose own
    ``step``/``run`` no longer owns it (worker-lifecycle-ownership CP7,
    plan G): the worker's exit no longer ends the follow -- a ``WAITING``
    or draining job keeps being followed, and a Controller that re-attaches
    (``resume``) carries it to its terminal record. With no Controller
    attached the follower says so once, naming ``resume``; once nothing
    the job owns is running either, nothing but ``resume`` can advance it,
    so the follow ends saying so. Returns whether it ended."""
    activity = job_activity(record, follower.runtime_root)
    if activity is None:
        return False
    if activity["activity"] == ACTIVITY_PENDING:
        follower.drain(lambda: True)
        follower.say(f"job {job_id}: {activity['text']}")
        return True
    if activity["activity"] == ACTIVITY_UNSUPERVISED:
        follower.warn_once("supervisor", f"no Controller is attached to job {job_id} -- "
                                         f"{activity['resume_command']} re-attaches; following")
    elif (activity.get("supervisor") or {}).get("state") == job.SUPERVISOR_UNKNOWN:
        follower.warn_once("supervisor", f"whether a Controller is attached to job {job_id} cannot be read "
                                         f"from /proc/locks; following until its job record ends")
    return False


def _job_live(runtime_root: Path, record: Mapping) -> bool:
    """Whether ``record``'s worker or owned work may still be running: the
    worker's verdict is ``active`` or, for a record carrying
    ``worker_state``, :func:`job_activity` reads one of
    :data:`LIVE_ACTIVITIES`."""
    activity = job_activity(record, runtime_root)
    if activity is not None:
        return activity["activity"] in LIVE_ACTIVITIES
    return worker_liveness(record) == worker.ACTIVE


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def active_runs(runtime_root: Path) -> list[tuple[dict, str]]:
    """Every ``running`` run record, with its Controller's liveness."""
    return [(run, controller_liveness(run.get("controller_process")))
            for run in list_runs(runtime_root) if run.get("state") == job.RUN_STATE_RUNNING]


def active_jobs(runtime_root: Path) -> list[tuple[dict, str | None]]:
    """Every non-terminal job record, with its worker's liveness (``None``
    when no worker is recorded)."""
    return [(record, worker_liveness(record)) for record in list_jobs(runtime_root) if not _is_terminal(record)]


def select_active(runtime_root: Path, target_repo: str) -> tuple[str, str] | None:
    """What a bare ``follow <repo>`` follows: ``("run", run_id)`` for the
    newest ``running`` run for ``target_repo`` whose Controller is not
    ``inactive``; otherwise ``("job", job_id)`` for a non-terminal job for
    it whose worker is ``active`` (an orphan after Ctrl-C); otherwise
    ``None``."""
    runs = [(run, liveness) for run, liveness in active_runs(runtime_root)
            if run.get("target_repo") == target_repo and liveness != worker.INACTIVE]
    if runs:
        return "run", runs[-1][0]["run_id"]
    for record, liveness in reversed(active_jobs(runtime_root)):
        if record.get("target_repo") != target_repo:
            continue
        if liveness == worker.ACTIVE or ("worker_state" in record and _job_live(runtime_root, record)):
            return "job", record["job_id"]
    return None


def last_run(runtime_root: Path, target_repo: str) -> dict | None:
    """The newest run record for ``target_repo``, whatever its state."""
    runs = [run for run in list_runs(runtime_root) if run.get("target_repo") == target_repo]
    return runs[-1] if runs else None


#: How many of the newest job records ``status`` lists (settings-and-
#: telemetry CP6, E.4).
STATUS_RECENT_JOBS = 10


def job_command(record: Mapping) -> str | None:
    """The job's selected command, ``None`` when it selected none."""
    action = record.get("selected_action")
    return action.get("command") if isinstance(action, Mapping) else None


def job_summary(record: Mapping, *, now: str) -> dict:
    """``status``'s view of one job record (settings-and-telemetry CP6,
    E.4): its id, status, command, work item and start time, and either
    its age at ``now`` (still active) or, once terminal, its wall time
    (the telemetry block's ``job_seconds``, else ``created_at`` to
    ``updated_at``) and cost (``None`` without telemetry figures)."""
    finished = _is_terminal(record)
    block = record.get("telemetry") if isinstance(record.get("telemetry"), Mapping) else None
    wall = block.get("job_seconds") if block is not None else None
    if finished and wall is None:
        wall = telemetry.seconds_between(record.get("created_at"), record.get("updated_at"))
    cost = None if block is None or telemetry.unavailable(block) else block.get("cost_usd")
    summary = {
        "job_id": record.get("job_id"),
        "status": record.get("status"),
        "command": job_command(record),
        "work_item_id": record.get("work_item_id"),
        "created_at": record.get("created_at"),
        "finished": finished,
        "age_seconds": None if finished else telemetry.seconds_between(record.get("created_at"), now),
        "wall_seconds": wall if finished else None,
        "cost_usd": cost if finished else None,
    }
    protocol_block = record.get("protocol")
    if isinstance(protocol_block, Mapping):
        # Orchestration-protocol-v1 F: a protocol job's reconcile class and
        # invalid reasons (``None``/empty until reconciled). Additive: a
        # legacy record's entry gains no key.
        reconcile = protocol_block.get("reconcile")
        reconcile = reconcile if isinstance(reconcile, Mapping) else {}
        reasons = reconcile.get("invalid_reasons")
        summary["reconcile_class"] = reconcile.get("class")
        summary["invalid_reasons"] = [
            {"code": reason.get("code"), "text": reason.get("text")}
            for reason in (reasons if isinstance(reasons, list) else []) if isinstance(reason, Mapping)]
    return summary


def recent_jobs(records: list[dict], *, now: str, limit: int = STATUS_RECENT_JOBS) -> list[dict]:
    """:func:`job_summary` of the ``limit`` newest of ``records`` (oldest
    first, as :func:`list_jobs` returns them), newest first."""
    return [job_summary(record, now=now) for record in reversed(records[-limit:] if limit else [])]


def _seconds_text(seconds: Any) -> str:
    return f"{seconds} s" if isinstance(seconds, int) and not isinstance(seconds, bool) else "unknown"


def age_text(seconds: Any) -> str:
    """A job's age, in the largest unit that keeps two digits readable:
    ``45 s``, ``12 min``, ``5 h``, ``3 d``; ``unknown`` without one."""
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        return "unknown"
    for unit, size, limit in (("s", 1, 120), ("min", 60, 120 * 60), ("h", 3600, 48 * 3600)):
        if seconds < limit:
            return f"{seconds // size} {unit}"
    return f"{seconds // 86400} d"


def job_summary_text(entry: Mapping) -> str:
    """One ``status`` line for a :func:`job_summary` entry:
    ``<id> <status> <command> (work item <id>): age 12 min`` while active,
    ``...: wall 1400 s, cost $11.66`` once finished."""
    head = (f"{entry['job_id']} {entry['status']} {entry['command'] or 'no command'} "
            f"(work item {entry['work_item_id'] or 'none'})")
    if entry.get("reconcile_class") is not None:
        codes = [str(reason["code"]) for reason in entry.get("invalid_reasons") or []]
        head += f" [reconcile {entry['reconcile_class']}" + (f": {', '.join(codes)}" if codes else "") + "]"
    if not entry["finished"]:
        return f"{head}: age {age_text(entry['age_seconds'])}"
    cost = "" if entry["cost_usd"] is None else f", {telemetry.money_text(entry['cost_usd'])}"
    return f"{head}: wall {_seconds_text(entry['wall_seconds'])}{cost}"


def drain_text(record: Mapping) -> str | None:
    """``worker exited; waiting on process group: ...`` for a ``LAUNCHED``
    record carrying ``worker_group_drain``, else ``None``."""
    drain = record.get("worker_group_drain")
    if record.get("status") != job.STATUS_LAUNCHED or not isinstance(drain, Mapping):
        return None
    pids = [str(pid) for pid in drain.get("remaining_pids") or []]
    return f"worker exited; waiting on process group: {len(pids)} process(es) at drain start ({' '.join(pids)})"


# ---------------------------------------------------------------------------
# The job activity presenter (worker-lifecycle-ownership CP7, plan G)
# ---------------------------------------------------------------------------

#: The activity labels of plan G's table.
ACTIVITY_ACTIVE = "active"
ACTIVITY_WAITING = "waiting"
ACTIVITY_DRAINING = "draining"
ACTIVITY_UNSUPERVISED = "unsupervised"
ACTIVITY_PENDING = "pending reconciliation"
ACTIVITY_TERMINAL = "terminal"

#: The activities whose worker or owned work may still be running: a
#: follower keeps following them, and a bare ``follow <repo>`` selects them.
LIVE_ACTIVITIES = frozenset({ACTIVITY_ACTIVE, ACTIVITY_WAITING, ACTIVITY_DRAINING, ACTIVITY_UNSUPERVISED})


def _plural(count: int, noun: str) -> str:
    """``count`` and ``noun``, pluralised: ``1 process``, ``2 processes``."""
    if count == 1:
        return f"{count} {noun}"
    return f"{count} {noun}es" if noun.endswith("s") else f"{count} {noun}s"


def _uuid8(value: Any) -> str:
    return str(value)[:8] if value is not None else "unknown"


def resume_command(record: Mapping, runtime_root: Path) -> str:
    """The command that re-attaches to, or reconciles, ``record``'s job,
    carrying ``--runtime-dir`` as :func:`job.follow_command` does."""
    return job._resume_command(Path(str(record.get("target_repo"))), runtime_root)


def _stream_age(record: Mapping, runtime_root: Path) -> float | None:
    """Seconds since the worker's stream was last written, from its
    ``stdout`` file's modification time (read-only), or ``None``."""
    streams = record.get("worker_streams") if isinstance(record.get("worker_streams"), Mapping) else {}
    path = streams.get("stdout_path") if isinstance(streams.get("stdout_path"), str) else None
    if path is None and isinstance(record.get("job_id"), str):
        path = str(Path(runtime_root) / "jobs" / record["job_id"] / "worker.stdout")
    try:
        return max(0.0, time.time() - os.stat(path).st_mtime) if path else None
    except OSError:
        return None


def _live_not_owned(worker_state: Mapping) -> list[dict]:
    """The recorded recognised daemons (never owned, plan C) still running."""
    excluded = worker_state.get("excluded_processes")
    return [dict(entry) for entry in (excluded if isinstance(excluded, list) else [])
            if isinstance(entry, Mapping)
            and worker.identity_alive(entry.get("pid"), entry.get("start_ticks")) is True]


def _task_clause(tasks: list) -> str:
    listed = ", ".join(f"{task.get('task_id')} \"{task.get('description') or 'no description'}\""
                       for task in tasks if isinstance(task, Mapping))
    return _plural(len(tasks), "background task") + (f" ({listed})" if listed else "")


def _due_text(due_at: Any) -> str:
    """A pending wakeup's recorded due time in local time. A recorded value
    that does not parse is named, never replaced by the current time."""
    if not due_at:
        return "due time unknown"
    return f"due {_clock(due_at)}" if _parse_at(due_at) else f"due time unreadable: {due_at!r}"


def _wakeup_clause(pending: list) -> str:
    dues = ", ".join(_due_text(w.get("due_at")) for w in pending if isinstance(w, Mapping))
    return _plural(len(pending), "wakeup") + (f" ({dues})" if dues else "")


def _bracket_clause(brackets: list, *, attached: bool, flushed_age: float) -> str:
    parts = []
    for bracket in brackets:
        if not isinstance(bracket, Mapping):
            continue
        seen = "turn ended, awaiting completion" if bracket.get("turn_seen") else "no turn yet"
        stalled = bracket.get("stalled_seconds")
        if not attached:
            stall = "stall time unknown (no Controller attached)"
        elif isinstance(stalled, (int, float)):
            stall = (f"stalled {_minutes(min(stalled + flushed_age, worker.COMMAND_LIFECYCLE_GRACE_SECONDS))} "
                     f"of {_minutes(worker.COMMAND_LIFECYCLE_GRACE_SECONDS)}")
        else:
            stall = "stall time unknown"
        parts.append(f"command_uuid {_uuid8(bracket.get('command_uuid'))}, {seen}; {stall}")
    return _plural(len(brackets), "harness command") + f" ({' | '.join(parts)})"


def _fire_matched_clause(matched: list, *, attached: bool, flushed_age: float) -> str:
    parts = []
    for wakeup in matched:
        if not isinstance(wakeup, Mapping):
            continue
        left = wakeup.get("settle_seconds_left")
        if not attached:
            when = "settle time unknown (no Controller attached; restarts on resume)"
        elif isinstance(left, (int, float)):
            when = f"settles in {_minutes(max(0.0, left - flushed_age))}"
        else:
            when = "settle time unknown"
        parts.append(f"matched by harness command {_uuid8(wakeup.get('command_uuid'))}; {when} unless another "
                     f"harness command contradicts it; ScheduleWakeup stop:true settles it now")
    noun = "wakeup presumed fired, not yet settled" if len(matched) == 1 else \
        "wakeups presumed fired, not yet settled"
    return f"{len(matched)} {noun} ({' | '.join(parts)})"


def _waiting_text(pid: Any, waiting_on: Mapping, *, attached: bool, flushed_age: float) -> str:
    tasks = [t for t in waiting_on.get("tasks") or [] if isinstance(t, Mapping)]
    wakeups = [w for w in waiting_on.get("wakeups") or [] if isinstance(w, Mapping)]
    pending = [w for w in wakeups if w.get("state") != "fire_matched"]
    matched = [w for w in wakeups if w.get("state") == "fire_matched"]
    brackets = [b for b in waiting_on.get("command_lifecycles") or [] if isinstance(b, Mapping)]
    text = f"worker pid {pid} waiting on {_task_clause(tasks)} and {_wakeup_clause(pending)}"
    if brackets:
        text += f" and {_bracket_clause(brackets, attached=attached, flushed_age=flushed_age)}"
    if matched:
        text += f" and {_fire_matched_clause(matched, attached=attached, flushed_age=flushed_age)}"
    return text


def _pids_text(entries: list) -> str:
    return ", ".join(str(entry.get("pid")) for entry in entries)


def job_activity(record: Mapping, runtime_root: Path, *, last_event_seconds: float | None = None) -> dict | None:
    """What a job carrying ``worker_state`` is doing (plan G), from the
    record, the read-only supervisor-lock probe and a fresh owned-work
    scan -- ``None`` for a record without ``worker_state``, whose
    presentation is unchanged. Read-only: it takes no lock, sends no signal
    and writes nothing.

    The dict carries ``activity`` (one of plan G's labels), ``text`` (the
    line ``status``, ``inspect``, ``explain`` and ``follow``'s heartbeat
    print), and the structured fields ``--json`` carries: ``worker_state``,
    ``waiting_on``, ``owned_processes`` (the fresh scan), ``not_owned``
    (recognised daemons still running), ``supervisor`` and
    ``resume_command``. A bracket's stall time and a ``fire_matched``
    wakeup's settle time are shown only with a Controller attached --
    otherwise they are unknown, never a number."""
    worker_state = record.get("worker_state")
    if "worker_state" not in record:
        return None
    worker_state = worker_state if isinstance(worker_state, Mapping) else {}
    state = worker_state.get("state")
    waiting_on = worker_state.get("waiting_on") if isinstance(worker_state.get("waiting_on"), Mapping) else {}
    resume = resume_command(record, runtime_root)
    status = record.get("status")
    base = {"worker_state": state, "waiting_on": dict(waiting_on), "resume_command": resume}
    if status in job.TERMINAL_STATUSES:
        return {**base, "activity": ACTIVITY_TERMINAL, "text": f"job {status}", "owned_processes": [],
                "not_owned": [], "supervisor": None}

    supervisor, holders = job.supervisor_probe(runtime_root, record)
    attached = supervisor == job.SUPERVISOR_ATTACHED
    entries, verifiable = job._scan_record_owned(record)
    not_owned = _live_not_owned(worker_state)
    verdict = worker_liveness(record)
    worker_live = verdict is not None and verdict != worker.INACTIVE
    process = record.get("worker_process") if isinstance(record.get("worker_process"), Mapping) else {}
    pid = process.get("pid")
    flushed_age = _since(record.get("updated_at")) or 0.0

    if status == job.STATUS_LAUNCHED and worker_live and state in (worker.STARTING, worker.RUNNING):
        activity = ACTIVITY_ACTIVE
        age = last_event_seconds if last_event_seconds is not None else _stream_age(record, runtime_root)
        text = (f"worker pid {pid} running (turn {worker_state.get('turns') or 0}), "
                f"elapsed {_minutes(_since(record.get('created_at')) or 0)}"
                + (f", last event {int(age)}s ago" if age is not None else ""))
        if entries:
            text += f"; owns {_plural(len(entries), 'process')} ({_pids_text(entries)})"
    elif status == job.STATUS_LAUNCHED and worker_live and state == worker.WAITING:
        activity = ACTIVITY_WAITING
        text = _waiting_text(pid, waiting_on, attached=attached, flushed_age=flushed_age)
        if entries:
            text += f"; owns {_plural(len(entries), 'process')} ({_pids_text(entries)})"
    elif worker_live or entries or not verifiable:
        activity = ACTIVITY_DRAINING
        if worker_live:
            text = f"worker pid {pid} ending ({state})"
        else:
            text = "worker ended"
        if entries:
            text += f"; {_plural(len(entries), 'owned process')} still running (pids {_pids_text(entries)})"
        elif not verifiable:
            text += "; its owned processes cannot be verified from here"
        if record.get("drain_detached_at"):
            # Settings-and-telemetry CP2: the bound the drain applied, as
            # recorded, never this invocation's setting.
            text += (f"; detached after {_minutes(job.applied_drain_bound(record))} -- end them, then {resume}")
    else:
        activity = ACTIVITY_PENDING
        outcome = record.get("worker_outcome") or "not classified"
        text = f"worker ended ({outcome}); pending reconciliation -- {resume}"
    if not_owned and activity != ACTIVITY_PENDING:
        text += "; not owned: " + ", ".join(
            f"pid {entry.get('pid')} ({entry.get('cmdline') or 'command line unreadable'})" for entry in not_owned)
    if activity in LIVE_ACTIVITIES and supervisor == job.SUPERVISOR_UNATTACHED:
        activity = ACTIVITY_UNSUPERVISED
        text += f"; no Controller attached -- {resume} re-attaches"
    elif activity in LIVE_ACTIVITIES and supervisor == job.SUPERVISOR_UNKNOWN:
        text += "; whether a Controller is attached cannot be read from /proc/locks"
    return {**base, "activity": activity, "text": text, "owned_processes": entries, "not_owned": not_owned,
            "supervisor": {"state": supervisor, "pids": list(holders)}}


def activity_fields(activity: Mapping) -> dict:
    """``job_activity``'s structured fields, as ``--json`` carries them."""
    return {key: activity[key] for key in ("activity", "worker_state", "waiting_on", "owned_processes",
                                           "not_owned", "supervisor", "resume_command")}


def _wakeup_line(wakeup: Mapping) -> str:
    state, settled_by = wakeup.get("state"), wakeup.get("settled_by")
    head = f"wakeup {wakeup.get('tool_use_id')}: {state}"
    if state == "fire_matched":
        return f"{head}, matched by harness command {wakeup.get('matched_by')}, never settled"
    if state == "settled" and settled_by == "settle_window":
        return f"{head} by settle_window, matched by harness command {wakeup.get('command_uuid')}"
    if state == "settled" and settled_by == "stop":
        text = (f"{head} by stop (cancelledWakeups {wakeup.get('cancelled_wakeups')}, "
                f"expected {wakeup.get('expected_count')})")
        if wakeup.get("command_uuid"):
            text += f", inside its own fire's harness command {wakeup.get('command_uuid')}"
        return text
    if state == "pending":
        return f"{head}, never fired"
    return head


#: The stream-diagnosis reasons plan G has ``explain`` render.
_LIFECYCLE_REASONS = frozenset({"command_lifecycle_irregular", "command_lifecycle_unterminated",
                                "wakeup_not_delivered"})


def stream_diagnosis(record: Mapping) -> dict | None:
    """The part of a ``COMPLETED`` or terminal record's
    ``worker.stream_diagnosis`` ``explain`` presents (plan G), or ``None``
    when the record carries none, or it has no harness-lifecycle matter to
    explain: no ``command_lifecycle`` anomaly, no wakeup, and none of
    :data:`_LIFECYCLE_REASONS` among its reasons (an ordinary exit-status
    failure is left to the job's own record, as before)."""
    worker_block = record.get("worker") if isinstance(record.get("worker"), Mapping) else {}
    diagnosis = worker_block.get("stream_diagnosis")
    if not isinstance(diagnosis, Mapping):
        return None
    anomalies = [dict(a) for a in diagnosis.get("command_lifecycle_anomalies") or [] if isinstance(a, Mapping)]
    wakeups = [dict(w) for w in diagnosis.get("wakeups_seen") or [] if isinstance(w, Mapping)]
    secondary = [r for r in diagnosis.get("secondary_reasons") or [] if isinstance(r, str)]
    reason = diagnosis.get("reason")
    if not (anomalies or wakeups or _LIFECYCLE_REASONS.intersection([reason, *secondary])):
        return None
    facts = diagnosis.get("supervisor_facts") if isinstance(diagnosis.get("supervisor_facts"), Mapping) else {}
    return {
        "worker_outcome": record.get("worker_outcome"),
        "reason": reason,
        "secondary_reasons": secondary,
        "command_lifecycle_anomalies": anomalies,
        "wakeups": wakeups,
        "command_lifecycle_overdue_command_uuid": facts.get("command_lifecycle_overdue_command_uuid")
        or record.get("command_lifecycle_overdue_command_uuid"),
    }


def diagnosis_lines(diagnosis: Mapping) -> list[str]:
    """:func:`stream_diagnosis` as ``explain``'s text lines: the reason
    (``command_lifecycle_irregular`` and ``command_lifecycle_unterminated``
    with the offending ``command_uuid``), one line per anomaly, and each
    wakeup's final state and how it got there."""
    reasons = [diagnosis.get("reason"), *diagnosis.get("secondary_reasons", [])]
    lines = [f"worker outcome {diagnosis.get('worker_outcome')}: {diagnosis.get('reason')}"
             + (f" (also: {', '.join(diagnosis['secondary_reasons'])})" if diagnosis.get("secondary_reasons")
                else "")]
    anomalies = diagnosis.get("command_lifecycle_anomalies") or []
    if "command_lifecycle_irregular" in reasons:
        uuids = list(dict.fromkeys(str(a["command_uuid"]) for a in anomalies if a.get("command_uuid")))
        lines.append(f"command_lifecycle_irregular: harness command(s) {', '.join(uuids) or 'none named'}")
    if "command_lifecycle_unterminated" in reasons:
        lines.append(f"command_lifecycle_unterminated: harness command "
                     f"{diagnosis.get('command_lifecycle_overdue_command_uuid')} never completed")
    for anomaly in anomalies:
        text = (f"command_lifecycle anomaly: {anomaly.get('kind')}, command_uuid {anomaly.get('command_uuid')}, "
                f"offset {anomaly.get('offset')}")
        if anomaly.get("kind") == "wakeup_count_mismatch":
            text += (f", cancelledWakeups {anomaly.get('cancelled_wakeups')}, "
                     f"expected {anomaly.get('expected_count')}")
        lines.append(text)
    lines.extend(_wakeup_line(wakeup) for wakeup in diagnosis.get("wakeups") or [])
    return lines


# ---------------------------------------------------------------------------
# The in-process renderer's output
# ---------------------------------------------------------------------------

#: Free space a FIFO sink leaves for the Controller's own stderr lines.
PIPE_RESERVE_BYTES = 16 * 1024

#: The longest a sink waits for its descriptor to accept a write.
WRITE_WAIT_SECONDS = 1.0


class FdSink:
    """``step --follow``/``run --follow``'s output: a private descriptor
    (``os.dup(2)``) written with ``os.write`` -- never ``sys.stderr``, whose
    buffered writer's lock a blocked daemon thread would hold through
    interpreter finalisation (``Fatal Python error:
    _enter_buffered_busy``).

    Each write is at most ``select.PIPE_BUF`` bytes, preceded by a
    ``poll(POLLOUT)`` wait of at most :data:`WRITE_WAIT_SECONDS`. A wait
    that expires disables the sink for good. On a FIFO it also disables
    itself rather than leave less than :data:`PIPE_RESERVE_BYTES` free, so
    a stalled reader can never block the Controller's own stderr lines.
    A stall is not reported: the note would block too. Any other failure
    propagates to the caller, which disables the sink with :meth:`fail`."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.disabled = False
        try:
            self._fifo = stat.S_ISFIFO(os.fstat(fd).st_mode)
        except OSError:
            self._fifo = False

    def _room(self, size: int) -> bool:
        poller = select.poll()
        poller.register(self.fd, select.POLLOUT)
        ready = poller.poll(WRITE_WAIT_SECONDS * 1000)
        if not ready or not ready[0][1] & select.POLLOUT:
            return bool(ready)  # an error/hangup is reported by the write itself
        if self._fifo:
            capacity = fcntl.fcntl(self.fd, fcntl.F_GETPIPE_SZ)
            unread = bytearray(4)
            fcntl.ioctl(self.fd, termios.FIONREAD, unread)
            if capacity - int.from_bytes(unread, sys.byteorder) - size < PIPE_RESERVE_BYTES:
                return False
        return True

    def _write(self, text: str) -> None:
        data = (text + "\n").encode("utf-8", "replace")
        while data:
            chunk = data[:select.PIPE_BUF]
            if not self._room(len(chunk)):
                self.disabled = True
                return
            written = os.write(self.fd, chunk)
            data = data[written:]

    def __call__(self, text: str) -> None:
        if not self.disabled:
            self._write(text)

    def fail(self, exc: BaseException) -> None:
        """Disable the sink after a failure other than a stall, with one
        attempt at a note through the same descriptor. Never raises."""
        was_disabled, self.disabled = self.disabled, True
        if was_disabled:
            return
        try:
            self._write(f"workflow-controller: warning: --follow stopped rendering: {exc}")
        except Exception:  # noqa: BLE001 -- a failed note is not a lifecycle failure
            pass
        self.disabled = True

