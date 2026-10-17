"""`workflow-controller-review-modes` CP3: the same-provider review job.

At an external gate of row 14 the step runs the Claude harness when
``review_mode.choose`` selects it (a lowered policy signal under ``auto``, or
an explicit ``single-provider``): admitted against the Claude budget, recorded
with ``review.mode`` and ``review.lowered``, handed to ``record-external-result``
verbatim with a ``claude:`` run reference, and, when the Workflow refuses the
approval for the distinct-model rule, ended ``INCOMPLETE`` with a sticky stop
(D6). Runs on the real vendored Workflow of ``tests/test_review_job.py``;
``review.launch_review`` is its double (the real one is tested against the fake
``claude`` in ``tests/test_review_claude.py``). No test spends allowance or
makes a connection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, review, review_mode, usage, worker  # noqa: E402
from controller.anchor import OWNERSHIP_VAR  # noqa: E402
from tests import fixtures  # noqa: E402
from tests import test_review_job as rj  # noqa: E402
from tests.test_protocol_lifecycle import _APPROVE_PLAN, WID  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_review_mode import signal  # noqa: E402

_LOCAL_PLAN_MODEL = """
root = Path.cwd()
wid = sys.argv[1]
def mutate(state):
    local = state["work_items"][wid]["plan_review_stages"]["LOCAL_MODEL_PLAN_REVIEW"]
    local.update(reviewer_model="anthropic/claude-opus-5-5", verdict_sha256="0" * 64, run_ref="session:test")
    return state
ws.state_transaction(root, mutate)
"""


class SameProviderCase(rj._ReviewCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.dict(review.HARNESSES, {"claude": review.CLAUDE}))
        home = self.life.td / "claude-home"
        home.mkdir()
        (home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "orig"}}))
        self.same = {"harness": "claude", "model": "claude-opus-5-5", "effort": "high", "timeout_seconds": 600,
                     "binary": "claude", "operator_home": str(home)}

    def mode_step(self, mode="auto", capture="lowering_only", *, reviewer_options=None, usage_gate=None, **scenario):
        options = {"mode": mode, "source": "cli", "same_provider": self.same}
        double = self.double = rj.LaunchDouble(self, self.life, **scenario)
        forbidden = mock.Mock(side_effect=AssertionError("no Claude worker may launch"))
        read = None if capture is None else signal(capture)
        with mock.patch.object(job, "_review_mode_signal", return_value=read), \
                mock.patch.object(worker, "launch", forbidden), mock.patch.object(review, "launch_review", double):
            return job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                    reviewer=reviewer_options, usage_gate=usage_gate, review_mode_options=options)

    def refuse_distinct(self):
        """A same-provider APPROVE the real Workflow refuses: the local stage is already of family anthropic."""
        self.life.user_step(_LOCAL_PLAN_MODEL)
        return self.mode_step()


class RunTest(SameProviderCase):
    def test_a_lowered_policy_runs_the_claude_review_and_the_workflow_records_it(self) -> None:
        calls: list = []
        record = self.mode_step(usage_gate=self.gate(calls=calls))
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual(self.life.phase(), "AWAITING_PLAN_APPROVAL")
        [kw] = self.double.calls
        self.assertEqual((kw["harness_name"], kw["binary_name"], kw["model"], kw["effort"]),
                         ("claude", "claude", "claude-opus-5-5", "high"))
        self.assertEqual(kw["operator_home"], Path(self.same["operator_home"]))
        block = record["review"]
        self.assertEqual((block["harness"], block["mode"], block["mode_source"], block["lowered"]),
                         ("claude", "auto", "cli", True))
        self.assertEqual(block["signal_key"], ["warn", True, False, False])
        self.assertEqual(record["worker_route"]["harness"], "claude")
        self.assertEqual(record["worker_route"]["sources"]["model"], "reviewer.same_provider_model")
        # D8: admitted against the Claude budget, not the Codex one
        self.assertEqual([(provider, model) for provider, _, model in calls], [("claude", "claude-opus-5-5")])

    def test_the_verdict_is_handed_over_verbatim_with_a_claude_run_reference(self) -> None:
        real = job.protocol.record_external_result
        seen: list = []

        def spy(root, work_item, kind, path, **kwargs):
            seen.append((kind, Path(path).read_text(), kwargs["run_ref"]))
            return real(root, work_item, kind, path, **kwargs)

        with mock.patch.object(job.protocol, "record_external_result", spy):
            record = self.mode_step()
        [(kind, text, run_ref)] = seen
        self.assertEqual((kind, run_ref), ("plan_review_verdict", f"claude:{record['job_id']}"))
        self.assertIn("Reviewer model: anthropic/claude-opus-5-5", text)
        self.assertEqual(text, rj.verdict_text(self.double.calls[0]))  # byte for byte what the harness returned

    def test_a_revise_is_returned_to_the_workflow(self) -> None:
        record = self.mode_step(status="REVISE")
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["review"]["verdict_status"], "REVISE")
        self.assertEqual(record["review"]["ingested"], "recorded")
        self.assertNotEqual(self.life.phase(), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

    def test_single_provider_runs_only_with_the_signal(self) -> None:
        refused = self.mode_step("single-provider", None)
        self.assertEqual(refused["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])
        self.assertIn("(1)", json.dumps(refused))
        self.assertNotIn("(2)", json.dumps(refused))  # the human gate does not release an explicit single-provider
        self.assertIn("--review-mode manual", json.dumps(refused))
        ran = self.mode_step("single-provider")
        self.assertEqual(ran["status"], job.STATUS_FINISHED)
        self.assertEqual((ran["review"]["mode"], ran["review"]["lowered"]), ("single-provider", True))

    def test_a_limit_mid_review_ends_the_job_incomplete_naming_claude(self) -> None:
        record = self.mode_step(outcome="limit", detail="rejected")
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        self.assertEqual(record["reconciliation_evidence"]["code"], job.REVIEW_LIMIT_CODE)
        self.assertIn("The Claude plan review", job._review_message(record))

    def test_a_codex_review_records_the_mode_and_no_lowered_label(self) -> None:
        options = {"mode": "cross-model", "source": "file", "same_provider": self.same}
        double = rj.LaunchDouble(self, self.life)
        with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError)), \
                mock.patch.object(review, "launch_review", double):
            record = job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                      reviewer=self.reviewer, review_mode_options=options)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual((record["review"]["mode"], record["review"]["mode_source"]), ("cross-model", "file"))
        self.assertNotIn("lowered", record["review"])
        self.assertEqual(record["review"]["harness"], "codex")


class UsageTest(SameProviderCase):
    """A Claude review is charged like a Claude worker: its stream's first reading lowers a stale baseline."""

    def claude_readings(self, five, resets, observed):
        return [usage.make_reading("claude", usage.FIVE_HOUR, five, resets, observed, "test"),
                usage.make_reading("claude", usage.WEEKLY, 5.0, self.now + 7 * 86400, observed, "test")]

    def seed_expired_window(self) -> None:
        """The admission reading's window had already reset when the reservation was made."""
        usage.record_reading(self.runtime_root, self.claude_readings(10.0, self.now - 100, self.now - 500))

    def test_the_first_and_last_readings_charge_the_measured_delta_after_a_reset(self) -> None:
        self.seed_expired_window()
        window = self.now + 3600
        record = self.mode_step(usage_gate=self.gate(), readings=self.claude_readings(7.0, window, self.now + 20),
                                first_readings=self.claude_readings(2.0, window, self.now + 10))
        self.assertEqual(record["status"], job.STATUS_FINISHED, record.get("reconciliation_evidence"))
        self.assertEqual((record["usage"]["five_hour_first"], record["usage"]["five_hour_end"]), (2.0, 7.0))
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["five_hour"], 5.0)  # the existing worker accounting's figure, not the forecast of 8

    def test_without_a_first_reading_the_stale_baseline_charges_the_forecast(self) -> None:
        self.seed_expired_window()
        record = self.mode_step(usage_gate=self.gate(), readings=self.claude_readings(7.0, self.now + 3600, self.now + 20))
        [entry] = self.shared()["ledger"]
        self.assertEqual((entry["delta"], entry.get("charged")), ("after_reset", 8.0))

    def test_a_decreasing_first_reading_is_the_baseline_and_a_limit_still_charges_the_measured_delta(self) -> None:
        self.seed_expired_window()
        window = self.now + 3600
        record = self.mode_step(usage_gate=self.gate(), outcome="limit", detail="rejected",
                                readings=self.claude_readings(9.0, window, self.now + 20),
                                first_readings=self.claude_readings(1.0, window, self.now + 10))
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE)
        [entry] = self.shared()["ledger"]
        self.assertEqual(entry["five_hour"], 8.0)

    def test_a_replay_of_the_pending_block_lowers_the_baseline_like_the_live_completion(self) -> None:
        self.seed_expired_window()
        window = self.now + 3600
        pending = job._codex_end_block({"token": "t"}, self.claude_readings(7.0, window, self.now + 20),
                                       self.claude_readings(2.0, window, self.now + 10))
        first = usage._first_readings_from_block(pending)
        self.assertEqual({w: r["percent"] for w, r in first.items()}, {usage.FIVE_HOUR: 2.0, usage.WEEKLY: 5.0})


class ClaudeRecoveryTest(SameProviderCase):
    """`resume` of a Claude review a dead Controller left: the recorded harness's credentials are copied back
    (digest-checked) before the scratch home is removed; ``.claude.json`` is never copied."""

    def killed(self, refreshed: str = "rotated") -> tuple[dict, Path]:
        operator = Path(self.same["operator_home"])
        procs: list = []

        class Dies(BaseException):
            pass

        def launch(**kw):
            layout = review.JobLayout(kw["runtime_root"], kw["job_id"])
            layout.claude_home.mkdir(parents=True, exist_ok=True)
            (layout.claude_home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": refreshed}}))
            (layout.claude_home / ".claude.json").write_text(json.dumps({"state": "private"}))
            layout.tree.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                    env={**os.environ, OWNERSHIP_VAR: kw["job_id"]}, start_new_session=True,
                                    close_fds=True, stdin=subprocess.DEVNULL)
            self.addCleanup(lambda: (proc.kill(), proc.wait()))
            procs.append(proc)
            kw["on_spawn"](proc.pid, proc.pid)
            raise Dies()

        options = {"mode": "auto", "source": "cli", "same_provider": self.same}
        with mock.patch.object(job, "_review_mode_signal", return_value=signal("lowering_only")), \
                mock.patch.object(worker, "launch", mock.Mock()), mock.patch.object(review, "launch_review", launch):
            with self.assertRaises(Dies):
                job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                 usage_gate=self.gate(), review_mode_options=options)
        [record] = self.records()
        self.assertEqual(record["review"]["harness"], "claude")
        return record, operator

    def resume(self) -> dict:
        [resumed] = [r for r in job.resume(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root)
                     if r.get("kind") == "review"]
        return resumed

    def test_refreshed_claude_credentials_are_copied_back_before_the_scratch_home_is_removed(self) -> None:
        record, operator = self.killed()
        resumed = self.resume()
        self.assertEqual(resumed["review"]["credentials"], "copied")
        self.assertEqual(json.loads((operator / ".credentials.json").read_text()),
                         {"claudeAiOauth": {"accessToken": "rotated"}})
        self.assertFalse((operator / ".claude.json").exists())  # the job's private state is never copied back
        layout = review.JobLayout(self.runtime_root, record["job_id"])
        self.assertFalse(layout.claude_home.exists())

    def test_an_operator_file_that_changed_meanwhile_is_kept(self) -> None:
        record, operator = self.killed()
        (operator / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "newer-login"}}))
        resumed = self.resume()
        self.assertEqual(resumed["review"]["credentials"], "operator-changed")
        self.assertEqual(json.loads((operator / ".credentials.json").read_text()),
                         {"claudeAiOauth": {"accessToken": "newer-login"}})

    def test_an_unchanged_or_invalid_scratch_credential_is_not_copied(self) -> None:
        record, operator = self.killed(refreshed="orig")
        resumed = self.resume()
        self.assertEqual(resumed["review"]["credentials"], "unchanged")
        self.assertEqual(json.loads((operator / ".credentials.json").read_text()),
                         {"claudeAiOauth": {"accessToken": "orig"}})


_ADOPT_LOWERING = """
import json
import subprocess
import workflow_gate_policy as g
policy = {"schema_version": 1, "gates": {"plan_approval": {"require": []}, "technical_approval": {"require": []}}}
Path("docs/ai-workflow/GATE_POLICY.json").write_text(json.dumps(policy, indent=2))
for args in (["add", "-A"], ["commit", "-q", "-m", "gate policy"]):
    subprocess.run(["git", *args], check=True, capture_output=True)
ws.adopt_gate_policy(Path.cwd(), confirmation="I adopt gate_policy " + g.policy_digest(policy)[:12],
                     now="2026-10-17T10:00:00Z")
print(subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip())
"""

_LOCAL_ANTHROPIC = """
root = Path.cwd()
wid, stage = sys.argv[1], sys.argv[2]
key, role = (("plan_review_stages", "LOCAL_MODEL_PLAN_REVIEW") if stage == "plan"
             else ("implementation_review_stages", "LOCAL_MODEL_IMPLEMENTATION_REVIEW"))
def mutate(state):
    state["work_items"][wid][key][role].update(
        reviewer_model="anthropic/claude-opus-5-5", verdict_sha256="0" * 64, run_ref="session:test")
    return state
ws.state_transaction(root, mutate)
"""

_APPLY_PLAN_REVIEW = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
item = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
revision = item["plan_revision"] + 1
plan = root / item["plan_path"]
plan.write_text(plan.read_text().replace(f"(Revision {revision - 1})", f"(Revision {revision})")
                + f"\\nRevision {revision}: the reviewer's finding is applied.\\n")
checkpoints = [{"id": "CP1", "name": "one checkpoint", "depends_on": [], "complexity": 1, "session_target": 1}]
registry = ws.generate_registry(wid, revision, checkpoints)
mapping = ws.generate_mapping(wid, {"R1": {"description": "one requirement", "checkpoint_ids": ["CP1"]}}, registry=registry)
ws.write_registry_and_mapping(root, Path(item["registry_path"]), Path(item["mapping_path"]), registry, mapping)
rcid, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(root, wid)
ws.state_transaction(root, lambda s: ws.publish_plan_revision(s, wid, revision, now(), review_content_id=rcid))
inputs = root / fingerprint.resolve_plan_review_inputs_dir(root, wid)
inputs.mkdir(parents=True, exist_ok=True)
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
(inputs / "REVIEW_REQUEST.md").write_text(f"# Review request\\n\\nstage: plan\\nwork item: {wid}\\nreview_content_id: {rcid}\\n")
(inputs / "TEST_RESULTS.md").write_text(f"stage: plan (revision {revision})\\nhead: {head}\\n\\nNo checks at the plan stage.\\n")
(inputs / "CONTEXT_FILES.txt").write_text("README.md\\n")
subprocess.run(["./scripts/prepare-ai-review.sh", item["base_commit"], "plan", wid], cwd=root, check=True,
               capture_output=True, text=True)
binding = ws.verify_plan_review_bundle(root, wid)
ws.state_transaction(root, lambda s: ws.bind_plan_review_bundle(s, wid, binding=binding, now=now()))
"""

_APPLY_IMPLEMENTATION_REVIEW = """
import datetime, subprocess
root = Path.cwd()
wid = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def git(*a):
    return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
with open(root / "app" / "CP1.txt", "a") as fh:
    fh.write("the reviewer's finding is applied\\n")
git("add", "-A")
git("commit", "-q", "-m", "Apply the review finding")
ws.state_transaction(root, lambda s: ws.record_bundle_generation(
    s, wid, stage="post-fix", head=git("rev-parse", "HEAD"), now=now, outcome="ordinary"))
item = json.loads((root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())["work_items"][wid]
revision = item["implementation_revision"]
git("add", "docs/ai-workflow/WORKFLOW_STATE.json")
git("commit", "-q", "-m", f"Record post-fix bundle generation\\n\\nWorkflow-Bundle-Generation-Record: {wid}/{revision}\\nWorkflow-Work-Item: {wid}\\n")
digest = ws.approval_review_content_id(
    root, stage="implementation", base_commit=item["base_commit"], work_item_type=item["work_item_type"],
    work_item_id=wid, head="HEAD", artifacts_path=fingerprint.artifacts_path_for_work_item(wid))
bundle_dir = root / ".ai-review" / wid / "current"
bundle_dir.mkdir(parents=True, exist_ok=True)
(bundle_dir / "REVIEW_REQUEST.md").write_text(f"# Review request\\n\\nstage: implementation\\nreview_content_id: {digest}\\n")
(bundle_dir / "IMPLEMENTATION_SUMMARY.md").write_text(f"implementation_revision: {revision}\\n\\nbuilt\\n")
(bundle_dir / "TEST_RESULTS.md").write_text("all checks passed\\n")
(bundle_dir / "CONTEXT_FILES.txt").write_text("README.md\\n")
subprocess.run(["bash", "scripts/prepare-ai-review.sh", item["base_commit"], "post-fix", wid],
               cwd=root, check=True, capture_output=True, text=True)
"""


class AdoptedPolicyLoopTest(SameProviderCase):
    """Same-family approval under a **real** lowered policy: the Workflow's own adoption (no injected signal), a
    recorded Anthropic local reviewer, and the whole REVISE / apply / APPROVE loop at both stages."""

    def setUp(self) -> None:
        real_seed = fixtures.run_workflow_seed

        def seed(root, stage, **kw):
            head = fixtures.run_workflow_python(root, _ADOPT_LOWERING).stdout.strip()
            return real_seed(root, stage, **{**kw, "base_commit": head})

        self.enterContext(mock.patch.object(fixtures, "run_workflow_seed", seed))
        super().setUp()
        launched = self.life.effect

        def effect(task: str) -> None:
            token = task.split()[0].lstrip("/")
            if token == "apply-plan-review":
                self.life.tasks.append(token)
                self.life.workflow(_APPLY_PLAN_REVIEW, WID)
            elif token == "apply-implementation-review":
                self.life.tasks.append(token)
                self.life.workflow(_APPLY_IMPLEMENTATION_REVIEW, WID)
            else:
                launched(task)

        self.life.effect = effect

    def external(self, status: str) -> dict:
        """One step at the open external gate: auto, the signal read from the real ``verify``."""
        double = self.double = rj.LaunchDouble(self, self.life, status=status)
        options = {"mode": "auto", "source": "cli", "same_provider": self.same}
        with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError("no worker"))), \
                mock.patch.object(review, "launch_review", double):
            return job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                    usage_gate=self.gate(), review_mode_options=options)

    def stage_entry(self, stage: str, role: str) -> dict:
        key = "plan_review_stages" if stage == "plan" else "implementation_review_stages"
        return fixtures.state_entry(self.life.root, WID)[key][role]

    def loop(self, stage: str, gate_phase: str) -> None:
        local = {"plan": "LOCAL_MODEL_PLAN_REVIEW", "implementation": "LOCAL_MODEL_IMPLEMENTATION_REVIEW"}[stage]
        external = {"plan": "MANUAL_EXTERNAL_PLAN_REVIEW", "implementation": "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"}[stage]
        revising = {"plan": "REVISING_PLAN", "implementation": "APPLYING_REVIEW_FEEDBACK"}[stage]
        again = {"plan": "AWAITING_LOCAL_PLAN_REVIEW", "implementation": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}[stage]
        self.assertEqual(self.life.phase(), gate_phase)
        real = job._review_mode_signal(self.life.inspect())
        self.assertEqual((real.status, real.holds(stage)), ("warn", True))  # the Workflow's own answer, not a fixture
        self.life.user_step(_LOCAL_ANTHROPIC, stage)
        self.assertEqual(self.stage_entry(stage, local)["reviewer_model"], "anthropic/claude-opus-5-5")

        revise = self.external("REVISE")  # 1. the same-family REVISE is recorded
        self.assertEqual(revise["status"], job.STATUS_FINISHED, revise.get("reconciliation_evidence"))
        self.assertEqual((revise["review"]["harness"], revise["review"]["lowered"], revise["review"]["verdict_status"]),
                         ("claude", True, "REVISE"))
        self.assertEqual(self.life.phase(), revising)

        applying = self.life.step()  # 2. the apply worker, then a fresh local review (Anthropic again)
        self.assertEqual(applying["status"], job.STATUS_FINISHED)
        self.assertEqual(self.life.phase(), again)
        self.life.run_until(gate_phase)
        self.life.user_step(_LOCAL_ANTHROPIC, stage)

        approve = self.external("APPROVE")  # 3. the same-family APPROVE is admitted under the adopted policy
        self.assertEqual(approve["status"], job.STATUS_FINISHED, approve.get("reconciliation_evidence"))
        self.assertEqual((approve["review"]["lowered"], approve["review"]["ingested"]), (True, "recorded"))
        self.assertEqual(self.stage_entry(stage, local)["reviewer_model"], "anthropic/claude-opus-5-5")
        recorded = self.stage_entry(stage, external)  # the external stage records the Claude run, verbatim verdict
        self.assertEqual((recorded["verdict"], recorded["run_ref"]), ("APPROVE", f"claude:{approve['job_id']}"))
        self.assertIn("Reviewer model: anthropic/claude-opus-5-5", rj.verdict_text(self.double.calls[0]))

    def test_the_plan_stage_loop_under_the_adopted_policy(self) -> None:
        self.loop("plan", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(self.life.phase(), "AWAITING_PLAN_APPROVAL")

    def test_the_implementation_stage_loop_under_the_adopted_policy(self) -> None:
        self.life.user_step(_LOCAL_ANTHROPIC, "plan")
        self.assertEqual(self.external("APPROVE")["status"], job.STATUS_FINISHED)
        self.life.user_step(_APPROVE_PLAN)
        self.life.run_until("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.loop("implementation", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(self.life.phase(), "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")


class DefaultPolicyRefusalTest(SameProviderCase):
    """Under the unchanged default policy nothing runs a same-family review: the real ``verify`` reports no signal."""

    def test_an_explicit_single_provider_is_refused_before_launch_and_auto_stays_at_the_gate(self) -> None:
        self.life.user_step(_LOCAL_ANTHROPIC, "plan")
        for mode in ("single-provider", "auto"):
            double = rj.LaunchDouble(self, self.life)
            options = {"mode": mode, "source": "cli", "same_provider": self.same}
            with mock.patch.object(worker, "launch", mock.Mock(side_effect=AssertionError("no worker"))), \
                    mock.patch.object(review, "launch_review", double):
                record = job.execute_step(self.life.inspect(), identity=IDENTITY, runtime=self.runtime_root,
                                          usage_gate=self.gate(), review_mode_options=options)
            self.assertEqual((mode, record["status"], double.calls), (mode, job.STATUS_GATE_BLOCKED, []))
        self.assertEqual(self.life.phase(), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")


class ReportTest(SameProviderCase):
    """``job.review_mode_report``: what ``explain`` says the next step does, read-only (review-modes D7)."""

    def report(self, mode="auto", capture="lowering_only", *, reviewer_options=None):
        from controller import protocol_decision, target_state
        target = self.life.inspect()
        item = target_state.select_work_item(target_state.read(target), work_item_id=rj.WID)
        decision, _ = protocol_decision.decide_after_preflight(target, item)
        read = None if capture is None else signal(capture)
        options = {"mode": mode, "source": "cli", "same_provider": self.same}
        with mock.patch.object(job, "_review_mode_signal", return_value=read):
            return job.review_mode_report(self.runtime_root, target, rj.WID, decision, reviewer_options, options)

    def test_each_choice_is_reported_with_the_mode_and_its_source(self) -> None:
        self.assertEqual(self.report("manual"), {
            "mode": "manual", "source": "cli", "stage": "plan", "choice": "gate", "harness": None, "lowered": False})
        self.assertEqual(self.report("auto", None)["choice"], "gate")
        run = self.report("single-provider")
        self.assertEqual((run["choice"], run["harness"], run["lowered"]), ("review", "claude", True))
        refused = self.report("cross-model", None)
        self.assertEqual(refused["choice"], "refused")
        self.assertIn("reviewer.harness is unset", refused["reason"])
        codex = self.report("auto", None, reviewer_options=self.reviewer)
        self.assertEqual((codex["choice"], codex["harness"], codex["lowered"]), ("review", "codex", False))

    def test_the_sticky_stop_is_reported_and_nothing_is_written(self) -> None:
        self.refuse_distinct()
        before = sorted(p.name for p in (self.runtime_root / "jobs").glob("*"))
        launched = len(self.double.calls)
        stopped = self.report("auto")
        self.assertEqual((stopped["choice"], stopped["lowered"]), ("refused", False))
        self.assertIn("no review is paid for twice", stopped["reason"])
        self.assertNotIn("(2)", self.report("single-provider")["reason"])
        self.assertEqual(self.report("manual")["choice"], "gate")
        self.assertEqual(sorted(p.name for p in (self.runtime_root / "jobs").glob("*")), before)
        self.assertEqual(len(self.double.calls), launched)

    def test_a_decision_that_is_not_an_external_review_gate_has_no_report(self) -> None:
        from controller import protocol_decision, target_state
        target = self.life.inspect()
        item = target_state.select_work_item(target_state.read(target), work_item_id=rj.WID)
        decision, _ = protocol_decision.decide_after_preflight(target, item)
        other = mock.Mock(protocol=mock.Mock(disposition="human_gate", action_id="plan.approve"))
        self.assertIsNone(job.review_mode_report(self.runtime_root, target, rj.WID, other, None, None))
        self.assertIsNotNone(job.review_mode_report(self.runtime_root, target, rj.WID, decision, None, None))


class DistinctRefusalTest(SameProviderCase):
    def test_the_workflows_refusal_ends_the_job_incomplete_and_names_the_ways_out(self) -> None:
        record = self.refuse_distinct()
        self.assertEqual(record["status"], job.STATUS_INCOMPLETE, record.get("reconciliation_evidence"))
        evidence = record["reconciliation_evidence"]
        self.assertEqual(evidence["code"], job.REVIEW_DISTINCT_MODEL_CODE)
        for words in ("(1)", "(2)", "(3)", "GATE_POLICY", "GATE_POLICY.json", "--review-mode cross-model"):
            self.assertIn(words, evidence["message"])
        self.assertEqual(self.life.phase(), "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")  # nothing was recorded
        self.assertIn("The Claude plan review", job._review_message(record))

    def test_the_next_step_stops_without_launching_while_nothing_changed(self) -> None:
        self.refuse_distinct()
        for _ in range(2):  # repeatable
            record = self.mode_step()
            self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
            self.assertIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(record))
            self.assertEqual(self.double.calls, [])
        self.assertEqual(len(self.records()), 1)
        # an unrelated unreferenced_runs warning leaves the key, and so the stop, alone
        record = self.mode_step(capture="lowering_plus_unreferenced_runs")
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])

    def test_single_provider_stops_too_and_leaves_path_two_out(self) -> None:
        self.refuse_distinct()
        record = self.mode_step("single-provider")
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.double.calls, [])
        self.assertNotIn("(2)", json.dumps(record))
        # turning the gate human (a file_tightened clause) cancels the signal: refused again before launch,
        # pointing to --review-mode manual instead of path 2
        human = self.mode_step("single-provider", "lowering_plus_file_tightened")
        self.assertEqual(human["status"], job.STATUS_GATE_BLOCKED)
        text = json.dumps(human)
        self.assertIn(review_mode.REVIEW_MODE_UNAVAILABLE, text)
        self.assertIn("--review-mode manual", text)
        self.assertNotIn("(2)", text)
        self.assertEqual(self.double.calls, [])

    def test_a_mode_or_harness_that_selects_the_gate_or_a_distinct_provider_goes_ahead(self) -> None:
        self.refuse_distinct()
        manual = self.mode_step("manual")
        self.assertEqual(manual["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(manual))
        # a cancelled signal (the gate turned human: a file_tightened clause) releases auto to the ordinary gate
        gate = self.mode_step("auto", "lowering_plus_file_tightened")
        self.assertEqual(gate["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn(review_mode.REVIEW_MODE_UNAVAILABLE, json.dumps(gate))
        self.assertEqual(self.double.calls, [])
        # a distinct provider runs, at an unchanged state identity and signal
        codex = self.mode_step("auto", reviewer_options=self.reviewer)
        self.assertEqual(codex["status"], job.STATUS_FINISHED)
        self.assertEqual(self.double.calls[0]["harness_name"], "codex")

    def test_cross_model_with_codex_launches_after_the_refusal(self) -> None:
        self.refuse_distinct()
        record = self.mode_step("cross-model", reviewer_options=self.reviewer)
        self.assertEqual(record["status"], job.STATUS_FINISHED)

    def test_a_changed_state_identity_or_signal_key_releases_the_stop(self) -> None:
        self.refuse_distinct()
        root, wid = self.life.root, rj.WID
        key = signal("lowering_only").key("plan")
        [prior] = self.records()
        identity = prior["review"]["state_identity"]
        stop = lambda *a: job._sticky_review_stop(self.runtime_root, root, wid, *a)  # noqa: E731
        self.assertTrue(stop("plan", identity, key))
        self.assertFalse(stop("plan", "other-identity", key))
        self.assertFalse(stop("implementation", identity, key))
        self.assertFalse(stop("plan", identity, signal("lowering_plus_file_tightened").key("plan")))
        self.assertFalse(stop("plan", None, key))

    def test_a_later_review_job_supersedes_the_refusal(self) -> None:
        self.refuse_distinct()
        self.assertEqual(self.mode_step("auto", reviewer_options=self.reviewer)["status"], job.STATUS_FINISHED)
        [prior, later] = sorted(self.records(), key=lambda r: (r["created_at"], r["job_id"]))
        self.assertFalse(job._sticky_review_stop(
            self.runtime_root, self.life.root, rj.WID, "plan", prior["review"]["state_identity"],
            signal("lowering_only").key("plan")))


class RemediesTest(unittest.TestCase):
    def test_the_paths_named_follow_the_mode(self) -> None:
        auto = review_mode.remedies("auto", "implementation")
        self.assertIn("technical_approval.require", auto)
        self.assertIn("(2)", auto)
        single = review_mode.remedies("single-provider", "plan")
        self.assertNotIn("(2)", single)
        self.assertIn("--review-mode manual", single)
        self.assertNotIn("--review-mode manual", auto)


if __name__ == "__main__":
    unittest.main()
