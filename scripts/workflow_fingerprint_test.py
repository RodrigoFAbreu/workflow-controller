#!/usr/bin/env python3
"""Hermetic unit tests for workflow_fingerprint.py.

Runs entirely against disposable scratch Git repositories this suite
creates and destroys itself — never against this repository's own working
tree or a hardcoded historical commit. The real-repository demonstration
lives separately in `workflow_fingerprint_demo_test.py`.

Covers round-6 required acceptance criteria items 2-3 (the plan's own
test-vector table implemented as real tests), round-6 missing-test items
89-96/110-111, round-7's findings (`PROTO-R7-001` through `-013`), and
round-8's findings (`OPUS-R8-001` through `-019`, missing-test items
21-50 in `REVIEW_FEEDBACK.md`).

This docstring intentionally states exactly which items are implemented,
per OPUS-R8-012's finding that the previous version's coverage claim
("round-6 missing-test items 89-96/110-111") overstated what the suite
actually contained: items 90, 94, and 96 were absent despite being
claimed. All of 89-96, 110, 111, and 21-50 (round 8) are implemented
below, each tagged with its item/finding ID in the test name or
docstring. Round 9's findings (`GPT-R9-001`, `-009`, `-010`, `-014`;
missing-test items 1-3, 22, 23, 25) are implemented too.

`WF4a-i`: missing-test items 138 (`OPUS-R20-001`, atomic manifest write),
139 (`OPUS-R20-002`, root build files pinned unclassified), and 140
(`OPUS-R20-003`, plan-stage/implementation-stage opposite classification)
are implemented, plus coverage for the new implementation-stage
classification/manifest/identity functions
(`TestImplementationStageClassification`).

Stdlib-only. Run: python3 scripts/workflow_fingerprint_test.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import workflow_fingerprint as wf
import workflow_state as ws


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _write_review_request_with_content_id(repo, bundle_dir, **compute_overrides):
    """Seed REVIEW_REQUEST.md with the review_content_id
    `write_manifest_with_verified_identifiers` will compute, mirroring the
    real operator workflow: read the identifier (read-only), state it in
    REVIEW_REQUEST.md, then run the write path, which recomputes and
    asserts the two still agree (OPUS-R18-005)."""
    digest, _ = repo.compute(**compute_overrides)
    (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {digest}\n")
    return digest


# Realistic 64-hex-character placeholders for bundle_id test fixtures --
# the field contract (OPUS-R8-013) requires exactly 64 hex characters, so
# short placeholders like "deadbeef" no longer match and must not be used.
FAKE_ID_A = "a" * 64
FAKE_ID_B = "b" * 64


class ScratchRepo:
    """A disposable Git repo with the three plan-stage protected files
    present, mirroring this repository's actual layout."""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="wf-fingerprint-test-"))
        _run(["git", "init", "-q"], cwd=self.root)
        _run(["git", "config", "user.email", "test@example.com"], cwd=self.root)
        _run(["git", "config", "user.name", "Test"], cwd=self.root)
        (self.root / "README.md").write_text("base\n")
        _run(["git", "add", "README.md"], cwd=self.root)
        _run(["git", "commit", "-q", "-m", "base"], cwd=self.root)
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        (self.root / "docs" / "ai-workflow").mkdir(parents=True)
        return self

    def write_plan_docs(
        self, plan_text="plan v1\n", audit_text="audit v1\n", decisions_text="decisions v1\n",
        registry_text='{"checkpoints": []}\n', mapping_text='{"requirements": {}}\n',
    ):
        (self.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text(plan_text)
        (self.root / "docs" / "ai-workflow" / "WORKFLOW_V2_AUDIT.md").write_text(audit_text)
        (self.root / "docs" / "TECHNICAL_DECISIONS.md").write_text(decisions_text)
        (self.root / "docs" / "ai-workflow" / "registry").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "ai-workflow" / "registry" / "workflow-v2-1-core-registry.json").write_text(registry_text)
        (self.root / "docs" / "ai-workflow" / "requirements").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "ai-workflow" / "requirements" / "workflow-v2-1-core-mapping.json").write_text(mapping_text)

    def commit_plan_docs_as_base(self):
        """Commit the three plan docs and advance `self.base` to that
        commit, so nothing is changed/untracked relative to base -- used
        by tests that vary the *protected set parameter* itself rather
        than file content, where the docs must already be settled,
        unclassified-content-neutral history."""
        _run(["git", "add", "-A"], cwd=self.root)
        _run(["git", "commit", "-q", "-m", "settle plan docs"], cwd=self.root)
        self.base = self.head()

    def compute(self, base=None, **overrides):
        """The core-specific fixture helper `GPT-R30-005` asks for: this
        class's own docstring already scopes it to `workflow-v2-1-core`'s
        real layout, so its `protected`/`excluded_paths`/`excluded_prefixes`
        defaults (retired from the low-level production function itself)
        live here instead, explicitly, rather than silently on the
        function every generic caller shares."""
        kwargs = dict(
            work_item_type="process",
            work_item_id="workflow-v2-1-core",
            plan_revision=7,
            protected=wf.PLAN_STAGE_PROTECTED,
            excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
            excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
        )
        kwargs.update(overrides)
        return wf.compute_review_content_id_plan_stage(self.root, base or self.base, **kwargs)

    def compute_at_commit(self, commit, base=None, **overrides):
        """Commit-source counterpart of `compute()` -- same core-specific
        fixture defaults, explicit here rather than borrowed from the
        (now default-free) low-level function (`GPT-R30-005`)."""
        kwargs = dict(
            work_item_type="process",
            work_item_id="workflow-v2-1-core",
            plan_revision=7,
            protected=wf.PLAN_STAGE_PROTECTED,
            excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
            excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
        )
        kwargs.update(overrides)
        return wf.compute_review_content_id_plan_stage_at_commit(self.root, base or self.base, commit, **kwargs)

    def head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)


class TestFailClosedClassification(unittest.TestCase):
    """PROTO-R7-001: classification must actually gate manifest
    construction, not merely exist as an untriggered helper."""

    def test_089_untracked_files_produce_nonempty_manifest_with_real_shas(self):
        """Every entry in PLAN_STAGE_PROTECTED now names a file
        write_plan_docs() actually creates (OPUS-R14-001/-009 moved the
        one previously-unwritten path, the operator guide, to the excluded
        set), so this manifest has no tombstone entries at all."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest, projection = repo.compute()
            manifest = projection["review_content_manifest"]
            self.assertEqual(len(manifest), 5)
            for entry in manifest:
                self.assertTrue(entry["exists"])
                expected = wf._hash_object(repo.root, entry["path"])
                self.assertEqual(entry["blob"], expected)

    def test_090_manifest_identical_whether_or_not_add_N_was_applied(self):
        """Round-6 missing-test item 90, absent from the previous suite
        despite being claimed covered (OPUS-R8-012): a prior caller's
        `git add -N` (e.g. `prepare-ai-review.sh`'s intent-to-add step)
        must not change the plan-stage manifest."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest_before, _ = repo.compute()
            _run(["git", "add", "-N", "-A"], cwd=repo.root)
            digest_after, _ = repo.compute()
            self.assertEqual(digest_before, digest_after)

    def test_095_unclassified_changed_path_fails_closed_end_to_end(self):
        """`app/` is now an excluded prefix (OPUS-R18-004), so this uses a
        genuinely novel path outside every protected/excluded set instead
        of `app/`, which no longer demonstrates fail-closed behavior."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            (repo.root / "totally_unmapped_surprise").mkdir()
            (repo.root / "totally_unmapped_surprise" / "unclassified.txt").write_text("surprise\n")
            with self.assertRaises(wf.UnclassifiedPathError):
                repo.compute()

    def test_unclassified_untracked_path_fails_closed(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            (repo.root / "random_new_file.txt").write_text("x\n")
            with self.assertRaises(wf.UnclassifiedPathError):
                wf.assert_all_changed_paths_classified_worktree(
                    repo.root, wf.resolve_base(repo.root, repo.base)
                )

    def test_excluded_path_does_not_raise_and_does_not_affect_identity(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            (repo.root / ".gitignore").write_text("*.log\n")
            digest2, _ = repo.compute()
            self.assertEqual(digest1, digest2)

    def test_096_every_path_this_milestone_creates_is_classified_by_exactly_one_list(self):
        """Round-6 missing-test item 96, previously absent (OPUS-R8-012):
        the classifier's exhaustiveness over this milestone's own known
        artifact set."""
        known_paths = [
            "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
            "docs/TECHNICAL_DECISIONS.md",
            "docs/ai-workflow/registry/workflow-v2-1-core-registry.json",
            "docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json",
            "scripts/workflow_fingerprint.py",
            "scripts/workflow_fingerprint_test.py",
            "scripts/workflow_fingerprint_demo_test.py",
            ".gitignore",
            ".github/workflows/ci.yml",
            "docs/ai-workflow/WORKFLOW_STATE.json",
            "docs/ai-workflow/WORKFLOW_CONFIG.json",
            # Implementation-phase artifacts (OPUS-R10-001): every path this
            # milestone's own checkpoints are known to create or modify past
            # the plan stage must classify too, or plan-approval durability
            # becomes unevaluable the moment the first one lands.
            "CLAUDE.md",
            "docs/ai-workflow/REVIEW_PROTOCOL.md",
            "docs/ai-workflow/MILESTONE_WORKFLOW.md",
            ".claude/commands/bootstrap-workflow-v2.md",
            ".claude/commands/approve-review.md",
            ".claude/commands/accept-milestone.md",
            ".claude/commands/milestone-plan.md",
            ".claude/commands/milestone-implement.md",
            ".claude/commands/apply-plan-review.md",
            ".claude/commands/apply-implementation-review.md",
            ".claude/commands/prepare-functional-review.md",
            ".claude/commands/apply-functional-review.md",
            ".claude/commands/prepare-review.md",
            "scripts/prepare-ai-review.sh",
            "scripts/validate_workflow_state.py",
            "scripts/workflow_state_test.py",
            "docs/ai-workflow/requirements/workflow-v2-1-core-ledger.md",
            # Concurrent-work-item artifacts (OPUS-R16-001, missing-test item
            # 121): the classification input is repository-wide, not scoped
            # to this work item, so a path any *other* work item's own
            # commands are documented to write must classify too -- not just
            # this milestone's own artifacts.
            "docs/ACTIVE_MILESTONE.md",
            "docs/ROADMAP.md",
            "docs/milestones/completed/milestone-8-execution.md",
            "docs/ai-workflow/archive/workflow-v2.1-core-final-state.json",
            # WF8b's own dry-run evidence and the synthetic item's artifact
            # tree (remediation: docs/ai-workflow/dry-run/ added to
            # PLAN_STAGE_EXCLUDED_PREFIXES after the real
            # docs/ai-workflow/dry-run/WF8B_SCENARIOS.md commit surfaced an
            # UnclassifiedPathError the classifier's prior exhaustive list
            # never covered).
            "docs/ai-workflow/dry-run/WF8B_SCENARIOS.md",
            "docs/ai-workflow/dry-run/v2-1-dry-run-plan.md",
        ]
        for path in known_paths:
            with self.subTest(path=path):
                result = wf.classify_path(
                    path, wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                    wf.PLAN_STAGE_EXCLUDED_PREFIXES,
                )
                self.assertIn(result, {"protected", "excluded"})

    def test_055_plan_stage_id_recomputes_across_a_bootstrap_checkpoint_commit(self):
        """OPUS-R10-001, missing-test item 55: the exact end-to-end scenario
        the reviewer reproduced -- a WF0 commit (plan docs) followed by a
        WF1a-shaped commit that adds a command file, a script, and the
        ledger -- must not make plan-stage `review_content_id` unevaluable."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            approval_digest, _ = repo.compute()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "WF0: bootstrap"], cwd=repo.root)

            (repo.root / ".claude" / "commands").mkdir(parents=True)
            (repo.root / ".claude" / "commands" / "bootstrap-workflow-v2.md").write_text("x\n")
            (repo.root / "scripts").mkdir(exist_ok=True)
            (repo.root / "scripts" / "validate_workflow_state.py").write_text("x\n")
            (repo.root / "docs" / "ai-workflow" / "requirements").mkdir(parents=True, exist_ok=True)
            (repo.root / "docs" / "ai-workflow" / "requirements" / "workflow-v2-1-core-ledger.md").write_text("x\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "WF1a"], cwd=repo.root)

            post_checkpoint_digest, _ = repo.compute()
            self.assertEqual(
                approval_digest, post_checkpoint_digest,
                "plan-approval durability must survive a checkpoint commit "
                "that adds implementation-phase artifacts",
            )

    def test_056_every_registry_declared_checkpoint_artifact_classifies(self):
        """Missing-test item 56 (best available proxy: the registry JSON does
        not yet declare per-checkpoint artifact paths -- WF1b's stated future
        scope -- so this exercises the concrete, currently-known set from
        test_096 as the exhaustiveness check available today)."""
        for path in [
            ".claude/commands/bootstrap-workflow-v2.md",
            "scripts/validate_workflow_state.py",
            "docs/ai-workflow/requirements/workflow-v2-1-core-ledger.md",
            "CLAUDE.md",
            "docs/ai-workflow/REVIEW_PROTOCOL.md",
            "docs/ai-workflow/MILESTONE_WORKFLOW.md",
        ]:
            with self.subTest(path=path):
                self.assertEqual(
                    wf.classify_path(
                        path, wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                        wf.PLAN_STAGE_EXCLUDED_PREFIXES,
                    ),
                    "excluded",
                )

    def test_101_full_checkpoint_sequence_leaves_plan_stage_id_unchanged(self):
        """Missing-test item 101 (OPUS-R14-001, acceptance criterion 2):
        simulates one commit per excluded artifact class the real
        17-checkpoint registry is known to create -- a command file, a
        script, a registry-directory sibling, a requirements-directory
        sibling, and (now that OPUS-R14-001/-009 moved it) the plan-review
        operator guide itself -- and asserts plan-stage `review_content_id`
        is identical to its approval-time value after every single one,
        not just after the whole sequence. Extends test_055's one-commit
        proxy to a full walk of every excluded prefix/path this milestone's
        own checkpoints are known to populate."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            approval_digest, _ = repo.compute()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "WF0: bootstrap"], cwd=repo.root)

            simulated_checkpoint_artifacts = [
                (".claude/commands", "bootstrap-workflow-v2.md"),
                (".claude/commands", "review-plan.md"),
                (".claude/commands", "record-manual-plan-review.md"),
                ("scripts", "validate_workflow_state.py"),
                ("scripts", "prepare-ai-review.sh"),
                ("docs/ai-workflow/requirements", "workflow-v2-1-core-ledger.md"),
                ("docs/ai-workflow/registry", "some-future-registry-artifact.json"),
                ("docs/ai-workflow", "PLAN_REVIEW_WORKFLOW.md"),
            ]
            for i, (directory, filename) in enumerate(simulated_checkpoint_artifacts):
                (repo.root / directory).mkdir(parents=True, exist_ok=True)
                (repo.root / directory / filename).write_text(f"checkpoint {i}\n")
                _run(["git", "add", "-A"], cwd=repo.root)
                _run(["git", "commit", "-q", "-m", f"simulated checkpoint {i}: {filename}"], cwd=repo.root)

                digest, _ = repo.compute()
                self.assertEqual(
                    approval_digest, digest,
                    f"plan-stage review_content_id changed after simulated "
                    f"checkpoint {i} ({directory}/{filename})",
                )

    def test_117_120_concurrent_work_item_writes_mid_sequence_do_not_raise_or_change_id(self):
        """Missing-test items 117-120 (OPUS-R16-001): the classification
        input is repository-wide (git diff/untracked over the whole
        worktree), not scoped to this work item, so a *different* work
        item's own documented write -- Milestone 8 adoption writing
        docs/ACTIVE_MILESTONE.md, /accept-milestone writing docs/ROADMAP.md
        or docs/milestones/, or this work item's own eventual
        process-completion archival under docs/ai-workflow/archive/ -- must
        neither raise nor change this work item's plan-stage
        review_content_id, even while this work item is itself mid-sequence
        (some checkpoints already committed, more remaining)."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            approval_digest, _ = repo.compute()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "WF0: bootstrap"], cwd=repo.root)

            # Simulate this work item already mid-sequence: one checkpoint
            # commit landed before the concurrent write below arrives.
            (repo.root / "scripts").mkdir(exist_ok=True)
            (repo.root / "scripts" / "validate_workflow_state.py").write_text("x\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "simulated checkpoint: WF1a"], cwd=repo.root)
            mid_sequence_digest, _ = repo.compute()
            self.assertEqual(approval_digest, mid_sequence_digest)

            concurrent_work_item_writes = [
                ("docs", "ACTIVE_MILESTONE.md"),  # item 117
                ("docs", "ROADMAP.md"),  # item 118
                ("docs/milestones/completed", "milestone-8-execution.md"),  # item 119
                ("docs/ai-workflow/archive", "workflow-v2.1-core-final-state.json"),  # item 120
            ]
            for i, (directory, filename) in enumerate(concurrent_work_item_writes):
                (repo.root / directory).mkdir(parents=True, exist_ok=True)
                (repo.root / directory / filename).write_text(f"concurrent write {i}\n")
                _run(["git", "add", "-A"], cwd=repo.root)
                _run(["git", "commit", "-q", "-m", f"concurrent work item write {i}: {filename}"], cwd=repo.root)

                digest, _ = repo.compute()
                self.assertEqual(
                    approval_digest, digest,
                    f"plan-stage review_content_id changed after a concurrent "
                    f"work item's write to {directory}/{filename}",
                )

    def test_057_a_genuinely_unknown_path_still_fails_closed(self):
        """Missing-test item 57: widening the lists must not turn the
        classifier into a rubber stamp. `app/Foo.kt` was this test's
        original example; OPUS-R18-004 excluded `app/` as a genuine
        concurrent-work-item path, so a path outside every named set is
        used here instead (item 135)."""
        with self.assertRaises(wf.UnclassifiedPathError):
            wf.classify_path(
                "totally_unmapped_surprise/Foo.kt", wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            )

    def test_technical_decisions_is_protected_not_excluded(self):
        """OPUS-R8-008 bonus-answer 5: a technical-decision entry created
        specifically to satisfy a review finding is durable design content,
        not incidental housekeeping -- it must be protected, so editing it
        changes review_content_id."""
        self.assertIn("docs/TECHNICAL_DECISIONS.md", wf.PLAN_STAGE_PROTECTED)
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            repo.write_plan_docs(decisions_text="decisions v2 -- changed\n")
            digest2, _ = repo.compute()
            self.assertNotEqual(digest1, digest2)

    def test_079_plan_review_workflow_guide_is_excluded_not_protected(self):
        """Missing-test item 79, corrected per OPUS-R14-001/-009: the
        concise two-stage plan-review operator guide's final path must
        classify as excluded, not protected, so WF4a-iv's creation of it
        never stales a plan approval -- pre-declaring it protected while
        unwritten (GPT-R11-008's original resolution) made D-States'
        checkpoint-invariance claim false the moment WF4a-iv actually wrote
        it, since that write is itself a checkpoint touching a protected
        path. Excluding it (like MILESTONE_WORKFLOW.md/REVIEW_PROTOCOL.md)
        means the operator guide is treated as documentation of the
        approved design, not the design itself."""
        path = "docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md"
        self.assertNotIn(path, wf.PLAN_STAGE_PROTECTED)
        self.assertIn(path, wf.PLAN_STAGE_EXCLUDED_PATHS)
        self.assertEqual(
            wf.classify_path(
                path, wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            ),
            "excluded",
        )

    def test_wf8b_dry_run_prefix_is_excluded(self):
        """Remediation test: `docs/ai-workflow/dry-run/WF8B_SCENARIOS.md`
        (committed by WF8b's evidence-prep commit) raised
        `UnclassifiedPathError` because the exhaustive prefix list predated
        that directory. `docs/ai-workflow/dry-run/` was added to
        `PLAN_STAGE_EXCLUDED_PREFIXES` -- it is WF8b's own dry-run evidence
        and the throwaway synthetic work item's artifact tree, not design
        content for this work item's own plan."""
        path = "docs/ai-workflow/dry-run/WF8B_SCENARIOS.md"
        self.assertNotIn(path, wf.PLAN_STAGE_PROTECTED)
        self.assertEqual(
            wf.classify_path(
                path, wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            ),
            "excluded",
        )

    def test_wf8b_dry_run_prefix_match_is_precise_not_a_substring_match(self):
        """A lookalike path that merely shares the `dry-run` substring but is
        not actually under the `docs/ai-workflow/dry-run/` directory must
        still fail closed -- proves the exclusion is a real path-segment
        prefix, not a loose substring match that could be widened by
        accident."""
        with self.assertRaises(wf.UnclassifiedPathError):
            wf.classify_path(
                "docs/ai-workflow/dry-runner/x.md", wf.PLAN_STAGE_PROTECTED,
                wf.PLAN_STAGE_EXCLUDED_PATHS, wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            )


class TestExclusionListStructure(unittest.TestCase):
    """OPUS-R8-006: exact-path vs. directory-prefix exclusions are
    separated and validated; OPUS-R8-007: every entry is reachable;
    OPUS-R8-008: every entry carries a justification."""

    def test_036_excluded_path_with_appended_suffix_fails_closed(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            (repo.root / ".gitignore.bak").write_text("x\n")
            with self.assertRaises(wf.UnclassifiedPathError):
                repo.compute()

    def test_037_malformed_prefix_constant_rejected_at_import(self):
        with self.assertRaises(wf.MalformedExclusionConstantError):
            wf._validate_exclusion_prefixes({"scripts": "missing trailing slash"})
        # a well-formed prefix is accepted without raising
        wf._validate_exclusion_prefixes({"scripts/": "well-formed"})

    def test_039_every_exclusion_entry_is_reachable(self):
        """OPUS-R8-007: the previous suite proved the exclusion list via a
        scratch repo with no matching `.gitignore` entry, which passed for
        a different reason than production (invisibility, not exclusion).
        Every current exact-path entry must actually classify as
        'excluded' when presented directly."""
        for path in wf.PLAN_STAGE_EXCLUDED_PATHS:
            with self.subTest(path=path):
                result = wf.classify_path(
                    path, wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                    wf.PLAN_STAGE_EXCLUDED_PREFIXES,
                )
                self.assertEqual(result, "excluded")

    def test_040_every_exclusion_entry_has_a_justification(self):
        for path, reason in wf.PLAN_STAGE_EXCLUDED_PATHS.items():
            with self.subTest(path=path):
                self.assertTrue(reason and reason.strip())
        for prefix, reason in wf.PLAN_STAGE_EXCLUDED_PREFIXES.items():
            with self.subTest(prefix=prefix):
                self.assertTrue(reason and reason.strip())

    def test_gitignored_path_in_repo_with_real_gitignore_is_invisible_not_excluded(self):
        """OPUS-R8-007: state and test the actual production path -- a
        gitignored file is invisible to classification because it never
        appears in untracked-path enumeration, not because an exclusion
        entry matches it."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            (repo.root / ".gitignore").write_text("ignored_dir/\n")
            _run(["git", "add", ".gitignore"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "add gitignore"], cwd=repo.root)
            digest1, _ = repo.compute()
            (repo.root / "ignored_dir").mkdir()
            (repo.root / "ignored_dir" / "secret.txt").write_text("hidden\n")
            digest2, _ = repo.compute()
            self.assertEqual(digest1, digest2)


class TestDeterministicBase(unittest.TestCase):
    """PROTO-R7-002: identity must not depend on core.abbrev or on how the
    base commit was spelled."""

    def test_full_short_and_HEAD_base_spellings_agree(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            id_full, _ = repo.compute()
            id_short, _ = repo.compute(base=repo.base[:10])
            self.assertEqual(id_full, id_short)

    def test_core_abbrev_does_not_affect_identity(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            _run(["git", "config", "core.abbrev", "40"], cwd=repo.root)
            id_abbrev40, _ = repo.compute()
            _run(["git", "config", "core.abbrev", "4"], cwd=repo.root)
            id_abbrev4, _ = repo.compute()
            self.assertEqual(id_abbrev40, id_abbrev4)

    def test_ambiguous_base_fails_before_manifest_construction(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            with self.assertRaises(wf.AmbiguousBaseError):
                wf.resolve_base(repo.root, "not-a-real-ref")

    def test_091_one_byte_edit_changes_review_content_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            repo.write_plan_docs(plan_text="plan v1 X\n")
            digest2, _ = repo.compute()
            self.assertNotEqual(digest1, digest2)


class TestWorktreeCommitParity(unittest.TestCase):
    """PROTO-R7-003: worktree-source and commit-source manifests must be
    genuinely compared, not merely each internally self-consistent."""

    def test_complete_manifests_are_equal_after_approval_commit(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            worktree_id, worktree_projection = repo.compute()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "plan-approval"], cwd=repo.root)
            commit_id, commit_projection = repo.compute_at_commit(repo.head())
            self.assertEqual(
                worktree_projection["review_content_manifest"],
                commit_projection["review_content_manifest"],
            )
            self.assertEqual(worktree_id, commit_id)

    def test_commit_anchored_manifest_is_immune_to_a_later_uncommitted_edit(self):
        """`workflow-v2-3`'s own CP1 missing-test item (revision 5, round 4
        B2/I1): pins the *class* of defect the `_blob_at_commit` repair in
        both real-repository `_demo_test.py` files fixes, not only this
        repository's own instance of it. A manifest computed at a fixed
        commit A must report commit A's own blob for a protected path even
        after that same path is edited in the working tree without being
        committed -- a commit-anchored recompute must never silently read
        through to dirty worktree content."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "commit A"], cwd=repo.root)
            commit_a = repo.head()
            commit_a_id, commit_a_projection = repo.compute_at_commit(commit_a)
            plan_entry_a = [
                e for e in commit_a_projection["review_content_manifest"]
                if e["path"].endswith("WORKFLOW_V2_PLAN.md")
            ][0]
            expected_blob_at_a = wf._hash_object(repo.root, plan_entry_a["path"])

            # Edit the working tree without committing -- the hazard.
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text("plan v1 DIRTY EDIT\n")
            dirty_blob = wf._hash_object(repo.root, plan_entry_a["path"])
            self.assertNotEqual(dirty_blob, expected_blob_at_a)

            recomputed_id, recomputed_projection = repo.compute_at_commit(commit_a)
            recomputed_entry = [
                e for e in recomputed_projection["review_content_manifest"]
                if e["path"].endswith("WORKFLOW_V2_PLAN.md")
            ][0]
            self.assertEqual(recomputed_entry["blob"], expected_blob_at_a)
            self.assertNotEqual(recomputed_entry["blob"], dirty_blob)
            self.assertEqual(recomputed_id, commit_a_id)

    def test_tracked_unchanged_protected_path_identical_in_both_modes(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "initial"], cwd=repo.root)
            repo.write_plan_docs(plan_text="plan v2\n")  # audit/decisions unchanged
            worktree_manifest = wf.compute_review_content_manifest_plan_stage_worktree(repo.root)
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "revise"], cwd=repo.root)
            commit_manifest = wf.compute_review_content_manifest_plan_stage_commit(
                repo.root, repo.head()
            )
            self.assertEqual(worktree_manifest, commit_manifest)
            audit_entries = [e for e in commit_manifest if e["path"].endswith("AUDIT.md")]
            self.assertEqual(len(audit_entries), 1)
            self.assertTrue(audit_entries[0]["exists"])
            self.assertNotIn("status", audit_entries[0])

    def test_raw_diff_blob_is_never_a_usable_source_for_untracked_files(self):
        """Renamed per OPUS-R8-016: the previous name
        (`test_removing_hash_object_substitution_would_break_parity`)
        claimed to test parity, but the assertions only show the raw diff
        is empty and blobs are non-zero -- the actual parity guarantee is
        `test_complete_manifests_are_equal_after_approval_commit`, verified
        load-bearing by mutation (replacing `_hash_object` with a
        zero-blob stub fails four tests)."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            raw = subprocess.run(
                ["git", "diff", "--raw", "-z", "--find-renames", "--abbrev=40", repo.base],
                cwd=repo.root, check=True, capture_output=True, text=True,
            ).stdout
            self.assertEqual(raw, "")
            real_manifest = wf.compute_review_content_manifest_plan_stage_worktree(repo.root)
            for entry in real_manifest:
                if not entry["exists"]:
                    # The pre-declared, not-yet-written
                    # PLAN_REVIEW_WORKFLOW.md guide path (GPT-R11-008) has
                    # no blob to hash -- correctly None, not the assertion
                    # this test makes about real files' blobs.
                    continue
                self.assertIsNotNone(entry["blob"])
                self.assertNotEqual(entry["blob"], "0" * 40)


class TestCommitSourceClassification(unittest.TestCase):
    """OPUS-R8-005: the commit-source entry point must enforce the same
    fail-closed classification precondition as the worktree entry point."""

    def test_034_unclassified_path_at_approval_commit_fails_closed(self):
        """`app/` is now an excluded prefix (OPUS-R18-004); a genuinely
        novel path is used here instead so this still demonstrates
        fail-closed behavior."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            (repo.root / "totally_unmapped_surprise").mkdir()
            (repo.root / "totally_unmapped_surprise" / "unclassified.kt").write_text("surprise\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "sneaks in an unclassified file"], cwd=repo.root)
            with self.assertRaises(wf.UnclassifiedPathError):
                repo.compute_at_commit(repo.head())

    def test_035_both_entry_points_reject_the_same_unclassified_input(self):
        """`app/` is now an excluded prefix (OPUS-R18-004); a genuinely
        novel path is used here instead so this still demonstrates
        fail-closed behavior."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            (repo.root / "totally_unmapped_surprise").mkdir()
            (repo.root / "totally_unmapped_surprise" / "unclassified.kt").write_text("surprise\n")
            with self.assertRaises(wf.UnclassifiedPathError):
                repo.compute()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "commit it"], cwd=repo.root)
            with self.assertRaises(wf.UnclassifiedPathError):
                repo.compute_at_commit(repo.head())


class TestFileModeParity(unittest.TestCase):
    """OPUS-R8-003: worktree/commit parity must hold under both
    `core.fileMode` settings, matching real Git semantics."""

    def test_028_parity_holds_under_core_fileMode_false_with_a_chmod_applied(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            _run(["git", "config", "core.fileMode", "false"], cwd=repo.root)
            plan_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            os.chmod(plan_path, 0o755)
            worktree_id, worktree_projection = repo.compute()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "plan-approval"], cwd=repo.root)
            commit_id, commit_projection = repo.compute_at_commit(repo.head())
            self.assertEqual(
                worktree_projection["review_content_manifest"],
                commit_projection["review_content_manifest"],
            )
            self.assertEqual(worktree_id, commit_id)
            plan_entry = [
                e for e in worktree_projection["review_content_manifest"]
                if e["path"].endswith("PLAN.md")
            ][0]
            self.assertEqual(plan_entry["mode"], "100644")

    def test_029_review_content_id_equal_under_both_fileMode_settings_with_no_chmod(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            _run(["git", "config", "core.fileMode", "true"], cwd=repo.root)
            id_true, _ = repo.compute()
            _run(["git", "config", "core.fileMode", "false"], cwd=repo.root)
            id_false, _ = repo.compute()
            self.assertEqual(id_true, id_false)

    def test_030_genuine_mode_change_reflected_identically_under_fileMode_true(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            _run(["git", "config", "core.fileMode", "true"], cwd=repo.root)
            plan_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            os.chmod(plan_path, 0o755)
            worktree_id, worktree_projection = repo.compute()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "plan-approval"], cwd=repo.root)
            commit_id, commit_projection = repo.compute_at_commit(repo.head())
            plan_entry = [
                e for e in worktree_projection["review_content_manifest"]
                if e["path"].endswith("PLAN.md")
            ][0]
            self.assertEqual(plan_entry["mode"], "100755")
            self.assertEqual(worktree_id, commit_id)
            self.assertEqual(
                worktree_projection["review_content_manifest"],
                commit_projection["review_content_manifest"],
            )


class TestSymlinkAndUnsupportedEntries(unittest.TestCase):
    """OPUS-R8-004: a symlink to a directory must fail closed; ordering
    (`is_symlink()` before `is_dir()`) is what makes that reachable."""

    def test_031_symlink_to_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "current"
            bundle.mkdir()
            (bundle / "MANIFEST.md").write_text("bundle_id: " + FAKE_ID_A + "\n")
            real_dir = bundle / "realdir"
            real_dir.mkdir()
            (real_dir / "secret.txt").write_text("hidden\n")
            (bundle / "linkdir").symlink_to(real_dir, target_is_directory=True)
            with self.assertRaises(wf.UnsupportedPathTypeError):
                wf.compute_bundle_id(bundle)

    def test_symlink_to_file_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "current"
            bundle.mkdir()
            (bundle / "MANIFEST.md").write_text("bundle_id: " + FAKE_ID_A + "\n")
            (bundle / "REVIEW_REQUEST.md").write_text("stage: plan\n")
            (bundle / "sneaky_link").symlink_to(bundle / "REVIEW_REQUEST.md")
            with self.assertRaises(wf.UnsupportedPathTypeError):
                wf.compute_bundle_id(bundle)

    def test_032_fifo_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "current"
            bundle.mkdir()
            (bundle / "MANIFEST.md").write_text("bundle_id: " + FAKE_ID_A + "\n")
            os.mkfifo(bundle / "a_fifo")
            with self.assertRaises(wf.UnsupportedPathTypeError):
                wf.compute_bundle_id(bundle)

    def test_033_real_nested_directory_still_contributes_its_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "current"
            bundle.mkdir()
            (bundle / "MANIFEST.md").write_text("bundle_id: " + FAKE_ID_A + "\n")
            nested = bundle / "files" / "docs"
            nested.mkdir(parents=True)
            (nested / "a.md").write_text("content\n")
            _, entries = wf.compute_bundle_id(bundle)
            self.assertIn("files/docs/a.md", entries)


class TestExcludedFieldWriteDoesNotAffectIdentity(unittest.TestCase):
    def test_093_state_file_write_does_not_change_review_content_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(
                '{"plan_approval": {"status": "CURRENT"}}\n'
            )
            digest2, _ = repo.compute()
            self.assertEqual(digest1, digest2)


class TestRegistryAndMappingAreProtected(unittest.TestCase):
    """GPT-R9-003, missing-test items 6-7: the registry and requirements-
    mapping JSON files are protected -- editing either changes
    review_content_id, so plan approval cannot be granted over one version
    and silently retargeted at another."""

    def test_editing_registry_json_changes_review_content_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            repo.write_plan_docs(registry_text='{"checkpoints": [{"id": "WF1a"}]}\n')
            digest2, _ = repo.compute()
            self.assertNotEqual(digest1, digest2)

    def test_editing_mapping_json_changes_review_content_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            repo.write_plan_docs(mapping_text='{"requirements": {"WFR-01": {}}}\n')
            digest2, _ = repo.compute()
            self.assertNotEqual(digest1, digest2)


class TestLedgerDocIsExcludedFromPlanStageIdentity(unittest.TestCase):
    """WF4b, missing-test item 6: the mutable
    `workflow-v2-1-core-ledger.md` execution ledger (D4b) is physically
    separate from the protected, machine-readable
    `workflow-v2-1-core-mapping.json` -- creating or editing the ledger
    doc must never change plan-stage `review_content_id`, since it is
    appended to on every checkpoint completion and must never stale a
    plan approval or require a fresh review round."""

    def _ledger_path(self, repo):
        return (
            repo.root / "docs" / "ai-workflow" / "requirements"
            / "workflow-v2-1-core-ledger.md"
        )

    def test_creating_ledger_doc_does_not_change_review_content_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            self._ledger_path(repo).write_text("# ledger v1\n")
            digest2, _ = repo.compute()
            self.assertEqual(digest1, digest2)

    def test_editing_ledger_doc_does_not_change_review_content_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            self._ledger_path(repo).write_text("# ledger v1\n")
            digest1, _ = repo.compute()
            self._ledger_path(repo).write_text("# ledger v1\n\nappended entry\n")
            digest2, _ = repo.compute()
            self.assertEqual(digest1, digest2)


class TestIdentityScalars(unittest.TestCase):
    """OPUS-R8-010: no identity-bearing scalar has a default; `work_item_id`
    is slug-validated; a differing declared revision changes the ID even
    when file content is identical."""

    def test_043_missing_scalar_raises(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            with self.assertRaises(TypeError):
                wf.compute_review_content_id_plan_stage(
                    repo.root, repo.base,
                    work_item_type="process", work_item_id="workflow-v2-1-core",
                )  # plan_revision omitted

    def test_044_invalid_work_item_id_slug_fails_closed(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            for bad_id in ["Has-Upper", "has space", "../traversal", "", "a" * 65]:
                with self.subTest(bad_id=bad_id):
                    with self.assertRaises(wf.InvalidWorkItemIdError):
                        repo.compute(work_item_id=bad_id)

    def test_two_documents_same_content_different_declared_revision_differ(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest_rev7, _ = repo.compute(plan_revision=7)
            digest_rev8, _ = repo.compute(plan_revision=8)
            self.assertNotEqual(digest_rev7, digest_rev8)

    def test_gpt_r9_014_invalid_work_item_type_rejected(self):
        """Missing-test item 25: work_item_type must be one of the
        controlled vocabulary, checked before hashing."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            for bad_type in ["Process", "PRODUCT", "synthetic", "process ", ""]:
                with self.subTest(bad_type=bad_type):
                    with self.assertRaises(wf.InvalidWorkItemTypeError):
                        repo.compute(work_item_type=bad_type)
            # valid values are accepted
            repo.compute(work_item_type="process")
            repo.compute(work_item_type="product")


class TestWF8bSyntheticWorkItemIdCanonicalization(unittest.TestCase):
    """WF8b entry setup found the plan's literal synthetic work-item name
    (`v2.1-dry-run`, D-Self-Governance/D1) violates D1's own
    `WORK_ITEM_ID_RE` grammar (no dot allowed) -- both are already-approved
    protected-path sources. Resolved by canonicalizing the machine
    `work_item_id` to `v2-1-dry-run` without widening the grammar; this
    pins both halves of that decision so a future edit can't silently
    re-admit the dotted literal or reject the canonical one."""

    def test_canonical_synthetic_id_is_accepted(self):
        wf.validate_work_item_id("v2-1-dry-run")  # must not raise

    def test_dotted_literal_from_plan_prose_remains_rejected(self):
        with self.assertRaises(wf.InvalidWorkItemIdError):
            wf.validate_work_item_id("v2.1-dry-run")


class TestNonStringIdentityValuesFailClosed(unittest.TestCase):
    """Salvage audit `I2`: `validate_work_item_id`/`validate_work_item_type`
    are reached with values read straight out of an unvalidated
    `WORKFLOW_STATE.json` by every implementation-stage reader
    (`write_manifest_with_verified_identifiers_implementation_stage_for_work_item`,
    `workflow_state.approval_review_content_id`,
    `workflow_state.resolve_bundle_generation_outcome`, and therefore
    `scripts/prepare-ai-review.sh`). A corrupt, hand-edited or badly
    merged JSON value that is not a string used to escape as a raw
    `TypeError` from `re.match`/`frozenset.__contains__` instead of this
    module's own documented error -- the exact class `workflow-v2-3-1`
    CP1's round-1 remediation closed for `resolve_plan_stage_metadata`'s
    own inline check but not for the shared validators behind it."""

    UNHASHABLE_AND_WRONG_TYPE_VALUES = (["process"], {"type": "process"}, 3, None, True)

    def test_non_string_work_item_type_raises_the_documented_error(self):
        for value in self.UNHASHABLE_AND_WRONG_TYPE_VALUES:
            with self.subTest(value=value):
                with self.assertRaises(wf.InvalidWorkItemTypeError):
                    wf.validate_work_item_type(value)

    def test_non_string_work_item_id_raises_the_documented_error(self):
        for value in self.UNHASHABLE_AND_WRONG_TYPE_VALUES:
            with self.subTest(value=value):
                with self.assertRaises(wf.InvalidWorkItemIdError):
                    wf.validate_work_item_id(value)

    def test_valid_string_values_are_unaffected(self):
        wf.validate_work_item_type("process")
        wf.validate_work_item_type("product")
        wf.validate_work_item_id("wi")
        with self.assertRaises(wf.InvalidWorkItemTypeError):
            wf.validate_work_item_type("widget")
        with self.assertRaises(wf.InvalidWorkItemIdError):
            wf.validate_work_item_id("Not A Slug")


class TestProtectedAndExclusionSetsInProjection(unittest.TestCase):
    """OPUS-R8-014: the protected and exclusion sets are themselves part of
    the hashed projection."""

    def test_049_protected_set_edit_with_unchanged_content_changes_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            digest1, _ = repo.compute()
            shrunk_protected = frozenset(
                p for p in wf.PLAN_STAGE_PROTECTED if not p.endswith("AUDIT.md")
            )
            digest2, _ = repo.compute(protected=shrunk_protected)
            self.assertNotEqual(digest1, digest2)

    def test_exclusion_list_edit_changes_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest1, _ = repo.compute()
            from types import MappingProxyType
            shrunk_excluded = MappingProxyType(
                {k: v for k, v in wf.PLAN_STAGE_EXCLUDED_PATHS.items() if k != ".gitignore"}
            )
            digest2, _ = repo.compute(excluded_paths=shrunk_excluded)
            self.assertNotEqual(digest1, digest2)

    def test_050_removed_protected_path_distinguishable_from_deleted_file(self):
        """Corrected per OPUS-R14-001/-006: a plan-stage protected path
        that still exists (Case 1: removed from the protected set) and one
        that no longer exists while still protected (Case 2: deleted) are
        distinguishable -- but Case 2 now fails closed rather than
        producing a silent `exists: False` tombstone, since a plan-stage
        protected path is always supposed to be real, reviewable content."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            _, projection_with_all = repo.compute()
            self.assertEqual(len(projection_with_all["review_content_manifest"]), 5)

            # Case 1: path removed from the protected set entirely -- it
            # simply doesn't appear in the manifest, no error.
            shrunk_protected = frozenset(
                p for p in wf.PLAN_STAGE_PROTECTED if not p.endswith("AUDIT.md")
            )
            _, projection_shrunk_set = repo.compute(protected=shrunk_protected)
            self.assertEqual(len(projection_shrunk_set["review_content_manifest"]), 4)

            # Case 2: path stays protected, but the file itself is deleted
            # -- plan-stage computation fails closed, naming the path
            # (OPUS-R14-001/-006), rather than returning a usable identity
            # over content nobody can actually review.
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_AUDIT.md").unlink()
            with self.assertRaises(wf.AbsentProtectedPathError) as ctx:
                repo.compute()
            self.assertIn("AUDIT.md", str(ctx.exception))

    def test_102_absent_protected_path_fails_closed_at_plan_stage(self):
        """Missing-test item 102 (OPUS-R14-001/-006): both the worktree-
        source and commit-source plan-stage manifest builders refuse to
        compute an identity over a protected-but-absent path, naming which
        path is missing -- and the two snapshot sources are independent
        (an uncommitted worktree deletion doesn't affect the commit read,
        and vice versa)."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            committed_head = repo.head()

            (repo.root / "docs" / "TECHNICAL_DECISIONS.md").unlink()
            with self.assertRaises(wf.AbsentProtectedPathError) as ctx_worktree:
                wf.compute_review_content_manifest_plan_stage_worktree(repo.root)
            self.assertIn("TECHNICAL_DECISIONS.md", str(ctx_worktree.exception))

            # The deletion above is uncommitted -- the commit-source read
            # of the still-intact prior commit is unaffected.
            manifest_commit = wf.compute_review_content_manifest_plan_stage_commit(
                repo.root, committed_head,
            )
            self.assertEqual(len(manifest_commit), 5)

            # Now commit the deletion and confirm the commit-source read
            # fails closed too, against the commit that actually lacks it.
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "delete decisions doc"], cwd=repo.root)
            deleted_head = repo.head()
            with self.assertRaises(wf.AbsentProtectedPathError) as ctx_commit:
                wf.compute_review_content_manifest_plan_stage_commit(
                    repo.root, deleted_head,
                )
            self.assertIn("TECHNICAL_DECISIONS.md", str(ctx_commit.exception))


class TestMutableDefaultArgumentSafety(unittest.TestCase):
    """OPUS-R8-015: shared defaults must be immutable, so no caller can
    corrupt them for every subsequent call."""

    def test_protected_and_exclusion_constants_are_immutable(self):
        self.assertIsInstance(wf.PLAN_STAGE_PROTECTED, frozenset)
        with self.assertRaises(AttributeError):
            wf.PLAN_STAGE_EXCLUDED_PATHS.update({"x": "y"})
        with self.assertRaises(AttributeError):
            wf.PLAN_STAGE_EXCLUDED_PREFIXES.update({"x": "y"})


class TestModeDetectionIsStatBased(unittest.TestCase):
    """GPT-R9-009, missing-test item 22: mode must come from the file's own
    stored mode bits, not from `os.access()`, which reflects the current
    process's *effective* access and can differ by user/ACL for identical
    stored bits."""

    def test_owner_executable_reads_stat_bits_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            f = tmp / "a.txt"
            f.write_text("x\n")
            f.chmod(0o644)
            self.assertFalse(wf._owner_executable(f.stat().st_mode))
            f.chmod(0o744)
            self.assertTrue(wf._owner_executable(f.stat().st_mode))
            # Independent of os.access's *effective*-access semantics: the
            # function never calls os.access at all -- confirmed by
            # construction (grep the implementation), and here by checking
            # its result depends only on st_mode, not on the live
            # X_OK-effective-access call, which we deliberately do not
            # invoke or need for the assertions above.

    def test_bundle_entries_use_stat_based_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "current"
            bundle.mkdir()
            (bundle / "MANIFEST.md").write_text("bundle_id: " + FAKE_ID_A + "\n")
            (bundle / "REVIEW_REQUEST.md").write_text("stage: plan\n")
            (bundle / "REVIEW_REQUEST.md").chmod(0o744)
            _, entries = wf.compute_bundle_id(bundle)
            self.assertEqual(entries["REVIEW_REQUEST.md"]["mode"], "100755")


class TestVerificationIsReadOnly(unittest.TestCase):
    """GPT-R9-010, missing-test item 23: importing/running this module to
    verify a bundle must never write files (bytecode) inside the bundle
    directory it is hashing."""

    def test_dont_write_bytecode_is_set_on_import(self):
        self.assertTrue(sys.dont_write_bytecode)

    def test_verifying_a_bundle_creates_no_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "current"
            bundle.mkdir()
            (bundle / "MANIFEST.md").write_text("bundle_id: " + FAKE_ID_A + "\n")
            (bundle / "REVIEW_REQUEST.md").write_text("stage: plan\n")
            before = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*")}
            wf.compute_bundle_id(bundle)
            wf.compute_bundle_id(bundle)
            after = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*")}
            self.assertEqual(before, after)


class TestBundleId(unittest.TestCase):
    """PROTO-R7-004/006/007, OPUS-R8-001/002/004/009/013/017: bundle_id
    must exclude only the self-referential `bundle_id:` field wherever it
    appears, normalize only the true timestamp header line (failing closed
    if it's missing/duplicated), represent file mode, use POSIX-style
    relative paths, and require MANIFEST.md."""

    def _make_bundle(self, tmp: Path, generated="2026-07-28T18:31:55Z", bundle_id=None):
        bundle = tmp / "current"
        bundle.mkdir()
        (bundle / "REVIEW_REQUEST.md").write_text("stage: plan\n")
        (bundle / "CHANGED_FILES.txt").write_text(
            f"branch: x\nbase: y\nhead: z\nstage: plan\ngenerated: {generated}\n"
        )
        manifest_line = f"bundle_id: {bundle_id}\n" if bundle_id else ""
        (bundle / "MANIFEST.md").write_text(f"{manifest_line}protected_paths: [a.md, b.md]\n")
        return bundle

    def test_110_idempotent_across_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            id1, _ = wf.compute_bundle_id(bundle)
            (bundle / "CHANGED_FILES.txt").write_text(
                "branch: x\nbase: y\nhead: z\nstage: plan\ngenerated: 2026-07-28T19:00:00Z\n"
            )
            id2, _ = wf.compute_bundle_id(bundle)
            self.assertEqual(id1, id2)

    def test_wrapper_word_edit_changes_bundle_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            id1, _ = wf.compute_bundle_id(bundle)
            (bundle / "REVIEW_REQUEST.md").write_text("stage: plan (revised)\n")
            id2, _ = wf.compute_bundle_id(bundle)
            self.assertNotEqual(id1, id2)

    def test_111_manifest_bundle_id_field_excluded_but_rest_of_file_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "MANIFEST.md").write_text(
                f"bundle_id: {FAKE_ID_A}\nprotected_paths: [a.md, b.md]\n"
            )
            id_before, _ = wf.compute_bundle_id(bundle)
            (bundle / "MANIFEST.md").write_text(
                f"bundle_id: {FAKE_ID_B}\nprotected_paths: [a.md, b.md]\n"
            )
            id_bundle_id_only_changed, _ = wf.compute_bundle_id(bundle)
            self.assertEqual(
                id_before, id_bundle_id_only_changed,
                "changing only the self-referential bundle_id field must not "
                "change the identifier it is excluded from",
            )
            (bundle / "MANIFEST.md").write_text(
                f"bundle_id: {FAKE_ID_B}\nprotected_paths: [a.md, b.md, c.md]\n"
            )
            id_other_field_changed, _ = wf.compute_bundle_id(bundle)
            self.assertNotEqual(
                id_before, id_other_field_changed,
                "changing non-ID manifest content must change the identifier "
                "(PROTO-R7-004: the whole file must not be excluded)",
            )

    def test_gpt_r9_001_a_top_level_bundle_id_field_outside_manifest_fails(self):
        """GPT-R9-001, missing-test item 1: only MANIFEST.md may report the
        bundle's own identity. Round 8's blanket exclusion made a wrong or
        stale `bundle_id:`-shaped line in TEST_RESULTS.md invisible to the
        identifier instead of flagged -- confirmed live in round 9's own
        reviewed bundle. It must now fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "TEST_RESULTS.md").write_text(f"All tests passed.\nbundle_id: {FAKE_ID_A}\n")
            with self.assertRaises(wf.ForeignBundleIdFieldError):
                wf.compute_bundle_id(bundle)

    def test_gpt_r9_001_item_2_changing_a_bundle_id_looking_line_still_fails(self):
        """Missing-test item 2: whether the specific hex value is stale,
        fresh, or all-zeroes, a conforming line outside MANIFEST.md always
        fails -- there is no value for which it is silently accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "TEST_RESULTS.md").write_text(f"All tests passed.\nbundle_id: {FAKE_ID_A}\n")
            with self.assertRaises(wf.ForeignBundleIdFieldError):
                wf.compute_bundle_id(bundle)
            (bundle / "TEST_RESULTS.md").write_text(f"All tests passed.\nbundle_id: {'0' * 64}\n")
            with self.assertRaises(wf.ForeignBundleIdFieldError):
                wf.compute_bundle_id(bundle)

    def test_gpt_r9_001_item_3_only_the_manifest_self_field_is_excluded(self):
        """Missing-test item 3, positive case: MANIFEST.md's own field is
        still excluded (idempotent across a value-only change), while the
        exact same line in REVIEW_REQUEST.md or PLAN.md fails."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp, bundle_id=FAKE_ID_A)
            id1, _ = wf.compute_bundle_id(bundle)
            (bundle / "MANIFEST.md").write_text(
                f"bundle_id: {FAKE_ID_B}\nprotected_paths: [a.md, b.md]\n"
            )
            id2, _ = wf.compute_bundle_id(bundle)
            self.assertEqual(id1, id2, "MANIFEST.md's own field must still be excluded")
            for other in ("REVIEW_REQUEST.md", "PLAN.md"):
                with self.subTest(other=other):
                    (bundle / other).write_text(f"bundle_id: {FAKE_ID_A}\n")
                    with self.assertRaises(wf.ForeignBundleIdFieldError):
                        wf.compute_bundle_id(bundle)
                    (bundle / other).unlink()

    def test_023_bundle_missing_required_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = tmp / "current"
            bundle.mkdir()
            (bundle / "REVIEW_REQUEST.md").write_text("stage: plan\n")
            with self.assertRaises(wf.MissingRequiredBundleFileError):
                wf.compute_bundle_id(bundle)

    def test_025_lf_vs_crlf_variant_of_same_logical_file_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "REVIEW_REQUEST.md").write_bytes(b"line one\nline two\n")
            id_lf, _ = wf.compute_bundle_id(bundle)
            (bundle / "REVIEW_REQUEST.md").write_bytes(b"line one\r\nline two\r\n")
            id_crlf, _ = wf.compute_bundle_id(bundle)
            self.assertNotEqual(id_lf, id_crlf)

    def test_026_trailing_newline_presence_or_absence_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "REVIEW_REQUEST.md").write_bytes(b"stage: plan\n")
            id_with_nl, _ = wf.compute_bundle_id(bundle)
            (bundle / "REVIEW_REQUEST.md").write_bytes(b"stage: plan")
            id_without_nl, _ = wf.compute_bundle_id(bundle)
            self.assertNotEqual(id_with_nl, id_without_nl)

    def test_027_non_utf8_byte_difference_is_preserved_not_collided(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "CHANGED_FILES.txt").write_bytes(
                b"branch: x\nbase: y\nhead: z\nstage: plan\n"
                b"generated: 2026-07-28T18:31:55Z\n\npath-with-byte: \xff\n"
            )
            id_ff, _ = wf.compute_bundle_id(bundle)
            (bundle / "CHANGED_FILES.txt").write_bytes(
                b"branch: x\nbase: y\nhead: z\nstage: plan\n"
                b"generated: 2026-07-28T18:31:55Z\n\npath-with-byte: \xfe\n"
            )
            id_fe, _ = wf.compute_bundle_id(bundle)
            self.assertNotEqual(id_ff, id_fe)

    def test_normalizer_never_raises_on_non_utf8_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "REVIEW_REQUEST.md").write_bytes(b"stage: plan\n\xff\xfe binary noise\n")
            # must not raise
            wf.compute_bundle_id(bundle)

    def test_generated_prefixed_content_outside_header_line_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "CHANGED_FILES.txt").write_text(
                "branch: x\nbase: y\nhead: z\nstage: plan\n"
                "generated: 2026-07-28T18:31:55Z\n\n"
                "generated: this line is real evidence, not the timestamp\n"
            )
            id1, _ = wf.compute_bundle_id(bundle)
            (bundle / "CHANGED_FILES.txt").write_text(
                "branch: x\nbase: y\nhead: z\nstage: plan\n"
                "generated: 2026-07-28T19:00:00Z\n\n"
                "generated: this line is real evidence, not the timestamp\n"
            )
            id2, _ = wf.compute_bundle_id(bundle)
            self.assertEqual(id1, id2)
            (bundle / "CHANGED_FILES.txt").write_text(
                "branch: x\nbase: y\nhead: z\nstage: plan\n"
                "generated: 2026-07-28T19:00:00Z\n\n"
                "generated: this line CHANGED and must affect identity\n"
            )
            id3, _ = wf.compute_bundle_id(bundle)
            self.assertNotEqual(id2, id3)

    def test_041_header_with_timestamp_at_shifted_position_still_normalizes(self):
        """OPUS-R8-009: the header is found by searching the header block
        for the field, not by a fixed line index, so a header gaining a
        field before `generated:` still normalizes correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "CHANGED_FILES.txt").write_text(
                "branch: x\nbase: y\nhead: z\nstage: plan\n"
                "schema_version: 2\ngenerated: 2026-07-28T18:31:55Z\n"
            )
            id1, _ = wf.compute_bundle_id(bundle)
            (bundle / "CHANGED_FILES.txt").write_text(
                "branch: x\nbase: y\nhead: z\nstage: plan\n"
                "schema_version: 2\ngenerated: 2026-07-28T19:00:00Z\n"
            )
            id2, _ = wf.compute_bundle_id(bundle)
            self.assertEqual(id1, id2)

    def test_042_header_with_no_generated_line_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "CHANGED_FILES.txt").write_text("branch: x\nbase: y\nhead: z\nstage: plan\n")
            with self.assertRaises(wf.MissingGeneratedTimestampError):
                wf.compute_bundle_id(bundle)

    def test_042b_header_with_duplicated_generated_line_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "CHANGED_FILES.txt").write_text(
                "branch: x\nbase: y\nhead: z\n"
                "generated: 2026-07-28T18:31:55Z\ngenerated: 2026-07-28T19:00:00Z\n"
            )
            with self.assertRaises(wf.DuplicateGeneratedTimestampError):
                wf.compute_bundle_id(bundle)

    def test_047_manifest_id_line_in_non_conforming_spelling_is_not_stripped(self):
        """OPUS-R8-013: a spelling other than the exact contract
        (`bundle_id: <64-hex-chars>`) is not recognized as the
        self-reference field -- it is ordinary content, so it participates
        in the hash like anything else."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "MANIFEST.md").write_text("- **Bundle ID**: " + FAKE_ID_A + "\n")
            id1, _ = wf.compute_bundle_id(bundle)
            (bundle / "MANIFEST.md").write_text("- **Bundle ID**: " + FAKE_ID_B + "\n")
            id2, _ = wf.compute_bundle_id(bundle)
            self.assertNotEqual(
                id1, id2,
                "a non-conforming spelling must not be treated as the "
                "self-reference field",
            )

    def test_048_duplicated_conforming_id_line_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "MANIFEST.md").write_text(
                f"bundle_id: {FAKE_ID_A}\nbundle_id: {FAKE_ID_B}\n"
            )
            with self.assertRaises(wf.MalformedBundleIdFieldError):
                wf.compute_bundle_id(bundle)

    def test_conforming_manifest_round_trips_write_compute_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            (bundle / "MANIFEST.md").write_text(
                wf.render_manifest_md(
                    review_content_id="c" * 64,
                    protected=wf.PLAN_STAGE_PROTECTED,
                    excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
                    excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
                )
            )
            bid, _ = wf.compute_bundle_id(bundle)
            (bundle / "MANIFEST.md").write_text(
                wf.render_manifest_md(
                    review_content_id="c" * 64,
                    protected=wf.PLAN_STAGE_PROTECTED,
                    excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
                    excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
                    bundle_id=bid,
                )
            )
            bid_after_rewrite, _ = wf.compute_bundle_id(bundle)
            self.assertEqual(bid, bid_after_rewrite)

    def test_bundle_entry_keys_use_posix_separator(self):
        """OPUS-R8-017: nested paths must be keyed with forward slashes."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            nested = bundle / "files" / "docs" / "ai-workflow"
            nested.mkdir(parents=True)
            (nested / "WORKFLOW_V2_PLAN.md").write_text("x\n")
            _, entries = wf.compute_bundle_id(bundle)
            self.assertIn("files/docs/ai-workflow/WORKFLOW_V2_PLAN.md", entries)
            self.assertNotIn("\\", "".join(entries.keys()))

    def test_empty_directory_is_invisible_to_bundle_identity(self):
        """OPUS-R8-018, documented explicitly rather than silently true:
        a bundle differing only by an empty directory has the same ID."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bundle = self._make_bundle(tmp)
            id_before, _ = wf.compute_bundle_id(bundle)
            (bundle / "an_empty_dir").mkdir()
            id_after, _ = wf.compute_bundle_id(bundle)
            self.assertEqual(id_before, id_after)


class TestGeneratorWriteDiscipline(unittest.TestCase):
    """OPUS-R18-001/-002: the generator around the frozen identity
    algorithm -- write_manifest_with_verified_identifiers and __main__ --
    must be as safe as the algorithm itself. Missing-test items 126-130."""

    def test_126_protected_path_edit_between_compute_and_recompute_is_caught(self):
        """Missing-test item 126: simulates the exact PROTO-R7-004-class
        defect OPUS-R18-001 found -- a protected-path edit landing between
        the first review_content_id computation and the final
        recompute-and-assert step must be caught, not silently written."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            # Outside repo.root: a bundle directory nested inside the
            # scratch repo's own working tree would itself be an
            # untracked path the classifier has to see (the real
            # .ai-review/ is gitignored; this scratch repo has no such
            # entry), which is incidental to what these tests check.
            bundle_dir = Path(tempfile.mkdtemp(prefix="wf-fingerprint-bundle-"))
            self.addCleanup(shutil.rmtree, bundle_dir, ignore_errors=True)
            _write_review_request_with_content_id(repo, bundle_dir)

            real_compute = wf.compute_review_content_id_plan_stage
            call_count = {"n": 0}

            def fake_compute(*args, **kwargs):
                call_count["n"] += 1
                result = real_compute(*args, **kwargs)
                if call_count["n"] == 1:
                    (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text(
                        "plan v2 -- edited mid-flight\n"
                    )
                return result

            with mock.patch.object(wf, "compute_review_content_id_plan_stage", side_effect=fake_compute):
                with self.assertRaises(wf.ReviewContentIdNotIdempotentError):
                    wf.write_manifest_with_verified_identifiers(
                        repo.root, bundle_dir, repo.base,
                        work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
                    )

    def test_127_double_generation_idempotent_for_both_identifiers(self):
        """Missing-test item 127: regenerating with no content change must
        be idempotent for review_content_id too, not only bundle_id."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            # Outside repo.root: a bundle directory nested inside the
            # scratch repo's own working tree would itself be an
            # untracked path the classifier has to see (the real
            # .ai-review/ is gitignored; this scratch repo has no such
            # entry), which is incidental to what these tests check.
            bundle_dir = Path(tempfile.mkdtemp(prefix="wf-fingerprint-bundle-"))
            self.addCleanup(shutil.rmtree, bundle_dir, ignore_errors=True)
            _write_review_request_with_content_id(repo, bundle_dir)
            digest1, bundle_id1 = wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            digest2, bundle_id2 = wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            self.assertEqual(digest1, digest2)
            self.assertEqual(bundle_id1, bundle_id2)

    def test_128_read_only_inspection_leaves_bundle_directory_byte_identical(self):
        """Missing-test item 128: the read-only operations __main__'s
        default invocation performs (recompute bundle_id, read existing
        manifest fields) must not modify anything under the bundle
        directory -- OPUS-R18-002's core acceptance criterion."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            # Outside repo.root: a bundle directory nested inside the
            # scratch repo's own working tree would itself be an
            # untracked path the classifier has to see (the real
            # .ai-review/ is gitignored; this scratch repo has no such
            # entry), which is incidental to what these tests check.
            bundle_dir = Path(tempfile.mkdtemp(prefix="wf-fingerprint-bundle-"))
            self.addCleanup(shutil.rmtree, bundle_dir, ignore_errors=True)
            _write_review_request_with_content_id(repo, bundle_dir)
            wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            before = {
                p.relative_to(bundle_dir).as_posix(): p.read_bytes()
                for p in bundle_dir.rglob("*") if p.is_file()
            }
            # Exactly the operations the CLI's default (no --write-manifest) branch performs.
            wf.read_manifest_identifiers(bundle_dir / "MANIFEST.md")
            wf.compute_bundle_id(bundle_dir)
            after = {
                p.relative_to(bundle_dir).as_posix(): p.read_bytes()
                for p in bundle_dir.rglob("*") if p.is_file()
            }
            self.assertEqual(before, after)

    def test_129_manifest_writing_requires_explicit_named_invocation(self):
        """Missing-test item 129 (OPUS-R18-002): the CLI must expose a
        separate, explicitly-named, default-off flag for the one
        invocation that writes -- checked directly against the source,
        the governing artifact, per the same discipline item 123 used."""
        source = Path(wf.__file__).read_text()
        self.assertIn('"--write-manifest"', source)
        self.assertIn('action="store_true"', source)

    def test_130_main_contains_no_unconditional_write(self):
        """Missing-test item 130 (OPUS-R18-002): __main__ itself must
        never call .write_text() directly -- the only writer is
        write_manifest_with_verified_identifiers, reached solely through
        the --write-manifest branch."""
        source = Path(wf.__file__).read_text()
        main_block = source.split('if __name__ == "__main__":', 1)[1]
        self.assertNotIn(".write_text(", main_block)


class TestPlanRevisionFromRegistry(unittest.TestCase):
    """OPUS-R18-003: plan_revision must be sourced from the registry JSON,
    not a caller-supplied literal, and must agree with the plan's own
    declared title. Missing-test items 131-132."""

    def test_131_plan_revision_loaded_from_registry_not_a_literal(self):
        with ScratchRepo() as repo:
            registry_text = json.dumps({
                "schema_version": 1, "work_item_id": "workflow-v2-1-core",
                "plan_revision": 9, "checkpoints": [],
            }) + "\n"
            repo.write_plan_docs(
                plan_text="# Some Process Doc (Revision 9)\n",
                registry_text=registry_text,
            )
            revision = wf.load_plan_revision(
                repo.root,
                registry_path=Path("docs/ai-workflow/registry/workflow-v2-1-core-registry.json"),
                plan_path=Path("docs/ai-workflow/WORKFLOW_V2_PLAN.md"),
            )
            self.assertEqual(revision, 9)
        source = Path(wf.__file__).read_text()
        main_block = source.split('if __name__ == "__main__":', 1)[1]
        self.assertNotRegex(main_block, r"plan_revision\s*=\s*\d+")

    def test_132_registry_title_disagreement_fails_generation(self):
        with ScratchRepo() as repo:
            registry_text = json.dumps({
                "schema_version": 1, "work_item_id": "workflow-v2-1-core",
                "plan_revision": 5, "checkpoints": [],
            }) + "\n"
            repo.write_plan_docs(
                plan_text="# Some Process Doc (Revision 6)\n",
                registry_text=registry_text,
            )
            with self.assertRaises(wf.PlanRevisionMismatchError):
                wf.load_plan_revision(
                    repo.root,
                    registry_path=Path("docs/ai-workflow/registry/workflow-v2-1-core-registry.json"),
                    plan_path=Path("docs/ai-workflow/WORKFLOW_V2_PLAN.md"),
                )


class TestWidenedConcurrentWriteClosure(unittest.TestCase):
    """OPUS-R18-004: the concurrent-write classification closure widened
    to app/, docs/adr/, docs/agent-context/, docs/improvements/, gradle/,
    config/, .github/, and five top-level product docs must not raise or
    change review_content_id, while a genuinely novel path still fails
    closed. Missing-test items 133-135. docs/improvements/ was added
    after Milestone 8's FUNCTIONAL_FEATURE_AUDIT.md landed on the
    workflow-v2-1-core branch as a genuinely novel concurrent-write
    path the durability guard could not classify."""

    def test_133_concurrent_product_code_write_mid_sequence_does_not_raise_or_change_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest_before, _ = repo.compute()
            concurrent_writes = [
                ("app/src/main/kotlin/com/repflow/Foo.kt", "class Foo\n"),
                ("app/src/test/kotlin/com/repflow/FooTest.kt", "class FooTest\n"),
            ]
            for i, (path_str, content) in enumerate(concurrent_writes):
                path = repo.root / path_str
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                _run(["git", "add", "-A"], cwd=repo.root)
                _run(["git", "commit", "-q", "-m", f"functional fix {i}: {path_str}"], cwd=repo.root)
                digest_after, _ = repo.compute()
                self.assertEqual(
                    digest_before, digest_after,
                    f"review_content_id changed after a concurrent product-code write to {path_str}",
                )

    def test_134_concurrent_product_doc_write_mid_sequence_does_not_raise_or_change_id(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest_before, _ = repo.compute()
            concurrent_writes = [
                ("AGENTS.md", "agent instructions\n"),
                ("docs/DOMAIN_GLOSSARY.md", "glossary\n"),
                ("docs/adr/0099-new-decision.md", "adr\n"),
                ("docs/improvements/FUNCTIONAL_FEATURE_AUDIT.md", "audit\n"),
                ("gradle/libs.versions.toml", "[versions]\n"),
                ("config/detekt/detekt.yml", "rules: {}\n"),
                (".github/copilot-instructions.md", "instructions\n"),
            ]
            for i, (path_str, content) in enumerate(concurrent_writes):
                path = repo.root / path_str
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                _run(["git", "add", "-A"], cwd=repo.root)
                _run(["git", "commit", "-q", "-m", f"concurrent doc write {i}: {path_str}"], cwd=repo.root)
                digest_after, _ = repo.compute()
                self.assertEqual(
                    digest_before, digest_after,
                    f"review_content_id changed after a concurrent product-doc write to {path_str}",
                )

    def test_135_novel_path_still_fails_closed_after_widened_lists(self):
        with self.assertRaises(wf.UnclassifiedPathError):
            wf.classify_path(
                "yet_another_new_thing/mystery.bin", wf.PLAN_STAGE_PROTECTED,
                wf.PLAN_STAGE_EXCLUDED_PATHS, wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            )


class TestReviewRequestReviewContentIdAgreement(unittest.TestCase):
    """OPUS-R18-005: REVIEW_REQUEST.md and MANIFEST.md must state the same
    review_content_id; disagreement or absence must fail generation.
    Missing-test item 137."""

    def test_137_review_request_and_manifest_review_content_id_agreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            digest = "d" * 64

            (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {digest}\n")
            wf.assert_review_request_states_review_content_id(bundle_dir, digest)  # must not raise

            (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {'e' * 64}\n")
            with self.assertRaises(wf.ReviewContentIdMismatchError):
                wf.assert_review_request_states_review_content_id(bundle_dir, digest)

            (bundle_dir / "REVIEW_REQUEST.md").write_text("stage: plan\n")
            with self.assertRaises(wf.MissingReviewContentIdStatementError):
                wf.assert_review_request_states_review_content_id(bundle_dir, digest)


class TestAtomicManifestWrite(unittest.TestCase):
    """OPUS-R20-001: the write sequence itself must be atomic across the
    idempotence check, not only across the pre-write REVIEW_REQUEST.md
    check -- missing-test item 138."""

    def test_138_protected_path_edit_mid_write_leaves_manifest_untouched(self):
        """A protected-path edit landing between the first
        review_content_id computation and the final recompute-and-assert
        step must still raise (test_126 already covers that), but now
        MANIFEST.md itself must be byte-identical to its pre-invocation
        state afterward -- proof that nothing was written before every
        check passed, not merely that the written value was flagged
        stale."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            bundle_dir = Path(tempfile.mkdtemp(prefix="wf-fingerprint-bundle-"))
            self.addCleanup(shutil.rmtree, bundle_dir, ignore_errors=True)
            _write_review_request_with_content_id(repo, bundle_dir)

            # Establish a real pre-existing MANIFEST.md (a prior, valid
            # generation) so there is real "pre-invocation state" to prove
            # untouched, not just an absent file.
            wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            manifest_path = bundle_dir / "MANIFEST.md"
            before = manifest_path.read_bytes()

            real_compute = wf.compute_review_content_id_plan_stage
            call_count = {"n": 0}

            def fake_compute(*args, **kwargs):
                call_count["n"] += 1
                result = real_compute(*args, **kwargs)
                if call_count["n"] == 1:
                    (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text(
                        "plan v3 -- edited mid-flight\n"
                    )
                return result

            with mock.patch.object(wf, "compute_review_content_id_plan_stage", side_effect=fake_compute):
                with self.assertRaises(wf.ReviewContentIdNotIdempotentError):
                    wf.write_manifest_with_verified_identifiers(
                        repo.root, bundle_dir, repo.base,
                        work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
                    )

            after = manifest_path.read_bytes()
            self.assertEqual(before, after, "MANIFEST.md must be byte-identical to its pre-invocation state")
            # No stray temp file left behind either.
            leftovers = [p.name for p in bundle_dir.iterdir() if p.name != "MANIFEST.md" and p.name != "REVIEW_REQUEST.md"]
            self.assertEqual(leftovers, [], f"unexpected files left in bundle_dir: {leftovers}")

    def test_manifest_content_override_lets_bundle_id_be_computed_before_any_write(self):
        """compute_bundle_id's manifest_content_override must reproduce
        exactly the bundle_id an on-disk MANIFEST.md with the same bytes
        would have produced -- the mechanism the atomic write relies on."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            content = "# Bundle Manifest\n\nreview_content_id: " + ("a" * 64) + "\n"
            (bundle_dir / "OTHER.md").write_text("some other bundle file\n")

            bid_via_override, _ = wf.compute_bundle_id(bundle_dir, manifest_content_override=content.encode())

            (bundle_dir / "MANIFEST.md").write_text(content)
            bid_via_disk, _ = wf.compute_bundle_id(bundle_dir)

            self.assertEqual(bid_via_override, bid_via_disk)


class TestRootBuildFilesFailClosed(unittest.TestCase):
    """OPUS-R20-002: root-level build files deliberately stay unclassified
    at the plan stage and fail closed -- missing-test item 139."""

    def test_139_root_build_files_raise_unclassified_at_plan_stage(self):
        for path in (
            "build.gradle.kts", "settings.gradle.kts", "gradlew",
            "gradlew.bat", ".editorconfig", "Makefile",
        ):
            with self.subTest(path=path):
                with self.assertRaises(wf.UnclassifiedPathError):
                    wf.classify_path(
                        path, wf.PLAN_STAGE_PROTECTED,
                        wf.PLAN_STAGE_EXCLUDED_PATHS, wf.PLAN_STAGE_EXCLUDED_PREFIXES,
                    )


class TestImplementationStageClassification(unittest.TestCase):
    """OPUS-R20-003, D-Fingerprint's WF4a-i mandate: the implementation-
    stage classification projection is a new, independent mechanism, and
    must classify a representative source file oppositely from the
    plan-stage projection -- missing-test item 140."""

    def _write_artifacts_declarations(self, repo, **overrides):
        implementation_stage = {
            "protected_prefixes": {"app/": "source"},
            "protected_paths": {},
            "excluded_prefixes": {},
            "excluded_paths": {},
        }
        implementation_stage.update(overrides)
        data = {
            "schema_version": 2,
            "work_item_id": "workflow-v2-1-core",
            "implementation_stage": implementation_stage,
        }
        artifacts_dir = repo.root / "docs" / "ai-workflow" / "registry"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "workflow-v2-1-core-artifacts.json").write_text(json.dumps(data))
        # Committed immediately, mirroring the real repository's own
        # tracked artifacts file -- otherwise this write would itself be
        # an untracked path the classifier has to see, incidental to what
        # these tests check.
        _run(["git", "add", "-A"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "declare implementation-stage artifacts"], cwd=repo.root)

    def test_140_plan_and_implementation_stage_classify_same_path_oppositely(self):
        path = "app/src/main/kotlin/com/repflow/Foo.kt"
        self.assertEqual(
            wf.classify_path(
                path, wf.PLAN_STAGE_PROTECTED,
                wf.PLAN_STAGE_EXCLUDED_PATHS, wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            ),
            "excluded",
            "app/ is a PLAN_STAGE_EXCLUDED_PREFIXES entry -- not approval-critical before implementation exists",
        )
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            self._write_artifacts_declarations(repo)
            protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
                wf.load_implementation_stage_classification(
                    repo.root, artifacts_path=Path("docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json")
                )
            )
            self.assertEqual(
                wf.classify_path_implementation_stage(
                    path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
                ),
                "protected",
                "app/ must be protected at the implementation stage -- exactly the source content "
                "technical_approval binds to",
            )

    def test_unclassified_path_fails_closed_at_implementation_stage_too(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            self._write_artifacts_declarations(repo)
            protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
                wf.load_implementation_stage_classification(
                    repo.root, artifacts_path=Path("docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json")
                )
            )
            with self.assertRaises(wf.UnclassifiedPathError):
                wf.classify_path_implementation_stage(
                    "yet_another_new_thing/mystery.bin",
                    protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
                )

    def test_implementation_stage_manifest_tombstones_a_deletion_instead_of_raising(self):
        """Unlike the plan-stage manifest builders (AbsentProtectedPathError,
        OPUS-R14-001/-006), an implementation-stage protected path that no
        longer exists must be represented as a tombstone entry -- a
        deletion is a real, representable change at this stage."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            self._write_artifacts_declarations(repo, protected_prefixes={"app/": "source"})
            (repo.root / "app").mkdir()
            (repo.root / "app" / "Foo.kt").write_text("class Foo\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "add app/Foo.kt"], cwd=repo.root)
            base = repo.head()

            (repo.root / "app" / "Foo.kt").unlink()
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "delete app/Foo.kt"], cwd=repo.root)

            protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
                wf.load_implementation_stage_classification(
                    repo.root, artifacts_path=Path("docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json")
                )
            )
            manifest = wf.compute_review_content_manifest_implementation_stage_commit(
                repo.root, base, repo.head(),
                protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
            )
            self.assertEqual(manifest, [{"path": "app/Foo.kt", "exists": False, "mode": None, "blob": None}])

    def test_compute_review_content_id_implementation_stage_hashes_protected_changes(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            self._write_artifacts_declarations(repo)
            registry_exclusion = {"docs/ai-workflow/registry/": "artifact-declarations file itself"}
            digest_before, projection_before = wf.compute_review_content_id_implementation_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="workflow-v2-1-core",
                protected_paths={}, protected_prefixes={"app/": "source"},
                excluded_paths={}, excluded_prefixes=registry_exclusion,
            )
            self.assertEqual(projection_before["review_content_manifest"], [])

            (repo.root / "app").mkdir()
            (repo.root / "app" / "Foo.kt").write_text("class Foo\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "add app/Foo.kt"], cwd=repo.root)

            digest_after, projection_after = wf.compute_review_content_id_implementation_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="workflow-v2-1-core",
                protected_paths={}, protected_prefixes={"app/": "source"},
                excluded_paths={}, excluded_prefixes=registry_exclusion,
            )
            self.assertNotEqual(digest_before, digest_after)
            self.assertEqual(
                [e["path"] for e in projection_after["review_content_manifest"]], ["app/Foo.kt"]
            )
            self.assertEqual(projection_after["stage"], "implementation")
            # reviewed_implementation_head is never derived from live HEAD
            # and hashed here -- folding it in would make review_content_id
            # change on every new commit regardless of content, which is
            # exactly what test_excluded_concurrent_write_does_not_change_
            # implementation_stage_id below proves must not happen.
            self.assertIsNone(projection_after["reviewed_implementation_head"])

    def test_excluded_concurrent_write_does_not_change_implementation_stage_id(self):
        """Mirrors TestWidenedConcurrentWriteClosure at the plan stage: a
        write to a path this work item's own artifacts declarations name
        as excluded must not raise or change review_content_id."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            self._write_artifacts_declarations(
                repo,
                protected_prefixes={},
                excluded_paths={"docs/ai-workflow/WORKFLOW_STATE.json": "runtime-mutable state"},
            )
            kwargs = dict(
                work_item_type="process", work_item_id="workflow-v2-1-core",
                protected_paths={}, protected_prefixes={},
                excluded_paths={"docs/ai-workflow/WORKFLOW_STATE.json": "runtime-mutable state"},
                excluded_prefixes={"docs/ai-workflow/registry/": "artifact-declarations file itself"},
            )
            digest_before, _ = wf.compute_review_content_id_implementation_stage(repo.root, repo.base, **kwargs)
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text('{"phase": "IMPLEMENTING"}\n')
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "state write"], cwd=repo.root)
            digest_after, _ = wf.compute_review_content_id_implementation_stage(repo.root, repo.base, **kwargs)
            self.assertEqual(digest_before, digest_after)


class TestToolingAmbientExcludedPaths(unittest.TestCase):
    """workflow-2.6.0, `D-Tooling-Ambient-Classification` (closes the
    legacy-item half of `v2.4.0-001`): `.workflow-manager/installation.json`
    is excluded by a release-derived, exact-path terminal fallback that both
    classifiers consult only after every declared classification has
    failed. It never overrides a declaration, never enters a hashed
    classification set, and leaves every digest `2.5.1` could compute
    byte-identical. "Without the fallback" below means the constant
    patched to the empty set -- exactly `2.5.1`'s classifier behavior."""

    INSTALLATION = ".workflow-manager/installation.json"
    WORK_ITEM_ID = "legacy-item"
    PLAN_REL = "docs/ai-workflow/legacy-item-plan.md"
    # A 2.3.1-shaped declaration: nothing under `.workflow-manager/`.
    PLAN_PROTECTED = frozenset({PLAN_REL})
    PLAN_EXCLUDED_PATHS = {"docs/ai-workflow/WORKFLOW_STATE.json": "runtime-mutable state"}
    PLAN_EXCLUDED_PREFIXES = {"docs/ai-workflow/registry/": "declarations", "scripts/": "implementation"}
    IMPL_PROTECTED_PATHS: dict = {}
    IMPL_PROTECTED_PREFIXES = {"scripts/": "implementation"}
    IMPL_EXCLUDED_PATHS = {"docs/ai-workflow/WORKFLOW_STATE.json": "runtime-mutable state"}
    IMPL_EXCLUDED_PREFIXES = {"docs/": "design docs"}

    def _without_fallback(self):
        return mock.patch.object(wf, "TOOLING_AMBIENT_EXCLUDED_PATHS", frozenset())

    def _classify_plan(self, path, protected=None, excluded_paths=None):
        return wf.classify_path(
            path,
            self.PLAN_PROTECTED if protected is None else protected,
            self.PLAN_EXCLUDED_PATHS if excluded_paths is None else excluded_paths,
            self.PLAN_EXCLUDED_PREFIXES,
        )

    def _classify_impl(self, path, protected_paths=None):
        return wf.classify_path_implementation_stage(
            path,
            self.IMPL_PROTECTED_PATHS if protected_paths is None else protected_paths,
            self.IMPL_PROTECTED_PREFIXES, self.IMPL_EXCLUDED_PATHS, self.IMPL_EXCLUDED_PREFIXES,
        )

    def test_constant_is_the_exact_installation_record_path(self):
        self.assertEqual(wf.TOOLING_AMBIENT_EXCLUDED_PATHS, frozenset({self.INSTALLATION}))

    def test_legacy_declaration_classifies_installation_record_excluded_at_both_stages(self):
        self.assertEqual(self._classify_plan(self.INSTALLATION), "excluded")
        self.assertEqual(self._classify_impl(self.INSTALLATION), "excluded")
        # Control arm: 2.5.1's classifier raised on the identical input.
        with self._without_fallback():
            with self.assertRaises(wf.UnclassifiedPathError):
                self._classify_plan(self.INSTALLATION)
            with self.assertRaises(wf.UnclassifiedPathError):
                self._classify_impl(self.INSTALLATION)

    def test_explicit_protected_declaration_still_wins(self):
        self.assertEqual(
            self._classify_plan(self.INSTALLATION, protected=self.PLAN_PROTECTED | {self.INSTALLATION}),
            "protected",
        )
        self.assertEqual(
            self._classify_impl(self.INSTALLATION, protected_paths={self.INSTALLATION: "declared"}),
            "protected",
        )

    def test_siblings_still_fail_closed(self):
        for path in (
            ".workflow-manager/installation.json.tmp",
            ".workflow-manager/other.json",
            ".workflow-manager/",
            "installation.json",
            "nested/.workflow-manager/installation.json",
        ):
            with self.subTest(path=path):
                with self.assertRaises(wf.UnclassifiedPathError):
                    self._classify_plan(path)
                with self.assertRaises(wf.UnclassifiedPathError):
                    self._classify_impl(path)

    def _seed_legacy_repo(self, repo):
        """Base commit already carries the installation record (a managed
        repository), the legacy item's plan, and one implementation file."""
        (repo.root / ".workflow-manager").mkdir()
        (repo.root / self.INSTALLATION).write_text('{"release": "2.3.1"}\n')
        (repo.root / self.PLAN_REL).write_text("# Plan\n\nplan v1\n")
        (repo.root / "scripts").mkdir()
        (repo.root / "scripts" / "tool.py").write_text("print('v1')\n")
        _run(["git", "add", "-A"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "managed repository base"], cwd=repo.root)
        repo.base = repo.head()
        # An implementation-stage change, so that projection is non-empty.
        (repo.root / "scripts" / "tool.py").write_text("print('v2')\n")
        _run(["git", "add", "-A"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "implement"], cwd=repo.root)

    def _plan_worktree(self, repo):
        return wf.compute_review_content_id_plan_stage(
            repo.root, repo.base, "process", self.WORK_ITEM_ID, 1,
            self.PLAN_PROTECTED, self.PLAN_EXCLUDED_PATHS, self.PLAN_EXCLUDED_PREFIXES,
        )

    def _plan_commit(self, repo, commit):
        return wf.compute_review_content_id_plan_stage_at_commit(
            repo.root, repo.base, commit, "process", self.WORK_ITEM_ID, 1,
            self.PLAN_PROTECTED, self.PLAN_EXCLUDED_PATHS, self.PLAN_EXCLUDED_PREFIXES,
        )

    def _impl_worktree(self, repo):
        return wf.compute_review_content_id_implementation_stage(
            repo.root, repo.base, "process", self.WORK_ITEM_ID,
            self.IMPL_PROTECTED_PATHS, self.IMPL_PROTECTED_PREFIXES,
            self.IMPL_EXCLUDED_PATHS, self.IMPL_EXCLUDED_PREFIXES,
        )

    def _impl_commit(self, repo, commit):
        return wf.compute_review_content_id_implementation_stage_at_commit(
            repo.root, repo.base, commit, "process", self.WORK_ITEM_ID,
            self.IMPL_PROTECTED_PATHS, self.IMPL_PROTECTED_PREFIXES,
            self.IMPL_EXCLUDED_PATHS, self.IMPL_EXCLUDED_PREFIXES,
        )

    def _all_digests(self, repo, commit):
        return {
            "plan_worktree": self._plan_worktree(repo),
            "plan_commit": self._plan_commit(repo, commit),
            "impl_worktree": self._impl_worktree(repo),
            "impl_commit": self._impl_commit(repo, commit),
        }

    def test_hashed_classification_sets_are_invariant(self):
        """Hashed-set invariance: every projection (not only its digest)
        is identical with and without the fallback, and the constant's
        path appears nowhere in any projection."""
        with ScratchRepo() as repo:
            self._seed_legacy_repo(repo)
            head = repo.head()
            with_fallback = self._all_digests(repo, head)
            with self._without_fallback():
                without_fallback = self._all_digests(repo, head)
            self.assertEqual(with_fallback, without_fallback)
            for name, (_, projection) in with_fallback.items():
                with self.subTest(projection=name):
                    self.assertNotIn(self.INSTALLATION, json.dumps(projection, sort_keys=True))

    def test_digest_invariance_when_installation_record_is_unchanged(self):
        with ScratchRepo() as repo:
            self._seed_legacy_repo(repo)
            head = repo.head()
            with self._without_fallback():
                recorded = {k: v[0] for k, v in self._all_digests(repo, head).items()}
            self.assertEqual({k: v[0] for k, v in self._all_digests(repo, head).items()}, recorded)

    def test_previously_raising_digests_equal_the_pre_change_recorded_values(self):
        """The `v2.4.0-001` reproduction: a committed `workflow_manager
        update` rewrites only the installation record. Under `2.5.1` every
        digest then raised; now each returns and equals the value recorded
        before the update, both uncommitted (worktree source) and
        committed (worktree and commit source)."""
        with ScratchRepo() as repo:
            self._seed_legacy_repo(repo)
            pre_update_head = repo.head()
            with self._without_fallback():
                recorded = {k: v[0] for k, v in self._all_digests(repo, pre_update_head).items()}

            (repo.root / self.INSTALLATION).write_text('{"release": "2.5.1"}\n')
            with self._without_fallback():
                with self.assertRaises(wf.UnclassifiedPathError):
                    self._plan_worktree(repo)
                with self.assertRaises(wf.UnclassifiedPathError):
                    self._impl_worktree(repo)
            self.assertEqual(self._plan_worktree(repo)[0], recorded["plan_worktree"])
            self.assertEqual(self._impl_worktree(repo)[0], recorded["impl_worktree"])

            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "chore(workflow): update installed workflow"], cwd=repo.root)
            update_head = repo.head()
            with self._without_fallback():
                for name, compute in (
                    ("plan_worktree", lambda: self._plan_worktree(repo)),
                    ("plan_commit", lambda: self._plan_commit(repo, update_head)),
                    ("impl_worktree", lambda: self._impl_worktree(repo)),
                    ("impl_commit", lambda: self._impl_commit(repo, update_head)),
                ):
                    with self.subTest(control=name):
                        with self.assertRaises(wf.UnclassifiedPathError):
                            compute()
            after = {k: v[0] for k, v in self._all_digests(repo, update_head).items()}
            self.assertEqual(after, recorded)

    def test_temp_sibling_still_raises_through_the_digest(self):
        with ScratchRepo() as repo:
            self._seed_legacy_repo(repo)
            (repo.root / ".workflow-manager" / "installation.json.tmp").write_text("{}\n")
            with self.assertRaises(wf.UnclassifiedPathError):
                self._plan_worktree(repo)
            with self.assertRaises(wf.UnclassifiedPathError):
                self._impl_worktree(repo)


class TestApprovalRecordManifestUsesTheRealComputeFunctions(unittest.TestCase):
    """`workflow-v2-3-followups` continued scope, self-discovered during
    this item's own `/accept-milestone` pre-flight:
    `workflow_state.build_approval_record`'s `review_content_manifest`
    argument must be the real compute function's own returned
    `projection["review_content_manifest"]`, never `projection` itself --
    the exact substitution that silently produced two malformed approval
    records (plan and technical) for this same work item, with zero prior
    regression coverage in either direction: every pre-existing
    `build_approval_record` call site in this suite either discarded the
    real projection (`_`) or hand-built a placeholder manifest, never
    exercising the real compute-function-to-record sequence."""

    def test_plan_stage_projections_own_manifest_field_is_accepted(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest, projection = repo.compute()
            record = ws.build_approval_record(
                basis="EXTERNAL_APPROVE", stage="plan", user_confirmation="approve wi plan",
                now="t", reviewed_bundle_id="b", approved_review_content_id=digest,
                review_content_manifest=projection["review_content_manifest"],
            )
            self.assertEqual(record["review_content_manifest"], projection["review_content_manifest"])
            self.assertIsInstance(record["review_content_manifest"], list)
            self.assertTrue(record["review_content_manifest"])

    def test_plan_stage_whole_projection_object_is_rejected(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            digest, projection = repo.compute()
            with self.assertRaises(ws.InvalidApprovalRecordError):
                ws.build_approval_record(
                    basis="EXTERNAL_APPROVE", stage="plan", user_confirmation="approve wi plan",
                    now="t", reviewed_bundle_id="b", approved_review_content_id=digest,
                    review_content_manifest=projection,
                )

    def _write_implementation_artifacts_declaration(self, repo):
        data = {
            "schema_version": 2,
            "work_item_id": "workflow-v2-1-core",
            "implementation_stage": {
                "protected_prefixes": {"app/": "source"},
                "protected_paths": {},
                "excluded_prefixes": {"docs/ai-workflow/registry/": "artifact-declarations file itself"},
                "excluded_paths": {},
            },
        }
        artifacts_dir = repo.root / "docs" / "ai-workflow" / "registry"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "workflow-v2-1-core-artifacts.json").write_text(json.dumps(data))
        _run(["git", "add", "-A"], cwd=repo.root)
        _run(["git", "commit", "-q", "-m", "declare implementation-stage artifacts"], cwd=repo.root)

    def _implementation_stage_projection(self, repo):
        return wf.compute_review_content_id_implementation_stage(
            repo.root, repo.base, work_item_type="process", work_item_id="workflow-v2-1-core",
            protected_paths={}, protected_prefixes={"app/": "source"},
            excluded_paths={},
            excluded_prefixes={"docs/ai-workflow/registry/": "artifact-declarations file itself"},
        )

    def test_implementation_stage_projections_own_manifest_field_is_accepted(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            self._write_implementation_artifacts_declaration(repo)
            (repo.root / "app").mkdir()
            (repo.root / "app" / "Foo.kt").write_text("class Foo\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "add app/Foo.kt"], cwd=repo.root)

            digest, projection = self._implementation_stage_projection(repo)
            record = ws.build_approval_record(
                basis="EXTERNAL_APPROVE", stage="implementation", user_confirmation="approve wi implementation",
                now="t", reviewed_bundle_id="b", approved_review_content_id=digest,
                review_content_manifest=projection["review_content_manifest"], reviewed_content_commit=repo.head(),
            )
            self.assertEqual(record["review_content_manifest"], projection["review_content_manifest"])
            self.assertEqual([e["path"] for e in record["review_content_manifest"]], ["app/Foo.kt"])

    def test_implementation_stage_whole_projection_object_is_rejected(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            self._write_implementation_artifacts_declaration(repo)
            (repo.root / "app").mkdir()
            (repo.root / "app" / "Foo.kt").write_text("class Foo\n")
            _run(["git", "add", "-A"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "add app/Foo.kt"], cwd=repo.root)

            digest, projection = self._implementation_stage_projection(repo)
            with self.assertRaises(ws.InvalidApprovalRecordError):
                ws.build_approval_record(
                    basis="EXTERNAL_APPROVE", stage="implementation", user_confirmation="approve wi implementation",
                    now="t", reviewed_bundle_id="b", approved_review_content_id=digest,
                    review_content_manifest=projection, reviewed_content_commit=repo.head(),
                )


class TestBundleLayoutResolver(unittest.TestCase):
    """WF5's `.ai-review/<work_item_id>/{current,feedback}/` relayout
    resolver, with the stated compatibility fallback (resolves
    `OPUS-R6-021`).

    Re-pointed by `D-Feedback-Layout` (workflow-2.6.0): every scratch repo
    here has no `WORKFLOW_STATE.json`, so the feedback assertions pin the
    **legacy** rule (scoped-else-flat) that an item without a
    `feedback_layout` stamp keeps. A scoped item's resolution is pinned by
    `TestFeedbackLayout`."""

    def test_falls_back_to_flat_layout_when_scoped_dir_absent(self):
        with ScratchRepo() as repo:
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/current"),
            )
            self.assertEqual(
                wf.resolve_feedback_dir(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/feedback"),
            )

    def test_prefers_scoped_layout_once_it_exists(self):
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "workflow-v2-1-core" / "current").mkdir(parents=True)
            (repo.root / ".ai-review" / "workflow-v2-1-core" / "feedback").mkdir(parents=True)
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/workflow-v2-1-core/current"),
            )
            self.assertEqual(
                wf.resolve_feedback_dir(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/workflow-v2-1-core/feedback"),
            )

    def test_two_work_items_resolve_independently(self):
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "milestone-8" / "current").mkdir(parents=True)
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "milestone-8"),
                Path(".ai-review/milestone-8/current"),
            )
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/current"),
            )

    def test_rejects_invalid_work_item_id(self):
        with ScratchRepo() as repo:
            with self.assertRaises(wf.InvalidWorkItemIdError):
                wf.resolve_bundle_dir(repo.root, "Not_A_Valid_Slug!")


class TestBundleLayoutResolverStageAwareness(unittest.TestCase):
    """Convergence repair `I1`: `resolve_bundle_dir`'s existence gate is
    correct only where the flat layout is a reachable generation target.
    It is for the three stages whose `work-item-id` argument to
    `prepare-ai-review.sh` is optional; it is not for the plan stage,
    whose argument is required and whose generator templates
    `.ai-review/<work_item_id>/` directly. Passing `stage="plan"` makes
    the resolver answer scoped by construction, closing the split that
    sent the authoring half of every plan-stage command to the flat path
    on a work item's first bundle while the generator wrote the scoped
    one."""

    def test_plan_stage_is_scoped_by_construction_with_no_directory_on_disk(self):
        with ScratchRepo() as repo:
            self.assertFalse((repo.root / ".ai-review").exists())
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "workflow-v2-1-core", stage="plan"),
                Path(".ai-review/workflow-v2-1-core/current"),
            )

    def test_plan_stage_ignores_an_existing_flat_layout(self):
        """A flat `.ai-review/current/` belonging to some other work item
        must never capture a plan-stage resolution -- the pre-repair
        resolver would have authored this item's plan bundle straight over
        it."""
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "current").mkdir(parents=True)
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "milestone-9", stage="plan"),
                Path(".ai-review/milestone-9/current"),
            )
            # ... while a stageless resolution still takes it, unchanged.
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "milestone-9"), Path(".ai-review/current"),
            )

    def test_plan_stage_answer_survives_the_scoped_current_being_withdrawn(self):
        """`withdraw_bundle` renames `current/` to a quarantine sibling,
        which is what made the same defect recur on every regeneration
        after a withdrawal, not just on a work item's very first bundle."""
        with ScratchRepo() as repo:
            scoped = repo.root / ".ai-review" / "milestone-9" / "current"
            scoped.mkdir(parents=True)
            scoped.rename(scoped.parent / "current.rejected-deadbeef")
            self.assertEqual(
                wf.resolve_bundle_dir(repo.root, "milestone-9", stage="plan"),
                Path(".ai-review/milestone-9/current"),
            )

    def test_non_plan_stages_keep_the_flat_compatibility_rule(self):
        with ScratchRepo() as repo:
            for stage in ("implementation", "post-fix", "functional-review", None):
                with self.subTest(stage=stage):
                    self.assertEqual(
                        wf.resolve_bundle_dir(repo.root, "milestone-9", stage=stage),
                        Path(".ai-review/current"),
                    )
            (repo.root / ".ai-review" / "milestone-9" / "current").mkdir(parents=True)
            for stage in ("implementation", "post-fix", "functional-review", None):
                with self.subTest(stage=stage, scoped=True):
                    self.assertEqual(
                        wf.resolve_bundle_dir(repo.root, "milestone-9", stage=stage),
                        Path(".ai-review/milestone-9/current"),
                    )

    def test_non_plan_stages_stay_scoped_while_current_is_quarantined(self):
        """The re-audit's own finding, the same defect one stage over:
        `withdraw_bundle` renames `current/` away, and a `current/`-gated
        answer flipped an item that had demonstrably been generating
        scoped bundles back onto the flat path for its next round -- while
        `prepare-ai-review.sh`, given the same `[work-item-id]` argument
        the round before, still wrote the scoped directory. Deciding from
        the work item's own root directory is stable across the rename."""
        with ScratchRepo() as repo:
            scoped_root = repo.root / ".ai-review" / "milestone-9"
            (scoped_root / "current").mkdir(parents=True)
            (scoped_root / "current").rename(scoped_root / "current.rejected-deadbeef")
            for stage in ("implementation", "post-fix", "functional-review", None):
                with self.subTest(stage=stage):
                    self.assertEqual(
                        wf.resolve_bundle_dir(repo.root, "milestone-9", stage=stage),
                        Path(".ai-review/milestone-9/current"),
                    )

    def test_a_never_scoped_work_item_still_resolves_flat_at_non_plan_stages(self):
        """The genuinely flat-generated layout is untouched: omitting
        `prepare-ai-review.sh`'s optional `[work-item-id]` argument is a
        documented, supported invocation that really does write
        `.ai-review/current/`, and an item that has never had a
        `.ai-review/<id>/` directory created still resolves there."""
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "current").mkdir(parents=True)
            (repo.root / ".ai-review" / "feedback").mkdir(parents=True)
            for stage in ("implementation", "post-fix", "functional-review", None):
                with self.subTest(stage=stage):
                    self.assertEqual(
                        wf.resolve_bundle_dir(repo.root, "milestone-9", stage=stage),
                        Path(".ai-review/current"),
                    )

    def test_unknown_stage_is_refused_rather_than_treated_as_no_stage(self):
        """Falling through to the compatibility branch on a typo would
        reintroduce exactly the defect the argument closes, silently."""
        with ScratchRepo() as repo:
            for bad in ("Plan", "plan-stage", "PLAN", "", "post_fix"):
                with self.subTest(stage=bad):
                    with self.assertRaises(wf.InvalidBundleStageError):
                        wf.resolve_bundle_dir(repo.root, "milestone-9", stage=bad)

    def test_stage_vocabulary_matches_the_generation_script(self):
        """The same four stages `scripts/prepare-ai-review.sh` accepts,
        read out of the script itself rather than restated here."""
        script = (Path(__file__).resolve().parent / "prepare-ai-review.sh").read_text()
        self.assertIn("plan | implementation | post-fix | functional-review", script)
        self.assertEqual(
            wf.GENERATION_STAGES,
            frozenset({"plan", "implementation", "post-fix", "functional-review"}),
        )
        self.assertTrue(wf.SCOPED_BY_CONSTRUCTION_STAGES <= wf.GENERATION_STAGES)
        self.assertEqual(wf.SCOPED_BY_CONSTRUCTION_STAGES, frozenset({"plan"}))

    def test_work_item_id_is_still_validated_before_the_stage(self):
        with ScratchRepo() as repo:
            with self.assertRaises(wf.InvalidWorkItemIdError):
                wf.resolve_bundle_dir(repo.root, "Not_A_Valid_Slug!", stage="plan")

    def test_resolve_feedback_dir_is_deliberately_not_stage_aware(self):
        """`feedback/` is stage-agnostic by contract (`REVIEW_PROTOCOL.md`,
        `REQ-21`); this repair leaves it completely untouched. (No state
        file here, so the flat answer is the legacy rule's --
        `D-Feedback-Layout` keeps the signature stage-free for every
        layout.)"""
        with ScratchRepo() as repo:
            self.assertEqual(
                wf.resolve_feedback_dir(repo.root, "milestone-9"), Path(".ai-review/feedback"),
            )
            with self.assertRaises(TypeError):
                wf.resolve_feedback_dir(repo.root, "milestone-9", stage="plan")


class TestRejectedBundleMarker(unittest.TestCase):
    """`WFR-67`'s shared `REJECTED`-marker resolver/assertion (`WF8c`
    item (h), part 1: the consumer-side read half). The generator-side
    write (the ordered quarantine withdrawal) is separate, deferred
    scope -- these tests exercise the read/refuse contract directly
    against a hand-written marker, which is exactly how a real,
    generator-written marker would be observed too."""

    def test_resolves_flat_path_when_scoped_dir_absent(self):
        with ScratchRepo() as repo:
            self.assertEqual(
                wf.resolve_rejected_marker_path(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/REJECTED"),
            )

    def test_resolves_scoped_path_once_scoped_layout_exists(self):
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "workflow-v2-1-core" / "current").mkdir(parents=True)
            self.assertEqual(
                wf.resolve_rejected_marker_path(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/workflow-v2-1-core/REJECTED"),
            )

    def test_passes_when_no_marker_present(self):
        with ScratchRepo() as repo:
            wf.assert_bundle_not_rejected(repo.root, "workflow-v2-1-core")

    def test_refuses_with_marker_content_when_marker_present(self):
        with ScratchRepo() as repo:
            marker = repo.root / ".ai-review" / "REJECTED"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("withdrawal failed at: archive removal; surviving path: .ai-review/current\n")
            with self.assertRaises(wf.BundleRejectedError) as ctx:
                wf.assert_bundle_not_rejected(repo.root, "workflow-v2-1-core")
            self.assertIn("REJECTED", str(ctx.exception))
            self.assertIn("archive removal", str(ctx.exception))

    def test_refuses_on_presence_alone_for_empty_marker(self):
        """An empty/truncated marker degrades the diagnostic -- it never
        passes as "not rejected" (revision 79, `GPT-OPUS-R97-006`)."""
        with ScratchRepo() as repo:
            marker = repo.root / ".ai-review" / "REJECTED"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("")
            with self.assertRaises(wf.BundleRejectedError) as ctx:
                wf.assert_bundle_not_rejected(repo.root, "workflow-v2-1-core")
            self.assertIn("empty marker", str(ctx.exception))

    def test_refuses_when_marker_parent_is_not_a_directory(self):
        """A presence check that cannot complete (`EACCES`/`ENOTDIR`/
        `ELOOP`) is treated as present, never as absent (revision 80,
        `OPUS-R98-004`) -- simulated here via `ENOTDIR`: `.ai-review`
        itself, the marker's own parent, is a regular file rather than a
        directory, so `os.stat` on the marker path cannot complete.
        `Path.is_file()` would silently swallow this and report "absent"
        instead -- the reason `assert_bundle_not_rejected` uses `os.stat`
        directly rather than pathlib's own presence check."""
        with ScratchRepo() as repo:
            (repo.root / ".ai-review").write_text("not a directory\n")
            with self.assertRaises(wf.BundleRejectedError) as ctx:
                wf.assert_bundle_not_rejected(repo.root, "workflow-v2-1-core")
            self.assertIn("could not be determined", str(ctx.exception))

    def test_two_work_items_have_independent_markers(self):
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "milestone-8" / "current").mkdir(parents=True)
            (repo.root / ".ai-review" / "milestone-8" / "REJECTED").write_text("blocked\n")
            wf.assert_bundle_not_rejected(repo.root, "workflow-v2-1-core")
            with self.assertRaises(wf.BundleRejectedError):
                wf.assert_bundle_not_rejected(repo.root, "milestone-8")

    def test_marker_resolves_scoped_while_current_is_quarantined(self):
        """Convergence repair, Optional finding 4. `withdraw_bundle`
        writes the marker first, renames `current/` away, and only then
        removes the marker. While the marker path was derived from
        `resolve_bundle_dir`'s own answer, and that answer was keyed on
        `current/`'s existence, the rename moved the *resolved* marker
        path from the scoped location it had just been written at to the
        flat one -- so a crash in the window between the successful rename
        and the marker's removal left a scoped marker that every later
        `assert_bundle_not_rejected` resolved past, failing **open** on
        exactly the residue the marker exists to refuse. The shared
        resolver now decides the layout from the work item's own root
        directory, which a withdrawal does not remove."""
        with ScratchRepo() as repo:
            scoped_root = repo.root / ".ai-review" / "milestone-8"
            (scoped_root / "current").mkdir(parents=True)
            marker = scoped_root / "REJECTED"
            marker.write_text("REJECTED: withdrawal in progress\nstep: MANIFEST.md removed\n")
            # The exact crash window: the rename landed, the unlink did not.
            (scoped_root / "current").rename(scoped_root / "current.rejected-deadbeef")
            self.assertEqual(
                wf.resolve_rejected_marker_path(repo.root, "milestone-8"),
                Path(".ai-review/milestone-8/REJECTED"),
            )
            with self.assertRaises(wf.BundleRejectedError) as ctx:
                wf.assert_bundle_not_rejected(repo.root, "milestone-8")
            self.assertIn("MANIFEST.md removed", str(ctx.exception))

    def test_clearing_the_marker_targets_the_same_resolved_path(self):
        """`clear_rejected_marker_if_present` resolves through the same
        function, so the residue above is cleared by the next successful
        generation rather than becoming permanently sticky."""
        with ScratchRepo() as repo:
            scoped_root = repo.root / ".ai-review" / "milestone-8"
            (scoped_root / "current").mkdir(parents=True)
            (scoped_root / "REJECTED").write_text("blocked\n")
            (scoped_root / "current").rename(scoped_root / "current.rejected-deadbeef")
            wf.clear_rejected_marker_if_present(repo.root, "milestone-8")
            self.assertFalse((scoped_root / "REJECTED").exists())
            wf.assert_bundle_not_rejected(repo.root, "milestone-8")

    def test_a_work_item_with_no_scoped_root_still_resolves_flat(self):
        """The flat compatibility layout is untouched by the change above:
        a work item that has never had a `.ai-review/<id>/` directory
        created is still on the flat marker path, exactly as before."""
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "current").mkdir(parents=True)
            self.assertEqual(
                wf.resolve_rejected_marker_path(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/REJECTED"),
            )

    def test_marker_resolver_rejects_invalid_work_item_id(self):
        with ScratchRepo() as repo:
            with self.assertRaises(wf.InvalidWorkItemIdError):
                wf.resolve_rejected_marker_path(repo.root, "Not_A_Valid_Slug!")

    def test_marker_path_is_derived_from_the_bundle_resolver_not_a_second_copy(self):
        """The two resolvers agree by derivation, not by restating the
        layout rule twice -- so a future change to one cannot leave the
        other behind."""
        with ScratchRepo() as repo:
            for setup in (lambda: None,
                          lambda: (repo.root / ".ai-review" / "milestone-8" / "current").mkdir(parents=True)):
                setup()
                self.assertEqual(
                    wf.resolve_rejected_marker_path(repo.root, "milestone-8"),
                    wf.resolve_bundle_dir(repo.root, "milestone-8").parent / "REJECTED",
                )


class TestFunctionalReviewConsumedMarker(unittest.TestCase):
    """O3 (`workflow-v2-3-followups` continued scope, external cross-model
    review round 2): the smallest mechanism consistent with two existing
    conventions at once -- `assert_bundle_not_rejected`'s presence-then-
    content marker shape (`.ai-review/<work_item_id>/REJECTED`, an
    untracked sibling of the artifact it describes) and `/prepare-
    functional-review`'s own checklist-evidence content-hash binding --
    that prevents an already-applied `FUNCTIONAL_REVIEW.md` from being
    re-read as fresh findings on a later `/apply-functional-review` pass,
    without any lifecycle/review-stage/ledger-stage/quorum addition.

    Re-pointed by `D-Feedback-Layout` (workflow-2.6.0): these scratch repos
    have no `WORKFLOW_STATE.json`, so the marker follows the **legacy**
    rule and is shared across legacy items resolving flat. For scoped
    items the marker is per item (`TestFeedbackLayout`)."""

    def _write_feedback(self, repo, work_item_id, content):
        feedback_dir = repo.root / wf.resolve_feedback_dir(repo.root, work_item_id)
        feedback_dir.mkdir(parents=True, exist_ok=True)
        (feedback_dir / "FUNCTIONAL_REVIEW.md").write_text(content)

    def test_resolves_flat_path_when_scoped_dir_absent(self):
        with ScratchRepo() as repo:
            self.assertEqual(
                wf.resolve_functional_review_consumed_marker_path(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/feedback/FUNCTIONAL_REVIEW.consumed"),
            )

    def test_resolves_scoped_path_once_scoped_layout_exists(self):
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "workflow-v2-1-core" / "feedback").mkdir(parents=True)
            self.assertEqual(
                wf.resolve_functional_review_consumed_marker_path(repo.root, "workflow-v2-1-core"),
                Path(".ai-review/workflow-v2-1-core/feedback/FUNCTIONAL_REVIEW.consumed"),
            )

    def test_passes_when_no_marker_present(self):
        with ScratchRepo() as repo:
            self._write_feedback(repo, "wi", "# finding 1\n")
            wf.assert_functional_review_not_already_consumed(repo.root, "wi")  # must not raise

    def test_refuses_when_current_content_matches_the_recorded_hash(self):
        with ScratchRepo() as repo:
            self._write_feedback(repo, "wi", "# finding 1\n")
            wf.mark_functional_review_consumed(repo.root, "wi")
            with self.assertRaises(wf.FunctionalReviewAlreadyAppliedError) as ctx:
                wf.assert_functional_review_not_already_consumed(repo.root, "wi")
            self.assertIn("already applied", str(ctx.exception))

    def test_passes_when_content_changed_since_the_marker_was_written(self):
        """A genuinely new round of functional testing wrote fresh
        findings -- the marker's own hash no longer matches, so this
        must be treated as unconsumed, not refused."""
        with ScratchRepo() as repo:
            self._write_feedback(repo, "wi", "# finding 1\n")
            wf.mark_functional_review_consumed(repo.root, "wi")
            self._write_feedback(repo, "wi", "# finding 2 (new round)\n")
            wf.assert_functional_review_not_already_consumed(repo.root, "wi")  # must not raise

    def test_marking_twice_for_the_same_content_stays_idempotent(self):
        with ScratchRepo() as repo:
            self._write_feedback(repo, "wi", "# finding 1\n")
            wf.mark_functional_review_consumed(repo.root, "wi")
            wf.mark_functional_review_consumed(repo.root, "wi")  # must not raise
            with self.assertRaises(wf.FunctionalReviewAlreadyAppliedError):
                wf.assert_functional_review_not_already_consumed(repo.root, "wi")

    def test_two_scoped_work_items_have_independent_markers(self):
        """Distinguished by the scoped-layout `feedback_dir`, mirroring
        `TestRejectedBundleMarker.test_two_work_items_have_independent_markers`
        -- both must already be on the scoped layout, or they would
        collide at the same flat compatibility path
        (`resolve_feedback_dir`'s own documented fallback)."""
        with ScratchRepo() as repo:
            (repo.root / ".ai-review" / "workflow-v2-1-core" / "feedback").mkdir(parents=True)
            (repo.root / ".ai-review" / "milestone-8" / "feedback").mkdir(parents=True)
            self._write_feedback(repo, "workflow-v2-1-core", "# shared content\n")
            self._write_feedback(repo, "milestone-8", "# shared content\n")
            wf.mark_functional_review_consumed(repo.root, "workflow-v2-1-core")
            with self.assertRaises(wf.FunctionalReviewAlreadyAppliedError):
                wf.assert_functional_review_not_already_consumed(repo.root, "workflow-v2-1-core")
            wf.assert_functional_review_not_already_consumed(repo.root, "milestone-8")  # must not raise


class TestGenerationDiagnosticMetadata(unittest.TestCase):
    """`worktree_root`/`generation_head` recorded in `MANIFEST.md` as
    diagnostic metadata, and the repository-local-only staleness check
    that reads them back (resolves `OPUS-R6-016`, `WFR-17`)."""

    def _bundle_dir(self, repo):
        # Outside repo.root, same reasoning as TestManifestWriteSafety
        # above: the real .ai-review/ is gitignored, this scratch repo has
        # no such entry, and that is incidental to what these tests check.
        bundle_dir = Path(tempfile.mkdtemp(prefix="wf-fingerprint-bundle-"))
        self.addCleanup(shutil.rmtree, bundle_dir, ignore_errors=True)
        return bundle_dir

    def test_write_manifest_records_current_worktree_root_and_head(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            bundle_dir = self._bundle_dir(repo)
            _write_review_request_with_content_id(repo, bundle_dir)
            wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            meta = wf.read_manifest_generation_metadata(bundle_dir / "MANIFEST.md")
            expected_root, expected_head = wf.current_worktree_root_and_head(repo.root)
            self.assertEqual(meta["worktree_root"], expected_root)
            self.assertEqual(meta["generation_head"], expected_head)

    def test_local_generation_check_passes_in_the_generating_worktree(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            bundle_dir = self._bundle_dir(repo)
            _write_review_request_with_content_id(repo, bundle_dir)
            wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            wf.assert_local_generation_matches(repo.root, bundle_dir / "MANIFEST.md")

    def test_local_generation_check_stops_on_worktree_root_mismatch(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            bundle_dir = self._bundle_dir(repo)
            _write_review_request_with_content_id(repo, bundle_dir)
            wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            manifest_path = bundle_dir / "MANIFEST.md"
            content = manifest_path.read_text()
            content = wf._WORKTREE_ROOT_LINE_RE.sub("worktree_root: /some/other/worktree", content)
            manifest_path.write_text(content)
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path)

    def test_local_generation_check_stops_on_head_mismatch(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            bundle_dir = self._bundle_dir(repo)
            _write_review_request_with_content_id(repo, bundle_dir)
            wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            manifest_path = bundle_dir / "MANIFEST.md"
            content = manifest_path.read_text()
            fake_head = "f" * 40
            content = wf._GENERATION_HEAD_LINE_RE.sub(f"generation_head: {fake_head}", content)
            manifest_path.write_text(content)
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path)

    def test_local_staleness_unweakened_by_reviewed_head_generation_head_split(self):
        """Item 229 (`WF8c`): the local-staleness check's original
        Milestone-8-incident coverage survives the `reviewed_implementation_
        head`/`generation_head` split unweakened -- (1) a bundle whose
        `generation_head` names a different `worktree_root` is still
        refused (mirrors `test_local_generation_check_stops_on_worktree_
        root_mismatch` above, exercised again here as part of the same
        item), and (2) a bundle whose `generation_head` is a real,
        genuine ancestor of live HEAD -- not a fabricated hash -- is still
        refused once an unrelated, later commit lands, since the check is
        exact-equality on `generation_head`, never "is an ancestor of"."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            bundle_dir = self._bundle_dir(repo)
            _write_review_request_with_content_id(repo, bundle_dir)
            wf.write_manifest_with_verified_identifiers(
                repo.root, bundle_dir, repo.base,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
            )
            manifest_path = bundle_dir / "MANIFEST.md"

            # (1) worktree_root mismatch.
            content = manifest_path.read_text()
            tampered = wf._WORKTREE_ROOT_LINE_RE.sub("worktree_root: /some/other/worktree", content)
            manifest_path.write_text(tampered)
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path)
            manifest_path.write_text(content)  # restore for part (2)

            # (2) generation_head is a genuine ancestor of live HEAD, but
            # an unrelated, later commit landed since generation.
            recorded_head = repo.head()
            (repo.root / "unrelated.txt").write_text("later, unrelated change\n")
            _run(["git", "add", "unrelated.txt"], cwd=repo.root)
            _run(["git", "commit", "-q", "-m", "unrelated later commit"], cwd=repo.root)
            self.assertNotEqual(repo.head(), recorded_head)
            ancestor_check = subprocess.run(
                ["git", "merge-base", "--is-ancestor", recorded_head, repo.head()],
                cwd=repo.root,
            )
            self.assertEqual(ancestor_check.returncode, 0)  # genuinely an ancestor, not a fabricated hash
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path)

    def test_missing_manifest_metadata_is_not_a_local_mismatch(self):
        """An older bundle generated before WF5 landed has no
        worktree_root/generation_head lines at all -- absence is not
        itself a mismatch, since there is nothing to compare against."""
        with ScratchRepo() as repo:
            bundle_dir = self._bundle_dir(repo)
            (bundle_dir / "MANIFEST.md").write_text("# Bundle Manifest\n\nreview_content_id: " + "a" * 64 + "\n")
            wf.assert_local_generation_matches(repo.root, bundle_dir / "MANIFEST.md")

    def test_current_worktree_root_and_head_ignores_worktree_identity_json(self):
        """`GPT-R62-002` (item 340): `current_worktree_root_and_head` --
        the sole function both manifest generation and every
        `assert_local_generation_matches` call site read -- derives both
        values from live Git directly, never from
        `.ai-review/runtime/WORKTREE_IDENTITY.json` (that file's role is
        dirty-`IN_PROGRESS`-checkpoint resume safety only, `D3`).
        Confirmed by planting an identity document naming a different,
        wrong `worktree_root` and confirming it is ignored in favor of
        independently-recomputed `git rev-parse` output."""
        with ScratchRepo() as repo:
            real_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], cwd=repo.root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            real_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo.root, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            identity_path = repo.root / ".ai-review" / "runtime" / "WORKTREE_IDENTITY.json"
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            identity_path.write_text(json.dumps({
                "workflow-v2-1-core": {
                    "worktree_root": "/some/stale/pre-relocation/path",
                    "git_common_dir": "/nowhere/.git",
                }
            }))
            root, head = wf.current_worktree_root_and_head(repo.root)
            self.assertEqual(root, real_root)
            self.assertEqual(head, real_head)
            self.assertNotEqual(root, "/some/stale/pre-relocation/path")

    # --- Strict local-generation metadata mode (`GPT-R62-001`, item (k)
    # of `WF8c`'s scope -- `require_metadata=True`) -----------------------

    def _manifest_with(self, repo, transforms):
        """Writes a real manifest, then applies each `content -> content`
        callable in `transforms` in sequence and rewrites the file."""
        bundle_dir = self._bundle_dir(repo)
        _write_review_request_with_content_id(repo, bundle_dir)
        wf.write_manifest_with_verified_identifiers(
            repo.root, bundle_dir, repo.base,
            work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=7,
        )
        manifest_path = bundle_dir / "MANIFEST.md"
        content = manifest_path.read_text()
        for transform in transforms:
            content = transform(content)
        manifest_path.write_text(content)
        return manifest_path

    def test_strict_mode_passes_with_both_fields_well_formed_and_matching(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(repo, [])
            wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)

    def test_strict_mode_rejects_missing_worktree_root_only(self):
        """Item 335(h)'s scenario, exercised directly against the function
        (`WF8c`'s owed half of the item; the current-round binding check
        caller item 335 itself describes never got built -- superseded,
        revision 82, `OPUS-R102-001`)."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(
                repo, [lambda c: wf._WORKTREE_ROOT_ANY_LINE_RE.sub("", c)]
            )
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)
            # The permissive default is entirely unaffected by the same file.
            wf.assert_local_generation_matches(repo.root, manifest_path)

    def test_strict_mode_rejects_missing_generation_head_only(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(
                repo, [lambda c: wf._GENERATION_HEAD_ANY_LINE_RE.sub("", c)]
            )
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)
            wf.assert_local_generation_matches(repo.root, manifest_path)

    def test_strict_mode_rejects_both_fields_missing(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(repo, [
                lambda c: wf._WORKTREE_ROOT_ANY_LINE_RE.sub("", c),
                lambda c: wf._GENERATION_HEAD_ANY_LINE_RE.sub("", c),
            ])
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)
            wf.assert_local_generation_matches(repo.root, manifest_path)

    def test_strict_mode_rejects_duplicate_worktree_root_lines(self):
        """Item 335(m)'s scenario: one well-formed, matching line plus one
        additional malformed line -- the occurrence sub-check counts two
        lines regardless of either line's grammar, so this is never
        wrongly accepted by a well-formed-regex-count implementation."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(repo, [
                lambda c: c + "\nworktree_root: relative/not-absolute\n",
            ])
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)

    def test_strict_mode_rejects_duplicate_generation_head_lines(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(repo, [
                lambda c: c + "\ngeneration_head: " + "b" * 39 + "\n",
            ])
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)

    def test_strict_mode_rejects_malformed_worktree_root_not_absolute(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(repo, [
                lambda c: wf._WORKTREE_ROOT_LINE_RE.sub("worktree_root: relative/not-absolute", c),
            ])
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)

    def test_strict_mode_rejects_malformed_generation_head_wrong_length(self):
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(repo, [
                lambda c: wf._GENERATION_HEAD_LINE_RE.sub("generation_head: " + "c" * 39, c),
            ])
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)

    def test_strict_mode_still_detects_value_mismatch_once_presence_checks_pass(self):
        """Presence/grammar clearing is not itself sufficient -- the
        existing value-equality comparison still runs afterward."""
        with ScratchRepo() as repo:
            repo.write_plan_docs()
            repo.commit_plan_docs_as_base()
            manifest_path = self._manifest_with(repo, [
                lambda c: wf._WORKTREE_ROOT_LINE_RE.sub("worktree_root: /some/other/worktree", c),
            ])
            with self.assertRaises(wf.WorktreeOrHeadMismatchError):
                wf.assert_local_generation_matches(repo.root, manifest_path, require_metadata=True)

    def test_require_metadata_default_false_matches_pre_existing_behavior(self):
        """Item 339: `require_metadata`'s default is `False`, and passing
        it explicitly changes nothing relative to omitting it -- the two
        already-live permissive callers (`/approve-review`, `/review-plan`)
        are unaffected by strict mode's existence."""
        with ScratchRepo() as repo:
            bundle_dir = self._bundle_dir(repo)
            (bundle_dir / "MANIFEST.md").write_text("# Bundle Manifest\n\nreview_content_id: " + "a" * 64 + "\n")
            wf.assert_local_generation_matches(repo.root, bundle_dir / "MANIFEST.md")
            wf.assert_local_generation_matches(repo.root, bundle_dir / "MANIFEST.md", require_metadata=False)


class TestStageCompletenessCheck(unittest.TestCase):
    """`assert_stage_completeness`: a bundle's own author-written stage
    document must state the revision the authoritative source currently
    declares (unchanged design from round 5's `R5-PLAN-015`)."""

    def test_plan_stage_passes_when_revision_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "PLAN.md").write_text("# Plan (Revision 7)\n\ncontent\n")
            wf.assert_stage_completeness(bundle_dir, "plan", plan_revision=7)

    def test_plan_stage_fails_on_stale_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "PLAN.md").write_text("# Plan (Revision 6)\n\ncontent\n")
            with self.assertRaises(wf.StageCompletenessError):
                wf.assert_stage_completeness(bundle_dir, "plan", plan_revision=7)

    def test_plan_stage_fails_when_marker_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "PLAN.md").write_text("# Plan\n\ncontent, no revision marker\n")
            with self.assertRaises(wf.StageCompletenessError):
                wf.assert_stage_completeness(bundle_dir, "plan", plan_revision=7)

    def test_implementation_stage_passes_when_revision_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "IMPLEMENTATION_SUMMARY.md").write_text(
                "implementation_revision: 3\n\nwhat was built\n"
            )
            wf.assert_stage_completeness(bundle_dir, "implementation", implementation_revision=3)
            wf.assert_stage_completeness(bundle_dir, "post-fix", implementation_revision=3)

    def test_implementation_stage_fails_on_stale_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "IMPLEMENTATION_SUMMARY.md").write_text(
                "implementation_revision: 2\n\nwhat was built\n"
            )
            with self.assertRaises(wf.StageCompletenessError):
                wf.assert_stage_completeness(bundle_dir, "implementation", implementation_revision=3)

    def test_functional_review_stage_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            wf.assert_stage_completeness(bundle_dir, "functional-review")

    def test_unknown_stage_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            with self.assertRaises(wf.StageCompletenessError):
                wf.assert_stage_completeness(bundle_dir, "not-a-real-stage")


class TestTestResultsConsistencyCheck(unittest.TestCase):
    """`assert_test_results_consistent_with_plan_review_request` (item
    272): a plan-stage bundle's `TEST_RESULTS.md` must state this
    round's own stage/revision/HEAD, the same discipline
    `assert_stage_completeness` already applies to `PLAN.md`/
    `IMPLEMENTATION_SUMMARY.md`."""

    _HEAD = "a" * 40
    _OTHER_HEAD = "b" * 40

    def test_passes_when_stage_revision_and_head_all_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "TEST_RESULTS.md").write_text(
                f"# Test Results\n\nstage: plan (revision 7)\nhead: {self._HEAD}\n\nmore prose\n"
            )
            wf.assert_test_results_consistent_with_plan_review_request(bundle_dir, 7, self._HEAD)

    def test_fails_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            with self.assertRaises(wf.TestResultsStaleError):
                wf.assert_test_results_consistent_with_plan_review_request(bundle_dir, 7, self._HEAD)

    def test_fails_when_file_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "TEST_RESULTS.md").write_text("")
            with self.assertRaises(wf.TestResultsStaleError):
                wf.assert_test_results_consistent_with_plan_review_request(bundle_dir, 7, self._HEAD)

    def test_fails_on_stale_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "TEST_RESULTS.md").write_text(f"stage: plan (revision 6)\nhead: {self._HEAD}\n")
            with self.assertRaises(wf.TestResultsStaleError):
                wf.assert_test_results_consistent_with_plan_review_request(bundle_dir, 7, self._HEAD)

    def test_fails_on_carried_forward_implementation_stage_content(self):
        """Leftover evidence from a previous implementation round never
        matches the required literal `stage: plan (revision N)` -- caught
        by the same single check as a stale revision, never silently
        accepted as this round's plan-stage evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "TEST_RESULTS.md").write_text(
                f"stage: implementation (revision 3)\nhead: {self._HEAD}\n\nfull suite green\n"
            )
            with self.assertRaises(wf.TestResultsStaleError):
                wf.assert_test_results_consistent_with_plan_review_request(bundle_dir, 7, self._HEAD)

    def test_fails_when_head_line_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "TEST_RESULTS.md").write_text("stage: plan (revision 7)\n")
            with self.assertRaises(wf.TestResultsStaleError):
                wf.assert_test_results_consistent_with_plan_review_request(bundle_dir, 7, self._HEAD)

    def test_fails_on_stale_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "TEST_RESULTS.md").write_text(
                f"stage: plan (revision 7)\nhead: {self._OTHER_HEAD}\n"
            )
            with self.assertRaises(wf.TestResultsStaleError):
                wf.assert_test_results_consistent_with_plan_review_request(bundle_dir, 7, self._HEAD)


class TestFeedbackBindingFields(unittest.TestCase):
    """`WFR-03`: external feedback is matched against `bundle_id` exactly;
    stale/missing feedback is rejected naming both values."""

    def _feedback(self, *, status="APPROVE", bundle_id=FAKE_ID_A, base_commit="a" * 40, work_item="workflow-v2-1-core"):
        return (
            f"# Review Decision\n\nStatus: {status}\n\n"
            f"Reviewed bundle ID: {bundle_id}\n"
            f"Reviewed base commit: {base_commit}\n"
            f"Work item: {work_item}\n"
        )

    def test_parses_all_four_fields(self):
        fields = wf.parse_review_feedback_binding_fields(self._feedback())
        self.assertEqual(fields["status"], "APPROVE")
        self.assertEqual(fields["reviewed_bundle_id"], FAKE_ID_A)
        self.assertEqual(fields["reviewed_base_commit"], "a" * 40)
        self.assertEqual(fields["work_item"], "workflow-v2-1-core")

    def test_missing_fields_parse_as_none(self):
        fields = wf.parse_review_feedback_binding_fields("# Review Decision\n\nStatus: APPROVE\n")
        self.assertIsNone(fields["reviewed_bundle_id"])
        self.assertIsNone(fields["reviewed_base_commit"])
        self.assertIsNone(fields["work_item"])

    def test_assert_matches_passes_on_agreement(self):
        fields = wf.parse_review_feedback_binding_fields(self._feedback())
        wf.assert_feedback_matches_bundle(
            fields, bundle_id=FAKE_ID_A, base_commit="a" * 40, work_item_id="workflow-v2-1-core"
        )

    def test_assert_matches_rejects_missing_field_naming_it(self):
        fields = wf.parse_review_feedback_binding_fields("# Review Decision\n\nStatus: APPROVE\n")
        with self.assertRaises(wf.MissingFeedbackBindingFieldError):
            wf.assert_feedback_matches_bundle(
                fields, bundle_id=FAKE_ID_A, base_commit="a" * 40, work_item_id="workflow-v2-1-core"
            )

    def test_assert_matches_rejects_stale_bundle_id(self):
        fields = wf.parse_review_feedback_binding_fields(self._feedback(bundle_id=FAKE_ID_B))
        with self.assertRaises(wf.FeedbackBundleMismatchError):
            wf.assert_feedback_matches_bundle(
                fields, bundle_id=FAKE_ID_A, base_commit="a" * 40, work_item_id="workflow-v2-1-core"
            )

    def test_assert_matches_rejects_stale_base_commit(self):
        fields = wf.parse_review_feedback_binding_fields(self._feedback(base_commit="b" * 40))
        with self.assertRaises(wf.FeedbackBundleMismatchError):
            wf.assert_feedback_matches_bundle(
                fields, bundle_id=FAKE_ID_A, base_commit="a" * 40, work_item_id="workflow-v2-1-core"
            )

    def test_assert_matches_rejects_wrong_work_item(self):
        fields = wf.parse_review_feedback_binding_fields(self._feedback(work_item="milestone-8"))
        with self.assertRaises(wf.FeedbackBundleMismatchError):
            wf.assert_feedback_matches_bundle(
                fields, bundle_id=FAKE_ID_A, base_commit="a" * 40, work_item_id="workflow-v2-1-core"
            )


class TestFeedbackNotOwnedByOtherWorkItem(unittest.TestCase):
    """`workflow-v2-3-followups` `CP2` (REQ-4/REQ-21, `GPT-FUP-R6-I01`,
    `LPR-R7-B01`): `/review-implementation`'s new pre-write ownership
    guard. Refuses rather than relocates, and never touches
    `resolve_feedback_dir` or the filesystem itself -- it is a pure
    function over already-read content."""

    def _feedback(self, *, work_item="workflow-v2-1-core", bundle_id=FAKE_ID_A):
        return (
            f"# Review Decision\n\nStatus: APPROVE\n\n"
            f"Reviewed bundle ID: {bundle_id}\n"
            f"Reviewed base commit: {'a' * 40}\n"
            f"Work item: {work_item}\n"
        )

    def test_no_existing_file_is_unowned(self):
        wf.assert_feedback_not_owned_by_other_work_item(None, work_item_id="workflow-v2-3-followups")

    def test_same_work_item_overwrite_is_allowed(self):
        wf.assert_feedback_not_owned_by_other_work_item(
            self._feedback(work_item="workflow-v2-3-followups"),
            work_item_id="workflow-v2-3-followups",
        )

    def test_unparseable_work_item_field_is_treated_as_unowned(self):
        wf.assert_feedback_not_owned_by_other_work_item(
            "# Review Decision\n\nStatus: APPROVE\n",  # predates the binding-field convention
            work_item_id="workflow-v2-3-followups",
        )

    def test_different_work_item_refuses_naming_both(self):
        with self.assertRaises(wf.FeedbackOwnedByOtherWorkItemError) as ctx:
            wf.assert_feedback_not_owned_by_other_work_item(
                self._feedback(work_item="workflow-v2-1-core"),
                work_item_id="workflow-v2-3-followups",
            )
        self.assertIn("workflow-v2-1-core", str(ctx.exception))
        self.assertIn("workflow-v2-3-followups", str(ctx.exception))


class TestReviewImplementationWritebackCrossWorkItemIsolation(unittest.TestCase):
    """`workflow-v2-3-followups` `CP2` (REQ-21, `GPT-FUP-R6-I01`,
    `LPR-R7-B01`, extended `LPR-R8-I01`): the concrete scenario the new
    guard exists to prevent -- two work items resolving the identical flat
    `resolve_feedback_dir` path, since neither yet has its own scoped
    `.ai-review/<work_item_id>/feedback/` directory. Drives the real
    resolver, not a stand-in path.

    Re-pointed by `D-Feedback-Layout` (workflow-2.6.0): the shared flat
    path, and so the "creates no scoped dir" assertion, now applies to
    **legacy** items only -- this fixture has no `WORKFLOW_STATE.json`,
    so neither item carries a `feedback_layout` stamp. Two scoped items
    never share a path at all (`TestFeedbackLayout`)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name)
        (self.repo_root / ".ai-review" / "feedback").mkdir(parents=True)

    def _write_a_feedback(self, work_item="work-item-a"):
        path = self.repo_root / wf.resolve_feedback_dir(self.repo_root, "work-item-a")
        content = (
            f"# Review Decision\n\nStatus: APPROVE\n\n"
            f"Reviewed bundle ID: {FAKE_ID_A}\n"
            f"Reviewed base commit: {'a' * 40}\n"
            f"Work item: {work_item}\n"
        )
        (path / "REVIEW_FEEDBACK.md").write_text(content)
        return path / "REVIEW_FEEDBACK.md", content

    def test_refused_run_for_b_leaves_as_own_feedback_byte_identical_and_creates_no_scoped_dir(self):
        a_file, a_content = self._write_a_feedback(work_item="work-item-a")
        # Both A and B resolve the same flat path today -- neither has a
        # scoped directory of its own yet.
        self.assertEqual(
            wf.resolve_feedback_dir(self.repo_root, "work-item-b"), Path(".ai-review/feedback"),
        )

        existing = a_file.read_text()
        with self.assertRaises(wf.FeedbackOwnedByOtherWorkItemError):
            wf.assert_feedback_not_owned_by_other_work_item(existing, work_item_id="work-item-b")

        self.assertEqual(a_file.read_text(), a_content, "A's feedback must survive byte-identical")
        self.assertFalse(
            (self.repo_root / ".ai-review" / "work-item-b").exists(),
            "the refused run must create no .ai-review/work-item-b/feedback/ directory as a side effect",
        )

    def test_a_may_overwrite_its_own_feedback_at_the_same_flat_path(self):
        a_file, _ = self._write_a_feedback(work_item="work-item-a")
        wf.assert_feedback_not_owned_by_other_work_item(
            a_file.read_text(), work_item_id="work-item-a",
        )


class TestReviewImplementationFeedbackBindingRoundTrip(unittest.TestCase):
    """`workflow-v2-3-followups` `CP2` (REQ-5): a freshly composed
    `REVIEW_FEEDBACK.md` in exactly the shape `/review-implementation`
    step 6 composes -- the three binding fields plus the non-binding
    `Reviewed review content ID:` line -- parses and validates cleanly
    through the same shared, stage-agnostic parsers `/review-plan` and
    `/approve-review` already use, with the extra line ignored rather than
    breaking parsing."""

    def test_freshly_written_feedback_binds_successfully(self):
        content = (
            "# Review Decision\n\n"
            "Status: APPROVE\n\n"
            f"Reviewed bundle ID: {FAKE_ID_A}\n"
            f"Reviewed base commit: {'c' * 40}\n"
            "Work item: workflow-v2-3-followups\n"
            f"Reviewed review content ID: {FAKE_ID_B}\n"
        )
        fields = wf.parse_review_feedback_binding_fields(content)
        wf.assert_feedback_matches_bundle(
            fields, bundle_id=FAKE_ID_A, base_commit="c" * 40,
            work_item_id="workflow-v2-3-followups",
        )


class TestPinnedReviewContentIdLabel(unittest.TestCase):
    """workflow-2.7.0 `D-Feedback-Label` (`v2.6.0-002`, CP1): one pinned
    `Reviewed review_content_id:` label, the spaced form kept as a legacy
    alias, read from the header block only; `Status:` and the three
    binding fields keep their 2.6.0 whole-file scan."""

    def _verdict(self, *id_lines, body=""):
        return (
            "# Review Decision\n\n"
            "Status: REVISE\n"
            "Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW\n\n"
            f"Reviewed bundle ID: {FAKE_ID_A}\n"
            f"Reviewed base commit: {'c' * 40}\n"
            "Work item: workflow-manager-trunk-model\n"
            + "".join(f"{line}\n" for line in id_lines)
            + "\n## Blocking findings\n\n"
            + body
        )

    def test_pinned_label_only_parses(self):
        content = self._verdict(f"Reviewed review_content_id: {FAKE_ID_B}")
        self.assertEqual(wf.parse_feedback_review_content_id(content), FAKE_ID_B)

    def test_legacy_alias_only_parses(self):
        content = self._verdict(f"Reviewed review content ID: {FAKE_ID_B}")
        self.assertEqual(wf.parse_feedback_review_content_id(content), FAKE_ID_B)

    def test_bare_form_parses(self):
        content = self._verdict(f"review_content_id: {FAKE_ID_B}")
        self.assertEqual(wf.parse_feedback_review_content_id(content), FAKE_ID_B)

    def test_alias_is_case_insensitive(self):
        content = self._verdict(f"reviewed Review Content id: {FAKE_ID_B}")
        self.assertEqual(wf.parse_feedback_review_content_id(content), FAKE_ID_B)

    def test_alias_plus_pinned_with_the_same_value_parses(self):
        content = self._verdict(
            f"Reviewed review_content_id: {FAKE_ID_B}",
            f"Reviewed review content ID: {FAKE_ID_B}",
        )
        self.assertEqual(wf.parse_feedback_review_content_id(content), FAKE_ID_B)

    def test_two_labels_with_different_values_give_none(self):
        content = self._verdict(
            f"Reviewed review_content_id: {FAKE_ID_B}",
            f"Reviewed review content ID: {FAKE_ID_A}",
        )
        self.assertIsNone(wf.parse_feedback_review_content_id(content))

    def test_id_after_the_first_section_following_a_field_is_ignored(self):
        content = self._verdict(body=f"Reviewed review_content_id: {FAKE_ID_B}\n")
        self.assertIsNone(wf.parse_feedback_review_content_id(content))

    def test_verdict_opening_with_review_decision_section_parses_every_field(self):
        content = (
            "## Review Decision\n\n"
            "Status: APPROVE\n"
            "Reviewer role: LOCAL_MODEL_PLAN_REVIEW\n"
            f"Reviewed bundle ID: {FAKE_ID_A}\n"
            f"Reviewed base commit: {'c' * 40}\n"
            "Work item: orchestration-protocol-v1\n"
            f"Reviewed review_content_id: {FAKE_ID_B}\n\n"
            "## Blocking findings\n\nNone.\n"
        )
        self.assertEqual(
            wf.parse_review_feedback_header(content),
            {
                "status": "APPROVE",
                "reviewer_role": "LOCAL_MODEL_PLAN_REVIEW",
                "reviewed_bundle_id": FAKE_ID_A,
                "reviewed_base_commit": "c" * 40,
                "work_item": "orchestration-protocol-v1",
                "review_content_id": FAKE_ID_B,
                "reviewer_model": None,
            },
        )

    def test_reviewer_model_is_read_from_the_header_block_only(self):
        # CP2 (`LPR-R17-O1`, `LPR-R17-O2`): the declared `<vendor>/<model>` form
        # parses from the header; the same text quoted in the body declares nothing.
        header = self._verdict(
            f"Reviewed review_content_id: {FAKE_ID_B}", "Reviewer model: anthropic/claude-opus-5-5")
        self.assertEqual(wf.parse_review_feedback_header(header)["reviewer_model"], "anthropic/claude-opus-5-5")
        body_only = self._verdict(
            f"Reviewed review_content_id: {FAKE_ID_B}",
            body="- The reviewer wrote\nReviewer model: openai/x\nin the paste.\n")
        self.assertIsNone(wf.parse_review_feedback_header(body_only)["reviewer_model"])
        conflicting = self._verdict(
            f"Reviewed review_content_id: {FAKE_ID_B}", "Reviewer model: a/b", "Reviewer model: c/d")
        self.assertIsNone(wf.parse_feedback_reviewer_model(conflicting), "an ambiguous statement never matches")

    def test_finding_quoting_another_id_leaves_the_parse_intact(self):
        content = self._verdict(
            f"Reviewed review_content_id: {FAKE_ID_B}",
            body=f"- The earlier round reviewed\n  review_content_id: {FAKE_ID_A}\n",
        )
        self.assertEqual(wf.parse_feedback_review_content_id(content), FAKE_ID_B)

    def test_header_on_the_m1_round_1_verdict_shape(self):
        # Workflow-manager M1, MANUAL_EXTERNAL_PLAN_REVIEW round 1: the
        # underscore form only, which Controller 1.3.0 read as absent.
        content = self._verdict(f"Reviewed review_content_id: {FAKE_ID_B}", body="- finding\n")
        header = wf.parse_review_feedback_header(content)
        self.assertEqual(header["status"], "REVISE")
        self.assertEqual(header["reviewer_role"], "MANUAL_EXTERNAL_PLAN_REVIEW")
        self.assertEqual(header["work_item"], "workflow-manager-trunk-model")
        self.assertEqual(header["review_content_id"], FAKE_ID_B)

    def test_header_on_the_controller_archived_spaced_only_shape(self):
        content = (
            "# Review Decision\n\n"
            "Status: REVISE\n"
            "Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW\n"
            f"Reviewed bundle ID: {FAKE_ID_A}\n"
            f"Reviewed base commit: {'c' * 40}\n"
            "Work item: workflow-controller-gen1\n"
            f"Reviewed review content ID: {FAKE_ID_B}\n\n"
            "## Blocking findings\n\n"
            "Status: this is prose quoting the template, never a live field.\n"
        )
        header = wf.parse_review_feedback_header(content)
        self.assertEqual(header["status"], "REVISE")
        self.assertEqual(header["reviewed_bundle_id"], FAKE_ID_A)
        self.assertEqual(header["review_content_id"], FAKE_ID_B)

    def test_absent_fields_parse_as_none(self):
        header = wf.parse_review_feedback_header("# Review Decision\n\nStatus: APPROVE\n")
        self.assertEqual(header["status"], "APPROVE")
        for key in ("reviewer_role", "reviewed_bundle_id", "reviewed_base_commit",
                    "work_item", "review_content_id"):
            self.assertIsNone(header[key], key)

    def test_binding_fields_keep_the_whole_file_scan(self):
        content = (
            "# Review Decision\n\n"
            f"Reviewed bundle ID: {FAKE_ID_A}\n\n"
            "## Notes\n\n"
            "Status: APPROVE\n"
            "Work item: orchestration-protocol-v1\n"
        )
        fields = wf.parse_review_feedback_binding_fields(content)
        self.assertEqual(fields["status"], "APPROVE")
        self.assertEqual(fields["work_item"], "orchestration-protocol-v1")
        header = wf.parse_review_feedback_header(content)
        self.assertEqual(header["status"], "APPROVE")
        self.assertEqual(header["work_item"], "orchestration-protocol-v1")

    def test_each_command_file_names_exactly_the_pinned_label(self):
        commands = Path(__file__).resolve().parent.parent / ".claude" / "commands"
        for name in ("review-plan.md", "review-implementation.md",
                     "record-manual-plan-review.md", "record-manual-implementation-review.md"):
            text = (commands / name).read_text()
            self.assertIn(wf.FEEDBACK_REVIEW_CONTENT_ID_LABEL, text, name)
        for name in ("review-plan.md", "review-implementation.md"):
            text = (commands / name).read_text()
            self.assertNotIn("Reviewed review content ID:", text, name)


class TestBinaryAndUnusualPathBundleEntries(unittest.TestCase):
    """`OPUS-R6-019`/`WFR-05`: `compute_bundle_id` treats bundle-file
    content as opaque bytes throughout, so binaries and unusual-but-
    supported paths (newline-in-path, non-ASCII path) round-trip
    deterministically -- restored to explicit test-vector coverage
    (previously only asserted in the module docstring)."""

    def test_binary_file_hashes_by_raw_bytes_never_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "MANIFEST.md").write_text(f"review_content_id: {'a' * 64}\n")
            binary_content = bytes(range(256)) + b"\xff\xfe\x00\x01"
            (bundle_dir / "files").mkdir()
            (bundle_dir / "files" / "blob.bin").write_bytes(binary_content)
            digest1, entries1 = wf.compute_bundle_id(bundle_dir)
            digest2, entries2 = wf.compute_bundle_id(bundle_dir)
            self.assertEqual(digest1, digest2)
            self.assertEqual(
                entries1["files/blob.bin"]["sha256"], hashlib.sha256(binary_content).hexdigest()
            )
            self.assertEqual(entries1, entries2)

    def test_different_binary_content_changes_bundle_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "MANIFEST.md").write_text(f"review_content_id: {'a' * 64}\n")
            (bundle_dir / "files").mkdir()
            (bundle_dir / "files" / "blob.bin").write_bytes(b"\x00\x01\x02")
            digest1, _ = wf.compute_bundle_id(bundle_dir)
            (bundle_dir / "files" / "blob.bin").write_bytes(b"\x00\x01\x03")
            digest2, _ = wf.compute_bundle_id(bundle_dir)
            self.assertNotEqual(digest1, digest2)

    def test_non_ascii_path_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "MANIFEST.md").write_text(f"review_content_id: {'a' * 64}\n")
            (bundle_dir / "files").mkdir()
            (bundle_dir / "files" / "café.txt").write_text("espresso\n")
            digest, entries = wf.compute_bundle_id(bundle_dir)
            self.assertIn("files/café.txt", entries)
            digest_again, _ = wf.compute_bundle_id(bundle_dir)
            self.assertEqual(digest, digest_again)

    def test_newline_in_path_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "MANIFEST.md").write_text(f"review_content_id: {'a' * 64}\n")
            (bundle_dir / "files").mkdir()
            weird_name = "line1\nline2.txt"
            (bundle_dir / "files" / weird_name).write_text("content\n")
            digest, entries = wf.compute_bundle_id(bundle_dir)
            self.assertIn(f"files/{weird_name}", entries)
            digest_again, _ = wf.compute_bundle_id(bundle_dir)
            self.assertEqual(digest, digest_again)


def _metadata_for(repo, plan_revision=7, work_item_id="workflow-v2-1-core"):
    """A hand-built `PlanStageMetadata` for `ScratchRepo`'s own fixed
    plan-doc layout -- bypasses `resolve_plan_stage_metadata`'s
    `WORKFLOW_STATE.json`/artifacts-declaration machinery entirely, which
    every low-level test in this file already does for
    `compute_review_content_id_plan_stage` itself."""
    return wf.PlanStageMetadata(
        work_item_id=work_item_id,
        work_item_type="process",
        plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
        registry_path="docs/ai-workflow/registry/workflow-v2-1-core-registry.json",
        mapping_path="docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json",
        base_commit=repo.base,
        plan_revision=plan_revision,
        protected_paths=wf.PLAN_STAGE_PROTECTED,
        excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
        excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
    )


class TestGeneratorSideStageDocumentBinding(unittest.TestCase):
    """`WFR-67`, `WF8c` item (h) part 2: `capture_plan_stage_pin`,
    `derive_plan_stage_document`, the pin-sourced identity computation,
    the closing byte-identity assertion, and the `REJECTED`-marker
    withdrawal/clearing lifecycle."""

    @staticmethod
    @contextmanager
    def _seeded_repo(plan_revision=7, **plan_doc_kwargs):
        """A `ScratchRepo` with plan docs committed as base, seeded with a
        `.gitignore` covering `.ai-review/` -- exactly this repository's
        own real setup (`.gitignore`'s first line), needed because every
        test below writes a private pin snapshot under `.ai-review/`, and
        an unignored one would otherwise show up as an unclassified
        untracked path to the (still-live) plan-stage classifier the
        pre-existing `write_manifest_with_verified_identifiers`/
        `compute_review_content_id_plan_stage` recompute step calls.
        `registry_text` carries a `plan_revision` field matching
        `plan_text`'s own `(Revision N)` marker, so
        `resolve_plan_stage_metadata`'s `load_plan_revision`
        cross-check -- reached by the small number of tests below that
        exercise the real resolver rather than `_metadata_for`'s
        hand-built bypass -- succeeds too."""
        plan_doc_kwargs.setdefault("plan_text", f"plan body (Revision {plan_revision})\n")
        plan_doc_kwargs.setdefault(
            "registry_text", json.dumps({
                "work_item_id": "workflow-v2-1-core", "checkpoints": [], "plan_revision": plan_revision,
            }) + "\n",
        )
        plan_doc_kwargs.setdefault(
            "mapping_text", json.dumps({
                "work_item_id": "workflow-v2-1-core", "requirements": {},
            }) + "\n",
        )
        with ScratchRepo() as repo:
            repo.write_plan_docs(**plan_doc_kwargs)
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            repo.commit_plan_docs_as_base()
            yield repo

    @staticmethod
    def _seed_workflow_state(repo, metadata):
        """Seeds the minimal `WORKFLOW_STATE.json`/`<id>-artifacts.json`
        pair `resolve_plan_stage_metadata` needs -- only for the tests
        below that exercise it directly (`finalize_bundle_generation`'s
        plan-stage branch, matching the real `prepare-ai-review.sh`-driven
        flow, unlike every other test in this class). Both live under
        plan-stage excluded paths/prefixes, so leaving them untracked is
        safe -- they never need to be part of the pinned snapshot."""
        artifacts_dir = repo.root / "docs" / "ai-workflow" / "registry"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / f"{metadata.work_item_id}-artifacts.json").write_text(json.dumps({
            "schema_version": 2,
            "work_item_id": metadata.work_item_id,
            "plan_stage": {
                "protected_paths": sorted(metadata.protected_paths),
                "excluded_paths": dict(metadata.excluded_paths),
                "excluded_prefixes": dict(metadata.excluded_prefixes),
            },
        }))
        (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(json.dumps({
            "schema_version": 1,
            "active_work_item_id": metadata.work_item_id,
            "work_items": {
                metadata.work_item_id: {
                    "work_item_id": metadata.work_item_id,
                    "work_item_type": metadata.work_item_type,
                    "plan_path": metadata.plan_path,
                    "registry_path": metadata.registry_path,
                    "mapping_path": metadata.mapping_path,
                    "base_commit": metadata.base_commit,
                    "plan_revision": metadata.plan_revision,
                },
            },
        }))

    def test_capture_pin_copies_exact_bytes_of_every_protected_path(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            for rel_path in wf.PLAN_STAGE_PROTECTED:
                self.assertEqual(
                    (pin_dir / rel_path).read_bytes(), (repo.root / rel_path).read_bytes(),
                )

    def test_capture_pin_fails_closed_on_absent_protected_path(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            (repo.root / metadata.plan_path).unlink()
            with self.assertRaises(wf.AbsentProtectedPathError):
                wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)

    def test_capture_pin_fails_closed_on_symlinked_protected_path(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            target = repo.root / metadata.plan_path
            real = repo.root / "real-plan.md"
            real.write_text(target.read_text())
            target.unlink()
            target.symlink_to(real)
            with self.assertRaises(wf.AbsentProtectedPathError):
                wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)

    def test_capture_pin_replaces_a_prior_pin_atomically(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            (repo.root / metadata.plan_path).write_text("plan v2 (Revision 7)\n")
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            self.assertEqual((pin_dir / metadata.plan_path).read_text(), "plan v2 (Revision 7)\n")
            self.assertFalse((pin_dir.with_name(".pin.tmp")).exists())

    def test_derive_writes_plan_md_from_pin_and_passes_stage_completeness(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo, plan_revision=7)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            bundle_dir = repo.root / ".ai-review" / "workflow-v2-1-core" / "current"
            bundle_dir.mkdir(parents=True)
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            self.assertEqual((bundle_dir / "PLAN.md").read_text(), "plan body (Revision 7)\n")

    def test_derive_overwrites_unconditionally_never_create_if_missing(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            bundle_dir = repo.root / ".ai-review" / "workflow-v2-1-core" / "current"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "PLAN.md").write_text("stale leftover copy\n")
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            self.assertEqual((bundle_dir / "PLAN.md").read_text(), "plan body (Revision 7)\n")

    def test_derive_fails_closed_when_pinned_plan_revision_is_stale(self):
        with self._seeded_repo(plan_revision=6) as repo:
            metadata = _metadata_for(repo, plan_revision=7)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            bundle_dir = repo.root / ".ai-review" / "workflow-v2-1-core" / "current"
            bundle_dir.mkdir(parents=True)
            with self.assertRaises(wf.StageCompletenessError):
                wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)

    def test_refresh_files_copy_overwrites_stale_protected_path_copy(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            bundle_dir = repo.root / ".ai-review" / "workflow-v2-1-core" / "current"
            files_copy = bundle_dir / "files" / metadata.plan_path
            files_copy.parent.mkdir(parents=True)
            files_copy.write_text("stale diff-based copy, edited after pin capture\n")
            wf.refresh_files_copy_from_pin(pin_dir, bundle_dir, metadata)
            self.assertEqual(files_copy.read_text(), "plan body (Revision 7)\n")

    def test_refresh_files_copy_never_creates_an_absent_entry(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            bundle_dir = repo.root / ".ai-review" / "workflow-v2-1-core" / "current"
            bundle_dir.mkdir(parents=True)
            wf.refresh_files_copy_from_pin(pin_dir, bundle_dir, metadata)
            self.assertFalse((bundle_dir / "files" / metadata.plan_path).exists())

    def test_pin_sourced_digest_matches_live_when_nothing_changed(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            live_digest, _ = repo.compute()
            pin_digest, _ = wf.compute_review_content_id_plan_stage_from_pin(
                repo.root, pin_dir, repo.base, work_item_type="process",
                work_item_id="workflow-v2-1-core", plan_revision=7,
                protected=wf.PLAN_STAGE_PROTECTED, excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
                excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            )
            self.assertEqual(live_digest, pin_digest)

    def test_pin_sourced_digest_diverges_from_live_after_a_late_protected_edit(self):
        """The exact staleness `OPUS-R91-001` requires be caught: a
        protected path edited after the pin was captured must make the
        pin-sourced digest disagree with a fresh live recompute -- the
        comparison `write_manifest_with_verified_identifiers`'s own
        pin-vs-live idempotence check relies on."""
        with self._seeded_repo(decisions_text="decisions v1\n") as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            (repo.root / "docs" / "TECHNICAL_DECISIONS.md").write_text("decisions v2, edited after pin\n")
            live_digest, _ = repo.compute()
            pin_digest, _ = wf.compute_review_content_id_plan_stage_from_pin(
                repo.root, pin_dir, repo.base, work_item_type="process",
                work_item_id="workflow-v2-1-core", plan_revision=7,
                protected=wf.PLAN_STAGE_PROTECTED, excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
                excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            )
            self.assertNotEqual(live_digest, pin_digest)

    def test_write_manifest_with_pin_detects_staleness_via_existing_idempotence_check(self):
        """End-to-end through the real write path: a protected path
        edited after `capture_plan_stage_pin` but before
        `write_manifest_with_verified_identifiers` runs must refuse via
        the pre-existing `ReviewContentIdNotIdempotentError`, never
        silently write a manifest bound to stale pinned bytes."""
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, "workflow-v2-1-core", metadata)
            bundle_dir = repo.root / ".ai-review" / "workflow-v2-1-core" / "current"
            bundle_dir.mkdir(parents=True)
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            _write_review_request_with_content_id(repo, bundle_dir)
            # Edit a protected path *after* the pin was captured and after
            # REVIEW_REQUEST.md was stated against the (now-stale) pin.
            (repo.root / "docs" / "TECHNICAL_DECISIONS.md").write_text("edited after pin\n")
            with self.assertRaises(wf.ReviewContentIdNotIdempotentError):
                wf.write_manifest_with_verified_identifiers(
                    repo.root, bundle_dir, repo.base, "process", "workflow-v2-1-core", 7,
                    wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                    wf.PLAN_STAGE_EXCLUDED_PREFIXES, pin_dir=pin_dir,
                )
            self.assertFalse((bundle_dir / "MANIFEST.md").exists())

    def _build_valid_pinned_bundle(
        self, repo, metadata, corrupt_derivation=False, test_results_text=None,
    ):
        """A complete, self-consistent plan-stage bundle_dir + archive,
        generated the same way `prepare-ai-review.sh` does: capture pin,
        derive PLAN.md, populate files/, write REVIEW_REQUEST.md, write
        MANIFEST.md through the pin. Returns (bundle_dir, archive_path).

        `corrupt_derivation=True` simulates a latent bug in
        `derive_plan_stage_document`/`refresh_files_copy_from_pin`
        themselves: `PLAN.md` and its `files/` copy are overwritten with
        the wrong bytes *before* `MANIFEST.md` is written and the archive
        is built, so the bundle_id three-way check stays internally
        self-consistent (manifest/on-disk/archived all agree, since all
        three are computed from -- or reproduce -- the same, already-
        corrupted tree) while `review_content_id` (computed from the pin,
        never from `PLAN.md`) stays correct -- exactly the residual gap
        only the dedicated byte-identity-vs-pin assertion catches.

        `test_results_text`, when given, replaces the default compliant
        `TEST_RESULTS.md` content *before* `MANIFEST.md` is written and
        the archive is built -- the item-272 analogue of
        `corrupt_derivation`: keeps the bundle_id three-way check
        internally self-consistent so a bad `TEST_RESULTS.md` is caught
        by the dedicated consistency assertion, not masked behind (or
        confused with) the bundle_id mismatch path."""
        pin_dir = wf.capture_plan_stage_pin(repo.root, metadata.work_item_id, metadata)
        bundle_dir = repo.root / ".ai-review" / metadata.work_item_id / "current"
        bundle_dir.mkdir(parents=True)
        wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
        (bundle_dir / "files" / metadata.plan_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pin_dir / metadata.plan_path, bundle_dir / "files" / metadata.plan_path)
        if corrupt_derivation:
            for target in (bundle_dir / "PLAN.md", bundle_dir / "files" / metadata.plan_path):
                target.write_text("consistently wrong everywhere (Revision {})\n".format(metadata.plan_revision))
        (bundle_dir / "DIFF.patch").write_text("")
        if test_results_text is None:
            _, current_head = wf.current_worktree_root_and_head(repo.root)
            test_results_text = f"stage: plan (revision {metadata.plan_revision})\nhead: {current_head}\n"
        (bundle_dir / "TEST_RESULTS.md").write_text(test_results_text)
        _write_review_request_with_content_id(
            repo, bundle_dir, base=repo.base, work_item_type=metadata.work_item_type,
            work_item_id=metadata.work_item_id, plan_revision=metadata.plan_revision,
            protected=metadata.protected_paths, excluded_paths=metadata.excluded_paths,
            excluded_prefixes=metadata.excluded_prefixes,
        )
        wf.write_manifest_with_verified_identifiers(
            repo.root, bundle_dir, repo.base, metadata.work_item_type, metadata.work_item_id,
            metadata.plan_revision, metadata.protected_paths, metadata.excluded_paths,
            metadata.excluded_prefixes, pin_dir=pin_dir,
        )
        root_dir = bundle_dir.parent
        archive_path = root_dir / "review-bundle.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(bundle_dir, arcname="current")
        return bundle_dir, archive_path

    def test_assert_document_matches_pin_passes_on_a_consistent_bundle(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, metadata.work_item_id, metadata)
            bundle_dir = repo.root / ".ai-review" / metadata.work_item_id / "current"
            bundle_dir.mkdir(parents=True)
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            with tempfile.TemporaryDirectory() as tmp:
                extracted = Path(tmp)
                shutil.copy2(bundle_dir / "PLAN.md", extracted / "PLAN.md")
                wf.assert_plan_stage_document_matches_pin(pin_dir, bundle_dir, metadata, extracted)

    def test_assert_document_matches_pin_absent_files_copy_is_not_a_failure(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, metadata.work_item_id, metadata)
            bundle_dir = repo.root / ".ai-review" / metadata.work_item_id / "current"
            bundle_dir.mkdir(parents=True)
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            with tempfile.TemporaryDirectory() as tmp:
                extracted = Path(tmp)
                shutil.copy2(bundle_dir / "PLAN.md", extracted / "PLAN.md")
                wf.assert_plan_stage_document_matches_pin(pin_dir, bundle_dir, metadata, extracted)

    def test_assert_document_matches_pin_fails_on_stale_plan_md(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, metadata.work_item_id, metadata)
            bundle_dir = repo.root / ".ai-review" / metadata.work_item_id / "current"
            bundle_dir.mkdir(parents=True)
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            (bundle_dir / "PLAN.md").write_text("mutated after derivation\n")
            with tempfile.TemporaryDirectory() as tmp:
                extracted = Path(tmp)
                shutil.copy2(bundle_dir / "PLAN.md", extracted / "PLAN.md")
                with self.assertRaises(wf.PlanStageDocumentStaleError):
                    wf.assert_plan_stage_document_matches_pin(pin_dir, bundle_dir, metadata, extracted)

    def test_assert_document_matches_pin_fails_on_stale_files_copy(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, metadata.work_item_id, metadata)
            bundle_dir = repo.root / ".ai-review" / metadata.work_item_id / "current"
            bundle_dir.mkdir(parents=True)
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            files_copy = bundle_dir / "files" / metadata.plan_path
            files_copy.parent.mkdir(parents=True)
            files_copy.write_text("stale files/ copy\n")
            with tempfile.TemporaryDirectory() as tmp:
                extracted = Path(tmp)
                shutil.copy2(bundle_dir / "PLAN.md", extracted / "PLAN.md")
                with self.assertRaises(wf.PlanStageDocumentStaleError):
                    wf.assert_plan_stage_document_matches_pin(pin_dir, bundle_dir, metadata, extracted)

    def test_assert_document_matches_pin_fails_on_stale_archived_copy(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            pin_dir = wf.capture_plan_stage_pin(repo.root, metadata.work_item_id, metadata)
            bundle_dir = repo.root / ".ai-review" / metadata.work_item_id / "current"
            bundle_dir.mkdir(parents=True)
            wf.derive_plan_stage_document(pin_dir, bundle_dir, metadata)
            with tempfile.TemporaryDirectory() as tmp:
                extracted = Path(tmp)
                extracted.mkdir(exist_ok=True)
                (extracted / "PLAN.md").write_text("stale extracted archive copy\n")
                with self.assertRaises(wf.PlanStageDocumentStaleError):
                    wf.assert_plan_stage_document_matches_pin(pin_dir, bundle_dir, metadata, extracted)

    def test_withdraw_bundle_full_success_writes_and_then_removes_marker(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(repo, metadata)
            root_dir = bundle_dir.parent
            quarantine_path = wf.withdraw_bundle(repo.root, metadata.work_item_id, "test withdrawal")
            self.assertFalse(bundle_dir.exists())
            self.assertFalse(archive_path.exists())
            self.assertFalse((quarantine_path / "MANIFEST.md").exists())
            self.assertTrue((quarantine_path / "PLAN.md").is_file())
            self.assertRegex(quarantine_path.name, r"^current\.rejected-[0-9a-f]{32}$")
            marker_path = root_dir / "REJECTED"
            self.assertFalse(marker_path.exists())

    def test_withdraw_bundle_names_the_failed_step_and_never_deletes_the_marker(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(repo, metadata)
            root_dir = bundle_dir.parent
            marker_path = root_dir / "REJECTED"
            with mock.patch.object(os, "rename", side_effect=OSError("simulated rename failure")):
                with self.assertRaises(wf.BundleWithdrawalError):
                    wf.withdraw_bundle(repo.root, metadata.work_item_id, "test withdrawal")
            self.assertTrue(marker_path.is_file())
            self.assertIn("quarantine current/", marker_path.read_text())
            # Archive and MANIFEST.md were already removed (they precede
            # the failed step); current/ itself was never renamed away.
            self.assertFalse(archive_path.exists())
            self.assertFalse((bundle_dir / "MANIFEST.md").exists())
            self.assertTrue(bundle_dir.is_dir())

    def test_withdraw_bundle_writes_marker_before_first_removal(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(repo, metadata)
            root_dir = bundle_dir.parent
            marker_path = root_dir / "REJECTED"
            real_unlink = Path.unlink
            seen_marker_before_archive_removed = {"value": False}

            def _spy_unlink(self_path, *a, **kw):
                if self_path == archive_path:
                    seen_marker_before_archive_removed["value"] = marker_path.is_file()
                return real_unlink(self_path, *a, **kw)

            with mock.patch.object(Path, "unlink", _spy_unlink):
                wf.withdraw_bundle(repo.root, metadata.work_item_id, "test withdrawal")
            self.assertTrue(seen_marker_before_archive_removed["value"])

    def test_clear_rejected_marker_removes_when_present(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            bundle_dir, _ = self._build_valid_pinned_bundle(repo, metadata)
            marker_path = bundle_dir.parent / "REJECTED"
            marker_path.write_text("REJECTED: some prior withdrawal\n")
            wf.clear_rejected_marker_if_present(repo.root, metadata.work_item_id)
            self.assertFalse(marker_path.exists())

    def test_clear_rejected_marker_is_a_no_op_when_absent(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            bundle_dir, _ = self._build_valid_pinned_bundle(repo, metadata)
            wf.clear_rejected_marker_if_present(repo.root, metadata.work_item_id)  # no raise

    def test_finalize_bundle_generation_ok_path_clears_a_stale_marker(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            self._seed_workflow_state(repo, metadata)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(repo, metadata)
            marker_path = bundle_dir.parent / "REJECTED"
            marker_path.write_text("REJECTED: leftover from an earlier round\n")
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "plan", metadata.work_item_id,
            )
            self.assertEqual(result["status"], "ok")
            self.assertFalse(marker_path.exists())

    def test_finalize_bundle_generation_withdraws_on_bundle_id_mismatch(self):
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(repo, metadata)
            # Mutate on-disk content after the archive was built, so the
            # manifest/on-disk/archived three-way check disagrees.
            (bundle_dir / "TEST_RESULTS.md").write_text("mutated after archiving\n")
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "plan", metadata.work_item_id,
            )
            self.assertEqual(result["status"], "withdrawn")
            self.assertFalse(bundle_dir.exists())
            self.assertTrue(Path(result["quarantine"]).is_dir())

    def test_finalize_bundle_generation_flat_layout_reports_mismatch_without_withdrawing(self):
        """No `work_item_id` (the flat compatibility layout) has no
        `REJECTED`-marker counterpart -- a mismatch is reported exactly
        as before this checkpoint, never withdrawn."""
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(repo, metadata)
            (bundle_dir / "TEST_RESULTS.md").write_text("mutated after archiving\n")
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "plan", None,
            )
            self.assertEqual(result["status"], "mismatch")
            self.assertTrue(bundle_dir.exists())

    def test_finalize_bundle_generation_withdraws_on_stale_plan_document(self):
        """The plan-stage byte-identity assertion (part 3) catches a bug
        the bundle_id three-way check alone cannot: `PLAN.md`/its `files/`
        copy corrupted *before* `MANIFEST.md` is written and the archive
        is built, so bundle_id stays internally self-consistent (manifest/
        on-disk/archived all reflect the same already-corrupted tree)
        while `review_content_id` -- computed from the pin, never from
        `PLAN.md` -- remains correct."""
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            self._seed_workflow_state(repo, metadata)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(
                repo, metadata, corrupt_derivation=True,
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "plan", metadata.work_item_id,
            )
            self.assertEqual(result["status"], "withdrawn")
            self.assertIn("not byte-identical", result["message"])

    def test_finalize_bundle_generation_withdraws_on_empty_test_results(self):
        """Item 272: the historical bug this checkpoint fixes --
        `prepare-ai-review.sh`'s own "create if missing" stub leaves
        `TEST_RESULTS.md` empty, and nothing previously caught that
        before the bundle was archived and offered for review."""
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            self._seed_workflow_state(repo, metadata)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(
                repo, metadata, test_results_text="",
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "plan", metadata.work_item_id,
            )
            self.assertEqual(result["status"], "withdrawn")
            self.assertIn("item 272", result["message"])
            self.assertFalse(bundle_dir.exists())

    def test_finalize_bundle_generation_withdraws_on_carried_forward_test_results(self):
        """A `TEST_RESULTS.md` left over from a previous round -- here,
        naming a stale plan revision -- is withdrawn rather than
        published as though it were this round's own evidence."""
        with self._seeded_repo() as repo:
            metadata = _metadata_for(repo)
            self._seed_workflow_state(repo, metadata)
            bundle_dir, archive_path = self._build_valid_pinned_bundle(
                repo, metadata,
                test_results_text=f"stage: plan (revision {metadata.plan_revision - 1})\nhead: {'a' * 40}\n",
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "plan", metadata.work_item_id,
            )
            self.assertEqual(result["status"], "withdrawn")
            self.assertIn("authoritative plan document currently declares", result["message"])

    def _build_valid_implementation_bundle(self, repo, work_item_id, implementation_summary_text):
        """A minimal, internally self-consistent implementation-stage
        bundle: `MANIFEST.md` alone satisfies `compute_bundle_id`'s own
        `REQUIRED_BUNDLE_FILES` (`{"MANIFEST.md"}`), so this deliberately
        skips the full `write_manifest_with_verified_identifiers_
        implementation_stage` machinery (which additionally demands a
        matching `REVIEW_REQUEST.md`/real protected paths) -- irrelevant
        to `finalize_bundle_generation`'s own checks, which read only
        `bundle_id` from `MANIFEST.md`. Returns `(bundle_dir, archive_path)`."""
        bundle_dir = repo.root / ".ai-review" / work_item_id / "current"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "IMPLEMENTATION_SUMMARY.md").write_text(implementation_summary_text)
        placeholder = b"# Bundle Manifest\n\nstage: implementation\n"
        bundle_id, _entries = wf.compute_bundle_id(bundle_dir, manifest_content_override=placeholder)
        (bundle_dir / "MANIFEST.md").write_text(
            f"# Bundle Manifest\n\nstage: implementation\nbundle_id: {bundle_id}\n"
        )
        root_dir = bundle_dir.parent
        archive_path = root_dir / "review-bundle.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(bundle_dir, arcname="current")
        return bundle_dir, archive_path

    def test_finalize_bundle_generation_withdraws_on_implementation_revision_mismatch(self):
        """`OPUS-R133-003`: the implementation/post-fix branch of
        `assert_stage_completeness` had no live caller anywhere in the
        generation path before this -- a bundle whose author-written
        `IMPLEMENTATION_SUMMARY.md` declared a stale `implementation_
        revision` (here, carried forward from a previous round) published
        anyway. This is that branch's first live caller."""
        work_item_id = "workflow-v2-1-core"
        with ScratchRepo() as repo:
            (repo.root / "docs" / "ai-workflow").mkdir(parents=True, exist_ok=True)
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(json.dumps({
                "schema_version": 1,
                "active_work_item_id": work_item_id,
                "work_items": {
                    work_item_id: {
                        "work_item_id": work_item_id,
                        "work_item_type": "process",
                        "implementation_revision": 14,
                    },
                },
            }))
            bundle_dir, archive_path = self._build_valid_implementation_bundle(
                repo, work_item_id,
                "implementation_revision: 13\n\nstale, carried forward from a previous round\n",
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "implementation", work_item_id,
            )
            self.assertEqual(result["status"], "withdrawn")
            self.assertIn("implementation_revision", result["message"])
            self.assertFalse(bundle_dir.exists())

    def test_finalize_bundle_generation_ok_when_implementation_revision_matches(self):
        work_item_id = "workflow-v2-1-core"
        with ScratchRepo() as repo:
            (repo.root / "docs" / "ai-workflow").mkdir(parents=True, exist_ok=True)
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(json.dumps({
                "schema_version": 1,
                "active_work_item_id": work_item_id,
                "work_items": {
                    work_item_id: {
                        "work_item_id": work_item_id,
                        "work_item_type": "process",
                        "implementation_revision": 14,
                    },
                },
            }))
            bundle_dir, archive_path = self._build_valid_implementation_bundle(
                repo, work_item_id,
                "implementation_revision: 14\n\ncurrent round\n",
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "implementation", work_item_id,
            )
            self.assertEqual(result["status"], "ok")
            self.assertTrue(bundle_dir.exists())

    def _build_valid_implementation_bundle_flat_layout(self, repo, implementation_summary_text):
        """`OPUS-R136-M04` regressions: the same fixture as
        `_build_valid_implementation_bundle`, but written under the flat
        compatibility path (`.ai-review/current/`) `prepare-ai-review.sh`
        uses whenever `work-item-id` is omitted, per
        `docs/ai-workflow/REVIEW_PROTOCOL.md`'s documented optionality for
        the `implementation`/`post-fix` stages."""
        bundle_dir = repo.root / ".ai-review" / "current"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "IMPLEMENTATION_SUMMARY.md").write_text(implementation_summary_text)
        placeholder = b"# Bundle Manifest\n\nstage: implementation\n"
        bundle_id, _entries = wf.compute_bundle_id(bundle_dir, manifest_content_override=placeholder)
        (bundle_dir / "MANIFEST.md").write_text(
            f"# Bundle Manifest\n\nstage: implementation\nbundle_id: {bundle_id}\n"
        )
        root_dir = bundle_dir.parent
        archive_path = root_dir / "review-bundle.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(bundle_dir, arcname="current")
        return bundle_dir, archive_path

    def test_finalize_bundle_generation_omitted_work_item_id_reports_mismatch_on_stale_revision(self):
        """`OPUS-R136-M04`: before this fix, `finalize_bundle_generation`
        only ran the implementation/post-fix `assert_stage_completeness`
        check `and work_item_id is not None` -- the documented omitted-id
        invocation (`REVIEW_PROTOCOL.md`: work-item-id "remains optional
        for every other stage") skipped the check entirely rather than
        resolving a target for it, so a stale `IMPLEMENTATION_SUMMARY.md`
        published as `status: ok` through this exact call shape even
        though `WORKFLOW_STATE.json` had a resolvable
        `active_work_item_id`. The fix must resolve that fallback and
        still fail closed -- proven here by a stale revision (13 on disk,
        14 authoritative) reaching this call with `work_item_id=None`."""
        work_item_id = "workflow-v2-1-core"
        with ScratchRepo() as repo:
            (repo.root / "docs" / "ai-workflow").mkdir(parents=True, exist_ok=True)
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(json.dumps({
                "schema_version": 1,
                "active_work_item_id": work_item_id,
                "work_items": {
                    work_item_id: {
                        "work_item_id": work_item_id,
                        "work_item_type": "process",
                        "implementation_revision": 14,
                    },
                },
            }))
            bundle_dir, archive_path = self._build_valid_implementation_bundle_flat_layout(
                repo, "implementation_revision: 13\n\nstale, carried forward from a previous round\n",
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "implementation", None,
            )
            self.assertEqual(result["status"], "mismatch")
            self.assertIn("implementation_revision", result["message"])
            # No `work_item_id` was ever named by this call, so nothing is
            # quarantined under an inferred one -- the bundle is left in
            # place, just reported as not ok (mirrors the flat-layout
            # bundle_id-mismatch behavior already covered by
            # `test_finalize_bundle_generation_flat_layout_reports_mismatch_without_withdrawing`).
            self.assertTrue(bundle_dir.exists())

    def test_finalize_bundle_generation_omitted_work_item_id_ok_when_revision_matches(self):
        """Companion to the stale-revision regression above: the same
        omitted-id flat-layout call shape must still publish `status: ok`
        when the resolved `active_work_item_id`'s counter genuinely
        matches, so the fix does not just fail the omitted-id path
        unconditionally."""
        work_item_id = "workflow-v2-1-core"
        with ScratchRepo() as repo:
            (repo.root / "docs" / "ai-workflow").mkdir(parents=True, exist_ok=True)
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(json.dumps({
                "schema_version": 1,
                "active_work_item_id": work_item_id,
                "work_items": {
                    work_item_id: {
                        "work_item_id": work_item_id,
                        "work_item_type": "process",
                        "implementation_revision": 14,
                    },
                },
            }))
            bundle_dir, archive_path = self._build_valid_implementation_bundle_flat_layout(
                repo, "implementation_revision: 14\n\ncurrent round\n",
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "implementation", None,
            )
            self.assertEqual(result["status"], "ok")
            self.assertTrue(bundle_dir.exists())

    def test_finalize_bundle_generation_omitted_work_item_id_no_active_item_reports_mismatch(self):
        """When `work_item_id` is omitted and `WORKFLOW_STATE.json` has no
        `active_work_item_id` to fall back to either, the fix must fail
        closed (report `mismatch`) rather than silently skip the check --
        the same "cannot verify, so don't publish unchecked" discipline as
        the resolvable case above, just with nothing to resolve."""
        with ScratchRepo() as repo:
            (repo.root / "docs" / "ai-workflow").mkdir(parents=True, exist_ok=True)
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").write_text(json.dumps({
                "schema_version": 1,
                "active_work_item_id": None,
                "work_items": {},
            }))
            bundle_dir, archive_path = self._build_valid_implementation_bundle_flat_layout(
                repo, "implementation_revision: 1\n\nno tracked work item\n",
            )
            result = wf.finalize_bundle_generation(
                repo.root, bundle_dir, archive_path, "implementation", None,
            )
            self.assertEqual(result["status"], "mismatch")
            self.assertIn("active_work_item_id", result["message"])
            self.assertTrue(bundle_dir.exists())



class TestFeedbackLayout(unittest.TestCase):
    """`D-Feedback-Layout` (workflow-2.6.0, CP3; REQ-1/REQ-2): one
    authoritative resolver keyed on the durable `feedback_layout` stamp,
    scoped by construction for new items, the unchanged legacy rule for
    everything else, fail-closed on an undecidable state file (INV-3), the
    bounded terminal-owner relaxation, the per-item consumed marker, the
    `--resolve-feedback-path` CLI contract and the manual-record
    `Work item:` binding. `workflow_state_test.TestFeedbackLayoutStamp`
    pins the stamp's writers."""

    SCOPED = "scoped-item"
    OTHER_SCOPED = "other-scoped-item"
    LEGACY = "legacy-item"
    COMPLETED = "completed-legacy-item"

    def _state(self, repo, work_items):
        path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        path.write_text(json.dumps({"schema_version": 1, "work_items": work_items}, indent=2) + "\n")
        return json.loads(path.read_text())

    def _entry(self, work_item_id, *, phase="IMPLEMENTING", **extra):
        return {"work_item_id": work_item_id, "phase": phase, **extra}

    def _standard_state(self, repo):
        return self._state(repo, {
            self.SCOPED: self._entry(self.SCOPED, feedback_layout="scoped"),
            self.OTHER_SCOPED: self._entry(self.OTHER_SCOPED, feedback_layout="scoped"),
            self.LEGACY: self._entry(self.LEGACY),
            self.COMPLETED: self._entry(self.COMPLETED, phase="MILESTONE_COMPLETE"),
        })

    def _feedback(self, work_item):
        return (
            f"# Review Decision\n\nStatus: APPROVE\n\n"
            f"Reviewed bundle ID: {FAKE_ID_A}\n"
            f"Reviewed base commit: {'a' * 40}\n"
            f"Work item: {work_item}\n"
        )

    def _write_flat_feedback(self, repo, owner):
        flat = repo.root / ".ai-review" / "feedback"
        flat.mkdir(parents=True, exist_ok=True)
        (flat / "REVIEW_FEEDBACK.md").write_text(self._feedback(owner))
        return flat / "REVIEW_FEEDBACK.md"

    # -- resolution -------------------------------------------------------

    def test_scoped_item_resolves_scoped_with_no_directory_present(self):
        with ScratchRepo() as repo:
            self._standard_state(repo)
            scoped = Path(".ai-review") / self.SCOPED / "feedback"
            self.assertFalse((repo.root / scoped).exists())
            self.assertEqual(wf.resolve_feedback_layout(repo.root, self.SCOPED), "scoped")
            self.assertEqual(wf.resolve_feedback_dir(repo.root, self.SCOPED), scoped)
            self.assertFalse((repo.root / scoped).exists(), "resolution alone creates nothing")

    def test_ensure_feedback_dir_creates_the_scoped_directory(self):
        with ScratchRepo() as repo:
            self._standard_state(repo)
            created = wf.ensure_feedback_dir(repo.root, self.SCOPED)
            self.assertEqual(created, Path(".ai-review") / self.SCOPED / "feedback")
            self.assertTrue((repo.root / created).is_dir())
            self.assertEqual(wf.ensure_feedback_dir(repo.root, self.SCOPED), created)  # idempotent

    def test_legacy_entry_no_entry_and_no_state_file_keep_the_unchanged_rule(self):
        cases = {
            "legacy entry": lambda repo: self._standard_state(repo) and self.LEGACY,
            "no entry": lambda repo: self._standard_state(repo) and "never-routed-item",
            "no state file": lambda repo: "never-routed-item",
        }
        for name, arrange in cases.items():
            with self.subTest(case=name), ScratchRepo() as repo:
                work_item_id = arrange(repo)
                self.assertEqual(wf.resolve_feedback_layout(repo.root, work_item_id), "legacy-flat")
                self.assertEqual(wf.resolve_feedback_dir(repo.root, work_item_id), Path(".ai-review/feedback"))
                scoped = Path(".ai-review") / work_item_id / "feedback"
                (repo.root / scoped).mkdir(parents=True)
                self.assertEqual(wf.resolve_feedback_layout(repo.root, work_item_id), "legacy-scoped")
                self.assertEqual(wf.resolve_feedback_dir(repo.root, work_item_id), scoped)

    def test_ensure_feedback_dir_never_flips_a_legacy_flat_item_to_scoped(self):
        with ScratchRepo() as repo:
            self._standard_state(repo)
            self.assertEqual(wf.ensure_feedback_dir(repo.root, self.LEGACY), Path(".ai-review/feedback"))
            self.assertFalse((repo.root / ".ai-review" / self.LEGACY).exists())
            self.assertEqual(wf.resolve_feedback_layout(repo.root, self.LEGACY), "legacy-flat")

    def test_undecidable_state_file_refuses_rather_than_falling_back(self):
        state_rel = Path("docs/ai-workflow/WORKFLOW_STATE.json")
        cases = {
            "not json": b"{not json",
            "not utf-8": b"\xff\xfe\x00",
            "top level not an object": b"[]",
            "work_items not an object": b'{"work_items": []}',
            "entry not an object": b'{"work_items": {"scoped-item": "scoped"}}',
        }
        for name, content in cases.items():
            with self.subTest(case=name), ScratchRepo() as repo:
                (repo.root / state_rel).write_bytes(content)
                with self.assertRaises(wf.FeedbackLayoutUndecidableError):
                    wf.resolve_feedback_dir(repo.root, self.SCOPED)
        with ScratchRepo() as repo:
            real = repo.root / "elsewhere.json"
            real.write_text(json.dumps({"work_items": {}}))
            (repo.root / state_rel).symlink_to(real)
            with self.assertRaises(wf.FeedbackLayoutUndecidableError):
                wf.resolve_feedback_dir(repo.root, self.SCOPED)

    def test_unknown_layout_value_refuses(self):
        for value in ("flat", "Scoped", "", None, 1, ["scoped"]):
            with self.subTest(value=value), ScratchRepo() as repo:
                self._state(repo, {self.SCOPED: self._entry(self.SCOPED, feedback_layout=value)})
                with self.assertRaises(wf.UnknownFeedbackLayoutError):
                    wf.resolve_feedback_dir(repo.root, self.SCOPED)
                with self.assertRaises(wf.UnknownFeedbackLayoutError):
                    wf.ensure_feedback_dir(repo.root, self.SCOPED)

    def test_two_scoped_items_never_collide(self):
        with ScratchRepo() as repo:
            self._standard_state(repo)
            a = wf.ensure_feedback_dir(repo.root, self.SCOPED)
            b = wf.ensure_feedback_dir(repo.root, self.OTHER_SCOPED)
            self.assertNotEqual(a, b)
            (repo.root / a / "REVIEW_FEEDBACK.md").write_text(self._feedback(self.SCOPED))
            self.assertFalse((repo.root / b / "REVIEW_FEEDBACK.md").exists())

    def test_completed_legacy_flat_file_never_affects_a_scoped_item(self):
        with ScratchRepo() as repo:
            self._standard_state(repo)
            flat_file = self._write_flat_feedback(repo, self.COMPLETED)
            flat_bytes = flat_file.read_bytes()
            feedback_dir = wf.ensure_feedback_dir(repo.root, self.SCOPED)
            self.assertNotEqual(feedback_dir, Path(".ai-review/feedback"))
            target = repo.root / feedback_dir / "REVIEW_FEEDBACK.md"
            self.assertFalse(target.exists(), "the scoped item never sees the flat file")
            wf.assert_feedback_not_owned_by_other_work_item(None, work_item_id=self.SCOPED)
            target.write_text(self._feedback(self.SCOPED))
            self.assertEqual(flat_file.read_bytes(), flat_bytes, "nor is the flat file touched")

    # -- ownership guard ---------------------------------------------------

    def test_legacy_writer_may_replace_a_terminal_owners_flat_file(self):
        with ScratchRepo() as repo:
            state = self._standard_state(repo)
            existing = self._write_flat_feedback(repo, self.COMPLETED).read_text()
            wf.assert_feedback_not_owned_by_other_work_item(
                existing, work_item_id=self.LEGACY, state=state,
            )
            # the unrelaxed call (no state) is 2.5.1's behavior, unchanged
            with self.assertRaises(wf.FeedbackOwnedByOtherWorkItemError):
                wf.assert_feedback_not_owned_by_other_work_item(existing, work_item_id=self.LEGACY)

    def test_non_terminal_or_unknown_owner_still_refuses(self):
        with ScratchRepo() as repo:
            state = self._standard_state(repo)
            for owner in (self.SCOPED, "owner-absent-from-state"):
                with self.subTest(owner=owner):
                    with self.assertRaises(wf.FeedbackOwnedByOtherWorkItemError):
                        wf.assert_feedback_not_owned_by_other_work_item(
                            self._feedback(owner), work_item_id=self.LEGACY, state=state,
                        )

    def test_scoped_writer_is_never_relaxed(self):
        """A scoped directory is private by construction: a foreign file
        inside it is an anomaly, refused even when its owner is terminal."""
        with ScratchRepo() as repo:
            state = self._standard_state(repo)
            with self.assertRaises(wf.FeedbackOwnedByOtherWorkItemError):
                wf.assert_feedback_not_owned_by_other_work_item(
                    self._feedback(self.COMPLETED), work_item_id=self.SCOPED, state=state,
                )

    def test_terminal_phase_set_matches_workflow_state(self):
        self.assertEqual(wf.FEEDBACK_OWNER_TERMINAL_PHASES, ws.TERMINAL_PHASES)

    # -- consumed marker ---------------------------------------------------

    def test_consumed_marker_is_per_item_for_scoped_items(self):
        with ScratchRepo() as repo:
            self._standard_state(repo)
            for work_item_id in (self.SCOPED, self.OTHER_SCOPED):
                self.assertEqual(
                    wf.resolve_functional_review_consumed_marker_path(repo.root, work_item_id),
                    Path(".ai-review") / work_item_id / "feedback" / "FUNCTIONAL_REVIEW.consumed",
                )
            feedback_dir = wf.ensure_feedback_dir(repo.root, self.SCOPED)
            (repo.root / feedback_dir / "FUNCTIONAL_REVIEW.md").write_text("findings\n")
            wf.mark_functional_review_consumed(repo.root, self.SCOPED)
            with self.assertRaises(wf.FunctionalReviewAlreadyAppliedError):
                wf.assert_functional_review_not_already_consumed(repo.root, self.SCOPED)
            other_dir = wf.ensure_feedback_dir(repo.root, self.OTHER_SCOPED)
            (repo.root / other_dir / "FUNCTIONAL_REVIEW.md").write_text("findings\n")
            wf.assert_functional_review_not_already_consumed(repo.root, self.OTHER_SCOPED)  # must not raise

    # -- CLI contract ------------------------------------------------------

    def test_cli_json_matches_the_function_for_all_three_layouts(self):
        script = Path(__file__).resolve().parent / "workflow_fingerprint.py"
        with ScratchRepo() as repo:
            self._standard_state(repo)
            (repo.root / ".ai-review" / "legacy-scoped-item" / "feedback").mkdir(parents=True)
            expected_layouts = {
                self.SCOPED: "scoped", "legacy-scoped-item": "legacy-scoped", self.LEGACY: "legacy-flat",
            }
            for work_item_id, layout in expected_layouts.items():
                with self.subTest(work_item_id=work_item_id):
                    result = subprocess.run(
                        [sys.executable, str(script), "--resolve-feedback-path", work_item_id],
                        cwd=repo.root, check=True, capture_output=True, text=True,
                    )
                    lines = result.stdout.strip().splitlines()
                    self.assertEqual(len(lines), 1, "exactly one JSON object")
                    printed = json.loads(lines[0])
                    self.assertEqual(printed, wf.resolve_feedback_path_contract(repo.root, work_item_id))
                    feedback_dir = wf.resolve_feedback_dir(repo.root, work_item_id).as_posix()
                    self.assertEqual(printed, {
                        "work_item_id": work_item_id,
                        "layout": layout,
                        "feedback_dir": feedback_dir,
                        "review_feedback_path": f"{feedback_dir}/REVIEW_FEEDBACK.md",
                        "functional_review_path": f"{feedback_dir}/FUNCTIONAL_REVIEW.md",
                    })
            self.assertFalse((repo.root / ".ai-review" / self.SCOPED).exists(), "the CLI creates nothing")

    # -- manual-record binding ---------------------------------------------

    def test_manual_record_refuses_a_foreign_work_item_field(self):
        with self.assertRaises(wf.ManualFeedbackForeignWorkItemError) as ctx:
            wf.assert_manual_feedback_names_work_item(self._feedback(self.COMPLETED), work_item_id=self.SCOPED)
        self.assertIn(self.COMPLETED, str(ctx.exception))
        self.assertIn(self.SCOPED, str(ctx.exception))

    def test_manual_record_accepts_its_own_or_an_absent_work_item_field(self):
        wf.assert_manual_feedback_names_work_item(self._feedback(self.SCOPED), work_item_id=self.SCOPED)
        wf.assert_manual_feedback_names_work_item(
            "# Review Decision\n\nStatus: APPROVE\n\nreview_content_id: " + FAKE_ID_B + "\n",
            work_item_id=self.SCOPED,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
