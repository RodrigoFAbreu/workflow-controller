"""`workflow-controller-verify-codex-command` CP2: the operator-run live flow of ``workflow-controller verify-codex``.

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on CI. It runs the command against the real
``codex`` in a scratch runtime root and spends one short model turn on the operator's Codex credentials. Skipped,
with the reason, when ``codex`` or ``bwrap`` is missing or the operator's Codex home has no ``auth.json``.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -m unittest tests.test_verify_codex_live -v``
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli, codex_build, review  # noqa: E402
from tests.test_codex_sandbox_live import operator_codex_home, skip_reason  # noqa: E402


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveVerifyCodexTest(unittest.TestCase):
    def setUp(self) -> None:
        # Not under /tmp: Codex refuses to create its helper binaries there.
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="verify-codex-live-", dir=cache)).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.runtime = self.root / "runtime"

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        env = {"CODEX_HOME": str(operator_codex_home())}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--runtime-dir", str(self.runtime), "--settings", str(self.root / "settings.json"),
                             "verify-codex", *args])
        return code, out.getvalue(), err.getvalue()

    def test_the_real_build_is_verified_recorded_and_not_probed_again(self) -> None:
        binary = review.resolve_binary("codex")
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, out + err)
        self.assertIn("probe sandbox: pass", out)
        self.assertIn("probe exec: pass", out)
        digest = codex_build.build_digest(binary)
        self.assertIn(digest, codex_build.read_verified(self.runtime)[0])
        self.assertTrue(codex_build.is_verified(self.runtime, binary).ok)
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("already verified", out)
        self.assertFalse((self.root / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
