"""CI2f: what one lifecycle step costs in process starts, and the instrument that counts it.

``CounterTest`` pins ``fixtures.count_process_starts`` (D3): the kinds it counts, that it restores the hooked
``Popen`` and that it nests and sees a thread's starts. The later checkpoints add the tests of the savings.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import managed_repo, protocol  # noqa: E402
from controller.errors import DriftedInstallationError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests import test_protocol_lifecycle as lifecycle  # noqa: E402


class CounterTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        # A stand-in for a vendored interpreter run: the counter classifies by the script's name.
        self.protocol_script = self.tmp / "workflow_protocol.py"
        self.protocol_script.write_text("")
        self.stub = fixtures.write_stub_workflow_manager(self.tmp / "workflow-manager-stub")

    def test_counts_a_git_call(self) -> None:
        with fixtures.count_process_starts() as counts:
            subprocess.run(["git", "--version"], check=True, capture_output=True)
        self.assertEqual(dict(counts), {"git": 1})

    def test_counts_an_interpreter_run_by_operation(self) -> None:
        with fixtures.count_process_starts() as counts:
            for operation in ("describe", "next-action", "next-action"):
                subprocess.run([sys.executable, "-B", "-E", "-s", str(self.protocol_script), operation],
                               check=True, capture_output=True, timeout=60)
        self.assertEqual(dict(counts), {"protocol:describe": 1, "protocol:next-action": 2})

    def test_skips_the_global_options_before_the_operation(self) -> None:
        with fixtures.count_process_starts() as counts:
            subprocess.run([sys.executable, str(self.protocol_script), "--protocol-major", "1", "--repo-root",
                            str(self.tmp), "reconcile"], capture_output=True, timeout=60)
        self.assertEqual(dict(counts), {"protocol:reconcile": 1})

    def test_counts_a_stub_manager_run(self) -> None:
        with fixtures.count_process_starts() as counts:
            subprocess.run([str(self.stub), "verify"], check=True, capture_output=True, timeout=60)
        self.assertEqual(dict(counts), {"manager": 1})

    def test_counts_any_other_program_as_other(self) -> None:
        with fixtures.count_process_starts() as counts:
            subprocess.run([sys.executable, "-c", "pass"], check=True, timeout=60)
        self.assertEqual(dict(counts), {"other": 1})

    def test_restores_popen_on_exit_and_on_error(self) -> None:
        original = subprocess.Popen.__init__
        with fixtures.count_process_starts():
            self.assertIsNot(subprocess.Popen.__init__, original)
        self.assertIs(subprocess.Popen.__init__, original)
        with self.assertRaises(RuntimeError):
            with fixtures.count_process_starts():
                raise RuntimeError("boom")
        self.assertIs(subprocess.Popen.__init__, original)

    def test_lets_every_start_through_unchanged(self) -> None:
        with fixtures.count_process_starts():
            done = subprocess.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout), (0, "hi\n"))

    def test_blocks_nest_and_each_sees_its_own_starts(self) -> None:
        with fixtures.count_process_starts() as outer:
            subprocess.run(["git", "--version"], check=True, capture_output=True)
            with fixtures.count_process_starts() as inner:
                subprocess.run(["git", "--version"], check=True, capture_output=True)
            subprocess.run(["git", "--version"], check=True, capture_output=True)
        self.assertEqual(inner["git"], 1)
        self.assertEqual(outer["git"], 3)

    def test_a_start_inside_a_thread_is_counted(self) -> None:
        with fixtures.count_process_starts() as counts:
            worker = threading.Thread(
                target=lambda: subprocess.run(["git", "--version"], check=True, capture_output=True))
            worker.start()
            worker.join()
        self.assertEqual(counts["git"], 1)


def _answer(row: str) -> SimpleNamespace:
    return SimpleNamespace(raw={"row": row})


class DecidedRowTest(unittest.TestCase):
    """``Lifecycle.decided_row`` (D1): the row a step decided is read from the step's own ``next-action`` call."""

    def block(self, answers, drive=lambda stub: None, *, raising: bool = False):
        """Run ``decided_row`` over a stub life, ``protocol.next_action`` faked to answer ``answers`` in order, and
        ``drive`` standing in for the step; return the rows appended and the calls the fake saw."""
        seen: list[tuple] = []
        rows_iter = iter(answers)

        def fake(*args, **kwargs):
            seen.append((args, kwargs))
            return _answer(next(rows_iter))

        stub = SimpleNamespace(root=Path("root"))
        rows: list[str] = []
        with mock.patch.object(protocol, "next_action", fake):
            try:
                with lifecycle.Lifecycle.decided_row(stub, rows):
                    drive(stub)
                    if raising:
                        raise RuntimeError("step failed")
            except RuntimeError:
                pass
        return rows, seen

    def test_the_identity_bound_call_is_not_mistaken_for_the_decision(self) -> None:
        def drive(stub):
            protocol.next_action(stub.root, lifecycle.WID, expected_digests={})
            protocol.next_action(stub.root, lifecycle.WID, expect_state_identity="id", expected_digests={})

        rows, seen = self.block(["38a", "38b"], drive)
        self.assertEqual(rows, ["38a"])
        self.assertEqual(len(seen), 2)  # every call passed through, and no extra query was made

    def test_a_call_with_the_identity_first_is_skipped(self) -> None:
        def drive(stub):
            protocol.next_action(stub.root, lifecycle.WID, expect_state_identity="id")
            protocol.next_action(stub.root, lifecycle.WID)

        rows, _ = self.block(["currency", "decision"], drive)
        self.assertEqual(rows, ["decision"])

    def test_calls_pass_through_unchanged(self) -> None:
        def drive(stub):
            protocol.next_action(stub.root, lifecycle.WID, expected_digests={"a": "b"}, timeout=3.0)

        _, seen = self.block(["x"], drive)
        self.assertEqual(seen, [((Path("root"), lifecycle.WID), {"expected_digests": {"a": "b"}, "timeout": 3.0})])

    def test_a_step_that_never_decides_asks_directly_afterwards(self) -> None:
        rows, seen = self.block(["after"])
        self.assertEqual(rows, ["after"])
        self.assertEqual(seen, [((Path("root"), lifecycle.WID), {})])

    def test_a_step_that_raises_still_appends_its_decided_row(self) -> None:
        def drive(stub):
            protocol.next_action(stub.root, lifecycle.WID)

        rows, _ = self.block(["38c"], drive, raising=True)
        self.assertEqual(rows, ["38c"])


class DecidedRowSavingTest(unittest.TestCase):
    """On a real lifecycle: the spy row equals the direct row, and costs exactly one ``next-action`` query less."""

    def test_the_spy_row_equals_the_direct_row_and_saves_one_query(self) -> None:
        spy_life = lifecycle.GateLifecycle(self)
        direct_life = lifecycle.GateLifecycle(self)
        with fixtures.count_process_starts() as spy_counts:
            spy_life.step()
        with fixtures.count_process_starts() as direct_counts:
            direct_life.rows.append(protocol.next_action(direct_life.root, lifecycle.WID).raw["row"])
            lifecycle.Lifecycle.step(direct_life)
        with fixtures.count_process_starts() as query_counts:
            protocol.next_action(spy_life.root, lifecycle.WID)
        self.assertEqual(spy_life.rows, direct_life.rows)
        self.assertEqual(query_counts["protocol:next-action"], 1)
        # The step with the spy starts exactly one protocol query (with its own isolation calls) fewer, and
        # nothing else differs: the production sequence of queries is unchanged.
        for kind in set(spy_counts) | set(direct_counts) | set(query_counts):
            self.assertEqual(direct_counts[kind] - spy_counts[kind], query_counts[kind], kind)


class InspectCacheTest(unittest.TestCase):
    """``fixtures.inspect_unchanged`` (D2): a repeated ``inspect`` of unchanged inputs starts no process, and
    anything the real ``inspect`` reads moves the key."""

    def setUp(self) -> None:
        fixtures._INSPECT_CACHE.clear()
        self.addCleanup(fixtures._INSPECT_CACHE.clear)
        self.life = lifecycle.GateLifecycle(self)
        self.root, self.stub = self.life.root, self.life.stub

    def cached(self):
        return fixtures.inspect_unchanged(self.root, str(self.stub))

    def fresh(self):
        return managed_repo.inspect(self.root, manager_bin=str(self.stub))

    def record(self) -> Path:
        return self.root / ".workflow-manager" / "installation.json"

    def assert_moved(self, change) -> None:
        """After ``change`` the answer is a real inspect again (processes start) and equals a fresh one."""
        self.cached()
        change()
        with fixtures.count_process_starts() as counts:
            answer = self.cached()
        self.assertGreater(sum(counts.values()), 0)
        self.assertEqual(answer, self.fresh())

    def test_a_second_call_starts_no_process_and_equals_a_fresh_inspect(self) -> None:
        first = self.cached()
        with fixtures.count_process_starts() as counts:
            second = self.cached()
        self.assertEqual(dict(counts), {})
        self.assertEqual(second, first)
        self.assertEqual(second, self.fresh())
        self.assertTrue(second.target_protocol)

    def test_the_key_moves_when_the_record_changes(self) -> None:
        def change() -> None:
            data = json.loads(self.record().read_text())
            data["note"] = "changed"
            self.record().write_text(json.dumps(data, indent=2))
        self.assert_moved(change)
        self.assertEqual(self.cached().manifest["note"], "changed")

    def test_the_key_moves_when_a_managed_script_changes(self) -> None:
        managed = json.loads(self.record().read_text())["managed"]
        script = self.root / protocol.qualifying_script_keys(managed)[0]
        before = self.cached().script_digests

        self.assert_moved(lambda: script.write_text(script.read_text() + "\n# changed\n"))
        self.assertNotEqual(self.cached().script_digests, before)

    def test_the_key_moves_when_the_stub_changes(self) -> None:
        self.assert_moved(lambda: fixtures.write_stub_workflow_manager(
            self.stub, verify_stdout="a different clean line", release="2.12.0"))

    def test_the_stubs_mode_bits_move_the_key(self) -> None:
        self.assert_moved(lambda: os.chmod(self.stub, 0o750))

    def test_a_refusal_is_raised_again_and_never_cached(self) -> None:
        good = self.cached()
        fixtures.write_stub_workflow_manager(self.stub, verify_exit=1, release="2.12.0")
        for _ in range(2):
            with self.assertRaises(DriftedInstallationError):
                self.cached()
        fixtures.write_stub_workflow_manager(self.stub, release="2.12.0")
        self.assertEqual(self.cached(), good)

    def test_two_roots_do_not_share_an_entry(self) -> None:
        other = lifecycle.GateLifecycle(self)
        mine = self.cached()
        theirs = fixtures.inspect_unchanged(other.root, str(other.stub))
        self.assertNotEqual(mine.root, theirs.root)
        self.assertEqual(fixtures.inspect_unchanged(other.root, str(other.stub)).root, theirs.root)
        self.assertEqual(self.cached().root, mine.root)

    def test_the_cached_result_is_a_copy(self) -> None:
        self.cached()
        served = self.cached()
        served.manifest["note"] = "mutated"
        served.verify["returncode"] = 99
        again = self.cached()
        self.assertNotIn("note", again.manifest)
        self.assertEqual(again.verify["returncode"], 0)
        self.assertEqual(again, self.fresh())
        self.assertTrue(dataclasses.is_dataclass(again) and copy.deepcopy(again) == again)


if __name__ == "__main__":
    unittest.main()
