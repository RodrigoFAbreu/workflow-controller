"""The functional review's lifecycle scenarios and isolation tests
(``workflow-controller-automated-functional-review`` CP5, D5-D7).

Each flow below is written once against a small ``Judge`` (``equal``/``check``), so the ordinary suite runs it with
``unittest`` assertions here and the scenario catalogue (``tests/scenarios/lifecycle.py``) runs the same body inside
the kit's sandbox with the kit's own assertions. Everything is driven through the production ``execute_step`` path on
a real vendored Workflow (2.12.0): the fake workers perform the effect of each Workflow command, and the tester is
either ``TesterLifecycle``'s fake (the sandbox is CP3's) or, for the isolation flows, the real
``review.launch_functional`` over ``tests/fake_functional_tester.py``.
"""

from __future__ import annotations

import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import functional_review, job, review, review_rounds, worker  # noqa: E402
from tests import fake_functional_tester as fake  # noqa: E402
from tests import fixtures  # noqa: E402
from tests import test_protocol_lifecycle as lifecycle  # noqa: E402
from tests.test_protocol_job import IDENTITY, TesterLifecycle  # noqa: E402,F401
from tests.test_review_round_lifecycle import GENERATE  # noqa: E402

WID = lifecycle.WID
_REAL_LAUNCH = review.launch_functional
CONSUME = "fingerprint.mark_functional_review_consumed(Path.cwd(), sys.argv[1])"
#: The stale-before-edit write, a fix commit and the post-fix regeneration, then the round's findings marked consumed,
#: as ``/apply-functional-review`` does for a bounded finding; or only the consumption, for a "no code change" one.
FIX, NO_CHANGE, SKIP = "fix", "no-change", "skip"


class Judge:
    """What a flow asserts with; ``unittest`` here, the kit in the scenario catalogue."""

    def equal(self, actual, expected, what: str) -> None:
        raise NotImplementedError

    def check(self, condition: bool, message: str) -> None:
        raise NotImplementedError

    def observe(self, name: str, value) -> None:  # the kit records observations; the suite ignores them
        pass


class SuiteJudge(Judge):
    def __init__(self, test: unittest.TestCase) -> None:
        self.test = test

    def equal(self, actual, expected, what: str) -> None:
        self.test.assertEqual(actual, expected, what)

    def check(self, condition: bool, message: str) -> None:
        self.test.assertTrue(condition, message)


class KitJudge(Judge):
    def __init__(self, kit) -> None:
        self.kit = kit

    def equal(self, actual, expected, what: str) -> None:
        self.kit.equal(actual, expected, what)

    def check(self, condition: bool, message: str) -> None:
        self.kit.check(condition, message)

    def observe(self, name: str, value) -> None:
        self.kit.observe(name, value)


class FunctionalCycle(TesterLifecycle):
    """``TesterLifecycle`` plus the applier: ``/apply-functional-review`` makes a bounded fix (the real post-fix
    regeneration, so both implementation-review stages run again) or disposes of the findings with no code change."""

    def __init__(self, test: unittest.TestCase, flows: int = 1, checklist: str | None = None) -> None:
        super().__init__(test, flows, checklist)
        self.flow_count = flows
        stub_dir = getattr(test, "stub_dir", None)  # the scenario kit allows executables only from its scratch bin/
        if stub_dir is not None:
            self.stub = fixtures.write_stub_workflow_manager(Path(stub_dir) / "workflow-manager-2.12.0", release="2.12.0")
        self.apply_mode = FIX
        self.applied: list[str] = []

    def effect(self, task: str) -> None:
        words = task.lstrip("/").split()
        if words[0] == "prepare-functional-review" and self.revision() > 1:
            # A later revision's checklist must differ from the first, or its commit would be empty.
            self.numbered = "".join(f"{n}. Exercise flow {n} (revision {self.revision()}).\\n"
                                    for n in range(1, self.flow_count + 1))
        if words[0] == "apply-functional-review":
            self.tasks.append(words[0])
            self.applied.append(self.apply_mode)
            if self.apply_mode == SKIP:  # an applier that does nothing (a mutation of the cycle's claim)
                return
            if self.apply_mode == FIX:
                self.workflow(GENERATE, WID, "post-fix", f"functional fix {len(self.applied)}")
            self.workflow(CONSUME, WID)
            return
        super().effect(task)

    def revision(self) -> int:
        return fixtures.state_entry(self.root, WID)["implementation_revision"]

    def reapprove(self) -> None:
        """After a bounded fix: both implementation-review stages and the technical approval, then the new
        revision's checklist (``functional.prepare``) -- the Workflow is back at row 38e."""
        self.manual_gate("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation")
        self.run_until("AWAITING_FUNCTIONAL_REVIEW")
        self.test.assertEqual(self.step()["status"], job.STATUS_FINISHED)
        self.test.assertEqual(self.row(), "38e")

    def accept(self, judge: Judge) -> None:
        """Row 38h: the validator accepts by policy."""
        judge.equal(self.row(), "38h", "the row after the passing record")
        record = self.step()
        judge.equal(record["status"], job.STATUS_FINISHED, "the acceptance step")
        judge.check(self.satisfied[-1:] == ["acceptance"], f"acceptance by policy: {self.satisfied}")
        judge.equal(self.phase(), "MILESTONE_COMPLETE", "the phase after acceptance")


def new_cycle(test: unittest.TestCase, flows: int = 1, checklist: str | None = None) -> FunctionalCycle:
    life = FunctionalCycle(test, flows, checklist)
    life.to_38e()
    return life


def tester_runs(life: FunctionalCycle) -> list[dict]:
    return life.tester_records()


# ---------------------------------------------------------------------------
# Scenario 1 and 2: the cycle closes
# ---------------------------------------------------------------------------

def flow_cycle_closes(judge: Judge, test: unittest.TestCase, *, mutation: str = "") -> None:
    """(1) every flow passes and the validator accepts; (2) a flow fails, the applier fixes it, both review stages and
    the technical approval run again, the retest at the new head passes and the milestone is accepted."""
    # (1)
    life = new_cycle(test)
    life.plan = [("flow-1", "passed", None)]
    record = life.step()
    judge.equal((record["status"], record["functional"]["outcome"]), (job.STATUS_FINISHED, "recorded"), "pass: the run")
    judge.check(not life.findings_path().exists(), "pass: no findings file")
    life.accept(judge)
    judge.observe("pass-path", {"runs": len(tester_runs(life)), "phase": life.phase()})

    # (2) one failing flow, then the fix
    life = new_cycle(test, flows=2)
    if mutation == "applier-noop":
        life.apply_mode = SKIP
    first_head = life.head()
    life.plan = [("flow-1", "passed", None), ("flow-2", "failed", "defect")]
    record = life.step()
    judge.equal(record["functional"]["outcome"], "findings", "fail: the first run")
    judge.equal(life.row(), "38", "fail: the next decision is the applier")
    judge.check(life.findings_path().is_file(), "fail: the findings file exists")
    launched = len(life.tasks)
    judge.equal(life.step()["status"], job.STATUS_FINISHED, "fail: the applier step")
    judge.equal(life.tasks[launched:], ["apply-functional-review"], "fail: the applier is the only worker")
    judge.check(life.head() != first_head, "fail: the fix moved the head")
    life.reapprove()
    life.plan = [("flow-1", "passed", None), ("flow-2", "passed", None)]
    record = life.step()
    judge.equal(record["functional"]["outcome"], "recorded", "retest: the second run")
    judge.equal(record["functional"]["recorded"], ["flow-1", "flow-2"], "retest: both flows recorded")
    stored = life.evidence()
    judge.equal({flow: entry["head"] for flow, entry in stored.items()}, {"flow-1": life.head(), "flow-2": life.head()},
                "retest: the evidence is bound to the new head")
    judge.equal([e["status"] for e in stored.values()], ["passed", "passed"], "retest: both flows pass")
    life.accept(judge)
    judge.equal(review_rounds.functional_rounds(tester_runs(life), str(life.root), WID), 1, "the failed run counts once")
    judge.observe("fix-cycle", {"runs": len(tester_runs(life)), "applied": list(life.applied), "phase": life.phase()})

    # (2b) the retest fails with the identical text: the applier is launched a second time (I3)
    life = new_cycle(test)
    if mutation == "applier-noop":
        life.apply_mode = SKIP
    life.plan = [("flow-1", "failed", "defect")]
    texts = []
    for round_number in (1, 2):
        record = life.step()
        judge.equal(record["functional"]["outcome"], "findings", f"identical: run {round_number}")
        judge.equal(life.row(), "38", f"identical: the next decision after run {round_number}")
        texts.append(life.findings_path().read_text())
        launched = len(life.tasks)
        judge.equal(life.step()["status"], job.STATUS_FINISHED, f"identical: the applier step {round_number}")
        judge.equal(life.tasks[launched:], ["apply-functional-review"], f"identical: applier {round_number} launched")
        life.reapprove()
    judge.check(texts[0] != texts[1], "identical: the findings carry the new head and checklist, so they are not the old bytes")
    judge.equal(life.applied, [FIX, FIX], "identical: the applier ran twice")
    judge.observe("identical-text", {"applied": len(life.applied), "runs": len(tester_runs(life))})


# ---------------------------------------------------------------------------
# Scenario 3: where a person's stop is the answer
# ---------------------------------------------------------------------------

def flow_stops_for_a_person(judge: Judge, test: unittest.TestCase, *, mutation: str = "") -> None:
    """(3) a failed flow the applier disposes of with no code change meets row 38i and no tester runs again;
    ``required_flows`` the checklist does not cover is ``unchanged`` and one ``lane_blocked``; a moving-head cycle
    that fails three times ends at ``functional_rounds_exhausted``."""
    life = new_cycle(test)
    life.plan = [("flow-1", "failed", "defect")]
    life.apply_mode = NO_CHANGE
    judge.equal(life.step()["functional"]["outcome"], "findings", "disposition: the failing run")
    launched = len(life.tasks)
    judge.equal(life.step()["status"], job.STATUS_FINISHED, "disposition: the applier step")
    judge.equal(life.tasks[launched:], ["apply-functional-review"], "disposition: the applier ran")
    judge.equal(life.row(), "38i", "disposition: the Workflow's own stop")
    runs = len(tester_runs(life))
    record = life.step()
    judge.equal(record["status"], job.STATUS_GATE_BLOCKED, "disposition: the next step is a person's stop")
    judge.equal((len(tester_runs(life)), life.tasks[launched:]), (runs, ["apply-functional-review"]),
                "disposition: no further tester run and no further worker")
    judge.observe("disposition", {"row": life.row(), "runs": runs})

    life = new_cycle(test)
    # A policy whose required flow the checklist does not cover: the Workflow keeps answering row 38e after the record.
    life.workflow(lifecycle._ADOPT_ACCEPTANCE_POLICY, json.dumps({"required_flows": ["flow-9"]}))
    judge.equal(life.row(), "38e", "unchanged: the Workflow still wants evidence under the policy")
    life.plan = [("flow-1", "passed", None)]
    judge.equal(life.step()["functional"]["outcome"], "recorded", "unchanged: the first run")
    judge.equal(life.row(), "38e", "unchanged: flow-9 is still missing after the record")
    record = life.step()
    judge.equal((record["status"], record["functional"]["gate"]), (job.STATUS_GATE_BLOCKED, "functional_unchanged"),
                "unchanged: the gate")
    judge.equal(len(life.launches), 1, "unchanged: the tester did not run again")
    judge.equal([e.event_type for e in job.derive_job_event(record)], ["lane_blocked"], "unchanged: one lane_blocked")
    judge.observe("unchanged", {"gate": record["functional"]["gate"]})

    bound = functional_review.ROUND_BOUND
    life = new_cycle(test)
    life.plan = [("flow-1", "failed", "defect")]
    patch = mock.patch.object(functional_review, "ROUND_BOUND", bound + 5) if mutation == "no-bound" else contextlib.nullcontext()
    with patch:
        _bounded_rounds(judge, life, bound)


def _bounded_rounds(judge: Judge, life: FunctionalCycle, bound: int) -> None:
    for number in range(bound):
        record = life.step()
        judge.equal(record["functional"]["outcome"], "findings", f"bound: run {number + 1}")
        judge.equal(life.step()["status"], job.STATUS_FINISHED, f"bound: applier {number + 1}")
        life.reapprove()
    record = life.step()
    judge.equal(record["status"], job.STATUS_GATE_BLOCKED, "bound: the stop after the bound")
    judge.equal(record["functional"]["gate"], "functional_rounds_exhausted", "bound: the gate")
    judge.equal(len(tester_runs(life)), bound, "bound: no further tester run")
    judge.check("functional.automated" in record["human_gate_pending"]["what_is_required"], "bound: the remedy is named")
    judge.observe("bound", {"gate": record["functional"]["gate"], "runs": len(tester_runs(life))})


# ---------------------------------------------------------------------------
# Scenario 5, 6 and 7: the old stop, flows that cannot run, a checklist nobody can read
# ---------------------------------------------------------------------------

def flow_stops_before_or_without_a_run(judge: Judge, test: unittest.TestCase, *, mutation: str = "") -> None:
    """(5) ``functional.automated`` false keeps the old stop with ``user_gate``; (6) flows the sandbox cannot run
    (the network, a forge call, a tool off the ``PATH``) end at ``flows_not_runnable`` with the results recorded, no
    findings and no applier; (7) a checklist with no recognised flow gives ``checklist_unrecognised`` before any
    tester job, sandbox or usage reservation exists."""
    life = new_cycle(test)
    for functional in (dict(life.functional, automated=mutation == "automated-ignored"), None):
        record = life.step(functional=functional)
        judge.equal(record["status"], job.STATUS_GATE_BLOCKED, f"automated off ({functional is None}): the stop")
        judge.check("kind" not in record, "automated off: no tester job record")
        judge.equal([e.event_type for e in job.derive_job_event(record)], ["user_gate"], "automated off: user_gate")
    judge.equal(life.launches, [], "automated off: the tester never ran")
    judge.observe("automated-off", {"launches": 0})

    life = new_cycle(test, flows=3)
    life.plan = [("flow-1", "failed", "environment"), ("flow-2", "failed", "environment"),
                 ("flow-3", "failed", "environment")]
    life.extra = {"fake_calls": {"gh": [{"argv": ["pr", "list"]}]}}
    launched = len(life.tasks)
    record = life.step()
    judge.equal((record["status"], record["functional"]["outcome"], record["functional"]["gate"]),
                (job.STATUS_GATE_BLOCKED, "environment_limited", "flows_not_runnable"), "unrunnable: the stop")
    judge.equal(sorted(life.evidence()), ["flow-1", "flow-2", "flow-3"], "unrunnable: every flow is recorded")
    judge.equal({e["status"] for e in life.evidence().values()}, {"failed"}, "unrunnable: recorded failed")
    judge.check(not life.findings_path().exists(), "unrunnable: no findings file")
    judge.equal(life.tasks[launched:], [], "unrunnable: no applier launched")
    judge.equal([e.event_type for e in job.derive_job_event(record)], ["user_gate"], "unrunnable: user_gate")
    judge.equal(review_rounds.functional_rounds(tester_runs(life), str(life.root), WID), 0,
                "unrunnable: not counted towards the round bound")
    judge.observe("unrunnable", {"gate": record["functional"]["gate"], "recorded": sorted(life.evidence())})

    life = new_cycle(test, checklist="Check that it works somehow.\\n")
    life.plan = [("flow-1", "passed", None)]
    record = life.step(gate=life.usage_gate())
    judge.equal((record["status"], record["functional"]["gate"]), (job.STATUS_GATE_BLOCKED, "checklist_unrecognised"),
                "unrecognised: the stop")
    judge.equal((life.launches, tester_runs(life), life.reservations()), ([], [], {}),
                "unrecognised: no tester job, sandbox or reservation")
    judge.observe("unrecognised", {"gate": record["functional"]["gate"]})


# ---------------------------------------------------------------------------
# Scenario 4: the isolation boundary, on the real launch over the fake CLI
# ---------------------------------------------------------------------------

class RealLaunch:
    """Row 38e answered through the real ``review.launch_functional`` (a real sandbox, snapshots and binding) over
    ``tests/fake_functional_tester.py``, which emulates the CLI's enforcement from the settings file."""

    def __init__(self, life: TesterLifecycle, tools: Path) -> None:
        self.life = life
        self.tools = tools
        self.count = 0
        self.scenario: dict = {}
        self.log: Path | None = None

    def launch(self, **kwargs) -> dict:
        self.count += 1
        scenario = {"log": str(self.tools / f"run-{self.count}.log"), **self.scenario}
        self.log = Path(scenario["log"])
        wrapper = fake.write_binary(self.tools, scenario, name=f"claude-{self.count}")
        return _REAL_LAUNCH(**{**kwargs, "binary_name": str(wrapper), "probe": lambda: None,
                                           "inherited_env": {}})

    def step(self, **kwargs) -> dict:
        life = self.life
        life.rows.append(life.row())

        def worker_launch(task, *, cwd, **extra):
            life.effect(task)
            return lifecycle._result(life.report)

        with mock.patch.object(worker, "launch", worker_launch), mock.patch.object(review, "launch_functional", self.launch):
            return job.execute_step(life.inspect(), identity=IDENTITY, runtime=life.runtime,
                                    functional=life.functional, **kwargs)


def results_action(*statuses: str) -> dict:
    entries = [{"flow_id": f"flow-{n}", "status": status, "summary": f"flow-{n} {status}", "log": f"flow-{n}.log",
                **({"reason": "defect"} if status == "failed" else {})} for n, status in enumerate(statuses, 1)]
    return {"op": "results", "entries": entries, "logs": {f"flow-{n}.log": "log\n" for n in range(1, len(statuses) + 1)}}


def real_cycle(judge: Judge, test: unittest.TestCase, tools: Path, *, flows: int = 1) -> tuple[FunctionalCycle, RealLaunch]:
    life = new_cycle(test, flows=flows)
    operator = life.td / "claude-home"
    (operator / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "t"}}))
    return life, RealLaunch(life, tools)


def real_state(life: FunctionalCycle) -> tuple:
    """What the real repository must keep: HEAD, its refs and its worktree (the state file apart)."""
    status = fixtures.run(["git", "status", "--porcelain", "--", ".", ":(exclude)docs/ai-workflow/WORKFLOW_STATE.json"],
                          cwd=life.root).stdout  # the Workflow's own record is the only thing a run may change
    return life.head(), status, fixtures.run(["git", "for-each-ref"], cwd=life.root).stdout


def flow_isolation_holds(judge: Judge, test: unittest.TestCase, tools: Path, *, mutation: str = "") -> None:
    """(4) an honest run is recorded and leaves the real repository as it was; a tester that edits product source or
    the checklist, passes its check on the edit and restores the original before reporting ``passed`` is refused at
    the first write by the sandbox (the candidate is unchanged throughout); one that edits an unbound copy under its
    ``TMPDIR`` and reports ``passed`` is recorded with the clone unchanged (the stated limit of D5, not a closed
    hole); a clone that differs after a run gives ``tested_bytes_unbound`` and records nothing; a breach gives the
    gate, no record and no change to the real repository."""
    # honest
    life, real = real_cycle(judge, test, tools)
    before = real_state(life)
    real.scenario = {"actions": [results_action("passed")]}
    record = real.step()
    judge.equal((record["status"], record["functional"]["outcome"]), (job.STATUS_FINISHED, "recorded"), "honest: the run")
    judge.equal(real_state(life), before, "honest: the real repository")
    judge.equal(life.evidence()["flow-1"]["status"], "passed", "honest: recorded passed")

    # an edit-and-restore of the checklist and of product source: refused at the first write
    for path in ("docs/ACTIVE_MILESTONE.md", "README.md"):
        life, real = real_cycle(judge, test, tools)
        before = real_state(life)
        real.scenario = {"actions": [{"op": "restore", "path": path, "new": "edited\n"}, results_action("passed")],
                         "emulate_sandbox": mutation != "sandbox-off"}
        record = real.step()
        calls = fake.read_log(real.log)[-1]["actions"]
        restore = [a for a in calls if a["op"] == "restore"]
        judge.check(restore and restore[0]["refused"], f"restore {path}: the sandbox refused the first write: {calls}")
        judge.check("check_saw" not in restore[0], f"restore {path}: the check never saw an edit")
        judge.equal(real_state(life), before, f"restore {path}: the real repository")
        judge.equal(record["functional"]["outcome"], "recorded", f"restore {path}: an honest pass is recorded")

    # an unbound copy under TMPDIR: recorded, the clone unchanged (the stated limit)
    life, real = real_cycle(judge, test, tools)
    before = real_state(life)
    real.scenario = {"actions": [{"op": "copy_edit", "path": "README.md", "new": "edited\n"}, results_action("passed")]}
    record = real.step()
    judge.equal(record["functional"]["outcome"], "recorded", "copy: recorded")
    judge.equal(real_state(life), before, "copy: the real repository")
    judge.check(not record["functional"]["unbound"] if "unbound" in record["functional"] else True, "copy: the clone is bound")

    # a clone that differs after the run (the enforcement switched off, as a failed sandbox would be): nothing recorded
    life, real = real_cycle(judge, test, tools)
    before = real_state(life)
    real.scenario = {"emulate_sandbox": False,
                     "actions": [{"op": "write", "path": "README.md", "data": "tampered\n"}, results_action("passed")]}
    record = real.step()
    judge.equal(record["status"], job.STATUS_GATE_BLOCKED, "unbound: the stop")
    judge.equal(record["functional"]["gate"], "tested_bytes_unbound", "unbound: the gate")
    judge.equal(life.evidence(), {}, "unbound: nothing is recorded")
    judge.equal(real_state(life), before, "unbound: the real repository")
    judge.equal([e.event_type for e in job.derive_job_event(record)], ["user_gate"], "unbound: user_gate")

    # a breach: a session of the job's private Claude home (the nested-session check) fails closed
    life, real = real_cycle(judge, test, tools)
    before = real_state(life)
    real.scenario = {"actions": [{"op": "session"}, results_action("passed")]}
    record = real.step()
    judge.equal(record["status"], job.STATUS_GATE_BLOCKED, "breach: the stop")
    judge.equal(life.evidence(), {}, "breach: nothing is recorded")
    judge.equal(real_state(life), before, "breach: the real repository")
    judge.observe("isolation", {"gate": record["functional"]["gate"]})


# ---------------------------------------------------------------------------
# The ordinary suite
# ---------------------------------------------------------------------------

class CycleClosesTest(unittest.TestCase):
    def test_the_pass_path_and_the_fix_cycle_close_without_a_person(self) -> None:
        flow_cycle_closes(SuiteJudge(self), self)


class PersonStopsTest(unittest.TestCase):
    def test_a_disposition_an_unchanged_answer_and_the_round_bound_stop_for_a_person(self) -> None:
        flow_stops_for_a_person(SuiteJudge(self), self)


class NoRunTest(unittest.TestCase):
    def test_the_old_stop_unrunnable_flows_and_an_unreadable_checklist(self) -> None:
        flow_stops_before_or_without_a_run(SuiteJudge(self), self)


@contextlib.contextmanager
def short_folders_made():
    """The short ``TMPDIR`` folders created while the block runs (only this process's, so a parallel shard's do not count)."""
    made: list[Path] = []
    real = functional_review.make_short_tmp

    def recording(*args, **kwargs):
        path = real(*args, **kwargs)
        made.append(path)
        return path

    with mock.patch.object(functional_review, "make_short_tmp", recording):
        yield made


class IsolationTest(unittest.TestCase):
    def test_the_real_launch_keeps_the_candidate_unchanged_and_fails_closed(self) -> None:
        import tempfile
        tools = Path(tempfile.mkdtemp(prefix="wfr-cp5-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tools, ignore_errors=True))
        with short_folders_made() as made:
            flow_isolation_holds(SuiteJudge(self), self, tools)
            self.doCleanups()  # the cycles' cleanups remove their kept sandboxes' short TMPDIR folders
        self.assertTrue(made, "the run built no sandbox")
        self.assertEqual([p for p in made if p.exists()], [], "the run left a short TMPDIR folder behind")


if __name__ == "__main__":
    unittest.main()
