"""Tests for the durable lifecycle event logs' shape and write semantics
(``workflow-controller-release-runtime-observability`` CP5, the log-shape
part): ``runtime.append_jsonl`` and the best-effort wrapper every event
append and run-record write goes through. CP6 adds the observation surface
(``controller/observe.py``) to this module.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import stat
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import runtime  # noqa: E402
from controller.errors import RuntimeContainmentError  # noqa: E402


class _RuntimeRootCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime_root = Path(self._tmp.name) / "runtime"
        self.runtime_root.mkdir()
        latch = unittest.mock.patch.object(runtime, "_best_effort_warned", False)
        latch.start()
        self.addCleanup(latch.stop)


class AppendJsonlTest(_RuntimeRootCase):
    def test_each_call_appends_one_line_to_a_0600_file(self) -> None:
        path = runtime.append_jsonl(self.runtime_root, "jobs/j1/events.jsonl", {"seq": 1, "event": "planned"})
        runtime.append_jsonl(self.runtime_root, "jobs/j1/events.jsonl", {"seq": 2, "event": "launched"})
        self.assertEqual(path, (self.runtime_root / "jobs" / "j1" / "events.jsonl").resolve())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        lines = path.read_text().splitlines()
        self.assertEqual([json.loads(line) for line in lines],
                         [{"seq": 1, "event": "planned"}, {"seq": 2, "event": "launched"}])
        self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_an_existing_log_is_appended_to_never_truncated(self) -> None:
        path = self.runtime_root / "runs" / "r1" / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"seq": 1}\n')
        runtime.append_jsonl(self.runtime_root, "runs/r1/events.jsonl", {"seq": 2})
        self.assertEqual(path.read_text(), '{"seq": 1}\n{"seq": 2}\n')

    def test_a_path_outside_the_runtime_root_is_refused(self) -> None:
        with self.assertRaises(RuntimeContainmentError):
            runtime.append_jsonl(self.runtime_root, "../escape.jsonl", {"seq": 1})
        self.assertFalse((self.runtime_root.parent / "escape.jsonl").exists())

    def test_a_non_json_value_raises_before_the_file_is_touched(self) -> None:
        with self.assertRaises(TypeError):
            runtime.append_jsonl(self.runtime_root, "jobs/j1/events.jsonl", {"detail": object()})
        self.assertFalse((self.runtime_root / "jobs" / "j1" / "events.jsonl").exists())


class BestEffortTest(_RuntimeRootCase):
    def _warnings(self, stderr: io.StringIO) -> list[str]:
        return [line for line in stderr.getvalue().splitlines()
                if line.startswith("workflow-controller: warning: could not write ")]

    def test_each_failure_class_is_swallowed_and_warned_once_per_process(self) -> None:
        failures = [OSError(errno.EIO, "injected"), RuntimeContainmentError("injected", evidence={}),
                    TypeError("injected")]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            for failure in failures:
                with unittest.mock.patch.object(runtime, "append_jsonl", side_effect=failure):
                    self.assertFalse(runtime.append_jsonl_best_effort(self.runtime_root, "jobs/j/events.jsonl", {}))
            with unittest.mock.patch.object(runtime, "write_json", side_effect=OSError(errno.ENOSPC, "full")):
                self.assertFalse(runtime.write_json_best_effort(self.runtime_root, "runs/r.json", {}))
        [warning] = self._warnings(stderr)
        self.assertIn(str(self.runtime_root / "jobs/j/events.jsonl"), warning)
        self.assertIn("injected", warning)

    def test_a_real_non_json_detail_is_swallowed(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertFalse(runtime.append_jsonl_best_effort(self.runtime_root, "jobs/j/events.jsonl",
                                                              {"detail": object()}))
        self.assertEqual(len(self._warnings(stderr)), 1)

    def test_success_writes_and_warns_nothing(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertTrue(runtime.append_jsonl_best_effort(self.runtime_root, "jobs/j/events.jsonl", {"seq": 1}))
            self.assertTrue(runtime.write_json_best_effort(self.runtime_root, "runs/r.json", {"state": "running"}))
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(runtime.read_json(self.runtime_root / "runs" / "r.json"), {"state": "running"})

    def test_keyboard_interrupt_and_system_exit_propagate(self) -> None:
        for interrupt in (KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(interrupt=type(interrupt).__name__):
                with unittest.mock.patch.object(runtime, "append_jsonl", side_effect=interrupt), \
                        self.assertRaises(type(interrupt)):
                    runtime.append_jsonl_best_effort(self.runtime_root, "jobs/j/events.jsonl", {})

    def test_a_broken_stderr_does_not_raise_out_of_the_warning(self) -> None:
        class BrokenStderr(io.StringIO):
            def write(self, text):
                raise OSError(errno.EPIPE, "Broken pipe")

        with contextlib.redirect_stderr(BrokenStderr()), \
                unittest.mock.patch.object(runtime, "append_jsonl", side_effect=OSError(errno.EIO, "x")):
            self.assertFalse(runtime.append_jsonl_best_effort(self.runtime_root, "jobs/j/events.jsonl", {}))


if __name__ == "__main__":
    unittest.main()
