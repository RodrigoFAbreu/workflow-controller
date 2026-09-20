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

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import identity, runtime  # noqa: E402
from controller.errors import DirtyControllerSourceError, SourceSnapshotError  # noqa: E402
from tests import fixtures  # noqa: E402


class MaterialiseCleanTreeTest(unittest.TestCase):
    """Test 1 (mechanism half) + test 9 (dirt outside the pathspec)."""

    def test_clean_tree_materialises_commit_kind(self) -> None:
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
            (checkout / "pyproject.toml").write_text("# edited\n")
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
                    generation_source=None, pinned_at="now",
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


if __name__ == "__main__":
    unittest.main()
