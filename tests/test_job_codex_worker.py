"""`workflow-controller-codex-worker-job-record` CP2: a Codex worker run recorded as a job (`controller.job`).

Offline, through ``job.execute_step``: a fixture adapter and a ``CodexLauncher`` subclass are registered for the test
only, a route reaches them, both module seams are set and the worker is the scripted fake Codex of H4b2. Nothing real
reaches the branch: the real registry is asserted unchanged at the end.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import (  # noqa: E402
    cli, codex_worker as cw, codex_worker_job, codex_worker_sandbox as ws, codex_worker_tree, harness, job, protocol_decision, review,
    routing, usage_record, worker, worker_launch,
)
from controller.decision import phase_from_wire  # noqa: E402
from controller.errors import WorkerLaunchError  # noqa: E402
from tests import test_codex_worker as cwtest  # noqa: E402
from tests import process_fixtures  # noqa: E402
from tests.test_codex_worker_sandbox import make_clone  # noqa: E402
from tests.test_job import FAKE_IDENTITY, _build_target  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_protocol_outcome import _Env  # noqa: E402

FIXTURE = "codex-fixture"
EIGHT = {"runtime_root", "job_id", "operator_home", "cwd", "timeout", "on_spawn", "model", "effort"}
GENERIC_COMMAND = "---\ndescription: a fixture command\nargument-hint: \"<x>\"\nstate_writer: false\n---\nRun: $ARGUMENTS\n"


def per_test_job_id(test_id: str, n: int) -> str:
    """A job id that is the test's own: `t-<test name, last 24 characters>-<8 hex digits of sha256(test_id)>-<pid>-<n>`.

    The job id is the ownership tag `resume` and the tag scans use, so two tests (or two parallel copies of one) must
    never share it. The `t-` prefix is load-bearing: the id is a landing ref component and must pass
    `gitrepo._LANDING_JOB_ID` (`^[A-Za-z0-9][A-Za-z0-9._-]*$`), while a test name's tail can start with `_`. The tail is
    only for a human reading a tag; the digest of the full test id (module.class.method) is what keeps two names that end
    alike apart.
    """
    digest = hashlib.sha256(test_id.encode()).hexdigest()[:8]
    return f"t-{test_id.rsplit('.', 1)[-1][-24:]}-{digest}-{os.getpid()}-{n}"


class FixtureLauncher(cw.CodexLauncher):
    """A ``CodexLauncher`` whose ``launch`` adds the launcher's own test seams to the eight options the job passes."""

    def __init__(self, test: "JobFixture") -> None:
        self.test = test
        self.calls: list[dict] = []
        self.results: list = []

    def launch(self, task: str, **options):
        self.calls.append(dict(options))
        test = self.test
        if test.write_command:
            (test.commands).mkdir(exist_ok=True)
            name = task.split()[0][1:]
            if not (test.commands / f"{name}.md").exists():
                (test.commands / f"{name}.md").write_text(GENERIC_COMMAND)
        result = super().launch(
            task, commands_dir=test.commands, binary_name=str(test.binary), system_dir=test.system,
            real_home=test.real_home, run=test.controls(options["job_id"]), **options)
        self.results.append(result)
        return result


class JobFixture(cwtest.LaunchFixture):
    write_command = True

    def setUp(self) -> None:
        super().setUp()
        self.recorded = False
        self.ids = 0
        self.launcher = FixtureLauncher(self)
        fixture = dataclasses.replace(harness.CODEX, name=FIXTURE, reviewer_name=None, roles=(harness.WORKER,))
        self.enterContext(mock.patch.object(harness, "ADAPTERS", {**harness.ADAPTERS, FIXTURE: fixture}))
        self.enterContext(mock.patch.object(worker_launch, "LAUNCHERS",
                                            {**worker_launch.LAUNCHERS, (FIXTURE, harness.WORKER): self.launcher}))
        self.enterContext(mock.patch.object(job, "_new_job_id", self.next_id))
        self.enterContext(mock.patch.object(job, "CODEX_WORKTREE_PREPARER", self.prepare))
        self.enterContext(mock.patch.object(job, "CODEX_LANDER", process_fixtures.nothing_lander))
        self.enterContext(mock.patch.object(job, "CODEX_HOME_RESOLVER", lambda: self.operator))
        self.options = self.routing_options()
        (self.tmp / "legacy-root").mkdir()
        self.managed = _build_target(self.tmp / "legacy-root", phase="PLANNING", governing_workflow_version="2.1")

    def next_id(self) -> str:
        self.ids += 1
        return f"job{self.ids}"

    def prepare(self, managed_repo, layout, base):
        if not layout.worktree.exists():
            make_clone(layout.worktree)
        return process_fixtures.prepared_clone(layout.worktree)

    def routing_options(self) -> routing.RoutingOptions:
        roles = {role: {"harness": FIXTURE, "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT}
                 for role in sorted(routing.ROLES) if routing.ADAPTER_ROLE[role] == harness.WORKER}
        config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<test>")
        return routing.RoutingOptions(config=config, config_source="settings")

    def run_step(self, **kw) -> dict:
        if not self.recorded:
            self.record()
        kw.setdefault("timeout", 30)
        return job.execute_step(self.managed, identity=FAKE_IDENTITY, runtime=self.runtime, routing=self.options, **kw)

    def only_record(self) -> dict:
        (path,) = sorted((self.runtime / "jobs").glob("*.json"))
        return json.loads(path.read_text())

    def assert_not_started(self, error: str) -> dict:
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.WORKER_NOT_STARTED_CODE)
        self.assertEqual(record["reconciliation_evidence"]["error"], error)
        self.assertNotIn("worker_process", record)
        self.assertEqual(self.execs(), [])
        self.assert_no_copy()
        return record

    def assert_no_copy(self) -> None:
        for path in (self.runtime / "worker").glob("*/codex-home/auth.json"):
            self.fail(f"a credential copy is left at {path}")


class CleanRunTest(JobFixture):
    def test_a_clean_run_on_a_legacy_target_is_recorded_in_the_claude_shape(self) -> None:
        self.script(message="all done", subagent=500, stdout="the run said this", stderr="a transcript line")
        record = self.run_step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "phase_not_in_to_any_of")
        self.assertFalse(record["transition_verified"])
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)
        block = record["worker"]
        self.assertEqual((block["subtype"], block["exit_code"], block["is_error"]), ("success", 0, False))
        for field in ("session_id", "num_turns", "total_cost_usd", "permission_denials", "terminal_reason",
                      "stop_reason", "owned_processes_seen"):
            self.assertIsNone(block[field], field)
        self.assertEqual(block["result_excerpt"], "all done")
        self.assertEqual(block["stream_diagnosis"]["harness"], "codex")
        self.assertEqual(record["worker_state"]["state"], worker.ENDED)
        self.assertEqual(record["ownership_tag"], "job1")
        self.assertIn("pid", record["worker_process"])
        self.assertEqual(record["worker_streams"]["format"], "text")
        (result,) = self.launcher.results
        self.assertIn("the run said this", result.stdout)
        self.assertIn("a transcript line", result.stderr)
        streams = record["worker_streams"]
        self.assertEqual(Path(streams["stdout_path"]).read_text(), result.stdout)
        self.assertEqual(Path(streams["stderr_path"]).read_text(), result.stderr)
        rollouts = block["stream_diagnosis"]["usage_rollouts"]
        self.assertEqual(record["usage_record"], usage_record.from_codex_rollouts(
            rollouts, model=review.DEFAULT_MODEL, wall_ms=block["duration_ms"]).to_dict())
        self.assertEqual((record["usage_record"]["harness"], record["usage_record"]["subagent_total_tokens"]),
                         ("codex", 500))
        self.assertNotIn("usage", record)
        self.assertEqual(record["telemetry"]["problems"], [{"kind": "no_stream", "path": None}])
        self.assertEqual(record["telemetry"]["harness"], FIXTURE)
        validity = job.validate_record(record, managed_repo=self.managed, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid, validity)
        self.assert_no_copy()

    def test_the_option_mapping_reaches_the_launcher_with_exactly_the_eight_keys(self) -> None:
        self.run_step()
        (options,) = self.launcher.calls
        self.assertEqual(set(options), EIGHT)
        self.assertEqual(options["operator_home"], self.operator)
        self.assertEqual(options["cwd"], ws.WorkerLayout(self.runtime, "job1").worktree)
        self.assertEqual((options["model"], options["effort"]), (review.DEFAULT_MODEL, review.DEFAULT_EFFORT))
        self.assertEqual(options["timeout"], 30)

    def test_a_build_that_raises_gives_usage_record_failed_and_nothing_escapes(self) -> None:
        def broken(result, route):
            def build():
                raise ValueError("no figures")
            return build

        with mock.patch.object(codex_worker_job, "usage_builder", broken):
            record = self.run_step()
        self.assertEqual(record["usage_record_failed"], "ValueError: no figures")
        self.assertNotIn("usage_record", record)
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)

    def test_the_credential_is_copied_back_into_the_codex_home_the_resolver_names(self) -> None:
        self.script(message="done", refresh_auth=True)
        record = self.run_step()
        self.assertIn("rotated-", (self.operator / "auth.json").read_text())
        self.assertIsNotNone(record["worker"]["stream_diagnosis"]["credentials"])
        self.assert_no_copy()

    def test_a_codex_home_without_a_credential_file_ends_worker_not_started(self) -> None:
        (self.operator / "auth.json").unlink()
        with self.assertRaises(Exception):
            self.run_step()
        self.assert_not_started("ReviewLaunchRefusedError")


class CodexHomeTest(unittest.TestCase):
    def test_the_job_layers_codex_home_is_the_clis_with_and_without_the_environment_variable(self) -> None:
        for env in ({"CODEX_HOME": "/somewhere/codex"}, {}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                if not env:
                    os.environ.pop("CODEX_HOME", None)
                self.assertEqual(job._codex_home(), cli._codex_home())


class UsageGateTest(JobFixture):
    """H4d (D1): a Codex launcher is admitted on the Codex provider and accounted; the full cases are in
    ``tests.test_job_codex_worker_usage``."""

    def test_a_supplied_usage_gate_is_called_once_with_the_codex_provider_and_the_job_carries_a_usage_block(self) -> None:
        gate = mock.Mock(return_value="token-1")
        with mock.patch.object(job.usage, "bind_job"), \
                mock.patch.object(job, "_usage_start_block", return_value={"token": "token-1"}), \
                mock.patch.object(job, "_usage_account", side_effect=lambda _root, record, **_kw: record):
            record = self.run_step(usage_gate=gate)
        gate.assert_called_once()
        self.assertEqual(gate.call_args.kwargs, {"provider": "codex"})
        self.assertEqual(record["usage"]["token"], "token-1")
        self.assertEqual(record["usage"]["accounting"], "pending")
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)

    def test_a_chain_selecting_the_codex_entry_under_a_gate_carries_its_token_and_runs(self) -> None:
        roles = {role: {"harness": FIXTURE, "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT,
                        "fallback": [{"harness": FIXTURE, "model": review.DEFAULT_MODEL,
                                      "effort": review.DEFAULT_EFFORT},
                                     {"harness": "claude-code", "model": "opus"}]}
                 for role in sorted(routing.ROLES) if routing.ADAPTER_ROLE[role] == harness.WORKER}
        config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<test>")
        self.options = routing.RoutingOptions(config=config, config_source="settings")
        gate = mock.Mock(return_value="token-1")
        with mock.patch.object(job.usage, "bind_job"), \
                mock.patch.object(job, "_usage_start_block", return_value={"token": "token-1"}), \
                mock.patch.object(job, "_usage_account", side_effect=lambda _root, record, **_kw: record):
            record = self.run_step(usage_gate=gate)
        self.assertEqual(record["usage"]["token"], "token-1")
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)


class OutcomeTest(JobFixture):
    def test_a_timeout_run_is_failed_with_an_interrupted_outcome_and_the_timeout_subtype(self) -> None:
        self.script(verdict="hang", message="late")
        record = self.run_step(timeout=2)
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual((record["worker_outcome"], record["worker"]["subtype"]), (worker.INTERRUPTED, "timeout"))
        self.assertEqual(self.tagged("job1"), [])
        self.assert_no_copy()

    def test_a_limit_record_with_no_derivable_window_is_a_failure_with_the_limit_subtype_and_no_usage_limit_fact(self) -> None:
        self.script(verdict="limit", message="partial")
        record = self.run_step()
        self.assertEqual((record["worker_outcome"], record["worker"]["subtype"]), (worker.FAILURE, "usage_limit"))
        self.assertNotIn("usage_limit", record["worker"]["stream_diagnosis"])
        self.assertIsNone(job.usage_limit_of(record))
        self.assertNotEqual(record["status"], job.STATUS_INTERRUPTED)

    def test_a_breach_is_a_failure_and_never_finished_even_when_the_transition_would_verify(self) -> None:
        to_phase = phase_from_wire(self.expected_to_phase())
        with mock.patch.object(job, "_observe_post_phase", return_value=to_phase), \
                mock.patch.object(job, "_postcondition_for_phase", return_value=None):
            clean = self.run_step()
            self.assertEqual(clean["status"], job.STATUS_FINISHED)  # the control: this run verifies
            self.script(message="done", write_outside=[str(self.worktree.parent.parent / "job2" / "tree" / ".git"
                                                           / "hooks" / "pre-commit")])
            self.recorded = False  # a new script is a new build digest
            breached = self.run_step()
        self.assertEqual((breached["worker_outcome"], breached["worker"]["subtype"]), (worker.FAILURE,
                                                                                       "sandbox_breach"))
        self.assertNotEqual(breached["status"], job.STATUS_FINISHED)
        self.assertFalse(breached["transition_verified"])
        self.assert_no_copy()

    def expected_to_phase(self) -> str:
        """The phase the legacy transition expects, from a throwaway refused run on a scratch runtime."""
        scratch = Path(tempfile.mkdtemp(prefix="codex-probe-", dir=self.tmp))
        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", None), self.assertRaises(WorkerLaunchError):
            job.execute_step(self.managed, identity=FAKE_IDENTITY, runtime=scratch, routing=self.options, timeout=30)
        (path,) = sorted((scratch / "jobs").glob("*.json"))
        self.ids = 0
        return json.loads(path.read_text())["expected_transition"]["to_any_of"][0]


class RefusalTest(JobFixture):
    def test_an_unverified_build_ends_worker_not_started(self) -> None:
        self.recorded = True  # nothing recorded in the build record
        with self.assertRaises(Exception):
            job.execute_step(self.managed, identity=FAKE_IDENTITY, runtime=self.runtime, routing=self.options,
                             timeout=30)
        self.assert_not_started("ReviewLaunchRefusedError")

    def test_a_task_that_is_not_a_slash_command_ends_worker_not_started(self) -> None:
        with mock.patch.object(job, "worker_task", return_value="not a command"), self.assertRaises(Exception):
            self.run_step()
        self.assert_not_started("ReviewLaunchRefusedError")

    def test_an_unknown_command_ends_worker_not_started(self) -> None:
        self.write_command = False
        with self.assertRaises(Exception) as ctx:
            self.run_step()
        self.assert_not_started(type(ctx.exception).__name__)

    def test_a_user_only_command_ends_worker_not_started(self) -> None:
        self.write_command = False
        (self.commands / "milestone-plan.md").write_text(cwtest.USER_ONLY)
        with self.assertRaises(Exception) as ctx:
            self.run_step()
        self.assert_not_started(type(ctx.exception).__name__)

    def test_a_missing_commands_directory_ends_worker_not_started(self) -> None:
        self.write_command = False
        self.commands = self.tmp / "no-such-commands"
        with self.assertRaises(Exception) as ctx:
            self.run_step()
        self.assert_not_started(type(ctx.exception).__name__)

    def test_a_preparer_refusal_ends_worker_not_started_with_no_spawn_and_names_the_cause(self) -> None:
        def refuse(managed_repo, layout, base):
            raise codex_worker_tree.CodexTreeError("managed_tree_not_clean", "the managed tree has changes")

        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", refuse), self.assertRaises(codex_worker_tree.CodexTreeError):
            self.run_step()
        record = self.assert_not_started("CodexTreeError")
        self.assertEqual(record["reconciliation_evidence"]["evidence"]["cause"], "managed_tree_not_clean")
        self.assertEqual(self.launcher.calls, [])

    def test_the_preparer_is_given_the_base_the_controller_captured(self) -> None:
        seen = {}

        def prepare(managed_repo, layout, base):
            seen["base"] = base
            return self.prepare(managed_repo, layout, base)

        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", prepare):
            record = self.run_step()
        self.assertEqual(seen["base"], record["pre_state"]["target_head"])
        self.assertEqual(record["codex_worker"]["clone"]["path"], str(ws.WorkerLayout(self.runtime, record["job_id"]).worktree))

    def test_a_launcher_refusal_after_the_preparer_ran_removes_the_tree(self) -> None:
        self.recorded = True  # nothing recorded in the build record: the build gate refuses after the tree exists
        made = []

        def prepare(managed_repo, layout, base):
            prepared = self.prepare(managed_repo, layout, base)
            made.append(prepared.path)
            return prepared

        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", prepare), self.assertRaises(Exception):
            job.execute_step(self.managed, identity=FAKE_IDENTITY, runtime=self.runtime, routing=self.options,
                             timeout=30)
        self.assert_not_started("ReviewLaunchRefusedError")
        (path,) = made
        self.assertFalse(path.exists())

    def test_the_missing_tree_seam_is_a_worker_launch_error_and_no_process_starts(self) -> None:
        with mock.patch.object(job, "CODEX_WORKTREE_PREPARER", None), self.assertRaises(WorkerLaunchError):
            self.run_step()
        self.assert_not_started("WorkerLaunchError")
        self.assertEqual(self.launcher.calls, [])


class UnexpectedPathsTest(JobFixture):
    def launch_that(self, behaviour):
        def fake(task, **options):
            behaviour(options)

        return mock.patch.object(cw, "launch_codex_worker", fake)

    def spawn(self, options) -> None:
        options["on_spawn"](os.getpid(), os.getpgid(0))

    def test_a_keyboard_interrupt_after_the_spawn_leaves_a_launched_record_with_the_process(self) -> None:
        def behaviour(options):
            self.spawn(options)
            raise KeyboardInterrupt

        with self.launch_that(behaviour), self.assertRaises(KeyboardInterrupt):
            self.run_step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        self.assertEqual(record["worker_process"]["pid"], os.getpid())
        self.assertEqual(record["ownership_tag"], "job1")

    def test_any_other_exception_after_the_spawn_ends_the_record_failed_naming_it(self) -> None:
        def behaviour(options):
            self.spawn(options)
            raise RuntimeError("boom")

        with self.launch_that(behaviour), self.assertRaises(RuntimeError):
            self.run_step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"], {
            "code": "RuntimeError", "message": "boom", "clone": record["codex_worker"]["clone"]["path"]})
        self.assertIn("worker_process", record)

    def test_a_controller_error_after_the_spawn_is_failed_not_worker_not_started(self) -> None:
        def behaviour(options):
            self.spawn(options)
            raise WorkerLaunchError("late", evidence={})

        with self.launch_that(behaviour), self.assertRaises(WorkerLaunchError):
            self.run_step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], "WorkerLaunchError")

    def test_a_non_controller_error_before_the_spawn_ends_the_record_failed_naming_it(self) -> None:
        def behaviour(options):
            raise OSError("disk")

        with self.launch_that(behaviour), self.assertRaises(OSError):
            self.run_step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], "OSError")
        self.assertNotIn("worker_process", record)


class ProtocolDecisionTest(unittest.TestCase):
    """A protocol decision reaches the Codex branch with the shared currency check and the Workflow's own reconcile."""

    def setUp(self) -> None:
        self.fixture = JobFixture("run_step")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.env = _Env(self)
        self.fixture.managed = self.env.inspect()
        self.fixture.runtime = self.env.runtime

    def step(self) -> dict:
        fixture = self.fixture
        fixture.record()
        return job.execute_step(self.env.inspect(), identity=IDENTITY, runtime=self.env.runtime,
                                routing=fixture.options, timeout=30)

    def test_a_stale_answer_before_the_spawn_ends_the_terminal_record_with_no_process(self) -> None:
        stale = SimpleNamespace(current=False, reason="answer_changed", evidence=lambda: {"reason": "answer_changed"})
        real = protocol_decision.check_currency

        def check(*args, **kwargs):  # only the check immediately before the spawn answers stale
            return stale if sys._getframe(1).f_code.co_name == "_launch_job" else real(*args, **kwargs)

        with mock.patch.object(protocol_decision, "check_currency", check):
            self.step()
        (record,) = self.env.records()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["reconciliation_evidence"]["code"], protocol_decision.DECISION_STALE_AT_LAUNCH)
        self.assertNotIn("worker_process", record)
        self.assertEqual(self.fixture.launcher.calls, [])

    def test_a_clean_run_on_the_unlanded_clone_is_finished_with_progress_none_not_failed(self) -> None:
        record = self.step()
        self.assertEqual(record["protocol"]["reconcile"]["class"], "no_progress")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["protocol"]["progress"], "none")
        self.assertEqual(record["worker_outcome"], worker.SUCCESS)
        self.assertEqual(record["worker_streams"]["format"], "text")


class UnchangedClaudePathTest(unittest.TestCase):
    def test_the_real_registrations_and_the_claude_stream_format_are_unchanged(self) -> None:
        self.assertNotIn((harness.CODEX.name, harness.WORKER), worker_launch.LAUNCHERS)
        self.assertNotIn(harness.WORKER, harness.CODEX.roles)
        self.assertEqual(job.WORKER_STREAM_FORMAT, "stream-json")
        self.assertFalse(job._is_codex_launcher(worker_launch.for_harness(harness.CLAUDE_CODE.name)))
        self.assertIsNone(job.CODEX_WORKTREE_PREPARER)
        self.assertIs(job.CODEX_HOME_RESOLVER, job._codex_home)

    def test_the_predicate_is_on_the_launcher_object(self) -> None:
        self.assertTrue(job._is_codex_launcher(cw.CodexLauncher()))
        self.assertTrue(job._is_codex_launcher(type("Sub", (cw.CodexLauncher,), {})()))
        self.assertFalse(job._is_codex_launcher(object()))


if __name__ == "__main__":
    unittest.main()
