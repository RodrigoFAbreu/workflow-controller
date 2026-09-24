"""Tests for the Controller's command-line surface (capability 10, CP9's
own "CLI completion").

CP1 built and tested the parser, the pinned-execution guard and the
materialise-and-re-exec mechanism (``tests/test_identity.py``); CP8 built
and tested the ``run`` loop's own exit-code mapping and the handoff
boundary (``tests/test_handoff.py``). This file covers what CP9 itself
adds: ``inspect``, ``explain``, ``step`` and ``resume`` wired to their real
behaviour, the parser's own command-line surface (including the
``OPUS-R34-B2`` global-option-ordering fixture pair the plan's own
document-consistency property cites by name), and ``step``/``resume``'s
own exit-code mappings (``step`` shares ``run``'s per-status codes through
``cli._run_one_step`` but, unlike ``run``, never loops past a single
``FINISHED``; ``resume`` adds the one exit code CP8's own test file never
exercises, ``40``, for a record reconciled to ``INTERRUPTED``).

As with the other checkpoints' own test files, command functions are
called directly with a hand-built ``args`` namespace and a real or fake
``ControllerIdentity`` -- ``test_handoff.py``'s own established technique
-- rather than driving every case through a subprocess.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import errno
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import (  # noqa: E402
    cli, evidence, identity, job, lock, managed_repo, observe, routing, runtime, worker,
)
from controller.decision import Action, Decision  # noqa: E402
from controller.errors import (  # noqa: E402
    GitDirectoryUnresolvableError,
    JobAbandonRefusedError,
    LifecycleLockError,
    LifecycleWorkerActiveError,
    LifecycleWorkerUnverifiableError,
    PendingJobReconciliationError,
    RuntimeContainmentError,
    SourceSnapshotError,
    UnmanagedRepositoryError,
)
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT  # noqa: E402
from tests import fixtures, process_fixtures  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"

FAKE_IDENTITY = ControllerIdentity(
    generation=7,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_COMMIT,
    source_commit="a" * 40,
    tree_digest="b" * 64,
    generation_source="head",
    pinned_at="2024-01-01T00:00:00Z", version=fixtures.CONTROLLER_VERSION,
)

#: A valid release ``BUILD_INFO.json`` for the running version.
RELEASE_BUILD = {
    "schema_version": 1, "name": "workflow-controller", "version": "1.1.0",
    "source_commit": "0123456789ab" + "c" * 28, "source_dirty": False,
    "package_digest": "3f2a1c9d0b7e" + "d" * 52, "build_origin": "release", "release_tag": "v1.1.0",
}


def _package_identity(build: dict) -> ControllerIdentity:
    return dataclasses.replace(
        FAKE_IDENTITY, runtime_kind="package", source_kind="package", build=build,
        source_commit=build["source_commit"] if build["source_dirty"] is False else None,
    )


class _Args:
    """A minimal stand-in for ``argparse.Namespace`` carrying exactly the
    attributes a command function reads -- every global option plus
    whichever subcommand-only ones a given test needs."""

    def __init__(self, repo: str, *, work_item: str | None = None, json_out: bool = False,
                 workflow_manager: str | None = None, permission_mode: str | None = None,
                 timeout: int | None = None, claude_binary: str | None = None,
                 max_steps: int = 20, pause_file: str | None = None) -> None:
        self.repo = repo
        self.work_item = work_item
        self.json = json_out
        self.workflow_manager = workflow_manager
        self.permission_mode = permission_mode
        self.timeout = timeout
        self.claude_binary = claude_binary
        self.max_steps = max_steps
        self.pause_file = pause_file


# ---------------------------------------------------------------------------
# The parser: `build_parser()` itself, plus the global-option-ordering
# fixture pair the plan's own document-consistency property (CP9's own
# "command line" half) cites by address as `OPUS-R34-B2`'s pin.
# ---------------------------------------------------------------------------


class ParserTest(unittest.TestCase):
    def test_every_command_is_declared(self) -> None:
        parser = cli.build_parser()
        subparsers_action = next(
            a for a in parser._subparsers._group_actions if a.dest == "command"
        )
        self.assertEqual(set(subparsers_action.choices), cli.ALL_COMMANDS)

    def test_no_command_is_a_usage_error(self) -> None:
        parser = cli.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args([])
        self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)

    def test_global_options_parse_before_the_subcommand(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([
            "--runtime-dir", "/tmp/rt", "--work-item", "wi-1", "--json",
            "step", "/target",
        ])
        self.assertEqual(args.command, "step")
        self.assertEqual(args.runtime_dir, "/tmp/rt")
        self.assertEqual(args.work_item, "wi-1")
        self.assertTrue(args.json)
        self.assertEqual(args.repo, "/target")

    def test_permission_mode_before_subcommand_parses(self) -> None:
        """The `OPUS-R34-B2` fixture's own `must not fail` half."""
        parser = cli.build_parser()
        args = parser.parse_args(["--permission-mode", "X", "step", "<repo>"])
        self.assertEqual(args.permission_mode, "X")
        self.assertEqual(args.repo, "<repo>")

    def test_permission_mode_after_subcommand_is_a_usage_error(self) -> None:
        """The `OPUS-R34-B2` fixture's own `must fail` half: a global
        option placed after the subcommand is `error: unrecognized
        arguments` and exits 2, exactly as measured against the live
        parser in the plan's own CP9 section."""
        parser = cli.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["step", "<repo>", "--permission-mode", "X"])
        self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)

    def test_run_max_steps_default_and_pause_file_are_run_only(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["run", "/target"])
        self.assertEqual(args.max_steps, 20)
        self.assertIsNone(args.pause_file)
        # `--max-steps` is `run`'s own option, not a global -- absent from
        # `step`'s subparser.
        with self.assertRaises(SystemExit):
            parser.parse_args(["step", "/target", "--max-steps", "5"])

    def test_status_takes_no_positional_repo(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.command, "status")
        self.assertFalse(hasattr(args, "repo"))

    def test_follow_options(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["--runtime-dir", "/rt", "--json", "follow"])
        self.assertEqual((args.command, args.repo, args.job, args.run, args.from_start, args.json),
                         ("follow", ".", None, None, False, True))
        args = parser.parse_args(["follow", "--run", "r1", "--from-start", "/target"])
        self.assertEqual((args.run, args.from_start, args.repo), ("r1", True, "/target"))
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["follow", "--run", "r1", "--job", "j1"])
        self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)

    def test_follow_is_a_step_and_run_flag_only(self) -> None:
        parser = cli.build_parser()
        for command in ("step", "run"):
            self.assertTrue(parser.parse_args([command, "/t", "--follow"]).follow)
            self.assertFalse(parser.parse_args([command, "/t"]).follow)
        for argv in (["resume", "--follow", "/t"], ["--follow", "step", "/t"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)


class VersionFlagTest(unittest.TestCase):
    """`--version` (release-runtime-observability CP1/CP2): line 1 is
    exactly `workflow-controller <version>`, line 2 describes the
    running runtime; exit 0, no subcommand needed, nothing written."""

    def _line1(self) -> str:
        return f"workflow-controller {fixtures.CONTROLLER_VERSION}"

    def _assert_version_run(self, result, *, checkout: Path, runtime_dir: Path) -> None:
        self.assertEqual(result.returncode, cli.EXIT_OK, result.stderr)
        head = fixtures.run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
        self.assertEqual(
            result.stdout,
            f"{self._line1()}\nruntime: source ({checkout.resolve()} @ {head[:12]})\n",
        )
        self.assertEqual(result.stderr, "")
        self.assertFalse(runtime_dir.exists())
        self.assertFalse((checkout / ".controller").exists())
        status = fixtures.run(["git", "status", "--porcelain", "--ignored"], cwd=checkout)
        self.assertEqual(status.stdout, "")

    def test_source_checkout_prints_two_lines_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            checkout = fixtures.build_checkout(tmp_path / "checkout")
            runtime_dir = tmp_path / "does-not-exist"
            env = {**os.environ, "PYTHONPATH": str(checkout), "XDG_STATE_HOME": str(tmp_path / "xdg")}
            result = fixtures.run(
                [sys.executable, "-P", "-B", "-m", "controller", "--runtime-dir", str(runtime_dir),
                 "--version"],
                cwd=tmp_path, env=env, check=False,
            )
            self._assert_version_run(result, checkout=checkout, runtime_dir=runtime_dir)
            self.assertFalse((tmp_path / "xdg").exists())

    def test_editable_install_prints_two_lines_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            checkout = fixtures.build_checkout(tmp_path / "checkout")
            console_script = fixtures.editable_install(checkout, tmp_path / "venv")
            # the editable install itself leaves an ignored *.egg-info behind
            fixtures.run(["git", "clean", "-fdqX"], cwd=checkout)
            runtime_dir = tmp_path / "does-not-exist"
            env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "xdg"), "PYTHONDONTWRITEBYTECODE": "1"}
            result = fixtures.run([str(console_script), "--runtime-dir", str(runtime_dir), "--version"],
                                  cwd=tmp_path, env=env, check=False)
            self._assert_version_run(result, checkout=checkout, runtime_dir=runtime_dir)
            self.assertFalse((tmp_path / "xdg").exists())

    def test_build_parser_runs_no_subprocess_and_version_needs_no_subcommand(self) -> None:
        with unittest.mock.patch("subprocess.run", side_effect=AssertionError("subprocess.run")) as run, \
                unittest.mock.patch("subprocess.Popen", side_effect=AssertionError("Popen")) as popen:
            parser = cli.build_parser()
        run.assert_not_called()
        popen.assert_not_called()
        stdout = io.StringIO()
        with unittest.mock.patch.object(cli.identity, "pin", return_value=_package_identity(RELEASE_BUILD)), \
                contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, cli.EXIT_OK)
        self.assertEqual(stdout.getvalue().splitlines()[0], self._line1())

    def test_line2_describes_each_runtime_kind(self) -> None:
        commit = RELEASE_BUILD["source_commit"]
        digest = RELEASE_BUILD["package_digest"]
        local = {**RELEASE_BUILD, "build_origin": "local", "release_tag": None}
        cases = [
            (_package_identity(RELEASE_BUILD),
             f"package (release v1.1.0; built from {commit[:12]}; package {digest[:12]})"),
            (_package_identity(local), f"package (local build from {commit[:12]})"),
            (_package_identity({**local, "source_dirty": True}),
             f"package (local build from {commit[:12]}, uncommitted changes)"),
            (_package_identity({**local, "source_commit": None, "source_dirty": None}),
             "package (local build, unknown provenance)"),
            (dataclasses.replace(FAKE_IDENTITY, origin_source_root=Path("/checkout"),
                                 source_commit=commit),
             f"source (/checkout @ {commit[:12]})"),
            (dataclasses.replace(FAKE_IDENTITY, origin_source_root=Path("/checkout"),
                                 source_kind="worktree", source_commit=None),
             "source (/checkout, uncommitted changes)"),
            (dataclasses.replace(FAKE_IDENTITY, runtime_kind="unidentified", runtime_reason="no build info"),
             "unidentified (no build info)"),
        ]
        for ident, expected in cases:
            stdout = io.StringIO()
            with unittest.mock.patch.object(cli.identity, "pin", return_value=ident), \
                    contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["--version"])
            self.assertEqual(stdout.getvalue(), f"{self._line1()}\nruntime: {expected}\n")

    def test_an_unresolvable_identity_is_reported_not_raised(self) -> None:
        stdout = io.StringIO()
        error = SourceSnapshotError("snapshot tampered", evidence={"raised_by": "pin"})
        with unittest.mock.patch.object(cli.identity, "pin", side_effect=error), \
                contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
            cli.build_parser().parse_args(["--version"])
        self.assertEqual(ctx.exception.code, cli.EXIT_OK)
        self.assertEqual(stdout.getvalue().splitlines()[1], "runtime: unidentified (snapshot tampered)")


class StatusFirstLineTest(unittest.TestCase):
    """CP2: `status` opens with the running process's `controller:` line;
    every other line is unchanged."""

    def test_controller_line_comes_first_and_the_rest_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            checkout = fixtures.build_checkout(tmp_path / "checkout")
            head = fixtures.run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
            runtime_root = tmp_path / "runtime"
            env = {**os.environ, "PYTHONPATH": str(checkout)}
            env.pop(identity.EXEC_HANDOFF_ENV, None)
            argv = [sys.executable, "-P", "-B", "-m", "controller", "--runtime-dir", str(runtime_root), "status"]
            expected_first = (f"controller: workflow-controller {fixtures.CONTROLLER_VERSION} -- "
                              f"source ({checkout.resolve()} @ {head[:12]})")

            first = fixtures.run(argv, cwd=tmp_path, env=env, check=False)
            self.assertEqual(first.returncode, cli.EXIT_OK, first.stderr)
            self.assertEqual(first.stdout.splitlines(), [
                expected_first,
                f"no Controller runtime state at {runtime_root.resolve()} (ladder row 1)",
            ])

            second = fixtures.run(argv, cwd=tmp_path, env=env, check=False)
            lines = second.stdout.splitlines()
            self.assertEqual(lines[0], expected_first)
            self.assertTrue(lines[1].startswith("pinned identity: source_kind=unpinned "), lines)
            self.assertEqual(lines[2:], [
                "jobs: none", "handoff: none", "active: none",
                f"runtime root: {runtime_root.resolve()} (ladder row 1)",
            ])
            record = runtime.read_json(runtime_root / "identity.json")
            self.assertEqual(record["controller_runtime"]["runtime_kind"], "source")
            self.assertEqual(record["controller_runtime"]["source_commit"], head)


# ---------------------------------------------------------------------------
# A real managed target: `.workflow-manager/installation.json` + a stub
# `workflow-manager` + a real `WORKFLOW_STATE.json` -- exercises
# `managed_repo.inspect` -> `target_state.read` for real, the same
# pipeline `inspect`/`explain`/`step`/`resume` all open with.
# ---------------------------------------------------------------------------


def _build_managed_target(tmp_root: Path, *, phase: str, work_item_id: str = "wi-1",
                           governing_workflow_version: str | None = "2.1") -> Path:
    repo = fixtures.build_managed_repo(tmp_root / "repo")
    entry = {
        "work_item_type": "product",
        "work_item_kind": "product",
        "work_item_id": work_item_id,
        "governing_workflow_version": governing_workflow_version,
        "phase": phase,
        "plan_revision": 3,
        "implementation_revision": None,
        "functional_review_round": None,
        "state_revision": 1,
        "checkpoints": {"CP1": {"status": "COMPLETE"}},
        "current_bundle_id": None,
        "current_checkpoint_id": None,
        "last_completed_checkpoint_id": "CP1",
        "base_commit": "0" * 40,
        "parent_work_item_id": None,
        "plan_approval": None,
        "technical_approval": None,
        "functional_acceptance_status": None,
        "plan_review_stages": None,
    }
    state = {
        "schema_version": 1,
        "active_work_item_id": work_item_id,
        "work_items": {work_item_id: entry},
    }
    fixtures.write_workflow_state(repo, state)
    return repo


def _build_managed_target_with_no_work_items(tmp_root: Path) -> Path:
    """The bootstrap fixture (revision 63's B2, `target_state.NoWorkItemYet`):
    a real managed repository whose `WORKFLOW_STATE.json` has zero
    `work_items` entries and no `active_work_item_id` -- the state
    `target_state.select_work_item` resolves to the `NoWorkItemYet`
    sentinel rather than a `WorkItemView`."""
    repo = fixtures.build_managed_repo(tmp_root / "repo")
    state = {"schema_version": 1, "active_work_item_id": None, "work_items": {}}
    fixtures.write_workflow_state(repo, state)
    return repo


class InspectCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.repo = _build_managed_target(self.tmp_root, phase="PLANNING")
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp_root / "workflow-manager")
        self.runtime_root = self.tmp_root / "runtime"

    def _args(self, **kwargs) -> _Args:
        return _Args(str(self.repo), workflow_manager=str(self.stub_manager), **kwargs)

    def test_text_report_names_repository_and_phase(self, capsys=None) -> None:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_inspect(self._args(), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("Workflow 2.5.1", out)
        self.assertIn("phase: PLANNING", out)
        self.assertIn("wi-1", out)

    def test_json_report_carries_the_full_work_item_payload(self) -> None:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_inspect(self._args(json_out=True), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["repository"]["workflow_version"], "2.5.1")
        self.assertEqual(payload["work_item"]["work_item_id"], "wi-1")
        self.assertEqual(payload["work_item"]["phase"], "PLANNING")
        self.assertEqual(payload["work_item"]["last_completed_checkpoint_id"], "CP1")
        self.assertEqual(payload["controller"], identity.runtime_record(FAKE_IDENTITY))

    def test_unmanaged_repository_refuses(self) -> None:
        bare = fixtures.build_bare_git_repo(self.tmp_root / "bare")
        args = _Args(str(bare), workflow_manager=str(self.stub_manager))
        with self.assertRaises(UnmanagedRepositoryError):
            cli.cmd_inspect(args, self.runtime_root, FAKE_IDENTITY)

    def test_no_work_item_yet_text_report_names_repository_only(self) -> None:
        """Revision 63/64's B2 `NoWorkItemYet` bootstrap sentinel
        (CONTROLLER_GEN1_PLAN.md's "NoWorkItemYet CLI dispatch"): `inspect`
        reports the bootstrap state directly rather than building a
        work-item payload (there is none to build), and never raises
        `AttributeError` against the sentinel."""
        repo = _build_managed_target_with_no_work_items(self.tmp_root / "no-work-item-text")
        args = _Args(str(repo), workflow_manager=str(self.stub_manager))
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_inspect(args, self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("Workflow 2.5.1", out)
        self.assertIn("work item: none", out)

    def test_no_work_item_yet_json_report_carries_a_null_work_item(self) -> None:
        repo = _build_managed_target_with_no_work_items(self.tmp_root / "no-work-item-json")
        args = _Args(str(repo), workflow_manager=str(self.stub_manager), json_out=True)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_inspect(args, self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["repository"]["workflow_version"], "2.5.1")
        self.assertIsNone(payload["work_item"])
        self.assertEqual(payload["controller"], identity.runtime_record(FAKE_IDENTITY))


class ExplainCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp_root / "workflow-manager")
        self.runtime_root = self.tmp_root / "runtime"

    def _args(self, repo: Path, **kwargs) -> _Args:
        return _Args(str(repo), workflow_manager=str(self.stub_manager), **kwargs)

    def test_automatic_phase_reports_the_action_and_exits_ok(self) -> None:
        repo = _build_managed_target(self.tmp_root / "auto", phase="PLANNING")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_explain(self._args(repo), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("phase: PLANNING", out)
        self.assertIn("next automatic action: /milestone-plan wi-1", out)

    def test_gate_phase_reports_what_a_human_must_do(self) -> None:
        repo = _build_managed_target(self.tmp_root / "gate", phase="AWAITING_PLAN_APPROVAL")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_explain(self._args(repo), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("human gate", out)
        self.assertIn("/approve-review plan wi-1", out)

    def test_implementing_without_a_current_plan_approval_reports_the_gate(self) -> None:
        """automatic-lifecycle-orchestration CP3: ``"2.1"`` ``IMPLEMENTING``
        with ``plan_approval`` absent used to report a declined
        ``/milestone-implement``; it is now the plan-approval gate."""
        repo = _build_managed_target(self.tmp_root / "plan-approval-gate", phase="IMPLEMENTING")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_explain(self._args(repo), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("human gate", out)
        self.assertIn("entry validation (step 1a)", out)
        self.assertIn("safe resume command: workflow-controller explain --work-item wi-1", out)
        self.assertNotIn("declined", out)

    def test_declined_phase_reports_the_declined_action(self) -> None:
        """Re-pinned at ``"1"`` ``IMPLEMENTING``, which has no
        ``ExpectedOutcome`` row: the general dispatch rule declines it."""
        repo = _build_managed_target(
            self.tmp_root / "declined", phase="IMPLEMENTING", governing_workflow_version="1",
        )
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_explain(self._args(repo), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("declined", out)
        self.assertIn("/milestone-implement wi-1", out)
        self.assertIn("no verifiable ExpectedOutcome is declared for (IMPLEMENTING, \"1\", "
                      "/milestone-implement)", out)

    def test_json_report_carries_gate_and_reason(self) -> None:
        repo = _build_managed_target(self.tmp_root / "gate-json", phase="AWAITING_PLAN_APPROVAL")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_explain(self._args(repo, json_out=True), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["observed_phase"], "AWAITING_PLAN_APPROVAL")
        self.assertFalse(payload["automatic"])
        self.assertIsNotNone(payload["gate"])
        self.assertEqual(payload["gate"]["safe_resume_command"], "/approve-review plan wi-1")
        self.assertEqual(payload["controller"], identity.runtime_record(FAKE_IDENTITY))

    def test_never_writes_a_job_record(self) -> None:
        """`explain` only ever calls `evidence.decide`, never
        `job.execute_step` -- proven by leaving the runtime root
        untouched rather than only by reading the source."""
        repo = _build_managed_target(self.tmp_root / "no-write", phase="PLANNING")
        cli.cmd_explain(self._args(repo), self.runtime_root, FAKE_IDENTITY)
        jobs_dir = self.runtime_root / "jobs"
        self.assertFalse(jobs_dir.is_dir() and any(jobs_dir.iterdir()))

    def test_no_work_item_yet_reports_bare_milestone_plan(self) -> None:
        """Revision 63's B2 `decide_no_work_item`: `explain` routes a
        `NoWorkItemYet` target to `decision.decide_no_work_item` directly
        (never `evidence.decide`, which would raise `AttributeError`
        dereferencing `work_item.phase` against the sentinel) and reports
        the unconditional bare `/milestone-plan` action -- no work-item id,
        since frozen Workflow alone derives and creates the first one."""
        repo = _build_managed_target_with_no_work_items(self.tmp_root / "no-work-item")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_explain(self._args(repo), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("next automatic action: /milestone-plan", out)
        self.assertNotIn("/milestone-plan wi-1", out)

    def test_no_work_item_yet_json_report_carries_the_wire_form_phase(self) -> None:
        """The JSON branch must map `decision.observed_phase` (the
        in-memory `NO_PHASE` sentinel for this case) through
        `decision.phase_to_wire` before serialising -- a raw `NO_PHASE` is
        not JSON-serialisable at all."""
        repo = _build_managed_target_with_no_work_items(self.tmp_root / "no-work-item-json")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_explain(self._args(repo, json_out=True), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["observed_phase"], "__NO_PHASE__")
        self.assertTrue(payload["automatic"])
        self.assertIsNone(payload["gate"])
        self.assertEqual(payload["action"], "/milestone-plan")


# ---------------------------------------------------------------------------
# `step`: one call through `cli._run_one_step`, sharing `run`'s own
# per-status exit-code mapping (`tests/test_handoff.py`'s
# `RunLoopExitCodeTest` pins that mapping exhaustively for `run`) but
# stopping after exactly one action rather than looping on `FINISHED`.
# ---------------------------------------------------------------------------


class _StepFixture:
    """The managed target, stub manager, pinned identity and
    ``job.execute_step`` save/restore shared by the ``step``/``run``
    command tests below."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.repo = _build_managed_target(self.tmp_root, phase="PLANNING")
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp_root / "workflow-manager")
        self.runtime_root = self.tmp_root / "runtime"

        # A real origin checkout at generation 1, matching `self.ident`'s
        # own pinned generation -- so `handoff.detect()` (which
        # `_run_one_step` calls first, on every path) returns `None`
        # (ordinary in-generation development) rather than refusing on a
        # nonexistent origin. `PendingHandoffTest` below builds its own
        # origin separately, bumped to generation 2, for the one case that
        # wants a real pending handoff.
        self.origin = fixtures.build_checkout(self.tmp_root / "origin", generation=1)
        head = fixtures.current_head(self.origin)
        self.ident = ControllerIdentity(
            generation=1, source_root=self.origin, origin_source_root=self.origin,
            source_kind=SOURCE_KIND_COMMIT, source_commit=head, tree_digest="d" * 64,
            generation_source="head", pinned_at="2024-01-01T00:00:00Z", version=fixtures.CONTROLLER_VERSION,
        )

        self._orig_execute_step = job.execute_step
        self.addCleanup(self._restore)

        import controller.identity as identity_module
        self._identity_module = identity_module
        self._orig_pin = identity_module.pin
        self._orig_current = identity_module.current
        identity_module.pin = lambda: self.ident
        identity_module.current = lambda: self.ident

    def _restore(self) -> None:
        self._identity_module.pin = self._orig_pin
        self._identity_module.current = self._orig_current
        job.execute_step = self._orig_execute_step

    def _args(self) -> _Args:
        return _Args(str(self.repo), workflow_manager=str(self.stub_manager))

    def _step(self) -> int:
        return cli.cmd_step(self._args(), self.runtime_root, self.ident)


class StepCommandTest(_StepFixture, unittest.TestCase):
    def test_finished_status_exits_ok_after_exactly_one_call(self) -> None:
        calls = []

        def fake_execute_step(*a, **k):
            calls.append(1)
            return {"status": job.STATUS_FINISHED}

        job.execute_step = fake_execute_step
        self.assertEqual(self._step(), cli.EXIT_OK)
        self.assertEqual(len(calls), 1)

    def test_gate_blocked_exits_10(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_GATE_BLOCKED}
        self.assertEqual(self._step(), cli.EXIT_GATE)

    def test_declined_exits_15(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_DECLINED}
        self.assertEqual(self._step(), cli.EXIT_DECLINED)

    def test_failed_exits_30(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_FAILED}
        self.assertEqual(self._step(), cli.EXIT_WORKER_FAILED)

    def test_incomplete_exits_35(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_INCOMPLETE}
        self.assertEqual(self._step(), cli.EXIT_INCOMPLETE)

    def test_handoff_pending_status_exits_50(self) -> None:
        job.execute_step = lambda *a, **k: {"status": job.STATUS_HANDOFF_PENDING}
        self.assertEqual(self._step(), cli.EXIT_HANDOFF_PENDING)

    def test_no_action_decision_exits_ok_and_writes_no_record(self) -> None:
        job.execute_step = lambda *a, **k: Decision(
            observed_phase="LEGACY_READY", evidence=(), action=None, automatic=False,
            gate=None, declined=False, reason="nothing to do",
        )
        self.assertEqual(self._step(), cli.EXIT_OK)

    def test_pending_handoff_is_detected_before_execute_step_runs(self) -> None:
        """`_run_one_step` -- shared with `run` -- checks
        `handoff.detect()` at its own orchestration boundary before
        calling `execute_step` at all: bump the origin's own committed
        generation past the running identity's pinned one and assert the
        worker is never launched."""
        (self.origin / "controller" / "GENERATION.json").write_text(
            json.dumps({"schema_version": 1, "generation": 2}) + "\n"
        )
        fixtures.commit_all(self.origin, "bump generation")

        calls = []
        job.execute_step = lambda *a, **k: calls.append(1)
        self.assertEqual(self._step(), cli.EXIT_HANDOFF_PENDING)
        self.assertEqual(calls, [])
        handoff_record = runtime.read_json(self.runtime_root / "handoff.json")
        self.assertEqual(handoff_record["running"]["generation"], 1)
        self.assertEqual(handoff_record["approved"]["generation"], 2)


class PermissionModePassThroughTest(_StepFixture, unittest.TestCase):
    """Worker-execution hardening CP1: a default ``step``/``run`` hands
    ``execute_step`` the lifecycle-worker default ``auto``; an explicit
    ``--permission-mode`` reaches it unchanged."""

    def _received_mode(self, command, permission_mode: str | None) -> str:
        received = []

        def fake_execute_step(*a, **k):
            received.append(k["permission_mode"])
            return {"status": job.STATUS_GATE_BLOCKED}

        job.execute_step = fake_execute_step
        args = _Args(str(self.repo), workflow_manager=str(self.stub_manager),
                     permission_mode=permission_mode)
        self.assertEqual(command(args, self.runtime_root, self.ident), cli.EXIT_GATE)
        self.assertEqual(len(received), 1)
        return received[0]

    def test_step_defaults_to_auto(self) -> None:
        self.assertEqual(self._received_mode(cli.cmd_step, None), "auto")

    def test_step_passes_explicit_modes_through(self) -> None:
        for mode in ("acceptEdits", "bypassPermissions"):
            with self.subTest(mode=mode):
                self.assertEqual(self._received_mode(cli.cmd_step, mode), mode)

    def test_run_defaults_to_auto(self) -> None:
        self.assertEqual(self._received_mode(cli.cmd_run, None), "auto")

    def test_run_passes_explicit_modes_through(self) -> None:
        for mode in ("acceptEdits", "bypassPermissions"):
            with self.subTest(mode=mode):
                self.assertEqual(self._received_mode(cli.cmd_run, mode), mode)


class RoutingOptionsParserTest(unittest.TestCase):
    """Automatic-lifecycle-orchestration CP6: the routing global options.
    A malformed ``--role-model``/``--role-effort`` (no ``=``, an unknown
    role, an unusable value, one role assigned twice) or an unusable
    ``--model``/``--effort`` is a usage error, exit 2, that writes
    nothing."""

    def test_the_routing_options_parse_before_the_subcommand(self) -> None:
        args = cli.build_parser().parse_args([
            "--model", "claude-sonnet-5", "--effort", "high",
            "--role-model", "review-plan=claude-opus-5-5", "--role-model", "milestone-plan=m",
            "--role-effort", "review-implementation=max", "--routing-config", "/cfg.json",
            "run", "/target",
        ])
        self.assertEqual((args.model, args.effort), ("claude-sonnet-5", "high"))
        self.assertEqual(args.role_model, {"review-plan": "claude-opus-5-5", "milestone-plan": "m"})
        self.assertEqual(args.role_effort, {"review-implementation": "max"})
        self.assertEqual(args.routing_config, "/cfg.json")

    def test_the_defaults_are_no_overrides(self) -> None:
        args = cli.build_parser().parse_args(["step", "/target"])
        for name in ("model", "effort", "role_model", "role_effort", "routing_config"):
            self.assertIsNone(getattr(args, name), name)
        self.assertEqual(cli._routing_options(args), routing.NO_OVERRIDES)

    def test_every_malformed_routing_option_exits_2_and_writes_nothing(self) -> None:
        cases = {
            "unknown role model": ["--role-model", "review-everything=m"],
            "unknown role effort": ["--role-effort", "nobody=high"],
            "no equals": ["--role-model", "review-plan"],
            "empty role value": ["--role-effort", "review-plan="],
            "option-like role value": ["--role-model", "review-plan=--resume"],
            "role assigned twice": ["--role-model", "review-plan=a", "--role-model", "review-plan=b"],
            "empty model": ["--model="],
            "option-like effort": ["--effort=-c"],
        }
        for name, options in cases.items():
            for command in ("step", "run"):
                with self.subTest(case=name, command=command), tempfile.TemporaryDirectory() as td:
                    runtime_dir = Path(td) / "runtime"
                    dispatched = []
                    stderr = io.StringIO()
                    with unittest.mock.patch.object(cli, "_dispatch", lambda *a: dispatched.append(a)), \
                            contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
                        cli.main(["--runtime-dir", str(runtime_dir), *options, command, td])
                    self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)
                    self.assertEqual(dispatched, [])
                    self.assertFalse(runtime_dir.exists(), "a usage error must write nothing")
                    self.assertIn(f"argument {options[0].split('=')[0]}: ", stderr.getvalue())


class RoutingOptionsPassThroughTest(_StepFixture, unittest.TestCase):
    """``step`` and ``run`` hand ``execute_step`` the operator's
    ``routing.RoutingOptions``, the config file parsed once, before any job
    runs; a malformed config is ``RoutingConfigError`` (exit 20) before a
    ``PLANNED`` record is written."""

    def _args_with(self, **routing_args) -> _Args:
        args = _Args(str(self.repo), workflow_manager=str(self.stub_manager))
        for name, value in routing_args.items():
            setattr(args, name, value)
        return args

    def _received(self, command, args) -> list:
        received = []

        def fake_execute_step(*a, **k):
            received.append(k["routing"])
            return {"status": job.STATUS_GATE_BLOCKED}

        job.execute_step = fake_execute_step
        self.assertEqual(command(args, self.runtime_root, self.ident), cli.EXIT_GATE)
        return received

    def test_step_and_run_pass_the_options_and_the_parsed_config(self) -> None:
        config_path = self.tmp_root / "routing.json"
        config_path.write_text(json.dumps({"schema_version": 1, "roles": {"review-plan": {"effort": "max"}}}))
        args = self._args_with(model="m", effort=None, role_model={"review-plan": "rm"},
                               role_effort={"milestone-plan": "low"}, routing_config=str(config_path))
        for command in (cli.cmd_step, cli.cmd_run):
            with self.subTest(command=command.__name__):
                [options] = self._received(command, args)
                self.assertEqual((options.cli_model, options.cli_effort), ("m", None))
                self.assertEqual(dict(options.cli_role_models), {"review-plan": "rm"})
                self.assertEqual(dict(options.cli_role_efforts), {"milestone-plan": "low"})
                self.assertEqual(dict(options.config.roles["review-plan"]), {"effort": "max"})

    def test_a_namespace_without_the_options_routes_by_the_built_in_table(self) -> None:
        for command in (cli.cmd_step, cli.cmd_run):
            with self.subTest(command=command.__name__):
                self.assertEqual(self._received(command, self._args_with()), [routing.NO_OVERRIDES])

    def test_an_invalid_config_exits_20_before_any_record_or_worker(self) -> None:
        job.execute_step = self._orig_execute_step  # the real one: nothing may reach it
        invocations = self.tmp_root / "invocations"
        bad = self.tmp_root / "bad-routing.json"
        configs = {
            "unparseable": "{",
            "schema_version": json.dumps({"schema_version": 2}),
            "unknown role": json.dumps({"schema_version": 1, "roles": {"review-everything": {}}}),
            "unknown key": json.dumps({"schema_version": 1, "default": {"single_agent": False}}),
            "missing file": None,
        }
        env = {"FAKE_CLAUDE_INVOCATIONS_FILE": str(invocations),
               "FAKE_CLAUDE_REQUIRE_FILE": str(self.tmp_root / "never-created")}
        for name, text in configs.items():
            for command in (cli.cmd_step, cli.cmd_run):
                with self.subTest(config=name, command=command.__name__):
                    if text is None:
                        bad.unlink(missing_ok=True)
                    else:
                        bad.write_text(text)
                    args = _Args(str(self.repo), workflow_manager=str(self.stub_manager),
                                 claude_binary=str(FAKE_CLAUDE))
                    args.routing_config = str(bad)
                    stderr = io.StringIO()
                    with unittest.mock.patch.dict("os.environ", env), \
                            unittest.mock.patch.object(cli, "_dispatch",
                                                       lambda a, argv: command(args, self.runtime_root, self.ident)), \
                            contextlib.redirect_stderr(stderr):
                        self.assertEqual(cli.main(["step", str(self.repo)]), cli.EXIT_FAIL_CLOSED)
                    self.assertIn(f"error: the routing config {bad} cannot be used", stderr.getvalue())
                    self.assertEqual(list(self.runtime_root.glob("jobs/*.json")), [])
                    self.assertFalse(invocations.exists())

    def test_a_valid_config_routes_the_real_launch(self) -> None:
        job.execute_step = self._orig_execute_step
        config_path = self.tmp_root / "routing.json"
        config_path.write_text(json.dumps({
            "schema_version": 1, "default": {"effort": "low"},
            "roles": {"milestone-plan": {"model": "claude-sonnet-5"}},
        }))
        diag = self.tmp_root / "diag.json"
        args = _Args(str(self.repo), workflow_manager=str(self.stub_manager), claude_binary=str(FAKE_CLAUDE),
                     timeout=10)
        args.routing_config = str(config_path)
        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_DIAG_FILE": str(diag)}):
            # A no-op fake publishes no plan revision, so nothing verifies.
            self.assertEqual(cli.cmd_step(args, self.runtime_root, self.ident), cli.EXIT_WORKER_FAILED)
        [record] = [json.loads(path.read_text()) for path in self.runtime_root.glob("jobs/*.json")]
        self.assertEqual(record["worker_route"], {
            "role": "milestone-plan", "model": "claude-sonnet-5", "effort": "low", "single_agent": False,
            "fresh_session": True, "sources": {"model": "config-role", "effort": "config-default"},
        })
        argv = json.loads(diag.read_text())["argv"]
        self.assertEqual(argv[-4:], ["--model", "claude-sonnet-5", "--effort", "low"])


class PartialApplyPlanReviewCliTest(_StepFixture, unittest.TestCase):
    """Worker-execution hardening CP5, the RepFlow case end to end through
    the CLI: a real ``step`` whose fake worker publishes plan revision 11
    but leaves the revision-10 bundle exits ``EXIT_WORKER_FAILED``, and
    ``explain`` then renders CP4's recovery steps."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = fixtures.build_managed_repo(self.tmp_root / "partial")
        head = fixtures.current_head(self.repo)
        fixtures.write_workflow_state(self.repo, {
            "schema_version": 1, "active_work_item_id": "wi-1",
            "work_items": {"wi-1": {
                "work_item_type": "product", "work_item_kind": "product", "work_item_id": "wi-1",
                "governing_workflow_version": "2.2", "phase": "REVISING_PLAN", "plan_revision": 10,
                "implementation_revision": None, "state_revision": 1, "checkpoints": {},
                "current_bundle_id": None, "last_completed_checkpoint_id": None,
                "base_commit": head, "parent_work_item_id": None,
            }},
        })
        self.base_commit = head
        fixtures.write_plan_manifest(self.repo, "wi-1", 10, generation_head=head)
        current = self.repo / ".ai-review" / "wi-1" / "current"
        (current / "REVIEW_REQUEST.md").write_text("review_content_id: " + "c" * 64 + "\n")
        (current / "TEST_RESULTS.md").write_text(f"stage: plan (revision 10)\nhead: {head}\n")
        (current / "CONTEXT_FILES.txt").write_text("docs/ai-workflow/REVIEW_PROTOCOL.md\n")
        fixtures.write_review_feedback(
            self.repo, ".ai-review/wi-1/feedback",
            fixtures.build_review_feedback_text(status="REVISE", reviewed_base_commit=head),
        )

    def _worker_env(self) -> dict[str, str]:
        state_path = self.repo / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"]["wi-1"].update(phase="AWAITING_LOCAL_PLAN_REVIEW", plan_revision=11, state_revision=2)
        return {"FAKE_CLAUDE_WRITES": json.dumps([
            {"path": str(state_path), "text": json.dumps(state, indent=2) + "\n"},
        ])}

    def _args(self, **kwargs) -> _Args:
        return _Args(str(self.repo), workflow_manager=str(self.stub_manager),
                     claude_binary=str(Path(__file__).resolve().parent / "fake_claude.py"),
                     timeout=10, **kwargs)

    def test_step_exits_worker_failed_then_explain_renders_the_recovery_steps(self) -> None:
        import contextlib
        import io
        import unittest.mock
        with unittest.mock.patch.dict("os.environ", self._worker_env()):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cli.cmd_step(self._args(), self.runtime_root, self.ident)
        self.assertEqual(exit_code, cli.EXIT_WORKER_FAILED)
        records = [runtime.read_json(p) for p in (self.runtime_root / "jobs").glob("*.json")]
        self.assertEqual([r["status"] for r in records], [job.STATUS_FAILED])
        self.assertEqual(records[0]["reconciliation_evidence"]["reason"], "postcondition_not_satisfied")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.cmd_explain(self._args(), self.runtime_root, self.ident), cli.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("phase: AWAITING_LOCAL_PLAN_REVIEW", out)
        self.assertIn("human gate", out)
        self.assertNotIn("next automatic action", out)
        self.assertIn("1. refresh .ai-review/wi-1/current/REVIEW_REQUEST.md", out)
        self.assertIn("2. refresh .ai-review/wi-1/current/TEST_RESULTS.md", out)
        self.assertIn("`stage: plan (revision 11)`", out)
        self.assertIn(f"3. run scripts/prepare-ai-review.sh {self.base_commit} plan wi-1", out)
        self.assertIn("manifest plan_revision 10 != state plan_revision 11", out)


class _ApplyingReviewFeedbackFixture(_StepFixture):
    """A ``"2.2"`` work item at ``APPLYING_REVIEW_FEEDBACK`` with an
    admissible local ``REVISE`` and the review-stage write still
    uncommitted (shared by the CP4B and CP5 CLI tests below)."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = fixtures.build_managed_repo(self.tmp_root / "apply")
        base = fixtures.current_head(self.repo)
        fixtures.write_workflow_state(self.repo, {
            "schema_version": 1, "active_work_item_id": "wi-1",
            "work_items": {"wi-1": {
                "work_item_type": "product", "work_item_kind": "product", "work_item_id": "wi-1",
                "governing_workflow_version": "2.2", "phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                "plan_revision": 1, "implementation_revision": 1, "reviewed_implementation_head": "1" * 40,
                "state_revision": 1, "checkpoints": {"CP1": {"status": "COMPLETE"}},
                "current_bundle_id": None, "last_completed_checkpoint_id": "CP1", "base_commit": base,
                "parent_work_item_id": None, "plan_approval": {"status": "CURRENT"},
            }},
        })
        fixtures.commit_paths(self.repo, "Record implementation bundle generation",
                              "docs/ai-workflow/WORKFLOW_STATE.json")
        fixtures.write_implementation_bundle(self.repo, "wi-1", 1, reviewed_implementation_head="1" * 40)
        fixtures.write_review_feedback(self.repo, ".ai-review/wi-1/feedback", fixtures.build_review_feedback_text(
            status="REVISE", reviewer_role="LOCAL_MODEL_IMPLEMENTATION_REVIEW", reviewed_base_commit=base,
        ))
        fixtures.update_workflow_state(self.repo, "wi-1", phase="APPLYING_REVIEW_FEEDBACK")
        self.target_repo = str(managed_repo.inspect(str(self.repo), manager_bin=str(self.stub_manager)).root)
        self.addendum = evidence.pending_review_stage_write_addendum(
            "wi-1", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
        )

    def _args(self, **kwargs) -> _Args:
        return _Args(str(self.repo), workflow_manager=str(self.stub_manager),
                     claude_binary=str(Path(__file__).resolve().parent / "fake_claude.py"),
                     timeout=10, **kwargs)

    def _explain(self, *, json_out: bool = False) -> str:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.cmd_explain(self._args(json_out=json_out), self.runtime_root, self.ident),
                             cli.EXIT_OK)
        return buf.getvalue()

    def _seed_failed_apply(self, job_id: str, *, target_repo: str | None = None) -> None:
        runtime.write_json(self.runtime_root, f"jobs/{job_id}.json", {
            "schema_version": job.SCHEMA_VERSION, "job_id": job_id,
            "target_repo": self.target_repo if target_repo is None else target_repo,
            "work_item_id": "wi-1", "observed_phase_before": "APPLYING_REVIEW_FEEDBACK",
            "pre_state": {"phase": "APPLYING_REVIEW_FEEDBACK", "bundle_manifest_bundle_id": "b" * 64},
            "selected_action": {"kind": "slash_command", "command": "/apply-implementation-review wi-1",
                                "task_addendum": None, "automatic": True, "declined": False,
                                "reason": "seeded", "evidence": []},
            "expected_transition": {"from": "APPLYING_REVIEW_FEEDBACK",
                                    "to_any_of": ["AWAITING_LOCAL_IMPLEMENTATION_REVIEW"]},
            "status": job.STATUS_FAILED, "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        })

    def _step_with(self, args: _Args) -> int:
        return cli.cmd_step(args, self.runtime_root, self.ident)


class ApplyingReviewFeedbackCliTest(_ApplyingReviewFeedbackFixture, unittest.TestCase):
    """Automatic-lifecycle-orchestration CP4B through the CLI: ``explain``
    reports the launch with its task addendum; with an unverified earlier
    apply job against the same bundle, ``explain`` and ``step`` report the
    same relaunch-bound gate; and ``explain`` reads the job history
    tolerantly (round 3's O5)."""

    def test_explain_reports_the_launch_and_its_task_addendum(self) -> None:
        out = self._explain()
        self.assertIn("next automatic action: /apply-implementation-review wi-1", out)
        self.assertIn(f"  task addendum: {self.addendum}", out)
        payload = json.loads(self._explain(json_out=True))
        self.assertTrue(payload["automatic"])
        self.assertEqual(payload["action"], "/apply-implementation-review wi-1")
        self.assertEqual(payload["task_addendum"], self.addendum)

    def test_explain_and_step_report_the_same_relaunch_bound_gate(self) -> None:
        import unittest.mock
        self._seed_failed_apply("earlier-apply")
        payload = json.loads(self._explain(json_out=True))
        self.assertFalse(payload["automatic"])
        self.assertIsNone(payload["action"])
        self.assertIn("job earlier-apply, ended FAILED", payload["gate"]["what_is_required"])

        diag = self.tmp_root / "diag.json"
        env = {"FAKE_CLAUDE_DIAG_FILE": str(diag),
               "FAKE_CLAUDE_REQUIRE_FILE": str(self.tmp_root / "never-created")}
        with unittest.mock.patch.dict("os.environ", env):
            self.assertEqual(self._step_with(self._args()), cli.EXIT_GATE)
        self.assertFalse(diag.exists(), "the fail-if-invoked worker must never start")
        records = [runtime.read_json(p) for p in sorted((self.runtime_root / "jobs").glob("*.json"))]
        gated = [r for r in records if r["status"] == job.STATUS_GATE_BLOCKED]
        self.assertEqual(len(gated), 1)
        self.assertEqual(gated[0]["human_gate_pending"], payload["gate"])

    def test_explain_reads_the_job_history_tolerantly(self) -> None:
        jobs_dir = self.runtime_root / "jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "unparseable.json").write_text("{not json")
        (jobs_dir / "abandoned.json").write_text(json.dumps({
            "job_id": "abandoned", "target_repo": None, "status": job.STATUS_FAILED,
            "reconciliation_evidence": {"code": "OperatorAbandoned"},
        }))
        self._seed_failed_apply("other-target", target_repo="/somewhere/else")
        out = self._explain()
        self.assertIn("next automatic action: /apply-implementation-review wi-1", out)
        self.assertIsNone(job.last_launched_apply_job_view(self.runtime_root, Path(self.target_repo), "wi-1"))

        self._seed_failed_apply("this-target")
        out = self._explain()
        self.assertIn("human gate", out)
        self.assertIn("job this-target, ended FAILED", out)
        self.assertEqual(
            job.last_launched_apply_job_view(self.runtime_root, Path(self.target_repo), "wi-1").job_id,
            "this-target",
        )


# ---------------------------------------------------------------------------
# `resume`
# ---------------------------------------------------------------------------


def _job_record(*, job_id: str, target_repo: str, status: str,
                 work_item_id: str = "wi-1") -> dict:
    return {
        "schema_version": job.SCHEMA_VERSION,
        "job_id": job_id,
        "controller_generation": FAKE_IDENTITY.generation,
        "controller_source_commit": FAKE_IDENTITY.source_commit,
        "controller_source_tree_digest": FAKE_IDENTITY.tree_digest,
        "target_repo": target_repo,
        "target_workflow_version": "2.3.1",
        "work_item_id": work_item_id,
        "observed_phase_before": "PLANNING",
        "pre_state": {
            "phase": "PLANNING", "governing_workflow_version": "2.1", "target_head": None,
            "state_revision": 1, "plan_revision": 1, "implementation_revision": None,
            "last_completed_checkpoint_id": None, "checkpoints": {}, "bundle_id": None,
            "bundle_manifest_readable": False, "bundle_manifest_generation_head": None,
            "bundle_generated_digest": None, "rejected_marker_present": False,
            "child_work_item_ids": [], "functional_review_consumed_blob": None,
            "functional_checklist_evidence": None,
        },
        "selected_action": {
            "kind": "slash_command", "command": "/milestone-plan wi-1", "automatic": True,
            "declined": False, "reason": "ordinary case", "evidence": [],
        },
        "status": status,
        "human_gate_pending": None,
        "handoff_pending": False,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "worker_outcome": {
            "classification": "SUCCESS", "returncode": 0, "session_id": "s1",
            "duration_seconds": 1.0, "stdout_path": "jobs/j1.stdout.txt",
            "stderr_path": "jobs/j1.stderr.txt",
        },
        "observed_phase_after": "AWAITING_LOCAL_PLAN_REVIEW",
        "transition_verified": True,
    }


class ResumeCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.repo = _build_managed_target(self.tmp_root, phase="AWAITING_LOCAL_PLAN_REVIEW")
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp_root / "workflow-manager")
        self.runtime_root = self.tmp_root / "runtime"

        # `resume` (like `step`/`run`) calls `require_pinned_execution()`
        # on entry, which reads the process-global cached pin
        # (`identity.current()`), never the identity object passed into
        # the command function directly -- see `test_identity.py`'s own
        # `GuardTest` and `test_handoff.py`'s `RunLoopExitCodeTest`.
        import controller.identity as identity_module
        self._identity_module = identity_module
        self._orig_pin = identity_module.pin
        self._orig_current = identity_module.current
        identity_module.pin = lambda: FAKE_IDENTITY
        identity_module.current = lambda: FAKE_IDENTITY
        self.addCleanup(self._restore_identity)

    def _restore_identity(self) -> None:
        self._identity_module.pin = self._orig_pin
        self._identity_module.current = self._orig_current

    def _args(self, **kwargs) -> _Args:
        return _Args(str(self.repo), workflow_manager=str(self.stub_manager), **kwargs)

    def test_no_records_exits_ok(self) -> None:
        exit_code = cli.cmd_resume(self._args(), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)

    def test_terminal_finished_record_is_reported_and_exits_ok(self) -> None:
        record = _job_record(job_id="j1", target_repo=str(self.repo.resolve()),
                              status=job.STATUS_FINISHED)
        runtime.write_json(self.runtime_root, "jobs/j1.json", record)

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_resume(self._args(), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)
        self.assertIn("j1: FINISHED", buf.getvalue())

    def test_historical_terminal_interrupted_record_alone_does_not_exit_40(self) -> None:
        """`I2`, MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 1: exit 40 means
        "the Controller itself was interrupted, or `resume` reconciled a
        record to `INTERRUPTED`" (`CONTROLLER_GEN1_PLAN.md`'s exit code
        table) -- an event of *this* invocation, not the mere presence of a
        historical terminal `INTERRUPTED` record from an earlier one.
        `status in TERMINAL_STATUSES` records are reported verbatim by
        `job.resume` and reconcile nothing, so a record already terminal
        before this call must not by itself produce exit 40. This test used
        to pin the opposite (wrong) behavior under the name
        `test_terminal_interrupted_record_exits_40`."""
        record = _job_record(job_id="j2", target_repo=str(self.repo.resolve()),
                              status=job.STATUS_INTERRUPTED)
        record.pop("worker_outcome")
        record.pop("observed_phase_after")
        record.pop("transition_verified")
        record["reconciled_at"] = "2024-01-01T00:05:00Z"
        runtime.write_json(self.runtime_root, "jobs/j2.json", record)

        exit_code = cli.cmd_resume(self._args(), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_OK)

    def test_a_record_this_invocation_reconciles_to_interrupted_exits_40(self) -> None:
        """M2's other discriminating case: a non-terminal record (`PLANNED`)
        that *this* `resume` invocation reconciles to `INTERRUPTED` (row 1,
        unconditional) must still produce exit 40 -- the positive case the
        fix above must not have broken while narrowing the negative one."""
        record = _job_record(job_id="j5", target_repo=str(self.repo.resolve()),
                              status=job.STATUS_PLANNED)
        for key in ("worker_outcome", "observed_phase_after", "transition_verified"):
            record.pop(key, None)
        runtime.write_json(self.runtime_root, "jobs/j5.json", record)

        exit_code = cli.cmd_resume(self._args(), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(exit_code, cli.EXIT_INTERRUPTED)

        on_disk = json.loads((self.runtime_root / "jobs" / "j5.json").read_text())
        self.assertEqual(on_disk["status"], job.STATUS_INTERRUPTED)
        # The marker that discriminates "reconciled this call" is an
        # in-memory-only signal for `cmd_resume` -- never part of the
        # persisted job-record schema.
        self.assertNotIn("reconciled_this_call", on_disk)

    def test_json_output_is_a_list_of_records(self) -> None:
        record = _job_record(job_id="j3", target_repo=str(self.repo.resolve()),
                              status=job.STATUS_FINISHED)
        runtime.write_json(self.runtime_root, "jobs/j3.json", record)

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_resume(self._args(json_out=True), self.runtime_root, FAKE_IDENTITY)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["job_id"], "j3")

    def test_json_output_strips_reconciled_this_call_marker(self) -> None:
        """`O1`/`M2` (MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 2):
        `job.resume` adds `reconciled_this_call: True` in memory to every
        record this invocation actually reconciled, but `job.py`'s own
        docstring declares it "never part of the persisted job-record
        schema" -- `resume --json` must report the same durable shape
        `jobs/<job_id>.json` holds, not a superset of it. Uses a `PLANNED`
        record (row 1, unconditionally reconciled) so the marker is
        actually present on the in-memory record `cmd_resume` receives,
        the one case `test_json_output_is_a_list_of_records`'s terminal
        `FINISHED` fixture is structurally incapable of exercising."""
        record = _job_record(job_id="j6", target_repo=str(self.repo.resolve()),
                              status=job.STATUS_PLANNED)
        for key in ("worker_outcome", "observed_phase_after", "transition_verified"):
            record.pop(key, None)
        runtime.write_json(self.runtime_root, "jobs/j6.json", record)

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = cli.cmd_resume(self._args(json_out=True), self.runtime_root, FAKE_IDENTITY)
        # The record this call reconciled to INTERRUPTED must still drive
        # exit 40 -- stripping the marker from the *printed* payload must
        # not also blind the exit-code computation, which reads the
        # original (unstripped) records.
        self.assertEqual(exit_code, cli.EXIT_INTERRUPTED)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["job_id"], "j6")
        self.assertEqual(payload[0]["status"], job.STATUS_INTERRUPTED)
        self.assertNotIn("reconciled_this_call", payload[0])

    def test_never_launches_a_worker(self) -> None:
        """`controller.job.resume` contains no call to
        `controller.worker.launch` at all -- proven here by monkeypatching
        `launch` to fail the test if it is ever called, with a non-terminal
        `PLANNED` record on disk to reconcile."""
        from controller import worker as worker_module

        def _fail_if_called(*a, **k):
            raise AssertionError("resume must never launch a worker")

        original_launch = worker_module.launch
        worker_module.launch = _fail_if_called
        self.addCleanup(lambda: setattr(worker_module, "launch", original_launch))

        record = _job_record(job_id="j4", target_repo=str(self.repo.resolve()),
                              status=job.STATUS_PLANNED)
        for key in ("worker_outcome", "observed_phase_after", "transition_verified"):
            record.pop(key, None)
        runtime.write_json(self.runtime_root, "jobs/j4.json", record)

        cli.cmd_resume(self._args(), self.runtime_root, FAKE_IDENTITY)  # must not raise/launch


# ---------------------------------------------------------------------------
# workflow-controller-automatic-lifecycle-orchestration CP5 -- exit 45, the
# lock errors, `resume --abandon`, and explain's pending and lock reports.
# ---------------------------------------------------------------------------


class ExitCodeTableTest(unittest.TestCase):
    def test_exit_worker_active_is_45_and_distinct(self) -> None:
        codes = {name: value for name, value in vars(cli).items() if name.startswith("EXIT_")}
        self.assertEqual(cli.EXIT_WORKER_ACTIVE, 45)
        self.assertEqual(len(set(codes.values())), len(codes), codes)

    def test_the_worker_active_clause_precedes_the_blanket_clause_in_main(self) -> None:
        tree = ast.parse(Path(cli.__file__).read_text())
        main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
        handlers = [
            handler.type.id for node in ast.walk(main) if isinstance(node, ast.Try)
            for handler in node.handlers if isinstance(handler.type, ast.Name)
        ]
        self.assertIn("LifecycleWorkerActiveError", handlers)
        self.assertIn("ControllerError", handlers)
        self.assertLess(handlers.index("LifecycleWorkerActiveError"), handlers.index("ControllerError"))

    def test_main_maps_each_lifecycle_error(self) -> None:
        for error, expected in (
            (LifecycleWorkerActiveError("held"), cli.EXIT_WORKER_ACTIVE),
            (LifecycleWorkerUnverifiableError("unverifiable"), cli.EXIT_WORKER_ACTIVE),
            (LifecycleLockError("flock failed with ENOLCK"), cli.EXIT_FAIL_CLOSED),
            (PendingJobReconciliationError("pending"), cli.EXIT_FAIL_CLOSED),
            (JobAbandonRefusedError("refused"), cli.EXIT_FAIL_CLOSED),
            (GitDirectoryUnresolvableError("no git dir"), cli.EXIT_FAIL_CLOSED),
        ):
            with self.subTest(error=type(error).__name__):
                def raise_it(args, argv, error=error):
                    raise error

                stderr = io.StringIO()
                with unittest.mock.patch.object(cli, "_dispatch", raise_it), contextlib.redirect_stderr(stderr):
                    self.assertEqual(cli.main(["step", "/target"]), expected)
                self.assertIn(f"error: {error.message}", stderr.getvalue())

    def test_lifecycle_lock_error_is_a_sibling_never_a_subclass(self) -> None:
        self.assertFalse(issubclass(LifecycleLockError, LifecycleWorkerActiveError))
        self.assertTrue(issubclass(LifecycleWorkerUnverifiableError, LifecycleWorkerActiveError))

    def test_acknowledge_without_abandon_is_a_usage_error(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            cli.main(["resume", "--acknowledge-unverifiable-worker", "/target"])
        self.assertEqual(ctx.exception.code, cli.EXIT_USAGE)
        self.assertIn("--acknowledge-unverifiable-worker is accepted only with --abandon", stderr.getvalue())

    def test_resume_parses_abandon_and_the_acknowledgement(self) -> None:
        args = cli.build_parser().parse_args(
            ["resume", "--abandon", "20260101T000000Z-abcd", "--acknowledge-unverifiable-worker", "/target"])
        self.assertEqual(args.abandon, "20260101T000000Z-abcd")
        self.assertTrue(args.acknowledge_unverifiable_worker)
        self.assertEqual(args.repo, "/target")
        plain = cli.build_parser().parse_args(["resume", "/target"])
        self.assertIsNone(plain.abandon)
        self.assertFalse(plain.acknowledge_unverifiable_worker)


class LockErrorCliTest(_StepFixture, unittest.TestCase):
    """Lock errors other than contention (round 4, O4): ``step``, ``resume``
    and ``--abandon`` each exit 20 with ``LifecycleLockError`` naming the
    errno, never exit 45's "worker active" text, and no ``OSError``
    escapes ``cli.main``."""

    def _run_main(self, command) -> tuple[int, str]:
        stderr = io.StringIO()
        with unittest.mock.patch.object(cli, "_dispatch", lambda args, argv: command()), \
                contextlib.redirect_stderr(stderr):
            code = cli.main(["step", str(self.repo)])
        return code, stderr.getvalue()

    def test_every_non_contention_failure_is_exit_20(self) -> None:
        runtime.write_json(self.runtime_root, "jobs/j-launched.json", {
            **_job_record(job_id="j-launched", target_repo=str(self.repo.resolve()), status=job.STATUS_LAUNCHED),
        })
        abandon_args = _Args(str(self.repo), workflow_manager=str(self.stub_manager))
        abandon_args.abandon = "j-launched"
        abandon_args.acknowledge_unverifiable_worker = False
        commands = {
            "step": lambda: cli.cmd_step(self._args(), self.runtime_root, self.ident),
            "resume": lambda: cli.cmd_resume(self._args(), self.runtime_root, self.ident),
            "--abandon": lambda: cli.cmd_resume(abandon_args, self.runtime_root, self.ident),
        }
        failures = {
            "flock ENOLCK": ("flock", OSError(errno.ENOLCK, "No locks available"), "ENOLCK"),
            "flock EBADF": ("flock", OSError(errno.EBADF, "Bad file descriptor"), "EBADF"),
            "open EACCES": ("open", PermissionError(errno.EACCES, "Permission denied"), "EACCES"),
        }
        real_open = os.open

        def open_refusing_directories(error):
            def fake_open(path, flags, *args, **kwargs):
                if flags & os.O_DIRECTORY:
                    raise error
                return real_open(path, flags, *args, **kwargs)
            return fake_open

        for command_name, command in commands.items():
            for failure_name, (target, error, errno_name) in failures.items():
                with self.subTest(command=command_name, failure=failure_name):
                    patcher = (
                        unittest.mock.patch.object(lock.fcntl, "flock", side_effect=error) if target == "flock"
                        else unittest.mock.patch.object(lock.os, "open", open_refusing_directories(error))
                    )
                    with patcher:
                        code, stderr = self._run_main(command)
                    self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
                    self.assertIn(f"error: the lifecycle lock on {lock.resolve_git_dir(self.repo)} could not be "
                                  f"taken: {target} failed with {errno_name}", stderr)
                    self.assertNotIn("holds the lifecycle lock", stderr)
        on_disk = runtime.read_json(self.runtime_root / "jobs" / "j-launched.json")
        self.assertEqual(on_disk["status"], job.STATUS_LAUNCHED)


class UnreadableJobFileCliTest(_StepFixture, unittest.TestCase):
    """A regular job file the Controller cannot read: ``explain`` names its
    manual disposition, and ``resume --abandon`` exits 20 through
    ``cli.main`` -- never an uncaught ``PermissionError``."""

    def test_abandon_exits_20_and_explain_names_the_manual_disposition(self) -> None:
        runtime.write_json(self.runtime_root, "jobs/j-locked.json", {
            **_job_record(job_id="j-locked", target_repo=str(self.repo.resolve()), status=job.STATUS_LAUNCHED),
        })
        path = self.runtime_root / "jobs" / "j-locked.json"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        try:
            path.read_bytes()
        except PermissionError:
            pass
        else:
            self.skipTest("running as root: chmod 000 does not make a file unreadable")
        clearing = f"make {path} readable again, or remove it by hand"

        abandon_args = _Args(str(self.repo), workflow_manager=str(self.stub_manager))
        abandon_args.abandon = "j-locked"
        abandon_args.acknowledge_unverifiable_worker = False
        stderr = io.StringIO()
        with unittest.mock.patch.object(
                cli, "_dispatch", lambda args, argv: cli.cmd_resume(abandon_args, self.runtime_root, self.ident)), \
                contextlib.redirect_stderr(stderr):
            code = cli.main(["resume", str(self.repo)])
        self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
        self.assertIn("`resume --abandon 'j-locked'` refused", stderr.getvalue())
        self.assertIn(clearing, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertFalse((self.runtime_root / "jobs" / "abandoned").exists())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            explain_args = _Args(str(self.repo), workflow_manager=str(self.stub_manager), json_out=True)
            self.assertEqual(cli.cmd_explain(explain_args, self.runtime_root, self.ident), cli.EXIT_OK)
        self.assertEqual(json.loads(out.getvalue())["pending_jobs"],
                         [{"job_id": "j-locked", "status": None, "clearing_command": clearing}])


class ResumeWorkerHoldExitTest(unittest.TestCase):
    """``cmd_resume`` exits 45 when ``resume`` left a record alone because
    its recorded worker is ``active`` or ``unverifiable``; ``--abandon``
    reports the terminal record it wrote and exits 0."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.repo = _build_managed_target(self.tmp_root, phase="PLANNING")
        fixtures.commit_all(self.repo, "seed workflow state")
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp_root / "workflow-manager")
        self.runtime_root = self.tmp_root / "runtime"
        import controller.identity as identity_module
        for name in ("pin", "current"):
            patcher = unittest.mock.patch.object(identity_module, name, lambda: FAKE_IDENTITY)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _seed_launched(self, job_id: str, worker_process: dict) -> None:
        record = _job_record(job_id=job_id, target_repo=str(self.repo.resolve()), status=job.STATUS_LAUNCHED)
        for key in ("worker_outcome", "observed_phase_after", "transition_verified"):
            record.pop(key)
        record["pre_state"]["target_head"] = fixtures.current_head(self.repo)
        record["expected_transition"] = {"from": "PLANNING", "to_any_of": ["AWAITING_LOCAL_PLAN_REVIEW"]}
        record["lifecycle_lock"] = {"path": str(lock.resolve_git_dir(self.repo))}
        record["worker_process"] = worker_process
        runtime.write_json(self.runtime_root, f"jobs/{job_id}.json", record)

    def _resume(self, **kwargs) -> tuple[int, str]:
        args = _Args(str(self.repo), workflow_manager=str(self.stub_manager))
        args.abandon = kwargs.get("abandon")
        args.acknowledge_unverifiable_worker = kwargs.get("acknowledge", False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.cmd_resume(args, self.runtime_root, FAKE_IDENTITY)
        return code, out.getvalue()

    def test_an_active_worker_exits_45_naming_its_group(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        self._seed_launched("j-active", process_fixtures.worker_process_dict(sleeper.pid))
        code, out = self._resume()
        self.assertEqual(code, cli.EXIT_WORKER_ACTIVE)
        self.assertIn("j-active: LAUNCHED (resume_marked=worker_active:", out)
        self.assertIn(f"process group {sleeper.pid}", out)

    def test_an_unverifiable_worker_exits_45_naming_no_group_and_abandon_clears_it(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        context = {**worker.read_process_context(), "boot_id": "boot-elsewhere", "hostname": "elsewhere",
                   "machine_id": "m-elsewhere"}
        self._seed_launched("j-far", process_fixtures.worker_process_dict(sleeper.pid, context=context))
        code, out = self._resume()
        self.assertEqual(code, cli.EXIT_WORKER_ACTIVE)
        self.assertIn("resume_marked=worker_unverifiable", out)
        self.assertIn("--acknowledge-unverifiable-worker", out)
        self.assertNotIn(str(sleeper.pid), out)

        stderr = io.StringIO()
        with unittest.mock.patch.object(
                cli, "_dispatch", lambda args, argv: self._resume(abandon="j-far")[0]), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["step", str(self.repo)]), cli.EXIT_WORKER_ACTIVE)
        self.assertIn("--acknowledge-unverifiable-worker", stderr.getvalue())

        code, out = self._resume(abandon="j-far", acknowledge=True)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("j-far: FAILED (OperatorAbandoned; was LAUNCHED", out)
        self.assertIn("recorded worker liveness: unverifiable (acknowledged)", out)
        code, out = self._resume()
        self.assertEqual(code, cli.EXIT_OK)


class ExplainPendingJobsTest(_ApplyingReviewFeedbackFixture, unittest.TestCase):
    """Round 4, O3: ``explain`` while a job is pending. A ``"2.2"`` item at
    ``APPLYING_REVIEW_FEEDBACK`` with an admissible ``REVISE``, and a
    ``LAUNCHED`` apply record J whose ``bundle_manifest_bundle_id`` equals
    the manifest's."""

    def _seed_launched_apply(self, job_id: str) -> None:
        runtime.write_json(self.runtime_root, f"jobs/{job_id}.json", {
            "schema_version": job.SCHEMA_VERSION, "job_id": job_id, "controller_generation": self.ident.generation,
            "target_repo": self.target_repo, "target_workflow_version": "2.5.1", "work_item_id": "wi-1",
            "observed_phase_before": "APPLYING_REVIEW_FEEDBACK",
            "pre_state": {"phase": "APPLYING_REVIEW_FEEDBACK", "governing_workflow_version": "2.2",
                          "target_head": fixtures.current_head(self.repo), "bundle_manifest_bundle_id": "b" * 64},
            "selected_action": {"kind": "slash_command", "command": "/apply-implementation-review wi-1",
                                "task_addendum": None, "automatic": True, "declined": False,
                                "reason": "seeded", "evidence": []},
            "expected_transition": {"from": "APPLYING_REVIEW_FEEDBACK",
                                    "to_any_of": ["AWAITING_LOCAL_IMPLEMENTATION_REVIEW"]},
            "status": job.STATUS_LAUNCHED, "human_gate_pending": None, "handoff_pending": False,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        })

    def test_explain_reports_the_pending_job_ahead_of_its_decision_until_resume_clears_it(self) -> None:
        self._seed_launched_apply("j-apply")
        clearing = f"workflow-controller resume {self.target_repo}"
        out = self._explain()
        self.assertIn("pending job: j-apply (LAUNCHED)", out)
        self.assertIn(clearing, out)
        self.assertIn("`step`/`run` refuse until each is cleared", out)
        self.assertLess(out.index("pending job: j-apply"), out.index("phase: APPLYING_REVIEW_FEEDBACK"))
        self.assertNotIn("human gate", out)  # a LAUNCHED record is never J
        self.assertIn("next automatic action: /apply-implementation-review wi-1", out)
        payload = json.loads(self._explain(json_out=True))
        self.assertEqual(payload["pending_jobs"],
                         [{"job_id": "j-apply", "status": "LAUNCHED", "clearing_command": clearing}])

        stderr = io.StringIO()
        with unittest.mock.patch.object(cli, "_dispatch", lambda args, argv: self._step_with(self._args())), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["step", str(self.repo)]), cli.EXIT_FAIL_CLOSED)
        self.assertIn("job j-apply (LAUNCHED", stderr.getvalue())
        self.assertIn(clearing, stderr.getvalue())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_resume(self._args(), self.runtime_root, self.ident), cli.EXIT_INTERRUPTED)
        self.assertIn("j-apply: INTERRUPTED", out.getvalue())

        payload = json.loads(self._explain(json_out=True))
        self.assertEqual(payload["pending_jobs"], [])
        self.assertIn("job j-apply, ended INTERRUPTED", payload["gate"]["what_is_required"])
        self.assertNotIn("pending job", self._explain())
        env = {"FAKE_CLAUDE_REQUIRE_FILE": str(self.tmp_root / "never-created")}
        with unittest.mock.patch.dict("os.environ", env):
            self.assertEqual(self._step_with(self._args()), cli.EXIT_GATE)

    def test_explain_reports_pending_jobs_for_the_no_work_item_bootstrap_too(self) -> None:
        """Round 1's O4 of the manual external plan review: the pending
        report precedes both of ``explain``'s branches."""
        fixtures.write_workflow_state(self.repo, {"schema_version": 1, "active_work_item_id": None, "work_items": {}})
        (self.runtime_root / "jobs").mkdir(parents=True, exist_ok=True)
        (self.runtime_root / "jobs" / "garbage.json").write_text("{not json")
        out = self._explain()
        self.assertIn("pending job: garbage (unreadable)", out)
        self.assertIn(f"workflow-controller resume --abandon garbage {self.target_repo}", out)
        self.assertIn("phase: NO_PHASE", out)
        payload = json.loads(self._explain(json_out=True))
        self.assertEqual([entry["job_id"] for entry in payload["pending_jobs"]], ["garbage"])


class LifecycleLockReportTest(unittest.TestCase):
    """``explain``/``inspect`` report "lifecycle lock: held/free/unknown"
    through the non-acquiring probe."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.repo = _build_managed_target(self.tmp_root, phase="PLANNING")
        self.stub_manager = fixtures.write_stub_workflow_manager(self.tmp_root / "workflow-manager")
        self.runtime_root = self.tmp_root / "runtime"
        self.free = "free" if lock.read_pid_namespace() == lock.INIT_PID_NAMESPACE else "unknown"

    def _run(self, command, json_out: bool = False) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(command(_Args(str(self.repo), workflow_manager=str(self.stub_manager),
                                           json_out=json_out), self.runtime_root, FAKE_IDENTITY), cli.EXIT_OK)
        return out.getvalue()

    def test_explain_and_inspect_report_the_probe(self) -> None:
        for command in (cli.cmd_explain, cli.cmd_inspect):
            with self.subTest(command=command.__name__):
                self.assertIn(f"lifecycle lock: {self.free}", self._run(command))
                self.assertEqual(json.loads(self._run(command, json_out=True))["lifecycle_lock"], self.free)
                held = lock.acquire_lifecycle_lock(self.repo)
                try:
                    self.assertIn("lifecycle lock: held", self._run(command))
                    self.assertEqual(json.loads(self._run(command, json_out=True))["lifecycle_lock"], "held")
                finally:
                    held.release()



# ---------------------------------------------------------------------------
# Release-runtime-observability CP5: run records. `step`/`run` create
# `runs/<run_id>.json` at entry; `main()` closes it once, with the code it
# returns, or marks it `interrupted` on Ctrl-C.
# ---------------------------------------------------------------------------


class _RunRecordFixture(_StepFixture):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(setattr, cli, "_open_run", None)
        self.release = self.tmp_root / "release"
        self.addCleanup(self._end_workers)

    def _end_workers(self) -> None:
        self.release.touch()
        for path in (self.runtime_root / "jobs").glob("*.json"):
            try:
                process_fixtures.kill_group((json.loads(path.read_text()).get("worker_process") or {}).get("pgid"))
            except (OSError, ValueError, AttributeError):
                continue

    def _main(self, command: str, args: _Args | None = None, *, argv: list[str] | None = None) -> int:
        args = args or self._args()
        command_fn = {"step": cli.cmd_step, "run": cli.cmd_run}[command]
        with unittest.mock.patch.object(cli, "_dispatch",
                                        lambda _args, _argv: command_fn(args, self.runtime_root, self.ident)), \
                contextlib.redirect_stderr(io.StringIO()) as self.stderr:
            return cli.main(argv or [command, args.repo])

    def _real_args(self, **kwargs) -> _Args:
        return _Args(str(self.repo), workflow_manager=str(self.stub_manager), claude_binary=str(FAKE_CLAUDE),
                     timeout=10, **kwargs)

    def _runs(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.runtime_root / "runs").glob("*.json"))]

    def _run_events(self, run_id: str) -> list[dict]:
        path = self.runtime_root / "runs" / run_id / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def _jobs(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.runtime_root / "jobs").glob("*.json"))]

    def _quiet_follower(self):
        """``--follow``'s renderer writes to a duplicate of the real fd 2;
        point it at ``/dev/null`` instead, so the test output stays clean."""
        return unittest.mock.patch.object(cli.os, "dup", lambda fd: os.open(os.devnull, os.O_WRONLY))


class RunRecordExitPathTest(_RunRecordFixture, unittest.TestCase):
    """Every exit path closes the run ``ended`` with the code ``main()``
    returned -- including a raised ``ControllerError`` (20) and 45."""

    def _fake_execute_step(self, value):
        def fake_execute_step(*a, **k):
            if isinstance(value, BaseException):
                raise value
            return value
        return fake_execute_step

    def test_every_exit_code_is_recorded(self) -> None:
        no_action = Decision(observed_phase="MILESTONE_COMPLETE", evidence=(), action=None, automatic=False,
                             gate=None, declined=False, reason="nothing to do")
        cases = [
            ("step", {"status": job.STATUS_FINISHED}, cli.EXIT_OK),
            ("step", {"status": job.STATUS_GATE_BLOCKED}, cli.EXIT_GATE),
            ("step", {"status": job.STATUS_DECLINED}, cli.EXIT_DECLINED),
            ("step", {"status": job.STATUS_FAILED}, cli.EXIT_WORKER_FAILED),
            ("step", {"status": job.STATUS_INCOMPLETE}, cli.EXIT_INCOMPLETE),
            ("step", {"status": job.STATUS_HANDOFF_PENDING}, cli.EXIT_HANDOFF_PENDING),
            ("step", no_action, cli.EXIT_OK),
            ("step", PendingJobReconciliationError("a pending job"), cli.EXIT_FAIL_CLOSED),
            ("step", LifecycleWorkerActiveError("the worktree is held"), cli.EXIT_WORKER_ACTIVE),
            ("run", {"status": job.STATUS_FINISHED}, cli.EXIT_MAX_STEPS),
            ("run", {"status": job.STATUS_FAILED}, cli.EXIT_WORKER_FAILED),
            ("run", no_action, cli.EXIT_OK),
            ("run", PendingJobReconciliationError("a pending job"), cli.EXIT_FAIL_CLOSED),
            ("run", LifecycleWorkerActiveError("the worktree is held"), cli.EXIT_WORKER_ACTIVE),
        ]
        for command, outcome, expected in cases:
            with self.subTest(command=command, outcome=outcome):
                shutil.rmtree(self.runtime_root / "runs", ignore_errors=True)
                job.execute_step = self._fake_execute_step(outcome)
                code = self._main(command, _Args(str(self.repo), workflow_manager=str(self.stub_manager),
                                                 max_steps=2))
                self.assertEqual(code, expected)
                [run] = self._runs()
                self.assertEqual((run["state"], run["exit_code"], run["command"]), ("ended", code, command))
                self.assertIsNotNone(run["ended_at"])
                self.assertEqual(run["max_steps"], 2 if command == "run" else None)
                events = self._run_events(run["run_id"])
                self.assertEqual(events[0]["event"], "run_started")
                self.assertEqual((events[-1]["event"], events[-1]["exit_code"]), ("run_ended", code))
                self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
                steps = [e["n"] for e in events if e["event"] == "step_started"]
                self.assertEqual(steps, [1, 2] if code == cli.EXIT_MAX_STEPS else [1])
                if outcome is no_action:
                    [no_action_event] = [e for e in events if e["event"] == "no_action"]
                    self.assertEqual((no_action_event["observed_phase"], no_action_event["reason"]),
                                     ("MILESTONE_COMPLETE", "nothing to do"))
                self.assertIsNone(cli._open_run)
                self.assertNotIn(run["run_id"], job._OPEN_RUNS)

    def test_a_detected_handoff_is_logged_and_the_run_ends_50(self) -> None:
        (self.origin / "controller" / "GENERATION.json").write_text(
            json.dumps({"schema_version": 1, "generation": 2}) + "\n")
        fixtures.commit_all(self.origin, "bump generation")
        self.assertEqual(self._main("run"), cli.EXIT_HANDOFF_PENDING)
        [run] = self._runs()
        self.assertEqual((run["state"], run["exit_code"]), ("ended", 50))
        self.assertEqual([e["event"] for e in self._run_events(run["run_id"])],
                         ["run_started", "step_started", "handoff_detected", "run_ended"])

    def test_the_record_carries_the_controller_process_and_runtime(self) -> None:
        job.execute_step = self._fake_execute_step({"status": job.STATUS_GATE_BLOCKED})
        self._main("step")
        [run] = self._runs()
        self.assertEqual(run["controller_process"]["pid"], os.getpid())
        self.assertEqual(run["controller_runtime"], identity.runtime_record(self.ident))
        self.assertEqual(run["schema_version"], 1)
        self.assertEqual(sorted(run), sorted([
            "schema_version", "run_id", "command", "target_repo", "max_steps", "controller_process",
            "controller_runtime", "state", "exit_code", "job_ids", "current_job_id", "started_at",
            "updated_at", "ended_at",
        ]))
        self.assertEqual(self.stderr.getvalue(), "")


class RunRecordJobTrackingTest(_RunRecordFixture, unittest.TestCase):
    """A real job: the run is ``running`` with ``current_job_id`` while the
    worker is paused, the job record carries ``run_id``, and the run log
    mirrors the job's start and end."""

    def test_running_during_a_paused_job_then_ended(self) -> None:
        outcome: dict = {}

        def run_main() -> None:
            outcome["code"] = self._main("step", self._real_args())

        with unittest.mock.patch.dict("os.environ", {"FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release)}):
            thread = threading.Thread(target=run_main, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 30)
            self.assertTrue(process_fixtures.wait_until(
                lambda: any("worker_process" in r for r in self._jobs())), "the worker never started")
            [run] = self._runs()
            [record] = self._jobs()
            self.assertEqual(run["state"], "running")
            self.assertIsNone(run["exit_code"])
            self.assertEqual((run["job_ids"], run["current_job_id"]), ([record["job_id"]], record["job_id"]))
            self.assertEqual(record["run_id"], run["run_id"])
            self.release.touch()
            thread.join(30)
        self.assertEqual(outcome["code"], cli.EXIT_WORKER_FAILED)
        [run] = self._runs()
        [record] = self._jobs()
        self.assertEqual((run["state"], run["exit_code"], run["current_job_id"], run["job_ids"]),
                         ("ended", cli.EXIT_WORKER_FAILED, None, [record["job_id"]]))
        events = self._run_events(run["run_id"])
        self.assertEqual([e["event"] for e in events],
                         ["run_started", "step_started", "job_started", "job_ended", "run_ended"])
        self.assertEqual(events[2]["job_id"], record["job_id"])
        self.assertEqual((events[3]["job_id"], events[3]["status"]), (record["job_id"], job.STATUS_FAILED))

    def test_command_is_the_subcommand_and_target_repo_is_canonical(self) -> None:
        (self.repo / "sub").mkdir()
        link = self.tmp_root / "repo-link"
        link.symlink_to(self.repo)
        spellings = {"root": str(self.repo), "subdirectory": str(self.repo / "sub"), "symlink": str(link)}
        for command in ("step", "run"):
            for follow in (False, True):
                for name, repo in spellings.items():
                    with self.subTest(command=command, follow=follow, repo=name):
                        shutil.rmtree(self.runtime_root, ignore_errors=True)
                        args = self._real_args(max_steps=1)
                        args.repo = repo
                        if follow:
                            args.follow = True  # the record must not see it
                        argv = [command, repo, *(["--follow"] if follow else [])]
                        with self._quiet_follower():
                            code = self._main(command, args, argv=[command, repo])
                        self.assertEqual(code, cli.EXIT_WORKER_FAILED)
                        [run] = self._runs()
                        [record] = self._jobs()
                        self.assertEqual(run["command"], command)
                        self.assertEqual(run["target_repo"], record["target_repo"])
                        self.assertEqual(run["target_repo"], str(self.repo.resolve()))
                        self.assertNotIn("follow", run)
                        self.assertNotIn("argv", run)
                        self.assertNotIn(" ".join(argv), json.dumps(run))


_CHILD_RUN = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pathlib import Path; from controller import cli, identity; "
    "ident = identity.ControllerIdentity(generation=1, source_root=Path(sys.argv[2]), "
    "origin_source_root=Path(sys.argv[2]), source_kind=identity.SOURCE_KIND_COMMIT, "
    "source_commit=sys.argv[3], tree_digest='d'*64, generation_source='head', "
    "pinned_at='2024-01-01T00:00:00Z', version='1.1.1'); "
    "identity.pin = lambda: ident; identity.current = lambda: ident; "
    "sys.exit(cli.main(sys.argv[4:]))"
)


class RunRecordCtrlCTest(_RunRecordFixture, unittest.TestCase):
    """``run`` interrupted with ``SIGINT`` while its fake worker is paused:
    the run reads ``interrupted``, ``exit_code`` ``null``, and
    ``current_job_id`` names the orphan's job, whose worker still runs."""

    def test_sigint_marks_the_run_interrupted_and_keeps_the_orphan(self) -> None:
        import signal
        import subprocess
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_RUN, str(fixtures.REPO_ROOT), str(self.origin),
             self.ident.source_commit, "--runtime-dir", str(self.runtime_root),
             "--workflow-manager", str(self.stub_manager), "--claude-binary", str(FAKE_CLAUDE),
             "run", str(self.repo)],
            env={**os.environ, "FAKE_CLAUDE_HANG_UNTIL_FILE": str(self.release)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait(10)))
        self.assertTrue(process_fixtures.wait_until(
            lambda: any("worker_process" in r for r in self._jobs()), timeout=30), "the worker never started")
        child.send_signal(signal.SIGINT)
        _out, err = child.communicate(timeout=30)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("KeyboardInterrupt", err)
        [run] = self._runs()
        [record] = self._jobs()
        self.assertEqual((run["state"], run["exit_code"], run["current_job_id"]),
                         ("interrupted", None, record["job_id"]))
        self.assertEqual(run["command"], "run")
        self.assertEqual(self._run_events(run["run_id"])[-1]["event"], "run_interrupted")
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        self.assertTrue(process_fixtures.group_has_running_member(record["worker_process"]["pgid"]),
                        "the Controller must not end the worker")


class RunRecordBestEffortTest(_RunRecordFixture, unittest.TestCase):
    """A failing event append or run-record write changes nothing that
    counts: the exit code, the job's status and ``event_seq`` are the
    unforced run's, and exactly one warning is printed."""

    def _step_once(self) -> tuple[int, dict]:
        shutil.rmtree(self.runtime_root, ignore_errors=True)
        with unittest.mock.patch.object(runtime, "_best_effort_warned", False):
            code = self._main("step", self._real_args())
        [record] = self._jobs()
        return code, record

    def _significant(self, code: int, record: dict) -> tuple:
        return (code, record["status"], record["worker_outcome"], record["transition_verified"],
                record["observed_phase_after"], record["event_seq"], sorted(record))

    def test_forced_failures_leave_the_lifecycle_unchanged(self) -> None:
        unforced = self._significant(*self._step_once())
        self.assertNotIn("warning", self.stderr.getvalue())
        real_append, real_write_json = runtime.append_jsonl, runtime.write_json

        def non_json_append(runtime_root, rel_path, obj):
            return real_append(runtime_root, rel_path, {**obj, "detail": object()})

        def failing_run_write(runtime_root, rel_path, obj):
            if str(rel_path).startswith("runs/"):
                raise OSError(errno.ENOSPC, "injected run-record failure")
            return real_write_json(runtime_root, rel_path, obj)

        forcings = {
            "append OSError": unittest.mock.patch.object(
                runtime, "append_jsonl", side_effect=OSError(errno.EIO, "injected")),
            "append RuntimeContainmentError": unittest.mock.patch.object(
                runtime, "append_jsonl", side_effect=RuntimeContainmentError("injected", evidence={})),
            "append TypeError": unittest.mock.patch.object(runtime, "append_jsonl", non_json_append),
            "run-record OSError": unittest.mock.patch.object(runtime, "write_json", failing_run_write),
        }
        for name, forcing in forcings.items():
            with self.subTest(forcing=name):
                with forcing:
                    forced = self._significant(*self._step_once())
                self.assertEqual(forced, unforced)
                warnings = [l for l in self.stderr.getvalue().splitlines()
                            if l.startswith("workflow-controller: warning: could not write ")]
                self.assertEqual(len(warnings), 1, self.stderr.getvalue())


# ---------------------------------------------------------------------------
# CP6 (release-runtime-observability): `follow`, `--follow`, `status`'s
# `active:` section and the follow hints.
# ---------------------------------------------------------------------------


def _tree_listing(root: Path) -> list[tuple]:
    """Every path under ``root`` with its type, size and mtime."""
    if not root.exists():
        return []
    listing = []
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        listing.append((str(path.relative_to(root)), st.st_mode, st.st_size, st.st_mtime_ns))
    return listing


class _FollowCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.repo = fixtures.build_target_git_repo(self.tmp_root / "repo").resolve()
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir()
        for name, value in (("POLL_SECONDS", 0.02), ("FINAL_EVENT_GRACE_SECONDS", 0.2)):
            patcher = unittest.mock.patch.object(observe, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, run_id: str, **fields) -> dict:
        record = {"schema_version": 1, "run_id": run_id, "command": "step", "target_repo": str(self.repo),
                  "max_steps": None, "controller_process": worker.capture_worker_process(os.getpid()).to_dict(),
                  "controller_runtime": {}, "state": "ended", "exit_code": 10, "job_ids": [],
                  "current_job_id": None, "started_at": "2026-01-01T00:00:00Z",
                  "updated_at": "2026-01-01T00:00:00Z", "ended_at": None, **fields}
        runtime.write_json(self.runtime_root, f"runs/{run_id}.json", record)
        return record

    def _run_event(self, run_id: str, seq: int, event: str, **details) -> None:
        runtime.append_jsonl(self.runtime_root, f"runs/{run_id}/events.jsonl",
                             {"v": 1, "seq": seq, "at": "2026-01-01T00:00:00Z", "run_id": run_id,
                              "event": event, **details})

    def _job(self, job_id: str, **fields) -> dict:
        record = {"job_id": job_id, "target_repo": str(self.repo), "status": job.STATUS_LAUNCHED,
                  "created_at": "2026-01-01T00:00:00Z", **fields}
        runtime.write_json(self.runtime_root, f"jobs/{job_id}.json", record)
        return record

    def _dead_process(self) -> dict:
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        captured = worker.capture_worker_process(proc.pid).to_dict()
        proc.wait()
        return captured

    def _follow(self, *argv: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        previous = os.getcwd()
        if cwd is not None:
            os.chdir(cwd)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--runtime-dir", str(self.runtime_root), *argv])
        finally:
            os.chdir(previous)
        return code, out.getvalue(), err.getvalue()


class FollowCommandTest(_FollowCliCase):
    def test_a_running_run_is_chosen(self) -> None:
        self._run("r-old", started_at="2026-01-01T00:00:00Z")
        self._run("r-live", state="running", exit_code=None, started_at="2026-01-02T00:00:00Z")
        self._run_event("r-live", 1, "run_started")
        done = threading.Timer(0.3, lambda: (self._run("r-live", state="ended", exit_code=0),
                                             self._run_event("r-live", 2, "run_ended", exit_code=0)))
        done.start()
        self.addCleanup(done.cancel)
        code, out, _err = self._follow("follow", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("run r-live started", out)
        self.assertTrue(out.splitlines()[-1].endswith("run ended: exit 0"))

    def test_a_running_run_whose_controller_is_gone_is_not_chosen(self) -> None:
        self._run("r-dead", state="running", exit_code=None, controller_process=self._dead_process())
        code, out, _err = self._follow("follow", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn(f"nothing active for {self.repo}; last run r-dead was not closed", out)

    def test_with_no_run_an_orphan_job_with_an_active_worker_is_chosen(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        self._run("r-int", state="interrupted", exit_code=None)
        self._job("j-orphan", worker_process=process_fixtures.worker_process_dict(sleeper.pid))
        runtime.append_jsonl(self.runtime_root, "jobs/j-orphan/events.jsonl",
                             {"v": 1, "seq": 1, "at": "2026-01-01T00:00:00Z", "job_id": "j-orphan",
                              "event": "worker_spawned", "pid": sleeper.pid, "pgid": sleeper.pid})
        threading.Timer(0.3, process_fixtures.kill_group, (sleeper.pid,)).start()
        code, out, _err = self._follow("follow", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn(f"job j-orphan worker spawned: pid {sleeper.pid}", out)
        self.assertTrue(out.splitlines()[-1].endswith("worker exited; job j-orphan awaits resume"))

    def test_nothing_active(self) -> None:
        code, out, _err = self._follow("follow", str(self.repo))
        self.assertEqual((code, out), (cli.EXIT_OK, f"nothing active for {self.repo}; no runs recorded\n"))
        self._run("r-1", exit_code=30)
        code, out, _err = self._follow("follow", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(out, f"nothing active for {self.repo}; last run r-1 ended with exit 30; replay: "
                              f"workflow-controller --runtime-dir {self.runtime_root} follow --run r-1 "
                              f"--from-start {self.repo}\n")

    def test_explicit_run_and_job(self) -> None:
        self._run("r-1", exit_code=10, job_ids=["j-1"])
        self._run_event("r-1", 1, "run_started")
        self._run_event("r-1", 2, "run_ended", exit_code=10)
        self._job("j-1", status=job.STATUS_GATE_BLOCKED, event_seq=1)
        runtime.append_jsonl(self.runtime_root, "jobs/j-1/events.jsonl",
                             {"v": 1, "seq": 1, "at": "2026-01-01T00:00:00Z", "job_id": "j-1",
                              "event": "gate_blocked", "reason": "needs a human"})
        code, out, _err = self._follow("follow", "--run", "r-1", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(out.splitlines()[-1].endswith("run ended: exit 10"))
        code, out, _err = self._follow("--json", "follow", "--job", "j-1", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK)
        [event] = [json.loads(line) for line in out.splitlines()]
        self.assertEqual((event["kind"], event["event"], event["reason"]),
                         ("job_event", "gate_blocked", "needs a human"))

    def test_the_repo_defaults_to_the_working_directory(self) -> None:
        (self.repo / "sub").mkdir()
        self._run("r-1", exit_code=0)
        self._run_event("r-1", 1, "run_ended", exit_code=0)
        for cwd in (self.repo, self.repo / "sub"):
            with self.subTest(cwd=cwd):
                code, out, _err = self._follow("follow", cwd=cwd)
                self.assertEqual(code, cli.EXIT_OK)
                self.assertIn(f"nothing active for {self.repo}; last run r-1", out)
                code, out, _err = self._follow("follow", "--run", "r-1", cwd=cwd)
                self.assertEqual(code, cli.EXIT_OK)
                self.assertIn("run ended: exit 0", out)

    def test_a_target_mismatch_or_an_unknown_id_exits_20(self) -> None:
        other = fixtures.build_target_git_repo(self.tmp_root / "other").resolve()
        self._run("r-1")
        self._job("j-1", status=job.STATUS_FINISHED)
        for argv in (["follow", "--run", "r-1", str(other)], ["follow", "--job", "j-1", str(other)],
                     ["follow", "--run", "r-none", str(self.repo)], ["follow", "--job", "j-none", str(self.repo)]):
            with self.subTest(argv=argv):
                code, out, err = self._follow(*argv)
                self.assertEqual(code, cli.EXIT_FAIL_CLOSED)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("error: "), err)

    def test_follow_writes_nothing(self) -> None:
        self._run("r-1", exit_code=10, job_ids=["j-1"])
        self._run_event("r-1", 1, "run_started")
        self._run_event("r-1", 2, "run_ended", exit_code=10)
        self._job("j-1", status=job.STATUS_FINISHED)
        runtime.write_json(self.runtime_root, "identity.json", {"schema_version": 1, "generation": 3})
        before = _tree_listing(self.runtime_root)
        for argv in (["follow", str(self.repo)], ["follow", "--run", "r-1", "--from-start", str(self.repo)],
                     ["follow", "--job", "j-1", str(self.repo)]):
            with self.subTest(argv=argv):
                code, _out, _err = self._follow(*argv)
                self.assertEqual(code, cli.EXIT_OK)
                self.assertEqual(_tree_listing(self.runtime_root), before)

    def test_a_stdout_whose_reader_is_gone_exits_0_without_a_traceback(self) -> None:
        # A real interpreter exit: the final flush of an unwritable stdout is
        # what turns an unhandled `BrokenPipeError` into exit 120.
        import subprocess
        self._run("r-1", exit_code=10)
        for n in range(1, 200):
            self._run_event("r-1", n, "step_started", n=n)
        self._run_event("r-1", 200, "run_ended", exit_code=10)
        code_root = Path(cli.__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(code_root)}
        for argv in (["follow", "--run", "r-1", "--from-start"], ["follow"]):
            with self.subTest(argv=argv):
                read_end, write_end = os.pipe()
                os.close(read_end)
                try:
                    proc = subprocess.run(
                        [sys.executable, "-P", "-m", "controller", "--runtime-dir", str(self.runtime_root),
                         *argv, str(self.repo)],
                        stdin=subprocess.DEVNULL, stdout=write_end, stderr=subprocess.PIPE, text=True,
                        env=env, timeout=60, check=False,
                    )
                finally:
                    os.close(write_end)
                self.assertEqual(proc.returncode, cli.EXIT_OK, proc.stderr)
                self.assertEqual(proc.stderr, "")

    def test_a_nonexistent_runtime_dir_is_not_created(self) -> None:
        missing = self.tmp_root / "no-runtime"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--runtime-dir", str(missing), "follow", str(self.repo)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no runs recorded", out.getvalue())
        self.assertFalse(missing.exists())

    def test_follow_never_pins_materialises_or_locks(self) -> None:
        with unittest.mock.patch.object(identity, "pin", side_effect=AssertionError("pinned")), \
                unittest.mock.patch.object(identity, "materialise", side_effect=AssertionError("materialised")), \
                unittest.mock.patch.object(runtime, "ensure_runtime_root", side_effect=AssertionError("created")), \
                unittest.mock.patch.object(lock, "acquire_lifecycle_lock", side_effect=AssertionError("locked")):
            code, out, _err = self._follow("follow", str(self.repo))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("nothing active", out)


class FollowSourceCheckoutTest(unittest.TestCase):
    """From a source checkout, a bare ``follow <repo>`` without
    ``--runtime-dir`` finds the run ``step`` recorded there without one
    (ladder row 3, through the read-only ``resolve_runtime``), and writes
    nothing."""

    def test_follow_finds_steps_row_3_runtime_root(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_root = Path(tmp.name)
        checkout = fixtures.build_checkout(tmp_root / "checkout", generation=1)
        repo = _build_managed_target(tmp_root, phase="PLANNING").resolve()
        stub_manager = fixtures.write_stub_workflow_manager(tmp_root / "workflow-manager")
        env = {k: v for k, v in os.environ.items()
               if k not in ("WORKFLOW_CONTROLLER_HOME", "XDG_STATE_HOME", "PYTHONPATH")}
        env["PYTHONPATH"] = str(checkout)
        stepped = fixtures.run_controller_module(
            ["--workflow-manager", str(stub_manager), "--claude-binary", str(FAKE_CLAUDE), "--timeout", "30",
             "step", str(repo)], cwd=checkout, env=env)
        self.assertEqual(stepped.returncode, cli.EXIT_WORKER_FAILED, stepped.stderr)
        runtime_root = (checkout / ".controller").resolve()
        [run_path] = (runtime_root / "runs").glob("*.json")
        run_id = run_path.stem
        before = _tree_listing(runtime_root)

        followed = fixtures.run_controller_module(["follow", str(repo)], cwd=checkout, env=env)
        self.assertEqual(followed.returncode, cli.EXIT_OK, followed.stderr)
        self.assertEqual(followed.stdout,
                         f"nothing active for {repo}; last run {run_id} ended with exit 30; replay: "
                         f"workflow-controller --runtime-dir {runtime_root} follow --run {run_id} "
                         f"--from-start {repo}\n")
        replayed = fixtures.run_controller_module(["follow", "--run", run_id, "--from-start", str(repo)],
                                                  cwd=checkout, env=env)
        self.assertEqual(replayed.returncode, cli.EXIT_OK, replayed.stderr)
        self.assertIn("worker session", replayed.stdout)
        self.assertTrue(replayed.stdout.splitlines()[-1].endswith("run ended: exit 30"))
        self.assertEqual(_tree_listing(runtime_root), before)


class StatusActiveSectionTest(_FollowCliCase):
    def _status(self) -> str:
        pre_existing = cli._capture_pre_existing_state(self.runtime_root)
        pre_existing["ladder_row"] = 1
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_status(_Args(str(self.repo)), self.runtime_root, FAKE_IDENTITY, pre_existing=pre_existing)
        return out.getvalue()

    def test_idle(self) -> None:
        self._run("r-1")
        self._job("j-1", status=job.STATUS_FINISHED)
        self.assertIn("\nactive: none\n", self._status())

    def test_the_running_run_and_the_non_terminal_job_with_the_follow_command(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        self._run("r-live", state="running", exit_code=None)
        self._job("j-live", worker_process=process_fixtures.worker_process_dict(sleeper.pid))
        self._job("j-drain", worker_process=process_fixtures.worker_process_dict(sleeper.pid),
                  worker_group_drain={"direct_child_exited_at": "2026-01-01T00:00:00Z", "remaining_pids": [7, 8]})
        text = self._status()
        follow = f"    follow: workflow-controller --runtime-dir {self.runtime_root} follow {self.repo}"
        self.assertIn("active:\n", text)
        self.assertIn(f"  run r-live (step, target {self.repo}): controller pid {os.getpid()} active\n{follow}\n",
                      text)
        self.assertIn(f"  job j-live (LAUNCHED, target {self.repo}): worker pid {sleeper.pid} active\n{follow}\n",
                      text)
        self.assertIn(f"  job j-drain (LAUNCHED, target {self.repo}): worker exited; waiting on process group: "
                      f"2 process(es) at drain start (7 8)\n{follow}\n", text)
        self.assertNotIn("r-1", text)


class FollowHintTest(_StepFixture, unittest.TestCase):
    """The exit-45 messages carry the ``follow`` command."""

    def test_the_lock_refusal_names_the_follow_command(self) -> None:
        held = lock.acquire_lifecycle_lock(self.repo)
        self.addCleanup(held.release)
        with self.assertRaises(LifecycleWorkerActiveError) as ctx:
            self._step()
        self.assertIn(f"`workflow-controller --runtime-dir {self.runtime_root} follow {self.repo.resolve()}`",
                      ctx.exception.message)

    def test_resume_exit_45_prints_the_follow_command(self) -> None:
        sleeper = process_fixtures.spawn_sleeper(self)
        record = _job_record(job_id="j-active", target_repo=str(self.repo.resolve()), status=job.STATUS_LAUNCHED)
        for key in ("worker_outcome", "observed_phase_after", "transition_verified"):
            record.pop(key)
        record["lifecycle_lock"] = {"path": str(lock.resolve_git_dir(self.repo))}
        record["worker_process"] = process_fixtures.worker_process_dict(sleeper.pid)
        runtime.write_json(self.runtime_root, "jobs/j-active.json", record)
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = cli.cmd_resume(self._args(), self.runtime_root, FAKE_IDENTITY)
        self.assertEqual(code, cli.EXIT_WORKER_ACTIVE)
        self.assertIn(f"follow it: workflow-controller --runtime-dir {self.runtime_root} follow "
                      f"{self.repo.resolve()}", stderr.getvalue())
        abandon = self._args()
        abandon.abandon, abandon.acknowledge_unverifiable_worker = "j-active", False
        with self.assertRaises(LifecycleWorkerActiveError) as ctx:
            cli.cmd_resume(abandon, self.runtime_root, FAKE_IDENTITY)
        self.assertIn(f"follow {self.repo.resolve()}`", ctx.exception.message)


class FollowAttributeReadTest(unittest.TestCase):
    """``--follow`` is read in exactly one place, ``cli._start_follower``."""

    def test_the_follow_attribute_is_read_only_in_start_follower(self) -> None:
        readers = []
        for path in sorted((fixtures.REPO_ROOT / "controller").glob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node

            def enclosing(node):
                while node in parents:
                    node = parents[node]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return node.name
                return None

            for node in ast.walk(tree):
                reads = (
                    (isinstance(node, ast.Attribute) and node.attr == "follow")
                    or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("getattr", "hasattr") and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant) and node.args[1].value == "follow")
                )
                if reads:
                    readers.append((path.name, enclosing(node)))
        self.assertEqual(readers, [("cli.py", "_start_follower")])


class StepFollowTest(_RunRecordFixture, unittest.TestCase):
    """``step --follow`` renders the run on stderr through the private
    descriptor, and its exit code and records are those of a plain step."""

    def _child(self, *extra: str) -> tuple[int, str, str]:
        import subprocess
        shutil.rmtree(self.runtime_root, ignore_errors=True)
        child = subprocess.run(
            [sys.executable, "-c", _CHILD_RUN, str(fixtures.REPO_ROOT), str(self.origin),
             self.ident.source_commit, "--runtime-dir", str(self.runtime_root),
             "--workflow-manager", str(self.stub_manager), "--claude-binary", str(FAKE_CLAUDE),
             "--timeout", "30", "step", str(self.repo), *extra],
            capture_output=True, text=True, timeout=60,
        )
        return child.returncode, child.stdout, child.stderr

    def test_step_follow_renders_and_changes_nothing_else(self) -> None:
        plain_code, plain_out, plain_err = self._child()
        [plain_record] = self._jobs()
        code, out, err = self._child("--follow")
        [record] = self._jobs()
        self.assertEqual((code, out), (plain_code, plain_out))
        self.assertEqual(plain_err, "")
        self.assertIn("worker session", err)
        self.assertIn("tool Bash: true", err)
        self.assertTrue(err.splitlines()[-1].endswith(f"run ended: exit {code}"), err)
        self.assertEqual({k: v for k, v in record.items() if k in ("status", "worker_outcome", "event_seq")},
                         {k: v for k, v in plain_record.items() if k in ("status", "worker_outcome", "event_seq")})
        [run] = self._runs()
        self.assertNotIn("follow", run)


if __name__ == "__main__":
    unittest.main()
