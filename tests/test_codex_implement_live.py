"""`workflow-controller-h4f` CP5 (D8): the live proof of the checkpoint role routed to Codex.

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on ``CI``, with its reason. It costs one real model
turn on the operator's credentials. It builds the implementer tests' scratch managed repository (a real Workflow
installed, the work item at ``IMPLEMENTING`` with a current plan approval and a registry of one trivial checkpoint),
records the real ``codex`` build as verified in a scratch runtime root, routes ``milestone-implement`` to ``codex`` and
runs ``job.execute_step`` with a real ``codex exec`` in a private clone, through the real preparer, launcher guard and
lander. It asserts that the checkpoint's commit landed on the managed branch, that the state file shows the checkpoint
complete, that the clone is gone and that the record's ``usage_record`` is present. It prints the status, the landing
block, the worker's last message and the measured tokens, which the operator copies into
``tests/harness_contract/codex/implement_role.txt``. The run also answers whether a worker can run a checkpoint's narrow
test inside the H4b1 sandbox profile: the result is printed either way, and a failure is a follow-up issue, not a change
of this item's scope. The scratch folders are under ``~/.cache`` because Codex refuses to create its helper binaries
under ``/tmp``. Skipped, with the reason and before any run, when ``codex`` or ``bwrap`` is missing or the operator's
Codex home has no ``auth.json``. The squash does not wait for the operator's run.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_implement_live -v``
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_build, codex_worker as cw, harness, job, review, routing, worker_launch  # noqa: E402
from tests.test_codex_worker_sandbox_live import operator_codex_home  # noqa: E402
from tests.test_job import FAKE_IDENTITY  # noqa: E402
from tests.test_job_codex_worker_landing import git  # noqa: E402
from tests.test_protocol_outcome import WID, _Env  # noqa: E402

STATE = "docs/ai-workflow/WORKFLOW_STATE.json"
ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
RUN_TIMEOUT = 1200


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex checkpoint is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex checkpoint (one model turn)"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveImplementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scratch = Path.home() / ".cache"
        scratch.mkdir(exist_ok=True)
        previous = tempfile.tempdir
        tempfile.tempdir = str(scratch)
        cls.addClassCleanup(setattr, tempfile, "tempdir", previous)

    def setUp(self) -> None:
        self.env = _Env(self, "ready", implementing=True)
        self.enterContext(mock.patch.object(job, "CODEX_HOME_RESOLVER", operator_codex_home))
        roles = {"milestone-implement": {"harness": "codex", "model": review.DEFAULT_MODEL,
                                         "effort": review.DEFAULT_EFFORT}}
        config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<test>")
        self.options = routing.RoutingOptions(config=config, config_source="settings")
        digest = codex_build.build_digest(Path(review.resolve_binary("codex")))
        self.assertTrue(digest, "the Codex build cannot be bound to a digest")
        codex_build.record_verified(self.env.runtime, digest, {"note": "live test"})

    def test_a_real_codex_checkpoint_lands_and_the_state_shows_it_complete(self) -> None:
        root = self.env.root
        base = git(root, "rev-parse", "HEAD")
        record = job.execute_step(self.env.inspect(), identity=FAKE_IDENTITY, runtime=self.env.runtime,
                                  routing=self.options, timeout=RUN_TIMEOUT)
        usage = record.get("usage_record")
        block = (record.get("codex_worker") or {}).get("landing")
        item = json.loads((root / STATE).read_text())["work_items"][WID]
        print(f"\n--- milestone-implement: status {record['status']}, landing {block}, "
              f"subtype {(record.get('worker') or {}).get('subtype')} ---\n--- usage_record ---\n{usage}\n"
              f"--- checkpoints ---\n{item.get('checkpoints')}\n--- phase ---\n{item.get('phase')}\n"
              f"--- evidence ---\n{record.get('reconciliation_evidence')}")
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertTrue(record["codex_worker"].get("implementer"))
        self.assertEqual((block["outcome"], block["moved"], block["clone_kept"]), ("landed", True, False))
        self.assertNotEqual(git(root, "rev-parse", "HEAD"), base, "no checkpoint commit landed")
        self.assertEqual(item["checkpoints"]["CP1"]["status"], "COMPLETE")
        self.assertFalse((self.env.runtime / "worker" / record["job_id"] / "tree").exists(), "the clone is left")
        self.assertEqual(usage["harness"], "codex")
        self.assertGreater((usage["input"] or 0) + (usage["cache_read"] or 0), 0)
        self.assertGreater(usage["output"] or 0, 0)
        for leftover in self.env.runtime.glob("worker/*/codex-home/auth.json"):
            self.fail(f"a credential copy is left at {leftover}")


class LiveFlowTests(unittest.TestCase):
    """Offline: the live proof's seams exist, so a rename in the code it drives fails here rather than on the operator's run."""

    def test_the_names_the_live_proof_uses_exist(self) -> None:
        self.assertIn("milestone-implement", routing.IMPLEMENT_ROLES)
        self.assertIn(("codex", harness.IMPLEMENTER), worker_launch.LAUNCHERS)
        self.assertNotIn(("codex", harness.WORKER), worker_launch.LAUNCHERS)
        self.assertTrue(callable(job._is_codex_implement_route))
        self.assertTrue(callable(cw.launch_codex_worker))
        self.assertTrue(callable(operator_codex_home))

    def test_the_live_class_is_skipped_without_the_opt_in_and_on_ci(self) -> None:
        with mock.patch.dict(os.environ, {"CI": "true"}):
            self.assertIn("CI", skip_reason() or "")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CI", None)
            with mock.patch(f"{__name__}.ENABLED", False):
                self.assertIn("CONTROLLER_LIVE_CODEX_TESTS=1", skip_reason() or "")


if __name__ == "__main__":
    unittest.main()
