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
import errno
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, evidence, job, lock, managed_repo, runtime, worker  # noqa: E402
from controller.decision import Action, Decision  # noqa: E402
from controller.errors import (  # noqa: E402
    GitDirectoryUnresolvableError,
    JobAbandonRefusedError,
    LifecycleLockError,
    LifecycleWorkerActiveError,
    LifecycleWorkerUnverifiableError,
    PendingJobReconciliationError,
    UnmanagedRepositoryError,
)
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT  # noqa: E402
from tests import fixtures, process_fixtures  # noqa: E402

FAKE_IDENTITY = ControllerIdentity(
    generation=7,
    source_root=Path("/fake/source"),
    origin_source_root=Path("/fake/origin"),
    source_kind=SOURCE_KIND_COMMIT,
    source_commit="a" * 40,
    tree_digest="b" * 64,
    generation_source="head",
    pinned_at="2024-01-01T00:00:00Z",
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
    def test_all_six_commands_are_declared(self) -> None:
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
            generation_source="head", pinned_at="2024-01-01T00:00:00Z",
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


if __name__ == "__main__":
    unittest.main()
