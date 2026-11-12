"""`workflow-controller-h4e3` CP5 (D11): the live proof of a routed Codex apply, one role at a time.

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on ``CI``, with its reason. Each role costs one real
model turn on the operator's credentials. It builds the apply tests' scratch repository (the installed Workflow, a
generated bundle, the work item at ``REVISING_PLAN`` or ``APPLYING_REVIEW_FEEDBACK`` with one trivial finding), records
the real ``codex`` build as verified in a scratch runtime root, routes the apply role to ``codex`` and runs
``job.execute_step`` with a real ``codex exec`` in a private clone that holds the carried working set. It asserts that
the job stopped at the Controller's own gate ``carry_bundle_regeneration_pending``, that the clone is gone, that the
managed repository holds what the ending allows (the plan role: the carried-out members and no commit; the
implementation role: the landed range over the carry base) and that the record's ``usage_record`` is present. It prints
the status, the gate and the measured tokens, which the operator copies into
``tests/harness_contract/codex/apply_roles.txt``. The scratch folders are under ``~/.cache`` because Codex refuses to
create its helper binaries under ``/tmp``. Skipped, with the reason and before any run, when ``codex`` or ``bwrap`` is
missing or the operator's Codex home has no ``auth.json``. The squash does not wait for the operator's run.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_apply_live -v``
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

from controller import codex_build, codex_carry, codex_worker as cw, harness, job, review, routing, worker_launch  # noqa: E402
from tests.test_codex_worker_sandbox_live import operator_codex_home  # noqa: E402
from tests.test_job_codex_apply import ImplCase, PlanCase, REAL_LAUNCH  # noqa: E402
from tests.test_review_recorder import _entry  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
RUN_TIMEOUT = 1200


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex apply is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex apply (one model turn per role)"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


class LiveApplyMixin:
    """The offline apply fixture with the real ``codex`` and the operator's Codex home in place of the fake."""

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

    def live_launch(self, task, *, runtime_root, job_id, operator_home, cwd, timeout, model, effort, prompt=None,
                    on_spawn=None, **rest):
        self.launched.append({"task": task, "prompt": prompt, "cwd": cwd})
        return REAL_LAUNCH(task, runtime_root=runtime_root, job_id=job_id, operator_home=operator_home, cwd=cwd,
                           timeout=timeout, on_spawn=on_spawn, model=model, effort=effort, prompt=prompt)


class LiveApplyTests:
    def test_a_real_codex_apply_ends_at_the_regeneration_gate(self) -> None:
        self.record()
        head_before = self.managed_snapshot()[3]
        record = self.step(timeout=RUN_TIMEOUT)

        usage = record["usage_record"]
        evidence = record.get("reconciliation_evidence") or {}
        print(f"\n--- {self.ROLE}: status {record['status']}, gate {evidence.get('code')}, "
              f"carry stage {record['codex_carry']['stage']} ---\n--- usage_record ---\n{usage}\n"
              f"--- entry ---\n{ {k: _entry(self.root).get(k) for k in ('phase', 'plan_revision', 'implementation_revision')} }")
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, evidence)
        self.assertEqual(evidence.get("code"), job.CARRY_BUNDLE_GATE_CODE)
        self.assertEqual(record["codex_carry"]["stage"], "done")
        self.assertFalse(self.clone().exists(), "the clone is left")
        self.assert_head_as_the_ending_allows(head_before)
        self.assertEqual(usage["harness"], "codex")
        self.assertGreater((usage["input"] or 0) + (usage["cache_read"] or 0), 0)
        self.assertGreater(usage["output"] or 0, 0)
        for leftover in self.runtime.glob("worker/*/codex-home/auth.json"):
            self.fail(f"a credential copy is left at {leftover}")


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LivePlanApplyTests(LiveApplyTests, LiveApplyMixin, PlanCase):
    def assert_head_as_the_ending_allows(self, head_before: str) -> None:
        self.assertEqual(self.managed_snapshot()[3], head_before, "the plan role makes no commit")


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveImplementationApplyTests(LiveApplyTests, LiveApplyMixin, ImplCase):
    def assert_head_as_the_ending_allows(self, head_before: str) -> None:
        self.assertNotEqual(self.managed_snapshot()[3], head_before, "the implementation role's range is landed")


class LiveFlowTests(unittest.TestCase):
    """Offline: the live proof's seams exist, so a rename in the code it drives fails here rather than on the operator's run."""

    def test_the_names_the_live_proof_uses_exist(self) -> None:
        self.assertEqual(job.CARRY_BUNDLE_GATE_CODE, "carry_bundle_regeneration_pending")
        self.assertNotIn(job.CARRY_BUNDLE_GATE_CODE, codex_carry.CAUSES)
        self.assertTrue(callable(REAL_LAUNCH))
        for role in (routing.APPLY_PLAN_REVIEW, routing.APPLY_IMPLEMENTATION_REVIEW):
            self.assertIn(role, routing.LOCAL_APPLY_ROLES)
            self.assertIn(("codex", harness.LOCAL_APPLIER), worker_launch.LAUNCHERS)

    def test_the_live_classes_use_the_offline_fixtures_with_the_apply_roles(self) -> None:
        self.assertEqual(LivePlanApplyTests.ROLE, routing.APPLY_PLAN_REVIEW)
        self.assertEqual(LiveImplementationApplyTests.ROLE, routing.APPLY_IMPLEMENTATION_REVIEW)


if __name__ == "__main__":
    unittest.main()
