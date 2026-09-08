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

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import worker  # noqa: E402
from controller.errors import UserOnlyCommandError, WorkerLaunchError  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
