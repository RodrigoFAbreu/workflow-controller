"""Fresh Claude worker abstraction (capability 4,
``docs/ACTIVE_MILESTONE.md``).

``launch(task, *, cwd, permission_mode, timeout, stdout_path, stderr_path,
claude_bin=None, pass_fds=(), on_spawn=None, on_group_drain=None,
model=None, effort=None, disallowed_tools=None) -> WorkerResult`` is the
whole launch entry point: it runs --

    claude -p "<task>" --output-format stream-json --verbose \\
        --permission-mode <mode> [--model <model>] [--effort <effort>] \\
        [--disallowedTools <tool>,<tool>] < /dev/null

against ``cwd``, with the worker's stdout and stderr written by the worker
itself straight into two caller-created files, waits until it has returned
control (or times out), and classifies the result into exactly one of four
outcomes. It never inspects, repairs, or
writes Workflow state; it never decides *what* task to run (that is
``controller.decision``); it is a pure launch-and-classify primitive
``controller.job`` (CP6) composes.

See ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP5 -- Fresh Claude
worker abstraction".

``workflow-controller-automatic-lifecycle-orchestration`` CP5 adds the
worker's process identity (:class:`WorkerProcess`, captured at spawn and
handed to ``on_spawn`` before the wait begins) and the boot- and
host-keyed, zombie-aware liveness verdict a later ``resume``/``--abandon``
applies to it (:func:`assess_worker_liveness`). ``timeout=None`` waits
until the worker returns control: silence or a long run is never treated
as termination.

CP6 of the same milestone adds the worker's route: ``model``/``effort``
(each omitted when ``None``, so the ``claude`` CLI's own configuration
applies) and ``disallowed_tools``, passed as one comma-joined argv element
placed last, so the CLI's variadic option cannot swallow anything after
it. ``launch`` never passes ``--resume``/``-r``, ``--continue``/``-c``,
``--fork-session`` or ``--session-id``: every worker is a fresh session.
The route itself is ``controller.routing``'s; this module only places it
on the command line.

``workflow-controller-trunk-branch-pr-release-orchestration`` CP8 widens
the list, when a milestone-branch binding governs the step, with
``routing.BRANCH_GUARD_TOOLS`` -- Bash permission rules such as
``Bash(git push:*)``. They stay in the same single comma-joined element:
the CLI splits the list on commas and whitespace outside parentheses, so a
rule's own space (``Bash(git reset --hard:*)``) stays inside the rule, and
``Bash(<prefix>:*)`` denies every Bash command that starts with
``<prefix>``.

``workflow-controller-release-runtime-observability`` CP4 streams the
worker's output (``docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md``,
"Streaming worker output"): ``stream-json`` events land in the durable log
files as the worker produces them, with no pipe the Controller or a
follower could apply back-pressure through. Control returns when the
direct child has exited **and** its process group is empty (the group
drain). The final ``result`` event is parsed strictly
into the same fields the single JSON body carried before.

``workflow-controller-worker-lifecycle-ownership`` CP2 hands the stream to
``controller.worker_stream`` (``docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md``,
design B): the terminal classification accepts several ``result`` events,
uses the last one, and names why a run is not ``SUCCESS`` in
``WorkerResult.stream_diagnosis`` (owned work killed at exit, exit mid-turn,
a malformed stream).

CP3 of the same milestone makes every launch a streaming-input session
(design A): the argv is

    claude -p --input-format stream-json --output-format stream-json --verbose \\
        --permission-mode <mode> [--model <m>] [--effort <e>] \\
        --append-system-prompt <WORKER_LIFECYCLE_NOTE> [--disallowedTools <list>]

with no prompt argument; the task is one stream-json ``user`` line on a pipe
whose write end is held by the stdin anchor (``controller.anchor``), which
also holds the lifecycle lock. ``launch`` supervises the stream (design C):
``RUNNING`` while a turn is open, ``WAITING`` while the worker owns
background work, ``ENDING`` at a quiescent terminal turn (only then is
stdin closed), ``DRAINING`` while owned processes outlive the worker, and
``ENDED``. Descendants are owned through the ownership tag in
:data:`OWNERSHIP_VAR`, the process group, adoption by the Controller as a
child subreaper, and the record of what was already seen owned; recognised
tool daemons (:data:`RECOGNISED_DAEMONS`) never are. The drain is bounded by
:data:`DRAIN_DETACH_SECONDS`, after which ``launch`` returns
:class:`DrainDetached` and ends nothing.

CP5 adds :func:`reattach` (plan E): the same supervision, resumed by a
``resume`` that did not launch the worker, from the job record -- the
stream state rebuilt by replaying the stream (:func:`replay_stream`) with
the persisted supervisor facts, the worker and the anchor followed by their
recorded identities (:func:`identity_alive`), the exit status unknown. It
never launches anything. :func:`scan_recorded_owned_processes` is the
ownership scan a Controller that is not supervising a job makes for its
ownership hold.

``workflow-controller-child-process-reaping`` CP2 collects every finished
child this process holds as a subreaper: on every supervision tick, in every
state, once more after the launch has given up the subreaper, and between
launches through a background reaper while anything recorded remains
(:func:`_collect_children`, :func:`reap_adopted_children`). Each child is
reaped by its own pid, never the worker or the anchor of any launch in
progress, never a child the process spawned in its own session (only an
orphan seen born to one of those), and never a child registered with
:func:`exclude_from_reaping`.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import datetime
import fcntl
import json
import os
import secrets
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from controller import anchor as anchor_module
from controller import lock, runtime, worker_stream
from controller.errors import UserOnlyCommandError, WorkerLaunchError

#: The four outcomes :func:`launch` classifies a completed (or interrupted)
#: worker run into, by ``controller.worker_stream.classify``'s ordered rows:
#: ``INTERRUPTED`` (a timeout, or termination by signal), ``FAILURE`` (a
#: non-zero exit), ``AMBIGUOUS`` (a malformed stream, no ``result``, an
#: exit mid-turn or with owned work still open or killed after the end),
#: ``FAILURE``/``AMBIGUOUS`` for an ``is_error`` or incomplete last
#: ``result``, and only then ``SUCCESS``.
SUCCESS = worker_stream.SUCCESS
FAILURE = worker_stream.FAILURE
AMBIGUOUS = worker_stream.AMBIGUOUS
INTERRUPTED = worker_stream.INTERRUPTED

#: The four Workflow commands CP4's ``controller.decision.
#: derive_user_only_commands`` derives fresh from the installed
#: ``.claude/commands/*.md`` files, as the union of two recognisers
#: (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "The user-only
#: denylist, derived from the property rather than from a proxy", revision
#: 64's baseline widening, round 63's ``B6``): three of the four --
#: ``approve-review``, ``accept-milestone``, ``recover-implementation-
#: provenance`` -- carry the qualified ``workflow_state.
#: validate_..._confirmation`` guard against a live human turn in their
#: own procedural text; the fourth, ``request-plan-amendment``, carries
#: only the front-matter ``disable-model-invocation: true`` flag instead,
#: so a single-recogniser derivation would miss it. This module keeps its
#: own, independent literal copy of the resulting four-name set so
#: :func:`launch` can refuse a task naming one of these commands without
#: depending on an installed commands directory at call time -- "never
#: fabricate user approval" is enforced twice, by two different mechanisms
#: fed by two different sources, never by one shared code path a single
#: defect could disable in both places at once.
USER_ONLY_COMMANDS: frozenset[str] = frozenset({
    "approve-review",
    "accept-milestone",
    "recover-implementation-provenance",
    "request-plan-amendment",
})

#: Trailing punctuation a command mention can carry in ordinary prose (a
#: backticked or sentence-final mention) -- stripped from a token's tail
#: before the denylist comparison, per the plan's own text model.
_TRAILING_PUNCTUATION = "`'\",.;:)]}"

#: Leading characters a command mention can carry: the ``/`` the plan's
#: text model names explicitly, plus the opening backtick of a backticked
#: mention -- both stripped as a run before the denylist comparison. The
#: plan's own worked example (a backticked `` `/approve-review` `` mention
#: recognised the same as the bare token) requires stripping the opening
#: backtick as well as the slash: a token that begins with a literal
#: backtick immediately followed by ``/`` has no leading ``/`` to strip
#: until the backtick is stripped first.
_LEADING_MARKERS = "`/"

#: The two required fields the last ``result`` event must carry for its
#: result to be decidable at all (every other field is optional and
#: defaults to ``None`` when absent).
_REQUIRED_FIELDS = ("session_id", "is_error")

#: The remaining fields ``docs/ACTIVE_MILESTONE.md`` names as what the
#: Controller needs from a worker's JSON result, preserved verbatim (as
#: ``None`` when absent) on every :class:`WorkerResult`.
_OPTIONAL_FIELDS = (
    "subtype", "terminal_reason", "stop_reason", "result", "num_turns",
    "permission_denials", "total_cost_usd", "duration_ms",
)


def _task_tokens(task: str) -> list[str]:
    """Split ``task`` on whitespace runs into tokens, each with a leading
    ``/`` and any run of trailing punctuation from ``_TRAILING_PUNCTUATION``
    stripped -- the plan's own text model (revision 47), so a backticked
    `` `/approve-review` `` or a sentence-final ``/approve-review.`` is
    recognised the same as the bare token ``approve-review``."""
    tokens = []
    for raw in task.split():
        token = raw.lstrip(_LEADING_MARKERS)
        token = token.rstrip(_TRAILING_PUNCTUATION)
        tokens.append(token)
    return tokens


def _assert_not_user_only(task: str) -> None:
    """Raise :class:`~controller.errors.UserOnlyCommandError` if any
    whitespace-delimited, punctuation-stripped token of ``task`` equals
    (case-sensitively, whole-token) one of :data:`USER_ONLY_COMMANDS`'s
    four bare names. A whole-token equality, never a substring test --
    a task that merely contains one of the four names' letters as part
    of a longer word is not refused."""
    for token in _task_tokens(task):
        if token in USER_ONLY_COMMANDS:
            raise UserOnlyCommandError(
                f"refusing to launch a worker with a task naming the user-only "
                f"command {token!r} -- {token} requires a live human confirmation "
                f"turn no fresh worker can supply",
                evidence={"task": task, "matched_token": token},
            )


@dataclasses.dataclass(frozen=True)
class WorkerResult:
    """The classified outcome of one ``launch()`` call.

    ``outcome`` is one of :data:`SUCCESS`/:data:`FAILURE`/
    :data:`AMBIGUOUS`/:data:`INTERRUPTED`. ``returncode`` is the worker
    process's own exit status (negative when it was terminated by a
    signal, including the Controller's own timeout-driven ``SIGKILL``).
    ``stdout``/``stderr`` are the contents of the two files the worker
    wrote itself (``launch``'s ``stdout_path``/``stderr_path``), decoded as
    UTF-8 with ``errors="replace"`` and preserved verbatim so a human can
    read exactly what the worker said; this module never writes those
    files. ``raw_json`` is the stream's last ``result`` event (``None``
    when it has none); the eight named fields below are that same event's
    own fields, extracted for convenience and ``None`` wherever there was
    no ``result`` or the field was absent. ``stream_diagnosis`` is
    ``controller.worker_stream.classify``'s structured account of the
    stream (why the outcome is what it is). ``owned_processes_seen`` is
    ``{"count", "sample"}``: how many distinct processes the supervisor
    ever recorded as owned, and the first :data:`OWNED_PROCESS_SAMPLE` of
    them.
    """

    outcome: str
    returncode: int | None
    session_id: str | None
    is_error: bool | None
    subtype: str | None
    terminal_reason: str | None
    stop_reason: str | None
    result: str | None
    num_turns: int | None
    permission_denials: object | None
    total_cost_usd: float | None
    duration_ms: int | None
    stdout: str
    stderr: str
    raw_json: dict | None
    stream_diagnosis: dict | None = None
    owned_processes_seen: dict | None = None


def _extract_fields(parsed: dict | None) -> dict:
    if parsed is None:
        return {field: None for field in (*_REQUIRED_FIELDS, *_OPTIONAL_FIELDS)}
    return {field: parsed.get(field) for field in (*_REQUIRED_FIELDS, *_OPTIONAL_FIELDS)}


def _kill_process_group(pid: int) -> None:
    """``os.killpg`` the whole process group ``pid`` started
    (``start_new_session=True`` makes the worker its own process-group
    leader), so a timeout tears down every subprocess the worker itself
    spawned rather than leaving orphans holding the target repository.
    Tolerates the group already being gone."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


#: How often the group drain rescans the worker's process group.
_DRAIN_POLL_SECONDS = 0.2

#: How often, and for how long at most, :func:`launch` rescans the group
#: after the phase-2 kill before it stops waiting for the scan to report it
#: empty.
_DRAIN_KILL_POLL_SECONDS = 0.05
_DRAIN_KILL_SETTLE_SECONDS = 2.0


def _kill_drained_group(pgid: int) -> None:
    """``SIGKILL`` the process group ``pgid`` once its leader has been
    reaped. :func:`_kill_process_group` cannot do this: ``os.getpgid`` of
    the reaped leader raises ``ProcessLookupError`` and it would signal
    nothing. ``pgid`` is the leader's pid (``start_new_session=True``), and
    Linux never reuses a pid that still names a process group, so the
    signal cannot reach an unrelated group. Tolerates the group already
    being empty."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _group_members(worker_process: WorkerProcess) -> tuple[bool, list[int]]:
    """``(empty, members)`` for the worker's process group after its
    leader was reaped, through :func:`process_test`. Only ``not live``
    means empty: under the ``killpg`` form ``possibly live`` keeps the
    drain waiting (a zombie member included), as
    :func:`assess_worker_liveness` never reads it as gone. ``members`` is
    the ``/proc`` member scan's list, and empty under the ``killpg`` form,
    which has none."""
    answer = process_test(worker_process.pid, worker_process.pid, worker_process.start_ticks)
    return answer.answer == NOT_LIVE, list(answer.members)


def _end_drained_group(worker_process: WorkerProcess) -> None:
    """The phase-2 kill: ``SIGKILL`` the group, then rescan it until it is
    reported empty, for at most :data:`_DRAIN_KILL_SETTLE_SECONDS`. The
    members are not the Controller's children, so it cannot reap them; a
    group still reported non-empty after the bound (the ``killpg`` form
    cannot tell a zombie from a running process; a ``/proc`` member may be
    in uninterruptible sleep) is not waited for further."""
    _kill_drained_group(worker_process.pid)
    deadline = time.monotonic() + _DRAIN_KILL_SETTLE_SECONDS
    while not _group_members(worker_process)[0] and time.monotonic() < deadline:
        time.sleep(_DRAIN_KILL_POLL_SECONDS)


def _read_stream(path: str | Path) -> str:
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Streaming-input launch and supervision (worker-lifecycle-ownership CP3).
# ---------------------------------------------------------------------------

#: The environment variable carrying a worker's ownership tags, a
#: ``:``-separated list: ``launch`` appends the job's tag to whatever the
#: Controller itself inherited, so a worker that runs a nested Controller
#: stays owned by the outer job too (design C, step 1).
OWNERSHIP_VAR = anchor_module.OWNERSHIP_VAR

#: The Controller's worker lifecycle system note (plan decision 11), passed
#: with ``--append-system-prompt``. Its text is part of the production argv
#: CP1 captured (``tests/harness_contract/capture.py``), and is unchanged.
WORKER_LIFECYCLE_NOTE = (
    "Workflow Controller worker lifecycle: the Controller keeps this session open while you own "
    "background work and delivers every background-task, Monitor and subagent notification to you "
    "as a new turn. Do not schedule fallback wakeups (ScheduleWakeup) in case a notification never "
    "arrives, and cancel any wakeup you no longer need (ScheduleWakeup with stop: true) before "
    "ending your final turn."
)

#: The closed ``worker_state`` enumeration (invariant I8). ``STARTING`` is
#: written by the caller before the spawn; ``launch`` reports the rest
#: through ``on_state_change``.
STARTING = "STARTING"
RUNNING = "RUNNING"
WAITING = "WAITING"
ENDING = "ENDING"
DRAINING = "DRAINING"
ENDED = "ENDED"
WORKER_STATES = (STARTING, RUNNING, WAITING, ENDING, DRAINING, ENDED)

#: Long-lived tool daemons a worker may start, which later jobs reuse:
#: shared infrastructure, never owned (plan decision 9, limitation H9).
#: Defined in the stdlib-only ``controller.anchor`` so the anchor's orphan
#: rule applies the same exclusion; this is the same object.
RECOGNISED_DAEMONS = anchor_module.RECOGNISED_DAEMONS

#: How often the supervisor reads the stream (every
#: :data:`_ACTIVE_POLL_SECONDS` while bytes arrived within the last
#: :data:`_ACTIVE_STREAM_SECONDS`), and how often it scans for owned
#: processes while ``WAITING`` or ``DRAINING``. The worker's exit is
#: noticed at once: the supervisor waits on the process between reads.
_SUPERVISE_POLL_SECONDS = 0.2
_ACTIVE_POLL_SECONDS = 0.05
_ACTIVE_STREAM_SECONDS = 1.0
_OWNERSHIP_SCAN_SECONDS = 1.0

#: How long a quiescent stream must stay quiescent, with no new bytes,
#: before the supervisor ends the session. The harness writes a turn's
#: ``result`` with ``queued_turn_count: 0`` and still opens an already
#: queued notification turn a few milliseconds later, so B's
#: ``quiescent()`` is momentarily true between the two. In CP1's fixtures
#: that gap follows a background task that ended during or after the last
#: turn (4 ms in ``p11_subagent_handback``, 21-52 ms in ``p3``, ``p4``,
#: ``p8_monitor_timeout``): then the longer window applies, otherwise the
#: shorter one. Either only ever delays ``ENDING``; neither makes a
#: non-quiescent worker end.
_QUIESCENCE_CONFIRM_SECONDS = 0.1
_NOTIFICATION_CONFIRM_SECONDS = 1.0

#: How long the supervisor waits for the anchor to install its ``SIGUSR1``
#: handler before it signals it anyway.
_ANCHOR_READY_SECONDS = 10.0

#: The harness-contract bounds (plan C, decision 5): a ``pending`` wakeup
#: this far past due over task-free idle time, or a ``command_lifecycle``
#: bracket stalled this long with no task open, ends the session and fails
#: closed.
WAKEUP_GRACE_SECONDS = 300
COMMAND_LIFECYCLE_GRACE_SECONDS = 300

#: The idle time after a ``fire_matched`` wakeup's bracket closed before
#: the supervisor declares it settled (plan decision 13).
WAKEUP_SETTLE_SECONDS = WAKEUP_GRACE_SECONDS + worker_stream.WAKEUP_SKEW_SECONDS

#: How long, after the worker exited, the supervisor waits for owned
#: processes before it detaches (plan decision 9). It ends nothing. An
#: interim 3-hour bound (amendment 1 of adaptive test sharding): legitimate
#: background verification outlives 600 s. Making it configurable is
#: deferred (ROADMAP 1.4).
DRAIN_DETACH_SECONDS = 10800

#: How many owned processes ``WorkerResult.owned_processes_seen`` lists.
OWNED_PROCESS_SAMPLE = 20

_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37

#: ``stat`` states that are not running (zombie, dead).
_GONE_STATES = frozenset({"Z", "X", "x"})


def quiescence_confirm_seconds(stream: worker_stream.WorkerStream) -> float:
    """How long a quiescent ``stream`` must stay without new bytes before
    ``ENDING``: :data:`_NOTIFICATION_CONFIRM_SECONDS` while a background
    task ended during or after the last turn (its notification turn may
    already be queued), else :data:`_QUIESCENCE_CONFIRM_SECONDS`."""
    last_open = stream.turns[-1].open_offset if stream.turns else 0
    owed = any(task.listed and task.status_offset is not None and task.status_offset >= last_open
               for task in stream.tasks.values())
    return _NOTIFICATION_CONFIRM_SECONDS if owed else _QUIESCENCE_CONFIRM_SECONDS


@dataclasses.dataclass(frozen=True)
class DrainDetached:
    """What :func:`launch` returns when owned processes outlived the worker
    by :data:`DRAIN_DETACH_SECONDS` (plan C, step 3). Nothing was ended: the
    anchor still holds the lifecycle lock, and the caller keeps the job
    held. ``remaining`` lists each live owned process (``pid``,
    ``start_ticks``, ``source``, ``cmdline``); ``worker_state`` is the last
    ``DRAINING`` details; ``supervisor_facts`` the facts a later classifier
    needs."""

    drain_detached_at: str
    remaining: tuple[dict, ...]
    returncode: int | None
    worker_state: dict
    supervisor_facts: dict

    @property
    def remaining_pids(self) -> list[int]:
        return [entry["pid"] for entry in self.remaining]


def worker_argv(
    claude_bin: str,
    *,
    permission_mode: str,
    model: str | None = None,
    effort: str | None = None,
    disallowed_tools: Iterable[str] | None = None,
) -> list[str]:
    """The streaming-input argv (design A): no prompt argument, the system
    note, and the disallow list as one comma-joined element placed last --
    the CLI's ``--disallowedTools`` is variadic, so nothing may follow it."""
    # `stream-json` in print mode requires `--verbose` (the CLI refuses it
    # otherwise, before any request).
    args = [claude_bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--permission-mode", permission_mode]
    if model is not None:
        args += ["--model", model]
    if effort is not None:
        args += ["--effort", effort]
    args += ["--append-system-prompt", WORKER_LIFECYCLE_NOTE]
    tools = tuple(disallowed_tools or ())
    if tools:
        args += ["--disallowedTools", ",".join(tools)]
    return args


def task_line(task: str) -> bytes:
    """The one stream-json ``user`` message that carries ``task``."""
    return (json.dumps({"type": "user", "message": {"role": "user", "content": task}}) + "\n").encode("utf-8")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- the child subreaper -------------------------------------------------------


def _prctl(option: int, argument) -> int | None:
    """``prctl(option, argument, 0, 0, 0)`` through ``libc``, or ``None``
    where it cannot be called (a patchable seam: "prctl unavailable")."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(option, argument, 0, 0, 0)
    except (OSError, AttributeError):
        return None


def _get_child_subreaper() -> int | None:
    value = ctypes.c_int(0)
    if _prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(value)) != 0:
        return None
    return value.value


def _set_child_subreaper(value: int) -> bool:
    return _prctl(_PR_SET_CHILD_SUBREAPER, value) == 0


#: How many launches in this process currently want the subreaper, and the
#: value it had before the first of them.
_subreaper_users = 0
_subreaper_previous: int | None = None


@contextlib.contextmanager
def _child_subreaper():
    """Mark this process ``PR_SET_CHILD_SUBREAPER`` for the duration, then
    restore the previous value; yields whether it is a subreaper. Where
    ``prctl`` is unavailable the launch goes on without it: the tag scan is
    the authority, the subreaper a best-effort helper."""
    global _subreaper_users, _subreaper_previous
    if _subreaper_users == 0:
        _subreaper_previous = _get_child_subreaper()
        enabled = _subreaper_previous is not None and _set_child_subreaper(1)
    else:
        enabled = _subreaper_previous is not None
    _subreaper_users += 1
    try:
        yield enabled
    finally:
        _subreaper_users -= 1
        if _subreaper_users == 0 and _subreaper_previous is not None:
            _set_child_subreaper(_subreaper_previous)


#: Processes this Controller adopted as a subreaper, by pid (with their
#: start ticks): each one is reaped, by its own pid, whenever it is found a
#: zombie, in this launch or a later one in the same process.
_ADOPTED: dict[int, int | None] = {}

#: Held by every record-and-reap step and across every protected spawn and
#: its registration (the worker's, the anchor's, and
#: :func:`exclude_from_reaping`'s), so no sweep can record or reap a
#: new-session child between its fork and its registration. Re-entrant: the
#: sweep's record step calls :func:`_reap_adopted`, which takes it too. It
#: is never held across a wait for anything but a dead child.
_SPAWN_LOCK = threading.RLock()

#: The worker and anchor of every launch in progress in this process, as
#: ``pid -> start_ticks`` (``None``: excluded by pid alone), so overlapping
#: launches never record each other's.
_LAUNCH_CHILDREN: dict[int, int | None] = {}

#: Which launch last registered each :data:`_LAUNCH_CHILDREN` entry: a
#: finished launch's pid can be reused by another launch's worker or anchor
#: before the first launch's final sweep, which must then leave the newer
#: entry in place.
_LAUNCH_OWNERS: dict[int, "_LaunchExclusions"] = {}

#: Pids registered by :func:`exclude_from_reaping`, with a count (nesting).
_REAP_EXCLUDED: dict[int, int] = {}

#: Same-session processes the sweep has seen as a descendant of one of this
#: process's children, never as a child of the process itself, as
#: ``pid -> start_ticks``: only these same-session children may be recorded
#: (a same-session child the process spawned itself may be waited for).
_FOREIGN_BORN: dict[int, int] = {}

#: When the sweep last fell back to the full ``/proc`` scan (monotonic).
_last_full_scan: float | None = None

#: The between-launch reaper's tick, and the thread itself while it runs.
_IDLE_REAP_SECONDS = 0.2
_IDLE_REAPER_NAME = "workflow-controller-reaper"
_idle_reaper: threading.Thread | None = None
_idle_reaper_stop = threading.Event()


def _reap_excluded(pid: int, stat: _ProcStat) -> bool:
    """Whether ``pid`` (with its current ``stat``) must never be recorded or
    reaped: a worker or anchor of a launch in progress (by pid alone while
    its start ticks are unknown), a child in this process's own session that
    the sweep has not seen born to another child, or a pid registered with
    :func:`exclude_from_reaping`. Callers hold :data:`_SPAWN_LOCK`."""
    if pid in _REAP_EXCLUDED:
        return True
    if pid in _LAUNCH_CHILDREN:
        ticks = _LAUNCH_CHILDREN[pid]
        if ticks is None or ticks == stat.start_ticks:
            return True
    return stat.session == os.getsid(0) and _FOREIGN_BORN.get(pid) != stat.start_ticks


def _record_adopted(pid: int, stat: _ProcStat) -> None:
    """The one writer of :data:`_ADOPTED`: records ``pid`` unless
    :func:`_reap_excluded` excludes it."""
    with _SPAWN_LOCK:
        if not _reap_excluded(pid, stat):
            _ADOPTED.setdefault(pid, stat.start_ticks)


def _reap_adopted() -> None:
    """``waitpid(pid, WNOHANG)`` for every adopted pid -- never
    ``waitpid(-1)``, so the worker's own exit status is never stolen from
    ``Popen``. Each recorded pid's ``stat`` is re-read first: a pid that is
    gone, now names another process (other start ticks), or is excluded by
    :func:`_reap_excluded` is dropped without a ``waitpid``; one whose
    ``stat`` cannot be read is kept and skipped this time."""
    with _SPAWN_LOCK:
        root = _proc_root()
        for pid, ticks in list(_ADOPTED.items()):
            try:
                stat = _read_stat(root / str(pid) / "stat")
            except _NoAnswer:
                continue
            if stat is None or (ticks is not None and stat.start_ticks != ticks) or _reap_excluded(pid, stat):
                _ADOPTED.pop(pid, None)
                continue
            try:
                reaped, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                reaped = pid
            if reaped:
                _ADOPTED.pop(pid, None)
                _FOREIGN_BORN.pop(pid, None)


def reap_adopted_children() -> None:
    """Reap, each by its own pid, every recorded child that has finished
    (between launches, steps and at the end of a run)."""
    _reap_adopted()


@contextlib.contextmanager
def exclude_from_reaping(spawn: Callable[[], subprocess.Popen]):
    """Spawn a child with ``spawn()`` and keep this process's reaper away
    from it for the block, which yields the ``Popen``. The only way for code
    outside :func:`launch` to protect a child it starts **in a new session**
    while a launch supervises in the same process and whose exit status it
    reads; the caller waits for it inside the block. The spawn and the
    registration happen under one hold of :data:`_SPAWN_LOCK`."""
    with _SPAWN_LOCK:
        child = spawn()
        _REAP_EXCLUDED[child.pid] = _REAP_EXCLUDED.get(child.pid, 0) + 1
    try:
        yield child
    finally:
        with _SPAWN_LOCK:
            count = _REAP_EXCLUDED.get(child.pid, 0) - 1
            if count > 0:
                _REAP_EXCLUDED[child.pid] = count
            else:
                _REAP_EXCLUDED.pop(child.pid, None)


def _direct_children(pid: int | None = None) -> set[int] | None:
    """The direct children of ``pid`` (default: this process): the union of
    ``/proc/<pid>/task/<tid>/children`` over every thread, or ``None`` when
    the ``children`` file does not exist (``CONFIG_PROC_CHILDREN`` off). A
    thread that exits while it is read is skipped; another ``pid`` that has
    exited has no children (a patchable seam)."""
    own = pid is None
    task = _proc_root() / str(os.getpid() if own else pid) / "task"
    try:
        tids = os.listdir(task)
    except FileNotFoundError:
        return None if own else set()
    except OSError:
        return None
    children: set[int] = set()
    supported = False
    for tid in tids:
        try:
            with open(task / tid / "children") as fh:
                text = fh.read()
        except FileNotFoundError:
            if (task / tid).is_dir():
                return None  # the thread is there, the file is not
            continue  # the thread exited
        except OSError:
            continue
        supported = True
        children.update(int(item) for item in text.split() if item.isdigit())
    if not supported and own:
        return None
    return children


def _scan_proc_stats() -> dict[int, _ProcStat] | None:
    """Every process's ``stat`` by a full ``/proc`` scan, or ``None`` when
    ``/proc`` cannot be listed. Unreadable and vanished entries are
    skipped."""
    root = _proc_root()
    try:
        names = _list_proc(root)
    except OSError:
        return None
    stats: dict[int, _ProcStat] = {}
    for name in names:
        if not name.isdigit():
            continue
        try:
            stat = _read_stat(root / name / "stat")
        except _NoAnswer:
            continue
        if stat is not None:
            stats[int(name)] = stat
    return stats


def _update_foreign_born(children: Mapping[int, _ProcStat], all_stats: Mapping[int, _ProcStat] | None) -> None:
    """Admit to :data:`_FOREIGN_BORN` every same-session descendant of a
    same-session child in ``children`` (walked only through same-session
    descendants), when the ``stat`` read that gives its start ticks also
    shows the walked parent as its ``ppid``; then drop every entry whose
    process is gone or whose pid names another process. ``all_stats`` (the
    full-scan fallback) supplies the ``ppid`` links where ``children`` files
    are missing. Callers hold :data:`_SPAWN_LOCK`."""
    sid = os.getsid(0)
    root = _proc_root()
    seen: dict[int, int] = {pid: stat.start_ticks for pid, stat in children.items()}
    walk = [pid for pid, stat in children.items() if stat.session == sid]
    while walk:
        parent = walk.pop()
        if all_stats is not None:
            kids = {pid: stat for pid, stat in all_stats.items() if stat.ppid == parent}
        else:
            kids = {}
            for pid in _direct_children(parent) or ():
                try:
                    stat = _read_stat(root / str(pid) / "stat")
                except _NoAnswer:
                    continue
                if stat is not None and stat.ppid == parent:
                    kids[pid] = stat
        for pid, stat in kids.items():
            if stat.session != sid or pid in seen:
                continue
            _FOREIGN_BORN[pid] = stat.start_ticks
            seen[pid] = stat.start_ticks
            walk.append(pid)
    for pid, ticks in list(_FOREIGN_BORN.items()):
        if seen.get(pid) == ticks:
            continue
        try:
            stat = _read_stat(root / str(pid) / "stat")
        except _NoAnswer:
            continue
        if stat is None or stat.start_ticks != ticks:
            _FOREIGN_BORN.pop(pid, None)


def _collect_children(exclude: Mapping[int, int | None], baseline: set[int], *, force_full: bool = False) -> None:
    """The sweep: record every child of this process except ``baseline``,
    ``exclude`` (this launch's worker and anchor, ``pid -> start_ticks``,
    ``None`` for pid alone) and what :func:`_reap_excluded` excludes, then
    reap every recorded pid. The children come from :func:`_direct_children`
    or, without ``children`` files, from the full ``/proc`` scan at most
    once per :data:`_OWNERSHIP_SCAN_SECONDS` (always with ``force_full``).
    A child whose ``stat`` cannot be read is left for the next sweep. It
    publishes nothing."""
    global _last_full_scan
    with _SPAWN_LOCK:
        me = os.getpid()
        root = _proc_root()
        listed = _direct_children()
        all_stats = None
        children: dict[int, _ProcStat] = {}
        if listed is None:
            now = time.monotonic()
            if force_full or _last_full_scan is None or now - _last_full_scan >= _OWNERSHIP_SCAN_SECONDS:
                _last_full_scan = now
                all_stats = _scan_proc_stats()
            if all_stats is not None:
                children = {pid: stat for pid, stat in all_stats.items() if stat.ppid == me}
        else:
            for pid in listed:
                try:
                    stat = _read_stat(root / str(pid) / "stat")
                except _NoAnswer:
                    continue
                if stat is not None and stat.ppid == me:
                    children[pid] = stat
        if children:
            _update_foreign_born(children, all_stats)
        for pid, stat in children.items():
            if pid in baseline:
                continue
            if pid in exclude and exclude[pid] in (None, stat.start_ticks):
                continue
            _record_adopted(pid, stat)
        _reap_adopted()


def _idle_reap_loop() -> None:
    """The between-launch reaper: reap every :data:`_IDLE_REAP_SECONDS`
    until nothing is recorded. The reap, the emptiness check and the exit
    are one hold of :data:`_SPAWN_LOCK`, so a record made concurrently
    either keeps this loop going or finds no reaper and starts one."""
    global _idle_reaper
    while True:
        with _SPAWN_LOCK:
            _reap_adopted()
            if not _ADOPTED or _idle_reaper_stop.is_set():
                _idle_reaper = None
                return
        _idle_reaper_stop.wait(_IDLE_REAP_SECONDS)


def _ensure_idle_reaper() -> None:
    """Start the between-launch reaper if a child is recorded and none
    runs."""
    global _idle_reaper
    with _SPAWN_LOCK:
        if not _ADOPTED or _idle_reaper is not None:
            return
        _idle_reaper = threading.Thread(target=_idle_reap_loop, name=_IDLE_REAPER_NAME, daemon=True)
        _idle_reaper.start()


def _stop_idle_reaper(timeout: float) -> None:
    """Test support: end a running between-launch reaper and wait for it.
    :data:`_ADOPTED` is left as it is; use :func:`_reset_reaping_for_tests`,
    which never strands a live recorded child."""
    with _SPAWN_LOCK:
        thread = _idle_reaper
    if thread is None:
        return
    _idle_reaper_stop.set()
    try:
        thread.join(timeout)
    finally:
        _idle_reaper_stop.clear()


def _reset_reaping_for_tests(timeout: float) -> list[int]:
    """Test support: stop the between-launch reaper, then settle every
    record against the **real** ``/proc`` (never the patchable seam): a
    record that is this process's child with the recorded start ticks and
    that :func:`_reap_excluded` admits is killed (unless already a zombie)
    and collected by its pid; every other record is dropped untouched.
    :data:`_ADOPTED` and :data:`_FOREIGN_BORN` are then cleared. Returns the
    pids collected."""
    _stop_idle_reaper(timeout)
    collected: list[int] = []
    with _SPAWN_LOCK:
        me = os.getpid()
        for pid, ticks in list(_ADOPTED.items()):
            try:
                stat = _read_stat(Path("/proc") / str(pid) / "stat")
            except _NoAnswer:
                continue
            if stat is None or stat.ppid != me or stat.start_ticks != ticks or _reap_excluded(pid, stat):
                continue
            if stat.state not in _GONE_STATES:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, 0)
            collected.append(pid)
        _ADOPTED.clear()
        _FOREIGN_BORN.clear()
    return collected


class _LaunchExclusions:
    """What one launch's sweeps skip, filled as it comes to exist: the
    baseline, then the worker (pid first, start ticks once captured), then
    the anchor. Every identity is also entered in :data:`_LAUNCH_CHILDREN`
    until the launch's final sweep has run."""

    def __init__(self) -> None:
        self.baseline: set[int] | None = None
        self.children: dict[int, int | None] = {}

    def add(self, pid: int, start_ticks: int | None = None) -> None:
        with _SPAWN_LOCK:
            self.children[pid] = start_ticks
            _LAUNCH_CHILDREN[pid] = start_ticks
            _LAUNCH_OWNERS[pid] = self

    def collect(self, *, force_full: bool = False) -> None:
        if self.baseline is not None:
            _collect_children(self.children, self.baseline, force_full=force_full)

    def final_sweep(self) -> None:
        """Run once the launch has given up the subreaper (I5): the sweep,
        then this launch's entries leave :data:`_LAUNCH_CHILDREN` (only those
        it still owns: an entry another launch registered for a reused pid
        stays), then the between-launch reaper starts if anything is still
        recorded."""
        try:
            self.collect(force_full=True)
        finally:
            with _SPAWN_LOCK:
                for pid in self.children:
                    if _LAUNCH_OWNERS.get(pid) is self:
                        del _LAUNCH_OWNERS[pid]
                        _LAUNCH_CHILDREN.pop(pid, None)
        _ensure_idle_reaper()


def _reap_killed_children(baseline: set[int], pgid: int, others: Iterable[int] = ()) -> None:
    """After a kill: reap, each by its own pid, every zombie child this
    Controller adopted (not in the launch's ``baseline``), until no
    adopted child of the killed group ``pgid`` (or of ``others``) is still
    running, for at most :data:`_DRAIN_KILL_SETTLE_SECONDS`. As a subreaper
    the Controller inherits the killed descendants, and an unreaped zombie
    would look alive to every later check."""
    me = os.getpid()
    others = set(others)
    root = _proc_root()
    deadline = time.monotonic() + _DRAIN_KILL_SETTLE_SECONDS
    while True:
        running = False
        try:
            names = _list_proc(root)
        except OSError:
            return
        for name in names:
            if not name.isdigit() or int(name) in baseline:
                continue
            try:
                stat = _read_stat(root / name / "stat")
            except _NoAnswer:
                continue
            if stat is None or stat.ppid != me:
                continue
            pid = int(name)
            if stat.state in _GONE_STATES:
                _record_adopted(pid, stat)
            elif stat.pgrp == pgid or pid in others:
                running = True
        _reap_adopted()
        if not running or time.monotonic() >= deadline:
            return
        time.sleep(_DRAIN_KILL_POLL_SECONDS)


def _own_children() -> set[int]:
    """This process's current children, by ``ppid`` (the launch's baseline:
    anything that becomes a child later, other than the worker and the
    anchor, was adopted)."""
    me = os.getpid()
    children = set()
    root = _proc_root()
    try:
        names = _list_proc(root)
    except OSError:
        return children
    for name in names:
        if not name.isdigit():
            continue
        try:
            stat = _read_stat(root / name / "stat")
        except _NoAnswer:
            continue
        if stat is not None and stat.ppid == me:
            children.add(int(name))
    return children


# -- the ownership scan --------------------------------------------------------


def _list_proc(root: Path) -> list[str]:
    """``os.listdir`` of ``/proc`` (a patchable seam: an unreadable
    ``/proc``)."""
    return os.listdir(root)


def _read_environ(root: Path, pid: int) -> bytes:
    """``/proc/<pid>/environ`` (a patchable seam: ``EACCES``, a scrubbed
    tag)."""
    with open(f"{root}/{pid}/environ", "rb") as fh:
        return fh.read()


#: The fields of a recognised daemon's entry in
#: ``worker_state.excluded_processes``: presentation data only (never owned,
#: never ended, never replayed). ``cmdline`` is the same space-joined,
#: 200-character field an owned entry carries, which
#: ``observe.job_activity``'s "not owned" names.
DAEMON_ENTRY_FIELDS = ("pid", "start_ticks", "pattern", "cmdline")


def _read_cmdline(root: Path, pid: int) -> list[str]:
    try:
        with open(f"{root}/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    return [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]


def _carries_tag(root: Path, pid: int, tag: str) -> bool:
    """Whether ``pid``'s ``environ`` lists ``tag``. An unreadable
    ``environ`` (the non-dumpable processes the plan measured) is not owned
    by tag; group membership and adoption still apply to it."""
    try:
        environ = _read_environ(root, pid)
    except OSError:
        return False
    prefix = f"{OWNERSHIP_VAR}=".encode()
    for item in environ.split(b"\0"):
        if item.startswith(prefix):
            return tag.encode() in item[len(prefix):].split(b":")
    return False


#: The :data:`RECOGNISED_DAEMONS` pattern a command line matches, or
#: ``None`` (``controller.anchor.daemon_pattern``, shared with the anchor).
daemon_pattern = anchor_module.daemon_pattern


class _Ownership:
    """The owned-process set of one launch (plan C, step 3): members of the
    worker's process group, same-uid processes carrying the job's tag,
    children this Controller adopted as a subreaper, and every process
    already seen owned whose ``(pid, start_ticks)`` still matches -- minus
    the worker, the anchor and recognised daemons."""

    def __init__(self, *, tag: str, worker_process: WorkerProcess, anchor_pid: int | None,
                 baseline: set[int], adopting: bool) -> None:
        self.tag = tag
        self.worker_process = worker_process
        self.pgid = worker_process.pgid or worker_process.pid
        self.anchor_pid = anchor_pid
        self.baseline = baseline
        self.adopting = adopting
        self.owned: dict[int, dict] = {}
        self.outside_group: list[int] = []
        self.excluded: dict[int, dict] = {}
        self.seen: set[tuple[int, int | None]] = set()
        self.seen_count = 0
        self.sample: list[dict] = []
        self.verifiable = True
        self.scans = 0

    def scan(self) -> None:
        self.scans += 1
        root = _proc_root()
        try:
            names = _list_proc(root)
        except OSError:
            # Never "none alive": the owned set is kept as it was (I7).
            self.verifiable = False
            return
        me, uid = os.getpid(), os.getuid()
        worker = self.worker_process
        found: dict[int, dict] = {}
        outside: list[int] = []
        verifiable = True
        for name in names:
            if not name.isdigit():
                continue
            pid = int(name)
            if pid in (me, self.anchor_pid):
                continue
            try:
                if os.stat(f"{root}/{name}").st_uid != uid:
                    continue
                stat = _read_stat(f"{root}/{name}/stat")
            except (FileNotFoundError, ProcessLookupError):
                continue  # it exited during the scan
            except (OSError, _NoAnswer):
                # A same-uid pid whose stat cannot be read: undecidable. A
                # recorded entry for it is kept.
                verifiable = False
                if pid in self.owned:
                    found[pid] = self.owned[pid]
                continue
            if stat is None:
                continue
            if pid == worker.pid and (worker.start_ticks is None or stat.start_ticks == worker.start_ticks):
                continue
            adopted = self.adopting and stat.ppid == me and pid not in self.baseline
            if adopted:
                _record_adopted(pid, stat)
            if stat.state in _GONE_STATES:
                continue
            recorded = self.owned.get(pid)
            if recorded is not None and recorded["start_ticks"] != stat.start_ticks:
                recorded = None  # the pid was reused: the new process is not owned
            if stat.pgrp == self.pgid:
                source = "group"
            elif _carries_tag(root, pid, self.tag):
                source = "tag"
            elif adopted:
                source = "adopted"
            elif recorded is not None:
                source = recorded["source"]
            else:
                continue
            cmdline = _read_cmdline(root, pid)
            pattern = daemon_pattern(cmdline)
            if pattern is not None:
                self.excluded.setdefault(pid, {"pid": pid, "start_ticks": stat.start_ticks, "pattern": pattern,
                                               "cmdline": " ".join(cmdline)[:200]})
                continue
            # A recorded entry keeps its first-seen identity and cmdline, but
            # its source says why this scan owns it (Design H). A cmdline first
            # read empty (mid-execve, or on the exit path) takes the first
            # non-empty read, once; a non-empty one is never replaced.
            if recorded is not None:
                entry = dict(recorded, source=source)
                if not entry["cmdline"] and cmdline:
                    entry["cmdline"] = " ".join(cmdline)[:200]
                    self._fill_sample(pid, stat.start_ticks, entry["cmdline"])
            else:
                entry = {"pid": pid, "start_ticks": stat.start_ticks, "source": source,
                         "cmdline": " ".join(cmdline)[:200]}
            found[pid] = entry
            if stat.pgrp != self.pgid:
                outside.append(pid)
            identity = (pid, stat.start_ticks)
            if identity not in self.seen:
                self.seen.add(identity)
                self.seen_count += 1
                if len(self.sample) < OWNED_PROCESS_SAMPLE:
                    self.sample.append(dict(entry))
        self.owned = found
        self.outside_group = sorted(outside)
        self.verifiable = verifiable

    def _fill_sample(self, pid: int, start_ticks: int, cmdline: str) -> None:
        for kept in self.sample:
            if kept["pid"] == pid and kept["start_ticks"] == start_ticks and not kept["cmdline"]:
                kept["cmdline"] = cmdline

    def entries(self) -> list[dict]:
        return [dict(self.owned[pid]) for pid in sorted(self.owned)]

    def excluded_entries(self) -> list[dict]:
        return [dict(self.excluded[pid]) for pid in sorted(self.excluded)]

    def end_outside_group(self) -> None:
        """Identity-checked ``SIGKILL`` of every owned process outside the
        worker's group (the group itself is ended by ``killpg``)."""
        root = _proc_root()
        for pid in list(self.outside_group):
            entry = self.owned.get(pid)
            try:
                stat = _read_stat(root / str(pid) / "stat")
            except _NoAnswer:
                continue
            if entry is None or stat is None or stat.start_ticks != entry["start_ticks"]:
                continue
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)


# -- the supervisor lock -------------------------------------------------------


@contextlib.contextmanager
def _private_supervisor_lock():
    """A supervisor lock for a direct caller that holds none (plan A): held
    in a temporary directory for the launch's duration, so the anchor's
    orphan-lifetime rule works the same way."""
    with tempfile.TemporaryDirectory(prefix="workflow-controller-supervisor-") as directory:
        fd = runtime.open_lock_file(Path(directory), "supervisor.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield Path(directory) / "supervisor.lock"
        finally:
            os.close(fd)


# -- spawning ------------------------------------------------------------------


def _write_task(fd: int, task: str) -> None:
    """Write the task line in full. A worker that exited before reading it
    is not a launch error: its stream says what happened."""
    data = task_line(task)
    with contextlib.suppress(BrokenPipeError):
        while data:
            data = data[os.write(fd, data):]


def _spawn_anchor(write_end: int, worker_process: WorkerProcess, tag: str, supervisor_lock_path: Path,
                  pass_fds: tuple[int, ...]) -> subprocess.Popen:
    """The anchor (``controller.anchor.command``) in its own session, with
    an empty environment and only the stdin write end and ``pass_fds`` (the
    lifecycle lock's descriptor)."""
    return subprocess.Popen(
        anchor_module.command(write_end, worker_process.pid, worker_process.start_ticks, tag,
                              str(supervisor_lock_path)),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env={}, cwd="/", pass_fds=(write_end, *pass_fds),
    )


def _end_anchor(anchor: subprocess.Popen) -> None:
    """``SIGKILL`` and reap the anchor. It is this Controller's own child,
    so its pid cannot have been reused while it is unreaped, and an
    unreaped zombie anchor would look live to every later check."""
    if anchor.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            anchor.kill()
    anchor.wait()


def _catches_sigusr1(pid: int) -> bool:
    """Whether ``pid`` has a ``SIGUSR1`` handler installed (``SigCgt`` in
    ``/proc/<pid>/status``); ``True`` when that cannot be read."""
    try:
        with open(_proc_root() / str(pid) / "status") as fh:
            for line in fh:
                if line.startswith("SigCgt:"):
                    return bool(int(line.split()[1], 16) & (1 << (signal.SIGUSR1 - 1)))
    except (OSError, ValueError, IndexError):
        return True
    return True


def launch(
    task: str,
    *,
    cwd: str | Path,
    permission_mode: str,
    timeout: float | None,
    stdout_path: str | Path,
    stderr_path: str | Path,
    claude_bin: str | None = None,
    pass_fds: Iterable[int] = (),
    on_spawn: Callable[..., None] | None = None,
    on_group_drain: Callable[[int, list[int]], None] | None = None,
    model: str | None = None,
    effort: str | None = None,
    disallowed_tools: Iterable[str] | None = None,
    on_state_change: Callable[[str, dict], None] | None = None,
    ownership_tag: str | None = None,
    supervisor_lock_path: str | Path | None = None,
) -> WorkerResult | DrainDetached:
    """Launch one fresh streaming-input ``claude`` worker against ``cwd``
    and supervise it until it and every process it owns have ended.

    ``permission_mode`` has no default: ``auto`` (the lifecycle-worker
    default, ``controller.job.DEFAULT_PERMISSION_MODE``) against a real
    development repository, ``bypassPermissions`` only against a
    disposable throwaway repository -- the caller states its posture
    explicitly every time.

    ``stdout_path``/``stderr_path`` are files the caller has already
    created, empty. Each is opened ``O_WRONLY | O_APPEND`` and handed to
    the worker as its stdout/stderr. The worker writes its ``stream-json``
    events there itself, so the file is the durable log as it is produced;
    the supervisor only reads it.

    **Spawn** (plan C, step 1). The argv is :func:`worker_argv`'s. The
    worker's stdin is a pipe: the task goes in as one stream-json line
    (:func:`task_line`), and the write end then belongs to the anchor
    (``controller.anchor``), which also inherits ``pass_fds`` (the
    lifecycle lock's descriptor, as the worker does). The worker's
    environment is the Controller's own without ``PYTHONPATH``, with
    ``ownership_tag`` (default: a fresh ``launch-<hex>``) appended to
    :data:`OWNERSHIP_VAR`. For the launch's duration the Controller is a
    child subreaper where ``prctl`` allows. ``supervisor_lock_path`` is the
    job's supervisor lock, which the caller holds; ``None`` makes
    ``launch`` hold a private one. ``on_spawn`` is called as
    ``on_spawn(worker_process, anchor=anchor_process,
    ownership_tag=tag)`` before supervision begins; if it raises
    (``KeyboardInterrupt`` included), the worker's group and the anchor are
    killed and reaped, and the exception propagates unchanged.

    **Supervise** (step 2). ``on_state_change(state, details)`` is called
    whenever ``worker_state`` changes, or the owned processes or what the
    worker waits on change (at most once per scan): ``RUNNING`` while a turn
    is open; ``WAITING`` while none is and ``worker_stream`` reports owned
    work; ``ENDING`` at a quiescent terminal turn (confirmed by a short
    stretch without new bytes, :func:`quiescence_confirm_seconds`), or at a
    harness-contract breach (an overdue wakeup, an unterminated
    ``command_lifecycle`` bracket), with ``ending_offset`` and the
    supervisor facts in ``details``; only then is the anchor asked
    (``SIGUSR1``) to close stdin. ``fire_matched`` wakeups settle after
    :data:`WAKEUP_SETTLE_SECONDS` of idle time. If ``on_state_change``
    raises an ``Exception``, every owned process and the anchor are ended
    and it propagates.

    **Drain** (step 3). Once the worker has exited, ``DRAINING`` lasts while
    an owned process lives, for at most :data:`DRAIN_DETACH_SECONDS`, after
    which ``launch`` returns :class:`DrainDetached` and ends nothing.
    ``on_group_drain(pid, remaining_pids)`` keeps its contract: called once
    when the worker exited and members of its process group remain; if it
    raises, the group is ended and the exception propagates.

    **End** (step 4). ``ENDED``: the anchor is killed and reaped, and the
    stream is classified (``worker_stream.classify``, ``streaming`` mode).

    ``timeout`` ``None`` waits however long the owned lifetime lasts. A
    number is the operator's explicit budget over the whole owned lifetime:
    when it expires the worker's group, every owned process (never a
    recognised daemon) and the anchor are ended, and the result is
    ``INTERRUPTED`` with the worker's real exit status. A
    ``KeyboardInterrupt`` during supervision propagates and ends nothing.

    Raises :class:`~controller.errors.UserOnlyCommandError` before spawning
    anything if ``task`` names one of :data:`USER_ONLY_COMMANDS`, and
    :class:`~controller.errors.WorkerLaunchError` if the worker or its
    anchor could not be started (no worker is left running). Every other
    outcome is the returned :class:`WorkerResult`'s own ``outcome``, or
    :class:`DrainDetached`.
    """
    _assert_not_user_only(task)

    resolved_claude_bin = claude_bin or "claude"
    args = worker_argv(resolved_claude_bin, permission_mode=permission_mode, model=model, effort=effort,
                       disallowed_tools=disallowed_tools)
    tag = ownership_tag or f"launch-{secrets.token_hex(8)}"

    # The worker's environment is the Controller's own, with PYTHONPATH
    # removed (revision 35, local round 34's OPUS-R34-O1): CP1's re-exec
    # assigns PYTHONPATH the immutable-snapshot directory, and this
    # subprocess otherwise inherits it -- without this, every worker (and
    # everything it shells out to inside the target repository) would run
    # with the Controller's own code first on sys.path.
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    inherited_tags = env.get(OWNERSHIP_VAR)
    env[OWNERSHIP_VAR] = f"{inherited_tags}:{tag}" if inherited_tags else tag
    pass_fds = tuple(pass_fds)

    with contextlib.ExitStack() as stack:
        if supervisor_lock_path is None:
            supervisor_lock_path = stack.enter_context(_private_supervisor_lock())
        # The final sweep is registered before the subreaper is entered, so
        # on unwind it runs after the subreaper has been given up (I5).
        reaping = _LaunchExclusions()
        stack.callback(reaping.final_sweep)
        adopting = stack.enter_context(_child_subreaper())
        _reap_adopted()
        baseline = _own_children()
        reaping.baseline = baseline

        read_end, write_end = os.pipe()
        stream_fds: list[int] = []
        try:
            for path in (stdout_path, stderr_path):
                stream_fds.append(os.open(path, os.O_WRONLY | os.O_APPEND))
            with _SPAWN_LOCK:
                proc = subprocess.Popen(
                    args, cwd=cwd, stdin=read_end, stdout=stream_fds[0], stderr=stream_fds[1],
                    start_new_session=True, env=env, pass_fds=pass_fds,
                )
                reaping.add(proc.pid)
        except OSError as exc:
            os.close(write_end)
            raise WorkerLaunchError(
                f"could not launch the Claude worker ({resolved_claude_bin!r}): {exc}",
                evidence={"claude_bin": resolved_claude_bin, "os_error": str(exc)},
            ) from exc
        finally:
            os.close(read_end)
            for fd in stream_fds:
                os.close(fd)

        worker_process = capture_worker_process(proc.pid)
        reaping.add(proc.pid, worker_process.start_ticks)
        try:
            _write_task(write_end, task)
            with _SPAWN_LOCK:
                anchor = _spawn_anchor(write_end, worker_process, tag, Path(supervisor_lock_path), pass_fds)
                reaping.add(anchor.pid)
        except BaseException as exc:
            _kill_process_group(proc.pid)
            proc.wait()
            _reap_killed_children(baseline, worker_process.pgid or proc.pid)
            if isinstance(exc, OSError):
                raise WorkerLaunchError(
                    f"could not start the worker's stdin anchor: {exc}",
                    evidence={"claude_bin": resolved_claude_bin, "os_error": str(exc)},
                ) from exc
            raise
        finally:
            os.close(write_end)

        anchor_process = capture_worker_process(anchor.pid)
        if on_spawn is not None:
            try:
                on_spawn(worker_process, anchor=anchor_process, ownership_tag=tag)
            except BaseException:
                # A process exists, so this is never "the worker never
                # started": end the whole group and the anchor, and reap
                # both, before the original error propagates.
                _kill_process_group(proc.pid)
                proc.wait()
                _end_anchor(anchor)
                _reap_killed_children(baseline | {anchor.pid}, worker_process.pgid or proc.pid)
                raise

        reader = stack.enter_context(open(stdout_path, "rb"))
        supervision = _Supervision(
            proc=proc, anchor=anchor, worker_process=worker_process, reader=reader,
            stdout_path=stdout_path, stderr_path=stderr_path, timeout=timeout,
            on_state_change=on_state_change, on_group_drain=on_group_drain,
            ownership=_Ownership(tag=tag, worker_process=worker_process, anchor_pid=anchor.pid,
                                 baseline=baseline | {anchor.pid}, adopting=adopting),
            reaping=reaping,
        )
        return supervision.run()


class _Supervision:
    """One launch's supervision loop (plan C, steps 2-4)."""

    def __init__(self, *, proc: subprocess.Popen, anchor: subprocess.Popen, worker_process: WorkerProcess,
                 reader, stdout_path, stderr_path, timeout: float | None,
                 on_state_change, on_group_drain, ownership: _Ownership,
                 reaping: _LaunchExclusions | None = None) -> None:
        self.proc = proc
        self.reaping = reaping
        self.anchor = anchor
        self.worker_process = worker_process
        self.reader = reader
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.deadline = None if timeout is None else time.monotonic() + timeout
        self.on_state_change = on_state_change
        self.on_group_drain = on_group_drain
        self.ownership = ownership
        self.stream = worker_stream.WorkerStream()
        self.state = STARTING
        self.turns_reported = 0
        self.waiting_key: object = None
        self.published: object = None
        self.last_bytes_at = time.monotonic()
        self.next_scan = 0.0
        # Supervisor facts (B): only this supervisor knows them.
        self.timed_out = False
        self.ending_offset: int | None = None
        self.wakeup_overdue_declared_at: str | None = None
        self.lifecycle_overdue_declared_at: str | None = None
        self.lifecycle_overdue_command_uuid: str | None = None
        self.settled: list[str] = []
        # Timers, by this supervisor's clock.
        self.idle_task_free: tuple[float, float] | None = None  # (monotonic, wall): no turn, no task
        self.idle_full: float | None = None  # no turn, no task, no bracket
        self.marks_task_free: object = None
        self.marks_full: object = None
        self.stall: dict[str, tuple[object, float]] = {}
        self.matched_at: dict[str, float] = {}
        self.first_seen_wall: dict[str, float] = {}

    # -- the loop ------------------------------------------------------------

    def run(self) -> WorkerResult | DrainDetached:
        while True:
            self._consume()
            self._collect()
            if self.proc.poll() is not None:
                break
            if self._expired():
                return self._interrupt()
            now = time.monotonic()
            self._decide(now)
            self._wait_for_exit(self._poll_interval(now))
        self.proc.wait()
        self._consume()
        return self._drain()

    def _poll_interval(self, now: float) -> float:
        """The next read: sooner while the stream is active, and exactly at
        the end of a quiescence confirmation that is under way."""
        interval = _ACTIVE_POLL_SECONDS if now - self.last_bytes_at < _ACTIVE_STREAM_SECONDS \
            else _SUPERVISE_POLL_SECONDS
        if self.ending_offset is None and self.stream.quiescent():
            interval = min(interval, max(0.01, self.last_bytes_at + quiescence_confirm_seconds(self.stream) - now))
        return interval

    def _collect(self, *, force_full: bool = False) -> None:
        """Record and reap the finished children this Controller holds as
        a subreaper (every tick, in every state). A re-attached supervision
        adopted nothing and has no sweep."""
        if self.reaping is not None:
            self.reaping.collect(force_full=force_full)

    def _wait_for_exit(self, seconds: float) -> None:
        if self.deadline is not None:
            seconds = max(0.0, min(seconds, self.deadline - time.monotonic()))
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=seconds)

    def _consume(self) -> None:
        chunk = self.reader.read()
        if chunk:
            self.stream.feed(chunk)
            self.last_bytes_at = time.monotonic()

    def _expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _sleep(self, seconds: float) -> None:
        if self.deadline is not None:
            seconds = max(0.0, min(seconds, self.deadline - time.monotonic()))
        time.sleep(seconds)

    def _decide(self, now: float) -> None:
        stream = self.stream
        self._track(now)
        if self.ending_offset is not None:
            return  # ENDING: follow the worker to its exit
        if len(stream.turns) > self.turns_reported:
            # A turn opened since the last look -- one that also closed
            # between two reads included -- is reported.
            self.turns_reported = len(stream.turns)
            self._transition(RUNNING)
        if stream.turn_open:
            return
        self._settle(now)
        if stream.quiescent():
            if now - self.last_bytes_at >= quiescence_confirm_seconds(stream):
                self._end_session()
            return
        if self._breached(now):
            self._end_session()
            return
        if stream.has_owned_work():
            self._transition(WAITING)
            if now >= self.next_scan or self._waiting_key() != self.waiting_key:
                self._scan_and_publish()

    # -- timers --------------------------------------------------------------

    def _open_brackets(self) -> list:
        return [b for b in self.stream.brackets if b.state == worker_stream.OPEN]

    def _track(self, now: float) -> None:
        """Restart the idle stretches whenever a turn, task or bracket was
        seen since the last look -- one that opened and closed between two
        polls included -- and each open bracket's stall timer whenever its
        own state changed."""
        stream = self.stream
        tasks_open = bool(stream.open_tasks())
        listed = sum(1 for task in stream.tasks.values() if task.listed)
        marks = (len(stream.turns), listed)
        if stream.turn_open or tasks_open:
            self.idle_task_free = None
        elif marks != self.marks_task_free or self.idle_task_free is None:
            self.idle_task_free = (now, time.time())
        self.marks_task_free = marks

        open_brackets = self._open_brackets()
        full_marks = (marks, len(stream.brackets), len(open_brackets))
        if stream.turn_open or tasks_open or open_brackets:
            self.idle_full = None
        elif full_marks != self.marks_full or self.idle_full is None:
            self.idle_full = now
        self.marks_full = full_marks

        stall = {}
        for bracket in open_brackets:
            turns = bracket.turns_opened
            key = (len(turns), turns and stream.turns[turns[-1]].close_offset is not None)
            previous = self.stall.get(bracket.command_uuid)
            stall[bracket.command_uuid] = previous if previous is not None and previous[0] == key else (key, now)
        self.stall = stall

        for wakeup in stream.wakeups.values():
            self.first_seen_wall.setdefault(wakeup.tool_use_id, time.time())
            if wakeup.state == worker_stream.FIRE_MATCHED:
                self.matched_at.setdefault(wakeup.tool_use_id, now)

    def _settle_left(self, tool_use_id: str, now: float) -> float | None:
        """Seconds left in a ``fire_matched`` wakeup's settle window, or
        ``None`` while its timer is paused (not idle)."""
        if self.idle_full is None:
            return None
        start = max(self.matched_at.get(tool_use_id, now), self.idle_full)
        return max(0.0, WAKEUP_SETTLE_SECONDS - (now - start))

    def _settle(self, now: float) -> None:
        for wakeup in list(self.stream.wakeups.values()):
            if wakeup.state != worker_stream.FIRE_MATCHED:
                continue
            left = self._settle_left(wakeup.tool_use_id, now)
            if left is not None and left <= 0:
                self.settled.append(wakeup.tool_use_id)
                self.stream.settle_wakeups([wakeup.tool_use_id])

    def _breached(self, now: float) -> bool:
        """The two harness-contract breaches (decision 5)."""
        stream = self.stream
        if self.idle_task_free is not None:
            idle_wall = self.idle_task_free[1]
            for wakeup in stream.wakeups.values():
                if wakeup.state != worker_stream.PENDING:
                    continue
                due = wakeup.due
                if due is None:
                    # No stated time to measure from: the latest the harness
                    # could have scheduled it for, from when it was seen.
                    due = self.first_seen_wall[wakeup.tool_use_id] + worker_stream.WAKEUP_MAX_DELAY_SECONDS
                if time.time() - max(due, idle_wall) >= WAKEUP_GRACE_SECONDS:
                    self.wakeup_overdue_declared_at = _utc_now()
                    return True
        if not stream.turn_open and not stream.open_tasks():
            for command_uuid, (_key, since) in self.stall.items():
                if now - since >= COMMAND_LIFECYCLE_GRACE_SECONDS:
                    self.lifecycle_overdue_declared_at = _utc_now()
                    self.lifecycle_overdue_command_uuid = command_uuid
                    return True
        return False

    # -- publishing ----------------------------------------------------------

    def facts(self) -> worker_stream.SupervisorFacts:
        return worker_stream.SupervisorFacts(
            timed_out=self.timed_out, ending_offset=self.ending_offset,
            wakeup_overdue_declared_at=self.wakeup_overdue_declared_at,
            command_lifecycle_overdue_declared_at=self.lifecycle_overdue_declared_at,
            command_lifecycle_overdue_command_uuid=self.lifecycle_overdue_command_uuid,
            settled_wakeups=tuple(self.settled),
        )

    def _waiting_on(self, now: float) -> dict:
        stream = self.stream
        wakeups = []
        for wakeup in stream.wakeups.values():
            if wakeup.state == worker_stream.PENDING:
                wakeups.append({"tool_use_id": wakeup.tool_use_id, "state": wakeup.state,
                                "due_at": worker_stream._iso(wakeup.due)})
            elif wakeup.state == worker_stream.FIRE_MATCHED:
                wakeups.append({"tool_use_id": wakeup.tool_use_id, "state": wakeup.state,
                                "command_uuid": wakeup.matched_by,
                                "settle_seconds_left": self._settle_left(wakeup.tool_use_id, now)})
        brackets = []
        for bracket in self._open_brackets():
            key, since = self.stall.get(bracket.command_uuid, ((0, False), now))
            brackets.append({"command_uuid": bracket.command_uuid, "turn_seen": bool(key[0]),
                             "stalled_seconds": 0.0 if stream.turn_open else round(now - since, 3)})
        return {
            "tasks": [{"task_id": task_id, "description": stream.tasks[task_id].description}
                      for task_id in stream.open_tasks()],
            "wakeups": wakeups,
            "command_lifecycles": brackets,
        }

    def details(self) -> dict:
        now = time.monotonic()
        details = {
            "turns": len(self.stream.turns),
            "waiting_on": self._waiting_on(now),
            "owned_processes": self.ownership.entries(),
            "owned_processes_seen_count": self.ownership.seen_count,
            "excluded_processes": self.ownership.excluded_entries(),
            "scan": "verified" if self.ownership.verifiable else "unverifiable",
        }
        if self.ending_offset is not None:
            details["ending_offset"] = self.ending_offset
            details["supervisor_facts"] = self.facts().to_dict()
        return details

    def _signature(self, details: dict) -> object:
        waiting_on = details["waiting_on"]
        return json.dumps([
            self.state,
            [(p["pid"], p["start_ticks"], p["source"], p["cmdline"]) for p in details["owned_processes"]],
            [p["pid"] for p in details["excluded_processes"]],
            details["scan"],
            [t["task_id"] for t in waiting_on["tasks"]],
            [(w["tool_use_id"], w["state"]) for w in waiting_on["wakeups"]],
            [(b["command_uuid"], b["turn_seen"]) for b in waiting_on["command_lifecycles"]],
            details.get("supervisor_facts"),
        ], sort_keys=True, default=str)

    def _publish(self) -> None:
        details = self.details()
        signature = self._signature(details)
        if signature == self.published:
            return
        self.published = signature
        if self.on_state_change is None:
            return
        try:
            self.on_state_change(self.state, details)
        except Exception:
            self._end_everything()
            raise

    def _waiting_key(self) -> tuple:
        """What the worker waits on, as membership only (no timers)."""
        stream = self.stream
        return (
            tuple(stream.open_tasks()),
            tuple((w.tool_use_id, w.state) for w in stream.wakeups.values()),
            tuple((b.command_uuid, len(b.turns_opened)) for b in self._open_brackets()),
        )

    def _scan_and_publish(self) -> None:
        self.ownership.scan()
        self.next_scan = time.monotonic() + _OWNERSHIP_SCAN_SECONDS
        self.waiting_key = self._waiting_key()
        self._publish()

    def _transition(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._scan_and_publish()

    # -- ending ----------------------------------------------------------------

    def _end_session(self) -> None:
        """``ENDING``: flush it with ``ending_offset`` first (I3), then ask
        the anchor to release stdin."""
        self.ending_offset = self.stream.offset
        self._transition(ENDING)
        self._release_stdin()

    def _release_stdin(self) -> None:
        """Ask the anchor (``SIGUSR1``) to close the worker's stdin, once
        it has installed its handler."""
        deadline = time.monotonic() + _ANCHOR_READY_SECONDS
        while self.anchor.poll() is None and not _catches_sigusr1(self.anchor.pid) \
                and time.monotonic() < deadline:
            time.sleep(0.01)
        if self.anchor.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                self.anchor.send_signal(signal.SIGUSR1)

    def _end_everything(self) -> None:
        """End the worker's group, every owned process outside it and the
        anchor, and reap what can be reaped (a timeout, or a failed flush)."""
        if self.proc.poll() is None:
            _kill_process_group(self.proc.pid)
            self.proc.wait()
        else:
            _end_drained_group(self.worker_process)
        self.ownership.end_outside_group()
        _end_anchor(self.anchor)
        _reap_killed_children(self.ownership.baseline, self.ownership.pgid, self.ownership.outside_group)

    def _interrupt(self) -> WorkerResult:
        self.timed_out = True
        self.ownership.scan()
        self._end_everything()
        self._collect(force_full=True)
        return self._finish()

    def _drain(self) -> WorkerResult | DrainDetached:
        worker_process = self.worker_process
        # The group-emptiness test applies no RECOGNISED_DAEMONS exclusion:
        # it assumes (H9, P2) that a recognised daemon never stays in the
        # worker's group -- the harness kills attached, non-``setsid``
        # descendants at exit, and the named daemons ``setsid`` themselves.
        # A group-resident one would hold DRAINING until the detach bound.
        empty, members = _group_members(worker_process)
        if not empty and self.on_group_drain is not None:
            try:
                self.on_group_drain(self.proc.pid, members)
            except BaseException:
                _end_drained_group(worker_process)
                self.ownership.scan()
                self.ownership.end_outside_group()
                _end_anchor(self.anchor)
                _reap_killed_children(self.ownership.baseline, self.ownership.pgid,
                                      self.ownership.outside_group)
                raise
        self.ownership.scan()
        self.next_scan = time.monotonic() + _OWNERSHIP_SCAN_SECONDS
        if empty and not self.ownership.outside_group and self.ownership.verifiable:
            return self._finish()
        self.state = DRAINING
        self._publish()
        started = time.monotonic()
        while True:
            if self._expired():
                return self._interrupt()
            if time.monotonic() - started >= DRAIN_DETACH_SECONDS:
                return self._detach()
            self._sleep(_DRAIN_POLL_SECONDS)
            self._collect()
            empty = _group_members(worker_process)[0]
            if empty or time.monotonic() >= self.next_scan:
                self._scan_and_publish()
            if empty and not self.ownership.outside_group and self.ownership.verifiable:
                return self._finish()

    def _detach(self) -> DrainDetached:
        """Stop waiting and end nothing: the anchor keeps the lifecycle lock
        (plan C, step 3)."""
        self._collect(force_full=True)
        self.ownership.scan()
        return DrainDetached(
            drain_detached_at=_utc_now(),
            remaining=tuple(self.ownership.entries()),
            returncode=self.proc.returncode,
            worker_state=self.details(),
            supervisor_facts=self.facts().to_dict(),
        )

    def _finish(self) -> WorkerResult:
        _end_anchor(self.anchor)
        self._collect(force_full=True)
        self.state = ENDED
        self._publish()
        with open(self.stdout_path, "rb") as fh:
            stdout_bytes = fh.read()
        outcome, terminal_result, diagnosis = worker_stream.classify(
            stdout_bytes, mode=worker_stream.STREAMING, facts=self.facts(), returncode=self.proc.returncode,
        )
        return WorkerResult(
            outcome=outcome,
            returncode=self.proc.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=_read_stream(self.stderr_path),
            raw_json=terminal_result,
            stream_diagnosis=diagnosis,
            owned_processes_seen={"count": self.ownership.seen_count,
                                  "sample": [dict(entry) for entry in self.ownership.sample]},
            **_extract_fields(terminal_result),
        )


# ---------------------------------------------------------------------------
# Re-attach (worker-lifecycle-ownership CP5, plan E): supervision of a
# worker a lost Controller launched, resumed by `resume` from the record.
# ---------------------------------------------------------------------------


def identity_alive(pid: object, start_ticks: object) -> bool | None:
    """Whether the recorded process ``(pid, start_ticks)`` still runs:
    ``False`` when it is gone, a zombie, or the pid now names another
    process (its start ticks differ); ``None`` when ``/proc`` cannot answer
    (never read as gone). A ``start_ticks`` of ``None`` matches any start."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        stat = _read_stat(_proc_root() / str(pid) / "stat")
    except _NoAnswer:
        return None
    if stat is None or stat.state in _GONE_STATES:
        return False
    return start_ticks is None or stat.start_ticks == start_ticks


def end_recorded_process(identity: Mapping | None, *, settle_seconds: float = _DRAIN_KILL_SETTLE_SECONDS) -> bool:
    """Identity-checked ``SIGKILL`` of a recorded process this Controller
    did not spawn (a lost Controller's stdin anchor), then wait up to
    ``settle_seconds`` until it is gone (a zombie counts as gone: its
    descriptors, the lifecycle lock's included, are closed by then).
    Returns whether it is gone."""
    if not isinstance(identity, Mapping):
        return True
    pid, start_ticks = identity.get("pid"), identity.get("start_ticks")
    if identity_alive(pid, start_ticks):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + settle_seconds
    while identity_alive(pid, start_ticks) is not False:
        if time.monotonic() >= deadline:
            return False
        time.sleep(_DRAIN_KILL_POLL_SECONDS)
    return True


class _FollowedProcess:
    """A recorded process a re-attaching supervisor follows but did not
    spawn (the worker, the anchor): the ``subprocess.Popen`` surface
    :class:`_Supervision` uses, answered from the recorded ``(pid,
    start_ticks)``. Its exit status cannot be read, so ``returncode`` is
    always ``None`` (an unknown exit status, B's note under the table);
    ``poll()`` answers ``None`` while it runs and :data:`_FOLLOWED_GONE`
    once it is gone. An unanswerable ``/proc`` read keeps it running (I7)."""

    returncode = None

    def __init__(self, identity: Mapping | None) -> None:
        identity = identity if isinstance(identity, Mapping) else {}
        pid = identity.get("pid")
        self.pid = pid if isinstance(pid, int) and not isinstance(pid, bool) else -1
        self.start_ticks = identity.get("start_ticks")

    def _running(self) -> bool:
        return identity_alive(self.pid, self.start_ticks) is not False

    def poll(self):
        return None if self._running() else _FOLLOWED_GONE

    def wait(self, timeout: float | None = None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._running():
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(f"pid {self.pid}", timeout)
            time.sleep(_DRAIN_KILL_POLL_SECONDS if deadline is None
                       else max(0.0, min(_DRAIN_KILL_POLL_SECONDS, deadline - time.monotonic())))
        return None

    def send_signal(self, sig: int) -> None:
        if identity_alive(self.pid, self.start_ticks):
            os.kill(self.pid, sig)

    def kill(self) -> None:
        self.send_signal(signal.SIGKILL)


#: What :meth:`_FollowedProcess.poll` answers for a followed process that is
#: gone (anything but ``None``; its exit status is unknown).
_FOLLOWED_GONE = "gone"


def replay_stream(data: bytes, settled_wakeups: Iterable[str] = ()) -> worker_stream.WorkerStream:
    """The stream state a re-attaching supervisor starts from (plan E,
    step 2): every complete line of ``data`` fed to a fresh
    ``worker_stream.WorkerStream``, then the persisted ``settled_wakeups``
    fact applied. A pure function of the lines and the fact, so it is the
    state live supervision reached at the same byte, bracket by bracket;
    a trailing partial line stays buffered until the rest arrives."""
    stream = worker_stream.WorkerStream()
    stream.feed(data)
    settled = [tool_use_id for tool_use_id in settled_wakeups if tool_use_id]
    if settled:
        stream.settle_wakeups(settled)
    return stream


def _recorded_worker_process(identity: Mapping | None) -> "WorkerProcess":
    identity = identity if isinstance(identity, Mapping) else {}
    fields = {field.name: identity.get(field.name) for field in dataclasses.fields(WorkerProcess)}
    if not isinstance(fields["pid"], int) or isinstance(fields["pid"], bool):
        fields["pid"] = -1
    return WorkerProcess(**fields)


def _recorded_ownership(*, tag: str | None, worker_process: "WorkerProcess", anchor_pid: int | None,
                        owned_processes: Iterable[Mapping] = (), seen_count: int | None = None) -> _Ownership:
    """The owned-process set a Controller that did not launch the worker
    scans (plan C step 3, E): the worker's recorded group, the tag, and the
    record's ``owned_processes`` entries whose identity still matches --
    never adoption, which only the original supervisor had."""
    ownership = _Ownership(tag=tag or "", worker_process=worker_process, anchor_pid=anchor_pid,
                           baseline=set(), adopting=False)
    for entry in owned_processes:
        if not isinstance(entry, Mapping):
            continue
        pid = entry.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            continue
        recorded = {"pid": pid, "start_ticks": entry.get("start_ticks"), "source": entry.get("source") or "recorded",
                    "cmdline": entry.get("cmdline") or ""}
        ownership.owned[pid] = recorded
        ownership.seen.add((pid, recorded["start_ticks"]))
        if len(ownership.sample) < OWNED_PROCESS_SAMPLE:
            ownership.sample.append(dict(recorded))
    ownership.seen_count = seen_count if isinstance(seen_count, int) and not isinstance(seen_count, bool) \
        else len(ownership.seen)
    return ownership


def scan_recorded_owned_processes(*, ownership_tag: str | None, worker_process: Mapping | None,
                                  anchor: Mapping | None, owned_processes: Iterable[Mapping] = ()
                                  ) -> tuple[list[dict], bool]:
    """One ownership scan for a job record, by a Controller that is not
    supervising it (``resume``'s and ``--abandon``'s ownership hold, and
    ``pending_reconciliation_jobs``' report; plan E): ``(live owned
    entries, verifiable)``. The owned set is C step 3's without adoption:
    the worker's recorded process group (its leader excluded), the job's
    tag, and every recorded entry whose ``(pid, start_ticks)`` still
    matches; recognised daemons and the anchor are never owned. ``verifiable``
    is ``False`` when ``/proc`` could not be listed or a same-uid ``stat``
    could not be read -- never "none alive" (I7)."""
    anchor_pid = anchor.get("pid") if isinstance(anchor, Mapping) else None
    ownership = _recorded_ownership(
        tag=ownership_tag, worker_process=_recorded_worker_process(worker_process),
        anchor_pid=anchor_pid if isinstance(anchor_pid, int) else None, owned_processes=owned_processes,
    )
    ownership.scan()
    return ownership.entries(), ownership.verifiable


class _ReattachedSupervision(_Supervision):
    """:class:`_Supervision` resumed by a Controller that did not launch
    the worker (plan E). It follows the recorded worker and anchor by
    identity, never ends owned work on a failed flush (the record already
    names every identity, I3, and the anchor keeps the lifecycle lock), and
    -- with ``classify`` false, for a session no supervisor ended -- only
    drains and ends the anchor, leaving the classification to ``resume``'s
    fail-closed reconciliation."""

    def __init__(self, *, classify: bool, **kwargs) -> None:
        super().__init__(**kwargs)
        self.classify = classify

    def _end_everything(self) -> None:
        """Nothing is ended: see the class docstring."""

    def _finish(self):
        if self.classify:
            return super()._finish()
        _end_anchor(self.anchor)
        return None


def reattach(
    *,
    stdout_path: str | Path,
    stderr_path: str | Path,
    worker_process: Mapping,
    anchor: Mapping | None,
    ownership_tag: str | None,
    state: str | None = None,
    owned_processes: Iterable[Mapping] = (),
    owned_processes_seen_count: int | None = None,
    ending_offset: int | None = None,
    wakeup_overdue_declared_at: str | None = None,
    command_lifecycle_overdue_declared_at: str | None = None,
    command_lifecycle_overdue_command_uuid: str | None = None,
    settled_wakeups: Iterable[str] = (),
    on_state_change: Callable[[str, dict], None] | None = None,
    classify: bool = True,
) -> "WorkerResult | DrainDetached | None":
    """Resume supervision of a worker a lost Controller launched (plan E,
    "Re-attach"), from its job record. Never launches anything (I1).

    The stream state is rebuilt by replaying ``stdout_path`` from the start
    with the persisted supervisor facts (:func:`replay_stream`); every
    ``command_lifecycle`` bracket open at the end of the replay is open
    again, with its ``command_uuid``. Only the lost Controller's timers are
    not recovered: each open bracket's stall timer and each ``fire_matched``
    wakeup's settle timer start now, so a re-attached supervisor may settle
    or declare later than the lost one would have, never earlier.

    Then, as :func:`launch` does from the same state (design C): while the
    recorded worker (``worker_process``, followed by its ``pid`` and
    ``start_ticks``: its exit status is unknown) runs, supervise it --
    ``RUNNING``/``WAITING``/``ENDING``, the recorded anchor signalled to
    release stdin at a quiescent turn or a breach; with ``ending_offset``
    already recorded (a supervisor ended the session), follow the worker to
    its exit, re-sending the release in case the lost Controller died
    before sending it. Once it is gone, drain the owned processes (the
    recorded group, the tag, and the recorded ``owned_processes``; this
    Controller adopted nothing) for at most :data:`DRAIN_DETACH_SECONDS`,
    returning :class:`DrainDetached` and ending nothing if any outlives the
    bound. Otherwise end the recorded anchor (identity-checked) and, with
    ``classify``, publish ``ENDED`` and return the stream's classification
    with an unknown exit status; without it (a session no supervisor ended:
    plan E's "no re-attach"), return ``None``.

    ``on_state_change`` is :func:`launch`'s; if it raises, nothing is ended
    and the exception propagates."""
    worker = _recorded_worker_process(worker_process)
    anchor_proc = _FollowedProcess(anchor)
    ownership = _recorded_ownership(
        tag=ownership_tag, worker_process=worker, anchor_pid=anchor_proc.pid if anchor_proc.pid > 0 else None,
        owned_processes=owned_processes, seen_count=owned_processes_seen_count,
    )
    with open(stdout_path, "rb") as reader:
        supervision = _ReattachedSupervision(
            classify=classify, proc=_FollowedProcess(worker_process), anchor=anchor_proc, worker_process=worker,
            reader=reader, stdout_path=stdout_path, stderr_path=stderr_path, timeout=None,
            on_state_change=on_state_change, on_group_drain=None, ownership=ownership,
        )
        settled = [item for item in settled_wakeups if isinstance(item, str) and item]
        supervision.stream = replay_stream(reader.read(), settled)
        supervision.settled = list(settled)
        supervision.ending_offset = ending_offset
        supervision.wakeup_overdue_declared_at = wakeup_overdue_declared_at
        supervision.lifecycle_overdue_declared_at = command_lifecycle_overdue_declared_at
        supervision.lifecycle_overdue_command_uuid = command_lifecycle_overdue_command_uuid
        stream = supervision.stream
        supervision.turns_reported = len(stream.turns) - (1 if stream.turn_open else 0)
        supervision.state = state if state in (STARTING, RUNNING, WAITING, ENDING, DRAINING, ENDED) else STARTING
        if ending_offset is not None and supervision.proc.poll() is None:
            supervision._release_stdin()
        return supervision.run()


# ---------------------------------------------------------------------------
# The worker's process identity (automatic-lifecycle-orchestration CP5).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WorkerProcess:
    """What identifies one launched worker's process group, and the
    context its numbers mean something in. Each field is ``None`` where it
    could not be read.

    ``start_ticks`` is ``/proc/<pid>/stat`` field 22, read before the
    worker can have been reaped. ``boot_id`` is
    ``/proc/sys/kernel/random/boot_id``; ``pid_namespace`` the
    Controller's own ``/proc/self/ns/pid`` (the namespace ``pid``/``pgid``
    are numbered in); ``hostname`` ``socket.gethostname()``; ``machine_id``
    the trimmed ``/etc/machine-id``."""

    pid: int
    pgid: int | None
    start_ticks: int | None
    boot_id: str | None
    pid_namespace: str | None
    hostname: str | None
    machine_id: str | None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


#: The four context fields :func:`read_process_context` returns, recorded
#: beside ``pid``/``pgid``/``start_ticks`` in every ``worker_process``.
CONTEXT_FIELDS = ("boot_id", "pid_namespace", "hostname", "machine_id")


def _read_trimmed(path: str) -> str | None:
    try:
        with open(path) as fh:
            value = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value or None


def read_process_context() -> dict:
    """The current ``{boot_id, pid_namespace, hostname, machine_id}`` --
    the one reader both the spawn-time capture and every later liveness
    check call, so tests can change the context without touching ``/proc``.
    ``machine_id`` is ``None`` when ``/etc/machine-id`` is unreadable,
    empty, or reads ``uninitialized``."""
    machine_id = _read_trimmed("/etc/machine-id")
    if machine_id == "uninitialized":
        machine_id = None
    try:
        hostname = socket.gethostname() or None
    except OSError:
        hostname = None
    return {
        "boot_id": _read_trimmed("/proc/sys/kernel/random/boot_id"),
        "pid_namespace": lock.read_pid_namespace(),
        "hostname": hostname,
        "machine_id": machine_id,
    }


def _proc_root() -> Path:
    """The ``/proc`` the process test reads (a patchable seam, so the
    zombie and member-scan cases can also run over fixture ``stat``
    files)."""
    return Path("/proc")


def _killpg(pgid: int, sig: int) -> None:
    """``os.killpg`` (a patchable seam for the ``killpg`` form)."""
    os.killpg(pgid, sig)


class _NoAnswer(Exception):
    """The ``/proc`` form cannot answer: a listing or read that failed for
    a reason other than the entry vanishing, or a line that does not
    parse."""


@dataclasses.dataclass(frozen=True)
class _ProcStat:
    state: str
    pgrp: int
    start_ticks: int
    session: int
    ppid: int = 0


def _parse_stat(text: str) -> _ProcStat:
    """Fields are counted after the line's **last** ``)``: ``comm`` (field
    2) may itself contain spaces and parentheses. Field 3 is the state,
    field 4 ``ppid``, field 5 ``pgrp``, field 6 ``session``, field 22
    ``starttime``."""
    close = text.rfind(")")
    if close < 0:
        raise _NoAnswer(f"stat line does not parse: {text!r}")
    rest = text[close + 1:].split()
    if len(rest) < 20:
        raise _NoAnswer(f"stat line has too few fields: {text!r}")
    try:
        return _ProcStat(state=rest[0], pgrp=int(rest[2]), start_ticks=int(rest[19]), session=int(rest[3]),
                         ppid=int(rest[1]))
    except ValueError:
        raise _NoAnswer(f"stat line does not parse: {text!r}") from None


def _read_stat(path: Path) -> _ProcStat | None:
    """``None`` when the entry vanished (the process exited); ``_NoAnswer``
    for any other read failure or an unparseable line -- never silently
    skipped (round 1's O1 of the manual external plan review)."""
    try:
        with open(path) as fh:
            text = fh.read()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise _NoAnswer(f"{path} could not be read: {exc}") from exc
    return _parse_stat(text)


def _proc_numbers_own_namespace(root: Path) -> bool:
    """Whether ``root`` numbers processes in the Controller's own pid
    namespace: its ``self`` link names ``os.getpid()``."""
    try:
        return os.readlink(root / "self") == str(os.getpid())
    except OSError:
        return False


def capture_worker_process(pid: int) -> WorkerProcess:
    """The spawn-time :class:`WorkerProcess` for ``pid``. The worker cannot
    have been reaped yet (the wait has not begun), so ``/proc/<pid>/stat``
    is readable whenever a ``/proc`` numbering this namespace is."""
    try:
        pgid: int | None = os.getpgid(pid)
    except OSError:
        pgid = None
    start_ticks = None
    root = _proc_root()
    if _proc_numbers_own_namespace(root):
        try:
            stat = _read_stat(root / str(pid) / "stat")
        except _NoAnswer:
            stat = None
        start_ticks = stat.start_ticks if stat is not None else None
    return WorkerProcess(pid=pid, pgid=pgid, start_ticks=start_ticks, **read_process_context())


# ---------------------------------------------------------------------------
# The liveness verdict.
# ---------------------------------------------------------------------------

#: The three verdicts :func:`classify_worker_liveness` returns.
ACTIVE = "active"
INACTIVE = "inactive"
UNVERIFIABLE = "unverifiable"

#: The process test's three answers.
LIVE = "live"
NOT_LIVE = "not live"
POSSIBLY_LIVE = "possibly live"

#: ``stat`` states that are not running: zombie and dead (``x`` is the
#: dead state's spelling on some kernels).
_NOT_RUNNING_STATES = frozenset({"Z", "X", "x"})


@dataclasses.dataclass(frozen=True)
class ProcessAnswer:
    """The process test's answer, and which case produced it: ``basis`` is
    one of ``leader_running`` (the recorded leader, matched by its
    ``start_ticks``), ``member_scan`` (running members of the recorded
    group, listed in ``members``, with no matched leader), ``leader_pid_
    reused``, ``no_running_member``, ``killpg_no_such_group`` or
    ``killpg_possibly_live``."""

    answer: str
    basis: str
    members: tuple[int, ...] = ()


def _is_running(root: Path, pid: int, stat: _ProcStat) -> bool:
    """A process is running iff its state is neither ``Z`` nor ``X`` --
    or, when the process-level state is ``Z``/``X``, iff some thread in
    ``/proc/<pid>/task/`` is: a multithreaded process whose leader thread
    exited reads ``Z`` while another thread still runs (round 1's O1 of
    the manual external plan review)."""
    if stat.state not in _NOT_RUNNING_STATES:
        return True
    task_dir = root / str(pid) / "task"
    try:
        tids = os.listdir(task_dir)
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError as exc:
        raise _NoAnswer(f"{task_dir} could not be listed: {exc}") from exc
    for tid in tids:
        if not tid.isdigit():
            continue
        task_stat = _read_stat(task_dir / tid / "stat")
        if task_stat is not None and task_stat.state not in _NOT_RUNNING_STATES:
            return True
    return False


def _proc_scan(root: Path, pid: int, pgid: int, start_ticks: int | None) -> ProcessAnswer:
    leader = _read_stat(root / str(pid) / "stat")
    if leader is not None and start_ticks is not None:
        if leader.start_ticks != start_ticks:
            # Linux never reuses a pid that still names a process group,
            # zombie members included: the recorded group has ended.
            return ProcessAnswer(NOT_LIVE, "leader_pid_reused")
        if _is_running(root, pid, leader):
            return ProcessAnswer(LIVE, "leader_running")
    # The leader is absent, a zombie, or has no recorded start_ticks: the
    # group's running members decide. Members can outlive their leader.
    try:
        names = os.listdir(root)
    except OSError as exc:
        raise _NoAnswer(f"{root} could not be listed: {exc}") from exc
    members = []
    for name in names:
        if not name.isdigit():
            continue
        stat = _read_stat(root / name / "stat")
        if stat is None or stat.pgrp != pgid:
            continue  # vanished during the scan (it exited), or another group
        if _is_running(root, int(name), stat):
            members.append(int(name))
    if members:
        return ProcessAnswer(LIVE, "member_scan", tuple(sorted(members)))
    return ProcessAnswer(NOT_LIVE, "no_running_member")


def _proc_form(pid: int, pgid: int, start_ticks: int | None) -> ProcessAnswer | None:
    """The ``/proc`` form, or ``None`` where it is unavailable (``/proc``
    does not number the Controller's own namespace) or has no answer. A
    ``not live`` from the member scan is confirmed by one complete rescan
    before it is returned."""
    root = _proc_root()
    if not _proc_numbers_own_namespace(root):
        return None
    try:
        answer = _proc_scan(root, pid, pgid, start_ticks)
        if answer.basis == "no_running_member":
            answer = _proc_scan(root, pid, pgid, start_ticks)
    except _NoAnswer:
        return None
    return answer


def _killpg_form(pgid: int) -> ProcessAnswer:
    """``os.killpg(pgid, 0)``: ``ProcessLookupError`` is ``not live`` (no
    process of that group exists, zombie or not, in this namespace);
    success or any other error is only ``possibly live`` -- ``killpg``
    cannot tell a zombie from a running process."""
    try:
        _killpg(pgid, 0)
    except ProcessLookupError:
        return ProcessAnswer(NOT_LIVE, "killpg_no_such_group")
    except OSError:
        return ProcessAnswer(POSSIBLY_LIVE, "killpg_possibly_live")
    return ProcessAnswer(POSSIBLY_LIVE, "killpg_possibly_live")


def process_test(pid: int, pgid: int, start_ticks: int | None) -> ProcessAnswer:
    """``live``/``not live``/``possibly live`` for the recorded group: the
    ``/proc`` form whenever it can answer, the ``killpg`` form otherwise.
    Only the ``/proc`` form can answer ``live``; a zombie never counts as
    running."""
    answer = _proc_form(pid, pgid, start_ticks)
    if answer is not None:
        return answer
    return _killpg_form(pgid)


@dataclasses.dataclass(frozen=True)
class LivenessAssessment:
    """:func:`assess_worker_liveness`'s full answer: the ``verdict``, the
    verdict ``row`` that decided it (``same_boot``, ``other_boot_same_host``,
    ``other_host``, ``no_boot_identity`` or ``malformed``), a ``reason``
    sentence, the process test's answer where it ran (``None`` otherwise),
    and both contexts."""

    verdict: str
    row: str
    reason: str
    process: ProcessAnswer | None
    recorded: dict
    current: dict

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "row": self.row,
            "reason": self.reason,
            "process_answer": None if self.process is None else self.process.answer,
            "process_basis": None if self.process is None else self.process.basis,
            "members": [] if self.process is None else list(self.process.members),
            "recorded": dict(self.recorded),
            "current": dict(self.current),
        }


def _equal(a: object, b: object) -> bool:
    """Both read and the same; a ``None`` on either side is never equal."""
    return a is not None and b is not None and a == b


def _valid_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 1


def assess_worker_liveness(worker_process: Mapping) -> LivenessAssessment:
    """The boot-keyed liveness verdict for a recorded ``worker_process``,
    compared against :func:`read_process_context` now:

    1. **Same boot** (equal ``boot_id``s): with equal ``pid_namespace``s
       the process test decides (``live`` -> ``active``, ``not live`` ->
       ``inactive``, ``possibly live`` -> ``unverifiable``), whatever the
       hostname; otherwise ``unverifiable``.
    2. **Another boot of the same host** (both ``boot_id``s read and
       different; hostnames *and* ``machine_id``s both read and equal):
       ``inactive`` -- the recorded boot is over.
    3. **Another or unidentifiable host** (both ``boot_id``s read and
       different, not the same host): ``unverifiable``.
    4. **No boot identity** (``boot_id`` ``None`` on either side): on the
       same host, with ``pid_namespace``s both read and equal or both
       unread, the process test runs and ``not live`` is ``inactive``;
       any other answer, a one-sided unread ``pid_namespace`` (round 1's
       O4 of the manual external plan review) or any other combination is
       ``unverifiable``.

    A ``worker_process`` whose ``pid``/``pgid`` are not usable numbers is
    ``unverifiable`` (row ``malformed``) -- never guessed ``inactive``."""
    recorded_context = {field: worker_process.get(field) for field in CONTEXT_FIELDS}
    recorded = {
        "pid": worker_process.get("pid"), "pgid": worker_process.get("pgid"),
        "start_ticks": worker_process.get("start_ticks"), **recorded_context,
    }
    current = read_process_context()

    def verdict(value: str, row: str, reason: str, process: ProcessAnswer | None = None) -> LivenessAssessment:
        return LivenessAssessment(verdict=value, row=row, reason=reason, process=process,
                                  recorded=recorded, current=current)

    pid, pgid, start_ticks = recorded["pid"], recorded["pgid"], recorded["start_ticks"]
    if not _valid_number(pid) or not _valid_number(pgid) or not (
        start_ticks is None or (isinstance(start_ticks, int) and not isinstance(start_ticks, bool))
    ):
        return verdict(UNVERIFIABLE, "malformed",
                       f"the recorded worker_process carries no usable pid/pgid/start_ticks "
                       f"(pid={pid!r}, pgid={pgid!r}, start_ticks={start_ticks!r})")

    same_host = (_equal(recorded["hostname"], current["hostname"])
                 and _equal(recorded["machine_id"], current["machine_id"]))
    recorded_boot, current_boot = recorded["boot_id"], current["boot_id"]

    if recorded_boot is not None and current_boot is not None:
        if recorded_boot == current_boot:
            if not _equal(recorded["pid_namespace"], current["pid_namespace"]):
                return verdict(UNVERIFIABLE, "same_boot",
                               "same boot, but the recorded pid namespace differs from this one or "
                               "could not be read on one side -- the recorded numbers may belong to "
                               "another container")
            process = process_test(pid, pgid, start_ticks)
            if process.answer == LIVE:
                return verdict(ACTIVE, "same_boot", "a running member of the recorded process group "
                               "is observed in this boot and pid namespace", process)
            if process.answer == NOT_LIVE:
                return verdict(INACTIVE, "same_boot", "no running member of the recorded process "
                               "group exists in this boot and pid namespace", process)
            return verdict(UNVERIFIABLE, "same_boot", "only killpg could be asked, and it cannot tell "
                           "a running member of the recorded group from a zombie", process)
        if same_host:
            return verdict(INACTIVE, "other_boot_same_host",
                           "the recorded boot of this host is over, so no process from it can exist")
        return verdict(UNVERIFIABLE, "other_host",
                       "the record comes from another host, or from a host whose identity (hostname "
                       "and machine id together) cannot be established here")

    # Row 4: no boot identity on one side or both.
    if not same_host:
        return verdict(UNVERIFIABLE, "no_boot_identity",
                       "no boot identity, and the host (hostname and machine id together) cannot be "
                       "shown to be this one")
    recorded_ns, current_ns = recorded["pid_namespace"], current["pid_namespace"]
    if (recorded_ns is None) != (current_ns is None) or (
        recorded_ns is not None and recorded_ns != current_ns
    ):
        return verdict(UNVERIFIABLE, "no_boot_identity",
                       "no boot identity, and the recorded pid namespace differs from this one or "
                       "could not be read on one side")
    process = process_test(pid, pgid, start_ticks)
    if process.answer == NOT_LIVE:
        return verdict(INACTIVE, "no_boot_identity", "no running member of the recorded process "
                       "group exists now, whichever boot recorded it", process)
    return verdict(UNVERIFIABLE, "no_boot_identity", "without a boot identity, a live member of the "
                   "recorded group may be a later boot's reuse of the same number", process)


def classify_worker_liveness(worker_process: Mapping) -> str:
    """``"active"``, ``"inactive"`` or ``"unverifiable"`` for a recorded
    ``worker_process`` (:func:`assess_worker_liveness`'s verdict).
    ``active`` is the only verdict that positively observes a *running*
    member of the recorded group in this boot and pid namespace."""
    return assess_worker_liveness(worker_process).verdict
