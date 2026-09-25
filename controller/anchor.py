"""The worker's stdin anchor (``workflow-controller-worker-lifecycle-ownership``
CP3, ``docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md``,
design A).

``controller.worker.launch`` runs this file's *source text* as
``[sys.executable, "-I", "-c", <source>, <arguments>]``, in its own
session, with an empty environment and no descriptor beyond the two it is
handed: the write end of the worker's stdin pipe and, when the caller holds
one, the lifecycle lock's descriptor. It therefore imports nothing from the
``controller`` package (stdlib only), so it runs the same from a pipx
install or a source tree.

It has two separately ended roles:

- **stdin.** The worker sees EOF only when the anchor closes its write end,
  which it does on ``SIGUSR1`` -- the supervisor's request at a quiescent
  terminal turn (``ENDING``);
- **lock.** It keeps its copy of the lifecycle lock until the supervisor
  ends it after the drain, so the lock stays held for the whole owned
  lifetime even if the Controller dies.

**Orphan lifetime.** It ends itself, with no signal, once all of these have
held on every check for ``orphan_seconds``: the worker's recorded
``(pid, start_ticks)`` is gone (a zombie counts as gone); no same-uid
process's readable ``environ`` carries the job's ownership tag; and no
supervisor is attached (a non-blocking ``flock`` on the supervisor lock
succeeds, and is released at once). So an orphaned anchor never holds the
lock for good.

Arguments, in order: the stdin write-end descriptor number, the worker pid,
its start ticks (``-`` when unknown), the ownership tag, the supervisor-lock
path, the poll interval and the orphan lifetime in seconds.
"""

from __future__ import annotations

import fcntl
import os
import signal
import sys
import time

#: How often the anchor re-checks the orphan-lifetime rule.
ANCHOR_POLL_SECONDS = 5.0

#: How long the orphan-lifetime rule must hold, on every check, before the
#: anchor ends itself.
ANCHOR_ORPHAN_SECONDS = 60.0

#: The environment variable carrying a process's ownership tags, a
#: ``:``-separated list (``controller.worker.OWNERSHIP_VAR``).
OWNERSHIP_VAR = "WORKFLOW_CONTROLLER_OWNERSHIP"

_NOT_RUNNING_STATES = ("Z", "X", "x")


def _stat(pid: int) -> tuple[str, int] | None:
    """``(state, start_ticks)`` of ``pid``, or ``None`` when it is gone or
    its ``stat`` line does not parse."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            text = fh.read()
    except OSError:
        return None
    rest = text[text.rfind(")") + 1:].split()
    try:
        return rest[0], int(rest[19])
    except (IndexError, ValueError):
        return None


def worker_gone(pid: int, start_ticks: int | None) -> bool:
    """Whether the recorded worker no longer exists as a running process."""
    stat = _stat(pid)
    if stat is None or stat[0] in _NOT_RUNNING_STATES:
        return True
    return start_ticks is not None and stat[1] != start_ticks


def tagged_process_alive(tag: str) -> bool:
    """Whether any running same-uid process's readable ``environ`` lists
    ``tag`` in :data:`OWNERSHIP_VAR`. An unreadable ``environ`` is not
    owned by tag."""
    uid = os.getuid()
    prefix = f"{OWNERSHIP_VAR}=".encode()
    wanted = tag.encode()
    try:
        names = os.listdir("/proc")
    except OSError:
        return True  # cannot tell: never end on an unanswerable check
    for name in names:
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            if os.stat(f"/proc/{name}").st_uid != uid:
                continue
            with open(f"/proc/{name}/environ", "rb") as fh:
                environ = fh.read().split(b"\0")
        except OSError:
            continue
        for item in environ:
            if item.startswith(prefix) and wanted in item[len(prefix):].split(b":"):
                stat = _stat(int(name))
                if stat is not None and stat[0] not in _NOT_RUNNING_STATES:
                    return True
                break
    return False


def supervisor_attached(path: str) -> bool:
    """Whether a supervisor holds the lock at ``path``. A missing file is
    unattached; any other failure to test it counts as attached."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def orphaned(pid: int, start_ticks: int | None, tag: str, supervisor_lock: str) -> bool:
    return (worker_gone(pid, start_ticks) and not tagged_process_alive(tag)
            and not supervisor_attached(supervisor_lock))


#: This file's own source text, read once at import (the package is loaded
#: eagerly, so a later upgrade of the installed files can never change what
#: a running Controller spawns). ``None`` inside the anchor process itself,
#: which runs the text with ``-c`` and has no ``__file__``.
SOURCE: str | None = None
if __name__ != "__main__":
    with open(__file__, encoding="utf-8") as _source_file:
        SOURCE = _source_file.read()


def command(stdin_fd: int, worker_pid: int, worker_start_ticks: int | None, tag: str,
            supervisor_lock: str, *, poll: float | None = None, orphan_seconds: float | None = None) -> list[str]:
    """The anchor's argv: :data:`SOURCE` run by ``sys.executable`` in
    isolated mode, with :func:`main`'s arguments. ``poll`` and
    ``orphan_seconds`` default to :data:`ANCHOR_POLL_SECONDS` and
    :data:`ANCHOR_ORPHAN_SECONDS`, read at call time."""
    return [
        sys.executable, "-I", "-c", SOURCE, str(stdin_fd), str(worker_pid),
        "-" if worker_start_ticks is None else str(worker_start_ticks), tag, supervisor_lock,
        str(ANCHOR_POLL_SECONDS if poll is None else poll),
        str(ANCHOR_ORPHAN_SECONDS if orphan_seconds is None else orphan_seconds),
    ]


def main(argv: list[str]) -> int:
    stdin_fd = int(argv[0])
    pid = int(argv[1])
    start_ticks = None if argv[2] == "-" else int(argv[2])
    tag, supervisor_lock = argv[3], argv[4]
    poll, orphan_seconds = float(argv[5]), float(argv[6])

    def release_stdin(_signum, _frame) -> None:
        try:
            os.close(stdin_fd)
        except OSError:
            pass

    signal.signal(signal.SIGUSR1, release_stdin)
    orphaned_since = None
    while True:
        time.sleep(poll)
        if orphaned(pid, start_ticks, tag, supervisor_lock):
            now = time.monotonic()
            orphaned_since = now if orphaned_since is None else orphaned_since
            if now - orphaned_since >= orphan_seconds:
                return 0
        else:
            orphaned_since = None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
