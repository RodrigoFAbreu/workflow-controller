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
    from stdin was immediate EOF), ``pythonpath`` (``os.environ``'s
    ``PYTHONPATH``, or ``None``), ``pid``, ``pgid`` and ``fds`` (the open
    descriptor numbers, so a test can see an inherited lifecycle-lock
    descriptor) -- is written to this path before anything else runs
    (after the invocation counter). This is how the launch-mechanics tests (cwd,
    closed stdin, no inherited ``PYTHONPATH``) observe the child's own
    view of the world without needing it to also be the case under test.
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
    counter line and before any action. Checked after
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

A failing action (a commit with nothing staged, a missing path to delete)
raises, so the worker exits non-zero -- a script error is never silent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time


def _write_diagnostics() -> None:
    diag_file = os.environ.get("FAKE_CLAUDE_DIAG_FILE")
    if not diag_file:
        return
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
        else:
            raise ValueError(f"unknown scripted action {kind!r}")


def script_invocations_path(script_path: "str | os.PathLike") -> str:
    """The counter file beside a ``FAKE_CLAUDE_SCRIPT`` file."""
    return os.fspath(script_path) + ".invocations"


def _task() -> str | None:
    argv = sys.argv[1:]
    if "-p" in argv and argv.index("-p") + 1 < len(argv):
        return argv[argv.index("-p") + 1]
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
        fh.write(json.dumps({"task": task, "invocation": prior}) + "\n")
    with open(script_path) as fh:
        script = json.load(fh)
    invocations = script.get(task)
    if invocations is None or prior >= len(invocations):
        sys.stderr.write(f"FAKE_CLAUDE_SCRIPT: no scripted invocation #{prior} for task {task!r}\n")
        sys.exit(92)
    perform_actions(invocations[prior])


def main() -> None:
    _count_invocation()
    _write_diagnostics()
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

    sys.stdout.write(os.environ.get("FAKE_CLAUDE_STDOUT", _default_stdout()))
    stderr = os.environ.get("FAKE_CLAUDE_STDERR", "")
    if stderr:
        sys.stderr.write(stderr)
    sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))


if __name__ == "__main__":
    main()
