"""The stand-ins the functional tester's sandbox puts first on its ``PATH``
(``workflow-controller-automated-functional-review`` CP3, D3).

Each of ``gh``, ``claude``, ``codex`` and ``ssh`` is a small program that logs
its argv and exits non-zero: it reaches nothing, so a tester that tries the
real tool learns so at once. They ship inside the package because a file under
``tests/`` does not exist for an installed Controller driving another
repository. The sandbox's ``bin/`` entries are launcher scripts the Controller
writes (``functional_review.write_launchers``), each running one of these
modules through ``sys.executable -P -m``.

The log is a file of JSON lines named by ``FUNCTIONAL_FAKE_LOG``; a program
whose log cannot be written still refuses.
"""

from __future__ import annotations

import json
import os
import sys

#: The environment variable that names the log file.
LOG_ENV = "FUNCTIONAL_FAKE_LOG"
#: The exit status of every stand-in.
REFUSED_STATUS = 127


def refuse(name: str, argv: list[str] | None = None) -> int:
    """Log ``name`` and its argv, say it is a stand-in, and return the status."""
    argv = list(sys.argv[1:] if argv is None else argv)
    record = {"name": name, "argv": argv, "cwd": os.getcwd()}
    path = os.environ.get(LOG_ENV)
    if path:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, json.dumps(record, sort_keys=True).encode("utf-8") + b"\n")
            finally:
                os.close(fd)
        except OSError:
            pass
    sys.stderr.write(f"{name}: not available in the functional tester's sandbox (a stand-in that refuses and "
                     "logs; there is no network, no real GitHub, no real claude, codex or ssh)\n")
    return REFUSED_STATUS
