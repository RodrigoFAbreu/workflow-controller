"""Both modes end to end (workflow-controller-orchestration-protocol-v1 CP6,
plan K2): a disposable managed repository is driven through its whole
lifecycle by the real ``job.execute_step`` -- plan review, plan approval,
implementation, self-review, bundle generation, both implementation-review
stages, technical approval, the functional-review checklist and acceptance --
until the work item is ``MILESTONE_COMPLETE``.

The same lifecycle runs on the vendored 2.7.0 in protocol mode (the
Workflow's own ``next-action`` decides and its ``reconcile`` verifies) and on
the vendored 2.6.0 in legacy mode (1.6.0's ``evidence.decide`` and
verification). The fake worker is a ``worker.launch`` double whose effect is
the observable effect of the Workflow command it was launched for, made
through the target's own installed writers and generator, in that command's
own commit order. The user-only steps (plan approval, technical approval,
acceptance) are performed by the test, as the user's own commands would.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, forge as forge_mod, gitrepo, job, managed_repo, milestone_branch, protocol, worker  # noqa: E402
from controller import runtime as controller_runtime  # noqa: E402
from controller.errors import WorkflowReleaseChangedError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402

WID = "demo"
STATE_REL = "docs/ai-workflow/WORKFLOW_STATE.json"

_PLAN_REVIEW = """
import datetime
root = Path.cwd()
wid, stage, verdict = sys.argv[1], sys.argv[2], sys.argv[3]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
bundle_dir = root / fingerprint.resolve_bundle_dir(root, wid, stage=stage)
bundle_id = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["bundle_id"]
if stage == "plan":
    rcid, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
else:
    rcid = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["review_content_id"]
if stage == "implementation":
    role = "LOCAL_MODEL_IMPLEMENTATION_REVIEW" if sys.argv[4] == "local" else "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
    base = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]["base_commit"]
    feedback_dir = root / fingerprint.resolve_feedback_dir(root, wid)
    feedback_dir.mkdir(parents=True, exist_ok=True)
    (feedback_dir / "REVIEW_FEEDBACK.md").write_text(
        f"# Review Decision\\n\\nStatus: {verdict}\\nReviewer role: {role}\\nReviewed bundle ID: {bundle_id}\\n"
        f"Reviewed base commit: {base}\\nWork item: {wid}\\nReviewed review content ID: {rcid}\\n"
        f"Reviewed review_content_id: {rcid}\\n\\n## Blocking findings\\n\\nNone.\\n")
if sys.argv[4] == "local":
    writer = ws.record_local_plan_review if stage == "plan" else ws.record_local_implementation_review
    ws.state_transaction(root, lambda s: writer(
        s, wid, verdict=verdict, bundle_id=bundle_id, review_content_id=rcid, round=1, now=now))
else:
    writer = ws.record_manual_plan_review if stage == "plan" else ws.record_manual_implementation_review
    ws.state_transaction(root, lambda s: writer(
        s, wid, verdict=verdict, bundle_id=bundle_id, round=1, now=now, current_review_content_id=rcid,
        feedback_role=f"MANUAL_EXTERNAL_{stage.upper()}_REVIEW", feedback_review_content_id=rcid))
"""

_APPROVE_PLAN = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
git("add", "-A")
git("commit", "-q", "--allow-empty", "-m", "Publish the plan")
state = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
base = state["work_items"][wid]["base_commit"]
digest, projection = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
    root, wid, "HEAD", base=base)
record = ws.build_approval_record(
    basis="EXTERNAL_APPROVE", stage="plan", user_confirmation=f"I approve {wid} at the plan stage", now=now,
    reviewed_bundle_id="b" * 64, approved_review_content_id=digest,
    review_content_manifest=projection["review_content_manifest"])
ws.state_transaction(root, lambda s: ws.apply_plan_approval(s, wid, record, now))
git("add", "-A")
git("commit", "-q", "-m", f"approve the plan\\n\\nWorkflow-Plan-Approval: {digest}\\nWorkflow-Work-Item: {wid}\\n")
"""

_POLICY_APPROVE_PLAN = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
box = {}
def mutate(state):
    box["record"] = ws.build_policy_approval_record(root, state, wid, "plan", now=now)
    return ws.apply_plan_approval(state, wid, box["record"], now)
ws.state_transaction(root, mutate)
record = box["record"]
git("add", "-A")
git("commit", "-q", "-m", "approve the plan by policy\\n\\nWorkflow-Plan-Approval: " + record["approved_review_content_id"]
    + "\\nWorkflow-Work-Item: " + wid + "\\nWorkflow-Gate-Satisfied-By: " + ws.gate_satisfied_by_trailer(record) + "\\n")
"""

_POLICY_TECHNICAL_APPROVAL = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
box = {}
def mutate(state):
    box["record"] = ws.build_policy_approval_record(root, state, wid, "implementation", now=now)
    return ws.apply_technical_approval(state, wid, box["record"], now)
ws.state_transaction(root, mutate)
record = box["record"]
git("add", "-A")
git("commit", "-q", "-m", "approve the implementation by policy\\n\\nWorkflow-Technical-Approval: "
    + record["approved_review_content_id"] + "\\nWorkflow-Work-Item: " + wid
    + "\\nWorkflow-Gate-Satisfied-By: " + ws.gate_satisfied_by_trailer(record) + "\\n")
"""

_CHECKPOINT = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
registry = json.loads((root / f"docs/ai-workflow/registry/{wid}-registry.json").read_text())
state = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
item = state["work_items"][wid]
cid = ws.select_next_checkpoint(item, registry)
head = git("rev-parse", "HEAD")
ws.state_transaction(root, lambda s: ws.transition_checkpoint_in_progress(s, wid, cid, start_commit=head, now=now))
(root / "app").mkdir(exist_ok=True)
(root / "app" / f"{cid}.txt").write_text(f"{cid} implemented\\n")
ws.state_transaction(root, lambda s: ws.complete_checkpoint(s, wid, cid, registry, now=now, repo_root=root))
git("add", "-A")
git("commit", "-q", "-m", f"Implement {cid}\\n\\nWorkflow-Checkpoint: {cid}\\nWorkflow-Work-Item: {wid}\\n")
"""

_ENTER_SELF_REVIEW = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
registry = json.loads((root / f"docs/ai-workflow/registry/{wid}-registry.json").read_text())
before = (root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text()
ws.state_transaction(root, lambda s: ws.enter_self_reviewing_implementation(s, wid, registry, now=now))
if (root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text() != before:
    subprocess.run(["git", "add", "docs/ai-workflow/WORKFLOW_STATE.json"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"Enter SELF_REVIEWING_IMPLEMENTATION\\n\\nWorkflow-Work-Item: {wid}\\n"],
                   cwd=root, check=True)
"""

_GENERATE_BUNDLE = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
ws.state_transaction(root, lambda s: ws.record_bundle_generation(s, wid, stage="implementation", head=git("rev-parse", "HEAD"), now=now))
state = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
item = state["work_items"][wid]
revision = item["implementation_revision"]
git("add", "docs/ai-workflow/WORKFLOW_STATE.json")
git("commit", "-q", "-m", f"Record implementation bundle generation\\n\\nWorkflow-Bundle-Generation-Record: {wid}/{revision}\\nWorkflow-Work-Item: {wid}\\n")
digest = ws.approval_review_content_id(
    root, stage="implementation", base_commit=item["base_commit"], work_item_type=item["work_item_type"],
    work_item_id=wid, head="HEAD", artifacts_path=fingerprint.artifacts_path_for_work_item(wid))
bundle_dir = root / ".ai-review" / wid / "current"
bundle_dir.mkdir(parents=True, exist_ok=True)
(bundle_dir / "REVIEW_REQUEST.md").write_text(f"# Review request\\n\\nstage: implementation\\nreview_content_id: {digest}\\n")
(bundle_dir / "IMPLEMENTATION_SUMMARY.md").write_text(f"implementation_revision: {revision}\\n\\nbuilt\\n")
(bundle_dir / "TEST_RESULTS.md").write_text("all checks passed\\n")
(bundle_dir / "CONTEXT_FILES.txt").write_text("README.md\\n")
subprocess.run(["bash", "scripts/prepare-ai-review.sh", item["base_commit"], "implementation", wid],
               cwd=root, check=True, capture_output=True, text=True)
"""

_TECHNICAL_APPROVAL = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
state = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
item = state["work_items"][wid]
bundle_dir = root / fingerprint.resolve_bundle_dir(root, wid, stage="implementation")
manifest = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")
record = ws.build_approval_record(
    basis="EXTERNAL_APPROVE", stage="implementation", user_confirmation=f"I approve {wid} at the implementation stage",
    now=now, reviewed_bundle_id=manifest["bundle_id"], approved_review_content_id=manifest["review_content_id"],
    review_content_manifest=[], reviewed_content_commit=item["reviewed_implementation_head"])
ws.state_transaction(root, lambda s: ws.apply_technical_approval(s, wid, record, now))
git("add", "-A")
git("commit", "-q", "-m", f"approve the implementation\\n\\nWorkflow-Technical-Approval: {manifest['review_content_id']}\\nWorkflow-Work-Item: {wid}\\n")
"""

_FUNCTIONAL_CHECKLIST = """
import subprocess
root = Path.cwd()
wid = sys.argv[1]
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
state = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
revision = state["work_items"][wid]["implementation_revision"]
path = root / "docs" / "ACTIVE_MILESTONE.md"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("# Active milestone\\n\\n## Functional review checklist\\n\\n1. Exercise the feature.\\n")
blob = git("hash-object", "docs/ACTIVE_MILESTONE.md")
git("add", "docs/ACTIVE_MILESTONE.md")
git("commit", "-q", "-m", f"Functional review checklist\\n\\nWorkflow-Functional-Checklist: {wid}/{revision}/{blob}\\nWorkflow-Work-Item: {wid}\\n")
fingerprint.ensure_feedback_dir(root, wid)
"""

_ACCEPT = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ws.state_transaction(root, lambda s: ws.complete_work_item(s, wid, now, repo_root=root))
subprocess.run(["git", "add", "-A"], cwd=root, check=True)
subprocess.run(["git", "commit", "-q", "-m", f"Accept the milestone\\n\\nWorkflow-Work-Item: {wid}\\n"], cwd=root, check=True)
"""

#: The task's command token -> the effect of that Workflow command.
_EFFECTS = {
    "review-plan": ("plan", "APPROVE", "local"),
    "review-implementation": ("implementation", "APPROVE", "local"),
}


def _result(report: str = "ok") -> worker.WorkerResult:
    return worker.WorkerResult(
        outcome="SUCCESS", returncode=0, session_id="s", is_error=False, subtype="success",
        terminal_reason=None, stop_reason=None, result=report, num_turns=1, permission_denials=[],
        total_cost_usd=0.0, duration_ms=1, stdout="", stderr="", raw_json={})


class Lifecycle:
    """A disposable ``release`` target seeded at a bound plan bundle, and the
    fake worker that performs the effect of each launched command."""

    #: What the fake worker says at the end of a step.
    report = "ok"

    def __init__(self, test: unittest.TestCase, release: str) -> None:
        self.test = test
        self.release = release
        self.td = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: shutil.rmtree(self.td, ignore_errors=True))
        self.root = self.td / "repo"
        fixtures.seed_workflow_item(self.root, release, "ready", work_item_id=WID)
        self.stub = fixtures.write_stub_workflow_manager(self.td / "workflow-manager", release=release)
        self.runtime = self.td / "runtime"
        self.runtime.mkdir()
        self.tasks: list[str] = []

    #: The instance attributes a snapshot carries with the tree: what the drive launched, answered or recorded.
    SNAPSHOT_ATTRS: tuple[str, ...] = ("tasks",)

    def snapshot_inputs(self) -> tuple:
        """Everything set before a drive that the tree it builds depends on (a key part, so that two drives that
        differ in any of it never share a snapshot)."""
        return (self.release, self.report)

    def snapshot_state(self) -> dict:
        return {attr: getattr(self, attr) for attr in self.SNAPSHOT_ATTRS}

    def restore_state(self, state: dict) -> None:
        for attr, value in state.items():
            setattr(self, attr, value)

    def rebase(self, td: Path) -> None:
        """Point every path this life holds at ``td``, the tree a snapshot was copied back to."""
        self.td = td
        self.root = td / "repo"
        self.stub = td / "workflow-manager"
        self.runtime = td / "runtime"

    def reach(self, name: str, drive) -> None:
        """Bring this life to the row ``name`` by ``drive()`` -- once per test process. The first life to ask for
        ``(name, snapshot_inputs)`` drives for real and its tree is kept; every later one takes a copy of that tree,
        at the path it was built at (a bundle records its worktree path, so the tree cannot move), instead of
        driving again (D3). Falls back to driving when no copy can be made, and always drives under the scenario
        kit, whose isolation boundary this does not change."""
        key = (name, self.snapshot_inputs())
        kit = getattr(self.test, "stub_dir", None) is not None
        restored = None if kit else fixtures.restore_snapshot(key)
        if restored is not None:
            td, state = restored
            shutil.rmtree(self.td, ignore_errors=True)  # the tree this life seeded and no longer needs
            self.rebase(td)  # the cleanup registered at construction removes whatever ``self.td`` is then
            self.restore_state(state)
            return
        drive()
        if not kit:
            fixtures.take_snapshot(key, self.td, self.snapshot_state())

    def inspect(self):
        return managed_repo.inspect(self.root, manager_bin=str(self.stub))

    def workflow(self, code: str, *args: str) -> None:
        fixtures.run_workflow_python(self.root, "import json\n" + code, *args)

    def phase(self) -> str:
        return fixtures.state_entry(self.root, WID)["phase"]

    def effect(self, task: str) -> None:
        token = task.split()[0].lstrip("/")
        self.tasks.append(token)
        if token in _EFFECTS:
            stage, verdict, how = _EFFECTS[token]
            self.workflow(_PLAN_REVIEW, WID, stage, verdict, how)
        elif token == "milestone-implement":
            if self.phase() == "SELF_REVIEWING_IMPLEMENTATION":
                self.workflow(_GENERATE_BUNDLE, WID)
            else:
                self.workflow(_CHECKPOINT, WID)
        elif token == "prepare-functional-review":
            self.workflow(_FUNCTIONAL_CHECKLIST, WID)
        elif token == "milestone-plan":
            pass  # the next milestone's planning: only the launch is observed
        else:
            raise AssertionError(f"no scripted effect for {task!r}")

    def step(self) -> dict:
        def launch(task, *, cwd, **kwargs):
            self.effect(task)
            return _result(self.report)

        with mock.patch.object(worker, "launch", launch):
            return job.execute_step(self.inspect(), identity=IDENTITY, runtime=self.runtime)

    def run_until(self, phase: str, limit: int = 12) -> list[dict]:
        records: list[dict] = []
        for _ in range(limit):
            if self.phase() == phase:
                return records
            record = self.step()
            records.append(record)
            self.test.assertEqual(record["status"], job.STATUS_FINISHED, record)
        self.test.fail(f"never reached {phase}; at {self.phase()}; tasks {self.tasks}")

    def user_step(self, code: str, *args: str) -> None:
        self.workflow(code, WID, *args)

    def manual_gate(self, phase: str, stage: str) -> None:
        """The external-review gate: the Controller launches nothing, and
        the user's pasted verdict is recorded (``/record-manual-*-review``)."""
        self.run_until(phase)
        launched = len(self.tasks)
        record = self.step()
        self.test.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.test.assertEqual(len(self.tasks), launched)
        self.user_step(_PLAN_REVIEW, stage, "APPROVE", "manual")


#: The commands the Controller launches, in order, from a bound plan bundle
#: to the next milestone's planning -- the same in both modes.
EXPECTED_LAUNCHES = [
    "review-plan", "milestone-implement", "milestone-implement", "review-implementation",
    "prepare-functional-review", "milestone-plan",
]


class _LifecycleCase(unittest.TestCase):
    release = ""
    #: ``functional.prepare`` is automatic only under the protocol (D6): 1.6.0
    #: gates there and the user prepares the checklist.
    controller_prepares_checklist = True

    def drive(self, life: Lifecycle | None = None, *, past_plan_gate: bool = False) -> Lifecycle:
        life = life or Lifecycle(self, self.release)
        if not past_plan_gate:
            life.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        life.run_until("AWAITING_PLAN_APPROVAL")
        life.user_step(_APPROVE_PLAN)
        life.manual_gate("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation")
        life.run_until("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        life.user_step(_TECHNICAL_APPROVAL)
        self.assertEqual(life.phase(), "AWAITING_FUNCTIONAL_REVIEW")
        launched = len(life.tasks)
        if self.controller_prepares_checklist:
            self.assertEqual(life.step()["status"], job.STATUS_FINISHED)
            self.assertEqual(life.tasks[launched:], ["prepare-functional-review"])
        else:
            self.assertEqual(life.step()["status"], job.STATUS_GATE_BLOCKED)
            self.assertEqual(life.tasks[launched:], [])
            life.user_step(_FUNCTIONAL_CHECKLIST)
        launched = len(life.tasks)
        self.assertEqual(life.step()["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(len(life.tasks), launched)
        life.user_step(_ACCEPT)
        self.assertEqual(life.phase(), "MILESTONE_COMPLETE")
        return life

    def records(self, life: Lifecycle) -> list[dict]:
        return [json.loads(path.read_text()) for path in sorted((life.runtime / "jobs").glob("*.json"))]

    def launched(self, life: Lifecycle) -> list[dict]:
        return [record for record in self.records(life) if record["status"] == job.STATUS_FINISHED]


class ProtocolModeLifecycleTest(_LifecycleCase):
    release = "2.7.0"

    def test_a_2_7_0_repository_runs_to_complete_in_protocol_mode(self) -> None:
        life = self.drive()
        self.assertTrue(life.inspect().target_protocol)
        launched = self.launched(life)
        for record in self.records(life):
            self.assertEqual(record["target_workflow_version"], "2.7.0")
        self.assertTrue(all("protocol" in record and record["transition_verified"] for record in launched))
        self.assertEqual([r["protocol"]["action_id"] for r in launched], [
            "plan.review.local", "implementation.checkpoint", "implementation.self_review",
            "implementation.review.local", "functional.prepare"])
        self.assertEqual([r["protocol"]["reconcile"]["class"] for r in launched],
                         ["gate_reached", "progress", "progress", "gate_reached", "gate_reached"])
        self.assertEqual(life.tasks, EXPECTED_LAUNCHES[:-1])
        # The Workflow itself calls the item done, and the next step plans the next milestone.
        self.assertEqual(protocol.next_action(life.root, WID).disposition, "complete")
        life.step()
        self.assertEqual(life.tasks, EXPECTED_LAUNCHES)


class LegacyModeLifecycleTest(_LifecycleCase):
    release = "2.6.0"
    controller_prepares_checklist = False

    def test_a_2_6_0_repository_runs_to_the_same_end_in_legacy_mode(self) -> None:
        life = self.drive()
        self.assertFalse(life.inspect().target_protocol)
        for record in self.records(life):
            self.assertEqual(record["target_workflow_version"], "2.6.0")
            self.assertNotIn("protocol", record)
        self.assertTrue(all(record["transition_verified"] for record in self.launched(life)))
        life.step()
        # The protocol's launches, less the checklist the user prepared (difference D6).
        self.assertEqual(life.tasks, [t for t in EXPECTED_LAUNCHES if t != "prepare-functional-review"])


class ReleaseMovedBetweenStepsTest(_LifecycleCase):
    """A repository on 2.6.0 that Workflow Manager updates to 2.7.0 between
    two steps: the step already admitted under 2.6.0 refuses, nothing is
    decided under the old contract, and the next run is in protocol mode."""

    release = "2.6.0"

    def update_to_2_7_0(self, life: Lifecycle) -> None:
        fixtures.install_workflow_release(life.root, "2.7.0")
        paths = sorted(fixtures.workflow_release_files("2.7.0")) + [".workflow-manager/installation.json"]
        fixtures.run(["git", "add", "-f", "--", *paths], cwd=life.root)
        fixtures.run(["git", "commit", "-q", "-m", "Update Workflow to 2.7.0"], cwd=life.root)
        life.stub = fixtures.write_stub_workflow_manager(life.td / "workflow-manager-2-7-0", release="2.7.0")

    def test_the_stale_step_is_refused_and_the_fresh_run_finishes_in_protocol_mode(self) -> None:
        life = Lifecycle(self, "2.6.0")
        life.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        first = len(self.records(life))
        admitted = life.inspect()
        self.assertFalse(admitted.target_protocol)
        self.update_to_2_7_0(life)
        with self.assertRaises(WorkflowReleaseChangedError) as caught:
            with mock.patch.object(worker, "launch", side_effect=AssertionError("a worker was launched")):
                job.execute_step(admitted, identity=IDENTITY, runtime=life.runtime)
        self.assertEqual((caught.exception.evidence["admitted"], caught.exception.evidence["installed"]),
                         ("2.6.0", "2.7.0"))
        self.assertEqual(len(self.records(life)), first)
        self.assertTrue(life.inspect().target_protocol)
        self.drive(life, past_plan_gate=True)
        records = self.records(life)
        self.assertNotIn("protocol", records[0])
        self.assertEqual(records[0]["target_workflow_version"], "2.6.0")
        later = [r for r in records[first:] if r["status"] == job.STATUS_FINISHED]
        self.assertTrue(later)
        self.assertTrue(all("protocol" in r and r["target_workflow_version"] == "2.7.0" for r in later))
        life.step()
        self.assertEqual(life.tasks, EXPECTED_LAUNCHES)


# -- gate-policy CP2: the three validations under automatic gates (workflow-controller-gate-policy D5, D8) ---------------


def _audited_review() -> str:
    """``_PLAN_REVIEW`` with the audit a stage records while its gate is automatic: a ``verdict_sha256`` and a declared
    ``Reviewer model`` of a different family per stage (``distinct_reviewer_models``), and the plan stage's feedback
    file written as the implementation stage's is."""
    script = _PLAN_REVIEW
    replacements = (
        ('if stage == "implementation":\n    role = "LOCAL_MODEL_IMPLEMENTATION_REVIEW" if sys.argv[4] == "local" '
         'else "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"',
         'if True:\n    role = ("LOCAL_MODEL_" if sys.argv[4] == "local" else "MANUAL_EXTERNAL_") + stage.upper() + "_REVIEW"'),
        ('review_content_id=rcid, round=1, now=now))',
         'review_content_id=rcid, round=1, now=now,\n        audit={"verdict_sha256": "a" * 64, "run_ref": None, "reviewer_model": "anthropic/claude-opus-5-5"}))'),
        ('feedback_review_content_id=rcid))',
         'feedback_review_content_id=rcid,\n        audit={"verdict_sha256": "b" * 64, "run_ref": None, "reviewer_model": "openai/gpt-6.1-sol"}))'),
    )
    for old, new in replacements:
        assert old in script, old
        script = script.replace(old, new)
    return script


_AUDITED_REVIEW = _audited_review()

#: The effect of ``/satisfy-gate acceptance``: the Workflow's own ``satisfy_acceptance_gate``, with its ``gh`` query
#: answered by a scripted pull request (``argv[1]`` is the checks file: ``green`` or anything else for pending). It
#: reaches no forge; the fixture's ``origin`` is only a github.com URL for the repository name.
_SATISFY_ACCEPTANCE = """
import datetime, json
root = Path.cwd()
wid, checks_file = sys.argv[1], sys.argv[2]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def run(argv, timeout):
    commit = argv[argv.index("--search") + 1]
    green = Path(checks_file).read_text().strip() == "green"
    rollup = [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}] if green \\
        else [{"name": "ci", "status": "IN_PROGRESS", "conclusion": ""}]
    return json.dumps([{"number": 7, "url": "https://github.com/owner/repo/pull/7", "state": "OPEN",
                        "headRefOid": commit, "reviewDecision": "", "reviews": [], "statusCheckRollup": rollup}])

try:
    ws.satisfy_acceptance_gate(root, wid, now=now, run=run, resolve=lambda r: {"path": "gh", "sha256": "0" * 64})
    print("satisfied")
except ws.GateNotSatisfiableError as exc:
    print("refused:", exc)
"""

#: An orchestrator's ``functional_evidence`` result (what C10c's automated functional review will report): one passing flow
#: at the current head. Row 38e asks for it under an automatic acceptance gate.
_FUNCTIONAL_EVIDENCE = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
ws.record_functional_evidence(root, wid, {
    "flow_id": "flow-1", "status": "passed", "head": head, "ran_at": now, "summary": {"message": "passed"},
    "log_digest": "c" * 64, "run_ref": None, "reporter": "lifecycle-test/1"}, now=now)
"""

_REPORT_PR = """
import datetime, hashlib, json, subprocess, tempfile
root = Path.cwd()
wid, checks_file = sys.argv[1], sys.argv[2]
state = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
commit = ws.gate_policy.anchor_of(root, wid, state["work_items"][wid])["commit"]
green = Path(checks_file).read_text().strip() == "green"
rollup = [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}] if green \\
    else [{"name": "ci", "status": "IN_PROGRESS", "conclusion": ""}]
raw = json.dumps([{"number": 7, "url": "https://github.com/owner/repo/pull/7", "state": "OPEN", "headRefOid": commit,
                   "reviewDecision": "", "reviews": [], "statusCheckRollup": rollup}])
payload = {"forge": {"source": "github", "query": "pr-list-v1", "raw": raw, "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                     "repository": "owner/repo", "queried_commit": commit,
                     "fetched_by": {"name": "orchestrator", "version": "1"}, "fetched_at": "2026-10-17T00:00:00Z"}}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
    json.dump(payload, handle)
done = subprocess.run([sys.executable, "scripts/workflow_protocol.py", "--repo-root", str(root), "record-external-result",
                       "--work-item", wid, "--kind", "pr_review_result", "--input", handle.name],
                      capture_output=True, text=True)
print(done.stdout, done.stderr)
assert json.loads(done.stdout)["ok"], done.stdout
"""


class GateLifecycle(Lifecycle):
    """A ``Lifecycle`` on Workflow 2.12.0 whose default gate policy is automatic: the fake worker also performs the
    effect of ``/satisfy-gate``, and the reviews are recorded with the audit the automatic gate requires."""

    def __init__(self, test: unittest.TestCase) -> None:
        super().__init__(test, "2.12.0")
        fixtures.run(["git", "remote", "add", "origin", "https://github.com/owner/repo.git"], cwd=self.root)
        self.checks = self.td / "checks"
        self.checks.write_text("green")
        #: Stages whose ``/satisfy-gate`` the fake validator refuses (it writes nothing).
        self.refused: set[str] = set()
        self.satisfied: list[str] = []
        #: The protocol row the Workflow answered before each step.
        self.rows: list[str] = []

    def step(self) -> dict:
        self.rows.append(protocol.next_action(self.root, WID).raw["row"])
        return super().step()

    SNAPSHOT_ATTRS = ("tasks", "rows", "satisfied")

    def snapshot_inputs(self) -> tuple:
        return (*super().snapshot_inputs(), self.checks.read_text(), tuple(sorted(self.refused)))

    def rebase(self, td: Path) -> None:
        super().rebase(td)
        self.checks = td / "checks"

    def explain(self) -> dict:
        """``explain --json`` for the repository, as an operator would read it."""
        out = io.StringIO()
        argv = ["--json", "--runtime-dir", str(self.runtime), "--workflow-manager", str(self.stub), "explain",
                str(self.root)]
        with contextlib.redirect_stdout(out):
            self.test.assertEqual(cli.main(argv), cli.EXIT_OK)
        return json.loads(out.getvalue())

    def explain_text(self) -> str:
        """``explain`` (text) for the repository, as an operator would read it."""
        out = io.StringIO()
        argv = ["--runtime-dir", str(self.runtime), "--workflow-manager", str(self.stub), "explain", str(self.root)]
        with contextlib.redirect_stdout(out):
            self.test.assertEqual(cli.main(argv), cli.EXIT_OK)
        return out.getvalue()

    def effect(self, task: str) -> None:
        words = task.lstrip("/").split()
        if words[0] == "satisfy-gate":
            self.tasks.append("satisfy-gate " + words[1])
            if words[1] in self.refused:
                return
            self.satisfied.append(words[1])
            if words[1] == "plan":
                self.workflow(_POLICY_APPROVE_PLAN, WID)
            elif words[1] == "implementation":
                self.workflow(_POLICY_TECHNICAL_APPROVAL, WID)
            else:
                self.workflow(_SATISFY_ACCEPTANCE, WID, str(self.checks))
            return
        if words[0] == "apply-pr-review":
            # The command's own effect (a refreshed fact, a reopen) is CP3's scenarios; here only the launch is observed.
            self.tasks.append(words[0])
            return
        if words[0] in _EFFECTS:
            self.tasks.append(words[0])
            stage, verdict, how = _EFFECTS[words[0]]
            self.workflow(_AUDITED_REVIEW, WID, stage, verdict, how)
            return
        super().effect(task)

    def manual_gate(self, phase: str, stage: str) -> None:
        self.run_until(phase)
        launched = len(self.tasks)
        record = self.step()
        self.test.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.test.assertEqual(len(self.tasks), launched)
        self.user_step(_AUDITED_REVIEW, stage, "APPROVE", "manual")

    def to_plan_gate(self) -> None:
        """The first manual gate passed: the plan reviewed, the next step is the validator's."""
        self.reach("to_plan_gate", lambda: self.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan"))

    def to_acceptance(self) -> None:
        """Plan to the functional checklist, every gate passed by the validator."""
        self.reach("to_acceptance", self._to_acceptance)

    def _to_acceptance(self) -> None:
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        self.run_until("IMPLEMENTING")
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation")
        self.run_until("AWAITING_FUNCTIONAL_REVIEW")
        self.test.assertEqual(self.step()["status"], job.STATUS_FINISHED)  # functional.prepare
        self.user_step(_FUNCTIONAL_EVIDENCE)


class SatisfyGateLifecycleTest(unittest.TestCase):
    """gate-policy CP2 (D5, D8): the vendored 2.12.0's own ``next-action`` answers rows 14a, 28a and 38h and the
    Controller launches ``/satisfy-gate`` for each; a refused validation stops the run."""

    def notifications(self, records: list[dict]) -> list[str]:
        return [event.event_type for record in records for event in job.derive_job_event(record)]

    def test_a_run_passes_plan_technical_approval_and_acceptance_by_policy_without_a_stop(self) -> None:
        life = GateLifecycle(self)
        life.to_acceptance()
        self.assertEqual(protocol.next_action(life.root, WID).raw["row"], "38h")
        self.assertEqual(life.step()["status"], job.STATUS_FINISHED)  # acceptance.satisfy
        self.assertEqual(life.phase(), "MILESTONE_COMPLETE")
        self.assertEqual(life.tasks, [
            "review-plan", "satisfy-gate plan", "milestone-implement", "milestone-implement", "review-implementation",
            "satisfy-gate implementation", "prepare-functional-review", "satisfy-gate acceptance"])
        records = [r for r in self.records(life) if r["status"] == job.STATUS_FINISHED]
        self.assertEqual([r["protocol"]["action_id"] for r in records if "satisfy" in r["protocol"]["action_id"]],
                         ["plan.satisfy", "implementation.satisfy", "acceptance.satisfy"])
        self.assertTrue(all(r["transition_verified"] for r in records))
        events = self.notifications(records)
        self.assertEqual(events.count("approval_recorded"), 3)
        self.assertEqual(events.count("milestone_accepted"), 1)
        item = fixtures.state_entry(life.root, WID)
        self.assertIn("acceptance_satisfaction", item)
        # The approvals are the vendored ``build_policy_approval_record``'s, not a human's: basis and evidence.
        for field in ("plan_approval", "technical_approval"):
            approval = item[field]
            self.assertEqual(approval["basis"], "POLICY_SATISFIED", field)
            evidence = approval["policy_evidence"]
            self.assertEqual(approval["user_confirmation"], "policy:" + evidence["policy_digest"], field)
            self.assertTrue(evidence["requirements"] and all(r["met"] for r in evidence["requirements"]), field)
        self.assertTrue({"14a", "28a", "38h"} <= set(life.rows), life.rows)
        self.assertEqual(protocol.next_action(life.root, WID).disposition, "complete")

    UNMET = "distinct_reviewer_models: the reviewers are both openai/gpt-6.1-sol"

    def test_a_refused_validation_stops_the_run_after_two_launches_and_sends_no_approval(self) -> None:
        self.refused_validation_run(("Refused: plan_approval is not satisfiable by policy. " + "The policy was evaluated. " * 30
                                     + f"unmet: ['{self.UNMET}']. Nothing was written"))

    def test_a_refusal_requirement_survives_a_preamble_that_already_says_unmet(self) -> None:
        self.refused_validation_run("I checked the unmet requirements against the current review records. "
                                    + "The policy was evaluated. " * 30
                                    + f"Refused; unmet: ['{self.UNMET}']. Nothing was written")

    def test_a_refusal_requirement_survives_a_trailing_unmet_see_above(self) -> None:
        self.refused_validation_run("Refused: plan_approval is not satisfiable by policy. "
                                    + "The policy was evaluated. " * 30
                                    + f"The requirement {self.UNMET} does not hold. unmet: see above")

    def test_a_refusal_requirement_survives_an_early_unmet_followed_by_it_only_after_a_semicolon(self) -> None:
        self.refused_validation_run("Refused: unmet: see the policy; the details follow. "
                                    + "The policy was evaluated. " * 30
                                    + f"The requirement {self.UNMET} does not hold. Nothing was written")

    def test_a_refusal_requirement_survives_a_report_with_no_unmet_marker(self) -> None:
        self.refused_validation_run("Refused: plan_approval is not satisfiable by policy. "
                                    + "The policy was evaluated. " * 30
                                    + f"The requirement {self.UNMET} does not hold. Nothing was written")

    def refused_validation_run(self, report: str) -> None:
        life = GateLifecycle(self)
        life.to_plan_gate()
        life.refused = {"plan"}
        unmet = self.UNMET
        life.report = report
        self.assertGreater(life.report.index(unmet), 400)
        first = life.step()
        # After the first refusal the gate is still automatic, and explain already names the unmet requirement.
        self.assertTrue(life.explain()["automatic"])
        self.assertIn(unmet, life.explain()["last_worker_report"])
        self.assertIn(unmet, life.explain_text())
        second = life.step()
        # After the second refusal the loop guard applies: explain still names the requirement, in text and JSON.
        after_second = life.explain()
        self.assertFalse(after_second["automatic"])
        self.assertIn(unmet, after_second["gate"]["what_is_required"])
        self.assertIn(unmet, life.explain_text())
        for record in (first, second):
            self.assertEqual(record["status"], job.STATUS_FINISHED)
            self.assertEqual(record["protocol"]["progress"], "none")
            self.assertEqual(record["protocol"]["reconcile"]["class"], "no_progress")
            self.assertEqual(self.notifications([record]), ["step_done"])
        self.assertEqual(life.phase(), "AWAITING_PLAN_APPROVAL")
        self.assertEqual(life.rows[-2:], ["14a", "14a"])
        launched = len(life.tasks)
        third = life.step()
        self.assertEqual(third["status"], job.STATUS_GATE_BLOCKED)
        self.assertIn("no_progress_repeated", json.dumps(third["human_gate_pending"]))
        self.assertEqual(len(life.tasks), launched)
        self.assertEqual(life.tasks, ["review-plan", "satisfy-gate plan", "satisfy-gate plan"])
        explained = life.explain()
        self.assertFalse(explained["automatic"])
        self.assertIn("no_progress_repeated", explained["gate"]["what_is_required"])
        self.assertIn(unmet, explained["gate"]["what_is_required"])
        self.assertIn(unmet, life.explain_text())

    def pending_acceptance(self) -> GateLifecycle:
        """The acceptance stage reached with the pull request's checks still running: the validator is launched,
        is refused (the Workflow's query stores a pending fact) and the next answer is row 38f."""
        life = GateLifecycle(self)
        life.checks.write_text("pending")
        life.to_acceptance()
        refused = life.step()
        self.assertEqual(refused["status"], job.STATUS_FINISHED)
        # The refusal stored the pending fact, so the Workflow classes the step as reaching the next gate (row 38f).
        self.assertEqual(refused["protocol"]["reconcile"]["class"], "gate_reached")
        self.assertEqual(life.phase(), "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(self.notifications([refused]), ["step_done"])
        return life

    def test_pending_checks_end_at_row_38f_and_stay_there_until_a_person_accepts(self) -> None:
        life = self.pending_acceptance()
        launched = len(life.tasks)
        stopped = life.step()
        self.assertEqual(stopped["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(life.rows[-1], "38f")
        self.assertEqual(len(life.tasks), launched)
        self.assertEqual([e.event_type for e in job.derive_job_event(stopped)], ["user_gate"])
        # The checks turn green, but next-action never queries GitHub, so the answer does not change (D7).
        life.checks.write_text("green")
        self.assertEqual(protocol.next_action(life.root, WID).raw["row"], "38f")
        self.assertEqual(life.step()["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(len(life.tasks), launched)
        # The outside act: the person runs the Workflow's own acceptance once, outside ``run`` (no Controller worker;
        # the real command's roadmap, narrative and completion-commit writes are not reproduced here).
        life.user_step(_SATISFY_ACCEPTANCE, str(life.checks))
        self.assertEqual(life.phase(), "MILESTONE_COMPLETE")
        self.assertIn("acceptance_satisfaction", fixtures.state_entry(life.root, WID))
        self.assertEqual(protocol.next_action(life.root, WID).disposition, "complete")
        self.assertEqual(len(life.tasks), launched)

    def test_a_reported_pr_review_result_leads_to_the_launched_apply_review(self) -> None:
        life = self.pending_acceptance()
        self.assertEqual(life.step()["status"], job.STATUS_GATE_BLOCKED)
        life.checks.write_text("green")
        life.user_step(_REPORT_PR, str(life.checks))
        answer = protocol.next_action(life.root, WID)
        self.assertEqual(answer.raw["row"], "38d")
        self.assertEqual(answer.raw["action"]["id"], "pr.apply_review")
        self.assertEqual(answer.raw["action"]["invocation"], f"/apply-pr-review {WID}")
        launched = len(life.tasks)
        record = life.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(life.rows[-1], "38d")
        self.assertEqual(life.tasks[launched:], ["apply-pr-review"])
        self.assertEqual(record["protocol"]["action_id"], "pr.apply_review")
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["step_done"])

    def records(self, life: Lifecycle) -> list[dict]:
        return [json.loads(path.read_text()) for path in sorted((life.runtime / "jobs").glob("*.json"))]


# ---------------------------------------------------------------------------
# workflow-controller-pr-defect-loop CP3: the loop end to end on real answers.
# ---------------------------------------------------------------------------

def world_raw(world_file, commit):
    """The verbatim ``gh pr list`` stdout for the scripted pull request ``world_file`` describes (``checks``: green,
    pending, failed or empty; ``decision``: a review decision or empty). One function, two readers: the Controller's own
    query (:class:`PrWorld`) and the fake workers' Workflow-side seams (this source is prepended to their scripts)."""
    import json
    from pathlib import Path
    world = json.loads(Path(world_file).read_text())
    rollups = {
        "green": [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "pending": [{"name": "ci", "status": "IN_PROGRESS", "conclusion": ""}],
        "failed": [{"name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}],
        "empty": [],
    }
    decision = world.get("decision", "")
    reviews = [{"id": "review-1", "state": decision, "body": "Please look at the failing path.",
                "commit": {"oid": commit}}] if decision else []
    return json.dumps([{"number": 7, "url": "https://github.com/owner/repo/pull/7", "state": "OPEN",
                        "headRefOid": commit, "reviewDecision": decision, "reviews": reviews,
                        "statusCheckRollup": rollups[world["checks"]]}])


_WORLD_SOURCE = inspect.getsource(world_raw)

#: The effect of ``/satisfy-gate acceptance`` against the scripted pull request (the Workflow's own function, its ``gh``
#: query answered by ``world_raw``); it prints ``satisfied`` or ``refused: ...`` and reaches no forge.
_SATISFY_ACCEPTANCE_WORLD = _WORLD_SOURCE + """
import datetime
root = Path.cwd()
wid, world_file = sys.argv[1], sys.argv[2]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def run(argv, timeout):
    return world_raw(world_file, argv[argv.index("--search") + 1])

try:
    ws.satisfy_acceptance_gate(root, wid, now=now, run=run, resolve=lambda r: {"path": "gh", "sha256": "0" * 64})
    print("satisfied")
except ws.GateNotSatisfiableError as exc:
    print("refused:", exc)
"""

#: The effect of ``/apply-pr-review``: the command's first step, ``begin_pr_review``, with the Workflow's own query
#: answered by ``world_raw``. Prints the result's JSON. For a reopened item the branch the cause table then names (a code
#: fix, a bounded or broad review cycle) is the real command's work and is not reproduced: only its one consumption
#: write, ``mark_pr_key_applied``, is, so the cycle terminates; the fake worker then runs the bounded fix's regeneration.
_APPLY_PR_REVIEW_WORLD = _WORLD_SOURCE + """
import datetime
root = Path.cwd()
wid, world_file = sys.argv[1], sys.argv[2]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def run(argv, timeout):
    return world_raw(world_file, argv[argv.index("--search") + 1])

result = ws.begin_pr_review(root, wid, now=now, run=run, resolve=lambda r: {"path": "gh", "sha256": "0" * 64})
if result["result"] in ("reopened", "already_reopened"):
    ws.state_transaction(root, lambda s: ws.mark_pr_key_applied(s, wid, result["key"], now))
print(json.dumps(result))
"""

_ADOPT_ACCEPTANCE_POLICY = """
import json, subprocess
import workflow_gate_policy as g
acceptance = json.loads(sys.argv[1])
policy = {"schema_version": 1, "gates": {"acceptance": acceptance}}
Path("docs/ai-workflow/GATE_POLICY.json").write_text(json.dumps(policy, indent=2))
for args in (["add", "-A"], ["commit", "-q", "-m", "gate policy"]):
    subprocess.run(["git", *args], check=True, capture_output=True)
ws.adopt_gate_policy(Path.cwd(), confirmation="I adopt gate_policy " + g.policy_digest(policy)[:12],
                     now="2026-10-18T10:00:00Z")
"""


#: Tightens the adopted policy's acceptance gate (a tightening takes effect with its file committed, no adoption needed).
_TIGHTEN_ACCEPTANCE_HUMAN = """
import json, subprocess
acceptance = json.loads(sys.argv[1])
Path("docs/ai-workflow/GATE_POLICY.json").write_text(json.dumps({"schema_version": 1, "gates": {"acceptance": acceptance}}, indent=2))
for args in (["add", "-A"], ["commit", "-q", "-m", "gate policy: human acceptance"]):
    subprocess.run(["git", *args], check=True, capture_output=True)
"""


class PrWorld:
    """The scripted pull request of the scenarios, shared by the Controller's query and the fake workers."""

    def __init__(self, path: Path, checks: str = "pending", decision: str = "") -> None:
        self.path = path
        self.set(checks=checks, decision=decision)
        #: Every commit the Controller's own query was made for.
        self.queries: list[str] = []
        #: Called before each Controller query, to turn the pull request over time.
        self.on_query = None

    def set(self, *, checks: str | None = None, decision: str | None = None) -> None:
        current = json.loads(self.path.read_text()) if self.path.exists() else {"checks": "pending", "decision": ""}
        current.update({k: v for k, v in (("checks", checks), ("decision", decision)) if v is not None})
        self.path.write_text(json.dumps(current))

    # The Controller's ``GhForge`` seam.
    repository = "owner/repo"

    def query_pull_requests(self, commit: str) -> str:
        self.queries.append(commit)
        if self.on_query is not None:
            self.on_query(len(self.queries))
        return world_raw(self.path, commit)


class LoopLifecycle(GateLifecycle):
    """A ``GateLifecycle`` whose pull request is a :class:`PrWorld`: the fake validator and the fake apply worker read
    it through the vendored Workflow functions, the Controller's own query reads it through the forge seam, and the
    Controller steps run with the milestone-branch preflight satisfied and a binding that has a pull request."""

    def __init__(self, test: unittest.TestCase, *, checks: str = "pending", decision: str = "",
                 acceptance: dict | None = None, bind: bool = True) -> None:
        super().__init__(test)
        self.world = PrWorld(self.td / "world.json", checks, decision)
        #: The acceptance policy adopted once the functional checklist exists: a commit at any earlier point would
        #: leave the plan bundle generated at an older HEAD.
        self.acceptance = acceptance
        self.bound = bind
        if bind:
            self.bind()
        #: The Workflow's results for each launched ``/apply-pr-review``.
        self.applied: list[dict] = []
        self.sleeps: list[float] = []
        self.wait_seconds = 0

    def bind(self) -> None:
        key = milestone_branch.repo_key(gitrepo.common_dir(self.root))
        record = {"schema_version": milestone_branch.SCHEMA_VERSION, "state": sorted(milestone_branch.STATES)[0],
                  "work_item_id": WID, "branch": "milestone/demo", "trunk": "main", "binding_generation": 1,
                  "repository": {"forge_repository": "owner/repo"},
                  "pr": {"number": 7, "url": "https://github.com/owner/repo/pull/7"}}
        controller_runtime.write_json(self.runtime, milestone_branch.record_rel(key, WID), record)

    SNAPSHOT_ATTRS = (*GateLifecycle.SNAPSHOT_ATTRS, "applied", "sleeps")

    def snapshot_inputs(self) -> tuple:
        # The binding is in the runtime tree and the world file is beside it: both are what a drive starts from.
        return (*super().snapshot_inputs(), json.dumps(self.acceptance, sort_keys=True), self.world.path.read_text(),
                self.bound)

    def snapshot_state(self) -> dict:
        return {**super().snapshot_state(), "queries": self.world.queries}

    def restore_state(self, state: dict) -> None:
        state = dict(state)
        self.world.queries = state.pop("queries")
        super().restore_state(state)

    def rebase(self, td: Path) -> None:
        super().rebase(td)
        self.world.path = td / "world.json"

    def _to_acceptance(self) -> None:
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        self.run_until("IMPLEMENTING")
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation")
        self.run_until("AWAITING_FUNCTIONAL_REVIEW")
        self.test.assertEqual(self.step()["status"], job.STATUS_FINISHED)  # functional.prepare
        if self.acceptance is not None:
            self.workflow(_ADOPT_ACCEPTANCE_POLICY, json.dumps(self.acceptance))
        self.user_step(_FUNCTIONAL_EVIDENCE)

    def effect(self, task: str) -> None:
        words = task.lstrip("/").split()
        if words[:2] == ["satisfy-gate", "acceptance"] and "acceptance" not in self.refused:
            self.tasks.append("satisfy-gate acceptance")
            done = self.workflow_output(_SATISFY_ACCEPTANCE_WORLD, WID, str(self.world.path)).strip()
            if done == "satisfied":
                self.satisfied.append("acceptance")
            return
        if words[0] == "apply-pr-review":
            self.tasks.append("apply-pr-review")
            result = json.loads(self.workflow_output(_APPLY_PR_REVIEW_WORLD, WID, str(self.world.path)))
            self.applied.append(result)
            if result["result"] == "reopened":
                # The bounded fix the cause table names: the stale-before-edit write, a fix commit and the post-fix
                # regeneration (the helper of the review-round lifecycle tests, imported here to avoid a cycle).
                from tests.test_review_round_lifecycle import GENERATE
                self.workflow(GENERATE, WID, "post-fix", "fix the failing path")
                self.world.set(checks="green", decision="")
            return
        if words[0] == "prepare-functional-review":
            # A checklist for a later revision must differ from the first (the review-round tests' script names it).
            from tests.test_review_round_lifecycle import FUNCTIONAL_CHECKLIST
            self.tasks.append(words[0])
            self.workflow(FUNCTIONAL_CHECKLIST, WID)
            return
        super().effect(task)

    def workflow_output(self, code: str, *args: str) -> str:
        return fixtures.run_workflow_python(self.root, "import json\n" + code, *args).stdout

    def step(self) -> dict:
        clock = {"now": 0.0}

        def sleep(seconds):
            self.sleeps.append(seconds)
            clock["now"] += seconds

        with mock.patch.object(milestone_branch, "waiting_preflight", return_value=milestone_branch.Proceed()), \
                mock.patch.object(forge_mod, "GhForge", lambda repo, **_: self.world), \
                mock.patch.object(milestone_branch, "_sleep", sleep), \
                mock.patch.object(milestone_branch, "_monotonic", lambda: clock["now"]):
            return self._step_with_budget()

    def _step_with_budget(self) -> dict:
        self.rows.append(protocol.next_action(self.root, WID).raw["row"])

        def launch(task, *, cwd, **kwargs):
            self.effect(task)
            return _result(self.report)

        with mock.patch.object(worker, "launch", launch):
            return job.execute_step(self.inspect(), identity=IDENTITY, runtime=self.runtime,
                                    wait_seconds=self.wait_seconds, poll_seconds=10)

    def row(self) -> str:
        return protocol.next_action(self.root, WID).raw["row"]

    def to_refused_acceptance(self) -> None:
        """Acceptance reached and ``/satisfy-gate acceptance`` launched once: it is refused by the scripted pull
        request, the Workflow's query stores the fact, and the next answer is the external gate."""
        self.reach("to_refused_acceptance", self._to_refused_acceptance)

    def _to_refused_acceptance(self) -> None:
        self.to_acceptance()
        self.test.assertEqual(self.step()["status"], job.STATUS_FINISHED)

    def job_records(self) -> list[dict]:
        return sorted((json.loads(p.read_text()) for p in (self.runtime / "jobs").glob("*.json")),
                      key=lambda r: (r["created_at"], r["job_id"]))

    def reports(self) -> list[dict]:
        """The ``pr_report`` jobs, in order."""
        return [r for r in self.job_records() if r.get("kind") == job.PR_REPORT_KIND]

    def applies(self) -> list[dict]:
        """The launched ``/apply-pr-review`` jobs, in order."""
        return [r for r in self.job_records() if (r.get("protocol") or {}).get("action_id") == "pr.apply_review"]

    def stops(self) -> str:
        """Every stop the Controller made, as one searchable text."""
        return json.dumps([r["human_gate_pending"] for r in self.job_records() if r.get("human_gate_pending")])

    def back_to_acceptance(self) -> None:
        """After a reopen: the regenerated bundle through both implementation-review stages, the technical approval by
        policy, the checklist and the functional evidence, to the acceptance gate again."""
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation")
        self.run_until("AWAITING_FUNCTIONAL_REVIEW")
        self.test.assertEqual(self.step()["status"], job.STATUS_FINISHED)  # functional.prepare
        self.user_step(_FUNCTIONAL_EVIDENCE)


class PrDefectLoopLifecycleTest(unittest.TestCase):
    """workflow-controller-pr-defect-loop CP3 (D7, D9): the loop on the vendored 2.12.0's own answers, fake workers
    calling the Workflow's functions, the Controller's query injected. No test reaches GitHub."""

    def notifications(self, records: list[dict]) -> list[str]:
        return [event.event_type for record in records for event in job.derive_job_event(record)]

    def test_pending_checks_clear_themselves_and_the_run_ends_complete(self) -> None:
        life = LoopLifecycle(self, checks="pending")
        life.to_refused_acceptance()
        self.assertEqual(life.row(), "38f")
        # explain says what the next step does, not only the Workflow's by-hand remedy.
        self.assertEqual(life.explain()["pr_report"], {
            "row": "38f", "queried_commit": life.explain()["pr_report"]["queried_commit"], "pull_request": 7,
            "waits": True, "last_report": None})
        self.assertIn("pull request report: the next step queries pull request #7", life.explain_text())
        self.assertIn("waits while the checks are pending", life.explain_text().replace("It first waits", "waits"))
        life.wait_seconds = 100
        # The checks turn green while the step waits for them.
        life.world.on_query = lambda n: life.world.set(checks="green") if n == 3 else None
        waited = life.step()
        self.assertEqual(waited["kind"], job.PR_REPORT_KIND)
        self.assertEqual(waited["status"], job.STATUS_FINISHED, waited)
        self.assertEqual(waited["pr_report"]["outcome"], "reported")
        self.assertEqual(len(life.sleeps), 2)
        self.assertEqual(life.row(), "38d")
        self.assertIn(f"next automatic action: /apply-pr-review {WID}", life.explain_text())
        applied = life.step()
        self.assertEqual(applied["protocol"]["action_id"], "pr.apply_review")
        self.assertEqual(applied["protocol"]["reconcile"]["class"], "no_progress")
        self.assertEqual([a["result"] for a in life.applied], ["pr_fact_refreshed"])
        self.assertEqual(life.row(), "38h")
        self.assertEqual(life.step()["status"], job.STATUS_FINISHED)
        self.assertEqual(life.phase(), "MILESTONE_COMPLETE")
        self.assertEqual(life.tasks[-3:], ["satisfy-gate acceptance", "apply-pr-review", "satisfy-gate acceptance"])


    def test_a_red_pull_request_reopens_the_item_and_the_run_returns_to_acceptance(self) -> None:
        life = LoopLifecycle(self, checks="pending")
        life.to_refused_acceptance()
        before = len(life.job_records())
        life.world.set(checks="green", decision="CHANGES_REQUESTED")
        reported = life.step()
        self.assertEqual(reported["pr_report"]["outcome"], "reported")
        self.assertEqual(life.row(), "38d")
        reopened = life.step()
        self.assertEqual(reopened["protocol"]["action_id"], "pr.apply_review")
        self.assertEqual(reopened["protocol"]["reconcile"]["class"], "progress")
        self.assertEqual([a["result"] for a in life.applied], ["reopened"])
        self.assertEqual(life.applied[0]["cause"], "changes_requested")
        self.assertEqual(self.notifications([reopened]), ["step_done"])
        # The fix is pushed and the reviewer's request is resolved: the ordinary rows carry the item back to acceptance.
        life.world.set(checks="green", decision="")
        life.back_to_acceptance()
        self.assertEqual(life.row(), "38f")
        self.assertEqual(life.step()["pr_report"]["outcome"], "reported")
        self.assertEqual(life.row(), "38d")
        self.assertEqual(life.step()["protocol"]["reconcile"]["class"], "no_progress")
        self.assertEqual([a["result"] for a in life.applied], ["reopened", "pr_fact_refreshed"])
        self.assertEqual(life.row(), "38h")
        self.assertEqual(life.step()["status"], job.STATUS_FINISHED)
        self.assertEqual(life.phase(), "MILESTONE_COMPLETE")
        self.assertEqual(len(life.applies()), 2)
        self.assertNotIn("no_progress_repeated", life.stops())
        self.assertTrue(all(r["status"] == job.STATUS_FINISHED for r in life.job_records()[before:]
                            if r.get("kind") == job.PR_REPORT_KIND or r.get("protocol", {}).get("action_id") in (
                                "pr.apply_review", "acceptance.satisfy")))

    def test_an_objection_is_not_hidden_by_pending_checks(self) -> None:
        life = LoopLifecycle(self, checks="pending")
        life.to_refused_acceptance()
        life.world.set(checks="pending", decision="CHANGES_REQUESTED")
        life.wait_seconds = 100
        reported = life.step()
        self.assertEqual(reported["pr_report"]["outcome"], "reported")
        self.assertEqual(life.sleeps, [])  # no wait: D6 (c)
        self.assertEqual(len(life.world.queries), 1)
        self.assertEqual(life.row(), "38d")
        reopened = life.step()
        self.assertEqual(reopened["protocol"]["reconcile"]["class"], "progress")
        self.assertEqual([a["result"] for a in life.applied], ["reopened"])
        self.assertEqual(life.applied[0]["cause"], "changes_requested")

    def test_an_output_that_changes_nothing_is_not_reported_again_and_the_apply_is_not_repeated(self) -> None:
        # Approval required: the first report differs from the stored fact (checks turned green), so row 38d refreshes
        # it with no progress, and the gate that is still unmet is the approval -- the Workflow's own stop.
        life = LoopLifecycle(self, checks="pending", acceptance={"requires_pr_approved": True})
        life.to_refused_acceptance()
        life.world.set(checks="green")
        self.assertEqual(life.step()["pr_report"]["outcome"], "reported")
        # The fact is refreshed and the answer moves on to the next gate (38g), which the Workflow classes gate_reached.
        self.assertEqual(life.step()["protocol"]["reconcile"]["class"], "gate_reached")
        self.assertEqual(life.row(), "38g")
        launched = len(life.tasks)
        for _ in range(2):
            stopped = life.step()
            self.assertEqual(stopped["status"], job.STATUS_GATE_BLOCKED)
            self.assertEqual(stopped["pr_report"]["outcome"], "unchanged")
            self.assertIn("pr_report_unchanged", json.dumps(stopped["human_gate_pending"]))
            self.assertEqual([e.event_type for e in job.derive_job_event(stopped)], ["user_gate"])
        self.assertEqual(len(life.tasks), launched)
        self.assertEqual(len([r for r in life.reports() if r["pr_report"]["outcome"] == "reported"]), 1)
        self.assertEqual(len(life.applies()), 1)
        self.assertIn("the report changed nothing", life.explain_text())
        self.assertNotIn("no_progress_repeated", life.stops())

    def reach_38g(self, life: "LoopLifecycle") -> None:
        """Acceptance refused for the missing approval, the unapproved fact stored, the answer row 38g; the
        Controller's step reports the same state, which makes the answer row 38h, and the one launch is refused."""
        life.to_refused_acceptance()
        self.assertEqual(life.row(), "38g")
        self.assertFalse(life.explain()["pr_report"]["waits"])  # row 38g never waits (D6 (a))
        self.assertEqual(life.step()["pr_report"]["outcome"], "reported")
        self.assertEqual(life.row(), "38h")  # the vendored answer after a report that equals the stored fact
        launched = len(life.tasks)
        refused = life.step()
        self.assertEqual(life.tasks[launched:], ["satisfy-gate acceptance"])
        self.assertEqual(refused["protocol"]["action_id"], "acceptance.satisfy")
        self.assertEqual(life.row(), "38g")

    def approval_run(self, life: "LoopLifecycle") -> None:
        self.reach_38g(life)
        launched = len(life.tasks)
        stopped = life.step()
        self.assertEqual(stopped["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(stopped["pr_report"]["outcome"], "unchanged")
        self.assertIn("approved pull request", json.dumps(stopped["human_gate_pending"]))
        self.assertEqual([e.event_type for e in job.derive_job_event(stopped)], ["user_gate"])
        self.assertEqual(len(life.tasks), launched)
        self.assertEqual(len([r for r in life.reports() if r["pr_report"]["outcome"] == "reported"]), 1)
        self.assertEqual(len(life.applies()), 0)
        self.assertEqual(life.tasks.count("satisfy-gate acceptance"), 2)  # the first refusal and the one launch
        self.assertNotIn("no_progress_repeated", life.stops())
        # A person approves on GitHub: the next step reports the new output and the run carries on.
        life.world.set(decision="APPROVED")
        self.assertEqual(life.step()["pr_report"]["outcome"], "reported")
        self.assertEqual(life.row(), "38d")
        self.assertEqual(life.step()["protocol"]["action_id"], "pr.apply_review")
        self.assertEqual([a["result"] for a in life.applied], ["pr_fact_refreshed"])
        self.assertEqual(life.row(), "38h")
        self.assertEqual(life.step()["status"], job.STATUS_FINISHED)
        self.assertEqual(life.phase(), "MILESTONE_COMPLETE")

    def test_row_38g_needs_a_person_and_costs_one_refused_launch(self) -> None:
        self.approval_run(LoopLifecycle(self, checks="green", acceptance={"requires_pr_approved": True}))

    def test_approval_without_ci_is_reported_and_not_held_back_by_an_empty_rollup(self) -> None:
        life = LoopLifecycle(self, checks="empty", acceptance={"require_ci": False, "requires_pr_approved": True})
        life.wait_seconds = 100
        self.approval_run(life)
        self.assertEqual(life.sleeps, [])  # row 38g never waits

    def test_a_target_without_a_binding_keeps_the_old_stop_at_row_38f(self) -> None:
        life = LoopLifecycle(self, checks="pending", bind=False)
        life.to_refused_acceptance()
        self.assertEqual(life.row(), "38f")
        self.assertNotIn("pr_report", life.explain())  # nothing is reported for a target without a binding
        launched = len(life.tasks)
        life.world.set(checks="green")
        stopped = life.step()
        self.assertEqual(stopped["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("kind", stopped)
        self.assertEqual(len(life.tasks), launched)
        self.assertEqual(life.world.queries, [])
        self.assertEqual(life.reports(), [])

    def test_a_human_acceptance_gate_at_row_38g_reports_nothing_and_stays_a_user_gate(self) -> None:
        life = LoopLifecycle(self, checks="green", acceptance={"requires_pr_approved": True})
        life.to_refused_acceptance()
        self.assertEqual(life.row(), "38g")
        life.workflow(_TIGHTEN_ACCEPTANCE_HUMAN, "{\"requires_pr_approved\": true, \"human\": true}")
        answer = protocol.next_action(life.root, WID)
        self.assertEqual(answer.raw["row"], "38g")
        self.assertEqual(answer.raw["policy"]["mode"], "human")
        self.assertNotIn("pr_report", life.explain())  # a human acceptance gate keeps the Workflow's remedy
        launched = len(life.tasks)
        stopped = life.step()
        self.assertEqual(stopped["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("kind", stopped)
        self.assertIn(f"/accept-milestone {WID}", json.dumps(stopped["human_gate_pending"]))
        self.assertEqual([e.event_type for e in job.derive_job_event(stopped)], ["user_gate"])
        self.assertEqual(len(life.tasks), launched)
        self.assertEqual(life.world.queries, [])
        self.assertEqual(life.reports(), [])
        self.assertEqual(life.applies(), [])

    def test_a_red_pull_request_after_acceptance_ends_at_the_merge_flows_stop_and_is_not_looped(self) -> None:
        # The deferral to C10d: a MILESTONE_COMPLETE item is stopped by the merge flow before the Workflow is asked.
        life = LoopLifecycle(self, checks="green")
        life.to_acceptance()
        self.assertEqual(life.step()["status"], job.STATUS_FINISHED)  # acceptance.satisfy
        self.assertEqual(life.phase(), "MILESTONE_COMPLETE")
        life.world.set(checks="failed")
        record = {"work_item_id": WID, "branch": "milestone/demo", "repository": {"forge_repository": "owner/repo"}}
        failing = forge_mod.Checks("checks", (forge_mod.Check("ci", "FAILURE", "fail"),))
        gh = mock.Mock(pr_checks=mock.Mock(return_value=failing))
        gate = milestone_branch._checks_gate(milestone_branch.Context(
            repo_root=life.root, runtime_root=life.runtime, forge_factory=lambda repo: gh), record, 7)
        self.assertEqual(gate.code, milestone_branch.GATE_CHECKS_FAILING)
        reports, applies, launched = len(life.reports()), len(life.applies()), len(life.tasks)
        with mock.patch.object(milestone_branch, "waiting_preflight", return_value=gate), \
                mock.patch.object(forge_mod, "GhForge", lambda repo, **_: life.world):
            stopped = job.execute_step(life.inspect(), identity=IDENTITY, runtime=life.runtime)
        self.assertEqual(stopped["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("kind", stopped)
        self.assertIn("failing checks", json.dumps(stopped["human_gate_pending"]))
        self.assertEqual((len(life.reports()), len(life.applies()), len(life.tasks)), (reports, applies, launched))
        self.assertEqual(life.world.queries, [])


class InspectRepositoryBlockTest(unittest.TestCase):
    """``inspect``'s ``repository`` object names the mode for a protocol
    target only (orchestration-protocol-v1 F); a legacy target's gains no key."""

    def test_only_a_protocol_target_gains_the_mode_and_the_digest_map(self) -> None:
        from controller import cli
        protocol_target = Lifecycle(self, "2.7.0").inspect()
        block = cli._repository_block(protocol_target)
        self.assertEqual(block["workflow_mode"], "protocol")
        self.assertEqual(block["target_protocol"]["major"], 1)
        self.assertEqual(block["script_digests"], dict(protocol_target.script_digests))
        self.assertIn("scripts/workflow_protocol.py", block["script_digests"])
        # Every id 2.7.0's describe lists is known: the C.3 advisory is empty.
        self.assertIn("plan.author", protocol_target.protocol_action_ids)
        self.assertEqual(block["unknown_action_ids"], [])
        legacy = cli._repository_block(Lifecycle(self, "2.6.0").inspect())
        self.assertEqual(set(legacy), {"root", "workflow_version", "profile"})


class GoldensUnchangedTest(unittest.TestCase):
    """Every golden generator's own ``--check``, except the plan-stage one:
    its ``AMENDING_PLAN`` cases carry the permitted difference that
    ``tests/test_golden_plan_stage_decisions.py`` names and asserts (it has
    differed from a bare ``--check`` since before this milestone, and that
    test, which runs in the suite, is its check)."""

    GENERATORS = (
        "generate_external_implementation_review_decisions.py", "generate_no_policy_lifecycle.py",
        "generate_protocol_vs_legacy_differences.py",
    )

    def test_every_other_golden_generator_still_reproduces_its_golden_byte_for_byte(self) -> None:
        golden = Path(__file__).resolve().parent / "golden"
        on_disk = {path.name for path in golden.glob("generate_*.py")}
        self.assertEqual(on_disk - set(self.GENERATORS), {"generate_plan_stage_decisions.py"})
        for name in self.GENERATORS:
            with self.subTest(generator=name):
                result = subprocess.run(
                    [sys.executable, str(golden / name), "--check"], capture_output=True, text=True, timeout=600,
                    cwd=golden.parent.parent)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_2_6_0_plan_stage_golden_is_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "golden" / "generate_plan_stage_decisions.py"),
             "--release", "2.6.0", "--check"], capture_output=True, text=True, timeout=600,
            cwd=Path(__file__).resolve().parent.parent)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
