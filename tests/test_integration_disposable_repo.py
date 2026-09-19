"""Disposable managed-repository, real-Workflow-action integration
evidence (``REQ-T18``, capability 5,
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, CP9's own "Disposable
managed-repository integration evidence").

Proves the one capability no unit test can: that the Controller, run
exactly as an operator would run it, drives a **real** Workflow command
(``/milestone-plan``) against a disposable managed repository through a
**real** ``claude`` worker, and that the resulting Workflow lifecycle
transition is genuine and durable.

Opt-in: skipped unless ``CONTROLLER_LIVE_WORKER=1`` is set, since it
requires a live ``claude`` binary, network access and real spend -- none
of which the default suite may depend on. Per the plan, it is nevertheless
run for real, once, during CP9, with its output (worker ``session_id``,
pre-phase, post-phase, target commit, wall-clock and cost) recorded
verbatim in the implementation bundle's ``TEST_RESULTS.md`` and in the
functional-review checklist.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

REPO_ROOT = fixtures.REPO_ROOT
WORKFLOW_MANAGER_BIN = shutil.which("workflow-manager")
CLAUDE_BIN = shutil.which("claude")

_TRIVIAL_MILESTONE = """# Active Milestone

## Milestone

hello-file

## Goal

Add a file named `hello.txt` at the repository root containing exactly
the single line `hello, controller`. This is a disposable integration
fixture (`tests/test_integration_disposable_repo.py`, `REQ-T18`) -- the
smallest possible real change, chosen only to give `/milestone-plan`
something genuine to plan.

## Current checkpoint

None.

## Current blockers

None.

## Active plan

None. `/milestone-plan` records the plan document path on the work item's
`WORKFLOW_STATE.json` entry when a work item is created.

## Functional review checklist

Empty. `/prepare-functional-review` writes the numbered checklist for the
active work item into this section; `/apply-functional-review` and
`/accept-milestone` read it back from here.

<!--
This file is `workflow_state.FUNCTIONAL_CHECKLIST_PATH`. It is
repository-local state: workflow-manager generates it once at bootstrap and
never overwrites it on update.
-->
"""


def _source_is_dirty() -> bool:
    """The exact pathspec-scoped dirty check CP1's own ``materialise()``
    uses: the tree is dirty iff ``git status --porcelain -- controller
    pyproject.toml`` produces any output."""
    result = fixtures.run(
        ["git", "status", "--porcelain", "--", "controller", "pyproject.toml"],
        cwd=REPO_ROOT, check=False,
    )
    return bool(result.stdout.strip())


def _seed_target(target: Path) -> None:
    """Steps 1-3: a throwaway repository, a real Workflow v2.3.1
    installation, and a trivial committed milestone for `/milestone-plan`
    to plan."""
    target.mkdir(parents=True, exist_ok=True)
    fixtures.run(["git", "init", "-q"], cwd=target)
    fixtures.run(["git", "config", "user.email", "controller-live-test@example.invalid"], cwd=target)
    fixtures.run(["git", "config", "user.name", "Controller Live Test"], cwd=target)
    (target / "README.md").write_text("disposable integration fixture\n")
    fixtures.run(["git", "add", "-A"], cwd=target)
    fixtures.run(["git", "commit", "-q", "-m", "seed"], cwd=target)

    if WORKFLOW_MANAGER_BIN is not None:
        result = fixtures.run(
            [WORKFLOW_MANAGER_BIN, "bootstrap", str(target), "--profile", "full"],
            cwd=target, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"workflow-manager bootstrap failed (exit {result.returncode}): "
                f"{result.stdout}\n{result.stderr}"
            )
    else:
        # Fallback fixture detail, not a reimplementation of install
        # semantics: copy this repository's own frozen Workflow v2.3.1
        # installation. CP2 still asks the real Manager to verify the
        # result at `step` time; if the Manager refuses this fixture, the
        # test fails rather than proceeding.
        shutil.copytree(REPO_ROOT / ".claude" / "commands", target / ".claude" / "commands",
                         dirs_exist_ok=True)
        shutil.copytree(REPO_ROOT / "scripts", target / "scripts", dirs_exist_ok=True,
                         ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ai-review"))
        shutil.copytree(REPO_ROOT / "docs" / "ai-workflow", target / "docs" / "ai-workflow",
                         dirs_exist_ok=True)
        (target / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(
            json.dumps({"schema_version": 1, "active_work_item_id": None, "work_items": {}}, indent=2)
            + "\n"
        )
        shutil.copytree(REPO_ROOT / ".workflow-manager", target / ".workflow-manager",
                         dirs_exist_ok=True)

    (target / "docs" / "ACTIVE_MILESTONE.md").write_text(_TRIVIAL_MILESTONE)
    fixtures.run(["git", "add", "-A"], cwd=target)
    fixtures.run(["git", "commit", "-q", "-m", "seed trivial milestone"], cwd=target)


@unittest.skipUnless(
    os.environ.get("CONTROLLER_LIVE_WORKER") == "1",
    "requires a live claude binary, network access and real spend -- set "
    "CONTROLLER_LIVE_WORKER=1 to opt in",
)
class DisposableRepoRealWorkflowActionTest(unittest.TestCase):
    def test_real_milestone_plan_produces_a_durable_awaiting_local_plan_review_transition(self) -> None:
        self.assertIsNotNone(CLAUDE_BIN, "a live claude binary is required for this test")

        with tempfile.TemporaryDirectory(prefix="controller-live-") as td:
            tmp_root = Path(td)
            target = tmp_root / "target"
            runtime_root = tmp_root / "runtime"
            _seed_target(target)

            argv = ["--runtime-dir", str(runtime_root)]
            if _source_is_dirty():
                argv.append("--allow-dirty-source")
            argv += ["--permission-mode", "bypassPermissions", "step", str(target)]

            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT)
            env.pop("WORKFLOW_CONTROLLER_EXEC_HANDOFF", None)

            start = time.monotonic()
            proc = subprocess.run(
                [sys.executable, "-P", "-m", "controller", *argv],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=1800,
            )
            wall_clock = time.monotonic() - start

            self.assertEqual(
                proc.returncode, 0,
                f"`step` did not exit 0 (exit {proc.returncode})\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
            )

            state_path = target / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            work_items = state["work_items"]
            self.assertEqual(len(work_items), 1, f"expected exactly one work item, got {work_items!r}")
            work_item_id, entry = next(iter(work_items.items()))
            self.assertEqual(entry["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

            registry_path = entry.get("registry_path")
            mapping_path = entry.get("mapping_path")
            self.assertIsNotNone(registry_path)
            self.assertIsNotNone(mapping_path)
            self.assertTrue((target / registry_path).is_file())
            self.assertTrue((target / mapping_path).is_file())

            artifacts_path = (
                target / "docs" / "ai-workflow" / "registry" / f"{work_item_id}-artifacts.json"
            )
            self.assertTrue(
                artifacts_path.is_file(),
                f"expected an artifacts-declaration file at {artifacts_path}",
            )

            jobs_dir = runtime_root / "jobs"
            job_files = sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []
            self.assertEqual(len(job_files), 1, f"expected exactly one job record, got {job_files!r}")
            job_record = json.loads(job_files[0].read_text())
            self.assertEqual(job_record["status"], "FINISHED")
            self.assertTrue(job_record["transition_verified"])
            # `job.py`'s own schema (`_worker_dict`/`execute_step`):
            # `worker_outcome` is the plain classification string itself
            # (`worker.SUCCESS`/etc.), never a dict; the worker's own
            # session id, exit code and timing live under the sibling
            # `"worker"` block. B1 (self-review round, `docs/ACTIVE_
            # MILESTONE.md`'s "Current blockers"): this test previously
            # read `worker_outcome` as a dict with `session_id`/
            # `classification`/`duration_seconds` keys, a schema that has
            # never existed in `job.py`'s actual records.
            worker = job_record["worker"]
            worker_outcome = job_record["worker_outcome"]
            self.assertIsNotNone(worker.get("session_id"))

            evidence = {
                "worker_session_id": worker.get("session_id"),
                "worker_outcome_classification": worker_outcome,
                "pre_phase": job_record["pre_state"]["phase"],
                "post_phase": job_record["observed_phase_after"],
                "target_repo_commit_before": job_record["pre_state"].get("target_head"),
                "wall_clock_seconds": round(wall_clock, 1),
                "worker_duration_ms": worker.get("duration_ms"),
            }
            print("DISPOSABLE_REPO_INTEGRATION_EVIDENCE " + json.dumps(evidence))


if __name__ == "__main__":
    unittest.main()
