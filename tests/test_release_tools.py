"""Tests for ``tools/release.py`` (CP8): tag/version verification, wheel
verification, the fail-closed duplicate-release check, the tag-to-commit
check that runs immediately before publication, and ``SHA256SUMS``.

``verify-wheel`` runs against real wheels built once per class from a
disposable committed clone (as ``tests.test_buildinfo`` does), and against
copies of them mutated in-test. The ``gh`` and Git runners are injected, so
nothing here reaches a network.
"""

from __future__ import annotations

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

from controller import buildinfo  # noqa: E402
from tests import fixtures  # noqa: E402

RELEASE_PY = fixtures.REPO_ROOT / "tools" / "release.py"
_spec = importlib.util.spec_from_file_location("release_tools", RELEASE_PY)
release = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release
_spec.loader.exec_module(release)

VERSION = fixtures.CONTROLLER_VERSION
TAG = f"v{VERSION}"
WHEEL_NAME = f"workflow_controller-{VERSION}-py3-none-any.whl"
DIST_INFO = f"workflow_controller-{VERSION}.dist-info"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "89abcdef0123456789abcdef0123456789abcdef"
TAG_OBJECT = "fedcba9876543210fedcba9876543210fedcba98"


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class Recorder:
    """An injectable runner returning a fixed result and recording argv."""

    def __init__(self, result: subprocess.CompletedProcess | Exception) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RefusalAssertions(unittest.TestCase):
    def assertRefused(self, check: str, fn, *args, **kwargs) -> release.Refusal:
        with self.assertRaises(release.Refusal) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.check, check, str(ctx.exception))
        return ctx.exception


class VerifyTagTest(RefusalAssertions):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pyproject = Path(self._tmp.name) / "pyproject.toml"
        shutil.copy2(fixtures.PYPROJECT, self.pyproject)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _verify(self, tag: str) -> None:
        release.verify_tag(tag, version=VERSION, pyproject=self.pyproject)

    def test_the_version_tag_passes(self) -> None:
        self._verify(TAG)
        release.verify_tag(TAG, version=VERSION)  # the real pyproject.toml

    def test_another_version_is_refused(self) -> None:
        major, minor, patch = (int(p) for p in VERSION.split("."))
        refusal = self.assertRefused("tag/version", self._verify, f"v{major}.{minor}.{patch + 1}")
        self.assertIn(TAG, refusal.detail)

    def test_malformed_tags_are_refused(self) -> None:
        for tag in (VERSION, "v1.1", "v1.1.0-rc.1", "v01.1.0", f"{TAG}x", f"{TAG}\n", f"{TAG}.1",
                    f"V{VERSION}", ""):
            with self.subTest(tag=tag):
                self.assertRefused("tag format", self._verify, tag)

    def test_the_static_form_is_required(self) -> None:
        text = self.pyproject.read_text()
        static = f'version = "{VERSION}"\n'
        self.assertIn(static, text)
        for label, rewritten in (
            ("no static version", text.replace(static, "")),
            ("a dynamic version", text.replace(static, 'dynamic = ["version"]\n')),
            ("static and dynamic", text.replace(static, static + 'dynamic = ["version"]\n')),
            ("a setuptools version attr", text + '\n[tool.setuptools.dynamic]\n'
                                                 'version = {attr = "controller.version.__version__"}\n'),
        ):
            with self.subTest(label):
                self.pyproject.write_text(rewritten)
                self.assertRefused("pyproject version source", self._verify, TAG)

    def test_a_static_version_other_than_the_given_one_is_refused(self) -> None:
        text = self.pyproject.read_text()
        self.pyproject.write_text(text.replace(f'version = "{VERSION}"', 'version = "9.9.9"'))
        refusal = self.assertRefused("pyproject version source", self._verify, TAG)
        self.assertIn("9.9.9", refusal.detail)


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


class CheckUnpublishedTest(RefusalAssertions):
    def test_release_not_found_passes(self) -> None:
        gh = Recorder(_completed(1, stderr="release not found\n"))
        release.check_unpublished(TAG, run_gh=gh)
        self.assertEqual(gh.calls, [["gh", "release", "view", TAG, "--json", "tagName"]])

    def test_an_existing_release_is_refused(self) -> None:
        gh = Recorder(_completed(0, stdout=json.dumps({"tagName": TAG})))
        self.assertRefused("already published", release.check_unpublished, TAG, run_gh=gh)

    def test_any_other_failure_is_undecidable(self) -> None:
        for label, runner in (
            ("auth", Recorder(_completed(4, stderr="To get started with GitHub CLI, please run:  gh auth login\n"))),
            ("network", Recorder(_completed(1, stderr="error connecting to api.github.com\n"))),
            ("unknown", Recorder(_completed(1, stderr="HTTP 500: Internal Server Error\n"))),
            ("silent", Recorder(_completed(1))),
            ("not found in passing", Recorder(_completed(1, stderr="error: release not found for x\n"))),
            ("gh missing", Recorder(FileNotFoundError("gh"))),
        ):
            with self.subTest(label):
                self.assertRefused("release lookup undecidable", release.check_unpublished, TAG,
                                   run_gh=runner)

    def test_a_malformed_tag_never_reaches_gh(self) -> None:
        gh = Recorder(_completed(1, stderr="release not found\n"))
        self.assertRefused("tag format", release.check_unpublished, "main", run_gh=gh)
        self.assertEqual(gh.calls, [])


class VerifyTagCommitTest(RefusalAssertions):
    def _git(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> Recorder:
        return Recorder(_completed(returncode, stdout, stderr))

    def test_a_lightweight_tag_naming_the_commit_passes(self) -> None:
        git = self._git(f"{SHA}\trefs/tags/{TAG}\n")
        release.verify_tag_commit(TAG, SHA, run_git=git)
        self.assertEqual(git.calls, [["git", "ls-remote", "origin", f"refs/tags/{TAG}",
                                      f"refs/tags/{TAG}^{{}}"]])

    def test_an_annotated_tag_uses_the_peeled_line(self) -> None:
        stdout = f"{TAG_OBJECT}\trefs/tags/{TAG}\n{SHA}\trefs/tags/{TAG}^{{}}\n"
        release.verify_tag_commit(TAG, SHA, run_git=self._git(stdout))
        self.assertRefused("tag moved", release.verify_tag_commit, TAG, TAG_OBJECT,
                           run_git=self._git(stdout))

    def test_a_tag_naming_another_commit_is_refused(self) -> None:
        for stdout in (f"{OTHER_SHA}\trefs/tags/{TAG}\n",
                       f"{TAG_OBJECT}\trefs/tags/{TAG}\n{OTHER_SHA}\trefs/tags/{TAG}^{{}}\n"):
            with self.subTest(stdout=stdout):
                refusal = self.assertRefused("tag moved", release.verify_tag_commit, TAG, SHA,
                                             run_git=self._git(stdout))
                self.assertIn(OTHER_SHA, refusal.detail)

    def test_undecidable_answers_are_refused(self) -> None:
        cases = {
            "non-zero exit": self._git(f"{SHA}\trefs/tags/{TAG}\n", returncode=128,
                                       stderr="fatal: unable to access\n"),
            "no matching line": self._git(""),
            "only a peeled line": self._git(f"{SHA}\trefs/tags/{TAG}^{{}}\n"),
            "short sha": self._git(f"{SHA[:12]}\trefs/tags/{TAG}\n"),
            "space separated": self._git(f"{SHA} refs/tags/{TAG}\n"),
            "trailing field": self._git(f"{SHA}\trefs/tags/{TAG}\textra\n"),
            "another ref": self._git(f"{SHA}\trefs/tags/{TAG}\n{SHA}\trefs/heads/{TAG}\n"),
            "duplicate ref": self._git(f"{SHA}\trefs/tags/{TAG}\n{OTHER_SHA}\trefs/tags/{TAG}\n"),
            "git missing": Recorder(FileNotFoundError("git")),
        }
        for label, git in cases.items():
            with self.subTest(label):
                self.assertRefused("tag commit undecidable", release.verify_tag_commit, TAG, SHA,
                                   run_git=git)

    def test_malformed_arguments_are_refused(self) -> None:
        git = self._git(f"{SHA}\trefs/tags/{TAG}\n")
        self.assertRefused("tag format", release.verify_tag_commit, "latest", SHA, run_git=git)
        self.assertRefused("commit format", release.verify_tag_commit, TAG, SHA.upper(), run_git=git)
        self.assertEqual(git.calls, [])

    def test_the_cli_refuses_with_one_line(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = release.main(["verify-tag-commit", TAG, SHA],
                                run_git=self._git(f"{OTHER_SHA}\trefs/tags/{TAG}\n"))
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue().count("\n"), 1)
        self.assertIn("refused: tag moved:", err.getvalue())


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
    def test_version_prints_the_controller_version(self) -> None:
        result = fixtures.run([sys.executable, str(RELEASE_PY), "version"], check=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, f"{VERSION}\n", ""))

    def test_an_unreadable_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pyproject.toml").write_text('[project]\nname = "x"\n')
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = release.main(["version"], repo_root=Path(td))
            self.assertEqual(code, 1)
            self.assertIn("refused: version source:", stderr.getvalue())


class CheckedOutVersionTest(unittest.TestCase):
    """``version``, ``verify-tag`` and ``verify-wheel`` all take the version
    from the checked-out ``pyproject.toml``: rewriting it in a disposable
    checkout moves all three."""

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

    def test_all_three_subcommands_follow_the_checked_out_pyproject(self) -> None:
        result = self._release("version")
        self.assertEqual((result.returncode, result.stdout), (0, f"{self.MOVED}\n"), result.stderr)

        result = self._release("verify-tag", f"v{self.MOVED}")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self._release("verify-tag", TAG)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"expected 'v{self.MOVED}'", result.stderr)

        result = self._release("verify-wheel", str(self.wheel), "--local", "--commit", self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        # This repository's own release.py, reading its own pyproject.toml,
        # refuses the same wheel.
        with self.assertRaises(release.Refusal):
            release.verify_wheel(self.wheel, version=release.checked_out_version(),
                                 commit=self.commit, tag=None)


if __name__ == "__main__":
    unittest.main()
