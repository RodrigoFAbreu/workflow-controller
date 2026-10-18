"""The ``pr_review_result`` reporter (``workflow-controller-pr-defect-loop`` CP2, D4-D6).

Pure parts first (``controller.pr_report``), tied to the vendored 2.12.0
Workflow by agreement tests that run the Workflow's own parser, then the
report step (``job._run_pr_report``) over a real 2.12.0 lifecycle fixture with
a fake forge. Nothing reaches GitHub: the Controller's query is a fake
``Forge`` and the fixture's ``origin`` is only a github.com URL for the
repository name.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import forge as forge_mod, gitrepo, job, milestone_branch, pr_report, protocol, protocol_decision  # noqa: E402
from controller import runtime, target_state  # noqa: E402
from controller.errors import ForgeUndecidableError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_protocol_decision import IDENTITY  # noqa: E402
from tests.test_protocol_lifecycle import WID, GateLifecycle  # noqa: E402

COMMIT = "a" * 40
CI_TEXT = "the automatic acceptance gate awaits a pull-request fact: ci_green: the checks are pending"
OK = {"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}
RUNNING = {"name": "ci", "status": "IN_PROGRESS", "conclusion": ""}
FAILED = {"name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}


def raw_of(commit: str = COMMIT, *, rollup=(OK,), decision: str = "", state: str = "OPEN", number: int = 7,
           reviews=()) -> str:
    return json.dumps([{"number": number, "url": f"https://github.com/owner/repo/pull/{number}", "state": state,
                        "headRefOid": commit, "reviewDecision": decision, "reviews": list(reviews),
                        "statusCheckRollup": list(rollup)}])


class PurePartsTest(unittest.TestCase):
    def test_the_forge_block_is_the_verbatim_output_with_its_digest(self) -> None:
        raw = raw_of()
        block = pr_report.forge_block(repository="owner/repo", queried_commit=COMMIT, raw=raw,
                                      fetched_at="2026-10-18T00:00:00Z", version="1.14.0")
        self.assertEqual(block["raw"], raw)
        self.assertEqual(block["raw_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(block["fetched_by"], {"name": "workflow-controller", "version": "1.14.0"})
        self.assertEqual((block["source"], block["query"]), ("github", "pr-list-v1"))
        self.assertEqual(pr_report.input_document(block, "controller:j"), {"forge": block, "run_ref": "controller:j"})

    def test_the_idempotence_comparison_is_against_the_last_report_digest(self) -> None:
        raw = raw_of()
        self.assertFalse(pr_report.is_unchanged(raw, None))
        self.assertTrue(pr_report.is_unchanged(raw, pr_report.raw_sha256(raw)))
        self.assertFalse(pr_report.is_unchanged(raw_of(rollup=(FAILED,)), pr_report.raw_sha256(raw)))

    def test_the_wait_predicate_needs_all_three_conditions(self) -> None:
        pending = raw_of(rollup=(RUNNING,))
        wait = pr_report.pending_checks(pending, row="38f", reason_text=CI_TEXT)
        self.assertEqual(wait, pr_report.Wait(True, empty=False))
        # (a) row 38g never waits
        self.assertFalse(pr_report.pending_checks(pending, row="38g", reason_text=CI_TEXT).applies)
        # (b) the stop text must name ci_green
        self.assertFalse(pr_report.pending_checks(pending, row="38f", reason_text="pr_fact_current: stale").applies)
        # (c) a standing objection is reported, not waited on
        self.assertFalse(pr_report.pending_checks(raw_of(rollup=(RUNNING,), decision="CHANGES_REQUESTED"),
                                                  row="38f", reason_text=CI_TEXT).applies)
        self.assertFalse(pr_report.pending_checks(raw_of(rollup=(RUNNING, FAILED)), row="38f",
                                                  reason_text=CI_TEXT).applies)
        self.assertFalse(pr_report.pending_checks(raw_of(rollup=(OK,)), row="38f", reason_text=CI_TEXT).applies)

    def test_an_empty_rollup_waits_and_says_so(self) -> None:
        wait = pr_report.pending_checks(raw_of(rollup=()), row="38f", reason_text=CI_TEXT)
        self.assertEqual(wait, pr_report.Wait(True, empty=True))
        self.assertIn("no checks reported", pr_report.pending_text(wait))
        self.assertIn("checks pending", pr_report.pending_text(pr_report.Wait(True)))

    def test_no_record_and_an_undecidable_answer_never_wait(self) -> None:
        self.assertFalse(pr_report.pending_checks("[]", row="38f", reason_text=CI_TEXT).applies)
        self.assertFalse(pr_report.pending_checks("not json", row="38f", reason_text=CI_TEXT).applies)
        two = json.dumps([json.loads(raw_of(number=1))[0], json.loads(raw_of(number=2))[0]])
        self.assertFalse(pr_report.pending_checks(two, row="38f", reason_text=CI_TEXT).applies)


class VendoredAgreementTest(unittest.TestCase):
    """The Controller's one hand-written reading and the anchor/argv copies agree with the vendored 2.12.0 Workflow
    (the Controller does not import its scripts, so a Workflow change is caught here)."""

    PARSE = """
import json, workflow_forge as forge
out = {}
for name, raw in json.loads(sys.argv[1]).items():
    try:
        fact = forge.parse_forge_raw(raw, "owner/repo", "%s")
        out[name] = {"state": fact["state"], "checks": fact["checks"]["state"], "decision": fact["review_decision"]}
    except forge.ForgeError as exc:
        out[name] = {"error": exc.code}
print(json.dumps(out))
""" % COMMIT

    def fixtures(self) -> dict[str, str]:
        older_closed = json.loads(raw_of(rollup=(RUNNING,), state="CLOSED", number=3))[0]
        green_open = json.loads(raw_of(rollup=(OK,), number=9))[0]
        closed_new = json.loads(raw_of(rollup=(RUNNING,), state="MERGED", number=11))[0]
        context_expected = {"context": "ci/x", "state": "EXPECTED"}
        return {
            "green": raw_of(rollup=(OK,)),
            "running": raw_of(rollup=(RUNNING,)),
            "empty": raw_of(rollup=()),
            "expected_context": raw_of(rollup=(context_expected,)),
            "failed_and_running": raw_of(rollup=(RUNNING, FAILED)),
            "context_pending": raw_of(rollup=({"context": "ci/x", "state": "PENDING"},)),
            "no_conclusion": raw_of(rollup=({"name": "ci", "status": "COMPLETED", "conclusion": ""},)),
            "changes_requested": raw_of(rollup=(RUNNING,), decision="CHANGES_REQUESTED"),
            "closed_pending_next_to_green_open": json.dumps([older_closed, green_open]),
            "newest_closed_pending": json.dumps([older_closed, closed_new]),
            "none": "[]",
            "two_open": json.dumps([green_open, json.loads(raw_of(number=12))[0]]),
            "full_page": json.dumps([green_open] * 200),
        }

    def test_the_wait_predicate_equals_the_workflows_own_selection_and_check_rule(self) -> None:
        life = GateLifecycle(self)
        cases = self.fixtures()
        theirs = json.loads(fixtures.run_workflow_python(life.root, self.PARSE, json.dumps(cases)).stdout)
        for name, raw in cases.items():
            with self.subTest(name):
                expected = theirs[name]
                waits = ("error" not in expected and expected["state"] != "none" and expected["checks"] == "pending"
                         and expected["decision"] != "CHANGES_REQUESTED")
                self.assertEqual(pr_report.pending_checks(raw, row="38f", reason_text=CI_TEXT).applies, waits, expected)

    def test_the_query_argv_equals_the_workflows_fixed_query(self) -> None:
        life = GateLifecycle(self)
        script = ("import json, workflow_forge as forge\n"
                  "print(json.dumps(forge.gh_argv('owner/repo', sys.argv[1])))")
        theirs = json.loads(fixtures.run_workflow_python(life.root, script, COMMIT).stdout)
        seen: list[list[str]] = []

        def runner(argv):
            seen.append(list(argv))
            return mock.Mock(returncode=0, stdout="[]", stderr="")

        self.assertEqual(forge_mod.GhForge("owner/repo", runner).query_pull_requests(COMMIT), "[]")
        self.assertEqual(seen, [theirs])

    def test_the_anchor_commit_equals_the_workflows_anchor_including_the_fallback(self) -> None:
        life = GateLifecycle(self)
        life.to_acceptance()
        wi = target_state.select_work_item(target_state.read(life.inspect()), work_item_id=WID)
        self.assertTrue(wi.technical_approval and wi.technical_approval.get("reviewed_content_commit"))
        script = """
import json
state = json.loads((Path.cwd() / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
item = state["work_items"][sys.argv[1]]
stripped = dict(item); stripped["technical_approval"] = None
print(json.dumps([ws.gate_policy.anchor_of(Path.cwd(), sys.argv[1], variant)["commit"] for variant in (item, stripped)]))
"""
        theirs = json.loads(fixtures.run_workflow_python(life.root, script, WID).stdout)
        stripped = dataclasses.replace(wi, technical_approval=None)
        self.assertEqual([pr_report.anchor_commit(wi), pr_report.anchor_commit(stripped)], theirs)
        self.assertIsNone(pr_report.anchor_commit(dataclasses.replace(
            stripped, reviewed_implementation_head=None)))


class FakeForge:
    """The Controller's query seam: scripted outputs, one per call (the last repeats)."""

    def __init__(self, outputs, repository: str = "owner/repo") -> None:
        self.repository = repository
        self.outputs = list(outputs)
        self.calls: list[str] = []

    def query_pull_requests(self, commit: str) -> str:
        self.calls.append(commit)
        item = self.outputs[min(len(self.calls), len(self.outputs)) - 1]
        if isinstance(item, Exception):
            raise item
        return item(commit) if callable(item) else item


class ReporterCase(unittest.TestCase):
    """``job._run_pr_report`` at the 2.12.0 Workflow's row 38f, with a binding record that has a pull request."""

    def at_38f(self, *, checks: str = "pending") -> GateLifecycle:
        life = GateLifecycle(self)
        life.checks.write_text(checks)
        life.to_acceptance()
        self.assertEqual(life.step()["status"], job.STATUS_FINISHED)  # the refused /satisfy-gate stored the fact
        self.assertEqual(protocol.next_action(life.root, WID).raw["row"], "38f")
        return life

    def bind(self, life: GateLifecycle, *, repository: str = "owner/repo", pull_request=True) -> None:
        key = milestone_branch.repo_key(gitrepo.common_dir(life.root))
        record = {"schema_version": milestone_branch.SCHEMA_VERSION, "state": sorted(milestone_branch.STATES)[0],
                  "work_item_id": WID, "branch": "milestone/demo", "trunk": "main", "binding_generation": 1,
                  "repository": {"forge_repository": repository},
                  "pr": {"number": 7, "url": "https://github.com/owner/repo/pull/7"} if pull_request else None}
        runtime.write_json(life.runtime, milestone_branch.record_rel(key, WID), record)

    def head(self, life: GateLifecycle) -> str:
        return pr_report.anchor_commit(self.item(life))

    def item(self, life: GateLifecycle):
        return target_state.select_work_item(target_state.read(life.inspect()), work_item_id=WID)

    def decision(self, life: GateLifecycle, *, row: str | None = None, text: str | None = None,
                 mode: str | None = None):
        managed = life.inspect()
        wi = self.item(life)
        decision, _ = protocol_decision.decide_after_preflight(managed, wi, base=None)
        self.assertEqual(decision.protocol.disposition, "external_gate")
        if row is not None or text is not None or mode is not None:
            document = json.loads(json.dumps(decision.protocol.document))
            document["row"] = row or document["row"]
            if text is not None:
                document["reason"]["text"] = text
            if mode is not None:
                document["policy"]["mode"] = mode
            decision = dataclasses.replace(decision, protocol=dataclasses.replace(
                decision.protocol, document=document, row=document["row"]))
        return decision

    def report(self, life: GateLifecycle, outputs, *, decision=None, wait_seconds: int = 0, repository: str = "owner/repo",
               sleeps: list | None = None, forge=None):
        managed = life.inspect()
        wi = self.item(life)
        pre = job._capture_pre_state(managed, target_state.read(managed), wi)
        decision = decision or self.decision(life)
        forge = forge or FakeForge(outputs, repository)
        ctx = milestone_branch.Context(repo_root=life.root, runtime_root=life.runtime, forge_factory=lambda repo: forge,
                                       wait_seconds=wait_seconds, poll_seconds=10)
        clock = {"now": 0.0}

        def sleep(seconds):
            if sleeps is not None:
                sleeps.append(seconds)
            clock["now"] += seconds

        with mock.patch.object(milestone_branch, "_sleep", sleep), \
                mock.patch.object(milestone_branch, "_monotonic", lambda: clock["now"]):
            record = job._run_pr_report(
                managed, runtime_root=life.runtime, identity=IDENTITY, run_id=None, work_item=wi, work_item_id=WID,
                decision=decision, pre_state=pre, row=decision.protocol.row, ctx=ctx, on_wait=None)
        self.forge = forge
        return record

    def evidence(self, life: GateLifecycle) -> dict:
        return fixtures.state_entry(life.root, WID).get("gate_evidence") or {}

    def reported(self, life: GateLifecycle):
        return self.evidence(life).get("pr_reported")


class ReportTest(ReporterCase):
    def test_a_green_query_is_recorded_and_the_record_holds_the_workflows_result(self) -> None:
        life = self.at_38f()
        self.bind(life)
        record = self.report(life, [lambda commit: raw_of(commit, rollup=(OK,))])
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(record["kind"], job.PR_REPORT_KIND)
        block = record["pr_report"]
        self.assertEqual(block["outcome"], "reported")
        fact = self.reported(life)
        self.assertEqual(block["result"], {"pr_review": "pr_review", "slot": "pr_reported", "fact_id": fact["fact_id"]})
        self.assertEqual(block["queried_commit"], self.head(life))
        self.assertEqual(self.forge.calls, [self.head(life)])
        self.assertEqual(fact["provenance"]["run_ref"], f"controller:{record['job_id']}")
        self.assertEqual(fact["provenance"]["fetched_by"]["name"], "workflow-controller")
        self.assertEqual(fact["provenance"]["raw_sha256"], block["raw_sha256"])
        self.assertTrue(record["transition_verified"])
        # A green report differs from the stored pending fact, so it arms the query: row 38d.
        self.assertEqual(protocol.next_action(life.root, WID).raw["row"], "38d")
        self.assertEqual([e.event_type for e in job.derive_job_event(record)], ["step_done"])

    def test_the_record_is_valid_and_nothing_is_written_outside_the_job_directory(self) -> None:
        life = self.at_38f()
        self.bind(life)
        before = {p for p in life.runtime.rglob("*") if p.is_file()}
        record = self.report(life, [lambda commit: raw_of(commit)])
        written = {p.relative_to(life.runtime).as_posix() for p in life.runtime.rglob("*") if p.is_file()} - {
            p.relative_to(life.runtime).as_posix() for p in before}
        job_id = record["job_id"]
        self.assertEqual(written, {f"jobs/{job_id}.json", f"jobs/{job_id}/events.jsonl",
                                   f"jobs/{job_id}/pr-review-result.json"})
        sent = json.loads((life.runtime / f"jobs/{job_id}/pr-review-result.json").read_text())
        self.assertEqual(sent["forge"]["raw"], raw_of(self.head(life)))
        validity = job.validate_record(record, managed_repo=life.inspect(), identity=IDENTITY)
        self.assertTrue(validity.valid, validity)

    def test_the_same_output_twice_is_reported_once_and_the_second_step_stops(self) -> None:
        life = self.at_38f()
        self.bind(life)
        output = lambda commit: raw_of(commit)  # noqa: E731
        decision = self.decision(life)  # the gate as the second step sees it: the same output changes nothing
        first = self.report(life, [output], decision=decision)
        self.assertEqual(first["pr_report"]["outcome"], "reported")
        seq = self.reported(life)["ingest_seq"]
        second = self.report(life, [output], decision=decision)
        self.assertEqual(second["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(second["pr_report"]["outcome"], "unchanged")
        self.assertIn(pr_report.GATE_UNCHANGED, second["human_gate_pending"]["what_is_required"])
        self.assertIn("reported; the report changed nothing", second["human_gate_pending"]["what_is_required"])
        self.assertEqual(self.reported(life)["ingest_seq"], seq)
        # a changed output is not "unchanged"; the first report moved the Workflow state, so this gate is stale
        third = self.report(life, [lambda commit: raw_of(commit, rollup=(FAILED,))], decision=decision)
        self.assertEqual(third["pr_report"]["outcome"], "stale")
        self.assertEqual(self.reported(life)["ingest_seq"], seq)

    def test_a_workflow_refusal_ends_in_a_gate_naming_it_and_writes_nothing(self) -> None:
        life = self.at_38f()
        self.bind(life)
        fixtures.run(["git", "remote", "set-url", "origin", "https://example.com/owner/repo.git"], cwd=life.root)
        record = self.report(life, [lambda commit: raw_of(commit)])
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(record["pr_report"]["outcome"], "refused")
        self.assertIn(pr_report.GATE_REFUSED, record["human_gate_pending"]["what_is_required"])
        self.assertIn("ForgeUnavailableError", record["human_gate_pending"]["what_is_required"])
        self.assertIsNone(self.reported(life))

    def test_a_binding_repository_other_than_origins_is_the_workflows_mismatch_refusal(self) -> None:
        life = self.at_38f()
        self.bind(life, repository="other/repo")
        record = self.report(life, [lambda commit: raw_of(commit)], repository="other/repo")
        self.assertEqual(record["pr_report"]["outcome"], "refused")
        self.assertIn("ForgeRepositoryMismatchError", record["human_gate_pending"]["what_is_required"])
        self.assertIsNone(self.reported(life))

    def test_queries_that_cannot_answer_stop_with_their_reasons_and_write_nothing(self) -> None:
        life = self.at_38f()
        self.bind(life)
        down = self.report(life, [ForgeUndecidableError("gh is not logged in")])
        self.assertEqual(down["pr_report"]["outcome"], "forge_unavailable")
        self.assertIn("gh is not logged in", down["human_gate_pending"]["what_is_required"])
        self.assertEqual(self.forge.calls, [self.head(life)])  # never retried inside the step
        none = self.report(life, ["[]"])
        self.assertEqual(none["pr_report"]["outcome"], "no_pull_request")
        self.assertIn(pr_report.GATE_NO_PULL_REQUEST, none["human_gate_pending"]["what_is_required"])
        bad = self.report(life, ["not json"])  # the Workflow's parser refuses; the Controller derives nothing
        self.assertEqual(bad["pr_report"]["outcome"], "refused")
        self.assertIn("ForgeUnparseableError", bad["human_gate_pending"]["what_is_required"])
        self.assertIsNone(self.reported(life))

    def test_a_target_without_a_binding_or_pull_request_is_untouched(self) -> None:
        life = self.at_38f()
        self.assertIsNone(self.report(life, [raw_of()]))
        self.bind(life, pull_request=False)
        self.assertIsNone(self.report(life, [raw_of()]))
        self.assertEqual(self.forge.calls, [])
        self.assertIsNone(self.reported(life))

    def test_the_reporter_acts_only_under_an_automatic_acceptance_gate(self) -> None:
        life = self.at_38f()
        decision = self.decision(life)
        self.assertEqual(job._pr_report_row(decision), "38f")
        self.assertIsNone(job._pr_report_row(self.decision(life, mode="human")))
        self.assertIsNone(job._pr_report_row(self.decision(life, row="38e")))
        review_gate = dataclasses.replace(decision, protocol=dataclasses.replace(
            decision.protocol, action_id="plan.review.external"))
        self.assertIsNone(job._pr_report_row(review_gate))


class WaitTest(ReporterCase):
    def test_pending_checks_wait_and_then_report_the_green_result(self) -> None:
        life = self.at_38f()
        self.bind(life)
        sleeps: list = []
        pending = lambda commit: raw_of(commit, rollup=(RUNNING,))  # noqa: E731
        green = lambda commit: raw_of(commit, rollup=(OK,))  # noqa: E731
        record = self.report(life, [pending, pending, green], wait_seconds=100, sleeps=sleeps)
        self.assertEqual(record["pr_report"]["outcome"], "reported")
        self.assertEqual(sleeps, [10, 10])
        self.assertEqual(len(self.forge.calls), 3)

    def test_the_wait_ends_at_the_one_budget_and_the_stop_names_the_pending_checks(self) -> None:
        life = self.at_38f()
        self.bind(life)
        sleeps: list = []
        record = self.report(life, [lambda commit: raw_of(commit, rollup=(RUNNING,))], wait_seconds=25, sleeps=sleeps)
        self.assertEqual(sleeps, [10, 10, 5])
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        text = record["human_gate_pending"]["what_is_required"]
        self.assertIn(pr_report.GATE_CHECKS_PENDING, text)
        self.assertIn("checks pending", text)
        self.assertIsNone(self.reported(life))

    def test_without_a_wait_budget_the_stop_names_the_pending_checks_at_once(self) -> None:
        life = self.at_38f()
        self.bind(life)
        sleeps: list = []
        record = self.report(life, [lambda commit: raw_of(commit, rollup=(RUNNING,))], sleeps=sleeps)
        self.assertEqual((sleeps, record["pr_report"]["outcome"]), ([], "pending"))
        self.assertEqual(len(self.forge.calls), 1)

    def test_an_empty_rollup_waits_and_the_stop_says_no_checks_reported(self) -> None:
        life = self.at_38f()
        self.bind(life)
        record = self.report(life, [lambda commit: raw_of(commit, rollup=())])
        self.assertEqual(record["pr_report"]["outcome"], "pending")
        self.assertTrue(record["pr_report"]["wait"]["checks_empty"])
        self.assertIn("no checks reported", record["human_gate_pending"]["what_is_required"])
        self.assertIsNone(self.reported(life))

    def test_pending_checks_are_reported_when_waiting_would_hide_something(self) -> None:
        life = self.at_38f()
        self.bind(life)
        # row 38g: the Workflow awaits an approval, so an empty or pending rollup must not hold it back
        at_38g = self.decision(life, row="38g", text="the automatic acceptance gate awaits an approved pull request")
        record = self.report(life, [lambda commit: raw_of(commit, rollup=())], decision=at_38g)
        self.assertEqual(record["pr_report"]["outcome"], "reported", record)

    def test_a_row_38f_stop_that_does_not_name_ci_green_reports_pending_checks(self) -> None:
        life = self.at_38f()
        self.bind(life)
        text = "the automatic acceptance gate awaits a pull-request fact: pr_fact_current: the stored fact is older"
        record = self.report(life, [lambda commit: raw_of(commit, rollup=(RUNNING,))],
                             decision=self.decision(life, text=text))
        self.assertEqual(record["pr_report"]["outcome"], "reported", record)

    def test_a_standing_objection_is_reported_even_while_checks_are_pending(self) -> None:
        life = self.at_38f()
        self.bind(life)
        record = self.report(life, [lambda commit: raw_of(commit, rollup=(RUNNING,), decision="CHANGES_REQUESTED")])
        self.assertEqual(record["pr_report"]["outcome"], "reported", record)
        self.assertEqual(protocol.next_action(life.root, WID).raw["row"], "38d")


class DispatchTest(ReporterCase):
    """``execute_step`` reaches the reporter at the external gate, and only there."""

    def step(self, life: GateLifecycle, outputs):
        forge = FakeForge(outputs)
        proceed = milestone_branch.Proceed()
        with mock.patch.object(milestone_branch, "waiting_preflight", return_value=proceed), \
                mock.patch.object(forge_mod, "GhForge", lambda repo: forge):
            record = life.step()
        self.forge = forge
        return record

    def test_a_step_at_row_38f_reports_instead_of_stopping(self) -> None:
        life = self.at_38f()
        self.bind(life)
        launched = len(life.tasks)
        record = self.step(life, [lambda commit: raw_of(commit)])
        self.assertEqual(record["kind"], job.PR_REPORT_KIND)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(len(life.tasks), launched)  # a Controller step: no worker
        self.assertEqual(life.rows[-1], "38f")

    def test_a_step_without_a_binding_keeps_the_old_stop(self) -> None:
        life = self.at_38f()
        record = life.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("kind", record)

    def test_a_step_under_a_human_gate_reports_nothing(self) -> None:
        life = self.at_38f()
        self.bind(life)
        human = self.decision(life, mode="human")
        forge = FakeForge([lambda commit: raw_of(commit)])
        with mock.patch.object(milestone_branch, "waiting_preflight", return_value=milestone_branch.Proceed()), \
                mock.patch.object(forge_mod, "GhForge", lambda repo: forge), \
                mock.patch.object(protocol_decision, "decide_after_preflight", return_value=(human, ())):
            record = life.step()
        self.assertEqual(record["status"], job.STATUS_GATE_BLOCKED)
        self.assertNotIn("kind", record)
        self.assertEqual(forge.calls, [])


if __name__ == "__main__":
    unittest.main()
