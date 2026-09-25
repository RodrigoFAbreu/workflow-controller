#!/usr/bin/env python3
"""A hermetic, offline stand-in for the real ``claude`` binary, used by
the worker, job and lifecycle tests.

``controller.worker.launch`` always invokes its subprocess with the exact,
fixed argv shape ``-p <task> --output-format stream-json --verbose
--permission-mode <mode>``, so this script cannot be driven by its own
arguments the way
``tests/fixtures.py``'s stub ``workflow-manager`` is (that one branches on
its first positional argument). Instead every behaviour is selected by
environment variable, which ``tests/test_worker.py`` sets per case before
calling ``controller.worker.launch``.

Recognised environment variables (all optional):

``FAKE_CLAUDE_DIAG_FILE``
    If set, a JSON diagnostic record -- ``argv`` (everything after the
    script path), ``cwd``, ``stdin_at_eof`` (whether the first byte read
    from stdin was immediate EOF), ``pythonpath`` (``os.environ``'s
    ``PYTHONPATH``, or ``None``), ``pid``, ``pgid`` and ``fds`` (the open
    descriptor numbers, so a test can see an inherited lifecycle-lock
    descriptor) -- is written to this path before anything else runs
    (after the invocation counter). This is how the launch-mechanics tests (cwd,
    closed stdin, no inherited ``PYTHONPATH``) observe the child's own
    view of the world without needing it to also be the case under test.
``FAKE_CLAUDE_DIAG_LOG``
    If set, one JSON line per invocation is appended to this path, right
    after ``FAKE_CLAUDE_DIAG_FILE``'s record: ``argv``, ``cwd``,
    ``env_keys`` (the sorted names of the environment) and ``fd_count``
    (the number of open descriptors) -- every worker of a multi-job run,
    in launch order, so an equivalence test can compare each worker's own
    view (release-runtime-observability CP7).
``FAKE_CLAUDE_HANG``
    If set, sleep forever (a real ``SIGKILL`` is required to end it) --
    used by the timeout/interruption tests.
``FAKE_CLAUDE_HANG_UNTIL_FILE``
    If set, poll until the file at this path exists, then carry on exactly
    as without it (the ``FAKE_CLAUDE_WRITE*`` writes, the optional commit,
    then the ordinary output). Checked after the diagnostics, the
    invocation counter and the required-file check, and *before* the
    writes -- so a test ends a hanging worker deterministically, and a
    released worker's writes land only after its release
    (`workflow-controller-automatic-lifecycle-orchestration` CP5's orphan
    tests).
``FAKE_CLAUDE_INVOCATIONS_FILE``
    If set, one line (this process's pid) is appended to this file on
    every invocation, first of all -- a counter a test reads to prove how
    many workers ran.
``FAKE_CLAUDE_GIT_COMMIT``
    If set, after the writes, ``git add -A`` and ``git commit -q -m
    <value>`` run in the current directory -- a worker whose effect is a
    commit.
``FAKE_CLAUDE_HANG_CHILD_PID_FILE``
    Only consulted when ``FAKE_CLAUDE_HANG`` is also set: spawn one
    grandchild (itself a hanging ``sleep``) sharing this process's
    process group, and write its pid to this path -- so a test can
    confirm the Controller's timeout ``os.killpg`` reaps the *whole*
    process-group tree, not only the direct child.
``FAKE_CLAUDE_SELF_TERM``
    If set, send this process ``SIGTERM`` before producing any output --
    deterministically exercises the "worker terminated by a signal"
    classification without needing a second process to race a signal
    against this one.
``FAKE_CLAUDE_STDOUT``
    The exact stdout to write, all at once. Defaults to a canned,
    well-formed ``SUCCESS``-shaped ``stream-json`` event stream
    (:func:`default_events`: ``system/init``, an assistant text, a Bash
    ``tool_use``, its ``tool_result`` and a final ``result``), one event
    per line, each flushed as it is written.
``FAKE_CLAUDE_EVENT_DELAY``
    Seconds to sleep between two default events -- a worker whose stream
    a follower can watch arrive.
``FAKE_CLAUDE_PAUSE_AFTER_EVENTS_FILE``
    ``<n>:<path>``: write the first ``n`` default events, then poll until
    the file at ``path`` exists before writing the rest -- so a test can
    read the stream file while the worker is provably still running.
``FAKE_CLAUDE_DESCENDANT``
    ``group:<seconds>``, ``group-closed:<seconds>`` or
    ``setsid:<seconds>``: after its output, and before exiting, this
    process forks a descendant that lives that long. ``group:`` stays in
    the worker's process group with stdout and stderr open;
    ``group-closed:`` stays in the group but points both at ``/dev/null``
    first; ``setsid:`` leaves the group (``os.setsid``) and keeps stdout
    open. The descendant inherits every other descriptor (the lifecycle
    lock's included).
``FAKE_CLAUDE_DESCENDANT_FILE``
    Only with ``FAKE_CLAUDE_DESCENDANT``: the descendant writes
    ``{"pid"}`` here as JSON when it starts, and rewrites it as
    ``{"pid", "exited_at"}`` (``time.time()``) just before it exits.
``FAKE_CLAUDE_STDERR``
    The exact stderr to write. Defaults to empty.

Like the real CLI, ``--output-format stream-json`` without ``--verbose``
is refused before anything else runs: stderr ``Error: When using --print,
--output-format=stream-json requires --verbose``, exit ``1``.
``FAKE_CLAUDE_EXIT``
    The exit code. Defaults to ``0``.
``FAKE_CLAUDE_REQUIRE_FILE``
    If set, this process checks that the file at this path exists
    *before* producing any output. If it does not, this process fails
    immediately (exit ``91``, stderr names the missing path) instead of
    emitting its ordinary result. CP6's own "the ``LAUNCHED`` job record
    is durable on disk before the worker starts" assertion is checked
    this way -- from inside the worker itself, which can only ever run
    after ``controller.job.execute_step`` has already flushed that
    record, rather than by racing the parent process from the outside.
``FAKE_CLAUDE_WRITE_PATH`` / ``FAKE_CLAUDE_WRITE_TEXT``
    If both are set, this process writes ``FAKE_CLAUDE_WRITE_TEXT``
    verbatim to ``FAKE_CLAUDE_WRITE_PATH`` (creating parent directories as
    needed) *before* producing its ordinary output -- CP6B's own "a fake
    worker that performs the expected state edit" fixture
    (``tests/test_job_validation.py``): a worker that never touches the
    target repository is otherwise indistinguishable, from
    ``controller.job.execute_step``'s own step 7 re-read, from one that
    ran and did nothing.
``FAKE_CLAUDE_WRITES``
    A JSON list of ``{"path", "text"}`` objects, each written the same way
    (parent directories created) *after* the single-file
    ``FAKE_CLAUDE_WRITE_PATH``/``_TEXT`` write above, which stays for
    backward compatibility -- for a worker whose completion promises more
    than one durable artifact (e.g. ``WORKFLOW_STATE.json`` *and* a
    coherent plan-bundle ``MANIFEST.md``, `workflow-controller-worker-
    execution-hardening` CP3).
``FAKE_CLAUDE_SCRIPT``
    A scripted worker (`workflow-controller-automatic-lifecycle-
    orchestration` CP7): the path of a JSON file mapping an exact task
    string -- the ``-p`` argument, addendum included -- to an ordered list
    of per-invocation action lists. Each invocation appends one JSON line,
    ``{"task", "invocation"}``, to the counter file beside it
    (:func:`script_invocations_path`), then performs that task's next
    action list (:func:`perform_actions`) in the current directory, which
    is the target repository. A task the script does not name, or an
    invocation past the end of its list, exits ``92`` naming it, after the
    counter line and before any action. The counter line also carries
    ``at`` (``time.time()`` at the invocation's start). An invocation may
    instead be an object ``{"turns": [...]}`` (worker-lifecycle-ownership
    CP4): in streaming mode, those turns are played exactly as
    ``FAKE_CLAUDE_TURNS`` would be (and take its place), so each
    invocation of a multi-job ``run`` can wait on its own background work;
    their ``{"step": "actions", "actions": [...]}`` steps perform the
    scripted actions below inside the turn. Checked after
    ``FAKE_CLAUDE_REQUIRE_FILE`` and ``FAKE_CLAUDE_HANG_UNTIL_FILE``, and
    before the ``FAKE_CLAUDE_WRITE*`` writes.

The scripted actions (:func:`perform_actions`), each a JSON object with an
``"action"`` key:

- ``{"action": "write", "path", "text"}`` writes ``text`` to ``path``
  (parent directories created). Every ``{HEAD}``, ``{HEAD^}`` or
  ``{HEAD~N}`` token in ``text`` is replaced with that revision's full SHA
  at the moment of the write -- so a manifest written after the
  generation-record commit ``T`` records ``generation_head: {HEAD}`` (``T``)
  and ``reviewed_implementation_head: {HEAD^}`` (the commit
  ``record_bundle_generation`` recorded).
- ``{"action": "commit", "paths", "message"}`` stages exactly ``paths``
  (``git add --``) and commits them with ``message``.
- ``{"action": "delete", "path"}`` removes a file or a whole directory
  tree (a withdrawn ``current/``).
- ``{"action": "git", "args"}`` runs ``git <args>`` (a worker that
  switches or rewrites the milestone branch, trunk-branch-pr-release-
  orchestration CP8).

A failing action (a commit with nothing staged, a missing path to delete)
raises, so the worker exits non-zero -- a script error is never silent.

**Streaming-input mode** (`workflow-controller-worker-lifecycle-ownership`
CP1), used whenever ``--input-format stream-json`` is on the argv: the task
is the ``content`` of the first stream-json ``user`` line read from stdin
(:func:`_task` returns the ``-p`` argument when argv carries one, and
otherwise that content, read once), and ``FAKE_CLAUDE_DIAG_FILE`` records
it as ``task_message``; at stdin EOF the record gains ``stdin_eof_at``
and ``stdin_lines_after_task`` (:func:`_record_eof`). Every existing variable (``FAKE_CLAUDE_STDOUT``,
``FAKE_CLAUDE_HANG``, ``FAKE_CLAUDE_DESCENDANT``, ``FAKE_CLAUDE_SCRIPT``)
is played as one turn, exactly as in print mode; then, if that output held
a ``result`` line, the fake waits for stdin EOF and exits with its
configured status, and if it did not (a malformed or truncated stream) it
exits at once, as a crashed harness would. Tool processes, including
``FAKE_CLAUDE_DESCENDANT``'s, inherit no descriptor beyond 0-2 in this
mode (the lifecycle lock's included).

``FAKE_CLAUDE_TURNS``
    Streaming mode only: a JSON list of scripted turns, each a list of
    steps (:class:`StreamingSession`). Turn 0 answers the task; each later
    scripted turn is played, in order, as the turn a task-notification
    (a task's completion, a Monitor event or timeout, a subagent's
    hand-back) starts, with ``origin: {"kind": "task-notification"}``. A
    wakeup's fire turn is its own ``wakeup`` step's ``fire_turn``, played
    inside the measured ``command_lifecycle`` bracket (P11), never taken
    from this list. The steps:

    - ``{"step": "text", "text"}`` / ``{"step": "thinking"}``: one
      ``assistant`` event;
    - ``{"step": "tool", "name", "input", "is_error", "content",
      "fg_task"}``: a foreground call (``tool_use`` then its ``user``
      ``tool_result``; ``fg_task`` puts a foreground ``task_started`` /
      ``task_notification`` pair between them, as real Bash calls do);
    - ``{"step": "tools", "calls": [...], "result_order": [...]}``:
      parallel calls, every ``tool_use`` first, then the results in
      ``result_order`` (indices; default call order). A call is a ``tool``
      step's fields, or ``{"bash_bg": {...}}``;
    - ``{"step": "bash_bg", "id", "seconds", "orphan":
      "none|setsid|reparent|daemon", "argv0", "orphan_seconds",
      "write_file", "description", "command"}``: a *real* background process
      carrying the inherited environment (``background_tasks_changed`` /
      ``task_started``), running ``command`` (a ``bash -c`` string) instead
      of ``sleep <seconds>`` when given; ``orphan`` leaves a descendant
      behind (``setsid``
      in its own session, ``reparent`` double-forked in the group,
      ``daemon`` double-forked into its own session and long-lived, under
      ``argv0``; with ``orphan_write_file`` the descendant instead writes
      its end time there when it exits);
    - ``{"step": "task_stop", "id"}``: a worker-initiated stop, P8's
      statuses (``killed``/``stopped``) mid-turn, and no notification turn;
    - ``{"step": "await", "id", "notify"}``: wait, mid-turn, for task
      ``id`` to end and emit its completion there (a completion the harness
      delivers while a turn is still running); ``notify`` (default true)
      queues its notification turn;
    - ``{"step": "monitor", "id", "ticks", "interval", "timeout"}``: a
      Monitor, a ``local_bash`` task whose every tick starts a turn and
      whose end (``completed``, or ``killed``/``stopped`` at ``timeout``
      seconds) starts one more;
    - ``{"step": "wakeup", "delay", "fire_turn", "prompt", "reason",
      "noop"}`` / ``{"step": "wakeup_stop"}``: the ``ScheduleWakeup`` pair
      as P5/P9 measured (``timestamp``, the harness-stated ``in Ns``,
      ``tool_use_result`` ``scheduledFor``/``clampedDelaySeconds``/
      ``wasClamped``; ``{stopped: true, cancelledWakeups: n}`` for a stop,
      ``n`` the fake's own count of wakeups scheduled and not yet fired --
      a wakeup counts as fired from its ``started(X)``). The fire is
      ``command_lifecycle started(X)``, the fire turn (``system/init``, no
      opening ``user`` event, a ``result`` with no ``origin``), then
      ``completed(X)``, with a fresh ``command_uuid`` per fire;
      ``fire_turn`` (default one ``text`` step) may call tools, nested
      ``wakeup`` included;
    - ``{"step": "lifecycle_fault", "kind", ...}``: perturbs the *next*
      fire's bracket (:data:`LIFECYCLE_FAULTS`);
    - ``{"step": "subagent_handback", "after", "id", "events"}``: a
      background ``Agent`` (``local_agent`` task) whose hand-back, ``after``
      seconds later, completes the task and starts a turn; ``events``
      subagent ``assistant`` texts are emitted with its
      ``parent_tool_use_id`` before the completion;
    - ``{"step": "commit", "message"}`` / ``{"step": "write", "path",
      "text"}``: a Bash / Write call with that side effect;
    - ``{"step": "actions", "actions": [...]}``: one Bash call that performs
      ``FAKE_CLAUDE_SCRIPT``'s scripted actions (:func:`perform_actions`,
      revision tokens included) in the current directory;
    - ``{"step": "end_turn", ...overrides}``: the turn's ``result``
      (``queued_turn_count: 0``); implied at the end of every turn.

    On stdin EOF with tasks open, the fake plays the P1 kill sequence
    (``background_tasks_changed`` without the task, ``task_updated
    {killed}``, ``task_notification {stopped}``) for each open task --
    Monitors first, then the rest in start order, a killed Monitor's
    notification playing the next scripted turn if one remains, as
    ``job_5d4a976a`` measured -- ends its own task processes (never an
    ``orphan`` descendant) and exits 0. ``user``/``assistant`` events carry
    a ``timestamp``; ``system/init`` and ``result`` do not.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid as uuid_module


def _write_diagnostics() -> None:
    diag_file = os.environ.get("FAKE_CLAUDE_DIAG_FILE")
    if not diag_file:
        return
    if _streaming():
        # The task line was already read; whether anything was sent at all.
        stdin_at_eof = _TASK_MESSAGE is None
    else:
        stdin_at_eof = sys.stdin.read(1) == ""
    try:
        fds = sorted(int(name) for name in os.listdir("/proc/self/fd"))
    except OSError:
        fds = None
    diag = {
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "stdin_at_eof": stdin_at_eof,
        "pythonpath": os.environ.get("PYTHONPATH"),
        "pid": os.getpid(),
        "pgid": os.getpgid(0),
        "fds": fds,
    }
    if _streaming():
        diag["task_message"] = _TASK_MESSAGE
    with open(diag_file, "w") as fh:
        json.dump(diag, fh)


def _append_diagnostics_log() -> None:
    log_file = os.environ.get("FAKE_CLAUDE_DIAG_LOG")
    if not log_file:
        return
    try:
        fd_count = len(os.listdir("/proc/self/fd"))
    except OSError:
        fd_count = None
    entry = {"argv": sys.argv[1:], "cwd": os.getcwd(), "env_keys": sorted(os.environ), "fd_count": fd_count}
    with open(log_file, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


#: The session id every default event carries.
FAKE_SESSION_ID = "fake-session-id"

#: The real CLI's refusal of ``stream-json`` in print mode without
#: ``--verbose`` (``claude`` 2.1.281).
STREAM_JSON_REQUIRES_VERBOSE = "Error: When using --print, --output-format=stream-json requires --verbose"


def result_event(**overrides) -> dict:
    """The default final ``result`` event (the fields ``--output-format
    json`` also carries), with ``overrides`` applied."""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": FAKE_SESSION_ID,
        "terminal_reason": None,
        "stop_reason": None,
        "result": "ok",
        "num_turns": 1,
        "permission_denials": [],
        "total_cost_usd": 0.0,
        "duration_ms": 1,
        **overrides,
    }


def default_events() -> list[dict]:
    """The default stream: ``system/init``, an assistant text, a Bash
    ``tool_use``, its ``tool_result`` and the final ``result``."""
    return [
        {"type": "system", "subtype": "init", "session_id": FAKE_SESSION_ID, "cwd": os.getcwd(),
         "model": "fake-model", "tools": ["Bash", "Read"]},
        {"type": "assistant", "session_id": FAKE_SESSION_ID, "parent_tool_use_id": None,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Working on it."}]}},
        {"type": "assistant", "session_id": FAKE_SESSION_ID, "parent_tool_use_id": None,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_fake_1", "name": "Bash", "input": {"command": "true"}}]}},
        {"type": "user", "session_id": FAKE_SESSION_ID, "parent_tool_use_id": None,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_fake_1", "content": "", "is_error": False}]}},
        result_event(),
    ]


def stream_text(events: list[dict]) -> str:
    """``events`` as ``stream-json`` text: one compact JSON object per line."""
    return "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)


def _refuse_stream_json_without_verbose() -> None:
    argv = sys.argv[1:]
    stream_json = any(
        (arg == "--output-format" and argv[index + 1:index + 2] == ["stream-json"])
        or arg == "--output-format=stream-json"
        for index, arg in enumerate(argv)
    )
    if stream_json and "--verbose" not in argv:
        sys.stderr.write(STREAM_JSON_REQUIRES_VERBOSE + "\n")
        sys.exit(1)


def _pause_after() -> tuple[int, str] | None:
    raw = os.environ.get("FAKE_CLAUDE_PAUSE_AFTER_EVENTS_FILE")
    if not raw:
        return None
    count, _, path = raw.partition(":")
    return int(count), path


def _write_stdout() -> str:
    exact = os.environ.get("FAKE_CLAUDE_STDOUT")
    if exact is not None:
        sys.stdout.write(exact)
        sys.stdout.flush()
        return exact
    delay = float(os.environ.get("FAKE_CLAUDE_EVENT_DELAY") or 0)
    pause = _pause_after()
    for index, event in enumerate(default_events()):
        if pause is not None and index == pause[0]:
            while not os.path.exists(pause[1]):
                time.sleep(0.05)
        if index and delay:
            time.sleep(delay)
        sys.stdout.write(stream_text([event]))
        sys.stdout.flush()
    return stream_text(default_events())


def _write_descendant_file(path: str | None, record: dict) -> None:
    if not path:
        return
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(record, fh)
    os.replace(tmp, path)


def _fork_descendant() -> None:
    spec = os.environ.get("FAKE_CLAUDE_DESCENDANT")
    if not spec:
        return
    mode, _, seconds = spec.partition(":")
    if mode not in ("group", "group-closed", "setsid"):
        raise ValueError(f"unknown FAKE_CLAUDE_DESCENDANT mode {mode!r}")
    record_path = os.environ.get("FAKE_CLAUDE_DESCENDANT_FILE")
    sys.stdout.flush()
    sys.stderr.flush()
    if os.fork() != 0:
        # The worker itself: wait until the descendant has recorded its
        # pid, so a test never races the fork.
        if record_path:
            while not os.path.exists(record_path):
                time.sleep(0.01)
        return
    try:
        if _streaming():
            # Streaming mode: a tool process inherits no descriptor beyond
            # 0-2 (``close_fds=True``), so never the lifecycle lock (H6).
            os.closerange(3, _max_fd())
        if mode == "setsid":
            os.setsid()
        elif mode == "group-closed":
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            os.close(devnull)
        _write_descendant_file(record_path, {"pid": os.getpid()})
        time.sleep(float(seconds))
        _write_descendant_file(record_path, {"pid": os.getpid(), "exited_at": time.time()})
    finally:
        os._exit(0)


def _check_required_file() -> None:
    required = os.environ.get("FAKE_CLAUDE_REQUIRE_FILE")
    if required and not os.path.isfile(required):
        sys.stderr.write(f"FAKE_CLAUDE_REQUIRE_FILE: {required} does not exist\n")
        sys.exit(91)


def _write_requested_file() -> None:
    path = os.environ.get("FAKE_CLAUDE_WRITE_PATH")
    text = os.environ.get("FAKE_CLAUDE_WRITE_TEXT")
    if not path or text is None:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def _write_requested_files() -> None:
    raw = os.environ.get("FAKE_CLAUDE_WRITES")
    if not raw:
        return
    for entry in json.loads(raw):
        os.makedirs(os.path.dirname(entry["path"]), exist_ok=True)
        with open(entry["path"], "w") as fh:
            fh.write(entry["text"])


def _count_invocation() -> None:
    path = os.environ.get("FAKE_CLAUDE_INVOCATIONS_FILE")
    if path:
        with open(path, "a") as fh:
            fh.write(f"{os.getpid()}\n")


def _hang_until_file() -> None:
    path = os.environ.get("FAKE_CLAUDE_HANG_UNTIL_FILE")
    if not path:
        return
    while not os.path.exists(path):
        time.sleep(0.05)


def _git_commit() -> None:
    message = os.environ.get("FAKE_CLAUDE_GIT_COMMIT")
    if not message:
        return
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], check=True)


#: ``{HEAD}``, ``{HEAD^}`` and ``{HEAD~N}`` in a scripted write's text.
_REVISION_TOKEN_RE = re.compile(r"\{(HEAD(?:\^|~[0-9]+)?)\}")


def _substitute_revisions(text: str, cwd: str) -> str:
    def resolve(match: re.Match) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{match.group(1)}^{{commit}}"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    return _REVISION_TOKEN_RE.sub(resolve, text)


def perform_actions(actions: list, cwd: "str | os.PathLike" = ".") -> None:
    """Perform one scripted invocation's ``actions`` in ``cwd``, in order
    (the module docstring's action list). The one implementation both the
    scripted worker process and a test's in-process seeding use, so a
    seeded state is exactly what a scripted worker would have left."""
    cwd = os.fspath(cwd)
    for action in actions:
        kind = action["action"]
        if kind == "write":
            path = os.path.join(cwd, action["path"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(_substitute_revisions(action["text"], cwd))
        elif kind == "commit":
            subprocess.run(["git", "add", "--", *action["paths"]], cwd=cwd, check=True)
            subprocess.run(["git", "commit", "-q", "-m", action["message"]], cwd=cwd, check=True)
        elif kind == "delete":
            path = os.path.join(cwd, action["path"])
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
        elif kind == "git":
            subprocess.run(["git", *action["args"]], cwd=cwd, check=True)
        else:
            raise ValueError(f"unknown scripted action {kind!r}")


def script_invocations_path(script_path: "str | os.PathLike") -> str:
    """The counter file beside a ``FAKE_CLAUDE_SCRIPT`` file."""
    return os.fspath(script_path) + ".invocations"


def _task() -> str | None:
    """The ``-p`` prompt argument when argv carries one; otherwise, in
    streaming mode, the first stream-json user message's content (read
    once, by :func:`_read_task_message`)."""
    argv = sys.argv[1:]
    if "-p" in argv and argv.index("-p") + 1 < len(argv):
        value = argv[argv.index("-p") + 1]
        if not value.startswith("--"):
            return value
    if _streaming() and isinstance(_TASK_MESSAGE, dict):
        content = (_TASK_MESSAGE.get("message") or {}).get("content")
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        return content
    return None


def _run_script() -> None:
    script_path = os.environ.get("FAKE_CLAUDE_SCRIPT")
    if not script_path:
        return
    task = _task()
    counter = script_invocations_path(script_path)
    prior = 0
    if os.path.exists(counter):
        with open(counter) as fh:
            prior = sum(1 for line in fh if line.strip() and json.loads(line)["task"] == task)
    with open(counter, "a") as fh:
        fh.write(json.dumps({"task": task, "invocation": prior, "at": time.time()}) + "\n")
    with open(script_path) as fh:
        script = json.load(fh)
    invocations = script.get(task)
    if invocations is None or prior >= len(invocations):
        sys.stderr.write(f"FAKE_CLAUDE_SCRIPT: no scripted invocation #{prior} for task {task!r}\n")
        sys.exit(92)
    entry = invocations[prior]
    if isinstance(entry, dict):
        # Worker-lifecycle-ownership CP4: a scripted streaming session.
        global _SCRIPTED_TURNS
        _SCRIPTED_TURNS = entry["turns"]
        return
    perform_actions(entry)


# ---------------------------------------------------------------------------
# Streaming-input mode (worker-lifecycle-ownership CP1).
# ---------------------------------------------------------------------------

#: The turns of a ``FAKE_CLAUDE_SCRIPT`` invocation given as
#: ``{"turns": [...]}`` (worker-lifecycle-ownership CP4), or ``None``.
_SCRIPTED_TURNS: "list | None" = None

#: The first stream-json line read from stdin in streaming mode (a parsed
#: object, the raw text when it is not JSON, or ``None`` at immediate EOF).
_TASK_MESSAGE: "dict | str | None" = None


def _streaming() -> bool:
    argv = sys.argv[1:]
    return any(
        (arg == "--input-format" and argv[index + 1:index + 2] == ["stream-json"])
        or arg == "--input-format=stream-json"
        for index, arg in enumerate(argv)
    )


def _read_task_message() -> None:
    """Read the one task line a streaming-input launch sends, once."""
    global _TASK_MESSAGE
    line = sys.stdin.readline()
    if not line:
        _TASK_MESSAGE = None
        return
    try:
        _TASK_MESSAGE = json.loads(line)
    except ValueError:
        _TASK_MESSAGE = line


def _max_fd() -> int:
    try:
        return os.sysconf("SC_OPEN_MAX")
    except (ValueError, OSError):
        return 4096


def _has_result_line(text: str | None) -> bool:
    for line in (text or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return True
    return False


def _wait_for_stdin_eof() -> None:
    extra = 0
    while sys.stdin.readline():
        extra += 1
    _record_eof(extra)


def _record_eof(extra_lines: int) -> None:
    """Streaming mode: rewrite ``FAKE_CLAUDE_DIAG_FILE`` at stdin EOF,
    adding ``stdin_eof_at`` (``time.time()``) and ``stdin_lines_after_task``
    (lines read after the task line), so a test can see when, and after
    what, the Controller closed stdin. ``stdin_at_eof`` keeps its meaning
    (stdin was at EOF when the task was read)."""
    diag_file = os.environ.get("FAKE_CLAUDE_DIAG_FILE")
    if not diag_file:
        return
    try:
        with open(diag_file) as fh:
            diag = json.load(fh)
    except (OSError, ValueError):
        diag = {}
    diag.update({"stdin_eof_at": time.time(), "stdin_lines_after_task": extra_lines})
    tmp = f"{diag_file}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(diag, fh)
    os.replace(tmp, diag_file)


def _now_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


def _children_tree(pid: int) -> list[int]:
    """``pid``'s live descendants still attached to it, from ``/proc``."""
    parents: dict[int, list[int]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            text = open(f"/proc/{name}/stat").read()
        except OSError:
            continue
        ppid = int(text[text.rfind(")") + 1:].split()[1])
        parents.setdefault(ppid, []).append(int(name))
    found, stack = [], [pid]
    while stack:
        for child in parents.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


#: The ``lifecycle_fault`` kinds (plan CP1). Each perturbs the *next* fire's
#: bracket, except ``bracket_turn`` and ``spurious_bracket``, which bracket
#: the next task-completion, Monitor or hand-back turn.
LIFECYCLE_FAULTS = (
    "omit_started", "omit_completed", "duplicate_started", "duplicate_completed",
    "completed_before_started", "delay_completed", "delay_completed_past_next_turn", "reuse_uuid",
    "overlap", "empty", "malformed", "bracket_turn", "spurious_bracket", "delay_fire", "delay_turn",
)
_NOTIFICATION_FAULTS = ("bracket_turn", "spurious_bracket")

_WAKEUP_SCHEDULED_TAIL = (" Nothing more to do this turn — the harness re-invokes you when the wakeup fires "
                          "or a task-notification arrives.")


class _Task:
    def __init__(self, task_id: str, tool_use_id: str, kind: str, description: str, *,
                 proc: "subprocess.Popen | None" = None, task_type: str = "local_bash") -> None:
        self.task_id = task_id
        self.tool_use_id = tool_use_id
        self.kind = kind  # "bash" | "monitor" | "agent"
        self.description = description
        self.proc = proc
        self.task_type = task_type
        self.started = time.monotonic()
        self.open = True
        self.ticks = 0
        self.ticks_done = 0
        self.interval = 0.0
        self.timeout: float | None = None
        self.handback_at: float | None = None
        self.handback_events: list = []

    def finished(self) -> bool:
        """Whether the task's own work is over (not whether it was reported)."""
        if self.kind == "bash":
            return self.proc is not None and self.proc.poll() is not None
        if self.kind == "agent":
            return self.handback_at is not None and time.monotonic() >= self.handback_at
        # A Monitor ends one interval after its last tick.
        return self.ticks_done >= self.ticks and time.monotonic() >= self.started + (self.ticks + 1) * self.interval


class StreamingSession:
    """The streaming-input harness the fake plays for ``FAKE_CLAUDE_TURNS``
    (the module docstring lists the steps). Every event shape is the one the
    ``tests/harness_contract/`` fixtures measured."""

    def __init__(self, turns: list) -> None:
        self.turns = list(turns) or [[{"step": "text", "text": "ok"}]]
        self.next_turn = 1
        self.session_id = FAKE_SESSION_ID
        self.tasks: dict[str, _Task] = {}
        self.wakeups: list[dict] = []
        self.fire_faults: list[dict] = []
        self.notification_faults: list[dict] = []
        self.notifications: list[str] = []
        self.last_command_uuid: str | None = None
        self.held_completed: list[dict] = []  # completed(X) events owed after the next turn
        self.overlap_completed: dict | None = None
        self.moved_bracket = False
        self.counter = 0
        self.eof = threading.Event()

    # -- emission -----------------------------------------------------------

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter:04d}"

    def emit(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("session_id", self.session_id)
        event.setdefault("uuid", str(uuid_module.uuid4()))
        if event.get("type") in ("user", "assistant"):
            event.setdefault("timestamp", _now_timestamp())
        sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def assistant(self, item: dict, parent: str | None = None) -> None:
        self.emit({"type": "assistant", "message": {"role": "assistant", "model": "fake-model",
                                                    "content": [item]},
                   "parent_tool_use_id": parent})

    def tool_use(self, name: str, tool_input: dict) -> str:
        tool_use_id = self._id("toolu_fake_")
        self.assistant({"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input})
        return tool_use_id

    def tool_result(self, tool_use_id: str, content, *, is_error=None, tool_use_result=None) -> None:
        item = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if is_error is not None:
            item["is_error"] = is_error
        event = {"type": "user", "message": {"role": "user", "content": [item]}, "parent_tool_use_id": None}
        if tool_use_result is not None:
            event["tool_use_result"] = tool_use_result
        self.emit(event)

    def system(self, subtype: str, **fields) -> None:
        self.emit({"type": "system", "subtype": subtype, **fields})

    def init(self) -> None:
        self.system("init", cwd=os.getcwd(), model="fake-model", permissionMode="auto",
                    claude_code_version="fake",
                    tools=["Agent", "Bash", "Monitor", "Read", "ScheduleWakeup", "TaskStop", "Write"])

    def lifecycle(self, command_uuid: str, state: str, drop: str | None = None) -> None:
        event = {"type": "command_lifecycle", "command_uuid": command_uuid, "state": state,
                 "uuid": str(uuid_module.uuid4()), "session_id": self.session_id}
        if drop:
            event.pop(drop, None)
            sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            return
        self.emit(event)

    def open_tasks(self) -> list[_Task]:
        return [task for task in self.tasks.values() if task.open]

    def tasks_changed(self) -> None:
        self.system("background_tasks_changed", tasks=[
            {"task_id": t.task_id, "task_type": t.task_type, "description": t.description}
            for t in self.open_tasks()])

    def task_started(self, task: _Task, **extra) -> None:
        self.system("task_started", task_id=task.task_id, tool_use_id=task.tool_use_id,
                    description=task.description, is_backgrounded=True, task_type=task.task_type, **extra)

    def end_task(self, task: _Task, updated: str, notified: str) -> None:
        """The P3 (``completed``) or P1/P8 (``killed``/``stopped``) sequence."""
        task.open = False
        self.tasks_changed()
        self.system("task_updated", task_id=task.task_id,
                    patch={"status": updated, "end_time": int(time.time() * 1000)})
        self.system("task_notification", task_id=task.task_id, tool_use_id=task.tool_use_id, status=notified,
                    summary=f"Background task \"{task.description}\" {notified}")

    def kill_task(self, task: _Task) -> None:
        """End the task's own process tree (never an escaped orphan)."""
        if task.proc is None or task.proc.poll() is not None:
            return
        for pid in [task.proc.pid] + _children_tree(task.proc.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        task.proc.wait()

    # -- turns --------------------------------------------------------------

    def end_turn(self, origin: dict | None, overrides: dict) -> None:
        fields = {k: v for k, v in overrides.items() if k != "step"}
        event = result_event(**{"terminal_reason": "completed", "stop_reason": "end_turn", "result": "done",
                                "queued_turn_count": 0, **fields})
        if origin is not None and "origin" not in fields:
            event["origin"] = origin
        event["uuid"] = str(uuid_module.uuid4())
        self.emit(event)

    def play_turn(self, steps: list, origin: dict | None = None) -> None:
        self.init()
        for step in steps:
            if step.get("step") == "end_turn":
                self.end_turn(origin, step)
                break
            self.play_step(step)
        else:
            self.end_turn(origin, {})
        while self.held_completed:
            self.emit(self.held_completed.pop(0))

    def next_scripted_turn(self) -> list:
        if self.next_turn < len(self.turns):
            turn = self.turns[self.next_turn]
            self.next_turn += 1
            return turn
        return [{"step": "text", "text": "ok"}]

    def play_notification_turn(self, source: str) -> None:
        turn = self.next_scripted_turn()
        origin = {"kind": "task-notification"}
        fault = next((f for f in self.notification_faults
                      if f.get("turn", f.get("kind_of_turn")) in (None, source)), None)
        if fault is None:
            self.play_turn(turn, origin)
            return
        self.notification_faults.remove(fault)
        if fault["kind"] == "bracket_turn":
            self.moved_bracket = True
        command_uuid = str(uuid_module.uuid4())
        self.lifecycle(command_uuid, "started")
        self.play_turn(turn, origin)
        self.lifecycle(command_uuid, "completed")

    # -- steps --------------------------------------------------------------

    def play_step(self, step: dict) -> None:
        kind = step.get("step")
        handler = getattr(self, f"step_{kind}", None)
        if handler is None:
            raise ValueError(f"unknown FAKE_CLAUDE_TURNS step {kind!r}")
        handler(step)

    def step_text(self, step: dict) -> None:
        self.assistant({"type": "text", "text": step.get("text", "ok")})

    def step_thinking(self, step: dict) -> None:
        self.assistant({"type": "thinking", "thinking": step.get("thinking", "")})

    def _foreground_default_error(self, name: str):
        return False if name == "Bash" else None

    def _fg_result(self, call: dict, tool_use_id: str) -> None:
        if call.get("fg_task"):
            task_id = self._id("bfg")
            self.system("task_started", task_id=task_id, tool_use_id=tool_use_id, description=call.get("name"),
                        is_backgrounded=False, task_type="local_bash")
            self.system("task_notification", task_id=task_id, tool_use_id=tool_use_id, status="completed")
        name = call.get("name", "Bash")
        self.tool_result(tool_use_id, call.get("content", "ok"),
                         is_error=call.get("is_error", self._foreground_default_error(name)),
                         tool_use_result=call.get("tool_use_result"))

    def step_tool(self, step: dict) -> None:
        tool_use_id = self.tool_use(step.get("name", "Bash"), step.get("input") or {})
        self._fg_result(step, tool_use_id)

    def step_tools(self, step: dict) -> None:
        calls = step["calls"]
        ids = []
        for call in calls:
            if "bash_bg" in call:
                ids.append(self.tool_use("Bash", {"command": "background", "run_in_background": True}))
            else:
                ids.append(self.tool_use(call.get("name", "Bash"), call.get("input") or {}))
        for index in step.get("result_order") or range(len(calls)):
            call = calls[index]
            if "bash_bg" in call:
                self._start_bash(call["bash_bg"], ids[index])
            else:
                self._fg_result(call, ids[index])

    def _bash_command(self, step: dict) -> str:
        import shlex
        seconds = step.get("seconds", 1)
        command = step.get("command") or f"sleep {seconds}"
        if step.get("write_file"):
            command += f"; echo done > {shlex.quote(step['write_file'])}"
        orphan = step.get("orphan", "none")
        if orphan == "none":
            return command
        argv0 = step.get("argv0") or ("fake-claude-daemon" if orphan == "daemon" else "fake-claude-orphan")
        lifetime = step.get("orphan_seconds", 3600 if orphan == "daemon" else 60)
        if step.get("orphan_write_file"):
            # The orphan writes its end time (``time.time()``) when it exits.
            inner = shlex.quote(f"sleep {lifetime}; date +%s.%N > {shlex.quote(step['orphan_write_file'])}")
        else:
            inner = shlex.quote(f"exec -a {shlex.quote(argv0)} sleep {lifetime}")
        if orphan == "setsid":
            spawn = f"(setsid bash -c {inner} </dev/null >/dev/null 2>&1 &)"
        elif orphan == "reparent":
            spawn = f"(bash -c {inner} </dev/null >/dev/null 2>&1 &)"
        elif orphan == "daemon":
            spawn = f"(setsid bash -c {inner} </dev/null >/dev/null 2>&1 & disown)"
        else:
            raise ValueError(f"unknown bash_bg orphan mode {orphan!r}")
        return f"{spawn}; {command}"

    def _start_bash(self, step: dict, tool_use_id: str) -> _Task:
        proc = subprocess.Popen(["bash", "-c", self._bash_command(step)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        task = _Task(step.get("id") or self._id("bfake"), tool_use_id, "bash",
                     step.get("description", f"sleep {step.get('seconds', 1)}"), proc=proc)
        self.tasks[task.task_id] = task
        self.tasks_changed()
        self.task_started(task)
        self.tool_result(tool_use_id, f"Command running in background with ID: {task.task_id}.", is_error=False,
                         tool_use_result={"stdout": "", "stderr": "", "interrupted": False,
                                          "backgroundTaskId": task.task_id})
        return task

    def step_bash_bg(self, step: dict) -> None:
        tool_use_id = self.tool_use("Bash", {"command": self._bash_command(step), "run_in_background": True})
        self._start_bash(step, tool_use_id)

    def step_task_stop(self, step: dict) -> None:
        task = self.tasks[step["id"]]
        tool_use_id = self.tool_use("TaskStop", {"task_id": task.task_id})
        self.kill_task(task)
        self.end_task(task, "killed", "stopped")
        self.tool_result(tool_use_id, f"Successfully stopped task: {task.task_id}")

    def step_await(self, step: dict) -> None:
        task = self.tasks[step["id"]]
        while not task.finished():
            time.sleep(0.01)
        if task.kind == "agent":
            self._agent_events(task)
        self.end_task(task, "completed", "completed")
        if step.get("notify", True):
            self.notifications.append({"bash": "task", "agent": "handback"}.get(task.kind, "monitor"))

    def step_monitor(self, step: dict) -> None:
        tool_use_id = self.tool_use("Monitor", {"command": "fake monitor", "description": step.get("id", "monitor")})
        ticks, interval = int(step.get("ticks", 1)), float(step.get("interval", 0.1))
        timeout = step.get("timeout")
        lifetime = 3600 if timeout is not None else ticks * interval + 1
        proc = subprocess.Popen(["sleep", str(lifetime)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True)
        task = _Task(step.get("id") or self._id("bmon"), tool_use_id, "monitor", step.get("id", "monitor"),
                     proc=proc)
        task.ticks, task.interval = ticks, interval
        task.timeout = float(timeout) if timeout is not None else None
        self.tasks[task.task_id] = task
        self.tasks_changed()
        self.task_started(task)
        self.tool_result(tool_use_id, f"Monitor started (task {task.task_id}).")

    def step_wakeup(self, step: dict) -> None:
        delay = step.get("delay", 60)
        tool_input = {"delaySeconds": delay, "prompt": step.get("prompt", "fake wakeup"),
                      "reason": step.get("reason", "fake"), "noop": step.get("noop", False)}
        tool_use_id = self.tool_use("ScheduleWakeup", tool_input)
        due = time.time() + float(delay)
        stated = max(1, int(round(float(delay))))
        self.wakeups.append({"due": due, "fire_turn": step.get("fire_turn") or [{"step": "text", "text": "woken"}],
                             "fired": False, "cancelled": False, "tool_use_id": tool_use_id})
        self.tool_result(
            tool_use_id,
            f"Next wakeup scheduled for {time.strftime('%H:%M:%S', time.localtime(due))} (in {stated}s)."
            + _WAKEUP_SCHEDULED_TAIL,
            tool_use_result={"scheduledFor": int(due * 1000), "clampedDelaySeconds": delay, "wasClamped": False})

    def pending_wakeups(self) -> list[dict]:
        return [w for w in self.wakeups if not w["fired"] and not w["cancelled"]]

    def step_wakeup_stop(self, step: dict) -> None:
        tool_use_id = self.tool_use("ScheduleWakeup", {"stop": True})
        pending = self.pending_wakeups()
        for wakeup in pending:
            wakeup["cancelled"] = True
        count = len(pending)
        text = (f"Loop stopped — cancelled {count} pending wakeup(s); no further dynamic-loop wakeups "
                "scheduled." if count else
                "Loop stopped — any dynamic loop in this session is ended; there was no pending wakeup "
                "to cancel.")
        self.tool_result(tool_use_id, text, tool_use_result={
            "scheduledFor": 0, "clampedDelaySeconds": 0, "wasClamped": False, "stopped": True,
            "cancelledWakeups": count})

    def step_lifecycle_fault(self, step: dict) -> None:
        if step.get("kind") not in LIFECYCLE_FAULTS:
            raise ValueError(f"unknown lifecycle_fault kind {step.get('kind')!r}")
        (self.notification_faults if step["kind"] in _NOTIFICATION_FAULTS else self.fire_faults).append(dict(step))

    def step_subagent_handback(self, step: dict) -> None:
        tool_use_id = self.tool_use("Agent", {"description": "fake subagent", "prompt": "fake",
                                              "subagent_type": "general-purpose", "run_in_background": True})
        task = _Task(step.get("id") or self._id("afake"), tool_use_id, "agent", "fake subagent",
                     task_type="local_agent")
        task.handback_at = time.monotonic() + float(step.get("after", 0.1))
        task.handback_events = step.get("events", [{"step": "text", "text": "HANDBACK"}])
        self.tasks[task.task_id] = task
        self.tasks_changed()
        self.task_started(task, subagent_type="general-purpose", spawn_depth=1)
        self.tool_result(tool_use_id, f"Async agent launched successfully. agentId: {task.task_id}")

    def _agent_events(self, task: _Task) -> None:
        for event in task.handback_events:
            if event.get("step") == "thinking":
                self.assistant({"type": "thinking", "thinking": ""}, parent=task.tool_use_id)
            else:
                self.assistant({"type": "text", "text": event.get("text", "HANDBACK")}, parent=task.tool_use_id)

    def step_commit(self, step: dict) -> None:
        tool_use_id = self.tool_use("Bash", {"command": "git commit"})
        subprocess.run(["git", "add", "-A"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-q", "-m", step["message"]], check=True, stdout=subprocess.DEVNULL)
        self.tool_result(tool_use_id, "committed", is_error=False)

    def step_actions(self, step: dict) -> None:
        tool_use_id = self.tool_use("Bash", {"command": "scripted actions"})
        perform_actions(step["actions"])
        self.tool_result(tool_use_id, "done", is_error=False)

    def step_write(self, step: dict) -> None:
        tool_use_id = self.tool_use("Write", {"file_path": step["path"]})
        directory = os.path.dirname(step["path"])
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(step["path"], "w") as fh:
            fh.write(step.get("text", ""))
        self.tool_result(tool_use_id, "written")

    # -- the fire -----------------------------------------------------------

    def fire(self, wakeup: dict) -> None:
        fault = self.fire_faults[0] if self.fire_faults else None
        if fault is not None and fault["kind"] == "delay_fire" and not fault.get("applied"):
            fault["applied"] = True
            wakeup["due"] += float(fault.get("seconds", 1))
            self.fire_faults.pop(0)
            return
        if fault is not None:
            self.fire_faults.pop(0)
        kind = fault["kind"] if fault else None
        wakeup["fired"] = True  # counts as fired from started(X) on
        if self.moved_bracket:
            self.moved_bracket = False
            self.play_turn(wakeup["fire_turn"])
            return
        if kind == "reuse_uuid" and self.last_command_uuid:
            command_uuid = self.last_command_uuid
        else:
            command_uuid = str(uuid_module.uuid4())
        self.last_command_uuid = command_uuid
        if self.overlap_completed is not None:
            held, self.overlap_completed = self.overlap_completed, None
            self.lifecycle(command_uuid, "started")
            self.lifecycle(held["command_uuid"], "completed")
            self.play_turn(wakeup["fire_turn"])
            self.lifecycle(command_uuid, "completed")
            return
        if kind == "completed_before_started":
            self.lifecycle(command_uuid, "completed")
            self.lifecycle(command_uuid, "started")
            self.play_turn(wakeup["fire_turn"])
            return
        if kind == "empty":
            self.lifecycle(command_uuid, "started")
            self.lifecycle(command_uuid, "completed")
            return
        if kind != "omit_started":
            self.lifecycle(command_uuid, "started", drop=fault.get("field") if kind == "malformed" else None)
        if kind == "duplicate_started":
            self.lifecycle(command_uuid, "started")
        if kind == "delay_turn":
            # Worker-lifecycle-ownership CP5: the bracket stays open, with no
            # turn yet, for ``seconds`` (a Controller lost in that window).
            time.sleep(float(fault.get("seconds", 1)))
        self.play_turn(wakeup["fire_turn"])
        if kind == "omit_completed":
            return
        if kind == "delay_completed":
            time.sleep(float(fault.get("seconds", 1)))
        if kind == "delay_completed_past_next_turn":
            self._owe_completed_after_next_turn(command_uuid)
            return
        if kind == "overlap":
            self.overlap_completed = {"command_uuid": command_uuid}
            return
        self.lifecycle(command_uuid, "completed")
        if kind == "duplicate_completed":
            self.lifecycle(command_uuid, "completed")

    def _owe_completed_after_next_turn(self, command_uuid: str) -> None:
        self.held_completed.append({"type": "command_lifecycle", "command_uuid": command_uuid,
                                    "state": "completed", "uuid": str(uuid_module.uuid4()),
                                    "session_id": self.session_id})
        # ``play_turn`` flushes it after the *next* turn's result: this
        # fire turn's own ``play_turn`` has already returned.

    # -- the session --------------------------------------------------------

    def _read_stdin(self) -> None:
        _wait_for_stdin_eof()
        self.eof.set()

    def poll_once(self) -> bool:
        """Report one thing that happened while idle; ``False`` if nothing did."""
        now = time.monotonic()
        for task in self.open_tasks():
            if task.kind == "monitor":
                if task.timeout is not None and now >= task.started + task.timeout:
                    # P8: a Monitor reaching its own timeout is killed/stopped
                    # between turns, and a turn follows.
                    self.kill_task(task)
                    self.end_task(task, "killed", "stopped")
                    self.notifications.append("monitor")
                    return True
                if task.ticks_done < task.ticks and now >= task.started + (task.ticks_done + 1) * task.interval:
                    task.ticks_done += 1
                    self.notifications.append("monitor")
                    return True
                if task.timeout is None and task.finished():
                    self.kill_task(task)
                    self.end_task(task, "completed", "completed")
                    self.notifications.append("monitor")
                    return True
            elif task.finished():
                if task.kind == "agent":
                    self._agent_events(task)
                self.end_task(task, "completed", "completed")
                self.notifications.append("handback" if task.kind == "agent" else "task")
                return True
        due = [w for w in self.pending_wakeups() if w["due"] <= time.time()]
        if due:
            self.fire(min(due, key=lambda w: w["due"]))
            return True
        return False

    def exit_at_eof(self) -> None:
        """The P1 kill sequence for every open task (Monitors first), a
        killed Monitor's notification playing the next scripted turn if one
        remains (``job_5d4a976a``)."""
        order = [t for t in self.open_tasks() if t.kind == "monitor"] + \
                [t for t in self.open_tasks() if t.kind != "monitor"]
        for task in order:
            self.kill_task(task)
            self.end_task(task, "killed", "stopped")
            if task.kind == "monitor" and self.next_turn < len(self.turns):
                self.play_turn(self.next_scripted_turn(), {"kind": "task-notification"})

    def run(self) -> None:
        self.play_turn(self.turns[0])
        threading.Thread(target=self._read_stdin, daemon=True).start()
        while True:
            if self.eof.is_set():
                self.exit_at_eof()
                return
            if self.notifications:
                self.play_notification_turn(self.notifications.pop(0))
                continue
            if not self.open_tasks() and not self.pending_wakeups():
                self.eof.wait()
                continue
            if not self.poll_once():
                self.eof.wait(0.01)


def main() -> None:
    _refuse_stream_json_without_verbose()
    _count_invocation()
    if _streaming():
        _read_task_message()
    _write_diagnostics()
    _append_diagnostics_log()
    _check_required_file()
    _hang_until_file()
    _run_script()
    _write_requested_file()
    _write_requested_files()
    _git_commit()

    if os.environ.get("FAKE_CLAUDE_SELF_TERM"):
        os.kill(os.getpid(), signal.SIGTERM)
        # SIGTERM's default disposition ends the process; sleep in case a
        # test harness's own signal handling ever intercepts it, so this
        # script never falls through and emits a misleading result.
        time.sleep(3600)

    if os.environ.get("FAKE_CLAUDE_HANG"):
        child_pid_file = os.environ.get("FAKE_CLAUDE_HANG_CHILD_PID_FILE")
        if child_pid_file:
            child = subprocess.Popen(["sleep", "3600"])
            with open(child_pid_file, "w") as fh:
                fh.write(str(child.pid))
        time.sleep(3600)

    if _streaming() and (_SCRIPTED_TURNS is not None or os.environ.get("FAKE_CLAUDE_TURNS") is not None):
        turns = _SCRIPTED_TURNS if _SCRIPTED_TURNS is not None else json.loads(os.environ["FAKE_CLAUDE_TURNS"])
        StreamingSession(turns).run()
        sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))

    written = _write_stdout()
    stderr = os.environ.get("FAKE_CLAUDE_STDERR", "")
    if stderr:
        sys.stderr.write(stderr)
        sys.stderr.flush()
    _fork_descendant()
    if _streaming() and _has_result_line(written):
        # One turn played; like the harness, the session stays open until
        # stdin is closed. A stream without a ``result`` is a crashed
        # harness, which exits at once.
        _wait_for_stdin_eof()
    sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))


if __name__ == "__main__":
    main()
