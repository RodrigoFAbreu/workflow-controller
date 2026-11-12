"""`workflow-controller-codex-worker-usage` CP3: the live proof of a Codex worker job under a real usage gate (H4d, D5).

Opt-in (``CONTROLLER_LIVE_CODEX_TESTS=1``), skipped by default and on CI, with its reason. It costs one model turn on the
operator's credentials: it records the real build as verified in a scratch runtime root, registers the fixture role and
a ``CodexLauncher`` subclass for the test only, and runs ``job.execute_step`` with a real ``codex exec`` on a scratch
clone, gated by the real ``usage.admit`` over that scratch runtime (the operator's own shared record is never touched).
It asserts the admission (provider ``codex``, a token, a live reservation seen while the job runs) and the finished
``usage`` block (``accounting`` ``done``, bound to the job, one ledger entry of provider ``codex``). The clone is not the
managed repository, so the record's ``status`` is ``FAILED`` (``phase_not_in_to_any_of``), as in the H4c1 proof. A usage
limit cannot be provoked, so the mid-job hold is proven only against the fake. Skipped, with the reason and before any
run, when ``codex`` or ``bwrap`` is missing or the operator's Codex home has no ``auth.json``. The output is the capture
``tests/harness_contract/codex/worker_usage.txt``.
``CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_worker_usage_live -v``
"""

from __future__ import annotations

import dataclasses
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

from controller import (  # noqa: E402
    codex_build, codex_worker as cw, command_files, harness, job, review, routing, usage, worker, worker_launch,
)
from tests import process_fixtures  # noqa: E402
from tests.test_codex_worker_sandbox import make_clone  # noqa: E402
from tests.test_codex_worker_sandbox_live import operator_codex_home  # noqa: E402
from tests.test_job import FAKE_IDENTITY, _build_target  # noqa: E402

ENABLED = os.environ.get("CONTROLLER_LIVE_CODEX_TESTS") == "1"
RUN_TIMEOUT = 600
FIXTURE = "codex-live-fixture"
COMMAND = """---
description: Live usage proof command
argument-hint: "<x>"
state_writer: false
---
Do not change any file. Reply with the single word `done`.
Ignore this: $ARGUMENTS
"""


def skip_reason() -> str | None:
    if os.environ.get("CI"):
        return "the live Codex worker usage proof is skipped on CI"
    if not ENABLED:
        return "set CONTROLLER_LIVE_CODEX_TESTS=1 to run the live Codex worker usage proof (one model turn)"
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
            (Path(tmp) / "live-usage-proof.md").write_text(COMMAND)
            rendered = command_files.render_command("live-usage-proof", "", Path(tmp))
        self.assertIn("done", str(rendered))


class LiveLauncher(cw.CodexLauncher):
    """The real launcher with a commands directory holding the proof's command under whatever name the job asks."""

    def __init__(self, commands: Path) -> None:
        self.commands = commands

    def launch(self, task: str, **options):
        (self.commands / f"{task.split()[0][1:]}.md").write_text(COMMAND)
        return super().launch(task, commands_dir=self.commands, **options)


@unittest.skipIf(skip_reason() is not None, skip_reason() or "")
class LiveUsageTest(unittest.TestCase):
    def test_a_real_codex_turn_is_admitted_reserved_and_accounted(self) -> None:
        # Not under /tmp: Codex refuses to create its helper binaries there (tester_sandbox.txt).
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="codex-worker-usage-live-", dir=cache)).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        commands = root / "commands"
        commands.mkdir()
        managed = _build_target(root, phase="PLANNING", governing_workflow_version="2.1")
        digest = codex_build.build_digest(Path(review.resolve_binary("codex")))
        self.assertTrue(digest, "the Codex build cannot be bound to a digest")
        codex_build.record_verified(runtime, digest, {"note": "live test"})

        fixture = dataclasses.replace(harness.CODEX, name=FIXTURE, reviewer_name=None,
                                      roles=(harness.WORKER, harness.IMPLEMENTER))
        roles = {role: {"harness": FIXTURE, "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT}
                 for role in sorted(routing.ROLES)
                 if routing.ADAPTER_ROLE[role] in (harness.WORKER, harness.IMPLEMENTER)}
        admitted: list[dict] = []

        def gate(route, **kwargs):
            provider = kwargs.get("provider", usage.PROVIDER_CLAUDE)
            admission = usage.admit(
                runtime, provider=provider, role=route.role, model=route.model, repository=str(managed.root),
                run_id=None, limits=usage.Limits(provider=provider), now=job.usage_now())
            if admission.hold is not None:
                return admission.hold
            held = usage.snapshot(runtime, now=job.usage_now())["reservations"]
            admitted.append({"provider": provider, "token": admission.token, "reservation": held.get(admission.token)})
            return admission.token

        def prepare(managed_repo, layout, base):
            make_clone(layout.worktree)
            return process_fixtures.prepared_clone(layout.worktree)

        with mock.patch.object(harness, "ADAPTERS", {**harness.ADAPTERS, FIXTURE: fixture}), \
                mock.patch.object(worker_launch, "LAUNCHERS", {**worker_launch.LAUNCHERS, **dict.fromkeys(
                        ((FIXTURE, harness.WORKER), (FIXTURE, harness.IMPLEMENTER)), LiveLauncher(commands))}), \
                mock.patch.object(job, "CODEX_WORKTREE_PREPARER", prepare), \
                mock.patch.object(job, "CODEX_LANDER", process_fixtures.nothing_lander), \
                mock.patch.object(job, "CODEX_HOME_RESOLVER", operator_codex_home):
            config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<live>")
            options = routing.RoutingOptions(config=config, config_source="settings")
            job.execute_step(managed, identity=FAKE_IDENTITY, runtime=runtime, routing=options, timeout=RUN_TIMEOUT,
                             usage_gate=gate)

        (path,) = sorted((runtime / "jobs").glob("*.json"))
        record = json.loads(path.read_text())
        block = record["worker"]
        used = record["usage_record"]
        block_usage = record["usage"]
        print(f"\n--- status: {record['status']} ({record['reconciliation_evidence'].get('reason')}), "
              f"worker_outcome {record['worker_outcome']}, subtype {block['subtype']}, exit {block['exit_code']}, "
              f"{block['duration_ms']} ms ---\n{block['result_excerpt']}\n--- usage_record ---\n{used}\n--- usage block ---\n{block_usage}\n"
              f"--- diagnosis ---\n{block['stream_diagnosis']}")
        self.assertEqual(record["worker_outcome"], worker.SUCCESS, block["stream_diagnosis"])
        self.assertEqual(block["subtype"], "success")
        self.assertTrue(block["result_excerpt"].strip(), "the worker returned no final message")
        self.assertEqual(block["stream_diagnosis"]["breach"], [])
        self.assertEqual(record["worker_state"]["state"], worker.ENDED)
        self.assertEqual(record["ownership_tag"], record["job_id"])
        self.assertIn("pid", record["worker_process"])
        self.assertEqual(record["worker_streams"]["format"], "text")
        self.assertTrue(Path(record["worker_streams"]["stdout_path"]).read_text().strip())
        self.assertEqual(used["harness"], "codex")
        self.assertIn(used["cost_source"], ("derived", "unknown"))  # derived when the price table knows the model
        self.assertGreater((used["input"] or 0) + (used["cache_read"] or 0), 0)
        self.assertGreater(used["output"] or 0, 0)
        (admission,) = admitted
        self.assertEqual(admission["provider"], usage.PROVIDER_CODEX)
        self.assertTrue(admission["token"])
        self.assertIsNotNone(admission["reservation"], "no reservation was written at admission")
        self.assertEqual(block_usage["token"], admission["token"])
        self.assertEqual(block_usage["accounting"], "done")
        shared = usage.snapshot(runtime, now=job.usage_now())
        self.assertEqual(shared["reservations"], {}, "the finished job left its reservation open")
        self.assertIn(admission["token"], shared["settled"])
        self.assertEqual([e["provider"] for e in shared["ledger"]], [usage.PROVIDER_CODEX])  # the scratch root holds this job only
        self.assertEqual(record["status"], job.STATUS_FAILED)  # the clone is not the managed repository (D5)
        self.assertEqual(record["reconciliation_evidence"]["reason"], "phase_not_in_to_any_of")
        validity = job.validate_record(record, managed_repo=managed, identity=FAKE_IDENTITY)
        self.assertTrue(validity.valid, validity)
        for leftover in (runtime / "worker").glob("*/codex-home/auth.json"):
            self.fail(f"a credential copy is left at {leftover}")


if __name__ == "__main__":
    unittest.main()
