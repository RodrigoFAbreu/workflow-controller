"""`workflow-controller-codex-tester-bridge`: the Codex tester's sandbox bridge (`controller.codex_bridge`).
Offline: no `codex`, no network; the candidate is captured from a scratch Git repository and the Codex home
controls use no process.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_bridge as cb, codex_sandbox as cs, functional_review as fr, review  # noqa: E402
from controller.errors import ReviewLaunchRefusedError  # noqa: E402
from tests.test_codex_sandbox import Fixture as SandboxFixture  # noqa: E402
from tests.test_functional_sandbox import git, make_repo  # noqa: E402


class BridgeFixture(SandboxFixture):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "repo"
        self.head = make_repo(self.repo, {"build_notes.txt": "x\n"})

    def candidate(self) -> fr.Candidate:
        return fr.capture_candidate(self.repo, self.head)

    def bridge(self, candidate: fr.Candidate | None = None, **kw) -> cb.Bridge:
        kw.setdefault("runtime_root", self.runtime)
        kw.setdefault("job_id", "job1")
        kw.setdefault("work_item_id", "item-1")
        kw.setdefault("operator_home", self.operator)
        kw.setdefault("binary", self.binary)
        kw.setdefault("system_dir", self.system)
        kw.setdefault("real_home", self.real_home)
        return cb.build_bridge(candidate or self.candidate(), **kw)


class LayoutTest(BridgeFixture):
    def test_the_clone_equals_the_manifest_with_modes_and_links(self) -> None:
        bridge = self.bridge()
        clone = bridge.clone
        self.assertEqual(clone, self.runtime / "functional" / "job1" / "tree")
        self.assertEqual(fr.walk_differences(clone, bridge.candidate, bridge.check_dir, bridge.writable_links), [])
        self.assertTrue(os.access(clone / "tool.sh", os.X_OK))
        self.assertEqual(os.readlink(clone / "link"), "src")
        self.assertEqual((clone / "src" / "app.py").read_text(), "print('hello')\n")

    def test_the_scratch_origin_holds_the_branch_and_the_remote_points_at_it(self) -> None:
        bridge = self.bridge()
        self.assertEqual(git(bridge.clone, "remote", "get-url", "origin"), str(bridge.origin))
        self.assertEqual(git(bridge.origin, "rev-parse", f"refs/heads/{bridge.candidate.branch}"), self.head)
        self.assertEqual(git(bridge.clone, "rev-parse", "HEAD"), self.head)

    def test_writable_files_are_inside_the_scratch_area_and_the_controllers_files_outside(self) -> None:
        bridge = self.bridge()
        scratch = bridge.layout.scratch
        for path in (bridge.origin, bridge.out, bridge.writable_dir, *bridge.writable_links.values()):
            self.assertTrue(path.exists(), path)
            self.assertTrue(path.is_relative_to(scratch), path)
        for path in (bridge.check_dir, bridge.layout.base / "ITEM"):
            self.assertTrue(path.exists(), path)
            self.assertFalse(path.is_relative_to(scratch), path)
            self.assertFalse(path.is_relative_to(bridge.clone), path)

    def test_writable_locations_are_linked_hidden_from_git_and_refused_ones_are_listed(self) -> None:
        repo = self.tmp / "repo2"
        head = make_repo(repo, {".gitignore": "build/\n*.pyc\n!keep\n"})
        bridge = self.bridge(fr.capture_candidate(repo, head), job_id="job2")
        self.assertEqual(bridge.writable, ("build",))
        self.assertEqual(os.readlink(bridge.clone / "build"), str(bridge.writable_dir / "build"))
        self.assertEqual(git(bridge.clone, "status", "--porcelain"), "")
        self.assertEqual({item.pattern for item in bridge.refused}, {"*.pyc", "!keep"})
        self.assertTrue(all(item.reason for item in bridge.refused))

    def test_the_stand_ins_are_on_the_path_directory(self) -> None:
        bridge = self.bridge()
        for name in fr.FAKE_PROGRAMS:
            launcher = bridge.layout.bin / name
            self.assertTrue(launcher.is_file(), name)
            self.assertTrue(stat.S_IMODE(launcher.stat().st_mode) & 0o111, name)

    def test_the_command_is_built_and_the_prompt_stays_on_standard_input(self) -> None:
        bridge = self.bridge()
        self.assertEqual(bridge.built.argv[-1], "-")
        self.assertEqual(bridge.layout.scratch_tmp, bridge.layout.scratch / "tmp")

    def test_the_profile_lists_only_the_clone_and_the_scratch_area_for_the_bridges_files(self) -> None:
        bridge = self.bridge()
        profile = tomllib.loads(bridge.built.config_text)["permissions"][cs.PROFILE]["filesystem"]
        writes = [path for path, access in profile.items() if access == "write"]
        self.assertEqual(writes, [str(bridge.layout.scratch)])
        files = [bridge.out, bridge.origin, bridge.writable_dir, bridge.check_dir, bridge.layout.base / "ITEM",
                 bridge.clone / "src" / "app.py"]
        for path in files:
            covering = {entry for entry in profile if entry != ":minimal" and path.is_relative_to(entry)}
            expected = {str(bridge.clone)} if path.is_relative_to(bridge.clone) else (
                {str(bridge.layout.scratch)} if path.is_relative_to(bridge.layout.scratch) else set())
            self.assertEqual(covering, expected, path)


class RefusalTest(BridgeFixture):
    def test_a_startup_loaded_entry_in_the_codex_home_refuses_and_leaves_no_folder(self) -> None:
        real = review.build_home

        def plant(layout, operator_home):
            digest = real(layout, operator_home)
            (layout.codex_home / "hooks.json").write_text("{}")
            return digest

        with mock.patch.object(review, "build_home", plant):
            with self.assertRaises(ReviewLaunchRefusedError):
                self.bridge()
        self.assertFalse((self.runtime / "functional" / "job1").exists())

    def test_a_pre_existing_job_folder_refuses_and_is_left_alone(self) -> None:
        folder = self.runtime / "functional" / "job1"
        folder.mkdir(parents=True)
        (folder / "keep").write_text("mine")
        with self.assertRaises(fr.TesterUnavailable) as caught:
            self.bridge()
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_ENFORCEMENT)
        self.assertEqual((folder / "keep").read_text(), "mine")

    def test_a_checkout_conversion_ends_at_clone_mismatch_and_removes_the_folder(self) -> None:
        real = fr._plain

        def convert(cwd: Path, *args: str) -> None:
            return real(cwd, *(("-c", "core.autocrlf=true", *args) if args[0] == "checkout" else args))

        repo = self.tmp / "crlf"
        head = make_repo(repo, {"win.txt": "a\r\nb\r\n"})
        candidate = fr.capture_candidate(repo, head)
        with mock.patch.object(fr, "_plain", convert):
            with self.assertRaises(fr.TesterUnavailable) as caught:
                self.bridge(candidate)
        self.assertEqual(caught.exception.evidence["cause"], fr.UNAVAILABLE_CLONE_MISMATCH)
        self.assertFalse((self.runtime / "functional" / "job1").exists())

    def test_a_failure_after_the_folder_exists_removes_it(self) -> None:
        with mock.patch.object(fr, "make_writable_links", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.bridge()
        self.assertFalse((self.runtime / "functional" / "job1").exists())


class KeptTest(BridgeFixture):
    def test_kept_sandboxes_finds_the_codex_job_and_remove_sandbox_removes_it(self) -> None:
        self.bridge()
        self.assertEqual(fr.kept_sandboxes(self.runtime, "item-1"), ["job1"])
        self.assertEqual(fr.kept_sandboxes(self.runtime, "other"), [])
        fr.remove_sandbox(self.runtime, "job1")
        self.assertFalse((self.runtime / "functional" / "job1").exists())
        self.assertEqual(fr.kept_sandboxes(self.runtime, "item-1"), [])


if __name__ == "__main__":
    unittest.main()
