#!/usr/bin/env python3
"""Hermetic self-verification of `workflow_test_harness.py` (`WF8a-i`,
`WFR-36`: "WF8a-i's fixture harness scaffolding is itself verifiable").

Every fixture the harness documents is proven here against the *real*
production functions in `workflow_fingerprint.py`/`workflow_state.py` --
not merely asserted to look right by inspection -- so a future consumer
(starting with `WF8a-ii`) can rely on the harness's own docstring being
accurate. There is no `_demo_test.py` counterpart: this module makes no
claims about this repository's real state, only about its own fixtures.

Stdlib-only. Run: python3 scripts/workflow_test_harness_test.py
"""

from __future__ import annotations

import unittest

import workflow_fingerprint as fingerprint
import workflow_state as ws
import workflow_test_harness as h


class TestScratchRepoBasics(unittest.TestCase):
    def test_base_commit_is_head_on_entry(self):
        with h.ScratchRepo() as repo:
            self.assertEqual(repo.base, repo.head())

    def test_each_use_gets_an_independent_directory(self):
        with h.ScratchRepo() as repo_a, h.ScratchRepo() as repo_b:
            self.assertNotEqual(repo_a.root, repo_b.root)
            self.assertTrue(repo_a.root.is_dir())
            self.assertTrue(repo_b.root.is_dir())

    def test_directory_is_removed_on_exit(self):
        with h.ScratchRepo() as repo:
            root = repo.root
        self.assertFalse(root.exists())


class TestScratchRepoCommitTrailers(unittest.TestCase):
    def test_commit_trailers_are_discovered_by_the_real_trailer_search(self):
        """Proves the harness's own commit() output is exactly what
        workflow_state.py's real production trailer-discovery mechanism
        expects -- the harness fixture and the module under test agree,
        not just the harness's own idea of a trailer."""
        with h.ScratchRepo() as repo:
            sha = repo.commit("wf0", trailers={"Workflow-Checkpoint": "WF0", "Workflow-Work-Item": "wi"})
            discovered = ws.discover_checkpoint_commits(repo.root, "wi", repo.base)
            self.assertEqual(discovered, {"WF0": sha})

    def test_commit_without_trailers_is_not_discovered(self):
        with h.ScratchRepo() as repo:
            repo.commit("untrailered")
            discovered = ws.discover_checkpoint_commits(repo.root, "wi", repo.base)
            self.assertEqual(discovered, {})

    def test_successive_commits_each_advance_head(self):
        with h.ScratchRepo() as repo:
            first = repo.commit("one")
            second = repo.commit("two")
            self.assertNotEqual(first, second)
            self.assertEqual(repo.head(), second)


class TestScratchRepoPlanDocs(unittest.TestCase):
    def test_plan_docs_round_trip_through_the_real_plan_stage_identity_function(self):
        """Proves write_plan_docs/commit_plan_docs_as_base produce a tree
        workflow_fingerprint.compute_review_content_id_plan_stage genuinely
        accepts -- not merely files at plausible-looking paths."""
        with h.ScratchRepo() as repo:
            repo.write_plan_docs(work_item_id="wi")
            repo.commit_plan_docs_as_base()
            digest, projection = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi", plan_revision=1,
                protected=h.plan_stage_protected_paths("wi"),
                excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            self.assertEqual(len(digest), 64)
            self.assertEqual(projection["plan_revision"], 1)

    def test_plan_stage_identity_is_idempotent_over_the_fixture(self):
        with h.ScratchRepo() as repo:
            repo.write_plan_docs(work_item_id="wi")
            repo.commit_plan_docs_as_base()
            protected = h.plan_stage_protected_paths("wi")
            first, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi", plan_revision=1,
                protected=protected, excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            second, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi", plan_revision=1,
                protected=protected, excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            self.assertEqual(first, second)

    def test_a_plan_doc_edit_after_settling_changes_the_identity(self):
        """A one-byte edit to a protected fixture file changes the
        identity -- proves the fixture's protected files are genuinely
        load-bearing for the function under test, not inert placeholders."""
        with h.ScratchRepo() as repo:
            repo.write_plan_docs(work_item_id="wi")
            repo.commit_plan_docs_as_base()
            protected = h.plan_stage_protected_paths("wi")
            before, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi", plan_revision=1,
                protected=protected, excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            (repo.root / "docs" / "ai-workflow" / "WORKFLOW_V2_PLAN.md").write_text("plan v2\n")
            after, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process", work_item_id="wi", plan_revision=1,
                protected=protected, excluded_paths=h.plan_stage_excluded_paths(),
                excluded_prefixes=h.plan_stage_excluded_prefixes(),
            )
            self.assertNotEqual(before, after)

    def test_workflow_v2_1_core_itself_needs_no_protected_override(self):
        """The one work_item_id whose real PLAN_STAGE_PROTECTED default
        already matches the fixture's own paths, proving the override is
        only needed for a *different* work_item_id, not universally."""
        with h.ScratchRepo() as repo:
            repo.write_plan_docs(work_item_id="workflow-v2-1-core")
            repo.commit_plan_docs_as_base()
            digest, _ = fingerprint.compute_review_content_id_plan_stage(
                repo.root, repo.base, work_item_type="process",
                work_item_id="workflow-v2-1-core", plan_revision=1,
                protected=fingerprint.PLAN_STAGE_PROTECTED,
                excluded_paths=fingerprint.PLAN_STAGE_EXCLUDED_PATHS,
                excluded_prefixes=fingerprint.PLAN_STAGE_EXCLUDED_PREFIXES,
            )
            self.assertEqual(len(digest), 64)


class TestBaseWorkItemAndState(unittest.TestCase):
    def test_base_work_item_alone_is_schema_valid(self):
        ws.validate_state(h.base_state(wi=h.base_work_item()))  # must not raise

    def test_base_work_item_overrides_replace_fields(self):
        work_item = h.base_work_item(phase="AWAITING_PLAN_APPROVAL", work_item_id="custom")
        self.assertEqual(work_item["phase"], "AWAITING_PLAN_APPROVAL")
        self.assertEqual(work_item["work_item_id"], "custom")

    def test_base_state_with_multiple_work_items_is_schema_valid(self):
        state = h.base_state(
            a=h.base_work_item(work_item_id="a"),
            b=h.base_work_item(work_item_id="b"),
        )
        ws.validate_state(state)  # must not raise

    def test_dangling_parent_is_still_caught(self):
        """The builder does not itself validate -- it must still be
        possible to construct an invalid fixture with it, e.g. to test a
        validator's own rejection path."""
        state = h.base_state(wi=h.base_work_item(parent_work_item_id="missing"))
        with self.assertRaises(ws.DanglingParentWorkItemError):
            ws.validate_state(state)


class TestBaseRegistryAndMapping(unittest.TestCase):
    def test_empty_registry_and_mapping_are_valid(self):
        registry = h.base_registry()
        mapping = h.base_mapping()
        ws.validate_registry_topological_order(registry)  # must not raise
        ws.validate_registry_mapping_coverage(registry, mapping)  # must not raise

    def test_registry_with_satisfied_dependencies_is_valid(self):
        registry = h.base_registry(checkpoints=[
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
            {"id": "B", "name": "b", "depends_on": ["A"], "complexity": 1, "session_target": "1"},
        ])
        ws.validate_registry_topological_order(registry)  # must not raise

    def test_registry_can_construct_a_deliberately_invalid_order(self):
        """Proves the builder is also usable to build the negative
        fixtures a validator-rejection test needs."""
        registry = h.base_registry(checkpoints=[
            {"id": "B", "name": "b", "depends_on": ["A"], "complexity": 1, "session_target": "1"},
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
        ])
        with self.assertRaises(ws.NonTopologicalRegistryOrderError):
            ws.validate_registry_topological_order(registry)

    def test_mapping_covering_every_checkpoint_passes_coverage(self):
        registry = h.base_registry(checkpoints=[
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
        ])
        mapping = h.base_mapping(requirements={"R-1": {"description": "d", "checkpoint_ids": ["A"]}})
        ws.validate_registry_mapping_coverage(registry, mapping)  # must not raise

    def test_unowned_checkpoint_is_still_caught(self):
        registry = h.base_registry(checkpoints=[
            {"id": "A", "name": "a", "depends_on": [], "complexity": 1, "session_target": "1"},
        ])
        mapping = h.base_mapping()
        with self.assertRaises(ws.UnownedCheckpointError):
            ws.validate_registry_mapping_coverage(registry, mapping)



class TestBundleFixtures(unittest.TestCase):
    """workflow-2.7.0: the real-bundle fixtures produce what the production
    verifiers accept."""

    def test_a_published_and_bound_plan_bundle_verifies(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="PLANNING")
            review_content_id, bundle_id = h.publish_and_bind_plan_bundle(repo)
            state = h.read_state(repo)
            self.assertEqual(state["work_items"]["wi"]["phase"], "AWAITING_LOCAL_PLAN_REVIEW")
            binding = ws.verify_plan_review_bundle(repo.root, "wi")
            self.assertEqual((binding["review_content_id"], binding["bundle_id"]), (review_content_id, bundle_id))

    def test_an_implementation_bundle_verifies_and_its_record_commit_is_found(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="SELF_REVIEWING_IMPLEMENTATION")
            repo.commit("implement", filename=h.BUNDLE_ITEM_IMPLEMENTATION_PATH)
            review_content_id = h.generate_implementation_bundle(repo)
            self.assertEqual(ws.verify_implementation_review_bundle(repo.root, "wi")["review_content_id"],
                             review_content_id)
            work_item = h.read_state(repo)["work_items"]["wi"]
            self.assertTrue(ws.implementation_provenance_interval_reachable(repo.root, work_item, repo.base))

    def test_an_approved_plan_is_an_implementing_entry(self):
        with h.ScratchRepo() as repo:
            h.seed_bundle_item(repo, phase="IMPLEMENTING")
            h.approve_plan(repo)
            work_item = h.read_state(repo)["work_items"]["wi"]
            self.assertTrue(ws.implementing_entry_reachable(repo.root, work_item, repo.base))

if __name__ == "__main__":
    unittest.main()
