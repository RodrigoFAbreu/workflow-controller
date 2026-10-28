"""`workflow-controller-verify-codex-command` CP1: the verification run (`controller.codex_verify`).

Offline: a fake ``codex`` (a Python script in a temporary npm-style tree, so ``build_digest`` binds it) that prints
canned probe output and does what a scenario file says; real ``git`` for the host fixtures; no model, no sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_build, codex_verify as cv, review  # noqa: E402

FEATURES = "shell_tool    stable    true\nbrowser_use   stable    false\n"

FAKE = r'''#!{python}
import json, os, re, socket, sys
from pathlib import Path
sys.path.insert(0, {repo!r})
from controller import codex_sandbox as cs

here = Path(__file__).resolve().parent.parent
scenario = json.loads((here / "scenario.json").read_text())
args = sys.argv[1:]


def flag(name):
    return args[args.index(name) + 1] if name in args else None


def lines(opts):
    out = {{n: r for n, r in cs.PROBE_EXPECTED.items()}}
    out.update({{n: "REFUSED" for n in cs.PROBE_INFORMATIONAL}})
    out.update(opts.get("lines", {{}}))
    for name in opts.get("drop", []):
        out.pop(name, None)
    return "\n".join(f"PROBE {{n}} {{r}}" for n, r in out.items()) + "\n"


def act(opts, clone, text):
    scratch = Path(os.environ["TMPDIR"]).parent
    if not opts.get("no_effects"):
        (scratch / "probe").write_text("x")
        if not opts.get("no_gh"):
            (scratch / "fake-logs" / "gh.jsonl").write_text('{{"argv": ["--version"]}}\n')
    for op in opts.get("plant", []):
        target = Path(clone) / op["path"]
        if op["kind"] == "symlink":
            target.symlink_to(op["to"])
        elif op["kind"] == "fifo":
            os.mkfifo(target)
        elif op["kind"] == "dir":
            target.mkdir()
        elif op["kind"] == "file":
            target.write_text("planted")
        elif op["kind"] == "edit":
            target.write_text("edited\n")
    if opts.get("marker"):
        marker = Path("/tmp") / re.search(r"/tmp/(verify-[0-9a-f]{{12}})", text).group(1)
        marker.write_text("x")
        with open(scenario["created_log"], "a") as fh:
            fh.write(str(marker) + "\n")
    if opts.get("connect") == "tcp":
        socket.create_connection(("127.0.0.1", int(re.search(r"127\.0\.0\.1\W+(\d+)", text).group(1))), 3).close()
    if opts.get("connect") == "unix":
        s = socket.socket(socket.AF_UNIX)
        s.connect(re.search(r"s\.connect\(\W+(/[^'\"\\)]+)", text).group(1))
        s.close()
    if opts.get("edit_build"):
        (here / "extra.txt").write_text(str(os.getpid()))
    if opts.get("write_auth"):
        (Path(os.environ["CODEX_HOME"]) / "auth.json").write_text(opts["write_auth"])


if args[:1] == ["--version"]:
    print("codex-cli 9.9.9")
elif args[:2] == ["mcp", "list"]:
    print(scenario.get("mcp", "No MCP servers configured yet."))
elif args[:2] == ["features", "list"]:
    print({features!r}, end="")
elif args[:1] == ["sandbox"]:
    opts = scenario.get("sandbox", {{}})
    act(opts, flag("-C"), args[-1])
    sys.stdout.write(lines(opts))
    sys.exit(opts.get("exit", 0))
elif args[:1] == ["exec"]:
    opts = scenario.get("exec", {{}})
    if opts.get("sleep"):
        import time; time.sleep(opts["sleep"])
    act(opts, flag("-C"), args[-1])
    if opts.get("write_auth_late"):
        (Path(os.environ["CODEX_HOME"]) / "auth.json").write_text(opts["write_auth_late"])
    sys.stderr.write(opts.get("stderr", ""))
    if not opts.get("no_output"):
        Path(flag("-o")).write_text(lines(opts))
    sys.exit(opts.get("exit", 0))
else:
    sys.exit(2)
'''


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir()
        self.operator = self.tmp / "operator-codex"
        self.operator.mkdir()
        self.auth = self.operator / "auth.json"
        self.auth.write_text('{"token": "secret"}')
        self.pkg = self.tmp / "lib/node_modules/@openai/codex"
        (self.pkg / "bin").mkdir(parents=True)
        (self.pkg / "package.json").write_text('{"name": "@openai/codex"}')
        self.script = self.pkg / "bin/codex.js"
        self.script.write_text(FAKE.format(python=sys.executable, repo=str(REPO_ROOT), features=FEATURES))
        self.script.chmod(0o755)
        (self.tmp / "bin").mkdir()
        self.binary = self.tmp / "bin/codex"
        self.binary.symlink_to(self.script)
        self.created = self.tmp / "created.log"
        self.scenario({})

    def scenario(self, data: dict) -> None:
        self.created.touch()
        (self.pkg / "scenario.json").write_text(json.dumps({"created_log": str(self.created), **data}))
        self.addCleanup(self.remove_created)

    def remove_created(self) -> None:
        for line in self.created.read_text().splitlines():
            Path(line).unlink(missing_ok=True)

    def verify(self, *, run=review.run_bounded, report=None, **kw):
        kw.setdefault("which", lambda name: "/usr/bin/" + name)
        return cv.verify_build(self.binary, runtime_root=self.runtime, operator_home=self.operator, run=run,
                               report=report or (lambda message: None), **kw)

    def refused(self, probe: str, **kw) -> cv.BuildNotVerified:
        with self.assertRaises(cv.BuildNotVerified) as ctx:
            self.verify(**kw)
        self.assertEqual(ctx.exception.probe, probe, ctx.exception.message)
        self.assertFalse((self.runtime / codex_build.RECORD_NAME).exists())
        return ctx.exception

    def credential_copies(self) -> list[Path]:
        return [Path(root, name) for root, _, names in os.walk(self.runtime) for name in names if name == "auth.json"]


class Recorder:
    def __init__(self, hook=None) -> None:
        self.calls: list[str] = []
        self.hook = hook

    def __call__(self, command, **kw):
        self.calls.append(command[1])
        if self.hook is not None:
            self.hook(command)
        return review.run_bounded(command, **kw)


class VerifyBuildTests(Fixture):
    def test_clean_run_passes_both_probes_and_records_nothing(self) -> None:
        before = codex_build.build_digest(self.binary)
        result = self.verify()
        self.assertEqual(result.probe_results(), {"sandbox": "pass", "exec": "pass"})
        self.assertEqual(result.digest, before)
        self.assertEqual(result.version, "codex-cli 9.9.9")
        self.assertEqual(result.path, str(self.binary))
        self.assertFalse((self.runtime / codex_build.RECORD_NAME).exists())
        self.assertEqual(self.credential_copies(), [])

    def test_a_layout_with_no_digest(self) -> None:
        loose = self.tmp / "loose-codex"
        loose.write_text("#!/bin/sh\n")
        loose.chmod(0o755)
        self.binary = loose
        self.refused("identity")

    def test_missing_codex_or_bwrap(self) -> None:
        self.refused("host", which=lambda name: None)
        self.script.chmod(0o644)
        self.refused("host")

    def test_missing_credential_file(self) -> None:
        self.auth.unlink()
        self.refused("credentials")

    def test_a_launch_control_refusal(self) -> None:
        self.scenario({"mcp": "evil  stdio  enabled"})
        error = self.refused("launch_controls")
        self.assertIn("mcp", error.message)
        self.assertEqual(self.credential_copies(), [])

    def test_a_judged_failure_names_the_probe_line(self) -> None:
        self.scenario({"sandbox": {"lines": {"tracked": "WROTE"}}})
        error = self.refused("sandbox")
        self.assertIn("tracked: WROTE, expected REFUSED", error.message)

    def test_a_missing_probe_line_fails_closed(self) -> None:
        self.scenario({"sandbox": {"drop": ["push"]}})
        self.assertIn("push: no result line", self.refused("sandbox").message)

    def test_a_listener_connection_fails(self) -> None:
        for kind in ("tcp", "unix"):
            with self.subTest(kind=kind):
                self.scenario({"sandbox": {"connect": kind}})
                self.assertIn(f"a host listener saw a connection ({kind})", self.refused("sandbox").message)

    def test_a_changed_clone_file_fails(self) -> None:
        self.scenario({"sandbox": {"plant": [{"kind": "edit", "path": "src.txt"}]}})
        self.assertIn("src.txt changed", self.refused("sandbox").message)

    def test_a_created_marker_path_fails(self) -> None:
        self.scenario({"sandbox": {"marker": True}})
        self.assertRegex(self.refused("sandbox").message, r"/tmp/verify-[0-9a-f]{12} exists")

    def test_a_missing_stand_in_gh_log_fails(self) -> None:
        self.scenario({"sandbox": {"no_gh": True}})
        self.assertIn("the stand-in gh logged no call", self.refused("sandbox").message)

    def test_exec_login_and_no_output_are_model_unreachable(self) -> None:
        for opts in ({"exit": 1, "stderr": "Not logged in: please login"}, {"no_output": True},
                     {"exit": 1, "stderr": "usage_limit_exceeded"}):
            with self.subTest(opts=opts):
                self.scenario({"exec": opts})
                error = self.refused("model_unreachable")
                self.assertIsInstance(error, cv.ModelUnreachable)
                self.assertTrue(error.inconclusive)

    def test_exec_timeout_is_model_unreachable_and_returns_credentials(self) -> None:
        self.scenario({"exec": {"sleep": 30, "write_auth_late": '{"token": "late"}'}})
        with mock.patch.object(cv, "EXEC_TIMEOUT", 2):
            error = self.refused("model_unreachable")
        self.assertIn("timed out", error.message)
        self.assertEqual(self.credential_copies(), [])

    def test_exec_other_failure_carries_exit_code_and_stderr(self) -> None:
        self.scenario({"exec": {"exit": 3, "stderr": "segfault in tool"}})
        error = self.refused("exec_failed")
        self.assertFalse(error.inconclusive)
        self.assertIn("exited 3", error.message)
        self.assertIn("segfault in tool", error.message)

    def test_exec_has_its_own_evidence(self) -> None:
        self.scenario({"exec": {"no_effects": True}})
        error = self.refused("exec")
        self.assertIn("the scratch write did not happen", error.message)
        self.assertIn("the stand-in gh logged no call", error.message)

    def test_snapshot_terminates_on_planted_entries_and_sees_them(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret").write_text("s")
        self.scenario({"sandbox": {"plant": [{"kind": "symlink", "path": "ln", "to": str(outside)},
                                            {"kind": "fifo", "path": "pipe"}, {"kind": "dir", "path": "empty"},
                                            {"kind": "file", "path": "new.txt"}]}})
        message = self.refused("sandbox").message
        for name in ("ln", "pipe", "empty", "new.txt"):
            self.assertIn(f"the clone: {name} was created", message)

    def test_a_build_edited_during_the_run_is_build_changed(self) -> None:
        self.scenario({"sandbox": {"edit_build": True}})
        self.refused("build_changed")

    def test_a_build_repointed_after_the_last_probe_is_build_changed(self) -> None:
        other = self.tmp / "other/lib/node_modules/@openai/codex"
        (other / "bin").mkdir(parents=True)
        (other / "package.json").write_text('{"name": "@openai/codex"}')
        (other / "bin/codex.js").write_text(self.script.read_text() + "# other\n")
        (other / "bin/codex.js").chmod(0o755)
        (other / "scenario.json").write_text((self.pkg / "scenario.json").read_text())
        calls = []

        def hook(command):
            calls.append(command[1])
            if command[1] == "exec":
                self.binary.unlink()
                self.binary.symlink_to(other / "bin/codex.js")

        self.refused("build_changed", run=Recorder(hook))

    def test_exec_is_not_run_when_sandbox_failed(self) -> None:
        self.scenario({"sandbox": {"lines": {"git": "WROTE"}}})
        recorder = Recorder()
        self.refused("sandbox", run=recorder)
        self.assertNotIn("exec", recorder.calls)

    def test_probes_run_in_order_of_cost(self) -> None:
        recorder = Recorder()
        self.verify(run=recorder)
        self.assertEqual([c for c in recorder.calls if c in ("sandbox", "exec")], ["sandbox", "exec"])

    def test_the_host_folder_is_short_and_removed_also_after_a_failure(self) -> None:
        seen = []
        real = cv._short_folder

        def spy() -> Path:
            folder = real()
            seen.append(folder)
            return folder

        with mock.patch.object(cv, "_short_folder", spy):
            self.verify()
            self.scenario({"sandbox": {"lines": {"git": "WROTE"}}})
            self.refused("sandbox")
        self.assertEqual(len(seen), 2)
        for folder in seen:
            self.assertLessEqual(len(os.fsencode(folder / cv.SOCKET_NAME)), 107)
            self.assertFalse(folder.exists())

    def test_a_refreshed_credential_is_copied_back_also_after_a_failure(self) -> None:
        self.scenario({"exec": {"write_auth_late": '{"token": "fresh"}', "lines": {"git": "WROTE"}}})
        messages = []
        self.refused("exec", report=messages.append)
        self.assertEqual(json.loads(self.auth.read_text()), {"token": "fresh"})
        self.assertTrue(any("copied" in m for m in messages), messages)
        self.assertEqual(self.credential_copies(), [])

    def test_an_operator_file_changed_meanwhile_is_not_overwritten(self) -> None:
        self.scenario({"exec": {"write_auth_late": '{"token": "fresh"}'}})

        def hook(command):
            if command[1] == "exec":
                self.auth.write_text('{"token": "operator"}')

        messages = []
        self.verify(run=Recorder(hook), report=messages.append)
        self.assertEqual(json.loads(self.auth.read_text()), {"token": "operator"})
        self.assertTrue(any("operator-changed" in m for m in messages), messages)
        self.assertEqual(self.credential_copies(), [])

    def test_no_snapshot_or_check_runs_git_after_the_run_starts(self) -> None:
        started = []

        def hook(command):
            if command[1] in ("sandbox", "exec"):
                started.append(command[1])

        real = cv._git

        def guarded(args, cwd, stdin=None):
            if started:
                self.fail(f"git ran after the {started[0]} run started: {args}")
            return real(args, cwd, stdin)

        with mock.patch.object(cv, "_git", guarded):
            self.scenario({"sandbox": {"lines": {"git": "WROTE"}}})
            self.refused("sandbox", run=Recorder(hook))

    def test_the_snapshot_never_follows_a_symlink_or_opens_a_fifo(self) -> None:
        root = self.tmp / "snap"
        root.mkdir()
        (root / "f").write_text("a")
        os.symlink("/nonexistent/target", root / "dangling")
        os.mkfifo(root / "fifo")
        (root / "d").mkdir()
        snap = cv.snapshot(root)
        self.assertEqual(snap["f"], ("F", hashlib.sha256(b"a").hexdigest()))
        self.assertEqual(snap["dangling"], ("L", "/nonexistent/target"))
        self.assertEqual(snap["fifo"], ("S", ""))
        self.assertEqual(snap["d"], ("D", ""))


if __name__ == "__main__":
    unittest.main()
