"""End-to-end lifecycle regression suite (`workflow-controller-automatic-
lifecycle-orchestration` CP7).

Every scenario runs a real, temporary ``"2.2"`` target repository through
the real ``cli.main`` (``run``, ``step``, ``explain``, ``resume``) and so the
real ``execute_step``, its real verification and the real next decision.
The worker is ``tests/fake_claude.py`` in its scripted mode
(``FAKE_CLAUDE_SCRIPT``): a real subprocess whose effect is the observable
effect of the Workflow command it was launched for, in that command's own
commit order -- a checkpoint commit with its trailers, a state-only
transition commit, a generation-record commit ``T`` followed by the author
files and a ``MANIFEST.md`` whose ``generation_head`` is ``T``, and the
review-stage writers' state writes left uncommitted. The script is keyed on
the exact task string, so a worker launched with any other task (a missing
or unexpected addendum included) does nothing and fails.

:class:`Lifecycle` models the work item's ``WORKFLOW_STATE.json`` entry and
advances it the way each Workflow writer does, returning each command's
scripted effect. A pre-state that is not itself under test is seeded by
performing the same actions in-process (``fixtures.perform_script_actions``),
never by launching a worker.

Only the identity pin is patched (``identity.pin``/``identity.current``, to a
pinned identity whose origin checkout declares the same generation), and the
Workflow Manager is the offline stub. A step meant to launch nothing runs the
fake fail-if-invoked (``FAKE_CLAUDE_REQUIRE_FILE`` naming a path that never
exists), and ``FAKE_CLAUDE_INVOCATIONS_FILE`` counts every worker process that
started.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, evidence, identity, job, lock, runtime, worker  # noqa: E402
from controller.errors import LifecycleWorkerActiveError  # noqa: E402
from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT  # noqa: E402
from tests import fixtures, process_fixtures  # noqa: E402

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"

WI = "wi-1"
CHECKPOINT_IDS = ("CP1", "CP2")
STATE_REL = "docs/ai-workflow/WORKFLOW_STATE.json"
BUNDLE_REL = f".ai-review/{WI}/current"
FEEDBACK_REL = f".ai-review/{WI}/feedback/REVIEW_FEEDBACK.md"
MARKER_REL = f".ai-review/{WI}/REJECTED"
QUARANTINE_REL = f".ai-review/{WI}/current.rejected-{'e' * 32}"

IMPLEMENTING = "IMPLEMENTING"
SELF_REVIEWING = "SELF_REVIEWING_IMPLEMENTATION"
AWAITING_LOCAL = "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"
AWAITING_MANUAL = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
AWAITING_EXTERNAL = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
APPLYING = "APPLYING_REVIEW_FEEDBACK"

LOCAL = evidence.LOCAL_IMPLEMENTATION_ROLE
MANUAL = evidence.MANUAL_IMPLEMENTATION_ROLE

MILESTONE_IMPLEMENT = f"/milestone-implement {WI}"
REVIEW_IMPLEMENTATION = f"/review-implementation {WI}"
RECORD_MANUAL = f"/record-manual-implementation-review {WI}"
APPLY = f"/apply-implementation-review {WI}"

GENERATION_RECORD = "Workflow-Bundle-Generation-Record"
WORK_ITEM = "Workflow-Work-Item"
SUPERSEDES = "Workflow-Supersedes"
COMPLETED_AT = "2026-01-01T00:00:00Z"


def apply_task(committed_phase: str | None) -> str:
    """The exact task of an ``/apply-implementation-review`` worker: the
    bare command when ``HEAD`` already records ``APPLYING_REVIEW_FEEDBACK``
    (``committed_phase`` ``None``), else the command plus the pending-write
    addendum naming the phase ``HEAD`` records."""
    if committed_phase is None:
        return APPLY
    return f"{APPLY}\n\n{evidence.pending_review_stage_write_addendum(WI, committed_phase)}"


#: The apply task after a review-stage ``REVISE``: ``HEAD`` is the round's
#: generation-record commit, which records ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW``.
APPLY_PENDING = apply_task(AWAITING_LOCAL)


def bundle_id(round_no: int) -> str:
    return f"b{round_no}" * 32


def content_id(round_no: int) -> str:
    return f"c{round_no}" * 32


OTHER_CONTENT = "d" * 64


def _commit_count(root: Path) -> int:
    return int(fixtures.run(["git", "rev-list", "--count", "HEAD"], cwd=root).stdout.strip())


class Lifecycle:
    """One ``"2.2"`` work item in its own target repository: its
    ``WORKFLOW_STATE.json`` entry, advanced the way each Workflow writer
    advances it, and the scripted effect of each Workflow command. Every
    command method updates the modelled entry and returns one invocation's
    action list; :meth:`add` files it under the exact task it answers.

    ``reviewed_implementation_head`` is modelled as a revision token until
    :meth:`sync` reads concrete values back: ``{HEAD}`` in the state write
    of a generation-record commit ``T`` (the commit
    ``record_bundle_generation`` records), and ``{HEAD^}`` in every write
    made while ``T`` is ``HEAD`` -- the manifest and the review-stage
    writes, which never commit."""

    def __init__(self, case_dir: Path, root: Path, base_commit: str, *, phase: str,
                 checkpoints: dict, **overrides) -> None:
        self.case_dir = case_dir
        self.root = root
        self.base_commit = base_commit
        self.runtime = case_dir / "runtime"
        self.script_path = case_dir / "worker-script.json"
        self.processes_file = case_dir / "worker-processes"
        self.worktree_root = fixtures.target_worktree_root(root)
        self.script: dict[str, list[list[dict]]] = {}
        self.manifest_present = False
        self._fixes = 0
        self.entry: dict = {
            "work_item_type": "product",
            "work_item_kind": "product",
            "work_item_id": WI,
            "parent_work_item_id": None,
            "governing_workflow_version": "2.2",
            "phase": phase,
            "plan_revision": 1,
            "implementation_revision": None,
            "functional_review_round": None,
            "base_commit": base_commit,
            "reviewed_implementation_head": None,
            "current_checkpoint_id": None,
            "last_completed_checkpoint_id": None,
            "checkpoints": checkpoints,
            "current_bundle_id": None,
            "registry_path": fixtures.registry_rel_path(WI),
            "plan_approval": {"status": "CURRENT"},
            "technical_approval": None,
            "technical_review_block_pins": [],
            "implementation_review_stages": None,
            "functional_acceptance_status": None,
            "state_revision": 1,
            "last_transition": COMPLETED_AT,
            **overrides,
        }

    # --- script plumbing ----------------------------------------------------

    def add(self, task: str, actions: list[dict]) -> list[dict]:
        """File ``actions`` as the next invocation of ``task``."""
        self.script.setdefault(task, []).append(actions)
        return actions

    def perform(self, actions: list[dict]) -> None:
        """Seed a pre-state: perform ``actions`` in-process, with no worker."""
        fixtures.perform_script_actions(self.root, actions)

    def state_text(self) -> str:
        return json.dumps(
            {"schema_version": 1, "active_work_item_id": WI, "work_items": {WI: self.entry}}, indent=2,
        ) + "\n"

    def write_state(self) -> dict:
        self.entry["state_revision"] += 1
        return fixtures.script_write(STATE_REL, self.state_text())

    def sync(self) -> None:
        """Reload the entry from the working tree, so every field -- the
        reviewed head included -- is concrete again."""
        self.entry = fixtures.state_entry(self.root, WI)

    # --- /milestone-implement -----------------------------------------------

    def implement(self, checkpoint_id: str, *, last: bool = False, commit: str = "all") -> list[dict]:
        """Step 1 for one checkpoint: its change, then step 1f's
        ``complete_checkpoint`` (``SELF_REVIEWING_IMPLEMENTATION`` on the
        last one), committed together with the step's trailers
        (``commit="all"``), or with only the product change committed
        (``"product"``)."""
        product = f"{checkpoint_id}.txt"
        self.entry["checkpoints"][checkpoint_id] = {"status": "COMPLETE", "start_commit": self.base_commit}
        self.entry["current_checkpoint_id"] = None
        self.entry["last_completed_checkpoint_id"] = checkpoint_id
        if last:
            self.entry["phase"] = SELF_REVIEWING
        actions = [fixtures.script_write(product, f"{checkpoint_id} implemented\n"), self.write_state()]
        if commit == "all":
            actions.append(fixtures.script_commit(
                fixtures.trailer_message(f"Implement {checkpoint_id}", ("Workflow-Checkpoint", checkpoint_id),
                                         (WORK_ITEM, WI)),
                product, STATE_REL,
            ))
        elif commit == "product":
            actions.append(fixtures.script_commit(f"Implement {checkpoint_id}", product))
        return actions

    def enter_self_review(self, *, commit: bool = True) -> list[dict]:
        """Step 2 on the ``NO_CHECKPOINT`` path: the state-only
        ``SELF_REVIEWING_IMPLEMENTATION`` commit, with a single
        ``Workflow-Work-Item`` trailer (or, ``commit=False``, the state
        write alone)."""
        self.entry["phase"] = SELF_REVIEWING
        actions = [self.write_state()]
        if commit:
            actions.append(fixtures.script_commit(
                fixtures.trailer_message("Enter SELF_REVIEWING_IMPLEMENTATION", (WORK_ITEM, WI)), STATE_REL,
            ))
        return actions

    # --- bundle generation (/milestone-implement step 4, apply step 7) -------

    def manifest_text(self, round_no: int, revision: int, *, reviewed_head: str,
                      generation_head: str = "{HEAD}") -> str:
        return fixtures.build_implementation_manifest_text(
            WI, revision, worktree_root=self.worktree_root, reviewed_implementation_head=reviewed_head,
            generation_head=generation_head, bundle_id=bundle_id(round_no),
            review_content_id=content_id(round_no),
        )

    @staticmethod
    def author_files(round_no: int, revision: int, directory: str = BUNDLE_REL) -> list[dict]:
        """The four author files, each stating what the generator requires."""
        return [
            fixtures.script_write(f"{directory}/IMPLEMENTATION_SUMMARY.md",
                                  f"# Implementation summary\n\nimplementation_revision: {revision}\n"),
            fixtures.script_write(f"{directory}/REVIEW_REQUEST.md",
                                  f"# Review request\n\nstage: implementation\n"
                                  f"review_content_id: {content_id(round_no)}\n"),
            fixtures.script_write(f"{directory}/TEST_RESULTS.md", "# Test results\n\nall green\n"),
            fixtures.script_write(f"{directory}/CONTEXT_FILES.txt", "README.md\n"),
        ]

    def generate(self, *, round_no: int | None = None, outcome: str = "ordinary",
                 supersedes: str | None = None, manifest: str = "coherent") -> list[dict]:
        """``record_bundle_generation``'s state write, committed alone as
        ``T`` with the record trailers (``Workflow-Supersedes`` added for a
        ``same_content`` outcome, whose revision and reviewed head stay),
        then the author files and the generator's ``MANIFEST.md``.

        ``manifest`` is the generator's outcome: ``"coherent"``;
        ``"none"`` (it never ran, so any previous round's manifest stays);
        ``"before-record"`` (the manifest lands before ``T``, so its
        ``generation_head`` is behind ``HEAD``); ``"withdrawn"`` (a completed
        withdrawal: ``current/`` renamed into a quarantine with this round's
        author files and no manifest, the marker removed); ``"rejected"`` (a
        withdrawal that failed at its quarantine step: the marker stays,
        ``MANIFEST.md`` is removed and ``current/`` survives)."""
        self.entry["phase"] = AWAITING_LOCAL
        if outcome == "ordinary":
            self.entry["implementation_revision"] = (self.entry["implementation_revision"] or 0) + 1
            self.entry["reviewed_implementation_head"] = "{HEAD}"
        elif "{" in str(self.entry["reviewed_implementation_head"]):
            raise AssertionError("a same_content generation needs a concrete reviewed head: call sync() first")
        revision = self.entry["implementation_revision"]
        round_no = revision if round_no is None else round_no
        trailers = [(GENERATION_RECORD, f"{WI}/{revision}"), (WORK_ITEM, WI)]
        if outcome == "same_content":
            trailers.append((SUPERSEDES, supersedes))
        record = [self.write_state(), fixtures.script_commit(
            fixtures.trailer_message("Record implementation bundle generation", *trailers), STATE_REL,
        )]
        if outcome == "ordinary":
            self.entry["reviewed_implementation_head"] = "{HEAD^}"
        reviewed_after_record = self.entry["reviewed_implementation_head"]

        if manifest == "before-record":
            actions = [record[0], *self.author_files(round_no, revision),
                       fixtures.script_write(f"{BUNDLE_REL}/MANIFEST.md", self.manifest_text(
                           round_no, revision, reviewed_head="{HEAD}")),
                       record[1]]
            self.manifest_present = True
            return actions
        actions = list(record)
        if manifest == "coherent":
            actions += self.author_files(round_no, revision)
            actions.append(fixtures.script_write(f"{BUNDLE_REL}/MANIFEST.md", self.manifest_text(
                round_no, revision, reviewed_head=reviewed_after_record)))
            self.manifest_present = True
        elif manifest == "none":
            actions += self.author_files(round_no, revision)
        elif manifest == "withdrawn":
            actions += self.author_files(round_no, revision, directory=QUARANTINE_REL)
            if self.manifest_present:
                actions.append(fixtures.script_delete(BUNDLE_REL))
            self.manifest_present = False
        elif manifest == "rejected":
            actions += self.author_files(round_no, revision)
            actions.append(fixtures.script_write(
                MARKER_REL,
                "REJECTED: withdrawal FAILED at step 'quarantine current/'\n"
                "reason: assert_stage_completeness failed\nsurviving: current/\n",
            ))
            if self.manifest_present:
                actions.append(fixtures.script_delete(f"{BUNDLE_REL}/MANIFEST.md"))
            self.manifest_present = False
        else:
            raise AssertionError(f"unknown manifest outcome {manifest!r}")
        return actions

    # --- review-stage writers (uncommitted) ----------------------------------

    def feedback_text(self, status: str, role: str, round_no: int, **overrides) -> str:
        fields = dict(
            status=status, reviewer_role=role, reviewed_bundle_id=bundle_id(round_no),
            reviewed_base_commit=self.base_commit, work_item=WI, reviewed_content_id=content_id(round_no),
        )
        fields.update(overrides)
        return fixtures.build_review_feedback_text(**fields)

    def paste_manual_verdict(self, status: str, round_no: int, **overrides) -> None:
        """The human's act: a manual external verdict placed on file."""
        path = self.root / FEEDBACK_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.feedback_text(status, MANUAL, round_no, **overrides))

    def local_review(self, verdict: str, round_no: int, *, ledger_content: str | None = None) -> list[dict]:
        """``/review-implementation`` (A6): the verdict on file, then
        ``record_local_implementation_review`` -- a fresh ledger and
        ``AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`` on ``APPROVE``,
        ``APPLYING_REVIEW_FEEDBACK`` on ``REVISE``, nothing on ``BLOCK``."""
        actions = [fixtures.script_write(FEEDBACK_REL, self.feedback_text(verdict, LOCAL, round_no))]
        if verdict == "APPROVE":
            self.entry["implementation_review_stages"] = {
                "review_content_id": ledger_content or content_id(round_no),
                LOCAL: {"bundle_id": bundle_id(round_no), "verdict": "APPROVE", "round": 1,
                        "completed_at": COMPLETED_AT},
                MANUAL: None,
            }
            self.entry["phase"] = AWAITING_MANUAL
            actions.append(self.write_state())
        elif verdict == "REVISE":
            self.entry["phase"] = APPLYING
            actions.append(self.write_state())
        return actions

    def manual_review(self, verdict: str, round_no: int, *, ledger_content: str | None = None) -> list[dict]:
        """``/record-manual-implementation-review`` step 7:
        ``record_manual_implementation_review`` -- the manual stage and
        ``AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`` on ``APPROVE``,
        ``APPLYING_REVIEW_FEEDBACK`` on ``REVISE``."""
        if verdict == "APPROVE":
            stages = dict(self.entry["implementation_review_stages"])
            stages[MANUAL] = {"bundle_id": bundle_id(round_no), "verdict": "APPROVE", "round": 1,
                              "completed_at": COMPLETED_AT}
            if ledger_content is not None:
                stages["review_content_id"] = ledger_content
            self.entry["implementation_review_stages"] = stages
            self.entry["phase"] = AWAITING_EXTERNAL
        elif verdict == "REVISE":
            self.entry["phase"] = APPLYING
        return [self.write_state()]

    # --- /apply-implementation-review ----------------------------------------

    def commit_pending_state(self) -> dict:
        """The addendum's state-only commit of the pending review-stage write."""
        return fixtures.script_commit(
            fixtures.trailer_message("Commit the pending review-stage state write", (WORK_ITEM, WI)), STATE_REL,
        )

    def fix(self) -> list[dict]:
        self._fixes += 1
        path = f"fix-{self._fixes}.txt"
        return [fixtures.script_write(path, f"review fix {self._fixes}\n"),
                fixtures.script_commit(f"Apply review fix {self._fixes}", path)]

    def apply(self, *, commit_pending: bool = True, fix: bool = True, **generate_kwargs) -> list[dict]:
        """A remediation round: the pending state-only commit (as the
        addendum asks), a fix commit, then step 7's post-fix generation."""
        actions = [self.commit_pending_state()] if commit_pending else []
        if fix:
            actions += self.fix()
        return actions + self.generate(**generate_kwargs)


class Run(NamedTuple):
    code: int
    records: list[dict]
    stdout: str
    stderr: str


class _LifecycleTestCase(unittest.TestCase):
    """The pinned identity, the stub Workflow Manager and the ``cli.main``
    driver shared by every scenario."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._class_tmp = tempfile.TemporaryDirectory(prefix="controller-lifecycle-origin-")
        origin = fixtures.build_checkout(Path(cls._class_tmp.name) / "origin", generation=1)
        cls.ident = ControllerIdentity(
            generation=1, source_root=origin, origin_source_root=origin, source_kind=SOURCE_KIND_COMMIT,
            source_commit=fixtures.current_head(origin), tree_digest="d" * 64, generation_source="head",
            pinned_at=COMPLETED_AT, version=fixtures.CONTROLLER_VERSION,
        )
        cls.stub_manager = fixtures.write_stub_workflow_manager(Path(cls._class_tmp.name) / "workflow-manager")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._class_tmp.cleanup()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="controller-lifecycle-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name).resolve()
        self.never = self.tmp_root / "never-created"
        for name in ("pin", "current"):
            patcher = unittest.mock.patch.object(identity, name, return_value=self.ident)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- targets ---------------------------------------------------------------

    def seed(self, name: str, phase: str, *, done: tuple[str, ...] = (), **overrides) -> Lifecycle:
        """A committed target at ``phase`` whose checkpoints ``done`` are
        ``COMPLETE``, under ``<tmp>/<name>/``."""
        case_dir = self.tmp_root / name
        root = case_dir / "target"
        fixtures.build_target_git_repo(root)
        (root / "README.md").write_text("lifecycle fixture\n")
        (root / ".gitignore").write_text(".ai-review/\n")
        fixtures.write_installation_manifest(root)
        base = fixtures.commit_all(root, "initial")
        lifecycle = Lifecycle(
            case_dir, root.resolve(), base, phase=phase,
            checkpoints={cid: {"status": "COMPLETE", "start_commit": base} for cid in done}, **overrides,
        )
        fixtures.write_registry(root, WI, CHECKPOINT_IDS)
        (root / STATE_REL).write_text(lifecycle.state_text())
        fixtures.commit_all(root, "Seed the work item")
        return lifecycle

    def at_last_checkpoint(self, name: str) -> Lifecycle:
        return self.seed(name, IMPLEMENTING, done=("CP1",))

    def at_every_checkpoint_done(self, name: str) -> Lifecycle:
        """``IMPLEMENTING`` with the registry complete: the ``NO_CHECKPOINT``
        path after a plan re-approval."""
        return self.seed(name, IMPLEMENTING, done=CHECKPOINT_IDS)

    def at_self_reviewing(self, name: str) -> Lifecycle:
        return self.seed(name, SELF_REVIEWING, done=CHECKPOINT_IDS)

    def at_awaiting_local(self, name: str) -> Lifecycle:
        lc = self.at_self_reviewing(name)
        lc.perform(lc.generate())
        return lc

    def at_applying(self, name: str) -> Lifecycle:
        lc = self.at_awaiting_local(name)
        lc.perform(lc.local_review("REVISE", 1))
        return lc

    def at_awaiting_manual(self, name: str) -> Lifecycle:
        lc = self.at_awaiting_local(name)
        lc.perform(lc.local_review("APPROVE", 1))
        return lc

    def at_manual_verdict(self, name: str, status: str = "APPROVE") -> Lifecycle:
        lc = self.at_awaiting_manual(name)
        lc.paste_manual_verdict(status, 1)
        return lc

    # --- the command line ------------------------------------------------------

    def cli(self, lc: Lifecycle, command: str, *args: str, json_out: bool = False,
            fail_if_invoked: bool = False) -> Run:
        """``cli.main`` against ``lc``'s target, its script written first.
        ``records`` are the job records this call created, in order."""
        fixtures.write_worker_script(lc.script_path, lc.script)
        jobs_dir = lc.runtime / "jobs"
        before = {p.name for p in jobs_dir.glob("*.json")} if jobs_dir.is_dir() else set()
        env = {"FAKE_CLAUDE_SCRIPT": str(lc.script_path), "FAKE_CLAUDE_INVOCATIONS_FILE": str(lc.processes_file)}
        if fail_if_invoked:
            env["FAKE_CLAUDE_REQUIRE_FILE"] = str(self.never)
        argv = ["--runtime-dir", str(lc.runtime), "--workflow-manager", str(self.stub_manager),
                "--claude-binary", str(FAKE_CLAUDE), "--timeout", "60"]
        if json_out:
            argv.append("--json")
        argv += [command, *args, str(lc.root)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with unittest.mock.patch.dict(os.environ, env), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        created = sorted(
            (p for p in jobs_dir.glob("*.json") if p.name not in before), key=lambda p: p.stat().st_mtime_ns,
        ) if jobs_dir.is_dir() else []
        return Run(code, [json.loads(p.read_text()) for p in created], stdout.getvalue(), stderr.getvalue())

    def explain(self, lc: Lifecycle) -> dict:
        result = self.cli(lc, "explain", json_out=True)
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assertEqual(result.records, [], "explain writes no job record")
        return json.loads(result.stdout)

    @staticmethod
    def processes(lc: Lifecycle) -> int:
        """How many worker processes started (the invocation counter)."""
        return len(lc.processes_file.read_text().splitlines()) if lc.processes_file.exists() else 0

    @staticmethod
    def read_record(lc: Lifecycle, job_id: str) -> dict:
        return json.loads((lc.runtime / "jobs" / f"{job_id}.json").read_text())

    @staticmethod
    def rewrite_as(lc: Lifecycle, record: dict, status: str) -> dict:
        """The same job's record as a Controller that died before its
        verification (``COMPLETED``) or before the worker result
        (``LAUNCHED``) left it on disk.

        Worker-lifecycle-ownership CP5: the ``LAUNCHED`` shape is a
        Controller lost *before it ended the session* -- ``worker_state``
        ``RUNNING``, no ``ending_offset`` or other ``ENDING`` fact -- so
        ``resume`` does not re-attach (plan E) and takes the fail-closed
        ``_reconcile_launched`` path these scenarios pin. A record lost
        after ``ENDING``/``ENDED`` is re-attached and classified from the
        stream instead (``tests/test_resume.py``'s CP5 tests)."""
        dropped = {"observed_phase_after", "transition_verified", "reconciliation_evidence", "reconciled_at"}
        if status == job.STATUS_LAUNCHED:
            dropped |= {"worker", "worker_outcome", "ending_offset", "wakeup_overdue_declared_at",
                        "command_lifecycle_overdue_declared_at", "command_lifecycle_overdue_command_uuid",
                        "settled_wakeups"}
        rewritten = {k: v for k, v in record.items() if k not in dropped}
        rewritten["status"] = status
        if status == job.STATUS_LAUNCHED and isinstance(rewritten.get("worker_state"), dict):
            rewritten["worker_state"] = {**rewritten["worker_state"], "state": worker.RUNNING}
        runtime.write_json(lc.runtime, f"jobs/{record['job_id']}.json", rewritten)
        return rewritten

    # --- assertions --------------------------------------------------------------

    def assert_finished(self, record: dict, observed: str) -> None:
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertTrue(record["transition_verified"])
        self.assertEqual(record["observed_phase_after"], observed)

    def assert_failed(self, record: dict, reason: str, detail: str | None = None) -> dict:
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertFalse(record["transition_verified"])
        ev = record["reconciliation_evidence"]
        self.assertEqual(ev["reason"], reason, ev)
        if detail is not None:
            self.assertIn(detail, ev["postcondition_detail"])
        return ev

    def assert_gate(self, result: Run, lc: Lifecycle, *, processes_before: int,
                    contains: tuple[str, ...] = (), safe: str | None = None) -> dict:
        """``result`` stopped at a gate (exit 10, one ``GATE_BLOCKED``
        record) and no worker process started."""
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        gate_record = result.records[-1]
        self.assertEqual(gate_record["status"], job.STATUS_GATE_BLOCKED)
        self.assertFalse(gate_record["selected_action"]["automatic"])
        self.assertNotIn("worker", gate_record)
        self.assertEqual(self.processes(lc), processes_before, "a gate launched a worker")
        gate = gate_record["human_gate_pending"]
        for fragment in contains:
            self.assertIn(fragment, gate["what_is_required"])
        if safe is not None:
            self.assertEqual(gate["safe_resume_command"], safe)
        return gate

    def assert_regeneration_gate(self, gate: dict, lc: Lifecycle, stage: str) -> None:
        """CP4B's bundle gate, case 3: the ordered steps, ending in the
        generator at ``stage`` -- never the bare generator, never
        ``/recover-implementation-provenance``."""
        other = "post-fix" if stage == "implementation" else "implementation"
        generator = f"run scripts/prepare-ai-review.sh {lc.base_commit} {stage} {WI}"
        self.assertIn("perform in order:", gate["what_is_required"])
        self.assertIn(generator, gate["what_is_required"])
        self.assertIn(generator, gate["safe_resume_command"])
        self.assertNotIn(f"prepare-ai-review.sh {lc.base_commit} {other} {WI}", gate["safe_resume_command"])
        self.assertIn("implementation_revision:", gate["safe_resume_command"])
        self.assertNotIn("recover-implementation-provenance", gate["what_is_required"] + gate["safe_resume_command"])

    def assert_rejected_gate(self, gate: dict, lc: Lifecycle, stage: str) -> None:
        """CP4's ``"2.2"`` ``REJECTED`` branch: the marker-clearing clause,
        then the ordered recovery steps -- never the bare generator."""
        clause = f"resolve the failure the REJECTED marker at {MARKER_REL}"
        self.assertIn(clause, gate["what_is_required"])
        self.assertTrue(gate["safe_resume_command"].startswith(clause))
        self.assertIn(f"run scripts/prepare-ai-review.sh {lc.base_commit} {stage} {WI}", gate["safe_resume_command"])
        self.assertIn("implementation_revision:", gate["safe_resume_command"])

    def assert_uncommitted_state_gate(self, gate: dict, lc: Lifecycle, facts: tuple[str, ...]) -> None:
        """The durable-state gate: every fact, in order, then what a human
        does; never a regeneration step or a Workflow command to launch."""
        what = gate["what_is_required"]
        self.assertTrue(what.startswith(
            f"the working tree's {STATE_REL} records state that HEAD does not durably record: "
            + "; ".join(facts) + ". "
        ), what)
        self.assertIn("so none is launched", what)
        self.assertIn("A human reconciles the working tree with HEAD first", what)
        self.assertNotIn("prepare-ai-review.sh", what)
        self.assertEqual(gate["artifact_path"], str(lc.root / STATE_REL))
        self.assertEqual(gate["safe_resume_command"], f"workflow-controller explain --work-item {WI}")

    def assert_no_user_only_task(self, lc: Lifecycle) -> None:
        """No worker was ever handed a user-only command."""
        tasks = fixtures.scripted_worker_tasks(lc.script_path)
        for task in tasks:
            self.assertFalse(set(worker._task_tokens(task)) & worker.USER_ONLY_COMMANDS, task)


# ---------------------------------------------------------------------------
# The scripted worker itself.
# ---------------------------------------------------------------------------


class ScriptedWorkerTest(_LifecycleTestCase):
    """The harness every scenario rests on: a worker launched with a task
    its script does not name changes nothing and fails, and revision tokens
    resolve when the write happens."""

    def test_an_unscripted_task_changes_nothing_and_fails(self) -> None:
        lc = self.at_awaiting_local("unscripted")
        lc.add(apply_task(None), [fixtures.script_write("stray.txt", "never written\n")])
        head = fixtures.current_head(lc.root)
        state = (lc.root / STATE_REL).read_text()
        result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        self.assertEqual(record["selected_action"]["command"], REVIEW_IMPLEMENTATION)
        self.assertEqual(record["worker_outcome"], "FAILURE")
        self.assertEqual(record["worker"]["exit_code"], 92)
        self.assert_failed(record, "worker_outcome")
        self.assertEqual(fixtures.current_head(lc.root), head)
        self.assertEqual((lc.root / STATE_REL).read_text(), state)
        self.assertFalse((lc.root / "stray.txt").exists())
        self.assertEqual(fixtures.scripted_worker_tasks(lc.script_path), [REVIEW_IMPLEMENTATION])

    def test_revision_tokens_resolve_at_write_time(self) -> None:
        lc = self.seed("tokens", IMPLEMENTING)
        parent = fixtures.current_head(lc.root)
        lc.perform([fixtures.script_write("a.txt", "a\n"), fixtures.script_commit("A", "a.txt")])
        head = fixtures.current_head(lc.root)
        lc.perform([fixtures.script_write("tokens.txt", "{HEAD} {HEAD^} {HEAD~1} {HEAD~2}\n")])
        grandparent = fixtures.run(["git", "rev-parse", "HEAD~2"], cwd=lc.root).stdout.strip()
        self.assertEqual((lc.root / "tokens.txt").read_text(), f"{head} {parent} {parent} {grandparent}\n")


# ---------------------------------------------------------------------------
# Scenario 1: `run` from IMPLEMENTING to the manual gate.
# ---------------------------------------------------------------------------


def script_full_lifecycle(lc: Lifecycle) -> None:
    """The six workers of one run from ``IMPLEMENTING`` (two checkpoints)
    to the manual gate: two checkpoints, the final pass, a local ``REVISE``,
    the remediation round with the pending-write addendum, and a fresh local
    ``APPROVE``."""
    lc.add(MILESTONE_IMPLEMENT, lc.implement("CP1"))
    lc.add(MILESTONE_IMPLEMENT, lc.implement("CP2", last=True))
    lc.add(MILESTONE_IMPLEMENT, lc.generate())
    lc.add(REVIEW_IMPLEMENTATION, lc.local_review("REVISE", 1))
    lc.add(APPLY_PENDING, lc.apply())
    lc.add(REVIEW_IMPLEMENTATION, lc.local_review("APPROVE", 2))


#: ``(command, role, single_agent, observed phase after)`` of each launched
#: job of :func:`script_full_lifecycle`, in order.
FULL_LIFECYCLE_JOBS = (
    (MILESTONE_IMPLEMENT, "milestone-implement", False, IMPLEMENTING),
    (MILESTONE_IMPLEMENT, "milestone-implement", False, SELF_REVIEWING),
    (MILESTONE_IMPLEMENT, "milestone-implement-self-review", True, AWAITING_LOCAL),
    (REVIEW_IMPLEMENTATION, "review-implementation", True, APPLYING),
    (APPLY, "apply-implementation-review", False, AWAITING_LOCAL),
    (REVIEW_IMPLEMENTATION, "review-implementation", True, AWAITING_MANUAL),
)


class FullLifecycleRunTest(_LifecycleTestCase):
    """Scenario 1: one ``run`` carries a ``"2.2"`` item from
    ``IMPLEMENTING`` through both checkpoints, the final pass, a local
    ``REVISE`` and its remediation, and a fresh local ``APPROVE``, to the
    manual gate -- six workers, each verified, then exit 10."""

    def test_run_drives_the_lifecycle_to_the_manual_gate(self) -> None:
        lc = self.seed("run", IMPLEMENTING)
        script_full_lifecycle(lc)
        result = self.cli(lc, "run")

        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(len(result.records), 7)
        launched, gate_record = result.records[:6], result.records[6]
        for record, (command, role, single_agent, observed) in zip(launched, FULL_LIFECYCLE_JOBS):
            with self.subTest(job=record["job_id"], command=command, observed=observed):
                self.assertEqual(record["selected_action"]["command"], command)
                self.assert_finished(record, observed)
                self.assertEqual(record["worker_route"]["role"], role)
                self.assertEqual(record["worker_route"]["model"], "claude-opus-5-5")
                self.assertEqual(record["worker_route"]["effort"], "xhigh")
                self.assertEqual(record["worker_route"]["single_agent"], single_agent)
                self.assertEqual(record["worker_route"]["sources"], {"model": "default", "effort": "default"})

        # Exactly six workers, each handed the task its script is keyed on.
        self.assertEqual(self.processes(lc), 6)
        self.assertEqual(fixtures.scripted_worker_tasks(lc.script_path), [
            MILESTONE_IMPLEMENT, MILESTONE_IMPLEMENT, MILESTONE_IMPLEMENT, REVIEW_IMPLEMENTATION,
            APPLY_PENDING, REVIEW_IMPLEMENTATION,
        ])
        self.assert_no_user_only_task(lc)

        # The remediation round's task carried the pending-write addendum,
        # recorded as such; every other task was the bare command.
        apply_record = launched[4]
        addendum = apply_record["selected_action"]["task_addendum"]
        self.assertEqual(addendum, evidence.pending_review_stage_write_addendum(WI, AWAITING_LOCAL))
        self.assertIn("Controller note (pending review-stage state write)", addendum)
        self.assertIn(f"`HEAD` records `{AWAITING_LOCAL}`", addendum)
        self.assertIn(f"`{WORK_ITEM}: {WI}`", addendum)
        for record in launched[:4] + launched[5:]:
            self.assertIsNone(record["selected_action"]["task_addendum"])

        # The worker followed it: the state-only commit, a fix commit, then
        # T, whose parent records APPLYING_REVIEW_FEEDBACK. The local
        # APPROVE after it stays uncommitted, so HEAD is still T.
        root = lc.root
        self.assertEqual(evidence.bundle_generation_record_role(evidence.commit_trailers(root, "HEAD"), WI),
                         "ordinary")
        self.assertEqual(evidence.committed_work_item(root, WI, "HEAD^")["phase"], APPLYING)
        self.assertEqual(evidence.commit_trailers(root, "HEAD~2"), {WORK_ITEM: WI})
        self.assertEqual(evidence.committed_work_item(root, WI, "HEAD~2")["phase"], APPLYING)
        self.assertEqual(evidence.committed_work_item(root, WI, "HEAD~3")["phase"], AWAITING_LOCAL)
        self.assertEqual(fixtures.state_entry(root, WI)["implementation_revision"], 2)

        # Stopped at the manual gate.
        self.assertEqual(gate_record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(gate_record["observed_phase_before"], AWAITING_MANUAL)
        self.assertEqual(gate_record["human_gate_pending"]["safe_resume_command"], RECORD_MANUAL)
        self.assertIn(f"bundle_id {bundle_id(2)}", gate_record["human_gate_pending"]["what_is_required"])

        # A second run launches nothing.
        again = self.cli(lc, "run")
        self.assert_gate(again, lc, processes_before=6, safe=RECORD_MANUAL)
        self.assertEqual(len(again.records), 1)


# ---------------------------------------------------------------------------
# Scenario 2: manual-verdict ingestion.
# ---------------------------------------------------------------------------


class ManualVerdictTest(_LifecycleTestCase):
    """Scenario 2, continuing from scenario 1's manual gate: an admissible
    manual ``APPROVE`` is ingested by exactly one worker and stops at the
    user-only ``/approve-review implementation`` gate; a manual ``REVISE``
    loops through remediation and a fresh local review back to the manual
    gate; a manual ``BLOCK`` launches nothing."""

    def setUp(self) -> None:
        super().setUp()
        self.lc = self.seed("manual", IMPLEMENTING)
        script_full_lifecycle(self.lc)
        result = self.cli(self.lc, "run")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.processes(self.lc), 6)

    def test_a_manual_approve_is_ingested_once_and_stops_at_the_approval_gate(self) -> None:
        lc = self.lc
        lc.paste_manual_verdict("APPROVE", 2)
        lc.add(RECORD_MANUAL, lc.manual_review("APPROVE", 2))
        result = self.cli(lc, "run")

        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        ingest, gate_record = result.records
        self.assertEqual(ingest["selected_action"]["command"], RECORD_MANUAL)
        self.assert_finished(ingest, AWAITING_EXTERNAL)
        self.assertEqual(ingest["worker_route"]["role"], "record-manual-implementation-review")
        self.assertIsNone(ingest["worker_route"]["model"])
        self.assertEqual(self.processes(lc), 7)
        self.assertEqual(gate_record["human_gate_pending"]["safe_resume_command"],
                         f"/approve-review implementation {WI}")
        self.assertIn("user-only", gate_record["human_gate_pending"]["what_is_required"])
        self.assert_no_user_only_task(lc)

        # The approval stays the human's: another run launches nothing.
        again = self.cli(lc, "run", fail_if_invoked=True)
        self.assert_gate(again, lc, processes_before=7, safe=f"/approve-review implementation {WI}")

    def test_a_manual_revise_loops_through_remediation_and_a_fresh_local_review(self) -> None:
        lc = self.lc
        lc.paste_manual_verdict("REVISE", 2)
        lc.add(RECORD_MANUAL, lc.manual_review("REVISE", 2))
        lc.add(APPLY_PENDING, lc.apply())
        lc.add(REVIEW_IMPLEMENTATION, lc.local_review("APPROVE", 3))
        result = self.cli(lc, "run")

        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        ingest, apply_record, review, gate_record = result.records
        self.assert_finished(ingest, APPLYING)
        self.assertEqual(apply_record["selected_action"]["command"], APPLY)
        self.assertEqual(apply_record["selected_action"]["task_addendum"],
                         evidence.pending_review_stage_write_addendum(WI, AWAITING_LOCAL))
        self.assert_finished(apply_record, AWAITING_LOCAL)
        self.assert_finished(review, AWAITING_MANUAL)
        self.assertEqual(gate_record["human_gate_pending"]["safe_resume_command"], RECORD_MANUAL)
        self.assertIn(f"bundle_id {bundle_id(3)}", gate_record["human_gate_pending"]["what_is_required"])
        self.assertEqual(self.processes(lc), 9)
        self.assertEqual(fixtures.scripted_worker_tasks(lc.script_path)[6:],
                         [RECORD_MANUAL, APPLY_PENDING, REVIEW_IMPLEMENTATION])
        self.assertEqual(fixtures.state_entry(lc.root, WI)["implementation_revision"], 3)
        self.assert_no_user_only_task(lc)

    def test_a_manual_block_launches_nothing(self) -> None:
        lc = self.lc
        lc.paste_manual_verdict("BLOCK", 2)
        result = self.cli(lc, "run", fail_if_invoked=True)
        self.assert_gate(result, lc, processes_before=6, contains=("blocked",), safe=RECORD_MANUAL)
        self.assertEqual(len(result.records), 1)


# ---------------------------------------------------------------------------
# Scenario 3: fail-closed artifacts.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FailClosedCase:
    """One automated command whose worker publishes the phase but leaves a
    defective artifact. ``prepare`` names the pre-state builder, ``effect``
    builds the failing action list, ``row`` is the ``ExpectedOutcome`` key,
    ``detail`` a fragment of the postcondition detail, and ``next_step``
    asserts what the next decision is."""

    prepare: str
    task: str
    effect: Callable[[Lifecycle], list[dict]]
    row: tuple[str, str, str]
    observed: str
    detail: str
    next_step: Callable[["FailClosedArtifactTest", Lifecycle], None]


def _regeneration_next(stage: str):
    def check(test: "FailClosedArtifactTest", lc: Lifecycle) -> None:
        gate = test.next_step_gate(lc)
        test.assert_regeneration_gate(gate, lc, stage)
    return check


def _rejected_next(stage: str):
    def check(test: "FailClosedArtifactTest", lc: Lifecycle) -> None:
        gate = test.next_step_gate(lc)
        test.assert_rejected_gate(gate, lc, stage)
    return check


def _manual_gate_next(test: "FailClosedArtifactTest", lc: Lifecycle) -> None:
    """A local ``APPROVE`` recorded against other content is not a bundle
    fault, so no bundle gate applies: the next decision is the manual gate,
    which launches nothing, and names the ledger's (wrong) content."""
    gate = test.next_step_gate(lc, safe=RECORD_MANUAL)
    test.assertIn(f"review_content_id {OTHER_CONTENT} from the ledger", gate["what_is_required"])


def _approval_refusal_next(test: "FailClosedArtifactTest", lc: Lifecycle) -> None:
    gate = test.next_step_gate(lc, safe=f"workflow-controller explain --work-item {WI}")
    test.assertIn("/approve-review implementation would refuse here", gate["what_is_required"])
    test.assertIn(f"the ledger's review_content_id '{OTHER_CONTENT}' is not the current content's",
                  gate["what_is_required"])


def uncommitted_checkpoint_fact(checkpoint_id: str) -> str:
    """The durable-state gate's fact for a checkpoint ``COMPLETE`` only in
    the working tree, whose committed status is absent."""
    return (f"checkpoint {checkpoint_id!r} is COMPLETE in the working tree's WORKFLOW_STATE.json, but its "
            "status in the state committed at HEAD is absent")


#: The durable-state gate's fact for an uncommitted
#: ``SELF_REVIEWING_IMPLEMENTATION`` transition.
UNCOMMITTED_PHASE_FACT = (f"the working tree's phase is {SELF_REVIEWING!r}, but the committed phase at HEAD "
                          f"is {IMPLEMENTING!r}")


def _uncommitted_state_next(*facts: str):
    """After an uncommitted checkpoint completion or an uncommitted
    ``SELF_REVIEWING_IMPLEMENTATION`` transition, the working tree records
    state ``HEAD`` does not: the next ``step`` is the durable-state gate
    (``evidence.uncommitted_implementation_state``), naming each fact, and
    launches nothing -- another ``/milestone-implement`` worker could
    otherwise build on the uncommitted completion or commit it."""
    def check(test: "FailClosedArtifactTest", lc: Lifecycle) -> None:
        gate = test.next_step_gate(lc, safe=f"workflow-controller explain --work-item {WI}")
        test.assert_uncommitted_state_gate(gate, lc, facts)
        test.assertEqual(evidence.committed_work_item(lc.root, WI, "HEAD")["phase"], IMPLEMENTING)
    return check


FAIL_CLOSED_CASES: dict[str, FailClosedCase] = {
    "milestone-implement leaves the last checkpoint completion uncommitted": FailClosedCase(
        prepare="at_last_checkpoint", task=MILESTONE_IMPLEMENT,
        effect=lambda lc: lc.implement("CP2", last=True, commit="product"),
        row=(IMPLEMENTING, "2.2", "/milestone-implement"), observed=SELF_REVIEWING,
        detail="the checkpoint completion is uncommitted",
        next_step=_uncommitted_state_next(uncommitted_checkpoint_fact("CP2"), UNCOMMITTED_PHASE_FACT),
    ),
    "milestone-implement leaves the self-review transition uncommitted": FailClosedCase(
        prepare="at_every_checkpoint_done", task=MILESTONE_IMPLEMENT,
        effect=lambda lc: lc.enter_self_review(commit=False),
        row=(IMPLEMENTING, "2.2", "/milestone-implement"), observed=SELF_REVIEWING,
        detail="the committed phase at HEAD is 'IMPLEMENTING', not 'SELF_REVIEWING_IMPLEMENTATION': "
               "the transition is uncommitted",
        next_step=_uncommitted_state_next(UNCOMMITTED_PHASE_FACT),
    ),
    "final pass leaves a stale bundle": FailClosedCase(
        prepare="at_self_reviewing", task=MILESTONE_IMPLEMENT,
        effect=lambda lc: lc.generate(manifest="before-record"),
        row=(SELF_REVIEWING, "2.2", "/milestone-implement"), observed=AWAITING_LOCAL,
        detail="manifest generation_head", next_step=_regeneration_next("implementation"),
    ),
    "final pass leaves the bundle withdrawn": FailClosedCase(
        prepare="at_self_reviewing", task=MILESTONE_IMPLEMENT,
        effect=lambda lc: lc.generate(manifest="withdrawn"),
        row=(SELF_REVIEWING, "2.2", "/milestone-implement"), observed=AWAITING_LOCAL,
        detail="is absent (withdrawn or never generated)", next_step=_regeneration_next("implementation"),
    ),
    "final pass leaves the bundle REJECTED": FailClosedCase(
        prepare="at_self_reviewing", task=MILESTONE_IMPLEMENT,
        effect=lambda lc: lc.generate(manifest="rejected"),
        row=(SELF_REVIEWING, "2.2", "/milestone-implement"), observed=AWAITING_LOCAL,
        detail="the implementation bundle was withdrawn", next_step=_rejected_next("implementation"),
    ),
    "local review records an APPROVE bound to other content": FailClosedCase(
        prepare="at_awaiting_local", task=REVIEW_IMPLEMENTATION,
        effect=lambda lc: lc.local_review("APPROVE", 1, ledger_content=OTHER_CONTENT),
        row=(AWAITING_LOCAL, "2.2", "/review-implementation"), observed=AWAITING_MANUAL,
        detail=f"ledger review_content_id '{OTHER_CONTENT}' != manifest review_content_id",
        next_step=_manual_gate_next,
    ),
    "manual ingestion records a ledger bound to other content": FailClosedCase(
        prepare="at_manual_verdict", task=RECORD_MANUAL,
        effect=lambda lc: lc.manual_review("APPROVE", 1, ledger_content=OTHER_CONTENT),
        row=(AWAITING_MANUAL, "2.2", "/record-manual-implementation-review"), observed=AWAITING_EXTERNAL,
        detail=f"ledger review_content_id '{OTHER_CONTENT}' != manifest review_content_id",
        next_step=_approval_refusal_next,
    ),
    "remediation leaves the previous round's bundle": FailClosedCase(
        prepare="at_applying", task=APPLY_PENDING, effect=lambda lc: lc.apply(manifest="none"),
        row=(APPLYING, "2.2", "/apply-implementation-review"), observed=AWAITING_LOCAL,
        detail="manifest implementation_revision 1 != state implementation_revision 2",
        next_step=_regeneration_next("post-fix"),
    ),
    "remediation leaves the bundle withdrawn": FailClosedCase(
        prepare="at_applying", task=APPLY_PENDING, effect=lambda lc: lc.apply(manifest="withdrawn"),
        row=(APPLYING, "2.2", "/apply-implementation-review"), observed=AWAITING_LOCAL,
        detail="is absent (withdrawn or never generated)", next_step=_regeneration_next("post-fix"),
    ),
    "remediation leaves the bundle REJECTED": FailClosedCase(
        prepare="at_applying", task=APPLY_PENDING, effect=lambda lc: lc.apply(manifest="rejected"),
        row=(APPLYING, "2.2", "/apply-implementation-review"), observed=AWAITING_LOCAL,
        detail="the implementation bundle was withdrawn", next_step=_rejected_next("post-fix"),
    ),
}


class FailClosedArtifactTest(_LifecycleTestCase):
    """Scenario 3: for each automated command, a worker that publishes the
    phase but leaves the bundle stale, withdrawn or ``REJECTED``, a ledger
    bound to the wrong ``review_content_id``, or a checkpoint completion
    uncommitted, yields ``FAILED`` with the postcondition's detail; the
    next decision launches nothing; ``resume`` of the same job as
    ``COMPLETED`` is ``FAILED`` and as ``LAUNCHED`` is
    ``UnreconcilableJobError`` (exit 20). The positive control removes only
    that row's postconditions (``mock.patch.dict`` over
    ``_EXPECTED_OUTCOMES_BY_KEY``) and the same worker verifies
    ``FINISHED``: the postcondition is what fails closed."""

    def next_step_gate(self, lc: Lifecycle, *, safe: str | None = None) -> dict:
        processes = self.processes(lc)
        result = self.cli(lc, "step", fail_if_invoked=True)
        self.assertEqual(len(result.records), 1)
        return self.assert_gate(result, lc, processes_before=processes, safe=safe)

    def _failing_run(self, name: str, case: FailClosedCase) -> tuple[Lifecycle, dict]:
        lc = getattr(self, case.prepare)(name)
        lc.add(case.task, case.effect(lc))
        result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        self.assertEqual(record["worker_outcome"], "SUCCESS")
        self.assertEqual(record["observed_phase_after"], case.observed)
        self.assert_failed(record, "postcondition_not_satisfied", case.detail)
        return lc, record

    def _assert_resume_fails_closed(self, lc: Lifecycle, record: dict, case: FailClosedCase) -> None:
        detail = record["reconciliation_evidence"]["postcondition_detail"]

        self.rewrite_as(lc, record, job.STATUS_COMPLETED)
        result = self.cli(lc, "resume")
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        completed = self.read_record(lc, record["job_id"])
        self.assert_failed(completed, "postcondition_not_satisfied")
        self.assertEqual(completed["reconciliation_evidence"]["postcondition_detail"], detail)

        self.rewrite_as(lc, record, job.STATUS_LAUNCHED)
        result = self.cli(lc, "resume")
        self.assertEqual(result.code, cli.EXIT_FAIL_CLOSED, result.stdout)
        self.assertIn("cannot be reconciled", result.stderr)
        self.assertIn(f"postcondition not satisfied: {detail}", result.stderr)
        launched = self.read_record(lc, record["job_id"])
        self.assertEqual(launched["status"], job.STATUS_FAILED)
        self.assertEqual(launched["reconciliation_evidence"]["code"], job.UNRECONCILABLE_JOB_CODE)
        self.assertEqual(launched["reconciliation_evidence"]["postcondition_detail"], detail)
        self.assertEqual(launched["observed_phase_after"], case.observed)

    def _assert_postcondition_is_what_fails(self, name: str, case: FailClosedCase) -> None:
        lc = getattr(self, case.prepare)(name)
        lc.add(case.task, case.effect(lc))
        row = job._EXPECTED_OUTCOMES_BY_KEY[case.row]
        self.assertTrue(row.postconditions)
        without = dataclasses.replace(row, postconditions=())
        with unittest.mock.patch.dict(job._EXPECTED_OUTCOMES_BY_KEY, {case.row: without}):
            result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        [record] = result.records
        self.assert_finished(record, case.observed)

    def test_each_defective_artifact_fails_closed_and_the_next_step_launches_nothing(self) -> None:
        for i, (name, case) in enumerate(FAIL_CLOSED_CASES.items()):
            with self.subTest(case=name):
                lc, record = self._failing_run(f"fail-{i}", case)
                case.next_step(self, lc)
                self._assert_resume_fails_closed(lc, record, case)
                self._assert_postcondition_is_what_fails(f"control-{i}", case)

    def test_coherent_artifacts_verify_for_every_row(self) -> None:
        """The positive controls with every postcondition in place: each
        command's coherent effect from the same pre-states verifies."""
        coherent = (
            ("at_last_checkpoint", MILESTONE_IMPLEMENTATION_LAST, SELF_REVIEWING),
            ("at_self_reviewing", MILESTONE_IMPLEMENTATION_FINAL, AWAITING_LOCAL),
            ("at_awaiting_local", REVIEW_APPROVE, AWAITING_MANUAL),
            ("at_awaiting_local", REVIEW_REVISE, APPLYING),
            ("at_manual_verdict", MANUAL_APPROVE, AWAITING_EXTERNAL),
            ("at_applying", APPLY_ROUND, AWAITING_LOCAL),
        )
        for i, (prepare, (task, effect), observed) in enumerate(coherent):
            with self.subTest(prepare=prepare, task=task.split()[0], observed=observed):
                lc = getattr(self, prepare)(f"coherent-{i}")
                lc.add(task, effect(lc))
                result = self.cli(lc, "step")
                self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
                [record] = result.records
                self.assert_finished(record, observed)


#: ``(task, effect)`` of each command's coherent effect, for the positive
#: controls.
MILESTONE_IMPLEMENTATION_LAST = (MILESTONE_IMPLEMENT, lambda lc: lc.implement("CP2", last=True))
MILESTONE_IMPLEMENTATION_FINAL = (MILESTONE_IMPLEMENT, lambda lc: lc.generate())
REVIEW_APPROVE = (REVIEW_IMPLEMENTATION, lambda lc: lc.local_review("APPROVE", 1))
REVIEW_REVISE = (REVIEW_IMPLEMENTATION, lambda lc: lc.local_review("REVISE", 1))
MANUAL_APPROVE = (RECORD_MANUAL, lambda lc: lc.manual_review("APPROVE", 1))
APPLY_ROUND = (APPLY_PENDING, lambda lc: lc.apply())


@dataclasses.dataclass(frozen=True)
class UncommittedCase:
    """A ``/milestone-implement`` worker that leaves a completion only in
    the working tree. ``prepare`` names the pre-state builder, ``effect``
    the failing action list, ``reason`` the failed record's reason,
    ``facts`` the gate's facts, ``checkpoint`` the checkpoint whose
    completion the human commits (``None`` for the step-2 transition), and
    ``next_effect``/``next_observed`` the durable path's next worker."""

    prepare: str
    effect: Callable[[Lifecycle], list[dict]]
    observed: str
    reason: str
    facts: tuple[str, ...]
    checkpoint: str | None
    next_effect: Callable[[Lifecycle], list[dict]]
    next_observed: str


UNCOMMITTED_CASES: dict[str, UncommittedCase] = {
    "a checkpoint completion at IMPLEMENTING": UncommittedCase(
        prepare="at_first_checkpoint", effect=lambda lc: lc.implement("CP1", commit="product"),
        observed=IMPLEMENTING, reason="predicate_not_satisfied", facts=(uncommitted_checkpoint_fact("CP1"),),
        checkpoint="CP1", next_effect=lambda lc: lc.implement("CP2", last=True), next_observed=SELF_REVIEWING,
    ),
    "the last checkpoint completion": UncommittedCase(
        prepare="at_last_checkpoint", effect=lambda lc: lc.implement("CP2", last=True, commit="product"),
        observed=SELF_REVIEWING, reason="postcondition_not_satisfied",
        facts=(uncommitted_checkpoint_fact("CP2"), UNCOMMITTED_PHASE_FACT),
        checkpoint="CP2", next_effect=lambda lc: lc.generate(), next_observed=AWAITING_LOCAL,
    ),
    "the SELF_REVIEWING_IMPLEMENTATION transition": UncommittedCase(
        prepare="at_every_checkpoint_done", effect=lambda lc: lc.enter_self_review(commit=False),
        observed=SELF_REVIEWING, reason="postcondition_not_satisfied", facts=(UNCOMMITTED_PHASE_FACT,),
        checkpoint=None, next_effect=lambda lc: lc.generate(), next_observed=AWAITING_LOCAL,
    ),
}


class UncommittedImplementationStateTest(_LifecycleTestCase):
    """Scenario 3's uncommitted-completion cases, end to end. Once a
    ``/milestone-implement`` worker leaves a checkpoint completion or the
    ``SELF_REVIEWING_IMPLEMENTATION`` transition only in the working tree,
    neither ``run`` nor ``step`` launches another lifecycle worker: the
    durable-state gate stops both, at ``IMPLEMENTING`` and at
    ``SELF_REVIEWING_IMPLEMENTATION`` alike. Once a human commits the
    completion as the Workflow step would have, with its trailers, the
    committed, durable path proceeds as before."""

    def at_first_checkpoint(self, name: str) -> Lifecycle:
        return self.seed(name, IMPLEMENTING)

    def test_no_lifecycle_worker_launches_until_the_completion_is_committed(self) -> None:
        for i, (name, case) in enumerate(UNCOMMITTED_CASES.items()):
            with self.subTest(case=name):
                lc = getattr(self, case.prepare)(f"uncommitted-{i}")
                lc.add(MILESTONE_IMPLEMENT, case.effect(lc))
                failed = self.cli(lc, "step")
                self.assertEqual(failed.code, cli.EXIT_WORKER_FAILED, failed.stderr)
                [record] = failed.records
                self.assertEqual(record["observed_phase_after"], case.observed)
                self.assert_failed(record, case.reason)
                self.assertEqual(self.processes(lc), 1)

                # Neither `run` nor `step` launches a worker: each records
                # the one gate, and the fail-if-invoked fake never starts.
                for command in ("run", "step"):
                    result = self.cli(lc, command, fail_if_invoked=True)
                    self.assertEqual(len(result.records), 1, command)
                    gate = self.assert_gate(result, lc, processes_before=1)
                    self.assert_uncommitted_state_gate(gate, lc, case.facts)
                decision = self.explain(lc)
                self.assertFalse(decision["automatic"])
                self.assertIsNone(decision["action"])
                self.assertEqual(decision["evidence"], list(case.facts))

                # The human commits the completion as the step would have:
                # step 1f's trailers, or step 2's single work-item trailer.
                trailers = ((WORK_ITEM, WI),)
                if case.checkpoint is not None:
                    trailers = (("Workflow-Checkpoint", case.checkpoint),) + trailers
                lc.perform([fixtures.script_commit(
                    fixtures.trailer_message("Commit the completion", *trailers), STATE_REL,
                )])
                self.assertEqual(evidence.committed_work_item(lc.root, WI, "HEAD")["phase"], case.observed)

                # The committed, durable path proceeds: the next worker is
                # selected, launched and verified.
                decision = self.explain(lc)
                self.assertTrue(decision["automatic"])
                self.assertEqual(decision["action"], MILESTONE_IMPLEMENT)
                self.assertEqual(decision["evidence"], [])
                lc.add(MILESTONE_IMPLEMENT, case.next_effect(lc))
                proceeded = self.cli(lc, "step")
                self.assertEqual(proceeded.code, cli.EXIT_OK, proceeded.stderr)
                [record] = proceeded.records
                self.assert_finished(record, case.next_observed)
                self.assertEqual(self.processes(lc), 2)


# ---------------------------------------------------------------------------
# Scenario 4: resume and reconciliation.
# ---------------------------------------------------------------------------


#: A child Controller: ``execute_step`` against the target, from a process
#: the test can ``SIGKILL`` once the worker is recorded (the CP5 orphan
#: shape), with the same generation as the test's own pinned identity.
_CHILD_STEP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pathlib import Path; from controller import job; "
    "from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT; "
    "ident = ControllerIdentity(generation=1, source_root=Path('/fake'), "
    "origin_source_root=Path('/fake'), source_kind=SOURCE_KIND_COMMIT, "
    "source_commit='a'*40, tree_digest='d'*64, generation_source='head', "
    "pinned_at='2026-01-01T00:00:00Z', version='1.1.1'); "
    "from controller.managed_repo import ManagedRepository; "
    "mr = ManagedRepository(root=Path(sys.argv[2]), manifest={}, "
    "workflow_version='2.5.1', profile='full', "
    "verify={'returncode': 0, 'stdout': '', 'stderr': ''}, "
    "status={'returncode': 0, 'stdout': '', 'stderr': ''}); "
    "job.execute_step(mr, identity=ident, runtime=Path(sys.argv[3]), claude_bin=sys.argv[4])"
)


class ResumeReconciliationTest(_LifecycleTestCase):
    """Scenario 4: ``LAUNCHED``/``COMPLETED`` records of every new row,
    reconciled by ``resume`` against coherent targets (``FINISHED``, never a
    relaunch) and against an unchanged one (``INTERRUPTED``); scenario 3
    covers the incoherent ones. Then CP5's orphan and active cases at an
    implementation-stage phase, including the I2 case: the Controller dies
    during an ``/apply-implementation-review`` and the orphaned worker
    commits ``T`` while its generator fails."""

    def _step_and_reconcile(self, lc: Lifecycle, observed: str) -> dict:
        processes = self.processes(lc)
        result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        [record] = result.records
        self.assert_finished(record, observed)
        self.assertEqual(self.processes(lc), processes + 1)
        for status in (job.STATUS_COMPLETED, job.STATUS_LAUNCHED):
            with self.subTest(command=record["selected_action"]["command"], observed=observed, status=status):
                self.rewrite_as(lc, record, status)
                resumed = self.cli(lc, "resume")
                self.assertEqual(resumed.code, cli.EXIT_OK, resumed.stderr)
                self.assertEqual(resumed.records, [])
                reconciled = self.read_record(lc, record["job_id"])
                self.assert_finished(reconciled, observed)
                self.assertIn("reconciled_at", reconciled)
                self.assertEqual(self.processes(lc), processes + 1, "resume never relaunches")
        return record

    def test_launched_and_completed_records_of_every_row_reconcile_finished(self) -> None:
        lc = self.seed("rows", IMPLEMENTING)
        lc.add(MILESTONE_IMPLEMENT, lc.implement("CP1"))
        self._step_and_reconcile(lc, IMPLEMENTING)                 # row 13, self-loop
        lc.add(MILESTONE_IMPLEMENT, lc.implement("CP2", last=True))
        self._step_and_reconcile(lc, SELF_REVIEWING)               # row 13
        lc.add(MILESTONE_IMPLEMENT, lc.generate())
        self._step_and_reconcile(lc, AWAITING_LOCAL)               # row 15
        lc.add(REVIEW_IMPLEMENTATION, lc.local_review("REVISE", 1))
        self._step_and_reconcile(lc, APPLYING)                     # row 16, REVISE
        lc.add(APPLY_PENDING, lc.apply())
        self._step_and_reconcile(lc, AWAITING_LOCAL)               # row 18
        lc.add(REVIEW_IMPLEMENTATION, lc.local_review("APPROVE", 2))
        self._step_and_reconcile(lc, AWAITING_MANUAL)              # row 16, APPROVE
        lc.paste_manual_verdict("REVISE", 2)
        lc.add(RECORD_MANUAL, lc.manual_review("REVISE", 2))
        self._step_and_reconcile(lc, APPLYING)                     # row 17, REVISE
        lc.add(APPLY_PENDING, lc.apply())
        self._step_and_reconcile(lc, AWAITING_LOCAL)               # row 18, second round
        lc.add(REVIEW_IMPLEMENTATION, lc.local_review("APPROVE", 3))
        self._step_and_reconcile(lc, AWAITING_MANUAL)
        lc.paste_manual_verdict("APPROVE", 3)
        lc.add(RECORD_MANUAL, lc.manual_review("APPROVE", 3))
        self._step_and_reconcile(lc, AWAITING_EXTERNAL)            # row 17, APPROVE

        result = self.cli(lc, "step", fail_if_invoked=True)
        self.assert_gate(result, lc, processes_before=10, safe=f"/approve-review implementation {WI}")

    def test_a_launched_record_with_nothing_changed_reconciles_interrupted(self) -> None:
        lc = self.at_awaiting_local("interrupted")
        lc.add(REVIEW_IMPLEMENTATION, [])
        result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        self.assert_failed(record, "predicate_not_satisfied")
        self.rewrite_as(lc, record, job.STATUS_LAUNCHED)
        resumed = self.cli(lc, "resume")
        self.assertEqual(resumed.code, cli.EXIT_INTERRUPTED, resumed.stderr)
        self.assertEqual(self.read_record(lc, record["job_id"])["status"], job.STATUS_INTERRUPTED)
        self.assertEqual(self.processes(lc), 1)

    # --- the orphan and active cases (CP5), at APPLYING_REVIEW_FEEDBACK --------

    def _end_orphans(self, lc: Lifecycle, child: subprocess.Popen, release: Path) -> None:
        release.touch()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        for path in (lc.runtime / "jobs").glob("*.json"):
            try:
                worker_process = json.loads(path.read_text()).get("worker_process") or {}
            except (OSError, ValueError, AttributeError):
                continue
            process_fixtures.kill_group(worker_process.get("pgid"))
        if lc.processes_file.exists():
            for line in lc.processes_file.read_text().splitlines():
                process_fixtures.kill_group(int(line))

    def _launched_record(self, lc: Lifecycle) -> dict | None:
        for path in (lc.runtime / "jobs").glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if record.get("status") == job.STATUS_LAUNCHED and "worker_process" in record:
                return record
        return None

    def test_an_orphaned_apply_worker_that_commits_t_without_a_bundle(self) -> None:
        lc = self.at_applying("orphan")
        # The I2 worker: it follows the addendum and commits T, and then its
        # generator fails -- the previous round's manifest stays.
        lc.add(APPLY_PENDING, lc.apply(manifest="none"))
        fixtures.write_worker_script(lc.script_path, lc.script)
        lc.runtime.mkdir(parents=True, exist_ok=True)
        release = lc.case_dir / "release"
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_STEP, str(fixtures.REPO_ROOT), str(lc.root), str(lc.runtime),
             str(FAKE_CLAUDE)],
            env={**os.environ, "FAKE_CLAUDE_SCRIPT": str(lc.script_path),
                 "FAKE_CLAUDE_INVOCATIONS_FILE": str(lc.processes_file),
                 "FAKE_CLAUDE_HANG_UNTIL_FILE": str(release)},
        )
        self.addCleanup(self._end_orphans, lc, child, release)
        self.assertTrue(process_fixtures.wait_until(
            lambda: self._launched_record(lc) is not None or child.poll() is not None, timeout=30,
        ))
        record = self._launched_record(lc)
        self.assertIsNotNone(record, "the LAUNCHED record never carried worker_process")
        self.assertEqual(record["selected_action"]["command"], APPLY)
        self.assertEqual(record["selected_action"]["task_addendum"],
                         evidence.pending_review_stage_write_addendum(WI, AWAITING_LOCAL))
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
        self.assertTrue(process_fixtures.wait_until(lambda: self.processes(lc) == 1),
                        "the orphaned worker never started")

        # Active: while the orphan runs, step exits 45 and nothing is
        # reconciled or launched. (Worker-lifecycle-ownership CP5: `resume`
        # would re-attach to the unsupervised worker instead -- plan E.)
        for command in ("step",):
            with self.subTest(active=command):
                held = self.cli(lc, command, fail_if_invoked=True)
                self.assertEqual(held.code, cli.EXIT_WORKER_ACTIVE, held.stderr)
                self.assertIn("holds the lifecycle lock", held.stderr)
                self.assertIn(f"process group {record['worker_process']['pgid']}", held.stderr)
        self.assertEqual(self.read_record(lc, record["job_id"])["status"], job.STATUS_LAUNCHED)

        # Released, the orphan commits the pending write, a fix and T, and
        # its generator fails.
        head_before = fixtures.current_head(lc.root)
        release.touch()
        # Worker-lifecycle-ownership CP3: the lost Controller's anchor holds
        # the orphan's stdin; ending it ends the session with no supervisor
        # (so CP5's `resume` does not re-attach), and the worker exits once
        # its work is done.
        process_fixtures.end_recorded_anchor(record)
        pgid = record["worker_process"]["pgid"]

        def gone() -> bool:
            if process_fixtures.group_has_running_member(pgid):
                return False
            try:
                lock.acquire_lifecycle_lock(lc.root).release()
            except LifecycleWorkerActiveError:
                return False
            return True

        self.assertTrue(process_fixtures.wait_until(gone, timeout=30), "the released orphan never ended")
        self.assertEqual(_commit_count(lc.root) - int(fixtures.run(
            ["git", "rev-list", "--count", head_before], cwd=lc.root).stdout.strip()), 3)
        self.assertEqual(evidence.bundle_generation_record_role(evidence.commit_trailers(lc.root, "HEAD"), WI),
                         "ordinary")

        # resume: the moved state fails the postcondition -- FAILED on
        # disk, exit 20, once.
        resumed = self.cli(lc, "resume")
        self.assertEqual(resumed.code, cli.EXIT_FAIL_CLOSED, resumed.stdout)
        self.assertIn("postcondition not satisfied", resumed.stderr)
        self.assertIn("now FAILED", resumed.stderr)
        on_disk = self.read_record(lc, record["job_id"])
        self.assertEqual(on_disk["status"], job.STATUS_FAILED)
        self.assertEqual(on_disk["reconciliation_evidence"]["code"], job.UNRECONCILABLE_JOB_CODE)
        self.assertIn("manifest implementation_revision 1 != state implementation_revision 2",
                      on_disk["reconciliation_evidence"]["postcondition_detail"])
        self.assertEqual(self.cli(lc, "resume").code, cli.EXIT_OK)

        # The next step decides from the evidence: CP4B's regeneration gate
        # at post-fix, and nothing launches.
        result = self.cli(lc, "step", fail_if_invoked=True)
        gate = self.assert_gate(result, lc, processes_before=1)
        self.assert_regeneration_gate(gate, lc, "post-fix")


# ---------------------------------------------------------------------------
# Scenario 5: the plan stage is unchanged.
# ---------------------------------------------------------------------------


class PlanStageUnchangedTest(unittest.TestCase):
    """Scenario 5: the previous milestone's end-to-end plan-stage
    scenarios -- the stale-plan-bundle classes of ``tests/test_job.py``,
    ``tests/test_cli.py`` and ``tests/test_resume.py``, run unmodified from
    their own modules -- still pass, and so does the CP1 plan-stage decision
    golden."""

    SUITES = (
        "tests.test_job.PartialApplyPlanReviewExecuteTest",
        "tests.test_cli.PartialApplyPlanReviewCliTest",
        "tests.test_resume.PartialApplyPlanReviewResumeTest",
        "tests.test_golden_plan_stage_decisions",
    )

    def test_the_plan_stage_scenarios_and_golden_still_pass(self) -> None:
        for name in self.SUITES:
            with self.subTest(suite=name):
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
                result = unittest.TestResult()
                suite.run(result)
                self.assertGreater(result.testsRun, 0)
                self.assertEqual(result.skipped, [])
                self.assertTrue(result.wasSuccessful(),
                                "\n".join(text for _test, text in result.failures + result.errors))


# ---------------------------------------------------------------------------
# Scenario 6: manual and user gates launch nothing.
# ---------------------------------------------------------------------------


def _seed_gate(test: "GatesLaunchNothingTest", name: str, case: str) -> tuple[Lifecycle, str | None, str]:
    """The pre-state of one gate sub-case: ``(lifecycle, expected
    safe_resume_command or None, a fragment of what_is_required)``."""
    explain = f"workflow-controller explain --work-item {WI}"
    if case == "IMPLEMENTING, no plan approval":
        return test.seed(name, IMPLEMENTING, plan_approval=None), explain, "step 1a"
    if case == "SELF_REVIEWING_IMPLEMENTATION, a stale plan approval":
        return (test.seed(name, SELF_REVIEWING, done=CHECKPOINT_IDS, plan_approval={"status": "STALE"}),
                explain, "step 1a")
    if case == "AWAITING_PLAN_APPROVAL":
        return test.seed(name, "AWAITING_PLAN_APPROVAL"), f"/approve-review plan {WI}", "/approve-review plan"
    if case == "AWAITING_FUNCTIONAL_REVIEW, no checklist":
        return (test.seed(name, "AWAITING_FUNCTIONAL_REVIEW", done=CHECKPOINT_IDS),
                f"/prepare-functional-review {WI}", "/prepare-functional-review")

    lc = test.at_awaiting_local(name)
    if case == "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a local BLOCK":
        lc.perform(lc.local_review("BLOCK", 1))
        return lc, REVIEW_IMPLEMENTATION, "explicit user resolution"
    if case == "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a REJECTED marker":
        lc.perform([fixtures.script_write(MARKER_REL, "REJECTED: withdrawal in progress\n")])
        return lc, None, "resolve the failure the REJECTED marker"
    if case == "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a withdrawn bundle":
        lc.perform([fixtures.script_delete(BUNDLE_REL)])
        return lc, None, "perform in order:"
    if case == "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, commits past generation_head":
        lc.perform([fixtures.script_write("NOTES.md", "notes\n"), fixtures.script_commit("Add notes", "NOTES.md")])
        return lc, None, "/recover-implementation-provenance"
    if case == "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a malformed generation record":
        lc.entry["implementation_revision"] = 2
        lc.entry["reviewed_implementation_head"] = "{HEAD}"
        lc.perform([lc.write_state(), fixtures.script_commit(fixtures.trailer_message(
            "Record implementation bundle generation", (GENERATION_RECORD, f"{WI}/2"), (WORK_ITEM, WI),
        ), STATE_REL)])
        return lc, explain, "OPUS-R101-001"
    if case.startswith("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"):
        lc.perform(lc.local_review("APPROVE", 1))
        if case.endswith("no verdict"):
            return lc, RECORD_MANUAL, "upload"
        if case.endswith("an inadmissible verdict"):
            lc.paste_manual_verdict("APPROVE", 1, reviewed_base_commit="0" * 40)
            return lc, RECORD_MANUAL, "not ingestible"
        lc.paste_manual_verdict("BLOCK", 1)
        return lc, RECORD_MANUAL, "blocked"
    if case.startswith("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"):
        lc.perform(lc.local_review("APPROVE", 1))
        lc.paste_manual_verdict("APPROVE", 1)
        if case.endswith("the ledger is incomplete"):
            lc.entry["phase"] = AWAITING_EXTERNAL
            lc.perform([lc.write_state()])
            return lc, explain, "/approve-review implementation would refuse here"
        lc.perform(lc.manual_review("APPROVE", 1))
        if case.endswith("both stages approved"):
            return lc, f"/approve-review implementation {WI}", "user-only"
        lc.paste_manual_verdict("REVISE", 1)
        return lc, APPLY, "a later manual verdict is on file"
    if case.startswith("APPLYING_REVIEW_FEEDBACK"):
        lc.perform(lc.local_review("REVISE", 1))
        if case.endswith("no feedback"):
            lc.perform([fixtures.script_delete(FEEDBACK_REL)])
            return lc, APPLY, "no REVIEW_FEEDBACK.md is on file"
        lc.perform([fixtures.script_write(FEEDBACK_REL, lc.feedback_text("BLOCK", LOCAL, 1))])
        return lc, APPLY, "not an admissible two-stage REVISE"
    raise AssertionError(case)


GATE_CASES = (
    "IMPLEMENTING, no plan approval",
    "SELF_REVIEWING_IMPLEMENTATION, a stale plan approval",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a local BLOCK",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a REJECTED marker",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a withdrawn bundle",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, commits past generation_head",
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW, a malformed generation record",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW, no verdict",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW, an inadmissible verdict",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW, an admissible BLOCK",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW, both stages approved",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW, a later manual REVISE",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW, the ledger is incomplete",
    "APPLYING_REVIEW_FEEDBACK, no feedback",
    "APPLYING_REVIEW_FEEDBACK, an inadmissible verdict",
    "AWAITING_PLAN_APPROVAL",
    "AWAITING_FUNCTIONAL_REVIEW, no checklist",
)


class GatesLaunchNothingTest(_LifecycleTestCase):
    """Scenario 6: at every gate phase and sub-case, ``run`` stops with exit
    10 and one ``GATE_BLOCKED`` record, and the fail-if-invoked fake never
    starts. The apply relaunch bound's gate is scenario 8's."""

    def test_every_gate_launches_nothing(self) -> None:
        for i, case in enumerate(GATE_CASES):
            with self.subTest(case=case):
                lc, safe, fragment = _seed_gate(self, f"gate-{i}", case)
                result = self.cli(lc, "run", fail_if_invoked=True)
                self.assertEqual(len(result.records), 1)
                gate = self.assert_gate(result, lc, processes_before=0, contains=(fragment,), safe=safe)
                self.assertFalse(lc.processes_file.exists())
                self.assertEqual(result.records[0]["observed_phase_before"], fixtures.state_entry(lc.root, WI)["phase"])
                command = result.records[0]["selected_action"]["command"]
                self.assertIsNone(command)
                self.assertTrue(gate["what_is_required"])


# ---------------------------------------------------------------------------
# Scenario 7: a literal apply worker.
# ---------------------------------------------------------------------------


class LiteralApplyWorkerTest(_LifecycleTestCase):
    """Scenario 7 (I1): an ``/apply-implementation-review`` worker that
    ignores the addendum and follows the frozen text literally -- a fix
    commit, then ``T`` staging only ``WORKFLOW_STATE.json``, which folds the
    pending review-stage write into ``T``, then no manifest, because the real
    generator's preflight would refuse. The job fails and the next decision
    is the malformed-``T`` gate. With the pending write already committed by
    a human, the task has no addendum and the same literal worker verifies."""

    def test_a_worker_ignoring_the_addendum_leaves_a_malformed_t_that_gates(self) -> None:
        lc = self.at_applying("literal")
        lc.add(APPLY_PENDING, lc.apply(commit_pending=False, manifest="none"))
        result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        self.assertEqual(record["selected_action"]["task_addendum"],
                         evidence.pending_review_stage_write_addendum(WI, AWAITING_LOCAL))
        self.assertEqual(record["observed_phase_after"], AWAITING_LOCAL)
        self.assert_failed(record, "postcondition_not_satisfied",
                           "manifest implementation_revision 1 != state implementation_revision 2")

        t = fixtures.current_head(lc.root)
        self.assertEqual(evidence.committed_work_item(lc.root, WI, "HEAD^")["phase"], AWAITING_LOCAL)
        gated = self.cli(lc, "step", fail_if_invoked=True)
        gate = self.assert_gate(gated, lc, processes_before=1, contains=(t, "OPUS-R101-001"),
                                safe=f"workflow-controller explain --work-item {WI}")
        self.assertNotIn("perform in order", gate["what_is_required"])
        self.assertNotIn("prepare-ai-review.sh", gate["what_is_required"])

    def test_with_the_pending_write_committed_there_is_no_addendum_and_it_verifies(self) -> None:
        lc = self.at_applying("committed-first")
        fixtures.commit_paths(lc.root, fixtures.trailer_message(
            "Commit the pending review-stage state write", (WORK_ITEM, WI)), STATE_REL)
        lc.add(apply_task(None), lc.apply(commit_pending=False))
        result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        [record] = result.records
        self.assertEqual(record["selected_action"]["command"], APPLY)
        self.assertIsNone(record["selected_action"]["task_addendum"])
        self.assert_finished(record, AWAITING_LOCAL)
        self.assertEqual(fixtures.scripted_worker_tasks(lc.script_path), [APPLY])


# ---------------------------------------------------------------------------
# Scenario 8: the apply relaunch bound.
# ---------------------------------------------------------------------------


class ApplyRelaunchBoundTest(_LifecycleTestCase):
    """Scenario 8 (I5): an apply worker that rewrites
    ``IMPLEMENTATION_SUMMARY.md`` (its step 4) and exits without
    transitioning fails; the next ``run`` stops at the relaunch-bound gate,
    and no second worker runs."""

    def test_an_unverified_apply_attempt_is_never_relaunched_against_the_same_bundle(self) -> None:
        lc = self.at_applying("relaunch")
        lc.add(APPLY_PENDING, [fixtures.script_write(
            f"{BUNDLE_REL}/IMPLEMENTATION_SUMMARY.md",
            "# Implementation summary\n\nimplementation_revision: 1\n\nRejected finding F1: not reproducible.\n",
        )])
        first = self.cli(lc, "run")
        self.assertEqual(first.code, cli.EXIT_WORKER_FAILED, first.stderr)
        [failed] = first.records
        self.assert_failed(failed, "phase_not_in_to_any_of")
        self.assertEqual(failed["pre_state"]["bundle_manifest_bundle_id"], bundle_id(1))

        second = self.cli(lc, "run", fail_if_invoked=True)
        gate = self.assert_gate(
            second, lc, processes_before=1,
            contains=(f"job {failed['job_id']}, ended FAILED", "review-bundle.tar.gz"),
            safe=f"workflow-controller explain --work-item {WI}",
        )
        self.assertIn(bundle_id(1), gate["what_is_required"])
        self.assertEqual(len(second.records), 1)


# ---------------------------------------------------------------------------
# Scenario 9: a same_content post-fix whose generator failed.
# ---------------------------------------------------------------------------


class SameContentGeneratorFailureTest(_LifecycleTestCase):
    """Scenario 9 (I4): a remediation round that lands a recovered-role
    ``T`` (``Workflow-Supersedes``, revision unchanged) and no manifest
    fails; the next decision names the regeneration steps at ``post-fix``,
    never ``/recover-implementation-provenance``."""

    def test_the_next_step_names_the_post_fix_regeneration(self) -> None:
        lc = self.at_applying("same-content")
        lc.sync()
        superseded = fixtures.current_head(lc.root)
        lc.add(APPLY_PENDING, lc.apply(fix=False, outcome="same_content", supersedes=superseded, manifest="none"))
        result = self.cli(lc, "step")
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        self.assertEqual(record["observed_phase_after"], AWAITING_LOCAL)
        self.assert_failed(record, "postcondition_not_satisfied", "manifest generation_head")
        self.assertEqual(evidence.bundle_generation_record_role(evidence.commit_trailers(lc.root, "HEAD"), WI),
                         "recovered")
        self.assertEqual(fixtures.state_entry(lc.root, WI)["implementation_revision"], 1)

        gated = self.cli(lc, "step", fail_if_invoked=True)
        gate = self.assert_gate(gated, lc, processes_before=1)
        self.assert_regeneration_gate(gate, lc, "post-fix")
        self.assertIn(fixtures.current_head(lc.root), " ".join(gated.records[0]["selected_action"]["evidence"]))


# ---------------------------------------------------------------------------
# Scenario 10: the NO_CHECKPOINT partial run.
# ---------------------------------------------------------------------------


class NoCheckpointPartialRunTest(_LifecycleTestCase):
    """Scenario 10 (O4): from ``IMPLEMENTING`` with every checkpoint already
    ``COMPLETE``, a worker that makes only step 2's state-only
    ``SELF_REVIEWING_IMPLEMENTATION`` commit and stops verifies, and the
    next ``run`` launches the final pass."""

    def test_the_partial_run_verifies_and_the_next_run_launches_the_final_pass(self) -> None:
        lc = self.seed("no-checkpoint", IMPLEMENTING, done=CHECKPOINT_IDS)
        lc.add(MILESTONE_IMPLEMENT, lc.enter_self_review())
        partial = self.cli(lc, "step")
        self.assertEqual(partial.code, cli.EXIT_OK, partial.stderr)
        [record] = partial.records
        self.assert_finished(record, SELF_REVIEWING)
        self.assertEqual(record["worker_route"]["role"], "milestone-implement-self-review")
        self.assertEqual(evidence.commit_trailers(lc.root, "HEAD"), {WORK_ITEM: WI})

        lc.add(MILESTONE_IMPLEMENT, lc.generate())
        lc.add(REVIEW_IMPLEMENTATION, lc.local_review("APPROVE", 1))
        result = self.cli(lc, "run")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        final_pass, review, gate_record = result.records
        self.assertEqual(final_pass["selected_action"]["command"], MILESTONE_IMPLEMENT)
        self.assertEqual(final_pass["observed_phase_before"], SELF_REVIEWING)
        self.assertEqual(final_pass["worker_route"]["role"], "milestone-implement-self-review")
        self.assert_finished(final_pass, AWAITING_LOCAL)
        self.assert_finished(review, AWAITING_MANUAL)
        self.assertEqual(gate_record["human_gate_pending"]["safe_resume_command"], RECORD_MANUAL)
        self.assertEqual(self.processes(lc), 3)


# ---------------------------------------------------------------------------
# Worker-lifecycle-ownership CP4: a worker that waits on its own background
# work, through the real `run`/`step`, `execute_step` and verification. The
# worker is the scripted fake in streaming mode (`{"turns": [...]}`
# invocations), so each job's turns are its own.
# ---------------------------------------------------------------------------

#: A second ``step`` from another process: ``cli.main`` with the same
#: pinned identity, exiting with its real code.
_CHILD_CLI = (
    "import sys, json, unittest.mock; sys.path.insert(0, sys.argv[1]); "
    "from pathlib import Path; from controller import cli, identity; "
    "from controller.identity import ControllerIdentity; "
    "fields = json.loads(sys.argv[2]); "
    "fields.update(source_root=Path(fields['source_root']), "
    "origin_source_root=Path(fields['origin_source_root'])); "
    "ident = ControllerIdentity(**fields); "
    "unittest.mock.patch.object(identity, 'pin', return_value=ident).start(); "
    "unittest.mock.patch.object(identity, 'current', return_value=ident).start(); "
    "sys.exit(cli.main(sys.argv[3:]))"
)

RUNNING, WAITING, ENDING, DRAINING, ENDED = (worker.RUNNING, worker.WAITING, worker.ENDING, worker.DRAINING,
                                             worker.ENDED)
_WORKER_STATE_EVENTS = ("worker_running", "worker_waiting", "worker_ending", "worker_draining", "worker_ended")


def verification(done: Path, *, seconds: float = 1.5, release: Path | None = None) -> dict:
    """A background full verification (``bash_bg``) that writes its
    completion time (``time.time()``) to ``done`` -- after ``seconds``, or
    once ``release`` exists."""
    import shlex
    wait = (f"while [ ! -e {shlex.quote(str(release))} ]; do sleep 0.05; done" if release is not None
            else f"sleep {seconds}")
    return {"step": "bash_bg", "id": "verify", "description": "full verification",
            "command": f"{wait}; date +%s.%N > {shlex.quote(str(done))}"}


SAYS_IT_WILL_CONTINUE = {"step": "text", "text": "Full verification is running in the background; I will "
                                                 "continue when it completes."}


class _WaitingWorkerCase(_LifecycleTestCase):
    """Scripted streaming sessions, a write spy with a hook, the recorded
    ``worker_state`` history, and a second-process ``step``. Every worker,
    anchor and tagged process a record names is reaped at cleanup."""

    def seed(self, name: str, phase: str, **kwargs) -> Lifecycle:
        lc = super().seed(name, phase, **kwargs)
        self.addCleanup(process_fixtures.reap_recorded_workers, lc.runtime)
        return lc

    def cli_spied(self, lc: Lifecycle, command: str, *args: str,
                  hook: Callable[[dict], None] | None = None) -> tuple[Run, list[tuple[float, dict]]]:
        """:meth:`cli` with every job-record write recorded as
        ``(time.time(), record)`` (after the write) and passed to ``hook``."""
        writes: list[tuple[float, dict]] = []
        real = job.runtime.write_json

        def spy(runtime_root, rel_path, obj):
            written = real(runtime_root, rel_path, obj)
            if str(rel_path).startswith("jobs/"):
                writes.append((time.time(), copy.deepcopy(obj)))
                if hook is not None:
                    hook(obj)
            return written

        with unittest.mock.patch.object(job.runtime, "write_json", spy):
            result = self.cli(lc, command, *args)
        return result, writes

    @staticmethod
    def events(lc: Lifecycle, job_id: str) -> list[dict]:
        path = lc.runtime / "jobs" / job_id / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def history(self, lc: Lifecycle, job_id: str) -> list[str]:
        """The worker states the job's event log records, one per change."""
        return [e["event"][len("worker_"):].upper() for e in self.events(lc, job_id)
                if e["event"] in _WORKER_STATE_EVENTS and e["state_changed"]]

    def child_step(self, lc: Lifecycle) -> subprocess.Popen:
        fields = json.dumps(dataclasses.asdict(self.ident), default=str)
        argv = ["--runtime-dir", str(lc.runtime), "--workflow-manager", str(self.stub_manager),
                "--claude-binary", str(FAKE_CLAUDE), "--timeout", "60", "step", str(lc.root)]
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_CLI, str(fixtures.REPO_ROOT), fields, *argv],
            env={**os.environ, "FAKE_CLAUDE_REQUIRE_FILE": str(self.never),
                 "FAKE_CLAUDE_INVOCATIONS_FILE": str(lc.processes_file)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait()))
        return child

    def script_starts(self, lc: Lifecycle) -> list[float]:
        """Each scripted worker invocation's start time, in order."""
        from tests.fake_claude import script_invocations_path
        counter = Path(script_invocations_path(lc.script_path))
        return [json.loads(line)["at"] for line in counter.read_text().splitlines() if line.strip()]

    def assert_worker_outcome_failure(self, record: dict, outcome: str, reason: str) -> dict:
        """FAILED on the worker outcome (non-verifying before any
        predicate), with ``reason`` the stream diagnosis's."""
        self.assert_failed(record, "worker_outcome")
        self.assertEqual(record["worker_outcome"], outcome)
        diagnosis = record["worker"]["stream_diagnosis"]
        self.assertEqual(diagnosis["reason"], reason, diagnosis)
        return diagnosis

    @staticmethod
    def kill_anchor_when_waiting() -> tuple[Callable[[dict], None], list[int]]:
        killed: list[int] = []

        def hook(obj: dict) -> None:
            if not killed and obj.get("status") == job.STATUS_LAUNCHED \
                    and (obj.get("worker_state") or {}).get("state") == WAITING:
                killed.append(obj["worker_anchor"]["pid"])
                os.kill(obj["worker_anchor"]["pid"], signal.SIGKILL)

        return hook, killed


class WaitingWorkerRegressionTest(_WaitingWorkerCase):
    """R12: the three observed phases in which a worker started its full
    verification in the background, said it would continue, and ended its
    turn -- each now finishes on its continuation turn -- and their
    fail-closed variants (the anchor killed mid-wait)."""

    def _r12_scripts(self, lc: Lifecycle, phase: str, done: Path) -> tuple[str, list, str]:
        """``(task, turns, observed phase after)`` of the R12 worker at ``phase``."""
        if phase == IMPLEMENTING:
            return MILESTONE_IMPLEMENT, [
                [{"step": "actions", "actions": lc.implement("CP1")}, verification(done), SAYS_IT_WILL_CONTINUE],
                [{"step": "text", "text": "Verification passed."}],
            ], IMPLEMENTING
        if phase == SELF_REVIEWING:
            return MILESTONE_IMPLEMENT, [
                [verification(done), {"step": "text", "text": "The full regression suite is running; waiting."}],
                [{"step": "actions", "actions": lc.generate()}],
            ], AWAITING_LOCAL
        fixes = [lc.commit_pending_state(), *lc.fix()]
        return APPLY_PENDING, [
            [{"step": "actions", "actions": fixes}, verification(done), SAYS_IT_WILL_CONTINUE],
            [{"step": "actions", "actions": lc.generate()}],
        ], AWAITING_LOCAL

    def _seed_r12(self, name: str, phase: str) -> Lifecycle:
        if phase == IMPLEMENTING:
            return self.seed(name, IMPLEMENTING)
        if phase == SELF_REVIEWING:
            return self.at_self_reviewing(name)
        return self.at_applying(name)

    def test_r12_a_worker_waiting_on_its_verification_finishes_on_the_continuation_turn(self) -> None:
        """R12a (``IMPLEMENTING``; the observed jobs ``8b244f42`` and
        ``5d4a976a``), R12b (``SELF_REVIEWING_IMPLEMENTATION``; ``66988e17``,
        ``6082a90b``, ``cb43fe49``) and R12c (``APPLYING_REVIEW_FEEDBACK``;
        ``12a9f268``). Against the unfixed Controller the same scripts end
        ``AMBIGUOUS``/``FAILED``: the first turn's ``result`` was taken as
        the end of the job and the session was closed under the running
        verification."""
        for name, phase in (("r12a", IMPLEMENTING), ("r12b", SELF_REVIEWING), ("r12c", APPLYING)):
            with self.subTest(phase=phase):
                lc = self._seed_r12(name, phase)
                done = lc.case_dir / "verification-done"
                task, turns, observed = self._r12_scripts(lc, phase, done)
                lc.add(task, {"turns": turns})
                result, writes = self.cli_spied(lc, "step")
                self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
                [record] = result.records
                self.assert_finished(record, observed)
                self.assertEqual(record["worker_outcome"], "SUCCESS")
                self.assertEqual(record["worker"]["stream_diagnosis"]["turns"], 2)
                completed_at = next(t for t, w in writes if w["status"] == job.STATUS_COMPLETED)
                self.assertLess(float(done.read_text()), completed_at)
                self.assertEqual(self.history(lc, record["job_id"]), [RUNNING, WAITING, RUNNING, ENDING, ENDED])
                self.assertEqual(self.processes(lc), 1)
                if phase == APPLYING:
                    # The relaunch-bound view counts one verified attempt.
                    view = job.last_launched_apply_job_view(lc.runtime, lc.root, WI)
                    self.assertEqual((view.job_id, view.status), (record["job_id"], job.STATUS_FINISHED))
                    self.assertFalse(evidence.relaunch_bound_applies(view, bundle_id(2)))

    def test_r12d_the_anchor_killed_mid_wait_fails_closed(self) -> None:
        """R12d: the same three scripts with the anchor killed while the
        worker waits: stdin closes under the running verification, so the
        outcome is ``AMBIGUOUS`` (``stdin_closed_while_waiting``) and the
        job ``FAILED`` on it -- even at ``IMPLEMENTING``, whose checkpoint
        commit already landed. The ``APPLYING_REVIEW_FEEDBACK`` variant is
        then refused by the apply relaunch bound exactly as today (AC9)."""
        for name, phase in (("r12d-impl", IMPLEMENTING), ("r12d-self", SELF_REVIEWING),
                            ("r12d-apply", APPLYING)):
            with self.subTest(phase=phase):
                lc = self._seed_r12(name, phase)
                done = lc.case_dir / "verification-done"
                task, turns, _observed = self._r12_scripts(lc, phase, done)
                turns[0][turns[0].index(next(s for s in turns[0] if s.get("id") == "verify"))] = \
                    verification(done, release=lc.case_dir / "never-released")
                lc.add(task, {"turns": turns})
                hook, killed = self.kill_anchor_when_waiting()
                result, _writes = self.cli_spied(lc, "step", hook=hook)
                self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
                self.assertTrue(killed)
                [record] = result.records
                self.assert_worker_outcome_failure(record, "AMBIGUOUS", "stdin_closed_while_waiting")
                self.assertFalse(done.exists())
                if phase == APPLYING:
                    second = self.cli(lc, "run", fail_if_invoked=True)
                    gate = self.assert_gate(second, lc, processes_before=1,
                                            contains=(f"job {record['job_id']}, ended FAILED",),
                                            safe=f"workflow-controller explain --work-item {WI}")
                    self.assertIn(bundle_id(1), gate["what_is_required"])


class NextActionWaitsForOwnedWorkTest(_WaitingWorkerCase):
    """R13: with ``run`` driving two ``IMPLEMENTING`` checkpoints, the worker
    for checkpoint N+1 starts only after checkpoint N's owned background
    work ended, and a concurrent ``step`` from a second process during N's
    ``WAITING`` exits 45."""

    def test_checkpoint_n_plus_1_starts_after_n_s_background_verification(self) -> None:
        lc = self.seed("r13-task", IMPLEMENTING)
        done, release = lc.case_dir / "verification-done", lc.case_dir / "release"
        lc.add(MILESTONE_IMPLEMENT, {"turns": [
            [{"step": "actions", "actions": lc.implement("CP1")}, verification(done, release=release),
             SAYS_IT_WILL_CONTINUE],
            [{"step": "text", "text": "Verification passed."}],
        ]})
        lc.add(MILESTONE_IMPLEMENT, {"turns": [[{"step": "actions", "actions": lc.implement("CP2", last=True)}]]})
        concurrent: dict = {}

        def hook(obj: dict) -> None:
            if "child" in concurrent or (obj.get("worker_state") or {}).get("state") != WAITING:
                return
            child = concurrent["child"] = self.child_step(lc)

            def wait_then_release() -> None:
                child.wait()
                concurrent["exited_at"] = time.time()
                release.touch()

            threading.Thread(target=wait_then_release, daemon=True).start()

        result, _writes = self.cli_spied(lc, "run", "--max-steps", "2", hook=hook)
        self.assertEqual(result.code, cli.EXIT_MAX_STEPS, result.stderr)
        first, second = result.records
        self.assert_finished(first, IMPLEMENTING)
        self.assert_finished(second, SELF_REVIEWING)
        child = concurrent["child"]
        self.assertEqual(child.returncode, cli.EXIT_WORKER_ACTIVE, child.stderr.read())
        self.assertLess(concurrent["exited_at"], float(done.read_text()))
        starts = self.script_starts(lc)
        self.assertEqual(len(starts), 2)
        self.assertGreater(starts[1], float(done.read_text()))
        self.assertEqual(self.processes(lc), 2, "the concurrent step launched a worker")

    def test_checkpoint_n_plus_1_starts_after_n_s_reparented_orphan(self) -> None:
        lc = self.seed("r13-orphan", IMPLEMENTING)
        orphan_done = lc.case_dir / "orphan-done"
        lc.add(MILESTONE_IMPLEMENT, {"turns": [
            [{"step": "actions", "actions": lc.implement("CP1")},
             {"step": "bash_bg", "seconds": 0.2, "orphan": "reparent", "orphan_seconds": 2,
              "orphan_write_file": str(orphan_done)}, SAYS_IT_WILL_CONTINUE],
            [{"step": "text", "text": "Verification passed."}],
        ]})
        lc.add(MILESTONE_IMPLEMENT, {"turns": [[{"step": "actions", "actions": lc.implement("CP2", last=True)}]]})
        result = self.cli(lc, "run", "--max-steps", "2")
        self.assertEqual(result.code, cli.EXIT_MAX_STEPS, result.stderr)
        first, second = result.records
        self.assert_finished(first, IMPLEMENTING)
        self.assert_finished(second, SELF_REVIEWING)
        self.assertIn(DRAINING, self.history(lc, first["job_id"]))
        starts = self.script_starts(lc)
        self.assertGreater(starts[1], float(orphan_done.read_text()))


class ResumingWorkerTest(_WaitingWorkerCase):
    """R14: a worker ``WAITING`` on ``bash_bg``, and separately on a wakeup,
    resumes on the completion turn, commits and finishes."""

    def _implementing(self, name: str, turns: list) -> tuple[Lifecycle, dict, Run]:
        lc = self.seed(name, IMPLEMENTING)
        lc.add(MILESTONE_IMPLEMENT, {"turns": turns(lc)})
        result = self.cli(lc, "step")
        return lc, result.records[0], result

    def test_r14_a_worker_waiting_on_a_background_task_resumes_and_finishes(self) -> None:
        lc, record, result = self._implementing("r14-task", lambda lc: [
            [{"step": "bash_bg", "id": "suite", "seconds": 1}, SAYS_IT_WILL_CONTINUE],
            [{"step": "actions", "actions": lc.implement("CP1")}],
        ])
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assert_finished(record, IMPLEMENTING)
        self.assertEqual(record["worker_state"]["state"], ENDED)
        self.assertEqual(self.history(lc, record["job_id"]), [RUNNING, WAITING, RUNNING, ENDING, ENDED])
        waiting = next(e for e in self.events(lc, record["job_id"]) if e["event"] == "worker_waiting")
        self.assertEqual(waiting["tasks"], ["suite"])

    def test_r14_a_worker_waiting_on_a_wakeup_resumes_on_the_fire_and_stops_it(self) -> None:
        lc, record, result = self._implementing("r14-wakeup", lambda lc: [
            [{"step": "wakeup", "delay": 1, "fire_turn": [{"step": "actions", "actions": lc.implement("CP1")},
                                                          {"step": "wakeup_stop"}]},
             {"step": "text", "text": "I will continue when the wakeup fires."}],
        ])
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assert_finished(record, IMPLEMENTING)
        self.assertEqual(self.history(lc, record["job_id"]), [RUNNING, WAITING, RUNNING, ENDING, ENDED])
        diagnosis = record["worker"]["stream_diagnosis"]
        [bracket] = diagnosis["command_lifecycles"]
        [wakeup] = diagnosis["wakeups_seen"]
        self.assertEqual((wakeup["state"], wakeup["settled_by"]), ("settled", "stop"))
        self.assertEqual(wakeup["command_uuid"], bracket["command_uuid"])
        self.assertEqual((wakeup["cancelled_wakeups"], wakeup["expected_count"]), (0, 0))
        self.assertEqual(diagnosis["command_lifecycle_anomalies"], [])
        waiting = next(e for e in self.events(lc, record["job_id"]) if e["event"] == "worker_waiting")
        self.assertEqual([w["state"] for w in waiting["wakeups"]], ["pending"])

    def test_r14_a_matched_wakeup_without_a_stop_settles_after_the_window(self) -> None:
        with unittest.mock.patch.object(worker, "WAKEUP_SETTLE_SECONDS", 1):
            lc, record, result = self._implementing("r14-settle", lambda lc: [
                [{"step": "wakeup", "delay": 1, "fire_turn": [{"step": "actions", "actions": lc.implement("CP1")}]},
                 {"step": "text", "text": "I will continue when the wakeup fires."}],
            ])
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        self.assert_finished(record, IMPLEMENTING)
        self.assertEqual(self.history(lc, record["job_id"]), [RUNNING, WAITING, RUNNING, WAITING, ENDING, ENDED])
        diagnosis = record["worker"]["stream_diagnosis"]
        [bracket] = diagnosis["command_lifecycles"]
        [wakeup] = diagnosis["wakeups_seen"]
        self.assertEqual((wakeup["settled_by"], wakeup["command_uuid"]), ("settle_window", bracket["command_uuid"]))
        self.assertEqual(record["settled_wakeups"], [wakeup["tool_use_id"]])
        self.assertIsInstance(record["ending_offset"], int)
        waits = [e for e in self.events(lc, record["job_id"]) if e["event"] == "worker_waiting" and e["state_changed"]]
        self.assertEqual([w["state"] for w in waits[-1]["wakeups"]], ["fire_matched"])

    def test_r14_an_irregular_fire_fails_closed_although_the_continuation_committed(self) -> None:
        """The fail-closed twin: the fire's ``started(X)`` is omitted, so the
        wakeup is never matched and becomes overdue (grace patched to 1 s).
        The fire turn committed the checkpoint, but the outcome is
        non-verifying before any predicate runs (I7)."""
        with unittest.mock.patch.object(worker, "WAKEUP_GRACE_SECONDS", 1):
            lc, record, result = self._implementing("r14-irregular", lambda lc: [
                [{"step": "lifecycle_fault", "kind": "omit_started"},
                 {"step": "wakeup", "delay": 1, "fire_turn": [{"step": "actions", "actions": lc.implement("CP1")}]},
                 {"step": "text", "text": "I will continue when the wakeup fires."}],
            ])
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        self.assertEqual(evidence.committed_work_item(lc.root, WI, "HEAD")["checkpoints"]["CP1"]["status"],
                         "COMPLETE")
        diagnosis = self.assert_worker_outcome_failure(record, "AMBIGUOUS", "command_lifecycle_irregular")
        self.assertIn("wakeup_not_delivered", diagnosis["secondary_reasons"])


class WrongWakeupMatchTest(_WaitingWorkerCase):
    """R14b: a wrong wakeup match cannot finish a job (round 9's I1). CP3's
    dangerous sequence through ``execute_step``, ``WAKEUP_SETTLE_SECONDS``
    patched to 3 s: the first turn already made the durable state satisfy
    the row's predicate, schedules ``W`` and a ``bash_bg``; the
    task-completion turn after ``W``'s due time is wrapped in a spurious
    bracket, and ``W``'s real fire follows inside the window."""

    def setUp(self) -> None:
        super().setUp()
        patcher = unittest.mock.patch.object(worker, "WAKEUP_SETTLE_SECONDS", 3)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _turns(first: list, task_turn: list) -> list:
        return [[*first, {"step": "wakeup", "delay": 1}, {"step": "bash_bg", "id": "A", "seconds": 2},
                 {"step": "lifecycle_fault", "kind": "spurious_bracket", "turn": "task"},
                 {"step": "lifecycle_fault", "kind": "delay_fire", "seconds": 2}],
                task_turn]

    def _run(self, lc: Lifecycle, task: str, turns: list, *, hook=None):
        lc.add(task, {"turns": turns})
        launch_returned: list[float] = []
        predicate_calls: list[float] = []
        real_launch, real_clauses = job.worker.launch, job._row_clauses_failure

        def launch(*args, **kwargs):
            try:
                return real_launch(*args, **kwargs)
            finally:
                launch_returned.append(time.time())

        def clauses(*args, **kwargs):
            predicate_calls.append(time.time())
            return real_clauses(*args, **kwargs)

        with unittest.mock.patch.object(job.worker, "launch", launch), \
                unittest.mock.patch.object(job, "_row_clauses_failure", clauses):
            result, writes = self.cli_spied(lc, "step", hook=hook)
        self.assertTrue(launch_returned)
        self.assertTrue(all(at >= launch_returned[0] for at in predicate_calls),
                        "the predicate was evaluated before the worker ended")
        return result, writes

    def _assert_owned_until_the_real_fire(self, lc: Lifecycle, record: dict, writes: list) -> dict:
        diagnosis = record["worker"]["stream_diagnosis"]
        spurious, real = diagnosis["command_lifecycles"]
        flushed = [w for _t, w in writes if "worker_state" in w and w["job_id"] == record["job_id"]]
        # Before the real fire's turn (turns < 3): LAUNCHED, never ENDING,
        # and W fire_matched while WAITING after the spurious bracket.
        before = [w for w in flushed if (w["worker_state"].get("turns") or 0) < 3]
        self.assertEqual({w["status"] for w in before}, {job.STATUS_LAUNCHED})
        self.assertNotIn(ENDING, {w["worker_state"]["state"] for w in before})
        matched = [w for w in before if w["worker_state"]["state"] == WAITING
                   and any(entry["state"] == "fire_matched" for entry in w["worker_state"]["waiting_on"]["wakeups"])]
        self.assertTrue(matched, "no WAITING flush named W fire_matched")
        [entry] = matched[0]["worker_state"]["waiting_on"]["wakeups"]
        self.assertEqual(entry["command_uuid"], spurious["command_uuid"])
        self.assertFalse([e for e in self.events(lc, record["job_id"])
                          if e["event"] == "worker_ending" and (e.get("turns") or 0) < 3])
        # ENDING, and so stdin's EOF, came only after the real fire's completed(X).
        self.assertGreater(record["ending_offset"], real["completed_offset"])
        return diagnosis

    def test_a_wrong_match_fails_the_implementing_job_closed(self) -> None:
        lc = self.seed("r14b-impl", IMPLEMENTING)
        concurrent: dict = {}

        def hook(obj: dict) -> None:
            waiting_on = (obj.get("worker_state") or {}).get("waiting_on") or {}
            if "child" not in concurrent and any(w["state"] == "fire_matched" for w in waiting_on.get("wakeups", [])):
                child = concurrent["child"] = self.child_step(lc)
                threading.Thread(target=lambda: (child.wait(), concurrent.setdefault("exited_at", time.time())),
                                 daemon=True).start()

        result, writes = self._run(lc, MILESTONE_IMPLEMENT, self._turns(
            [{"step": "actions", "actions": lc.implement("CP1")}], [{"step": "text", "text": "task done"}]),
            hook=hook)
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        # The checkpoint is committed, so the predicate would pass.
        self.assertEqual(evidence.committed_work_item(lc.root, WI, "HEAD")["checkpoints"]["CP1"]["status"],
                         "COMPLETE")
        diagnosis = self.assert_worker_outcome_failure(record, "AMBIGUOUS", "command_lifecycle_irregular")
        real = diagnosis["command_lifecycles"][1]
        self.assertEqual(diagnosis["command_lifecycle_anomalies"],
                         [{"kind": "unmatched_bracket", "command_uuid": real["command_uuid"],
                           "offset": real["completed_offset"]}])
        self._assert_owned_until_the_real_fire(lc, record, writes)
        # The concurrent step during the settle window was refused.
        child = concurrent["child"]
        self.assertTrue(process_fixtures.wait_until(lambda: "exited_at" in concurrent, timeout=30))
        self.assertEqual(child.returncode, cli.EXIT_WORKER_ACTIVE, child.stderr.read())
        ending_at = next(t for t, w in writes if (w.get("worker_state") or {}).get("state") == ENDING)
        self.assertLess(concurrent["exited_at"], ending_at)
        self.assertEqual(self.processes(lc), 1)

    def test_a_wrong_match_fails_the_self_reviewing_job_closed(self) -> None:
        lc = self.at_self_reviewing("r14b-self")
        result, writes = self._run(lc, MILESTONE_IMPLEMENT, self._turns(
            [{"step": "actions", "actions": lc.generate()}], [{"step": "text", "text": "task done"}]))
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        self.assertEqual(evidence.committed_work_item(lc.root, WI, "HEAD")["phase"], AWAITING_LOCAL)
        diagnosis = self.assert_worker_outcome_failure(record, "AMBIGUOUS", "command_lifecycle_irregular")
        self.assertEqual([a["kind"] for a in diagnosis["command_lifecycle_anomalies"]], ["unmatched_bracket"])
        self._assert_owned_until_the_real_fire(lc, record, writes)

    def test_a_stop_inside_the_spurious_bracket_fails_on_the_count(self) -> None:
        lc = self.seed("r14b-stop", IMPLEMENTING)
        result, _writes = self._run(lc, MILESTONE_IMPLEMENT, self._turns(
            [{"step": "actions", "actions": lc.implement("CP1")}], [{"step": "wakeup_stop"}]))
        self.assertEqual(result.code, cli.EXIT_WORKER_FAILED, result.stderr)
        [record] = result.records
        diagnosis = self.assert_worker_outcome_failure(record, "AMBIGUOUS", "command_lifecycle_irregular")
        self.assertIn("wakeup_count_mismatch", [a["kind"] for a in diagnosis["command_lifecycle_anomalies"]])
        [stop] = diagnosis["wakeup_stops"]
        self.assertEqual((stop["cancelled_wakeups"], stop["expected_count"]), (1, 0))


_CHILD_WAITING_STEP = _CHILD_STEP


class LifecycleLockLifetimeTest(_WaitingWorkerCase):
    """The lifecycle lock is held for the whole owned lifetime: while the
    job is ``WAITING``, and after its Controller is ``SIGKILL``ed mid-wait,
    when the recorded anchor is what holds it."""

    def test_the_lock_is_held_while_waiting_and_by_the_anchor_after_the_controller_dies(self) -> None:
        lc = self.seed("lock-lifetime", IMPLEMENTING)
        lc.runtime.mkdir(parents=True, exist_ok=True)
        release = lc.case_dir / "release"
        turns = [[verification(lc.case_dir / "done", release=release), SAYS_IT_WILL_CONTINUE],
                 [{"step": "text", "text": "done"}]]
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_WAITING_STEP, str(fixtures.REPO_ROOT), str(lc.root), str(lc.runtime),
             str(FAKE_CLAUDE)],
            env={**os.environ, "FAKE_CLAUDE_TURNS": json.dumps(turns),
                 "FAKE_CLAUDE_INVOCATIONS_FILE": str(lc.processes_file)},
        )
        self.addCleanup(release.touch)
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait()))

        def waiting_record() -> dict | None:
            for path in (lc.runtime / "jobs").glob("*.json"):
                with contextlib.suppress(OSError, ValueError):
                    record = json.loads(path.read_text())
                    if (record.get("worker_state") or {}).get("state") == WAITING:
                        return record
            return None

        self.assertTrue(process_fixtures.wait_until(lambda: waiting_record() is not None, timeout=30),
                        "the job never reached WAITING")
        record = waiting_record()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        self.assertEqual(lock.probe_lifecycle_lock(lc.root), lock.HELD)

        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
        self.assertEqual(lock.probe_lifecycle_lock(lc.root), lock.HELD)
        # fuser-style evidence: the recorded anchor holds the git directory open.
        anchor_pid = record["worker_anchor"]["pid"]
        git_dir = str(lock.resolve_git_dir(lc.root))
        targets = set()
        for name in os.listdir(f"/proc/{anchor_pid}/fd"):
            with contextlib.suppress(OSError):
                targets.add(os.readlink(f"/proc/{anchor_pid}/fd/{name}"))
        self.assertIn(git_dir, targets)
        with self.assertRaises(LifecycleWorkerActiveError):
            lock.acquire_lifecycle_lock(lc.root)


if __name__ == "__main__":
    unittest.main()
