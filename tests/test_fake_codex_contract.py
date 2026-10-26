"""`tests/fake_codex.py` held to the contract D4 and D12 state for it
(`workflow-controller-codex-reviewer` CP2): the committed capture it is built
from, what it composes and runs, and its orphan watchdog.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests import fake_codex, process_fixtures  # noqa: E402

CODEX_FIXTURES = REPO_ROOT / "tests" / "harness_contract" / "codex"


def review_sources():
    from controller import review
    return review.CONFIG_SOURCES


class FakeCodexContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fake-codex-", dir=process_fixtures.build_dir()))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / "auth.json").write_text("{}")
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.log = self.tmp / "log"
        self.env = {"PATH": "/usr/bin:/bin", "CODEX_HOME": str(self.home), "HOME": str(self.tmp)}

    def fake(self, scenario=None, name="codex"):
        return fake_codex.write_binary(self.tmp / "bin", {"log": str(self.log), "orphan_seconds": 30, **(scenario or {})},
                                       name=name)

    def run_fake(self, args, scenario=None, stdin=""):
        return subprocess.run([str(self.fake(scenario)), *args], cwd=self.work, env=self.env, input=stdin,
                              capture_output=True, text=True, timeout=60, check=False)

    CAPTURES = ("features_list.0.163.0.txt", "exec_argv.json", "rollout_excerpt.jsonl", "stderr_shapes.txt",
                "compose.txt", "state_entries.txt", "env.txt", "flags.txt", "sandbox_probe.txt")

    def test_every_capture_digest_is_the_one_the_readme_records(self):
        readme = (CODEX_FIXTURES / "README.md").read_text()
        for name in self.CAPTURES:
            with self.subTest(name):
                digest = hashlib.sha256((CODEX_FIXTURES / name).read_bytes()).hexdigest()
                self.assertIn(f"`{name}`", readme)
                self.assertIn(digest, readme)

    def test_the_pre_step_is_recorded_and_the_harness_registered(self):
        from controller import review
        self.assertNotIn("pre-step is still open", re.sub(r"\s+", " ", (CODEX_FIXTURES / "README.md").read_text()))
        self.assertTrue(review.PRE_STEP_CAPTURED)
        self.assertEqual(sorted(review.HARNESSES), ["claude", "codex"])

    def test_review_argv_is_the_captured_recommended_invocation(self):
        """`review.argv("exec", ...)` equals the real command line the pre-step ran with the recommended
        overrides (`exec_argv.json` `recommended_run`), paths aside and without the operator-rule `-m`.

        `exec_argv.json` is the 0.162.1 capture and is not rewritten; the three `--disable` pairs for the features
        0.163.0 added (`review.DISABLED_FEATURES`, evidenced by the 2026-10-26 probe) are inserted into its sorted
        `--disable` run."""
        from controller import review
        captured = json.loads((CODEX_FIXTURES / "exec_argv.json").read_text())["recommended_run"]["argv"]
        tree, tmpdir, verdict = "/home/rodrigo/.cache/c7-prestep/work/tree-probe3", \
            "/home/rodrigo/.cache/c7-prestep/work/homes/x3/tmp", "/home/rodrigo/.cache/c7-prestep/work/homes/x3/verdict.md"
        at = captured.index("-m")
        without_m = captured[:at] + captured[at + 2:]
        first = without_m.index("--disable")
        last = len(without_m) - 1 - without_m[::-1].index("--disable")
        run = sorted({without_m[i + 1] for i in range(first, last + 1, 2)}
                     | {"api_key_cyber_access_programs", "cli_daybreak", "code_mode_interrupt"})
        without_m = without_m[:first] + [x for name in run for x in ("--disable", name)] + without_m[last + 2:]
        built = review.argv("exec", binary=Path(captured[0]), tree=Path(tree), verdict=Path(verdict),
                            tmpdir=Path(tmpdir), prompt="-")
        self.assertEqual(built, without_m)
        review.check_argv("exec", built, tree=Path(tree), verdict=Path(verdict), tmpdir=Path(tmpdir))

    def test_the_composing_command_carries_the_captured_exec_overrides(self):
        from controller import review
        composing = review.argv("app-server", binary=Path("/x/codex"), tree=Path("/t"), tmpdir=Path("/m"))
        text = (CODEX_FIXTURES / "compose.txt").read_text()
        self.assertIn("app-server --strict-config", text)
        self.assertIn("includeLayers: true", text)
        self.assertEqual(composing[1:3], ["app-server", "--strict-config"])
        self.assertEqual(composing[-2:], ["--listen", "stdio://"])

    def test_the_constants_follow_the_captures(self):
        from controller import review
        state = (CODEX_FIXTURES / "state_entries.txt").read_text()
        for name in review.CODEX_STATE_ENTRIES:
            self.assertIn(name, state)
        self.assertEqual(review.CODEX_STARTUP_SUBENTRIES, frozenset())
        flags = (CODEX_FIXTURES / "flags.txt").read_text()
        for kind in ("features", "mcp"):
            self.assertNotIn("--strict-config", review.KIND_FLAGS[kind])
        self.assertIn("app-server:  accepted", flags)
        for layer in review.CONFIG_SOURCES:
            self.assertIn(layer, (CODEX_FIXTURES / "compose.txt").read_text())
        self.assertIn("code_mode_host", review.TOOL_ALLOWLIST)
        self.assertIn("code-mode host is disabled", (CODEX_FIXTURES / "sandbox_probe.txt").read_text())

    def test_the_fakes_shapes_are_the_captured_ones(self):
        """config/read, the rollout and the home's state entries, in the captured shapes."""
        rollout = [json.loads(line) for line in (CODEX_FIXTURES / "rollout_excerpt.jsonl").read_text().splitlines()
                   if line.startswith("{")]
        failing = next(e for e in rollout if e.get("payload", {}).get("error"))
        self.assertEqual(sorted(failing["payload"]["error"]), ["codex_error_info", "message"])
        fake_codex._write_rollout(self.home, {}, {"verdict": "limit"})
        written = [json.loads(line) for p in (self.home / "sessions").rglob("rollout-*.jsonl")
                   for line in p.read_text().splitlines()]
        self.assertEqual([e["type"] for e in written], ["session_meta", "event_msg", "event_msg"])
        self.assertEqual(sorted(written[2]["payload"]["error"]), ["codex_error_info", "message"])
        token_count = next(e for e in rollout if e.get("payload", {}).get("type") == "token_count")
        self.assertEqual(set(written[1]["payload"]["rate_limits"]) - {"limit_id", "limit_name", "credits", "plan_type",
                                                                      "rate_limit_reached_type"},
                         {"primary", "secondary"})
        self.assertIn("total_token_usage", token_count["payload"]["info"])
        self.assertIn("total_token_usage", written[1]["payload"]["info"])

    def test_version_and_the_features_list_follow_the_capture_and_the_disable_flags(self):
        self.assertEqual(self.run_fake(["--version"]).stdout.strip(), fake_codex.VERSION)
        listing = self.run_fake(["features", "list", "--disable", "browser_use", "--disable", "hooks"]).stdout
        state = {p[0]: p[-1] == "true" for p in (line.split() for line in listing.splitlines())}
        self.assertFalse(state["browser_use"])
        self.assertFalse(state["hooks"])
        self.assertTrue(state["computer_use"])
        capture = (CODEX_FIXTURES / "features_list.0.163.0.txt").read_text().splitlines()
        self.assertEqual(len(state), len([line for line in capture if line.split()]))

    def test_mcp_list_reports_the_composed_servers_without_running_them(self):
        marker = self.tmp / "ran"
        (self.home / "config.toml").write_text(f'[mcp_servers.anything]\ncommand = "touch {marker}"\n')
        out = self.run_fake(["mcp", "list", "-c", "mcp_servers={}"]).stdout
        self.assertIn("anything", out)           # -c mcp_servers={} merges, it does not replace
        self.assertFalse(marker.exists())
        (self.home / "config.toml").unlink()
        self.assertIn("No MCP servers configured", self.run_fake(["mcp", "list"]).stdout)

    def test_exec_runs_what_the_composed_configuration_names_and_only_that(self):
        ran = self.tmp / "ran"
        script = self.tmp / "s.sh"
        script.write_text(f'#!/bin/sh\necho "$0" >> {ran}\n')
        script.chmod(0o755)
        (self.home / "config.toml").write_text(
            f'notify = ["{script}"]\n[mcp_servers.m]\ncommand = "{script}"\n[hooks.h]\ncommand = "{script}"\n')
        run = lambda *extra: self.run_fake(["exec", "-C", str(self.work), *extra, "-"], stdin="p")  # noqa: E731
        run("-c", "notify=[]", "--disable", "hooks")
        layers = [r["layer"] for r in fake_codex.read_log(self.log)[-1]["ran"]]
        self.assertEqual(layers, ["mcp_servers"])  # notify replaced by the empty list, hooks disabled
        self.log.unlink()
        run()
        self.assertEqual([r["layer"] for r in fake_codex.read_log(self.log)[-1]["ran"]], ["mcp_servers", "hooks", "notify"])

    def test_project_layers_stop_at_the_root_marker_override(self):
        (self.tmp / ".codex").mkdir()
        (self.tmp / ".codex" / "config.toml").write_text('[mcp_servers.ancestor]\ncommand = "true"\n')
        (self.work / ".git").mkdir()
        self.assertIn("ancestor", self.run_fake(["mcp", "list"]).stdout)
        self.assertIn("No MCP servers", self.run_fake(["mcp", "list", "-c", 'project_root_markers=[".git"]']).stdout)

    def test_a_profile_overlays_only_when_selected(self):
        (self.home / "config.toml").write_text('[profiles.ops.mcp_servers.p]\ncommand = "true"\n')
        self.assertIn("No MCP servers", self.run_fake(["mcp", "list"]).stdout)
        self.assertIn("p  ", self.run_fake(["mcp", "list", "-p", "ops"]).stdout)

    def config_read(self, extra_args, scenario=None):
        requests = [{"id": 1, "method": "initialize", "params": {}}, {"method": "initialized"},
                    {"id": 2, "method": "config/read", "params": {"includeLayers": True, "cwd": str(self.work)}}]
        out = self.run_fake(["app-server", "--strict-config", *extra_args, "--listen", "stdio://"], scenario,
                            "".join(json.dumps(r) + "\n" for r in requests)).stdout
        return next(json.loads(line) for line in out.splitlines() if json.loads(line).get("id") == 2)["result"]

    def test_a_managed_layer_outranks_the_command_line(self):
        managed = self.tmp / "m.toml"
        managed.write_text('approval_policy = "on-request"\n')
        result = self.config_read(["-c", 'approval_policy="never"'], {"managed": str(managed)})
        self.assertEqual(result["config"]["approval_policy"], "on-request")
        self.assertEqual(result["origins"]["approval_policy"]["type"], "enterpriseManaged")

    def test_config_read_has_the_captured_shape(self):
        (self.home / "config.toml").write_text('model_provider = "openai"\n')
        overrides = ["-c", 'notify=[]', "-c", "sandbox_workspace_write.writable_roots=[\"/m\"]",
                     "-c", f'projects={{"{self.work}"={{trust_level="untrusted"}}}}', "-c", "skills.bundled.enabled=false"]
        result = self.config_read(overrides)
        config = result["config"]
        self.assertEqual(config["chatgpt_base_url"], "https://chatgpt.com/backend-api/")     # packaged default, no origin
        self.assertNotIn("chatgpt_base_url", result["origins"])
        for key in ("model_providers", "profiles", "mcp_servers"):
            self.assertEqual(config[key], {})
        for key in ("profile", "openai_base_url", "auto_review", "permissions", "default_permissions", "hooks"):
            self.assertIsNone(config[key])
        self.assertEqual(result["origins"]["sandbox_workspace_write.writable_roots.0"], {"type": "sessionFlags"})
        self.assertEqual(result["origins"][f"projects.{self.work}.trust_level"], {"type": "sessionFlags"})
        self.assertEqual(result["origins"]["model_provider"]["type"], "user")
        types = [layer["name"]["type"] for layer in result["layers"]]
        self.assertLessEqual({"sessionFlags", "system", "user"}, set(types))
        self.assertLessEqual(set(types), set(review_sources()))
        self.assertNotIn("notify", result["origins"])                                        # empty list: no origin

    def test_an_untrusted_project_layer_is_reported_disabled_and_not_merged(self):
        (self.work / ".git").mkdir()
        (self.work / ".codex").mkdir()
        (self.work / ".codex" / "config.toml").write_text('[mcp_servers.proj]\ncommand = "true"\n')
        trusted = self.config_read(["-c", 'project_root_markers=[".git"]'])
        self.assertIn("proj", trusted["config"]["mcp_servers"])
        untrusted = self.config_read(["-c", 'project_root_markers=[".git"]', "-c",
                                      f'projects={{"{self.work}"={{trust_level="untrusted"}}}}'])
        self.assertEqual(untrusted["config"]["mcp_servers"], {})
        project = next(layer for layer in untrusted["layers"] if layer["name"]["type"] == "project")
        self.assertTrue(project["disabledReason"])

    def test_exec_appends_a_trust_entry_unless_the_project_is_marked_untrusted(self):
        (self.home / "config.toml").write_text("")
        self.run_fake(["exec", "-C", str(self.work), "-"], stdin="p")
        self.assertIn(f'[projects."{self.work}"]', (self.home / "config.toml").read_text())
        (self.home / "config.toml").write_text("")
        self.run_fake(["exec", "-C", str(self.work), "-c", f'projects={{"{self.work}"={{trust_level="untrusted"}}}}', "-"],
                      stdin="p")
        self.assertEqual((self.home / "config.toml").read_text(), "")

    def test_features_does_not_accept_strict_config_and_exec_does(self):
        # the shape the installed Codex has (`codex features --help` lists -c/--enable/--disable only)
        self.assertEqual(self.run_fake(["features", "list", "--strict-config"]).returncode, 0)  # the fake does not police flags
        from controller import review
        self.assertNotIn("--strict-config", review.KIND_FLAGS["features"])
        self.assertIn("--strict-config", review.KIND_FLAGS["exec"])

    def test_exec_writes_a_rollout_the_usage_reader_parses_and_codex_like_state(self):
        from controller import usage
        self.run_fake(["exec", "-C", str(self.work), "-"], {"rate_limits": {"primary": 61.0, "secondary": 9.0}}, "p")
        readings = {r["window"]: r["percent"] for r in usage.read_codex_home(self.home)}
        self.assertEqual(sorted(readings.values()), [9.0, 61.0])
        for name in ("installation_id", "models_cache.json", "thread-writer-locks", "tmp", "skills/.system"):
            self.assertTrue((self.home / name).exists(), name)
        for name in ("history.jsonl", "version.json", "plugins"):
            self.assertFalse((self.home / name).exists(), name)
        self.run_fake(["exec", "-C", str(self.work), "-c", "skills.bundled.enabled=false", "-"],
                      {}, "p")
        self.assertTrue((self.home / "skills" / ".system").exists())    # left by the first run; the second adds nothing

    def test_exec_without_a_credential_fails_like_a_logged_out_codex(self):
        (self.home / "auth.json").unlink()
        result = self.run_fake(["exec", "-C", str(self.work), "-"], stdin="p")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not logged in", result.stderr)

    def test_the_header_is_copied_from_the_prompt_and_the_first_run_can_drop_five_characters(self):
        prompt = "Reviewed bundle ID: " + "a" * 64 + "\nWork item: x\n"
        out = self.work / "v.md"
        self.run_fake(["exec", "-C", str(self.work), "-o", str(out), "-"], {"exec": {"verdict": "wrong_id_first"}}, prompt)
        self.assertIn("Reviewed bundle ID: " + "a" * 59 + "\n", out.read_text())
        self.run_fake(["exec", "-C", str(self.work), "-o", str(out), "-"], {"exec": {"verdict": "wrong_id_first"}},
                      prompt + "did not equal the required values")
        self.assertIn("Reviewed bundle ID: " + "a" * 64 + "\n", out.read_text())

    def test_an_orphaned_fake_ends_itself_and_its_group(self):
        pidfile = self.tmp / "pid"
        binary = self.fake({"exec": {"verdict": "hang"}, "hang_seconds": 120, "orphan_seconds": 1,
                              "ready_file": str(pidfile.with_suffix(".ready"))})
        launcher = (f"import subprocess,sys\np=subprocess.Popen([{str(binary)!r},'exec','-C',{str(self.work)!r},'-'],"
                    f"stdin=subprocess.DEVNULL,env={self.env!r},start_new_session=True)\n"
                    f"open({str(pidfile)!r},'w').write(str(p.pid))\np.wait()\n")
        intermediate = subprocess.Popen([sys.executable, "-c", launcher])
        self.addCleanup(intermediate.wait)
        ready = pidfile.with_suffix(".ready")
        self.assertTrue(process_fixtures.wait_until(ready.exists))     # armed: only then is its parent the original
        pid = int(ready.read_text())
        os.kill(intermediate.pid, signal.SIGKILL)
        intermediate.wait()
        self.addCleanup(process_fixtures.end_pid, pid)
        self.assertTrue(process_fixtures.wait_until(
            lambda: (process_fixtures.read_stat(pid) or ("Z",))[0] == "Z" or process_fixtures.read_stat(pid) is None,
            timeout=20))


if __name__ == "__main__":
    unittest.main()
