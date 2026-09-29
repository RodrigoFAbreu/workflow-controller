"""``controller.forge``: the GitHub boundary (``workflow-controller-trunk-
branch-pr-release-orchestration`` CP3), driven against ``tests/fake_gh.py``
over a disposable bare origin."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import forge, gitrepo  # noqa: E402
from controller.errors import ForgeError, ForgeUndecidableError  # noqa: E402
from tests import fake_gh  # noqa: E402
from tests.fixtures import FAKE_GH_REPOSITORY, build_origin_pair, commit_all, fake_gh_env, run  # noqa: E402

#: Every fake ``gh`` invocation log this module produced, for the
#: module-level "nothing ever ran ``gh pr merge``" check.
_LOGS: list[list[str]] = []


def tearDownModule() -> None:  # noqa: N802 -- unittest's hook name
    merges = [argv for argv in _LOGS if argv[:2] in (["pr", "merge"], ["pr", "close"])]
    assert not merges, f"the forge invoked a forbidden gh operation: {merges}"


class _Case(unittest.TestCase):
    failures: dict[str, str] | None = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.origin, self.clone = build_origin_pair(self.tmp)
        self.env = fake_gh_env(self.tmp, origin=self.origin, failures=self.failures)
        self.calls: list[list[str]] = []
        real = gitrepo.subprocess_runner(self.env)

        def spy(argv):
            self.calls.append(list(argv))
            return real(argv)

        self.forge = forge.GhForge(FAKE_GH_REPOSITORY, spy)

    def tearDown(self) -> None:
        _LOGS.extend(fake_gh.invocations(Path(self.env["FAKE_GH_LOG"])))
        self._tmp.cleanup()

    @property
    def state_path(self) -> Path:
        return Path(self.env["FAKE_GH_STATE"])

    def edit_state(self, edit) -> None:
        state = fake_gh.read_state(self.state_path)
        edit(state)
        fake_gh.write_state(self.state_path, state)

    def set_failures(self, failures: dict[str, str]) -> None:
        self.env["FAKE_GH_FAIL"] = json.dumps(failures)
        real = gitrepo.subprocess_runner(self.env)
        self.forge = forge.GhForge(FAKE_GH_REPOSITORY, real)

    def push_branch_with_commit(self, branch: str = "milestone/wi-1") -> str:
        run(["git", "switch", "-q", "-c", branch], cwd=self.clone)
        (self.clone / f"{branch.replace('/', '-')}.txt").write_text("work\n")
        tip = commit_all(self.clone, "work")
        run(["git", "push", "-q", "origin", f"refs/heads/{branch}:refs/heads/{branch}"], cwd=self.clone)
        return tip

    def seed_pr(self, number: int, **fields) -> None:
        record = {"number": number, "state": "OPEN", "isDraft": True, "headRefName": "milestone/wi-1",
                  "headRefOid": "a" * 40, "baseRefName": "main", "isCrossRepository": False,
                  "url": f"https://github.com/{FAKE_GH_REPOSITORY}/pull/{number}", "mergedAt": None,
                  "mergeCommit": None, "title": "t", "body": "b", "checks": None}
        record.update(fields)

        def edit(state):
            state["prs"].append(record)
            state["next_number"] = max(state["next_number"], number + 1)

        self.edit_state(edit)


class RepositoryTest(_Case):
    def test_identity_and_verification(self) -> None:
        identity = self.forge.verify_repository()
        self.assertEqual(identity.name_with_owner, FAKE_GH_REPOSITORY)
        other = forge.GhForge("someone/else", gitrepo.subprocess_runner(self.env))
        with self.assertRaises(ForgeUndecidableError):
            other.repository_identity()

    def test_verification_refuses_another_repository(self) -> None:
        self.edit_state(lambda s: s.update(repository="fork-owner/example-repo"))
        fork = forge.GhForge("fork-owner/example-repo", gitrepo.subprocess_runner(self.env))
        fork.verify_repository()

        def renamed(argv):
            return subprocess.CompletedProcess(argv, 0, json.dumps(
                {"nameWithOwner": "upstream/example-repo", "url": "u"}).encode(), b"")

        with self.assertRaises(ForgeError) as caught:
            forge.GhForge(FAKE_GH_REPOSITORY, renamed).verify_repository()
        self.assertNotIsInstance(caught.exception, ForgeUndecidableError)

    def test_the_repository_is_named_on_every_call(self) -> None:
        self.push_branch_with_commit()
        self.forge.repository_identity()
        pr = self.forge.create_draft_pr("milestone/wi-1", "main", "title", "body")
        self.forge.list_prs("milestone/wi-1")
        self.forge.mark_ready(pr.number)
        self.forge.pr_checks(pr.number)
        self.forge.view_release("v1.0.0")
        self.assertGreaterEqual(len(self.calls), 7)
        for argv in self.calls:
            if argv[1:3] == ["repo", "view"]:
                self.assertEqual(argv[3], FAKE_GH_REPOSITORY)  # `gh repo view` has no --repo flag
                self.assertNotIn("--repo", argv)
            else:
                index = argv.index("--repo")
                self.assertEqual(argv[index + 1], FAKE_GH_REPOSITORY, argv)


class PullRequestTest(_Case):
    def test_create_requires_a_commit_beyond_the_base(self) -> None:
        run(["git", "push", "-q", "origin", "refs/heads/main:refs/heads/milestone/wi-1"], cwd=self.clone)
        with self.assertRaises(ForgeUndecidableError) as caught:
            self.forge.create_draft_pr("milestone/wi-1", "main", "t", "b")
        self.assertIn("No commits between", caught.exception.evidence["stderr"])
        self.assertEqual(self.forge.list_prs("milestone/wi-1"), [])

    def test_create_view_list_and_ready(self) -> None:
        tip = self.push_branch_with_commit()
        pr = self.forge.create_draft_pr("milestone/wi-1", "main", "Milestone wi-1", "body")
        self.assertEqual((pr.state, pr.is_draft, pr.head_ref, pr.head_oid, pr.base_ref),
                         ("OPEN", True, "milestone/wi-1", tip, "main"))
        self.assertFalse(pr.is_cross_repository)
        self.assertIsNone(pr.merge_commit)
        self.assertEqual(self.forge.list_prs("milestone/wi-1"), [pr])
        self.assertEqual(self.forge.list_prs("milestone/other"), [])
        self.forge.mark_ready(pr.number)
        self.assertFalse(self.forge.view_pr(pr.number).is_draft)

    def test_a_second_open_pr_for_the_same_head_and_base_is_refused(self) -> None:
        self.push_branch_with_commit()
        self.forge.create_draft_pr("milestone/wi-1", "main", "t", "b")
        with self.assertRaises(ForgeUndecidableError) as caught:
            self.forge.create_draft_pr("milestone/wi-1", "main", "t", "b")
        self.assertIn("already exists", caught.exception.evidence["stderr"])

    def test_list_filters_by_head_name_over_closed_and_merged_prs(self) -> None:
        self.seed_pr(3, state="CLOSED")
        self.seed_pr(4, state="MERGED", mergedAt="2026-09-01T00:00:00Z", mergeCommit={"oid": "b" * 40})
        self.seed_pr(5, headRefName="milestone/other")
        prs = self.forge.list_prs("milestone/wi-1")
        self.assertEqual([(p.number, p.state) for p in prs], [(4, "MERGED"), (3, "CLOSED")])
        self.assertEqual(prs[0].merge_commit, "b" * 40)
        self.assertEqual(prs[0].merged_at, "2026-09-01T00:00:00Z")

    def test_a_malformed_pull_request_record_is_undecidable(self) -> None:
        for bad in ({"state": "WEIRD"}, {"headRefOid": "not-an-oid"}, {"isDraft": "yes"},
                    {"isCrossRepository": None}, {"mergeCommit": {"oid": 5}}):
            with self.subTest(bad=bad):
                self._tmp.cleanup()
                self.setUp()
                self.seed_pr(7, **bad)
                with self.assertRaises(ForgeUndecidableError):
                    self.forge.list_prs("milestone/wi-1")


class ChecksTest(_Case):
    def _checks(self, checks) -> forge.Checks:
        self.seed_pr(1, checks=checks)
        return self.forge.pr_checks(1)

    def test_exit_codes_classify_with_the_json(self) -> None:
        cases = {
            "passing (exit 0)": [{"name": "a", "state": "SUCCESS", "bucket": "pass"}],
            "pending (exit 8)": [{"name": "a", "state": "IN_PROGRESS", "bucket": "pending"}],
            "failing (exit 1)": [{"name": "a", "state": "FAILURE", "bucket": "fail"},
                                 {"name": "b", "state": "IN_PROGRESS", "bucket": "pending"}],
        }
        for label, checks in cases.items():
            with self.subTest(label):
                self._tmp.cleanup()
                self.setUp()
                result = self._checks(checks)
                self.assertEqual(result.outcome, "checks")
                self.assertEqual([c.bucket for c in result.checks], [c["bucket"] for c in checks])

    def test_no_checks_reported_is_its_own_outcome(self) -> None:
        self.assertEqual(self._checks(None), forge.Checks("no_checks"))

    def test_every_documented_bucket_is_accepted(self) -> None:
        checks = [{"name": b, "state": "X", "bucket": b} for b in sorted(forge.CHECK_BUCKETS)]
        self.assertEqual({c.bucket for c in self._checks(checks).checks},
                         {"pass", "fail", "pending", "skipping", "cancel"})

    def test_an_out_of_set_or_missing_bucket_is_undecidable(self) -> None:
        for check in ({"name": "a", "state": "X", "bucket": "neutral"}, {"name": "a", "state": "X"}):
            with self.subTest(check=check):
                self._tmp.cleanup()
                self.setUp()
                with self.assertRaises(ForgeUndecidableError):
                    self._checks([check])

    def test_exit_8_or_1_without_json_is_undecidable(self) -> None:
        self.seed_pr(1, checks=[])
        for kind in ("malformed:8", "malformed:1", "malformed"):
            with self.subTest(kind=kind):
                self.set_failures({"pr checks": kind})
                with self.assertRaises(ForgeUndecidableError):
                    self.forge.pr_checks(1)


class ReleaseTest(_Case):
    def _tag(self, tag: str) -> None:
        run(["git", "tag", "-a", "-m", tag, tag], cwd=self.clone)
        run(["git", "push", "-q", "origin", f"refs/tags/{tag}"], cwd=self.clone)

    def _asset(self, name: str, text: str) -> Path:
        path = self.tmp / "dist" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return path

    def test_view_absent_release_is_none(self) -> None:
        self.assertIsNone(self.forge.view_release("v9.9.9"))

    def test_create_requires_the_remote_tag(self) -> None:
        with self.assertRaises(ForgeUndecidableError):
            self.forge.create_release("v1.0.0", [], "1.0.0", "notes")
        self.assertIn("--verify-tag", self.calls[-1])
        self.assertIsNone(self.forge.view_release("v1.0.0"))

    def test_create_publishes_with_assets_and_download_never_overwrites(self) -> None:
        self._tag("v1.0.0")
        wheel = self._asset("pkg.whl", "wheel bytes")
        sums = self._asset("SHA256SUMS", "sums")
        self.forge.create_release("v1.0.0", [wheel, sums], "1.0.0", "notes")
        release = self.forge.view_release("v1.0.0")
        self.assertFalse(release.is_draft)
        self.assertEqual({(a.name, a.size) for a in release.assets}, {("pkg.whl", 11), ("SHA256SUMS", 4)})
        self.assertNotIn("--draft", self.calls[-2])
        out = self.tmp / "download"
        self.forge.download_assets("v1.0.0", out)
        self.assertEqual((out / "pkg.whl").read_text(), "wheel bytes")
        with self.assertRaises(ForgeUndecidableError):
            self.forge.download_assets("v1.0.0", out)

    def test_upload_is_draft_only_and_publish_draft(self) -> None:
        self._tag("v1.0.0")
        self.forge.create_release("v1.0.0", [], "1.0.0", "notes")
        with self.assertRaises(ForgeError) as caught:
            self.forge.upload_assets("v1.0.0", [self._asset("late.whl", "x")])
        self.assertNotIsInstance(caught.exception, ForgeUndecidableError)
        with self.assertRaises(ForgeError):
            self.forge.upload_assets("v2.0.0", [self._asset("late.whl", "x")])

        self._tag("v1.1.0")
        self.edit_state(lambda s: s["releases"].append(
            {"tagName": "v1.1.0", "isDraft": True, "url": "u", "assets": [], "title": "t", "notes": "n"}))
        asset = self._asset("pkg.whl", "draft wheel")
        self.forge.upload_assets("v1.1.0", [asset])
        with self.assertRaises(ForgeUndecidableError):  # never replaces an asset
            self.forge.upload_assets("v1.1.0", [asset])
        self.forge.publish_draft("v1.1.0")
        release = self.forge.view_release("v1.1.0")
        self.assertFalse(release.is_draft)
        self.assertEqual([a.name for a in release.assets], ["pkg.whl"])


class UndecidableTest(_Case):
    READS = {
        "repo view": lambda f: f.repository_identity(),
        "pr list": lambda f: f.list_prs("milestone/wi-1"),
        "pr view": lambda f: f.view_pr(1),
        "pr checks": lambda f: f.pr_checks(1),
        "release view": lambda f: f.view_release("v1.0.0"),
        "pr ready": lambda f: f.mark_ready(1),
        "release edit": lambda f: f.publish_draft("v1.0.0"),
    }

    def test_every_gh_failure_class_is_undecidable(self) -> None:
        self.seed_pr(1, checks=[])
        for kind in ("auth", "network", "server_error", "malformed"):
            for command, call in self.READS.items():
                if kind == "malformed" and command in ("pr ready", "release edit"):
                    continue  # no output is parsed; exit 0 is success
                with self.subTest(kind=kind, command=command):
                    self.set_failures({command: kind})
                    with self.assertRaises(ForgeUndecidableError) as caught:
                        call(self.forge)
                    self.assertIn("argv", caught.exception.evidence)

    def test_a_missing_gh_or_a_timeout_is_undecidable(self) -> None:
        def missing(argv):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        def hangs(argv):
            raise subprocess.TimeoutExpired(argv, 1)

        for runner in (missing, hangs):
            with self.assertRaises(ForgeUndecidableError):
                forge.GhForge(FAKE_GH_REPOSITORY, runner).list_prs("milestone/wi-1")

    def test_a_full_page_of_prs_is_undecidable(self) -> None:
        records = [{"number": n, "state": "CLOSED", "isDraft": False, "headRefName": "b",
                    "headRefOid": "a" * 40, "baseRefName": "main", "isCrossRepository": False,
                    "url": "u", "mergedAt": None, "mergeCommit": None}
                   for n in range(forge.PR_LIST_LIMIT)]

        def full(argv):
            return subprocess.CompletedProcess(argv, 0, json.dumps(records).encode(), b"")

        with self.assertRaises(ForgeUndecidableError):
            forge.GhForge(FAKE_GH_REPOSITORY, full).list_prs("b")


class EditTest(_Case):
    def test_view_reads_the_title_and_body(self) -> None:
        self.push_branch_with_commit()
        pr = self.forge.create_draft_pr("milestone/wi-1", "main", "feat: a title", "the body\n")
        self.assertEqual((pr.title, pr.body), ("feat: a title", "the body\n"))
        self.assertIn("title,body", forge.PR_FIELDS)

    def test_edit_runs_the_exact_argv_and_re_reads(self) -> None:
        self.push_branch_with_commit()
        pr = self.forge.create_draft_pr("milestone/wi-1", "main", "t", "b")
        self.calls.clear()
        edited = self.forge.edit_pr(pr.number, title="fix: new", body="new body\n")
        self.assertEqual(self.calls[0], ["gh", "pr", "edit", str(pr.number), "--title", "fix: new", "--body",
                                         "new body\n", "--repo", FAKE_GH_REPOSITORY])
        self.assertEqual(self.calls[1][:3], ["gh", "pr", "view"])
        self.assertEqual((edited.title, edited.body), ("fix: new", "new body\n"))
        self.calls.clear()
        self.assertEqual(self.forge.edit_pr(pr.number, title="docs: only the title").body, "new body\n")
        self.assertEqual(self.calls[0], ["gh", "pr", "edit", str(pr.number), "--title", "docs: only the title",
                                         "--repo", FAKE_GH_REPOSITORY])
        with self.assertRaises(ValueError):
            self.forge.edit_pr(pr.number)

    def test_edit_refuses_when_the_re_read_disagrees(self) -> None:
        self.push_branch_with_commit()
        pr = self.forge.create_draft_pr("milestone/wi-1", "main", "t", "b")
        real = gitrepo.subprocess_runner(self.env)

        def ignores_edits(argv):
            if argv[1:3] == ["pr", "edit"]:
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            return real(argv)

        stale = forge.GhForge(FAKE_GH_REPOSITORY, ignores_edits)
        for kwargs in ({"title": "fix: x"}, {"body": "other"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ForgeError) as caught:
                    stale.edit_pr(pr.number, **kwargs)
                self.assertNotIsInstance(caught.exception, ForgeUndecidableError)

    def test_a_body_differing_only_in_line_ends_is_the_same(self) -> None:
        self.assertTrue(forge.same_text("a\r\nb\r\n", "a\nb\n"))
        self.assertTrue(forge.same_text("a\nb", "a\nb\n"))
        self.assertFalse(forge.same_text("a\nb", "a\nc"))

    def test_a_closed_pr_cannot_be_edited(self) -> None:
        self.seed_pr(4, state="CLOSED")
        with self.assertRaises(ForgeUndecidableError):
            self.forge.edit_pr(4, title="fix: x")


class NoMergeOperationTest(unittest.TestCase):
    def test_the_forge_has_no_merge_close_delete_or_comment_operation(self) -> None:
        names = {name.lower() for name in dir(forge.GhForge) if not name.startswith("_")}
        for forbidden in ("merge", "close", "delete", "comment", "api"):
            self.assertFalse([n for n in names if forbidden in n], forbidden)
        # The one edit: a pull request's title and body (squash-merge-tag-versioning CP5).
        self.assertEqual([n for n in names if "edit" in n], ["edit_pr"])


if __name__ == "__main__":
    unittest.main()
