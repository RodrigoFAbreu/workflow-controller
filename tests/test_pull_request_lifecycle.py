"""``controller.milestone_branch``'s Draft PR lifecycle and completion
(``workflow-controller-trunk-branch-pr-release-orchestration`` CP7): PR
creation, discovery and reuse, re-verification, drift, readiness, the merge
gate, the merged-PR handling and close-out (from the branch, from trunk, and
PR-less), and the refusal-state exits -- over a bare origin, a clone, and the
executable fake ``gh`` (``tests/fake_gh.py``). A human merge is simulated in
a second clone of the origin, plus an edit of the fake forge's state."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import decision, forge as forge_mod, gitrepo, milestone_branch as mb, runtime  # noqa: E402
from controller.errors import BranchBindingError, ForgeUndecidableError  # noqa: E402
from tests.fixtures import commit_all, current_head, run, trailer_message  # noqa: E402
from tests.test_milestone_branch import BRANCH, WID, _Case  # noqa: E402


class _Lifecycle(_Case):
    # -- a human on GitHub, and a second clone ---------------------------------------------

    def human(self) -> Path:
        """A second clone of the origin, on the trunk at the origin's tip."""
        human = self.tmp / "human"
        if not human.exists():
            run(["git", "clone", "-q", str(self.origin), str(human)])
            run(["git", "config", "user.email", "human@example.invalid"], cwd=human)
            run(["git", "config", "user.name", "Human"], cwd=human)
        run(["git", "fetch", "-q", "origin"], cwd=human)
        run(["git", "switch", "-q", "main"], cwd=human)
        run(["git", "reset", "-q", "--hard", "origin/main"], cwd=human)
        return human

    def origin_ref(self, ref: str) -> str:
        return run(["git", "--git-dir", str(self.origin), "rev-parse", ref]).stdout.strip()

    def trunk_commit(self, name: str = "drift.txt") -> str:
        """Someone else lands a commit on the origin's trunk."""
        human = self.human()
        (human / name).write_text("elsewhere\n")
        commit = commit_all(human, f"land {name} on main")
        run(["git", "push", "-q", "origin", "main"], cwd=human)
        return commit

    def human_merge(self, number: int, *, how: str = "merge") -> str:
        """A human merges pull request ``number`` on GitHub ("Create a merge
        commit", a fast-forward, or a squash) and returns its final head."""
        head = self.origin_ref(f"refs/heads/{BRANCH}")
        human = self.human()
        if how == "merge":
            run(["git", "merge", "-q", "--no-ff", "-m", f"Merge pull request #{number}", f"origin/{BRANCH}"], cwd=human)
        elif how == "ff":
            run(["git", "merge", "-q", "--ff-only", f"origin/{BRANCH}"], cwd=human)
        else:
            run(["git", "merge", "-q", "--squash", f"origin/{BRANCH}"], cwd=human)
            run(["git", "commit", "-q", "-m", f"Squash pull request #{number}"], cwd=human)
        run(["git", "push", "-q", "origin", "main"], cwd=human)
        self.gh_edit(number, state="MERGED", headRefOid=head, mergedAt="2026-09-25T01:00:00Z",
                     mergeCommit={"oid": current_head(human)})
        return head

    def gh_edit(self, number: int, **fields) -> None:
        from tests import fake_gh

        data = self.gh()
        for pr in data["prs"]:
            if pr["number"] == number:
                if fields.get("state") in ("CLOSED", "MERGED") and "headRefOid" not in fields:
                    fields["headRefOid"] = self.origin_ref(f"refs/heads/{pr['headRefName']}")
                pr.update(fields)
        fake_gh.write_state(self.gh_state, data)

    def set_checks(self, number: int, *buckets: tuple[str, str]) -> None:
        self.gh_edit(number, checks=[{"name": name, "state": bucket.upper(), "bucket": bucket}
                                     for name, bucket in buckets])

    def gh_pr_view(self, number: int) -> dict:
        return next(pr for pr in self.gh()["prs"] if pr["number"] == number)

    def calls(self, group: str, sub: str) -> list[list[str]]:
        return [argv for argv in self.gh_calls() if argv[:2] == [group, sub]]

    def fresh(self) -> None:
        """A new disposable repository pair, for one subtest."""
        self.tearDown()
        self.setUp()

    def pull_main(self) -> None:
        """``git switch main && git pull`` by hand."""
        self.git("switch", "-q", "main")
        self.git("fetch", "-q", "origin")
        self.git("merge", "-q", "--ff-only", "origin/main")

    # -- lifecycle shortcuts ------------------------------------------------------------------

    def open_pr(self) -> int:
        """Bind, approve the plan on the branch, and let the next preflight
        open the Draft PR."""
        self.bind()
        self.approve()
        outcome = self.preflight()
        self.assertIsInstance(outcome, mb.Proceed)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        return self.record()["pr"]["number"]

    def to_ready(self) -> str:
        """Through acceptance and green checks to ``READY``; returns ``A``."""
        number = self.open_pr()
        a = self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(self.record()["state"], mb.READY)
        return a

    def to_pr_planned_without_pr(self) -> None:
        """A crash after PR creation step 2 (``PR_PLANNED`` written, the
        branch pushed) and before ``gh pr create``."""
        self.bind()
        self.approve()
        with mock.patch.object(forge_mod.GhForge, "create_draft_pr", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertEqual(self.record()["state"], mb.PR_PLANNED)

    def events_named(self, name: str) -> list[dict]:
        return [e for e in self.events() if e["event"] == name]

    def snapshot(self) -> tuple:
        path = self.rt / mb.record_rel(self.key, WID)
        return (path.read_bytes() if path.exists() else None, len(self.events()))


# ---------------------------------------------------------------------------
# The outcome matrix, pinned (TBR-R6-001, TBR-R7-002, TBR-R7-004).
# ---------------------------------------------------------------------------

#: ``(state, split, side, cell)``. ``side`` is ``branch``, ``trunk``, or
#: ``trunk_gone`` (from trunk, with ``milestone/<id>`` deleted locally and on
#: the remote). A split state's rows partition it.
MATRIX = [
    (mb.BRANCH_PLANNED, None, "branch", "planned_branch"),
    (mb.BRANCH_PLANNED, None, "trunk", "planned_trunk"),
    (mb.BRANCH_BOUND, "condition_true", "branch", "bound_true_branch"),
    (mb.BRANCH_BOUND, "condition_true", "trunk", "bound_true_trunk"),
    (mb.BRANCH_BOUND, "condition_true", "trunk_gone", "bound_true_gone"),
    (mb.BRANCH_BOUND, "condition_false_incomplete", "branch", "bound_incomplete_branch"),
    (mb.BRANCH_BOUND, "condition_false_incomplete", "trunk", "bound_incomplete_trunk"),
    (mb.BRANCH_BOUND, "condition_false_incomplete", "trunk_gone", "bound_incomplete_gone"),
    (mb.BRANCH_BOUND, "condition_false_complete", "branch", "bound_complete_branch"),
    (mb.BRANCH_BOUND, "condition_false_complete", "trunk", "bound_complete_trunk"),
    (mb.BRANCH_BOUND, "condition_false_complete", "trunk_gone", "bound_complete_gone"),
    (mb.PR_PLANNED, None, "branch", "pr_planned_branch"),
    (mb.PR_PLANNED, "excluded_open", "trunk", "pr_planned_excluded_open_trunk"),
    (mb.PR_PLANNED, "many_open", "trunk", "pr_planned_many_open_trunk"),
    (mb.PR_PLANNED, "many_merged", "trunk", "pr_planned_many_merged_trunk"),
    (mb.PR_PLANNED, "merged", "trunk", "pr_planned_merged_trunk"),
    (mb.PR_PLANNED, "open", "trunk", "pr_planned_open_trunk"),
    (mb.PR_PLANNED, "closed_unmerged", "trunk", "pr_planned_closed_trunk"),
    (mb.PR_PLANNED, "none", "trunk", "pr_planned_none_trunk"),
    (mb.PR_PLANNED, "none", "trunk_gone", "pr_planned_none_gone"),
    (mb.PR_OPEN, None, "branch", "pr_open_branch"),
    (mb.PR_OPEN, None, "trunk", "pr_open_trunk"),
    (mb.READY, None, "branch", "ready_branch"),
    (mb.READY, None, "trunk", "ready_trunk"),
    (mb.MERGED, None, "branch", "merged_branch"),
    (mb.MERGED, None, "trunk", "merged_trunk"),
    (mb.PR_CLOSED_UNMERGED, None, "branch", "closed_unmerged_branch"),
    (mb.PR_CLOSED_UNMERGED, None, "trunk", "closed_unmerged_trunk"),
    (mb.MERGED_BEFORE_ACCEPTANCE, None, "branch", "mba_branch"),
    (mb.MERGED_BEFORE_ACCEPTANCE, None, "trunk", "mba_trunk"),
    (mb.CLOSED, None, "branch", "closed_branch"),
    (mb.CLOSED, None, "trunk", "closed_trunk"),
    (mb.MERGED_REWRITTEN, None, "branch", "rewritten_branch"),
    (mb.MERGED_REWRITTEN, None, "trunk", "rewritten_trunk"),
    (mb.ABANDONED, None, "branch", "abandoned_branch"),
    (mb.ABANDONED, None, "trunk", "abandoned_trunk"),
]

DISCOVERY_ROWS = {"excluded_open", "many_open", "many_merged", "merged", "open", "closed_unmerged", "none"}
BRANCH_BOUND_SPLIT = {"condition_true", "condition_false_incomplete", "condition_false_complete"}


class MatrixShapeTest(unittest.TestCase):
    def test_every_state_has_a_row_from_each_side(self) -> None:
        for side in ("branch", "trunk"):
            with self.subTest(side=side):
                self.assertEqual({state for state, _, s, _ in MATRIX if s == side}, set(mb.STATES))

    def test_split_rows_partition_their_state(self) -> None:
        for side in ("branch", "trunk"):
            self.assertEqual({split for state, split, s, _ in MATRIX if state == mb.BRANCH_BOUND and s == side},
                             BRANCH_BOUND_SPLIT)
        self.assertEqual({split for state, split, s, _ in MATRIX if state == mb.PR_PLANNED and s == "trunk"},
                         DISCOVERY_ROWS)
        cells = [cell for *_, cell in MATRIX]
        self.assertEqual(len(cells), len(set(cells)))

    def test_every_cell_has_a_fixture(self) -> None:
        for *_, cell in MATRIX:
            self.assertTrue(hasattr(OutcomeMatrixTest, f"test_cell_{cell}"), cell)

    def test_every_trunk_cell_that_needs_the_tip_has_a_branch_gone_row(self) -> None:
        gone = {(state, split) for state, split, side, _ in MATRIX if side == "trunk_gone"}
        self.assertEqual(gone, {(mb.BRANCH_BOUND, s) for s in BRANCH_BOUND_SPLIT} | {(mb.PR_PLANNED, "none")})


class OutcomeMatrixTest(_Lifecycle):
    """One fixture per cell: one preflight from the cell's state, asserting
    the cell's writer, gate or refusal, and the exit it names."""

    def gone(self) -> str:
        self.git("switch", "-q", "main")
        tip = mb._last_tip(self.record())
        self.git("branch", "-D", BRANCH)
        if self.git("ls-remote", "origin", f"refs/heads/{BRANCH}"):
            self.git("push", "-q", "origin", "--delete", BRANCH)
        return tip

    # BRANCH_PLANNED
    def test_cell_planned_branch(self) -> None:
        self.crash_after_switch()
        self.assertEqual(self.preflight().action, "completed")
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)

    def test_cell_planned_trunk(self) -> None:
        self.crash_before_switch()
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertBoundOn()
        self.assertTrue(self.events_named("bound"))

    # BRANCH_BOUND, creation condition true
    def test_cell_bound_true_branch(self) -> None:
        self.bind()
        self.approve()
        self.assertEqual(self.preflight().action, "pr_created")
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        events = [e["event"] for e in self.events()]
        self.assertLess(events.index("pr_planned"), events.index("pr_created"))

    def test_cell_bound_true_trunk(self) -> None:
        self.bind()
        self.approve()
        self.git("switch", "-q", "main")
        before = self.snapshot()
        error = self.assertRefuses(BRANCH, f"git switch {BRANCH}")
        self.assertNotIn("--abandon", error.message)
        self.assertEqual(self.snapshot(), before)

    def test_cell_bound_true_gone(self) -> None:
        self.bind()
        self.approve()
        tip = self.gone()
        self.assertRefuses(f"git branch {BRANCH} {tip}")

    # BRANCH_BOUND, condition false, not MILESTONE_COMPLETE
    def test_cell_bound_incomplete_branch(self) -> None:
        self.bind()
        outcome = self.preflight()
        self.assertIsInstance(outcome, mb.Proceed)
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.assertEqual(self.calls("pr", "create"), [])

    def test_cell_bound_incomplete_trunk(self) -> None:
        self.bind()
        self.git("switch", "-q", "main")
        self.assertRefuses(BRANCH, f"git switch {BRANCH}")
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)

    def test_cell_bound_incomplete_gone(self) -> None:
        self.bind()
        self.approve()
        self.preflight()
        self.seed(mb.BRANCH_BOUND, pr=None)
        tip = self.gone()
        self.assertRefuses(f"git branch {BRANCH} {tip}")

    # BRANCH_BOUND, condition false, MILESTONE_COMPLETE: the PR-less close-out
    def complete_on_trunk_without_pr(self) -> str:
        self.bind()
        self.approve()
        a = self.accept()
        self.push(BRANCH)
        self.git("push", "-q", "origin", f"{BRANCH}:main")  # a human fast-forwards trunk to A
        return a

    def test_cell_bound_complete_branch(self) -> None:
        a = self.complete_on_trunk_without_pr()
        self.assertEqual(self.preflight().action, "closed_out")
        record = self.record()
        self.assertEqual((record["state"], record["pr"], record["merged_head"]), (mb.CLOSED, None, a))
        self.assertEqual(self.head(), gitrepo.HeadState("main", a))
        self.assertEqual(self.calls("pr", "create"), [])

    def test_cell_bound_complete_trunk(self) -> None:
        a = self.complete_on_trunk_without_pr()
        self.pull_main()
        self.assertEqual(self.preflight().action, "trunk_start")
        self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, a))
        self.assertEqual(self.calls("pr", "create"), [])

    def test_cell_bound_complete_gone(self) -> None:
        self.complete_on_trunk_without_pr()
        tip = self.gone()
        self.assertRefuses(f"git branch {BRANCH} {tip}")
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)

    # PR_PLANNED
    def test_cell_pr_planned_branch(self) -> None:
        self.to_pr_planned_without_pr()
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.assertEqual(len(self.calls("pr", "create")), 1)

    def test_cell_pr_planned_excluded_open_trunk(self) -> None:
        number = self.open_pr()
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.gh_edit(number, state="OPEN")
        self.assertRefuses(f"#{number}", "superseded", "close")
        self.assertEqual(self.record()["state"], mb.PR_PLANNED)
        self.git("switch", "-q", "main")
        error = self.assertRefuses(f"#{number}", "superseded", "close")
        self.assertNotIn("git switch", error.message)

    def test_cell_pr_planned_many_open_trunk(self) -> None:
        self.to_pr_planned_without_pr()
        self.gh_pr(5)
        self.gh_pr(6)
        self.git("switch", "-q", "main")
        error = self.assertRefuses("#5", "#6", "close the extras")
        self.assertNotIn("git switch", error.message)

    def test_cell_pr_planned_many_merged_trunk(self) -> None:
        self.to_pr_planned_without_pr()
        self.gh_pr(5, state="MERGED")
        self.gh_pr(6, state="MERGED")
        self.git("switch", "-q", "main")
        error = self.assertRefuses("#5", "#6", "lost-runtime-root recovery")
        self.assertNotIn("git switch", error.message)

    def test_cell_pr_planned_merged_trunk(self) -> None:
        self.bind()
        self.approve()
        self.accept()
        with mock.patch.object(forge_mod.GhForge, "create_draft_pr", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.gh_pr(5, head_oid=self.origin_ref(f"refs/heads/{BRANCH}"))
        self.human_merge(5)
        self.pull_main()
        self.assertEqual(self.preflight().action, "trunk_start")
        self.assertEqual((self.record()["state"], self.record()["pr"]["number"]), (mb.CLOSED, 5))
        self.assertTrue(self.events_named("merged"))

    def test_cell_pr_planned_open_trunk(self) -> None:
        self.to_pr_planned_without_pr()
        self.gh_pr(5)
        self.git("switch", "-q", "main")
        before = self.snapshot()
        self.assertRefuses(BRANCH, "#5 is open", f"git switch {BRANCH}")
        self.assertEqual(self.snapshot(), before)

    def test_cell_pr_planned_closed_trunk(self) -> None:
        self.to_pr_planned_without_pr()
        self.gh_pr(5, state="CLOSED")
        self.git("switch", "-q", "main")
        self.assertRefuses(BRANCH, "--new-pr", "--abandon")
        self.assertEqual((self.record()["state"], self.record()["pr"]["number"]), (mb.PR_CLOSED_UNMERGED, 5))

    def test_cell_pr_planned_none_trunk(self) -> None:
        self.to_pr_planned_without_pr()
        self.git("switch", "-q", "main")
        before = self.snapshot()
        self.assertRefuses(BRANCH, f"git switch {BRANCH}")
        self.assertEqual(self.snapshot(), before)

    def test_cell_pr_planned_none_gone(self) -> None:
        self.to_pr_planned_without_pr()
        tip = self.gone()
        self.assertRefuses(f"git branch {BRANCH} {tip}")

    # PR_OPEN
    def test_cell_pr_open_branch(self) -> None:
        self.open_pr()
        views = len(self.calls("pr", "view"))
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertGreater(len(self.calls("pr", "view")), views)  # re-verified

    def test_cell_pr_open_trunk(self) -> None:
        self.open_pr()
        self.git("switch", "-q", "main")
        self.assertRefuses(BRANCH, "is open", f"git switch {BRANCH}")
        self.assertEqual(self.record()["state"], mb.PR_OPEN)

    # READY
    def test_cell_ready_branch(self) -> None:
        self.to_ready()
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertIn("Create a merge commit", gate.exits[0])

    def test_cell_ready_trunk(self) -> None:
        a = self.to_ready()
        self.human_merge(self.record()["pr"]["number"])
        self.pull_main()
        self.assertEqual(self.preflight().action, "trunk_start")
        self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, a))

    # MERGED
    def test_cell_merged_branch(self) -> None:
        self.to_ready()
        self.human_merge(self.record()["pr"]["number"])
        (self.clone / "README.md").write_text("dirty\n")
        self.assertGate(self.preflight(), mb.GATE_DIRTY_TREE)
        self.assertEqual(self.record()["state"], mb.MERGED)
        self.git("checkout", "--", "README.md")
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(self.record()["state"], mb.CLOSED)

    def test_cell_merged_trunk(self) -> None:
        self.to_ready()
        self.human_merge(self.record()["pr"]["number"])
        real = mb.write_record

        def write(root, key, record, *, now):
            if record["state"] == mb.CLOSED:
                raise KeyboardInterrupt  # a crash in step 3, after the switch
            return real(root, key, record, now=now)

        with mock.patch.object(mb, "write_record", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertEqual((self.record()["state"], self.head().branch), (mb.MERGED, "main"))
        self.assertEqual(self.preflight().action, "trunk_start")
        self.assertEqual(self.record()["state"], mb.CLOSED)

    # PR_CLOSED_UNMERGED
    def closed_unmerged(self) -> int:
        number = self.open_pr()
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        return number

    def test_cell_closed_unmerged_branch(self) -> None:
        number = self.closed_unmerged()
        gate = self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        self.assertEqual(len(gate.exits), 3)
        self.assertIn(f"reopen pull request #{number}", gate.exits[0])
        self.assertIn("--new-pr", gate.exits[1])
        self.assertIn("--abandon", gate.exits[2])

    def test_cell_closed_unmerged_trunk(self) -> None:
        number = self.closed_unmerged()
        self.git("switch", "-q", "main")
        self.assertRefuses(BRANCH, f"reopen pull request #{number}", "--new-pr", "--abandon")

    # MERGED_BEFORE_ACCEPTANCE
    def merged_before_acceptance(self) -> None:
        number = self.open_pr()
        self.human_merge(number)
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)

    def test_cell_mba_branch(self) -> None:
        self.merged_before_acceptance()
        gate = self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        self.assertIn("--new-pr", gate.exits[0])

    def test_cell_mba_trunk(self) -> None:
        self.merged_before_acceptance()
        self.pull_main()
        self.assertRefuses(BRANCH, "before acceptance", "--new-pr")

    # CLOSED
    def closed(self) -> None:
        self.to_ready()
        self.human_merge(self.record()["pr"]["number"])
        self.assertEqual(self.preflight().action, "closed_out")

    def test_cell_closed_branch(self) -> None:
        self.closed()
        self.git("switch", "-q", BRANCH)
        self.assertGate(self.preflight(), mb.GATE_SWITCH_TO_TRUNK)

    def test_cell_closed_trunk(self) -> None:
        self.closed()
        self.assertEqual(self.preflight().action, "trunk_start")

    # MERGED_REWRITTEN
    def squashed(self) -> None:
        self.to_ready()
        self.human_merge(self.record()["pr"]["number"], how="squash")

    def test_cell_rewritten_branch(self) -> None:
        self.squashed()
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_METHOD_REWROTE_HISTORY)
        self.assertIn("git switch main", gate.exits)
        self.assertEqual(self.head().branch, BRANCH)  # no automatic switch
        self.assertGate(self.preflight(), mb.GATE_SWITCH_TO_TRUNK)

    def test_cell_rewritten_trunk(self) -> None:
        self.squashed()
        self.pull_main()
        self.assertGate(self.preflight(), mb.GATE_MERGE_METHOD_REWROTE_HISTORY)
        self.assertEqual(self.record()["state"], mb.MERGED_REWRITTEN)
        self.assertEqual(self.preflight().action, "trunk_start")

    # ABANDONED
    def abandoned(self) -> None:
        self.closed_unmerged()
        mb.acknowledge(self.ctx, WID, mb.ABANDON)

    def test_cell_abandoned_branch(self) -> None:
        self.abandoned()
        self.assertGate(self.preflight(), mb.GATE_SWITCH_TO_TRUNK)

    def test_cell_abandoned_trunk(self) -> None:
        self.abandoned()
        self.git("switch", "-q", "main")
        self.assertEqual(self.preflight().action, "trunk_start")


# ---------------------------------------------------------------------------
# PR creation, discovery and reuse.
# ---------------------------------------------------------------------------


class CreationTest(_Lifecycle):
    def test_a_draft_pr_is_created_only_after_the_first_branch_commit(self) -> None:
        self.bind()
        self.preflight()
        self.assertEqual(self.calls("pr", "create"), [])
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        approval = self.approve()
        outcome = self.preflight()
        self.assertEqual(outcome.action, "pr_created")
        self.assertEqual(len(self.calls("pr", "create")), 1)
        pr = self.gh_pr_view(self.record()["pr"]["number"])
        self.assertTrue(pr["isDraft"])
        self.assertEqual((pr["title"], pr["headRefName"], pr["baseRefName"]), (WID, BRANCH, "main"))
        self.assertIn(f"<!-- workflow-controller: work_item={WID} -->", pr["body"])
        self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), approval)
        push = self.record()["last_observation"]["push"]
        self.assertEqual(push, {"intent": approval, "outcome": approval})

    def test_a_crash_between_create_and_the_record_write_adopts_without_a_duplicate(self) -> None:
        self.bind()
        self.approve()
        real = mb.write_record

        def write(root, key, record, *, now):
            if record["state"] == mb.PR_OPEN:
                raise KeyboardInterrupt
            return real(root, key, record, now=now)

        with mock.patch.object(mb, "write_record", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertEqual(self.record()["state"], mb.PR_PLANNED)
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual((self.record()["state"], self.record()["pr"]["number"]), (mb.PR_OPEN, 1))
        self.assertEqual(len(self.calls("pr", "create")), 1)
        self.assertTrue(self.events_named("pr_adopted"))

    def test_two_open_or_two_merged_prs_refuse(self) -> None:
        self.to_pr_planned_without_pr()
        self.gh_pr(5)
        self.gh_pr(6)
        self.assertRefuses("#5", "#6", "close the extras")
        self.gh_edit(5, state="MERGED")
        self.gh_edit(6, state="MERGED")
        self.assertRefuses("#5", "#6", "lost-runtime-root recovery")
        self.assertEqual(self.calls("pr", "create"), [])

    def test_a_recorded_pr_that_changed_identity_refuses(self) -> None:
        for field, value in (("isCrossRepository", True), ("baseRefName", "develop"),
                             ("headRefName", "someone/else")):
            with self.subTest(field=field):
                self.fresh()
                number = self.open_pr()
                self.gh_edit(number, **{field: value})
                self.assertRefuses(f"#{number} is no longer {WID}'s")
                self.assertEqual(self.record()["state"], mb.PR_OPEN)

    def test_a_reopened_superseded_pr_refuses_until_closed(self) -> None:
        number = self.open_pr()
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertEqual(self.record()["superseded_prs"], [number])
        self.gh_edit(number, state="OPEN")  # a human reopens the superseded PR
        self.assertRefuses(f"#{number}", "superseded", "close")
        self.assertEqual(len(self.calls("pr", "create")), 1)
        self.gh_edit(number, state="CLOSED")
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(len(self.calls("pr", "create")), 2)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.assertNotEqual(self.record()["pr"]["number"], number)

    def test_fake_gh_refuses_a_second_open_pr_for_the_same_head_and_base(self) -> None:
        self.open_pr()
        gh = forge_mod.GhForge(self.gh()["repository"], gitrepo.subprocess_runner(self.gh_env))
        with self.assertRaises(ForgeUndecidableError) as caught:
            gh.create_draft_pr(BRANCH, "main", WID, "body")
        self.assertIn("already exists", str(caught.exception.evidence))

    def test_own_pr_closed_before_the_record_write_then_new_pr_continues(self) -> None:
        self.bind()
        self.approve()
        real = mb.write_record

        def write(root, key, record, *, now):
            if record["state"] == mb.PR_OPEN:
                raise KeyboardInterrupt
            return real(root, key, record, now=now)

        with mock.patch.object(mb, "write_record", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.gh_edit(1, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        self.assertEqual((self.record()["state"], self.record()["pr"]["number"]), (mb.PR_CLOSED_UNMERGED, 1))
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual((self.record()["state"], self.record()["pr"]["number"]), (mb.PR_OPEN, 2))

    def test_trunk_fast_forwarded_after_pr_planned_returns_to_branch_bound(self) -> None:
        self.to_pr_planned_without_pr()
        self.git("push", "-q", "origin", f"{BRANCH}:main")
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.assertEqual(self.calls("pr", "create"), [])

    def test_rebinding_an_abandoned_id_creates_one_new_pr(self) -> None:
        for crash in (False, True):
            with self.subTest(crash_between_rename_and_create=crash):
                self.fresh()
                self._rebind_through_to_the_first_pr(crash)

    def _rebind_through_to_the_first_pr(self, crash: bool) -> None:
        old = self.open_pr()
        self.gh_edit(old, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.git("switch", "-q", "main")
        self.git("branch", "-D", BRANCH)
        self.git("push", "-q", "origin", "--delete", BRANCH)
        self.plan()
        if crash:
            with mock.patch.object(runtime, "create_json", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.preflight()
            self.assertIsNone(self.record())
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(self.record()["binding_generation"], 2)
        creates = len(self.calls("pr", "create"))
        self.approve()
        self.assertEqual(self.preflight().action, "pr_created")
        self.assertEqual(len(self.calls("pr", "create")), creates + 1)
        self.assertNotEqual(self.record()["pr"]["number"], old)


# ---------------------------------------------------------------------------
# Re-verification, drift, readiness, the merge gate.
# ---------------------------------------------------------------------------


class ReadinessTest(_Lifecycle):
    def test_a_human_readied_pr_is_tolerated_and_never_flipped_back(self) -> None:
        number = self.open_pr()
        self.gh_edit(number, isDraft=False)
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertFalse(self.record()["pr"]["is_draft"])
        self.accept()
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(self.calls("pr", "ready"), [])
        self.assertFalse(self.gh_pr_view(number)["isDraft"])

    def test_a_closed_unmerged_pr_gates(self) -> None:
        number = self.open_pr()
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        self.assertEqual(self.record()["state"], mb.PR_CLOSED_UNMERGED)
        self.assertEqual(len(self.calls("pr", "create")), 1)  # never recreated automatically

    def test_drift_is_informational_before_completion_and_gates_at_readiness(self) -> None:
        number = self.open_pr()
        self.trunk_commit()
        self.assertIsInstance(self.preflight(), mb.Proceed)
        observation = self.record()["last_observation"]
        self.assertEqual((observation["fresh"], observation["behind"]), (False, 1))
        self.accept()
        self.set_checks(number, ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)
        self.assertIn("1 commit(s)", gate.message)
        self.assertIn("Create a merge commit", gate.message)
        self.assertEqual(self.calls("pr", "ready"), [])

    def test_checks_pending_failing_and_cancelled(self) -> None:
        number = self.open_pr()
        self.accept()
        gate = self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)  # no checks reported yet
        self.assertIn("no checks reported", gate.message)
        cases = [
            ((("ci", "pending"),), mb.GATE_CHECKS_PENDING),
            ((("ci", "fail"), ("lint", "pass")), mb.GATE_CHECKS_FAILING),
            ((("ci", "cancel"), ("lint", "pass")), mb.GATE_CHECKS_CANCELLED),
            ((("ci", "fail"), ("a", "pending"), ("b", "cancel")), mb.GATE_CHECKS_FAILING),
            ((("a", "pending"), ("b", "cancel")), mb.GATE_CHECKS_PENDING),
        ]
        for checks, code in cases:
            with self.subTest(checks=checks):
                self.set_checks(number, *checks)
                gate = self.assertGate(self.preflight(), code)
                if code == mb.GATE_CHECKS_CANCELLED:
                    self.assertIn("ci", gate.message)
        self.set_checks(number, ("ci", "pass"), ("docs", "skipping"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)

    def test_readiness_marks_ready_exactly_once_and_ready_only_gates_the_merge(self) -> None:
        a = self.to_ready()
        record = self.record()
        self.assertEqual((record["accepted_head"], record["pr"]["is_draft"]), (a, False))
        self.assertEqual(len(self.calls("pr", "ready")), 1)
        for _ in range(2):
            gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
            self.assertIn(f"#{record['pr']['number']}", gate.message)
        self.assertEqual(len(self.calls("pr", "ready")), 1)
        self.assertFalse([argv for argv in self.gh_calls() if "merge" in argv])

    def test_a_ready_pr_is_never_pushed_past_the_acceptance_commit(self) -> None:
        a = self.to_ready()
        extra = self.commit_file("late.txt")
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertIn(extra, gate.message)
        self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), a)

    def test_a_commit_after_acceptance_gates_post_acceptance_commits(self) -> None:
        number = self.open_pr()
        self.accept()
        extra = self.commit_file("late.txt")
        self.set_checks(number, ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_POST_ACCEPTANCE_COMMITS)
        self.assertIn(extra, gate.message)
        self.assertEqual(self.calls("pr", "ready"), [])

    def test_an_untrailered_completion_refuses_readiness(self) -> None:
        self.open_pr()
        untrailered = self.accept(trailer=False)
        self.assertRefuses("no acceptance commit", untrailered)

    def test_a_child_acceptance_before_the_parent_is_not_the_acceptance_commit(self) -> None:
        number = self.open_pr()
        self.set_item("wi-child", phase="MILESTONE_COMPLETE", parent_work_item_id=WID)
        commit_all(self.clone, trailer_message("Accept child", ("Workflow-Work-Item", "wi-child")))
        self.assertIsInstance(self.preflight(), mb.Proceed)
        a = self.accept()
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(self.record()["accepted_head"], a)
        self.assertEqual(len(self.calls("pr", "create")), 1)


# ---------------------------------------------------------------------------
# The merged-PR handling and close-out.
# ---------------------------------------------------------------------------


class CloseOutTest(_Lifecycle):
    def test_close_out_after_a_merge_switches_to_main_and_fast_forwards(self) -> None:
        a = self.to_ready()
        self.human_merge(self.record()["pr"]["number"])
        self.assertEqual(self.preflight().action, "closed_out")
        record = self.record()
        self.assertEqual((record["state"], record["merged_head"], record["accepted_head"]), (mb.CLOSED, a, a))
        self.assertEqual(self.head(), gitrepo.HeadState("main", self.origin_ref("refs/heads/main")))
        self.assertIn(BRANCH, self.git("branch", "--list", BRANCH))  # never deleted

    def test_a_merge_from_pr_open_after_acceptance_converges(self) -> None:
        number = self.open_pr()
        self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.human_merge(number)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(self.record()["state"], mb.CLOSED)

    def test_a_merge_before_acceptance_gates_every_later_step(self) -> None:
        number = self.open_pr()
        self.human_merge(number)
        for _ in range(2):
            self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        self.commit_file("more.txt")
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        self.assertEqual(self.record()["state"], mb.MERGED_BEFORE_ACCEPTANCE)

    def test_a_squash_merge_writes_merged_rewritten_without_a_switch(self) -> None:
        self.to_ready()
        self.human_merge(self.record()["pr"]["number"], how="squash")
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_METHOD_REWROTE_HISTORY)
        self.assertEqual(self.record()["state"], mb.MERGED_REWRITTEN)
        self.assertTrue(self.record()["rewrite_gate_shown"])
        self.assertIn(self.record()["merge_commit"], gate.message)
        self.assertEqual(self.head().branch, BRANCH)

    def test_a_dirty_tree_at_close_out_gates(self) -> None:
        self.to_ready()
        self.human_merge(self.record()["pr"]["number"])
        (self.clone / "README.md").write_text("dirty\n")
        self.assertGate(self.preflight(), mb.GATE_DIRTY_TREE)
        self.assertEqual((self.record()["state"], self.head().branch), (mb.MERGED, BRANCH))

    def test_the_unmerged_commit_gate_clears_after_moving_the_commit(self) -> None:
        a = self.to_ready()
        self.human_merge(self.record()["pr"]["number"])
        extra = self.commit_file("unmerged.txt")
        gate = self.assertGate(self.preflight(), mb.GATE_UNMERGED_COMMITS)
        self.assertIn(extra, gate.message)
        self.git("branch", "keep-unmerged", BRANCH)
        self.git("reset", "-q", "--keep", a)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(self.record()["state"], mb.CLOSED)

    def test_an_untrailered_completion_merged_is_merged_before_acceptance(self) -> None:
        for side in ("branch", "trunk"):
            with self.subTest(side=side):
                self.fresh()
                self._untrailered_merge(side)

    def _untrailered_merge(self, side: str) -> None:
        """A human sets the phase by hand on the branch in their own clone,
        merges, and deletes the branch on GitHub: ``H`` is absent locally
        and is fetched through ``refs/pull/<n>/head``."""
        number = self.open_pr()
        human = self.human()
        run(["git", "switch", "-q", "-c", BRANCH, f"origin/{BRANCH}"], cwd=human)
        import json
        state_path = human / "docs/ai-workflow/WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"][WID]["phase"] = "MILESTONE_COMPLETE"
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        h = commit_all(human, "Set it complete by hand")
        run(["git", "push", "-q", "origin", BRANCH], cwd=human)
        self.human_merge(number)
        run(["git", "--git-dir", str(self.origin), "update-ref", f"refs/pull/{number}/head", h])
        run(["git", "push", "-q", "origin", "--delete", BRANCH], cwd=human)
        self.assertIsNone(gitrepo.ref_commit(self.clone, h))
        if side == "branch":
            gate = self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
            self.assertIn(h, gate.message)
        else:
            self.git("switch", "-q", "main")
            self.assertRefuses("before acceptance", h)
        self.assertEqual(self.record()["state"], mb.MERGED_BEFORE_ACCEPTANCE)
        self.assertEqual(self.record()["untrailered_completion"], h)


class CloseOutFromTrunkTest(_Lifecycle):
    def test_from_pr_open_and_from_ready(self) -> None:
        for ready in (False, True):
            with self.subTest(ready=ready):
                self.fresh()
                if ready:
                    a = self.to_ready()
                else:
                    self.open_pr()
                    a = self.accept()
                    self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
                self.human_merge(self.record()["pr"]["number"])
                self.pull_main()
                self.assertEqual(self.preflight().action, "trunk_start")
                record = self.record()
                self.assertEqual((record["state"], record["merged_head"]), (mb.CLOSED, a))
                self.assertEqual([e["event"] for e in self.events()][-2:], ["merged", "closed"])
                self.assertEqual(self.head().branch, "main")

    def test_an_open_pr_refuses_and_a_closed_one_writes_pr_closed_unmerged(self) -> None:
        number = self.open_pr()
        self.git("switch", "-q", "main")
        self.assertRefuses(BRANCH, f"git switch {BRANCH}")
        self.gh_edit(number, state="CLOSED")
        self.assertRefuses(BRANCH, "--new-pr", "--abandon")
        self.assertEqual(self.record()["state"], mb.PR_CLOSED_UNMERGED)

    def test_a_local_commit_not_in_the_merged_head_gates(self) -> None:
        self.to_ready()
        extra = self.commit_file("unmerged.txt")
        self.human_merge(self.record()["pr"]["number"])
        self.pull_main()
        gate = self.assertGate(self.preflight(), mb.GATE_UNMERGED_COMMITS)
        self.assertIn(extra, gate.message)
        self.assertIn(f"git branch -D {BRANCH}", gate.message)
        self.assertEqual(self.record()["state"], mb.MERGED)

    def test_a_second_worktree_on_the_branch_refuses_and_writes_nothing(self) -> None:
        number = self.open_pr()
        self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.human_merge(number)
        self.git("switch", "-q", "main")
        self.git("worktree", "add", "-q", str(self.tmp / "second"), BRANCH)
        before = self.snapshot()
        self.assertRefuses("second")
        self.assertEqual(self.snapshot(), before)
        self.gh_edit(number, state="CLOSED", mergedAt=None)
        self.seed(mb.PR_CLOSED_UNMERGED)
        before = self.snapshot()
        for disposition in mb.DISPOSITIONS:
            with self.assertRaises(BranchBindingError) as caught:
                mb.acknowledge(self.ctx, WID, disposition)
            self.assertIn("second", caught.exception.message)
        self.assertEqual(self.snapshot(), before)


# ---------------------------------------------------------------------------
# The PR-less close-out.
# ---------------------------------------------------------------------------


class PrLessCloseOutTest(_Lifecycle):
    def closed_then_new_pr_then_trunk_at_a(self) -> str:
        number = self.open_pr()
        a = self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.git("push", "-q", "origin", f"{a}:refs/heads/main")  # trunk fast-forwarded to A
        return a

    def test_from_the_branch(self) -> None:
        a = self.closed_then_new_pr_then_trunk_at_a()
        creates = len(self.calls("pr", "create"))
        self.assertEqual(self.preflight().action, "closed_out")
        record = self.record()
        self.assertEqual((record["state"], record["pr"], record["merged_head"]), (mb.CLOSED, None, a))
        self.assertEqual([e["event"] for e in self.events()][-2:], ["merged", "closed"])
        self.assertEqual(self.head(), gitrepo.HeadState("main", a))
        self.assertEqual(len(self.calls("pr", "create")), creates)

    def test_from_trunk_and_with_the_branch_gone(self) -> None:
        a = self.closed_then_new_pr_then_trunk_at_a()
        self.pull_main()
        self.assertEqual(self.preflight().action, "trunk_start")
        self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, a))
        self.fresh()
        self.closed_then_new_pr_then_trunk_at_a()
        self.pull_main()
        tip = mb._last_tip(self.record())
        self.git("branch", "-D", BRANCH)
        self.git("push", "-q", "origin", "--delete", BRANCH)
        self.assertRefuses(f"git branch {BRANCH} {tip}")
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)

    def test_a_pr_planned_record_with_no_pr_converges_from_both_sides(self) -> None:
        for side in ("branch", "trunk"):
            with self.subTest(side=side):
                self.fresh()
                self.bind()
                self.approve()
                a = self.accept()
                with mock.patch.object(forge_mod.GhForge, "create_draft_pr", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        self.preflight()
                self.git("push", "-q", "origin", f"{a}:refs/heads/main")
                if side == "trunk":
                    self.pull_main()
                self.assertIn(self.preflight().action, ("closed_out", "trunk_start"))
                self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, a))
                self.assertEqual(self.calls("pr", "create"), [])

    def test_an_untrailered_completion_on_trunk_leaves_abandon_alone(self) -> None:
        self.bind()
        self.approve()
        self.preflight()
        number = self.record()["pr"]["number"]
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        untrailered = self.accept(trailer=False)
        self.push(BRANCH)
        self.git("push", "-q", "origin", f"{untrailered}:refs/heads/main")
        gate = self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        self.assertIsNone(self.record()["pr"])
        self.assertEqual(len(gate.exits), 1)
        self.assertIn("--abandon", gate.exits[0])
        self.assertIn(untrailered, gate.message)
        views = len(self.calls("pr", "view"))
        self.assertEqual(mb.acknowledge(self.ctx, WID, mb.ABANDON)["state"], mb.ABANDONED)
        self.assertEqual(len(self.calls("pr", "view")), views)  # pr is null: no re-read
        self.pull_main()
        self.assertEqual(self.preflight().action, "trunk_start")

    def test_on_trunk_but_not_complete_runs_the_worker_or_blocks(self) -> None:
        self.bind()
        self.approve()
        self.git("push", "-q", "origin", f"{BRANCH}:main")
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.pull_main()
        self.assertRefuses(BRANCH, f"git switch {BRANCH}")


# ---------------------------------------------------------------------------
# The refusal-state exits (TBR-R4-001), through the API CP8's subcommand calls.
# ---------------------------------------------------------------------------


class RefusalExitTest(_Lifecycle):
    def closed(self) -> int:
        number = self.open_pr()
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        return number

    def test_reopen_returns_to_pr_open_without_a_new_pr(self) -> None:
        number = self.closed()
        self.gh_edit(number, state="OPEN")
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual((self.record()["state"], self.record()["pr"]["number"]), (mb.PR_OPEN, number))
        self.assertEqual(len(self.calls("pr", "create")), 1)
        self.assertTrue(self.events_named("reopened"))

    def test_a_reopened_pr_whose_head_changed_refuses(self) -> None:
        number = self.closed()
        self.gh_edit(number, state="OPEN", headRefName="other")
        self.assertRefuses(f"#{number} is no longer")
        self.assertEqual(self.record()["state"], mb.PR_CLOSED_UNMERGED)

    def test_reopened_then_merged_goes_to_the_merged_handling(self) -> None:
        number = self.open_pr()
        a = self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.gh_edit(number, state="CLOSED")
        self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
        self.gh_edit(number, state="OPEN")
        self.human_merge(number)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, a))

    def test_reopened_with_the_item_gone_writes_pr_open_then_gates(self) -> None:
        number = self.closed()
        self.gh_edit(number, state="OPEN")
        self.write_state({"active_work_item_id": None, "work_items": {}})
        self.assertGate(self.preflight(), mb.GATE_BOUND_ITEM_MISSING)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)

    def test_a_reopened_pr_from_trunk_refuses_and_writes_nothing(self) -> None:
        number = self.closed()
        self.gh_edit(number, state="OPEN")
        self.git("switch", "-q", "main")
        before = self.snapshot()
        self.assertRefuses(BRANCH, f"git switch {BRANCH}")
        self.assertEqual(self.snapshot(), before)

    def test_new_pr_from_closed_unmerged_creates_exactly_one_new_pr(self) -> None:
        number = self.closed()
        record = mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertEqual((record["state"], record["superseded_prs"]), (mb.BRANCH_BOUND, [number]))
        self.assertEqual(self.preflight().action, "pr_created")
        self.assertEqual(len(self.calls("pr", "create")), 2)
        self.assertNotEqual(self.record()["pr"]["number"], number)

    def test_new_pr_after_a_merge_before_acceptance_waits_for_the_next_commit(self) -> None:
        number = self.open_pr()
        self.human_merge(number)
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertIsInstance(self.preflight(), mb.Proceed)  # the worker runs
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.assertEqual(len(self.calls("pr", "create")), 1)
        self.commit_file("next.txt")
        self.assertEqual(self.preflight().action, "pr_created")
        self.assertEqual(len(self.calls("pr", "create")), 2)
        new = self.record()["pr"]["number"]
        self.accept()
        self.set_checks(new, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)

    def test_abandon_from_closed_unmerged_then_the_trunk_starts(self) -> None:
        self.closed()
        mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertEqual(self.record()["state"], mb.ABANDONED)
        self.assertEqual(self.events_named("acknowledged")[-1]["disposition"], mb.ABANDON)
        self.git("switch", "-q", "main")
        self.assertEqual(self.preflight().action, "trunk_start")

    def test_abandon_after_a_merge_before_acceptance_refuses_naming_new_pr(self) -> None:
        number = self.open_pr()
        self.human_merge(number)
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        before = self.snapshot()
        with self.assertRaises(BranchBindingError) as caught:
            mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.assertIn("--new-pr", caught.exception.message)
        self.assertEqual(self.snapshot(), before)

    def test_new_pr_needs_an_acceptance_commit_when_trunk_records_completion(self) -> None:
        number = self.open_pr()
        self.human_merge(number)
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        # a phase set by hand and merged again: --new-pr would loop
        self.accept(trailer=False)
        self.push(BRANCH)
        self.human_merge(number)
        before = self.snapshot()
        with self.assertRaises(BranchBindingError) as caught:
            mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertIn("--abandon", caught.exception.message)
        self.assertEqual(self.snapshot(), before)

    def test_new_pr_after_an_acceptance_on_the_branch_converges(self) -> None:
        number = self.open_pr()
        self.human_merge(number)
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        a = self.accept()  # accepted on the branch outside the Controller
        self.push(BRANCH)
        self.human_merge(number)
        gate = self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        self.assertTrue(any("--new-pr" in e for e in gate.exits))
        self.assertTrue(any("--abandon" in e for e in gate.exits))
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, a))

    def test_exit_c_with_the_approval_on_remote_trunk(self) -> None:
        self.crash_before_switch()
        approval = self.approve()
        self.push("main")
        self.assertRefuses(f"git switch -c {BRANCH}")
        self.git("switch", "-q", "-c", BRANCH)
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(self.record()["state"], mb.BRANCH_BOUND)
        self.assertEqual(self.head().commit, approval)
        self.assertEqual(self.calls("pr", "create"), [])
        with self.assertRaises(BranchBindingError):
            mb.acknowledge(self.ctx, WID, mb.ABANDON)
        self.commit_file("first.txt")
        self.assertEqual(self.preflight().action, "pr_created")
        self.assertEqual(len(self.calls("pr", "create")), 1)

    def test_dispositions_refuse_outside_refusal_states_and_never_mutate(self) -> None:
        number = self.open_pr()
        spies = {name: mock.patch.object(gitrepo, name, side_effect=AssertionError(name))
                 for name in ("push_branch", "switch", "switch_at_head", "create_and_switch", "fast_forward")}
        for spy in spies.values():
            spy.start()
        try:
            before = self.snapshot()
            for disposition in mb.DISPOSITIONS:
                with self.assertRaises(BranchBindingError):
                    mb.acknowledge(self.ctx, WID, disposition)
            self.gh_edit(number, state="CLOSED")
            self.seed(mb.PR_CLOSED_UNMERGED)
            self.gh_edit(number, state="OPEN")  # reopened
            before = self.snapshot()
            for disposition in mb.DISPOSITIONS:
                with self.assertRaises(BranchBindingError):
                    mb.acknowledge(self.ctx, WID, disposition)
            self.assertEqual(self.snapshot(), before)
            self.gh_edit(number, state="CLOSED")
            mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        finally:
            for spy in spies.values():
                spy.stop()
        mutations = [argv for argv in self.gh_calls() if argv[:2] in (["pr", "create"], ["pr", "ready"])]
        self.assertEqual(len(mutations), 1)  # only open_pr's own create
        self.assertTrue(self.git("ls-remote", "origin", f"refs/heads/{BRANCH}"))


class GateTextTest(unittest.TestCase):
    def test_every_gate_has_a_human_gate_text(self) -> None:
        self.assertEqual(set(decision.BRANCH_GATE_TEXTS), set(mb.GATE_CODES))
        gate = mb.Gate(mb.GATE_CHECKS_PENDING, WID, BRANCH, "pull request #1 has no checks reported yet",
                       ("re-run the step once the checks finish",))
        human = decision.branch_human_gate("/repo", gate)
        self.assertEqual((human.work_item_id, human.phase), (WID, decision.BRANCH_GATE_PHASE))
        self.assertIn("checks_pending", human.what_is_required)
        self.assertEqual(human.safe_resume_command, gate.exits[0])
        with self.assertRaises(ValueError):
            decision.branch_human_gate("/repo", mb.Gate("nope", None, None, "x"))

    def test_selected_commands_are_unchanged(self) -> None:
        self.assertNotIn("milestone-binding", " ".join(decision.SELECTED_COMMANDS))


if __name__ == "__main__":
    unittest.main()
