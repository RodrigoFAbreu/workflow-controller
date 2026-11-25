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

from controller import job, protocol, usage

from tests import fixtures
from tests import test_protocol_lifecycle as lifecycle
from tests.test_review_job import _ReviewLife


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
        self.assertTrue(fixtures.has_snapshot(first.snapshot_key(name)))
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
        self.assertFalse(fixtures.has_snapshot(life.snapshot_key(name)))
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
        self.assertEqual(len({life.snapshot_key("row") for life in (plain, pending, reported)}), 3)

    def test_a_keys_parts_are_named(self) -> None:
        life = lifecycle.GateLifecycle(self)
        self.assertEqual(set(life.snapshot_inputs()), {"release", "report", "checks", "refused"})
        loop = lifecycle.LoopLifecycle(self)
        self.assertEqual(set(loop.snapshot_inputs()) - set(life.snapshot_inputs()),
                         {"acceptance", "world", "bound", "wait_seconds"})

    def test_a_part_not_declared_late_still_separates_keys(self) -> None:
        plain = lifecycle.GateLifecycle(self)
        pending = lifecycle.GateLifecycle(self)
        pending.checks.write_text("pending")
        self.assertNotEqual(plain.snapshot_key("row", late=("refused",)), pending.snapshot_key("row", late=("refused",)))
        self.assertEqual(plain.snapshot_key("row", late=("checks",)), pending.snapshot_key("row", late=("checks",)))

    def test_an_unknown_late_part_is_refused(self) -> None:
        with self.assertRaisesRegex(AssertionError, "unknown late part"):
            lifecycle.GateLifecycle(self).snapshot_key("row", late=("nope",))

    def test_the_world_of_a_loop_life_stays_in_the_key(self) -> None:
        pending = lifecycle.LoopLifecycle(self, checks="pending")
        green = lifecycle.LoopLifecycle(self, checks="green")
        self.assertNotEqual(pending.snapshot_key("row"), green.snapshot_key("row"))


class LatePartTest(SnapshotCase):
    """D4: a part the drive never reads is left out of the key, each life still sees its own value, and a drive that
    does read it fails loudly."""

    LATE = ("checks", "refused")

    def late_life(self, *, checks: str = "green", refused: tuple = ()) -> lifecycle.GateLifecycle:
        life = lifecycle.GateLifecycle(self)
        life.checks.write_text(checks)
        life.refused = set(refused)
        return life

    def test_two_lives_that_differ_only_in_a_late_part_share_one_snapshot_and_each_sees_its_own_value(self) -> None:
        name = self.key()
        first = self.late_life(checks="green")
        first.reach(name, lambda: (first.root / "marker.txt").write_text("built\n"), late=self.LATE)
        self.assertEqual(first.checks.read_text(), "green")
        self.release(first)
        second = self.late_life(checks="pending", refused=("plan",))
        second.reach(name, lambda: self.fail("the late parts are not in the key, so this restores"), late=self.LATE)
        self.assertEqual((second.root / "marker.txt").read_text(), "built\n")
        self.assertEqual(second.checks.read_text(), "pending")
        self.assertEqual(second.refused, {"plan"})
        self.assertEqual(second.checks, second.td / "checks")

    def test_the_drive_runs_with_the_canonical_value_and_the_life_gets_its_own_back(self) -> None:
        name = self.key()
        life = self.late_life(checks="pending", refused=("plan",))
        seen: list[tuple] = []
        life.reach(name, lambda: seen.append((life.checks.read_text(), set(life.refused))), late=self.LATE)
        self.assertEqual(seen, [("green", set())])
        self.assertEqual((life.checks.read_text(), life.refused), ("pending", {"plan"}))

    def test_a_drive_that_reads_a_late_part_fails_with_the_guards_message(self) -> None:
        name = self.key()
        life = self.late_life()
        with self.assertRaisesRegex(AssertionError, "declared 'refused' late but read it"):
            life.reach(name, lambda: life.late("refused"), late=self.LATE)
        self.assertEqual(life.late("refused"), ())  # the guard is off again
        self.assertFalse(fixtures.has_snapshot(life.snapshot_key(name, self.LATE)))

    def test_a_drive_may_read_a_part_it_did_not_declare_late(self) -> None:
        life = self.late_life()
        seen: list[str] = []
        life.reach(self.key(), lambda: seen.append(life.late("checks")), late=("refused",))
        self.assertEqual(seen, ["green"])

    def test_an_effect_that_reads_refused_trips_the_guard_in_a_declaring_drive(self) -> None:
        life = self.late_life()
        life._late_running, life._late_row = frozenset({"refused"}), "row"
        with self.assertRaisesRegex(AssertionError, "read it"):
            life.effect("/satisfy-gate plan workflow-demo")


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


class ReviewRowTest(SnapshotCase):
    """D6: the review case's row (the manual plan gate, the clock it was driven at) is the same fresh and restored."""

    ROW = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"

    def drive(self, now: float, *, share: str | None) -> dict:
        """One review-case setUp at the clock ``now``: what a review test reads at its start, relative to its clock."""
        life = _ReviewLife(self, "2.9.0")
        life.review_now = now
        with mock.patch.object(job, "usage_now", lambda: life.review_now):
            if share is None:
                life.run_until(self.ROW)
            else:
                life.reach(share, lambda: life.run_until(self.ROW))
            clock = life.review_now
        files = sorted(str(p.relative_to(life.root)) for p in life.root.rglob("*") if ".git" not in p.parts)
        record = usage.load_record(life.runtime)

        def relative(value):
            if isinstance(value, dict):
                return {k: relative(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relative(v) for v in value]
            return round(value - clock, 3) if isinstance(value, float) else value

        shape = {"phase": life.phase(), "tasks": list(life.tasks), "files": files,
                 "row": protocol.next_action(life.root, lifecycle.WID).raw["row"], "clock": clock,
                 "usage": relative(record)}
        self.release(life)
        return shape

    def test_a_restored_review_row_reads_as_a_fresh_one_at_the_clock_it_was_driven_at(self) -> None:
        fresh = self.drive(1000.0, share=None)
        name = self.key()
        source = self.drive(1000.0, share=name)
        copy = self.drive(5000.0, share=name)  # a later test, whose own clock differs: the copy keeps the saved one
        self.assertEqual(source, fresh)
        self.assertEqual(copy["clock"], 1000.0)
        self.assertEqual(copy, fresh)
        self.assertEqual(copy["phase"], self.ROW)


class CountersTest(SnapshotCase):
    """``CI2c`` D1: every outcome of a snapshot call is counted once, by row name; the report is opt-in."""

    def counts(self, name: str) -> dict[str, int]:
        return fixtures.snapshot_counts().get(name, {})

    def test_a_built_a_missing_and_a_restored_row_are_each_counted_once(self) -> None:
        name = self.key()
        first = self.built(name)
        self.assertEqual(self.counts(name), {"missing": 1, "built": 1})
        self.release(first)
        lifecycle.GateLifecycle(self).reach(name, lambda: self.fail("must restore"))
        self.assertEqual(self.counts(name), {"missing": 1, "built": 1, "restored": 1})

    def test_a_path_in_use_and_the_drive_after_it_are_counted(self) -> None:
        name = self.key()
        self.release(self.built(name))
        lifecycle.GateLifecycle(self).reach(name, lambda: self.fail("must restore"))
        third = lifecycle.GateLifecycle(self)
        third.reach(name, lambda: None)
        self.assertEqual(self.counts(name), {"missing": 1, "built": 1, "restored": 1, "in_use": 1, "already_kept": 1})

    def test_a_failed_copy_is_counted_on_restore_and_on_take(self) -> None:
        name = self.key()
        self.release(self.built(name))
        second = lifecycle.GateLifecycle(self)
        with mock.patch.object(fixtures.shutil, "copytree", side_effect=OSError("disk full")):
            second.reach(name, lambda: None)
        self.assertEqual(self.counts(name), {"missing": 1, "built": 1, "copy_failed": 1, "already_kept": 1})
        other = self.key()
        with mock.patch.object(fixtures.shutil, "copytree", side_effect=OSError("disk full")):
            lifecycle.GateLifecycle(self).reach(other, lambda: None)
        self.assertEqual(self.counts(other), {"missing": 1, "unkept": 1})

    def test_a_kit_drive_is_counted_and_nothing_else(self) -> None:
        name = self.key()
        self.stub_dir = "/nonexistent"
        self.addCleanup(delattr, self, "stub_dir")
        lifecycle.GateLifecycle(self).reach(name, lambda: None)
        self.assertEqual(self.counts(name), {"kit_drove": 1})

    def test_the_counts_are_keyed_by_the_row_name_and_are_a_copy(self) -> None:
        one, two = self.key(), self.key()
        self.built(one)
        self.built(two)
        self.assertEqual(self.counts(one), self.counts(two))
        fixtures.snapshot_counts()[one]["built"] = 99
        self.assertEqual(self.counts(one)["built"], 1)

    def users(self, name: str) -> dict[str, list[str]]:
        return {label: ids for label, ids in fixtures.snapshot_users().items() if label.startswith(name + "#")}

    def test_a_test_id_is_recorded_for_the_key_it_reached_and_a_drive_and_a_restore_both_record(self) -> None:
        name = self.key()
        first = self.built(name)
        label = fixtures.snapshot_key_label(first.snapshot_key(name))
        self.assertEqual(self.users(name), {label: [self.id()]})
        self.release(first)
        lifecycle.GateLifecycle(self).reach(name, lambda: self.fail("must restore"))
        self.assertEqual(self.users(name), {label: [self.id()]})
        fixtures.note_snapshot_user(first.snapshot_key(name), "tests.other.Case.test_x")
        self.assertEqual(self.users(name), {label: sorted([self.id(), "tests.other.Case.test_x"])})

    def test_two_inputs_of_one_row_are_two_keys_with_two_labels(self) -> None:
        name = self.key()
        one = lifecycle.GateLifecycle(self)
        two = lifecycle.GateLifecycle(self)
        two.refused = {"plan"}
        one.reach(name, lambda: None)
        two.reach(name, lambda: None)
        labels = {fixtures.snapshot_key_label(one.snapshot_key(name)), fixtures.snapshot_key_label(two.snapshot_key(name))}
        self.assertEqual(len(labels), 2)
        self.assertEqual(set(self.users(name)), labels)
        self.assertTrue(all(label.startswith(name + "#") for label in labels))

    def test_a_kit_drive_records_no_user(self) -> None:
        name = self.key()
        self.stub_dir = "/nonexistent"
        self.addCleanup(delattr, self, "stub_dir")
        lifecycle.GateLifecycle(self).reach(name, lambda: None)
        self.assertEqual(self.users(name), {})

    def test_the_label_is_the_same_in_two_processes(self) -> None:
        import os
        import subprocess
        import sys
        key = ("row-a", (("release", "2.12.0"), ("report", True)))
        code = ("import sys; sys.path.insert(0, '.'); from tests import fixtures; "
                "print(fixtures.snapshot_key_label(('row-a', (('release', '2.12.0'), ('report', True)))))")
        out = [subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parent.parent,
                              env={**os.environ, "PYTHONHASHSEED": seed}).stdout.strip() for seed in ("1", "2")]
        self.assertEqual(out[0], out[1])
        self.assertEqual(out[0], fixtures.snapshot_key_label(key))

    def test_the_report_gets_one_json_line_and_nothing_is_written_with_the_variable_unset(self) -> None:
        import json
        import os
        import tempfile
        name = self.key()
        self.built(name)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "report.jsonl"
            with mock.patch.dict(os.environ, {fixtures._REPORT_VARIABLE: str(target)}):
                fixtures._write_snapshot_report()
            lines = target.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["pid"], os.getpid())
            self.assertEqual(record["counts"][name], {"missing": 1, "built": 1})
            self.assertEqual(set(record), {"pid", "counts", "users"})
            self.assertIn(self.id(), [i for ids in record["users"].values() for i in ids])
            env = {k: v for k, v in os.environ.items() if k != fixtures._REPORT_VARIABLE}
            with mock.patch.dict(os.environ, env, clear=True):
                fixtures._write_snapshot_report()
            self.assertEqual(len(target.read_text().splitlines()), 1)
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()), ["report.jsonl"])


if __name__ == "__main__":
    unittest.main()
