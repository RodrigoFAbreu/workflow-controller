"""Tests for ``tools/ci_changes.py`` (CI1a, #317): the documentation-only
classifier, the change detection on real temporary repositories, the exit rule
of the aggregate job, and the ``hide`` step (CI2e, #322) that proves the
documentation set unneeded by running the shards without it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

_spec = importlib.util.spec_from_file_location("ci_changes", fixtures.REPO_ROOT / "tools" / "ci_changes.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

class ClassifyTests(unittest.TestCase):
    def test_every_member_of_the_set_is_documentation(self):
        for path in ("docs/items/workflow-controller-a1.md", "docs/items/sub/x.md",
                     "docs/releases/1.2.3.md", "docs/run.md", "docs/README.md", "docs/ROADMAP.md",
                     "docs/release-history.md", "docs/backlog.yaml"):
            with self.subTest(path=path):
                self.assertEqual(cc.classify([path]), "docs")

    def test_everything_else_is_code(self):
        for path in ("docs/guide/commands.md", "docs/guide/ci-and-releases.md", "docs/adr/0001-x.md",
                     "docs/roadmap-archive.md", "docs/ACTIVE_MILESTONE.md",
                     "docs/ai-workflow/WORKFLOW_STATE.json", "docs/milestones/x.md",
                     "docs/other.yaml", "docs/sub/page.md", "docs/items", "docs/items/",
                     "README.md", "pyproject.toml", "tools/x.py", "tests/test_x.py",
                     "controller/x.py", "scripts/x.py", ".github/workflows/tests.yml",
                     ".claude/commands/x.md", "docs/items/../tools/x.py", "/docs/run.md",
                     "docs\\run.md", "docs//run.md", "", "nothing.txt"):
            with self.subTest(path=path):
                self.assertEqual(cc.classify([path]), "code")

    def test_one_code_path_makes_the_change_code(self):
        self.assertEqual(cc.classify(["docs/run.md", "docs/items/x.md"]), "docs")
        self.assertEqual(cc.classify(["docs/run.md", "tools/x.py"]), "code")
        self.assertEqual(cc.classify(["docs/run.md", "docs/guide/commands.md"]), "code")

    def test_the_empty_list_is_code(self):
        self.assertEqual(cc.classify([]), "code")
        self.assertEqual(cc.classify(iter(())), "code")

    def test_every_documented_exception_is_code(self):
        for path, reason in cc.DOCS_TOP_LEVEL_EXCEPTIONS:
            with self.subTest(path=path):
                self.assertTrue(reason)
                self.assertEqual(cc.classify([path]), "code")
        for prefix, reason in cc.NOT_DOCUMENTATION:
            with self.subTest(prefix=prefix):
                self.assertTrue(reason)
                self.assertEqual(cc.classify([prefix + "x.md" if prefix.endswith("/") else prefix]), "code")

    def test_every_entry_of_the_set_has_a_reason(self):
        for table in (cc.DOCS_PREFIXES, cc.DOCS_FILES, cc.DOCS_TOP_LEVEL_EXCEPTIONS, cc.NOT_DOCUMENTATION):
            for entry in table:
                with self.subTest(entry=entry):
                    self.assertTrue(entry[-1].strip())


# ---------------------------------------------------------------------------
# detect, on real temporary repositories
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return fixtures.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args], env=env).stdout


def _apply(repo: Path, changes: dict[str, str | None]) -> None:
    """Write (or, for ``None``, delete) each repository-relative path."""
    for rel, text in changes.items():
        target = repo / rel
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)


def _commit(repo: Path, changes: dict[str, str | None], message: str = "c") -> None:
    _apply(repo, changes)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


class DetectRepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = fixtures.git_init(Path(self._tmp.name) / "repo", "-b", "main")
        _commit(self.repo, {"docs/run.md": "run\n", "docs/items/a.md": "a\n", "docs/items/b.md": "b\n",
                            "tools/x.py": "x = 1\n", "docs/guide/g.md": "g\n"}, "base")
        self.env = {"GITHUB_EVENT_NAME": "pull_request"}

    def merge(self, changes: dict[str, str | None], *, main_after: dict[str, str | None] | None = None,
              branch_message: str = "pr") -> None:
        """Leave ``HEAD`` on a merge commit, as a pull request's merge ref is."""
        _git(self.repo, "checkout", "-q", "-b", "pr")
        _commit(self.repo, changes, branch_message)
        _git(self.repo, "checkout", "-q", "main")
        if main_after is not None:
            _commit(self.repo, main_after, "main moves")
        _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")

    def decide(self, env: dict | None = None) -> str:
        return cc.detect(self.env if env is None else env, cwd=self.repo)[0]

    def test_a_documentation_only_merge_is_false(self):
        self.merge({"docs/run.md": "new\n", "docs/items/c.md": "c\n"})
        self.assertEqual(self.decide(), "false")

    def test_a_mixed_change_is_true(self):
        self.merge({"docs/run.md": "new\n", "tools/x.py": "x = 2\n"})
        self.assertEqual(self.decide(), "true")

    def test_a_guide_change_is_true(self):
        self.merge({"docs/guide/g.md": "new\n"})
        self.assertEqual(self.decide(), "true")

    def test_deleting_a_documentation_file_is_false_and_a_code_file_true(self):
        self.merge({"docs/items/a.md": None})
        self.assertEqual(self.decide(), "false")
        _git(self.repo, "reset", "-q", "--hard", "HEAD^1")
        _git(self.repo, "branch", "-q", "-D", "pr")
        self.merge({"tools/x.py": None})
        self.assertEqual(self.decide(), "true")

    def test_a_rename_counts_under_both_names(self):
        # From a documentation path into code: the old name is documentation,
        # the new one is not.
        _git(self.repo, "checkout", "-q", "-b", "pr")
        _git(self.repo, "mv", "docs/run.md", "tools/run.py")
        _git(self.repo, "commit", "-q", "-m", "rename")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")
        self.assertEqual(cc.changed_paths(self.repo).__len__(), 2)
        self.assertEqual(sorted(cc.changed_paths(self.repo)), ["docs/run.md", "tools/run.py"])
        self.assertEqual(self.decide(), "true")

    def test_a_rename_inside_the_set_is_false(self):
        _git(self.repo, "checkout", "-q", "-b", "pr")
        _git(self.repo, "mv", "docs/items/a.md", "docs/items/z.md")
        _git(self.repo, "commit", "-q", "-m", "rename")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")
        self.assertEqual(self.decide(), "false")

    def test_an_empty_diff_is_true(self):
        self.merge({})
        self.assertEqual(cc.changed_paths(self.repo), [])
        self.assertEqual(self.decide(), "true")

    def test_a_head_that_is_not_a_merge_commit_is_true(self):
        _commit(self.repo, {"docs/run.md": "only docs\n"})
        with self.assertRaises(cc.DetectError):
            cc.changed_paths(self.repo)
        self.assertEqual(self.decide(), "true")

    def test_a_stale_recorded_base_does_not_change_the_result(self):
        # main moves on (code) after the pull request branched; the merge
        # ref's first parent is the new tip, so the diff is only the pull
        # request's documentation. The branch point would show the code.
        branch_point = _git(self.repo, "rev-parse", "HEAD").strip()
        self.merge({"docs/run.md": "new\n"}, main_after={"tools/x.py": "x = 3\n"})
        self.assertEqual(self.decide(), "false")
        stale = _git(self.repo, "diff", "--name-only", branch_point, "HEAD").split()
        self.assertIn("tools/x.py", stale)

    def test_an_event_that_is_not_a_pull_request_is_true_without_reading_git(self):
        def never(args, cwd):
            raise AssertionError("git must not be called")

        for env in ({"GITHUB_EVENT_NAME": "push"}, {"GITHUB_EVENT_NAME": "workflow_dispatch"},
                    {"GITHUB_EVENT_NAME": "pull_request_target"}, {"GITHUB_EVENT_NAME": ""}, {}):
            with self.subTest(env=env):
                self.assertEqual(cc.detect(env, cwd=self.repo, git=never)[0], "true")

    def test_a_failing_git_is_true(self):
        def broken(args, cwd):
            raise cc.DetectError("boom")

        self.assertEqual(cc.detect(self.env, cwd=self.repo, git=broken)[0], "true")
        self.assertEqual(cc.detect(self.env, cwd=self.repo, git=lambda a, c: 1 / 0)[0], "true")
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(cc.detect(self.env, cwd=Path(plain))[0], "true")

    def test_the_command_line_writes_the_output_file(self):
        self.merge({"docs/run.md": "new\n"})
        for event, expected in (("pull_request", "false"), ("push", "true")):
            with self.subTest(event=event), tempfile.TemporaryDirectory() as out:
                target = Path(out) / "github_output"
                # Stand in for a runner whose GITHUB_EVENT_PATH names a Draft pull request's
                # payload: the command under test must not inherit it (CI2d PR check, tests (1)).
                payload = Path(out) / "event.json"
                payload.write_text(json.dumps({"pull_request": {"draft": True}}))
                with unittest.mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(payload)}):
                    env = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}
                env.update(GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(target))
                done = fixtures.run([sys.executable, str(fixtures.REPO_ROOT / "tools" / "ci_changes.py"), "detect"],
                                    cwd=self.repo, env=env)
                self.assertEqual(target.read_text(), f"code={expected}\n")
                self.assertIn(f"code={expected}", done.stdout)


# ---------------------------------------------------------------------------
# detect: a push and a Draft pull request (CI2d, D1, D2)
# ---------------------------------------------------------------------------

def _state(**phases: str) -> str:
    return json.dumps({"work_items": {key: {"work_item_id": key, "phase": phase}
                                      for key, phase in phases.items()}})


class PushDetectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = fixtures.git_init(self.tmp / "repo", "-b", "main")
        _commit(self.repo, {"docs/run.md": "run\n", "tools/x.py": "x = 1\n"}, "base")
        self.before = _git(self.repo, "rev-parse", "HEAD").strip()

    def head(self) -> str:
        return _git(self.repo, "rev-parse", "HEAD").strip()

    def decide(self, payload="default", *, git=None, event="push") -> tuple[str, str]:
        if payload == "default":
            payload = {"before": self.before, "after": self.head()}
        env = {"GITHUB_EVENT_NAME": event}
        if payload is not None:
            path = self.tmp / "event.json"
            path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
            env["GITHUB_EVENT_PATH"] = str(path)
        return cc.detect(env, cwd=self.repo, **({"git": git} if git else {}))

    def test_a_push_of_documentation_only_is_false(self):
        _commit(self.repo, {"docs/run.md": "new\n", "docs/items/c.md": "c\n"})
        self.assertEqual(self.decide()[0], "false")

    def test_a_push_range_of_two_documentation_commits_is_false(self):
        _commit(self.repo, {"docs/run.md": "one\n"})
        _commit(self.repo, {"docs/items/c.md": "c\n"})
        self.assertEqual(self.decide()[0], "false")

    def test_a_push_range_with_one_code_commit_is_true(self):
        _commit(self.repo, {"docs/run.md": "one\n"})
        _commit(self.repo, {"tools/x.py": "x = 2\n"})
        self.assertEqual(self.decide()[0], "true")

    def test_a_push_with_one_code_path_is_true(self):
        _commit(self.repo, {"docs/run.md": "new\n", "tools/x.py": "x = 2\n"})
        self.assertEqual(self.decide()[0], "true")

    def test_an_all_zero_before_is_true(self):
        _commit(self.repo, {"docs/run.md": "new\n"})
        self.assertEqual(self.decide({"before": "0" * 40, "after": self.head()})[0], "true")

    def test_an_after_that_is_not_head_is_true(self):
        _commit(self.repo, {"docs/run.md": "new\n"})
        self.assertEqual(self.decide({"before": self.before, "after": self.before})[0], "true")

    def test_an_unresolvable_before_is_true(self):
        _commit(self.repo, {"docs/run.md": "new\n"})
        self.assertEqual(self.decide({"before": "1" * 40, "after": self.head()})[0], "true")

    def test_an_empty_diff_is_true(self):
        _commit(self.repo, {})
        self.assertEqual(self.decide()[0], "true")

    def test_an_unreadable_or_odd_payload_is_true(self):
        _commit(self.repo, {"docs/run.md": "new\n"})
        for payload in (None, "not json", "[]", {}, {"before": 1, "after": 2}, {"before": "", "after": ""}):
            with self.subTest(payload=payload):
                self.assertEqual(self.decide(payload)[0], "true")

    def test_a_failing_git_is_true(self):
        _commit(self.repo, {"docs/run.md": "new\n"})

        def broken(args, cwd):
            raise cc.DetectError("boom")

        self.assertEqual(self.decide(git=broken)[0], "true")
        self.assertEqual(self.decide(git=lambda a, c: 1 / 0)[0], "true")

    def test_the_reason_names_the_rule(self):
        _commit(self.repo, {"docs/run.md": "new\n"})
        self.assertIn("push", self.decide()[1])

    def test_a_manual_dispatch_is_true(self):
        _commit(self.repo, {"docs/run.md": "new\n"})
        self.assertEqual(self.decide(event="workflow_dispatch")[0], "true")


class DraftDetectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = fixtures.git_init(self.tmp / "repo", "-b", "main")
        self.state = "docs/ai-workflow/WORKFLOW_STATE.json"

    def merge(self, head_state: str | None, base_state: str | None = "default", *, extra=None) -> None:
        base = _state(old="MILESTONE_COMPLETE") if base_state == "default" else base_state
        _commit(self.repo, {"tools/x.py": "x = 1\n", self.state: base}, "base")
        _git(self.repo, "checkout", "-q", "-b", "pr")
        _commit(self.repo, {self.state: head_state, **(extra or {})}, "pr")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")

    def decide(self, draft="default", *, event="pull_request") -> tuple[str, str]:
        payload = {"pull_request": {"draft": True}} if draft == "default" else draft
        path = self.tmp / "event.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return cc.detect({"GITHUB_EVENT_NAME": event, "GITHUB_EVENT_PATH": str(path)}, cwd=self.repo)

    def test_the_partition_covers_every_known_phase_exactly_once(self):
        sys.path.insert(0, str(fixtures.REPO_ROOT / "scripts"))
        try:
            import workflow_state
        finally:
            sys.path.pop(0)
        bound, excluded = set(cc.REVIEW_BOUND_PHASES), set(cc.EXCLUDED_PHASES)
        self.assertFalse(bound & excluded)
        self.assertEqual(bound | excluded, set(workflow_state.KNOWN_PHASES))

    def test_a_draft_with_no_review_bound_item_is_false_even_for_code(self):
        self.merge(_state(old="MILESTONE_COMPLETE", new="IMPLEMENTING"), extra={"tools/x.py": "x = 2\n"})
        self.assertEqual(self.decide()[0], "false")

    def test_every_review_bound_phase_new_at_the_head_is_true(self):
        for phase in sorted(cc.REVIEW_BOUND_PHASES):
            with self.subTest(phase=phase):
                self.setUp()
                self.merge(_state(old="MILESTONE_COMPLETE", new=phase))
                self.assertEqual(self.decide()[0], "true")

    def test_the_usual_anchor_phases_run_the_shards_and_the_head_at_a_and_later_do(self):
        for phase in ("APPLYING_REVIEW_FEEDBACK", "SELF_REVIEWING_IMPLEMENTATION", "MILESTONE_COMPLETE"):
            with self.subTest(phase=phase):
                self.setUp()
                self.merge(_state(old="MILESTONE_COMPLETE", new=phase))
                self.assertEqual(self.decide()[0], "true")
                # a later head: the base (main) still lacks the item
                _git(self.repo, "checkout", "-q", "pr")
                _commit(self.repo, {"tools/y.py": "y\n"})
                _git(self.repo, "checkout", "-q", "main")
                _git(self.repo, "reset", "-q", "--hard", "HEAD^1")
                _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")
                self.assertEqual(self.decide()[0], "true")

    def test_an_item_in_a_plan_stage_phase_or_implementing_is_false(self):
        for phase in sorted(cc.EXCLUDED_PHASES):
            with self.subTest(phase=phase):
                self.setUp()
                self.merge(_state(old="MILESTONE_COMPLETE", new=phase))
                self.assertEqual(self.decide()[0], "false")

    def test_an_item_already_review_bound_at_the_base_is_false(self):
        self.merge(_state(old="MILESTONE_COMPLETE", new="AWAITING_FUNCTIONAL_REVIEW"),
                   _state(old="MILESTONE_COMPLETE", new="AWAITING_FUNCTIONAL_REVIEW"))
        self.assertEqual(self.decide()[0], "false")

    def test_an_item_the_base_holds_at_an_excluded_phase_is_true(self):
        self.merge(_state(new="APPLYING_REVIEW_FEEDBACK"), _state(new="IMPLEMENTING"))
        self.assertEqual(self.decide()[0], "true")

    def test_an_unreadable_state_file_is_true(self):
        for head, base in (("not json", "default"), ("{}", "default"), (_state(new="PLANNING"), "not json"),
                           ('{"work_items": 3}', "default")):
            with self.subTest(head=head, base=base):
                self.setUp()
                self.merge(head, base)
                self.assertEqual(self.decide()[0], "true")

    def test_a_missing_state_file_at_the_head_is_true(self):
        self.merge(None)
        self.assertEqual(self.decide()[0], "true")

    def test_a_missing_draft_key_and_a_non_draft_run_everything(self):
        self.merge(_state(old="MILESTONE_COMPLETE", new="PLANNING"), extra={"tools/x.py": "x = 2\n"})
        for payload in ({}, {"pull_request": {}}, {"pull_request": {"draft": False}}, "not json",
                        {"pull_request": {"draft": "true"}}, {"pull_request": "draft"},
                        {"pull_request": ["draft"]}, {"pull_request": None}):
            with self.subTest(payload=payload):
                self.assertEqual(self.decide(payload)[0], "true")

    def test_a_non_draft_documentation_pull_request_is_still_false(self):
        self.merge(_state(old="MILESTONE_COMPLETE"), extra={"docs/items/a.md": "a\n"})
        self.assertEqual(self.decide({"pull_request": {"draft": False}})[0], "false")


# ---------------------------------------------------------------------------
# require
# ---------------------------------------------------------------------------

class RequireTests(unittest.TestCase):
    def test_only_two_successes_and_a_boolean_pass(self):
        results = ("success", "failure", "cancelled", "skipped", "")
        outputs = ("true", "false", "", "maybe", "True")
        for changes, docs, code in itertools.product(results, results, outputs):
            expected = changes == "success" and docs == "success" and code in ("true", "false")
            with self.subTest(changes=changes, docs=docs, code=code):
                self.assertEqual(cc.require(changes, docs, code)[0], expected)

    def test_the_exit_status_of_the_command_line(self):
        def run(*argv):
            err, out = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                return cc.main(["require", *argv])

        self.assertEqual(run("--changes", "success", "--docs", "success", "--code", "false"), 0)
        self.assertEqual(run("--changes", "success", "--docs", "success", "--code", "true"), 0)
        self.assertEqual(run("--changes", "success", "--docs", "failure", "--code", "true"), 1)
        self.assertEqual(run("--changes", "failure", "--docs", "success", "--code", ""), 1)
        self.assertEqual(run("--changes", "success", "--docs", "success"), 1)
        self.assertEqual(run(), 1)


# ---------------------------------------------------------------------------
# hide, on real temporary repositories (CI2e)
# ---------------------------------------------------------------------------

class HideTests(unittest.TestCase):
    FILES = {"docs/run.md": "run\n", "docs/items/a.md": "a\n", "docs/items/sub/b.md": "b\n",
             "docs/releases/1.0.0.md": "r\n", "docs/backlog.yaml": "items: []\n",
             "docs/guide/g.md": "g\n", "docs/ACTIVE_MILESTONE.md": "m\n",
             "tools/x.py": "x = 1\n", "README.md": "readme\n"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = fixtures.git_init(Path(self._tmp.name) / "repo", "-b", "main")
        _commit(self.repo, self.FILES, "base")

    def hide(self, *argv, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cc.main(["hide", *argv]) if cwd is None else self._in(cwd, argv)
        return code, out.getvalue(), err.getvalue()

    def _in(self, cwd, argv):
        previous = os.getcwd()
        os.chdir(cwd)
        try:
            return cc.main(["hide", *argv])
        finally:
            os.chdir(previous)

    def present(self) -> set[str]:
        return {p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(self.repo).parts}

    def test_it_removes_exactly_the_set_and_keeps_git_status_clean(self):
        code, out, _ = self.hide(cwd=self.repo)
        self.assertEqual(code, 0)
        self.assertIn("hid 5 documentation paths", out)
        expected = {n for n in self.FILES if not cc.is_documentation(n)}
        self.assertEqual(self.present(), expected)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        self.assertFalse((self.repo / "docs" / "items").exists())
        self.assertTrue((self.repo / "docs" / "guide").is_dir())

    def test_a_dry_run_lists_and_touches_nothing(self):
        before = self.present()
        code, out, _ = self.hide("--dry-run", cwd=self.repo)
        self.assertEqual(code, 0)
        self.assertIn("docs/items/sub/b.md", out)
        self.assertNotIn("docs/guide/g.md", out)
        self.assertIn("would hide 5 documentation paths", out)
        self.assertEqual(self.present(), before)

    def test_a_path_already_deleted_is_hidden_and_a_restore_brings_it_back(self):
        (self.repo / "docs" / "run.md").unlink()
        code, out, _ = self.hide(cwd=self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        _git(self.repo, "update-index", "--no-skip-worktree", "docs/run.md", "docs/items/a.md")
        _git(self.repo, "checkout", "--", "docs/run.md", "docs/items/a.md")
        self.assertEqual((self.repo / "docs" / "run.md").read_text(), "run\n")
        self.assertEqual((self.repo / "docs" / "items" / "a.md").read_text(), "a\n")

    def test_it_works_from_a_subdirectory_and_twice(self):
        self.assertEqual(self.hide(cwd=self.repo / "tools")[0], 0)
        self.assertEqual(self.hide(cwd=self.repo)[0], 0)
        self.assertEqual(self.present(), {n for n in self.FILES if not cc.is_documentation(n)})

    def test_it_never_deletes_an_untracked_documentation_file(self):
        (self.repo / "docs" / "items").mkdir(exist_ok=True)
        (self.repo / "docs" / "items" / "untracked.md").write_text("u\n")
        self.assertEqual(self.hide(cwd=self.repo)[0], 0)
        self.assertTrue((self.repo / "docs" / "items" / "untracked.md").is_file())

    def test_it_refuses_outside_a_work_tree(self):
        with tempfile.TemporaryDirectory() as bare:
            env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(bare).parent)}
            with unittest.mock.patch.dict(os.environ, env):
                code, out, err = self.hide(cwd=bare)
        self.assertEqual(code, 1)
        self.assertIn("hide:", err)
        self.assertEqual(out, "")

    def test_hidden_paths_uses_the_injected_git(self):
        calls = []

        def fake(args, cwd):
            calls.append(args)
            return "true\n" if args[0] == "rev-parse" else "docs/run.md\0tools/x.py\0docs/items/a.md\0"

        self.assertEqual(cc.hidden_paths(None, fake), ["docs/run.md", "docs/items/a.md"])
        self.assertEqual([c[0] for c in calls], ["rev-parse", "ls-files"])
        with self.assertRaises(cc.DetectError):
            cc.hidden_paths(None, lambda args, cwd: "false\n")

    def test_a_test_that_reads_a_set_path_fails_after_hide(self):
        """The proof the check can fail: the reader lives only in the temporary
        repository, never under ``tests/`` of this one."""
        module = self.repo / "tests" / "test_reads_docs.py"
        module.parent.mkdir(exist_ok=True)
        module.write_text("import pathlib, unittest\n\n\nclass T(unittest.TestCase):\n"
                          "    def test_reads(self):\n"
                          "        self.assertEqual(pathlib.Path('docs/run.md').read_text(), 'run\\n')\n")
        run = lambda: subprocess.run([sys.executable, "-m", "unittest", "tests.test_reads_docs"],
                                     cwd=self.repo, capture_output=True, text=True, timeout=120)
        self.assertEqual(run().returncode, 0)
        self.assertEqual(self.hide(cwd=self.repo)[0], 0)
        failed = run()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("FileNotFoundError", failed.stderr)
