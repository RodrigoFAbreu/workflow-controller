"""`workflow-controller-h4f` CP3: the managed repository's checkpoint claim and amendment witness, read before the
implementer worker's clone is made (``controller.checkpoint_claims``, D5).

Real Git repositories in scratch directories. The Controller-side literal of the Workflow's layout is pinned to the
vendored release's own functions in a subprocess, as ``tests/test_target_state.py`` pins the phase list.
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

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import checkpoint_claims as cc, gitrepo  # noqa: E402
from controller.managed_repo import VALIDATED_WORKFLOW_RELEASES  # noqa: E402
from tests import fixtures  # noqa: E402

ITEM = "wi-1"
STATE = "docs/ai-workflow/WORKFLOW_STATE.json"


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def entry(**changes) -> dict:
    return {"requested_at": "2026-01-01T00:00:00Z", "reason": "r", "resolved_at_plan_revision": 2,
            "reconciliation_outcome": "same_content", "resolved_review_content_id": "abc", **changes}


class ClaimsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        self.commit_state({"work_items": {ITEM: {"amendment_history": [entry()]}}})
        self.managed = SimpleNamespace(root=self.root)
        self.common = Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        self.claims = self.common / cc.CLAIMS_RELDIR

    def commit_state(self, state) -> None:
        path = self.root / STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state if isinstance(state, str) else json.dumps(state))
        git(self.root, "add", "--", STATE)
        git(self.root, "commit", "-q", "-m", "state")

    def write_claim(self, text: str = "{}") -> Path:
        self.claims.mkdir(parents=True, exist_ok=True)
        path = cc.claim_path(self.common, ITEM)
        path.write_text(text)
        return path

    def write_witness(self, **fields) -> Path:
        self.claims.mkdir(parents=True, exist_ok=True)
        path = cc.witness_path(self.common, ITEM)
        data = {"schema_version": 1, "work_item_id": ITEM, "status": "NONE", "amendment_seq": 0, **fields}
        path.write_text(json.dumps(data))
        return path

    def resolved(self, **fields) -> Path:
        return self.write_witness(status="RESOLVED", amendment_seq=1, request_projection_sha256="r" * 8,
                                  resolution_projection_sha256=cc.resolution_projection_sha256(entry()), **fields)

    def refused(self) -> str:
        with self.assertRaises(cc.ManagedStateRefused) as ctx:
            cc.check(self.managed, ITEM, gitrepo.subprocess_runner())
        self.assertEqual(ctx.exception.evidence["cause"], ctx.exception.cause)
        self.assertIn(cc.REMEDIES[ctx.exception.cause], str(ctx.exception))
        self.assertTrue(str(ctx.exception).startswith(ctx.exception.cause))
        return ctx.exception.cause

    def clean(self) -> None:
        cc.check(self.managed, ITEM, gitrepo.subprocess_runner())


class ClaimTest(ClaimsFixture):
    def test_no_claims_directory_is_clean(self) -> None:
        self.clean()

    def test_an_empty_claims_directory_is_clean(self) -> None:
        self.claims.mkdir(parents=True)
        self.clean()

    def test_another_items_claim_is_not_this_items(self) -> None:
        self.claims.mkdir(parents=True)
        (cc.claim_path(self.common, "other")).write_text("{}")
        self.clean()

    def test_a_claim_file_is_present_whatever_it_holds(self) -> None:
        for text in ("{}", "", "not json", json.dumps({"work_item_id": "other"})):
            with self.subTest(text=text):
                path = self.write_claim(text)
                self.assertEqual(self.refused(), "managed_claim_present")
                path.unlink()

    def test_a_claim_that_is_a_link_a_directory_or_a_fifo_is_unreadable(self) -> None:
        self.claims.mkdir(parents=True)
        path = cc.claim_path(self.common, ITEM)
        (self.tmp / "target").write_text("{}")
        path.symlink_to(self.tmp / "target")
        self.assertEqual(self.refused(), "managed_claim_unreadable")
        path.unlink()
        path.mkdir()
        self.assertEqual(self.refused(), "managed_claim_unreadable")
        path.rmdir()
        os.mkfifo(path)
        self.assertEqual(self.refused(), "managed_claim_unreadable")

    def test_a_claims_directory_that_is_a_link_or_a_file_is_unreadable(self) -> None:
        self.claims.parent.mkdir(parents=True, exist_ok=True)
        (self.tmp / "elsewhere").mkdir()
        self.claims.symlink_to(self.tmp / "elsewhere")
        self.assertEqual(self.refused(), "managed_claim_unreadable")
        self.claims.unlink()
        self.claims.write_text("x")
        self.assertEqual(self.refused(), "managed_claim_unreadable")

    def test_an_oversized_claim_is_unreadable_never_read(self) -> None:
        path = self.write_claim()
        with path.open("wb") as handle:
            handle.truncate(cc.MAX_BYTES + 1)
        self.assertEqual(self.refused(), "managed_claim_unreadable")

    def test_a_claim_in_a_linked_worktrees_common_dir_is_the_same_claim(self) -> None:
        linked = self.tmp / "linked"
        git(self.root, "worktree", "add", "-q", "-b", "other", str(linked))
        self.write_claim()
        with self.assertRaises(cc.ManagedStateRefused) as ctx:
            cc.check(SimpleNamespace(root=linked), ITEM, gitrepo.subprocess_runner())
        self.assertEqual(ctx.exception.cause, "managed_claim_present")

    def test_the_claim_outranks_the_witness(self) -> None:
        self.write_claim()
        self.write_witness(status="OPEN", amendment_seq=1, request_projection_sha256="r", previous={})
        self.assertEqual(self.refused(), "managed_claim_present")

    def test_check_writes_nothing(self) -> None:
        self.claims.mkdir(parents=True)
        before = sorted(p.name for p in self.claims.iterdir()), git(self.root, "status", "--porcelain")
        self.clean()
        self.assertEqual((sorted(p.name for p in self.claims.iterdir()), git(self.root, "status", "--porcelain")), before)


class WitnessTest(ClaimsFixture):
    def test_an_absent_witness_and_a_none_witness_are_clean(self) -> None:
        self.claims.mkdir(parents=True)
        self.clean()
        self.write_witness()
        self.clean()

    def test_an_open_or_resolving_witness_refuses(self) -> None:
        for status in ("OPEN", "RESOLVING"):
            with self.subTest(status=status):
                self.write_witness(status=status, amendment_seq=1, request_projection_sha256="r", previous={})
                self.assertEqual(self.refused(), "managed_amendment_open")

    def test_a_resolved_witness_the_head_carries_is_clean(self) -> None:
        self.resolved()
        self.clean()

    def test_a_resolved_witness_the_head_lacks_is_stale(self) -> None:
        for state in ({"work_items": {ITEM: {"amendment_history": []}}},
                      {"work_items": {ITEM: {"amendment_history": [entry(resolved_at_plan_revision=None)]}}},
                      {"work_items": {ITEM: {}}}):
            with self.subTest(state=state):
                self.commit_state(state)
                self.resolved()
                self.assertEqual(self.refused(), "managed_amendment_stale")

    def test_a_resolved_witness_the_head_contradicts_is_a_conflict(self) -> None:
        for change in ({"reconciliation_outcome": "changed"}, {"resolved_at_plan_revision": 3},
                       {"resolved_review_content_id": "zzz"}):
            with self.subTest(change=change):
                self.commit_state({"work_items": {ITEM: {"amendment_history": [entry(**change)]}}})
                self.resolved()
                self.assertEqual(self.refused(), "managed_amendment_conflict")

    def test_the_resolution_comparison_reads_the_committed_state_not_the_work_tree(self) -> None:
        (self.root / STATE).write_text(json.dumps({"work_items": {ITEM: {"amendment_history": []}}}))
        self.resolved()
        self.clean()

    def test_a_witness_of_a_shape_the_workflow_refuses_is_unreadable(self) -> None:
        path = cc.witness_path(self.common, ITEM)
        bad = [
            {"schema_version": 2}, {"work_item_id": "other"}, {"status": "WAITING"}, {"amendment_seq": -1},
            {"amendment_seq": True}, {"amendment_seq": "1"}, {"status": "NONE", "amendment_seq": 1},
            {"status": "RESOLVED", "amendment_seq": 0, "resolution_projection_sha256": "x"},
            {"status": "RESOLVED", "amendment_seq": 1},
            {"status": "RESOLVED", "amendment_seq": 1.5, "resolution_projection_sha256": "x"},
        ]
        for fields in bad:
            with self.subTest(fields=fields):
                self.write_witness(**fields)
                self.assertEqual(self.refused(), "managed_amendment_unreadable")
        self.claims.mkdir(parents=True, exist_ok=True)
        for text in ("{torn", "[]", "null", "\xff\xfe"):
            with self.subTest(text=text):
                path.write_bytes(text.encode("latin-1"))
                self.assertEqual(self.refused(), "managed_amendment_unreadable")

    def test_a_witness_that_is_a_link_a_directory_or_oversized_is_unreadable(self) -> None:
        path = self.write_witness()
        path.unlink()
        (self.tmp / "target").write_text("{}")
        path.symlink_to(self.tmp / "target")
        self.assertEqual(self.refused(), "managed_amendment_unreadable")
        path.unlink()
        path.mkdir()
        self.assertEqual(self.refused(), "managed_amendment_unreadable")
        path.rmdir()
        with path.open("wb") as handle:
            handle.truncate(cc.MAX_BYTES + 1)
        self.assertEqual(self.refused(), "managed_amendment_unreadable")

    def test_committed_state_that_cannot_be_read_as_data_is_unreadable(self) -> None:
        for state in ("{torn", "[]", json.dumps({"work_items": []}), json.dumps({"work_items": {ITEM: []}}),
                      json.dumps({"work_items": {ITEM: {"amendment_history": "x"}}}),
                      json.dumps({"work_items": {ITEM: {"amendment_history": ["x"]}}}),
                      json.dumps({"work_items": {ITEM: {"amendment_history": [5]}}})):
            with self.subTest(state=state):
                self.commit_state(state)
                self.resolved()
                self.assertEqual(self.refused(), "managed_amendment_unreadable")

    def test_a_head_without_the_state_file_is_unreadable(self) -> None:
        git(self.root, "rm", "-q", "--", STATE)
        git(self.root, "commit", "-q", "-m", "drop")
        self.resolved()
        self.assertEqual(self.refused(), "managed_amendment_unreadable")


class LaggingWorktreeTest(ClaimsFixture):
    """The Workflow's mixed-release check (``_lagging_checks``) for a linked worktree the private clone cannot see."""

    INSTALL = ".workflow-manager/installation.json"

    def linked(self, version: str = "2.5.1", history=(entry(resolved_at_plan_revision=None),)) -> Path:
        path = self.tmp / "linked"
        git(self.root, "worktree", "add", "-q", "-B", "other", str(path))
        (path / ".workflow-manager").mkdir(exist_ok=True)
        (path / self.INSTALL).write_text(json.dumps({"workflow_version": version}))
        (path / STATE).write_text(json.dumps({"work_items": {ITEM: {"amendment_history": list(history)}}}))
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "linked")
        return path

    def test_an_unrecorded_amendment_in_a_lagging_worktree_refuses_for_an_absent_or_none_witness(self) -> None:
        self.linked()
        self.assertEqual(self.refused(), "managed_amendment_lagging")
        self.claims.mkdir(parents=True)
        self.assertEqual(self.refused(), "managed_amendment_lagging")
        self.write_witness()
        self.assertEqual(self.refused(), "managed_amendment_lagging")

    def test_a_clean_lagging_worktree_or_a_current_one_is_eligible(self) -> None:
        self.linked(history=(entry(),))
        self.clean()
        git(self.root, "worktree", "remove", "--force", str(self.tmp / "linked"))
        self.linked(version="2.6.0")
        self.clean()

    def test_an_unorderable_or_missing_installation_record_lags(self) -> None:
        path = self.linked(version="weird")
        self.assertEqual(self.refused(), "managed_amendment_lagging")
        (path / self.INSTALL).unlink()
        git(path, "commit", "-q", "-am", "drop")
        self.assertEqual(self.refused(), "managed_amendment_lagging")

    def test_a_recorded_amendment_is_eligible(self) -> None:
        self.linked(history=(entry(resolved_at_plan_revision=None),))
        self.write_witness(status="RESOLVED", amendment_seq=2, request_projection_sha256="r" * 8,
                           resolution_projection_sha256=cc.resolution_projection_sha256(entry()))
        self.commit_state({"work_items": {ITEM: {"amendment_history": [entry(), entry()]}}})
        self.clean()

    def test_an_unreadable_state_in_a_linked_worktree_is_unreadable(self) -> None:
        path = self.linked()
        (path / STATE).write_text("{torn")
        self.assertEqual(self.refused(), "managed_amendment_unreadable")


class LaggingAgreementTest(ClaimsFixture):
    """The check agrees with the admitted release's own ``_lagging_checks`` on the same repository: for the absent,
    ``NONE`` and ``RESOLVED`` witnesses, both refusal classes and the repository where nothing lags."""

    def workflow_decision(self, release: str, witness: dict | None) -> str:
        code = ("import sys, json\nimport workflow_state as w\nfrom pathlib import Path\n"
                f"root = Path({json.dumps(str(self.root))})\nwitness = json.loads({json.dumps(json.dumps(witness))})\n"
                "try:\n    probe = w._probe_worktrees(root)\n"
                "    here = w._find_probed_worktree(probe, w._git_identity(root)[2], w._worktree_git_dir(root))\n"
                "    derivable = w._bootstrap_derivable_entries(probe, here, " + json.dumps(ITEM) + ") if witness is None "
                "and probe['lagging'] else frozenset()\n"
                "    w._lagging_checks(root, " + json.dumps(ITEM) + ", witness, probe, derivable=derivable)\n"
                "except w.LaggingWorktreeAmendmentError:\n    print('managed_amendment_lagging')\n"
                "except w.AmendmentResolutionConflictError:\n    print('managed_amendment_conflict')\n"
                "else:\n    print('ok')\n")
        result = subprocess.run([sys.executable, "-B", "-E", "-s", "-c", code],
                                cwd=fixtures.workflow_release_tree(release) / "scripts", capture_output=True,
                                text=True, check=True, timeout=120)
        return result.stdout.strip()

    def controller_decision(self) -> str:
        try:
            cc.check(self.managed, ITEM, gitrepo.subprocess_runner())
        except cc.ManagedStateRefused as exc:
            return exc.cause
        return "ok"

    def linked(self, name: str, version: str, history: list[dict], *, raw: str | None = None) -> Path:
        path = self.tmp / name
        git(self.root, "worktree", "add", "-q", "-b", name, str(path))
        (path / ".workflow-manager").mkdir(exist_ok=True)
        (path / ".workflow-manager" / "installation.json").write_text(json.dumps({"workflow_version": version}))
        (path / STATE).write_text(raw if raw is not None else json.dumps({"work_items": {ITEM: {
            "amendment_history": history}}}))
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "x")
        return path

    def test_the_decision_matches_the_release(self) -> None:
        resolved_witness = {"schema_version": 1, "work_item_id": ITEM, "status": "RESOLVED", "amendment_seq": 1,
                            "request_projection_sha256": "r" * 8,
                            "resolution_projection_sha256": cc.resolution_projection_sha256(entry())}
        none_witness = {"schema_version": 1, "work_item_id": ITEM, "status": "NONE", "amendment_seq": 0}
        scenarios = [
            # name, witness, lagging version, linked history, linked raw state, expected
            ("absent-pending", None, "2.5.1", [entry(resolved_at_plan_revision=None)], None, "managed_amendment_lagging"),
            ("absent-resolved", None, "2.5.1", [entry()], None, "ok"),
            ("none-pending", none_witness, "2.5.1", [entry(resolved_at_plan_revision=None)], None,
             "managed_amendment_lagging"),
            ("none-resolved", none_witness, "2.5.1", [entry()], None, "ok"),
            ("resolved-same", resolved_witness, "2.5.1", [entry()], None, "ok"),
            ("resolved-conflicting", resolved_witness, "2.5.1", [entry(reason="other")], None,
             "managed_amendment_conflict"),
            ("resolved-pending-above", resolved_witness, "2.5.1", [entry(), entry(resolved_at_plan_revision=None)], None,
             "managed_amendment_lagging"),
            # nothing lags: another worktree's unparseable state is not read
            ("all-current-absent", None, "2.6.0", [], "<<<<<<< conflict", "ok"),
            ("all-current-none", none_witness, "2.6.0", [], "<<<<<<< conflict", "ok"),
            ("all-current-resolved", resolved_witness, "2.6.0", [], "<<<<<<< conflict", "ok"),
        ]
        (self.root / ".workflow-manager").mkdir(exist_ok=True)
        (self.root / ".workflow-manager" / "installation.json").write_text('{"workflow_version": "2.6.0"}')
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "installation")
        for release in ("2.6.0",):
            for name, witness, version, history, raw, expected in scenarios:
                with self.subTest(release=release, scenario=name):
                    if witness is not None:
                        self.write_witness(**{k: v for k, v in witness.items()
                                              if k not in ("schema_version", "work_item_id")})
                    linked = self.linked(name, version, history, raw=raw)
                    decided = self.controller_decision()
                    self.assertEqual(decided, self.workflow_decision(release, witness))
                    self.assertEqual(decided, expected)
                    git(self.root, "worktree", "remove", "--force", str(linked))
                    cc.witness_path(self.common, ITEM).unlink(missing_ok=True)


class PinTest(unittest.TestCase):
    """The Controller-side literal equals the release's own functions, for every admitted release that has them."""

    def releases(self) -> list[str]:
        found = [r for r in sorted(VALIDATED_WORKFLOW_RELEASES)
                 if fixtures.evaluate_in_workflow_release(r, 'hasattr(workflow_state, "amendment_witness_path")')]
        self.assertTrue(found, "no admitted release has the amendment witness")
        return found

    def test_the_paths_the_statuses_and_the_projections_equal_the_releases(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            ids = [ITEM, "../../escape", "a/b/c", "/abs/path", "ünï-çode", ""]
            entries = [entry(), {"requested_at": "x"}, {"reason": "naïve — ünï", "resolved_at_plan_revision": 1},
                       entry(reconciliation_outcome=None, resolved_review_content_id=None)]
            for release in self.releases():
                with self.subTest(release=release):
                    repo_text = json.dumps(str(repo))
                    for item in ids:
                        item_text = json.dumps(item)
                        self.assertEqual(
                            fixtures.evaluate_in_workflow_release(
                                release, f"str(workflow_state.claim_path(workflow_state.Path({repo_text}), {item_text}))"),
                            str(cc.claim_path(common, item)))
                        self.assertEqual(
                            fixtures.evaluate_in_workflow_release(
                                release,
                                f"str(workflow_state.amendment_witness_path(workflow_state.Path({repo_text}), {item_text}))"),
                            str(cc.witness_path(common, item)))
                    self.assertEqual(
                        frozenset(fixtures.evaluate_in_workflow_release(
                            release, "sorted(workflow_state.AMENDMENT_WITNESS_STATUSES)")), cc.WITNESS_STATUSES)
                    self.assertEqual(
                        fixtures.evaluate_in_workflow_release(release, "workflow_state.AMENDMENT_WITNESS_SCHEMA_VERSION"),
                        cc.WITNESS_SCHEMA_VERSION)
                    self.assertEqual(
                        fixtures.evaluate_in_workflow_release(release, "workflow_state.CLAIMS_RELDIR"), cc.CLAIMS_RELDIR)
                    self.assertEqual(
                        frozenset(fixtures.evaluate_in_workflow_release(
                            release, "sorted(workflow_state.AMENDMENT_RESOLUTION_TIME_KEYS)")), cc.RESOLUTION_TIME_KEYS)
                    for value in entries:
                        text = f"json.loads({json.dumps(json.dumps(value))})"
                        self.assertEqual(
                            fixtures.evaluate_in_workflow_release(
                                release, f"workflow_state.amendment_request_projection_sha256({text})"),
                            cc.request_projection_sha256(value))
                        if value.get("resolved_at_plan_revision") is not None:
                            self.assertEqual(
                                fixtures.evaluate_in_workflow_release(
                                    release, f"workflow_state.amendment_resolution_projection_sha256({text})"),
                                cc.resolution_projection_sha256(value))


if __name__ == "__main__":
    unittest.main()
