"""The review round cap through real Workflow reconciliation
(workflow-controller-review-convergence CP1 and CP2, D1-D3, D7).

Each test drives a disposable target on the vendored 2.7.0 Workflow (protocol
1.0, so ``review_round`` is absent and every round is the Controller's own
count, ``source`` ``jobs``) with the real ``job.execute_step``: the Workflow's
own ``next-action`` decides, its ``reconcile`` judges each job, and the fake
worker is a ``worker.launch`` double whose effect is the Workflow command's,
made through the target's own installed writers and generator. No reconcile
class is synthesised and no job record is written by hand, so the count is
taken over exactly the ``pre_state`` values a real loop stores.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, managed_repo, protocol_decision, review_rounds, telemetry, worker  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_protocol_lifecycle import (  # noqa: E402
    _CHECKPOINT, _FUNCTIONAL_CHECKLIST, _PLAN_REVIEW, _TECHNICAL_APPROVAL, _result)

WID = "demo"
STATE_REL = "docs/ai-workflow/WORKFLOW_STATE.json"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

#: ``/review-plan``'s effect with a ``REVISE``: the verdict on file, bound to
#: the reviewed bundle and content, then ``record_local_plan_review``.
PLAN_REVISE = """
import datetime
root = Path.cwd()
wid = sys.argv[1]
item = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
bundle_dir = root / fingerprint.resolve_bundle_dir(root, wid, stage="plan")
bundle_id = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["bundle_id"]
rcid, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
path = root / fingerprint.resolve_feedback_path_contract(root, wid)["review_feedback_path"]
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("# Review Decision\\n\\nStatus: REVISE\\nReviewer role: LOCAL_MODEL_PLAN_REVIEW\\n"
                f"Reviewed bundle ID: {bundle_id}\\nReviewed base commit: {item['base_commit']}\\nWork item: {wid}\\n"
                f"Reviewed review content ID: {rcid}\\n\\n## Blocking findings\\n\\n1. Revise it.\\n")
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ws.state_transaction(root, lambda s: ws.record_local_plan_review(
    s, wid, verdict="REVISE", bundle_id=bundle_id, review_content_id=rcid, round=1, now=now))
"""

#: ``/milestone-plan``'s and ``/apply-plan-review``'s publication: the plan
#: edited (its revision advanced, ``advance``, or not, ``same``), the
#: registry, mapping and declarations written, ``publish_plan_revision``, the
#: generator and ``bind_plan_review_bundle``.
PLAN_PUBLISH = """
import datetime, subprocess
root = Path.cwd()
wid, advance = sys.argv[1], sys.argv[2] == "advance"
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
item = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
revision = item["plan_revision"] + (1 if advance else 0)
plan_path = f"docs/ai-workflow/{wid}-PLAN.md"
registry_path = f"docs/ai-workflow/registry/{wid}-registry.json"
mapping_path = f"docs/ai-workflow/requirements/{wid}-mapping.json"
artifacts_path = f"docs/ai-workflow/registry/{wid}-artifacts.json"
checkpoints = [{"id": "CP1", "name": "one checkpoint", "depends_on": [], "complexity": 1, "session_target": 1}]
registry = ws.generate_registry(wid, revision, checkpoints)
mapping = ws.generate_mapping(wid, {"R1": {"description": "one requirement", "checkpoint_ids": ["CP1"]}},
                              registry=registry)
ws.write_registry_and_mapping(root, Path(registry_path), Path(mapping_path), registry, mapping)
if not (root / artifacts_path).is_file():
    declarations = ws.generate_artifacts_declarations(wid, plan_path, registry_path, mapping_path,
                                                      work_item_type="product")
    (root / artifacts_path).write_text(json.dumps(declarations, indent=2) + "\\n")
plan = root / plan_path
edits = (plan.read_text().count("Applied.") if plan.is_file() else 0) + 1
plan.write_text(f"# {wid} plan (Revision {revision})\\n\\nA disposable fixture plan.\\n" + "Applied.\\n" * edits
                + ws.render_registry_markdown(registry) + "\\n")
git("add", "-N", "--", plan_path, registry_path, mapping_path, artifacts_path)
rcid, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
ws.state_transaction(root, lambda s: ws.publish_plan_revision(s, wid, revision, now(), review_content_id=rcid))
inputs = root / fingerprint.resolve_plan_review_inputs_dir(root, wid)
inputs.mkdir(parents=True, exist_ok=True)
(inputs / "REVIEW_REQUEST.md").write_text(f"# Review request\\n\\nstage: plan\\nwork item: {wid}\\nreview_content_id: {rcid}\\n")
(inputs / "TEST_RESULTS.md").write_text(f"stage: plan (revision {revision})\\nhead: {git('rev-parse', 'HEAD')}\\n\\n"
                                        "No checks at the plan stage.\\n")
(inputs / "CONTEXT_FILES.txt").write_text("README.md\\n")
subprocess.run(["./scripts/prepare-ai-review.sh", item["base_commit"], "plan", wid], cwd=root, check=True,
               capture_output=True, text=True)
binding = ws.verify_plan_review_bundle(root, wid)
ws.state_transaction(root, lambda s: ws.bind_plan_review_bundle(s, wid, binding=binding, now=now()))
"""

#: The plan, registry, mapping and declarations of an item routed but never
#: published, at its current ``plan_revision``, committed (authoring begun).
REGISTRY_AT_REVISION = """
import subprocess
root = Path.cwd()
wid = sys.argv[1]
item = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
checkpoints = [{"id": "CP1", "name": "one checkpoint", "depends_on": [], "complexity": 1, "session_target": 1}]
registry = ws.generate_registry(wid, item["plan_revision"], checkpoints)
mapping = ws.generate_mapping(wid, {"R1": {"description": "one requirement", "checkpoint_ids": ["CP1"]}},
                              registry=registry)
ws.write_registry_and_mapping(root, Path(item["registry_path"]), Path(item["mapping_path"]), registry, mapping)
declarations = ws.generate_artifacts_declarations(wid, item["plan_path"], item["registry_path"], item["mapping_path"],
                                                  work_item_type="product")
(root / fingerprint.artifacts_path_for_work_item(wid)).write_text(json.dumps(declarations, indent=2) + "\\n")
(root / item["plan_path"]).write_text(f"# {wid} plan (Revision {item['plan_revision']})\\n\\nA first draft.\\n"
                                      + ws.render_registry_markdown(registry) + "\\n")
subprocess.run(["git", "add", "-A"], cwd=root, check=True)
subprocess.run(["git", "commit", "-q", "-m", "Begin authoring"], cwd=root, check=True)
"""

#: An implementation bundle generation (``/milestone-implement`` step 4, a
#: bounded functional fix of ``/apply-functional-review`` from
#: ``AWAITING_FUNCTIONAL_REVIEW`` after its stale-before-edit write,
#: ``stage`` ``implementation``; ``/apply-implementation-review``'s post-fix
#: regeneration, ``stage`` ``post-fix``): for a post-fix round the entry into
#: ``APPLYING_REVIEW_FEEDBACK`` where the item is not there yet, the pending
#: state write committed, a fix commit (``fix`` other than ``none``), then
#: ``record_bundle_generation`` with the outcome the Workflow resolves
#: (``same_content`` when the fix changed no protected content), its record
#: commit, the author files and the generator.
GENERATE = """
import datetime, subprocess
root = Path.cwd()
wid, stage, fix = sys.argv[1], sys.argv[2], sys.argv[3]
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
state_rel = "docs/ai-workflow/WORKFLOW_STATE.json"
def item():
    return json.loads((root / state_rel).read_text())["work_items"][wid]
outcome, superseded = "ordinary", None
if stage == "post-fix":
    if item()["phase"] == "AWAITING_FUNCTIONAL_REVIEW":
        ws.state_transaction(root, lambda s: ws.mark_technical_approval_stale(s, wid, now()))
    elif item()["phase"] != "APPLYING_REVIEW_FEEDBACK":
        ws.state_transaction(root, lambda s: ws.enter_applying_review_feedback(s, wid, now()))
    if git("status", "--porcelain", "--", state_rel):
        git("add", "--", state_rel)
        git("commit", "-q", "-m", f"Commit the pending review-stage state write\\n\\nWorkflow-Work-Item: {wid}")
    if fix != "none":
        path = root / "app" / "fix.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text((path.read_text() if path.exists() else "") + fix + "\\n")
        git("add", "app/fix.txt")
        git("commit", "-q", "-m", "Apply the review fix")
    outcome, superseded = ws.resolve_bundle_generation_outcome(
        root, item(), base_commit=item()["base_commit"], head=git("rev-parse", "HEAD"))
head = git("rev-parse", "HEAD")
ws.state_transaction(root, lambda s: ws.record_bundle_generation(
    s, wid, stage=stage, head=head, now=now(), outcome=outcome))
revision = item()["implementation_revision"]
trailers = f"Workflow-Bundle-Generation-Record: {wid}/{revision}\\nWorkflow-Work-Item: {wid}"
if outcome == "same_content":
    trailers += f"\\nWorkflow-Supersedes: {superseded}"
git("add", "--", state_rel)
git("commit", "-q", "-m", f"Record implementation bundle generation\\n\\n{trailers}")
digest = ws.approval_review_content_id(
    root, stage="implementation", base_commit=item()["base_commit"], work_item_type="product",
    work_item_id=wid, head="HEAD", artifacts_path=fingerprint.artifacts_path_for_work_item(wid))
bundle = root / ".ai-review" / wid / "current"
bundle.mkdir(parents=True, exist_ok=True)
(bundle / "IMPLEMENTATION_SUMMARY.md").write_text(f"# Summary\\n\\nimplementation_revision: {revision}\\n")
(bundle / "TEST_RESULTS.md").write_text("# Test results\\n\\nall green\\n")
(bundle / "CONTEXT_FILES.txt").write_text("README.md\\n")
(bundle / "REVIEW_REQUEST.md").write_text(f"# Review request\\n\\nstage: implementation\\nreview_content_id: {digest}\\n")
subprocess.run(["./scripts/prepare-ai-review.sh", item()["base_commit"], stage, wid], cwd=root, check=True,
               capture_output=True, text=True)
"""

#: An implementation-review ``REVISE`` on file for the current bundle: by the
#: local reviewer (``/review-implementation``, recorded with
#: ``record_local_implementation_review``) or by the external reviewer of a
#: ``"2.1"`` item (the verdict file alone, which ``/apply-implementation-review``
#: consumes).
IMPLEMENTATION_REVISE = """
import datetime
root = Path.cwd()
wid, reviewer = sys.argv[1], sys.argv[2]
item = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
ids = fingerprint.read_manifest_identifiers(root / ".ai-review" / wid / "current" / "MANIFEST.md")
role = "LOCAL_MODEL_IMPLEMENTATION_REVIEW" if reviewer == "local" else "EXTERNAL_IMPLEMENTATION_REVIEW"
path = root / fingerprint.resolve_feedback_path_contract(root, wid)["review_feedback_path"]
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(f"# Review Decision\\n\\nStatus: REVISE\\nReviewer role: {role}\\n"
                f"Reviewed bundle ID: {ids['bundle_id']}\\nReviewed base commit: {item['base_commit']}\\n"
                f"Work item: {wid}\\nReviewed review content ID: {ids['review_content_id']}\\n\\n"
                "## Blocking findings\\n\\n1. Fix it.\\n")
if reviewer == "local":
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ws.state_transaction(root, lambda s: ws.record_local_implementation_review(
        s, wid, verdict="REVISE", bundle_id=ids["bundle_id"], review_content_id=ids["review_content_id"],
        round=1, now=now))
"""


class CapLoop:
    """A disposable 2.7.0 target with the ``demo`` work item seeded to
    ``stage`` (``fixtures.seed_workflow_item``), optionally moved to
    ``IMPLEMENTING`` under a current plan approval, and one-step drivers."""

    def __init__(self, test: unittest.TestCase, stage: str, *, governing: str = "2.2", implementing: bool = False,
                 root: Path | None = None, stub_dir: Path | None = None) -> None:
        self.test = test
        if root is None:
            self.td = Path(tempfile.mkdtemp())
            test.addCleanup(lambda: shutil.rmtree(self.td, ignore_errors=True))
            root = self.td / "repo"
        self.root = root
        fixtures.seed_workflow_item(self.root, "2.7.0", stage, work_item_id=WID, governing_workflow_version=governing)
        if implementing:
            self._approve_plan()
        self.stub = fixtures.write_stub_workflow_manager((stub_dir or self.root.parent) / "workflow-manager-2.7.0",
                                                         release="2.7.0")
        self.runtime = self.root.parent / "runtime"
        self.runtime.mkdir(exist_ok=True)
        self.tasks: list[str] = []

    def _approve_plan(self) -> None:
        """``IMPLEMENTING`` with a current plan approval of the seeded plan,
        committed with the trailers the Workflow discovers."""
        rcid = fixtures.run_workflow_python(
            self.root, "print(fingerprint.compute_review_content_id_plan_stage_for_work_item(Path.cwd(), sys.argv[1])[0])",
            WID).stdout.strip()
        path = self.root / STATE_REL
        state = json.loads(path.read_text())
        item = state["work_items"][WID]
        item.pop("plan_review_binding", None)
        item.update(phase="IMPLEMENTING", current_bundle_id=None, checkpoints={}, plan_approval={
            "status": "CURRENT", "basis": "EXTERNAL_APPROVE", "reviewed_bundle_id": "a" * 64,
            "approved_review_content_id": rcid, "review_content_manifest": [], "user_confirmation": "approved",
            "reviewed_content_commit": None, "waived_guarantees": []})
        path.write_text(json.dumps(state, indent=2) + "\n")
        fixtures.run(["git", "add", "-A"], cwd=self.root)
        fixtures.run(["git", "commit", "-q", "-m", f"approve plan\n\nWorkflow-Plan-Approval: {rcid}\n"
                                                   f"Workflow-Work-Item: {WID}\n"], cwd=self.root)

    # --- the Workflow's own writers ------------------------------------------------

    def workflow(self, code: str, *args: str) -> None:
        fixtures.run_workflow_python(self.root, "import json\n" + code, WID, *args)

    def review_plan(self) -> None:
        self.workflow(PLAN_REVISE)

    def publish_plan(self, mode: str = "advance") -> None:
        self.workflow(PLAN_PUBLISH, mode)

    def checkpoint(self) -> None:
        self.workflow(_CHECKPOINT)

    def generate(self, stage: str = "implementation", fix: str = "none") -> None:
        self.workflow(GENERATE, stage, fix)

    def review_implementation(self, reviewer: str = "local") -> None:
        self.workflow(IMPLEMENTATION_REVISE, reviewer)

    # --- reading -----------------------------------------------------------------------

    def item(self) -> dict:
        return fixtures.state_entry(self.root, WID)

    def records(self) -> list[dict]:
        return sorted((json.loads(p.read_text()) for p in (self.runtime / "jobs").glob("*.json")),
                      key=review_rounds.record_key)

    def counted(self, stage: str) -> tuple[int, str]:
        """D1's round of ``stage`` now: the current snapshot after every stored record."""
        snapshot = {key: self.item().get(key) for key in ("phase", "plan_revision", "implementation_revision")}
        return review_rounds.current_round(snapshot, stage, self.records(), target_repo=str(self.root),
                                           work_item_id=WID)

    def inspect(self):
        return managed_repo.inspect(self.root, manager_bin=str(self.stub))

    # --- one step ---------------------------------------------------------------------

    def step(self, effect=None, *, cap: int) -> tuple[dict, list[str]]:
        """One ``execute_step`` under ``cap``; ``effect()`` is the launched
        worker's work. Returns the record and the tasks launched."""
        launched: list[str] = []

        def launch(task, *, cwd, **kwargs):
            launched.append(task.split()[0])
            if effect is not None:
                effect()
            return _result()

        with mock.patch.object(worker, "launch", launch):
            record = job.execute_step(self.inspect(), identity=IDENTITY, runtime=self.runtime, review_round_cap=cap)
        self.tasks += launched
        return record, launched


def _action(record: dict) -> str | None:
    return (record.get("protocol") or {}).get("action_id")


def _reconciled(record: dict) -> str | None:
    return ((record.get("protocol") or {}).get("reconcile") or {}).get("class")


class _CapCase(unittest.TestCase):
    def assert_launched(self, result: tuple[dict, list[str]], task: str, action: str, reconcile: str = "progress"):
        record, launched = result
        self.assertEqual(launched, [task], record)
        self.assertEqual((record["status"], _action(record), _reconciled(record)),
                         (job.STATUS_FINISHED, action, reconcile), record)
        return record

    def assert_capped(self, loop: CapLoop, cap: int, stage: str, round_number: int) -> str:
        """One step at ``cap`` is the ``review_round_cap_reached`` gate: no
        worker, the Workflow state and ``HEAD`` untouched, one more record,
        ``GATE_BLOCKED``, which no later count includes."""
        state_before = (loop.root / STATE_REL).read_bytes()
        head_before = fixtures.current_head(loop.root)
        count_before = loop.counted(stage)
        records_before = len(loop.records())
        record, launched = loop.step(cap=cap)
        self.assertEqual(launched, [], record)
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record)
        text = record["human_gate_pending"]["what_is_required"]
        self.assertTrue(text.startswith(f"{protocol_decision.REVIEW_ROUND_CAP_REACHED}: "), text)
        self.assertIn(f"at {stage} review round {round_number} (counted from the Controller's job records", text)
        self.assertIn(f"at the round cap of {cap}", text)
        self.assertEqual((loop.root / STATE_REL).read_bytes(), state_before)
        self.assertEqual(fixtures.current_head(loop.root), head_before)
        self.assertEqual(len(loop.records()), records_before + 1)
        self.assertEqual(loop.counted(stage), count_before)
        return text

    def telemetry_rounds(self, loop: CapLoop) -> list[tuple[str, int | None, str | None]]:
        return [(row["action"], row["round"], row["round_source"]) for row in telemetry.rows(loop.records())]


class PlanLoopTest(_CapCase):
    """The plan stage: rounds are the distinct plan revisions seen at a
    plan-review ready phase (D1), a lower bound of the Workflow's count."""

    def test_the_cap_stops_the_apply_and_a_raised_cap_continues_to_the_next_review(self) -> None:
        loop = CapLoop(self, "ready")
        # Round 1: the first local review runs at cap 1 (a review stops only above the cap).
        self.assert_launched(loop.step(loop.review_plan, cap=1), "/review-plan", "plan.review.local")
        self.assertEqual(loop.counted("plan"), (1, review_rounds.SOURCE_JOBS))
        # Applying the REVISE would open round 2: stopped at cap 1.
        text = self.assert_capped(loop, 1, "plan", 1)
        self.assertIn("the Workflow's own count may be higher", text)
        # The same command with a raised cap applies it, and the loop continues to the next review.
        self.assert_launched(loop.step(loop.publish_plan, cap=2), "/apply-plan-review", "plan.apply_review")
        self.assertEqual((loop.item()["phase"], loop.item()["plan_revision"]), ("AWAITING_LOCAL_PLAN_REVIEW", 2))
        self.assertEqual(loop.counted("plan"), (2, review_rounds.SOURCE_JOBS))
        self.assert_launched(loop.step(loop.review_plan, cap=2), "/review-plan", "plan.review.local")
        self.assert_capped(loop, 2, "plan", 2)
        self.assertEqual(loop.tasks, ["/review-plan", "/apply-plan-review", "/review-plan"])
        # telemetry derives each job's round with the same function, over the same records.
        self.assertEqual(self.telemetry_rounds(loop), [
            ("plan.review.local", 1, "jobs"), ("plan.apply_review", 1, "jobs"), ("plan.review.local", 2, "jobs")])

        # A round that publishes new content without advancing plan_revision is not counted: the fallback is a
        # lower bound of the Workflow's own number, so the cap fires later than that number would, never earlier.
        self.assert_launched(loop.step(lambda: loop.publish_plan("same"), cap=3), "/apply-plan-review",
                             "plan.apply_review")
        self.assertEqual(loop.item()["plan_revision"], 2)
        self.assert_launched(loop.step(loop.review_plan, cap=3), "/review-plan", "plan.review.local")
        counted, source = loop.counted("plan")
        sys.path.insert(0, str(SCRIPTS))
        try:
            import workflow_protocol
        finally:
            sys.path.remove(str(SCRIPTS))
        workflows = workflow_protocol._plan_review_round(loop.item())
        self.assertEqual((counted, source, workflows), (2, review_rounds.SOURCE_JOBS, 3))
        self.assertFalse(review_rounds.cap_decision("plan.apply_review", counted, 3))
        self.assertTrue(review_rounds.cap_decision("plan.apply_review", workflows, 3))
        self.assert_launched(loop.step(cap=3), "/apply-plan-review", "plan.apply_review", reconcile="no_progress")

    def test_a_first_publication_at_revision_two_is_round_one(self) -> None:
        """An unpublished ``PLANNING`` item at ``plan_revision`` 1 whose first
        publication is at revision 2 -- the shape
        ``create_remediation_child_work_item`` leaves, made here by routing
        the item at revision 1 and authoring it at 2: the authoring jobs'
        ``PLANNING`` observations are not a round."""
        loop = CapLoop(self, "route")
        loop.workflow(REGISTRY_AT_REVISION)
        self.assertEqual((loop.item()["phase"], loop.item()["plan_revision"]), ("PLANNING", 1))
        self.assert_launched(loop.step(loop.publish_plan, cap=1), "/milestone-plan", "plan.author")
        self.assertEqual((loop.item()["phase"], loop.item()["plan_revision"]), ("AWAITING_LOCAL_PLAN_REVIEW", 2))
        self.assertEqual(loop.counted("plan"), (1, review_rounds.SOURCE_JOBS))
        self.assert_launched(loop.step(loop.review_plan, cap=1), "/review-plan", "plan.review.local")
        self.assert_capped(loop, 1, "plan", 1)
        self.assert_launched(loop.step(loop.publish_plan, cap=2), "/apply-plan-review", "plan.apply_review")
        self.assertEqual(loop.item()["plan_revision"], 3)
        self.assertEqual(loop.counted("plan"), (2, review_rounds.SOURCE_JOBS))
        self.assert_launched(loop.step(loop.review_plan, cap=2), "/review-plan", "plan.review.local")
        self.assertEqual(self.telemetry_rounds(loop), [
            ("plan.author", 1, "jobs"), ("plan.review.local", 1, "jobs"), ("plan.apply_review", 1, "jobs"),
            ("plan.review.local", 2, "jobs")])


class ImplementationLoopTest(_CapCase):
    """The implementation stage: 1 plus the rises of
    ``implementation_revision`` (D1), whatever the reconcile class."""

    def test_an_ordinary_apply_opens_a_round_and_a_same_content_apply_does_not(self) -> None:
        loop = CapLoop(self, "publish", implementing=True)
        self.assert_launched(loop.step(loop.checkpoint, cap=2), "/milestone-implement", "implementation.checkpoint")
        self.assert_launched(loop.step(loop.generate, cap=2), "/milestone-implement", "implementation.self_review")
        self.assertEqual(loop.item()["implementation_revision"], 1)
        self.assert_launched(loop.step(loop.review_implementation, cap=2), "/review-implementation",
                             "implementation.review.local")
        # An ordinary post-fix generation: revision 1 -> 2, a new round.
        self.assert_launched(loop.step(lambda: loop.generate("post-fix", "first fix"), cap=2),
                             "/apply-implementation-review", "implementation.apply_review")
        self.assertEqual(loop.item()["implementation_revision"], 2)
        self.assertEqual(loop.counted("implementation"), (2, review_rounds.SOURCE_JOBS))
        self.assert_launched(loop.step(loop.review_implementation, cap=2), "/review-implementation",
                             "implementation.review.local")
        # A same_content republication ends `progress` with the revision unchanged: no new round.
        self.assert_launched(loop.step(lambda: loop.generate("post-fix", "none"), cap=3),
                             "/apply-implementation-review", "implementation.apply_review")
        self.assertEqual((loop.item()["phase"], loop.item()["implementation_revision"]),
                         ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", 2))
        self.assertEqual(loop.counted("implementation"), (2, review_rounds.SOURCE_JOBS))
        # Round 2 at cap 2: the review runs, the apply after it stops.
        self.assert_launched(loop.step(loop.review_implementation, cap=2), "/review-implementation",
                             "implementation.review.local")
        text = self.assert_capped(loop, 2, "implementation", 2)
        self.assertNotIn("may be higher", text)
        self.assertEqual(self.telemetry_rounds(loop), [
            ("implementation.checkpoint", 1, "jobs"), ("implementation.self_review", 1, "jobs"),
            ("implementation.review.local", 1, "jobs"), ("implementation.apply_review", 1, "jobs"),
            ("implementation.review.local", 2, "jobs"), ("implementation.apply_review", 2, "jobs"),
            ("implementation.review.local", 2, "jobs")])

    def test_a_2_1_apply_that_ends_gate_reached_opens_a_round(self) -> None:
        loop = CapLoop(self, "publish", governing="2.1", implementing=True)
        self.assert_launched(loop.step(loop.checkpoint, cap=2), "/milestone-implement", "implementation.checkpoint")
        self.assert_launched(loop.step(loop.generate, cap=2), "/milestone-implement", "implementation.self_review",
                             reconcile="gate_reached")
        loop.review_implementation("external")
        self.assert_launched(loop.step(lambda: loop.generate("post-fix", "first fix"), cap=2),
                             "/apply-implementation-review", "implementation.apply_review", reconcile="gate_reached")
        self.assertEqual((loop.item()["phase"], loop.item()["implementation_revision"]),
                         ("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", 2))
        self.assertEqual(loop.counted("implementation"), (2, review_rounds.SOURCE_JOBS))
        loop.review_implementation("external")
        self.assert_capped(loop, 2, "implementation", 2)


#: ``/review-functional``'s output on file: findings the bounded branch applies.
FUNCTIONAL_FINDINGS = """
root = Path.cwd()
wid, text = sys.argv[1], sys.argv[2]
feedback_dir = root / fingerprint.resolve_feedback_dir(root, wid)
feedback_dir.mkdir(parents=True, exist_ok=True)
(feedback_dir / "FUNCTIONAL_REVIEW.md").write_text(f"# Functional review\\n\\n1. {text}\\n")
"""

#: The functional checklist, naming the revision it is for (so each
#: republication commits a new one).
FUNCTIONAL_CHECKLIST = _FUNCTIONAL_CHECKLIST.replace(
    '1. Exercise the feature.\\n")', '1. Exercise revision " + str(revision) + ".\\n")')
assert FUNCTIONAL_CHECKLIST != _FUNCTIONAL_CHECKLIST


class FunctionalFixLoopTest(_CapCase):
    """A bounded functional fix (``functional.apply_findings``) republishes
    the implementation bundle from ``AWAITING_FUNCTIONAL_REVIEW``: an ordinary
    republication is a new implementation round, a ``same_content`` one is
    not, and the cap gate and ``telemetry`` agree on both (D2, D7)."""

    def approve_to_functional_review(self, loop: CapLoop) -> None:
        """The manual external review, the technical approval and the
        functional checklist, recorded as the Workflow's own writers record
        them, from a bundle whose local review is an ``APPROVE``."""
        loop.workflow(_PLAN_REVIEW, "implementation", "APPROVE", "manual")
        loop.workflow(_TECHNICAL_APPROVAL)
        self.assertEqual(loop.item()["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        loop.workflow(FUNCTIONAL_CHECKLIST)

    def approve_locally(self, loop: CapLoop, cap: int) -> None:
        self.assert_launched(loop.step(lambda: loop.workflow(_PLAN_REVIEW, "implementation", "APPROVE", "local"),
                                       cap=cap), "/review-implementation", "implementation.review.local",
                             reconcile="gate_reached")

    def functional_fix(self, loop: CapLoop, text: str, fix: str, *, cap: int = 9):
        loop.workflow(FUNCTIONAL_FINDINGS, text)
        return loop.step(lambda: loop.generate("post-fix", fix), cap=cap)

    def test_an_ordinary_functional_fix_opens_a_round_and_a_same_content_one_does_not(self) -> None:
        loop = CapLoop(self, "publish", implementing=True)
        self.assert_launched(loop.step(loop.checkpoint, cap=9), "/milestone-implement", "implementation.checkpoint")
        self.assert_launched(loop.step(loop.generate, cap=9), "/milestone-implement", "implementation.self_review")
        self.approve_locally(loop, 2)
        self.approve_to_functional_review(loop)
        self.assertEqual(loop.item()["implementation_revision"], 1)
        # An ordinary bounded fix: revision 1 -> 2, both review stages run again.
        record = self.assert_launched(self.functional_fix(loop, "Fix it.", "functional fix"),
                                      "/apply-functional-review", "functional.apply_findings")
        self.assertEqual((record["pre_state"]["phase"], loop.item()["phase"], loop.item()["implementation_revision"]),
                         ("AWAITING_FUNCTIONAL_REVIEW", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", 2))
        self.assertEqual(loop.counted("implementation"), (2, review_rounds.SOURCE_JOBS))
        # The review boundary after it: the local review of round 2 runs under cap 2.
        self.approve_locally(loop, 2)
        self.approve_to_functional_review(loop)
        # A same_content bounded fix (it nets out identical): `progress`, revision unchanged, no new round.
        self.assert_launched(self.functional_fix(loop, "Fix it again.", "none", cap=2),
                             "/apply-functional-review", "functional.apply_findings")
        self.assertEqual((loop.item()["phase"], loop.item()["implementation_revision"]),
                         ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", 2))
        self.assertEqual(loop.counted("implementation"), (2, review_rounds.SOURCE_JOBS))
        self.approve_locally(loop, 2)
        # The cap gate and telemetry read the same rounds over the same records.
        self.assertEqual(self.telemetry_rounds(loop), [
            ("implementation.checkpoint", 1, "jobs"), ("implementation.self_review", 1, "jobs"),
            ("implementation.review.local", 1, "jobs"), ("functional.apply_findings", None, None),
            ("implementation.review.local", 2, "jobs"), ("functional.apply_findings", None, None),
            ("implementation.review.local", 2, "jobs")])


if __name__ == "__main__":
    unittest.main()
