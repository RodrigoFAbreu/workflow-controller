"""`workflow-controller-codex-reviewer` CP2: the review harness (`controller.review`)
against ``tests/fake_codex.py``. Every test uses isolated configuration homes,
local attempt markers and no network; no test spends Codex allowance.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import review, runtime  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests import fake_codex, fixtures, process_fixtures  # noqa: E402

FEATURES_CAPTURE = REPO_ROOT / "tests" / "harness_contract" / "codex" / "features_list.0.162.1.txt"

MANIFEST = {
    "bundle_id": "b" * 64, "base_commit": "c" * 40, "work_item_id": "item-1", "review_content_id": "d" * 64,
    "plan_revision": "3", "implementation_revision": "2",
}
PLAN_EXPECTED = review.expected_headers(MANIFEST, "plan", "gpt-6.1-sol")


def verdict_text(expected: dict, status: str = "APPROVE", *, drop: str | None = None, extra: list[str] = ()) -> str:
    lines = ["# Review Decision", "", f"Status: {status}", ""]
    lines += [f"{label} {value}" for label, value in expected.items() if label != drop]
    return "\n".join([*lines, *extra, "", "## Blocking findings", "", "None.", ""])


class VerifyVerdictTest(unittest.TestCase):
    def test_exact_header_passes(self):
        check = review.verify_verdict(verdict_text(PLAN_EXPECTED), PLAN_EXPECTED)
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "APPROVE")

    def test_every_label_has_a_row_and_each_mismatch_is_caught(self):
        self.assertEqual(
            list(PLAN_EXPECTED),
            ["Reviewed bundle ID:", "Reviewed base commit:", "Work item:", "Reviewed review content ID:",
             "Reviewed review_content_id:", "Plan revision reviewed:", "Reviewer role:", "Reviewer model:"])
        for label, value in PLAN_EXPECTED.items():
            with self.subTest(label=label, case="wrong"):
                changed = dict(PLAN_EXPECTED)
                changed[label] = value[:-1] + ("0" if value[-1] != "0" else "1")
                check = review.verify_verdict(verdict_text(changed), PLAN_EXPECTED)
                self.assertFalse(check.ok)
                self.assertEqual([m.label for m in check.mismatches], [label])
            with self.subTest(label=label, case="missing"):
                check = review.verify_verdict(verdict_text(PLAN_EXPECTED, drop=label), PLAN_EXPECTED)
                self.assertEqual([m.label for m in check.mismatches], [label])
                self.assertEqual(check.mismatches[0].found, ())

    def test_five_dropped_characters_of_an_id_is_a_mismatch(self):
        short = dict(PLAN_EXPECTED, **{"Reviewed bundle ID:": PLAN_EXPECTED["Reviewed bundle ID:"][:-5]})
        self.assertFalse(review.verify_verdict(verdict_text(short), PLAN_EXPECTED).ok)

    def test_a_repeated_or_prefixed_line_is_a_mismatch(self):
        twice = verdict_text(PLAN_EXPECTED, extra=["Work item: item-1"])
        self.assertEqual([m.label for m in review.verify_verdict(twice, PLAN_EXPECTED).mismatches], ["Work item:"])
        padded = dict(PLAN_EXPECTED, **{"Plan revision reviewed:": "3 (final)"})
        self.assertFalse(review.verify_verdict(verdict_text(padded), PLAN_EXPECTED).ok)

    def test_stale_revision_missing_revision_and_bad_status(self):
        stale = dict(PLAN_EXPECTED, **{"Plan revision reviewed:": "2"})
        self.assertFalse(review.verify_verdict(verdict_text(stale), PLAN_EXPECTED).ok)
        self.assertFalse(review.verify_verdict(verdict_text(PLAN_EXPECTED, drop="Plan revision reviewed:"),
                                               PLAN_EXPECTED).ok)
        for status in ("MAYBE", ""):
            check = review.verify_verdict(verdict_text(PLAN_EXPECTED, status), PLAN_EXPECTED)
            self.assertFalse(check.ok)
            self.assertIsNone(check.status)

    def test_a_label_below_the_first_heading_does_not_count(self):
        text = "# Review Decision\n\nStatus: APPROVE\n\n## Notes\n" + "\n".join(
            f"{label} {value}" for label, value in PLAN_EXPECTED.items())
        self.assertFalse(review.verify_verdict(text, PLAN_EXPECTED).ok)

    def test_the_implementation_stage_uses_its_own_revision_label_and_role(self):
        expected = review.expected_headers(MANIFEST, "implementation", "m")
        self.assertEqual(expected["Implementation revision reviewed:"], "2")
        self.assertNotIn("Plan revision reviewed:", expected)
        self.assertEqual(expected["Reviewer role:"], "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(review.expected_headers(MANIFEST, "plan", "m")["Reviewer role:"],
                         "MANUAL_EXTERNAL_PLAN_REVIEW")

    def test_a_missing_manifest_value_refuses(self):
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            review.expected_headers({k: v for k, v in MANIFEST.items() if k != "plan_revision"}, "plan", "m")
        self.assertEqual(raised.exception.evidence["entry"], "plan_revision")

    def test_manifest_parsing_stops_at_the_first_heading(self):
        text = "# Bundle Manifest\n\nstage: plan\nbundle_id: abc\n\n## Protected paths\nbundle_id: wrong\n"
        self.assertEqual(review.parse_manifest(text), {"stage": "plan", "bundle_id": "abc"})

    def test_the_prompt_carries_the_exact_values(self):
        prompt = review.build_prompt("plan", PLAN_EXPECTED, bundle_dir="/b/current")
        for label, value in PLAN_EXPECTED.items():
            self.assertIn(f"{label} {value}", prompt)


class RegistryAndContractTest(unittest.TestCase):
    def test_the_harness_is_unregistered_until_the_pre_step_is_recorded(self):
        self.assertFalse(review.PRE_STEP_CAPTURED)
        self.assertEqual(review.HARNESSES, {})
        with self.assertRaises(ReviewLaunchRefusedError):
            review.harness("codex")

    def test_the_capture_is_in_the_repository(self):
        self.assertTrue(FEATURES_CAPTURE.is_file())

    def test_every_enabled_feature_of_the_capture_is_allowlisted_or_disabled(self):
        enabled = {n for n, on in review.parse_features(FEATURES_CAPTURE.read_text()).items() if on}
        self.assertTrue(enabled)
        self.assertEqual(sorted(enabled - review.TOOL_ALLOWLIST - set(review.DISABLED_FEATURES)), [])
        self.assertEqual(review.TOOL_ALLOWLIST & set(review.DISABLED_FEATURES), set())
        self.assertEqual(sorted(set(review.DISABLED_FEATURES) - enabled), [])

    @staticmethod
    def with_overrides_applied() -> str:
        """The capture as `features list` prints it once every `--disable` took."""
        return "\n".join(
            line.rsplit(None, 1)[0] + " false" if line.split()[0] in review.DISABLED_FEATURES else line
            for line in FEATURES_CAPTURE.read_text().splitlines()) + "\n"

    def test_the_capture_passes_the_drift_check_once_the_overrides_took(self):
        review.check_features(self.with_overrides_applied())

    def test_a_feature_enabled_in_a_capture_but_unlisted_is_found(self):
        for name in ("browser_use", "computer_use", "plugins", "image_generation", "multi_agent"):
            self.assertIn(name, review.DISABLED_FEATURES)
        listing = self.with_overrides_applied() + "brand_new_tool stable true\n"
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            review.check_features(listing)
        self.assertEqual(raised.exception.evidence["entry"], "brand_new_tool")

    def test_a_disabled_feature_reported_enabled_refuses(self):
        # the override was ignored: the preflight sees the capture as it is
        with self.assertRaises(ReviewLaunchRefusedError):
            review.check_features(FEATURES_CAPTURE.read_text())

    def test_the_command_line_carries_the_pinned_set(self):
        binary = Path("/x/codex")
        exec_argv = review.argv("exec", binary=binary, tree=Path("/t"), verdict=Path("/v.md"), tmpdir=Path("/tmpd"))
        review.check_argv("exec", exec_argv)
        for expected in ('model="gpt-6.1-sol"', 'model_reasoning_effort="high"', 'approval_policy="never"',
                         "sandbox_workspace_write.network_access=false", "notify=[]", 'web_search="disabled"',
                         'model_provider="openai"', 'project_root_markers=[".git"]',
                         'sandbox_workspace_write.writable_roots=["/tmpd"]',
                         "sandbox_workspace_write.exclude_slash_tmp=true"):
            self.assertIn(expected, exec_argv)
        self.assertIn("--strict-config", exec_argv)
        self.assertIn("--ignore-rules", exec_argv)
        self.assertEqual(exec_argv[exec_argv.index("-s") + 1], "workspace-write")
        self.assertNotIn("--approve-for-me", exec_argv)
        self.assertNotIn("--ephemeral", exec_argv)
        for kind in ("features", "mcp"):
            other = review.argv(kind, binary=binary)
            review.check_argv(kind, other)
            self.assertNotIn("--strict-config", other)
            self.assertNotIn("-s", other)

    def test_a_launch_missing_any_pinned_item_is_refused(self):
        base = review.argv("exec", binary=Path("/x/codex"), tree=Path("/t"), verdict=Path("/v.md"), tmpdir=Path("/m"))
        for feature in review.DISABLED_FEATURES:
            command = list(base)
            at = command.index(feature) - 1
            del command[at:at + 2]
            with self.subTest(feature=feature), self.assertRaises(ReviewLaunchRefusedError):
                review.check_argv("exec", command)
        for item in review.required_overrides("exec"):
            command = list(base)
            at = command.index(item) - 1
            del command[at:at + 2]
            with self.subTest(override=item), self.assertRaises(ReviewLaunchRefusedError):
                review.check_argv("exec", command)
        for extra in (["-p", "ops"], ["--approve-for-me"], ["--add-dir", "/"], ["--profile", "x"]):
            with self.subTest(extra=extra), self.assertRaises(ReviewLaunchRefusedError):
                review.check_argv("exec", [*base, *extra])
        with self.assertRaises(ReviewLaunchRefusedError):
            review.check_argv("features", [*review.argv("features", binary=Path("/x")), "--strict-config"])
        without_sandbox = list(base)
        without_sandbox[without_sandbox.index("-s") + 1] = "danger-full-access"
        with self.assertRaises(ReviewLaunchRefusedError):
            review.check_argv("exec", without_sandbox)


class CopyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-copy-", dir=REPO_ROOT / "build"))
        self.addCleanup(runtime.remove_tree, self.tmp, ".")
        self.repo = self.tmp / "repo"
        fixtures.git_init(self.repo)
        (self.repo / "tracked.txt").write_text("v1\n")
        (self.repo / "staged.txt").write_text("s1\n")
        fixtures.commit_all(self.repo, "init")
        (self.repo / "tracked.txt").write_text("v1 edited\n")           # unstaged
        (self.repo / "staged.txt").write_text("s2\n")
        fixtures.run(["git", "add", "staged.txt"], cwd=self.repo)         # staged
        (self.repo / "new.txt").write_text("untracked\n")                 # untracked
        (self.repo / ".codex").mkdir()
        (self.repo / ".codex" / "config.toml").write_text('[mcp_servers.x]\ncommand="true"\n')
        (self.repo / ".agents" / "skills").mkdir(parents=True)
        (self.repo / ".agents" / "skills" / "s.md").write_text("skill")
        (self.repo / ".agents" / "keep.md").write_text("keep")
        (self.repo / "AGENTS.override.md").write_text("override")
        (self.repo / "AGENTS.md").write_text("agents")
        (self.repo / "__pycache__").mkdir()
        (self.repo / "__pycache__" / "x.pyc").write_text("c")
        (self.repo / "pkg.egg-info").mkdir()
        (self.repo / "pkg.egg-info" / "PKG-INFO").write_text("i")
        self.runtime = self.repo / ".controller" / "runtime"
        self.runtime.mkdir(parents=True)
        (self.runtime / "state.json").write_text("{}")

    def test_the_copy_holds_the_worktree_bytes_and_a_private_clean_repository(self):
        tree = review.build_copy(self.repo, self.runtime, "job1")
        self.assertEqual((tree / "tracked.txt").read_text(), "v1 edited\n")
        self.assertEqual((tree / "staged.txt").read_text(), "s2\n")
        self.assertEqual((tree / "new.txt").read_text(), "untracked\n")
        self.assertEqual(fixtures.run(["git", "-C", str(tree), "status", "--porcelain"]).stdout, "")
        self.assertEqual(fixtures.run(["git", "-C", str(tree), "diff"]).stdout, "")
        self.assertEqual(fixtures.run(["git", "-C", str(tree), "rev-list", "--count", "HEAD"]).stdout.strip(), "1")
        self.assertTrue((tree / ".git").is_dir())

    def test_exclusions_and_project_local_discovery(self):
        tree = review.build_copy(self.repo, self.runtime, "job1")
        for gone in (".codex", ".agents/skills", "AGENTS.override.md", "__pycache__", "pkg.egg-info", ".controller"):
            self.assertFalse((tree / gone).exists(), gone)
        self.assertTrue((tree / "AGENTS.md").is_file())
        self.assertTrue((tree / ".agents" / "keep.md").is_file())

    def test_a_runtime_directory_inside_the_worktree_does_not_copy_itself(self):
        custom = self.repo / "rt"
        custom.mkdir()
        (custom / "marker").write_text("m")
        tree = review.build_copy(self.repo, custom, "job1")
        self.assertFalse((tree / "rt").exists())

    def test_a_linked_worktree_gets_no_gitlink_and_cannot_reach_the_real_repository(self):
        linked = self.tmp / "linked"
        fixtures.run(["git", "worktree", "add", "-q", "-b", "side", str(linked)], cwd=self.repo)
        self.assertTrue((linked / ".git").is_file())
        rt = linked / ".controller" / "runtime"
        rt.mkdir(parents=True)
        tree = review.build_copy(linked, rt, "jobL")
        self.assertTrue((tree / ".git").is_dir())
        self.assertFalse((tree / ".git").is_symlink())
        before = review.snapshot_repository(linked)
        git_env = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@y", "GIT_COMMITTER_NAME": "x",
                   "GIT_COMMITTER_EMAIL": "x@y", "GIT_CEILING_DIRECTORIES": str(tree.parent)}
        fixtures.run(["git", "-C", str(tree), "update-ref", "refs/heads/evil", "HEAD"], env=git_env)
        fixtures.run(["git", "-C", str(tree), "config", "evil.key", "1"], env=git_env)
        self.assertEqual(review.compare_snapshots(before, review.snapshot_repository(linked)), [])
        self.assertEqual(fixtures.run(["git", "-C", str(self.repo), "for-each-ref", "refs/heads/evil"]).stdout, "")

    def test_settle_removes_everything_but_the_sessions(self):
        layout = review.JobLayout(self.runtime, "job1")
        review.build_copy(self.repo, self.runtime, "job1")
        (layout.codex_home / "sessions" / "2026").mkdir(parents=True)
        (layout.codex_home / "sessions" / "2026" / "rollout-x.jsonl").write_text("{}")
        (layout.codex_home / "auth.json").write_text("{}")
        (layout.codex_home / "history.jsonl").write_text("{}")
        (layout.base / "verdict.md").write_text("v")
        review.settle_job_dir(self.runtime, "job1")
        self.assertEqual(sorted(p.name for p in layout.base.iterdir()), ["codex-home"])
        self.assertEqual(sorted(p.name for p in layout.codex_home.iterdir()), ["sessions"])
        review.settle_job_dir(self.runtime, "job1")  # idempotent
        review.settle_job_dir(self.runtime, "never-existed")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-guard-", dir=REPO_ROOT / "build"))
        self.addCleanup(runtime.remove_tree, self.tmp, ".")
        self.repo = self.tmp / "repo"
        fixtures.git_init(self.repo)
        (self.repo / "a.txt").write_text("a\n")
        (self.repo / ".gitignore").write_text("ignored/\n")
        fixtures.commit_all(self.repo, "init")
        (self.repo / "a.txt").write_text("a2\n")                 # already modified
        self.feedback = self.repo / ".ai-review" / "feedback"
        self.feedback.mkdir(parents=True)

    def snap(self, identity="s1"):
        return review.snapshot_repository(self.repo, feedback_dir=self.feedback, state_identity=identity)

    def test_an_unchanged_repository_compares_equal_and_the_snapshot_writes_nothing(self):
        index = (self.repo / ".git" / "index").read_bytes()
        before = self.snap()
        self.assertEqual(review.compare_snapshots(before, self.snap()), [])
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), index)

    def test_each_kind_of_change_is_seen(self):
        before = self.snap()
        cases = {
            "head": lambda: fixtures.commit_all(self.repo, "second"),
            "refs": lambda: fixtures.run(["git", "-C", str(self.repo), "branch", "extra"]),
            "config": lambda: fixtures.run(["git", "-C", str(self.repo), "config", "x.y", "1"]),
            "feedback": lambda: (self.feedback / "REVIEW_FEEDBACK.md").write_text("forged"),
            "file:a.txt": lambda: (self.repo / "a.txt").write_text("a3\n"),   # second edit, status unchanged
            "file:new.txt": lambda: (self.repo / "new.txt").write_text("n"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                fresh = self.snap()
                change()
                self.assertIn(name, review.compare_snapshots(fresh, self.snap()))
        self.assertIn("state_identity", review.compare_snapshots(before, self.snap("s2")))

    def test_only_the_excluded_directory_and_its_collapsed_parent_leave_the_status(self):
        exclude = ["run/review-x"]
        before = review.snapshot_repository(self.repo, exclude=exclude)
        (self.repo / "run" / "review-x").mkdir(parents=True)
        (self.repo / "run" / "review-x" / "copy").write_text("c")   # untracked `run/` collapses to its parent
        self.assertEqual(review.compare_snapshots(before, review.snapshot_repository(self.repo, exclude=exclude)), [])
        (self.repo / "run" / "review-x" / "copy").unlink()
        (self.repo / "run" / "review-x").rmdir()
        (self.repo / "run").rmdir()
        (self.repo / "r").write_text("r")                            # a name that is only a string prefix of it
        self.assertIn("status", review.compare_snapshots(before, review.snapshot_repository(self.repo, exclude=exclude)))

    def test_an_ignored_file_is_not_part_of_the_manifest(self):
        before = self.snap()
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored" / "x").write_text("x")
        self.assertEqual(review.compare_snapshots(before, self.snap()), [])


class EnvironmentAndHomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-env-", dir=REPO_ROOT / "build"))
        self.addCleanup(runtime.remove_tree, self.tmp, ".")
        self.layout = review.JobLayout(self.tmp, "job1")
        self.operator = self.tmp / "operator-home"
        self.operator.mkdir()
        (self.operator / "auth.json").write_text(json.dumps({"tokens": {"refresh_token": "orig"}}))
        self.binary = fake_codex.write_binary(self.tmp / "bin", {})

    def test_the_environment_is_exactly_the_allowlist(self):
        hostile = {name: "evil" for name in (
            "OPENAI_BASE_URL", "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_EXEC_SERVER_URL",
            "CODEX_PERMISSION_PROFILE", "CODEX_SQLITE_HOME", "HTTPS_PROXY", "SSL_CERT_FILE", "BASH_ENV", "LD_PRELOAD",
            "PYTHONPATH", "NODE_OPTIONS", "GIT_DIR", "GIT_CONFIG_GLOBAL", "ZDOTDIR", "GH_TOKEN")}
        env = review.child_environment(self.layout, self.binary, "job1", {**hostile, "PATH": "/evil"})
        self.assertEqual(sorted(env), sorted(review.ENV_ALLOWLIST))
        self.assertEqual(env[review.OWNERSHIP_VAR], "job1")
        self.assertEqual(env["CODEX_HOME"], str(self.layout.codex_home))
        self.assertTrue(env["PATH"].startswith(str(self.binary.parent) + ":"))
        self.assertTrue(env["PATH"].endswith(review.SYSTEM_PATH))
        self.assertNotIn("/evil", env["PATH"])

    def test_the_ownership_tag_is_appended_to_an_inherited_one(self):
        env = review.child_environment(self.layout, self.binary, "job1", {review.OWNERSHIP_VAR: "outer"})
        self.assertEqual(env[review.OWNERSHIP_VAR], "outer:job1")

    def test_the_path_includes_the_interpreter_directory(self):
        shim = self.tmp / "shim"
        shim.mkdir()
        (shim / "node").write_text("")
        script = self.tmp / "bin" / "codex.js"
        script.write_text(f"#!{shim / 'node'}\n")
        script.chmod(0o755)
        env = review.child_environment(self.layout, script, "job1", {})
        self.assertEqual(env["PATH"], f"{script.parent}:{shim}:{review.SYSTEM_PATH}")
        self.assertIsNone(review.interpreter_dir(self.binary.parent / "missing"))

    def test_an_unresolvable_binary_refuses(self):
        with self.assertRaises(ReviewLaunchRefusedError):
            review.resolve_binary(str(self.tmp / "no-such-codex"))

    def test_build_home_and_the_startup_load_check(self):
        digest = review.build_home(self.layout, self.operator)
        self.assertEqual(digest, hashlib.sha256((self.operator / "auth.json").read_bytes()).hexdigest())
        self.assertEqual((self.layout.codex_home / "auth.json").stat().st_mode & 0o777, 0o600)
        review.check_home(self.layout)
        config = (self.layout.codex_home / "config.toml").read_text()
        for forbidden in ("mcp_servers", "hooks", "notify", "profiles", "plugins", "projects"):
            self.assertNotIn(forbidden, config)

    def test_a_home_without_credentials_refuses(self):
        (self.operator / "auth.json").unlink()
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            review.build_home(self.layout, self.operator)
        self.assertEqual(raised.exception.evidence["check"], "credentials")

    def test_pinned_codex_state_entries_are_accepted_and_anything_else_names_itself(self):
        review.build_home(self.layout, self.operator)
        home = self.layout.codex_home
        for name in ("history.jsonl", "installation_id", "models_cache.json", "logs_1.sqlite", "state.sqlite-wal"):
            (home / name).write_text("x")
        (home / "skills" / ".system").mkdir(parents=True)
        (home / "skills" / ".codex-system-skills.marker").write_text("1")
        (home / "plugins" / "cache").mkdir(parents=True)
        review.check_home(self.layout)
        cases = {
            "mystery": lambda: (home / "mystery").write_text("x"),
            "extra.toml": lambda: (home / "extra.toml").write_text("x"),
            "hooks.json": lambda: (home / "hooks.json").write_text("{}"),
            "rules": lambda: (home / "rules").mkdir(),
            "skills/evil": lambda: (home / "skills" / "evil").mkdir(),
            "plugins/evil": lambda: (home / "plugins" / "evil").mkdir(),
        }
        for entry, create in cases.items():
            with self.subTest(entry):
                create()
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    review.check_home(self.layout)
                self.assertEqual(raised.exception.evidence["entry"], entry)
                runtime.remove_tree(self.tmp, f"reviews/job1/codex-home/{entry}")

    def test_a_config_toml_that_differs_from_the_pinned_digest_refuses(self):
        review.build_home(self.layout, self.operator)
        (self.layout.codex_home / "config.toml").write_text('[mcp_servers.x]\ncommand = "x"\n')
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            review.check_home(self.layout)
        self.assertEqual(raised.exception.evidence["entry"], "config.toml")

    def test_outside_layers(self):
        system = self.tmp / "etc"
        system.mkdir()
        review.check_outside_layers(system)                              # absent
        (system / "config.toml").write_text("  \n")
        review.check_outside_layers(system)                              # empty
        for name in review.SYSTEM_LAYER_FILES:
            with self.subTest(name):
                (system / name).write_text('[mcp_servers.x]\ncommand = "x"\n')
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    review.check_outside_layers(system)
                self.assertEqual(raised.exception.evidence["source"], str(system / name))
                (system / name).write_text("")

    def test_the_effective_policy_check_names_the_layer_and_value(self):
        good = {k: {"value": v, "source": "override"} for k, v in review.EFFECTIVE_POLICY.items()}
        good.update(model={"value": "gpt-6.1-sol", "source": "override"},
                    model_reasoning_effort={"value": "high", "source": "override"},
                    service_tier={"value": review.SERVICE_TIER, "source": "override"})
        review.check_effective(good)
        bad_values = {
            "model_provider": "evil", "approval_policy": "on-request", "sandbox_mode": "danger-full-access",
            "sandbox_workspace_write.network_access": True, "model": "other", "service_tier": "fast",
            "mcp_servers": {"x": 1}, "notify": ["x"], "web_search": "live",
        }
        for key, value in bad_values.items():
            with self.subTest(key):
                changed = {**good, key: {"value": value, "source": "managed:/m.toml"}}
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    review.check_effective(changed)
                self.assertEqual(raised.exception.evidence["key"], key)
                self.assertEqual(raised.exception.evidence["source"], "managed:/m.toml")
        for key in ("model_providers.x.auth.command", "openai_base_url", "permissions.p.network", "profile"):
            with self.subTest(key):
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    review.check_effective({**good, key: {"value": "x", "source": "system:/etc/codex/config.toml"}})
                self.assertEqual(raised.exception.evidence["key"], key)


class CredentialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-cred-", dir=REPO_ROOT / "build"))
        self.addCleanup(runtime.remove_tree, self.tmp, ".")
        self.layout = review.JobLayout(self.tmp, "job1")
        self.operator = self.tmp / "operator-home"
        self.operator.mkdir()
        self.auth = self.operator / "auth.json"
        self.auth.write_text(json.dumps({"tokens": {"refresh_token": "orig"}}))
        self.auth.chmod(0o640)
        self.digest = review.build_home(self.layout, self.operator)
        self.mine = self.layout.codex_home / "auth.json"

    def test_an_unrefreshed_copy_is_not_written_back(self):
        before = self.auth.stat().st_ino
        self.assertEqual(review.copy_back_credentials(self.layout, self.operator, self.digest), "unchanged")
        self.assertEqual(self.auth.stat().st_ino, before)

    def test_a_refreshed_copy_replaces_the_operators_atomically_with_its_mode(self):
        self.mine.write_text(json.dumps({"tokens": {"refresh_token": "rotated"}}))
        self.assertEqual(review.copy_back_credentials(self.layout, self.operator, self.digest), "copied")
        self.assertEqual(json.loads(self.auth.read_text())["tokens"]["refresh_token"], "rotated")
        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o640)
        self.assertEqual([p.name for p in self.operator.iterdir()], ["auth.json"])

    def test_a_changed_operator_file_is_never_overwritten(self):
        self.mine.write_text(json.dumps({"tokens": {"refresh_token": "rotated"}}))
        self.auth.write_text(json.dumps({"tokens": {"refresh_token": "interactive"}}))
        self.assertEqual(review.copy_back_credentials(self.layout, self.operator, self.digest), "operator-changed")
        self.assertEqual(json.loads(self.auth.read_text())["tokens"]["refresh_token"], "interactive")

    def test_an_unparseable_or_missing_copy_is_not_written_back(self):
        self.mine.write_text("not json")
        self.assertEqual(review.copy_back_credentials(self.layout, self.operator, self.digest), "invalid")
        self.mine.unlink()
        self.assertEqual(review.copy_back_credentials(self.layout, self.operator, self.digest), "missing")
        self.assertEqual(json.loads(self.auth.read_text())["tokens"]["refresh_token"], "orig")


class FakeLaunchTest(unittest.TestCase):
    """`launch_review` end to end against the fake, with the registry patched
    (the real one is empty until the D12 pre-step is recorded)."""

    def setUp(self):
        patch = mock.patch.dict(review.HARNESSES, {"codex": review.CODEX})
        patch.start()
        self.addCleanup(patch.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="review-launch-", dir=REPO_ROOT / "build"))
        self.addCleanup(runtime.remove_tree, self.tmp, ".")
        self.repo = self.tmp / "repo"
        fixtures.git_init(self.repo)
        (self.repo / "a.txt").write_text("a\n")
        (self.repo / ".gitignore").write_text(".controller/\n.ai-review/\n")
        fixtures.commit_all(self.repo, "init")
        self.runtime = self.repo / ".controller" / "runtime"
        self.runtime.mkdir(parents=True)
        self.operator = self.tmp / "operator-home"
        self.operator.mkdir()
        self.auth = self.operator / "auth.json"
        self.auth.write_text(json.dumps({"tokens": {"refresh_token": "orig"}}))
        self.log = self.tmp / "fake.log"
        self.marker = self.tmp / "marker"
        self.system = self.tmp / "etc-codex"
        self.system.mkdir()

    def launch(self, scenario=None, *, stage="plan", job="job1", timeout=60, compose=True, binary=None, **extra):
        scenario = {"log": str(self.log), "system_dir": str(self.system), "orphan_seconds": 30, **(scenario or {})}
        binary = binary or fake_codex.write_binary(self.tmp / "bin", scenario)
        if compose is True:
            compose = self.compose(binary, job)
        return review.launch_review(
            worktree=self.repo, runtime_root=self.runtime, job_id=job, harness_name="codex", binary_name=str(binary),
            operator_home=self.operator, stage=stage, manifest=MANIFEST, bundle_dir="/b/current", model="gpt-6.1-sol",
            effort="high", timeout=timeout, system_dir=self.system, compose=compose or None,
            inherited_env={"PATH": "/evil", "OPENAI_API_KEY": "evil"}, **extra)

    def compose(self, binary, job):
        """The fake's composing command (its own recorded shape): the pinned
        overrides of an ``exec`` launch, printed with their source layers."""
        layout = review.JobLayout(self.runtime, job)

        def run(env):
            pinned = review.argv("exec", binary=binary, tree=layout.tree, verdict=layout.verdict, tmpdir=layout.tmp,
                                 prompt="-")
            overrides = [item for i, item in enumerate(pinned) if i and pinned[i - 1] == "-c"]
            command = [str(binary), "debug", "config", "-C", str(layout.tree)]
            for item in overrides:
                command += ["-c", item]
            result = review.run_bounded(command, cwd=layout.tree, env=env, timeout=30)
            self.assertEqual(result.exit_code, 0, result.stderr)
            return json.loads(result.stdout)
        return run

    def records(self, kind=None):
        return [r for r in fake_codex.read_log(self.log) if kind is None or r["kind"] == kind]

    def hostile_server(self, name: str):
        script = self.tmp / f"{name}.sh"
        script.write_text(f"#!/bin/sh\necho ran > {self.marker}.{name}\n")
        script.chmod(0o755)
        return script

    # -- the happy path and the boundary ------------------------------------

    def test_an_exact_verdict_is_returned_and_the_job_files_are_gone(self):
        result = self.launch()
        self.assertEqual(result["outcome"], "verdict", result)
        self.assertEqual(result["status"], "APPROVE")
        self.assertIn("Reviewed bundle ID: " + MANIFEST["bundle_id"], result["verdict"])
        self.assertEqual(sorted(p.name for p in (self.runtime / "reviews" / "job1").iterdir()), ["codex-home"])
        self.assertEqual(sorted(p.name for p in (self.runtime / "reviews" / "job1" / "codex-home").iterdir()),
                         ["sessions"])
        kinds = [r["kind"] for r in self.records()]
        self.assertEqual(kinds, ["mcp", "features", "compose", "exec"])

    def test_every_invocation_gets_exactly_the_allowlisted_environment_and_the_minimal_home(self):
        self.launch()
        for record in self.records():
            with self.subTest(kind=record["kind"]):
                self.assertEqual(sorted(record["env"]), sorted(review.ENV_ALLOWLIST))
                self.assertNotIn("evil", json.dumps(record["env"]))
                self.assertEqual(record["codex_home"], str(self.runtime / "reviews" / "job1" / "codex-home"))
                self.assertEqual(record["env"][review.OWNERSHIP_VAR], "job1")
        execution = self.records("exec")[0]
        self.assertEqual(execution["approval_policy"], "never")
        self.assertIs(execution["network_access"], False)
        self.assertEqual(execution["web_search"], "disabled")
        self.assertEqual((execution["model"], execution["effort"], execution["service_tier"]),
                         ("gpt-6.1-sol", "high", review.SERVICE_TIER))
        tmpdir = str(self.runtime / "reviews" / "job1" / "tmp")
        self.assertEqual(execution["writable_roots"], [execution["cwd"], tmpdir])
        self.assertEqual(execution["cwd"], str(self.runtime / "reviews" / "job1" / "tree"))
        self.assertEqual(sorted(set(execution["features_enabled"]) & set(review.DISABLED_FEATURES)), [])

    def test_inherited_servers_hooks_notify_and_profiles_are_never_run(self):
        script = self.hostile_server("op")
        (self.operator / "config.toml").write_text(
            f'notify = ["{script}"]\n[mcp_servers.anything]\ncommand = "{script}"\n[hooks.start]\ncommand = "{script}"\n'
            f'[profiles.ops]\nnotify = ["{script}"]\n')
        (self.repo / ".codex").mkdir()
        (self.repo / ".codex" / "config.toml").write_text(f'[mcp_servers.proj]\ncommand = "{script}"\n')
        (self.system / "config.toml").write_text("")
        result = self.launch()
        self.assertEqual(result["outcome"], "verdict", result)
        self.assertFalse(Path(f"{self.marker}.op").exists())
        for record in self.records():
            self.assertEqual(record["ran"], [])

    def test_the_same_servers_do_run_when_the_launch_uses_the_operators_home(self):
        # the counter-example behind the minimal home: -c mcp_servers={} merges
        script = self.hostile_server("op")
        (self.operator / "config.toml").write_text(f'[mcp_servers.anything]\ncommand = "{script}"\n')
        binary = fake_codex.write_binary(self.tmp / "bin", {"log": str(self.log), "orphan_seconds": 30})
        tree = self.tmp / "tree"
        tree.mkdir()
        env = {"PATH": review.SYSTEM_PATH, "HOME": str(self.tmp), "CODEX_HOME": str(self.operator)}
        command = review.argv("exec", binary=binary, tree=tree, verdict=self.tmp / "v.md", tmpdir=self.tmp)
        review.run_bounded([*command[:-1], "-c", "mcp_servers={}", "-"], cwd=tree, env=env, timeout=30,
                           stdin_text="prompt")
        self.assertTrue(Path(f"{self.marker}.op").exists())

    def test_a_system_or_managed_layer_with_content_refuses_and_nothing_runs(self):
        script = self.hostile_server("sys")
        for name in review.SYSTEM_LAYER_FILES:
            with self.subTest(name):
                (self.system / name).write_text(f'[mcp_servers.s]\ncommand = "{script}"\n')
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    self.launch(job=f"job-{name}")
                self.assertEqual(raised.exception.evidence["source"], str(self.system / name))
                (self.system / name).write_text("")
        self.assertFalse(Path(f"{self.marker}.sys").exists())
        self.assertEqual(self.records(), [])

    def test_a_managed_layer_is_caught_by_the_composed_values_check(self):
        managed = self.tmp / "managed.toml"
        managed.write_text('model_provider = "corp"\n[model_providers.corp]\nbase_url = "http://x"\n')
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            self.launch({"managed": str(managed)})
        self.assertEqual(raised.exception.evidence["check"], "effective")
        self.assertEqual(raised.exception.evidence["source"], f"managed:{managed}")
        self.assertEqual(self.records("exec"), [])
        self.assertEqual(self.records("compose")[0]["ran"], [])

    def test_each_managed_override_in_turn_is_refused(self):
        script = self.hostile_server("mgd")
        layers = {
            "hook": f'[hooks.h]\ncommand = "{script}"\n',
            "approval": 'approval_policy = "on-request"\n',
            "network": "[sandbox_workspace_write]\nnetwork_access = true\n",
            "sandbox": 'sandbox_mode = "danger-full-access"\n',
            "permissions": '[permissions.p]\nname = "p"\n',
            "provider": '[model_providers.c]\nname = "c"\n[model_providers.c.auth]\ncommand = "x"\n',
        }
        for name, text in layers.items():
            with self.subTest(name):
                managed = self.tmp / f"m-{name}.toml"
                managed.write_text(text)
                with self.assertRaises(ReviewLaunchRefusedError) as raised:
                    self.launch({"managed": str(managed)}, job=f"job-{name}")
                self.assertEqual(raised.exception.evidence["check"], "effective")
                self.assertEqual(raised.exception.evidence["source"], f"managed:{managed}")
        self.assertFalse(Path(f"{self.marker}.mgd").exists())
        self.assertEqual(self.records("exec"), [])

    def test_an_empty_outside_layer_does_not_refuse(self):
        for name in review.SYSTEM_LAYER_FILES:
            (self.system / name).write_text("")
        self.assertEqual(self.launch()["outcome"], "verdict")

    def test_without_a_composing_command_the_harness_does_not_launch(self):
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            self.launch(compose=False)
        self.assertEqual(raised.exception.evidence["check"], "effective")
        self.assertEqual(self.records("exec"), [])

    def test_a_feature_not_in_the_allowlist_stops_the_launch(self):
        capture = self.tmp / "features.txt"
        capture.write_text(FEATURES_CAPTURE.read_text() + "shiny_new_tool stable true\n")
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            self.launch({"features": str(capture)})
        self.assertEqual(raised.exception.evidence["entry"], "shiny_new_tool")
        self.assertEqual(self.records("exec"), [])

    def test_a_non_empty_mcp_list_stops_the_launch(self):
        script = self.hostile_server("x")
        managed = self.tmp / "m.toml"
        managed.write_text(f'[mcp_servers.m]\ncommand = "{script}"\n')
        # the composed check would catch it too; the mcp list check runs first
        with self.assertRaises(ReviewLaunchRefusedError) as raised:
            self.launch({"managed": str(managed)})
        self.assertEqual(raised.exception.evidence["check"], "mcp")

    def test_browser_and_computer_use_are_unavailable(self):
        result = self.launch({"exec": {"tool_attempts": ["browser_use", "computer_use", "plugins", "image_generation",
                                                         "multi_agent"], "use_web_search": True}})
        self.assertEqual(result["outcome"], "verdict")
        execution = self.records("exec")[0]
        self.assertEqual([t for t in execution["tools"] if t["used"]], [])
        self.assertEqual(len(execution["tools"]), 5)

    def test_a_write_outside_the_working_roots_is_blocked_by_the_fake_and_recorded(self):
        outside = self.tmp / "outside.txt"
        self.launch({"exec": {"write_outside": [str(outside)]}})
        self.assertFalse(outside.exists())
        self.assertEqual(self.records("exec")[0]["writes"], [{"path": str(outside), "blocked": True}])

    def test_the_runtime_directory_inside_an_unignored_worktree_is_not_a_change(self):
        (self.repo / ".gitignore").write_text("")
        fixtures.commit_all(self.repo, "no ignore")
        self.assertEqual(self.launch()["outcome"], "verdict")

    def test_the_private_tmpdir_is_the_only_extra_writable_root(self):
        inside = self.runtime / "reviews" / "job1" / "tmp" / "scratch"
        self.launch({"exec": {"write_outside": [str(inside), str(Path(tempfile.gettempdir()) / "review-x")]}})
        writes = self.records("exec")[0]["writes"]
        self.assertFalse(writes[0]["blocked"])
        self.assertTrue(writes[1]["blocked"])
        self.assertFalse((Path(tempfile.gettempdir()) / "review-x").exists())

    # -- the state the fake writes -------------------------------------------

    def test_codex_written_state_does_not_refuse_the_retry(self):
        result = self.launch({"exec": {"verdict": "wrong_id_first"}})
        self.assertEqual(result["outcome"], "verdict", result)
        self.assertEqual(len(self.records("exec")), 2)
        # the boundary ran before each exec, the retry's too, on a home carried forward and never rebuilt
        self.assertEqual([r["kind"] for r in self.records()],
                         ["mcp", "features", "compose", "exec", "mcp", "features", "compose", "exec"])

    def test_an_unlisted_state_entry_refuses_the_next_launch_by_name(self):
        for extra, name in (("mystery", "mystery"), ("hooks.json", "hooks.json"), ("skills/evil/", "skills/evil"),
                            ("plugins/evil/", "plugins/evil")):
            with self.subTest(extra), self.assertRaises(ReviewLaunchRefusedError) as raised:
                self.launch({"state_extra": [extra]}, job=f"job-{name.replace('/', '-')}")
            self.assertEqual(raised.exception.evidence["entry"], name)
            self.assertEqual(self.records("exec"), [])
            self.log.unlink(missing_ok=True)

    # -- the verdict ---------------------------------------------------------

    def test_a_wrong_id_gets_one_retry_with_the_exact_values(self):
        result = self.launch({"exec": {"verdict": "wrong_id_first"}})
        self.assertEqual(result["outcome"], "verdict")
        self.assertEqual(len(self.records("exec")), 2)

    def test_a_second_mismatch_ends_the_job_with_the_diff_and_ingests_nothing(self):
        result = self.launch({"exec": {"verdict": "bad_revision"}})
        self.assertEqual(result["outcome"], "mismatch")
        self.assertIsNone(result["verdict"])
        self.assertIn("Plan revision reviewed:", result["detail"])
        self.assertEqual([m.label for m in result["check"].mismatches], ["Plan revision reviewed:"])
        self.assertEqual(len(self.records("exec")), 2)

    def test_no_verdict_a_limit_and_a_failure_are_told_apart(self):
        self.assertEqual(self.launch({"exec": {"verdict": "none"}}, job="j-empty")["outcome"], "empty")
        limit = self.launch({"exec": {"verdict": "limit"}}, job="j-limit")
        self.assertEqual(limit["outcome"], "limit")
        failed = self.launch({"exec": {"verdict": "none", "exit": 3}}, job="j-failed")
        self.assertEqual(failed["outcome"], "failed")

    def test_a_hung_review_is_killed_at_the_deadline_with_its_descendants(self):
        started = []
        result = self.launch({"exec": {"verdict": "hang", "descendant": True}, "hang_seconds": 20}, timeout=2,
                             on_spawn=lambda pid, pgid: started.append(pgid))
        self.assertEqual(result["outcome"], "timeout")
        self.assertEqual(len(started), 1)
        self.assertTrue(process_fixtures.wait_until(lambda: not self.group_alive(started[0])))

    @staticmethod
    def group_alive(pgid):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_the_implementation_stage_uses_its_own_values(self):
        result = self.launch(stage="implementation")
        self.assertEqual(result["outcome"], "verdict")
        self.assertIn("Implementation revision reviewed: 2", result["verdict"])
        self.assertIn("MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", result["verdict"])
        self.assertIn("Reviewer model: openai/gpt-6.1-sol", result["verdict"])

    # -- the repository guard ------------------------------------------------

    def test_a_sandbox_escape_into_the_real_worktree_voids_the_verdict(self):
        result = self.launch({"exec": {"escape_to": str(self.repo / "escaped.txt")}})
        self.assertEqual(result["outcome"], "changed")
        self.assertIsNone(result["verdict"])
        self.assertIn("file:escaped.txt", result["detail"])

    def test_a_state_identity_change_voids_the_verdict(self):
        identities = iter(["s1", "s2"])
        result = self.launch(state_identity=lambda: next(identities))
        self.assertEqual(result["outcome"], "changed")
        self.assertIn("state_identity", result["detail"])

    def test_git_commands_the_reviewer_runs_cannot_move_the_real_refs(self):
        refs = fixtures.run(["git", "-C", str(self.repo), "for-each-ref"]).stdout
        result = self.launch({"exec": {"git": [["update-ref", "refs/heads/evil", "HEAD"], ["config", "evil.k", "1"]]}})
        self.assertEqual(result["outcome"], "verdict", result)
        self.assertEqual(fixtures.run(["git", "-C", str(self.repo), "for-each-ref"]).stdout, refs)
        self.assertEqual(fixtures.run(["git", "-C", str(self.repo), "config", "--get", "evil.k"], check=False).stdout, "")

    # -- credentials and readings ----------------------------------------------

    def test_a_refreshed_credential_is_written_back_and_the_working_copy_deleted(self):
        result = self.launch({"exec": {"refresh_auth": True}})
        self.assertEqual(result["credentials"], "copied")
        self.assertIn("rotated-", self.auth.read_text())
        self.assertFalse((self.runtime / "reviews" / "job1" / "codex-home" / "auth.json").exists())

    def test_an_operator_file_changed_meanwhile_is_left_alone(self):
        self.auth.write_text(json.dumps({"tokens": {"refresh_token": "interactive"}}))
        original = self.auth.read_text()

        def change_during(pid, pgid):
            self.auth.write_text(json.dumps({"tokens": {"refresh_token": "interactive-2"}}))
        result = self.launch({"exec": {"refresh_auth": True}}, on_spawn=change_during)
        self.assertEqual(result["credentials"], "operator-changed")
        self.assertNotEqual(self.auth.read_text(), original)
        self.assertIn("interactive-2", self.auth.read_text())

    def test_a_missing_credential_file_refuses_the_launch(self):
        self.auth.unlink()
        with self.assertRaises(ReviewLaunchRefusedError):
            self.launch()
        self.assertEqual(self.records(), [])

    def test_the_end_readings_come_from_the_jobs_own_home_and_the_sessions_stay(self):
        result = self.launch({"rate_limits": {"primary": 42.0, "secondary": 11.0}})
        windows = {r["window"]: r["percent"] for r in result["readings"]}
        self.assertEqual(sorted(windows.values()), [11.0, 42.0])
        sessions = self.runtime / "reviews" / "job1" / "codex-home" / "sessions"
        self.assertEqual(len(list(sessions.glob("*/*/*/rollout-*.jsonl"))), 1)

    def test_an_unregistered_harness_never_starts_anything(self):
        with mock.patch.dict(review.HARNESSES, clear=True), self.assertRaises(ReviewLaunchRefusedError):
            self.launch()
        self.assertEqual(self.records(), [])
        self.assertFalse((self.runtime / "reviews").exists())


class RunnerTest(unittest.TestCase):
    def test_a_keyboard_interrupt_kills_the_group_before_it_propagates(self):
        pgids = []

        def interrupt(pid, pgid):
            pgids.append(pgid)
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            review.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], cwd=Path("/"),
                               env={"PATH": review.SYSTEM_PATH}, timeout=30, on_spawn=interrupt)
        self.assertTrue(process_fixtures.wait_until(lambda: not FakeLaunchTest.group_alive(pgids[0])))

    def test_the_child_is_its_own_session_leader_and_gets_the_given_environment_only(self):
        result = review.run_bounded(
            [sys.executable, "-c", "import os; print(os.getsid(0)==os.getpid(), sorted(os.environ))"], cwd=Path("/"),
            env={"A": "1"}, timeout=30)
        self.assertEqual(result.stdout.decode().split(" ", 1)[0], "True")
        self.assertNotIn("HOME", result.stdout.decode())
        self.assertIn("'A'", result.stdout.decode())
        self.assertEqual(result.pgid, result.pid)


if __name__ == "__main__":
    unittest.main()
