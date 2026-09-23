"""Tests for the fresh Claude worker abstraction (capability 4,
``controller.worker``).

``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``'s CP5 section names these
cases: success JSON classifies ``SUCCESS`` with fields extracted; a
non-zero exit classifies ``FAILURE``; exit 0 with ``is_error: true``
classifies ``FAILURE``; non-JSON stdout on exit 0 classifies
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

import contextlib
import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import lock, worker  # noqa: E402
from controller.errors import UserOnlyCommandError, WorkerLaunchError  # noqa: E402
from tests import process_fixtures  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"


def _launch(task="do the bounded thing", *, cwd, env_overrides, timeout=10, claude_bin=None):
    old = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    try:
        return worker.launch(
            task, cwd=cwd, permission_mode="acceptEdits", timeout=timeout,
            claude_bin=claude_bin or str(FAKE_CLAUDE),
        )
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class SuccessTest(unittest.TestCase):
    def test_success_json_classifies_success_with_fields_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            body = {
                "session_id": "abc123", "is_error": False, "subtype": "success",
                "terminal_reason": "end_turn", "stop_reason": None, "result": "did it",
                "num_turns": 3, "permission_denials": ["x"], "total_cost_usd": 0.01,
                "duration_ms": 500,
            }
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": json.dumps(body)})
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


class FailureTest(unittest.TestCase):
    def test_nonzero_exit_classifies_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_EXIT": "1"})
            self.assertEqual(result.outcome, worker.FAILURE)
            self.assertEqual(result.returncode, 1)

    def test_exit_zero_with_is_error_true_classifies_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            body = {"session_id": "s", "is_error": True, "result": "went wrong"}
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": json.dumps(body)})
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

    def test_json_missing_required_fields_classifies_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": json.dumps({"foo": "bar"})})
            self.assertEqual(result.outcome, worker.AMBIGUOUS)

    def test_json_array_classifies_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": "[1, 2, 3]\n"})
            self.assertEqual(result.outcome, worker.AMBIGUOUS)

    def test_two_concatenated_json_documents_classify_ambiguous(self) -> None:
        # REQ-26: the candidate span is exactly one JSON document -- trailing
        # "extra data" after a complete value must never be silently
        # dropped in favour of the first document.
        with tempfile.TemporaryDirectory() as td:
            body = json.dumps({"session_id": "s", "is_error": False}) + json.dumps({"session_id": "t", "is_error": False})
            result = _launch(cwd=td, env_overrides={"FAKE_CLAUDE_STDOUT": body})
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

    def test_stdin_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "diag.json"
            _launch(cwd=td, env_overrides={"FAKE_CLAUDE_DIAG_FILE": str(marker)})
            diag = json.loads(marker.read_text())
            self.assertTrue(diag["stdin_at_eof"])

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


class WorkerLaunchErrorTest(unittest.TestCase):
    def test_nonexistent_claude_bin_raises_worker_launch_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no-such-binary"
            with self.assertRaises(WorkerLaunchError):
                worker.launch(
                    "do the thing", cwd=td, permission_mode="acceptEdits", timeout=5,
                    claude_bin=str(missing),
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
        self.spawned_groups: list[int] = []
        self.addCleanup(self._end_spawned)

    def _end_spawned(self) -> None:
        self.release.touch()
        for pgid in self.spawned_groups:
            process_fixtures.kill_group(pgid)

    def _launch(self, *, on_spawn=None, pass_fds=(), timeout=30, env=None) -> worker.WorkerResult:
        overrides = {
            "FAKE_CLAUDE_DIAG_FILE": str(self.diag),
            "FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release),
            **(env or {}),
        }

        def recording_on_spawn(worker_process: worker.WorkerProcess) -> None:
            self.spawned_groups.append(worker_process.pgid or worker_process.pid)
            if on_spawn is not None:
                on_spawn(worker_process)

        with _environment(overrides):
            return worker.launch(
                "do the bounded thing", cwd=self.dir, permission_mode="acceptEdits", timeout=timeout,
                claude_bin=str(FAKE_CLAUDE), pass_fds=pass_fds, on_spawn=recording_on_spawn,
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


if __name__ == "__main__":
    unittest.main()
