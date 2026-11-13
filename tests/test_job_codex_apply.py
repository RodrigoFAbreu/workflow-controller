"""`workflow-controller-h4e3` CP3: a routed apply role run as a job (`controller.job`, D8 and D9).

Offline, through ``job.execute_step``: the managed repository is the recorder tests' fixture (the installed Workflow,
a generated and bound bundle), moved to ``REVISING_PLAN`` or ``APPLYING_REVIEW_FEEDBACK`` through the Workflow's own
writers; the route sends the apply role to ``codex``; the carry, the landing and the recorder are the real ones and
the worker is the scripted fake Codex behind the real launcher, whose own work (the Workflow's writers run in the
clone) is the ``after`` hook, as ``tests/test_job_codex_worker_landing.py`` does. D8's table has one test per ending,
D9's recovery sites one test per site and stage.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import (  # noqa: E402
    codex_carry, codex_worker as cw, generator_run, codex_worker_sandbox, harness, job, managed_repo, review, review_recorder, routing,
)
from controller import runtime as runtime_module  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_codex_review_role import RoleCase, message  # noqa: E402
from tests.test_job import FAKE_IDENTITY  # noqa: E402
from tests.test_review_recorder import (  # noqa: E402
    COMPLETED_AT, IN_PLACE, STATE, WID, RecorderCase, _entry, _git,
)

REAL_LAUNCH = cw.launch_codex_worker

#: The Workflow's own writers, run in the clone: what ``/apply-plan-review`` does for one accepted finding -- the plan
#: edited, the revision published, the bundle generated from the author inputs and bound.
PLAN_WORK = r'''
import datetime
import subprocess
root = Path.cwd()
wid = "wi-1"
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
revision = entry["plan_revision"] + 1
plan = root / entry["plan_path"]
plan.write_text(plan.read_text().replace(f"(Revision {revision - 1})", f"(Revision {revision})") + "\nRevised for the finding.\n")
registry = root / entry["registry_path"]
registry.write_text(registry.read_text().replace(f'"plan_revision": {revision - 1}', f'"plan_revision": {revision}'))
rcid, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
ws.state_transaction(root, lambda s: ws.publish_plan_revision(s, wid, revision, now(), review_content_id=rcid))
inputs = root / fingerprint.resolve_plan_review_inputs_dir(root, wid)
(inputs / "REVIEW_REQUEST.md").write_text(f"# Review request\n\nstage: plan\nwork item: {wid}\nreview_content_id: {rcid}\n")
(inputs / "TEST_RESULTS.md").write_text(f"stage: plan (revision {revision})\nhead: {subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}\n\nNo checks at the plan stage.\n")
done = subprocess.run(["./scripts/prepare-ai-review.sh", entry["base_commit"], "plan", wid], cwd=root, check=False,
                      capture_output=True, text=True, stdin=subprocess.DEVNULL)
assert done.returncode == 0, done.stdout + done.stderr
binding = ws.verify_plan_review_bundle(root, wid)
ws.state_transaction(root, lambda s: ws.bind_plan_review_bundle(s, wid, binding=binding, now=now()))
'''

#: ``/review-plan``'s REVISE recorded through the Workflow's writer: the item goes to ``REVISING_PLAN``.
REVISE = r'''
import datetime
root = Path.cwd()
wid = "wi-1"
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
rcid, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ws.state_transaction(root, lambda s: ws.record_local_plan_review(
    s, wid, verdict="REVISE", bundle_id=entry["current_bundle_id"], review_content_id=rcid, round=1, now=now))
feedback = root / ".ai-review" / wid / "feedback" / "REVIEW_FEEDBACK.md"
feedback.parent.mkdir(parents=True, exist_ok=True)
feedback.write_text("\n".join([
    "# Review Decision", "", "Status: REVISE", "Reviewer role: LOCAL_MODEL_PLAN_REVIEW",
    f"Reviewed bundle ID: {entry['current_bundle_id']}", f"Reviewed base commit: {entry['base_commit']}",
    f"Work item: {wid}", f"Reviewed review content ID: {rcid}", "", "## Blocking findings", "", "- B1: add a line.", ""]))
'''



#: ``/apply-implementation-review``'s Workflow writers run in the clone: the finding fixed and committed, the revision
#: advanced through ``record_bundle_generation`` and its generation-record commit, and this round's author-written
#: summary (the generator itself is the operator's step after the landing).
IMPL_WORK = r'''
import datetime
import subprocess
root = Path.cwd()
wid = "wi-1"
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*args):
    return subprocess.run(["git", "-c", "user.name=Worker", "-c", "user.email=worker@example.invalid", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout.strip()
flows = root / "docs" / "UX_FLOWS.md"
flows.write_text("the fixed flow\n")
git("add", "docs/UX_FLOWS.md")
git("commit", "-m", "fix the finding")
head = git("rev-parse", "HEAD")
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
outcome = ws.resolve_bundle_generation_outcome(root, entry, base_commit=entry["base_commit"], head=head)
assert outcome[0] == "ordinary", outcome
ws.state_transaction(root, lambda s: ws.record_bundle_generation(
    s, wid, stage="post-fix", head=head, now=now(), outcome=outcome[0]))
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
revision = entry["implementation_revision"]
current = root / ".ai-review" / wid / "current"
(current / "IMPLEMENTATION_SUMMARY.md").write_text(f"# Summary\n\nimplementation_revision: {revision}\n")
(current / "TEST_RESULTS.md").write_text("# Test results\n\nall green, round 2\n")
git("add", "docs/ai-workflow/WORKFLOW_STATE.json")
git("commit", "-m", f"generation record\n\nWorkflow-Bundle-Generation-Record: {wid}/{revision}\nWorkflow-Work-Item: {wid}")
content_id = ws.approval_review_content_id(
    root, stage="implementation", base_commit=entry["base_commit"], work_item_type="product", work_item_id=wid,
    head="HEAD", artifacts_path=fingerprint.artifacts_path_for_work_item(wid))
(current / "REVIEW_REQUEST.md").write_text(f"# Review request\n\nstage: implementation\nreview_content_id: {content_id}\n")
'''

ITEM = f".ai-review/{WID}"


class ApplyCase(RoleCase):
    """A fixture moved to its apply phase (``REVISING_PLAN`` or ``APPLYING_REVIEW_FEEDBACK``), restored before each
    test, with the apply role routed to ``codex`` and driven through ``execute_step``; the worker's own work is the
    ``after`` hook, called with the clone's path once the fake run has ended."""

    ROLE = routing.APPLY_PLAN_REVIEW

    #: The guarded generator run (workflow-controller-h4e3b) refuses with this cause, so the tests of the carry, the
    #: landing and the regeneration gate see the gate H4e3 built; the tests of the run itself set it to ``None``.
    GUARD_CAUSE: str | None = "surface_foreign_entry"

    @classmethod
    def prepare(cls) -> None:
        raise NotImplementedError

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.prepare()
        shutil.rmtree(cls._kept)
        shutil.copytree(cls._built, cls._kept, symlinks=True)

    def setUp(self) -> None:
        super().setUp()
        self.stub = fixtures.write_stub_workflow_manager(self.tmp / "workflow-manager", release=self.release())
        self.target = managed_repo.inspect(self.root, manager_bin=str(self.stub))
        self.options = self.routing_for({self.ROLE: self.entry()})
        self.enterContext(mock.patch.object(job, "CODEX_HOME_RESOLVER", lambda: self.operator))
        self.enterContext(mock.patch.object(cw, "launch_codex_worker", self.fake_launch))
        if self.GUARD_CAUSE is not None:
            def refuse(*args, **kw):
                raise generator_run.GeneratorRefused(self.GUARD_CAUSE, "planted by the test")
            self.guard = self.enterContext(mock.patch.object(generator_run, "run", refuse))
        self.ids = 0
        self.enterContext(mock.patch.object(job, "_new_job_id", self.next_id))
        self.after = None
        self.worker_reconciler = self.enterContext(mock.patch.object(
            job, "_reconcile_codex_worker", side_effect=AssertionError("a carry record reached the worker reconciler")))

    @staticmethod
    def entry(**extra) -> dict:
        return {"harness": "codex", "model": review.DEFAULT_MODEL, "effort": review.DEFAULT_EFFORT, **extra}

    @staticmethod
    def routing_for(roles: dict) -> routing.RoutingOptions:
        config = routing.parse_routing_config(json.dumps({"schema_version": 1, "roles": roles}), path="<test>")
        return routing.RoutingOptions(config=config, config_source="settings")

    def release(self) -> str:
        return json.loads((self.root / ".workflow-manager" / "installation.json").read_text())["workflow_version"]

    def next_id(self) -> str:
        self.ids += 1
        return f"job{self.ids}"

    def fake_launch(self, task, *, runtime_root, job_id, operator_home, cwd, timeout, model, effort, prompt=None,
                    on_spawn=None, **rest):
        self.launched.append({"task": task, "prompt": prompt, "cwd": cwd})
        result = REAL_LAUNCH(
            task, runtime_root=runtime_root, job_id=job_id, operator_home=operator_home, cwd=cwd, timeout=timeout,
            on_spawn=on_spawn, model=model, effort=effort, prompt=prompt, binary_name=str(self.binary),
            system_dir=self.system, real_home=self.real_home, run=self.controls(job_id))
        if self.after is not None:
            self.after(Path(cwd))
        return result

    def step(self, **kw):
        if not self.recorded:
            self.record()
        kw.setdefault("timeout", 60)
        kw.setdefault("routing", self.options)
        return job.execute_step(self.target, identity=FAKE_IDENTITY, runtime=self.runtime, **kw)

    def resume(self, target=None) -> list[dict]:
        return job.resume(target or self.target, identity=FAKE_IDENTITY, runtime=self.runtime)

    def abandon(self, job_id: str = "job1") -> dict:
        return job.abandon(self.target, identity=FAKE_IDENTITY, runtime=self.runtime, job_id=job_id)

    def records(self) -> list[dict]:
        return [json.loads(path.read_text()) for path in sorted((self.runtime / "jobs").glob("*.json"))]

    def only_record(self) -> dict:
        (record,) = self.records()
        return record

    def clone(self) -> Path:
        return codex_worker_sandbox.WorkerLayout(self.runtime, "job1").worktree

    def kill_at(self, target: str, name: str, *, after: int = 0):
        """Raise ``KeyboardInterrupt`` from ``target.name`` on its ``after``-th call (0 = the first): the Controller
        process killed at that point; the record stays as the last write left it."""
        real = getattr(target, name)
        calls = []

        def killer(*args, **kw):
            calls.append(1)
            if len(calls) > after:
                raise KeyboardInterrupt
            return real(*args, **kw)

        return mock.patch.object(target, name, killer)

    def managed_snapshot(self) -> tuple:
        return ((self.root / STATE).read_bytes(), _git(self.root, "status", "--porcelain=v1", "-uall"),
                _git(self.root, "for-each-ref"), _git(self.root, "rev-parse", "HEAD"))

    def regenerate_as_the_operator(self, stage: str) -> None:
        """The steps the gate names, run by the test acting as the operator in the managed repository; then the next
        review's preconditions pass (D8, CP4's operator case)."""
        entry = _entry(self.root)
        done = subprocess.run(["./scripts/prepare-ai-review.sh", entry["base_commit"], stage, WID], cwd=self.root,
                              capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        kind = "plan" if stage == "plan" else "implementation"
        fixtures.run_workflow_python(self.root, f"""
entry = ws.json.loads((Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"]["wi-1"]
manifest = Path.cwd() / fingerprint.resolve_bundle_dir(Path.cwd(), "wi-1", stage={kind!r}) / "MANIFEST.md"
fingerprint.assert_local_generation_matches(Path.cwd(), manifest)
if {kind!r} == "plan":
    ws.validate_local_plan_review_preconditions_bound(Path.cwd(), entry)
else:
    ws.validate_local_implementation_review_preconditions(entry)
    ws.verify_implementation_review_bundle(Path.cwd(), "wi-1")
""")

    def evidence(self, record: dict) -> dict:
        return record["reconciliation_evidence"]

    def assert_carry_ended(self, record: dict, *, stage: str = "done", kept: bool = False) -> None:
        self.assertEqual(record["codex_carry"]["stage"], stage)
        self.assertEqual(self.clone().exists(), kept)


class PlanCase(ApplyCase):
    STAGE = "plan"
    ROLE = routing.APPLY_PLAN_REVIEW

    @classmethod
    def prepare(cls) -> None:
        fixtures.run_workflow_python(cls._built, REVISE)

    def plan_work(self, clone: Path) -> None:
        fixtures.run_workflow_python(clone, PLAN_WORK)

    def worked(self) -> None:
        self.script(message="applied")
        self.after = self.plan_work


class ImplCase(ApplyCase):
    STAGE = "implementation"
    ROLE = routing.APPLY_IMPLEMENTATION_REVIEW

    @classmethod
    def setUpClass(cls) -> None:
        """The fixture's checkpoint commit carries the Workflow's trailer pair, so the repository verifies healthy."""
        real = fixtures.commit_all

        def commit_all(root, message, *args, **kw):
            if message == "implemented":
                message += "\n\nWorkflow-Checkpoint: CP1\nWorkflow-Work-Item: wi-1"
            return real(root, message, *args, **kw)

        with mock.patch.object(fixtures, "commit_all", commit_all):
            super().setUpClass()

    @classmethod
    def prepare(cls) -> None:
        """A manual-ingest ``REVISE``: the entry is written and left uncommitted (D4's case)."""
        values = review_recorder.check(cls._built, "implementation", WID).values
        verdict = cls._area / "verdict.md"
        verdict.write_text(RecorderCase.compose(None, values, "REVISE"), encoding="utf-8")
        fixtures.run_workflow_python(
            cls._built, IN_PLACE.replace("    ws.commit_pending_applying_review_feedback_entry(root, \"wi-1\", "
                                         "attribution=())\n", "    pass\n"),
            "implementation", "REVISE", COMPLETED_AT, str(values["round"]), str(verdict))

    def impl_work(self, clone: Path) -> None:
        fixtures.run_workflow_python(clone, IMPL_WORK)

    def worked(self) -> None:
        self.script(message="applied")
        self.after = self.impl_work


GATE_STEPS_PLAN = "scripts/prepare-ai-review.sh {base} plan wi-1"
GATE_STEPS_IMPL = "scripts/prepare-ai-review.sh {base} post-fix wi-1"


class PlanRoleTest(PlanCase):
    def test_a_routed_plan_apply_ends_at_the_regeneration_gate(self) -> None:
        self.worked()
        before = {rel: (self.root / rel).read_bytes() for rel in (f"{ITEM}/current/MANIFEST.md",)}
        before_plan = (self.root / _entry(self.root)["plan_path"]).read_text()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        self.assertEqual(self.evidence(record)["code"], job.CARRY_BUNDLE_GATE_CODE)
        self.assertEqual(self.evidence(record)["reconcile_class"], "invalid")
        self.assertEqual(record["protocol"]["reconcile"]["class"], "invalid")
        self.assertEqual(record["protocol_gate"]["code"], job.CARRY_BUNDLE_GATE_CODE)
        self.assert_carry_ended(record)
        self.assertEqual(_entry(self.root)["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(_entry(self.root)["plan_revision"], 2)
        self.assertNotEqual((self.root / _entry(self.root)["plan_path"]).read_text(), before_plan)
        self.assertIn('"plan_revision": 2', (self.root / _entry(self.root)["registry_path"]).read_text())
        self.assertIn("revision 2", (self.root / f"{ITEM}/plan-inputs/TEST_RESULTS.md").read_text())
        # no generated bundle file was written by the job: the managed bundle is as it was
        for rel, data in before.items():
            self.assertEqual((self.root / rel).read_bytes(), data)
        self.assertFalse((self.root / ITEM / "REJECTED").exists())
        self.assertTrue(record["usage_record"])
        self.assertIsNone(self.worker_reconciler.call_args)

    def test_after_the_operator_regenerates_the_next_review_preconditions_pass(self) -> None:
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        advisory_before = _entry(self.root)["current_bundle_id"]
        self.regenerate_as_the_operator("plan")
        self.assertEqual(_entry(self.root)["current_bundle_id"], advisory_before)  # as the clone wrote it

    def test_the_gate_message_names_the_plan_steps(self) -> None:
        self.worked()
        record = self.step()
        gate = record["human_gate_pending"]
        base = _entry(self.root)["base_commit"]
        self.assertEqual(gate["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        text = gate["what_is_required"]
        self.assertIn(f"(1) run `{GATE_STEPS_PLAN.format(base=base)}`", text)
        self.assertIn("(2) take the next step", text)
        self.assertNotIn("git log -p", text)
        self.assertIn("did not regenerate the bundle (surface_foreign_entry: planted by the test)", text)

    def test_a_gate_record_passes_validate_record_and_resume_never_reconciles_it(self) -> None:
        self.worked()
        record = self.step()
        self.assertTrue(job.validate_record(record, managed_repo=self.target, identity=FAKE_IDENTITY).valid)
        before = self.only_record()
        (reported,) = self.resume()
        self.assertEqual(reported["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("reconciled_this_call", reported)
        self.assertEqual(self.only_record(), before)

    def test_a_worker_that_changed_nothing_is_the_no_progress_judgement(self) -> None:
        self.script(message="nothing to do")
        before = self.managed_snapshot()
        record = self.step()
        self.assertNotEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["protocol"]["progress"], "none")
        self.assert_carry_ended(record)
        self.assertEqual(self.managed_snapshot(), before)

    def test_a_reconcile_answer_that_is_not_the_stale_bundle_is_the_resolutions_own_ending(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            (self.root / ITEM / "REJECTED").write_text("the generator's failure marker\n")  # a second reason

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["reason"], job.RECONCILE_INVALID_REASON)
        codes = [reason["code"] for reason in self.evidence(record)["invalid_reasons"]]
        self.assertIn("bundle_rejected", codes)
        self.assert_carry_ended(record)

    # -- the narrow check, one condition at a time ---------------------------

    def done_record(self) -> tuple[dict, dict]:
        """The ended gate record, and the ``FAILED`` fields the resolution gave before the gate (the synthetic answer
        holds only ``plan_review_not_bound``, so each refusal below is by its own condition)."""
        self.worked()
        record = self.step()
        phase = "AWAITING_LOCAL_PLAN_REVIEW"
        fields = {"status": job.STATUS_FAILED, "observed_phase_after": phase, "reconciliation_evidence": {
            "reason": job.RECONCILE_INVALID_REASON,
            "invalid_reasons": [{"code": "plan_review_not_bound",
                                 "text": f"{phase} with plan_review_publication_status BUNDLE_UNVERIFIED"}]}}
        self.assertTrue(job._is_expected_stale_bundle(fields, record, self.root))
        return record, fields

    def refused(self, record: dict, fields: dict) -> None:
        self.assertFalse(job._is_expected_stale_bundle(fields, record, self.root))

    def test_condition_1_the_carry_is_done_after_every_member_was_written(self) -> None:
        record, fields = self.done_record()
        self.refused({**record, "codex_carry": {**record["codex_carry"], "stage": "out"}}, fields)
        writes = {**record["codex_carry"]["writes"], "docs/ai-workflow/wi-1-PLAN.md": {"sha256": "0" * 64, "mode": 420}}
        self.refused({**record, "codex_carry": {**record["codex_carry"], "writes": writes}}, fields)
        self.refused({**record, "codex_carry": {**record["codex_carry"], "role": routing.APPLY_IMPLEMENTATION_REVIEW}},
                     fields)

    def test_condition_2_one_reason_with_exactly_the_stale_bundle_text(self) -> None:
        record, fields = self.done_record()
        evidence = fields["reconciliation_evidence"]
        reasons = evidence["invalid_reasons"]
        for changed in (
            [*reasons, {"code": "illegal_edge", "text": "x"}],
            [{"code": "plan_review_not_bound", "text": "AWAITING_LOCAL_PLAN_REVIEW with plan_review_publication_status "
                                                       "CONTENT_DRIFTED"}],
            [{"code": "plan_review_not_bound", "text": "raised: BUNDLE_UNVERIFIED while checking"}],
            [{"code": "bundle_rejected", "text": reasons[0]["text"]}],
            [],
        ):
            self.refused(record, {**fields, "reconciliation_evidence": {**evidence, "invalid_reasons": changed}})
        self.refused(record, {**fields, "observed_phase_after": "REVISING_PLAN"})
        self.refused(record, {**fields, "reconciliation_evidence": {**evidence, "reason": "worker_outcome"}})

    def test_condition_3_the_managed_manifest_is_an_older_integer_revision(self) -> None:
        record, fields = self.done_record()
        manifest = self.root / ITEM / "current" / "MANIFEST.md"
        original = manifest.read_text()
        for text in (original.replace("plan_revision: 1", "plan_revision: 2"),
                     original.replace("plan_revision: 1", "plan_revision: 3"),
                     original.replace("plan_revision: 1", "plan_revision: one"),
                     original.replace("plan_revision: 1\n", "")):
            manifest.write_text(text)
            self.refused(record, fields)
        manifest.unlink()
        self.refused(record, fields)
        manifest.mkdir()
        self.refused(record, fields)

    def test_condition_4_no_rejected_marker_and_a_bound_binding_at_the_revision(self) -> None:
        record, fields = self.done_record()
        marker = self.root / ITEM / "REJECTED"
        marker.symlink_to("nowhere")  # an entry of any kind; it is never opened
        self.refused(record, fields)
        marker.unlink()
        state = json.loads((self.root / STATE).read_text())
        binding = state["work_items"][WID]["plan_review_binding"]
        for change in (lambda b: b.update(status="PUBLISHED"), lambda b: b["bound"].update(plan_revision=1),
                       lambda b: b.pop("bound")):
            changed = json.loads(json.dumps(state))
            change(changed["work_items"][WID]["plan_review_binding"])
            (self.root / STATE).write_text(json.dumps(changed))
            self.refused(record, fields)
        (self.root / STATE).write_text(json.dumps(state))
        self.assertTrue(job._is_expected_stale_bundle(fields, record, self.root), binding)


class ImplRoleTest(ImplCase):
    def test_a_routed_implementation_apply_with_a_pending_entry_lands_over_the_carry_base_and_ends_at_the_gate(self) -> None:
        pre_head = _git(self.root, "rev-parse", "HEAD").strip()
        self.assertIn(STATE, _git(self.root, "status", "--porcelain"))  # the pending manual-ingest entry
        manifest = (self.root / ITEM / "current" / "MANIFEST.md").read_bytes()
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        self.assertEqual(self.evidence(record)["code"], job.CARRY_BUNDLE_GATE_CODE)
        carry = record["codex_carry"]
        base = carry["base_commit"]
        self.assertNotEqual(base, pre_head)  # D4's commit is the carry base ...
        self.assertEqual(carry["pre_step_head"], pre_head)
        self.assertEqual(_git(self.root, "rev-parse", f"{base}^").strip(), pre_head)  # ... and the only commit D4 made
        self.assertEqual(record["pre_state"]["target_head"], base)  # the baseline is re-captured at the carry base
        self.assertEqual(Path(self.launched[0]["cwd"]), self.clone())  # launched once, from that commit: no retry
        self.assertEqual(len(self.launched), 1)
        head = _git(self.root, "rev-parse", "HEAD").strip()
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{base}..{head}").strip(), "2")  # landed over it
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")
        self.assertEqual(_entry(self.root)["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(_entry(self.root)["implementation_revision"], 2)
        self.assert_carry_ended(record)
        self.assertEqual(_git(self.root, "status", "--porcelain"), "")
        # a carried-out member, and no generated bundle file written by the job
        self.assertIn("implementation_revision: 2", (self.root / ITEM / "current" / "IMPLEMENTATION_SUMMARY.md").read_text())
        self.assertEqual((self.root / ITEM / "current" / "MANIFEST.md").read_bytes(), manifest)
        self.assertTrue(record["usage_record"])

    def test_after_the_operator_reads_the_range_and_regenerates_the_next_review_preconditions_pass(self) -> None:
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.regenerate_as_the_operator("post-fix")

    def test_the_gate_message_names_the_landed_range_before_the_steps(self) -> None:
        self.worked()
        record = self.step()
        base, head = record["codex_carry"]["base_commit"], _git(self.root, "rev-parse", "HEAD").strip()
        text = record["human_gate_pending"]["what_is_required"]
        entry_base = _entry(self.root)["base_commit"]
        self.assertIn(f"(1) read the landed range first: `git log -p {base}..{head}`", text)
        self.assertIn(f"(2) run `{GATE_STEPS_IMPL.format(base=entry_base)}`", text)
        self.assertIn("(3) take the next step", text)
        self.assertEqual(self.evidence(record)["landed_head"], head)
        self.assertEqual(record["human_gate_pending"]["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

    def test_a_usage_limit_with_no_change_is_interrupted_and_waits(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED, record.get("reconciliation_evidence"))
        self.assertIsNotNone(job.usage_limit_of(record))
        self.assertEqual(record["pre_state"]["target_head"], record["codex_carry"]["base_commit"])

    def test_a_worker_killed_before_its_end_is_recorded_ends_interrupted_on_resume(self) -> None:
        self.script(message="applied")
        with self.kill_at(job, "_with_worker_state"), self.assertRaises(KeyboardInterrupt):
            self.step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED)
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_INTERRUPTED, resumed.get("reconciliation_evidence"))
        self.assertTrue(job.validate_record(resumed, managed_repo=self.target, identity=FAKE_IDENTITY).valid)

    def test_a_kill_at_preparing_is_recovered_as_not_started_and_the_rerun_launches_from_the_kept_commit(self) -> None:
        self.script(message="applied")
        pre_head = _git(self.root, "rev-parse", "HEAD").strip()
        with self.kill_at(codex_carry, "carry_in"), self.assertRaises(KeyboardInterrupt):
            self.step()
        killed = self.only_record()
        self.assertEqual(killed["codex_carry"]["stage"], "preparing")
        base = killed["codex_carry"]["base_commit"]
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), base)  # D4's commit stays
        (resumed,) = self.resume()
        self.assertNotEqual(resumed["status"], job.STATUS_LAUNCHED)
        self.assertFalse(self.clone().exists())
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), base)
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{pre_head}..HEAD").strip(), "1")
        self.worked()
        again = self.step()  # a new job: its pre-step head is D4's commit, so commit_pending is a no-op
        self.assertEqual(again["status"], job.STATUS_GATE_BLOCKED, again.get("reconciliation_evidence"))
        self.assertEqual(again["codex_carry"]["pre_step_head"], base)
        self.assertEqual(again["codex_carry"]["base_commit"], base)
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{pre_head}..{base}").strip(), "1")  # no second commit

    def test_an_unrelated_branch_move_after_d4_still_refuses(self) -> None:
        self.script(message="applied")
        real = codex_carry.carry_in

        def moved(managed, layout, carry_set, block, **kw):
            subprocess.run(["git", "-c", "user.name=Other", "-c", "user.email=other@example.invalid", "commit",
                            "--allow-empty", "-m", "someone else"], cwd=self.root, check=True, capture_output=True)
            return real(managed, layout, carry_set, block, **kw)

        with mock.patch.object(codex_carry, "carry_in", moved):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["code"], job.WORKER_NOT_STARTED_CODE)
        self.assertIn("managed_branch_moved", json.dumps(self.evidence(record)))
        self.assertEqual(self.launched, [])
        self.assertFalse(self.clone().exists())

    def test_a_refused_landing_carries_nothing_out_and_keeps_the_clone(self) -> None:
        self.worked()
        refused = mock.Mock()
        refused.outcome = "refused"
        refused.to_block = lambda clone_kept: {"cause": "landing_refused", "clone_kept": clone_kept, "commits": []}
        with mock.patch.object(job, "CODEX_LANDER", lambda *a, **k: refused):
            before = (self.root / ITEM / "current" / "IMPLEMENTATION_SUMMARY.md").read_bytes()
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["code"], job.CODEX_LANDING_REFUSED_CODE)
        self.assertNotEqual(record["codex_carry"]["stage"], "done")
        self.assertTrue(self.clone().exists())
        self.assertEqual((self.root / ITEM / "current" / "IMPLEMENTATION_SUMMARY.md").read_bytes(), before)

    def test_the_apply_branch_is_taken_before_the_review_and_worker_branches(self) -> None:
        self.worked()
        with mock.patch.object(job, "_run_codex_review", side_effect=AssertionError("review branch")), \
                mock.patch.object(job, "_is_codex_review_route", side_effect=AssertionError("review branch asked")):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertTrue(job._is_codex_carry_record(record))
        self.assertFalse(job._is_codex_worker_record(record))

    def test_the_usage_gate_is_asked_with_the_codex_provider_on_the_single_route_and_the_chain(self) -> None:
        for roles in (
            {self.ROLE: self.entry()},
            {self.ROLE: self.entry(fallback=[self.entry(), {"harness": "claude-code", "model": "opus"}])},
        ):
            self.setUp()
            self.options = self.routing_for(roles)
            self.worked()
            gate = mock.Mock(return_value="token-1")
            with mock.patch.object(job.usage, "bind_job"), \
                    mock.patch.object(job, "_usage_start_block", return_value={"token": "token-1"}), \
                    mock.patch.object(job, "_usage_account", side_effect=lambda _root, record, **_kw: record):
                record = self.step(usage_gate=gate)
            gate.assert_called_once()
            self.assertEqual(gate.call_args.kwargs, {"provider": "codex"})
            self.assertEqual(record["usage"]["token"], "token-1")
            self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)


def _in_clone(path: Path, rel: str) -> Path:
    return path / rel


class PlanEndingsTest(PlanCase):
    """D8's table, one test per ending of the carry that is not the gate."""

    def refused_at_carry_out(self, work, cause: str) -> dict:
        self.script(message="applied")
        self.after = work
        before = self.managed_snapshot()
        before_files = {rel: (self.root / rel).read_bytes() for rel in (
            _entry(self.root)["plan_path"], _entry(self.root)["registry_path"])}
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        evidence = self.evidence(record)
        self.assertEqual((evidence["code"], evidence["cause"]), (job.CODEX_CARRY_REFUSED_CODE, cause))
        self.assertEqual(self.managed_snapshot(), before)  # nothing written to the managed repository
        for rel, data in before_files.items():
            self.assertEqual((self.root / rel).read_bytes(), data)
        self.assertTrue(self.clone().exists())  # the clone is kept ...
        self.assertEqual(evidence["clone"], str(self.clone()))  # ... and named
        self.assertNotEqual(record["codex_carry"]["stage"], "done")
        # the rest of the worker's directory is settled as a refused landing's is: the clone and the sessions stay
        layout = codex_worker_sandbox.WorkerLayout(self.runtime, "job1")
        self.assertLessEqual({path.name for path in layout.base.iterdir()}, {"tree", "codex-home"})
        self.assertFalse((layout.base / "carry").exists())
        if layout.codex_home.exists():
            self.assertLessEqual({path.name for path in layout.codex_home.iterdir()}, {"sessions"})
        return record

    def test_carry_foreign_dirt_at_carry_in_is_worker_not_started_with_no_codex_and_no_clone(self) -> None:
        self.script(message="applied")
        (self.root / "README.md").write_text("changed by someone else\n")
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["code"], job.WORKER_NOT_STARTED_CODE)
        self.assertIn("carry_foreign_dirt", json.dumps(self.evidence(record)))
        self.assertEqual(self.launched, [])
        self.assertFalse(self.clone().exists())

    def test_carry_member_refused_at_carry_in_is_worker_not_started(self) -> None:
        self.script(message="applied")
        (self.root / ITEM / "feedback" / "auth.json").write_text("{}")  # a credential-shaped name
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["code"], job.WORKER_NOT_STARTED_CODE)
        self.assertIn("carry_member_refused", json.dumps(self.evidence(record)))
        self.assertEqual(self.launched, [])
        self.assertFalse(self.clone().exists())

    def test_carry_out_of_set(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            (clone / "stray.txt").write_text("outside the set\n")

        self.refused_at_carry_out(work, "carry_out_of_set")

    def test_carry_state_foreign(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            path = clone / STATE
            document = json.loads(path.read_text())
            document["a_new_top_level_key"] = 1
            path.write_text(json.dumps(document, indent=2) + "\n")

        self.refused_at_carry_out(work, "carry_state_foreign")

    def test_carry_unexpected_commit(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit",
                            "--allow-empty", "-m", "a commit the plan role must not make"], cwd=clone, check=True,
                           capture_output=True)

        self.refused_at_carry_out(work, "carry_unexpected_commit")

    def test_clone_git_changed(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            hooks = clone / ".git" / "hooks"
            hooks.mkdir(exist_ok=True)
            (hooks / "post-merge").write_text("#!/bin/sh\necho owned\n")

        self.refused_at_carry_out(work, "clone_git_changed")

    def test_carry_index_changed(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            subprocess.run(["git", "add", "-N", "extra-intent.txt"], cwd=clone, capture_output=True)
            (clone / "extra-intent.txt").write_text("x\n")
            subprocess.run(["git", "add", "-N", "extra-intent.txt"], cwd=clone, check=True, capture_output=True)

        self.refused_at_carry_out(work, "carry_index_changed")

    def test_carry_member_refused_at_carry_out(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            member = clone / ITEM / "plan-inputs" / "REVIEW_REQUEST.md"
            member.unlink()
            os.symlink("/etc/passwd", member)

        self.refused_at_carry_out(work, "carry_member_refused")

    def test_carry_managed_changed(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            plan = self.root / _entry(self.root)["plan_path"]
            plan.write_text(plan.read_text() + "someone else edited this meanwhile\n")

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["cause"], "carry_managed_changed")
        self.assertTrue(self.clone().exists())

    def test_carry_clone_gone_names_the_members_not_yet_written(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            shutil.rmtree(clone)

        self.script(message="applied")
        self.after = work
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["cause"], "carry_clone_gone")
        self.assertIn("not_written", self.evidence(record)["codex_carry"])

    def test_a_worker_failure_keeps_the_launchers_subtype_and_the_clone(self) -> None:
        self.script(exit=3, message="partial")
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["worker_outcome"], "FAILURE")
        self.assertEqual(record["worker"]["subtype"], "non_zero_exit")
        self.assertTrue(self.clone().exists())
        self.assertNotEqual(record["codex_carry"]["stage"], "done")

    def test_a_usage_limit_keeps_its_fact_and_is_interrupted_for_the_wait_path(self) -> None:
        self.script(verdict="limit", limit_shape="string_full", message="partial")
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_INTERRUPTED, record.get("reconciliation_evidence"))
        self.assertIsNotNone(job.usage_limit_of(record))
        self.assertEqual(record["pre_state"]["target_head"], record["codex_carry"]["base_commit"])

    def test_a_sandbox_breach_is_failed_and_keeps_the_clone(self) -> None:
        self.script(message="applied", git=[["init", "-q", "sub"]])  # a nested repository: the Codex scan's breach
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(record["worker"]["subtype"], cw.SUBTYPE_BREACH)
        self.assertTrue(self.clone().exists())

    def test_an_exception_after_the_spawn_is_failed_naming_it_with_the_stage_and_keeps_the_clone(self) -> None:
        def work(clone: Path) -> None:
            self.plan_work(clone)
            raise RuntimeError("the harness fell over")

        self.script(message="applied")
        self.after = work
        with self.assertRaises(RuntimeError):
            self.step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        found = self.evidence(record)
        self.assertEqual(found["code"], "RuntimeError")
        self.assertEqual(found["codex_carry"]["stage"], "carried_in")
        self.assertIn("not_written", found["codex_carry"])
        self.assertTrue(self.clone().exists())


STAGES = ("preparing", "carried_in", "carried_in_with_worker", "worker_ran", "out", "done")
WORKER_RAN = {"preparing": False, "carried_in": False, "carried_in_with_worker": True, "worker_ran": True,
              "out": True, "done": True}
#: The stages whose clone is still on disk when the Controller is killed: the clone is removed once the carry is done.
CLONE_KEPT = {"preparing": False, "carried_in": False, "carried_in_with_worker": True, "worker_ran": True,
              "out": True, "done": False}


class RecoveryTest(PlanCase):
    """D9: every recovery site, for each stage of the block."""

    def kill_a_managed_write(self):
        """The Controller killed after the first member of the carry out was written (the carry in writes the clone
        through the same primitive, which is let through)."""
        real = runtime_module.write_carried_file
        written = []

        def write(root, rel, data, **kw):
            if Path(root) == self.root:
                if written:
                    raise KeyboardInterrupt
                written.append(rel)
            return real(root, rel, data, **kw)

        return mock.patch.object(runtime_module, "write_carried_file", write)

    def killed_at(self, stage: str) -> dict:
        """Run the step and kill the Controller at ``stage``; returns the record the kill left."""
        self.script(message="applied")
        self.after = self.plan_work
        kills = {
            "preparing": lambda: self.kill_at(codex_carry, "carry_in"),
            "carried_in": lambda: self.kill_at(job.codex_worker_job, "launch_options"),
            "worker_ran": lambda: self.kill_at(job, "_complete_codex_carry"),
            "out": self.kill_a_managed_write,
            "done": lambda: self.kill_at(job, "_carry_resolution"),
        }
        if stage == "carried_in_with_worker":
            def work(clone: Path) -> None:
                self.plan_work(clone)
                raise KeyboardInterrupt

            self.after = work
            context = contextlib.nullcontext()
        else:
            context = kills[stage]()
        with context, self.assertRaises(KeyboardInterrupt):
            self.step()
        record = self.only_record()
        self.assertEqual(record["status"], job.STATUS_LAUNCHED if stage not in ("worker_ran", "out", "done")
                         else record["status"])
        self.assertEqual(record["codex_carry"]["stage"], "carried_in" if stage == "carried_in_with_worker"
                         else stage)
        return record

    def test_resume_recovers_each_stage(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.killed_at(stage)
                before = _entry(self.root)
                (resumed,) = self.resume()
                self.assertNotEqual(resumed["status"], job.STATUS_LAUNCHED)
                self.assertFalse(self.clone().exists())
                if WORKER_RAN[stage]:
                    # the carry out ran (or finished) from the kept clone: the gate of the ordinary ending
                    self.assertEqual(resumed["status"], job.STATUS_GATE_BLOCKED, resumed.get("reconciliation_evidence"))
                    self.assertEqual(resumed["codex_carry"]["stage"], "done")
                    self.assertEqual(_entry(self.root)["plan_revision"], 2)
                else:
                    # nothing ran: the clone is removed and nothing is written
                    self.assertEqual(_entry(self.root), before)
                    self.assertEqual(_entry(self.root)["plan_revision"], 1)
                self.assertTrue(job.validate_record(resumed, managed_repo=self.target, identity=FAKE_IDENTITY).valid)

    def test_abandon_ends_each_stage_without_carrying_out(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.killed_at(stage)
                before = self.managed_snapshot()
                before_state = _entry(self.root)
                ended = self.abandon()
                self.assertEqual(ended["status"], job.STATUS_FAILED)
                self.assertEqual(self.clone().exists(), CLONE_KEPT[stage])  # kept only when the worker ran
                if stage != "out":
                    self.assertEqual(self.managed_snapshot(), before)
                    self.assertEqual(_entry(self.root), before_state)
                else:  # members written before the kill stay; nothing more is written
                    self.assertEqual(_entry(self.root), before_state)

    def drifted(self):
        (self.root / "scripts" / "workflow_state.py").write_text(
            "# changed after the job was decided\n" + (self.root / "scripts" / "workflow_state.py").read_text())
        stub = fixtures.write_stub_workflow_manager(self.tmp / "drifted-manager", release=self.release(),
                                                    verify_exit=1)
        target = managed_repo.inspect_for_resume(self.root, manager_bin=str(stub))
        self.assertIsNotNone(target.drift)
        return target

    def test_a_drifted_release_ends_each_stage_without_carrying_out(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.setUp()
                shutil.rmtree(self.runtime / "jobs", ignore_errors=True)
                shutil.rmtree(self.runtime / "worker", ignore_errors=True)
                self.killed_at(stage)
                before_state = _entry(self.root)
                (ended,) = self.resume(self.drifted())
                self.assertEqual(ended["status"], job.STATUS_FAILED)
                self.assertEqual(ended["reconciliation_evidence"]["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
                self.assertEqual(self.clone().exists(), CLONE_KEPT[stage])
                self.assertEqual(_entry(self.root), before_state)

    def test_a_carry_record_never_reaches_the_worker_reconciler(self) -> None:
        self.killed_at("worker_ran")
        self.resume()
        self.worker_reconciler.assert_not_called()


class ImplRecoveryTest(ImplCase):
    """D9 for the implementation role: the landing is the record's own, and decides first."""

    def kill_a_managed_member_write(self):
        real = runtime_module.write_carried_file
        seen = []

        def write(root, rel, data, **kw):
            if Path(root) == self.root:
                seen.append(rel)
                raise KeyboardInterrupt
            return real(root, rel, data, **kw)

        return mock.patch.object(runtime_module, "write_carried_file", write)

    def killed_after_the_landing(self) -> tuple[dict, str]:
        self.worked()
        with self.kill_a_managed_member_write(), self.assertRaises(KeyboardInterrupt):
            self.step()
        record = self.only_record()
        self.assertEqual(record["codex_carry"]["stage"], "out")
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")
        return record, _git(self.root, "rev-parse", "HEAD").strip()

    def test_resume_after_the_landing_finishes_the_carry_out_and_lands_nothing_twice(self) -> None:
        record, head = self.killed_after_the_landing()
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_GATE_BLOCKED, resumed.get("reconciliation_evidence"))
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), head)
        self.assertIn("implementation_revision: 2", (self.root / ITEM / "current" / "IMPLEMENTATION_SUMMARY.md").read_text())
        self.assertFalse(self.clone().exists())

    def test_resume_before_the_landing_lands_then_carries_out(self) -> None:
        self.worked()
        with self.kill_at(job, "_complete_codex_carry"), self.assertRaises(KeyboardInterrupt):
            self.step()
        record = self.only_record()
        self.assertEqual(record["codex_carry"]["stage"], "worker_ran")
        base = record["codex_carry"]["base_commit"]
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), base)
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_GATE_BLOCKED, resumed.get("reconciliation_evidence"))
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{base}..HEAD").strip(), "2")
        self.assertEqual(resumed["codex_worker"]["landing"]["outcome"], "landed")

    def test_abandon_after_the_landing_carries_nothing_out_and_keeps_the_clone(self) -> None:
        record, head = self.killed_after_the_landing()
        before = (self.root / ITEM / "current" / "IMPLEMENTATION_SUMMARY.md").read_bytes()
        ended = self.abandon()
        self.assertEqual(ended["status"], job.STATUS_FAILED)
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), head)
        self.assertEqual((self.root / ITEM / "current" / "IMPLEMENTATION_SUMMARY.md").read_bytes(), before)
        self.assertTrue(self.clone().exists())

    def test_abandon_before_the_landing_lands_nothing_and_keeps_the_clone(self) -> None:
        self.worked()
        with self.kill_at(job, "_complete_codex_carry"), self.assertRaises(KeyboardInterrupt):
            self.step()
        base = self.only_record()["codex_carry"]["base_commit"]
        ended = self.abandon()
        self.assertEqual(ended["status"], job.STATUS_FAILED)
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), base)
        self.assertTrue(self.clone().exists())

    def test_a_drifted_release_after_the_landing_carries_nothing_out_and_keeps_the_clone(self) -> None:
        record, head = self.killed_after_the_landing()
        state = (self.root / "scripts" / "workflow_state.py")
        state.write_text("# changed after the job was decided\n" + state.read_text())
        stub = fixtures.write_stub_workflow_manager(self.tmp / "drifted-manager", release=self.release(), verify_exit=1)
        target = managed_repo.inspect_for_resume(self.root, manager_bin=str(stub))
        (ended,) = self.resume(target)
        self.assertEqual(ended["status"], job.STATUS_FAILED)
        self.assertEqual(ended["reconciliation_evidence"]["reason"], job.WORKFLOW_RELEASE_CHANGED_REASON)
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), head)
        self.assertTrue(self.clone().exists())


class GuardedRunMixin:
    """The tests of the guarded generator run itself (workflow-controller-h4e3b, D6 and D7): the guard is the real one,
    over the fixture's installed Workflow."""

    GUARD_CAUSE = None

    def assert_next_stage_passes(self, stage: str) -> None:
        kind = "plan" if stage == "plan" else "implementation"
        fixtures.run_workflow_python(self.root, f"""
entry = ws.json.loads((Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"]["wi-1"]
manifest = Path.cwd() / fingerprint.resolve_bundle_dir(Path.cwd(), "wi-1", stage={kind!r}) / "MANIFEST.md"
fingerprint.assert_local_generation_matches(Path.cwd(), manifest)
if {kind!r} == "plan":
    ws.validate_local_plan_review_preconditions_bound(Path.cwd(), entry)
else:
    ws.validate_local_implementation_review_preconditions(entry)
    ws.verify_implementation_review_bundle(Path.cwd(), "wi-1")
""")

    def test_a_clean_landing_is_regenerated_and_the_lane_continues(self) -> None:
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(record["regeneration"]["stage"], "finished")
        self.assertEqual(record["regeneration"]["argument"], self.ARGUMENT)
        self.assertEqual(record["regeneration"]["returncode"], 0)
        self.assertFalse(record.get("human_gate_pending"))
        self.assert_carry_ended(record)
        self.assert_next_stage_passes(self.ARGUMENT)
        self.assertTrue(job.validate_record(record, managed_repo=self.target, identity=FAKE_IDENTITY).valid)

    def test_a_kill_during_the_run_is_run_again_by_resume(self) -> None:
        self.worked()
        with mock.patch.object(generator_run.workflow_contract, "run_in_place", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.step()
        record = self.only_record()
        self.assertEqual(record["regeneration"]["stage"], "started")
        self.assertNotIn(record["status"], job.TERMINAL_STATUSES)
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assertEqual(resumed["regeneration"]["stage"], "finished")
        self.assert_next_stage_passes(self.ARGUMENT)

    def test_a_record_at_finished_is_run_again_by_resume(self) -> None:
        self.worked()
        with self.kill_at(job, "_protocol_resolution", after=1), self.assertRaises(KeyboardInterrupt):
            self.step()
        record = self.only_record()
        self.assertEqual(record["regeneration"]["stage"], "finished")
        self.assertNotIn(record["status"], job.TERMINAL_STATUSES)
        real = generator_run.workflow_contract.run_in_place
        with mock.patch.object(generator_run.workflow_contract, "run_in_place", side_effect=real) as spy:
            (resumed,) = self.resume()
        self.assertEqual(spy.call_count, 1, "resume did not run the generator again")
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assert_next_stage_passes(self.ARGUMENT)

    def test_abandon_runs_no_generator(self) -> None:
        self.worked()
        with self.kill_at(job, "_protocol_resolution", after=1), self.assertRaises(KeyboardInterrupt):
            self.step()
        with mock.patch.object(generator_run, "run", side_effect=AssertionError("the generator ran")):
            ended = self.abandon()
        self.assertEqual(ended["status"], job.STATUS_FAILED)


    def interrupted_publication(self, state: str) -> None:
        """I1 of the h4e3b review: the generator replaces ``current/`` before the archive, so a kill between the two
        leaves a manifest that is missing (``manifest_missing``) or already at the carried revision with the previous
        archive still in place (``stale_archive``). The persisted regeneration stage, not the first reconcile, decides
        that ``resume`` runs the generator again."""
        self.worked()
        real = generator_run.workflow_contract.run_in_place
        archive = self.root / ITEM / "review-bundle.tar.gz"
        before = archive.read_bytes() if archive.exists() else None
        calls: list[list[str]] = []

        def torn(root: Path, argv: list[str], **kwargs):
            calls.append(argv)
            if len(calls) > 1:
                return real(root, argv, **kwargs)
            real(root, argv, **kwargs)
            if state == "manifest_missing":
                (Path(root) / ITEM / "current" / "MANIFEST.md").unlink()
            elif before is None:
                archive.unlink(missing_ok=True)
            else:
                archive.write_bytes(before)
            raise KeyboardInterrupt

        with mock.patch.object(generator_run.workflow_contract, "run_in_place", side_effect=torn):
            with self.assertRaises(KeyboardInterrupt):
                self.step()
            self.assertEqual(self.only_record()["regeneration"]["stage"], "started")
            (resumed,) = self.resume()
        self.assertEqual(len(calls), 2, f"resume did not regenerate a bundle torn as {state}")
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assertEqual(resumed["regeneration"]["stage"], "finished")
        self.assert_next_stage_passes(self.ARGUMENT)

    def test_a_kill_between_the_current_and_the_archive_replacement_leaves_a_missing_manifest_that_resume_regenerates(
            self) -> None:
        self.interrupted_publication("manifest_missing")

    def test_a_kill_between_the_current_and_the_archive_replacement_leaves_a_stale_archive_that_resume_regenerates(
            self) -> None:
        self.interrupted_publication("stale_archive")

    def test_a_generator_that_survives_a_kill_is_ended_before_resume_runs_it_again(self) -> None:
        # I2 of the h4e3b review: the generator carries a tag of its own, recorded before it starts, from its first instruction, so a run that
        # outlives the Controller is found and ended by `resume` before the next run touches the bundle.
        self.worked()
        real_execute = generator_run.workflow_contract._execute_in_place
        survivors: list[subprocess.Popen] = []
        order: list[str] = []

        def spawn_then_die(argv, *, cwd, env, timeout):
            if survivors:
                order.append("second run: survivor " + ("gone" if survivors[0].poll() is not None else "ALIVE"))
                return real_execute(argv, cwd=cwd, env=env, timeout=timeout)
            survivors.append(subprocess.Popen(
                ["sleep", "60"], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True))
            raise KeyboardInterrupt

        try:
            with mock.patch.object(generator_run.workflow_contract, "_execute_in_place", side_effect=spawn_then_die):
                with self.assertRaises(KeyboardInterrupt):
                    self.step()
                environ = Path(f"/proc/{survivors[0].pid}/environ").read_bytes().split(b"\0")
                tags = [item.split(b"=", 1)[1].split(b":") for item in environ
                        if item.startswith(b"WORKFLOW_CONTROLLER_OWNERSHIP=")]
                recorded = self.only_record()["regeneration"]["ownership_tag"].encode()
                self.assertTrue(tags and recorded in tags[0], (tags, recorded))
                self.assertIsNone(survivors[0].poll())
                (resumed,) = self.resume()
        finally:
            for survivor in survivors:
                with contextlib.suppress(OSError):
                    survivor.kill()
                survivor.wait()
        self.assertEqual(order, ["second run: survivor gone"])
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assert_next_stage_passes(self.ARGUMENT)


class PlanGuardedRunTest(GuardedRunMixin, PlanCase):
    ARGUMENT = "plan"

    def test_a_refusal_ends_at_the_gate_with_its_cause_and_runs_no_generator(self) -> None:
        self.worked()
        previous = self.after

        def plant(clone: Path) -> None:
            previous(clone)
            (self.root / "scripts" / "sitecustomize.py").write_text("raise SystemExit('loaded')\n")

        self.after = plant
        with mock.patch.object(generator_run.workflow_contract, "run_in_place",
                               side_effect=AssertionError("the generator ran")):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        found = self.evidence(record)
        self.assertEqual(found["code"], job.CARRY_BUNDLE_GATE_CODE)
        self.assertEqual(found["regeneration"]["cause"], "surface_foreign_entry")
        self.assertEqual(record["regeneration"]["stage"], "refused")
        text = record["human_gate_pending"]["what_is_required"]
        self.assertIn("did not regenerate the bundle (surface_foreign_entry", text)
        self.assertIn(f"scripts/prepare-ai-review.sh", text)
        self.assertTrue(job.validate_record(record, managed_repo=self.target, identity=FAKE_IDENTITY).valid)

    def test_a_staging_directory_left_by_a_cut_off_run_is_discarded(self) -> None:
        self.worked()
        with mock.patch.object(generator_run.workflow_contract, "run_in_place", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.step()
        (self.root / ITEM / "current.staging-leftover").mkdir()
        (resumed,) = self.resume()
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assert_next_stage_passes("plan")


class ImplGuardedRunTest(GuardedRunMixin, ImplCase):
    ARGUMENT = "post-fix"

    def test_a_landing_that_touched_scripts_ends_at_the_gate(self) -> None:
        self.worked()
        previous = self.after

        def land_a_script(clone: Path) -> None:
            previous(clone)
            fixtures.run_workflow_python(clone, """
import subprocess
(Path.cwd() / "scripts" / "sitecustomize.py").write_text("raise SystemExit('loaded')\\n")
subprocess.run(["git", "add", "-f", "scripts/sitecustomize.py"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", "plant"], check=True)
""")

        self.after = land_a_script
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED, record.get("reconciliation_evidence"))
        self.assertEqual(self.evidence(record)["regeneration"]["cause"], "surface_landing_touched")
        self.assertIn("scripts/sitecustomize.py", self.evidence(record)["regeneration"]["detail"])

    def test_a_kill_in_the_middle_of_a_bundle_rewrite_is_run_again_by_resume(self) -> None:
        # D7: `post-fix` rewrites its generation in place, so a run cut off part-way leaves a torn bundle; `resume`
        # runs the guard and the generator again and ends with a complete bundle that verifies.
        self.worked()
        real = generator_run.workflow_contract.run_in_place
        calls: list[list[str]] = []

        seen: list[object] = []

        def torn(root: Path, argv: list[str], **kwargs):
            calls.append(argv)
            if len(calls) == 1:
                try:
                    completed = real(root, argv, **kwargs)
                    seen.append(completed.returncode)
                    bundle = Path(root) / ITEM / "current"
                    (bundle / "MANIFEST.md").unlink()
                    for path in sorted((bundle / "files").rglob("*")):
                        if path.is_file():
                            path.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])
                except Exception as exc:  # recorded for the assertion below, then re-raised
                    seen.append(repr(exc))
                    raise
                raise KeyboardInterrupt
            return real(root, argv, **kwargs)

        with mock.patch.object(generator_run.workflow_contract, "run_in_place", side_effect=torn):
            try:
                ended = self.step()
            except KeyboardInterrupt:
                ended = None
            record = self.only_record()
            self.assertIsNone(ended, (len(calls), seen, record.get("status"), record.get("regeneration"),
                                      record.get("reconciliation_evidence")))
            self.assertEqual(record["regeneration"]["stage"], "started")
            (resumed,) = self.resume()
        self.assertEqual(len(calls), 2, "resume did not run the generator again")
        self.assertEqual(resumed["status"], job.STATUS_FINISHED, resumed.get("reconciliation_evidence"))
        self.assertEqual(resumed["regeneration"]["stage"], "finished")
        self.assert_next_stage_passes(self.ARGUMENT)


#: ``/milestone-implement`` step 2's own writers for the wrap-up role, run in the clone (``workflow-controller-h4g``):
#: the phase commit when the item is still at ``IMPLEMENTING`` (``enter_self_reviewing_implementation``, one commit of the
#: state file alone, one ``Workflow-Work-Item`` trailer), and the generation record (``record_bundle_generation`` with
#: ``stage="implementation"``, its own commit with the two trailers), then the author-written bundle inputs.
SELF_REVIEW_PHASE = r'''
import datetime
import subprocess
root = Path.cwd()
wid = "wi-1"
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*args):
    return subprocess.run(["git", "-c", "user.name=Worker", "-c", "user.email=worker@example.invalid", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout.strip()
registry = ws.json.loads((root / f"docs/ai-workflow/registry/{wid}-registry.json").read_text())
ws.state_transaction(root, lambda s: ws.enter_self_reviewing_implementation(s, wid, registry, now=now()))
git("add", "docs/ai-workflow/WORKFLOW_STATE.json")
git("commit", "-m", f"enter self-review\n\nWorkflow-Work-Item: {wid}")
'''

SELF_REVIEW_RECORD = r'''
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
outcome = ws.resolve_bundle_generation_outcome(root, entry, base_commit=entry["base_commit"], head=head)
ws.state_transaction(root, lambda s: ws.record_bundle_generation(
    s, wid, stage="implementation", head=head, now=now(), outcome=outcome[0]))
entry = ws.json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
revision = entry["implementation_revision"]
current = root / ".ai-review" / wid / "current"
current.mkdir(parents=True, exist_ok=True)
(current / "IMPLEMENTATION_SUMMARY.md").write_text(f"# Summary\n\nimplementation_revision: {revision}\n")
(current / "TEST_RESULTS.md").write_text("# Test results\n\nall green\n")
(current / "CONTEXT_FILES.txt").write_text("README.md\n")
git("add", "docs/ai-workflow/WORKFLOW_STATE.json")
git("commit", "-m", f"generation record\n\nWorkflow-Bundle-Generation-Record: {wid}/{revision}\nWorkflow-Work-Item: {wid}")
content_id = ws.approval_review_content_id(
    root, stage="implementation", base_commit=entry["base_commit"], work_item_type="product", work_item_id=wid,
    head="HEAD", artifacts_path=fingerprint.artifacts_path_for_work_item(wid))
(current / "REVIEW_REQUEST.md").write_text(f"# Review request\n\nstage: implementation\nreview_content_id: {content_id}\n")
'''

#: Writes a landing the self-review profile must refuse, run in the clone after the worker's own writers.
PLANT_SCRIPT = r'''
import subprocess
(Path.cwd() / "scripts" / "sitecustomize.py").write_text("raise SystemExit('loaded')\n")
subprocess.run(["git", "add", "-f", "scripts/sitecustomize.py"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", "plant"], check=True)
'''

PLANT_APPROVAL = r'''
import subprocess
path = Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json"
document = ws.json.loads(path.read_text())
document["work_items"]["wi-1"]["plan_approval"]["status"] = "STALE"
path.write_text(ws.json.dumps(document, indent=2) + "\n")
subprocess.run(["git", "add", "docs/ai-workflow/WORKFLOW_STATE.json"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", "approval"], check=True)
'''

#: A state-file-only commit with the given message and phase, made in the managed repository by the predicate tests.
STATE_COMMIT = r'''
import subprocess, sys
path = Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json"
document = ws.json.loads(path.read_text())
document["work_items"]["wi-1"]["phase"] = sys.argv[1]
path.write_text(ws.json.dumps(document, indent=2) + "\n")
subprocess.run(["git", "add", "docs/ai-workflow/WORKFLOW_STATE.json"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", sys.argv[2]], check=True)
'''


#: Moves the seeded item to ``phase`` with every checkpoint ``COMPLETE`` and a current plan approval, then commits it
#: with the trailers the Workflow discovers the approval by (``tests/test_protocol_outcome._Env._approve_plan``).
_APPROVE_AND_COMPLETE = """
import subprocess, sys
root = Path.cwd()
path = root / "docs/ai-workflow/WORKFLOW_STATE.json"
state = ws.json.loads(path.read_text())
item = state["work_items"]["wi-1"]
rcid = item["plan_review_binding"]["published"]["review_content_id"]
item.update(phase=sys.argv[1], current_bundle_id=None, checkpoints={"CP1": {"status": "COMPLETE",
            "start_commit": item["base_commit"]}}, plan_approval={
    "status": "CURRENT", "basis": "EXTERNAL_APPROVE", "reviewed_bundle_id": "a" * 64,
    "approved_review_content_id": rcid, "review_content_manifest": [], "user_confirmation": "approved",
    "reviewed_content_commit": None, "waived_guarantees": []})
del item["plan_review_binding"]
path.write_text(ws.json.dumps(state, indent=2) + "\\n")
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-q", "-m",
                f"approve plan\\n\\nWorkflow-Plan-Approval: {rcid}\\nWorkflow-Work-Item: wi-1\\n"], check=True)
"""


def build_self_review_target(root: Path, phase: str) -> None:
    """The recorder fixture's repository with ``wi-1`` at ``phase`` (``SELF_REVIEWING_IMPLEMENTATION`` or
    ``IMPLEMENTING``), every checkpoint ``COMPLETE`` and a current, reachable plan approval: the checkpoint commit
    carries the Workflow's trailer pair and there is no generation record."""
    from tests import test_review_recorder as recorder  # noqa: PLC0415
    recorder.build_target(root, "plan")
    fixtures.commit_all(root, "seed state")
    fixtures.run_workflow_python(root, _APPROVE_AND_COMPLETE, phase)
    with (root / "README.md").open("a") as readme:
        readme.write("CP1 implemented\n")
    fixtures.commit_all(root, "implemented\n\nWorkflow-Checkpoint: CP1\nWorkflow-Work-Item: wi-1")


class SelfReviewCase(ApplyCase):
    """The wrap-up role (``milestone-implement-self-review``) routed to ``codex`` over a fixture whose checkpoints are
    all ``COMPLETE``; ``PHASE`` is the phase the item is at when the worker starts."""

    ROLE = routing.MILESTONE_IMPLEMENT_SELF_REVIEW
    PHASE = "SELF_REVIEWING_IMPLEMENTATION"
    GUARD_CAUSE = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._area = Path(tempfile.mkdtemp(prefix="selfreview-"))
        cls._built = cls._area / "target"
        build_self_review_target(cls._built, cls.PHASE)
        cls._kept = cls._area / "kept"
        shutil.copytree(cls._built, cls._kept, symlinks=True)

    def self_review_work(self, clone: Path) -> None:
        if self.PHASE == "IMPLEMENTING":
            fixtures.run_workflow_python(clone, SELF_REVIEW_PHASE)
        fixtures.run_workflow_python(clone, SELF_REVIEW_RECORD)

    def worked(self) -> None:
        self.script(message="self-reviewed")
        self.after = self.self_review_work

    def assert_next_stage_passes(self) -> None:
        fixtures.run_workflow_python(self.root, """
entry = ws.json.loads((Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"]["wi-1"]
manifest = Path.cwd() / fingerprint.resolve_bundle_dir(Path.cwd(), "wi-1", stage="implementation") / "MANIFEST.md"
fingerprint.assert_local_generation_matches(Path.cwd(), manifest)
ws.validate_local_implementation_review_preconditions(entry)
ws.verify_implementation_review_bundle(Path.cwd(), "wi-1")
""")


class SelfReviewRoleTest(SelfReviewCase):
    def test_a_clean_self_review_is_handed_to_the_guarded_implementation_run_and_ends_finished(self) -> None:
        self.worked()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(record["codex_carry"]["role"], routing.MILESTONE_IMPLEMENT_SELF_REVIEW)
        self.assertEqual(record["regeneration"]["stage"], "finished")
        self.assertEqual(record["regeneration"]["argument"], "implementation")
        self.assertEqual(_entry(self.root)["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(record["codex_worker"]["landing"]["outcome"], "landed")
        self.assertTrue(record["codex_worker"]["implementer"])
        self.assert_carry_ended(record)
        self.assert_next_stage_passes()

    def test_the_landing_is_judged_by_the_self_review_profile_and_the_implementer_surface(self) -> None:
        seen: list[dict] = []
        real = job.CODEX_LANDER

        def spy(*args, **kw):
            seen.append(kw)
            return real(*args, **kw)

        self.worked()
        with mock.patch.object(job, "CODEX_LANDER", spy):
            self.step()
        (kw,) = seen
        self.assertIs(kw["state_profile"], job.codex_worker_landing.SELF_REVIEW)
        self.assertIs(kw["surface"], job.codex_worker_landing.LandingSurface.IMPLEMENTER)
        self.assertEqual(kw["work_item_id"], WID)

    def test_a_scripts_commit_is_surface_refused_and_an_approval_field_is_state_touched_with_the_profile_name(self) -> None:
        for plant, cause in ((PLANT_SCRIPT, "surface"), (PLANT_APPROVAL, "state_touched")):
            with self.subTest(cause=cause):
                self.setUp()
                self.script(message="self-reviewed")

                def work(clone: Path, plant: str = plant) -> None:
                    self.self_review_work(clone)
                    fixtures.run_workflow_python(clone, plant)

                self.after = work
                before = _git(self.root, "rev-parse", "HEAD").strip()
                record = self.step()
                self.assertEqual(record["status"], job.STATUS_FAILED, record.get("reconciliation_evidence"))
                block = json.dumps(record["codex_worker"]["landing"])
                self.assertIn(cause, block)
                if cause == "state_touched":
                    self.assertIn("self-review", block)
                self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), before)  # nothing landed

    def test_a_dirty_managed_tree_is_managed_tree_not_clean_and_never_carry_foreign_dirt(self) -> None:
        self.worked()
        (self.root / "README.md").write_text("changed by someone else\n")
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.evidence(record)["code"], job.WORKER_NOT_STARTED_CODE)
        text = json.dumps(self.evidence(record))
        self.assertIn("managed_tree_not_clean", text)
        self.assertNotIn("carry_foreign_dirt", text)
        self.assertEqual(self.launched, [])
        self.assertFalse(self.clone().exists())

    def test_commit_pending_is_not_called_for_the_role(self) -> None:
        self.worked()
        with mock.patch.object(codex_carry, "commit_pending", side_effect=AssertionError("commit_pending")):
            record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(record["codex_carry"]["base_commit"], record["codex_carry"]["pre_step_head"])

    def test_a_checkpoint_claim_in_the_common_directory_is_refused_before_the_clone(self) -> None:
        self.worked()
        common = Path(_git(self.root, "rev-parse", "--git-common-dir").strip())
        claim = job.checkpoint_claims.claim_path(common if common.is_absolute() else self.root / common, WID)
        claim.parent.mkdir(parents=True, exist_ok=True)
        claim.write_text("{}")
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertIn("managed_claim_present", json.dumps(self.evidence(record)))
        self.assertEqual(self.launched, [])

    def test_the_workers_own_bundle_is_discarded_and_the_managed_bundle_is_the_controllers(self) -> None:
        self.worked()
        worked = self.after

        def with_forged_bundle(clone: Path) -> None:
            worked(clone)
            current = clone / ITEM / "current"
            current.mkdir(parents=True, exist_ok=True)
            (current / "MANIFEST.md").write_text("forged\n")
            (current / "DIFF.patch").write_text("forged\n")

        self.after = with_forged_bundle
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertNotIn("forged", (self.root / ITEM / "current" / "MANIFEST.md").read_text())
        self.assert_next_stage_passes()


class SelfReviewFromImplementingTest(SelfReviewCase):
    """The case an ``IMPLEMENTING`` item with every checkpoint ``COMPLETE`` is in: the phase commit, then the record."""

    PHASE = "IMPLEMENTING"

    def test_the_phase_commit_and_the_generation_record_land_and_the_item_ends_at_the_review_phase(self) -> None:
        self.worked()
        base = _git(self.root, "rev-parse", "HEAD").strip()
        record = self.step()
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(_git(self.root, "rev-list", "--count", f"{base}..HEAD").strip(), "2")
        self.assertEqual(record["regeneration"]["argument"], "implementation")
        self.assertEqual(_entry(self.root)["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assert_next_stage_passes()

    def test_a_partial_self_review_is_the_resolutions_own_ending_and_the_generator_is_never_started(self) -> None:
        self.script(message="half done")
        self.after = lambda clone: fixtures.run_workflow_python(clone, SELF_REVIEW_PHASE)
        with mock.patch.object(generator_run, "run", side_effect=AssertionError("generator started")):
            record = self.step()
        self.assertNotIn("regeneration", record)
        self.assertEqual(_entry(self.root)["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        self.assertEqual(record["codex_carry"]["stage"], "done")
        self.assertNotEqual((record.get("reconciliation_evidence") or {}).get("code"), job.CARRY_BUNDLE_GATE_CODE)
        self.assertNotIn("regenerat", json.dumps(record.get("human_gate_pending") or {}))


class SelfReviewHandoverTest(SelfReviewCase):
    """The hand-over predicate (D5) at its own level, over commits made in the managed repository."""

    def commit_work(self, *scripts: str) -> str:
        """Run ``scripts`` in the managed repository (the worker's own writers); return the head before them."""
        base = _git(self.root, "rev-parse", "HEAD").strip()
        for code in scripts:
            fixtures.run_workflow_python(self.root, code)
        return base

    def carry(self, base: str) -> dict:
        return {"work_item_id": WID, "codex_carry": {"role": self.ROLE, "base_commit": base, "stage": "done"}}

    @staticmethod
    def fields(phase: str) -> dict:
        return {"observed_phase_after": phase, "protocol": {"progress": "progress"}}

    def test_the_ordinary_record_with_an_advanced_revision_meets_it(self) -> None:
        base = self.commit_work(SELF_REVIEW_RECORD)
        self.assertTrue(job._self_review_handover(
            self.fields("AWAITING_LOCAL_IMPLEMENTATION_REVIEW"), self.carry(base), self.root))

    def test_the_wrong_phase_or_no_record_does_not(self) -> None:
        base = self.commit_work(SELF_REVIEW_RECORD)
        self.assertFalse(job._self_review_handover(
            self.fields("SELF_REVIEWING_IMPLEMENTATION"), self.carry(base), self.root))
        head = _git(self.root, "rev-parse", "HEAD").strip()
        self.assertFalse(job._self_review_handover(
            self.fields("AWAITING_LOCAL_IMPLEMENTATION_REVIEW"), self.carry(head), self.root))

    def test_the_recovered_record_leaves_the_revision_unchanged_and_names_the_record_at_the_base(self) -> None:
        self.commit_work(SELF_REVIEW_RECORD)  # the item already has a generation record at revision 1 ...
        first = _git(self.root, "rev-parse", "HEAD").strip()
        fixtures.run_workflow_python(  # ... then a plan-only re-approval puts it back at the self-review phase
            self.root, STATE_COMMIT, "SELF_REVIEWING_IMPLEMENTATION", "re-approved\n\nWorkflow-Work-Item: wi-1")
        base = _git(self.root, "rev-parse", "HEAD").strip()
        fixtures.run_workflow_python(
            self.root, STATE_COMMIT, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            "recovered record\n\nWorkflow-Bundle-Generation-Record: wi-1/1\nWorkflow-Work-Item: wi-1\n"
            f"Workflow-Supersedes: {first}")
        fields = self.fields("AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertTrue(job._self_review_handover(fields, self.carry(base), self.root))
        # a record that supersedes a commit that is not the item's current record at the base is not the recovered role
        subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-q", "--amend", "-m",
                        f"recovered record\n\nWorkflow-Bundle-Generation-Record: {WID}/1\nWorkflow-Work-Item: {WID}\n"
                        f"Workflow-Supersedes: {base}"], cwd=self.root, check=True)
        self.assertFalse(job._self_review_handover(fields, self.carry(base), self.root))

    #: Sets ``argv[1]`` (a JSON object) on the item and commits with message ``argv[2]``.
    SET_FIELDS = r'''
import subprocess, sys
path = Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json"
document = ws.json.loads(path.read_text())
document["work_items"]["wi-1"].update(ws.json.loads(sys.argv[1]))
path.write_text(ws.json.dumps(document, indent=2) + "\n")
subprocess.run(["git", "add", "docs/ai-workflow/WORKFLOW_STATE.json"], check=True)
subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-m", sys.argv[2]], check=True)
'''

    #: Prints whether the Workflow's own validator accepts the HEAD commit as a generation record.
    VALIDATE_HEAD = r'''
import subprocess
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
try:
    ws.validate_bundle_generation_record_commit(Path.cwd(), head, "wi-1")
    print("accepted")
except ws.MalformedBundleGenerationRecordCommitError:
    print("rejected")
'''

    def assert_workflow_rejects_and_no_handover(self, base: str) -> None:
        verdict = fixtures.run_workflow_python(self.root, self.VALIDATE_HEAD).stdout.strip()
        self.assertEqual(verdict, "rejected")
        self.assertFalse(job._self_review_handover(
            self.fields("AWAITING_LOCAL_IMPLEMENTATION_REVIEW"), self.carry(base), self.root))

    def test_an_ordinary_record_whose_phase_an_earlier_commit_already_moved_is_not_handed_over(self) -> None:
        base = _git(self.root, "rev-parse", "HEAD").strip()
        fixtures.run_workflow_python(
            self.root, self.SET_FIELDS, '{"phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}',
            "phase moved\n\nWorkflow-Work-Item: wi-1")
        fixtures.run_workflow_python(
            self.root, self.SET_FIELDS, '{"implementation_revision": 2}',
            f"record\n\nWorkflow-Bundle-Generation-Record: {WID}/2\nWorkflow-Work-Item: {WID}")
        self.assert_workflow_rejects_and_no_handover(base)

    def test_a_recovered_record_that_changes_the_reviewed_head_is_not_handed_over(self) -> None:
        self.commit_work(SELF_REVIEW_RECORD)
        first = _git(self.root, "rev-parse", "HEAD").strip()
        fixtures.run_workflow_python(
            self.root, STATE_COMMIT, "SELF_REVIEWING_IMPLEMENTATION", "re-approved\n\nWorkflow-Work-Item: wi-1")
        base = _git(self.root, "rev-parse", "HEAD").strip()
        fixtures.run_workflow_python(
            self.root, self.SET_FIELDS,
            '{"phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "reviewed_implementation_head": "' + "f" * 40 + '"}',
            f"recovered record\n\nWorkflow-Bundle-Generation-Record: {WID}/1\nWorkflow-Work-Item: {WID}\n"
            f"Workflow-Supersedes: {first}")
        self.assert_workflow_rejects_and_no_handover(base)

    def test_a_commit_that_touches_more_than_the_state_file_is_not_the_record(self) -> None:
        base = self.commit_work(SELF_REVIEW_RECORD)
        subprocess.run(["git", "reset", "-q", "--soft", "HEAD~1"], cwd=self.root, check=True)
        (self.root / "extra.txt").write_text("also\n")
        subprocess.run(["git", "add", "extra.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", "commit", "-q", "-m",
                        f"generation record\n\nWorkflow-Bundle-Generation-Record: {WID}/1\nWorkflow-Work-Item: {WID}"],
                       cwd=self.root, check=True)
        self.assertFalse(job._self_review_handover(
            self.fields("AWAITING_LOCAL_IMPLEMENTATION_REVIEW"), self.carry(base), self.root))


class SelfReviewSitesTest(unittest.TestCase):
    """The sites D6 moves to one predicate."""

    ROLE = routing.MILESTONE_IMPLEMENT_SELF_REVIEW

    def test_one_predicate_names_the_roles_that_land_commits(self) -> None:
        self.assertTrue(job._carries_landed_commits(self.ROLE))
        self.assertTrue(job._carries_landed_commits(routing.APPLY_IMPLEMENTATION_REVIEW))
        self.assertFalse(job._carries_landed_commits(routing.APPLY_PLAN_REVIEW))
        self.assertEqual(job._REGENERATION_STAGE[self.ROLE], "implementation")

    def test_the_gate_text_names_the_implementation_stage_and_the_landed_range(self) -> None:
        text = job._carry_gate_text(self.ROLE, "wi-1", "b" * 40, "workflow-controller resume", carry_base="a" * 40,
                                    landed_head="c" * 40)
        self.assertIn(f"git log -p {'a' * 40}..{'c' * 40}", text)
        self.assertIn(f"scripts/prepare-ai-review.sh {'b' * 40} implementation wi-1", text)
        self.assertNotIn("post-fix", text)

    def test_the_landing_profile_is_the_roles_and_the_checkpoint_roles_otherwise(self) -> None:
        self.assertIs(job._landing_state_profile({"codex_carry": {"role": self.ROLE}}),
                      job.codex_worker_landing.SELF_REVIEW)
        self.assertIs(job._landing_state_profile({"codex_worker": {"implementer": True}}),
                      job.codex_worker_landing.CHECKPOINT)

    def test_the_route_enters_the_carry_branch_while_the_adapter_role_is_still_the_implementer(self) -> None:
        route = mock.Mock(role=self.ROLE, harness="codex")
        self.assertTrue(job._is_codex_apply_route(route))
        self.assertTrue(job._is_codex_implement_route(route))
        self.assertFalse(job._is_codex_apply_route(mock.Mock(role=self.ROLE, harness="claude-code")))
        self.assertFalse(job._is_codex_apply_route(mock.Mock(role=routing.MILESTONE_IMPLEMENT, harness="codex")))
        self.assertEqual(routing.ADAPTER_ROLE[self.ROLE], harness.IMPLEMENTER)
        self.assertNotIn(self.ROLE, routing.LOCAL_APPLY_ROLES)

    def test_a_landing_that_changed_the_surface_ends_at_the_gate_naming_the_implementation_stage(self) -> None:
        record = {"codex_carry": {"stage": "done", "role": self.ROLE, "base_commit": "a" * 40},
                  "work_item_id": "wi-1", "pre_state": {"phase": "SELF_REVIEWING_IMPLEMENTATION"}}
        refused = generator_run.GeneratorRefused("surface_landing_touched", "scripts/x.py changed")
        with mock.patch.object(generator_run, "check_landing", side_effect=refused), \
                mock.patch.object(job, "_current_head", return_value="c" * 40), \
                mock.patch.object(job, "_carry_gate_fields",
                                  side_effect=lambda base, *a, **kw: {**base, "regeneration": kw["regeneration"]}):
            ended = job._landing_gate_before_queries(record, managed_repo=mock.Mock(root="/nowhere"), decision=None)
        self.assertEqual(ended["regeneration"]["cause"], "surface_landing_touched")
        self.assertEqual(ended["regeneration"]["argument"], "implementation")

    def test_abandon_of_a_record_with_a_begun_landing_runs_the_landings_recover_and_deletes_its_ref(self) -> None:
        record = {"job_id": "job1", "codex_carry": {"stage": "worker_ran", "role": self.ROLE},
                  "codex_worker": {"landing": {"outcome": "started"}}}
        recovery = mock.Mock(delete_ref=True, landing={"outcome": "refused"})
        with mock.patch.object(job.codex_worker_landing, "recover", return_value=recovery) as recover, \
                mock.patch.object(job, "_delete_landing_ref") as delete, \
                mock.patch.object(job, "_settle_codex_worker_dir"):
            ended, found = job._end_carry_without_carry_out(record, managed_repo=mock.Mock(), runtime_root=Path("/rt"))
        recover.assert_called_once()
        delete.assert_called_once()
        self.assertIs(found, recovery)
        self.assertEqual(ended["codex_worker"]["landing"], {"outcome": "refused"})


class RouteTest(unittest.TestCase):
    def test_the_apply_branch_is_keyed_on_the_roles_and_the_harness(self) -> None:
        def route(role: str, harness_name: str) -> routing.ResolvedRoute:
            return mock.Mock(role=role, harness=harness_name)

        for role in routing.LOCAL_APPLY_ROLES:
            self.assertTrue(job._is_codex_apply_route(route(role, "codex")))
            self.assertFalse(job._is_codex_apply_route(route(role, "claude-code")))
        for role in (routing.REVIEW_PLAN, routing.REVIEW_IMPLEMENTATION):
            self.assertFalse(job._is_codex_apply_route(route(role, "codex")))

    def test_the_gate_cause_is_not_a_refusal_cause(self) -> None:
        self.assertNotIn(job.CARRY_BUNDLE_GATE_CODE, codex_carry.CAUSES)


if __name__ == "__main__":
    unittest.main()
