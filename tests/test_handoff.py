"""Tests for the generation handoff primitive (CP8, ``controller.handoff``)
and its wiring into ``controller.cli``'s ``run`` loop.

Covers ``detect()``'s three branches (greater/equal/lower generation),
``write_handoff_record``'s schema, the ``--pause-file``/
``WORKFLOW_CONTROLLER_TEST_HOOKS`` gate, the run loop's own exit-code
mapping (exercised in-process against a monkeypatched ``job.execute_step``,
following this repository's own established technique for cases that do
not need a real worker), and one real end-to-end subprocess test proving
the actual property CP8 exists for: a running generation stops
intentionally, with a durable handoff record, when the origin source
repository's committed ``HEAD`` moves to a newer generation -- and never
adopts the new bytes.

As with ``tests/test_identity.py``, the end-to-end test is faithful but
scoped to what is practical for a single checkpoint's own test file: it
proves exit code, the handoff record's content, and that the running
snapshot's own bytes and digest are untouched, without reconstructing the
plan's own separate probe-interpreter subprocess.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, handoff, identity, job, runtime  # noqa: E402
from controller.decision import Decision  # noqa: E402
from controller.errors import GenerationHandoffPendingError, SourceSnapshotError  # noqa: E402
from tests import fixtures  # noqa: E402


def _pinned_identity(
    origin_root: Path, *, generation: int, source_commit: str,
    source_kind: str = identity.SOURCE_KIND_COMMIT,
) -> identity.ControllerIdentity:
    """A ``ControllerIdentity`` naming ``origin_root`` as its own origin --
    everything ``detect()`` reads is either this object's own attributes
    or ``origin_root``'s committed ``HEAD``, so a fake ``source_root``
    (never read by ``detect()``) is fine."""
    return identity.ControllerIdentity(
        generation=generation,
        source_root=origin_root,
        origin_source_root=origin_root,
        source_kind=source_kind,
        source_commit=source_commit,
        tree_digest="d" * 64,
        generation_source="head",
        pinned_at="2024-01-01T00:00:00Z",
    )


def _write_generation(root: Path, generation: int) -> None:
    (root / "controller" / "GENERATION.json").write_text(
        json.dumps({"schema_version": 1, "generation": generation}) + "\n"
    )


class DetectTest(unittest.TestCase):
    def test_equal_generation_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=1)
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=1, source_commit=head_a)
            self.assertIsNone(handoff.detect(ident, origin))

    def test_equal_generation_different_commit_still_returns_none(self) -> None:
        """The branch that keeps the Controller usable while its own
        repository is under ordinary development: a commit that never
        touches ``GENERATION.json`` is not a handoff."""
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=1)
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=1, source_commit=head_a)
            (origin / "pyproject.toml").write_text(
                (origin / "pyproject.toml").read_text() + "\n# unrelated\n"
            )
            fixtures.commit_all(origin, "unrelated change")
            self.assertIsNone(handoff.detect(ident, origin))

    def test_greater_generation_returns_handoff_with_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=1)
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=1, source_commit=head_a)

            _write_generation(origin, 2)
            head_b = fixtures.commit_all(origin, "bump to generation 2")

            result = handoff.detect(ident, origin)
            self.assertIsInstance(result, handoff.Handoff)
            self.assertEqual(result.from_generation, 1)
            self.assertEqual(result.from_commit, head_a)
            self.assertEqual(result.to_generation, 2)
            self.assertEqual(result.to_commit, head_b)
            self.assertEqual(result.source_root, origin)

    def test_greater_generation_worktree_kind_carries_none_from_commit(self) -> None:
        """A ``"worktree"``-kind running identity (dirty-source pin) has no
        ``source_commit`` of its own -- carried through unchanged, never
        invented."""
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=1)
            ident = _pinned_identity(
                origin, generation=1, source_commit=None, source_kind=identity.SOURCE_KIND_WORKTREE,
            )
            _write_generation(origin, 2)
            fixtures.commit_all(origin, "bump to generation 2")
            result = handoff.detect(ident, origin)
            self.assertIsInstance(result, handoff.Handoff)
            self.assertIsNone(result.from_commit)

    def test_lower_generation_raises_generation_handoff_pending(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=5)
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=5, source_commit=head_a)

            _write_generation(origin, 3)
            fixtures.commit_all(origin, "revert to generation 3")

            with self.assertRaises(GenerationHandoffPendingError) as ctx:
                handoff.detect(ident, origin)
            self.assertEqual(ctx.exception.evidence["pinned_generation"], 5)
            self.assertEqual(ctx.exception.evidence["approved_generation"], 3)

    def test_uncommitted_generation_bump_does_not_reach_the_lower_branch(self) -> None:
        """Both the pinned and the approved generation are read from the
        same committed ``HEAD`` (CP1's own generation-source rule), so an
        *uncommitted* local bump can never by itself make them disagree --
        the state is reported as a dirty source, never as a handoff."""
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=1)
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=1, source_commit=head_a)
            _write_generation(origin, 2)  # uncommitted
            self.assertIsNone(handoff.detect(ident, origin))

    def test_unpinned_identity_refuses_rather_than_comparing_against_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=1)
            ident = identity.ControllerIdentity(
                generation=None, source_root=origin, origin_source_root=origin,
                source_kind=identity.SOURCE_KIND_UNPINNED, source_commit=None, tree_digest=None,
                generation_source=None, pinned_at="now",
            )
            with self.assertRaises(SourceSnapshotError) as ctx:
                handoff.detect(ident, origin)
            self.assertEqual(ctx.exception.evidence["raised_by"], "detect")

    def test_missing_generation_json_at_head_refuses_with_no_worktree_fallback(self) -> None:
        """Unlike ``controller.identity``'s own generation read, ``detect``
        never falls back to the worktree: an uncommitted
        ``GENERATION.json`` must never be read as an approved
        generation."""
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=None)
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=1, source_commit=head_a)
            _write_generation(origin, 9)  # uncommitted -- must not be read
            with self.assertRaises(SourceSnapshotError) as ctx:
                handoff.detect(ident, origin)
            self.assertEqual(ctx.exception.evidence["raised_by"], "detect")

    def test_malformed_generation_json_at_head_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = fixtures.build_checkout(Path(td) / "origin", generation=1)
            (origin / "controller" / "GENERATION.json").write_text("not json\n")
            fixtures.commit_all(origin, "malform")
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=1, source_commit="deadbeef")
            with self.assertRaises(SourceSnapshotError) as ctx:
                handoff.detect(ident, origin)
            self.assertEqual(ctx.exception.evidence["raised_by"], "detect")
            self.assertEqual(ctx.exception.evidence["commit"], head_a)


class WriteHandoffRecordTest(unittest.TestCase):
    def test_writes_the_full_declared_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime"
            h = handoff.Handoff(
                from_generation=1, from_commit="a" * 40, to_generation=2, to_commit="b" * 40,
                source_root=Path("/origin"),
            )
            record = handoff.write_handoff_record(
                runtime_root, h, jobs_complete=["j2", "j1"], jobs_open=[],
                next_generation_command="workflow-controller --runtime-dir /rt run /target",
                now="2024-01-01T00:00:00Z",
            )
            on_disk = runtime.read_json(runtime_root / "handoff.json")
            self.assertEqual(on_disk, record)
            self.assertEqual(record["running"], {"generation": 1, "commit": "a" * 40})
            self.assertEqual(record["approved"], {"generation": 2, "commit": "b" * 40})
            self.assertEqual(record["source_root"], "/origin")
            self.assertEqual(record["detected_at"], "2024-01-01T00:00:00Z")
            self.assertEqual(record["jobs_complete"], ["j1", "j2"])  # sorted
            self.assertEqual(record["jobs_open"], [])
            self.assertEqual(
                record["next_generation_command"],
                "workflow-controller --runtime-dir /rt run /target",
            )


class ClassifyJobsTest(unittest.TestCase):
    def test_partitions_by_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime"
            (runtime_root / "jobs").mkdir(parents=True)
            runtime.write_json(runtime_root, "jobs/finished.json", {"status": job.STATUS_FINISHED})
            runtime.write_json(runtime_root, "jobs/failed.json", {"status": job.STATUS_FAILED})
            runtime.write_json(runtime_root, "jobs/launched.json", {"status": job.STATUS_LAUNCHED})
            runtime.write_json(runtime_root, "jobs/planned.json", {"status": job.STATUS_PLANNED})
            complete, open_ = cli._classify_jobs(runtime_root)
            self.assertEqual(sorted(complete), ["failed", "finished"])
            self.assertEqual(sorted(open_), ["launched", "planned"])

    def test_empty_jobs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime"
            runtime_root.mkdir()
            self.assertEqual(cli._classify_jobs(runtime_root), ([], []))


class PauseFileGatingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get(cli.TEST_HOOKS_ENV)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._old is None:
            os.environ.pop(cli.TEST_HOOKS_ENV, None)
        else:
            os.environ[cli.TEST_HOOKS_ENV] = self._old

    def test_no_pause_file_returns_immediately(self) -> None:
        os.environ[cli.TEST_HOOKS_ENV] = "1"
        started = time.monotonic()
        cli._await_pause_file(None)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_inert_without_test_hooks_env_even_though_file_exists(self) -> None:
        os.environ.pop(cli.TEST_HOOKS_ENV, None)
        with tempfile.TemporaryDirectory() as td:
            sentinel = Path(td) / "sentinel"
            sentinel.write_text("")
            started = time.monotonic()
            cli._await_pause_file(str(sentinel))
            self.assertLess(time.monotonic() - started, 1.0)

    def test_wrong_test_hooks_value_is_also_inert(self) -> None:
        os.environ[cli.TEST_HOOKS_ENV] = "true"
        with tempfile.TemporaryDirectory() as td:
            sentinel = Path(td) / "sentinel"
            sentinel.write_text("")
            started = time.monotonic()
            cli._await_pause_file(str(sentinel))
            self.assertLess(time.monotonic() - started, 1.0)

    def test_blocks_while_file_exists_when_enabled(self) -> None:
        os.environ[cli.TEST_HOOKS_ENV] = "1"
        with tempfile.TemporaryDirectory() as td:
            sentinel = Path(td) / "sentinel"
            sentinel.write_text("")

            def _delete_later() -> None:
                time.sleep(0.2)
                sentinel.unlink(missing_ok=True)

            threading.Thread(target=_delete_later, daemon=True).start()
            started = time.monotonic()
            cli._await_pause_file(str(sentinel))
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.15)
            self.assertFalse(sentinel.exists())


# ---------------------------------------------------------------------------
# The run loop's own exit-code mapping, exercised in-process against a
# monkeypatched ``controller.job.execute_step`` -- CP6's/CP6B's own
# correctness (what a real worker run produces) is that module's own test
# files' concern; this suite's concern is only "does `cmd_run` translate
# each of `execute_step`'s reachable return shapes into the right exit
# code and stop/continue decision."
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, repo: str, *, pause_file: str | None = None, max_steps: int = 20) -> None:
        self.repo = repo
        self.pause_file = pause_file
        self.max_steps = max_steps
        self.work_item = None
        self.permission_mode = None
        self.timeout = None
        self.claude_binary = None
        self.workflow_manager = "stub-unused"


class RunLoopExitCodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

        self.origin = fixtures.build_checkout(self.tmp_root / "origin", generation=1)
        head_a = fixtures.current_head(self.origin)
        self.ident = _pinned_identity(self.origin, generation=1, source_commit=head_a)

        self.target_root = self.tmp_root / "target"
        fixtures.build_managed_repo(self.target_root)
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp_root / "workflow-manager")

        self.runtime_root = self.tmp_root / "runtime"

        self._orig_execute_step = job.execute_step
        self.addCleanup(self._restore_execute_step)

        # `require_pinned_execution()` reads the process-global cached pin
        # (`identity.current()`), never the `ident` object passed into a
        # command function directly -- so it must be monkeypatched to
        # this fixture's own pinned identity, exactly as
        # `test_identity.py`'s own `GuardTest` does.
        self._orig_pin = identity.pin
        self._orig_current = identity.current
        identity.pin = lambda: self.ident
        identity.current = lambda: self.ident
        self.addCleanup(self._restore_identity)

    def _restore_identity(self) -> None:
        identity.pin = self._orig_pin
        identity.current = self._orig_current

    def _restore_execute_step(self) -> None:
        job.execute_step = self._orig_execute_step

    def _args(self, **kwargs) -> _Args:
        args = _Args(str(self.target_root), **kwargs)
        args.workflow_manager = str(self.stub_manager)
        return args

    def _run(self, **kwargs) -> int:
        return cli.cmd_run(self._args(**kwargs), self.runtime_root, self.ident)

    def test_gate_blocked_exits_10(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_GATE_BLOCKED}
        self.assertEqual(self._run(), cli.EXIT_GATE)

    def test_declined_exits_15(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_DECLINED}
        self.assertEqual(self._run(), cli.EXIT_DECLINED)

    def test_failed_exits_30(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_FAILED}
        self.assertEqual(self._run(), cli.EXIT_WORKER_FAILED)

    def test_incomplete_exits_35(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_INCOMPLETE}
        self.assertEqual(self._run(), cli.EXIT_INCOMPLETE)

    def test_handoff_pending_status_exits_50(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_HANDOFF_PENDING}
        self.assertEqual(self._run(), cli.EXIT_HANDOFF_PENDING)
        # execute_step's own check caught it -- this loop never called
        # handoff.write_handoff_record, so no record was published here.
        self.assertIsNone(runtime.read_json(self.runtime_root / "handoff.json"))

    def test_no_action_decision_exits_0(self) -> None:
        decision = Decision(
            observed_phase="MILESTONE_COMPLETE", evidence=(), action=None,
            automatic=False, gate=None, declined=False, reason="nothing left",
        )
        job.execute_step = lambda *a, **k: decision
        self.assertEqual(self._run(), cli.EXIT_OK)

    def test_finished_then_no_action_loops_once_and_exits_0(self) -> None:
        calls = {"n": 0}

        def fake(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"status": job.STATUS_FINISHED}
            return Decision(
                observed_phase="MILESTONE_COMPLETE", evidence=(), action=None,
                automatic=False, gate=None, declined=False, reason="nothing left",
            )

        job.execute_step = fake
        self.assertEqual(self._run(), cli.EXIT_OK)
        self.assertEqual(calls["n"], 2)

    def test_max_steps_reached_exits_16(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_FINISHED}
        self.assertEqual(self._run(max_steps=3), cli.EXIT_MAX_STEPS)

    def test_detected_handoff_at_the_boundary_writes_the_record_and_exits_50_before_any_launch(self) -> None:
        launched = {"n": 0}

        def fake(*a, **k):
            launched["n"] += 1
            return {"status": job.STATUS_FINISHED}

        job.execute_step = fake
        _write_generation(self.origin, 2)
        fixtures.commit_all(self.origin, "bump to generation 2")

        self.assertEqual(self._run(), cli.EXIT_HANDOFF_PENDING)
        self.assertEqual(launched["n"], 0)
        record = runtime.read_json(self.runtime_root / "handoff.json")
        self.assertEqual(record["running"]["generation"], 1)
        self.assertEqual(record["approved"]["generation"], 2)
        self.assertIn("next_generation_command", record)


class EqualGenerationRunLoopContinuesTest(unittest.TestCase):
    """The branch that makes the Controller usable while its own
    repository is under ordinary development: an unrelated commit to the
    origin never stops the loop or writes ``handoff.json``."""

    def test_unrelated_origin_commit_does_not_stop_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_root = Path(td)
            origin = fixtures.build_checkout(tmp_root / "origin", generation=1)
            head_a = fixtures.current_head(origin)
            ident = _pinned_identity(origin, generation=1, source_commit=head_a)

            target_root = tmp_root / "target"
            fixtures.build_managed_repo(target_root)
            stub_manager = fixtures.write_stub_workflow_manager(tmp_root / "workflow-manager")
            runtime_root = tmp_root / "runtime"

            (origin / "pyproject.toml").write_text(
                (origin / "pyproject.toml").read_text() + "\n# unrelated development\n"
            )
            fixtures.commit_all(origin, "unrelated")

            orig_execute_step = job.execute_step
            orig_pin, orig_current = identity.pin, identity.current
            try:
                identity.pin = lambda: ident
                identity.current = lambda: ident
                decision = Decision(
                    observed_phase="MILESTONE_COMPLETE", evidence=(), action=None,
                    automatic=False, gate=None, declined=False, reason="nothing left",
                )
                job.execute_step = lambda *a, **k: decision
                args = _Args(str(target_root))
                args.workflow_manager = str(stub_manager)
                exit_code = cli.cmd_run(args, runtime_root, ident)
            finally:
                job.execute_step = orig_execute_step
                identity.pin = orig_pin
                identity.current = orig_current

            self.assertEqual(exit_code, cli.EXIT_OK)
            self.assertIsNone(runtime.read_json(runtime_root / "handoff.json"))


# ---------------------------------------------------------------------------
# The real, end-to-end property: a live subprocess, paused at the
# orchestration boundary via the test-only `--pause-file` hook, observes a
# generation bump on the origin only after being unpaused, stops with
# exit 50, publishes the handoff record, and never executed the newer
# bytes.
# ---------------------------------------------------------------------------


class EndToEndHandoffSubprocessTest(unittest.TestCase):
    def test_pending_handoff_stops_the_running_generation_with_exit_50(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_root = Path(td)
            checkout = fixtures.build_checkout(tmp_root / "origin", generation=1)
            head_a = fixtures.current_head(checkout)
            venv_dir = tmp_root / "venv"
            try:
                console_script = fixtures.editable_install(checkout, venv_dir)
            except Exception as exc:  # pragma: no cover -- environment-dependent
                self.skipTest(f"editable install unavailable in this environment: {exc}")

            target_root = tmp_root / "target"
            fixtures.build_managed_repo(target_root)
            stub_manager = fixtures.write_stub_workflow_manager(tmp_root / "workflow-manager")

            runtime_root = tmp_root / "runtime"
            sentinel = tmp_root / "pause.sentinel"
            sentinel.write_text("")

            env = dict(os.environ)
            env.pop(identity.EXEC_HANDOFF_ENV, None)
            env["WORKFLOW_CONTROLLER_TEST_HOOKS"] = "1"

            import subprocess

            proc = subprocess.Popen(
                [
                    str(console_script), "--runtime-dir", str(runtime_root),
                    "--workflow-manager", str(stub_manager),
                    "run", str(target_root), "--pause-file", str(sentinel),
                ],
                cwd=checkout, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                # Step 3: wait for the paused child's own identity.json,
                # written before the first orchestration boundary is ever
                # reached, to show the pinned commit A / generation 1.
                deadline = time.monotonic() + 20
                record = None
                while time.monotonic() < deadline:
                    record = runtime.read_json(runtime_root / "identity.json")
                    if record is not None:
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(record, "child never wrote identity.json")
                self.assertEqual(record["source_kind"], "commit")
                self.assertEqual(record["generation"], 1)
                self.assertEqual(record["source_commit"], head_a)
                source_root = Path(record["source_root"])
                self.assertTrue(str(source_root).startswith(str((runtime_root / "source").resolve())))
                # Cheaper than a full probe subprocess, and the plan names
                # both as valid on their own: the recorded interpreter
                # sys.path never resolves into the mutable checkout.
                checkout_resolved = str(checkout.resolve())
                for entry in record["interpreter_sys_path"]:
                    self.assertNotEqual(entry, checkout_resolved)

                # Step 4: move the origin's HEAD to a newer generation
                # while the child is paused, with an observable source
                # change alongside it.
                marker = "# GENERATION-2-MARKER -- must never reach the paused snapshot\n"
                cli_py = checkout / "controller" / "cli.py"
                cli_py.write_text(cli_py.read_text() + marker)
                _write_generation(checkout, 2)
                head_b = fixtures.commit_all(checkout, "bump to generation 2")

                # The paused child's own snapshot is untouched by step 4.
                snapshot_cli_py = source_root / "controller" / "cli.py"
                self.assertNotIn(marker.strip(), snapshot_cli_py.read_text())
                self.assertEqual(identity.compute_tree_digest(source_root), source_root.name)

                # Step 5: unpause.
                sentinel.unlink()

                stdout, stderr = proc.communicate(timeout=30)
            except Exception:
                proc.kill()
                proc.communicate()
                raise

            # Step 6.
            self.assertEqual(proc.returncode, 50, f"stdout={stdout!r} stderr={stderr!r}")
            handoff_record = runtime.read_json(runtime_root / "handoff.json")
            self.assertIsNotNone(handoff_record)
            self.assertEqual(handoff_record["running"], {"generation": 1, "commit": head_a})
            self.assertEqual(handoff_record["approved"], {"generation": 2, "commit": head_b})
            self.assertEqual(handoff_record["source_root"], str(checkout.resolve()))

            # The process's own reported identity is still (1, A) -- never
            # rewritten to (2, B). identity.json is written exactly once,
            # before the command body (and so before the loop) ever runs.
            final_identity = runtime.read_json(runtime_root / "identity.json")
            self.assertEqual(final_identity["generation"], 1)
            self.assertEqual(final_identity["source_commit"], head_a)

            # The snapshot the process actually ran from still verifies
            # against its own recorded digest -- CP1's snapshot integrity
            # holds even after the origin moved out from under it.
            self.assertEqual(identity.compute_tree_digest(source_root), source_root.name)
            self.assertNotIn(marker.strip(), snapshot_cli_py.read_text())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
