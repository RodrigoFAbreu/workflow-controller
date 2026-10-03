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

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, managed_repo, protocol, worker  # noqa: E402
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


def _result() -> worker.WorkerResult:
    return worker.WorkerResult(
        outcome="SUCCESS", returncode=0, session_id="s", is_error=False, subtype="success",
        terminal_reason=None, stop_reason=None, result="ok", num_turns=1, permission_denials=[],
        total_cost_usd=0.0, duration_ms=1, stdout="", stderr="", raw_json={})


class Lifecycle:
    """A disposable ``release`` target seeded at a bound plan bundle, and the
    fake worker that performs the effect of each launched command."""

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
            return _result()

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
