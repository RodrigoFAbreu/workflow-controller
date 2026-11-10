"""`workflow-controller-h4e` CP1: the private clone of a Codex worker (`controller.codex_worker_tree`), D1 and D2.

Offline, over a real managed repository and the clone the preparer makes of it (`fixtures.git_init`): the clone's shape,
what the managed repository's hooks, filters and fsmonitor do not run, and one test per refusal cause with the managed
repository unchanged after it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_worker_sandbox as ws, codex_worker_tree as tree, gitrepo  # noqa: E402
from tests import fixtures  # noqa: E402

BRANCH = "trunk"


def git(path: Path, *args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=check, env=env)


def out(path: Path, *args: str) -> str:
    return git(path, *args).stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.root = self.tmp / "managed"
        fixtures.git_init(self.root, f"--initial-branch={BRANCH}")
        (self.root / "src.txt").write_text("one\n")
        git(self.root, "add", "src.txt")
        git(self.root, "commit", "-q", "-m", "init")
        self.base = out(self.root, "rev-parse", "HEAD")
        self.managed = SimpleNamespace(root=self.root)
        self.runtime = self.tmp / "runtime"
        self.layout = ws.WorkerLayout(self.runtime, "job1")

    def prepare(self, base: str | None = None, layout: ws.WorkerLayout | None = None, **kw) -> tree.Prepared:
        return tree.prepare(self.managed, layout or self.layout, base or self.base, **kw)

    def snapshot(self) -> dict:
        return {"head": out(self.root, "rev-parse", "HEAD"), "refs": out(self.root, "for-each-ref"),
                "status": out(self.root, "status", "--porcelain", "--untracked-files=all"),
                "config": (self.root / ".git" / "config").read_bytes()}

    def assertRefused(self, cause: str, **kw) -> tree.CodexTreeError:
        before = self.snapshot()
        with self.assertRaises(tree.CodexTreeError) as caught:
            self.prepare(**kw)
        self.assertEqual(caught.exception.cause, cause)
        self.assertEqual(caught.exception.evidence["cause"], cause)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.layout.worktree.exists())
        return caught.exception

    def marker(self, name: str = "fired") -> Path:
        return self.tmp / f"fired-{name}"

    def script(self, name: str, *, passthrough: bool = True) -> Path:
        path = self.tmp / f"script-{name}"
        path.write_text(f"#!/bin/sh\ncat\ntouch {self.marker(name)}\n"
                        if passthrough else f"#!/bin/sh\ntouch {self.marker(name)}\n")
        path.chmod(0o755)
        return path


class AllowDirtyTest(Fixture):
    """`prepare(allow_dirty=...)` (`workflow-controller-h4e3` D5): the clone is made from the commit when the given set
    accounts for every dirty path, and the clean-tree refusal stands otherwise."""

    def test_a_dirty_tree_is_accepted_only_when_every_dirty_path_is_in_the_set(self) -> None:
        (self.root / "src.txt").write_text("two\n")
        (self.root / "new.txt").write_text("x\n")
        self.assertRefused("managed_tree_not_clean", allow_dirty=["src.txt"])
        self.assertRefused("managed_tree_not_clean", allow_dirty=[])
        prepared = self.prepare(allow_dirty=["src.txt", "new.txt"])
        self.assertEqual((prepared.path / "src.txt").read_text(), "one\n")
        self.assertFalse((prepared.path / "new.txt").exists())

    def test_the_other_refusals_stand_and_a_clean_tree_needs_no_set(self) -> None:
        git(self.root, "checkout", "-q", "-b", "other")
        self.assertRefused("managed_branch_moved", base="0" * 40, allow_dirty=["src.txt"])
        self.assertEqual(self.prepare(allow_dirty=[]).base, self.base)


class CloneShapeTest(Fixture):
    def test_the_clone_is_a_separate_repository_with_nothing_connecting_it_back(self) -> None:
        prepared = self.prepare()
        clone = prepared.path
        self.assertEqual(clone, self.layout.worktree)
        self.assertTrue((clone / ".git").is_dir() and not (clone / ".git").is_symlink())
        self.assertFalse((clone / ".git" / "objects" / "info" / "alternates").exists())
        self.assertEqual(out(clone, "remote"), "")
        self.assertEqual(out(clone, "config", "--local", "core.hooksPath"), "/dev/null")
        self.assertEqual(out(clone, "config", "--local", "gc.auto"), "0")
        self.assertEqual(out(clone, "config", "--local", "maintenance.auto"), "false")
        self.assertEqual(out(clone, "config", "--local", "user.name"), tree.WORKER_AUTHOR_NAME)
        self.assertEqual(out(clone, "config", "--local", "user.email"), tree.WORKER_AUTHOR_EMAIL)
        self.assertEqual(out(clone, "symbolic-ref", "--short", "HEAD"), BRANCH)
        self.assertEqual(out(clone, "rev-parse", "HEAD"), self.base)
        self.assertEqual((clone / "src.txt").read_text(), "one\n")
        self.assertEqual(out(clone, "for-each-ref", "--format=%(refname)"), f"refs/heads/{BRANCH}")
        ws.check_worktree(clone)
        self.assertEqual((prepared.branch, prepared.base), (BRANCH, self.base))
        ws.check_guard(prepared.guard, ws.guard_digest(clone))
        self.assertEqual(prepared.to_dict(), {"path": str(clone), "branch": BRANCH, "base": self.base,
                                              "guard": prepared.guard})

    def test_a_commit_made_as_the_sandboxed_shell_makes_it_succeeds_with_the_worker_author(self) -> None:
        clone = self.prepare().path
        env = ws.command_environment(self.layout)
        Path(env["HOME"]).mkdir(parents=True)
        (clone / "new.txt").write_text("two\n")
        for args in (["add", "new.txt"], ["commit", "-q", "-m", "worker commit"]):
            done = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(out(clone, "log", "-1", "--format=%an <%ae>|%cn <%ce>"),
                         f"{tree.WORKER_AUTHOR_NAME} <{tree.WORKER_AUTHOR_EMAIL}>|"
                         f"{tree.WORKER_AUTHOR_NAME} <{tree.WORKER_AUTHOR_EMAIL}>")

    def test_a_commit_that_exists_only_on_another_branch_is_absent_from_the_clone(self) -> None:
        git(self.root, "switch", "-q", "-c", "other")
        (self.root / "other.txt").write_text("o\n")
        git(self.root, "add", "other.txt")
        git(self.root, "commit", "-q", "-m", "other")
        other = out(self.root, "rev-parse", "HEAD")
        git(self.root, "switch", "-q", BRANCH)
        clone = self.prepare().path
        self.assertNotEqual(git(clone, "cat-file", "-e", other, check=False).returncode, 0)

    def test_a_managed_repository_with_alternates_yields_a_clone_whose_guard_accepts(self) -> None:
        donor = self.tmp / "donor"
        fixtures.git_init(donor)
        (donor / "d.txt").write_text("d\n")
        git(donor, "add", "d.txt")
        git(donor, "commit", "-q", "-m", "donor")
        info = self.root / ".git" / "objects" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "alternates").write_text(f"{donor / '.git' / 'objects'}\n")
        prepared = self.prepare()
        self.assertFalse((prepared.path / ".git" / "objects" / "info" / "alternates").exists())
        ws.check_guard(prepared.guard, ws.guard_digest(prepared.path))


class NothingRunsTest(Fixture):
    def test_a_managed_hook_filter_and_fsmonitor_run_nothing_in_the_clone(self) -> None:
        hooks = self.root / ".git" / "hooks"
        for name in ("post-checkout", "post-index-change", "reference-transaction", "post-merge"):
            (hooks / name).write_text(f"#!/bin/sh\ntouch {self.marker(name)}\n")
            (hooks / name).chmod(0o755)
        (self.root / ".git" / "info").mkdir(exist_ok=True)
        (self.root / ".git" / "info" / "attributes").write_text("*.txt filter=probe\n")
        git(self.root, "config", "filter.probe.clean", f"{self.script('clean')}")
        git(self.root, "config", "filter.probe.smudge", f"{self.script('smudge')}")
        git(self.root, "config", "core.fsmonitor", f"{self.script('fsmonitor', passthrough=False)}")
        # bring the index's stat data up to date under the operator's own filter, then forget what that fired
        os.utime(self.root / "src.txt", (1_000_000_000, 1_000_000_000))
        git(self.root, "update-index", "--really-refresh")
        for fired in self.tmp.glob("fired-*"):
            fired.unlink()
        prepared = self.prepare()
        self.assertEqual((prepared.path / "src.txt").read_text(), "one\n")
        for name in ("post-checkout", "post-index-change", "clean", "smudge", "fsmonitor"):
            self.assertFalse(self.marker(name).exists(), name)
        # the clone holds no filter key, so nothing selects a program there
        self.assertEqual(git(prepared.path, "config", "--local", "--get-regexp", "^filter", check=False).stdout, "")


class RefusalTest(Fixture):
    def test_a_detached_head(self) -> None:
        git(self.root, "switch", "-q", "--detach")
        self.assertRefused("detached_head")

    def test_a_managed_branch_that_moved_off_the_recorded_commit(self) -> None:
        (self.root / "next.txt").write_text("n\n")
        git(self.root, "add", "next.txt")
        git(self.root, "commit", "-q", "-m", "next")
        error = self.assertRefused("managed_branch_moved")
        self.assertEqual(error.evidence["base"], self.base)

    def test_a_modified_file(self) -> None:
        (self.root / "src.txt").write_text("changed\n")
        self.assertRefused("managed_tree_not_clean")

    def test_a_staged_file(self) -> None:
        (self.root / "staged.txt").write_text("s\n")
        git(self.root, "add", "staged.txt")
        self.assertRefused("managed_tree_not_clean")

    def test_an_intent_to_add_entry(self) -> None:
        (self.root / "intent.txt").write_text("i\n")
        git(self.root, "add", "-N", "intent.txt")
        self.assertRefused("managed_tree_not_clean")

    def test_an_untracked_file(self) -> None:
        (self.root / "loose.txt").write_text("l\n")
        error = self.assertRefused("managed_tree_not_clean")
        self.assertEqual(error.evidence["untracked"], ["loose.txt"])

    def test_a_read_only_clone_is_made_of_a_dirty_tree_and_returns_the_dirty_paths(self) -> None:
        (self.root / "src.txt").write_text("changed\n")
        (self.root / "loose.txt").write_text("l\n")
        prepared = self.prepare(read_only=True)
        self.assertEqual(prepared.dirty, {"tracked_changes": [" M src.txt"], "untracked": ["loose.txt"]})
        self.assertEqual((prepared.path / "src.txt").read_text(), "one\n")
        self.assertFalse((prepared.path / "loose.txt").exists())
        self.assertEqual(prepared.to_dict()["dirty"], prepared.dirty)

    def test_a_read_only_clone_of_a_clean_tree_has_empty_dirty_paths_and_a_default_clone_has_none(self) -> None:
        self.assertEqual(self.prepare(read_only=True).dirty, {"tracked_changes": [], "untracked": []})
        self.assertIsNone(self.prepare(layout=ws.WorkerLayout(self.runtime, "job2")).dirty)
        self.assertNotIn("dirty", self.prepare(layout=ws.WorkerLayout(self.runtime, "job3")).to_dict())

    def test_a_read_only_clone_keeps_every_other_refusal(self) -> None:
        (self.root / "next.txt").write_text("n\n")
        git(self.root, "add", "next.txt")
        git(self.root, "commit", "-q", "-m", "next")
        with self.assertRaises(tree.CodexTreeError) as caught:
            self.prepare(read_only=True)
        self.assertEqual(caught.exception.cause, "managed_branch_moved")
        self.assertFalse(self.layout.worktree.exists())

    def test_an_ignored_file_is_not_an_unclean_tree(self) -> None:
        (self.root / ".gitignore").write_text("ign/\n")
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "ignore")
        (self.root / "ign").mkdir()
        (self.root / "ign" / "state").write_text("s\n")
        base = out(self.root, "rev-parse", "HEAD")
        self.assertFalse((self.prepare(base=base).path / "ign").exists())

    def test_a_config_hook_before_any_clone_is_made(self) -> None:
        git(self.root, "config", "hook.audit.event", "post-merge")
        git(self.root, "config", "hook.audit.command", "true")
        self.assertRefused("managed_hooks_configured")

    def test_a_config_hook_in_an_included_file(self) -> None:
        included = self.tmp / "extra.gitconfig"
        included.write_text("[hook \"audit\"]\n\tevent = post-merge\n\tcommand = true\n")
        git(self.root, "config", "include.path", str(included))
        self.assertRefused("managed_hooks_configured")

    def test_a_runtime_root_inside_the_managed_root(self) -> None:
        for runtime_root in (self.root / ".controller", self.root / "deep" / "runtime"):
            self.layout = ws.WorkerLayout(runtime_root, "job1")
            error = self.assertRefused("runtime_inside_managed")
            self.assertEqual(error.evidence["runtime_root"], str(runtime_root))

    def test_a_runtime_root_that_is_a_link_into_the_managed_root(self) -> None:
        link = self.tmp / "link"
        link.symlink_to(self.root)
        self.layout = ws.WorkerLayout(link / "rt", "job1")
        self.assertRefused("runtime_inside_managed")

    def test_a_clone_that_cannot_be_made_leaves_no_tree(self) -> None:
        self.layout.worktree.mkdir(parents=True)
        (self.layout.worktree / "occupied").write_text("x\n")
        before = self.snapshot()
        with self.assertRaises(tree.CodexTreeError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.cause, "clone_failed")
        self.assertIn("stderr", caught.exception.evidence)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.layout.worktree.exists())

    def test_an_unreadable_managed_repository_is_clone_failed(self) -> None:
        self.managed = SimpleNamespace(root=self.tmp / "not-a-repository")
        (self.tmp / "not-a-repository").mkdir()
        with self.assertRaises(tree.CodexTreeError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.cause, "clone_failed")

    def test_every_cause_is_named(self) -> None:
        self.assertEqual(set(tree.CAUSES), {
            "detached_head", "managed_branch_moved", "managed_tree_not_clean", "runtime_inside_managed",
            "managed_hooks_configured", "clone_failed"})


if __name__ == "__main__":
    unittest.main()
