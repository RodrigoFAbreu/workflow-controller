"""Observation isolation and equivalence (`workflow-controller-release-
runtime-observability` CP7).

Observation is presentation-only (R11): a follower -- ``step``/``run
--follow``'s in-process renderer, or a separate ``follow`` process attached
and detached at will -- changes nothing a lifecycle produces. These tests
prove it end to end. Every Controller here is a real ``python -m
controller`` process run from a committed ``fixtures.build_checkout``
clone, so it pins and materialises for real, owns a real fd 2 (the
descriptor ``--follow`` duplicates), and exits through real interpreter
finalisation (where a daemon renderer blocked on a buffered writer's lock
would abort). The worker is ``tests/fake_claude.py`` in its scripted mode,
driven by the ``Lifecycle`` model of ``tests/test_lifecycle_orchestration.py``.

The scripted lifecycle has two legs. Leg 1 is ``run`` on a ``"2.2"``
``PLANNING`` item: ``/milestone-plan``, then ``/review-plan``, then the
manual-external plan gate (exit 10). The human's plan approval is then
performed in-process. Leg 2 is ``run --max-steps 1``: one
``/milestone-implement`` checkpoint (exit 16). Commit dates are fixed, so
three identical fixtures produce byte-identical Git histories, and every
other run-dependent value (ids, timestamps, pids, fixture paths) is
replaced by a placeholder before comparison.
"""

from __future__ import annotations

import json
import os
import re
import fcntl
import select
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, job, observe  # noqa: E402
from tests import fake_claude, fixtures  # noqa: E402
from tests.test_lifecycle_orchestration import (  # noqa: E402
    CHECKPOINT_IDS, IMPLEMENTING, MILESTONE_IMPLEMENT, STATE_REL, WI, WORK_ITEM, Lifecycle,
)

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"

MILESTONE_PLAN = f"/milestone-plan {WI}"
REVIEW_PLAN = f"/review-plan {WI}"
AWAITING_LOCAL_PLAN = "AWAITING_LOCAL_PLAN_REVIEW"
AWAITING_MANUAL_PLAN = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
PLAN_MANIFEST_REL = f".ai-review/{WI}/current/MANIFEST.md"
FEEDBACK_REL = f".ai-review/{WI}/feedback/REVIEW_FEEDBACK.md"
PLAN_BUNDLE_ID = "b" * 64

#: A worker holds (``FAKE_CLAUDE_HANG_UNTIL_FILE``) while this target file
#: is absent. It exists from the start, so the first worker passes, and
#: that worker's script deletes it, so the second worker holds until the
#: test recreates it. Under the ignored ``.ai-review/``.
HOLD_REL = ".ai-review/hold-worker"

#: Every commit, the fixture's own and the worker's, carries these dates,
#: so identical fixtures have identical commit ids.
GIT_DATES = {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000"}

#: How long any single wait in this module may take.
WAIT_SECONDS = 60

#: The bound test 4a puts on a Controller whose stderr is never read.
STALLED_STDERR_BOUND_SECONDS = 30

#: Record keys whose values are process identities, never lifecycle results.
PROCESS_KEYS = frozenset({"pid", "pgid", "start_ticks"})

#: Byte offsets into a worker's stream (worker-lifecycle-ownership CP4
#: persists them): the stream embeds each case's own paths, whose lengths
#: differ, so an offset is normalised like a pid.
OFFSET_KEYS = frozenset({"offset", "ending_point"})

_TIMESTAMP_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


class Fixture:
    """One target repository with its own runtime root, worker script,
    diagnostics log and pause-release file."""

    def __init__(self, lifecycle: Lifecycle, approval: list[dict]) -> None:
        self.lc = lifecycle
        self.approval = approval
        self.case_dir = lifecycle.case_dir
        self.root = lifecycle.root
        self.runtime = lifecycle.runtime
        self.diag_log = self.case_dir / "worker-diagnostics.jsonl"
        self.release = self.case_dir / "release-worker"
        self.run_ids: list[str] = []
        self.exit_codes: list[int] = []
        self.stdouts: list[str] = []

    def run_records(self) -> set[str]:
        runs = self.runtime / "runs"
        return {p.stem for p in runs.glob("*.json")} if runs.is_dir() else set()

    def job_stdout_paths(self) -> list[Path]:
        jobs = self.runtime / "jobs"
        return sorted(jobs.glob("*/worker.stdout")) if jobs.is_dir() else []


def _read_json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _line_count(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def _wait_for(predicate, what: str, timeout: float = WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(0.05)


class _LineReader:
    """Reads a child's stdout on a thread, so a test can wait for a line
    with a bound instead of blocking in ``readline``."""

    def __init__(self, stream) -> None:
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        for line in iter(stream.readline, ""):
            with self._lock:
                self.lines.append(line.rstrip("\n"))

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.lines)

    def wait_for(self, fragment: str) -> None:
        _wait_for(lambda: any(fragment in line for line in self.snapshot()), f"a follower line with {fragment!r}")

    def join(self) -> list[str]:
        self._thread.join(WAIT_SECONDS)
        return self.snapshot()


class _ObservationCase(unittest.TestCase):
    """The committed checkout every Controller process runs from, the stub
    Workflow Manager, and fixture/process helpers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._class_tmp = tempfile.TemporaryDirectory(prefix="controller-observation-origin-")
        class_root = Path(cls._class_tmp.name).resolve()
        cls.checkout = fixtures.build_checkout(class_root / "checkout", generation=1)
        cls.stub_manager = fixtures.write_stub_workflow_manager(class_root / "workflow-manager")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._class_tmp.cleanup()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="controller-observation-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name).resolve()

    # --- fixtures --------------------------------------------------------------

    def fixture(self, name: str, *, review_plan: list[dict] | None = None, hold: bool = False) -> Fixture:
        """A committed ``"2.2"`` target at ``PLANNING`` and its worker script:
        ``/milestone-plan`` (a coherent plan bundle and the committed
        ``AWAITING_LOCAL_PLAN_REVIEW`` write), ``/review-plan`` (the local
        ``APPROVE`` on file and the uncommitted
        ``AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`` write, or ``review_plan``),
        then the human's approval (returned, performed later) and one
        ``/milestone-implement`` checkpoint. With ``hold``, ``/milestone-plan``
        also deletes :data:`HOLD_REL`."""
        case_dir = self.tmp_root / name
        root = case_dir / "target"
        with unittest.mock.patch.dict(os.environ, GIT_DATES):
            fixtures.build_target_git_repo(root)
            (root / "README.md").write_text("observation fixture\n")
            (root / ".gitignore").write_text(".ai-review/\n")
            fixtures.write_installation_manifest(root)
            base = fixtures.commit_all(root, "initial")
            lc = Lifecycle(case_dir, root.resolve(), base, phase="PLANNING", checkpoints={})
            fixtures.write_registry(root, WI, CHECKPOINT_IDS)
            (root / STATE_REL).write_text(lc.state_text())
            fixtures.commit_all(root, "Seed the work item")

        lc.entry["phase"] = AWAITING_LOCAL_PLAN
        lc.add(MILESTONE_PLAN, [
            fixtures.script_write(PLAN_MANIFEST_REL,
                                  fixtures.build_plan_manifest_text(WI, 1, bundle_id=PLAN_BUNDLE_ID)),
            lc.write_state(),
            fixtures.script_commit(fixtures.trailer_message("Publish plan revision 1", (WORK_ITEM, WI)), STATE_REL),
            *([fixtures.script_delete(HOLD_REL)] if hold else []),
        ])
        if hold:
            (root / HOLD_REL).parent.mkdir(parents=True, exist_ok=True)
            (root / HOLD_REL).touch()
        lc.entry["phase"] = AWAITING_MANUAL_PLAN
        feedback = fixtures.build_review_feedback_text(
            status="APPROVE", reviewer_role="LOCAL_MODEL_PLAN_REVIEW", reviewed_bundle_id=PLAN_BUNDLE_ID,
            reviewed_base_commit=base, work_item=WI, reviewed_content_id="c" * 64,
        )
        lc.add(REVIEW_PLAN, review_plan if review_plan is not None else [
            fixtures.script_write(FEEDBACK_REL, feedback), lc.write_state(),
        ])
        lc.entry["phase"] = IMPLEMENTING
        approval = [lc.write_state(), fixtures.script_commit(
            fixtures.trailer_message("Approve plan revision 1", (WORK_ITEM, WI)), STATE_REL)]
        lc.add(MILESTONE_IMPLEMENT, lc.implement("CP1"))
        fixtures.write_worker_script(lc.script_path, lc.script)
        return Fixture(lc, approval)

    def approve_plan(self, fx: Fixture) -> None:
        """The human's ``/approve-review plan``, performed in-process."""
        with unittest.mock.patch.dict(os.environ, GIT_DATES):
            fx.lc.perform(fx.approval)

    # --- processes -------------------------------------------------------------

    def env(self, fx: Fixture, **extra: str) -> dict[str, str]:
        """The same environment keys for every Controller in this module --
        only the fixture's own paths differ between fixtures."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("WORKFLOW_CONTROLLER_HOME", "XDG_STATE_HOME", "PYTHONPATH")}
        env.update(GIT_DATES)
        env["PYTHONPATH"] = str(self.checkout)
        env["FAKE_CLAUDE_SCRIPT"] = str(fx.lc.script_path)
        env["FAKE_CLAUDE_DIAG_LOG"] = str(fx.diag_log)
        env["FAKE_CLAUDE_PAUSE_AFTER_EVENTS_FILE"] = f"{extra.pop('pause_after', 1)}:{fx.release}"
        env.update(extra)
        return env

    def controller(self, fx: Fixture, command: str, *args: str, stderr=subprocess.PIPE,
                   **env_extra: str) -> subprocess.Popen:
        argv = [sys.executable, "-m", "controller", "--runtime-dir", str(fx.runtime),
                "--workflow-manager", str(self.stub_manager), "--claude-binary", str(FAKE_CLAUDE),
                "--timeout", str(WAIT_SECONDS), command, *args, str(fx.root)]
        proc = subprocess.Popen(argv, cwd=fx.case_dir, env=self.env(fx, **env_extra), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=stderr, text=True)
        self.addCleanup(self._reap, proc)
        return proc

    def follower(self, fx: Fixture, *args: str, stdout=subprocess.PIPE) -> subprocess.Popen:
        argv = [sys.executable, "-m", "controller", "--runtime-dir", str(fx.runtime), "follow", *args,
                str(fx.root)]
        proc = subprocess.Popen(argv, cwd=fx.case_dir, env=self.env(fx), stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self._reap, proc)
        return proc

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def finish(self, fx: Fixture, proc: subprocess.Popen, before: set[str]) -> str:
        """Wait for a Controller, recording its exit code, stdout and new run
        id on ``fx``. Returns its stderr (or ``""`` when not piped)."""
        stdout, stderr = proc.communicate(timeout=WAIT_SECONDS)
        fx.exit_codes.append(proc.returncode)
        fx.stdouts.append(stdout)
        [run_id] = fx.run_records() - before
        fx.run_ids.append(run_id)
        return stderr or ""

    def leg(self, fx: Fixture, command: str, *args: str, **kwargs) -> str:
        before = fx.run_records()
        return self.finish(fx, self.controller(fx, command, *args, **kwargs), before)

    def run_lifecycle(self, fx: Fixture, *, follow: bool = False) -> list[str]:
        """Both legs, the worker never paused. Returns each leg's stderr."""
        fx.release.touch()
        extra = ("--follow",) if follow else ()
        stderr = [self.leg(fx, "run", *extra)]
        self.approve_plan(fx)
        stderr.append(self.leg(fx, "run", "--max-steps", "1", *extra))
        return stderr

    # --- durable results -------------------------------------------------------

    def durable_results(self, fx: Fixture) -> dict:
        """Everything a lifecycle leaves behind, normalised: exit codes,
        stdout, run records and logs, job records, logs and worker streams,
        the target's ``WORKFLOW_STATE.json``, Git log and status, and each
        worker's own diagnostics."""
        run_records = [json.loads((fx.runtime / "runs" / f"{run_id}.json").read_text()) for run_id in fx.run_ids]
        job_ids = [job_id for record in run_records for job_id in record["job_ids"]]
        names = {run_id: f"<RUN {n}>" for n, run_id in enumerate(fx.run_ids)}
        names.update({job_id: f"<JOB {n}>" for n, job_id in enumerate(job_ids)})
        names[str(fx.case_dir)] = "<CASE>"

        jobs = []
        for job_id in job_ids:
            job_dir = fx.runtime / "jobs" / job_id
            jobs.append({
                "record": json.loads((fx.runtime / "jobs" / f"{job_id}.json").read_text()),
                "events": _read_json_lines(job_dir / "events.jsonl"),
                "worker.stdout": (job_dir / "worker.stdout").read_text() if (job_dir / "worker.stdout").exists()
                else None,
                "worker.stderr": (job_dir / "worker.stderr").read_text() if (job_dir / "worker.stderr").exists()
                else None,
            })
        results = {
            "exit_codes": fx.exit_codes,
            "stdout": fx.stdouts,
            "runs": [{"record": record, "events": _read_json_lines(fx.runtime / "runs" / record["run_id"] /
                                                                     "events.jsonl")}
                     for record in run_records],
            "jobs": jobs,
            "state": (fx.root / STATE_REL).read_text(),
            "git_log": fixtures.run(["git", "log", "--format=%H %P%n%B"], cwd=fx.root).stdout,
            "git_status": fixtures.run(["git", "status", "--porcelain", "--untracked-files=all"],
                                       cwd=fx.root).stdout,
            "worker_diagnostics": _read_json_lines(fx.diag_log),
        }
        return _normalise(results, names)


def _normalise(value, names: dict[str, str]):
    if isinstance(value, dict):
        return {key: "<N>" if (key in PROCESS_KEYS or key in OFFSET_KEYS or key.endswith("_offset"))
                and isinstance(item, int) else
                ["<N>"] * len(item) if key == "remaining_pids" and isinstance(item, list) else
                _normalise(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item, names) for item in value]
    if isinstance(value, str):
        for original in sorted(names, key=len, reverse=True):
            value = value.replace(original, names[original])
        return _TIMESTAMP_RE.sub("<T>", value)
    return value


# ---------------------------------------------------------------------------
# 1. Equivalence: plain, --follow, and plain with a follower killed mid-job.
# ---------------------------------------------------------------------------


class EquivalenceTest(_ObservationCase):
    def test_followed_and_unfollowed_lifecycles_leave_identical_durable_results(self) -> None:
        plain = self.fixture("plain")
        self.run_lifecycle(plain)

        followed = self.fixture("followed")
        rendered = self.run_lifecycle(followed, follow=True)

        attached = self.fixture("attached")
        before = attached.run_records()
        proc = self.controller(attached, "run")
        _wait_for(lambda: any(_line_count(p) >= 1 for p in attached.job_stdout_paths()),
                  "the first worker's first event")
        follower = self.follower(attached)
        reader = _LineReader(follower.stdout)
        reader.wait_for("worker session")  # attached after the first event, mid-job
        self.assertIsNone(proc.poll(), "the job ended before the follower was killed")
        self.assertIsNone(follower.poll())
        follower.kill()
        self.assertEqual(follower.wait(WAIT_SECONDS), -signal.SIGKILL)
        attached.release.touch()
        self.finish(attached, proc, before)
        self.approve_plan(attached)
        self.leg(attached, "run", "--max-steps", "1")

        expected = self.durable_results(plain)
        self.assertEqual(expected["exit_codes"], [cli.EXIT_GATE, cli.EXIT_MAX_STEPS])
        self.assertEqual([entry["record"]["status"] for entry in expected["jobs"]],
                         [job.STATUS_FINISHED, job.STATUS_FINISHED, job.STATUS_GATE_BLOCKED, job.STATUS_FINISHED])
        self.assertEqual(len(expected["worker_diagnostics"]), 3)
        self.assertEqual(self.durable_results(followed), expected)
        self.assertEqual(self.durable_results(attached), expected)

        # The --follow legs did render, onto stderr only.
        self.assertIn("worker session", rendered[0])
        self.assertTrue(rendered[0].rstrip("\n").endswith(f"run ended: exit {cli.EXIT_GATE}"), rendered[0])
        self.assertTrue(rendered[1].rstrip("\n").endswith(f"run ended: exit {cli.EXIT_MAX_STEPS}"), rendered[1])


# ---------------------------------------------------------------------------
# 2. Attach from a second process; 3. detach by SIGKILL and by a broken pipe.
# ---------------------------------------------------------------------------


class AttachDetachTest(_ObservationCase):
    def test_a_second_process_attaches_mid_stream_and_exits_0_when_the_run_ends(self) -> None:
        fx = self.fixture("attach")
        before = fx.run_records()
        proc = self.controller(fx, "step", pause_after="2")
        _wait_for(lambda: any(_line_count(p) >= 2 for p in fx.job_stdout_paths()), "two emitted events")

        follower = self.follower(fx)
        reader = _LineReader(follower.stdout)
        reader.wait_for("Working on it.")  # the second, already-emitted event
        self.assertFalse(any("worker result" in line for line in reader.snapshot()))
        self.assertIsNone(proc.poll())

        fx.release.touch()
        self.assertEqual(follower.wait(WAIT_SECONDS), cli.EXIT_OK, follower.stderr.read())
        lines = reader.join()
        self.finish(fx, proc, before)
        self.assertEqual(fx.exit_codes, [cli.EXIT_OK])

        bodies = [line.split(" ", 2)[-1].strip() for line in lines]
        released = next(n for n, body in enumerate(bodies) if body.startswith("worker result: success"))
        self.assertGreater(released, next(n for n, body in enumerate(bodies) if "Working on it." in body))
        self.assertTrue(any("FINISHED" in body for body in bodies[released:]), bodies)
        self.assertEqual(bodies[-1], "run ended: exit 0")

    def test_killed_and_broken_pipe_followers_leave_the_worker_and_exit_code_alone(self) -> None:
        plain = self.fixture("plain")
        plain.release.touch()
        self.leg(plain, "step")

        fx = self.fixture("detached")
        before = fx.run_records()
        proc = self.controller(fx, "step")
        _wait_for(lambda: any(_line_count(p) >= 1 for p in fx.job_stdout_paths()), "the first event")

        killed = self.follower(fx)
        reader = _LineReader(killed.stdout)
        reader.wait_for("worker session")

        read_end, write_end = os.pipe()
        os.close(read_end)  # a reader that is already gone
        try:
            broken = self.follower(fx, stdout=write_end)
        finally:
            os.close(write_end)
        # A follower whose stdout is gone stops, like Ctrl-C: exit 0, no
        # traceback (ADR 0002's `0`/`2`/`20`).
        self.assertEqual(broken.wait(WAIT_SECONDS), cli.EXIT_OK)
        self.assertNotIn("Traceback", broken.stderr.read())

        killed.kill()
        self.assertEqual(killed.wait(WAIT_SECONDS), -signal.SIGKILL)
        self.assertIsNone(proc.poll(), "the worker ended before its followers detached")

        fx.release.touch()
        self.finish(fx, proc, before)
        results = self.durable_results(fx)
        self.assertEqual(results["exit_codes"], [cli.EXIT_OK])
        [record] = [entry["record"] for entry in results["jobs"]]
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertTrue(record["transition_verified"])
        self.assertEqual(results, self.durable_results(plain))


# ---------------------------------------------------------------------------
# 4. In-process renderer failure; 4a. a stalled stderr.
# ---------------------------------------------------------------------------


def _unread(fd: int) -> int:
    """The bytes waiting in a pipe, without reading them."""
    unread = bytearray(4)
    fcntl.ioctl(fd, termios.FIONREAD, unread)
    return int.from_bytes(unread, "little")


#: The most bytes one environment string may hold (Linux ``MAX_ARG_STRLEN``).
_MAX_ENV_STRING = 128 * 1024


def _large_stream() -> str:
    """A well-formed stream whose rendering is larger than a pipe's
    capacity, in short lines: 900 minimal assistant texts of 60 characters,
    then the result. Each renders as its own write of under 100 bytes, so a
    full pipe has almost no room left in its last page and, without the
    reserve, even a short ``error:`` line would block. The stream is one
    ``FAKE_CLAUDE_STDOUT`` value, so it stays under ``MAX_ARG_STRLEN``."""
    events = fake_claude.default_events()
    texts = [{"type": "assistant", "message": {"content": [{"type": "text", "text": f"{n:04d} " + "x" * 55}]}}
             for n in range(900)]
    stream = fake_claude.stream_text(events[:-1] + texts + events[-1:])
    assert len("FAKE_CLAUDE_STDOUT=" + stream) < _MAX_ENV_STRING, len(stream)
    return stream


class RendererFailureTest(_ObservationCase):
    def test_a_closed_stderr_reader_changes_neither_the_exit_code_nor_the_results(self) -> None:
        plain = self.fixture("plain")
        plain.release.touch()
        self.leg(plain, "run")

        fx = self.fixture("broken")
        fx.release.touch()
        read_end, write_end = os.pipe()
        os.close(read_end)  # BrokenPipeError on the renderer's first write
        try:
            before = fx.run_records()
            proc = self.controller(fx, "run", "--follow", stderr=write_end)
        finally:
            os.close(write_end)
        self.finish(fx, proc, before)
        self.assertEqual(fx.exit_codes, [cli.EXIT_GATE])
        self.assertEqual(self.durable_results(fx), self.durable_results(plain))

    def _stalled(self, fx: Fixture, *, follow: bool, hold: bool = False) -> tuple[int, bytes]:
        """``run`` with stderr on a pipe nobody reads until the process has
        exited, bounded. Returns the exit code and what reached the pipe.

        With ``hold`` (a :meth:`fixture` built with ``hold``), the second
        worker holds until the renderer, if any, has filled the pipe down to
        its reserve -- measured with ``FIONREAD``, never by reading -- so
        whatever the Controller writes after that job meets a full pipe."""
        fx.release.touch()
        extra = {"FAKE_CLAUDE_HANG_UNTIL_FILE": str(fx.root / HOLD_REL)} if hold else {}
        read_end, write_end = os.pipe()
        try:
            before = fx.run_records()
            proc = self.controller(fx, "run", *(("--follow",) if follow else ()), stderr=write_end,
                                   FAKE_CLAUDE_STDOUT=_large_stream(), **extra)
            os.close(write_end)
            write_end = -1
            if hold:
                _wait_for(lambda: len(fx.job_stdout_paths()) == 2, "the second worker")
                if follow:
                    capacity = fcntl.fcntl(read_end, fcntl.F_GETPIPE_SZ)
                    _wait_for(lambda: _unread(read_end) >= capacity - observe.PIPE_RESERVE_BYTES - 4096,
                              "the renderer to fill the pipe down to its reserve")
                (fx.root / HOLD_REL).touch()
            try:
                stdout, _ = proc.communicate(timeout=STALLED_STDERR_BOUND_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                self.fail(f"a Controller with a stalled stderr ran past {STALLED_STDERR_BOUND_SECONDS}s")
            fx.exit_codes.append(proc.returncode)
            fx.stdouts.append(stdout)
            [run_id] = fx.run_records() - before
            fx.run_ids.append(run_id)
            received = b""
            while select.select([read_end], [], [], 0)[0]:
                chunk = os.read(read_end, 65536)
                if not chunk:
                    break
                received += chunk
        finally:
            os.close(read_end)
            if write_end >= 0:
                os.close(write_end)
        self.assertGreaterEqual(proc.returncode, 0, f"killed by signal {-proc.returncode}")
        return proc.returncode, received

    def test_a_stalled_stderr_bounds_the_run_and_changes_nothing(self) -> None:
        plain = self.fixture("plain")
        plain_code, plain_received = self._stalled(plain, follow=False)
        self.assertEqual(plain_code, cli.EXIT_GATE)
        self.assertEqual(plain_received, b"")

        fx = self.fixture("followed")
        code, received = self._stalled(fx, follow=True)
        self.assertEqual(code, plain_code)
        self.assertIn(b"worker session", received)
        self.assertEqual(self.durable_results(fx), self.durable_results(plain))

    def test_a_stalled_stderr_still_carries_a_controller_error_line(self) -> None:
        """The reserve rule: after the renderer has filled the pipe, the
        Controller's own ``error:`` line still fits."""
        corrupt = [fixtures.script_write(STATE_REL, "{")]
        plain = self.fixture("plain", review_plan=corrupt, hold=True)
        plain_code, plain_received = self._stalled(plain, follow=False, hold=True)
        self.assertEqual(plain_code, cli.EXIT_FAIL_CLOSED)
        self.assertIn(b"error: ", plain_received)

        fx = self.fixture("followed", review_plan=corrupt, hold=True)
        code, received = self._stalled(fx, follow=True, hold=True)
        self.assertEqual(code, plain_code)
        error_lines = [line for line in received.splitlines() if line.startswith(b"error: ")]
        self.assertEqual(len(error_lines), 1, received[-2000:])
        self.assertIn(b"is not valid JSON", error_lines[0])
        self.assertGreater(received.index(error_lines[0]), 32 * 1024, "the error line met an empty pipe")
        self.assertEqual(self.durable_results(fx), self.durable_results(plain))


# ---------------------------------------------------------------------------
# 5. The final result survives streaming.
# ---------------------------------------------------------------------------


class FinalResultTest(_ObservationCase):
    def test_a_streamed_job_keeps_the_same_result_as_a_result_only_stream(self) -> None:
        streamed = self.fixture("streamed")
        streamed.release.touch()
        self.leg(streamed, "step")

        result_only = self.fixture("result-only")
        result_only.release.touch()
        self.leg(result_only, "step", FAKE_CLAUDE_STDOUT=fake_claude.stream_text([fake_claude.result_event()]))

        [streamed_job] = self.durable_results(streamed)["jobs"]
        [result_job] = self.durable_results(result_only)["jobs"]
        self.assertEqual(streamed_job["worker.stdout"].count("\n"), len(fake_claude.default_events()))
        self.assertEqual(result_job["worker.stdout"].count("\n"), 1)
        # Worker-lifecycle-ownership CP4: `worker.stream_diagnosis` accounts
        # for each stream as read (its events and offsets), so it differs by
        # construction; the reason it gives must not.
        streamed_diagnosis = streamed_job["record"]["worker"].pop("stream_diagnosis")
        result_diagnosis = result_job["record"]["worker"].pop("stream_diagnosis")
        self.assertEqual(streamed_diagnosis["reason"], result_diagnosis["reason"])
        for key in ("worker", "worker_outcome", "transition_verified", "status"):
            with self.subTest(key=key):
                self.assertEqual(streamed_job["record"][key], result_job["record"][key])
        self.assertEqual(streamed_job["record"]["worker_outcome"], "SUCCESS")


# ---------------------------------------------------------------------------
# follow's zero-write contract and legacy replay, end to end.
# ---------------------------------------------------------------------------


def _tree_listing(root: Path) -> list[tuple]:
    """Every path under ``root`` with its type, size and mtime."""
    listing = []
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        listing.append((str(path.relative_to(root)), st.st_mode, st.st_size, st.st_mtime_ns))
    return listing


class FollowReadOnlyTest(_ObservationCase):
    def _completed(self) -> Fixture:
        fx = self.fixture("done")
        fx.release.touch()
        self.leg(fx, "step")
        return fx

    def test_follow_processes_write_nothing_to_the_runtime_root_or_the_target(self) -> None:
        fx = self._completed()
        [run_id] = fx.run_ids
        [job_id] = json.loads((fx.runtime / "runs" / f"{run_id}.json").read_text())["job_ids"]
        runtime_before, target_before = _tree_listing(fx.runtime), _tree_listing(fx.root)
        for args in ((), ("--run", run_id, "--from-start"), ("--job", job_id, "--from-start")):
            with self.subTest(args=args):
                follower = self.follower(fx, *args)
                stdout, stderr = follower.communicate(timeout=WAIT_SECONDS)
                self.assertEqual(follower.returncode, cli.EXIT_OK, stderr)
                self.assertTrue(stdout)
        self.assertEqual(_tree_listing(fx.runtime), runtime_before)
        self.assertEqual(_tree_listing(fx.root), target_before)

    def test_a_legacy_single_json_worker_stdout_replays_as_its_result_line(self) -> None:
        fx = self._completed()
        [run_id] = fx.run_ids
        [job_id] = json.loads((fx.runtime / "runs" / f"{run_id}.json").read_text())["job_ids"]
        # A record from before streaming: `--output-format json`'s single
        # object, no trailing newline, and no `worker_streams` block.
        (fx.runtime / "jobs" / job_id / "worker.stdout").write_text(json.dumps(fake_claude.result_event()))
        record_path = fx.runtime / "jobs" / f"{job_id}.json"
        record = json.loads(record_path.read_text())
        del record["worker_streams"]
        record_path.write_text(json.dumps(record, indent=2) + "\n")

        follower = self.follower(fx, "--job", job_id, "--from-start")
        stdout, stderr = follower.communicate(timeout=WAIT_SECONDS)
        self.assertEqual(follower.returncode, cli.EXIT_OK, stderr)
        bodies = [line.split(" ", 2)[-1].strip() for line in stdout.splitlines()]
        self.assertEqual([body for body in bodies if body.startswith("worker ")],
                         ["worker result: success, is_error=False, turns=1, cost=0.0, duration=1ms"])


if __name__ == "__main__":
    unittest.main()
