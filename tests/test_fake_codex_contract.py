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


class FakeCodexContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fake-codex-", dir=process_fixtures.BUILD_DIR))
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

    def test_the_capture_digest_is_the_one_the_readme_records(self):
        digest = hashlib.sha256((CODEX_FIXTURES / "features_list.0.162.1.txt").read_bytes()).hexdigest()
        self.assertIn(digest, (CODEX_FIXTURES / "README.md").read_text())

    def test_the_pre_step_is_recorded_as_open_and_the_harness_unregistered(self):
        from controller import review
        self.assertIn("pre-step is still open", re.sub(r"\s+", " ", (CODEX_FIXTURES / "README.md").read_text()))
        self.assertFalse(review.PRE_STEP_CAPTURED)
        self.assertEqual(review.HARNESSES, {})

    def test_version_and_the_features_list_follow_the_capture_and_the_disable_flags(self):
        self.assertEqual(self.run_fake(["--version"]).stdout.strip(), fake_codex.VERSION)
        listing = self.run_fake(["features", "list", "--disable", "browser_use", "--disable", "hooks"]).stdout
        state = {p[0]: p[-1] == "true" for p in (line.split() for line in listing.splitlines())}
        self.assertFalse(state["browser_use"])
        self.assertFalse(state["hooks"])
        self.assertTrue(state["computer_use"])
        capture = (CODEX_FIXTURES / "features_list.0.162.1.txt").read_text().splitlines()
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

    def test_a_managed_layer_outranks_the_command_line(self):
        managed = self.tmp / "m.toml"
        managed.write_text('approval_policy = "on-request"\n')
        out = self.run_fake(["debug", "config", "-c", 'approval_policy="never"'], {"managed": str(managed)}).stdout
        entry = json.loads(out)["approval_policy"]
        self.assertEqual(entry, {"value": "on-request", "source": f"managed:{managed}"})

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
        for name in ("installation_id", "history.jsonl", "skills/.system", "plugins/cache"):
            self.assertTrue((self.home / name).exists(), name)

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
        self.addCleanup(process_fixtures.kill_group, pid)
        self.assertTrue(process_fixtures.wait_until(
            lambda: (process_fixtures.read_stat(pid) or ("Z",))[0] == "Z" or process_fixtures.read_stat(pid) is None,
            timeout=20))


if __name__ == "__main__":
    unittest.main()
