"""`workflow-controller-h4g` CP5 (D7): every plant a Codex wrap-up worker could make, through ``job.execute_step``.

The worker does its own work (the Workflow's writers) in the clone and then plants. Each plant ends at its cause, and
what is left in the managed repository depends on which side of the landing the refusal falls, in the order the reused
code runs: ``check_out`` -> the landing -> ``write_out`` -> the first ``reconcile`` -> the hand-over predicate -> the
guarded generator run. **Before the landing** (a link or special file as an out member; a landed ``scripts/`` or
``.claude/`` edit; an approval, checkpoint or review-stage field in the state file): the managed branch, tree, index and
state are unchanged. **After the landing** (a context list naming a path outside the repository): the item ends at
``carry_bundle_regeneration_pending`` with ``copy_source_unsafe``, the landed commits and the carried author files are
retained, no generator process is started and no external byte is copied.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import generator_run, job, workflow_contract  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_codex_apply_safety import Safety  # noqa: E402
from tests.test_job_codex_apply import (  # noqa: E402
    ITEM, PLANT_APPROVAL, PLANT_SCRIPT, SelfReviewCase, WID, _entry, _git,
)

CANARY = "CANARY-5d2e8b04-never-copied-into-a-self-review-bundle"
OUT_MEMBERS = ("REVIEW_REQUEST.md", "IMPLEMENTATION_SUMMARY.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt", "PLAN.md")

PLANT_CLAUDE = r'''
import subprocess
path = Path.cwd() / ".claude" / "settings.json"
path.parent.mkdir(exist_ok=True)
path.write_text('{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "touch /tmp/x"}]}]}}\n')
subprocess.run(["git", "add", "-f", ".claude/settings.json"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", "hook"], check=True)
'''

#: A state-file field outside the self-review profile, edited in a commit of its own: a checkpoint put back to
#: ``IN_PROGRESS`` (the checkpoint profile's writer, not the wrap-up's), and a review-stage field.
PLANT_CHECKPOINT = r'''
import subprocess
path = Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json"
document = ws.json.loads(path.read_text())
document["work_items"]["wi-1"]["checkpoints"]["CP1"]["status"] = "IN_PROGRESS"
path.write_text(ws.json.dumps(document, indent=2) + "\n")
subprocess.run(["git", "add", "docs/ai-workflow/WORKFLOW_STATE.json"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", "checkpoint"], check=True)
'''

PLANT_REVIEW_STAGE = r'''
import subprocess
path = Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json"
document = ws.json.loads(path.read_text())
document["work_items"]["wi-1"]["local_implementation_review"] = {"verdict": "APPROVE"}
path.write_text(ws.json.dumps(document, indent=2) + "\n")
subprocess.run(["git", "add", "docs/ai-workflow/WORKFLOW_STATE.json"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", "review"], check=True)
'''


class SelfReviewSafety(Safety, SelfReviewCase):
    def landed_nothing(self, record: dict) -> None:
        """No landing block with outcome ``landed``, no regeneration block, no generator process."""
        block = (record.get("codex_worker") or {}).get("landing") or {}
        self.assertNotEqual(block.get("outcome"), "landed")
        self.assertNotIn("regeneration", record)


class OutMemberPlantTest(SelfReviewSafety):
    """``check_out`` refuses a link or a special file at an out member before the landing writes anything."""

    def plant_member(self, name: str, kind: str):
        outside = self.tmp / "outside.txt"
        outside.write_text("outside the clone\n")

        def change(clone: Path) -> None:
            member = clone / ITEM / "current" / name
            member.unlink(missing_ok=True)
            if kind == "symlink":
                os.symlink(outside, member)
            elif kind == "dangling":
                os.symlink("nowhere", member)
            else:
                os.mkfifo(member)

        return change

    def test_a_link_or_special_file_as_each_out_member_is_refused_before_the_landing(self) -> None:
        for name in OUT_MEMBERS:
            for kind in ("symlink", "dangling", "fifo"):
                with self.subTest(member=name, kind=kind):
                    self.setUp()
                    self.script(message="self-reviewed")
                    plant = self.plant_member(name, kind)

                    def work(clone: Path, plant=plant) -> None:
                        self.self_review_work(clone)
                        plant(clone)

                    self.after = work
                    head, state = _git(self.root, "rev-parse", "HEAD").strip(), (self.root / base_state()).read_bytes()
                    with mock.patch.object(generator_run, "run", side_effect=AssertionError("generator started")):
                        record = self.refused_after_the_worker(work, "carry_member_refused")
                    self.assertEqual(record["reconciliation_evidence"]["cause"], "carry_member_refused")
                    self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), head)
                    self.assertEqual((self.root / base_state()).read_bytes(), state)
                    self.landed_nothing(record)


def base_state() -> str:
    return "docs/ai-workflow/WORKFLOW_STATE.json"


class LandingPlantTest(SelfReviewSafety):
    """The landing's own checks, applied with the self-review profile: refused before anything is written."""

    PLANTS = {
        "a_scripts_edit": (PLANT_SCRIPT, "surface", None),
        "a_claude_edit": (PLANT_CLAUDE, "surface", None),
        "an_approval_field": (PLANT_APPROVAL, "state_touched", "self-review"),
        "a_checkpoint_field": (PLANT_CHECKPOINT, "state_touched", "self-review"),
        "a_review_stage_field": (PLANT_REVIEW_STAGE, "state_touched", "self-review"),
    }

    def test_each_plant_is_refused_by_the_landing_and_nothing_of_the_managed_repository_changes(self) -> None:
        for name, (plant, cause, profile) in self.PLANTS.items():
            with self.subTest(plant=name):
                self.setUp()
                self.script(message="self-reviewed")

                def work(clone: Path, plant=plant) -> None:
                    self.self_review_work(clone)
                    fixtures.run_workflow_python(clone, plant)

                self.after = work
                before = self.managed_snapshot()
                with mock.patch.object(generator_run, "run", side_effect=AssertionError("generator started")):
                    record = self.step()
                self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
                block = record["codex_worker"]["landing"]
                self.assertEqual((block["outcome"], block["moved"]), ("refused", False), block)
                self.assertIn(cause, json.dumps(block))
                if profile:
                    self.assertIn(profile, json.dumps(block))
                self.assertEqual(self.managed_snapshot(), before)
                self.assertNotIn("regeneration", record)
                self.no_armour(record)
                self.assertTrue(self.clone().exists())  # kept, for the operator

    def test_a_generation_record_commit_from_the_worker_is_not_refused_by_the_profile(self) -> None:
        """The wrap-up profile allows a generation record: the clean run lands it (the checkpoint profile does not)."""
        self.worked()
        record = self.step()
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")


class GeneratorGuardPlantTest(SelfReviewSafety):
    """The only plant that refuses **after** the landing: a context list that names a path outside the repository."""

    def test_a_context_list_naming_a_file_outside_the_repository_ends_at_the_regeneration_gate(self) -> None:
        canary = self.tmp / "outside" / "canary.txt"
        canary.parent.mkdir()
        canary.write_text(CANARY + "\n")
        self.worked()
        worked = self.after

        def with_list(clone: Path) -> None:
            worked(clone)
            (clone / ITEM / "current" / "CONTEXT_FILES.txt").write_text(f"{canary}\n")

        self.after = with_list
        base = _git(self.root, "rev-parse", "HEAD").strip()
        with mock.patch.object(workflow_contract, "run_in_place", wraps=workflow_contract.run_in_place) as spy:
            record = self.step()
        self.assertEqual(spy.call_count, 0, "a generator process was started")
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        found = record["reconciliation_evidence"]
        self.assertEqual(found["code"], job.CARRY_BUNDLE_GATE_CODE)
        self.assertEqual(found["regeneration"]["cause"], "copy_source_unsafe", found["regeneration"])
        self.assertEqual(record["regeneration"]["stage"], "refused")
        # retained: the landed commits (the generation-record commit included), the changed state, the carried files
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{base}..HEAD").strip(), "1")
        self.assertIn(f"Workflow-Bundle-Generation-Record: {WID}/1", _git(self.root, "log", "-1", "--format=%B"))
        self.assertEqual(_entry(self.root)["implementation_revision"], 1)
        carried = (self.root / ITEM / "current" / "CONTEXT_FILES.txt").read_text()
        self.assertIn(str(canary), carried)
        for path in sorted((self.root / ITEM).rglob("*")):
            if path.is_file() and not path.is_symlink() and path.name != "CONTEXT_FILES.txt":
                self.assertNotIn(CANARY.encode(), path.read_bytes(), str(path))
        self.no_armour(record)


if __name__ == "__main__":
    unittest.main()
