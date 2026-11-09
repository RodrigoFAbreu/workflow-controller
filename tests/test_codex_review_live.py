"""`workflow-controller-h4e2b` CP4 (D6): the live proof of a routed Codex review, one stage at a time.

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on ``CI``, with its reason. Each stage costs one real
model turn on the operator's credentials. It builds the recorder tests' scratch repository (the installed Workflow, a
generated bundle, the work item at ``AWAITING_LOCAL_PLAN_REVIEW`` or ``AWAITING_LOCAL_IMPLEMENTATION_REVIEW``), records
the real ``codex`` build as verified in a scratch runtime root, routes the review role to ``codex`` and runs
``job.execute_step`` with a real ``codex exec`` in a private clone. It asserts that the verdict is recorded, that the
managed tree outside the feedback file and the state is unchanged, that the clone is gone and that the job record's
``usage_record`` is present, and it prints the measured tokens, the price-table cost and any weekly reading, which the
operator copies into ``tests/harness_contract/codex/review_role.txt`` (the cost table of ``docs/guide/harnesses.md``
takes its figures from there). The scratch folders are under ``~/.cache`` because Codex refuses to create its helper
binaries under ``/tmp``. Skipped, with the reason and before any run, when ``codex`` or ``bwrap`` is missing or the
operator's Codex home has no ``auth.json``.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_review_live -v``
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_build, codex_review_role as crr, codex_worker as cw, job, review, routing, worker  # noqa: E402
from tests.test_codex_worker_sandbox_live import operator_codex_home  # noqa: E402
from tests.test_job_codex_review import REAL_LAUNCH, ReviewJobCase  # noqa: E402
from tests.test_review_recorder import FEEDBACK, STATE  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
RUN_TIMEOUT = 900


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex review is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex review (one model turn per stage)"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


class LiveReviewCase(ReviewJobCase):
    """The offline job fixture with the real ``codex`` and the operator's Codex home in place of the fake."""

    @classmethod
    def setUpClass(cls) -> None:
        scratch = Path.home() / ".cache"
        scratch.mkdir(exist_ok=True)
        previous = tempfile.tempdir
        tempfile.tempdir = str(scratch)
        cls.addClassCleanup(setattr, tempfile, "tempdir", previous)
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.object(job, "CODEX_HOME_RESOLVER", operator_codex_home))
        self.enterContext(mock.patch.object(cw, "launch_codex_worker", self.live_launch))

    def record(self) -> str:
        digest = codex_build.build_digest(Path(review.resolve_binary("codex")))
        self.assertTrue(digest, "the Codex build cannot be bound to a digest")
        codex_build.record_verified(self.runtime, digest, {"note": "live test"})
        self.recorded = True
        return digest

    def live_launch(self, task, *, runtime_root, job_id, operator_home, cwd, timeout, model, effort, prompt,
                    on_spawn=None, **rest):
        self.launched.append({"task": task, "prompt": prompt, "cwd": cwd})
        return REAL_LAUNCH(task, runtime_root=runtime_root, job_id=job_id, operator_home=operator_home, cwd=cwd,
                           timeout=timeout, on_spawn=on_spawn, model=model, effort=effort, prompt=prompt)

    def outside_the_record(self) -> tuple:
        """The managed repository apart from the state file and the feedback file the recorder writes."""
        status = [line for line in self.snapshot()[1].splitlines() if not line.endswith((STATE, FEEDBACK))]
        return (status, self.refs())


class LiveReviewTests:
    def test_a_real_codex_review_is_recorded_through_execute_step(self) -> None:
        self.record()
        before = self.outside_the_record()
        record = self.step(timeout=RUN_TIMEOUT)

        block = record["codex_review"]
        usage = record["usage_record"]
        print(f"\n--- {self.STAGE}: status {record['status']}, worker_outcome {record['worker_outcome']}, "
              f"result {block['result']['code']} ---\n--- usage_record ---\n{usage}\n--- usage ---\n{record.get('usage')}")
        self.assertEqual(record["worker_outcome"], worker.SUCCESS, record.get("reconciliation_evidence"))
        self.assertIn(block["result"]["code"], (crr.RECORDED, crr.RECORD_PENDING), block)
        self.assertTrue((self.root / FEEDBACK).is_file(), "no feedback file was written")
        self.assertEqual(self.outside_the_record(), before)
        self.assertFalse(self.layout.worktree.exists(), "the clone is left")
        self.assertEqual(usage["harness"], "codex")
        self.assertGreater((usage["input"] or 0) + (usage["cache_read"] or 0), 0)
        self.assertGreater(usage["output"] or 0, 0)
        self.assert_valid(record)
        for leftover in self.runtime.glob("worker/*/codex-home/auth.json"):
            self.fail(f"a credential copy is left at {leftover}")


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LivePlanReviewTests(LiveReviewTests, LiveReviewCase):
    STAGE = "plan"
    ROLE = routing.REVIEW_PLAN


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveImplementationReviewTests(LiveReviewTests, LiveReviewCase):
    STAGE = "implementation"
    ROLE = routing.REVIEW_IMPLEMENTATION


class LiveFlowTests(unittest.TestCase):
    """Offline: the live proof's seams exist, so a rename in the code it drives fails here rather than on the operator's run."""

    def test_the_names_the_live_proof_uses_exist(self) -> None:
        for name in ("RECORDED", "RECORD_PENDING"):
            self.assertTrue(hasattr(crr, name), name)
        self.assertTrue(callable(REAL_LAUNCH))
        self.assertIn(routing.REVIEW_PLAN, routing.LOCAL_REVIEW_ROLES)
        self.assertIn(routing.REVIEW_IMPLEMENTATION, routing.LOCAL_REVIEW_ROLES)


if __name__ == "__main__":
    unittest.main()
