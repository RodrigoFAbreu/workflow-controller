"""The enforcement spike on the pinned Claude CLI
(``workflow-controller-automated-functional-review`` CP2, D2, D5 I2, D10).

Two groups. The unit tests need no process: the run settings, the launch flags,
the static probe and the probe's judge. The **live** tests run the real
``claude`` under the real sandbox settings (a fake tester cannot prove Claude's
own enforcement) and assert, on the host, that nothing the sandbox should have
refused happened. They are skipped when ``claude`` or ``bwrap`` is absent, or
when the CLI cannot reach a model (not logged in, a usage limit); each run is
one short model turn at low effort, except the missing-component run, which the
CLI refuses before any model call.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import claude_build as cb  # noqa: E402
from controller import functional_review as fr  # noqa: E402

CLAUDE = shutil.which("claude")
LIVE_MODEL = "claude-sonnet-5-5"
TRACKED = "src.txt"
CHECKLIST = "CHECKLIST.md"
ORIGINAL = b"original bytes\n"


def sandbox_dirs(base: Path) -> dict[str, Path]:
    """A sandbox folder shaped like D3's: ``clone/`` with a ``.git`` and two
    tracked files, an empty writable location, ``out/``, and scratch dirs."""
    clone = base / "clone"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "config").write_text("[core]\n")
    (clone / TRACKED).write_bytes(ORIGINAL)
    (clone / CHECKLIST).write_bytes(ORIGINAL)
    fr.make_writable_links(clone, base / "writable", ["build"])
    scratch = [base / "home", base / "tmp"]
    out = base / "out"
    for path in (out, *scratch):
        path.mkdir()
    return {"clone": clone, "out": out, "home": scratch[0], "tmp": scratch[1]}


class SettingsTests(unittest.TestCase):
    def build(self) -> dict:
        base = Path("/work")
        return fr.tester_settings(
            clone_dir=base / "clone", output_dir=base / "out", scratch_dirs=[base / "home", base / "tmp"],
            writable_dir=base / "writable", writable=["build", ".venv"])

    def test_the_sandbox_is_required_closed_and_without_an_unsandboxed_retry(self) -> None:
        sandbox = self.build()["sandbox"]
        self.assertIs(sandbox["enabled"], True)
        self.assertIs(sandbox["failIfUnavailable"], True)
        self.assertIs(sandbox["allowUnsandboxedCommands"], False)
        self.assertEqual(sandbox["excludedCommands"], [])
        self.assertEqual(sandbox["network"], {"allowedDomains": [], "allowUnixSockets": []})

    def test_the_write_allowlist_holds_only_the_output_scratch_and_writable_locations(self) -> None:
        sandbox = self.build()["sandbox"]
        self.assertEqual(sandbox["filesystem"]["allowWrite"],
                         ["/work/out", "/work/home", "/work/tmp", "/work/writable/build", "/work/writable/.venv"])

    def test_an_allowance_equal_to_or_below_a_denial_is_refused_and_a_denial_below_an_allowance_is_fine(self) -> None:
        refused = {
            "a child of the clone root": {"allowWrite": ["/w/clone/build"], "denyWrite": ["/w/clone"]},
            "the clone root itself": {"allowWrite": ["/w/clone"], "denyWrite": ["/w/clone"]},
            "a child of .git": {"allowWrite": ["/w/clone/.git/hooks"], "denyWrite": ["/w/clone/.git"]},
            "an allow equal to a deny": {"allowWrite": ["/w/x"], "denyWrite": ["/w/x"]},
        }
        for name, filesystem in refused.items():
            with self.subTest(name), self.assertRaises(ValueError):
                fr.assert_writes_not_below_denies(filesystem)
        fr.assert_writes_not_below_denies({"allowWrite": ["/w"], "denyWrite": ["/w/clone"]})
        with self.assertRaises(ValueError):
            fr.tester_settings(clone_dir=Path("/w/clone"), output_dir=Path("/w/out"), scratch_dirs=[],
                               writable_dir=Path("/w/clone/writable"), writable=["build"])

    def test_the_deny_list_covers_the_clone_root_and_git_by_name(self) -> None:
        self.assertEqual(self.build()["sandbox"]["filesystem"]["denyWrite"], ["/work/clone", "/work/clone/.git"])

    def test_the_settings_document_is_plain_json(self) -> None:
        json.dumps(self.build())

    def test_the_flags_grant_the_shell_alone_and_answer_no_prompt(self) -> None:
        flags = fr.tester_flags(Path("/s/settings.json"))
        self.assertEqual(flags[flags.index("--tools") + 1], "Bash")
        self.assertEqual(flags[flags.index("--allowedTools") + 1], "Bash")
        self.assertEqual(flags[flags.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(flags[flags.index("--permission-prompts") + 1], "none")
        self.assertEqual(flags[flags.index("--settings") + 1], "/s/settings.json")
        self.assertEqual(fr.TESTER_TOOLS, ("Bash",))


class FakeSandboxEmulationTests(unittest.TestCase):
    """The fake tester judges a write as Claude Code 2.1.304 and later do (D5), so a layout those builds refuse
    fails the fake suite too."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wfr-fake-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        sys.path.insert(0, str(REPO_ROOT / "tests"))
        import fake_functional_tester  # noqa: E402
        self.fake = fake_functional_tester
        self.dirs = sandbox_dirs(self.tmp)
        self.settings = fr.tester_settings(
            clone_dir=self.dirs["clone"], output_dir=self.dirs["out"], scratch_dirs=[self.dirs["home"], self.dirs["tmp"]],
            writable_dir=self.tmp / "writable", writable=["build"])

    def sandbox(self, settings: dict | None = None):
        return self.fake.Sandbox(settings or self.settings, True)

    def test_a_write_through_the_link_reaches_the_external_folder_and_is_allowed(self) -> None:
        box = self.sandbox()
        self.assertTrue(box.allows(self.dirs["clone"] / "build" / "out.o"))
        self.assertTrue(box.allows(self.dirs["out"] / "RESULTS.json"))

    def test_the_replacement_of_the_link_is_judged_at_the_clone_and_refused(self) -> None:
        box = self.sandbox()
        self.assertFalse(box.allows(self.dirs["clone"] / "build", parent_only=True))

    def test_every_tracked_path_a_new_file_and_git_stay_refused(self) -> None:
        box = self.sandbox()
        for path in (TRACKED, CHECKLIST, "new-file", ".git/config", "build2/x"):
            with self.subTest(path):
                self.assertFalse(box.allows(self.dirs["clone"] / path))

    def test_a_path_in_no_allowance_is_refused(self) -> None:
        self.assertFalse(self.sandbox().allows(self.tmp / "elsewhere"))

    def test_the_previous_layout_is_refused_as_the_real_builds_refuse_it(self) -> None:
        clone = self.dirs["clone"]
        old = {"sandbox": {"filesystem": {"allowWrite": [str(self.dirs["out"]), str(clone / "build")],
                                          "denyWrite": [str(clone), str(clone / ".git")]}}}
        (clone / "build").unlink()
        (clone / "build").mkdir()
        self.assertFalse(self.sandbox(old).allows(clone / "build" / "out.o"))
        self.assertTrue(self.sandbox(old).allows(self.dirs["out"] / "x"))

    def test_a_denial_below_an_allowance_still_refuses(self) -> None:
        settings = {"sandbox": {"filesystem": {"allowWrite": [str(self.tmp)], "denyWrite": [str(self.tmp / "clone")]}}}
        box = self.sandbox(settings)
        self.assertTrue(box.allows(self.tmp / "other"))
        self.assertFalse(box.allows(self.dirs["clone"] / TRACKED))

    def test_two_copies_made_by_the_copy_rule_do_not_meet(self) -> None:
        (self.dirs["clone"] / "build" / "seed").write_text("s")
        tmpdir = self.dirs["tmp"]
        log: list[dict] = []
        ctx = {"sandbox": self.sandbox(), "clone": self.dirs["clone"], "record": {"actions": log}}
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmpdir)}):
            for index in range(2):
                self.fake._act({"op": "copy_rule", "name": f"copy{index}", "path": f"build/only-{index}"}, ctx)
        first, second = tmpdir / "copy0", tmpdir / "copy1"
        self.assertEqual((first / "build" / "seed").read_text(), "s")
        self.assertTrue((first / "build" / "only-0").exists())
        self.assertFalse((second / "build" / "only-0").exists())
        self.assertFalse((first / "build").is_symlink())
        self.assertEqual(sorted(p.name for p in (self.tmp / "writable" / "build").iterdir()), ["seed"])
        self.assertEqual(sorted(p.name for p in (self.dirs["clone"] / "build").iterdir()), ["seed"])


class StaticProbeTests(unittest.TestCase):
    def test_a_missing_program_ends_at_the_unavailable_gate_naming_it(self) -> None:
        for gone in fr.REQUIRED_PROGRAMS:
            with self.subTest(gone=gone):
                which = lambda name: None if name == gone else f"/usr/bin/{name}"  # noqa: E731
                with self.assertRaises(fr.TesterUnavailable) as caught:
                    fr.static_probe(which=which, run=mock.Mock())
                self.assertIn(gone, str(caught.exception))
                self.assertEqual(caught.exception.code, fr.GATE_TESTER_UNAVAILABLE)

    @staticmethod
    def probe(run, *, native=True, claude="/usr/bin/claude"):
        which = lambda name: claude if name == "claude" else f"/usr/bin/{name}"  # noqa: E731
        fr.static_probe(which=which, run=run, is_native=lambda path: native)

    @staticmethod
    def ok(code=0, err=b""):
        return subprocess.CompletedProcess([], code, b"", err)

    def test_a_bwrap_that_cannot_make_a_namespace_is_unavailable(self) -> None:
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.probe(mock.Mock(return_value=self.ok(1, b"bwrap: No permissions to create")))
        self.assertIn("No permissions", str(caught.exception))

    def test_a_bwrap_that_cannot_be_run_is_unavailable(self) -> None:
        with self.assertRaises(fr.TesterUnavailable):
            self.probe(mock.Mock(side_effect=OSError("denied")))

    def test_a_kernel_without_seccomp_filters_is_unavailable(self) -> None:
        run = mock.Mock(side_effect=[self.ok(), self.ok(3)])
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.probe(run)
        self.assertIn("seccomp", str(caught.exception))
        self.assertEqual(caught.exception.code, fr.GATE_TESTER_UNAVAILABLE)

    def test_a_seccomp_check_that_cannot_be_run_is_unavailable(self) -> None:
        with self.assertRaises(fr.TesterUnavailable):
            self.probe(mock.Mock(side_effect=[self.ok(), OSError("denied")]))

    def test_a_claude_that_is_not_the_native_build_is_unavailable(self) -> None:
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.probe(mock.Mock(return_value=self.ok()), native=False)
        self.assertIn("native", str(caught.exception))

    def test_a_missing_claude_is_unavailable(self) -> None:
        which = lambda name: None if name == "claude" else f"/usr/bin/{name}"  # noqa: E731
        with self.assertRaises(fr.TesterUnavailable):
            fr.static_probe(which=which, run=mock.Mock(return_value=self.ok()), is_native=lambda path: True)

    def test_the_native_check_admits_only_a_verified_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            native, launcher, script = Path(tmp, "native"), Path(tmp, "launcher"), Path(tmp, "script")
            native.write_bytes(b"\x7fELF\x02" + b"x" * 5000 + b"verified build")
            verified = {"9.9.9": hashlib.sha256(native.read_bytes()).hexdigest()}
            # A compiled launcher that forwards to the script CLI has the magic but another digest.
            launcher.write_bytes(b"\x7fELF\x02" + b"x" * 5000 + b"exec node cli.js")
            script.write_text("#!/usr/bin/env node\napply-seccomp\n")
            self.assertTrue(fr._is_native_executable(str(native), verified=verified))
            self.assertFalse(fr._is_native_executable(str(launcher), verified=verified))
            self.assertFalse(fr._is_native_executable(str(script), verified=verified))
            self.assertFalse(fr._is_native_executable(str(Path(tmp, "absent")), verified=verified))
            # A newer build is refused until its digest is added.
            self.assertFalse(fr._is_native_executable(str(native), verified={}))
            # A symlink resolves to the file that would be executed.
            link = Path(tmp, "link")
            link.symlink_to(native)
            self.assertTrue(fr._is_native_executable(str(link), verified=verified))

    def test_a_helperless_elf_that_names_the_helper_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp, "claude")
            # A forwarding ELF whose diagnostic text names the helper (I1): the name proves nothing.
            bundled.write_bytes(b"\x7fELF\x02" + b"x" * 5000 + b"warning: apply-seccomp not found; exec node cli.js")
            with self.assertRaises(fr.TesterUnavailable) as caught:
                fr.static_probe(which=lambda name: str(bundled) if name == "claude" else f"/usr/bin/{name}",
                                run=mock.Mock(return_value=self.ok()))
            self.assertIn("verified native", str(caught.exception))
            self.assertEqual(caught.exception.code, fr.GATE_TESTER_UNAVAILABLE)

    def test_a_verified_build_passes_the_probe_by_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp, "claude")
            native.write_bytes(b"\x7fELF\x02" + b"verified build")
            digest = hashlib.sha256(native.read_bytes()).hexdigest()
            with mock.patch.dict(fr.VERIFIED_NATIVE_BUILDS, {"9.9.9": digest}):
                fr.static_probe(which=lambda name: str(native) if name == "claude" else f"/usr/bin/{name}",
                                run=mock.Mock(return_value=self.ok()))

    def test_a_forwarding_elf_launcher_is_refused_by_the_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp, "claude")
            launcher.write_bytes(b"\x7fELF\x02" + b"exec node cli.js")
            with self.assertRaises(fr.TesterUnavailable) as caught:
                fr.static_probe(which=lambda name: str(launcher) if name == "claude" else f"/usr/bin/{name}",
                                run=mock.Mock(return_value=self.ok()))
            self.assertIn("native", str(caught.exception))

    def test_a_working_host_passes(self) -> None:
        run = mock.Mock(return_value=self.ok())
        self.probe(run)
        self.assertEqual(run.call_args_list[0].args[0][0], "bwrap")
        self.assertEqual(len(run.call_args_list), 2)

    @unittest.skipUnless(CLAUDE and shutil.which("bwrap") and shutil.which("socat"), "claude, bwrap or socat is absent")
    def test_this_host_passes_the_static_probe(self) -> None:
        if not fr._is_native_executable(CLAUDE):
            self.skipTest("the installed claude is not a verified native build (VERIFIED_NATIVE_BUILDS)")
        fr.static_probe()

    def test_the_cli_refusal_is_recognised_and_other_results_are_not(self) -> None:
        text = ("Error: sandbox required but unavailable: sandbox is enabled but dependencies are missing: "
                "bubblewrap (bwrap) not installed\n  sandbox.failIfUnavailable is set")
        refusal = fr.launch_refusal(text)
        self.assertIsInstance(refusal, fr.TesterUnavailable)
        self.assertIn("bwrap", str(refusal))
        self.assertIsNotNone(fr.launch_refusal("Sandbox required but unavailable: dependencies are missing"))
        self.assertIsNone(fr.launch_refusal("all flows done"))

    def test_the_result_text_joins_the_result_and_the_errors(self) -> None:
        self.assertEqual(fr.result_event_text({"result": "a", "errors": ["b", 3, "c"]}), "a\nb\nc")
        self.assertEqual(fr.result_event_text({"errors": "not a list"}), "")


class JudgeTests(unittest.TestCase):
    GOOD = "\n".join(f"PROBE {name} {want}" for name, want in fr.PROBE_EXPECTED.items()) + "\nPROBE shm WROTE\n"

    def test_the_expected_output_holds(self) -> None:
        self.assertEqual(fr.judge_probe(self.GOOD), [])

    def test_each_wrong_result_is_named(self) -> None:
        for name, want in fr.PROBE_EXPECTED.items():
            with self.subTest(name=name):
                wrong = "WROTE" if want != "WROTE" else "REFUSED"
                if name in ("tcp", "unix"):
                    wrong = "CONNECTED"
                bad = self.GOOD.replace(f"PROBE {name} {want}", f"PROBE {name} {wrong}")
                failures = fr.judge_probe(bad)
                self.assertEqual(len(failures), 1)
                self.assertTrue(failures[0].startswith(name))

    def test_a_missing_line_and_garbage_fail_closed(self) -> None:
        self.assertEqual(len(fr.judge_probe(self.GOOD.replace("PROBE unix BLOCKED\n", ""))), 1)
        self.assertEqual(len(fr.judge_probe("")), len(fr.PROBE_EXPECTED))
        self.assertEqual(len(fr.judge_probe("I could not run the command")), len(fr.PROBE_EXPECTED))

    def test_a_contradictory_repeat_is_a_failure(self) -> None:
        self.assertTrue(any("both" in item for item in fr.judge_probe(self.GOOD + "PROBE tracked WROTE\n")))

    def test_the_command_is_valid_shell_and_names_every_probe(self) -> None:
        targets = fr.ProbeTargets(
            clone_dir=Path("/w/clone"), output_dir=Path("/w/out"), tracked=TRACKED, checklist=CHECKLIST,
            writable="build", stand_in_home=Path("/w/home"), socket_path=Path("/w/s.sock"), tcp_port=9)
        command = fr.probe_command(targets, marker="m1")
        subprocess.run(["sh", "-n"], input=command.encode(), check=True)
        for name in ("rename", "delete", "tcp", "unix"):
            self.assertIn(f"PROBE {name} ", command)
        for name in set(fr.PROBE_EXPECTED) - {"rename", "delete", "tcp", "unix", "link"} | {"shm"}:
            self.assertIn(f"\nw {name} ", command)
        self.assertIn("PROBE link ", command)
        self.assertIn("rm -f /w/clone/build 2>/dev/null && mkdir /w/clone/build", command)
        self.assertIn("/w/clone/.git/config", command)
        self.assertIn("/var/tmp/m1", command)

    def test_the_link_probe_replaces_the_link_where_nothing_refuses_it(self) -> None:
        """The positive control of the link probe: without a sandbox the link is replaced, so only enforcement can make
        the live probe's REFUSED."""
        with tempfile.TemporaryDirectory() as tmp:
            dirs = sandbox_dirs(Path(tmp))
            targets = fr.ProbeTargets(
                clone_dir=dirs["clone"], output_dir=dirs["out"], tracked=TRACKED, checklist=CHECKLIST,
                writable="build", stand_in_home=Path(tmp, "home"), socket_path=Path(tmp, "s"), tcp_port=9)
            lines = fr.probe_command(targets, marker="m1").splitlines()
            script = "\n".join([lines[0], next(line for line in lines if "PROBE link" in line)])
            done = subprocess.run(["sh"], input=script.encode(), capture_output=True, check=True)
            self.assertEqual(done.stdout.decode().strip(), "PROBE link WROTE")
            self.assertFalse((dirs["clone"] / "build").is_symlink())

    def test_the_home_probe_writes_where_nothing_refuses_it(self) -> None:
        """The positive control of the home probe: without a sandbox, the line writes, so only enforcement can make
        the live probe's REFUSED."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp, "home")
            (home / ".config").mkdir(parents=True)
            targets = fr.ProbeTargets(
                clone_dir=Path(tmp, "clone"), output_dir=Path(tmp, "out"), tracked=TRACKED, checklist=CHECKLIST,
                writable="build", stand_in_home=home, socket_path=Path(tmp, "s"), tcp_port=9)
            lines = fr.probe_command(targets, marker="m1").splitlines()
            script = "\n".join([lines[0], next(line for line in lines if line.startswith("w home "))])
            done = subprocess.run(["sh"], input=script.encode(), capture_output=True, check=True)
            self.assertEqual(done.stdout.decode().strip(), "PROBE home WROTE")
            self.assertTrue((home / ".config" / "probe").exists())


def run_claude(prompt: str, *, cwd: Path, settings: dict, env_path: str | None = None) -> tuple[dict, list[dict]]:
    """One tester-shaped launch of the real CLI: the closed flags of
    :func:`fr.tester_flags`, the working directory the output folder. Returns
    the final ``result`` event and every event."""
    settings_path = cwd.parent / "settings.json"
    settings_path.write_text(json.dumps(settings))
    command = [CLAUDE, "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
               "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--disable-slash-commands", "--model", LIVE_MODEL, "--effort", "low", *fr.tester_flags(settings_path)]
    # The opted-in launch gets its own home and only the authentication the operator names: never the real
    # ~/.claude, a CLAUDE_CONFIG_DIR or the rest of the environment.
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(cwd.parent / "claude-home")}
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        if name in os.environ:
            env[name] = os.environ[name]
    Path(env["HOME"]).mkdir(exist_ok=True)
    if env_path is not None:
        env["PATH"] = env_path
    done = subprocess.run(command, input=prompt.encode(), capture_output=True, cwd=cwd, env=env, timeout=300,
                          check=False)
    events = []
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    results = [event for event in events if event.get("type") == "result"]
    if not results:
        raise AssertionError(f"no result event; stderr: {done.stderr.decode('utf-8', 'replace')[:500]}")
    return results[-1], events


def result_text(result: dict) -> str:
    return fr.result_event_text(result) or json.dumps(result)


LIVE = os.environ.get("CONTROLLER_LIVE_CLAUDE_TESTS") == "1"


@unittest.skipUnless(LIVE, "live model probes run only with CONTROLLER_LIVE_CLAUDE_TESTS=1 (they spend model usage)")
@unittest.skipUnless(CLAUDE and shutil.which("bwrap") and shutil.which("socat"), "claude, bwrap or socat is absent")
class LiveEnforcementTests(unittest.TestCase):
    """The probes of D10 on the pinned CLI, under the real sandbox settings."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="wfr-cp2-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.dirs = sandbox_dirs(self.base)
        self.settings = fr.tester_settings(
            clone_dir=self.dirs["clone"], output_dir=self.dirs["out"],
            scratch_dirs=[self.dirs["home"], self.dirs["tmp"]], writable_dir=self.base / "writable",
            writable=["build"])
        self.marker = f"wfr-cp2-{uuid.uuid4().hex}"
        self.host_paths = [Path("/tmp") / self.marker, Path("/var/tmp") / self.marker, Path("/dev/shm") / self.marker]
        for path in self.host_paths:
            self.addCleanup(lambda p=path: p.unlink() if p.exists() else None)

    def _skip_without_a_model(self, result: dict) -> None:
        text = result_text(result).lower()
        if result.get("is_error") and any(word in text for word in ("login", "credit", "limit", "authenticat", "api key")):
            self.skipTest(f"the CLI cannot reach a model: {text[:120]}")

    # The three probes below are ``claude_build.verify_build``'s own, the ones ``workflow-controller verify-claude``
    # runs, so the release check and the command cannot drift apart (workflow-controller-claude-build-verify D1).
    # The fourth test stays release-side only: it asserts a write is *refused* in a configuration the Controller never
    # uses, so a build where it changes does not weaken the boundary and it is not a condition of admission.

    def _probe(self, name: str) -> cb.Verification:
        try:
            return cb.verify_build(CLAUDE, runtime_root=self.base / "runtime", operator_home=Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"),
                                   probes=[name])
        except cb.ModelUnreachable as exc:
            self.skipTest(str(exc))

    def test_the_boundary_holds_on_the_pinned_cli(self) -> None:
        self.assertEqual([p.name for p in self._probe("boundary").probes], ["boundary"])

    def test_an_unsandboxed_retry_and_a_file_tool_change_nothing_on_the_host(self) -> None:
        self.assertEqual([p.name for p in self._probe("retry").probes], ["retry"])

    def test_a_launch_without_an_enforcement_component_is_refused_before_any_model_call(self) -> None:
        self.assertEqual([p.name for p in self._probe("missing_component").probes], ["missing_component"])

    def test_a_launch_from_above_the_clone_still_cannot_write_a_tracked_file(self) -> None:
        """Release-side only: the working directory is the output folder in every launch the Controller makes; from a
        directory above the clone the denial of the clone root must still hold."""
        target = self.dirs["clone"] / TRACKED
        result, _ = run_claude(f"Run `echo hi > {target}; echo done` with the Bash tool and report the output.",
                               cwd=self.base, settings=self.settings)
        self._skip_without_a_model(result)
        self.assertEqual(target.read_bytes(), ORIGINAL)

if __name__ == "__main__":
    unittest.main()
