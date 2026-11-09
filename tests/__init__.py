"""The test package.

Importing it isolates every test process from the operator's settings file
(``workflow-controller-settings-and-telemetry`` I5): ``XDG_CONFIG_HOME``
points at a fresh temporary directory and ``WORKFLOW_CONTROLLER_SETTINGS``
is removed, so ``controller.settings.resolve_path`` never reaches
``~/.config/workflow-controller/settings.json`` or a path the parent
environment named. It runs at package import, so ``tools/run_tests.py`` and
a direct ``python3 -m unittest tests.<module>`` run are covered alike, and
every subprocess environment built from ``os.environ`` inherits it.

Beside it (``workflow-controller-usage-budget`` CP3, D1), ``CODEX_HOME``
points at a fresh, empty temporary directory, so a Codex reading taken in
the suite (the ``usage`` subcommand defaults to ``$CODEX_HOME``, else
``~/.codex``) never reads the operator's Codex sessions
(``tests.test_usage``'s guard reads it).

A third guard (issue #329, after ``os.killpg(1, SIGKILL)`` -- ``kill(-1, SIGKILL)`` -- ended the desktop session
four times on 2026-11-09): ``os.kill`` and ``os.killpg`` refuse a real signal whose target is pid or group ``1``,
``0`` or ``-1`` (every process the user may signal, init, or the caller's own group) with an ``AssertionError``, in
every test process. Signal ``0`` (a liveness probe) and every other target pass through, and a test that replaces
``os.kill``/``os.killpg`` with a mock replaces the guard with it. It is a second layer, not a sandbox: a command run
by ``subprocess`` (``kill -9 -1``) is not covered, so a test that needs real signals to a broad target belongs in an
isolated PID namespace (``docs/guide/development.md``, "Running signal tests safely").
"""

import atexit
import os
import shutil
import tempfile

#: The redirected ``XDG_CONFIG_HOME`` (``tests.test_settings``'s guard reads
#: it).
ISOLATED_XDG_CONFIG_HOME = tempfile.mkdtemp(prefix="workflow-controller-tests-config-")
os.environ["XDG_CONFIG_HOME"] = ISOLATED_XDG_CONFIG_HOME
os.environ.pop("WORKFLOW_CONTROLLER_SETTINGS", None)
atexit.register(shutil.rmtree, ISOLATED_XDG_CONFIG_HOME, ignore_errors=True)

#: The redirected ``CODEX_HOME``, empty (``tests.test_usage``'s guard reads it).
ISOLATED_CODEX_HOME = tempfile.mkdtemp(prefix="workflow-controller-tests-codex-")
os.environ["CODEX_HOME"] = ISOLATED_CODEX_HOME
atexit.register(shutil.rmtree, ISOLATED_CODEX_HOME, ignore_errors=True)

#: The redirected ``SIGNALHUB_CONFIG_DIR``, empty
#: (``workflow-controller-signalhub-notifications`` CP1, D7): no test process
#: can resolve the operator's ``~/.config/signalhub/url``, key files or kill
#: switch, even inside a worker the lane watchdog started.
for _name in ("SIGNALHUB_URL", "SIGNALHUB_API_KEY", "SIGNALHUB_API_KEY_FILE"):
    os.environ.pop(_name, None)
ISOLATED_SIGNALHUB_CONFIG_DIR = tempfile.mkdtemp(prefix="workflow-controller-tests-signalhub-")
os.environ["SIGNALHUB_CONFIG_DIR"] = ISOLATED_SIGNALHUB_CONFIG_DIR
atexit.register(shutil.rmtree, ISOLATED_SIGNALHUB_CONFIG_DIR, ignore_errors=True)


#: The targets a real signal must never reach from a test: ``-1`` (every process the user may signal), ``0`` (the
#: caller's own group), ``1`` (init, or group ``1`` for ``killpg``).
UNSAFE_SIGNAL_TARGETS = (-1, 0, 1)


def _guard_signal(real, name):
    def guarded(target, sig):
        if sig and target in UNSAFE_SIGNAL_TARGETS:
            raise AssertionError(f"os.{name}({target}, {sig}) refused: it could signal every process of the "
                                 f"user and end the desktop session (tests/__init__.py)")
        return real(target, sig)
    guarded.__name__ = name
    guarded.__wrapped__ = real
    return guarded


os.kill = _guard_signal(os.kill, "kill")
os.killpg = _guard_signal(os.killpg, "killpg")
