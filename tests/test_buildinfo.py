"""Tests for the release identity CP1 adds: the single version source
(``pyproject.toml``'s static ``[project].version``), ``controller/buildinfo.py``'s digest and schema
rules, and ``setup.py``'s ``build_py`` hook that writes
``controller/BUILD_INFO.json`` into every built wheel.

The hook tests build real wheels from disposable committed clones with
``pip wheel --no-deps --no-build-isolation``. They skip, naming the missing
piece, when ``tests.fixtures.wheel_build_prerequisite()`` reports one, unless
``CONTROLLER_REQUIRE_PACKAGING_TESTS=1`` makes that a failure.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import buildinfo  # noqa: E402
from tests import fixtures  # noqa: E402

VERSION = fixtures.CONTROLLER_VERSION
COMMIT = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "f" * 64


def _record(**overrides) -> dict:
    record = {
        "schema_version": 1,
        "name": "workflow-controller",
        "version": VERSION,
        "source_commit": COMMIT,
        "source_dirty": False,
        "package_digest": DIGEST,
        "build_origin": "local",
        "release_tag": None,
    }
    record.update(overrides)
    return record


class VersionSourceTest(unittest.TestCase):
    def test_version_is_plain_semver(self) -> None:
        self.assertRegex(VERSION, buildinfo.SEMVER_RE)

    def test_pyproject_declares_the_version_statically(self) -> None:
        pyproject = tomllib.loads(fixtures.PYPROJECT.read_text())
        self.assertEqual(pyproject["project"]["version"], VERSION)
        self.assertNotIn("version", pyproject["project"].get("dynamic", []))
        self.assertNotIn("dynamic", pyproject.get("tool", {}).get("setuptools", {}))

    def test_generation_file_is_declared_package_data(self) -> None:
        pyproject = tomllib.loads(fixtures.PYPROJECT.read_text())
        self.assertIn("GENERATION.json", pyproject["tool"]["setuptools"]["package-data"]["controller"])

    def test_tag_round_trip(self) -> None:
        self.assertEqual(buildinfo.tag_for_version("1.2.3"), "v1.2.3")
        self.assertEqual(buildinfo.version_for_tag("v1.2.3"), "1.2.3")
        for bad in ("1.2", "01.2.3", "1.2.3-rc.1", "1.2.3rc1", "1.2.3\n"):
            with self.assertRaises(ValueError):
                buildinfo.tag_for_version(bad)
        for bad in ("1.2.3", "v1.2", "V1.2.3", "v1.2.3-rc.1", "v1.2.3\n"):
            with self.assertRaises(ValueError):
                buildinfo.version_for_tag(bad)


class PackageDigestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pkg = Path(self._tmp.name) / "controller"
        (self.pkg / "sub").mkdir(parents=True)
        (self.pkg / "a.py").write_text("A = 1\n")
        (self.pkg / "GENERATION.json").write_text('{"generation": 1}\n')
        (self.pkg / "sub" / "b.py").write_text("B = 2\n")
        self.base = buildinfo.compute_package_digest(self.pkg)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stable_under_mode_changes(self) -> None:
        os.chmod(self.pkg / "a.py", 0o755)
        self.assertEqual(buildinfo.compute_package_digest(self.pkg), self.base)

    def test_stable_under_bytecode_additions(self) -> None:
        (self.pkg / "__pycache__").mkdir()
        (self.pkg / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0")
        (self.pkg / "sub" / "stray.pyc").write_bytes(b"\0")
        self.assertEqual(buildinfo.compute_package_digest(self.pkg), self.base)

    def test_changes_on_a_byte_change(self) -> None:
        (self.pkg / "sub" / "b.py").write_text("B = 3\n")
        self.assertNotEqual(buildinfo.compute_package_digest(self.pkg), self.base)

    def test_changes_on_an_added_file(self) -> None:
        (self.pkg / "c.py").write_text("")
        self.assertNotEqual(buildinfo.compute_package_digest(self.pkg), self.base)

    def test_changes_on_a_removed_file(self) -> None:
        (self.pkg / "a.py").unlink()
        self.assertNotEqual(buildinfo.compute_package_digest(self.pkg), self.base)

    def test_ignores_build_info_at_the_root_only(self) -> None:
        (self.pkg / "BUILD_INFO.json").write_text("{}\n")
        self.assertEqual(buildinfo.compute_package_digest(self.pkg), self.base)
        (self.pkg / "sub" / "BUILD_INFO.json").write_text("{}\n")
        self.assertNotEqual(buildinfo.compute_package_digest(self.pkg), self.base)


class ValidateBuildInfoTest(unittest.TestCase):
    def test_accepts_a_release_build(self) -> None:
        info = buildinfo.validate_build_info(
            _record(build_origin="release", release_tag=f"v{VERSION}"), expected_version=VERSION)
        self.assertEqual(info.build_origin, "release")
        self.assertEqual(info.to_dict(), _record(build_origin="release", release_tag=f"v{VERSION}"))

    def test_accepts_a_local_build_clean_or_dirty(self) -> None:
        for dirty in (False, True):
            info = buildinfo.validate_build_info(_record(source_dirty=dirty), expected_version=VERSION)
            self.assertEqual(info.source_dirty, dirty)

    def test_accepts_a_local_build_of_unknown_provenance(self) -> None:
        info = buildinfo.validate_build_info(
            _record(source_commit=None, source_dirty=None), expected_version=VERSION)
        self.assertIsNone(info.source_commit)

    def _rejects(self, record, pattern: str, expected_version: str = VERSION) -> None:
        with self.assertRaisesRegex(ValueError, pattern):
            buildinfo.validate_build_info(record, expected_version=expected_version)

    def test_rejects_a_release_without_a_tag(self) -> None:
        self._rejects(_record(build_origin="release"), "must carry a release_tag")

    def test_rejects_a_tag_that_does_not_match_the_version(self) -> None:
        self._rejects(_record(build_origin="release", release_tag="v9.9.9"), "does not match version")

    def test_rejects_a_dirty_release(self) -> None:
        self._rejects(_record(build_origin="release", release_tag=f"v{VERSION}", source_dirty=True),
                      "clean source tree")

    def test_rejects_a_release_without_a_commit(self) -> None:
        self._rejects(_record(build_origin="release", release_tag=f"v{VERSION}",
                              source_commit=None, source_dirty=None),
                      "must record its source_commit")

    def test_rejects_a_bad_digest_shape(self) -> None:
        for bad in ("f" * 63, "F" * 64, None, 1):
            self._rejects(_record(package_digest=bad), "package_digest")

    def test_rejects_a_version_mismatch(self) -> None:
        self._rejects(_record(), "does not match", expected_version="9.9.9")

    def test_rejects_an_unknown_schema_version(self) -> None:
        for bad in (2, "1", True, None):
            self._rejects(_record(schema_version=bad), "schema_version")

    def test_rejects_other_shape_violations(self) -> None:
        self._rejects([], "JSON object")
        self._rejects({k: v for k, v in _record().items() if k != "release_tag"}, "missing")
        self._rejects(_record(extra=1), "unexpected")
        self._rejects(_record(name="other"), "name")
        self._rejects(_record(version="1.1"), "MAJOR.MINOR.PATCH")
        self._rejects(_record(source_commit="abc"), "source_commit")
        self._rejects(_record(source_dirty=0), "source_dirty")
        self._rejects(_record(source_commit=None), "null together")
        self._rejects(_record(build_origin="nightly"), "build_origin")
        self._rejects(_record(release_tag=f"v{VERSION}"), "local build must not carry")

    def test_rejects_a_trailing_newline_in_any_pattern_field(self) -> None:
        self._rejects(_record(source_commit=COMMIT + "\n"), "source_commit")
        self._rejects(_record(package_digest=DIGEST + "\n"), "package_digest")
        self._rejects(_record(version=VERSION + "\n"), "MAJOR.MINOR.PATCH",
                      expected_version=VERSION + "\n")


def _clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "WORKFLOW_CONTROLLER_RELEASE_TAG"}
    env.update(extra)
    return env


def _wheel_members(wheel: Path, dest: Path) -> Path:
    """Extract the wheel's ``controller/`` members under ``dest`` and return
    the extracted package directory."""
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            if name.startswith("controller/"):
                zf.extract(name, dest)
    return dest / "controller"


class BuildHookTest(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.require_wheel_build(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.clone = fixtures.build_checkout(self.tmp / "clone")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build(self, source: Path | None = None, *, name: str = "dist", check: bool = True, **env):
        out = self.tmp / name
        result = fixtures.build_wheel(source or self.clone, out, env=_clean_env(**env), check=False)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result, out

    def _only_wheel(self, out: Path) -> Path:
        wheels = sorted(out.glob("*.whl"))
        self.assertEqual(len(wheels), 1, wheels)
        return wheels[0]

    def _build_info(self, wheel: Path) -> dict:
        with zipfile.ZipFile(wheel) as zf:
            return json.loads(zf.read(f"controller/{buildinfo.BUILD_INFO_NAME}"))

    def _assert_no_source_build_info(self, root: Path) -> None:
        self.assertEqual(
            [p for p in root.rglob(buildinfo.BUILD_INFO_NAME) if "build" not in p.relative_to(root).parts],
            [])

    def test_clean_clone_builds_a_valid_local_wheel(self) -> None:
        _, out = self._build()
        wheel = self._only_wheel(out)
        self.assertIn(f"-{VERSION}-", wheel.name)
        with zipfile.ZipFile(wheel) as zf:
            self.assertIn("controller/GENERATION.json", zf.namelist())
        record = self._build_info(wheel)
        info = buildinfo.validate_build_info(record, expected_version=VERSION)
        self.assertEqual(info.source_commit, fixtures.current_head(self.clone))
        self.assertIs(info.source_dirty, False)
        self.assertEqual(info.build_origin, "local")
        self.assertIsNone(info.release_tag)
        package = _wheel_members(wheel, self.tmp / "extracted")
        self.assertEqual(info.package_digest, buildinfo.compute_package_digest(package))
        self._assert_no_source_build_info(self.clone)
        self.assertFalse((self.clone / "controller" / buildinfo.BUILD_INFO_NAME).exists())

    def test_dirty_clone_records_source_dirty(self) -> None:
        (self.clone / "controller" / "errors.py").write_text(
            (self.clone / "controller" / "errors.py").read_text() + "\n# uncommitted\n")
        _, out = self._build()
        record = self._build_info(self._only_wheel(out))
        self.assertEqual(record["source_commit"], fixtures.current_head(self.clone))
        self.assertIs(record["source_dirty"], True)
        self._assert_no_source_build_info(self.clone)

    def test_non_git_copy_has_unknown_provenance(self) -> None:
        copy = self.tmp / "copy"
        shutil.copytree(self.clone, copy, ignore=shutil.ignore_patterns(".git"))
        _, out = self._build(copy)
        record = self._build_info(self._only_wheel(out))
        self.assertIsNone(record["source_commit"])
        self.assertIsNone(record["source_dirty"])
        buildinfo.validate_build_info(record, expected_version=VERSION)

    def test_release_tag_on_a_clean_clone_is_a_release_build(self) -> None:
        _, out = self._build(WORKFLOW_CONTROLLER_RELEASE_TAG=f"v{VERSION}")
        record = self._build_info(self._only_wheel(out))
        self.assertEqual(record["build_origin"], "release")
        self.assertEqual(record["release_tag"], f"v{VERSION}")
        self.assertEqual(record["source_commit"], fixtures.current_head(self.clone))

    def test_mismatched_release_tag_fails_the_build(self) -> None:
        result, out = self._build(check=False, WORKFLOW_CONTROLLER_RELEASE_TAG="v0.0.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match version", result.stdout + result.stderr)
        self.assertEqual(list(out.glob("*.whl")), [])

    def test_release_tag_on_a_dirty_clone_fails_the_build(self) -> None:
        (self.clone / "setup.py").write_text((self.clone / "setup.py").read_text() + "\n# dirty\n")
        result, out = self._build(check=False, WORKFLOW_CONTROLLER_RELEASE_TAG=f"v{VERSION}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean source tree", result.stdout + result.stderr)
        self.assertEqual(list(out.glob("*.whl")), [])
        self._assert_no_source_build_info(self.clone)

    def test_stale_build_directory_is_refused(self) -> None:
        extra = self.clone / "controller" / "extra_module.py"
        extra.write_text("X = 1\n")
        fixtures.commit_all(self.clone, "add extra module")
        self._build(name="first")
        self.assertTrue((self.clone / "build" / "lib" / "controller" / "extra_module.py").is_file())

        extra.unlink()
        fixtures.commit_all(self.clone, "remove extra module")
        result, out = self._build(name="second", check=False)
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn(str(Path("build") / "lib" / "controller" / "extra_module.py"), output)
        self.assertIn("delete build/", output)
        self.assertEqual(list(out.glob("*.whl")), [])

        shutil.rmtree(self.clone / "build")
        _, out = self._build(name="third")
        with zipfile.ZipFile(self._only_wheel(out)) as zf:
            self.assertNotIn("controller/extra_module.py", zf.namelist())

    def test_changed_source_is_recopied_even_when_its_mtime_does_not_advance(self) -> None:
        self._build(name="first")
        module = self.clone / "controller" / "errors.py"
        copied = self.clone / "build" / "lib" / "controller" / "errors.py"
        new_bytes = module.read_bytes() + b"\n# changed after the first build\n"
        module.write_bytes(new_bytes)
        fixtures.commit_all(self.clone, "change errors.py")
        old = copied.stat().st_mtime - 100
        os.utime(module, (old, old))

        _, out = self._build(name="second")
        wheel = self._only_wheel(out)
        with zipfile.ZipFile(wheel) as zf:
            self.assertEqual(zf.read("controller/errors.py"), new_bytes)
        record = self._build_info(wheel)
        self.assertIs(record["source_dirty"], False)
        package = _wheel_members(wheel, self.tmp / "extracted")
        self.assertEqual(record["package_digest"], buildinfo.compute_package_digest(package))

    def test_editable_install_writes_no_build_info(self) -> None:
        venv_dir = self.tmp / "venv"
        fixtures.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)])
        result = fixtures.run(
            [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "--quiet", "--no-deps",
             "--no-build-isolation", "-e", str(self.clone)],
            env=_clean_env(PIP_DISABLE_PIP_VERSION_CHECK="1"), check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.clone.rglob(buildinfo.BUILD_INFO_NAME)), [])
        self.assertEqual(list(venv_dir.rglob(buildinfo.BUILD_INFO_NAME)), [])
        # The editable mapping resolves the package to the clone itself.
        located = subprocess.run(
            [str(venv_dir / "bin" / "python"), "-P", "-c",
             "import controller, pathlib; print(pathlib.Path(controller.__file__).resolve().parent)"],
            cwd=self.tmp, capture_output=True, text=True, check=True,
        )
        mapped = Path(located.stdout.strip())
        self.assertEqual(mapped, (self.clone / "controller").resolve())
        self.assertFalse((mapped / buildinfo.BUILD_INFO_NAME).exists())


class EditableModeEarlyReturnTest(unittest.TestCase):
    def test_editable_mode_writes_nothing_and_raises_nothing(self) -> None:
        try:
            from setuptools.dist import Distribution
        except ImportError as exc:  # pragma: no cover - depends on the host
            raise unittest.SkipTest(f"setuptools is not importable: {exc}")
        spec = importlib.util.spec_from_file_location("_setup_under_test", fixtures.SETUP_PY)
        setup_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup_module)  # __name__ != "__main__": setup() is not called

        with tempfile.TemporaryDirectory() as tmp:
            build_lib = Path(tmp) / "lib"
            dist = Distribution({"name": "workflow-controller", "packages": ["controller"],
                                 "package_dir": {"": str(fixtures.REPO_ROOT)}})
            command = setup_module.build_py(dist)
            command.build_lib = str(build_lib)
            command.editable_mode = True
            command.ensure_finalized()
            command.run()
            self.assertFalse(build_lib.exists() and any(build_lib.rglob("*")))
        self.assertFalse((fixtures.CONTROLLER_PKG / buildinfo.BUILD_INFO_NAME).exists())


if __name__ == "__main__":
    unittest.main()
