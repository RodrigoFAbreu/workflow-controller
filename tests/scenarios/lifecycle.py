"""Scenarios 1-5: the lifecycle core (`workflow-controller-automated-lifecycle-scenarios` CP3).

Every scenario drives the real ``cli.main`` over a ``"2.2"`` target inside the
kit's sandbox, through the existing end-to-end models: ``Lifecycle`` and the
``_E2ECase`` drivers are imported by name and used as they are (D2); this module
adds the sandbox wiring (the claude and Workflow Manager fakes are the scratch
``bin/`` ones, so the audit hook sees no executable outside it), the scenario's
own assertions, and the observations that become the evidence record's summary.

A scenario honours ``kit.params["mutation"]`` (set only by
``tests/test_scenarios_negative.py``) to break its own claim, so a scenario that
does not fail under its mutation is vacuous (R7).
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import signal
import time
import unittest.mock
from pathlib import Path

from tests.scenarios import ScenarioFailure, scenario


def mutation(kit) -> str:
    return kit.params.get("mutation", "")


# ---------------------------------------------------------------------------
# Wiring the existing models into the sandbox
# ---------------------------------------------------------------------------

@functools.cache
def e2e_class():
    """``_E2ECase`` (a real Workflow tree cloned from a scratch bare origin, the fake ``gh`` and the scripted worker)
    with the fakes resolved from the scratch ``bin/``."""
    from tests import test_trunk_orchestration_e2e as e2e

    class ScenarioE2E(e2e._E2ECase):
        mutation = ""

        @classmethod
        def setUpClass(cls) -> None:
            super().setUpClass()
            cls.stub_manager = Path(os.environ["SCENARIO_SCRATCH"]) / "bin" / "workflow-manager"

        def setUp(self) -> None:
            super().setUp()
            # The scratch bin/ already resolves `gh` to the fake: no second shim directory in front of it.
            self.gh_env["PATH"] = os.environ["PATH"]

        def script_implementation(self) -> None:
            if self.mutation != "skip-trailer":
                return super().script_implementation()
            lc = self.lc
            lc.entry.update(phase=e2e.lifecycle.IMPLEMENTING)
            lc.add(e2e.lifecycle.MILESTONE_IMPLEMENT, lc.implement("CP1", commit="product"))
            lc.add(e2e.lifecycle.MILESTONE_IMPLEMENT, lc.implement("CP2", last=True))
            lc.add(e2e.lifecycle.MILESTONE_IMPLEMENT, lc.generate())
            lc.add(e2e.lifecycle.REVIEW_IMPLEMENTATION, lc.local_review("APPROVE", 1))

        def runTest(self) -> None:  # pragma: no cover -- a TestCase needs one; the scenario drives the methods
            pass

    return ScenarioE2E


@functools.cache
def seeded_class():
    """``_LifecycleTestCase`` (seeded targets, the scripted worker, ``cli`` and the assertions) with the same wiring."""
    from tests import test_lifecycle_orchestration as lo

    class ScenarioSeeded(lo._LifecycleTestCase):
        worker_timeout = "60"

        @classmethod
        def setUpClass(cls) -> None:
            super().setUpClass()
            cls.stub_manager = Path(os.environ["SCENARIO_SCRATCH"]) / "bin" / "workflow-manager"

        def cli(self, lc, command, *args, **kwargs):
            """``cli`` with the worker timeout of this case (the model's fixed ``--timeout 60`` replaced)."""
            from controller import cli as controller_cli
            real_main = controller_cli.main

            def main(argv):
                argv = list(argv)
                argv[argv.index("--timeout") + 1] = self.worker_timeout
                return real_main(argv)
            with unittest.mock.patch.object(controller_cli, "main", main):
                return super().cli(lc, command, *args, **kwargs)

        def runTest(self) -> None:  # pragma: no cover
            pass

    return ScenarioSeeded


def assert_case_in_scratch(kit, case) -> None:
    """D3: every repository the case built, and every remote of it, resolves inside the scratch tree."""
    for holder in (case, getattr(case, "lc", None)):
        root, origin = getattr(holder, "root", None), getattr(holder, "origin", None)
        if isinstance(root, Path):
            kit.assert_inside_scratch(root)
            kit.assert_remote_in_scratch(root)
        if isinstance(origin, Path):
            kit.assert_inside_scratch(origin)


@contextlib.contextmanager
def opened(kit, cls):
    """An instance of the test-case class ``cls`` set up inside the scenario, torn down afterwards."""
    from tests import test_lifecycle_orchestration as lo
    with unittest.mock.patch.object(lo, "FAKE_CLAUDE", kit.bin_dir / "claude"):
        cls.setUpClass()
        case = cls()
        case.mutation = mutation(kit)
        try:
            case.setUp()
            if mutation(kit) == "remote-outside":  # the repository now pushes to the real checkout: the guard must fail
                kit.git("remote", "set-url", "origin", str(kit.checkout), cwd=case.root)
            assert_case_in_scratch(kit, case)
            yield case
            assert_case_in_scratch(kit, case)
        finally:
            case.doCleanups()
            cls.tearDownClass()


# ---------------------------------------------------------------------------
# Scenario 1
# ---------------------------------------------------------------------------

@scenario("scenario-full-lifecycle-to-merge", covers=("C1", "C2", "C9"))
def full_lifecycle_to_merge(kit) -> None:
    """A ``"2.2"`` target from trunk start through the scripted ``/milestone-plan``, bind, plan approval, the Draft PR,
    the checkpoint steps with their sync pushes, ``MILESTONE_COMPLETE``, ready, the human merge, close-out and the next
    trunk start."""
    from controller import cli, gitrepo, milestone_branch as mb, routing
    from tests import test_trunk_orchestration_e2e as e2e
    from tests import test_lifecycle_orchestration as lo
    with opened(kit, e2e_class()) as case:
        with e2e._run_spy() as argvs:
            case.drive_to_pr()
            case.drive_implementation()
            accepted = case.drive_to_merge_gate()
            merge_commit = case.drive_close_out()

        kit.equal(case.worker_tasks(), case.lifecycle_tasks(merge_commit), "the ordered worker commands")
        kit.equal(case.processes(case.lc), 7, "worker invocations: each automatic action ran once")
        case.assert_single_branch_and_pr()
        case.assert_no_force_and_no_merge(argvs)
        record = case.record()
        kit.equal((record["merged_head"], record["accepted_head"]), (accepted, accepted), "the merged and accepted heads")
        kit.check(gitrepo.is_ancestor(case.root, accepted, merge_commit), "the merge commit contains the accepted head")
        events = case.binding_events()
        for event in ("bind_planned", "bound", "push_intent", "pushed", "pr_planned", "pr_created", "ready", "merged",
                      "closed"):
            kit.check(event in events, f"binding event {event!r} never happened")
        kit.equal(events.count("pr_created"), 1, "pull requests created")
        guard = ",".join(routing.BRANCH_GUARD_TOOLS)
        argv = [json.loads(line)["argv"] for line in case.diag_log.read_text().splitlines()]
        kit.equal([guard in a[-1] for a in argv], [False, True, True, True, True, True, False],
                  "the branch guard on the workers under the binding")
        state = json.loads((case.root / e2e.STATE_REL).read_text())
        kit.equal(state["work_items"][e2e.WI]["phase"], e2e.MILESTONE_COMPLETE, "the accepted item's phase")
        kit.equal(state["work_items"][e2e.WI_NEXT]["phase"], e2e.AWAITING_LOCAL_PLAN, "the next item's phase")
        case.lc.add(f"/review-plan {e2e.WI_NEXT}", [])
        case.cli("step")
        following = mb.read_record(case.lc.runtime, case.key, e2e.WI_NEXT)
        kit.equal((following["state"], following["branch_point"]), (mb.BRANCH_BOUND, merge_commit),
                  "the next milestone binds from the new trunk tip")

        kit.observe("worker-tasks", case.worker_tasks())
        kit.observe("worker-invocations", case.processes(case.lc))
        kit.observe("binding-events", events)
        kit.observe("final-binding", {"state": record["state"], "merged_head_is_accepted": record["merged_head"] == accepted})
        kit.observe("pull-requests", [{"number": pr["number"], "state": pr["state"], "draft": pr["isDraft"]}
                                      for pr in case.gh_state()["prs"]])
        kit.observe("branches", {"origin": case.origin_branches(), "local": case.local_branches()})
        kit.observe("phases", {e2e.WI: state["work_items"][e2e.WI]["phase"],
                               e2e.WI_NEXT: state["work_items"][e2e.WI_NEXT]["phase"]})
        kit.observe("next-binding", following["state"])
        kit.record_digest("final-binding-record", {k: record[k] for k in ("state", "work_item_id")})
        kit.observe("controller-push-argvs", sum(1 for a in argvs if a[:3] == ["git", "-C", str(case.root)] and "push" in a))


# ---------------------------------------------------------------------------
# Scenario 2
# ---------------------------------------------------------------------------

def user_act(case, lc, message: str, **entry) -> str:
    """A user-only Workflow command's effect on the target: the state write, committed with its trailer."""
    from tests import fixtures
    from tests import test_lifecycle_orchestration as lo
    lc.entry.update(entry)
    lc.perform([lc.write_state(), fixtures.script_commit(fixtures.trailer_message(message, (lo.WORK_ITEM, lo.WI)),
                                                         lo.STATE_REL)])
    return fixtures.current_head(lc.root)


@scenario("scenario-human-gates-stop-and-resume", covers=("C1", "C9"))
def human_gates_stop_and_resume(kit) -> None:
    """At the plan-approval, technical-approval and acceptance gates ``run`` stops with the gate exit code and launches
    no worker, and after the user-only act ``run`` resumes at the right step."""
    from controller import cli
    from tests import test_lifecycle_orchestration as lo
    with opened(kit, seeded_class()) as case:
        # Gate 1: the plan approval.
        lc = case.seed("plan-gate", "AWAITING_PLAN_APPROVAL" if mutation(kit) != "gate-launches-worker" else lo.IMPLEMENTING)
        gated = case.cli(lc, "run", fail_if_invoked=True)
        gate = case.assert_gate(gated, lc, processes_before=0, contains=("/approve-review plan",),
                                safe=f"/approve-review plan {lo.WI}")
        kit.observe("plan-gate", {"exit": gated.code, "workers": case.processes(lc), "records": len(gated.records),
                                  "safe_resume": gate["safe_resume_command"]})
        user_act(case, lc, f"Approve plan for {lo.WI}", phase=lo.IMPLEMENTING, plan_approval={"status": "CURRENT"})
        lo.script_full_lifecycle(lc)
        resumed = case.cli(lc, "run")
        kit.equal(resumed.code, cli.EXIT_GATE, "run after the plan approval stops at the next gate")
        kit.equal(case.processes(lc), 6, "the six workers of the resumed run")
        kit.observe("after-plan-approval", {"exit": resumed.code, "workers": case.processes(lc),
                                            "commands": [r["selected_action"]["command"] for r in resumed.records],
                                            "gate": resumed.records[-1]["human_gate_pending"]["safe_resume_command"]})

        # Gate 2: the technical approval (both implementation-review stages approved).
        lc = case.at_awaiting_local("technical-gate")
        lc.perform(lc.local_review("APPROVE", 1))
        lc.paste_manual_verdict("APPROVE", 1)
        lc.perform(lc.manual_review("APPROVE", 1))
        gated = case.cli(lc, "run", fail_if_invoked=True)
        gate = case.assert_gate(gated, lc, processes_before=0, contains=("user-only",),
                                safe=f"/approve-review implementation {lo.WI}")
        kit.observe("technical-gate", {"exit": gated.code, "workers": case.processes(lc),
                                       "safe_resume": gate["safe_resume_command"]})
        user_act(case, lc, f"Approve implementation for {lo.WI}", phase="AWAITING_FUNCTIONAL_REVIEW")
        # Gate 3: the acceptance track.
        gated = case.cli(lc, "run", fail_if_invoked=True)
        gate = case.assert_gate(gated, lc, processes_before=0, contains=("/prepare-functional-review",),
                                safe=f"/prepare-functional-review {lo.WI}")
        kit.observe("acceptance-gate", {"exit": gated.code, "workers": case.processes(lc),
                                        "safe_resume": gate["safe_resume_command"]})
        user_act(case, lc, f"Accept {lo.WI}", phase="MILESTONE_COMPLETE")
        done = case.cli(lc, "run", fail_if_invoked=True)
        kit.equal((done.code, case.processes(lc), done.records), (cli.EXIT_OK, 0, []), "run on an accepted milestone")
        kit.observe("after-acceptance", {"exit": done.code, "workers": case.processes(lc), "records": len(done.records)})


# ---------------------------------------------------------------------------
# Scenario 3
# ---------------------------------------------------------------------------

@scenario("scenario-worker-failure-fails-closed", covers=("C1", "C9", "C12a"))
def worker_failure_fails_closed(kit) -> None:
    """A worker that exits non-zero, one that times out and one whose effect does not verify each end the step with
    the documented exit code and record, never advance the phase, and are not retried inside the same ``run``."""
    from controller import cli
    from tests import fixtures
    from tests import test_lifecycle_orchestration as lo
    cases = (("exit-nonzero", {"FAKE_CLAUDE_EXIT": "3"}, "worker_outcome", "FAILURE"),
             ("timeout", {"FAKE_CLAUDE_HANG": "1"}, "predicate_not_satisfied", "INTERRUPTED"),
             ("unverified-effect", {}, "predicate_not_satisfied", "SUCCESS"))
    with opened(kit, seeded_class()) as case:
        case.worker_timeout = "3"
        for name, env, reason, outcome in cases:
            lc = case.seed(name, lo.IMPLEMENTING)
            effect = [fixtures.script_write("stray.txt", "no state change\n")]
            if mutation(kit) == "advancing-worker" and name == "exit-nonzero":
                effect = lc.implement("CP1")
            lc.add(lo.MILESTONE_IMPLEMENT, effect)
            head = fixtures.current_head(lc.root)
            with unittest.mock.patch.dict(os.environ, env):
                result = case.cli(lc, "run")
            [record] = result.records
            kit.equal(result.code, cli.EXIT_WORKER_FAILED, f"{name}: exit status")
            kit.equal(case.processes(lc), 1, f"{name}: workers launched by one run")
            kit.equal(record["status"], "FAILED", f"{name}: job status")
            kit.equal(record["transition_verified"], False, f"{name}: transition verified")
            kit.equal(record["reconciliation_evidence"]["reason"], reason, f"{name}: reconciliation reason")
            kit.equal(record["worker_outcome"], outcome, f"{name}: worker outcome")
            kit.equal(fixtures.state_entry(lc.root, lo.WI)["phase"], lo.IMPLEMENTING, f"{name}: the phase advanced")
            kit.equal(fixtures.current_head(lc.root), head, f"{name}: a commit was made")
            kit.observe(name, {"exit": result.code, "workers": case.processes(lc), "status": record["status"],
                               "worker_outcome": record["worker_outcome"],
                               "reason": record["reconciliation_evidence"]["reason"],
                               "worker_exit": record.get("worker", {}).get("exit_code")})
            # Running again is a new decision by the operator: it launches a worker once more, never on its own.
            with unittest.mock.patch.dict(os.environ, env):
                case.cli(lc, "run")
            kit.observe(name + "-rerun", {"workers": case.processes(lc)})


def _limited_first_attempts(kit, count: int):
    """A ``job.worker.launch`` wrapper whose first ``count`` launches replay the golden usage-limit stream at exit 1
    (and no scripted action); under the ``plain-failure`` mutation they exit 1 without the stream's structured fact."""
    from controller import job
    from tests.test_job import UsageLimitLegacyTest
    real, seen = job.worker.launch, [0]

    def launch(task, **kwargs):
        seen[0] += 1
        if seen[0] > count:
            return real(task, **kwargs)
        env = {"FAKE_CLAUDE_EXIT": "1"}
        if mutation(kit) != "plain-failure":
            env["FAKE_CLAUDE_STDOUT"] = UsageLimitLegacyTest.GOLDEN.read_text()
        saved = {key: os.environ.pop(key, None) for key in ("FAKE_CLAUDE_SCRIPT", "FAKE_CLAUDE_STDOUT")}
        os.environ.update(env)
        try:
            return real(task, **kwargs)
        finally:
            for key in env:
                os.environ.pop(key, None)
            os.environ.update({key: value for key, value in saved.items() if value is not None})

    return launch


@scenario("scenario-usage-limit-mid-job-waits-and-repeats", covers=("C8b",))
def usage_limit_mid_job_waits_and_repeats(kit) -> None:
    """A worker that reaches the usage limit having changed nothing is ``INTERRUPTED``; ``run`` waits for the reset with
    no lock held, records ``usage_paused`` and repeats the step without counting it; a wait over the bound stops with
    17 and the bound line; three limited attempts in a row stop with 17 and the third-attempt line; ``step`` exits 17."""
    from controller import cli, job, lock
    from tests import test_lifecycle_orchestration as lo
    from tests.scenarios.features import write_settings

    reset = 1790287800.0  # the golden stream's resetsAt
    clock = [reset - 3600.0]
    sleeps: list[float] = []
    lock_free: list[bool] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        try:
            lock.acquire_lifecycle_lock(case_root[0]).release()
            lock_free.append(True)
        except Exception:  # noqa: BLE001 -- any refusal means the lock was held
            lock_free.append(False)
        clock[0] += seconds

    case_root: list = [None]
    with opened(kit, seeded_class()) as case, \
            unittest.mock.patch.object(job, "usage_now", lambda: clock[0]), \
            unittest.mock.patch.object(cli, "_usage_sleep", sleep):
        write_settings(kit, {"usage": {"max_wait_seconds": 7200, "resume_grace_seconds": 1}})
        lc = case.seed("repeat", lo.IMPLEMENTING)
        case_root[0] = lc.root
        lo.script_full_lifecycle(lc)
        with unittest.mock.patch.object(job.worker, "launch", _limited_first_attempts(kit, 1)):
            result = case.cli(lc, "run", "--max-steps", "1")
        statuses = [record["status"] for record in result.records]
        kit.equal(statuses, ["INTERRUPTED", "FINISHED"], "the limited attempt and the repeat: job statuses")
        kit.equal(result.code, cli.EXIT_MAX_STEPS, "the repeat is the one counted step")
        kit.equal(result.records[0]["reconciliation_evidence"]["code"], "UsageLimitReached", "the evidence code")
        kit.equal(case.processes(lc), 2, "workers launched")
        kit.check(bool(sleeps) and sum(sleeps) >= 3601.0, "the run did not wait for the reset plus grace")
        kit.check(all(lock_free), "the lifecycle lock was held while waiting")
        paused = [e for e in _usage_events(lc.runtime) if e["event"] == "usage_paused"]
        kit.equal([e["window"] for e in paused], ["five_hour"], "usage_paused window")
        kit.observe("wait-and-repeat", {"exit": result.code, "statuses": statuses, "workers": case.processes(lc)})

        stepped = case.seed("step", lo.IMPLEMENTING)
        lo.script_full_lifecycle(stepped)
        clock[0] = reset - 3600.0
        with unittest.mock.patch.object(job.worker, "launch", _limited_first_attempts(kit, 1)):
            one = case.cli(stepped, "step")
        kit.equal((one.code, [r["status"] for r in one.records]), (cli.EXIT_USAGE_PAUSED, ["INTERRUPTED"]),
                  "step: exit status and job status")
        kit.check("usage limit reached mid-job" in one.stderr and "nothing was started" not in one.stderr,
                  "the step line names the mid-job limit")
        kit.observe("step", {"exit": one.code})

        write_settings(kit, {"usage": {"max_wait_seconds": 60, "resume_grace_seconds": 1}})
        bound = case.seed("bound", lo.IMPLEMENTING)
        lo.script_full_lifecycle(bound)
        clock[0] = reset - 3600.0
        before = len(sleeps)
        with unittest.mock.patch.object(job.worker, "launch", _limited_first_attempts(kit, 1)):
            over = case.cli(bound, "run")
        kit.equal(over.code, cli.EXIT_USAGE_PAUSED, "over the bound: exit status")
        kit.check("would exceed usage.max_wait_seconds (60 s); run again after it" in over.stderr
                  and "nothing was started" not in over.stderr, "the bound line")
        kit.equal(len(sleeps), before, "a wait over the bound must not sleep")
        kit.observe("over-bound", {"exit": over.code})

        write_settings(kit, {"usage": {"enabled": False, "max_wait_seconds": 20000,
                                      "resume_grace_seconds": 1}})  # the wait holds with the budget off, too
        three = case.seed("three", lo.IMPLEMENTING)
        lo.script_full_lifecycle(three)
        clock[0] = reset - 3600.0
        with unittest.mock.patch.object(job.worker, "launch", _limited_first_attempts(kit, 99)):
            stopped = case.cli(three, "run")
        kit.equal((stopped.code, case.processes(three)), (cli.EXIT_USAGE_PAUSED, 3), "three attempts: exit, workers")
        kit.check("the last 3 attempts all reached the limit" in stopped.stderr, "the third-attempt line")
        kit.observe("three-attempts", {"exit": stopped.code, "workers": case.processes(three)})


def _usage_events(runtime) -> list[dict]:
    from controller import usage
    path = runtime / usage.EVENTS_FILE
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---------------------------------------------------------------------------
# Scenario 5
# ---------------------------------------------------------------------------

@scenario("scenario-trunk-drift", covers=("C1", "C3"))
def trunk_drift(kit) -> None:
    """The trunk advances mid-milestone: readiness gates and nothing is integrated, and a human merge from ``PR_OPEN``
    converges to ``CLOSED``."""
    from controller import cli, gitrepo, milestone_branch as mb
    from tests import fixtures
    from tests import test_trunk_orchestration_e2e as e2e
    with opened(kit, e2e_class()) as case:
        with e2e._run_spy() as argvs:
            case.drive_to_pr()
            landed = case.land_on_trunk("drift.txt") if mutation(kit) != "no-drift" else None
            case.drive_implementation()
            observation = case.record()["last_observation"]
            kit.equal((observation["remote_trunk"], observation["fresh"], observation["behind"]), (landed, False, 1),
                      "the branch's observation of the drifted trunk")
            accepted = case.accept()
            case.gh_edit(1, checks=e2e.PASSING_CHECKS)
            gated = case.step(cli.EXIT_GATE)
            case.assert_branch_gate(gated, "the Controller does not integrate")
            kit.equal(case.record()["state"], mb.PR_OPEN, "the binding stays PR_OPEN")
            kit.equal(fixtures.current_head(case.root), accepted, "the branch is still exactly the accepted history")
            kit.check(not gitrepo.is_ancestor(case.root, landed, accepted), "the drifted trunk was integrated")
            kit.equal(case.gh_calls("pr", "ready"), [], "pull requests marked ready")
            kit.check(case.gh_state()["prs"][0]["isDraft"], "the pull request left draft")
            kit.observe("readiness-gate", {"exit": gated.code, "state": case.record()["state"],
                                           "behind": observation["behind"], "fresh": observation["fresh"]})

            case.gh_edit(1, isDraft=False)
            merge_commit = case.human_merge(1)
            case.script_next_plan(merge_commit)
            closed = case.step(cli.EXIT_OK)
        record = case.record()
        kit.equal((record["state"], record["merged_head"]), (mb.CLOSED, accepted), "the binding after the human merge")
        kit.equal((case.head().branch, case.head().commit), ("main", merge_commit), "the target after close-out")
        kit.check(gitrepo.is_ancestor(case.root, landed, merge_commit), "the merge commit lost the drifted trunk commit")
        case.assert_launched(closed, case.bootstrap(merge_commit), e2e.AWAITING_LOCAL_PLAN, bound=False)
        kit.check("ready" not in case.binding_events(), "a ready event was recorded")
        case.assert_single_branch_and_pr()
        case.assert_no_force_and_no_merge(argvs)
        kit.observe("close-out", {"state": record["state"], "branch": case.head().branch,
                                  "events": case.binding_events(), "workers": case.processes(case.lc)})


# ---------------------------------------------------------------------------
# Scenario 4
# ---------------------------------------------------------------------------

KILL_POINT_NAMES = ("binding-before", "binding-after", "job-before", "job-after", "run-before", "run-after")


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return ""


def _leftovers(kit) -> tuple[list[int], list[int]]:
    """``(stdin anchors, other tagged processes)`` of the scenario's scratch tree."""
    from tests.scenarios.kit import _ancestors, tagged_pids
    alive = tagged_pids(str(kit.scratch), exclude=_ancestors())
    anchors = [pid for pid in alive if "stdin anchor" in _cmdline(pid)]
    return anchors, [pid for pid in alive if pid not in anchors]


def _wait_workers_gone(kit, deadline: float = 10.0) -> None:
    end = time.monotonic() + deadline
    while _leftovers(kit)[1] and time.monotonic() < end:
        time.sleep(0.05)


def kill_and_resume(kit, point: str) -> None:
    """One variant of scenario 4: the child Controller of the plan's first bound step is killed at ``point``, then the
    recovery is driven by the commands the Controller offers."""
    from controller import cli, lock
    from tests import fixtures
    from tests import test_trunk_orchestration_e2e as e2e
    from tests import test_lifecycle_orchestration as lo
    with opened(kit, e2e_class()) as case:
        case.script_lifecycle()
        started = case.step(cli.EXIT_OK)
        case.assert_launched(started, case.bootstrap(case.trunk_tip), e2e.AWAITING_LOCAL_PLAN, bound=False)
        fixtures.write_worker_script(case.lc.script_path, case.lc.script)
        ready = kit.scratch / "worker-ready"
        baseline = {p.name for p in (case.lc.runtime / "jobs").glob("*.json")}
        child = kit.child_controller(
            case.argv("step"), kill_at=point, ready_file=ready,
            extra_env={**case.env(), "FAKE_CLAUDE_ORPHAN_SECONDS": "2", "FAKE_CLAUDE_ORPHAN_READY_FILE": str(ready)})
        kit.wait_for_marker(point)
        kit.kill(child)
        _wait_workers_gone(kit)

        def jobs() -> list[str]:
            """The statuses of the jobs created since the trunk start, in a stable order."""
            directory = case.lc.runtime / "jobs"
            return sorted(json.loads(p.read_text())["status"] for p in directory.glob("*.json")
                          if p.name not in baseline) if directory.is_dir() else []
        anchors, others = _leftovers(kit)
        post = point.startswith("job-")
        killed = {"binding": (case.record() or {}).get("state"), "jobs": jobs(), "workers-left": len(others),
                  "anchors": len(anchors), "lock": str(lock.probe_lifecycle_lock(case.root)),
                  "workers-started": case.processes(case.lc)}
        kit.observe("after-kill", killed)
        # What is on disk after the kill. A pre-spawn kill leaves no worker and no job record to reconcile (no tagged
        # process of the killed step was ever started); a post-spawn kill leaves the LAUNCHED record, no worker after
        # its deadline, and the killed Controller's stdin anchor holding the lifecycle lock.
        kit.equal(killed, {
            "binding": {"binding-after": "BRANCH_PLANNED", "job-before": "BRANCH_BOUND", "job-after": "BRANCH_BOUND"}.get(point),
            "jobs": ["LAUNCHED"] if post else [], "workers-left": 0, "anchors": 1 if post else 0,
            "lock": "held" if post else "free", "workers-started": 2 if post else 1}, f"{point}: the state after the kill")

        if mutation(kit) != "resume-true":
            resumed = case.cli("resume")
            reconciled = {"exit": resumed.code, "jobs": jobs(), "lock": str(lock.probe_lifecycle_lock(case.root))}
            kit.observe("after-resume", reconciled)
            expected = {"exit": 0, "jobs": [] if not post else ["FINISHED"], "lock": "free"}
            if point == "job-before":
                # The worker was spawned but no record names it, so `resume` cannot reconcile: the anchor holds the
                # lifecycle lock (exit 45). The scenario ends the anchor itself -- the operator's `kill` the exit-45 text
                # names -- and resumes again.
                expected = {"exit": cli.EXIT_WORKER_ACTIVE, "jobs": ["LAUNCHED"], "lock": "held"}
            kit.equal(reconciled, expected, f"{point}: what resume does")
            if point == "job-before":
                for pid in anchors:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)
                end = time.monotonic() + 10
                while str(lock.probe_lifecycle_lock(case.root)) != "free" and time.monotonic() < end:
                    time.sleep(0.05)
                again = case.cli("resume")
                final = {"exit": again.code, "jobs": jobs(), "lock": str(lock.probe_lifecycle_lock(case.root))}
                kit.observe("after-anchor-ended", final)
                kit.equal(final, {"exit": 0, "jobs": ["FINISHED"], "lock": "free"}, f"{point}: resume once the anchor ended")
        # The `run-true` mutation replaces the continuation `run` by `true` (exit 0, nothing changed), and the result
        # goes through the same end-state comparison, which must then fail (D5.4: no variant passes vacuously).
        continued_code = 0 if mutation(kit) == "run-true" else case.cli("run").code
        tasks = case.worker_tasks()
        end_state = {"exit": continued_code, "binding": (case.record() or {}).get("state"),
                     "jobs": jobs(), "tasks": tasks, "workers-started": case.processes(case.lc)}
        kit.observe("after-run", end_state)
        # Every variant converges to the state the uninterrupted bound step reaches (scenario 1's `drive_to_pr`):
        # each automatic command ran exactly once, the binding is bound, the run stops at the manual plan gate.
        kit.equal(end_state, {"exit": cli.EXIT_GATE, "binding": "BRANCH_BOUND",
                              "jobs": ["FINISHED", "GATE_BLOCKED"], "tasks": [case.bootstrap(case.trunk_tip), e2e.REVIEW_PLAN],
                              "workers-started": 2}, f"{point}: the continuation run's end state")


def _register_kill_variants() -> None:
    for point in KILL_POINT_NAMES:
        scenario(f"scenario-kill-and-resume-{point}", covers=("C1", "C9", "C12a"))(
            functools.partial(kill_and_resume, point=point))


_register_kill_variants()


# ---------------------------------------------------------------------------
# Scenario 13
# ---------------------------------------------------------------------------

@scenario("scenario-review-round-cap-stops-and-continues", covers=("C13",))
def review_round_cap_stops_and_continues(kit) -> None:
    """A protocol plan loop on a real vendored 2.7.0 Workflow (no ``review_round``, so the round is counted from the
    job records): ``run --review-round-cap 1`` runs the first local review and stops before the apply with the
    ``review_round_cap_reached`` gate, launching nothing and leaving the Workflow state as it was; ``explain`` under
    ``review.round_cap`` 1 prints the same gate; ``run --review-round-cap 2`` (the flag beats the setting) applies the
    review, runs the next one and stops again at the cap."""
    from controller import cli, identity, job, worker
    from tests.scenarios.features import write_settings
    from tests.test_protocol_lifecycle import _result
    from tests.test_review_round_lifecycle import STATE_REL, CapLoop

    loop = CapLoop(None, "ready", root=kit.workdir / "cap" / "repo", stub_dir=kit.bin_dir)
    kit.assert_inside_scratch(loop.root)
    effects = {"/review-plan": loop.review_plan, "/apply-plan-review": loop.publish_plan}
    tasks: list[str] = []

    def claude(task, *, cwd, **kwargs):
        tasks.append(task.split()[0])
        if task.split()[0] not in effects:
            raise ScenarioFailure(f"no scripted effect for {task!r}")
        effects[task.split()[0]]()
        return _result()

    def run(command: str, *extra: str):
        kit.install_audit_hook()
        ident = kit._identity()
        argv = ["--runtime-dir", str(loop.runtime), "--workflow-manager", str(loop.stub),
                "--claude-binary", str(kit.bin_dir / "claude"), "--timeout", "60", command, *extra, str(loop.root)]
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(identity, "pin", return_value=ident), \
                unittest.mock.patch.object(identity, "current", return_value=ident), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def last_record() -> dict:
        paths = sorted((loop.runtime / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        return json.loads(paths[-1].read_text())

    def gate_text() -> str:
        record = last_record()
        if record["status"] != job.STATUS_GATE_BLOCKED:
            raise ScenarioFailure(f"the last job record is {record['status']}, not GATE_BLOCKED")
        return record["human_gate_pending"]["what_is_required"]

    with contextlib.ExitStack() as stack:
        stack.enter_context(unittest.mock.patch.object(worker, "launch", claude))
        if mutation(kit) == "cap-ignored":
            stack.enter_context(unittest.mock.patch.object(job, "_review_cap_gate", lambda *a, **k: None))

        code, _out, _err = run("run", "--max-steps", "4", "--review-round-cap", "1")
        state_at_gate = (loop.root / STATE_REL).read_bytes()
        kit.equal((code, tasks), (cli.EXIT_GATE, ["/review-plan"]), "the first run: exit status and workers")
        first = gate_text()
        kit.check(first.startswith("review_round_cap_reached: demo is at plan review round 1 (counted from the "
                                   "Controller's job records"), f"the first stop's gate text: {first!r}")
        kit.check("at the round cap of 1" in first and "(1) raise the cap" in first, "the cap and the options")
        kit.equal(loop.item()["phase"], "REVISING_PLAN", "the phase at the cap")
        kit.observe("first-run", {"exit": code, "workers": list(tasks), "phase": loop.item()["phase"],
                                  "records": len(list((loop.runtime / "jobs").glob("*.json")))})

        write_settings(kit, {"review": {"round_cap": 1}})
        code, out, _err = run("explain")
        kit.equal(code, cli.EXIT_OK, "explain: exit status")
        kit.check(first in out, f"explain does not print the step's gate: {out!r}")
        kit.equal((loop.root / STATE_REL).read_bytes(), state_at_gate, "the Workflow state after the gate and explain")
        kit.observe("explain", {"exit": code, "same-gate": first in out})

        code, _out, _err = run("run", "--max-steps", "4", "--review-round-cap", "2")
        kit.equal((code, tasks), (cli.EXIT_GATE, ["/review-plan", "/apply-plan-review", "/review-plan"]),
                  "the raised run: exit status and workers")
        second = gate_text()
        kit.check("at plan review round 2" in second and "at the round cap of 2" in second,
                  f"the second stop's gate text: {second!r}")
        kit.equal((loop.item()["phase"], loop.item()["plan_revision"]), ("REVISING_PLAN", 2), "the item at the cap")
        kit.observe("raised-run", {"exit": code, "workers": list(tasks), "plan_revision": loop.item()["plan_revision"],
                                   "records": len(list((loop.runtime / "jobs").glob("*.json")))})
