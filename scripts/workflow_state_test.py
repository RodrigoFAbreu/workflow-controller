#!/usr/bin/env python3
"""Hermetic unit tests for workflow_state.py (WF1a, extended by WF1b).

Runs entirely against disposable scratch Git repositories this suite
creates and destroys itself, mirroring workflow_fingerprint_test.py's own
pattern. The real-repository demonstration lives separately in
workflow_state_demo_test.py.

Covers D3's "Validator rejects" list (WFR-09, WFR-14, WFR-18;
missing-test items 19, 29/73, 44, 50, 62, 63, 64, 116) to the extent
checkable from schema/registry/Git history alone -- see workflow_state.py's
module docstring for what is deliberately out of WF1a's scope.

WF1b additions: D1's work-item routing (create-or-resume, completion/
reset) and D-Registry/D4b's registry/mapping generator, including missing-
test item 35 (review_content_id invariant under Markdown-view
reformatting, sensitive to a real registry/mapping edit).

Stdlib-only. Run: python3 scripts/workflow_state_test.py
"""

from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import workflow_fingerprint as fingerprint
import workflow_state as ws


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


class ScratchRepo:
    """A disposable Git repo. `commit(subject, trailers=...)` writes a
    commit whose body carries the named Git trailers, exactly like a real
    Workflow-Checkpoint/Workflow-Work-Item-bearing commit."""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="wf-state-test-"))
        self._extra_worktrees: list[Path] = []
        _run(["git", "init", "-q"], cwd=self.root)
        _run(["git", "config", "user.email", "test@example.com"], cwd=self.root)
        _run(["git", "config", "user.name", "Test"], cwd=self.root)
        # Mirrors the real repository's own .gitignore: `.ai-review/` (the
        # WORKTREE_IDENTITY.json/.lock/checkpoint-claims home) is never
        # tracked or reported as an untracked dirty path there, and a
        # scratch repo without this ignores nothing, so
        # `identity_document_lock`'s own lock file would otherwise leak
        # into `_hash_dirty_paths`' snapshot.
        (self.root / ".gitignore").write_text(".ai-review/\n")
        (self.root / "README.md").write_text("base\n")
        _run(["git", "add", "README.md", ".gitignore"], cwd=self.root)
        _run(["git", "commit", "-q", "-m", "base"], cwd=self.root)
        self.base = self.head()
        return self

    def __exit__(self, *exc):
        import shutil
        for path in self._extra_worktrees:
            shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def worktree(self, name: str) -> Path:
        """A second **linked** worktree of this same repository, sharing
        this repo's `git rev-parse --git-common-dir` -- what a real
        second Claude Code session/worktree looks like to the claim/guard
        mechanism. Cleaned up alongside `self.root`."""
        path = self.root.parent / f"{self.root.name}-{name}"
        _run(["git", "worktree", "add", "-q", "-b", name, str(path), "HEAD"], cwd=self.root)
        self._extra_worktrees.append(path)
        return path

    def remove_worktree(self, path: Path) -> None:
        """Deregisters a linked worktree the way an operator's own
        `git worktree remove` would -- used by the abandoned-guard
        recovery tests, which require the holder to no longer be in
        `git worktree list`."""
        _run(["git", "worktree", "remove", "--force", str(path)], cwd=self.root)
        if path in self._extra_worktrees:
            self._extra_worktrees.remove(path)

    def head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, subject: str, trailers: dict[str, str] | None = None, filename: str | None = None) -> str:
        filename = filename or f"{subject.replace(' ', '_')}.txt"
        (self.root / filename).parent.mkdir(parents=True, exist_ok=True)
        (self.root / filename).write_text(subject + "\n")
        _run(["git", "add", filename], cwd=self.root)
        body = subject
        if trailers:
            body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
        _run(["git", "commit", "-q", "-m", body], cwd=self.root)
        return self.head()


class TestConfigFailSafe(unittest.TestCase):
    def test_missing_config_pre_activation_defaults_to_v1(self):
        with ScratchRepo() as repo:
            config = ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            self.assertEqual(config["default_workflow_version"], "1")

    def test_corrupt_config_pre_activation_defaults_to_v1(self):
        with ScratchRepo() as repo:
            (repo.root / "bad.json").write_text("{not json")
            config = ws.load_config(repo.root, config_path=Path("bad.json"))
            self.assertEqual(config["default_workflow_version"], "1")

    def test_missing_config_after_activation_is_a_hard_stop(self):
        with ScratchRepo() as repo:
            repo.commit("activate", trailers={"Workflow-Activation": "2.1"})
            with self.assertRaises(ws.ConfigMissingAfterActivationError):
                ws.load_config(repo.root, config_path=Path("nonexistent.json"))

    def test_rollback_after_activation_restores_pre_activation_behavior(self):
        with ScratchRepo() as repo:
            repo.commit("activate", trailers={"Workflow-Activation": "2.1"})
            repo.commit("rollback", trailers={"Workflow-Rollback": "2.1"})
            config = ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            self.assertEqual(config["default_workflow_version"], "1")

    def test_latest_event_wins_not_first(self):
        with ScratchRepo() as repo:
            repo.commit("activate", trailers={"Workflow-Activation": "2.1"})
            repo.commit("noop")
            self.assertTrue(ws.is_activated(repo.root))

    def test_present_valid_config_is_returned_as_is(self):
        with ScratchRepo() as repo:
            (repo.root / "config.json").write_text(json.dumps(ws.default_config()))
            config = ws.load_config(repo.root, config_path=Path("config.json"))
            self.assertEqual(config, ws.default_config())

    def test_default_workflow_version_must_be_in_supported_versions(self):
        with self.assertRaises(ws.CorruptJsonError):
            ws.validate_config({
                "schema_version": 1, "default_workflow_version": "3", "supported_versions": ["1", "2.1"],
            })


class TestWFActivateFourCombinations(unittest.TestCase):
    """Item 32: all four combinations of (config present/absent) x
    (activation trailer present/absent) behave per D-Self-Governance's
    documented rule -- config-present short-circuits regardless of
    trailer state (the trailer only matters for missing-config recovery),
    so the four combinations collapse to three distinct behaviors, all
    asserted here in one place explicitly owned by WF-Activate."""

    def test_config_present_trailer_present_returns_config_as_is(self):
        with ScratchRepo() as repo:
            repo.commit("activate", trailers={"Workflow-Activation": "2.1"})
            config = {"schema_version": 1, "default_workflow_version": "2.1", "supported_versions": ["1", "2.1"]}
            (repo.root / "config.json").write_text(json.dumps(config))
            loaded = ws.load_config(repo.root, config_path=Path("config.json"))
            self.assertEqual(loaded, config)

    def test_config_present_trailer_absent_returns_config_as_is(self):
        with ScratchRepo() as repo:
            config = ws.default_config()
            (repo.root / "config.json").write_text(json.dumps(config))
            loaded = ws.load_config(repo.root, config_path=Path("config.json"))
            self.assertEqual(loaded, config)

    def test_config_absent_trailer_present_is_a_hard_stop(self):
        with ScratchRepo() as repo:
            repo.commit("activate", trailers={"Workflow-Activation": "2.1"})
            with self.assertRaises(ws.ConfigMissingAfterActivationError):
                ws.load_config(repo.root, config_path=Path("nonexistent.json"))

    def test_config_absent_trailer_absent_defaults_to_v1(self):
        with ScratchRepo() as repo:
            config = ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            self.assertEqual(config["default_workflow_version"], "1")


class TestActivationRollbackTransforms(unittest.TestCase):
    def test_build_activated_config_flips_only_the_version_field(self):
        config = ws.default_config()
        activated = ws.build_activated_config(config)
        self.assertEqual(activated["default_workflow_version"], "2.1")
        self.assertEqual(activated["supported_versions"], config["supported_versions"])
        self.assertEqual(config["default_workflow_version"], "1")  # input untouched

    def test_build_activated_config_rejects_already_activated(self):
        config = {**ws.default_config(), "default_workflow_version": "2.1"}
        with self.assertRaises(ws.AlreadyActivatedError):
            ws.build_activated_config(config)

    def test_build_rolled_back_config_flips_back_to_v1(self):
        config = {**ws.default_config(), "default_workflow_version": "2.1"}
        rolled_back = ws.build_rolled_back_config(config)
        self.assertEqual(rolled_back["default_workflow_version"], "1")

    def test_build_rolled_back_config_rejects_not_activated(self):
        config = ws.default_config()
        with self.assertRaises(ws.NotActivatedError):
            ws.build_rolled_back_config(config)

    def test_activate_then_rollback_round_trips_to_original(self):
        config = ws.default_config()
        activated = ws.build_activated_config(config)
        rolled_back = ws.build_rolled_back_config(activated)
        self.assertEqual(rolled_back, config)


class TestCheckpointTrailerDiscovery(unittest.TestCase):
    def test_single_match_is_discovered(self):
        with ScratchRepo() as repo:
            sha = repo.commit("wf0", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"})
            discovered = ws.discover_checkpoint_commits(repo.root, "wi", repo.base)
            self.assertEqual(discovered, {"WF0": sha})

    def test_wrong_work_item_is_not_matched(self):
        with ScratchRepo() as repo:
            repo.commit("wf0", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "other"})
            discovered = ws.discover_checkpoint_commits(repo.root, "wi", repo.base)
            self.assertEqual(discovered, {})

    def test_reachable_completion_verifies(self):
        with ScratchRepo() as repo:
            sha = repo.commit("wf0", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"})
            work_item = {
                "work_item_id": "wi",
                "checkpoints": {"WF0": {"status": "COMPLETE", "start_commit": repo.base}},
            }
            ws.verify_checkpoint_completions(work_item, repo.root, repo.base)  # must not raise

    def test_unreachable_completion_raises(self):
        with ScratchRepo() as repo:
            work_item = {
                "work_item_id": "wi",
                "checkpoints": {"WF0": {"status": "COMPLETE", "start_commit": repo.base}},
            }
            with self.assertRaises(ws.CheckpointNotReachableError):
                ws.verify_checkpoint_completions(work_item, repo.root, repo.base)

    def test_duplicate_trailer_resolves_via_first_parent_tie_break(self):
        """A cherry-pick/rebase can copy a trailer onto a new commit while
        the original stays reachable (OPUS-R6-022) -- the first-parent
        ancestor of HEAD wins."""
        with ScratchRepo() as repo:
            # Simulate: an off-first-parent branch commit carries the
            # trailer, then a first-parent commit (e.g. a cherry-pick onto
            # main) carries the same trailer -- the first-parent one wins.
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            side_sha = repo.commit("wf0-side", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"})
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            main_sha = repo.commit("wf0-main", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"})
            _run(["git", "merge", "-q", "--no-ff", "-m", "merge side", "side"], cwd=repo.root)
            discovered = ws.discover_checkpoint_commits(repo.root, "wi", repo.base)
            self.assertEqual(discovered, {"WF0": main_sha})
            self.assertNotEqual(discovered["WF0"], side_sha)

    def test_genuine_ambiguity_raises(self):
        """Two first-parent-ancestor commits both carrying the same
        trailer pair is genuine ambiguity only once the role-specific
        verification tie-break (item 39) also fails to pick a single
        survivor -- here neither commit's own committed
        `WORKFLOW_STATE.json` claims `WF0` as `COMPLETE` (neither commit
        touches that file at all), so zero candidates verify and the
        result stays undecidable, not silently resolved."""
        with ScratchRepo() as repo:
            repo.commit("wf0-a", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"})
            repo.commit("wf0-b", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"})
            with self.assertRaises(ws.AmbiguousCheckpointTrailerError):
                ws.discover_checkpoint_commits(repo.root, "wi", repo.base)

    def test_verification_tie_break_resolves_when_one_candidate_claims_complete(self):
        """Item 39's real requirement: two first-parent-ancestor commits
        carry the same checkpoint trailer, but only one's own committed
        `WORKFLOW_STATE.json` records that checkpoint `COMPLETE` for this
        work item -- that candidate resolves, the other (which claims a
        different status) is rejected by the verification filter."""
        with ScratchRepo() as repo:
            _commit_state_with_trailers(
                repo, _state_json(wi={"checkpoints": {"WF0": {"status": "IN_PROGRESS"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
            )
            complete_sha = _commit_state_with_trailers(
                repo, _state_json(wi={"checkpoints": {"WF0": {"status": "COMPLETE"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
            )
            discovered = ws.discover_checkpoint_commits(repo.root, "wi", repo.base)
            self.assertEqual(discovered, {"WF0": complete_sha})

    def test_verification_tie_break_still_refuses_when_both_candidates_claim_complete(self):
        """Genuine ambiguity persists when *both* first-parent-ancestor
        candidates' own committed state claims `COMPLETE` -- verification
        narrows, it does not manufacture a winner out of two equally
        plausible survivors."""
        with ScratchRepo() as repo:
            _commit_state_with_trailers(
                repo, _state_json_marked("a", wi={"checkpoints": {"WF0": {"status": "COMPLETE"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
            )
            _commit_state_with_trailers(
                repo, _state_json_marked("b", wi={"checkpoints": {"WF0": {"status": "COMPLETE"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
            )
            with self.assertRaises(ws.AmbiguousCheckpointTrailerError):
                ws.discover_checkpoint_commits(repo.root, "wi", repo.base)

    def test_verification_tie_break_refuses_when_zero_candidates_claim_complete(self):
        """Zero valid survivors after verification is still refused, not
        treated as "no opinion, pick the first one"."""
        with ScratchRepo() as repo:
            _commit_state_with_trailers(
                repo, _state_json_marked("a", wi={"checkpoints": {"WF0": {"status": "IN_PROGRESS"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
            )
            _commit_state_with_trailers(
                repo, _state_json_marked("b", wi={"checkpoints": {"WF0": {"status": "IN_PROGRESS"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
            )
            with self.assertRaises(ws.AmbiguousCheckpointTrailerError):
                ws.discover_checkpoint_commits(repo.root, "wi", repo.base)

    def test_duplicate_trailer_resolves_via_first_parent_before_verification(self):
        """The two-filter contract's ordering: when the first-parent
        filter alone already narrows to one candidate, verification is
        never consulted -- a cherry-picked/duplicate off-first-parent
        trailer resolves exactly as before, even if the winning commit's
        own committed state does not (yet) claim `COMPLETE`."""
        with ScratchRepo() as repo:
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            _commit_state_with_trailers(
                repo, _state_json(wi={"checkpoints": {"WF0": {"status": "COMPLETE"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
                message="wf0-side",
            )
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            main_sha = _commit_state_with_trailers(
                repo, _state_json(wi={"checkpoints": {"WF0": {"status": "IN_PROGRESS"}}}),
                trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"},
                message="wf0-main",
            )
            # `-X ours`: both branches add the same WORKFLOW_STATE.json
            # path with different content (no common-ancestor version to
            # three-way-merge), so the merge itself needs a resolution
            # strategy -- irrelevant to what this test is proving, which
            # is purely about trailer/commit tie-breaking, not merge
            # content.
            _run(["git", "merge", "-q", "--no-ff", "-X", "ours", "-m", "merge side", "side"], cwd=repo.root)
            discovered = ws.discover_checkpoint_commits(repo.root, "wi", repo.base)
            self.assertEqual(discovered, {"WF0": main_sha})


STATE_REL_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"


def _commit_state(repo: "ScratchRepo", content: str, message: str = "state") -> str:
    """Commits arbitrary bytes at the real `WORKFLOW_STATE.json` path
    inside a `ScratchRepo`, so `checkpoint_origination_provable`'s
    per-commit partition can be exercised against realistic nested-path
    history rather than `ScratchRepo.commit`'s flat scratch files."""
    full = repo.root / STATE_REL_PATH
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _run(["git", "add", STATE_REL_PATH], cwd=repo.root)
    _run(["git", "commit", "-q", "-m", message], cwd=repo.root)
    return repo.head()


def _commit_state_with_trailers(
    repo: "ScratchRepo", content: str, trailers: dict[str, str], message: str = "state",
) -> str:
    """Like `_commit_state`, but the commit also carries the given Git
    trailers -- the shape a real checkpoint commit takes (it both advances
    `WORKFLOW_STATE.json` and carries `Workflow-Checkpoint`/
    `Workflow-Work-Item` trailers in the same commit), used by the
    checkpoint-trailer-discovery verification tie-break tests (item 39)."""
    full = repo.root / STATE_REL_PATH
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _run(["git", "add", STATE_REL_PATH], cwd=repo.root)
    body = message + "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
    return repo.head()


def _state_json(**work_items) -> str:
    return json.dumps({"schema_version": 1, "work_items": work_items})


def _state_json_marked(marker: str, **work_items) -> str:
    """Like `_state_json`, plus an inert top-level marker field -- used
    where two commits must carry deliberately identical checkpoint status
    (to exercise the verification tie-break's "both claim it" or "neither
    claims it" branches) but still need distinct blob content, since Git
    refuses an empty commit whose tree would be byte-identical to HEAD's."""
    return json.dumps({"schema_version": 1, "work_items": work_items, "_test_marker": marker})


def _write_local_state(repo: "ScratchRepo", content: str) -> None:
    """Writes `WORKFLOW_STATE.json` in the working tree only -- never
    committed -- the exact "uncommitted delta" shape a real interrupted
    checkpoint's fresh-start bookkeeping leaves (`transition_checkpoint_
    in_progress` is a working-tree-only write by design). Used to set up
    `adopt_claim`'s local-`IN_PROGRESS` precondition without polluting the
    origination reference `_commit_state` feeds."""
    full = repo.root / STATE_REL_PATH
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


class TestCheckpointOriginationReference(unittest.TestCase):
    """WF8b's `D-Checkpoint-Ownership` origination-reference slice
    (`checkpoint_origination_provable`/`origination_reference_commits`),
    against the plan's "The read fails closed, and the partition is
    total" table and its reduction/precedence rules."""

    def test_no_reference_commits_admits(self):
        with ScratchRepo() as repo:
            result = ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(result, {
                "decision": "admit", "route": "no_reference_commits",
                "state_rel_path": STATE_REL_PATH, "examined_commits": 0,
            })

    def test_absent_from_every_examined_commit_admits(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json())  # no "wi" entry at all
            result = ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(result["decision"], "admit")
            self.assertEqual(result["route"], "scanned_all_decidable")

    def test_decidable_complete_status_admits(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}}))
            result = ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(result["decision"], "admit")

    def test_observed_in_progress_refuses(self):
        with ScratchRepo() as repo:
            sha = _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "observed")
            self.assertEqual(ctx.exception.evidence["commit"], sha)

    def test_reduction_rule_any_observation_anywhere_binds(self):
        """No supersession, no recency: an `IN_PROGRESS` observed at an
        earlier commit still refuses even though a later commit in the
        same reference records `COMPLETE`."""
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}}))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "observed")

    def test_undecidable_unparseable_document_refuses(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not json")
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "undecidable")

    def test_undecidable_non_object_document_refuses(self):
        with ScratchRepo() as repo:
            _commit_state(repo, json.dumps([1, 2, 3]))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "undecidable")

    def test_present_but_non_object_work_items_refuses_undecidable(self):
        """A present `work_items: null` is a schema-invalid document, not
        an absence -- absence is a missing key, established with `in`,
        never a falsy or non-object value."""
        with ScratchRepo() as repo:
            _commit_state(repo, json.dumps({"schema_version": 1, "work_items": None}))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "undecidable")

    def test_present_but_non_object_checkpoint_entry_refuses_undecidable(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": None}}))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "undecidable")

    def test_status_outside_controlled_vocabulary_refuses_undecidable(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "BOGUS"}}}))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "undecidable")

    def test_precedence_observed_wins_over_undecidable_in_same_reference(self):
        """`OPUS-R89-001`'s precedence rule: when both refusal routes hold
        in the same reference, the observed route is reported -- the
        decision is refuse either way, but diagnosis should point at the
        actionable fact rather than whichever commit `rev-list` happened
        to reach first."""
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json at all")
            sha = _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "observed")
            self.assertEqual(ctx.exception.evidence["commit"], sha)

    def test_symlinked_state_path_is_undecidable_not_absent(self):
        """A symlink at the state path is never followed -- it is a
        present-but-not-a-regular-file blob, refused as undecidable."""
        with ScratchRepo() as repo:
            full = repo.root / STATE_REL_PATH
            full.parent.mkdir(parents=True, exist_ok=True)
            (full.parent / "elsewhere.json").write_text(
                _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}})
            )
            full.symlink_to("elsewhere.json")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "symlink state"], cwd=repo.root)
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "undecidable")

    def test_full_history_retains_a_deleted_branchs_only_observation(self):
        """Reproduces `OPUS-R88-001`'s exact gap: a merge resolved
        `-s ours` produces a merge commit TREESAME to mainline for the
        state path, and once the side branch ref is deleted the only
        remaining path to the side commit is through the merge's second
        parent. A plain `git rev-list --all -- <path>` (Git's default
        History Simplification) prunes that parent entirely and misses
        the side commit's `IN_PROGRESS`; `--full-history` must not."""
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}}))
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            side_sha = _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            _run(["git", "merge", "-q", "-s", "ours", "-m", "merge side (ours)", "side"], cwd=repo.root)
            _run(["git", "branch", "-D", "side"], cwd=repo.root)

            # Control arm: the default, non-full-history walk really does
            # drop the side commit here -- proving this is the exact gap
            # --full-history exists to close, not a hypothetical one.
            pruned = subprocess.run(
                ["git", "rev-list", "--all", "--", STATE_REL_PATH],
                cwd=repo.root, check=True, capture_output=True, text=True,
            ).stdout.splitlines()
            self.assertNotIn(side_sha, pruned)

            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.checkpoint_origination_provable(repo.root, "wi", "CP")
            self.assertEqual(ctx.exception.evidence["route"], "observed")
            self.assertEqual(ctx.exception.evidence["commit"], side_sha)

    def test_origination_reference_commits_matches_rev_list(self):
        with ScratchRepo() as repo:
            sha = _commit_state(repo, _state_json())
            commits = ws.origination_reference_commits(repo.root, STATE_REL_PATH)
            self.assertEqual(commits, [sha])

    def test_unresolvable_reference_refuses(self):
        """A repository the `rev-list` invocation itself cannot run
        against (no `.git` at all) refuses -- an unavailable reference is
        never read as an empty one."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.origination_reference_commits(Path(tmp), STATE_REL_PATH)
            self.assertEqual(ctx.exception.evidence["route"], "reference_unresolvable")


class TestIdentityReferenceReadPartition(unittest.TestCase):
    """WFR-66's identity-query enforcement: the read partition
    (`_identity_query_at_commit`/`_scan_identity_reference`), disjoint
    from and stated separately from the origination test's own partition
    though both scan the same reference (`OPUS-R90-003`)."""

    def test_no_reference_commits_admits(self):
        with ScratchRepo() as repo:
            result = ws._scan_identity_reference(repo.root, "wi")
            self.assertEqual(result["decision"], "admit")
            self.assertEqual(result["route"], "no_reference_commits")

    def test_work_item_id_never_observed_admits(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json())  # empty work_items
            result = ws._scan_identity_reference(repo.root, "wi")
            self.assertEqual(result["decision"], "admit")
            self.assertEqual(result["route"], "scanned_all_decidable")

    def test_work_item_id_key_present_is_observed_whatever_its_value(self):
        """Key presence at the queried level is the whole of what an
        existence query asks -- a `null` value still refuses, which is
        exactly where this partition diverges from the origination
        table's own (there, `null` is undecidable)."""
        with ScratchRepo() as repo:
            sha = _commit_state(repo, json.dumps({"schema_version": 1, "work_items": {"wi": None}}))
            result = ws._scan_identity_reference(repo.root, "wi")
            self.assertEqual(result["decision"], "observed")
            self.assertEqual(result["commit"], sha)

    def test_checkpoint_pair_key_present_is_observed_whatever_its_value(self):
        with ScratchRepo() as repo:
            sha = _commit_state(repo, _state_json(wi={"checkpoints": {"CP": None}}))
            result = ws._scan_identity_reference(repo.root, "wi", "CP")
            self.assertEqual(result["decision"], "observed")
            self.assertEqual(result["commit"], sha)

    def test_checkpoint_pair_absent_when_only_a_different_checkpoint_present(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"OTHER": {"status": "COMPLETE"}}}))
            result = ws._scan_identity_reference(repo.root, "wi", "CP")
            self.assertEqual(result["decision"], "admit")

    def test_checkpoint_pair_absent_when_work_item_id_itself_never_appears(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json())
            result = ws._scan_identity_reference(repo.root, "wi", "CP")
            self.assertEqual(result["decision"], "admit")

    def test_work_items_non_object_is_undecidable_for_work_item_query(self):
        with ScratchRepo() as repo:
            _commit_state(repo, json.dumps({"schema_version": 1, "work_items": 4}))
            with self.assertRaises(ws.IdentityReferenceUndecidableError) as ctx:
                ws._scan_identity_reference(repo.root, "wi")
            self.assertEqual(len(ctx.exception.evidence["undecidable_commits"]), 1)

    def test_work_item_entry_non_object_is_undecidable_for_pair_query(self):
        with ScratchRepo() as repo:
            _commit_state(repo, json.dumps({"schema_version": 1, "work_items": {"wi": 4}}))
            with self.assertRaises(ws.IdentityReferenceUndecidableError):
                ws._scan_identity_reference(repo.root, "wi", "CP")

    def test_checkpoints_non_object_is_undecidable_for_pair_query(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": 4}))
            with self.assertRaises(ws.IdentityReferenceUndecidableError):
                ws._scan_identity_reference(repo.root, "wi", "CP")

    def test_each_commit_judged_on_its_own_document_decidable_absence_does_not_mask_a_later_undecidable(self):
        """A commit that never mentions `wi` at all is decidably absent
        for the pair query; a *later* commit undecidable for the work
        item entry still raises, since the reduction rule requires every
        examined commit to be decidable before admitting."""
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json())  # no "wi" key: decidable absent
            _commit_state(repo, json.dumps({"schema_version": 1, "work_items": {"wi": 4}}))
            with self.assertRaises(ws.IdentityReferenceUndecidableError):
                ws._scan_identity_reference(repo.root, "wi", "CP")

    def test_unresolvable_reference_raises_identity_specific_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ws.IdentityReferenceUndecidableError) as ctx:
                ws._scan_identity_reference(Path(tmp), "wi")
            self.assertTrue(ctx.exception.evidence.get("reference_unresolvable"))

    def test_reduction_rule_observed_wins_over_undecidable_anywhere_in_scan(self):
        """Any observation anywhere binds, no supersession, no recency --
        and here the two routes differ in whether an escape exists at
        all, so an observed commit's unescapable refusal wins over an
        undecidable commit found in the same scan regardless of order."""
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            sha = _commit_state(repo, json.dumps({"schema_version": 1, "work_items": {"wi": {}}}))
            result = ws._scan_identity_reference(repo.root, "wi")
            self.assertEqual(result["decision"], "observed")
            self.assertEqual(result["commit"], sha)

    def test_full_scan_collects_every_undecidable_commit_not_only_the_first(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json 1", message="bad1")
            _commit_state(repo, "{not valid json 2", message="bad2")
            with self.assertRaises(ws.IdentityReferenceUndecidableError) as ctx:
                ws._scan_identity_reference(repo.root, "wi")
            self.assertEqual(len(ctx.exception.evidence["undecidable_commits"]), 2)


class TestGapObservationIdAndLiteral(unittest.TestCase):
    def test_digest_is_deterministic_and_order_independent(self):
        evidence_a = {
            "work_item_id": "wi", "checkpoint_id": None, "reference_unresolvable": False,
            "undecidable_commits": [{"commit": "b", "failure_class": "x"}, {"commit": "a", "failure_class": "y"}],
        }
        evidence_b = {
            "work_item_id": "wi", "checkpoint_id": None, "reference_unresolvable": False,
            "undecidable_commits": [{"commit": "a", "failure_class": "y"}, {"commit": "b", "failure_class": "x"}],
        }
        self.assertEqual(ws.gap_observation_id(evidence_a), ws.gap_observation_id(evidence_b))

    def test_digest_changes_with_identity(self):
        base = {"work_item_id": "wi", "checkpoint_id": None, "reference_unresolvable": False,
                "undecidable_commits": [{"commit": "a", "failure_class": "x"}]}
        other = {**base, "work_item_id": "other"}
        self.assertNotEqual(ws.gap_observation_id(base), ws.gap_observation_id(other))
        other_cp = {**base, "checkpoint_id": "CP"}
        self.assertNotEqual(ws.gap_observation_id(base), ws.gap_observation_id(other_cp))

    def test_digest_changes_with_undecidable_set(self):
        base = {"work_item_id": "wi", "checkpoint_id": None, "reference_unresolvable": False,
                "undecidable_commits": [{"commit": "a", "failure_class": "x"}]}
        changed = {**base, "undecidable_commits": [{"commit": "a", "failure_class": "different"}]}
        self.assertNotEqual(ws.gap_observation_id(base), ws.gap_observation_id(changed))

    def test_literal_carries_checkpoint_id_only_for_pair_query(self):
        evidence = {"work_item_id": "wi", "checkpoint_id": None, "reference_unresolvable": False,
                    "undecidable_commits": []}
        literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
        self.assertTrue(literal.startswith("authorize identity reference gap wi gap "))
        pair_evidence = {**evidence, "checkpoint_id": "CP"}
        pair_literal = ws.identity_reference_gap_authorization_literal("wi", "CP", pair_evidence)
        self.assertIn("checkpoint CP", pair_literal)


class TestAuthorizeIdentityReferenceGap(unittest.TestCase):
    def _undecidable_evidence(self, repo, work_item_id="wi", checkpoint_id=None):
        with self.assertRaises(ws.IdentityReferenceUndecidableError) as ctx:
            ws._scan_identity_reference(repo.root, work_item_id, checkpoint_id)
        return ctx.exception.evidence

    def test_wrong_literal_refused(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            evidence = self._undecidable_evidence(repo)
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.authorize_identity_reference_gap(
                    repo.root, "wi", now="t", user_authorization="wrong", evidence=evidence)

    def test_evidence_identity_mismatch_refused(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            evidence = self._undecidable_evidence(repo)
            literal = ws.identity_reference_gap_authorization_literal("other", None, evidence)
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.authorize_identity_reference_gap(
                    repo.root, "other", now="t", user_authorization=literal, evidence=evidence)

    def test_happy_path_publishes_a_durable_record(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            evidence = self._undecidable_evidence(repo)
            literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
            record = ws.authorize_identity_reference_gap(
                repo.root, "wi", now="t1", user_authorization=literal, evidence=evidence)
            self.assertEqual(record["work_item_id"], "wi")
            self.assertIsNone(record["checkpoint_id"])
            self.assertFalse(record["consumed"])
            path = ws.identity_gap_authorization_path(repo.root, ws.gap_observation_id(evidence))
            self.assertTrue(path.exists())

    def test_idempotent_second_call_recognises_existing_record_rather_than_erroring(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            evidence = self._undecidable_evidence(repo)
            literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
            first = ws.authorize_identity_reference_gap(
                repo.root, "wi", now="t1", user_authorization=literal, evidence=evidence)
            second = ws.authorize_identity_reference_gap(
                repo.root, "wi", now="t2", user_authorization=literal, evidence=evidence)
            self.assertEqual(first, second)  # t1 preserved -- not re-authorized

    def test_stale_evidence_refused_when_reference_changed(self):
        """A commit repaired (or a new undecidable one added) between the
        evidence and the authorization changes the digest -- refused,
        having recorded nothing."""
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            evidence = self._undecidable_evidence(repo)
            literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
            _commit_state(repo, "{also not valid json", message="second bad commit")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.authorize_identity_reference_gap(
                    repo.root, "wi", now="t", user_authorization=literal, evidence=evidence)

    def test_refused_when_identity_now_decidably_observed(self):
        """Once a *second* commit decidably observes "wi", the reduction
        rule makes the whole scan resolve to "observed" rather than
        "undecidable" (an observed commit always wins) -- so a fresh
        evidence-independent re-scan hits the "now decidably observed"
        branch, never the digest-mismatch one."""
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            evidence = self._undecidable_evidence(repo)
            literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
            _commit_state(repo, _state_json(wi={}))
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError) as ctx:
                ws.authorize_identity_reference_gap(
                    repo.root, "wi", now="t", user_authorization=literal, evidence=evidence)
            self.assertIn("now decidably observed", str(ctx.exception))

    def test_refused_when_the_query_now_admits_on_its_own(self):
        """The undecidable commit becoming unreachable from every ref
        (ordinary history maintenance -- `reset --hard` past it here)
        flips the reference's own scan to "admit": no gap remains to
        authorize."""
        with ScratchRepo() as repo:
            before = repo.head()
            _commit_state(repo, "{not valid json")
            evidence = self._undecidable_evidence(repo)
            literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
            _run(["git", "reset", "--hard", before], cwd=repo.root)
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError) as ctx:
                ws.authorize_identity_reference_gap(
                    repo.root, "wi", now="t", user_authorization=literal, evidence=evidence)
            self.assertIn("no gap remains to authorize", str(ctx.exception))


class TestIdentityReferenceAdmits(unittest.TestCase):
    def test_decidable_absence_admits(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json())
            result = ws.identity_reference_admits(repo.root, "wi")
            self.assertEqual(result["decision"], "admit")

    def test_observed_work_item_id_raises_reused_error(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={}))
            with self.assertRaises(ws.WorkItemIdReusedError):
                ws.identity_reference_admits(repo.root, "wi")

    def test_observed_checkpoint_pair_raises_reused_error(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}}))
            with self.assertRaises(ws.CheckpointIdReusedError):
                ws.identity_reference_admits(repo.root, "wi", "CP")

    def test_undecidable_without_authorization_raises(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            with self.assertRaises(ws.IdentityReferenceUndecidableError):
                ws.identity_reference_admits(repo.root, "wi")

    def test_undecidable_with_matching_authorization_admits(self):
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            with self.assertRaises(ws.IdentityReferenceUndecidableError) as ctx:
                ws.identity_reference_admits(repo.root, "wi")
            evidence = ctx.exception.evidence
            literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
            ws.authorize_identity_reference_gap(
                repo.root, "wi", now="t", user_authorization=literal, evidence=evidence)
            result = ws.identity_reference_admits(repo.root, "wi")
            self.assertEqual(result["decision"], "admit")
            self.assertEqual(result["route"], "authorized_gap")

    def test_authorization_for_a_different_identity_does_not_admit_this_one(self):
        """The digest binds the identity -- an authorized gap for `wi`
        never admits `other`, even against byte-identical undecidable
        commits."""
        with ScratchRepo() as repo:
            _commit_state(repo, "{not valid json")
            with self.assertRaises(ws.IdentityReferenceUndecidableError) as ctx:
                ws.identity_reference_admits(repo.root, "wi")
            evidence = ctx.exception.evidence
            literal = ws.identity_reference_gap_authorization_literal("wi", None, evidence)
            ws.authorize_identity_reference_gap(
                repo.root, "wi", now="t", user_authorization=literal, evidence=evidence)
            with self.assertRaises(ws.IdentityReferenceUndecidableError):
                ws.identity_reference_admits(repo.root, "other")


class TestWorkItemCreationIdentityEnforcement(unittest.TestCase):
    def test_omitted_repo_root_skips_the_check(self):
        """Backward compatible: a caller that never supplies `repo_root`
        (the in-memory/testing convenience this function always had) gets
        the unchanged, repo-independent routing."""
        state = _base_state()
        config = ws.default_config()
        new_state = ws.route_work_item(
            state, config, work_item_id="wi", work_item_type="process",
            work_item_kind="process", plan_path="p", registry_path="r",
            plan_revision=1, now="t",
        )
        self.assertIn("wi", new_state["work_items"])

    def test_fresh_id_never_observed_is_created(self):
        with ScratchRepo() as repo:
            new_state = ws.route_work_item(
                _base_state(), ws.default_config(), work_item_id="wi", work_item_type="process",
                work_item_kind="process", plan_path="p", registry_path="r",
                plan_revision=1, now="t", repo_root=repo.root,
            )
            self.assertIn("wi", new_state["work_items"])

    def test_fresh_id_observed_in_history_is_refused(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={}))
            with self.assertRaises(ws.WorkItemIdReusedError):
                ws.route_work_item(
                    _base_state(), ws.default_config(), work_item_id="wi", work_item_type="process",
                    work_item_kind="process", plan_path="p", registry_path="r",
                    plan_revision=1, now="t", repo_root=repo.root,
                )

    def test_resuming_an_existing_entry_never_reaches_the_check(self):
        """The check applies only to the fresh-id branch -- a resume
        (the id already lives in `state`) is untouched, even for an id
        that also happens to be historically observed."""
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={}))
            state = _base_state(wi=_base_work_item())
            new_state = ws.route_work_item(
                state, ws.default_config(), work_item_id="wi", work_item_type="process",
                work_item_kind="process", plan_path="p", registry_path="r",
                plan_revision=2, now="t", repo_root=repo.root,
            )
            self.assertEqual(new_state["work_items"]["wi"]["plan_revision"], 2)


class TestRegistryCheckpointIdReuseEnforcement(unittest.TestCase):
    def _write(self, repo, checkpoint_ids):
        registry = ws.generate_registry("wi", 1, [
            {"id": cid, "name": cid, "depends_on": [], "complexity": 1, "session_target": "1"}
            for cid in checkpoint_ids
        ])
        mapping = ws.generate_mapping(
            "wi", {"R1": {"description": "d", "checkpoint_ids": checkpoint_ids}}, registry=registry,
        )
        ws.write_registry_and_mapping(repo.root, Path("reg.json"), Path("map.json"), registry, mapping)

    def test_first_write_of_a_never_observed_id_succeeds(self):
        with ScratchRepo() as repo:
            self._write(repo, ["A"])
            self.assertTrue((repo.root / "reg.json").exists())

    def test_new_id_observed_historically_for_this_work_item_is_refused(self):
        with ScratchRepo() as repo:
            _commit_state(repo, _state_json(wi={"checkpoints": {"RETIRED": {"status": "COMPLETE"}}}))
            with self.assertRaises(ws.CheckpointIdReusedError):
                self._write(repo, ["RETIRED"])

    def test_id_kept_live_across_revisions_is_in_place_redefinition_not_reuse(self):
        """A checkpoint id present in the registry currently on disk is
        never re-checked on a later write, even if it is (as it always
        will be, once any checkpoint starts) observed in committed
        history -- in-place redefinition must stay legal."""
        with ScratchRepo() as repo:
            self._write(repo, ["A"])
            _commit_state(repo, _state_json(wi={"checkpoints": {"A": {"status": "IN_PROGRESS"}}}))
            self._write(repo, ["A", "B"])  # "A" unchanged, "B" genuinely new
            registry = json.loads((repo.root / "reg.json").read_text())
            self.assertEqual([c["id"] for c in registry["checkpoints"]], ["A", "B"])


def _base_work_item(**overrides) -> dict:
    work_item = {
        "work_item_type": "process",
        "work_item_kind": "process",
        "work_item_id": "wi",
        "parent_work_item_id": None,
        "governing_workflow_version": "1",
        "phase": "IMPLEMENTING",
        "checkpoints": {},
        "plan_review_stages": None,
    }
    work_item.update(overrides)
    return work_item


def _base_state(**work_items) -> dict:
    return {"schema_version": 1, "active_work_item_id": None, "work_items": work_items}


class TestStateValidation(unittest.TestCase):
    def test_minimal_valid_state_passes(self):
        ws.validate_state(_base_state(wi=_base_work_item()))

    def _valid_plan_approval(self):
        return ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan", user_confirmation="approve wi plan",
            now="t0", reviewed_bundle_id="b", approved_review_content_id="c",
            review_content_manifest=[{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
        )

    def _valid_technical_approval(self):
        return ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="implementation",
            user_confirmation="approve wi implementation", now="t0",
            reviewed_bundle_id="b", approved_review_content_id="c",
            review_content_manifest=[{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
            reviewed_content_commit="deadbeef",
        )

    def test_valid_persisted_plan_approval_passes_state_validation(self):
        wi = _base_work_item(plan_approval=self._valid_plan_approval())
        ws.validate_state(_base_state(wi=wi))  # must not raise

    def test_valid_persisted_technical_approval_passes_state_validation(self):
        wi = _base_work_item(technical_approval=self._valid_technical_approval())
        ws.validate_state(_base_state(wi=wi))  # must not raise

    def test_malformed_persisted_plan_approval_manifest_rejected_by_state_validation(self):
        """I2 (`workflow-v2-3-followups` continued scope, external
        cross-model review round 2): `validate_approval_record`'s shape
        check protected only newly *constructed* records -- a malformed
        `plan_approval` already sitting in `WORKFLOW_STATE.json` (the
        exact shape that reached `/accept-milestone` undetected and
        crashed `_assert_registry_covered_by_current_plan_approval` with
        a bare `AttributeError`) must now be rejected cleanly by
        `validate_state` itself, before any consumer ever sees it. The
        malformed manifest here is the whole projection object
        `workflow_fingerprint.compute_review_content_id_plan_stage*`
        returns -- a dict, not the flat list every consumer expects --
        the exact historical shape, not a synthetic stand-in."""
        malformed = self._valid_plan_approval()
        malformed["review_content_manifest"] = {
            "stage": "plan", "work_item_type": "process", "work_item_id": "wi",
            "plan_revision": 1, "base_commit": "deadbeef", "reviewed_implementation_head": None,
            "review_content_manifest": [{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
            "protected_paths": ["x"], "excluded_paths": [], "excluded_prefixes": [],
        }
        wi = _base_work_item(plan_approval=malformed)
        with self.assertRaises(ws.InvalidApprovalRecordError) as ctx:
            ws.validate_state(_base_state(wi=wi))
        self.assertNotIsInstance(ctx.exception, AttributeError)

    def test_malformed_persisted_technical_approval_manifest_rejected_by_state_validation(self):
        """I2's technical_approval counterpart -- the second of the two
        approval records the original incident found malformed."""
        malformed = self._valid_technical_approval()
        malformed["review_content_manifest"] = {
            "stage": "implementation", "work_item_type": "process", "work_item_id": "wi",
            "base_commit": "deadbeef", "reviewed_implementation_head": None,
            "review_content_manifest": [{"path": "x", "exists": True, "mode": "100644", "blob": "y"}],
            "protected_paths": [], "protected_prefixes": ["src/"],
            "excluded_paths": [], "excluded_prefixes": [],
        }
        wi = _base_work_item(technical_approval=malformed)
        with self.assertRaises(ws.InvalidApprovalRecordError) as ctx:
            ws.validate_state(_base_state(wi=wi))
        self.assertNotIsInstance(ctx.exception, AttributeError)

    def test_unknown_phase_rejected(self):
        with self.assertRaises(ws.UnknownPhaseError):
            ws.validate_state(_base_state(wi=_base_work_item(phase="NOT_A_REAL_PHASE")))

    def test_work_item_id_key_mismatch_rejected(self):
        with self.assertRaises(ws.WorkItemIdKeyMismatchError):
            ws.validate_state(_base_state(wi=_base_work_item(work_item_id="different")))

    def test_active_work_item_id_naming_nonexistent_entry_rejected(self):
        state = _base_state(wi=_base_work_item())
        state["active_work_item_id"] = "nonexistent"
        with self.assertRaises(ws.ActiveWorkItemInvalidError):
            ws.validate_state(state)

    def test_active_work_item_id_naming_terminal_phase_rejected(self):
        state = _base_state(wi=_base_work_item(phase="MILESTONE_COMPLETE"))
        state["active_work_item_id"] = "wi"
        with self.assertRaises(ws.ActiveWorkItemInvalidError):
            ws.validate_state(state)

    def test_two_simultaneous_non_terminal_non_active_entries_accepted(self):
        """Missing-test item 19: LEGACY_READY alongside an active
        IMPLEMENTING item -- both non-terminal, only active_work_item_id
        naming a terminal-or-nonexistent entry is rejected."""
        state = _base_state(
            active=_base_work_item(work_item_id="active", phase="IMPLEMENTING"),
            legacy=_base_work_item(work_item_id="legacy", phase="LEGACY_READY"),
        )
        state["active_work_item_id"] = "active"
        ws.validate_state(state)  # must not raise

    def test_multiple_in_progress_checkpoints_rejected(self):
        wi = _base_work_item(checkpoints={
            "A": {"status": "IN_PROGRESS", "start_commit": "x"},
            "B": {"status": "IN_PROGRESS", "start_commit": "y"},
        })
        with self.assertRaises(ws.MultipleInProgressCheckpointsError):
            ws.validate_state(_base_state(wi=wi))

    def test_unknown_checkpoint_status_rejected(self):
        wi = _base_work_item(checkpoints={"A": {"status": "READY", "start_commit": "x"}})
        with self.assertRaises(ws.UnknownCheckpointStatusError):
            ws.validate_state(_base_state(wi=wi))

    def test_completion_while_dependency_incomplete_rejected(self):
        registry = {"checkpoints": [
            {"id": "A", "depends_on": []},
            {"id": "B", "depends_on": ["A"]},
        ]}
        wi = _base_work_item(checkpoints={"B": {"status": "COMPLETE", "start_commit": "x"}})
        with self.assertRaises(ws.CheckpointDependencyNotCompleteError):
            ws.validate_state(_base_state(wi=wi), registry=registry)

    def test_completion_with_dependency_complete_accepted(self):
        registry = {"checkpoints": [
            {"id": "A", "depends_on": []},
            {"id": "B", "depends_on": ["A"]},
        ]}
        wi = _base_work_item(checkpoints={
            "A": {"status": "COMPLETE", "start_commit": "x"},
            "B": {"status": "COMPLETE", "start_commit": "y"},
        })
        ws.validate_state(_base_state(wi=wi), registry=registry)  # must not raise

    def test_plan_revision_mirror_mismatch_rejected(self):
        # Missing-test item 147 (`OPUS-R25-002`/`-009`, `GPT-R30-004`):
        # WORKFLOW_STATE.json's own plan_revision field for a work item is
        # a non-authoritative mirror of the registry's own plan_revision --
        # a hand-edited or stale mirror must fail closed before any
        # fingerprint call runs, not silently diverge.
        registry = {"work_item_id": "wi", "plan_revision": 5, "checkpoints": []}
        wi = _base_work_item(plan_revision=4)
        with self.assertRaises(ws.PlanRevisionMirrorMismatchError):
            ws.validate_state(_base_state(wi=wi), registry=registry)

    def test_plan_revision_mirror_match_accepted(self):
        registry = {"work_item_id": "wi", "plan_revision": 5, "checkpoints": []}
        wi = _base_work_item(plan_revision=5)
        ws.validate_state(_base_state(wi=wi), registry=registry)  # must not raise

    def test_plan_revision_mirror_check_skipped_for_unrelated_registry(self):
        # A registry naming a work item absent from this state (e.g. a
        # different work item's registry passed by mistake, or a registry
        # with no `work_item_id` at all, matching every pre-existing test
        # above) must not raise -- there is nothing to mirror-check.
        registry = {"work_item_id": "some-other-item", "plan_revision": 999, "checkpoints": []}
        wi = _base_work_item(plan_revision=1)
        ws.validate_state(_base_state(wi=wi), registry=registry)  # must not raise

    def _init_git_repo(self, root: Path) -> None:
        """`validate_state`'s whole-state registry check now requires a
        resolved `registry_path` to be a Git-tracked file (`GPT-R33-002`),
        not merely present on disk -- every test below that expects its
        fixture registry to actually be read must run inside a real (if
        disposable) Git repository. `git add` alone (no commit, no
        configured identity) is sufficient to make a path satisfy `git
        ls-files --error-unmatch`."""
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    def _write_registry_file(
        self, root: Path, rel_path: str, *, work_item_id: str, plan_revision: int, track: bool = True,
    ) -> None:
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(json.dumps({
            "work_item_id": work_item_id, "plan_revision": plan_revision, "checkpoints": [],
        }))
        if track:
            subprocess.run(["git", "add", rel_path], cwd=root, check=True)

    def test_repo_root_check_passes_when_every_applicable_items_mirror_matches(self):
        # Missing-test requirement (GPT-R31-003): two process work items,
        # each with its own registry_path and matching mirror.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=3)
            self._write_registry_file(root, "registry/b.json", work_item_id="b", plan_revision=7)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
                b=_base_work_item(work_item_id="b", registry_path="registry/b.json", plan_revision=7),
            )
            ws.validate_state(state, repo_root=root)  # must not raise

    def test_repo_root_check_rejects_either_items_mismatch_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=3)
            self._write_registry_file(root, "registry/b.json", work_item_id="b", plan_revision=7)

            # item-a's own mirror disagrees; item-b's agrees.
            state_a_bad = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=999),
                b=_base_work_item(work_item_id="b", registry_path="registry/b.json", plan_revision=7),
            )
            with self.assertRaises(ws.PlanRevisionMirrorMismatchError):
                ws.validate_state(state_a_bad, repo_root=root)

            # item-a's own mirror agrees; item-b's disagrees -- proves the
            # check is not vacuously passing just because *some* item
            # matches.
            state_b_bad = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
                b=_base_work_item(work_item_id="b", registry_path="registry/b.json", plan_revision=999),
            )
            with self.assertRaises(ws.PlanRevisionMirrorMismatchError):
                ws.validate_state(state_b_bad, repo_root=root)

    def test_repo_root_check_fails_closed_on_missing_registry_file_not_silently(self):
        # "omitted registry coverage fails rather than silently skipping
        # the check" (GPT-R31-003's own required test).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/does-not-exist.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_exempts_null_registry_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path=None, plan_revision=1),
            )
            ws.validate_state(state, repo_root=root)  # must not raise

    def test_repo_root_check_catches_a_different_items_stale_mirror_even_when_registry_param_names_another(self):
        """The exact failure scenario GPT-R31-003 describes: passing only
        `registry=` (core's own) lets a *different* item's stale mirror
        through; passing `repo_root=` too must catch it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/core.json", work_item_id="core", plan_revision=21)
            self._write_registry_file(root, "registry/second.json", work_item_id="second", plan_revision=5)
            state = _base_state(
                core=_base_work_item(work_item_id="core", registry_path="registry/core.json", plan_revision=21),
                second=_base_work_item(work_item_id="second", registry_path="registry/second.json", plan_revision=999),
            )
            core_registry = {"work_item_id": "core", "plan_revision": 21, "checkpoints": []}
            ws.validate_state(state, registry=core_registry)  # passes -- the exact defect GPT-R31-003 flagged
            with self.assertRaises(ws.PlanRevisionMirrorMismatchError):
                ws.validate_state(state, registry=core_registry, repo_root=root)

    # -- GPT-R32-001: whole-state registry_path resolution must go through
    # the shared safe-path resolver, not a bare `repo_root / registry_path`
    # join. --

    def test_repo_root_check_rejects_absolute_registry_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=3)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="/etc/passwd", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_dot_dot_traversal_registry_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / "foreign.json"
            outside.write_text(json.dumps({"work_item_id": "a", "plan_revision": 3, "checkpoints": []}))
            try:
                (root / "registry").mkdir(parents=True, exist_ok=True)
                state = _base_state(
                    a=_base_work_item(
                        work_item_id="a", registry_path="registry/../../foreign.json", plan_revision=3,
                    ),
                )
                with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                    ws.validate_state(state, repo_root=root)
            finally:
                outside.unlink()

    def test_repo_root_check_rejects_symlinked_registry_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/real.json", work_item_id="a", plan_revision=3)
            link = root / "registry" / "a.json"
            link.symlink_to(root / "registry" / "real.json")
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    # -- GPT-R33-001: a symlink at an *intermediate* path component must
    # be rejected exactly like a symlinked final component -- checking
    # only `Path.is_symlink()` on the fully joined path missed a symlinked
    # parent directory entirely. --

    def test_repo_root_check_rejects_intermediate_symlinked_directory_component_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            outside = root.parent / "outside_registry_target"
            try:
                outside.mkdir(parents=True, exist_ok=True)
                (outside / "item.json").write_text(json.dumps({
                    "work_item_id": "a", "plan_revision": 3, "checkpoints": [],
                }))
                # "registry" is not a symlink to a file -- it is a symlink
                # to an entire directory *outside* the repository. The
                # joined path `registry/item.json` is itself a real
                # regular file, not a symlink, so a final-component-only
                # check passes it straight through.
                (root / "registry").symlink_to(outside, target_is_directory=True)
                state = _base_state(
                    a=_base_work_item(work_item_id="a", registry_path="registry/item.json", plan_revision=3),
                )
                with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                    ws.validate_state(state, repo_root=root)
            finally:
                (root / "registry").unlink()
                (outside / "item.json").unlink()
                outside.rmdir()

    def test_repo_root_check_rejects_intermediate_symlinked_directory_component_inside_repo(self):
        # The rule is "no symlink component", full stop -- not "no symlink
        # component that happens to escape the repository". A symlinked
        # directory that merely aliases another location *inside* the
        # repository must be rejected too.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "real_registry/item.json", work_item_id="a", plan_revision=3)
            (root / "registry").symlink_to(root / "real_registry", target_is_directory=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/item.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_nested_symlink_chain(self):
        # The symlinked component is not the first one -- proves every
        # component is checked as the path is walked, not only the head
        # or the tail.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "real_registry/item.json", work_item_id="a", plan_revision=3)
            (root / "dir1").mkdir()
            (root / "dir1" / "link2").symlink_to(root / "real_registry", target_is_directory=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="dir1/link2/item.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_accepts_legitimate_nested_path_with_no_symlinks(self):
        # Regression guard for the two tests above: a real (non-symlinked)
        # multi-component path must still validate cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "a/b/c/item.json", work_item_id="a", plan_revision=3)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="a/b/c/item.json", plan_revision=3),
            )
            ws.validate_state(state, repo_root=root)  # must not raise

    def test_repo_root_check_rejects_non_regular_file_registry_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "registry").mkdir(parents=True, exist_ok=True)
            state = _base_state(
                # "registry" itself is a directory, not a regular file.
                a=_base_work_item(work_item_id="a", registry_path="registry", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    # -- GPT-R32-002: a registry with no valid plan_revision must fail
    # closed, not be silently treated as "nothing to compare". --

    def test_repo_root_check_rejects_registry_missing_plan_revision_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            full = root / "registry" / "a.json"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(json.dumps({"work_item_id": "a", "checkpoints": []}))
            subprocess.run(["git", "add", "registry/a.json"], cwd=root, check=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.InvalidRegistryPlanRevisionError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_registry_with_null_plan_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            full = root / "registry" / "a.json"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(json.dumps({"work_item_id": "a", "plan_revision": None, "checkpoints": []}))
            subprocess.run(["git", "add", "registry/a.json"], cwd=root, check=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.InvalidRegistryPlanRevisionError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_registry_with_string_plan_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            full = root / "registry" / "a.json"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(json.dumps({"work_item_id": "a", "plan_revision": "3", "checkpoints": []}))
            subprocess.run(["git", "add", "registry/a.json"], cwd=root, check=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.InvalidRegistryPlanRevisionError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_registry_with_boolean_plan_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            full = root / "registry" / "a.json"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(json.dumps({"work_item_id": "a", "plan_revision": True, "checkpoints": []}))
            subprocess.run(["git", "add", "registry/a.json"], cwd=root, check=True)
            state = _base_state(
                # bool is an int subclass in Python -- must still be rejected.
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=1),
            )
            with self.assertRaises(ws.InvalidRegistryPlanRevisionError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_registry_with_out_of_range_plan_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=0)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=0),
            )
            with self.assertRaises(ws.InvalidRegistryPlanRevisionError):
                ws.validate_state(state, repo_root=root)

    # -- GPT-R32-003: the loaded registry must declare the exact state
    # work-item ID -- cross-wiring two items must not pass just because
    # their revision numbers happen to agree. --

    def test_repo_root_check_rejects_cross_wired_registry_with_matching_revision(self):
        # Distinct registry_path values per item (so the pre-existing
        # write-time duplicate-path check does not itself catch this), but
        # item b's own registry file was hand-edited/copied and its content
        # still declares work_item_id "a" -- the exact "validate one
        # artifact while trusting another identity" defect class.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=21)
            self._write_registry_file(root, "registry/b.json", work_item_id="a", plan_revision=21)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=21),
                b=_base_work_item(work_item_id="b", registry_path="registry/b.json", plan_revision=21),
            )
            with self.assertRaises(fingerprint.RegistryWorkItemIdMismatchError):
                ws.validate_state(state, repo_root=root)

    # -- GPT-R33-002: the whole-state registry check must require a
    # *Git-tracked* regular file, not merely a readable one -- otherwise
    # an untracked JSON file dropped anywhere in the worktree can become
    # the authoritative comparison source, invisible to commits, review
    # bundles, fresh sessions, and approval provenance. --

    def test_repo_root_check_accepts_tracked_registry_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=3, track=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            ws.validate_state(state, repo_root=root)  # must not raise

    def test_repo_root_check_rejects_untracked_registry_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            # An otherwise-valid registry file (right shape, right
            # content, right location) that was simply never `git add`ed.
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=3, track=False)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_registry_path_removed_from_index_but_left_in_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            self._write_registry_file(root, "registry/a.json", work_item_id="a", plan_revision=3)
            # Staged and tracked a moment ago; now untracked again while
            # the file itself remains on disk unchanged -- the exact
            # split-brain scenario GPT-R33-002 describes (worktree
            # validates, committed repository does not).
            subprocess.run(["git", "rm", "--cached", "-q", "registry/a.json"], cwd=root, check=True)
            self.assertTrue((root / "registry" / "a.json").is_file())
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    # -- GPT-R33-004: malformed or wrong-shape registry JSON must fail
    # through this check's own named, work-item-specific error, not a raw
    # JSONDecodeError/AttributeError. --

    def test_repo_root_check_rejects_malformed_json_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            full = root / "registry" / "a.json"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("{not valid json")
            subprocess.run(["git", "add", "registry/a.json"], cwd=root, check=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_json_array_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            full = root / "registry" / "a.json"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(json.dumps(["a", "plan_revision", 3]))
            subprocess.run(["git", "add", "registry/a.json"], cwd=root, check=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_repo_root_check_rejects_scalar_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            full = root / "registry" / "a.json"
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(json.dumps(3))
            subprocess.run(["git", "add", "registry/a.json"], cwd=root, check=True)
            state = _base_state(
                a=_base_work_item(work_item_id="a", registry_path="registry/a.json", plan_revision=3),
            )
            with self.assertRaises(ws.MissingRegistryForPlanRevisionMirrorCheckError):
                ws.validate_state(state, repo_root=root)

    def test_corrupt_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json")
            with self.assertRaises(ws.CorruptJsonError):
                ws._load_json(path)


class TestWF8bDryRunEntrySetup(unittest.TestCase):
    """WF8b's entry step: `default_work_item` builds the synthetic
    `v2-1-dry-run` item (the grammar-compliant canonicalization of the
    plan's literal `v2.1-dry-run` prose -- see
    TestWF8bSyntheticWorkItemIdCanonicalization in
    workflow_fingerprint_test.py for the ID-grammar half of that
    decision), and the resulting multi-item state -- the real process
    item still present, non-active, exactly as WF8b's entry step leaves
    it -- validates cleanly."""

    def _dry_run_item(self, **overrides):
        item = ws.default_work_item(
            work_item_id="v2-1-dry-run", work_item_type="process",
            work_item_kind="synthetic", plan_path="docs/ai-workflow/dry-run/v2-1-dry-run-plan.md",
            registry_path=None, governing_workflow_version="2.1",
            plan_revision=1, last_transition="2026-07-31T19:25:00+01:00",
        )
        item.update(overrides)
        return item

    def test_synthetic_item_type_is_process_per_opus_r10_007(self):
        item = self._dry_run_item()
        self.assertEqual(item["work_item_type"], "process")
        self.assertEqual(item["work_item_kind"], "synthetic")

    def test_active_pointer_repointed_to_synthetic_item_validates(self):
        """Mirrors the real WF8b entry commit's shape: the prior active
        process item (`workflow-v2-1-core`) stays present and unmodified,
        non-active, while `active_work_item_id` repoints at the synthetic
        item -- D1's "resume-focus pointer, not an execution lock"."""
        state = _base_state(
            **{
                "workflow-v2-1-core": _base_work_item(
                    work_item_id="workflow-v2-1-core", phase="IMPLEMENTING",
                ),
                "v2-1-dry-run": self._dry_run_item(),
            }
        )
        state["active_work_item_id"] = "v2-1-dry-run"
        ws.validate_state(state)  # must not raise


class TestPlanReviewStages(unittest.TestCase):
    def test_non_null_on_v1_item_rejected(self):
        wi = _base_work_item(
            governing_workflow_version="1",
            plan_review_stages={"review_content_id": "x", "LOCAL_MODEL_PLAN_REVIEW": None, "MANUAL_EXTERNAL_PLAN_REVIEW": None},
        )
        with self.assertRaises(ws.PlanReviewStagesInvalidForVersionError):
            ws.validate_state(_base_state(wi=wi))

    def test_manual_without_local_rejected(self):
        wi = _base_work_item(
            governing_workflow_version="2.1",
            plan_review_stages={
                "review_content_id": "x",
                "LOCAL_MODEL_PLAN_REVIEW": None,
                "MANUAL_EXTERNAL_PLAN_REVIEW": {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
            },
        )
        with self.assertRaises(ws.ManualStageWithoutLocalStageError):
            ws.validate_state(_base_state(wi=wi))

    def test_non_approve_verdict_rejected(self):
        """Missing-test item 116 (GPT-R14-010)."""
        wi = _base_work_item(
            governing_workflow_version="2.1",
            plan_review_stages={
                "review_content_id": "x",
                "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b", "verdict": "REVISE", "round": 1, "completed_at": "t"},
                "MANUAL_EXTERNAL_PLAN_REVIEW": None,
            },
        )
        with self.assertRaises(ws.StageVerdictNotApproveError):
            ws.validate_state(_base_state(wi=wi))

    def test_both_stages_approve_accepted(self):
        wi = _base_work_item(
            governing_workflow_version="2.1",
            plan_review_stages={
                "review_content_id": "x",
                "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
                "MANUAL_EXTERNAL_PLAN_REVIEW": {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"},
            },
        )
        ws.validate_state(_base_state(wi=wi))  # must not raise


class TestRegistryTopologicalOrder(unittest.TestCase):
    def test_valid_topological_order_accepted(self):
        registry = {"checkpoints": [{"id": "A", "depends_on": []}, {"id": "B", "depends_on": ["A"]}]}
        ws.validate_registry_topological_order(registry)  # must not raise

    def test_non_topological_order_rejected(self):
        """Missing-test items 29/73."""
        registry = {"checkpoints": [{"id": "B", "depends_on": ["A"]}, {"id": "A", "depends_on": []}]}
        with self.assertRaises(ws.NonTopologicalRegistryOrderError):
            ws.validate_registry_topological_order(registry)


class TestRegistryMappingCoverage(unittest.TestCase):
    def test_full_coverage_accepted(self):
        registry = {"checkpoints": [{"id": "A"}, {"id": "B"}]}
        mapping = {"requirements": {
            "R1": {"checkpoint_ids": ["A"]},
            "R2": {"checkpoint_ids": ["B"]},
        }}
        ws.validate_registry_mapping_coverage(registry, mapping)  # must not raise

    def test_unmapped_requirement_rejected(self):
        registry = {"checkpoints": [{"id": "A"}]}
        mapping = {"requirements": {"R1": {"checkpoint_ids": ["NOT_A_CHECKPOINT"]}}}
        with self.assertRaises(ws.UnmappedRequirementError):
            ws.validate_registry_mapping_coverage(registry, mapping)

    def test_unowned_checkpoint_rejected(self):
        registry = {"checkpoints": [{"id": "A"}, {"id": "B"}]}
        mapping = {"requirements": {"R1": {"checkpoint_ids": ["A"]}}}
        with self.assertRaises(ws.UnownedCheckpointError):
            ws.validate_registry_mapping_coverage(registry, mapping)


class TestWorktreeIdentitySchema(unittest.TestCase):
    def test_valid_document_accepted(self):
        ws.validate_worktree_identity({
            "repo_root": "/r", "git_common_dir": "/r/.git", "worktree_root": "/r",
            "expected_dirty_paths_by_work_item": {"wi": [{"path": "a", "sha256": "b"}]},
            "generated_at": "t",
        })  # must not raise

    def test_missing_field_rejected(self):
        with self.assertRaises(ws.CorruptJsonError):
            ws.validate_worktree_identity({"repo_root": "/r"})

    def test_malformed_dirty_path_entry_rejected(self):
        with self.assertRaises(ws.CorruptJsonError):
            ws.validate_worktree_identity({
                "repo_root": "/r", "git_common_dir": "/r/.git", "worktree_root": "/r",
                "expected_dirty_paths_by_work_item": {"wi": [{"path": "a"}]},
                "generated_at": "t",
            })


class TestGoverningVersion(unittest.TestCase):
    def test_supported_version_accepted(self):
        ws.validate_governing_version("2.1", ws.default_config())  # must not raise

    def test_unsupported_version_rejected(self):
        """Resolves OPUS-R6-024 (optional), missing-test item 41."""
        with self.assertRaises(ws.UnsupportedGoverningVersionError):
            ws.validate_governing_version("3", ws.default_config())


class TestWorkItemRouting(unittest.TestCase):
    def test_fresh_id_creates_entry_and_claims_active_pointer(self):
        state = _base_state()
        config = ws.default_config()
        new_state = ws.route_work_item(
            state, config, work_item_id="milestone-9", work_item_type="product",
            work_item_kind="product", plan_path="p", registry_path="r",
            plan_revision=1, now="t1",
        )
        entry = new_state["work_items"]["milestone-9"]
        self.assertEqual(entry["phase"], "PLANNING")
        self.assertEqual(entry["governing_workflow_version"], config["default_workflow_version"])
        self.assertEqual(entry["plan_revision"], 1)
        self.assertEqual(entry["state_revision"], 1)
        self.assertEqual(new_state["active_work_item_id"], "milestone-9")
        # input untouched
        self.assertEqual(state, _base_state())

    def test_resuming_existing_non_terminal_entry_only_advances_revision_fields(self):
        state = _base_state(wi=_base_work_item(governing_workflow_version="2.1"))
        config = ws.default_config()
        new_state = ws.route_work_item(
            state, config, work_item_id="wi", work_item_type="process",
            work_item_kind="process", plan_path="p", registry_path="r",
            plan_revision=2, now="t2",
        )
        entry = new_state["work_items"]["wi"]
        self.assertEqual(entry["plan_revision"], 2)
        self.assertEqual(entry["state_revision"], 2)
        self.assertEqual(entry["last_transition"], "t2")
        # identity fields are immutable across a resume
        self.assertEqual(entry["governing_workflow_version"], "2.1")

    def test_routing_a_terminal_id_is_rejected(self):
        state = _base_state(wi=_base_work_item(phase="MILESTONE_COMPLETE"))
        config = ws.default_config()
        with self.assertRaises(ws.WorkItemTerminalReuseError):
            ws.route_work_item(
                state, config, work_item_id="wi", work_item_type="process",
                work_item_kind="process", plan_path="p", registry_path="r",
                plan_revision=1, now="t",
            )

    def test_routing_never_steals_focus_from_a_different_active_item(self):
        state = _base_state(
            active=_base_work_item(work_item_id="active", phase="IMPLEMENTING"),
            legacy=_base_work_item(work_item_id="legacy", phase="LEGACY_READY"),
        )
        state["active_work_item_id"] = "active"
        config = ws.default_config()
        new_state = ws.route_work_item(
            state, config, work_item_id="legacy", work_item_type="process",
            work_item_kind="process", plan_path="p", registry_path="r",
            plan_revision=2, now="t",
        )
        self.assertEqual(new_state["active_work_item_id"], "active")
        self.assertEqual(new_state["work_items"]["legacy"]["plan_revision"], 2)

    def test_invalid_work_item_id_rejected(self):
        config = ws.default_config()
        with self.assertRaises(ws.InvalidWorkItemIdError):
            ws.route_work_item(
                _base_state(), config, work_item_id="Not Valid!", work_item_type="process",
                work_item_kind="process", plan_path="p", registry_path="r",
                plan_revision=1, now="t",
            )

    def test_unsupported_governing_version_rejected_at_creation(self):
        config = {"schema_version": 1, "default_workflow_version": "3", "supported_versions": ["3"]}
        # "3" is supported by this deliberately-bogus config but not the
        # real default_config() -- exercise the opposite: config's own
        # default outside its own supported_versions is a config bug, not
        # this function's concern (validate_config catches that). Here we
        # confirm the *positive* path: creation succeeds when the default
        # is in supported_versions.
        new_state = ws.route_work_item(
            _base_state(), config, work_item_id="wi", work_item_type="process",
            work_item_kind="process", plan_path="p", registry_path="r",
            plan_revision=1, now="t",
        )
        self.assertEqual(new_state["work_items"]["wi"]["governing_workflow_version"], "3")


class TestPublishPlanRevision(unittest.TestCase):
    """`D-Plan-Revision-Publication`/`WFR-65`, missing-test item 371's
    hermetic half (sub-items a-c, e): the single sanctioned writer of a
    plan-revision bump into `WORKFLOW_STATE.json`'s non-authoritative
    mirror, which also performs the plan-review phase transition in the
    same operation."""

    def test_v1_governed_end_to_end_publication(self):
        """Item 371(a)/(d), v1 half: a governing-v1 continued-scope plan
        revision opened from `IMPLEMENTING` mirrors the registry's
        `plan_revision` immediately and enters
        `AWAITING_EXTERNAL_PLAN_REVIEW`, and the result passes
        `validate_state` against a registry declaring the same revision --
        no manual repair required."""
        wi = _base_work_item(
            governing_workflow_version="1", phase="IMPLEMENTING",
            plan_revision=62, state_revision=5, last_transition="t0",
        )
        state = _base_state(wi=wi)
        new_state = ws.publish_plan_revision(state, "wi", 63, "t1")
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["plan_revision"], 63)
        self.assertEqual(item["phase"], "AWAITING_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(item["state_revision"], 6)
        self.assertEqual(item["last_transition"], "t1")
        # input state untouched (every mutator in this module returns a
        # fresh dict rather than mutating its argument)
        self.assertEqual(state["work_items"]["wi"]["plan_revision"], 62)
        # the mirror now agrees with a registry declaring the same revision
        registry = {"work_item_id": "wi", "plan_revision": 63, "checkpoints": []}
        ws.validate_state(new_state, registry=registry)

    def test_v21_governed_enters_awaiting_local_plan_review(self):
        """Item 371(d): a `"2.1"`-governed item's target phase is
        `AWAITING_LOCAL_PLAN_REVIEW`, never `AWAITING_EXTERNAL_PLAN_REVIEW`
        -- `D-Plan-Review-Stages` always enters local review first."""
        wi = _v21_work_item(phase="REVISING_PLAN", plan_revision=4, state_revision=2)
        new_state = ws.publish_plan_revision(_base_state(wi=wi), "wi", 5, "t1")
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["plan_revision"], 5)
        self.assertEqual(item["phase"], "AWAITING_LOCAL_PLAN_REVIEW")

    def test_idempotent_retry_is_a_true_no_op(self):
        """Item 371(b): re-running with the same `plan_revision` and the
        resulting phase already reached is a no-op -- no `state_revision`/
        `last_transition` bump -- so an interrupted revision is retried
        rather than repaired."""
        wi = _base_work_item(
            governing_workflow_version="1", phase="AWAITING_EXTERNAL_PLAN_REVIEW",
            plan_revision=63, state_revision=6, last_transition="t1",
        )
        state = _base_state(wi=wi)
        result = ws.publish_plan_revision(state, "wi", 63, "t2")
        self.assertIs(result, state)
        self.assertEqual(result["work_items"]["wi"]["state_revision"], 6)
        self.assertEqual(result["work_items"]["wi"]["last_transition"], "t1")

    def test_touches_no_other_work_item(self):
        """Item 371(b): publication alters no other work item's entry."""
        wi = _base_work_item(governing_workflow_version="1", phase="IMPLEMENTING", plan_revision=1)
        other = _base_work_item(
            work_item_id="other", governing_workflow_version="2.1",
            phase="IMPLEMENTING", plan_revision=9, state_revision=3, last_transition="tX",
        )
        state = _base_state(wi=wi, other=other)
        new_state = ws.publish_plan_revision(state, "wi", 2, "t1")
        self.assertEqual(new_state["work_items"]["other"], other)

    def test_refuses_terminal_phase_item(self):
        """Item 371(c): refuses a terminal-phase item outright."""
        wi = _base_work_item(
            governing_workflow_version="1", phase="MILESTONE_COMPLETE", plan_revision=1,
        )
        with self.assertRaises(ws.TerminalPlanRevisionPublicationError):
            ws.publish_plan_revision(_base_state(wi=wi), "wi", 2, "t1")

    def test_unsupported_governing_version_rejected(self):
        wi = _base_work_item(governing_workflow_version="3", phase="IMPLEMENTING", plan_revision=1)
        with self.assertRaises(ws.UnsupportedGoverningVersionError):
            ws.publish_plan_revision(_base_state(wi=wi), "wi", 2, "t1")

    def test_written_through_d1_serialized_state_write_primitive(self):
        """Item 371(c): the exhaustive call sites write through
        `state_transaction` (`D1`'s serialized primitive), never as a
        plain JSON edit -- exercised here end to end against a real
        on-disk state file."""
        with ScratchRepo() as repo:
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            wi = _base_work_item(governing_workflow_version="1", phase="IMPLEMENTING", plan_revision=1)
            state_path.write_text(json.dumps(_base_state(wi=wi)))
            mutator = lambda state: ws.publish_plan_revision(state, "wi", 2, "t1")
            result = ws.state_transaction(repo.root, mutator, path=Path("docs/ai-workflow/WORKFLOW_STATE.json"))
            self.assertEqual(result["work_items"]["wi"]["plan_revision"], 2)
            on_disk = json.loads(state_path.read_text())
            self.assertEqual(on_disk["work_items"]["wi"]["plan_revision"], 2)
            self.assertEqual(on_disk["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_PLAN_REVIEW")


class TestWorkItemCompletion(unittest.TestCase):
    def test_completing_active_item_resets_pointer(self):
        state = _base_state(wi=_base_work_item(phase="AWAITING_USER_ACCEPTANCE"))
        state["active_work_item_id"] = "wi"
        new_state = ws.complete_work_item(state, "wi", now="t", repo_root=Path("."))
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "MILESTONE_COMPLETE")
        self.assertIsNone(new_state["active_work_item_id"])
        ws.validate_state(new_state)  # a completed, unpointed item is valid

    def test_completing_non_active_item_leaves_pointer_alone(self):
        state = _base_state(
            active=_base_work_item(work_item_id="active", phase="IMPLEMENTING"),
            other=_base_work_item(work_item_id="other", phase="AWAITING_USER_ACCEPTANCE"),
        )
        state["active_work_item_id"] = "active"
        new_state = ws.complete_work_item(state, "other", now="t", repo_root=Path("."))
        self.assertEqual(new_state["active_work_item_id"], "active")


class TestLegacyBranchReconciliation(unittest.TestCase):
    def test_reachable_ancestor_with_matching_substring_accepted(self):
        with ScratchRepo() as repo:
            (repo.root / "docs").mkdir()
            (repo.root / "docs/ACTIVE_MILESTONE.md").write_text("Milestone 8 accepted and closed.\n")
            _run(["git", "add", "docs/ACTIVE_MILESTONE.md"], cwd=repo.root)
            reviewed = repo.commit("reviewed head")
            ws.verify_legacy_branch_reconciliation(
                repo.root, reviewed_content_commit=reviewed,
                required_active_milestone_substring="Milestone 8 accepted",
            )  # must not raise

    def test_unreachable_reviewed_commit_rejected(self):
        with ScratchRepo() as repo:
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            side_sha = repo.commit("side work")
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            with self.assertRaises(ws.LegacyReconciliationError):
                ws.verify_legacy_branch_reconciliation(
                    repo.root, reviewed_content_commit=side_sha,
                    required_active_milestone_substring="anything",
                )

    def test_missing_expected_substring_rejected(self):
        with ScratchRepo() as repo:
            (repo.root / "docs").mkdir()
            (repo.root / "docs/ACTIVE_MILESTONE.md").write_text("still in progress\n")
            _run(["git", "add", "docs/ACTIVE_MILESTONE.md"], cwd=repo.root)
            reviewed = repo.commit("reviewed head")
            with self.assertRaises(ws.LegacyReconciliationError):
                ws.verify_legacy_branch_reconciliation(
                    repo.root, reviewed_content_commit=reviewed,
                    required_active_milestone_substring="accepted and closed",
                )

    def test_missing_active_milestone_file_rejected(self):
        with ScratchRepo() as repo:
            reviewed = repo.base
            with self.assertRaises(ws.LegacyReconciliationError):
                ws.verify_legacy_branch_reconciliation(
                    repo.root, reviewed_content_commit=reviewed,
                    required_active_milestone_substring="anything",
                )


class TestLegacyImport(unittest.TestCase):
    def _import(self, state=None, **overrides):
        kwargs = dict(
            work_item_id="milestone-8", plan_path="docs/milestones/completed/milestone-8-execution.md",
            registry_path=None, base_commit="base-sha", reviewed_content_commit="reviewed-sha",
            approved_review_content_id="backfilled-id", legacy_evidence={"rounds": 4},
            user_confirmation="legacy import confirmed for milestone-8 implementation", now="t1",
        )
        kwargs.update(overrides)
        return ws.import_legacy_work_item(state if state is not None else _base_state(), **kwargs)

    def test_import_creates_dormant_legacy_ready_entry(self):
        new_state = self._import()
        entry = new_state["work_items"]["milestone-8"]
        self.assertEqual(entry["phase"], "LEGACY_READY")
        self.assertEqual(entry["work_item_type"], "product")
        self.assertEqual(entry["work_item_kind"], "product")
        self.assertEqual(entry["governing_workflow_version"], "1")
        self.assertEqual(entry["base_commit"], "base-sha")
        self.assertIsNone(entry["plan_approval"])
        approval = entry["technical_approval"]
        self.assertEqual(approval["basis"], "LEGACY_V1")
        self.assertEqual(approval["status"], "CURRENT")
        self.assertIsNone(approval["reviewed_bundle_id"])
        self.assertEqual(approval["approved_review_content_id"], "backfilled-id")
        self.assertEqual(approval["reviewed_content_commit"], "reviewed-sha")
        self.assertEqual(approval["waived_guarantees"], ["no_bundle_id", "no_telemetry"])
        ws.validate_state(new_state)  # must not raise

    def test_import_never_claims_active_work_item_pointer(self):
        state = _base_state(active=_base_work_item(work_item_id="active", phase="IMPLEMENTING"))
        state["active_work_item_id"] = "active"
        new_state = self._import(state=state)
        self.assertEqual(new_state["active_work_item_id"], "active")

    def test_import_does_not_mutate_input_state(self):
        state = _base_state()
        self._import(state=state)
        self.assertEqual(state, _base_state())

    def test_reimporting_existing_id_rejected(self):
        new_state = self._import()
        with self.assertRaises(ws.LegacyImportAlreadyExistsError):
            self._import(state=new_state)


class TestLegacyPromotion(unittest.TestCase):
    """WF-M8b: D-Legacy phase 2 (`promote_legacy_work_item`). Uses its own
    scratch implementation-stage artifact-declarations file (never
    `workflow-v2-1-core`'s own `DEFAULT_ARTIFACTS_PATH`), exactly the
    "never silently reused across work items" discipline the function's
    own docstring states."""

    ARTIFACTS_REL = Path("artifacts.json")
    PROTECTED_PATH = "src/thing.txt"

    def _write_artifacts(self, repo):
        (repo.root / self.ARTIFACTS_REL).write_text(json.dumps({
            "schema_version": 2,
            "work_item_id": "milestone-8",
            "implementation_stage": {
                "protected_paths": {self.PROTECTED_PATH: "product code"},
                "protected_prefixes": {},
                "excluded_paths": {"artifacts.json": "declarations file"},
                "excluded_prefixes": {"docs/": "docs"},
            },
        }))
        _run(["git", "add", str(self.ARTIFACTS_REL)], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "add artifact declarations"], cwd=repo.root)

    def _seed_active_milestone(self, repo, text):
        (repo.root / "docs").mkdir(exist_ok=True)
        (repo.root / "docs/ACTIVE_MILESTONE.md").write_text(text)
        _run(["git", "add", "docs/ACTIVE_MILESTONE.md"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "seed active milestone doc"], cwd=repo.root)

    def _content_id_at(self, repo, commit):
        digest, _ = fingerprint.compute_review_content_id_implementation_stage_at_commit(
            repo.root, repo.base, commit, "product", "milestone-8",
            {self.PROTECTED_PATH: "product code"}, {},
            {"artifacts.json": "declarations file"}, {"docs/": "docs"},
        )
        return digest

    def _import(self, repo, *, active_milestone_text="Milestone 8 accepted and closed.\n"):
        self._write_artifacts(repo)
        self._seed_active_milestone(repo, active_milestone_text)
        (repo.root / "src").mkdir(exist_ok=True)
        (repo.root / self.PROTECTED_PATH).write_text("v1\n")
        _run(["git", "add", self.PROTECTED_PATH], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "reviewed head"], cwd=repo.root)
        reviewed_sha = repo.head()
        content_id = self._content_id_at(repo, reviewed_sha)
        state = ws.import_legacy_work_item(
            _base_state(), work_item_id="milestone-8",
            plan_path="docs/milestones/completed/milestone-8-execution.md", registry_path=None,
            base_commit=repo.base, reviewed_content_commit=reviewed_sha,
            approved_review_content_id=content_id, legacy_evidence={"rounds": 4},
            user_confirmation="legacy import confirmed for milestone-8 implementation", now="t1",
        )
        return state, reviewed_sha

    def _promote(self, state, repo, **overrides):
        kwargs = dict(
            work_item_id="milestone-8",
            required_active_milestone_substring="accepted and closed",
            artifacts_path=self.ARTIFACTS_REL, now="t2",
        )
        kwargs.update(overrides)
        return ws.promote_legacy_work_item(state, repo.root, **kwargs)

    def test_promotion_activates_and_transitions_version_and_phase(self):
        with ScratchRepo() as repo:
            state, _ = self._import(repo)
            new_state = self._promote(state, repo)
            entry = new_state["work_items"]["milestone-8"]
            self.assertEqual(new_state["active_work_item_id"], "milestone-8")
            self.assertEqual(entry["governing_workflow_version"], "2.1")
            self.assertEqual(entry["phase"], "AWAITING_FUNCTIONAL_REVIEW")
            # technical_approval itself is preserved exactly as imported.
            self.assertEqual(entry["technical_approval"]["basis"], "LEGACY_V1")
            self.assertEqual(entry["technical_approval"], state["work_items"]["milestone-8"]["technical_approval"])

    def test_promotion_does_not_mutate_input_state(self):
        with ScratchRepo() as repo:
            state, _ = self._import(repo)
            before = json.loads(json.dumps(state))
            self._promote(state, repo)
            self.assertEqual(state, before)

    def test_promotion_refused_when_not_legacy_ready(self):
        with ScratchRepo() as repo:
            state, _ = self._import(repo)
            already_active = self._promote(state, repo)
            with self.assertRaises(ws.LegacyAdoptionWrongPhaseError):
                self._promote(already_active, repo)

    def test_stale_technical_approval_blocks_promotion_not_reimport(self):
        with ScratchRepo() as repo:
            state, reviewed_sha = self._import(repo)
            # A protected path changes after import, before adoption.
            (repo.root / self.PROTECTED_PATH).write_text("v2 -- changed after import\n")
            _run(["git", "add", self.PROTECTED_PATH], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "post-import protected edit"], cwd=repo.root)
            with self.assertRaises(ws.LegacyAdoptionStaleApprovalError):
                self._promote(state, repo)
            # Still dormant -- never silently re-imported or promoted.
            self.assertEqual(state["work_items"]["milestone-8"]["phase"], "LEGACY_READY")

    def test_branch_reconciliation_recheck_failure_blocks_promotion(self):
        with ScratchRepo() as repo:
            state, _ = self._import(repo)
            (repo.root / "docs/ACTIVE_MILESTONE.md").write_text("still in progress\n")
            _run(["git", "add", "docs/ACTIVE_MILESTONE.md"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "regressed active milestone doc"], cwd=repo.root)
            with self.assertRaises(ws.LegacyReconciliationError):
                self._promote(state, repo)


class TestRegistryMappingGenerator(unittest.TestCase):
    def test_generate_registry_valid_order_accepted(self):
        registry = ws.generate_registry("wi", 1, [
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
            {"id": "B", "name": "b", "depends_on": ["A"], "complexity": 1, "session_target": "1"},
        ])
        self.assertEqual(registry["work_item_id"], "wi")
        self.assertEqual([c["id"] for c in registry["checkpoints"]], ["A", "B"])

    def test_generate_registry_bad_order_rejected(self):
        with self.assertRaises(ws.NonTopologicalRegistryOrderError):
            ws.generate_registry("wi", 1, [
                {"id": "B", "name": "b", "depends_on": ["A"], "complexity": 1, "session_target": "1"},
                {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
            ])

    def test_generate_mapping_full_coverage_accepted(self):
        registry = ws.generate_registry("wi", 1, [
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
        ])
        mapping = ws.generate_mapping("wi", {"R1": {"description": "d", "checkpoint_ids": ["A"]}}, registry=registry)
        self.assertEqual(mapping["work_item_id"], "wi")

    def test_generate_mapping_unowned_checkpoint_rejected(self):
        registry = ws.generate_registry("wi", 1, [
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
            {"id": "B", "name": "b", "depends_on": [], "complexity": 1, "session_target": "1"},
        ])
        with self.assertRaises(ws.UnownedCheckpointError):
            ws.generate_mapping("wi", {"R1": {"description": "d", "checkpoint_ids": ["A"]}}, registry=registry)

    def test_write_registry_and_mapping_round_trips(self):
        # A real Git repo (OPUS-R89-005's checkpoint-id-reuse check reads
        # D-Checkpoint-Ownership's origination reference, which needs one).
        with ScratchRepo() as repo:
            registry = ws.generate_registry("wi", 1, [
                {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
            ])
            mapping = ws.generate_mapping("wi", {"R1": {"description": "d", "checkpoint_ids": ["A"]}}, registry=registry)
            ws.write_registry_and_mapping(repo.root, Path("reg.json"), Path("map.json"), registry, mapping)
            self.assertEqual(json.loads((repo.root / "reg.json").read_text()), registry)
            self.assertEqual(json.loads((repo.root / "map.json").read_text()), mapping)

    def test_write_refuses_bad_coverage_even_if_caller_bypassed_generate(self):
        """Fail-closed guard: write_registry_and_mapping re-validates even
        against a hand-built (not generate_*-produced) argument pair."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = {"work_item_id": "wi", "checkpoints": [{"id": "A", "depends_on": []}]}
            mapping = {"work_item_id": "wi", "requirements": {}}
            with self.assertRaises(ws.UnownedCheckpointError):
                ws.write_registry_and_mapping(root, Path("reg.json"), Path("map.json"), registry, mapping)
            self.assertFalse((root / "reg.json").exists())

    def test_write_registry_and_mapping_creates_missing_parent_directories(self):
        """Salvage audit `O3`: `/milestone-plan` step 3 calls this writer
        before anything has necessarily created
        `docs/ai-workflow/registry/` or `docs/ai-workflow/requirements/`.
        A repository adopting the workflow for the first time used to get
        a bare `FileNotFoundError` out of the one step that is supposed to
        create these artifacts."""
        with ScratchRepo() as repo:
            registry_rel = Path("docs/ai-workflow/registry/wi-registry.json")
            mapping_rel = Path("docs/ai-workflow/requirements/wi-mapping.json")
            self.assertFalse((repo.root / registry_rel.parent).exists())
            self.assertFalse((repo.root / mapping_rel.parent).exists())
            registry = ws.generate_registry("wi", 1, [
                {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
            ])
            mapping = ws.generate_mapping(
                "wi", {"R1": {"description": "d", "checkpoint_ids": ["A"]}}, registry=registry,
            )
            ws.write_registry_and_mapping(repo.root, registry_rel, mapping_rel, registry, mapping)
            self.assertEqual(json.loads((repo.root / registry_rel).read_text()), registry)
            self.assertEqual(json.loads((repo.root / mapping_rel).read_text()), mapping)

    def test_non_string_work_item_kind_raises_the_documented_error(self):
        """Salvage audit `I2`, `work_item_kind` half: the three membership
        checks that used to be written inline (`default_work_item`,
        `route_work_item`, `_validate_work_item`) all read this value from
        an unvalidated source, so an unhashable JSON value produced a raw
        `TypeError` instead of `InvalidWorkItemTypeError`."""
        for value in (["process"], {"kind": "process"}, 3, True):
            with self.subTest(value=value):
                with self.assertRaises(ws.InvalidWorkItemTypeError):
                    ws.validate_work_item_kind(value)
        for value in ("process", "product", "synthetic"):
            ws.validate_work_item_kind(value)
        with self.assertRaises(ws.InvalidWorkItemTypeError):
            ws.validate_work_item_kind("gadget")

    def test_validate_state_refuses_a_non_string_work_item_kind(self):
        """The same guard reached through the real consumer: a persisted
        entry whose `work_item_kind` is not a string must be refused by
        name, never crash the validator."""
        work_item = ws.default_work_item(
            work_item_id="wi", work_item_type="process", work_item_kind="process",
            plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md", registry_path=None,
            governing_workflow_version="1", plan_revision=1, last_transition="t0",
        )
        work_item["work_item_kind"] = ["process"]
        state = {"schema_version": 1, "active_work_item_id": None, "work_items": {"wi": work_item}}
        with self.assertRaises(ws.InvalidWorkItemTypeError):
            ws.validate_state(state)

    def test_render_registry_markdown_is_a_pure_function_of_the_json(self):
        registry = ws.generate_registry("wi", 1, [
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
        ])
        self.assertEqual(ws.render_registry_markdown(registry), ws.render_registry_markdown(registry))
        self.assertIn("A", ws.render_registry_markdown(registry))

    def test_review_content_id_invariant_under_markdown_reformatting_sensitive_to_real_edit(self):
        """Missing-test item 35: regenerating the (unhashed) Markdown view
        never changes review_content_id; a real registry/mapping JSON edit
        does. Exercised end-to-end via workflow_fingerprint's own plan-
        stage identity function against a scratch repo."""
        import workflow_fingerprint as wf

        with ScratchRepo() as repo:
            registry_rel = Path("docs/ai-workflow/registry/wi-registry.json")
            mapping_rel = Path("docs/ai-workflow/requirements/wi-mapping.json")
            generated_md_rel = Path("docs/ai-workflow/registry/wi-registry.generated.md")
            (repo.root / registry_rel.parent).mkdir(parents=True, exist_ok=True)
            (repo.root / mapping_rel.parent).mkdir(parents=True, exist_ok=True)

            def write_round(checkpoint_name: str, markdown_note: str) -> str:
                registry = ws.generate_registry("wi", 1, [
                    {"id": "A", "name": checkpoint_name, "depends_on": [], "complexity": 1, "session_target": "1"},
                ])
                mapping = ws.generate_mapping(
                    "wi", {"R1": {"description": "d", "checkpoint_ids": ["A"]}}, registry=registry,
                )
                ws.write_registry_and_mapping(repo.root, registry_rel, mapping_rel, registry, mapping)
                # The Markdown view is a *generated, excluded* artifact
                # (D-Registry): only markdown_note changes here, never the
                # protected registry/mapping JSON bytes.
                (repo.root / generated_md_rel).write_text(
                    ws.render_registry_markdown(registry) + markdown_note + "\n"
                )
                _run(["git", "add", "."], cwd=repo.root)
                _run(["git", "commit", "-q", "-m", "round"], cwd=repo.root)
                review_content_id, _manifest = wf.compute_review_content_id_plan_stage(
                    repo.root, repo.base, "process", "wi", 1,
                    protected=frozenset({str(registry_rel), str(mapping_rel)}),
                    excluded_paths={str(generated_md_rel): "generated registry markdown view, never hashed"},
                    excluded_prefixes={},
                )
                return review_content_id

            id_round1 = write_round("checkpoint A", "cosmetic note one")
            id_round2 = write_round("checkpoint A", "totally different cosmetic note")
            self.assertEqual(id_round1, id_round2, "Markdown-only reformatting must not change review_content_id")

            # A real registry edit (renaming the checkpoint) must change it.
            id_round3 = write_round("checkpoint A renamed", "totally different cosmetic note")
            self.assertNotEqual(id_round2, id_round3, "a real registry edit must change review_content_id")


class TestApprovalGateReachability(unittest.TestCase):
    """WF4a-ii, D-States: non-circular entry -- reachable from the review
    round alone, never from plan_approval/technical_approval existing."""

    def test_approve_round_reaches_gate(self):
        self.assertTrue(ws.approval_gate_reachable("APPROVE"))

    def test_revise_round_with_findings_resolved_reaches_gate(self):
        """Missing-test items 12/27: a REVISE round with zero blocking
        findings left reaches the gate exactly as readily as APPROVE,
        with no approval record in existence."""
        self.assertTrue(ws.approval_gate_reachable("REVISE"))

    def test_block_never_reaches_gate(self):
        """Missing-test item 15."""
        self.assertFalse(ws.approval_gate_reachable("BLOCK"))

    def test_missing_feedback_never_reaches_gate(self):
        """Item 305 (`WF8c`): deleting, renaming, or otherwise making the
        current `BLOCK` feedback file unreadable yields no discoverable
        status (`None`), which must leave the gate unreachable exactly
        like a literal `BLOCK` -- a `BLOCK` cannot be converted into an
        override-eligible state merely by losing its feedback file."""
        self.assertFalse(ws.approval_gate_reachable(None))

    def test_technical_gate_blocked_by_dirty_protected_path(self):
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status="APPROVE", protected_path_dirty=True,
            head_matches_reviewed_implementation_head=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

    def test_technical_gate_blocked_by_head_mismatch(self):
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status="APPROVE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=False,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

    def test_technical_gate_reachable_when_clean_and_matching(self):
        self.assertTrue(ws.technical_approval_gate_reachable(
            latest_round_status="REVISE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

    def test_technical_gate_pinned_block_refuses_even_when_otherwise_reachable(self):
        """D2a (WF8c item (a)): a durable pin refuses the gate regardless
        of what latest_round_status/protected_path_dirty/head-matching say
        -- every other predicate here is deliberately set to its own
        "reachable" value to prove pinned_block alone is decisive."""
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status="APPROVE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True, pinned_block=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

    def test_technical_gate_pinned_block_defaults_false(self):
        """Backward compatible: omitting pinned_block behaves exactly as
        before D2a existed."""
        self.assertTrue(ws.technical_approval_gate_reachable(
            latest_round_status="REVISE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

    def test_technical_gate_missing_or_block_feedback_never_reaches_regardless_of_other_conditions(self):
        """Item 297 (`WF8c`): `test_technical_gate_reachable_when_clean_
        and_matching` above already proves a current `REVISE` reaches this
        gate exactly like `APPROVE` -- that is not a general relaxation.
        Feedback that is missing entirely, bound to a stale/non-current
        bundle, or unparseable all collapse to no discoverable
        `latest_round_status` (`None`, the same convention item 305's
        `approval_gate_reachable(None)` case uses), and a current `BLOCK`
        is its own distinct status -- both must still return `False` here
        even when every other predicate is at its own "reachable" value,
        confirming only current `REVISE`/`APPROVE` ever reach this gate."""
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status=None, protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))
        self.assertFalse(ws.technical_approval_gate_reachable(
            latest_round_status="BLOCK", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

    def test_technical_gate_reachable_for_first_pass_approve_round(self):
        """Item 296 (`WF8c`, `GPT-R52-003`): `test_technical_gate_reachable_
        when_clean_and_matching` above already proves a current `REVISE`
        reaches this gate; this is the symmetric `APPROVE` case, standing
        in for a first-pass round whose entire phase history never
        includes `APPLYING_REVIEW_FEEDBACK` at all (`P -> S`, bundle `B1`,
        matching `APPROVE`). `technical_approval_gate_reachable`'s own
        signature takes no phase-history argument -- only the three/four
        directly-checkable predicates already passed in -- so its result
        can only ever depend on those, never on how the work item arrived
        at its current round."""
        self.assertTrue(ws.technical_approval_gate_reachable(
            latest_round_status="APPROVE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True,
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        ))

    def test_v1_plan_gate_ignores_plan_review_stages(self):
        self.assertTrue(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="1",
            plan_review_stages=None, current_review_content_id="c1",
        ))

    def test_v2_1_plan_gate_requires_both_stages_current(self):
        """GPT-R11-001/-003: a single reviewed round is necessary but no
        longer sufficient for a "2.1" item."""
        self.assertFalse(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=None, current_review_content_id="c1",
        ))
        stages = {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_PLAN_REVIEW": {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"},
        }
        self.assertTrue(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=stages, current_review_content_id="c1",
        ))

    def test_v2_1_plan_gate_rejects_stale_review_content_id(self):
        stages = {
            "review_content_id": "stale",
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_PLAN_REVIEW": {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"},
        }
        self.assertFalse(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=stages, current_review_content_id="current",
        ))

    def test_v2_1_plan_gate_rejects_local_only(self):
        stages = {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_PLAN_REVIEW": None,
        }
        self.assertFalse(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=stages, current_review_content_id="c1",
        ))


class TestUserConfirmationGuard(unittest.TestCase):
    """WF4a-ii, D2's mechanism-independent guard, second control."""

    def test_empty_confirmation_rejected(self):
        """Missing-test item 26."""
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_user_confirmation("", work_item_id="wi", stage="plan")

    def test_whitespace_only_confirmation_rejected(self):
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_user_confirmation("   ", work_item_id="wi", stage="plan")

    def test_confirmation_naming_wrong_work_item_rejected(self):
        """Missing-test item 74."""
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_user_confirmation("I approve the plan for other-item", work_item_id="wi", stage="plan")

    def test_confirmation_naming_wrong_stage_rejected(self):
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_user_confirmation("I approve the implementation for wi", work_item_id="wi", stage="plan")

    def test_confirmation_naming_exact_item_and_stage_accepted(self):
        ws.validate_user_confirmation("I approve the plan for wi", work_item_id="wi", stage="plan")  # no raise

    def test_unknown_stage_rejected(self):
        with self.assertRaises(ws.InvalidApprovalRecordError):
            ws.validate_user_confirmation("I approve wi plan", work_item_id="wi", stage="not_a_stage")

    def test_legitimate_repeat_across_two_different_approvals_accepted(self):
        """Missing-test items 74/91 (GPT-R11-004): a correct confirmation
        is not a novelty check -- the same literal text legitimately
        repeated across two genuinely different approval calls is accepted
        both times, not rejected as "reused"."""
        ws.validate_user_confirmation("wi plan implementation go-ahead", work_item_id="wi", stage="plan")
        ws.validate_user_confirmation("wi plan implementation go-ahead", work_item_id="wi", stage="implementation")

    def test_acceptance_stage_supported_for_accept_milestone(self):
        ws.validate_user_confirmation("I accept the wi milestone acceptance", work_item_id="wi", stage="acceptance")

    def test_retired_scoped_remediation_stage_is_not_a_known_stage(self):
        """Ledger `I10`: `"scoped_remediation"` was `APPROVAL_STAGES`'
        fourth member, for `/accept-scoped-remediation` only. Retiring that
        command retires the stage keyword with it -- an operator can never
        be asked to type a confirmation for a stage no command consumes.
        `validate_user_confirmation` refuses the unknown stage outright,
        and the three surviving stages stay mutually non-interchangeable
        (`test_confirmation_naming_wrong_stage_rejected`'s property)."""
        self.assertEqual(ws.APPROVAL_STAGES, frozenset({"plan", "implementation", "acceptance"}))
        with self.assertRaises(ws.InvalidApprovalRecordError) as ctx:
            ws.validate_user_confirmation(
                "I confirm scoped_remediation for wi", work_item_id="wi", stage="scoped_remediation",
            )
        self.assertIn("unknown approval stage", str(ctx.exception))
        # The three surviving keywords remain pairwise non-interchangeable.
        for stage, other in (("acceptance", "plan"), ("plan", "implementation"),
                             ("implementation", "acceptance")):
            ws.validate_user_confirmation(f"I confirm {stage} for wi", work_item_id="wi", stage=stage)
            with self.assertRaises(ws.UserConfirmationRejectedError):
                ws.validate_user_confirmation(f"I confirm {stage} for wi", work_item_id="wi", stage=other)


class TestApprovalBasisResolution(unittest.TestCase):
    """WF4a-ii, D2's basis decision."""

    def test_matching_approve_feedback_yields_external_approve(self):
        """Missing-test item 13: writes EXTERNAL_APPROVE without a separate
        override-justification prompt -- the standard confirmation still
        satisfies the mechanism-independent guard."""
        basis = ws.resolve_approval_basis(
            latest_round_status="APPROVE", feedback_bundle_id="b1", current_bundle_id="b1",
            user_confirmation="approve wi plan", work_item_id="wi", stage="plan",
        )
        self.assertEqual(basis, "EXTERNAL_APPROVE")

    def test_mismatched_bundle_id_requires_override_text(self):
        """Missing-test item 14: without matching feedback, refuses to
        write until literal override text is supplied."""
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.resolve_approval_basis(
                latest_round_status="APPROVE", feedback_bundle_id="stale-bundle", current_bundle_id="b1",
                user_confirmation="", work_item_id="wi", stage="plan",
            )
        basis = ws.resolve_approval_basis(
            latest_round_status="APPROVE", feedback_bundle_id="stale-bundle", current_bundle_id="b1",
            user_confirmation="override wi plan", work_item_id="wi", stage="plan",
        )
        self.assertEqual(basis, "USER_OVERRIDE")

    def test_revise_round_requires_override_text(self):
        basis = ws.resolve_approval_basis(
            latest_round_status="REVISE", feedback_bundle_id="b1", current_bundle_id="b1",
            user_confirmation="override wi implementation", work_item_id="wi", stage="implementation",
        )
        self.assertEqual(basis, "USER_OVERRIDE")

    def test_revise_round_with_no_override_text_refuses_to_write(self):
        """Item 307 (`WF8c`): a current, bundle-matching, parse-valid
        `REVISE` feedback file with no override text supplied refuses --
        `resolve_approval_basis`'s mechanism-independent confirmation
        guard runs before the `APPROVE`/`REVISE` branch is even reached,
        so a matching bundle_id never substitutes for it the way it does
        for `EXTERNAL_APPROVE`."""
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.resolve_approval_basis(
                latest_round_status="REVISE", feedback_bundle_id="b1", current_bundle_id="b1",
                user_confirmation="", work_item_id="wi", stage="implementation",
            )

    def test_block_never_reaches_either_basis(self):
        """Missing-test item 15."""
        with self.assertRaises(ws.BlockCannotApproveError):
            ws.resolve_approval_basis(
                latest_round_status="BLOCK", feedback_bundle_id="b1", current_bundle_id="b1",
                user_confirmation="override wi plan", work_item_id="wi", stage="plan",
            )

    def test_pinned_block_refuses_a_stale_bundle_revise_laundering_the_current_pin(self):
        """D2a (WF8c item (a), OPUS-R102-002): the exact laundering path the
        review reproduced -- a stale-bundle REVISE round (feedback_bundle_id
        disagrees with current_bundle_id, so latest_round_status alone
        would only ever demand override text, never refuse outright) must
        still refuse outright when a durable pin exists for the *current*
        bundle_id, exactly as if latest_round_status were itself BLOCK."""
        with self.assertRaises(ws.BlockCannotApproveError):
            ws.resolve_approval_basis(
                latest_round_status="REVISE", feedback_bundle_id="STALE-BUNDLE-FROM-AN-OLD-ROUND",
                current_bundle_id="FRESH-BUNDLE-ID", user_confirmation="override wi implementation",
                work_item_id="wi", stage="implementation", pinned_block=True,
            )

    def test_pinned_block_refuses_even_with_a_matching_approve(self):
        """A pin outranks even a fresh, bundle-matching APPROVE round --
        once pinned, a bundle_id can never become override-eligible again
        by any means, only by a genuinely new bundle_id."""
        with self.assertRaises(ws.BlockCannotApproveError):
            ws.resolve_approval_basis(
                latest_round_status="APPROVE", feedback_bundle_id="b1", current_bundle_id="b1",
                user_confirmation="approve wi implementation", work_item_id="wi", stage="implementation",
                pinned_block=True,
            )

    def test_pinned_block_defaults_false(self):
        """Backward compatible: omitting pinned_block behaves exactly as
        before D2a existed."""
        basis = ws.resolve_approval_basis(
            latest_round_status="APPROVE", feedback_bundle_id="b1", current_bundle_id="b1",
            user_confirmation="approve wi plan", work_item_id="wi", stage="plan",
        )
        self.assertEqual(basis, "EXTERNAL_APPROVE")

    def test_pinned_block_refuses_even_with_no_feedback_file_at_all(self):
        """Item 309 (`WF8c`): feedback missing entirely (a work item never
        yet reviewed this round, `latest_round_status=None`) is refused
        identically to a literal `BLOCK` once a durable pin exists for the
        current bundle_id -- the pin check runs before `latest_round_status`
        is even consulted, so neither a stale/different bundle (item 306)
        nor a wholly absent one can become an override basis."""
        with self.assertRaises(ws.BlockCannotApproveError):
            ws.resolve_approval_basis(
                latest_round_status=None, feedback_bundle_id=None, current_bundle_id="b1",
                user_confirmation="override wi implementation", work_item_id="wi", stage="implementation",
                pinned_block=True,
            )


class TestTechnicalReviewBlockPins(unittest.TestCase):
    """D2a, WF8c item (a): the durable, append-only, permanent
    technical_review_block_pins ledger -- writer idempotency, the
    positive-membership predicate, shape validation, and the
    state_transaction-wired monotonicity enforcement."""

    def test_writer_appends_a_pin_with_the_expected_shape(self):
        state = _base_state(wi=_base_work_item())
        new_state = ws.record_technical_review_block_pin(
            state, "wi", bundle_id="b1", review_content_id="c1", now="t1",
        )
        pins = new_state["work_items"]["wi"]["technical_review_block_pins"]
        self.assertEqual(pins, [{"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"}])
        ws.validate_state(new_state)  # must not raise

    def test_writer_does_not_mutate_the_input_state(self):
        state = _base_state(wi=_base_work_item())
        ws.record_technical_review_block_pin(state, "wi", bundle_id="b1", review_content_id="c1", now="t1")
        self.assertNotIn("technical_review_block_pins", state["work_items"]["wi"])

    def test_writer_bumps_state_revision_and_last_transition(self):
        wi = _base_work_item(state_revision=4, last_transition="t0")
        state = _base_state(wi=wi)
        new_state = ws.record_technical_review_block_pin(
            state, "wi", bundle_id="b1", review_content_id="c1", now="t1",
        )
        self.assertEqual(new_state["work_items"]["wi"]["state_revision"], 5)
        self.assertEqual(new_state["work_items"]["wi"]["last_transition"], "t1")

    def test_writer_is_idempotent_on_repeat_observation(self):
        """A pin already present for the exact bundle_id is never
        duplicated -- the writer returns the identical state object so a
        caller can skip committing on a repeat observation."""
        state = _base_state(wi=_base_work_item())
        once = ws.record_technical_review_block_pin(state, "wi", bundle_id="b1", review_content_id="c1", now="t1")
        twice = ws.record_technical_review_block_pin(once, "wi", bundle_id="b1", review_content_id="c1", now="t2")
        self.assertIs(twice, once)
        self.assertEqual(len(twice["work_items"]["wi"]["technical_review_block_pins"]), 1)

    def test_writer_records_a_second_distinct_bundle_as_a_second_pin(self):
        state = _base_state(wi=_base_work_item())
        once = ws.record_technical_review_block_pin(state, "wi", bundle_id="b1", review_content_id="c1", now="t1")
        twice = ws.record_technical_review_block_pin(once, "wi", bundle_id="b2", review_content_id="c2", now="t2")
        self.assertEqual(
            [p["bundle_id"] for p in twice["work_items"]["wi"]["technical_review_block_pins"]], ["b1", "b2"],
        )

    def test_is_pinned_predicate(self):
        wi = _base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
        ])
        self.assertTrue(ws.is_technical_review_block_pinned(wi, "b1"))
        self.assertFalse(ws.is_technical_review_block_pinned(wi, "b2"))

    def test_is_pinned_predicate_absent_field(self):
        self.assertFalse(ws.is_technical_review_block_pinned(_base_work_item(), "b1"))

    def test_validate_state_rejects_non_list(self):
        wi = _base_work_item(technical_review_block_pins="not-a-list")
        with self.assertRaises(ws.CorruptJsonError):
            ws.validate_state(_base_state(wi=wi))

    def test_validate_state_rejects_malformed_entry_shape(self):
        wi = _base_work_item(technical_review_block_pins=[{"bundle_id": "b1"}])
        with self.assertRaises(ws.CorruptJsonError):
            ws.validate_state(_base_state(wi=wi))

    def test_validate_state_rejects_duplicate_bundle_id(self):
        wi = _base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
            {"bundle_id": "b1", "review_content_id": "c2", "recorded_at": "t2"},
        ])
        with self.assertRaises(ws.DuplicateTechnicalReviewBlockPinError):
            ws.validate_state(_base_state(wi=wi))

    def test_validate_state_accepts_a_well_formed_ledger(self):
        wi = _base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
            {"bundle_id": "b2", "review_content_id": "c2", "recorded_at": "t2"},
        ])
        ws.validate_state(_base_state(wi=wi))  # must not raise

    def test_monotonicity_allows_appending_exactly_one_new_pin(self):
        prev = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
        ]))
        new = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
            {"bundle_id": "b2", "review_content_id": "c2", "recorded_at": "t2"},
        ]))
        ws._assert_technical_review_block_pins_monotonic(prev, new)  # must not raise

    def test_monotonicity_allows_a_no_op_write(self):
        state = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
        ]))
        ws._assert_technical_review_block_pins_monotonic(state, state)  # must not raise

    def test_monotonicity_rejects_removing_an_existing_pin(self):
        prev = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
        ]))
        new = _base_state(wi=_base_work_item(technical_review_block_pins=[]))
        with self.assertRaises(ws.PinLedgerMonotonicityError):
            ws._assert_technical_review_block_pins_monotonic(prev, new)

    def test_monotonicity_rejects_mutating_an_existing_pin(self):
        """Closes GPT-R56-004's gap: a status-preserving-binding-preserving
        edit that changes only recorded_at (or any other field) of an
        already-committed pin must be rejected just as hard as a removal."""
        prev = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
        ]))
        new = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "TAMPERED"},
        ]))
        with self.assertRaises(ws.PinLedgerMonotonicityError):
            ws._assert_technical_review_block_pins_monotonic(prev, new)

    def test_monotonicity_rejects_appending_more_than_one_pin_in_one_write(self):
        prev = _base_state(wi=_base_work_item(technical_review_block_pins=[]))
        new = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
            {"bundle_id": "b2", "review_content_id": "c2", "recorded_at": "t2"},
        ]))
        with self.assertRaises(ws.PinLedgerMonotonicityError):
            ws._assert_technical_review_block_pins_monotonic(prev, new)

    def test_monotonicity_ignores_work_items_with_no_prior_pins(self):
        prev = _base_state(wi=_base_work_item())
        new = _base_state(wi=_base_work_item(technical_review_block_pins=[
            {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
        ]))
        ws._assert_technical_review_block_pins_monotonic(prev, new)  # must not raise

    def test_state_transaction_end_to_end_enforces_monotonicity(self):
        """Wired into state_transaction itself (D1's sole write choke
        point), not only into record_technical_review_block_pin's own call
        site -- a hand-written mutator that drops an existing pin is
        refused here too."""
        with ScratchRepo() as repo:
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            wi = _base_work_item(technical_review_block_pins=[
                {"bundle_id": "b1", "review_content_id": "c1", "recorded_at": "t1"},
            ])
            state_path.write_text(json.dumps(_base_state(wi=wi)))

            def drop_pin(state):
                new_state = copy.deepcopy(state)
                new_state["work_items"]["wi"]["technical_review_block_pins"] = []
                return new_state

            with self.assertRaises(ws.PinLedgerMonotonicityError):
                ws.state_transaction(repo.root, drop_pin, path=Path("docs/ai-workflow/WORKFLOW_STATE.json"))
            # Refused before publication -- the on-disk file still carries the original pin.
            on_disk = json.loads(state_path.read_text())
            self.assertEqual(len(on_disk["work_items"]["wi"]["technical_review_block_pins"]), 1)

    def test_state_transaction_end_to_end_allows_the_writer_through(self):
        with ScratchRepo() as repo:
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(_base_state(wi=_base_work_item())))
            mutator = lambda state: ws.record_technical_review_block_pin(
                state, "wi", bundle_id="b1", review_content_id="c1", now="t1",
            )
            result = ws.state_transaction(repo.root, mutator, path=Path("docs/ai-workflow/WORKFLOW_STATE.json"))
            self.assertEqual(len(result["work_items"]["wi"]["technical_review_block_pins"]), 1)
            on_disk = json.loads(state_path.read_text())
            self.assertEqual(len(on_disk["work_items"]["wi"]["technical_review_block_pins"]), 1)


class TestApprovalRecordShape(unittest.TestCase):
    """WF4a-ii, D2's record shape."""

    def test_plan_stage_forbids_reviewed_content_commit(self):
        with self.assertRaises(ws.InvalidApprovalRecordError):
            ws.validate_approval_record({
                "status": "CURRENT", "basis": "EXTERNAL_APPROVE",
                "reviewed_bundle_id": "b", "approved_review_content_id": "c",
                "review_content_manifest": [], "reviewed_content_commit": "deadbeef",
                "user_confirmation": "approve wi plan",
            }, stage="plan")

    def test_implementation_stage_allows_reviewed_content_commit(self):
        ws.validate_approval_record({
            "status": "CURRENT", "basis": "EXTERNAL_APPROVE",
            "reviewed_bundle_id": "b", "approved_review_content_id": "c",
            "review_content_manifest": [], "reviewed_content_commit": "deadbeef",
            "user_confirmation": "approve wi implementation",
        }, stage="implementation")  # must not raise

    def test_non_legacy_basis_requires_bundle_fields(self):
        with self.assertRaises(ws.InvalidApprovalRecordError):
            ws.validate_approval_record({
                "status": "CURRENT", "basis": "USER_OVERRIDE",
                "reviewed_bundle_id": None, "approved_review_content_id": None,
                "review_content_manifest": None, "reviewed_content_commit": None,
                "user_confirmation": "override wi plan",
            }, stage="plan")

    def test_legacy_v1_basis_allows_null_bundle_fields(self):
        ws.validate_approval_record({
            "status": "CURRENT", "basis": "LEGACY_V1",
            "reviewed_bundle_id": None, "approved_review_content_id": "backfilled",
            "review_content_manifest": None, "reviewed_content_commit": "deadbeef",
            "user_confirmation": "legacy import confirmed for wi",
            "legacy_evidence": {"note": "milestone-8"},
        }, stage="implementation")  # must not raise

    def test_unknown_waived_guarantee_rejected(self):
        with self.assertRaises(ws.InvalidApprovalRecordError):
            ws.validate_approval_record({
                "status": "CURRENT", "basis": "LEGACY_V1",
                "reviewed_bundle_id": None, "approved_review_content_id": "backfilled",
                "review_content_manifest": None, "reviewed_content_commit": "deadbeef",
                "user_confirmation": "legacy import confirmed for wi",
                "waived_guarantees": ["no_content_id"],
            }, stage="implementation")

    def test_build_approval_record_round_trips(self):
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan", user_confirmation="approve wi plan",
            now="t", reviewed_bundle_id="b", approved_review_content_id="c",
            review_content_manifest=[{"path": "x"}],
        )
        self.assertEqual(record["status"], "CURRENT")
        self.assertIsNone(record["reviewed_content_commit"])


class TestApprovalStateWrites(unittest.TestCase):
    """WF4a-ii, D-Approval-Commits' state-write step (commit creation is
    WF4a-iii's own concern, not exercised here)."""

    def test_apply_plan_approval_transitions_to_implementing(self):
        wi = _base_work_item(phase="AWAITING_PLAN_APPROVAL", state_revision=1)
        state = _base_state(wi=wi)
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan", user_confirmation="approve wi plan",
            now="t2", reviewed_bundle_id="b", approved_review_content_id="c",
            review_content_manifest=[],
        )
        new_state = ws.apply_plan_approval(state, "wi", record, now="t2")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "IMPLEMENTING")
        self.assertEqual(new_state["work_items"]["wi"]["plan_approval"], record)
        self.assertEqual(new_state["work_items"]["wi"]["state_revision"], 2)
        # Original state is untouched.
        self.assertIsNone(state["work_items"]["wi"].get("plan_approval"))

    def test_apply_technical_approval_transitions_to_awaiting_functional_review(self):
        wi = _base_work_item(phase="AWAITING_TECHNICAL_APPROVAL", state_revision=1)
        state = _base_state(wi=wi)
        record = ws.build_approval_record(
            basis="USER_OVERRIDE", stage="implementation", user_confirmation="override wi implementation",
            now="t2", reviewed_bundle_id="b", approved_review_content_id="c",
            review_content_manifest=[], reviewed_content_commit="deadbeef",
        )
        new_state = ws.apply_technical_approval(state, "wi", record, now="t2")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertEqual(new_state["work_items"]["wi"]["technical_approval"], record)


# ---------------------------------------------------------------------------
# WF4a-iii: approval-trailer discovery (extends D-Commit-Provenance beyond
# checkpoint trailers, resolves OPUS-R6-022 for approval commits too --
# missing-test item 39)
# ---------------------------------------------------------------------------


class TestApprovalTrailerDiscovery(unittest.TestCase):
    def test_single_plan_approval_match_is_discovered(self):
        with ScratchRepo() as repo:
            sha = repo.commit("approve-plan", trailers={
                "Workflow-Plan-Approval": "abc123", "Workflow-Work-Item": "wi",
            })
            discovered = ws.discover_approval_commits(repo.root, "Workflow-Plan-Approval", "wi", repo.base)
            self.assertEqual(discovered, {"abc123": sha})
            self.assertEqual(ws.discover_plan_approval_commit(repo.root, "wi", "abc123", repo.base), sha)

    def test_single_technical_approval_match_is_discovered(self):
        with ScratchRepo() as repo:
            sha = repo.commit("approve-impl", trailers={
                "Workflow-Technical-Approval": "def456", "Workflow-Work-Item": "wi",
            })
            self.assertEqual(ws.discover_technical_approval_commit(repo.root, "wi", "def456", repo.base), sha)

    def test_no_match_returns_none(self):
        with ScratchRepo() as repo:
            self.assertIsNone(ws.discover_plan_approval_commit(repo.root, "wi", "nonexistent", repo.base))

    def test_wrong_work_item_is_not_matched(self):
        with ScratchRepo() as repo:
            repo.commit("approve-plan", trailers={
                "Workflow-Plan-Approval": "abc123", "Workflow-Work-Item": "other",
            })
            self.assertIsNone(ws.discover_plan_approval_commit(repo.root, "wi", "abc123", repo.base))

    def test_duplicate_approval_trailer_resolves_via_first_parent_tie_break(self):
        """Same OPUS-R6-022 scenario as checkpoint trailers, extended to
        approval trailers: a cherry-pick/rebase can copy the trailer onto
        a new commit while the original stays reachable."""
        with ScratchRepo() as repo:
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            repo.commit("approve-side", trailers={"Workflow-Plan-Approval": "abc123", "Workflow-Work-Item": "wi"})
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            main_sha = repo.commit("approve-main", trailers={"Workflow-Plan-Approval": "abc123", "Workflow-Work-Item": "wi"})
            _run(["git", "merge", "-q", "--no-ff", "-m", "merge side", "side"], cwd=repo.root)
            self.assertEqual(ws.discover_plan_approval_commit(repo.root, "wi", "abc123", repo.base), main_sha)

    def test_genuine_ambiguity_raises(self):
        with ScratchRepo() as repo:
            repo.commit("approve-a", trailers={"Workflow-Technical-Approval": "abc123", "Workflow-Work-Item": "wi"})
            repo.commit("approve-b", trailers={"Workflow-Technical-Approval": "abc123", "Workflow-Work-Item": "wi"})
            with self.assertRaises(ws.AmbiguousApprovalTrailerError):
                ws.discover_approval_commits(repo.root, "Workflow-Technical-Approval", "wi", repo.base)


# ---------------------------------------------------------------------------
# WF4a-iii: per-stage approval-freshness rule and the full IMPLEMENTING
# entry condition (resolves OPUS-R6-003; missing-test items 9, 10, 11)
# ---------------------------------------------------------------------------


class TestApprovalFreshnessAndEntry(unittest.TestCase):
    """Uses the module's own real `PLAN_STAGE_PROTECTED`/
    `PLAN_STAGE_EXCLUDED_PREFIXES` defaults rather than a fabricated
    classification: the five real `workflow-v2-1-core` paths stand in as
    `"wi"`'s own declared plan/registry/mapping paths, and checkpoint-
    commit scaffolding lives under `scripts/` (a real excluded prefix), so
    the scenario matches this milestone's own actual classification.
    `D-Fingerprint-Generalization`: every approval-freshness function now
    routes through `resolve_plan_stage_metadata`, so `"wi"`'s own
    `WORKFLOW_STATE.json` entry and `wi-artifacts.json` declaration are
    committed alongside the plan docs, exactly mirroring the real
    repository's own layout for a second work item."""

    # One representative real protected path (the plan-stage manifest
    # requires every entry in PLAN_STAGE_PROTECTED to exist, fail-closed --
    # `_approve_plan` below writes placeholder content for all of them,
    # this is the one it later edits to simulate a post-approval plan
    # revision).
    PLAN_DOC = "docs/ai-workflow/WORKFLOW_V2_PLAN.md"
    REGISTRY_PATH = "docs/ai-workflow/registry/workflow-v2-1-core-registry.json"
    MAPPING_PATH = "docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json"
    ARTIFACTS_PATH = "docs/ai-workflow/registry/wi-artifacts.json"
    STATE_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"

    def _plan_doc_content(self, plan_revision, body="plan v1"):
        return f"# Plan (Revision {plan_revision})\n\n{body}\n"

    def _plan_stage_worktree_id(self, repo, plan_revision=1):
        # This class's own docstring already documents deliberately
        # reusing the module's real PLAN_STAGE_* defaults as "wi"'s own
        # fixture -- now passed explicitly since the low-level function no
        # longer defaults them itself (`GPT-R30-005`).
        digest, _ = fingerprint.compute_review_content_id_plan_stage(
            repo.root, repo.base, "process", "wi", plan_revision,
            fingerprint.PLAN_STAGE_PROTECTED,
            fingerprint.PLAN_STAGE_EXCLUDED_PATHS,
            fingerprint.PLAN_STAGE_EXCLUDED_PREFIXES,
        )
        return digest

    def _commit_at_path(self, repo, rel_path, content, subject, trailers=None):
        full = repo.root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        _run(["git", "add", rel_path], cwd=repo.root)
        body = subject
        if trailers:
            body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
        _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
        return repo.head()

    def _approve_plan(self, repo, plan_revision=1):
        for rel_path in fingerprint.PLAN_STAGE_PROTECTED:
            full = repo.root / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            if rel_path == self.PLAN_DOC:
                full.write_text(self._plan_doc_content(plan_revision))
            elif rel_path == self.REGISTRY_PATH:
                full.write_text(json.dumps({
                    "work_item_id": "wi", "plan_revision": plan_revision, "checkpoints": [],
                }))
            elif rel_path == self.MAPPING_PATH:
                full.write_text(json.dumps({"work_item_id": "wi", "requirements": {}}))
            elif not full.exists():
                full.write_text(f"placeholder for {rel_path}\n")
            _run(["git", "add", rel_path], cwd=repo.root)

        artifacts_full = repo.root / self.ARTIFACTS_PATH
        artifacts_full.parent.mkdir(parents=True, exist_ok=True)
        artifacts_full.write_text(json.dumps({
            "schema_version": 2,
            "work_item_id": "wi",
            "plan_stage": {
                "protected_paths": sorted(fingerprint.PLAN_STAGE_PROTECTED),
                "excluded_paths": dict(fingerprint.PLAN_STAGE_EXCLUDED_PATHS),
                "excluded_prefixes": dict(fingerprint.PLAN_STAGE_EXCLUDED_PREFIXES),
            },
        }))
        _run(["git", "add", self.ARTIFACTS_PATH], cwd=repo.root)

        state_full = repo.root / self.STATE_PATH
        state_full.parent.mkdir(parents=True, exist_ok=True)
        state_full.write_text(json.dumps({
            "schema_version": 1,
            "active_work_item_id": "wi",
            "work_items": {
                "wi": {
                    "work_item_id": "wi",
                    "work_item_type": "process",
                    "plan_path": self.PLAN_DOC,
                    "registry_path": self.REGISTRY_PATH,
                    "mapping_path": self.MAPPING_PATH,
                    "base_commit": repo.base,
                },
            },
        }))
        _run(["git", "add", self.STATE_PATH], cwd=repo.root)

        review_content_id = self._plan_stage_worktree_id(repo, plan_revision)
        _run(["git", "commit", "-q", "-m",
              "approve plan\n\nWorkflow-Plan-Approval: " + review_content_id + "\nWorkflow-Work-Item: wi"],
             cwd=repo.root)
        approval_sha = repo.head()
        work_item = {
            "work_item_id": "wi", "work_item_type": "process", "plan_revision": plan_revision,
            "implementation_revision": None,
            "plan_approval": {"status": "CURRENT", "approved_review_content_id": review_content_id},
        }
        return approval_sha, review_content_id, work_item

    def _commit_checkpoint(self, repo, n):
        return self._commit_at_path(
            repo, f"scripts/checkpoint_{n}.txt", f"checkpoint {n}\n", f"checkpoint {n}", trailers={
                "Workflow-Checkpoint": f"WF{n}", "Workflow-Work-Item": "wi",
            },
        )

    def test_reachable_at_checkpoint_0_1_2_and_n(self):
        with ScratchRepo() as repo:
            _, _, work_item = self._approve_plan(repo)
            self.assertTrue(ws.implementing_entry_reachable(repo.root, work_item, repo.base))
            for n in range(1, 4):
                self._commit_checkpoint(repo, n)
                self.assertTrue(
                    ws.implementing_entry_reachable(repo.root, work_item, repo.base),
                    f"expected reachable at checkpoint {n}",
                )

    def test_plan_document_edit_after_checkpoint_stales_plan_approval(self):
        with ScratchRepo() as repo:
            _, _, work_item = self._approve_plan(repo)
            self._commit_checkpoint(repo, 1)
            self.assertTrue(ws.implementing_entry_reachable(repo.root, work_item, repo.base))
            self._commit_at_path(
                repo, self.PLAN_DOC,
                self._plan_doc_content(work_item["plan_revision"], "plan v2 -- edited after checkpoint"),
                "edit plan post-checkpoint",
            )
            self.assertFalse(ws.implementing_entry_reachable(repo.root, work_item, repo.base))
            self.assertFalse(
                ws.approval_is_current(repo.root, work_item, stage="plan", base_commit=repo.base)
            )

    def test_implementation_revision_bump_does_not_stale_plan_approval(self):
        with ScratchRepo() as repo:
            _, _, work_item = self._approve_plan(repo)
            self.assertTrue(
                ws.approval_is_current(repo.root, work_item, stage="plan", base_commit=repo.base)
            )
            work_item["implementation_revision"] = 5
            self.assertTrue(
                ws.approval_is_current(repo.root, work_item, stage="plan", base_commit=repo.base),
                "an implementation_revision bump touches no field of the plan-stage projection",
            )

    def test_no_approval_record_is_never_reachable(self):
        with ScratchRepo() as repo:
            work_item = {"work_item_id": "wi", "work_item_type": "process", "plan_revision": 1, "plan_approval": None}
            self.assertFalse(ws.implementing_entry_reachable(repo.root, work_item, repo.base))

    def test_stale_status_is_never_reachable_even_with_matching_content(self):
        with ScratchRepo() as repo:
            _, _, work_item = self._approve_plan(repo)
            work_item["plan_approval"]["status"] = "STALE"
            self.assertFalse(ws.implementing_entry_reachable(repo.root, work_item, repo.base))

    def test_forged_manifest_entry_with_no_durable_commit_is_never_reachable(self):
        """Item 233: `plan_approval.approved_review_content_id` edited
        directly in `WORKFLOW_STATE.json` to agree with a rewritten
        registry file, with no durable `Workflow-Plan-Approval` commit
        ever covering that rewritten content. `approval_is_current` alone
        is fooled -- it only recomputes and compares digests, it never
        checks for a commit -- but `implementing_entry_reachable` also
        requires `discover_plan_approval_commit` to find a real trailer
        commit, which a forged state-file edit can never produce."""
        with ScratchRepo() as repo:
            _, review_content_id, work_item = self._approve_plan(repo)
            registry_full = repo.root / self.REGISTRY_PATH
            registry_full.write_text(json.dumps({
                "work_item_id": "wi", "plan_revision": 2, "checkpoints": ["forged"],
            }))
            _run(["git", "add", self.REGISTRY_PATH], cwd=repo.root)
            plan_full = repo.root / self.PLAN_DOC
            plan_full.write_text(self._plan_doc_content(2, "plan v2 -- rewritten registry scenario"))
            _run(["git", "add", self.PLAN_DOC], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "rewrite registry, no approval trailer"], cwd=repo.root)
            forged_head = repo.head()
            forged_id, _ = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
                repo.root, "wi", forged_head, base=repo.base,
            )
            self.assertNotEqual(forged_id, review_content_id)
            work_item["plan_approval"]["approved_review_content_id"] = forged_id
            self.assertTrue(
                ws.approval_is_current(
                    repo.root, work_item, stage="plan", base_commit=repo.base, head=forged_head,
                ),
                "content-only freshness check is fooled by the forged digest",
            )
            self.assertIsNone(
                ws.discover_plan_approval_commit(repo.root, "wi", forged_id, repo.base, head=forged_head)
            )
            self.assertFalse(
                ws.implementing_entry_reachable(repo.root, work_item, repo.base, head=forged_head)
            )

    def test_durable_commit_unreachable_from_head_is_never_reachable(self):
        """Item 234: the durable `Workflow-Plan-Approval` provenance
        commit for `plan_approval.approved_review_content_id` exists
        somewhere in history but is not reachable from live HEAD --
        distinct from item 233's missing-commit case, and distinct from
        an ordinary content mismatch: the approved content is reproduced
        byte-identically on `head`, isolating the commit-reachability
        check alone."""
        with ScratchRepo() as repo:
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            _, review_content_id, work_item = self._approve_plan(repo)
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            _run(["git", "checkout", "-q", "side", "--", "."], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "same approved content, no approval trailer"], cwd=repo.root)
            main_head = repo.head()
            self.assertTrue(
                ws.approval_is_current(
                    repo.root, work_item, stage="plan", base_commit=repo.base, head=main_head,
                ),
                "content alone matches -- isolates the commit-reachability gap",
            )
            self.assertIsNone(
                ws.discover_plan_approval_commit(repo.root, "wi", review_content_id, repo.base, head=main_head)
            )
            self.assertFalse(
                ws.implementing_entry_reachable(repo.root, work_item, repo.base, head=main_head)
            )

    def test_post_approval_manifest_match_succeeds_for_the_real_approval_commit(self):
        with ScratchRepo() as repo:
            approval_sha, _, work_item = self._approve_plan(repo)
            ws.verify_post_approval_manifest_match(
                repo.root, work_item, stage="plan", base_commit=repo.base, commit=approval_sha,
            )  # must not raise

    def test_post_approval_manifest_mismatch_raises(self):
        with ScratchRepo() as repo:
            approval_sha, _, work_item = self._approve_plan(repo)
            work_item["plan_approval"]["approved_review_content_id"] = "wrong-value"
            with self.assertRaises(ws.PostApprovalManifestMismatchError):
                ws.verify_post_approval_manifest_match(
                    repo.root, work_item, stage="plan", base_commit=repo.base, commit=approval_sha,
                )


class TestImplementationStageApprovalSecondItem(unittest.TestCase):
    """Missing-test item 164 (`OPUS-R27-002`/`OPUS-R28-001`, `GPT-R30-004`
    correction #7): `approval_is_current(stage="implementation")` and
    `verify_post_approval_manifest_match(stage="implementation")`, each
    exercised against two distinct work items (never `workflow-v2-1-core`
    alone), load each item's own `<id>-artifacts.json` via
    `fingerprint.artifacts_path_for_work_item` -- never `DEFAULT_ARTIFACTS_PATH`.
    Unlike the plan stage, neither function's `stage="implementation"`
    branch reads `plan_path`/`registry_path`/`mapping_path` at all
    (`approval_review_content_id`'s own implementation branch), so this
    fixture needs no full plan-stage scaffolding -- only each item's own
    artifacts declaration and a real committed source file to change."""

    def _write_implementation_stage_artifacts(self, repo, work_item_id: str) -> str:
        """Each item gets a disjoint protected exact-path set (never a
        shared prefix) so a change to one item's own protected file is
        unambiguously `excluded` (not merely unclassified) under a
        *different* item's own sets -- proving real independence rather
        than accidental non-interference from an always-unclassified path."""
        artifacts_path = f"docs/ai-workflow/registry/{work_item_id}-artifacts.json"
        full = repo.root / artifacts_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(json.dumps({
            "schema_version": 2,
            "work_item_id": work_item_id,
            "implementation_stage": {
                "protected_paths": {
                    artifacts_path: "self-referential declarations file",
                    f"scripts/{work_item_id}_tool.py": "this item's own tooling change",
                },
                "protected_prefixes": {},
                "excluded_paths": {},
                "excluded_prefixes": {
                    "scripts/": "any other item's own tooling files, or this item's own non-protected scripts",
                    "docs/": "plan-stage-governed design content",
                },
            },
        }))
        _run(["git", "add", artifacts_path], cwd=repo.root)
        return artifacts_path

    def _approve_implementation(self, repo, work_item_id: str):
        self._write_implementation_stage_artifacts(repo, work_item_id)
        tool_path = f"scripts/{work_item_id}_tool.py"
        full = repo.root / tool_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("print('v1')\n")
        _run(["git", "add", tool_path], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", f"seed {work_item_id}"], cwd=repo.root)
        commit = repo.head()
        digest = ws.approval_review_content_id(
            repo.root, stage="implementation", base_commit=repo.base,
            work_item_type="process", work_item_id=work_item_id, head=commit,
            artifacts_path=fingerprint.artifacts_path_for_work_item(work_item_id),
        )
        work_item = {
            "work_item_id": work_item_id, "work_item_type": "process",
            "technical_approval": {"status": "CURRENT", "approved_review_content_id": digest},
        }
        return commit, work_item

    def test_approval_is_current_gates_independently_per_item(self):
        with ScratchRepo() as repo:
            commit_a, work_item_a = self._approve_implementation(repo, "workflow-v2-1-core")
            commit_b, work_item_b = self._approve_implementation(repo, "second-item")
            self.assertTrue(
                ws.approval_is_current(
                    repo.root, work_item_a, stage="implementation", base_commit=repo.base, head=commit_a,
                )
            )
            self.assertTrue(
                ws.approval_is_current(
                    repo.root, work_item_b, stage="implementation", base_commit=repo.base, head=commit_b,
                )
            )

    def test_verify_post_approval_manifest_match_succeeds_for_second_item(self):
        with ScratchRepo() as repo:
            commit, work_item = self._approve_implementation(repo, "second-item")
            ws.verify_post_approval_manifest_match(
                repo.root, work_item, stage="implementation", base_commit=repo.base, commit=commit,
            )  # must not raise

    def test_editing_workflow_v2_1_cores_own_artifacts_file_leaves_second_item_untouched(self):
        with ScratchRepo() as repo:
            commit_core, work_item_core = self._approve_implementation(repo, "workflow-v2-1-core")
            commit_second, work_item_second = self._approve_implementation(repo, "second-item")
            # Widen workflow-v2-1-core's own self-referential artifacts
            # declaration -- changes only its own implementation-stage
            # review_content_id.
            core_artifacts_path = repo.root / "docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json"
            core_data = json.loads(core_artifacts_path.read_text())
            core_data["implementation_stage"]["excluded_paths"]["README.md"] = "newly excluded"
            core_artifacts_path.write_text(json.dumps(core_data))
            _run(["git", "add", "docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "widen workflow-v2-1-core's own artifacts file"], cwd=repo.root)
            new_head = repo.head()

            self.assertFalse(
                ws.approval_is_current(
                    repo.root, work_item_core, stage="implementation", base_commit=repo.base, head=new_head,
                ),
                "workflow-v2-1-core's own approval must stale on its own artifacts-file edit",
            )
            self.assertTrue(
                ws.approval_is_current(
                    repo.root, work_item_second, stage="implementation", base_commit=repo.base, head=new_head,
                ),
                "a different item's approval must be untouched by workflow-v2-1-core's own artifacts-file edit",
            )

    def test_changed_implementation_file_stales_approval(self):
        with ScratchRepo() as repo:
            _, work_item = self._approve_implementation(repo, "second-item")
            tool_path = repo.root / "scripts/second-item_tool.py"
            tool_path.write_text("print('v2')\n")
            _run(["git", "add", "scripts/second-item_tool.py"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "change second-item's own protected tool file"], cwd=repo.root)
            new_head = repo.head()
            self.assertFalse(
                ws.approval_is_current(
                    repo.root, work_item, stage="implementation", base_commit=repo.base, head=new_head,
                )
            )

    def test_excluded_implementation_file_does_not_stale_approval(self):
        with ScratchRepo() as repo:
            _, work_item = self._approve_implementation(repo, "second-item")
            # Matches second-item's own excluded_prefixes ("scripts/"),
            # and is not its own declared protected path.
            unrelated_path = repo.root / "scripts/unrelated_helper.py"
            unrelated_path.write_text("print('unrelated')\n")
            _run(["git", "add", "scripts/unrelated_helper.py"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "add an excluded-prefix script file"], cwd=repo.root)
            new_head = repo.head()
            self.assertTrue(
                ws.approval_is_current(
                    repo.root, work_item, stage="implementation", base_commit=repo.base, head=new_head,
                )
            )

    def test_wrong_artifacts_declaration_missing_file_fails_closed(self):
        with ScratchRepo() as repo:
            with self.assertRaises(fingerprint.MissingWorkItemArtifactsDeclarationError):
                ws.approval_review_content_id(
                    repo.root, stage="implementation", base_commit=repo.base,
                    work_item_type="process", work_item_id="second-item", head=repo.base,
                    artifacts_path=fingerprint.artifacts_path_for_work_item("second-item"),
                )

    def test_wrong_artifacts_declaration_missing_implementation_stage_key_fails_closed(self):
        with ScratchRepo() as repo:
            artifacts_path = "docs/ai-workflow/registry/second-item-artifacts.json"
            full = repo.root / artifacts_path
            full.parent.mkdir(parents=True, exist_ok=True)
            # Pre-migration, schema-version-1-shaped file: no
            # implementation_stage key at all -- the same fail-closed
            # boundary as a wholly absent file, not a distinct mechanism.
            full.write_text(json.dumps({
                "schema_version": 1, "work_item_id": "second-item", "plan_stage": {},
            }))
            _run(["git", "add", artifacts_path], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "pre-migration artifacts file"], cwd=repo.root)
            head = repo.head()
            with self.assertRaises(fingerprint.MissingWorkItemArtifactsDeclarationError):
                ws.approval_review_content_id(
                    repo.root, stage="implementation", base_commit=repo.base,
                    work_item_type="process", work_item_id="second-item", head=head,
                    artifacts_path=fingerprint.artifacts_path_for_work_item("second-item"),
                )


class TestWf8cItemNLedgerArtifactsSelfProtection(unittest.TestCase):
    """WF8c item (n) (revision 85, `GPT-R106-002`): `workflow-v2-1-core-
    ledger-status.json` and its companion `workflow-v2-1-core-wf8c-
    evidence.json` are added to `workflow-v2-1-core-artifacts.json`'s own
    `implementation_stage.protected_paths` -- the same self-referential
    carve-out the artifacts file already applies to its own path -- so a
    post-approval edit to either stales `technical_approval` instead of
    silently letting `WFO-LEDGER-COVERAGE`'s evidence pointers be swapped
    for an unrelated already-green test while the prior approval remains
    usable. Both halves are exercised: the fixed classification (edit
    stales approval) and, as a **negative control proving the finding was
    real and not vacuous**, the pre-fix classification (same edit, same
    file, `docs/ai-workflow/registry/` left as a bare `excluded_prefixes`
    entry with no carve-out) leaves approval untouched."""

    _LEDGER_REL = "docs/ai-workflow/registry/workflow-v2-1-core-ledger-status.json"
    _EVIDENCE_REL = "docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json"
    _ARTIFACTS_REL = "docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json"

    def _seed(self, repo, *, carve_out: bool):
        """Writes a minimal but faithful implementation-stage declaration
        for `workflow-v2-1-core`: `docs/ai-workflow/registry/` excluded by
        prefix (matching the real file), with the ledger/evidence carve-out
        present or absent per `carve_out`. Returns the approved commit and
        a `technical_approval`-bearing work-item dict."""
        protected_paths = {self._ARTIFACTS_REL: "self-referential declarations file"}
        if carve_out:
            protected_paths[self._LEDGER_REL] = "WFO-LEDGER-COVERAGE subject artifact"
            protected_paths[self._EVIDENCE_REL] = "WFO-LEDGER-COVERAGE companion evidence file"
        full = repo.root / self._ARTIFACTS_REL
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(json.dumps({
            "schema_version": 2,
            "work_item_id": "workflow-v2-1-core",
            "implementation_stage": {
                "protected_paths": protected_paths,
                "protected_prefixes": {},
                "excluded_paths": {},
                "excluded_prefixes": {
                    "docs/ai-workflow/registry/": "plan-stage-governed registry/artifact-declaration "
                                                   "files -- not an implementation deliverable in their "
                                                   "own right",
                },
            },
        }))
        _run(["git", "add", self._ARTIFACTS_REL], cwd=repo.root)

        ledger_full = repo.root / self._LEDGER_REL
        ledger_full.write_text(json.dumps({"entries": [{"item": 1, "status": "IMPLEMENTED"}]}))
        _run(["git", "add", self._LEDGER_REL], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "seed workflow-v2-1-core ledger artifacts"], cwd=repo.root)
        commit = repo.head()

        digest = ws.approval_review_content_id(
            repo.root, stage="implementation", base_commit=repo.base,
            work_item_type="process", work_item_id="workflow-v2-1-core", head=commit,
            artifacts_path=fingerprint.artifacts_path_for_work_item("workflow-v2-1-core"),
        )
        work_item = {
            "work_item_id": "workflow-v2-1-core", "work_item_type": "process",
            "technical_approval": {"status": "CURRENT", "approved_review_content_id": digest},
        }
        return commit, work_item

    def _edit_ledger_and_commit(self, repo) -> str:
        ledger_full = repo.root / self._LEDGER_REL
        ledger_full.write_text(json.dumps({"entries": [{"item": 1, "status": "SUPERSEDED"}]}))
        _run(["git", "add", self._LEDGER_REL], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "relabel a ledger entry post-approval"], cwd=repo.root)
        return repo.head()

    def _edit_evidence_and_commit(self, repo) -> str:
        evidence_full = repo.root / self._EVIDENCE_REL
        evidence_full.write_text(json.dumps({1: "workflow_state_test.SomeUnrelatedAlreadyGreenTest"}))
        _run(["git", "add", self._EVIDENCE_REL], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "swap in an unrelated evidence pointer post-approval"], cwd=repo.root)
        return repo.head()

    def test_post_approval_ledger_edit_stales_approval_when_protected(self):
        with ScratchRepo() as repo:
            _, work_item = self._seed(repo, carve_out=True)
            new_head = self._edit_ledger_and_commit(repo)
            self.assertFalse(
                ws.approval_is_current(
                    repo.root, work_item, stage="implementation", base_commit=repo.base, head=new_head,
                ),
                "a post-approval ledger-status.json edit must stale technical_approval once protected",
            )

    def test_post_approval_evidence_file_creation_stales_approval_when_protected(self):
        with ScratchRepo() as repo:
            _, work_item = self._seed(repo, carve_out=True)
            new_head = self._edit_evidence_and_commit(repo)
            self.assertFalse(
                ws.approval_is_current(
                    repo.root, work_item, stage="implementation", base_commit=repo.base, head=new_head,
                ),
                "a post-approval wf8c-evidence.json creation/edit must stale technical_approval once protected",
            )

    def test_post_approval_ledger_edit_is_invisible_without_the_carve_out(self):
        """Negative control: reproduces the pre-item-(n) vulnerability
        `GPT-R106-002` found -- with no carve-out, ledger-status.json falls
        under the bare `docs/ai-workflow/registry/` `excluded_prefixes`
        entry, so the identical relabel above leaves a stale `technical_
        approval` looking current, letting `WFO-LEDGER-COVERAGE` be
        satisfied from unreviewed content."""
        with ScratchRepo() as repo:
            _, work_item = self._seed(repo, carve_out=False)
            new_head = self._edit_ledger_and_commit(repo)
            self.assertTrue(
                ws.approval_is_current(
                    repo.root, work_item, stage="implementation", base_commit=repo.base, head=new_head,
                ),
                "without the carve-out this edit is invisible to technical_approval -- proves the finding "
                "was real, not a vacuous test",
            )


class TestImplementingEntryReachableSecondItem(unittest.TestCase):
    """Missing-test item 150's third caller (`GPT-R31-002`):
    `implementing_entry_reachable` -- as opposed to `approval_is_current`,
    already exercised against a second item at the implementation stage
    by `TestImplementationStageApprovalSecondItem` above -- gates
    independently for a second, non-`"wi"`, non-`workflow-v2-1-core`
    process work item's own `plan_approval` record, with no `plan_revision`
    parameter passed."""

    def test_second_items_plan_approval_is_reachable_via_implementing_entry_reachable(self):
        with ScratchRepo() as repo:
            work_item_id = "second-item"
            plan_path_rel = f"docs/ai-workflow/{work_item_id}-plan.md"
            registry_rel = f"docs/ai-workflow/registry/{work_item_id}-registry.json"
            mapping_rel = f"docs/ai-workflow/requirements/{work_item_id}-mapping.json"
            artifacts_rel = f"docs/ai-workflow/registry/{work_item_id}-artifacts.json"
            state_rel = "docs/ai-workflow/WORKFLOW_STATE.json"
            for rel, content in (
                (plan_path_rel, "# Plan (Revision 1)\n\nplan v1\n"),
                (registry_rel, json.dumps({"work_item_id": work_item_id, "plan_revision": 1, "checkpoints": []})),
                (mapping_rel, json.dumps({"work_item_id": work_item_id, "requirements": {}})),
                (artifacts_rel, json.dumps({
                    "schema_version": 2, "work_item_id": work_item_id,
                    "plan_stage": {
                        "protected_paths": [plan_path_rel, registry_rel, mapping_rel],
                        "excluded_paths": {
                            state_rel: "runtime-mutable state",
                            artifacts_rel: "non-immutable registry artifact",
                        },
                        "excluded_prefixes": {"scripts/": "checkpoint scaffolding"},
                    },
                })),
                (state_rel, json.dumps({
                    "schema_version": 1, "active_work_item_id": work_item_id,
                    "work_items": {
                        work_item_id: {
                            "work_item_id": work_item_id, "work_item_type": "process",
                            "plan_path": plan_path_rel, "registry_path": registry_rel,
                            "mapping_path": mapping_rel, "base_commit": repo.base,
                        },
                    },
                })),
            ):
                full = repo.root / rel
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content)
                _run(["git", "add", rel], cwd=repo.root)

            protected = frozenset({plan_path_rel, registry_rel, mapping_rel})
            excluded_paths = {state_rel: "runtime-mutable state", artifacts_rel: "non-immutable registry artifact"}
            excluded_prefixes = {"scripts/": "checkpoint scaffolding"}
            review_content_id, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, "process", work_item_id, 1, protected, excluded_paths, excluded_prefixes,
            )
            _run(
                ["git", "commit", "-q", "-m",
                 f"approve plan\n\nWorkflow-Plan-Approval: {review_content_id}\n"
                 f"Workflow-Work-Item: {work_item_id}"],
                cwd=repo.root,
            )
            approval_sha = repo.head()

            work_item = {
                "work_item_id": work_item_id, "work_item_type": "process", "plan_revision": 1,
                "plan_approval": {"status": "CURRENT", "approved_review_content_id": review_content_id},
            }
            self.assertTrue(
                ws.implementing_entry_reachable(repo.root, work_item, repo.base),
                "a second item's own plan_approval must be independently reachable, "
                "with no plan_revision parameter passed to implementing_entry_reachable",
            )

            # A checkpoint commit for this second item must keep it
            # reachable, exactly like TestApprovalFreshnessAndEntry proves
            # for "wi" above -- proving the property generalizes, not
            # only holds for one hardcoded item.
            checkpoint_path = repo.root / "scripts" / f"{work_item_id}_checkpoint_1.txt"
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text("checkpoint 1\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(
                ["git", "commit", "-q", "-m",
                 f"checkpoint 1\n\nWorkflow-Checkpoint: WF1\nWorkflow-Work-Item: {work_item_id}"],
                cwd=repo.root,
            )
            self.assertTrue(ws.implementing_entry_reachable(repo.root, work_item, repo.base))
            self.assertNotEqual(approval_sha, repo.head())


# ---------------------------------------------------------------------------
# WF4a-iii: "no protected path is dirty" gate condition (D-States;
# missing-test item 16)
# ---------------------------------------------------------------------------


class TestProtectedPathDirty(unittest.TestCase):
    PROTECTED_PATHS = {"scripts/foo.py": "protected"}
    PROTECTED_PREFIXES = {"app/": "protected"}
    EXCLUDED_PATHS = {
        "docs/ai-workflow/WORKFLOW_STATE.json": "excluded",
        "docs/ai-workflow/WORKFLOW_CONFIG.json": "excluded",
    }
    EXCLUDED_PREFIXES = {"docs/": "excluded"}

    def _any_dirty(self, repo_root):
        return ws.any_protected_path_dirty(
            repo_root, self.PROTECTED_PATHS, self.PROTECTED_PREFIXES,
            self.EXCLUDED_PATHS, self.EXCLUDED_PREFIXES,
        )

    def test_clean_tree_is_not_dirty(self):
        with ScratchRepo() as repo:
            self.assertFalse(self._any_dirty(repo.root))

    def test_dirty_state_and_config_files_alone_do_not_block(self):
        with ScratchRepo() as repo:
            (repo.root / "docs" / "ai-workflow").mkdir(parents=True)
            (repo.root / "docs/ai-workflow/WORKFLOW_STATE.json").write_text("{}")
            (repo.root / "docs/ai-workflow/WORKFLOW_CONFIG.json").write_text("{}")
            self.assertFalse(self._any_dirty(repo.root))

    def test_dirty_protected_prefix_path_blocks(self):
        with ScratchRepo() as repo:
            (repo.root / "app").mkdir()
            (repo.root / "app" / "Main.kt").write_text("fun main() {}\n")
            self.assertTrue(self._any_dirty(repo.root))

    def test_dirty_protected_exact_path_blocks(self):
        with ScratchRepo() as repo:
            (repo.root / "scripts").mkdir()
            (repo.root / "scripts" / "foo.py").write_text("pass\n")
            self.assertTrue(self._any_dirty(repo.root))

    def test_unclassified_dirty_path_fails_closed(self):
        with ScratchRepo() as repo:
            (repo.root / "mystery.txt").write_text("???\n")
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                self._any_dirty(repo.root)


# ---------------------------------------------------------------------------
# WF-M8b: D-Legacy phase 2's own freshness primitive -- a committed-
# history (not exact-hash-reproduction) counterpart of
# any_protected_path_dirty, purpose-built so widening a dormant legacy
# item's own artifact-declarations file (to classify a concurrent work
# item's later paths) never itself reads as staleness.
# ---------------------------------------------------------------------------


class TestProtectedPathChangedSince(unittest.TestCase):
    PROTECTED_PATHS = {"app/Main.kt": "protected"}
    PROTECTED_PREFIXES = {"gradle/": "protected"}
    EXCLUDED_PATHS = {}
    EXCLUDED_PREFIXES = {"docs/": "excluded", "scripts/": "excluded"}

    def _changed_since(self, repo_root, base, head):
        return ws.any_protected_path_changed_since(
            repo_root, base, head, self.PROTECTED_PATHS, self.PROTECTED_PREFIXES,
            self.EXCLUDED_PATHS, self.EXCLUDED_PREFIXES,
        )

    def test_no_change_in_range_is_not_stale(self):
        with ScratchRepo() as repo:
            reviewed = repo.commit("reviewed head")
            self.assertFalse(self._changed_since(repo.root, reviewed, "HEAD"))

    def test_unrelated_excluded_commit_after_review_is_not_stale(self):
        with ScratchRepo() as repo:
            reviewed = repo.commit("reviewed head")
            self._commit_at(repo, "docs/NOTES.md", "unrelated concurrent work item\n")
            self.assertFalse(self._changed_since(repo.root, reviewed, "HEAD"))

    def test_protected_exact_path_commit_after_review_is_stale(self):
        with ScratchRepo() as repo:
            reviewed = repo.commit("reviewed head")
            self._commit_at(repo, "app/Main.kt", "fun main() {}\n")
            self.assertTrue(self._changed_since(repo.root, reviewed, "HEAD"))

    def test_protected_prefix_path_commit_after_review_is_stale(self):
        with ScratchRepo() as repo:
            reviewed = repo.commit("reviewed head")
            self._commit_at(repo, "gradle/libs.versions.toml", "[versions]\n")
            self.assertTrue(self._changed_since(repo.root, reviewed, "HEAD"))

    def test_protected_change_before_review_is_never_seen(self):
        with ScratchRepo() as repo:
            self._commit_at(repo, "app/Main.kt", "fun main() {}\n")
            reviewed = repo.head()
            self._commit_at(repo, "docs/NOTES.md", "unrelated\n")
            self.assertFalse(self._changed_since(repo.root, reviewed, "HEAD"))

    def test_unclassified_committed_path_after_review_fails_closed(self):
        with ScratchRepo() as repo:
            reviewed = repo.commit("reviewed head")
            self._commit_at(repo, "mystery.txt", "???\n")
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                self._changed_since(repo.root, reviewed, "HEAD")

    def _commit_at(self, repo, rel_path, content):
        full = repo.root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        _run(["git", "add", rel_path], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", f"touch {rel_path}"], cwd=repo.root)
        return repo.head()


# ---------------------------------------------------------------------------
# WF4a-iv: D-Plan-Review-Stages' verdict/state transition table -- the
# ledger writers `record_local_plan_review`/`record_manual_plan_review`
# and the `"2.1"`-only revised /apply-plan-review exit step
# (transition_to_awaiting_local_plan_review). Missing-test items 80-99,
# 112-114, WFR-41.
# ---------------------------------------------------------------------------


def _v21_work_item(**overrides) -> dict:
    defaults = {"governing_workflow_version": "2.1", "phase": "AWAITING_LOCAL_PLAN_REVIEW"}
    defaults.update(overrides)
    return _base_work_item(**defaults)


class TestRecordLocalPlanReview(unittest.TestCase):
    def test_wrong_governing_version_rejected(self):
        wi = _base_work_item(governing_workflow_version="1", phase="AWAITING_LOCAL_PLAN_REVIEW")
        with self.assertRaises(ws.WrongGoverningVersionForPlanReviewStageError):
            ws.record_local_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
                review_content_id="c1", round=1, now="t1",
            )

    def test_wrong_phase_rejected(self):
        """Missing-test item 90 (v2.1 side): "already completed this
        round" is exactly a phase that has moved on."""
        wi = _v21_work_item(phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        with self.assertRaises(ws.WrongPhaseForPlanReviewStageError):
            ws.record_local_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
                review_content_id="c1", round=1, now="t1",
            )

    def test_unknown_verdict_rejected(self):
        wi = _v21_work_item()
        with self.assertRaises(ws.UnknownPlanReviewVerdictError):
            ws.record_local_plan_review(
                _base_state(wi=wi), "wi", verdict="MAYBE", bundle_id="b1",
                review_content_id="c1", round=1, now="t1",
            )

    def test_approve_records_ledger_and_transitions_to_manual_stage(self):
        """Missing-test items 80/82 (first half): a local APPROVE alone
        reaches AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW, not AWAITING_PLAN_APPROVAL."""
        wi = _v21_work_item(state_revision=1)
        new_state = ws.record_local_plan_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
            review_content_id="c1", round=1, now="t1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(item["plan_review_stages"], {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_PLAN_REVIEW": None,
        })
        self.assertEqual(item["state_revision"], 2)

    def test_approve_clears_stale_manual_entry_from_a_prior_content_id(self):
        wi = _v21_work_item(plan_review_stages={
            "review_content_id": "old",
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b0", "verdict": "APPROVE", "round": 1, "completed_at": "t0"},
            "MANUAL_EXTERNAL_PLAN_REVIEW": {"bundle_id": "b0", "verdict": "APPROVE", "round": 1, "completed_at": "t0"},
        })
        new_state = ws.record_local_plan_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
            review_content_id="new", round=1, now="t1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["plan_review_stages"]["review_content_id"], "new")
        self.assertIsNone(item["plan_review_stages"]["MANUAL_EXTERNAL_PLAN_REVIEW"])

    def test_revise_transitions_to_revising_plan_with_no_ledger_write(self):
        """Missing-test item 94: can never reach AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW."""
        wi = _v21_work_item(state_revision=1, plan_review_stages=None)
        new_state = ws.record_local_plan_review(
            _base_state(wi=wi), "wi", verdict="REVISE", bundle_id="b1",
            review_content_id="c1", round=1, now="t1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "REVISING_PLAN")
        self.assertIsNone(item["plan_review_stages"])

    def test_block_is_a_true_no_op(self):
        """Missing-test item 95: no ledger write, no transition; remains
        AWAITING_LOCAL_PLAN_REVIEW -- the returned state is unchanged."""
        wi = _v21_work_item(state_revision=1, plan_review_stages=None)
        state = _base_state(wi=wi)
        new_state = ws.record_local_plan_review(
            state, "wi", verdict="BLOCK", bundle_id="b1",
            review_content_id="c1", round=1, now="t1",
        )
        self.assertEqual(new_state, state)
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_LOCAL_PLAN_REVIEW")


class TestRecordManualPlanReview(unittest.TestCase):
    def _local_approved_wi(self, **overrides):
        defaults = {
            "phase": "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
            "plan_review_stages": {
                "review_content_id": "c1",
                "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
                "MANUAL_EXTERNAL_PLAN_REVIEW": None,
            },
        }
        defaults.update(overrides)
        return _v21_work_item(**defaults)

    def test_wrong_governing_version_rejected(self):
        wi = self._local_approved_wi(governing_workflow_version="1")
        with self.assertRaises(ws.WrongGoverningVersionForPlanReviewStageError):
            ws.record_manual_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_wrong_phase_rejected(self):
        """WFR-41: a local REVISE verdict followed by an attempted manual-
        stage action is refused."""
        wi = self._local_approved_wi(phase="REVISING_PLAN")
        with self.assertRaises(ws.WrongPhaseForPlanReviewStageError):
            ws.record_manual_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_wrong_role_rejected(self):
        """Missing-test item 97."""
        wi = self._local_approved_wi()
        with self.assertRaises(ws.WrongReviewerRoleError):
            ws.record_manual_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="LOCAL_MODEL_PLAN_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_stale_review_content_id_is_hard_blocked(self):
        """Missing-test items 97/112: a review_content_id change blocks
        ingestion, unlike a wrapper-only bundle_id mismatch."""
        wi = self._local_approved_wi()
        with self.assertRaises(ws.StaleReviewContentIdError):
            ws.record_manual_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id="stale",
            )

    def test_missing_local_approval_rejected(self):
        """Missing-test item 81/97: no current local APPROVE for this
        review_content_id."""
        wi = self._local_approved_wi(plan_review_stages={
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": None,
            "MANUAL_EXTERNAL_PLAN_REVIEW": None,
        })
        with self.assertRaises(ws.MissingLocalApprovalForManualStageError):
            ws.record_manual_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_duplicate_ingestion_rejected(self):
        """Missing-test item 97."""
        wi = self._local_approved_wi(plan_review_stages={
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_PLAN_REVIEW": {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"},
        })
        with self.assertRaises(ws.DuplicateManualStageIngestionError):
            ws.record_manual_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b3", round=2, now="t3",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_approve_completes_ledger_and_transitions_to_plan_approval(self):
        """Missing-test item 82: both stages recorded, in order, against
        the same review_content_id reaches AWAITING_PLAN_APPROVAL."""
        wi = self._local_approved_wi(state_revision=1)
        new_state = ws.record_manual_plan_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id="c1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "AWAITING_PLAN_APPROVAL")
        self.assertEqual(item["plan_review_stages"]["MANUAL_EXTERNAL_PLAN_REVIEW"], {
            "bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2",
        })
        self.assertEqual(item["state_revision"], 2)
        self.assertTrue(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=item["plan_review_stages"], current_review_content_id="c1",
        ))

    def test_approve_records_actual_bundle_id_even_when_mismatched(self):
        """Missing-test item 113 (OPUS-R14-005): the ledger records the
        reviewed feedback's actual bundle_id in both the matching and
        mismatched cases -- the advisory bundle_id check never blocks."""
        wi = self._local_approved_wi()
        warning = ws.check_manual_stage_bundle_id_advisory(
            feedback_bundle_id="stale-wrapper-bundle", current_bundle_id="fresh-wrapper-bundle",
        )
        self.assertIsNotNone(warning)
        new_state = ws.record_manual_plan_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="stale-wrapper-bundle", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id="c1",
        )
        self.assertEqual(
            new_state["work_items"]["wi"]["plan_review_stages"]["MANUAL_EXTERNAL_PLAN_REVIEW"]["bundle_id"],
            "stale-wrapper-bundle",
        )

    def test_matching_bundle_id_has_no_advisory_warning(self):
        self.assertIsNone(ws.check_manual_stage_bundle_id_advisory("b1", "b1"))

    def test_revise_transitions_to_revising_plan_with_no_ledger_write(self):
        """Missing-test item 99."""
        wi = self._local_approved_wi(state_revision=1)
        new_state = ws.record_manual_plan_review(
            _base_state(wi=wi), "wi", verdict="REVISE", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id="c1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "REVISING_PLAN")
        self.assertIsNone(item["plan_review_stages"]["MANUAL_EXTERNAL_PLAN_REVIEW"])

    def test_block_is_a_true_no_op(self):
        """Missing-test item 99: no ledger write, no transition; remains
        AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW."""
        wi = self._local_approved_wi(state_revision=1)
        state = _base_state(wi=wi)
        new_state = ws.record_manual_plan_review(
            state, "wi", verdict="BLOCK", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id="c1",
        )
        self.assertEqual(new_state, state)
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")


class TestTransitionToAwaitingLocalPlanReview(unittest.TestCase):
    def test_from_revising_plan_after_an_accepted_edit(self):
        """Missing-test item 89: whether the edit was driven by a local or
        manual-external REVISE, the next required stage is always
        AWAITING_LOCAL_PLAN_REVIEW -- never directly back to manual-external
        review or to AWAITING_PLAN_APPROVAL."""
        wi = _v21_work_item(phase="REVISING_PLAN", state_revision=3, plan_review_stages={
            "review_content_id": "stale",
            "LOCAL_MODEL_PLAN_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_PLAN_REVIEW": None,
        })
        new_state = ws.transition_to_awaiting_local_plan_review(_base_state(wi=wi), "wi", now="t2")
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
        self.assertEqual(item["state_revision"], 4)
        # The stale ledger is left as-is, not explicitly cleared -- it
        # simply no longer matches a freshly recomputed current id.
        self.assertEqual(item["plan_review_stages"]["review_content_id"], "stale")
        self.assertFalse(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=item["plan_review_stages"], current_review_content_id="fresh",
        ))


class TestFullTwoStageSequence(unittest.TestCase):
    def test_local_approve_then_manual_approve_reaches_plan_approval_gate(self):
        """Missing-test item 80: a local APPROVE alone cannot reach
        AWAITING_PLAN_APPROVAL for a "2.1" item; missing-test item 82: both
        stages, in order, do."""
        wi = _v21_work_item()
        state = _base_state(wi=wi)

        after_local = ws.record_local_plan_review(
            state, "wi", verdict="APPROVE", bundle_id="b1", review_content_id="c1", round=1, now="t1",
        )
        item = after_local["work_items"]["wi"]
        self.assertEqual(item["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assertFalse(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=item["plan_review_stages"], current_review_content_id="c1",
        ))

        after_manual = ws.record_manual_plan_review(
            after_local, "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id="c1",
        )
        item2 = after_manual["work_items"]["wi"]
        self.assertEqual(item2["phase"], "AWAITING_PLAN_APPROVAL")
        self.assertTrue(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=item2["plan_review_stages"], current_review_content_id="c1",
        ))


class TestPlanReviewStageKeyNormalization(unittest.TestCase):
    """workflow-v2-3-followups CP3 (REQ-8/-9/-10/-22): normalize_plan_review_
    stages' compatibility-reading and collision detection, and
    migrate_plan_review_stage_keys' one-time migration -- legacy lowercase
    reads remain supported so historical evidence never needs rewriting,
    while every live non-terminal ledger is migrated to canonical casing."""

    _LEGACY_LOCAL_APPROVE = {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"}
    _LEGACY_MANUAL_APPROVE = {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"}

    def test_normalize_passes_review_content_id_through_unchanged(self):
        stages = {"review_content_id": "c1", "LOCAL_MODEL_PLAN_REVIEW": None, "MANUAL_EXTERNAL_PLAN_REVIEW": None}
        self.assertEqual(ws.normalize_plan_review_stages(stages), stages)

    def test_normalize_reads_legacy_lowercase_keys(self):
        """The compatibility-read proof: a dict built entirely with the
        legacy lowercase keys normalizes to the canonical dict."""
        legacy = {
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "manual_external_plan_review": None,
        }
        self.assertEqual(ws.normalize_plan_review_stages(legacy), {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE,
            "MANUAL_EXTERNAL_PLAN_REVIEW": None,
        })

    def test_normalize_is_idempotent_on_an_already_canonical_dict(self):
        canonical = {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE,
            "MANUAL_EXTERNAL_PLAN_REVIEW": self._LEGACY_MANUAL_APPROVE,
        }
        self.assertEqual(ws.normalize_plan_review_stages(canonical), canonical)

    def test_normalize_collapses_byte_identical_duplicate_regardless_of_insertion_order(self):
        """GPT-FUP-R6-I02: a legacy+canonical duplicate with byte-identical
        values collapses silently, proven both ways round (whichever raw
        key was inserted first)."""
        forward = {
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE,
        }
        backward = {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE,
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
        }
        expected = {"review_content_id": "c1", "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE}
        self.assertEqual(ws.normalize_plan_review_stages(forward), expected)
        self.assertEqual(ws.normalize_plan_review_stages(backward), expected)

    def test_normalize_raises_on_conflicting_duplicate_regardless_of_insertion_order(self):
        """GPT-FUP-R6-I02: a legacy+canonical duplicate with conflicting
        values raises AmbiguousPlanReviewStageKeyError, naming both raw
        keys -- never resolved by dict key-iteration order."""
        forward = {
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "LOCAL_MODEL_PLAN_REVIEW": {**self._LEGACY_LOCAL_APPROVE, "round": 2},
        }
        backward = {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": {**self._LEGACY_LOCAL_APPROVE, "round": 2},
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
        }
        for stages in (forward, backward):
            with self.assertRaises(ws.AmbiguousPlanReviewStageKeyError) as ctx:
                ws.normalize_plan_review_stages(stages)
            self.assertIn("local_model_plan_review", str(ctx.exception))
            self.assertIn("LOCAL_MODEL_PLAN_REVIEW", str(ctx.exception))

    def test_plan_approval_gate_reachable_tolerates_legacy_keys(self):
        legacy = {
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "manual_external_plan_review": self._LEGACY_MANUAL_APPROVE,
        }
        self.assertTrue(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="2.1",
            plan_review_stages=legacy, current_review_content_id="c1",
        ))

    def test_validate_state_accepts_legacy_cased_non_terminal_ledger(self):
        """LPR-R1-I03: a non-terminal work item's legacy-cased ledger reads
        and behaves correctly via the compatibility-read helper, and can be
        driven through a further transition, before any migration runs."""
        wi = _v21_work_item(phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", plan_review_stages={
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "manual_external_plan_review": None,
        })
        ws.validate_state(_base_state(wi=copy.deepcopy(wi)))  # must not raise

        # The further transition: a REVISE verdict only reads the ledger
        # (via validate_manual_plan_review_preconditions's compatibility-
        # tolerant normalization) and never writes it, so it is unaffected
        # by the write-site hazard the next test documents.
        new_state = ws.record_manual_plan_review(
            _base_state(wi=wi), "wi", verdict="REVISE", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
            feedback_review_content_id="c1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "REVISING_PLAN")

    def test_record_manual_plan_review_approve_raises_on_a_legacy_cased_ledger(self):
        """Documents `GPT-FUP-R6-I02`'s accepted, by-design edge case:
        `record_manual_plan_review`'s APPROVE branch writes the canonical
        key by in-place assignment (unlike `record_local_plan_review`,
        which replaces the whole dict), so a not-yet-migrated legacy-cased
        ledger ends up holding both a legacy and a canonical key for the
        MANUAL_EXTERNAL_PLAN_REVIEW stage. This is refused cleanly via
        AmbiguousPlanReviewStageKeyError -- never silently resolved by
        dict-iteration order -- exactly the fail-closed behavior CP3's
        write-site disposition relies on instead of changing the write
        site itself (not reachable for any item in this repository today,
        since CP3's own migration runs immediately after this checkpoint
        lands)."""
        wi = _v21_work_item(phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", plan_review_stages={
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "manual_external_plan_review": None,
        })
        with self.assertRaises(ws.AmbiguousPlanReviewStageKeyError):
            ws.record_manual_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_reviewer_role_accepts_either_casing_and_refuses_a_third_value(self):
        wi = _v21_work_item(phase="AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", plan_review_stages={
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE,
            "MANUAL_EXTERNAL_PLAN_REVIEW": None,
        })
        ws.validate_manual_plan_review_preconditions(
            copy.deepcopy(wi), current_review_content_id="c1",
            feedback_role="MANUAL_EXTERNAL_PLAN_REVIEW", feedback_review_content_id="c1",
        )
        ws.validate_manual_plan_review_preconditions(
            copy.deepcopy(wi), current_review_content_id="c1",
            feedback_role="manual_external_plan_review", feedback_review_content_id="c1",
        )
        with self.assertRaises(ws.WrongReviewerRoleError):
            ws.validate_manual_plan_review_preconditions(
                copy.deepcopy(wi), current_review_content_id="c1",
                feedback_role="something_else", feedback_review_content_id="c1",
            )


class TestMigratePlanReviewStageKeys(unittest.TestCase):
    """workflow-v2-3-followups CP3 (LPR-R2-I02): the one-time migration
    step run against the live WORKFLOW_STATE.json."""

    _LEGACY_LOCAL_APPROVE = {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"}
    _LEGACY_MANUAL_APPROVE = {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"}

    def _legacy_stages(self):
        return {
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "manual_external_plan_review": self._LEGACY_MANUAL_APPROVE,
        }

    def test_migrates_every_live_non_terminal_record_leaves_terminal_untouched(self):
        non_terminal = _v21_work_item(
            work_item_id="live", phase="AWAITING_PLAN_APPROVAL", plan_review_stages=self._legacy_stages(),
        )
        terminal = _v21_work_item(
            work_item_id="done", phase="MILESTONE_COMPLETE", plan_review_stages=self._legacy_stages(),
        )
        state = _base_state(live=non_terminal, done=terminal)

        migrated = ws.migrate_plan_review_stage_keys(state)

        self.assertEqual(migrated["work_items"]["live"]["plan_review_stages"], {
            "review_content_id": "c1",
            "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE,
            "MANUAL_EXTERNAL_PLAN_REVIEW": self._LEGACY_MANUAL_APPROVE,
        })
        # Terminal-phase record is byte-unchanged -- immutable historical
        # evidence, never rewritten.
        self.assertEqual(migrated["work_items"]["done"]["plan_review_stages"], self._legacy_stages())
        # The migrated non-terminal record validates cleanly through the
        # rest of the codebase, not just a dict that looks right.
        ws.validate_state(migrated)

    def test_is_idempotent(self):
        state = _base_state(live=_v21_work_item(
            work_item_id="live", phase="AWAITING_PLAN_APPROVAL", plan_review_stages=self._legacy_stages(),
        ))
        once = ws.migrate_plan_review_stage_keys(state)
        twice = ws.migrate_plan_review_stage_keys(once)
        self.assertEqual(once, twice)

    def test_leaves_a_null_or_absent_ledger_untouched(self):
        wi = _v21_work_item(work_item_id="live", phase="AWAITING_LOCAL_PLAN_REVIEW", plan_review_stages=None)
        state = _base_state(live=wi)
        migrated = ws.migrate_plan_review_stage_keys(state)
        self.assertIsNone(migrated["work_items"]["live"]["plan_review_stages"])

    def test_raises_on_an_ambiguous_non_terminal_ledger_leaving_state_untouched(self):
        """An already-ambiguous non-terminal work item's ledger makes the
        migration raise rather than silently pick a winner. Run via
        state_transaction (the real call site), a raised exception leaves
        WORKFLOW_STATE.json completely unwritten."""
        ambiguous = _v21_work_item(work_item_id="live", phase="AWAITING_PLAN_APPROVAL", plan_review_stages={
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "LOCAL_MODEL_PLAN_REVIEW": {**self._LEGACY_LOCAL_APPROVE, "round": 2},
        })
        state = _base_state(live=ambiguous)
        with self.assertRaises(ws.AmbiguousPlanReviewStageKeyError):
            ws.migrate_plan_review_stage_keys(state)

    def test_conflicting_duplicate_collapses_when_byte_identical(self):
        identical = _v21_work_item(work_item_id="live", phase="AWAITING_PLAN_APPROVAL", plan_review_stages={
            "review_content_id": "c1",
            "local_model_plan_review": self._LEGACY_LOCAL_APPROVE,
            "LOCAL_MODEL_PLAN_REVIEW": self._LEGACY_LOCAL_APPROVE,
        })
        state = _base_state(live=identical)
        migrated = ws.migrate_plan_review_stage_keys(state)
        self.assertEqual(
            migrated["work_items"]["live"]["plan_review_stages"]["LOCAL_MODEL_PLAN_REVIEW"],
            self._LEGACY_LOCAL_APPROVE,
        )


# ---------------------------------------------------------------------------
# WF2: D-Selection's four-rule checkpoint-selection algorithm, the
# IN_PROGRESS/COMPLETE state writers, and WORKTREE_IDENTITY.json's writer
# and resume-side check. Missing-test items 9, 28, 31, 43, 71.
# ---------------------------------------------------------------------------


_REGISTRY = {"checkpoints": [
    {"id": "A", "depends_on": []},
    {"id": "B", "depends_on": ["A"]},
    {"id": "C", "depends_on": ["A"]},
    {"id": "D", "depends_on": ["B", "C"]},
]}


class TestSelectNextCheckpoint(unittest.TestCase):
    def test_fresh_work_item_selects_first_dependency_free_checkpoint(self):
        wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
        self.assertEqual(ws.select_next_checkpoint(wi, _REGISTRY), "A")

    def test_rule_1_resumes_in_progress_checkpoint_without_reconciliation(self):
        """Missing-test item 31: an interrupted checkpoint with state
        written but narrative not updated resumes without human
        reconciliation -- rule 1 alone decides this, no other field of the
        work item is consulted."""
        wi = _base_work_item(
            current_checkpoint_id="B",
            checkpoints={"A": {"status": "COMPLETE"}, "B": {"status": "IN_PROGRESS"}},
        )
        self.assertEqual(ws.select_next_checkpoint(wi, _REGISTRY), "B")

    def test_rule_2_skips_completed_and_respects_dependencies(self):
        wi = _base_work_item(
            current_checkpoint_id=None,
            checkpoints={"A": {"status": "COMPLETE"}, "B": {"status": "COMPLETE"}},
        )
        # C is also dependency-ready (depends only on A); registry order
        # places C before D, and D's own dependency (C) isn't complete yet.
        self.assertEqual(ws.select_next_checkpoint(wi, _REGISTRY), "C")

    def test_all_complete_returns_none(self):
        wi = _base_work_item(current_checkpoint_id=None, checkpoints={
            cid: {"status": "COMPLETE"} for cid in ("A", "B", "C", "D")
        })
        self.assertIsNone(ws.select_next_checkpoint(wi, _REGISTRY))

    def test_rule_4_blocked_dependency_is_named_not_silently_idle(self):
        """B and C both depend on A, which is not COMPLETE -- nothing is
        selectable, and this must raise rather than return None (None is
        reserved for the genuinely-finished case)."""
        wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
        registry = {"checkpoints": [
            {"id": "A", "depends_on": ["MISSING"]},
        ]}
        with self.assertRaises(ws.NoCheckpointReadyError) as ctx:
            ws.select_next_checkpoint(wi, registry)
        self.assertIn("A", str(ctx.exception))
        self.assertIn("MISSING", str(ctx.exception))

    def test_determinism_across_independent_calls(self):
        """Missing-test item 28: two independent fresh sessions against
        identical state select the same checkpoint -- the function is
        pure, so this is just repeatability, checked against two
        independently-constructed (not shared/mutated) copies of the same
        logical state."""
        wi_a = _base_work_item(current_checkpoint_id=None, checkpoints={"A": {"status": "COMPLETE"}})
        wi_b = _base_work_item(current_checkpoint_id=None, checkpoints={"A": {"status": "COMPLETE"}})
        self.assertEqual(
            ws.select_next_checkpoint(wi_a, _REGISTRY),
            ws.select_next_checkpoint(wi_b, _REGISTRY),
        )


class TestCheckpointStateTransitions(unittest.TestCase):
    def test_transition_to_in_progress_sets_status_and_current_pointer(self):
        wi = _base_work_item(current_checkpoint_id=None, checkpoints={}, state_revision=1)
        state = _base_state(wi=wi)
        new_state = ws.transition_checkpoint_in_progress(state, "wi", "A", "deadbeef", now="t1")
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["checkpoints"]["A"], {"status": "IN_PROGRESS", "start_commit": "deadbeef"})
        self.assertEqual(item["current_checkpoint_id"], "A")
        self.assertEqual(item["state_revision"], 2)
        # Original state is untouched (functions return a new dict).
        self.assertEqual(state["work_items"]["wi"]["checkpoints"], {})

    def test_transition_to_in_progress_refuses_once_the_work_item_has_left_implementing(self):
        """XMODEL-R4-B1, missing-tests item 3: this is the second,
        independent half of the amendment-vs-checkpoint-start race fix. A
        checkpoint claim published before an amendment transition's own
        state_transaction committed must not be able to publish
        IN_PROGRESS once the work item has moved on to `AMENDING_PLAN` --
        or, more generally, to any phase outside
        `CHECKPOINT_START_LEGAL_PHASES`.

        Also pins that this guard is not made redundant by
        `claim_checkpoint`'s own newer, round-8 phase check (missing-test
        item 3, round 9 external implementation review): this call goes
        directly through `transition_checkpoint_in_progress` with no
        `claim_checkpoint` call anywhere in this test, which is exactly the
        shape three real call sites take in production --
        `.claude/commands/milestone-implement.md`'s `CONTINUE_CLAIM`/
        `RESUME` branches (the claim already exists from an earlier step
        1c, so `claim_checkpoint` is never called again), `adopt_claim`
        (publishes through `_claim_or_refuse` directly), and
        `take_over_claim` (publishes through `_publish_claim_replacing`).
        Removing this guard on the theory that `claim_checkpoint`'s own
        check "already covers it" would silently reopen the
        `IN_PROGRESS`-after-`AMENDING_PLAN` write on all three."""
        wi = _base_work_item(phase="AMENDING_PLAN", current_checkpoint_id=None, checkpoints={})
        state = _base_state(wi=wi)
        with self.assertRaises(ws.IllegalCheckpointStartPhaseError):
            ws.transition_checkpoint_in_progress(state, "wi", "A", "deadbeef", now="t1")
        # Refused before any write: the original checkpoints map is untouched.
        self.assertEqual(state["work_items"]["wi"]["checkpoints"], {})

    def test_complete_checkpoint_stays_implementing_when_others_remain(self):
        """Checkpoint-complete-vs-all-complete semantics: completing one
        checkpoint out of several never itself flips the phase."""
        wi = _base_work_item(
            current_checkpoint_id="A", checkpoints={"A": {"status": "IN_PROGRESS", "start_commit": "x"}},
            phase="IMPLEMENTING", state_revision=1,
        )
        state = _base_state(wi=wi)
        new_state = ws.complete_checkpoint(state, "wi", "A", _REGISTRY, now="t2", repo_root=Path("."))
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["checkpoints"]["A"]["status"], "COMPLETE")
        self.assertIsNone(item["current_checkpoint_id"])
        self.assertEqual(item["last_completed_checkpoint_id"], "A")
        self.assertEqual(item["phase"], "IMPLEMENTING")

    def test_complete_checkpoint_transitions_phase_when_all_complete(self):
        registry = {"checkpoints": [{"id": "A", "depends_on": []}]}
        wi = _base_work_item(
            current_checkpoint_id="A", checkpoints={"A": {"status": "IN_PROGRESS", "start_commit": "x"}},
            phase="IMPLEMENTING", state_revision=1,
        )
        state = _base_state(wi=wi)
        new_state = ws.complete_checkpoint(state, "wi", "A", registry, now="t2", repo_root=Path("."))
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "SELF_REVIEWING_IMPLEMENTATION")


class TestEnterSelfReviewingImplementation(unittest.TestCase):
    """Salvage audit `B8`: `SELF_REVIEWING_IMPLEMENTATION`'s second writer,
    the one `/milestone-implement` step 2 and `/bootstrap-workflow-v2`'s
    `NO_CHECKPOINT` wrap-up branch call. Before it existed,
    `complete_checkpoint` was the phase's sole writer and could only fire
    as a side effect of a checkpoint *transitioning* to `COMPLETE`, so a
    work item whose checkpoints were all already `COMPLETE` when
    `apply_plan_approval` wrote `IMPLEMENTING` had no way out."""

    REGISTRY = {"checkpoints": [{"id": "A", "depends_on": []},
                                {"id": "B", "depends_on": ["A"]}]}

    def _state(self, *, phase, a="COMPLETE", b="COMPLETE", state_revision=7):
        return _base_state(wi=_base_work_item(
            phase=phase, state_revision=state_revision,
            checkpoints={"A": {"status": a}, "B": {"status": b}},
        ))

    def test_transitions_from_implementing_when_every_checkpoint_is_complete(self):
        state = self._state(phase="IMPLEMENTING")
        new_state = ws.enter_self_reviewing_implementation(state, "wi", self.REGISTRY, "t9")
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "SELF_REVIEWING_IMPLEMENTATION")
        self.assertEqual(item["state_revision"], 8)
        self.assertEqual(item["last_transition"], "t9")
        # The input is never mutated -- same contract as every sibling writer.
        self.assertEqual(state["work_items"]["wi"]["phase"], "IMPLEMENTING")
        self.assertEqual(state["work_items"]["wi"]["state_revision"], 7)

    def test_already_self_reviewing_is_a_true_no_op(self):
        """The ordinary path re-enters the wrap-up branch on every
        invocation until the bundle is generated. A writer that bumped
        `state_revision` each time would manufacture a state change out of
        a read-only re-check."""
        state = self._state(phase="SELF_REVIEWING_IMPLEMENTATION")
        new_state = ws.enter_self_reviewing_implementation(state, "wi", self.REGISTRY, "t9")
        self.assertIs(new_state, state)
        self.assertEqual(new_state["work_items"]["wi"]["state_revision"], 7)
        self.assertNotEqual(new_state["work_items"]["wi"].get("last_transition"), "t9")

    def test_outstanding_checkpoint_refuses_and_names_it(self):
        state = self._state(phase="IMPLEMENTING", b="IN_PROGRESS")
        with self.assertRaises(ws.IncompleteCheckpointsForSelfReviewError) as ctx:
            ws.enter_self_reviewing_implementation(state, "wi", self.REGISTRY, "t9")
        self.assertEqual(ctx.exception.outstanding_checkpoint_id, "B")
        self.assertIn("'B'", str(ctx.exception))

    def test_untracked_checkpoint_refuses(self):
        """A registry entry the work item has never recorded at all is
        outstanding exactly like an `IN_PROGRESS` one."""
        state = _base_state(wi=_base_work_item(
            phase="IMPLEMENTING", checkpoints={"A": {"status": "COMPLETE"}},
        ))
        with self.assertRaises(ws.IncompleteCheckpointsForSelfReviewError) as ctx:
            ws.enter_self_reviewing_implementation(state, "wi", self.REGISTRY, "t9")
        self.assertEqual(ctx.exception.outstanding_checkpoint_id, "B")

    def test_blocked_checkpoint_refuses_rather_than_raising_selection_rule_4(self):
        """`registry_completion_status` absorbs `NoCheckpointReadyError`
        and reports the blocked checkpoint, so this writer's own refusal
        is the one the caller sees."""
        registry = {"checkpoints": [{"id": "A", "depends_on": ["Z"]}]}
        state = _base_state(wi=_base_work_item(phase="IMPLEMENTING", checkpoints={}))
        with self.assertRaises(ws.IncompleteCheckpointsForSelfReviewError) as ctx:
            ws.enter_self_reviewing_implementation(state, "wi", registry, "t9")
        self.assertEqual(ctx.exception.outstanding_checkpoint_id, "A")

    def test_every_other_phase_refuses_by_name(self):
        for phase in (
            "PLANNING", "AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_PLAN_APPROVAL",
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK",
            "AWAITING_FUNCTIONAL_REVIEW", "MILESTONE_COMPLETE", "LEGACY_READY",
        ):
            with self.subTest(phase=phase):
                state = self._state(phase=phase)
                with self.assertRaises(ws.IllegalSelfReviewEntryPhaseError) as ctx:
                    ws.enter_self_reviewing_implementation(state, "wi", self.REGISTRY, "t9")
                self.assertIn(repr(phase), str(ctx.exception))
                self.assertIn("IMPLEMENTING", str(ctx.exception))

    def test_agrees_with_complete_checkpoints_own_all_complete_predicate(self):
        """The two writers of this phase must never disagree about when it
        is legal: whatever `complete_checkpoint` would have transitioned
        on, this writer accepts, and nothing else."""
        for a, b in (("COMPLETE", "COMPLETE"), ("COMPLETE", "IN_PROGRESS"),
                     ("IN_PROGRESS", "COMPLETE")):
            with self.subTest(a=a, b=b):
                all_complete = a == "COMPLETE" and b == "COMPLETE"
                state = self._state(phase="IMPLEMENTING", a=a, b=b)
                if all_complete:
                    result = ws.enter_self_reviewing_implementation(
                        state, "wi", self.REGISTRY, "t9",
                    )
                    self.assertEqual(
                        result["work_items"]["wi"]["phase"], "SELF_REVIEWING_IMPLEMENTATION",
                    )
                else:
                    with self.assertRaises(ws.IncompleteCheckpointsForSelfReviewError):
                        ws.enter_self_reviewing_implementation(
                            state, "wi", self.REGISTRY, "t9",
                        )

    def test_the_phase_it_writes_is_a_legal_bundle_generation_source(self):
        """The whole point of the transition: `record_bundle_generation(
        stage="implementation")` must accept the resulting state, and must
        have refused the one it started from."""
        state = self._state(phase="IMPLEMENTING")
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError):
            ws.record_bundle_generation(
                state, "wi", stage="implementation", head="h", now="t",
            )
        transitioned = ws.enter_self_reviewing_implementation(
            state, "wi", self.REGISTRY, "t9",
        )
        published = ws.record_bundle_generation(
            transitioned, "wi", stage="implementation", head="h", now="t10",
        )
        self.assertEqual(
            published["work_items"]["wi"]["phase"],
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        )


_RECONCILIATION_TABLE_FIXTURE = """### Reconciliation table

| Items | Topic | Status | Owner | Evidence / rationale |
|---|---|---|---|---|
| 1 | thing one | IMPLEMENTED | WF8b | already delivered |
| 2 | thing two | ABSENT | WF8c | still missing |
| 3 | thing three | SUPERSEDED | none (superseded) | design superseded |
"""

_EVIDENCE_TEST_MODULE = """import unittest

class OkCase(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)

class BrokenCase(unittest.TestCase):
    def test_fail(self):
        self.assertTrue(False)
"""


class TestCompleteCheckpointWfr69PreFlight(unittest.TestCase):
    """`WFR-69` (`WF8c` item (o)): `complete_checkpoint`'s own
    non-approval-gated pre-flight for a checkpoint whose registry entry
    declares `completion_obligations`, re-derived directly against a
    real filesystem fixture standing in for the live working tree -- no
    Git repository is needed since the pre-flight never shells out to
    Git at all."""

    def _write_fixture(self, root: Path, *, table: str = _RECONCILIATION_TABLE_FIXTURE) -> None:
        plan_dir = root / "docs" / "ai-workflow"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "WORKFLOW_V2_PLAN.md").write_text(table)
        (root / "docs" / "ai-workflow" / "registry").mkdir(parents=True, exist_ok=True)
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "fixture_evidence_test.py").write_text(_EVIDENCE_TEST_MODULE)

    def _registry(self) -> dict:
        return {"checkpoints": [
            {"id": "A", "depends_on": [], "completion_obligations": ["WFO-LEDGER-COVERAGE"]},
        ]}

    def _state(self) -> dict:
        wi = _base_work_item(
            current_checkpoint_id="A", checkpoints={"A": {"status": "IN_PROGRESS", "start_commit": "x"}},
            phase="IMPLEMENTING", state_revision=1,
        )
        return _base_state(wi=wi)

    def test_refuses_when_no_evidence_is_recorded_for_any_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            with self.assertRaises(ws.UnsatisfiedCompletionObligationError) as ctx:
                ws.complete_checkpoint(self._state(), "wi", "A", self._registry(), now="t2", repo_root=root)
            message = str(ctx.exception)
            self.assertIn("1", message)
            self.assertIn("2", message)
            # Item 3 resolves SUPERSEDED/none -- satisfied without evidence.
            self.assertNotIn("[1, 2, 3]", message)
            self.assertIn("[1, 2]", message)

    def test_passes_when_every_open_item_has_passing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            ledger = {
                "entries": [
                    {"item": 1, "status": "IMPLEMENTED", "owner_checkpoint": "WF8b",
                     "evidence": "fixture_evidence_test.OkCase.test_pass"},
                    {"item": 3, "status": "SUPERSEDED", "owner_checkpoint": "none", "evidence": None},
                ]
            }
            (root / ws.ledger_status_path_for_work_item("wi")).write_text(json.dumps(ledger))
            companion = {"entries": [
                {"item": 2, "evidence": "fixture_evidence_test.OkCase.test_pass"},
            ]}
            (root / ws.wf8c_evidence_path_for_work_item("wi")).write_text(json.dumps(companion))

            new_state = ws.complete_checkpoint(self._state(), "wi", "A", self._registry(), now="t2", repo_root=root)
            self.assertEqual(new_state["work_items"]["wi"]["checkpoints"]["A"]["status"], "COMPLETE")

    def test_relative_repo_root_produces_the_same_result_as_absolute(self):
        """OPUS-R129-M01: `_load_and_run_named_test` builds `scripts_dir =
        Path(repo_root) / "scripts"` and uses that *same relative* value
        two ways: as the evidence subprocess's own `cwd` (resolved once,
        correctly, against the calling process's cwd), and as the literal
        string written into the driver's `sys.path.insert(0, ...)` line,
        which Python then resolves a *second* time -- against the child's
        own cwd, which `cwd=` already moved inside `scripts_dir`. Passing
        `repo_root=Path(".")` while the caller's own cwd is the fixture
        root reproduces this exactly: the relative `"scripts"` segment
        gets applied twice, landing on a nonexistent `<root>/scripts/scripts`
        and failing every evidence lookup closed -- not because anything
        is genuinely missing, but purely from the double-relative
        resolution. `complete_checkpoint` now resolves `repo_root` once,
        before any pre-flight runs, closing this regardless of the
        caller's own cwd or how `repo_root` was spelled."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            ledger = {
                "entries": [
                    {"item": 1, "status": "IMPLEMENTED", "owner_checkpoint": "WF8b",
                     "evidence": "fixture_evidence_test.OkCase.test_pass"},
                    {"item": 3, "status": "SUPERSEDED", "owner_checkpoint": "none", "evidence": None},
                ]
            }
            (root / ws.ledger_status_path_for_work_item("wi")).write_text(json.dumps(ledger))
            companion = {"entries": [
                {"item": 2, "evidence": "fixture_evidence_test.OkCase.test_pass"},
            ]}
            (root / ws.wf8c_evidence_path_for_work_item("wi")).write_text(json.dumps(companion))

            original_cwd = os.getcwd()
            os.chdir(root)
            try:
                new_state = ws.complete_checkpoint(
                    self._state(), "wi", "A", self._registry(), now="t2", repo_root=Path("."),
                )
            finally:
                os.chdir(original_cwd)
            self.assertEqual(new_state["work_items"]["wi"]["checkpoints"]["A"]["status"], "COMPLETE")

    def test_flags_item_whose_evidence_test_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            ledger = {"entries": [
                {"item": 1, "status": "IMPLEMENTED", "owner_checkpoint": "WF8b",
                 "evidence": "fixture_evidence_test.BrokenCase.test_fail"},
                {"item": 2, "status": "ABSENT", "owner_checkpoint": "WF8c",
                 "evidence": "fixture_evidence_test.OkCase.test_pass"},
            ]}
            (root / ws.ledger_status_path_for_work_item("wi")).write_text(json.dumps(ledger))

            with self.assertRaises(ws.UnsatisfiedCompletionObligationError) as ctx:
                ws.complete_checkpoint(self._state(), "wi", "A", self._registry(), now="t2", repo_root=root)
            message = str(ctx.exception)
            self.assertIn("[1]", message)

    def test_flags_item_whose_evidence_reference_is_unresolvable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)
            ledger = {"entries": [
                {"item": 1, "status": "IMPLEMENTED", "owner_checkpoint": "WF8b",
                 "evidence": "fixture_evidence_test.NoSuchCase.test_nope"},
                {"item": 2, "status": "ABSENT", "owner_checkpoint": "WF8c",
                 "evidence": "fixture_evidence_test.OkCase.test_pass"},
            ]}
            (root / ws.ledger_status_path_for_work_item("wi")).write_text(json.dumps(ledger))

            with self.assertRaises(ws.UnsatisfiedCompletionObligationError) as ctx:
                ws.complete_checkpoint(self._state(), "wi", "A", self._registry(), now="t2", repo_root=root)
            self.assertIn("[1]", str(ctx.exception))

    def test_obligation_with_no_bound_pre_flight_fails_closed(self):
        registry = {"checkpoints": [
            {"id": "A", "depends_on": [], "completion_obligations": ["SOME-UNBOUND-OBLIGATION"]},
        ]}
        with self.assertRaises(ws.UnsatisfiedCompletionObligationError) as ctx:
            ws.complete_checkpoint(self._state(), "wi", "A", registry, now="t2", repo_root=Path("/nonexistent"))
        self.assertIn("SOME-UNBOUND-OBLIGATION", str(ctx.exception))
        self.assertIn("no WFR-69 pre-flight bound", str(ctx.exception))

    def test_checkpoint_declaring_no_obligations_is_unaffected_by_missing_fixture(self):
        """A checkpoint that declares no `completion_obligations` at all
        never even looks at `repo_root` -- the pre-flight is vacuously
        satisfied for it, matching every registry row besides `WF8c`."""
        registry = {"checkpoints": [{"id": "A", "depends_on": []}]}
        new_state = ws.complete_checkpoint(
            self._state(), "wi", "A", registry, now="t2", repo_root=Path("/definitely/does/not/exist"),
        )
        self.assertEqual(new_state["work_items"]["wi"]["checkpoints"]["A"]["status"], "COMPLETE")


class TestReconciliationTableWorkingTreeParse(unittest.TestCase):
    def test_parses_live_working_tree_file_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_dir = root / "docs" / "ai-workflow"
            plan_dir.mkdir(parents=True)
            (plan_dir / "WORKFLOW_V2_PLAN.md").write_text(_RECONCILIATION_TABLE_FIXTURE)
            table = ws._parse_reconciliation_table_working_tree(root)
        self.assertEqual(table[1]["status"], "IMPLEMENTED")
        self.assertEqual(table[1]["owner"], "WF8b")
        self.assertEqual(table[2]["status"], "ABSENT")
        self.assertEqual(table[2]["owner"], "WF8c")
        self.assertEqual(table[3]["status"], "SUPERSEDED")
        self.assertEqual(table[3]["owner"], "none")


class TestWorktreeIdentityWriteAndResume(unittest.TestCase):
    def test_write_creates_file_and_resume_then_succeeds(self):
        """Missing-test item 43: the IN_PROGRESS transition creates
        WORKTREE_IDENTITY.json, and resume against the same worktree
        succeeds."""
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t1")
            self.assertTrue((repo.root / ws.WORKTREE_IDENTITY_PATH).exists())
            ws.verify_dirty_resume_safety(repo.root, "wi")  # must not raise

    def test_resume_with_missing_file_stops(self):
        with ScratchRepo() as repo:
            with self.assertRaises(ws.WorktreeIdentityMissingError):
                ws.verify_dirty_resume_safety(repo.root, "wi")

    def test_resume_for_a_different_work_item_stops(self):
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi-a", now="t1")
            with self.assertRaises(ws.WorktreeIdentityMissingError):
                ws.verify_dirty_resume_safety(repo.root, "wi-b")

    def test_two_work_items_keep_independently_keyed_entries(self):
        """Missing-test item 71: two work items with interleaved
        IN_PROGRESS dirty work each resume against their own keyed
        expected set -- writing wi-b's entry must not disturb wi-a's."""
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi-a", now="t1")
            ws.write_worktree_identity(repo.root, "wi-b", now="t2")
            data = ws._load_json(repo.root / ws.WORKTREE_IDENTITY_PATH)
            self.assertIn("wi-a", data["expected_dirty_paths_by_work_item"])
            self.assertIn("wi-b", data["expected_dirty_paths_by_work_item"])
            ws.verify_dirty_resume_safety(repo.root, "wi-a")  # must not raise
            ws.verify_dirty_resume_safety(repo.root, "wi-b")  # must not raise

    def test_mismatched_worktree_identity_stops(self):
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t1")
            full_path = repo.root / ws.WORKTREE_IDENTITY_PATH
            data = json.loads(full_path.read_text())
            data["repo_root"] = "/somewhere/else"
            full_path.write_text(json.dumps(data))
            with self.assertRaises(ws.WorktreeIdentityMismatchError):
                ws.verify_dirty_resume_safety(repo.root, "wi")

    def test_snapshot_captures_current_dirty_paths_with_content_hash(self):
        import hashlib
        with ScratchRepo() as repo:
            (repo.root / "dirty.txt").write_text("wip content\n")
            written = ws.write_worktree_identity(repo.root, "wi", now="t1")
            entries = written["expected_dirty_paths_by_work_item"]["wi"]
            self.assertEqual(
                entries,
                [{"path": "dirty.txt", "sha256": hashlib.sha256(b"wip content\n").hexdigest()}],
            )

    def test_resume_succeeds_even_after_dirty_set_changes(self):
        """The resume check never re-compares the snapshot's per-path
        content against the current dirty state (missing-test item 31's
        underlying reason: the dirty set legitimately keeps changing while
        a checkpoint is in progress)."""
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t1")
            (repo.root / "more_wip.txt").write_text("more work\n")
            ws.verify_dirty_resume_safety(repo.root, "wi")  # must not raise


# ---------------------------------------------------------------------------
# WF4c: D-Functional-Remediation -- stale-before-edit write,
# reviewed_implementation_head's sole writer, and the broad-remediation
# child-work-item branch plus its parent-completion block.
# ---------------------------------------------------------------------------


class TestMarkTechnicalApprovalStale(unittest.TestCase):
    def _approved_work_item(self, **overrides):
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="implementation", user_confirmation="approve wi implementation",
            now="t1", reviewed_bundle_id="b", approved_review_content_id="c",
            review_content_manifest=[], reviewed_content_commit="deadbeef",
        )
        return _base_work_item(technical_approval=record, **overrides)

    def test_marks_status_stale_and_leaves_other_fields_untouched(self):
        wi = self._approved_work_item()
        state = _base_state(wi=wi)
        new_state = ws.mark_technical_approval_stale(state, "wi", now="t2")
        record = new_state["work_items"]["wi"]["technical_approval"]
        self.assertEqual(record["status"], "STALE")
        self.assertEqual(record["approved_review_content_id"], "c")
        self.assertEqual(record["basis"], "EXTERNAL_APPROVE")
        # original state untouched (stale-before-edit ordering requires a
        # fresh dict the caller can persist independently of the input)
        self.assertEqual(state["work_items"]["wi"]["technical_approval"]["status"], "CURRENT")

    def test_bumps_state_revision_and_last_transition(self):
        wi = self._approved_work_item(state_revision=1)
        state = _base_state(wi=wi)
        new_state = ws.mark_technical_approval_stale(state, "wi", now="t2")
        self.assertEqual(new_state["work_items"]["wi"]["state_revision"], 2)
        self.assertEqual(new_state["work_items"]["wi"]["last_transition"], "t2")

    def test_no_existing_technical_approval_rejected(self):
        state = _base_state(wi=_base_work_item())
        with self.assertRaises(ws.InvalidApprovalRecordError):
            ws.mark_technical_approval_stale(state, "wi", now="t2")


class TestRecordBundleGeneration(unittest.TestCase):
    def test_first_implementation_stage_call_sets_head_and_revision_one(self):
        state = _base_state(wi=_base_work_item(
            phase="SELF_REVIEWING_IMPLEMENTATION",
            reviewed_implementation_head=None, implementation_revision=None,
        ))
        new_state = ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["reviewed_implementation_head"], "abc123")
        self.assertEqual(wi["implementation_revision"], 1)

    def test_post_fix_call_advances_head_and_increments_revision(self):
        state = _base_state(wi=_base_work_item(
            phase="APPLYING_REVIEW_FEEDBACK",
            reviewed_implementation_head="abc123", implementation_revision=1,
        ))
        new_state = ws.record_bundle_generation(state, "wi", stage="post-fix", head="def456", now="t2")
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["reviewed_implementation_head"], "def456")
        self.assertEqual(wi["implementation_revision"], 2)

    def test_self_reviewing_implementation_with_changed_content_uses_ordinary_path(self):
        """Item 310 (`WF8c`, `GPT-R54-002`): `SELF_REVIEWING_IMPLEMENTATION`
        becoming a legal same-content source phase (revision 38, reached
        when a later checkpoint's own self-review round returns to this
        phase against an already-reviewed prior round) must not disturb
        its pre-existing ordinary-outcome behavior when that later round's
        content has genuinely changed from the currently-reviewed one --
        `outcome="ordinary"` still records live `head` as
        `reviewed_implementation_head` and advances `implementation_revision`
        by exactly one, exactly as it always has from either legal source
        phase."""
        state = _base_state(wi=_base_work_item(
            phase="SELF_REVIEWING_IMPLEMENTATION",
            reviewed_implementation_head="abc123", implementation_revision=1,
        ))
        new_state = ws.record_bundle_generation(
            state, "wi", stage="implementation", head="def456", now="t2", outcome="ordinary",
        )
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["reviewed_implementation_head"], "def456")
        self.assertEqual(wi["implementation_revision"], 2)
        self.assertEqual(wi["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

    def test_unknown_stage_rejected(self):
        state = _base_state(wi=_base_work_item())
        with self.assertRaises(ws.InvalidBundleGenerationStageError):
            ws.record_bundle_generation(state, "wi", stage="plan", head="abc123", now="t1")

    def test_original_state_untouched(self):
        state = _base_state(wi=_base_work_item(
            phase="SELF_REVIEWING_IMPLEMENTATION",
            reviewed_implementation_head=None, implementation_revision=None,
        ))
        ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        self.assertIsNone(state["work_items"]["wi"]["reviewed_implementation_head"])

    def test_first_call_writes_target_phase(self):
        """OPUS-R101-001: `record_bundle_generation` must itself write
        `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, asserted from the
        returned state (the caller persists it, mirroring the committed
        blob a fresh session would re-read)."""
        state = _base_state(wi=_base_work_item(phase="SELF_REVIEWING_IMPLEMENTATION"))
        new_state = ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        self.assertEqual(
            new_state["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        )

    def test_post_fix_call_writes_target_phase(self):
        state = _base_state(wi=_base_work_item(phase="APPLYING_REVIEW_FEEDBACK"))
        new_state = ws.record_bundle_generation(state, "wi", stage="post-fix", head="def456", now="t2")
        self.assertEqual(
            new_state["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        )

    def test_illegal_source_phase_refused_naming_actual_and_legal_phases(self):
        """Stage-specific since workflow-v2-3-followups's own continued
        scope widened legality per stage: `stage="implementation"`'s only
        legal source is `SELF_REVIEWING_IMPLEMENTATION` -- the refusal
        message for it must name only that, never `APPLYING_REVIEW_FEEDBACK`
        (a `post-fix`-only source since the widening, never legal for a
        round's first bundle)."""
        state = _base_state(wi=_base_work_item(phase="IMPLEMENTING"))
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError) as ctx:
            ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        self.assertIn("IMPLEMENTING", str(ctx.exception))
        self.assertIn("SELF_REVIEWING_IMPLEMENTATION", str(ctx.exception))
        self.assertNotIn("APPLYING_REVIEW_FEEDBACK", str(ctx.exception))

    def test_post_fix_illegal_source_phase_names_its_own_legal_phases(self):
        """The `stage="post-fix"` counterpart: its own legal set is
        `{APPLYING_REVIEW_FEEDBACK, AWAITING_FUNCTIONAL_REVIEW}` --
        `SELF_REVIEWING_IMPLEMENTATION` (legal only for `stage=
        "implementation"`) must never appear in this refusal's message."""
        state = _base_state(wi=_base_work_item(phase="IMPLEMENTING"))
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError) as ctx:
            ws.record_bundle_generation(state, "wi", stage="post-fix", head="abc123", now="t1")
        self.assertIn("IMPLEMENTING", str(ctx.exception))
        self.assertIn("APPLYING_REVIEW_FEEDBACK", str(ctx.exception))
        self.assertIn("AWAITING_FUNCTIONAL_REVIEW", str(ctx.exception))
        self.assertNotIn("SELF_REVIEWING_IMPLEMENTATION", str(ctx.exception))

    def test_post_fix_from_illegal_source_phase_also_refused(self):
        state = _base_state(wi=_base_work_item(phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"))
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError):
            ws.record_bundle_generation(state, "wi", stage="post-fix", head="def456", now="t2")

    def test_bundle_generation_target_and_recovery_phase_pair_are_both_reachable(self):
        """Narrower guard than OPUS-R101-001's own suggested blanket
        all-17-phases sweep (see IMPLEMENTATION_SUMMARY.md for why that
        broader test was not added as-is): the specific pair this finding's
        reproduction is about -- `record_bundle_generation`'s target
        (`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) and
        `enter_applying_review_feedback`'s target
        (`APPLYING_REVIEW_FEEDBACK`) -- must each be written by a real,
        named module-level function, not merely declared in `KNOWN_PHASES`."""
        import ast
        source = Path(ws.__file__).read_text()
        tree = ast.parse(source)
        written_phases: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].slice, ast.Constant)
                and node.targets[0].slice.value == "phase"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                written_phases.add(node.value.value)
        self.assertIn("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", written_phases)
        self.assertIn("APPLYING_REVIEW_FEEDBACK", written_phases)

    def test_end_to_end_implementation_round_reaches_external_review_durably(self):
        """Item 273 (`WF8c`): a real pre-bundle implementation round --
        starting from `SELF_REVIEWING_IMPLEMENTATION` -- exercises
        `record_bundle_generation` end to end: immediately after the
        durability commit `S` lands, `WORKFLOW_STATE.json` durably reads
        `phase == AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`; `S`
        independently passes the same role validator
        (`validate_bundle_generation_record_commit`) `/approve-review
        implementation` later uses; and a fresh session -- re-derived
        directly from Git via a separate read, never from this process's
        own in-memory return value -- sees the same external-review
        phase."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, "wi")
            _seed_base_provenance_state(repo, "wi")
            p = repo.commit("protected fix", filename="src/Foo.kt")
            pre_state = _base_state(wi={
                "work_item_id": "wi",
                "reviewed_implementation_head": None,
                "implementation_revision": None,
                "phase": "SELF_REVIEWING_IMPLEMENTATION",
                "state_revision": 0,
                "last_transition": "t0",
            })
            post_state = ws.record_bundle_generation(
                pre_state, "wi", stage="implementation", head=p, now="t1",
            )
            wi_after = post_state["work_items"]["wi"]
            s = _commit_state_only(
                repo, "wi", wi_after, "record gen",
                trailers=_record_trailers("wi", wi_after["implementation_revision"]),
            )

            durable = ws._read_json_at_commit_or_empty(repo.root, s, STATE_REL_PATH)
            self.assertEqual(
                durable["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            )
            ws.validate_bundle_generation_record_commit(repo.root, s, "wi")  # must not raise

            fresh_session = ws._read_json_at_commit_or_empty(repo.root, "HEAD", STATE_REL_PATH)
            self.assertEqual(
                fresh_session["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            )

    def test_post_fix_end_to_end_round_converges_on_the_same_target_phase(self):
        """Item 274 (`WF8c`): the same end-to-end flow as item 273,
        exercised from the post-fix source phase
        `APPLYING_REVIEW_FEEDBACK` after a `REVISE` round -- produces the
        identical target phase (`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`)
        and passes the same role validator, confirming the two legal
        source phases converge on one target."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, "wi")
            _seed_base_provenance_state(repo, "wi")
            p1 = repo.commit("protected fix round 1", filename="src/Foo.kt")
            pre_round_one = _base_state(wi={
                "work_item_id": "wi",
                "reviewed_implementation_head": None,
                "implementation_revision": None,
                "phase": "SELF_REVIEWING_IMPLEMENTATION",
                "state_revision": 0,
                "last_transition": "t0",
            })
            round_one = ws.record_bundle_generation(
                pre_round_one, "wi", stage="implementation", head=p1, now="t1",
            )
            wi_round_one = round_one["work_items"]["wi"]
            _commit_state_only(
                repo, "wi", wi_round_one, "record gen round 1",
                trailers=_record_trailers("wi", wi_round_one["implementation_revision"]),
            )

            # REVISE round: the reviewer sends feedback, work resumes via
            # the dedicated APPLYING_REVIEW_FEEDBACK writer.
            feedback_intermediate = ws.enter_applying_review_feedback(round_one, "wi", now="t1b")
            wi_feedback = feedback_intermediate["work_items"]["wi"]
            _commit_state_only(repo, "wi", wi_feedback, "enter applying review feedback")
            p2 = repo.commit("protected fix round 2", filename="src/Foo.kt")

            round_two = ws.record_bundle_generation(
                feedback_intermediate, "wi", stage="post-fix", head=p2, now="t2",
            )
            wi_round_two = round_two["work_items"]["wi"]
            s2 = _commit_state_only(
                repo, "wi", wi_round_two, "record gen round 2",
                trailers=_record_trailers("wi", wi_round_two["implementation_revision"]),
            )

            self.assertEqual(wi_round_two["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
            durable = ws._read_json_at_commit_or_empty(repo.root, s2, STATE_REL_PATH)
            self.assertEqual(
                durable["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            )
            ws.validate_bundle_generation_record_commit(repo.root, s2, "wi")  # must not raise

    def test_interrupted_before_durability_commit_resumes_deterministically(self):
        """Item 226 (`WF8c`): a simulated interruption after
        `record_bundle_generation`'s state write but before the durability
        commit `S` lands leaves the prior round's state authoritative --
        nothing durable ever changed, so a fresh session still reads the
        same starting state and re-attempting from it is deterministic,
        never doubly advancing `implementation_revision` on top of an
        attempt that was never persisted."""
        state = _base_state(wi=_base_work_item(
            phase="SELF_REVIEWING_IMPLEMENTATION",
            reviewed_implementation_head=None, implementation_revision=None,
        ))
        interrupted = ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        # The interrupted attempt's write was never persisted or committed
        # -- record_bundle_generation is a pure state -> state mutator, so
        # the original `state` a fresh session would still be holding is
        # untouched by it.
        self.assertIsNone(state["work_items"]["wi"]["implementation_revision"])
        resumed = ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        self.assertEqual(interrupted, resumed)
        self.assertEqual(resumed["work_items"]["wi"]["implementation_revision"], 1)


class TestFunctionalReviewBoundedFixReachesRecordBundleGeneration(unittest.TestCase):
    """workflow-v2-3-followups continued scope (self-discovered during
    this item's own `/accept-milestone` pre-flight): the real end-to-end
    sequence `/apply-functional-review`'s own "bounded code change" branch
    drives -- `AWAITING_FUNCTIONAL_REVIEW` with a `CURRENT` technical_
    approval -> `mark_technical_approval_stale` -> a bounded fix ->
    `record_bundle_generation(stage="post-fix", ...)` ->
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` -- was never exercised end
    to end before this widening. OPUS-R101-001's phase-transition
    contract (landed 2026-08-15 15:06) made this structurally unreachable
    for the only phase this branch is ever actually invoked from: the
    real prior exercise of this branch, `v2-1-dry-run`'s S10 scenario
    (commits `fae7420`/`c11ec01`, both 2026-08-15 10:44-11:06), ran
    *before* that contract landed and was never re-tested against it."""

    WI = "wi"

    def _approved_technical_approval(self, reviewed_content_commit: str) -> dict:
        return ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="implementation",
            user_confirmation="approve wi implementation", now="t0",
            reviewed_bundle_id="b1", approved_review_content_id="c1",
            review_content_manifest=[
                {"path": "src/Foo.kt", "exists": True, "mode": "100644", "blob": "deadbeef"},
            ],
            reviewed_content_commit=reviewed_content_commit,
        )

    def test_bounded_fix_from_awaiting_functional_review_reaches_external_review_durably(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p1 = repo.commit("protected content, round 1", filename="src/Foo.kt")

            approved = self._approved_technical_approval(p1)
            self.assertEqual(approved["status"], "CURRENT")
            pre_fix_state = _base_state(wi={
                "work_item_id": self.WI,
                "work_item_type": "process",
                "phase": "AWAITING_FUNCTIONAL_REVIEW",
                "technical_approval": approved,
                "reviewed_implementation_head": p1,
                "implementation_revision": 1,
                "state_revision": 1,
                "last_transition": "t0",
            })

            # 1. Stale-before-edit ordering: mark stale and persist it as
            # its own commit -- this becomes the generation-record
            # commit's own git parent below.
            staled = ws.mark_technical_approval_stale(pre_fix_state, self.WI, now="t1")
            wi_staled = staled["work_items"][self.WI]
            self.assertEqual(wi_staled["technical_approval"]["status"], "STALE")
            self.assertEqual(wi_staled["phase"], "AWAITING_FUNCTIONAL_REVIEW")
            _commit_state_only(repo, self.WI, wi_staled, "mark technical approval stale")

            # 2. The bounded fix itself -- a real protected-content commit,
            # touching no state (mirrors /apply-functional-review's own
            # "commit the fix" step, separate from the durability commit).
            p2 = repo.commit("bounded fix", filename="src/Foo.kt")

            # 3. resolve_bundle_generation_outcome + record_bundle_generation
            # (post-fix stage), exactly as /apply-functional-review's
            # bounded branch drives them.
            outcome, _ = ws.resolve_bundle_generation_outcome(
                repo.root, wi_staled, base_commit=repo.base, head=p2,
            )
            self.assertEqual(outcome, "ordinary")
            post_fix = ws.record_bundle_generation(
                staled, self.WI, stage="post-fix", head=p2, now="t2", outcome=outcome,
            )
            wi_post_fix = post_fix["work_items"][self.WI]
            self.assertEqual(wi_post_fix["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
            self.assertEqual(wi_post_fix["reviewed_implementation_head"], p2)
            self.assertEqual(wi_post_fix["implementation_revision"], 2)

            # 4. The durability commit -- touches only WORKFLOW_STATE.json,
            # its git parent is the STALE-marking commit from step 1 (the
            # bounded-fix commit in between touched no state, so the state
            # file's own bytes are unchanged between them).
            s = _commit_state_only(
                repo, self.WI, wi_post_fix, "record gen (post-fix)",
                trailers=_record_trailers(self.WI, 2),
            )
            ws.validate_bundle_generation_record_commit(repo.root, s, self.WI)  # must not raise

            durable = ws._read_json_at_commit_or_empty(repo.root, s, STATE_REL_PATH)
            self.assertEqual(
                durable["work_items"][self.WI]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            )

    def test_post_fix_from_awaiting_functional_review_refused_unless_stale(self):
        """Regression requirement 2: the bounded-fix marker
        (`technical_approval.status == "STALE"`) is a hard precondition,
        not merely the phase -- a `CURRENT` approval at
        `AWAITING_FUNCTIONAL_REVIEW` means no bounded fix is actually in
        flight."""
        state = _base_state(wi={
            "work_item_id": self.WI,
            "phase": "AWAITING_FUNCTIONAL_REVIEW",
            "technical_approval": self._approved_technical_approval("abc123"),
            "reviewed_implementation_head": "abc123",
            "implementation_revision": 1,
        })
        self.assertEqual(state["work_items"][self.WI]["technical_approval"]["status"], "CURRENT")
        with self.assertRaises(ws.BundleGenerationRequiresStaleTechnicalApprovalError) as ctx:
            ws.record_bundle_generation(state, self.WI, stage="post-fix", head="def456", now="t2")
        self.assertIn("CURRENT", str(ctx.exception))

    def test_post_fix_from_awaiting_functional_review_with_no_technical_approval_refused(self):
        state = _base_state(wi=_base_work_item(phase="AWAITING_FUNCTIONAL_REVIEW"))
        with self.assertRaises(ws.BundleGenerationRequiresStaleTechnicalApprovalError):
            ws.record_bundle_generation(state, "wi", stage="post-fix", head="def456", now="t2")

    def test_ordinary_implementation_stage_generation_still_refused_from_awaiting_functional_review(self):
        """Regression requirement 3: widening `stage="post-fix"`'s
        legality must never widen `stage="implementation"`'s -- a round's
        first bundle can never legitimately be generated from
        `AWAITING_FUNCTIONAL_REVIEW`, STALE or not."""
        state = _base_state(wi={
            "work_item_id": "wi",
            "phase": "AWAITING_FUNCTIONAL_REVIEW",
            "technical_approval": self._approved_technical_approval("abc123") | {"status": "STALE"},
        })
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError) as ctx:
            ws.record_bundle_generation(state, "wi", stage="implementation", head="def456", now="t2")
        self.assertIn("AWAITING_FUNCTIONAL_REVIEW", str(ctx.exception))


class TestEnterApplyingReviewFeedback(unittest.TestCase):
    def test_sets_phase_from_legal_source(self):
        state = _base_state(wi=_base_work_item(phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"))
        new_state = ws.enter_applying_review_feedback(state, "wi", now="t1")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "APPLYING_REVIEW_FEEDBACK")

    def test_version_independent_escape_from_terminal_phase_for_22_item(self):
        """Missing-tests item (I3, round 4): the writer itself never
        checked `governing_workflow_version` -- only
        `apply-implementation-review.md`'s own prose told the operator to
        skip calling it for a `"2.2"` item. Pinning that a `"2.2"` item
        which reached the terminal `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
        phase (both implementation-review stages already `APPROVE`d, then a
        late fix is committed after `/approve-review implementation` closed
        the gate) can still call this writer and land back in
        `APPLYING_REVIEW_FEEDBACK` -- the escape the command file's
        phase-conditional fix now actually exercises."""
        wi = _v22_work_item(
            phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            implementation_review_stages={
                "review_content_id": "c1",
                "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t0"},
                "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t0"},
            },
        )
        state = _base_state(wi=wi)
        new_state = ws.enter_applying_review_feedback(state, "wi", now="t1")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "APPLYING_REVIEW_FEEDBACK")

    def test_refused_from_applying_review_feedback_phase_for_a_1_item(self):
        """Missing-tests item (I2, round 5): the writer's own refusal from
        `APPLYING_REVIEW_FEEDBACK` does not distinguish `governing_workflow_
        version` at all -- `apply-implementation-review.md`'s step 0 is what
        decides, by `phase` alone, never by version, whether to call this
        writer. This pins the primitive's own half of that contract for a
        `"1"` item explicitly: if the command were ever to call this writer
        from `APPLYING_REVIEW_FEEDBACK` (the re-invocation case the prose's
        phase-conditional skip instead avoids calling into for every
        version), it still refuses -- exactly as the `"2.2"` escape test
        above shows the same primitive firing successfully from the
        terminal phase. Together the two tests show the primitive is
        version-blind, so the skip a `"1"`/`"2.1"` re-invocation gets under
        the new contract is a deliberate command-prose decision, not
        something this function enforces."""
        wi = _base_work_item(governing_workflow_version="1", phase="APPLYING_REVIEW_FEEDBACK")
        state = _base_state(wi=wi)
        with self.assertRaises(ws.IllegalApplyingReviewFeedbackEntryPhaseError) as ctx:
            ws.enter_applying_review_feedback(state, "wi", now="t1")
        self.assertIn("APPLYING_REVIEW_FEEDBACK", str(ctx.exception))

    def test_refused_from_illegal_source_phase(self):
        state = _base_state(wi=_base_work_item(phase="IMPLEMENTING"))
        with self.assertRaises(ws.IllegalApplyingReviewFeedbackEntryPhaseError) as ctx:
            ws.enter_applying_review_feedback(state, "wi", now="t1")
        self.assertIn("IMPLEMENTING", str(ctx.exception))
        self.assertIn("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", str(ctx.exception))

    def test_original_state_untouched(self):
        state = _base_state(wi=_base_work_item(phase="AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"))
        ws.enter_applying_review_feedback(state, "wi", now="t1")
        self.assertEqual(
            state["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        )


def _write_test_artifacts_declaration(
    repo: "ScratchRepo", work_item_id: str, *, protected_prefixes=None, excluded_prefixes=None,
) -> None:
    """A minimal implementation-stage classification declaration at the
    real `artifacts_path_for_work_item` location: `src/` protected,
    `docs/` (which also covers `WORKFLOW_STATE.json`) excluded, by
    default -- committed immediately so later commits can be diffed
    against it."""
    rel = fingerprint.artifacts_path_for_work_item(work_item_id)
    full = repo.root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    declaration = {
        "schema_version": 2,
        "work_item_id": work_item_id,
        "implementation_stage": {
            "protected_paths": {},
            "protected_prefixes": {p: "test" for p in (protected_prefixes or ["src/"])},
            "excluded_paths": {},
            "excluded_prefixes": {p: "test" for p in (excluded_prefixes or ["docs/"])},
        },
    }
    full.write_text(json.dumps(declaration))
    _run(["git", "add", str(rel)], cwd=repo.root)
    _run(["git", "commit", "-q", "-m", "artifacts declaration"], cwd=repo.root)


def _commit_state_only(
    repo: "ScratchRepo", work_item_id: str, work_item: dict, message: str,
    trailers: dict[str, str] | None = None,
) -> str:
    """Commits `WORKFLOW_STATE.json` alone -- staging exactly that one
    path, wrapping `work_item` in the real `{"work_items": {...}}` shape
    -- mirroring the ordinary `Workflow-Bundle-Generation-Record` commit
    contract (`validate_bundle_generation_record_commit`: touches only
    `WORKFLOW_STATE.json`)."""
    full = repo.root / STATE_REL_PATH
    full.parent.mkdir(parents=True, exist_ok=True)
    content = {"schema_version": 1, "work_items": {work_item_id: work_item}}
    full.write_bytes(ws._serialize_state(content))
    _run(["git", "add", STATE_REL_PATH], cwd=repo.root)
    body = message
    if trailers:
        body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
    return repo.head()


def _seed_base_provenance_state(repo: "ScratchRepo", work_item_id: str) -> None:
    """Commits a baseline `WORKFLOW_STATE.json` (static fields like
    `work_item_id` already present, `reviewed_implementation_head` unset)
    *before* any protected content commit -- mirroring the real repository,
    where `WORKFLOW_STATE.json` already exists and already carries every
    static field by the time a generation-record commit runs. Without
    this, a test's very first commit would show every field as "changed"
    (added from nothing), including static ones no real generation-record
    commit ever touches."""
    _commit_state_only(repo, work_item_id, {
        "work_item_id": work_item_id,
        "reviewed_implementation_head": None,
        "implementation_revision": 0,
        "phase": "IMPLEMENTING",
        "state_revision": 0,
        "last_transition": "t0",
    }, "seed base state")


def _provenance_state(work_item_id: str, *, reviewed_implementation_head, implementation_revision) -> dict:
    """The state as committed *by* the generation-record commit `T` --
    `phase` is therefore the ordinary target
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, not a source phase
    (OPUS-R101-001: a fixture that hard-codes the same phase on both sides
    of `T` can never exercise the phase-transition contract)."""
    return {
        "work_item_id": work_item_id,
        "reviewed_implementation_head": reviewed_implementation_head,
        "implementation_revision": implementation_revision,
        "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        "state_revision": implementation_revision + 1,
        "last_transition": f"t{implementation_revision}",
    }


def _record_trailers(work_item_id: str, implementation_revision: int) -> dict[str, str]:
    return {
        "Workflow-Bundle-Generation-Record": f"{work_item_id}/{implementation_revision}",
        "Workflow-Work-Item": work_item_id,
    }


def _recovered_trailers(work_item_id: str, implementation_revision: int, supersedes: str) -> dict[str, str]:
    """WF8c (c)/(b): the recovered-role three-trailer set -- the unchanged
    `Workflow-Bundle-Generation-Record` value plus `Workflow-Supersedes`
    naming the commit this one replaces as the chain's current tip."""
    return _record_trailers(work_item_id, implementation_revision) | {
        "Workflow-Supersedes": supersedes,
    }


def _write_and_commit(repo: "ScratchRepo", filename: str, content: str, message: str) -> str:
    """Like `repo.commit`, but with caller-controlled exact file content --
    needed to construct a byte-identical protected-edit-then-revert
    scenario (WF8c (c)'s own worked failure case), where `repo.commit`'s
    own subject-derived content would never coincidentally repeat."""
    full = repo.root / filename
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _run(["git", "add", filename], cwd=repo.root)
    _run(["git", "commit", "-q", "-m", message], cwd=repo.root)
    return repo.head()


class TestImplementationProvenanceInterval(unittest.TestCase):
    """WF8B-003 remediation (WF8b): `verify_implementation_provenance_interval`
    replaces a bare `reviewed_implementation_head == HEAD` comparison with
    D-Commit-Provenance's "Provenance interval" check (revision 28
    onward)."""

    WI = "wi"

    def test_exact_reviewed_head_zero_gap_is_reachable(self):
        """T immediately follows P (the protected content commit) with no
        intervening commits -- the simplest valid interval."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            work_item = state | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)
            self.assertEqual(result, t)
            self.assertTrue(ws.implementation_provenance_interval_reachable(repo.root, work_item, repo.base))

    def test_full_sequence_reaches_awaiting_functional_review_via_technical_approval(self):
        """Item 215 (`WF8c`): the whole `/approve-review implementation`
        sequence -- a protected commit `P`, `record_bundle_generation`'s
        own durability commit `S`, the gate's own
        `verify_implementation_provenance_interval` check
        (`approve-review.md` step, before any approval is applied), and
        finally `apply_technical_approval` -- composed end to end reaches
        `AWAITING_FUNCTIONAL_REVIEW` with `technical_approval.status ==
        CURRENT`, exercising `D-Approval-Commits`' revised ordering as one
        real sequence rather than each half in isolation (the gate check
        and the state write are each already covered separately by this
        class and by `TestApprovalStateWrites`)."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            provenance_state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(
                repo, self.WI, provenance_state, "record gen", trailers=_record_trailers(self.WI, 1),
            )
            work_item = provenance_state | {"work_item_id": self.WI}

            # The gate itself: must find a valid interval, naming S, before
            # any approval is even attempted.
            self.assertEqual(ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base), t)

            state = _base_state(**{self.WI: provenance_state})
            record = ws.build_approval_record(
                basis="USER_OVERRIDE", stage="implementation", user_confirmation="approve wi implementation",
                now="t2", reviewed_bundle_id="bundle-1", approved_review_content_id="content-1",
                review_content_manifest=[], reviewed_content_commit=t,
            )
            new_state = ws.apply_technical_approval(state, self.WI, record, now="t2")
            new_work_item = new_state["work_items"][self.WI]
            self.assertEqual(new_work_item["phase"], "AWAITING_FUNCTIONAL_REVIEW")
            self.assertEqual(new_work_item["technical_approval"]["status"], "CURRENT")
            self.assertEqual(new_work_item["technical_approval"]["reviewed_content_commit"], t)

    def test_ordinary_approve_path_never_durably_reads_awaiting_technical_approval(self):
        """Item 282 (`WF8c`, `GPT-R51-001`): the real ordinary `APPROVE`
        path, end to end -- `P -> S`; external feedback `APPROVE` binds
        exactly to `S`'s own bundle_id; `resolve_approval_basis` resolves
        `EXTERNAL_APPROVE` (never prompting for override text, missing-test
        item 13); the resulting single `apply_technical_approval` write
        lands on `AWAITING_FUNCTIONAL_REVIEW`. The work item's `phase`
        field is never, at any point in this sequence, the literal string
        `'AWAITING_TECHNICAL_APPROVAL'` -- that name is exclusively
        `technical_approval_gate_reachable`'s own computed gate (`D-States`),
        never a value this repository's `WORKFLOW_STATE.json` durably
        records, exactly as items 282/283's own text states."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            provenance_state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(
                repo, self.WI, provenance_state, "record gen", trailers=_record_trailers(self.WI, 1),
            )
            work_item = provenance_state | {"work_item_id": self.WI}
            self.assertEqual(ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base), t)
            self.assertNotEqual(work_item["phase"], "AWAITING_TECHNICAL_APPROVAL")

            current_bundle_id = "bundle-B1"
            basis = ws.resolve_approval_basis(
                latest_round_status="APPROVE", feedback_bundle_id=current_bundle_id,
                current_bundle_id=current_bundle_id, user_confirmation="approve wi implementation",
                work_item_id=self.WI, stage="implementation",
            )
            self.assertEqual(basis, "EXTERNAL_APPROVE")

            state = _base_state(**{self.WI: provenance_state})
            record = ws.build_approval_record(
                basis=basis, stage="implementation", user_confirmation="approve wi implementation",
                now="t2", reviewed_bundle_id=current_bundle_id, approved_review_content_id="content-1",
                review_content_manifest=[], reviewed_content_commit=t,
            )
            new_state = ws.apply_technical_approval(state, self.WI, record, now="t2")
            new_work_item = new_state["work_items"][self.WI]
            self.assertEqual(new_work_item["phase"], "AWAITING_FUNCTIONAL_REVIEW")
            self.assertNotEqual(new_work_item["phase"], "AWAITING_TECHNICAL_APPROVAL")
            self.assertEqual(new_work_item["technical_approval"]["status"], "CURRENT")
            self.assertEqual(new_work_item["technical_approval"]["basis"], "EXTERNAL_APPROVE")

    def test_fresh_session_restart_before_approval_leaves_gate_reachable_from_durable_state_alone(self):
        """Item 283 (`WF8c`, `GPT-R51-001`): a fresh-session restart
        between `B1` receiving `APPROVE` and the user invoking
        `/approve-review implementation` -- re-reading `S`'s own committed
        content directly (`_read_json_at_commit_or_empty`, never any
        in-memory carryover from the session that generated the bundle)
        shows `phase` durably `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
        with no `technical_approval` record, and
        `technical_approval_gate_reachable` -- fed only values re-derived
        from that fresh read plus a fresh
        `verify_implementation_provenance_interval` HEAD-match check, no
        session-local state of any kind -- still reachable."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            provenance_state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(
                repo, self.WI, provenance_state, "record gen", trailers=_record_trailers(self.WI, 1),
            )

            # Fresh session: re-read S's own committed content directly,
            # never anything carried over from the generating session.
            fresh_committed = ws._read_json_at_commit_or_empty(repo.root, t, STATE_REL_PATH)
            fresh_work_item = fresh_committed["work_items"][self.WI] | {"work_item_id": self.WI}
            self.assertEqual(fresh_work_item["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
            self.assertNotIn("technical_approval", fresh_work_item)

            resolved_t = ws.verify_implementation_provenance_interval(repo.root, fresh_work_item, repo.base)
            head_matches = resolved_t == repo.head()
            self.assertTrue(head_matches)
            self.assertTrue(ws.technical_approval_gate_reachable(
                latest_round_status="APPROVE", protected_path_dirty=False,
                head_matches_reviewed_implementation_head=head_matches,
                governing_workflow_version=None, implementation_review_stages=None,
                current_review_content_id=None,
            ))

    def test_one_excluded_commit_between_p_and_t_is_reachable(self):
        """A single excluded-only commit (mirroring a docs/outcome-record
        commit) lands between P and T -- still a valid interval, since it
        touches no protected path."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            repo.commit("unrelated excluded commit", filename="docs/notes.md")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            work_item = state | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)
            self.assertEqual(result, t)

    def test_protected_commit_refused_at_both_preflight_and_gate_independently(self):
        """Item 217 (`WF8c`): a genuine protected-implementation-path
        commit landing between `reviewed_implementation_head` and the
        attempted generation/approval point is refused independently at
        both call sites sharing `_classify_generation_record_interval` --
        `verify_implementation_provenance_interval` (`/approve-review
        implementation`'s own gate, its `P..T` walk) and
        `resolve_bundle_generation_outcome` (bundle-generation preflight,
        its own `T..head` walk) -- each naming the offending commit in
        its own raised exception, never silently treating the interval as
        authorized."""
        # Gate side: an unreviewed protected commit lands strictly between
        # P and the generation-record commit T itself.
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            intruder = repo.commit("unreviewed protected edit", filename="src/Bar.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.ProtectedPathInProvenanceIntervalError) as gate_ctx:
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)
            self.assertIn(intruder, str(gate_ctx.exception))

        # Preflight side: an edit-then-byte-identical-revert nets out
        # content-identical at head, but the interval itself still
        # contains the offending, never-reviewed commit.
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = _write_and_commit(repo, "src/Foo.kt", "v1\n", "protected v1")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            intruder = _write_and_commit(repo, "src/Foo.kt", "v2\n", "unreviewed protected edit")
            head = _write_and_commit(repo, "src/Foo.kt", "v1\n", "protected revert")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            with self.assertRaises(ws.ProtectedPathInProvenanceIntervalError) as preflight_ctx:
                ws.resolve_bundle_generation_outcome(
                    repo.root, work_item, base_commit=repo.base, head=head,
                )
            self.assertIn(intruder, str(preflight_ctx.exception))

    def test_unrelated_descendant_commit_after_t_refuses(self):
        """Live HEAD is one commit past the discovered T -- the exact
        real-world shape WF8B-003 exists to catch: a further commit
        landed carrying no provenance record of its own."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            repo.commit("trailing commit, no record", filename="docs/more.md")
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.HeadPastBundleGenerationRecordError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)
            self.assertFalse(ws.implementation_provenance_interval_reachable(repo.root, work_item, repo.base))

    def test_wrong_trailer_key_is_never_discovered_and_refuses(self):
        """A commit that carries a misspelled/wrong trailer key is simply
        not discovered -- the gate refuses with "not found", never a
        silent match."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers={
                "Workflow-Bundle-Generation-Recordx": f"{self.WI}/1",  # typo'd key
                "Workflow-Work-Item": self.WI,
            })
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.BundleGenerationRecordNotFoundError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

    def test_bare_head_equality_with_no_generation_record_refuses(self):
        """Item 241: `/approve-review implementation` refuses live HEAD
        exactly equal to `reviewed_implementation_head` when no
        current-revision `Workflow-Bundle-Generation-Record` commit is
        discoverable at all -- confirming the pre-`WF8B-003` bare-equality
        branch (`reviewed_implementation_head == HEAD` alone treated as
        sufficient) is gone, never silently still accepted, even in the
        one shape where bare equality genuinely holds."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            work_item = state | {"work_item_id": self.WI}
            self.assertEqual(repo.head(), p)  # bare equality genuinely holds
            self.assertEqual(work_item["reviewed_implementation_head"], repo.head())
            with self.assertRaises(ws.BundleGenerationRecordNotFoundError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)
            self.assertFalse(ws.implementation_provenance_interval_reachable(repo.root, work_item, repo.base))

    def test_reviewed_head_not_an_ancestor_refuses(self):
        """`reviewed_implementation_head` names a commit that isn't
        actually an ancestor of the discovered T at all."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            repo.commit("protected fix", filename="src/Foo.kt")
            bogus_p = repo.commit("unrelated commit, never reviewed", filename="src/Bar.kt")
            # Reset to before bogus_p so it's a sibling, not an ancestor, of T.
            _run(["git", "reset", "-q", "--hard", "HEAD^"], cwd=repo.root)
            state = _provenance_state(self.WI, reviewed_implementation_head=bogus_p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.ReviewedImplementationHeadNotAncestorError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

    def test_second_unexpected_descendant_with_same_trailer_value_refuses(self):
        """Two distinct commits both carry a Workflow-Bundle-Generation-Record
        trailer for the exact same `<work_item_id>/<implementation_revision>`
        value -- genuine ambiguity, not silently resolved (mirrors
        `TestCheckpointTrailerDiscovery.test_genuine_ambiguity_raises`)."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen a", trailers=_record_trailers(self.WI, 1))
            state_b = state | {"last_transition": "t1-again"}  # distinct content, same trailer value
            _commit_state_only(repo, self.WI, state_b, "record gen b", trailers=_record_trailers(self.WI, 1))
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.AmbiguousBundleGenerationRecordTrailerError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

    def test_forked_supersedes_trailer_is_genuine_ambiguity_refuses(self):
        """Item 270 (`WF8c`): two distinct commits each carrying
        `Workflow-Supersedes: <T-sha>` -- both claiming to recover the
        *same* prior generation-record commit `T`, rather than one
        recovering the other -- is refused as genuine, unresolved
        ambiguity, never auto-resolved by recency or commit order.
        `_bundle_generation_record_chain_tip`'s own docstring predicts
        this exact outcome (a fork leaves more-than-one verified tip
        survivor), but no prior test actually constructed a forked
        `Workflow-Supersedes` edge -- `test_second_unexpected_descendant_
        with_same_trailer_value_refuses` above only forks the plain
        two-trailer ordinary role, never the three-trailer recovered one."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            repo.commit("unrelated excluded commit", filename="docs/notes.md")
            recovered_state = state | {"state_revision": state["state_revision"] + 1}
            _commit_state_only(
                repo, self.WI, recovered_state, "recover a",
                trailers=_recovered_trailers(self.WI, 1, t),
            )
            recovered_state_b = recovered_state | {"last_transition": "t1-recover-b"}
            _commit_state_only(
                repo, self.WI, recovered_state_b, "recover b (also claims to supersede T, not A)",
                trailers=_recovered_trailers(self.WI, 1, t),
            )
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.AmbiguousBundleGenerationRecordTrailerError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

    def test_side_branch_merge_topology_refuses(self):
        """`reviewed_implementation_head` is genuinely reachable from T,
        but only through a merge's non-first-parent side -- T's own
        first-parent chain never passes through it."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            fork_point = repo.head()
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            p = repo.commit("protected fix on side", filename="src/Foo.kt")
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            _run(["git", "reset", "-q", "--hard", fork_point], cwd=repo.root)
            repo.commit("excluded commit on main", filename="docs/main-notes.md")
            _run(["git", "merge", "-q", "--no-ff", "-m", "merge side", "side"], cwd=repo.root)
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.NonFirstParentProvenanceIntervalError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)
            self.assertFalse(ws.implementation_provenance_interval_reachable(repo.root, work_item, repo.base))
            self.assertNotEqual(t, "")  # t is created; just never a valid interval terminus

    def test_real_v2_1_dry_run_s8_to_s9_shape_becomes_reachable(self):
        """Reproduces the exact real-repository shape WF8B-003 surfaced at
        `v2-1-dry-run`'s own S9 gate: a protected scratch-marker fix
        commit (S8's `ae7ef4c`-equivalent), followed by an excluded-only
        docs/outcome-recording commit (S8's `34ce1dd`-equivalent), then a
        dedicated ordinary Workflow-Bundle-Generation-Record commit --
        the gate must become reachable, unlike the pre-remediation bare
        `reviewed_implementation_head == HEAD` rule that blocked it."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(
                repo, "v2-1-dry-run",
                protected_prefixes=["docs/ai-workflow/dry-run/scratch/"],
                excluded_prefixes=["docs/"],
            )
            _seed_base_provenance_state(repo, "v2-1-dry-run")
            p = repo.commit("fix S-CP3 scratch marker", filename="docs/ai-workflow/dry-run/scratch/c.txt")
            repo.commit(
                "docs(wf8b): execute v2-1-dry-run's S8", filename="docs/ai-workflow/dry-run/WF8B_SCENARIOS.md",
            )
            state = _provenance_state("v2-1-dry-run", reviewed_implementation_head=p, implementation_revision=2)
            t = _commit_state_only(
                repo, "v2-1-dry-run", state, "record gen (post-fix)",
                trailers=_record_trailers("v2-1-dry-run", 2),
            )
            work_item = state | {"work_item_id": "v2-1-dry-run"}
            self.assertTrue(ws.implementation_provenance_interval_reachable(repo.root, work_item, repo.base))
            self.assertEqual(
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base), t,
            )

    def test_forbidden_mutation_alongside_generation_record_refuses(self):
        """Item 267 (`WF8c`): an otherwise well-formed ordinary `T` that
        additionally changes, in the same commit, one representative
        forbidden field in each class -- `technical_approval`,
        `plan_approval`, a `checkpoints` entry, `functional_acceptance_status`,
        the top-level `active_work_item_id`, and another work item's own
        state -- refuses the terminal-commit classification in every case,
        even though the commit still nominally "touches only
        `WORKFLOW_STATE.json`". The first four are already caught by
        `_work_item_field_diff`'s existing subset check (same work item,
        non-ordinary field); the last two are the real code gap
        `_forbidden_state_mutation` closes -- invisible to a diff scoped to
        one work item's own fields."""
        same_item_mutations = {
            "technical_approval": {
                "technical_approval": {"status": "CURRENT", "approved_review_content_id": "a" * 64},
            },
            "plan_approval": {
                "plan_approval": {"status": "CURRENT", "approved_review_content_id": "b" * 64},
            },
            "checkpoints_entry": {
                "checkpoints": {"WF0": {"status": "COMPLETE"}},
            },
            "functional_acceptance_status": {
                "functional_acceptance_status": "ACCEPTED",
            },
        }
        for label, extra_fields in same_item_mutations.items():
            with self.subTest(label):
                with ScratchRepo() as repo:
                    _write_test_artifacts_declaration(repo, self.WI)
                    _seed_base_provenance_state(repo, self.WI)
                    p = repo.commit("protected fix", filename="src/Foo.kt")
                    state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
                    content = json.dumps({"schema_version": 1, "work_items": {self.WI: state | extra_fields}})
                    _commit_state_with_trailers(repo, content, _record_trailers(self.WI, 1), message="record gen")
                    work_item = state | {"work_item_id": self.WI}
                    with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
                        ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

        with self.subTest("active_work_item_id"):
            with ScratchRepo() as repo:
                _write_test_artifacts_declaration(repo, self.WI)
                _seed_base_provenance_state(repo, self.WI)
                p = repo.commit("protected fix", filename="src/Foo.kt")
                state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
                content = json.dumps({
                    "schema_version": 1,
                    "active_work_item_id": self.WI,
                    "work_items": {self.WI: state},
                })
                _commit_state_with_trailers(repo, content, _record_trailers(self.WI, 1), message="record gen")
                work_item = state | {"work_item_id": self.WI}
                with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
                    ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

        with self.subTest("another_work_item_own_state"):
            with ScratchRepo() as repo:
                _write_test_artifacts_declaration(repo, self.WI)
                _seed_base_provenance_state(repo, self.WI)
                p = repo.commit("protected fix", filename="src/Foo.kt")
                state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
                content = json.dumps({
                    "schema_version": 1,
                    "work_items": {
                        self.WI: state,
                        "other-wi": {"work_item_id": "other-wi", "phase": "IMPLEMENTING"},
                    },
                })
                _commit_state_with_trailers(repo, content, _record_trailers(self.WI, 1), message="record gen")
                work_item = state | {"work_item_id": self.WI}
                with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
                    ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

    def test_unrelated_other_work_item_unchanged_is_still_reachable(self):
        """Negative control for item 267: another work item's entry
        merely *existing*, byte-identical across parent and `T`, is not a
        forbidden mutation -- only a genuine change to it is."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            other = {"work_item_id": "other-wi", "phase": "IMPLEMENTING"}
            _commit_state_with_trailers(
                repo,
                json.dumps({
                    "schema_version": 1,
                    "work_items": {
                        self.WI: {
                            "work_item_id": self.WI, "reviewed_implementation_head": None,
                            "implementation_revision": 0, "phase": "IMPLEMENTING",
                            "state_revision": 0, "last_transition": "t0",
                        },
                        "other-wi": other,
                    },
                }),
                {}, message="seed base state",
            )
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_with_trailers(
                repo,
                json.dumps({
                    "schema_version": 1,
                    "work_items": {self.WI: state, "other-wi": other},
                }),
                _record_trailers(self.WI, 1), message="record gen",
            )
            work_item = state | {"work_item_id": self.WI}
            self.assertEqual(ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base), t)

    def test_generation_record_trailer_naming_different_work_item_is_not_discovered(self):
        """Item 219 (`WF8c`): a well-formed `Workflow-Bundle-Generation-
        Record: wi/1` value on a commit whose `Workflow-Work-Item` trailer
        names a *different* work item is not discovered by this work
        item's scoped lookup at all -- the gate refuses exactly as if no
        provenance commit existed, never falling back to an unscoped
        match on the generation-record value alone."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen for a different work item", trailers={
                "Workflow-Bundle-Generation-Record": f"{self.WI}/1",
                "Workflow-Work-Item": "a-different-work-item",
            })
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.BundleGenerationRecordNotFoundError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

    def test_own_work_item_generation_record_is_discoverable_by_its_own_lookup_only(self):
        """Item 264 (`WF8c`): a well-formed `S` for `other-wi` (both
        trailers self-consistently naming `other-wi`) is discovered by
        `other-wi`'s own lookup, confirming the trailer genuinely scopes a
        real, positive match -- not merely that some *other* work item's
        lookup happens to miss a decorative mismatch
        (`test_generation_record_trailer_naming_different_work_item_is_not_discovered`
        above already covers the negative half for `self.WI`'s own
        lookup); `self.WI`'s own lookup for its own `implementation_revision`
        finds nothing at all, since no commit anywhere names `self.WI`."""
        with ScratchRepo() as repo:
            other_wi = "other-wi"
            _write_test_artifacts_declaration(repo, other_wi)
            _seed_base_provenance_state(repo, other_wi)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            other_state = _provenance_state(other_wi, reviewed_implementation_head=p, implementation_revision=1)
            s = _commit_state_only(
                repo, other_wi, other_state, "record gen", trailers=_record_trailers(other_wi, 1),
            )
            other_work_item = other_state | {"work_item_id": other_wi}
            self.assertEqual(
                ws.verify_implementation_provenance_interval(repo.root, other_work_item, repo.base), s,
            )
            missing_work_item = other_state | {"work_item_id": self.WI, "reviewed_implementation_head": p}
            with self.assertRaises(ws.BundleGenerationRecordNotFoundError):
                ws.verify_implementation_provenance_interval(repo.root, missing_work_item, repo.base)

    def test_missing_work_item_trailer_entirely_is_not_discovered(self):
        """Item 263 (`WF8c`): an otherwise well-formed `S` missing the
        `Workflow-Work-Item` trailer entirely (not merely carrying a wrong
        value) is not discovered by the scoped lookup at all -- refuses
        exactly as if no provenance commit existed, never falling back to
        an unscoped match on the `Workflow-Bundle-Generation-Record` value
        alone."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen, no Workflow-Work-Item trailer", trailers={
                "Workflow-Bundle-Generation-Record": f"{self.WI}/1",
            })
            work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.BundleGenerationRecordNotFoundError):
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)

    def test_recovered_s2_missing_work_item_trailer_is_not_discovered(self):
        """Item 265 (`WF8c`): a recovered generation-record commit `S2`
        carrying exactly the canonical three-trailer set
        (`Workflow-Bundle-Generation-Record`, `Workflow-Supersedes`,
        `Workflow-Work-Item`) is discovered and validates as the terminal
        member of its multi-commit interval; the same `S2` missing
        `Workflow-Work-Item` is not discovered at all."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            s = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            repo.commit("excluded-only doc fix", filename="docs/notes.md")
            s2_state = state | {"state_revision": 3}
            s2 = _commit_state_only(
                repo, self.WI, s2_state, "recover stale generation_head",
                trailers=_recovered_trailers(self.WI, 1, s),
            )
            full_work_item = s2_state | {"work_item_id": self.WI}
            self.assertEqual(
                ws.verify_implementation_provenance_interval(repo.root, full_work_item, repo.base), s2,
            )

        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            s = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            repo.commit("excluded-only doc fix", filename="docs/notes.md")
            s2_state = state | {"state_revision": 3}
            _commit_state_only(
                repo, self.WI, s2_state, "recover, no Workflow-Work-Item trailer", trailers={
                    "Workflow-Bundle-Generation-Record": f"{self.WI}/1",
                    "Workflow-Supersedes": s,
                },
            )
            full_work_item = s2_state | {"work_item_id": self.WI}
            # S2 (missing its own Workflow-Work-Item trailer) is not
            # discovered as a generation-record commit at all -- S remains
            # the last one found, and live HEAD has moved past it with no
            # further discovered record, refused as such.
            with self.assertRaises(ws.HeadPastBundleGenerationRecordError):
                ws.verify_implementation_provenance_interval(repo.root, full_work_item, repo.base)

    def test_wrong_implementation_revision_names_both_expected_and_found(self):
        """Item 220 (`WF8c`): a `Workflow-Bundle-Generation-Record`
        trailer naming the wrong `implementation_revision` (here, the
        previous round's) fails the exactly-one-match lookup for the
        *current* revision and refuses, naming both the expected and the
        found revision in the raised error -- not only what was
        expected."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p1 = repo.commit("protected fix, round 1", filename="src/Foo.kt")
            state1 = _provenance_state(self.WI, reviewed_implementation_head=p1, implementation_revision=1)
            _commit_state_only(repo, self.WI, state1, "record gen round 1", trailers=_record_trailers(self.WI, 1))
            p2 = repo.commit("protected fix, round 2", filename="src/Foo.kt")
            state2 = _provenance_state(self.WI, reviewed_implementation_head=p2, implementation_revision=2)
            work_item = state2 | {"work_item_id": self.WI}
            with self.assertRaises(ws.BundleGenerationRecordNotFoundError) as ctx:
                ws.verify_implementation_provenance_interval(repo.root, work_item, repo.base)
            self.assertIn(f"{self.WI}/2", str(ctx.exception))
            self.assertIn(f"{self.WI}/1", str(ctx.exception))

    def test_terminal_commit_touching_an_extra_unclassified_path_refuses(self):
        """Item 253 (`WF8c`): a terminal `T` carrying the correct
        `Workflow-Bundle-Generation-Record` trailer, touching no protected
        path, but *also* touching an unclassified path outside the
        implementation artifact declaration, refuses -- naming the
        unclassified path found in `T` itself. `validate_bundle_generation_
        record_commit`'s own first check (`changed_paths != {state_rel}`)
        already refuses on *any* extra path regardless of its
        classification, so this is a strict superset of the "extra
        protected path" case, never merely the "no protected path" half
        revision 30 originally left ambiguous."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            full = repo.root / STATE_REL_PATH
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(ws._serialize_state({"schema_version": 1, "work_items": {self.WI: state}}))
            (repo.root / "other").mkdir(exist_ok=True)
            (repo.root / "other" / "extra.txt").write_text("extra\n")
            _run(["git", "add", STATE_REL_PATH, "other/extra.txt"], cwd=repo.root)
            body = "record gen\n\n" + "\n".join(f"{k}: {v}" for k, v in _record_trailers(self.WI, 1).items())
            _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
            t = repo.head()
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.validate_bundle_generation_record_commit(repo.root, t, self.WI)
            self.assertIn(t, str(ctx.exception))
            self.assertIn("other/extra.txt", str(ctx.exception))


class TestRecordBundleGenerationSameContentOutcome(unittest.TestCase):
    """WF8c (c): `record_bundle_generation`'s `outcome` parameter -- the
    pure state-mutation half of D-Commit-Provenance's "Same-content
    post-fix republication". `resolve_bundle_generation_outcome` (the
    Git-inspecting half) is exercised separately by
    `TestResolveBundleGenerationOutcome`/`TestSameContentRepublicationEndToEnd`
    below."""

    def test_default_outcome_is_ordinary_backward_compatible(self):
        state = _base_state(wi=_base_work_item(phase="SELF_REVIEWING_IMPLEMENTATION"))
        new_state = ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["reviewed_implementation_head"], "abc123")
        self.assertEqual(wi["implementation_revision"], 1)

    def test_same_content_outcome_leaves_head_and_revision_unchanged(self):
        state = _base_state(wi=_base_work_item(
            phase="APPLYING_REVIEW_FEEDBACK",
            reviewed_implementation_head="abc123", implementation_revision=3,
        ))
        new_state = ws.record_bundle_generation(
            state, "wi", stage="post-fix", head="def456", now="t2", outcome="same_content",
        )
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["reviewed_implementation_head"], "abc123")
        self.assertEqual(wi["implementation_revision"], 3)

    def test_same_content_outcome_still_transitions_phase(self):
        state = _base_state(wi=_base_work_item(
            phase="APPLYING_REVIEW_FEEDBACK",
            reviewed_implementation_head="abc123", implementation_revision=3,
        ))
        new_state = ws.record_bundle_generation(
            state, "wi", stage="post-fix", head="def456", now="t2", outcome="same_content",
        )
        self.assertEqual(
            new_state["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        )

    def test_same_content_outcome_from_self_reviewing_implementation_also_transitions(self):
        """D-Commit-Provenance's widened (revision 38) authority: legal
        from either of the two source phases, not `APPLYING_REVIEW_FEEDBACK`
        alone."""
        state = _base_state(wi=_base_work_item(
            phase="SELF_REVIEWING_IMPLEMENTATION",
            reviewed_implementation_head="abc123", implementation_revision=1,
        ))
        new_state = ws.record_bundle_generation(
            state, "wi", stage="implementation", head="def456", now="t2", outcome="same_content",
        )
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["reviewed_implementation_head"], "abc123")
        self.assertEqual(wi["implementation_revision"], 1)
        self.assertEqual(wi["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

    def test_same_content_outcome_still_bumps_state_revision_and_last_transition(self):
        state = _base_state(wi=_base_work_item(
            phase="APPLYING_REVIEW_FEEDBACK", state_revision=5,
            reviewed_implementation_head="abc123", implementation_revision=1,
        ))
        new_state = ws.record_bundle_generation(
            state, "wi", stage="post-fix", head="def456", now="t9", outcome="same_content",
        )
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["state_revision"], 6)
        self.assertEqual(wi["last_transition"], "t9")

    def test_unknown_outcome_rejected(self):
        state = _base_state(wi=_base_work_item(phase="SELF_REVIEWING_IMPLEMENTATION"))
        with self.assertRaises(ws.InvalidBundleGenerationOutcomeError):
            ws.record_bundle_generation(
                state, "wi", stage="implementation", head="abc123", now="t1", outcome="bogus",
            )

    def test_illegal_source_phase_refused_even_for_same_content_outcome(self):
        state = _base_state(wi=_base_work_item(
            phase="IMPLEMENTING", reviewed_implementation_head="abc", implementation_revision=1,
        ))
        with self.assertRaises(ws.IllegalBundleGenerationSourcePhaseError):
            ws.record_bundle_generation(
                state, "wi", stage="post-fix", head="def456", now="t1", outcome="same_content",
            )

    def test_original_state_untouched_for_same_content_outcome(self):
        state = _base_state(wi=_base_work_item(
            phase="APPLYING_REVIEW_FEEDBACK",
            reviewed_implementation_head="abc123", implementation_revision=1,
        ))
        ws.record_bundle_generation(
            state, "wi", stage="post-fix", head="def456", now="t2", outcome="same_content",
        )
        self.assertEqual(state["work_items"]["wi"]["phase"], "APPLYING_REVIEW_FEEDBACK")


class TestResolveBundleGenerationOutcome(unittest.TestCase):
    """WF8c (c), D-Commit-Provenance "Same-content post-fix republication":
    the read-only, Git-inspecting decision `record_bundle_generation`'s
    caller makes before choosing which `outcome` to pass it and which
    commit-trailer set to write."""

    WI = "wi"

    def test_no_prior_round_resolves_ordinary(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            work_item = {
                "work_item_id": self.WI, "work_item_type": "process",
                "reviewed_implementation_head": None, "implementation_revision": None,
            }
            outcome = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=repo.head(),
            )
            self.assertEqual(outcome, ("ordinary", None))

    def test_content_differs_resolves_ordinary(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected v1", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head = repo.commit("protected v2", filename="src/Foo.kt")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            outcome = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(outcome, ("ordinary", None))

    def test_identical_content_with_clean_excluded_interval_resolves_same_content(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head = repo.commit("resolved via excluded content", filename="docs/notes.md")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            outcome = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(outcome, ("same_content", t))

    def test_multiple_excluded_commits_between_t_and_head_still_resolves_same_content(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            repo.commit("finding rejected with evidence", filename="docs/notes-a.md")
            head = repo.commit("second finding rejected too", filename="docs/notes-b.md")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            outcome = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(outcome, ("same_content", t))

    def test_protected_edit_then_byte_identical_revert_between_t_and_head_refuses(self):
        """D-Commit-Provenance's own worked failure scenario (`GPT-R46-002`):
        endpoint content nets out identical, but the interval itself
        contains a genuinely protected commit -- "a protected edit
        followed by a byte-identical revert ... can leave endpoint content
        identical while the interval contains a commit the operation must
        refuse." The fail-closed backstop must fire, never silently
        classify this as same-content."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = _write_and_commit(repo, "src/Foo.kt", "v1\n", "protected v1")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            _write_and_commit(repo, "src/Foo.kt", "v2\n", "protected edit")
            head = _write_and_commit(repo, "src/Foo.kt", "v1\n", "protected revert")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            with self.assertRaises(ws.ProtectedPathInProvenanceIntervalError):
                ws.resolve_bundle_generation_outcome(
                    repo.root, work_item, base_commit=repo.base, head=head,
                )

    def test_item_290_genuine_content_diff_resolves_ordinary_even_after_excluded_commits(self):
        """Item 290 (`WF8c`): a genuine protected-content fix, preceded by
        one or more legitimate excluded-only commits, still resolves
        `("ordinary", None)` -- never `same_content` -- regardless of how
        many excluded-only commits came before it. Distinguishes this from
        `test_multiple_excluded_commits_between_t_and_head_still_resolves_
        same_content`, whose interval's endpoint content is genuinely
        identical throughout; here the endpoint itself differs."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected v1", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            repo.commit("legitimate excluded doc fix", filename="docs/notes-a.md")
            repo.commit("second legitimate excluded doc fix", filename="docs/notes-b.md")
            head = repo.commit("protected v2", filename="src/Foo.kt")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            outcome = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(outcome, ("ordinary", None))

    def test_no_discoverable_generation_record_commit_refuses(self):
        """Content identical (trivially: `p` itself as the candidate head,
        the zero-commit-interval case) but no `T` was ever recorded --
        the fail-closed backstop, not silently treated as ordinary."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            work_item = {
                "work_item_id": self.WI, "work_item_type": "process",
                "reviewed_implementation_head": p, "implementation_revision": 1,
            }
            with self.assertRaises(ws.BundleGenerationRecordNotFoundError):
                ws.resolve_bundle_generation_outcome(
                    repo.root, work_item, base_commit=repo.base, head=p,
                )

    def test_deleted_or_corrupted_manifest_does_not_affect_outcome(self):
        """Item 225 (`WF8c`): `.ai-review/<work_item_id>/current/MANIFEST.md`
        is disposable review-bundle content, never a value this Git- and
        `WORKFLOW_STATE.json`-only decision reads. Deleting it, or leaving
        unparseable garbage in its place, must not change the resolved
        outcome from the identical round `test_identical_content_with_
        clean_excluded_interval_resolves_same_content` already proves."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head = repo.commit("resolved via excluded content", filename="docs/notes.md")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}

            manifest = repo.root / ".ai-review" / self.WI / "current" / "MANIFEST.md"

            # Absent entirely (never written for this scratch repo).
            self.assertFalse(manifest.exists())
            outcome_absent = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(outcome_absent, ("same_content", t))

            # Present but corrupted (unparseable garbage, no MANIFEST.md
            # structure at all) -- untracked, so it never affects Git-object
            # content identity either.
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("not a real manifest\x00\xff garbage")
            outcome_corrupted = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(outcome_corrupted, ("same_content", t))

            # Deleted again after having existed.
            manifest.unlink()
            outcome_deleted_again = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(outcome_deleted_again, ("same_content", t))


class TestSameContentRepublicationEndToEnd(unittest.TestCase):
    """WF8c (c): the full round trip -- `resolve_bundle_generation_outcome`
    sanctions a republication, `record_bundle_generation`'s `same_content`
    outcome produces the state to commit, the caller commits it as a
    recovered-role `Workflow-Supersedes` commit, and
    `verify_implementation_provenance_interval` (the approval gate's own
    check) validates the resulting multi-commit chain end to end --
    exactly the round trip `/approve-review implementation` depends on."""

    WI = "wi"
    WORK_ITEM_TYPE = "process"

    def _seed_base_state(self, repo: "ScratchRepo") -> None:
        """Like `_seed_base_provenance_state`, plus `work_item_type` baked
        in from this work item's very first committed state --
        `resolve_bundle_generation_outcome` needs `work_item_type`
        present, but it must be present *identically* across every commit
        in the chain (never introduced partway through, as plain
        `_seed_base_provenance_state` would leave it) or it would itself
        register as a spurious field change against
        `validate_bundle_generation_record_commit`'s exact-subset check --
        including for the ordinary `T` this class's own tests still walk
        as a non-terminal chain member."""
        _commit_state_only(repo, self.WI, {
            "work_item_id": self.WI, "work_item_type": self.WORK_ITEM_TYPE,
            "reviewed_implementation_head": None, "implementation_revision": 0,
            "phase": "IMPLEMENTING", "state_revision": 0, "last_transition": "t0",
        }, "seed base state")

    def _ordinary_state(self, repo: "ScratchRepo", p: str) -> dict:
        return _provenance_state(
            self.WI, reviewed_implementation_head=p, implementation_revision=1,
        ) | {"work_item_type": self.WORK_ITEM_TYPE}

    def _enter_applying_review_feedback(
        self, repo: "ScratchRepo", prior_state: dict, *, state_revision: int,
    ) -> dict:
        feedback_state = prior_state | {"phase": "APPLYING_REVIEW_FEEDBACK", "state_revision": state_revision}
        _commit_state_only(repo, self.WI, feedback_state, "enter applying review feedback")
        return feedback_state

    def test_single_republication_then_full_interval_validates(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            ordinary_state = self._ordinary_state(repo, p)
            t = _commit_state_only(repo, self.WI, ordinary_state, "record gen", trailers=_record_trailers(self.WI, 1))
            feedback_state = self._enter_applying_review_feedback(repo, ordinary_state, state_revision=2)
            head = repo.commit("resolved via excluded content", filename="docs/notes.md")

            work_item = feedback_state | {"work_item_id": self.WI}
            outcome, resolved_t = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual((outcome, resolved_t), ("same_content", t))

            new_state = ws.record_bundle_generation(
                _base_state(**{self.WI: work_item}), self.WI, stage="post-fix",
                head=head, now="t3", outcome=outcome,
            )
            s2_work_item = new_state["work_items"][self.WI]
            self.assertEqual(s2_work_item["reviewed_implementation_head"], p)
            self.assertEqual(s2_work_item["implementation_revision"], 1)

            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "republish (same content)",
                trailers=_recovered_trailers(self.WI, 1, resolved_t),
            )
            final_work_item = s2_work_item | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertEqual(result, s2)
            self.assertTrue(
                ws.implementation_provenance_interval_reachable(repo.root, final_work_item, repo.base),
            )
            # Discovery resolves s2, not t, as the current tip: t alone
            # would fail HeadPastBundleGenerationRecordError (live HEAD is
            # s2, past t) -- the assertions above already prove this.

    def test_second_sequential_republication_chain_validates(self):
        """D-Commit-Provenance "Multiple sequential recoveries /
        supersession chain": a second same-content republication, from
        S2's own tip, produces `P -> T -> U1 -> S2 -> U2 -> S3`, and the
        approval gate validates the *entire* chain, not a shortcut from
        `P` directly to `S3`."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            ordinary_state = self._ordinary_state(repo, p)
            t = _commit_state_only(repo, self.WI, ordinary_state, "record gen", trailers=_record_trailers(self.WI, 1))
            feedback_state = self._enter_applying_review_feedback(repo, ordinary_state, state_revision=2)
            head1 = repo.commit("first finding rejected", filename="docs/notes-a.md")

            work_item_1 = feedback_state | {"work_item_id": self.WI}
            outcome_1, resolved_t_1 = ws.resolve_bundle_generation_outcome(
                repo.root, work_item_1, base_commit=repo.base, head=head1,
            )
            self.assertEqual((outcome_1, resolved_t_1), ("same_content", t))
            s2_work_item = ws.record_bundle_generation(
                _base_state(**{self.WI: work_item_1}), self.WI, stage="post-fix",
                head=head1, now="t3", outcome=outcome_1,
            )["work_items"][self.WI]
            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "republish (same content) 1",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_1),
            )

            feedback_state_2 = self._enter_applying_review_feedback(repo, s2_work_item, state_revision=4)
            head2 = repo.commit("second finding rejected", filename="docs/notes-b.md")

            work_item_2 = feedback_state_2 | {"work_item_id": self.WI}
            outcome_2, resolved_t_2 = ws.resolve_bundle_generation_outcome(
                repo.root, work_item_2, base_commit=repo.base, head=head2,
            )
            self.assertEqual((outcome_2, resolved_t_2), ("same_content", s2))
            s3_work_item = ws.record_bundle_generation(
                _base_state(**{self.WI: work_item_2}), self.WI, stage="post-fix",
                head=head2, now="t5", outcome=outcome_2,
            )["work_items"][self.WI]
            s3 = _commit_state_only(
                repo, self.WI, s3_work_item, "republish (same content) 2",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_2),
            )

            final_work_item = s3_work_item | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertEqual(result, s3)

    def test_orphan_recovered_role_commit_with_no_predecessor_refuses(self):
        """A recovered-role commit standing as the pair's *only*
        `Workflow-Bundle-Generation-Record` commit -- discoverable with no
        tie-break needed at all, so this reaches
        `_assert_generation_record_terminal_chain_continuity` rather than
        being caught by discovery's own fork/ambiguity detection -- must
        still refuse: chain continuity requires an actual, immediately
        preceding generation-record commit, never merely *some*
        `Workflow-Supersedes` trailer with a plausible-looking target
        (here: `P` itself, which is never a generation-record commit)."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            seed_feedback_state = {
                "work_item_id": self.WI, "work_item_type": self.WORK_ITEM_TYPE,
                "reviewed_implementation_head": p, "implementation_revision": 1,
                "phase": "APPLYING_REVIEW_FEEDBACK", "state_revision": 1, "last_transition": "t1",
            }
            _commit_state_only(repo, self.WI, seed_feedback_state, "enter applying review feedback")
            repo.commit("resolved via excluded content", filename="docs/notes.md")

            orphan_state = seed_feedback_state | {
                "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "state_revision": 2,
            }
            s2 = _commit_state_only(
                repo, self.WI, orphan_state, "republish (no actual predecessor)",
                trailers=_recovered_trailers(self.WI, 1, p),  # names P, never a generation-record commit
            )
            final_work_item = orphan_state | {"work_item_id": self.WI}
            with self.assertRaises(ws.MalformedProvenanceSupersessionChainError):
                ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertNotEqual(s2, "")  # s2 is created; just never a valid interval terminus

    def test_malformed_field_diff_recovered_commit_refuses(self):
        """A "recovered-role"-trailered commit that also changes
        `reviewed_implementation_head` (never legal for that role) is
        rejected outright, not silently accepted because its trailer set
        looks right."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            ordinary_state = self._ordinary_state(repo, p)
            t = _commit_state_only(repo, self.WI, ordinary_state, "record gen", trailers=_record_trailers(self.WI, 1))
            feedback_state = self._enter_applying_review_feedback(repo, ordinary_state, state_revision=2)
            repo.commit("resolved via excluded content", filename="docs/notes.md")

            tampered_state = feedback_state | {
                "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "state_revision": 3,
                "reviewed_implementation_head": "0" * 40,  # illegally changed
            }
            s2 = _commit_state_only(
                repo, self.WI, tampered_state, "republish (tampered head)",
                trailers=_recovered_trailers(self.WI, 1, t),
            )
            # The work item's own tracked reviewed_implementation_head is
            # still the real p -- nothing legitimate ever changed it; the
            # corruption lives only in the rogue commit under test.
            final_work_item = ordinary_state | {"work_item_id": self.WI}
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            # Pins down *which* commit and *why* -- guards against a
            # different, unrelated field-diff bug (e.g. an inconsistent
            # static field across the fixture's own commits) silently
            # satisfying this assertion for the wrong reason.
            self.assertIn(s2, str(ctx.exception))
            self.assertIn("reviewed_implementation_head", str(ctx.exception))

    def test_terminal_commit_with_wrong_target_phase_refuses(self):
        """Item 276 (`WF8c`): an otherwise well-formed ordinary `T` that
        sets `phase` to any value other than
        `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` (here, a hand-edited
        `AWAITING_TECHNICAL_APPROVAL`) refuses the terminal-commit
        role-specific check -- confirming the exact target phase value is
        enforced, not merely "some phase change occurred"."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            wrong_phase_state = state | {"phase": "AWAITING_TECHNICAL_APPROVAL"}
            t = _commit_state_only(
                repo, self.WI, wrong_phase_state, "record gen", trailers=_record_trailers(self.WI, 1),
            )
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.validate_bundle_generation_record_commit(repo.root, t, self.WI)
            self.assertIn("AWAITING_TECHNICAL_APPROVAL", str(ctx.exception))
            self.assertIn("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", str(ctx.exception))

    def test_republication_then_recovery_chain_validates(self):
        """Item 289 (`WF8c`, `GPT-R51-003`), first half: a supersession
        chain containing one link authored by `record_bundle_generation`'s
        same-content-republication branch (source phase
        `APPLYING_REVIEW_FEEDBACK`) followed by one authored by the
        standalone `/recover-implementation-provenance` command (source
        phase `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) validates at
        `verify_implementation_provenance_interval` -- the chain mechanics
        never distinguish which of the two legal writers produced a given
        link. See `test_recovery_then_republication_chain_validates` for
        the opposite writer order."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            ordinary_state = self._ordinary_state(repo, p)
            t = _commit_state_only(repo, self.WI, ordinary_state, "record gen", trailers=_record_trailers(self.WI, 1))

            feedback_state = self._enter_applying_review_feedback(repo, ordinary_state, state_revision=2)
            head1 = repo.commit("first finding rejected", filename="docs/notes-a.md")
            work_item_1 = feedback_state | {"work_item_id": self.WI}
            outcome_1, resolved_t_1 = ws.resolve_bundle_generation_outcome(
                repo.root, work_item_1, base_commit=repo.base, head=head1,
            )
            self.assertEqual((outcome_1, resolved_t_1), ("same_content", t))
            s2_work_item = ws.record_bundle_generation(
                _base_state(**{self.WI: work_item_1}), self.WI, stage="post-fix",
                head=head1, now="t3", outcome=outcome_1,
            )["work_items"][self.WI]
            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "republish (same content)",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_1),
            )

            head2 = repo.commit("second excluded-only doc fix", filename="docs/notes-b.md")
            work_item_2 = s2_work_item | {"work_item_id": self.WI}
            resolved_t_2 = ws.verify_implementation_provenance_recovery(
                repo.root, work_item_2, base_commit=repo.base, head=head2,
            )
            self.assertEqual(resolved_t_2, s2)
            s3_work_item = ws.apply_implementation_provenance_recovery(
                _base_state(**{self.WI: work_item_2}), self.WI, now="t9",
            )["work_items"][self.WI]
            s3 = _commit_state_only(
                repo, self.WI, s3_work_item, "recover stale generation_head",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_2),
            )

            final_work_item = s3_work_item | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertEqual(result, s3)

    def test_recovery_then_republication_chain_validates(self):
        """Item 289 (`WF8c`, `GPT-R51-003`), second half: the opposite
        writer order from `test_republication_then_recovery_chain_validates`
        -- a recovery link followed by a republication link -- validates
        identically, confirming the chain mechanics are order-agnostic
        between the two legal writers."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            ordinary_state = self._ordinary_state(repo, p)
            t = _commit_state_only(repo, self.WI, ordinary_state, "record gen", trailers=_record_trailers(self.WI, 1))

            head1 = repo.commit("legitimate excluded-only doc fix", filename="docs/notes-a.md")
            work_item_1 = ordinary_state | {"work_item_id": self.WI}
            resolved_t_1 = ws.verify_implementation_provenance_recovery(
                repo.root, work_item_1, base_commit=repo.base, head=head1,
            )
            self.assertEqual(resolved_t_1, t)
            s2_work_item = ws.apply_implementation_provenance_recovery(
                _base_state(**{self.WI: work_item_1}), self.WI, now="t9",
            )["work_items"][self.WI]
            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "recover stale generation_head",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_1),
            )

            feedback_state = self._enter_applying_review_feedback(repo, s2_work_item, state_revision=4)
            head2 = repo.commit("finding rejected", filename="docs/notes-b.md")
            work_item_2 = feedback_state | {"work_item_id": self.WI}
            outcome_2, resolved_t_2 = ws.resolve_bundle_generation_outcome(
                repo.root, work_item_2, base_commit=repo.base, head=head2,
            )
            self.assertEqual((outcome_2, resolved_t_2), ("same_content", s2))
            s3_work_item = ws.record_bundle_generation(
                _base_state(**{self.WI: work_item_2}), self.WI, stage="post-fix",
                head=head2, now="t10", outcome=outcome_2,
            )["work_items"][self.WI]
            s3 = _commit_state_only(
                repo, self.WI, s3_work_item, "republish (same content)",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_2),
            )

            final_work_item = s3_work_item | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertEqual(result, s3)


class TestSameContentRepublicationFromSelfReviewingImplementation(unittest.TestCase):
    """`WF8c` items 311/312 (revision 38, `GPT-R54-002`): the same
    same-content-republication mechanics
    `TestSameContentRepublicationEndToEnd` exercises from
    `APPLYING_REVIEW_FEEDBACK` also apply, unchanged, from
    `SELF_REVIEWING_IMPLEMENTATION` -- the second source phase revision 38
    widened condition 4's recovered-role contract to cover, reached when a
    later checkpoint's own self-review round turns out to be a no-op
    against the currently-reviewed content (a no-op implementation pass, a
    plan-only correction requiring no protected implementation change, or
    a fix reverted before publication)."""

    WI = "wi"
    WORK_ITEM_TYPE = "process"

    def _seed_base_state(self, repo: "ScratchRepo") -> None:
        _commit_state_only(repo, self.WI, {
            "work_item_id": self.WI, "work_item_type": self.WORK_ITEM_TYPE,
            "reviewed_implementation_head": None, "implementation_revision": 0,
            "phase": "IMPLEMENTING", "state_revision": 0, "last_transition": "t0",
        }, "seed base state")

    def test_republication_from_self_reviewing_implementation_then_full_interval_validates(self):
        """Item 311: a same-content round re-entered from
        `SELF_REVIEWING_IMPLEMENTATION` (rather than
        `APPLYING_REVIEW_FEEDBACK`) produces a well-formed recovered-role
        `S2` with a real `SELF_REVIEWING_IMPLEMENTATION` ->
        `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` transition,
        `reviewed_implementation_head`/`implementation_revision`
        unchanged, and validates end to end -- the exact case revision
        35-37 left with no legal outcome."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            ordinary_state = _provenance_state(
                self.WI, reviewed_implementation_head=p, implementation_revision=1,
            ) | {"work_item_type": self.WORK_ITEM_TYPE}
            t = _commit_state_only(
                repo, self.WI, ordinary_state, "record gen", trailers=_record_trailers(self.WI, 1),
            )

            # The next checkpoint's own self-review round begins: back to
            # SELF_REVIEWING_IMPLEMENTATION, never through
            # APPLYING_REVIEW_FEEDBACK -- mirroring
            # TestSameContentRepublicationEndToEnd's own
            # _enter_applying_review_feedback helper for the other source.
            self_review_state = ordinary_state | {
                "phase": "SELF_REVIEWING_IMPLEMENTATION", "state_revision": 2,
            }
            _commit_state_only(repo, self.WI, self_review_state, "re-enter self-reviewing implementation")
            head = repo.commit("no-op pass, reverted before publication", filename="docs/notes.md")

            work_item = self_review_state | {"work_item_id": self.WI}
            outcome, resolved_t = ws.resolve_bundle_generation_outcome(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual((outcome, resolved_t), ("same_content", t))

            new_state = ws.record_bundle_generation(
                _base_state(**{self.WI: work_item}), self.WI, stage="implementation",
                head=head, now="t3", outcome=outcome,
            )
            s2_work_item = new_state["work_items"][self.WI]
            self.assertEqual(s2_work_item["reviewed_implementation_head"], p)
            self.assertEqual(s2_work_item["implementation_revision"], 1)
            self.assertEqual(s2_work_item["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "republish (same content, self-review)",
                trailers=_recovered_trailers(self.WI, 1, resolved_t),
            )
            final_work_item = s2_work_item | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertEqual(result, s2)
            self.assertTrue(
                ws.implementation_provenance_interval_reachable(repo.root, final_work_item, repo.base),
            )

    def test_both_source_phases_independently_satisfy_the_role_contract(self):
        """Item 312: an `APPLYING_REVIEW_FEEDBACK`-sourced and a
        `SELF_REVIEWING_IMPLEMENTATION`-sourced same-content republication
        commit are distinguished only by which real phase transition each
        one's `phase` field records -- both independently satisfy
        condition 4's now-three-combination recovered-role contract
        (`validate_bundle_generation_record_commit`), confirmed here by
        building one short chain of each from a common `T` and validating
        each terminal commit directly."""
        for source_phase, stage in (
            ("APPLYING_REVIEW_FEEDBACK", "post-fix"),
            ("SELF_REVIEWING_IMPLEMENTATION", "implementation"),
        ):
            with self.subTest(source_phase=source_phase):
                with ScratchRepo() as repo:
                    _write_test_artifacts_declaration(repo, self.WI)
                    self._seed_base_state(repo)
                    p = repo.commit("protected fix", filename="src/Foo.kt")
                    ordinary_state = _provenance_state(
                        self.WI, reviewed_implementation_head=p, implementation_revision=1,
                    ) | {"work_item_type": self.WORK_ITEM_TYPE}
                    t = _commit_state_only(
                        repo, self.WI, ordinary_state, "record gen", trailers=_record_trailers(self.WI, 1),
                    )
                    source_state = ordinary_state | {"phase": source_phase, "state_revision": 2}
                    _commit_state_only(repo, self.WI, source_state, f"enter {source_phase}")
                    head = repo.commit("no-op", filename="docs/notes.md")

                    work_item = source_state | {"work_item_id": self.WI}
                    outcome, resolved_t = ws.resolve_bundle_generation_outcome(
                        repo.root, work_item, base_commit=repo.base, head=head,
                    )
                    self.assertEqual((outcome, resolved_t), ("same_content", t))
                    new_state = ws.record_bundle_generation(
                        _base_state(**{self.WI: work_item}), self.WI, stage=stage,
                        head=head, now="t3", outcome=outcome,
                    )
                    s2_work_item = new_state["work_items"][self.WI]
                    s2 = _commit_state_only(
                        repo, self.WI, s2_work_item, "republish (same content)",
                        trailers=_recovered_trailers(self.WI, 1, resolved_t),
                    )
                    ws.validate_bundle_generation_record_commit(repo.root, s2, self.WI)  # must not raise


class TestValidateTechnicalApprovalCommit(unittest.TestCase):
    """`WF8c`, missing-test item 285: `validate_technical_approval_commit`'s
    own exhaustive field-mutation check, mirroring items 267/254's
    generation-record coverage for the technical-approval commit
    (`apply_technical_approval`'s own `{"technical_approval", "phase",
    "state_revision", "last_transition"}` exact field set, widened
    workflow-2.5.0 CP12 with `implementation_review_stages` -- see
    `TestTechnicalApprovalCommitAdmitsImplementationReviewStagesResidue`
    below for why)."""

    WI = "wi"

    def _parent_state(self) -> dict:
        return {
            "work_item_id": self.WI, "work_item_type": "process",
            "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            "technical_approval": None, "state_revision": 5, "last_transition": "t5",
        }

    def test_well_formed_commit_passes(self):
        with ScratchRepo() as repo:
            _seed_base_provenance_state(repo, self.WI)
            parent_state = self._parent_state()
            _commit_state_only(repo, self.WI, parent_state, "parent")
            child_state = parent_state | {
                "technical_approval": {"status": "CURRENT"},
                "phase": "AWAITING_FUNCTIONAL_REVIEW", "state_revision": 6, "last_transition": "t6",
            }
            commit = _commit_state_only(repo, self.WI, child_state, "technical approval")
            ws.validate_technical_approval_commit(repo.root, commit, self.WI)  # must not raise

    def test_each_representative_forbidden_field_class_refused(self):
        """Item 285: one representative forbidden field from each class --
        `plan_approval`, `active_work_item_id` (top-level), a `checkpoints`
        entry, `functional_acceptance_status`, `reviewed_implementation_head`,
        `implementation_revision`, and another work item's own state --
        each independently fails the exhaustive check even though the
        commit still nominally 'touches only WORKFLOW_STATE.json'."""
        with ScratchRepo() as repo:
            _seed_base_provenance_state(repo, self.WI)
            parent_state = self._parent_state() | {
                "plan_approval": {"status": "CURRENT"}, "checkpoints": {"WF1": {"status": "COMPLETE"}},
                "functional_acceptance_status": None,
                "reviewed_implementation_head": "a" * 40, "implementation_revision": 1,
            }
            _commit_state_only(repo, self.WI, parent_state, "parent")
            base_child = parent_state | {
                "technical_approval": {"status": "CURRENT"},
                "phase": "AWAITING_FUNCTIONAL_REVIEW", "state_revision": 6, "last_transition": "t6",
            }

            variants = {
                "plan_approval": base_child | {"plan_approval": {"status": "STALE"}},
                "functional_acceptance_status": base_child | {"functional_acceptance_status": "PENDING"},
                "reviewed_implementation_head": base_child | {"reviewed_implementation_head": "b" * 40},
                "implementation_revision": base_child | {"implementation_revision": 2},
            }
            for label, variant_state in variants.items():
                with self.subTest(field=label):
                    with ScratchRepo() as vrepo:
                        _seed_base_provenance_state(vrepo, self.WI)
                        _commit_state_only(vrepo, self.WI, parent_state, "parent")
                        commit = _commit_state_only(vrepo, self.WI, variant_state, "technical approval")
                        with self.assertRaises(ws.MalformedTechnicalApprovalCommitError):
                            ws.validate_technical_approval_commit(vrepo.root, commit, self.WI)

            # checkpoints entry change -- same class, exercised via the
            # full work-items diff since checkpoints lives inside it too.
            with ScratchRepo() as vrepo:
                _seed_base_provenance_state(vrepo, self.WI)
                _commit_state_only(vrepo, self.WI, parent_state, "parent")
                variant_state = base_child | {"checkpoints": {"WF1": {"status": "COMPLETE"}, "WF2": {"status": "COMPLETE"}}}
                commit = _commit_state_only(vrepo, self.WI, variant_state, "technical approval")
                with self.assertRaises(ws.MalformedTechnicalApprovalCommitError):
                    ws.validate_technical_approval_commit(vrepo.root, commit, self.WI)

            # top-level active_work_item_id -- caught by _forbidden_state_mutation.
            with ScratchRepo() as vrepo:
                full = vrepo.root / STATE_REL_PATH
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_bytes(ws._serialize_state({
                    "schema_version": 1, "active_work_item_id": "wi", "work_items": {self.WI: parent_state},
                }))
                _run(["git", "add", STATE_REL_PATH], cwd=vrepo.root)
                _run(["git", "commit", "-q", "-m", "parent"], cwd=vrepo.root)
                full.write_bytes(ws._serialize_state({
                    "schema_version": 1, "active_work_item_id": "other-item", "work_items": {self.WI: base_child},
                }))
                _run(["git", "add", STATE_REL_PATH], cwd=vrepo.root)
                _run(["git", "commit", "-q", "-m", "technical approval"], cwd=vrepo.root)
                with self.assertRaises(ws.MalformedTechnicalApprovalCommitError):
                    ws.validate_technical_approval_commit(vrepo.root, vrepo.head(), self.WI)

            # another work item's own state changing in the same commit.
            with ScratchRepo() as vrepo:
                full = vrepo.root / STATE_REL_PATH
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_bytes(ws._serialize_state({
                    "schema_version": 1,
                    "work_items": {self.WI: parent_state, "other-wi": {"state_revision": 1}},
                }))
                _run(["git", "add", STATE_REL_PATH], cwd=vrepo.root)
                _run(["git", "commit", "-q", "-m", "parent"], cwd=vrepo.root)
                full.write_bytes(ws._serialize_state({
                    "schema_version": 1,
                    "work_items": {self.WI: base_child, "other-wi": {"state_revision": 2}},
                }))
                _run(["git", "add", STATE_REL_PATH], cwd=vrepo.root)
                _run(["git", "commit", "-q", "-m", "technical approval"], cwd=vrepo.root)
                with self.assertRaises(ws.MalformedTechnicalApprovalCommitError):
                    ws.validate_technical_approval_commit(vrepo.root, vrepo.head(), self.WI)

    def test_missing_technical_approval_or_phase_refused(self):
        """A commit that sets `phase` without `technical_approval` (or vice
        versa) is not a real technical-approval transition."""
        with ScratchRepo() as repo:
            _seed_base_provenance_state(repo, self.WI)
            parent_state = self._parent_state()
            _commit_state_only(repo, self.WI, parent_state, "parent")
            phase_only = parent_state | {
                "phase": "AWAITING_FUNCTIONAL_REVIEW", "state_revision": 6, "last_transition": "t6",
            }
            commit = _commit_state_only(repo, self.WI, phase_only, "phase only")
            with self.assertRaises(ws.MalformedTechnicalApprovalCommitError):
                ws.validate_technical_approval_commit(repo.root, commit, self.WI)


class TestTechnicalApprovalCommitAdmitsImplementationReviewStagesResidue(unittest.TestCase):
    """workflow-2.5.0 CP12 (disposable-repository functional validation):
    `TECHNICAL_APPROVAL_COMMIT_FIELDS`' own widening, found live -- not
    hand-derived -- by CP12's own end-to-end `"2.2"` scenario, whose
    `/review-implementation` (local) then `/record-manual-implementation-
    review` (manual) rounds leave `implementation_review_stages` uncommitted
    (neither writer creates its own durability commit -- see `review-
    implementation.md`'s "2.2" authoritative branch step A6 and
    `record-manual-implementation-review.md`'s identical shape) until the
    very next commit, which for a `"2.2"` item's ordinary positive path is
    always `/approve-review implementation`'s own technical-approval commit.
    Before this widening, that commit's own field diff always included
    `implementation_review_stages` and `validate_technical_approval_commit`
    always refused it outright -- the mainline, first-round positive path
    for *every* `"2.2"` item, not an edge case."""

    WI = "wi"

    def test_technical_approval_commit_with_ledger_residue_passes(self):
        with ScratchRepo() as repo:
            _seed_base_provenance_state(repo, self.WI)
            parent_state = {
                "work_item_id": self.WI, "work_item_type": "process",
                "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                "technical_approval": None, "state_revision": 5, "last_transition": "t5",
                "implementation_review_stages": {
                    "review_content_id": "c-1",
                    "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {
                        "bundle_id": "b-1", "verdict": "APPROVE", "round": 1, "completed_at": "t3",
                    },
                    "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None,
                },
            }
            _commit_state_only(repo, self.WI, parent_state, "parent")
            # The manual-approve round's own ledger write, uncommitted --
            # exactly the residue a real /record-manual-implementation-review
            # invocation leaves behind, riding into the very next commit.
            child_state = parent_state | {
                "technical_approval": {"status": "CURRENT"},
                "phase": "AWAITING_FUNCTIONAL_REVIEW", "state_revision": 6, "last_transition": "t6",
                "implementation_review_stages": {
                    **parent_state["implementation_review_stages"],
                    "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": {
                        "bundle_id": "b-1", "verdict": "APPROVE", "round": 1, "completed_at": "t6",
                    },
                },
            }
            commit = _commit_state_only(repo, self.WI, child_state, "technical approval")
            ws.validate_technical_approval_commit(repo.root, commit, self.WI)  # must not raise

    def test_a_1_or_21_item_never_carries_this_residue_so_the_widening_is_moot_for_it(self):
        """Safety argument, made concrete: `"1"`/`"2.1"`'s own state
        mutators never write `implementation_review_stages` at all, so
        widening this set can never let a genuinely unrelated field slip
        through for them -- the field is simply always absent from their
        own diffs."""
        with ScratchRepo() as repo:
            _seed_base_provenance_state(repo, self.WI)
            parent_state = {
                "work_item_id": self.WI, "work_item_type": "process",
                "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                "technical_approval": None, "state_revision": 5, "last_transition": "t5",
            }
            _commit_state_only(repo, self.WI, parent_state, "parent")
            child_state = parent_state | {
                "technical_approval": {"status": "CURRENT"},
                "phase": "AWAITING_FUNCTIONAL_REVIEW", "state_revision": 6, "last_transition": "t6",
            }
            commit = _commit_state_only(repo, self.WI, child_state, "technical approval")
            ws.validate_technical_approval_commit(repo.root, commit, self.WI)  # must not raise
            self.assertNotIn(
                "implementation_review_stages",
                ws._work_item_field_diff(repo.root, commit, self.WI),
            )


class TestApplyImplementationProvenanceRecovery(unittest.TestCase):
    """`WF8c` (b), `WFR-62`: `apply_implementation_provenance_recovery`'s
    own state-half contract -- the recovered-role field set, minus `phase`
    itself since recovery never transitions it (unlike
    `record_bundle_generation`'s two entry points, both of which
    transition *into* `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`)."""

    def test_bumps_state_revision_and_last_transition_only(self):
        state = _base_state(wi={
            "work_item_id": "wi", "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
            "state_revision": 3, "last_transition": "t3",
        })
        new_state = ws.apply_implementation_provenance_recovery(state, "wi", now="t4")
        work_item = new_state["work_items"]["wi"]
        self.assertEqual(work_item["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(work_item["reviewed_implementation_head"], "p" * 40)
        self.assertEqual(work_item["implementation_revision"], 1)
        self.assertEqual(work_item["state_revision"], 4)
        self.assertEqual(work_item["last_transition"], "t4")

    def test_illegal_source_phase_refused(self):
        state = _base_state(wi={
            "work_item_id": "wi", "phase": "SELF_REVIEWING_IMPLEMENTATION",
            "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
            "state_revision": 3, "last_transition": "t3",
        })
        with self.assertRaises(ws.IllegalImplementationProvenanceRecoverySourcePhaseError):
            ws.apply_implementation_provenance_recovery(state, "wi", now="t4")

    def test_original_state_untouched(self):
        state = _base_state(wi={
            "work_item_id": "wi", "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
            "state_revision": 3, "last_transition": "t3",
        })
        before = copy.deepcopy(state)
        ws.apply_implementation_provenance_recovery(state, "wi", now="t4")
        self.assertEqual(state, before)


class TestVerifyImplementationProvenanceRecovery(unittest.TestCase):
    """`WF8c` (b), `WFR-62`: the read-only precondition pair
    `/recover-implementation-provenance` runs before creating anything --
    reuses `WF8c` (c)'s own `resolve_bundle_generation_outcome`, adding
    only the phase gate and the already-current-tip no-op refusal."""

    WI = "wi"

    def test_illegal_source_phase_refused(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head = repo.commit("resolved via excluded content", filename="docs/notes.md")
            work_item = state | {
                "work_item_id": self.WI, "work_item_type": "process",
                "phase": "SELF_REVIEWING_IMPLEMENTATION",
            }
            with self.assertRaises(ws.IllegalImplementationProvenanceRecoverySourcePhaseError):
                ws.verify_implementation_provenance_recovery(
                    repo.root, work_item, base_commit=repo.base, head=head,
                )

    def test_content_differs_not_applicable(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected v1", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head = repo.commit("protected v2", filename="src/Foo.kt")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            with self.assertRaises(ws.ImplementationProvenanceRecoveryNotApplicableError):
                ws.verify_implementation_provenance_recovery(
                    repo.root, work_item, base_commit=repo.base, head=head,
                )

    def test_nothing_to_recover_when_head_already_current(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            with self.assertRaises(ws.ImplementationProvenanceRecoveryNotApplicableError):
                ws.verify_implementation_provenance_recovery(
                    repo.root, work_item, base_commit=repo.base, head=t,
                )

    def test_protected_commit_in_interval_propagates_underlying_error(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = _write_and_commit(repo, "src/Foo.kt", "v1\n", "protected v1")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            _write_and_commit(repo, "src/Foo.kt", "v2\n", "protected edit")
            head = _write_and_commit(repo, "src/Foo.kt", "v1\n", "protected revert")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            with self.assertRaises(ws.ProtectedPathInProvenanceIntervalError):
                ws.verify_implementation_provenance_recovery(
                    repo.root, work_item, base_commit=repo.base, head=head,
                )

    def test_identical_content_with_clean_excluded_interval_returns_t(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head = repo.commit("resolved via excluded content", filename="docs/notes.md")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            result = ws.verify_implementation_provenance_recovery(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(result, t)

    def test_unclassified_path_added_then_removed_in_interval_refuses_naming_the_commit(self):
        """Item 248 (`WF8c`): an unclassified path added by one commit and
        removed by a later one leaves the interval's endpoint content
        identical, but the recovery precondition still refuses, naming the
        offending commit -- `classify_path_implementation_stage` itself
        fails closed by *raising* `UnclassifiedPathError(path)` rather than
        returning an "unclassified" classification, so
        `_classify_generation_record_interval` must catch it and re-raise
        with commit context, exactly as it already does for a `protected`
        classification (`test_protected_commit_in_interval_propagates_underlying_error`
        above)."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            added = _write_and_commit(repo, "other/mystery.txt", "???\n", "add unclassified")
            _run(["git", "rm", "-q", "other/mystery.txt"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "remove unclassified"], cwd=repo.root)
            head = repo.head()
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            with self.assertRaises(ws.ProtectedPathInProvenanceIntervalError) as ctx:
                ws.verify_implementation_provenance_recovery(
                    repo.root, work_item, base_commit=repo.base, head=head,
                )
            self.assertIn(added, str(ctx.exception))
            self.assertIn("other/mystery.txt", str(ctx.exception))


class TestValidateImplementationProvenanceRecoveryConfirmation(unittest.TestCase):
    """`WF8c` (b), missing-test item 251: `/recover-implementation-provenance`'s
    own user-confirmation guard -- "identical in spirit to `/approve-review`'s
    own `validate_user_confirmation` guard", but bound to the exact
    superseded commit SHA `t` rather than an approval stage name, since
    recovery is not a member of `APPROVAL_STAGES`."""

    def test_empty_text_refused(self):
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_implementation_provenance_recovery_confirmation(
                "", work_item_id="wi", superseded_commit="c" * 40,
            )

    def test_generic_go_ahead_with_no_sha_refused(self):
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_implementation_provenance_recovery_confirmation(
                "yes, go ahead and recover wi", work_item_id="wi", superseded_commit="c" * 40,
            )

    def test_missing_work_item_id_refused(self):
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_implementation_provenance_recovery_confirmation(
                f"recover {'c' * 40}", work_item_id="wi", superseded_commit="c" * 40,
            )

    def test_wrong_commit_refused(self):
        with self.assertRaises(ws.UserConfirmationRejectedError):
            ws.validate_implementation_provenance_recovery_confirmation(
                f"recover wi past {'d' * 40}", work_item_id="wi", superseded_commit="c" * 40,
            )

    def test_exact_work_item_and_commit_accepted(self):
        ws.validate_implementation_provenance_recovery_confirmation(
            f"recover wi past commit {'c' * 40}", work_item_id="wi", superseded_commit="c" * 40,
        )  # no raise


class TestRecoveredRoleLegalParentPhaseCombinations(unittest.TestCase):
    """`WF8c` (b)/(c), items 292/293/294/313: direct, isolated coverage of
    `validate_bundle_generation_record_commit`'s recovered-role branch
    against each of its legal parent-phase combinations and the illegal
    ones around them -- distinguished entirely by committed parent state
    (`RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES`) and the
    single required committed-phase target, never by which command
    (`/recover-implementation-provenance` vs `record_bundle_generation`'s
    same-content branch) happened to produce the commit -- no such signal
    exists anywhere in a commit's own trailers or fields for the validator
    to consult, which is exactly item 294's claim. `AWAITING_FUNCTIONAL_
    REVIEW` joined the legal set as workflow-v2-3-followups's own
    continued scope (self-discovered during this item's own
    `/accept-milestone` pre-flight) -- see
    `test_awaiting_functional_review_parent_phase_validates` below; this
    class's own name was originally "ThreeCombination", pinned to a count
    that widening made stale, so it no longer names one."""

    WI = "wi"

    def _seed_and_parent(self, repo: "ScratchRepo", parent_phase: str) -> tuple[str, str]:
        _write_test_artifacts_declaration(repo, self.WI)
        _seed_base_provenance_state(repo, self.WI)
        p = repo.commit("protected fix", filename="src/Foo.kt")
        parent_state = {
            "work_item_id": self.WI, "work_item_type": "process",
            "reviewed_implementation_head": p, "implementation_revision": 1,
            "phase": parent_phase, "state_revision": 5, "last_transition": "t5",
        }
        parent = _commit_state_only(repo, self.WI, parent_state, "parent")
        return p, parent

    def _child(self, repo: "ScratchRepo", p: str, committed_phase: str, supersedes: str) -> str:
        child_state = {
            "work_item_id": self.WI, "work_item_type": "process",
            "reviewed_implementation_head": p, "implementation_revision": 1,
            "phase": committed_phase, "state_revision": 6, "last_transition": "t6",
        }
        return _commit_state_only(
            repo, self.WI, child_state, "recovered-role commit",
            trailers=_recovered_trailers(self.WI, 1, supersedes),
        )

    def test_item_292_both_recovery_and_republication_parent_phases_independently_validate(self):
        with ScratchRepo() as repo:
            p, parent = self._seed_and_parent(repo, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
            child = self._child(repo, p, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", parent)
            ws.validate_bundle_generation_record_commit(repo.root, child, self.WI)  # must not raise

        with ScratchRepo() as repo:
            p, parent = self._seed_and_parent(repo, "APPLYING_REVIEW_FEEDBACK")
            child = self._child(repo, p, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", parent)
            ws.validate_bundle_generation_record_commit(repo.root, child, self.WI)  # must not raise

    def test_item_293_awaiting_technical_approval_parent_phase_refused(self):
        with ScratchRepo() as repo:
            p, parent = self._seed_and_parent(repo, "AWAITING_TECHNICAL_APPROVAL")
            child = self._child(repo, p, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", parent)
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.validate_bundle_generation_record_commit(repo.root, child, self.WI)
            self.assertIn("AWAITING_TECHNICAL_APPROVAL", str(ctx.exception))

    def test_item_294_wrong_committed_phase_refused_despite_legal_parent(self):
        """A commit whose parent phase is the legal `APPLYING_REVIEW_FEEDBACK`
        republication source, but whose own committed phase was left
        value-wise unchanged (`/recover-implementation-provenance`'s own
        shape) instead of performing the real transition
        `record_bundle_generation`'s republication path always performs, is
        refused -- the committed-phase requirement is unconditional,
        regardless of which parent phase produced it."""
        with ScratchRepo() as repo:
            p, parent = self._seed_and_parent(repo, "APPLYING_REVIEW_FEEDBACK")
            child = self._child(repo, p, "APPLYING_REVIEW_FEEDBACK", parent)
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.validate_bundle_generation_record_commit(repo.root, child, self.WI)
            self.assertIn("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", str(ctx.exception))

    def test_item_313_self_reviewing_parent_wrong_committed_phase_and_illegal_parent_both_refused(self):
        with ScratchRepo() as repo:
            p, parent = self._seed_and_parent(repo, "SELF_REVIEWING_IMPLEMENTATION")
            child = self._child(repo, p, "SELF_REVIEWING_IMPLEMENTATION", parent)
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
                ws.validate_bundle_generation_record_commit(repo.root, child, self.WI)

        with ScratchRepo() as repo:
            p, parent = self._seed_and_parent(repo, "IMPLEMENTING")
            child = self._child(repo, p, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", parent)
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.validate_bundle_generation_record_commit(repo.root, child, self.WI)
            self.assertIn("IMPLEMENTING", str(ctx.exception))

    def test_awaiting_functional_review_parent_phase_validates(self):
        """workflow-v2-3-followups continued scope: `AWAITING_FUNCTIONAL_
        REVIEW` joined `RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_
        PHASES` alongside `record_bundle_generation`'s own widened
        per-stage legality -- a recovered-role commit whose parent sat at
        `AWAITING_FUNCTIONAL_REVIEW` (the functional-review bounded-fix
        path) validates exactly like the pre-existing
        `APPLYING_REVIEW_FEEDBACK`/`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
        combinations `test_item_292` already covers."""
        with ScratchRepo() as repo:
            p, parent = self._seed_and_parent(repo, "AWAITING_FUNCTIONAL_REVIEW")
            child = self._child(repo, p, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", parent)
            ws.validate_bundle_generation_record_commit(repo.root, child, self.WI)  # must not raise


class TestImplementationProvenanceRecoveryEndToEnd(unittest.TestCase):
    """`WF8c` (b), `WFR-62`: the full round trip from
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` itself --
    `verify_implementation_provenance_recovery` sanctions the recovery,
    `apply_implementation_provenance_recovery` produces the state to
    commit, the caller commits it as a recovered-role
    `Workflow-Supersedes` commit, and
    `verify_implementation_provenance_interval` validates the resulting
    chain -- the standalone counterpart of
    `TestSameContentRepublicationEndToEnd`, entered from
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` rather than
    `APPLYING_REVIEW_FEEDBACK`/`SELF_REVIEWING_IMPLEMENTATION`."""

    WI = "wi"
    WORK_ITEM_TYPE = "process"

    def _seed_base_state(self, repo: "ScratchRepo") -> None:
        """Like `_seed_base_provenance_state`, plus `work_item_type` baked
        in from this work item's very first committed state -- must be
        present identically across every commit in the chain (never
        introduced partway through) or it would itself register as a
        spurious field change against
        `validate_bundle_generation_record_commit`'s exact-subset check,
        exactly as `TestSameContentRepublicationEndToEnd._seed_base_state`
        documents for the same reason."""
        _commit_state_only(repo, self.WI, {
            "work_item_id": self.WI, "work_item_type": self.WORK_ITEM_TYPE,
            "reviewed_implementation_head": None, "implementation_revision": 0,
            "phase": "IMPLEMENTING", "state_revision": 0, "last_transition": "t0",
        }, "seed base state")

    def test_single_recovery_then_full_interval_validates(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(
                self.WI, reviewed_implementation_head=p, implementation_revision=1,
            ) | {"work_item_type": self.WORK_ITEM_TYPE}
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head = repo.commit("legitimate excluded-only doc fix", filename="docs/notes.md")

            work_item = state | {"work_item_id": self.WI}
            resolved_t = ws.verify_implementation_provenance_recovery(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(resolved_t, t)

            new_state = ws.apply_implementation_provenance_recovery(
                _base_state(**{self.WI: work_item}), self.WI, now="t9",
            )
            s2_work_item = new_state["work_items"][self.WI]
            self.assertEqual(s2_work_item["reviewed_implementation_head"], p)
            self.assertEqual(s2_work_item["implementation_revision"], 1)
            self.assertEqual(s2_work_item["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "recover stale generation_head",
                trailers=_recovered_trailers(self.WI, 1, resolved_t),
            )
            final_work_item = s2_work_item | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertEqual(result, s2)
            self.assertTrue(
                ws.implementation_provenance_interval_reachable(repo.root, final_work_item, repo.base),
            )

            # Item 244 (`WF8c`): retrying the recovery operation once `S2`
            # is already the current tip is idempotent -- no second
            # superseding commit, refused as nothing-to-recover rather than
            # silently producing a duplicate -- and repeated discovery
            # still resolves to the same `S2`, never a phantom or a
            # different commit.
            with self.assertRaises(ws.ImplementationProvenanceRecoveryNotApplicableError):
                ws.verify_implementation_provenance_recovery(
                    repo.root, final_work_item, base_commit=repo.base, head=s2,
                )
            self.assertEqual(
                ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base), s2,
            )

    def test_second_sequential_recovery_chain_validates(self):
        """D-Commit-Provenance "Multiple sequential recoveries /
        supersession chain", exercised via the standalone recovery command
        rather than `record_bundle_generation`'s own same-content path: a
        second recovery, invoked again from
        `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` after the first `S2` is
        itself staled by a further excluded-only commit, produces
        `P -> T -> U1 -> S2 -> U2 -> S3`, and the approval gate validates
        the entire chain."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(
                self.WI, reviewed_implementation_head=p, implementation_revision=1,
            ) | {"work_item_type": self.WORK_ITEM_TYPE}
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            head1 = repo.commit("first excluded-only doc fix", filename="docs/notes-a.md")

            work_item_1 = state | {"work_item_id": self.WI}
            resolved_t_1 = ws.verify_implementation_provenance_recovery(
                repo.root, work_item_1, base_commit=repo.base, head=head1,
            )
            self.assertEqual(resolved_t_1, t)
            s2_work_item = ws.apply_implementation_provenance_recovery(
                _base_state(**{self.WI: work_item_1}), self.WI, now="t9",
            )["work_items"][self.WI]
            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "recover stale generation_head 1",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_1),
            )

            head2 = repo.commit("second excluded-only doc fix", filename="docs/notes-b.md")
            work_item_2 = s2_work_item | {"work_item_id": self.WI, "work_item_type": self.WORK_ITEM_TYPE}
            resolved_t_2 = ws.verify_implementation_provenance_recovery(
                repo.root, work_item_2, base_commit=repo.base, head=head2,
            )
            self.assertEqual(resolved_t_2, s2)
            s3_work_item = ws.apply_implementation_provenance_recovery(
                _base_state(**{self.WI: work_item_2}), self.WI, now="t10",
            )["work_items"][self.WI]
            s3 = _commit_state_only(
                repo, self.WI, s3_work_item, "recover stale generation_head 2",
                trailers=_recovered_trailers(self.WI, 1, resolved_t_2),
            )

            final_work_item = s3_work_item | {"work_item_id": self.WI}
            result = ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertEqual(result, s3)

    def test_malformed_ordinary_s_refuses_whole_interval_even_with_valid_recovery_after(self):
        """Item 278 (`WF8c`, `GPT-R50-002` scenario A): a malformed ordinary
        `S` (carrying an extra forbidden `technical_approval` mutation
        alongside its legitimate round fields), followed by an otherwise-
        valid recovery (`U`, then `S2`), refuses the whole interval at
        `verify_implementation_provenance_interval`, naming `S` as the
        offending historical commit -- even though `S2` itself is
        well-formed and current. `_classify_generation_record_interval`
        walks oldest-first and validates every generation-record commit it
        encounters, so a malformed `S` is caught before `S2` is ever
        reached, regardless of how well-formed the chain becomes later."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(
                self.WI, reviewed_implementation_head=p, implementation_revision=1,
            ) | {"work_item_type": self.WORK_ITEM_TYPE}
            malformed_extra = {"technical_approval": {"status": "CURRENT", "approved_review_content_id": "a" * 64}}
            content = json.dumps({"schema_version": 1, "work_items": {self.WI: state | malformed_extra}})
            s = _commit_state_with_trailers(repo, content, _record_trailers(self.WI, 1), message="record gen")
            repo.commit("excluded-only doc fix", filename="docs/notes.md")
            s2_content = json.dumps({"schema_version": 1, "work_items": {self.WI: state | {"state_revision": 2}}})
            _commit_state_with_trailers(
                repo, s2_content, _recovered_trailers(self.WI, 1, s), message="recover stale generation_head",
            )
            final_work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertIn(s, str(ctx.exception))

    def test_malformed_recovered_s2_refuses_whole_interval_even_with_valid_recovery_after(self):
        """Item 279 (`WF8c`, `GPT-R50-002` scenario B): a valid ordinary
        `S`, followed by a malformed recovered `S2` (mutating a
        `WORKFLOW_STATE.json` field outside `phase`/`state_revision`/
        `last_transition`), followed by an otherwise-valid second recovery
        (`U`, then `S3`), refuses the whole interval, naming `S2` as the
        offending historical commit, even though `S3` itself is
        well-formed and current."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            self._seed_base_state(repo)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(
                self.WI, reviewed_implementation_head=p, implementation_revision=1,
            ) | {"work_item_type": self.WORK_ITEM_TYPE}
            s = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            repo.commit("first excluded-only doc fix", filename="docs/notes-a.md")
            malformed_s2_state = state | {"state_revision": 2, "reviewed_implementation_head": "0" * 40}
            s2 = _commit_state_only(
                repo, self.WI, malformed_s2_state, "recover (illegally changes reviewed_implementation_head)",
                trailers=_recovered_trailers(self.WI, 1, s),
            )
            repo.commit("second excluded-only doc fix", filename="docs/notes-b.md")
            s3_state = state | {"state_revision": 3}
            _commit_state_only(
                repo, self.WI, s3_state, "recover stale generation_head 2",
                trailers=_recovered_trailers(self.WI, 1, s2),
            )
            final_work_item = state | {"work_item_id": self.WI}
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError) as ctx:
                ws.verify_implementation_provenance_interval(repo.root, final_work_item, repo.base)
            self.assertIn(s2, str(ctx.exception))


class TestImplementationProvenanceRecoveryApprovalGateInteraction(unittest.TestCase):
    """`WF8c` (b), items 258/259: recovery's effect on
    `/approve-review implementation`'s own `WFR-03`/`D2` exact-`bundle_id`-
    match rule -- no new staleness mechanism is required (item 258), since
    `resolve_approval_basis`'s existing exact-match check already refuses a
    pre-recovery bundle's feedback the moment `current_bundle_id` differs,
    and recovery necessarily changes `current_bundle_id` (a new
    `generation_head`, `T` -> `S2`). `bundle_id` values below are opaque
    stand-ins tied to the real commit each represents -- `resolve_approval_basis`
    itself never inspects a `bundle_id`'s internal shape (see
    `TestApprovalBasisResolution`), only exact string equality, so a
    real commit-derived label documents the interaction precisely without
    depending on `workflow_fingerprint.compute_bundle_id`'s own,
    separately-tested, file-content mechanics."""

    WI = "wi"

    def test_full_round_trip_approve_recover_stale_refused_fresh_approve(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, self.WI)
            _seed_base_provenance_state(repo, self.WI)
            p = repo.commit("protected fix", filename="src/Foo.kt")
            state = _provenance_state(self.WI, reviewed_implementation_head=p, implementation_revision=1)
            t = _commit_state_only(repo, self.WI, state, "record gen", trailers=_record_trailers(self.WI, 1))
            b1 = f"bundle-for-{t}"

            # Item 259: a genuine external APPROVE against B1 (bundle at T)
            # succeeds before recovery ever happens.
            basis = ws.resolve_approval_basis(
                latest_round_status="APPROVE", feedback_bundle_id=b1, current_bundle_id=b1,
                user_confirmation="approve wi implementation", work_item_id=self.WI, stage="implementation",
            )
            self.assertEqual(basis, "EXTERNAL_APPROVE")

            # A legitimate excluded-only commit lands after T, staling the
            # bundle's generation_head without changing protected content.
            head = repo.commit("legitimate excluded-only doc fix", filename="docs/notes.md")
            work_item = state | {"work_item_id": self.WI, "work_item_type": "process"}
            resolved_t = ws.verify_implementation_provenance_recovery(
                repo.root, work_item, base_commit=repo.base, head=head,
            )
            self.assertEqual(resolved_t, t)
            s2_work_item = ws.apply_implementation_provenance_recovery(
                _base_state(**{self.WI: work_item}), self.WI, now="t9",
            )["work_items"][self.WI]
            s2 = _commit_state_only(
                repo, self.WI, s2_work_item, "recover stale generation_head",
                trailers=_recovered_trailers(self.WI, 1, resolved_t),
            )
            b2 = f"bundle-for-{s2}"
            self.assertNotEqual(b1, b2)

            # Item 258: B1's prior APPROVE, still naming its own (now stale)
            # bundle_id, is refused as EXTERNAL_APPROVE against the new
            # current bundle B2 -- no override text supplied, so it raises,
            # under the pre-existing exact-match rule alone.
            with self.assertRaises(ws.UserConfirmationRejectedError):
                ws.resolve_approval_basis(
                    latest_round_status="APPROVE", feedback_bundle_id=b1, current_bundle_id=b2,
                    user_confirmation="", work_item_id=self.WI, stage="implementation",
                )

            # Item 259: a fresh external review of B2 returns APPROVE, and
            # /approve-review implementation now succeeds via EXTERNAL_APPROVE
            # bound to B2 -- implementation_revision/reviewed_implementation_head
            # unchanged throughout the whole round trip.
            basis = ws.resolve_approval_basis(
                latest_round_status="APPROVE", feedback_bundle_id=b2, current_bundle_id=b2,
                user_confirmation="approve wi implementation", work_item_id=self.WI, stage="implementation",
            )
            self.assertEqual(basis, "EXTERNAL_APPROVE")
            self.assertEqual(s2_work_item["reviewed_implementation_head"], p)
            self.assertEqual(s2_work_item["implementation_revision"], 1)


class TestRemediationChildWorkItem(unittest.TestCase):
    def test_creates_child_with_derived_id_and_parent_link(self):
        state = _base_state(parent=_base_work_item(
            work_item_id="parent", work_item_type="product", work_item_kind="product",
        ))
        config = ws.default_config()
        new_state, child_id = ws.create_remediation_child_work_item(
            state, config, parent_work_item_id="parent", plan_path="p", registry_path="r",
            base_commit="feedcafe", now="t1",
        )
        self.assertEqual(child_id, "parent-remediation-1")
        child = new_state["work_items"][child_id]
        self.assertEqual(child["parent_work_item_id"], "parent")
        self.assertEqual(child["base_commit"], "feedcafe")
        self.assertEqual(child["work_item_type"], "product")
        self.assertEqual(child["work_item_kind"], "product")
        self.assertEqual(child["phase"], "PLANNING")
        self.assertEqual(child["governing_workflow_version"], config["default_workflow_version"])
        # parent entry itself is untouched, and the input state is unmutated
        self.assertNotIn("parent-remediation-1", state["work_items"])
        self.assertEqual(new_state["work_items"]["parent"], state["work_items"]["parent"])

    def test_numbering_increments_past_existing_children(self):
        state = _base_state(
            parent=_base_work_item(work_item_id="parent"),
            **{"parent-remediation-1": _base_work_item(
                work_item_id="parent-remediation-1", parent_work_item_id="parent",
            )},
        )
        config = ws.default_config()
        _, child_id = ws.create_remediation_child_work_item(
            state, config, parent_work_item_id="parent", plan_path="p", registry_path="r",
            base_commit="feedcafe", now="t1",
        )
        self.assertEqual(child_id, "parent-remediation-2")

    def test_never_touches_active_work_item_id(self):
        state = _base_state(parent=_base_work_item(work_item_id="parent"))
        state["active_work_item_id"] = "parent"
        config = ws.default_config()
        new_state, _ = ws.create_remediation_child_work_item(
            state, config, parent_work_item_id="parent", plan_path="p", registry_path="r",
            base_commit="feedcafe", now="t1",
        )
        self.assertEqual(new_state["active_work_item_id"], "parent")

    def test_resulting_state_is_valid(self):
        state = _base_state(parent=_base_work_item(work_item_id="parent"))
        config = ws.default_config()
        new_state, _ = ws.create_remediation_child_work_item(
            state, config, parent_work_item_id="parent", plan_path="p", registry_path="r",
            base_commit="feedcafe", now="t1",
        )
        ws.validate_state(new_state)  # must not raise -- parent_work_item_id resolves

    def test_child_enters_the_plan_review_stage_its_own_governing_version_selects(self):
        """Diagram finding `B1`: the lifecycle diagram claimed a remediation
        child "runs the full lifecycle beginning at
        `AWAITING_EXTERNAL_PLAN_REVIEW`". There is no remediation-specific
        entry rule at all -- the child is an ordinary work item, so
        `publish_plan_revision`'s own version branch decides, against the
        version `create_remediation_child_work_item` fixed from the config
        default at creation. Under this repository's own default (`"2.1"`)
        that is `AWAITING_LOCAL_PLAN_REVIEW`; only a `"1"` default produces
        the phase the diagram named."""
        for default_version, expected_phase in (
            ("2.1", "AWAITING_LOCAL_PLAN_REVIEW"),
            ("1", "AWAITING_EXTERNAL_PLAN_REVIEW"),
        ):
            with self.subTest(default_version=default_version):
                config = dict(ws.default_config(), default_workflow_version=default_version)
                state = _base_state(parent=_base_work_item(work_item_id="parent"))
                state["active_work_item_id"] = "parent"
                created, child_id = ws.create_remediation_child_work_item(
                    state, config, parent_work_item_id="parent", plan_path="p",
                    registry_path="r", base_commit="feedcafe", now="t1",
                )
                child = created["work_items"][child_id]
                self.assertEqual(child["governing_workflow_version"], default_version)
                self.assertEqual(child["phase"], "PLANNING")
                planned = ws.publish_plan_revision(created, child_id, 1, "t2")
                self.assertEqual(planned["work_items"][child_id]["phase"], expected_phase)
                # And planning the child never repoints focus away from the
                # parent, which is why every command driving it needs an
                # explicit work-item id.
                self.assertEqual(planned["active_work_item_id"], "parent")


class TestParentCompletionBlocksOnIncompleteChild(unittest.TestCase):
    def test_incomplete_children_lists_only_non_terminal_children(self):
        state = _base_state(
            parent=_base_work_item(work_item_id="parent"),
            **{
                "parent-remediation-1": _base_work_item(
                    work_item_id="parent-remediation-1", parent_work_item_id="parent",
                    phase="IMPLEMENTING",
                ),
                "parent-remediation-2": _base_work_item(
                    work_item_id="parent-remediation-2", parent_work_item_id="parent",
                    phase="MILESTONE_COMPLETE",
                ),
                "unrelated": _base_work_item(work_item_id="unrelated"),
            },
        )
        self.assertEqual(ws.incomplete_children(state, "parent"), ["parent-remediation-1"])

    def test_complete_work_item_refuses_while_child_incomplete(self):
        state = _base_state(
            parent=_base_work_item(work_item_id="parent", phase="AWAITING_USER_ACCEPTANCE"),
            **{"parent-remediation-1": _base_work_item(
                work_item_id="parent-remediation-1", parent_work_item_id="parent", phase="IMPLEMENTING",
            )},
        )
        with self.assertRaises(ws.IncompleteChildWorkItemError):
            ws.complete_work_item(state, "parent", now="t2", repo_root=Path("."))
        # refusing must not have mutated the input
        self.assertEqual(state["work_items"]["parent"]["phase"], "AWAITING_USER_ACCEPTANCE")

    def test_complete_work_item_succeeds_once_every_child_is_complete(self):
        state = _base_state(
            parent=_base_work_item(work_item_id="parent", phase="AWAITING_USER_ACCEPTANCE"),
            **{"parent-remediation-1": _base_work_item(
                work_item_id="parent-remediation-1", parent_work_item_id="parent",
                phase="MILESTONE_COMPLETE",
            )},
        )
        new_state = ws.complete_work_item(state, "parent", now="t2", repo_root=Path("."))
        self.assertEqual(new_state["work_items"]["parent"]["phase"], "MILESTONE_COMPLETE")

    def test_complete_work_item_with_no_children_still_succeeds(self):
        state = _base_state(wi=_base_work_item(phase="AWAITING_USER_ACCEPTANCE"))
        new_state = ws.complete_work_item(state, "wi", now="t2", repo_root=Path("."))
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "MILESTONE_COMPLETE")


class TestDanglingParentWorkItem(unittest.TestCase):
    def test_parent_work_item_id_naming_unknown_entry_rejected(self):
        state = _base_state(wi=_base_work_item(parent_work_item_id="nonexistent"))
        with self.assertRaises(ws.DanglingParentWorkItemError):
            ws.validate_state(state)

    def test_parent_work_item_id_naming_real_entry_accepted(self):
        state = _base_state(
            parent=_base_work_item(work_item_id="parent"),
            child=_base_work_item(work_item_id="child", parent_work_item_id="parent"),
        )
        ws.validate_state(state)  # must not raise


# =============================================================================
# D-Scoped-Remediation-Acceptance's surviving half (revision 27,
# GPT-R40-001/-002; resolves WF8B-002): registry_completion_status,
# milestone_complete_gate_reachable, complete_work_item's authoritative
# own-registry guard, and the functional-checklist evidence trailer
# discovery /prepare-functional-review writes and /review-functional reads.
#
# The other half -- /accept-scoped-remediation, its gate function, the
# pre-commit evidence guard, the confirmation binding-field parser, replay/
# duplicate classification and the acceptance record writer -- was retired
# with the command itself (ledger I10), together with every test that only
# proved that dead path.
# =============================================================================


def _write(repo, rel_path, content):
    full = repo.root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return full


def _commit_paths(repo, rel_paths, subject, trailers=None):
    for rel_path in rel_paths:
        _run(["git", "add", rel_path], cwd=repo.root)
    body = subject
    if trailers:
        body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    _run(["git", "commit", "-q", "-m", body], cwd=repo.root)
    return repo.head()


def _commit_empty(repo, subject, trailers=None):
    body = subject
    if trailers:
        body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    _run(["git", "commit", "-q", "--allow-empty", "-m", body], cwd=repo.root)
    return repo.head()


def _current_plan_approval_covering(repo, *tracked_paths):
    """GPT-R43-002 fixture helper: a minimal `CURRENT` `plan_approval`
    whose `review_content_manifest` names each of `tracked_paths` at its
    *live* git blob hash -- satisfies `_assert_registry_covered_by_
    current_plan_approval` for tests exercising `resolve_own_registry_
    completion_status`/`complete_work_item` that are not themselves about
    plan-approval currency. Each path must already be committed (its live
    blob is read via `git hash-object`, which needs the file to exist)."""
    return {
        "status": "CURRENT",
        "basis": "EXTERNAL_APPROVE",
        "reviewed_bundle_id": "a" * 66,
        "approved_review_content_id": "b" * 66,
        "review_content_manifest": [
            {
                "path": path, "exists": True, "mode": "100644",
                "blob": fingerprint._hash_object(repo.root, path),
            }
            for path in tracked_paths
        ],
        "reviewed_content_commit": None,
        "legacy_evidence": None,
        "user_confirmation": "test fixture approval",
        "waived_guarantees": [],
        "recorded_at": "t0",
    }


class TestRegistryCompletionStatusAndGates(unittest.TestCase):
    REGISTRY = {
        "work_item_id": "wi", "plan_revision": 1,
        "checkpoints": [{"id": "A", "depends_on": []}, {"id": "B", "depends_on": ["A"]}],
    }

    def test_terminal_when_every_checkpoint_complete(self):
        work_item = _base_work_item(checkpoints={
            "A": {"status": "COMPLETE"}, "B": {"status": "COMPLETE"},
        })
        is_terminal, outstanding = ws.registry_completion_status(work_item, self.REGISTRY)
        self.assertTrue(is_terminal)
        self.assertIsNone(outstanding)

    def test_non_terminal_names_next_selectable_checkpoint(self):
        work_item = _base_work_item(checkpoints={"A": {"status": "COMPLETE"}})
        is_terminal, outstanding = ws.registry_completion_status(work_item, self.REGISTRY)
        self.assertFalse(is_terminal)
        self.assertEqual(outstanding, "B")

    def test_non_terminal_names_blocked_checkpoint_via_structured_attribute(self):
        registry = {
            "work_item_id": "wi", "plan_revision": 1,
            "checkpoints": [
                {"id": "A", "depends_on": ["missing"]},
                {"id": "missing", "depends_on": ["A"]},
            ],
        }
        work_item = _base_work_item(checkpoints={})
        is_terminal, outstanding = ws.registry_completion_status(work_item, registry)
        self.assertFalse(is_terminal)
        self.assertEqual(outstanding, "A")  # first incomplete entry in registry order

    def test_milestone_complete_gate_reachable_truth_table(self):
        self.assertTrue(ws.milestone_complete_gate_reachable(phase="AWAITING_FUNCTIONAL_REVIEW", is_terminal=True))
        self.assertTrue(ws.milestone_complete_gate_reachable(phase="AWAITING_USER_ACCEPTANCE", is_terminal=True))
        self.assertFalse(ws.milestone_complete_gate_reachable(phase="AWAITING_FUNCTIONAL_REVIEW", is_terminal=False))
        self.assertFalse(ws.milestone_complete_gate_reachable(phase="IMPLEMENTING", is_terminal=True))

    def test_no_second_gate_function_survives_the_retirement(self):
        """Ledger `I10`: `scoped_remediation_gate_reachable` was the second
        of a mutually exclusive pair, and existed only to admit
        `/accept-scoped-remediation`. Retiring the command retires the
        gate: `milestone_complete_gate_reachable` is now the only
        registry-terminality gate, and a non-terminal registry reaches no
        acceptance gate at all -- exactly the fail-closed shape the
        retirement is meant to preserve."""
        self.assertFalse(hasattr(ws, "scoped_remediation_gate_reachable"))
        for phase in ("AWAITING_FUNCTIONAL_REVIEW", "AWAITING_USER_ACCEPTANCE", "IMPLEMENTING"):
            self.assertFalse(ws.milestone_complete_gate_reachable(phase=phase, is_terminal=False))


class TestCompleteWorkItemOwnRegistryGuard(unittest.TestCase):
    """complete_work_item's authoritative, repo_root-driven own-registry
    resolution (revision 24, GPT-R37-001) -- also exercises "direct
    complete_work_item bypass attempts" and "/accept-milestone refusal with
    incomplete checkpoints" (both route through this same guard) and
    "successful final completion after all checkpoints are complete"."""

    REGISTRY_PATH = "registry.json"

    def _registry(self):
        return {
            "work_item_id": "wi", "plan_revision": 1,
            "checkpoints": [{"id": "A", "depends_on": []}, {"id": "B", "depends_on": ["A"]}],
        }

    def _state(self, *, b_complete, plan_approval=None):
        checkpoints = {"A": {"status": "COMPLETE"}}
        if b_complete:
            checkpoints["B"] = {"status": "COMPLETE"}
        work_item = _base_work_item(
            phase="AWAITING_USER_ACCEPTANCE", registry_path=self.REGISTRY_PATH, checkpoints=checkpoints,
            plan_approval=plan_approval,
        )
        return _base_state(wi=work_item)

    def test_succeeds_when_own_registry_terminal(self):
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, json.dumps(self._registry()))
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(
                b_complete=True, plan_approval=_current_plan_approval_covering(repo, self.REGISTRY_PATH),
            )
            new_state = ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)
            self.assertEqual(new_state["work_items"]["wi"]["phase"], "MILESTONE_COMPLETE")

    def test_raises_incomplete_own_checkpoints_when_own_registry_non_terminal(self):
        """Direct complete_work_item bypass attempt: calling straight
        through, past /accept-milestone's own advisory pre-flight, must
        still fail closed -- this is the authoritative guard, the pre-flight
        is only a clearer early message."""
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, json.dumps(self._registry()))
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(
                b_complete=False, plan_approval=_current_plan_approval_covering(repo, self.REGISTRY_PATH),
            )
            with self.assertRaises(ws.IncompleteOwnCheckpointsError) as ctx:
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)
            self.assertIn("'B'", str(ctx.exception))
            is_terminal, outstanding = ws.resolve_own_registry_completion_status(
                repo.root, state["work_items"]["wi"],
            )
            self.assertFalse(is_terminal)
            self.assertEqual(outstanding, "B")
            self.assertFalse(
                ws.milestone_complete_gate_reachable(phase="AWAITING_USER_ACCEPTANCE", is_terminal=is_terminal)
            )

    def test_refusal_message_names_only_supported_ways_forward(self):
        """Ledger `I10`: this refusal used to read "use
        /accept-scoped-remediation if this is a continued-scope remediation
        round" -- a command whose own gate no supported lifecycle can
        reach, so the operator following that instruction hit a second,
        unrelated refusal. The message now names the three paths that do
        exist: finish the checkpoint, or route a functional-review finding
        through the bounded or the broad branch."""
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, json.dumps(self._registry()))
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(
                b_complete=False, plan_approval=_current_plan_approval_covering(repo, self.REGISTRY_PATH),
            )
            with self.assertRaises(ws.IncompleteOwnCheckpointsError) as ctx:
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)
            message = str(ctx.exception)
            self.assertNotIn("accept-scoped-remediation", message)
            self.assertNotIn("scoped_remediation", message)
            self.assertIn("/milestone-implement", message)
            self.assertIn("/apply-functional-review", message)
            self.assertIn("bounded", message)
            self.assertIn("remediation child work item", message)
            # And it still names the checkpoint that actually blocks.
            self.assertIn("'B'", message)

    def test_registry_coverage_missing_file(self):
        with ScratchRepo() as repo:
            state = self._state(b_complete=True)  # registry.json never written
            with self.assertRaises(ws.RegistryCoverageError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)

    def test_registry_coverage_untracked_file(self):
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, json.dumps(self._registry()))
            # deliberately not `git add`/committed
            state = self._state(b_complete=True)
            with self.assertRaises(ws.RegistryCoverageError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)

    def test_registry_coverage_malformed_json(self):
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, "{not json")
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(b_complete=True)
            with self.assertRaises(ws.RegistryCoverageError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)

    def test_registry_coverage_not_a_json_object(self):
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, json.dumps([1, 2, 3]))
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(b_complete=True)
            with self.assertRaises(ws.RegistryCoverageError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)

    def test_registry_coverage_wrong_work_item_id(self):
        with ScratchRepo() as repo:
            foreign = {**self._registry(), "work_item_id": "someone-else"}
            _write(repo, self.REGISTRY_PATH, json.dumps(foreign))
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(b_complete=True)
            with self.assertRaises(ws.RegistryCoverageError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)

    def test_registry_coverage_cannot_be_satisfied_by_a_fabricated_caller_dict(self):
        """GPT-R37-001: complete_work_item no longer accepts a registry
        argument at all -- there is no way to pass a fabricated
        all-complete registry in, even though the on-disk file itself says
        B is incomplete."""
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, json.dumps(self._registry()))
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(
                b_complete=False, plan_approval=_current_plan_approval_covering(repo, self.REGISTRY_PATH),
            )
            import inspect
            self.assertNotIn("registry", inspect.signature(ws.complete_work_item).parameters)
            with self.assertRaises(ws.IncompleteOwnCheckpointsError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)

    def test_incomplete_children_checked_independently_of_own_checkpoints(self):
        with ScratchRepo() as repo:
            _write(repo, self.REGISTRY_PATH, json.dumps(self._registry()))
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            state = self._state(b_complete=True)
            state["work_items"]["child"] = _base_work_item(
                work_item_id="child", parent_work_item_id="wi", phase="IMPLEMENTING",
            )
            with self.assertRaises(ws.IncompleteChildWorkItemError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)


class TestRegistryReadBoundToCurrentPlanApproval(unittest.TestCase):
    """GPT-R43-002: `resolve_own_registry_completion_status` must prove
    the registry bytes it is about to trust are exactly the ones the
    current `plan_approval` covers -- `RegistryCoverageError` alone (safe
    path, exists, readable, valid JSON, declares the right work_item_id)
    never proved that. The registry's own two checkpoints (A, B) never
    encode terminality themselves -- `registry_completion_status` derives
    that from `work_item["checkpoints"]` against the registry's
    dependency graph (same pattern `TestCompleteWorkItemOwnRegistryGuard`
    uses) -- so `tag` is this fixture's only lever for varying the
    registry's own bytes/blob."""

    REGISTRY_PATH = "registry.json"
    OTHER_PROTECTED_PATH = "plan.md"

    def _registry(self, *, tag="v1"):
        return {
            "work_item_id": "wi", "plan_revision": 1, "tag": tag,
            "checkpoints": [{"id": "A", "depends_on": []}, {"id": "B", "depends_on": ["A"]}],
        }

    def _write_registry(self, repo, *, tag="v1"):
        _write(repo, self.REGISTRY_PATH, json.dumps(self._registry(tag=tag)))

    def _checkpoints(self, *, b_complete):
        checkpoints = {"A": {"status": "COMPLETE"}}
        if b_complete:
            checkpoints["B"] = {"status": "COMPLETE"}
        return checkpoints

    def test_dirty_tracked_registry_after_approval_refuses(self):
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            plan_approval = _current_plan_approval_covering(repo, self.REGISTRY_PATH)
            self._write_registry(repo, tag="tampered")  # dirty, never committed
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=False),
            )
            with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
                ws.resolve_own_registry_completion_status(repo.root, work_item)

    def test_malformed_live_plan_approval_manifest_rejected_cleanly(self):
        """I2's live-state half (`workflow-v2-3-followups` continued
        scope, external cross-model review round 4): the exact
        `/accept-milestone` step 2a entry point,
        `resolve_own_registry_completion_status`, must reject a
        malformed `plan_approval.review_content_manifest` -- the whole
        `workflow_fingerprint.compute_review_content_id_plan_stage*`
        projection object substituted for its own inner manifest list,
        the exact historical shape, not a synthetic stand-in -- with a
        typed `StalePlanApprovalRegistryReadError`, never a bare
        `AttributeError`. Reproduced first (asserting the real crash
        before the fix), then guarded against."""
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            malformed_plan_approval = _current_plan_approval_covering(repo, self.REGISTRY_PATH)
            malformed_plan_approval["review_content_manifest"] = {
                "stage": "plan", "work_item_type": "process", "work_item_id": "wi",
                "plan_revision": 1, "base_commit": repo.base, "reviewed_implementation_head": None,
                "review_content_manifest": [
                    {"path": self.REGISTRY_PATH, "exists": True, "mode": "100644",
                     "blob": fingerprint._hash_object(repo.root, self.REGISTRY_PATH)},
                ],
                "protected_paths": [self.REGISTRY_PATH], "excluded_paths": [], "excluded_prefixes": [],
            }
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=malformed_plan_approval,
                checkpoints=self._checkpoints(b_complete=False),
            )
            with self.assertRaises(ws.StalePlanApprovalRegistryReadError) as ctx:
                ws.resolve_own_registry_completion_status(repo.root, work_item)
            self.assertNotIsInstance(ctx.exception, AttributeError)
            self.assertIn("malformed", str(ctx.exception))

    def test_clean_committed_but_unapproved_mutation_refuses(self):
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            plan_approval = _current_plan_approval_covering(repo, self.REGISTRY_PATH)
            self._write_registry(repo, tag="mutated")  # committed, no new plan-review round
            _commit_paths(repo, [self.REGISTRY_PATH], "registry mutated post-approval")
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=False),
            )
            with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
                ws.resolve_own_registry_completion_status(repo.root, work_item)

    def test_registry_restored_to_approved_bytes_succeeds(self):
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            plan_approval = _current_plan_approval_covering(repo, self.REGISTRY_PATH)
            original_bytes = (repo.root / self.REGISTRY_PATH).read_text()
            self._write_registry(repo, tag="mutated")
            _commit_paths(repo, [self.REGISTRY_PATH], "mutate")
            _write(repo, self.REGISTRY_PATH, original_bytes)
            _commit_paths(repo, [self.REGISTRY_PATH], "restore to approved bytes")
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=False),
            )
            is_terminal, outstanding = ws.resolve_own_registry_completion_status(repo.root, work_item)
            self.assertFalse(is_terminal)
            self.assertEqual(outstanding, "B")

    def test_newly_plan_approved_registry_revision_succeeds(self):
        """A registry mutation covered by its own fresh plan_approval
        (a real new plan-review/approval round, not merely a commit) must
        be trusted, not treated as automatically stale."""
        with ScratchRepo() as repo:
            self._write_registry(repo, tag="revision-2")
            _commit_paths(repo, [self.REGISTRY_PATH], "registry revision 2")
            plan_approval = _current_plan_approval_covering(repo, self.REGISTRY_PATH)
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=True),
            )
            is_terminal, outstanding = ws.resolve_own_registry_completion_status(repo.root, work_item)
            self.assertTrue(is_terminal)
            self.assertIsNone(outstanding)

    def test_terminal_routing_refuses_stale_plan_metadata(self):
        """`complete_work_item` -- the function `/accept-milestone`'s own
        pre-flight and terminal routing both name -- must refuse rather
        than complete when the registry disagrees with `plan_approval`,
        even though `work_item["checkpoints"]` alone would otherwise
        read as terminal."""
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            plan_approval = _current_plan_approval_covering(repo, self.REGISTRY_PATH)
            self._write_registry(repo, tag="tampered")
            _commit_paths(repo, [self.REGISTRY_PATH], "tampered")
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                phase="AWAITING_USER_ACCEPTANCE", checkpoints=self._checkpoints(b_complete=True),
            )
            state = _base_state(wi=work_item)
            with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
                ws.complete_work_item(state, "wi", now="t", repo_root=repo.root)

    def test_non_terminal_routing_refuses_stale_plan_metadata(self):
        """`resolve_own_registry_completion_status` must refuse for the
        non-terminal path too, not only the terminal one; staleness is
        about the bytes, not about which routing outcome the tampered
        registry happens to produce. The non-terminal result is still read
        by `/accept-milestone`'s own advisory pre-flight, which reports the
        outstanding checkpoint to the operator."""
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _commit_paths(repo, [self.REGISTRY_PATH], "registry")
            plan_approval = _current_plan_approval_covering(repo, self.REGISTRY_PATH)
            self._write_registry(repo, tag="tampered-but-still-non-terminal")
            _commit_paths(repo, [self.REGISTRY_PATH], "tampered, committed")
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=False),
            )
            with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
                ws.resolve_own_registry_completion_status(repo.root, work_item)

    def test_dirty_other_protected_path_refuses_even_when_registry_unchanged(self):
        """Items 231/232 (`WF8c`): a *different* plan-stage protected
        document (here standing in for the plan doc, mapping,
        `TECHNICAL_DECISIONS.md`, or the audit doc) going dirty after
        approval must refuse registry-derived completion exactly as a
        dirty registry would -- even though `registry.json` itself is
        byte-identical to what `plan_approval` covers."""
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _write(repo, self.OTHER_PROTECTED_PATH, "plan v1\n")
            _commit_paths(repo, [self.REGISTRY_PATH, self.OTHER_PROTECTED_PATH], "registry + plan")
            plan_approval = _current_plan_approval_covering(
                repo, self.REGISTRY_PATH, self.OTHER_PROTECTED_PATH,
            )
            _write(repo, self.OTHER_PROTECTED_PATH, "plan v1 -- tampered\n")  # dirty, never committed
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=False),
            )
            with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
                ws.resolve_own_registry_completion_status(repo.root, work_item)

    def test_clean_committed_but_unapproved_mutation_of_other_protected_path_refuses(self):
        """Items 231/232: the sibling of
        `test_clean_committed_but_unapproved_mutation_refuses` for a
        *non-registry* protected path -- committed, not merely dirty,
        but never covered by a fresh plan-review/approval round."""
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _write(repo, self.OTHER_PROTECTED_PATH, "plan v1\n")
            _commit_paths(repo, [self.REGISTRY_PATH, self.OTHER_PROTECTED_PATH], "registry + plan")
            plan_approval = _current_plan_approval_covering(
                repo, self.REGISTRY_PATH, self.OTHER_PROTECTED_PATH,
            )
            _write(repo, self.OTHER_PROTECTED_PATH, "plan v1 -- mutated\n")
            _commit_paths(repo, [self.OTHER_PROTECTED_PATH], "plan mutated post-approval")
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=False),
            )
            with self.assertRaises(ws.StalePlanApprovalRegistryReadError):
                ws.resolve_own_registry_completion_status(repo.root, work_item)

    def test_all_protected_paths_matching_approved_bytes_succeeds(self):
        """Positive case: a multi-path manifest where every tracked path,
        not only the registry, still matches its approved snapshot must
        not be refused -- widening the check to every manifest path must
        not produce a false positive on the ordinary, nothing-changed
        case."""
        with ScratchRepo() as repo:
            self._write_registry(repo)
            _write(repo, self.OTHER_PROTECTED_PATH, "plan v1\n")
            _commit_paths(repo, [self.REGISTRY_PATH, self.OTHER_PROTECTED_PATH], "registry + plan")
            plan_approval = _current_plan_approval_covering(
                repo, self.REGISTRY_PATH, self.OTHER_PROTECTED_PATH,
            )
            work_item = _base_work_item(
                registry_path=self.REGISTRY_PATH, plan_approval=plan_approval,
                checkpoints=self._checkpoints(b_complete=True),
            )
            is_terminal, outstanding = ws.resolve_own_registry_completion_status(repo.root, work_item)
            self.assertTrue(is_terminal)
            self.assertIsNone(outstanding)


class TestFunctionalChecklistTrailerDiscovery(unittest.TestCase):
    WI = "swi"
    CHECKLIST_PATH = ws.FUNCTIONAL_CHECKLIST_PATH

    def _seed(self, repo, content="checklist v1\n"):
        _write(repo, self.CHECKLIST_PATH, content)
        return _commit_paths(repo, [self.CHECKLIST_PATH], "seed checklist")

    def _blob(self, repo, ref="HEAD"):
        return subprocess.run(
            ["git", "rev-parse", f"{ref}:{self.CHECKLIST_PATH}"],
            cwd=repo.root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def _evidence_commit(self, repo, *, implementation_revision, blob=None):
        blob = blob or self._blob(repo)
        sha = _commit_empty(repo, "checklist evidence", trailers={
            "Workflow-Functional-Checklist": f"{self.WI}/{implementation_revision}/{blob}",
            "Workflow-Work-Item": self.WI,
        })
        return sha, blob

    def test_no_evidence_returns_none(self):
        with ScratchRepo() as repo:
            self._seed(repo)
            result = ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            self.assertIsNone(result)

    def test_single_evidence_is_discovered(self):
        with ScratchRepo() as repo:
            self._seed(repo)
            sha, blob = self._evidence_commit(repo, implementation_revision=1)
            result = ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            self.assertEqual(result, {"commit_sha": sha, "blob": blob})

    def test_round_scoped_prefix_does_not_cross_implementation_revisions(self):
        with ScratchRepo() as repo:
            self._seed(repo)
            self._evidence_commit(repo, implementation_revision=1)
            result = ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 2)
            self.assertIsNone(result)

    def test_corrected_checklist_produces_new_current_evidence_ahead_of_earlier(self):
        """Unchanged and corrected checklist revisions: an unchanged
        checklist content re-commits nothing (idempotency covered
        separately); a genuinely corrected checklist produces a new,
        distinct, discoverable evidence commit that becomes current, while
        the earlier commit remains separately discoverable by its own
        value (checklist preparation provenance)."""
        with ScratchRepo() as repo:
            self._seed(repo)
            old_sha, old_blob = self._evidence_commit(repo, implementation_revision=1)
            self._seed(repo, content="checklist v2 -- corrected\n")
            new_sha, new_blob = self._evidence_commit(repo, implementation_revision=1)
            self.assertNotEqual(old_blob, new_blob)
            current = ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            self.assertEqual(current, {"commit_sha": new_sha, "blob": new_blob})
            # the earlier evidence is still independently discoverable
            all_commits = ws.discover_functional_checklist_commits(repo.root, self.WI, repo.base, "HEAD")
            self.assertEqual(all_commits[f"{self.WI}/1/{old_blob}"], old_sha)

    def test_interrupted_preparation_is_safe_to_retry(self):
        """An unchanged checklist re-committed twice (simulating a
        preparation retried after interruption) must resolve to the exact
        same current evidence both times -- discovery is idempotent by
        content, never mistaking existing evidence for missing."""
        with ScratchRepo() as repo:
            self._seed(repo)
            sha, blob = self._evidence_commit(repo, implementation_revision=1)
            first = ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            second = ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            self.assertEqual(first, second)
            self.assertEqual(first, {"commit_sha": sha, "blob": blob})

    def test_ambiguous_functional_checklist_trailer_raises(self):
        """Duplicate/ambiguous evidence history: two first-parent-reachable
        commits carrying the exact same trailer value (identical checklist
        content) is genuine ambiguity, never silently resolved (mirrors
        TestCheckpointTrailerDiscovery.test_genuine_ambiguity_raises: two
        commits on the same linear branch are both first-parent ancestors
        of HEAD by construction, so the tie-break cannot narrow to one)."""
        with ScratchRepo() as repo:
            self._seed(repo)
            blob = self._blob(repo)
            self._evidence_commit(repo, implementation_revision=1, blob=blob)
            self._evidence_commit(repo, implementation_revision=1, blob=blob)
            with self.assertRaises(ws.AmbiguousFunctionalChecklistTrailerError):
                ws.discover_functional_checklist_commits(repo.root, self.WI, repo.base, "HEAD")

    def test_evidence_only_on_merged_side_branch_raises(self):
        """Evidence prepared on a side branch, then merged, without any
        matching evidence commit on the resulting branch's first-parent
        chain: the resolver must refuse rather than silently accepting the
        side-branch commit by ordinary reachable-history order
        (`GPT-R41-002`)."""
        with ScratchRepo() as repo:
            self._seed(repo)
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            side_sha, side_blob = self._evidence_commit(repo, implementation_revision=1)
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            _run(["git", "merge", "-q", "--no-ff", "-m", "merge side", "side"], cwd=repo.root)
            with self.assertRaises(ws.NonFirstParentFunctionalChecklistEvidenceError) as ctx:
                ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            self.assertIn(side_sha, str(ctx.exception))

    def test_two_side_branches_with_different_revisions_both_off_first_parent_raises(self):
        """Two side branches, each carrying its own distinct checklist
        revision for the same round, both merged without either becoming a
        first-parent transition: still a refusal, not a pick between the
        two off-first-parent candidates by log order."""
        with ScratchRepo() as repo:
            self._seed(repo)
            _run(["git", "checkout", "-q", "-b", "side-a"], cwd=repo.root)
            side_a_sha, _ = self._evidence_commit(repo, implementation_revision=1)
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            _run(["git", "checkout", "-q", "-b", "side-b"], cwd=repo.root)
            self._seed(repo, content="checklist v2 -- corrected\n")
            side_b_sha, _ = self._evidence_commit(repo, implementation_revision=1)
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            _run(["git", "merge", "-q", "--no-ff", "-m", "merge side-a", "side-a"], cwd=repo.root)
            _run(["git", "merge", "-q", "--no-ff", "-m", "merge side-b", "side-b"], cwd=repo.root)
            with self.assertRaises(ws.NonFirstParentFunctionalChecklistEvidenceError) as ctx:
                ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            self.assertIn(side_a_sha, str(ctx.exception))
            self.assertIn(side_b_sha, str(ctx.exception))

    def test_first_parent_evidence_wins_over_newer_side_branch_evidence(self):
        """A first-parent evidence commit exists for the round; a *newer*
        off-first-parent commit (a later-merged side branch) also carries
        round-scoped evidence. The first-parent commit is still current --
        first-parent standing is never overridden by recency."""
        with ScratchRepo() as repo:
            self._seed(repo)
            main_sha, main_blob = self._evidence_commit(repo, implementation_revision=1)
            _run(["git", "checkout", "-q", "-b", "side"], cwd=repo.root)
            self._seed(repo, content="checklist v2 -- corrected\n")
            self._evidence_commit(repo, implementation_revision=1)
            _run(["git", "checkout", "-q", "-"], cwd=repo.root)
            _run(["git", "merge", "-q", "--no-ff", "-m", "merge side", "side"], cwd=repo.root)
            result = ws.discover_current_functional_checklist_evidence(repo.root, self.WI, repo.base, "HEAD", 1)
            self.assertEqual(result, {"commit_sha": main_sha, "blob": main_blob})





class TestCheckpointClaimRecord(unittest.TestCase):
    def test_claim_checkpoint_publishes_and_resolves(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            self.assertEqual(claim["work_item_id"], "wi")
            self.assertEqual(claim["checkpoint_id"], "CP")
            self.assertEqual(claim["takeover_count"], 0)
            self.assertEqual(ws.resolve_claim(repo.root, "wi"), claim)

    def test_resolve_claim_absent_returns_none(self):
        with ScratchRepo() as repo:
            self.assertIsNone(ws.resolve_claim(repo.root, "wi"))

    def test_claim_checkpoint_same_checkpoint_is_idempotent(self):
        with ScratchRepo() as repo:
            first = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            second = ws.claim_checkpoint(repo.root, "wi", "CP", now="t2")
            self.assertEqual(first["owner_token"], second["owner_token"])

    def test_claim_checkpoint_different_checkpoint_same_worktree_raises_state_mismatch(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP1", now="t1")
            with self.assertRaises(ws.CheckpointOwnershipStateMismatchError):
                ws.claim_checkpoint(repo.root, "wi", "CP2", now="t2")

    def test_claim_across_worktrees_raises_owned_by_other(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            with self.assertRaises(ws.CheckpointOwnedByOtherWorktreeError):
                ws.claim_checkpoint(wt2, "wi", "CP", now="t2")

    def test_symlinked_claim_path_refuses_never_followed(self):
        with ScratchRepo() as repo:
            claims = ws.claims_dir(repo.root)
            claims.mkdir(parents=True, exist_ok=True)
            target = claims / "elsewhere.json"
            target.write_text(json.dumps({"schema_version": ws.CLAIM_SCHEMA_VERSION}))
            ws.claim_path(repo.root, "wi").symlink_to(target)
            with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                ws.resolve_claim(repo.root, "wi")
            with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")

    def test_observe_claim_never_raises_reports_symlink_with_domain_tagged_id(self):
        with ScratchRepo() as repo:
            claims = ws.claims_dir(repo.root)
            claims.mkdir(parents=True, exist_ok=True)
            target = claims / "elsewhere.json"
            target.write_text("irrelevant")
            ws.claim_path(repo.root, "wi").symlink_to(target)
            oid, claim, error, unreadable = ws.observe_claim(repo.root, "wi")
            self.assertIsNone(claim)
            self.assertIsNotNone(error)
            self.assertEqual(unreadable["kind"], "symlink")
            self.assertNotEqual(oid, ws.ABSENT_OBSERVATION)

    def test_release_checkpoint_is_compare_and_delete(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            ws.release_checkpoint(repo.root, "wi", "CP", owner_token=claim["owner_token"], now="t2")
            self.assertIsNone(ws.resolve_claim(repo.root, "wi"))

    def test_release_checkpoint_from_foreign_worktree_refuses(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            with self.assertRaises(ws.CheckpointOwnedByOtherWorktreeError):
                ws.release_checkpoint(wt2, "wi", "CP", owner_token=claim["owner_token"], now="t2")


class TestClaimAdoption(unittest.TestCase):
    """`adopt_claim` -- "Reconciling the two authorities" (revisions
    63-72): the one-time migration path for a checkpoint interrupted
    before this mechanism existed."""

    def test_refuses_when_local_state_has_no_such_checkpoint(self):
        with ScratchRepo() as repo:
            with self.assertRaises(ws.CheckpointNotInProgressLocallyError):
                ws.adopt_claim(repo.root, "wi", "CP", now="t1")

    def test_refuses_when_local_status_is_not_in_progress(self):
        with ScratchRepo() as repo:
            _write_local_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}}))
            with self.assertRaises(ws.CheckpointNotInProgressLocallyError):
                ws.adopt_claim(repo.root, "wi", "CP", now="t1")

    def test_dirty_resume_safety_checked_before_origination(self):
        """No `WORKTREE_IDENTITY.json` at all -- `verify_dirty_resume_
        safety`'s own error, not an origination refusal, proving ordering
        (2) runs before (3)."""
        with ScratchRepo() as repo:
            _write_local_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            with self.assertRaises(ws.WorktreeIdentityMissingError):
                ws.adopt_claim(repo.root, "wi", "CP", now="t1")

    def test_adoption_succeeds_when_origination_reference_is_silent(self):
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _write_local_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            record = ws.adopt_claim(repo.root, "wi", "CP", now="t1")
            self.assertEqual(record["checkpoint_id"], "CP")
            self.assertTrue(record["adopted"])
            self.assertEqual(ws.resolve_claim(repo.root, "wi"), record)

    def test_adoption_is_idempotent(self):
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _write_local_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            first = ws.adopt_claim(repo.root, "wi", "CP", now="t1")
            second = ws.adopt_claim(repo.root, "wi", "CP", now="t2")
            self.assertEqual(first["owner_token"], second["owner_token"])

    def test_refuses_when_origination_reference_observes_in_progress_at_head(self):
        """The committed-history case (3): this worktree's own local
        `IN_PROGRESS` is fully explained by a checkout of committed
        history, not by this worktree's own step 1d -- adoption must not
        treat it as proof of origination."""
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.adopt_claim(repo.root, "wi", "CP", now="t1")
            self.assertEqual(ctx.exception.evidence["route"], "observed")

    def test_refuses_foreign_claim_even_when_origination_admits(self):
        with ScratchRepo() as repo:
            wt2 = repo.worktree("b")
            ws.claim_checkpoint(wt2, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _write_local_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            with self.assertRaises(ws.CheckpointOwnedByOtherWorktreeError):
                ws.adopt_claim(repo.root, "wi", "CP", now="t1")

    def test_refuses_to_repoint_this_worktrees_own_claim_to_a_different_checkpoint(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP1", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _write_local_state(repo, _state_json(wi={"checkpoints": {"CP2": {"status": "IN_PROGRESS"}}}))
            with self.assertRaises(ws.CheckpointOwnershipStateMismatchError):
                ws.adopt_claim(repo.root, "wi", "CP2", now="t1")


class TestResolveCheckpointOwnership(unittest.TestCase):
    """`resolve_checkpoint_ownership` -- `/milestone-implement`'s step 1c
    ("Where the check belongs, and the ordering"), the reconciliation
    table under "Reconciling the two authorities" in full: every row of

        | local WORKFLOW_STATE.json | shared claim | outcome |

    from docs/ai-workflow/WORKFLOW_V2_PLAN.md, asserted against the real
    function rather than the unwired dry-run prototype it is authored
    fresh against."""

    def test_uncontended_no_claim_no_local_in_progress_is_fresh(self):
        with ScratchRepo() as repo:
            wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
            outcome, checkpoint_id, token = ws.resolve_checkpoint_ownership(
                repo.root, wi, "wi", "CP", now="t1")
            self.assertEqual((outcome, checkpoint_id, token), (ws.FRESH, "CP", None))

    def test_uncontended_nothing_selectable_is_no_checkpoint(self):
        """`GPT-R81-003`: an exhausted registry is terminal, never a
        mutation-capable `FRESH` carrying no checkpoint id."""
        with ScratchRepo() as repo:
            wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
            outcome, checkpoint_id, token = ws.resolve_checkpoint_ownership(
                repo.root, wi, "wi", None, now="t1")
            self.assertEqual((outcome, checkpoint_id, token), (ws.NO_CHECKPOINT, None, None))

    def test_uncontended_case_does_not_require_worktree_identity(self):
        """The ordinary uncontended case must stay exactly as permissive
        as it is today -- no `WORKTREE_IDENTITY.json` is ever required
        when neither authority says this work item is live."""
        with ScratchRepo() as repo:
            self.assertFalse((repo.root / ".ai-review").exists())
            wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
            ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "CP", now="t1")
            self.assertFalse((repo.root / ".ai-review" / "runtime" / "WORKTREE_IDENTITY.json").exists())

    def test_contended_via_local_in_progress_with_no_identity_refuses(self):
        """Local `IN_PROGRESS` alone is enough to make this contended, even
        with no claim at all -- `verify_dirty_resume_safety` still runs."""
        with ScratchRepo() as repo:
            wi = _base_work_item(current_checkpoint_id="CP",
                                 checkpoints={"CP": {"status": "IN_PROGRESS"}})
            with self.assertRaises(ws.WorktreeIdentityMissingError) as ctx:
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "CP", now="t1")
            self.assertIsNotNone(ctx.exception.ownership_evidence)
            self.assertIsNone(ctx.exception.ownership_evidence["claim"])
            self.assertIn("takeover", ctx.exception.ownership_evidence["escape"])

    def test_contended_via_foreign_claim_alone_with_no_identity_refuses(self):
        """A published claim alone is enough to make this contended, even
        with nothing locally `IN_PROGRESS`."""
        with ScratchRepo() as repo:
            wt2 = repo.worktree("b")
            ws.claim_checkpoint(wt2, "wi", "CP", now="t0")
            wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
            with self.assertRaises(ws.WorktreeIdentityMissingError):
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "CP", now="t1")

    def test_adoption_then_resume_when_origination_reference_is_silent(self):
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _write_local_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            wi = _base_work_item(current_checkpoint_id="CP",
                                 checkpoints={"CP": {"status": "IN_PROGRESS"}})
            outcome, checkpoint_id, token = ws.resolve_checkpoint_ownership(
                repo.root, wi, "wi", "CP", now="t1")
            self.assertEqual((outcome, checkpoint_id), (ws.RESUME, "CP"))
            self.assertIsNotNone(token)
            claim = ws.resolve_claim(repo.root, "wi")
            self.assertTrue(claim["adopted"])
            self.assertEqual(claim["owner_token"], token)

    def test_adoption_refuses_when_origination_reference_observes_in_progress(self):
        """The committed-history case: this worktree's own local
        `IN_PROGRESS` is fully explained by a checkout of committed
        history, not by this worktree's own step 1d. The refusal carries
        both the origination evidence and the local-identity/claim-state
        components revision 71 (`OPUS-R88-005`) adds."""
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "IN_PROGRESS"}}}))
            wi = _base_work_item(current_checkpoint_id="CP",
                                 checkpoints={"CP": {"status": "IN_PROGRESS"}})
            with self.assertRaises(ws.CheckpointOriginationUnprovableError) as ctx:
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "CP", now="t1")
            evidence = ctx.exception.ownership_evidence
            self.assertEqual(evidence["claim"], None)
            self.assertEqual(evidence["claim_state"], "absent")
            self.assertEqual(evidence["local_identity"]["state"], "valid")
            self.assertEqual(evidence["origination"]["route"], "observed")

    def test_resume_when_claim_self_owned_matches_local_in_progress(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            wi = _base_work_item(current_checkpoint_id="CP",
                                 checkpoints={"CP": {"status": "IN_PROGRESS"}})
            outcome, checkpoint_id, token = ws.resolve_checkpoint_ownership(
                repo.root, wi, "wi", "CP", now="t1")
            self.assertEqual((outcome, checkpoint_id, token), (ws.RESUME, "CP", claim["owner_token"]))

    def test_refuses_when_claim_self_owned_disagrees_with_local_in_progress(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP1", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            wi = _base_work_item(current_checkpoint_id="CP2",
                                 checkpoints={"CP2": {"status": "IN_PROGRESS"}})
            with self.assertRaises(ws.CheckpointOwnershipStateMismatchError) as ctx:
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "CP2", now="t1")
            self.assertEqual(ctx.exception.ownership_evidence["claimed_checkpoint"], "CP1")

    def test_foreign_claim_refuses_even_with_valid_identity_for_this_work_item(self):
        """`S14a`/`S14b` are not the only classes a foreign worktree can
        see: one carrying a valid identity record of its own for this
        work item -- a displaced owner after a takeover, typically --
        passes `verify_dirty_resume_safety` and is refused one line
        later, by the foreign-claim row, with `CheckpointOwnedByOtherWorktreeError`."""
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            wt2 = repo.worktree("b")
            claim = ws.claim_checkpoint(wt2, "wi", "CP", now="t0")
            wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
            with self.assertRaises(ws.CheckpointOwnedByOtherWorktreeError) as ctx:
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "CP", now="t1")
            evidence = ctx.exception.ownership_evidence
            self.assertEqual(evidence["holder"], claim["worktree_root"])
            self.assertIn("resume it there", evidence["escape"])

    def test_continue_claim_when_self_claim_has_no_local_entry_at_all(self):
        """The crash window between publishing the claim and writing the
        state (1d's own ordering keeps it narrow, never closes it)."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
            outcome, checkpoint_id, token = ws.resolve_checkpoint_ownership(
                repo.root, wi, "wi", None, now="t1")
            self.assertEqual((outcome, checkpoint_id, token),
                            (ws.CONTINUE_CLAIM, "CP", claim["owner_token"]))

    def test_continue_claim_refuses_when_selection_disagrees(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            wi = _base_work_item(current_checkpoint_id=None, checkpoints={})
            with self.assertRaises(ws.CheckpointOwnershipStateMismatchError):
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "SOMETHING_ELSE", now="t1")

    def test_durable_completion_releases_and_returns_fresh_with_next_checkpoint(self):
        """The single automatic release in the whole design: a self-owned
        claim on a checkpoint whose completion is durable (committed at
        `HEAD`), i.e. a crash between step 1f's commit and its release."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}}))
            wi = _base_work_item(current_checkpoint_id=None,
                                 checkpoints={"CP": {"status": "COMPLETE"}})
            outcome, checkpoint_id, token = ws.resolve_checkpoint_ownership(
                repo.root, wi, "wi", "NEXT", now="t1")
            self.assertEqual((outcome, checkpoint_id, token), (ws.FRESH, "NEXT", None))
            self.assertIsNone(ws.resolve_claim(repo.root, "wi"))

    def test_durable_completion_with_nothing_left_releases_and_returns_no_checkpoint(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            _commit_state(repo, _state_json(wi={"checkpoints": {"CP": {"status": "COMPLETE"}}}))
            wi = _base_work_item(current_checkpoint_id=None,
                                 checkpoints={"CP": {"status": "COMPLETE"}})
            outcome, checkpoint_id, token = ws.resolve_checkpoint_ownership(
                repo.root, wi, "wi", None, now="t1")
            self.assertEqual((outcome, checkpoint_id, token), (ws.NO_CHECKPOINT, None, None))
            self.assertIsNone(ws.resolve_claim(repo.root, "wi"))

    def test_completion_not_yet_durable_refuses_and_keeps_the_claim(self):
        """A6: the working tree saying `COMPLETE` is not enough -- the
        commit hasn't happened yet, so releasing now would hand the work
        item to another worktree while the completion is still
        uncommitted."""
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            wi = _base_work_item(current_checkpoint_id=None,
                                 checkpoints={"CP": {"status": "COMPLETE"}})
            with self.assertRaises(ws.CheckpointOwnershipStateMismatchError):
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "NEXT", now="t1")
            self.assertIsNotNone(ws.resolve_claim(repo.root, "wi"))

    def test_self_claim_with_unexpected_local_status_refuses_rather_than_guesses(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws.write_worktree_identity(repo.root, "wi", now="t0")
            wi = _base_work_item(current_checkpoint_id=None,
                                 checkpoints={"CP": {"status": "BOGUS"}})
            with self.assertRaises(ws.CheckpointOwnershipStateMismatchError):
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", None, now="t1")

    def test_outcome_is_always_one_of_the_four_named_constants(self):
        self.assertEqual(ws.CHECKPOINT_OWNERSHIP_OUTCOMES,
                         {ws.RESUME, ws.FRESH, ws.CONTINUE_CLAIM, ws.NO_CHECKPOINT})


class TestOwnershipEvidenceAttachment(unittest.TestCase):
    """`_attach_ownership_evidence` -- carries the claim record, the
    holder, the claimed checkpoint, and the escape on *every* refusal,
    including an absent claim (corrected, revision 70, `OPUS-R87-003`;
    the unwired dry-run prototype's own version returns early on
    `claim is None`, which is exactly the defect withdrawn)."""

    def test_attaches_evidence_even_when_claim_is_absent(self):
        with ScratchRepo() as repo:
            exc = ws.CheckpointOwnershipStateMismatchError("boom")
            ws._attach_ownership_evidence(repo.root, exc, "wi", None)
            self.assertIsNotNone(exc.ownership_evidence)
            self.assertIsNone(exc.ownership_evidence["claim"])
            self.assertIsNone(exc.ownership_evidence["holder"])
            self.assertIn("takeover", exc.ownership_evidence["escape"])

    def test_is_idempotent_keeps_first_attachment(self):
        with ScratchRepo() as repo:
            exc = ws.CheckpointOwnershipStateMismatchError("boom")
            ws._attach_ownership_evidence(repo.root, exc, "wi", None)
            first = exc.ownership_evidence
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            ws._attach_ownership_evidence(repo.root, exc, "wi", claim)
            self.assertIs(exc.ownership_evidence, first)
            self.assertIsNone(exc.ownership_evidence["claim"])

    def test_self_owned_claim_names_continuation_not_a_removal(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t0")
            exc = ws.CheckpointOwnershipStateMismatchError("boom")
            ws._attach_ownership_evidence(repo.root, exc, "wi", claim)
            self.assertIn("continue it here", exc.ownership_evidence["escape"])
            self.assertIn("no takeover applies", exc.ownership_evidence["escape"])


class TestCheckpointMutationGuard(unittest.TestCase):
    def test_owner_mutation_happy_path_releases_guard(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            token = claim["owner_token"]
            with ws.owner_mutation(repo.root, "wi", token, checkpoint_id="CP",
                                   step="1d", step_class=ws.ORDINARY, now="t2") as asserted:
                self.assertEqual(asserted["owner_token"], token)
            self.assertIsNone(ws.read_guard(repo.root, "wi"))

    def test_owner_mutation_fences_displaced_owner_after_takeover(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            token = claim["owner_token"]
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            ws.take_over_claim(wt2, "wi", "CP", now="t2", user_authorization=literal, evidence=evidence)
            with self.assertRaises(ws.CheckpointOwnedByOtherWorktreeError):
                ws.assert_claim_owner(repo.root, "wi", token)

    def test_superseded_epoch_guard_reclaimed_with_no_authorization(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            # A stale guard left by a session whose ownership has already
            # rotated away -- the current claim's token is `claim
            # ["owner_token"]`, not this one.
            ws._publish_guard(repo.root, "wi", {
                "lease_id": "stale-lease", "holder_owner_token": "bogus-old-token",
                "holder_worktree_git_dir": "/nowhere", "work_item_id": "wi",
                "checkpoint_id": "CP", "step": "old-step", "step_class": ws.ORDINARY,
                "acquired_at": "t0",
            })
            lease = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                     checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            self.assertNotEqual(lease["lease_id"], "stale-lease")
            ws.release_guard(repo.root, "wi", lease)

    def test_same_worktree_same_token_guard_reclaimed(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            token = claim["owner_token"]
            first = ws.acquire_guard(repo.root, "wi", holder_owner_token=token, checkpoint_id="CP",
                                     step="1d", step_class=ws.DESTRUCTIVE, now="t2")
            # Simulates a crashed/interrupted session in the *same* worktree
            # re-entering the step without having released -- reclaimed with
            # no authorization (rule 2), never a fence between sessions in
            # one worktree.
            second = ws.acquire_guard(repo.root, "wi", holder_owner_token=token, checkpoint_id="CP",
                                      step="1d", step_class=ws.DESTRUCTIVE, now="t3")
            self.assertNotEqual(second["lease_id"], first["lease_id"])
            ws.release_guard(repo.root, "wi", second)

    def test_destructive_guard_blocks_a_concurrent_owner_mutation(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            token = claim["owner_token"]
            ws.acquire_guard(repo.root, "wi", holder_owner_token=token, checkpoint_id="CP",
                             step="1f-commit", step_class=ws.DESTRUCTIVE, now="t2")
            with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                ws.acquire_guard(repo.root, "wi", holder_owner_token="a-different-token",
                                 checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t3")

    def test_unknown_step_class_refuses(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                 checkpoint_id="CP", step="1d", step_class="bogus", now="t2")


class TestGuardFailureSurfaceItem372(unittest.TestCase):
    """Item 372: the mutation guard's own failure surface, asserted since
    the guard is itself a new shared object. Parts (a)-(e) and (g) below;
    (f) (same-worktree concurrency, which needs real, separate OS
    processes) is `TestSameWorktreeConcurrencyRealProcesses`; part (h)
    (the global lock order conformance obligation) is
    `TestGlobalLockOrderItem372h`, below."""

    def test_a_guard_with_no_backing_claim_is_superseded_by_construction(self):
        """Item 372(a): a guard planted by a session holding no current
        token -- one that names `holder_owner_token=None` while no claim
        exists at all -- is superseded by construction and never becomes
        a permanent false lock. Before the fix below, `current is None`
        (no claim) and `held["holder_owner_token"] is None` compared
        equal, so the superseded-epoch branch never fired and no other
        branch reclaimed it either -- reproduced here as the control
        arm's shape, not merely asserted."""
        with ScratchRepo() as repo:
            self.assertIsNone(ws.resolve_claim(repo.root, "wi"))
            ws._publish_guard(repo.root, "wi", {
                "lease_id": "orphan-lease", "holder_owner_token": None,
                "holder_worktree_git_dir": "/nowhere", "work_item_id": "wi",
                "checkpoint_id": "CP", "step": "old-step", "step_class": ws.ORDINARY,
                "acquired_at": "t0",
            })
            lease = ws.acquire_guard(repo.root, "wi", holder_owner_token="new-real-token",
                                     checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            self.assertNotEqual(lease["lease_id"], "orphan-lease")
            ws.release_guard(repo.root, "wi", lease)

    def test_b_undecidable_guard_fails_closed_for_every_session_including_the_owner(self):
        """Item 372(b): a torn/unparseable guard fails closed for every
        session, including one presenting the exact, currently valid
        owner token -- `read_guard` raises before any token comparison
        happens, so there is no privileged reader."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            ws.guard_path(repo.root, "wi").parent.mkdir(parents=True, exist_ok=True)
            ws.guard_path(repo.root, "wi").write_text("{not json")
            with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                ws.read_guard(repo.root, "wi")
            with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                 checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")

    def test_b_undecidable_guard_never_silently_deleted_by_a_release_path(self):
        """Item 372(b): a release path that finds an undecidable guard
        leaves it in place rather than deleting it -- the one place
        fail-closed would quietly become fail-open otherwise. Exercised
        directly against `_release_guard_path`, the primitive
        `release_guard`/`owner_mutation`'s own `finally` calls."""
        with ScratchRepo() as repo:
            ws.guard_path(repo.root, "wi").parent.mkdir(parents=True, exist_ok=True)
            ws.guard_path(repo.root, "wi").write_text("{not json")
            ws._release_guard_path(repo.root, "wi", "whatever-lease-id")
            self.assertTrue(ws.guard_path(repo.root, "wi").exists())
            with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                ws.read_guard(repo.root, "wi")

    def test_b_clearance_refuses_generic_authorization_and_recovers_on_the_exact_one(self):
        """Item 372(b): `clear_malformed_guard` refuses a generic
        authorization and recovers only on the literal bound to the
        guard's own observation id."""
        with ScratchRepo() as repo:
            ws.guard_path(repo.root, "wi").parent.mkdir(parents=True, exist_ok=True)
            ws.guard_path(repo.root, "wi").write_text("{not json")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.clear_malformed_guard(repo.root, "wi", user_authorization="clear it")
            self.assertTrue(ws.guard_path(repo.root, "wi").exists())

            evidence = {"work_item_id": "wi",
                       "guard_observation_id": ws._guard_observation_id(ws.guard_path(repo.root, "wi"))}
            literal = ws.guard_clearance_authorization_literal(evidence)
            ws.clear_malformed_guard(repo.root, "wi", user_authorization=literal)
            self.assertFalse(ws.guard_path(repo.root, "wi").exists())

    def test_b_clearance_refuses_outright_on_a_well_formed_guard(self):
        """Item 372(b): clearance only ever removes an undecidable
        record -- presented against a well-formed guard, even with a
        correctly-computed literal for its (decidable) observation, it
        refuses and removes nothing."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            lease = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                     checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            evidence = {"work_item_id": "wi",
                       "guard_observation_id": ws._guard_observation_id(ws.guard_path(repo.root, "wi"))}
            literal = ws.guard_clearance_authorization_literal(evidence)
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.clear_malformed_guard(repo.root, "wi", user_authorization=literal)
            self.assertEqual(ws.read_guard(repo.root, "wi"), lease)
            ws.release_guard(repo.root, "wi", lease)

    def test_b_clearance_is_compare_and_delete_a_different_undecidable_guard_is_not_removed(self):
        """Item 372(b): clearance's removal is a compare-and-delete on
        the observed bytes, not merely a re-check that the record is
        still undecidable -- an undecidable guard replaced by a
        *different* undecidable guard between the authorization and the
        unlink is not removed, since it is not the record the user
        authorized clearing."""
        with ScratchRepo() as repo:
            path = ws.guard_path(repo.root, "wi")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json, version A")
            evidence = {"work_item_id": "wi", "guard_observation_id": ws._guard_observation_id(path)}
            literal = ws.guard_clearance_authorization_literal(evidence)

            # The guard changes to a *different* undecidable record before
            # the authorized clearance runs.
            path.unlink()
            path.write_text("{not json, version B -- a different undecidable record")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.clear_malformed_guard(repo.root, "wi", user_authorization=literal)
            self.assertEqual(path.read_text(), "{not json, version B -- a different undecidable record")

    def test_c_symlinked_guard_path_fails_closed_on_read_and_publish_target_untouched(self):
        """Item 372(c): a symlinked guard path fails closed on both a
        read and an attempted acquisition (publication), and the
        off-tree symlink target is never opened or written through."""
        with ScratchRepo() as repo:
            claims = ws.claims_dir(repo.root)
            claims.mkdir(parents=True, exist_ok=True)
            off_tree_dir = Path(tempfile.mkdtemp(prefix="wf-guard-off-tree-"))
            off_tree = off_tree_dir / "elsewhere.json"
            off_tree.write_text("not a guard, untouched")
            ws.guard_path(repo.root, "wi").symlink_to(off_tree)
            try:
                with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                    ws.read_guard(repo.root, "wi")
                with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                    ws.acquire_guard(repo.root, "wi", holder_owner_token="tok",
                                     checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t1")
                self.assertEqual(off_tree.read_text(), "not a guard, untouched")
            finally:
                import shutil
                shutil.rmtree(off_tree_dir, ignore_errors=True)

    def test_c_clear_malformed_guard_recovers_a_symlinked_guard_target_untouched(self):
        """Item 372(b)/(c) intersection: `clear_malformed_guard` -- the
        documented recovery for an undecidable guard -- must itself be
        able to observe and clear a *symlinked* guard, not only a torn
        one, since `_read_claim_bytes` raises before ever returning bytes
        for a symlink and a naive caller of it would propagate that raise
        uncontrolled rather than the polished evidence-bound refusal."""
        with ScratchRepo() as repo:
            claims = ws.claims_dir(repo.root)
            claims.mkdir(parents=True, exist_ok=True)
            off_tree_dir = Path(tempfile.mkdtemp(prefix="wf-guard-off-tree-"))
            off_tree = off_tree_dir / "elsewhere.json"
            off_tree.write_text("not a guard, untouched")
            path = ws.guard_path(repo.root, "wi")
            path.symlink_to(off_tree)
            try:
                with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                    ws.clear_malformed_guard(repo.root, "wi", user_authorization="clear it")
                self.assertTrue(path.is_symlink())

                evidence = {"work_item_id": "wi", "guard_observation_id": ws._guard_observation_id(path)}
                literal = ws.guard_clearance_authorization_literal(evidence)
                ws.clear_malformed_guard(repo.root, "wi", user_authorization=literal)
                self.assertFalse(path.exists())
                self.assertFalse(path.is_symlink())
                self.assertEqual(off_tree.read_text(), "not a guard, untouched")
            finally:
                import shutil
                shutil.rmtree(off_tree_dir, ignore_errors=True)

    def test_c_clear_malformed_guard_refuses_a_directory_guard_never_removes_it(self):
        """Item 372(c) hardening: a directory at the guard path is
        undecidable (`read_guard` raises `EISDIR`) but is not a kind this
        design ever removes on its own -- matching `resolve_claim`'s own
        `REPLACEABLE_UNREADABLE_KINDS` philosophy for the claim path --
        so clearance refuses rather than raising `IsADirectoryError` out
        of an `unlink()` it should never have attempted."""
        with ScratchRepo() as repo:
            path = ws.guard_path(repo.root, "wi")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir()
            evidence = {"work_item_id": "wi", "guard_observation_id": ws._guard_observation_id(path)}
            literal = ws.guard_clearance_authorization_literal(evidence)
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.clear_malformed_guard(repo.root, "wi", user_authorization=literal)
            self.assertTrue(path.is_dir())

    def test_d_guard_released_when_the_mutation_raises(self):
        """Item 372(d): the guard is released on every exit path,
        including a mutation that raises -- no residue left behind for
        the next session to trip over."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            with self.assertRaises(RuntimeError):
                with ws.owner_mutation(repo.root, "wi", claim["owner_token"], checkpoint_id="CP",
                                       step="1d", step_class=ws.ORDINARY, now="t2"):
                    raise RuntimeError("boom")
            self.assertIsNone(ws.read_guard(repo.root, "wi"))

    def test_d_release_is_compare_and_delete_a_different_current_guard_is_left_in_place(self):
        """Item 372(d): release removes exactly the guard it compared
        against (by `lease_id`) and no other -- releasing a stale lease
        id the caller no longer actually holds must leave whatever guard
        is currently published untouched, never delete it by pathname
        alone."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            current = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                       checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            ws._release_guard_path(repo.root, "wi", "a-stale-lease-id-not-held")
            self.assertEqual(ws.read_guard(repo.root, "wi"), current)
            ws.release_guard(repo.root, "wi", current)

    def test_d_release_refuses_absent_lease_id_every_flavour(self):
        """Item 372(d), extended (`OPUS-R84`/`OPUS-R85`): an absent
        `lease_id` is a refusal, never a wildcard that removes whatever
        guard is present -- both `None` and the empty string."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            lease = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                     checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            for bogus in (None, ""):
                with self.assertRaises(ws.CheckpointOwnershipUnavailableError):
                    ws._release_guard_path(repo.root, "wi", bogus)
            self.assertEqual(ws.read_guard(repo.root, "wi"), lease)
            ws.release_guard(repo.root, "wi", lease)

    def test_d_publish_exclusivity_holds_independent_of_the_mutation_lock(self):
        """Item 372(d): `os.link`'s `EEXIST` exclusivity is retained
        underneath `guard_mutation_lock` as defense in depth, not
        replaced by it -- even a caller that bypasses the lock and calls
        `_publish_guard` directly cannot silently overwrite an existing
        guard."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            lease = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                     checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            with self.assertRaises(FileExistsError):
                ws._publish_guard(repo.root, "wi", {**lease, "lease_id": "bypassed"})
            self.assertEqual(ws.read_guard(repo.root, "wi"), lease)
            ws.release_guard(repo.root, "wi", lease)

    def test_e_authorized_break_of_ordinary_guard_still_fences_the_broken_session(self):
        """Item 372(e): an authorized break of an "ordinary" guard still
        fences the session it broke -- its own compare-and-delete
        release, presenting the lease it used to hold, cannot remove the
        new owner's guard."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            broken = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                      checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            new_owner = ws.acquire_guard(repo.root, "wi", holder_owner_token="a-different-token",
                                         checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t3",
                                         role="takeover", authorized_lease_id=broken["lease_id"])
            self.assertNotEqual(new_owner["lease_id"], broken["lease_id"])
            ws.release_guard(repo.root, "wi", broken)
            self.assertEqual(ws.read_guard(repo.root, "wi"), new_owner)
            ws.release_guard(repo.root, "wi", new_owner)

    def test_e_takeover_refuses_when_lease_id_changed_since_the_authorization(self):
        """Item 372(e): a guard whose `lease_id` changed since the
        authorization proves the owner live (released and re-acquired,
        or was reclaimed by someone else in the meantime) and refuses
        the now-stale authorization."""
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            first = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                     checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            ws.release_guard(repo.root, "wi", first)
            second = ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                                      checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t3")
            self.assertNotEqual(second["lease_id"], first["lease_id"])
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.acquire_guard(repo.root, "wi", holder_owner_token="another-token",
                                 checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t4",
                                 role="takeover", authorized_lease_id=first["lease_id"])
            self.assertEqual(ws.read_guard(repo.root, "wi"), second)
            ws.release_guard(repo.root, "wi", second)

    def test_f_a_foreign_token_is_refused_by_assert_claim_owner(self):
        """Item 372(f), second clause: a session presenting a token the
        claim does not carry is refused. Acquiring the guard itself never
        checks claim membership -- that is `assert_claim_owner`'s job,
        inside the same window -- so a foreign token is admitted by
        `acquire_guard` and only then refused by `owner_mutation`'s own
        `assert_claim_owner` call; the guard is still released (the
        `finally`) even though the refusal happens inside the window."""
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            with self.assertRaises(ws.CheckpointOwnershipStateMismatchError):
                with ws.owner_mutation(repo.root, "wi", "a-foreign-token", checkpoint_id="CP",
                                       step="1d", step_class=ws.ORDINARY, now="t2"):
                    pass
            self.assertIsNone(ws.read_guard(repo.root, "wi"))

    def test_g_s14_foreign_worktree_refusal_leaves_no_guard_residue(self):
        """Item 372(g): the S14 refusal path (resume attempted from a
        mismatched worktree, `docs/ai-workflow/dry-run/WF8B_SCENARIOS.md`)
        still mutates nothing, creates no guard residue, and leaves the
        claim record untouched now that the mutation guard exists
        alongside the claim -- the S14->S15 path unchanged end to end."""
        with ScratchRepo() as repo:
            wt_holder = repo.worktree("holder")
            ws.claim_checkpoint(wt_holder, "wi", "CP", now="t1")
            before = ws.claim_path(repo.root, "wi").read_bytes()
            wi = _base_work_item(current_checkpoint_id="CP",
                                 checkpoints={"CP": {"status": "IN_PROGRESS"}})
            with self.assertRaises(ws.WorktreeIdentityMissingError):
                ws.resolve_checkpoint_ownership(repo.root, wi, "wi", "CP", now="t2")
            after = ws.claim_path(repo.root, "wi").read_bytes()
            self.assertEqual(before, after)
            self.assertIsNone(ws.read_guard(repo.root, "wi"))


class TestGlobalLockOrderItem372h(unittest.TestCase):
    """Item 372(h): the global lock order is a conformance obligation with
    an owner, not a convention. `WF8c`'s own eventual conformance test the
    plan's "Three arms" text (a completeness arm, a graph arm) explicitly
    deferred past `docs/ai-workflow/dry-run/verify_372h_lock_primitive_
    predicate.py`/`verify_372h_raw_edge_derivation.py`'s own real-repository
    dry-run reproductions (revisions 91-96, five external review rounds).
    Loads those two scripts, unmodified, from their real on-disk location --
    the mechanical AST discovery/resolution/`releasable`-predicate/edge-
    derivation logic they implement is not duplicated here a third time --
    and asserts the same zero-failures property their own `main()` reports
    when run standalone, wired into the standing suite instead of a manual
    `python3 docs/ai-workflow/dry-run/verify_372h_*.py` invocation. Reads
    the live `scripts/workflow_state.py` and `WORKFLOW_V2_PLAN.md`, exactly
    as both scripts always have -- a real-repository check, not a
    `ScratchRepo` one, mirroring the established `Path(ws.__file__).
    read_text()` + `ast` pattern already used elsewhere in this file
    (`test_bundle_generation_target_and_recovery_phase_pair_are_both_
    reachable`)."""

    @staticmethod
    def _load(path, module_name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    @classmethod
    def setUpClass(cls):
        dry_run_dir = Path(__file__).resolve().parent.parent / "docs" / "ai-workflow" / "dry-run"
        # Load order matters: the graph-arm script imports
        # PathResolver/canon/DECLARED_PRIMITIVES from the completeness-arm
        # module by bare name (it inserts its own directory onto sys.path
        # itself); pre-registering that name in sys.modules first means the
        # graph-arm load resolves to this single already-executed instance
        # rather than a second, independent execution.
        cls._completeness = cls._load(
            dry_run_dir / "verify_372h_lock_primitive_predicate.py",
            "verify_372h_lock_primitive_predicate",
        )
        cls._graph = cls._load(
            dry_run_dir / "verify_372h_raw_edge_derivation.py",
            "verify_372h_raw_edge_derivation",
        )

    def test_completeness_arm_discovery_resolution_and_predicate_are_correct(self):
        """The eight-primitive forward-direction comparison (discovery +
        pathname resolution + the mechanically-decided `releasable`
        conjunct), plus both required non-vacuousness regressions
        (`release_checkpoint` made an unconditional release,
        `close_plan_approval_journal` made a genuine compare-and-delete) --
        `verify_372h_lock_primitive_predicate.main()`'s own checks, as
        assertions rather than a `sys.exit` code."""
        m = self._completeness
        src = Path(ws.__file__).read_text()
        tree = m.parse_module(src)
        candidates, failures = m.run_pass(tree, verbose=False)
        failures = list(failures)
        failures += m.compare_to_declared(candidates, verbose=False)
        failures += m.run_regression_checks(src)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_graph_arm_edges_acyclicity_and_single_attempt_discriminator(self):
        """The six code-derivable raw edges rediscovered exactly and
        bidirectionally (shared `os.link` statement attributed
        caller-aware), the four command-orchestrated edges checked against
        the plan's own independently-parsed ten-edge table, the blocking
        sub-order's acyclicity, the call-chain-aware single-attempt
        discriminator, and all required non-vacuousness regressions --
        `verify_372h_raw_edge_derivation.main()`'s own checks, as
        assertions rather than a `sys.exit` code."""
        import ast
        m = self._graph
        src = Path(ws.__file__).read_text()
        tree = m.parse_module(src)
        functions = {fn.name: fn for fn in ast.walk(tree)
                     if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
        discovered, failures = m.run_pass(tree, verbose=False)
        failures = list(failures)
        plan_declared_edges = m.parse_declared_raw_edges_from_plan(m.PLAN_PATH.read_text())
        failures += m.compare_to_declared(discovered, plan_declared_edges, verbose=False)
        failures += m.check_single_attempt_discriminator(tree, functions)
        failures += m.run_regression_checks(src, plan_declared_edges)
        self.assertEqual(failures, [], "\n".join(failures))


_GUARD_WORKER_SOURCE = """
import json
import sys
import time
from pathlib import Path

import workflow_state as ws

mode = sys.argv[1]
repo_root = Path(sys.argv[2])
work_item_id = sys.argv[3]
checkpoint_id = sys.argv[4]
holder_owner_token = sys.argv[5]
step_class = sys.argv[6]
now = sys.argv[7]
out_path = Path(sys.argv[8])

if mode == "hold":
    ready_path = Path(sys.argv[9])
    release_path = Path(sys.argv[10])
    lease = ws.acquire_guard(repo_root, work_item_id, holder_owner_token=holder_owner_token,
                             checkpoint_id=checkpoint_id, step="1f-commit", step_class=step_class,
                             now=now)
    ready_path.write_text(json.dumps(lease))
    deadline = time.monotonic() + 15
    while not release_path.exists():
        if time.monotonic() > deadline:
            out_path.write_text(json.dumps({"outcome": "timeout"}))
            sys.exit(0)
        time.sleep(0.001)
    out_path.write_text(json.dumps({"outcome": "held_to_completion", "lease_id": lease["lease_id"]}))
elif mode == "reclaim":
    try:
        lease = ws.acquire_guard(repo_root, work_item_id, holder_owner_token=holder_owner_token,
                                 checkpoint_id=checkpoint_id, step="1d", step_class=step_class,
                                 now=now)
        ws.release_guard(repo_root, work_item_id, lease)
        out_path.write_text(json.dumps({"outcome": "success", "lease_id": lease["lease_id"]}))
    except ws.CheckpointOwnershipUnavailableError as exc:
        out_path.write_text(json.dumps({"outcome": "refused", "detail": str(exc)}))
"""


class TestSameWorktreeConcurrencyRealProcesses(unittest.TestCase):
    """Item 372(f): same-worktree concurrency is honestly out of scope,
    not falsely fenced, and the assertion is the one the specified
    mechanism can satisfy -- proven with real, separate OS processes,
    since a single process/thread fixture cannot reproduce two
    independent holders (mirroring `TestRealProcessConcurrentTakeover`'s
    own reasoning, applied to the guard rather than the claim). The
    corresponding cross-worktree half is item 373(f), not this item."""

    @classmethod
    def setUpClass(cls):
        cls._scripts_dir = Path(__file__).resolve().parent
        cls._worker_dir = Path(tempfile.mkdtemp(prefix="wf-guard-worker-"))
        cls._worker = cls._worker_dir / "_guard_worker.py"
        cls._worker.write_text(_GUARD_WORKER_SOURCE)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._worker_dir, ignore_errors=True)

    def _spawn(self, *args) -> subprocess.Popen:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self._scripts_dir) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.Popen([sys.executable, str(self._worker), *[str(a) for a in args]], env=env)

    def test_second_process_reclaims_first_process_destructive_guard_windows_open_simultaneously(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            token = claim["owner_token"]
            with tempfile.TemporaryDirectory(prefix="wf-guard-io-") as scratch:
                scratch_path = Path(scratch)
                ready = scratch_path / "ready"
                release = scratch_path / "release"
                out_hold = scratch_path / "out_hold.json"
                out_reclaim = scratch_path / "out_reclaim.json"

                holder = self._spawn("hold", repo.root, "wi", "CP", token, ws.DESTRUCTIVE, "t2",
                                     out_hold, ready, release)
                try:
                    deadline = time.monotonic() + 15
                    while not ready.exists():
                        self.assertLess(time.monotonic(), deadline,
                                        "holder did not become ready in time")
                        time.sleep(0.001)
                    first_lease = json.loads(ready.read_text())
                    self.assertEqual(first_lease["step_class"], ws.DESTRUCTIVE)

                    reclaimer = self._spawn("reclaim", repo.root, "wi", "CP", token, ws.ORDINARY,
                                            "t3", out_reclaim)
                    self.assertEqual(reclaimer.wait(timeout=15), 0)
                    result = json.loads(out_reclaim.read_text())
                    self.assertEqual(result["outcome"], "success", result)
                    self.assertNotEqual(result["lease_id"], first_lease["lease_id"])

                    # Both windows open simultaneously: the reclaim above
                    # returned without waiting for the holder, which is
                    # still parked right now -- the "they serialize"
                    # control arm the item text names must fail, and does.
                    self.assertIsNone(
                        holder.poll(),
                        "the holder process already exited -- the reclaim must not wait for "
                        "it, but something serialized them")
                finally:
                    release.write_text("go")
                    holder.wait(timeout=15)

                held_result = json.loads(out_hold.read_text())
                self.assertEqual(held_result["outcome"], "held_to_completion")


class TestExplicitTakeover(unittest.TestCase):
    def test_takeover_requires_exact_literal(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP", now="t2", user_authorization="not the literal")

    def test_takeover_rotates_token_and_establishes_taker_identity(self):
        with ScratchRepo() as repo:
            original = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            new_claim = ws.take_over_claim(wt2, "wi", "CP", now="t2",
                                           user_authorization=literal, evidence=evidence)
            self.assertNotEqual(new_claim["owner_token"], original["owner_token"])
            self.assertEqual(new_claim["takeover_count"], 1)
            self.assertEqual(new_claim["previous_owner_tokens"], [original["owner_token"]])
            self.assertTrue(ws.claim_is_this_worktree(wt2, new_claim))
            identity = json.loads((wt2 / ws.WORKTREE_IDENTITY_PATH).read_text())
            self.assertIn("wi", identity["expected_dirty_paths_by_work_item"])

    def test_takeover_authorization_binds_to_the_target_checkpoint(self):
        """Item 369(a): the literal names both the observed record and
        the checkpoint the takeover would establish -- an authorization
        naming checkpoint X while the installed claim holds Y is refused,
        even one correctly formed (for a *different* target checkpoint)
        from the exact same evidence. The pre-`GPT-R81-002` (revision 63)
        literal shape, which carried no target-checkpoint binding at all,
        is the control arm: it is refused too, proving the binding is
        load-bearing rather than decorative."""
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP1", now="t1")
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            self.assertEqual(evidence["observed_checkpoint_id"], "CP1")

            # Correctly formed for a different target checkpoint than the
            # one this call actually tries to establish.
            literal_for_other_checkpoint = ws.takeover_authorization_literal("wi", evidence, "CP2")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP1", now="t2",
                                   user_authorization=literal_for_other_checkpoint, evidence=evidence)

            # Control arm: the revision-63 shape named the observation and
            # the displaced checkpoint but never the checkpoint being
            # established.
            legacy_literal = f"take over wi claim {evidence['claim_observation_id']} holding CP1"
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP1", now="t2",
                                   user_authorization=legacy_literal, evidence=evidence)

            # The correctly bound literal succeeds.
            literal = ws.takeover_authorization_literal("wi", evidence, "CP1")
            record = ws.take_over_claim(wt2, "wi", "CP1", now="t2",
                                        user_authorization=literal, evidence=evidence)
            self.assertEqual(record["checkpoint_id"], "CP1")

    def test_takeover_authorization_not_replayable_against_its_own_rotation(self):
        """Item 369(c): the same authorization is not replayable against
        the claim its own rotation produced. Distinct from the
        third-party staleness race in `test_takeover_refuses_on_stale_
        evidence` below -- here the exact evidence/literal pair that just
        won is resubmitted unchanged, as an attacker who captured it
        would resubmit it, and must refuse because the record it names no
        longer exists."""
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            first = ws.take_over_claim(wt2, "wi", "CP", now="t2",
                                       user_authorization=literal, evidence=evidence)
            self.assertEqual(first["takeover_count"], 1)

            wt3 = repo.worktree("c")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt3, "wi", "CP", now="t3",
                                   user_authorization=literal, evidence=evidence)
            # Nothing mutated by the replay: still exactly one takeover.
            final = ws.resolve_claim(repo.root, "wi")
            self.assertEqual(final["takeover_count"], 1)
            self.assertEqual(final["owner_token"], first["owner_token"])

    def test_takeover_of_corrupt_claim_record_requires_binding_to_that_exact_record(self):
        """Item 369(e): the unreadable/corrupt-record recovery path
        carries the same explicit observation binding as an ordinary
        takeover, never a reusable generic authorization. A generic
        literal is refused; an authorization bound to one corrupt record
        does not carry over to a *different* corrupt record; the
        correctly bound literal recovers it and records the recovery."""
        with ScratchRepo() as repo:
            claims = ws.claims_dir(repo.root)
            claims.mkdir(parents=True, exist_ok=True)
            target = claims / "elsewhere.json"
            target.write_text(json.dumps({"schema_version": ws.CLAIM_SCHEMA_VERSION}))
            path = ws.claim_path(repo.root, "wi")
            path.symlink_to(target)

            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            self.assertEqual(evidence["claim_unreadable"]["kind"], "symlink")
            self.assertTrue(evidence["claim_replaceable"])

            # A generic, observation-unbound literal is refused.
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP", now="t2",
                                   user_authorization="take over the corrupt claim", evidence=evidence)

            # A literal correctly bound to a *different* corrupt record
            # (a symlink to different bytes -- a different observation id)
            # does not carry over to this one.
            other_target = claims / "elsewhere2.json"
            other_target.write_text(json.dumps({"schema_version": ws.CLAIM_SCHEMA_VERSION}))
            path.unlink()
            path.symlink_to(other_target)
            wt3 = repo.worktree("c")
            other_evidence = ws.takeover_evidence(wt3, "wi")
            self.assertNotEqual(other_evidence["claim_observation_id"], evidence["claim_observation_id"])
            other_literal = ws.takeover_authorization_literal("wi", other_evidence, "CP")
            path.unlink()
            path.symlink_to(target)  # restore the original corrupt record
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP", now="t2",
                                   user_authorization=other_literal, evidence=evidence)

            # The correctly bound literal recovers it and records the
            # recovery.
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            record = ws.take_over_claim(wt2, "wi", "CP", now="t2",
                                        user_authorization=literal, evidence=evidence)
            self.assertTrue(record["taken_over_from"]["unreadable_record"])
            self.assertEqual(record["taken_over_from"]["claim_observation_id"],
                             evidence["claim_observation_id"])
            self.assertEqual(ws.resolve_claim(repo.root, "wi"), record)

    def test_taken_over_from_describes_the_atomically_displaced_record(self):
        """Item 369(d) (new, revision 64, `GPT-R81-002`): `taken_over_from`
        describes the record atomically displaced -- its own
        `claim_observation_id` equal to the digest of the bytes present at
        rotation (re-read under the guard, `current_oid`), never the
        evidence's own earlier-read `observed_oid` -- together with the
        displaced `owner_token`/`checkpoint_id` and the
        `takeover_count`/`previous_owner_tokens` chain."""
        with ScratchRepo() as repo:
            original = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            observed_oid = evidence["claim_observation_id"]
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            new_claim = ws.take_over_claim(wt2, "wi", "CP", now="t2",
                                           user_authorization=literal, evidence=evidence)
            taken_over_from = new_claim["taken_over_from"]
            self.assertEqual(taken_over_from["owner_token"], original["owner_token"])
            self.assertEqual(taken_over_from["checkpoint_id"], "CP")
            # Re-read at rotation time (current_oid), which for an
            # unmodified claim equals the evidence's own observed_oid --
            # the interesting property (unequal to an *earlier*, now-stale
            # read) is exercised by test_takeover_refuses_on_stale_evidence
            # above, which proves the mismatched case refuses outright
            # rather than silently rotating against stale bytes.
            self.assertEqual(taken_over_from["claim_observation_id"], observed_oid)
            self.assertEqual(new_claim["takeover_count"], 1)
            self.assertEqual(new_claim["previous_owner_tokens"], [original["owner_token"]])

    def test_takeover_of_absent_claim_refuses_once_someone_publishes_one_in_the_meantime(self):
        """Item 369(f): the `"absent"` observation is an observation like
        any other -- authorizing a takeover of "no claim" does not survive
        somebody publishing a real claim in the meantime. Distinct from
        `test_takeover_refuses_on_stale_evidence` above, which exercises a
        claim-to-claim staleness race; here the race is absent-to-present,
        the specific case item 369(f) names."""
        with ScratchRepo() as repo:
            wt2 = repo.worktree("b")
            absent_evidence = ws.takeover_evidence(wt2, "wi")
            self.assertEqual(absent_evidence["claim_observation_id"], ws.ABSENT_OBSERVATION)
            literal = ws.takeover_authorization_literal("wi", absent_evidence, "CP")

            # Someone else genuinely publishes a claim in the meantime.
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t2")

            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP", now="t3",
                                   user_authorization=literal, evidence=absent_evidence)

    def test_takeover_of_absent_claim_uses_absent_observation(self):
        with ScratchRepo() as repo:
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            self.assertEqual(evidence["claim_observation_id"], ws.ABSENT_OBSERVATION)
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            record = ws.take_over_claim(wt2, "wi", "CP", now="t2",
                                        user_authorization=literal, evidence=evidence)
            self.assertEqual(record["takeover_count"], 1)
            self.assertNotIn("taken_over_from", record)

    def test_takeover_refuses_on_destructive_guard(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                             checkpoint_id="CP", step="1f-commit", step_class=ws.DESTRUCTIVE, now="t2")
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP", now="t3", user_authorization=literal, evidence=evidence)

    def test_takeover_refuses_on_stale_evidence(self):
        """Item 369(b): a claim that changes between the evidence
        presentation and the takeover produces a stale-evidence refusal
        with zero mutation -- the surviving claim is byte-compared
        before/after the refused attempt, not merely inferred unchanged
        from the exception alone."""
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            stale_evidence = ws.takeover_evidence(wt2, "wi")
            literal = ws.takeover_authorization_literal("wi", stale_evidence, "CP")
            # The claim changes underneath the evidence the user reviewed --
            # here, a takeover from a third worktree beats them to it.
            wt3 = repo.worktree("c")
            fresh_evidence = ws.takeover_evidence(wt3, "wi")
            ws.take_over_claim(wt3, "wi", "CP", now="t2",
                               user_authorization=ws.takeover_authorization_literal("wi", fresh_evidence, "CP"),
                               evidence=fresh_evidence)
            before = ws.claim_path(repo.root, "wi").read_bytes()
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP", now="t3",
                                   user_authorization=literal, evidence=stale_evidence)
            after = ws.claim_path(repo.root, "wi").read_bytes()
            self.assertEqual(before, after)

    def test_takeover_refuses_when_claim_path_is_a_directory(self):
        with ScratchRepo() as repo:
            claim_path = ws.claim_path(repo.root, "wi")
            claim_path.parent.mkdir(parents=True, exist_ok=True)
            claim_path.mkdir()
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            self.assertFalse(evidence["claim_replaceable"])
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.take_over_claim(wt2, "wi", "CP", now="t2", user_authorization=literal, evidence=evidence)


_TAKEOVER_RACE_WORKER_SOURCE = """
import json
import sys
import time
from pathlib import Path

import workflow_state as ws

repo_root = Path(sys.argv[1])
work_item_id = sys.argv[2]
checkpoint_id = sys.argv[3]
evidence = json.loads(Path(sys.argv[4]).read_text())
user_authorization = sys.argv[5]
now = sys.argv[6]
barrier_path = Path(sys.argv[7])
ready_path = Path(sys.argv[8])
out_path = Path(sys.argv[9])

ready_path.write_text("ready")
deadline = time.monotonic() + 15
while not barrier_path.exists():
    if time.monotonic() > deadline:
        out_path.write_text(json.dumps({"outcome": "timeout"}))
        sys.exit(0)
    time.sleep(0.001)

try:
    record = ws.take_over_claim(repo_root, work_item_id, checkpoint_id, now=now,
                                user_authorization=user_authorization, evidence=evidence)
    out_path.write_text(json.dumps({"outcome": "success", "record": record}))
except ws.CheckpointClaimTakeoverRefusedError as exc:
    out_path.write_text(json.dumps({"outcome": "refused", "detail": str(exc)}))
"""


class TestRealProcessConcurrentTakeover(unittest.TestCase):
    """Item 368(d): two simultaneous authorized takeovers, run as **real,
    separate OS processes** racing to take over the same claim, are
    asserted to produce exactly one winner, a complete parseable
    surviving record, and a loser that refused on evidence or on the
    guard rather than by force -- never both winning, never a corrupted
    record. `acquire_guard`'s `guard_mutation_lock` is a process-scoped
    `fcntl.flock`, and `TestExplicitTakeover`/`TestCheckpointMutationGuard`
    already prove its *logic* in-process; only genuinely separate OS
    processes contending on the same on-disk lock file prove that the
    primitive itself holds under real concurrency, the property a
    single-process or threaded fixture cannot exercise (threads share one
    process's file-descriptor table and one `flock` owner, so they can
    never reproduce two independent holders racing for the same lock)."""

    @classmethod
    def setUpClass(cls):
        cls._scripts_dir = Path(__file__).resolve().parent
        cls._worker_dir = Path(tempfile.mkdtemp(prefix="wf-race-worker-"))
        cls._worker = cls._worker_dir / "_takeover_race_worker.py"
        cls._worker.write_text(_TAKEOVER_RACE_WORKER_SOURCE)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._worker_dir, ignore_errors=True)

    def _spawn(self, repo_root: Path, evidence_path: Path, literal: str,
               barrier: Path, ready: Path, out: Path) -> subprocess.Popen:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self._scripts_dir) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.Popen(
            [sys.executable, str(self._worker), str(repo_root), "wi", "CP",
             str(evidence_path), literal, "t2", str(barrier), str(ready), str(out)],
            env=env,
        )

    def _race_once(self, trial: int) -> None:
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt_b = repo.worktree(f"b{trial}")
            wt_c = repo.worktree(f"c{trial}")
            # Both racers observe the same pre-takeover evidence -- exactly
            # what two users independently reviewing the claim at the same
            # moment, then racing to submit their own authorized takeover,
            # would each see.
            evidence_b = ws.takeover_evidence(wt_b, "wi")
            evidence_c = ws.takeover_evidence(wt_c, "wi")
            literal_b = ws.takeover_authorization_literal("wi", evidence_b, "CP")
            literal_c = ws.takeover_authorization_literal("wi", evidence_c, "CP")

            with tempfile.TemporaryDirectory(prefix="wf-race-io-") as scratch:
                scratch_path = Path(scratch)
                barrier = scratch_path / "go"
                ready_b, ready_c = scratch_path / "ready_b", scratch_path / "ready_c"
                out_b, out_c = scratch_path / "out_b.json", scratch_path / "out_c.json"
                ev_b_path, ev_c_path = scratch_path / "ev_b.json", scratch_path / "ev_c.json"
                ev_b_path.write_text(json.dumps(evidence_b))
                ev_c_path.write_text(json.dumps(evidence_c))

                proc_b = self._spawn(wt_b, ev_b_path, literal_b, barrier, ready_b, out_b)
                proc_c = self._spawn(wt_c, ev_c_path, literal_c, barrier, ready_c, out_c)
                try:
                    deadline = time.monotonic() + 15
                    while not (ready_b.exists() and ready_c.exists()):
                        self.assertLess(time.monotonic(), deadline,
                                        "race workers did not become ready in time")
                        time.sleep(0.001)
                    # Both workers are now parked on the barrier -- release
                    # them together so the race is genuinely concurrent
                    # rather than one process completing before the other
                    # even starts.
                    barrier.write_text("go")
                    self.assertEqual(proc_b.wait(timeout=15), 0)
                    self.assertEqual(proc_c.wait(timeout=15), 0)
                finally:
                    proc_b.kill()
                    proc_c.kill()

                result_b = json.loads(out_b.read_text())
                result_c = json.loads(out_c.read_text())

            outcomes = [result_b["outcome"], result_c["outcome"]]
            self.assertEqual(outcomes.count("success"), 1, (result_b, result_c))
            self.assertEqual(outcomes.count("refused"), 1, (result_b, result_c))
            winner, loser = (
                (result_b, result_c) if result_b["outcome"] == "success" else (result_c, result_b)
            )
            # The loser refused cleanly -- either the guard was already
            # live (the guard-contention path) or its evidence had gone
            # stale by the time it re-verified under the guard (the
            # stale-evidence path) -- never a third, undocumented outcome.
            self.assertTrue(
                "refusing to break it" in loser["detail"]
                or "claim changed" in loser["detail"],
                loser["detail"],
            )
            # The surviving record is complete and parseable, and it is
            # the *only* mutation that landed: takeover_count is 1, never
            # 2, proving the loser mutated nothing.
            final = ws.resolve_claim(repo.root, "wi")
            self.assertIsNotNone(final)
            self.assertEqual(final["takeover_count"], 1)
            self.assertEqual(final["owner_token"], winner["record"]["owner_token"])
            self.assertEqual(winner["record"]["checkpoint_id"], "CP")

    def test_two_simultaneous_authorized_takeovers_produce_exactly_one_winner(self):
        for trial in range(5):
            self._race_once(trial)


class TestAbandonedDestructiveGuardRecovery(unittest.TestCase):
    def test_recovery_refuses_while_holder_still_registered(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                             checkpoint_id="CP", step="1f-commit", step_class=ws.DESTRUCTIVE, now="t2")
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            literal = ws.abandoned_guard_recovery_authorization_literal("wi", evidence)
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.recover_abandoned_destructive_guard(wt2, "wi", "CP", now="t3",
                                                        user_authorization=literal, evidence=evidence)

    def test_recovery_refuses_on_non_destructive_guard(self):
        with ScratchRepo() as repo:
            claim = ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            ws.acquire_guard(repo.root, "wi", holder_owner_token=claim["owner_token"],
                             checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t2")
            wt2 = repo.worktree("b")
            evidence = ws.takeover_evidence(wt2, "wi")
            literal = ws.abandoned_guard_recovery_authorization_literal("wi", evidence)
            with self.assertRaises(ws.CheckpointClaimTakeoverRefusedError):
                ws.recover_abandoned_destructive_guard(wt2, "wi", "CP", now="t3",
                                                        user_authorization=literal, evidence=evidence)

    def test_recovery_succeeds_once_holder_worktree_is_deregistered(self):
        with ScratchRepo() as repo:
            wt_holder = repo.worktree("holder")
            claim = ws.claim_checkpoint(wt_holder, "wi", "CP", now="t1")
            lease = ws.acquire_guard(wt_holder, "wi", holder_owner_token=claim["owner_token"],
                                     checkpoint_id="CP", step="1f-commit", step_class=ws.DESTRUCTIVE, now="t2")
            repo.remove_worktree(wt_holder)
            evidence = ws.takeover_evidence(repo.root, "wi")
            self.assertFalse(evidence["holder_registered"])
            literal = ws.abandoned_guard_recovery_authorization_literal("wi", evidence)
            record = ws.recover_abandoned_destructive_guard(
                repo.root, "wi", "CP", now="t3", user_authorization=literal, evidence=evidence)
            self.assertEqual(record["takeover_count"], 1)
            self.assertEqual(
                record["taken_over_from"]["recovered_from_abandoned_guard"]["lease_id"], lease["lease_id"])
            # the abandoned guard is superseded by construction once the
            # claim rotates -- the recovering worktree can acquire cleanly.
            new_lease = ws.acquire_guard(repo.root, "wi", holder_owner_token=record["owner_token"],
                                         checkpoint_id="CP", step="1d", step_class=ws.ORDINARY, now="t4")
            ws.release_guard(repo.root, "wi", new_lease)


class TestCanonicalStateSerialization(unittest.TestCase):
    """`WFR-63` item 353 (`GPT-R73-002`): `_serialize_state` is the single
    source of truth for `WORKFLOW_STATE.json` bytes, `ensure_ascii=False`,
    shared by production publication and by this suite's own direct-write
    fixture (`_commit_state_only`) so the two can never disagree. Item 353's
    own text names `ensure_ascii=True` and `sort_keys=True` as the two
    "obvious neighbours" that must each independently fail to round-trip --
    exercised directly below, not merely by construction. Corrected this
    round from a prior, confirmed-wrong `ensure_ascii=True` (`OPUS-R101-005`'s
    own docstring called that "not the json.dumps default-by-accident it
    replaces" while setting the exact value that default already is; the
    plan's own `ensure_ascii=False` requirement, stated identically since
    revision 56, was never actually implemented)."""

    def test_round_trip_is_byte_stable_for_non_ascii_content(self):
        state = {
            "schema_version": 1,
            "work_items": {
                "wi": {
                    "note": "WF8b finding disposition (revision 53 → 54) — done",
                    "emoji": "\U0001F600",
                }
            },
        }
        first = ws._serialize_state(state)
        second = ws._serialize_state(json.loads(first.decode("utf-8")))
        self.assertEqual(first, second)

    def test_serialization_preserves_non_ascii_as_literal_utf8_bytes(self):
        """Item 353: canonical bytes contain the literal UTF-8 encoding of
        non-ASCII content, never escaped `\\uXXXX` sequences -- the
        property that makes `ensure_ascii=True` a failing "obvious
        neighbour" rather than an equally valid alternative."""
        state = {"schema_version": 1, "work_items": {"wi": {"note": "→—"}}}
        payload = ws._serialize_state(state)
        self.assertIn("→—".encode("utf-8"), payload)
        self.assertNotIn(b"\\u2192", payload)
        self.assertTrue(payload.endswith(b"\n"))

    def test_ensure_ascii_true_neighbour_does_not_round_trip(self):
        """Item 353: the `ensure_ascii=True` neighbour, applied to the same
        content, produces different bytes than the canonical form -- proof
        the choice is load-bearing, not cosmetic."""
        state = {"schema_version": 1, "work_items": {"wi": {"note": "→—"}}}
        canonical = ws._serialize_state(state)
        wrong_neighbour = (json.dumps(state, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
        self.assertNotEqual(canonical, wrong_neighbour)

    def test_sort_keys_true_neighbour_does_not_round_trip(self):
        """Item 353: the `sort_keys=True` neighbour reorders `work_items`,
        producing different bytes than the canonical insertion-order form."""
        state = {"schema_version": 1, "work_items": {"zeta-wi": {}, "alpha-wi": {}}}
        canonical = ws._serialize_state(state)
        wrong_neighbour = (json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self.assertNotEqual(canonical, wrong_neighbour)

    def test_live_workflow_state_has_no_escaped_unicode_sequences(self):
        """The live repository's own `WORKFLOW_STATE.json` must already be
        free of `ensure_ascii=True`-style escaped sequences -- the
        historical-bug regression guard: had the prior, wrong
        `ensure_ascii=True` ever actually round-tripped non-ASCII content
        through a real write, this would catch it."""
        live_path = Path(__file__).resolve().parent.parent / "docs/ai-workflow/WORKFLOW_STATE.json"
        text = live_path.read_text(encoding="utf-8")
        self.assertNotIn("\\u00", text)
        self.assertNotIn("\\u20", text)

    def test_publish_state_file_uses_the_canonical_serialization(self):
        with ScratchRepo() as repo:
            full_path = repo.root / "docs/ai-workflow/WORKFLOW_STATE.json"
            state = {"schema_version": 1, "work_items": {"wi": {"note": "→"}}}
            ws._publish_state_file(full_path, state)
            self.assertEqual(full_path.read_bytes(), ws._serialize_state(state))

    def test_live_workflow_state_bytes_equal_canonical_serialization_of_its_own_parsed_content(self):
        """The live repository's own `docs/ai-workflow/WORKFLOW_STATE.json`
        must already be in the canonical form -- proves the silent
        re-encoding OPUS-R101-005 found is not merely fixed going forward
        but that the live file matches the now-explicit contract today."""
        live_path = Path(__file__).resolve().parent.parent / "docs/ai-workflow/WORKFLOW_STATE.json"
        raw = live_path.read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
        self.assertEqual(raw, ws._serialize_state(parsed))


class TestIdentityDocumentSerialization(unittest.TestCase):
    """`OPUS-R86-002`'s lost-update fix: `identity_document_lock` plus the
    single-`os.replace` publish in `_publish_worktree_identity`."""

    def test_write_worktree_identity_leaves_no_temp_file(self):
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t1")
            runtime_dir = repo.root / ".ai-review/runtime"
            leftover = [p for p in runtime_dir.iterdir() if p.name.endswith(".tmp") or ".tmp-" in p.name]
            self.assertEqual(leftover, [])

    def test_concurrent_writes_for_different_work_items_do_not_lose_entries(self):
        """Reproduces the plan's own repro in the fixed direction: through
        revision 68 this lost 12/12 threaded trials; serialized under
        `identity_document_lock`, every work item's entry survives."""
        with ScratchRepo() as repo:
            work_items = [f"wi-{i}" for i in range(10)]
            errors: list[Exception] = []

            def _write(work_item_id: str) -> None:
                try:
                    ws.write_worktree_identity(repo.root, work_item_id, now="t")
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=_write, args=(wi,)) for wi in work_items]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            doc = json.loads((repo.root / ws.WORKTREE_IDENTITY_PATH).read_text())
            self.assertEqual(set(doc["expected_dirty_paths_by_work_item"]), set(work_items))

    def test_local_identity_observation_absent(self):
        with ScratchRepo() as repo:
            observed = ws.local_identity_observation(repo.root)
            self.assertEqual(observed["state"], "absent")
            self.assertEqual(observed["identity_observation_id"], ws.ABSENT_OBSERVATION)

    def test_local_identity_observation_valid(self):
        with ScratchRepo() as repo:
            ws.write_worktree_identity(repo.root, "wi", now="t1")
            observed = ws.local_identity_observation(repo.root)
            self.assertEqual(observed["state"], "valid")

    def test_local_identity_observation_undecidable_on_corrupt_document(self):
        with ScratchRepo() as repo:
            full = repo.root / ws.WORKTREE_IDENTITY_PATH
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("{not json")
            observed = ws.local_identity_observation(repo.root)
            self.assertEqual(observed["state"], "undecidable")

    def test_repair_worktree_identity_discards_corrupt_document(self):
        with ScratchRepo() as repo:
            full = repo.root / ws.WORKTREE_IDENTITY_PATH
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("{not json")
            document = ws.repair_worktree_identity(repo.root, "wi", now="t1")
            self.assertIn("wi", document["expected_dirty_paths_by_work_item"])
            ws.verify_dirty_resume_safety(repo.root, "wi")  # must not raise

    def test_takeover_repairs_takers_own_corrupt_identity_under_explicit_authorization(self):
        with ScratchRepo() as repo:
            ws.claim_checkpoint(repo.root, "wi", "CP", now="t1")
            wt2 = repo.worktree("b")
            full = wt2 / ws.WORKTREE_IDENTITY_PATH
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("{not json")
            evidence = ws.takeover_evidence(wt2, "wi")
            self.assertEqual(evidence["local_identity"]["state"], "undecidable")
            literal = ws.takeover_authorization_literal("wi", evidence, "CP")
            self.assertIn("repairing identity", literal)
            ws.take_over_claim(wt2, "wi", "CP", now="t2", user_authorization=literal, evidence=evidence)
            document = json.loads(full.read_text())
            self.assertIn("wi", document["expected_dirty_paths_by_work_item"])


class TestReconciliationTableParsing(unittest.TestCase):
    """WF8c (m), part 1: `parse_reconciliation_table` re-derives the plan
    document's '### Reconciliation table' into `{item: (status, owner)}`
    -- WFR-68 property (i)'s own item-set source of truth, read from a
    pinned Git commit rather than the working tree or the artifact's own
    enumeration."""

    def _commit_plan(self, repo, table_body: str, *, heading: bool = True, header_row: bool = True) -> str:
        parts = ["# Plan\n\n"]
        if heading:
            parts.append(ws.RECONCILIATION_TABLE_HEADING + "\n\n")
        if header_row:
            parts.append(ws.RECONCILIATION_TABLE_HEADER_ROW + "\n")
            parts.append("|---|---|---|---|---|\n")
        parts.append(table_body)
        full = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("".join(parts))
        _run(["git", "add", "docs/ai-workflow/WORKFLOW_V2_PLAN.md"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "plan"], cwd=repo.root)
        return repo.head()

    # -- _split_markdown_table_row --

    def test_split_row_treats_pipe_inside_backticks_as_literal(self):
        cells = ws._split_markdown_table_row(
            "| 376 | `review-subject: bundle\\|verdict\\|none` | `ABSENT` | `WF8c` |"
        )
        self.assertEqual(cells, ["376", "`review-subject: bundle\\|verdict\\|none`", "`ABSENT`", "`WF8c`"])

    def test_split_row_ordinary_row(self):
        cells = ws._split_markdown_table_row("| 1-5 | topic | `IMPLEMENTED` | `WF8b` | evidence |")
        self.assertEqual(cells, ["1-5", "topic", "`IMPLEMENTED`", "`WF8b`", "evidence"])

    # -- _expand_reconciliation_items_cell --

    def test_expand_items_single(self):
        self.assertEqual(ws._expand_reconciliation_items_cell("272"), [272])

    def test_expand_items_comma_list(self):
        self.assertEqual(ws._expand_reconciliation_items_cell("231, 232"), [231, 232])

    def test_expand_items_range(self):
        self.assertEqual(ws._expand_reconciliation_items_cell("167-170"), [167, 168, 169, 170])

    def test_expand_items_mixed_ranges_and_singles(self):
        self.assertEqual(
            ws._expand_reconciliation_items_cell("229-230, 233-235"), [229, 230, 233, 234, 235],
        )

    def test_expand_items_strips_trailing_parenthetical(self):
        self.assertEqual(
            ws._expand_reconciliation_items_cell("166 (pre-167, outside the umbrella range)"), [166],
        )

    # -- _resolve_reconciliation_status --

    def test_resolve_status_plain(self):
        self.assertEqual(ws._resolve_reconciliation_status("`IMPLEMENTED`"), "IMPLEMENTED")

    def test_resolve_status_bold_with_trailing_prose(self):
        self.assertEqual(
            ws._resolve_reconciliation_status("**`SUPERSEDED` — not owed to any checkpoint**"), "SUPERSEDED",
        )

    def test_resolve_status_with_qualifier(self):
        self.assertEqual(ws._resolve_reconciliation_status("`PARTIAL` (368(d) only)"), "PARTIAL")

    def test_resolve_status_unrecognized_raises(self):
        with self.assertRaises(ws.ReconciliationTableParseError):
            ws._resolve_reconciliation_status("`MAYBE`")

    # -- _resolve_reconciliation_owner --

    def test_resolve_owner_superseded_is_none(self):
        self.assertEqual(
            ws._resolve_reconciliation_owner("SUPERSEDED", "none (`SUPERSEDED`, see rationale)"), "none",
        )

    def test_resolve_owner_partial_compound_cell_takes_remaining_work_owner(self):
        self.assertEqual(
            ws._resolve_reconciliation_owner(
                "PARTIAL", "`WF8b` for the delivered core; `WF8c` for the dependent sub-cases",
            ),
            "WF8c",
        )

    def test_resolve_owner_partial_single_cell(self):
        self.assertEqual(ws._resolve_reconciliation_owner("PARTIAL", "`WF8c` (267 only)"), "WF8c")

    def test_resolve_owner_partial_without_wf8c_raises(self):
        with self.assertRaises(ws.ReconciliationTableParseError):
            ws._resolve_reconciliation_owner("PARTIAL", "`WF8b` only")

    def test_resolve_owner_bare_wf8b(self):
        self.assertEqual(ws._resolve_reconciliation_owner("IMPLEMENTED", "`WF8b`"), "WF8b")

    def test_resolve_owner_picks_leftmost_occurring_checkpoint_not_a_fixed_preference(self):
        # Regression: item 376's real row names WF8c first and WF8b only
        # in later, explanatory prose ("reassigned from WF8b") -- a fixed
        # WF8b-before-WF8c preference order would misresolve this to WF8b.
        self.assertEqual(
            ws._resolve_reconciliation_owner("ABSENT", "`WF8c` (`WFR-67`, reassigned from `WF8b`)"), "WF8c",
        )

    def test_resolve_owner_unresolvable_raises(self):
        with self.assertRaises(ws.ReconciliationTableParseError):
            ws._resolve_reconciliation_owner("ABSENT", "somebody, presumably")

    # -- parse_reconciliation_table end to end --

    def test_parse_end_to_end_resolves_all_four_statuses(self):
        with ScratchRepo() as repo:
            table = (
                "| 1-2 | topic a | `IMPLEMENTED` | `WF8b` | evidence a |\n"
                "| 3 | topic b | `ABSENT` | `WF8c` | evidence b |\n"
                "| 4-5 | topic c | `PARTIAL` | `WF8b` for the delivered core; `WF8c` for the rest | evidence c |\n"
                "| 6 | topic d | **`SUPERSEDED`** — retired | none (see rationale) | evidence d |\n"
            )
            commit = self._commit_plan(repo, table)
            result = ws.parse_reconciliation_table(repo.root, commit)
            self.assertEqual(
                result,
                {
                    1: {"status": "IMPLEMENTED", "owner": "WF8b", "topic": "topic a"},
                    2: {"status": "IMPLEMENTED", "owner": "WF8b", "topic": "topic a"},
                    3: {"status": "ABSENT", "owner": "WF8c", "topic": "topic b"},
                    4: {"status": "PARTIAL", "owner": "WF8c", "topic": "topic c"},
                    5: {"status": "PARTIAL", "owner": "WF8c", "topic": "topic c"},
                    6: {"status": "SUPERSEDED", "owner": "none", "topic": "topic d"},
                },
            )

    def test_parse_missing_heading_raises(self):
        with ScratchRepo() as repo:
            commit = self._commit_plan(repo, "| 1 | t | `IMPLEMENTED` | `WF8b` | e |\n", heading=False)
            with self.assertRaises(ws.ReconciliationTableParseError):
                ws.parse_reconciliation_table(repo.root, commit)

    def test_parse_missing_header_row_raises(self):
        with ScratchRepo() as repo:
            commit = self._commit_plan(repo, "| 1 | t | `IMPLEMENTED` | `WF8b` | e |\n", header_row=False)
            with self.assertRaises(ws.ReconciliationTableParseError):
                ws.parse_reconciliation_table(repo.root, commit)

    def test_parse_duplicate_item_across_rows_raises(self):
        with ScratchRepo() as repo:
            table = (
                "| 1-3 | topic a | `IMPLEMENTED` | `WF8b` | evidence a |\n"
                "| 3-5 | topic b | `ABSENT` | `WF8c` | evidence b |\n"
            )
            commit = self._commit_plan(repo, table)
            with self.assertRaises(ws.ReconciliationTableParseError):
                ws.parse_reconciliation_table(repo.root, commit)

    def test_parse_stops_at_first_non_table_line(self):
        with ScratchRepo() as repo:
            table = (
                "| 1 | topic a | `IMPLEMENTED` | `WF8b` | evidence a |\n"
                "\n"
                "Some prose after the table, not itself a row.\n"
            )
            commit = self._commit_plan(repo, table)
            result = ws.parse_reconciliation_table(repo.root, commit)
            self.assertEqual(set(result.keys()), {1})

    def test_parse_reads_pinned_commit_not_working_tree(self):
        with ScratchRepo() as repo:
            commit = self._commit_plan(repo, "| 1 | t | `IMPLEMENTED` | `WF8b` | e |\n")
            # Overwrite the working tree after the commit -- the parser
            # must read the pinned commit's content, never the live file.
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text("garbage, no table at all")
            result = ws.parse_reconciliation_table(repo.root, commit)
            self.assertEqual(set(result.keys()), {1})


class TestLedgerStatusPathForWorkItem(unittest.TestCase):
    def test_path_shape(self):
        self.assertEqual(
            ws.ledger_status_path_for_work_item("workflow-v2-1-core"),
            Path("docs/ai-workflow/registry/workflow-v2-1-core-ledger-status.json"),
        )

    def test_rejects_invalid_work_item_id(self):
        with self.assertRaises(fingerprint.InvalidWorkItemIdError):
            ws.ledger_status_path_for_work_item("Not Valid!")


class TestCheckpointReachabilityConformance(unittest.TestCase):
    """WF8c (m), part 2: `verify_checkpoint_reachability_conformance` --
    item 355's own conformance test, re-derived at a pinned commit against
    the reconciliation table, the live registry/state, the 'Missing
    tests' list, and `WFR-63`'s `checkpoint_ids` (the plan's own worked
    example for clause (d), unchanged since revision 59)."""

    def _commit_fixture(
        self, repo, *,
        table_rows: str,
        missing_test_max: int = 6,
        registry_checkpoints: list[dict],
        state_checkpoints: dict,
        current_checkpoint_id: str | None = None,
        wfr63_checkpoint_ids: list[str] | None = ("WF8b", "WF8c"),
        include_mapping: bool = True,
        ledger_entries: list[dict] | None = None,
        wf8c_evidence_entries: list[dict] | None = None,
    ) -> str:
        plan_lines = [
            "# Plan\n\n",
            ws.RECONCILIATION_TABLE_HEADING + "\n\n",
            ws.RECONCILIATION_TABLE_HEADER_ROW + "\n",
            "|---|---|---|---|---|\n",
            table_rows,
            "\n" + ws.CHECKPOINT_REACHABILITY_MISSING_TESTS_HEADING + " (fixture)\n\n",
        ]
        for n in range(1, missing_test_max + 1):
            plan_lines.append(f"{n}. fixture missing-test item {n}\n")
        plan_lines.append("\n## Next section\n\nnothing here\n")
        plan_dir = repo.root / "docs" / "ai-workflow"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "WORKFLOW_V2_PLAN.md").write_text("".join(plan_lines))

        registry = {
            "schema_version": 1, "work_item_id": "wi", "plan_revision": 1,
            "checkpoints": registry_checkpoints,
        }
        (repo.root / "registry.json").write_text(json.dumps(registry))

        work_item = {
            "registry_path": "registry.json",
            "checkpoints": state_checkpoints,
            "current_checkpoint_id": current_checkpoint_id,
        }
        if include_mapping:
            work_item["mapping_path"] = "mapping.json"
            requirements = {}
            if wfr63_checkpoint_ids is not None:
                requirements["WFR-63"] = {
                    "description": "fixture", "checkpoint_ids": list(wfr63_checkpoint_ids),
                }
            mapping = {"schema_version": 1, "work_item_id": "wi", "requirements": requirements}
            (repo.root / "mapping.json").write_text(json.dumps(mapping))
        state = {"work_items": {"wi": work_item}}
        (plan_dir / "WORKFLOW_STATE.json").write_text(json.dumps(state))

        if ledger_entries is not None:
            ledger_path = repo.root / ws.ledger_status_path_for_work_item("wi")
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(json.dumps({"entries": ledger_entries}))
        if wf8c_evidence_entries is not None:
            evidence_path = repo.root / ws.wf8c_evidence_path_for_work_item("wi")
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps({"entries": wf8c_evidence_entries}))

        _run(["git", "add", "-A"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "fixture"], cwd=repo.root)
        return repo.head()

    _LINEAR_TWO_CHECKPOINT_REGISTRY = [
        {"id": "WF8b", "depends_on": []},
        {"id": "WF8c", "depends_on": ["WF8b"]},
    ]
    _CLEAN_TABLE_ROWS = (
        "| 1-3 | topic a | `IMPLEMENTED` | `WF8b` | evidence a |\n"
        "| 4-6 | topic b | `ABSENT` | `WF8c` | evidence b |\n"
    )

    def test_passes_on_a_healthy_fixture(self):
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "PASS", result["detail"])
            self.assertEqual(result["failing_assertions"], [])

    def test_flags_item_naming_an_already_complete_checkpoint(self):
        # WF8c itself has since completed but the table still assigns
        # items 4-6 to it -- clauses (a) and (b1) both fire.
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}, "WF8c": {"status": "COMPLETE"}},
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("clause (a)" in a for a in result["failing_assertions"]))
            self.assertTrue(any("clause (b1)" in a for a in result["failing_assertions"]))

    def test_discharged_items_pass_once_the_owner_completes(self):
        """OPUS-R129-003: the counterpart to the undischarged case above,
        pinning clause (a)'s `current_owner is None` (post-terminal)
        branch explicitly rather than exercising it only by accident.
        Same terminal fixture (both registry checkpoints `COMPLETE`,
        items 4-6 still `ABSENT`/`WF8c`), but items 4-6 now carry a
        recorded evidence reference in the companion
        `wi-wf8c-evidence.json` -- `WF8c`'s own resolution rule (revision
        88, `OPUS-R110-001`) closes such an item by evidence, never by
        flipping the table's frozen status to `IMPLEMENTED`. Discharged
        items must not count as open work once their owner completes."""
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}, "WF8c": {"status": "COMPLETE"}},
                wf8c_evidence_entries=[
                    {"item": 4, "evidence": "fixture.Case.test_ok"},
                    {"item": 5, "evidence": "fixture.Case.test_ok"},
                    {"item": 6, "evidence": "fixture.Case.test_ok"},
                ],
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "PASS", result["detail"])
            self.assertEqual(result["failing_assertions"], [])

    def test_partially_discharged_items_still_flag_the_undischarged_remainder(self):
        """A mix within the same owner: item 4 discharged (evidence
        recorded, via the ledger's own `evidence` field this time -- the
        `ledger-wins` half of the merge, not only the companion-file-fills-
        gaps half the test above exercises), items 5-6 not. Only 5 and 6
        may still be flagged -- discharge is per-item, not a blanket
        exemption once any evidence exists for the owning checkpoint."""
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}, "WF8c": {"status": "COMPLETE"}},
                ledger_entries=[
                    {"item": 4, "status": "ABSENT", "owner_checkpoint": "WF8c",
                     "evidence": "fixture.Case.test_ok"},
                ],
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "FAIL")
            for assertion in result["failing_assertions"]:
                # OPUS-R130-M03: the item list lives after the clause
                # label's own colon (e.g. "clause (b1): items [5, 6] ...");
                # checking only `split(":")[0]` inspected the label text
                # and could never fail regardless of which items were
                # actually reported, so it never proved item 4 absent.
                self.assertNotIn("4", assertion.split(":", 1)[1])
            self.assertTrue(any("5" in a and "6" in a for a in result["failing_assertions"]))

    def test_flags_owner_absent_from_the_registry(self):
        # The registry never grew a WF8c entry at all, but the table
        # still names it -- clause (b2)'s "not a real registry entry" arm.
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=[{"id": "WF8b", "depends_on": []}],
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
                include_mapping=False,
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("clause (b2)" in a for a in result["failing_assertions"]))

    def test_flags_table_upper_bound_ahead_of_missing_tests_list(self):
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,  # highest item 6
                missing_test_max=5,  # list stops one short
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(len(result["failing_assertions"]), 1)
            self.assertIn("clause (c)", result["failing_assertions"][0])

    def test_flags_missing_tests_list_ahead_of_table_upper_bound(self):
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,  # highest item 6
                missing_test_max=7,  # list runs one past the table
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(len(result["failing_assertions"]), 1)
            self.assertIn("clause (c)", result["failing_assertions"][0])

    def test_flags_wfr63_checkpoint_ids_missing_the_live_owner(self):
        # The plan's own clause (d) worked example: WFR-63's checkpoint_ids
        # names only its historical, already-COMPLETE checkpoint, so its
        # still-open half would be silently read as inherited.
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
                wfr63_checkpoint_ids=["WF8b"],
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(len(result["failing_assertions"]), 1)
            self.assertIn("clause (d)", result["failing_assertions"][0])

    def test_passes_when_mapping_path_is_absent(self):
        # Clause (d) is a WFR-63-specific worked example, not a universal
        # requirement -- a work item carrying no mapping_path at all (or
        # no WFR-63 row) must not fail on that account alone.
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
                include_mapping=False,
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "PASS", result["detail"])

    def test_fails_closed_on_unknown_work_item(self):
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
            )
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "does-not-exist")
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("does-not-exist", result["detail"])

    def test_reads_pinned_commit_not_working_tree(self):
        with ScratchRepo() as repo:
            commit = self._commit_fixture(
                repo,
                table_rows=self._CLEAN_TABLE_ROWS,
                missing_test_max=6,
                registry_checkpoints=self._LINEAR_TWO_CHECKPOINT_REGISTRY,
                state_checkpoints={"WF8b": {"status": "COMPLETE"}},
            )
            # Corrupt every working-tree copy after the commit -- the
            # function must read the pinned commit's blobs, never these.
            (repo.root / "registry.json").write_text("garbage")
            (repo.root / "mapping.json").write_text("garbage")
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text("garbage")
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text("garbage")
            result = ws.verify_checkpoint_reachability_conformance(repo.root, commit, "wi")
            self.assertEqual(result["status"], "PASS", result["detail"])


# ---------------------------------------------------------------------------
# Persisted-phase writer census, derived mechanically from this module's own
# source (Workflow v2.x convergence campaign, ledger rows `B10`/`B11`).
# ---------------------------------------------------------------------------


def _persisted_phase_writers() -> dict[str, set[str]]:
    """Every `KNOWN_PHASES` value this module actually persists, mapped to
    the function(s) that write it -- derived by walking
    `workflow_state.py`'s own AST, never from a hand-maintained list, so a
    new writer (or a removed one) moves this census by construction.

    Recognizes the three shapes the module uses: a direct
    `<subject>["phase"] = "<CONST>"` assignment, a `"phase": "<CONST>"`
    entry in a dict literal (`default_work_item`'s fresh-item shape), and
    a call to a module-level *resolver function* whose own body returns
    only string constants (`record_bundle_generation`'s
    `bundle_generation_target_phase(stage, governing_workflow_version)`,
    workflow-2.5.0 CP3) -- every such literal `return "<CONST>"` in the
    resolver's own body is counted as one of its possible outputs,
    resolved through the call exactly like a constant bound to a local
    name first (`publish_plan_revision`'s `target_phase`) is resolved
    through that binding -- so a version-keyed resolver's targets are
    counted rather than lost as "dynamic", whether it branches via
    if/elif-bound locals (`publish_plan_revision`) or via a separate,
    reusable resolver function called from the assignment site
    (`record_bundle_generation`)."""
    tree = ast.parse(Path(ws.__file__).read_text())
    writers: dict[str, set[str]] = {}

    def record(phase: str, fn: str) -> None:
        writers.setdefault(phase, set()).add(fn)

    def const_str(node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    # Module-level resolver functions: name -> every string constant any
    # `return` statement in its own body yields, directly or (recursively)
    # through an `if`/`elif`/`else` chain -- never following a call to
    # *another* function, only literal returns of its own.
    resolver_returns: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        literals: set[str] = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and inner.value is not None:
                literal = const_str(inner.value)
                if literal is not None:
                    literals.add(literal)
        if literals:
            resolver_returns[node.name] = literals

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_consts: dict[str, set[str]] = {}
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    value = const_str(node.value)
                    if value is not None:
                        local_consts.setdefault(target.id, set()).add(value)
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not (isinstance(target, ast.Subscript)
                            and const_str(target.slice) == "phase"):
                        continue
                    value = const_str(node.value)
                    if value is not None:
                        record(value, fn.name)
                    elif isinstance(node.value, ast.Name):
                        for candidate in local_consts.get(node.value.id, ()):
                            record(candidate, fn.name)
                    elif (
                        isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id in resolver_returns
                    ):
                        for candidate in resolver_returns[node.value.func.id]:
                            record(candidate, fn.name)
                    else:  # pragma: no cover -- guarded by the test below
                        record(f"<unresolved:{ast.unparse(node.value)}>", fn.name)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if const_str(key) == "phase":
                        literal = const_str(value)
                        if literal is not None:
                            record(literal, fn.name)
    return writers


class TestPersistedPhaseWriterCensus(unittest.TestCase):
    """`KNOWN_PHASES` is an allowlist, not a transition graph
    (`workflow_state.py`'s own words), so "declared" and "persisted" are
    different sets and only the second one is a live gate. Both the
    lifecycle diagram and `WORKFLOW_V2_1_OPERATOR_REFERENCE.md` have to
    represent that distinction, and both got it wrong before this
    campaign: `AWAITING_TECHNICAL_APPROVAL` was drawn and documented as a
    live persisted gate `/apply-implementation-review` transitions into,
    and `FIXING_FUNCTIONAL_FINDINGS` as the state
    `/apply-functional-review` enters. Neither is ever written.

    This census is the mechanical ground truth both of those documents
    are now checked against. It fails if a phase gains or loses a writer,
    which is exactly when those documents need re-checking."""

    #: The phases `KNOWN_PHASES` declares that nothing persists.
    #: Narrative/compatibility vocabulary only -- three of the original
    #: four are named by `MILESTONE_WORKFLOW.md`'s v1 state list, and
    #: `AWAITING_TECHNICAL_APPROVAL`/`AWAITING_PLAN_APPROVAL` have
    #: *computed reachability* predicates
    #: (`technical_approval_gate_reachable`/`plan_approval_gate_reachable`)
    #: instead; `AWAITING_PLAN_APPROVAL` happens to also be persisted (by
    #: `record_manual_plan_review`) and `AWAITING_TECHNICAL_APPROVAL` is
    #: not, which is precisely the asymmetry the documents flattened.
    #:
    #: workflow-2.5.0 CP2 added `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`/
    #: `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` to `KNOWN_PHASES`
    #: (D-Implementation-Review-Stages) ahead of their own writers. CP3
    #: is where `bundle_generation_target_phase`'s version-dependent
    #: resolver (called from `record_bundle_generation`) and
    #: `record_local_implementation_review`/`record_manual_implementation_review`
    #: actually persist them, so this census shrinks by two here -- both
    #: moved to `EXPECTED_WRITERS` below.
    DECLARED_BUT_UNWRITTEN = frozenset({
        "SELF_REVIEWING_PLAN",
        "AWAITING_TECHNICAL_APPROVAL",
        "FIXING_FUNCTIONAL_FINDINGS",
        "AWAITING_USER_ACCEPTANCE",
    })

    EXPECTED_WRITERS = {
        "PLANNING": {"default_work_item"},
        "AWAITING_EXTERNAL_PLAN_REVIEW": {"publish_plan_revision"},
        "AWAITING_LOCAL_PLAN_REVIEW": {
            "publish_plan_revision", "transition_to_awaiting_local_plan_review",
        },
        "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": {"record_local_plan_review"},
        "REVISING_PLAN": {"record_local_plan_review", "record_manual_plan_review"},
        "AWAITING_PLAN_APPROVAL": {"record_manual_plan_review"},
        "IMPLEMENTING": {"apply_plan_approval"},
        "SELF_REVIEWING_IMPLEMENTATION": {
            "complete_checkpoint", "enter_self_reviewing_implementation",
        },
        # workflow-2.5.0 CP3: record_bundle_generation's own phase write is
        # now the version-dependent bundle_generation_target_phase(stage,
        # governing_workflow_version) resolver -- its two possible outputs
        # are both counted against record_bundle_generation, exactly like
        # publish_plan_revision's own two version-keyed literals above.
        "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW": {
            "record_bundle_generation", "record_manual_implementation_review",
        },
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW": {"record_bundle_generation"},
        "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": {"record_local_implementation_review"},
        "APPLYING_REVIEW_FEEDBACK": {
            "enter_applying_review_feedback", "record_local_implementation_review",
            "record_manual_implementation_review",
        },
        "AWAITING_FUNCTIONAL_REVIEW": {"apply_technical_approval", "promote_legacy_work_item"},
        "MILESTONE_COMPLETE": {"complete_work_item"},
        "LEGACY_READY": {"import_legacy_work_item"},
        # workflow-2.4.0, D-Plan-Amendment-1: real and persisted, unlike
        # DECLARED_BUT_UNWRITTEN's four -- the mechanism must survive an
        # interruption between the request and the first post-request
        # /milestone-plan call.
        "AMENDING_PLAN": {"request_plan_amendment"},
    }

    def test_every_write_site_resolves_to_a_named_phase(self):
        """No `<unresolved:...>` pseudo-phase: if a new write site computes
        its target some way this census cannot follow, the census is blind
        there and must be taught the shape rather than silently under-
        reporting."""
        unresolved = sorted(p for p in _persisted_phase_writers() if p.startswith("<unresolved:"))
        self.assertEqual(unresolved, [])

    def test_the_persisted_phase_writers_are_exactly_these(self):
        census = {phase: writers for phase, writers in _persisted_phase_writers().items()}
        self.assertEqual(census, self.EXPECTED_WRITERS)

    def test_every_written_phase_is_a_known_phase(self):
        self.assertTrue(set(_persisted_phase_writers()).issubset(ws.KNOWN_PHASES))

    def test_exactly_six_known_phases_are_declared_but_never_written(self):
        unwritten = ws.KNOWN_PHASES - set(_persisted_phase_writers())
        self.assertEqual(unwritten, self.DECLARED_BUT_UNWRITTEN)

    def test_apply_technical_approval_goes_straight_to_the_functional_gate(self):
        """`AWAITING_TECHNICAL_APPROVAL` is computed reachability, never a
        persisted stop: the single writer on that edge moves the item from
        `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` directly to
        `AWAITING_FUNCTIONAL_REVIEW`."""
        work_item = ws.default_work_item(
            work_item_id="wi", work_item_type="process", work_item_kind="process",
            plan_path="docs/plan.md", registry_path="docs/registry.json",
            governing_workflow_version="2.1", plan_revision=1,
            last_transition="2026-01-01T00:00:00Z",
        )
        work_item["phase"] = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
        work_item["reviewed_implementation_head"] = "a" * 40
        state = {"schema_version": 1, "active_work_item_id": "wi",
                 "work_items": {"wi": work_item}}
        record = ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="implementation",
            user_confirmation="implementation wi", now="2026-01-01T00:00:00Z",
            reviewed_bundle_id="b" * 64, approved_review_content_id="c" * 64,
            review_content_manifest=[{"path": "docs/plan.md", "sha256": "d" * 64}],
            reviewed_content_commit="a" * 40,
        )
        new_state = ws.apply_technical_approval(state, "wi", record, "2026-01-01T00:00:01Z")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_FUNCTIONAL_REVIEW")
        self.assertNotIn(
            "AWAITING_TECHNICAL_APPROVAL",
            {wi["phase"] for wi in new_state["work_items"].values()},
        )


# ---------------------------------------------------------------------------
# CP8 (`plan-amendment-mechanism`): D-Plan-Amendment-1..8's own unit-level
# coverage -- CP2/CP3 authored request_plan_amendment/reconcile_checkpoints_
# after_amendment/apply_plan_approval's amendment branch/the anchor grammar,
# but (plan revision 34, section 4's own "items 11-15" note) deliberately
# deferred their dedicated tests to this checkpoint. Every class below tests
# functions that previously had zero coverage in this suite.
# ---------------------------------------------------------------------------


class TestCheckpointIdSupportsAnchor(unittest.TestCase):
    """`checkpoint_id_supports_anchor`'s `CP<digits>` shape gate
    (D-Plan-Amendment-4)."""

    def test_plain_cp_digits_is_supported(self):
        self.assertTrue(ws.checkpoint_id_supports_anchor("CP1"))
        self.assertTrue(ws.checkpoint_id_supports_anchor("CP123"))

    def test_non_cp_digits_shape_is_unsupported(self):
        self.assertFalse(ws.checkpoint_id_supports_anchor("WF4a-i"))

    def test_trailing_newline_is_unsupported(self):
        """IMPL4-O3: Python's `$` matches immediately before a trailing
        `\\n` as well as at the true end of string, so `"CP1\\n"` would
        otherwise pass this shape gate while remaining unsatisfiable by
        `_CHECKPOINT_ANCHOR_RE`'s own anchor-tag parser, which has no such
        allowance. `\\Z` closes it."""
        self.assertFalse(ws.checkpoint_id_supports_anchor("CP1\n"))


class TestCheckpointAnchorSpans(unittest.TestCase):
    """`parse_checkpoint_anchor_spans`'s closed, non-nesting, per-id
    balanced-tag grammar (D-Plan-Amendment-4, B5-new/I5-new)."""

    def test_two_disjoint_pairs_for_the_same_id_are_legal(self):
        text = "<!-- CP1 -->a<!-- /CP1 -->mid<!-- CP1 -->b<!-- /CP1 -->"
        spans = ws.parse_checkpoint_anchor_spans(text)
        self.assertEqual(len(spans["CP1"]), 2)

    def test_nested_open_tag_is_malformed_in_strict_mode(self):
        text = "<!-- CP1 --><!-- CP1 -->x<!-- /CP1 --><!-- /CP1 -->"
        with self.assertRaises(ws.AmendmentAnchorMalformedError):
            ws.parse_checkpoint_anchor_spans(text, strict=True)

    def test_orphan_close_tag_is_malformed_in_strict_mode(self):
        with self.assertRaises(ws.AmendmentAnchorMalformedError):
            ws.parse_checkpoint_anchor_spans("<!-- /CP2 -->", strict=True)

    def test_unterminated_open_tag_is_malformed_in_strict_mode(self):
        with self.assertRaises(ws.AmendmentAnchorMalformedError):
            ws.parse_checkpoint_anchor_spans("<!-- CP3 -->dangling", strict=True)

    def test_non_strict_mode_omits_only_the_malformed_id_never_raises(self):
        text = "<!-- CP1 -->ok<!-- /CP1 --><!-- /CP2 -->"
        spans = ws.parse_checkpoint_anchor_spans(text, strict=False)
        self.assertIn("CP1", spans)
        self.assertNotIn("CP2", spans)


class TestCheckpointContentHash(unittest.TestCase):
    def test_none_when_the_id_has_no_well_formed_spans(self):
        self.assertIsNone(ws.checkpoint_content_hash("no anchors here", "CP1"))

    def test_changes_when_the_span_content_changes(self):
        h1 = ws.checkpoint_content_hash("<!-- CP1 -->a<!-- /CP1 -->", "CP1", strict=True)
        h2 = ws.checkpoint_content_hash("<!-- CP1 -->b<!-- /CP1 -->", "CP1", strict=True)
        self.assertNotEqual(h1, h2)

    def test_identical_when_the_span_content_is_identical_despite_surrounding_prose(self):
        h1 = ws.checkpoint_content_hash("<!-- CP1 -->same<!-- /CP1 -->", "CP1", strict=True)
        h2 = ws.checkpoint_content_hash("prefix <!-- CP1 -->same<!-- /CP1 --> suffix", "CP1", strict=True)
        self.assertEqual(h1, h2)


class TestValidatePostAnchorCoverage(unittest.TestCase):
    @staticmethod
    def _registry(ids):
        return {"checkpoints": [{"id": cid} for cid in ids]}

    def test_a_missing_anchor_pair_is_refused(self):
        with self.assertRaises(ws.AmendmentAnchorCoverageError):
            ws.validate_post_anchor_coverage(
                "<!-- CP1 -->x<!-- /CP1 -->", self._registry(["CP1", "CP2"]),
            )

    def test_two_disjoint_pairs_for_the_same_id_are_legal_not_malformed(self):
        text = "<!-- CP1 -->a<!-- /CP1 -->mid<!-- CP1 -->b<!-- /CP1 -->"
        ws.validate_post_anchor_coverage(text, self._registry(["CP1"]))  # must not raise

    def test_an_orphan_close_tag_anywhere_is_malformed(self):
        with self.assertRaises(ws.AmendmentAnchorMalformedError):
            ws.validate_post_anchor_coverage("<!-- /CP1 -->", self._registry(["CP1"]))

    def test_an_overlapping_open_tag_anywhere_is_malformed(self):
        text = "<!-- CP1 --><!-- CP1 -->x<!-- /CP1 --><!-- /CP1 -->"
        with self.assertRaises(ws.AmendmentAnchorMalformedError):
            ws.validate_post_anchor_coverage(text, self._registry(["CP1"]))


class TestReconcileCheckpointsAfterAmendment(unittest.TestCase):
    """`reconcile_checkpoints_after_amendment`'s three-outcome algorithm
    plus its own dependency-closure pass -- pure and directly testable with
    no repository at all."""

    @staticmethod
    def _row(cid, depends_on=(), name=None):
        return {
            "id": cid, "name": name or f"checkpoint {cid}",
            "depends_on": list(depends_on), "complexity": 1, "session_target": 1,
        }

    @staticmethod
    def _registry(rows):
        return {"checkpoints": rows}

    def test_retained_when_row_and_content_are_both_unchanged(self):
        pre = self._registry([self._row("CP1")])
        post = self._registry([self._row("CP1")])
        text = "<!-- CP1 -->same<!-- /CP1 -->"
        checkpoints = {"CP1": {"status": "COMPLETE", "start_commit": "x"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, text, text, checkpoints)
        self.assertEqual(result["outcome"]["CP1"], "retained")
        self.assertEqual(result["checkpoints"]["CP1"]["status"], "COMPLETE")

    def test_needs_revalidation_when_the_registry_row_changed(self):
        pre = self._registry([self._row("CP1", name="old name")])
        post = self._registry([self._row("CP1", name="new name")])
        text = "<!-- CP1 -->same<!-- /CP1 -->"
        checkpoints = {"CP1": {"status": "COMPLETE", "start_commit": "x"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, text, text, checkpoints)
        self.assertEqual(result["outcome"]["CP1"], "needs_revalidation")
        self.assertEqual(result["checkpoints"]["CP1"]["status"], "NEEDS_REVALIDATION")

    def test_needs_revalidation_when_the_row_is_byte_identical_but_content_changed(self):
        """B6.2: the discriminator must see a redefinition the registry row
        alone would miss."""
        pre_row = self._row("CP1")
        pre = self._registry([pre_row])
        post = self._registry([dict(pre_row)])
        pre_text = "<!-- CP1 -->old design<!-- /CP1 -->"
        post_text = "<!-- CP1 -->new design<!-- /CP1 -->"
        checkpoints = {"CP1": {"status": "COMPLETE", "start_commit": "x"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, pre_text, post_text, checkpoints)
        self.assertEqual(result["outcome"]["CP1"], "needs_revalidation")

    def test_a_pre_text_with_no_anchors_anywhere_is_conservative(self):
        """B4-new.1: the legacy/no-anchor case flips every shared id rather
        than ever silently treating it as unchanged."""
        pre = self._registry([self._row("CP1")])
        post = self._registry([self._row("CP1")])
        pre_text = "plain legacy plan text with no anchors at all"
        post_text = "<!-- CP1 -->new<!-- /CP1 -->"
        checkpoints = {"CP1": {"status": "COMPLETE", "start_commit": "x"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, pre_text, post_text, checkpoints)
        self.assertEqual(result["outcome"]["CP1"], "needs_revalidation")

    def test_a_checkpoint_removed_from_the_registry_is_dropped(self):
        pre = self._registry([self._row("CP1"), self._row("CP2", depends_on=["CP1"])])
        post = self._registry([self._row("CP1")])
        pre_text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 -->"
        checkpoints = {"CP1": {"status": "COMPLETE"}, "CP2": {"status": "COMPLETE"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, pre_text, post_text, checkpoints)
        self.assertEqual(result["outcome"]["CP2"], "dropped")
        self.assertNotIn("CP2", result["checkpoints"])
        self.assertEqual(result["dropped"], ["CP2"])

    def test_a_checkpoint_new_to_the_registry_is_reported_new(self):
        pre = self._registry([self._row("CP1")])
        post = self._registry([self._row("CP1"), self._row("CP2", depends_on=["CP1"])])
        pre_text = "<!-- CP1 -->a<!-- /CP1 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        checkpoints = {"CP1": {"status": "COMPLETE"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, pre_text, post_text, checkpoints)
        self.assertEqual(result["outcome"]["CP2"], "new")
        self.assertNotIn("CP2", result["checkpoints"])

    def test_dependency_closure_flips_an_otherwise_unchanged_dependent(self):
        """B6.3: CPj changed -> NEEDS_REVALIDATION; CPk depends_on CPj, CPk
        itself unchanged -- CPk is also flipped by the closure pass."""
        pre = self._registry([self._row("CP1"), self._row("CP2", depends_on=["CP1"])])
        post = self._registry([self._row("CP1", name="changed"), self._row("CP2", depends_on=["CP1"])])
        text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        checkpoints = {"CP1": {"status": "COMPLETE"}, "CP2": {"status": "COMPLETE"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, text, text, checkpoints)
        self.assertEqual(result["outcome"]["CP1"], "needs_revalidation")
        self.assertEqual(result["outcome"]["CP2"], "needs_revalidation_dependency")
        self.assertEqual(result["checkpoints"]["CP2"]["status"], "NEEDS_REVALIDATION")

    def test_closure_derived_flip_is_reported_with_a_distinct_token(self):
        """IMPL6-B1: a closure-derived demotion must be distinguishable in
        the reported outcome from a direct row/content demotion -- both
        leave `status` at `NEEDS_REVALIDATION`, but only the outcome token
        says which pass caused it."""
        pre = self._registry([self._row("CP1"), self._row("CP2", depends_on=["CP1"])])
        post = self._registry([self._row("CP1", name="changed"), self._row("CP2", depends_on=["CP1"])])
        text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        checkpoints = {"CP1": {"status": "COMPLETE"}, "CP2": {"status": "COMPLETE"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, text, text, checkpoints)
        self.assertNotEqual(result["outcome"]["CP1"], result["outcome"]["CP2"])
        self.assertEqual(result["outcome"]["CP1"], "needs_revalidation")
        self.assertEqual(result["outcome"]["CP2"], "needs_revalidation_dependency")

    def test_a_non_complete_status_is_left_alone_by_needs_revalidation(self):
        """"any other status is left as-is (nothing to revalidate that has
        not already completed)"."""
        pre = self._registry([self._row("CP1", name="old")])
        post = self._registry([self._row("CP1", name="new")])
        text = "<!-- CP1 -->same<!-- /CP1 -->"
        checkpoints = {"CP1": {"status": "IN_PROGRESS", "start_commit": "x"}}
        result = ws.reconcile_checkpoints_after_amendment(pre, post, text, text, checkpoints)
        self.assertEqual(result["outcome"]["CP1"], "needs_revalidation")
        self.assertEqual(result["checkpoints"]["CP1"]["status"], "IN_PROGRESS")


class TestRequestPlanAmendment(unittest.TestCase):
    """`/request-plan-amendment`'s sole writer (D-Plan-Amendment-1/2/3)."""

    @staticmethod
    def _state_with_approved_plan(repo, work_item_id="wi", phase="IMPLEMENTING",
                                  review_content_id="rc-1"):
        approval_commit = repo.commit(
            "approve plan",
            trailers={"Workflow-Plan-Approval": review_content_id, "Workflow-Work-Item": work_item_id},
        )
        work_item = {
            "work_item_id": work_item_id, "phase": phase, "plan_revision": 1,
            "base_commit": repo.base,
            "plan_approval": {"status": "CURRENT", "approved_review_content_id": review_content_id},
            "checkpoints": {"CP1": {"status": "COMPLETE", "start_commit": approval_commit}},
            "current_checkpoint_id": None, "last_completed_checkpoint_id": "CP1",
        }
        state = {"schema_version": 1, "active_work_item_id": work_item_id,
                 "work_items": {work_item_id: work_item}}
        return state, approval_commit

    def test_success_supersedes_the_approval_and_enters_amending_plan(self):
        with ScratchRepo() as repo:
            state, approval_commit = self._state_with_approved_plan(repo)
            head = repo.head()
            new_state = ws.request_plan_amendment(
                state, "wi", "amend for a real reason", repo_root=repo.root,
                now="2026-01-01T00:00:00Z",
            )
            wi = new_state["work_items"]["wi"]
            self.assertEqual(wi["phase"], "AMENDING_PLAN")
            self.assertEqual(wi["plan_approval"]["status"], "SUPERSEDED")
            self.assertEqual(wi["amendment_base_commit"], head)
            self.assertEqual(len(wi["amendment_history"]), 1)
            entry = wi["amendment_history"][0]
            self.assertEqual(entry["pre_amendment_approval_commit"], approval_commit)
            self.assertIsNone(entry["resolved_at_plan_revision"])
            self.assertEqual(entry["requested_from_phase"], "IMPLEMENTING")
            # The original input state is never mutated in place.
            self.assertEqual(state["work_items"]["wi"]["phase"], "IMPLEMENTING")

    def test_self_reviewing_implementation_is_also_an_allowed_phase(self):
        with ScratchRepo() as repo:
            state, _ = self._state_with_approved_plan(repo, phase="SELF_REVIEWING_IMPLEMENTATION")
            new_state = ws.request_plan_amendment(
                state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
            )
            self.assertEqual(new_state["work_items"]["wi"]["phase"], "AMENDING_PLAN")

    def test_wrong_phase_is_refused(self):
        with ScratchRepo() as repo:
            state, _ = self._state_with_approved_plan(repo, phase="PLANNING")
            with self.assertRaises(ws.WrongPhaseForAmendmentRequestError):
                ws.request_plan_amendment(
                    state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
                )

    def test_unreachable_approval_commit_is_refused_before_superseding_anything(self):
        with ScratchRepo() as repo:
            state, _ = self._state_with_approved_plan(repo, review_content_id="rc-real")
            state["work_items"]["wi"]["plan_approval"]["approved_review_content_id"] = "rc-nonexistent"
            with self.assertRaises(ws.AmendmentApprovalCommitUnreachableError):
                ws.request_plan_amendment(
                    state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
                )
            self.assertEqual(state["work_items"]["wi"]["plan_approval"]["status"], "CURRENT")

    def test_a_second_request_against_an_already_amending_item_is_refused(self):
        with ScratchRepo() as repo:
            state, _ = self._state_with_approved_plan(repo)
            amended = ws.request_plan_amendment(
                state, "wi", "first", repo_root=repo.root, now="2026-01-01T00:00:00Z",
            )
            with self.assertRaises(ws.WrongPhaseForAmendmentRequestError):
                ws.request_plan_amendment(
                    amended, "wi", "second", repo_root=repo.root, now="2026-01-01T00:00:01Z",
                )

    def test_non_cp_digit_checkpoint_id_is_refused_before_superseding_anything(self):
        """IMPL2-R1: `workflow-v2-1-core`'s own real checkpoint id shape
        (`WF4a-i`) can never be given a well-formed `<!-- CPn -->` anchor
        pair -- `validate_post_anchor_coverage` would refuse the amended
        plan for it unconditionally, two review stages later, with no
        anchor text able to fix it. `request_plan_amendment` must refuse by
        name instead, before `plan_approval` is superseded."""
        with ScratchRepo() as repo:
            registry_path = "registry.json"
            _write(repo, registry_path, json.dumps({
                "work_item_id": "wi", "plan_revision": 1,
                "checkpoints": [{"id": "WF4a-i", "depends_on": []}],
            }))
            _commit_paths(repo, [registry_path], "add registry")
            work_item = _base_work_item(
                base_commit=repo.base,
                registry_path=registry_path,
                plan_approval=_current_plan_approval_covering(repo, registry_path),
                checkpoints={}, current_checkpoint_id=None, last_completed_checkpoint_id=None,
            )
            state = _base_state(wi=work_item)
            with self.assertRaises(ws.AmendmentCheckpointIdShapeError) as ctx:
                ws.request_plan_amendment(
                    state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
                )
            self.assertIn("WF4a-i", str(ctx.exception))
            # Refused before any write: plan_approval is never superseded.
            self.assertEqual(state["work_items"]["wi"]["plan_approval"]["status"], "CURRENT")

    def test_all_cp_digit_checkpoint_ids_are_unaffected(self):
        """The shape check is additive: a registry whose ids are already
        all `CP<digits>` (the only shape this milestone's own registries
        use) proceeds exactly as before."""
        with ScratchRepo() as repo:
            registry_path = "registry.json"
            _write(repo, registry_path, json.dumps({
                "work_item_id": "wi", "plan_revision": 1,
                "checkpoints": [{"id": "CP1", "depends_on": []}],
            }))
            approval_commit = _commit_paths(
                repo, [registry_path], "add registry",
                trailers={"Workflow-Plan-Approval": "rc-1", "Workflow-Work-Item": "wi"},
            )
            plan_approval = _current_plan_approval_covering(repo, registry_path)
            plan_approval["approved_review_content_id"] = "rc-1"
            work_item = _base_work_item(
                base_commit=repo.base,
                registry_path=registry_path,
                plan_approval=plan_approval,
                checkpoints={}, current_checkpoint_id=None, last_completed_checkpoint_id=None,
            )
            state = _base_state(wi=work_item)
            new_state = ws.request_plan_amendment(
                state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
            )
            self.assertEqual(new_state["work_items"]["wi"]["phase"], "AMENDING_PLAN")
            self.assertEqual(new_state["work_items"]["wi"]["amendment_history"][0]
                              ["pre_amendment_approval_commit"], approval_commit)

    def test_dirty_plan_stage_document_does_not_refuse_the_amendment_request(self):
        """IMPL3-R1: a work item with a `registry_path` whose plan-stage
        protected `plan_path` carries an uncommitted edit -- the single
        most likely working-tree state for an operator about to request a
        plan amendment -- must not be refused by
        `_assert_registry_covered_by_current_plan_approval`'s coverage
        check. `.claude/commands/request-plan-amendment.md` step 1 says in
        as many words that this command does not refuse on digest-only
        staleness (`D-Plan-Amendment-1`, `B-R12-1`); IMPL2-R1's fix
        reintroduced exactly that refusal through
        `_load_authoritative_registry_or_none`'s default coverage check.
        `require_plan_approval_coverage=False` (this round's fix) restores
        the narrower, id-shape-only read."""
        with ScratchRepo() as repo:
            registry_path = "registry.json"
            plan_path = "plan.md"
            _write(repo, registry_path, json.dumps({
                "work_item_id": "wi", "plan_revision": 1,
                "checkpoints": [{"id": "CP1", "depends_on": []}],
            }))
            _write(repo, plan_path, "original plan content\n")
            approval_commit = _commit_paths(
                repo, [registry_path, plan_path], "add registry and plan",
                trailers={"Workflow-Plan-Approval": "rc-1", "Workflow-Work-Item": "wi"},
            )
            plan_approval = _current_plan_approval_covering(repo, registry_path, plan_path)
            plan_approval["approved_review_content_id"] = "rc-1"
            work_item = _base_work_item(
                base_commit=repo.base,
                registry_path=registry_path,
                plan_path=plan_path,
                plan_approval=plan_approval,
                checkpoints={}, current_checkpoint_id=None, last_completed_checkpoint_id=None,
            )
            state = _base_state(wi=work_item)

            # Dirty the plan-stage protected plan_path in the working tree,
            # uncommitted -- the exact IMPL3-R1 scenario.
            (repo.root / plan_path).write_text("edited, not yet committed\n")

            new_state = ws.request_plan_amendment(
                state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
            )
            self.assertEqual(new_state["work_items"]["wi"]["phase"], "AMENDING_PLAN")
            self.assertEqual(new_state["work_items"]["wi"]["amendment_history"][0]
                              ["pre_amendment_approval_commit"], approval_commit)

    def test_legacy_v1_basis_plan_approval_does_not_refuse_the_amendment_request(self):
        """IMPL3-R1, second arm: a `LEGACY_V1`-basis `plan_approval`
        (`review_content_manifest: None`, schema-sanctioned per
        `validate_approval_record`) must not be refused either -- the
        coverage check's `registry_path` "not named in the manifest" arm
        fires unconditionally for this basis whenever a `registry_path` is
        declared, so a registry-bearing `LEGACY_V1` item was wrongly
        refused before this round's fix."""
        with ScratchRepo() as repo:
            registry_path = "registry.json"
            _write(repo, registry_path, json.dumps({
                "work_item_id": "wi", "plan_revision": 1,
                "checkpoints": [{"id": "CP1", "depends_on": []}],
            }))
            approval_commit = _commit_paths(
                repo, [registry_path], "add registry",
                trailers={"Workflow-Plan-Approval": "rc-1", "Workflow-Work-Item": "wi"},
            )
            work_item = _base_work_item(
                base_commit=repo.base,
                registry_path=registry_path,
                plan_approval={
                    "status": "CURRENT", "basis": "LEGACY_V1",
                    "approved_review_content_id": "rc-1",
                    "review_content_manifest": None,
                    "reviewed_bundle_id": None, "reviewed_content_commit": None,
                    "legacy_evidence": {"note": "pre-2.1 import"},
                },
                checkpoints={}, current_checkpoint_id=None, last_completed_checkpoint_id=None,
            )
            state = _base_state(wi=work_item)
            new_state = ws.request_plan_amendment(
                state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
            )
            self.assertEqual(new_state["work_items"]["wi"]["phase"], "AMENDING_PLAN")
            self.assertEqual(new_state["work_items"]["wi"]["amendment_history"][0]
                              ["pre_amendment_approval_commit"], approval_commit)

    def test_registry_checkpoint_missing_id_key_is_a_named_refusal(self):
        """IMPL3-O2, renamed IMPL4-O2: `_load_authoritative_registry_or_none`
        validates the registry's envelope but never its checkpoint-row
        shape, so a row missing `id` must be named here rather than
        escaping as an unnamed `KeyError` from the id-shape comprehension.
        Raises the amendment-specific `AmendmentRegistryMissingIdError`,
        not the completion-accounting-flavored `RegistryCoverageError`
        (IMPL4-O2)."""
        with ScratchRepo() as repo:
            registry_path = "registry.json"
            _write(repo, registry_path, json.dumps({
                "work_item_id": "wi", "plan_revision": 1,
                "checkpoints": [{"depends_on": []}],  # no "id" key at all
            }))
            _commit_paths(repo, [registry_path], "add registry")
            work_item = _base_work_item(
                base_commit=repo.base,
                registry_path=registry_path,
                plan_approval=_current_plan_approval_covering(repo, registry_path),
                checkpoints={}, current_checkpoint_id=None, last_completed_checkpoint_id=None,
            )
            state = _base_state(wi=work_item)
            with self.assertRaises(ws.AmendmentRegistryMissingIdError) as ctx:
                ws.request_plan_amendment(
                    state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
                )
            self.assertIn("no 'id' key", str(ctx.exception))
            self.assertEqual(state["work_items"]["wi"]["plan_approval"]["status"], "CURRENT")

    def test_checkpoint_already_in_progress_refuses(self):
        """XMODEL-R4-B1, missing-tests item 1: `request_plan_amendment`
        must refuse outright while a checkpoint is already IN_PROGRESS in
        WORKFLOW_STATE.json, rather than superseding `plan_approval` with
        live implementation state underneath it."""
        with ScratchRepo() as repo:
            state, _ = self._state_with_approved_plan(repo)
            state["work_items"]["wi"]["checkpoints"]["CP2"] = {
                "status": "IN_PROGRESS", "start_commit": repo.base,
            }
            state["work_items"]["wi"]["current_checkpoint_id"] = "CP2"
            with self.assertRaises(ws.AmendmentCheckpointActiveError):
                ws.request_plan_amendment(
                    state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:00Z",
                )
            self.assertEqual(state["work_items"]["wi"]["plan_approval"]["status"], "CURRENT")

    def test_outstanding_checkpoint_claim_with_no_local_in_progress_refuses(self):
        """XMODEL-R4-B1, missing-tests item 2 -- the actual reported race:
        `claim_checkpoint` (step 1d) is published to the filesystem claims
        directory *before* `transition_checkpoint_in_progress` writes
        `WORKFLOW_STATE.json`, so a claim can be outstanding while state
        still looks completely idle (`resolve_checkpoint_ownership`'s own
        supported `CONTINUE_CLAIM` window). A state-only IN_PROGRESS check
        would miss this window entirely; `request_plan_amendment` must
        also consult the shared claim record directly, exercising the real
        claim mechanism rather than only a sequential command-level
        precheck."""
        with ScratchRepo() as repo:
            state, _ = self._state_with_approved_plan(repo)
            # The checkpoint claim is real and published (`claim_checkpoint`),
            # but nothing in `state` reflects it -- CP2 is absent from
            # `checkpoints` entirely, exactly the "claimed but not yet
            # started in state" window.
            ws.claim_checkpoint(repo.root, "wi", "CP2", now="2026-01-01T00:00:00Z")
            with self.assertRaises(ws.AmendmentCheckpointActiveError):
                ws.request_plan_amendment(
                    state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:01Z",
                )
            self.assertEqual(state["work_items"]["wi"]["plan_approval"]["status"], "CURRENT")

    def test_amendment_succeeds_once_the_claim_is_properly_released(self):
        """XMODEL-R4-B1, missing-tests item 4: the ordinary quiescent
        amendment path is unaffected once the checkpoint claim has been
        released (step 1f, the normal end of a checkpoint's own
        lifecycle) -- the new guard is additive, not a regression for the
        uncontended case."""
        with ScratchRepo() as repo:
            state, _ = self._state_with_approved_plan(repo)
            claim = ws.claim_checkpoint(repo.root, "wi", "CP2", now="2026-01-01T00:00:00Z")
            ws.release_checkpoint(
                repo.root, "wi", "CP2", owner_token=claim["owner_token"],
                now="2026-01-01T00:00:01Z",
            )
            new_state = ws.request_plan_amendment(
                state, "wi", "reason", repo_root=repo.root, now="2026-01-01T00:00:02Z",
            )
            self.assertEqual(new_state["work_items"]["wi"]["phase"], "AMENDING_PLAN")

    def test_claim_checkpoint_refuses_once_amending_plan_is_already_durable(self):
        """XMODEL-R8-B1: `claim_checkpoint`'s own new pre-publication phase
        check (under `WORKFLOW_STATE.lock`) refuses, and publishes nothing,
        once the work item has already committed `AMENDING_PLAN` -- the half
        of the closed race in which the amendment side won the shared lock
        first. Deterministic, no concurrency needed: the on-disk state
        already shows `AMENDING_PLAN` before `claim_checkpoint` is ever
        called, exactly what a claim attempt arriving after the amendment's
        own critical section has already closed would observe."""
        with ScratchRepo() as repo:
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "schema_version": 1, "active_work_item_id": "wi",
                "work_items": {"wi": {"work_item_id": "wi", "phase": "AMENDING_PLAN"}},
            }))
            with self.assertRaises(ws.IllegalCheckpointStartPhaseError):
                ws.claim_checkpoint(repo.root, "wi", "CP2", now="2026-01-01T00:00:00Z")
            self.assertIsNone(ws.resolve_claim(repo.root, "wi"))

    def test_claim_checkpoint_with_no_state_entry_is_unaffected(self):
        """The phase check is skipped entirely for a work item with no
        `WORKFLOW_STATE.json` entry at all -- no amendment mechanism could
        ever race a claim for a work item state does not track, matching
        every other state-aware precondition in this module (`D1`)."""
        with ScratchRepo() as repo:
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "schema_version": 1, "active_work_item_id": "someone-else",
                "work_items": {"someone-else": {"work_item_id": "someone-else", "phase": "IMPLEMENTING"}},
            }))
            claim = ws.claim_checkpoint(repo.root, "wi", "CP2", now="2026-01-01T00:00:00Z")
            self.assertEqual(claim["checkpoint_id"], "CP2")


_AMENDMENT_RACE_CLAIMER_SOURCE = """
import json
import sys
import time
from pathlib import Path

import workflow_state as ws

repo_root = Path(sys.argv[1])
work_item_id = sys.argv[2]
checkpoint_id = sys.argv[3]
now = sys.argv[4]
ready_path = Path(sys.argv[5])
go_path = Path(sys.argv[6])
out_path = Path(sys.argv[7])

ready_path.write_text("ready")
deadline = time.monotonic() + 15
while not go_path.exists():
    if time.monotonic() > deadline:
        out_path.write_text(json.dumps({"outcome": "timeout"}))
        sys.exit(0)
    time.sleep(0.001)

try:
    claim = ws.claim_checkpoint(repo_root, work_item_id, checkpoint_id, now=now)
    out_path.write_text(json.dumps({"outcome": "success", "owner_token": claim["owner_token"]}))
except ws.IllegalCheckpointStartPhaseError as exc:
    out_path.write_text(json.dumps({"outcome": "refused", "detail": str(exc)}))
"""

_AMENDMENT_RACE_AMENDER_SOURCE = """
import json
import sys
import time
from pathlib import Path

import workflow_state as ws

repo_root = Path(sys.argv[1])
work_item_id = sys.argv[2]
reason = sys.argv[3]
now = sys.argv[4]
hold_seconds = float(sys.argv[5])
ready_path = Path(sys.argv[6])
go_path = Path(sys.argv[7])
out_path = Path(sys.argv[8])

ready_path.write_text("ready")
deadline = time.monotonic() + 15
while not go_path.exists():
    if time.monotonic() > deadline:
        out_path.write_text(json.dumps({"outcome": "timeout"}))
        sys.exit(0)
    time.sleep(0.001)


def mutator(state):
    # Holds WORKFLOW_STATE.lock (state_transaction's own, acquired before
    # this function is ever called) for `hold_seconds` before doing any of
    # request_plan_amendment's own work -- simulating the real, non-zero
    # wall-clock time that function's own git/registry reads take, so a
    # concurrent claim_checkpoint call issued during this window has a real
    # chance to actually block on the shared lock rather than merely run
    # before or after it.
    time.sleep(hold_seconds)
    return ws.request_plan_amendment(state, work_item_id, reason, repo_root=repo_root, now=now)


try:
    ws.state_transaction(repo_root, mutator)
    out_path.write_text(json.dumps({"outcome": "success"}))
except ws.AmendmentCheckpointActiveError as exc:
    out_path.write_text(json.dumps({"outcome": "refused", "detail": str(exc)}))
"""

_AMENDMENT_RACE_AMENDER_POST_RESOLVE_CLAIM_SOURCE = """
import json
import sys
import time
from pathlib import Path

import workflow_state as ws

repo_root = Path(sys.argv[1])
work_item_id = sys.argv[2]
reason = sys.argv[3]
now = sys.argv[4]
hold_seconds = float(sys.argv[5])
ready_path = Path(sys.argv[6])
go_path = Path(sys.argv[7])
out_path = Path(sys.argv[8])

# Cross-worktree reproduction (`XMODEL-R9-B1`): unlike
# `_AMENDMENT_RACE_AMENDER_SOURCE` above (whose `mutator` sleeps *before*
# calling `request_plan_amendment` at all -- fine for the same-worktree
# case, since the identical shared lock is held for the whole sleep
# either way), this worker places the delay exactly where the external
# review placed it: immediately after `request_plan_amendment`'s own
# authoritative `resolve_claim(...)` read returns, standing in for the
# git rev-parse / discover_plan_approval_commit / registry-load work that
# really follows it. That is the genuine window a claim published from a
# *different* worktree (a different `WORKFLOW_STATE.lock` file entirely)
# can land in undetected.
_real_resolve_claim = ws.resolve_claim


def _slow_resolve_claim(repo_root_arg, work_item_id_arg):
    result = _real_resolve_claim(repo_root_arg, work_item_id_arg)
    time.sleep(hold_seconds)
    return result


ws.resolve_claim = _slow_resolve_claim

ready_path.write_text("ready")
deadline = time.monotonic() + 15
while not go_path.exists():
    if time.monotonic() > deadline:
        out_path.write_text(json.dumps({"outcome": "timeout"}))
        sys.exit(0)
    time.sleep(0.001)

try:
    ws.state_transaction(
        repo_root,
        lambda state: ws.request_plan_amendment(state, work_item_id, reason, repo_root=repo_root, now=now),
    )
    out_path.write_text(json.dumps({"outcome": "success"}))
except ws.AmendmentCheckpointActiveError as exc:
    out_path.write_text(json.dumps({"outcome": "refused", "detail": str(exc)}))
"""

_AMENDMENT_RACE_SLOW_CLAIMER_SOURCE = """
import contextlib
import json
import sys
import time
from pathlib import Path

import workflow_state as ws

repo_root = Path(sys.argv[1])
work_item_id = sys.argv[2]
checkpoint_id = sys.argv[3]
now = sys.argv[4]
hold_seconds = float(sys.argv[5])
ready_path = Path(sys.argv[6])
go_path = Path(sys.argv[7])
out_path = Path(sys.argv[8])

# Missing-test item 1 (round 9 external implementation review): the
# claimer-wins-the-lock ordering, under real contention rather than the
# pre-existing deterministic unit test's claim-already-present setup.
# `claim_checkpoint` has no `hold_seconds` parameter of its own (unlike
# `request_plan_amendment`'s caller-supplied `mutator`, which the amender
# worker already sleeps inside), so this worker holds the real, shared
# `WORKFLOW_STATE.lock` for `hold_seconds` itself, standing in for the
# real wall-clock work `claim_checkpoint`'s own critical section does --
# by wrapping `state_lock`, the exact context manager `claim_checkpoint`
# acquires by bare name, so the real production function still runs, only
# with its held interval extended to something a concurrent process can
# reliably observe blocking on.
_real_state_lock = ws.state_lock


@contextlib.contextmanager
def _slow_state_lock(*a, **kw):
    with _real_state_lock(*a, **kw):
        time.sleep(hold_seconds)
        yield


ws.state_lock = _slow_state_lock

ready_path.write_text("ready")
deadline = time.monotonic() + 15
while not go_path.exists():
    if time.monotonic() > deadline:
        out_path.write_text(json.dumps({"outcome": "timeout"}))
        sys.exit(0)
    time.sleep(0.001)

try:
    claim = ws.claim_checkpoint(repo_root, work_item_id, checkpoint_id, now=now)
    out_path.write_text(json.dumps({"outcome": "success", "owner_token": claim["owner_token"]}))
except ws.IllegalCheckpointStartPhaseError as exc:
    out_path.write_text(json.dumps({"outcome": "refused", "detail": str(exc)}))
"""


class TestAmendmentClaimRaceRealProcesses(unittest.TestCase):
    """`XMODEL-R8-B1`: `request_plan_amendment`'s authoritative
    `resolve_claim(...)` read and `claim_checkpoint`'s own publication now
    share one real, on-disk lock (`WORKFLOW_STATE.lock`, `state_lock`), run
    as genuinely separate OS processes racing on it -- a single-process or
    threaded fixture cannot reproduce two independent holders contending for
    the same `fcntl.flock` (`TestRealProcessConcurrentTakeover`'s own
    reasoning, applied to this pair). Proves the specific window the finding
    named: the amendment worker is made to hold the lock for a real,
    measurable interval (`mutator`'s own `time.sleep`, standing in for
    `request_plan_amendment`'s own git/registry work) before it does
    anything else, while a concurrent `claim_checkpoint` call is issued
    against the identical lock file -- and confirms it does not merely run
    before or after unrelated to the amendment, but genuinely blocks for the
    held duration and only then resolves, correctly, against whatever the
    amendment committed."""

    @classmethod
    def setUpClass(cls):
        cls._scripts_dir = Path(__file__).resolve().parent
        cls._worker_dir = Path(tempfile.mkdtemp(prefix="wf-amend-race-worker-"))
        cls._claimer = cls._worker_dir / "_amend_race_claimer.py"
        cls._claimer.write_text(_AMENDMENT_RACE_CLAIMER_SOURCE)
        cls._amender = cls._worker_dir / "_amend_race_amender.py"
        cls._amender.write_text(_AMENDMENT_RACE_AMENDER_SOURCE)
        cls._slow_claimer = cls._worker_dir / "_amend_race_slow_claimer.py"
        cls._slow_claimer.write_text(_AMENDMENT_RACE_SLOW_CLAIMER_SOURCE)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._worker_dir, ignore_errors=True)

    def _spawn(self, worker: Path, *args) -> subprocess.Popen:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self._scripts_dir) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.Popen(
            [sys.executable, str(worker), *[str(a) for a in args]], env=env,
        )

    @staticmethod
    def _wait_for(path: Path, what: str) -> None:
        deadline = time.monotonic() + 15
        while not path.exists():
            if time.monotonic() > deadline:
                raise AssertionError(f"{what} did not become ready in time")
            time.sleep(0.001)

    def _state_with_approved_plan(self, repo):
        approval_commit = repo.commit(
            "approve plan", trailers={"Workflow-Plan-Approval": "rc-1", "Workflow-Work-Item": "wi"},
        )
        state = {
            "schema_version": 1, "active_work_item_id": "wi",
            "work_items": {"wi": {
                "work_item_id": "wi", "phase": "IMPLEMENTING", "plan_revision": 1,
                "base_commit": repo.base,
                "plan_approval": {"status": "CURRENT", "approved_review_content_id": "rc-1"},
                "checkpoints": {"CP1": {"status": "COMPLETE", "start_commit": approval_commit}},
                "current_checkpoint_id": None, "last_completed_checkpoint_id": "CP1",
            }},
        }
        state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state))
        return state_path

    def test_amendment_holds_the_lock_and_the_concurrent_claim_genuinely_blocks_then_correctly_refuses(self):
        with ScratchRepo() as repo:
            state_path = self._state_with_approved_plan(repo)
            with tempfile.TemporaryDirectory(prefix="wf-amend-race-io-") as scratch:
                scratch_path = Path(scratch)
                ready_amend, go_amend, out_amend = (
                    scratch_path / "ready_amend", scratch_path / "go_amend", scratch_path / "out_amend.json")
                ready_claim, go_claim, out_claim = (
                    scratch_path / "ready_claim", scratch_path / "go_claim", scratch_path / "out_claim.json")

                hold_seconds = 1.0
                amender = self._spawn(
                    self._amender, repo.root, "wi", "reason", "2026-01-01T00:00:00Z", hold_seconds,
                    ready_amend, go_amend, out_amend,
                )
                claimer = self._spawn(
                    self._claimer, repo.root, "wi", "CP2", "2026-01-01T00:00:01Z",
                    ready_claim, go_claim, out_claim,
                )
                try:
                    self._wait_for(ready_amend, "amender")
                    self._wait_for(ready_claim, "claimer")

                    # Release the amender first, and give it a moment to
                    # actually win the flock acquisition and enter its
                    # sleep -- then release the claimer while the amender
                    # is provably still inside its held critical section.
                    go_amend.write_text("go")
                    time.sleep(0.2)
                    started_blocking_at = time.monotonic()
                    go_claim.write_text("go")

                    self.assertEqual(amender.wait(timeout=15), 0)
                    self.assertEqual(claimer.wait(timeout=15), 0)
                    blocked_for = time.monotonic() - started_blocking_at

                    amend_result = json.loads(out_amend.read_text())
                    claim_result = json.loads(out_claim.read_text())

                    self.assertEqual(amend_result["outcome"], "success", amend_result)
                    self.assertEqual(claim_result["outcome"], "refused", claim_result)

                    # The claimer, released 0.2s into the amender's 1.0s
                    # held interval, could not have resolved in a few
                    # milliseconds the way an unheld `os.link` acquisition
                    # would -- proof it genuinely blocked on the shared lock
                    # rather than racing past an unheld one. A generous
                    # threshold, well under the ~0.8s actually expected,
                    # avoids flaking on process-startup jitter while still
                    # clearly distinguishing "blocked" from "raced past."
                    self.assertGreaterEqual(
                        blocked_for, 0.5,
                        "the claimer resolved too quickly to have actually blocked on the "
                        "shared WORKFLOW_STATE.lock")
                finally:
                    for p in (amender, claimer):
                        if p.poll() is None:
                            p.kill()
                            p.wait(timeout=5)

            final_state = json.loads(state_path.read_text())
            final_phase = final_state["work_items"]["wi"]["phase"]
            claim = ws.resolve_claim(repo.root, "wi")

            self.assertEqual(final_phase, "AMENDING_PLAN")
            self.assertIsNone(
                claim, "a checkpoint claim survived alongside a committed AMENDING_PLAN phase -- "
                "exactly the XMODEL-R8-B1 defect this fix closes")

    def test_claimer_wins_the_lock_and_the_concurrent_amendment_genuinely_blocks_then_correctly_refuses(self):
        """Round 8's own acceptance criterion 6, completed (missing-test
        item 1, round 9 external implementation review): the claimer-wins
        ordering under real contention. The pre-existing deterministic unit
        test (`claim_checkpoint` called with a claim already present before
        `request_plan_amendment` runs) does not prove mutual exclusion when
        claim *publication* races the amendment's authoritative claim
        *read* itself -- exactly the gap round 8's own feedback named. Here
        the claimer is released first and made to hold the real,
        shared `WORKFLOW_STATE.lock` for a real, measurable interval before
        it does anything else, while a concurrent `request_plan_amendment`
        call is issued against the identical lock file -- confirming it
        genuinely blocks for the held duration, then correctly refuses
        rather than superseding a plan approval a live claim already
        stands against."""
        with ScratchRepo() as repo:
            state_path = self._state_with_approved_plan(repo)
            with tempfile.TemporaryDirectory(prefix="wf-amend-race-io-") as scratch:
                scratch_path = Path(scratch)
                ready_claim, go_claim, out_claim = (
                    scratch_path / "ready_claim", scratch_path / "go_claim", scratch_path / "out_claim.json")
                ready_amend, go_amend, out_amend = (
                    scratch_path / "ready_amend", scratch_path / "go_amend", scratch_path / "out_amend.json")

                hold_seconds = 1.0
                claimer = self._spawn(
                    self._slow_claimer, repo.root, "wi", "CP2", "2026-01-01T00:00:00Z", hold_seconds,
                    ready_claim, go_claim, out_claim,
                )
                amender = self._spawn(
                    self._amender, repo.root, "wi", "reason", "2026-01-01T00:00:01Z", 0.0,
                    ready_amend, go_amend, out_amend,
                )
                try:
                    self._wait_for(ready_claim, "claimer")
                    self._wait_for(ready_amend, "amender")

                    # Release the claimer first, and give it a moment to
                    # actually win the flock acquisition and enter its
                    # held interval -- then release the amender while the
                    # claimer is provably still inside its critical section.
                    go_claim.write_text("go")
                    time.sleep(0.2)
                    started_blocking_at = time.monotonic()
                    go_amend.write_text("go")

                    self.assertEqual(claimer.wait(timeout=15), 0)
                    self.assertEqual(amender.wait(timeout=15), 0)
                    blocked_for = time.monotonic() - started_blocking_at

                    claim_result = json.loads(out_claim.read_text())
                    amend_result = json.loads(out_amend.read_text())

                    self.assertEqual(claim_result["outcome"], "success", claim_result)
                    self.assertEqual(amend_result["outcome"], "refused", amend_result)

                    self.assertGreaterEqual(
                        blocked_for, 0.5,
                        "the amender resolved too quickly to have actually blocked on the "
                        "shared WORKFLOW_STATE.lock")
                finally:
                    for p in (claimer, amender):
                        if p.poll() is None:
                            p.kill()
                            p.wait(timeout=5)

            final_state = json.loads(state_path.read_text())
            final_item = final_state["work_items"]["wi"]
            claim = ws.resolve_claim(repo.root, "wi")

            self.assertEqual(final_item["phase"], "IMPLEMENTING")
            self.assertEqual(final_item["plan_approval"]["status"], "CURRENT")
            self.assertIsNotNone(
                claim, "the claim published first must survive an amendment that lost the race "
                "for the shared WORKFLOW_STATE.lock")


class TestCrossWorktreeAmendmentClaimResidualXModelR9B1(unittest.TestCase):
    """`XMODEL-R9-B1` (round 9 external implementation review,
    `docs/defects/v2.4.0-002-amendment-claim-race-crosses-worktree-
    boundary.md`): round 8's fix (`TestAmendmentClaimRaceRealProcesses`
    above) closes the amendment-versus-claim-start race only *within one
    worktree root*. These tests pin the documented residual across linked
    worktrees of the same repository -- not a regression to silently
    worsen, and not (yet) a bug to silently fix either: a future change to
    either direction of this behavior must also update the defect
    record above, which is exactly what a test failure here is meant to
    surface. Missing-test item 2, round 9 external implementation review."""

    def _write_state(self, repo_root: Path, *, phase: str, checkpoint_id: str | None = None,
                     checkpoints: dict | None = None) -> Path:
        state = {
            "schema_version": 1, "active_work_item_id": "wi",
            "work_items": {"wi": {
                "work_item_id": "wi", "phase": phase, "plan_revision": 1,
                "base_commit": "0" * 40,
                "plan_approval": {"status": "CURRENT" if phase != "AMENDING_PLAN" else "SUPERSEDED",
                                  "approved_review_content_id": "rc-1"},
                "checkpoints": checkpoints or {},
                "current_checkpoint_id": checkpoint_id,
                "last_completed_checkpoint_id": None,
            }},
        }
        state_path = repo_root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state))
        return state_path

    def test_deterministic_a_durable_amending_plan_in_one_worktree_does_not_block_a_claim_from_another(self):
        """The second, independent reason named in `XMODEL-R9-B1`: no
        concurrency at all is needed to reproduce this half. Worktree A's
        own working-tree `WORKFLOW_STATE.json` durably records
        `AMENDING_PLAN`; worktree B's own copy still says `IMPLEMENTING`
        (as it would if B's branch has not merged A's amendment commit).
        `claim_checkpoint` reads only its own worktree's copy, so it
        succeeds from B even though the work item is, in worktree A,
        already `AMENDING_PLAN`."""
        with ScratchRepo() as repo:
            wt_b = repo.worktree("b")
            self._write_state(repo.root, phase="AMENDING_PLAN")
            self._write_state(wt_b, phase="IMPLEMENTING")

            claim = ws.claim_checkpoint(wt_b, "wi", "CP2", now="t1")

            self.assertEqual(claim["checkpoint_id"], "CP2")
            a_state = json.loads((repo.root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
            self.assertEqual(a_state["work_items"]["wi"]["phase"], "AMENDING_PLAN")

    def test_racing_an_amendment_in_one_worktree_and_a_claim_in_another_are_not_serialized(self):
        """The first, independent reason named in `XMODEL-R9-B1`, under
        real contention: `WORKFLOW_STATE.lock` is per-worktree
        (`repo_root`-scoped), so an amendment racing a claim start from a
        *different* worktree take `flock` on two different inodes and are
        not ordered by it at all -- both sides may succeed, unlike the
        same race within one worktree (`TestAmendmentClaimRaceRealProcesses`
        above), where exactly one must win and the other must be refused."""
        with ScratchRepo() as repo:
            wt_b = repo.worktree("b")
            approval_commit = repo.commit(
                "approve plan", trailers={"Workflow-Plan-Approval": "rc-1", "Workflow-Work-Item": "wi"},
            )
            state = {
                "schema_version": 1, "active_work_item_id": "wi",
                "work_items": {"wi": {
                    "work_item_id": "wi", "phase": "IMPLEMENTING", "plan_revision": 1,
                    "base_commit": repo.base,
                    "plan_approval": {"status": "CURRENT", "approved_review_content_id": "rc-1"},
                    "checkpoints": {"CP1": {"status": "COMPLETE", "start_commit": approval_commit}},
                    "current_checkpoint_id": None, "last_completed_checkpoint_id": "CP1",
                }},
            }
            for root in (repo.root, wt_b):
                state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(state))

            scripts_dir = Path(__file__).resolve().parent
            worker_dir = Path(tempfile.mkdtemp(prefix="wf-amend-race-xwt-worker-"))
            amender = worker_dir / "_amend_race_amender_xwt.py"
            amender.write_text(_AMENDMENT_RACE_AMENDER_POST_RESOLVE_CLAIM_SOURCE)
            claimer = worker_dir / "_amend_race_claimer_xwt.py"
            claimer.write_text(_AMENDMENT_RACE_CLAIMER_SOURCE)

            def spawn(worker: Path, *args) -> subprocess.Popen:
                env = dict(os.environ)
                env["PYTHONPATH"] = str(scripts_dir) + (
                    os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
                return subprocess.Popen(
                    [sys.executable, str(worker), *[str(a) for a in args]], env=env,
                )

            def wait_for(path: Path, what: str) -> None:
                deadline = time.monotonic() + 15
                while not path.exists():
                    if time.monotonic() > deadline:
                        raise AssertionError(f"{what} did not become ready in time")
                    time.sleep(0.001)

            try:
                with tempfile.TemporaryDirectory(prefix="wf-amend-race-xwt-io-") as scratch:
                    scratch_path = Path(scratch)
                    ready_amend, go_amend, out_amend = (
                        scratch_path / "ready_amend", scratch_path / "go_amend", scratch_path / "out_amend.json")
                    ready_claim, go_claim, out_claim = (
                        scratch_path / "ready_claim", scratch_path / "go_claim", scratch_path / "out_claim.json")

                    hold_seconds = 1.0
                    amender_proc = spawn(
                        amender, repo.root, "wi", "reason", "2026-01-01T00:00:00Z", hold_seconds,
                        ready_amend, go_amend, out_amend,
                    )
                    claimer_proc = spawn(
                        claimer, wt_b, "wi", "CP2", "2026-01-01T00:00:01Z",
                        ready_claim, go_claim, out_claim,
                    )
                    try:
                        wait_for(ready_amend, "amender")
                        wait_for(ready_claim, "claimer")

                        go_amend.write_text("go")
                        time.sleep(0.2)
                        go_claim.write_text("go")

                        self.assertEqual(amender_proc.wait(timeout=15), 0)
                        self.assertEqual(claimer_proc.wait(timeout=15), 0)

                        amend_result = json.loads(out_amend.read_text())
                        claim_result = json.loads(out_claim.read_text())

                        # Both succeed: the amender's own authoritative
                        # `resolve_claim(...)` read (released at t=0) finds
                        # nothing yet and is not re-checked, so the claimer
                        # (released at t=0.2, into the genuine window the
                        # sleep stands in for) publishes into the shared
                        # claims directory undetected -- the claimer never
                        # blocks on the amender's own lock either, since it
                        # is a different `repo_root`'s own
                        # `WORKFLOW_STATE.lock` file. Unlike the
                        # same-worktree race above, where exactly one of
                        # these two outcomes must occur, both succeeding is
                        # the documented `XMODEL-R9-B1` residual, not a
                        # flake.
                        self.assertEqual(amend_result["outcome"], "success", amend_result)
                        self.assertEqual(claim_result["outcome"], "success", claim_result)
                    finally:
                        for p in (amender_proc, claimer_proc):
                            if p.poll() is None:
                                p.kill()
                                p.wait(timeout=5)
            finally:
                import shutil
                shutil.rmtree(worker_dir, ignore_errors=True)

            a_state = json.loads((repo.root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
            self.assertEqual(a_state["work_items"]["wi"]["phase"], "AMENDING_PLAN")
            self.assertIsNotNone(ws.resolve_claim(wt_b, "wi"))


class TestApplyPlanApprovalAmendmentBranch(unittest.TestCase):
    """`apply_plan_approval`'s four new, optional, keyword-only reconciliation
    parameters (D-Plan-Amendment-4)."""

    @staticmethod
    def _approval_record(review_content_id="rc-2"):
        return ws.build_approval_record(
            basis="EXTERNAL_APPROVE", stage="plan", user_confirmation="approve wi",
            now="2026-01-01T00:00:00Z", reviewed_bundle_id="b" * 64,
            approved_review_content_id=review_content_id,
            review_content_manifest=[{"path": "docs/plan.md", "sha256": "d" * 64}],
        )

    @staticmethod
    def _open_amendment_state():
        return {
            "schema_version": 1, "active_work_item_id": "wi",
            "work_items": {"wi": {
                "work_item_id": "wi", "phase": "AMENDING_PLAN", "plan_revision": 2,
                "checkpoints": {"CP1": {"status": "COMPLETE"}},
                "current_checkpoint_id": None, "last_completed_checkpoint_id": "CP1",
                "amendment_history": [{
                    "amendment_id": "0", "resolved_at_plan_revision": None,
                    "pre_amendment_approval_commit": "a" * 40,
                }],
            }},
        }

    def test_an_open_amendment_with_missing_reconciliation_inputs_is_refused(self):
        state = self._open_amendment_state()
        with self.assertRaises(ws.AmendmentReconciliationInputsMissingError):
            ws.apply_plan_approval(state, "wi", self._approval_record(), "2026-01-01T00:00:01Z")

    def test_an_already_resolved_amendment_is_refused(self):
        state = self._open_amendment_state()
        state["work_items"]["wi"]["amendment_history"][0]["resolved_at_plan_revision"] = 2
        registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        text = "<!-- CP1 -->a<!-- /CP1 -->"
        with self.assertRaises(ws.AmendmentAlreadyResolvedError):
            ws.apply_plan_approval(
                state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
                pre_registry=registry, pre_plan_text=text,
                post_registry=registry, post_plan_text=text,
            )

    def test_missing_post_anchor_coverage_is_refused_before_any_reconciliation(self):
        state = self._open_amendment_state()
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        post_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
            {"id": "CP2", "name": "new", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
        ]}
        pre_text = "<!-- CP1 -->a<!-- /CP1 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 -->"  # CP2 has no anchor pair at all
        with self.assertRaises(ws.AmendmentAnchorCoverageError):
            ws.apply_plan_approval(
                state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
                pre_registry=pre_registry, pre_plan_text=pre_text,
                post_registry=post_registry, post_plan_text=post_text,
            )
        # Refused before any write: the input work item is untouched.
        self.assertEqual(state["work_items"]["wi"]["phase"], "AMENDING_PLAN")

    def test_post_registry_only_non_cp_digit_id_is_a_named_shape_refusal(self):
        """IMPL3-O1: an id introduced *by the amendment itself* (absent from
        the pre-amendment registry, so `request_plan_amendment`'s own early
        shape check never saw it) that is not of the shape `CP<digits>`
        must raise `AmendmentCheckpointIdShapeError` here, not the
        unactionable `AmendmentAnchorCoverageError` -- no anchor text could
        ever satisfy the latter for this id."""
        state = self._open_amendment_state()
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        post_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
            {"id": "WF-New", "name": "new", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
        ]}
        pre_text = "<!-- CP1 -->a<!-- /CP1 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 -->"  # WF-New has no anchor either -- shape wins first
        with self.assertRaises(ws.AmendmentCheckpointIdShapeError) as ctx:
            ws.apply_plan_approval(
                state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
                pre_registry=pre_registry, pre_plan_text=pre_text,
                post_registry=post_registry, post_plan_text=post_text,
            )
        self.assertIn("WF-New", str(ctx.exception))
        self.assertEqual(state["work_items"]["wi"]["phase"], "AMENDING_PLAN")

    def test_post_registry_missing_checkpoints_key_is_a_named_refusal(self):
        """IMPL2-O2: a `post_registry` with no `"checkpoints"` key at all
        (e.g. a caller-side `json.loads` of a malformed on-disk registry)
        must raise a named error, not an unnamed `KeyError` from inside
        `validate_registry_topological_order` -- `validate_post_anchor_
        coverage` alone would pass this input vacuously via its own
        `.get("checkpoints", [])`."""
        state = self._open_amendment_state()
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        post_registry = {"no_checkpoints_key": True}
        pre_text = "<!-- CP1 -->a<!-- /CP1 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 -->"
        with self.assertRaises(ws.AmendmentPostRegistryMalformedError):
            ws.apply_plan_approval(
                state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
                pre_registry=pre_registry, pre_plan_text=pre_text,
                post_registry=post_registry, post_plan_text=post_text,
            )
        self.assertEqual(state["work_items"]["wi"]["phase"], "AMENDING_PLAN")

    def test_post_registry_checkpoint_missing_id_key_is_a_named_refusal(self):
        """IMPL4-O1: `validate_post_anchor_coverage` and
        `reconcile_checkpoints_after_amendment` both directly read
        `entry["id"]` from `post_registry["checkpoints"]` -- a row with no
        `id` key at all must raise a named error here, before either call,
        rather than escape as a bare, unnamed `KeyError` from whichever one
        happens to run first."""
        state = self._open_amendment_state()
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        post_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
            {"name": "no id at all", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
        ]}
        pre_text = "<!-- CP1 -->a<!-- /CP1 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 -->"
        with self.assertRaises(ws.AmendmentPostRegistryMalformedError) as ctx:
            ws.apply_plan_approval(
                state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
                pre_registry=pre_registry, pre_plan_text=pre_text,
                post_registry=post_registry, post_plan_text=post_text,
            )
        self.assertIn("no 'id' key", str(ctx.exception))
        self.assertEqual(state["work_items"]["wi"]["phase"], "AMENDING_PLAN")

    def test_successful_reconciliation_resolves_the_amendment_and_enters_implementing(self):
        state = self._open_amendment_state()
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        post_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
            {"id": "CP2", "name": "new", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
        ]}
        pre_text = "<!-- CP1 -->a<!-- /CP1 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        new_state = ws.apply_plan_approval(
            state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
            pre_registry=pre_registry, pre_plan_text=pre_text,
            post_registry=post_registry, post_plan_text=post_text,
        )
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["phase"], "IMPLEMENTING")
        self.assertEqual(wi["checkpoints"]["CP1"]["status"], "COMPLETE")
        self.assertNotIn("CP2", wi["checkpoints"])
        self.assertEqual(wi["amendment_history"][-1]["resolved_at_plan_revision"], 2)
        self.assertEqual(wi["plan_approval"]["approved_review_content_id"], "rc-2")
        # IMPL6-B1: the reconciliation outcome itself is recorded onto the
        # resolved amendment_history entry -- the durable field
        # `/approve-review plan` step 7 reports from, distinct from the
        # `checkpoints`/`dropped` fields this function already consumed.
        self.assertEqual(wi["amendment_history"][-1]["reconciliation_outcome"], {"CP1": "retained", "CP2": "new"})

    def test_reconciliation_outcome_distinguishes_direct_from_closure_derived_flips(self):
        """IMPL6-B1: the recorded `reconciliation_outcome` must retain the
        distinction `reconcile_checkpoints_after_amendment` computes
        between a direct demotion and a dependency-closure-derived one --
        `apply_plan_approval` stores the map verbatim, never collapsing
        it."""
        state = self._open_amendment_state()
        state["work_items"]["wi"]["checkpoints"]["CP2"] = {"status": "COMPLETE"}
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
            {"id": "CP2", "name": "n2", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
        ]}
        post_registry = {"checkpoints": [
            {"id": "CP1", "name": "changed", "depends_on": [], "complexity": 1, "session_target": 1},
            {"id": "CP2", "name": "n2", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
        ]}
        text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        new_state = ws.apply_plan_approval(
            state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
            pre_registry=pre_registry, pre_plan_text=text,
            post_registry=post_registry, post_plan_text=text,
        )
        outcome = new_state["work_items"]["wi"]["amendment_history"][-1]["reconciliation_outcome"]
        self.assertEqual(outcome["CP1"], "needs_revalidation")
        self.assertEqual(outcome["CP2"], "needs_revalidation_dependency")

    def test_a_dropped_current_or_last_completed_checkpoint_id_is_nulled(self):
        state = self._open_amendment_state()
        state["work_items"]["wi"]["checkpoints"]["CP2"] = {"status": "IN_PROGRESS"}
        state["work_items"]["wi"]["current_checkpoint_id"] = "CP2"
        state["work_items"]["wi"]["last_completed_checkpoint_id"] = "CP2"
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
            {"id": "CP2", "name": "n2", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
        ]}
        post_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        pre_text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        post_text = "<!-- CP1 -->a<!-- /CP1 -->"
        new_state = ws.apply_plan_approval(
            state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
            pre_registry=pre_registry, pre_plan_text=pre_text,
            post_registry=post_registry, post_plan_text=post_text,
        )
        wi = new_state["work_items"]["wi"]
        self.assertIsNone(wi["current_checkpoint_id"])
        self.assertIsNone(wi["last_completed_checkpoint_id"])
        self.assertNotIn("CP2", wi["checkpoints"])

    def test_with_no_open_amendment_the_four_parameters_are_never_consulted(self):
        """For a work item with no open amendment, behavior is byte-for-byte
        unchanged from v2.3.1 -- no existing call site needs to change."""
        state = {
            "schema_version": 1, "active_work_item_id": "wi",
            "work_items": {"wi": {
                "work_item_id": "wi", "phase": "PLANNING", "plan_revision": 1,
                "checkpoints": {},
            }},
        }
        new_state = ws.apply_plan_approval(state, "wi", self._approval_record(), "2026-01-01T00:00:01Z")
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["phase"], "IMPLEMENTING")
        self.assertNotIn("amendment_history", wi)

    def test_a_resolved_amendment_history_never_re_triggers_reconciliation(self):
        """`amendment_history` present but its last entry already resolved
        -- `has_open_amendment` is false, so the four parameters stay
        optional here too."""
        state = self._open_amendment_state()
        state["work_items"]["wi"]["amendment_history"][0]["resolved_at_plan_revision"] = 2
        new_state = ws.apply_plan_approval(state, "wi", self._approval_record(), "2026-01-01T00:00:01Z")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "IMPLEMENTING")

    def test_real_caller_shape_after_a_resolved_amendment_is_never_refused(self):
        """`OPUS-R145-001` regression: `approve-review.md` step 4c reads and
        forwards `post_plan_text`/`post_registry` *unconditionally* on every
        plan-stage approval, from the working tree, regardless of amendment
        state -- only `pre_registry`/`pre_plan_text` are gated there on an
        open amendment. Reproduces exactly that call shape (post-side
        supplied, pre-side `None`) against a work item whose last amendment
        is already resolved: the guard must key on the pre-side pair alone,
        never on "any of the four", or this ordinary, non-re-run call --
        the only shape the real caller ever produces once a work item has
        amended once -- would be wrongly refused forever after."""
        state = self._open_amendment_state()
        state["work_items"]["wi"]["amendment_history"][0]["resolved_at_plan_revision"] = 2
        registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        text = "<!-- CP1 -->a<!-- /CP1 -->"
        new_state = ws.apply_plan_approval(
            state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
            post_registry=registry, post_plan_text=text,
        )
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "IMPLEMENTING")
        # Reconciliation itself never ran (no open amendment) -- the last
        # amendment_history entry is untouched.
        self.assertEqual(
            new_state["work_items"]["wi"]["amendment_history"][0]["resolved_at_plan_revision"], 2,
        )

    def test_non_topological_post_registry_is_refused_before_reconciliation(self):
        """IMPL-O2: `reconcile_checkpoints_after_amendment`'s single
        forward-pass dependency closure relies on `post_registry`'s own
        order already being a valid topological order of `depends_on`
        (B6.3) -- a precondition `write_registry_and_mapping` enforces at
        write time, but `/approve-review plan` step 4c reads `post_registry`
        straight off the working tree, which a hand-edited (reviewed, but
        not mechanically re-checked) registry could violate. Now enforced
        directly inside `apply_plan_approval`, alongside the existing
        anchor-coverage validation, before any reconciliation runs."""
        state = self._open_amendment_state()
        pre_registry = {"checkpoints": [
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        pre_text = "<!-- CP1 -->a<!-- /CP1 -->"
        # CP2 depends on CP1 but is listed *before* it -- not a valid
        # topological order.
        post_registry = {"checkpoints": [
            {"id": "CP2", "name": "n2", "depends_on": ["CP1"], "complexity": 1, "session_target": 1},
            {"id": "CP1", "name": "n", "depends_on": [], "complexity": 1, "session_target": 1},
        ]}
        post_text = "<!-- CP1 -->a<!-- /CP1 --><!-- CP2 -->b<!-- /CP2 -->"
        with self.assertRaises(ws.NonTopologicalRegistryOrderError):
            ws.apply_plan_approval(
                state, "wi", self._approval_record(), "2026-01-01T00:00:01Z",
                pre_registry=pre_registry, pre_plan_text=pre_text,
                post_registry=post_registry, post_plan_text=post_text,
            )


class ReviewMaterialLifecycleMarkerTest(unittest.TestCase):
    """`workflow-2.5.0` CP1's canonical review-material-lifecycle marker:
    the sole representation `D-Review-Material-Lifecycle` (a later
    `workflow-2.5.0` design section) imports and reuses, never
    re-derived. See `docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s
    `D-Implementation-Review-Stages` `2.5.0 disposition record`
    subsection."""

    def test_render_marker_exact_bytes(self):
        self.assertEqual(
            ws.render_marker("HISTORICAL"),
            "<!-- review-material-lifecycle: HISTORICAL -->",
        )
        self.assertEqual(
            ws.render_marker("CURRENT"),
            "<!-- review-material-lifecycle: CURRENT -->",
        )

    def test_render_marker_rejects_third_state(self):
        with self.assertRaises(ValueError):
            ws.render_marker("WEIRD")

    def test_parse_marker_roundtrips_render_marker_output(self):
        for state in ws.REVIEW_MATERIAL_LIFECYCLE_STATES:
            self.assertEqual(ws.parse_marker(ws.render_marker(state)), state)

    def test_parse_marker_explicit_current_distinct_from_absent(self):
        # An explicit CURRENT marker parses to "CURRENT", the same value
        # the fail-closed default also produces for absent/malformed
        # input -- these are two different code paths a caller must not
        # conflate (this test only pins parse_marker's own return value
        # for each; the already-marked-nested-unit and marker-presence
        # fixtures D-Review-Material-Lifecycle names cover the caller-side
        # distinction).
        self.assertEqual(ws.parse_marker(ws.render_marker("CURRENT")), "CURRENT")
        self.assertIsNone(ws.parse_marker("no marker in this text at all"))

    def test_parse_marker_rejects_malformed_forms(self):
        historical = ws.render_marker("HISTORICAL")
        self.assertIsNone(ws.parse_marker(""))
        self.assertIsNone(ws.parse_marker("<!-- review-material-lifecycle: HISTORICAL -"))
        self.assertIsNone(ws.parse_marker("<!-- review-material-lifecycle: HISTORICALLY -->"))
        self.assertIsNone(ws.parse_marker("<!-- review-material-lifecycle: THIRD_STATE -->"))
        # Duplicated marker text in the same field is ambiguous, not two
        # independent HISTORICAL findings -- rejected, never averaged or
        # first-wins.
        self.assertIsNone(ws.parse_marker(historical + "\n" + historical))

    def test_parse_marker_accepts_marker_embedded_in_surrounding_prose(self):
        text = f"Some heading\n\n{ws.render_marker('HISTORICAL')}\n\nSome body text."
        self.assertEqual(ws.parse_marker(text), "HISTORICAL")


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP2: KNOWN_PHASES additions, generalized WF-Activate
# helpers for a "2.2" target, the version-aware activation/rollback event
# model, TWO_STAGE_PLAN_REVIEW_VERSIONS' plan-review-gate inheritance
# widening, and implementation_review_stages' normalize/read plumbing.
# ---------------------------------------------------------------------------


class TestKnownPhases22Additions(unittest.TestCase):
    def test_the_two_new_implementation_review_phases_are_known(self):
        self.assertIn("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", ws.KNOWN_PHASES)
        self.assertIn("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", ws.KNOWN_PHASES)

    def test_a_work_item_may_be_persisted_at_either_new_phase(self):
        for phase in ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"):
            wi = _base_work_item(governing_workflow_version="2.1", phase=phase)
            ws.validate_state(_base_state(wi=wi))  # must not raise


class TestActivationHelpersGeneralizedTargetVersion(unittest.TestCase):
    """`build_activated_config`/`build_rolled_back_config` generalized to a
    `target_version` parameter (default `"2.1"`, preserving the original
    `"1"` <-> `"2.1"` call shape byte-for-byte) rather than the prior
    hard-coded `"2.1"` literal."""

    def test_default_target_version_still_flips_only_default_workflow_version(self):
        """Regression: the pre-2.5.0 call shape (no target_version) must
        keep behaving exactly as before -- `supported_versions` already
        contains "2.1", so activating leaves it byte-unchanged."""
        config = ws.default_config()
        activated = ws.build_activated_config(config)
        self.assertEqual(activated["default_workflow_version"], "2.1")
        self.assertEqual(activated["supported_versions"], config["supported_versions"])
        self.assertEqual(config["default_workflow_version"], "1")  # input untouched

    def test_default_target_version_rollback_still_flips_to_v1(self):
        config = {**ws.default_config(), "default_workflow_version": "2.1"}
        rolled_back = ws.build_rolled_back_config(config)
        self.assertEqual(rolled_back["default_workflow_version"], "1")

    def test_activate_2_2_appends_to_supported_versions(self):
        config = {**ws.default_config(), "default_workflow_version": "2.1"}
        activated = ws.build_activated_config(config, target_version="2.2")
        self.assertEqual(activated["default_workflow_version"], "2.2")
        self.assertEqual(activated["supported_versions"], ["1", "2.1", "2.2"])
        self.assertEqual(config["default_workflow_version"], "2.1")  # input untouched

    def test_activate_2_2_is_idempotent_on_supported_versions_if_already_present(self):
        config = {
            "schema_version": ws.SCHEMA_VERSION,
            "default_workflow_version": "2.1",
            "supported_versions": ["1", "2.1", "2.2"],
        }
        activated = ws.build_activated_config(config, target_version="2.2")
        self.assertEqual(activated["supported_versions"], ["1", "2.1", "2.2"])

    def test_activate_2_2_rejects_already_activated(self):
        config = {**ws.default_config(), "default_workflow_version": "2.2"}
        with self.assertRaises(ws.AlreadyActivatedError):
            ws.build_activated_config(config, target_version="2.2")

    def test_activate_rejects_unsupported_target_version(self):
        with self.assertRaises(ValueError):
            ws.build_activated_config(ws.default_config(), target_version="3")

    def test_rollback_2_2_flips_back_to_2_1_never_a_fixed_1_literal(self):
        """The rollback destination is the version activation superseded,
        not a fixed "1" literal: "2.2" rolls back to "2.1", never to "1"."""
        config = {**ws.default_config(), "default_workflow_version": "2.2",
                  "supported_versions": ["1", "2.1", "2.2"]}
        rolled_back = ws.build_rolled_back_config(config, target_version="2.2")
        self.assertEqual(rolled_back["default_workflow_version"], "2.1")

    def test_rollback_2_2_does_not_remove_2_2_from_supported_versions(self):
        config = {**ws.default_config(), "default_workflow_version": "2.2",
                  "supported_versions": ["1", "2.1", "2.2"]}
        rolled_back = ws.build_rolled_back_config(config, target_version="2.2")
        self.assertEqual(rolled_back["supported_versions"], ["1", "2.1", "2.2"])

    def test_rollback_2_2_rejects_not_activated(self):
        config = {**ws.default_config(), "default_workflow_version": "2.1"}
        with self.assertRaises(ws.NotActivatedError):
            ws.build_rolled_back_config(config, target_version="2.2")

    def test_activate_then_rollback_2_2_then_2_1_reaches_v1(self):
        config = ws.default_config()
        activated_2_1 = ws.build_activated_config(config)
        activated_2_2 = ws.build_activated_config(activated_2_1, target_version="2.2")
        rolled_back_to_2_1 = ws.build_rolled_back_config(activated_2_2, target_version="2.2")
        self.assertEqual(rolled_back_to_2_1["default_workflow_version"], "2.1")
        rolled_back_to_1 = ws.build_rolled_back_config(rolled_back_to_2_1)
        self.assertEqual(rolled_back_to_1["default_workflow_version"], "1")


class TestVersionAwareActivationEventModel(unittest.TestCase):
    """`find_latest_activation_event`/`is_activated` read each event's own
    resolved destination version rather than a binary activated/not-
    activated trailer-kind check."""

    def test_is_activated_true_after_workflow_activation_2_2(self):
        with ScratchRepo() as repo:
            repo.commit("activate 2.2", trailers={"Workflow-Activation": "2.2"})
            self.assertTrue(ws.is_activated(repo.root))

    def test_load_config_raises_and_names_2_2_after_activation_2_2_missing_config(self):
        with ScratchRepo() as repo:
            repo.commit("activate 2.2", trailers={"Workflow-Activation": "2.2"})
            with self.assertRaises(ws.ConfigMissingAfterActivationError) as ctx:
                ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            self.assertIn("Workflow 2.2", str(ctx.exception))

    def test_blank_activation_trailer_value_reports_activated_true(self):
        """Missing-tests item (round 2 of round 1's own O3): activation-side
        twin of `test_unresolvable_rollback_trailer_value_reports_activated_
        bare` below -- `_activation_event_description`'s branch selection
        moved from `is not None` to truthiness, plus a trailer-name split,
        so a blank `Workflow-Activation` trailer (`destination_version ==
        ""`, falsy but not `None`) must still resolve fail-closed as
        activated, never fall through to not-activated."""
        with ScratchRepo() as repo:
            repo.commit("activate bare", trailers={"Workflow-Activation": ""})
            self.assertTrue(ws.is_activated(repo.root))

    def test_load_config_names_blank_activation_trailer_value_verbatim(self):
        """The blank trailer's own raw value (`''`) is named verbatim in the
        recovery message, never `"Workflow ''"` -- `_activation_event_
        description`'s unresolvable-miss branch, not its resolved-
        destination branch."""
        with ScratchRepo() as repo:
            repo.commit("activate bare", trailers={"Workflow-Activation": ""})
            with self.assertRaises(ws.ConfigMissingAfterActivationError) as ctx:
                ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            message = str(ctx.exception)
            self.assertIn("an unresolvable Workflow-Activation trailer value ''", message)
            self.assertNotIn("Workflow ''", message)

    def test_workflow_activation_1_trailer_resolves_activated_true(self):
        """Missing-tests item (I2): the activation-direction twin of
        `test_a_typo_d_rollback_trailer_value_resolves_activated_fail_closed`
        above and of the round-2 blank-activation-trailer pair just above --
        `Workflow-Activation: 1` is the one trailer value a naive
        `destination_version != "1"` reading answers differently from
        `2.4.0`'s own binary `kind == "activation"` check, which reports
        activated for every activation trailer value. The activation
        direction must stay fail-closed like every other direction: only a
        *resolved rollback* destination may ever report not-activated."""
        with ScratchRepo() as repo:
            repo.commit("activate one", trailers={"Workflow-Activation": "1"})
            self.assertTrue(ws.is_activated(repo.root))

    def test_rollback_2_1_still_resolves_not_activated(self):
        """Reproduces today's binary behavior exactly at the boundary it
        already covers."""
        with ScratchRepo() as repo:
            repo.commit("activate", trailers={"Workflow-Activation": "2.1"})
            repo.commit("rollback", trailers={"Workflow-Rollback": "2.1"})
            self.assertFalse(ws.is_activated(repo.root))
            config = ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            self.assertEqual(config["default_workflow_version"], "1")

    def test_rollback_2_2_still_resolves_activated(self):
        """A Workflow-Rollback: 2.2 commit resolves to destination "2.1",
        which is still activated -- the repository is still "2.1"-
        configured and ConfigMissingAfterActivationError's hard stop must
        stay armed. Left binary, this would silently disarm."""
        with ScratchRepo() as repo:
            repo.commit("activate 2.2", trailers={"Workflow-Activation": "2.2"})
            repo.commit("rollback 2.2", trailers={"Workflow-Rollback": "2.2"})
            self.assertTrue(ws.is_activated(repo.root))

    def test_load_config_missing_config_behavior_unchanged_after_rollback_2_2(self):
        with ScratchRepo() as repo:
            repo.commit("activate 2.2", trailers={"Workflow-Activation": "2.2"})
            repo.commit("rollback 2.2", trailers={"Workflow-Rollback": "2.2"})
            with self.assertRaises(ws.ConfigMissingAfterActivationError) as ctx:
                ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            self.assertIn("Workflow 2.1", str(ctx.exception))

    def test_unresolvable_rollback_trailer_value_reports_activated_bare(self):
        """Rollback-trailer-value miss resolves fail-closed: a bare/empty
        value never silently falls through to not-activated."""
        with ScratchRepo() as repo:
            repo.commit("rollback bare", trailers={"Workflow-Rollback": ""})
            self.assertTrue(ws.is_activated(repo.root))

    def test_unresolvable_rollback_trailer_value_reports_activated_unknown_version(self):
        with ScratchRepo() as repo:
            repo.commit("rollback unknown", trailers={"Workflow-Rollback": "2.9"})
            self.assertTrue(ws.is_activated(repo.root))

    def test_unresolvable_rollback_trailer_never_raises_keyerror(self):
        with ScratchRepo() as repo:
            repo.commit("rollback unknown", trailers={"Workflow-Rollback": "2.9"})
            # Must resolve cleanly, never raise -- fail-closed as
            # activated, not a bare uncaught KeyError.
            ws.is_activated(repo.root)

    def test_load_config_names_unresolvable_rollback_trailer_value_verbatim(self):
        with ScratchRepo() as repo:
            repo.commit("rollback unknown", trailers={"Workflow-Rollback": "2.9"})
            with self.assertRaises(ws.ConfigMissingAfterActivationError) as ctx:
                ws.load_config(repo.root, config_path=Path("nonexistent.json"))
            self.assertIn("2.9", str(ctx.exception))

    def test_find_latest_activation_event_resolves_activation_destination_directly(self):
        with ScratchRepo() as repo:
            commit = repo.commit("activate 2.2", trailers={"Workflow-Activation": "2.2"})
            event = ws.find_latest_activation_event(repo.root)
            self.assertEqual(event, ("activation", "2.2", commit))

    def test_find_latest_activation_event_resolves_rollback_destination_via_mapping(self):
        with ScratchRepo() as repo:
            commit = repo.commit("rollback 2.2", trailers={"Workflow-Rollback": "2.2"})
            event = ws.find_latest_activation_event(repo.root)
            self.assertEqual(event, ("rollback", "2.1", commit))

    def test_find_latest_activation_event_destination_none_for_unresolvable_rollback(self):
        with ScratchRepo() as repo:
            commit = repo.commit("rollback unknown", trailers={"Workflow-Rollback": "2.9"})
            event = ws.find_latest_activation_event(repo.root)
            self.assertEqual(event, ("rollback", None, commit))


class TestTwoStagePlanReviewVersionsInheritance(unittest.TestCase):
    """`TWO_STAGE_PLAN_REVIEW_VERSIONS = {"2.1", "2.2"}` replaces the exact
    `governing_workflow_version == "2.1"` literal at every plan-review gate
    site -- parametrized over ("1", "2.1", "2.2"), pinning that "1" and
    "2.1" stay byte-unchanged."""

    def test_publish_plan_revision_target_phase_per_version(self):
        expected = {
            "1": "AWAITING_EXTERNAL_PLAN_REVIEW",
            "2.1": "AWAITING_LOCAL_PLAN_REVIEW",
            "2.2": "AWAITING_LOCAL_PLAN_REVIEW",
        }
        for version, target_phase in expected.items():
            with self.subTest(version=version):
                wi = _base_work_item(governing_workflow_version=version, phase="PLANNING", plan_revision=1)
                new_state = ws.publish_plan_revision(_base_state(wi=wi), "wi", 2, "t1")
                self.assertEqual(new_state["work_items"]["wi"]["phase"], target_phase)

    def test_publish_plan_revision_rejects_unsupported_version(self):
        wi = _base_work_item(governing_workflow_version="3", phase="PLANNING", plan_revision=1)
        with self.assertRaises(ws.UnsupportedGoverningVersionError):
            ws.publish_plan_revision(_base_state(wi=wi), "wi", 2, "t1")

    def test_plan_approval_gate_reachable_per_version(self):
        # "1": the shared rule alone, no ledger involved.
        self.assertTrue(ws.plan_approval_gate_reachable(
            latest_round_status="APPROVE", governing_workflow_version="1",
            plan_review_stages=None, current_review_content_id="c1",
        ))
        # "2.1"/"2.2": both stages must be recorded APPROVE against the
        # current review_content_id.
        stages = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_PLAN_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
            ws.MANUAL_EXTERNAL_PLAN_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
        }
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                self.assertTrue(ws.plan_approval_gate_reachable(
                    latest_round_status="APPROVE", governing_workflow_version=version,
                    plan_review_stages=stages, current_review_content_id="c1",
                ))
                self.assertFalse(ws.plan_approval_gate_reachable(
                    latest_round_status="APPROVE", governing_workflow_version=version,
                    plan_review_stages=None, current_review_content_id="c1",
                ))

    def test_validate_local_plan_review_preconditions_accepts_2_2(self):
        wi = _base_work_item(governing_workflow_version="2.2", phase="AWAITING_LOCAL_PLAN_REVIEW")
        ws.validate_local_plan_review_preconditions(wi)  # must not raise

    def test_record_local_plan_review_rejects_wrong_version_1(self):
        wi = _base_work_item(governing_workflow_version="1", phase="AWAITING_LOCAL_PLAN_REVIEW")
        with self.assertRaises(ws.WrongGoverningVersionForPlanReviewStageError):
            ws.record_local_plan_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
                review_content_id="c1", round=1, now="t1",
            )

    def test_record_local_plan_review_accepts_2_2(self):
        wi = _base_work_item(governing_workflow_version="2.2", phase="AWAITING_LOCAL_PLAN_REVIEW")
        new_state = ws.record_local_plan_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
            review_content_id="c1", round=1, now="t1",
        )
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW")

    def test_validate_plan_review_stages_per_version(self):
        stages = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_PLAN_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
            ws.MANUAL_EXTERNAL_PLAN_REVIEW: None,
        }
        for version in ("2.1", "2.2"):
            with self.subTest(version=version):
                wi = _base_work_item(governing_workflow_version=version, plan_review_stages=stages)
                ws.validate_state(_base_state(wi=wi))  # must not raise
        wi = _base_work_item(governing_workflow_version="1", plan_review_stages=stages)
        with self.assertRaises(ws.PlanReviewStagesInvalidForVersionError):
            ws.validate_state(_base_state(wi=wi))

    def test_transition_to_awaiting_local_plan_review_works_for_2_2(self):
        wi = _base_work_item(governing_workflow_version="2.2", phase="REVISING_PLAN")
        new_state = ws.transition_to_awaiting_local_plan_review(_base_state(wi=wi), "wi", "t1")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_LOCAL_PLAN_REVIEW")


class TestImplementationReviewStagesLedgerPlumbing(unittest.TestCase):
    """`implementation_review_stages` normalize/read helpers, mirroring
    `normalize_plan_review_stages`/`_validate_plan_review_stages`."""

    def test_normalize_passes_through_canonical_keys_and_review_content_id(self):
        stages = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: {"verdict": "APPROVE"},
            ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: None,
        }
        self.assertEqual(ws.normalize_implementation_review_stages(stages), stages)

    def test_normalize_key_helper_is_the_identity_mapping_today(self):
        """No legacy lowercase variant has ever existed for this ledger
        (unlike `plan_review_stages`), so `_normalize_implementation_
        review_stage_key` is the identity function today -- and therefore
        `AmbiguousImplementationReviewStageKeyError` is unreachable through
        any two distinct raw keys a real caller could ever pass (two
        distinct dict keys can never both equal the same canonical name
        while the mapping is the identity). This test pins that identity
        mapping directly, since a genuine-conflict fixture cannot be
        constructed without it changing."""
        for key in (ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW, ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW, "review_content_id"):
            self.assertEqual(ws._normalize_implementation_review_stage_key(key), key)

    def test_normalize_never_raises_on_ordinary_canonical_input(self):
        stages = {ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: {"v": 1}, ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: {"v": 2}}
        ws.normalize_implementation_review_stages(stages)  # must not raise

    def test_validate_implementation_review_stages_none_is_fine_for_any_version(self):
        for version in ("1", "2.1", "2.2"):
            wi = _base_work_item(governing_workflow_version=version)
            wi["implementation_review_stages"] = None
            ws.validate_state(_base_state(wi=wi))  # must not raise

    def test_validate_implementation_review_stages_wrong_version_rejected(self):
        for version in ("1", "2.1"):
            with self.subTest(version=version):
                wi = _base_work_item(governing_workflow_version=version)
                wi["implementation_review_stages"] = {
                    "review_content_id": "c1",
                    ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: None,
                    ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: None,
                }
                with self.assertRaises(ws.ImplementationReviewStagesInvalidForVersionError):
                    ws.validate_state(_base_state(wi=wi))

    def test_validate_implementation_review_stages_manual_without_local_rejected(self):
        wi = _base_work_item(governing_workflow_version="2.2")
        wi["implementation_review_stages"] = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: None,
            ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
        }
        with self.assertRaises(ws.ManualImplementationStageWithoutLocalStageError):
            ws.validate_state(_base_state(wi=wi))

    def test_validate_implementation_review_stages_non_approve_verdict_rejected(self):
        wi = _base_work_item(governing_workflow_version="2.2")
        wi["implementation_review_stages"] = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: {"bundle_id": "b", "verdict": "REVISE", "round": 1, "completed_at": "t"},
            ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: None,
        }
        with self.assertRaises(ws.StageVerdictNotApproveError):
            ws.validate_state(_base_state(wi=wi))

    def test_validate_implementation_review_stages_both_approve_accepted(self):
        wi = _base_work_item(governing_workflow_version="2.2")
        wi["implementation_review_stages"] = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"},
        }
        ws.validate_state(_base_state(wi=wi))  # must not raise


class TestActivatingDoesNotMutateExistingWorkItems(unittest.TestCase):
    """Regression (MANUAL_EXTERNAL_PLAN_REVIEW round 1, finding I1's own
    required test): activating "2.2" changes only `default_work_item`'s
    own output for a *subsequently* created work item or remediation
    child, and mutates no already-existing `work_items[...]` entry's
    `governing_workflow_version`."""

    def test_route_work_item_leaves_existing_entries_governing_version_untouched(self):
        existing = _base_work_item(governing_workflow_version="2.1", phase="IMPLEMENTING")
        state = _base_state(existing=existing)
        config_2_2_default = {
            "schema_version": ws.SCHEMA_VERSION,
            "default_workflow_version": "2.2",
            "supported_versions": ["1", "2.1", "2.2"],
        }
        new_state = ws.route_work_item(
            state, config_2_2_default, work_item_id="fresh", work_item_type="process",
            work_item_kind="process", plan_path="p", registry_path="r",
            plan_revision=1, now="t1",
        )
        # The pre-existing item's own governing version is untouched.
        self.assertEqual(new_state["work_items"]["existing"]["governing_workflow_version"], "2.1")
        # A genuinely new item picks up the repository's current default.
        self.assertEqual(new_state["work_items"]["fresh"]["governing_workflow_version"], "2.2")

    def test_route_work_item_resume_branch_also_leaves_governing_version_untouched(self):
        existing = _base_work_item(governing_workflow_version="2.1", phase="IMPLEMENTING", plan_revision=1)
        state = _base_state(existing=existing)
        config_2_2_default = {
            "schema_version": ws.SCHEMA_VERSION,
            "default_workflow_version": "2.2",
            "supported_versions": ["1", "2.1", "2.2"],
        }
        new_state = ws.route_work_item(
            state, config_2_2_default, work_item_id="existing", work_item_type="process",
            work_item_kind="process", plan_path="p", registry_path="r",
            plan_revision=2, now="t1",
        )
        self.assertEqual(new_state["work_items"]["existing"]["governing_workflow_version"], "2.1")


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP3: D-Implementation-Review-Stages' own review-stage
# writers and gate widening, mirroring the plan-review-stage suite's
# coverage shape (`TestRecordLocalPlanReview`/`TestRecordManualPlanReview`/
# `TestTwoStagePlanReviewVersionsInheritance`) exactly, substituted for the
# implementation stage.
# ---------------------------------------------------------------------------


def _v22_work_item(**overrides) -> dict:
    defaults = {"governing_workflow_version": "2.2", "phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"}
    defaults.update(overrides)
    return _base_work_item(**defaults)


class TestRecordLocalImplementationReview(unittest.TestCase):
    def test_wrong_governing_version_rejected(self):
        for version in ("1", "2.1"):
            with self.subTest(version=version):
                wi = _base_work_item(governing_workflow_version=version, phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
                with self.assertRaises(ws.WrongGoverningVersionForImplementationReviewStageError):
                    ws.record_local_implementation_review(
                        _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
                        review_content_id="c1", round=1, now="t1",
                    )

    def test_wrong_phase_rejected(self):
        wi = _v22_work_item(phase="AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        with self.assertRaises(ws.WrongPhaseForImplementationReviewStageError):
            ws.record_local_implementation_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
                review_content_id="c1", round=1, now="t1",
            )

    def test_unknown_verdict_rejected(self):
        wi = _v22_work_item()
        with self.assertRaises(ws.UnknownImplementationReviewVerdictError):
            ws.record_local_implementation_review(
                _base_state(wi=wi), "wi", verdict="MAYBE", bundle_id="b1",
                review_content_id="c1", round=1, now="t1",
            )

    def test_approve_records_ledger_and_transitions_to_manual_stage(self):
        wi = _v22_work_item(state_revision=1)
        new_state = ws.record_local_implementation_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b1",
            review_content_id="c1", round=1, now="t1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(item["implementation_review_stages"], {
            "review_content_id": "c1",
            "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None,
        })
        self.assertEqual(item["state_revision"], 2)

    def test_revise_transitions_directly_to_applying_review_feedback_with_no_ledger_write(self):
        """Unlike the plan side's REVISING_PLAN: this writer itself sets
        APPLYING_REVIEW_FEEDBACK directly, so /apply-implementation-review's
        step 0 finds the phase already set and skips its own
        enter_applying_review_feedback call under the phase-conditional
        guard -- not because of a "1"/"2.1" vs "2.2" version branch; there
        is no version branch at step 0 any more."""
        wi = _v22_work_item(state_revision=1, implementation_review_stages=None)
        new_state = ws.record_local_implementation_review(
            _base_state(wi=wi), "wi", verdict="REVISE", bundle_id="b1",
            review_content_id="c1", round=1, now="t1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "APPLYING_REVIEW_FEEDBACK")
        self.assertIsNone(item["implementation_review_stages"])

    def test_block_is_a_true_no_op(self):
        wi = _v22_work_item(state_revision=1, implementation_review_stages=None)
        state = _base_state(wi=wi)
        new_state = ws.record_local_implementation_review(
            state, "wi", verdict="BLOCK", bundle_id="b1",
            review_content_id="c1", round=1, now="t1",
        )
        self.assertEqual(new_state, state)
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")


class TestRecordManualImplementationReview(unittest.TestCase):
    def _local_approved_wi(self, **overrides):
        defaults = {
            "phase": "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            "implementation_review_stages": {
                "review_content_id": "c1",
                "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
                "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None,
            },
        }
        defaults.update(overrides)
        return _v22_work_item(**defaults)

    def test_wrong_governing_version_rejected(self):
        wi = self._local_approved_wi(governing_workflow_version="2.1")
        with self.assertRaises(ws.WrongGoverningVersionForImplementationReviewStageError):
            ws.record_manual_implementation_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_wrong_phase_rejected(self):
        wi = self._local_approved_wi(phase="APPLYING_REVIEW_FEEDBACK")
        with self.assertRaises(ws.WrongPhaseForImplementationReviewStageError):
            ws.record_manual_implementation_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_wrong_role_rejected(self):
        wi = self._local_approved_wi()
        with self.assertRaises(ws.WrongReviewerRoleError):
            ws.record_manual_implementation_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="LOCAL_MODEL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_stale_review_content_id_is_hard_blocked(self):
        wi = self._local_approved_wi()
        with self.assertRaises(ws.StaleReviewContentIdError):
            ws.record_manual_implementation_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="stale",
            )

    def test_missing_local_approval_rejected(self):
        wi = self._local_approved_wi(implementation_review_stages={
            "review_content_id": "c1",
            "LOCAL_MODEL_IMPLEMENTATION_REVIEW": None,
            "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None,
        })
        with self.assertRaises(ws.MissingLocalApprovalForManualImplementationStageError):
            ws.record_manual_implementation_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_duplicate_ingestion_rejected(self):
        wi = self._local_approved_wi(implementation_review_stages={
            "review_content_id": "c1",
            "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {"bundle_id": "b1", "verdict": "APPROVE", "round": 1, "completed_at": "t1"},
            "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": {"bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2"},
        })
        with self.assertRaises(ws.DuplicateManualImplementationStageIngestionError):
            ws.record_manual_implementation_review(
                _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b3", round=2, now="t3",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="c1",
            )

    def test_approve_completes_ledger_and_transitions_to_terminal_phase(self):
        """Unlike the plan side's manual-APPROVE exit (a distinct
        AWAITING_PLAN_APPROVAL gate phase): the implementation side reuses
        the pre-existing AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW name as
        its own terminal "ready for approval" phase."""
        wi = self._local_approved_wi(state_revision=1)
        new_state = ws.record_manual_implementation_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            feedback_review_content_id="c1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(item["implementation_review_stages"]["MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"], {
            "bundle_id": "b2", "verdict": "APPROVE", "round": 1, "completed_at": "t2",
        })
        self.assertEqual(item["state_revision"], 2)
        self.assertTrue(ws.technical_approval_gate_reachable(
            latest_round_status="APPROVE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True,
            governing_workflow_version="2.2",
            implementation_review_stages=item["implementation_review_stages"],
            current_review_content_id="c1",
        ))

    def test_approve_records_actual_bundle_id_even_when_mismatched(self):
        wi = self._local_approved_wi()
        warning = ws.check_manual_stage_bundle_id_advisory(
            feedback_bundle_id="stale-wrapper-bundle", current_bundle_id="fresh-wrapper-bundle",
        )
        self.assertIsNotNone(warning)
        new_state = ws.record_manual_implementation_review(
            _base_state(wi=wi), "wi", verdict="APPROVE", bundle_id="stale-wrapper-bundle", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            feedback_review_content_id="c1",
        )
        self.assertEqual(
            new_state["work_items"]["wi"]["implementation_review_stages"]["MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"]["bundle_id"],
            "stale-wrapper-bundle",
        )

    def test_revise_transitions_directly_to_applying_review_feedback_with_no_ledger_write(self):
        wi = self._local_approved_wi(state_revision=1)
        new_state = ws.record_manual_implementation_review(
            _base_state(wi=wi), "wi", verdict="REVISE", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            feedback_review_content_id="c1",
        )
        item = new_state["work_items"]["wi"]
        self.assertEqual(item["phase"], "APPLYING_REVIEW_FEEDBACK")
        self.assertIsNone(item["implementation_review_stages"]["MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"])

    def test_block_is_a_true_no_op(self):
        wi = self._local_approved_wi(state_revision=1)
        state = _base_state(wi=wi)
        new_state = ws.record_manual_implementation_review(
            state, "wi", verdict="BLOCK", bundle_id="b2", round=1, now="t2",
            current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            feedback_review_content_id="c1",
        )
        self.assertEqual(new_state, state)
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP5: the §2.3 convergence/token-efficiency disposable-repo
# fixture -- REQ-4 (no bundle regeneration between the local-approve and
# manual-external stages) and REQ-5 (a single reviewer pass can no longer,
# by itself, exhaust a manual-external round on an issue the local pass
# would have caught for free), demonstrated end to end against a real Git
# history rather than asserted only structurally.
# ---------------------------------------------------------------------------


class TestLocalStageCatchesPlantedDefectWithoutManualRound(unittest.TestCase):
    """§2.3 point 4's disposable-repo fixture: a realistic planted defect,
    caught by the `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage, never reaches
    -- and never opens or consumes a round of -- the manual-external
    stage."""

    def test_planted_defect_is_caught_locally_without_opening_a_manual_round(self):
        with ScratchRepo() as repo:
            # The disposable repo's own planted defect: a commit standing in
            # for an implementation checkpoint that a careful reviewer
            # should catch (e.g. a missing test, or a layering violation) --
            # the fixture only needs the review-stage state machine's own
            # reaction to a REVISE verdict, not a real static-analysis
            # finding.
            repo.commit("implement checkpoint with a planted defect", filename="src/planted_defect.py")
            wi = _v22_work_item(base_commit=repo.base, implementation_review_stages={
                "review_content_id": "c-defect",
                "LOCAL_MODEL_IMPLEMENTATION_REVIEW": None,
                "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None,
            })
            state = _base_state(wi=wi)

            # LOCAL_MODEL_IMPLEMENTATION_REVIEW catches the planted defect on
            # its very first round.
            new_state = ws.record_local_implementation_review(
                state, "wi", verdict="REVISE", bundle_id="b-defect",
                review_content_id="c-defect", round=1, now="t1",
            )
            item = new_state["work_items"]["wi"]

            # The defect is routed straight back for a fix -- it never
            # reached, and never consumed a round of, the manual-external
            # stage. This is REQ-5's convergence claim demonstrated, not
            # merely the structural argument that the split makes it
            # possible.
            self.assertEqual(item["phase"], "APPLYING_REVIEW_FEEDBACK")
            self.assertNotEqual(item["phase"], "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
            self.assertIsNone(item["implementation_review_stages"]["MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"])


class TestNoBundleRegenerationBetweenImplementationReviewStages(unittest.TestCase):
    """REQ-4: the same bundle, `bundle_id`, and `review_content_id` the
    local stage approved is what the manual-external stage is fed -- mirrors
    the plan side, which already has this property structurally
    (`D-Plan-Review-Stages`). Demonstrated two ways against the same
    disposable-repo fixture: the ordinary carry-through succeeds unchanged,
    and a simulated regeneration between the two stages is hard-blocked
    rather than silently accepted under the stale local approval."""

    def _locally_approved_wi(self, repo, **overrides):
        defaults = {
            "base_commit": repo.base,
            "phase": "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            "implementation_review_stages": {
                "review_content_id": "c-original",
                "LOCAL_MODEL_IMPLEMENTATION_REVIEW": {
                    "bundle_id": "b-original", "verdict": "APPROVE", "round": 1, "completed_at": "t1",
                },
                "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": None,
            },
        }
        defaults.update(overrides)
        return _v22_work_item(**defaults)

    def test_ordinary_carry_through_ingests_the_same_bundle_unchanged(self):
        with ScratchRepo() as repo:
            repo.commit("implement checkpoint", filename="src/feature.py")
            state = _base_state(wi=self._locally_approved_wi(repo))
            new_state = ws.record_manual_implementation_review(
                state, "wi", verdict="APPROVE", bundle_id="b-original", round=1, now="t2",
                current_review_content_id="c-original",
                feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="c-original",
            )
            self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW")

    def test_regeneration_between_stages_is_hard_blocked_not_silently_ingested(self):
        with ScratchRepo() as repo:
            repo.commit("implement checkpoint", filename="src/feature.py")
            state = _base_state(wi=self._locally_approved_wi(repo))

            # A bundle-refresh step run between the two stages (exactly the
            # accident §2.3 point 1 warns is "easy to accidentally regress
            # by adding... where none belongs") changes the protected
            # content the manual stage would see, so its own live
            # review_content_id no longer matches what
            # LOCAL_MODEL_IMPLEMENTATION_REVIEW actually approved. The
            # reviewer's own feedback still carries the value they were
            # shown -- the now-stale one -- which is exactly what makes this
            # a hard block rather than a silent re-approval of different
            # content under the old ledger entry.
            repo.commit("accidental bundle regeneration", filename="src/feature.py")
            with self.assertRaises(ws.StaleReviewContentIdError):
                ws.record_manual_implementation_review(
                    state, "wi", verdict="APPROVE", bundle_id="b-regenerated", round=1, now="t2",
                    current_review_content_id="c-regenerated",
                    feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                    feedback_review_content_id="c-original",
                )
            # No regeneration-tolerant fallback exists: the work item is left
            # exactly where it was, still awaiting a genuine, matching manual
            # round.
            self.assertEqual(state["work_items"]["wi"]["phase"], "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW")
            self.assertIsNone(
                state["work_items"]["wi"]["implementation_review_stages"]["MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"]
            )

    def test_recomputed_fresh_regeneration_is_hard_blocked_via_the_ledger_check(self):
        """Missing-tests item (O1, round 4): the other half of the
        no-regeneration rule -- the operator's own recomputed-fresh
        `review_content_id` can agree with the reviewer's feedback-carried
        one (no `StaleReviewContentIdError`) while *both* disagree with the
        ledger's own recorded `review_content_id`, because the bundle
        regenerated after `LOCAL_MODEL_IMPLEMENTATION_REVIEW`'s own
        approval and the reviewer was handed -- and reviewed against -- the
        new content throughout. This falls through to the restated-invariant
        check instead, raising `MissingLocalApprovalForManualImplementation
        StageError`, not `StaleReviewContentIdError` -- the exception
        `docs/ai-workflow/REVIEW_PROTOCOL.md`'s no-regeneration section now
        names for this half."""
        with ScratchRepo() as repo:
            repo.commit("implement checkpoint", filename="src/feature.py")
            state = _base_state(wi=self._locally_approved_wi(repo))
            with self.assertRaises(ws.MissingLocalApprovalForManualImplementationStageError):
                ws.record_manual_implementation_review(
                    state, "wi", verdict="APPROVE", bundle_id="b-regenerated", round=1, now="t2",
                    current_review_content_id="c-regenerated",
                    feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                    feedback_review_content_id="c-regenerated",
                )


class TestBundleGenerationTargetPhaseResolver(unittest.TestCase):
    """`bundle_generation_target_phase`/`bundle_generation_recovered_role_
    legal_committed_phases` -- both version-dependent resolvers CP3
    introduces, tested directly (independent of `record_bundle_generation`/
    `validate_bundle_generation_record_commit`, which merely call them)."""

    def test_target_phase_per_version_and_stage(self):
        for version in ("1", "2.1", None):
            for stage in ("implementation", "post-fix"):
                with self.subTest(version=version, stage=stage):
                    self.assertEqual(
                        ws.bundle_generation_target_phase(stage, version),
                        "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                    )
        for stage in ("implementation", "post-fix"):
            with self.subTest(stage=stage):
                self.assertEqual(
                    ws.bundle_generation_target_phase(stage, "2.2"),
                    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                )

    def test_target_phase_rejects_unknown_stage(self):
        with self.assertRaises(ws.InvalidBundleGenerationStageError):
            ws.bundle_generation_target_phase("plan", "2.2")

    def test_recovered_role_legal_committed_phases_per_version(self):
        for version in ("1", "2.1", None):
            with self.subTest(version=version):
                self.assertEqual(
                    ws.bundle_generation_recovered_role_legal_committed_phases(version),
                    frozenset({"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"}),
                )
        self.assertEqual(
            ws.bundle_generation_recovered_role_legal_committed_phases("2.2"),
            frozenset({
                "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            }),
        )

    def test_recovered_role_legal_committed_phases_is_a_subset_of_legal_source_phases(self):
        """The stated invariant: for every version, the recovered-role
        committed-phase set is a subset of the additively-widened
        RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES."""
        for version in ("1", "2.1", "2.2", None):
            with self.subTest(version=version):
                self.assertTrue(
                    ws.bundle_generation_recovered_role_legal_committed_phases(version)
                    <= ws.RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES,
                )


class TestTechnicalApprovalGateReachableImplementationReviewWidening(unittest.TestCase):
    """workflow-2.5.0 CP3: `technical_approval_gate_reachable` widened
    exactly like `plan_approval_gate_reachable` already is, mirroring
    `TestTwoStagePlanReviewVersionsInheritance.test_plan_approval_gate_
    reachable_per_version`."""

    def _reachable(self, **overrides):
        kwargs = dict(
            latest_round_status="APPROVE", protected_path_dirty=False,
            head_matches_reviewed_implementation_head=True,
            # round 7's I1: these three are required keyword-only
            # parameters now (no longer defaulted to None inside the
            # function itself) -- this helper still supplies its own
            # `None` defaults so most test cases below only need to
            # override the ones they care about, but every actual call
            # this helper makes states all three explicitly.
            governing_workflow_version=None, implementation_review_stages=None,
            current_review_content_id=None,
        )
        kwargs.update(overrides)
        return ws.technical_approval_gate_reachable(**kwargs)

    def test_absent_and_1_and_2_1_ignore_the_ledger(self):
        for version in (None, "1", "2.1"):
            with self.subTest(version=version):
                self.assertTrue(self._reachable(
                    governing_workflow_version=version, implementation_review_stages=None,
                ))

    def test_2_2_requires_both_stages_approved_against_current_content_id(self):
        stages = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
            ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
        }
        self.assertTrue(self._reachable(
            governing_workflow_version="2.2", implementation_review_stages=stages,
            current_review_content_id="c1",
        ))
        self.assertFalse(self._reachable(
            governing_workflow_version="2.2", implementation_review_stages=None,
            current_review_content_id="c1",
        ))
        self.assertFalse(self._reachable(
            governing_workflow_version="2.2", implementation_review_stages=stages,
            current_review_content_id="stale",
        ))

    def test_2_2_still_honors_dirty_path_head_mismatch_and_pinned_block(self):
        stages = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
            ws.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: {"bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t"},
        }
        self.assertFalse(self._reachable(
            protected_path_dirty=True, governing_workflow_version="2.2",
            implementation_review_stages=stages, current_review_content_id="c1",
        ))
        self.assertFalse(self._reachable(
            head_matches_reviewed_implementation_head=False, governing_workflow_version="2.2",
            implementation_review_stages=stages, current_review_content_id="c1",
        ))
        self.assertFalse(self._reachable(
            pinned_block=True, governing_workflow_version="2.2",
            implementation_review_stages=stages, current_review_content_id="c1",
        ))

    def test_explicit_none_for_the_three_new_parameters_matches_pre_cp3(self):
        """Passing `None` explicitly for all three new parameters (a `"1"`/
        `"2.1"` item's actual call shape) behaves exactly like pre-CP3
        `technical_approval_gate_reachable`, which never had these
        parameters at all."""
        self.assertTrue(self._reachable())
        self.assertFalse(self._reachable(protected_path_dirty=True))

    def test_omitting_governing_workflow_version_now_raises_instead_of_silently_opening_the_gate(self):
        """workflow-2.5.0 REVISE round 7's own `I1`: before this round, a
        caller that omitted `governing_workflow_version` (its old default
        was `None`) fell through the `!= "2.2"` branch and returned `True`
        with the `"2.2"` ledger never consulted -- fail *open*, reachable
        in practice by a caller that correctly passed
        `implementation_review_stages`/`current_review_content_id` for a
        real `"2.2"` item but simply forgot the version kwarg. Removing the
        default turns that omission into an immediate `TypeError`, for
        every caller, not merely the one this repository ships
        (`approve-review.md`, corrected separately this round)."""
        with self.assertRaises(TypeError):
            ws.technical_approval_gate_reachable(
                latest_round_status="APPROVE", protected_path_dirty=False,
                head_matches_reviewed_implementation_head=True,
                implementation_review_stages=None, current_review_content_id="c1",
            )

    def test_omitting_implementation_review_stages_or_review_content_id_also_raises(self):
        """The same structural guarantee for the other two: `plan_approval_
        gate_reachable`'s own three widening parameters are all required,
        and this function now mirrors that exactly, not just for the one
        parameter round 7's reproduction happened to omit."""
        with self.assertRaises(TypeError):
            ws.technical_approval_gate_reachable(
                latest_round_status="APPROVE", protected_path_dirty=False,
                head_matches_reviewed_implementation_head=True,
                governing_workflow_version="2.2", current_review_content_id="c1",
            )
        with self.assertRaises(TypeError):
            ws.technical_approval_gate_reachable(
                latest_round_status="APPROVE", protected_path_dirty=False,
                head_matches_reviewed_implementation_head=True,
                governing_workflow_version="2.2", implementation_review_stages=None,
            )

    def test_a_22_call_that_forgets_only_the_version_kwarg_no_longer_silently_approves(self):
        """Round 7's own reproduction, reasserted directly: a call for a
        real `"2.2"` item's ledger state that passes `implementation_review_
        stages`/`current_review_content_id` correctly but omits
        `governing_workflow_version` must never again return `True` --
        before this fix it did, with the ledger below plainly incomplete
        (no `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` entry at all)."""
        incomplete_stages = {
            "review_content_id": "c1",
            ws.LOCAL_MODEL_IMPLEMENTATION_REVIEW: {
                "bundle_id": "b", "verdict": "APPROVE", "round": 1, "completed_at": "t",
            },
        }
        with self.assertRaises(TypeError):
            ws.technical_approval_gate_reachable(
                latest_round_status="APPROVE", protected_path_dirty=False,
                head_matches_reviewed_implementation_head=True,
                implementation_review_stages=incomplete_stages,
                current_review_content_id="c1",
            )


class TestRecordBundleGenerationImplementationReviewTargetPhase(unittest.TestCase):
    """`record_bundle_generation` reaches `AWAITING_LOCAL_IMPLEMENTATION_
    REVIEW` for a "2.2" item, at both bundle-generation stages -- the
    resolver's own sole call site."""

    def test_first_implementation_stage_call_for_2_2_reaches_local_review(self):
        state = _base_state(wi=_base_work_item(
            governing_workflow_version="2.2", phase="SELF_REVIEWING_IMPLEMENTATION",
            reviewed_implementation_head=None, implementation_revision=None,
        ))
        new_state = ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
        wi = new_state["work_items"]["wi"]
        self.assertEqual(wi["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
        self.assertEqual(wi["reviewed_implementation_head"], "abc123")
        self.assertEqual(wi["implementation_revision"], 1)

    def test_post_fix_call_for_2_2_reaches_local_review(self):
        state = _base_state(wi=_base_work_item(
            governing_workflow_version="2.2", phase="APPLYING_REVIEW_FEEDBACK",
            reviewed_implementation_head="abc123", implementation_revision=1,
        ))
        new_state = ws.record_bundle_generation(state, "wi", stage="post-fix", head="def456", now="t2")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

    def test_post_fix_from_functional_review_bounded_fix_for_2_2_reaches_local_review(self):
        """A "2.2" item's functional-review bounded fix re-enters both
        implementation-review stages -- the resolver's target for
        stage="post-fix" from AWAITING_FUNCTIONAL_REVIEW is identical to
        every other post-fix source, never a special case."""
        state = _base_state(wi=_base_work_item(
            governing_workflow_version="2.2", phase="AWAITING_FUNCTIONAL_REVIEW",
            reviewed_implementation_head="abc123", implementation_revision=1,
            technical_approval={"status": "STALE"},
        ))
        new_state = ws.record_bundle_generation(state, "wi", stage="post-fix", head="def456", now="t2")
        self.assertEqual(new_state["work_items"]["wi"]["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")

    def test_1_and_2_1_are_unaffected(self):
        for version in ("1", "2.1"):
            with self.subTest(version=version):
                state = _base_state(wi=_base_work_item(
                    governing_workflow_version=version, phase="SELF_REVIEWING_IMPLEMENTATION",
                ))
                new_state = ws.record_bundle_generation(state, "wi", stage="implementation", head="abc123", now="t1")
                self.assertEqual(
                    new_state["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                )


class TestValidateBundleGenerationRecordCommitVersionDependence(unittest.TestCase):
    """`validate_bundle_generation_record_commit`'s own version-dependent
    target-phase check and recovered-role membership test, exercised
    end-to-end against a real Git history -- mirroring
    `TestRecordBundleGeneration.test_end_to_end_implementation_round_
    reaches_external_review_durably`, substituted per version."""

    def test_ordinary_role_2_2_end_to_end(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, "wi")
            _commit_state_only(repo, "wi", {
                "work_item_id": "wi", "governing_workflow_version": "2.2",
                "reviewed_implementation_head": None, "implementation_revision": 0,
                "phase": "IMPLEMENTING", "state_revision": 0, "last_transition": "t0",
            }, "seed base state")
            p = repo.commit("protected fix", filename="src/Foo.kt")
            pre_state = _base_state(wi={
                "work_item_id": "wi", "governing_workflow_version": "2.2",
                "reviewed_implementation_head": None, "implementation_revision": None,
                "phase": "SELF_REVIEWING_IMPLEMENTATION", "state_revision": 0, "last_transition": "t0",
            })
            post_state = ws.record_bundle_generation(
                pre_state, "wi", stage="implementation", head=p, now="t1",
            )
            wi_after = post_state["work_items"]["wi"]
            self.assertEqual(wi_after["phase"], "AWAITING_LOCAL_IMPLEMENTATION_REVIEW")
            s = _commit_state_only(
                repo, "wi", wi_after, "record gen",
                trailers=_record_trailers("wi", wi_after["implementation_revision"]),
            )
            ws.validate_bundle_generation_record_commit(repo.root, s, "wi")  # must not raise

    def test_ordinary_role_2_2_wrong_target_phase_rejected(self):
        """A commit that hand-writes AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW
        (the "1"/"2.1" target) for a "2.2" item is malformed -- the ordinary
        role's required target is version-dependent, not a bare literal."""
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, "wi")
            _commit_state_only(repo, "wi", {
                "work_item_id": "wi", "governing_workflow_version": "2.2",
                "reviewed_implementation_head": None, "implementation_revision": 0,
                "phase": "IMPLEMENTING", "state_revision": 0, "last_transition": "t0",
            }, "seed base state")
            wi_after = {
                "work_item_id": "wi", "governing_workflow_version": "2.2",
                "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
                "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "state_revision": 1, "last_transition": "t1",
            }
            s = _commit_state_only(
                repo, "wi", wi_after, "record gen", trailers=_record_trailers("wi", 1),
            )
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
                ws.validate_bundle_generation_record_commit(repo.root, s, "wi")

    def test_recovered_role_2_2_accepted_from_each_of_the_three_legal_committed_phases(self):
        """B1(b)/B2/round-4 finding B1: a recovered-role commit landing at
        any of the three phases a "2.2" item can occupy between T and
        approval passes -- the membership test, not a single-valued
        equality."""
        for target_phase in (
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        ):
            with self.subTest(target_phase=target_phase):
                with ScratchRepo() as repo:
                    _write_test_artifacts_declaration(repo, "wi")
                    base_wi = {
                        "work_item_id": "wi", "governing_workflow_version": "2.2",
                        "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
                        "phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                        "state_revision": 1, "last_transition": "t1",
                    }
                    ordinary = _commit_state_only(
                        repo, "wi", base_wi, "record gen", trailers=_record_trailers("wi", 1),
                    )
                    recovered_wi = dict(base_wi, phase=target_phase, state_revision=2, last_transition="t2")
                    recovered = _commit_state_only(
                        repo, "wi", recovered_wi, "recover gen",
                        trailers=_record_trailers("wi", 1) | {"Workflow-Supersedes": ordinary},
                    )
                    ws.validate_bundle_generation_record_commit(repo.root, recovered, "wi")  # must not raise

    def test_recovered_role_2_2_rejects_a_phase_outside_the_three_legal_ones(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, "wi")
            base_wi = {
                "work_item_id": "wi", "governing_workflow_version": "2.2",
                "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
                "phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                "state_revision": 1, "last_transition": "t1",
            }
            ordinary = _commit_state_only(
                repo, "wi", base_wi, "record gen", trailers=_record_trailers("wi", 1),
            )
            recovered_wi = dict(base_wi, phase="APPLYING_REVIEW_FEEDBACK", state_revision=2, last_transition="t2")
            recovered = _commit_state_only(
                repo, "wi", recovered_wi, "recover gen",
                trailers=_record_trailers("wi", 1) | {"Workflow-Supersedes": ordinary},
            )
            with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
                ws.validate_bundle_generation_record_commit(repo.root, recovered, "wi")

    def test_1_and_2_1_negative_case_unaffected_by_the_widened_sets(self):
        """The widened "2.2" sets change no "1"/"2.1" recovery refusal:
        a recovered-role commit at AWAITING_LOCAL_IMPLEMENTATION_REVIEW
        (a "2.2"-only phase) is still rejected for a "1"/"2.1" item."""
        for version in ("1", "2.1"):
            with self.subTest(version=version):
                with ScratchRepo() as repo:
                    _write_test_artifacts_declaration(repo, "wi")
                    base_wi = {
                        "work_item_id": "wi", "governing_workflow_version": version,
                        "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
                        "phase": "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
                        "state_revision": 1, "last_transition": "t1",
                    }
                    ordinary = _commit_state_only(
                        repo, "wi", base_wi, "record gen", trailers=_record_trailers("wi", 1),
                    )
                    recovered_wi = dict(
                        base_wi, phase="AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                        state_revision=2, last_transition="t2",
                    )
                    recovered = _commit_state_only(
                        repo, "wi", recovered_wi, "recover gen",
                        trailers=_record_trailers("wi", 1) | {"Workflow-Supersedes": ordinary},
                    )
                    with self.assertRaises(ws.MalformedBundleGenerationRecordCommitError):
                        ws.validate_bundle_generation_record_commit(repo.root, recovered, "wi")


class TestImplementationProvenanceRecoveryWidenedForV2_2(unittest.TestCase):
    """`verify_implementation_provenance_recovery`/`apply_implementation_
    provenance_recovery`'s own phase guard, widened to the three phases a
    "2.2" item can occupy between T and approval."""

    def test_apply_recovery_accepted_from_each_of_the_three_legal_phases(self):
        for phase in (
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        ):
            with self.subTest(phase=phase):
                state = _base_state(wi={
                    "work_item_id": "wi", "governing_workflow_version": "2.2", "phase": phase,
                    "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
                    "state_revision": 3, "last_transition": "t3",
                })
                new_state = ws.apply_implementation_provenance_recovery(state, "wi", now="t4")
                work_item = new_state["work_items"]["wi"]
                self.assertEqual(work_item["phase"], phase)
                self.assertEqual(work_item["state_revision"], 4)

    def test_apply_recovery_still_refuses_a_phase_outside_the_three(self):
        state = _base_state(wi={
            "work_item_id": "wi", "governing_workflow_version": "2.2",
            "phase": "APPLYING_REVIEW_FEEDBACK",
            "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
            "state_revision": 3, "last_transition": "t3",
        })
        with self.assertRaises(ws.IllegalImplementationProvenanceRecoverySourcePhaseError):
            ws.apply_implementation_provenance_recovery(state, "wi", now="t4")

    def test_1_and_2_1_still_admit_only_the_single_terminal_phase(self):
        for version in ("1", "2.1", None):
            with self.subTest(version=version):
                state = _base_state(wi={
                    "work_item_id": "wi", "governing_workflow_version": version,
                    "phase": "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
                    "reviewed_implementation_head": "p" * 40, "implementation_revision": 1,
                    "state_revision": 3, "last_transition": "t3",
                })
                with self.assertRaises(ws.IllegalImplementationProvenanceRecoverySourcePhaseError):
                    ws.apply_implementation_provenance_recovery(state, "wi", now="t4")


class TestProvenanceIntervalUnaffectedByReviewStageLedgerWrites(unittest.TestCase):
    """A unit-level pin of D-Implementation-Review-Stages' own "Provenance-
    interval interaction" claim: `implementation_provenance_interval_
    reachable`'s HEAD == T requirement is unaffected by a local-APPROVE +
    manual-APPROVE `implementation_review_stages` ledger-write sequence for
    a "2.2" item -- neither writer ever creates a commit, so live HEAD
    never moves past T while the ledger fills in."""

    def test_head_still_equals_t_after_both_ledger_writes(self):
        with ScratchRepo() as repo:
            _write_test_artifacts_declaration(repo, "wi")
            _commit_state_only(repo, "wi", {
                "work_item_id": "wi", "governing_workflow_version": "2.2",
                "reviewed_implementation_head": None, "implementation_revision": 0,
                "phase": "IMPLEMENTING", "state_revision": 0, "last_transition": "t0",
            }, "seed base state")
            p = repo.commit("protected fix", filename="src/Foo.kt")
            pre_state = _base_state(wi={
                "work_item_id": "wi", "governing_workflow_version": "2.2",
                "reviewed_implementation_head": None, "implementation_revision": None,
                "phase": "SELF_REVIEWING_IMPLEMENTATION", "state_revision": 0, "last_transition": "t0",
            })
            post_state = ws.record_bundle_generation(
                pre_state, "wi", stage="implementation", head=p, now="t1",
            )
            wi_after_generation = post_state["work_items"]["wi"]
            t = _commit_state_only(
                repo, "wi", wi_after_generation, "record gen",
                trailers=_record_trailers("wi", wi_after_generation["implementation_revision"]),
            )
            work_item = wi_after_generation | {"work_item_id": "wi"}
            self.assertTrue(
                ws.implementation_provenance_interval_reachable(repo.root, work_item, base_commit=repo.base),
            )
            self.assertEqual(ws._run(["git", "rev-parse", "HEAD"], cwd=repo.root).strip(), t)

            local_approved = ws.record_local_implementation_review(
                post_state, "wi", verdict="APPROVE", bundle_id="b1",
                review_content_id="c1", round=1, now="t2",
            )
            # Uncommitted -- HEAD must still be exactly t.
            self.assertEqual(ws._run(["git", "rev-parse", "HEAD"], cwd=repo.root).strip(), t)
            manual_approved = ws.record_manual_implementation_review(
                local_approved, "wi", verdict="APPROVE", bundle_id="b2", round=1, now="t3",
                current_review_content_id="c1", feedback_role="MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                feedback_review_content_id="c1",
            )
            self.assertEqual(ws._run(["git", "rev-parse", "HEAD"], cwd=repo.root).strip(), t)
            self.assertEqual(
                manual_approved["work_items"]["wi"]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            )
            # implementation_provenance_interval_reachable still holds --
            # it reads reviewed_implementation_head/implementation_revision
            # (both untouched by either ledger writer) and live HEAD (also
            # untouched), never the ledger itself.
            self.assertTrue(
                ws.implementation_provenance_interval_reachable(repo.root, work_item, base_commit=repo.base),
            )


class TestPromoteLegacyWorkItemDestinationLiteral(unittest.TestCase):
    """workflow-2.5.0 (resolves `LOCAL_MODEL_PLAN_REVIEW` round 6, optional
    finding 1; regression added at revision 16, round 15, missing test 2):
    `promote_legacy_work_item` always promotes to the literal `"2.1"`,
    never `config["default_workflow_version"]`, even once a repository has
    separately activated `"2.2"` as its own current default -- the
    adopted item is already past both implementation-review stages, so
    there is no future round left for it to satisfy that obligation in."""

    def test_promotes_to_2_1_literal_even_when_2_2_is_the_current_default(self):
        with ScratchRepo() as repo:
            _run(["git", "commit", "-q", "--allow-empty", "-m", "legacy work"], cwd=repo.root)
            legacy_commit = repo.head()
            _write_and_commit(repo, "docs/ACTIVE_MILESTONE.md", "integrated legacy work\n", "narrative")
            technical_approval = ws.build_approval_record(
                basis="LEGACY_V1", stage="implementation", user_confirmation="legacy import",
                now="t0", reviewed_content_commit=legacy_commit,
                legacy_evidence={"note": "pre-Workflow"}, waived_guarantees=["no_bundle_id", "no_telemetry"],
            )
            wi = _base_work_item(
                work_item_id="legacy-wi", governing_workflow_version="1", phase="LEGACY_READY",
                technical_approval=technical_approval,
            )
            state = _base_state(**{"legacy-wi": wi})
            _write_test_artifacts_declaration(repo, "legacy-wi", protected_prefixes=["app/"], excluded_prefixes=["docs/"])
            artifacts_path = fingerprint.artifacts_path_for_work_item("legacy-wi")
            # This repository has separately activated "2.2" as its own
            # current default -- irrelevant to promote_legacy_work_item,
            # which never reads WORKFLOW_CONFIG.json at all.
            new_state = ws.promote_legacy_work_item(
                state, repo.root, work_item_id="legacy-wi",
                required_active_milestone_substring="integrated legacy work",
                artifacts_path=artifacts_path, now="t1",
            )
            promoted = new_state["work_items"]["legacy-wi"]
            self.assertEqual(promoted["governing_workflow_version"], "2.1")
            self.assertEqual(promoted["phase"], "AWAITING_FUNCTIONAL_REVIEW")


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP6: D-Review-Material-Lifecycle -- unit classification,
# the marker-presence obligation, the narrative-content guarantee, the
# marking pass, and the governing-version enumeration sweep. Reuses CP1's
# canonical render_marker/parse_marker throughout -- never a second,
# independently-derived grammar.
# ---------------------------------------------------------------------------


class ReviewMaterialLifecycleClassificationTest(unittest.TestCase):
    """Guarantees (i)-(vi): explicit marker precedence, fail-closed
    default, non-cascading nesting, and the narrative-content guarantee's
    own scope."""

    def test_positive_explicit_current_stays_review_visible(self):
        text = "# Doc\n\n" + ws.render_marker("CURRENT") + "\n\nBody text.\n"
        unit = ws.document_level_unit(text)
        self.assertEqual(ws.unit_state(unit), ("CURRENT", True))

    def test_negative_explicit_historical_excluded(self):
        text = "## Section\n\n" + ws.render_marker("HISTORICAL") + "\n\nOld stuff.\n"
        unit = ws.parse_markdown_units(text)[0]
        self.assertEqual(ws.unit_state(unit), ("HISTORICAL", True))

    def test_ambiguous_unmarked_fails_closed_to_current(self):
        text = "## Section\n\nNo marker here at all.\n"
        unit = ws.parse_markdown_units(text)[0]
        self.assertEqual(ws.unit_state(unit), ("CURRENT", False))

    def test_ambiguous_malformed_marker_fails_closed_to_current(self):
        text = "## Section\n\n<!-- review-material-lifecycle: WEIRD -->\n\nBody.\n"
        unit = ws.parse_markdown_units(text)[0]
        self.assertEqual(ws.unit_state(unit), ("CURRENT", False))

    def test_two_version_transition_non_marker_edit_leaves_classification_unchanged(self):
        v1 = "## Original Heading\n\nSome prose.\n"
        v2 = "## Reworded Heading\n\nSome reworded prose, same material.\n"
        self.assertEqual(
            ws.unit_state(ws.parse_markdown_units(v1)[0])[0],
            ws.unit_state(ws.parse_markdown_units(v2)[0])[0],
        )

    def test_two_version_transition_marker_only_edit_performs_transition(self):
        v1 = "## Heading\n\nProse.\n"
        v2 = "## Heading\n\n" + ws.render_marker("HISTORICAL") + "\n\nProse.\n"
        self.assertEqual(ws.unit_state(ws.parse_markdown_units(v1)[0])[0], "CURRENT")
        self.assertEqual(ws.unit_state(ws.parse_markdown_units(v2)[0])[0], "HISTORICAL")

    def test_boundary_redrawing_merge_without_marker_edit_does_not_promote_to_historical(self):
        # A CURRENT unit and an adjacent HISTORICAL-marked unit, merged by
        # demoting/deleting the heading between them with no marker edit
        # anywhere in the diff, must not report the merged material
        # HISTORICAL.
        before = (
            "## Current section\n\nCurrent prose.\n\n"
            "## Historical section\n\n" + ws.render_marker("HISTORICAL") + "\n\nOld prose.\n"
        )
        before_units = ws.parse_markdown_units(before)
        self.assertEqual(ws.unit_state(before_units[0])[0], "CURRENT")
        self.assertEqual(ws.unit_state(before_units[1])[0], "HISTORICAL")

        # The merge: demote the boundary heading to plain prose, touching
        # no marker anywhere in the diff.
        after = (
            "## Current section\n\nCurrent prose.\n\n"
            "Historical section (no longer its own heading)\n\n"
            + ws.render_marker("HISTORICAL") + "\n\nOld prose.\n"
        )
        after_units = ws.parse_markdown_units(after)
        self.assertEqual(len(after_units), 1)
        # The merged unit's own marker is no longer its own_text's first
        # non-blank line (the former CURRENT prose and the demoted heading
        # text now precede it) -- so the merge falls to the fail-closed
        # CURRENT default rather than silently inheriting a marker deeper
        # in its own text. This is the mechanical basis for the guarantee:
        # a merge can never *promote* material to HISTORICAL without an
        # explicit marker edit of its own.
        self.assertEqual(ws.unit_state(after_units[0]), ("CURRENT", False))

    def test_narrative_location_forbidden_inside_current_fails(self):
        text = (
            "### D-Something\n\n" + ws.render_marker("CURRENT") + "\n\n"
            "Corrected at revision 5, finding B1: the design now does X.\n"
        )
        unit = ws.parse_markdown_units(text)[0]
        self.assertTrue(ws.check_narrative_content(unit))

    def test_narrative_location_identical_narrative_inside_historical_passes(self):
        text = (
            "### D-Something\n\n" + ws.render_marker("HISTORICAL") + "\n\n"
            "Corrected at revision 5, finding B1: the design now does X.\n"
        )
        unit = ws.parse_markdown_units(text)[0]
        self.assertEqual(ws.check_narrative_content(unit), [])

    def test_scope_unmarked_pre_existing_section_with_forbidden_narrative_passes(self):
        # CURRENT only by the fail-closed default (no marker at all) is
        # outside guarantee (vi)'s reach.
        text = "### D-PreExisting\n\nCorrected at revision 5, finding B1: the design now does X.\n"
        unit = ws.parse_markdown_units(text)[0]
        self.assertEqual(ws.check_narrative_content(unit), [])

    def test_already_marked_nested_unit_survives_and_is_not_folded_into_parent(self):
        text = (
            "### D-Container\n\n" + ws.render_marker("CURRENT") + "\n\n"
            "Current prose with no forbidden narrative.\n\n"
            "#### Disposition record\n\n" + ws.render_marker("HISTORICAL") + "\n\n"
            "Corrected at revision 5, finding B1: history here.\n"
        )
        units = ws.parse_markdown_units(text)
        container, nested = units[0], units[1]
        self.assertEqual(ws.unit_state(container), ("CURRENT", True))
        self.assertEqual(ws.unit_state(nested), ("HISTORICAL", True))
        # A separately, explicitly marked descendant is never folded into
        # its container's own guarantee-(vi) walk.
        self.assertEqual(ws.check_narrative_content(container), [])


class ReviewMaterialLifecycleMarkerPresenceTest(unittest.TestCase):
    def test_marker_presence_fixture_in_scope_unit_missing_fails(self):
        text = "### D-InScope\n\nNo marker.\n"
        self.assertEqual(
            ws.check_marker_presence_plan_sections(text, ("### D-InScope",)), ["### D-InScope"],
        )

    def test_marker_presence_unit_elsewhere_in_same_document_unaffected(self):
        text = (
            "### D-InScope\n\n" + ws.render_marker("CURRENT") + "\n\nBody.\n\n"
            "### D-OutOfScope\n\nNo marker here, not a named subject.\n"
        )
        self.assertEqual(ws.check_marker_presence_plan_sections(text, ("### D-InScope",)), [])

    def test_whole_document_marker_presence_fails_when_absent(self):
        text = "# Title\n\nNo marker anywhere in the intro.\n\n## Section\n\nBody.\n"
        self.assertFalse(ws.check_marker_presence_whole_document(text))

    def test_whole_document_marker_presence_passes_when_present(self):
        text = "# Title\n\n" + ws.render_marker("CURRENT") + "\n\nIntro.\n\n## Section\n\nBody.\n"
        self.assertTrue(ws.check_marker_presence_whole_document(text))

    def test_already_marked_nested_unit_discharges_its_own_presence_obligation(self):
        text = (
            "### D-Container\n\n" + ws.render_marker("CURRENT") + "\n\nProse.\n\n"
            "#### Disposition\n\n" + ws.render_marker("HISTORICAL") + "\n\nHistory.\n"
        )
        self.assertEqual(ws.check_marker_presence_plan_sections(text, ("### D-Container",)), [])


class ReviewMaterialLifecycleMarkingPassTest(unittest.TestCase):
    def test_marks_missing_units_current_and_never_touches_already_marked(self):
        text = (
            "### D-Unmarked\n\nProse one.\n\n"
            "### D-AlreadyMarked\n\n" + ws.render_marker("HISTORICAL") + "\n\nProse two.\n"
        )
        names = ("### D-Unmarked", "### D-AlreadyMarked")
        result = ws.mark_missing_units_current(text, names)
        units = ws.parse_markdown_units(result)
        self.assertEqual(ws.unit_state(units[0]), ("CURRENT", True))
        self.assertEqual(ws.unit_state(units[1]), ("HISTORICAL", True))
        # Idempotent: a second pass changes nothing further.
        self.assertEqual(ws.mark_missing_units_current(result, names), result)


class ReviewMaterialLifecyclePreMarkingPartitionFixtureTest(unittest.TestCase):
    """A corpus-shaped fixture proving guarantee (v)'s classification
    partition: only the deliberately, visibly marked unit is HISTORICAL;
    every unmarked unit stays CURRENT. Fixture-based (not a one-shot
    real-corpus read) so it keeps catching a future edit that re-widens
    guarantee (v) even once this repository's own real corpus has moved
    past its own pre-marking moment."""

    FIXTURE = (
        "# Fixture corpus\n\n"
        "## Marked Historical Section\n\n" + ws.render_marker("HISTORICAL") + "\n\nOld.\n\n"
        "## Unmarked Section One\n\nCurrent by default.\n\n"
        "## Unmarked Section Two\n\nAlso current by default.\n"
    )

    def test_classification_partition_matches_expected(self):
        units = ws.parse_markdown_units(self.FIXTURE)
        states = {u.heading: ws.unit_state(u)[0] for u in units}
        self.assertEqual(states["Marked Historical Section"], "HISTORICAL")
        self.assertEqual(states["Unmarked Section One"], "CURRENT")
        self.assertEqual(states["Unmarked Section Two"], "CURRENT")

    def test_mutated_copy_claiming_every_unit_current_including_marked_fails(self):
        units = ws.parse_markdown_units(self.FIXTURE)
        states = {u.heading: ws.unit_state(u)[0] for u in units}
        with self.assertRaises(AssertionError):
            for state in states.values():
                self.assertEqual(state, "CURRENT")


class ReviewMaterialLifecycleRealCorpusTest(unittest.TestCase):
    """Guarantee (vii)'s real-corpus run of the finished marker-presence
    check over every one of that obligation's actual subjects, plus the
    narrative-content guarantee over the same named sections."""

    def _read(self, relative_path):
        overlay_root = Path(__file__).resolve().parent
        return (overlay_root.parent / "docs" / "ai-workflow" / relative_path).read_text()

    PLAN_SECTION_NAMES = (
        "### D-Implementation-Review-Stages",
        "### D-Implementation-Review-Version-Activation",
        "### D-Review-Material-Lifecycle",
    )

    def test_implementation_review_workflow_carries_its_own_marker(self):
        text = self._read("IMPLEMENTATION_REVIEW_WORKFLOW.md")
        self.assertTrue(ws.check_marker_presence_whole_document(text))

    def test_workflow_v2_plan_named_sections_all_carry_markers(self):
        text = self._read("WORKFLOW_V2_PLAN.md")
        self.assertEqual(ws.check_marker_presence_plan_sections(text, self.PLAN_SECTION_NAMES), [])

    def test_named_sections_carry_no_forbidden_narrative(self):
        text = self._read("WORKFLOW_V2_PLAN.md")
        units = ws.parse_markdown_units(text)
        resolved = ws.find_named_top_level_units(units, self.PLAN_SECTION_NAMES)
        for name, unit in resolved.items():
            self.assertEqual(ws.check_narrative_content(unit), [], name)

    def test_disposition_record_subsection_survived_untouched_as_historical(self):
        text = self._read("WORKFLOW_V2_PLAN.md")
        units = ws.parse_markdown_units(text)
        disposition = [u for u in units if u.heading.startswith("2.5.0 disposition record")]
        self.assertEqual(len(disposition), 1)
        self.assertEqual(ws.unit_state(disposition[0]), ("HISTORICAL", True))


class GoverningVersionEnumerationSweepTest(unittest.TestCase):
    def test_exhaustive_enumeration_form_flagged(self):
        text = (
            'For plan review, only work items with governing_workflow_version '
            '"1" or "2.1" use the single-stage flow.'
        )
        findings = ws.find_governing_version_occurrences("doc.md", text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].form, "exhaustive_enumeration")

    def test_bare_21_scoped_form_flagged(self):
        text = 'The two-stage plan review protocol is scoped entirely to "2.1" work items.'
        findings = ws.find_governing_version_occurrences("doc.md", text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].form, "bare_21_scoped")

    def test_negated_version_independence_assertion_not_flagged(self):
        text = 'Plan review is not a "2.1"-only mechanism; "2.2" items use it identically.'
        self.assertEqual(ws.find_governing_version_occurrences("doc.md", text), [])

    def test_widened_form_mentioning_both_versions_not_flagged(self):
        text = 'Plan review applies to work items whose governing version is "2.1"/"2.2" only.'
        self.assertEqual(ws.find_governing_version_occurrences("doc.md", text), [])

    def test_occurrence_inside_historical_unit_is_allowlisted(self):
        text = (
            "## Old plan review disposition\n\n" + ws.render_marker("HISTORICAL") + "\n\n"
            'At the time, plan review was scoped entirely to "2.1" work items.\n'
        )
        self.assertEqual(ws.find_governing_version_occurrences("doc.md", text), [])

    def test_allowlist_pin_negative_fixture_new_current_section_still_flagged(self):
        # Even though the document's own historical sections stay exempt,
        # a stale claim inside a *new*, non-HISTORICAL current design
        # section must still fail -- proving the allowlist is
        # occurrence-scoped, never a whole-document grant.
        text = (
            "## Old plan review disposition\n\n" + ws.render_marker("HISTORICAL") + "\n\n"
            'At the time, plan review was scoped entirely to "2.1" work items.\n\n'
            "## New plan review design\n\n" + ws.render_marker("CURRENT") + "\n\n"
            'This plan review mechanism is scoped entirely to "2.1" work items.\n'
        )
        findings = ws.find_governing_version_occurrences("doc.md", text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].form, "bare_21_scoped")
        self.assertIn("New plan review design", text[:findings[0].offset].rsplit("##", 1)[-1] or "")

    def test_whole_document_allowlist_for_named_history_documents(self):
        text = 'Plan review is scoped entirely to "2.1" work items.'
        findings = ws.find_governing_version_occurrences(
            "docs/ai-workflow/WORKFLOW_V2_3_PLAN.md", text,
        )
        self.assertEqual(findings, [])

    def test_positive_fixture_negated_assertion_only_occurrence_passes_clean(self):
        text = 'For plan review, this is not a single-governing-version-only mechanism; "2.1" and "2.2" both use it.'
        self.assertEqual(ws.sweep_governing_version_enumeration({"doc.md": text}), [])

    def test_real_corpus_sweep_is_clean(self):
        """Deliberately scoped (round 4's O2), not exhaustive: the command
        files plus `docs/ai-workflow/`'s own top-level documents, both
        non-recursive -- the corpus this milestone's own review-facing
        prose lives in. `docs/ai-workflow/audit/`, `dry-run/`, and
        `requirements/` are out of this sweep's scope (a wider,
        `**/*.md`-recursive run does find one true positive there today --
        a base-2.4.0-inherited row in `audit/WORKFLOW_DEFECT_LEDGER.md`
        this milestone's own overlay does not own or replace -- which is a
        real gap, not a false negative, and is left for whichever future
        checkpoint widens this sweep's own corpus deliberately rather than
        as an accidental side effect of an unrelated fix)."""
        overlay_root = Path(__file__).resolve().parent
        payload_root = overlay_root.parent
        paths = list((payload_root / ".claude" / "commands").glob("*.md")) + \
            list((payload_root / "docs" / "ai-workflow").glob("*.md"))
        texts = {str(p): p.read_text() for p in paths}
        findings = ws.sweep_governing_version_enumeration(texts)
        self.assertEqual(findings, [], [repr(f) for f in findings])


class ApplyingReviewFeedbackVersionClaimSweepTest(unittest.TestCase):
    """workflow-2.5.0 REVISE round 7, Missing-tests item 1 / `B1`: a
    standing negative-corpus sweep for the superseded "`enter_applying_
    review_feedback` is skipped for `\"2.2\"` because of a version branch"
    claim, which has now produced a Blocking finding in rounds 3, 5, 6, and
    twice more in round 7 -- always as a fresh paraphrase or a
    Markdown-mangled substring the previous round's own point-fix and grep
    did not anticipate.

    **Round 8's own `B1`, disposition (b): kept as a regression guard, not
    widened.** This is a *closed* five-cue list against the five wordings
    known as of round 7 (`_APPLYING_REVIEW_FEEDBACK_ABSOLUTE_CUES`); it is
    not, and does not claim to be, a general detector for every future
    paraphrase of the underlying claim. Round 8 found the sweep does not
    in fact catch round 6's own three real instances --
    `test_round_6_782_row_is_not_currently_flagged_by_neighbor_suppression`
    and `test_round_6_no_separate_call_wording_is_not_currently_flagged`
    below pin the real, in-situ text and document that gap directly,
    rather than asserting coverage the mechanism does not have. Widening
    the cue set and/or the suppression-qualifier matching to actually close
    those two gaps is left to a future checkpoint that also resolves the
    now-in-corpus disposition question strengthening would raise (see
    `test_real_corpus_sweep_is_clean` below)."""

    def test_round_7_b1_instance_1_is_flagged(self):
        # WORKFLOW_V2_1_OPERATOR_REFERENCE.md:483-488 before this round's
        # fix, verbatim.
        text = (
            "so this command makes **no** `enter_applying_review_feedback` "
            "call for it at all; steps 1-8 below run identically "
            "regardless."
        )
        findings = ws.find_applying_review_feedback_version_claims("doc.md", text)
        # Two of the five absolute-cue patterns both match this single
        # passage ("no ... call" and "for it at all") -- that is fine; the
        # point is that at least one fires, not exactly which.
        self.assertGreaterEqual(len(findings), 1, [repr(f) for f in findings])

    def test_round_7_b1_instance_2_is_flagged(self):
        # WORKFLOW_V2_1_OPERATOR_REFERENCE.md:830 before this round's fix,
        # verbatim.
        text = (
            "`/apply-implementation-review` (`enter_applying_review_feedback`, "
            "`\"1\"`/`\"2.1\"` only — a `\"2.2\"` item never needs this call, "
            "per its own dual-mode note at the top of that file)"
        )
        findings = ws.find_applying_review_feedback_version_claims("doc.md", text)
        self.assertEqual(len(findings), 2, [repr(f) for f in findings])

    def test_round_6_b1_instance_is_flagged(self):
        # WORKFLOW_V2_1_OPERATOR_REFERENCE.md:782 before round 6's fix
        # (paraphrased as round 6's own disposition record describes it):
        # a bare, unqualified "a '2.2' item never reaches APPLYING_REVIEW_
        # FEEDBACK via enter_applying_review_feedback" claim, with no
        # terminal-phase-escape qualifier anywhere nearby.
        text = (
            "a `\"2.2\"` item never reaches `APPLYING_REVIEW_FEEDBACK` via "
            "`enter_applying_review_feedback`; that phase is entered "
            "directly by the review-stage writer instead, and this call is "
            "not called at all for that governing version."
        )
        findings = ws.find_applying_review_feedback_version_claims("doc.md", text)
        self.assertGreaterEqual(len(findings), 1, [repr(f) for f in findings])

    def test_the_fixed_round_7_passages_are_not_flagged(self):
        # This round's own replacement text for both instances -- proves
        # the sweep does not simply re-flag its own fix.
        overlay_root = Path(__file__).resolve().parent
        payload_root = overlay_root.parent
        text = (payload_root / "docs" / "ai-workflow" / "WORKFLOW_V2_1_OPERATOR_REFERENCE.md").read_text()
        findings = ws.find_applying_review_feedback_version_claims(
            "WORKFLOW_V2_1_OPERATOR_REFERENCE.md", text,
        )
        self.assertEqual(findings, [], [repr(f) for f in findings])

    def test_milestone_workflow_finds_phase_qualifier_is_not_flagged(self):
        # MILESTONE_WORKFLOW.md:349's already-correct passage -- named in
        # round 6's Missing-tests item as the reason a naive substring ban
        # cannot work: it states the *fixed* rule using nearly the banned
        # words ("no ... call ... for a '2.2' item"), distinguished only by
        # "since it finds `phase` already there".
        text = (
            "transitions directly to `APPLYING_REVIEW_FEEDBACK` (the "
            "command itself performs the phase write, exactly as "
            "`/review-plan`'s `REVISE` branch does for `REVISING_PLAN` — "
            "`/apply-implementation-review` makes no "
            "`enter_applying_review_feedback` call for a `\"2.2\"` item, "
            "since it finds `phase` already there)."
        )
        self.assertEqual(ws.find_applying_review_feedback_version_claims("doc.md", text), [])

    def test_workflow_v2_plan_terminal_escape_qualifier_is_not_flagged(self):
        # WORKFLOW_V2_PLAN.md's own correct, detailed passage: contains
        # "never reaches this writer" (not one of the banned absolute-cue
        # phrasings) and explicitly names the terminal-phase escape.
        text = (
            "for a `\"2.2\"` item, this phase-conditional rule is always "
            "the skip case in the normal two-stage loop -- the command "
            "finds `phase` already at `APPLYING_REVIEW_FEEDBACK` -- but a "
            "`\"2.2\"` item that instead reaches the *terminal* "
            "`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` phase ... sits at "
            "exactly the phase `enter_applying_review_feedback`'s own "
            "guard names as legal, so the call fires for that item too."
        )
        self.assertEqual(ws.find_applying_review_feedback_version_claims("doc.md", text), [])

    def test_a_qualifier_stated_in_the_next_sentence_of_the_same_bullet_still_suppresses(self):
        # WORKFLOW_V2_1_OPERATOR_REFERENCE.md:438-441's own already-correct
        # shape: the absolute-sounding "no ... call" sentence ends before
        # the qualifying "terminal" appears, in the very next sentence of
        # the same bullet. A sentence-level (rather than window-level)
        # check would miss this.
        text = (
            "REVISE (→ `APPLYING_REVIEW_FEEDBACK` directly, no "
            "`enter_applying_review_feedback` call). A `\"2.2\"` item at "
            "any *other* phase — most commonly its own terminal "
            "`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` once both "
            "implementation-review stages have already approved — still "
            "takes the advisory branch described above, unchanged."
        )
        self.assertEqual(ws.find_applying_review_feedback_version_claims("doc.md", text), [])

    def test_unrelated_reference_far_outside_the_window_does_not_suppress(self):
        # A document that states the absolute-cue phrase near one mention
        # of enter_applying_review_feedback, and a qualifier only far away
        # near an unrelated second mention, must still be flagged: the
        # qualifier has to be *near* the claim it qualifies, not merely
        # present anywhere in the document.
        far_qualifier = "terminal escape " * 5
        padding = "x" * 400
        text = (
            f"`enter_applying_review_feedback` call for it at all.{padding}"
            f"Elsewhere, the {far_qualifier} phase-conditional guard is "
            f"unrelated context about `enter_applying_review_feedback`."
        )
        findings = ws.find_applying_review_feedback_version_claims("doc.md", text)
        self.assertEqual(len(findings), 1, [repr(f) for f in findings])

    def test_round_6_782_row_is_not_currently_flagged_by_neighbor_suppression(self):
        # WORKFLOW_V2_1_OPERATOR_REFERENCE.md's real pre-round-7 `:782` table
        # row (git show 17b8641b, verbatim) IS matched by the "no ... call"
        # cue in isolation, but its real preceding table row in the same
        # document ends in "...the terminal \"ready for approval\" phase
        # (reused rather than a new name)" -- an unrelated use of the word
        # "terminal" that still falls inside the ±300-character window and
        # suppresses the finding. Round 8's `B1` (1): the bare-substring
        # `terminal` qualifier is not scoped to the same list item/table row
        # as the cue it is meant to qualify. Pinned here, in situ, rather
        # than as a paraphrase, exactly because round 6's own fixture below
        # (`test_round_6_b1_instance_is_flagged`) is a paraphrase that
        # cannot catch this — the previous round's Missing-tests item this
        # gap corresponds to.
        preceding_row = (
            '| `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | `record_bundle_'
            'generation` — every `"1"`/`"2.1"` stage/outcome; for `"2.2"`, '
            '`record_manual_implementation_review` on `APPROVE` (the '
            'terminal "ready for approval" phase, reused rather than a new '
            'name) |'
        )
        real_row = (
            '| `APPLYING_REVIEW_FEEDBACK` | `enter_applying_review_feedback` '
            '(`"1"`/`"2.1"`); for `"2.2"` (`workflow-2.5.0`), '
            '`record_local_implementation_review`/'
            '`record_manual_implementation_review` on `REVISE` write it '
            'directly instead, with no `enter_applying_review_feedback` '
            'call |'
        )
        # In isolation the cue fires...
        self.assertEqual(
            len(ws.find_applying_review_feedback_version_claims("doc.md", real_row)), 1,
        )
        # ...but preceded by its real neighbouring row, it is suppressed.
        # This is the documented gap, not the desired behavior: a future
        # strengthening (round 8's `B1` fix (a), not taken this round)
        # should turn this assertion into a non-zero one.
        combined = preceding_row + "\n" + real_row
        self.assertEqual(
            ws.find_applying_review_feedback_version_claims("doc.md", combined), [],
        )

    def test_round_6_no_separate_call_wording_is_not_currently_flagged(self):
        # Round 6's other two real instances both read "makes no
        # **separate** `enter_applying_review_feedback` call" -- an
        # adjective between "no" and the reference cue that the
        # `no\s+enter_applying_review_feedback\s+call` pattern does not
        # tolerate. Round 8's `B1` (2): pinned in situ (markup stripped,
        # matching what the detector actually sees) rather than as a
        # paraphrase.
        text = "this command makes no separate `enter_applying_review_feedback` call for a \"2.2\" item"
        self.assertEqual(
            ws.find_applying_review_feedback_version_claims("doc.md", text), [],
        )

    def test_real_corpus_sweep_is_clean(self):
        """Same deliberately-scoped corpus as
        `GoverningVersionEnumerationSweepTest.test_real_corpus_sweep_is_clean`
        (round 4's O2) -- non-recursive over `.claude/commands/` and
        `docs/ai-workflow/`'s own top level, for consistency with that
        sibling sweep's precedent. **Not**, as round 7's docstring wrongly
        claimed and round 8's `B1` (3) corrects, because a wider recursive
        run demonstrates a true positive outside this scope: run over the
        entire `2.5.0` payload (all `.md`/`.json`/`.py`/`.sh`/`.yml`/`.svg`
        files, recursively), this sweep's cue set returns exactly 6
        findings, every one of them self-referential (this module's own
        cue table and this file's own test fixtures above) --
        `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md:1685` and
        `docs/ai-workflow/requirements/workflow-v2-3-followups-mapping.json:133`
        (both inherited unmodified from the `2.4.0` base, round 7's `O2`)
        are flagged by neither the narrow nor the wide run: their wording,
        "`APPLYING_REVIEW_FEEDBACK` (entered only by ... `enter_applying_
        review_feedback` ...)", matches none of the five closed absolute
        cues. Whether that wording is itself a true instance of the
        underlying claim is a separate question this sweep's cue set is not
        equipped to answer either way; it is not evidence for or against
        widening this test's own corpus, and is not relied on as such
        (round 8's `B1`, correcting round 7's `O2`/this docstring)."""
        overlay_root = Path(__file__).resolve().parent
        payload_root = overlay_root.parent
        paths = list((payload_root / ".claude" / "commands").glob("*.md")) + \
            list((payload_root / "docs" / "ai-workflow").glob("*.md"))
        texts = {str(p): p.read_text() for p in paths}
        findings = ws.sweep_applying_review_feedback_version_claims(texts)
        self.assertEqual(findings, [], [repr(f) for f in findings])


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP7: D-Canonical-Review-Data's generic, parametric
# declaration-coverage helper. This replaces the bespoke, hand-authored
# per-work-item declaration-coverage pattern this item's own CP2
# (`TestImplementationReviewTwoStageDeclarationCoverage`, above) and
# `plan-amendment-mechanism` each separately hand-derived from their own
# registry/declarations data -- the identical mechanical check
# (checkpoint-declared deliverable paths classify `protected`, never
# `UnclassifiedPathError`/`excluded`) re-authored twice by hand from data
# that already exists. Both of those existing tests are left exactly as
# they are (rewriting another, already-approved work item's own test file
# is out of this milestone's scope, and CP2's own test predates CP7 in
# the checkpoint order); every *future* work item's plan calls
# `assert_declaration_coverage` below instead of re-authoring the check.
#
# Precedent, not a second defect (D-Canonical-Review-Data): this is the
# same "mechanically discovered, never hand-maintained" model
# `workflow_state_completion_obligations_test.py`'s own
# `surface_census`/`verifier_census` mechanism already follows -- that
# census is *computed* from the real module surface each run rather than
# typed out by a plan author. This helper generalizes the identical idea
# to per-work-item declaration coverage; it is not a rediscovery of a gap
# that mechanism has, it is the same pattern applied one level up.
# ---------------------------------------------------------------------------


def _implementation_stage_declaration_pairs(implementation_stage: dict) -> list[tuple[str, bool]]:
    """Every implementation-stage declaration as `(entry, is_prefix)`
    pairs -- exact paths (`protected_paths`/`excluded_paths`) and
    prefixes (`protected_prefixes`/`excluded_prefixes`) alike."""
    pairs: list[tuple[str, bool]] = []
    for entry in implementation_stage.get("protected_paths", {}):
        pairs.append((entry, False))
    for entry in implementation_stage.get("excluded_paths", {}):
        pairs.append((entry, False))
    for entry in implementation_stage.get("protected_prefixes", {}):
        pairs.append((entry, True))
    for entry in implementation_stage.get("excluded_prefixes", {}):
        pairs.append((entry, True))
    return pairs


def _contained_by(excepted_entry: str, excepted_is_prefix: bool,
                   candidate_entry: str, candidate_is_prefix: bool) -> bool:
    """Whether `candidate_entry` (an implementation-stage declaration,
    exact path or prefix alike) is contained by `excepted_entry` (a
    `plan_stage.excluded_paths`/`excluded_prefixes` entry): equal to it,
    and itself an exact path, when `excepted_entry` is an exact path --
    nothing counts as contained under a plan-stage exact path beyond that
    same exact path, since nothing can be nested beneath a file -- or
    itself starting with `excepted_entry` when `excepted_entry` is a
    prefix, exact paths and prefixes alike."""
    if not excepted_is_prefix:
        return (not candidate_is_prefix) and candidate_entry == excepted_entry
    return candidate_entry.startswith(excepted_entry)


def implementation_declarations_contained_by(
    excepted_entry: str, excepted_is_prefix: bool, implementation_stage: dict,
) -> list[str]:
    """The complete set of implementation-stage declarations -- exact
    paths and prefixes, `protected_*`/`excluded_*` alike -- contained by
    `excepted_entry`, sorted. This is the set `narrowing_exceptions`'
    exhaustiveness requirement measures a listed entry set against."""
    return sorted(
        entry for entry, is_prefix in _implementation_stage_declaration_pairs(implementation_stage)
        if _contained_by(excepted_entry, excepted_is_prefix, entry, is_prefix)
    )


def find_declaration_symmetry_gaps(plan_stage: dict, implementation_stage: dict) -> tuple[list[str], list[str]]:
    """`D-Canonical-Review-Data`'s plan-stage/implementation-stage
    declaration-symmetry check, both directions -- never raises itself,
    so a caller can assert on the complete failure set at once rather
    than stopping at the first one found:

    (a) every `implementation_stage.protected_paths`/`protected_prefixes`
        entry's own literal key must classify under the plan-stage sets
        (`fingerprint.classify_path`) without raising
        `UnclassifiedPathError`.
    (b) every `plan_stage.excluded_paths`/`excluded_prefixes` entry's own
        literal key must be either (i) classifiable outright under the
        implementation-stage sets (`fingerprint.classify_path_implementation_stage`),
        or (ii) named as a key in `plan_stage.narrowing_exceptions`, whose
        mapped list is non-empty, every listed entry declared at
        implementation stage and contained by the entry it excepts, and
        the list exhaustive -- equal to (not merely a subset of) the
        complete set of implementation-stage declarations contained by
        that entry (`implementation_declarations_contained_by`).
        Partial overlap alone, with no `narrowing_exceptions` entry, is
        never accepted as coverage. A `narrowing_exceptions` key that
        does not itself equal a real `plan_stage.excluded_paths`/
        `excluded_prefixes` entry is flagged too, closing the same
        staleness from the declaration side.

    Returns `(direction_a_gaps, direction_b_gaps)`, each a list of
    human-readable failure descriptions (empty when that direction is
    clean)."""
    protected = frozenset(plan_stage.get("protected_paths", []))
    excluded_paths = plan_stage.get("excluded_paths", {})
    excluded_prefixes = plan_stage.get("excluded_prefixes", {})
    narrowing_exceptions = plan_stage.get("narrowing_exceptions", {})

    impl_protected_paths = implementation_stage.get("protected_paths", {})
    impl_protected_prefixes = implementation_stage.get("protected_prefixes", {})
    impl_excluded_paths = implementation_stage.get("excluded_paths", {})
    impl_excluded_prefixes = implementation_stage.get("excluded_prefixes", {})

    direction_a_gaps: list[str] = []
    for entry in list(impl_protected_paths) + list(impl_protected_prefixes):
        try:
            fingerprint.classify_path(entry, protected, excluded_paths, excluded_prefixes)
        except fingerprint.UnclassifiedPathError:
            direction_a_gaps.append(
                f"implementation_stage entry {entry!r} is not classifiable by any plan_stage set"
            )

    declared_implementation_entries = {
        entry for entry, _is_prefix in _implementation_stage_declaration_pairs(implementation_stage)
    }
    implementation_prefix_entries = set(impl_protected_prefixes) | set(impl_excluded_prefixes)

    direction_b_gaps: list[str] = []
    excepted_entries = ([(p, False) for p in excluded_paths]
                        + [(p, True) for p in excluded_prefixes])
    for excepted_entry, is_prefix in excepted_entries:
        try:
            fingerprint.classify_path_implementation_stage(
                excepted_entry, impl_protected_paths, impl_protected_prefixes,
                impl_excluded_paths, impl_excluded_prefixes,
            )
            continue  # clause (i): classifiable outright
        except fingerprint.UnclassifiedPathError:
            pass
        # clause (ii): must be named in narrowing_exceptions
        if excepted_entry not in narrowing_exceptions:
            direction_b_gaps.append(
                f"plan_stage entry {excepted_entry!r} is not classifiable by any implementation_stage "
                f"set and has no narrowing_exceptions entry"
            )
            continue
        listed = narrowing_exceptions[excepted_entry]
        if not listed:
            direction_b_gaps.append(
                f"narrowing_exceptions[{excepted_entry!r}] is empty -- no implementation_stage "
                f"narrowing declared for it at all"
            )
            continue
        complete = implementation_declarations_contained_by(excepted_entry, is_prefix, implementation_stage)
        problems: list[str] = []
        for listed_entry in listed:
            if listed_entry not in declared_implementation_entries:
                problems.append(f"{listed_entry!r} is not declared at implementation stage")
                continue
            listed_is_prefix = listed_entry in implementation_prefix_entries
            if not _contained_by(excepted_entry, is_prefix, listed_entry, listed_is_prefix):
                problems.append(f"{listed_entry!r} is not contained by {excepted_entry!r}")
        if sorted(listed) != complete:
            missing = sorted(set(complete) - set(listed))
            if missing:
                problems.append(
                    f"omits {missing!r}, also contained by {excepted_entry!r} (not exhaustive)"
                )
        for problem in problems:
            direction_b_gaps.append(f"narrowing_exceptions[{excepted_entry!r}]: {problem}")

    # A narrowing_exceptions key that does not itself equal a real
    # plan_stage.excluded_paths/excluded_prefixes entry must fail too.
    for key in narrowing_exceptions:
        if key not in excluded_paths and key not in excluded_prefixes:
            direction_b_gaps.append(
                f"narrowing_exceptions key {key!r} is not itself a plan_stage.excluded_paths/"
                f"excluded_prefixes entry"
            )

    return direction_a_gaps, direction_b_gaps


def assert_declaration_coverage(work_item_id: str, repo_root: Path) -> None:
    """The generic, parametric declaration-coverage assertion
    `D-Canonical-Review-Data` extracts from this item's own CP2
    (`TestImplementationReviewTwoStageDeclarationCoverage`) and
    `plan-amendment-mechanism`'s own separately hand-authored test: reads
    `<work_item_id>-registry.json` and `<work_item_id>-artifacts.json`
    straight off disk, then asserts (1) every
    `implementation_stage.protected_paths`/`protected_prefixes` entry
    classifies `protected` under the implementation-stage classifier, and
    (2) both plan-stage/implementation-stage declaration-symmetry
    directions (`find_declaration_symmetry_gaps`) are clean. A future
    work item's plan calls this instead of re-authoring the check by
    hand; CP12 (`workflow-2.5.0`'s own disposable-repository validation)
    exercises this helper directly for its own synthetic work item rather
    than hand-writing another copy."""
    registry_path = repo_root / f"docs/ai-workflow/registry/{work_item_id}-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("work_item_id") != work_item_id:
        raise AssertionError(
            f"{registry_path}: registry work_item_id {registry.get('work_item_id')!r} != {work_item_id!r}"
        )

    artifacts_path = repo_root / fingerprint.artifacts_path_for_work_item(work_item_id)
    declarations = json.loads(artifacts_path.read_text(encoding="utf-8"))
    plan_stage = declarations["plan_stage"]
    implementation_stage = declarations["implementation_stage"]

    impl_protected_paths = implementation_stage.get("protected_paths", {})
    impl_protected_prefixes = implementation_stage.get("protected_prefixes", {})
    impl_excluded_paths = implementation_stage.get("excluded_paths", {})
    impl_excluded_prefixes = implementation_stage.get("excluded_prefixes", {})

    for path in impl_protected_paths:
        classification = fingerprint.classify_path_implementation_stage(
            path, impl_protected_paths, impl_protected_prefixes, impl_excluded_paths, impl_excluded_prefixes,
        )
        if classification != "protected":
            raise AssertionError(f"{path}: classifies {classification!r}, expected 'protected'")
    for prefix in impl_protected_prefixes:
        sample = prefix + "some_deliverable_file.txt"
        classification = fingerprint.classify_path_implementation_stage(
            sample, impl_protected_paths, impl_protected_prefixes, impl_excluded_paths, impl_excluded_prefixes,
        )
        if classification != "protected":
            raise AssertionError(f"{sample}: classifies {classification!r}, expected 'protected'")

    direction_a_gaps, direction_b_gaps = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
    if direction_a_gaps:
        raise AssertionError(f"declaration symmetry direction (a) failed: {direction_a_gaps}")
    if direction_b_gaps:
        raise AssertionError(f"declaration symmetry direction (b) failed: {direction_b_gaps}")


class DeclarationSymmetryHelperTest(unittest.TestCase):
    """The required synthetic fixtures (`IMPLEMENTATION_REVIEW_TWO_STAGE_PLAN.md`
    §CP7, revision 41) pinning `find_declaration_symmetry_gaps`'s every
    documented case. (workflow-2.5.0 CP11, release-authoring sweep: the
    real-corpus before/after check this class used to also carry -- pinned
    against this repository's own live `implementation-review-two-stage`
    declarations file and a hardcoded CP7 commit SHA -- was removed here,
    along with the sibling `TestImplementationReviewTwoStageDeclarationCoverage`
    class: both were self-referential to this exact repository at this
    exact commit and could never pass once installed into any other target
    repository via the general release payload. CP12's own disposable-
    repository functional validation exercises this same generic helper
    against a synthetic work item instead, per its own registry entry.)"""

    # -- direction (a) -----------------------------------------------------

    def test_direction_a_fails_when_implementation_protected_path_unclaimed_by_plan_stage(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {}, "excluded_prefixes": {}}
        implementation_stage = {"protected_paths": {"src/x.py": "j"}, "protected_prefixes": {},
                                 "excluded_paths": {}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_b, [])
        self.assertEqual(len(gaps_a), 1)
        self.assertIn("src/x.py", gaps_a[0])

    # -- direction (b): no coverage at all ----------------------------------

    def test_direction_b_fails_when_plan_excluded_path_has_no_implementation_declaration_at_all(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {"foo.txt": "j"}, "excluded_prefixes": {}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("foo.txt", gaps_b[0])
        self.assertIn("no narrowing_exceptions entry", gaps_b[0])

    def test_direction_b_fails_when_plan_excluded_prefix_is_only_partially_covered_with_no_exception(self):
        # foo/bar.txt is declared at implementation stage, but the
        # excepted entry's own literal key ("foo/") still does not
        # classify -- partial overlap alone is never accepted.
        plan_stage = {"protected_paths": [], "excluded_paths": {}, "excluded_prefixes": {"foo/": "j"}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {"foo/bar.txt": "j"}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("foo/", gaps_b[0])
        self.assertIn("no narrowing_exceptions entry", gaps_b[0])

    # -- direction (b): narrowing_exceptions present but malformed ----------

    def test_direction_b_fails_when_narrowing_exceptions_value_is_empty(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": []}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("is empty", gaps_b[0])

    def test_direction_b_fails_when_narrowing_exceptions_listed_path_is_undeclared(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": ["foo/bar.txt"]}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("not declared at implementation stage", gaps_b[0])

    def test_direction_b_fails_when_narrowing_exceptions_listed_path_is_not_contained(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": ["bar/baz.txt"]}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {"bar/baz.txt": "j"}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("not contained by", gaps_b[0])

    def test_direction_b_fails_when_narrowing_exceptions_list_omits_a_contained_exact_child(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": ["foo/a.txt"]}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {"foo/a.txt": "j", "foo/b.txt": "j"}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("not exhaustive", gaps_b[0])
        self.assertIn("foo/b.txt", gaps_b[0])

    def test_direction_b_fails_when_narrowing_exceptions_list_omits_a_contained_prefix_child(self):
        # The prefix twin of the exact-child exhaustiveness fixture above:
        # exhaustiveness is measured against the complete set (exact
        # paths and prefixes alike), never the exact-only subset.
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": ["foo/a.txt"]}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {"foo/a.txt": "j"},
                                 "excluded_prefixes": {"foo/sub/": "j"}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("not exhaustive", gaps_b[0])
        self.assertIn("foo/sub/", gaps_b[0])

    def test_direction_b_live_regression_exact_path_addition_without_updating_the_exception(self):
        # The exact-path live-regression fixture: a state that passes
        # today must fail the moment a further implementation-stage exact
        # path lands under an already-excepted entry without that
        # entry's own narrowing_exceptions list being updated to match.
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": ["foo/a.txt", "foo/b.txt"]}}
        good_implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                      "excluded_paths": {"foo/a.txt": "j", "foo/b.txt": "j"},
                                      "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, good_implementation_stage)
        self.assertEqual((gaps_a, gaps_b), ([], []))

        regressed_implementation_stage = copy.deepcopy(good_implementation_stage)
        regressed_implementation_stage["excluded_paths"]["foo/c.txt"] = "j"
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, regressed_implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("foo/c.txt", gaps_b[0])

    def test_direction_b_live_regression_prefix_addition_without_updating_the_exception(self):
        # The prefix twin of the live-regression fixture above.
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": ["foo/a.txt", "foo/sub/"]}}
        good_implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                      "excluded_paths": {"foo/a.txt": "j"},
                                      "excluded_prefixes": {"foo/sub/": "j"}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, good_implementation_stage)
        self.assertEqual((gaps_a, gaps_b), ([], []))

        regressed_implementation_stage = copy.deepcopy(good_implementation_stage)
        regressed_implementation_stage["excluded_prefixes"]["foo/sub2/"] = "j"
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, regressed_implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("foo/sub2/", gaps_b[0])

    def test_direction_b_fails_when_narrowing_exceptions_key_is_not_a_real_plan_stage_entry(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {"real.txt": "j"}, "excluded_prefixes": {},
                       "narrowing_exceptions": {"fake/": ["fake/child.txt"]}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {"fake/child.txt": "j", "real.txt": "j"}, "excluded_prefixes": {}}
        gaps_a, gaps_b = find_declaration_symmetry_gaps(plan_stage, implementation_stage)
        self.assertEqual(gaps_a, [])
        self.assertEqual(len(gaps_b), 1)
        self.assertIn("not itself a plan_stage.excluded_paths/excluded_prefixes entry", gaps_b[0])

    # -- direction (b): passing cases ---------------------------------------

    def test_direction_b_passes_for_the_declared_workflow_manager_narrowing_exception(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {".workflow-manager/": "j"},
                       "narrowing_exceptions": {".workflow-manager/": [".workflow-manager/installation.json"]}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {".workflow-manager/installation.json": "j"},
                                 "excluded_prefixes": {}}
        self.assertEqual(find_declaration_symmetry_gaps(plan_stage, implementation_stage), ([], []))

    def test_direction_b_passes_when_plan_entry_is_fully_covered_without_needing_an_exception(self):
        plan_stage = {"protected_paths": [], "excluded_paths": {"tools/build_release.py": "j"},
                       "excluded_prefixes": {}}
        implementation_stage = {"protected_paths": {"tools/build_release.py": "j"}, "protected_prefixes": {},
                                 "excluded_paths": {}, "excluded_prefixes": {}}
        self.assertEqual(find_declaration_symmetry_gaps(plan_stage, implementation_stage), ([], []))

    def test_direction_b_passes_when_narrowing_exception_names_a_contained_prefix_not_an_exact_path(self):
        # A plan-stage entry whose only implementation-stage narrowing is
        # itself a prefix -- contained by, not equal to, the excepted
        # entry -- must be representable and pass.
        plan_stage = {"protected_paths": [], "excluded_paths": {},
                       "excluded_prefixes": {"foo/": "j"},
                       "narrowing_exceptions": {"foo/": ["foo/sub/"]}}
        implementation_stage = {"protected_paths": {}, "protected_prefixes": {},
                                 "excluded_paths": {}, "excluded_prefixes": {"foo/sub/": "j"}}
        self.assertEqual(find_declaration_symmetry_gaps(plan_stage, implementation_stage), ([], []))


# ---------------------------------------------------------------------------
# CP8 -- D-Review-Finding-Taxonomy-and-Circuit-Breaker. Purely advisory
# prose (REVIEW_PROTOCOL.md's "Feedback protocol" section): no
# WORKFLOW_STATE.json field, no phase gate, no governing-version bump, and
# no runtime parser rejects a missing/malformed tag. The helpers below are
# test-only reference logic modeling exactly the algorithm the prose
# describes -- they are never imported by workflow_state.py or by any
# review command, matching the "advisory, never machine-enforced" framing.
# ---------------------------------------------------------------------------


import re as _re

_FINDING_TAG_RE = _re.compile(r"\[(substantive|apparatus)\]", _re.IGNORECASE)
_REVIEWER_ROLE_RE = _re.compile(r"^Reviewer role:\s*(\S+)\s*$", _re.MULTILINE)
_STATUS_RE = _re.compile(r"^Status:\s*(APPROVE|REVISE|BLOCK)\s*$", _re.MULTILINE)
_LOCAL_MODEL_STAGES = {"LOCAL_MODEL_PLAN_REVIEW", "LOCAL_MODEL_IMPLEMENTATION_REVIEW"}


def _finding_blocks(feedback_text):
    """Every Blocking/Important finding entry, as the raw text following
    each leading '- ' bullet under those two headings -- fixture-only
    parsing, deliberately naive (no REVIEW_FEEDBACK.md structural
    validation), since only the tag's own presence/value matters here."""
    blocks = []
    for heading in ("Blocking findings", "Important findings"):
        match = _re.search(
            rf"^## {heading}\n(.*?)(?=\n## |\Z)", feedback_text, _re.MULTILINE | _re.DOTALL,
        )
        if not match:
            continue
        body = match.group(1)
        blocks.extend(line for line in body.splitlines() if line.strip().startswith("-"))
    return blocks


def _finding_tag(finding_line):
    """The advisory tag for one finding line -- 'apparatus' only when
    exactly and unambiguously tagged so; a missing, malformed, or (were one
    ever present) multiply-tagged line defaults, conservatively, to
    'substantive' (D-Review-Finding-Taxonomy-and-Circuit-Breaker point 1),
    so a missing tag can never masquerade as an apparatus-only finding."""
    tags = _FINDING_TAG_RE.findall(finding_line)
    if len(tags) == 1 and tags[0].lower() == "apparatus":
        return "apparatus"
    return "substantive"


def _reviewer_role(feedback_text):
    match = _REVIEWER_ROLE_RE.search(feedback_text)
    return match.group(1) if match else None


def _status(feedback_text):
    match = _STATUS_RE.search(feedback_text)
    return match.group(1) if match else None


def _is_apparatus_only_revise_round(feedback_text):
    """A REVISE round is apparatus-only when at least one Blocking/
    Important finding is present and every one of them tags 'apparatus'
    (an untagged/malformed one, defaulting to 'substantive', breaks this).
    A round with no Blocking/Important findings at all is not a REVISE
    round to begin with under REVIEW_PROTOCOL.md's own rule, so it is
    never treated as apparatus-only here."""
    if _status(feedback_text) != "REVISE":
        return False
    findings = _finding_blocks(feedback_text)
    if not findings:
        return False
    return all(_finding_tag(line) == "apparatus" for line in findings)


def circuit_breaker_fires(previous_feedback_text, current_feedback_text):
    """Reference model of the advisory signal: fires exactly when both
    rounds declare the *same* local-model `Reviewer role:` stage, both are
    REVISE, and both are apparatus-only -- the exact scope
    D-Review-Finding-Taxonomy-and-Circuit-Breaker states, narrowed to the
    two local-model stages only (never a manual-external one)."""
    prev_role = _reviewer_role(previous_feedback_text)
    curr_role = _reviewer_role(current_feedback_text)
    if prev_role is None or prev_role != curr_role or prev_role not in _LOCAL_MODEL_STAGES:
        return False
    return (
        _is_apparatus_only_revise_round(previous_feedback_text)
        and _is_apparatus_only_revise_round(current_feedback_text)
    )


def _feedback(role, status, findings):
    """Builds a minimal, schema-shaped REVIEW_FEEDBACK.md fixture: findings
    is a list of (heading, tag_or_None) pairs, tag_or_None omitted meaning
    'untagged'."""
    lines = [
        "# Review Decision", "", f"Status: {status}", "",
        "Reviewed bundle ID: deadbeef", "Reviewed base commit: cafef00d",
        "Work item: implementation-review-two-stage", "",
        f"Reviewer role: {role}", "",
        "## Blocking findings", "",
    ]
    for heading, tag in findings:
        tagged = f"[{tag}] " if tag else ""
        lines.append(f"- {tagged}{heading}")
    lines += ["", "## Important findings", "", "## Optional findings", ""]
    return "\n".join(lines)


class FindingTaxonomyCircuitBreakerTest(unittest.TestCase):
    """CP8's first required test: the signal fires after two consecutive
    apparatus-only REVISE rounds for the same local-model stage -- for
    each of the two local-model stages independently -- and never
    otherwise, including the missing-tag-can't-masquerade case."""

    def test_fires_after_two_consecutive_apparatus_only_rounds_for_each_local_model_stage(self):
        for role in sorted(_LOCAL_MODEL_STAGES):
            with self.subTest(role=role):
                previous = _feedback(role, "REVISE", [("stale prose", "apparatus")])
                current = _feedback(role, "REVISE", [("another stale reference", "apparatus")])
                self.assertTrue(circuit_breaker_fires(previous, current))

    def test_does_not_fire_across_different_stages(self):
        previous = _feedback("LOCAL_MODEL_PLAN_REVIEW", "REVISE", [("x", "apparatus")])
        current = _feedback("LOCAL_MODEL_IMPLEMENTATION_REVIEW", "REVISE", [("y", "apparatus")])
        self.assertFalse(circuit_breaker_fires(previous, current))

    def test_does_not_fire_for_manual_external_rounds(self):
        previous = _feedback("MANUAL_EXTERNAL_PLAN_REVIEW", "REVISE", [("x", "apparatus")])
        current = _feedback("MANUAL_EXTERNAL_PLAN_REVIEW", "REVISE", [("y", "apparatus")])
        self.assertFalse(circuit_breaker_fires(previous, current))

    def test_a_missing_tag_defaults_to_substantive_and_cannot_masquerade_as_apparatus(self):
        # An untagged finding never manufactures an apparatus-only streak
        # by silence (point 1 of the design decision).
        previous = _feedback("LOCAL_MODEL_PLAN_REVIEW", "REVISE", [("untagged finding", None)])
        current = _feedback("LOCAL_MODEL_PLAN_REVIEW", "REVISE", [("also apparatus", "apparatus")])
        self.assertFalse(circuit_breaker_fires(previous, current))
        self.assertEqual(_finding_tag("- untagged finding"), "substantive")

    def test_a_single_substantive_round_never_fires_regardless_of_the_other_round(self):
        previous = _feedback("LOCAL_MODEL_IMPLEMENTATION_REVIEW", "REVISE",
                              [("a real defect", "substantive")])
        current = _feedback("LOCAL_MODEL_IMPLEMENTATION_REVIEW", "REVISE",
                             [("cleanup only", "apparatus")])
        self.assertFalse(circuit_breaker_fires(previous, current))

    def test_resolve_or_reject_rule_is_unaffected_by_tag_or_its_absence(self):
        # D-Review-Finding-Taxonomy-and-Circuit-Breaker point 2: the tag
        # (or its absence) changes nothing about which findings must be
        # resolved or explicitly rejected -- REVIEW_PROTOCOL.md's own text
        # states this for both tags and for an untagged finding alike.
        protocol_path = Path(__file__).resolve().parent.parent / "docs/ai-workflow/REVIEW_PROTOCOL.md"
        text = protocol_path.read_text(encoding="utf-8")
        self.assertIn(
            "Every blocking and\nimportant finding must end up either resolved, or explicitly rejected",
            text,
        )
        self.assertIn('never means "may be\nignored"', text)


class ReviewProtocolManualExternalCircuitBreakerScopeTest(unittest.TestCase):
    """CP8's second required test: REVIEW_PROTOCOL.md's own added text
    makes no manual-external recoverability claim a reader could act on
    (revision 15, MANUAL_EXTERNAL_PLAN_REVIEW round 1, finding I3) --
    proved against the prose itself, not assumed."""

    def _circuit_breaker_section(self):
        protocol_path = Path(__file__).resolve().parent.parent / "docs/ai-workflow/REVIEW_PROTOCOL.md"
        text = protocol_path.read_text(encoding="utf-8")
        match = _re.search(
            r"### Finding taxonomy and circuit breaker.*?(?=\n## )", text, _re.DOTALL,
        )
        self.assertIsNotNone(match, "D-Review-Finding-Taxonomy-and-Circuit-Breaker section not found")
        return match.group(0)

    def test_section_states_no_manual_external_recoverability_claim(self):
        section = self._circuit_breaker_section()
        self.assertIn("no", section)
        self.assertIn("recoverability claim", section)
        self.assertIn("manual-external", section.lower())
        self.assertIn("operator\njudgment", section)

    def test_section_never_affirmatively_claims_the_bound_fires_for_manual_external_rounds(self):
        section = self._circuit_breaker_section()
        flat = _re.sub(r"\s+", " ", section)
        sentences = _re.split(r"(?<=[.:])\s+", flat)
        manual_external_sentences = [s for s in sentences if "manual-external" in s.lower()]
        self.assertTrue(manual_external_sentences, "no sentence mentions manual-external at all")
        affirmative_claim_markers = (
            "fires", "applies to", "also applies", "the same bound", "recovers the signal",
            "is available for", "holds across two consecutive manual",
        )
        for sentence in manual_external_sentences:
            lowered = sentence.lower()
            for marker in affirmative_claim_markers:
                self.assertNotIn(
                    marker, lowered,
                    f"sentence {sentence!r} makes an affirmative manual-external "
                    f"circuit-breaker claim via {marker!r}",
                )


# ---------------------------------------------------------------------------
# CP9 -- post-v2.3.1 backlog, tractable fixes (`docs/ai-workflow/
# IMPLEMENTATION_REVIEW_TWO_STAGE_PLAN.md` section 2.7). Forward-only:
# `generate_artifacts_declarations`'s implementation-stage default template
# gains the `.workflow-manager/` exclusion the plan-stage default already
# carried (`v2.4.0-001`'s own plan-stage fix), so a freshly generated
# declarations file is not exposed to the identical gap this item's own
# CP2 (revision 2, finding B1) had to hand-fix for itself. No existing work
# item's already-generated declarations file is edited by this change. The
# other three backlog fixes (`v2.3.1-003`'s mode-`100644` fallback,
# `v2.3.1-001`'s portable host-note skip, and `migration/
# portability_exceptions.json`'s required empty `by_version["2.5.0"]`
# entry) are regression-tested in this overlay's own
# `workflow_integration_test.py` and this repository's own
# `tests/test_conformance_suite.py`, respectively -- not here.
# ---------------------------------------------------------------------------


class GeneratedDeclarationsWorkflowManagerImplementationStageWideningTest(unittest.TestCase):
    """CP9's own required regression test: `generate_artifacts_declarations`'s
    implementation-stage default classifies
    `.workflow-manager/installation.json` `excluded` for a freshly-generated
    declarations file, for both work-item types -- the implementation-stage
    twin of the plan-stage `.workflow-manager/` exclusion `v2.4.0-001`'s own
    fix already added (`plan_stage_excluded_prefixes.setdefault('.workflow-
    manager/', ...)`)."""

    def _declarations(self, work_item_type: str) -> dict:
        return ws.generate_artifacts_declarations(
            "new-item", "docs/ai-workflow/new-item-plan.md",
            "docs/ai-workflow/registry/new-item-registry.json",
            "docs/ai-workflow/requirements/new-item-mapping.json",
            work_item_type=work_item_type,
        )

    def _classify_impl(self, declarations: dict, path: str) -> str:
        stage = declarations["implementation_stage"]
        return fingerprint.classify_path_implementation_stage(
            path, stage["protected_paths"], stage["protected_prefixes"],
            stage["excluded_paths"], stage["excluded_prefixes"],
        )

    def test_process_item_implementation_stage_excludes_workflow_manager_installation_record(self):
        declarations = self._declarations("process")
        self.assertEqual(
            self._classify_impl(declarations, ".workflow-manager/installation.json"),
            "excluded",
        )

    def test_product_item_implementation_stage_excludes_workflow_manager_installation_record(self):
        declarations = self._declarations("product")
        self.assertEqual(
            self._classify_impl(declarations, ".workflow-manager/installation.json"),
            "excluded",
        )

    def test_the_widened_prefix_is_present_verbatim_symmetric_with_the_plan_stage_default(self):
        declarations = self._declarations("process")
        self.assertIn(
            ".workflow-manager/", declarations["implementation_stage"]["excluded_prefixes"],
        )
        self.assertIn(
            ".workflow-manager/", declarations["plan_stage"]["excluded_prefixes"],
        )


if __name__ == "__main__":
    unittest.main()
