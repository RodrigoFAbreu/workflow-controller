"""The single Controller version authority (trunk-branch-pr-release CP1;
squash-merge-tag-versioning CP2).

Before the squash-merge cutover, ``pyproject.toml``'s static
``[project].version`` is the only place the version is written; after it,
``pyproject.toml`` declares ``dynamic = ["version"]`` and the Git tags are
the authority (``version.tag_version``). A source runtime reads its own code
root and never distribution metadata; a package runtime reads the metadata
of the distribution installed in its own code root, cross-checked against
``BUILD_INFO.json``; a pinned child reads its own ``SOURCE_PIN.json``. The
packaged ``--version``/``METADATA``/``BUILD_INFO``/``tools/release.py
version`` agreement is ``tests.test_packaged_runtime`` case 6,
``tools/release.py`` following the checked-out tree is
``tests.test_release_tools.CheckedOutVersionTest``, and building a dynamic
``pyproject.toml`` is ``tests.test_buildinfo.DynamicVersionBuildTest``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
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


def _tag(checkout: Path, name: str, *, annotated: bool = False, rev: str = "HEAD") -> None:
    args = ["-a", "-m", name] if annotated else []
    fixtures.run(["git", "tag", *args, name, rev], cwd=checkout)


def _version_line(code_root: Path, *, pythonpath: list[Path], cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != identity.EXEC_HANDOFF_ENV}
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in pythonpath)
    result = fixtures.run([sys.executable, "-P", "-B", "-m", "controller", "--version"],
                          cwd=cwd, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()[0]


class SingleAuthorityTest(unittest.TestCase):
    def test_the_repository_is_exactly_pre_or_post_cutover(self) -> None:
        """Pre-cutover: a static version with the ``version_change``
        trigger. Post-cutover: a dynamic version with the
        ``conventional_commit`` trigger. Nothing else."""
        pyproject = tomllib.loads(fixtures.PYPROJECT.read_text())
        project = pyproject["project"]
        trigger = json.loads((fixtures.REPO_ROOT / ".workflow-controller" / "policy.json")
                             .read_text())["release"]["trigger"]
        self.assertNotIn("dynamic", pyproject.get("tool", {}).get("setuptools", {}))
        if "version" in project:
            self.assertRegex(project["version"], version.SEMVER_RE)
            self.assertNotIn("dynamic", project)
            self.assertEqual(trigger, "version_change")
        else:
            self.assertEqual(project.get("dynamic"), ["version"])
            self.assertEqual(trigger, "conventional_commit")

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
    def test_source_version_reads_this_repository(self) -> None:
        self.assertEqual(version.source_version(fixtures.REPO_ROOT), VERSION)

    def test_a_static_version_wins_over_the_tags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _tag(checkout, "v9.9.9")
            self.assertEqual(version.static_pyproject_version(checkout), VERSION)
            self.assertEqual(version.source_version(checkout), VERSION)
            self.assertEqual(version.local_build_version(checkout), VERSION)

    def test_static_pyproject_version_is_none_when_dynamic_or_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for text in ('[project]\nname = "x"\ndynamic = ["version"]\n', '[project]\nname = "x"\n', ""):
                with self.subTest(text=text):
                    (root / "pyproject.toml").write_text(text)
                    self.assertIsNone(version.static_pyproject_version(root))

    def test_source_version_refuses_a_missing_or_malformed_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for label, text in (
                ("missing file", None),
                ("not toml", "[project\n"),
                # Dynamic, and root is not a Git work tree: no tag to derive from.
                ("dynamic outside Git", '[project]\nname = "x"\ndynamic = ["version"]\n'),
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


class TagVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.checkout = fixtures.build_checkout(self.tmp / "checkout", dynamic_version=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_tag_is_0_0_0(self) -> None:
        self.assertEqual(version.tag_version(self.checkout), "0.0.0")

    def test_the_highest_reachable_release_tag_wins(self) -> None:
        _tag(self.checkout, "v1.9.0")
        _tag(self.checkout, "v1.10.0", annotated=True)
        _tag(self.checkout, "v1.2.3")
        self.assertEqual(version.tag_version(self.checkout), "1.10.0")

    def test_lightweight_and_annotated_tags_are_both_read(self) -> None:
        _tag(self.checkout, "v2.0.0", annotated=True)
        self.assertEqual(version.tag_version(self.checkout), "2.0.0")
        _tag(self.checkout, "v2.0.1")
        self.assertEqual(version.tag_version(self.checkout), "2.0.1")

    def test_other_tag_shapes_are_ignored(self) -> None:
        _tag(self.checkout, "v1.0.0")
        for name in ("v01.2.3", "1.2.3", "v1.2.3-rc.1", "v2.0", "release-9.9.9", "V9.9.9"):
            _tag(self.checkout, name)
        self.assertEqual(version.tag_version(self.checkout), "1.0.0")

    def test_a_tag_off_the_history_is_ignored(self) -> None:
        base = fixtures.current_head(self.checkout)
        _tag(self.checkout, "v1.0.0")
        fixtures.run(["git", "checkout", "-q", "-b", "side"], cwd=self.checkout)
        fixtures.commit_all(self.checkout, "side", allow_empty=True)
        _tag(self.checkout, "v3.0.0")
        fixtures.run(["git", "checkout", "-q", "-"], cwd=self.checkout)
        self.assertEqual(version.tag_version(self.checkout), "1.0.0")
        # The side branch reaches both; an explicit rev is honoured.
        self.assertEqual(version.tag_version(self.checkout, "side"), "3.0.0")
        self.assertEqual(version.tag_version(self.checkout, base), "1.0.0")

    def test_an_unborn_head_is_0_0_0(self) -> None:
        unborn = fixtures.build_checkout(self.tmp / "unborn", dynamic_version=True, committed=False)
        self.assertEqual(version.tag_version(unborn), "0.0.0")
        self.assertEqual(version.source_version(unborn), "0.0.0")
        # An unborn checkout fails the source probes in either model; the
        # version it reports is still 0.0.0.
        static_unborn = fixtures.build_checkout(self.tmp / "static-unborn", committed=False)
        resolved = identity.resolve_runtime(unborn)
        self.assertEqual(resolved.runtime_kind, identity.resolve_runtime(static_unborn).runtime_kind)
        self.assertEqual(resolved.version, "0.0.0")

    def test_undecidable_reads_refuse(self) -> None:
        (self.checkout / "sub").mkdir()
        cases = (
            ("not a repository", self.tmp / "plain", "HEAD"),
            ("not the top of the work tree", self.checkout / "sub", "HEAD"),
            ("a rev that does not resolve", self.checkout, "no-such-branch"),
        )
        (self.tmp / "plain").mkdir()
        for label, root, rev in cases:
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    version.tag_version(root, rev)

    def test_a_failing_git_refuses(self) -> None:
        with fixtures.empty_path(self.tmp):
            with self.assertRaises(ValueError):
                version.tag_version(self.checkout)

    def test_git_environment_redirection_is_ignored(self) -> None:
        other = fixtures.build_checkout(self.tmp / "other", dynamic_version=True)
        _tag(other, "v8.0.0")
        with unittest.mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            self.assertEqual(version.tag_version(self.checkout), "0.0.0")

    def test_local_build_version(self) -> None:
        _tag(self.checkout, "v4.1.0")
        self.assertEqual(version.local_build_version(self.checkout), "4.1.0")
        copy = self.tmp / "copy"
        copy.mkdir()
        (copy / "pyproject.toml").write_text((self.checkout / "pyproject.toml").read_text())
        self.assertEqual(version.local_build_version(copy), "0.0.0")


class FixtureTest(unittest.TestCase):
    """Clones never depend on tags, on either side of the cutover."""

    def test_a_clone_declares_the_static_version_unless_asked_otherwise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            static = fixtures.build_checkout(Path(td) / "static")
            project = tomllib.loads((static / "pyproject.toml").read_text())["project"]
            self.assertEqual((project["version"], "dynamic" in project), (VERSION, False))
            dynamic = fixtures.build_checkout(Path(td) / "dynamic", dynamic_version=True)
            project = tomllib.loads((dynamic / "pyproject.toml").read_text())["project"]
            self.assertEqual((project.get("version"), project["dynamic"]), (None, ["version"]))
            # Only the version line differs from the real file.
            real = fixtures.PYPROJECT.read_text().splitlines()
            for clone in (static, dynamic):
                lines = (clone / "pyproject.toml").read_text().splitlines()
                self.assertEqual(len(lines), len(real))
                self.assertLessEqual(sum(a != b for a, b in zip(lines, real)), 1)

    def test_a_clone_is_never_tagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for kwargs in ({}, {"dynamic_version": True}):
                with self.subTest(**kwargs):
                    clone = fixtures.build_checkout(Path(td) / f"c{len(kwargs)}", **kwargs)
                    self.assertEqual(fixtures.run(["git", "tag"], cwd=clone).stdout, "")

    def test_no_test_copies_the_real_policy_into_a_clone(self) -> None:
        copy_call = re.compile(r"copy(?:2|tree|file)?\(")
        for path in sorted((fixtures.REPO_ROOT / "tests").glob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                with self.subTest(file=path.name, line=number):
                    self.assertFalse(copy_call.search(line) and ".workflow-controller" in line,
                                     f"{path.name}:{number} copies the real policy: {line.strip()}")


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

    def test_a_dynamic_checkout_reports_its_tag_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout", dynamic_version=True)
            _tag(checkout, "v3.2.1")
            resolved = identity.resolve_runtime(checkout)
            self.assertEqual((resolved.runtime_kind, resolved.version), (identity.RUNTIME_KIND_SOURCE, "3.2.1"))
            self.assertEqual(_version_line(checkout, pythonpath=[checkout], cwd=Path(td)),
                             "workflow-controller 3.2.1")
            # A commit past the tag still reports the last release it contains.
            fixtures.commit_all(checkout, "after the release", allow_empty=True)
            self.assertEqual(identity.resolve_runtime(checkout).version, "3.2.1")

    def test_an_unreadable_version_leaves_the_checkout_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            (checkout / "pyproject.toml").write_text('[project]\nname = "workflow-controller"\nversion = "1.2"\n')
            fixtures.commit_all(checkout, "break the version")
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


    def test_a_dynamic_snapshot_reports_the_origins_tag_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout", dynamic_version=True)
            _tag(checkout, "v2.7.0")
            dest = identity.materialise(checkout, Path(td) / "runtime")
            self.assertFalse((dest / ".git").exists())
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["source_kind"], identity.SOURCE_KIND_COMMIT)
            self.assertEqual((pin_data["version"], pin_data["controller_runtime"]["version"]), ("2.7.0", "2.7.0"))
            self.assertEqual(self._pin_from(dest).version, "2.7.0")

    def test_a_dirty_dynamic_snapshot_reports_its_commits_tag_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout", dynamic_version=True)
            _tag(checkout, "v2.7.1")
            errors_py = checkout / "controller" / "errors.py"
            errors_py.write_text(errors_py.read_text() + "\n# uncommitted\n")
            dest = identity.materialise(checkout, Path(td) / "runtime", allow_dirty=True)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["source_kind"], identity.SOURCE_KIND_WORKTREE)
            self.assertEqual(pin_data["version"], "2.7.1")


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
            (checkout / "pyproject.toml").write_text('[project]\nname = "workflow-controller"\nversion = "1.2"\n')
            fixtures.commit_all(checkout, "break the version")
            self.assertIsNone(handoff._read_committed_version(checkout))

    def test_a_dynamic_committed_version_is_read_from_the_tags_at_head(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout", dynamic_version=True)
            _tag(checkout, "v5.0.0")
            self.assertEqual(handoff._read_committed_version(checkout), "5.0.0")
            # An uncommitted static line is not what HEAD approves.
            (checkout / "pyproject.toml").write_text(fixtures.pyproject_text().replace(VERSION, "6.6.6"))
            self.assertEqual(handoff._read_committed_version(checkout), "5.0.0")
            # A tag on a commit HEAD does not reach is not HEAD's either.
            fixtures.run(["git", "checkout", "-q", "-b", "side"], cwd=checkout)
            fixtures.run(["git", "stash", "-q"], cwd=checkout)
            fixtures.commit_all(checkout, "side", allow_empty=True)
            _tag(checkout, "v5.1.0")
            fixtures.run(["git", "checkout", "-q", "-"], cwd=checkout)
            self.assertEqual(handoff._read_committed_version(checkout), "5.0.0")


if __name__ == "__main__":
    unittest.main()
