#!/usr/bin/env python3
"""Hermetic conformance suite for `D-Fingerprint-Generalization`
(`docs/ai-workflow/WORKFLOW_V2_PLAN.md`, Revision 21, `WF8B-S1-001`).

Covers the core, independently-testable core of missing-tests 141-166: the
per-work-item plan-stage metadata resolver (`resolve_plan_stage_metadata`),
its thirteen-condition fail-closed matrix, the manifest work-item/base-commit
binding, and `route_work_item`'s resume-branch declaration-fact writer.
Runs entirely against disposable scratch Git repositories this suite
creates and destroys itself -- never against this repository's own working
tree or a fixed historical commit, so it is safe on every PR (unlike the
`_demo_test.py` suites, `GPT-R9-002` does not apply here).

Not an exhaustive enumeration of every sub-case the plan's own missing-test
list names (some items name a dozen-plus independent sub-cases) -- this
suite exercises each item's own core, load-bearing assertion at least once,
named in each test's docstring by item number.

Stdlib-only. Run: python3 scripts/workflow_fingerprint_generalization_test.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import workflow_fingerprint as fingerprint
import workflow_state as ws
import workflow_test_harness as h

_REAL_SCRIPTS_DIR = Path(__file__).resolve().parent


def _write_second_item(
    repo, work_item_id="second-item", *, plan_revision=1, base_commit=None,
    work_item_type="process", work_item_kind=None,
):
    """Seeds a second, fully-declared work item's plan-stage
    fixture (its own five protected files, its own `<id>-artifacts.json`,
    its own `WORKFLOW_STATE.json` entry) alongside `workflow-test-harness`'s
    default `"wi"` item is never written here -- this is the *only* item in
    the scratch repo unless the caller writes another. Also seeds a
    `.gitignore` excluding `.ai-review/`, mirroring the real repository, so
    a later bundle-directory write is invisible to plan-stage
    classification the same way it is for real. `work_item_type` defaults
    to `"process"` (every existing call site unchanged); `work_item_kind`
    defaults to mirroring `work_item_type`, matching what `/milestone-plan`
    actually produces for a `"product"`-typed item."""
    (repo.root / ".gitignore").write_text(".ai-review/\n")
    repo.write_plan_docs(work_item_id=work_item_id, plan_revision=plan_revision)
    repo.write_workflow_state(
        active_work_item_id=work_item_id,
        **{
            work_item_id: ws.default_work_item(
                work_item_id=work_item_id, work_item_type=work_item_type,
                work_item_kind=work_item_kind or work_item_type,
                plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                base_commit=base_commit or repo.base,
                governing_workflow_version="2.1",
                plan_revision=plan_revision, last_transition="t0",
            ),
        },
    )


class TestSecondWorkItemProducesDistinctIdentity(unittest.TestCase):
    """Items 143/144/149/151: a second work item resolves and fingerprints
    its own plan-stage content, distinct from `workflow-v2-1-core`'s (no
    such item exists in these scratch repos, but the resolver never reads
    or falls back to any literal naming one)."""

    def test_143_second_item_computes_distinct_id_with_own_manifest(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            digest, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "second-item",
            )
            self.assertEqual(projection["work_item_id"], "second-item")
            paths = {e["path"] for e in projection["review_content_manifest"]}
            self.assertEqual(
                paths,
                {
                    "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                    "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
                    "docs/TECHNICAL_DECISIONS.md",
                    "docs/ai-workflow/registry/second-item-registry.json",
                    "docs/ai-workflow/requirements/second-item-mapping.json",
                },
            )
            self.assertNotEqual(digest, "")

    def test_144_mutating_second_items_own_protected_file_changes_only_its_own_id(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            before, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "second-item",
            )
            (repo.root / "docs" / "ai-workflow" / "registry" / "second-item-registry.json").write_text(
                json.dumps({"work_item_id": "second-item", "plan_revision": 1, "checkpoints": [], "extra": True})
            )
            after, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "second-item",
            )
            self.assertNotEqual(before, after)

    def test_149_cli_read_only_omitted_work_item_id_resolves_to_live_active(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            work_items, active = fingerprint._load_workflow_state_work_items(repo.root, None)
            self.assertEqual(active, "second-item")
            digest_explicit, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "second-item",
            )
            digest_via_active, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, active,
            )
            self.assertEqual(digest_explicit, digest_via_active)

    def test_149_cli_subprocess_prints_no_workflow_v2_1_core_literal_for_a_second_item(self):
        """Missing-test item 149's own real-subprocess half: a genuine
        `python3 scripts/workflow_fingerprint.py --work-item-id
        second-item` invocation's entire stdout must not name
        `workflow-v2-1-core` anywhere, end to end through the real CLI,
        not only through direct function calls."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            scripts_dir = repo.root / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(_REAL_SCRIPTS_DIR / "workflow_fingerprint.py", scripts_dir / "workflow_fingerprint.py")
            result = subprocess.run(
                ["python3", str(scripts_dir / "workflow_fingerprint.py"),
                 "--work-item-id", "second-item"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("second-item", result.stdout)
            self.assertNotIn("workflow-v2-1-core", result.stdout)

    def test_149_write_manifest_with_work_item_id_omitted_refuses(self):
        """OPUS-R28-010's own sub-case, real-subprocess: `--write-manifest`
        with `--work-item-id` omitted refuses with a usage error, before
        any directory is created or bound -- never silently resolving to
        and writing the live `active_work_item_id`'s directory."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            scripts_dir = repo.root / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(_REAL_SCRIPTS_DIR / "workflow_fingerprint.py", scripts_dir / "workflow_fingerprint.py")
            result = subprocess.run(
                ["python3", str(scripts_dir / "workflow_fingerprint.py"),
                 repo.base, "--write-manifest"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("work-item-id is required", result.stderr)
            self.assertFalse((repo.root / ".ai-review" / "second-item").exists())
            self.assertFalse((repo.root / ".ai-review" / "current").exists())

    def test_151_manifest_write_for_second_item_names_only_its_own_files(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            bundle_dir = repo.root / ".ai-review" / "second-item" / "current"
            bundle_dir.mkdir(parents=True)
            digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(repo.root, "second-item")
            (bundle_dir / "REVIEW_REQUEST.md").write_text(f"review_content_id: {digest}\n")
            (bundle_dir / "PLAN.md").write_text("plan\n")
            (bundle_dir / "DIFF.patch").write_text("\n")
            (bundle_dir / "TEST_RESULTS.md").write_text("results\n")
            written_digest, bundle_id = fingerprint.write_manifest_with_verified_identifiers_for_work_item(
                repo.root, "second-item",
            )
            self.assertEqual(written_digest, digest)
            manifest_text = (bundle_dir / "MANIFEST.md").read_text()
            self.assertIn("work_item_id: second-item", manifest_text)
            self.assertNotIn("workflow-v2-1-core", manifest_text)


class TestWorkflowV21CoreThroughTheNewResolver(unittest.TestCase):
    """Missing-test item 159 (`OPUS-R25-014`): `workflow_test_harness.py`'s
    `write_plan_docs` emits a valid per-item `<id>-artifacts.json`
    consumed successfully by `resolve_plan_stage_metadata`/
    `compute_review_content_id_plan_stage_for_work_item` with no override
    needed, **including `workflow-v2-1-core` itself** -- the historically
    hardcoded item, as opposed to `test_143`'s `second-item` and
    `test_workflow_v2_1_core_itself_needs_no_protected_override`
    (`workflow_test_harness_test.py`), which proves the same "no override
    needed" property but only through the old, frozen-default
    `compute_review_content_id_plan_stage`, never through the new
    resolver at all."""

    def test_workflow_v2_1_core_resolves_with_no_override_through_the_new_resolver(self):
        with h.ScratchRepo() as repo:
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            repo.write_plan_docs(work_item_id="workflow-v2-1-core", plan_revision=1)
            repo.write_workflow_state(
                active_work_item_id="workflow-v2-1-core",
                **{"workflow-v2-1-core": ws.default_work_item(
                    work_item_id="workflow-v2-1-core", work_item_type="process", work_item_kind="process",
                    plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                    registry_path="docs/ai-workflow/registry/workflow-v2-1-core-registry.json",
                    mapping_path="docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json",
                    base_commit=repo.base, governing_workflow_version="1",
                    plan_revision=1, last_transition="t0",
                )},
            )
            repo.commit_plan_docs_as_base()
            metadata = fingerprint.resolve_plan_stage_metadata(repo.root, "workflow-v2-1-core")
            self.assertEqual(metadata.work_item_id, "workflow-v2-1-core")
            digest, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "workflow-v2-1-core",
            )
            self.assertEqual(projection["work_item_id"], "workflow-v2-1-core")
            self.assertNotEqual(digest, "")


class TestSyntheticWorkItemKind(unittest.TestCase):
    """Missing-test item 157 (`OPUS-R25-011`): a `work_item_kind:
    "synthetic"` item (mirroring the real `v2-1-dry-run`) with
    `work_item_type: "process"` computes its own distinct plan-stage
    `review_content_id` through the corrected resolution algorithm --
    exercising the exact item the superseded code comment named."""

    def test_synthetic_kind_process_type_item_computes_its_own_id(self):
        with h.ScratchRepo() as repo:
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            repo.write_plan_docs(work_item_id="v2-1-dry-run", plan_revision=1)
            repo.write_workflow_state(
                active_work_item_id="v2-1-dry-run",
                **{"v2-1-dry-run": ws.default_work_item(
                    work_item_id="v2-1-dry-run", work_item_type="process", work_item_kind="synthetic",
                    plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                    registry_path="docs/ai-workflow/registry/v2-1-dry-run-registry.json",
                    mapping_path="docs/ai-workflow/requirements/v2-1-dry-run-mapping.json",
                    base_commit=repo.base, governing_workflow_version="2.1",
                    plan_revision=1, last_transition="t0",
                )},
            )
            repo.commit_plan_docs_as_base()
            digest, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "v2-1-dry-run",
            )
            self.assertEqual(projection["work_item_id"], "v2-1-dry-run")
            self.assertNotEqual(digest, "")


class TestCommitSourceMetadataPinning(unittest.TestCase):
    """Missing-test item 145 (`OPUS-R25-013`): `compute_review_content_id_
    plan_stage_at_commit_for_work_item` resolves metadata from the given
    commit, not from a subsequently-edited live `WORKFLOW_STATE.json`; and
    fails closed, not silently, against a pre-migration commit."""

    def test_commit_source_recomputation_unaffected_by_a_later_live_edit(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            pinned_commit = repo.base
            digest_at_commit, _ = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
                repo.root, "second-item", pinned_commit,
            )
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["plan_revision"] = 999
            repo.commit_files("edit live state after pinning", {
                "docs/ai-workflow/WORKFLOW_STATE.json": json.dumps(state),
            })
            digest_recomputed, _ = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
                repo.root, "second-item", pinned_commit,
            )
            self.assertEqual(
                digest_at_commit, digest_recomputed,
                "commit-source recomputation must be pinned to the given commit, "
                "unaffected by a later live-state edit",
            )

    def test_pre_migration_commit_fails_closed_not_a_silent_fallback(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            pre_migration_commit = repo.base
            (repo.root / "docs" / "ai-workflow" / "registry" / "second-item-artifacts.json").unlink()
            post_removal_commit = repo.commit_files(
                "remove artifacts file (simulates a pre-migration commit)", {},
            )
            with self.assertRaises(fingerprint.MissingWorkItemArtifactsDeclarationError):
                fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
                    repo.root, "second-item", post_removal_commit,
                )
            # The earlier, pre-removal commit must still resolve fine --
            # confirms the failure is scoped to the commit that actually
            # predates the migration, not a global break.
            fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
                repo.root, "second-item", pre_migration_commit,
            )  # must not raise


class TestFailClosedMatrix(unittest.TestCase):
    """A representative subset of the thirteen-condition fail-closed
    matrix (items 146-148, 150, 152, 161-163)."""

    def test_condition_1_unknown_work_item(self):
        with h.ScratchRepo() as repo:
            repo.write_workflow_state(active_work_item_id=None)
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.UnknownWorkItemError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "nonexistent")

    def test_condition_2_unsupported_type_not_applicable(self):
        """`R7`: condition 2 still fails closed for a genuinely unsupported
        `work_item_type` -- (a) an invalid string value, (b) the field
        missing entirely (`None` via `entry.get`), and (c) an unhashable
        value (a corrupted/hand-edited state file) -- while `"product"` (a
        real `WORK_ITEM_TYPES` member) is no longer such a type. `registry_path`/
        `mapping_path`/`base_commit` stay non-null in all subcases so the
        rejection is provably condition 2's, not condition 3's."""
        base_work_item = ws.default_work_item(
            work_item_id="prod-item", work_item_type="process", work_item_kind="product",
            plan_path="docs/milestones/x.md",
            registry_path="docs/ai-workflow/registry/prod-item-registry.json",
            mapping_path="docs/ai-workflow/requirements/prod-item-mapping.json",
            governing_workflow_version="1", plan_revision=1, last_transition="t0",
            base_commit="deadbeef",
        )
        cases = {
            "unsupported string value": {**base_work_item, "work_item_type": "milestone"},
            "missing field": {k: v for k, v in base_work_item.items() if k != "work_item_type"},
            "unhashable value": {**base_work_item, "work_item_type": ["process"]},
        }
        for label, work_item in cases.items():
            with self.subTest(label):
                with h.ScratchRepo() as repo:
                    repo.write_workflow_state(
                        active_work_item_id="prod-item", **{"prod-item": work_item},
                    )
                    repo.commit_plan_docs_as_base()
                    with self.assertRaises(fingerprint.PlanStageNotApplicableError):
                        fingerprint.resolve_plan_stage_metadata(repo.root, "prod-item")

    def test_product_work_item_resolves_like_a_process_item(self):
        """R1/R3: a legitimate `work_item_type="product"` item resolves
        through `resolve_plan_stage_metadata` exactly like a process item
        -- the direct counterpart of the existing process-item resolution
        tests (items 143/144/149/151)."""
        with h.ScratchRepo() as repo:
            declared_base_commit = repo.base
            _write_second_item(repo, "prod-item", work_item_type="product")
            repo.commit_plan_docs_as_base()
            metadata = fingerprint.resolve_plan_stage_metadata(repo.root, "prod-item")
            self.assertEqual(metadata.work_item_id, "prod-item")
            self.assertEqual(metadata.work_item_type, "product")
            self.assertEqual(metadata.plan_path, "docs/ai-workflow/WORKFLOW_V2_PLAN.md")
            self.assertEqual(
                metadata.registry_path, "docs/ai-workflow/registry/prod-item-registry.json",
            )
            self.assertEqual(
                metadata.mapping_path, "docs/ai-workflow/requirements/prod-item-mapping.json",
            )
            self.assertEqual(metadata.base_commit, declared_base_commit)
            self.assertEqual(metadata.plan_revision, 1)

    def test_product_work_item_computes_review_content_id(self):
        """R3: fingerprinting completes for a product item -- the
        product-typed counterpart of
        `test_143_second_item_computes_distinct_id_with_own_manifest`."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "prod-item", work_item_type="product")
            repo.commit_plan_docs_as_base()
            digest, projection = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "prod-item",
            )
            self.assertNotEqual(digest, "")
            self.assertEqual(projection["work_item_id"], "prod-item")
            paths = {e["path"] for e in projection["review_content_manifest"]}
            self.assertEqual(
                paths,
                {
                    "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                    "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
                    "docs/TECHNICAL_DECISIONS.md",
                    "docs/ai-workflow/registry/prod-item-registry.json",
                    "docs/ai-workflow/requirements/prod-item-mapping.json",
                },
            )

    def test_condition_3_missing_metadata_field_named(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item", base_commit=None)
            # Overwrite with a null base_commit to exercise condition 3.
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["base_commit"] = None
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.MissingPlanStageMetadataError) as ctx:
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")
            self.assertIn("base_commit", str(ctx.exception))

    def _null_declared_field(self, field_name):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"][field_name] = None
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.MissingPlanStageMetadataError) as ctx:
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")
            self.assertIn(field_name, str(ctx.exception))

    def test_condition_3a_null_plan_path(self):
        self._null_declared_field("plan_path")

    def test_condition_3b_null_registry_path(self):
        self._null_declared_field("registry_path")

    def test_condition_3c_null_mapping_path(self):
        self._null_declared_field("mapping_path")

    def test_condition_4_invalid_path_grammar_absolute_path(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["plan_path"] = "/etc/passwd"
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.InvalidPlanStageMetadataPathError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_5_missing_artifacts_declaration(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            (repo.root / "docs" / "ai-workflow" / "registry" / "second-item-artifacts.json").unlink()
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.MissingWorkItemArtifactsDeclarationError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_4b_invalid_path_grammar_registry_path_absolute(self):
        """Condition 4's registry_path sub-case, independent of 4a's
        plan_path sub-case above -- the same rule-violation vector (an
        absolute path) exercised against a *different* one of the three
        fields."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["registry_path"] = "/etc/passwd"
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.InvalidPlanStageMetadataPathError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_4c_invalid_path_grammar_mapping_path_traversal(self):
        """Condition 4's mapping_path sub-case, using the second named
        rule-violation vector (a `../` traversal) rather than repeating
        4a/4b's absolute-path vector a third time."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["mapping_path"] = "../escape.json"
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.InvalidPlanStageMetadataPathError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_6a_registry_work_item_id_mismatch(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            registry_path = repo.root / "docs" / "ai-workflow" / "registry" / "second-item-registry.json"
            data = json.loads(registry_path.read_text())
            data["work_item_id"] = "someone-else"
            registry_path.write_text(json.dumps(data))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.RegistryWorkItemIdMismatchError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_6b_mapping_work_item_id_mismatch(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            mapping_path = repo.root / "docs" / "ai-workflow" / "requirements" / "second-item-mapping.json"
            data = json.loads(mapping_path.read_text())
            data["work_item_id"] = "someone-else"
            mapping_path.write_text(json.dumps(data))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.MappingWorkItemIdMismatchError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_6c_artifacts_work_item_id_mismatch(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            artifacts_path = repo.root / "docs" / "ai-workflow" / "registry" / "second-item-artifacts.json"
            data = json.loads(artifacts_path.read_text())
            data["work_item_id"] = "someone-else"
            artifacts_path.write_text(json.dumps(data))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.ArtifactsWorkItemIdMismatchError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_7a_declared_path_not_a_member_of_protected_set(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            artifacts_path = repo.root / "docs" / "ai-workflow" / "registry" / "second-item-artifacts.json"
            data = json.loads(artifacts_path.read_text())
            data["plan_stage"]["protected_paths"].remove("docs/ai-workflow/WORKFLOW_V2_PLAN.md")
            artifacts_path.write_text(json.dumps(data))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.PlanStageMetadataNotProtectedError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_7b_three_paths_not_pairwise_distinct(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            # mapping_path collapsed onto registry_path's own value -- both
            # remain members of the protected set (satisfying 7a), but the
            # triple is no longer pairwise distinct.
            state["work_items"]["second-item"]["mapping_path"] = (
                state["work_items"]["second-item"]["registry_path"]
            )
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.PlanStageMetadataNotProtectedError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_8a_duplicate_plan_path_claimed_by_two_items(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            third = dict(state["work_items"]["second-item"])
            third["work_item_id"] = "third-item"
            third["registry_path"] = "docs/ai-workflow/registry/third-item-registry.json"
            third["mapping_path"] = "docs/ai-workflow/requirements/third-item-mapping.json"
            # Only plan_path collides -- registry_path/mapping_path differ.
            state["work_items"]["third-item"] = third
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.DuplicateWorkItemArtifactPathError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_8b_duplicate_registry_path_claimed_by_two_items(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            third = dict(state["work_items"]["second-item"])
            third["work_item_id"] = "third-item"
            third["plan_path"] = "docs/ai-workflow/third-item-plan.md"
            third["mapping_path"] = "docs/ai-workflow/requirements/third-item-mapping.json"
            # Only registry_path collides -- plan_path/mapping_path differ.
            state["work_items"]["third-item"] = third
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.DuplicateWorkItemArtifactPathError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_8c_duplicate_mapping_path_claimed_by_two_items(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            third = dict(state["work_items"]["second-item"])
            third["work_item_id"] = "third-item"
            third["plan_path"] = "docs/ai-workflow/third-item-plan.md"
            third["registry_path"] = "docs/ai-workflow/registry/third-item-registry.json"
            # Only mapping_path collides -- plan_path/registry_path differ.
            state["work_items"]["third-item"] = third
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.DuplicateWorkItemArtifactPathError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_158_partial_collision_same_plan_path_only_raises_from_resolver_itself(self):
        """Missing-test item 158 (`OPUS-R25-007`): two work items
        declaring the same `plan_path` but different `registry_path`/
        `mapping_path` (a partial, not full-triple, collision) raise
        `DuplicateWorkItemArtifactPathError` from `resolve_plan_stage_metadata`
        itself -- exercising the read-path relocation, not only
        `validate_state`'s own write-path backstop (already covered by
        `test_duplicate_registry_path_across_two_items_fails_validate_state`
        below)."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            third = dict(state["work_items"]["second-item"])
            third["work_item_id"] = "third-item"
            third["registry_path"] = "docs/ai-workflow/registry/third-item-registry.json"
            third["mapping_path"] = "docs/ai-workflow/requirements/third-item-mapping.json"
            # plan_path is deliberately left identical to second-item's own
            # -- only one of the three fields collides.
            state["work_items"]["third-item"] = third
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.DuplicateWorkItemArtifactPathError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_9_plan_revision_mismatch(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item", plan_revision=1)
            plan_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan_path.write_text("# Plan (Revision 2)\n\nplan v1\n")
            repo.commit_plan_docs_as_base()
            with self.assertRaises(fingerprint.PlanRevisionMismatchError):
                fingerprint.resolve_plan_stage_metadata(repo.root, "second-item")

    def test_condition_10_unclassified_path_scoped_per_item(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            (repo.root / "novel_untracked_path.txt").write_text("surprise\n")
            with self.assertRaises(fingerprint.UnclassifiedPathError):
                fingerprint.compute_review_content_id_plan_stage_for_work_item(repo.root, "second-item")

    def test_condition_12a_manifest_bound_to_different_work_item_refuses(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            bundle_dir = repo.root / ".ai-review" / "second-item" / "current"
            bundle_dir.mkdir(parents=True)
            for name, content in (
                ("REVIEW_REQUEST.md", "review_content_id: " + "a" * 64 + "\n"),
                ("PLAN.md", "plan\n"), ("DIFF.patch", "\n"), ("TEST_RESULTS.md", "results\n"),
            ):
                (bundle_dir / name).write_text(content)
            (bundle_dir / "MANIFEST.md").write_text("work_item_id: some-other-item\n")
            with self.assertRaises(fingerprint.BundleWorkItemMismatchError):
                fingerprint.write_manifest_with_verified_identifiers_for_work_item(repo.root, "second-item")

    def test_152_manifest_bound_to_v2_1_dry_run_refuses_workflow_v2_1_core_write_and_leaves_it_untouched(self):
        """Missing-test item 152's own exact literal names -- `v2-1-dry-run`
        and `workflow-v2-1-core` -- rather than the generic `second-item`/
        `some-other-item` placeholders `test_condition_12a` above uses,
        plus the "leaves the existing MANIFEST.md untouched" assertion
        neither `test_condition_12a`/`test_condition_12b` currently makes."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "workflow-v2-1-core")
            repo.commit_plan_docs_as_base()
            bundle_dir = repo.root / ".ai-review" / "workflow-v2-1-core" / "current"
            bundle_dir.mkdir(parents=True)
            for name, content in (
                ("REVIEW_REQUEST.md", "review_content_id: " + "a" * 64 + "\n"),
                ("PLAN.md", "plan\n"), ("DIFF.patch", "\n"), ("TEST_RESULTS.md", "results\n"),
            ):
                (bundle_dir / name).write_text(content)
            manifest_path = bundle_dir / "MANIFEST.md"
            existing_manifest_content = "work_item_id: v2-1-dry-run\n"
            manifest_path.write_text(existing_manifest_content)

            with self.assertRaises(fingerprint.BundleWorkItemMismatchError) as ctx:
                fingerprint.write_manifest_with_verified_identifiers_for_work_item(
                    repo.root, "workflow-v2-1-core",
                )
            self.assertIn("v2-1-dry-run", str(ctx.exception))
            self.assertIn("workflow-v2-1-core", str(ctx.exception))
            self.assertEqual(
                manifest_path.read_text(), existing_manifest_content,
                "a refused write must leave the existing MANIFEST.md byte-for-byte untouched",
            )

    def test_condition_12b_manifest_present_but_unbound_refuses(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            bundle_dir = repo.root / ".ai-review" / "second-item" / "current"
            bundle_dir.mkdir(parents=True)
            for name, content in (
                ("REVIEW_REQUEST.md", "review_content_id: " + "a" * 64 + "\n"),
                ("PLAN.md", "plan\n"), ("DIFF.patch", "\n"), ("TEST_RESULTS.md", "results\n"),
            ):
                (bundle_dir / name).write_text(content)
            manifest_path = bundle_dir / "MANIFEST.md"
            existing_manifest_content = "# Bundle Manifest\n\nreview_content_id: " + "a" * 64 + "\n"
            manifest_path.write_text(existing_manifest_content)
            with self.assertRaises(fingerprint.BundleWorkItemMismatchError) as ctx:
                fingerprint.write_manifest_with_verified_identifiers_for_work_item(repo.root, "second-item")
            self.assertIn("unbound", str(ctx.exception).lower() + repr(ctx.exception))
            self.assertEqual(
                manifest_path.read_text(), existing_manifest_content,
                "a refused write must leave the existing unbound MANIFEST.md byte-for-byte untouched",
            )

    def test_condition_13_base_commit_disagreement_refuses(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["base_commit"] = None
            state_path.write_text(json.dumps(state))
            repo.commit_plan_docs_as_base()
            # No declared base_commit at all fails closed rather than
            # silently falling back to any other item's value -- the
            # underlying condition-13 property this matrix row protects.
            with self.assertRaises(fingerprint.MissingPlanStageMetadataError) as ctx:
                fingerprint.compute_review_content_id_plan_stage_for_work_item(repo.root, "second-item")
            self.assertIn("base_commit", str(ctx.exception))

    def test_condition_13_manifest_bound_to_a_different_base_commit_refuses(self):
        """The write-path half of condition 13 (`OPUS-R26-003`):
        `MANIFEST.md`'s own recorded `base_commit` disagreeing with the
        resolved work item's declared value fails closed via the same
        `BundleWorkItemMismatchError` condition 12 raises -- one failure
        mode, not two mechanisms."""
        with h.ScratchRepo() as repo:
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            bundle_dir = repo.root / ".ai-review" / "second-item" / "current"
            bundle_dir.mkdir(parents=True)
            for name, content in (
                ("REVIEW_REQUEST.md", "review_content_id: " + "a" * 64 + "\n"),
                ("PLAN.md", "plan\n"), ("DIFF.patch", "\n"), ("TEST_RESULTS.md", "results\n"),
            ):
                (bundle_dir / name).write_text(content)
            (bundle_dir / "MANIFEST.md").write_text(
                "# Bundle Manifest\n\nwork_item_id: second-item\nbase_commit: " + "c" * 40 + "\n"
            )
            with self.assertRaises(fingerprint.BundleWorkItemMismatchError):
                fingerprint.write_manifest_with_verified_identifiers_for_work_item(repo.root, "second-item")


class TestRouteWorkItemResumeBranchDeclarationFacts(unittest.TestCase):
    """Items 154 (resume-branch case)/OPUS-R28-002: `route_work_item`'s
    resume branch accepts the same four declaration facts a fresh entry
    does, per field independently: writes a still-null field, no-ops on an
    identical repeat, and raises on a genuine conflict."""

    def _config(self):
        config = ws.default_config()
        config["default_workflow_version"] = "2.1"
        return config

    def _pre_declared_state(self):
        return {
            "schema_version": 1,
            "active_work_item_id": "v2-1-dry-run-like",
            "work_items": {
                "v2-1-dry-run-like": ws.default_work_item(
                    work_item_id="v2-1-dry-run-like", work_item_type="process",
                    work_item_kind="synthetic", plan_path="docs/ai-workflow/dry-run/plan.md",
                    registry_path=None, governing_workflow_version="2.1",
                    plan_revision=1, last_transition="t0",
                ),
            },
        }

    def test_resume_branch_populates_still_null_fields(self):
        state = self._pre_declared_state()
        config = self._config()
        new_state = ws.route_work_item(
            state, config, work_item_id="v2-1-dry-run-like", work_item_type="process",
            work_item_kind="synthetic", plan_path="docs/ai-workflow/dry-run/plan.md",
            registry_path="docs/ai-workflow/registry/v2-1-dry-run-like-registry.json",
            mapping_path="docs/ai-workflow/requirements/v2-1-dry-run-like-mapping.json",
            base_commit="a" * 40, plan_revision=1, now="t1",
        )
        entry = new_state["work_items"]["v2-1-dry-run-like"]
        self.assertEqual(entry["registry_path"], "docs/ai-workflow/registry/v2-1-dry-run-like-registry.json")
        self.assertEqual(entry["mapping_path"], "docs/ai-workflow/requirements/v2-1-dry-run-like-mapping.json")
        self.assertEqual(entry["base_commit"], "a" * 40)

    def test_resume_branch_is_idempotent_on_exact_repeat(self):
        state = self._pre_declared_state()
        config = self._config()
        kwargs = dict(
            work_item_id="v2-1-dry-run-like", work_item_type="process", work_item_kind="synthetic",
            plan_path="docs/ai-workflow/dry-run/plan.md",
            registry_path="docs/ai-workflow/registry/v2-1-dry-run-like-registry.json",
            mapping_path="docs/ai-workflow/requirements/v2-1-dry-run-like-mapping.json",
            base_commit="a" * 40, plan_revision=1,
        )
        first = ws.route_work_item(state, config, now="t1", **kwargs)
        second = ws.route_work_item(first, config, now="t2", **kwargs)
        entry = second["work_items"]["v2-1-dry-run-like"]
        self.assertEqual(entry["registry_path"], kwargs["registry_path"])
        self.assertEqual(entry["base_commit"], kwargs["base_commit"])

    def test_resume_branch_raises_on_genuine_conflict(self):
        state = self._pre_declared_state()
        config = self._config()
        first = ws.route_work_item(
            state, config, work_item_id="v2-1-dry-run-like", work_item_type="process",
            work_item_kind="synthetic", plan_path="docs/ai-workflow/dry-run/plan.md",
            registry_path="docs/ai-workflow/registry/v2-1-dry-run-like-registry.json",
            mapping_path="docs/ai-workflow/requirements/v2-1-dry-run-like-mapping.json",
            base_commit="a" * 40, plan_revision=1, now="t1",
        )
        with self.assertRaises(ws.WorkItemDeclarationFactConflictError):
            ws.route_work_item(
                first, config, work_item_id="v2-1-dry-run-like", work_item_type="process",
                work_item_kind="synthetic", plan_path="docs/ai-workflow/dry-run/plan.md",
                registry_path="docs/ai-workflow/registry/v2-1-dry-run-like-registry.json",
                mapping_path="docs/ai-workflow/requirements/v2-1-dry-run-like-mapping.json",
                base_commit="b" * 40, plan_revision=1, now="t2",
            )

    def test_creation_branch_end_to_end_through_real_bundle_generation(self):
        """Item 154's own creation-branch case, run end to end (`OPUS-R25-004`,
        `OPUS-R28-002`): unlike every other test in this suite, which
        constructs its fixture's `WORKFLOW_STATE.json` entry directly via
        `ws.default_work_item(...)` -- bypassing `route_work_item` entirely
        -- this test calls `route_work_item` itself, for a `work_item_id`
        genuinely absent from `state["work_items"]`, and follows through
        to a real, successful `prepare-ai-review.sh` invocation. This is
        not interchangeable with the resume-branch tests above: only the
        creation branch (`default_work_item`'s own write path) is
        exercised here."""
        with h.ScratchRepo() as repo:
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            work_item_id = "fresh-item"
            repo.write_plan_docs(work_item_id=work_item_id, plan_revision=1)
            repo.commit_plan_docs_as_base()

            config = self._config()
            state = {"schema_version": 1, "active_work_item_id": None, "work_items": {}}
            self.assertNotIn(work_item_id, state["work_items"])
            new_state = ws.route_work_item(
                state, config, work_item_id=work_item_id, work_item_type="process",
                work_item_kind="process",
                plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                base_commit=repo.base, plan_revision=1, now="t0",
            )
            entry = new_state["work_items"][work_item_id]
            self.assertEqual(entry["registry_path"], f"docs/ai-workflow/registry/{work_item_id}-registry.json")
            self.assertEqual(entry["base_commit"], repo.base)

            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state_path.write_text(json.dumps(new_state))
            subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "route_work_item creation branch"],
                cwd=repo.root, check=True, capture_output=True,
            )

            digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, work_item_id,
            )
            bundle_dir = repo.root / ".ai-review" / work_item_id / "current"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {digest}\n")
            _, current_head = fingerprint.current_worktree_root_and_head(repo.root)
            (bundle_dir / "TEST_RESULTS.md").write_text(f"stage: plan (revision 1)\nhead: {current_head}\n")

            scripts_dir = repo.root / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            for name in ("prepare-ai-review.sh", "workflow_fingerprint.py", "workflow_state.py", "workflow_gate_policy.py", "workflow_forge.py"):
                dest = scripts_dir / name
                shutil.copy(_REAL_SCRIPTS_DIR / name, dest)
            script_path = scripts_dir / "prepare-ai-review.sh"
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            result = subprocess.run(
                ["bash", str(script_path), repo.base, "plan", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_text = (bundle_dir / "MANIFEST.md").read_text()
            self.assertIn(f"work_item_id: {work_item_id}", manifest_text)
            self.assertIn(f"review_content_id: {digest}", manifest_text)


class TestArtifactsDeclarationsGenerator(unittest.TestCase):
    """Items 155/156: `generate_artifacts_declarations`'s default template
    satisfies the path-to-role binding check by construction, and the
    generated file's own concrete implementation-stage self-reference
    classifies correctly."""

    def test_155_default_template_satisfies_protected_membership(self):
        declarations = ws.generate_artifacts_declarations(
            "new-item", "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            "docs/ai-workflow/registry/new-item-registry.json",
            "docs/ai-workflow/requirements/new-item-mapping.json",
            work_item_type="process",
        )
        protected = set(declarations["plan_stage"]["protected_paths"])
        self.assertEqual(
            protected,
            {
                "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                "docs/ai-workflow/registry/new-item-registry.json",
                "docs/ai-workflow/requirements/new-item-mapping.json",
            },
        )

    def test_156_self_referential_entry_protects_own_file_at_implementation_stage(self):
        declarations = ws.generate_artifacts_declarations(
            "new-item", "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            "docs/ai-workflow/registry/new-item-registry.json",
            "docs/ai-workflow/requirements/new-item-mapping.json",
            work_item_type="process",
        )
        own_path = "docs/ai-workflow/registry/new-item-artifacts.json"
        self.assertIn(own_path, declarations["implementation_stage"]["protected_paths"])
        classification = fingerprint.classify_path_implementation_stage(
            own_path,
            declarations["implementation_stage"]["protected_paths"],
            declarations["implementation_stage"]["protected_prefixes"],
            declarations["implementation_stage"]["excluded_paths"],
            declarations["implementation_stage"]["excluded_prefixes"],
        )
        self.assertEqual(classification, "protected")


class TestArtifactsDeclarationsGeneratorIsUsableAtBothStages(unittest.TestCase):
    """Salvage audit `B4`/`B5`: `/milestone-plan` step 3 is the sole
    sanctioned writer of `<work_item_id>-artifacts.json`, so whatever this
    generator emits is what a work item actually lives with. The
    implementation-stage half used to name nothing but the declarations
    file protecting itself, and the plan-stage half inherited an exclusion
    set authored as the complement of a *different*, larger protected set,
    so both halves failed closed on ordinary content: the first
    implementation bundle raised `UnclassifiedPathError` on the item's own
    deliverable, and a plan-stage edit to `docs/TECHNICAL_DECISIONS.md` --
    which `/milestone-plan` step 5 explicitly directs the planner to
    engage with -- raised it too."""

    def _declarations(self, work_item_type, plan_path):
        return ws.generate_artifacts_declarations(
            "new-item", plan_path,
            "docs/ai-workflow/registry/new-item-registry.json",
            "docs/ai-workflow/requirements/new-item-mapping.json",
            work_item_type=work_item_type,
        )

    def _classify_impl(self, declarations, path):
        stage = declarations["implementation_stage"]
        return fingerprint.classify_path_implementation_stage(
            path, stage["protected_paths"], stage["protected_prefixes"],
            stage["excluded_paths"], stage["excluded_prefixes"],
        )

    def _classify_plan(self, declarations, path):
        stage = declarations["plan_stage"]
        return fingerprint.classify_path(
            path, frozenset(stage["protected_paths"]),
            stage["excluded_paths"], stage["excluded_prefixes"],
        )

    def test_work_item_type_is_required(self):
        with self.assertRaises(TypeError):
            ws.generate_artifacts_declarations(
                "new-item", "docs/ai-workflow/new-item-plan.md",
                "docs/ai-workflow/registry/new-item-registry.json",
                "docs/ai-workflow/requirements/new-item-mapping.json",
            )
        with self.assertRaises(ws.InvalidWorkItemTypeError):
            self._declarations("widget", "docs/ai-workflow/new-item-plan.md")

    def test_process_deliverable_tree_is_protected_product_tree_is_excluded(self):
        declarations = self._declarations("process", "docs/ai-workflow/new-item-plan.md")
        for path in ("scripts/workflow_state.py", ".claude/commands/milestone-plan.md"):
            self.assertEqual(self._classify_impl(declarations, path), "protected", path)
        for path in ("app/src/main/kotlin/Feature.kt", "gradle/libs.versions.toml"):
            self.assertEqual(self._classify_impl(declarations, path), "excluded", path)

    def test_product_deliverable_tree_is_protected_process_tree_is_excluded(self):
        declarations = self._declarations("product", "docs/milestones/new-item-plan.md")
        for path in ("app/src/main/kotlin/Feature.kt", "gradle/libs.versions.toml",
                     "config/detekt/detekt.yml"):
            self.assertEqual(self._classify_impl(declarations, path), "protected", path)
        for path in ("scripts/workflow_state.py", ".claude/commands/milestone-plan.md",
                     "docs/ai-workflow/MILESTONE_WORKFLOW.md"):
            self.assertEqual(self._classify_impl(declarations, path), "excluded", path)

    def test_every_path_this_workflow_itself_writes_is_classified(self):
        """The machinery's own writes must never be the thing that fails a
        work item closed: `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json`
        (every state writer), `FUNCTIONAL_CHECKLIST_PATH`
        (`/prepare-functional-review`'s mandatory evidence commit),
        `docs/ROADMAP.md` (`/accept-milestone` step 3), the registry and
        requirements trees (`/milestone-plan`, `/milestone-implement`'s
        ledger), and the item's own three declaration paths."""
        for work_item_type, plan_path in (
            ("process", "docs/ai-workflow/new-item-plan.md"),
            ("product", "docs/milestones/new-item-plan.md"),
        ):
            declarations = self._declarations(work_item_type, plan_path)
            for path in (
                "docs/ai-workflow/WORKFLOW_STATE.json",
                "docs/ai-workflow/WORKFLOW_CONFIG.json",
                ws.FUNCTIONAL_CHECKLIST_PATH,
                "docs/ROADMAP.md",
                "docs/ai-workflow/registry/new-item-registry.json",
                "docs/ai-workflow/requirements/new-item-mapping.json",
                "docs/ai-workflow/requirements/new-item-ledger.md",
                plan_path,
            ):
                with self.subTest(work_item_type=work_item_type, path=path):
                    self.assertEqual(self._classify_impl(declarations, path), "excluded")

    def test_own_declarations_file_stays_protected_despite_the_registry_exclusion(self):
        for work_item_type, plan_path in (
            ("process", "docs/ai-workflow/new-item-plan.md"),
            ("product", "docs/milestones/new-item-plan.md"),
        ):
            declarations = self._declarations(work_item_type, plan_path)
            self.assertEqual(
                self._classify_impl(
                    declarations, "docs/ai-workflow/registry/new-item-artifacts.json",
                ),
                "protected",
                work_item_type,
            )

    def test_implementation_stage_default_still_fails_closed_on_a_novel_path(self):
        declarations = self._declarations("process", "docs/ai-workflow/new-item-plan.md")
        for path in ("some/unheard/of/place.txt", "vendor/thing.kt", "tools/build.sh"):
            with self.subTest(path=path):
                with self.assertRaises(fingerprint.UnclassifiedPathError):
                    self._classify_impl(declarations, path)

    def test_a_new_workflow_design_document_is_excluded_not_unclassified(self):
        """Salvage audit `B7`: the one judgment the template makes rather
        than defers. Everything under `docs/ai-workflow/` is either this
        workflow's own bookkeeping or another work item's plan-stage
        content, so it is excluded at both stages; a `process` item whose
        deliverable genuinely is such a document must move that exact path
        into `implementation_stage.protected_paths` during
        `SELF_REVIEWING_PLAN`, and doing so wins, protected-first."""
        declarations = self._declarations("process", "docs/ai-workflow/new-item-plan.md")
        doc = "docs/ai-workflow/A_NEW_DESIGN_DOC.md"
        self.assertEqual(self._classify_impl(declarations, doc), "excluded")
        declarations["implementation_stage"]["protected_paths"][doc] = (
            "this item's own deliverable, promoted during SELF_REVIEWING_PLAN"
        )
        self.assertEqual(self._classify_impl(declarations, doc), "protected")

    def test_plan_stage_default_classifies_the_frozen_protected_documents(self):
        """`B5`: `PLAN_STAGE_EXCLUDED_PATHS` is the complement of
        `PLAN_STAGE_PROTECTED`, but this template's own protected set is
        the item's own three declaration paths, so those three documents
        used to land in neither set."""
        declarations = self._declarations("process", "docs/ai-workflow/new-item-plan.md")
        for path in sorted(fingerprint.PLAN_STAGE_PROTECTED):
            with self.subTest(path=path):
                self.assertEqual(self._classify_plan(declarations, path), "excluded")

    def test_plan_stage_default_keeps_an_own_declaration_path_protected(self):
        """Protected-first ordering: an item whose own `plan_path` happens
        to be one of the frozen documents keeps it protected, not
        excluded."""
        declarations = self._declarations("process", "docs/ai-workflow/WORKFLOW_V2_PLAN.md")
        self.assertEqual(
            self._classify_plan(declarations, "docs/ai-workflow/WORKFLOW_V2_PLAN.md"), "protected",
        )
        self.assertNotIn(
            "docs/ai-workflow/WORKFLOW_V2_PLAN.md", declarations["plan_stage"]["excluded_paths"],
        )

    def test_a_sibling_work_items_plan_document_is_classified_at_both_stages(self):
        """Salvage audit `B7`: two work items in one repository is `D1`'s
        normal case, not an exception, and every real work item in this
        repository ended up hand-enumerating every sibling's plan
        document -- one of them missing a sibling, caught only at plan
        review. The template classifies the whole `docs/ai-workflow/`
        prefix at both stages instead."""
        sibling = "docs/ai-workflow/some-other-item-plan.md"
        for work_item_type, plan_path in (
            ("process", "docs/ai-workflow/new-item-plan.md"),
            ("product", "docs/milestones/new-item-plan.md"),
        ):
            declarations = self._declarations(work_item_type, plan_path)
            with self.subTest(work_item_type=work_item_type):
                self.assertEqual(self._classify_plan(declarations, sibling), "excluded")
                self.assertEqual(self._classify_impl(declarations, sibling), "excluded")
                # The item's own declared paths still win, protected-first.
                self.assertEqual(self._classify_plan(declarations, plan_path), "protected")
                self.assertEqual(
                    self._classify_impl(
                        declarations, "docs/ai-workflow/registry/new-item-artifacts.json",
                    ),
                    "protected",
                )

    def test_plan_stage_default_still_fails_closed_on_a_novel_path(self):
        declarations = self._declarations("process", "docs/ai-workflow/new-item-plan.md")
        with self.assertRaises(fingerprint.UnclassifiedPathError):
            self._classify_plan(declarations, "some/unheard/of/place.txt")


class TestValidateStateDuplicatePathDetection(unittest.TestCase):
    """Belt-and-suspenders write-time check (`OPUS-R25-007`): a hand-edited
    state file with two work items claiming the same registry_path fails
    `validate_state` directly, independent of the read-path's own
    `resolve_plan_stage_metadata` enforcement."""

    def test_duplicate_registry_path_across_two_items_fails_validate_state(self):
        state = {
            "schema_version": ws.SCHEMA_VERSION,
            "active_work_item_id": None,
            "work_items": {
                "item-a": ws.default_work_item(
                    work_item_id="item-a", work_item_type="process", work_item_kind="process",
                    plan_path="a-plan.md", registry_path="shared-registry.json",
                    governing_workflow_version="2.1", plan_revision=1, last_transition="t0",
                ),
                "item-b": ws.default_work_item(
                    work_item_id="item-b", work_item_type="process", work_item_kind="process",
                    plan_path="b-plan.md", registry_path="shared-registry.json",
                    governing_workflow_version="2.1", plan_revision=1, last_transition="t0",
                ),
            },
        }
        with self.assertRaises(fingerprint.DuplicateWorkItemArtifactPathError):
            ws.validate_state(state)


class TestPrepareAiReviewShPlanStageRequiredArgument(unittest.TestCase):
    """Items 153/161-163: `scripts/prepare-ai-review.sh`'s own plan-stage
    path, end to end against a real subprocess invocation of the actual
    script (copied into the scratch repo, never a reimplementation)."""

    def _install_scripts(self, repo):
        scripts_dir = repo.root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for name in ("prepare-ai-review.sh", "workflow_fingerprint.py", "workflow_state.py", "workflow_gate_policy.py", "workflow_forge.py"):
            dest = scripts_dir / name
            shutil.copy(_REAL_SCRIPTS_DIR / name, dest)
        script_path = scripts_dir / "prepare-ai-review.sh"
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return script_path

    def test_162_no_third_argument_refuses_for_plan_stage(self):
        with h.ScratchRepo() as repo:
            script_path = self._install_scripts(repo)
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "plan"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("work-item-id is required", result.stderr)
            self.assertFalse((repo.root / ".ai-review").exists())

    def test_163_base_commit_disagreement_refuses_before_any_content(self):
        with h.ScratchRepo() as repo:
            script_path = self._install_scripts(repo)
            _write_second_item(repo, "second-item", base_commit="d" * 40)
            repo.commit_plan_docs_as_base()
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "plan", "second-item"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("declares base_commit", result.stderr)
            self.assertFalse((repo.root / ".ai-review").exists())

    def test_371f_plan_revision_mirror_disagreement_refuses_before_any_content(self):
        """Item 371(f) (`D-Plan-Revision-Publication`, `WFR-65`): the plan
        stage refuses, before generating any bundle content, when the
        resolved work item's `WORKFLOW_STATE.json` `plan_revision` mirror
        disagrees with its own registry's `plan_revision` -- naming both
        values, exactly as it already refuses a `base_commit` disagreement
        (`test_163` above). The registry and the plan document's own
        `(Revision N)` title are bumped together (so `load_plan_revision`'s
        own cross-check passes and the *mirror* disagreement, not a
        registry/title disagreement, is what's exercised), while
        `WORKFLOW_STATE.json`'s mirror is deliberately left behind --
        exactly the unowned-bump shape `D-Plan-Revision-Publication`
        documents this repository once hit for real."""
        with h.ScratchRepo() as repo:
            script_path = self._install_scripts(repo)
            _write_second_item(repo, "second-item", plan_revision=1)
            repo.commit_plan_docs_as_base()

            # Correct the declared base_commit (same fixup
            # test_153_successful_run_writes_a_bound_manifest below uses),
            # then bump the registry's plan_revision and the plan
            # document's own title together -- so load_plan_revision's
            # cross-check passes and the *mirror* disagreement, not a
            # registry/title disagreement, is what's exercised -- while
            # WORKFLOW_STATE.json's plan_revision mirror is deliberately
            # left behind.
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["base_commit"] = repo.base
            state_path.write_text(json.dumps(state))
            registry_path = repo.root / "docs" / "ai-workflow" / "registry" / "second-item-registry.json"
            registry = json.loads(registry_path.read_text())
            registry["plan_revision"] = 2
            registry_path.write_text(json.dumps(registry))
            plan_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
            plan_path.write_text("# Plan (Revision 2)\n\nplan v2\n")
            subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fix base_commit; bump registry plan_revision only"],
                cwd=repo.root, check=True, capture_output=True,
            )

            result = subprocess.run(
                ["bash", str(script_path), repo.base, "plan", "second-item"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("plan_revision mirror is '1'", result.stderr)
            self.assertIn("registry declares plan_revision '2'", result.stderr)
            self.assertFalse((repo.root / ".ai-review").exists())

    def test_371f_plan_revision_mirror_agreement_proceeds(self):
        """Item 371(f)'s positive half: an agreeing mirror/registry pair
        proceeds unchanged -- the new check is not a false-positive
        refusal on the ordinary case every other test in this class
        already exercises."""
        with h.ScratchRepo() as repo:
            script_path = self._install_scripts(repo)
            _write_second_item(repo, "second-item", plan_revision=1)
            repo.commit_plan_docs_as_base()
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["base_commit"] = repo.base
            state_path.write_text(json.dumps(state))
            subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fix declared base_commit"],
                cwd=repo.root, check=True, capture_output=True,
            )
            digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "second-item",
            )
            bundle_dir = repo.root / ".ai-review" / "second-item" / "current"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {digest}\n")
            _, current_head = fingerprint.current_worktree_root_and_head(repo.root)
            (bundle_dir / "TEST_RESULTS.md").write_text(f"stage: plan (revision 1)\nhead: {current_head}\n")
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "plan", "second-item"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((bundle_dir / "MANIFEST.md").is_file())

    def test_153_successful_run_writes_a_bound_manifest(self):
        with h.ScratchRepo() as repo:
            script_path = self._install_scripts(repo)
            _write_second_item(repo, "second-item")
            repo.commit_plan_docs_as_base()
            # The item's own declared base_commit must equal the commit
            # these files were actually settled at -- correct it in a
            # small follow-up commit (WORKFLOW_STATE.json is excluded, so
            # this never touches plan-stage identity).
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["second-item"]["base_commit"] = repo.base
            state_path.write_text(json.dumps(state))
            subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fix declared base_commit"],
                cwd=repo.root, check=True, capture_output=True,
            )
            digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "second-item",
            )
            bundle_dir = repo.root / ".ai-review" / "second-item" / "current"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {digest}\n")
            _, current_head = fingerprint.current_worktree_root_and_head(repo.root)
            (bundle_dir / "TEST_RESULTS.md").write_text(f"stage: plan (revision 1)\nhead: {current_head}\n")
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "plan", "second-item"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_text = (bundle_dir / "MANIFEST.md").read_text()
            self.assertIn("work_item_id: second-item", manifest_text)
            self.assertIn(f"review_content_id: {digest}", manifest_text)
            # Item 163's own positive half: MANIFEST.md's base_commit
            # field must equal the resolved item's declared value,
            # checked at write time -- not only that a *disagreeing* one
            # refuses (test_163_base_commit_disagreement_refuses_before_any_content
            # above only covers the negative half).
            self.assertIn(f"base_commit: {repo.base}", manifest_text)
            self.assertTrue((repo.root / ".ai-review" / "second-item" / "review-bundle.tar.gz").is_file())

    def test_product_item_successful_run_writes_a_bound_manifest(self):
        """R4: the product-typed counterpart of
        `test_153_successful_run_writes_a_bound_manifest` -- real,
        real-subprocess coverage that a legitimate `"product"` work item
        can generate a plan review bundle through the actual, unmodified
        `prepare-ai-review.sh`, not only through direct function calls."""
        with h.ScratchRepo() as repo:
            script_path = self._install_scripts(repo)
            _write_second_item(repo, "prod-item", work_item_type="product")
            repo.commit_plan_docs_as_base()
            # The item's own declared base_commit must equal the commit
            # these files were actually settled at -- correct it in a
            # small follow-up commit (WORKFLOW_STATE.json is excluded, so
            # this never touches plan-stage identity).
            state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
            state = json.loads(state_path.read_text())
            state["work_items"]["prod-item"]["base_commit"] = repo.base
            state_path.write_text(json.dumps(state))
            subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fix declared base_commit"],
                cwd=repo.root, check=True, capture_output=True,
            )
            digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
                repo.root, "prod-item",
            )
            bundle_dir = repo.root / ".ai-review" / "prod-item" / "current"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {digest}\n")
            _, current_head = fingerprint.current_worktree_root_and_head(repo.root)
            (bundle_dir / "TEST_RESULTS.md").write_text(f"stage: plan (revision 1)\nhead: {current_head}\n")
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "plan", "prod-item"],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_text = (bundle_dir / "MANIFEST.md").read_text()
            self.assertIn("work_item_id: prod-item", manifest_text)
            self.assertIn(f"review_content_id: {digest}", manifest_text)
            self.assertIn(f"base_commit: {repo.base}", manifest_text)
            self.assertIn("work_item_type: product", manifest_text)
            self.assertTrue((repo.root / ".ai-review" / "prod-item" / "review-bundle.tar.gz").is_file())


class TestPrepareAiReviewShAmendmentDiffWorkingTreeAnchor(unittest.TestCase):
    """workflow-2.6.0 CP2 (`D-Plan-Amendment-5` revision, `v2.4.0-003`):
    `AMENDMENT_DIFF.patch` is `git diff <amendment_base_commit> --
    <pathspec>`, anchored at the working tree, over the union of the
    declaration at `amendment_base_commit`, the declaration now, and
    `<id>-artifacts.json` itself, behind a provenance preamble. Driven
    end to end through the real `prepare-ai-review.sh`, the same fixture
    pattern `TestPrepareAiReviewShPlanStageRequiredArgument` uses. The
    amended plan is never committed here -- exactly the state
    `/request-plan-amendment` leaves it in until `/approve-review plan`
    commits it, and the one the `..HEAD` form could never show."""

    ITEM = "amend-item"
    PLAN = "docs/ai-workflow/WORKFLOW_V2_PLAN.md"
    AUDIT = "docs/ai-workflow/WORKFLOW_V2_AUDIT.md"
    ARTIFACTS = f"docs/ai-workflow/registry/{ITEM}-artifacts.json"

    def _install_scripts(self, repo):
        scripts_dir = repo.root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for name in ("prepare-ai-review.sh", "workflow_fingerprint.py", "workflow_state.py", "workflow_gate_policy.py", "workflow_forge.py"):
            shutil.copy(_REAL_SCRIPTS_DIR / name, scripts_dir / name)
        script_path = scripts_dir / "prepare-ai-review.sh"
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return script_path

    def _git(self, repo, *args):
        # Scrubbed the way the product scrubs its own diff (round 3, O3): a
        # runner-side `GIT_DIFF_OPTS` or global pathspec mode must not move
        # the oracle away from the product output.
        env = {key: value for key, value in fingerprint.literal_pathspec_env().items()
               if key != "GIT_DIFF_OPTS"}
        return subprocess.run(
            ["git", *args], cwd=repo.root, check=True, capture_output=True, text=True, env=env,
        ).stdout

    def _state_path(self, repo):
        return repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"

    def _seed_open_amendment(self, repo, *, amendment_base=None):
        """Seeds the item with a line-per-entry `<id>-artifacts.json` (so a
        declaration edit is visible per path), commits it with the item's
        declared `base_commit` corrected, then opens an amendment at that
        commit (or at `amendment_base`) in the working-tree state only --
        `WORKFLOW_STATE.json` is excluded, so this never touches plan-stage
        identity. Returns `(script_path, amendment_base_commit)`."""
        script_path = self._install_scripts(repo)
        _write_second_item(repo, self.ITEM)
        artifacts_path = repo.root / self.ARTIFACTS
        artifacts_path.write_text(json.dumps(json.loads(artifacts_path.read_text()), indent=2) + "\n")
        repo.commit_plan_docs_as_base()
        state = json.loads(self._state_path(repo).read_text())
        state["work_items"][self.ITEM]["base_commit"] = repo.base
        self._state_path(repo).write_text(json.dumps(state))
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "fix declared base_commit")
        base = amendment_base or repo.head()
        state = json.loads(self._state_path(repo).read_text())
        state["work_items"][self.ITEM]["amendment_history"] = [
            {"amendment_id": "0", "resolved_at_plan_revision": None},
        ]
        state["work_items"][self.ITEM]["amendment_base_commit"] = base
        self._state_path(repo).write_text(json.dumps(state))
        return script_path, base

    def _edit_declaration(self, repo, *, drop=(), add=(), exclude=()):
        artifacts_path = repo.root / self.ARTIFACTS
        artifacts = json.loads(artifacts_path.read_text())
        plan_stage = artifacts["plan_stage"]
        plan_stage["protected_paths"] = sorted(
            (set(plan_stage["protected_paths"]) - set(drop)) | set(add)
        )
        for path in exclude:
            plan_stage["excluded_paths"][path] = "dropped from the design set by this amendment"
        artifacts_path.write_text(json.dumps(artifacts, indent=2) + "\n")

    def _amend_plan(self, repo, line="amended design line"):
        plan_path = repo.root / self.PLAN
        plan_path.write_text(plan_path.read_text() + f"{line}\n")

    def _generate(self, repo, script_path, *, as_bytes=False, env=None):
        """Writes the two author-written plan-stage preconditions for the
        current working tree, runs the real script, and returns the
        patch's text (its bytes with `as_bytes`), or `None` when the
        script wrote none. `env`, when given, replaces the script's own
        environment -- the preconditions above are always computed under
        the caller's."""
        digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(
            repo.root, self.ITEM,
        )
        bundle_dir = repo.root / ".ai-review" / self.ITEM / "current"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "REVIEW_REQUEST.md").write_text(f"stage: plan\nreview_content_id: {digest}\n")
        _, current_head = fingerprint.current_worktree_root_and_head(repo.root)
        (bundle_dir / "TEST_RESULTS.md").write_text(f"stage: plan (revision 1)\nhead: {current_head}\n")
        result = subprocess.run(
            ["bash", str(script_path), repo.base, "plan", self.ITEM],
            cwd=repo.root, capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        patch_path = repo.root / ".ai-review" / self.ITEM / "AMENDMENT_DIFF.patch"
        if not patch_path.is_file():
            return None
        return patch_path.read_bytes() if as_bytes else patch_path.read_text()

    def _file_section(self, patch, path):
        """The patch's own section for `path` (header through the next
        `diff --git`), or `None` when it has none."""
        marker = f"diff --git a/{path} b/{path}\n"
        if marker not in patch:
            return None
        section = patch.split(marker, 1)[1]
        return section.split("diff --git ", 1)[0]

    def _preamble(self, patch):
        fields = {}
        for line in patch.split("diff --git ", 1)[0].splitlines():
            if line.startswith("# ") and ": " in line:
                key, value = line[2:].split(": ", 1)
                fields[key] = value
        return fields

    def _assert_applies_at(self, repo, commit, patch):
        worktree = Path(tempfile.mkdtemp(prefix="wf-amendment-apply-"))
        try:
            self._git(repo, "worktree", "add", "-q", "--detach", str(worktree / "wt"), commit)
            patch_file = worktree / "AMENDMENT_DIFF.patch"
            if isinstance(patch, bytes):
                patch_file.write_bytes(patch)
            else:
                patch_file.write_text(patch)
            result = subprocess.run(
                ["git", "apply", "--check", str(patch_file)],
                cwd=worktree / "wt", capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree / "wt")],
                cwd=repo.root, capture_output=True,
            )
            shutil.rmtree(worktree, ignore_errors=True)

    def test_uncommitted_plan_edit_gives_a_non_empty_patch_containing_it(self):
        with h.ScratchRepo() as repo:
            script_path, base = self._seed_open_amendment(repo)
            self._amend_plan(repo)
            patch = self._generate(repo, script_path)
            section = self._file_section(patch, self.PLAN)
            self.assertIsNotNone(section, patch)
            self.assertIn("+amended design line\n", section)
            # 2.5.1 control arm: the `..HEAD` form this replaces is empty
            # for the very same, uncommitted amendment.
            self.assertEqual(self._git(repo, "diff", f"{base}..HEAD", "--", self.PLAN), "")

    def test_new_untracked_protected_file_is_a_new_file_and_the_index_is_restored(self):
        new_path = "docs/ai-workflow/AMENDED_DESIGN.md"
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._edit_declaration(repo, add=[new_path])
            (repo.root / new_path).write_text("new design content\n")
            patch = self._generate(repo, script_path)
            section = self._file_section(patch, new_path)
            self.assertIsNotNone(section, patch)
            self.assertIn("new file mode", section)
            self.assertIn("+new design content\n", section)
            # No lingering intent-to-add: the path is untracked again.
            self.assertEqual(self._git(repo, "ls-files", "--cached", "--", new_path), "")
            self.assertIn(f"?? {new_path}\n", self._git(repo, "status", "--porcelain", "--", new_path))

    def test_path_dropped_from_the_declaration_and_deleted_is_a_deleted_file(self):
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._edit_declaration(repo, drop=[self.AUDIT], exclude=[self.AUDIT])
            (repo.root / self.AUDIT).unlink()
            patch = self._generate(repo, script_path)
            section = self._file_section(patch, self.AUDIT)
            self.assertIsNotNone(section, patch)
            self.assertIn("deleted file mode", section)

    def test_rename_is_a_deleted_file_plus_a_new_file_and_applies_at_the_base(self):
        renamed = "docs/ai-workflow/WORKFLOW_V2_AUDIT_RENAMED.md"
        with h.ScratchRepo() as repo:
            script_path, base = self._seed_open_amendment(repo)
            self._edit_declaration(repo, drop=[self.AUDIT], add=[renamed], exclude=[self.AUDIT])
            (repo.root / self.AUDIT).rename(repo.root / renamed)
            self._amend_plan(repo)
            patch = self._generate(repo, script_path)
            self.assertIn("deleted file mode", self._file_section(patch, self.AUDIT) or "")
            self.assertIn("new file mode", self._file_section(patch, renamed) or "")
            self.assertNotIn("rename from", patch)
            self._assert_applies_at(repo, base, patch)

    def test_dropped_but_kept_path_changed_since_the_base_shows_its_content_diff(self):
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._edit_declaration(repo, drop=[self.AUDIT], exclude=[self.AUDIT])
            (repo.root / self.AUDIT).write_text("audit v2\n")
            patch = self._generate(repo, script_path)
            section = self._file_section(patch, self.AUDIT)
            self.assertIsNotNone(section, patch)
            self.assertNotIn("deleted file mode", section)
            self.assertIn("-audit v1\n", section)
            self.assertIn("+audit v2\n", section)

    def test_dropped_but_kept_path_unchanged_since_the_base_shows_only_the_declaration_hunk(self):
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._edit_declaration(repo, drop=[self.AUDIT])
            patch = self._generate(repo, script_path)
            self.assertIsNone(self._file_section(patch, self.AUDIT), patch)
            declaration = self._file_section(patch, self.ARTIFACTS)
            self.assertIsNotNone(declaration, patch)
            self.assertIn(f'-      "{self.AUDIT}",\n', declaration)

    def test_artifacts_absent_at_the_amendment_base_gives_the_current_only_form(self):
        with h.ScratchRepo() as repo:
            root_commit = repo.base  # the harness's own initial commit: no declaration yet
            script_path, base = self._seed_open_amendment(repo, amendment_base=root_commit)
            self.assertEqual(self._git(repo, "ls-tree", "--name-only", base, "--", self.ARTIFACTS), "")
            self._amend_plan(repo)
            patch = self._generate(repo, script_path)
            current_only = sorted(h.plan_stage_protected_paths(self.ITEM) | {self.ARTIFACTS})
            # The oracle pins the implementation's own diff options, so a
            # configured external diff or context width cannot fail it
            # while the product output is correct (round 2, O2).
            expected = self._git(
                repo, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                "--src-prefix=a/", "--dst-prefix=b/", "--no-renames", "--binary", "-U3",
                base, "--", *current_only,
            )
            self.assertEqual(patch.split("diff --git ", 1)[1], expected.split("diff --git ", 1)[1])
            self.assertTrue(patch.endswith(expected))

    def test_an_edit_to_a_non_protected_path_is_absent(self):
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._amend_plan(repo)
            (repo.root / ".gitignore").write_text(".ai-review/\nnot-design-content/\n")
            (repo.root / "scripts" / "helper.py").write_text("print('tooling')\n")
            patch = self._generate(repo, script_path)
            self.assertIsNotNone(self._file_section(patch, self.PLAN), patch)
            self.assertNotIn(".gitignore", patch)
            self.assertNotIn("scripts/", patch)
            self.assertNotIn("not-design-content", patch)

    def test_archive_member_is_byte_identical_to_the_on_disk_patch(self):
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._amend_plan(repo)
            patch = self._generate(repo, script_path)
            archive = repo.root / ".ai-review" / self.ITEM / "review-bundle.tar.gz"
            with tarfile.open(archive, "r:gz") as tar:
                member = tar.extractfile("AMENDMENT_DIFF.patch").read()
            self.assertEqual(member, patch.encode())

    def test_bundle_id_is_identical_with_and_without_the_patch(self):
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._amend_plan(repo)
            root_dir = repo.root / ".ai-review" / self.ITEM
            bundle_dir = root_dir / "current"
            self.assertIsNotNone(self._generate(repo, script_path))
            recorded = fingerprint.read_manifest_identifiers(bundle_dir / "MANIFEST.md")["bundle_id"]
            self.assertEqual(fingerprint.compute_bundle_id(bundle_dir)[0], recorded)
            with tempfile.TemporaryDirectory() as tmp:
                with tarfile.open(root_dir / "review-bundle.tar.gz", "r:gz") as tar:
                    self.assertIn("AMENDMENT_DIFF.patch", tar.getnames())
                    tar.extractall(tmp, filter="data")
                self.assertEqual(fingerprint.compute_bundle_id(Path(tmp) / "current")[0], recorded)
            (root_dir / "AMENDMENT_DIFF.patch").unlink()
            self.assertEqual(fingerprint.compute_bundle_id(bundle_dir)[0], recorded)

    def test_after_resolution_the_patch_is_removed_and_leaves_the_archive(self):
        with h.ScratchRepo() as repo:
            script_path, _ = self._seed_open_amendment(repo)
            self._amend_plan(repo)
            root_dir = repo.root / ".ai-review" / self.ITEM
            self.assertIsNotNone(self._generate(repo, script_path))
            state = json.loads(self._state_path(repo).read_text())
            state["work_items"][self.ITEM]["amendment_history"][-1]["resolved_at_plan_revision"] = 1
            self._state_path(repo).write_text(json.dumps(state))
            self.assertIsNone(self._generate(repo, script_path))
            self.assertFalse((root_dir / "AMENDMENT_DIFF.patch").exists())
            with tarfile.open(root_dir / "review-bundle.tar.gz", "r:gz") as tar:
                self.assertNotIn("AMENDMENT_DIFF.patch", tar.getnames())

    def test_preamble_fields_equal_the_manifest_and_state(self):
        with h.ScratchRepo() as repo:
            script_path, base = self._seed_open_amendment(repo)
            self._amend_plan(repo)
            patch = self._generate(repo, script_path)
            self.assertTrue(patch.startswith("# AMENDMENT_DIFF.patch"))
            preamble = self._preamble(patch)
            manifest_text = (repo.root / ".ai-review" / self.ITEM / "current" / "MANIFEST.md").read_text()
            manifest = dict(
                line.split(": ", 1) for line in manifest_text.splitlines()
                if line.split(": ", 1)[0] in ("work_item_id", "plan_revision", "review_content_id")
            )
            self.assertEqual(len(manifest), 3, manifest_text)
            for key, value in manifest.items():
                self.assertEqual(preamble.get(key), value, key)
            self.assertEqual(preamble.get("amendment_base_commit"), base)
            self.assertEqual(preamble.get("amendment_id"), "0")

    def test_git_apply_check_accepts_the_patch_against_the_amendment_base(self):
        new_path = "docs/ai-workflow/AMENDED_DESIGN.md"
        with h.ScratchRepo() as repo:
            script_path, base = self._seed_open_amendment(repo)
            self._amend_plan(repo)
            self._edit_declaration(repo, add=[new_path])
            (repo.root / new_path).write_text("new design content\n")
            patch = self._generate(repo, script_path)
            self._assert_applies_at(repo, base, patch)

    def test_non_utf8_and_binary_protected_content_generates_an_applicable_patch(self):
        """Implementation review round 1, Important 4: a protected file
        that is not valid UTF-8 aborted the whole plan-stage generation
        while an amendment was open (`text=True` capture). The patch is
        bytes end to end now, and `--binary` keeps a binary protected
        file applicable."""
        binary_path = "docs/ai-workflow/AMENDED_DIAGRAM.bin"
        with h.ScratchRepo() as repo:
            script_path, base = self._seed_open_amendment(repo)
            # The plan itself must stay UTF-8 (its title line is parsed);
            # any other protected file carries no such constraint.
            audit_path = repo.root / self.AUDIT
            audit_path.write_bytes(audit_path.read_bytes() + b"caf\xe9 latin-1 design line\n")
            self._edit_declaration(repo, add=[binary_path])
            (repo.root / binary_path).write_bytes(bytes(range(256)) * 4)
            patch = self._generate(repo, script_path, as_bytes=True)
            self.assertIsNotNone(patch)
            self.assertTrue(patch.startswith(b"# AMENDMENT_DIFF.patch"))
            self.assertIn(b"+caf\xe9 latin-1 design line\n", patch)
            self.assertIn(f"diff --git a/{binary_path} b/{binary_path}\n".encode(), patch)
            self.assertIn(b"GIT binary patch", patch)
            self._assert_applies_at(repo, base, patch)

    def test_patch_is_byte_stable_under_a_hostile_global_git_config(self):
        """Implementation review round 1, Important 5: every git option
        that shapes the patch is pinned on the command line, so a user's
        `diff.noprefix`, `diff.mnemonicPrefix`, `color.ui=always`,
        `diff.external` or `diff.renames` changes nothing -- byte for
        byte -- and `git apply --check` still accepts it."""
        renamed = "docs/ai-workflow/WORKFLOW_V2_AUDIT_RENAMED.md"
        with h.ScratchRepo() as repo:
            script_path, base = self._seed_open_amendment(repo)
            self._edit_declaration(repo, drop=[self.AUDIT], add=[renamed], exclude=[self.AUDIT])
            (repo.root / self.AUDIT).rename(repo.root / renamed)
            self._amend_plan(repo)
            baseline = self._generate(repo, script_path, as_bytes=True)
            self.assertIsNotNone(baseline)

            config_dir = Path(tempfile.mkdtemp(prefix="wf-hostile-git-config-"))
            try:
                external = config_dir / "external-diff.sh"
                external.write_text("#!/bin/sh\necho EXTERNAL-DIFF-RAN\n")
                external.chmod(0o755)
                hostile = config_dir / "gitconfig"
                hostile.write_text(
                    "[diff]\n"
                    "\tnoprefix = true\n"
                    "\tmnemonicPrefix = true\n"
                    "\trenames = copies\n"
                    f"\texternal = {external}\n"
                    "[color]\n"
                    "\tui = always\n"
                    "\tdiff = always\n"
                )
                env = dict(os.environ, GIT_CONFIG_GLOBAL=str(hostile))
                hostile_patch = self._generate(repo, script_path, as_bytes=True, env=env)
            finally:
                shutil.rmtree(config_dir, ignore_errors=True)
            self.assertEqual(hostile_patch, baseline)
            self.assertNotIn(b"EXTERNAL-DIFF-RAN", hostile_patch)
            self.assertNotIn(b"\x1b[", hostile_patch)
            self._assert_applies_at(repo, base, hostile_patch)

    def test_mid_file_edit_keeps_context_under_a_zero_context_config_and_environment(self):
        """Implementation review round 2, O1: `diff.context=0` and
        `GIT_DIFF_OPTS=--unified=0` would give a mid-file edit a
        zero-context hunk `git apply --check` rejects; the pinned `-U3`
        and the scrubbed environment keep the patch byte-identical and
        applicable."""
        with h.ScratchRepo() as repo:
            script_path, base = self._seed_open_amendment(repo)
            plan_path = repo.root / self.PLAN
            state_bytes = self._state_path(repo).read_bytes()
            plan_path.write_text(plan_path.read_text() + "".join(f"design line {i}\n" for i in range(12)))
            self._git(repo, "add", "--", self.PLAN)
            self._git(repo, "commit", "-q", "-m", "a multi-line plan at the amendment base")
            base = repo.head()
            state = json.loads(state_bytes)
            state["work_items"][self.ITEM]["amendment_base_commit"] = base
            self._state_path(repo).write_text(json.dumps(state))
            plan_path.write_text(plan_path.read_text().replace(
                "design line 6\n", "design line 6, amended mid-file\n"))
            baseline = self._generate(repo, script_path, as_bytes=True)
            self.assertIn(b" design line 5\n-design line 6\n+design line 6, amended mid-file\n", baseline)

            config_dir = Path(tempfile.mkdtemp(prefix="wf-zero-context-git-config-"))
            try:
                hostile = config_dir / "gitconfig"
                hostile.write_text("[diff]\n\tcontext = 0\n")
                env = dict(os.environ, GIT_CONFIG_GLOBAL=str(hostile), GIT_DIFF_OPTS="--unified=0")
                zero_context = subprocess.run(
                    ["git", "diff", base, "--", self.PLAN], cwd=repo.root, env=env,
                    check=True, capture_output=True,
                ).stdout
                self.assertNotIn(b"\n design line 5\n", zero_context)  # the hazard is real
                hostile_patch = self._generate(repo, script_path, as_bytes=True, env=env)
            finally:
                shutil.rmtree(config_dir, ignore_errors=True)
            self.assertEqual(hostile_patch, baseline)
            self._assert_applies_at(repo, base, hostile_patch)

    def test_runner_diff_opts_and_global_pathspec_modes_move_neither_product_nor_oracle(self):
        """Implementation review round 3, O1/O3: a `GIT_DIFF_OPTS` or a
        global glob/icase pathspec mode in the runner's own environment
        neither aborts generation (Git refuses `--literal-pathspecs`
        alongside those modes) nor shifts the oracle off the product."""
        for hostile in ({"GIT_DIFF_OPTS": "--unified=0"}, {"GIT_GLOB_PATHSPECS": "1"},
                        {"GIT_ICASE_PATHSPECS": "1"}, {"GIT_NOGLOB_PATHSPECS": "1"}):
            with self.subTest(env=hostile), mock.patch.dict(os.environ, hostile):
                self.test_artifacts_absent_at_the_amendment_base_gives_the_current_only_form()


class TestPrepareAiReviewShImplementationStageHeadGuard(unittest.TestCase):
    """GPT-R42-001: an implementation/post-fix bundle must not be
    finalized while `WORKFLOW_STATE.json`'s own
    `work_items[work_item_id].reviewed_implementation_head` disagrees
    with the exact commit the bundle was actually generated at -- the
    defect that let an already-generated bundle's own manifest declare
    one reviewed head while the bundle's own copied `WORKFLOW_STATE.json`
    snapshot still named an older one. Exercised end to end against a
    real subprocess invocation of the actual script (copied into the
    scratch repo, never a reimplementation), mirroring
    `TestPrepareAiReviewShPlanStageRequiredArgument`'s technique for the
    plan stage. The guard reads `WORKFLOW_STATE.json` straight off disk,
    so these fixtures deliberately leave it uncommitted at generation
    time -- exactly the pre-technical-approval convention
    `record_bundle_generation`'s own docstring and this round's
    `REVIEW_REQUEST.md` describe (the state write happens before the
    bundle is finalized, not necessarily before it is committed)."""

    def _install_scripts(self, repo):
        scripts_dir = repo.root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for name in ("prepare-ai-review.sh", "workflow_fingerprint.py", "workflow_state.py", "workflow_gate_policy.py", "workflow_forge.py"):
            dest = scripts_dir / name
            shutil.copy(_REAL_SCRIPTS_DIR / name, dest)
        script_path = scripts_dir / "prepare-ai-review.sh"
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return script_path

    def _seed_and_implement(self, repo, work_item_id="wi"):
        """Settles the plan-stage fixture as `repo.base`, then adds one
        more commit (`impl_head`) representing the reviewed implementation
        content -- `impl.txt` is declared implementation-stage protected
        so it classifies rather than raising `UnclassifiedPathError`.
        Returns `impl_head`."""
        (repo.root / ".gitignore").write_text(".ai-review/\n")
        repo.write_plan_docs(work_item_id=work_item_id, plan_revision=1)
        declarations = ws.generate_artifacts_declarations(
            work_item_id, "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            f"docs/ai-workflow/registry/{work_item_id}-registry.json",
            f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
            work_item_type="process",
        )
        declarations["implementation_stage"]["protected_paths"]["impl.txt"] = "test fixture content"
        artifacts_path = repo.root / "docs" / "ai-workflow" / "registry" / f"{work_item_id}-artifacts.json"
        artifacts_path.write_text(json.dumps(declarations) + "\n")
        repo.commit_plan_docs_as_base()
        impl_head = repo.commit("implement thing", filename="impl.txt")
        return impl_head

    def _write_review_request(self, repo, work_item_id, base, impl_head, implementation_revision=1):
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
            fingerprint.load_implementation_stage_classification(
                repo.root, fingerprint.artifacts_path_for_work_item(work_item_id),
            )
        )
        digest, _ = fingerprint.compute_review_content_id_implementation_stage_at_commit(
            repo.root, base, impl_head, "process", work_item_id,
            protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
        )
        bundle_dir = repo.root / ".ai-review" / work_item_id / "current"
        bundle_dir.mkdir(parents=True, exist_ok=True)  # a later round reuses round 1's own directory
        (bundle_dir / "REVIEW_REQUEST.md").write_text(
            f"stage: post-fix\nreview_content_id: {digest}\n"
        )
        # OPUS-R133-003: IMPLEMENTATION_SUMMARY.md is a STUB_FILES entry
        # (prepare-ai-review.sh creates it empty only if missing, never
        # overwrites) but is also author-edited before every real
        # invocation, same as REVIEW_REQUEST.md above -- unconditionally
        # (re)written here so `finalize_bundle_generation`'s now-live
        # assert_stage_completeness check has a matching revision to find,
        # exactly as a real round's author-written summary would.
        (bundle_dir / "IMPLEMENTATION_SUMMARY.md").write_text(
            f"implementation_revision: {implementation_revision}\n\nfixture round\n"
        )
        return bundle_dir

    def test_stale_reviewed_implementation_head_refuses_before_archiving(self):
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            impl_head = self._seed_and_implement(repo, work_item_id)
            self._write_review_request(repo, work_item_id, repo.base, impl_head)

            entry = ws.default_work_item(
                work_item_id=work_item_id, work_item_type="process", work_item_kind="process",
                plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                base_commit=repo.base, governing_workflow_version="1",
                plan_revision=1, last_transition="t0",
            )
            entry["reviewed_implementation_head"] = "0" * 40
            entry["implementation_revision"] = 1
            repo.write_workflow_state(active_work_item_id=work_item_id, **{work_item_id: entry})

            script_path = self._install_scripts(repo)
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GPT-R42-001", result.stderr)
            self.assertIn("reviewed_implementation_head", result.stderr)
            # The guard runs before any write to current/ (GPT-R43-003) --
            # no MANIFEST.md and no archive are ever produced for a bundle
            # this guard refused.
            self.assertFalse((repo.root / ".ai-review" / work_item_id / "current" / "MANIFEST.md").is_file())
            self.assertFalse((repo.root / ".ai-review" / work_item_id / "review-bundle.tar.gz").is_file())

    def test_matching_reviewed_implementation_head_succeeds(self):
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            impl_head = self._seed_and_implement(repo, work_item_id)
            bundle_dir = self._write_review_request(repo, work_item_id, repo.base, impl_head)

            entry = ws.default_work_item(
                work_item_id=work_item_id, work_item_type="process", work_item_kind="process",
                plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                base_commit=repo.base, governing_workflow_version="1",
                plan_revision=1, last_transition="t0",
            )
            entry["reviewed_implementation_head"] = impl_head
            entry["implementation_revision"] = 1
            repo.write_workflow_state(active_work_item_id=work_item_id, **{work_item_id: entry})

            script_path = self._install_scripts(repo)
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_text = (bundle_dir / "MANIFEST.md").read_text()
            self.assertIn(f"reviewed_implementation_head: {impl_head}", manifest_text)

    def test_stale_amendment_diff_patch_is_never_archived_at_the_implementation_stage(self):
        """Implementation review round 1, Optional 1: once an amendment has
        resolved, the plan stage's last `AMENDMENT_DIFF.patch` can still
        sit next to `current/`. The archive includes it only at the plan
        stage, so an implementation or post-fix archive never ships that
        stale plan-stage copy."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            impl_head = self._seed_and_implement(repo, work_item_id)
            self._write_review_request(repo, work_item_id, repo.base, impl_head)
            entry = ws.default_work_item(
                work_item_id=work_item_id, work_item_type="process", work_item_kind="process",
                plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                base_commit=repo.base, governing_workflow_version="1",
                plan_revision=1, last_transition="t0",
            )
            entry["reviewed_implementation_head"] = impl_head
            entry["implementation_revision"] = 1
            repo.write_workflow_state(active_work_item_id=work_item_id, **{work_item_id: entry})
            root_dir = repo.root / ".ai-review" / work_item_id
            (root_dir / "AMENDMENT_DIFF.patch").write_text("# stale plan-stage patch\n")

            script_path = self._install_scripts(repo)
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with tarfile.open(root_dir / "review-bundle.tar.gz", "r:gz") as tar:
                names = tar.getnames()
            self.assertIn("current", names)
            self.assertNotIn("AMENDMENT_DIFF.patch", names)

    def test_interrupted_after_durability_commit_resumes_without_a_second_commit(self):
        """Item 227 (`WF8c`): a simulated interruption after the
        durability commit `S` lands but before the bundle's files are
        written is safe to resume -- a fresh session, via the
        caller-level bundle-publication-resume contract alone (never a
        second invocation of `record_bundle_generation`), discovers `S`
        as the current tip, confirms live HEAD == S with unchanged
        content, and writes the bundle's files directly: reusing the
        same `generation_head`/`reviewed_implementation_head`, producing
        no second durability commit, no `implementation_revision`
        change, no `phase` rewrite, and reaching the same round
        identity. Unlike this class's other fixtures (which write
        `WORKFLOW_STATE.json` to the working tree only, uncommitted, via
        `repo.write_workflow_state`), this test commits it for real,
        carrying the same trailer shape `record_bundle_generation`'s own
        durability commit carries, and never pre-writes any bundle file
        -- proving the positive scenario item 227 actually describes
        (files genuinely absent, `S` genuinely durable), not merely a
        stale-manifest variant of it. `prepare-ai-review.sh` never calls
        `git commit` or writes `WORKFLOW_STATE.json` (confirmed by
        inspection: zero occurrences of either in the script, declared
        in its own `state_writer: false` header comment), so the
        no-second-commit/no-phase-rewrite guarantees are structural; what
        this test proves is the positive resume path itself actually
        completes and reaches the same round identity."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            base_entry = ws.default_work_item(
                work_item_id=work_item_id, work_item_type="process", work_item_kind="process",
                plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                base_commit="0" * 40, governing_workflow_version="1",
                plan_revision=1, last_transition="t0",
            )
            # A baseline WORKFLOW_STATE.json commit, landed *before*
            # impl_head so it sits outside the reviewed_implementation_
            # head..HEAD provenance interval S's own role-validation
            # examines -- without it, S would be this repo's first-ever
            # WORKFLOW_STATE.json commit, making every static top-level
            # field (schema_version, active_work_item_id) look "changed"
            # to the role validator, which forbids exactly that (item
            # 267). Mirrors _seed_base_provenance_state's own rationale
            # in workflow_state_test.py.
            repo.commit_files(
                "seed base state",
                {"docs/ai-workflow/WORKFLOW_STATE.json": json.dumps({
                    "schema_version": 1, "active_work_item_id": work_item_id,
                    "work_items": {work_item_id: base_entry},
                })},
            )

            # Inlined variant of _seed_and_implement that additionally
            # excludes WORKFLOW_STATE.json at the implementation stage
            # (mirroring this real repository's own workflow-v2-1-core-
            # artifacts.json) -- the shared helper's own default
            # declaration leaves it unclassified, which is fine for
            # every other test in this class (none of which puts a real
            # WORKFLOW_STATE.json commit inside the diffed base..HEAD
            # range), but this test's own durability commit S is exactly
            # such a commit.
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            repo.write_plan_docs(work_item_id=work_item_id, plan_revision=1)
            declarations = ws.generate_artifacts_declarations(
                work_item_id, "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                work_item_type="process",
            )
            declarations["implementation_stage"]["protected_paths"]["impl.txt"] = "test fixture content"
            declarations["implementation_stage"]["excluded_paths"]["docs/ai-workflow/WORKFLOW_STATE.json"] = (
                "runtime-mutable per-work-item state -- excluded so the durability commit itself "
                "never needs to be classified as protected/implementation content"
            )
            artifacts_path = repo.root / "docs" / "ai-workflow" / "registry" / f"{work_item_id}-artifacts.json"
            artifacts_path.write_text(json.dumps(declarations) + "\n")
            repo.commit_plan_docs_as_base()
            impl_head = repo.commit("implement thing", filename="impl.txt")

            entry = dict(base_entry)
            entry["reviewed_implementation_head"] = impl_head
            entry["implementation_revision"] = 1
            entry["phase"] = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
            state_content = json.dumps({
                "schema_version": 1, "active_work_item_id": work_item_id,
                "work_items": {work_item_id: entry},
            })
            s_sha = repo.commit_files(
                "record gen (durability commit S)",
                {"docs/ai-workflow/WORKFLOW_STATE.json": state_content},
                trailers={
                    "Workflow-Bundle-Generation-Record": f"{work_item_id}/1",
                    "Workflow-Work-Item": work_item_id,
                },
            )
            self.assertEqual(repo.head(), s_sha)

            # REVIEW_REQUEST.md is the human/agent-authored input the
            # script reads (not one of "the bundle's files" item 227
            # describes as unwritten -- those are MANIFEST.md and the
            # archive, this script's own output); writing it here mirrors
            # a real session that prepared the review request, then ran
            # record_bundle_generation (the commit above), then was
            # interrupted before ever invoking this script.
            bundle_dir = self._write_review_request(repo, work_item_id, repo.base, impl_head)
            self.assertFalse((bundle_dir / "MANIFEST.md").exists())  # the script's own output, genuinely never written
            self.assertFalse((repo.root / ".ai-review" / work_item_id / "review-bundle.tar.gz").exists())

            script_path = self._install_scripts(repo)
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            # No second durability commit: live HEAD is byte-identical to S.
            self.assertEqual(repo.head(), s_sha)

            # The manifest's two head fields now name two different
            # commits, as they must (salvage audit `I1`): `generation_head`
            # is S itself -- the commit this generation ran at, and the
            # commit `review_content_id` was measured at -- while
            # `reviewed_implementation_head` is WORKFLOW_STATE.json's own
            # field of that name, still pointing at the content commit
            # impl_head that S's durability write recorded. Before the
            # salvage repair both lines carried S, so the manifest
            # contradicted the state file (and the approval record
            # /approve-review implementation derives from it) under one
            # name. review_content_id is unaffected by the distinction --
            # S touches only the excluded WORKFLOW_STATE.json path,
            # contributing nothing to the diffed content between impl_head
            # and S -- confirmed by this same assertion succeeding:
            # REVIEW_REQUEST.md's review_content_id (computed against
            # base..impl_head by _write_review_request) was independently
            # reproduced by the script computing at base..S, or
            # assert_review_request_states_review_content_id above would
            # itself have refused.
            manifest_text = (bundle_dir / "MANIFEST.md").read_text()
            self.assertIn(f"generation_head: {s_sha}", manifest_text)
            self.assertIn(f"reviewed_implementation_head: {impl_head}", manifest_text)
            self.assertNotIn(f"reviewed_implementation_head: {s_sha}", manifest_text)
            self.assertIn("implementation_revision: 1", manifest_text)

            # No phase rewrite, no implementation_revision change: the
            # durable WORKFLOW_STATE.json at S is exactly what this
            # resume read and left untouched.
            durable = json.loads((repo.root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
            self.assertEqual(
                durable["work_items"][work_item_id]["phase"], "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
            )
            self.assertEqual(durable["work_items"][work_item_id]["implementation_revision"], 1)

    def test_valid_provenance_interval_with_excluded_only_commit_succeeds(self):
        """WF8B-003 remediation: `reviewed_implementation_head` need not
        equal `head_sha` exactly any more -- a bounded interval of
        excluded-only commits followed by a dedicated ordinary
        `Workflow-Bundle-Generation-Record` commit for the exact head is
        equally valid, reusing the same
        `workflow_state.implementation_provenance_interval_reachable`
        check `/approve-review implementation`'s own gate uses. `notes.txt`
        is declared excluded from the start (not widened in a later
        commit), so the interim commit inside the interval never touches a
        protected path itself."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            (repo.root / ".gitignore").write_text(".ai-review/\n")
            repo.write_plan_docs(work_item_id=work_item_id, plan_revision=1)
            declarations = ws.generate_artifacts_declarations(
                work_item_id, "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                work_item_type="process",
            )
            declarations["implementation_stage"]["protected_paths"]["impl.txt"] = "test fixture content"
            declarations["implementation_stage"]["excluded_paths"]["notes.txt"] = "test fixture, excluded"
            declarations["implementation_stage"]["excluded_paths"]["docs/ai-workflow/WORKFLOW_STATE.json"] = (
                "runtime state, never protected"
            )
            artifacts_path = repo.root / "docs" / "ai-workflow" / "registry" / f"{work_item_id}-artifacts.json"
            artifacts_path.write_text(json.dumps(declarations) + "\n")
            repo.commit_plan_docs_as_base()
            impl_head = repo.commit("implement thing", filename="impl.txt")  # P

            bundle_dir = self._write_review_request(repo, work_item_id, repo.base, impl_head)

            # A baseline WORKFLOW_STATE.json commit -- static fields (work_item_id,
            # paths, ...) already present -- so the record commit's own field
            # diff below reflects only what actually changes, not every field
            # appearing "added from nothing" (mirrors workflow_state_test.py's
            # own _seed_base_provenance_state).
            baseline_entry = ws.default_work_item(
                work_item_id=work_item_id, work_item_type="process", work_item_kind="process",
                plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
                registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
                mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
                base_commit=repo.base, governing_workflow_version="1",
                plan_revision=1, last_transition="t0",
            )
            baseline_entry["state_revision"] = 1
            repo.commit_files(
                "seed base state",
                {"docs/ai-workflow/WORKFLOW_STATE.json": json.dumps(
                    {"schema_version": 1, "active_work_item_id": work_item_id,
                     "work_items": {work_item_id: baseline_entry}},
                    indent=2,
                ) + "\n"},
            )

            excluded_commit = repo.commit("unrelated excluded commit", filename="notes.txt")
            self.assertNotEqual(excluded_commit, impl_head)

            entry = dict(baseline_entry)
            entry["reviewed_implementation_head"] = impl_head
            entry["implementation_revision"] = 1
            entry["phase"] = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
            entry["state_revision"] = 2
            entry["last_transition"] = "t1"
            state_for_record = {
                "schema_version": 1, "active_work_item_id": work_item_id,
                "work_items": {work_item_id: entry},
            }
            record_commit = repo.commit_files(
                "record gen",
                {"docs/ai-workflow/WORKFLOW_STATE.json": json.dumps(state_for_record, indent=2) + "\n"},
                trailers={
                    "Workflow-Bundle-Generation-Record": f"{work_item_id}/1",
                    "Workflow-Work-Item": work_item_id,
                },
            )

            script_path = self._install_scripts(repo)
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_text = (bundle_dir / "MANIFEST.md").read_text()
            # The two head fields name two different commits (salvage audit
            # `I1`): `generation_head` is the durability commit this
            # generation ran at, `reviewed_implementation_head` is
            # WORKFLOW_STATE.json's own on-disk value (impl_head) -- exactly
            # the value the preflight validated via the interval check, and
            # exactly the value /approve-review implementation records as
            # technical_approval.reviewed_content_commit.
            self.assertIn(f"generation_head: {record_commit}", manifest_text)
            self.assertIn(f"reviewed_implementation_head: {impl_head}", manifest_text)
            self.assertNotIn(f"reviewed_implementation_head: {record_commit}", manifest_text)
            live_state = json.loads((repo.root / "docs/ai-workflow/WORKFLOW_STATE.json").read_text())
            self.assertEqual(
                live_state["work_items"][work_item_id]["reviewed_implementation_head"], impl_head,
            )

    def _write_state_entry(self, repo, work_item_id, *, base, head, revision):
        entry = ws.default_work_item(
            work_item_id=work_item_id, work_item_type="process", work_item_kind="process",
            plan_path="docs/ai-workflow/WORKFLOW_V2_PLAN.md",
            registry_path=f"docs/ai-workflow/registry/{work_item_id}-registry.json",
            mapping_path=f"docs/ai-workflow/requirements/{work_item_id}-mapping.json",
            base_commit=base, governing_workflow_version="1",
            plan_revision=1, last_transition="t0",
        )
        entry["reviewed_implementation_head"] = head
        entry["implementation_revision"] = revision
        repo.write_workflow_state(active_work_item_id=work_item_id, **{work_item_id: entry})

    def _generate_first_round(self, repo, work_item_id, script_path):
        """Establishes a real, successful round-1 bundle (no prior
        `MANIFEST.md` to compare against, so `implementation_revision`
        advancement is unguarded for this call, same as the plan stage's
        own creation branch) -- the fixture every GPT-R43-001/-003 test
        below needs as its "previous round" starting point."""
        impl_head = self._seed_and_implement(repo, work_item_id)
        self._write_review_request(repo, work_item_id, repo.base, impl_head)
        self._write_state_entry(repo, work_item_id, base=repo.base, head=impl_head, revision=1)
        result = subprocess.run(
            ["bash", str(script_path), repo.base, "implementation", work_item_id],
            cwd=repo.root, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return impl_head

    def test_new_head_with_stale_revision_refuses(self):
        """GPT-R43-001: the head-only guard from GPT-R42-001 is satisfied
        (state's reviewed_implementation_head matches the new commit), but
        implementation_revision was left at the previous round's value --
        must still refuse.

        Salvage-audit note (ledger `B1`, repair `R1`): an unchanged
        revision at a new generation head is *not* refused categorically
        any more -- that over-broad reading is exactly what made
        `record_bundle_generation(..., outcome="same_content")` and
        `/recover-implementation-provenance` unable to publish. It is
        refused *here* because this fixture records no
        `Workflow-Bundle-Generation-Record` commit for the new head at
        all, so the preflight's provenance-interval half never verified
        it. The legitimate counterpart -- a real recovered-role commit,
        built through `record_bundle_generation` rather than by writing
        `WORKFLOW_STATE.json` directly -- is proven end to end by
        `workflow_acceptance_matrix_test.py` rows B3/B4/B5/C3/D1/G1, and
        this class's own
        `test_valid_provenance_interval_with_excluded_only_commit_succeeds`
        already covers the interval half."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            script_path = self._install_scripts(repo)
            self._generate_first_round(repo, work_item_id, script_path)

            impl_head_2 = repo.commit("second implementation change", filename="impl.txt")
            self._write_review_request(repo, work_item_id, repo.base, impl_head_2)
            self._write_state_entry(repo, work_item_id, base=repo.base, head=impl_head_2, revision=1)

            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GPT-R43-001", result.stderr)
            self.assertIn("implementation_revision", result.stderr)

    def test_new_head_with_correctly_advanced_revision_succeeds(self):
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            script_path = self._install_scripts(repo)
            self._generate_first_round(repo, work_item_id, script_path)

            impl_head_2 = repo.commit("second implementation change", filename="impl.txt")
            self._write_review_request(repo, work_item_id, repo.base, impl_head_2, implementation_revision=2)
            self._write_state_entry(repo, work_item_id, base=repo.base, head=impl_head_2, revision=2)

            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_text = (
                repo.root / ".ai-review" / work_item_id / "current" / "MANIFEST.md"
            ).read_text()
            self.assertIn(f"reviewed_implementation_head: {impl_head_2}", manifest_text)
            self.assertIn("implementation_revision: 2", manifest_text)

    def test_new_head_with_revision_jump_greater_than_one_refuses(self):
        """The finding's own "exactly one" requirement: a new head must
        advance the revision by precisely 1, not 2 -- the exact mistake a
        session that calls `record_bundle_generation` twice before its
        first commit can make."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            script_path = self._install_scripts(repo)
            self._generate_first_round(repo, work_item_id, script_path)

            impl_head_2 = repo.commit("second implementation change", filename="impl.txt")
            self._write_review_request(repo, work_item_id, repo.base, impl_head_2)
            self._write_state_entry(repo, work_item_id, base=repo.base, head=impl_head_2, revision=3)

            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GPT-R43-001", result.stderr)

    def test_repeat_generation_of_same_round_is_idempotent(self):
        """Regenerating for the exact same head the previous bundle
        already named (no new commit) must succeed without requiring an
        additional revision bump."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            script_path = self._install_scripts(repo)
            impl_head = self._generate_first_round(repo, work_item_id, script_path)

            # Re-run for the identical head/revision -- nothing changed.
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_text = (
                repo.root / ".ai-review" / work_item_id / "current" / "MANIFEST.md"
            ).read_text()
            self.assertIn(f"reviewed_implementation_head: {impl_head}", manifest_text)
            self.assertIn("implementation_revision: 1", manifest_text)

    def test_repeat_generation_with_stale_revision_bump_refuses(self):
        """The idempotent case's own negative half: regenerating for the
        *same* head but with implementation_revision changed anyway must
        still refuse -- a same-head regeneration is never itself a reason
        to advance the revision."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            script_path = self._install_scripts(repo)
            impl_head = self._generate_first_round(repo, work_item_id, script_path)

            self._write_state_entry(repo, work_item_id, base=repo.base, head=impl_head, revision=2)
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GPT-R43-001", result.stderr)

    def test_refused_regeneration_leaves_current_byte_identical(self):
        """GPT-R43-003: a refused regeneration attempt must leave the
        previously valid `current/` bundle byte-identical -- `current/` is
        "the bundle currently under review" (REVIEW_PROTOCOL.md), not a
        scratch directory a rejected generation may leave mutated."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            script_path = self._install_scripts(repo)
            self._generate_first_round(repo, work_item_id, script_path)

            impl_head_2 = repo.commit("second implementation change", filename="impl.txt")
            self._write_review_request(repo, work_item_id, repo.base, impl_head_2)
            # Stale revision -- refused, same as test_new_head_with_stale_revision_refuses.
            self._write_state_entry(repo, work_item_id, base=repo.base, head=impl_head_2, revision=1)

            # Snapshot after this round's own author-prep (REVIEW_REQUEST.md
            # is legitimately author-edited before every invocation, same
            # as a real round) but before invoking the script -- isolates
            # what the *script itself* does to current/ (and the archive
            # already produced by round 1) on refusal.
            bundle_dir = repo.root / ".ai-review" / work_item_id / "current"
            archive = repo.root / ".ai-review" / work_item_id / "review-bundle.tar.gz"
            self.assertTrue(archive.is_file())  # round 1 already produced it
            before = {
                p.relative_to(bundle_dir): p.read_bytes()
                for p in bundle_dir.rglob("*") if p.is_file()
            }
            before_archive = archive.read_bytes()

            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)

            after = {
                p.relative_to(bundle_dir): p.read_bytes()
                for p in bundle_dir.rglob("*") if p.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual(before_archive, archive.read_bytes())

    def test_interrupted_archive_write_leaves_previous_archive_intact(self):
        """Item 318 (WF8c scope clause (j), `OPUS-R102-011`): an
        interrupted or failed archive write must never replace the
        previously valid `review-bundle.tar.gz` -- unlike
        `test_refused_regeneration_leaves_current_byte_identical` above
        (which exercises a *preflight* refusal, before the script ever
        reaches the archive step), this exercises a failure *during* the
        archive step itself, with every earlier guard satisfied. A fake
        `tar` shadowing the real one on `PATH` writes garbage to its
        destination argument and exits non-zero, simulating a real
        interrupted/failed write (e.g. disk full, killed process)
        independently of any actual disk condition. Before the
        temp-file-plus-rename fix this reproducibly corrupts
        `review-bundle.tar.gz` in place; the fix's rename step is only
        reached once `tar` itself has already succeeded, so a failing
        `tar` never touches the real archive path at all."""
        with h.ScratchRepo() as repo:
            work_item_id = "wi"
            script_path = self._install_scripts(repo)
            self._generate_first_round(repo, work_item_id, script_path)

            archive = repo.root / ".ai-review" / work_item_id / "review-bundle.tar.gz"
            before_archive = archive.read_bytes()

            impl_head_2 = repo.commit("second implementation change", filename="impl.txt")
            self._write_review_request(repo, work_item_id, repo.base, impl_head_2)
            self._write_state_entry(repo, work_item_id, base=repo.base, head=impl_head_2, revision=2)

            fake_bin = repo.root / "fake-bin"
            fake_bin.mkdir()
            fake_tar = fake_bin / "tar"
            fake_tar.write_text(
                "#!/bin/sh\n"
                "# Simulates an interrupted/failed tar invocation: partially\n"
                "# writes to its destination argument, then fails.\n"
                'out="$2"\n'
                'printf \'CORRUPTED-INTERRUPTED-TAR-OUTPUT\' > "$out"\n'
                "exit 1\n"
            )
            fake_tar.chmod(fake_tar.stat().st_mode | stat.S_IEXEC)

            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
            result = subprocess.run(
                ["bash", str(script_path), repo.base, "post-fix", work_item_id],
                cwd=repo.root, capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before_archive, archive.read_bytes())

    def test_item_341_archive_command_never_interpolates_a_variable_positional_argument(self):
        """Item 341 (`WF8c`): its own token-grammar concern -- a malformed
        `staging/<token>/`-derived directory name reaching `tar` as a
        leading-dash/flag-shaped positional argument -- is a property of
        the `staging/<token>/` -> `bundles/<token>/` -> `current` symlink
        design the reconciliation table's own `314-338` row (`SUPERSEDED`)
        retires: "the token-grammar half of these items retires with it...
        `WF8c` owes the `assert_local_generation_matches` three-caller/
        `require_metadata` half only" (`339-344` row rationale). The live,
        non-superseded design (`REVIEW_PROTOCOL.md`'s flat `current/`
        directory) has no token and no symlink at all, so there is nothing
        for a token-grammar check to validate -- confirmed here directly
        against the installed script's own source rather than assumed: the
        archive step's `tar` invocation passes the bare string literal
        `current` as its positional directory argument, never a shell
        variable interpolation of `work_item_id`, a generated token, or
        any other caller-influenced value, so no value this repository's
        callers control ever reaches that argument position the way the
        superseded design's token would have. A regression guard: if a
        future change reintroduces a variable positional argument here,
        this assertion catches it before item 341's retired vulnerability
        class could reappear in a new form."""
        with h.ScratchRepo() as repo:
            script_path = self._install_scripts(repo)
            script_text = script_path.read_text()
            tar_lines = [line for line in script_text.splitlines() if line.strip().startswith("tar ")]
            self.assertEqual(len(tar_lines), 1, f"expected exactly one tar invocation, found {tar_lines!r}")
            tar_line = tar_lines[0].strip()
            self.assertTrue(
                tar_line.endswith(" current"),
                f"tar invocation must end with the bare literal 'current', got: {tar_line!r}",
            )
            self.assertNotIn("$", tar_line.rsplit(" ", 1)[-1])


class TestBundleRelocation(unittest.TestCase):
    """Missing-test item 165's relocation/migration sub-cases
    (`OPUS-R27-003`, `OPUS-R28-004`/`-006`): `relocate_flat_bundle_to_scoped_layout`
    and its `verify_relocation_file_set_complete` post-move check.

    Exercised against a bundle fixture built entirely inside the test's own
    temporary directory, never against this repository's own (gitignored)
    `.ai-review/` state: a clean checkout -- exactly what runs this
    module's committed CI job -- has no such directory, so reading it here
    made the "hermetic" suite this module's own header docstring promises
    fail outside a developer worktree that happened to hold a live bundle
    (`GPT-R34-001`). The fixture still has real, non-trivial byte content
    (its `MANIFEST.md` is rendered through the production
    `render_manifest_md` helper) and a nested `files/` subtree, so
    relocation exercises the same directory shape a real bundle has --
    item 165 needs realistic content shape here, not byte-identity with
    any specific real bundle."""

    def _build_synthetic_bundle_fixture(self, dest_repo_root: Path) -> Path:
        flat_dir = dest_repo_root / ".ai-review" / "current"
        files_dir = flat_dir / "files" / "scripts"
        files_dir.mkdir(parents=True)
        manifest_content = fingerprint.render_manifest_md(
            review_content_id="a" * 64,
            protected=frozenset({"scripts/example.py"}),
            excluded_paths={},
            excluded_prefixes={},
            bundle_id="b" * 64,
            work_item_id="second-item",
            work_item_type="process",
            plan_revision=1,
            base_commit="c" * 40,
        )
        (flat_dir / "MANIFEST.md").write_text(manifest_content)
        for name in sorted(fingerprint.REQUIRED_GENERATION_FILES):
            (flat_dir / name).write_text(f"synthetic {name} content for relocation fixture\n")
        (files_dir / "example.py").write_text("print('synthetic bundle fixture content')\n")
        (files_dir / "another.py").write_text("VALUE = 42\n")

        archive_path = dest_repo_root / ".ai-review" / "review-bundle.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(flat_dir, arcname="current")
        return flat_dir

    def test_relocation_succeeds_and_verifies_complete_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai-review").mkdir()
            flat_dir = self._build_synthetic_bundle_fixture(root)
            source_files = {p.relative_to(flat_dir).as_posix() for p in flat_dir.rglob("*") if p.is_file()}
            self.assertTrue(source_files, "the synthetic bundle fixture must be non-empty")

            fingerprint.relocate_flat_bundle_to_scoped_layout(root, "second-item")

            self.assertFalse(flat_dir.exists(), "the flat source directory must be gone after relocation")
            dest_dir = root / ".ai-review" / "second-item" / "current"
            dest_files = {p.relative_to(dest_dir).as_posix() for p in dest_dir.rglob("*") if p.is_file()}
            self.assertEqual(source_files, dest_files)
            self.assertTrue((root / ".ai-review" / "second-item" / "review-bundle.tar.gz").is_file())

    def test_relocation_refuses_when_destination_already_exists_non_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai-review").mkdir()
            self._build_synthetic_bundle_fixture(root)
            dest_dir = root / ".ai-review" / "second-item" / "current"
            dest_dir.mkdir(parents=True)
            (dest_dir / "PRE_EXISTING.txt").write_text("already here\n")

            with self.assertRaises(fingerprint.BundleRelocationDestinationExistsError) as ctx:
                fingerprint.relocate_flat_bundle_to_scoped_layout(root, "second-item")
            self.assertIn(str(dest_dir), str(ctx.exception))
            # Refusal must be a hard stop before touching the source at all.
            self.assertTrue((root / ".ai-review" / "current").is_dir())
            self.assertEqual((dest_dir / "PRE_EXISTING.txt").read_text(), "already here\n")

    def test_partial_move_missing_file_is_caught_by_post_move_verification(self):
        # Simulates an interrupted move directly against the real
        # verification function, rather than trying to interrupt
        # shutil.move mid-flight: a destination missing one file the
        # pre-move snapshot named.
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "dest"
            dest_dir.mkdir()
            (dest_dir / "a.txt").write_bytes(b"a")
            source_snapshot = {
                "a.txt": hashlib.sha256(b"a").hexdigest(),
                "b.txt": hashlib.sha256(b"b").hexdigest(),
            }
            with self.assertRaises(fingerprint.BundleRelocationPartialMoveError) as ctx:
                fingerprint.verify_relocation_file_set_complete(source_snapshot, dest_dir)
            self.assertIn("b.txt", str(ctx.exception))

    def test_partial_move_content_mismatch_is_also_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "dest"
            dest_dir.mkdir()
            (dest_dir / "a.txt").write_bytes(b"corrupted")
            source_snapshot = {"a.txt": hashlib.sha256(b"original").hexdigest()}
            with self.assertRaises(fingerprint.BundleRelocationPartialMoveError) as ctx:
                fingerprint.verify_relocation_file_set_complete(source_snapshot, dest_dir)
            self.assertIn("a.txt", str(ctx.exception))

    def test_complete_move_verification_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "dest"
            dest_dir.mkdir()
            (dest_dir / "a.txt").write_bytes(b"same")
            source_snapshot = {"a.txt": hashlib.sha256(b"same").hexdigest()}
            fingerprint.verify_relocation_file_set_complete(source_snapshot, dest_dir)  # must not raise



class TestPrepareAiReviewShPlanStageStaging(unittest.TestCase):
    """workflow-2.6.0 CP4 (`D-Plan-Review-Bundle-Binding` item 5, the
    revised `WFR-67`): the plan stage assembles into
    `.ai-review/<id>/current.staging-<token>/` with a staging pin and
    archive, reads its author inputs from `plan-inputs/`, and renames into
    place only after the closing checks pass. A failed generation discards
    the staging area and leaves the previous `current/`, archive and
    `.pin` byte-identical -- no withdrawal, no `REJECTED` marker. Driven
    end to end through the real `prepare-ai-review.sh`."""

    ITEM = "stage-item"

    def _install_scripts(self, repo):
        scripts_dir = repo.root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for name in ("prepare-ai-review.sh", "workflow_fingerprint.py", "workflow_state.py", "workflow_gate_policy.py", "workflow_forge.py"):
            shutil.copy(_REAL_SCRIPTS_DIR / name, scripts_dir / name)
        script_path = scripts_dir / "prepare-ai-review.sh"
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return script_path

    def _seed(self, repo):
        script_path = self._install_scripts(repo)
        _write_second_item(repo, self.ITEM)
        repo.commit_plan_docs_as_base()
        state_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
        state = json.loads(state_path.read_text())
        state["work_items"][self.ITEM]["base_commit"] = repo.base
        state_path.write_text(json.dumps(state))
        subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fix declared base_commit"], cwd=repo.root, check=True)
        return script_path

    def _item_root(self, repo):
        return repo.root / ".ai-review" / self.ITEM

    def _write_inputs(self, repo, *, where="plan-inputs", test_results=None, review_request=None):
        target = self._item_root(repo) / where
        target.mkdir(parents=True, exist_ok=True)
        digest, _ = fingerprint.compute_review_content_id_plan_stage_for_work_item(repo.root, self.ITEM)
        _, head = fingerprint.current_worktree_root_and_head(repo.root)
        (target / "REVIEW_REQUEST.md").write_text(
            review_request if review_request is not None else f"stage: plan\nreview_content_id: {digest}\n"
        )
        (target / "TEST_RESULTS.md").write_text(
            test_results if test_results is not None else f"stage: plan (revision 1)\nhead: {head}\n"
        )
        (target / "CONTEXT_FILES.txt").write_text("")
        return target

    def _run(self, repo, script_path):
        return subprocess.run(
            ["bash", str(script_path), repo.base, "plan", self.ITEM],
            cwd=repo.root, capture_output=True, text=True,
        )

    def _tree_digest(self, path: Path) -> dict:
        if path.is_file():
            return {"": hashlib.sha256(path.read_bytes()).hexdigest()}
        return {
            p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file()
        }

    def _published_snapshot(self, repo):
        root = self._item_root(repo)
        return {
            name: self._tree_digest(root / name)
            for name in ("current", "review-bundle.tar.gz", ".pin")
        }

    def _assert_no_staging_left(self, repo):
        names = sorted(p.name for p in self._item_root(repo).iterdir())
        leftovers = [n for n in names if n.startswith(("current.staging-", ".pin.staging-", "current.old-", ".pin.old-"))]
        self.assertEqual(leftovers, [], names)

    def _assert_bundle_self_consistent(self, repo):
        bundle = self._item_root(repo) / "current"
        recorded = fingerprint.read_manifest_identifiers(bundle / "MANIFEST.md")["bundle_id"]
        self.assertEqual(fingerprint.compute_bundle_id(bundle)[0], recorded)
        self.assertEqual(
            fingerprint.compute_archived_bundle_id(self._item_root(repo) / "review-bundle.tar.gz"), recorded,
        )

    def test_success_promotes_the_staging_generation_from_plan_inputs(self):
        with h.ScratchRepo() as repo:
            script_path = self._seed(repo)
            inputs = self._write_inputs(repo)
            before_inputs = self._tree_digest(inputs)
            result = self._run(repo, script_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("current.staging-", result.stdout)
            bundle = self._item_root(repo) / "current"
            self.assertEqual((bundle / "REVIEW_REQUEST.md").read_bytes(), (inputs / "REVIEW_REQUEST.md").read_bytes())
            self.assertEqual((bundle / "TEST_RESULTS.md").read_bytes(), (inputs / "TEST_RESULTS.md").read_bytes())
            self.assertEqual((bundle / "IMPLEMENTATION_SUMMARY.md").read_bytes(), b"")
            self.assertTrue((self._item_root(repo) / ".pin").is_dir())
            self.assertEqual(self._tree_digest(inputs), before_inputs, "plan-inputs/ is never written")
            self._assert_bundle_self_consistent(repo)
            self._assert_no_staging_left(repo)

    def test_a_failed_generation_leaves_the_previous_bundle_byte_identical(self):
        """Section 3.3's two variants: a stale `TEST_RESULTS.md` (fails at
        finalization) and a stale `REVIEW_REQUEST.md` (fails inside
        `--write-manifest`, before finalization -- the variant that used to
        leave a mixed bundle)."""
        for variant in ("stale TEST_RESULTS.md", "stale REVIEW_REQUEST.md"):
            with self.subTest(variant=variant), h.ScratchRepo() as repo:
                script_path = self._seed(repo)
                self._write_inputs(repo)
                self.assertEqual(self._run(repo, script_path).returncode, 0)
                before = self._published_snapshot(repo)
                plan_path = repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md"
                plan_path.write_text(plan_path.read_text() + "an edit the next round publishes\n")
                if variant == "stale TEST_RESULTS.md":
                    self._write_inputs(repo, test_results="stage: implementation\n")
                else:
                    self._write_inputs(repo, review_request="stage: plan\nreview_content_id: " + "0" * 64 + "\n")
                result = self._run(repo, script_path)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._published_snapshot(repo), before)
                self.assertFalse((self._item_root(repo) / "REJECTED").exists())
                self.assertEqual(list(self._item_root(repo).glob("current.rejected-*")), [])
                self._assert_no_staging_left(repo)
                fingerprint.assert_bundle_not_rejected(repo.root, self.ITEM)

    def test_a_pre_existing_rejected_marker_survives_failure_and_is_cleared_by_success(self):
        with h.ScratchRepo() as repo:
            script_path = self._seed(repo)
            self._write_inputs(repo)
            self.assertEqual(self._run(repo, script_path).returncode, 0)
            marker = self._item_root(repo) / "REJECTED"
            marker.write_text("REJECTED: left by an earlier withdrawal\n")
            self._write_inputs(repo, test_results="")
            self.assertNotEqual(self._run(repo, script_path).returncode, 0)
            with self.assertRaises(fingerprint.BundleRejectedError):
                fingerprint.assert_bundle_not_rejected(repo.root, self.ITEM)
            self._write_inputs(repo)
            self.assertEqual(self._run(repo, script_path).returncode, 0)
            self.assertFalse(marker.exists())

    def test_inputs_seed_from_current_when_plan_inputs_has_none(self):
        """The migration path: an author who still writes into `current/`
        (read-only to the generator) gets exactly those bytes, and the
        resulting `bundle_id` equals the one the same bytes produce from
        `plan-inputs/` -- the bundle hashes content by relative path, never
        by where the author file came from (INV-8)."""
        with h.ScratchRepo() as repo:
            script_path = self._seed(repo)
            inputs = self._write_inputs(repo)
            self.assertEqual(self._run(repo, script_path).returncode, 0)
            from_inputs = fingerprint.read_manifest_identifiers(
                self._item_root(repo) / "current" / "MANIFEST.md")["bundle_id"]
            shutil.rmtree(inputs)
            self.assertEqual(self._run(repo, script_path).returncode, 0)
            from_current = fingerprint.read_manifest_identifiers(
                self._item_root(repo) / "current" / "MANIFEST.md")["bundle_id"]
            self.assertEqual(from_current, from_inputs)
            self._assert_bundle_self_consistent(repo)

    def test_seed_plan_review_inputs_sources_and_refusals(self):
        with h.ScratchRepo() as repo:
            _write_second_item(repo, self.ITEM)
            item_root = self._item_root(repo)
            (item_root / "plan-inputs").mkdir(parents=True)
            (item_root / "current").mkdir()
            (item_root / "plan-inputs" / "REVIEW_REQUEST.md").write_text("from inputs\n")
            (item_root / "current" / "REVIEW_REQUEST.md").write_text("from current\n")
            (item_root / "current" / "TEST_RESULTS.md").write_text("seeded\n")
            script = item_root / "current" / "CONTEXT_FILES.txt"
            script.write_text("")
            script.chmod(0o755)
            dest = Path(tempfile.mkdtemp(prefix="wf-seed-"))
            self.addCleanup(shutil.rmtree, dest, True)
            sources = fingerprint.seed_plan_review_inputs(repo.root, self.ITEM, dest)
            self.assertEqual(sources, {
                "REVIEW_REQUEST.md": "plan-inputs", "TEST_RESULTS.md": "current",
                "CONTEXT_FILES.txt": "current", "IMPLEMENTATION_SUMMARY.md": "stub",
            })
            self.assertEqual((dest / "REVIEW_REQUEST.md").read_text(), "from inputs\n")
            self.assertEqual((dest / "TEST_RESULTS.md").read_text(), "seeded\n")
            self.assertTrue(os.access(dest / "CONTEXT_FILES.txt", os.X_OK), "the executable bit is hashed")
            self.assertEqual((dest / "IMPLEMENTATION_SUMMARY.md").read_bytes(), b"")
            self.assertEqual((item_root / "current" / "REVIEW_REQUEST.md").read_text(), "from current\n")
            (item_root / "plan-inputs" / "TEST_RESULTS.md").symlink_to(item_root / "current" / "TEST_RESULTS.md")
            with self.assertRaises(fingerprint.PlanReviewInputPathError):
                fingerprint.seed_plan_review_inputs(repo.root, self.ITEM, dest)

    def test_leftovers_of_an_interrupted_generation_are_removed_by_the_next(self):
        with h.ScratchRepo() as repo:
            script_path = self._seed(repo)
            self._write_inputs(repo)
            item_root = self._item_root(repo)
            for name in ("current.staging-" + "a" * 32, ".pin.staging-" + "a" * 32, "current.old-" + "b" * 32):
                (item_root / name / "current").mkdir(parents=True)
            self.assertEqual(self._run(repo, script_path).returncode, 0)
            self._assert_no_staging_left(repo)

    def test_staging_paths_validate_the_token(self):
        with h.ScratchRepo() as repo:
            for bad in ("", "../x", "A" * 32, "a" * 31):
                with self.subTest(token=bad), self.assertRaises(fingerprint.InvalidStagingTokenError):
                    fingerprint.plan_stage_staging_paths(repo.root, self.ITEM, bad)
            paths = fingerprint.plan_stage_staging_paths(repo.root, self.ITEM, "f" * 32)
            self.assertEqual(paths["bundle_dir"].name, "current")
            self.assertEqual(paths["root"].parent, paths["pin_dir"].parent)

    def test_resolve_plan_review_inputs_dir_is_a_sibling_of_current(self):
        with h.ScratchRepo() as repo:
            self.assertEqual(
                fingerprint.resolve_plan_review_inputs_dir(repo.root, self.ITEM),
                Path(".ai-review") / self.ITEM / "plan-inputs",
            )
            self.assertEqual(
                fingerprint.resolve_plan_review_inputs_dir(repo.root, self.ITEM).parent,
                fingerprint.resolve_bundle_dir(repo.root, self.ITEM, stage="plan").parent,
            )


if __name__ == "__main__":
    unittest.main()
