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
