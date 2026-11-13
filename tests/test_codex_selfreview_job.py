"""`workflow-controller-h4g` CP5 (D7): the wrap-up role routed to Codex, end to end through ``job.execute_step``.

The fixtures of ``tests/test_job_codex_apply.py`` (a real managed repository with a real Workflow installed, every
checkpoint ``COMPLETE``), the real clone, carry, landing, guard and generator over the scripted fake ``codex``. The
worker's own work (the Workflow's writers run in the clone) is the ``after`` hook. Cases: a clean self-review carries the
five out members out and ends with a verifying bundle; the worker's own bundle is never the managed one; an
``IMPLEMENTING`` item (the phase commit, then the record); a plan-only re-approval (a recovered-role record); the partial
self-review, which ends without a generator run.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_carry, generator_run, job, routing  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_job_codex_apply import (  # noqa: E402
    ITEM, SELF_REVIEW_PHASE, SELF_REVIEW_RECORD, STATE_COMMIT, SelfReviewCase, WID, _entry, _git,
)

#: The recovered-role record: ``record_bundle_generation`` resolves ``same_content`` (the protected implementation
#: content is unchanged by a plan-only re-approval) and the commit names the record it supersedes.
RECOVERED_RECORD = r'''
import datetime
import subprocess
root = Path.cwd()
wid = "wi-1"
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*args):
    return subprocess.run(["git", "-c", "user.name=Worker", "-c", "user.email=worker@example.invalid", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout.strip()
head = git("rev-parse", "HEAD")
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
outcome, superseded = ws.resolve_bundle_generation_outcome(root, entry, base_commit=entry["base_commit"], head=head)
assert outcome == "same_content", (outcome, superseded)
ws.state_transaction(root, lambda s: ws.record_bundle_generation(
    s, wid, stage="implementation", head=head, now=now(), outcome=outcome))
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
revision = entry["implementation_revision"]
current = root / ".ai-review" / wid / "current"
current.mkdir(parents=True, exist_ok=True)
(current / "IMPLEMENTATION_SUMMARY.md").write_text(f"# Summary\n\nimplementation_revision: {revision}\n\nrecovered\n")
(current / "TEST_RESULTS.md").write_text("# Test results\n\nall green, recovered\n")
(current / "CONTEXT_FILES.txt").write_text("README.md\n")
git("add", "docs/ai-workflow/WORKFLOW_STATE.json")
git("commit", "-m", f"recovered record\n\nWorkflow-Bundle-Generation-Record: {wid}/{revision}\n"
    f"Workflow-Work-Item: {wid}\nWorkflow-Supersedes: {superseded}")
content_id = ws.approval_review_content_id(
    root, stage="implementation", base_commit=entry["base_commit"], work_item_type="product", work_item_id=wid,
    head="HEAD", artifacts_path=fingerprint.artifacts_path_for_work_item(wid))
(current / "REVIEW_REQUEST.md").write_text(f"# Review request\n\nstage: implementation\nreview_content_id: {content_id}\n")
'''

OUT_MEMBERS = ("REVIEW_REQUEST.md", "IMPLEMENTATION_SUMMARY.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt")


class CleanSelfReviewTest(SelfReviewCase):
    def test_the_out_members_are_carried_out_and_the_bundle_verifies(self) -> None:
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(self.evidence_phase(), "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        current = self.root / ITEM / "current"
        for name in OUT_MEMBERS:
            self.assertTrue((current / name).is_file(), name)
        self.assertIn("implementation_revision: 1", (current / "IMPLEMENTATION_SUMMARY.md").read_text())
        self.assertEqual(record["regeneration"]["stage"], "finished")
        self.assert_carry_ended(record)
        self.assert_next_stage_passes()

    def test_the_generation_record_commit_is_the_landed_tip_and_no_phase_commit_is_made(self) -> None:
        self.worked()
        base = _git(self.root, "rev-parse", "HEAD").strip()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        # at SELF_REVIEWING_IMPLEMENTATION ``enter_self_reviewing_implementation`` is a no-op: one commit lands
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{base}..HEAD").strip(), "1")
        message = _git(self.root, "log", "-1", "--format=%B").strip().splitlines()
        self.assertEqual(message[-2], f"Workflow-Bundle-Generation-Record: {WID}/1")

    def test_a_bundle_the_worker_generated_is_ignored_and_the_managed_bundle_is_the_controllers(self) -> None:
        self.worked()
        worked = self.after

        def with_a_generated_bundle(clone: Path) -> None:
            worked(clone)
            current = clone / ITEM / "current"
            (current / "MANIFEST.md").write_text("forged manifest\n")
            (current / "DIFF.patch").write_text("forged diff\n")
            (current / "files").mkdir(exist_ok=True)
            (current / "files" / "README.md").write_text("forged copy\n")
            (clone / ITEM / "review-bundle.tar.gz").write_bytes(b"forged archive")
            (clone / ITEM / ".pin").write_text("forged pin\n")

        self.after = with_a_generated_bundle
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        for path in sorted((self.root / ITEM).rglob("*")):
            if path.is_file() and not path.is_symlink():
                self.assertNotIn(b"forged", path.read_bytes(), str(path))
        self.assertTrue((self.root / ITEM / "current" / "MANIFEST.md").is_file())
        self.assert_next_stage_passes()

    def evidence_phase(self) -> str:
        return _entry(self.root)["phase"]


class SelfReviewFromImplementingJobTest(SelfReviewCase):
    PHASE = "IMPLEMENTING"

    def test_the_phase_commit_then_the_record_land_in_order_and_the_bundle_verifies(self) -> None:
        self.worked()
        base = _git(self.root, "rev-parse", "HEAD").strip()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        subjects = _git(self.root, "log", "--format=%s", f"{base}..HEAD").split("\n")
        self.assertEqual([s for s in subjects if s], ["generation record", "enter self-review"])
        self.assertEqual(_entry(self.root)["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assert_next_stage_passes()

    def test_a_partial_self_review_ends_without_a_generator_run_or_the_regeneration_wording(self) -> None:
        self.script(message="half done")
        self.after = lambda clone: fixtures.run_workflow_python(clone, SELF_REVIEW_PHASE)
        with mock.patch.object(generator_run, "run", side_effect=AssertionError("generator started")) as run:
            record = self.step()
        run.assert_not_called()
        self.assertNotIn("regeneration", record)
        self.assertEqual(_entry(self.root)["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        self.assertNotIn("regenerat", json.dumps(record.get("human_gate_pending") or {}).lower())
        self.assertNotEqual((record.get("reconciliation_evidence") or {}).get("code"), job.CARRY_BUNDLE_GATE_CODE)
        self.assertEqual(self.launched and len(self.launched), 1)


class RecoveredRoleTest(SelfReviewCase):
    """A plan-only re-approval of an item whose implementation round already exists: the item is at ``IMPLEMENTING``
    with every checkpoint ``COMPLETE`` and a record at revision 1, and the protected content is unchanged."""

    PHASE = "SELF_REVIEWING_IMPLEMENTATION"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        fixtures.run_workflow_python(cls._built, SELF_REVIEW_RECORD)
        fixtures.run_workflow_python(cls._built, STATE_COMMIT, "IMPLEMENTING", "re-approved\n\nWorkflow-Work-Item: wi-1")
        shutil.rmtree(cls._kept)
        shutil.copytree(cls._built, cls._kept, symlinks=True)

    def recovered_work(self, clone: Path) -> None:
        fixtures.run_workflow_python(clone, SELF_REVIEW_PHASE)
        fixtures.run_workflow_python(clone, RECOVERED_RECORD)

    def test_the_predicate_is_met_and_the_item_ends_at_the_target_phase_with_a_verifying_bundle(self) -> None:
        self.assertEqual(_entry(self.root)["implementation_revision"], 1)
        self.script(message="recovered")
        self.after = self.recovered_work
        with mock.patch.object(generator_run, "run", wraps=generator_run.run) as run:
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(record["regeneration"]["argument"], "implementation")
        self.assertEqual(record["regeneration"]["stage"], "finished")
        entry = _entry(self.root)
        self.assertEqual(entry["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(entry["implementation_revision"], 1)
        tip = _git(self.root, "log", "-1", "--format=%B").strip().splitlines()
        self.assertTrue(tip[-1].startswith("Workflow-Supersedes: "), tip)
        self.assert_next_stage_passes()


class SurfaceOfTheCarryTest(unittest.TestCase):
    def test_the_five_out_members_are_the_ones_the_plan_names(self) -> None:
        members = codex_carry.OUT_MEMBERS["implementation"]
        self.assertEqual(sorted(m.split("/")[-1] for m in members),
                         sorted((*OUT_MEMBERS, "PLAN.md")))
        self.assertEqual(codex_carry.ROLES[routing.MILESTONE_IMPLEMENT_SELF_REVIEW], "implementation")


if __name__ == "__main__":
    unittest.main()
