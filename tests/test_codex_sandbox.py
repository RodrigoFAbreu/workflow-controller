"""`workflow-controller-codex-tester-sandbox` CP1: the Codex tester sandbox builder
(`controller.codex_sandbox`). Offline: no `codex`, no network, no sandbox run; the
runtime is a temporary folder and the listing controls use a fake runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_sandbox as cs, review  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402

NO_SERVERS = b"No MCP servers configured yet.\n"
FEATURES = b"shell_tool    stable    true\nbrowser_use   stable    false\n"


def result(stdout: bytes = b"", code: int = 0, timed_out: bool = False) -> review.RunResult:
    return review.RunResult(code, timed_out, stdout, b"boom", 1, 1)


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
        # a fake nvm layout: bin/codex -> ../lib/node_modules/@openai/codex/bin/codex.js
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
        self.layout = cs.TesterLayout(self.runtime, "job1")
        self.system = self.tmp / "etc-codex"
        self.system.mkdir()

    def build(self, **kw) -> cs.Built:
        kw.setdefault("runtime_root", self.runtime)
        kw.setdefault("job_id", "job1")
        kw.setdefault("operator_home", self.operator)
        kw.setdefault("binary", self.binary)
        kw.setdefault("system_dir", self.system)
        kw.setdefault("real_home", self.real_home)
        return cs.build(**kw)

    def roots(self, **kw) -> list[Path]:
        kw.setdefault("operator_home", self.real_home)
        return cs.readable_roots(self.layout, self.binary, **kw)


class OutsideLayerTest(Fixture):
    def test_a_system_layer_with_content_refuses_and_nothing_runs(self) -> None:
        for name in review.SYSTEM_LAYER_FILES:
            with self.subTest(name=name):
                (self.system / name).write_text('[mcp_servers.evil]\ncommand = "x"\n')
                run = mock.Mock()
                with self.assertRaises(ReviewLaunchRefusedError) as ctx:
                    cs.check_launch(self.layout, "x", self.binary, {}, system_dir=self.system, run=run)
                self.assertIn(name, str(ctx.exception))
                run.assert_not_called()
                with mock.patch("subprocess.Popen") as popen, self.assertRaises(ReviewLaunchRefusedError):
                    self.build()
                popen.assert_not_called()
                self.assertFalse(self.runtime.exists())
                (self.system / name).unlink()

    def test_an_empty_or_absent_layer_passes_and_an_unreadable_one_refuses(self) -> None:
        (self.system / "config.toml").write_text("  \n")
        self.build()
        (self.system / "managed_config.toml").mkdir()  # a directory cannot be read as a file
        with self.assertRaises(ReviewLaunchRefusedError):
            self.build(job_id="job2")


class ListingControlsTest(Fixture):
    def launch(self, run, **kw) -> None:
        built = self.build()
        cs.check_launch(built.layout, built.config_text, self.binary, built.environment, system_dir=self.system,
                        run=run, **kw)

    def test_clean_listings_pass_in_the_documented_order(self) -> None:
        calls = []
        outputs = {"mcp": NO_SERVERS, "features": FEATURES}

        def run(command, **kw):
            calls.append(command[1])
            return result(outputs[command[1]])

        with mock.patch.object(cs, "check_home", wraps=cs.check_home) as home, \
                mock.patch.object(review, "check_outside_layers", wraps=review.check_outside_layers) as outside:
            self.launch(run)
        self.assertEqual(calls, ["mcp", "features"])
        self.assertEqual(outside.call_count, 2)  # build and check_launch
        self.assertEqual(home.call_count, 1 + 3)  # build, then before mcp, before features, after both

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

    def test_the_listings_run_in_the_clone_with_the_testers_environment(self) -> None:
        seen = []

        def run(command, *, cwd, env, timeout):
            seen.append((cwd, env["CODEX_HOME"]))
            return result(NO_SERVERS if command[1] == "mcp" else FEATURES)

        self.launch(run)
        self.assertEqual(seen, [(self.layout.clone, str(self.layout.codex_home))] * 2)


class KnownLimitsTest(Fixture):
    def test_the_composition_window_is_the_only_limit_and_is_named_in_the_profile(self) -> None:
        self.assertEqual([limit.name for limit in cs.KNOWN_LIMITS], ["composition_window"])
        limit = cs.KNOWN_LIMITS[0]
        self.assertIn("app-server", limit.reason)
        self.assertIn("app-server", limit.guide)
        self.assertIn(limit.guide, self.build().config_text)


class CheckHomeTest(Fixture):
    def test_the_written_home_passes_and_each_change_refuses(self) -> None:
        built = self.build()
        home = self.layout.codex_home
        cs.check_home(self.layout, built.config_text)
        (home / "sessions" / "x.sqlite").write_text("")  # a state database is allowed
        cs.check_home(self.layout, built.config_text)
        for name in ("hooks.json", "skills/x", "mystery", "managed_config.toml"):
            with self.subTest(name=name):
                target = home / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("")
                with self.assertRaises(ReviewLaunchRefusedError):
                    cs.check_home(self.layout, built.config_text)
                target.unlink()
                if target.parent != home:
                    target.parent.rmdir()
        (home / "config.toml").write_text(built.config_text + "# edited\n")
        with self.assertRaises(ReviewLaunchRefusedError):
            cs.check_home(self.layout, built.config_text)

    def test_the_reviewers_home_and_the_testers_home_are_not_interchangeable(self) -> None:
        built = self.build()
        with self.assertRaises(ReviewLaunchRefusedError):
            review.check_home(self.layout.job)  # the tester's config is not the reviewer's pinned file
        (self.layout.codex_home / "config.toml").write_text(review.HOME_CONFIG_TOML)
        with self.assertRaises(ReviewLaunchRefusedError):
            cs.check_home(self.layout, built.config_text)


class ReadableRootsTest(Fixture):
    def test_the_controller_package_directory_is_accepted_and_its_parent_is_not_listed(self) -> None:
        roots = self.roots()
        self.assertIn(REPO_ROOT / "controller", roots)
        self.assertNotIn(REPO_ROOT, roots)

    def test_roots_equal_to_or_containing_the_job_the_runtime_or_the_codex_home_refuse(self) -> None:
        for bad in (self.layout.base, self.layout.base.parent, self.layout.runtime_root, self.runtime.parent,
                    self.layout.codex_home):
            with self.subTest(root=str(bad)), self.assertRaises(ReviewLaunchRefusedError):
                self.roots(extra=[bad])
            with self.subTest(build=str(bad)), self.assertRaises(ReviewLaunchRefusedError):
                self.build(extra_readable=[bad])

    def test_a_root_inside_the_scratch_area_or_the_codex_home_refuses(self) -> None:
        for bad in (self.layout.scratch_tmp, self.layout.codex_home / "sessions"):
            with self.subTest(root=str(bad)), self.assertRaises(ReviewLaunchRefusedError):
                self.roots(extra=[bad])

    def test_a_runtime_root_under_the_controller_package_parent_refuses_the_parent_but_not_the_package(self) -> None:
        layout = cs.TesterLayout(REPO_ROOT / ".controller", "job1")
        cs.readable_roots(layout, self.binary, operator_home=self.real_home)
        with self.assertRaises(ReviewLaunchRefusedError):
            cs.readable_roots(layout, self.binary, package_dir=REPO_ROOT, operator_home=self.real_home)

    def test_root_home_and_ancestors_of_the_home_refuse(self) -> None:
        for bad in (Path("/"), self.real_home, self.real_home.parent):
            with self.subTest(root=str(bad)), self.assertRaises(ReviewLaunchRefusedError):
                self.roots(extra=[bad])

    def test_a_symlink_to_a_container_of_the_job_refuses(self) -> None:
        link = self.tmp / "link"
        link.symlink_to(self.runtime.parent)
        self.runtime.mkdir()
        with self.assertRaises(ReviewLaunchRefusedError):
            self.roots(extra=[link])

    def test_the_install_roots_of_a_fake_nvm_layout_name_the_package_root(self) -> None:
        roots = self.roots()
        for expected in (self.prefix / "bin", self.package / "bin", self.package):
            self.assertIn(expected, roots)

    def test_a_stray_package_json_above_a_non_npm_binary_is_not_reached(self) -> None:
        (self.tmp / "package.json").write_text("{}")
        binary = self.tmp / "opt" / "bin" / "codex"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        self.assertEqual(cs.package_root(binary.resolve()), binary.parent)
        self.assertEqual(cs.codex_install_roots(binary)[:3], [binary.parent] * 3)

    def test_the_stand_in_interpreter_roots_cover_a_venv_under_a_home(self) -> None:
        venv = self.real_home / "venvs" / "wc"
        (venv / "bin").mkdir(parents=True)
        base = self.real_home / "pythons" / "3.13"
        (base / "bin").mkdir(parents=True)
        real = base / "bin" / "python3"
        real.write_text("")
        link = venv / "bin" / "python"
        link.symlink_to(real)
        roots = self.roots(python=link, prefix=venv, base_prefix=base)
        for expected in (base / "bin", venv, base):
            self.assertIn(expected, roots)
        with self.assertRaises(ReviewLaunchRefusedError):  # the overlap refusal still applies to them
            self.roots(python=link, prefix=self.runtime.parent, base_prefix=base)

    def test_build_names_the_resolved_interpreter_in_the_launchers(self) -> None:
        built = self.build()
        launcher = (self.layout.bin / "gh").read_text()
        self.assertIn(str(Path(sys.executable).resolve()), launcher)
        for root in cs.interpreter_roots():
            self.assertIn(root, built.roots)


class ProfileTest(Fixture):
    def test_the_profile_lists_the_clone_read_the_scratch_write_and_nothing_of_the_real_home(self) -> None:
        built = self.build()
        text = built.config_text
        self.assertIn(f'"{self.layout.clone}" = "read"', text)
        self.assertIn(f'"{self.layout.scratch}" = "write"', text)
        self.assertIn('"/usr" = "read"', text)
        self.assertIn('default_permissions = "tester"', text)
        self.assertIn("[permissions.tester.network]\nenabled = false", text)
        self.assertEqual(text.count('= "write"'), 1)
        self.assertNotIn(str(self.real_home / "secret"), text)
        self.assertNotIn(f'"{self.real_home}"', text)
        self.assertNotIn(f'"{self.runtime}"', text)
        self.assertNotIn(f'"{self.layout.base}"', text)
        self.assertNotIn(f'"{self.layout.codex_home}"', text)

    def test_the_same_inputs_give_identical_bytes(self) -> None:
        roots = self.roots()
        self.assertEqual(cs.config_toml(self.layout, roots), cs.config_toml(self.layout, list(reversed(roots))))

    def test_a_path_with_a_quote_a_newline_or_a_relative_form_refuses(self) -> None:
        for bad in ('/tmp/a"b', "/tmp/a\nb", "/tmp/a\\b", "relative/path", "/tmp/a\x00b"):
            with self.subTest(path=bad), self.assertRaises(ReviewLaunchRefusedError):
                cs.profile_toml(self.layout, [Path(bad)])
        with self.assertRaises(ReviewLaunchRefusedError):
            cs.profile_toml(cs.TesterLayout(self.tmp / 'a"b', "job1"), [])

    def test_supplementary_unicode_paths_parse_and_are_preserved(self) -> None:
        runtime = self.tmp / "runtime-\U0001F600-\u00e9"
        layout = cs.TesterLayout(runtime, "job1")
        root = self.tmp / "root-\U0001F680"
        parsed = tomllib.loads(cs.config_toml(layout, [root]))
        access = parsed["permissions"]["tester"]["filesystem"]
        self.assertEqual(access[str(root)], "read")
        self.assertEqual(access[str(layout.clone)], "read")
        for override in cs.required_overrides(layout):
            tomllib.loads(f"x = {override.split('=', 1)[1]}" if "." not in override.split("=", 1)[0] else override)
        projects = tomllib.loads(next(o for o in cs.required_overrides(layout) if o.startswith("projects=")))
        self.assertIn(str(layout.clone), projects["projects"])
        with self.assertRaises(ReviewLaunchRefusedError):
            cs.profile_toml(layout, [Path("/tmp/a\ud800b")])


class EnvironmentTest(Fixture):
    def test_the_environment_is_exactly_the_allowlist_with_the_scratch_tmpdir(self) -> None:
        env = cs.child_environment(self.layout, self.binary, "tag", {"CANARY": "leak", "AWS_SECRET": "x"})
        self.assertEqual(set(env), set(review.ENV_ALLOWLIST))
        self.assertNotIn("leak", env.values())
        self.assertEqual(env["TMPDIR"], str(self.layout.scratch_tmp))
        self.assertEqual(env["CODEX_HOME"], str(self.layout.codex_home))

    def test_the_shell_policy_inherits_nothing_and_puts_the_launchers_first(self) -> None:
        inherit, set_ = cs.shell_policy_overrides(self.layout)
        self.assertEqual(inherit, 'shell_environment_policy.inherit="none"')
        self.assertIn(f'PATH="{self.layout.bin}:{review.SYSTEM_PATH}"', set_)
        self.assertEqual(set(cs.command_environment(self.layout)), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"})
        self.assertEqual(cs.command_environment(self.layout)["HOME"], str(self.layout.home))


class ArgvTest(Fixture):
    def command(self) -> list[str]:
        return cs.argv(self.layout, binary=self.binary)

    def test_the_argv_carries_the_pinned_set_and_no_sandbox_selection(self) -> None:
        command = self.command()
        disables = [command[i + 1] for i, a in enumerate(command) if a == "--disable"]
        self.assertEqual(disables, list(review.DISABLED_FEATURES))
        present = [command[i + 1] for i, a in enumerate(command) if a == "-c"]
        self.assertEqual(present, list(cs.required_overrides(self.layout)))
        self.assertIn('default_permissions="tester"', present)
        self.assertNotIn("-s", command)
        self.assertFalse([c for c in present if c.startswith(("sandbox_mode", "sandbox_workspace_write"))])
        for flag in ("--strict-config", "--ignore-rules"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("-C") + 1], str(self.layout.clone))
        self.assertEqual(command[command.index("-o") + 1], str(self.layout.verdict))
        cs.check_argv(command, self.layout)

    def test_check_argv_refuses_each_deviation(self) -> None:
        good = self.command()
        flag = good.index("--disable")
        cases = {
            "missing disable": good[:flag] + good[flag + 2:],
            "extra -c": good[:-1] + ["-c", 'sandbox_mode="danger-full-access"', good[-1]],
            "-s": good[:-1] + ["-s", "workspace-write", good[-1]],
            "--add-dir": good[:-1] + ["--add-dir", "/", good[-1]],
            "bypass": good[:-1] + ["--dangerously-bypass-approvals-and-sandbox", good[-1]],
            "approve": good[:-1] + ["--approve-for-me", good[-1]],
            "changed -C": [("/elsewhere" if i and good[i - 1] == "-C" else a) for i, a in enumerate(good)],
            "missing -c": good[:good.index('default_permissions="tester"') - 1]
            + good[good.index('default_permissions="tester"') + 1:],
            "code mode": good + ["--disable", "code_mode_host"],
            "--config": good[:-1] + ["--config", 'sandbox_mode="danger-full-access"', good[-1]],
            "--sandbox=": good[:-1] + ["--sandbox=danger-full-access", good[-1]],
        }
        for name, command in cases.items():
            with self.subTest(name=name), self.assertRaises(ReviewLaunchRefusedError):
                cs.check_argv(command, self.layout)


class BuildTest(Fixture):
    def test_build_writes_a_private_credential_copy_and_nothing_under_the_clone(self) -> None:
        built = self.build()
        auth = self.layout.codex_home / "auth.json"
        self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o600)
        self.assertEqual(auth.read_bytes(), (self.operator / "auth.json").read_bytes())
        self.assertFalse(self.layout.clone.exists())
        self.assertTrue(self.layout.scratch_tmp.is_dir() and self.layout.fake_logs.is_dir())
        self.assertEqual(hashlib.sha256((self.layout.codex_home / "config.toml").read_bytes()).hexdigest(),
                         hashlib.sha256(built.config_text.encode()).hexdigest())
        self.assertTrue(os.access(self.layout.bin / "gh", os.X_OK))
        self.assertEqual(list(built.argv[:2]), [str(self.binary), "exec"])

    def test_build_refuses_without_a_credential_file(self) -> None:
        (self.operator / "auth.json").unlink()
        with self.assertRaises(ReviewLaunchRefusedError):
            self.build()


class ComposeArgvTest(Fixture):
    def command(self) -> list[str]:
        return cs.compose_argv(self.layout, binary=self.binary)

    def test_the_composing_command_carries_the_execs_flags_and_listens_on_stdio(self) -> None:
        command = self.command()
        self.assertEqual(command[:3], [str(self.binary), "app-server", "--strict-config"])
        self.assertEqual(command[-2:], ["--listen", "stdio://"])
        self.assertEqual([command[i + 1] for i, a in enumerate(command) if a == "--disable"],
                         list(review.DISABLED_FEATURES))
        self.assertEqual([command[i + 1] for i, a in enumerate(command) if a == "-c"],
                         list(cs.required_overrides(self.layout)))
        exec_command = cs.argv(self.layout, binary=self.binary)
        self.assertEqual(command[3:-2], exec_command[3:exec_command.index("-C")])
        cs.check_compose_argv(command, self.layout)

    def test_check_compose_argv_refuses_each_deviation(self) -> None:
        good = self.command()
        flag = good.index("--disable")
        mark = good.index('default_permissions="tester"')
        cases = {
            "missing disable": good[:flag] + good[flag + 2:],
            "extra -c": good[:-2] + ["-c", 'sandbox_mode="danger-full-access"'] + good[-2:],
            "missing -c": good[:mark - 1] + good[mark + 1:],
            "-s": good[:-2] + ["-s", "workspace-write"] + good[-2:],
            "-p": good[:-2] + ["-p", "x"] + good[-2:],
            "--add-dir": good[:-2] + ["--add-dir", "/"] + good[-2:],
            "--config": good[:-2] + ["--config", 'sandbox_mode="x"'] + good[-2:],
            "no strict config": [a for a in good if a != "--strict-config"],
            "no listen": good[:-2],
            "other listen": good[:-1] + ["ws://127.0.0.1:1"],
            "exec": [good[0], "exec", *good[2:]],
            "code mode": good + ["--disable", "code_mode_host"],
            "reordered": good[:3] + good[-2:] + good[3:-2],
        }
        for name, command in cases.items():
            with self.subTest(name=name), self.assertRaises(ReviewLaunchRefusedError):
                cs.check_compose_argv(command, self.layout)

    def test_another_model_or_effort_is_a_different_command(self) -> None:
        other = cs.compose_argv(self.layout, binary=self.binary, model="m2", effort="low")
        cs.check_compose_argv(other, self.layout, model="m2", effort="low")
        with self.assertRaises(ReviewLaunchRefusedError):
            cs.check_compose_argv(other, self.layout)


def composed_for(layout: cs.TesterLayout, roots: list[Path], **kw) -> dict:
    """A ``config/read`` result of the shape ``tester_compose.txt`` records: the tester's own policy, ``null`` for the
    unset siblings, the three layers and the origins as measured."""
    import copy
    config = copy.deepcopy(cs.effective_policy(layout, roots, **kw))
    config["sandbox_mode"] = None
    config["sandbox_workspace_write"] = None
    config["hooks"] = None
    config["skills"] = {"bundled": {"enabled": config.pop("skills.bundled.enabled")}}
    config["features"] = {name: False for name in review.DISABLED_FEATURES} | {"memories": True}
    tester = config["permissions"]["tester"]
    tester.update(description=None, extends=None, workspace_roots=None)
    tester["filesystem"]["glob_scan_max_depth"] = None
    tester["network"].update(mode=None, proxy_url=None, domains=None)
    config["shell_environment_policy"].update(exclude=None, filters=None)
    flags = {"name": {"type": "sessionFlags"}, "version": "sha256:0"}
    user = {"type": "user", "file": str(layout.codex_home / "config.toml"), "profile": None}
    origins = {}
    for leaf, value in review.flatten(config).items():
        if value is None or value in ({}, []):
            continue
        origins[leaf] = user if leaf.startswith("permissions.") else flags
    layers = [{"name": {"type": "sessionFlags"}, "config": {"model": config["model"]}, "disabledReason": None},
              {"name": {"type": "user", "file": str(layout.codex_home / "config.toml"), "profile": None},
               "config": {"default_permissions": "tester"}, "disabledReason": None},
              {"name": {"type": "system", "file": "/etc/codex/config.toml"}, "config": None, "disabledReason": None}]
    return {"config": config, "origins": origins, "layers": layers}


class CheckEffectiveTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.paths = self.roots()
        self.composed = composed_for(self.layout, self.paths)

    def check(self, composed: dict | None = None) -> None:
        cs.check_effective(composed if composed is not None else self.composed, self.layout, self.paths)

    def refused(self, composed: dict) -> ReviewLaunchRefusedError:
        with self.assertRaises(ReviewLaunchRefusedError) as caught:
            self.check(composed)
        return caught.exception

    def mutated(self, *path: str, value: object) -> dict:
        import copy
        composed = copy.deepcopy(self.composed)
        node = composed["config"]
        for part in path[:-1]:
            if node.get(part) is None:
                node[part] = {}
            node = node[part]
        if value is KeyError:
            node.pop(path[-1])
        else:
            node[path[-1]] = value
        return composed

    def test_the_testers_own_composed_configuration_passes(self) -> None:
        self.check()

    def test_a_different_model_effort_or_tier_is_checked_against_the_arguments(self) -> None:
        composed = composed_for(self.layout, self.paths, model="m2", effort="low")
        cs.check_effective(composed, self.layout, self.paths, model="m2", effort="low")
        with self.assertRaises(ReviewLaunchRefusedError):
            self.check(composed)

    def test_each_changed_value_is_refused_naming_the_leaf_and_its_value(self) -> None:
        clone = str(self.layout.clone)
        fs = "permissions", "tester", "filesystem"
        some_root = next(key for key in self.composed["config"]["permissions"]["tester"]["filesystem"]
                         if key.startswith("/usr"))
        cases = {
            "sandbox_mode": (("sandbox_mode",), "workspace-write", "sandbox_mode"),
            "default_permissions": (("default_permissions",), "other", "default_permissions"),
            "approval_policy": (("approval_policy",), "on-request", "approval_policy"),
            "notify": (("notify",), ["x"], "notify.0"),
            "model_provider": (("model_provider",), "evil", "model_provider"),
            "web_search": (("web_search",), "live", "web_search"),
            "project_root_markers": (("project_root_markers",), [".hg"], "project_root_markers.0"),
            "model": (("model",), "other", "model"),
            "effort": (("model_reasoning_effort",), "low", "model_reasoning_effort"),
            "tier": (("service_tier",), "fast", "service_tier"),
            "trust": (("projects", clone, "trust_level"), "trusted", f"projects.{clone}.trust_level"),
            "extra project": (("projects", "/elsewhere"), {"trust_level": "trusted"}, "projects./elsewhere.trust_level"),
            "inherit": (("shell_environment_policy", "inherit"), "all", "shell_environment_policy.inherit"),
            "set value": (("shell_environment_policy", "set", "PATH"), "/evil", "shell_environment_policy.set.PATH"),
            "set added": (("shell_environment_policy", "set", "SECRET"), "x", "shell_environment_policy.set.SECRET"),
            "include_only": (("shell_environment_policy", "include_only"), ["A"], "shell_environment_policy.include_only.0"),
            "entry changed": ((*fs, some_root), "write", f"permissions.tester.filesystem.{some_root}"),
            "entry added": ((*fs, "/root"), "read", "permissions.tester.filesystem./root"),
            "entry removed": ((*fs, some_root), KeyError, f"permissions.tester.filesystem.{some_root}"),
            "network": (("permissions", "tester", "network", "enabled"), True, "permissions.tester.network.enabled"),
            "network proxy": (("permissions", "tester", "network", "proxy_url"), "http://x", "permissions.tester.network.proxy_url"),
            "other profile": (("permissions", "other"), {"filesystem": {"/": "write"}}, "permissions.other.filesystem./"),
            "workspace write": (("sandbox_workspace_write", "network_access"), True, "sandbox_workspace_write.network_access"),
            "mcp": (("mcp_servers", "x"), {"command": "y"}, "mcp_servers.x.command"),
            "hook": (("hooks", "PreToolUse"), [{"command": "y"}], "hooks.PreToolUse.0.command"),
            "skill": (("skills", "bundled", "enabled"), True, "skills.bundled.enabled"),
            "feature": (("features", review.DISABLED_FEATURES[0]), True, f"features.{review.DISABLED_FEATURES[0]}"),
        }
        for name, (path, value, leaf) in cases.items():
            with self.subTest(name=name):
                composed = self.mutated(*path, value=value)
                error = self.refused(composed)
                self.assertEqual(error.evidence.get("check"), "effective", error)
                self.assertEqual(error.evidence.get("key"), leaf, error)
                if isinstance(value, (str, bool)):
                    self.assertEqual(error.evidence.get("value"), value, error)
                self.assertIn(leaf, str(error))

    def test_a_refusal_names_the_origin_layer_of_the_leaf(self) -> None:
        composed = self.mutated("approval_policy", value="on-request")
        error = self.refused(composed)
        self.assertEqual(error.evidence.get("value"), "on-request")
        self.assertEqual(error.evidence.get("source"), composed["origins"]["approval_policy"])

    def test_a_null_sibling_is_unset_and_an_empty_table_is_unset(self) -> None:
        composed = self.mutated("permissions", "tester", "description", value=None)
        composed["config"]["mcp_servers"] = None
        composed["config"]["notify"] = None
        self.check(composed)

    def test_a_feature_the_composition_does_not_report_is_not_refused(self) -> None:
        composed = self.mutated("features", value={})
        self.check(composed)
        del composed["config"]["features"]
        self.check(composed)

    def test_a_leaf_from_a_managed_or_other_origin_is_refused(self) -> None:
        for kind in ("mdm", "enterpriseManaged", "system", "legacyManagedConfigTomlFromFile",
                     "legacyManagedConfigTomlFromMdm", "project", "unknownType"):
            with self.subTest(kind=kind):
                import copy
                composed = copy.deepcopy(self.composed)
                composed["origins"]["web_search"] = {"name": {"type": kind}, "version": "sha256:0"}
                error = self.refused(composed)
                self.assertEqual(error.evidence.get("key"), "web_search")
                self.assertIn(kind, str(error))

    def test_a_bare_origin_without_the_name_wrapper_is_judged_too(self) -> None:
        composed = self.mutated("web_search", value="disabled")
        composed["origins"]["web_search"] = {"type": "mdm"}
        self.assertEqual(self.refused(composed).evidence.get("key"), "web_search")

    def test_the_layer_rules_are_the_reviewers(self) -> None:
        import copy
        cases = {
            "managed layer with configuration": {"name": {"type": "mdm"}, "config": {"web_search": "live"}},
            "enterprise layer with configuration": {"name": {"type": "enterpriseManaged"}, "config": {"x": 1}},
            "system layer with configuration": {"name": {"type": "system", "file": "/etc/codex/config.toml"},
                                                "config": {"approval_policy": "never"}},
            "legacy layer with configuration": {"name": {"type": "legacyManagedConfigTomlFromFile"},
                                                "config": {"x": 1}},
            "unknown layer": {"name": {"type": "cloud"}, "config": None},
            "enabled project layer": {"name": {"type": "project", "dotCodexFolder": "/x"}, "config": {"x": 1},
                                      "disabledReason": None},
        }
        for name, layer in cases.items():
            with self.subTest(name=name):
                composed = copy.deepcopy(self.composed)
                composed["layers"].append(layer)
                self.assertEqual(self.refused(composed).evidence.get("check"), "effective")
        with self.subTest(name="foreign user layer"):
            composed = copy.deepcopy(self.composed)
            composed["layers"][1]["name"]["file"] = "/home/someone/.codex/config.toml"
            self.refused(composed)
        with self.subTest(name="disabled project layer is accepted"):
            composed = copy.deepcopy(self.composed)
            composed["layers"].append({"name": {"type": "project"}, "config": None, "disabledReason": "untrusted"})
            self.check(composed)

    def test_an_instruction_bearing_key_from_no_judgeable_origin_is_refused(self) -> None:
        composed = self.mutated("developer_instructions", value="be evil")
        self.assertEqual(self.refused(composed).evidence.get("key"), "developer_instructions")

    def test_a_forbidden_key_set_by_a_layer_is_refused_whatever_the_layer(self) -> None:
        import copy
        cases = {"model_providers": {"x": {"base_url": "http://evil"}}, "openai_base_url": "http://evil",
                 "profiles": {"p": {"model": "m"}}, "auto_review": {"x": 1}}
        for key, value in cases.items():
            for kind in ("sessionFlags", "user"):
                with self.subTest(key=key, kind=kind):
                    composed = copy.deepcopy(self.composed)
                    composed["config"][key] = value
                    for leaf in review.flatten(value, key):
                        composed["origins"][leaf] = {"name": {"type": kind}, "version": "sha256:0"}
                    error = self.refused(composed)
                    self.assertEqual(error.evidence.get("check"), "effective")
                    self.assertTrue(str(error.evidence.get("key")).startswith(key), error)

    def test_a_forbidden_key_with_the_packaged_default_origin_is_not_refused(self) -> None:
        composed = self.mutated("openai_base_url", value="https://api.openai.com/v1")
        composed["origins"]["openai_base_url"] = {"name": {"type": "packagedDefaults"}, "version": "sha256:0"}
        self.check(composed)

    def test_the_testers_own_permissions_keys_are_not_forbidden_but_are_compared(self) -> None:
        self.check()
        self.refused(self.mutated("permissions", "tester", "network", "enabled", value=True))

    def test_a_value_that_is_not_a_config_read_result_is_refused(self) -> None:
        for composed in (None, [], {}, {"config": {}, "origins": {}}, {"config": [], "origins": {}, "layers": []},
                         {"config": {}, "origins": [], "layers": []}, {"config": {}, "origins": {}, "layers": {}}):
            with self.subTest(composed=composed), self.assertRaises(ReviewLaunchRefusedError):
                cs.check_effective(composed, self.layout, self.paths)


class CheckComposedTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.built = self.build()
        self.layout = self.built.layout
        self.roots_ = list(self.built.roots)
        self.composed = composed_for(self.layout, self.roots_)

    def answer(self, composed: dict | None = None) -> bytes:
        body = {"id": 2, "result": composed if composed is not None else self.composed}
        return (json.dumps({"id": 1, "result": {}}) + "\n" + json.dumps(body) + "\n").encode()

    def compose(self, run, **kw) -> None:
        cs.check_composed(self.layout, self.built.config_text, self.roots_, self.binary, self.built.environment,
                          run=run, **kw)

    def test_a_clean_answer_passes_and_the_home_is_checked_after_the_command_and_before_the_comparison(self) -> None:
        order = []

        def run(command, **kw):
            order.append("run")
            return result(self.answer())

        with mock.patch.object(cs, "check_home", side_effect=lambda *a: order.append("home")), \
                mock.patch.object(cs, "check_effective", side_effect=lambda *a, **k: order.append("effective")):
            self.compose(run)
        self.assertEqual(order, ["run", "home", "effective"])

    def test_the_command_is_the_constructors_run_in_the_clone_with_the_testers_environment(self) -> None:
        seen = {}

        def run(command, **kw):
            seen.update(kw, command=command)
            return result(self.answer())

        self.compose(run)
        self.assertEqual(seen["command"], cs.compose_argv(self.layout, binary=self.binary))
        self.assertEqual(seen["cwd"], self.layout.clone)
        self.assertEqual(seen["env"], self.built.environment)
        self.assertIs(seen["hold_open_until"], review._answered)
        requests = [json.loads(line) for line in seen["stdin_text"].splitlines()]
        self.assertEqual([r.get("method") for r in requests], ["initialize", "initialized", "config/read"])
        self.assertEqual(requests[2]["params"], {"includeLayers": True, "cwd": str(self.layout.clone)})

    def test_a_failed_timed_out_empty_or_error_answer_is_a_refusal_with_check_effective(self) -> None:
        cases = {
            "exit": result(b"", code=2),
            "timeout": result(b"", code=None, timed_out=True),
            "empty": result(b""),
            "garbage": result(b"not json\n"),
            "error": result(json.dumps({"id": 2, "error": {"message": "no"}}).encode() + b"\n"),
            "not-a-result": result(json.dumps({"id": 2, "result": "x"}).encode() + b"\n"),
        }
        for name, outcome in cases.items():
            with self.subTest(name=name), self.assertRaises(ReviewLaunchRefusedError) as caught:
                self.compose(lambda command, _outcome=outcome, **kw: _outcome)
            self.assertEqual(caught.exception.evidence["check"], "effective")

    def test_a_refusal_by_check_effective_is_not_swallowed(self) -> None:
        import copy
        bad = copy.deepcopy(self.composed)
        bad["config"]["approval_policy"] = "on-request"
        with self.assertRaises(ReviewLaunchRefusedError) as caught:
            self.compose(lambda command, **kw: result(self.answer(bad)))
        self.assertEqual(caught.exception.evidence["key"], "approval_policy")

    def test_a_surprise_entry_in_the_home_after_the_command_is_a_refusal(self) -> None:
        def run(command, **kw):
            (self.layout.codex_home / "hooks.json").write_text("{}")
            return result(self.answer())

        with self.assertRaises(ReviewLaunchRefusedError) as caught:
            self.compose(run)
        self.assertEqual(caught.exception.evidence["check"], "home")

    def test_check_launch_never_runs_the_composing_command(self) -> None:
        calls = []

        def run(command, **kw):
            calls.append(command[1])
            return result(NO_SERVERS if command[1] == "mcp" else FEATURES)

        cs.check_launch(self.layout, self.built.config_text, self.binary, self.built.environment,
                        system_dir=self.system, run=run)
        self.assertEqual(calls, ["mcp", "features"])


class ReviewerUnchangedTest(unittest.TestCase):
    def test_the_reviewers_exec_still_selects_workspace_write(self) -> None:
        command = review.argv("exec", binary=Path("/x/codex"), tree=Path("/t"), verdict=Path("/v"))
        self.assertEqual(command[command.index("-s") + 1], "workspace-write")
        self.assertNotIn("default_permissions", " ".join(command))


def targets(base: Path = Path("/w")) -> cs.ProbeTargets:
    return cs.ProbeTargets(
        clone=base / "clone", scratch=base / "scratch", sibling=base / "sibling", stand_in_home=base / "ophome",
        canary_file=base / "ophome" / "canary", canary_env="OPERATOR_CANARY", credential=base / "codex-home" / "auth.json",
        push_origin=base / "origin.git", tcp_port=4321, socket_path=base / "sock")


def good_output() -> str:
    lines = [f"PROBE {name} {want}" for name, want in cs.PROBE_EXPECTED.items()]
    return "\n".join(lines + ["PROBE tmp WROTE", "PROBE vartmp WROTE", "PROBE sibling WROTE"]) + "\n"


class ProbeCommandTest(unittest.TestCase):
    def test_every_probe_name_appears_once_and_every_path_is_absolute(self) -> None:
        command = cs.probe_command(targets(), marker="m1")
        names = set(cs.PROBE_EXPECTED) | set(cs.PROBE_INFORMATIONAL) | {"rename", "delete"}
        for name in names:
            with self.subTest(name=name):
                hits = command.count(f"PROBE {name} ") + command.count(f"w {name} ")
                self.assertGreaterEqual(hits, 1)
        for path in ("/w/clone/src.txt", "/w/clone/.git/config", "/w/scratch/probe", "/w/ophome/canary",
                     "/w/codex-home/auth.json", "/w/origin.git", "/tmp/m1", "/var/tmp/m1", "/usr/bin/gh"):
            self.assertIn(path, command)
        self.assertIn("OPERATOR_CANARY", command)
        self.assertIn("4321", command)

    def test_the_prompt_carries_the_command_verbatim(self) -> None:
        command = cs.probe_command(targets(), marker="m1")
        self.assertIn(command, cs.probe_prompt(command))


class JudgeTest(unittest.TestCase):
    def test_the_expected_output_passes(self) -> None:
        self.assertEqual(cs.judge(good_output()), [])

    def test_each_failure_mode_fails_closed(self) -> None:
        good = good_output()
        cases = {
            "a missing line": good.replace("PROBE git REFUSED\n", ""),
            "a repeated line with a different result": good + "PROBE git WROTE\n",
            "an unexpected result": good.replace("PROBE tracked REFUSED", "PROBE tracked WROTE"),
            "an unknown probe": good + "PROBE mystery WROTE\n",
            "a missing informational line": good.replace("PROBE tmp WROTE\n", ""),
            "an informational line with an odd result": good.replace("PROBE tmp WROTE", "PROBE tmp ODD"),
            "unreadable output": "\x00\xff garbage",
            "empty output": "",
        }
        for name, output in cases.items():
            with self.subTest(name=name):
                self.assertTrue(cs.judge(output))

    def test_a_repeated_line_with_the_same_result_is_not_a_failure(self) -> None:
        self.assertEqual(cs.judge(good_output() + "PROBE git REFUSED\n"), [])


class SandboxProbeArgvTest(Fixture):
    def test_the_modelless_form_selects_the_profile_and_no_other_sandbox(self) -> None:
        command = cs.sandbox_probe_argv(self.layout, binary=self.binary, command="echo hi")
        self.assertEqual(command[:6], [str(self.binary), "sandbox", "-P", "tester", "-C", str(self.layout.clone)])
        self.assertEqual(command[-4:], ["--", "sh", "-c", "echo hi"])
        self.assertNotIn("-s", command)
        self.assertIn('shell_environment_policy.inherit="none"', command)


if __name__ == "__main__":
    unittest.main()
