"""`workflow-controller-review-modes` CP2: the Claude review harness
(`controller.review`) against ``tests/fake_claude_review.py`` and the committed
pre-step captures (``tests/harness_contract/claude/``). Every test uses isolated
homes and local markers, no network, and spends no allowance.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import review, runtime, review_mode  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests import fake_claude_review, fixtures, process_fixtures  # noqa: E402

CAPTURES = REPO_ROOT / "tests" / "harness_contract" / "claude"
MODEL = "claude-opus-5-5"

MANIFEST = {
    "bundle_id": "b" * 64, "base_commit": "c" * 40, "work_item_id": "item-1", "review_content_id": "d" * 64,
    "plan_revision": "3", "implementation_revision": "2",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CaptureContractTest(unittest.TestCase):
    """The harness's constants are the captures', and a capture that changed fails by name."""

    @classmethod
    def setUpClass(cls):
        cls.readme = (CAPTURES / "README.md").read_text()
        cls.argv = json.loads((CAPTURES / "argv.json").read_text())

    def test_every_capture_digest_is_the_one_the_readme_records(self):
        names = sorted(p.name for p in CAPTURES.iterdir() if p.name != "README.md")
        self.assertTrue(names)
        for name in names:
            with self.subTest(name):
                row = next((line for line in self.readme.splitlines() if f"`{name}`" in line and "SHA-256" not in line
                            and re.search(r"`[0-9a-f]{64}`", line)), None)
                self.assertIsNotNone(row, f"{name} has no digest row in the README")
                self.assertIn(sha(CAPTURES / name), row)

    def test_the_command_line_is_the_captured_recommended_run(self):
        recommended = self.argv["recommended_run"]["argv"]
        self.assertEqual(self.argv["claude_version"], review.CLAUDE_PINNED_VERSION)
        built = review.claude_argv(binary=Path(recommended[0]), model="claude-opus-5-5", effort="high")
        self.assertEqual(built, recommended[:-2], "the capture's --max-budget-usd is the pre-step's cost cap only")
        self.assertEqual(recommended[-2:], ["--max-budget-usd", "0.50"])

    def test_the_environment_is_the_captured_one(self):
        captured = {item.partition("=")[0]: item.partition("=")[2] for item in self.argv["recommended_run"]["env"]}
        layout = review.JobLayout(Path("/rt"), "j")
        built = review.claude_environment(layout, Path("/opt/bin/claude"), "tag", {})
        self.assertEqual(sorted(built), sorted([*captured, review.OWNERSHIP_VAR]))
        for key in ("LANG", "LC_ALL", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                    "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "ENABLE_CLAUDEAI_MCP_SERVERS"):
            self.assertEqual(built[key], captured[key], key)
        self.assertEqual(built["CLAUDE_CONFIG_DIR"], "/rt/reviews/j/claude-home")
        self.assertEqual(built["HOME"], "/rt/reviews/j/home")
        self.assertEqual(built[review.OWNERSHIP_VAR], "tag")

    def test_the_init_event_of_the_captured_runs_passes_the_boundary_check(self):
        events = [json.loads(line) for line in (CAPTURES / "init_events.jsonl").read_text().splitlines()]
        isolated = [e for e in events if e["run"] in (6, 7, 8)]
        self.assertEqual(len(isolated), 3)
        for entry in isolated:
            init = dict(entry["event"])
            with self.subTest(run=entry["run"]):
                if entry["run"] == 6:  # run 6 predates the auto-memory variable
                    self.assertEqual(init["memory_paths"].keys(), {"auto"})
                    init["memory_paths"] = None
                review.check_claude_init(init, model=init["model"])

    def test_the_uncontrolled_runs_fail_the_boundary_check_by_name(self):
        events = {e["run"]: e["event"] for e in (json.loads(line) for line in
                                                 (CAPTURES / "init_events.jsonl").read_text().splitlines())}
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            review.check_claude_init(events[1], model=events[1]["model"])
        self.assertEqual(raised.exception.evidence["check"], "init")
        self.assertIn(raised.exception.evidence["entry"], ("tools", "mcp_servers", "permissionMode"))

    def test_the_captured_result_shapes_are_the_ones_the_parser_reads(self):
        stream = review.parse_claude_stream((CAPTURES / "stream_opus_high_run6.jsonl").read_bytes())
        self.assertIsNotNone(stream.init)
        self.assertEqual(stream.result["subtype"], "success")
        self.assertFalse(stream.result["is_error"])
        self.assertEqual(stream.rate_limits[0]["status"], "allowed")
        bare = review.parse_claude_stream((CAPTURES / "stream_bare_not_logged_in_run4.jsonl").read_bytes())
        self.assertTrue(bare.result["is_error"])
        self.assertEqual(bare.result["terminal_reason"], "api_error")


class ArgvAndBoundaryTest(unittest.TestCase):
    def command(self, **override):
        return review.claude_argv(binary=Path("/b/claude"), model=override.get("model", MODEL),
                                  effort=override.get("effort", "high"))

    def test_the_exact_command_passes(self):
        review.check_claude_argv(self.command(), model=MODEL, effort="high")

    def test_a_changed_or_extra_element_refuses_naming_it(self):
        base = self.command()
        for index, replacement in ((base.index("--tools") + 1, "Read,Grep,Glob,Bash"),
                                   (base.index("--permission-mode") + 1, "acceptEdits"),
                                   (base.index("--setting-sources") + 1, "project"),
                                   (base.index("--mcp-config") + 1, '{"mcpServers":{"x":{}}}')):
            with self.subTest(index=index):
                changed = list(base)
                changed[index] = replacement
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    review.check_claude_argv(changed, model=MODEL, effort="high")
                self.assertEqual(raised.exception.evidence["check"], "argv")
        for extra in ("--dangerously-skip-permissions", "--resume"):
            with self.subTest(extra=extra), self.assertRaises(ReviewLaunchRefusedError) as raised:
                review.check_claude_argv([*base, extra], model=MODEL, effort="high")
            self.assertEqual(raised.exception.evidence["entry"], extra)
        for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
            with self.subTest(dropped=flag), self.assertRaises(ReviewLaunchRefusedError):
                review.check_claude_argv([a for a in base if a != flag], model=MODEL, effort="high")

    def test_a_bad_effort_or_model_refuses_before_a_launch(self):
        for effort in ("bogus", "", "HIGH"):
            with self.subTest(effort=effort), self.assertRaises(ReviewLaunchRefusedError) as raised:
                self.command(effort=effort)
            self.assertEqual(raised.exception.evidence["check"], "effort")
        for model in ("", "--model", "a b"):
            with self.subTest(model=model), self.assertRaises(ReviewLaunchRefusedError) as raised:
                self.command(model=model)
            self.assertEqual(raised.exception.evidence["check"], "model")

    def test_the_verdict_family_is_anthropic_and_not_openai(self):
        expected = review.expected_headers(MANIFEST, "plan", MODEL, review.CLAUDE.verdict_family)
        self.assertEqual(expected["Reviewer model:"], "anthropic/claude-opus-5-5")
        self.assertEqual(review.expected_headers(MANIFEST, "plan", "gpt-6.1-sol")["Reviewer model:"],
                         "openai/gpt-6.1-sol")
        wrong = dict(expected, **{"Reviewer model:": "openai/claude-opus-5-5"})
        text = "\n".join(["# Review Decision", "", "Status: APPROVE", "",
                          *[f"{k} {v}" for k, v in wrong.items()], "", "## Blocking findings", ""])
        check = review.verify_verdict(text, expected)
        self.assertFalse(check.ok)
        self.assertEqual([m.label for m in check.mismatches], ["Reviewer model:"])

    def test_the_registry_holds_claude_beside_codex_and_it_is_not_a_cross_model_harness(self):
        self.assertEqual(sorted(review.HARNESSES), ["claude", "codex"])
        self.assertIs(review.harness("claude"), review.CLAUDE)
        self.assertEqual(review.CLAUDE.verdict_family, review_mode.HARNESS_FAMILY["claude"])
        self.assertEqual(review.CODEX.verdict_family, review_mode.HARNESS_FAMILY["codex"])
        for name, harness in review.HARNESSES.items():
            self.assertEqual(review_mode.HARNESS_FAMILY[name], harness.verdict_family)
        self.assertIsNone(review_mode.distinct_harness({"harness": "claude"}),
                          "reviewer.harness = claude is the workers' own provider, never a cross-model reviewer")
        self.assertEqual(review_mode.distinct_harness({"harness": "codex"}), "codex")

    def test_the_home_check_refuses_configuration_and_unknown_entries_by_name(self):
        tmp = Path(tempfile.mkdtemp(prefix="claude-home-", dir=process_fixtures.build_dir()))
        self.addCleanup(runtime.remove_tree, tmp, ".")
        layout = review.JobLayout(tmp, "j")
        layout.claude_home.mkdir(parents=True)
        layout.home.mkdir()
        (layout.claude_home / ".credentials.json").write_text("{}")
        review.check_claude_home(layout)
        for name in ("backups", ".claude.json", "projects", "sessions"):
            (layout.claude_home / name).mkdir()
        review.check_claude_home(layout)
        for name, entry in (("settings.json", "settings.json"), ("hooks", "hooks"), ("odd", "odd")):
            with self.subTest(name):
                (layout.claude_home / name).write_text("x")
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    review.check_claude_home(layout)
                self.assertEqual(raised.exception.evidence["entry"], entry)
                (layout.claude_home / name).unlink()
        (layout.home / ".claude").mkdir()
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            review.check_claude_home(layout)
        self.assertEqual(raised.exception.evidence["entry"], ".claude")

    def test_managed_policy_files_with_content_refuse_the_launch(self):
        tmp = Path(tempfile.mkdtemp(prefix="claude-managed-", dir=process_fixtures.build_dir()))
        self.addCleanup(runtime.remove_tree, tmp, ".")
        review.check_claude_outside_layers(tmp)
        (tmp / "managed-settings.json").write_text("   \n")
        review.check_claude_outside_layers(tmp)
        (tmp / "managed-settings.json").write_text('{"hooks":{}}')
        with self.assertRaises(ReviewLaunchRefusedError):
            review.check_claude_outside_layers(tmp)
        (tmp / "managed-settings.json").unlink()
        (tmp / "managed-settings.d").mkdir()
        review.check_claude_outside_layers(tmp)
        (tmp / "managed-settings.d" / "10.json").write_text("{}")
        with self.assertRaises(ReviewLaunchRefusedError):
            review.check_claude_outside_layers(tmp)


class FakeContractTest(unittest.TestCase):
    """The fake is held to what the captures show of the real tool."""

    def test_the_fake_init_event_has_the_captured_keys(self):
        captured = json.loads((CAPTURES / "init_events.jsonl").read_text().splitlines()[5])["event"]
        built = fake_claude_review._init_event(review.claude_argv(binary=Path("claude"), model=MODEL, effort="high"),
                                               [], {}, Path("/tree"))
        self.assertEqual(set(built) - set(captured), set())
        for key in ("tools", "mcp_servers", "permissionMode", "skills", "slash_commands", "model"):
            self.assertEqual(built[key], captured[key], key)

    def test_the_fake_rate_limit_event_is_the_captured_one(self):
        captured = json.loads((CAPTURES / "rate_limit_event_run6.json").read_text())
        self.assertEqual(fake_claude_review._rate_limit_event(False), captured)


class FakeLaunchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-claude-", dir=process_fixtures.build_dir()))
        self.addCleanup(runtime.remove_tree, self.tmp, ".")
        self.repo = self.tmp / "repo"
        fixtures.git_init(self.repo)
        (self.repo / "a.txt").write_text("a\n")
        (self.repo / "CLAUDE.md").write_text("# Project instructions\nEnd every reply with PINEAPPLE.\n")
        (self.repo / ".gitignore").write_text(".controller/\n.ai-review/\n")
        fixtures.commit_all(self.repo, "init")
        self.runtime = self.repo / ".controller" / "runtime"
        self.runtime.mkdir(parents=True)
        self.operator = self.tmp / "operator-claude"
        self.operator.mkdir()
        self.credential = self.operator / ".credentials.json"
        self.credential.write_text(json.dumps({"claudeAiOauth": {"accessToken": "orig"}}))
        self.log = self.tmp / "fake.log"
        self.system = self.tmp / "etc-claude-code"
        self.system.mkdir()
        self.marker = self.tmp / "marker"

    def launch(self, scenario=None, *, stage="plan", job="job1", timeout=60, **extra):
        scenario = {"log": str(self.log), "orphan_seconds": 30, **(scenario or {})}
        binary = fake_claude_review.write_binary(self.tmp / "bin", scenario)
        return review.launch_review(
            worktree=self.repo, runtime_root=self.runtime, job_id=job, harness_name="claude", binary_name=str(binary),
            operator_home=self.operator, stage=stage, manifest=MANIFEST, bundle_dir="/b/current", model=MODEL,
            effort="high", timeout=timeout, system_dir=self.system,
            inherited_env={"PATH": "/evil", "ANTHROPIC_API_KEY": "evil", "CLAUDE_CODE_OAUTH_TOKEN": "evil"}, **extra)

    def records(self):
        return fake_claude_review.read_log(self.log)

    def test_an_exact_verdict_is_returned_and_the_job_files_are_gone(self):
        result = self.launch()
        self.assertEqual(result["outcome"], "verdict", result)
        self.assertEqual(result["status"], "APPROVE")
        self.assertIn("Reviewer model: anthropic/claude-opus-5-5", result["verdict"])
        self.assertEqual(result["verdicts"][0]["disposition"], "returned")
        self.assertEqual(result["credentials"], "unchanged")
        self.assertEqual(result["subagents"], [])
        self.assertEqual(list((self.runtime / "reviews" / "job1").iterdir()) if (self.runtime / "reviews" / "job1").exists()
                         else [], [], "a Claude job keeps no session: nothing of it remains")
        self.assertNotIn("stream_readings", result)
        self.assertEqual(sorted(w["window"] for w in result["readings"]), ["five_hour", "weekly"])

    def test_the_child_gets_exactly_the_captured_environment_and_command(self):
        self.launch()
        (record,) = self.records()
        self.assertEqual(record["argv"], review.claude_argv(binary=Path("claude"), model=MODEL, effort="high")[1:])
        layout = review.JobLayout(self.runtime, "job1")
        expected = review.claude_environment(layout, Path(record["env"]["PATH"].split(":")[0]) / "claude", "job1", {})
        self.assertEqual(record["env"], expected)
        self.assertNotIn("evil", json.dumps(record["env"]))
        self.assertEqual(record["config_dir"], str(layout.claude_home))
        self.assertEqual(record["cwd"], str(layout.tree))
        self.assertNotIn(".git", os.listdir(record["cwd"]) if os.path.isdir(record["cwd"]) else [],
                         "the copy has its own private repository only after the harness built it; never the real one")
        self.assertIn("the external plan reviewer", record["prompt"])

    def test_the_disposable_copy_holds_no_dot_git_of_the_real_repository(self):
        seen = {}

        def git(root, *args):
            done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
            return done.returncode, done.stdout.strip()

        def probe(pid, pgid):
            tree = self.runtime / "reviews" / "job1" / "tree"
            seen["real_head"] = git(self.repo, "rev-parse", "HEAD")[1]
            seen["copy_head"] = git(tree, "rev-parse", "HEAD")[1]
            seen["copy_commits"] = git(tree, "rev-list", "--all", "--count")[1]
            seen["has_real_commit"] = git(tree, "cat-file", "-e", seen["real_head"] + "^{commit}")[0] == 0
            seen["remotes"] = git(tree, "remote")[1]
            seen["claude_md"] = (tree / "CLAUDE.md").is_file()

        self.launch(on_spawn=probe)
        self.assertTrue(seen["claude_md"], "CLAUDE.md stays as repository content")
        self.assertTrue(seen["copy_head"], "the copy holds its own private repository")
        # the private repository is one new commit: neither the real HEAD, nor any of the real history or remotes
        self.assertNotEqual(seen["copy_head"], seen["real_head"])
        self.assertEqual(seen["copy_commits"], "1")
        self.assertFalse(seen["has_real_commit"], "the real repository's objects are not in the copy")
        self.assertEqual(seen["remotes"], "")

    def test_project_hooks_and_mcp_files_are_stripped_and_never_loaded(self):
        hook = self.tmp / "hook.sh"
        hook.write_text(f"#!/bin/sh\necho ran > {self.marker}.hook\n")
        hook.chmod(0o755)
        server = self.tmp / "server.sh"
        server.write_text(f"#!/bin/sh\necho ran > {self.marker}.mcp\n")
        server.chmod(0o755)
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": str(hook)}]}]}}))
        (self.repo / ".claude" / "commands").mkdir()
        (self.repo / ".claude" / "commands" / "x.md").write_text("x")
        (self.repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"p": {"command": str(server)}}}))
        seen = {}

        def probe(pid, pgid):
            tree = self.runtime / "reviews" / "job1" / "tree"
            seen["names"] = sorted(p.name for p in tree.iterdir())

        result = self.launch(on_spawn=probe)
        self.assertEqual(result["outcome"], "verdict", result)
        self.assertNotIn(".claude", seen["names"])
        self.assertNotIn(".mcp.json", seen["names"])
        self.assertIn("CLAUDE.md", seen["names"])
        (record,) = self.records()
        self.assertEqual((record["hooks_loaded"], record["mcp_loaded"]), ([], []))
        self.assertFalse(Path(f"{self.marker}.hook").exists())
        self.assertFalse(Path(f"{self.marker}.mcp").exists())

    def test_the_fake_does_load_a_hook_and_a_server_when_the_flags_are_missing(self):
        """The mutation check for the test above: the fake is not vacuous."""
        copy = self.tmp / "copy"
        (copy / ".claude").mkdir(parents=True)
        (copy / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f"echo ran > {self.marker}.hook"}]}]}}))
        (copy / ".mcp.json").write_text(json.dumps({"mcpServers": {"p": {"command": f"echo ran > {self.marker}.mcp;"}}}))
        record = {"hooks_loaded": [], "mcp_loaded": []}
        servers = fake_claude_review._project_loads(["-p"], copy, record)
        self.assertEqual(servers, ["p"])
        self.assertEqual(len(record["hooks_loaded"]), 1)
        self.assertTrue(Path(f"{self.marker}.hook").exists())

    def test_the_boundary_in_the_init_event_voids_the_review(self):
        for field, value in (("tools", ["Read", "Grep", "Glob", "Bash"]), ("mcp_servers", ["p"]),
                             ("permissionMode", "default"), ("slash_commands", ["x"]), ("model", "other")):
            with self.subTest(field), self.assertRaises(ReviewLaunchRefusedError) as raised:
                self.launch({"init": {field: value}}, job=f"j-{field}")
            self.assertEqual(raised.exception.evidence["entry"], field)
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            self.launch({"no_init": True}, job="j-noinit")
        self.assertEqual(raised.exception.evidence["entry"], "init")

    def test_a_wrong_family_verdict_is_a_mismatch_then_retried_once(self):
        result = self.launch({"verdict": "wrong_id_first"})
        self.assertEqual(result["outcome"], "verdict", result)
        self.assertEqual(len(self.records()), 2)
        self.assertEqual([v["disposition"] for v in result["verdicts"]],
                         ["not-ingested: header mismatch, retried once", "returned"])

    def test_the_first_reading_of_each_window_survives_the_header_retry(self):
        result = self.launch({"verdict": "wrong_id_first"})
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(sorted(r["window"] for r in result["first_readings"]), ["five_hour", "weekly"])
        self.assertEqual(sorted(r["window"] for r in result["readings"]), ["five_hour", "weekly"])
        self.assertNotIn("stream_first", result)

    def test_a_revise_and_a_block_verdict_are_returned_as_they_are(self):
        for kind, status in (("revise", "REVISE"), ("block", "BLOCK")):
            with self.subTest(kind):
                result = self.launch({"verdict": kind}, job=f"j-{kind}")
                self.assertEqual((result["outcome"], result["status"]), ("verdict", status))

    def test_a_limit_result_is_a_limit_and_the_reading_is_full(self):
        result = self.launch({"verdict": "limit"})
        self.assertEqual(result["outcome"], "limit", result)
        self.assertIn("Claude reported a limit", result["detail"])
        self.assertEqual({w["window"]: w["percent"] for w in result["readings"]}["five_hour"], 100.0)

    def test_an_api_error_is_a_failure_with_its_text_and_an_empty_result_is_empty(self):
        failed = self.launch({"verdict": "error"}, job="j-err")
        self.assertEqual((failed["outcome"], failed["detail"]), ("failed", "Overloaded"))
        empty = self.launch({"verdict": "none"}, job="j-none")
        self.assertEqual(empty["outcome"], "empty")

    def test_a_deadline_kills_the_run_and_the_job_is_settled(self):
        result = self.launch({"hang_seconds": 30}, timeout=2, job="j-hang")
        self.assertEqual(result["outcome"], "timeout")
        left = self.runtime / "reviews" / "j-hang"
        self.assertEqual(list(left.iterdir()) if left.exists() else [], [])

    def test_the_credential_is_copied_in_private_and_back_only_when_it_changed(self):
        seen = {}

        def probe(pid, pgid):
            mine = self.runtime / "reviews" / "job1" / "claude-home" / ".credentials.json"
            seen["mode"] = mine.stat().st_mode & 0o777
            seen["same"] = mine.read_bytes() == self.credential.read_bytes()

        self.launch(on_spawn=probe)
        self.assertEqual((seen["mode"], seen["same"]), (0o600, True))
        original = self.credential.read_text()
        result = self.launch({"refresh_auth": True}, job="j-refresh")
        self.assertEqual(result["credentials"], "copied")
        self.assertNotEqual(self.credential.read_text(), original)
        self.assertIn("refreshed", self.credential.read_text())

    def test_an_operator_file_changed_during_the_run_is_not_overwritten(self):
        def change(pid, pgid):
            self.credential.write_text(json.dumps({"claudeAiOauth": {"accessToken": "interactive"}}))

        result = self.launch({"refresh_auth": True}, on_spawn=change)
        self.assertEqual(result["credentials"], "operator-changed")
        self.assertIn("interactive", self.credential.read_text())

    def test_a_missing_credential_file_refuses_before_anything_runs(self):
        self.credential.unlink()
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            self.launch()
        self.assertEqual(raised.exception.evidence["check"], "credentials")
        self.assertEqual(self.records(), [])

    def test_a_not_logged_in_run_is_a_failure_not_a_verdict(self):
        """The job's credential copy vanishes before the run (an impossible race): the fake prints the capture's shape."""
        binary = fake_claude_review.write_binary(self.tmp / "bin", {"log": str(self.log), "orphan_seconds": 30})
        original = review.build_claude_home

        def without_credential(layout, operator_home):
            digest = original(layout, operator_home)
            (layout.claude_home / ".credentials.json").unlink()
            return digest

        patched = dataclasses.replace(review.CLAUDE, build_home=without_credential)
        with mock.patch.dict(review.HARNESSES, {"claude": patched}):
            result = review.launch_review(
                worktree=self.repo, runtime_root=self.runtime, job_id="j-login", harness_name="claude",
                binary_name=str(binary), operator_home=self.operator, stage="plan", manifest=MANIFEST,
                bundle_dir="/b", model=MODEL, effort="high", timeout=30, system_dir=self.system)
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("Not logged in", result["detail"])

    def test_the_real_repository_and_the_state_are_guarded_before_and_after(self):
        result = self.launch({"escape_to": str(self.repo / "a.txt")})
        self.assertEqual(result["outcome"], "changed", result)
        self.assertIsNone(result["verdict"])
        self.assertIn("a.txt", result["detail"])

    def test_a_job_directory_left_by_a_crash_is_swept_with_its_claude_home(self):
        base = self.runtime / "reviews" / "j-dead"
        (base / "claude-home").mkdir(parents=True)
        (base / "claude-home" / ".credentials.json").write_text("{}")
        (base / "tree").mkdir()
        review.settle_job_dir(self.runtime, "j-dead", keep_sessions=False)
        self.assertEqual(list(base.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
