"""``controller.review_rounds`` (workflow-controller-review-convergence CP1,
D1/D2): the round of a stage and the cap's boundary table."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

from controller import review_rounds as rr

REPO = "/target"
ITEM = "item-1"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def record(n: int, phase: str, *, plan: int | None = None, impl: int | None = None, repo: str = REPO,
           item: str | None = ITEM, status: str = "FINISHED", action: str = "plan.review.local",
           reconcile: str = "progress") -> dict:
    return {
        "job_id": f"j{n:03d}", "created_at": f"2026-10-16T10:{n:02d}:00Z", "target_repo": repo,
        "work_item_id": item, "status": status,
        "pre_state": {"phase": phase, "plan_revision": plan, "implementation_revision": impl},
        "protocol": {"action_id": action, "decided_ns": n, "reconcile": {"class": reconcile}},
    }


def snap(phase: str, *, plan: int | None = None, impl: int | None = None, rounds=None) -> dict:
    out = {"phase": phase, "plan_revision": plan, "implementation_revision": impl}
    if rounds is not None:
        out["review_round"] = rounds
    return out


def get(snapshot, stage, records=()):
    return rr.current_round(snapshot, stage, records, target_repo=REPO, work_item_id=ITEM)


class WorkflowIntegerTest(unittest.TestCase):
    def test_an_integer_is_used_as_given(self) -> None:
        s = snap("AWAITING_LOCAL_PLAN_REVIEW", plan=2, rounds={"plan": 7, "implementation": 3})
        self.assertEqual(get(s, "plan"), (7, "workflow"))
        self.assertEqual(get(s, "implementation"), (3, "workflow"))

    def test_null_missing_and_non_integer_fall_back_and_are_never_zero(self) -> None:
        for rounds in ({"plan": None, "implementation": None}, {}, None, {"plan": "3", "implementation": True},
                       {"plan": 0, "implementation": 0}):
            with self.subTest(rounds=rounds):
                s = snap("AWAITING_LOCAL_PLAN_REVIEW", plan=2, impl=None, rounds=rounds)
                self.assertEqual(get(s, "plan"), (1, "jobs"))
                self.assertEqual(get(s, "implementation"), (1, "jobs"))

    def test_a_missing_snapshot_still_counts_at_least_one(self) -> None:
        self.assertEqual(get(None, "plan"), (1, "jobs"))
        self.assertEqual(get(None, "implementation"), (1, "jobs"))


class ImplementationCountTest(unittest.TestCase):
    def test_counts_rises_whichever_action_status_or_class(self) -> None:
        records = [
            record(1, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", impl=1, action="implementation.review.local"),
            record(2, "REVISING_IMPLEMENTATION", impl=1, action="implementation.apply_review",
                   reconcile="gate_reached"),
            record(3, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", impl=2, action="implementation.review.local",
                   status="FAILED", reconcile="no_progress"),
            record(4, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", impl=3, action="functional.apply_findings"),
        ]
        self.assertEqual(get(snap("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", impl=4), "implementation", records),
                         (4, "jobs"))

    def test_a_same_content_republication_is_not_a_round(self) -> None:
        records = [record(1, "REVISING_IMPLEMENTATION", impl=3), record(2, "REVISING_IMPLEMENTATION", impl=3)]
        self.assertEqual(get(snap("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", impl=3), "implementation", records),
                         (1, "jobs"))

    def test_null_missing_and_a_decrease_are_skipped(self) -> None:
        records = [record(1, "X", impl=2), record(2, "X", impl=None), {**record(3, "X"), "pre_state": {"phase": "X"}},
                   record(4, "X", impl=1), record(5, "X", impl=2)]
        # 2 -> (skipped) -> (skipped) -> 1 (decrease, no count) -> 2 (rise from 1) -> snapshot 3 (rise).
        self.assertEqual(get(snap("X", impl=3), "implementation", records), (3, "jobs"))

    def test_a_hand_made_rise_between_two_jobs_is_counted(self) -> None:
        records = [record(1, "X", impl=1), record(2, "X", impl=2)]
        self.assertEqual(get(snap("X", impl=2), "implementation", records), (2, "jobs"))

    def test_other_repositories_items_and_gate_blocked_records_are_ignored(self) -> None:
        records = [record(1, "X", impl=1), record(2, "X", impl=5, repo="/other"),
                   record(3, "X", impl=6, item="other"), record(4, "X", impl=7, status="GATE_BLOCKED"),
                   record(5, "X", impl=2)]
        self.assertEqual(get(snap("X", impl=2), "implementation", records), (2, "jobs"))

    def test_the_records_are_read_oldest_first_whatever_their_order(self) -> None:
        records = [record(3, "X", impl=3), record(1, "X", impl=1), record(2, "X", impl=2)]
        self.assertEqual(get(snap("X", impl=3), "implementation", records), (3, "jobs"))


class PlanCountTest(unittest.TestCase):
    READY = "AWAITING_LOCAL_PLAN_REVIEW"

    def test_a_revision_counts_once_however_many_observations_show_it(self) -> None:
        records = [record(1, self.READY, plan=2), record(2, self.READY, plan=2), record(3, self.READY, plan=3)]
        self.assertEqual(get(snap(self.READY, plan=3), "plan", records), (2, "jobs"))

    def test_authoring_phases_never_count(self) -> None:
        for phase in ("PLANNING", "REVISING_PLAN", "AMENDING_PLAN"):
            with self.subTest(phase=phase):
                records = [record(1, phase, plan=1), record(2, phase, plan=2)]
                self.assertEqual(get(snap(self.READY, plan=3), "plan", records), (1, "jobs"))

    def test_a_missing_phase_or_non_integer_revision_is_ignored(self) -> None:
        bare = {**record(1, self.READY, plan=2), "pre_state": {"plan_revision": 2}}
        records = [bare, record(2, self.READY, plan=None), record(3, self.READY, plan=True)]
        self.assertEqual(get(snap("PLANNING", plan=4), "plan", records), (1, "jobs"))

    def test_interrupted_authoring_counts_one_round(self) -> None:
        records = [record(1, self.READY, plan=2), record(2, "REVISING_PLAN", plan=2),
                   record(3, "REVISING_PLAN", plan=3)]
        self.assertEqual(get(snap(self.READY, plan=4), "plan", records), (2, "jobs"))

    def test_a_precreated_planning_child_first_publication_is_round_one(self) -> None:
        records = [record(1, "PLANNING", plan=1, action="plan.author"),
                   record(2, "PLANNING", plan=1, action="plan.author")]
        # /milestone-plan advanced the revision to 2 and published it: the first ready observation.
        self.assertEqual(get(snap(self.READY, plan=2), "plan", records), (1, "jobs"))
        records.append(record(3, self.READY, plan=2))
        # A REVISE and an apply published revision 3.
        self.assertEqual(get(snap(self.READY, plan=3), "plan", records), (2, "jobs"))

    def test_a_version_1_item_is_counted_at_its_own_ready_phase(self) -> None:
        """#67: a ``"1"``-governed item is ready at ``AWAITING_EXTERNAL_PLAN_REVIEW``, a phase none of the
        two-stage items reaches; the other phases do not count for it."""
        legacy = "AWAITING_EXTERNAL_PLAN_REVIEW"

        def seen(n: int, phase: str, plan: int, version: str | None) -> dict:
            made = record(n, phase, plan=plan)
            made["pre_state"]["governing_workflow_version"] = version
            return made

        def at(phase: str, plan: int, version: str | None) -> dict:
            return {**snap(phase, plan=plan), "governing_workflow_version": version}

        self.assertEqual(get(at(legacy, 1, "1"), "plan", []), (1, "jobs"))
        records = [seen(1, legacy, 1, "1")]
        self.assertEqual(get(at(legacy, 2, "1"), "plan", records), (2, "jobs"))
        records.append(seen(2, legacy, 2, "1"))
        self.assertEqual(get(at(legacy, 3, "1"), "plan", records), (3, "jobs"))
        # The two-stage phases are not a "1" item's ready phases; the legacy one is not a two-stage item's.
        self.assertEqual(get(at(legacy, 3, "1"), "plan", [seen(1, self.READY, 1, "1"), seen(2, self.READY, 2, "1")]),
                         (1, "jobs"))  # only the snapshot, at the legacy phase, counts
        for version in ("2.2", "2.1", None):
            with self.subTest(version=version):
                records = [seen(1, legacy, 1, version), seen(2, legacy, 2, version), seen(3, self.READY, 3, version)]
                self.assertEqual(get(at(self.READY, 3, version), "plan", records), (1, "jobs"))
        two = [seen(1, self.READY, 1, "2.2"), seen(2, self.READY, 2, "2.2")]
        self.assertEqual(get(at(self.READY, 3, "2.2"), "plan", two), (3, "jobs"))

    def test_the_ready_phase_pin_is_not_changed_by_the_version_1_rule(self) -> None:
        self.assertNotIn("AWAITING_EXTERNAL_PLAN_REVIEW", rr.PLAN_REVIEW_READY_PHASES)

    def test_the_count_is_a_lower_bound_of_the_workflows_plan_round(self) -> None:
        """An apply round that published new plan content without advancing
        ``plan_revision``: the Workflow's number is higher, never lower."""
        sys.path.insert(0, str(SCRIPTS))
        try:
            import workflow_protocol
            import workflow_state
        finally:
            sys.path.remove(str(SCRIPTS))
        ids = [f"c{n}" for n in range(10)]
        work_item = {
            "plan_revision": 10,
            "plan_review_binding": {"status": workflow_state.PLAN_REVIEW_BINDING_BOUND,
                                    "consumed": {"review_content_id": ids[-1], "plan_revision": 10}},
            workflow_state.CONSUMED_PLAN_REVIEW_CONTENT_IDS_KEY: ids,
        }
        workflow = workflow_protocol._plan_review_round(work_item)
        self.assertEqual(workflow, 11)
        records = [record(n, self.READY, plan=n + 1) for n in range(1, 10)]
        count, source = get(snap(self.READY, plan=10), "plan", records)
        self.assertEqual(source, "jobs")
        self.assertLessEqual(count, workflow)
        self.assertLess(count, workflow)


def _ready_phases_in(path: Path) -> frozenset[str] | None:
    """The ``PLAN_REVIEW_READY_PHASES`` a Workflow ``workflow_state.py`` assigns, read from its source, or ``None``
    when that release does not define it."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLAN_REVIEW_READY_PHASES"
                                                for t in node.targets):
            return frozenset(ast.literal_eval(node.value.args[0]))
    return None


class ReadyPhasesPinTest(unittest.TestCase):
    """#66: ``review_rounds`` keeps its own copy of the plan-review ready phases (it imports no other Controller
    module), so a change to one copy must not go unnoticed. Each copy is compared with the Workflow's own, not
    with each other: the installed ``scripts/`` (the exception ``docs/guide/development.md`` states) and every
    vendored release that defines the constant."""

    def test_the_copies_equal_the_workflows_own(self) -> None:
        from controller import evidence

        self.assertEqual(rr.PLAN_REVIEW_READY_PHASES, evidence.PLAN_REVIEW_READY_PHASES)
        self.assertEqual(_ready_phases_in(SCRIPTS / "workflow_state.py"), rr.PLAN_REVIEW_READY_PHASES)
        vendored = sorted((SCRIPTS.parent / "tests" / "workflow_releases").glob("*/scripts/workflow_state.py"))
        checked = []
        for path in vendored:
            phases = _ready_phases_in(path)
            if phases is None:
                continue
            checked.append(path.parent.parent.name)
            self.assertEqual(phases, rr.PLAN_REVIEW_READY_PHASES, path.parent.parent.name)
        self.assertIn("2.9.0", checked)
        self.assertGreaterEqual(len(checked), 3)


class CapDecisionTest(unittest.TestCase):
    """D2's boundary table."""

    def test_apply_stops_at_the_cap(self) -> None:
        for action in ("plan.apply_review", "implementation.apply_review"):
            self.assertFalse(rr.cap_decision(action, 3, 4), action)
            self.assertTrue(rr.cap_decision(action, 4, 4), action)
            self.assertTrue(rr.cap_decision(action, 5, 4), action)

    def test_local_review_stops_above_the_cap(self) -> None:
        for action in ("plan.review.local", "implementation.review.local"):
            self.assertFalse(rr.cap_decision(action, 4, 4), action)
            self.assertTrue(rr.cap_decision(action, 5, 4), action)

    def test_no_other_action_stops(self) -> None:
        for action in ("plan.start", "plan.author", "plan.record_external", "implementation.checkpoint",
                       "implementation.self_review", "implementation.record_external", "functional.prepare",
                       "functional.apply_findings", "plan.review.external", "implementation.review.external", None):
            self.assertFalse(rr.cap_decision(action, 100, 1), action)

    def test_stage_of_action(self) -> None:
        self.assertEqual(rr.stage_of("plan.apply_review"), "plan")
        self.assertEqual(rr.stage_of("implementation.review.external"), "implementation")
        self.assertEqual(rr.stage_of("functional.apply_findings"), "functional")
        self.assertEqual(rr.stage_of(None), "none")
        self.assertEqual(rr.stage_of("pr.apply_review"), "none")


if __name__ == "__main__":
    unittest.main()
