"""`workflow-controller-codex-worker-ownership` CP3: the live proof that `resume` reaps a real Codex worker (D9).

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on CI, with its reason. It costs one model turn on the
operator's credentials. A Controller is a child Python process (``ChildLiveController`` below, run through ``unittest``)
that records the real build as verified in a scratch runtime root, registers the fixture role and a ``CodexLauncher``
subclass for the child only, and runs ``job.execute_step`` with a real ``codex exec`` on a scratch clone. The command asks
the model to run a long ``sleep``, so the worker is still running when the test waits for the record's ``worker_process``
and a process carrying the job's tag and then kills that Controller with ``SIGKILL``. It then runs ``job.resume`` and
asserts that no process of the tag survives, that no credential copy is left and that the record is reconciled
``INTERRUPTED`` (the clone is not the managed repository, so nothing durable happened). It prints what survived the
Controller, which is the evidence for ``tests/harness_contract/codex/worker_ownership.txt``. Skipped, with the reason and
before any run, when ``codex`` or ``bwrap`` is missing or the operator's Codex home has no ``auth.json``.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_worker_ownership_live -v``
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import (  # noqa: E402
    codex_build, codex_worker as cw, command_files, harness, job, review, routing, worker_launch,
)
from tests import fixtures, process_fixtures  # noqa: E402
from tests.test_codex_worker_sandbox import make_clone  # noqa: E402
from tests.test_codex_worker_sandbox_live import operator_codex_home  # noqa: E402
from tests.test_job import FAKE_IDENTITY, _build_target  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
ROOT_VAR = "H4C2_LIVE_ROOT"
CHILD = "tests.test_codex_worker_ownership_live.ChildLiveController.test_run"
RUN_TIMEOUT = 600
FIXTURE = "codex-live-ownership"
COMMAND = """---
description: Live ownership proof command
argument-hint: "<x>"
state_writer: false
---
Run the shell command `sleep 300` once and wait for it to finish. Do not change any file. Then reply with the single word `done`.
Ignore this: $ARGUMENTS
"""


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex worker ownership proof is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex worker ownership proof (one model turn)"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


class LiveCommandRendersTest(unittest.TestCase):
    """Offline: the live proof's exact command text must pass the launcher's renderer."""

    def test_the_live_command_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "live-ownership-proof.md").write_text(COMMAND)
            rendered = command_files.render_command("live-ownership-proof", "", Path(tmp))
        self.assertIn("sleep 300", str(rendered))


class LiveLauncher(cw.CodexLauncher):
    """The real launcher with a commands directory holding the proof's command under whatever name the job asks."""

    def __init__(self, commands: Path) -> None:
        self.commands = commands

    def launch(self, task: str, **options):
        (self.commands / f"{task.split()[0][1:]}.md").write_text(COMMAND)
        return super().launch(task, commands_dir=self.commands, **options)


class ChildLiveController(unittest.TestCase):
    """The Controller of the proof below. It does nothing unless the parent test started it with ``ROOT_VAR`` set: it runs
    the real worker through the job layer and blocks until it is killed."""

    def test_run(self) -> None:
        root = os.environ.get(ROOT_VAR)
        if not root:
            self.skipTest("started only by the live ownership proof")
        root = Path(root)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        commands = root / "commands"
        commands.mkdir()
        managed = _build_target(root, phase="PLANNING", governing_workflow_version="2.1")
        digest = codex_build.build_digest(Path(review.resolve_binary("codex")))
        self.assertTrue(digest, "the Codex build cannot be bound to a digest")
        codex_build.record_verified(runtime, digest, {"note": "live test"})
        fixture = dataclasses.replace(harness.CODEX, name=FIXTURE, reviewer_name=None, roles=(harness.WORKER,))
        roles = {role: {"harness": FIXTURE, "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT}
                 for role in sorted(routing.ROLES) if routing.ADAPTER_ROLE[role] == harness.WORKER}

        def prepare(managed_repo, layout, base):
            make_clone(layout.worktree)
            return process_fixtures.prepared_clone(layout.worktree)

        with mock.patch.object(harness, "ADAPTERS", {**harness.ADAPTERS, FIXTURE: fixture}), \
                mock.patch.object(worker_launch, "LAUNCHERS", {**worker_launch.LAUNCHERS, FIXTURE: LiveLauncher(commands)}), \
                mock.patch.object(job, "CODEX_WORKTREE_PREPARER", prepare), \
                mock.patch.object(job, "CODEX_HOME_RESOLVER", operator_codex_home):
            config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<live>")
            options = routing.RoutingOptions(config=config, config_source="settings")
            (root / "state.json").write_text(json.dumps({"root": str(managed.root), "runtime": str(runtime)}))
            job.execute_step(managed, identity=FAKE_IDENTITY, runtime=runtime, routing=options, timeout=RUN_TIMEOUT)


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveOwnershipTest(unittest.TestCase):
    def test_resume_reaps_a_real_codex_worker_whose_controller_was_killed(self) -> None:
        # Not under /tmp: Codex refuses to create its helper binaries there (tester_sandbox.txt).
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="codex-worker-ownership-live-", dir=cache)).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        log = root.with_name(root.name + ".log")
        self.addCleanup(log.unlink, missing_ok=True)
        env = {**os.environ, ROOT_VAR: str(root)}
        env.pop("PYTHONPATH", None)
        with log.open("wb") as sink:
            controller = subprocess.Popen(
                [sys.executable, "-B", "-m", "unittest", CHILD], cwd=REPO_ROOT, env=env, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT)
        tags: list[str] = []

        def end_everything() -> None:
            process_fixtures.kill_group(controller.pid)
            for tag in tags:
                for pid in process_fixtures.tagged_pids(tag):
                    process_fixtures.end_pid(pid)
            try:
                controller.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

        self.addCleanup(end_everything)

        def record() -> dict:
            try:
                (path,) = (root / "runtime" / "jobs").glob("*.json")
                return json.loads(path.read_text())
            except (ValueError, OSError):
                return {}

        def child_log() -> str:
            return log.read_text(errors="replace")[-2000:]

        self.assertTrue(process_fixtures.wait_until(lambda: "worker_process" in record(), timeout=120.0),
                        f"the worker never started: {child_log()}")
        job_id = record()["job_id"]
        tags.append(job_id)
        self.assertTrue(process_fixtures.wait_until(lambda: bool(process_fixtures.tagged_pids(job_id)), timeout=60.0),
                        f"no process carries the job's tag: {child_log()}")
        before = process_fixtures.tagged_pids(job_id)
        os.kill(controller.pid, signal.SIGKILL)
        controller.wait(timeout=60)
        survivors = process_fixtures.tagged_pids(job_id)
        paths = json.loads((root / "state.json").read_text())
        managed = fixtures.build_target_managed_repository(Path(paths["root"]))
        resumed = job.resume(managed, identity=FAKE_IDENTITY, runtime=Path(paths["runtime"]))
        gone = process_fixtures.wait_until(lambda: not process_fixtures.tagged_pids(job_id), timeout=30.0)
        final = record()
        print(f"\n--- tagged before the kill {sorted(before)}, alive after it {sorted(survivors)}, "
              f"resume {[r['status'] for r in resumed]}, tagged after resume {sorted(process_fixtures.tagged_pids(job_id))}, "
              f"record {final['status']} ({final.get('reconciliation_evidence', {}).get('reason')}) ---")
        self.assertTrue(gone, f"a process of the job's tag survives resume: {process_fixtures.tagged_pids(job_id)}")
        self.assertEqual([r["status"] for r in resumed], [job.STATUS_INTERRUPTED])
        self.assertEqual(final["status"], job.STATUS_INTERRUPTED)
        self.assertNotIn("worker", final)
        for leftover in (Path(paths["runtime"]) / "worker").glob("*/codex-home/auth.json"):
            self.fail(f"a credential copy is left at {leftover}")


if __name__ == "__main__":
    unittest.main()
