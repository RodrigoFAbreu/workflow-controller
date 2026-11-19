"""CI2f: what one lifecycle step costs in process starts, and the instrument that counts it.

``CounterTest`` pins ``fixtures.count_process_starts`` (D3): the kinds it counts, that it restores the hooked
``Popen`` and that it nests and sees a thread's starts. The later checkpoints add the tests of the savings.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402


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
                               check=True, capture_output=True)
        self.assertEqual(dict(counts), {"protocol:describe": 1, "protocol:next-action": 2})

    def test_skips_the_global_options_before_the_operation(self) -> None:
        with fixtures.count_process_starts() as counts:
            subprocess.run([sys.executable, str(self.protocol_script), "--protocol-major", "1", "--repo-root",
                            str(self.tmp), "reconcile"], capture_output=True)
        self.assertEqual(dict(counts), {"protocol:reconcile": 1})

    def test_counts_a_stub_manager_run(self) -> None:
        with fixtures.count_process_starts() as counts:
            subprocess.run([str(self.stub), "verify"], check=True, capture_output=True)
        self.assertEqual(dict(counts), {"manager": 1})

    def test_counts_any_other_program_as_other(self) -> None:
        with fixtures.count_process_starts() as counts:
            subprocess.run([sys.executable, "-c", "pass"], check=True)
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
            done = subprocess.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
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


if __name__ == "__main__":
    unittest.main()
