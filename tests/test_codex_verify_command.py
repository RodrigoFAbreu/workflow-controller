"""`workflow-controller-verify-codex-command` CP2: ``workflow-controller verify-codex`` over the fake ``codex`` of
``tests.test_codex_verify`` (offline: no model, no sandbox), with ``--runtime-dir`` and the settings file in temporary
folders."""

from __future__ import annotations

import contextlib
import errno
import io
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli, codex_build, codex_verify as cv, identity, review  # noqa: E402
from tests import test_codex_verify as base  # noqa: E402


class VerifyCodexCommandTests(base.Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "rt"
        self.settings = self.tmp / "cfg" / "settings.json"
        self.calls: list[str] = []
        pinned = identity.ControllerIdentity(
            generation=1, source_root=REPO_ROOT, origin_source_root=REPO_ROOT, source_kind=identity.SOURCE_KIND_COMMIT,
            source_commit="a" * 40, tree_digest="b" * 64, generation_source="head", pinned_at="2024-01-01T00:00:00Z",
            version="9.9.9")
        real_verify = cv.verify_build

        def counting(binary, **kw):
            self.calls.append(str(binary))
            return real_verify(binary, which=lambda name: "/usr/bin/" + name, **kw)

        for patcher in (
                mock.patch.object(identity, "pin", lambda: pinned),
                mock.patch.object(identity, "current", lambda: pinned),
                mock.patch.dict(os.environ, {"PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
                                             "CODEX_HOME": str(self.operator)}),
                mock.patch.object(cv, "verify_build", counting)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--runtime-dir", str(self.root), "--settings", str(self.settings), "verify-codex", *args])
        return code, out.getvalue(), err.getvalue()

    def record(self) -> dict:
        return codex_build.read_verified(self.root)[0]

    def test_a_first_run_probes_records_and_prints(self) -> None:
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (0, ""), out)
        digest = codex_build.build_digest(self.binary)
        self.assertIn(f"digest: {digest}", out)
        self.assertIn("version: codex-cli 9.9.9", out)
        self.assertIn("probe sandbox: pass", out)
        self.assertIn("probe exec: pass", out)
        self.assertIn(f"recorded: {digest} in {self.root / codex_build.RECORD_NAME}", out)
        entry = self.record()[digest]
        self.assertEqual(entry["version"], "codex-cli 9.9.9")
        self.assertEqual(entry["controller_version"], "9.9.9")
        self.assertEqual(entry["probes"], {"sandbox": "pass", "exec": "pass"})
        self.assertIn("verified_at", entry)
        self.assertEqual(self.credential_copies(), [])

    def test_a_second_run_does_not_probe_and_force_does(self) -> None:
        self.run_cli()
        self.assertEqual(len(self.calls), 1)
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("already verified", out)
        self.assertEqual(len(self.calls), 1)
        code, out, _ = self.run_cli("--force")
        self.assertEqual(code, 0)
        self.assertIn("recorded:", out)
        self.assertEqual(len(self.calls), 2)

    def test_an_edited_build_is_probed_again(self) -> None:
        self.run_cli()
        (self.pkg / "extra.txt").write_text("changed")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertNotIn("already verified", out)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.record()), 2)

    def test_an_ignored_record_prints_its_reason_and_the_build_is_probed(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / codex_build.RECORD_NAME).write_text("not json")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertIn(f"note: {codex_build.RECORD_NAME} was ignored", out)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.record()), 1)

    def test_each_refusal_exits_20_names_the_probe_and_records_nothing(self) -> None:
        cases = [
            ("sandbox", {"sandbox": {"lines": {"tracked": "WROTE"}}}),
            ("launch_controls", {"mcp": "evil  stdio  enabled"}),
            ("model_unreachable", {"exec": {"exit": 1, "stderr": "Not logged in: please login"}}),
            ("exec_failed", {"exec": {"exit": 3, "stderr": "segfault"}}),
            ("exec", {"exec": {"no_effects": True}}),
        ]
        for probe, data in cases:
            with self.subTest(probe=probe):
                self.scenario(data)
                code, _, err = self.run_cli()
                self.assertEqual(code, 20)
                self.assertIn(f"{probe}:", err)
                self.assertFalse((self.root / codex_build.RECORD_NAME).exists())
        self.scenario({})
        self.auth.unlink()
        code, _, err = self.run_cli()
        self.assertEqual(code, 20)
        self.assertIn("credentials:", err)

    def test_a_build_changed_during_the_run_records_nothing(self) -> None:
        self.scenario({"sandbox": {"edit_build": True}})
        code, _, err = self.run_cli()
        self.assertEqual(code, 20)
        self.assertIn("build_changed", err)
        self.assertFalse((self.root / codex_build.RECORD_NAME).exists())

    def test_a_build_with_no_digest_is_an_identity_refusal(self) -> None:
        loose = self.tmp / "loose-codex"
        loose.write_text("#!/bin/sh\n")
        loose.chmod(0o755)
        code, _, err = self.run_cli_global("--runtime-dir", str(self.root), "verify-codex", "--codex-binary", str(loose))
        self.assertEqual(code, 20)
        self.assertIn("identity:", err)
        self.assertEqual(self.calls, [])

    def run_cli_global(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--settings", str(self.settings), *args])
        return code, out.getvalue(), err.getvalue()

    def test_an_unwritable_record_exits_20_without_a_traceback(self) -> None:
        def failing(*args, **kw):
            raise OSError(errno.ENOSPC, "No space left on device")

        with mock.patch.object(codex_build, "record_verified", failing):
            code, out, err = self.run_cli()
        self.assertEqual(code, 20)
        self.assertIn("not recorded", err)
        self.assertNotIn("Traceback", err)

    def test_the_settings_file_is_neither_created_nor_changed(self) -> None:
        self.run_cli()
        self.assertFalse(self.settings.exists())
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{}\n")
        before = self.settings.read_bytes()
        self.run_cli("--force")
        self.assertEqual(self.settings.read_bytes(), before)

    def test_codex_binary_verifies_the_named_build_and_the_note_names_the_one_on_path(self) -> None:
        other = self.tmp / "other"
        shutil.copytree(self.tmp / "lib", other / "lib", symlinks=True)
        (other / "bin").mkdir()
        (other / "bin/codex").symlink_to(other / "lib/node_modules/@openai/codex/bin/codex.js")
        (other / "lib/node_modules/@openai/codex/extra.txt").write_text("other")
        code, out, err = self.run_cli_global(
            "--runtime-dir", str(self.root), "verify-codex", "--codex-binary", str(other / "bin/codex"))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn(f"codex: {other / 'bin/codex'}", out)
        self.assertIn(f"a launch runs {self.binary} (codex on PATH)", out)
        self.assertEqual(set(self.record()), {codex_build.build_digest(other / "bin/codex")})

    def test_codex_binary_needs_no_codex_on_path(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": review.SYSTEM_PATH}):
            code, out, err = self.run_cli_global(
                "--runtime-dir", str(self.root), "verify-codex", "--codex-binary", str(self.binary))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("a launch finds no codex on PATH", out)

    def test_without_codex_on_path_and_without_a_binary_the_command_refuses(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent-wc-path"}):
            code, _, err = self.run_cli()
        self.assertEqual(code, 20)
        self.assertIn("host:", err)
        self.assertIn("cannot be resolved", err)

    def test_the_command_is_classified(self) -> None:
        self.assertIn("verify-codex", cli.ALL_COMMANDS)
        self.assertIn("verify-codex", cli.SETTINGS_READ_ONLY_COMMANDS)
        self.assertNotIn("verify-codex", cli.READ_ONLY_COMMANDS)

    def test_the_help_states_the_cost(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["verify-codex", "--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("COST", text)
        self.assertIn("usage budget gate is not consulted", text)


if __name__ == "__main__":
    unittest.main()
