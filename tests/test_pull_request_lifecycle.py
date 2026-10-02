"""``controller.milestone_branch``'s Draft PR lifecycle and completion
(``workflow-controller-trunk-branch-pr-release-orchestration`` CP7): PR
creation, discovery and reuse, re-verification, drift, readiness, the merge
gate, the merged-PR handling and close-out (from the branch, from trunk, and
PR-less), and the refusal-state exits -- over a bare origin, a clone, and the
executable fake ``gh`` (``tests/fake_gh.py``). A human merge is simulated in
a second clone of the origin, plus an edit of the fake forge's state."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fake_gh  # noqa: E402
from controller import decision, forge as forge_mod, gitrepo, milestone_branch as mb, release_txn, runtime  # noqa: E402
from controller.errors import (  # noqa: E402
    BranchBindingError, ForgeUndecidableError, GitOperationError, ReleaseTransactionError,
)
from tests.fixtures import (  # noqa: E402
    FAKE_GH_REPOSITORY, commit_all, current_head, git_clone, git_init, run, trailer_message,
)
from tests.test_milestone_branch import BRANCH, WID, _Case, policy  # noqa: E402


class _Lifecycle(_Case):
    # -- a human on GitHub, and a second clone ---------------------------------------------

    def human(self) -> Path:
        """A second clone of the origin, on the trunk at the origin's tip."""
        human = self.tmp / "human"
        if not human.exists():
            git_clone(self.origin, human)
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

    def human_merge(self, number: int, *, how: str = "merge", subject: str | None = None) -> str:
        """A human merges pull request ``number`` on GitHub ("Create a merge
        commit", a fast-forward, a squash with a legacy subject, GitHub's
        squash of the title and body (``squash_pr``, ``subject`` replacing
        the title's), or a rebase) and returns its final head."""
        head = self.origin_ref(f"refs/heads/{BRANCH}")
        human = self.human()
        if how == "merge":
            run(["git", "merge", "-q", "--no-ff", "-m", f"Merge pull request #{number}", f"origin/{BRANCH}"], cwd=human)
        elif how == "ff":
            run(["git", "merge", "-q", "--ff-only", f"origin/{BRANCH}"], cwd=human)
        elif how == "squash_pr":
            # GitHub's "Squash and merge" with its default message.
            pr = self.gh_pr_view(number)
            run(["git", "merge", "-q", "--squash", f"origin/{BRANCH}"], cwd=human)
            message = f"{subject or pr['title'] + f' (#{number})'}\n\n{pr.get('body') or ''}"
            run(["git", "commit", "-q", "--cleanup=verbatim", "-m", message], cwd=human)
        elif how == "rebase":
            run(["git", "cherry-pick", f"main..origin/{BRANCH}"], cwd=human)
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
    (mb.MERGED_SQUASHED, None, "branch", "squashed_branch"),
    (mb.MERGED_SQUASHED, None, "trunk", "squashed_trunk"),
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

    # MERGED_SQUASHED (squash mode only)
    def squash_merged(self) -> str:
        """Under a squash policy: acceptance pushed, then GitHub's "Squash and
        merge" of the pull request; returns ``A``."""
        self.write_policy(squash_policy())
        commit_all(self.clone, "Squash merges")
        self.push("main")
        number = self.open_pr()
        a = self.accept()
        self.preflight()
        self.human_merge(number, how="squash_pr")
        return a

    def test_cell_squashed_branch(self) -> None:
        self.squash_merged()
        (self.clone / "README.md").write_text("dirty\n")
        self.assertGate(self.preflight(), mb.GATE_DIRTY_TREE)
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.git("checkout", "--", "README.md")
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(self.record()["state"], mb.CLOSED)

    def test_cell_squashed_trunk(self) -> None:
        a = self.squash_merged()
        real = mb.write_record

        def write(root, key, record, *, now):
            if record["state"] == mb.CLOSED:
                raise KeyboardInterrupt  # a crash in step 3, after the switch
            return real(root, key, record, now=now)

        with mock.patch.object(mb, "write_record", side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertEqual((self.record()["state"], self.head().branch), (mb.MERGED_SQUASHED, "main"))
        self.assertEqual(self.preflight().action, "trunk_start")
        self.assertEqual((self.record()["state"], self.record()["merged_head"]), (mb.CLOSED, a))

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


# ---------------------------------------------------------------------------
# The pull request title and body in squash mode
# (workflow-controller-squash-merge-tag-versioning CP5, Design E).
# ---------------------------------------------------------------------------

TITLE = "feat: squash the milestone"
PLAN_PATH = f"PLAN-{WID}.md"
CHANGE_TYPES = {"feat": "minor", "fix": "patch", "docs": "none"}


def squash_policy(*, trigger: str = "conventional_commit") -> dict:
    data = policy()
    data["milestone_branches"]["pull_request"]["merge_method"] = "squash"
    if trigger == "conventional_commit":
        release = data["release"]
        release["trigger"] = "conventional_commit"
        del release["version_source"]
        release["change_types"] = dict(CHANGE_TYPES)
    return data


def plan_text(*titles: str) -> str:
    return "# Plan\n\n" + "".join(f"Pull request title: `{t}`\n" for t in titles) + "\nThe plan.\n"


def trailers(root: Path, body: str) -> str:
    return run(["git", "interpret-trailers", "--parse"], cwd=root, input=body).stdout


class PlanTitleTest(unittest.TestCase):
    def test_exactly_one_well_formed_line_declares_the_title(self) -> None:
        self.assertEqual(mb.plan_title(plan_text(TITLE)), (TITLE, None))
        cases = {
            "none": plan_text(),
            "two": plan_text(TITLE, TITLE),
            "backtick inside": "Pull request title: `feat: a `b` c`\n",
            "missing backtick": "Pull request title: `feat: a\n",
            "no backticks": "Pull request title: feat: a\n",
            "trailing text": "Pull request title: `feat: a` (draft)\n",
            "empty": "Pull request title: ``\n",
            "not at the line start": " Pull request title: `feat: a`\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                title, problem = mb.plan_title(text)
                self.assertIsNone(title)
                if name != "not at the line start":
                    self.assertIsNotNone(problem)

    def test_the_plans_own_header_declares_a_valid_title(self) -> None:
        root = Path(__file__).resolve().parent.parent
        text = (root / "docs/ai-workflow/CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md").read_text()
        self.assertEqual(mb.plan_title(text)[0],
                         "feat: squash merges with release versions derived from pull request titles")


class _Squash(_Lifecycle):
    policy_data = staticmethod(squash_policy)

    def setUp(self) -> None:
        super().setUp()
        self.write_policy(self.policy_data())
        self.t = commit_all(self.clone, "Squash merges")
        self.push("main")

    def plan(self, wid: str = WID, *, phase: str = "PLANNING", base: str | None = None,
             text: str = plan_text(TITLE)) -> None:
        super().plan(wid, phase=phase, base=base)
        self.set_item(wid, plan_path=PLAN_PATH)
        (self.clone / PLAN_PATH).write_text(text)

    def pr_number(self) -> int:
        return self.record()["pr"]["number"]


class SquashTitleTest(_Squash):
    def test_the_declared_title_is_read_from_heads_commit_and_validated(self) -> None:
        self.open_pr()
        self.assertEqual(mb.declared_title(self.ctx, WID), mb.DeclaredTitle(PLAN_PATH, TITLE, None))
        # Uncommitted edits of the plan's title line or of the working tree's plan_path change nothing.
        (self.clone / PLAN_PATH).write_text(plan_text("fix: another"))
        self.set_item(plan_path="elsewhere.md")
        self.assertEqual(mb.declared_title(self.ctx, WID).title, TITLE)
        self.discard_plan()
        # A committed title whose type the committed change_types lack is none.
        (self.clone / PLAN_PATH).write_text(plan_text("chore: tidy"))
        commit_all(self.clone, "retitle")
        declared = mb.declared_title(self.ctx, WID)
        self.assertIsNone(declared.title)
        self.assertIn("unknown type 'chore'", declared.problem)
        (self.clone / PLAN_PATH).write_text(plan_text("docs!: a breaking doc"))
        commit_all(self.clone, "retitle")
        self.assertIsNone(mb.declared_title(self.ctx, WID).title)

    def test_a_title_declaration_that_is_not_utf_8_is_none_and_edits_nothing(self) -> None:
        number = self.open_pr()
        (self.clone / PLAN_PATH).write_bytes(plan_text("feat: abXcd").encode().replace(b"abXcd", b"ab\xffcd"))
        commit_all(self.clone, "a non-UTF-8 title")
        declared = mb.declared_title(self.ctx, WID)
        self.assertEqual((declared.plan_path, declared.title), (PLAN_PATH, None))
        self.assertIn("not valid UTF-8", declared.problem)
        edits = len(self.calls("pr", "edit"))
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(len(self.calls("pr", "edit")), edits)
        self.assertEqual(self.gh_pr_view(number)["title"], TITLE)
        # Invalid UTF-8 elsewhere in the plan leaves a valid declaration alone.
        (self.clone / PLAN_PATH).write_bytes(plan_text("fix: amended").encode() + b"\xff\n")
        commit_all(self.clone, "a non-UTF-8 byte outside the title")
        self.assertEqual(mb.declared_title(self.ctx, WID).title, "fix: amended")

    def test_creation_uses_the_declared_title_and_the_committed_plan_path(self) -> None:
        number = self.open_pr()
        pr = self.gh_pr_view(number)
        self.assertEqual(pr["title"], TITLE)
        self.assertEqual(pr["body"], f"Milestone `{WID}`, planned in `{PLAN_PATH}`, driven by workflow-controller.\n\n"
                                     f"<!-- workflow-controller: work_item={WID} -->\n")
        self.assertEqual(trailers(self.clone, pr["body"]), "")

    def test_creation_uses_the_id_without_a_valid_declaration(self) -> None:
        for text in (plan_text(), plan_text("chore: tidy"), plan_text(TITLE, TITLE)):
            with self.subTest(text=text):
                self.fresh()
                self.bind_with(text)
                self.approve()
                self.assertEqual(self.preflight().action, "pr_created")
                self.assertEqual(self.gh_pr_view(self.pr_number())["title"], WID)

    def bind_with(self, text: str) -> None:
        self.plan(text=text)
        self.assertEqual(self.preflight().action, "bound")

    def test_a_pr_open_step_syncs_a_differing_title_and_a_restart_edits_nothing(self) -> None:
        number = self.open_pr()
        self.gh_edit(number, title="someone's title")
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(self.gh_pr_view(number)["title"], TITLE)
        self.assertEqual(len(self.calls("pr", "edit")), 1)
        self.assertNotIn("--body", self.calls("pr", "edit")[0])
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(len(self.calls("pr", "edit")), 1)
        # An amendment of the declared title reaches the pull request.
        (self.clone / PLAN_PATH).write_text(plan_text("fix: amended"))
        commit_all(self.clone, "amend the plan")
        self.assertIsInstance(self.preflight(), mb.Proceed)
        self.assertEqual(self.gh_pr_view(number)["title"], "fix: amended")

    def test_a_ready_record_is_never_edited(self) -> None:
        self.to_ready()
        number = self.pr_number()
        edits = len(self.calls("pr", "edit"))
        self.gh_edit(number, title="fix: set by a human", body="changed")
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(len(self.calls("pr", "edit")), edits)
        self.assertEqual(self.gh_pr_view(number)["title"], "fix: set by a human")

    def test_readiness_adds_the_acceptance_line_to_the_body(self) -> None:
        a = self.to_ready_squash()
        pr = self.gh_pr_view(self.pr_number())
        self.assertEqual(pr["body"], f"Milestone `{WID}`, planned in `{PLAN_PATH}`, driven by workflow-controller.\n"
                                     f"Accepted at {a} on `{BRANCH}`; squash-merged into the trunk as one commit.\n\n"
                                     f"<!-- workflow-controller: work_item={WID} -->\n")
        self.assertEqual(trailers(self.clone, pr["body"]), "")
        self.assertEqual(pr["title"], TITLE)

    def to_ready_squash(self) -> str:
        """Acceptance, the body sync's ``checks_pending``, then ``READY``."""
        number = self.open_pr()
        a = self.accept()
        self.set_checks(number, ("ci", "pass"))
        self.assertIn("just updated", self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING).message)
        self.assertEqual(self.calls("pr", "ready"), [])
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(self.record()["state"], mb.READY)
        return a

    def to_ready(self) -> str:
        return self.to_ready_squash()


class SquashReadinessTest(_Squash):
    def test_an_invalid_title_with_a_valid_declaration_converges_without_a_human(self) -> None:
        number = self.open_pr()
        a = self.accept()
        self.gh_edit(number, title="not a conventional title")
        self.set_checks(number, ("PR title", "fail"), ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertIn("just updated", gate.message)
        self.assertEqual(self.gh_pr_view(number)["title"], TITLE)
        self.assertIn(f"Accepted at {a}", self.gh_pr_view(number)["body"])
        self.assertEqual(self.calls("pr", "ready"), [])
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        edit = self.calls("pr", "edit")[-1]
        self.assertIn("--title", edit)
        self.assertIn("--body", edit)
        # The re-run check passes; nothing is left to edit, and the PR is readied.
        self.set_checks(number, ("PR title", "pass"), ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(len(self.calls("pr", "ready")), 1)
        self.assertEqual((self.record()["state"], self.record()["accepted_head"]), (mb.READY, a))

    def test_a_missing_declaration_with_an_invalid_title_gates_pr_title_invalid(self) -> None:
        self.plan(text=plan_text())
        self.preflight()
        self.approve()
        self.preflight()
        number = self.pr_number()
        self.assertEqual(self.gh_pr_view(number)["title"], WID)
        self.accept()
        self.set_checks(number, ("PR title", "fail"))
        gate = self.assertGate(self.preflight(), mb.GATE_PR_TITLE_INVALID)
        self.assertIn(repr(WID), gate.message)
        self.assertEqual(gate.merge_method, "squash")
        self.assertEqual((self.calls("pr", "edit"), self.calls("pr", "checks"), self.calls("pr", "ready")), ([], [], []))
        human = decision.branch_human_gate("/repo", gate)
        self.assertIn("pr_title_invalid", human.what_is_required)
        # A human sets a valid title on GitHub: it is kept, and readiness goes on.
        self.gh_edit(number, title="fix: set by a human")
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)  # the body sync
        edit = self.calls("pr", "edit")[-1]
        self.assertNotIn("--title", edit)
        self.set_checks(number, ("PR title", "pass"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(self.gh_pr_view(number)["title"], "fix: set by a human")

    def test_a_declared_title_wins_over_a_valid_human_title(self) -> None:
        number = self.open_pr()
        self.accept()
        self.gh_edit(number, title="fix: set by a human")
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.gh_pr_view(number)["title"], TITLE)

    def test_an_edit_ends_the_step_without_checks_even_when_they_are_not_required(self) -> None:
        data = squash_policy()
        data["milestone_branches"]["pull_request"]["ready_requires_green_checks"] = False
        self.fresh_with(data)
        number = self.open_pr()
        self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.calls("pr", "ready"), [])
        self.assertNotEqual(self.record()["state"], mb.READY)
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(self.calls("pr", "checks"), [])
        self.assertFalse(self.gh_pr_view(number)["isDraft"])

    def fresh_with(self, data: dict) -> None:
        self.write_policy(data)
        self.t = commit_all(self.clone, "Change the policy")
        self.push("main")

    def test_grammar_only_when_the_release_trigger_is_version_change(self) -> None:
        self.fresh_with(squash_policy(trigger="version_change"))
        self.plan(text=plan_text("chore: any lowercase type"))
        self.preflight()
        self.approve()
        self.preflight()
        self.assertEqual(self.gh_pr_view(self.pr_number())["title"], "chore: any lowercase type")

    def test_squash_gate_texts_name_squash_and_merge(self) -> None:
        number = self.open_pr()
        self.accept()
        extra = self.commit_file("late.txt")
        self.set_checks(number, ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_POST_ACCEPTANCE_COMMITS)
        self.assertIn(extra, gate.message)
        for text in (gate.message, gate.exits[0], decision.branch_human_gate("/repo", gate).what_is_required):
            self.assertIn("Squash and merge", text)
            self.assertNotIn("Create a merge commit", text)

    def test_integration_and_merge_gates_name_squash_and_merge(self) -> None:
        number = self.open_pr()
        self.trunk_commit()
        self.accept()
        self.set_checks(number, ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)
        for text in (gate.message, gate.exits[0], decision.branch_human_gate("/repo", gate).what_is_required):
            self.assertIn("Squash and merge", text)
            self.assertNotIn("Create a merge commit", text)
        record = dict(self.record(), pr={"number": number, "url": "u"})
        gate = mb._merge_gate(record)
        for text in (gate.message, gate.exits[0], decision.branch_human_gate("/repo", gate).what_is_required):
            self.assertIn("Squash and merge", text)
            self.assertNotIn("Create a merge commit", text)
            self.assertNotIn("rebase", text)


# ---------------------------------------------------------------------------
# Squash close-out (workflow-controller-squash-merge-tag-versioning Design F).
# ---------------------------------------------------------------------------


NOTES_PATH = f"docs/milestones/completed/{WID}.md"
NOTES = "The settings file arrives.\n\n### Added\n\n- `settings show` prints it.\n- Telemetry v0 records each job."


def notes_policy(path: str = "docs/milestones/completed/{work_item_id}.md", heading: str = "Release notes") -> dict:
    """D.4's exact template (settings-and-telemetry CP4)."""
    data = squash_policy()
    data["milestone_branches"]["pull_request"]["release_notes"] = {"path": path, "heading": heading}
    return data


def narrative(notes: str | None, heading: str = "Release notes") -> str:
    section = "" if notes is None else f"## {heading}\n\n{notes}\n\n"
    return f"# {WID}\n\n## Goal\n\nThings.\n\n{section}## Afterwards\n\nMore.\n"


class SquashReleaseNotesTest(_Squash):
    """Readiness puts the milestone's notes section, read at the acceptance
    commit from the binding's policy snapshot's path and heading, into a
    block above the Controller's lines, or gates ``release_notes_invalid``
    with no edit (settings-and-telemetry D.2, I8)."""

    policy_data = staticmethod(notes_policy)

    def accept_with(self, text: str | bytes | None, path: str = NOTES_PATH) -> tuple[int, str]:
        number = self.open_pr()
        if text is not None:
            target = self.clone / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                target.write_bytes(text)
            else:
                target.write_text(text)
        a = self.accept()
        self.set_checks(number, ("ci", "pass"))
        return number, a

    def expected_body(self, a: str, notes: str | None) -> str:
        block = None if notes is None else mb.release_notes.render_block(WID, notes)
        return mb.squash_body(WID, PLAN_PATH, accepted=a, branch=BRANCH, notes_block=block)

    def assertNotesGate(self, number: int, *fragments: str) -> mb.Gate:
        edits = len(self.calls("pr", "edit"))
        gate = self.assertGate(self.preflight(), mb.GATE_RELEASE_NOTES_INVALID)
        for fragment in (NOTES_PATH, *fragments):
            self.assertIn(fragment, gate.message)
        self.assertEqual(gate.merge_method, "squash")
        self.assertEqual(len(self.calls("pr", "edit")), edits)
        self.assertEqual(self.calls("pr", "ready"), [])
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        self.assertNotIn("Accepted at", self.gh_pr_view(number)["body"])
        human = decision.branch_human_gate("/repo", gate)
        self.assertIn("release_notes_invalid", human.what_is_required)
        return gate

    def test_included_notes_go_above_the_controllers_lines_and_parse_as_no_trailer(self) -> None:
        number, a = self.accept_with(narrative(NOTES))
        self.assertIn("just updated", self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING).message)
        body = self.gh_pr_view(number)["body"]
        self.assertEqual(body, self.expected_body(a, NOTES))
        self.assertTrue(body.startswith(f"<!-- workflow-controller: release-notes work_item={WID} "
                                        f"sha256={mb.release_notes.digest(NOTES)} -->\n{NOTES}\n"))
        self.assertEqual(trailers(self.clone, body), "")
        self.assertIsNone(mb.release_notes.paragraph_problem(body))
        self.assertEqual(mb.release_notes.resolve([("m", f"{TITLE}\n\n{body}".encode())]).text, NOTES)
        # Idempotent: nothing is left to edit, and the record goes READY.
        edits = len(self.calls("pr", "edit"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(len(self.calls("pr", "edit")), edits)
        statuses = [(e["event"], e.get("release_notes")) for e in self.events() if e["event"] in ("pr_edited", "ready")]
        self.assertEqual(statuses[-2:], [("pr_edited", "included"), ("ready", "included")])

    def test_absent_and_empty_notes_give_todays_body(self) -> None:
        cases = {"no file": (None, "absent"), "no section": (narrative(None), "absent"),
                 "empty section": (narrative("\n   \n"), "empty")}
        for name, (text, status) in cases.items():
            with self.subTest(name):
                self.fresh()
                number, a = self.accept_with(text)
                self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
                body = self.gh_pr_view(number)["body"]
                self.assertEqual(body, self.expected_body(a, None))
                self.assertNotIn("release-notes", body)
                self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
                ready = [e for e in self.events() if e["event"] == "ready"][-1]
                self.assertEqual(ready["release_notes"], status)

    def test_a_trailer_paragraph_refuses_with_no_edit(self) -> None:
        cases = {
            "fixes and breaking change": ("Intro.\n\nFixes: the thing\nBreaking-Change: none\n\nOutro.",
                                          "Fixes: the thing"),
            "bare url": ("Intro.\n\nhttps://example.com/x\n\nOutro.", "https://example.com/x"),
            "one-line note": ("Intro.\n\nNote: this release changes the default.\n\nOutro.", "Note: this"),
        }
        for name, (notes, excerpt) in cases.items():
            with self.subTest(name):
                self.fresh()
                number, _ = self.accept_with(narrative(notes))
                gate = self.assertNotesGate(number, excerpt, "parses as a Git trailer block",
                                            "reword the paragraph or join it to its neighbour")
                self.assertIn("Squash and merge", gate.exits[0])
                self.assertIn(f"tools/release.py notes-block --work-item {WID}", gate.exits[0])

    def test_ordinary_shapes_are_accepted(self) -> None:
        notes = ("A colon: inside prose.\n\n- a list\nFixes: the thing\n\nUpgrade note: read the guide.\n\n"
                 "**Breaking:** none\n\n| a | b |\n|---|---|\n| 1 | 2 |")
        number, a = self.accept_with(narrative(notes))
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.gh_pr_view(number)["body"], self.expected_body(a, notes))

    def test_the_line_rules_and_the_length_refuse_with_no_edit(self) -> None:
        cases = {
            "73 bytes": ("ok\n" + "x" * 73, "line 2", "73 bytes"),
            "72 characters with an accent": ("é" + "x" * 71, "line 1", "73 bytes"),
            "trailing space": ("a line \nnext", "line 1", "ends in a space"),
            "tab": ("a\tb", "line 1", "tab"),
            "crlf": (None, "line 1", "carriage return"),
            "marker text": ("see <!-- workflow-controller: x -->", "line 1", "marker text"),
            "too long": ("\n".join(["x" * 70] * 1000), "", "65536"),
        }
        for name, (notes, line, rule) in cases.items():
            with self.subTest(name):
                self.fresh()
                text = (narrative("one\ntwo").replace("\n", "\r\n").encode() if notes is None
                        else narrative(notes))
                number, _ = self.accept_with(text)
                self.assertNotesGate(number, line, rule)
        self.fresh()
        number, a = self.accept_with(narrative("x" * 72))
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.gh_pr_view(number)["body"], self.expected_body(a, "x" * 72))

    def test_not_utf8_refuses(self) -> None:
        number, _ = self.accept_with(narrative("caf\u00e9").encode().replace("é".encode(), b"\xff"))
        self.assertNotesGate(number, "not valid UTF-8")

    def test_the_binding_snapshot_decides_the_path_and_heading(self) -> None:
        """A policy edit on the milestone's branch does not move its notes;
        the snapshot's location is read."""
        number = self.open_pr()
        self.write_policy(notes_policy(path="NOTES-{work_item_id}.md", heading="What changed"))
        (self.clone / f"NOTES-{WID}.md").write_text(narrative("Elsewhere.", heading="What changed"))
        target = self.clone / NOTES_PATH
        target.parent.mkdir(parents=True)
        target.write_text(narrative(NOTES))
        commit_all(self.clone, "edit the policy and the notes on the branch")
        a = self.accept()
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.gh_pr_view(number)["body"], self.expected_body(a, NOTES))

    def test_a_snapshot_without_release_notes_gives_todays_body(self) -> None:
        self.write_policy(squash_policy())
        self.t = commit_all(self.clone, "no notes in the policy")
        self.push("main")
        number = self.open_pr()
        self.write_policy(notes_policy())
        target = self.clone / NOTES_PATH
        target.parent.mkdir(parents=True)
        target.write_text(narrative(NOTES))
        commit_all(self.clone, "opt in on the branch")
        a = self.accept()
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.gh_pr_view(number)["body"], self.expected_body(a, None))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        ready = [e for e in self.events() if e["event"] == "ready"][-1]
        self.assertNotIn("release_notes", ready)

    def test_an_uncommitted_section_is_never_read(self) -> None:
        number, a = self.accept_with(narrative(NOTES))
        (self.clone / NOTES_PATH).write_text(narrative("Uncommitted."))
        self.git("update-index", "--assume-unchanged", NOTES_PATH)
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.gh_pr_view(number)["body"], self.expected_body(a, NOTES))


class VerifiedSquashTest(unittest.TestCase):
    """:func:`milestone_branch.verified_squash`, condition by condition, over
    a scratch repository: ``main`` at ``B``, the milestone branch
    ``B..h``, and the merge commit ``m`` built on ``main``."""

    NUMBER = 7

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "repo"
        git_init(self.root, "--initial-branch=main")
        self.git("config", "user.email", "dev@example.invalid")
        self.git("config", "user.name", "Dev")
        self.write("README.md", "base\n")
        self.base = commit_all(self.root, "base")
        self.git("switch", "-q", "-c", BRANCH)
        self.write("a.txt", "a\n")
        commit_all(self.root, "first")
        self.write("b.txt", "b\n")
        self.h = commit_all(self.root, "second")
        self.git("switch", "-q", "main")
        self.ctx = mb.Context(repo_root=self.root, runtime_root=self.tmp / "runtime")
        self.record = {"work_item_id": WID, "branch": BRANCH}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str, env: dict | None = None) -> str:
        import os

        return run(["git", *args], cwd=self.root, env=None if env is None else {**os.environ, **env}).stdout.strip()

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_text(text)

    def squash(self, message: str = f"{TITLE} (#{NUMBER})", *, env: dict | None = None,
               extra: str | None = None) -> str:
        """GitHub's "Squash and merge" of ``h`` onto ``main``."""
        self.git("merge", "-q", "--squash", self.h)
        if extra is not None:
            self.write(extra, "not reviewed\n")
            self.git("add", "-A")
        self.git("commit", "-q", "--cleanup=verbatim", "-m", message, env=env)
        return current_head(self.root)

    def pr(self, m: str | None, *, title: str = TITLE) -> forge_mod.PullRequest:
        return forge_mod.PullRequest(self.NUMBER, "MERGED", False, BRANCH, self.h, "main", False,
                                     f"https://github.com/o/r/pull/{self.NUMBER}", "2026-09-29T00:00:00Z", m,
                                     title, "")

    def verified(self, m: str | None, *, trunk: str | None = None, title: str = TITLE, runner=None) -> bool:
        ctx = self.ctx if runner is None else dataclasses.replace(self.ctx, runner=runner)
        return mb.verified_squash(ctx, self.record, self.pr(m, title=title), self.h, trunk or current_head(self.root))

    def test_a_squash_with_the_title_and_number_is_verified(self) -> None:
        m = self.squash(f"{TITLE} (#{self.NUMBER})\n\nMilestone body\n")
        self.assertTrue(self.verified(m))

    def test_no_merge_commit_or_one_off_the_trunk_is_not_verified(self) -> None:
        self.assertFalse(self.verified(None))
        self.assertFalse(self.verified("0" * 40))
        m = self.squash()
        self.git("reset", "-q", "--hard", self.base)
        self.assertFalse(self.verified(m, trunk=self.base))

    def test_a_two_parent_merge_is_not_verified(self) -> None:
        self.git("merge", "-q", "--no-ff", "-m", f"{TITLE} (#{self.NUMBER})", self.h)
        self.assertFalse(self.verified(current_head(self.root)))

    def test_the_subject_must_be_the_title_with_the_number(self) -> None:
        for message in (TITLE, f"{TITLE} (#8)", "fix: another (#7)", f"{TITLE}  (#7)"):
            with self.subTest(message=message):
                self.git("reset", "-q", "--hard", self.base)
                self.assertFalse(self.verified(self.squash(message)))
        self.assertFalse(self.verified(current_head(self.root), title="feat: renamed"))

    def test_a_different_tree_is_not_verified(self) -> None:
        self.assertFalse(self.verified(self.squash(extra="smuggled.txt")))

    def test_a_rebase_is_not_verified(self) -> None:
        # A multi-commit rebase: m is the rewrite of h, carrying h's subject.
        self.git("cherry-pick", f"{self.base}..{self.h}")
        self.assertFalse(self.verified(current_head(self.root)))
        self.assertFalse(self.verified(current_head(self.root), title="second"))

    def test_a_one_commit_rebase_titled_like_the_pull_request_is_not_verified(self) -> None:
        # h is the branch's only commit.
        for subject in (TITLE, f"{TITLE} (#{self.NUMBER})"):
            with self.subTest(subject=subject):
                self.git("reset", "-q", "--hard", self.base)
                self.git("switch", "-q", "-C", "one", self.base)
                self.write("one.txt", "one\n")
                self.h = commit_all(self.root, subject)
                self.git("switch", "-q", "main")
                self.git("cherry-pick", self.h)
                m = current_head(self.root)
                # Same parent, tree and subject as a squash; condition 3 or 5 tells them apart.
                self.assertEqual(gitrepo.commit_parents(self.root, m), [self.base])
                self.assertEqual(gitrepo.tree_of(self.root, m), gitrepo.tree_of(self.root, self.h))
                self.assertFalse(self.verified(m))

    def test_a_squash_given_the_heads_identity_is_not_verified(self) -> None:
        self.git("switch", "-q", BRANCH)
        self.write("c.txt", "c\n")
        self.h = commit_all(self.root, f"{TITLE} (#{self.NUMBER})")
        self.git("switch", "-q", "main")
        name, email, date = self.git("show", "-s", "--date=raw", "--format=%an%n%ae%n%ad", self.h).split("\n")
        same = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date}
        m = self.squash(f"{TITLE} (#{self.NUMBER})\n", env=same)
        self.assertEqual(gitrepo.commit_identity(self.root, m), gitrepo.commit_identity(self.root, self.h))
        self.assertFalse(self.verified(m))
        # Control: one second later, the same squash is verified.
        self.git("reset", "-q", "--hard", self.base)
        seconds, zone = date.split()
        m = self.squash(f"{TITLE} (#{self.NUMBER})\n", env=dict(same, GIT_AUTHOR_DATE=f"{int(seconds) + 1} {zone}"))
        self.assertTrue(self.verified(m))

    def moved_trunk(self, name: str = "trunk.txt", text: str = "trunk\n") -> None:
        self.write(name, text)
        commit_all(self.root, "main moved")

    def test_the_integration_path_compares_with_merge_tree(self) -> None:
        self.moved_trunk()
        m = self.squash()
        self.assertFalse(gitrepo.is_ancestor(self.root, gitrepo.commit_parents(self.root, m)[0], self.h))
        self.assertTrue(self.verified(m))
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.assertFalse(self.verified(self.squash(extra="smuggled.txt")))

    def test_an_integration_conflict_is_not_verified(self) -> None:
        self.moved_trunk("a.txt", "trunk's a\n")
        # The human resolved the conflict by hand; merge-tree conflicts.
        run(["git", "merge", "--squash", self.h], cwd=self.root, check=False)
        self.write("a.txt", "resolved\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"{TITLE} (#{self.NUMBER})")
        self.assertFalse(self.verified(current_head(self.root)))

    def test_a_configured_merge_driver_is_never_run(self) -> None:
        marker = self.tmp / "driver-ran"
        self.git("config", "merge.marker.driver", f"touch {marker}; cp %B %A")
        (self.root / ".git" / "info" / "attributes").write_text("*.txt merge=marker\n")
        self.moved_trunk("a.txt", "trunk's a\n")
        run(["git", "-c", "merge.marker.driver=false", "merge", "--squash", self.h], cwd=self.root, check=False)
        self.write("a.txt", "a\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"{TITLE} (#{self.NUMBER})")
        m = current_head(self.root)
        self.assertFalse(self.verified(m))
        self.assertFalse(marker.exists())
        # The guard is what kept it from running: merge-tree itself would.
        run(["git", "merge-tree", "--write-tree", f"{m}~1", self.h], cwd=self.root, check=False)
        self.assertTrue(marker.exists())

    def _faking(self, subcommand: str, result) -> Callable:
        real = gitrepo.subprocess_runner()

        def runner(argv):
            if argv[3] == subcommand:
                return result(argv)
            return real(argv)

        return runner

    def test_an_old_git_or_a_failing_merge_tree_refuses_on_the_integration_path(self) -> None:
        self.moved_trunk()
        m = self.squash()
        old = self._faking("version", lambda argv: subprocess.CompletedProcess(argv, 0, b"git version 2.37.9\n", b""))
        with self.assertRaises(BranchBindingError) as caught:
            self.verified(m, runner=old)
        self.assertIn("2.38", str(caught.exception))
        self.assertIn("2.37.9", str(caught.exception))
        broken = self._faking("merge-tree", lambda argv: subprocess.CompletedProcess(argv, 128, b"", b"fatal: boom\n"))
        with self.assertRaises(GitOperationError):
            self.verified(m, runner=broken)
        # The ordinary path needs neither.
        self.git("reset", "-q", "--hard", self.base)
        self.assertTrue(self.verified(self.squash(), runner=old))


class _SquashClose(_Squash):
    def squash_merge(self, number: int) -> str:
        """GitHub's "Squash and merge": the title with ``(#<number>)`` as
        the subject, the body as the body. Returns the squash commit."""
        self.human_merge(number, how="squash_pr")
        return self.origin_ref("refs/heads/main")

    def assert_closed(self, a: str, m: str) -> None:
        record = self.record()
        self.assertEqual((record["state"], record["merged_head"], record["accepted_head"], record["merge_commit"]),
                         (mb.CLOSED, a, a, m))


class SquashCloseOutTest(_SquashClose):
    def test_a_squash_merge_is_verified_and_closes_out_from_the_branch(self) -> None:
        a = self.to_ready()
        m = self.squash_merge(self.pr_number())
        self.assertEqual(self.preflight(), mb.Proceed(action="closed_out", base=m))
        self.assert_closed(a, m)
        self.assertEqual(self.head(), gitrepo.HeadState("main", m))
        self.assertEqual(gitrepo.commit_subject(self.clone, m), f"{TITLE} (#{self.pr_number()})")
        self.assertEqual([e["state"] for e in self.events() if e["event"] in ("merged_squashed", "closed")],
                         [mb.MERGED_SQUASHED, mb.CLOSED])
        self.assertIn(BRANCH, self.git("branch", "--list", BRANCH))  # never deleted
        self.assertEqual(self.preflight(), mb.Proceed(action="trunk_start", base=m))

    def test_from_the_branch_a_dirty_tree_and_an_unmerged_commit_gate(self) -> None:
        a = self.to_ready()
        self.squash_merge(self.pr_number())
        (self.clone / "README.md").write_text("dirty\n")
        self.assertGate(self.preflight(), mb.GATE_DIRTY_TREE)
        self.assertEqual((self.record()["state"], self.head().branch), (mb.MERGED_SQUASHED, BRANCH))
        self.git("checkout", "--", "README.md")
        extra = self.commit_file("unmerged.txt")
        self.assertIn(extra, self.assertGate(self.preflight(), mb.GATE_UNMERGED_COMMITS).message)
        self.git("reset", "-q", "--keep", a)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(self.record()["state"], mb.CLOSED)

    def test_from_the_trunk(self) -> None:
        a = self.to_ready()
        m = self.squash_merge(self.pr_number())
        extra = self.commit_file("unmerged.txt")
        self.pull_main()
        gate = self.assertGate(self.preflight(), mb.GATE_UNMERGED_COMMITS)
        self.assertIn(extra, gate.message)
        self.assertIn(f"git branch -D {BRANCH}", " ".join(gate.exits))
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.git("branch", "-f", BRANCH, a)
        self.assertEqual(self.preflight(), mb.Proceed(action="trunk_start", base=m))
        self.assert_closed(a, m)

    def test_from_the_trunk_before_the_pull(self) -> None:
        a = self.to_ready()
        m = self.squash_merge(self.pr_number())
        self.git("switch", "-q", "main")
        # Close-out from the trunk writes CLOSED; the trunk start then gates the behind trunk.
        self.assertGate(self.preflight(), mb.GATE_FAST_FORWARD_TRUNK)
        self.assert_closed(a, m)
        self.pull_main()
        self.assertEqual(self.preflight(), mb.Proceed(action="trunk_start", base=m))

    def test_a_merge_from_pr_open_after_acceptance_is_verified(self) -> None:
        number = self.open_pr()
        a = self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)  # pushes the acceptance commit
        m = self.squash_merge(number)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assert_closed(a, m)

    def test_the_integration_path_is_verified_with_merge_tree(self) -> None:
        number = self.open_pr()
        self.trunk_commit()
        a = self.accept()
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)
        m = self.squash_merge(number)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assert_closed(a, m)

    def test_an_old_git_on_the_integration_path_refuses_and_writes_nothing(self) -> None:
        number = self.open_pr()
        self.trunk_commit()
        a = self.accept()
        self.assertGate(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)  # pushes the acceptance commit
        m = self.squash_merge(number)
        before = self.snapshot()
        with mock.patch.object(gitrepo, "git_version", return_value=(2, 37, 0)):
            with self.assertRaises(BranchBindingError) as caught:
                self.preflight()
        self.assertIn("2.38", str(caught.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)
        failed = GitOperationError("git merge-tree failed (exit 128)", evidence={})
        with mock.patch.object(gitrepo, "merge_tree", side_effect=failed):
            with self.assertRaises(GitOperationError):
                self.preflight()
        self.assertEqual(self.snapshot(), before)
        # A later step with a working Git verifies the squash.
        self.assertEqual(self.preflight().action, "closed_out")
        self.assert_closed(a, m)

    def test_unverified_merges_stay_merged_rewritten(self) -> None:
        cases = {
            "rebase": lambda number: self.human_merge(number, how="rebase"),
            "bare title": lambda number: self.human_merge(number, how="squash_pr", subject=TITLE),
            "edited subject": lambda number: self.human_merge(number, how="squash_pr", subject="feat: edited (#1)"),
            "a legacy squash": lambda number: self.human_merge(number, how="squash"),
        }
        for name, merge in cases.items():
            with self.subTest(name):
                self.fresh()
                self.to_ready()
                merge(self.pr_number())
                gate = self.assertGate(self.preflight(), mb.GATE_MERGE_METHOD_REWROTE_HISTORY)
                self.assertEqual(self.record()["state"], mb.MERGED_REWRITTEN)
                self.assertIn("Disable squash and rebase merging", gate.message)
                self.assertEqual(self.head().branch, BRANCH)

    def test_merged_before_acceptance_then_new_pr_converges_through_a_verified_squash(self) -> None:
        """The continuation's acceptance and the first squash both add the
        item's state, so GitHub reports a conflict; the human resolves it on
        GitHub (a merge of the trunk into the branch there), then squashes.
        The squash is verified, and close-out names the fast-forward of the
        local branch to the resolved head."""
        number = self.open_pr()
        self.squash_merge(number)
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        # The squash commit is not in the branch, so the next pull request opens at once.
        self.assertEqual(self.preflight().action, "pr_created")
        new = self.pr_number()
        self.assertNotEqual(new, number)
        a = self.accept()
        self.set_checks(new, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)
        human = self.human()
        conflict = run(["git", "merge-tree", "--write-tree", "origin/main", f"origin/{BRANCH}"], cwd=human, check=False)
        self.assertEqual(conflict.returncode, 1)  # a squash-merge button GitHub would disable
        run(["git", "switch", "-q", "-c", "resolve", f"origin/{BRANCH}"], cwd=human)
        run(["git", "merge", "-q", "-X", "ours", "--no-edit", "origin/main"], cwd=human)
        resolved = current_head(human)
        run(["git", "push", "-q", "origin", f"resolve:refs/heads/{BRANCH}"], cwd=human)
        run(["git", "--git-dir", str(self.origin), "update-ref", f"refs/pull/{new}/head", resolved])
        m = self.squash_merge(new)
        self.assertEqual(gitrepo.commit_parents(self.origin, m), [self.origin_ref("refs/heads/main~1")])
        with self.assertRaises(BranchBindingError) as caught:
            self.preflight()
        self.assertIn(f"git merge --ff-only {resolved}", str(caught.exception))
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.git("merge", "-q", "--ff-only", resolved)
        self.assertEqual(self.preflight().action, "closed_out")
        record = self.record()
        self.assertEqual((record["state"], record["merged_head"], record["accepted_head"], record["merge_commit"]),
                         (mb.CLOSED, resolved, a, m))

    def test_merged_before_acceptance_then_new_pr_without_a_conflict_uses_merge_tree(self) -> None:
        number = self.open_pr()
        self.squash_merge(number)
        self.assertGate(self.preflight(), mb.GATE_MERGED_BEFORE_ACCEPTANCE)
        mb.acknowledge(self.ctx, WID, mb.NEW_PR)
        self.assertEqual(self.preflight().action, "pr_created")
        new = self.pr_number()
        self.commit_file("next.txt")
        a = self.accept()
        self.set_checks(new, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)
        # A human squashes with the item's state resolved identically on the
        # trunk first (no conflict left), so merge-tree decides.
        human = self.human()
        run(["git", "checkout", "-q", f"origin/{BRANCH}", "--", mb.STATE_REL_PATH], cwd=human)
        commit_all(human, "chore: take the milestone's state")
        run(["git", "push", "-q", "origin", "main"], cwd=human)
        m = self.squash_merge(new)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assert_closed(a, m)

    def test_status_inspect_and_explain_name_merged_squashed(self) -> None:
        self.to_ready()
        self.squash_merge(self.pr_number())
        (self.clone / "README.md").write_text("dirty\n")
        self.assertGate(self.preflight(), mb.GATE_DIRTY_TREE)
        self.assertIn(f"milestone: {WID} {mb.MERGED_SQUASHED} on {BRANCH}", "\n".join(mb.binding_lines(self.rt)))
        self.assertEqual(mb.observation(self.ctx)["milestone_branch"]["state"], mb.MERGED_SQUASHED)
        predicted = mb.predict(self.ctx)
        self.assertEqual((predicted["action"], predicted["binding_state"]), ("close_out", mb.MERGED_SQUASHED))
        self.assertIn("squash-merged", predicted["detail"])
        self.git("checkout", "--", "README.md")
        self.git("switch", "-q", "main")
        predicted = mb.predict(self.ctx)
        self.assertEqual((predicted["action"], predicted["binding_state"]), ("close_out", mb.MERGED_SQUASHED))
        self.assertIn("from the trunk", predicted["detail"])


class MergeModeUnchangedTest(_Lifecycle):
    """A policy without ``merge_method``: 1.3.0's title, body and gates."""

    def test_creation_title_and_body_are_1_3_0s(self) -> None:
        number = self.open_pr()
        pr = self.gh_pr_view(number)
        self.assertEqual(pr["title"], WID)
        self.assertEqual(pr["body"], f"Milestone `{WID}` (plan: `unrecorded`), driven by workflow-controller. A human "
                                     f"merges it with \"Create a merge commit\".\n\n"
                                     f"<!-- workflow-controller: work_item={WID} -->\n")

    def test_merge_mode_never_edits(self) -> None:
        number = self.open_pr()
        self.gh_edit(number, title="someone's title")
        self.preflight()
        self.accept()
        self.set_checks(number, ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(self.calls("pr", "edit"), [])
        self.assertEqual(gate.merge_method, "merge")
        self.assertIn("Create a merge commit", decision.branch_human_gate("/repo", gate).what_is_required)
        self.assertEqual(self.gh_pr_view(number)["title"], "someone's title")


class MergeModeSquashTest(_Lifecycle):
    """Design F "Only in squash mode": the squash ``SquashCloseOutTest``
    verifies, under a policy without ``merge_method``, is 1.3.0's
    ``MERGED_REWRITTEN``, record and gate byte for byte."""

    def test_a_github_squash_in_merge_mode_is_merged_rewritten_as_in_1_3_0(self) -> None:
        a = self.to_ready()
        number = self.record()["pr"]["number"]
        h = self.human_merge(number, how="squash_pr")
        m = self.origin_ref("refs/heads/main")
        with mock.patch.object(mb, "verified_squash", side_effect=AssertionError("never consulted")):
            gate = self.assertGate(self.preflight(), mb.GATE_MERGE_METHOD_REWROTE_HISTORY)
        record = self.record()
        self.assertLessEqual(set(record), {  # no field 1.3.0 did not write
            "schema_version", "work_item_id", "repository", "policy", "trunk", "branch", "branch_point",
            "workflow_base_commit", "binding_generation", "state", "pr", "superseded_prs", "merged_head",
            "last_observation", "accepted_head", "merge_commit", "rewrite_gate_shown", "untrailered_completion",
            "updated_at"})
        self.assertEqual((record["state"], record["merged_head"], record["accepted_head"], record["merge_commit"],
                          record["rewrite_gate_shown"]), (mb.MERGED_REWRITTEN, h, a, m, True))
        self.assertEqual(gate.message,
                         f"pull request #{number} was merged, but its head {h} is not on origin/main (merge commit "
                         f"{m}): a squash or rebase merge rewrote the reviewed history, and the Workflow provenance "
                         f"on main is unreachable. The Controller cannot undo this and does not switch; switch to "
                         f"the trunk by hand. Disable squash and rebase merging in the repository's settings")
        self.assertEqual((gate.exits, gate.merge_method), (("git switch main",), "merge"))
        self.assertEqual([e["event"] for e in self.events() if e["event"].startswith("merged")], ["merged_rewritten"])

    def test_a_rebase_is_merged_rewritten_in_both_modes(self) -> None:
        """The titled one-commit rebases are ``VerifiedSquashTest``'s."""
        for mode in ("merge", "squash"):
            with self.subTest(mode=mode):
                self.fresh()
                if mode == "squash":
                    self.write_policy(squash_policy())
                    commit_all(self.clone, "Squash merges")
                    self.push("main")
                a = self.to_ready() if mode == "merge" else self._ready_squash()
                number = self.record()["pr"]["number"]
                self.human_merge(number, how="rebase")
                self.assertGate(self.preflight(), mb.GATE_MERGE_METHOD_REWROTE_HISTORY)
                self.assertEqual((self.record()["state"], self.record()["accepted_head"]), (mb.MERGED_REWRITTEN, a))

    def _ready_squash(self) -> str:
        number = self.open_pr()
        a = self.accept()
        self.gh_edit(number, title="feat: a title")
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        return a


def auto_merge_policy() -> dict:
    data = squash_policy()
    data["milestone_branches"]["pull_request"]["auto_merge"] = True
    return data


class _AutoMerge(_SquashClose):
    """A binding whose policy opted in to auto-merge
    (``workflow-controller-auto-merge-release-wait`` CP3, Design C)."""

    policy_data = staticmethod(auto_merge_policy)

    def merges(self) -> list[list[str]]:
        return self.calls("pr", "merge")

    def to_pending(self) -> tuple[int, str]:
        """Accepted, the acceptance commit pushed, checks pending:
        ``(number, A)``."""
        number = self.open_pr()
        a = self.accept()
        self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        return number, a

    def to_held_ready(self, merge_state: str = "BLOCKED") -> tuple[int, str]:
        """``READY`` with green checks, GitHub reporting ``merge_state``, so
        nothing is sent: ``(number, A)``."""
        number, a = self.to_pending()
        self.set_checks(number, ("ci", "pass"))
        self.gh_edit(number, mergeStateStatus=merge_state)
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        self.assertTrue(gate.waitable)
        self.assertEqual((self.record()["state"], self.record()["accepted_head"]), (mb.READY, a))
        self.assertNotIn("merge", self.record())
        return number, a

    def assert_nothing_sent(self, gate: mb.Gate, code: str) -> mb.Gate:
        self.assertEqual(gate.code, code)
        self.assertEqual(self.merges(), [])
        self.assertEqual(self.record()["state"], mb.READY)
        return gate


class AutoMergeTest(_AutoMerge):
    def test_the_merge_is_sent_at_readiness_and_closes_out(self) -> None:
        number, a = self.to_pending()
        self.set_checks(number, ("ci", "pass"))
        outcome = self.preflight()
        m = self.origin_ref("refs/heads/main")
        # The release wait ran (the policy releases nothing), so the step stops (D.4).
        self.assertEqual(outcome, mb.Proceed(binding=self.record(), action="closed_out", stop=True))
        self.assertEqual(self.record()["release"], {"state": mb.RELEASE_NONE})
        self.assert_closed(a, m)
        self.assertEqual(self.merges(), [["pr", "merge", str(number), "--squash", "--match-head-commit", a,
                                          "--subject", f"{TITLE} (#{number})", "--body",
                                          self.gh_pr_view(number)["merge_body"], "--repo", FAKE_GH_REPOSITORY]])
        self.assertEqual(gitrepo.commit_subject(self.clone, m), f"{TITLE} (#{number})")
        self.assertEqual(self.record()["merge"], {"state": "accepted", "head": a, "attempts": 1,
                                                  "last_attempt_at": "2026-09-25T00:00:00Z"})
        self.assertEqual([e["event"] for e in self.events()
                          if e["event"] in ("ready", "merge_sent", "merge_accepted", "merged_squashed", "closed")],
                         ["ready", "merge_sent", "merge_accepted", "merged_squashed", "closed"])
        self.assertFalse(any("--auto" in argv or "--disable-auto" in argv for argv in self.gh_calls()))

    def test_a_ready_record_merges_at_the_next_step(self) -> None:
        number, a = self.to_held_ready("UNKNOWN")
        self.gh_edit(number, mergeStateStatus="CLEAN")
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)
        self.assert_closed(a, self.origin_ref("refs/heads/main"))

    def test_has_hooks_is_mergeable(self) -> None:
        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="HAS_HOOKS")
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)

    def test_a_local_commit_past_a_wins_over_a_failing_check(self) -> None:
        number, a = self.to_held_ready()
        extra = self.commit_file("later.txt")
        self.set_checks(number, ("ci", "fail"))
        self.gh_edit(number, mergeStateStatus="CLEAN")
        gate = self.assert_nothing_sent(self.preflight(), mb.GATE_POST_ACCEPTANCE_COMMITS)
        self.assertIn(extra, gate.message)
        self.assertIn(f"marked ready at {a}, and the Controller merges only {a}", gate.message)
        self.assertFalse(gate.waitable)
        self.assertEqual(gate.merge_method, "squash")

    def test_a_local_commit_that_is_also_pushed_gates_post_acceptance_commits(self) -> None:
        number, a = self.to_held_ready()
        extra = self.commit_file("later.txt")
        self.push(BRANCH)
        self.gh_edit(number, mergeStateStatus="CLEAN")
        self.assertEqual(self.gh_pr_view(number)["state"], "OPEN")
        gate = self.assert_nothing_sent(self.preflight(), mb.GATE_POST_ACCEPTANCE_COMMITS)
        self.assertIn(extra, gate.message)
        self.assertIn("sends no merge while the branch carries later commits", gate.message)

    def test_a_local_commit_that_is_not_pushed_gates_post_acceptance_commits(self) -> None:
        number, a = self.to_held_ready()
        self.commit_file("later.txt")
        self.gh_edit(number, mergeStateStatus="CLEAN")
        self.assert_nothing_sent(self.preflight(), mb.GATE_POST_ACCEPTANCE_COMMITS)
        self.assertEqual(self.origin_ref(f"refs/heads/{BRANCH}"), a)  # nothing pushed past A (I7)

    def test_a_push_on_github_only_is_refused_and_nothing_is_merged(self) -> None:
        number, a = self.to_held_ready()
        self.set_checks(number, ("ci", "pending"))
        human = self.human()
        run(["git", "switch", "-q", "-c", "update", f"origin/{BRANCH}"], cwd=human)
        (human / "update.txt").write_text("update branch\n")
        commit_all(human, "Update branch")
        run(["git", "push", "-q", "origin", f"update:{BRANCH}"], cwd=human)
        self.set_checks(number, ("ci", "pass"))
        self.gh_edit(number, mergeStateStatus="CLEAN")
        with self.assertRaises(BranchBindingError) as caught:
            self.preflight()
        self.assertIn("Update branch", str(caught.exception))
        self.assertEqual(self.merges(), [])
        self.assertEqual(self.gh_pr_view(number)["state"], "OPEN")

    def test_a_lagging_or_missing_pull_request_head_gates_pr_head_not_accepted(self) -> None:
        number, a = self.to_held_ready()
        view = dict(self.gh_pr_view(number), headRefOid=self.record()["branch_point"], mergeStateStatus="CLEAN")
        view = {k: view[k] for k in (*fake_gh_fields(), "mergeStateStatus")}
        self.gh_edit(number, mergeStateStatus="CLEAN", lagged_reads=1, lagged_view=view)
        gate = self.assert_nothing_sent(self.preflight(), mb.GATE_PR_HEAD_NOT_ACCEPTED)
        self.assertTrue(gate.waitable)
        # The remote branch is gone: the same gate.
        run(["git", "--git-dir", str(self.origin), "update-ref", "-d", f"refs/heads/{BRANCH}"])
        gate = self.assert_nothing_sent(self.preflight(), mb.GATE_PR_HEAD_NOT_ACCEPTED)
        self.assertIn(f"origin/{BRANCH} is None", gate.message)

    def test_a_draft_holds_the_merge_and_ready_for_review_releases_it(self) -> None:
        number, a = self.to_held_ready()
        self.set_checks(number, ("ci", "pending"))
        self.gh_edit(number, isDraft=True, mergeStateStatus="DRAFT")
        gate = self.assert_nothing_sent(self.preflight(), mb.GATE_MERGE_HELD)
        self.assertFalse(gate.waitable)
        self.assertIn("ready for review", gate.exits[0])
        self.assertEqual(self.record()["state"], mb.READY)
        self.set_checks(number, ("ci", "pass"))
        self.gh_edit(number, isDraft=False, mergeStateStatus="CLEAN")
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)

    def test_checks_after_acceptance(self) -> None:
        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN")
        for buckets, code, waitable in ((("ci", "pending"),), mb.GATE_CHECKS_PENDING, True), \
                                       ((("ci", "fail"), ("lint", "pending")), mb.GATE_CHECKS_FAILING, False), \
                                       ((("ci", "cancel"),), mb.GATE_CHECKS_CANCELLED, False):
            with self.subTest(code):
                self.set_checks(number, *buckets)
                gate = self.assert_nothing_sent(self.preflight(), code)
                self.assertEqual(gate.waitable, waitable)
                self.assertEqual("once a re-run turns the checks green" in gate.message, not waitable)

    def test_merge_states_that_send_nothing(self) -> None:
        number, a = self.to_held_ready()
        cases = {"UNKNOWN": ("not computed its mergeability", True),
                 "DRAFT": ("not computed its mergeability", True),
                 "BLOCKED": ("other than the checks", True),
                 "UNSTABLE": ("not required is not green", True),
                 "SOMETHING_NEW": ("SOMETHING_NEW", True),
                 "DIRTY": ("conflict", False)}
        for state, (text, waitable) in cases.items():
            with self.subTest(state):
                self.gh_edit(number, mergeStateStatus=state)
                gate = self.assert_nothing_sent(self.preflight(), mb.GATE_MERGE_PENDING)
                self.assertIn(text, gate.message)
                self.assertEqual(gate.waitable, waitable)
        self.gh_edit(number, mergeStateStatus="BEHIND")
        gate = self.assert_nothing_sent(self.preflight(), mb.GATE_INTEGRATION_REQUIRED)
        self.assertFalse(gate.waitable)
        self.assertIn("BEHIND", gate.message)

    def test_a_moved_trunk_without_behind_is_merged_and_verified(self) -> None:
        number, a = self.to_held_ready()
        self.trunk_commit()
        self.gh_edit(number, mergeStateStatus="CLEAN")
        self.assertEqual(self.preflight().action, "closed_out")
        self.assert_closed(a, self.origin_ref("refs/heads/main"))

    def test_a_merge_that_reads_open_is_accepted_and_never_sent_again(self) -> None:
        number, a = self.to_pending()
        self.gh_edit(number, merge_read_lag=2)
        self.set_checks(number, ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        self.assertTrue(gate.waitable)
        self.assertIn("is not visible yet", gate.message)
        self.assertIn("Squash and merge", " ".join(gate.exits))
        self.assertIn("milestone-binding", " ".join(gate.exits))
        self.assertEqual(self.record()["merge"]["state"], "accepted")
        # The second lagged read: still OPEN, nothing sent again.
        self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        self.assertEqual(len(self.merges()), 1)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)

    def test_an_accepted_merge_waits_through_lagging_red_checks_and_behind(self) -> None:
        number, a = self.to_pending()
        self.gh_edit(number, merge_read_lag=3)
        self.set_checks(number, ("ci", "pass"))
        self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        self.assertEqual(self.record()["merge"]["state"], "accepted")
        # A lagging read with a red check, then one reporting BEHIND: the
        # accepted row decides first, waitable, and nothing is sent again.
        self.set_checks(number, ("ci", "fail"))
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        self.assertTrue(gate.waitable)
        self.assertIn("is not visible yet", gate.message)
        self.gh_edit(number, lagged_view=dict(self.gh_pr_view(number)["lagged_view"], mergeStateStatus="BEHIND"))
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        self.assertTrue(gate.waitable)
        self.assertEqual(len(self.merges()), 1)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)
        self.assert_closed(a, self.origin_ref("refs/heads/main"))

    def test_a_lost_reply_is_adopted_by_the_re_read(self) -> None:
        number, a = self.to_pending()
        self.gh_edit(number, merge_reply_lost=True)
        self.set_checks(number, ("ci", "pass"))
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)
        self.assertEqual(self.events_named("merge_refused"), [])

    def test_a_send_that_gh_reports_already_merged_is_accepted(self) -> None:
        # Real gh (Flow Q4 of the functional review) exits 0 with "! ... was
        # already merged" for a pull request that merged while the Controller's
        # read still showed it open and clean: the send is accepted, no
        # refusal is counted, and the step closes out as for any merge.
        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN")
        stored = self.gh_pr_view(number)  # what a lagging read shows: open, clean, at A
        stale_open = {**{key: stored[key] for key in fake_gh.PR_JSON_FIELDS}, "headRefOid": a,
                      "mergeStateStatus": "CLEAN"}
        m = self.squash_merge(number)
        self.gh_edit(number, lagged_reads=1, lagged_view=stale_open)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)  # one send, and gh sent nothing
        self.assertEqual(self.origin_ref("refs/heads/main"), m)
        self.assertEqual(self.events_named("merge_refused"), [])
        merge = self.record()["merge"]
        self.assertEqual((merge["state"], merge["attempts"]), ("accepted", 1))
        self.assertNotIn("refusals", merge)
        self.assert_closed(a, m)

    def test_a_lagging_read_after_an_exit_1_refusal_is_still_counted(self) -> None:
        # The other path, kept covered: a send whose reply is an ordinary
        # refusal while the pull request is not yet merged counts against
        # the budget, whatever the read shows later.
        number, a = self.to_pending()
        self.set_checks(number, ("ci", "pass"))
        refusal = ForgeUndecidableError("gh pr merge failed (exit 1)", evidence={"stderr": "Refused.\n"})
        with mock.patch.object(forge_mod.GhForge, "merge_squash", side_effect=refusal):
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        merge = self.record()["merge"]
        self.assertEqual((merge["state"], merge["attempts"], merge["refusals"]), ("sending", 1, 1))

    def test_a_lost_reply_with_a_lagging_read_is_adopted_from_the_trunk(self) -> None:
        # C.3: the reply is lost and the re-read lags OPEN, but the trunk
        # shows the squash commit, so the merge is accepted and never sent
        # again. One squash commit lands.
        number, a = self.to_pending()
        self.gh_edit(number, merge_reply_lost=True, merge_read_lag=2)
        self.set_checks(number, ("ci", "pass"))
        before = self.origin_ref("refs/heads/main")
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
        self.assertTrue(gate.waitable)
        self.assertIn("is not visible yet", gate.message)
        m = self.origin_ref("refs/heads/main")
        merge = self.record()["merge"]
        self.assertEqual((merge["state"], merge["attempts"], merge["squash_commit"]), ("accepted", 1, m))
        self.assertNotIn("refusals", merge)
        self.assertEqual(self.events_named("merge_refused"), [])
        self.assertEqual(self.until_closed_out()[0].code, mb.GATE_MERGE_PENDING)
        self.assertEqual([argv[argv.index("--match-head-commit") + 1] for argv in self.merges()], [a])
        self.assertEqual(gitrepo.first_parent_log(self.clone, before, m), [m])
        self.assert_closed(a, m)

    def test_a_lost_reply_with_reads_lagging_past_the_budget_waits_for_the_merge(self) -> None:
        # The first send merged but its reply was lost, and the reads stay
        # OPEN for longer than the attempt budget. The trunk decides, not
        # any refusal text: nothing more is sent and the step waits, never
        # the terminal manual-merge refusal, until the read shows the merge.
        number, a = self.to_pending()
        self.gh_edit(number, merge_reply_lost=True, merge_read_lag=8)
        self.set_checks(number, ("ci", "pass"))
        before = self.origin_ref("refs/heads/main")
        gates = [self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING), *self.until_closed_out()]
        self.assertGreater(len(gates), 3)
        self.assertTrue(all(gate.code == mb.GATE_MERGE_PENDING and gate.waitable for gate in gates))
        self.assertTrue(all("is not visible yet" in gate.message for gate in gates))
        self.assertEqual([argv[argv.index("--match-head-commit") + 1] for argv in self.merges()], [a])
        m = self.origin_ref("refs/heads/main")
        self.assertEqual(self.events_named("merge_accepted")[-1]["squash_commit"], m)
        self.assertFalse(any("already merged" in str(e) for e in self.events()))
        self.assertEqual(gitrepo.first_parent_log(self.clone, before, m), [m])
        self.assert_closed(a, m)

    def test_a_merge_whose_reply_is_lost_at_the_budget_is_adopted_whatever_the_text(self) -> None:
        # Two refusals, then the third send merges but fails with a text
        # that says nothing about a merge (GitHub's mutation-path wording is
        # unverified), and the reads lag OPEN: the trunk shows the squash
        # commit, so the step waits instead of the terminal refusal.
        real = forge_mod.GhForge.merge_squash
        refusal = ForgeUndecidableError("gh pr merge failed (exit 1)", evidence={"stderr": "Refused.\n"})
        lost = ForgeUndecidableError("gh pr merge failed (exit 1)",
                                     evidence={"stderr": "GraphQL: Something went wrong while executing your query."})
        sends = []

        def refuse_twice_then_merge_and_fail(forge, *args, **kwargs):
            sends.append(kwargs["head"])
            if len(sends) <= 2:
                raise refusal
            real(forge, *args, **kwargs)
            raise lost

        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN", merge_read_lag=6)
        with mock.patch.object(forge_mod.GhForge, "merge_squash", refuse_twice_then_merge_and_fail):
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            self.assertIn("is not visible yet", gate.message)
            merge = self.record()["merge"]
            self.assertEqual((merge["state"], merge["attempts"], merge["refusals"]), ("accepted", 3, 2))
            self.assertEqual(mb.predict(self.ctx)["action"], "wait_merge")
            gates = self.until_closed_out()
        self.assertTrue(all(gate.code == mb.GATE_MERGE_PENDING and gate.waitable for gate in gates))
        self.assertEqual(sends, [a, a, a])
        self.assert_closed(a, self.origin_ref("refs/heads/main"))

    def test_a_crash_after_the_last_attempt_merged_is_adopted_from_the_trunk(self) -> None:
        # Two refused attempts, then the third merges and crashes before
        # its outcome is recorded, with the reads lagging: the next step
        # finds the squash commit on the trunk and waits; nothing more is sent.
        real = forge_mod.GhForge.merge_squash
        refusal = ForgeUndecidableError("gh pr merge failed (exit 1)", evidence={"stderr": "Refused.\n"})
        sends = []

        def refuse_twice_then_merge_and_crash(forge, *args, **kwargs):
            sends.append(kwargs["head"])
            if len(sends) <= 2:
                raise refusal
            real(forge, *args, **kwargs)
            raise KeyboardInterrupt

        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN", merge_read_lag=6)
        with mock.patch.object(forge_mod.GhForge, "merge_squash", refuse_twice_then_merge_and_crash):
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
            merge = self.record()["merge"]
            self.assertEqual((merge["state"], merge["attempts"], merge["refusals"]), ("sending", 3, 2))
            self.assertEqual(mb.predict(self.ctx)["action"], "wait_merge")
            gates = self.until_closed_out()
        self.assertTrue(gates)
        self.assertTrue(all(gate.code == mb.GATE_MERGE_PENDING and gate.waitable for gate in gates))
        self.assertTrue(all("is not visible yet" in gate.message for gate in gates))
        self.assertEqual(sends, [a, a, a])
        self.assert_closed(a, self.origin_ref("refs/heads/main"))

    def test_a_crash_before_the_last_send_waits_and_sends_no_more(self) -> None:
        # Two refusals, then a crash between the third intent and its call:
        # the budget is spent, one outcome is unrecorded and the trunk shows
        # no squash commit. The step waits, sends nothing more, and a hand
        # merge closes out.
        refusal = ForgeUndecidableError("gh pr merge failed (exit 1)", evidence={"stderr": "Refused.\n"})
        sends = []

        def refuse_twice_then_crash(forge, *args, **kwargs):
            sends.append(kwargs["head"])
            if len(sends) <= 2:
                raise refusal
            raise KeyboardInterrupt

        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN")
        with mock.patch.object(forge_mod.GhForge, "merge_squash", refuse_twice_then_crash):
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
            for _ in range(2):
                gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
                self.assertTrue(gate.waitable)
                self.assertIn("may already have merged", gate.message)
                self.assertIn("the outcome of 1 of the Controller's 3 merges is unknown (the step stopped",
                              gate.message)
                self.assertIn("origin/main does not show the squash commit yet", gate.message)
            self.assertEqual(sends, [a, a, a])
        self.assertEqual(self.record()["merge"]["state"], "sending")
        m = self.squash_merge(number)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assert_closed(a, m)

    def until_closed_out(self) -> list:
        gates = []
        for _ in range(12):
            result = self.preflight()
            if getattr(result, "action", None) == "closed_out":
                return gates
            gates.append(self.assertGate(result, mb.GATE_MERGE_PENDING))
        self.fail("the merged pull request never closed out")

    def test_a_crash_between_the_intent_and_the_call(self) -> None:
        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN")
        with mock.patch.object(forge_mod.GhForge, "merge_squash", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.preflight()
        self.assertEqual((self.record()["merge"]["state"], self.record()["merge"]["attempts"]), ("sending", 1))
        self.assertEqual(self.merges(), [])
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertEqual(len(self.merges()), 1)
        self.assertEqual(len(self.events_named("merge_sent")), 1)

    def test_a_crash_between_the_call_and_the_outcome(self) -> None:
        real = forge_mod.GhForge.merge_squash

        def merge_then_crash(forge, *args, **kwargs):
            real(forge, *args, **kwargs)
            raise KeyboardInterrupt

        for lag in (0, 1):
            with self.subTest(lag=lag):
                self.fresh()
                number, a = self.to_held_ready()
                self.gh_edit(number, mergeStateStatus="CLEAN", merge_read_lag=lag)
                with mock.patch.object(forge_mod.GhForge, "merge_squash", merge_then_crash):
                    with self.assertRaises(KeyboardInterrupt):
                        self.preflight()
                self.assertEqual(self.record()["merge"]["state"], "sending")
                # Lag 0: the re-read shows MERGED. Lag 1: the read lags OPEN,
                # the trunk shows the squash commit, and nothing is re-sent.
                if lag:
                    self.assertIn("is not visible yet", self.assertGate(self.preflight(),
                                                                        mb.GATE_MERGE_PENDING).message)
                    self.assertEqual(self.record()["merge"]["state"], "accepted")
                self.assertEqual(self.preflight().action, "closed_out")
                self.assertEqual(len(self.merges()), 1)
                self.assert_closed(a, self.origin_ref("refs/heads/main"))

    def test_refused_merges_and_the_attempt_budget(self) -> None:
        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN")
        refusal = ForgeUndecidableError("gh pr merge failed (exit 1)",
                                        evidence={"stderr": "Squash merges are not allowed on this repository.\n"})
        with mock.patch.object(forge_mod.GhForge, "merge_squash", side_effect=refusal) as merge:
            gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            self.assertTrue(gate.waitable)
            self.assertIn("Squash merges are not allowed on this repository.", gate.message)
            self.assertIn("attempt 1 of 3", gate.message)
            # Sent again only at CLEAN.
            self.gh_edit(number, mergeStateStatus="BLOCKED")
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            self.assertEqual(merge.call_count, 1)
            self.gh_edit(number, mergeStateStatus="CLEAN")
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            with self.assertRaises(BranchBindingError) as caught:
                self.preflight()
            self.assertEqual(merge.call_count, 3)
            self.assertIn("refused the Controller's merge of pull request", str(caught.exception))
            self.assertIn("Squash merges are not allowed", str(caught.exception))
            self.assertIn("Squash and merge", " ".join(caught.exception.evidence["exits"]))
            # The way out is stated once, as the Exit: line, not also in the message.
            self.assertEqual(str(caught.exception).count('merge pull request #'), 1)
            self.assertNotIn("Merge pull request", str(caught.exception))
            # Never sent again.
            with self.assertRaises(BranchBindingError):
                self.preflight()
            self.assertEqual(merge.call_count, 3)
        self.assertEqual(len(self.events_named("merge_refused")), 1)  # one per distinct message
        self.assertEqual(self.record()["merge"]["attempts"], 3)
        m = self.squash_merge(number)
        self.assertEqual(self.preflight().action, "closed_out")
        self.assert_closed(a, m)

    def test_an_unrelated_trunk_commit_with_the_suffix_is_not_adopted(self) -> None:
        # A trunk commit whose subject ends with ``(#<number>)`` but whose
        # content is not the acceptance commit's squash is not this pull
        # request's merge: a real refusal is counted, and at the budget the
        # terminal manual-merge refusal stands.
        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN")
        human = self.human()
        (human / "unrelated.txt").write_text("elsewhere\n")
        unrelated = commit_all(human, f"fix unrelated issue (#{number})")
        run(["git", "push", "-q", "origin", "main"], cwd=human)
        refusal = ForgeUndecidableError("gh pr merge failed (exit 1)",
                                        evidence={"stderr": "Resource not accessible by integration.\n"})
        with mock.patch.object(forge_mod.GhForge, "merge_squash", side_effect=refusal) as merge:
            gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            self.assertIn("Resource not accessible by integration.", gate.message)
            self.assertEqual((self.record()["merge"]["state"], self.record()["merge"]["refusals"]), ("sending", 1))
            self.assertNotIn("squash_commit", self.record()["merge"])
            self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
            with self.assertRaises(BranchBindingError) as caught:
                self.preflight()
            self.assertIn("Resource not accessible by integration.", str(caught.exception))
            self.assertEqual(merge.call_count, 3)
        self.assertEqual(self.events_named("merge_accepted"), [])
        self.assertEqual(self.origin_ref("refs/heads/main"), unrelated)
        merge = self.record()["merge"]
        self.assertEqual((merge["attempts"], merge["refusals"]), (3, 3))
        self.assertNotIn("squash_commit", merge)

    def test_new_pr_starts_a_new_merge_record(self) -> None:
        # The accepted gate's own recovery exit, and a pull request closed
        # after refused sends: the replacement is a new merge record (C.3's
        # attempt budget binds one pull request), sent once at A.
        refusal = ForgeUndecidableError("gh pr merge failed (exit 1)", evidence={"stderr": "Refused.\n"})
        for case, side_effect in (("accepted", None), ("refused", refusal)):
            with self.subTest(case):
                self.fresh()
                old, a = self.to_held_ready()
                self.gh_edit(old, mergeStateStatus="CLEAN")
                with mock.patch.object(forge_mod.GhForge, "merge_squash", side_effect=side_effect):
                    if case == "accepted":
                        self.assertIn("is not visible yet", self.assertGate(self.preflight(),
                                                                            mb.GATE_MERGE_PENDING).message)
                    else:
                        self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
                        self.assertGate(self.preflight(), mb.GATE_MERGE_PENDING)
                        with self.assertRaises(BranchBindingError):
                            self.preflight()
                merge = self.record()["merge"]
                self.assertEqual((merge["state"], merge["attempts"], merge.get("refusal")),
                                 ("accepted", 1, None) if case == "accepted" else ("sending", 3, "Refused."))
                self.gh_edit(old, state="CLOSED")
                self.assertGate(self.preflight(), mb.GATE_PR_CLOSED_UNMERGED)
                self.assertNotIn("merge", mb.acknowledge(self.ctx, WID, mb.NEW_PR))
                self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
                number = self.record()["pr"]["number"]
                self.assertNotEqual(number, old)
                self.assertNotIn("merge", self.record())
                self.set_checks(number, ("ci", "pass"))
                self.gh_edit(number, mergeStateStatus="CLEAN")
                self.assertEqual(self.preflight().action, "closed_out")
                self.assertEqual([(argv[2], argv[argv.index("--match-head-commit") + 1]) for argv in self.merges()],
                                 [(str(number), a)])
                self.assertEqual(self.record()["merge"]["attempts"], 1)
                self.assert_closed(a, self.origin_ref("refs/heads/main"))

    def test_a_persons_own_auto_merge_request_is_neither_read_nor_withdrawn(self) -> None:
        number, a = self.to_held_ready()
        self.gh_edit(number, mergeStateStatus="CLEAN", autoMergeRequest={"mergeMethod": "SQUASH"})
        self.assertEqual(self.preflight().action, "closed_out")
        self.assertFalse(any("autoMergeRequest" in " ".join(argv) or "--disable-auto" in argv
                             for argv in self.gh_calls()))

    def test_merge_auto_false_is_the_1_5_0_merge_gate(self) -> None:
        number, a = self.to_pending()
        self.ctx = dataclasses.replace(self.ctx, auto_merge=False)
        self.set_checks(number, ("ci", "pass"))
        gate = self.assertGate(self.preflight(), mb.GATE_MERGE_PULL_REQUEST)
        self.assertEqual(gate, mb._merge_gate(self.record(), tip=a, switched_off=True))
        # Functional review F6: the gate names the setting, not "never merges".
        self.assertIn("switched off by `merge.auto` in the settings file", gate.message)
        self.assertNotIn("never merges", gate.message)
        self.assertEqual(self.merges(), [])
        self.assertEqual(self.predict()["action"], "gate")
        # A record the Controller already tried to merge: still today's gate.
        self.seed(mb.READY, merge={"state": "accepted", "head": a, "attempts": 1})
        self.assertEqual(self.preflight(), gate)

    def test_predict(self) -> None:
        number, a = self.to_held_ready()
        prediction = self.predict()
        self.assertEqual((prediction["action"], prediction["gate"]), ("merge", None))
        self.seed(mb.READY, merge={"state": "sending", "head": a, "attempts": 1})
        self.assertEqual(self.predict()["action"], "merge")
        self.seed(mb.READY, merge={"state": "accepted", "head": a, "attempts": 1})
        prediction = self.predict()
        self.assertEqual((prediction["action"], prediction["gate"]), ("wait_merge", mb.GATE_MERGE_PENDING))
        self.assertIn("as of", prediction["detail"])

    def predict(self) -> dict:
        return mb.predict(self.ctx)


def release_wait_policy(**pull_request) -> dict:
    """Auto-merge with releases on, versioned by ``pyproject.toml``."""
    data = squash_policy(trigger="version_change")
    data["milestone_branches"]["pull_request"].update(auto_merge=True, **pull_request)
    data["release"]["enabled"] = True
    return data


RUNS_URL = f"https://github.com/{FAKE_GH_REPOSITORY}/actions/runs"


class _ReleaseWait(_AutoMerge):
    """The release wait (``workflow-controller-auto-merge-release-wait``
    CP4, Design D) over a real classification: ``pyproject.toml`` carries
    1.0.0, tags live in the bare origin, and the fake forge holds the
    releases and the workflow runs."""

    policy_data = staticmethod(release_wait_policy)

    def setUp(self) -> None:
        super().setUp()
        self.set_version(self.clone, "1.0.0")
        self.t = commit_all(self.clone, "Version 1.0.0")
        self.push("main")

    @staticmethod
    def set_version(root: Path, version: str) -> None:
        (root / "pyproject.toml").write_text(f'[project]\nname = "pkg"\nversion = "{version}"\n')

    # -- GitHub's side ---------------------------------------------------------------

    def add_run(self, commit: str, status: str = "in_progress", conclusion: str = "", *,
                workflow: str = "Main", file: str = "main.yml") -> int:
        data = self.gh()
        run_id = 100 + len(data["runs"])
        data["runs"].append({"databaseId": run_id, "workflowName": workflow, "workflowFile": file,
                             "headSha": commit, "headBranch": "main", "event": "push", "status": status,
                             "conclusion": conclusion, "attempt": 1, "url": f"{RUNS_URL}/{run_id}"})
        from tests import fake_gh

        fake_gh.write_state(self.gh_state, data)
        return run_id

    def finish_run(self, run_id: int, conclusion: str) -> None:
        from tests import fake_gh

        data = self.gh()
        for record in data["runs"]:
            if record["databaseId"] == run_id:
                record.update(status="completed", conclusion=conclusion)
        fake_gh.write_state(self.gh_state, data)

    def tag(self, tag: str, commit: str) -> None:
        run(["git", "--git-dir", str(self.origin), "tag", tag, commit])

    def publish(self, tag: str, *, draft: bool = False) -> str:
        """The release for ``tag``, as the publishing run creates it; returns its URL."""
        from tests import fake_gh

        data = self.gh()
        url = f"{data['url']}/releases/tag/{tag}"
        data["releases"].append({"tagName": tag, "isDraft": draft, "url": url, "assets": []})
        fake_gh.write_state(self.gh_state, data)
        return url

    def push_trunk(self, name: str, *, version: str | None = None) -> str:
        """Someone lands a later commit on the trunk (bumping the version)."""
        human = self.human()
        (human / name).write_text("later\n")
        if version is not None:
            self.set_version(human, version)
        commit = commit_all(human, f"land {name}")
        run(["git", "push", "-q", "origin", "main"], cwd=human)
        return commit

    # -- the Controller's side ------------------------------------------------------

    def merge(self) -> tuple[str, str, object]:
        """The Controller's merge at readiness: ``(A, m, the step's outcome)``."""
        number, a = self.to_pending()
        self.set_checks(number, ("ci", "pass"))
        outcome = self.preflight()
        self.assertEqual(len(self.merges()), 1)
        return a, self.origin_ref("refs/heads/main"), outcome

    def assertPending(self, outcome, *fragments: str) -> mb.Gate:
        gate = self.assertGate(outcome, mb.GATE_RELEASE_PENDING)
        self.assertTrue(gate.waitable)
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.assertNotIn("release", self.record())
        for fragment in fragments:
            self.assertIn(fragment, gate.message)
        return gate

    def assertFailed(self, outcome, *fragments: str) -> mb.Gate:
        gate = self.assertGate(outcome, mb.GATE_RELEASE_FAILED)
        self.assertFalse(gate.waitable)
        self.assertEqual(self.record()["state"], mb.MERGED_SQUASHED)
        self.assertNotIn("release", self.record())
        for fragment in fragments:
            self.assertIn(fragment, gate.message)
        self.assertIn(mb.HAND_RELEASE, gate.exits)
        return gate

    def assertStopped(self, outcome, release: dict) -> dict:
        record = self.record()
        self.assertEqual(outcome, mb.Proceed(binding=record, action="closed_out", stop=True))
        self.assertEqual((record["state"], record["release"]), (mb.CLOSED, release))
        self.assertEqual(self.events_named("release_failed"), [])
        return record

    def workflows_read(self) -> list[str]:
        return [argv[argv.index("--workflow") + 1] for argv in self.calls("run", "list")]


class ReleaseWaitTest(_ReleaseWait):
    def test_released_records_the_tag_and_url_without_downloading_or_running_anything(self) -> None:
        a, m, outcome = self.merge()
        self.assertPending(outcome, "v1.0.0 is not published yet (RELEASE_DUE)", "not reported yet",
                           "publish by hand with `tools/release.py`")
        self.assertEqual(self.head().branch, BRANCH)
        run_id = self.add_run(m, "queued")
        self.assertPending(self.preflight(), f"run {run_id} (queued) {RUNS_URL}/{run_id}")
        self.tag("v1.0.0", m)
        url = self.publish("v1.0.0")
        self.finish_run(run_id, "success")
        with mock.patch.object(release_txn, "release_problems", side_effect=AssertionError("downloaded")), \
                mock.patch.object(release_txn, "_run_policy_command", side_effect=AssertionError("ran")):
            outcome = self.preflight()
        self.assertStopped(outcome, {"state": "ALREADY_RELEASED", "version": "1.0.0", "tag": "v1.0.0", "url": url})
        self.assertEqual(self.head(), gitrepo.HeadState("main", m))
        self.assertEqual([e["release"] for e in self.events_named("released")], [self.record()["release"]])
        self.assertFalse(any(argv[:2] == ["release", "download"] for argv in self.gh_calls()))
        self.assertEqual(set(self.workflows_read()), {"main.yml"})
        # The next step starts from the trunk with the explicit base.
        self.assertEqual(self.preflight(), mb.Proceed(action="trunk_start", base=m))

    def test_no_change_settles_in_the_merge_step(self) -> None:
        self.tag("v1.0.0", self.t)
        self.publish("v1.0.0")
        a, m, outcome = self.merge()
        record = self.assertStopped(outcome, {"state": "NO_CHANGE", "version": "1.0.0",
                                              "detail": f"v1.0.0 is published at {self.t}, an ancestor"})
        self.assertEqual(self.workflows_read(), [])
        self.assertEqual(self.events_named("release_settled")[0]["release"], record["release"])

    def test_a_policy_at_m_without_releases_settles_none(self) -> None:
        # The milestone itself turns releases off: the policy at m decides.
        number = self.open_pr()
        data = release_wait_policy()
        data["release"]["enabled"] = False
        self.write_policy(data)
        commit_all(self.clone, "Releases off")
        self.accept()
        self.preflight()
        self.set_checks(number, ("ci", "pass"))
        self.assertStopped(self.preflight(), {"state": mb.RELEASE_NONE})
        self.assertEqual(self.calls("run", "list"), [])

    def test_a_failed_other_workflow_never_fails_the_wait(self) -> None:
        a, m, outcome = self.merge()
        self.add_run(m, "completed", "failure", workflow="Workflow conformance", file="conformance.yml")
        self.assertPending(outcome)
        self.assertPending(self.preflight(), "not reported yet")
        run_id = self.add_run(m, "queued")
        self.assertPending(self.preflight(), f"run {run_id} (queued)")
        self.finish_run(run_id, "success")
        self.tag("v1.0.0", m)
        url = self.publish("v1.0.0")
        self.assertStopped(self.preflight(), {"state": "ALREADY_RELEASED", "version": "1.0.0", "tag": "v1.0.0",
                                              "url": url})
        self.assertEqual(set(self.workflows_read()), {"main.yml"})
        self.assertNotIn("conformance", " ".join(" ".join(argv) for argv in self.gh_calls()))

    def test_the_snapshot_names_the_publishing_workflow(self) -> None:
        self.policy_data = lambda: release_wait_policy(release_workflow="release.yml")
        self.fresh()
        a, m, outcome = self.merge()
        self.add_run(m, "completed", "failure")  # main.yml: not the publishing workflow here
        self.assertPending(self.preflight(), "the release.yml run(s)", "not reported yet")
        run_id = self.add_run(m, "completed", "failure", workflow="Release", file="release.yml")
        self.assertFailed(self.preflight(), f"{RUNS_URL}/{run_id}")
        self.assertEqual(set(self.workflows_read()), {"release.yml"})

    def test_a_failed_publishing_run_fails_once_per_detail(self) -> None:
        a, m, outcome = self.merge()
        run_id = self.add_run(m, "completed", "failure")
        gate = self.assertFailed(self.preflight(), f"the main.yml run(s) for {m} did not succeed",
                                 f"run {run_id} (completed, failure) {RUNS_URL}/{run_id}")
        self.assertEqual(gate.exits[0], f"re-run the failed jobs of {RUNS_URL}/{run_id} on GitHub (the release "
                                        f"transaction resumes safely)")
        self.assertEqual(self.preflight(), gate)
        self.assertEqual(len(self.events_named("release_failed")), 1)
        self.assertEqual(self.head().branch, BRANCH)  # not closed out
        # A re-run that publishes settles it.
        self.tag("v1.0.0", m)
        self.publish("v1.0.0")
        self.assertEqual(self.preflight().action, "closed_out")

    def test_a_torn_event_line_does_not_stop_the_release_wait(self) -> None:
        a, m, outcome = self.merge()
        self.add_run(m, "completed", "failure")
        gate = self.preflight()
        self.assertEqual(len(self.events_named("release_failed")), 1)
        path = self.rt / mb.events_rel(self.key, WID)
        with path.open("ab") as log:
            log.write(b'{"at": "2026-10-02T00:00:00Z", "event": "rel\n[]\n')  # a torn line, then a non-object
        self.assertEqual(self.preflight(), gate)
        # The log is presentation: the event may be written once more, never a crash.
        self.assertLessEqual(len([line for line in path.read_text().splitlines()
                                  if '"release_failed"' in line]), 2)

    def test_a_successful_run_that_published_nothing_fails(self) -> None:
        a, m, outcome = self.merge()
        self.add_run(m, "completed", "success")
        gate = self.assertFailed(self.preflight(), f"the publishing workflow main.yml completed successfully "
                                                   f"for {m} and published nothing")
        # No run failed, so no gate text may say one did (functional review F2).
        text = decision.branch_human_gate("repo", gate).what_is_required
        self.assertIn("did not publish", text)
        self.assertIn("succeeded without publishing", text)
        self.assertNotIn("release failed", text)
        self.assertNotIn("its release failed", gate.message)

    def test_the_re_classify_race_settles(self) -> None:
        a, m, outcome = self.merge()
        run_id = self.add_run(m, "completed", "success")
        real = release_txn.classify
        calls = []

        def classify(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 2:  # the publish finished between the two reads
                self.tag("v1.0.0", m)
                self.publish("v1.0.0")
            return real(*args, **kwargs)

        with mock.patch.object(release_txn, "classify", side_effect=classify):
            outcome = self.preflight()
        self.assertEqual(calls, [m, m])
        self.assertEqual(outcome.action, "closed_out")
        self.assertEqual(self.record()["release"]["state"], "ALREADY_RELEASED")
        del run_id

    def test_a_failing_classification_and_a_refusal_fail(self) -> None:
        self.tag("v2.0.0", self.t)
        a, m, outcome = self.merge()
        self.assertFailed(outcome, "INVALID_TRANSITION: 1.0.0 is not above v2.0.0")
        self.assertEqual(self.calls("run", "list"), [])
        with mock.patch.object(release_txn, "classify", side_effect=ReleaseTransactionError("no trunk")):
            self.assertFailed(self.preflight(), f"classifying {m} refused: no trunk")
        self.assertEqual(len(self.events_named("release_failed")), 2)

    def test_a_tag_off_the_trunk_is_a_failed_release(self) -> None:
        human = self.human()
        run(["git", "switch", "-q", "-c", "side"], cwd=human)
        (human / "side.txt").write_text("side\n")
        side = commit_all(human, "side")
        run(["git", "push", "-q", "origin", "side"], cwd=human)
        self.tag("v1.0.0", side)
        self.publish("v1.0.0")
        a, m, outcome = self.merge()
        self.assertFailed(outcome, f"COLLISION_TAG_ELSEWHERE: v1.0.0 exists at {side}")

    def test_classify_runs_with_the_preflights_git_runner(self) -> None:
        self.ctx = dataclasses.replace(self.ctx, runner=gitrepo.subprocess_runner(None))
        with mock.patch.object(release_txn, "classify", wraps=release_txn.classify) as spy:
            self.merge()
        self.assertTrue(spy.call_args_list)
        for call in spy.call_args_list:
            self.assertIs(call.args[0].git_runner, self.ctx.runner)
            self.assertEqual(call.kwargs, {"verify_assets": False})

    def test_the_trunk_side_after_a_manual_switch(self) -> None:
        a, m, outcome = self.merge()
        self.assertPending(outcome)
        self.git("switch", "-q", "main")
        run_id = self.add_run(m)
        self.assertPending(self.preflight(), f"run {run_id} (in_progress)")
        self.finish_run(run_id, "success")
        self.tag("v1.0.0", m)
        url = self.publish("v1.0.0")
        self.assertStopped(self.preflight(), {"state": "ALREADY_RELEASED", "version": "1.0.0", "tag": "v1.0.0",
                                              "url": url})
        self.assertEqual(self.events_named("closed")[-1]["side"], "trunk")
        self.assertEqual(self.head().branch, "main")

    def test_predict(self) -> None:
        a, m, outcome = self.merge()
        prediction = mb.predict(self.ctx)
        self.assertEqual((prediction["action"], prediction["binding_state"]), ("wait_release", mb.MERGED_SQUASHED))
        self.assertIn(f"classifies the squash commit {m}", prediction["detail"])
        self.git("switch", "-q", "main")
        self.assertEqual(mb.predict(self.ctx)["action"], "wait_release")
        self.seed(mb.MERGED_SQUASHED, release={"state": mb.RELEASE_NONE})
        prediction = mb.predict(self.ctx)
        self.assertEqual(prediction["action"], "close_out")
        self.assertIn("the step then stops (release: NONE", prediction["detail"])


class SupersededReleaseTest(_ReleaseWait):
    """A later trunk run publishes, or can still publish, the release that
    covers ``m`` (D.3's superseded and recovery rows)."""

    def test_m_s_pending_run_cancelled_by_a_later_push_that_publishes_at_the_descendant(self) -> None:
        a, m, outcome = self.merge()
        mine = self.add_run(m, "queued")
        self.assertPending(self.preflight())
        d = self.push_trunk("later.txt")
        self.finish_run(mine, "cancelled")
        self.assertPending(self.preflight(), f"the main.yml run(s) for {m} ended without publishing",
                           f"the run(s) for {d} on the trunk can still publish", "not reported yet")
        later = self.add_run(d)
        self.assertPending(self.preflight(), f"run {later} (in_progress)")
        self.tag("v1.0.0", d)
        url = self.publish("v1.0.0")
        self.finish_run(later, "success")
        self.assertStopped(self.preflight(), {"state": mb.RELEASE_SUPERSEDED, "version": "1.0.0", "tag": "v1.0.0",
                                              "url": url, "commit": d})
        self.assertEqual(self.head(), gitrepo.HeadState("main", d))

    def test_the_same_with_the_descendant_raising_the_version(self) -> None:
        a, m, outcome = self.merge()
        mine = self.add_run(m, "queued")
        d = self.push_trunk("later.txt", version="1.1.0")
        self.finish_run(mine, "cancelled")
        later = self.add_run(d)
        self.assertPending(self.preflight(), f"run {mine} (completed, cancelled)", f"run {later} (in_progress)")
        self.tag("v1.1.0", d)
        url = self.publish("v1.1.0")
        self.finish_run(later, "success")
        self.assertStopped(self.preflight(), {"state": mb.RELEASE_SUPERSEDED, "version": "1.1.0", "tag": "v1.1.0",
                                              "url": url, "commit": d})

    def test_a_failed_run_of_m_recovered_by_a_later_run_at_the_descendant(self) -> None:
        a, m, outcome = self.merge()
        mine = self.add_run(m, "completed", "failure")
        d = self.push_trunk("later.txt")
        later = self.add_run(d)
        gate = self.assertPending(self.preflight(), f"run {mine} (completed, failure)", f"run {later} (in_progress)")
        self.assertIn(f"re-run the failed jobs of {RUNS_URL}/{mine} on GitHub (the release transaction resumes "
                      f"safely)", gate.exits)
        self.assertEqual(self.events_named("release_failed"), [])
        self.tag("v1.0.0", d)
        url = self.publish("v1.0.0")
        self.finish_run(later, "success")
        self.assertStopped(self.preflight(), {"state": mb.RELEASE_SUPERSEDED, "version": "1.0.0", "tag": "v1.0.0",
                                              "url": url, "commit": d})

    def test_a_later_run_resumes_m_s_own_tag(self) -> None:
        a, m, outcome = self.merge()
        self.tag("v1.0.0", m)  # m's run tagged, then failed
        mine = self.add_run(m, "completed", "failure")
        d = self.push_trunk("later.txt")
        later = self.add_run(d)
        self.assertPending(self.preflight(), f"run {mine} (completed, failure)", f"run {later} (in_progress)")
        url = self.publish("v1.0.0")
        self.finish_run(later, "success")
        self.assertStopped(self.preflight(), {"state": "ALREADY_RELEASED", "version": "1.0.0", "tag": "v1.0.0",
                                              "url": url})

    def test_a_covering_tag_left_unpublished_then_a_newer_run(self) -> None:
        for publishes in (True, False):
            with self.subTest(publishes=publishes):
                self.fresh()
                a, m, outcome = self.merge()
                mine = self.add_run(m, "queued")
                d1 = self.push_trunk("first.txt")
                self.finish_run(mine, "cancelled")
                self.tag("v1.0.0", d1)
                first = self.add_run(d1, "completed", "failure")
                self.assertFailed(self.preflight(), f"run {first} (completed, failure)")
                d2 = self.push_trunk("second.txt")
                second = self.add_run(d2)
                self.assertPending(self.preflight(), f"run {first} (completed, failure)",
                                   f"the run(s) for {d2} on the trunk can still publish", f"run {second} (in_progress)")
                self.finish_run(second, "success")
                if publishes:
                    url = self.publish("v1.0.0")
                    self.assertEqual(self.preflight().action, "closed_out")
                    self.assertEqual(self.record()["release"], {"state": mb.RELEASE_SUPERSEDED, "version": "1.0.0",
                                                                "tag": "v1.0.0", "url": url, "commit": d1})
                    self.assertEqual(len(self.events_named("release_failed")), 1)  # the gate at d1, before d2
                else:
                    self.assertFailed(self.preflight(), f"run {first} (completed, failure)",
                                      f"completed successfully for {d2} and published nothing",
                                      f"run {second} (completed, success)")

    def test_no_run_of_m_reported_and_a_later_run_publishes_at_the_descendant(self) -> None:
        for version in (None, "1.1.0"):
            with self.subTest(version=version):
                self.fresh()
                a, m, outcome = self.merge()
                self.assertPending(outcome, "not reported yet")
                d = self.push_trunk("later.txt", version=version)
                self.add_run(d, "completed", "success")
                tag = f"v{version or '1.0.0'}"
                self.tag(tag, d)
                url = self.publish(tag)
                # No run of m is ever reported.
                self.assertEqual([run for run in self.gh()["runs"] if run["headSha"] == m], [])
                self.assertStopped(self.preflight(), {"state": mb.RELEASE_SUPERSEDED, "version": version or "1.0.0",
                                                      "tag": tag, "url": url, "commit": d})

    def test_no_later_trunk_commit_fails_at_once(self) -> None:
        for conclusion in ("failure", "cancelled"):
            with self.subTest(conclusion=conclusion):
                self.fresh()
                a, m, outcome = self.merge()
                mine = self.add_run(m, "completed", conclusion)
                self.assertEqual(self.origin_ref("refs/heads/main"), m)
                self.assertFailed(self.preflight(), f"run {mine} (completed, {conclusion})")


class ReleaseWaitSkippedTest(_ReleaseWait):
    def test_a_hand_merge_commit_skips_the_wait_and_closes_out_as_1_5_0(self) -> None:
        number, a = self.to_pending()
        h = self.human_merge(number, how="merge")
        outcome = self.preflight()
        m = self.origin_ref("refs/heads/main")
        self.assertEqual(outcome, mb.Proceed(action="closed_out", base=m))
        record = self.record()
        self.assertEqual((record["state"], record["merged_head"]), (mb.CLOSED, h))
        self.assertEqual(record["release"], {"state": mb.RELEASE_SKIPPED, "reason": "not a verified squash merge"})
        self.assertEqual([e["merged_state"] for e in self.events_named("release_wait_skipped")], [mb.MERGED])
        self.assertEqual(self.calls("run", "list"), [])

    def test_a_rebase_merge_is_merged_rewritten_without_a_wait(self) -> None:
        number, a = self.to_pending()
        self.human_merge(number, how="rebase")
        self.assertGate(self.preflight(), mb.GATE_MERGE_METHOD_REWROTE_HISTORY)
        self.assertEqual(self.record()["state"], mb.MERGED_REWRITTEN)
        self.assertNotIn("release", self.record())
        self.assertEqual(self.calls("run", "list"), [])
        self.assertGate(self.preflight(), mb.GATE_SWITCH_TO_TRUNK)


class ReleaseWaitUnchangedTest(_SquashClose):
    def test_a_binding_without_the_key_closes_out_and_starts_the_trunk(self) -> None:
        a = self.to_ready()
        m = self.squash_merge(self.pr_number())
        self.assertEqual(self.preflight(), mb.Proceed(action="closed_out", base=m))
        self.assertNotIn("release", self.record())
        self.assertEqual(self.calls("run", "list"), [])
        self.assertFalse(mb.stops_after_close(self.record()))


class _Clock:
    """A fake monotonic clock and sleep for :func:`mb.waiting_preflight`:
    each sleep advances the clock by its length and then runs the next
    scripted change on GitHub's side, if any."""

    def __init__(self, *changes: Callable[[], None]) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.changes = list(changes)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.changes:
            self.changes.pop(0)()


class WaitingPreflightTest(_ReleaseWait):
    """``run``'s bounded wait (``workflow-controller-auto-merge-release-wait``
    CP5, Design E): the preflight polled while it returns a waitable gate."""

    def wait(self, clock: _Clock, *, wait_seconds: int = 3600, poll_seconds: int = 30):
        self.waits: list[tuple[str, str]] = []
        ctx = mb.Context(repo_root=self.ctx.repo_root, runtime_root=self.ctx.runtime_root,
                         forge_factory=self.ctx.forge_factory, clock=self.ctx.clock,
                         wait_seconds=wait_seconds, poll_seconds=poll_seconds)
        return mb.waiting_preflight(ctx, on_wait=lambda code, deadline: self.waits.append((code, deadline)),
                                    sleep=clock.sleep, monotonic=clock.monotonic)

    def test_one_step_waits_from_pending_checks_to_the_release_and_stops(self) -> None:
        number = self.open_pr()
        a = self.accept()
        published: dict[str, str] = {}

        def release() -> None:
            m = self.origin_ref("refs/heads/main")
            self.add_run(m, "completed", "success")
            self.tag("v1.0.0", m)
            published["url"] = self.publish("v1.0.0")

        clock = _Clock(lambda: (self.set_checks(number, ("ci", "pass")),
                                self.gh_edit(number, mergeStateStatus="UNKNOWN")),
                       lambda: self.gh_edit(number, mergeStateStatus="CLEAN"),
                       release)
        outcome = self.wait(clock)
        record = self.assertStopped(outcome, {"state": "ALREADY_RELEASED", "version": "1.0.0", "tag": "v1.0.0",
                                              "url": published["url"]})
        self.assert_closed(a, self.origin_ref("refs/heads/main"))
        self.assertEqual(clock.sleeps, [30, 30, 30])
        deadline = "2026-09-25T01:00:00Z"  # the clock's time plus merge.wait_seconds
        self.assertEqual(self.waits, [(mb.GATE_CHECKS_PENDING, deadline), (mb.GATE_MERGE_PENDING, deadline),
                                      (mb.GATE_RELEASE_PENDING, deadline)])
        self.assertEqual(len(self.merges()), 1)
        self.assertEqual(record["merge"]["state"], "accepted")

    def test_the_budget_is_one_per_step_and_returns_the_last_gate(self) -> None:
        number, a = self.to_pending()
        clock = _Clock(*[lambda: None] * 2, lambda: self.gh_edit(number, mergeStateStatus="UNKNOWN"),
                       lambda: self.set_checks(number, ("ci", "pass")))
        gate = self.assertGate(self.wait(clock, wait_seconds=100), mb.GATE_MERGE_PENDING)
        self.assertTrue(gate.waitable)
        # Each sleep is at most the poll, and together they never pass the budget.
        self.assertEqual(clock.sleeps, [30, 30, 30, 10])
        # merge_pending appeared at the deadline: returned, never waited on, so never announced.
        self.assertEqual([code for code, _ in self.waits], [mb.GATE_CHECKS_PENDING])
        self.assertEqual(self.merges(), [])

    def test_a_gate_seen_again_is_announced_once(self) -> None:
        number, a = self.to_pending()
        clock = _Clock()
        self.assertGate(self.wait(clock, wait_seconds=90), mb.GATE_CHECKS_PENDING)
        self.assertEqual(clock.sleeps, [30, 30, 30])
        self.assertEqual([code for code, _ in self.waits], [mb.GATE_CHECKS_PENDING])

    def test_ctrl_c_during_the_sleep_is_a_wait_interrupted(self) -> None:
        self.to_pending()
        clock = _Clock()

        def interrupted(seconds: float) -> None:
            raise KeyboardInterrupt

        clock.sleep = interrupted  # type: ignore[method-assign]
        with self.assertRaises(mb.WaitInterrupted) as caught:
            self.wait(clock)
        self.assertIsInstance(caught.exception, KeyboardInterrupt)
        self.assertEqual(caught.exception.code, mb.GATE_CHECKS_PENDING)
        self.assertEqual(self.waits[0][0], mb.GATE_CHECKS_PENDING)
        self.assertIn(mb.GATE_CHECKS_PENDING, caught.exception.message())
        self.assertEqual(self.merges(), [])

    def test_ctrl_c_during_the_re_read_between_sleeps_is_a_wait_interrupted(self) -> None:
        self.to_pending()
        clock = _Clock()
        real = mb.repository_preflight
        calls: list[int] = []

        def preflight(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real(*args, **kwargs)

        with mock.patch.object(mb, "repository_preflight", preflight):
            with self.assertRaises(mb.WaitInterrupted) as caught:
                self.wait(clock)
        self.assertEqual(clock.sleeps, [30])
        self.assertEqual(caught.exception.code, mb.GATE_CHECKS_PENDING)

    def test_zero_seconds_never_sleeps(self) -> None:
        self.to_pending()
        clock = _Clock()
        gate = self.assertGate(self.wait(clock, wait_seconds=0), mb.GATE_CHECKS_PENDING)
        self.assertTrue(gate.waitable)
        self.assertEqual((clock.sleeps, self.waits), ([], []))

    def test_a_gate_that_is_not_waitable_returns_at_once(self) -> None:
        number, a = self.to_pending()
        for checks, code in ((("ci", "fail"), mb.GATE_CHECKS_FAILING), (("ci", "cancel"), mb.GATE_CHECKS_CANCELLED)):
            with self.subTest(code=code):
                self.set_checks(number, checks)
                clock = _Clock()
                gate = self.assertGate(self.wait(clock), code)
                self.assertFalse(gate.waitable)
                self.assertEqual((clock.sleeps, self.waits), ([], []))
        self.set_checks(number, ("ci", "pass"))
        self.gh_edit(number, mergeStateStatus="BEHIND")
        clock = _Clock()
        self.assertFalse(self.assertGate(self.wait(clock), mb.GATE_INTEGRATION_REQUIRED).waitable)
        self.assertEqual(clock.sleeps, [])

    def test_readiness_waits_on_a_lagging_pull_request_head(self) -> None:
        number = self.open_pr()
        a = self.accept()
        view = dict(self.gh_pr_view(number), headRefOid=self.record()["branch_point"], mergeStateStatus="CLEAN")
        view = {k: view[k] for k in (*fake_gh_fields(), "mergeStateStatus")}
        # The cell's own read, then readiness's: both still show the old head.
        self.gh_edit(number, lagged_reads=2, lagged_view=view)
        gate = self.assertGate(self.preflight(), mb.GATE_PR_HEAD_NOT_ACCEPTED)
        self.assertTrue(gate.waitable)
        self.assertEqual(self.record()["state"], mb.PR_OPEN)


class ReadinessNotWaitableTest(_SquashClose):
    def test_without_auto_merge_readiness_gates_are_not_waitable(self) -> None:
        number = self.open_pr()
        self.accept()
        gate = self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING)
        self.assertFalse(gate.waitable)

    def test_with_merge_auto_off_readiness_gates_are_not_waitable(self) -> None:
        self.write_policy(auto_merge_policy())
        commit_all(self.clone, "Opt in")
        self.push("main")
        self.ctx = mb.Context(repo_root=self.ctx.repo_root, runtime_root=self.ctx.runtime_root,
                              forge_factory=self.ctx.forge_factory, clock=self.ctx.clock, auto_merge=False)
        self.open_pr()
        self.accept()
        self.assertFalse(self.assertGate(self.preflight(), mb.GATE_CHECKS_PENDING).waitable)


def fake_gh_fields() -> tuple[str, ...]:
    from tests import fake_gh

    return fake_gh.PR_JSON_FIELDS


class AutoMergeUnchangedTest(_SquashClose):
    def test_a_binding_without_the_key_keeps_the_human_merge(self) -> None:
        a = self.to_ready()
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertNotIn("merge", self.record())
        self.assertEqual(mb.predict(self.ctx)["action"], "gate")
        self.assertEqual(self.preflight(), mb._merge_gate(self.record(), tip=a))


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

    def test_squash_texts_replace_only_their_own_codes(self) -> None:
        self.assertLessEqual(set(decision.BRANCH_GATE_TEXTS_SQUASH), set(mb.GATE_CODES))
        for code in mb.GATE_CODES:
            merge = decision.branch_human_gate("/repo", mb.Gate(code, WID, BRANCH, "m"))
            squash = decision.branch_human_gate("/repo", mb.Gate(code, WID, BRANCH, "m", merge_method="squash"))
            self.assertEqual(merge.what_is_required, f"{decision.BRANCH_GATE_TEXTS[code]} ({code}): m")
            if code in decision.BRANCH_GATE_TEXTS_SQUASH:
                self.assertIn("Squash and merge", squash.what_is_required)
            else:
                self.assertEqual(squash, merge)
        self.assertIn("Conventional Commit", decision.BRANCH_GATE_TEXTS[mb.GATE_PR_TITLE_INVALID])

    def test_selected_commands_are_unchanged(self) -> None:
        self.assertNotIn("milestone-binding", " ".join(decision.SELECTED_COMMANDS))


if __name__ == "__main__":
    unittest.main()
