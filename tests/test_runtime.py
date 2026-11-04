import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import runtime
from controller.errors import RuntimeContainmentError, RuntimeRootUnwritableError
from tests import fixtures


class ResolveRuntimeRootTest(unittest.TestCase):
    def test_row1_runtime_dir_wins_over_everything(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            explicit = Path(td) / "explicit"
            root, row = runtime.resolve_runtime_root(
                runtime_dir=str(explicit),
                origin_source_root=Path(td), runtime_kind="source",
                env={"WORKFLOW_CONTROLLER_HOME": str(Path(td) / "env-home")},
            )
            self.assertEqual(root, explicit.resolve())
            self.assertEqual(row, runtime.LADDER_RUNTIME_DIR)

    def test_row2_env_home_when_no_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_home = Path(td) / "env-home"
            root, row = runtime.resolve_runtime_root(
                runtime_dir=None, origin_source_root=Path(td), runtime_kind="source",
                env={"WORKFLOW_CONTROLLER_HOME": str(env_home)},
            )
            self.assertEqual(root, env_home.resolve())
            self.assertEqual(row, runtime.LADDER_ENV_HOME)

    def test_row3_origin_checkout_when_origin_is_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = Path(td)
            fixtures.git_init(origin)
            root, row = runtime.resolve_runtime_root(
                runtime_dir=None, origin_source_root=origin, runtime_kind="source", env={},
            )
            self.assertEqual(root, (origin / ".controller").resolve())
            self.assertEqual(row, runtime.LADDER_ORIGIN_CHECKOUT)

    def test_row3_is_source_only_a_package_or_unidentified_runtime_falls_to_row4(self) -> None:
        # The venv-inside-checkout case: the origin (site-packages) is
        # inside a Git work tree, but only a source runtime selects row 3.
        with tempfile.TemporaryDirectory() as td:
            checkout = Path(td) / "checkout"
            site_packages = checkout / ".venv" / "lib" / "python3.12" / "site-packages"
            site_packages.mkdir(parents=True)
            fixtures.git_init(checkout)
            xdg = Path(td) / "xdg-state"
            for kind in ("package", "unidentified"):
                root, row = runtime.resolve_runtime_root(
                    runtime_dir=None, origin_source_root=site_packages, runtime_kind=kind,
                    env={"XDG_STATE_HOME": str(xdg)},
                )
                self.assertEqual(root, (xdg / "workflow-controller").resolve(), kind)
                self.assertEqual(row, runtime.LADDER_XDG_STATE, kind)
            root, row = runtime.resolve_runtime_root(
                runtime_dir=None, origin_source_root=checkout, runtime_kind="source",
                env={"XDG_STATE_HOME": str(xdg)},
            )
            self.assertEqual(row, runtime.LADDER_ORIGIN_CHECKOUT)

    def test_row4_xdg_state_home_when_origin_not_a_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = Path(td) / "not-a-repo"
            origin.mkdir()
            xdg = Path(td) / "xdg-state"
            root, row = runtime.resolve_runtime_root(
                runtime_dir=None, origin_source_root=origin, runtime_kind="source", env={"XDG_STATE_HOME": str(xdg)},
            )
            self.assertEqual(root, (xdg / "workflow-controller").resolve())
            self.assertEqual(row, runtime.LADDER_XDG_STATE)

    def test_row4_falls_back_to_local_state_home_without_xdg(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            origin = Path(td) / "not-a-repo"
            origin.mkdir()
            root, row = runtime.resolve_runtime_root(
                runtime_dir=None, origin_source_root=origin, runtime_kind="source", env={},
            )
            self.assertEqual(root, (Path.home() / ".local" / "state" / "workflow-controller"))
            self.assertEqual(row, runtime.LADDER_XDG_STATE)


class EnsureRuntimeRootTest(unittest.TestCase):
    def test_creates_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "fresh" / "nested"
            runtime.ensure_runtime_root(root, ladder_row=1)
            self.assertTrue(root.is_dir())

    def test_unwritable_root_is_a_named_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "readonly-parent"
            parent.mkdir()
            parent.chmod(0o500)
            try:
                target = parent / "runtime"
                with self.assertRaises(RuntimeRootUnwritableError) as ctx:
                    runtime.ensure_runtime_root(target, ladder_row=4)
                self.assertEqual(ctx.exception.evidence["ladder_row"], 4)
                self.assertEqual(ctx.exception.evidence["path"], str(target))
            finally:
                parent.chmod(0o700)


class WriteJsonTest(unittest.TestCase):
    def test_containment_guard_refuses_a_write_outside_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            root.mkdir()
            with self.assertRaises(RuntimeContainmentError):
                runtime.write_json(root, "../escape.json", {"a": 1})
            self.assertFalse((Path(td) / "escape.json").exists())

    def test_write_then_read_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = runtime.write_json(root, "sub/record.json", {"a": 1, "b": [1, 2]})
            self.assertTrue(path.is_file())
            self.assertEqual(runtime.read_json(path), {"a": 1, "b": [1, 2]})

    def test_atomic_write_survives_a_simulated_interruption(self) -> None:
        """A write that fails mid-way (the `os.replace` never happens)
        leaves no half-written record at the target path, and the
        temporary it used does not survive either."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "record.json"
            target.write_text('{"pre-existing": true}')

            original_replace = os.replace

            def failing_replace(src, dst):  # noqa: ANN001
                raise OSError("simulated interruption")

            os.replace = failing_replace
            try:
                with self.assertRaises(OSError):
                    runtime.write_json(root, "record.json", {"new": True})
            finally:
                os.replace = original_replace

            self.assertEqual(json.loads(target.read_text()), {"pre-existing": True})
            leftovers = [p for p in root.iterdir() if p.name != "record.json"]
            self.assertEqual(leftovers, [])

    def test_read_json_returns_none_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(runtime.read_json(Path(td) / "nope.json"))


class RemoveTreeTest(unittest.TestCase):
    """``remove_tree`` never follows a link: a link above the last component is refused (#140)."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.base = Path(self._td.name).resolve()
        self.sandbox = self.base / "sandbox"
        self.dest = self.sandbox / "writable" / "build"
        (self.dest / "artifacts").mkdir(parents=True)
        (self.dest / "artifacts" / "keep.txt").write_text("destination")
        (self.sandbox / "clone").mkdir(parents=True)
        os.symlink(self.dest, self.sandbox / "clone" / "build")

    def destination_intact(self) -> None:
        self.assertEqual((self.dest / "artifacts" / "keep.txt").read_text(), "destination")

    def test_a_path_below_a_link_is_refused_and_the_destination_kept(self) -> None:
        with self.assertRaises(RuntimeContainmentError) as caught:
            runtime.remove_tree(self.sandbox, "clone/build/artifacts")
        self.assertEqual(caught.exception.evidence, {
            "path": str(self.sandbox / "clone" / "build" / "artifacts"),
            "root": str(self.sandbox),
            "link": str(self.sandbox / "clone" / "build"),
        })
        self.destination_intact()
        self.assertTrue((self.sandbox / "clone" / "build").is_symlink())

    def test_a_link_two_levels_up_is_refused(self) -> None:
        with self.assertRaises(RuntimeContainmentError):
            runtime.remove_tree(self.sandbox, "clone/build/artifacts/keep.txt")
        self.destination_intact()

    def test_a_dangling_link_above_the_last_component_is_refused(self) -> None:
        os.symlink(self.sandbox / "nowhere", self.sandbox / "dangling")
        with self.assertRaises(RuntimeContainmentError):
            runtime.remove_tree(self.sandbox, "dangling/x")

    def test_a_link_as_the_last_component_is_unlinked_and_its_destination_kept(self) -> None:
        inside = self.sandbox / "real"
        (inside / "f").mkdir(parents=True)
        os.symlink(inside, self.sandbox / "clone" / "alias")
        runtime.remove_tree(self.sandbox, "clone/alias")
        self.assertFalse(os.path.lexists(self.sandbox / "clone" / "alias"))
        self.assertTrue((inside / "f").is_dir())

    def test_a_dotdot_component_is_refused(self) -> None:
        (self.sandbox / "a").mkdir()
        (self.sandbox / "b").mkdir()
        with self.assertRaises(RuntimeContainmentError):
            runtime.remove_tree(self.sandbox, "a/../b")
        self.assertTrue((self.sandbox / "b").is_dir())

    def test_a_path_through_a_missing_component_is_a_no_op(self) -> None:
        runtime.remove_tree(self.sandbox, "missing/deeper/leaf")
        runtime.remove_tree(self.sandbox, "clone/absent")

    def test_keep_and_a_read_only_subtree_still_work(self) -> None:
        top = self.sandbox / "job"
        (top / "sessions").mkdir(parents=True)
        (top / "sessions" / "s.json").write_text("{}")
        (top / "ro" / "inner").mkdir(parents=True)
        (top / "ro" / "inner" / "f").write_text("x")
        os.chmod(top / "ro" / "inner", 0o500)
        os.chmod(top / "ro", 0o500)
        runtime.remove_tree(self.sandbox, "job", keep=("sessions",))
        self.assertEqual(sorted(p.name for p in top.iterdir()), ["sessions"])

    def test_a_plain_path_below_a_root_reached_through_a_link_is_removed(self) -> None:
        via = self.base / "via"
        os.symlink(self.sandbox, via)
        (self.sandbox / "plain" / "x").mkdir(parents=True)
        runtime.remove_tree(via, "plain/x")
        self.assertFalse((self.sandbox / "plain" / "x").exists())
        self.assertTrue((self.sandbox / "plain").is_dir())

    def test_a_root_that_is_a_link_is_unlinked_for_dot_and_empty(self) -> None:
        for rel in (".", ""):
            with self.subTest(rel=rel):
                holder = self.base / f"holder{len(rel)}"
                holder.mkdir()
                link = holder / "root"
                os.symlink(self.dest, link)
                runtime.remove_tree(link, rel)
                self.assertFalse(os.path.lexists(link))
                self.destination_intact()

    def test_a_plain_root_is_cleared_and_removed_for_dot(self) -> None:
        plain = self.base / "plainroot"
        (plain / "sub").mkdir(parents=True)
        (plain / "sub" / "f").write_text("x")
        runtime.remove_tree(plain, ".")
        self.assertFalse(plain.exists())

    def test_an_absolute_path_inside_the_root_is_removed_and_one_outside_refused(self) -> None:
        (self.sandbox / "inside" / "x").mkdir(parents=True)
        runtime.remove_tree(self.sandbox, self.sandbox / "inside" / "x")
        self.assertFalse((self.sandbox / "inside" / "x").exists())
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "f").write_text("x")
        with self.assertRaises(RuntimeContainmentError):
            runtime.remove_tree(self.sandbox, outside)
        self.assertTrue((outside / "f").is_file())


class FillLogFileTest(unittest.TestCase):
    def test_it_fills_an_existing_log_file_and_keeps_its_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            made = runtime.create_log_file(root, "jobs/j/worker.stdout")
            filled = runtime.fill_log_file(root, "jobs/j/worker.stdout", b"first output\n")
            self.assertEqual(filled, made)
            self.assertEqual(made.read_bytes(), b"first output\n")
            self.assertEqual(made.stat().st_mode & 0o777, 0o600)

    def test_it_refuses_an_absent_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                runtime.fill_log_file(Path(td), "jobs/j/worker.stdout", b"x")
            self.assertFalse((Path(td) / "jobs/j/worker.stdout").exists())

    def test_it_refuses_a_path_outside_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td) / "outside.log"
            outside.write_bytes(b"keep")
            root = Path(td) / "root"
            root.mkdir()
            with self.assertRaises(RuntimeContainmentError):
                runtime.fill_log_file(root, "../outside.log", b"x")
            self.assertEqual(outside.read_bytes(), b"keep")

    def test_it_does_not_follow_a_symlink_even_inside_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = runtime.create_log_file(root, "real.log")
            (root / "link.log").symlink_to(target)
            with self.assertRaises(OSError):
                runtime.fill_log_file(root, "link.log", b"x")
            self.assertEqual(target.read_bytes(), b"")


class UsageLockTest(unittest.TestCase):
    def test_usage_lock_is_exclusive_and_released_on_exit(self) -> None:
        import fcntl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with runtime.usage_lock(root) as lock_path:
                self.assertEqual(lock_path, root / runtime.USAGE_LOCK_NAME)
                probe = os.open(lock_path, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(probe)
            probe = os.open(root / runtime.USAGE_LOCK_NAME, os.O_RDWR)
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)


if __name__ == "__main__":
    unittest.main()
