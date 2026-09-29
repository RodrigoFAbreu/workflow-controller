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


if __name__ == "__main__":
    unittest.main()
