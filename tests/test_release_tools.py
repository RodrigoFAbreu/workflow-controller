"""Tests for ``tools/release.py`` (CP8): wheel verification and
``SHA256SUMS``; (``workflow-controller-trunk-branch-pr-release-orchestration``
CP4) the policy-driven ``version``, ``classify``, ``build``, ``verify`` and
``publish`` subcommands; and (CP5) the removal of the tag-first
``verify-tag``, ``check-unpublished`` and ``verify-tag-commit``.

``verify-wheel`` runs against real wheels built once per class from a
disposable committed clone (as ``tests.test_buildinfo`` does), and against
copies of them mutated in-test. The transaction subcommands run over a bare
origin and the fake ``gh``, so nothing here reaches a network.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest import mock  # noqa: E402

from controller import buildinfo  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_release_txn import _ReleaseCase  # noqa: E402

RELEASE_PY = fixtures.REPO_ROOT / "tools" / "release.py"
_spec = importlib.util.spec_from_file_location("release_tools", RELEASE_PY)
release = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release
_spec.loader.exec_module(release)

VERSION = fixtures.CONTROLLER_VERSION
TAG = f"v{VERSION}"
WHEEL_NAME = f"workflow_controller-{VERSION}-py3-none-any.whl"
DIST_INFO = f"workflow_controller-{VERSION}.dist-info"
OTHER_SHA = "89abcdef0123456789abcdef0123456789abcdef"
#: The tag-first subcommands the retired ``release.yml`` called (CP5).
RETIRED_SUBCOMMANDS = ("verify-tag", "check-unpublished", "verify-tag-commit")


class RefusalAssertions(unittest.TestCase):
    def assertRefused(self, check: str, fn, *args, **kwargs) -> release.Refusal:
        with self.assertRaises(release.Refusal) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.check, check, str(ctx.exception))
        return ctx.exception


def _rewrite_wheel(source: Path, dest: Path, *, drop: tuple[str, ...] = (),
                   replace: dict[str, bytes] | None = None, add: dict[str, bytes] | None = None) -> Path:
    replace = replace or {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
        names = src.namelist()
        for name in (*replace, *drop):
            assert name in names, name
        for info in src.infolist():
            if info.filename in drop:
                continue
            out.writestr(info, replace.get(info.filename, src.read(info)))
        for name, data in (add or {}).items():
            out.writestr(name, data)
    return dest


class VerifyWheelTest(RefusalAssertions):
    @classmethod
    def setUpClass(cls) -> None:
        missing = fixtures.wheel_build_prerequisite()
        if missing is not None:
            if os.environ.get(fixtures.REQUIRE_PACKAGING_TESTS_ENV) == "1":
                raise AssertionError(f"wheel-build prerequisite missing: {missing}")
            raise unittest.SkipTest(f"wheel-build prerequisite missing: {missing}")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        try:
            clone = fixtures.build_checkout(cls.tmp / "clone")
            cls.commit = fixtures.current_head(clone)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("GIT_") and k != "WORKFLOW_CONTROLLER_RELEASE_TAG"}
            fixtures.build_wheel(clone, cls.tmp / "local", env=env)
            shutil.rmtree(clone / "build")
            fixtures.build_wheel(clone, cls.tmp / "release",
                                 env=dict(env, WORKFLOW_CONTROLLER_RELEASE_TAG=TAG))
        except BaseException:
            cls._tmp.cleanup()
            raise
        cls.local_wheel = cls.tmp / "local" / WHEEL_NAME
        cls.release_wheel = cls.tmp / "release" / WHEEL_NAME

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self._case = tempfile.TemporaryDirectory(dir=self.tmp)
        self.case = Path(self._case.name)

    def tearDown(self) -> None:
        self._case.cleanup()

    def _mutated(self, source: Path | None = None, **changes) -> Path:
        return _rewrite_wheel(source or self.local_wheel, self.case / "m" / WHEEL_NAME, **changes)

    def _local(self, wheel: Path, commit: str | None = None):
        return release.verify_wheel(wheel, version=VERSION, commit=commit or self.commit, tag=None)

    def _released(self, wheel: Path, tag: str = TAG):
        return release.verify_wheel(wheel, version=VERSION, commit=self.commit, tag=tag)

    def _build_info(self, wheel: Path) -> dict:
        with zipfile.ZipFile(wheel) as zf:
            return json.loads(zf.read(f"controller/{buildinfo.BUILD_INFO_NAME}"))

    def _with_build_info(self, **overrides) -> Path:
        record = dict(self._build_info(self.local_wheel), **overrides)
        return self._mutated(replace={f"controller/{buildinfo.BUILD_INFO_NAME}":
                                      json.dumps(record).encode()})

    def test_a_correct_local_wheel_passes(self) -> None:
        info = self._local(self.local_wheel)
        self.assertEqual(info.build_origin, "local")
        # An unchanged rewrite of the archive is still the same wheel.
        self._local(self._mutated())

    def test_a_release_wheel_built_with_the_matching_tag_passes(self) -> None:
        info = self._released(self.release_wheel)
        self.assertEqual(info.release_tag, TAG)

    def test_a_wrong_filename_version_is_refused(self) -> None:
        wrong = self.case / "w" / "workflow_controller-9.9.9-py3-none-any.whl"
        wrong.parent.mkdir()
        shutil.copy2(self.local_wheel, wrong)
        self.assertRefused("wheel filename", self._local, wrong)

    def test_a_metadata_version_mismatch_is_refused(self) -> None:
        with zipfile.ZipFile(self.local_wheel) as zf:
            metadata = zf.read(f"{DIST_INFO}/METADATA").decode()
        self.assertIn(f"\nVersion: {VERSION}\n", metadata)
        wheel = self._mutated(replace={f"{DIST_INFO}/METADATA":
                                       metadata.replace(f"Version: {VERSION}", "Version: 9.9.9").encode()})
        self.assertRefused("wheel metadata", self._local, wheel)

    def test_a_missing_generation_file_is_refused(self) -> None:
        wheel = self._mutated(drop=("controller/GENERATION.json",))
        refusal = self.assertRefused("required files", self._local, wheel)
        self.assertIn("GENERATION.json", refusal.detail)

    def test_a_missing_build_info_is_refused(self) -> None:
        wheel = self._mutated(drop=(f"controller/{buildinfo.BUILD_INFO_NAME}",))
        refusal = self.assertRefused("required files", self._local, wheel)
        self.assertIn(buildinfo.BUILD_INFO_NAME, refusal.detail)

    def test_an_invalid_build_info_is_refused(self) -> None:
        for label, data in (("not json", b"{not json"),
                            ("wrong schema", json.dumps(dict(self._build_info(self.local_wheel),
                                                             schema_version=2)).encode()),
                            ("bad digest", json.dumps(dict(self._build_info(self.local_wheel),
                                                           package_digest="zz")).encode())):
            with self.subTest(label):
                wheel = self._mutated(replace={f"controller/{buildinfo.BUILD_INFO_NAME}": data})
                self.assertRefused("build info", self._local, wheel)

    def test_included_bytecode_is_refused(self) -> None:
        for name in ("controller/__pycache__/cli.cpython-312.pyc", "controller/cli.pyc"):
            with self.subTest(name):
                wheel = self._mutated(add={name: b"\0"})
                self.assertRefused("forbidden files", self._local, wheel)

    def test_an_included_source_pin_is_refused(self) -> None:
        wheel = self._mutated(add={"controller/SOURCE_PIN.json": b"{}"})
        refusal = self.assertRefused("forbidden files", self._local, wheel)
        self.assertIn("SOURCE_PIN.json", refusal.detail)

    def test_a_tampered_module_is_refused(self) -> None:
        with zipfile.ZipFile(self.local_wheel) as zf:
            errors = zf.read("controller/errors.py")
        wheel = self._mutated(replace={"controller/errors.py": errors + b"\n# tampered\n"})
        self.assertRefused("package digest", self._local, wheel)

    def test_an_added_module_is_refused(self) -> None:
        wheel = self._mutated(add={"controller/extra.py": b"X = 1\n"})
        self.assertRefused("package digest", self._local, wheel)

    def test_a_commit_mismatch_is_refused(self) -> None:
        self.assertRefused("source commit", self._local, self.local_wheel, commit=OTHER_SHA)
        self.assertRefused("commit format", self._local, self.local_wheel, commit="abc")

    def test_a_dirty_build_is_refused(self) -> None:
        self.assertRefused("source dirty", self._local, self._with_build_info(source_dirty=True))

    def test_the_wrong_mode_is_refused(self) -> None:
        self.assertRefused("build origin", self._released, self.local_wheel)
        self.assertRefused("build origin", self._local, self.release_wheel)

    def test_a_wrong_entry_point_is_refused(self) -> None:
        name = f"{DIST_INFO}/entry_points.txt"
        with zipfile.ZipFile(self.local_wheel) as zf:
            text = zf.read(name).decode()
        self.assertIn("workflow-controller = controller.cli:main", text)
        for label, data in (("target", text.replace("controller.cli:main", "controller.cli:other")),
                            ("absent", "[console_scripts]\n")):
            with self.subTest(label):
                wheel = self._mutated(replace={name: data.encode()})
                self.assertRefused("entry point", self._local, wheel)
        self.assertRefused("entry point", self._local, self._mutated(drop=(name,)))

    def test_the_cli_exits_1_with_one_line_naming_the_check(self) -> None:
        result = fixtures.run([sys.executable, str(RELEASE_PY), "verify-wheel", str(self.local_wheel),
                               "--tag", TAG, "--commit", self.commit], check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertIn("refused: build origin:", result.stderr)

        result = fixtures.run([sys.executable, str(RELEASE_PY), "verify-wheel", str(self.local_wheel),
                               "--local", "--commit", self.commit], check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ChecksumsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "dist"
        self.dir.mkdir()
        (self.dir / WHEEL_NAME).write_bytes(b"wheel bytes\n" * 100)
        (self.dir / "notes.txt").write_bytes(b"")
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "nested.txt").write_bytes(b"not listed")
        (self.dir / "link").symlink_to(self.dir / "notes.txt")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _expected(self) -> str:
        return "".join(f"{hashlib.sha256((self.dir / n).read_bytes()).hexdigest()}  {n}\n"
                       for n in sorted(["notes.txt", WHEEL_NAME]))

    def _sha256sum_check(self) -> None:
        if shutil.which("sha256sum") is None:
            return
        result = fixtures.run(["sha256sum", "-c", release.CHECKSUMS_NAME], cwd=self.dir, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_output_matches_hashlib_in_sha256sum_format(self) -> None:
        path = release.checksums(self.dir)
        self.assertEqual(path, self.dir / "SHA256SUMS")
        self.assertEqual(path.read_text(), self._expected())
        self._sha256sum_check()

    def test_a_rerun_excludes_a_previous_sha256sums(self) -> None:
        first = release.checksums(self.dir).read_bytes()
        self.assertNotIn(b"SHA256SUMS", first)
        self.assertEqual(release.checksums(self.dir).read_bytes(), first)
        (self.dir / "SHA256SUMS").write_text("0" * 64 + "  stale.whl\n")
        self.assertEqual(release.checksums(self.dir).read_bytes(), first)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         sorted(["SHA256SUMS", WHEEL_NAME, "link", "notes.txt", "sub"]))
        self._sha256sum_check()


class VersionCommandTest(unittest.TestCase):
    """``version`` prints the version a local build of the checked-out tree
    carries (``version.local_build_version``); it needs no policy."""

    def test_version_prints_the_controller_version(self) -> None:
        result = fixtures.run([sys.executable, str(RELEASE_PY), "version"], check=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, f"{VERSION}\n", ""))

    def _committed(self, root: Path, pyproject: str) -> None:
        fixtures.run(["git", "init", "-q", str(root)])
        fixtures.run(["git", "config", "user.email", "t@example.invalid"], cwd=root)
        fixtures.run(["git", "config", "user.name", "T"], cwd=root)
        (root / "pyproject.toml").write_text(pyproject)
        fixtures.commit_all(root, "fixture")

    def _version(self, root: Path) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = release.main(["version"], repo_root=root)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_static_version_is_the_checked_out_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._committed(root, '[project]\nname = "x"\nversion = "4.5.6"\n')
            fixtures.run(["git", "tag", "v9.0.0"], cwd=root)
            self.assertEqual(self._version(root), (0, "4.5.6\n", ""))
            # What a local build of this tree would carry.
            (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "4.5.7"\n')
            self.assertEqual(self._version(root), (0, "4.5.7\n", ""))

    def test_a_dynamic_version_is_the_highest_tag_reachable_from_head(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._committed(root, '[project]\nname = "x"\ndynamic = ["version"]\n')
            self.assertEqual(self._version(root), (0, "0.0.0\n", ""))
            fixtures.run(["git", "tag", "v1.4.0"], cwd=root)
            fixtures.commit_all(root, "after", allow_empty=True)
            self.assertEqual(self._version(root), (0, "1.4.0\n", ""))

    def test_an_unreadable_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._committed(Path(td), '[project]\nname = "x"\nversion = "1.2"\n')
            code, _, stderr = self._version(Path(td))
            self.assertEqual(code, 1)
            self.assertIn("refused: version source:", stderr)


class CheckedOutVersionTest(unittest.TestCase):
    """``version`` and ``verify-wheel --local`` both take the version from
    the checked-out tree: rewriting and committing its static version in a
    disposable checkout moves both."""

    MOVED = "7.8.9"

    @classmethod
    def setUpClass(cls) -> None:
        missing = fixtures.wheel_build_prerequisite()
        if missing is not None:
            if os.environ.get(fixtures.REQUIRE_PACKAGING_TESTS_ENV) == "1":
                raise AssertionError(f"wheel-build prerequisite missing: {missing}")
            raise unittest.SkipTest(f"wheel-build prerequisite missing: {missing}")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        try:
            cls.clone = fixtures.build_checkout(cls.tmp / "clone")
            shutil.copytree(fixtures.REPO_ROOT / "tools", cls.clone / "tools",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            pyproject = cls.clone / "pyproject.toml"
            text = pyproject.read_text()
            assert f'version = "{VERSION}"' in text
            pyproject.write_text(text.replace(f'version = "{VERSION}"', f'version = "{cls.MOVED}"'))
            fixtures.run(["git", "add", "-A"], cwd=cls.clone)
            fixtures.run(["git", "commit", "-q", "-m", "move the version"], cwd=cls.clone)
            cls.commit = fixtures.current_head(cls.clone)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("GIT_") and k != "WORKFLOW_CONTROLLER_RELEASE_TAG"}
            fixtures.build_wheel(cls.clone, cls.tmp / "wheels", env=env)
        except BaseException:
            cls._tmp.cleanup()
            raise
        cls.wheel = cls.tmp / "wheels" / f"workflow_controller-{cls.MOVED}-py3-none-any.whl"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _release(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        return fixtures.run([sys.executable, "-B", str(self.clone / "tools" / "release.py"), *args],
                            cwd=self.tmp, env=env, check=False)

    def test_both_subcommands_follow_the_checked_out_pyproject(self) -> None:
        result = self._release("version")
        self.assertEqual((result.returncode, result.stdout), (0, f"{self.MOVED}\n"), result.stderr)

        result = self._release("verify-wheel", str(self.wheel), "--local", "--commit", self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        # This repository's own release.py, reading its own pyproject.toml,
        # refuses the same wheel.
        with self.assertRaises(release.Refusal):
            release.verify_wheel(self.wheel, version=release.checked_out_version(),
                                 commit=self.commit, tag=None)


class DynamicVersionWheelTest(unittest.TestCase):
    """In a tag-derived checkout, ``verify-wheel --tag TAG`` expects
    ``TAG``'s version and ``--local`` the highest tag reachable from
    ``HEAD``."""

    @classmethod
    def setUpClass(cls) -> None:
        missing = fixtures.wheel_build_prerequisite()
        if missing is not None:
            if os.environ.get(fixtures.REQUIRE_PACKAGING_TESTS_ENV) == "1":
                raise AssertionError(f"wheel-build prerequisite missing: {missing}")
            raise unittest.SkipTest(f"wheel-build prerequisite missing: {missing}")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        try:
            cls.clone = fixtures.build_checkout(cls.tmp / "clone", dynamic_version=True)
            shutil.copytree(fixtures.REPO_ROOT / "tools", cls.clone / "tools",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            fixtures.commit_all(cls.clone, "tools")
            fixtures.run(["git", "tag", "v2.3.4"], cwd=cls.clone)
            cls.commit = fixtures.current_head(cls.clone)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("GIT_") and k != "WORKFLOW_CONTROLLER_RELEASE_TAG"}
            fixtures.build_wheel(cls.clone, cls.tmp / "local", env=env)
            shutil.rmtree(cls.clone / "build")
            fixtures.build_wheel(cls.clone, cls.tmp / "release",
                                 env=dict(env, WORKFLOW_CONTROLLER_RELEASE_TAG="v2.4.0"))
            shutil.rmtree(cls.clone / "build")
        except BaseException:
            cls._tmp.cleanup()
            raise
        cls.local_wheel = cls.tmp / "local" / "workflow_controller-2.3.4-py3-none-any.whl"
        cls.release_wheel = cls.tmp / "release" / "workflow_controller-2.4.0-py3-none-any.whl"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _release(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        return fixtures.run([sys.executable, "-B", str(self.clone / "tools" / "release.py"), *args],
                            cwd=self.tmp, env=env, check=False)

    def test_version_is_the_tag_version(self) -> None:
        result = self._release("version")
        self.assertEqual((result.returncode, result.stdout), (0, "2.3.4\n"), result.stderr)

    def test_a_release_wheel_verifies_against_its_tag(self) -> None:
        result = self._release("verify-wheel", str(self.release_wheel), "--tag", "v2.4.0", "--commit", self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self._release("verify-wheel", str(self.release_wheel), "--tag", "v2.4.1", "--commit", self.commit)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused: wheel filename:", result.stderr)

    def test_a_local_wheel_verifies_against_the_local_build_version(self) -> None:
        result = self._release("verify-wheel", str(self.local_wheel), "--local", "--commit", self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self._release("verify-wheel", str(self.release_wheel), "--local", "--commit", self.commit)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused: wheel filename:", result.stderr)

    def test_a_malformed_tag_is_refused(self) -> None:
        result = self._release("verify-wheel", str(self.release_wheel), "--tag", "2.4.0", "--commit", self.commit)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused: tag format:", result.stderr)


class RetiredSubcommandsTest(unittest.TestCase):
    """CP5 removes the tag-first subcommands together with ``release.yml``,
    their only caller: a release tag is created only by ``publish``."""

    def test_the_parser_no_longer_offers_them(self) -> None:
        commands = next(action for action in release.build_parser()._actions
                        if isinstance(action, argparse._SubParsersAction)).choices
        self.assertEqual(sorted(commands), sorted(["version", "classify", "build", "verify", "publish",
                                                   "verify-wheel", "checksums", "check-title"]))
        for name in RETIRED_SUBCOMMANDS:
            with self.subTest(name=name):
                self.assertNotIn(name, commands)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
                    release.main([name, TAG])
                self.assertEqual(ctx.exception.code, 2)
                self.assertIn("invalid choice", stderr.getvalue())

    def test_the_module_no_longer_defines_them(self) -> None:
        for attr in ("verify_tag", "check_unpublished", "verify_tag_commit", "remote_tag_commit"):
            with self.subTest(attr=attr):
                self.assertFalse(hasattr(release, attr))


class CheckTitleTest(unittest.TestCase):
    """``check-title`` (squash-merge-tag-versioning CP1): the policy
    committed at ``HEAD`` decides, never the working tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = fixtures.build_target_git_repo(Path(self._tmp.name) / "repo")
        self.policy_file = self.root / ".workflow-controller" / "policy.json"

    def _commit_policy(self, raw: bytes) -> None:
        self.policy_file.parent.mkdir(exist_ok=True)
        self.policy_file.write_bytes(raw)
        fixtures.commit_all(self.root, "policy")

    def _check(self, title: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = release.main(["check-title", title], repo_root=self.root)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_version_change_does_not_read_the_title(self) -> None:
        self._commit_policy(fixtures.LEGACY_POLICY)
        expected = "ok: the release trigger is version_change; the title is not release input\n"
        for title in ("feat: x", "Revert \"feat: x\"", "anything"):
            with self.subTest(title=title):
                self.assertEqual(self._check(title), (0, expected, ""))

    def test_conventional_commit_names_the_bump(self) -> None:
        self._commit_policy(fixtures.CONVENTIONAL_POLICY)
        for title, line in (
            ("feat: squash merges with release versions derived from pull request titles", "feat → minor"),
            ("fix(forge): x (#12)", "fix → patch"),
            ("docs: x", "docs → none"),
            ("refactor!: x", "refactor! → major"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self._check(title), (0, f"ok: {line}\n", ""))

    def test_an_invalid_title_is_a_one_line_refusal(self) -> None:
        self._commit_policy(fixtures.CONVENTIONAL_POLICY)
        for title, fragment in (
            ("Feat: x", "is not a Conventional Commit title"),
            ("feature: x", "unknown type 'feature'"),
            ("docs!: x", "a breaking change must release"),
            ('Revert "feat: x"', "is not a Conventional Commit title"),
            ("feat: x\nsecond line", "is not a Conventional Commit title"),
        ):
            with self.subTest(title=title):
                code, out, err = self._check(title)
                self.assertEqual((code, out), (1, ""))
                self.assertTrue(err.startswith("release.py check-title: refused: title: "), err)
                self.assertIn(fragment, err)
                self.assertEqual(len(err.splitlines()), 1)

    def test_a_missing_policy_is_refused(self) -> None:
        (self.root / "README").write_text("x\n")
        fixtures.commit_all(self.root, "init")
        code, out, err = self._check("feat: x")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("refused: repository policy: HEAD has no .workflow-controller/policy.json", err)

    def test_the_policy_is_read_from_head_not_the_working_tree(self) -> None:
        self._commit_policy(fixtures.LEGACY_POLICY)
        self.policy_file.write_bytes(fixtures.CONVENTIONAL_POLICY)
        self.assertEqual(self._check("Feat: x")[0], 0)
        fixtures.commit_all(self.root, "cutover")
        self.assertEqual(self._check("Feat: x")[0], 1)
        self.policy_file.write_bytes(fixtures.LEGACY_POLICY)
        self.assertEqual(self._check("Feat: x")[0], 1)


class ReleaseTransactionCliTest(_ReleaseCase):
    """``classify``/``build``/``verify``/``publish`` over the toy adopter of
    ``tests.test_release_txn``: a bare origin and the fake ``gh``."""

    def _main(self, *args: str, github_output: Path | None = None) -> tuple[int, str, str]:
        env = dict(self.env, TOY_VERIFY_LOG=str(self.verify_log))
        env.pop("GITHUB_OUTPUT", None)
        if github_output is not None:
            env["GITHUB_OUTPUT"] = str(github_output)
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = release.main(list(args), repo_root=self.clone)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_classify_build_verify_and_publish(self) -> None:
        outputs = self.tmp / "github_output"
        code, out, err = self._main("classify", "--commit", self.base, github_output=outputs)
        expected = f"state=RELEASE_DUE\nversion=1.0.0\ntag=v1.0.0\ncommit={self.base}\n"
        self.assertEqual((code, out), (0, expected), err)
        self.assertEqual(outputs.read_text(), expected)

        fixtures.run(["git", "switch", "-q", "--detach", self.base], cwd=self.clone)
        self.assertEqual(self._main("build")[:2], (0, "ok: built dist/pkg-1.0.0.txt\n"))
        self.assertEqual(self._main("verify")[:2], (0, f"ok: dist/pkg-1.0.0.txt verified for {self.base}\n"))
        code, out, err = self._main("publish", "--commit", self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("v1.0.0", out)
        self.assertFalse(self.release_state("v1.0.0")["isDraft"])
        tagger = fixtures.run(["git", "for-each-ref", "--format=%(taggername)", "refs/tags/v1.0.0"],
                              cwd=self.clone).stdout.strip()
        self.assertEqual(tagger, release.TAGGER[0])
        self.assertEqual(self._main("classify", "--commit", self.base)[:2],
                         (0, f"state=ALREADY_RELEASED\nversion=1.0.0\ntag=v1.0.0\ncommit={self.base}\n"))

    def test_a_failing_state_writes_its_outputs_and_exits_1(self) -> None:
        self.seed_release("v1.0.0", self.good_assets("1.0.0", self.base))
        code, out, err = self._main("classify", "--commit", self.base)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("state=COLLISION_RELEASE_WITHOUT_TAG\n"), out)
        self.assertIn("refused: COLLISION_RELEASE_WITHOUT_TAG:", err)
        self.assertEqual(len(err.splitlines()), 1)

    def test_a_controller_refusal_is_one_line(self) -> None:
        code, _, err = self._main("publish", "--commit", self.base)
        self.assertEqual(code, 1)
        self.assertIn("refused: RELEASE_TRANSACTION_REFUSED:", err)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertEqual(self._main("classify", "--commit", "HEAD")[0], 1)


if __name__ == "__main__":
    unittest.main()
