"""Tests for ``tools/timings_refresh.py`` (CI2d D6): which pull-request runs are taken, the
deduplication of shard records, the merge that leaves the file unchanged, the idempotent
branch and pull request, the missing token and the untimed share. ``gh`` and ``git`` are a
fake runner: nothing here touches the network or the user's configuration."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402
from tests.test_run_tests import SyntheticRepo  # noqa: E402

_spec = importlib.util.spec_from_file_location("timings_refresh",
                                               fixtures.REPO_ROOT / "tools" / "timings_refresh.py")
tr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tr
_spec.loader.exec_module(tr)

REPO = "owner/repo"
CI_PATH = ".github/workflows/ci.yml"


def run(run_id: int, **changes) -> dict:
    base = {"id": run_id, "run_number": run_id, "run_attempt": 1, "event": "pull_request",
            "conclusion": "success", "status": "completed", "path": CI_PATH,
            "head_repository": {"full_name": REPO}}
    base.update(changes)
    return base


def artifact(attempt: int, **changes) -> dict:
    base = {"name": f"timings-ci-attempt-{attempt}", "expired": False}
    base.update(changes)
    return base


class FakeGh:
    """Answers ``gh api`` from ``runs`` and ``artifacts`` (run id to artifact list), writes
    ``records`` (run id to ``{(digest, shard)}``) on ``gh run download``, and records
    every call. ``failing`` names a command prefix that fails."""

    def __init__(self, runs=(), artifacts=None, records=None, pulls=(), failing=()) -> None:
        self.runs, self.artifacts, self.records = list(runs), artifacts or {}, records or {}
        self.pulls, self.failing, self.calls, self.api_paths = list(pulls), tuple(failing), [], []

    def __call__(self, argv, env=None) -> str:
        argv = list(argv)
        self.calls.append((argv, dict(env or {})))
        if tuple(argv[:len(self.failing)]) == self.failing and self.failing:
            raise tr.RefreshError(f"{' '.join(argv)} failed")
        if argv[:2] == ["gh", "api"]:
            path = argv[2]
            self.api_paths.append(path)
            if path.endswith("/artifacts?per_page=100"):
                run_id = int(path.split("/runs/")[1].split("/")[0])
                return json.dumps({"artifacts": self.artifacts.get(run_id, [])})
            return json.dumps({"workflow_runs": self.runs})
        if argv[:3] == ["gh", "run", "download"]:
            directory = Path(argv[argv.index("--dir") + 1])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "plan.json").write_text("{}", encoding="utf-8")
            for digest, shard in sorted(self.records.get(int(argv[3]), ())):
                (directory / f"shard-{shard}.json").write_text(
                    json.dumps({"plan_digest": digest, "shard": shard}), encoding="utf-8")
            return ""
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps(self.pulls)
        return ""

    def commands(self, *prefix) -> list[list[str]]:
        return [argv for argv, _ in self.calls if argv[:len(prefix)] == list(prefix)]


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.out = Path(self.scratch.name) / "out"

    def collect(self, gh: FakeGh, runs: int = 3) -> list[Path]:
        return tr.collect(gh, REPO, self.out, runs)

    def test_only_successful_pull_request_runs_of_ci_yml_are_taken_and_main_runs_never(self) -> None:
        listed = [run(1), run(2, event="push"), run(3, conclusion="failure"), run(4, status="in_progress"),
                  run(5, path=".github/workflows/other.yml"),
                  run(6, head_repository={"full_name": "fork/repo"}), run(7)]
        self.assertEqual([r["id"] for r in tr.eligible_runs({"workflow_runs": listed}, REPO)], [7, 1])
        gh = FakeGh(runs=listed, artifacts={1: [artifact(1)], 7: [artifact(1)], 2: [artifact(1)]},
                    records={1: {("d1", 0)}, 7: {("d7", 0)}, 2: {("d2", 0)}})
        self.collect(gh)
        downloaded = sorted(argv[3] for argv in gh.commands("gh", "run", "download"))
        self.assertEqual(downloaded, ["1", "7"])
        self.assertTrue(all("event=pull_request" in path for path in gh.api_paths[:1]))

    def test_a_run_without_a_timings_artifact_is_skipped(self) -> None:
        gh = FakeGh(runs=[run(1), run(2)], artifacts={1: [{"name": "results-0-attempt-1"}],
                                                      2: [artifact(1)]},
                    records={2: {("d", 0)}})
        kept = self.collect(gh)
        self.assertEqual([p.name for p in kept], ["run-2"])
        self.assertEqual([argv[3] for argv in gh.commands("gh", "run", "download")], ["2"])

    def test_an_expired_artifact_is_not_taken_and_the_highest_attempt_is(self) -> None:
        self.assertIsNone(tr.newest_timings_artifact({"artifacts": [artifact(1, expired=True)]}))
        found = tr.newest_timings_artifact({"artifacts": [artifact(1), artifact(2), artifact(10)]})
        self.assertEqual(found["name"], "timings-ci-attempt-10")

    def test_a_duplicate_plan_digest_and_shard_record_is_dropped(self) -> None:
        gh = FakeGh(runs=[run(2), run(1)], artifacts={1: [artifact(1)], 2: [artifact(1)]},
                    records={2: {("same", 0), ("same", 1)}, 1: {("same", 1), ("other", 0)}})
        kept = self.collect(gh)
        self.assertEqual([p.name for p in kept], ["run-2", "run-1"])
        self.assertEqual(sorted(p.name for p in kept[0].glob("shard-*.json")), ["shard-0.json", "shard-1.json"])
        self.assertEqual([p.name for p in kept[1].glob("shard-*.json")], ["shard-0.json"])
        self.assertEqual((self.out / tr.DIRS_FILE).read_text().split(), [str(p) for p in reversed(kept)])

    def test_a_run_whose_records_are_all_duplicates_leaves_no_directory(self) -> None:
        gh = FakeGh(runs=[run(2), run(1)], artifacts={1: [artifact(1)], 2: [artifact(1)]},
                    records={2: {("same", 0)}, 1: {("same", 0)}})
        self.assertEqual([p.name for p in self.collect(gh)], ["run-2"])
        self.assertFalse((self.out / "run-1").exists())

    def test_at_most_runs_are_fetched(self) -> None:
        listed = [run(n) for n in range(1, 8)]
        gh = FakeGh(runs=listed, artifacts={n: [artifact(1)] for n in range(1, 8)},
                    records={n: {(f"d{n}", 0)} for n in range(1, 8)})
        self.assertEqual(len(self.collect(gh, runs=2)), 2)
        self.assertEqual(len(gh.commands("gh", "run", "download")), 2)
        self.assertEqual(sorted(argv[3] for argv in gh.commands("gh", "run", "download")), ["6", "7"])
        with self.assertRaises(tr.RefreshError):
            self.collect(gh, runs=0)

    def test_no_eligible_run_collects_nothing(self) -> None:
        self.assertEqual(self.collect(FakeGh(runs=[run(1, event="push")])), [])
        self.assertEqual((self.out / tr.DIRS_FILE).read_text(), "")


class ChronologicalMergeTest(unittest.TestCase):
    """The refresh hands the real merger its runs oldest first (CI2d review I1): ``collect`` keeps the
    newest record of a duplicate, ``dirs.txt`` lists the runs oldest first, so the EWMA that weighs a
    later observation more ends on the newest run."""

    KEY = "tests.test_pass.PassTest"

    def setUp(self) -> None:
        self.repo = SyntheticRepo(self)
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def observed(self, shards: str, seconds: float) -> Path:
        """A passing run whose ``PassTest`` atom took ``seconds``; each ``shards`` count is a plan of its own."""
        completed, results = self.repo.run_selection("tests.test_pass", extra=["--shards", shards])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        for record_path in results.glob("shard-*.json"):
            record = json.loads(record_path.read_text())
            if self.KEY in record["atoms"]:
                record["atoms"][self.KEY] = seconds
                record_path.write_text(json.dumps(record), encoding="utf-8")
        return results

    def merge(self, into: Path, directories, root: Path) -> None:
        """The real ``timings merge``, run against the synthetic repository the plans came from."""
        merged = self.repo.run("timings", "merge", "--into", str(into), *map(str, directories))
        self.assertEqual(merged.returncode, 0, merged.stderr)

    def test_the_refresh_produces_the_chronological_ewma_not_the_reverse(self) -> None:
        seed = self.root / "timings.json"
        merged = self.repo.run("timings", "merge", "--into", str(seed), str(self.observed("1", 4.0)))
        self.assertEqual(merged.returncode, 0, merged.stderr)
        runs = {1: self.observed("2", 4.0), 2: self.observed("3", 8.0), 3: self.observed("4", 16.0)}

        class Download(FakeGh):
            def __call__(inner, argv, env=None) -> str:
                if list(argv)[:3] == ["gh", "run", "download"]:
                    shutil.copytree(runs[int(argv[3])], argv[argv.index("--dir") + 1])
                    return ""
                return super().__call__(argv, env)

        gh = Download(runs=[run(n) for n in (1, 2, 3)], artifacts={n: [artifact(1)] for n in (1, 2, 3)})
        out = self.root / "out"
        collected = tr.collect(gh, REPO, out, 3)
        self.assertEqual([p.name for p in collected], ["run-3", "run-2", "run-1"])
        listed = [Path(line).name for line in (out / tr.DIRS_FILE).read_text().split("\n") if line]
        self.assertEqual(listed, ["run-1", "run-2", "run-3"])

        output = self.root / "merged.json"
        changed, _ = tr.plan_change(seed, [Path(line) for line in (out / tr.DIRS_FILE).read_text().split("\n")
                                           if line], output, self.repo.root,
                                    merge=self.merge, share=lambda path, root: (0, 1))
        self.assertTrue(changed)
        seconds = json.loads(output.read_text())["atoms"][self.KEY]["seconds"]
        self.assertEqual(seconds, 11.0)  # chronological 4, 8, 16 from 4; newest first would give 6.5


class PlanChangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.timings = self.root / "timings.json"
        self.timings.write_text("old\n", encoding="utf-8")
        self.output = self.root / "merged.json"
        self.dirs = [self.root / "run-1"]

    @staticmethod
    def share(path: Path, root: Path) -> tuple[int, int]:
        return (10, 100) if path.read_text() == "old\n" else (2, 100)

    def test_an_unchanged_merge_is_not_a_change_and_writes_nothing(self) -> None:
        changed, lines = tr.plan_change(self.timings, self.dirs, self.output,
                                        merge=lambda into, dirs, root: None, share=self.share)
        self.assertFalse(changed)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.timings.read_text(), "old\n")
        self.assertEqual(lines[-1], "the merge left the timings unchanged")

    def test_a_changed_merge_writes_the_merged_file_to_output_not_over_the_input(self) -> None:
        merged_into: list[Path] = []

        def merge(into: Path, dirs, root) -> None:
            merged_into.append(into)
            into.write_text("new\n", encoding="utf-8")

        changed, lines = tr.plan_change(self.timings, self.dirs, self.output, merge=merge, share=self.share)
        self.assertTrue(changed)
        self.assertEqual(self.output.read_text(), "new\n")
        self.assertEqual(self.timings.read_text(), "old\n")  # merged into a copy
        self.assertNotEqual(merged_into[0], self.timings)

    def test_the_untimed_share_is_printed_before_and_after(self) -> None:
        _, lines = tr.plan_change(self.timings, self.dirs, self.output,
                                  merge=lambda into, dirs, root: into.write_text("new\n"), share=self.share)
        self.assertEqual(lines[0], "before: 10 of 100 CI-placed tests untimed (10.0 percent)")
        self.assertEqual(lines[1], "after: 2 of 100 CI-placed tests untimed (2.0 percent)")

    def test_no_directories_is_unchanged_without_merging(self) -> None:
        def merge(*args) -> None:
            raise AssertionError("must not merge")

        changed, _ = tr.plan_change(self.timings, [], self.output, merge=merge, share=self.share)
        self.assertFalse(changed)

    def test_the_real_untimed_share_counts_only_ci_placed_untimed_tests(self) -> None:
        untimed, total = tr.untimed_share(fixtures.REPO_ROOT / "tools" / "test_timings.json",
                                          fixtures.REPO_ROOT)
        self.assertGreater(total, 0)
        self.assertLessEqual(untimed, total)

    def test_the_command_line_writes_changed_to_the_github_output(self) -> None:
        dirs_file = self.root / "dirs.txt"
        dirs_file.write_text(f"{self.dirs[0]}\n", encoding="utf-8")
        output_file = self.root / "gh_output"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}), \
                contextlib.redirect_stdout(io.StringIO()):
            status = tr.main(["plan-change", "--timings", str(self.timings), "--output", str(self.output),
                              "--dirs-file", str(dirs_file), "--repo-root", str(self.root)],
                             merge=lambda into, dirs, root: None, share=self.share)
        self.assertEqual(status, 0)
        self.assertEqual(output_file.read_text(), "changed=false\n")


class ProposeTest(unittest.TestCase):
    TOKEN = {tr.TOKEN_VARIABLE: "secret-token"}

    def test_a_missing_token_fails_with_the_named_message_and_runs_nothing(self) -> None:
        for environ in ({}, {tr.TOKEN_VARIABLE: ""}):
            gh = FakeGh()
            with self.assertRaises(tr.RefreshError) as caught:
                tr.propose(gh, REPO, environ, "body")
            self.assertIn("TIMINGS_REFRESH_TOKEN", str(caught.exception))
            self.assertEqual(gh.calls, [])

    def test_the_command_line_exits_one_naming_the_secret_and_opens_nothing(self) -> None:
        gh = FakeGh()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = tr.main(["propose", "--repo", REPO], runner=gh, environ={})
        self.assertEqual(status, 1)
        self.assertIn("TIMINGS_REFRESH_TOKEN", err.getvalue())
        self.assertEqual(gh.calls, [])

    def test_a_first_run_opens_one_pull_request_from_the_bot_branch(self) -> None:
        gh = FakeGh(pulls=[])
        self.assertEqual(tr.propose(gh, REPO, self.TOKEN, "body"), "opened")
        self.assertEqual(len(gh.commands("gh", "pr", "create")), 1)
        self.assertEqual(gh.commands("gh", "pr", "edit"), [])
        create = gh.commands("gh", "pr", "create")[0]
        self.assertEqual(create[create.index("--head") + 1], tr.BRANCH)
        self.assertEqual(create[create.index("--title") + 1], tr.TITLE)
        self.assertEqual(create[create.index("--base") + 1], "main")

    def test_an_existing_branch_is_updated_with_force_with_lease_never_plain_force(self) -> None:
        gh = FakeGh(pulls=[])
        tr.propose(gh, REPO, self.TOKEN, "body")
        push = gh.commands("git", "push")
        self.assertEqual(len(push), 1)
        self.assertEqual(push[0][:3], ["git", "push", f"--force-with-lease={tr.BRANCH}"])
        self.assertNotIn("--force", push[0])
        self.assertNotIn("-f", push[0])
        self.assertEqual(gh.commands("git", "checkout"), [["git", "checkout", "-B", tr.BRANCH]])

    def test_the_lease_still_pushes_when_the_branch_does_not_exist_yet(self) -> None:
        gh = FakeGh(failing=("git", "fetch"))
        self.assertEqual(tr.propose(gh, REPO, self.TOKEN, "body"), "opened")
        self.assertEqual(len(gh.commands("git", "push")), 1)

    def test_an_open_refresh_pull_request_is_updated_and_never_duplicated(self) -> None:
        gh = FakeGh(pulls=[{"number": 41}])
        self.assertEqual(tr.propose(gh, REPO, self.TOKEN, "body"), "updated")
        self.assertEqual(gh.commands("gh", "pr", "create"), [])
        edit = gh.commands("gh", "pr", "edit")[0]
        self.assertEqual(edit[3], "41")
        listing = gh.commands("gh", "pr", "list")[0]
        self.assertEqual(listing[listing.index("--state") + 1], "open")  # a closed one is not reused
        self.assertEqual(listing[listing.index("--head") + 1], tr.BRANCH)

    def test_the_token_reaches_git_only_through_the_environment_of_the_network_commands(self) -> None:
        gh = FakeGh(pulls=[])
        tr.propose(gh, REPO, self.TOKEN, "body")
        for argv, env in gh.calls:
            self.assertNotIn("secret-token", " ".join(argv))
            network = argv[:2] in (["git", "fetch"], ["git", "push"]) or argv[0] == "gh"
            with self.subTest(argv=argv[:3]):
                self.assertEqual("GH_TOKEN" in env, network)
        self.assertNotIn("secret-token", json.dumps([env.get("GIT_CONFIG_VALUE_0") for _, env in gh.calls]))

    def test_the_commit_is_the_one_timings_file_with_the_ci_title(self) -> None:
        gh = FakeGh()
        tr.propose(gh, REPO, self.TOKEN, "body")
        self.assertEqual(gh.commands("git", "add"), [["git", "add", "--", tr.TIMINGS_PATH]])
        commit = [argv for argv, _ in gh.calls if "commit" in argv][0]
        self.assertEqual(commit[commit.index("--message") + 1], "ci: refresh the shard timings (automated)")


if __name__ == "__main__":
    unittest.main()
