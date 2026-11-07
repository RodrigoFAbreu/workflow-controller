"""`workflow-controller-codex-worker-launch` CP2: the Codex worker launch (`controller.codex_worker`). Offline: the
worker is the scripted fake Codex, the launch controls use a fake runner (no `app-server` starts), the worktree is a
real temporary clone and the build record is written by `codex_build` for the fake launcher.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_build, codex_tester, codex_worker as cw, codex_worker_sandbox as ws  # noqa: E402
from controller import harness, review, worker, worker_launch  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests import fake_codex  # noqa: E402
from tests.test_codex_worker_sandbox import FEATURES, NO_SERVERS, Fixture, composed_for, make_clone, result  # noqa: E402

COMMAND = "---\ndescription: do the thing\nargument-hint: \"<x>\"\nstate_writer: false\n---\nDo the thing: $ARGUMENTS\n"
USER_ONLY = "---\ndescription: only the user\ndisable-model-invocation: true\n---\nUser only.\n"


class LaunchFixture(Fixture):
    def setUp(self) -> None:
        super().setUp()
        # Each test's own inherited tag: the classes of this module run in parallel shards, and a deadline kills every
        # process of the user carrying the exact tag, so a shared `...:job1` would let one shard kill another's fake.
        inherited = os.environ.get(review.OWNERSHIP_VAR)
        self.tag_prefix = f"{inherited + ':' if inherited else ''}{self.id().rsplit('.', 1)[-1]}-{os.getpid()}"
        self.enterContext(mock.patch.dict(os.environ, {review.OWNERSHIP_VAR: self.tag_prefix}))
        node = shutil.which("node")
        assert node, "the fixture's fake Codex needs node"
        (self.prefix / "bin" / "node").symlink_to(node)
        self.runtime.mkdir(mode=0o700)
        self.log = self.tmp / "fake.log"
        self.commands = self.tmp / "commands"
        self.commands.mkdir()
        (self.commands / "do-thing.md").write_text(COMMAND)
        (self.commands / "user-only.md").write_text(USER_ONLY)
        self.worktree = self.layout.worktree
        make_clone(self.worktree)
        self.script(message="done")
        self.recorded = False

    def script(self, **exec_spec) -> None:
        scenario = {"log": str(self.log), "exec": exec_spec, "hang_seconds": 60, "orphan_seconds": 120}
        fake_codex.write_binary(self.package / "bin", scenario, name="codex.js")

    def record(self) -> str:
        digest = codex_build.build_digest(self.binary)
        assert digest
        codex_build.record_verified(self.runtime, digest, {"note": "test"})
        self.recorded = True
        return digest

    def controls(self, job_id: str = "job1", *, servers: bool = False, composed: dict | None = None):
        """A runner for the controls that starts no process; the composed answer is the worker's own policy."""
        layout = ws.WorkerLayout(self.runtime, job_id)

        def run(command, **kw):
            kind = command[1]
            if kind == "mcp":
                return result(b"srv  /bin/true\n" if servers else NO_SERVERS)
            if kind == "features":
                return result(FEATURES)
            roots = ws.readable_roots(layout, self.binary, python=Path(sys.executable).resolve(),
                                      operator_home=self.real_home)
            answer = composed if composed is not None else composed_for(layout, roots)
            body = (json.dumps({"id": 1, "result": {}}) + "\n" + json.dumps({"id": 2, "result": answer}) + "\n")
            return result(body.encode())

        return run

    def launch(self, task: str = "/do-thing now", **kw) -> worker.WorkerResult:
        if not self.recorded:
            self.record()
        job_id = kw.setdefault("job_id", "job1")
        kw.setdefault("run", self.controls(job_id))
        kw.setdefault("timeout", 20)
        kw.setdefault("cwd", ws.WorkerLayout(self.runtime, job_id).worktree)
        return cw.launch_codex_worker(
            task, runtime_root=self.runtime, operator_home=self.operator, commands_dir=self.commands,
            binary_name=str(self.binary), system_dir=self.system, real_home=self.real_home, **kw)

    def refusal(self, *args, **kw) -> Exception:
        with self.assertRaises(Exception) as ctx:
            self.launch(*args, **kw)
        return ctx.exception

    def execs(self) -> list[dict]:
        return [entry for entry in fake_codex.read_log(self.log) if entry["kind"] == "exec"]

    def credential_copy(self, job_id: str = "job1") -> Path:
        return ws.WorkerLayout(self.runtime, job_id).codex_home / review.CODEX.credential_file

    def tagged(self, tag: str) -> list[int]:
        found = []
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    for item in (entry / "environ").read_bytes().split(b"\0"):
                        value = item.decode("utf-8", "replace").partition("=")[2] \
                            if item.startswith(f"{review.OWNERSHIP_VAR}=".encode()) else ""
                        if value == f"{self.tag_prefix}:{tag}":
                            found.append(int(entry.name))
                except OSError:
                    continue
        return found

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.worktree), "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()


class CleanRunTest(LaunchFixture):
    def test_a_clean_run_is_success_with_the_message_the_commit_and_no_credential_copy(self) -> None:
        before = self.head()
        self.script(message="all done", write_outside=[str(self.worktree / "new.txt")],
                    git=[["add", "new.txt"], ["commit", "-q", "-m", "edit"]])
        outcome = self.launch()
        self.assertIsInstance(outcome, worker.WorkerResult)
        self.assertEqual((outcome.outcome, outcome.result, outcome.returncode), (worker.SUCCESS, "all done", 0))
        self.assertEqual((outcome.is_error, outcome.subtype), (False, cw.SUBTYPE_SUCCESS))
        self.assertNotEqual(self.head(), before)
        self.assertEqual((self.worktree / "new.txt").read_text(), "fake codex wrote this")
        self.assertEqual(outcome.stream_diagnosis["breach"], [])
        self.assertFalse(self.credential_copy().exists())
        self.assertIsNone(outcome.owned_processes_seen)
        for field in ("session_id", "total_cost_usd", "raw_json", "num_turns", "terminal_reason", "stop_reason",
                      "permission_denials"):
            self.assertIsNone(getattr(outcome, field), field)
        self.assertGreaterEqual(outcome.duration_ms, 0)

    def test_the_rendered_command_is_the_prompt_on_standard_input(self) -> None:
        self.launch("/do-thing now please")
        (entry,) = self.execs()
        self.assertEqual(entry["argv"][-1], "-")
        self.assertEqual(entry["cwd"], str(self.worktree))

    def test_the_child_environment_carries_the_job_id_as_its_ownership_tag(self) -> None:
        self.launch()
        (entry,) = self.execs()
        self.assertEqual(entry["env"][review.OWNERSHIP_VAR], f"{self.tag_prefix}:job1")

    def test_a_deadline_kill_leaves_a_process_tagged_with_another_job_alive(self) -> None:
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 env={**os.environ, review.OWNERSHIP_VAR: f"{self.tag_prefix}:other-job"}, start_new_session=True)
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        self.script(verdict="hang", message="late", descendant=True)
        outcome = self.launch(timeout=2)
        self.assertEqual(outcome.outcome, worker.INTERRUPTED)
        self.assertEqual(self.tagged("job1"), [])
        self.assertIsNone(other.poll())
        self.assertEqual(self.tagged("other-job"), [other.pid])  # the detector sees a live tagged process


class OrderTest(LaunchFixture):
    def assert_nothing_started(self) -> None:
        self.assertFalse(self.log.exists())

    def test_an_unrecorded_build_is_refused_naming_the_command_before_anything_is_written(self) -> None:
        shutil.rmtree(self.runtime)
        self.recorded = True  # the launch helper must not record
        error = self.refusal()
        self.assertIsInstance(error, ReviewLaunchRefusedError)
        self.assertIn("verify-codex", str(error))
        self.assert_nothing_started()
        self.assertFalse(self.runtime.exists())

    def test_a_task_that_is_not_a_slash_command_is_refused(self) -> None:
        for task in ("do the thing", "", "/", "  "):
            with self.subTest(task=task):
                self.assertIsInstance(self.refusal(task), ReviewLaunchRefusedError)
        self.assert_nothing_started()

    def test_an_unknown_a_user_only_and_an_argument_naming_a_user_only_command_are_refused(self) -> None:
        for task in ("/no-such-command", "/user-only", "/do-thing user-only", "/do-thing /user-only"):
            with self.subTest(task=task):
                self.refusal(task)
        self.assert_nothing_started()
        self.assertFalse((self.runtime / "reviews").exists())
        self.assertFalse(self.layout.codex_home.exists())

    def test_a_cwd_other_than_the_layouts_worktree_is_refused(self) -> None:
        other = self.tmp / "other"
        make_clone(other)
        self.assertEqual(self.refusal(cwd=other).evidence["check"], "cwd")
        self.assert_nothing_started()
        self.assertFalse(self.layout.codex_home.exists())

    def test_an_existing_codex_home_a_reused_job_is_refused(self) -> None:
        self.layout.codex_home.mkdir(parents=True)
        self.assertEqual(self.refusal().evidence["check"], "job")
        self.assert_nothing_started()

    def test_a_worktree_without_its_own_git_directory_is_refused(self) -> None:
        shutil.rmtree(self.worktree / ".git")
        self.assertEqual(self.refusal().evidence["check"], "worktree")
        self.assert_nothing_started()

    def test_a_nested_git_entry_before_the_run_is_refused(self) -> None:
        (self.worktree / "vendor" / "lib").mkdir(parents=True)
        (self.worktree / "vendor" / "lib" / ".git").write_text("gitdir: elsewhere\n")
        error = self.refusal()
        self.assertEqual(error.evidence["check"], "nested")
        self.assertEqual(error.evidence["entry"], "vendor/lib/.git")
        self.assert_nothing_started()
        self.assertFalse(self.layout.codex_home.exists())

    def test_a_runtime_root_inside_the_worktree_is_refused(self) -> None:
        shutil.rmtree(self.worktree)
        self.worktree.symlink_to(self.runtime)
        with mock.patch.object(ws, "check_worktree"), mock.patch.object(ws, "check_nested"):
            error = self.refusal()
        self.assertEqual(error.evidence["check"], "runtime_root")
        self.assert_nothing_started()


class ControlsTest(LaunchFixture):
    def assert_stopped_before_exec(self) -> None:
        self.assertEqual(self.execs(), [])
        self.assertFalse(self.credential_copy().exists())

    def test_check_argv_refuses_before_exec_and_removes_the_credential_copy(self) -> None:
        with mock.patch.object(ws, "check_argv", side_effect=ReviewLaunchRefusedError("no", evidence={})):
            self.assertIsInstance(self.refusal(), ReviewLaunchRefusedError)
        self.assert_stopped_before_exec()

    def test_check_launch_refuses_before_exec_and_removes_the_credential_copy(self) -> None:
        error = self.refusal(run=self.controls(servers=True))
        self.assertIsInstance(error, ReviewLaunchRefusedError)
        self.assert_stopped_before_exec()

    def test_check_composed_refuses_before_exec_and_removes_the_credential_copy(self) -> None:
        import copy
        layout = ws.WorkerLayout(self.runtime, "job1")
        roots = ws.readable_roots(layout, self.binary, python=Path(sys.executable).resolve(),
                                  operator_home=self.real_home)
        bad = copy.deepcopy(composed_for(layout, roots))
        bad["config"]["approval_policy"] = "on-request"
        error = self.refusal(run=self.controls(composed=bad))
        self.assertEqual(error.evidence["key"], "approval_policy")
        self.assert_stopped_before_exec()

    def test_a_build_that_refuses_after_writing_the_copy_propagates_and_leaves_no_copy(self) -> None:
        def stray(*args, **kw):
            (self.layout.codex_home / "stray-entry").write_text("x")

        with mock.patch.object(ws.functional_review, "write_launchers", side_effect=stray):
            error = self.refusal()
        self.assertIsInstance(error, ReviewLaunchRefusedError)
        self.assertEqual(error.evidence["check"], "home")
        self.assert_stopped_before_exec()


class OutcomeTest(LaunchFixture):
    def test_a_hang_past_the_deadline_is_interrupted_and_leaves_no_process_of_the_tag(self) -> None:
        self.script(verdict="hang", message="late", descendant=True)
        started = time.monotonic()
        outcome = self.launch(timeout=2)
        self.assertEqual((outcome.outcome, outcome.subtype), (worker.INTERRUPTED, cw.SUBTYPE_TIMEOUT))
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(self.tagged("job1"), [])
        self.assertFalse(self.credential_copy().exists())

    def test_termination_by_a_signal_without_a_timeout_is_interrupted(self) -> None:
        killed = review.RunResult(-signal.SIGKILL, False, b"", b"", 1, 1)
        with mock.patch.object(review, "run_bounded", return_value=killed):
            outcome = self.launch()
        self.assertEqual((outcome.outcome, outcome.subtype, outcome.returncode),
                         (worker.INTERRUPTED, cw.SUBTYPE_SIGNAL, -signal.SIGKILL))
        self.assertTrue(outcome.is_error)

    def test_a_prompt_replaces_the_rendered_command_and_every_control_still_runs(self) -> None:
        self.script(message="done")
        outcome = self.launch("/do-thing now", prompt="Review this, please.")
        self.assertEqual(outcome.outcome, worker.SUCCESS)
        (entry,) = self.execs()
        self.assertEqual(entry["prompt"], "Review this, please.")

    def test_without_a_prompt_the_command_is_rendered_as_before(self) -> None:
        self.script(message="done")
        self.launch("/do-thing now")
        (entry,) = self.execs()
        self.assertEqual(entry["prompt"].strip(), "Do the thing: now")

    def test_a_prompt_does_not_excuse_a_malformed_task_or_an_unverified_build(self) -> None:
        self.recorded = True  # no verified build is recorded: the gate refuses
        self.assertIsInstance(self.refusal("not a command", prompt="p"), ReviewLaunchRefusedError)
        self.assertIsInstance(self.refusal("/do-thing", prompt="p"), ReviewLaunchRefusedError)
        self.assertEqual(self.execs(), [])

    def test_a_limit_record_is_failure(self) -> None:
        self.script(verdict="limit", message="partial")
        outcome = self.launch()
        self.assertEqual((outcome.outcome, outcome.subtype), (worker.FAILURE, cw.SUBTYPE_LIMIT))
        self.assertNotIn("usage_limit", outcome.stream_diagnosis)

    def test_a_limit_record_with_a_full_window_carries_the_usage_limit_fact(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        outcome = self.launch()
        self.assertEqual((outcome.outcome, outcome.subtype), (worker.FAILURE, cw.SUBTYPE_LIMIT))
        fact = outcome.stream_diagnosis["usage_limit"]
        self.assertEqual((fact["window"], fact["rate_limit_type"]), ("five_hour", None))
        self.assertGreater(fact["resets_at"], time.time())

    def test_a_non_zero_exit_is_failure_with_the_output_tail(self) -> None:
        self.script(message="partial", exit=3, stderr="boom: transcript tail")
        outcome = self.launch()
        self.assertEqual((outcome.outcome, outcome.subtype, outcome.returncode), (worker.FAILURE, cw.SUBTYPE_EXIT, 3))
        self.assertIn("boom: transcript tail", outcome.stream_diagnosis["detail"])
        self.assertIn("boom: transcript tail", outcome.stderr)

    def test_a_zero_exit_with_no_final_message_is_ambiguous(self) -> None:
        self.script(message="")
        outcome = self.launch()
        self.assertEqual((outcome.outcome, outcome.subtype), (worker.AMBIGUOUS, cw.SUBTYPE_NO_MESSAGE))

    def test_the_precedence_is_deadline_then_signal_then_limit_then_exit_then_message(self) -> None:
        scan_limit = review.RolloutScan(limit_error=True, limit_detail="limit")
        scan = review.RolloutScan()
        run = lambda code, timed_out=False: review.RunResult(code, timed_out, b"", b"", 1, 1)  # noqa: E731
        self.assertEqual(cw.classify(run(-9, True), scan_limit, "m", 5)[0], worker.INTERRUPTED)
        self.assertEqual(cw.classify(run(-9), scan_limit, "m", 5)[0], worker.INTERRUPTED)
        self.assertEqual(cw.classify(run(1), scan_limit, "m", 5)[:2], (worker.FAILURE, cw.SUBTYPE_LIMIT))
        self.assertEqual(cw.classify(run(1), scan, "m", 5)[:2], (worker.FAILURE, cw.SUBTYPE_EXIT))
        self.assertEqual(cw.classify(run(0), scan, "", 5)[0], worker.AMBIGUOUS)
        self.assertEqual(cw.classify(run(0), scan, "m", 5)[0], worker.SUCCESS)


class BreachTest(LaunchFixture):
    def assert_breach(self, outcome: worker.WorkerResult, needle: str) -> None:
        self.assertEqual(outcome.outcome, worker.FAILURE)
        self.assertEqual((outcome.subtype, outcome.is_error), (cw.SUBTYPE_BREACH, True))
        self.assertTrue(any(needle in entry for entry in outcome.stream_diagnosis["breach"]),
                        outcome.stream_diagnosis["breach"])
        self.assertFalse(self.credential_copy().exists())

    def test_a_hook_written_into_the_git_directory_is_a_breach(self) -> None:
        self.script(message="done", write_outside=[str(self.worktree / ".git" / "hooks" / "pre-commit")])
        self.assert_breach(self.launch(), "guard")

    def test_a_commondir_file_is_a_breach(self) -> None:
        self.script(message="done", write_outside=[str(self.worktree / ".git" / "commondir")])
        self.assert_breach(self.launch(), "commondir")

    def test_a_nested_git_entry_created_by_the_worker_is_a_breach(self) -> None:
        self.script(message="done", write_outside=[str(self.worktree / "sub" / ".git")])
        self.assert_breach(self.launch(), "sub/.git")

    def test_a_changed_build_is_a_breach(self) -> None:
        self.script(message="done", escape_to=str(self.package / "bin" / "codex.js"))
        self.assert_breach(self.launch(), "codex build")

    def test_a_breach_turns_a_timeout_into_failure(self) -> None:
        self.script(verdict="hang", message="late",
                    write_outside=[str(self.worktree / ".git" / "hooks" / "post-commit")])
        self.assert_breach(self.launch(timeout=2), "guard")


class SettlingTest(LaunchFixture):
    def test_a_rotated_credential_is_copied_back_and_the_copy_removed(self) -> None:
        self.script(message="done", refresh_auth=True)
        outcome = self.launch()
        self.assertIn("rotated-", (self.operator / "auth.json").read_text())
        self.assertIsNotNone(outcome.stream_diagnosis["credentials"])
        self.assertFalse(self.credential_copy().exists())

    def test_the_readings_and_sub_agents_are_in_the_diagnosis(self) -> None:
        self.script(message="done", subagent=500)
        outcome = self.launch()
        self.assertEqual(len(outcome.stream_diagnosis["subagents"]), 1)
        self.assertTrue(outcome.stream_diagnosis["usage_rollouts"])

    def test_a_failing_step_does_not_skip_the_next_nor_replace_the_exception_in_flight(self) -> None:
        with mock.patch.object(review, "scan_rollouts", side_effect=OSError("scan broke")), \
                mock.patch.object(review, "copy_back_credentials", side_effect=OSError("copy broke")):
            error = self.refusal(run=self.controls(servers=True))
        self.assertIsInstance(error, ReviewLaunchRefusedError)  # the control's refusal, not the settling errors
        self.assertFalse(self.credential_copy().exists())

    def test_with_nothing_in_flight_the_first_failure_is_raised_and_the_copy_is_still_removed(self) -> None:
        with mock.patch.object(review, "scan_rollouts", side_effect=OSError("scan broke")), \
                mock.patch.object(review, "copy_back_credentials", side_effect=OSError("copy broke")):
            error = self.refusal()
        self.assertEqual(str(error), "scan broke")
        self.assertFalse(self.credential_copy().exists())

    def test_an_exception_the_caller_is_handling_is_not_taken_for_one_in_flight(self) -> None:
        with mock.patch.object(review, "scan_rollouts", side_effect=OSError("scan broke")):
            try:
                raise KeyError("the caller's own")
            except KeyError:
                error = self.refusal()
        self.assertEqual(str(error), "scan broke")
        self.assertFalse(self.credential_copy().exists())


class LauncherTest(LaunchFixture):
    def test_launch_calls_the_launch_with_the_options_unchanged(self) -> None:
        with mock.patch.object(cw, "launch_codex_worker", return_value="sentinel") as launch:
            self.assertEqual(cw.CodexLauncher().launch("/x", job_id="j"), "sentinel")
        launch.assert_called_once_with("/x", job_id="j")

    def test_an_option_codex_has_no_counterpart_for_is_a_type_error(self) -> None:
        for option in ("stdout_path", "stderr_path", "permission_mode", "ownership_tag"):
            with self.subTest(option=option), self.assertRaises(TypeError):
                cw.CodexLauncher().launch("/do-thing", runtime_root=self.runtime, job_id="job1",
                                          operator_home=self.operator, cwd=self.worktree, timeout=1, **{option: "x"})

    def test_the_launcher_satisfies_the_protocol_and_for_harness_reaches_it_for_a_fixture_role(self) -> None:
        fixture = dataclasses.replace(harness.CODEX, name="codex-fixture", reviewer_name=None,
                                      roles=(harness.WORKER,))
        adapters = {**harness.ADAPTERS, fixture.name: fixture}
        launchers = {**worker_launch.LAUNCHERS, fixture.name: cw.CodexLauncher()}
        with mock.patch.object(harness, "ADAPTERS", adapters), mock.patch.object(worker_launch, "LAUNCHERS", launchers):
            self.assertIsInstance(worker_launch.for_harness("codex-fixture"), cw.CodexLauncher)

    def test_the_real_registrations_are_unchanged(self) -> None:
        self.assertEqual(set(worker_launch.LAUNCHERS), {harness.CLAUDE_CODE.name})
        self.assertNotIn(harness.WORKER, harness.CODEX.roles)


if __name__ == "__main__":
    unittest.main()
