"""The shared lifecycle snapshot (``workflow-controller-ci-time`` CP4, D3, D4): a built repository is kept once per
test process and copied back for each later test that needs the same row.

The proofs here use a cheap drive of their own (one marker file) so that they do not depend on which lifecycle
tests ran before them in the process, and one proof drives the real plan gate both ways to show that a copy behaves
like a fresh build.
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

from controller import job, protocol

from tests import fixtures
from tests import test_protocol_lifecycle as lifecycle


class SnapshotCase(unittest.TestCase):
    def key(self) -> str:
        """A name no other test uses, so that the first life of the case always builds."""
        return "proof-" + uuid.uuid4().hex

    def built(self, name: str) -> lifecycle.GateLifecycle:
        life = lifecycle.GateLifecycle(self)
        life.reach(name, lambda: (life.root / "marker.txt").write_text("built\n"))
        return life

    def release(self, life: lifecycle.GateLifecycle) -> None:
        """End one life the way its test would (its cleanup removes the tree), so that the next copy can take the path."""
        shutil.rmtree(life.td, ignore_errors=True)


class CopyTest(SnapshotCase):
    def test_a_later_life_gets_the_built_tree_without_driving_again(self) -> None:
        name = self.key()
        first = self.built(name)
        first.tasks.append("review-plan")
        self.assertTrue(fixtures.has_snapshot((name, first.snapshot_inputs())))
        # The state was saved when the drive ended, so what the first test does afterwards is not in the copy.
        driven: list[bool] = []
        self.release(first)
        second = lifecycle.GateLifecycle(self)
        second.reach(name, lambda: driven.append(True))
        self.assertEqual(driven, [], "a snapshot was taken, so the copy needs no drive")
        self.assertEqual((second.root / "marker.txt").read_text(), "built\n")
        self.assertEqual(second.tasks, [])

    def test_a_copy_is_where_the_first_build_was_and_its_paths_are_its_own(self) -> None:
        name = self.key()
        first = self.built(name)
        built_at = first.td
        self.release(first)
        second = lifecycle.GateLifecycle(self)
        second.reach(name, lambda: self.fail("must restore"))
        # The tree sits at the path it was built at (a bundle records its worktree, so it cannot move), and every
        # path the life holds follows: nothing points at the directory the life seeded and dropped.
        self.assertEqual(second.td, built_at)
        for held in (second.root, second.runtime, second.stub, second.checks):
            self.assertTrue(str(held).startswith(str(built_at)), held)
            self.assertTrue(held.exists(), held)
        self.assertEqual(second.inspect().target_protocol, first.inspect().target_protocol)

    def test_a_write_to_one_copy_is_not_seen_by_the_next_or_by_the_snapshot(self) -> None:
        name = self.key()
        first = self.built(name)
        self.release(first)
        second = lifecycle.GateLifecycle(self)
        second.reach(name, lambda: self.fail("must restore"))
        (second.root / "marker.txt").write_text("changed by the second test\n")
        (second.root / "extra.txt").write_text("only in the second\n")
        (second.runtime / "left-over").write_text("x")
        second.tasks.append("only in the second")
        self.release(second)
        third = lifecycle.GateLifecycle(self)
        third.reach(name, lambda: self.fail("must restore"))
        self.assertEqual((third.root / "marker.txt").read_text(), "built\n")
        self.assertFalse((third.root / "extra.txt").exists())
        self.assertFalse((third.runtime / "left-over").exists())
        self.assertEqual(third.tasks, [])

    def test_a_second_live_copy_of_the_same_snapshot_builds_fresh_and_leaves_the_first_alone(self) -> None:
        name = self.key()
        first = self.built(name)
        self.release(first)
        second = lifecycle.GateLifecycle(self)
        second.reach(name, lambda: self.fail("must restore"))
        driven: list[bool] = []
        third = lifecycle.GateLifecycle(self)  # the path is in use: it is never overwritten
        third.reach(name, lambda: driven.append(True))
        self.assertEqual(driven, [True])
        self.assertNotEqual(third.td, second.td)
        self.assertEqual((second.root / "marker.txt").read_text(), "built\n")


class FallbackTest(SnapshotCase):
    def test_the_drive_runs_when_the_snapshot_cannot_be_copied_and_leaves_nothing_behind(self) -> None:
        name = self.key()
        first = self.built(name)
        built_at = first.td
        self.release(first)
        second = lifecycle.GateLifecycle(self)
        own = second.td
        with mock.patch.object(fixtures.shutil, "copytree", side_effect=OSError("disk full")):
            second.reach(name, lambda: (second.root / "marker.txt").write_text("driven\n"))
        self.assertEqual(second.td, own, "a failed copy leaves the life on its own tree")
        self.assertEqual((second.root / "marker.txt").read_text(), "driven\n")
        self.assertFalse(built_at.exists(), "the half-made copy was removed")

    def test_a_key_that_could_not_be_snapshotted_is_never_tried_again(self) -> None:
        name = self.key()
        life = lifecycle.GateLifecycle(self)
        with mock.patch.object(fixtures.shutil, "copytree", side_effect=OSError("disk full")):
            life.reach(name, lambda: None)
        self.assertFalse(fixtures.has_snapshot((name, life.snapshot_inputs())))
        later = lifecycle.GateLifecycle(self)
        driven: list[bool] = []
        later.reach(name, lambda: driven.append(True))
        self.assertEqual(driven, [True])

    def test_the_scenario_kit_always_drives(self) -> None:
        name = self.key()
        first = self.built(name)
        self.release(first)
        self.stub_dir = "/nonexistent"  # what the scenario kit's test object carries
        self.addCleanup(delattr, self, "stub_dir")
        life = lifecycle.GateLifecycle(self)
        driven: list[bool] = []
        life.reach(name, lambda: driven.append(True))
        self.assertEqual(driven, [True])


class InputsTest(SnapshotCase):
    def test_a_drive_that_starts_differently_has_a_different_snapshot(self) -> None:
        plain = lifecycle.GateLifecycle(self)
        pending = lifecycle.GateLifecycle(self)
        pending.checks.write_text("pending")  # the default is "green"
        reported = lifecycle.GateLifecycle(self)
        reported.report = "another"
        self.assertEqual(len({plain.snapshot_inputs(), pending.snapshot_inputs(), reported.snapshot_inputs()}), 3)


class FreshBuildTest(SnapshotCase):
    """A copy is indistinguishable, to a test, from a fresh build: the same row, the same launches, the same step."""

    def test_a_copy_of_the_plan_gate_steps_exactly_as_a_fresh_build_does(self) -> None:
        fresh = lifecycle.GateLifecycle(self)
        fresh.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan")
        fresh_row = protocol.next_action(fresh.root, lifecycle.WID).raw["row"]
        fresh_phase = fresh.phase()
        fresh_record = fresh.step()
        shapes = (fresh_row, fresh_phase, fresh.tasks, fresh_record["status"], fresh_record["protocol"]["action_id"],
                  fresh_record["transition_verified"], fresh.phase())

        name = self.key()
        source = lifecycle.GateLifecycle(self)
        source.reach(name, lambda: source.manual_gate("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "plan"))
        self.release(source)
        copy = lifecycle.GateLifecycle(self)
        copy.reach(name, lambda: self.fail("must restore"))
        copy_row = protocol.next_action(copy.root, lifecycle.WID).raw["row"]
        copy_phase = copy.phase()
        copy_record = copy.step()
        self.assertEqual((copy_row, copy_phase, copy.tasks, copy_record["status"], copy_record["protocol"]["action_id"],
                          copy_record["transition_verified"], copy.phase()), shapes)
        self.assertEqual(copy_record["status"], job.STATUS_FINISHED)
        # The bundle verifies at the copy's own path (it records the worktree it was generated in).
        manifest = (copy.root / ".ai-review" / lifecycle.WID / "current" / "MANIFEST.md").read_text()
        self.assertIn(f"worktree_root: {copy.root}", manifest)


if __name__ == "__main__":
    unittest.main()
