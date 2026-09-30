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
import itertools
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
import types
import unittest
import unittest.mock
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import anchor, lock, observe, routing, worker, worker_stream  # noqa: E402
from controller.errors import UserOnlyCommandError, WorkerLaunchError  # noqa: E402
from tests import fake_claude, fixtures, process_fixtures, test_observe  # noqa: E402

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


def _empty_first_cmdline_read(marker: str) -> tuple[object, list[int]]:
    """A ``worker._read_cmdline`` that reads empty the first time a pid's
    real command line contains ``marker``, as ``/proc/<pid>/cmdline`` does
    mid-``execve`` or on the exit path (ci-reliability CP1). It matches on
    content, because the orphan's pid file may not exist yet at its first
    scan. Returns the function and the pids it emptied, so a test can show
    its widening ran."""
    real_read_cmdline = worker._read_cmdline
    emptied: list[int] = []

    def read_cmdline(root, pid: int) -> list[str]:
        cmdline = real_read_cmdline(root, pid)
        if pid not in emptied and any(marker in part for part in cmdline):
            emptied.append(pid)
            return []
        return cmdline

    return read_cmdline, emptied


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


def _stat_line(pid: int, *, state: str, pgrp: int, start_ticks: int, comm: str = _TRAP_COMM, ppid: int = 1,
               session: int | None = None) -> str:
    """A ``/proc/<pid>/stat`` line: field 3 state, field 4 ppid, field 5
    pgrp, field 6 session (default: ``pgrp``), field 22 starttime, and a
    few more fields after it."""
    after = [
        state, str(ppid), str(pgrp), str(pgrp if session is None else session), "0", "-1", "4194560", "0", "0", "0", "0",
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
                threads: dict[int, str] | None = None, ppid: int = 1, session: int | None = None) -> "_FakeProc":
        (self.root / str(pid)).mkdir(exist_ok=True)
        (self.root / str(pid) / "stat").write_text(
            _stat_line(pid, state=state, pgrp=pgrp, start_ticks=start_ticks, ppid=ppid, session=session))
        for tid, thread_state in (threads or {}).items():
            task = self.root / str(pid) / "task" / str(tid)
            task.mkdir(parents=True, exist_ok=True)
            (task / "stat").write_text(_stat_line(tid, state=thread_state, pgrp=pgrp, start_ticks=start_ticks))
        return self

    def children(self, pid: int, tid: int, children: Iterable[int]) -> "_FakeProc":
        """``<pid>/task/<tid>/children``: one thread's direct children."""
        self.write(f"{pid}/task/{tid}/children", "".join(f"{child} " for child in children))
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


def _ppid(pid: int) -> int | None:
    """``pid``'s parent from the real ``/proc``, or ``None`` when it is gone."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return int(text[text.rfind(")") + 1:].split()[1])


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


def _open_gate(gate: Path, *, deadline: float | None = None) -> None:
    """Release a fake ``bash_bg`` ``orphan_gate``: open its FIFO for writing
    and close it, so the escapee's blocked open returns and its ``read``
    sees end of file. It retries (``ENXIO``: no reader yet) until
    ``deadline``. Without one it is the cleanup form: one ``O_RDWR`` open,
    which never blocks and releases an escapee already waiting."""
    if deadline is None:
        with contextlib.suppress(OSError):
            os.close(os.open(gate, os.O_RDWR | os.O_NONBLOCK))
        return
    while True:
        try:
            os.close(os.open(gate, os.O_WRONLY | os.O_NONBLOCK))
            return
        except OSError as exc:
            if exc.errno != errno.ENXIO or time.monotonic() >= deadline:
                return
        time.sleep(0.01)


class _FixtureOwnershipCase(unittest.TestCase):
    """An ``_Ownership`` scanning a fixture ``/proc``, with one escapee."""

    TAG = "cp5b-tag"
    PGID = 4_100_000  # the worker's group, not a real process here
    ESCAPEE = 4_100_001

    def setUp(self) -> None:
        self.fake = _FakeProc(self)
        self.enterContext(self.fake.patch())
        self.enterContext(unittest.mock.patch.dict(worker._ADOPTED))
        self.me = os.getpid()

    def _ownership(self, *, adopting: bool = True) -> worker._Ownership:
        process = worker.WorkerProcess(pid=self.PGID, pgid=self.PGID, start_ticks=1, boot_id=None,
                                       pid_namespace=None, hostname=None, machine_id=None)
        return worker._Ownership(tag=self.TAG, worker_process=process, anchor_pid=None, baseline=set(),
                                 adopting=adopting)

    def _process(self, pid: int, *, pgrp: int, tag: bool, ppid: int = 1, start_ticks: int = 500,
                 cmdline: str = "bash -c gated") -> None:
        self.fake.process(pid, state="S", pgrp=pgrp, start_ticks=start_ticks, ppid=ppid)
        environ = f"{worker.OWNERSHIP_VAR}={self.TAG}\0" if tag else "PATH=/bin\0"
        self.fake.write(f"{pid}/environ", environ)
        self.fake.write(f"{pid}/cmdline", cmdline.replace(" ", "\0") + "\0")


class OwnershipProvenanceTest(_FixtureOwnershipCase):
    """Design H (adaptive-test-sharding CP5B): an owned entry's ``source``
    follows the basis of the latest scan that found it, while its identity,
    its ``cmdline`` and the first-sighting sample do not change, and nor
    does which processes are owned (I10). Over a fixture ``/proc``."""

    def _first_sighting(self) -> worker._Ownership:
        ownership = self._ownership()
        self._process(self.ESCAPEE, pgrp=self.PGID, tag=True)
        ownership.scan()
        self.assertEqual([e["source"] for e in ownership.entries()], ["group"])
        return ownership

    def test_an_escapee_that_left_the_group_with_the_tag_is_relabelled_tag(self) -> None:
        ownership = self._first_sighting()
        self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=True, cmdline="fake-claude-orphan 2")
        ownership.scan()
        self.assertEqual(ownership.entries(), [{"pid": self.ESCAPEE, "start_ticks": 500, "source": "tag",
                                                "cmdline": "bash -c gated"}])
        self.assertEqual([e["source"] for e in ownership.sample], ["group"])

    def test_without_the_tag_an_adopted_escapee_is_relabelled_adopted(self) -> None:
        ownership = self._first_sighting()
        self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=False, ppid=self.me)
        ownership.scan()
        [entry] = ownership.entries()
        self.assertEqual((entry["pid"], entry["start_ticks"], entry["source"]), (self.ESCAPEE, 500, "adopted"))

    def test_a_process_found_only_by_its_record_keeps_its_recorded_source(self) -> None:
        ownership = self._first_sighting()
        self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=False)
        ownership.scan()
        self.assertEqual([e["source"] for e in ownership.entries()], ["group"])

    def test_a_changed_start_ticks_still_drops_the_entry(self) -> None:
        ownership = self._first_sighting()
        self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=False, start_ticks=501)
        ownership.scan()
        self.assertEqual(ownership.entries(), [])

    def test_the_relabel_changes_no_membership(self) -> None:
        # The same /proc sequence, scanned with the relabel and with it
        # undone after every scan (the pre-H ``entry = recorded``): the
        # membership fields agree at every step.
        daemon, stranger = self.ESCAPEE + 1, self.ESCAPEE + 2
        steps = [
            lambda: (self._process(self.ESCAPEE, pgrp=self.PGID, tag=True),
                     self._process(daemon, pgrp=self.PGID, tag=True, cmdline="gpg-agent --daemon"),
                     self._process(stranger, pgrp=stranger, tag=False)),
            lambda: self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=True),
            lambda: self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=False, ppid=self.me),
            lambda: self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=False),
            lambda: self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=False, start_ticks=501),
        ]

        def membership(ownership: worker._Ownership) -> tuple:
            return (sorted(ownership.owned), ownership.outside_group, ownership.excluded_entries(),
                    ownership.seen_count, ownership.verifiable)

        relabelled, first_seen = self._ownership(), self._ownership()
        labels: dict[int, str] = {}
        for step in steps:
            step()
            relabelled.scan()
            first_seen.scan()
            for pid, entry in first_seen.owned.items():
                entry["source"] = labels.setdefault(pid, entry["source"])
            self.assertEqual(membership(relabelled), membership(first_seen))
        self.assertEqual(relabelled.seen_count, 1)
        self.assertEqual([e["pid"] for e in relabelled.excluded_entries()], [daemon])

    def test_a_relabel_changes_the_signature_and_is_published(self) -> None:
        supervision = worker._Supervision.__new__(worker._Supervision)
        supervision.state, supervision.published = worker.DRAINING, None
        published: list[str] = []
        supervision.on_state_change = lambda state, details: published.append(
            details["owned_processes"][0]["source"])
        waiting_on = {"tasks": [], "wakeups": [], "command_lifecycles": []}

        def details(source: str) -> dict:
            return {"owned_processes": [{"pid": self.ESCAPEE, "start_ticks": 500, "source": source, "cmdline": "x"}],
                    "excluded_processes": [], "scan": "verified", "waiting_on": waiting_on}

        group, tag = details("group"), details("tag")
        self.assertNotEqual(supervision._signature(group), supervision._signature(tag))
        for current in (group, group, tag, tag):
            supervision.details = lambda current=current: current
            supervision._publish()
        self.assertEqual(published, ["group", "tag"])


class CmdlineFillTest(_FixtureOwnershipCase):
    """ci-reliability CP1 (I1, I2): an entry first read with an empty
    command line (mid-``execve``, or on the exit path) takes the first
    non-empty read, once; a non-empty one is never replaced, and never by
    an empty read. Nothing else about the entry, or what is owned, moves."""

    def _cmdline(self, text: str) -> None:
        self.fake.write(f"{self.ESCAPEE}/cmdline", text.replace(" ", "\0") + ("\0" if text else ""))

    def _scan(self, ownership: worker._Ownership, cmdline: str) -> dict:
        self._cmdline(cmdline)
        ownership.scan()
        [entry] = ownership.entries()
        return entry

    def test_an_entry_first_read_empty_takes_the_next_non_empty_read_once(self) -> None:
        ownership = self._ownership()
        self._process(self.ESCAPEE, pgrp=self.PGID, tag=True)
        first = self._scan(ownership, "")
        self.assertEqual(first["cmdline"], "")
        self.assertEqual(ownership.sample[0]["cmdline"], "")
        self.assertEqual(self._scan(ownership, "")["cmdline"], "", "an empty read filled the entry")
        filled = self._scan(ownership, "bash -c gated")
        self.assertEqual(filled, dict(first, cmdline="bash -c gated"))
        self.assertEqual(ownership.sample, [filled], "the first-sighting sample kept the empty command line")
        self.assertEqual(self._scan(ownership, "")["cmdline"], "bash -c gated")
        self.assertEqual(self._scan(ownership, "sleep 3")["cmdline"], "bash -c gated")
        self.assertEqual(ownership.sample[0]["cmdline"], "bash -c gated")
        self.assertEqual(ownership.seen_count, 1)

    def test_a_non_empty_entry_keeps_its_text_through_an_empty_and_a_different_read(self) -> None:
        ownership = self._ownership()
        self._process(self.ESCAPEE, pgrp=self.PGID, tag=True)
        first = self._scan(ownership, "bash -c gated")
        self.assertEqual(self._scan(ownership, ""), first)
        self.assertEqual(self._scan(ownership, "fake-claude-orphan 2"), first)
        self.assertEqual(ownership.sample, [first])

    def test_an_empty_recorded_entry_fills_after_a_reattach(self) -> None:
        # Re-attach reuses the same scan over the record's entries (I2).
        process = worker.WorkerProcess(pid=self.PGID, pgid=self.PGID, start_ticks=1, boot_id=None,
                                       pid_namespace=None, hostname=None, machine_id=None)
        self._process(self.ESCAPEE, pgrp=self.ESCAPEE, tag=False)
        ownership = worker._recorded_ownership(
            tag=self.TAG, worker_process=process, anchor_pid=None,
            owned_processes=[{"pid": self.ESCAPEE, "start_ticks": 500, "source": "tag", "cmdline": ""}])
        entry = self._scan(ownership, "fake-claude-orphan 2")
        self.assertEqual(entry, {"pid": self.ESCAPEE, "start_ticks": 500, "source": "tag",
                                 "cmdline": "fake-claude-orphan 2"})

    def test_the_fill_is_published_once_and_nothing_else_is(self) -> None:
        supervision = worker._Supervision.__new__(worker._Supervision)
        supervision.state, supervision.published = worker.DRAINING, None
        published: list[str] = []
        supervision.on_state_change = lambda state, details: published.append(
            details["owned_processes"][0]["cmdline"])
        waiting_on = {"tasks": [], "wakeups": [], "command_lifecycles": []}

        def details(cmdline: str) -> dict:
            return {"owned_processes": [{"pid": self.ESCAPEE, "start_ticks": 500, "source": "group",
                                         "cmdline": cmdline}],
                    "excluded_processes": [], "scan": "verified", "waiting_on": waiting_on}

        for cmdline in ("", "", "bash -c gated", "bash -c gated"):
            supervision.details = lambda current=details(cmdline): current
            supervision._publish()
        self.assertEqual(published, ["", "bash -c gated"])


class OwnershipTest(_SupervisedCase):
    """Owned descendants (plan C, step 3): by the process group, the tag,
    adoption and the record of what was seen owned; recognised daemons
    excluded; the drain bound; ``--timeout`` over the whole owned lifetime;
    and nested Controllers."""

    def _orphan(self, details_list: list[dict], pid: int) -> dict:
        """The orphan's entry in the **last** publication that lists it: its
        ``source`` follows its current basis (Design H), so the last one
        is the escapee's basis after its ``setsid``."""
        for details in reversed(details_list):
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
                    # The last ppid: the spawn subshell has exited, and the
                    # escapee has been reparented, before the worker exits.
                    seen[entry["pid"]] = int(stat.read_text().rsplit(")", 1)[1].split()[1])

        # Every first read of the orphan's command line comes back empty, as
        # it can mid-execve: the entry still names it (ci-reliability CP1).
        read_cmdline, emptied = _empty_first_cmdline_read("fake-claude-orphan")
        self.patch(worker, "_read_cmdline", read_cmdline)
        pid_file = self.dir / "orphan.pid"
        result = self.launch([[{"step": "bash_bg", "seconds": 0.2, "orphan": orphan, "orphan_seconds": 2,
                                "orphan_pid_file": str(pid_file)}],
                              [{"step": "text"}]], on_state=on_state)
        entry = self._orphan(self.details(worker.DRAINING), int(pid_file.read_text()))
        self.assertIn(entry["pid"], emptied, "the widening never ran")
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

    def _gated_escapee(self, *, until_adopted: bool = False) -> tuple[worker.WorkerResult, int, list[dict]]:
        """The first-sighting race made deterministic (Design H): the
        escapee is held in the worker's group until it is seen owned by
        group, then released to setsid, and relabelled by a later scan.
        ``until_adopted`` also holds it until its parent has exited and it
        is this Controller's child. Returns the result, the escapee's pid
        and its published entries, in order."""
        gate, pid_file = self.dir / "gate", self.dir / "orphan.pid"
        os.mkfifo(gate)
        self.addCleanup(_open_gate, gate)  # never leaves the escapee blocked
        seen_by_group = threading.Event()

        def on_state(state: str, details: dict) -> None:
            with contextlib.suppress(OSError, ValueError):
                pid = int(pid_file.read_text())
                if any(e["pid"] == pid and e["source"] == "group" for e in details["owned_processes"]):
                    seen_by_group.set()

        def release() -> None:
            if not seen_by_group.wait(timeout=20):
                return
            if until_adopted:
                pid = int(pid_file.read_text())
                process_fixtures.wait_until(lambda: _ppid(pid) == os.getpid(), timeout=10)
            _open_gate(gate, deadline=time.monotonic() + 10)

        releaser = threading.Thread(target=release, daemon=True)
        releaser.start()
        result = self.launch([[{"step": "bash_bg", "seconds": 0.2, "orphan": "setsid", "orphan_seconds": 3,
                                "orphan_pid_file": str(pid_file), "orphan_gate": str(gate)}],
                              [{"step": "text"}]], on_state=on_state, timeout=30)
        releaser.join(timeout=10)
        self.assertEqual(result.outcome, worker.SUCCESS)
        pid = int(pid_file.read_text())
        entries = [e for _at, _state, details in self.log for e in details["owned_processes"] if e["pid"] == pid]
        self.assertTrue(entries, "the escapee was never published")
        self.assertEqual({(e["pid"], e["start_ticks"]) for e in entries}, {(pid, entries[0]["start_ticks"])})
        # The first-seen cmdline is kept, except that one read empty (mid
        # execve) takes the first non-empty read, once (ci-reliability CP1).
        runs = [cmdline for cmdline, _ in itertools.groupby(e["cmdline"] for e in entries)]
        self.assertTrue(runs == [runs[0]] or (len(runs) == 2 and runs[0] == "" and runs[1]),
                        f"cmdline is not the first-seen one: {runs}")
        for sampled in result.owned_processes_seen["sample"]:
            if sampled["pid"] == pid:
                self.assertEqual(sampled["source"], "group", "the sample is not first-sighting history")
        return result, pid, entries

    def test_a_gated_escapee_is_published_as_group_then_as_tag(self) -> None:
        _result, _pid, entries = self._gated_escapee()
        sources = [e["source"] for e in entries]
        self.assertEqual(sources[0], "group", sources)
        self.assertIn("tag", sources, "the escapee's setsid was never published")
        first_tag = sources.index("tag")
        # Between its setsid(2) and its exec of bash, a scan can read an
        # empty environ: no tag, so that scan owns the escapee by adoption
        # alone and says so (functional review round 2, F1).
        self.assertLessEqual(set(sources[:first_tag]), {"group", "adopted"}, sources)
        self.assertEqual(set(sources[first_tag:]), {"tag"}, sources)

    def test_an_escapee_read_mid_exec_is_published_as_adopted_for_that_scan(self) -> None:
        # Functional review round 2, F1: the escapee's first environ read
        # outside the group comes back empty, as it can in the middle of
        # its exec. That scan's basis is adoption (Design H); the next
        # scan's is the tag again, and ownership never lapses (I10).
        real_read_environ = worker._read_environ
        pid_file = self.dir / "orphan.pid"
        emptied: list[int] = []

        def read_environ(root, pid: int) -> bytes:
            with contextlib.suppress(OSError, ValueError):
                if not emptied and pid == int(pid_file.read_text()):
                    emptied.append(pid)
                    return b""
            return real_read_environ(root, pid)

        self.patch(worker, "_read_environ", read_environ)
        _result, pid, entries = self._gated_escapee(until_adopted=True)
        self.assertEqual(emptied, [pid])
        runs = [source for source, _ in itertools.groupby(e["source"] for e in entries)]
        self.assertEqual(runs, ["group", "adopted", "tag"])

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
        read_cmdline, emptied = _empty_first_cmdline_read("fake-claude-daemon")
        self.patch(worker, "_read_cmdline", read_cmdline)
        pid_file = self.dir / "orphan.pid"
        result = self.launch([[{"step": "bash_bg", "seconds": 0.3, "orphan": "daemon",
                                "orphan_pid_file": str(pid_file)}], [{"step": "text"}]],
                             pass_fds=(lock_fd,))
        self.assertIsInstance(result, worker.DrainDetached)
        [remaining] = result.remaining
        self.extra_pids.append(remaining["pid"])
        self.assertEqual(remaining["pid"], int(pid_file.read_text()))
        self.assertIn(remaining["pid"], emptied, "the widening never ran")
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



# ---------------------------------------------------------------------------
# Reaping every finished child (child-process-reaping CP2).
# ---------------------------------------------------------------------------


def _real_stat(pid: int) -> "worker._ProcStat | None":
    """The real ``/proc/<pid>/stat``, parsed, or ``None`` when it is gone."""
    try:
        return worker._parse_stat(Path(f"/proc/{pid}/stat").read_text())
    except (FileNotFoundError, ProcessLookupError):
        return None


def _my_children() -> set[int]:
    """This process's direct children, from every thread's ``children``
    file (the real ``/proc``, never the patchable seam)."""
    children: set[int] = set()
    for tid in os.listdir("/proc/self/task"):
        with contextlib.suppress(FileNotFoundError):
            children.update(int(pid) for pid in Path(f"/proc/self/task/{tid}/children").read_text().split())
    return children


def _zombie_children(baseline: set[int]) -> list[int]:
    """This process's zombie children outside ``baseline``."""
    zombies = []
    for pid in _my_children() - baseline:
        stat = _real_stat(pid)
        if stat is not None and stat.state == "Z":
            zombies.append(pid)
    return zombies


def _reaper_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == worker._IDLE_REAPER_NAME and t.is_alive()]


class _Sampler:
    """Counts, every 20 ms, the zombie children of this process outside
    ``baseline`` (reading only: it starts nothing)."""

    INTERVAL = 0.02

    def __init__(self, baseline: set[int]) -> None:
        self.baseline = baseline
        self.samples: list[tuple[float, int]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append((time.time(), len(_zombie_children(self.baseline))))
            self._stop.wait(self.INTERVAL)

    def __enter__(self) -> "_Sampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class ChildReapingTest(_SupervisedCase):
    """The regression (reaping plan, Design C): a fake worker orphans 150
    short-lived ``setsid`` grandchildren, one per 20 ms, while the
    supervisor is ``RUNNING``, ``WAITING`` or ``DRAINING``; the zombies held
    under this process stay within a bound derived from the tick each state
    runs at (0.2 s at most: about 10 per tick, bound 30, at most 5 samples
    above it), and none is left after ``launch``."""

    BURST = {"count": 150, "spacing": 0.02, "lifetime": 0.01}
    BOUND = 30
    ALLOWED_ABOVE = 5
    MIN_SAMPLES = 75

    def setUp(self) -> None:
        fixtures.isolate_idle_reaper(self)
        super().setUp()
        self.marks = self.dir / "marks.json"
        self.gate = self.dir / "gate"

    def _open_gate_at(self, state: str):
        def on_state(published: str, details: dict) -> None:
            if published == state:
                self.gate.touch()
        return on_state

    def _burst(self, turns: list, *, state: str) -> None:
        baseline = _my_children()
        with _Sampler(baseline) as sampler:
            result = self.launch(turns, on_state=self._open_gate_at(state), timeout=120)
        self.assertEqual(result.outcome, worker.SUCCESS)
        marks = json.loads(self.marks.read_text())
        started, ended = marks["started_at"], marks["ended_at"]
        during = [count for at, count in sampler.samples if started <= at <= ended]
        self.assertGreaterEqual(len(during), self.MIN_SAMPLES, "the sampler did not look during the burst")
        above = [count for count in during if count > self.BOUND]
        self.assertLessEqual(len(above), self.ALLOWED_ABOVE,
                             f"zombies above {self.BOUND} in {len(above)} samples (peak {max(during)})")
        in_effect = [logged for at, logged, _details in self.log if at <= started][-1:]
        in_effect += [logged for at, logged, _details in self.log if started < at <= ended]
        self.assertEqual(set(in_effect), {state}, f"published during the burst: {in_effect}")
        self.assertEqual(_zombie_children(baseline), [], "a zombie child outlived launch")

    def test_orphans_made_while_running_are_reaped_within_the_bound(self) -> None:
        self._burst([[{"step": "orphan_burst", **self.BURST, "marks_file": str(self.marks),
                       "gate": str(self.gate)}]], state=worker.RUNNING)

    def test_orphans_made_while_waiting_are_reaped_within_the_bound(self) -> None:
        command = " ".join(shlex.quote(part) for part in (
            sys.executable, str(FAKE_CLAUDE), "--orphan-burst", str(self.BURST["count"]), str(self.BURST["spacing"]),
            str(self.BURST["lifetime"]), "--marks", str(self.marks), "--gate", str(self.gate)))
        self._burst([[{"step": "bash_bg", "command": command}], [{"step": "text"}]], state=worker.WAITING)

    def test_orphans_made_while_draining_are_reaped_within_the_bound(self) -> None:
        self._burst([[{"step": "orphan_burst", **self.BURST, "detach": True, "marks_file": str(self.marks),
                       "gate": str(self.gate)}]], state=worker.DRAINING)



class _ReapingSeamCase(unittest.TestCase):
    """A fixture ``/proc`` for the reaping sweep: this process with two
    threads whose ``children`` files the test writes, fake children in this
    process's session or another, and an ``os.waitpid`` spy that records
    every pid and reaps nothing real: a pid in ``self.reaped`` reads as
    collected, any other as still running."""

    OTHER_SID = 4_300_000
    BASE = 4_200_000

    def setUp(self) -> None:
        fixtures.isolate_idle_reaper(self)
        self.me, self.sid = os.getpid(), os.getsid(0)
        self.fake = _FakeProc(self)
        self.enterContext(self.fake.patch())
        for name in ("_ADOPTED", "_FOREIGN_BORN", "_LAUNCH_CHILDREN", "_LAUNCH_OWNERS", "_REAP_EXCLUDED"):
            self.enterContext(unittest.mock.patch.dict(getattr(worker, name), clear=True))
        self.enterContext(unittest.mock.patch.object(worker, "_last_full_scan", None))
        self.fake.process(self.me, state="S", pgrp=self.me, ppid=1, session=self.sid)
        self.threads = {self.me: [], self.me + 1: []}
        self._write_children()
        self.waited: list[int] = []
        self.reaped: set[int] = set()

        def waitpid(pid: int, options: int):
            self.waited.append(pid)
            return (pid, 0) if pid in self.reaped else (0, 0)

        self.enterContext(unittest.mock.patch.object(worker.os, "waitpid", waitpid))

    def _write_children(self) -> None:
        for tid, children in self.threads.items():
            self.fake.children(self.me, tid, children)

    def child(self, pid: int, *, state: str = "Z", same_session: bool = False, start_ticks: int = 500,
              tid: int | None = None, ppid: int | None = None) -> int:
        """A fake child of this process, listed in thread ``tid``'s file."""
        self.fake.process(pid, state=state, pgrp=pid, start_ticks=start_ticks,
                          ppid=self.me if ppid is None else ppid,
                          session=self.sid if same_session else self.OTHER_SID)
        self.threads[self.me if tid is None else tid].append(pid)
        self._write_children()
        return pid

    def descendant(self, parent: int, pid: int, *, same_session: bool = True, start_ticks: int = 700,
                   ppid: int | None = None, state: str = "S") -> int:
        """A fake child of ``parent`` (listed in its ``children`` file)."""
        self.fake.process(pid, state=state, pgrp=pid, start_ticks=start_ticks,
                          ppid=parent if ppid is None else ppid,
                          session=self.sid if same_session else self.OTHER_SID)
        path = self.fake.root / str(parent) / "task" / str(parent) / "children"
        listed = path.read_text().split() if path.exists() else []
        self.fake.children(parent, parent, [*map(int, listed), pid])
        return pid

    def gone(self, pid: int) -> None:
        shutil.rmtree(self.fake.root / str(pid), ignore_errors=True)
        for children in self.threads.values():
            if pid in children:
                children.remove(pid)
        self._write_children()

    def sweep(self, exclude=None, baseline=(), **kwargs) -> None:
        worker._collect_children(dict(exclude or {}), set(baseline), **kwargs)


class ReapingSweepTest(_ReapingSeamCase):
    """The sweep (Design B.2): which children it records and reaps."""

    def test_children_of_every_thread_are_recorded_and_a_zombie_is_reaped(self) -> None:
        first = self.child(self.BASE + 1, tid=self.me)
        second = self.child(self.BASE + 2, tid=self.me + 1, state="S")
        self.reaped.add(first)
        self.sweep()
        self.assertEqual(worker._ADOPTED, {second: 500})
        self.assertEqual(self.waited, [first, second])
        self.assertTrue(all(pid > 0 for pid in self.waited))

    def test_the_worker_anchor_baseline_and_every_exclusion_are_never_recorded(self) -> None:
        worker_pid, anchor_pid, old, own, other_launch, excluded = (self.child(self.BASE + i) for i in range(1, 7))
        self.child(own, same_session=True)
        worker._LAUNCH_CHILDREN[other_launch] = 500
        with worker.exclude_from_reaping(lambda: types.SimpleNamespace(pid=excluded)):
            self.sweep({worker_pid: 500, anchor_pid: None}, baseline={old})
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])

    def test_a_final_sweep_leaves_the_exclusions_of_a_launch_that_reused_its_pids(self) -> None:
        """Launch A's worker and anchor have been waited for, and launch B's
        worker (start ticks not yet captured) and anchor reuse their pids
        before A's final sweep: B's exclusions survive it, so a later sweep
        whose baseline predates B records neither, and leave with B's own
        final sweep."""
        reused_anchor, reused_worker = self.BASE + 1, self.BASE + 2
        launch_a, launch_b = worker._LaunchExclusions(), worker._LaunchExclusions()
        launch_a.baseline = launch_b.baseline = set()
        launch_a.add(reused_worker)
        launch_a.add(reused_worker, 400)
        launch_a.add(reused_anchor)
        launch_b.add(self.child(reused_anchor))  # B's worker, exited, its status still Popen's
        launch_b.add(self.child(reused_worker, state="S"))  # B's anchor
        launch_a.final_sweep()
        self.assertEqual(worker._LAUNCH_CHILDREN, {reused_anchor: None, reused_worker: None})
        launch_b.add(reused_anchor, 500)
        self.sweep()
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])
        launch_b.final_sweep()
        self.assertEqual((worker._LAUNCH_CHILDREN, worker._LAUNCH_OWNERS), ({}, {}))

    def test_the_worker_pid_with_unknown_start_ticks_is_never_recorded(self) -> None:
        pid = self.child(self.BASE + 1)
        self.sweep({pid: None})
        self.assertEqual(worker._ADOPTED, {})
        self.sweep({pid: 499})  # a known, different start: the pid was reused
        self.assertIn(pid, self.waited)

    def test_an_unreadable_stat_is_skipped_until_it_can_be_read(self) -> None:
        _skip_if_root(self)
        pid = self.child(self.BASE + 1, state="S")
        self.fake.lock_out(f"{pid}/stat")
        self.sweep()
        self.assertEqual(worker._ADOPTED, {})
        os.chmod(self.fake.root / str(pid) / "stat", 0o600)
        self.sweep()
        self.assertEqual(worker._ADOPTED, {pid: 500})

    def test_exclude_from_reaping_nests_and_removes_its_entry(self) -> None:
        pid = self.child(self.BASE + 1)
        spawn = lambda: types.SimpleNamespace(pid=pid)  # noqa: E731
        with worker.exclude_from_reaping(spawn):
            with worker.exclude_from_reaping(spawn):
                self.assertEqual(worker._REAP_EXCLUDED, {pid: 2})
            self.assertEqual(worker._REAP_EXCLUDED, {pid: 1})
            self.sweep()
            self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(worker._REAP_EXCLUDED, {})
        self.sweep()
        self.assertEqual(self.waited, [pid])

    def test_without_children_files_the_full_scan_is_used_and_rate_limited(self) -> None:
        pid = self.child(self.BASE + 1, state="S")
        listings: list[int] = []
        real_list = worker._list_proc

        def list_proc(root):
            listings.append(1)
            return real_list(root)

        self.enterContext(unittest.mock.patch.object(worker, "_list_proc", list_proc))
        shutil.rmtree(self.fake.root / str(self.me) / "task")
        self.assertIsNone(worker._direct_children())
        self.sweep()
        self.assertEqual(worker._ADOPTED, {pid: 500})
        self.sweep()
        self.sweep()
        self.assertEqual(len(listings), 1, "the full scan ran more than once per interval")
        self.sweep(force_full=True)
        self.assertEqual(len(listings), 2)
        worker._last_full_scan -= worker._OWNERSHIP_SCAN_SECONDS
        self.sweep()
        self.assertEqual(len(listings), 3)


class ReapingPredicateTest(_ReapingSeamCase):
    """One predicate, applied by every ``_ADOPTED`` writer and by the
    reaper before each ``waitpid`` (LPR3-01)."""

    def _excluded_zombies(self) -> tuple[int, int, int]:
        own = self.child(self.BASE + 1, same_session=True)
        other_launch = self.child(self.BASE + 2)
        excluded = self.child(self.BASE + 3)
        worker._LAUNCH_CHILDREN[other_launch] = 500
        worker._REAP_EXCLUDED[excluded] = 1
        return own, other_launch, excluded

    def test_the_ownership_scan_records_none_of_them(self) -> None:
        pids = self._excluded_zombies()
        for pid in pids:
            self.fake.process(pid, state="S", pgrp=pid, start_ticks=500, ppid=self.me,
                              session=self.sid if pid == pids[0] else self.OTHER_SID)
        process = worker.WorkerProcess(pid=self.BASE + 99, pgid=self.BASE + 99, start_ticks=1, boot_id=None,
                                       pid_namespace=None, hostname=None, machine_id=None)
        ownership = worker._Ownership(tag="cp2-no-tag", worker_process=process, anchor_pid=None, baseline=set(),
                                      adopting=True)
        ownership.scan()
        self.assertEqual({e["source"] for e in ownership.entries()}, {"adopted"})  # still owned (I3)
        self.assertEqual(worker._ADOPTED, {})
        worker._reap_adopted()
        self.assertEqual(self.waited, [])

    def test_the_killed_children_reaper_records_none_of_them(self) -> None:
        self._excluded_zombies()
        worker._reap_killed_children(set(), pgid=self.BASE + 99)
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])

    def test_a_planted_record_that_is_excluded_is_dropped_without_a_waitpid(self) -> None:
        for pid in self._excluded_zombies():
            worker._ADOPTED[pid] = 500
        worker._reap_adopted()
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])

    def test_a_record_made_before_its_registration_is_dropped_once_registered(self) -> None:
        pid = self.child(self.BASE + 1)
        worker._ADOPTED[pid] = 500
        with worker.exclude_from_reaping(lambda: types.SimpleNamespace(pid=pid)):
            worker._reap_adopted()
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])

    def test_a_record_whose_pid_now_names_another_process_is_dropped(self) -> None:
        pid = self.child(self.BASE + 1, start_ticks=501)
        worker._ADOPTED[pid] = 500
        gone = self.BASE + 2
        worker._ADOPTED[gone] = 500
        worker._reap_adopted()
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])


class ForeignBornTest(_ReapingSeamCase):
    """Same-session orphans (Design B.2): collected only once seen born to
    another child."""

    def test_a_same_session_child_never_seen_as_a_descendant_is_left_alone(self) -> None:
        pid = self.child(self.BASE + 1, same_session=True)
        self.sweep()
        worker._ADOPTED[pid] = 500
        worker._reap_adopted()
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])

    def test_one_seen_as_a_descendant_is_recorded_and_reaped_once_reparented(self) -> None:
        parent = self.child(self.BASE + 1, same_session=True, state="S")
        grandchild = self.descendant(parent, self.BASE + 2)
        self.sweep()
        self.assertEqual(worker._FOREIGN_BORN, {grandchild: 700})
        self.assertEqual(worker._ADOPTED, {})
        # The parent exits (its own waiter collects it); the grandchild is
        # re-parented to this process and finishes.
        self.gone(parent)
        self.child(grandchild, same_session=True, start_ticks=700)
        self.reaped.add(grandchild)
        self.sweep()
        self.assertEqual(self.waited, [grandchild])
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(worker._FOREIGN_BORN, {})

    def test_a_reused_pid_does_not_inherit_the_entry(self) -> None:
        parent = self.child(self.BASE + 1, same_session=True, state="S")
        grandchild = self.descendant(parent, self.BASE + 2)
        self.sweep()
        self.gone(parent)
        self.child(grandchild, same_session=True, start_ticks=701)  # another process, same pid
        self.sweep()
        self.assertEqual(worker._FOREIGN_BORN, {})
        self.assertEqual(worker._ADOPTED, {})
        self.assertEqual(self.waited, [])

    def test_the_walk_does_not_descend_into_a_setsid_descendant(self) -> None:
        parent = self.child(self.BASE + 1, same_session=True, state="S")
        escaped = self.descendant(parent, self.BASE + 2, same_session=False)
        below = self.descendant(escaped, self.BASE + 3, same_session=True)
        self.sweep()
        self.assertEqual(worker._FOREIGN_BORN, {})
        self.assertNotIn(below, worker._FOREIGN_BORN)

    def test_a_listed_pid_reused_by_a_child_of_this_process_is_not_admitted(self) -> None:
        parent = self.child(self.BASE + 1, same_session=True, state="S")
        reused = self.descendant(parent, self.BASE + 2, ppid=self.me, state="Z")
        self.sweep()
        self.assertEqual(worker._FOREIGN_BORN, {})
        worker._ADOPTED[reused] = 700
        worker._reap_adopted()
        self.assertEqual(self.waited, [])
        self.descendant(parent, reused)  # the same pid, now with the walked parent as ppid
        self.sweep()
        self.assertEqual(worker._FOREIGN_BORN, {reused: 700})

    def test_the_pruning_pass_drops_gone_and_reused_entries_and_keeps_unreadable_ones(self) -> None:
        worker._FOREIGN_BORN.update({self.BASE + 1: 700, self.BASE + 2: 700, self.BASE + 3: 700})
        self.fake.process(self.BASE + 2, state="S", pgrp=1, start_ticks=701, session=self.sid)
        self.fake.process(self.BASE + 3, state="S", pgrp=1, start_ticks=700, session=self.sid)
        self.child(self.BASE + 9, same_session=True, state="S")  # any child: the sweep walks
        if os.geteuid() != 0:
            self.fake.lock_out(f"{self.BASE + 3}/stat")
        self.sweep()
        self.assertEqual(worker._FOREIGN_BORN, {self.BASE + 3: 700})



class IdleReaperTest(_ReapingSeamCase):
    """The between-launch reaper (Design B.5, I8)."""

    def _final_sweep(self) -> None:
        exclusions = worker._LaunchExclusions()
        exclusions.baseline = set()
        exclusions.final_sweep()

    def _wait_no_reaper(self) -> None:
        self.assertTrue(process_fixtures.wait_until(lambda: not _reaper_threads(), timeout=5),
                        "the between-launch reaper did not end")

    def test_it_starts_only_while_something_is_recorded_and_ends_once_nothing_is(self) -> None:
        self._final_sweep()
        self.assertEqual(_reaper_threads(), [])
        pid = self.child(self.BASE + 1, state="S")
        self._final_sweep()
        self.assertEqual(worker._ADOPTED, {pid: 500})
        [reaper] = _reaper_threads()
        worker._ensure_idle_reaper()
        self._final_sweep()
        self.assertEqual(_reaper_threads(), [reaper], "a second reaper started")
        self.reaped.add(pid)
        self._wait_no_reaper()
        self.assertEqual(worker._ADOPTED, {})
        self.assertIsNone(worker._idle_reaper)
        self.assertTrue(self.waited and all(p == pid for p in self.waited))

    def _blocking_reap(self) -> tuple[threading.Event, threading.Event]:
        """Wrap ``_reap_adopted`` so the reaper thread, once it has emptied
        ``_ADOPTED``, signals and blocks inside that iteration (still
        holding ``_SPAWN_LOCK``) until released."""
        in_exit, release = threading.Event(), threading.Event()
        real = worker._reap_adopted

        def reap() -> None:
            real()
            if threading.current_thread().name == worker._IDLE_REAPER_NAME and not worker._ADOPTED \
                    and not in_exit.is_set():
                in_exit.set()
                release.wait(10)

        self.enterContext(unittest.mock.patch.object(worker, "_reap_adopted", reap))
        self.addCleanup(release.set)
        return in_exit, release

    def test_a_sweep_during_the_reapers_exit_iteration_starts_a_new_reaper(self) -> None:
        in_exit, release = self._blocking_reap()
        first = self.child(self.BASE + 1)
        self.reaped.add(first)
        worker._ADOPTED[first] = 500
        worker._ensure_idle_reaper()
        [old] = _reaper_threads()
        self.assertTrue(in_exit.wait(10))
        second = self.child(self.BASE + 2, state="S")
        sweeper = threading.Thread(target=self._final_sweep, daemon=True)
        sweeper.start()
        sweeper.join(0.2)
        self.assertTrue(sweeper.is_alive(), "the sweep did not wait for the reaper's exit iteration")
        release.set()
        sweeper.join(10)
        old.join(10)
        self.assertFalse(old.is_alive())
        self.assertEqual(worker._ADOPTED, {second: 500})
        self.assertTrue(_reaper_threads(), "a recorded child is waiting with no reaper")
        self.reaped.add(second)
        self._wait_no_reaper()

    def test_a_sweep_before_the_exit_iteration_keeps_the_running_reaper(self) -> None:
        first = self.child(self.BASE + 1, state="S")
        worker._ADOPTED[first] = 500
        worker._ensure_idle_reaper()
        [reaper] = _reaper_threads()
        second = self.child(self.BASE + 2, state="S")
        self._final_sweep()
        self.assertEqual(worker._ADOPTED, {first: 500, second: 500})
        self.reaped.add(first)
        self.assertTrue(process_fixtures.wait_until(lambda: first not in worker._ADOPTED, timeout=5))
        self.assertEqual(_reaper_threads(), [reaper])
        self.reaped.add(second)
        self._wait_no_reaper()


class ReapingResetTest(unittest.TestCase):
    """``_reset_reaping_for_tests`` (through ``fixtures.isolate_idle_reaper``'s
    settle step) never strands a live recorded child and never touches one
    it may not reap (external plan review round 2, LPR7-02). Real
    children, the real ``/proc``."""

    def setUp(self) -> None:
        fixtures.isolate_idle_reaper(self)
        self.waits: list[tuple[int, int]] = []
        self.kills: list[int] = []
        real_waitpid, real_kill = os.waitpid, os.kill

        def waitpid(pid: int, options: int):
            self.waits.append((pid, options))
            return real_waitpid(pid, options)

        def kill(pid: int, sig: int) -> None:
            self.kills.append(pid)
            real_kill(pid, sig)

        self.enterContext(unittest.mock.patch.object(worker.os, "waitpid", waitpid))
        self.enterContext(unittest.mock.patch.object(worker.os, "kill", kill))

    def _sleeper(self, *, new_session: bool) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"],
                                start_new_session=new_session)

        def cleanup() -> None:
            if proc.returncode is None and _real_stat(proc.pid) is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(proc.pid, signal.SIGKILL)
                proc.wait()
            proc.returncode = proc.returncode if proc.returncode is not None else -9
        self.addCleanup(cleanup)
        return proc

    def test_a_live_recorded_child_is_killed_and_collected_and_a_planted_pid_untouched(self) -> None:
        child = self._sleeper(new_session=True)
        planted = 4_199_999
        self.assertIsNone(_real_stat(planted))
        with worker._SPAWN_LOCK:
            worker._ADOPTED[child.pid] = _real_stat(child.pid).start_ticks
            worker._ADOPTED[planted] = 1
            worker._ADOPTED[1] = 1  # a real process, not a child of this one
            worker._ensure_idle_reaper()
        self.assertTrue(_reaper_threads())
        collected = worker._reset_reaping_for_tests(5.0)
        self.assertEqual(_reaper_threads(), [])
        self.assertEqual(collected, [child.pid])
        self.assertEqual([w for w in self.waits if w == (child.pid, 0)], [(child.pid, 0)])
        self.assertIsNone(_real_stat(child.pid), "the child is still there (a zombie?)")
        child.returncode = -9
        self.assertNotIn(planted, [pid for pid, _options in self.waits])
        self.assertNotIn(1, [pid for pid, _options in self.waits])
        self.assertEqual(self.kills, [child.pid])
        self.assertEqual((worker._ADOPTED, worker._FOREIGN_BORN), ({}, {}))

    def test_a_record_with_other_start_ticks_is_dropped_untouched(self) -> None:
        child = self._sleeper(new_session=True)
        worker._ADOPTED[child.pid] = _real_stat(child.pid).start_ticks + 1
        self.assertEqual(worker._reset_reaping_for_tests(5.0), [])
        self.assertEqual((self.waits, self.kills), ([], []))
        self.assertEqual(worker._ADOPTED, {})
        self.assertIsNone(child.poll())

    def test_a_planted_same_session_child_is_dropped_untouched(self) -> None:
        child = self._sleeper(new_session=False)
        worker._ADOPTED[child.pid] = _real_stat(child.pid).start_ticks
        self.assertEqual(worker._reset_reaping_for_tests(5.0), [])
        self.assertEqual((self.waits, self.kills), ([], []))
        self.assertEqual(worker._ADOPTED, {})
        self.assertIsNone(child.poll(), "the waited-for child was ended")



def _admitted_spy(case: unittest.TestCase) -> list[int]:
    """Wrap ``_record_adopted``; returns the list of pids it admitted."""
    admitted: list[int] = []
    real = worker._record_adopted

    def record(pid: int, stat) -> None:
        with worker._SPAWN_LOCK:
            real(pid, stat)
            if pid in worker._ADOPTED:
                admitted.append(pid)

    case.enterContext(unittest.mock.patch.object(worker, "_record_adopted", record))
    return admitted


def _await_file(path: Path, timeout: float = 10.0) -> bool:
    return process_fixtures.wait_until(path.exists, timeout=timeout, interval=0.01)


class ReapingLaunchTest(_SupervisedCase):
    """The reaping around real launches (Design C): children the process
    waits for keep their exit statuses (I6), spawns have no window before
    their registration (LPR3-02), adopted same-session grandchildren and
    between-launch orphans are collected (I2, I5, I8), the final sweep runs
    after the subreaper is given up, and publications are unchanged (I3)."""

    def setUp(self) -> None:
        fixtures.isolate_idle_reaper(self)
        super().setUp()

    # -- children the process waits for (I6) --------------------------------

    def test_a_child_a_callback_starts_and_another_thread_waits_for_keeps_its_status(self) -> None:
        admitted = _admitted_spy(self)
        sweeps: list[float] = []
        real_collect = worker._collect_children

        def collect(*args, **kwargs) -> None:
            sweeps.append(time.monotonic())
            real_collect(*args, **kwargs)

        self.patch(worker, "_collect_children", collect)
        waited: dict = {}

        def wait_for(child: subprocess.Popen) -> None:
            waited["returncode"] = child.wait()
            waited["exited"] = time.monotonic()

        def on_state(state: str, details: dict) -> None:
            if state == worker.WAITING and "child" not in waited:
                child = subprocess.Popen([sys.executable, "-c", "import time, sys; time.sleep(5); sys.exit(45)"])
                waited.update(child=child, started=time.monotonic())
                threading.Thread(target=wait_for, args=(child,), daemon=True).start()

        result = self.launch([[{"step": "bash_bg", "seconds": 7}], [{"step": "text"}]], on_state=on_state)
        self.assertEqual(result.outcome, worker.SUCCESS)
        self.assertTrue(process_fixtures.wait_until(lambda: "returncode" in waited, timeout=10))
        self.assertEqual(waited["returncode"], 45)
        self.assertGreaterEqual(len([at for at in sweeps if waited["started"] <= at <= waited["exited"]]), 20)
        self.assertNotIn(waited["child"].pid, admitted)

    def test_a_new_session_child_under_exclude_from_reaping_keeps_its_status(self) -> None:
        waited: dict = {}

        def run_excluded() -> None:
            spawn = lambda: subprocess.Popen(  # noqa: E731
                [sys.executable, "-c", "import time, sys; time.sleep(1.5); sys.exit(45)"], start_new_session=True)
            with worker.exclude_from_reaping(spawn) as child:
                waited["pid"] = child.pid
                waited["returncode"] = child.wait()

        def on_state(state: str, details: dict) -> None:
            if state == worker.WAITING and "thread" not in waited:
                waited["thread"] = threading.Thread(target=run_excluded, daemon=True)
                waited["thread"].start()

        admitted = _admitted_spy(self)
        result = self.launch([[{"step": "bash_bg", "seconds": 3}], [{"step": "text"}]], on_state=on_state)
        self.assertEqual(result.outcome, worker.SUCCESS)
        waited["thread"].join(10)
        self.assertEqual(waited["returncode"], 45)
        self.assertNotIn(waited["pid"], admitted)

    def test_the_same_new_session_child_without_the_exclusion_is_recorded(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"], start_new_session=True)
        self.extra_pids.append(child.pid)
        self.addCleanup(setattr, child, "returncode", -9)
        worker._collect_children({}, set(), force_full=True)
        self.assertEqual(worker._ADOPTED.get(child.pid), _real_stat(child.pid).start_ticks)

    def test_overlapping_launches_never_record_each_others_worker_or_anchor(self) -> None:
        admitted = _admitted_spy(self)
        spawned: dict[str, dict] = {}
        results: dict[str, object] = {}
        events = {name: threading.Event() for name in ("a", "b")}

        def run(name: str, turns: list) -> None:
            def on_spawn(worker_process, *, anchor=None, ownership_tag=None) -> None:
                spawned[name] = {"worker": worker_process, "anchor": anchor, "tag": ownership_tag}
                self.spawns.append(spawned[name])
                events[name].set()
            results[name] = worker.launch("do the bounded thing", cwd=self.dir, permission_mode="acceptEdits",
                                          timeout=60, claude_bin=str(FAKE_CLAUDE), on_spawn=on_spawn,
                                          **_stream_paths(self.dir))

        threads = {}
        with _environment({"FAKE_CLAUDE_EXIT": "3",
                           "FAKE_CLAUDE_TURNS": json.dumps([[{"step": "bash_bg", "seconds": 2}], [{"step": "text"}]])}):
            threads["a"] = threading.Thread(target=run, args=("a", None), daemon=True)
            threads["a"].start()
            self.assertTrue(events["a"].wait(20))
            os.environ["FAKE_CLAUDE_EXIT"] = "5"
            os.environ["FAKE_CLAUDE_TURNS"] = json.dumps([[{"step": "bash_bg", "seconds": 0.5}], [{"step": "text"}]])
            threads["b"] = threading.Thread(target=run, args=("b", None), daemon=True)
            threads["b"].start()
            self.assertTrue(events["b"].wait(20))
            for thread in threads.values():
                thread.join(60)
        self.assertEqual({name: result.returncode for name, result in results.items()}, {"a": 3, "b": 5})
        launch_children = {spawn[key].pid for spawn in spawned.values() for key in ("worker", "anchor")}
        self.assertFalse(launch_children & set(admitted), "a launch recorded another launch's worker or anchor")

    # -- no spawn-to-registration window (LPR3-02) ---------------------------

    def _blocked_sweeper(self) -> threading.Thread:
        def sweep() -> None:
            worker._collect_children({}, set(), force_full=True)
            worker._reap_adopted()
        thread = threading.Thread(target=sweep, daemon=True)
        thread.start()
        thread.join(0.2)
        return thread

    def test_a_sweep_waits_for_an_excluded_spawn_to_be_registered(self) -> None:
        admitted = _admitted_spy(self)
        seen: dict = {}

        def spawn() -> subprocess.Popen:
            child = subprocess.Popen([sys.executable, "-c", "import time, sys; time.sleep(0.5); sys.exit(45)"],
                                     start_new_session=True)
            self.assertTrue(process_fixtures.wait_until(lambda: child.pid in _my_children()))
            seen["sweeper"] = self._blocked_sweeper()
            seen["blocked"] = seen["sweeper"].is_alive()
            return child

        with worker.exclude_from_reaping(spawn) as child:
            seen["sweeper"].join(10)
            self.assertEqual(child.wait(), 45)
        self.assertTrue(seen["blocked"], "the sweep ran between the spawn and its registration")
        self.assertNotIn(child.pid, admitted)

    def test_a_sweep_waits_for_the_workers_and_the_anchors_registration(self) -> None:
        admitted = _admitted_spy(self)
        blocked: dict[str, bool] = {}
        sweepers: list[threading.Thread] = []
        real_popen, real_spawn_anchor = subprocess.Popen, worker._spawn_anchor

        def popen(args, *a, **kw):
            proc = real_popen(args, *a, **kw)
            if args and args[0] == str(FAKE_CLAUDE) and "worker" not in blocked:
                sweepers.append(self._blocked_sweeper())
                blocked["worker"] = sweepers[-1].is_alive()
            return proc

        def spawn_anchor(*a, **kw):
            anchor_proc = real_spawn_anchor(*a, **kw)
            sweepers.append(self._blocked_sweeper())
            blocked["anchor"] = sweepers[-1].is_alive()
            return anchor_proc

        self.patch(subprocess, "Popen", popen)
        self.patch(worker, "_spawn_anchor", spawn_anchor)
        result = self.launch([[{"step": "text"}]], env={"FAKE_CLAUDE_EXIT": "3"})
        for sweeper in sweepers:
            sweeper.join(10)
        self.assertEqual(blocked, {"worker": True, "anchor": True})
        self.assertEqual(result.returncode, 3)
        self.assertFalse({self.spawn["worker"].pid, self.spawn["anchor"].pid} & set(admitted))

    # -- adopted children -----------------------------------------------------

    def test_an_adopted_same_session_grandchild_is_reaped_and_its_parent_keeps_its_status(self) -> None:
        launch_release, parent_release, grandchild_release = (self.dir / name for name in (
            "launch-release", "parent-release", "grandchild-release"))
        command = f"while [ ! -e {shlex.quote(str(launch_release))} ]; do sleep 0.05; done"
        self.addCleanup(launch_release.touch)
        results: dict = {}
        waiting = threading.Event()

        def run_launch() -> None:
            results["result"] = self.launch([[{"step": "bash_bg", "command": command}], [{"step": "text"}]],
                                            on_state=lambda state, _d: waiting.set() if state == worker.WAITING
                                            else None)
            results["returned"] = time.monotonic()

        launcher = threading.Thread(target=run_launch, daemon=True)
        launcher.start()
        self.assertTrue(waiting.wait(20))
        poll = "import os, sys, time\nwhile not os.path.exists(sys.argv[1]): time.sleep(0.01)\n"
        script = (
            "import subprocess, sys, os, time\n"
            f"g = subprocess.Popen([sys.executable, '-c', {poll!r}, {str(grandchild_release)!r}])\n"
            "print(g.pid, flush=True)\n"
            f"while not os.path.exists({str(parent_release)!r}): time.sleep(0.01)\n"
            "sys.exit(45)\n"
        )
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        self.addCleanup(grandchild_release.touch)
        self.addCleanup(parent_release.touch)
        grandchild = int(parent.stdout.readline())
        self.extra_pids.append(grandchild)
        ticks = _real_stat(grandchild).start_ticks
        order: list[str] = []
        self.assertTrue(process_fixtures.wait_until(lambda: worker._FOREIGN_BORN.get(grandchild) == ticks,
                                                    timeout=10), "no sweep saw the grandchild as a descendant")
        order.append("foreign-born")
        parent_release.touch()
        self.assertEqual(parent.wait(10), 45)
        parent.stdout.close()
        self.assertTrue(process_fixtures.wait_until(lambda: worker._ADOPTED.get(grandchild) == ticks, timeout=10),
                        "the re-parented grandchild was never recorded")
        order.append("adopted")
        stat = _real_stat(grandchild)
        self.assertEqual((stat.state != "Z", stat.ppid), (True, os.getpid()))
        grandchild_release.touch()
        self.assertTrue(process_fixtures.wait_until(lambda: _real_stat(grandchild) is None, timeout=10))
        collected = time.monotonic()
        launch_release.touch()
        launcher.join(30)
        self.assertEqual(order, ["foreign-born", "adopted"])
        self.assertLess(collected, results["returned"])
        self.assertEqual(results["result"].outcome, worker.SUCCESS)

    def test_a_recorded_orphan_alive_at_the_end_of_a_launch_is_collected_between_launches(self) -> None:
        pid_file = self.dir / "orphan.pid"
        first = self.launch([[{"step": "bash_bg", "seconds": 0.3, "orphan": "daemon", "argv0": "gpg-agent",
                               "orphan_seconds": 2, "orphan_pid_file": str(pid_file)}], [{"step": "text"}]])
        self.assertEqual(first.outcome, worker.SUCCESS)
        orphan = int(pid_file.read_text())
        self.extra_pids.append(orphan)
        self.assertIn(orphan, worker._ADOPTED, "the orphan left alive was not recorded")
        self.assertIsNotNone(_real_stat(orphan))
        baselines: list[set[int]] = []
        real_own_children = worker._own_children

        def own_children() -> set[int]:
            baseline = real_own_children()
            baselines.append(baseline)
            if orphan in baseline:
                self.assertIn(orphan, worker._ADOPTED, "the orphan is in the next baseline unrecorded")
            return baseline

        self.patch(worker, "_own_children", own_children)
        second = self.launch([[{"step": "bash_bg", "seconds": 3}], [{"step": "text"}]])
        self.assertEqual(second.outcome, worker.SUCCESS)
        self.assertIsNone(_real_stat(orphan), "the orphan is still there (a zombie?)")
        self.assertNotIn(orphan, worker._ADOPTED)

    # -- the final sweep (I5) -------------------------------------------------

    def _final_sweep_order(self) -> list:
        """Patch the subreaper and the children seams: a zombie child
        appears only once the subreaper has been reset. Returns the event
        list: the two ``_set_child_subreaper`` calls and the record."""
        events: list = []
        planted = 4_199_998
        real_set, real_children, real_read = (worker._set_child_subreaper, worker._direct_children,
                                              worker._read_stat)
        real_record = worker._record_adopted

        def set_subreaper(value: int) -> bool:
            events.append(("set", value))
            return real_set(value)

        def direct_children(pid=None):
            children = real_children(pid)
            if pid is None and children is not None and len([e for e in events if e[0] == "set"]) >= 2:
                children = children | {planted}
            return children

        def read_stat(path):
            if str(path) == f"/proc/{planted}/stat":
                return worker._ProcStat(state="Z", pgrp=planted, start_ticks=1, session=planted, ppid=os.getpid())
            return real_read(path)

        def record(pid: int, stat) -> None:
            if pid == planted:
                events.append("record")
            real_record(pid, stat)

        self.patch(worker, "_set_child_subreaper", set_subreaper)
        self.patch(worker, "_direct_children", direct_children)
        self.patch(worker, "_read_stat", read_stat)
        self.patch(worker, "_record_adopted", record)
        return events

    def _assert_swept_after_the_reset(self, events: list) -> None:
        self.assertEqual(events[:2], [("set", 1), ("set", 0)], events)
        self.assertIn("record", events[2:], "the final sweep did not see the child re-parented before the reset")

    def test_the_final_sweep_runs_after_the_subreaper_is_given_up(self) -> None:
        events = self._final_sweep_order()
        self.assertEqual(self.launch([[{"step": "text"}]]).outcome, worker.SUCCESS)
        self._assert_swept_after_the_reset(events)

    def test_the_final_sweep_runs_after_a_drain_detach(self) -> None:
        self.patch(worker, "DRAIN_DETACH_SECONDS", 1)
        pid_file = self.dir / "orphan.pid"
        events = self._final_sweep_order()
        result = self.launch([[{"step": "bash_bg", "seconds": 0.3, "orphan": "daemon",
                                "orphan_pid_file": str(pid_file)}], [{"step": "text"}]])
        self.extra_pids.append(int(pid_file.read_text()))
        self.assertIsInstance(result, worker.DrainDetached)
        self._assert_swept_after_the_reset(events)

    def test_the_final_sweep_runs_after_an_exception(self) -> None:
        events = self._final_sweep_order()

        def on_state(state: str, details: dict) -> None:
            raise RuntimeError("the flush failed (test)")

        with self.assertRaises(RuntimeError):
            self.launch([[{"step": "text"}]], on_state=on_state)
        self._assert_swept_after_the_reset(events)

    def test_a_failed_capture_leaves_the_worker_excluded_from_the_final_sweep(self) -> None:
        admitted = _admitted_spy(self)
        captured: list[int] = []

        def capture(pid: int):
            captured.append(pid)
            os.killpg(pid, signal.SIGKILL)
            self.assertTrue(process_fixtures.wait_until(lambda: _real_stat(pid).state == "Z", timeout=10))
            raise RuntimeError("capture failed (test)")

        around_sweep: list[str | None] = []
        real_final_sweep = worker._LaunchExclusions.final_sweep

        def final_sweep(exclusions) -> None:
            # The launch's own Popen still holds the worker's status here;
            # it is released (and collects it) only once the error unwinds.
            around_sweep.append(getattr(_real_stat(captured[0]), "state", None))
            real_final_sweep(exclusions)
            around_sweep.append(getattr(_real_stat(captured[0]), "state", None))

        self.patch(worker, "capture_worker_process", capture)
        self.patch(worker._LaunchExclusions, "final_sweep", final_sweep)
        with self.assertRaises(RuntimeError):
            self.launch([[{"step": "text"}]])
        [pid] = captured
        self.assertNotIn(pid, admitted)
        self.assertEqual(around_sweep, ["Z", "Z"], "the final sweep reaped the worker")

    # -- publications are unchanged (I3) --------------------------------------

    def test_the_published_sequence_is_the_same_with_and_without_the_sweep(self) -> None:
        turns = [[{"step": "bash_bg", "seconds": 0.5}], [{"step": "text"}]]

        def published() -> list:
            return [(state, sorted(e["source"] for e in details["owned_processes"]), details["scan"])
                    for _at, state, details in self.log]

        self.assertEqual(self.launch(turns).outcome, worker.SUCCESS)
        with_sweep = published()
        self.log.clear()
        with unittest.mock.patch.object(worker._LaunchExclusions, "collect", lambda self, **kw: None):
            self.assertEqual(self.launch(turns).outcome, worker.SUCCESS)
        self.assertEqual(published(), with_sweep)


class FollowerStartsNoProcessTest(test_observe._FollowCase):
    """I6: ``--follow``'s renderer (``observe.follow_run``, the one
    Controller thread besides the supervising one) starts no process."""

    def test_follow_run_renders_a_run_without_starting_a_process(self) -> None:
        self.write_run("r1", state="ended", exit_code=10, job_ids=["j1"])
        self.run_event("r1", 1, "run_started")
        self.run_event("r1", 2, "job_started", job_id="j1")
        self.write_job("j1", status="FINISHED")
        self.job_event("j1", 1, "planned", command="/milestone-plan wi")
        self.worker_line("j1", test_observe._assistant({"type": "text", "text": "hello"}))
        self.run_event("r1", 3, "job_ended", job_id="j1", status="FINISHED")
        self.run_event("r1", 4, "run_ended", exit_code=10)
        spawned: list[str] = []

        def forbidden(name: str):
            def call(*args, **kwargs):
                spawned.append(name)
                raise AssertionError(f"{name} called by the follower")
            return call

        for target, name in ((subprocess, "Popen"), (os, "fork"), (os, "posix_spawn"), (os, "system")):
            self.enterContext(unittest.mock.patch.object(target, name, forbidden(name)))
        thread = threading.Thread(target=observe.follow_run, args=(self.runtime_root, "r1", self.sink),
                                  kwargs={"from_start": True}, daemon=True)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(spawned, [])
        self.assertEqual(self.bodies()[-1], "run ended: exit 10")
        self.assertIn("hello", self.bodies())


if __name__ == "__main__":
    unittest.main()
