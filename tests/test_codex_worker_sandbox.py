"""`workflow-controller-codex-worker-sandbox` CP1 and CP2: the Codex worker sandbox builder, probes and git guard
(`controller.codex_worker_sandbox`). Offline: no `codex`, no network, no sandbox run; the
runtime is a temporary folder, the worktree a real temporary clone made with `git`, and the
listing controls use a fake runner.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_sandbox as cs, codex_worker_sandbox as ws, review  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402

NO_SERVERS = b"No MCP servers configured yet.\n"
FEATURES = b"shell_tool    stable    true\nbrowser_use   stable    false\n"


def result(stdout: bytes = b"", code: int = 0, timed_out: bool = False) -> review.RunResult:
    return review.RunResult(code, timed_out, stdout, b"boom", 1, 1)


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


def make_clone(path: Path) -> None:
    """A real repository with one tracked file, as a worker's worktree must be."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    (path / "src.txt").write_text("one\n")
    git(path, "add", "src.txt")
    git(path, "commit", "-q", "-m", "init")


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.runtime = self.tmp / "runtime"
        self.operator = self.tmp / "operator-codex"
        self.operator.mkdir()
        (self.operator / "auth.json").write_text('{"token": "secret"}')
        self.real_home = self.tmp / "realhome"
        self.real_home.mkdir()
        self.prefix = self.real_home / ".nvm" / "v1"
        self.package = self.prefix / "lib" / "node_modules" / "@openai" / "codex"
        (self.package / "bin").mkdir(parents=True)
        (self.package / "package.json").write_text("{}")
        entry = self.package / "bin" / "codex.js"
        entry.write_text("#!/usr/bin/env node\n")
        entry.chmod(0o755)
        (self.prefix / "bin").mkdir()
        self.binary = self.prefix / "bin" / "codex"
        self.binary.symlink_to(entry)
        self.layout = ws.WorkerLayout(self.runtime, "job1")
        self.system = self.tmp / "etc-codex"
        self.system.mkdir()

    def build(self, **kw) -> ws.Built:
        kw.setdefault("runtime_root", self.runtime)
        kw.setdefault("job_id", "job1")
        kw.setdefault("operator_home", self.operator)
        kw.setdefault("binary", self.binary)
        kw.setdefault("system_dir", self.system)
        kw.setdefault("real_home", self.real_home)
        return ws.build(**kw)

    def roots(self, **kw) -> list[Path]:
        kw.setdefault("operator_home", self.real_home)
        return ws.readable_roots(self.layout, self.binary, **kw)


class LayoutTest(Fixture):
    def test_every_path_is_under_the_worker_area_of_the_job(self) -> None:
        base = self.runtime / "worker" / "job1"
        self.assertEqual(self.layout.base, base)
        self.assertEqual(self.layout.worktree, base / "tree")
        self.assertEqual(self.layout.home, base / "home")
        self.assertEqual(self.layout.codex_home, base / "codex-home")
        self.assertEqual(self.layout.last_message, base / "last-message.md")
        self.assertEqual(self.layout.scratch, base / "scratch")
        self.assertEqual(self.layout.scratch_tmp, base / "scratch" / "tmp")
        self.assertEqual(self.layout.fake_logs, base / "scratch" / "fake-logs")
        self.assertEqual(self.layout.bin, base / "bin")
        self.assertEqual(self.layout.rel("bin"), "worker/job1/bin")


class CheckWorktreeTest(Fixture):
    def test_a_real_clone_is_accepted(self) -> None:
        make_clone(self.tmp / "clone")
        ws.check_worktree(self.tmp / "clone")

    def test_a_missing_path_a_plain_file_and_a_missing_git_directory_refuse(self) -> None:
        (self.tmp / "file").write_text("x")
        (self.tmp / "bare").mkdir()
        for bad in (self.tmp / "missing", self.tmp / "file", self.tmp / "bare"):
            with self.subTest(path=bad.name), self.assertRaises(ReviewLaunchRefusedError):
                ws.check_worktree(bad)

    def test_a_symlinked_git_and_a_git_file_refuse_naming_the_cause(self) -> None:
        make_clone(self.tmp / "real")
        linked = self.tmp / "linked"
        linked.mkdir()
        (linked / ".git").symlink_to(self.tmp / "real" / ".git")
        with self.assertRaises(ReviewLaunchRefusedError) as ctx:
            ws.check_worktree(linked)
        self.assertIn("symlink", str(ctx.exception))
        filed = self.tmp / "filed"
        filed.mkdir()
        (filed / ".git").write_text(f"gitdir: {self.tmp / 'real' / '.git'}\n")
        with self.assertRaises(ReviewLaunchRefusedError) as ctx:
            ws.check_worktree(filed)
        self.assertIn("file", str(ctx.exception))

    def test_a_symlinked_worktree_root_refuses(self) -> None:
        make_clone(self.tmp / "real")
        (self.tmp / "alias").symlink_to(self.tmp / "real")
        with self.assertRaises(ReviewLaunchRefusedError):
            ws.check_worktree(self.tmp / "alias")


class ReadableRootsTest(Fixture):
    def test_the_controller_package_directory_is_accepted_and_its_parent_is_not_listed(self) -> None:
        roots = self.roots()
        self.assertIn(REPO_ROOT / "controller", roots)
        self.assertNotIn(REPO_ROOT, roots)
        self.assertIn(self.layout.bin, roots)

    def test_roots_overlapping_the_job_the_worktree_the_runtime_or_the_codex_home_refuse(self) -> None:
        inside = self.layout.worktree / "sub"
        for bad in (self.layout.base, self.layout.base.parent, self.layout.runtime_root, self.runtime.parent,
                    self.layout.codex_home, self.layout.worktree, inside, self.layout.scratch,
                    self.layout.scratch_tmp, self.layout.codex_home / "sessions"):
            with self.subTest(root=str(bad)), self.assertRaises(ReviewLaunchRefusedError):
                self.roots(extra=[bad])
            with self.subTest(build=str(bad)), self.assertRaises(ReviewLaunchRefusedError):
                self.build(extra_readable=[bad])

    def test_root_the_home_and_ancestors_of_the_home_refuse(self) -> None:
        for bad in (Path("/"), self.real_home, self.real_home.parent):
            with self.subTest(root=str(bad)), self.assertRaises(ReviewLaunchRefusedError):
                self.roots(extra=[bad])

    def test_a_symlink_to_a_container_of_the_job_refuses(self) -> None:
        link = self.tmp / "link"
        link.symlink_to(self.runtime.parent)
        self.runtime.mkdir()
        with self.assertRaises(ReviewLaunchRefusedError):
            self.roots(extra=[link])


class ProfileTest(Fixture):
    def test_the_profile_lists_worktree_and_scratch_write_roots_read_and_the_git_entries_read(self) -> None:
        built = self.build()
        text = built.config_text
        tree = self.layout.worktree
        self.assertIn(f'"{tree}" = "write"', text)
        self.assertIn(f'"{self.layout.scratch}" = "write"', text)
        self.assertIn(f'"{tree}/.git" = "write"', text)  # live-measured: Codex keeps .git read-only unless listed
        self.assertIn(f'"{tree}/.git/hooks" = "read"', text)
        self.assertIn(f'"{tree}/.git/config" = "read"', text)
        self.assertIn('"/usr" = "read"', text)
        for root in built.roots:
            self.assertIn(f'"{root}" = "read"', text)
        self.assertIn('default_permissions = "worker"', text)
        self.assertIn("[permissions.worker.network]\nenabled = false", text)
        self.assertEqual(text.count('= "write"'), 3)
        parsed = tomllib.loads(text)["permissions"]["worker"]["filesystem"]
        self.assertEqual(parsed[str(tree)], "write")
        self.assertEqual(parsed[f"{tree}/.git"], "write")
        self.assertEqual(parsed[f"{tree}/.git/config"], "read")

    def test_nothing_of_the_operators_home_the_runtime_a_sibling_job_or_the_credential_is_listed(self) -> None:
        text = self.build().config_text
        for absent in (str(self.real_home), str(self.runtime), str(self.layout.base), str(self.layout.codex_home),
                       str(self.operator), "auth.json", str(self.runtime / "worker" / "job2")):
            self.assertNotIn(f'"{absent}"', text)
        self.assertNotIn("auth.json", text)

    def test_the_same_inputs_give_identical_bytes(self) -> None:
        roots = self.roots()
        self.assertEqual(ws.config_toml(self.layout, roots), ws.config_toml(self.layout, list(reversed(roots))))

    def test_a_path_with_a_quote_a_newline_or_a_relative_form_refuses(self) -> None:
        for bad in ('/tmp/a"b', "/tmp/a\nb", "/tmp/a\\b", "relative/path", "/tmp/a\x00b"):
            with self.subTest(path=bad), self.assertRaises(ReviewLaunchRefusedError):
                ws.profile_toml(self.layout, [Path(bad)])
        with self.assertRaises(ReviewLaunchRefusedError):
            ws.profile_toml(ws.WorkerLayout(self.tmp / 'a"b', "job1"), [])


class EnvironmentTest(Fixture):
    def test_the_environment_is_exactly_the_allowlist_with_the_scratch_tmpdir(self) -> None:
        env = ws.child_environment(self.layout, self.binary, "tag", {"CANARY": "leak", "AWS_SECRET": "x"})
        self.assertEqual(set(env), set(review.ENV_ALLOWLIST))
        self.assertNotIn("leak", env.values())
        self.assertEqual(env["TMPDIR"], str(self.layout.scratch_tmp))
        self.assertEqual(env["CODEX_HOME"], str(self.layout.codex_home))

    def test_the_command_environment_carries_no_identity_and_no_config(self) -> None:
        env = ws.command_environment(self.layout)
        self.assertEqual(set(env), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM",
                                    "GIT_TERMINAL_PROMPT"})
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["HOME"], str(self.layout.home))
        self.assertEqual(env["TMPDIR"], str(self.layout.scratch_tmp))
        self.assertFalse([key for key in env if key.startswith(("GIT_AUTHOR", "GIT_COMMITTER", "EMAIL"))])

    def test_the_shell_policy_inherits_nothing_and_puts_the_launchers_first(self) -> None:
        inherit, set_ = ws.shell_policy_overrides(self.layout)
        self.assertEqual(inherit, 'shell_environment_policy.inherit="none"')
        self.assertIn(f'PATH="{self.layout.bin}:{review.SYSTEM_PATH}"', set_)
        self.assertIn('GIT_CONFIG_NOSYSTEM="1"', set_)


class ArgvTest(Fixture):
    def command(self) -> list[str]:
        return ws.argv(self.layout, binary=self.binary)

    def test_the_argv_is_the_documented_line(self) -> None:
        command = self.command()
        expected = [str(self.binary), "exec", "--strict-config"]
        for feature in review.DISABLED_FEATURES:
            expected += ["--disable", feature]
        for item in ws.required_overrides(self.layout):
            expected += ["-c", item]
        expected += ["-C", str(self.layout.worktree), "--ignore-rules", "--color", "never", "-o",
                     str(self.layout.last_message), "-"]
        self.assertEqual(command, expected)
        present = [command[i + 1] for i, a in enumerate(command) if a == "-c"]
        self.assertIn('default_permissions="worker"', present)
        self.assertIn(f'projects={{"{self.layout.worktree}"={{trust_level="untrusted"}}}}', present)
        self.assertNotIn("-s", command)
        self.assertFalse([c for c in present if c.startswith(("sandbox_mode", "sandbox_workspace_write"))])
        ws.check_argv(command, self.layout)

    def test_a_prompt_replaces_the_standard_input_marker(self) -> None:
        self.assertEqual(ws.argv(self.layout, binary=self.binary, prompt="do it")[-1], "do it")

    def test_each_departure_from_the_pinned_line_refuses(self) -> None:
        good = self.command()
        item = next(i for i, a in enumerate(good) if a == "-c" and good[i + 1].startswith("approval_policy"))
        cases = {
            "omitted override": good[:item] + good[item + 2:],
            "extra -c": [*good[:-1], "-c", 'sandbox_mode="danger-full-access"', good[-1]],
            "equivalent spelling": [*good[:-1], "--config=x=1", good[-1]],
            "-s": [*good[:-1], "-s", "workspace-write", good[-1]],
            "--sandbox": [*good[:-1], "--sandbox", "workspace-write", good[-1]],
            "--add-dir": [*good[:-1], "--add-dir", "/", good[-1]],
            "--full-auto": [*good[:-1], "--full-auto", good[-1]],
            "different -C": [("/elsewhere" if good[i - 1] == "-C" else a) for i, a in enumerate(good)],
            "different -o": [("/elsewhere/out" if good[i - 1] == "-o" else a) for i, a in enumerate(good)],
            "no strict config": [a for a in good if a != "--strict-config"],
            "no ignore rules": [a for a in good if a != "--ignore-rules"],
            "a feature not disabled": [a for i, a in enumerate(good) if a != "--disable" and good[i - 1] != "--disable"],
        }
        for name, command in cases.items():
            with self.subTest(name=name), self.assertRaises(ReviewLaunchRefusedError):
                ws.check_argv(command, self.layout)

    def test_the_pinned_items_other_than_profile_tree_and_environment_equal_the_testers(self) -> None:
        tester = cs.TesterLayout(self.runtime, "job1")
        own = {"projects=", "default_permissions=", "shell_environment_policy.set="}

        def common(items) -> list[str]:
            return [item for item in items if not item.startswith(tuple(own))]

        self.assertEqual(common(ws.required_overrides(self.layout)), common(cs.required_overrides(tester)))
        self.assertEqual(ws.BANNED_FLAGS, cs.BANNED_FLAGS)
        theirs, ours = cs.argv(tester, binary=self.binary), self.command()
        self.assertEqual(theirs[:3 + 2 * len(review.DISABLED_FEATURES)], ours[:3 + 2 * len(review.DISABLED_FEATURES)])


class CheckHomeTest(Fixture):
    def test_the_written_home_passes_and_each_change_refuses_naming_the_workers_home(self) -> None:
        built = self.build()
        home = self.layout.codex_home
        ws.check_home(self.layout, built.config_text)
        (home / "sessions" / "x.sqlite").write_text("")
        ws.check_home(self.layout, built.config_text)
        for name in ("hooks.json", "skills/x", "mystery", "managed_config.toml"):
            with self.subTest(name=name):
                target = home / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("")
                with self.assertRaises(ReviewLaunchRefusedError) as ctx:
                    ws.check_home(self.layout, built.config_text)
                self.assertIn("worker's Codex home", str(ctx.exception))
                target.unlink()
                if target.parent != home:
                    target.parent.rmdir()
        (home / "config.toml").write_text(built.config_text + "# edited\n")
        with self.assertRaises(ReviewLaunchRefusedError) as ctx:
            ws.check_home(self.layout, built.config_text)
        self.assertIn("worker's Codex home", str(ctx.exception))

    def test_the_testers_text_is_not_the_workers(self) -> None:
        built = self.build()
        (self.layout.codex_home / "config.toml").write_text(review.HOME_CONFIG_TOML)
        with self.assertRaises(ReviewLaunchRefusedError):
            ws.check_home(self.layout, built.config_text)


class CheckLaunchTest(Fixture):
    def launch(self, run, **kw) -> None:
        built = self.build()
        ws.check_launch(built.layout, built.config_text, self.binary, built.environment, system_dir=self.system,
                        run=run, **kw)

    def test_a_system_layer_with_content_refuses_and_nothing_runs(self) -> None:
        for name in review.SYSTEM_LAYER_FILES:
            with self.subTest(name=name):
                built = self.build()
                (self.system / name).write_text('[mcp_servers.evil]\ncommand = "x"\n')
                run = mock.Mock()
                with self.assertRaises(ReviewLaunchRefusedError) as ctx:
                    ws.check_launch(built.layout, built.config_text, self.binary, built.environment,
                                    system_dir=self.system, run=run)
                self.assertIn(name, str(ctx.exception))
                run.assert_not_called()
                (self.system / name).unlink()

    def test_clean_listings_pass_in_order_in_the_worktree_and_check_the_home_around_them(self) -> None:
        calls, seen = [], []
        outputs = {"mcp": NO_SERVERS, "features": FEATURES}

        def run(command, *, cwd, env, timeout):
            calls.append(command[1])
            seen.append((cwd, env["CODEX_HOME"]))
            return result(outputs[command[1]])

        with mock.patch.object(ws, "check_home", wraps=ws.check_home) as home, \
                mock.patch.object(review, "check_outside_layers", wraps=review.check_outside_layers) as outside:
            self.launch(run)
        self.assertEqual(calls, ["mcp", "features"])
        self.assertEqual(seen, [(self.layout.worktree, str(self.layout.codex_home))] * 2)
        self.assertEqual(outside.call_count, 2)  # build and check_launch
        self.assertEqual(home.call_count, 1 + 3)  # build, then before mcp, before features, after both

    def test_the_testers_controls_are_never_called(self) -> None:
        run = mock.Mock(side_effect=lambda command, **kw: result(NO_SERVERS if command[1] == "mcp" else FEATURES))
        with mock.patch.object(cs, "check_home") as tester_home, mock.patch.object(cs, "check_launch") as tester_launch:
            self.launch(run)
        tester_home.assert_not_called()
        tester_launch.assert_not_called()

    def test_a_reported_server_a_forbidden_feature_and_a_failed_listing_refuse(self) -> None:
        cases = {
            "server": lambda c, **kw: result(b"zz   enabled   stdio\n" if c[1] == "mcp" else FEATURES),
            "feature": lambda c, **kw: result(NO_SERVERS if c[1] == "mcp" else b"browser_use  stable  true\n"),
            "exit": lambda c, **kw: result(b"", code=2),
            "timeout": lambda c, **kw: result(b"", code=None, timed_out=True),
        }
        for name, run in cases.items():
            with self.subTest(name=name), self.assertRaises(ReviewLaunchRefusedError):
                self.launch(run)

    def test_a_refusal_stops_the_later_controls(self) -> None:
        run = mock.Mock(return_value=result(b"zz   enabled\n"))
        with self.assertRaises(ReviewLaunchRefusedError):
            self.launch(run)
        self.assertEqual(run.call_count, 1)  # `features list` never ran

    def test_a_home_changed_by_a_listing_refuses_after_both(self) -> None:
        def run(command, **kw):
            if command[1] == "features":
                (self.layout.codex_home / "hooks.json").write_text("{}")
            return result(NO_SERVERS if command[1] == "mcp" else FEATURES)

        with self.assertRaises(ReviewLaunchRefusedError) as ctx:
            self.launch(run)
        self.assertIn("hooks.json", str(ctx.exception))


class BuildTest(Fixture):
    def test_build_writes_the_expected_files_and_starts_no_process(self) -> None:
        with mock.patch("subprocess.Popen") as popen:
            built = self.build()
        popen.assert_not_called()
        layout = built.layout
        self.assertEqual(layout, self.layout)
        self.assertEqual((layout.codex_home / "config.toml").read_text(), built.config_text)
        self.assertEqual(hashlib.sha256((layout.codex_home / "auth.json").read_bytes()).hexdigest(),
                         built.credential_digest)
        launchers = sorted(path.name for path in layout.bin.iterdir())
        self.assertTrue(launchers)
        self.assertIn("gh", launchers)
        for directory in (layout.scratch_tmp, layout.fake_logs, layout.home):
            self.assertTrue(directory.is_dir())
        ws.check_argv(list(built.argv), layout, model=built.model, effort=built.effort)
        self.assertEqual(list(built.argv), ws.argv(layout, binary=self.binary))
        self.assertEqual(built.binary, str(self.binary))
        self.assertEqual(built.environment["CODEX_HOME"], str(layout.codex_home))

    def test_build_writes_nothing_under_the_worktree(self) -> None:
        tree = self.layout.worktree
        make_clone(tree)
        before = sorted(str(p) for p in tree.rglob("*"))
        self.build()
        self.assertEqual(sorted(str(p) for p in tree.rglob("*")), before)
        ws.check_worktree(tree)

    def test_a_build_without_a_credential_file_refuses_before_writing_anything(self) -> None:
        (self.operator / "auth.json").unlink()
        with self.assertRaises(ReviewLaunchRefusedError):
            self.build()
        self.assertFalse((self.layout.base / "scratch").exists())

    def test_the_testers_layout_is_untouched(self) -> None:
        self.build()
        self.assertFalse((self.runtime / "functional").exists())
        self.assertTrue((self.runtime / "worker" / "job1").is_dir())


# ---------------------------------------------------------------------------
# CP2: the probes
# ---------------------------------------------------------------------------

ALL_HELD = "\n".join(f"PROBE {n} {r}" for n, r in ws.PROBE_EXPECTED.items()) + "\n" + \
    "\n".join(f"PROBE {n} REFUSED" for n in ws.PROBE_INFORMATIONAL) + "\n"


class ProbeFixture(Fixture):
    def targets(self, tree: Path) -> ws.ProbeTargets:
        for name in ("scratch", "sibling-dir", "home", "origin"):
            (self.tmp / name).mkdir(exist_ok=True)
        if not (self.tmp / "origin" / "HEAD").exists():
            subprocess.run(["git", "init", "-q", "--bare", str(self.tmp / "origin")], check=True, capture_output=True)
        return ws.ProbeTargets(tree, self.tmp / "scratch", self.tmp / "sibling-dir" / "probe", self.tmp / "home",
                               self.tmp / "canary.txt", "CANARY_ENV_X", self.tmp / "cred.json", self.tmp / "origin",
                               1, self.tmp / "no.sock")


class ProbeCommandTest(ProbeFixture):
    def test_every_probe_is_named_once_with_absolute_quoted_paths(self) -> None:
        tree = self.tmp / "tree with space"
        make_clone(tree)
        command = ws.probe_command(self.targets(tree), marker="m1")
        lines = command.splitlines()
        for name in (*ws.PROBE_EXPECTED, *ws.PROBE_INFORMATIONAL):
            emitting = [line for line in lines if "$1" not in line
                        and re.search(rf"(PROBE {name} |^(w|ap|ah) {name} )", line)]
            self.assertEqual(len(emitting), 1, name)
        self.assertIn(shlex.quote(str(tree / "src.txt")), command)
        self.assertIn("git -C " + shlex.quote(str(tree)), command)
        self.assertIn("-c user.name=probe -c user.email=probe@example.invalid", command)

    def test_a_marker_that_is_not_a_plain_name_refuses(self) -> None:
        tree = self.tmp / "t"
        make_clone(tree)
        for marker in ("a b", "a;b", "", "a'b"):
            with self.assertRaises(ReviewLaunchRefusedError):
                ws.probe_command(self.targets(tree), marker=marker)

    def test_run_unsandboxed_in_a_clone_every_writable_side_probe_reports_its_result(self) -> None:
        tree = self.tmp / "tree"
        make_clone(tree)
        head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        out = subprocess.run(["sh", "-c", ws.probe_command(self.targets(tree), marker="m2")], capture_output=True,
                             text=True, cwd=self.tmp).stdout
        lines = dict(line[len("PROBE "):].split(" ", 1) for line in out.splitlines() if line.startswith("PROBE "))
        for name in ("tracked", "newfile", "rename", "delete", "commit", "scratch", "home", "hooks", "gitconfig"):
            self.assertEqual(lines[name], ws.PROBE_EXPECTED.get(name, "WROTE") if name != "home" else "WROTE", name)
        self.assertEqual((tree / "src.txt").read_text(), "one\nprobe\n")
        self.assertTrue((tree / ws.PROBE_RENAME_TARGET).exists())
        self.assertFalse((tree / ws.PROBE_RENAME_SOURCE).exists())
        self.assertFalse((tree / ws.PROBE_DELETE_FILE).exists())
        self.assertNotEqual(subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True,
                                           text=True).stdout, head)
        self.assertTrue((tree / ".git" / "hooks" / "probe-m2").exists())
        config = (tree / ".git" / "config").read_text()
        self.assertTrue(config.startswith("[core]") and config.rstrip().endswith("# probe-m2"))
        git(tree, "status", "--porcelain")  # git still works after the config append

    def test_hooks_and_gitconfig_come_after_commit_and_never_truncate_the_config(self) -> None:
        tree = self.tmp / "tree"
        make_clone(tree)
        command = ws.probe_command(self.targets(tree), marker="m3")
        commit = command.index("PROBE commit COMMITTED")
        self.assertGreater(command.index("ah hooks "), commit)
        self.assertGreater(command.index("PROBE gitconfig WROTE"), commit)
        config_line = next(line for line in command.splitlines() if "PROBE gitconfig WROTE" in line)
        self.assertIn(">> " + shlex.quote(str(tree / ".git" / "config")), config_line)
        self.assertNotIn(": > " + shlex.quote(str(tree / ".git" / "config")), command)
        self.assertNotIn("w gitconfig", command)
        self.assertNotIn("w hooks", command)
        self.assertIn("probe-m3", command)
        self.assertIn(shlex.quote(str(tree / ".git" / "hooks" / "probe-m3")), command)

    def test_rename_and_delete_use_files_of_their_own_not_the_tracked_one(self) -> None:
        tree = self.tmp / "tree"
        make_clone(tree)
        command = ws.probe_command(self.targets(tree), marker="m4")
        for line in command.splitlines():
            if "PROBE rename" in line or "PROBE delete" in line:
                self.assertNotIn("src.txt", line)
        self.assertIn(ws.PROBE_RENAME_SOURCE, command)
        self.assertIn(ws.PROBE_DELETE_FILE, command)


class JudgeTest(unittest.TestCase):
    def test_the_all_held_output_passes(self) -> None:
        self.assertEqual(ws.judge(ALL_HELD), [])
        self.assertEqual(ws.judge(ALL_HELD.replace("hooks REFUSED", "hooks WROTE")), [])

    def test_each_departure_fails_closed(self) -> None:
        cases = {
            "a missing line": ALL_HELD.replace("PROBE push REFUSED\n", ""),
            "a conflicting repeat": ALL_HELD + "PROBE push PUSHED\n",
            "an unexpected result": ALL_HELD.replace("PROBE tcp BLOCKED", "PROBE tcp CONNECTED"),
            "an expected probe refused": ALL_HELD.replace("PROBE commit COMMITTED", "PROBE commit FAILED"),
            "an invalid informational result": ALL_HELD.replace("PROBE tmp REFUSED", "PROBE tmp ODD"),
            "an unknown probe": ALL_HELD + "PROBE surprise WROTE\n",
            "empty output": "",
            "a missing informational line": ALL_HELD.replace("PROBE gitconfig REFUSED\n", ""),
        }
        for what, output in cases.items():
            self.assertTrue(ws.judge(output), what)

    def test_the_old_testers_names_are_not_known(self) -> None:
        self.assertTrue(ws.judge(ALL_HELD + "PROBE git REFUSED\n"))


class SandboxProbeArgvTest(Fixture):
    def test_the_command_selects_the_worker_profile_and_the_worktree_and_no_sandbox_flag(self) -> None:
        command = ws.sandbox_probe_argv(self.layout, binary=self.binary, command="echo hi")
        self.assertEqual(command[:7], [str(self.binary), "sandbox", "-P", "worker", "-C", str(self.layout.worktree),
                                       "-c"])
        self.assertEqual(command[-4:], ["--", "sh", "-c", "echo hi"])
        for item in ws.shell_policy_overrides(self.layout):
            self.assertIn(item, command)
        for flag in ("-s", "--sandbox", "--add-dir", "--full-auto"):
            self.assertNotIn(flag, command)


# ---------------------------------------------------------------------------
# CP2: the git guard
# ---------------------------------------------------------------------------


class GuardFixture(ProbeFixture):
    def setUp(self) -> None:
        super().setUp()
        self.tree = self.tmp / "gtree"
        make_clone(self.tree)
        self.git = self.tree / ".git"

    def digest(self) -> dict:
        return ws.guard_digest(self.tree)

    def refused(self, before: dict, after: dict | None = None, entry: str | None = None) -> str:
        with self.assertRaises(ReviewLaunchRefusedError) as caught:
            ws.check_guard(before, after if after is not None else self.digest())
        message = str(caught.exception)
        if entry is not None:
            self.assertIn(entry, message)
        return message


class GuardDigestTest(GuardFixture):
    def test_an_unchanged_tree_gives_an_equal_digest_and_passes(self) -> None:
        before = self.digest()
        self.assertEqual(before, self.digest())
        ws.check_guard(before, self.digest())
        self.assertEqual(before["git"], "dir")
        self.assertEqual(sorted(before["roots"]), ["logs", "objects", "refs"])

    def test_each_listed_change_is_detected_and_named(self) -> None:
        def new_hook() -> None:
            (self.git / "hooks" / "pre-commit").write_text("#!/bin/sh\n")

        def edit_hook() -> None:
            (self.git / "hooks" / "pre-commit.sample").write_text("# edited\n")

        def chmod_hook() -> None:
            os.chmod(self.git / "hooks" / "pre-commit.sample", 0o700)

        def edit_config() -> None:
            with open(self.git / "config", "a") as f:
                f.write("# x\n")

        def info_exclude() -> None:
            (self.git / "info").mkdir(exist_ok=True)
            (self.git / "info" / "exclude").write_text("x\n")

        cases = {
            "hooks/pre-commit": new_hook, "hooks/pre-commit.sample": edit_hook, "chmod": chmod_hook,
            "config": edit_config, "info/exclude": info_exclude,
            "commondir": lambda: (self.git / "commondir").write_text("../elsewhere\n"),
            "config.worktree": lambda: (self.git / "config.worktree").write_text("[core]\n"),
            "gitdir": lambda: (self.git / "gitdir").write_text("x\n"),
            "other": lambda: (self.git / "other-file").write_text("x\n"),
            "modules": lambda: ((self.git / "modules" / "x").mkdir(parents=True),
                                         (self.git / "modules" / "x" / "config").write_text("[core]\n")),
        }
        if not (self.git / "hooks" / "pre-commit.sample").exists():
            (self.git / "hooks").mkdir(exist_ok=True)
            (self.git / "hooks" / "pre-commit.sample").write_text("# sample\n")
        for name, change in cases.items():
            with self.subTest(name):
                before = self.digest()
                change()
                self.refused(before, entry=name if "/" in name or name in ("config", "commondir", "gitdir")
                             else None)

    def test_a_removed_entry_is_detected(self) -> None:
        before = self.digest()
        (self.git / "config").unlink()
        self.refused(before, entry="config")

    def test_info_refs_appearing_is_accepted_and_a_symlink_or_fifo_there_is_refused(self) -> None:
        (self.git / "info").mkdir(exist_ok=True)
        before = self.digest()
        (self.git / "info" / "refs").write_text("x\n")
        ws.check_guard(before, self.digest())
        (self.git / "info" / "refs").unlink()
        os.symlink(self.tmp, self.git / "info" / "refs")
        self.refused(before, entry="info/refs")
        (self.git / "info" / "refs").unlink()
        os.mkfifo(self.git / "info" / "refs")
        self.refused(before, entry="info/refs")

    def test_a_commit_leaves_the_digest_unchanged_on_a_fresh_clone(self) -> None:
        fresh = self.tmp / "fresh"
        subprocess.run(["git", "clone", "-q", str(self.tree), str(fresh)], check=True, capture_output=True)
        self.assertFalse((fresh / ".git" / "COMMIT_EDITMSG").exists())
        self.assertFalse((fresh / ".git" / "ORIG_HEAD").exists())
        before = ws.guard_digest(fresh)
        (fresh / "new.txt").write_text("n\n")
        git(fresh, "add", "-A")
        git(fresh, "commit", "-q", "-m", "second")
        self.assertTrue((fresh / ".git" / "COMMIT_EDITMSG").exists())
        after = ws.guard_digest(fresh)
        self.assertEqual(before, after)
        ws.check_guard(before, after)

    def test_a_first_commit_that_creates_logs_in_a_new_repository_is_accepted(self) -> None:
        fresh = self.tmp / "newrepo"
        fresh.mkdir()
        subprocess.run(["git", "init", "-q", str(fresh)], check=True, capture_output=True)
        before = ws.guard_digest(fresh)
        self.assertNotIn("logs", before["roots"])
        (fresh / "a.txt").write_text("a\n")
        git(fresh, "add", "-A")
        git(fresh, "commit", "-q", "-m", "first")
        after = ws.guard_digest(fresh)
        self.assertEqual(after["roots"]["logs"], "dir")
        ws.check_guard(before, after)

    def test_a_removed_excluded_root_is_refused(self) -> None:
        before = self.digest()
        subprocess.run(["rm", "-rf", str(self.git / "logs")], check=True)
        self.refused(before, entry="logs")

    def test_a_plain_gc_leaves_the_digest_unchanged(self) -> None:
        self.assertFalse((self.git / "info" / "refs").exists())
        before = self.digest()
        git(self.tree, "gc", "-q")
        self.assertTrue((self.git / "packed-refs").exists())
        self.assertTrue((self.git / "info" / "refs").exists())
        self.assertTrue(any((self.git / "objects" / "pack").glob("*.pack")))
        after = self.digest()
        self.assertEqual(before, after)
        ws.check_guard(before, after)

    def test_the_probe_command_run_unsandboxed_changes_the_digest_only_through_hooks_and_config(self) -> None:
        before = self.digest()
        subprocess.run(["sh", "-c", ws.probe_command(self.targets(self.tree), marker="m5")], capture_output=True,
                       cwd=self.tmp)
        message = self.refused(before)
        self.assertTrue("hooks" in message or "config" in message)
        (self.git / "hooks" / "probe-m5").unlink()
        text = (self.git / "config").read_text()
        (self.git / "config").write_text(text.replace("# probe-m5\n", ""))
        ws.check_guard(before, self.digest())

    def test_lock_files_appearing_leave_the_digest_unchanged(self) -> None:
        before = self.digest()
        (self.git / "index.lock").write_text("")
        (self.git / "refs" / "heads" / "x.lock").write_text("")
        ws.check_guard(before, self.digest())


class GuardGitEntryTest(GuardFixture):
    def test_the_digest_records_the_type_of_git_and_never_follows_a_symlink(self) -> None:
        copy = self.tmp / "gitcopy"
        subprocess.run(["cp", "-a", str(self.git), str(copy)], check=True)
        before = self.digest()
        self.assertEqual(before["git"], "dir")
        for what in ("file", "symlink", "removed"):
            with self.subTest(what):
                tree = self.tmp / f"tree-{what}"
                subprocess.run(["cp", "-a", str(self.tree), str(tree)], check=True)
                before = ws.guard_digest(tree)
                subprocess.run(["rm", "-rf", str(tree / ".git")], check=True)
                if what == "file":
                    (tree / ".git").write_text("gitdir: evil\n")
                elif what == "symlink":
                    os.symlink(copy, tree / ".git")
                after = ws.guard_digest(tree)
                self.assertNotEqual(after["git"], "dir")
                self.assertEqual(after["entries"], {})
                self.refused(before, after, entry=".git")
                self.refused(after, before, entry=".git")


class GuardRedirectionTest(GuardFixture):
    def redirections(self, g: Path, other: Path) -> dict:
        """Each change, as a function of a tree's git directory, with the entry the refusal must name."""
        first = next(p for p in (g / "objects").iterdir() if len(p.name) == 2)

        def to_symlink(path: Path, target: Path):
            def change() -> None:
                if path.is_dir() and not path.is_symlink():
                    subprocess.run(["rm", "-rf", str(path)], check=True)
                else:
                    path.unlink()
                os.symlink(target, path)
            return change

        def to_file(root: str):
            def change() -> None:
                subprocess.run(["rm", "-rf", str(g / root)], check=True)
                (g / root).write_text("x\n")
            return change

        def alternates() -> None:
            (g / "objects" / "info").mkdir(exist_ok=True)
            (g / "objects" / "info" / "alternates").write_text(str(other / ".git" / "objects") + "\n")

        return {
            "objects": to_symlink(g / "objects", other / ".git" / "objects"),
            f"objects/{first.name}": to_symlink(first, other),
            "objects/pack": to_symlink(g / "objects" / "pack", other),
            "refs/heads/link": lambda: os.symlink(other, g / "refs" / "heads" / "link"),
            "logs/link": lambda: os.symlink(other, g / "logs" / "link"),
            "COMMIT_EDITMSG": to_symlink(g / "COMMIT_EDITMSG", other / "f"),
            "index": to_symlink(g / "index", other / "f"),
            "HEAD": to_symlink(g / "HEAD", other / "f"),
            "refs": to_file("refs"), "logs": to_file("logs"),
            "refs/fifo": lambda: os.mkfifo(g / "refs" / "fifo"),
            "objects/info/alternates": alternates,
        }

    def test_each_structural_redirection_is_refused_naming_the_entry(self) -> None:
        other = self.tmp / "other-repo"
        make_clone(other)
        template = self.tmp / "template"
        make_clone(template)
        (template / ".git" / "objects" / "pack").mkdir(exist_ok=True)
        (template / ".git" / "COMMIT_EDITMSG").write_text("x\n")
        names = list(self.redirections(template / ".git", other))
        for index, name in enumerate(names):
            with self.subTest(name):
                tree = self.tmp / f"rtree-{index}"
                subprocess.run(["cp", "-a", str(template), str(tree)], check=True)
                before = ws.guard_digest(tree)
                ws.check_guard(before, ws.guard_digest(tree))
                self.redirections(tree / ".git", other)[name]()
                after = ws.guard_digest(tree)
                with self.assertRaises(ReviewLaunchRefusedError) as caught:
                    ws.check_guard(before, after)
                self.assertIn(name.split("/")[-1] if name.startswith(("refs/", "logs/")) else name,
                              str(caught.exception), name)

    def test_a_symlink_already_in_the_baseline_is_refused_not_accepted(self) -> None:
        os.symlink(self.tmp, self.git / "refs" / "heads" / "link")
        before = self.digest()
        self.assertEqual(before["special"], ["refs/heads/link"])
        self.refused(before, before, entry="refs/heads/link")

    def test_an_alternates_file_in_the_baseline_is_refused(self) -> None:
        (self.git / "objects" / "info").mkdir(exist_ok=True)
        (self.git / "objects" / "info" / "alternates").write_text("/x\n")
        before = self.digest()
        self.assertTrue(before["alternates"])
        self.refused(before, before, entry="alternates")


if __name__ == "__main__":
    unittest.main()
