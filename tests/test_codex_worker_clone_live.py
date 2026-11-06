"""`workflow-controller-h4e` CP4: the live proof of a Codex worker's private clone and its landing (D8).

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on CI, with its reason. It costs one model turn on the
operator's credentials: it records the real build as verified in a scratch runtime root, registers the fixture role and a
``CodexLauncher`` subclass for the test only, and runs ``job.execute_step`` with the real preparer
(``codex_worker_tree.prepare``) and the real lander (``codex_worker_landing.land``) over a scratch managed repository.
The worker is asked to create one file and make one plain ``git commit`` (no ``-c user.*``). The test asserts that the
commit landed with the D1 author, that the managed branch is at it with a clean tree, that the record carries a ``landed``
block and that no landing ref and no clone are left. The job then ends ``FINISHED`` or ``FAILED`` by the unchanged
reconciliation rules (a legacy target whose phase did not change ends ``FAILED``); the test prints the status and does not
assert it away. Skipped, with the reason and before any run, when ``codex`` or ``bwrap`` is missing or the operator's Codex
home has no ``auth.json``. The output is the capture ``tests/harness_contract/codex/worker_clone.txt``.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_worker_clone_live -v``
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import (  # noqa: E402
    codex_build, codex_worker as cw, codex_worker_landing, codex_worker_tree, command_files, harness, job, review,
    routing, worker, worker_launch,
)
from tests import fixtures  # noqa: E402
from tests.test_codex_worker_sandbox_live import operator_codex_home  # noqa: E402
from tests.test_job import FAKE_IDENTITY, _build_target  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
RUN_TIMEOUT = 600
FIXTURE = "codex-live-clone"
AUTHOR = "Codex worker"
PROOF_FILE = "worker-proof.txt"
COMMAND = """---
description: Live clone proof command
argument-hint: "<x>"
state_writer: false
---
In the current directory create a file named `worker-proof.txt` holding the single line `landed by the worker`, then run
exactly `git add worker-proof.txt` and `git commit -m "worker proof"` (no -c options, no git config). Reply with the single
word `done`.
Ignore this: $ARGUMENTS
"""


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex worker clone proof is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex worker clone proof (one model turn)"
    if shutil.which("codex") is None:
        return "codex is not installed"
    if shutil.which("bwrap") is None:
        return "bwrap is not installed"
    if not (operator_codex_home() / "auth.json").is_file():
        return f"no Codex credential file at {operator_codex_home() / 'auth.json'}"
    return None


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout.strip()


class LiveCommandRendersTest(unittest.TestCase):
    """Offline: the live proof's exact command text must pass the launcher's renderer."""

    def test_the_live_command_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "live-clone-proof.md").write_text(COMMAND)
            rendered = command_files.render_command("live-clone-proof", "", Path(tmp))
        self.assertIn(PROOF_FILE, str(rendered))


class LiveLauncher(cw.CodexLauncher):
    """The real launcher with a commands directory holding the proof's command under whatever name the job asks."""

    def __init__(self, commands: Path) -> None:
        self.commands = commands

    def launch(self, task: str, **options):
        (self.commands / f"{task.split()[0][1:]}.md").write_text(COMMAND)
        return super().launch(task, commands_dir=self.commands, **options)


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveCloneLandingTest(unittest.TestCase):
    def test_a_real_codex_commit_in_the_clone_lands_on_the_managed_branch(self) -> None:
        # Not under /tmp: Codex refuses to create its helper binaries there (tester_sandbox.txt).
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="codex-worker-clone-live-", dir=cache)).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        commands = root / "commands"
        commands.mkdir()
        managed = _build_target(root, phase="PLANNING", governing_workflow_version="2.1")
        fixtures.commit_all(managed.root, "the state")  # the clone is made only from a clean managed tree (D2)
        base = git(managed.root, "rev-parse", "HEAD")
        branch = git(managed.root, "symbolic-ref", "--short", "HEAD")
        digest = codex_build.build_digest(Path(review.resolve_binary("codex")))
        self.assertTrue(digest, "the Codex build cannot be bound to a digest")
        codex_build.record_verified(runtime, digest, {"note": "live test"})

        fixture = dataclasses.replace(harness.CODEX, name=FIXTURE, reviewer_name=None, roles=(harness.WORKER,))
        roles = {role: {"harness": FIXTURE, "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT}
                 for role in sorted(routing.ROLES) if routing.ADAPTER_ROLE[role] == harness.WORKER}
        with mock.patch.object(harness, "ADAPTERS", {**harness.ADAPTERS, FIXTURE: fixture}), \
                mock.patch.object(worker_launch, "LAUNCHERS", {**worker_launch.LAUNCHERS, FIXTURE: LiveLauncher(commands)}), \
                mock.patch.object(job, "CODEX_WORKTREE_PREPARER", codex_worker_tree.prepare), \
                mock.patch.object(job, "CODEX_LANDER", codex_worker_landing.land), \
                mock.patch.object(job, "CODEX_HOME_RESOLVER", operator_codex_home):
            config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<live>")
            options = routing.RoutingOptions(config=config, config_source="settings")
            job.execute_step(managed, identity=FAKE_IDENTITY, runtime=runtime, routing=options, timeout=RUN_TIMEOUT)

        (path,) = sorted((runtime / "jobs").glob("*.json"))
        record = json.loads(path.read_text())
        block = record["worker"]
        landing = record["codex_worker"]["landing"]
        tip = git(managed.root, "rev-parse", f"refs/heads/{branch}")
        print(f"\n--- status: {record['status']} ({record['reconciliation_evidence'].get('reason')}), "
              f"worker_outcome {record['worker_outcome']}, subtype {block['subtype']}, exit {block['exit_code']}, "
              f"{block['duration_ms']} ms ---\n{block['result_excerpt']}\n--- landing ---\n{landing}\n"
              f"--- managed branch {branch}: {base[:12]} -> {tip[:12]}, author "
              f"{git(managed.root, 'log', '-1', '--format=%an <%ae>')} ---\n"
              f"--- diagnosis ---\n{block['stream_diagnosis']}")
        self.assertEqual(record["worker_outcome"], worker.SUCCESS, block["stream_diagnosis"])
        self.assertEqual(block["stream_diagnosis"]["breach"], [])
        self.assertEqual((landing["outcome"], landing["cause"], landing["clone_kept"]), ("landed", None, False), landing)
        self.assertEqual((landing["base"], landing["tip"]), (base, tip))
        self.assertEqual(len(landing["commits"]), 1)
        self.assertEqual(git(managed.root, "rev-list", "--count", f"{base}..{tip}"), "1")
        self.assertEqual(git(managed.root, "log", "-1", "--format=%an"), AUTHOR)
        self.assertEqual((managed.root / PROOF_FILE).read_text().strip(), "landed by the worker")
        self.assertEqual(git(managed.root, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(git(managed.root, "for-each-ref", "refs/controller"), "")
        self.assertFalse((runtime / "worker" / record["job_id"] / "tree").exists(), "the clone was not removed")
        self.assertIn(record["status"], (job.STATUS_FINISHED, job.STATUS_FAILED))  # the unchanged rules decide
        validity = job.validate_record(record, managed_repo=managed, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid, validity)
        for leftover in (runtime / "worker").glob("*/codex-home/auth.json"):
            self.fail(f"a credential copy is left at {leftover}")


if __name__ == "__main__":
    unittest.main()
