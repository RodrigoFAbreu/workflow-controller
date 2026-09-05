#!/usr/bin/env python3
"""Explicit integration/demonstration test against this repository's real
state — split out from the hermetic unit suite per `PROTO-R7-009`, which
found the previous single-file suite's own module docstring claiming the
suite "never" runs against this repository's own working tree, while one
of its test classes did exactly that.

This file intentionally depends on the real repository and a specific
historical base commit (`162154d`, this milestone's actual base). It is
not part of the hermetic unit suite (`workflow_fingerprint_test.py`) and
should be run separately / treated as an opt-in integration check, not
wired into a CI job that expects to run against arbitrary checkouts.

Satisfies round-6's required acceptance criterion 4: a demonstration run
against this repository's actual state showing a non-empty plan-stage
manifest containing the real blob SHAs of `WORKFLOW_V2_PLAN.md`,
`WORKFLOW_V2_AUDIT.md`, `docs/TECHNICAL_DECISIONS.md`, and (since
`GPT-R9-003`) the registry and requirements-mapping JSON files.

Also satisfies OPUS-R8-001's core acceptance criterion as an integration
assertion (missing-test item 24): the real submitted bundle's own reported
`bundle_id` (written into `MANIFEST.md`) recomputes, unchanged, from the
bundle directory as actually submitted.

`WF4a-i`: `TestImplementationStageAgainstRealRepository` is the real
implementation-stage fixture this milestone previously had no diff to
validate against -- exercised against this milestone's own real
WF0/WF1a/WF1b commits, now that they exist.

Run: python3 scripts/workflow_fingerprint_demo_test.py
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import workflow_fingerprint as wf

BASE_COMMIT = "162154d3e5e10eb65e109833acae4b4fb01fc5d6"

# `workflow-v2-1-core`'s own `/accept-milestone` commit -- its `base_commit`
# reached `MILESTONE_COMPLETE`. Every real-repository test in this file that
# exists to validate a fact about `workflow-v2-1-core`'s own now-closed
# history is anchored here, never at live `"HEAD"`/the working tree: that
# history stopped moving the instant the item completed, so a fixed anchor
# stays green permanently regardless of what a later, concurrent work item
# (e.g. `workflow-v2-3`) commits on top of it (revision 4/5/6, round 3/4/5
# `local_model_plan_review` B1).
WORKFLOW_V2_1_CORE_COMPLETION_COMMIT = "27f051eba897d77c742ead8b160ed519c0671ee4"


def _repo_root() -> Path:
    return Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    )


def _blob_at_commit(repo_root: Path, commit: str, rel_path: str) -> str:
    """The blob SHA `rel_path` had at `commit`, never the live worktree --
    so a manifest fixed at a commit and its own verification loop are
    always compared from the same source (revision 5, round 4 B2/I1)."""
    return subprocess.run(
        ["git", "rev-parse", f"{commit}:{rel_path}"], cwd=repo_root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


class TestAgainstRealRepository(unittest.TestCase):
    def test_demonstration_against_real_repo(self):
        repo_root = _repo_root()
        # plan_revision is sourced from the registry JSON, not a literal
        # constant this file maintains by hand (OPUS-R18-003) -- the same
        # drift `test_plan_title_revision_matches_declared_plan_revision`
        # guards against is now structurally impossible here: there is no
        # second copy of the number left to drift.
        plan_revision = wf.load_plan_revision(
            repo_root, wf.DEFAULT_REGISTRY_PATH, wf.DEFAULT_PLAN_PATH,
            at_commit=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
        )
        digest, projection = wf.compute_review_content_id_plan_stage_at_commit(
            repo_root, BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
            work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=plan_revision,
            protected=wf.PLAN_STAGE_PROTECTED, excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
            excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
        )
        manifest = projection["review_content_manifest"]
        # 5: the concise two-stage plan-review guide's path
        # (docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md) moved from
        # PLAN_STAGE_PROTECTED to PLAN_STAGE_EXCLUDED_PATHS this round
        # (OPUS-R14-001/-009) -- a plan-stage manifest no longer has room
        # for a protected-but-unwritten tombstone entry at all; this call
        # would now raise AbsentProtectedPathError instead of returning one
        # (missing-test item 100, real-repo half: every entry below is a
        # real, present file this checkout's own manifest generation would
        # otherwise fail closed on).
        self.assertEqual(len(manifest), 5)
        paths = {e["path"] for e in manifest}
        self.assertEqual(
            paths,
            {
                "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
                "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                "docs/TECHNICAL_DECISIONS.md",
                "docs/ai-workflow/registry/workflow-v2-1-core-registry.json",
                "docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json",
            },
        )
        for entry in manifest:
            self.assertTrue(entry["exists"])
            real_sha = _blob_at_commit(repo_root, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, entry["path"])
            self.assertEqual(entry["blob"], real_sha)
        print(f"\n[demonstration] base_commit (resolved): {projection['base_commit']}")
        print(f"[demonstration] review_content_id = {digest}")
        for entry in manifest:
            print(f"[demonstration] {entry['path']}: {entry['blob']}")

    def test_141_migrated_artifacts_plan_stage_equals_frozen_python_constants(self):
        """Missing-test item 141 (`OPUS-R25-001`/`-010`): the real migrated
        `workflow-v2-1-core-artifacts.json`'s `plan_stage` section, loaded
        via `load_plan_stage_classification`, equals `PLAN_STAGE_PROTECTED`/
        `PLAN_STAGE_EXCLUDED_PATHS`/`PLAN_STAGE_EXCLUDED_PREFIXES` exactly,
        item for item, compared directly rather than via a hardcoded
        digest literal -- required before either Python constant is
        retired as a live default."""
        repo_root = _repo_root()
        protected, excluded_paths, excluded_prefixes = wf.load_plan_stage_classification(
            repo_root, wf.artifacts_path_for_work_item("workflow-v2-1-core"),
        )
        self.assertEqual(protected, wf.PLAN_STAGE_PROTECTED)
        self.assertEqual(dict(excluded_paths), dict(wf.PLAN_STAGE_EXCLUDED_PATHS))
        self.assertEqual(dict(excluded_prefixes), dict(wf.PLAN_STAGE_EXCLUDED_PREFIXES))

    def test_142_generalized_resolver_reproduces_the_migrated_digest_not_a_hardcoded_literal(self):
        """Missing-test item 142 (`OPUS-R25-001`, corrected): the
        generalized, per-work-item resolver must reproduce exactly the
        same digest the frozen-default plan-stage function computes at
        the same base/content -- computed fresh from both paths here,
        never a hardcoded literal this revision's own approval
        necessarily invalidates the moment it is recorded."""
        repo_root = _repo_root()
        digest_generalized, _ = wf.compute_review_content_id_plan_stage_at_commit_for_work_item(
            repo_root, "workflow-v2-1-core", WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, base=BASE_COMMIT,
        )
        plan_revision = wf.load_plan_revision(
            repo_root, wf.DEFAULT_REGISTRY_PATH, wf.DEFAULT_PLAN_PATH,
            at_commit=WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
        )
        digest_frozen_defaults, _ = wf.compute_review_content_id_plan_stage_at_commit(
            repo_root, BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
            work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=plan_revision,
            protected=wf.PLAN_STAGE_PROTECTED, excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
            excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
        )
        self.assertEqual(digest_generalized, digest_frozen_defaults)

    def test_demonstration_bundle_id_against_current_bundle(self):
        repo_root = _repo_root()
        bundle_dir = repo_root / ".ai-review" / "workflow-v2-1-core" / "current"
        if not bundle_dir.is_dir():
            self.skipTest("no .ai-review/current bundle present in this checkout")
        bid, entries = wf.compute_bundle_id(bundle_dir)
        recomputed_bid, _ = wf.compute_bundle_id(bundle_dir)
        self.assertEqual(bid, recomputed_bid, "bundle_id must be idempotent on the real bundle too")
        actual_file_count = sum(1 for p in bundle_dir.rglob("*") if p.is_file())
        self.assertEqual(
            len(entries), actual_file_count,
            "the reported file count must equal the bundle's actual file count "
            "(PROTO-R7-004: no computing against a stale snapshot)",
        )
        print(f"\n[demonstration] bundle_dir: {bundle_dir}")
        print(f"[demonstration] bundle_id = {bid}")
        print(f"[demonstration] file count = {len(entries)}")

    def test_024_real_bundle_recomputes_to_its_own_reported_bundle_id(self):
        """OPUS-R8-001's core acceptance criterion, as an integration
        assertion: the `bundle_id` reported inside the real submitted
        bundle's own `MANIFEST.md` must recompute unchanged from that same
        bundle directory."""
        repo_root = _repo_root()
        bundle_dir = repo_root / ".ai-review" / "workflow-v2-1-core" / "current"
        manifest_path = bundle_dir / "MANIFEST.md"
        if not manifest_path.is_file():
            self.skipTest("no MANIFEST.md present in this checkout's bundle")
        reported = None
        for line in manifest_path.read_text().splitlines():
            m = re.match(r"^bundle_id: ([0-9a-f]{64})$", line)
            if m:
                reported = m.group(1)
                break
        self.assertIsNotNone(reported, "MANIFEST.md must report bundle_id in the contract spelling")
        recomputed, _ = wf.compute_bundle_id(bundle_dir)
        self.assertEqual(
            reported, recomputed,
            "the bundle_id reported inside the submitted bundle must recompute "
            "unchanged from that same bundle directory",
        )
        print(f"\n[demonstration] MANIFEST.md-reported bundle_id: {reported}")
        print(f"[demonstration] recomputed bundle_id:            {recomputed}")

    def test_125_real_bundle_review_content_id_recomputes_to_its_own_reported_value(self):
        """Missing-test item 125 (OPUS-R18-001), the review_content_id
        counterpart of test_024: the value the real submitted bundle's own
        MANIFEST.md reports must recompute unchanged from the current
        working tree and registry-declared plan_revision. This is exactly
        the acceptance criterion round 18 found violated -- the round-17
        bundle's manifest stated a review_content_id that no revision
        number reproduced.

        Stage-aware (`OPUS-R130-M01`): this test previously always
        recomputed via the *plan*-stage projection, making it permanently
        red and detective-inert from the first implementation-stage bundle
        onward (`current/`'s own MANIFEST.md has stated `stage:
        implementation` since round 18's own successor rounds) -- the red
        result was stale-test logic, never evidence the bundle's own
        identity was wrong. `MANIFEST.md`'s own `stage:`/`base_commit:`
        fields (which it already states unconditionally) now select which
        of the two projections this test recomputes against, so it stays
        a live detective regression for whichever stage `current/` is
        actually holding."""
        repo_root = _repo_root()
        bundle_dir = repo_root / ".ai-review" / "workflow-v2-1-core" / "current"
        manifest_path = bundle_dir / "MANIFEST.md"
        if not manifest_path.is_file():
            self.skipTest("no MANIFEST.md present in this checkout's bundle")
        manifest_text = manifest_path.read_text()
        existing = wf.read_manifest_identifiers(manifest_path)
        reported = existing.get("review_content_id")
        self.assertIsNotNone(reported, "MANIFEST.md must report review_content_id in the contract spelling")

        stage_match = re.search(r"^stage: (\w+)$", manifest_text, re.MULTILINE)
        self.assertIsNotNone(stage_match, "MANIFEST.md must state its own stage")
        stage = stage_match.group(1)
        base_commit_match = re.search(r"^base_commit: ([0-9a-f]{40})$", manifest_text, re.MULTILINE)
        self.assertIsNotNone(base_commit_match, "MANIFEST.md must state its own base_commit")
        base_commit = base_commit_match.group(1)

        if stage == "plan":
            plan_revision = wf.load_plan_revision(repo_root, wf.DEFAULT_REGISTRY_PATH, wf.DEFAULT_PLAN_PATH)
            recomputed, _ = wf.compute_review_content_id_plan_stage(
                repo_root, base_commit,
                work_item_type="process", work_item_id="workflow-v2-1-core", plan_revision=plan_revision,
                protected=wf.PLAN_STAGE_PROTECTED, excluded_paths=wf.PLAN_STAGE_EXCLUDED_PATHS,
                excluded_prefixes=wf.PLAN_STAGE_EXCLUDED_PREFIXES,
            )
        elif stage == "implementation":
            protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
                wf.load_implementation_stage_classification(
                repo_root, wf.artifacts_path_for_work_item("workflow-v2-1-core"),
            )
            )
            recomputed, _ = wf.compute_review_content_id_implementation_stage(
                repo_root, base_commit,
                work_item_type="process", work_item_id="workflow-v2-1-core",
                protected_paths=protected_paths, protected_prefixes=protected_prefixes,
                excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
            )
        else:
            self.fail(f"MANIFEST.md states an unrecognized stage: {stage!r}")

        self.assertEqual(
            reported, recomputed,
            f"the review_content_id reported inside the submitted {stage}-stage bundle must "
            f"recompute unchanged from the current working tree",
        )
        print(f"\n[demonstration] MANIFEST.md-reported review_content_id ({stage}): {reported}")
        print(f"[demonstration] recomputed review_content_id:                    {recomputed}")

    def test_plan_title_revision_matches_declared_plan_revision(self):
        """Missing-test item 109 (OPUS-R14-004): a concrete regression
        guard against exactly this round's own defect, where the title
        line said 'Revision 10' while every other section (and the
        identity computation itself, via `plan_revision`) had already
        moved on to 11. `load_plan_revision` (OPUS-R18-003) now performs
        this exact check as part of normal generation; this test is kept
        as an explicit, independent regression guard against the same
        historical defect class, going straight to the title line rather
        than through the function under test."""
        repo_root = _repo_root()
        plan_path = repo_root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
        title_line = plan_path.read_text().splitlines()[0]
        m = re.search(r"\(Revision (\d+)\)", title_line)
        self.assertIsNotNone(m, f"title line has no '(Revision N)' marker: {title_line!r}")
        declared = int(m.group(1))
        registry = wf.load_plan_revision(repo_root, wf.DEFAULT_REGISTRY_PATH, wf.DEFAULT_PLAN_PATH)
        self.assertEqual(
            declared, registry,
            f"title declares Revision {declared}, but the registry JSON's "
            f"plan_revision (the value used to reproduce review_content_id) "
            f"is {registry}",
        )

    def test_every_protected_path_has_a_files_copy_in_the_bundle(self):
        """Missing-test item 115 (OPUS-R14-009): a protected path approved
        sight-unseen is exactly the defect this checks against -- every
        entry in PLAN_STAGE_PROTECTED must have a real copy under the
        generated bundle's files/ directory, not just a manifest entry."""
        repo_root = _repo_root()
        files_dir = repo_root / ".ai-review" / "workflow-v2-1-core" / "current" / "files"
        if not files_dir.is_dir():
            self.skipTest("no .ai-review/current/files present in this checkout")
        for path in sorted(wf.PLAN_STAGE_PROTECTED):
            with self.subTest(path=path):
                self.assertTrue(
                    (files_dir / path).is_file(),
                    f"protected path has no files/ copy in the generated bundle: {path}",
                )

    def test_archive_prefix_exclusion_is_timed_at_or_after_milestone_complete(self):
        """Missing-test item 123 (OPUS-R16-002): a conformance check against
        the exact defect this round retracted -- scheduling the
        disposition-table archival (a write under docs/ai-workflow/archive/)
        for "immediately after approval" would edit WORKFLOW_V2_PLAN.md, a
        protected path, between an approval and the next commit, staling
        that same approval with no "approved with corrections" transition
        to survive it (the OPUS-R14-003 class). Rather than grep the plan's
        own prose (which legitimately quotes the old, now-retracted timing
        in its historical disposition tables and self-review notes, and
        would false-positive on those accurate citations), this checks the
        one place the *current, governing* timing actually lives: the
        exclusion justification string itself."""
        justification = wf.PLAN_STAGE_EXCLUDED_PREFIXES["docs/ai-workflow/archive/"]
        self.assertIn(
            "MILESTONE_COMPLETE", justification,
            "the docs/ai-workflow/archive/ exclusion justification no longer "
            "states the at-or-after-MILESTONE_COMPLETE timing",
        )
        self.assertNotIn(
            "immediately after", justification,
            "the docs/ai-workflow/archive/ exclusion justification has "
            "regressed to the retracted immediately-after-approval timing "
            "(OPUS-R16-002)",
        )

    def test_136_every_path_named_in_a_command_doc_classifies(self):
        """Missing-test item 136 (OPUS-R18-004): an exhaustiveness scan
        over every path any `.claude/commands/*.md` file names, checked
        against classify_path. This is a best-effort superset scan, stated
        explicitly rather than silently assumed exhaustive: it extracts
        every backtick-quoted, repo-relative-looking path mentioned in a
        command doc and that actually exists in this checkout, whether the
        command reads or writes it -- over-inclusive of true write targets,
        which is the safe direction for a regression guard, not a formal
        per-command writes manifest. A path this scan finds and
        classify_path rejects is a real gap the same shape as
        OPUS-R18-004's own discovery process; a path outside this scan
        that a command actually writes is not caught here (see
        test_133/test_134/test_135 in the hermetic suite for the
        specific, already-known concurrent-write paths, and the
        `PRODUCT_SCOPE_JUSTIFICATION` closure that covers this scan's
        real findings today)."""
        repo_root = _repo_root()
        commands_dir = repo_root / ".claude" / "commands"
        if not commands_dir.is_dir():
            self.skipTest("no .claude/commands/ present in this checkout")
        path_re = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")
        mentioned: set[str] = set()
        for doc in sorted(commands_dir.glob("*.md")):
            mentioned.update(path_re.findall(doc.read_text()))

        def is_gitignored(rel_path: str) -> bool:
            return subprocess.run(
                ["git", "check-ignore", "-q", "--", rel_path], cwd=repo_root,
            ).returncode == 0

        # Only paths that exist, aren't themselves a `.claude/commands/*.md`
        # file (protected/excluded by the `.claude/commands/` prefix as a
        # directory, not as individual entries), and aren't gitignored --
        # a gitignored path (e.g. `.ai-review/current/...`) never actually
        # reaches classify_path in real usage, since both entry points
        # (`git diff --name-only` and `git ls-files --others
        # --exclude-standard`) are blind to it by construction.
        real_paths = {
            p for p in mentioned
            if (repo_root / p).is_file()
            and not p.startswith(".claude/commands/")
            and not is_gitignored(p)
        }
        self.assertTrue(real_paths, "expected at least one real repo-relative path mentioned in .claude/commands/*.md")
        for path in sorted(real_paths):
            with self.subTest(path=path):
                try:
                    wf.classify_path(
                        path, wf.PLAN_STAGE_PROTECTED, wf.PLAN_STAGE_EXCLUDED_PATHS,
                        wf.PLAN_STAGE_EXCLUDED_PREFIXES,
                    )
                except wf.UnclassifiedPathError:
                    self.fail(
                        f"{path} is mentioned in a .claude/commands/*.md file but "
                        f"classify_path does not recognize it as protected or excluded"
                    )


class TestImplementationStageAgainstRealRepository(unittest.TestCase):
    """WF4a-i's own real fixture: this milestone's actual WF0/WF1a/WF1b
    commits are, by now, a real changed-file diff to validate the
    implementation-stage classification/manifest algorithm against --
    exactly what this module's own docstring previously said did not
    exist yet."""

    def test_real_diff_since_base_commit_classifies_exhaustively(self):
        repo_root = _repo_root()
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            wf.load_implementation_stage_classification(
                repo_root, wf.artifacts_path_for_work_item("workflow-v2-1-core"),
            )
        )
        changed = sorted(
            wf._changed_tracked_paths_between(repo_root, BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT)
        )
        self.assertTrue(changed, "expected at least one changed path since this milestone's base commit")
        for path in changed:
            with self.subTest(path=path):
                try:
                    wf.classify_path_implementation_stage(
                        path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
                    )
                except wf.UnclassifiedPathError:
                    self.fail(
                        f"{path} changed since {BASE_COMMIT} but the implementation-stage "
                        f"classifier does not recognize it as protected or excluded"
                    )

    def test_real_diff_protects_this_milestones_own_tooling_and_commands(self):
        """The concrete, checkable claim: every `.claude/commands/` and
        `scripts/` path this milestone has actually changed since its base
        commit is protected at the implementation stage -- exactly the
        'workflow-command file' content D-Commit-Provenance names."""
        repo_root = _repo_root()
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            wf.load_implementation_stage_classification(
                repo_root, wf.artifacts_path_for_work_item("workflow-v2-1-core"),
            )
        )
        changed = wf._changed_tracked_paths(repo_root, BASE_COMMIT) | wf._untracked_paths(repo_root)
        tooling_paths = {
            p for p in changed if p.startswith(".claude/commands/") or p.startswith("scripts/")
        }
        self.assertTrue(tooling_paths, "expected at least one changed .claude/commands/ or scripts/ path")
        for path in sorted(tooling_paths):
            with self.subTest(path=path):
                self.assertEqual(
                    wf.classify_path_implementation_stage(
                        path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
                    ),
                    "protected",
                )

    def test_real_implementation_stage_manifest_is_nonempty_with_real_blob_shas(self):
        repo_root = _repo_root()
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            wf.load_implementation_stage_classification(
                repo_root, wf.artifacts_path_for_work_item("workflow-v2-1-core"),
            )
        )
        digest, projection = wf.compute_review_content_id_implementation_stage_at_commit(
            repo_root, BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
            work_item_type="process", work_item_id="workflow-v2-1-core",
            protected_paths=protected_paths, protected_prefixes=protected_prefixes,
            excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        )
        manifest = projection["review_content_manifest"]
        self.assertTrue(manifest, "expected a non-empty implementation-stage manifest against this milestone's own real diff")
        # OPUS-R129-004: kept current with implementation_stage.protected_paths'
        # own individually-carved-out exact entries (each with its own
        # recorded self-referential-carve-out rationale in the declaration
        # file itself) -- this assertion's job is catching an *unintended*
        # new category slipping into the manifest, not re-litigating each
        # already-declared carve-out's own justification.
        allowed_prefixes = (".claude/commands/", "scripts/")
        allowed_exact_paths = {
            ".github/workflows/ci.yml",
            "docs/ai-workflow/MILESTONE_WORKFLOW.md",
            "docs/ai-workflow/REVIEW_PROTOCOL.md",
            "docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json",
            "docs/ai-workflow/registry/workflow-v2-1-core-ledger-status.json",
            "docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json",
            "docs/ai-workflow/dry-run/verify_372h_lock_primitive_predicate.py",
            "docs/ai-workflow/dry-run/verify_372h_raw_edge_derivation.py",
        }
        for entry in manifest:
            with self.subTest(path=entry["path"]):
                self.assertTrue(
                    entry["path"].startswith(allowed_prefixes) or entry["path"] in allowed_exact_paths,
                    f"unexpected protected entry outside this milestone's own known tooling/declaration "
                    f"categories: {entry['path']}",
                )
            self.assertTrue(entry["exists"])
            real_sha = _blob_at_commit(repo_root, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT, entry["path"])
            self.assertEqual(entry["blob"], real_sha)
        # Recomputing twice must be idempotent, same discipline as the
        # plan-stage identity function.
        digest2, _ = wf.compute_review_content_id_implementation_stage_at_commit(
            repo_root, BASE_COMMIT, WORKFLOW_V2_1_CORE_COMPLETION_COMMIT,
            work_item_type="process", work_item_id="workflow-v2-1-core",
            protected_paths=protected_paths, protected_prefixes=protected_prefixes,
            excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        )
        self.assertEqual(digest, digest2)
        print(f"\n[demonstration] implementation-stage review_content_id = {digest}")
        for entry in manifest:
            print(f"[demonstration] {entry['path']}: {entry['blob']}")

    def test_review_protocol_md_is_a_real_protected_path_not_excluded(self):
        """Item 344 (`GPT-R65-001`, `WF8c` scope clause (k)):
        `docs/ai-workflow/REVIEW_PROTOCOL.md` is this work item's own real,
        currently-declared implementation-stage `protected_paths` entry
        (`workflow-v2-1-core-artifacts.json`), never `excluded_paths` --
        confirmed against the actual on-disk declaration, not a synthetic
        fixture, alongside an unrelated real `excluded_paths` control
        (`docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`) and a real protected
        `scripts/` prefix control (`scripts/workflow_state.py`)."""
        repo_root = _repo_root()
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            wf.load_implementation_stage_classification(
                repo_root, wf.artifacts_path_for_work_item("workflow-v2-1-core"),
            )
        )
        self.assertEqual(
            wf.classify_path_implementation_stage(
                "docs/ai-workflow/REVIEW_PROTOCOL.md",
                protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
            ),
            "protected",
        )
        self.assertEqual(
            wf.classify_path_implementation_stage(
                "docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md",
                protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
            ),
            "excluded",
        )
        self.assertEqual(
            wf.classify_path_implementation_stage(
                "scripts/workflow_state.py",
                protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
            ),
            "protected",
        )

    def test_372h_dry_run_verifiers_and_ledger_artifacts_are_real_protected_paths(self):
        """OPUS-R129-004: `docs/ai-workflow/dry-run/verify_372h_lock_primitive_
        predicate.py`/`verify_372h_raw_edge_derivation.py` are the actual
        substance of item 372(h)'s conformance obligation -- the protected
        `scripts/workflow_state_test.py::TestGlobalLockOrderItem372h` loads
        and executes them verbatim -- not `WF8b`'s throwaway dry-run
        scenario evidence the surrounding `docs/ai-workflow/dry-run/`
        prefix is otherwise excluded for. Confirmed here as real,
        currently-declared implementation-stage `protected_paths` entries
        (never left to the surrounding excluded prefix), same pattern this
        test already confirms for `workflow-v2-1-core-ledger-status.json`/
        `workflow-v2-1-core-wf8c-evidence.json` (`WF8c` item (n),
        `GPT-R106-002`) -- previously only a manual `TEST_RESULTS.md` note,
        never an automated regression. A path this test confirms
        `protected` here is, by `classify_path_implementation_stage`'s own
        construction, a path whose future edit changes the implementation-
        stage `review_content_manifest` and therefore stales
        `technical_approval` -- there is no separate mechanism to prove
        that with, since `approval_is_current` recomputes exactly this
        classification."""
        repo_root = _repo_root()
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            wf.load_implementation_stage_classification(
                repo_root, wf.artifacts_path_for_work_item("workflow-v2-1-core"),
            )
        )
        for path in (
            "docs/ai-workflow/dry-run/verify_372h_lock_primitive_predicate.py",
            "docs/ai-workflow/dry-run/verify_372h_raw_edge_derivation.py",
            "docs/ai-workflow/registry/workflow-v2-1-core-ledger-status.json",
            "docs/ai-workflow/registry/workflow-v2-1-core-wf8c-evidence.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    wf.classify_path_implementation_stage(
                        path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
                    ),
                    "protected",
                )
        # Control: an ordinary docs/ai-workflow/dry-run/ file with no
        # individual carve-out still falls through to the surrounding
        # excluded prefix -- the carve-out is scoped to these exact paths,
        # not a reclassification of the whole prefix.
        self.assertEqual(
            wf.classify_path_implementation_stage(
                "docs/ai-workflow/dry-run/v2-1-dry-run-plan.md",
                protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
            ),
            "excluded",
        )
        # Control: app/, config/, gradle/ (OPUS-R129-005) are excluded, not
        # protected, at the implementation stage -- the opposite classification
        # from before this round.
        for path in ("app/build.gradle.kts", "config/detekt/detekt.yml", "gradle/libs.versions.toml"):
            with self.subTest(path=path):
                self.assertEqual(
                    wf.classify_path_implementation_stage(
                        path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
                    ),
                    "excluded",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
