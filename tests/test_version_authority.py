"""The single Controller version authority (trunk-branch-pr-release CP1).

``pyproject.toml``'s static ``[project].version`` is the only place the
version is written. A source runtime reads its own code root's
``pyproject.toml`` and never distribution metadata; a package runtime reads
the metadata of the distribution installed in its own code root,
cross-checked against ``BUILD_INFO.json``; a pinned child reads its own
``SOURCE_PIN.json``. The packaged ``--version``/``METADATA``/``BUILD_INFO``/
``tools/release.py version`` agreement is ``tests.test_packaged_runtime``
case 6, and ``tools/release.py`` following the checked-out ``pyproject.toml``
is ``tests.test_release_tools.CheckedOutVersionTest``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import handoff, identity, runtime, version  # noqa: E402
from controller.errors import SourceSnapshotError  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_identity import _running_from  # noqa: E402

VERSION = fixtures.CONTROLLER_VERSION
#: A version no real checkout of this repository declares.
OTHER = "5.5.5"


def _set_version(checkout: Path, value: str) -> None:
    path = checkout / "pyproject.toml"
    text = path.read_text()
    replaced = text.replace(f'version = "{VERSION}"', f'version = "{value}"', 1)
    assert replaced != text
    path.write_text(replaced)


def _version_line(code_root: Path, *, pythonpath: list[Path], cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != identity.EXEC_HANDOFF_ENV}
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in pythonpath)
    result = fixtures.run([sys.executable, "-P", "-B", "-m", "controller", "--version"],
                          cwd=cwd, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()[0]


class SingleAuthorityTest(unittest.TestCase):
    def test_pyproject_declares_a_static_version_and_no_dynamic_one(self) -> None:
        pyproject = tomllib.loads(fixtures.PYPROJECT.read_text())
        self.assertRegex(pyproject["project"]["version"], version.SEMVER_RE)
        self.assertNotIn("dynamic", pyproject["project"])
        self.assertNotIn("dynamic", pyproject.get("tool", {}).get("setuptools", {}))

    def test_no_version_assignment_remains_in_the_package(self) -> None:
        for path in sorted((fixtures.REPO_ROOT / "controller").glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                    targets = [node.target]
                for target in targets:
                    self.assertFalse(isinstance(target, ast.Name) and target.id == "__version__",
                                     f"{path.name}:{node.lineno} assigns __version__")

    def test_the_semver_patterns_agree(self) -> None:
        from controller import buildinfo
        self.assertEqual(version.SEMVER_RE.pattern, buildinfo.SEMVER_RE.pattern)


class ReadersTest(unittest.TestCase):
    def test_source_version_reads_the_static_version(self) -> None:
        self.assertEqual(version.source_version(fixtures.REPO_ROOT), VERSION)

    def test_source_version_refuses_a_missing_or_malformed_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for label, text in (
                ("missing file", None),
                ("not toml", "[project\n"),
                ("no static version", '[project]\nname = "x"\ndynamic = ["version"]\n'),
                ("not semver", '[project]\nversion = "1.2"\n'),
                ("not a string", "[project]\nversion = 1\n"),
            ):
                with self.subTest(label):
                    (root / "pyproject.toml").unlink(missing_ok=True)
                    if text is not None:
                        (root / "pyproject.toml").write_text(text)
                    with self.assertRaises(ValueError):
                        version.source_version(root)

    def test_package_version_needs_exactly_one_owning_distribution(self) -> None:
        def package_version(site: Path) -> str:
            # importlib.metadata caches a directory listing by its mtime,
            # which can stay put across these back-to-back writes.
            importlib.invalidate_caches()
            return version.package_version(site)

        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            # `Distribution.files` omits a RECORD entry that does not exist.
            (site / "controller").mkdir()
            (site / "controller" / "__init__.py").write_text("")
            with self.assertRaisesRegex(ValueError, "0 workflow-controller"):
                package_version(site)
            # A distribution whose RECORD does not list the package does not own it.
            fixtures.write_dist_info(site, "1.0.0", record=("other/__init__.py",))
            with self.assertRaisesRegex(ValueError, "0 workflow-controller"):
                package_version(site)
            # Another project's distribution never counts.
            fixtures.write_dist_info(site, "2.0.0", name="someone-else")
            fixtures.write_dist_info(site, "3.4.5")
            self.assertEqual(package_version(site), "3.4.5")
            fixtures.write_dist_info(site, "3.4.6")
            with self.assertRaisesRegex(ValueError, "2 workflow-controller"):
                package_version(site)


class SourceRuntimeTest(unittest.TestCase):
    def test_a_stale_egg_info_in_the_checkout_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            egg = checkout / "workflow_controller.egg-info"
            egg.mkdir()
            (egg / "PKG-INFO").write_text(f"Metadata-Version: 2.1\nName: workflow-controller\n"
                                          f"Version: {OTHER}\n\n")
            (egg / "SOURCES.txt").write_text("controller/__init__.py\n")
            # The hazard is real: metadata lookup on this path finds the stale one.
            self.assertEqual(
                [d.version for d in importlib.metadata.distributions(path=[str(checkout)])], [OTHER])

            resolved = identity.resolve_runtime(checkout)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_SOURCE)
            self.assertEqual(resolved.version, VERSION)
            self.assertEqual(_version_line(checkout, pythonpath=[checkout], cwd=Path(td)),
                             f"workflow-controller {VERSION}")

    def test_another_distribution_on_sys_path_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            site = Path(td) / "site"
            fixtures.write_dist_info(site, OTHER)
            self.assertEqual(_version_line(checkout, pythonpath=[site, checkout], cwd=Path(td)),
                             f"workflow-controller {VERSION}")

    def test_the_working_tree_pyproject_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _set_version(checkout, OTHER)
            self.assertEqual(identity.resolve_runtime(checkout).version, OTHER)
            with _running_from(checkout):
                self.assertEqual(identity.pin().version, OTHER)

    def test_an_unreadable_version_leaves_the_checkout_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            (checkout / "pyproject.toml").write_text('[project]\nname = "workflow-controller"\n')
            fixtures.commit_all(checkout, "drop the version")
            resolved = identity.resolve_runtime(checkout)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn("no readable version", resolved.reason)
            self.assertEqual(resolved.version, identity.UNKNOWN_VERSION)


class PackageRuntimeTest(unittest.TestCase):
    def test_the_distribution_metadata_version_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages", version=OTHER)
            resolved = identity.resolve_runtime(tree)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_PACKAGE)
            self.assertEqual(resolved.version, OTHER)
            with _running_from(tree):
                self.assertEqual(identity.pin().version, OTHER)

    def test_metadata_and_build_info_disagreeing_is_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages", metadata_version=OTHER)
            resolved = identity.resolve_runtime(tree)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn("partially upgraded", resolved.reason)
            # Reported, never used: an unidentified runtime launches nothing.
            self.assertEqual(resolved.version, OTHER)

    def test_no_distribution_metadata_is_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages", dist_info=False)
            resolved = identity.resolve_runtime(tree)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn("no readable distribution metadata", resolved.reason)
            self.assertEqual(resolved.version, identity.UNKNOWN_VERSION)


class PinnedChildTest(unittest.TestCase):
    def _pin_from(self, dest: Path) -> identity.ControllerIdentity:
        with _running_from(dest):
            return identity.pin()

    def _rewrite_pin(self, dest: Path, **changes) -> None:
        data = runtime.read_json(dest / "SOURCE_PIN.json")
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        (dest / "SOURCE_PIN.json").write_text(json.dumps(data))

    def test_a_pinned_child_reports_its_pins_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            dest = identity.materialise(checkout, Path(td) / "runtime")
            self.assertEqual(runtime.read_json(dest / "SOURCE_PIN.json")["version"], VERSION)
            self.assertEqual(self._pin_from(dest).version, VERSION)
            # The pin, not the snapshot's pyproject.toml, is what a child reads.
            self._rewrite_pin(dest, version="4.5.6")
            self.assertEqual(self._pin_from(dest).version, "4.5.6")

    def test_a_missing_or_non_semver_pin_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            dest = identity.materialise(checkout, Path(td) / "runtime")
            for bad in (None, "1.2", "", 7, "1.2.3\n"):
                with self.subTest(version=bad):
                    self._rewrite_pin(dest, version=bad)
                    with self.assertRaises(SourceSnapshotError) as ctx:
                        self._pin_from(dest)
                    self.assertEqual(ctx.exception.evidence["raised_by"], "pin")

    def test_a_package_snapshot_reports_its_own_build_info_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages", version=OTHER)
            with fixtures.empty_path(Path(td)):
                dest = identity.materialise(tree, Path(td) / "runtime")
            self.assertFalse(list(dest.glob("*.dist-info")))
            self.assertEqual(runtime.read_json(dest / "controller" / "BUILD_INFO.json")["version"], OTHER)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["version"], OTHER)
            self.assertEqual(pin_data["controller_runtime"]["version"], OTHER)
            self.assertEqual(self._pin_from(dest).version, OTHER)

    def test_a_dirty_snapshot_reports_its_own_pyproject_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _set_version(checkout, OTHER)
            dest = identity.materialise(checkout, Path(td) / "runtime", allow_dirty=True)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["source_kind"], identity.SOURCE_KIND_WORKTREE)
            self.assertNotEqual(OTHER, VERSION)  # the parent process runs VERSION
            self.assertEqual(pin_data["version"], OTHER)
            self.assertEqual(pin_data["controller_runtime"]["version"], OTHER)
            self.assertEqual(self._pin_from(dest).version, OTHER)


class HandoffVersionTest(unittest.TestCase):
    def test_the_committed_pyproject_version_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _set_version(checkout, OTHER)
            fixtures.commit_all(checkout, "move the version")
            # An uncommitted edit is not what HEAD approves.
            pyproject = checkout / "pyproject.toml"
            pyproject.write_text(pyproject.read_text().replace(f'version = "{OTHER}"', 'version = "6.6.6"'))
            self.assertEqual(handoff._read_committed_version(checkout), OTHER)

    def test_a_malformed_committed_version_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            (checkout / "pyproject.toml").write_text('[project]\nname = "workflow-controller"\n')
            fixtures.commit_all(checkout, "drop the version")
            self.assertIsNone(handoff._read_committed_version(checkout))


if __name__ == "__main__":
    unittest.main()
