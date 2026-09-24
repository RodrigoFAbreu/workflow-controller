"""Tests for the durable lifecycle event logs' shape and write semantics
(``workflow-controller-release-runtime-observability`` CP5, the log-shape
part): ``runtime.append_jsonl`` and the best-effort wrapper every event
append and run-record write goes through -- and for CP6's observation
surface, ``controller/observe.py``: the tailer, the normaliser and
renderers, the run and job followers, the heartbeat, the Controller's
pid-level liveness and the in-process renderer's descriptor sink.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, observe, runtime, worker  # noqa: E402
from controller.errors import RuntimeContainmentError  # noqa: E402
from tests import fake_claude, process_fixtures  # noqa: E402


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


# ---------------------------------------------------------------------------
# CP6: the observation surface.
# ---------------------------------------------------------------------------


def _texts(source: str, *objects, tools: dict | None = None) -> list[str]:
    """Each object's rendered presentation text, without the time/source
    prefix."""
    tools = {} if tools is None else tools
    out = []
    for obj in objects:
        line = obj if isinstance(obj, str) else json.dumps(obj)
        for event in observe.normalise(source, line, tools):
            out.append(observe._body(event))
    return out


def _assistant(*blocks) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def _tool_result(tool_use_id: str, content, *, is_error: bool = False) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}]}}


class NormaliseRenderTest(unittest.TestCase):
    """Every row of the plan's presentation table."""

    def test_system_init(self) -> None:
        self.assertEqual(_texts("worker", {"type": "system", "subtype": "init", "session_id": "s-1",
                                           "model": "m-1", "permissionMode": "auto"}),
                         ["worker session s-1 model m-1 permission auto"])

    def test_assistant_text_is_verbatim_and_wrapped(self) -> None:
        self.assertEqual(_texts("worker", _assistant({"type": "text", "text": "Working on it."})),
                         ["Working on it."])
        long_line = "word " * 60
        [text] = _texts("worker", _assistant({"type": "text", "text": long_line}))
        self.assertGreater(len(text.splitlines()), 1)
        self.assertTrue(all(len(line) <= observe.WRAP_WIDTH for line in text.splitlines()))
        self.assertEqual("".join(text.splitlines()), long_line)

    def test_tool_use_summaries(self) -> None:
        cases = [
            ("Bash", {"command": "git status", "description": "x"}, "tool Bash: git status"),
            ("Read", {"file_path": "/a/b.py"}, "tool Read: /a/b.py"),
            ("Edit", {"file_path": "/a/c.py", "old_string": "x"}, "tool Edit: /a/c.py"),
            ("Write", {"file_path": "/a/d.py", "content": "x"}, "tool Write: /a/d.py"),
            ("Grep", {"pattern": "def main", "path": "."}, "tool Grep: def main"),
            ("Glob", {"pattern": "**/*.py"}, "tool Glob: **/*.py"),
            ("Task", {"b": 1, "a": "x"}, 'tool Task: {"a":"x","b":1}'),
        ]
        for name, tool_input, expected in cases:
            with self.subTest(tool=name):
                self.assertEqual(_texts("worker", _assistant(
                    {"type": "tool_use", "id": "t", "name": name, "input": tool_input})), [expected])

    def test_tool_use_summary_truncates_at_200_characters(self) -> None:
        for length, truncated in ((200, False), (201, True)):
            with self.subTest(length=length):
                [text] = _texts("worker", _assistant(
                    {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "x" * length}}))
                summary = text.removeprefix("tool Bash: ")
                self.assertEqual(len(summary), 200)
                self.assertEqual(summary.endswith("..."), truncated)
        [text] = _texts("worker", _assistant(
            {"type": "tool_use", "id": "t", "name": "Other", "input": {"k": "v" * 500}}))
        self.assertEqual(len(text.removeprefix("tool Other: ")), 200)

    def test_tool_result_names_its_tool_and_keeps_the_first_20_lines(self) -> None:
        tools: dict = {}
        _texts("worker", _assistant({"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                     "input": {"command": "seq 25"}}), tools=tools)
        [text] = _texts("worker", _tool_result("toolu_1", "\n".join(str(n) for n in range(1, 26))), tools=tools)
        lines = text.splitlines()
        self.assertEqual(lines[0], "result Bash ok: 1")
        self.assertEqual(lines[1:20], [str(n) for n in range(2, 21)])
        self.assertEqual(lines[20], "... (5 more lines)")
        self.assertEqual(len(lines), 21)
        [exact] = _texts("worker", _tool_result("toolu_1", "\n".join("x" * 20)), tools=tools)
        self.assertNotIn("more lines", exact)
        [error] = _texts("worker", _tool_result("toolu_1", [{"type": "text", "text": "boom"}], is_error=True),
                         tools=tools)
        self.assertEqual(error, "result Bash error: boom")

    def test_thinking_blocks_produce_no_output_at_all(self) -> None:
        for block in ({"type": "thinking", "thinking": "secret plan", "signature": "sig"},
                      {"type": "redacted_thinking", "data": "opaque"}):
            with self.subTest(block=block["type"]):
                line = json.dumps(_assistant(block))
                self.assertEqual(observe.normalise("worker", line), [])
        mixed = json.dumps(_assistant({"type": "thinking", "thinking": "secret plan"},
                                      {"type": "text", "text": "visible"}))
        events = observe.normalise("worker", mixed)
        self.assertEqual([observe._body(e) for e in events], ["visible"])
        rendered = "\n".join(observe.render_text(e) + observe.render_json(e) for e in events)
        self.assertNotIn("secret plan", rendered)
        self.assertNotIn("thinking", rendered)

    def test_result_event(self) -> None:
        self.assertEqual(_texts("worker", fake_claude.result_event(num_turns=3, total_cost_usd=0.25,
                                                                   duration_ms=1200)),
                         ["worker result: success, is_error=False, turns=3, cost=0.25, duration=1200ms"])

    def test_stderr_lines(self) -> None:
        self.assertEqual(_texts("stderr", "something went wrong"), ["stderr: something went wrong"])

    def test_job_and_run_events(self) -> None:
        self.assertEqual(_texts("job", {"v": 1, "seq": 9, "at": "2026-01-01T00:00:00Z", "job_id": "j1",
                                        "event": "finished", "observed_phase": "PLANNING",
                                        "observed_phase_after": "AWAITING_LOCAL_PLAN_REVIEW",
                                        "transition_verified": True}),
                         ["job j1 FINISHED (PLANNING -> AWAITING_LOCAL_PLAN_REVIEW verified)"])
        self.assertEqual(_texts("job", {"job_id": "j1", "event": "launched", "command": "/milestone-plan wi",
                                        "role": "planner", "model": "opus", "effort": "high"}),
                         ["job j1 LAUNCHED (/milestone-plan wi, planner, opus/high)"])
        self.assertEqual(_texts("job", {"job_id": "j1", "event": "gate_blocked", "reason": "a gate",
                                        "what_is_required": "approve"}),
                         ["gate: job j1 GATE_BLOCKED -- a gate; required: approve"])
        self.assertEqual(_texts("run", {"run_id": "r1", "event": "run_ended", "exit_code": 10}),
                         ["run ended: exit 10"])
        self.assertEqual(_texts("run", {"run_id": "r1", "event": "job_started", "job_id": "j1"}),
                         ["job j1 started"])

    def test_an_unknown_event_type(self) -> None:
        [event] = observe.normalise("worker", json.dumps({"type": "rate_limit_event", "x": 1}))
        self.assertEqual((event["kind"], event["type"]), ("unknown", "rate_limit_event"))
        self.assertEqual(observe._body(event), "unknown event rate_limit_event")

    def test_a_non_json_line(self) -> None:
        for line in ("not json at all", "[1, 2]", '{"unterminated": '):
            with self.subTest(line=line):
                [event] = observe.normalise("worker", line)
                self.assertEqual((event["kind"], event["line"]), ("raw", line))

    def test_a_legacy_single_json_worker_stdout(self) -> None:
        legacy = {"type": "result", "subtype": "success", "is_error": False, "session_id": "s",
                  "num_turns": 2, "total_cost_usd": 0.1, "duration_ms": 50, "result": "done"}
        self.assertEqual(_texts("worker", legacy), ["worker result: success, is_error=False, turns=2, "
                                                    "cost=0.1, duration=50ms"])
        untyped = {k: v for k, v in legacy.items() if k != "type"}
        self.assertEqual(_texts("worker", untyped)[0].split(":")[0], "worker result")

    def test_normalise_never_raises(self) -> None:
        for line in ('{"type": "assistant", "message": 5}', '{"type": "user", "message": {"content": [7]}}',
                     '{"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {}}]}}'):
            with self.subTest(line=line):
                observe.normalise("worker", line)

    def test_render_text_prefix_and_render_json(self) -> None:
        event = observe.normalise("stderr", "oops")[0]
        text = observe.render_text(event)
        self.assertRegex(text, r"^\d\d:\d\d:\d\d stderr stderr: oops$")
        self.assertEqual(json.loads(observe.render_json(event)),
                         {"source": "stderr", "kind": "stderr", "line": "oops"})
        [block] = observe.normalise("worker", json.dumps(_assistant({"type": "text", "text": "a\nb"})))
        first, second = observe.render_text(block).splitlines()
        self.assertTrue(first.endswith(" a"))
        self.assertEqual(second.strip(), "b")
        self.assertEqual(len(second) - len(second.lstrip()), len(first) - 1)


class TailTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "log"

    def test_a_partial_trailing_line_is_held_back_until_its_newline(self) -> None:
        tail = observe.Tail(self.path)
        self.path.write_bytes(b"one\ntw")
        self.assertEqual(tail.read_lines(), ["one"])
        self.assertEqual(tail.read_lines(), [])
        with open(self.path, "ab") as fh:
            fh.write(b"o\nthree")
        self.assertEqual(tail.read_lines(), ["two"])
        self.assertEqual(tail.flush(), ["three"])

    def test_a_file_appearing_late(self) -> None:
        tail = observe.Tail(self.path)
        self.assertEqual(tail.read_lines(), [])
        self.path.write_text("first\n")
        self.assertEqual(tail.read_lines(), ["first"])

    def test_the_file_is_only_opened_for_reading(self) -> None:
        self.path.write_text("x\n")
        real_open = open
        modes = []

        def spy(path, mode="r", *args, **kwargs):
            modes.append(mode)
            return real_open(path, mode, *args, **kwargs)

        with unittest.mock.patch("builtins.open", spy):
            observe.Tail(self.path).read_lines()
        self.assertEqual(modes, ["rb"])


class _FollowCase(unittest.TestCase):
    """A runtime root with hand-written run and job records and logs."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime_root = Path(self._tmp.name) / "runtime"
        self.runtime_root.mkdir()
        self.lines: list[str] = []
        for name, value in (("POLL_SECONDS", 0.02), ("FINAL_EVENT_GRACE_SECONDS", 0.3)):
            patcher = unittest.mock.patch.object(observe, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def sink(self, text: str) -> None:
        self.lines.append(text)

    def write_run(self, run_id: str, **fields) -> dict:
        record = {"schema_version": 1, "run_id": run_id, "command": "run", "target_repo": "/repo",
                  "max_steps": 20, "controller_process": worker.capture_worker_process(os.getpid()).to_dict(),
                  "controller_runtime": {}, "state": "running", "exit_code": None, "job_ids": [],
                  "current_job_id": None, "started_at": "2026-01-01T00:00:00Z",
                  "updated_at": "2026-01-01T00:00:00Z", "ended_at": None, **fields}
        runtime.write_json(self.runtime_root, f"runs/{run_id}.json", record)
        return record

    def run_event(self, run_id: str, seq: int, event: str, **details) -> None:
        runtime.append_jsonl(self.runtime_root, f"runs/{run_id}/events.jsonl",
                             {"v": 1, "seq": seq, "at": "2026-01-01T00:00:00Z", "run_id": run_id, "event": event,
                              **details})

    def write_job(self, job_id: str, **fields) -> dict:
        record = {"job_id": job_id, "target_repo": "/repo", "status": job.STATUS_LAUNCHED,
                  "observed_phase_before": "PLANNING", "created_at": "2026-01-01T00:00:00Z",
                  "selected_action": {"command": "/milestone-plan wi"},
                  "worker_route": {"role": "planner", "model": "opus", "effort": "high"}, **fields}
        runtime.write_json(self.runtime_root, f"jobs/{job_id}.json", record)
        return record

    def job_event(self, job_id: str, seq: int, event: str, **details) -> None:
        runtime.append_jsonl(self.runtime_root, f"jobs/{job_id}/events.jsonl",
                             {"v": 1, "seq": seq, "at": "2026-01-01T00:00:00Z", "job_id": job_id, "event": event,
                              **details})

    def worker_line(self, job_id: str, obj) -> None:
        path = self.runtime_root / "jobs" / job_id / "worker.stdout"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")

    def bodies(self) -> list[str]:
        """The sink's lines without the time/source prefix."""
        return [line.split(" ", 2)[2].strip() if line[:2].isdigit() else line for line in self.lines]

    def in_thread(self, target, *args, **kwargs) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        return thread

    def dead_process(self) -> dict:
        """A reaped process's record, in its own group, so no running
        member of the group remains."""
        proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        captured = worker.capture_worker_process(proc.pid).to_dict()
        proc.wait()
        return captured


class FollowRunTest(_FollowCase):
    def test_an_ended_run_is_replayed_and_the_follow_ends(self) -> None:
        self.write_run("r1", state="ended", exit_code=10, job_ids=["j1"])
        self.run_event("r1", 1, "run_started")
        self.run_event("r1", 2, "job_started", job_id="j1")
        self.write_job("j1", status=job.STATUS_FINISHED)
        self.job_event("j1", 1, "planned", command="/milestone-plan wi")
        self.worker_line("j1", _assistant({"type": "thinking", "thinking": "hidden"}))
        self.worker_line("j1", _assistant({"type": "text", "text": "hello"}))
        self.run_event("r1", 3, "job_ended", job_id="j1", status="FINISHED")
        self.run_event("r1", 4, "run_ended", exit_code=10)
        observe.follow_run(self.runtime_root, "r1", self.sink, from_start=True)
        bodies = self.bodies()
        self.assertEqual(bodies[0], "run r1 started")
        self.assertIn("job j1 PLANNED (/milestone-plan wi)", bodies)
        self.assertIn("hello", bodies)
        self.assertEqual(bodies[-1], "run ended: exit 10")
        self.assertFalse(any("hidden" in line for line in self.lines))

    def test_without_from_start_only_the_last_20_events_are_replayed(self) -> None:
        self.write_run("r1", state="ended", exit_code=0)
        for seq in range(1, 31):
            self.run_event("r1", seq, "step_started", n=seq)
        self.run_event("r1", 31, "run_ended", exit_code=0)
        observe.follow_run(self.runtime_root, "r1", self.sink)
        self.assertEqual(len(self.lines), 20)
        self.assertEqual(self.bodies()[0], "step 12")
        self.assertEqual(self.bodies()[-1], "run ended: exit 0")

    def test_a_live_run_is_followed_until_it_ends(self) -> None:
        self.write_run("r1")
        self.run_event("r1", 1, "run_started")
        thread = self.in_thread(observe.follow_run, self.runtime_root, "r1", self.sink)
        self.assertTrue(process_fixtures.wait_until(lambda: "run r1 started" in self.bodies()))
        self.write_job("j1")
        self.write_run("r1", job_ids=["j1"], current_job_id="j1")
        self.run_event("r1", 2, "job_started", job_id="j1")
        self.worker_line("j1", _assistant({"type": "text", "text": "live text"}))
        self.assertTrue(process_fixtures.wait_until(lambda: "live text" in self.bodies()))
        self.assertTrue(thread.is_alive())
        self.write_run("r1", state="ended", exit_code=0, job_ids=["j1"])
        self.run_event("r1", 3, "run_ended", exit_code=0)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.bodies()[-1], "run ended: exit 0")

    def test_the_run_ended_event_written_after_the_record_is_still_shown(self) -> None:
        self.write_run("r1", state="ended", exit_code=0)
        self.run_event("r1", 1, "run_started")
        threading.Timer(0.1, self.run_event, ("r1", 2, "run_ended"), {"exit_code": 0}).start()
        observe.follow_run(self.runtime_root, "r1", self.sink)
        self.assertEqual(self.bodies()[-1], "run ended: exit 0")

    def test_a_gone_controller_ends_the_follow(self) -> None:
        self.write_run("r1", controller_process=self.dead_process())
        self.run_event("r1", 1, "run_started")
        observe.follow_run(self.runtime_root, "r1", self.sink)
        self.assertEqual(self.bodies()[-1], "controller process is gone; run record was not closed")

    def test_an_interrupted_run_with_an_active_worker_hands_over_to_the_job(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        self.write_run("r1", state="interrupted", job_ids=["j1"], current_job_id="j1")
        self.run_event("r1", 1, "run_interrupted")
        self.write_job("j1", run_id="r1", worker_process=process_fixtures.worker_process_dict(sleeper.pid))
        thread = self.in_thread(observe.follow_run, self.runtime_root, "r1", self.sink)
        handover = "run r1 was interrupted; worker for job j1 is still running; following the job"
        self.assertTrue(process_fixtures.wait_until(lambda: handover in self.bodies()))
        self.worker_line("j1", _assistant({"type": "text", "text": "still working"}))
        self.assertTrue(process_fixtures.wait_until(lambda: "still working" in self.bodies()))
        self.assertTrue(thread.is_alive())
        process_fixtures.kill_group(sleeper.pid)
        sleeper.wait(10)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.bodies()[-1], "worker exited; job j1 awaits resume")

    def test_an_interrupted_run_whose_worker_is_gone_ends_at_once(self) -> None:
        self.write_run("r1", state="interrupted", job_ids=["j1"], current_job_id="j1")
        self.run_event("r1", 1, "run_interrupted")
        self.write_job("j1", run_id="r1", worker_process=self.dead_process())
        observe.follow_run(self.runtime_root, "r1", self.sink)
        self.assertEqual(self.bodies()[-1], "run r1 was interrupted")

    def test_stop_drains_and_returns(self) -> None:
        self.write_run("r1")
        self.run_event("r1", 1, "run_started")
        stop = threading.Event()
        thread = self.in_thread(observe.follow_run, self.runtime_root, "r1", self.sink, stop=stop)
        self.assertTrue(process_fixtures.wait_until(lambda: self.lines))
        self.run_event("r1", 2, "step_started", n=1)
        stop.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.bodies()[-1], "step 1")

    def test_json_output_is_one_parseable_object_per_line(self) -> None:
        self.write_run("r1", state="ended", exit_code=0, job_ids=["j1"])
        self.run_event("r1", 1, "run_started")
        self.write_job("j1", status=job.STATUS_FINISHED)
        for event in fake_claude.default_events():
            self.worker_line("j1", event)
        (self.runtime_root / "jobs" / "j1" / "worker.stderr").write_text("warn 1\nwarn 2\n")
        self.run_event("r1", 2, "run_ended", exit_code=0)
        observe.follow_run(self.runtime_root, "r1", self.sink, from_start=True, json_output=True)
        objects = [json.loads(line) for line in self.lines]
        self.assertTrue(all(isinstance(obj, dict) and "kind" in obj and "\n" not in line
                            for obj, line in zip(objects, self.lines)))
        self.assertIn({"source": "stderr", "kind": "stderr", "line": "warn 1", "job_id": "j1"}, objects)
        self.assertEqual(objects[-1]["kind"], "run_event")


class FollowJobTest(_FollowCase):
    def test_a_terminal_job_is_drained_to_its_final_event(self) -> None:
        self.write_job("j1", status=job.STATUS_FAILED, event_seq=2)
        self.job_event("j1", 1, "planned", command="/c")
        threading.Timer(0.1, self.job_event, ("j1", 2, "failed"),
                        {"observed_phase_after": "PLANNING", "transition_verified": False}).start()
        observe.follow_job(self.runtime_root, "j1", self.sink)
        self.assertEqual(self.bodies()[-1], "job j1 FAILED (PLANNING -> PLANNING not verified)")

    def test_a_legacy_worker_stdout_without_a_newline_replays(self) -> None:
        self.write_job("j1", status=job.STATUS_FINISHED)
        path = self.runtime_root / "jobs" / "j1" / "worker.stdout"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(fake_claude.result_event()))
        observe.follow_job(self.runtime_root, "j1", self.sink)
        self.assertTrue(self.bodies()[-1].startswith("worker result: success"))

    def test_a_job_still_owned_by_its_running_controller_is_not_ended_by_its_worker(self) -> None:
        self.write_run("r1", job_ids=["j1"], current_job_id="j1")
        self.write_job("j1", run_id="r1", worker_process=self.dead_process())
        stop = threading.Event()
        thread = self.in_thread(observe.follow_job, self.runtime_root, "j1", self.sink, stop=stop)
        time.sleep(0.3)
        self.assertTrue(thread.is_alive())
        self.write_job("j1", run_id="r1", status=job.STATUS_FINISHED)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("awaits resume", "\n".join(self.lines))


class HeartbeatTest(_FollowCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = unittest.mock.patch.object(observe, "HEARTBEAT_SECONDS", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _heartbeats(self, **job_fields) -> list[str]:
        sleeper = process_fixtures.spawn_sleeper(self)
        self.write_job("j1", worker_process=process_fixtures.worker_process_dict(sleeper.pid), **job_fields)
        stop = threading.Event()
        thread = self.in_thread(observe.follow_job, self.runtime_root, "j1", self.sink, stop=stop)
        self.assertTrue(process_fixtures.wait_until(lambda: len(self.lines) >= 2))
        stop.set()
        thread.join(10)
        return self.bodies()

    def test_a_silent_launched_worker_gets_a_heartbeat(self) -> None:
        bodies = self._heartbeats()
        self.assertRegex(bodies[0], r"^worker running: pid \d+, elapsed \d+:\d\d, last event \d+s ago$")

    def test_a_draining_group_gets_the_drain_heartbeat(self) -> None:
        bodies = self._heartbeats(worker_group_drain={"direct_child_exited_at": "2026-01-01T00:00:00Z",
                                                      "remaining_pids": [4242, 4343]})
        self.assertRegex(bodies[0], r"^worker exited; waiting on process group: 2 process\(es\) at drain start "
                                    r"\(4242 4343\), elapsed \d+:\d\d$")
        self.assertFalse(any(body.startswith("worker running") for body in bodies))


class ControllerLivenessTest(unittest.TestCase):
    def test_this_process_is_active(self) -> None:
        own = worker.capture_worker_process(os.getpid()).to_dict()
        self.assertEqual(observe.controller_liveness(own), worker.ACTIVE)

    def test_a_reaped_process_is_inactive(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        captured = worker.capture_worker_process(proc.pid).to_dict()
        proc.wait()
        self.assertEqual(observe.controller_liveness(captured), worker.INACTIVE)

    def test_a_reused_pid_is_inactive(self) -> None:
        own = worker.capture_worker_process(os.getpid()).to_dict()
        self.assertEqual(observe.controller_liveness({**own, "start_ticks": own["start_ticks"] + 1}),
                         worker.INACTIVE)

    def test_a_zombie_is_inactive(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        self.addCleanup(proc.wait)
        captured = worker.capture_worker_process(proc.pid).to_dict()
        self.assertTrue(process_fixtures.wait_until(lambda: (process_fixtures.read_stat(proc.pid) or "Z")[0] == "Z"))
        self.assertEqual(observe.controller_liveness(captured), worker.INACTIVE)

    def test_the_process_group_is_not_consulted(self) -> None:
        # A live member of the Controller's group (say `| tee`) must not keep
        # a dead Controller `active`: a gone pid is inactive whatever its group.
        sleeper = process_fixtures.spawn_sleeper(self)
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        captured = {**worker.capture_worker_process(proc.pid).to_dict(), "pgid": sleeper.pid}
        proc.wait()
        self.assertEqual(observe.controller_liveness(captured), worker.INACTIVE)

    def test_other_contexts(self) -> None:
        own = worker.capture_worker_process(os.getpid()).to_dict()
        self.assertEqual(observe.controller_liveness({**own, "boot_id": "other-boot"}), worker.INACTIVE)
        self.assertEqual(observe.controller_liveness({**own, "boot_id": "other-boot", "hostname": "elsewhere"}),
                         worker.UNVERIFIABLE)
        self.assertEqual(observe.controller_liveness({**own, "pid_namespace": "pid:[1]"}), worker.UNVERIFIABLE)
        self.assertEqual(observe.controller_liveness(None), worker.UNVERIFIABLE)
        self.assertEqual(observe.controller_liveness({"pid": "x"}), worker.UNVERIFIABLE)


class FdSinkTest(unittest.TestCase):
    def _pipe(self) -> tuple[int, int]:
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(lambda: os.close(write_fd) if write_fd >= 0 else None)
        return read_fd, write_fd

    def test_lines_are_written_whole(self) -> None:
        read_fd, write_fd = self._pipe()
        sink = observe.FdSink(write_fd)
        sink("hello")
        sink("x" * (select_pipe_buf() * 2 + 5))
        data = os.read(read_fd, 1 << 16)
        self.assertEqual(data, b"hello\n" + b"x" * (select_pipe_buf() * 2 + 5) + b"\n")
        self.assertFalse(sink.disabled)

    def test_a_stalled_fifo_disables_the_sink_leaving_the_reserve_free(self) -> None:
        read_fd, write_fd = self._pipe()
        sink = observe.FdSink(write_fd)
        with unittest.mock.patch.object(observe, "WRITE_WAIT_SECONDS", 0.05):
            for _ in range(10_000):
                sink("y" * 1000)
                if sink.disabled:
                    break
        self.assertTrue(sink.disabled)
        import fcntl
        import termios
        capacity = fcntl.fcntl(write_fd, fcntl.F_GETPIPE_SZ)
        unread = bytearray(4)
        fcntl.ioctl(read_fd, termios.FIONREAD, unread)
        self.assertGreaterEqual(capacity - int.from_bytes(unread, "little"), observe.PIPE_RESERVE_BYTES)
        sink("more")  # disabled: never blocks, never raises

    def test_a_closed_reader_raises_and_fail_disables_quietly(self) -> None:
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        self.addCleanup(os.close, write_fd)
        sink = observe.FdSink(write_fd)
        with self.assertRaises(BrokenPipeError):
            sink("x")
        sink.fail(BrokenPipeError())
        self.assertTrue(sink.disabled)


def select_pipe_buf() -> int:
    import select
    return select.PIPE_BUF


if __name__ == "__main__":
    unittest.main()
