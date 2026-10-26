"""Deriving and emitting the SignalHub events
(``workflow-controller-signalhub-notifications`` CP3).

The pure derivations are tested directly (``job.derive_job_event``,
``job.derive_branch_gate_event``, ``job.derive_pr_merged``,
``milestone_branch.classify`` and the gate streak); the wiring is tested
against the fake SignalHub in ``tests/fake_signalhub.py``, the fake worker
and disposable repositories already in ``tests/``. No test touches the real
home, the network or the real SignalHub.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli, job, milestone_branch as mb, notify, runtime, usage  # noqa: E402
from controller.notify import Credentials, NotifierConfig  # noqa: E402
from tests import process_fixtures, test_job, test_milestone_branch, test_trunk_preflight, test_usage_command  # noqa: E402
from tests.fake_signalhub import FakeSignalHub  # noqa: E402

KEY = "shpk1_wiring-test-key"
GENERATION = 1


def job_record(status: str, *, command: str = "/apply-plan-review", after: str | None = "AWAITING_LOCAL_PLAN_REVIEW",
               before: str = "REVISING_PLAN", work_item: str | None = "wi-1", job_id: str = "20261012T000000Z-aaaa",
               **extra) -> dict:
    record = {
        "job_id": job_id, "status": status, "work_item_id": work_item, "target_repo": "/tmp/some/target-repo",
        "observed_phase_before": before, "observed_phase_after": after, "run_id": "run-1",
        "selected_action": {"command": command},
        "pre_state": {"state_revision": 4, "plan_revision": 10, "pre_work_item_keys": []},
        "human_gate_pending": None,
    }
    record.update(extra)
    return record


def types(events) -> list[str]:
    return [event.event_type for event in events]


class DeriveJobEventTest(unittest.TestCase):
    def test_a_finished_plan_job_sends_step_done_and_milestone_planned(self) -> None:
        events = job.derive_job_event(job_record(job.STATUS_FINISHED))
        self.assertEqual(types(events), ["step_done", "milestone_planned"])
        self.assertTrue(all(e.ident == "20261012T000000Z-aaaa" for e in events))

    def test_finished_job_event_types_by_action_and_phase(self) -> None:
        approval = job.derive_job_event(job_record(job.STATUS_FINISHED, command="/satisfy-gate", after="IMPLEMENTING"))
        self.assertEqual(types(approval), ["step_done", "approval_recorded"])
        accepted = job.derive_job_event(job_record(job.STATUS_FINISHED, command="/satisfy-gate",
                                                   after="MILESTONE_COMPLETE"))
        self.assertEqual(types(accepted), ["step_done", "approval_recorded", "milestone_accepted"])
        self.assertEqual(accepted[-1].ident, "wi-1")
        plain = job.derive_job_event(job_record(job.STATUS_FINISHED, command="/milestone-implement",
                                                after="IMPLEMENTING"))
        self.assertEqual(types(plain), ["step_done"])

    def test_an_approval_is_sent_only_when_the_phase_moved(self) -> None:
        """gate-policy D5: a refused ``/satisfy-gate`` leaves the phase unchanged, and the reconcile class
        (``gate_reached`` for a real approval whose next answer is a human or external gate) does not decide it."""
        for stage_before, stage_after in (("AWAITING_PLAN_APPROVAL", "IMPLEMENTING"),
                                          ("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "AWAITING_FUNCTIONAL_REVIEW"),
                                          ("AWAITING_FUNCTIONAL_REVIEW", "MILESTONE_COMPLETE")):
            with self.subTest(before=stage_before, after=stage_after):
                moved = job.derive_job_event(job_record(job.STATUS_FINISHED, command="/satisfy-gate",
                                                        before=stage_before, after=stage_after,
                                                        protocol={"reconcile": {"class": "gate_reached"}}))
                self.assertIn("approval_recorded", types(moved))
                refused = job.derive_job_event(job_record(job.STATUS_FINISHED, command="/satisfy-gate",
                                                          before=stage_before, after=stage_before,
                                                          protocol={"reconcile": {"class": "none"}}))
                self.assertEqual(types(refused), ["step_done"])
        accepted = job.derive_job_event(job_record(job.STATUS_FINISHED, command="/satisfy-gate",
                                                   before="AWAITING_FUNCTIONAL_REVIEW", after="MILESTONE_COMPLETE"))
        self.assertEqual(types(accepted), ["step_done", "approval_recorded", "milestone_accepted"])

    def test_milestone_started_needs_the_bootstrap_and_names_the_new_item(self) -> None:
        record = job_record(job.STATUS_FINISHED, command="/milestone-plan", work_item=None,
                            before=job.NO_PHASE_WIRE, after="PLANNING")
        self.assertEqual(types(job.derive_job_event(record)), ["step_done"])
        events = job.derive_job_event(record, new_work_item_id="wi-new")
        self.assertEqual(types(events), ["step_done", "milestone_started"])
        self.assertEqual(events[1].ident, "wi-new")
        later = job_record(job.STATUS_FINISHED, command="/milestone-plan", before="PLANNING", after="PLANNING")
        self.assertNotIn("milestone_started", types(job.derive_job_event(later)))

    def test_failed_and_incomplete_are_blockers_keyed_on_the_job(self) -> None:
        for status in (job.STATUS_FAILED, job.STATUS_INCOMPLETE):
            [event] = job.derive_job_event(job_record(status, reconciliation_evidence={"code": "X"}))
            self.assertEqual((event.event_type, event.ident), ("lane_blocked", "20261012T000000Z-aaaa"))

    def test_a_lifecycle_gate_is_a_user_gate_with_the_resume_command(self) -> None:
        gate = {"phase": "AWAITING_PLAN_APPROVAL", "what_is_required": "approve", "safe_resume_command": "/approve-review plan wi-1"}
        record = job_record(job.STATUS_GATE_BLOCKED, human_gate_pending=gate)
        [event] = job.derive_job_event(record)
        self.assertEqual(event.event_type, "user_gate")
        self.assertIn("/approve-review plan wi-1", event.message)
        # A step that meets the same unchanged gate again mints a new job id but the same key and body.
        again = job.derive_job_event({**record, "job_id": "20261012T999999Z-bbbb"})[0]
        self.assertEqual((again.ident, again.title, again.message), (event.ident, event.title, event.message))
        changed = job.derive_job_event({**record, "pre_state": {"state_revision": 5}})[0]
        self.assertNotEqual(changed.ident, event.ident)

    def test_a_gate_the_workflow_calls_blocked_is_a_blocker_and_the_others_ask_a_person(self) -> None:
        gate = {"phase": "AWAITING_PLAN_APPROVAL", "what_is_required": "approve", "safe_resume_command": "/approve-review plan wi-1"}
        legacy = job_record(job.STATUS_GATE_BLOCKED, human_gate_pending=gate)
        [asked] = job.derive_job_event(legacy)

        def protocol(disposition: object) -> dict:
            return {"protocol": {"decision": {"disposition": disposition, "row": "38f"}}}

        [blocked] = job.derive_job_event({**legacy, **protocol("blocked")})
        self.assertEqual((blocked.event_type, blocked.title, blocked.work_item), ("lane_blocked", "Blocked: wi-1", "wi-1"))
        self.assertEqual((blocked.ident, blocked.message), (asked.ident, asked.message))
        again = job.derive_job_event({**copy.deepcopy(legacy), **protocol("blocked"), "job_id": "20261012T999999Z-bbbb"})[0]
        self.assertEqual((again.ident, again.title, again.message), (blocked.ident, blocked.title, blocked.message))
        for disposition in ("human_gate", "external_gate", "validation", None, 7):
            [event] = job.derive_job_event({**legacy, **protocol(disposition)})
            self.assertEqual((event.event_type, event.title), ("user_gate", asked.title), disposition)
        for block in ("not a mapping", {"decision": "x"}):
            [event] = job.derive_job_event({**legacy, "protocol": block})
            self.assertEqual(event.event_type, "user_gate")

    def test_a_branch_gate_record_and_the_non_events_derive_nothing(self) -> None:
        gate = {"phase": "MILESTONE_BRANCH", "what_is_required": "x", "safe_resume_command": "y"}
        self.assertEqual(job.derive_job_event(job_record(job.STATUS_GATE_BLOCKED, human_gate_pending=gate)), [])
        for status in (job.STATUS_DECLINED, job.STATUS_HANDOFF_PENDING, job.STATUS_LAUNCHED, job.STATUS_PLANNED,
                       job.STATUS_INTERRUPTED):
            self.assertEqual(job.derive_job_event(job_record(status)), [], status)

    def test_two_invocations_build_the_same_bodies(self) -> None:
        record = job_record(job.STATUS_FINISHED)
        first = [notify.build_event(e.event_type, repository="repo", ident=e.ident, title=e.title,
                                    work_item=e.work_item, message=e.message) for e in job.derive_job_event(record)]
        second = [notify.build_event(e.event_type, repository="repo", ident=e.ident, title=e.title,
                                     work_item=e.work_item, message=e.message)
                  for e in job.derive_job_event(copy.deepcopy(record))]
        self.assertEqual([(e.key, e.body) for e in first], [(e.key, e.body) for e in second])


def gate(code: str, *, waitable: bool = False, work_item: str = "wi-1") -> mb.Gate:
    return mb.Gate(code, work_item, "milestone/wi-1", f"{code} message", ("do the exit",), waitable=waitable)


def binding(*, generation: int = GENERATION, head: str | None = "a" * 40, streak: tuple[str, int] | None = None,
            state: str = "READY") -> dict:
    last = {"tip": "t"}
    if streak is not None:
        last["gate"] = {"code": streak[0], "streak": streak[1]}
    return {"work_item_id": "wi-1", "binding_generation": generation, "state": state,
            "pr": {"number": 7, "url": "https://github.com/o/r/pull/7", "head_oid": head}, "last_observation": last}


class RefusalRepositoryTest(unittest.TestCase):
    def test_a_refusal_on_a_relative_repository_names_the_directory(self) -> None:
        # `step .`, the lanes' form: the context and key name the directory, as every other event does.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "manager-repo"
        repo.mkdir()
        hub = FakeSignalHub().start()
        self.addCleanup(hub.stop)
        install_notifier(self, hub, Path(tmp.name))
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(repo)
        job.notify_refusal(".", cli.ControllerError("refused", evidence={"work_item_id": "wi-1"}))
        self.assertTrue(notify.flush(10))
        [request] = hub.requests
        self.assertEqual(request["body"]["context"], "manager-repo/wi-1")
        self.assertTrue(request["idempotency_key"].startswith("wc-lane_blocked-manager-repo-"))
        # The same refusal again is the same key; a different one (message or stable evidence) is a new key.
        job.notify_refusal(".", cli.ControllerError("refused", evidence={"work_item_id": "wi-1"}))
        job.notify_refusal(".", cli.ControllerError("refused", evidence={"work_item_id": "wi-1", "state": "READY"}))
        job.notify_refusal(".", cli.ControllerError("refused otherwise", evidence={"work_item_id": "wi-1"}))
        self.assertTrue(notify.flush(10))
        self.assertEqual(len(hub.requests), 3, "the repeated refusal is an in-process duplicate")
        self.assertEqual(len(set(hub.keys())), 3)


class GateClassificationTest(unittest.TestCase):
    def test_every_gate_code_is_classified(self) -> None:
        for code in sorted(mb.GATE_CODES):
            self.assertIsNotNone(mb.classify(gate(code, waitable=code in mb.WAIT_GATES)), code)
        classes = (mb.BLOCKER_GATES, mb.USER_GATES, mb.WAIT_GATES)
        self.assertEqual(set().union(*classes), set(mb.GATE_CODES))
        self.assertEqual(sum(len(c) for c in classes), len(mb.GATE_CODES), "a gate is in exactly one class")
        self.assertEqual(set(mb.GATE_OCCURRENCE), set(mb.GATE_CODES))

    def test_an_unclassified_code_is_none(self) -> None:
        self.assertIsNone(mb.classify(gate("something_new")))

    def test_a_wait_code_is_a_wait_only_when_waitable(self) -> None:
        self.assertEqual(mb.classify(gate("checks_pending", waitable=True)), mb.GATE_WAIT)
        self.assertEqual(mb.classify(gate("merge_pending", waitable=False)), mb.GATE_BLOCKER)  # GitHub's DIRTY
        self.assertEqual(mb.classify(gate("merge_held")), mb.GATE_USER)
        self.assertEqual(mb.classify(gate("merge_pull_request")), mb.GATE_USER)
        self.assertEqual(mb.classify(gate("checks_failing")), mb.GATE_BLOCKER)

    def test_a_wait_gate_notifies_only_when_a_run_waited_and_the_wait_is_spent(self) -> None:
        g = gate("checks_pending", waitable=True)
        self.assertIsNone(job.derive_branch_gate_event(g, binding(), waited=False, spent=False))
        self.assertIsNone(job.derive_branch_gate_event(g, binding(), waited=True, spent=False))
        event = job.derive_branch_gate_event(g, binding(), waited=True, spent=True)
        self.assertEqual((event.event_type, event.supersedes), ("checks_wait", "lane_blocked"))
        dirty = job.derive_branch_gate_event(gate("merge_pending"), binding(), waited=False, spent=False)
        self.assertEqual(dirty.event_type, "lane_blocked")

    def test_a_spent_checks_wait_is_a_blocker_only_when_the_checks_have_been_pending_long(self) -> None:
        g = gate("checks_pending", waitable=True)
        limit = job.CHECKS_PENDING_LONG_SECONDS
        self.assertEqual(limit, 4 * 3600)
        for seconds, expected in ((None, "checks_wait"), (0, "checks_wait"), (limit, "checks_wait"),
                                  (limit + 1, "lane_blocked")):
            with self.subTest(pending_seconds=seconds):
                event = job.derive_branch_gate_event(g, binding(), waited=True, spent=True, pending_seconds=seconds)
                self.assertEqual(event.event_type, expected)
                self.assertEqual(event.supersedes, "lane_blocked" if expected == "checks_wait" else None)
        short = job.derive_branch_gate_event(g, binding(), waited=True, spent=True, pending_seconds=60)
        self.assertEqual(short.title, "Waiting for the pull request's checks (wi-1)")
        self.assertEqual(short.message, "checks_pending message Next: do the exit")
        self.assertEqual(notify.resolve("checks_wait", None).severity, "LOW")
        self.assertEqual(notify.resolve("checks_wait", None).category, "INFO")

    def test_a_checks_pending_gate_that_cannot_wait_follows_the_same_rule(self) -> None:
        g = gate("checks_pending")  # no auto-merge: classify returns a blocker, whether or not the step waited
        for waited in (False, True):
            short = job.derive_branch_gate_event(g, binding(), waited=waited, spent=waited, pending_seconds=60)
            long = job.derive_branch_gate_event(g, binding(), waited=waited, spent=waited,
                                                pending_seconds=job.CHECKS_PENDING_LONG_SECONDS + 1)
            missing = job.derive_branch_gate_event(g, binding(), waited=waited, spent=waited)
            self.assertEqual((short.event_type, long.event_type, missing.event_type),
                             ("checks_wait", "lane_blocked", "checks_wait"))
            self.assertEqual(short.ident, long.ident)
        dirty = job.derive_branch_gate_event(gate("merge_pending"), binding(), waited=False, spent=False,
                                             pending_seconds=60)
        self.assertEqual(dirty.event_type, "lane_blocked")

    def test_the_age_is_measured_from_the_newest_binding_event(self) -> None:
        events = [{"at": "2026-10-26T00:00:00Z"}, {"at": "2026-10-26T01:00:00Z"}]
        self.assertEqual(job._pending_seconds(events, "2026-10-26T01:30:00Z"), 1800)
        self.assertEqual(job._pending_seconds(events, "2026-10-26T00:30:00Z"), 0)
        for log, now in (([], "2026-10-26T01:30:00Z"), (events, None), (events, "garbage"), ([{}], "2026-10-26T01:30:00Z")):
            self.assertIsNone(job._pending_seconds(log, now))


class BranchGateIdentityTest(unittest.TestCase):
    def ident(self, g: mb.Gate, record: dict, **kw) -> str:
        return job.derive_branch_gate_event(g, record, waited=False, spent=False, **kw).ident

    def test_classes_map_to_event_types(self) -> None:
        self.assertEqual(job.derive_branch_gate_event(gate("merge_held"), binding(), waited=False,
                                                      spent=False).event_type, "user_gate")
        self.assertEqual(job.derive_branch_gate_event(gate("dirty_tree"), binding(), waited=False,
                                                      spent=False).event_type, "lane_blocked")
        event = job.derive_branch_gate_event(gate("merge_held"), binding(), waited=False, spent=False)
        self.assertEqual(event.link, "https://github.com/o/r/pull/7")

    def test_checks_failing_streak_separates_a_rerun_on_the_same_head(self) -> None:
        g = gate("checks_failing")
        one = self.ident(g, binding(streak=("checks_failing", 1)))
        again = self.ident(g, binding(streak=("checks_failing", 1)))
        rerun = self.ident(g, binding(streak=("checks_failing", 3)))
        self.assertEqual(one, again)
        self.assertNotEqual(one, rerun)
        # A new head alone does not change the streak-keyed gates; the streak does.
        self.assertEqual(one, self.ident(g, binding(head="b" * 40, streak=("checks_failing", 1))))

    def test_release_failed_keys_on_the_latest_release_failed_event_at(self) -> None:
        g = gate("release_failed")
        log = lambda at: [{"event": "release_failed", "at": at}, {"event": "noise", "at": "2026-01-01T00:00:09Z"}]  # noqa: E731
        a = self.ident(g, binding(), events=log("2026-01-01T00:00:01Z"))
        a_again = self.ident(g, binding(), events=log("2026-01-01T00:00:01Z"))
        b = self.ident(g, binding(), events=log("2026-01-01T00:00:05Z"))
        self.assertEqual(a, a_again)
        self.assertNotEqual(a, b)

    def test_other_gates_key_on_head_event_at_and_streak(self) -> None:
        g = gate("merge_held")
        events = [{"event": "x", "at": "2026-01-01T00:00:01Z"}]
        base = self.ident(g, binding(streak=("merge_held", 1)), events=events)
        self.assertNotEqual(base, self.ident(g, binding(head="c" * 40, streak=("merge_held", 1)), events=events))
        self.assertNotEqual(base, self.ident(g, binding(streak=("merge_held", 1)),
                                             events=[{"event": "x", "at": "2026-01-01T00:00:02Z"}]))
        # merge_held, checks_failing, merge_held again: the streak advanced, so a new key.
        self.assertNotEqual(base, self.ident(g, binding(streak=("merge_held", 3)), events=events))

    def test_a_gate_with_no_head_and_no_event_keys_on_the_state_revision(self) -> None:
        g = gate("dirty_tree")
        record = {"work_item_id": "wi-1", "binding_generation": 1, "state": "BRANCH_BOUND", "last_observation": None}
        self.assertNotEqual(self.ident(g, record, state_revision=4), self.ident(g, record, state_revision=5))

    def test_the_work_item_and_generation_are_part_of_every_key(self) -> None:
        for code in ("checks_failing", "merge_held"):
            one = self.ident(gate(code, work_item="wi-1"), binding(streak=(code, 1)))
            other = self.ident(gate(code, work_item="wi-2"), binding(streak=(code, 1)))
            self.assertNotEqual(one, other, code)
            self.assertNotEqual(one, self.ident(gate(code), binding(generation=2, streak=(code, 1))), code)

    def test_a_repository_namespaces_the_key(self) -> None:
        ident = self.ident(gate("checks_failing"), binding(streak=("checks_failing", 1)))
        self.assertNotEqual(notify.idempotency_key("lane_blocked", "repo-a", ident),
                            notify.idempotency_key("lane_blocked", "repo-b", ident))


class PrMergedTest(unittest.TestCase):
    def test_keyed_on_the_work_item_and_pull_request(self) -> None:
        event = job.derive_pr_merged(binding(state="MERGED_SQUASHED"))
        self.assertEqual(event.event_type, "pr_merged")
        self.assertIn("#7", event.title)
        self.assertEqual(event.link, "https://github.com/o/r/pull/7")
        again = job.derive_pr_merged({**binding(state="CLOSED"), "binding_generation": 2})
        self.assertEqual(again.ident, event.ident, "the same merge seen on two steps is one notice")

    def test_a_pr_less_close_out_keys_on_the_generation(self) -> None:
        record = {"work_item_id": "wi-1", "binding_generation": 3, "state": "MERGED"}
        event = job.derive_pr_merged(record)
        self.assertNotIn("#", event.title)
        self.assertNotEqual(event.ident, job.derive_pr_merged({**record, "binding_generation": 4}).ident)


class GateStreakTest(unittest.TestCase):
    """``milestone_branch._record_gate`` and the field ``_observe_branch`` carries."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name)
        self.key = "repo-key"
        self.ctx = mb.Context(repo_root=self.runtime, runtime_root=self.runtime, clock=lambda: "2026-01-01T00:00:00Z")

    def put(self, **fields) -> None:
        record = {"schema_version": mb.SCHEMA_VERSION, "work_item_id": "wi-1", "branch": "milestone/wi-1",
                  "trunk": "main", "state": mb.PR_OPEN, "binding_generation": 1, "last_observation": {"tip": "t"}}
        record.update(fields)
        runtime.create_json(self.runtime, mb.record_rel(self.key, "wi-1"), record)

    def stored(self) -> dict:
        return mb.read_record(self.runtime, self.key, "wi-1")

    def record(self, outcome) -> None:
        mb._record_gate(self.ctx, self.key, outcome)

    def test_the_streak_advances_when_the_code_changes_and_holds_when_it_does_not(self) -> None:
        self.put()
        self.record(gate("checks_failing"))
        self.assertEqual(self.stored()["last_observation"]["gate"], {"code": "checks_failing", "streak": 1})
        self.record(gate("checks_failing"))
        self.assertEqual(self.stored()["last_observation"]["gate"]["streak"], 1, "an unchanged gate is one occurrence")
        self.record(gate("checks_pending", waitable=True))
        self.assertEqual(self.stored()["last_observation"]["gate"], {"code": "checks_pending", "streak": 2})
        self.record(gate("checks_failing"))
        self.assertEqual(self.stored()["last_observation"]["gate"], {"code": "checks_failing", "streak": 3})
        self.record(mb.Proceed(binding={"work_item_id": "wi-1"}))
        self.assertEqual(self.stored()["last_observation"]["gate"], {"code": None, "streak": 4})

    def test_the_write_adds_no_binding_event(self) -> None:
        self.put()
        self.record(gate("checks_failing"))
        self.assertFalse((self.runtime / mb.events_rel(self.key, "wi-1")).exists())

    def test_nothing_is_written_without_an_observation_or_for_a_terminal_record(self) -> None:
        self.put(last_observation=None)
        self.record(gate("checks_failing"))
        self.assertIsNone(self.stored()["last_observation"])
        for state in (mb.CLOSED, mb.MERGED, mb.MERGED_SQUASHED):
            with tempfile.TemporaryDirectory() as tmp:
                self.runtime, self.ctx = Path(tmp), mb.Context(repo_root=Path(tmp), runtime_root=Path(tmp))
                self.put(state=state)
                self.record(gate("release_pending"))
                self.assertNotIn("gate", self.stored()["last_observation"], state)

    def test_a_gate_with_no_record_is_ignored(self) -> None:
        self.record(gate("switch_to_trunk", work_item="wi-9"))
        self.record(mb.Proceed())


class ObservationTest(test_milestone_branch._Case):
    """The stored ``gate`` over the real preflight: the trunk-side bind (F1 of round 8) and an adoption
    (F1 of round 9). ``repository_preflight`` is the entry that runs ``_record_gate``."""

    def test_the_trunk_side_bind_leaves_no_observation(self) -> None:
        self.plan()
        outcome = mb.repository_preflight(self.ctx)
        self.assertEqual(outcome.action, "bound")
        record = self.record()
        self.assertIsNone(record["last_observation"])
        self.assertIsNone(mb._binding_view(record)["last_observation"], "status prints `not observed yet`")

    def test_an_adoption_stores_the_observation_and_the_proceed(self) -> None:
        self.plan()
        self.git("switch", "-q", "-c", test_milestone_branch.BRANCH)
        self.approve()
        outcome = mb.repository_preflight(self.ctx)
        self.assertEqual(outcome.action, "adopted")
        observation = self.record()["last_observation"]
        self.assertEqual(observation["gate"], {"code": None, "streak": 1})
        self.assertTrue(observation["tip"])
        view = mb._binding_view(self.record())["last_observation"]
        self.assertEqual(view["tip"], observation["tip"])
        self.assertNotIn("gate", view, "status prints the observation, not the gate field")


class RunsTextTest(unittest.TestCase):
    def test_the_attempt_is_printed_so_a_rerun_is_a_new_detail(self) -> None:
        from controller.forge import Run
        first = mb._runs_text((Run(id=5, workflow="release", status="completed", conclusion="failure", attempt=1,
                                   url="https://x/5"),))
        second = mb._runs_text((Run(id=5, workflow="release", status="completed", conclusion="failure", attempt=2,
                                    url="https://x/5"),))
        self.assertIn("attempt 1", first)
        self.assertNotEqual(first, second)


class UsageOccurrenceTest(unittest.TestCase):
    """``usage.append_event``'s ``occurrence`` (notifications D4) and its single hook (D8)."""

    NOW = 1_800_000_000.0

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def lines(self) -> list[dict]:
        path = self.root / usage.EVENTS_FILE
        out = []
        for line in (path.read_text().splitlines() if path.exists() else []):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def pause(self, offset: float, *, window: str = "five_hour", resume_at: float | None = None,
              resets: float | None = None, repository: str = "/r", provider: str = "claude", run_id=None) -> dict:
        usage.append_event(self.root, "usage_paused", now=self.NOW + offset, provider=provider, window=window,
                           resume_at=resume_at, window_resets_at=resets, repository=repository, run_id=run_id,
                           reason=f"{offset:.0f}% used")
        return self.lines()[-1]

    def test_a_creeping_timed_hold_keeps_its_first_resume_at(self) -> None:
        first = self.pause(0, resume_at=self.NOW + 3600)
        self.assertEqual(first["occurrence"], self.NOW + 3600)
        for step in range(1, 60):  # one second per pass, well past 50 lines of this log
            line = self.pause(step, resume_at=self.NOW + 3600 + step)
            self.assertEqual(line["occurrence"], self.NOW + 3600, step)
        later = self.pause(7200, resume_at=self.NOW + 20000)
        self.assertEqual(later["occurrence"], self.NOW + 20000, "a new window is a new hold")

    def test_the_chain_survives_unrelated_lines_up_to_the_scan_bound(self) -> None:
        first = self.pause(0, resume_at=self.NOW + 3600)
        for i in range(10):
            self.pause(i, resume_at=self.NOW + 99999 + i, repository="/other", window="weekly")
        self.assertEqual(self.pause(30, resume_at=self.NOW + 3601)["occurrence"], first["occurrence"])
        for i in range(usage.OCCURRENCE_TAIL_LINES + 5):
            self.pause(i, resume_at=self.NOW + 88888 + i, repository="/other", window="weekly")
        self.assertEqual(self.pause(60, resume_at=self.NOW + 3602)["occurrence"], self.NOW + 3602,
                         "more than 50 foreign lines between: one extra notice")

    def test_a_timed_hold_with_no_resume_time_is_always_a_new_occurrence(self) -> None:
        a = self.pause(0)
        b = self.pause(10)
        self.assertNotEqual(a["occurrence"], b["occurrence"])

    def test_cap_holds_follow_the_accounting_window(self) -> None:
        a = self.pause(0, window="repository_cap", resets=self.NOW + 5000)
        b = self.pause(60, window="repository_cap", resets=self.NOW + 5001)
        self.assertEqual(b["occurrence"], a["occurrence"])
        c = self.pause(40000, window="repository_cap", resets=self.NOW + 60000)
        self.assertEqual(c["occurrence"], self.NOW + 60000)

    def test_a_reservations_only_cap_resets_after_five_hours_or_a_resume(self) -> None:
        a = self.pause(0, window="repository_cap")
        b = self.pause(600, window="repository_cap")
        self.assertEqual(b["occurrence"], a["occurrence"])
        # More than five hours later: a new occurrence.
        c = self.pause(600 + 5 * 3600 + 1, window="repository_cap")
        self.assertEqual(c["occurrence"], c["at"])
        self.assertNotEqual(c["occurrence"], a["occurrence"])
        # A usage_resumed line between: a new occurrence inside five hours.
        usage.append_event(self.root, "usage_resumed", now=self.NOW + 30000, provider="claude", repository="/r")
        d = self.pause(30100, window="repository_cap")
        self.assertEqual(d["occurrence"], d["at"])
        e = self.pause(30200, window="repository_cap")
        self.assertEqual(e["occurrence"], d["occurrence"])

    def test_a_run_cap_has_no_occurrence_and_is_keyed_on_the_run(self) -> None:
        self.assertIsNone(self.pause(0, window="run_cap", run_id="run-a")["occurrence"])
        self.assertNotEqual(notify.usage_paused_identity("claude", "run_cap", "r", None, "run-a"),
                            notify.usage_paused_identity("claude", "run_cap", "r", None, "run-b"))

    def test_a_pre_1_9_0_line_is_read_by_its_own_fields(self) -> None:
        legacy = {"event": "usage_paused", "at": "2027-01-15T08:00:00+00:00", "provider": "claude",
                  "window": "five_hour", "resume_at": self.NOW + 3600, "repository": "/r", "reason": "x"}
        (self.root / usage.EVENTS_FILE).write_text(json.dumps(legacy) + "\n")
        line = self.pause(0, resume_at=self.NOW + 3601)
        self.assertEqual(line["occurrence"], self.NOW + 3600)

    def test_a_malformed_or_missing_tail_never_raises(self) -> None:
        (self.root / usage.EVENTS_FILE).write_text("not json\n{\"event\": \"usage_paused\"\n\x00\x01\n")
        line = self.pause(0, resume_at=self.NOW + 3600)
        self.assertEqual(line["occurrence"], self.NOW + 3600)
        with unittest.mock.patch.object(usage, "_tail_lines", side_effect=OSError("boom")):
            self.assertTrue(usage.append_event(self.root, "usage_paused", now=self.NOW, provider="claude",
                                               window="five_hour", resume_at=self.NOW + 1, repository="/r"))

    def test_the_occurrence_never_changes_a_usage_decision(self) -> None:
        # The decision reads neither field: evaluate has no parameter for them.
        names = usage.evaluate.__code__.co_varnames[:usage.evaluate.__code__.co_argcount + usage.evaluate.__code__.co_kwonlyargcount]
        self.assertNotIn("occurrence", names)


class UsageNotificationHookTest(unittest.TestCase):
    NOW = 1_800_000_000.0

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "runtime"
        self.root.mkdir()
        self.hub = FakeSignalHub().start()
        self.addCleanup(self.hub.stop)
        self.notifier = notify.install(
            NotifierConfig(attempts=1), runtime_root=self.root,
            credentials=Credentials(url=self.hub.url, url_source="test", key=KEY, key_source="test"))
        self.addCleanup(notify.uninstall)

    def sent(self) -> list[dict]:
        self.assertTrue(notify.flush(10))
        return self.hub.requests

    def test_only_the_four_usage_events_are_sent_and_the_diagnostics_are_not(self) -> None:
        usage.append_event(self.root, "usage_paused", now=self.NOW, provider="claude", window="five_hour",
                           resume_at=self.NOW + 3600, repository="/work/my-repo", reason="claude 91% reaches 85%")
        usage.append_event(self.root, "usage_resumed", now=self.NOW + 3700, provider="claude", repository="/work/my-repo")
        usage.append_event(self.root, "usage_released", now=self.NOW + 3800, provider="claude", token="t1",
                           repository="/work/my-repo")  # default off
        for diagnostic in ("usage_renew_failed", "usage_accounting_orphan", "usage_record_unreadable"):
            usage.append_event(self.root, diagnostic, now=self.NOW, provider="claude", token="t")
        requests = self.sent()
        self.assertEqual([r["body"]["metadata"]["event_type"] for r in requests], ["usage_paused", "usage_resumed"])
        paused = requests[0]
        self.assertEqual(paused["body"]["context"], "my-repo")
        self.assertNotIn("91", json.dumps(paused["body"]), "a body states the window, never the percentages")
        self.assertNotIn("occurredAt", paused["body"])

    def test_repeated_pauses_of_one_hold_send_one_notice(self) -> None:
        for i in range(3):
            usage.append_event(self.root, "usage_paused", now=self.NOW + 60 * i, provider="claude", window="five_hour",
                               resume_at=self.NOW + 3600 + i, repository="/work/my-repo",
                               reason=f"claude {80 + i}% reaches 85%")
        requests = self.sent()
        self.assertEqual(len(requests), 1)
        self.assertEqual(sorted(self.notifier.counts().items()), [("sent", 1)] if False else sorted(self.notifier.counts().items()))
        # The two repeats were dropped as in-process duplicates, never sent.
        self.assertEqual(len({r["idempotency_key"] for r in requests}), 1)

    def test_jittered_resume_times_give_identical_keys_and_bodies(self) -> None:
        for i in range(2):
            usage.append_event(self.root, "usage_paused", now=self.NOW + i, provider="claude", window="weekly",
                               resume_at=self.NOW + 90000 + i, repository="/work/my-repo")
        lines = [json.loads(line) for line in (self.root / usage.EVENTS_FILE).read_text().splitlines()]
        self.assertEqual(lines[0]["occurrence"], lines[1]["occurrence"])
        self.sent()

    def test_two_repositories_paused_by_one_window_send_two_notices(self) -> None:
        for repo in ("/work/a", "/work/b"):
            usage.append_event(self.root, "usage_paused", now=self.NOW, provider="claude", window="five_hour",
                               resume_at=self.NOW + 3600, repository=repo)
        self.assertEqual(len(self.sent()), 2)


def install_notifier(case: unittest.TestCase, hub: FakeSignalHub, runtime_root: Path, *, key: str | None = KEY,
                     **config) -> notify.Notifier:
    cred = Credentials(url=hub.url, url_source="test", key=key, key_source="test") if key else \
        Credentials(url=hub.url, url_source="test", tried=["nothing"])
    notifier = notify.install(NotifierConfig(**{"attempts": 1, **config}), runtime_root=runtime_root, credentials=cred,
                              sleeper=lambda seconds: None)
    case.addCleanup(notify.uninstall)
    return notifier


class _StepCase(unittest.TestCase):
    """A ``REVISING_PLAN`` target a fake worker takes to ``AWAITING_LOCAL_PLAN_REVIEW`` (a FINISHED job), a
    runtime root and a fake SignalHub."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_root = Path(tmp.name)
        self.runtime_root = self.tmp_root / "runtime"
        self.runtime_root.mkdir(parents=True)
        self.managed_repo = test_job.seed_partial_apply_plan_review(self.tmp_root)
        self.hub = FakeSignalHub().start()
        self.addCleanup(self.hub.stop)

    def execute(self, env: dict | None = None, **kwargs):
        env = env if env is not None else test_job.partial_apply_plan_review_worker_env(
            self.managed_repo.root, manifest_revision=11)
        with unittest.mock.patch.dict("os.environ", env):
            return job.execute_step(self.managed_repo, identity=test_job.FAKE_IDENTITY, runtime=self.runtime_root,
                                    claude_bin=str(test_job.FAKE_CLAUDE), timeout=10, **kwargs)

    def job_bytes(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted((self.runtime_root / "jobs").glob("*.json"))}

    def request_types(self) -> list[str]:
        self.assertTrue(notify.flush(10))
        return [r["body"]["metadata"]["event_type"] for r in self.hub.requests]


class FinishedJobWiringTest(_StepCase):
    def test_one_finished_job_sends_step_done_and_milestone_planned_once(self) -> None:
        install_notifier(self, self.hub, self.runtime_root)
        record = self.execute()
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        self.assertEqual(self.request_types(), ["step_done", "milestone_planned"])
        step_done = self.hub.requests[0]
        self.assertEqual(step_done["body"]["context"], f"target/{record['work_item_id']}")
        self.assertEqual(step_done["body"]["severity"], "LOW")
        self.assertIn(record["job_id"], step_done["idempotency_key"])
        self.assertNotIn(record["job_id"], json.dumps(step_done["body"]), "no per-invocation value in a body")

    def test_a_disabled_type_is_never_sent_and_a_severity_override_reaches_the_body(self) -> None:
        install_notifier(self, self.hub, self.runtime_root, events={
            "events": {"step_done": {"enabled": False}, "milestone_planned": {"severity": "CRITICAL"}}})
        self.execute()
        self.assertEqual(self.request_types(), ["milestone_planned"])
        self.assertEqual(self.hub.requests[0]["body"]["severity"], "CRITICAL")

    def test_a_gate_sends_one_user_gate_and_the_same_gate_again_repeats_the_key(self) -> None:
        install_notifier(self, self.hub, self.runtime_root)
        # The first worker leaves a partial apply: FAILED (a blocker); the next step meets the gate.
        self.execute(test_job.partial_apply_plan_review_worker_env(self.managed_repo.root))
        self.assertTrue(notify.flush(10))
        self.hub.requests.clear()
        gate_env = {"FAKE_CLAUDE_REQUIRE_FILE": str(self.tmp_root / "never"), "FAKE_CLAUDE_WRITES": ""}
        gated = self.execute(gate_env)
        self.assertEqual(gated["status"], job.STATUS_GATE_BLOCKED)
        self.assertEqual(self.request_types(), ["user_gate"])
        self.assertEqual(self.hub.requests[0]["body"]["severity"], "HIGH")
        first_key, first_body = self.hub.requests[0]["idempotency_key"], self.hub.requests[0]["body"]
        # A second step meets the same unchanged gate: a new job id, the same key and body, answered 200.
        notify.uninstall()
        install_notifier(self, self.hub, self.runtime_root)
        self.hub.requests.clear()
        again = self.execute(gate_env)
        self.assertNotEqual(again["job_id"], gated["job_id"])
        self.assertEqual(self.request_types(), ["user_gate"])
        self.assertEqual((self.hub.requests[0]["idempotency_key"], self.hub.requests[0]["body"]),
                         (first_key, first_body))
        lines = [json.loads(line) for line in (self.runtime_root / "notifications.jsonl").read_text().splitlines()
                 if "user_gate" in line]
        self.assertEqual({line["status"] for line in lines}, {"sent"}, "never conflict, duplicate or rejected")

    def test_a_failed_job_sends_lane_blocked(self) -> None:
        install_notifier(self, self.hub, self.runtime_root)
        record = self.execute({"FAKE_CLAUDE_WRITES": ""})  # the worker changes nothing: not verified
        self.assertEqual(record["status"], job.STATUS_FAILED)
        self.assertEqual(self.request_types(), ["lane_blocked"])
        self.assertEqual(self.hub.requests[0]["body"]["severity"], "CRITICAL")


class NoSideEffectTest(_StepCase):
    """A failing, hung or unreachable server changes no exit code, job record or step count."""

    VOLATILE = ("job_id", "created_at", "updated_at", "reconciled_at", "worker_process", "worker_anchor",
                "worker_streams", "worker", "telemetry", "usage", "worker_state", "ownership_tag",
                "controller_runtime", "run_id", "event_seq")

    def normalise(self) -> list[str]:
        out = []
        for blob in self.job_bytes().values():
            record = json.loads(blob)
            for volatile in self.VOLATILE:
                record.pop(volatile, None)
            text = json.dumps(record, sort_keys=True).replace(str(self.tmp_root), "<TMP>")
            out.append(re.sub(r"[0-9a-f]{40}", "<SHA>", text))
        return out

    def run_with(self, install) -> tuple[str, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp_root = Path(tmp)
            self.runtime_root = self.tmp_root / "runtime"
            self.runtime_root.mkdir()
            self.managed_repo = test_job.seed_partial_apply_plan_review(self.tmp_root)
            install()
            try:
                record = self.execute()
                jobs = self.normalise()
                notify.flush(10)  # the sender writes its outcome lines into this runtime root
            finally:
                notify.uninstall()
            return record["status"], jobs

    def test_unreachable_500_and_hung_servers_leave_the_lifecycle_unchanged(self) -> None:
        from tests.fake_signalhub import Step
        plain_status, plain_jobs = self.run_with(lambda: None)
        hub500 = FakeSignalHub().start()
        self.addCleanup(hub500.stop)
        hub500.queue(*([500] * 20))
        dead = FakeSignalHub().start()
        dead_url = dead.url
        dead.stop()
        hung = FakeSignalHub().start()
        self.addCleanup(hung.stop)
        hung.queue(*([Step(200, delay=2.0)] * 4))
        cases = {
            "500": lambda: install_notifier(self, hub500, self.runtime_root, attempts=2),
            "unreachable": lambda: notify.install(
                NotifierConfig(attempts=1, timeout_seconds=1), runtime_root=self.runtime_root,
                credentials=Credentials(url=dead_url, url_source="t", key=KEY, key_source="t")),
            "hung": lambda: install_notifier(self, hung, self.runtime_root, timeout_seconds=1, attempts=1),
        }
        for name, install in cases.items():
            with self.subTest(name):
                status, jobs = self.run_with(lambda: install())
                self.assertEqual(status, plain_status)
                self.assertEqual(jobs, plain_jobs, name)


class RunRecordWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "runtime"
        self.root.mkdir()
        self.hub = FakeSignalHub().start()
        self.addCleanup(self.hub.stop)

    def open_run(self) -> job.RunRecord:
        run = job.open_run(self.root, command="run", target_repo="/work/my-repo", max_steps=None,
                           ident=test_job.FAKE_IDENTITY)
        self.addCleanup(run.discard)
        return run

    def run_lines(self, run: job.RunRecord) -> list[dict]:
        path = self.root / "runs" / run.run_id / "notifications.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def events(self, run: job.RunRecord) -> list[dict]:
        path = self.root / "runs" / run.run_id / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_a_configured_run_writes_notifier_active_before_any_job(self) -> None:
        install_notifier(self, self.hub, self.root)
        run = self.open_run()
        self.assertEqual([line["status"] for line in self.run_lines(run)], ["notifier_active"])
        self.assertNotIn(KEY, json.dumps(self.run_lines(run)))

    def test_an_unconfigured_or_disabled_notifier_writes_no_notifier_active(self) -> None:
        install_notifier(self, self.hub, self.root, key=None)
        self.assertEqual(self.run_lines(self.open_run()), [])
        notify.uninstall()
        install_notifier(self, self.hub, self.root, enabled=False)
        self.assertEqual(self.run_lines(self.open_run()), [])

    def test_close_sends_run_finished_then_writes_the_summary_after_the_flush(self) -> None:
        install_notifier(self, self.hub, self.root)
        run = self.open_run()
        run.close(30)
        self.assertEqual([r["body"]["metadata"]["event_type"] for r in self.hub.requests], ["run_finished"])
        self.assertIn("worker failed", self.hub.requests[0]["body"]["title"])
        self.assertEqual(self.events(run)[-1]["event"], "notifications_summary")
        self.assertEqual(self.events(run)[-1]["sent"], 1)
        self.assertNotIn("pending", self.events(run)[-1])

    def test_exit_16_sends_no_run_finished_and_an_interrupt_sends_none(self) -> None:
        install_notifier(self, self.hub, self.root)
        run = self.open_run()
        run.close(16)
        run = self.open_run()
        run.interrupt()
        notify.flush(5)
        self.assertEqual(self.hub.requests, [])

    def test_no_notifier_leaves_the_run_log_exactly_as_before(self) -> None:
        run = self.open_run()
        run.close(0)
        self.assertEqual([e["event"] for e in self.events(run)], ["run_started", "run_ended"])


WAIT_PHASES = ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")


def wait_record(phase: str = WAIT_PHASES[0], *, protocol_gate: object = "default", functional: object = None,
                **extra) -> dict:
    gate = {"phase": phase, "what_is_required": "Wait for the verdict.", "safe_resume_command": "/record-manual-plan-review wi-1"}
    if protocol_gate == "default":
        protocol_gate = {"row": "14", "disposition": "external_gate", "action_id": "plan.review.external"}
    record = job_record(job.STATUS_GATE_BLOCKED, human_gate_pending=gate, **extra)
    if protocol_gate is not None:
        record["protocol_gate"] = protocol_gate
    if functional is not None:
        record["functional"] = functional
    return record


class ReviewWaitDeriveTest(unittest.TestCase):
    """``review_wait`` (notification-fix D1-D3): a stop that only waits for a reviewer or a tester."""

    def test_a_protocol_external_review_wait_at_each_manual_phase_is_a_low_info_event_with_the_gates_identity(self) -> None:
        for phase, action in zip(WAIT_PHASES, ("plan.review.external", "implementation.review.external")):
            with self.subTest(phase=phase):
                record = wait_record(phase, protocol_gate={"row": "14", "disposition": "external_gate", "action_id": action})
                [event] = job.derive_job_event(record)
                self.assertEqual((event.event_type, event.supersedes), ("review_wait", "user_gate"))
                self.assertEqual(event.title, "Waiting for the external review: wi-1")
                resolved = notify.resolve("review_wait", None)
                self.assertEqual((resolved.enabled, resolved.category, resolved.severity), (True, "INFO", "LOW"))
                self.assertIn("/record-manual-plan-review wi-1", event.message)
                again = job.derive_job_event({**copy.deepcopy(record), "job_id": "20261012T999999Z-bbbb"})[0]
                self.assertEqual((again.ident, again.title, again.message), (event.ident, event.title, event.message))
                # The identity is the gate's: the user_gate the same stop sent before this change had it too.
                legacy = job.derive_job_event({k: v for k, v in record.items() if k != "protocol_gate"})[0]
                self.assertEqual((legacy.event_type, legacy.ident), ("user_gate", event.ident))

    def test_every_other_protocol_gate_and_every_legacy_gate_stays_a_user_gate(self) -> None:
        cases = {
            "BLOCK resolution": wait_record(protocol_gate={"row": "11", "disposition": "human_gate",
                                                           "action_id": "review.resolve_block"}),
            "disposition not external": wait_record(protocol_gate={"row": "14", "disposition": "human_gate",
                                                                   "action_id": "plan.review.external"}),
            "another action": wait_record(protocol_gate={"row": "38", "disposition": "external_gate",
                                                         "action_id": "pr.report"}),
            "approval at a single-stage phase": wait_record("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", protocol_gate={
                "row": "33", "disposition": "external_gate", "action_id": "implementation.review.external"}),
            "single-stage plan phase": wait_record("AWAITING_EXTERNAL_PLAN_REVIEW"),
            "plan approval": wait_record("AWAITING_PLAN_APPROVAL"),
            "acceptance": wait_record("AWAITING_USER_ACCEPTANCE"),
            "legacy, no protocol_gate": wait_record(protocol_gate=None),
            "malformed block": wait_record(protocol_gate="x"),
        }
        for name, record in cases.items():
            with self.subTest(name):
                [event] = job.derive_job_event(record)
                self.assertEqual((event.event_type, event.supersedes), ("user_gate", None))

    def test_a_blocked_disposition_and_pending_checks_keep_their_events(self) -> None:
        blocked = wait_record(protocol={"decision": {"disposition": "blocked"}})
        [event] = job.derive_job_event(blocked)
        self.assertEqual(event.event_type, "lane_blocked")
        pending = {"kind": job.PR_REPORT_KIND, "pr_report": {"outcome": job.pr_report.PENDING, "wait": {"waited": True}}}
        [short] = job.derive_job_event(wait_record(**pending))
        self.assertEqual((short.event_type, short.supersedes), ("checks_wait", "lane_blocked"))
        self.assertEqual(short.title, "Waiting for the pull request's checks (wi-1)")
        pending["pr_report"]["wait"] = {"waited": True, "pending_seconds": job.CHECKS_PENDING_LONG_SECONDS + 1}
        [long] = job.derive_job_event(wait_record(**pending))
        self.assertEqual((long.event_type, long.supersedes, long.ident), ("lane_blocked", None, short.ident))
        pending["pr_report"]["wait"] = {"waited": True, "pending_seconds": "unreadable"}
        self.assertEqual(types(job.derive_job_event(wait_record(**pending))), ["checks_wait"])
        pending["pr_report"]["wait"] = {"waited": False}
        self.assertEqual(job.derive_job_event(wait_record(**pending)), [])

    def test_each_tester_fault_stop_but_isolation_breach_waits_and_the_others_keep_their_events(self) -> None:
        phase = "AWAITING_FUNCTIONAL_REVIEW"
        for code in job.functional_review.FAULT_GATES:
            with self.subTest(code=code):
                record = wait_record(phase, protocol_gate=None, functional={"gate": code})
                [event] = job.derive_job_event(record)
                if code == job.functional_review.GATE_ISOLATION_BREACH:
                    self.assertEqual((event.event_type, event.supersedes), ("user_gate", None))
                    continue
                self.assertEqual((event.event_type, event.supersedes), ("review_wait", "user_gate"))
                self.assertEqual(event.title, f"Waiting: {code} (wi-1)")
        [exhausted] = job.derive_job_event(wait_record(phase, protocol_gate=None, functional={
            "gate": job.functional_review.GATE_ROUNDS_EXHAUSTED}))
        self.assertEqual(exhausted.event_type, "review_wait")
        [unchanged] = job.derive_job_event(wait_record(phase, protocol_gate=None, functional={
            "gate": job.functional_review.GATE_UNCHANGED}))
        self.assertEqual(unchanged.event_type, "lane_blocked")
        [off] = job.derive_job_event(wait_record(phase, protocol_gate=None))
        self.assertEqual(off.event_type, "user_gate")

    def test_titles_are_the_exact_text_and_never_hold_the_identity(self) -> None:
        for record, title in ((wait_record(), "Waiting for the external review: wi-1"),
                              (wait_record("AWAITING_FUNCTIONAL_REVIEW", protocol_gate=None,
                                           functional={"gate": "flows_not_runnable"}),
                               "Waiting: flows_not_runnable (wi-1)")):
            [event] = job.derive_job_event(record)
            self.assertEqual(event.title, title)
            self.assertNotIn(event.ident, event.title)
            self.assertIsNone(re.search(r"[0-9a-f]{16}", event.title))

    def test_a_normal_merge_is_info_and_the_owners_branch_gates_stay_user_gates(self) -> None:
        resolved = notify.resolve("pr_merged", None)
        self.assertEqual((resolved.category, resolved.severity), ("INFO", "NORMAL"))
        for code in ("merge_pull_request", "merge_held"):
            with self.subTest(code=code):
                self.assertIn(code, mb.USER_GATES)
                event = job.derive_branch_gate_event(gate(code), binding(), waited=False, spent=False)
                self.assertEqual(event.event_type, "user_gate")


class SupersededNoticeTest(unittest.TestCase):
    """The old ``user_gate`` of a stop that now sends ``review_wait`` is never followed by a second notice (D3)."""

    REPO = "target-repo"
    #: The stop under test: here the protocol wait for the external review (rule 2).
    RECORD = staticmethod(wait_record)

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "runtime"
        (self.root / "runs").mkdir(parents=True)
        self.event = job.derive_job_event(self.RECORD())[0]
        self.assertEqual((self.event.event_type, self.event.supersedes), ("review_wait", "user_gate"))
        self.old_key = notify.idempotency_key("user_gate", self.REPO, self.event.ident)

    def log(self, run_id: str, status: str, *, key: str | None = None, source: str | None = None) -> None:
        path = self.root / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        line = {"idempotency_key": key or self.old_key, "status": status, **({"source": source} if source else {})}
        with open(path / "notifications.jsonl", "a") as handle:
            handle.write(json.dumps(line) + "\n")

    def handled(self, *, spent: bool, root: object = "default") -> bool:
        return job._superseded_handled(self.event, self.REPO, self.root if root == "default" else root, spent=spent)

    def test_delivered_outcomes_veto_on_both_questions_and_spent_attempts_only_on_recovery(self) -> None:
        for status, live, recovery in (("sent", True, True), ("conflict", True, True), ("failed", False, True),
                                       ("rejected", False, True), ("skipped", False, False)):
            with self.subTest(status=status):
                self.setUp()
                self.log("20261012T000000Z-run1", status)
                self.assertEqual((self.handled(spent=False), self.handled(spent=True)), (live, recovery))
        self.setUp()
        self.log("20261012T000000Z-run1", "duplicate", source="server")
        self.assertTrue(self.handled(spent=False))
        self.setUp()
        self.log("20261012T000000Z-run1", "duplicate")
        self.assertFalse(self.handled(spent=True))

    def test_the_log_of_any_recent_run_counts_and_another_key_does_not(self) -> None:
        self.log("20261012T000000Z-old", "sent")
        self.log("20261012T000001Z-new", "sent", key="some-other-key")
        self.assertTrue(self.handled(spent=False))

    def test_only_the_newest_runs_by_name_are_read(self) -> None:
        self.log("20260101T000000Z-oldest", "sent")
        for n in range(job.RECOVERY_RECORD_LIMIT):
            (self.root / "runs" / f"20261012T{n:06d}Z-run").mkdir()
        self.assertFalse(self.handled(spent=False))

    def test_a_missing_log_an_unresolvable_root_and_a_stop_with_nothing_superseded_send(self) -> None:
        self.assertFalse(self.handled(spent=False))
        self.assertFalse(self.handled(spent=False, root=None))
        self.assertFalse(self.handled(spent=False, root=self.tmp / "nowhere"))
        self.log("20261012T000000Z-run1", "sent")
        self.assertFalse(job._superseded_handled(dataclasses.replace(self.event, supersedes=None), self.REPO,
                                                 self.root, spent=False))

    def test_a_symlinked_run_directory_or_log_is_not_followed(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "notifications.jsonl").write_text(json.dumps({"idempotency_key": self.old_key, "status": "sent"}) + "\n")
        (self.root / "runs" / "20261012T000000Z-linkdir").symlink_to(outside, target_is_directory=True)
        self.assertFalse(self.handled(spent=False))
        real = self.root / "runs" / "20261012T000001Z-linklog"
        real.mkdir()
        (real / "notifications.jsonl").symlink_to(outside / "notifications.jsonl")
        self.assertFalse(self.handled(spent=False))

    def deliver(self, hub: FakeSignalHub, event: job.DerivedEvent, **kwargs) -> list[str]:
        hub.requests.clear()
        job._send_derived([event], self.REPO, run_id="20261012T999999Z-now", **kwargs)
        self.assertTrue(notify.flush(10))
        return [r["body"]["metadata"]["event_type"] for r in hub.requests]

    def hub(self) -> FakeSignalHub:
        hub = FakeSignalHub().start()
        self.addCleanup(hub.stop)
        install_notifier(self, hub, self.root)
        return hub

    def test_a_wait_is_sent_as_review_wait_low_and_only_once_across_steps(self) -> None:
        hub = self.hub()
        self.assertEqual(self.deliver(hub, self.event), ["review_wait"])
        self.assertEqual(hub.requests[0]["body"]["severity"], "LOW")
        self.assertEqual(self.deliver(hub, self.event), [])  # the log of the run that sent it holds the outcome

    def test_an_old_user_gate_already_delivered_is_never_followed_by_a_review_wait_on_the_live_path(self) -> None:
        self.log("20261012T000000Z-earlier", "sent")
        self.assertEqual(self.deliver(self.hub(), self.event), [])

    def test_a_delivered_notice_under_an_unrelated_key_never_suppresses_the_review_wait(self) -> None:
        # Only the stop's own superseded key vetoes: an unrelated delivered notice, in any recent run's log, does not.
        for status in ("sent", "conflict"):
            with self.subTest(status=status):
                self.setUp()
                self.log("20261012T000000Z-earlier", status, key="some-other-key")
                self.log("20261012T000001Z-later", "sent", key=notify.idempotency_key("user_gate", self.REPO, "other"))
                self.assertFalse(self.handled(spent=False))
                hub = self.hub()
                self.assertEqual(self.deliver(hub, self.event), ["review_wait"])
                self.assertEqual(self.deliver(hub, self.event), [])
                notify.uninstall()

    def test_a_failed_or_rejected_old_user_gate_does_not_veto_a_fresh_send_but_recovery_replays_nothing(self) -> None:
        for status in ("failed", "rejected"):
            with self.subTest(status=status):
                self.setUp()
                self.log("20261012T000000Z-earlier", "notifier_active", key="x")
                self.log("20261012T000000Z-earlier", status)
                self.assertEqual(self.deliver(self.hub(), self.event), ["review_wait"])
                notify.uninstall()
                record = {"run_id": "20261012T000000Z-earlier"}
                self.assertFalse(job._recoverable(record, self.root, self.event, self.REPO, None))

    def test_recovery_sends_a_never_handled_stop_once_as_review_wait(self) -> None:
        run = "20261012T000000Z-earlier"
        self.log(run, "notifier_active", key="x")
        record = {"run_id": run}
        self.assertTrue(job._recoverable(record, self.root, self.event, self.REPO, None))
        self.log(run, "sent", key=notify.idempotency_key("review_wait", self.REPO, self.event.ident))
        self.assertFalse(job._recoverable(record, self.root, self.event, self.REPO, None))

    def test_recovery_does_not_resend_a_stop_whose_old_user_gate_was_handled(self) -> None:
        run = "20261012T000000Z-earlier"
        self.log(run, "notifier_active", key="x")
        self.log(run, "sent")
        self.assertFalse(job._recoverable({"run_id": run}, self.root, self.event, self.REPO, None))


class ChecksWaitUpgradeTest(unittest.TestCase):
    """The stop that used to send ``lane_blocked`` and now sends ``checks_wait`` (#170): the same
    supersede rule as ``review_wait``, and a ``checks_wait`` already sent never blocks the later long ``lane_blocked``."""

    REPO = "target-repo"

    @staticmethod
    def pending_record(**wait) -> dict:
        return job_record(job.STATUS_GATE_BLOCKED, human_gate_pending={
            "phase": "AWAITING_FUNCTIONAL_REVIEW", "what_is_required": "Pending checks.", "safe_resume_command": "step"},
            kind=job.PR_REPORT_KIND, pr_report={"outcome": job.pr_report.PENDING, "wait": {"waited": True, **wait}})

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "runtime"
        (self.root / "runs").mkdir(parents=True)
        self.event = job.derive_job_event(self.pending_record())[0]
        self.assertEqual((self.event.event_type, self.event.supersedes), ("checks_wait", "lane_blocked"))
        self.old_key = notify.idempotency_key("lane_blocked", self.REPO, self.event.ident)

    def log(self, run_id: str, status: str) -> None:
        path = self.root / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "notifications.jsonl", "a") as handle:
            handle.write(json.dumps({"idempotency_key": self.old_key, "status": status}) + "\n")

    def deliver(self, hub: FakeSignalHub, event: job.DerivedEvent) -> list[str]:
        hub.requests.clear()
        job._send_derived([event], self.REPO, run_id="20261012T999999Z-now")
        self.assertTrue(notify.flush(10))
        return [r["body"]["metadata"]["event_type"] for r in hub.requests]

    def hub(self) -> FakeSignalHub:
        hub = FakeSignalHub().start()
        self.addCleanup(hub.stop)
        install_notifier(self, hub, self.root)
        return hub

    def test_a_checks_wait_is_sent_low_and_only_once_across_steps(self) -> None:
        hub = self.hub()
        self.assertEqual(self.deliver(hub, self.event), ["checks_wait"])
        self.assertEqual(hub.requests[0]["body"]["severity"], "LOW")
        self.assertEqual(self.deliver(hub, self.event), [])

    def test_a_lane_blocked_delivered_in_an_earlier_run_is_not_announced_again_as_checks_wait(self) -> None:
        self.log("20261012T000000Z-earlier", "sent")
        self.assertEqual(self.deliver(self.hub(), self.event), [])

    def test_a_stop_whose_lane_blocked_was_never_handled_sends_one_checks_wait(self) -> None:
        self.log("20261012T000000Z-earlier", "failed")
        self.assertEqual(self.deliver(self.hub(), self.event), ["checks_wait"])

    def test_a_checks_wait_already_sent_does_not_stop_the_later_long_lane_blocked(self) -> None:
        hub = self.hub()
        self.assertEqual(self.deliver(hub, self.event), ["checks_wait"])
        long = job.derive_job_event(self.pending_record(pending_seconds=job.CHECKS_PENDING_LONG_SECONDS + 1))[0]
        self.assertEqual((long.event_type, long.supersedes, long.ident), ("lane_blocked", None, self.event.ident))
        self.assertEqual(self.deliver(hub, long), ["lane_blocked"])


class TesterStopSupersededNoticeTest(SupersededNoticeTest):
    """The same checks for a tester fault stop (rule 3, ``flows_not_runnable``)."""

    RECORD = staticmethod(lambda: wait_record("AWAITING_FUNCTIONAL_REVIEW", protocol_gate=None,
                                              functional={"gate": "flows_not_runnable"}))


class RecoveryTest(_StepCase):
    """``resume`` re-derives what a dead Controller never enqueued (D13)."""

    def setUp(self) -> None:
        super().setUp()
        self.run = job.open_run(self.runtime_root, command="run", target_repo=str(self.managed_repo.root),
                                max_steps=None, ident=test_job.FAKE_IDENTITY)
        self.addCleanup(self.run.discard)

    def finish_job_unnotified(self) -> dict:
        """A FINISHED job whose notice was never enqueued (no notifier installed while it ran)."""
        record = self.execute(run_id=self.run.run_id)
        self.assertEqual(record["status"], job.STATUS_FINISHED)
        return record

    def active_log(self, *lines: dict) -> None:
        path = self.runtime_root / "runs" / self.run.run_id
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "notifications.jsonl", "a") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")

    def resume(self) -> None:
        job.resume(self.managed_repo, identity=test_job.FAKE_IDENTITY, runtime=self.runtime_root)

    def test_a_terminal_record_with_a_notifier_active_line_and_no_outcome_is_recovered_once(self) -> None:
        record = self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        install_notifier(self, self.hub, self.runtime_root)
        self.resume()
        self.assertEqual(self.request_types(), ["step_done", "milestone_planned"])
        # The outcome lines went to the record's own run log: a second resume finds them and sends nothing.
        self.hub.requests.clear()
        notify.uninstall()
        install_notifier(self, self.hub, self.runtime_root)
        self.resume()
        self.assertEqual(self.request_types(), [])
        self.assertTrue(record["job_id"])

    def test_nothing_is_recovered_without_a_notifier_active_line(self) -> None:
        self.finish_job_unnotified()
        install_notifier(self, self.hub, self.runtime_root)
        self.resume()
        self.assertEqual(self.request_types(), [])

    def test_a_record_older_than_the_window_is_not_recovered(self) -> None:
        record = self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        path = self.runtime_root / "jobs" / f"{record['job_id']}.json"
        old = json.loads(path.read_text())
        old["updated_at"] = "2020-01-01T00:00:00Z"
        path.write_text(json.dumps(old))
        install_notifier(self, self.hub, self.runtime_root)
        self.resume()
        self.assertEqual(self.request_types(), [])

    def test_handled_outcomes_are_not_recovered_but_unsent_ones_are(self) -> None:
        record = self.finish_job_unnotified()
        key = notify.idempotency_key("step_done", "target", record["job_id"])
        cases = {
            "sent": ({"status": "sent"}, False),
            "failed": ({"status": "failed"}, False),
            "conflict": ({"status": "conflict"}, False),
            "rejected": ({"status": "rejected"}, False),
            "server duplicate": ({"status": "duplicate", "source": "server"}, False),
            "skipped": ({"status": "skipped"}, True),
            "dropped": ({"status": "dropped"}, True),
            "process duplicate": ({"status": "duplicate", "source": "process"}, True),
        }
        for name, (line, recovered) in cases.items():
            with self.subTest(name):
                log = self.runtime_root / "runs" / self.run.run_id / "notifications.jsonl"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(json.dumps({"status": "notifier_active"}) + "\n"
                               + json.dumps({**line, "idempotency_key": key, "event_type": "step_done"}) + "\n")
                notify.uninstall()
                install_notifier(self, self.hub, self.runtime_root)
                self.hub.requests.clear()
                self.resume()
                self.assertEqual("step_done" in self.request_types(), recovered)

    def test_a_recovered_422_is_a_duplicate_not_a_conflict(self) -> None:
        self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        self.hub.queue(422, 422)
        install_notifier(self, self.hub, self.runtime_root)
        self.resume()
        notify.flush(10)
        lines = [json.loads(line) for line in
                 (self.runtime_root / "runs" / self.run.run_id / "notifications.jsonl").read_text().splitlines()]
        statuses = [(line["status"], line.get("source")) for line in lines[1:]]
        self.assertEqual(statuses, [("duplicate", "server")] * 2)

    def test_a_disabled_type_is_recovered_late_only_when_enabled_inside_the_window(self) -> None:
        self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        install_notifier(self, self.hub, self.runtime_root, events={"events": {"step_done": {"enabled": False}}})
        self.resume()
        self.assertEqual(self.request_types(), ["milestone_planned"])
        notify.uninstall()
        self.hub.requests.clear()
        install_notifier(self, self.hub, self.runtime_root)  # the type is enabled again within the window
        self.resume()
        self.assertEqual(self.request_types(), ["step_done"])

    def test_a_record_reconciled_in_this_call_is_recovered_whatever_its_age(self) -> None:
        # D13 (i): the record `resume` reconciled to terminal in this call needs no window; (ii)-(iv) still hold.
        record = self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        install_notifier(self, self.hub, self.runtime_root)
        old = {**record, "updated_at": "2020-01-01T00:00:00Z"}
        job._recover_job_notices([old], self.runtime_root, self.managed_repo)
        self.assertEqual(self.request_types(), [], "an old record not reconciled now is outside the window")
        job._recover_job_notices([{**old, "reconciled_this_call": True}], self.runtime_root, self.managed_repo)
        self.assertEqual(self.request_types(), ["step_done", "milestone_planned"])
        self.hub.requests.clear()
        failed = {**old, "job_id": "20261012T000001Z-bbbb", "status": job.STATUS_FAILED, "reconciled_this_call": True}
        job._recover_job_notices([failed], self.runtime_root, self.managed_repo)
        self.assertEqual(self.request_types(), ["lane_blocked"])

    def test_the_recovery_bound_examines_at_most_fifty_historical_records(self) -> None:
        record = self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        install_notifier(self, self.hub, self.runtime_root)
        recent = job._now()
        many = [{**record, "job_id": f"20261012T0000{i:02d}Z-{i:04x}", "updated_at": recent} for i in range(60)]
        examined: list[str] = []
        real = job.derive_job_event

        def counting(rec, **kwargs):
            examined.append(rec["job_id"])
            return real(rec, **kwargs)

        with unittest.mock.patch.object(job, "derive_job_event", counting):
            job._recover_job_notices(many, self.runtime_root, self.managed_repo)
            self.assertEqual(len(examined), job.RECOVERY_RECORD_LIMIT)
            # D13: records reconciled in this call are outside the historical bound.
            examined.clear()
            fresh = [{**r, "reconciled_this_call": True} for r in many[:55]]
            job._recover_job_notices(fresh + many[55:], self.runtime_root, self.managed_repo)
            self.assertEqual(len(examined), 55 + 5)
        self.assertTrue(notify.flush(10))

    def test_each_examined_run_log_is_read_once(self) -> None:
        record = self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        install_notifier(self, self.hub, self.runtime_root)
        real = job._outcome_lines
        reads: list[str] = []
        with unittest.mock.patch.object(job, "_outcome_lines", lambda root, run_id: (reads.append(run_id),
                                                                                    real(root, run_id))[1]):
            job._recover_job_notices([record], self.runtime_root, self.managed_repo)
        self.assertEqual(len(reads), len(set(reads)))
        self.assertTrue(notify.flush(10))

    def test_an_inert_notifier_recovers_nothing(self) -> None:
        self.finish_job_unnotified()
        self.active_log({"status": "notifier_active"})
        install_notifier(self, self.hub, self.runtime_root, key=None)
        self.resume()
        self.assertEqual(self.hub.requests, [])


class UsageReleaseWiringTest(test_usage_command._UsageCommandCase):
    """``usage --release`` installs the notifier: an enabled ``usage_released`` reaches the fake SignalHub once."""

    def test_an_enabled_release_sends_one_usage_released_notification(self) -> None:
        hub = FakeSignalHub().start()
        self.addCleanup(hub.stop)
        self.reading(10, weekly=1)
        token = self.reserve_token("--role", "plan", "--model", "m", "--repo", str(self.repo), "--run-id", "r1")
        self.now += 120
        self.reading(15, weekly=2)
        self.settings_path.write_text(json.dumps({
            "usage": {"resume_grace_seconds": 1},
            "notifications": {"events": {"events": {"usage_released": {"enabled": True}}}}}))
        env = {"SIGNALHUB_URL": hub.url, "SIGNALHUB_API_KEY": KEY}
        with unittest.mock.patch.dict(os.environ, env):
            code, out, _err = self.run_cmd("usage", "--release", token)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("released; measured delta, charged 5.0%", out)
        self.assertEqual([r["body"]["metadata"]["event_type"] for r in hub.requests], ["usage_released"])
        self.assertEqual(self.shared()["spend"]["run"]["claude"]["r1"]["charged"], 5)


class UsageRenewWiringTest(test_usage_command._UsageCommandCase):
    """``usage --renew`` installs the notifier: it lapses expired reservations, and each enabled
    ``usage_reservation_expired`` reaches the fake SignalHub once."""

    def test_renew_lapsing_expired_reservations_sends_one_notice_each(self) -> None:
        hub = FakeSignalHub().start()
        self.addCleanup(hub.stop)
        self.reading(10, weekly=1)
        first = self.reserve_token("--role", "plan", "--model", "m", "--repo", str(self.repo), "--run-id", "r1")
        second = self.reserve_token("--role", "plan", "--model", "m", "--repo", str(self.repo), "--run-id", "r2")
        self.settings_path.write_text(json.dumps({
            "notifications": {"events": {"events": {"usage_reservation_expired": {"enabled": True}}}}}))
        self.now += 10 * 3600  # past both leases
        env = {"SIGNALHUB_URL": hub.url, "SIGNALHUB_API_KEY": KEY}
        with unittest.mock.patch.dict(os.environ, env):
            code, _out, _err = self.run_cmd("usage", "--renew", first)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(sorted(r["body"]["metadata"]["event_type"] for r in hub.requests),
                         ["usage_reservation_expired"] * 2)
        self.assertIn(second, self.shared()["lapsed"])


class UsageShowWiringTest(test_usage_command._UsageCommandCase):
    """Plain ``usage`` runs the accounting sweep, which can write ``usage_reservation_expired``; the
    notifier is installed so that event is sent once, and a display with nothing to sweep stays silent."""

    def _enable(self) -> dict:
        self.settings_path.write_text(json.dumps({
            "notifications": {"events": {"events": {"usage_reservation_expired": {"enabled": True}}}}}))
        return {"SIGNALHUB_URL": self.hub.url, "SIGNALHUB_API_KEY": KEY}

    def setUp(self) -> None:
        super().setUp()
        self.hub = FakeSignalHub().start()
        self.addCleanup(self.hub.stop)

    def test_plain_usage_sweep_expiring_reservations_sends_one_notice_each(self) -> None:
        self.reading(10, weekly=1)
        for run_id in ("r1", "r2"):  # bound to a job whose record was never written
            token = self.reserve_token("--role", "plan", "--model", "m", "--repo", str(self.repo),
                                       "--run-id", run_id)
            usage.bind_job(self.runtime_root, token, f"job-{run_id}", now=self.now)
        env = self._enable()
        self.now += 10 * 3600  # past both leases
        with unittest.mock.patch.dict(os.environ, env):
            code, _out, _err = self.run_cmd("usage", "--json")
            again, _o, _e = self.run_cmd("usage", "--json")
        self.assertEqual((code, again), (cli.EXIT_OK, cli.EXIT_OK))
        self.assertEqual(sorted(r["body"]["metadata"]["event_type"] for r in self.hub.requests),
                         ["usage_reservation_expired"] * 2)

    def test_plain_usage_with_nothing_to_sweep_is_silent(self) -> None:
        self.reading(10, weekly=1)
        env = self._enable()
        with unittest.mock.patch.dict(os.environ, env):
            code, _out, err = self.run_cmd("usage")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual((self.hub.requests, err), ([], ""))


class BranchWiringTest(test_trunk_preflight.RunWaitTest):
    """The branch-gate, merge, release and refusal events over the fake forge and fake SignalHub (the
    ``RunWaitTest`` fixture: an auto-merge binding with releases)."""

    def setUp(self) -> None:
        super().setUp()
        self.hub = FakeSignalHub().start()
        self.addCleanup(self.hub.stop)

    def cli_hub(self, command: str, *args: str, **kwargs):
        env = {"SIGNALHUB_URL": self.hub.url, "SIGNALHUB_API_KEY": KEY}
        with unittest.mock.patch.dict(os.environ, env):
            return self.cli(command, *args, **kwargs)

    def sent_types(self, *, without: tuple[str, ...] = ("run_finished",)) -> list[str]:
        out = [r["body"]["metadata"]["event_type"] for r in self.hub.requests]
        return [t for t in out if t not in without]

    def outcome_lines(self) -> list[dict]:
        lines = []
        for path in sorted((self.lc.runtime / "runs").glob("*/notifications.jsonl")):
            lines += [json.loads(line) for line in path.read_text().splitlines()]
        return lines

    def test_an_ordinary_pending_checks_step_sends_nothing_but_run_finished(self) -> None:
        self.accepted()
        result = self.cli_hub("step")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.sent_types(), [])

    def test_failing_checks_send_one_blocker_per_occurrence(self) -> None:
        number = self.accepted()
        self.cli_hub("step")  # the PR is readied and its title updated: checks_pending, which sends nothing
        self.assertEqual(self.sent_types(), [])
        self.set_checks(number, "fail")
        self.assertEqual(self.cli_hub("step").code, cli.EXIT_GATE)
        self.assertEqual(self.sent_types(), ["lane_blocked"])
        key = next(r["idempotency_key"] for r in self.hub.requests if "lane_blocked" in r["idempotency_key"])
        # An unchanged gate seen on a second step: the same key and body, never a conflict.
        self.hub.requests.clear()
        self.cli_hub("step")
        self.assertEqual([r["idempotency_key"] for r in self.hub.requests if "lane_blocked" in r["idempotency_key"]],
                         [key])
        self.assertEqual({line["status"] for line in self.outcome_lines() if line.get("event_type") == "lane_blocked"},
                         {"sent"})
        # A checks re-run: pending is observed between, then it fails again on the same head -> a new key.
        self.set_checks(number, "pending")
        self.cli_hub("step")
        self.set_checks(number, "fail")
        self.hub.requests.clear()
        self.cli_hub("step")
        keys = [r["idempotency_key"] for r in self.hub.requests if "lane_blocked" in r["idempotency_key"]]
        self.assertEqual(len(keys), 1)
        self.assertNotEqual(keys[0], key)
        gate = self.record()["last_observation"]["gate"]
        self.assertEqual(gate["code"], mb.GATE_CHECKS_FAILING)
        self.assertGreaterEqual(gate["streak"], 3)
        self.assertEqual({line["status"] for line in self.outcome_lines() if line.get("event_type") == "lane_blocked"},
                         {"sent"})

    def test_the_streak_write_moves_no_binding_event(self) -> None:
        number = self.accepted()
        self.cli_hub("step")
        self.set_checks(number, "fail")
        self.cli_hub("step")
        before = self.record()["updated_at"], [e["at"] for e in self.binding_events()][-1]
        self.cli_hub("step")
        self.assertEqual([e["at"] for e in self.binding_events()][-1], before[1])

    def test_a_wait_that_runs_to_the_release_sends_the_merge_and_the_release_once(self) -> None:
        number = self.accepted()
        self.changes = [lambda: (self.set_checks(number, "pass"), self.gh_edit(number, mergeStateStatus="UNKNOWN")),
                        lambda: self.gh_edit(number, mergeStateStatus="CLEAN"),
                        lambda: self.release()]
        with self.waiting():
            result = self.cli_hub("run")
        self.assertEqual(result.code, cli.EXIT_OK, result.stderr)
        counts = {t: self.sent_types(without=()).count(t) for t in set(self.sent_types(without=()))}
        self.assertEqual(counts, {"pr_merged": 1, "release_published": 1, "run_finished": 1})
        merged = next(r for r in self.hub.requests if r["body"]["metadata"]["event_type"] == "pr_merged")
        self.assertEqual(merged["body"]["metadata"]["work_item"], test_trunk_preflight.WI)
        self.assertIn(f"#{number}", merged["body"]["title"])
        self.assertTrue(merged["body"]["link"].endswith(f"/pull/{number}"))
        released = next(r for r in self.hub.requests if r["body"]["metadata"]["event_type"] == "release_published")
        self.assertIn("v1.0.0", released["body"]["title"])
        summary = [e for e in self.events_of(self.run_record()["run_id"]) if e["event"] == "notifications_summary"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["sent"], 3)

    def test_a_spent_wait_sends_one_checks_wait_not_a_blocker(self) -> None:
        self.accepted()
        settings = self.tmp_root / "settings.json"
        settings.write_text(json.dumps({"merge": {"wait_seconds": 50}}))
        with self.waiting(), unittest.mock.patch.dict(os.environ, {"WORKFLOW_CONTROLLER_SETTINGS": str(settings)}):
            result = self.cli_hub("run")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.sent_types(), ["checks_wait"])
        self.assertEqual(self.hub.requests[0]["body"]["severity"], "LOW")

    def test_a_spent_wait_on_checks_pending_long_sends_one_blocker(self) -> None:
        self.accepted()
        self.cli_hub("step")  # the PR is readied and its title updated: checks_pending, which sends nothing
        self.assertEqual(self.sent_types(), [])
        path = self.lc.runtime / mb.events_rel(self.key, test_trunk_preflight.WI)
        path.write_text("".join(json.dumps({**e, "at": "2020-01-01T00:00:00Z"}) + "\n" for e in self.binding_events()))
        self.hub.requests.clear()
        settings = self.tmp_root / "settings.json"
        settings.write_text(json.dumps({"merge": {"wait_seconds": 50}}))
        with self.waiting(), unittest.mock.patch.dict(os.environ, {"WORKFLOW_CONTROLLER_SETTINGS": str(settings)}):
            result = self.cli_hub("run")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.sent_types(), ["lane_blocked"])
        self.assertEqual(self.hub.requests[0]["body"]["severity"], "CRITICAL")

    def test_a_dirty_merge_conflict_is_a_blocker_at_once(self) -> None:
        number = self.accepted()
        self.cli_hub("step")
        self.set_checks(number, "pass")
        self.gh_edit(number, mergeStateStatus="DIRTY")
        result = self.cli_hub("step")
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.sent_types(), ["lane_blocked"])

    def test_pr_merged_recovery_runs_before_the_preflight_and_the_finally_sends_once(self) -> None:
        number = self.accepted()
        record = self.record()
        written = mb.write_record(self.lc.runtime, self.key, {**record, "state": mb.MERGED_SQUASHED},
                                  now="2026-10-12T00:00:00Z")
        managed = SimpleNamespace(root=self.root)
        install_notifier(self, self.hub, self.lc.runtime)
        job._recover_merge_notices(managed, self.lc.runtime)
        self.assertTrue(notify.flush(10))
        self.assertEqual(self.sent_types(without=()), ["pr_merged"])
        key = self.hub.requests[0]["idempotency_key"]
        # The finally path for a merge this step observed: same work item and PR, same key (an in-process duplicate).
        ctx = mb.Context(repo_root=self.root, runtime_root=self.lc.runtime, clock=lambda: "2026-10-12T00:00:05Z")
        mb._event(ctx, self.key, written, "merged_squashed", head="h")
        job._notify_merge_events(managed, self.lc.runtime, "2026-10-12T00:00:01Z", ["merged_squashed"], None)
        self.assertTrue(notify.flush(10))
        self.assertEqual(self.hub.keys(), [key], "one notice for the same merge")
        # A step that did not observe a merge sends none.
        job._notify_merge_events(managed, self.lc.runtime, "2026-10-12T00:00:01Z", ["observed"], None)
        self.assertEqual(self.hub.keys(), [key])
        self.assertTrue(number)

    def typed_requests(self, event_type: str, *, at_least: int = 1) -> list[dict]:
        """The hub's requests of ``event_type``, waiting a little for an interrupted run's short flush."""
        def found() -> list[dict]:
            return [r for r in self.hub.requests if r["body"]["metadata"]["event_type"] == event_type]
        process_fixtures.wait_until(lambda: len(found()) >= at_least, timeout=10)
        return found()

    def merge_ready(self) -> int:
        """Accepted, the head pushed and its checks passing: the next preflight merges (the PR number)."""
        number = self.accepted()
        self.assertEqual(self.cli("step").code, cli.EXIT_GATE)  # the head pushed; its checks pending
        self.set_checks(number, "pass")
        return number

    def test_a_merge_seen_by_a_step_that_ends_at_a_branch_gate_sends_pr_merged_once(self) -> None:
        number = self.merge_ready()
        result = self.cli_hub("step")  # merged; the release is pending (a wait gate under wait 0: no gate notice)
        self.assertEqual(result.code, cli.EXIT_GATE, result.stderr)
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.assertEqual(self.sent_types(), ["pr_merged"])
        [merged] = self.typed_requests("pr_merged")
        self.assertIn(f"#{number}", merged["body"]["title"])
        # The next step's entry recovery derives the same key and body: the server answers 200, nothing new.
        self.hub.requests.clear()
        self.cli_hub("step")
        self.assertEqual([r["idempotency_key"] for r in self.typed_requests("pr_merged")],
                         [merged["idempotency_key"]])
        self.assertNotIn("conflict", {line["status"] for line in self.outcome_lines()})

    def test_a_ctrl_c_in_the_release_wait_after_the_merge_still_sends_pr_merged(self) -> None:
        # F1: one wait poll wrote merged_squashed, then Ctrl-C during the release_pending sleep raises.
        self.merge_ready()

        def interrupt() -> None:
            raise KeyboardInterrupt

        self.changes = [interrupt]
        with self.waiting():
            result = self.cli_hub("run")
        self.assertEqual(result.code, cli.SIGINT_EXIT_STATUS, result.stderr)
        self.assertIn(f"interrupted while waiting at {mb.GATE_RELEASE_PENDING}", result.stderr)
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.assertEqual(len(self.typed_requests("pr_merged")), 1)
        self.assertEqual(self.sent_types(), ["pr_merged"])

    def test_a_refusal_raised_by_the_preflight_after_the_merge_still_sends_pr_merged(self) -> None:
        # F1: the `_close_trunk_step3` shape -- the merge event is written, then the preflight raises (exit 20).
        self.merge_ready()
        real = mb.waiting_preflight

        def merged_then_refused(ctx, **kwargs):
            real(ctx, **kwargs)
            raise mb._refuse("the squash commit is not on origin/main", work_item_id=test_trunk_preflight.WI)

        with unittest.mock.patch.object(mb, "waiting_preflight", merged_then_refused):
            result = self.cli_hub("step")
        self.assertEqual(result.code, cli.EXIT_FAIL_CLOSED, result.stderr)
        self.assertIn("the squash commit is not on origin/main", result.stderr, "the original refusal propagates")
        self.assertEqual(sorted(self.sent_types()), ["lane_blocked", "pr_merged"])

    def test_a_changed_release_refusal_after_the_merge_still_sends_pr_merged(self) -> None:
        # The exits after the preflight that write no job record (the changed-release refusal; a usage hold
        # leaves the same way) run after the `finally` that enqueued the merge.
        self.merge_ready()
        refusal = cli.ControllerError("the installed Workflow release changed during the step")
        with unittest.mock.patch.object(job, "_refuse_changed_release", side_effect=refusal):
            result = self.cli_hub("step")
        self.assertEqual(result.code, cli.EXIT_FAIL_CLOSED, result.stderr)
        self.assertEqual(sorted(self.sent_types()), ["lane_blocked", "pr_merged"])

    def test_a_draft_toggled_back_without_a_push_is_a_new_user_gate(self) -> None:
        # F5 of round 10: merge_held, checks_failing, merge_held on one head with no binding event between.
        number = self.accepted()
        self.cli_hub("step")  # the head pushed; its checks pending
        self.set_checks(number, "pass")
        self.gh_edit(number, mergeStateStatus="UNKNOWN")
        self.cli_hub("step")  # marked ready, READY; merge_pending, a wait gate under wait 0: no notice
        self.assertEqual(self.record()["state"], mb.READY)
        self.gh_edit(number, isDraft=True)
        self.assertEqual(self.cli_hub("step").code, cli.EXIT_GATE)
        events_after_first = len(self.binding_events())
        self.gh_edit(number, isDraft=False)
        self.set_checks(number, "fail")
        self.assertEqual(self.cli_hub("step").code, cli.EXIT_GATE)
        self.gh_edit(number, isDraft=True)
        self.assertEqual(self.cli_hub("step").code, cli.EXIT_GATE)
        self.assertEqual(len(self.binding_events()), events_after_first, "no binding event between the two")
        held = self.typed_requests("user_gate", at_least=2)
        self.assertEqual([r["body"]["title"].split(":")[1].split()[0] for r in held], [mb.GATE_MERGE_HELD] * 2)
        self.assertEqual(len({r["idempotency_key"] for r in held}), 2, "the second merge_held is a new occurrence")
        self.assertEqual(len(self.typed_requests("lane_blocked")), 1)
        self.assertEqual(self.record()["last_observation"]["gate"]["code"], mb.GATE_MERGE_HELD)

    def test_a_refusal_sends_one_lane_blocked_and_a_different_refusal_a_new_one(self) -> None:
        self.bind_step()
        self.git("switch", "-q", "--detach")
        first = self.cli_hub("step")
        self.assertEqual(first.code, cli.EXIT_FAIL_CLOSED, first.stderr)
        self.assertEqual(self.sent_types(), ["lane_blocked"])
        key = next(r["idempotency_key"] for r in self.hub.requests if "lane_blocked" in r["idempotency_key"])
        self.hub.requests.clear()
        self.assertEqual(self.cli_hub("step").code, cli.EXIT_FAIL_CLOSED)
        self.assertEqual([r["idempotency_key"] for r in self.hub.requests if "lane_blocked" in r["idempotency_key"]],
                         [key])
        # The read-only commands that also exit 20 send nothing.
        self.hub.requests.clear()
        self.cli_hub("explain")
        self.assertEqual(self.sent_types(without=()), [])


for _name in dir(BranchWiringTest):
    if _name.startswith("test_") and _name not in BranchWiringTest.__dict__:
        setattr(BranchWiringTest, _name, None)  # the inherited RunWaitTest cases run in their own module


if __name__ == "__main__":
    unittest.main()
