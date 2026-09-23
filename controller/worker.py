"""Fresh Claude worker abstraction (capability 4,
``docs/ACTIVE_MILESTONE.md``).

``launch(task, *, cwd, permission_mode, timeout, claude_bin=None,
pass_fds=(), on_spawn=None, model=None, effort=None, disallowed_tools=None)
-> WorkerResult`` is the whole launch entry point: it runs exactly the
mechanism ``docs/ACTIVE_MILESTONE.md`` records as proven --

    claude -p "<task>" --output-format json --permission-mode <mode> \\
        [--model <model>] [--effort <effort>] \\
        [--disallowedTools <tool>,<tool>] < /dev/null

against ``cwd``, waits for it to finish (or times out), and classifies the
result into exactly one of four outcomes. It never inspects, repairs, or
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
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import socket
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from controller import lock
from controller.errors import UserOnlyCommandError, WorkerLaunchError

#: The four outcomes :func:`launch` classifies a completed (or interrupted)
#: worker run into. Fixed classification order, per the plan's table:
#: ``INTERRUPTED`` is checked first (a timeout, or termination by signal --
#: a negative return code), then ``FAILURE`` (non-zero exit, or exit 0 with
#: parsed ``is_error: true``), then ``AMBIGUOUS`` (exit 0 but stdout is not
#: a single parseable JSON object carrying the fields the Controller
#: needs), and only then ``SUCCESS``.
SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
AMBIGUOUS = "AMBIGUOUS"
INTERRUPTED = "INTERRUPTED"

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

#: The two required fields a parsed worker-stdout JSON object must carry
#: for its result to be decidable at all (``docs/ACTIVE_MILESTONE.md``'s
#: own field list; every other field is optional and defaults to
#: ``None`` when absent).
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
    ``stdout``/``stderr`` are the worker's raw, undecoded-error-tolerant
    text streams, preserved verbatim so a human can read exactly what the
    worker said -- a later checkpoint (CP6) is what writes them to
    ``.controller/jobs/<job_id>/worker.std{out,err}``, this module never
    writes any file itself. ``raw_json`` is the full parsed JSON result
    object when one was decidable (``None`` otherwise); the eight named
    fields below are that same object's own fields, extracted for
    convenience and ``None`` wherever the object was undecidable or the
    field was absent.
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


def _parse_worker_stdout(stdout: str) -> dict | None:
    """``REQ-26``: delimit the worker-stdout JSON classification's
    candidate span over the worker's own byte stream, rather than leaving
    it implicit.

    - ``stdout`` reaches this function already decoded with
      ``errors="replace"`` (:func:`launch`'s own
      ``subprocess.Popen(..., text=True, errors="replace")``), so a
      non-UTF-8 byte in the worker's output degrades to the U+FFFD
      replacement character rather than raising -- it then simply fails
      to parse as JSON below, exactly like any other malformed byte
      content, rather than crashing the Controller mid-classification.
    - the candidate span is ``stdout`` with **exactly one** trailing
      newline (``\\r\\n`` or ``\\n``) stripped -- ``--output-format json``
      emits its JSON body as a single terminated line, and this is the
      only trim this function performs. It is never a general
      ``.strip()``: leading or embedded whitespace inside a malformed
      body is preserved and still fails to parse, rather than being
      trimmed into something that accidentally does.
    - the whole candidate span is parsed as **exactly one** JSON
      document. Python's own ``json.loads`` already refuses trailing
      "extra data" after a complete value, so two concatenated JSON
      documents (or one valid document followed by trailing prose) fail
      to parse as a single candidate and fall through to ``None`` here --
      classified :data:`AMBIGUOUS` by the caller -- rather than either
      document being silently selected.

    Returns the parsed object only when it is a JSON *object* (a Python
    ``dict``); any other JSON value (a list, string, number, boolean or
    ``null``) is not the shape the Controller needs and also returns
    ``None``.
    """
    if stdout.endswith("\r\n"):
        candidate = stdout[:-2]
    elif stdout.endswith("\n"):
        candidate = stdout[:-1]
    else:
        candidate = stdout
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _classify(returncode: int | None, parsed: dict | None) -> str:
    """The fixed classification order the plan's table states, applied to
    an already-completed (or already-reaped-after-timeout) process."""
    if returncode is None or returncode < 0:
        return INTERRUPTED
    if returncode != 0:
        return FAILURE
    if parsed is None or not all(field in parsed for field in _REQUIRED_FIELDS):
        return AMBIGUOUS
    if parsed["is_error"]:
        return FAILURE
    return SUCCESS


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


def launch(
    task: str,
    *,
    cwd: str | Path,
    permission_mode: str,
    timeout: float | None,
    claude_bin: str | None = None,
    pass_fds: Iterable[int] = (),
    on_spawn: Callable[["WorkerProcess"], None] | None = None,
    model: str | None = None,
    effort: str | None = None,
    disallowed_tools: Iterable[str] | None = None,
) -> WorkerResult:
    """Launch one fresh ``claude -p`` worker against ``cwd`` and wait
    synchronously for it to return control.

    ``permission_mode`` has no default: ``auto`` (the lifecycle-worker
    default, ``controller.job.DEFAULT_PERMISSION_MODE``) against a real
    development repository, ``bypassPermissions`` only against a
    disposable throwaway repository -- the caller states its posture
    explicitly every time.

    ``timeout`` is ``None`` for no limit: the wait lasts until the worker
    returns control, however long or silent it is. A number is the
    operator's explicit opt-in; when it expires, the whole process group is
    killed and reaped before the result is classified.

    ``pass_fds`` are descriptors the worker inherits (``controller.job``
    passes the lifecycle lock's, so an orphaned worker keeps the lock until
    it really exits). ``on_spawn``, when given, is called with the
    worker's :class:`WorkerProcess` after ``Popen`` returns and before the
    wait begins -- outside the ``try`` that converts ``Popen``'s own
    ``OSError`` into ``WorkerLaunchError``, because a process exists by
    then. If it raises anything (``KeyboardInterrupt`` included), the
    worker's process group is killed and reaped, and then the original
    exception propagates unchanged, so no untracked worker survives.

    ``model``/``effort`` add ``--model``/``--effort`` when not ``None``,
    passed through unvalidated (the ``claude`` CLI is the authority, as for
    ``permission_mode``). A non-empty ``disallowed_tools`` adds
    ``--disallowedTools`` with the names comma-joined into one argv
    element, placed last. No session-reuse flag is ever passed, so every
    worker is a fresh session.

    Raises :class:`~controller.errors.UserOnlyCommandError` before
    spawning anything if ``task`` names one of :data:`USER_ONLY_COMMANDS`
    (the second denylist layer -- CP4's ``decision.decide`` already never
    *selects* such a command; this makes it impossible to *execute* one
    even through a hand-written task string). Raises
    :class:`~controller.errors.WorkerLaunchError` if the worker process
    itself could not be started at all (an ``OSError`` from
    ``subprocess.Popen``, e.g. ``claude_bin`` does not exist). Every other
    outcome -- including a non-zero exit, a malformed result, or a
    timeout/signal interruption -- is not a raised exception but the
    returned :class:`WorkerResult`'s own ``outcome``.
    """
    _assert_not_user_only(task)

    resolved_claude_bin = claude_bin or "claude"
    args = [
        resolved_claude_bin, "-p", task, "--output-format", "json",
        "--permission-mode", permission_mode,
    ]
    # CP6: the worker's route. The disallow list goes last -- the CLI's
    # `--disallowedTools <tools...>` is variadic, so nothing may follow it.
    if model is not None:
        args += ["--model", model]
    if effort is not None:
        args += ["--effort", effort]
    tools = tuple(disallowed_tools or ())
    if tools:
        args += ["--disallowedTools", ",".join(tools)]

    # The worker's environment is the Controller's own, with PYTHONPATH
    # removed (revision 35, local round 34's OPUS-R34-O1): CP1's re-exec
    # assigns PYTHONPATH the immutable-snapshot directory, and this
    # subprocess otherwise inherits it -- without this, every worker (and
    # everything it shells out to inside the target repository) would run
    # with the Controller's own code first on sys.path.
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
            env=env,
            pass_fds=tuple(pass_fds),
        )
    except OSError as exc:
        raise WorkerLaunchError(
            f"could not launch the Claude worker ({resolved_claude_bin!r}): {exc}",
            evidence={"claude_bin": resolved_claude_bin, "os_error": str(exc)},
        ) from exc

    if on_spawn is not None:
        try:
            on_spawn(capture_worker_process(proc.pid))
        except BaseException:
            # A process exists, so this is never "the worker never
            # started": end the whole group and reap it before the
            # original error propagates, so no untracked worker survives.
            _kill_process_group(proc.pid)
            proc.communicate()
            raise

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # `communicate()`'s own timeout handling never kills the child (see
        # its docstring's recommended usage) -- and killing only the direct
        # child would leave a process-group sibling the worker itself
        # spawned still running and holding the target repository, so the
        # whole group is torn down before reaping.
        _kill_process_group(proc.pid)
        stdout, stderr = proc.communicate()

    returncode = proc.returncode
    parsed = _parse_worker_stdout(stdout)
    outcome = _classify(returncode, parsed)
    fields = _extract_fields(parsed)

    return WorkerResult(
        outcome=outcome,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        raw_json=parsed,
        **fields,
    )


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


def _parse_stat(text: str) -> _ProcStat:
    """Fields are counted after the line's **last** ``)``: ``comm`` (field
    2) may itself contain spaces and parentheses. Field 3 is the state,
    field 5 ``pgrp``, field 22 ``starttime``."""
    close = text.rfind(")")
    if close < 0:
        raise _NoAnswer(f"stat line does not parse: {text!r}")
    rest = text[close + 1:].split()
    if len(rest) < 20:
        raise _NoAnswer(f"stat line has too few fields: {text!r}")
    try:
        return _ProcStat(state=rest[0], pgrp=int(rest[2]), start_ticks=int(rest[19]))
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
