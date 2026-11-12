"""`workflow-controller-h4f` CP4 (D7): the checkpoint role routed to Codex, end to end through ``job.execute_step``.

A real managed repository at ``IMPLEMENTING`` with a real Workflow installed, the real preparer's clone, the real
Codex launcher (and its guard) over the fake ``codex`` of ``tests/fake_codex.py`` and the real lander. The fake worker
performs a checkpoint's whole Workflow bookkeeping itself **during** its run, so the launcher's post-run guard sees it.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import (  # noqa: E402
    codex_worker_landing, codex_worker_sandbox as ws, codex_worker_tree, harness, job, routing, review, worker_launch,
)
from tests import fixtures  # noqa: E402
from tests.test_job import FAKE_IDENTITY  # noqa: E402
from tests.test_job_codex_worker import FIXTURE, per_test_job_id  # noqa: E402
from tests.test_job_codex_worker_landing import LandingFixture, git  # noqa: E402
from tests.test_protocol_outcome import WID, _Env  # noqa: E402

STATE = "docs/ai-workflow/WORKFLOW_STATE.json"

#: What a checkpoint's invocation does in the worker's clone, in the order ``/milestone-implement`` gives (1d, 1e, 1f):
#: the identity, the claim, the guarded transition to ``IN_PROGRESS``, the work, the guarded completion, the commit
#: (trailers in the final paragraph) and the release.
CHECKPOINT_CODE = """
import datetime, json, subprocess
root, wid = Path.cwd(), sys.argv[1]
def git(*args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
now = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
registry = json.loads((root / f"docs/ai-workflow/registry/{wid}-registry.json").read_text())
ws.write_worktree_identity(root, wid, now=now())
token = ws.claim_checkpoint(root, wid, "CP1", now=now())["owner_token"]
with ws.owner_mutation(root, wid, token, checkpoint_id="CP1", step="1d", step_class=ws.DESTRUCTIVE, now=now()):
    ws.write_worktree_identity(root, wid, now=now())
    head = git("rev-parse", "HEAD")
    ws.state_transaction(root, lambda s: ws.transition_checkpoint_in_progress(s, wid, "CP1", start_commit=head, now=now()))
(root / "CP1.txt").write_text("CP1 implemented\\n")
with ws.owner_mutation(root, wid, token, checkpoint_id="CP1", step="1f-commit", step_class=ws.DESTRUCTIVE, now=now()):
    ws.state_transaction(root, lambda s: ws.complete_checkpoint(s, wid, "CP1", registry, now=now(), repo_root=root))
    git("add", "-A")
    git("-c", "user.name=w", "-c", "user.email=w@w", "commit", "-q", "-m",
        "Implement CP1\\n\\nWorkflow-Checkpoint: CP1\\nWorkflow-Work-Item: " + wid)
assert ws.committed_checkpoint_status(root, wid, "CP1") == "COMPLETE"
ws.release_checkpoint(root, wid, "CP1", owner_token=token, now=now())
"""



def implementer_route(route) -> bool:
    return route.role in routing.IMPLEMENT_ROLES and route.harness == FIXTURE


class ImplementerEnvFixture(LandingFixture):
    """A managed repository at ``IMPLEMENTING`` and the checkpoint role routed to the fake Codex through the real
    implementer route (the fixture adapter declares the implementer role; its launcher is the real Codex launcher)."""

    def setUp(self) -> None:
        super().setUp()
        adapter = dataclasses.replace(harness.ADAPTERS[FIXTURE], roles=(harness.WORKER, harness.IMPLEMENTER))
        self.enterContext(mock.patch.object(harness, "ADAPTERS", {**harness.ADAPTERS, FIXTURE: adapter}))
        self.enterContext(mock.patch.object(worker_launch, "LAUNCHERS", {
            **worker_launch.LAUNCHERS, (FIXTURE, harness.IMPLEMENTER): self.launcher}))
        self.enterContext(mock.patch.object(job, "_is_codex_implement_route", implementer_route))
        roles = {"milestone-implement": {"harness": FIXTURE, "model": review.DEFAULT_MODEL,
                                         "effort": review.DEFAULT_EFFORT}}
        config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<test>")
        self.options = routing.RoutingOptions(config=config, config_source="settings")
        self.env = _Env(self, "ready", implementing=True)
        self.managed = self.env.inspect()
        self.runtime = self.env.runtime
        self.base = git(self.env.root, "rev-parse", "HEAD")

    def script(self, **exec_spec) -> None:
        super().script(**exec_spec)
        self.recorded = False  # the fake binary's bytes are its digest

    def run_step(self, **kw) -> dict:
        if not self.recorded:
            self.record()
        kw.setdefault("timeout", 60)
        return job.execute_step(self.env.inspect(), identity=FAKE_IDENTITY, runtime=self.runtime, routing=self.options,
                                **kw)

    def managed_snapshot(self) -> dict:
        root = self.env.root
        return {"refs": git(root, "for-each-ref"), "head": git(root, "rev-parse", "HEAD"),
                "status": git(root, "status", "--porcelain", "--untracked-files=all"),
                "state": (root / STATE).read_bytes(), "index": git(root, "ls-files", "-s")}

    def branch_tip(self) -> str:
        return git(self.env.root, "rev-parse", "HEAD")

    @property
    def job_id(self) -> str:
        return per_test_job_id(self.id(), 1)


class CleanCheckpointTest(ImplementerEnvFixture):
    def test_a_checkpoint_the_worker_performs_during_its_run_lands_and_the_job_ends_finished(self) -> None:
        self.script(message="done", workflow_python=[[CHECKPOINT_CODE, WID]])
        record = self.run_step()
        (execution,) = self.execs()
        self.assertEqual([r["exit"] for r in execution["ran"] if r["layer"] == "workflow_python"], [0],
                         execution["ran"])
        self.assertEqual(self.launcher.results[0].subtype, "success", self.launcher.results[0].stderr)
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertNotEqual(record["worker"]["subtype"], "sandbox_breach")
        self.assertTrue(record["transition_verified"])
        block = record["codex_worker"]["landing"]
        self.assertEqual((block["outcome"], block["moved"], block["clone_kept"]), ("landed", True, False))
        self.assertEqual(block["tip"], self.branch_tip())
        item = json.loads((self.env.root / STATE).read_text())["work_items"][WID]
        self.assertEqual(item["checkpoints"]["CP1"]["status"], "COMPLETE")
        self.assertEqual(item["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        self.assertTrue((self.env.root / "CP1.txt").exists())
        self.assertEqual(git(self.env.root, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(git(self.env.root, "for-each-ref", "refs/controller"), "")
        # the claim the worker made and released is in the clone's own common dir, never the managed one
        self.assertFalse((self.env.root / ".git" / "ai-workflow").exists())
        self.assertFalse((self.job_dir() / "tree").exists())
        self.assertNotIn("PRIVATE KEY", json.dumps(record))


class IndependenceTest(unittest.TestCase):
    """The independence check counts the checkpoint role: a lane that routes only ``milestone-implement`` to Codex is a
    mixed lane, so a Codex reviewer is refused as not distinct (``review_mode``)."""

    def test_only_the_checkpoint_role_on_codex_makes_the_lane_mixed_and_a_codex_reviewer_not_distinct(self) -> None:
        from controller import review_mode
        from tests.test_review_independence import choose_review
        from tests.test_review_mode import codex_workers, reviewer
        patch, options = codex_workers(list(routing.IMPLEMENT_ROLES))
        self.addCleanup(patch.stop)
        self.assertEqual(review_mode.worker_families(options), frozenset({"anthropic", "openai"}))
        choice, _ = choose_review(options, reviewer("codex"), review_mode.MODE_AUTO, "implementation")
        self.assertEqual(choice.kind, "refused")
        self.assertIn("more than one family", choice.reason)


if __name__ == "__main__":
    unittest.main()
