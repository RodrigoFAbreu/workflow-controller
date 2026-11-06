"""`workflow-controller-h4e` CP2: landing a Codex worker's commits (`controller.codex_worker_landing`), D3 to D7.

Offline, through real Git: a managed repository, the clone the real preparer makes of it, commits made in that clone
the way a worker would, and one test per refusal cause with the managed repository unchanged after it (except
`landing_unverified` and `merge_left_tree_dirty`, which say what they leave). `decide` is tested over each crash
window of D7, reading and writing nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import codex_worker_landing as landing, codex_worker_sandbox as ws, codex_worker_tree as tree, gitrepo, review  # noqa: E402
from controller.errors import GitOperationError  # noqa: E402
from tests import fixtures  # noqa: E402

BRANCH = "trunk"
JOB = "job1"


def git(path: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
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
        (self.root / ".gitignore").write_text("ign/\n")
        git(self.root, "add", "src.txt", ".gitignore")
        git(self.root, "commit", "-q", "-m", "init")
        self.base = out(self.root, "rev-parse", "HEAD")
        self.managed = SimpleNamespace(root=self.root)
        self.runtime = self.tmp / "runtime"
        self.layout = ws.WorkerLayout(self.runtime, JOB)
        self.prepared = tree.prepare(self.managed, self.layout, self.base)
        self.clone = self.prepared.path
        self.intents: list[str] = []

    # -- helpers --------------------------------------------------------------------------------------------------
    def commit(self, name: str = "work.txt", text: str = "w\n", message: str = "work", *, force: bool = False) -> str:
        path = self.clone / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(self.clone, "add", *(["-f"] if force else []), name)
        git(self.clone, "commit", "-q", "-m", message)
        return out(self.clone, "rev-parse", "HEAD")

    def land(self, *, breach: bool = False, on_intent=None, prepared=None) -> landing.Landing:
        return landing.land(self.managed, prepared or self.prepared, self.runtime, JOB, worker_breach=breach,
                            on_intent=on_intent or self.intents.append)

    def snapshot(self) -> dict:
        return {"head": out(self.root, "rev-parse", "HEAD"), "refs": out(self.root, "for-each-ref"),
                "status": out(self.root, "status", "--porcelain", "--untracked-files=all", "--ignored"),
                "config": (self.root / ".git" / "config").read_bytes(),
                "index": (self.root / ".git" / "index").read_bytes()}

    def assertRefused(self, result: landing.Landing, cause: str, *, moved: bool = False) -> None:
        self.assertEqual((result.outcome, result.cause, result.moved), ("refused", cause, moved), result.message)

    def assertUnchanged(self, before: dict) -> None:
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(out(self.root, "for-each-ref", "refs/controller"), "")

    def refuses(self, cause: str, **kw) -> landing.Landing:
        before = self.snapshot()
        result = self.land(**kw)
        self.assertRefused(result, cause)
        self.assertUnchanged(before)
        return result

    def marker(self, name: str) -> Path:
        return self.tmp / f"fired-{name}"

    def hook(self, name: str) -> None:
        path = self.root / ".git" / "hooks" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!/bin/sh\ntouch {self.marker(name)}\n")
        path.chmod(0o755)

    def fired(self) -> list[str]:
        return sorted(p.name for p in self.tmp.glob("fired-*"))


class LandedTest(Fixture):
    def test_one_commit_lands_byte_for_byte(self) -> None:
        tip = self.commit()
        result = self.land()
        self.assertEqual((result.outcome, result.cause, result.moved, result.ref_left), ("landed", None, True, False))
        self.assertEqual((result.base, result.tip, result.commits), (self.base, tip, (tip,)))
        self.assertEqual(out(self.root, "rev-parse", f"refs/heads/{BRANCH}"), tip)
        self.assertEqual(out(self.root, "symbolic-ref", "HEAD"), f"refs/heads/{BRANCH}")
        self.assertEqual((self.root / "work.txt").read_text(), "w\n")
        self.assertEqual(out(self.root, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(out(self.root, "for-each-ref", "refs/controller"), "")
        self.assertEqual(self.intents, [tip])
        self.assertEqual(result.to_block(clone_kept=False)["commits"], [tip])

    def test_several_commits_land_with_every_id_unchanged(self) -> None:
        commits = [self.commit(f"f{i}.txt", f"{i}\n", f"c{i}") for i in range(3)]
        result = self.land()
        self.assertEqual(result.commits, tuple(commits))
        self.assertEqual(out(self.root, "rev-list", "--reverse", f"{self.base}..HEAD").split(), commits)
        self.assertEqual(out(self.root, "rev-parse", "HEAD"), commits[-1])

    def test_an_unchanged_clone_is_nothing_and_writes_nothing(self) -> None:
        before = self.snapshot()
        with mock.patch.object(gitrepo, "_landing_git", side_effect=AssertionError("a write")):
            result = self.land()
        self.assertEqual((result.outcome, result.cause, result.moved, result.tip),
                         ("nothing", landing.NOTHING_TO_LAND, False, self.base))
        self.assertUnchanged(before)
        self.assertEqual(self.intents, [])

    def test_a_committed_symlink_lands_as_a_link_and_the_target_is_untouched(self) -> None:
        outside = self.tmp / "outside.txt"
        outside.write_text("keep\n")
        (self.clone / "link").symlink_to(outside)
        git(self.clone, "add", "link")
        git(self.clone, "commit", "-q", "-m", "link")
        result = self.land()
        self.assertEqual(result.outcome, "landed")
        self.assertTrue((self.root / "link").is_symlink())
        self.assertEqual(os.readlink(self.root / "link"), str(outside))
        self.assertEqual(outside.read_text(), "keep\n")

    def test_the_worker_author_survives_the_landing(self) -> None:
        (self.clone / "plain.txt").write_text("p\n")
        subprocess.run(["git", "-C", str(self.clone), "add", "plain.txt"], check=True)
        subprocess.run(["git", "-C", str(self.clone), "commit", "-q", "-m", "plain"], check=True)  # the clone's own identity
        self.land()
        self.assertEqual(out(self.root, "log", "-1", "--format=%an <%ae>"),
                         f"{tree.WORKER_AUTHOR_NAME} <{tree.WORKER_AUTHOR_EMAIL}>")


class CheckRefusalTest(Fixture):
    def test_a_recorded_breach_is_refused_before_anything_whatever_the_commits_look_like(self) -> None:
        self.commit()
        with mock.patch.object(ws, "guard_digest", side_effect=AssertionError("read after a breach")):
            result = self.refuses("sandbox_breach", breach=True)
        self.assertEqual(self.intents, [])
        self.assertEqual(result.to_block(clone_kept=True)["outcome"], "refused")

    def test_clone_git_changed_for_a_planted_hook(self) -> None:
        self.commit()
        hook = self.clone / ".git" / "hooks" / "post-checkout"
        hook.write_text("#!/bin/sh\ntouch x\n")
        self.refuses("clone_git_changed")

    def test_clone_git_changed_for_a_changed_config_key(self) -> None:
        self.commit()
        git(self.clone, "config", "--local", "core.pager", "evil")
        self.refuses("clone_git_changed")

    def test_clone_git_changed_for_a_changed_identity(self) -> None:
        self.commit()
        git(self.clone, "config", "--local", "user.name", "someone else")
        self.refuses("clone_git_changed")

    def test_clone_git_changed_for_a_symlink_under_dot_git(self) -> None:
        self.commit()
        (self.clone / ".git" / "evil").symlink_to(self.tmp)
        self.refuses("clone_git_changed")

    def test_clone_git_changed_for_an_alternates_file(self) -> None:
        self.commit()
        (self.clone / ".git" / "objects" / "info").mkdir(exist_ok=True)
        (self.clone / ".git" / "objects" / "info" / "alternates").write_text(str(self.root / ".git" / "objects") + "\n")
        self.refuses("clone_git_changed")

    def test_extra_refs_for_a_tag_a_branch_and_a_stash(self) -> None:
        for kind in ("tag", "branch", "stash"):
            with self.subTest(kind=kind):
                self.setUp()
                self.commit()
                if kind == "tag":
                    git(self.clone, "tag", "v1")
                elif kind == "branch":
                    git(self.clone, "branch", "other")
                else:
                    (self.clone / "src.txt").write_text("changed\n")
                    git(self.clone, "stash", "-q")
                result = self.refuses("extra_refs")
                self.assertIn({"tag": "refs/tags/v1", "branch": "refs/heads/other", "stash": "refs/stash"}[kind],
                              result.message)

    def test_extra_refs_for_a_head_that_is_not_the_branch(self) -> None:
        self.commit()
        git(self.clone, "checkout", "-q", "--detach")
        self.refuses("extra_refs")

    def test_managed_branch_moved(self) -> None:
        self.commit()
        (self.root / "more.txt").write_text("m\n")
        git(self.root, "add", "more.txt")
        git(self.root, "commit", "-q", "-m", "moved")
        self.refuses("managed_branch_moved")

    def test_managed_head_on_another_branch(self) -> None:
        self.commit()
        git(self.root, "checkout", "-q", "-b", "side")
        self.refuses("managed_branch_moved")

    def test_managed_tree_not_clean_for_a_tracked_and_an_untracked_change(self) -> None:
        for name in ("src.txt", "new.txt"):
            with self.subTest(name=name):
                self.setUp()
                self.commit()
                (self.root / name).write_text("dirty\n")
                result = self.refuses("managed_tree_not_clean")
                self.assertTrue(any(name in entry for entry in result.dirty), result.dirty)

    def test_managed_hooks_configured_for_a_config_hook_and_one_reached_by_include_path(self) -> None:
        self.commit()
        git(self.root, "config", "hook.mine.event", "post-merge")
        git(self.root, "config", "hook.mine.command", f"touch {self.marker('config')}")
        self.assertIn("hook.mine.event", self.refuses("managed_hooks_configured").message)
        git(self.root, "config", "--unset-all", "hook.mine.event")
        git(self.root, "config", "--unset-all", "hook.mine.command")
        extra = self.tmp / "extra.cfg"
        extra.write_text(f"[hook \"inc\"]\n\tevent = post-merge\n\tcommand = touch {self.marker('include')}\n")
        git(self.root, "config", "include.path", str(extra))
        self.refuses("managed_hooks_configured")
        self.assertEqual(self.fired(), [])

    def test_runtime_inside_managed_refused_before_the_fetch_with_nothing_forged_landing(self) -> None:
        """I2 of round 6: a worker commit that force-adds ``.controller/jobs/forged.json`` into a managed runtime."""
        runtime = self.root / ".controller"
        (self.root / ".gitignore").write_text("ign/\n.controller/\n")
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "ignore runtime")
        (runtime / "jobs").mkdir(parents=True)
        (runtime / "jobs" / "real.json").write_text('{"real": true}\n')
        base = out(self.root, "rev-parse", "HEAD")
        layout = ws.WorkerLayout(runtime, "job2")
        with mock.patch.object(tree, "runtime_is_inside", return_value=False):
            prepared = tree.prepare(self.managed, layout, base)
        git(prepared.path, "checkout", "-q", BRANCH)
        (prepared.path / ".controller" / "jobs").mkdir(parents=True)
        (prepared.path / ".controller" / "jobs" / "forged.json").write_text('{"forged": true}\n')
        git(prepared.path, "add", "-f", ".controller/jobs/forged.json")
        git(prepared.path, "commit", "-q", "-m", "forge")
        before = self.snapshot()
        real = (runtime / "jobs" / "real.json").read_bytes()
        with mock.patch.object(gitrepo, "landing_fetch", side_effect=AssertionError("fetched")):
            result = landing.land(self.managed, prepared, runtime, "job2", worker_breach=False,
                                  on_intent=self.intents.append)
        self.assertRefused(result, "runtime_inside_managed")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((runtime / "jobs" / "forged.json").exists())
        self.assertEqual((runtime / "jobs" / "real.json").read_bytes(), real)
        self.assertEqual(self.intents, [])


class RangeRefusalTest(Fixture):
    def test_not_a_descendant_for_a_rewritten_history_that_drops_the_base(self) -> None:
        git(self.clone, "checkout", "-q", "--orphan", "fresh")
        git(self.clone, "rm", "-rfq", ".")
        (self.clone / "other.txt").write_text("o\n")
        git(self.clone, "add", "other.txt")
        git(self.clone, "commit", "-q", "-m", "rewritten")
        git(self.clone, "branch", "-M", BRANCH)
        self.refuses("not_a_descendant")

    def test_a_missing_branch_is_not_a_descendant(self) -> None:
        real = gitrepo.ref_commit

        def ref(root, name, **kw):
            if Path(root) == self.clone and name == f"refs/heads/{BRANCH}":
                return None
            return real(root, name, **kw)

        self.commit()
        with mock.patch.object(gitrepo, "ref_commit", side_effect=ref):
            result = self.land()
        self.assertRefused(result, "not_a_descendant")

    def test_merge_commit(self) -> None:
        git(self.clone, "checkout", "-q", "-b", "side")
        self.commit("side.txt")
        git(self.clone, "checkout", "-q", BRANCH)
        self.commit("main.txt")
        git(self.clone, "merge", "-q", "--no-ff", "-m", "merge", "side")
        git(self.clone, "branch", "-D", "side")
        self.refuses("merge_commit")

    def test_too_many_commits(self) -> None:
        with mock.patch.object(landing, "MAX_LANDED_COMMITS", 2):
            for i in range(3):
                self.commit(f"f{i}.txt")
            self.refuses("too_many_commits")

    def test_submodule_for_a_gitlink_a_gitmodules_change_and_a_gitlink_added_then_removed(self) -> None:
        sha = out(self.clone, "rev-parse", "HEAD")
        for kind in ("gitlink", "gitmodules", "added_then_removed"):
            with self.subTest(kind=kind):
                self.setUp()
                sha = out(self.clone, "rev-parse", "HEAD")
                if kind == "gitmodules":
                    self.commit(".gitmodules", '[submodule "x"]\n\tpath = x\n\turl = ./x\n')
                else:
                    git(self.clone, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
                    git(self.clone, "commit", "-q", "-m", "gitlink")
                    if kind == "added_then_removed":
                        git(self.clone, "rm", "-q", "--cached", "sub")
                        git(self.clone, "commit", "-q", "-m", "remove it")
                self.refuses("submodule")

    def test_submodule_for_removing_or_replacing_a_gitlink_the_base_holds(self) -> None:
        """Self-review: the gitlink is only on the old side of the commit's diff, with no ``.gitmodules`` change."""
        for kind in ("removed", "replaced"):
            with self.subTest(kind=kind):
                self.setUp()
                git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.base},sub")
                git(self.root, "commit", "-q", "-m", "a gitlink")
                (self.root / "sub").mkdir()
                base = out(self.root, "rev-parse", "HEAD")
                prepared = tree.prepare(self.managed, ws.WorkerLayout(self.runtime, "job2"), base)
                self.clone = prepared.path
                git(self.clone, "rm", "-q", "--cached", "sub")
                if kind == "replaced":
                    (self.clone / "sub").rmdir()
                    (self.clone / "sub").write_text("a file\n")
                    git(self.clone, "add", "sub")
                git(self.clone, "commit", "-q", "-m", "drop the gitlink")
                self.refuses("submodule", prepared=prepared)

    def test_a_forged_commit_graph_cannot_hide_a_merge_commit(self) -> None:
        """O1 of round 4: ``.git/objects`` is outside the guard, so the worker can write a commit graph that gives the
        merge commit one parent; the range checks read with the graph off."""
        git(self.clone, "checkout", "-q", "-b", "side")
        self.commit("side.txt")
        git(self.clone, "checkout", "-q", BRANCH)
        self.commit("main.txt")
        git(self.clone, "merge", "-q", "--no-ff", "-m", "merge", "side")
        git(self.clone, "branch", "-D", "side")
        merge = out(self.clone, "rev-parse", "HEAD")
        git(self.clone, "commit-graph", "write", "--reachable")
        graph = self.clone / ".git" / "objects" / "info" / "commit-graph"
        graph.chmod(0o644)
        data = bytearray(graph.read_bytes())
        chunks, hash_len = data[6], 20
        table = {bytes(data[8 + 12 * i:12 + 12 * i]): int.from_bytes(data[12 + 12 * i:20 + 12 * i], "big")
                 for i in range(chunks)}
        count = (table[b"OIDF"] and int.from_bytes(data[table[b"OIDF"] + 255 * 4:table[b"OIDF"] + 256 * 4], "big"))
        oids = [bytes(data[table[b"OIDL"] + i * hash_len:table[b"OIDL"] + (i + 1) * hash_len]).hex()
                for i in range(count)]
        entry = table[b"CDAT"] + oids.index(merge) * (hash_len + 16)
        data[entry + hash_len + 4:entry + hash_len + 8] = (0x70000000).to_bytes(4, "big")  # no second parent
        graph.write_bytes(bytes(data))
        forged = out(self.clone, "rev-list", "--parents", "-n", "1", merge).split()
        self.assertEqual(len(forged), 2, "the forged graph must hide the second parent, or this test proves nothing")
        self.assertEqual(len(out(self.clone, "-c", "core.commitGraph=false", "rev-list", "--parents", "-n", "1",
                                 merge).split()), 3)
        self.refuses("merge_commit")


class FetchAndMergeRefusalTest(Fixture):
    def test_fetch_failed_for_a_crafted_tree_entry_that_names_dot_git(self) -> None:
        blob = out(self.clone, "hash-object", "-w", "--stdin") if False else subprocess.run(
            ["git", "-C", str(self.clone), "hash-object", "-w", "--stdin"], input="x\n", capture_output=True,
            text=True, check=True).stdout.strip()
        inner = subprocess.run(["git", "-C", str(self.clone), "mktree"], input=f"100644 blob {blob}\tx\n",
                               capture_output=True, text=True, check=True).stdout.strip()
        bad = subprocess.run(["git", "-C", str(self.clone), "mktree", "--missing"], input=f"040000 tree {inner}\t.git\n",
                             capture_output=True, text=True, check=True).stdout.strip()
        commit = subprocess.run(["git", "-C", str(self.clone), "-c", "user.name=t", "-c", "user.email=t@t",
                                 "commit-tree", bad, "-p", self.base, "-m", "crafted"],
                                capture_output=True, text=True, check=True).stdout.strip()
        git(self.clone, "update-ref", f"refs/heads/{BRANCH}", commit)
        before = self.snapshot()
        result = self.land()
        self.assertRefused(result, "fetch_failed")
        self.assertUnchanged(before)
        self.assertEqual(self.intents, [commit])

    def test_a_base_object_overlaid_in_the_clone_cannot_hide_a_gitlink_addition(self) -> None:
        """I1 of the external round: the worker overwrites the loose object of the base's root tree under its own id
        with a tree that already holds a gitlink, then commits a child whose tree holds that gitlink and an ordinary
        change. Git does not verify an object's id on those reads, so in the clone the child's diff shows no gitlink;
        the managed repository, which holds the authentic base tree, shows it."""
        import zlib

        def run(*args: str, stdin: str | None = None) -> str:
            return subprocess.run(["git", "-C", str(self.clone), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                  input=stdin, capture_output=True, text=True, check=True).stdout.strip()

        src, ignore = run("rev-parse", f"{self.base}:src.txt"), run("rev-parse", f"{self.base}:.gitignore")
        two = run("hash-object", "-w", "--stdin", stdin="two\n")
        link = f"160000 commit {self.base}\tsub\n"
        forged_tree = run("mktree", "--missing", stdin=f"100644 blob {ignore}\t.gitignore\n100644 blob {src}\tsrc.txt\n{link}")
        child_tree = run("mktree", "--missing", stdin=f"100644 blob {ignore}\t.gitignore\n100644 blob {two}\tsrc.txt\n{link}")
        forged = subprocess.run(["git", "-C", str(self.clone), "cat-file", "tree", forged_tree], capture_output=True,
                                check=True).stdout
        base_tree = run("rev-parse", f"{self.base}^{{tree}}")
        # The clone arrives as a pack and Git prefers packed objects, so the worker first explodes the pack to loose
        # objects, then replaces the base tree's.
        for pack in sorted((self.clone / ".git" / "objects" / "pack").glob("*.pack")):
            data = pack.read_bytes()
            for sibling in pack.parent.glob(pack.stem + ".*"):
                sibling.unlink()
            subprocess.run(["git", "-C", str(self.clone), "unpack-objects", "-q"], input=data, check=True)
        loose = self.clone / ".git" / "objects" / base_tree[:2] / base_tree[2:]
        loose.parent.mkdir(parents=True, exist_ok=True)
        loose.unlink(missing_ok=True)
        loose.write_bytes(zlib.compress(b"tree %d\0" % len(forged) + forged))
        child = run("commit-tree", child_tree, "-p", self.base, "-m", "child")
        git(self.clone, "update-ref", f"refs/heads/{BRANCH}", child)
        # Premise: the clone-side check no longer sees the gitlink.
        self.assertFalse(gitrepo.commit_touches_submodule(self.clone, child, self.base))
        before = self.snapshot()
        result = self.land()
        self.assertRefused(result, "submodule")
        self.assertUnchanged(before)
        self.assertTrue(self.clone.exists())
        self.assertEqual(self.intents, [child])

    def test_the_fetch_leaves_an_existing_submodule_alone_whatever_the_recursion_config(self) -> None:
        """I2 of the external round: a submodule the base already holds, ``fetch.recurseSubmodules=true`` in the
        managed repository and an upstream that advanced after the clone. The stack still lands, and the submodule's
        refs are what they were."""
        upstream = self.tmp / "upstream"
        fixtures.git_init(upstream, f"--initial-branch={BRANCH}")
        (upstream / "u.txt").write_text("u\n")
        git(upstream, "add", "u.txt")
        git(upstream, "commit", "-q", "-m", "u1")
        git(self.root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(upstream), "sub")
        git(self.root, "commit", "-q", "-m", "a submodule")
        base = out(self.root, "rev-parse", "HEAD")
        prepared = tree.prepare(self.managed, ws.WorkerLayout(self.runtime, "job2"), base)
        self.clone = prepared.path
        tip = self.commit("work.txt")
        (upstream / "u.txt").write_text("u2\n")
        git(upstream, "commit", "-q", "-am", "u2")
        git(self.root, "config", "fetch.recurseSubmodules", "true")
        git(self.root, "config", "submodule.recurse", "true")
        sub = self.root / "sub"
        refs_before = out(sub, "for-each-ref")
        head_before = out(sub, "rev-parse", "HEAD")
        result = self.land(prepared=prepared)
        self.assertEqual((result.outcome, result.cause, result.tip), ("landed", None, tip), result.message)
        self.assertEqual(out(sub, "for-each-ref"), refs_before)
        self.assertEqual(out(sub, "rev-parse", "HEAD"), head_before)
        self.assertEqual(out(self.root, "status", "--porcelain", "--untracked-files=all"), "")

    def test_a_planted_pack_objects_hook_is_refused_at_the_guard_before_any_fetch(self) -> None:
        self.commit()
        git(self.clone, "config", "--local", "uploadpack.packObjectsHook", f"touch {self.marker('pack')};")
        with mock.patch.object(gitrepo, "landing_fetch", side_effect=AssertionError("fetched")):
            self.refuses("clone_git_changed")
        self.assertEqual(self.fired(), [])

    def test_tip_moved_between_the_checks_and_the_fetch(self) -> None:
        self.commit()
        real = gitrepo.landing_fetch

        def moved(*args, **kw):
            self.commit("late.txt", message="late")  # a surviving worker process
            return real(*args, **kw)

        with mock.patch.object(gitrepo, "landing_fetch", side_effect=moved):
            before = self.snapshot()
            result = self.land()
        self.assertRefused(result, "tip_moved")
        self.assertUnchanged(before)

    def test_would_overwrite_ignored_for_an_ignored_managed_file(self) -> None:
        (self.root / "ign").mkdir()
        (self.root / "ign" / "state").write_text("operator\n")
        self.commit("ign/state", "forged\n", force=True)
        before = self.snapshot()
        result = self.land()
        self.assertRefused(result, "would_overwrite_ignored")
        self.assertUnchanged(before)
        self.assertEqual((self.root / "ign" / "state").read_text(), "operator\n")

    def test_would_overwrite_ignored_for_an_ignored_directory_replaced_by_a_file(self) -> None:
        (self.root / "ign").mkdir()
        (self.root / "ign" / "state").write_text("operator\n")
        self.commit("ign", "now a file\n", force=True)
        before = self.snapshot()
        result = self.land()
        self.assertRefused(result, "would_overwrite_ignored")
        self.assertUnchanged(before)
        self.assertEqual((self.root / "ign" / "state").read_text(), "operator\n")

    def test_a_fast_forward_that_fails_leaves_the_branch_at_base_and_a_clean_tree(self) -> None:
        self.commit()
        real = gitrepo.landing_merge

        def fails(root, sha, *, runner=None):
            raise GitOperationError("git merge failed (exit 2): boom", evidence={"exit_code": 2, "stderr": "boom"})

        with mock.patch.object(gitrepo, "landing_merge", side_effect=fails):
            before = self.snapshot()
            result = self.land()
        self.assertRefused(result, "fast_forward_failed")
        self.assertUnchanged(before)

    def test_merge_left_tree_dirty_when_the_ref_update_fails(self) -> None:
        self.commit()
        lock = self.root / ".git" / "refs" / "heads" / f"{BRANCH}.lock"
        lock.write_text("")
        self.addCleanup(lambda: lock.unlink(missing_ok=True))
        result = self.land()
        self.assertRefused(result, "merge_left_tree_dirty")
        self.assertTrue(any(entry.endswith("work.txt") for entry in result.dirty), result.dirty)
        self.assertEqual(out(self.root, "rev-parse", f"refs/heads/{BRANCH}"), self.base)
        self.assertIn("work.txt", out(self.root, "status", "--porcelain"))
        self.assertEqual(out(self.root, "for-each-ref", "refs/controller"), "")

    def test_a_range_that_removes_the_ignore_rule_is_landing_unverified_and_moved(self) -> None:
        (self.root / "ign").mkdir()
        (self.root / "ign" / "state").write_text("operator\n")
        self.commit(".gitignore", "")
        result = self.land()
        self.assertRefused(result, "landing_unverified", moved=True)
        self.assertEqual(out(self.root, "rev-parse", f"refs/heads/{BRANCH}"), result.tip)
        self.assertEqual(out(self.root, "for-each-ref", "refs/controller"), "")

    def test_on_intent_is_called_once_with_the_checked_tip_before_the_fetch(self) -> None:
        tip = self.commit()
        order: list[str] = []
        real = gitrepo.landing_fetch

        def fetch(*args, **kw):
            order.append("fetch")
            return real(*args, **kw)

        with mock.patch.object(gitrepo, "landing_fetch", side_effect=fetch):
            result = self.land(on_intent=lambda t: order.append(f"intent:{t}"))
        self.assertEqual((result.outcome, order), ("landed", [f"intent:{tip}", "fetch"]))

    def test_on_intent_is_not_called_for_a_refusal_at_the_checks_or_for_nothing(self) -> None:
        self.land()
        self.commit()
        git(self.clone, "tag", "v1")
        self.land()
        self.assertEqual(self.intents, [])

    def test_an_intent_that_raises_is_landing_error_with_nothing_fetched(self) -> None:
        self.commit()

        def boom(_tip: str) -> None:
            raise RuntimeError("disk full")

        with mock.patch.object(gitrepo, "landing_fetch", side_effect=AssertionError("fetched")):
            before = self.snapshot()
            result = self.land(on_intent=boom)
        self.assertRefused(result, "landing_error")
        self.assertIn("disk full", result.message)
        self.assertUnchanged(before)

    def test_landing_error_for_an_unexpected_git_failure_inside_land(self) -> None:
        self.commit()
        with mock.patch.object(gitrepo, "range_commits", side_effect=GitOperationError("rev-list died")):
            result = self.refuses("landing_error")
        self.assertIn("rev-list died", result.message)

    def test_an_os_error_is_landing_error_not_an_exception(self) -> None:
        self.commit()
        with mock.patch.object(gitrepo, "clone_refs", side_effect=OSError("EIO")):
            self.refuses("landing_error")


class AfterMergeTest(Fixture):
    """I1 of round 5: a merge that did not succeed is classified by where the branch stands."""

    def merge_then_raise(self, extra=None):
        real = gitrepo.landing_merge

        def run(root, sha, *, runner=None):
            real(root, sha, runner=runner)
            if extra:
                extra()
            raise GitOperationError("git merge failed (exit 137): killed", evidence={"exit_code": 137, "stderr": ""})

        return mock.patch.object(gitrepo, "landing_merge", side_effect=run)

    def test_a_failed_report_with_the_branch_at_the_tip_is_landed_through_verify_move(self) -> None:
        tip = self.commit()
        with self.merge_then_raise():
            result = self.land()
        self.assertEqual((result.outcome, result.moved, result.tip, result.commits), ("landed", True, tip, (tip,)))
        self.assertEqual(out(self.root, "for-each-ref", "refs/controller"), "")

    def test_a_failed_report_with_a_dirty_tree_is_landing_unverified(self) -> None:
        self.commit()
        with self.merge_then_raise(lambda: (self.root / "stray.txt").write_text("x\n")):
            result = self.land()
        self.assertRefused(result, "landing_unverified", moved=True)
        self.assertIn("stray.txt", result.message)

    def test_a_branch_at_neither_commit_is_landing_unverified(self) -> None:
        self.commit()

        def elsewhere() -> None:
            git(self.root, "commit", "-q", "--allow-empty", "-m", "someone else")

        with self.merge_then_raise(elsewhere):
            result = self.land()
        self.assertRefused(result, "landing_unverified", moved=True)

    def test_a_read_that_fails_inside_verify_move_after_a_successful_merge_is_landing_unverified(self) -> None:
        self.commit()
        real = gitrepo.head_state
        seen = {"merged": False}
        merge = gitrepo.landing_merge

        def merged(*args, **kw):
            merge(*args, **kw)
            seen["merged"] = True

        def head(*args, **kw):
            if seen["merged"]:
                raise GitOperationError("cannot read")
            return real(*args, **kw)

        with mock.patch.object(gitrepo, "landing_merge", side_effect=merged), \
                mock.patch.object(gitrepo, "head_state", side_effect=head):
            result = self.land()
        self.assertRefused(result, "landing_unverified", moved=True)
        self.assertIn("could not be read", result.message)

    def test_an_unreadable_branch_after_a_failed_merge_is_landing_unverified_and_moved(self) -> None:
        self.commit()
        real = gitrepo.ref_commit
        seen = {"merged": False}
        merge = gitrepo.landing_merge

        def merged(*args, **kw):
            merge(*args, **kw)
            seen["merged"] = True
            raise GitOperationError("killed", evidence={"exit_code": 137})

        def ref(root, name, **kw):
            if seen["merged"] and name == f"refs/heads/{BRANCH}":
                raise GitOperationError("garbage ref")
            return real(root, name, **kw)

        with mock.patch.object(gitrepo, "landing_merge", side_effect=merged), \
                mock.patch.object(gitrepo, "ref_commit", side_effect=ref):
            result = self.land()
        self.assertRefused(result, "landing_unverified", moved=True)

    def test_a_failed_landing_ref_delete_after_a_verified_move_stays_landed_with_ref_left(self) -> None:
        self.commit()
        with mock.patch.object(gitrepo, "landing_delete_ref", side_effect=GitOperationError("denied")):
            result = self.land()
        self.assertEqual((result.outcome, result.moved, result.ref_left), ("landed", True, True))
        block = result.to_block(clone_kept=False)
        self.assertEqual((block["ref_left"], block["ref"]), (True, f"refs/controller/landing/{JOB}"))


class HardeningTest(Fixture):
    """D4: the fetch, the merge and the ref delete each run no hook and no automatic maintenance."""

    EVENTS = gitrepo.LANDING_HOOK_EVENTS

    def plant_config_hooks(self) -> None:
        for event in self.EVENTS:
            git(self.root, "config", f"hook.free-{event}.event", event)
            git(self.root, "config", f"hook.free-{event}.command", f"touch {self.marker('cfg-' + event)}")

    def plant_file_hooks(self) -> None:
        for name in ("reference-transaction", "post-index-change", "post-merge", "pre-auto-gc", "post-checkout"):
            self.hook(name)

    def scrubbed(self) -> gitrepo.Runner:
        return landing._runner(self.layout.base, fetch=True)

    def test_each_write_runs_no_file_hook_and_no_config_hook(self) -> None:
        tip = self.commit()
        self.plant_file_hooks()
        self.plant_config_hooks()
        run = self.scrubbed()
        gitrepo.landing_fetch(self.root, self.clone, BRANCH, JOB, runner=run)
        self.assertEqual(self.fired(), [])
        gitrepo.landing_merge(self.root, tip, runner=run)
        self.assertEqual(self.fired(), [])
        gitrepo.landing_delete_ref(self.root, JOB, runner=run)
        self.assertEqual(self.fired(), [])
        self.assertEqual(out(self.root, "rev-parse", "HEAD"), tip)

    def test_the_planted_hooks_do_fire_without_the_hardened_options(self) -> None:
        """The control: without it the test above would pass vacuously."""
        self.plant_file_hooks()
        self.plant_config_hooks()
        git(self.root, "update-ref", "refs/controller/landing/x", self.base)
        git(self.root, "update-ref", "-d", "refs/controller/landing/x")
        git(self.root, "merge", "--ff-only", "-q", "HEAD")
        self.assertIn("fired-reference-transaction", self.fired())
        self.assertIn("fired-cfg-reference-transaction", self.fired())

    def test_a_config_hook_named_like_an_event_makes_the_delete_fail(self) -> None:
        """Git dies with exit 128 under the hardened options; the landing reads that as ``ref_left`` (D6)."""
        self.commit()
        git(self.root, "update-ref", "refs/controller/landing/" + JOB, self.base)
        git(self.root, "config", "hook.post-merge.event", "post-merge")
        git(self.root, "config", "hook.post-merge.command", f"touch {self.marker('named')}")
        with self.assertRaises(GitOperationError) as caught:
            gitrepo.landing_delete_ref(self.root, JOB, runner=self.scrubbed())
        self.assertEqual(caught.exception.evidence["exit_code"], 128)
        self.assertEqual(self.fired(), [])

    def test_a_stale_index_fires_no_post_index_change_through_a_whole_land(self) -> None:
        self.commit()
        self.hook("post-index-change")
        time = os.stat(self.root / "src.txt").st_mtime
        os.utime(self.root / "src.txt", (time + 100, time + 100))  # a stale index: the next status would refresh it
        result = self.land()
        self.assertEqual(result.outcome, "landed")
        self.assertEqual(self.fired(), [])

    def test_a_managed_fsmonitor_program_does_not_run(self) -> None:
        self.commit()
        program = self.tmp / "fsmonitor"
        program.write_text(f"#!/bin/sh\ntouch {self.marker('fsmonitor')}\n")
        program.chmod(0o755)
        git(self.root, "config", "core.fsmonitor", str(program))
        self.assertEqual(self.land().outcome, "landed")
        self.assertEqual(self.fired(), [])

    def gc_events(self, trace: Path) -> list[str]:
        events = []
        for line in trace.read_text().splitlines():
            record = json.loads(line)
            if record.get("event") == "cmd_name":
                events.append(record["name"])
        return events

    def arrange_gc(self) -> None:
        git(self.root, "config", "gc.auto", "1")
        git(self.root, "config", "gc.autoDetach", "false")
        git(self.root, "config", "maintenance.auto", "true")
        (self.root / ".git" / "objects" / "17").mkdir(exist_ok=True)
        for i in range(3):
            (self.root / ".git" / "objects" / "17" / f"{i:038x}").write_bytes(b"")

    def traced(self, trace: Path):
        real = landing._runner

        def runner(ceiling, *, fetch=False):
            env = review._git_env(ceiling)
            env["GIT_OPTIONAL_LOCKS"] = "0"
            env["GIT_TRACE2_EVENT"] = str(trace)
            if fetch:
                env["GIT_ALLOW_PROTOCOL"] = "file"
            return gitrepo.subprocess_runner(env)

        return mock.patch.object(landing, "_runner", side_effect=runner)

    def test_no_automatic_maintenance_runs_through_a_whole_land(self) -> None:
        self.commit()
        self.arrange_gc()
        trace = self.tmp / "trace"
        with self.traced(trace):
            self.assertEqual(self.land().outcome, "landed")
        names = self.gc_events(trace)
        self.assertIn("fetch", names)
        self.assertNotIn("gc", names)
        self.assertNotIn("maintenance", names)

    def test_the_gc_arrangement_does_trigger_maintenance_without_the_hardened_options(self) -> None:
        self.commit()
        self.arrange_gc()
        trace = self.tmp / "trace"
        env = {**review._git_env(self.layout.base), "GIT_TRACE2_EVENT": str(trace), "GIT_ALLOW_PROTOCOL": "file"}
        subprocess.run(["git", "-C", str(self.root), "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--",
                        str(self.clone), f"refs/heads/{BRANCH}:refs/controller/landing/x"],
                       env=env, capture_output=True, check=True)
        names = self.gc_events(trace)
        self.assertTrue({"gc", "maintenance"} & set(names), names)


class DecideTest(Fixture):
    """D7: ``decide`` reads refs and the tree only, writes nothing, never raises."""

    def record(self, *, landing_block: dict | None = None, clone: dict | None = None) -> dict:
        worker = {"clone": clone if clone is not None else self.prepared.to_dict()}
        if landing_block is not None:
            worker["landing"] = landing_block
        return {"job_id": JOB, "codex_worker": worker}

    def intent(self, tip: str) -> dict:
        return {"outcome": "started", "base": self.base, "tip": tip, "branch": BRANCH}

    def decide(self, record: dict) -> landing.Recovery:
        before = self.snapshot()
        with mock.patch.object(gitrepo, "_landing_git", side_effect=AssertionError("a write")):
            result = landing.decide(self.managed, record)
        self.assertEqual(self.snapshot(), before)
        return result

    def moved_branch(self) -> str:
        tip = self.commit()
        git(self.root, "fetch", "-q", "--no-tags", str(self.clone), f"refs/heads/{BRANCH}:refs/controller/landing/{JOB}")
        git(self.root, "merge", "-q", "--ff-only", tip)
        return tip

    def test_a_final_landed_or_nothing_block_goes_to_the_reconciliation_and_removes_the_tree(self) -> None:
        for outcome in ("landed", "nothing"):
            with self.subTest(outcome=outcome):
                block = {"outcome": outcome, "cause": None, "base": self.base, "tip": self.base, "commits": [],
                         "branch": BRANCH, "clone_kept": False, "moved": outcome == "landed", "ref_left": False,
                         "dirty": []}
                rec = self.decide(self.record(landing_block=block))
                self.assertEqual((rec.refused, rec.remove_tree, rec.delete_ref, rec.landing), (False, True, False, block))

    def test_window_a_with_and_without_the_intent_block_and_a_clean_and_dirty_tree(self) -> None:
        tip = self.commit()
        git(self.root, "update-ref", f"refs/controller/landing/{JOB}", self.base)
        cases = {
            "intent_clean": (self.intent(tip), None, "landing_interrupted", False),
            "no_intent_clean": (None, None, "landing_interrupted", False),
            "intent_dirty": (self.intent(tip), "dirty.txt", "merge_left_tree_dirty", False),
            "no_intent_dirty": (None, "dirty.txt", "landing_interrupted", False),
        }
        for name, (block, dirt, cause, moved) in cases.items():
            with self.subTest(name=name):
                path = self.root / "dirty.txt"
                path.unlink(missing_ok=True)
                if dirt:
                    path.write_text("d\n")
                rec = self.decide(self.record(landing_block=block))
                self.assertEqual((rec.refused, rec.cause, rec.delete_ref, rec.remove_tree),
                                 (True, cause, True, False))
                self.assertEqual(rec.landing["moved"], moved)
                self.assertTrue(rec.landing["clone_kept"])
                self.assertEqual(bool(rec.landing["dirty"]), bool(dirt))
        (self.root / "dirty.txt").unlink(missing_ok=True)

    def test_window_b_a_verified_move_is_landed_recovered_and_removes_the_tree(self) -> None:
        tip = self.moved_branch()
        rec = self.decide(self.record(landing_block=self.intent(tip)))
        self.assertEqual((rec.refused, rec.cause, rec.delete_ref, rec.remove_tree), (False, None, True, True))
        self.assertEqual((rec.landing["outcome"], rec.landing["recovered"], rec.landing["commits"], rec.landing["moved"]),
                         ("landed", True, [tip], True))

    def test_window_b_a_range_that_removes_the_ignore_rule_is_landing_unverified_like_the_live_path(self) -> None:
        (self.root / "ign").mkdir()
        (self.root / "ign" / "state").write_text("operator\n")
        tip = self.commit(".gitignore", "")
        git(self.root, "fetch", "-q", "--no-tags", str(self.clone), f"refs/heads/{BRANCH}:refs/controller/landing/{JOB}")
        git(self.root, "merge", "-q", "--ff-only", tip)
        rec = self.decide(self.record(landing_block=self.intent(tip)))
        self.assertEqual((rec.refused, rec.cause, rec.landing["moved"], rec.remove_tree), (True, "landing_unverified", True, False))

    def test_window_b_a_head_left_on_another_branch_is_landing_unverified(self) -> None:
        tip = self.moved_branch()
        git(self.root, "checkout", "-q", "-b", "side")
        rec = self.decide(self.record(landing_block=self.intent(tip)))
        self.assertEqual((rec.cause, rec.landing["moved"]), ("landing_unverified", True))

    def test_window_b_a_filter_that_rewrites_bytes_is_landing_unverified(self) -> None:
        (self.root / ".gitattributes").write_text("*.txt filter=up\n")
        git(self.root, "add", ".gitattributes")
        git(self.root, "commit", "-q", "-m", "attrs")
        base = out(self.root, "rev-parse", "HEAD")
        layout = ws.WorkerLayout(self.runtime, "job9")
        prepared = tree.prepare(self.managed, layout, base)
        (prepared.path / "f.txt").write_text("abc\n")
        git(prepared.path, "add", "f.txt")
        git(prepared.path, "commit", "-q", "-m", "w")
        tip = out(prepared.path, "rev-parse", "HEAD")
        git(self.root, "config", "filter.up.smudge", "tr a-z A-Z")
        git(self.root, "fetch", "-q", "--no-tags", str(prepared.path), f"refs/heads/{BRANCH}:refs/controller/landing/job9")
        git(self.root, "merge", "-q", "--ff-only", tip)
        rec = self.decide({"job_id": "job9", "codex_worker": {
            "clone": prepared.to_dict(), "landing": {"outcome": "started", "base": base, "tip": tip, "branch": BRANCH}}})
        self.assertEqual((rec.cause, rec.landing["moved"]), ("landing_unverified", True))

    def test_window_c_a_branch_at_neither_commit_with_and_without_an_intent_block(self) -> None:
        tip = self.commit()
        git(self.root, "commit", "-q", "--allow-empty", "-m", "someone else")
        for block in (self.intent(tip), None):
            with self.subTest(intent=block is not None):
                rec = self.decide(self.record(landing_block=block))
                self.assertEqual((rec.refused, rec.cause, rec.landing["moved"], rec.delete_ref, rec.remove_tree),
                                 (True, "landing_unverified", True, True, False))

    def test_unreadable_for_a_missing_root_and_malformed_blocks_without_raising(self) -> None:
        tip = self.commit()
        gone = SimpleNamespace(root=self.tmp / "gone")
        cases = {
            "missing root": (gone, self.record(landing_block=self.intent(tip))),
            "no clone block": (self.managed, {"job_id": JOB, "codex_worker": {}}),
            "malformed clone": (self.managed, self.record(clone={"path": 3})),
            "malformed landing": (self.managed, self.record(landing_block="started")),
            "landing missing fields": (self.managed, self.record(landing_block={"outcome": "started"})),
            "no codex_worker": (self.managed, {"job_id": JOB}),
        }
        for name, (managed, record) in cases.items():
            with self.subTest(name=name):
                rec = landing.decide(managed, record)
                self.assertEqual((rec.refused, rec.cause, rec.delete_ref, rec.remove_tree),
                                 (True, "landing_unreadable", False, False))
                self.assertTrue(rec.landing["clone_kept"])
                self.assertIn("could not be read", rec.landing["message"])

    def test_a_garbage_branch_ref_is_unreadable_not_window_c(self) -> None:
        tip = self.commit()
        (self.root / ".git" / "refs" / "heads" / BRANCH).write_text("garbage\n")
        rec = landing.decide(self.managed, self.record(landing_block=self.intent(tip)))
        self.assertEqual(rec.cause, "landing_unreadable")

    def test_a_garbage_index_makes_window_a_unreadable(self) -> None:
        self.commit()
        (self.root / ".git" / "index").write_bytes(b"garbage")
        rec = landing.decide(self.managed, self.record(landing_block=None))
        self.assertEqual(rec.cause, "landing_unreadable")

    def test_recover_is_the_decision_part(self) -> None:
        self.commit()
        record = self.record()
        self.assertEqual(landing.recover(self.managed, record), landing.decide(self.managed, record))


if __name__ == "__main__":
    unittest.main()
