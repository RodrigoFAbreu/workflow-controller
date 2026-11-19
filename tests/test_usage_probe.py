"""`workflow-controller-uf1` CP1: the ``usage --refresh`` probes (`controller.usage_probe`) against
``tests/fake_codex.py`` and ``tests/fake_claude_review.py``. Every test uses isolated homes, no network and
spends no allowance."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import harness, review, usage, usage_probe  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests import fake_claude_review, fake_codex  # noqa: E402

CODEX_AUTH = json.dumps({"OPENAI_API_KEY": None, "tokens": {"refresh_token": "operator"}})
CLAUDE_AUTH = json.dumps({"claudeAiOauth": {"accessToken": "operator"}})


def tree_digest(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
    return out


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="uf1-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.codex_home = self.tmp / "operator-codex"
        self.claude_home = self.tmp / "operator-claude"
        self.codex_home.mkdir()
        self.claude_home.mkdir()
        (self.codex_home / "auth.json").write_text(CODEX_AUTH)
        (self.claude_home / ".credentials.json").write_text(CLAUDE_AUTH)
        self.codex_log = self.tmp / "codex.log"
        self.claude_log = self.tmp / "claude.log"
        self.system = self.tmp / "etc-empty"
        self.system.mkdir()

    def codex(self, **scenario) -> str:
        scenario.setdefault("log", str(self.codex_log))
        return str(fake_codex.write_binary(self.tmp / "bin-codex", scenario))

    def claude(self, **scenario) -> str:
        scenario.setdefault("log", str(self.claude_log))
        return str(fake_claude_review.write_binary(self.tmp / "bin-claude", scenario))

    def refresh(self, providers=("codex", "claude"), **kwargs):
        if "codex_binary" not in kwargs:
            kwargs["codex_binary"] = self.codex()
        if "claude_binary" not in kwargs:
            kwargs["claude_binary"] = self.claude()
        kwargs.setdefault("codex_system_dir", self.system)
        kwargs.setdefault("claude_system_dir", self.system)
        return usage_probe.refresh(providers, runtime_root=self.runtime, codex_home=self.codex_home,
                                   claude_home=self.claude_home, **kwargs)

    def record(self) -> dict:
        return usage.load_record(self.runtime)["readings"]

    def leftovers(self) -> list[str]:
        area = self.runtime / usage_probe.AREA
        return sorted(p.name for p in area.iterdir()) if area.exists() else []

    def codex_runs(self, kind: str) -> list[dict]:
        return [r for r in fake_codex.read_log(self.codex_log) if r.get("kind") == kind]


class ModelAndArgvTest(ProbeCase):
    def test_each_named_model_is_declared_and_the_effort_is_the_lowest(self):
        self.assertIn(usage_probe.CLAUDE_MODEL, harness.CLAUDE_CODE.models)
        self.assertIn(usage_probe.CODEX_MODEL, harness.CODEX.models)
        self.assertEqual(usage_probe.CLAUDE_EFFORT, harness.CLAUDE_CODE.efforts[0])
        self.assertEqual(usage_probe.CODEX_EFFORT, harness.CODEX.efforts[0])
        self.assertEqual(usage_probe.CODEX_EFFORT, "high")

    def test_the_claude_flag_sets_are_equal_apart_from_the_listed_differences(self):
        def flags(command):
            return {item for item in command if item.startswith("--") or item == "-p"}

        probe = flags(usage_probe.claude_argv(Path("claude")))
        reviewed = flags(review.claude_argv(binary=Path("claude"), model="claude-opus-5-5", effort="high"))
        only_probe = {"--max-turns"}  # --tools "" is the same flag as the review's, with an empty value
        only_review = {"--permission-mode", "--permission-prompts"}  # no tool exists to prompt for
        self.assertEqual(probe - reviewed, only_probe)
        self.assertEqual(reviewed - probe, only_review)

    def test_the_claude_probe_has_no_tools_one_turn_and_the_cheap_model(self):
        command = usage_probe.claude_argv(Path("claude"))
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--max-turns") + 1], "1")
        self.assertEqual(command[command.index("--model") + 1], "haiku")

    def test_check_probe_argv_names_the_first_difference(self):
        command = usage_probe.claude_argv(Path("claude")) + ["--extra"]
        with self.assertRaisesRegex(ReviewLaunchRefusedError, "--extra"):
            usage_probe.check_probe_argv("claude", command)
        with self.assertRaises(ReviewLaunchRefusedError):
            usage_probe.check_probe_argv("exec", usage_probe.codex_exec_argv(Path("codex"), Path("/t"))[:-2],
                                         tree=Path("/t"))
        usage_probe.check_probe_argv("app-server", usage_probe.codex_compose_argv(Path("codex")))

    def test_the_codex_compose_argv_has_the_exec_set_and_no_dash_c_dir(self):
        compose = usage_probe.codex_compose_argv(Path("codex"))
        exec_argv = usage_probe.codex_exec_argv(Path("codex"), Path("/t"))
        for flag in ("-s", "--skip-git-repo-check", "--ignore-rules", "--color", "-C", usage_probe.PROMPT):
            self.assertNotIn(flag, compose)
        self.assertEqual(compose[2:-2], exec_argv[2:exec_argv.index("-s")])
        self.assertEqual(compose[-2:], ["--listen", "stdio://"])
        self.assertNotIn("--ephemeral", exec_argv)
        self.assertIn("--skip-git-repo-check", exec_argv)


class RecordingTest(ProbeCase):
    def test_a_reading_is_recorded_for_each_provider(self):
        results = self.refresh()
        self.assertEqual({p: r.outcome for p, r in results.items()}, {"codex": "ok", "claude": "ok"})
        record = self.record()
        self.assertEqual(sorted(record["codex"]), ["five_hour", "weekly"])
        self.assertEqual(record["codex"]["five_hour"]["percent"], 7.0)
        self.assertIn("five_hour", record["claude"])
        self.assertEqual(self.leftovers(), [])

    def test_the_codex_probe_argv_home_and_cwd(self):
        self.refresh(("codex",))
        (run,) = self.codex_runs("exec")
        argv = run["argv"]
        self.assertEqual(argv[-1], usage_probe.PROMPT)
        self.assertIn("--skip-git-repo-check", argv)
        self.assertNotIn("--ephemeral", argv)
        self.assertNotIn("-o", argv)
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        self.assertEqual(argv[argv.index("-C") + 1], run["cwd"])
        self.assertIn(f'model="{usage_probe.CODEX_MODEL}"', argv)
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertTrue(run["codex_home"].startswith(str(self.runtime / usage_probe.AREA)))
        self.assertTrue(run["cwd"].startswith(str(self.runtime / usage_probe.AREA)))

    def test_the_compose_command_carries_the_probes_own_overrides(self):
        self.refresh(("codex",))
        (compose,) = self.codex_runs("compose")
        self.assertEqual(compose["argv"][compose["argv"].index("app-server"):],
                         usage_probe.codex_compose_argv(Path("codex"))[1:])
        self.assertNotIn("-C", compose["argv"])
        exec_argv = self.codex_runs("exec")[0]["argv"]
        self.assertEqual(compose["argv"][1:-2], exec_argv[1:exec_argv.index("-s")])

    def test_the_claude_probe_argv_and_environment(self):
        self.refresh(("claude",))
        (run,) = fake_claude_review.read_log(self.claude_log)
        self.assertEqual(run["argv"], usage_probe.claude_argv(Path("claude"))[1:])
        self.assertEqual(run["prompt"], usage_probe.PROMPT)
        env = run["env"]
        base = Path(env["CLAUDE_CONFIG_DIR"]).parent
        self.assertEqual(base.parent, self.runtime / usage_probe.AREA)
        self.assertEqual(env["HOME"], str(base / "home"))
        self.assertEqual(env["TMPDIR"], str(base / "tmp"))
        for key, value in review.CLAUDE_FIXED_ENV.items():
            self.assertEqual(env[key], value)
        self.assertIn(review.OWNERSHIP_VAR, env)
        self.assertEqual(run["cwd"], str(base / "tree"))

    def test_the_codex_environment_is_the_allowlist(self):
        self.refresh(("codex",))
        for run in self.codex_runs("exec") + self.codex_runs("compose"):
            self.assertLessEqual({k for k in run["env"] if k not in ("PWD", "SHLVL", "_")},
                                 set(review.ENV_ALLOWLIST) | {"LC_CTYPE"})
            self.assertEqual(run["env"]["CODEX_HOME"], run["codex_home"])
            self.assertIn(review.OWNERSHIP_VAR, run["env"])
            self.assertTrue(run["env"]["HOME"].startswith(str(self.runtime / usage_probe.AREA)))

    def test_the_operator_homes_are_byte_identical_and_a_changed_credential_is_copied_back(self):
        before = (tree_digest(self.codex_home), tree_digest(self.claude_home))
        results = self.refresh()
        self.assertEqual((tree_digest(self.codex_home), tree_digest(self.claude_home)), before)
        self.assertEqual({r.credentials for r in results.values()}, {"unchanged"})
        results = self.refresh(claude_binary=self.claude(refresh_auth=True))
        self.assertEqual(results["claude"].credentials, "copied")
        self.assertIn("refreshed", (self.claude_home / ".credentials.json").read_text())
        self.assertEqual(tree_digest(self.codex_home), before[0])

    def test_a_refreshed_codex_credential_is_copied_back(self):
        results = self.refresh(("codex",), codex_binary=self.codex(exec={"refresh_auth": True}))
        self.assertEqual(results["codex"].credentials, "copied")
        self.assertIn("rotated-", (self.codex_home / "auth.json").read_text())

    def test_a_window_that_ends_later_replaces_a_stale_higher_percent_and_a_lower_percent_does_not(self):
        now = time.time()
        usage.record_reading(self.runtime, [
            usage.make_reading("codex", "weekly", 90.0, now + 100, now - 10, "old"),
            usage.make_reading("codex", "five_hour", 50.0, int(now) + 3600, now - 10, "same-window")])
        self.refresh(("codex",))
        record = self.record()["codex"]
        self.assertEqual(record["weekly"]["percent"], 3.0)  # the later window won
        self.assertEqual(record["five_hour"]["percent"], 50.0)  # the same window never falls

    def test_the_layout_is_gone_after_every_case(self):
        self.refresh(codex_binary=str(self.tmp / "missing"), claude_binary=self.claude(verdict="error"))
        self.assertEqual(self.leftovers(), [])


class FailureTest(ProbeCase):
    def assertUnchanged(self, before):
        self.assertEqual(self.record(), before)
        self.assertEqual(self.leftovers(), [])

    def test_a_missing_binary_is_named_and_the_other_provider_still_runs(self):
        results = self.refresh(codex_binary=str(self.tmp / "missing"))
        self.assertEqual(results["codex"].outcome, "not_found")
        self.assertEqual(results["claude"].outcome, "ok")
        self.assertNotIn("codex", self.record())
        self.assertIn("claude", self.record())

    def test_a_failing_exit_leaves_the_record_unchanged(self):
        before = self.record()
        results = self.refresh(codex_binary=self.codex(exec={"exit": 3}), claude_binary=self.claude(verdict="error"))
        self.assertEqual({p: r.outcome for p, r in results.items()}, {"codex": "failed", "claude": "failed"})
        self.assertIn("exit 3", results["codex"].detail)
        self.assertUnchanged(before)

    def test_a_timeout_leaves_the_record_unchanged(self):
        before = self.record()
        results = self.refresh(("codex",), codex_binary=self.codex(exec={"verdict": "hang"}, hang_seconds=30,
                                                                  orphan_seconds=0), timeout=2)
        self.assertEqual(results["codex"].outcome, "timed_out")
        self.assertUnchanged(before)

    def test_output_without_a_reading_is_no_reading(self):
        before = self.record()

        def silent(command, **kwargs):
            if "app-server" in command:
                return review.run_bounded(command, **kwargs)
            return review.RunResult(0, False, b'{"type":"result"}\n', b"", 1, 1)

        results = self.refresh(run=silent)
        self.assertEqual({p: r.outcome for p, r in results.items()}, {"codex": "no_reading", "claude": "no_reading"})
        self.assertUnchanged(before)

    def test_older_operator_rollouts_are_not_read(self):
        fake_codex._write_rollout(self.codex_home, {"rate_limits": {"primary": 99.0, "secondary": 99.0}})
        before = self.record()

        def silent(command, **kwargs):
            if "app-server" in command:
                return review.run_bounded(command, **kwargs)
            return review.RunResult(0, False, b"", b"", 1, 1)

        results = self.refresh(("codex",), run=silent)
        self.assertEqual(results["codex"].outcome, "no_reading")
        self.assertUnchanged(before)

    def test_a_binary_that_cannot_be_started_is_failed_and_the_other_provider_still_runs(self):
        broken = self.tmp / "bin-broken" / "codex"
        broken.parent.mkdir()
        broken.write_text("#!/nonexistent/interpreter\n")
        broken.chmod(0o755)
        before = self.record()
        results = self.refresh(codex_binary=str(broken))
        self.assertEqual({p: r.outcome for p, r in results.items()}, {"codex": "failed", "claude": "ok"})
        self.assertIn("could not run", results["codex"].detail)
        self.assertEqual(self.record().get("codex"), before.get("codex"))
        self.assertIn("claude", self.record())
        self.assertEqual(self.leftovers(), [])

    def test_the_timeout_cause_names_the_deadline_the_probe_ran_under(self):
        results = self.refresh(("codex",), codex_binary=self.codex(exec={"verdict": "hang"}, hang_seconds=30,
                                                                  orphan_seconds=0), timeout=2)
        self.assertIn("2-second deadline", results["codex"].detail)

    def test_an_operator_home_without_a_credential_is_failed_with_the_cause(self):
        (self.codex_home / "auth.json").unlink()
        results = self.refresh(("codex",))
        self.assertEqual(results["codex"].outcome, "failed")
        self.assertIn("credential", results["codex"].detail)
        self.assertEqual(self.codex_runs("exec"), [])


class SweepTest(ProbeCase):
    def test_a_stale_layout_is_removed_at_the_start_and_a_younger_one_is_left(self):
        area = self.runtime / usage_probe.AREA
        old, young = area / "old-job", area / "young-job"
        for layout in (old, young):
            (layout / "codex-home").mkdir(parents=True)
            (layout / "codex-home" / "auth.json").write_text("{}")
        os.utime(old, (time.time() - 400, time.time() - 400))
        self.refresh(("codex",))
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())


class BoundaryTest(ProbeCase):
    def managed_claude(self):
        (self.system / "managed-settings.json").write_text('{"hooks": {}}')

    def test_a_managed_layer_with_content_refuses_each_provider_before_launch(self):
        self.managed_claude()
        (self.system / "managed_config.toml").write_text('model = "x"\n')
        before, homes = self.record(), (tree_digest(self.codex_home), tree_digest(self.claude_home))
        results = self.refresh()
        for provider in ("codex", "claude"):
            self.assertEqual(results[provider].outcome, "failed")
            self.assertIn("carries content", results[provider].detail)
        self.assertEqual(self.codex_runs("exec") + self.codex_runs("compose"), [])
        self.assertEqual(fake_claude_review.read_log(self.claude_log), [])
        self.assertEqual(self.record(), before)
        self.assertEqual((tree_digest(self.codex_home), tree_digest(self.claude_home)), homes)
        self.assertEqual(self.leftovers(), [])

    def test_a_startup_loaded_entry_in_the_codex_home_is_refused_before_launch(self):
        original = review.build_home

        def with_hooks(layout, operator_home):
            digest = original(layout, operator_home)
            (layout.codex_home / "hooks.json").write_text("{}")
            return digest

        with mock.patch.object(review, "build_home", with_hooks):
            results = self.refresh(("codex",))
        self.assertEqual(results["codex"].outcome, "failed")
        self.assertIn("hooks.json", results["codex"].detail)
        self.assertEqual(self.codex_runs("exec") + self.codex_runs("compose"), [])

    def test_a_managed_layer_in_the_composed_configuration_is_refused_before_inference(self):
        managed = self.tmp / "managed.toml"
        managed.write_text('[mcp_servers.evil]\ncommand = "true"\n')
        before, homes = self.record(), tree_digest(self.codex_home)
        results = self.refresh(("codex",), codex_binary=self.codex(managed=str(managed)))
        self.assertEqual(results["codex"].outcome, "failed")
        self.assertIn("enterpriseManaged", results["codex"].detail)
        self.assertIn("mcp_servers", results["codex"].detail)
        self.assertEqual(len(self.codex_runs("compose")), 1)
        self.assertEqual(self.codex_runs("exec"), [])
        self.assertEqual(self.record(), before)
        self.assertEqual(tree_digest(self.codex_home), homes)
        self.assertEqual(self.leftovers(), [])

    def composed(self, layers, config=None, origins=None):
        return {"config": config or {}, "origins": origins or {}, "layers": layers}

    def user_layer(self):
        return {"name": {"type": "user", "file": "/h/config.toml"}, "config": {}, "disabledReason": None}

    def test_check_probe_composed_refusals(self):
        home = Path("/h")
        usage_probe.check_probe_composed(self.composed([self.user_layer()]), codex_home=home)
        cases = {
            "unknown layer": self.composed([{"name": {"type": "weird"}}]),
            "mdm layer with a leaf": self.composed([{"name": {"type": "mdm"}, "config": {"a": 1}}]),
            "managed file layer": self.composed([{"name": {"type": "legacyManagedConfigTomlFromMdm"},
                                                  "config": {"mcp_servers": {"x": {"command": "y"}}}}]),
            "enabled project layer": self.composed([{"name": {"type": "project"}, "config": {}, "disabledReason": None}]),
            "wrong user layer": self.composed([{"name": {"type": "user", "file": "/other/config.toml"}}]),
            "mcp servers": self.composed([self.user_layer()], {"mcp_servers": {"x": {"command": "y"}}},
                                         {"mcp_servers.x.command": {"type": "user", "file": "/h/config.toml"}}),
            "hooks": self.composed([self.user_layer()], {"hooks": {"a": 1}}),
            "notify": self.composed([self.user_layer()], {"notify": ["x"]}),
            "origin outside the allowed": self.composed([self.user_layer()], {"model": "m"},
                                                        {"model": {"type": "mdm"}}),
        }
        for name, composed in cases.items():
            with self.subTest(name), self.assertRaises(ReviewLaunchRefusedError):
                usage_probe.check_probe_composed(composed, codex_home=home)

    def test_compose_failures_are_refusals_and_inference_never_starts(self):
        started: list[str] = []
        outcomes = {
            "exit": review.RunResult(2, False, b"", b"boom", 1, 1),
            "timeout": review.RunResult(None, True, b"", b"", 1, 1),
            "no answer": review.RunResult(0, False, b'{"id":1,"result":{}}\n', b"", 1, 1),
            "error answer": review.RunResult(0, False, b'{"id":2,"error":{"message":"no"}}\n', b"", 1, 1),
        }
        for name, outcome in outcomes.items():
            def run(command, **kwargs):
                started.append(command[1])
                return outcome

            with self.subTest(name):
                results = self.refresh(("codex",), run=run)
                self.assertEqual(results["codex"].outcome, "failed")
                self.assertEqual(self.leftovers(), [])
        self.assertEqual(set(started), {"app-server"})


if __name__ == "__main__":
    unittest.main()
