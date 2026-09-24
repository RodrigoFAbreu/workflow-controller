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
import termios
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from controller import job, worker

#: How often a follower polls its files.
POLL_SECONDS = 0.2

#: Silence, while a job is ``LAUNCHED``, before a heartbeat line is printed
#: (and between two heartbeats). Tests override it.
HEARTBEAT_SECONDS = 30.0

#: How many presentation events ``follow`` replays before going live
#: without ``--from-start``.
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
    """A recorded ``%Y-%m-%dT%H:%M:%SZ`` time, as an aware UTC datetime."""
    if not isinstance(at, str):
        return None
    try:
        return datetime.datetime.strptime(f"{at}+0000", "%Y-%m-%dT%H:%M:%SZ%z")
    except ValueError:
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
        return f"job {job_id} COMPLETED (worker {event.get('outcome')}, exit {event.get('exit_code')})"
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
    return f"job {job_id} {name}"


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
                 stop: Any) -> None:
        self.runtime_root = Path(runtime_root)
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
        if event.get("event") not in ("launched", "finished", "failed", "incomplete"):
            return event
        record = read_job(self.runtime_root, job_id) if isinstance(job_id, str) else None
        if record is None:
            return event
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
        self.emit(events if from_start else events[-REPLAY_EVENTS:])

    def say(self, message: str) -> None:
        self.sink(self.render(notice(message)))

    def warn_once(self, key: str, message: str) -> None:
        if key not in self.warned:
            self.warned.add(key)
            self.say(message)

    def heartbeat(self, record: Mapping | None) -> None:
        """While ``record``'s job is ``LAUNCHED`` and nothing new arrived for
        :data:`HEARTBEAT_SECONDS`: what the worker is doing."""
        if record is None or record.get("status") != job.STATUS_LAUNCHED:
            return
        now = time.monotonic()
        if now - self.last_event < HEARTBEAT_SECONDS or now - self.last_heartbeat < HEARTBEAT_SECONDS:
            return
        self.last_heartbeat = now
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
               stop: Any = None, json_output: bool = False) -> None:
    """Follow run ``run_id`` into ``sink`` (called with each rendered line):
    the run's events, and each of its jobs' events, ``worker.stdout`` and
    ``worker.stderr``.

    Ends when the run record reads ``ended`` and every log is drained; when
    the recorded Controller process is gone while the record still reads
    ``running``; or, for an ``interrupted`` run, at once -- unless its
    current job's worker is still ``active``, which is then followed as
    :func:`follow_job` would. ``stop`` (a ``threading.Event``) ends it
    after one final drain. Without ``from_start`` only the last
    :data:`REPLAY_EVENTS` events already written are shown."""
    follower = _Follower(runtime_root, sink, json_output=json_output, stop=stop)
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
            if job_record is not None and worker_liveness(job_record) == worker.ACTIVE:
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
               stop: Any = None, json_output: bool = False) -> None:
    """Follow job ``job_id`` into ``sink``: its events, ``worker.stdout``
    and ``worker.stderr``. Ends, after draining every log, when the job
    record is terminal, or when it is not and its worker is no longer
    ``active`` (``worker exited; job <id> awaits resume``) -- unless the
    job's own ``step``/``run`` is still running, whose Controller then
    finishes it. An ``unverifiable`` worker keeps it following, with a
    one-time warning."""
    follower = _Follower(runtime_root, sink, json_output=json_output, stop=stop)
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
        if not _controller_owns_job(runtime_root, record):
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
        if record.get("target_repo") == target_repo and liveness == worker.ACTIVE:
            return "job", record["job_id"]
    return None


def last_run(runtime_root: Path, target_repo: str) -> dict | None:
    """The newest run record for ``target_repo``, whatever its state."""
    runs = [run for run in list_runs(runtime_root) if run.get("target_repo") == target_repo]
    return runs[-1] if runs else None


def drain_text(record: Mapping) -> str | None:
    """``worker exited; waiting on process group: ...`` for a ``LAUNCHED``
    record carrying ``worker_group_drain``, else ``None``."""
    drain = record.get("worker_group_drain")
    if record.get("status") != job.STATUS_LAUNCHED or not isinstance(drain, Mapping):
        return None
    pids = [str(pid) for pid in drain.get("remaining_pids") or []]
    return f"worker exited; waiting on process group: {len(pids)} process(es) at drain start ({' '.join(pids)})"


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
            if capacity - int.from_bytes(unread, "little") - size < PIPE_RESERVE_BYTES:
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

