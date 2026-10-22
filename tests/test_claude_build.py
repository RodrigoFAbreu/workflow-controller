"""The record of verified Claude builds and the launch that admits them
(``workflow-controller-claude-build-verify`` CP1, D3, D4, D6). No test reaches a
model: the launch runs over ``tests/fake_functional_tester.py``."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import claude_build as cb  # noqa: E402
from controller import functional_review as fr  # noqa: E402
from controller import review  # noqa: E402
from tests import fake_functional_tester as fake  # noqa: E402
from tests import test_functional_sandbox as sandbox_tests  # noqa: E402

DIGEST = "ab" * 32


def elf(tag: str) -> bytes:
    return b"\x7fELF\x02" + b"x" * 4000 + tag.encode()


def ok(code=0, err=b""):
    return subprocess.CompletedProcess([], code, b"", err)


class RecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name, "rt")
        self.root.mkdir()

    def write(self, obj, mode=0o600) -> Path:
        path = self.root / cb.RECORD_NAME
        path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
        path.chmod(mode)
        return path

    def test_a_missing_record_is_empty_without_a_reason(self) -> None:
        self.assertEqual(cb.read_verified(self.root), ({}, None))

    def test_a_recorded_build_reads_back_and_the_file_is_private(self) -> None:
        path = cb.record_verified(self.root, DIGEST, {"version": "9.9.9"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(cb.read_verified(self.root), ({DIGEST: {"version": "9.9.9"}}, None))

    def test_recording_merges_with_what_is_there(self) -> None:
        cb.record_verified(self.root, DIGEST, {"version": "1"})
        cb.record_verified(self.root, "cd" * 32, {"version": "2"})
        self.assertEqual(sorted(cb.read_verified(self.root)[0]), sorted([DIGEST, "cd" * 32]))

    def test_a_damaged_record_admits_nothing_and_says_why(self) -> None:
        cases = {"not json": "{{", "wrong schema": {"schema_version": 2, "builds": {}},
                 "builds not a map": {"schema_version": 1, "builds": []},
                 "bad digest": {"schema_version": 1, "builds": {"nope": {}}},
                 "entry not an object": {"schema_version": 1, "builds": {DIGEST: 1}}}
        for name, content in cases.items():
            with self.subTest(name):
                self.write(content)
                builds, note = cb.read_verified(self.root)
                self.assertEqual(builds, {})
                self.assertIn("was ignored", note)

    def test_a_record_writable_by_others_is_ignored(self) -> None:
        for mode in (0o620, 0o602):
            with self.subTest(mode=oct(mode)):
                self.write({"schema_version": 1, "builds": {DIGEST: {}}}, mode=mode)
                builds, note = cb.read_verified(self.root)
                self.assertEqual(builds, {})
                self.assertIn("was ignored", note)

    def test_a_record_owned_by_another_user_is_ignored(self) -> None:
        self.write({"schema_version": 1, "builds": {DIGEST: {}}})
        with mock.patch("os.geteuid", return_value=os.geteuid() + 1):
            builds, note = cb.read_verified(self.root)
        self.assertEqual(builds, {})
        self.assertIn("was ignored", note)

    def test_a_symlinked_record_is_ignored_and_never_written_through(self) -> None:
        elsewhere = Path(self.tmp.name, "tester-output")
        elsewhere.mkdir()
        target = elsewhere / "record.json"
        target.write_text(json.dumps({"schema_version": 1, "builds": {DIGEST: {}}}))
        target.chmod(0o600)
        (self.root / cb.RECORD_NAME).symlink_to(target)
        builds, note = cb.read_verified(self.root)
        self.assertEqual(builds, {})
        self.assertIn("was ignored", note)
        before = target.read_bytes()
        with self.assertRaises(OSError):
            cb.record_verified(self.root, "cd" * 32, {})
        self.assertEqual(target.read_bytes(), before)

    def test_a_record_swapped_for_a_symlink_after_the_check_is_not_read(self) -> None:
        path = self.write({"schema_version": 1, "builds": {DIGEST: {}}})
        other = Path(self.tmp.name, "other.json")
        other.write_text(json.dumps({"schema_version": 1, "builds": {"cd" * 32: {}}}))
        real_open = os.open

        def swapping_open(name, flags, *args, **kwargs):
            if Path(name) == path:
                path.unlink()
                path.symlink_to(other)
            return real_open(name, flags, *args, **kwargs)

        with mock.patch("os.open", swapping_open):
            builds, note = cb.read_verified(self.root)
        self.assertEqual(builds, {})
        self.assertIn("was ignored", note)


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.claude = Path(self.tmp.name, "claude")
        self.claude.write_bytes(elf("recorded build"))
        self.digest = hashlib.sha256(self.claude.read_bytes()).hexdigest()

    def probe(self, **kwargs) -> None:
        fr.static_probe(which=lambda name: f"/usr/bin/{name}", run=mock.Mock(return_value=ok()),
                        claude=str(self.claude), **kwargs)

    def test_a_recorded_digest_passes_and_the_same_build_is_refused_without_it(self) -> None:
        self.probe(recorded={self.digest})
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.probe()
        text = str(caught.exception)
        self.assertIn("verify-claude", text)
        self.assertIn(self.digest, text)
        self.assertEqual(caught.exception.code, fr.GATE_TESTER_UNAVAILABLE)

    def test_the_stop_text_says_when_a_record_was_ignored(self) -> None:
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.probe(recorded=(), record_note="verified-claude-builds.json was ignored: boom")
        self.assertIn("was ignored: boom", str(caught.exception))

    def test_a_recorded_digest_never_admits_a_non_elf(self) -> None:
        script = Path(self.tmp.name, "script")
        script.write_text("#!/bin/sh\n")
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
        self.assertFalse(fr._is_native_executable(str(script), recorded={digest}))

    def test_the_host_probe_and_identity_check_compose_into_the_static_probe(self) -> None:
        fr.host_probe(which=lambda name: f"/usr/bin/{name}", run=mock.Mock(return_value=ok()))
        with self.assertRaises(fr.TesterUnavailable):
            fr.host_probe(which=lambda name: None, run=mock.Mock())
        fr.identity_check(str(self.claude), recorded={self.digest})


class LaunchTests(sandbox_tests.Fixture):
    """``review.launch_functional`` over two different ``claude`` files."""

    def setUp(self) -> None:
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.logs = {}
        self.wrappers = {}
        for name in ("a", "b"):
            self.logs[name] = self.tmp / f"{name}.log"
            self.wrappers[name] = fake.write_binary(self.tools, {"log": str(self.logs[name])}, name=f"claude-{name}")
        self.link = self.bin / "claude"
        self.link.symlink_to(self.wrappers["a"])

    def launch_default(self, *, probe=None, job="job-1", runtime=None):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        with mock.patch.dict(os.environ, env), mock.patch.object(fr, "host_probe"):
            return review.launch_functional(
                candidate=self.candidate, flows=self.flows, runtime_root=runtime or self.runtime, job_id=job,
                work_item_id="item-1", operator_home=self.home, model=sandbox_tests.MODEL, effort="medium",
                timeout=60, probe=probe, inherited_env={})

    def test_the_build_whose_digest_is_recorded_is_launched_and_the_same_build_unrecorded_is_not(self) -> None:
        digest = hashlib.sha256(self.wrappers["a"].read_bytes()).hexdigest()
        with mock.patch.object(fr, "_is_native_executable", wraps=fr._is_native_executable) as native:
            with self.assertRaises(fr.TesterUnavailable) as caught:
                self.launch_default()
            self.assertIn(digest, str(caught.exception))
            native.assert_called()
        self.assertFalse(self.logs["a"].exists())
        # The wrapper is a script: the identity check wants an ELF, so admit it through the digest check seam.
        cb.record_verified(self.runtime, digest, {"version": "t"})
        with mock.patch.object(fr, "_is_native_executable",
                               side_effect=lambda path, **kw: hashlib.sha256(Path(os.path.realpath(path)).read_bytes()
                                                                              ).hexdigest() in kw["recorded"]):
            result = self.launch_default()
        self.settle(result)
        self.assertTrue(self.logs["a"].exists())

    def test_a_malformed_record_does_not_admit_and_the_stop_text_says_it_was_ignored(self) -> None:
        (self.runtime / cb.RECORD_NAME).write_text("{{")
        (self.runtime / cb.RECORD_NAME).chmod(0o600)
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.launch_default()
        self.assertIn("was ignored", str(caught.exception))

    def test_the_probe_gets_the_resolved_binary_and_a_symlink_re_pointed_after_it_still_runs_that_file(self) -> None:
        seen = {}

        def static(**kwargs):
            seen.update(kwargs)
            self.link.unlink()
            self.link.symlink_to(self.wrappers["b"])  # an installer re-points the link after the probe

        with mock.patch.object(fr, "static_probe", static):
            result = self.launch_default()
        self.settle(result)
        self.assertEqual(seen["claude"], os.path.realpath(self.wrappers["a"]))
        self.assertTrue(self.logs["a"].exists())
        self.assertFalse(self.logs["b"].exists())

    def test_the_launch_resolves_claude_on_path_and_ignores_the_binary_name_of_the_worker(self) -> None:
        with mock.patch.object(fr, "static_probe") as static:
            result = self.launch_default()
        self.settle(result)
        self.assertEqual(static.call_args.kwargs["claude"], os.path.realpath(self.wrappers["a"]))

    def test_a_runtime_root_inside_the_tester_s_folders_refuses_the_launch(self) -> None:
        real = fr.build_sandbox

        def widened(*args, **kwargs):
            box = real(*args, **kwargs)  # an output folder that contains the runtime root
            return types.SimpleNamespace(base=box.base, clone=box.clone, out=self.tmp, writable=box.writable,
                                         refused=box.refused)

        with mock.patch.object(fr, "static_probe"), mock.patch.object(fr, "build_sandbox", widened):
            with self.assertRaises(fr.TesterUnavailable) as caught:
                self.launch_default()
        self.assertIn("runtime root", str(caught.exception))
        self.assertFalse((self.runtime / fr.SANDBOX_AREA / "job-1").exists())


if __name__ == "__main__":
    unittest.main()


class FakeClaude:
    """A stand-in for the launch callable of ``verify_build``: it answers
    ``--version`` and plays the three probes the way a build that holds the
    boundary does, performing the permitted effects the host-side checks need.
    ``fault`` breaks exactly one probe; ``during`` runs inside the first
    probe's launch."""

    def __init__(self, fault: str | None = None, during=None) -> None:
        self.fault, self.during, self.launches, self.argv0 = fault, during, 0, set()

    def __call__(self, command, *, input=None, capture_output=True, cwd=None, env=None, timeout=None, check=False):
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, b"2.9.9 (Claude Code)\n", b"")
        self.launches += 1
        self.argv0.add(command[0])
        if self.fault == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if self.during is not None and self.launches == 1:
            self.during()
        prompt = input.decode()
        out = Path(cwd)
        clone = out.parent / "clone"
        if "```sh" in prompt:
            events = self.boundary(prompt, out, clone)
        elif "dangerouslyDisableSandbox" in prompt:
            events = self.retry()
        else:
            events = self.missing(env)
        return subprocess.CompletedProcess(command, 0, "\n".join(json.dumps(e) for e in events).encode(), b"")

    def init(self):
        tools = ["Bash", "Write"] if self.fault == "tools" else ["Bash"]
        return {"type": "system", "subtype": "init", "tools": tools}

    @staticmethod
    def call(command, output, call_id="t1"):
        return [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": call_id, "name": "Bash", "input": command}]}},
                {"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": call_id, "content": output}]}}]

    def boundary(self, prompt, out, clone):
        command = prompt.split("```sh\n", 1)[1].rsplit("\n```", 1)[0]
        if self.fault == "login":
            return [self.init(), {"type": "result", "is_error": True, "result": "Not logged in - please run /login"}]
        (clone / "build" / "probe").write_bytes(b"")
        (out / "probe").write_bytes(b"")
        if self.fault == "host_write":
            (clone / "src.txt").write_bytes(b"changed\n")
        if self.fault == "socket":
            import re
            import socket
            path = re.search(r"(/[^\s'\"]*host\.sock)", command)[1]
            sock = socket.socket(socket.AF_UNIX)
            sock.connect(path)
            sock.close()
            time.sleep(0.5)
        lines = [f"PROBE {name} {want}" for name, want in fr.PROBE_EXPECTED.items()] + ["PROBE shm WROTE"]
        if self.fault == "missing_line":
            lines = [line for line in lines if "unix" not in line]
        if self.fault == "denied_write_reported":
            lines = [line.replace("home REFUSED", "home WROTE") for line in lines]
        output = "\n".join(lines)
        if self.fault == "not_executed":
            return [self.init(), {"type": "result", "is_error": False, "result": output}]
        if self.fault == "altered_command":
            command = command.replace("tracked", "other", 1)
        if self.fault == "twice":
            return [self.init(), *self.call({"command": command}, output), *self.call({"command": command}, output, "t2"),
                    {"type": "result", "is_error": False, "result": output}]
        return [self.init(), *self.call({"command": command}, output),
                {"type": "result", "is_error": False, "result": output}]

    def retry(self):
        attempted = {"command": "echo hi > /tmp/x"}
        if self.fault != "no_retry":
            attempted["dangerouslyDisableSandbox"] = True
        return [self.init(), *self.call(attempted, "refused"), {"type": "result", "is_error": False, "result": "done"}]

    def missing(self, env):
        if self.fault == "not_refused":
            return [{"type": "result", "is_error": False, "result": "ran", "num_turns": 1}]
        return [{"type": "result", "is_error": True, "num_turns": 0,
                 "errors": ["sandbox required but unavailable: bwrap is missing"]}]


class VerifyBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "rt"
        self.root.mkdir()
        self.home = base / "claude-home"
        self.home.mkdir()
        (self.home / review.CLAUDE_CREDENTIAL_FILE).write_text("{}")
        self.real = base / "versions" / "claude-9.9.9"
        self.real.parent.mkdir()
        self.real.write_bytes(elf("real"))
        self.link = base / "bin-claude"
        self.link.symlink_to(self.real)
        patch = mock.patch.object(fr, "host_probe", lambda **kw: None)
        patch.start()
        self.addCleanup(patch.stop)

    def verify(self, fake: FakeClaude, **kw):
        return cb.verify_build(self.link, runtime_root=self.root, operator_home=self.home, run=fake, **kw)

    def assertRefused(self, fake: FakeClaude, probe: str, *, inconclusive: bool, text: str = "") -> None:
        with self.assertRaises(cb.BuildNotVerified) as ctx:
            self.verify(fake)
        self.assertEqual(ctx.exception.probe, probe, ctx.exception.message)
        self.assertEqual(ctx.exception.inconclusive, inconclusive, ctx.exception.message)
        self.assertIn(text, ctx.exception.message)
        self.assertFalse((self.root / cb.RECORD_NAME).exists())
        scratch = self.root / cb.SCRATCH_AREA
        self.assertEqual(list(scratch.iterdir()) if scratch.exists() else [], [])

    def test_a_build_that_holds_every_probe_passes_with_the_digest_of_the_resolved_file(self) -> None:
        fake = FakeClaude()
        result = self.verify(fake)
        self.assertEqual(result.digest, hashlib.sha256(elf("real")).hexdigest())
        self.assertEqual(result.path, str(self.real))
        self.assertEqual(result.version, "2.9.9")
        self.assertEqual([p.name for p in result.probes], ["boundary", "retry", "missing_component"])
        self.assertEqual(fake.argv0, {str(self.real)})  # every probe ran the resolved file, not the link
        self.assertFalse((self.root / cb.RECORD_NAME).exists())  # verifying records nothing by itself
        self.assertEqual(list((self.root / cb.SCRATCH_AREA).iterdir()), [])  # the scratch folder is gone

    def test_each_broken_probe_fails_closed_and_names_the_probe(self) -> None:
        for fault, probe, inconclusive, text in (
                ("host_write", "boundary", False, "changed"),
                ("socket", "boundary", False, "listener"),
                ("missing_line", "boundary", False, "unix: no result line"),
                ("denied_write_reported", "boundary", False, "home: WROTE"),
                ("tools", "boundary", False, "tool list"),
                ("login", "boundary", True, "cannot reach a model"),
                ("timeout", "launch", True, "did not finish"),
                ("not_executed", "boundary", True, "exactly once"),
                ("altered_command", "boundary", True, "exactly once"),
                ("twice", "boundary", True, "exactly once"),
                ("no_retry", "retry", True, "never attempted"),
                ("not_refused", "missing_component", False, "not refused")):
            with self.subTest(fault):
                self.assertRefused(FakeClaude(fault), probe, inconclusive=inconclusive, text=text)

    def test_a_symlink_re_pointed_during_a_probe_still_passes_with_the_original_digest(self) -> None:
        other = self.real.parent / "claude-other"
        other.write_bytes(elf("other"))

        def repoint() -> None:
            self.link.unlink()
            self.link.symlink_to(other)

        fake = FakeClaude(during=repoint)
        result = self.verify(fake)
        self.assertEqual(result.digest, hashlib.sha256(elf("real")).hexdigest())
        self.assertEqual(fake.argv0, {str(self.real)})

    def test_a_resolved_file_rewritten_during_a_probe_is_build_changed(self) -> None:
        fake = FakeClaude(during=lambda: self.real.write_bytes(elf("changed")))
        self.assertRefused(fake, "launch", inconclusive=False, text="build_changed")

    def test_a_non_elf_file_is_refused_before_any_launch(self) -> None:
        self.real.write_bytes(b"#!/bin/sh\n")
        fake = FakeClaude()
        self.assertRefused(fake, "identity", inconclusive=False)
        self.assertEqual(fake.launches, 0)

    def test_an_operator_home_without_a_credential_file_is_refused(self) -> None:
        (self.home / review.CLAUDE_CREDENTIAL_FILE).unlink()
        self.assertRefused(FakeClaude(), "credentials", inconclusive=False)


class VerifyClaudeCommandTests(unittest.TestCase):
    """``workflow-controller verify-claude`` (CP3, D2, D5) over ``FakeClaude``,
    with ``--runtime-dir`` and the settings file in temporary folders."""

    def setUp(self) -> None:
        from controller import cli

        self.cli = cli
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "rt"
        self.settings = base / "cfg" / "settings.json"
        self.home = base / "claude-home"
        self.home.mkdir()
        (self.home / review.CLAUDE_CREDENTIAL_FILE).write_text("{}")
        self.bindir = base / "bin"
        self.bindir.mkdir()
        self.real = base / "versions" / "claude-9.9.9"
        self.real.parent.mkdir()
        self.real.write_bytes(elf("real"))
        self.real.chmod(0o755)
        (self.bindir / "claude").symlink_to(self.real)
        self.fake = FakeClaude()
        real_verify = cb.verify_build
        from controller import identity

        pinned = identity.ControllerIdentity(
            generation=1, source_root=REPO_ROOT, origin_source_root=REPO_ROOT, source_kind=identity.SOURCE_KIND_COMMIT,
            source_commit="a" * 40, tree_digest="b" * 64, generation_source="head", pinned_at="2024-01-01T00:00:00Z",
            version="9.9.9")
        for patcher in (
                mock.patch.object(identity, "pin", lambda: pinned),
                mock.patch.object(identity, "current", lambda: pinned),
                mock.patch.object(fr, "host_probe", lambda **kw: None),
                mock.patch.dict(os.environ, {"PATH": f"{self.bindir}:{os.environ['PATH']}",
                                             "CLAUDE_CONFIG_DIR": str(self.home)}),
                mock.patch.object(cb, "verify_build", lambda binary, **kw: real_verify(binary, run=self.fake, **kw))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *extra: str) -> tuple[int, str, str]:
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(["--runtime-dir", str(self.root), "--settings", str(self.settings),
                                  "verify-claude", *extra])
        return code, out.getvalue(), err.getvalue()

    def test_a_clean_pass_records_the_digest_that_a_following_launch_check_admits(self) -> None:
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0, out)
        digest = hashlib.sha256(elf("real")).hexdigest()
        recorded, note = cb.read_verified(self.root)
        self.assertEqual(set(recorded), {digest})
        self.assertIsNone(note)
        self.assertEqual(recorded[digest]["version"], "2.9.9")
        self.assertEqual(set(recorded[digest]["probes"]), {"boundary", "retry", "missing_component"})
        for text in ("probe boundary: pass", "two short model turns"):
            self.assertIn(text, out)
        fr.identity_check(str(self.bindir / "claude"), recorded=cb.recorded_digests(self.root)[0])  # admits
        with self.assertRaises(fr.TesterUnavailable):
            fr.identity_check(str(self.bindir / "claude"))  # not without the record
        self.assertEqual(list((self.root / cb.SCRATCH_AREA).iterdir()), [])
        self.assertFalse(self.settings.exists())

    def test_each_broken_stub_exits_20_naming_the_probe_and_leaves_no_record_or_scratch(self) -> None:
        for fault, probe in (("host_write", "boundary"), ("socket", "boundary"), ("login", "boundary"),
                             ("no_retry", "retry"), ("not_refused", "missing_component"),
                             ("timeout", "launch")):
            with self.subTest(fault):
                self.fake = FakeClaude(fault)
                code, _, err = self.run_cli()
                self.assertEqual(code, 20)
                self.assertIn(probe, err)
                self.assertFalse((self.root / cb.RECORD_NAME).exists())
                scratch = self.root / cb.SCRATCH_AREA
                self.assertEqual(list(scratch.iterdir()) if scratch.exists() else [], [])
                self.assertFalse(self.settings.exists())

    def test_a_rewritten_build_is_refused_and_a_re_pointed_symlink_records_the_original(self) -> None:
        self.fake = FakeClaude(during=lambda: self.real.write_bytes(elf("changed")))
        code, _, err = self.run_cli()
        self.assertEqual((code, "build_changed" in err), (20, True))
        self.assertFalse((self.root / cb.RECORD_NAME).exists())

    def test_a_symlinked_record_exits_20_and_leaves_the_link_target_untouched(self) -> None:
        self.root.mkdir()
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text("untouched")
        (self.root / cb.RECORD_NAME).symlink_to(target)
        code, _, err = self.run_cli()
        self.assertEqual(code, 20)
        self.assertIn("was not recorded", err)
        self.assertEqual(target.read_text(), "untouched")
        self.assertTrue((self.root / cb.RECORD_NAME).is_symlink())

    def test_an_admitted_build_is_reported_without_a_probe_unless_forced(self) -> None:
        self.assertEqual(self.run_cli()[0], 0)
        launches = self.fake.launches
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("already admitted", out)
        self.assertEqual(self.fake.launches, launches)
        code, out, _ = self.run_cli("--force")
        self.assertEqual(code, 0)
        self.assertGreater(self.fake.launches, launches)

    def test_a_built_in_build_is_reported_without_a_probe(self) -> None:
        digest = hashlib.sha256(elf("real")).hexdigest()
        with mock.patch.dict(fr.VERIFIED_NATIVE_BUILDS, {"2.1.303": digest}):
            code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("built in", out)
        self.assertEqual(self.fake.launches, 0)

    def test_a_different_claude_binary_is_verified_with_the_launch_differs_note(self) -> None:
        other = self.real.parent / "claude-other"
        other.write_bytes(elf("other"))
        other.chmod(0o755)
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.cli.main(["--runtime-dir", str(self.root), "--settings", str(self.settings),
                                  "--claude-binary", str(other), "verify-claude"])
        self.assertEqual(code, 0)
        self.assertIn("a launch runs", out.getvalue())
        self.assertEqual(set(cb.read_verified(self.root)[0]), {hashlib.sha256(elf("other")).hexdigest()})

    def test_the_settings_file_is_never_created_filled_or_updated(self) -> None:
        self.settings.parent.mkdir()
        self.settings.write_text('{"schema_version": 1}\n')  # an existing file with missing defaults
        before = self.settings.read_bytes()
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(self.settings.read_bytes(), before)
        self.fake = FakeClaude("socket")
        self.assertEqual(self.run_cli("--force")[0], 20)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_the_command_is_not_read_only_and_the_help_states_the_cost(self) -> None:
        self.assertNotIn("verify-claude", self.cli.READ_ONLY_COMMANDS)
        self.assertIn("verify-claude", self.cli.SETTINGS_READ_ONLY_COMMANDS)
        self.assertIn("model turns", self.cli.build_parser().format_help() + self._help())

    def _help(self) -> str:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.cli.build_parser().parse_args(["verify-claude", "--help"])
        return " ".join(out.getvalue().split())
