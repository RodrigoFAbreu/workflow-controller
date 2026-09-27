"""Tests for the immutable-execution-root mechanism: materialise(), pin(),
current(), the runtime-root ladder as it applies to the re-exec, and the
positive read-only/pinned guard.

Nine tests are named in ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``'s
CP1 section, "Nine tests pin the immutable-execution model". This module
implements them as faithfully as is practical for a single checkpoint's
own test file, using real subprocesses and real Git fixtures rather than
mocking the mechanism whose correctness is under test. A handful of the
plan's most elaborate fixture nuances (three-way install-mode matrices
under ``chmod a-w``, the full seven-fixture-pair `raised_by` AST ladder)
are intentionally out of this file's scope -- ``test_package_structure.py``
covers the source-scan properties this file does not.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import buildinfo, identity, runtime  # noqa: E402
from controller.errors import DirtyControllerSourceError, SourceSnapshotError  # noqa: E402
from tests import fixtures  # noqa: E402


class MaterialiseCleanTreeTest(unittest.TestCase):
    """Test 1 (mechanism half) + test 9 (dirt outside the pathspec)."""

    def test_clean_tree_materialises_commit_kind(self) -> None:
        self.fail("functional probe")  # THROWAWAY L4 probe, never merge
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            head = fixtures.run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
            runtime_root = Path(td) / "runtime"
            dest = identity.materialise(checkout, runtime_root, allow_dirty=False)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["source_kind"], "commit")
            self.assertEqual(pin_data["source_commit"], head)
            self.assertEqual(dest.name, pin_data["tree_digest"])
            recomputed = identity.compute_tree_digest(dest)
            self.assertEqual(recomputed, pin_data["tree_digest"])
            # only the pathspec's own two paths were extracted
            self.assertTrue((dest / "controller" / "__init__.py").is_file())
            self.assertTrue((dest / "pyproject.toml").is_file())

    def test_dirt_outside_pathspec_is_not_dirt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            (checkout / "docs").mkdir()
            (checkout / "docs" / "note.md").write_text("not part of the snapshot pathspec\n")
            runtime_root = Path(td) / "runtime"
            # no --allow-dirty-source needed: the modification is outside controller/+pyproject.toml
            dest = identity.materialise(checkout, runtime_root, allow_dirty=False)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["source_kind"], "commit")
            self.assertFalse((dest / "docs").exists())

            clean_digest = identity.compute_tree_digest(dest)
            with tempfile.TemporaryDirectory() as td2:
                clean_checkout = fixtures.build_checkout(Path(td2) / "origin2")
                clean_dest = identity.materialise(clean_checkout, Path(td2) / "runtime", allow_dirty=False)
                self.assertEqual(identity.compute_tree_digest(clean_dest), clean_digest)

    def test_modification_inside_pathspec_requires_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            with (checkout / "pyproject.toml").open("a") as fh:
                fh.write("# edited\n")
            with self.assertRaises(DirtyControllerSourceError):
                identity.materialise(checkout, Path(td) / "runtime", allow_dirty=False)


class MaterialiseDirtyTreeTest(unittest.TestCase):
    """Test 2 (refusal, no side effect) + test 3 (worktree kind)."""

    def test_dirty_tree_refuses_without_the_flag_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            (checkout / "controller" / "identity.py").write_text("# dirtied\n")
            runtime_root = Path(td) / "runtime"
            with self.assertRaises(DirtyControllerSourceError):
                identity.materialise(checkout, runtime_root, allow_dirty=False)
            self.assertFalse((runtime_root / "source").exists() and
                              any((runtime_root / "source").iterdir()))

    def test_dirty_tree_with_flag_materialises_worktree_kind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            clean_digest_dest = identity.materialise(checkout, Path(td) / "runtime-clean", allow_dirty=False)
            clean_digest = clean_digest_dest.name

            (checkout / "controller" / "identity.py").write_text(
                (checkout / "controller" / "identity.py").read_text() + "\n# local edit\n"
            )
            runtime_root = Path(td) / "runtime"
            dest = identity.materialise(checkout, runtime_root, allow_dirty=True)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["source_kind"], "worktree")
            self.assertIsNone(pin_data["source_commit"])
            self.assertNotEqual(dest.name, clean_digest)


class MaterialiseReuseTest(unittest.TestCase):
    """Test 4: identical bytes reuse one snapshot directory; a byte
    changed produces a different one; the snapshot is untouched by its own
    execution; a reuse whose SOURCE_PIN.json rewrite fails leaves the
    snapshot directory holding nothing extra, and a later run still
    reuses it."""

    def test_reuse_same_bytes_and_divergence_on_a_changed_byte(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            dest1 = identity.materialise(checkout, runtime_root, allow_dirty=False)
            dest2 = identity.materialise(checkout, runtime_root, allow_dirty=False)
            self.assertEqual(dest1, dest2)

            # a single byte changed in the source -> a different directory,
            # committed so the digest is stable across the check below
            (checkout / "controller" / "GENERATION.json").write_text(
                json.dumps({"schema_version": 1, "generation": 2}) + "\n"
            )
            fixtures.run(["git", "add", "-A"], cwd=checkout)
            fixtures.run(["git", "commit", "-q", "-m", "bump generation"], cwd=checkout)
            dest3 = identity.materialise(checkout, runtime_root, allow_dirty=False)
            self.assertNotEqual(dest3, dest1)

    def test_reuse_survives_a_real_pinned_execution_with_no_pycache_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            dest = identity.materialise(checkout, runtime_root, allow_dirty=False)
            digest = dest.name

            env = dict(os.environ)
            env["PYTHONPATH"] = str(dest)
            proc = fixtures.run(
                [sys.executable, "-P", "-B", "-m", "controller", "--runtime-dir", str(runtime_root), "status"],
                cwd=checkout, env=env, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse((dest / "controller" / "__pycache__").exists())

            recomputed = identity.compute_tree_digest(dest)
            self.assertEqual(recomputed, digest)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["tree_digest"], digest)

            dest_again = identity.materialise(checkout, runtime_root, allow_dirty=False)
            self.assertEqual(dest_again, dest)

    def test_interrupted_reuse_rewrite_leaves_the_snapshot_untouched_and_still_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            dest = identity.materialise(checkout, runtime_root, allow_dirty=False)
            digest = dest.name
            expected_names = {p.name for p in dest.iterdir()}

            original_replace = os.replace

            def failing_replace(src, dst):  # noqa: ANN001
                if Path(dst).name == "SOURCE_PIN.json" and Path(dst).parent == dest:
                    raise OSError("simulated interruption between fsync and os.replace")
                return original_replace(src, dst)

            os.replace = failing_replace
            try:
                with self.assertRaises(OSError):
                    identity.materialise(checkout, runtime_root, allow_dirty=False)
            finally:
                os.replace = original_replace

            self.assertEqual({p.name for p in dest.iterdir()}, expected_names)
            self.assertFalse(any(p.name.endswith(".tmp") for p in (runtime_root / "source").iterdir()))

            dest_again = identity.materialise(checkout, runtime_root, allow_dirty=False)
            self.assertEqual(dest_again, dest)
            self.assertEqual(identity.compute_tree_digest(dest_again), digest)


class TamperedSnapshotRefusalTest(unittest.TestCase):
    """Test 5: a snapshot altered after materialisation refuses on the
    next materialise(), and refuses from pin() when run directly; both
    name their own raise site via evidence['raised_by']."""

    def test_altered_snapshot_content_refuses_from_materialise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            dest = identity.materialise(checkout, runtime_root, allow_dirty=False)
            (dest / "controller" / "identity.py").write_text("# tampered\n")

            with self.assertRaises(SourceSnapshotError) as ctx:
                identity.materialise(checkout, runtime_root, allow_dirty=False)
            self.assertEqual(ctx.exception.evidence["raised_by"], "materialise")
            # the altered directory is left untouched by the refusal
            self.assertEqual((dest / "controller" / "identity.py").read_text(), "# tampered\n")

    def test_altered_snapshot_with_matching_pin_digest_still_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            dest = identity.materialise(checkout, runtime_root, allow_dirty=False)
            (dest / "controller" / "identity.py").write_text("# tampered\n")
            tampered_digest = identity.compute_tree_digest(dest)
            pin_path = dest / "SOURCE_PIN.json"
            pin_data = runtime.read_json(pin_path)
            pin_data["tree_digest"] = tampered_digest
            pin_path.write_text(json.dumps(pin_data, indent=2, sort_keys=True) + "\n")

            with self.assertRaises(SourceSnapshotError) as ctx:
                identity.materialise(checkout, runtime_root, allow_dirty=False)
            self.assertEqual(ctx.exception.evidence["raised_by"], "materialise")

    def test_altered_snapshot_run_directly_refuses_from_pin(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            dest = identity.materialise(checkout, runtime_root, allow_dirty=False)
            # Tamper with data content, not code -- overwriting identity.py's
            # own source would remove `pin` itself and the import would
            # never reach the check under test.
            (dest / "controller" / "GENERATION.json").write_text(
                json.dumps({"schema_version": 1, "generation": 999}) + "\n"
            )

            env = dict(os.environ)
            env.pop(identity.EXEC_HANDOFF_ENV, None)
            env["PYTHONPATH"] = str(dest)
            proc = fixtures.run(
                [sys.executable, "-P", "-B", "-c",
                 "import controller.identity as i; i.pin()"],
                cwd=dest, env=env, check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("SourceSnapshotError", proc.stderr)


class GuardTest(unittest.TestCase):
    """Test 6: the two guard mechanisms."""

    def test_read_only_commands_succeed_unpinned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            env = dict(os.environ)
            for name in ("PYTHONPATH", identity.EXEC_HANDOFF_ENV):
                env.pop(name, None)
            proc = fixtures.run(
                [sys.executable, "-m", "controller", "--runtime-dir", str(runtime_root), "status"],
                cwd=checkout, env={**env, "PYTHONPATH": str(checkout)}, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse((runtime_root / "source").exists())

    def test_non_read_only_command_function_refuses_when_called_directly_unpinned(self) -> None:
        from controller import cli

        identity._reset_for_tests()
        try:
            with tempfile.TemporaryDirectory() as td:
                # force an unpinned identity with no SOURCE_PIN.json nearby
                fake_source_root = Path(td) / "not-pinned"
                fake_source_root.mkdir()

                class _Args:
                    pass

                original_pin = identity.pin
                identity.pin = lambda: identity.ControllerIdentity(
                    generation=None, source_root=fake_source_root, origin_source_root=fake_source_root,
                    source_kind=identity.SOURCE_KIND_UNPINNED, source_commit=None, tree_digest=None,
                    generation_source=None, pinned_at="now", version=fixtures.CONTROLLER_VERSION,
                )
                identity.current = identity.pin
                try:
                    for fn in (cli.cmd_step, cli.cmd_run, cli.cmd_resume):
                        with self.assertRaises(SourceSnapshotError) as ctx:
                            fn(_Args(), Path(td), identity.current())
                        self.assertEqual(ctx.exception.evidence["raised_by"], "cli.main")
                finally:
                    identity.pin = original_pin
                    identity.current = original_pin
        finally:
            identity._reset_for_tests()

    def test_non_git_unpinned_directory_refuses_from_materialise_through_cli_main(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            non_git = Path(td) / "plain"
            (non_git / "controller").mkdir(parents=True)
            (non_git / "controller" / "__init__.py").write_text("")
            (non_git / "pyproject.toml").write_text("[project]\nname='x'\n")
            runtime_root = Path(td) / "runtime"
            env = {**os.environ, "PYTHONPATH": str(non_git)}
            for name in (identity.EXEC_HANDOFF_ENV,):
                env.pop(name, None)
            # exercise materialise()'s own refusal directly: a non-Git origin
            # cannot be archived at all.
            with self.assertRaises(Exception):
                identity.materialise(non_git, runtime_root, allow_dirty=False)


class DecoyAndRealRouteTest(unittest.TestCase):
    """Test 1 (installed-console-script route) and test 8 (the decoy
    case), through a real editable install in a throwaway venv."""

    def test_console_script_route_pins_and_contains_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            venv_dir = Path(td) / "venv"
            try:
                console_script = fixtures.editable_install(checkout, venv_dir)
            except Exception as exc:  # pragma: no cover -- environment-dependent
                self.skipTest(f"editable install unavailable in this environment: {exc}")

            env = dict(os.environ)
            env.pop(identity.EXEC_HANDOFF_ENV, None)
            # `step` is not read-only, so this is the route that actually
            # exercises materialise-and-re-exec; the pinned child reaches
            # `cmd_step`'s own real body (CP9), which refuses with
            # `UnmanagedRepositoryError` -- this fixture checkout carries no
            # `.workflow-manager/installation.json` -- which is the proof
            # dispatch got there pinned just as surely as the CP1-era stub
            # did: `identity.json` is written before that command body runs
            # regardless.
            proc = fixtures.run([str(console_script), "step", str(checkout)], cwd=checkout, env=env, check=False)
            self.assertEqual(proc.returncode, 20)  # EXIT_FAIL_CLOSED
            self.assertIn("not a Workflow-managed repository", proc.stderr)

            runtime_root = checkout / ".controller"
            record = runtime.read_json(runtime_root / "identity.json")
            self.assertEqual(record["exec_depth"], 1)
            self.assertTrue(record["interpreter_safe_path"])
            source_root = Path(record["source_root"])
            self.assertEqual(record["interpreter_sys_path"][0], str(source_root))
            self.assertTrue(str(source_root).startswith(str((runtime_root / "source").resolve())))
            checkout_resolved = str(checkout.resolve())
            for entry in record["interpreter_sys_path"]:
                self.assertNotEqual(entry, checkout_resolved)
                if entry.startswith(checkout_resolved):
                    self.assertEqual(entry, str(source_root))

    def test_decoy_package_in_working_directory_is_never_imported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            venv_dir = Path(td) / "venv"
            try:
                console_script = fixtures.editable_install(checkout, venv_dir)
            except Exception as exc:  # pragma: no cover -- environment-dependent
                self.skipTest(f"editable install unavailable in this environment: {exc}")

            scratch = Path(td) / "scratch"
            (scratch / "controller").mkdir(parents=True)
            (scratch / "controller" / "__init__.py").write_text(
                "raise RuntimeError('DECOY IMPORTED')\n"
            )

            env = dict(os.environ)
            env.pop(identity.EXEC_HANDOFF_ENV, None)
            runtime_root = Path(td) / "runtime-decoy"
            proc = fixtures.run(
                [str(console_script), "--runtime-dir", str(runtime_root), "step", str(checkout)],
                cwd=scratch, env=env, check=False,
            )
            self.assertNotIn("DECOY IMPORTED", proc.stderr)
            self.assertIn("not a Workflow-managed repository", proc.stderr)
            record = runtime.read_json(runtime_root / "identity.json")
            self.assertEqual(record["exec_depth"], 1)


class GenerationSourceRuleTest(unittest.TestCase):
    """The three tests pinning the generation-source composition rule."""

    def test_worktree_generation_bump_is_not_seen_when_head_still_has_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin", generation=1)
            (checkout / "controller" / "GENERATION.json").write_text(
                json.dumps({"schema_version": 1, "generation": 2}) + "\n"
            )
            dest = identity.materialise(checkout, Path(td) / "runtime", allow_dirty=True)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["generation"], 1)
            self.assertEqual(pin_data["generation_source"], "head")

    def test_no_generation_json_at_head_falls_back_to_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin", generation=None)
            (checkout / "controller" / "GENERATION.json").write_text(
                json.dumps({"schema_version": 1, "generation": 7}) + "\n"
            )
            dest = identity.materialise(checkout, Path(td) / "runtime", allow_dirty=True)
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["generation"], 7)
            self.assertEqual(pin_data["generation_source"], "worktree")

            with self.assertRaises(DirtyControllerSourceError):
                identity.materialise(checkout, Path(td) / "runtime2", allow_dirty=False)

    def test_malformed_generation_json_refuses_rather_than_defaulting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin", generation=1)
            (checkout / "controller" / "GENERATION.json").write_text("not json\n")
            fixtures.run(["git", "add", "-A"], cwd=checkout)
            fixtures.run(["git", "commit", "-q", "-m", "malform"], cwd=checkout)
            with self.assertRaises(SourceSnapshotError) as ctx:
                identity.materialise(checkout, Path(td) / "runtime", allow_dirty=False)
            self.assertEqual(ctx.exception.evidence["raised_by"], "materialise")


class DirectoryCreationOrderingTest(unittest.TestCase):
    """Implementation-review round 1, finding I1: every snapshot-
    materialisation directory-creation write must be preceded, in the same
    function body, by the containment assertion for the exact path being
    created. ``mkdir`` is not a call form ``test_write_containment.py``'s
    package-wide scanner recognizes, so these two sites are pinned
    directly by recording call order for the one path each test cares
    about -- a fabricated ``mkdir``-before-``guard`` order fails, and the
    fixed ``guard``-before-``mkdir`` order passes."""

    def test_extract_dirty_target_guard_precedes_its_parent_mkdir(self) -> None:
        calls: list[str] = []
        original_mkdir = Path.mkdir
        original_guard = runtime.assert_contained

        def recording_mkdir(self, *a, **kw):  # noqa: ANN001
            if self.name == "sub":
                calls.append("mkdir")
            return original_mkdir(self, *a, **kw)

        def recording_guard(root, path):  # noqa: ANN001
            if Path(path).name == "leaf.txt":
                calls.append("guard")
            return original_guard(root, path)

        with tempfile.TemporaryDirectory() as td:
            origin = Path(td) / "origin"
            (origin / "sub").mkdir(parents=True)
            (origin / "sub" / "leaf.txt").write_text("hi\n")
            dest = Path(td) / "dest"

            class _FakeListing:
                stdout = "sub/leaf.txt\n"

            original_run_git = identity._run_git
            identity._run_git = lambda args, cwd: _FakeListing()
            Path.mkdir = recording_mkdir
            runtime.assert_contained = recording_guard
            try:
                identity._extract_dirty(origin, dest)
            finally:
                identity._run_git = original_run_git
                Path.mkdir = original_mkdir
                runtime.assert_contained = original_guard

            # "mkdir" can appear more than once: pathlib's own parents=True
            # fallback re-enters self.mkdir(...) after creating a missing
            # parent, which re-triggers the (still-patched) recorder. Only
            # the first occurrence of each reflects the call site under
            # test, and that is what the ordering claim is about.
            self.assertLess(calls.index("guard"), calls.index("mkdir"))
            self.assertEqual((dest / "sub" / "leaf.txt").read_text(), "hi\n")

    def test_materialise_guards_source_dir_before_creating_it(self) -> None:
        calls: list[str] = []
        original_mkdir = Path.mkdir
        original_guard = runtime.assert_contained

        def recording_mkdir(self, *a, **kw):  # noqa: ANN001
            if self.name == "source":
                calls.append("mkdir")
            return original_mkdir(self, *a, **kw)

        def recording_guard(root, path):  # noqa: ANN001
            if Path(path).name == "source":
                calls.append("guard")
            return original_guard(root, path)

        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "origin")
            runtime_root = Path(td) / "runtime"
            Path.mkdir = recording_mkdir
            runtime.assert_contained = recording_guard
            try:
                identity.materialise(checkout, runtime_root, allow_dirty=False)
            finally:
                Path.mkdir = original_mkdir
                runtime.assert_contained = original_guard

        # see the sibling test above for why "mkdir" may repeat.
        self.assertLess(calls.index("guard"), calls.index("mkdir"))


class PinCachingTest(unittest.TestCase):
    def test_current_returns_identical_object_and_git_is_invoked_at_most_once(self) -> None:
        identity._reset_for_tests()
        try:
            calls = []
            original = identity._run_git

            def counting(args, *, cwd):  # noqa: ANN001
                calls.append(args)
                return original(args, cwd=cwd)

            identity._run_git = counting
            try:
                first = identity.pin()
                second = identity.current()
            finally:
                identity._run_git = original
            self.assertIs(first, second)
            rev_parse_calls = [c for c in calls if c[:2] == ["rev-parse", "--git-dir"] or c == ["rev-parse", "HEAD"]]
            self.assertLessEqual(len(rev_parse_calls), 2)  # one is-git-repo check + one HEAD read, at most
        finally:
            identity._reset_for_tests()



# ---------------------------------------------------------------------------
# Release-runtime-observability CP2: packaged runtime identity.
# ---------------------------------------------------------------------------

_DELETE_IT = "delete it to run from source"


@contextlib.contextmanager
def _running_from(code_root: Path):
    """``pin()`` as if the running package were ``code_root/controller``."""
    identity._reset_for_tests()
    env = {k: v for k, v in os.environ.items() if k != identity.EXEC_HANDOFF_ENV}
    try:
        with unittest.mock.patch.object(identity, "__file__", str(code_root / "controller" / "identity.py")), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            yield
    finally:
        identity._reset_for_tests()


def _package_inside_checkout(td: Path, **build) -> tuple[Path, Path]:
    """A committed checkout with a package tree under ``.venv/.../site-packages``
    -- the venv-inside-checkout layout."""
    checkout = fixtures.build_checkout(td / "checkout")
    site_packages = fixtures.build_package_tree(
        checkout / ".venv" / "lib" / "python3.12" / "site-packages", **build)
    return checkout, site_packages


def _plant_build_info(checkout: Path) -> None:
    """A valid build info for the checkout's own tree, planted in it."""
    package = checkout / "controller"
    build = {
        "schema_version": 1, "name": "workflow-controller", "version": fixtures.CONTROLLER_VERSION,
        "source_commit": "a" * 40, "source_dirty": False,
        "package_digest": buildinfo.compute_package_digest(package),
        "build_origin": "local", "release_tag": None,
    }
    (package / "BUILD_INFO.json").write_text(json.dumps(build))


def _controller(args: list[str], *, code_root: Path, cwd: Path, path: str | None = None):
    env = {k: v for k, v in os.environ.items() if k != identity.EXEC_HANDOFF_ENV}
    env["PYTHONPATH"] = str(code_root)
    if path is not None:
        env["PATH"] = path
    return fixtures.run([sys.executable, "-P", "-B", "-m", "controller", *args], cwd=cwd, env=env, check=False)


class ResolveRuntimeTest(unittest.TestCase):
    def test_valid_build_info_is_a_package(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages")
            resolved = identity.resolve_runtime(tree)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_PACKAGE)
            self.assertEqual(resolved.build["source_commit"], "a" * 40)
            self.assertIsNone(resolved.reason)

    def test_malformed_build_info_or_a_version_mismatch_is_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "malformed")
            (tree / "controller" / "BUILD_INFO.json").write_text("{not json")
            resolved = identity.resolve_runtime(tree)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn("not valid build info", resolved.reason)

            # The distribution metadata says one version, BUILD_INFO another:
            # a partial upgrade.
            other = fixtures.build_package_tree(Path(td) / "mismatch", metadata_version="9.9.9")
            resolved = identity.resolve_runtime(other)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn("does not match", resolved.reason)
            self.assertIn("partially upgraded", resolved.reason)

    def test_a_committed_checkout_is_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            self.assertEqual(identity.resolve_runtime(checkout).runtime_kind, identity.RUNTIME_KIND_SOURCE)

    def test_a_non_git_copy_without_build_info_is_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / "copy"
            shutil.copytree(fixtures.CONTROLLER_PKG, copy / "controller",
                            ignore=shutil.ignore_patterns("__pycache__", "BUILD_INFO.json"))
            resolved = identity.resolve_runtime(copy)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn("neither an installed", resolved.reason)

    def test_site_packages_inside_a_checkout_without_build_info_is_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _checkout, site_packages = _package_inside_checkout(Path(td))
            (site_packages / "controller" / "BUILD_INFO.json").unlink()
            with fixtures.git_call_spy() as calls:
                resolved = identity.resolve_runtime(site_packages)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertEqual(calls, [])

    def test_a_package_inside_a_git_work_tree_makes_no_git_call(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _checkout, site_packages = _package_inside_checkout(Path(td))
            with fixtures.git_call_spy() as calls:
                resolved = identity.resolve_runtime(site_packages)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_PACKAGE)
            self.assertEqual(calls, [])

    def test_empty_path_package_still_resolves_and_a_checkout_names_the_missing_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout, site_packages = _package_inside_checkout(Path(td))
            with fixtures.empty_path(Path(td)):
                package = identity.resolve_runtime(site_packages)
                source = identity.resolve_runtime(checkout)
            self.assertEqual(package.runtime_kind, identity.RUNTIME_KIND_PACKAGE)
            self.assertEqual(source.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn("git could not be run", source.reason)

    def test_a_checkout_with_planted_valid_build_info_is_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _plant_build_info(checkout)
            with fixtures.git_call_spy() as calls:
                resolved = identity.resolve_runtime(checkout)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn(_DELETE_IT, resolved.reason)
            self.assertEqual(calls, [])

    def test_planted_build_info_step_exits_20_and_status_prints_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _plant_build_info(checkout)
            runtime_root = Path(td) / "runtime"
            step = _controller(["--runtime-dir", str(runtime_root), "step", str(checkout)],
                               code_root=checkout, cwd=Path(td))
            self.assertEqual(step.returncode, 20, step.stderr)
            self.assertIn(_DELETE_IT, step.stderr)
            self.assertFalse((runtime_root / "source").exists())
            self.assertFalse((runtime_root / "jobs").exists())

            status = _controller(["--runtime-dir", str(runtime_root), "status"],
                                 code_root=checkout, cwd=Path(td))
            self.assertEqual(status.returncode, 0, status.stderr)
            first = status.stdout.splitlines()[0]
            self.assertTrue(first.startswith(f"controller: workflow-controller {fixtures.CONTROLLER_VERSION} -- "
                                             f"unidentified (source checkout"), first)
            self.assertIn(_DELETE_IT, first)

    def test_planted_build_info_without_git_or_untracked_is_still_unidentified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _plant_build_info(checkout)
            with fixtures.empty_path(Path(td)) as empty, fixtures.git_call_spy() as calls:
                resolved = identity.resolve_runtime(checkout)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn(_DELETE_IT, resolved.reason)
            self.assertEqual(calls, [])
            step = _controller(["--runtime-dir", str(Path(td) / "runtime"), "step", str(checkout)],
                               code_root=checkout, cwd=Path(td), path=str(empty))
            self.assertEqual(step.returncode, 20, step.stderr)
            self.assertIn(_DELETE_IT, step.stderr)

            untracked = fixtures.build_checkout(Path(td) / "untracked", committed=False)
            _plant_build_info(untracked)
            resolved = identity.resolve_runtime(untracked)
            self.assertEqual(resolved.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIn(_DELETE_IT, resolved.reason)


class UnpinnedIdentityByKindTest(unittest.TestCase):
    def test_package_inside_a_work_tree_takes_the_build_commit_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _checkout, site_packages = _package_inside_checkout(Path(td), source_commit="c" * 40)
            with _running_from(site_packages), fixtures.git_call_spy() as calls:
                ident = identity.pin()
            self.assertEqual(ident.runtime_kind, identity.RUNTIME_KIND_PACKAGE)
            self.assertEqual(ident.source_kind, identity.SOURCE_KIND_UNPINNED)
            self.assertEqual(ident.source_commit, "c" * 40)
            self.assertEqual(ident.version, fixtures.CONTROLLER_VERSION)
            self.assertEqual(calls, [])

            with _running_from(site_packages), fixtures.empty_path(Path(td)):
                self.assertEqual(identity.pin().source_commit, "c" * 40)

    def test_dirty_or_unknown_provenance_builds_have_no_commit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dirty = fixtures.build_package_tree(Path(td) / "dirty", source_dirty=True)
            unknown = fixtures.build_package_tree(Path(td) / "unknown", source_commit=None, source_dirty=None)
            for tree in (dirty, unknown):
                with _running_from(tree):
                    self.assertIsNone(identity.pin().source_commit, tree)

    def test_unidentified_has_no_commit_and_runs_no_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / "copy"
            shutil.copytree(fixtures.CONTROLLER_PKG, copy / "controller",
                            ignore=shutil.ignore_patterns("__pycache__", "BUILD_INFO.json"))
            with _running_from(copy), fixtures.git_call_spy() as calls:
                ident = identity.pin()
            self.assertEqual(ident.runtime_kind, identity.RUNTIME_KIND_UNIDENTIFIED)
            self.assertIsNone(ident.source_commit)
            self.assertEqual(calls, [])

    def test_source_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            head = fixtures.run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
            with _running_from(checkout):
                ident = identity.pin()
            self.assertEqual(ident.runtime_kind, identity.RUNTIME_KIND_SOURCE)
            self.assertEqual(ident.source_commit, head)
            self.assertIsNone(ident.build)


class MaterialisePackageTest(unittest.TestCase):
    def test_package_snapshot_pin_records_the_package_identity_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages", generation=3, source_commit="c" * 40)
            with fixtures.empty_path(Path(td)):
                dest = identity.materialise(tree, Path(td) / "runtime")
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            self.assertEqual(pin_data["runtime_kind"], "package")
            self.assertEqual(pin_data["source_kind"], "package")
            self.assertEqual(pin_data["source_commit"], "c" * 40)
            self.assertEqual(pin_data["generation"], 3)
            self.assertEqual(pin_data["generation_source"], "package")
            self.assertEqual(pin_data["version"], fixtures.CONTROLLER_VERSION)
            self.assertEqual(pin_data["build"]["package_digest"],
                             buildinfo.compute_package_digest(tree / "controller"))
            self.assertEqual(pin_data["controller_runtime"]["tree_digest"], dest.name)
            self.assertEqual(identity.compute_tree_digest(dest), dest.name)
            self.assertTrue((dest / "controller" / "BUILD_INFO.json").is_file())

    def test_an_edited_installed_byte_is_refused_naming_both_digests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages")
            recorded = runtime.read_json(tree / "controller" / "BUILD_INFO.json")["package_digest"]
            (tree / "controller" / "cli.py").write_text("# edited after install\n")
            with self.assertRaises(SourceSnapshotError) as ctx:
                identity.materialise(tree, Path(td) / "runtime")
            self.assertEqual(ctx.exception.evidence["raised_by"], "materialise")
            self.assertEqual(ctx.exception.evidence["recorded"], recorded)
            self.assertIn(recorded, ctx.exception.message)
            self.assertIn(ctx.exception.evidence["recomputed"], ctx.exception.message)
            self.assertEqual(list((Path(td) / "runtime" / "source").iterdir()), [])

    def test_a_symlink_in_the_installed_tree_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages")
            (tree / "controller" / "extra.py").symlink_to(tree / "controller" / "cli.py")
            with self.assertRaises(SourceSnapshotError) as ctx:
                identity.materialise(tree, Path(td) / "runtime")
            self.assertEqual(ctx.exception.evidence["raised_by"], "materialise")
            self.assertIn("extra.py", ctx.exception.message)

    def test_dirty_or_unknown_provenance_needs_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for name, build, cause in (
                ("dirty", {"source_dirty": True}, "built from uncommitted changes"),
                ("unknown", {"source_commit": None, "source_dirty": None},
                 "built without verifiable source provenance"),
            ):
                tree = fixtures.build_package_tree(Path(td) / name, **build)
                runtime_root = Path(td) / f"runtime-{name}"
                with self.assertRaises(DirtyControllerSourceError) as ctx:
                    identity.materialise(tree, runtime_root)
                self.assertIn(cause, ctx.exception.message)
                self.assertFalse((runtime_root / "source").exists())
                dest = identity.materialise(tree, runtime_root, allow_dirty=True)
                self.assertIsNone(runtime.read_json(dest / "SOURCE_PIN.json")["source_commit"])

    def test_a_second_materialisation_reuses_the_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages")
            runtime_root = Path(td) / "runtime"
            first = identity.materialise(tree, runtime_root)
            second = identity.materialise(tree, runtime_root)
            self.assertEqual(first, second)
            self.assertEqual([p.name for p in (runtime_root / "source").iterdir()], [first.name])

    def test_unidentified_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            _plant_build_info(checkout)
            with self.assertRaises(SourceSnapshotError) as ctx:
                identity.materialise(checkout, Path(td) / "runtime")
            self.assertEqual(ctx.exception.evidence["raised_by"], "materialise")
            self.assertIn(_DELETE_IT, ctx.exception.message)
            self.assertIn("install a wheel built by this project", ctx.exception.message)

    def test_pin_in_a_package_snapshot_cross_checks_the_exec_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tree = fixtures.build_package_tree(Path(td) / "site-packages", source_commit="c" * 40)
            dest = identity.materialise(tree, Path(td) / "runtime")
            carried = {"exec_depth": 1, "source_kind": "package", "source_commit": "c" * 40}
            with _running_from(dest), unittest.mock.patch.dict(
                    os.environ, {identity.EXEC_HANDOFF_ENV: json.dumps(carried)}):
                ident = identity.pin()
            self.assertEqual(ident.runtime_kind, identity.RUNTIME_KIND_PACKAGE)
            self.assertEqual(ident.source_kind, identity.SOURCE_KIND_PACKAGE)
            self.assertEqual(ident.origin_source_root, tree.resolve())
            self.assertEqual(ident.build["source_commit"], "c" * 40)
            self.assertEqual(identity.runtime_record(ident)["package_digest"], ident.build["package_digest"])

            wrong = {**carried, "source_kind": "commit"}
            with _running_from(dest), unittest.mock.patch.dict(
                    os.environ, {identity.EXEC_HANDOFF_ENV: json.dumps(wrong)}):
                with self.assertRaises(SourceSnapshotError) as ctx:
                    identity.pin()
            self.assertEqual(ctx.exception.evidence["raised_by"], "pin")

    def test_a_pin_without_runtime_kind_reads_as_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            checkout = fixtures.build_checkout(Path(td) / "checkout")
            dest = identity.materialise(checkout, Path(td) / "runtime")
            pin_data = runtime.read_json(dest / "SOURCE_PIN.json")
            # Every pin carries `version`, so only the observability fields go.
            for field in ("runtime_kind", "build", "controller_runtime"):
                pin_data.pop(field)
            (dest / "SOURCE_PIN.json").write_text(json.dumps(pin_data))
            with _running_from(dest):
                ident = identity.pin()
            self.assertEqual(ident.runtime_kind, identity.RUNTIME_KIND_SOURCE)
            self.assertIsNone(ident.build)


class RuntimeRecordTest(unittest.TestCase):
    def test_every_field_for_a_release_package(self) -> None:
        build = {"schema_version": 1, "name": "workflow-controller", "version": fixtures.CONTROLLER_VERSION,
                 "source_commit": "c" * 40, "source_dirty": False, "package_digest": "e" * 64,
                 "build_origin": "release", "release_tag": f"v{fixtures.CONTROLLER_VERSION}"}
        ident = identity.ControllerIdentity(
            generation=1, source_root=Path("/s"), origin_source_root=Path("/o"),
            source_kind=identity.SOURCE_KIND_PACKAGE, source_commit="c" * 40, tree_digest="d" * 64,
            generation_source="package", pinned_at="now", version=fixtures.CONTROLLER_VERSION,
            runtime_kind="package", build=build,
        )
        self.assertEqual(identity.runtime_record(ident), {
            "runtime_kind": "package", "version": fixtures.CONTROLLER_VERSION, "source_kind": "package",
            "source_commit": "c" * 40, "tree_digest": "d" * 64, "package_digest": "e" * 64,
            "build_origin": "release", "release_tag": f"v{fixtures.CONTROLLER_VERSION}", "generation": 1,
        })


if __name__ == "__main__":
    unittest.main()
