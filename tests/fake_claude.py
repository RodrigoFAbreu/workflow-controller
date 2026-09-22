#!/usr/bin/env python3
"""A hermetic, offline stand-in for the real ``claude`` binary, used only
by ``tests/test_worker.py``.

``controller.worker.launch`` always invokes its subprocess with the exact,
fixed argv shape ``-p <task> --output-format json --permission-mode
<mode>``, so this script cannot be driven by its own arguments the way
``tests/fixtures.py``'s stub ``workflow-manager`` is (that one branches on
its first positional argument). Instead every behaviour is selected by
environment variable, which ``tests/test_worker.py`` sets per case before
calling ``controller.worker.launch``.

Recognised environment variables (all optional):

``FAKE_CLAUDE_DIAG_FILE``
    If set, a JSON diagnostic record -- ``argv`` (everything after the
    script path), ``cwd``, ``stdin_at_eof`` (whether the first byte read
    from stdin was immediate EOF) and ``pythonpath`` (``os.environ``'s
    ``PYTHONPATH``, or ``None``) -- is written to this path before
    anything else runs. This is how the launch-mechanics tests (cwd,
    closed stdin, no inherited ``PYTHONPATH``) observe the child's own
    view of the world without needing it to also be the case under test.
``FAKE_CLAUDE_HANG``
    If set, sleep forever (a real ``SIGKILL`` is required to end it) --
    used by the timeout/interruption tests.
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
    The exact stdout to write. Defaults to a canned, well-formed
    ``SUCCESS``-shaped JSON body.
``FAKE_CLAUDE_STDERR``
    The exact stderr to write. Defaults to empty.
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
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time


def _write_diagnostics() -> None:
    diag_file = os.environ.get("FAKE_CLAUDE_DIAG_FILE")
    if not diag_file:
        return
    stdin_at_eof = sys.stdin.read(1) == ""
    diag = {
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "stdin_at_eof": stdin_at_eof,
        "pythonpath": os.environ.get("PYTHONPATH"),
    }
    with open(diag_file, "w") as fh:
        json.dump(diag, fh)


def _default_stdout() -> str:
    return json.dumps({
        "session_id": "fake-session-id",
        "is_error": False,
        "subtype": "success",
        "terminal_reason": None,
        "stop_reason": None,
        "result": "ok",
        "num_turns": 1,
        "permission_denials": [],
        "total_cost_usd": 0.0,
        "duration_ms": 1,
    })


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


def main() -> None:
    _write_diagnostics()
    _check_required_file()
    _write_requested_file()
    _write_requested_files()

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

    sys.stdout.write(os.environ.get("FAKE_CLAUDE_STDOUT", _default_stdout()))
    stderr = os.environ.get("FAKE_CLAUDE_STDERR", "")
    if stderr:
        sys.stderr.write(stderr)
    sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))


if __name__ == "__main__":
    main()
