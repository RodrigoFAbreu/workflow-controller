"""Tests for the fresh Claude worker abstraction (capability 4,
``controller.worker``).

``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``'s CP5 section names these
cases: a success result classifies ``SUCCESS`` with fields extracted; a
non-zero exit classifies ``FAILURE``; exit 0 with ``is_error: true``
classifies ``FAILURE``; an undecidable stream on exit 0 classifies
``AMBIGUOUS``; a hanging worker with a short timeout classifies
``INTERRUPTED`` and its whole process group is confirmed reaped; a
``SIGTERM``-ed worker classifies ``INTERRUPTED``; a task naming
``/approve-review`` raises ``UserOnlyCommandError`` with no process
spawned; ``cwd`` is confirmed to be the target repository; stdin is
confirmed closed; and the launched process's environment is confirmed to
carry no ``PYTHONPATH``.

Every case here uses ``tests/fake_claude.py``, never the real ``claude``
binary -- this keeps the default suite hermetic, offline, free and
deterministic. The real binary is exercised once, in CP9's opt-in
integration test only.
"""

from __future__ import annotations

import ast
import contextlib
import errno
import fcntl
import json
import os
import re
import secrets
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import anchor, lock, routing, worker, worker_stream  # noqa: E402
from controller.errors import UserOnlyCommandError, WorkerLaunchError  # noqa: E402
from tests import fake_claude, process_fixtures  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"


def _stream_paths(directory) -> dict:
    """``stdout_path``/``stderr_path`` for one launch: two fresh, empty
    files in ``directory``, as ``controller.job`` creates them."""
    paths = {}
    for key in ("stdout_path", "stderr_path"):
        fd, path = tempfile.mkstemp(prefix=f"worker-{key[:6]}-", dir=directory)
        os.close(fd)
        paths[key] = path
    return paths


def _stream(**result_overrides) -> str:
    """A well-formed stream: the fake's default events with the final
    ``result`` event's fields overridden."""
    events = fake_claude.default_events()[:-1]
    return fake_claude.stream_text([*events, fake_claude.result_event(**result_overrides)])


def _launch(task="do the bounded thing", *, cwd, env_overrides, timeout=10, claude_bin=None):
    old = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    try:
        return worker.launch(
            task, cwd=cwd, permission_mode="acceptEdits", timeout=timeout,
            claude_bin=claude_bin or str(FAKE_CLAUDE), **_stream_paths(cwd),
        )
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class SuccessTest(unittest.TestCase):
    def test_success_result_classifies_success_with_fields_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            body = fake_claude.result_event(
                session_id="abc123", terminal_reason="end_turn", stop_reason=None, result="did it",
                num_turns=3, permission_denials=["x"], total_cost_usd=0.01, duration_ms=500,
            )
            stream = fake_claude.stream_text([*fake_claude.default_events()[:-1], body])
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": stream})
            self.assertEqual(result.outcome, worker.SUCCESS)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.session_id, "abc123")
            self.assertIs(result.is_error, False)
            self.assertEqual(result.subtype, "success")
            self.assertEqual(result.terminal_reason, "end_turn")
            self.assertEqual(result.result, "did it")
            self.assertEqual(result.num_turns, 3)
            self.assertEqual(result.permission_denials, ["x"])
            self.assertEqual(result.total_cost_usd, 0.01)
            self.assertEqual(result.duration_ms, 500)
            self.assertEqual(result.raw_json, body)
            self.assertEqual(result.stdout, stream)


class FailureTest(unittest.TestCase):
    def test_nonzero_exit_classifies_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_EXIT": "1"})
            self.assertEqual(result.outcome, worker.FAILURE)
            self.assertEqual(result.returncode, 1)

    def test_exit_zero_with_is_error_true_classifies_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={
                "FAKE_CLAUDE_STDOUT": _stream(session_id="s", is_error=True, result="went wrong"),
            })
            self.assertEqual(result.outcome, worker.FAILURE)
            self.assertEqual(result.returncode, 0)
            self.assertIs(result.is_error, True)


class AmbiguousTest(unittest.TestCase):
    def test_non_json_stdout_on_exit_zero_classifies_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": "not json at all\n"})
            self.assertEqual(result.outcome, worker.AMBIGUOUS)
            self.assertEqual(result.returncode, 0)
            self.assertIsNone(result.raw_json)

    def test_a_result_missing_session_id_classifies_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            event = fake_claude.result_event()
            del event["session_id"]
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": fake_claude.stream_text([event])})
            self.assertEqual(result.outcome, worker.AMBIGUOUS)

    def test_json_array_classifies_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": "[1, 2, 3]\n"})
            self.assertEqual(result.outcome, worker.AMBIGUOUS)

    def test_two_json_documents_on_one_line_classify_ambiguous(self) -> None:
        # REQ-26: each line is exactly one JSON document -- trailing "extra
        # data" after a complete value is never silently dropped.
        with tempfile.TemporaryDirectory() as td:
            line = json.dumps(fake_claude.result_event()) + json.dumps(fake_claude.result_event())
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": line + "\n"})
            self.assertEqual(result.outcome, worker.AMBIGUOUS)


class InterruptedTest(unittest.TestCase):
    def test_hanging_worker_with_short_timeout_classifies_interrupted_and_reaps_group(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            child_pid_file = Path(td) / "child.pid"
            result = _launch(
                cwd=td, timeout=1,
                env_overrides={
                    "FAKE_CLAUDE_HANG": "1",
                    "FAKE_CLAUDE_HANG_CHILD_PID_FILE": str(child_pid_file),
                },
            )
            self.assertEqual(result.outcome, worker.INTERRUPTED)
            self.assertLess(result.returncode, 0)

            # Confirm the whole process group -- including the grandchild
            # the fake worker itself spawned -- was actually reaped, not
            # only the direct child.
            deadline = time.monotonic() + 5
            child_pid = None
            while time.monotonic() < deadline:
                if child_pid_file.exists():
                    child_pid = int(child_pid_file.read_text())
                    break
                time.sleep(0.05)
            self.assertIsNotNone(child_pid, "the fake worker never recorded its grandchild's pid")
            self.assertFalse(
                _pid_alive(child_pid),
                f"grandchild pid {child_pid} is still alive after the timeout's killpg",
            )

    def test_sigterm_ed_worker_classifies_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_SELF_TERM": "1"})
            self.assertEqual(result.outcome, worker.INTERRUPTED)
            self.assertLess(result.returncode, 0)


def _orphan_cmdline_re(argv0: str, seconds: int) -> str:
    """The command lines a supervisor can record for a fake ``bash_bg``
    orphan. It records the one it first sees, and the orphan is owned from
    its fork, so under load that can be a stage before the ``exec -a``: the
    fake's ``bash -c`` (the fork), ``setsid bash -c ...`` or ``bash -c exec
    -a ...`` (adaptive-test-sharding CP5)."""
    stage = re.escape(f"exec -a {argv0} sleep {seconds}")
    return rf"^({re.escape(f'{argv0} {seconds}')}|(setsid )?bash -c .*{stage}.*)$"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class UserOnlyCommandTest(unittest.TestCase):
    def test_task_naming_approve_review_refuses_with_no_process_spawned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "diag.json"
            with self.assertRaises(UserOnlyCommandError) as ctx:
                _launch(
                    task="please run /approve-review plan now",
                    cwd=td, env_overrides={"FAKE_CLAUDE_DIAG_FILE": str(marker)},
                )
            self.assertEqual(ctx.exception.evidence["matched_token"], "approve-review")
            self.assertFalse(marker.exists(), "a worker process was spawned despite the denylist")

    def test_task_naming_accept_milestone_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(UserOnlyCommandError):
                _launch(task="/accept-milestone", cwd=td, env_overrides={})

    def test_task_naming_recover_implementation_provenance_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(UserOnlyCommandError):
                _launch(task="run `/recover-implementation-provenance`.", cwd=td, env_overrides={})

    def test_task_naming_request_plan_amendment_refuses(self) -> None:
        """`USER_ONLY_COMMANDS`'s fourth name (revision 64's baseline
        widening, round 63's `B6`): `request-plan-amendment.md` carries
        only the front-matter `disable-model-invocation: true` flag, not
        the qualified-literal confirmation guard the other three names
        carry, but CP5's own denylist copy refuses it identically -- the
        union CP4's `derive_user_only_commands` derives, not just the
        guard-literal recogniser's own three."""
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "diag.json"
            with self.assertRaises(UserOnlyCommandError) as ctx:
                _launch(
                    task="please run /request-plan-amendment now",
                    cwd=td, env_overrides={"FAKE_CLAUDE_DIAG_FILE": str(marker)},
                )
            self.assertEqual(ctx.exception.evidence["matched_token"], "request-plan-amendment")
            self.assertFalse(marker.exists(), "a worker process was spawned despite the denylist")

    def test_substring_of_user_only_name_is_not_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(
                task="please pre-approve-review-notes for later", cwd=td, env_overrides={},
            )
            self.assertEqual(result.outcome, worker.SUCCESS)


class LaunchMechanicsTest(unittest.TestCase):
    def test_cwd_is_the_target_repository(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td).resolve()
            marker = Path(td) / "diag.json"
            _launch(cwd=target, env_overrides={"FAKE_CLAUDE_DIAG_FILE": str(marker)})
            diag = json.loads(marker.read_text())
            self.assertEqual(Path(diag["cwd"]).resolve(), target)

    def test_stdin_carries_exactly_the_task_line_then_eof_only_at_ending(self) -> None:
        """Worker-lifecycle-ownership CP3 (rewritten from "stdin is
        closed"): stdin holds one stream-json user message carrying the
        task, and reaches EOF only after the supervisor's ``ENDING``."""
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "diag.json"
            states: list = []
            with _environment({"FAKE_CLAUDE_DIAG_FILE": str(marker)}):
                result = worker.launch(
                    "do the bounded thing", cwd=td, permission_mode="acceptEdits", timeout=10,
                    claude_bin=str(FAKE_CLAUDE), **_stream_paths(td),
                    on_state_change=lambda state, details: states.append((time.time(), state)),
                )
            self.assertEqual(result.outcome, worker.SUCCESS)
            diag = json.loads(marker.read_text())
            self.assertEqual(diag["task_message"], {"type": "user", "message": {
                "role": "user", "content": "do the bounded thing"}})
            self.assertFalse(diag["stdin_at_eof"])  # the task line was there
            self.assertEqual(diag["stdin_lines_after_task"], 0)
            ending_at = next(at for at, state in states if state == worker.ENDING)
            self.assertGreaterEqual(diag["stdin_eof_at"], ending_at)

    def test_environment_carries_no_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "diag.json"
            old = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = "/should/not/be/inherited"
            try:
                _launch(cwd=td, env_overrides={"FAKE_CLAUDE_DIAG_FILE": str(marker)})
            finally:
                if old is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = old
            diag = json.loads(marker.read_text())
            self.assertIsNone(diag["pythonpath"])


class RouteArgvTest(unittest.TestCase):
    """``workflow-controller-automatic-lifecycle-orchestration`` CP6: the
    route's ``--model``/``--effort`` and, for a single-agent route only,
    ``--disallowedTools`` naming ``routing.SUBAGENT_TOOLS`` as one
    comma-joined element placed last; never a session-reuse flag."""

    #: Every ``claude`` flag that would resume, continue or fork an earlier
    #: session instead of starting a fresh one.
    SESSION_REUSE_FLAGS = frozenset({"--resume", "-r", "--continue", "-c", "--fork-session", "--session-id"})

    def _argv(self, **route) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "diag.json"
            with _environment({"FAKE_CLAUDE_DIAG_FILE": str(marker)}):
                result = worker.launch(
                    "/review-plan wi-1", cwd=td, permission_mode="auto", timeout=10,
                    claude_bin=str(FAKE_CLAUDE), **_stream_paths(td), **route,
                )
            self.assertEqual(result.outcome, worker.SUCCESS)
            return json.loads(marker.read_text())["argv"]

    #: The streaming-input prefix every worker argv starts with, and the
    #: system note that precedes the disallow list (worker-lifecycle-
    #: ownership CP3, rewritten from the ``-p <task>`` form).
    PREFIX = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
              "--permission-mode", "auto"]
    NOTE = ["--append-system-prompt", worker.WORKER_LIFECYCLE_NOTE]

    def test_without_a_route_the_argv_is_the_streaming_form_with_no_prompt_argument(self) -> None:
        self.assertEqual(self._argv(), self.PREFIX + self.NOTE)
        self.assertEqual(self._argv(model=None, effort=None, disallowed_tools=()), self._argv())
        self.assertNotIn("/review-plan wi-1", self._argv())

    def test_model_effort_the_note_and_the_disallow_list_last(self) -> None:
        argv = self._argv(model="claude-opus-5-5", effort="xhigh", disallowed_tools=routing.SUBAGENT_TOOLS)
        self.assertEqual(argv, self.PREFIX + ["--model", "claude-opus-5-5", "--effort", "xhigh"] + self.NOTE
                         + ["--disallowedTools", "Agent,Workflow,Skill"])

    def test_the_argv_is_the_production_argv_cp1_captured(self) -> None:
        from tests.harness_contract import capture

        route = routing.NO_OVERRIDES.resolve(routing.REVIEW_PLAN)
        argv = self._argv(model=capture.PROBE_MODEL, effort=capture.PROBE_EFFORT,
                          disallowed_tools=routing.worker_disallowed_tools(route, branch_bound=False))
        self.assertEqual(argv, capture.production_argv("claude", single_agent=True)[1:])
        self.assertEqual(worker.WORKER_LIFECYCLE_NOTE, capture.WORKER_LIFECYCLE_NOTE)

    def test_each_flag_is_independent(self) -> None:
        self.assertEqual(self._argv(model="m")[-4:-2], ["--model", "m"])
        self.assertNotIn("--effort", self._argv(model="m"))
        self.assertEqual(self._argv(effort="high")[-4:-2], ["--effort", "high"])
        self.assertNotIn("--model", self._argv(effort="high"))
        self.assertEqual(self._argv(disallowed_tools=["Agent"])[-2:], ["--disallowedTools", "Agent"])

    def test_the_branch_guard_rules_stay_one_last_element(self) -> None:
        # trunk-branch-pr-release-orchestration CP8: Bash rules with a space
        # inside (`Bash(git reset --hard:*)`) are joined like any other name.
        # Worker-lifecycle-ownership CP3: the unownable tools come last, and
        # the system note precedes the list.
        route = routing.NO_OVERRIDES.resolve(routing.REVIEW_IMPLEMENTATION)
        argv = self._argv(disallowed_tools=routing.worker_disallowed_tools(route, branch_bound=True))
        self.assertEqual(argv[-4:], self.NOTE + [
            "--disallowedTools", "Agent,Workflow,Skill,Bash(gh:*),Bash(git push:*),"
            "Bash(git rebase:*),Bash(git switch:*),Bash(git checkout -b:*),"
            "Bash(git reset --hard:*),CronCreate,CronDelete,RemoteTrigger"])

    def test_every_routes_disallow_list_ends_with_the_unownable_tools(self) -> None:
        for role in sorted(routing.ROLES):
            for branch_bound in (False, True):
                with self.subTest(role=role, branch_bound=branch_bound):
                    route = routing.NO_OVERRIDES.resolve(role)
                    tools = routing.worker_disallowed_tools(route, branch_bound=branch_bound)
                    self.assertEqual(tools[-3:], ("CronCreate", "CronDelete", "RemoteTrigger"))
                    self.assertEqual(tools[:-3], route.disallowed_tools
                                     + (routing.BRANCH_GUARD_TOOLS if branch_bound else ()))
                    argv = self._argv(disallowed_tools=tools)
                    self.assertEqual(argv[-4:], self.NOTE + ["--disallowedTools", ",".join(tools)])

    def test_every_resolved_route_reaches_the_argv_with_no_session_reuse_flag(self) -> None:
        option_sets = (
            routing.NO_OVERRIDES,
            routing.RoutingOptions(cli_model="claude-sonnet-5", cli_effort="low"),
            routing.RoutingOptions(cli_role_models={role: "m" for role in routing.ROLES},
                                   cli_role_efforts={role: "max" for role in routing.ROLES}),
        )
        for options in option_sets:
            for role in sorted(routing.ROLES):
                with self.subTest(options=options, role=role):
                    route = options.resolve(role)
                    argv = self._argv(model=route.model, effort=route.effort,
                                      disallowed_tools=route.disallowed_tools)
                    self.assertFalse(self.SESSION_REUSE_FLAGS & set(argv), argv)
                    self.assertEqual("--model" in argv, route.model is not None)
                    self.assertEqual("--effort" in argv, route.effort is not None)
                    if route.model is not None:
                        self.assertEqual(argv[argv.index("--model") + 1], route.model)
                    if route.effort is not None:
                        self.assertEqual(argv[argv.index("--effort") + 1], route.effort)
                    if route.single_agent:
                        self.assertEqual(argv[-2:], ["--disallowedTools", ",".join(routing.SUBAGENT_TOOLS)])
                    else:
                        self.assertNotIn("--disallowedTools", argv)
                    self.assertEqual(argv[:len(self.PREFIX)], self.PREFIX)

    def test_the_launch_source_passes_no_session_reuse_flag(self) -> None:
        """No literal in ``worker.py`` names a session-reuse flag, so no code
        path there can add one."""
        literals = {
            node.value for node in ast.walk(ast.parse(Path(worker.__file__).read_text()))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertFalse(self.SESSION_REUSE_FLAGS & literals)


class WorkerLaunchErrorTest(unittest.TestCase):
    def test_nonexistent_claude_bin_raises_worker_launch_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no-such-binary"
            with self.assertRaises(WorkerLaunchError):
                worker.launch(
                    "do the thing", cwd=td, permission_mode="acceptEdits", timeout=5,
                    claude_bin=str(missing), **_stream_paths(td),
                )


# ---------------------------------------------------------------------------
# workflow-controller-automatic-lifecycle-orchestration CP5: the worker's
# process identity (`on_spawn`, `WorkerProcess`, `pass_fds`), `timeout=None`,
# and the boot-keyed, zombie-aware liveness verdict
# (docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md,
# "Concurrency and worker lifecycle" and CP5's Tests list; round 1's O1/O4 of
# the manual external plan review).
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _environment(overrides: dict):
    old = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _SpawnedWorkerCase(unittest.TestCase):
    """A scratch directory, a release file the fake polls for
    (``FAKE_CLAUDE_HANG_UNTIL_FILE``), and a cleanup -- registered before
    any spawn -- that releases the fake and kills every worker group
    ``on_spawn`` saw, so no ``tests/fake_claude.py`` survives a test."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cp5-worker-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.release = self.dir / "release"
        self.diag = self.dir / "diag.json"
        self.streams = _stream_paths(self.dir)
        self.spawned_groups: list[int] = []
        self.addCleanup(self._end_spawned)

    def _end_spawned(self) -> None:
        self.release.touch()
        for pgid in self.spawned_groups:
            process_fixtures.kill_group(pgid)

    def _launch(self, *, on_spawn=None, pass_fds=(), timeout=30, env=None,
                on_group_drain=None) -> worker.WorkerResult:
        overrides = {
            "FAKE_CLAUDE_DIAG_FILE": str(self.diag),
            "FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release),
            **(env or {}),
        }

        def recording_on_spawn(worker_process: worker.WorkerProcess, **_new_keywords) -> None:
            # Worker-lifecycle-ownership CP3: `launch` also passes `anchor`
            # and `ownership_tag`; each test's own `on_spawn` sees the
            # process alone.
            self.spawned_groups.append(worker_process.pgid or worker_process.pid)
            if on_spawn is not None:
                on_spawn(worker_process)

        with _environment(overrides):
            return worker.launch(
                "do the bounded thing", cwd=self.dir, permission_mode="acceptEdits", timeout=timeout,
                claude_bin=str(FAKE_CLAUDE), pass_fds=pass_fds, on_spawn=recording_on_spawn,
                on_group_drain=on_group_drain, **self.streams,
            )

    def _safety_release(self, seconds: float) -> None:
        """Release the fake after ``seconds`` whatever happens, so a wrong
        ``launch`` cannot hang the suite."""
        timer = threading.Timer(seconds, self.release.touch)
        timer.daemon = True
        self.addCleanup(timer.cancel)
        timer.start()

    def _assert_group_killed_and_reaped(self, worker_process: worker.WorkerProcess) -> None:
        self.assertIsNone(process_fixtures.read_stat(worker_process.pid),
                          "the worker is still present (running, or an unreaped zombie)")
        with self.assertRaises(ProcessLookupError):
            os.killpg(worker_process.pgid, 0)
        self.assertFalse(self.release.exists(), "the worker was released instead of killed")


class LaunchOnSpawnTest(_SpawnedWorkerCase):
    def test_on_spawn_receives_the_worker_process_before_the_wait(self) -> None:
        self._safety_release(20)
        passed_fd = os.open(self.dir, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, passed_fd)
        self.assertFalse(os.get_inheritable(passed_fd))  # only pass_fds can hand it on
        seen: dict = {}

        def on_spawn(worker_process: worker.WorkerProcess) -> None:
            seen["released_at_spawn"] = self.release.exists()
            seen["stat"] = process_fixtures.read_stat(worker_process.pid)
            seen["context"] = worker.read_process_context()
            seen["worker_process"] = worker_process
            self.release.touch()  # only now can the worker finish

        result = self._launch(on_spawn=on_spawn, pass_fds=(passed_fd,))
        self.assertEqual(result.outcome, worker.SUCCESS)

        worker_process = seen["worker_process"]
        self.assertIsInstance(worker_process, worker.WorkerProcess)
        self.assertFalse(seen["released_at_spawn"], "on_spawn ran after the worker could already finish")
        diag = json.loads(self.diag.read_text())
        self.assertEqual(worker_process.pid, diag["pid"])
        self.assertEqual(worker_process.pgid, worker_process.pid)
        self.assertEqual(diag["pgid"], worker_process.pid)
        self.assertIsNotNone(seen["stat"], "the worker was already gone inside on_spawn")
        self.assertEqual(worker_process.start_ticks, seen["stat"][2])
        for field in worker.CONTEXT_FIELDS:
            self.assertEqual(getattr(worker_process, field), seen["context"][field], field)
        self.assertEqual(set(worker_process.to_dict()),
                         {"pid", "pgid", "start_ticks", *worker.CONTEXT_FIELDS})
        self.assertIn(passed_fd, diag["fds"], "the pass_fds descriptor is not open in the worker")

    def test_capture_worker_process_reads_the_real_identity(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        captured = worker.capture_worker_process(sleeper.pid)
        state, pgrp, start_ticks = process_fixtures.read_stat(sleeper.pid)
        self.assertEqual((captured.pid, captured.pgid, captured.start_ticks), (sleeper.pid, pgrp, start_ticks))
        self.assertEqual({field: getattr(captured, field) for field in worker.CONTEXT_FIELDS},
                         worker.read_process_context())

    def test_capture_worker_process_has_no_start_ticks_where_proc_does_not_number_this_namespace(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        fake = _FakeProc(self, own_namespace=False)
        with fake.patch():
            captured = worker.capture_worker_process(sleeper.pid)
        self.assertEqual(captured.pgid, sleeper.pid)
        self.assertIsNone(captured.start_ticks)

    def test_read_process_context_fields(self) -> None:
        context = worker.read_process_context()
        self.assertEqual(set(context), set(worker.CONTEXT_FIELDS))
        self.assertEqual(context["pid_namespace"], lock.read_pid_namespace())

    def test_on_spawn_oserror_propagates_unchanged_after_the_group_is_killed_and_reaped(self) -> None:
        self._safety_release(20)
        failure = OSError(errno.ENOSPC, "No space left on device (the worker_process flush)")
        seen: dict = {}

        def on_spawn(worker_process: worker.WorkerProcess) -> None:
            seen["worker_process"] = worker_process
            raise failure

        with self.assertRaises(Exception) as ctx:
            self._launch(on_spawn=on_spawn)
        self.assertIs(ctx.exception, failure)
        self.assertNotIsInstance(ctx.exception, WorkerLaunchError)
        self._assert_group_killed_and_reaped(seen["worker_process"])

    def test_on_spawn_keyboard_interrupt_propagates_unchanged_after_the_group_is_killed_and_reaped(self) -> None:
        self._safety_release(20)
        interrupt = KeyboardInterrupt()
        seen: dict = {}

        def on_spawn(worker_process: worker.WorkerProcess) -> None:
            seen["worker_process"] = worker_process
            raise interrupt

        with self.assertRaises(KeyboardInterrupt) as ctx:
            self._launch(on_spawn=on_spawn)
        self.assertIs(ctx.exception, interrupt)
        self._assert_group_killed_and_reaped(seen["worker_process"])

    def test_on_spawn_failure_kills_the_whole_group_including_a_descendant(self) -> None:
        child_pid_file = self.dir / "child.pid"
        seen: dict = {}

        def on_spawn(worker_process: worker.WorkerProcess) -> None:
            seen["worker_process"] = worker_process
            if not process_fixtures.wait_until(lambda: child_pid_file.exists() and child_pid_file.read_text()):
                raise AssertionError("the fake worker never spawned its descendant")
            seen["child"] = int(child_pid_file.read_text())
            raise OSError(errno.EIO, "flush failed")

        with self.assertRaises(OSError):
            self._launch(on_spawn=on_spawn, env={
                "FAKE_CLAUDE_HANG_UNTIL_FILE": "", "FAKE_CLAUDE_HANG": "1",
                "FAKE_CLAUDE_HANG_CHILD_PID_FILE": str(child_pid_file),
            })
        worker_process = seen["worker_process"]
        self.assertIsNone(process_fixtures.read_stat(worker_process.pid))
        # The descendant was SIGKILLed with the group; its new parent reaps it.
        self.assertTrue(process_fixtures.wait_until(lambda: process_fixtures.read_stat(seen["child"]) is None))
        with self.assertRaises(ProcessLookupError):
            os.killpg(worker_process.pgid, 0)


#: A limit the old default played the role of (it was 3600 s); the test's
#: worker is silent for longer than this.
_FORMER_DEFAULT_TIMEOUT = 0.3


class TimeoutNoneTest(_SpawnedWorkerCase):
    def test_timeout_none_waits_for_a_worker_silent_longer_than_any_former_default(self) -> None:
        silence = 1.0
        timer = threading.Timer(silence, self.release.touch)
        timer.daemon = True
        self.addCleanup(timer.cancel)
        with unittest.mock.patch.object(
            worker, "_kill_process_group",
            side_effect=AssertionError("timeout=None must never reach a kill path"),
        ) as killer:
            started = time.monotonic()
            timer.start()
            result = self._launch(timeout=None)
            elapsed = time.monotonic() - started
        killer.assert_not_called()
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(result.returncode, 0)
        self.assertGreater(elapsed, _FORMER_DEFAULT_TIMEOUT)
        self.assertGreaterEqual(elapsed, silence * 0.9)

    def test_control_the_same_silent_worker_under_the_former_limit_is_interrupted(self) -> None:
        self._safety_release(20)
        result = self._launch(timeout=_FORMER_DEFAULT_TIMEOUT)
        self.assertEqual(result.outcome, worker.INTERRUPTED)
        self.assertLess(result.returncode, 0)


# ---------------------------------------------------------------------------
# workflow-controller-release-runtime-observability CP4: streaming worker
# output (docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md,
# "Streaming worker output" and CP4's Tests list).
# ---------------------------------------------------------------------------

GOLDEN_STREAM = Path(__file__).resolve().parent / "golden" / "claude_stream_json_2.1.281.jsonl"


def _lines(*events) -> str:
    return "".join((event if isinstance(event, str) else json.dumps(event)) + "\n" for event in events)


class StreamParseTest(unittest.TestCase):
    """The print-mode terminal classification (``controller.worker_stream``,
    which replaced ``_parse_worker_stream``/``_classify`` in
    workflow-controller-worker-lifecycle-ownership CP2), over stream text."""

    def _classify(self, stdout: str, returncode: int | None = 0, *, timed_out: bool = False):
        return worker_stream.classify(
            stdout, mode=worker_stream.PRINT,
            facts=worker_stream.SupervisorFacts(timed_out=timed_out), returncode=returncode,
        )

    def _classified(self, stdout: str, returncode: int | None = 0, *, timed_out: bool = False) -> str:
        return self._classify(stdout, returncode, timed_out=timed_out)[0]

    def test_a_well_formed_stream_returns_its_result_event_and_is_success(self) -> None:
        result = fake_claude.result_event()
        stdout = fake_claude.stream_text([*fake_claude.default_events()[:-1], result])
        outcome, terminal, diagnosis = self._classify(stdout)
        self.assertEqual(terminal, result)
        self.assertEqual(outcome, worker.SUCCESS)
        self.assertEqual(diagnosis["reason"], "quiescent_terminal_turn")

    def test_is_error_true_is_failure(self) -> None:
        self.assertEqual(self._classified(_lines(fake_claude.result_event(is_error=True))), worker.FAILURE)

    def test_a_non_zero_exit_is_failure(self) -> None:
        self.assertEqual(self._classified(_lines(fake_claude.result_event()), 1), worker.FAILURE)

    def test_undecidable_streams_are_ambiguous(self) -> None:
        init = fake_claude.default_events()[0]
        result = fake_claude.result_event()
        no_session = fake_claude.result_event()
        del no_session["session_id"]
        cases = {
            "no result event": (_lines(init), "no_result"),
            "a result that is not last (the stream ends mid-turn)": (_lines(init, result, init), "exited_mid_turn"),
            "a non-JSON line": (_lines(init, "not json", result), "malformed_line"),
            "a blank interior line": (_lines(init, "", result), "malformed_line"),
            "a non-object line": (_lines(init, "[1, 2]", result), "malformed_line"),
            "a result missing session_id": (_lines(init, no_session), "result_incomplete"),
            "an empty stream": ("", "no_result"),
            "two final empty segments": (_lines(init, result) + "\n", "malformed_line"),
        }
        for name, (stdout, reason) in cases.items():
            with self.subTest(name):
                outcome, _, diagnosis = self._classify(stdout)
                self.assertEqual(outcome, worker.AMBIGUOUS)
                self.assertEqual(diagnosis["reason"], reason)

    def test_several_result_events_are_accepted_and_the_last_one_is_used(self) -> None:
        """Formerly pinned ``AMBIGUOUS`` ("exactly one result"); a
        multi-turn session is legitimate (D2, job ``2857a730``)."""
        init = fake_claude.default_events()[0]
        first, last = fake_claude.result_event(result="one"), fake_claude.result_event(result="two")
        outcome, terminal, diagnosis = self._classify(_lines(init, first, last))
        self.assertEqual(outcome, worker.SUCCESS)
        self.assertEqual(terminal, last)
        self.assertEqual(diagnosis["result_count"], 2)

    def test_crlf_lines_and_a_missing_final_newline_are_accepted(self) -> None:
        init, result = fake_claude.default_events()[0], fake_claude.result_event()
        for stdout in (json.dumps(init) + "\r\n" + json.dumps(result) + "\r\n",
                       json.dumps(init) + "\n" + json.dumps(result)):
            with self.subTest(stdout=stdout[-3:]):
                outcome, terminal, _ = self._classify(stdout)
                self.assertEqual(terminal, result)
                self.assertEqual(outcome, worker.SUCCESS)

    def test_a_signal_is_interrupted(self) -> None:
        self.assertEqual(self._classified(_lines(fake_claude.result_event()), -9), worker.INTERRUPTED)

    def test_timed_out_is_interrupted_even_for_a_clean_exit_with_a_well_formed_stream(self) -> None:
        stdout = _lines(fake_claude.result_event())
        self.assertEqual(self._classified(stdout, 0, timed_out=True), worker.INTERRUPTED)
        self.assertEqual(self._classified(stdout, 1, timed_out=True), worker.INTERRUPTED)

    def test_timed_out_false_leaves_the_table_unchanged(self) -> None:
        """An unknown exit status (``None``, a re-attach only) skips the
        exit-status rows; it no longer reads as a signal."""
        clean = _lines(fake_claude.result_event())
        rows = [
            (0, clean, worker.SUCCESS),
            (0, _lines(fake_claude.result_event(is_error=True)), worker.FAILURE),
            (2, clean, worker.FAILURE),
            (0, "", worker.AMBIGUOUS),
            (0, _lines({"type": "result", "is_error": False}), worker.AMBIGUOUS),
            (-15, "", worker.INTERRUPTED),
            (None, "", worker.AMBIGUOUS),
            (None, clean, worker.SUCCESS),
        ]
        for returncode, stdout, expected in rows:
            with self.subTest(returncode=returncode, stdout=stdout):
                self.assertEqual(self._classified(stdout, returncode), expected)

    def test_the_result_fields_equal_the_former_single_json_bodys(self) -> None:
        """``raw_json`` is the result event, and ``_worker_dict`` reads the
        same values the ``--output-format json`` body carried."""
        from controller import job

        event = fake_claude.result_event(
            session_id="abc", terminal_reason="completed", stop_reason="end_turn", result="done",
            num_turns=4, permission_denials=[{"tool_name": "Bash"}], total_cost_usd=0.5, duration_ms=9,
        )
        former_body = {key: value for key, value in event.items() if key != "type"}

        def worker_dict(parsed: dict) -> dict:
            result = worker.WorkerResult(
                outcome=worker.SUCCESS, returncode=0, stdout="", stderr="",
                raw_json=parsed, **worker._extract_fields(parsed),
            )
            return job._worker_dict(result, stdout_path="o", stderr_path="e")

        _, parsed, _ = self._classify(_lines(fake_claude.default_events()[0], event))
        self.assertEqual(parsed, event)
        self.assertEqual(worker_dict(parsed), worker_dict(former_body))


class GoldenTranscriptTest(unittest.TestCase):
    """A real ``claude -p --output-format stream-json --verbose``
    transcript (``tests/golden/claude_stream_json_2.1.281.md``)."""

    def test_the_real_cli_transcript_parses_to_its_result_event_and_is_success(self) -> None:
        text = GOLDEN_STREAM.read_text()
        last = json.loads(text.splitlines()[-1])
        outcome, parsed, _ = worker_stream.classify(text, mode=worker_stream.PRINT, returncode=0)
        self.assertEqual(parsed, last)
        self.assertEqual(parsed["type"], "result")
        self.assertEqual(outcome, worker.SUCCESS)
        fields = worker._extract_fields(parsed)
        self.assertEqual(fields["session_id"], "00000000-0000-4000-8000-000000000001")
        self.assertIs(fields["is_error"], False)
        self.assertEqual(fields["num_turns"], 1)
        self.assertEqual(fields["result"], "ok")


class StreamFilesTest(_SpawnedWorkerCase):
    """The worker writes its own stdout/stderr into the caller's files."""

    def test_bytes_reach_stdout_path_while_the_worker_is_still_running(self) -> None:
        self._safety_release(20)
        outcome: dict = {}
        env = {"FAKE_CLAUDE_HANG_UNTIL_FILE": "",
               "FAKE_CLAUDE_PAUSE_AFTER_EVENTS_FILE": f"2:{self.release}"}

        def run() -> None:
            outcome["result"] = self._launch(env=env)

        # The environment is set by `_launch` inside the thread only: a
        # second, overlapping `_environment` here would restore out of order.
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 30)
        stdout_path = Path(self.streams["stdout_path"])
        self.assertTrue(process_fixtures.wait_until(
            lambda: stdout_path.read_text().count("\n") >= 2), "no event reached the file")
        self.assertTrue(thread.is_alive(), "launch returned before the worker was released")
        self.assertTrue(self.spawned_groups and process_fixtures.group_has_running_member(self.spawned_groups[0]))
        partial = stdout_path.read_text()
        self.release.touch()
        thread.join(30)
        result = outcome["result"]
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(result.stdout, stdout_path.read_text())
        lines = result.stdout.splitlines(keepends=True)
        self.assertEqual(len(lines), len(fake_claude.default_events()))
        self.assertEqual(partial, "".join(lines[:2]))

    def test_stderr_reaches_stderr_path(self) -> None:
        result = self._launch(env={"FAKE_CLAUDE_HANG_UNTIL_FILE": "", "FAKE_CLAUDE_STDERR": "a warning\n"})
        self.assertEqual(Path(self.streams["stderr_path"]).read_text(), "a warning\n")
        self.assertEqual(result.stderr, "a warning\n")
        self.assertEqual(result.stdout, Path(self.streams["stdout_path"]).read_text())

    def test_the_fake_refuses_stream_json_without_verbose_like_the_real_cli(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(FAKE_CLAUDE), "-p", "x", "--output-format", "stream-json"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr.strip(), fake_claude.STREAM_JSON_REQUIRES_VERBOSE)
        self.assertEqual(completed.stdout, "")

    def test_an_unopenable_stream_file_is_worker_launch_error_with_no_worker(self) -> None:
        marker = self.dir / "diag-missing.json"
        with self.assertRaises(WorkerLaunchError):
            with _environment({"FAKE_CLAUDE_DIAG_FILE": str(marker)}):
                worker.launch("x", cwd=self.dir, permission_mode="auto", timeout=10,
                              claude_bin=str(FAKE_CLAUDE), stdout_path=self.dir / "no" / "such",
                              stderr_path=self.streams["stderr_path"])
        self.assertFalse(marker.exists())


class GroupDrainTest(_SpawnedWorkerCase):
    """Control returns when the direct child has exited and its process
    group is empty (``FAKE_CLAUDE_DESCENDANT``)."""

    def setUp(self) -> None:
        super().setUp()
        self.descendant = self.dir / "descendant.json"
        self.addCleanup(self._end_descendant)

    def _end_descendant(self) -> None:
        try:
            pid = json.loads(self.descendant.read_text())["pid"]
        except (OSError, ValueError, KeyError):
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)

    def _env(self, spec: str) -> dict:
        return {"FAKE_CLAUDE_HANG_UNTIL_FILE": "", "FAKE_CLAUDE_DESCENDANT": spec,
                "FAKE_CLAUDE_DESCENDANT_FILE": str(self.descendant)}

    def _descendant(self) -> dict:
        return json.loads(self.descendant.read_text())

    def _assert_gone(self, pid: int) -> None:
        def gone() -> bool:
            stat = process_fixtures.read_stat(pid)
            return stat is None or stat[0] == "Z"
        self.assertTrue(process_fixtures.wait_until(gone, timeout=5), f"descendant {pid} is still running")

    def test_a_same_group_descendant_holding_stdout_is_waited_for(self) -> None:
        drains: list = []
        result = self._launch(env=self._env("group:2"), on_group_drain=lambda *a: drains.append(a))
        returned_at = time.time()
        record = self._descendant()
        self.assertIn("exited_at", record, "launch returned before the descendant exited")
        self.assertLessEqual(record["exited_at"], returned_at)
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(len(drains), 1)
        pid, remaining = drains[0]
        self.assertEqual(pid, self.spawned_groups[0])
        self.assertEqual(remaining, [record["pid"]])

    def test_a_same_group_descendant_that_closed_its_streams_is_still_waited_for(self) -> None:
        drains: list = []
        result = self._launch(env=self._env("group-closed:2"), on_group_drain=lambda *a: drains.append(a))
        returned_at = time.time()
        record = self._descendant()
        self.assertIn("exited_at", record)
        self.assertLessEqual(record["exited_at"], returned_at)
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual([remaining for _pid, remaining in drains], [[record["pid"]]])

    def test_a_setsid_descendant_is_owned_and_waited_for(self) -> None:
        """Worker-lifecycle-ownership CP3 (rewritten from "is not waited
        for", D4): a descendant that left the group is owned by the tag and
        waited for. ``on_group_drain`` is still never called: the group
        itself is empty."""
        drains: list = []
        result = self._launch(env=self._env("setsid:3"), on_group_drain=lambda *a: drains.append(a))
        returned_at = time.time()
        record = self._descendant()
        self.assertIn("exited_at", record, "launch returned before the setsid descendant exited")
        self.assertLessEqual(record["exited_at"], returned_at)
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(drains, [])

    def test_with_no_descendant_on_group_drain_is_never_called(self) -> None:
        drains: list = []
        result = self._launch(env={"FAKE_CLAUDE_HANG_UNTIL_FILE": ""}, on_group_drain=lambda *a: drains.append(a))
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(drains, [])

    def test_a_timeout_during_the_drain_kills_the_group_without_getpgid(self) -> None:
        drain_started = threading.Event()
        getpgid_after_phase_1: list = []
        real_getpgid = os.getpgid

        def spy_getpgid(pid):
            if drain_started.is_set():
                getpgid_after_phase_1.append(pid)
            return real_getpgid(pid)

        kills: list = []
        real_kill_drained = worker._kill_drained_group

        def spy_kill_drained(pgid):
            kills.append(pgid)
            real_kill_drained(pgid)

        with unittest.mock.patch.object(worker.os, "getpgid", spy_getpgid), \
                unittest.mock.patch.object(worker, "_kill_drained_group", spy_kill_drained):
            started = time.monotonic()
            result = self._launch(env=self._env("group-closed:30"), timeout=3,
                                  on_group_drain=lambda *a: drain_started.set())
            elapsed = time.monotonic() - started
        self.assertTrue(drain_started.is_set())
        self.assertEqual(getpgid_after_phase_1, [])
        self.assertEqual(kills, [self.spawned_groups[0]])
        self.assertLess(elapsed, 3 + worker._DRAIN_KILL_SETTLE_SECONDS + 2)
        self.assertEqual(result.outcome, worker.INTERRUPTED)
        self.assertEqual(result.returncode, 0)
        self._assert_gone(self._descendant()["pid"])
        self.assertNotIn("exited_at", self._descendant())

    def test_under_the_killpg_form_the_drain_waits_until_no_such_group(self) -> None:
        answers: list = []
        real_killpg = worker._killpg

        def spy_killpg(pgid, sig):
            try:
                real_killpg(pgid, sig)
            except ProcessLookupError:
                answers.append((sig, "no such group"))
                raise
            answers.append((sig, "delivered"))

        drains: list = []
        with unittest.mock.patch.object(worker, "_proc_numbers_own_namespace", return_value=False), \
                unittest.mock.patch.object(worker, "_killpg", spy_killpg):
            result = self._launch(env=self._env("group:1"), on_group_drain=lambda *a: drains.append(a))
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual([remaining for _pid, remaining in drains], [[]])
        self.assertIn((0, "delivered"), answers)
        self.assertEqual(answers[-1], (0, "no such group"))
        self.assertIn("exited_at", self._descendant())

    def test_on_group_drain_raising_ends_the_group_and_propagates(self) -> None:
        failure = OSError(errno.EIO, "drain flush failed")

        def on_group_drain(pid, remaining):
            raise failure

        with self.assertRaises(OSError) as ctx:
            self._launch(env=self._env("group-closed:30"), on_group_drain=on_group_drain)
        self.assertIs(ctx.exception, failure)
        self._assert_gone(self._descendant()["pid"])
        self.assertNotIn("exited_at", self._descendant())


# ---------------------------------------------------------------------------
# Fixture /proc trees for the process test.
# ---------------------------------------------------------------------------

#: A ``comm`` with spaces, a ``)`` and decoy fields: only parsing after the
#: line's *last* ``)`` reads the real state, pgrp and starttime (a
#: first-``)`` parser would read state ``Z`` and fail on ``(x``).
_TRAP_COMM = "claude) Z 1 (x y"


def _stat_line(pid: int, *, state: str, pgrp: int, start_ticks: int, comm: str = _TRAP_COMM) -> str:
    """A ``/proc/<pid>/stat`` line: field 3 state, field 5 pgrp, field 22
    starttime, and a few more fields after it."""
    after = [
        state, "1", str(pgrp), str(pgrp), "0", "-1", "4194560", "0", "0", "0", "0",
        "3", "1", "0", "0", "20", "0", "1", "0", str(start_ticks), "1000000", "300",
    ]
    return f"{pid} ({comm}) " + " ".join(after) + "\n"


class _FakeProc:
    """A fixture ``/proc``: ``self`` names this process (``own_namespace``)
    or not, and ``stat`` files are written per process and thread.
    ``patch()`` points ``worker._proc_root`` at it."""

    def __init__(self, test_case: unittest.TestCase, *, own_namespace: bool = True) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="cp5-fake-proc-"))
        self._locked: list[Path] = []
        test_case.addCleanup(self._cleanup)
        os.symlink(str(os.getpid()) if own_namespace else "1", self.root / "self")

    def process(self, pid: int, *, state: str, pgrp: int, start_ticks: int = 100,
                threads: dict[int, str] | None = None) -> "_FakeProc":
        (self.root / str(pid)).mkdir(exist_ok=True)
        (self.root / str(pid) / "stat").write_text(_stat_line(pid, state=state, pgrp=pgrp, start_ticks=start_ticks))
        for tid, thread_state in (threads or {}).items():
            task = self.root / str(pid) / "task" / str(tid)
            task.mkdir(parents=True, exist_ok=True)
            (task / "stat").write_text(_stat_line(tid, state=thread_state, pgrp=pgrp, start_ticks=start_ticks))
        return self

    def write(self, rel_path: str, text: str) -> None:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def lock_out(self, rel_path: str) -> None:
        """``chmod 000``: present, but unreadable (and unlistable)."""
        path = self.root / rel_path
        os.chmod(path, 0)
        self._locked.append(path)

    def patch(self):
        return unittest.mock.patch.object(worker, "_proc_root", return_value=self.root)

    def _cleanup(self) -> None:
        for path in self._locked:
            os.chmod(path, 0o700)
        shutil.rmtree(self.root, ignore_errors=True)


def _skip_if_root(test_case: unittest.TestCase) -> None:
    if os.geteuid() == 0:
        test_case.skipTest("running as root: chmod 000 does not make a file unreadable")


def _forbid(name: str):
    return unittest.mock.patch.object(worker, name, side_effect=AssertionError(f"worker.{name} must not be called"))


def _dead_group(test_case: unittest.TestCase) -> int:
    """The pid (and pgid) of a process that has exited and been reaped."""
    spawned: list[subprocess.Popen] = []

    def _cleanup() -> None:
        for proc in spawned:
            if proc.returncode is None:  # only if the wait below never finished
                process_fixtures.kill_group(proc.pid)
                proc.wait(timeout=10)

    test_case.addCleanup(_cleanup)
    proc = subprocess.Popen([sys.executable, "-c", ""], start_new_session=True)
    spawned.append(proc)
    proc.wait(timeout=30)
    with test_case.assertRaises(ProcessLookupError):
        os.killpg(proc.pid, 0)
    return proc.pid


def _zombie_group(test_case: unittest.TestCase, mode: str) -> dict:
    """``process_fixtures.ZombieGroup(test_case, mode).info`` (its release,
    registered as ``test_case``'s cleanup, also removes its scratch
    directory)."""
    return process_fixtures.ZombieGroup(test_case, mode).info


#: A leader in its own session that forks a running member of its group,
#: prints the member's pid, and exits when a line arrives on stdin.
_LEADER_WITH_RUNNING_MEMBER = r'''
import os, sys, time
member = os.fork()
if member == 0:
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    time.sleep(3600)
    os._exit(0)
print(member, flush=True)
sys.stdin.readline()
'''


# ---------------------------------------------------------------------------
# Liveness: the process test with real processes.
# ---------------------------------------------------------------------------


class LivenessProcessTestRealTest(unittest.TestCase):
    def test_recorded_pid_with_different_start_ticks_reads_inactive_although_killpg_succeeds(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        start_ticks = process_fixtures.read_stat(sleeper.pid)[2]
        os.killpg(sleeper.pid, 0)  # the premise: killpg alone would say "possibly live"
        recorded = process_fixtures.worker_process_dict(sleeper.pid, start_ticks=start_ticks + 1)
        self.assertEqual(worker.classify_worker_liveness(recorded), worker.INACTIVE)
        assessment = worker.assess_worker_liveness(recorded)
        self.assertEqual(assessment.process, worker.ProcessAnswer(worker.NOT_LIVE, "leader_pid_reused"))
        # The control: the real start_ticks is the running leader.
        matched = worker.assess_worker_liveness(process_fixtures.worker_process_dict(sleeper.pid))
        self.assertEqual(matched.verdict, worker.ACTIVE)
        self.assertEqual(matched.process.basis, "leader_running")

    def test_exited_leader_with_a_running_member_reads_active_by_member_scan(self) -> None:
        spawned: list[subprocess.Popen] = []
        members: list[int] = []

        def _cleanup() -> None:
            for proc in spawned:
                process_fixtures.kill_group(proc.pid)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                for stream in (proc.stdin, proc.stdout):
                    try:
                        stream.close()
                    except OSError:
                        pass
            for member in members:
                process_fixtures.wait_until(lambda: process_fixtures.read_stat(member) is None, timeout=10)

        self.addCleanup(_cleanup)
        leader = subprocess.Popen([sys.executable, "-c", _LEADER_WITH_RUNNING_MEMBER], start_new_session=True,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        spawned.append(leader)
        member = int(leader.stdout.readline())
        members.append(member)
        leader_ticks = process_fixtures.read_stat(leader.pid)[2]
        leader.stdin.write("\n")
        leader.stdin.flush()
        leader.wait(timeout=10)  # the leader has exited and been reaped

        self.assertIsNone(process_fixtures.read_stat(leader.pid))
        state, pgrp, _ = process_fixtures.read_stat(member)
        self.assertEqual(pgrp, leader.pid)
        self.assertNotIn(state, ("Z", "X"))

        recorded = process_fixtures.worker_process_dict(leader.pid, start_ticks=leader_ticks)
        assessment = worker.assess_worker_liveness(recorded)
        self.assertEqual(assessment.verdict, worker.ACTIVE)
        self.assertEqual(assessment.process, worker.ProcessAnswer(worker.LIVE, "member_scan", (member,)))
        self.assertEqual(worker.classify_worker_liveness(recorded), worker.ACTIVE)

    def test_null_start_ticks_falls_to_the_member_scan_where_proc_is_available(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        recorded = process_fixtures.worker_process_dict(sleeper.pid, start_ticks=None)
        assessment = worker.assess_worker_liveness(recorded)
        self.assertEqual(assessment.verdict, worker.ACTIVE)
        self.assertEqual(assessment.process, worker.ProcessAnswer(worker.LIVE, "member_scan", (sleeper.pid,)))

    def test_null_start_ticks_falls_to_the_killpg_form_where_proc_is_unavailable(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        dead = _dead_group(self)
        fake = _FakeProc(self, own_namespace=False)
        with fake.patch():
            live = worker.assess_worker_liveness(process_fixtures.worker_process_dict(sleeper.pid, start_ticks=None))
            gone = worker.assess_worker_liveness(process_fixtures.worker_process_dict(dead, start_ticks=None))
        self.assertEqual(live.verdict, worker.UNVERIFIABLE)
        self.assertNotEqual(live.verdict, worker.ACTIVE)
        self.assertEqual(live.process, worker.ProcessAnswer(worker.POSSIBLY_LIVE, "killpg_possibly_live"))
        self.assertEqual(gone.verdict, worker.INACTIVE)
        self.assertEqual(gone.process, worker.ProcessAnswer(worker.NOT_LIVE, "killpg_no_such_group"))

    def test_proc_whose_self_link_names_another_pid_makes_the_proc_form_unavailable(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        fake = _FakeProc(self, own_namespace=False)
        # Even a fixture entry that would say "running" is not consulted.
        fake.process(sleeper.pid, state="S", pgrp=sleeper.pid, start_ticks=100)
        with fake.patch():
            self.assertIsNone(worker._proc_form(sleeper.pid, sleeper.pid, 100))
            answer = worker.process_test(sleeper.pid, sleeper.pid, 100)
        self.assertEqual(answer, worker.ProcessAnswer(worker.POSSIBLY_LIVE, "killpg_possibly_live"))

    def test_reused_pid_with_patched_proc_reads_inactive(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)  # a live group: killpg would succeed
        fake = _FakeProc(self).process(sleeper.pid, state="S", pgrp=sleeper.pid, start_ticks=5000)
        with fake.patch(), _forbid("_killpg"):
            reused = worker.assess_worker_liveness(
                process_fixtures.worker_process_dict(sleeper.pid, start_ticks=4999))
            matched = worker.assess_worker_liveness(
                process_fixtures.worker_process_dict(sleeper.pid, start_ticks=5000))
        self.assertEqual(reused.verdict, worker.INACTIVE)
        self.assertEqual(reused.process, worker.ProcessAnswer(worker.NOT_LIVE, "leader_pid_reused"))
        # Last-')' parsing read state S and starttime 5000 past the trap comm.
        self.assertEqual(matched.verdict, worker.ACTIVE)
        self.assertEqual(matched.process, worker.ProcessAnswer(worker.LIVE, "leader_running"))


# ---------------------------------------------------------------------------
# Liveness: zombies.
# ---------------------------------------------------------------------------


class LivenessZombieTest(unittest.TestCase):
    def test_unreaped_zombie_leader_is_inactive(self) -> None:
        info = _zombie_group(self, "leader")
        state, pgrp, start_ticks = process_fixtures.read_stat(info["pid"])
        self.assertEqual(state, "Z")
        self.assertEqual((pgrp, start_ticks), (info["pgid"], info["start_ticks"]))
        os.killpg(info["pgid"], 0)  # the premise: killpg succeeds on a zombie
        recorded = process_fixtures.worker_process_dict(info["pid"], start_ticks=info["start_ticks"])
        self.assertEqual(worker.classify_worker_liveness(recorded), worker.INACTIVE)
        self.assertEqual(worker.assess_worker_liveness(recorded).process.basis, "no_running_member")

    def test_group_whose_only_survivor_is_a_zombie_member_is_inactive(self) -> None:
        info = _zombie_group(self, "member")
        self.assertIsNone(process_fixtures.read_stat(info["pid"]), "the leader was not reaped")
        state, pgrp, _ = process_fixtures.read_stat(info["member_pid"])
        self.assertEqual(state, "Z")
        self.assertEqual(pgrp, info["pgid"])
        os.killpg(info["pgid"], 0)  # the premise: killpg succeeds on the zombie member
        recorded = process_fixtures.worker_process_dict(info["pid"], start_ticks=info["start_ticks"])
        self.assertEqual(worker.classify_worker_liveness(recorded), worker.INACTIVE)
        self.assertEqual(worker.assess_worker_liveness(recorded).process.basis, "no_running_member")

    def test_zombie_group_with_the_proc_form_unavailable_is_possibly_live_so_unverifiable(self) -> None:
        info = _zombie_group(self, "leader")
        fake = _FakeProc(self, own_namespace=False)
        recorded = process_fixtures.worker_process_dict(info["pid"], start_ticks=info["start_ticks"])
        with fake.patch(), unittest.mock.patch.object(worker, "_killpg", wraps=worker._killpg) as killpg:
            assessment = worker.assess_worker_liveness(recorded)
        killpg.assert_called_once_with(info["pgid"], 0)
        self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
        self.assertNotEqual(assessment.verdict, worker.ACTIVE)
        self.assertEqual(assessment.process, worker.ProcessAnswer(worker.POSSIBLY_LIVE, "killpg_possibly_live"))

    # -- patched /proc -----------------------------------------------------

    def setUp(self) -> None:
        self.pid = 3999001
        self.member = 3999002

    def _process_test(self, fake: _FakeProc, *, start_ticks: int | None = 100, killpg=None):
        """The process test over ``fake``; ``_killpg`` must not run unless a
        behaviour for it is given."""
        killpg_patch = _forbid("_killpg") if killpg is None else \
            unittest.mock.patch.object(worker, "_killpg", side_effect=killpg)
        with fake.patch(), killpg_patch:
            return worker.process_test(self.pid, self.pid, start_ticks)

    def test_leader_in_state_x_reads_not_running(self) -> None:
        for dead_state in ("X", "x"):
            with self.subTest(state=dead_state):
                fake = _FakeProc(self).process(self.pid, state=dead_state, pgrp=self.pid)
                self.assertEqual(self._process_test(fake),
                                 worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"))
                fake.process(self.pid, state=dead_state, pgrp=self.pid, threads={self.pid: dead_state})
                self.assertEqual(self._process_test(fake),
                                 worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"))

    def test_zombie_leader_with_a_running_member_reads_live(self) -> None:
        fake = _FakeProc(self).process(self.pid, state="Z", pgrp=self.pid, threads={self.pid: "Z"})
        fake.process(self.member, state="S", pgrp=self.pid, start_ticks=200, threads={self.member: "S"})
        fake.process(3999003, state="R", pgrp=4242, start_ticks=300)  # another group
        self.assertEqual(self._process_test(fake),
                         worker.ProcessAnswer(worker.LIVE, "member_scan", (self.member,)))

    def test_zombie_leader_with_only_zombie_members_reads_not_live(self) -> None:
        fake = _FakeProc(self).process(self.pid, state="Z", pgrp=self.pid, threads={self.pid: "Z"})
        fake.process(self.member, state="Z", pgrp=self.pid, start_ticks=200, threads={self.member: "Z"})
        self.assertEqual(self._process_test(fake), worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"))

    def test_unreadable_proc_listing_falls_to_the_killpg_form(self) -> None:
        fake = _FakeProc(self)  # no leader entry: the member scan must list the root
        real_listdir = os.listdir

        def listdir(path="."):
            if Path(path) == fake.root:
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return real_listdir(path)

        for killpg, expected in (
            (ProcessLookupError(errno.ESRCH, "No such process"),
             worker.ProcessAnswer(worker.NOT_LIVE, "killpg_no_such_group")),
            (lambda pgid, sig: None, worker.ProcessAnswer(worker.POSSIBLY_LIVE, "killpg_possibly_live")),
        ):
            with self.subTest(expected=expected.basis), \
                    unittest.mock.patch.object(worker.os, "listdir", side_effect=listdir):
                self.assertEqual(self._process_test(fake, killpg=killpg), expected)

    def test_unparseable_stat_line_falls_to_the_killpg_form(self) -> None:
        layouts = {
            "leader without a closing paren": {f"{self.pid}/stat": "3999001 (claude S 1 3999001\n"},
            "leader with too few fields": {f"{self.pid}/stat": "3999001 (claude) S 1 3999001\n"},
            "member with a non-integer pgrp": {
                f"{self.member}/stat": _stat_line(self.member, state="S", pgrp=self.pid, start_ticks=1)
                .replace(f" {self.pid} {self.pid} ", " pgrp pgrp ", 1),
            },
        }
        for name, files in layouts.items():
            for killpg, expected in (
                (ProcessLookupError(errno.ESRCH, "No such process"),
                 worker.ProcessAnswer(worker.NOT_LIVE, "killpg_no_such_group")),
                (lambda pgid, sig: None, worker.ProcessAnswer(worker.POSSIBLY_LIVE, "killpg_possibly_live")),
            ):
                with self.subTest(layout=name, expected=expected.basis):
                    fake = _FakeProc(self)
                    for rel_path, text in files.items():
                        fake.write(rel_path, text)
                    self.assertEqual(self._process_test(fake, killpg=killpg), expected)

    def test_unparseable_stat_or_unreadable_listing_is_unverifiable_in_the_verdict(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        fake = _FakeProc(self)
        fake.write(f"{sleeper.pid}/stat", "garbage\n")
        recorded = process_fixtures.worker_process_dict(sleeper.pid, start_ticks=None)
        with fake.patch():
            assessment = worker.assess_worker_liveness(recorded)
        self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
        self.assertEqual(assessment.process.basis, "killpg_possibly_live")

    # -- round 1's O1: threads, fail-closed reads, the confirmation rescan --

    def test_zombie_process_state_with_a_running_thread_reads_live(self) -> None:
        fake = _FakeProc(self).process(self.pid, state="Z", pgrp=self.pid,
                                       threads={self.pid: "Z", self.pid + 7: "S"})
        self.assertEqual(self._process_test(fake), worker.ProcessAnswer(worker.LIVE, "leader_running"))
        # The same thread under a member whose leader record carries no start_ticks.
        self.assertEqual(self._process_test(fake, start_ticks=None),
                         worker.ProcessAnswer(worker.LIVE, "member_scan", (self.pid,)))

    def test_zombie_process_state_with_a_running_thread_is_active_in_the_verdict(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        fake = _FakeProc(self).process(sleeper.pid, state="Z", pgrp=sleeper.pid, start_ticks=100,
                                       threads={sleeper.pid: "Z", sleeper.pid + 1: "R"})
        with fake.patch(), _forbid("_killpg"):
            assessment = worker.assess_worker_liveness(
                process_fixtures.worker_process_dict(sleeper.pid, start_ticks=100))
        self.assertEqual(assessment.verdict, worker.ACTIVE)
        self.assertEqual(assessment.process.basis, "leader_running")

    def test_unreadable_stat_fails_closed_to_the_killpg_form(self) -> None:
        _skip_if_root(self)
        layouts = {
            "a thread of the zombie leader": lambda fake: (
                fake.process(self.pid, state="Z", pgrp=self.pid, threads={self.pid: "Z", self.pid + 1: "S"}),
                fake.lock_out(f"{self.pid}/task/{self.pid + 1}/stat")),
            "the zombie leader's task directory": lambda fake: (
                fake.process(self.pid, state="Z", pgrp=self.pid, threads={self.pid: "Z"}),
                fake.lock_out(f"{self.pid}/task")),
            "a candidate member": lambda fake: (
                fake.process(self.member, state="S", pgrp=self.pid, start_ticks=200),
                fake.lock_out(f"{self.member}/stat")),
            "the leader itself": lambda fake: (
                fake.process(self.pid, state="S", pgrp=self.pid),
                fake.lock_out(f"{self.pid}/stat")),
        }
        for name, build in layouts.items():
            with self.subTest(layout=name):
                fake = _FakeProc(self)
                build(fake)
                with fake.patch():
                    self.assertIsNone(worker._proc_form(self.pid, self.pid, 100))
                # Skipped instead of failing closed, each would read "not live" (inactive).
                self.assertEqual(self._process_test(fake, killpg=lambda pgid, sig: None),
                                 worker.ProcessAnswer(worker.POSSIBLY_LIVE, "killpg_possibly_live"))

    def test_a_vanished_entry_is_skipped_not_failed(self) -> None:
        # A task directory that is absent (the process exited) is "not running", not "no answer".
        fake = _FakeProc(self).process(self.pid, state="Z", pgrp=self.pid)
        self.assertEqual(self._process_test(fake), worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"))

    def test_not_live_from_the_member_scan_is_confirmed_by_one_rescan(self) -> None:
        fake = _FakeProc(self)
        cases = (
            ([worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"),
              worker.ProcessAnswer(worker.LIVE, "member_scan", (123,))],
             worker.ProcessAnswer(worker.LIVE, "member_scan", (123,)), 2),
            ([worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"),
              worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member")],
             worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"), 2),
            ([worker.ProcessAnswer(worker.NOT_LIVE, "no_running_member"), worker._NoAnswer("unreadable")],
             None, 2),
            ([worker.ProcessAnswer(worker.LIVE, "leader_running")],
             worker.ProcessAnswer(worker.LIVE, "leader_running"), 1),
            ([worker.ProcessAnswer(worker.NOT_LIVE, "leader_pid_reused")],
             worker.ProcessAnswer(worker.NOT_LIVE, "leader_pid_reused"), 1),
        )
        for side_effect, expected, calls in cases:
            with self.subTest(first=side_effect[0].basis if isinstance(side_effect[0], worker.ProcessAnswer) else None,
                              expected=expected), \
                    fake.patch(), \
                    unittest.mock.patch.object(worker, "_proc_scan", side_effect=side_effect) as scan:
                self.assertEqual(worker._proc_form(123, 123, None), expected)
                self.assertEqual(scan.call_count, calls)
                scan.assert_called_with(fake.root, 123, 123, None)


# ---------------------------------------------------------------------------
# Liveness: the boot-keyed verdict.
# ---------------------------------------------------------------------------

_INIT_NS = "pid:[4026531836]"
_OTHER_NS = "pid:[4026532862]"


def _context(*, boot="boot-a", ns=_INIT_NS, host="host-a", machine="m-a") -> dict:
    return {"boot_id": boot, "pid_namespace": ns, "hostname": host, "machine_id": machine}


class LivenessBootKeyedVerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.live = process_fixtures.spawn_sleeper(self).pid  # a real group, live in this boot
        os.killpg(self.live, 0)
        self.dead = _dead_group(self)

    def _assess(self, pid: int, recorded: dict, current: dict, *,
                process_test_allowed: bool = True) -> worker.LivenessAssessment:
        worker_process = process_fixtures.worker_process_dict(pid, context=recorded)
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.object(worker, "read_process_context", return_value=current))
            if not process_test_allowed:
                stack.enter_context(_forbid("process_test"))
            assessment = worker.assess_worker_liveness(worker_process)
            self.assertEqual(worker.classify_worker_liveness(worker_process), assessment.verdict)
        return assessment

    def test_another_boot_of_the_same_host_is_inactive(self) -> None:
        assessment = self._assess(self.live, _context(boot="boot-a"), _context(boot="boot-b"),
                                  process_test_allowed=False)
        self.assertEqual(assessment.verdict, worker.INACTIVE)
        self.assertEqual(assessment.row, "other_boot_same_host")
        self.assertIsNone(assessment.process)

    def test_unreadable_boot_id_runs_the_process_test(self) -> None:
        for name, recorded, current in (
            ("recorded null", _context(boot=None), _context(boot="boot-b")),
            ("unreadable now", _context(boot="boot-a"), _context(boot=None)),
            ("both unread", _context(boot=None), _context(boot=None)),
        ):
            with self.subTest(case=name):
                dead = self._assess(self.dead, recorded, current)
                self.assertEqual(dead.verdict, worker.INACTIVE)
                self.assertEqual(dead.row, "no_boot_identity")
                self.assertEqual(dead.process.answer, worker.NOT_LIVE)
                live = self._assess(self.live, recorded, current)
                self.assertEqual(live.verdict, worker.UNVERIFIABLE)
                self.assertEqual(live.row, "no_boot_identity")
                self.assertEqual(live.process.answer, worker.LIVE)

    def test_another_host_is_unverifiable(self) -> None:
        for pid in (self.live, self.dead):
            with self.subTest(pid=pid):
                assessment = self._assess(pid, _context(boot="boot-a", host="host-a", machine="m-a"),
                                          _context(boot="boot-b", host="host-b", machine="m-b"),
                                          process_test_allowed=False)
                self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
                self.assertEqual(assessment.row, "other_host")
                self.assertIsNone(assessment.process)

    def test_another_boot_with_equal_hostname_but_unestablished_machine_id_is_unverifiable(self) -> None:
        for name, recorded, current in (
            ("different machine_id", _context(boot="boot-a", machine="m-a"), _context(boot="boot-b", machine="m-b")),
            ("machine_id recorded null", _context(boot="boot-a", machine=None), _context(boot="boot-b")),
            ("machine_id unreadable now", _context(boot="boot-a"), _context(boot="boot-b", machine=None)),
            ("machine_id unread on both sides", _context(boot="boot-a", machine=None),
             _context(boot="boot-b", machine=None)),
        ):
            with self.subTest(case=name):
                assessment = self._assess(self.live, recorded, current, process_test_allowed=False)
                self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
                self.assertNotEqual(assessment.verdict, worker.INACTIVE)
                self.assertEqual(assessment.row, "other_host")

    def test_no_boot_identity_on_an_unestablished_host_is_unverifiable_without_the_process_test(self) -> None:
        for name, recorded, current in (
            ("different machine_id", _context(boot=None, machine="m-a"), _context(boot="boot-b", machine="m-b")),
            ("different hostname", _context(boot=None, host="host-a"), _context(boot=None, host="host-b")),
            ("machine_id unread", _context(boot="boot-a", machine=None), _context(boot=None, machine=None)),
        ):
            with self.subTest(case=name):
                # A dead group: had the process test run, it would read inactive.
                assessment = self._assess(self.dead, recorded, current, process_test_allowed=False)
                self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
                self.assertEqual(assessment.row, "no_boot_identity")
                self.assertIsNone(assessment.process)

    def test_same_boot_with_a_different_hostname_lets_the_process_test_decide(self) -> None:
        for name, current_machine in (("same machine_id", "m-a"), ("different machine_id", "m-b"),
                                      ("machine_id unread", None)):
            with self.subTest(case=name):
                recorded = _context(boot="boot-a", host="host-a", machine="m-a")
                current = _context(boot="boot-a", host="host-b", machine=current_machine)
                live = self._assess(self.live, recorded, current)
                self.assertEqual(live.verdict, worker.ACTIVE)
                self.assertEqual(live.row, "same_boot")
                self.assertEqual(live.process, worker.ProcessAnswer(worker.LIVE, "leader_running"))
                dead = self._assess(self.dead, recorded, current)
                self.assertEqual(dead.verdict, worker.INACTIVE)
                self.assertEqual(dead.row, "same_boot")

    def test_same_boot_with_a_different_or_unreadable_pid_namespace_is_unverifiable(self) -> None:
        for name, recorded_ns, current_ns in (
            ("different", _OTHER_NS, _INIT_NS),
            ("recorded unread", None, _INIT_NS),
            ("unreadable now", _INIT_NS, None),
            ("unread on both sides", None, None),
        ):
            with self.subTest(case=name):
                assessment = self._assess(self.live, _context(boot="boot-a", ns=recorded_ns),
                                          _context(boot="boot-a", ns=current_ns), process_test_allowed=False)
                self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
                self.assertEqual(assessment.row, "same_boot")

    def test_no_boot_identity_with_a_one_sided_or_different_pid_namespace_is_unverifiable(self) -> None:
        for name, recorded_ns, current_ns in (
            ("recorded unread", None, _INIT_NS),
            ("unreadable now", _INIT_NS, None),
            ("both read and different", _OTHER_NS, _INIT_NS),
        ):
            with self.subTest(case=name):
                # A dead group: had the process test run, it would read inactive.
                assessment = self._assess(self.dead, _context(boot=None, ns=recorded_ns),
                                          _context(boot="boot-b", ns=current_ns), process_test_allowed=False)
                self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
                self.assertEqual(assessment.row, "no_boot_identity")
        # Both unread: the process test runs.
        both_unread = self._assess(self.dead, _context(boot=None, ns=None), _context(boot=None, ns=None))
        self.assertEqual(both_unread.verdict, worker.INACTIVE)
        self.assertEqual(both_unread.process.answer, worker.NOT_LIVE)

    def test_malformed_worker_process_is_unverifiable(self) -> None:
        base = {"pid": self.live, "pgid": self.live, "start_ticks": None, **_context()}
        for name, worker_process in (
            ("pid null", {**base, "pid": None}),
            ("empty mapping", {}),
            ("pgid null", {**base, "pgid": None}),
            ("pid is a bool", {**base, "pid": True}),
            ("pgid 1", {**base, "pgid": 1}),
            ("pgid a string", {**base, "pgid": str(self.live)}),
            ("start_ticks a string", {**base, "start_ticks": "100"}),
            ("start_ticks a bool", {**base, "start_ticks": True}),
        ):
            with self.subTest(case=name), \
                    unittest.mock.patch.object(worker, "read_process_context", return_value=_context()), \
                    _forbid("process_test"):
                assessment = worker.assess_worker_liveness(worker_process)
                self.assertEqual(assessment.verdict, worker.UNVERIFIABLE)
                self.assertEqual(assessment.row, "malformed")
                self.assertEqual(worker.classify_worker_liveness(worker_process), worker.UNVERIFIABLE)

    def test_assessment_to_dict_carries_the_verdict_and_both_contexts(self) -> None:
        recorded_context, current_context = _context(), _context(host="host-b")
        start_ticks = process_fixtures.read_stat(self.live)[2]
        assessment = self._assess(self.live, recorded_context, current_context)
        as_dict = assessment.to_dict()
        self.assertEqual(as_dict["verdict"], worker.ACTIVE)
        self.assertEqual(as_dict["row"], "same_boot")
        self.assertEqual(as_dict["recorded"],
                         {"pid": self.live, "pgid": self.live, "start_ticks": start_ticks, **recorded_context})
        self.assertEqual(as_dict["current"], current_context)
        self.assertEqual(as_dict["process_answer"], worker.LIVE)
        self.assertEqual(as_dict["process_basis"], "leader_running")
        self.assertEqual(as_dict["members"], [])
        self.assertIsInstance(as_dict["reason"], str)
        json.dumps(as_dict)  # JSON-serialisable, as a job record needs

        other_host = self._assess(self.live, _context(), _context(boot="boot-b", host="host-b", machine="m-b"),
                                  process_test_allowed=False).to_dict()
        self.assertEqual(other_host["verdict"], worker.UNVERIFIABLE)
        self.assertIsNone(other_host["process_answer"])
        self.assertIsNone(other_host["process_basis"])
        self.assertEqual(other_host["members"], [])
        self.assertEqual(other_host["current"]["hostname"], "host-b")


# ---------------------------------------------------------------------------
# workflow-controller-worker-lifecycle-ownership CP3: streaming-input launch
# and supervision (docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md,
# designs A and C, and CP3's Tests list), against the CP1 fake.
# ---------------------------------------------------------------------------


def _running(pid: int) -> bool:
    stat = process_fixtures.read_stat(pid)
    return stat is not None and stat[0] not in ("Z", "X", "x")


def _fd_targets(pid: int) -> set[str]:
    targets = set()
    try:
        names = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return targets
    for name in names:
        with contextlib.suppress(OSError):
            targets.add(os.readlink(f"/proc/{pid}/fd/{name}"))
    return targets


def _environ(pid: int) -> bytes:
    with open(f"/proc/{pid}/environ", "rb") as fh:
        return fh.read()


def _ownership_tags(environ: bytes) -> list[str]:
    prefix = f"{worker.OWNERSHIP_VAR}=".encode()
    return [item[len(prefix):].decode() for item in environ.split(b"\0") if item.startswith(prefix)]


class _SupervisedCase(unittest.TestCase):
    """A scratch directory, a ``launch`` wrapper that records every
    ``on_state_change`` as ``(time.time(), state, details)``, and a cleanup
    that ends whatever the launch left: its tagged processes, the worker and
    the anchor (identity-checked), and any extra pid a test registers."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cp3-supervise-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.diag = self.dir / "diag.json"
        self.log: list[tuple[float, str, dict]] = []
        self.spawn: dict = {}
        self.spawns: list[dict] = []
        self.extra_pids: list[int] = []
        self.addCleanup(self._end_leftovers)

    def _end_leftovers(self) -> None:
        pids = list(self.extra_pids)
        for spawn in self.spawns:
            if spawn.get("tag"):
                pids += process_fixtures.tagged_pids(spawn["tag"])
            for key in ("worker", "anchor"):
                process = spawn.get(key)
                if process is not None and process_fixtures._identity_matches(process.to_dict()):
                    pids.append(process.pid)
        for pid in dict.fromkeys(pids):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        for pid in dict.fromkeys(pids):
            # Reaps a pid that is this process's child (a detached anchor,
            # an adopted escapee); any other pid is ChildProcessError.
            with contextlib.suppress(ChildProcessError, OSError):
                os.waitpid(pid, 0)

    def patch(self, target, name: str, value) -> None:
        self.enterContext(unittest.mock.patch.object(target, name, value))

    def launch(self, turns=None, *, env=None, timeout=60, on_state=None, **kwargs):
        overrides = {"FAKE_CLAUDE_DIAG_FILE": str(self.diag)}
        if turns is not None:
            overrides["FAKE_CLAUDE_TURNS"] = json.dumps(turns)
        overrides.update(env or {})

        def on_spawn(worker_process, *, anchor=None, ownership_tag=None) -> None:
            self.spawn = {"worker": worker_process, "anchor": anchor, "tag": ownership_tag}
            self.spawns.append(self.spawn)

        def on_state_change(state: str, details: dict) -> None:
            self.log.append((time.time(), state, json.loads(json.dumps(details))))
            if on_state is not None:
                on_state(state, details)

        self.started = time.time()
        with _environment(overrides):
            result = worker.launch(
                "do the bounded thing", cwd=self.dir, permission_mode="acceptEdits", timeout=timeout,
                claude_bin=str(FAKE_CLAUDE), on_spawn=on_spawn, on_state_change=on_state_change,
                **_stream_paths(self.dir), **kwargs,
            )
        self.returned = time.time()
        return result

    def states(self) -> list[str]:
        states: list[str] = []
        for _at, state, _details in self.log:
            if not states or states[-1] != state:
                states.append(state)
        return states

    def at(self, state: str) -> float:
        return next(at for at, logged, _details in self.log if logged == state)

    def details(self, state: str) -> list[dict]:
        return [details for _at, logged, details in self.log if logged == state]

    def ending(self) -> dict:
        [details] = self.details(worker.ENDING)
        return details

    def diagnosis(self, result) -> dict:
        self.assertIsInstance(result, worker.WorkerResult)
        return result.stream_diagnosis

    def anomalies(self, result) -> list[str]:
        return [a["kind"] for a in self.diagnosis(result)["command_lifecycle_anomalies"]]


class AnchorTest(_SupervisedCase):
    """The stdin anchor (plan A): it holds the pipe and the lock, releases
    stdin at ``ENDING``, keeps the lock through ``DRAINING``, is ended and
    reaped at ``ENDED`` and on a timeout, runs with an empty environment,
    and ends itself only as an orphan with nothing left to hold for."""

    def test_the_anchor_holds_the_pipe_and_the_lock_and_releases_only_stdin_at_ending(self) -> None:
        repo = process_fixtures.scratch_git_repo(self)
        held = lock.acquire_lifecycle_lock(repo)
        self.addCleanup(held.release)
        seen: dict = {}

        def on_state(state: str, details: dict) -> None:
            fds = _fd_targets(self.spawn["anchor"].pid)
            seen.setdefault(state, fds)
            if state == worker.DRAINING and "probe" not in seen:
                # The Controller's own copy goes; the worker has exited and
                # the descendant inherited no descriptor (H6), so only the
                # anchor can still hold the lock.
                held.release()
                seen["probe"] = lock.probe_lifecycle_lock(repo)

        descendant = self.dir / "descendant.json"
        result = self.launch(env={"FAKE_CLAUDE_DESCENDANT": "group-closed:2",
                                  "FAKE_CLAUDE_DESCENDANT_FILE": str(descendant)},
                             pass_fds=(held.fd,), on_state=on_state)
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(self.states(), [worker.RUNNING, worker.ENDING, worker.DRAINING, worker.ENDED])
        self.assertTrue(any(target.startswith("pipe:") for target in seen[worker.RUNNING]))
        self.assertIn(str(held.path), seen[worker.RUNNING])
        self.assertFalse(any(target.startswith("pipe:") for target in seen[worker.DRAINING]),
                         "the anchor still holds stdin after ENDING")
        self.assertIn(str(held.path), seen[worker.DRAINING])
        self.assertEqual(seen["probe"], lock.HELD)
        anchor_process = self.spawn["anchor"]
        self.assertIsNone(process_fixtures.read_stat(anchor_process.pid), "the anchor is alive or a zombie")
        lock.acquire_lifecycle_lock(repo).release()  # nothing holds it any more
        self.assertIn("exited_at", json.loads(descendant.read_text()))

    def test_after_a_timeout_the_anchor_is_neither_alive_nor_a_zombie(self) -> None:
        result = self.launch(env={"FAKE_CLAUDE_HANG": "1"}, timeout=1)
        self.assertEqual(result.outcome, worker.INTERRUPTED)
        self.assertIsNone(process_fixtures.read_stat(self.spawn["anchor"].pid))

    def test_the_anchor_environment_is_empty_under_an_inherited_ownership_tag(self) -> None:
        seen: dict = {}

        def on_state(state: str, details: dict) -> None:
            if "anchor" not in seen:
                seen["anchor"] = _environ(self.spawn["anchor"].pid)
                seen["worker"] = _environ(self.spawn["worker"].pid)

        result = self.launch(env={worker.OWNERSHIP_VAR: "outer-job"}, on_state=on_state)
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(seen["anchor"], b"")
        self.assertEqual(_ownership_tags(seen["worker"]), [f"outer-job:{self.spawn['tag']}"])
        self.assertTrue(self.spawn["tag"].startswith("launch-"))

    def test_an_orphaned_anchor_ends_itself_only_with_nothing_owned_and_no_supervisor(self) -> None:
        gone = subprocess.Popen(["true"])
        self.assertTrue(process_fixtures.wait_until(
            lambda: (process_fixtures.read_stat(gone.pid) or ("",))[0] == "Z"))
        gone_ticks = process_fixtures.read_stat(gone.pid)[2]  # read while it is an unreaped zombie
        gone.wait()
        lock_path = self.dir / "supervisor.lock"
        supervisor = os.open(lock_path, os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, supervisor)
        fcntl.flock(supervisor, fcntl.LOCK_EX)
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        tag = f"cp3-orphan-{secrets.token_hex(4)}"
        proc = subprocess.Popen(
            anchor.command(write_end, gone.pid, gone_ticks, tag, str(lock_path), poll=0.1, orphan_seconds=1.0),
            pass_fds=(write_end,), env={}, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.close(write_end)
        self.extra_pids.append(proc.pid)
        self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait()))

        # SIGUSR1 releases stdin, and only stdin: the reader sees EOF.
        self.assertTrue(process_fixtures.wait_until(lambda: worker._catches_sigusr1(proc.pid)))
        proc.send_signal(signal.SIGUSR1)
        ready, _, _ = select.select([read_end], [], [], 10)
        self.assertEqual(ready, [read_end])
        self.assertEqual(os.read(read_end, 1), b"")
        # A repeated request (a re-attached supervisor's) is a no-op: it
        # never closes whatever descriptor now has the old number.
        proc.send_signal(signal.SIGUSR1)

        time.sleep(2.0)
        self.assertIsNone(proc.poll(), "the anchor ended while a supervisor was attached")
        sleeper = subprocess.Popen(["sleep", "60"], env={**os.environ, worker.OWNERSHIP_VAR: f"other:{tag}"})
        self.extra_pids.append(sleeper.pid)
        fcntl.flock(supervisor, fcntl.LOCK_UN)
        time.sleep(2.0)
        self.assertIsNone(proc.poll(), "the anchor ended while a tagged process lived")
        sleeper.kill()
        sleeper.wait()
        released_at = time.monotonic()
        self.assertTrue(process_fixtures.wait_until(lambda: proc.poll() is not None, timeout=10))
        self.assertGreaterEqual(time.monotonic() - released_at, 0.9)
        self.assertEqual(proc.returncode, 0)

    def test_a_tagged_recognised_daemon_never_keeps_an_orphaned_anchor_alive(self) -> None:
        # The anchor's orphan rule and every ownership reader share one
        # daemon list and one matcher: they cannot drift.
        self.assertIs(worker.RECOGNISED_DAEMONS, anchor.RECOGNISED_DAEMONS)
        self.assertIs(worker.daemon_pattern, anchor.daemon_pattern)
        gone = subprocess.Popen(["true"])
        gone.wait()
        tag = f"cp3-daemon-{secrets.token_hex(4)}"
        env = {**os.environ, worker.OWNERSHIP_VAR: f"other:{tag}"}
        missing_lock = str(self.dir / "no-supervisor.lock")
        daemon = subprocess.Popen(["bash", "-c", "exec -a gpg-agent sleep 60"], env=env)
        self.extra_pids.append(daemon.pid)
        self.addCleanup(lambda: daemon.poll() is None and (daemon.kill(), daemon.wait()))
        self.assertTrue(process_fixtures.wait_until(
            lambda: anchor.daemon_pattern(anchor._cmdline(daemon.pid)) == "gpg-agent"))
        self.assertFalse(anchor.tagged_process_alive(tag))
        self.assertTrue(anchor.orphaned(gone.pid, None, tag, missing_lock))
        # An ordinary tagged process still keeps it alive.
        sleeper = subprocess.Popen(["sleep", "60"], env=env)
        self.extra_pids.append(sleeper.pid)
        self.addCleanup(lambda: sleeper.poll() is None and (sleeper.kill(), sleeper.wait()))
        self.assertTrue(process_fixtures.wait_until(lambda: anchor.tagged_process_alive(tag)))
        self.assertFalse(anchor.orphaned(gone.pid, None, tag, missing_lock))


class SupervisionTest(_SupervisedCase):
    """``RUNNING``/``WAITING``/``ENDING``/``ENDED`` from the stream (plan C,
    step 2), wakeup settlement, the anchor's loss and the overdue rule."""

    def test_a_background_task_keeps_the_worker_waiting_until_its_completion_turn(self) -> None:
        result = self.launch([[{"step": "bash_bg", "id": "b1", "seconds": 1, "description": "suite"}],
                              [{"step": "text", "text": "finished"}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(self.states(), [worker.RUNNING, worker.WAITING, worker.RUNNING, worker.ENDING,
                                         worker.ENDED])
        waiting = self.details(worker.WAITING)[0]["waiting_on"]
        self.assertEqual(waiting["tasks"], [{"task_id": "b1", "description": "suite"}])
        self.assertEqual(self.diagnosis(result)["turns"], 2)
        self.assertEqual(self.diagnosis(result)["reason"], "quiescent_terminal_turn")

    def test_a_monitor_runs_one_turn_per_tick_and_one_at_its_end(self) -> None:
        result = self.launch([[{"step": "monitor", "id": "m1", "ticks": 3, "interval": 0.4}],
                              *[[{"step": "text", "text": f"tick {n}"}] for n in range(4)]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(self.states(), [worker.RUNNING, worker.WAITING] * 4
                         + [worker.RUNNING, worker.ENDING, worker.ENDED])

    def test_a_wakeup_whose_fire_turn_stops_it_settles_by_the_stop_with_no_anomaly(self) -> None:
        result = self.launch([[{"step": "wakeup", "delay": 1, "fire_turn": [
            {"step": "text", "text": "woken"}, {"step": "wakeup_stop"}]}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(self.states(), [worker.RUNNING, worker.WAITING, worker.RUNNING, worker.ENDING,
                                         worker.ENDED])
        diagnosis = self.diagnosis(result)
        [bracket] = diagnosis["command_lifecycles"]
        [wakeup] = diagnosis["wakeups_seen"]
        self.assertEqual((wakeup["state"], wakeup["settled_by"]), ("settled", "stop"))
        self.assertEqual((wakeup["cancelled_wakeups"], wakeup["expected_count"]), (0, 0))
        self.assertEqual(wakeup["command_uuid"], bracket["command_uuid"])
        self.assertEqual(diagnosis["command_lifecycle_anomalies"], [])

    def test_a_fallback_wakeup_cancelled_from_the_fire_turn_counts_one(self) -> None:
        result = self.launch([[
            {"step": "wakeup", "delay": 1200},
            {"step": "wakeup", "delay": 1, "fire_turn": [{"step": "text"}, {"step": "wakeup_stop"}]},
        ]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        diagnosis = self.diagnosis(result)
        [stop] = diagnosis["wakeup_stops"]
        self.assertEqual((stop["cancelled_wakeups"], stop["expected_count"]), (1, 1))
        self.assertEqual(diagnosis["command_lifecycle_anomalies"], [])

    def test_a_matched_wakeup_with_no_stop_settles_after_the_window(self) -> None:
        self.patch(worker, "WAKEUP_SETTLE_SECONDS", 1)
        result = self.launch([[{"step": "wakeup", "delay": 1}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(self.states(), [worker.RUNNING, worker.WAITING, worker.RUNNING, worker.WAITING,
                                         worker.ENDING, worker.ENDED])
        diagnosis = self.diagnosis(result)
        [bracket] = diagnosis["command_lifecycles"]
        matched = [(at, details) for at, state, details in self.log if state == worker.WAITING
                   and any(w["state"] == "fire_matched" for w in details["waiting_on"]["wakeups"])]
        self.assertTrue(matched, "no WAITING flush named the fire_matched wakeup")
        matched_at, details = matched[0]
        [entry] = details["waiting_on"]["wakeups"]
        self.assertEqual(entry["command_uuid"], bracket["command_uuid"])
        self.assertIsNotNone(entry["settle_seconds_left"])
        self.assertGreaterEqual(self.at(worker.ENDING) - matched_at, 0.9)
        [wakeup] = diagnosis["wakeups_seen"]
        self.assertEqual((wakeup["settled_by"], wakeup["command_uuid"]), ("settle_window", bracket["command_uuid"]))
        self.assertEqual(self.ending()["supervisor_facts"]["settled_wakeups"], [wakeup["tool_use_id"]])

    def test_a_fallback_wakeup_cancelled_before_the_final_turn_ends_the_session_promptly(self) -> None:
        result = self.launch([[{"step": "wakeup", "delay": 1200}, {"step": "bash_bg", "seconds": 0.5}],
                              [{"step": "wakeup_stop"}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertLess(self.returned - self.started, 10)

    def test_without_the_stop_the_worker_waits_for_the_wakeup(self) -> None:
        self.patch(worker, "WAKEUP_SETTLE_SECONDS", 0.3)
        result = self.launch([[{"step": "wakeup", "delay": 2}, {"step": "bash_bg", "seconds": 0.3}],
                              [{"step": "text", "text": "task done"}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        after_task = self.details(worker.WAITING)[-1]["waiting_on"]["wakeups"]
        self.assertTrue(after_task)
        self.assertGreaterEqual(self.at(worker.ENDING) - self.started, 2.0)

    def test_an_anchor_killed_while_waiting_is_stdin_closed_while_waiting(self) -> None:
        def on_state(state: str, details: dict) -> None:
            if state == worker.WAITING:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(self.spawn["anchor"].pid, signal.SIGKILL)

        result = self.launch([[{"step": "bash_bg", "seconds": 5}], [{"step": "text"}]], on_state=on_state)
        self.assertEqual(result.outcome, worker.AMBIGUOUS)
        self.assertEqual(self.diagnosis(result)["reason"], "stdin_closed_while_waiting")
        self.assertNotIn(worker.ENDING, self.states())

    def test_an_overdue_wakeup_ends_the_session_as_not_delivered(self) -> None:
        self.patch(worker, "WAKEUP_GRACE_SECONDS", 1)
        result = self.launch([[{"step": "lifecycle_fault", "kind": "delay_fire", "seconds": 30},
                               {"step": "wakeup", "delay": 1}]])
        self.assertEqual(result.outcome, worker.AMBIGUOUS)
        self.assertEqual(self.diagnosis(result)["reason"], "wakeup_not_delivered")
        self.assertIsNotNone(self.ending()["supervisor_facts"]["wakeup_overdue_declared_at"])
        self.assertLess(self.returned - self.started, 15)


class QuiescenceConfirmationTest(unittest.TestCase):
    """The harness opens an already-queued notification turn a few
    milliseconds after a ``queued_turn_count: 0`` ``result`` (CP1's
    fixtures), so ``ENDING`` waits out a confirmation window: the longer
    one exactly where the fixtures show that gap."""

    CONTRACT = Path(__file__).resolve().parent / "harness_contract"

    def _stream_through(self, fixture: str, line: int) -> worker_stream.WorkerStream:
        lines = (self.CONTRACT / f"{fixture}.jsonl").read_text().splitlines(keepends=True)
        stream = worker_stream.WorkerStream()
        stream.feed("".join(lines[:line + 1]).encode())
        return stream

    def test_the_measured_gaps_get_the_notification_window(self) -> None:
        # (fixture, the last line before the queued turn's system/init, that init's line)
        for fixture, before, init in (("p3_streaming_background_bash", 15, 16),
                                      ("p11_subagent_handback", 17, 18),
                                      ("p8_monitor_timeout", 20, 21)):
            with self.subTest(fixture=fixture):
                gap = self._stream_through(fixture, before)
                self.assertTrue(gap.quiescent(), "the gap is not quiescent: nothing to confirm")
                self.assertEqual(worker.quiescence_confirm_seconds(gap), worker._NOTIFICATION_CONFIRM_SECONDS)
                arrivals = json.loads((self.CONTRACT / f"{fixture}.meta.json").read_text())["line_arrival_seconds"]
                self.assertLess(arrivals[init] - arrivals[before], worker._QUIESCENCE_CONFIRM_SECONDS)
                self.assertFalse(self._stream_through(fixture, init).quiescent())

    def test_a_quiescent_turn_with_no_task_just_ended_gets_the_short_window(self) -> None:
        stream = self._stream_through("p3_streaming_background_bash", 21)  # the continuation's result
        self.assertTrue(stream.quiescent())
        self.assertEqual(worker.quiescence_confirm_seconds(stream), worker._QUIESCENCE_CONFIRM_SECONDS)
        self.assertLessEqual(worker._QUIESCENCE_CONFIRM_SECONDS, worker._NOTIFICATION_CONFIRM_SECONDS)


class LifecycleBracketSupervisionTest(_SupervisedCase):
    """``command_lifecycle`` supervision (amendment 0): an open bracket is
    owned work, a stalled one is timed and declared, and an irregular one
    matches nothing."""

    def test_a_fire_does_not_end_the_session_before_its_completed(self) -> None:
        self.patch(worker, "WAKEUP_SETTLE_SECONDS", 1)
        result = self.launch([[{"step": "lifecycle_fault", "kind": "delay_completed", "seconds": 2},
                               {"step": "wakeup", "delay": 1}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        open_bracket = [at for at, state, details in self.log if state == worker.WAITING
                        and details["waiting_on"]["command_lifecycles"]
                        and details["waiting_on"]["command_lifecycles"][0]["turn_seen"]]
        matched = [at for at, state, details in self.log if state == worker.WAITING
                   and any(w["state"] == "fire_matched" for w in details["waiting_on"]["wakeups"])]
        self.assertTrue(open_bracket and matched)
        self.assertGreaterEqual(matched[0] - open_bracket[0], 1.5)
        self.assertGreater(self.at(worker.ENDING), matched[0])
        [wakeup] = self.diagnosis(result)["wakeups_seen"]
        self.assertEqual(wakeup["settled_by"], "settle_window")

    def test_an_unterminated_bracket_is_declared_and_flushed_with_ending(self) -> None:
        self.patch(worker, "COMMAND_LIFECYCLE_GRACE_SECONDS", 1)
        result = self.launch([[{"step": "lifecycle_fault", "kind": "omit_completed"}, {"step": "wakeup", "delay": 1}]])
        self.assertEqual(result.outcome, worker.AMBIGUOUS)
        diagnosis = self.diagnosis(result)
        self.assertEqual(diagnosis["reason"], "command_lifecycle_unterminated")
        facts = self.ending()["supervisor_facts"]
        self.assertIsNotNone(facts["command_lifecycle_overdue_declared_at"])
        self.assertEqual(facts["command_lifecycle_overdue_command_uuid"],
                         diagnosis["command_lifecycles"][0]["command_uuid"])

    def test_a_stalled_bracket_is_not_declared_while_a_task_is_open(self) -> None:
        self.patch(worker, "COMMAND_LIFECYCLE_GRACE_SECONDS", 1)
        done = self.dir / "task-done"
        result = self.launch([[{"step": "lifecycle_fault", "kind": "omit_completed"},
                               {"step": "wakeup", "delay": 1, "fire_turn": [
                                   {"step": "bash_bg", "seconds": 2, "write_file": str(done)}]}],
                              [{"step": "text", "text": "task done"}]])
        self.assertEqual(self.diagnosis(result)["reason"], "command_lifecycle_unterminated")
        self.assertGreaterEqual(self.at(worker.ENDING), done.stat().st_mtime)

    def test_a_long_bracketed_turn_is_never_declared(self) -> None:
        self.patch(worker, "COMMAND_LIFECYCLE_GRACE_SECONDS", 1)
        result = self.launch([[{"step": "wakeup", "delay": 1, "fire_turn": [
            {"step": "bash_bg", "id": "long", "seconds": 2.5},
            {"step": "await", "id": "long", "notify": False},
            {"step": "wakeup_stop"}]}]])
        self.assertEqual(result.outcome, worker.SUCCESS, self.diagnosis(result))
        self.assertIsNone(self.ending()["supervisor_facts"]["command_lifecycle_overdue_declared_at"])

    def test_an_irregular_bracket_matches_nothing_and_the_wakeup_becomes_overdue(self) -> None:
        cases = {
            "omit_started": (1, [[{"step": "lifecycle_fault", "kind": "omit_started"},
                                  {"step": "wakeup", "delay": 1}]]),
            "reuse_uuid": (2, [[{"step": "wakeup", "delay": 1, "fire_turn": [
                {"step": "lifecycle_fault", "kind": "reuse_uuid"}, {"step": "wakeup", "delay": 1}]}]]),
            "overlap": (2, [[{"step": "lifecycle_fault", "kind": "overlap"},
                             {"step": "wakeup", "delay": 1, "fire_turn": [{"step": "wakeup", "delay": 1}]}]]),
            "bracket_turn": (1, [[{"step": "wakeup", "delay": 6.5}, {"step": "bash_bg", "seconds": 0.5},
                                  {"step": "lifecycle_fault", "kind": "bracket_turn", "turn": "task"}],
                                 [{"step": "text", "text": "task done"}]]),
        }
        for name, (grace, turns) in cases.items():
            with self.subTest(fault=name):
                self.log.clear()
                with unittest.mock.patch.object(worker, "WAKEUP_GRACE_SECONDS", grace):
                    result = self.launch(turns)
                diagnosis = self.diagnosis(result)
                self.assertEqual(result.outcome, worker.AMBIGUOUS)
                self.assertEqual(diagnosis["reason"], "command_lifecycle_irregular")
                self.assertIn("wakeup_not_delivered", diagnosis["secondary_reasons"])
                self.assertTrue(diagnosis["command_lifecycle_anomalies"])

    def test_a_bracketed_task_turn_with_no_wakeup_ends_at_quiescence_irregular(self) -> None:
        result = self.launch([[{"step": "bash_bg", "seconds": 0.3},
                               {"step": "lifecycle_fault", "kind": "bracket_turn", "turn": "task"}],
                              [{"step": "text"}]])
        self.assertEqual(result.outcome, worker.AMBIGUOUS)
        self.assertEqual(self.diagnosis(result)["reason"], "command_lifecycle_irregular")
        self.assertEqual(self.anomalies(result), ["unmatched_bracket"])
        self.assertIsNone(self.ending()["supervisor_facts"]["wakeup_overdue_declared_at"])


class WrongMatchTest(_SupervisedCase):
    """A wrong match never ends the session (round 9's I1): a spurious
    bracket around the task-completion turn matches the due wakeup ``W``,
    which stays owned (``fire_matched``) until it settles, so ``W``'s real
    fire is still observed. ``WAKEUP_SETTLE_SECONDS`` is patched to 3 s."""

    def setUp(self) -> None:
        super().setUp()
        self.patch(worker, "WAKEUP_SETTLE_SECONDS", 3)

    @staticmethod
    def _turns(*, fire_delay: float, task_turn: list, task_seconds: float = 2, extra_turns=()) -> list:
        return [[{"step": "wakeup", "delay": 1},
                 {"step": "bash_bg", "id": "A", "seconds": task_seconds},
                 {"step": "lifecycle_fault", "kind": "spurious_bracket", "turn": "task"},
                 {"step": "lifecycle_fault", "kind": "delay_fire", "seconds": fire_delay}],
                task_turn, *extra_turns]

    def test_the_real_fire_inside_the_window_is_observed_and_fails_the_run_closed(self) -> None:
        result = self.launch(self._turns(fire_delay=2, task_turn=[{"step": "text", "text": "task done"}]))
        diagnosis = self.diagnosis(result)
        self.assertEqual(result.outcome, worker.AMBIGUOUS)
        self.assertEqual(diagnosis["reason"], "command_lifecycle_irregular")
        spurious, real = diagnosis["command_lifecycles"]
        self.assertEqual(spurious["state"], "closed_regular")
        self.assertEqual(diagnosis["command_lifecycle_anomalies"],
                         [{"kind": "unmatched_bracket", "command_uuid": real["command_uuid"],
                           "offset": real["completed_offset"]}])
        # ENDING, and so stdin's EOF, came only after the real fire's
        # completed(X): never between the spurious bracket and it.
        self.assertGreater(self.ending()["ending_offset"], real["completed_offset"])
        self.assertGreaterEqual(json.loads(self.diag.read_text())["stdin_eof_at"], self.at(worker.ENDING))
        self.assertEqual(diagnosis["turns"], 3)

    def test_a_stop_inside_the_spurious_bracket_exposes_the_wrong_match_through_the_count(self) -> None:
        result = self.launch(self._turns(fire_delay=2, task_turn=[{"step": "wakeup_stop"}]))
        diagnosis = self.diagnosis(result)
        self.assertEqual(result.outcome, worker.AMBIGUOUS)
        self.assertEqual(diagnosis["reason"], "command_lifecycle_irregular")
        self.assertIn("wakeup_count_mismatch", self.anomalies(result))
        [stop] = diagnosis["wakeup_stops"]
        self.assertEqual((stop["cancelled_wakeups"], stop["expected_count"]), (1, 0))
        # Ended at quiescence, without waiting for the window.
        self.assertLess(self.returned - self.started, 4.5)

    def test_a_turn_inside_the_window_restarts_it(self) -> None:
        result = self.launch([[{"step": "wakeup", "delay": 1},
                               {"step": "monitor", "id": "m", "ticks": 2, "interval": 1.5}],
                              *[[{"step": "text", "text": f"monitor {n}"}] for n in range(3)]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        last_turn = max(at for at, state, _details in self.log if state == worker.RUNNING)
        self.assertGreaterEqual(self.at(worker.ENDING) - last_turn, 2.9)

    def test_an_open_task_pauses_the_window(self) -> None:
        """Under revision 10's predicate this run would have ended the
        session at the second task's completion turn, before the real fire."""
        result = self.launch(self._turns(fire_delay=8, task_turn=[{"step": "bash_bg", "id": "B", "seconds": 6}],
                                         extra_turns=[[{"step": "text", "text": "B done"}]]))
        diagnosis = self.diagnosis(result)
        self.assertEqual(diagnosis["reason"], "command_lifecycle_irregular")
        spurious, real = diagnosis["command_lifecycles"]
        self.assertEqual(self.anomalies(result), ["unmatched_bracket"])
        self.assertEqual(diagnosis["command_lifecycle_anomalies"][0]["command_uuid"], real["command_uuid"])
        self.assertGreater(self.ending()["ending_offset"], real["completed_offset"])

    def test_the_double_breach_residue(self) -> None:
        """The one documented path by which an unrelated bracket can
        precede ``ENDING`` (H2): the harness brackets a non-fire turn after
        ``W``'s due time *and* delays ``W``'s real fire past the lateness
        bound the settle window is sized from."""
        result = self.launch(self._turns(fire_delay=30, task_turn=[{"step": "text", "text": "task done"}]))
        diagnosis = self.diagnosis(result)
        self.assertEqual(result.outcome, worker.SUCCESS)
        [spurious] = diagnosis["command_lifecycles"]
        [wakeup] = diagnosis["wakeups_seen"]
        self.assertEqual((wakeup["settled_by"], wakeup["command_uuid"]), ("settle_window", spurious["command_uuid"]))
        self.assertLess(self.returned - self.started, 20)


_INNER_CONTROLLER = r'''
import json, os, signal, sys, tempfile
sys.path.insert(0, sys.argv[1])
from controller import anchor, worker

out_dir, mode, fake = sys.argv[2], sys.argv[3], sys.argv[4]
for key in [k for k in os.environ if k.startswith("FAKE_CLAUDE_")]:
    del os.environ[key]
os.environ["FAKE_CLAUDE_DESCENDANT"] = "setsid:2"
os.environ["FAKE_CLAUDE_DESCENDANT_FILE"] = os.path.join(out_dir, "grandchild.json")
anchor.ANCHOR_POLL_SECONDS, anchor.ANCHOR_ORPHAN_SECONDS = 0.2, 2.0
paths = {}
for key in ("stdout_path", "stderr_path"):
    fd, paths[key] = tempfile.mkstemp(dir=out_dir)
    os.close(fd)


def dump(name, value):
    with open(os.path.join(out_dir, name + ".tmp"), "w") as fh:
        json.dump(value, fh)
    os.replace(os.path.join(out_dir, name + ".tmp"), os.path.join(out_dir, name))


def on_spawn(process, *, anchor=None, ownership_tag=None):
    dump("inner.json", {"anchor": anchor.pid, "tag": ownership_tag})


def on_state_change(state, details):
    if state != worker.DRAINING:
        return
    with open(os.environ["FAKE_CLAUDE_DESCENDANT_FILE"]) as fh:
        grandchild = json.load(fh)["pid"]
    with open(f"/proc/{grandchild}/environ", "rb") as fh:
        environ = fh.read().split(b"\0")
    prefix = b"WORKFLOW_CONTROLLER_OWNERSHIP="
    dump("tags.json", {"grandchild": grandchild,
                       "tags": [item[len(prefix):].decode() for item in environ if item.startswith(prefix)]})
    if mode == "die":
        os.kill(os.getpid(), signal.SIGKILL)


worker.launch("inner task", cwd=out_dir, permission_mode="auto", timeout=60, claude_bin=fake,
              on_spawn=on_spawn, on_state_change=on_state_change, **paths)
'''


class OwnershipTest(_SupervisedCase):
    """Owned descendants (plan C, step 3): by the process group, the tag,
    adoption and the record of what was seen owned; recognised daemons
    excluded; the drain bound; ``--timeout`` over the whole owned lifetime;
    and nested Controllers."""

    def _orphan(self, details_list: list[dict], pid: int) -> dict:
        for details in details_list:
            for entry in details["owned_processes"]:
                if entry["pid"] == pid:
                    return entry
        self.fail(f"the orphan, pid {pid}, was never flushed as owned")

    def _escaped(self, orphan: str, **patches) -> tuple[worker.WorkerResult, dict, dict]:
        seen: dict = {}

        def on_state(state: str, details: dict) -> None:
            for entry in details["owned_processes"]:
                stat = Path(f"/proc/{entry['pid']}/stat")
                with contextlib.suppress(OSError):
                    seen.setdefault(entry["pid"], int(stat.read_text().rsplit(")", 1)[1].split()[1]))

        pid_file = self.dir / "orphan.pid"
        result = self.launch([[{"step": "bash_bg", "seconds": 0.2, "orphan": orphan, "orphan_seconds": 2,
                                "orphan_pid_file": str(pid_file)}],
                              [{"step": "text"}]], on_state=on_state)
        entry = self._orphan(self.details(worker.DRAINING), int(pid_file.read_text()))
        self.assertRegex(entry["cmdline"], _orphan_cmdline_re("fake-claude-orphan", 2))
        return result, entry, seen

    def test_a_setsid_escapee_is_owned_by_tag_and_adopted_and_waited_for(self) -> None:
        result, entry, ppids = self._escaped("setsid")
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(entry["source"], "tag")
        self.assertEqual(ppids[entry["pid"]], os.getpid(), "the escapee was not adopted by the supervisor")
        self.assertIsNone(process_fixtures.read_stat(entry["pid"]), "launch returned before the escapee ended")

    def test_a_reparented_group_escapee_is_owned_and_adopted(self) -> None:
        result, entry, ppids = self._escaped("reparent")
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(entry["source"], "group")
        self.assertEqual(ppids[entry["pid"]], os.getpid())
        self.assertIsNone(process_fixtures.read_stat(entry["pid"]))

    def test_without_a_subreaper_the_tag_still_finds_the_escapee(self) -> None:
        self.patch(worker, "_prctl", lambda *args: None)
        result, entry, ppids = self._escaped("setsid")
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual(entry["source"], "tag")
        self.assertNotEqual(ppids[entry["pid"]], os.getpid())
        self.assertTrue(process_fixtures.wait_until(lambda: not _running(entry["pid"]), timeout=5))

    def test_a_recorded_process_stays_owned_after_it_passes_no_other_test(self) -> None:
        self.patch(worker, "_prctl", lambda *args: None)
        recorded: set[int] = set()
        real_read_environ = worker._read_environ

        def read_environ(root, pid):
            if pid in recorded:
                raise PermissionError(errno.EACCES, "scrubbed for the test")
            return real_read_environ(root, pid)

        pid_file = self.dir / "orphan.pid"

        def on_state(state: str, details: dict) -> None:
            with contextlib.suppress(OSError, ValueError):
                orphan = int(pid_file.read_text())
                recorded.update(entry["pid"] for entry in details["owned_processes"] if entry["pid"] == orphan)

        self.patch(worker, "_read_environ", read_environ)
        result = self.launch([[{"step": "bash_bg", "seconds": 0.2, "orphan": "setsid", "orphan_seconds": 3,
                                "orphan_pid_file": str(pid_file)}],
                              [{"step": "text"}]], on_state=on_state)
        self.assertEqual(result.outcome, worker.SUCCESS)
        [pid] = recorded
        self.assertRegex(self._orphan(self.details(worker.DRAINING), pid)["cmdline"],
                         _orphan_cmdline_re("fake-claude-orphan", 3))
        draining = self.details(worker.DRAINING)
        self.assertTrue(draining)
        self.assertTrue(all(any(e["pid"] == pid for e in d["owned_processes"]) for d in draining[:-1]))
        self.assertTrue(process_fixtures.wait_until(lambda: not _running(pid), timeout=5))
        self.assertGreaterEqual(self.returned - self.started, 3)

    def test_a_reused_pid_is_not_owned_and_a_dead_entry_is_pruned(self) -> None:
        leader, other = process_fixtures.spawn_sleeper(self), process_fixtures.spawn_sleeper(self)
        dead = subprocess.Popen(["true"])
        dead.wait()
        ticks = process_fixtures.read_stat(other.pid)[2]
        ownership = worker._Ownership(tag="cp3-no-such-tag", worker_process=worker.capture_worker_process(leader.pid),
                                      anchor_pid=None, baseline=set(), adopting=False)
        entry = {"pid": other.pid, "start_ticks": ticks, "source": "tag", "cmdline": "sleeper"}
        ownership.owned = {other.pid: dict(entry),
                           dead.pid: {"pid": dead.pid, "start_ticks": 1, "source": "tag", "cmdline": "gone"}}
        ownership.scan()
        self.assertEqual(ownership.entries(), [entry])  # recorded and still matching: kept
        ownership.owned = {other.pid: {**entry, "start_ticks": ticks + 1}}
        ownership.scan()
        self.assertEqual(ownership.entries(), [])  # the same pid, another process

    def test_the_record_stays_bounded_over_many_short_lived_children(self) -> None:
        scans: list[int] = []
        real_scan = worker._Ownership.scan

        def counting_scan(ownership) -> None:
            scans.append(1)
            real_scan(ownership)

        self.patch(worker._Ownership, "scan", counting_scan)
        command = "for i in $(seq 500); do sleep 0.02 & if [ $((i % 4)) -eq 0 ]; then wait; fi; done; wait"
        result = self.launch([[{"step": "bash_bg", "command": command}], [{"step": "text"}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertLessEqual(len(self.log), len(scans))
        flushed = set()
        for _at, _state, details in self.log:
            self.assertLessEqual(len(details["owned_processes"]), 8)
            flushed.update((e["pid"], e["start_ticks"]) for e in details["owned_processes"])
            self.assertLessEqual(len(flushed), details["owned_processes_seen_count"])
        seen = result.owned_processes_seen
        self.assertGreaterEqual(seen["count"], len(flushed))
        self.assertLessEqual(seen["count"], 505)
        self.assertLessEqual(len(seen["sample"]), worker.OWNED_PROCESS_SAMPLE)
        self.assertEqual(len({e["pid"] for e in seen["sample"]}), len(seen["sample"]))

    def test_the_adopted_zombie_reaper_never_reaps_the_worker(self) -> None:
        result = self.launch([[{"step": "bash_bg", "seconds": 0.3, "orphan": "reparent", "orphan_seconds": 1}],
                              [{"step": "text"}]], env={"FAKE_CLAUDE_EXIT": "3"})
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.outcome, worker.FAILURE)

    def test_an_unreadable_proc_keeps_draining_and_is_reported_unverifiable(self) -> None:
        blocked = threading.Event()
        real_list = worker._list_proc

        def list_proc(root):
            if blocked.is_set():
                raise PermissionError(errno.EACCES, "unreadable /proc (patched)")
            return real_list(root)

        def on_state(state: str, details: dict) -> None:
            if state == worker.DRAINING and not blocked.is_set():
                blocked.set()
                timer = threading.Timer(3.0, blocked.clear)
                timer.daemon = True
                timer.start()
                self.addCleanup(timer.cancel)

        self.patch(worker, "_list_proc", list_proc)
        descendant = self.dir / "descendant.json"
        result = self.launch(env={"FAKE_CLAUDE_DESCENDANT": "group-closed:1",
                                  "FAKE_CLAUDE_DESCENDANT_FILE": str(descendant)}, on_state=on_state)
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertIn("unverifiable", [details["scan"] for details in self.details(worker.DRAINING)])
        exited_at = json.loads(descendant.read_text())["exited_at"]
        self.assertGreaterEqual(self.returned - exited_at, 1.0, "the drain ended on an unverifiable scan")

    def test_an_unreadable_environ_is_not_unverifiable_and_the_group_still_owns(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        real_read_environ = worker._read_environ

        def unrelated_unreadable(root, pid):
            if pid == sleeper.pid:
                raise PermissionError(errno.EACCES, "non-dumpable (patched)")
            return real_read_environ(root, pid)

        with unittest.mock.patch.object(worker, "_read_environ", unrelated_unreadable):
            result = self.launch([[{"step": "bash_bg", "seconds": 0.5}], [{"step": "text"}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertEqual({details["scan"] for _at, _state, details in self.log}, {"verified"})

        self.log.clear()

        def everything_unreadable(root, pid):
            raise PermissionError(errno.EACCES, "non-dumpable (patched)")

        descendant = self.dir / "descendant.json"
        with unittest.mock.patch.object(worker, "_read_environ", everything_unreadable):
            result = self.launch(env={"FAKE_CLAUDE_DESCENDANT": "group:1",
                                      "FAKE_CLAUDE_DESCENDANT_FILE": str(descendant)})
        self.assertEqual(result.outcome, worker.SUCCESS)
        pid = json.loads(descendant.read_text())["pid"]
        entries = [e for d in self.details(worker.DRAINING) for e in d["owned_processes"] if e["pid"] == pid]
        self.assertTrue(entries)
        self.assertEqual({e["source"] for e in entries}, {"group"})

    def test_a_recognised_daemon_is_excluded_and_left_running(self) -> None:
        result = self.launch([[{"step": "bash_bg", "seconds": 0.5, "orphan": "daemon", "argv0": "gpg-agent"}],
                              [{"step": "text"}]])
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertLess(self.returned - self.started, 15)
        [excluded] = self.details(worker.ENDED)[0]["excluded_processes"]
        self.extra_pids.append(excluded["pid"])
        self.assertEqual(excluded["pattern"], "gpg-agent")
        # Functional review F3: the entry carries its command line, in the
        # one shape the presenter and its fixtures use.
        self.assertEqual(tuple(excluded), worker.DAEMON_ENTRY_FIELDS)
        self.assertEqual(excluded["cmdline"], "gpg-agent 3600")
        self.assertTrue(_running(excluded["pid"]), "the recognised daemon was ended")

    def test_an_unrecognised_daemon_detaches_after_the_drain_bound_and_ends_nothing(self) -> None:
        self.patch(worker, "DRAIN_DETACH_SECONDS", 2)
        lock_file = self.dir / "lock"
        lock_file.touch()
        lock_fd = os.open(lock_file, os.O_RDONLY)
        self.addCleanup(os.close, lock_fd)
        pid_file = self.dir / "orphan.pid"
        result = self.launch([[{"step": "bash_bg", "seconds": 0.3, "orphan": "daemon",
                                "orphan_pid_file": str(pid_file)}], [{"step": "text"}]],
                             pass_fds=(lock_fd,))
        self.assertIsInstance(result, worker.DrainDetached)
        [remaining] = result.remaining
        self.extra_pids.append(remaining["pid"])
        self.assertEqual(remaining["pid"], int(pid_file.read_text()))
        self.assertRegex(remaining["cmdline"], _orphan_cmdline_re("fake-claude-daemon", 3600))
        self.assertEqual(result.remaining_pids, [remaining["pid"]])
        self.assertTrue(_running(remaining["pid"]), "the drain bound ended the escapee")
        anchor_pid = self.spawn["anchor"].pid
        self.assertTrue(_running(anchor_pid), "the anchor was ended at the detach")
        self.assertIn(str(lock_file), _fd_targets(anchor_pid))
        self.assertEqual(self.states()[-1], worker.DRAINING)
        self.assertGreaterEqual(self.returned - self.at(worker.DRAINING), 1.9)

    def test_a_timeout_while_waiting_ends_the_group_the_tagged_processes_and_the_anchor(self) -> None:
        result = self.launch([[{"step": "bash_bg", "seconds": 30, "orphan": "setsid", "orphan_seconds": 60}]],
                             timeout=2)
        self.assertEqual(result.outcome, worker.INTERRUPTED)
        self.assertTrue(process_fixtures.wait_until(lambda: not process_fixtures.tagged_pids(self.spawn["tag"]),
                                                    timeout=5))
        self.assertIsNone(process_fixtures.read_stat(self.spawn["anchor"].pid))

    def test_a_timeout_while_draining_ends_the_escapee_and_the_anchor(self) -> None:
        descendant = self.dir / "descendant.json"
        result = self.launch(env={"FAKE_CLAUDE_DESCENDANT": "setsid:30",
                                  "FAKE_CLAUDE_DESCENDANT_FILE": str(descendant)}, timeout=3)
        self.assertEqual(result.outcome, worker.INTERRUPTED)
        self.assertEqual(result.returncode, 0)
        pid = json.loads(descendant.read_text())["pid"]
        self.assertTrue(process_fixtures.wait_until(lambda: not _running(pid), timeout=5))
        self.assertNotIn("exited_at", json.loads(descendant.read_text()))
        self.assertIsNone(process_fixtures.read_stat(self.spawn["anchor"].pid))

    def _nested(self, mode: str) -> worker.WorkerResult:
        script = self.dir / "inner_controller.py"
        script.write_text(_INNER_CONTROLLER)
        inner = self.dir / "inner"
        inner.mkdir()
        command = " ".join(shlex.quote(part) for part in (
            sys.executable, str(script), str(Path(__file__).resolve().parent.parent), str(inner), mode,
            str(FAKE_CLAUDE)))
        self.inner = inner
        return self.launch([[{"step": "bash_bg", "command": command, "description": "inner controller"}],
                            [{"step": "text", "text": "inner done"}]])

    def test_a_nested_worker_carries_both_tags_and_the_outer_launch_waits_for_it(self) -> None:
        result = self._nested("keep")
        self.assertEqual(result.outcome, worker.SUCCESS)
        tags = json.loads((self.inner / "tags.json").read_text())
        inner_tag = json.loads((self.inner / "inner.json").read_text())["tag"]
        [tag_list] = tags["tags"]
        self.assertEqual(tag_list.split(":")[-2:], [self.spawn["tag"], inner_tag])
        grandchild = json.loads((self.inner / "grandchild.json").read_text())
        self.assertLessEqual(grandchild["exited_at"], self.returned)

    def test_a_leaked_inner_anchor_holds_the_outer_job_only_until_it_ends_itself(self) -> None:
        """The inner Controller is SIGKILLed while draining: its anchor
        passes to the outer supervisor by adoption (the anchor carries no
        tag), and ends itself once its worker and every tagged process are
        gone and no supervisor is attached."""
        result = self._nested("die")
        self.assertEqual(result.outcome, worker.SUCCESS)
        inner_anchor = json.loads((self.inner / "inner.json").read_text())["anchor"]
        self.assertIsNone(process_fixtures.read_stat(inner_anchor), "the inner anchor outlived the outer launch")
        entries = [e for d in self.details(worker.DRAINING) for e in d["owned_processes"] if e["pid"] == inner_anchor]
        self.assertTrue(entries, "the outer supervisor never owned the leaked inner anchor")
        self.assertEqual({e["source"] for e in entries}, {"adopted"})
        self.assertLess(self.returned - self.started, 45)


if __name__ == "__main__":
    unittest.main()
