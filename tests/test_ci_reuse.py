"""Tests for ``tools/ci_reuse.py``: the switch, the lookup of a merged pull
request's CI run (against fake API replies, no network), the fail-closed
comparison of the recorded tree and plan digest, the report, and the command
line's outputs. Nothing here touches the network or the user's configuration."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

_spec = importlib.util.spec_from_file_location("ci_reuse", fixtures.REPO_ROOT / "tools" / "ci_reuse.py")
reuse = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = reuse
_spec.loader.exec_module(reuse)

REPO = "owner/repo"
SHA = "a" * 40
HEAD = "b" * 40
TREE = "c" * 40
DIGEST = "d" * 64


def pull(**changes) -> dict:
    base = {"number": 7, "merged_at": "2026-10-13T10:00:00Z", "merge_commit_sha": SHA,
            "base": {"ref": "main"}, "head": {"sha": HEAD, "repo": {"full_name": REPO}}}
    base.update(changes)
    return base


def run(run_id: int = 100, **changes) -> dict:
    base = {"id": run_id, "run_number": run_id, "run_attempt": 1, "head_sha": HEAD,
            "event": "pull_request", "conclusion": "success", "status": "completed",
            "path": ".github/workflows/ci.yml", "head_repository": {"full_name": REPO}}
    base.update(changes)
    return base


class FakeApi:
    def __init__(self, pulls=None, runs=None) -> None:
        self.pulls, self.runs, self.paths = pulls, runs, []

    def __call__(self, path: str):
        self.paths.append(path)
        if "/pulls" in path:
            return self.pulls
        return {"workflow_runs": self.runs or []}


def find(api: FakeApi, event: str = "push", ref: str = "refs/heads/main") -> dict:
    return reuse.find_candidate(api, REPO, SHA, event, ref)


class ModeTest(unittest.TestCase):
    def test_unset_or_report_is_report_only(self) -> None:
        for raw in (None, "", "  ", "report"):
            self.assertEqual(reuse.parse_mode(raw), ("report", ""))

    def test_on_and_off(self) -> None:
        self.assertEqual(reuse.parse_mode("on"), ("on", ""))
        self.assertEqual(reuse.parse_mode("off"), ("off", ""))

    def test_anything_else_is_off_and_says_so(self) -> None:
        for raw in ("ON", "true", "1", "reuse"):
            mode, note = reuse.parse_mode(raw)
            self.assertEqual(mode, "off")
            self.assertIn("treated as off", note)
            self.assertIn(repr(raw), note)


class FindCandidateTest(unittest.TestCase):
    def test_the_latest_successful_run_on_the_final_head(self) -> None:
        api = FakeApi([pull()], [run(100), run(105, run_attempt=2), run(103)])
        facts = find(api)
        self.assertEqual((facts["candidate"], facts["pr"], facts["pr_head"]), (105, 7, HEAD))
        self.assertTrue(any(f"head_sha={HEAD}" in p and "event=pull_request" in p for p in api.paths))

    def test_not_a_push_makes_no_api_call(self) -> None:
        for event in ("workflow_dispatch", "schedule"):
            api = FakeApi([pull()], [run()])
            facts = find(api, event=event)
            self.assertIsNone(facts["candidate"])
            self.assertIn(event, facts["reason"])
            self.assertEqual(api.paths, [])

    def test_not_main_makes_no_api_call(self) -> None:
        api = FakeApi([pull()], [run()])
        self.assertIsNone(find(api, ref="refs/heads/other")["candidate"])
        self.assertEqual(api.paths, [])

    def test_no_merged_pull_request(self) -> None:
        for pulls in ([], [pull(merged_at=None)], [pull(merge_commit_sha="e" * 40)],
                      [pull(base={"ref": "dev"})]):
            facts = find(FakeApi(pulls, [run()]))
            self.assertIsNone(facts["candidate"])
            self.assertIn("0 merged pull requests", facts["reason"])

    def test_two_pull_requests_are_ambiguous(self) -> None:
        facts = find(FakeApi([pull(), pull(number=8)], [run()]))
        self.assertIsNone(facts["candidate"])
        self.assertIn("2 merged pull requests", facts["reason"])

    def test_a_fork_pull_request(self) -> None:
        other = pull(head={"sha": HEAD, "repo": {"full_name": "someone/repo"}})
        facts = find(FakeApi([other], [run()]))
        self.assertIsNone(facts["candidate"])
        self.assertIn("someone/repo", facts["reason"])

    def test_a_deleted_fork_has_no_head_repository(self) -> None:
        facts = find(FakeApi([pull(head={"sha": HEAD, "repo": None})], [run()]))
        self.assertIsNone(facts["candidate"])

    def test_runs_that_do_not_count(self) -> None:
        bad = [run(conclusion="failure"), run(status="in_progress"), run(event="push"),
               run(head_sha="f" * 40), run(path=".github/workflows/main.yml"),
               run(head_repository={"full_name": "someone/repo"})]
        for one in bad:
            facts = find(FakeApi([pull()], [one]))
            with self.subTest(run=one):
                self.assertIsNone(facts["candidate"])
                self.assertIn("no successful CI run", facts["reason"])

    def test_no_runs(self) -> None:
        self.assertIsNone(find(FakeApi([pull()], []))["candidate"])


def record(directory: Path, attempt: int = 1, tree: str = TREE, digest: str = DIGEST,
           with_tree: bool = True) -> Path:
    path = directory / f"timings-ci-attempt-{attempt}"
    path.mkdir(parents=True)
    (path / "plan.json").write_text(json.dumps({"plan_digest": digest}), encoding="utf-8")
    if with_tree:
        (path / "tree.txt").write_text(tree + "\n", encoding="utf-8")
    return path


class CompareTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_equal_tree_and_digest_pass(self) -> None:
        record(self.dir)
        result = reuse.compare(self.dir, TREE, DIGEST)
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "")

    def test_highest_attempt_is_taken(self) -> None:
        record(self.dir, 1, tree="1" * 40)
        record(self.dir, 3)
        record(self.dir, 2, tree="2" * 40)
        result = reuse.compare(self.dir, TREE, DIGEST)
        self.assertTrue(result.ok)
        self.assertEqual(result.recorded.attempt, 3)

    def test_a_different_tree_refuses(self) -> None:
        record(self.dir)
        result = reuse.compare(self.dir, "9" * 40, DIGEST)
        self.assertFalse(result.ok)
        self.assertIs(result.tree_equal, False)
        self.assertIn("base moved", result.reason)

    def test_a_different_digest_refuses(self) -> None:
        record(self.dir)
        result = reuse.compare(self.dir, TREE, "9" * 64)
        self.assertFalse(result.ok)
        self.assertIs(result.digest_equal, False)
        self.assertIn("plan digest", result.reason)

    def test_missing_pieces_refuse(self) -> None:
        self.assertIn("no timings", reuse.compare(self.dir / "absent", TREE, DIGEST).reason)
        record(self.dir, with_tree=False)
        self.assertIn("predates reuse", reuse.compare(self.dir, TREE, DIGEST).reason)

    def test_malformed_records_refuse(self) -> None:
        path = record(self.dir, tree="not a tree")
        self.assertIn("malformed", reuse.compare(self.dir, TREE, DIGEST).reason)
        (path / "plan.json").write_text("{", encoding="utf-8")
        self.assertIn("plan.json", reuse.compare(self.dir, TREE, DIGEST).reason)

    def test_unrelated_directories_are_ignored(self) -> None:
        (self.dir / "results-0-attempt-1").mkdir()
        self.assertIn("no timings", reuse.compare(self.dir, TREE, DIGEST).reason)


class DecideTest(unittest.TestCase):
    FACTS = {"sha": SHA, "pr": 7, "pr_head": HEAD, "candidate": 100, "run_attempt": 1}

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        record(self.dir)

    def good(self):
        return reuse.compare(self.dir, TREE, DIGEST)

    def test_report_mode_logs_every_fact_and_would_reuse(self) -> None:
        decision = reuse.decide("report", "", self.FACTS, self.good(), TREE, DIGEST)
        text = decision.summary()
        for fragment in ("100", "#7", HEAD, TREE, DIGEST, "(equal)", "would reuse",
                         "full validation still runs"):
            self.assertIn(fragment, text)
        self.assertTrue(decision.reuse)

    def test_on_mode_reuses(self) -> None:
        decision = reuse.decide("on", "", self.FACTS, self.good(), TREE, DIGEST)
        self.assertTrue(decision.reuse)
        self.assertIn("Decision: reuse", decision.summary())

    def test_would_not_reuse_names_the_reason(self) -> None:
        other = reuse.compare(self.dir, "9" * 40, DIGEST)
        decision = reuse.decide("report", "", self.FACTS, other, "9" * 40, DIGEST)
        self.assertFalse(decision.reuse)
        self.assertIn("would not reuse: the recorded tree differs", decision.summary())
        self.assertIn("DIFFERENT", decision.summary())

    def test_no_candidate_says_why(self) -> None:
        facts = {"sha": SHA, "candidate": None, "reason": "the event is workflow_dispatch, not a push"}
        decision = reuse.decide("report", "", facts, None, TREE, None)
        self.assertFalse(decision.reuse)
        self.assertIn("would not reuse: the event is workflow_dispatch", decision.summary())

    def test_off_does_nothing_and_says_so(self) -> None:
        decision = reuse.decide("off", "CI_MAIN_REUSE='x' is not on, report or off; treated as off",
                                None, None, "", None)
        self.assertFalse(decision.reuse)
        self.assertIn("reuse is off", decision.summary())
        self.assertIn("treated as off", decision.summary())


class CommandLineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.output = self.dir / "output"
        self.summary = self.dir / "summary"
        env = {"GITHUB_OUTPUT": str(self.output), "GITHUB_STEP_SUMMARY": str(self.summary)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        tree = mock.patch.object(reuse, "_git_tree", return_value=TREE)
        tree.start()
        self.addCleanup(tree.stop)
        self.plan = self.dir / "plan.json"
        self.plan.write_text(json.dumps({"plan_digest": DIGEST}), encoding="utf-8")
        self.recorded = self.dir / "recorded"
        self.facts = self.dir / "facts.json"

    def call(self, *argv: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return reuse.main(list(argv))

    def outputs(self) -> dict:
        if not self.output.exists():
            return {}
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines())

    def find(self, mode: str, api) -> None:
        with mock.patch.object(reuse, "_github_get", api):
            self.assertEqual(self.call("find", "--repo", REPO, "--sha", SHA, "--event", "push",
                                       "--ref", "refs/heads/main", "--mode", mode,
                                       "--facts", str(self.facts)), 0)

    def decide(self) -> dict:
        self.output.write_text("")
        self.assertEqual(self.call("decide", "--facts", str(self.facts), "--reused-dir",
                                   str(self.recorded), "--plan", str(self.plan)), 0)
        return self.outputs()

    def test_on_outputs_the_run_id(self) -> None:
        record(self.recorded)
        self.find("on", FakeApi([pull()], [run(100)]))
        self.assertEqual(self.outputs()["candidate"], "100")
        self.assertEqual(self.decide()["run-id"], "100")
        self.assertIn("Decision: reuse", self.summary.read_text())

    def test_report_never_outputs_a_run_id(self) -> None:
        record(self.recorded)
        for mode in ("report", ""):
            self.find(mode, FakeApi([pull()], [run(100)]))
            self.assertEqual(self.decide()["run-id"], "")
        self.assertIn("would reuse", self.summary.read_text())

    def test_off_and_unknown_never_look_anything_up(self) -> None:
        def forbidden(path: str):
            raise AssertionError(f"off made an API call: {path}")
        for mode in ("off", "yes"):
            self.find(mode, forbidden)
            self.assertEqual(self.decide()["run-id"], "")
        self.assertIn("treated as off", self.summary.read_text())

    def test_on_with_a_moved_base_outputs_nothing(self) -> None:
        record(self.recorded, tree="9" * 40)
        self.find("on", FakeApi([pull()], [run(100)]))
        self.assertEqual(self.decide()["run-id"], "")
        self.assertIn("do not reuse", self.summary.read_text())

    def test_on_with_a_different_plan_outputs_nothing(self) -> None:
        record(self.recorded, digest="9" * 64)
        self.find("on", FakeApi([pull()], [run(100)]))
        self.assertEqual(self.decide()["run-id"], "")

    def test_on_without_the_artifact_outputs_nothing(self) -> None:
        self.find("on", FakeApi([pull()], [run(100)]))
        self.assertEqual(self.decide()["run-id"], "")

    def test_on_with_an_unreadable_plan_outputs_nothing(self) -> None:
        record(self.recorded)
        self.find("on", FakeApi([pull()], [run(100)]))
        self.plan.unlink()
        self.assertEqual(self.decide()["run-id"], "")
        self.assertIn("comparison failed", self.summary.read_text())

    def test_an_api_failure_is_no_candidate(self) -> None:
        def broken(path: str):
            raise OSError("network down")
        self.find("on", broken)
        self.assertEqual(self.outputs()["candidate"], "")
        self.assertEqual(self.decide()["run-id"], "")
        self.assertIn("lookup failed", self.summary.read_text())

    def test_missing_facts_are_off(self) -> None:
        self.assertEqual(self.decide()["run-id"], "")

    def test_verify_passes_and_names_the_directory(self) -> None:
        path = record(self.recorded)
        self.assertEqual(self.call("verify", "--reused-dir", str(self.recorded),
                                   "--plan", str(self.plan)), 0)
        self.assertEqual(self.outputs()["selected-dir"], str(path))

    def test_verify_fails_closed(self) -> None:
        cases = {"no artifact": None, "tree": {"tree": "9" * 40}, "digest": {"digest": "9" * 64},
                 "predates": {"with_tree": False}}
        for label, changes in cases.items():
            with self.subTest(label):
                recorded = self.dir / f"r-{label.replace(' ', '-')}"
                if changes is not None:
                    record(recorded, **changes)
                self.output.write_text("")
                self.assertEqual(self.call("verify", "--reused-dir", str(recorded),
                                           "--plan", str(self.plan)), 1)
                self.assertNotIn("selected-dir", self.outputs())

    def test_verify_with_an_unreadable_plan_fails(self) -> None:
        record(self.recorded)
        self.plan.unlink()
        self.assertEqual(self.call("verify", "--reused-dir", str(self.recorded),
                                   "--plan", str(self.plan)), 1)


if __name__ == "__main__":
    unittest.main()
