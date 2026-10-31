"""`workflow-controller-codex-tester-surroundings` CP2: the Codex tester launch (`codex_tester.launch_codex_functional`).
Offline: the tester is the scripted fake Codex, the launch controls use a fake runner, the repository is a temporary
one with a scratch bare origin and the build record is written by `codex_build` for the fake launcher.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_build, codex_tester as ct, functional_review as fr, review  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests import fake_codex, fixtures  # noqa: E402
from tests.test_codex_bridge import git  # noqa: E402
from tests.test_codex_tester import TesterFixture  # noqa: E402

D1_KEYS = {"outcome", "breach", "unbound", "fake_calls", "output_dir", "sandbox", "keep", "credentials", "detail",
           "readings", "first_readings", "head", "checklist_blob", "writable", "refused", "tester_message",
           "telemetry", "subagents", "exit_code"}


class LaunchFixture(TesterFixture):
    def setUp(self) -> None:
        super().setUp()
        self.runtime.mkdir(mode=0o700)
        node = shutil.which("node")
        assert node, "the fixture's fake Codex needs node"
        (self.prefix / "bin" / "node").symlink_to(node)
        self.origin = self.tmp / "origin.git"
        fixtures.git_init(self.origin, "--bare")
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "origin", "main")
        self.script(message="all flows passed")

    def record(self) -> str:
        digest = codex_build.build_digest(self.binary)
        assert digest
        codex_build.record_verified(self.runtime, digest, {"note": "test"})
        return digest

    def launch(self, **kw) -> dict:
        candidate = kw.pop("candidate", None) or self.candidate()
        kw.setdefault("run", self.controls_for_launch())
        return ct.launch_codex_functional(
            candidate=candidate, flows=[fr.Flow("flow-1", "1", "run the thing")], runtime_root=self.runtime,
            job_id=kw.pop("job_id", "job1"), work_item_id="item-1", operator_home=self.operator, timeout=20,
            binary_name=str(kw.pop("binary", self.binary)), system_dir=self.system, real_home=self.real_home, **kw)

    def controls_for_launch(self):
        """The three launch controls answer from the layout the launch itself builds: composed lazily."""
        from tests.test_codex_sandbox import FEATURES, NO_SERVERS, composed_for, result
        import json

        def run(command, **kw):
            layout = ct.codex_sandbox.TesterLayout(self.runtime, "job1")
            from controller import codex_sandbox as cs
            built_roots = cs.readable_roots(layout, self.binary, operator_home=self.real_home)
            if command[1] in ("mcp", "features"):
                return result({"mcp": NO_SERVERS, "features": FEATURES}[command[1]])
            answer = composed_for(layout, list(built_roots), model=review.DEFAULT_MODEL, effort=review.DEFAULT_EFFORT)
            body = (json.dumps({"id": 1, "result": {}}) + "\n" + json.dumps({"id": 2, "result": answer}) + "\n").encode()
            return result(body)

        return run

    def functional_dirs(self) -> list[str]:
        area = self.runtime / "functional"
        return sorted(p.name for p in area.iterdir()) if area.exists() else []

    def refusal(self, **kw) -> fr.TesterUnavailable:
        with self.assertRaises(fr.TesterUnavailable) as ctx:
            self.launch(**kw)
        return ctx.exception


class GateTest(LaunchFixture):
    def assert_gate_refusal(self, error: fr.TesterUnavailable) -> None:
        self.assertEqual(error.code, fr.GATE_TESTER_UNAVAILABLE)
        self.assertEqual(error.evidence["cause"], fr.UNAVAILABLE_ENFORCEMENT)
        self.assertIn("verify-codex", str(error))
        self.assertFalse(self.log.exists())  # no process started
        self.assertEqual(self.functional_dirs(), [])

    def test_no_record_refuses_before_anything_is_made(self) -> None:
        self.assert_gate_refusal(self.refusal())

    def test_an_untrusted_record_refuses(self) -> None:
        (self.runtime / codex_build.RECORD_NAME).write_text("not json")
        self.assert_gate_refusal(self.refusal())

    def test_a_digest_that_is_not_recorded_refuses(self) -> None:
        codex_build.record_verified(self.runtime, "a" * 64, {})
        self.assert_gate_refusal(self.refusal())

    def test_an_unbindable_layout_refuses(self) -> None:
        self.record()
        with mock.patch.object(codex_build, "build_digest", return_value=None):
            self.assert_gate_refusal(self.refusal())

    def test_a_missing_binary_is_no_binary(self) -> None:
        error = self.refusal(binary=self.tmp / "nowhere" / "codex")
        self.assertEqual(error.evidence["cause"], fr.UNAVAILABLE_NO_BINARY)
        self.assertEqual(self.functional_dirs(), [])


class RunTest(LaunchFixture):
    def test_a_clean_run_returns_every_key_and_leaves_the_sandbox_for_settle(self) -> None:
        self.record()
        outcome = self.launch()
        self.assertEqual(D1_KEYS - set(outcome), set())
        self.assertEqual((outcome["outcome"], outcome["breach"], outcome["unbound"], outcome["keep"]),
                         ("ran", [], [], False))
        self.assertEqual(outcome["tester_message"], "all flows passed")
        self.assertTrue(Path(outcome["sandbox"]).is_dir())
        self.assertEqual(outcome["output_dir"], Path(outcome["sandbox"]) / "scratch" / "out")
        self.assertEqual(len(self.execs()), 1)
        review.settle_functional(outcome)
        self.assertEqual(self.functional_dirs(), [])

    def test_a_symlinked_launcher_runs_the_node_next_to_the_symlink(self) -> None:
        self.record()
        self.assertTrue(self.binary.is_symlink())
        outcome = self.launch()
        self.assertEqual(outcome["outcome"], "ran")
        self.assertEqual(len(self.execs()), 1)
        self.assertFalse((self.package / "bin" / "node").exists())  # only the launcher's directory has a node

    def test_the_fake_call_log_of_a_stand_in_the_tester_used_appears(self) -> None:
        self.record()

        real = ct.run_tester

        def using_gh(bridge, *a, **k):
            (bridge.layout.fake_logs / "gh.jsonl").write_text('{"argv": ["pr", "list"]}\n')
            return real(bridge, *a, **k)

        with mock.patch.object(ct, "run_tester", using_gh):
            outcome = self.launch()
        self.assertEqual(outcome["fake_calls"].get("gh"), [{"argv": ["pr", "list"]}])


class BreachTest(LaunchFixture):
    def breach_of(self, mutate) -> dict:
        self.record()
        real = ct.run_tester

        def mutating(bridge, *a, **k):
            made = real(bridge, *a, **k)
            mutate(bridge)
            return made

        with mock.patch.object(ct, "run_tester", mutating):
            return self.launch()

    def test_a_changed_worktree_is_a_breach_and_keeps_the_sandbox(self) -> None:
        outcome = self.breach_of(lambda bridge: (self.repo / "src" / "data.txt").write_text("two\n"))
        self.assertTrue(outcome["keep"])
        self.assertTrue(outcome["breach"])
        self.assertTrue(Path(outcome["sandbox"]).is_dir())
        review.settle_functional(outcome)
        self.assertTrue(Path(outcome["sandbox"]).is_dir())

    def test_a_new_head_or_ref_is_a_breach(self) -> None:
        outcome = self.breach_of(lambda bridge: git(self.repo, "branch", "stray"))
        self.assertTrue(outcome["keep"])
        self.assertTrue(outcome["breach"])

    def test_a_new_commit_moving_head_is_a_breach(self) -> None:
        def commit(bridge):
            (self.repo / "src" / "extra.txt").write_text("x\n")
            git(self.repo, "add", "src/extra.txt")
            git(self.repo, "commit", "-q", "-m", "stray")

        outcome = self.breach_of(commit)
        self.assertTrue(outcome["breach"])
        self.assertTrue(outcome["keep"])

    def test_a_changed_local_origin_is_an_origin_refs_breach(self) -> None:
        def push(bridge):
            git(self.repo, "push", "-q", "origin", "main:refs/heads/other")

        outcome = self.breach_of(push)
        self.assertIn("origin refs", outcome["breach"])
        self.assertTrue(outcome["keep"])

    def test_a_record_removed_during_the_run_is_a_codex_build_breach(self) -> None:
        outcome = self.breach_of(lambda bridge: (self.runtime / codex_build.RECORD_NAME).unlink())
        self.assertIn(f"codex build: no {codex_build.RECORD_NAME} record exists", outcome["breach"])
        self.assertTrue(outcome["keep"])

    def test_a_digest_that_changes_to_another_recorded_digest_is_a_breach(self) -> None:
        def edit(bridge):
            entry = self.package / "bin" / "codex.js"
            entry.write_text(entry.read_text() + "\n// edited\n")
            codex_build.record_verified(self.runtime, codex_build.build_digest(self.binary), {"note": "other"})

        outcome = self.breach_of(edit)
        self.assertIn("codex build: the build's digest changed during the run", outcome["breach"])
        self.assertTrue(outcome["keep"])

    def test_a_launcher_re_pointed_to_another_target_is_a_breach(self) -> None:
        other = self.prefix / "lib" / "other.js"
        other.write_text((self.package / "bin" / "codex.js").read_text())
        other.chmod(0o755)

        def repoint(bridge):
            self.binary.unlink()
            self.binary.symlink_to(other)

        self.record()
        target = self.binary.resolve()
        outcome = self.breach_of(repoint)
        self.assertIn(f"codex build: the launcher now resolves to {other.resolve()}, not {target}", outcome["breach"])
        self.assertTrue(outcome["keep"])

    def test_a_clone_altered_during_the_run_is_unbound(self) -> None:
        outcome = self.breach_of(lambda bridge: (bridge.clone / "src" / "app.py").write_text("changed\n"))
        self.assertTrue(outcome["unbound"])
        self.assertTrue(outcome["keep"])


class RefusalTest(LaunchFixture):
    def test_a_pre_existing_sandbox_refuses_and_is_left_alone(self) -> None:
        self.record()
        base = self.runtime / "functional" / "job1"
        base.mkdir(parents=True)
        (base / "marker").write_text("mine")
        error = self.refusal()
        self.assertEqual(error.evidence["cause"], fr.UNAVAILABLE_ENFORCEMENT)
        self.assertEqual((base / "marker").read_text(), "mine")

    def test_a_refused_control_is_unavailable_with_no_sandbox_and_no_credential_copy(self) -> None:
        self.record()

        refused = review._refuse("a control refused", check="x", entry="x")
        with mock.patch.object(ct, "_check_controls", side_effect=refused):
            error = self.refusal()
        self.assertEqual(error.evidence["cause"], fr.UNAVAILABLE_ENFORCEMENT)
        self.assertIs(type(error.__cause__), ReviewLaunchRefusedError)
        self.assertEqual(self.functional_dirs(), [])
        self.assertFalse(self.log.exists())

    def test_an_altered_argv_is_unavailable_and_starts_nothing(self) -> None:
        self.record()
        real = ct.run_tester

        def altered(bridge, *a, **k):
            object.__setattr__(bridge.built, "argv", (*bridge.built.argv, "--extra"))
            return real(bridge, *a, **k)

        with mock.patch.object(ct, "run_tester", altered):
            error = self.refusal()
        self.assertEqual(error.evidence["cause"], fr.UNAVAILABLE_ENFORCEMENT)
        self.assertEqual(self.functional_dirs(), [])
        self.assertEqual(self.execs(), [])

    def test_a_clone_mismatch_passes_through_and_leaves_no_sandbox(self) -> None:
        self.record()
        with mock.patch("controller.functional_review.walk_differences", return_value=["src/app.py: differs"]):
            error = self.refusal()
        self.assertEqual(error.evidence["cause"], fr.UNAVAILABLE_CLONE_MISMATCH)
        self.assertEqual(self.functional_dirs(), [])

    def test_any_other_exception_propagates_and_removes_the_sandbox(self) -> None:
        self.record()
        with mock.patch.object(ct, "run_tester", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.launch()
        self.assertEqual(self.functional_dirs(), [])


class UnchangedTest(unittest.TestCase):
    # C10g-3b2b's own scope guard, pinned to its base commit: C10g-3c, which edits `job.py` and calls the launch,
    # deletes this class.
    def test_the_claude_launch_and_the_step_are_untouched(self) -> None:
        shown = subprocess.run(
            ["git", "diff", "01c67ac..HEAD", "--stat", "--", "controller/review.py", "controller/job.py",
             "controller/functional_review.py"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        if shown.returncode != 0:
            self.skipTest("the base commit is not available")
        self.assertEqual(shown.stdout.strip(), "")

    def test_only_the_launch_module_its_tests_and_the_package_reference_the_function(self) -> None:
        found = subprocess.run(["git", "grep", "-l", "launch_codex_functional", "--", "controller", "tests"],
                               cwd=REPO_ROOT, capture_output=True, text=True, check=False).stdout.split()
        self.assertEqual(set(found) - {"controller/codex_tester.py", "tests/test_codex_tester_launch.py"}, set())


if __name__ == "__main__":
    unittest.main()
