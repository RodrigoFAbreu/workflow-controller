"""`workflow-controller-verify-codex` CP1: the Codex build digest (`controller.codex_build`).
Offline: fake install trees in a temporary folder; no `node` or `codex` is run.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_build as cb  # noqa: E402

PLATFORM = "@openai/codex-linux-x64"


def write(path: Path, text: str = "x", mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()

    def npm(self, base: Path, *, nested: bool = True, manifest: str | None = None, shebang: str = "#!/usr/bin/env node\n",
            interp: bool = True) -> tuple[Path, Path]:
        """An npm global layout under ``base``; returns (launcher symlink, package root)."""
        pkg = base / "lib/node_modules/@openai/codex"
        write(pkg / "package.json", manifest or f'{{"name": "@openai/codex", "optionalDependencies": {{"{PLATFORM}": "1"}}}}')
        write(pkg / "bin/codex.js", shebang + "// launcher\n", 0o755)
        if nested:
            write(pkg / "node_modules" / PLATFORM / "package.json", "{}")
            write(pkg / "node_modules" / PLATFORM / "vendor/codex", "native", 0o755)
        launcher = base / "bin/codex"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.symlink_to(pkg / "bin/codex.js")
        if interp:
            write(base / "sys/node", "node", 0o755)
        return launcher, pkg

    def digest(self, launcher: Path, base: Path) -> str | None:
        return cb.build_digest(launcher, system_path=str(base / "sys"))


class BuildDigestTests(Tree):
    def test_npm_layout_changes_with_each_file(self) -> None:
        launcher, pkg = self.npm(self.tmp)
        base = self.digest(launcher, self.tmp)
        self.assertRegex(base or "", r"^[0-9a-f]{64}$")
        native = pkg / "node_modules" / PLATFORM / "vendor/codex"
        native.write_text("other")
        self.assertNotEqual(self.digest(launcher, self.tmp), base)
        native.write_text("native")
        self.assertEqual(self.digest(launcher, self.tmp), base)
        (pkg / "bin/codex.js").write_text("#!/usr/bin/env node\n// changed\n")
        self.assertNotEqual(self.digest(launcher, self.tmp), base)
        (pkg / "bin/codex.js").write_text("#!/usr/bin/env node\n// launcher\n")
        self.assertEqual(self.digest(launcher, self.tmp), base)
        native.chmod(0o644)
        self.assertNotEqual(self.digest(launcher, self.tmp), base)
        native.chmod(0o755)
        write(pkg / "extra.txt")
        self.assertNotEqual(self.digest(launcher, self.tmp), base)

    def test_copy_at_another_path_has_the_same_digest(self) -> None:
        a, b = self.tmp / "a", self.tmp / "b/deeper"
        la, _ = self.npm(a)
        lb, _ = self.npm(b)
        self.assertIsNotNone(self.digest(la, a))
        self.assertEqual(self.digest(la, a), self.digest(lb, b))

    def test_nvm_layout_with_hoisted_platform_package(self) -> None:
        launcher, pkg = self.npm(self.tmp, nested=False, interp=False)
        write(self.tmp / "lib/node_modules" / PLATFORM / "package.json", "{}")
        native = write(self.tmp / "lib/node_modules" / PLATFORM / "vendor/codex", "hoisted", 0o755)
        write(self.tmp / "bin/node", "node", 0o755)
        base = self.digest(launcher, self.tmp)
        self.assertIsNotNone(base)
        native.write_text("hoisted2")
        self.assertNotEqual(self.digest(launcher, self.tmp), base)
        # the nearer copy wins
        write(pkg / "node_modules" / PLATFORM / "package.json", "{}")
        write(pkg / "node_modules" / PLATFORM / "vendor/codex", "near", 0o755)
        near = self.digest(launcher, self.tmp)
        native.write_text("hoisted3")
        self.assertEqual(self.digest(launcher, self.tmp), near)

    def test_global_folder_of_the_resolved_interpreter(self) -> None:
        launcher, _ = self.npm(self.tmp, nested=False, interp=False)
        real = self.tmp / "prefix/bin/node"
        write(real, "node", 0o755)
        (self.tmp / "sys").mkdir()
        (self.tmp / "sys/node").symlink_to(real)
        global_pkg = self.tmp / "prefix/lib/node" / PLATFORM
        write(global_pkg / "package.json", "{}")
        native = write(global_pkg / "vendor/codex", "g", 0o755)
        present = self.digest(launcher, self.tmp)
        self.assertIsNotNone(present)
        native.write_text("g2")
        self.assertNotEqual(self.digest(launcher, self.tmp), present)
        # without the folder the dependency is simply skipped
        native.unlink()
        (global_pkg / "package.json").unlink()
        skipped = self.digest(launcher, self.tmp)
        self.assertIsNotNone(skipped)
        self.assertNotEqual(skipped, present)

    def test_pnpm_layout_follows_a_dependency_symlink(self) -> None:
        for inside in (False, True):
            with self.subTest(inside=inside):
                base = self.tmp / ("in" if inside else "out")
                launcher, pkg = self.npm(base, nested=False)
                real = base / ".pnpm" / PLATFORM
                write(real / "package.json", "{}")
                native = write(real / "vendor/codex", "p", 0o755)
                link = (pkg / "node_modules" / PLATFORM) if inside else (base / "lib/node_modules" / PLATFORM)
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(real)
                before = self.digest(launcher, base)
                self.assertIsNotNone(before)
                native.write_text("p2")
                self.assertNotEqual(self.digest(launcher, base), before)

    def test_stray_package_json_is_never_used(self) -> None:
        launcher, pkg = self.npm(self.tmp)
        # a package.json above the nearest node_modules, carrying dependencies, is not the package's manifest
        write(self.tmp / "lib/package.json", '{"dependencies": {"x": "1"}}')
        self.assertIsNotNone(self.digest(launcher, self.tmp))
        # a single file with a stray package.json beside it and no node_modules above it
        single = write(self.tmp / "solo/codex", "binary", 0o755)
        write(self.tmp / "solo/package.json", '{"dependencies": {"x": "1"}}')
        self.assertIsNotNone(self.digest(single, self.tmp))
        # node_modules/node_modules is never searched
        launcher2, pkg2 = self.npm(self.tmp / "n2", nested=False)
        write(self.tmp / "n2/lib/node_modules/node_modules" / PLATFORM / "package.json", "{}")
        write(self.tmp / "n2/lib/node_modules/node_modules" / PLATFORM / "v", "hidden")
        a = self.digest(launcher2, self.tmp / "n2")
        write(self.tmp / "n2/lib/node_modules/node_modules" / PLATFORM / "v", "hidden2")
        self.assertEqual(self.digest(launcher2, self.tmp / "n2"), a)

    def test_interpreter_inside_or_outside_the_roots(self) -> None:
        launcher, pkg = self.npm(self.tmp)
        base = self.digest(launcher, self.tmp)
        (self.tmp / "sys/node").write_text("node 2")
        self.assertEqual(self.digest(launcher, self.tmp), base)
        write(pkg / "bin/node", "inner", 0o755)
        launcher.unlink()
        write(pkg / "bin/codex.js", f"#!{pkg}/bin/node\n// launcher\n", 0o755)
        launcher.symlink_to(pkg / "bin/codex.js")
        inner = self.digest(launcher, self.tmp)
        self.assertIsNotNone(inner)
        (pkg / "bin/node").write_text("inner2")
        self.assertNotEqual(self.digest(launcher, self.tmp), inner)

    def test_refusals(self) -> None:
        def fresh(name: str, **kw) -> tuple[Path, Path, Path]:
            base = self.tmp / name
            launcher, pkg = self.npm(base, **kw)
            return launcher, pkg, base

        launcher, pkg, base = fresh("script")
        outside = write(base / "shim/codex", "#!/bin/sh\nexec x\n", 0o755)
        self.assertIsNone(self.digest(outside, base))
        launcher, pkg, base = fresh("deps", manifest='{"dependencies": {"a": "1"}}')
        self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("peer", manifest='{"peerDependencies": {"a": "1"}}')
        self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("envopt", shebang="#!/usr/bin/env -S node\n")
        self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("nointerp", interp=False)
        self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("badjson", manifest="{nope")
        self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("leave")
        (pkg / "escape").symlink_to(self.tmp)
        self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("unreadable")
        secret = write(pkg / "secret", "s", 0o000)
        self.addCleanup(secret.chmod, 0o644)
        if os.geteuid() != 0:
            self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("fifo")
        os.mkfifo(pkg / "pipe")
        self.assertIsNone(self.digest(launcher, base))
        launcher, pkg, base = fresh("many")
        write(pkg / "a", "a")
        self.assertIsNotNone(self.digest(launcher, base))
        original = cb.MAX_ENTRIES
        cb.MAX_ENTRIES = 3
        self.addCleanup(setattr, cb, "MAX_ENTRIES", original)
        self.assertIsNone(self.digest(launcher, base))

    def test_dependency_found_nowhere_is_skipped(self) -> None:
        launcher, pkg = self.npm(self.tmp, nested=False)
        self.assertIsNotNone(self.digest(launcher, self.tmp))

    def test_non_script_single_file_hashes_that_file(self) -> None:
        single = write(self.tmp / "solo/codex", "binary", 0o755)
        before = self.digest(single, self.tmp)
        self.assertIsNotNone(before)
        single.write_text("binary2")
        self.assertNotEqual(self.digest(single, self.tmp), before)
        self.assertIsNone(self.digest(self.tmp / "missing", self.tmp))


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class RecordTests(Tree):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "runtime"
        self.root.mkdir(mode=0o700)
        self.record = self.root / cb.RECORD_NAME

    def test_names_are_not_the_claude_ones(self) -> None:
        self.assertEqual(cb.RECORD_NAME, "verified-codex-builds.json")
        self.assertEqual(cb.LOCK_NAME, "verified-codex-builds.lock")

    def test_missing_record_is_empty_without_reason(self) -> None:
        self.assertEqual(cb.read_verified(self.root), ({}, None))

    def test_record_merges_two_entries_and_is_private(self) -> None:
        cb.record_verified(self.root, DIGEST_A, {"version": "1"})
        cb.record_verified(self.root, DIGEST_B, {"version": "2"})
        builds, note = cb.read_verified(self.root)
        self.assertEqual(sorted(builds), [DIGEST_A, DIGEST_B])
        self.assertIsNone(note)
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.root / cb.LOCK_NAME).exists())
        self.assertFalse((self.root / "verified-claude-builds.json").exists())
        self.assertFalse((self.root / "verified-claude-builds.lock").exists())

    def test_non_digest_refused(self) -> None:
        with self.assertRaises(ValueError):
            cb.record_verified(self.root, "nope", {})
        self.assertFalse(self.record.exists())

    def test_symlink_record_refused_and_target_untouched(self) -> None:
        target = write(self.tmp / "target.json", "keep", 0o600)
        self.record.symlink_to(target)
        with self.assertRaises(OSError):
            cb.record_verified(self.root, DIGEST_A, {})
        self.assertEqual(target.read_text(), "keep")
        builds, note = cb.read_verified(self.root)
        self.assertEqual(builds, {})
        self.assertIn("was ignored", note or "")

    def test_untrustworthy_records_admit_nothing(self) -> None:
        good = '{"schema_version": 1, "builds": {"%s": {}}}' % DIGEST_A
        cases = {
            "group-writable": (good, 0o660),
            "malformed": ("{not json", 0o600),
            "wrong schema": ('{"schema_version": 2, "builds": {}}', 0o600),
            "bad digest": ('{"schema_version": 1, "builds": {"zz": {}}}', 0o600),
        }
        for name, (text, mode) in cases.items():
            with self.subTest(name):
                write(self.record, text, mode)
                builds, note = cb.read_verified(self.root)
                self.assertEqual(builds, {})
                self.assertIn("was ignored", note or "")

    def test_foreign_owner_is_ignored(self) -> None:
        write(self.record, '{"schema_version": 1, "builds": {}}', 0o600)
        real = os.geteuid
        try:
            os.geteuid = lambda: real() + 1  # type: ignore[assignment]
            builds, note = cb.read_verified(self.root)
        finally:
            os.geteuid = real  # type: ignore[assignment]
        self.assertEqual(builds, {})
        self.assertIn("was ignored", note or "")


class IsVerifiedTests(Tree):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "runtime"
        self.root.mkdir(mode=0o700)
        self.launcher, self.pkg = self.npm(self.tmp / "install")
        self.sys = str(self.tmp / "install/sys")

    def check(self) -> cb.Verified:
        return cb.is_verified(self.root, self.launcher, system_path=self.sys)

    def test_recorded_build_is_verified_and_an_edit_is_not(self) -> None:
        digest = cb.build_digest(self.launcher, system_path=self.sys)
        cb.record_verified(self.root, digest, {"version": "1"})
        self.assertEqual(self.check(), cb.Verified(True, digest, None))
        (self.pkg / "extra.txt").write_text("edited")
        result = self.check()
        self.assertFalse(result.ok)
        self.assertIn("not recorded", result.reason or "")

    def test_unbindable_build_is_not_verified(self) -> None:
        launcher, _ = self.npm(self.tmp / "bad", manifest='{"dependencies": {"a": "1"}}')
        result = cb.is_verified(self.root, launcher, system_path=str(self.tmp / "bad/sys"))
        self.assertFalse(result.ok)
        self.assertIsNone(result.digest)
        self.assertIn("cannot be bound", result.reason or "")

    def test_missing_record_says_so(self) -> None:
        result = self.check()
        self.assertFalse(result.ok)
        self.assertIn("no verified-codex-builds.json", result.reason or "")

    def test_ignored_record_says_it_was_ignored(self) -> None:
        write(self.root / cb.RECORD_NAME, "{bad", 0o600)
        result = self.check()
        self.assertFalse(result.ok)
        self.assertIn("was ignored", result.reason or "")

    def test_writes_nothing(self) -> None:
        digest = cb.build_digest(self.launcher, system_path=self.sys)
        before = sorted(p.name for p in self.root.iterdir())
        self.check()
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        cb.record_verified(self.root, digest, {})
        before = {p.name: p.stat().st_mtime_ns for p in self.root.iterdir()}
        self.check()
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in self.root.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
