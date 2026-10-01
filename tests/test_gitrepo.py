"""``controller.gitrepo``: the target-repository Git boundary
(``workflow-controller-trunk-branch-pr-release-orchestration`` CP3), over
real disposable repositories -- a bare origin and a clone
(``fixtures.build_origin_pair``)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import gitrepo  # noqa: E402
from controller.errors import GitOperationError  # noqa: E402
from tests.fixtures import build_origin_pair, commit_all, current_head, git_clone, git_init, run  # noqa: E402


class _Spy:
    """A runner that records every argv, then runs it for real."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._real = gitrepo.subprocess_runner()

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self._real(argv)

    def subcommands(self) -> list[str]:
        return [argv[3] for argv in self.calls]


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.origin, self.clone = build_origin_pair(self.tmp)
        self.base = current_head(self.clone)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def commit(self, name: str, text: str, root: Path | None = None) -> str:
        root = root or self.clone
        (root / name).write_text(text)
        return commit_all(root, f"change {name}")

    def second_clone(self) -> Path:
        other = self.tmp / "other"
        git_clone(self.origin, other)
        run(["git", "config", "user.email", "other@example.invalid"], cwd=other)
        run(["git", "config", "user.name", "Other"], cwd=other)
        return other


class ReadTest(_Case):
    def test_head_state_attached_detached_and_unborn(self) -> None:
        self.assertEqual(gitrepo.head_state(self.clone), gitrepo.HeadState("main", self.base))
        run(["git", "switch", "-q", "--detach"], cwd=self.clone)
        state = gitrepo.head_state(self.clone)
        self.assertTrue(state.detached)
        self.assertEqual(state.commit, self.base)
        unborn = self.tmp / "unborn"
        git_init(unborn, "--initial-branch=trunk")
        self.assertEqual(gitrepo.head_state(unborn), gitrepo.HeadState("trunk", None))

    def test_ref_reads(self) -> None:
        first = self.commit("a.txt", "a\n")
        self.assertEqual(gitrepo.ref_commit(self.clone, "refs/heads/main"), first)
        self.assertIsNone(gitrepo.ref_commit(self.clone, "refs/heads/nope"))
        self.assertTrue(gitrepo.is_ancestor(self.clone, self.base, first))
        self.assertFalse(gitrepo.is_ancestor(self.clone, first, self.base))
        self.assertEqual(gitrepo.ahead_behind(self.clone, first, self.base), (1, 0))
        self.assertEqual(gitrepo.merge_base(self.clone, first, self.base), self.base)
        self.assertEqual(gitrepo.first_parent_log(self.clone, self.base, first), [first])
        self.assertEqual(gitrepo.common_dir(self.clone), (self.clone / ".git").resolve())
        self.assertEqual(gitrepo.remote_url(self.clone, "origin"), str(self.origin))
        self.assertIsNone(gitrepo.remote_url(self.clone, "upstream"))

    def test_an_unknown_revision_is_a_failure_not_a_negative_answer(self) -> None:
        with self.assertRaises(GitOperationError):
            gitrepo.is_ancestor(self.clone, "0" * 40, self.base)
        with self.assertRaises(GitOperationError):
            gitrepo.head_state(self.tmp / "not-a-repo-at-all")

    def test_tracked_changes_ignore_untracked_files(self) -> None:
        (self.clone / "untracked.txt").write_text("x\n")
        self.assertEqual(gitrepo.tracked_changes(self.clone), [])
        (self.clone / "README.md").write_text("changed\n")
        self.assertEqual(gitrepo.tracked_changes(self.clone), [" M README.md"])

    def test_show_distinguishes_absent_from_failure(self) -> None:
        self.assertEqual(gitrepo.show(self.clone, "HEAD", "README.md"), b"fixture\n")
        self.assertIsNone(gitrepo.show(self.clone, "HEAD", "missing.txt"))
        with self.assertRaises(GitOperationError):
            gitrepo.show(self.clone, "no-such-rev", "README.md")

    def test_commit_trailers_read_the_last_paragraph(self) -> None:
        (self.clone / "t.txt").write_text("t\n")
        run(["git", "add", "t.txt"], cwd=self.clone)
        run(["git", "commit", "-q", "-m", "Subject\n\nBody.\n\nWorkflow-Checkpoint: CP3\n"
             "Workflow-Work-Item: wi-1"], cwd=self.clone)
        self.assertEqual(gitrepo.commit_trailers(self.clone, "HEAD"),
                         [("Workflow-Checkpoint", "CP3"), ("Workflow-Work-Item", "wi-1")])
        self.assertEqual(gitrepo.commit_trailers(self.clone, self.base), [])

    def test_ls_remote_peels_annotated_tags_and_omits_absent_refs(self) -> None:
        run(["git", "tag", "-a", "-m", "v", "v1.0.0"], cwd=self.clone)
        run(["git", "push", "-q", "origin", "refs/tags/v1.0.0"], cwd=self.clone)
        refs = gitrepo.ls_remote(self.clone, "origin",
                                 ["refs/tags/v1.0.0", "refs/heads/main", "refs/heads/absent"])
        self.assertEqual(refs, {"refs/tags/v1.0.0": self.base, "refs/heads/main": self.base})

    def test_ls_remote_tags_lists_every_tag_peeled(self) -> None:
        self.assertEqual(gitrepo.ls_remote_tags(self.clone, "origin"), {})
        second = self.commit("b.txt", "b\n")
        run(["git", "tag", "-a", "-m", "annotated", "v1.0.0", self.base], cwd=self.clone)
        run(["git", "tag", "light", second], cwd=self.clone)
        run(["git", "push", "-q", "origin", "refs/tags/v1.0.0", "refs/tags/light"], cwd=self.clone)
        self.assertEqual(gitrepo.ls_remote_tags(self.clone, "origin"),
                         {"v1.0.0": self.base, "light": second})

    def test_worktree_branches_reports_linked_and_detached_worktrees(self) -> None:
        one, two = self.tmp / "wt-one", self.tmp / "wt-two"
        run(["git", "worktree", "add", "-q", "-b", "milestone/a", str(one)], cwd=self.clone)
        run(["git", "worktree", "add", "-q", "--detach", str(two)], cwd=self.clone)
        branches = gitrepo.worktree_branches(self.clone)
        self.assertEqual(branches, {str(self.clone.resolve()): "main", str(one.resolve()): "milestone/a",
                                    str(two.resolve()): None})
        self.assertEqual(gitrepo.worktree_branches(one), branches)

    def test_first_parent_subjects(self) -> None:
        first = self.commit("a.txt", "a\n")
        run(["git", "switch", "-q", "-c", "side"], cwd=self.clone)
        side = self.commit("b.txt", "b\n")
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        (self.clone / "c.txt").write_text("c\n")
        run(["git", "add", "c.txt"], cwd=self.clone)
        # A multi-line first paragraph is one subject; the body is not read.
        run(["git", "commit", "-q", "-m", "feat: two\nlines", "-m", "body: ignored"], cwd=self.clone)
        second = current_head(self.clone)
        run(["git", "merge", "-q", "--no-ff", "-m", "Merge side", "side"], cwd=self.clone)
        merge = current_head(self.clone)
        spy = _Spy()
        self.assertEqual(gitrepo.first_parent_subjects(self.clone, first, merge, runner=spy),
                         [(second, "feat: two lines"), (merge, "Merge side")])
        self.assertEqual(spy.subcommands(), ["rev-list"])
        self.assertNotIn(side, [sha for sha, _ in gitrepo.first_parent_subjects(self.clone, None, merge)])
        self.assertEqual(gitrepo.first_parent_subjects(self.clone, None, merge),
                         [(self.base, "Initial commit"), (first, "change a.txt"),
                          (second, "feat: two lines"), (merge, "Merge side")])
        self.assertEqual(gitrepo.first_parent_subjects(self.clone, merge, merge), [])
        with self.assertRaises(GitOperationError):
            gitrepo.first_parent_subjects(self.clone, None, "no-such-rev")


class FetchTest(_Case):
    def test_fetch_into_remote_tracking_refs(self) -> None:
        other = self.second_clone()
        tip = self.commit("o.txt", "o\n", root=other)
        run(["git", "push", "-q", "origin", "main"], cwd=other)
        self.assertEqual(gitrepo.fetch_branch(self.clone, "origin", "main"), tip)
        # The local branch is untouched.
        self.assertEqual(gitrepo.ref_commit(self.clone, "refs/heads/main"), self.base)

    def test_forced_or_local_branch_refspecs_are_refused_before_running(self) -> None:
        spy = _Spy()
        for refspec in ("+refs/heads/main:refs/remotes/origin/main", "refs/heads/main:refs/heads/main",
                        "refs/heads/main", "refs/tags/v1:refs/tags/v1"):
            with self.assertRaises(GitOperationError, msg=refspec):
                gitrepo.fetch(self.clone, "origin", [refspec], runner=spy)
        self.assertEqual(spy.calls, [])

    def test_tag_fetch_fails_rather_than_overwrite_a_differing_local_tag(self) -> None:
        run(["git", "tag", "v1"], cwd=self.clone)
        other = self.second_clone()
        self.commit("o.txt", "o\n", root=other)
        run(["git", "tag", "v1"], cwd=other)
        run(["git", "push", "-q", "origin", "refs/tags/v1"], cwd=other)
        with self.assertRaises(GitOperationError):
            gitrepo.fetch(self.clone, "origin", ["refs/tags/*:refs/tags/*"])
        self.assertEqual(gitrepo.tag_commit(self.clone, "v1"), self.base)


class BranchTest(_Case):
    def test_create_and_switch_carries_the_tree_and_refuses_an_existing_branch(self) -> None:
        (self.clone / "README.md").write_text("in flight\n")
        gitrepo.create_and_switch(self.clone, "milestone/wi-1")
        self.assertEqual(gitrepo.head_state(self.clone), gitrepo.HeadState("milestone/wi-1", self.base))
        self.assertEqual((self.clone / "README.md").read_text(), "in flight\n")
        with self.assertRaises(GitOperationError):
            gitrepo.create_and_switch(self.clone, "main")

    def test_switch_refuses_a_dirty_tracked_tree(self) -> None:
        gitrepo.create_and_switch(self.clone, "feature")
        (self.clone / "README.md").write_text("dirty\n")
        with self.assertRaises(GitOperationError) as caught:
            gitrepo.switch(self.clone, "main")
        self.assertEqual(caught.exception.evidence["tracked_changes"], [" M README.md"])
        self.assertEqual(gitrepo.head_state(self.clone).branch, "feature")

    def test_fast_forward_only(self) -> None:
        gitrepo.create_and_switch(self.clone, "feature")
        ahead = self.commit("f.txt", "f\n")
        run(["git", "switch", "-q", "main"], cwd=self.clone)
        self.assertEqual(gitrepo.fast_forward(self.clone, "main", "feature"), ahead)
        run(["git", "switch", "-q", "-c", "diverged", self.base], cwd=self.clone)
        self.commit("d.txt", "d\n")
        with self.assertRaises(GitOperationError):
            gitrepo.fast_forward(self.clone, "diverged", "main")
        with self.assertRaises(GitOperationError):
            gitrepo.fast_forward(self.clone, "main", "feature")  # HEAD is not on main


class PushBranchTest(_Case):
    def test_push_creates_then_fast_forwards(self) -> None:
        gitrepo.create_and_switch(self.clone, "milestone/wi-1")
        first = self.commit("a.txt", "a\n")
        self.assertEqual(gitrepo.push_branch(self.clone, "origin", "milestone/wi-1"), first)
        second = self.commit("b.txt", "b\n")
        gitrepo.push_branch(self.clone, "origin", "milestone/wi-1")
        self.assertEqual(gitrepo.ls_remote(self.clone, "origin", ["refs/heads/milestone/wi-1"]),
                         {"refs/heads/milestone/wi-1": second})

    def test_a_diverged_remote_is_refused_without_contacting_it_again(self) -> None:
        gitrepo.create_and_switch(self.clone, "milestone/wi-1")
        self.commit("a.txt", "a\n")
        gitrepo.push_branch(self.clone, "origin", "milestone/wi-1")
        other = self.second_clone()
        run(["git", "switch", "-q", "milestone/wi-1"], cwd=other)
        theirs = self.commit("theirs.txt", "t\n", root=other)
        run(["git", "push", "-q", "origin", "milestone/wi-1"], cwd=other)
        ours = self.commit("ours.txt", "o\n")

        spy = _Spy()
        with self.assertRaises(GitOperationError) as caught:
            gitrepo.push_branch(self.clone, "origin", "milestone/wi-1", runner=spy)
        self.assertEqual(caught.exception.evidence["reason"], "not_fast_forward")
        self.assertEqual(caught.exception.evidence["remote_commit"], theirs)
        self.assertEqual(caught.exception.evidence["local_commit"], ours)
        self.assertNotIn("push", spy.subcommands())
        # Exactly one ls-remote and one fetch reached the remote; the rest were local reads.
        self.assertEqual([c for c in spy.subcommands() if c in ("ls-remote", "fetch")], ["ls-remote", "fetch"])
        self.assertEqual(gitrepo.ls_remote(self.clone, "origin", ["refs/heads/milestone/wi-1"]),
                         {"refs/heads/milestone/wi-1": theirs})


class TagTest(_Case):
    def test_create_and_push_a_new_annotated_tag(self) -> None:
        gitrepo.create_annotated_tag(self.clone, "v1.0.0", self.base, "Release 1.0.0")
        self.assertEqual(run(["git", "cat-file", "-t", "refs/tags/v1.0.0"], cwd=self.clone).stdout.strip(), "tag")
        self.assertEqual(gitrepo.push_tag(self.clone, "origin", "v1.0.0"), self.base)
        self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", "v1.0.0"), self.base)

    def test_a_tag_is_created_as_the_given_tagger(self) -> None:
        tagger = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")
        gitrepo.create_annotated_tag(self.clone, "v1.0.0", self.base, "Release", tagger=tagger)
        line = run(["git", "for-each-ref", "--format=%(taggername) %(taggeremail)", "refs/tags/v1.0.0"],
                   cwd=self.clone).stdout.strip()
        self.assertEqual(line, f"{tagger[0]} <{tagger[1]}>")

    def test_creating_an_existing_local_tag_refuses(self) -> None:
        gitrepo.create_annotated_tag(self.clone, "v1.0.0", self.base, "one")
        with self.assertRaises(GitOperationError) as caught:
            gitrepo.create_annotated_tag(self.clone, "v1.0.0", self.base, "again")
        self.assertEqual(caught.exception.evidence["reason"], "exists")

    def test_pushing_a_tag_the_remote_has_refuses_even_at_the_same_commit(self) -> None:
        for remote_commit_is_ours in (True, False):
            with self.subTest(same_commit=remote_commit_is_ours):
                tag = "v-same" if remote_commit_is_ours else "v-other"
                other = self.second_clone() if not (self.tmp / "other").exists() else self.tmp / "other"
                run(["git", "fetch", "-q", "origin"], cwd=other)
                run(["git", "switch", "-q", "--detach", "origin/main"], cwd=other)
                target = self.base if remote_commit_is_ours else self.commit(f"{tag}.txt", "x\n", root=other)
                run(["git", "tag", "-a", "-m", tag, tag, target], cwd=other)
                run(["git", "push", "-q", "origin", f"refs/tags/{tag}"], cwd=other)
                gitrepo.create_annotated_tag(self.clone, tag, self.base, tag)

                spy = _Spy()
                with self.assertRaises(GitOperationError) as caught:
                    gitrepo.push_tag(self.clone, "origin", tag, runner=spy)
                self.assertEqual(caught.exception.evidence["reason"], "exists")
                self.assertEqual(caught.exception.evidence["remote_commit"], target)
                self.assertNotIn("push", spy.subcommands())
                # Read as "exists", never moved.
                self.assertEqual(gitrepo.remote_tag_commit(self.clone, "origin", tag), target)

    def test_the_remote_tags_message_is_read_without_writing_a_ref(self) -> None:
        other = self.second_clone()
        target = self.commit("remote-only.txt", "x\n", root=other)
        run(["git", "push", "-q", "origin", "HEAD:refs/heads/side"], cwd=other)
        run(["git", "tag", "-a", "--cleanup=verbatim", "-m", "# theirs\n", "v-annotated", target], cwd=other)
        run(["git", "tag", "v-light", target], cwd=other)
        run(["git", "push", "-q", "origin", "refs/tags/v-annotated", "refs/tags/v-light"], cwd=other)
        gitrepo.create_annotated_tag(self.clone, "v-annotated", self.base, "ours")
        refs = run(["git", "for-each-ref"], cwd=self.clone).stdout
        fetch_head = self.clone / ".git" / "FETCH_HEAD"
        fetched = fetch_head.read_bytes() if fetch_head.exists() else None
        self.assertEqual(gitrepo.remote_tag_message(self.clone, "origin", "v-annotated"), b"# theirs\n")
        self.assertIsNone(gitrepo.remote_tag_message(self.clone, "origin", "v-light"))
        self.assertEqual(gitrepo.tag_message(self.clone, "v-annotated"), b"ours\n")
        self.assertEqual(run(["git", "for-each-ref"], cwd=self.clone).stdout, refs)
        self.assertEqual(fetch_head.read_bytes() if fetch_head.exists() else None, fetched)
        with self.assertRaises(GitOperationError):
            gitrepo.remote_tag_message(self.clone, "origin", "v-absent")


class MergeTrunkTest(_Case):
    def _branch_and_trunk(self, *, conflict: bool) -> tuple[str, str]:
        gitrepo.create_and_switch(self.clone, "milestone/wi-1")
        branch_tip = self.commit("README.md" if conflict else "branch.txt", "branch side\n")
        other = self.second_clone()
        trunk_tip = self.commit("README.md" if conflict else "trunk.txt", "trunk side\n", root=other)
        run(["git", "push", "-q", "origin", "main"], cwd=other)
        gitrepo.fetch_branch(self.clone, "origin", "main")
        return branch_tip, trunk_tip

    def test_a_clean_merge_is_two_parent_with_the_branch_first(self) -> None:
        branch_tip, trunk_tip = self._branch_and_trunk(conflict=False)
        merge = gitrepo.merge_trunk(self.clone, "origin", "main")
        parents = run(["git", "rev-list", "--parents", "-n", "1", merge], cwd=self.clone).stdout.split()
        self.assertEqual(parents, [merge, branch_tip, trunk_tip])
        self.assertEqual(gitrepo.head_state(self.clone), gitrepo.HeadState("milestone/wi-1", merge))

    def test_a_conflict_aborts_and_leaves_the_tree_unchanged(self) -> None:
        branch_tip, _ = self._branch_and_trunk(conflict=True)
        (self.clone / "untracked.txt").write_text("kept\n")
        with self.assertRaises(GitOperationError) as caught:
            gitrepo.merge_trunk(self.clone, "origin", "main")
        self.assertEqual(caught.exception.evidence["conflicts"], ["README.md"])
        self.assertTrue(caught.exception.evidence["aborted"])
        self.assertEqual(gitrepo.head_state(self.clone), gitrepo.HeadState("milestone/wi-1", branch_tip))
        self.assertEqual(gitrepo.tracked_changes(self.clone), [])
        self.assertEqual((self.clone / "README.md").read_text(), "branch side\n")
        self.assertEqual((self.clone / "untracked.txt").read_text(), "kept\n")
        self.assertIsNone(gitrepo.ref_commit(self.clone, "MERGE_HEAD"))

    def test_a_dirty_tracked_tree_refuses_before_merging(self) -> None:
        self._branch_and_trunk(conflict=False)
        (self.clone / "branch.txt").write_text("edited\n")
        spy = _Spy()
        with self.assertRaises(GitOperationError):
            gitrepo.merge_trunk(self.clone, "origin", "main", runner=spy)
        self.assertNotIn("merge", spy.subcommands())

    def test_it_is_wired_into_no_lifecycle_path(self) -> None:
        """Neither admitted Workflow release (2.5.1, 2.6.0) has a transition
        that moves a work item's base after an integration merge (E1-E5,
        ``docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md``), so
        no Controller module but its own names it."""
        package = Path(gitrepo.__file__).resolve().parent
        callers = sorted(path.name for path in package.glob("*.py")
                         if path.name != "gitrepo.py" and "merge_trunk" in path.read_text())
        self.assertEqual(callers, [])


class SquashReadTest(_Case):
    """The reads ``milestone_branch.verified_squash`` makes
    (``workflow-controller-squash-merge-tag-versioning`` Design F)."""

    def test_parents_tree_subject_and_identity(self) -> None:
        first = self.commit("a.txt", "a\n")
        self.assertEqual(gitrepo.commit_parents(self.clone, first), [self.base])
        self.assertEqual(gitrepo.commit_parents(self.clone, self.base), [])
        self.assertEqual(gitrepo.tree_of(self.clone, first),
                         run(["git", "rev-parse", f"{first}^{{tree}}"], cwd=self.clone).stdout.strip())
        run(["git", "commit", "-q", "--allow-empty", "-m", "feat: x (#3)\n\nbody\nline"], cwd=self.clone)
        second = current_head(self.clone)
        self.assertEqual(gitrepo.commit_subject(self.clone, second), "feat: x (#3)")
        identity = gitrepo.commit_identity(self.clone, second)
        self.assertEqual(identity.split("\0")[:2], ["Controller Tests", "controller-tests@example.invalid"])
        self.assertTrue(identity.split("\0")[3].startswith("feat: x (#3)\n\nbody\nline"))
        self.assertNotEqual(identity, gitrepo.commit_identity(self.clone, first))
        with self.assertRaises(GitOperationError):
            gitrepo.commit_parents(self.clone, "no-such-rev")

    def test_git_version_parses_and_refuses_garbage(self) -> None:
        self.assertGreaterEqual(gitrepo.git_version(self.clone), (2, 31, 0))
        for out, expected in ((b"git version 2.37.1\n", (2, 37, 1)), (b"git version 2.45.0.windows.1\n", (2, 45, 0)),
                              (b"git version 2.39.2 (Apple Git-143)\n", (2, 39, 2))):
            fake = lambda argv, out=out: subprocess.CompletedProcess(argv, 0, out, b"")  # noqa: E731
            self.assertEqual(gitrepo.git_version(self.clone, runner=fake), expected)
        with self.assertRaises(GitOperationError):
            gitrepo.git_version(self.clone, runner=lambda argv: subprocess.CompletedProcess(argv, 0, b"hg 6\n", b""))

    def test_merge_drivers_from_every_scope(self) -> None:
        self.assertEqual(gitrepo.merge_drivers(self.clone), [])
        run(["git", "config", "merge.ours-please.driver", "true"], cwd=self.clone)
        run(["git", "config", "merge.other.name", "not a driver"], cwd=self.clone)
        self.assertEqual(gitrepo.merge_drivers(self.clone), ["merge.ours-please.driver"])

    def test_merge_tree_clean_conflict_and_failure(self) -> None:
        gitrepo.create_and_switch(self.clone, "side")
        side = self.commit("side.txt", "side\n")
        conflicting = self.commit("README.md", "side\n")
        gitrepo.switch(self.clone, "main")
        trunk = self.commit("README.md", "trunk\n")
        spy = _Spy()
        tree = gitrepo.merge_tree(self.clone, trunk, side, runner=spy)
        self.assertEqual(spy.subcommands(), ["merge-tree"])
        run(["git", "merge", "-q", "--no-edit", side], cwd=self.clone)
        self.assertEqual(tree, gitrepo.tree_of(self.clone, "HEAD"))
        self.assertIsNone(gitrepo.merge_tree(self.clone, trunk, conflicting))
        self.assertEqual(gitrepo.head_state(self.clone).commit, current_head(self.clone))
        self.assertEqual(gitrepo.tracked_changes(self.clone), [])
        with self.assertRaises(GitOperationError):
            gitrepo.merge_tree(self.clone, trunk, "no-such-rev")
        failing = lambda argv: subprocess.CompletedProcess(argv, 0, b"not a tree\n", b"")  # noqa: E731
        with self.assertRaises(GitOperationError):
            gitrepo.merge_tree(self.clone, trunk, side, runner=failing)


class RunnerTest(unittest.TestCase):
    def test_a_missing_git_is_a_git_operation_error(self) -> None:
        def missing(argv):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        with self.assertRaises(GitOperationError):
            gitrepo.head_state(Path("."), runner=missing)

    def test_a_timeout_is_a_git_operation_error(self) -> None:
        def hangs(argv):
            raise subprocess.TimeoutExpired(argv, 1)

        with self.assertRaises(GitOperationError):
            gitrepo.ls_remote(Path("."), "origin", ["refs/heads/main"], runner=hangs)


if __name__ == "__main__":
    unittest.main()
