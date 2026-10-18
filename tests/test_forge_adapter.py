"""``controller.forge`` git host adapter registry (workflow-controller-
adapters-1 CP2, D5)."""

from __future__ import annotations

import dataclasses
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import forge, milestone_branch, repo_policy  # noqa: E402
from controller.errors import ControllerError, ForgeKindUnregisteredError  # noqa: E402


def _context(**kw) -> milestone_branch.Context:
    return milestone_branch.Context(repo_root=Path("."), runtime_root=Path("."), **kw)


class ForgeRegistryTest(unittest.TestCase):
    def test_registry_content_and_capabilities(self) -> None:
        self.assertEqual(set(forge.FORGES), {"github"})
        github = forge.FORGES["github"]
        self.assertIsInstance(github.implementation("owner/repo"), forge.GhForge)
        self.assertEqual(github.kind, "github")
        self.assertTrue(all(dataclasses.astuple(github.capabilities)))

    def test_registry_keys_equal_the_policy_set(self) -> None:
        self.assertEqual(set(forge.FORGES), repo_policy.FORGE_KINDS)

    def test_capabilities_cover_the_protocol_methods(self) -> None:
        methods = {n for n, _ in inspect.getmembers(forge.Forge, inspect.isfunction) if not n.startswith("_")}
        covered = {
            "pull_requests": {"list_prs", "view_pr", "query_pull_requests", "repository_identity"},
            "draft_pull_requests": {"create_draft_pr", "mark_ready", "edit_pr"},
            "squash_merge": {"merge_squash"},
            "check_reports": {"pr_checks", "commit_runs"},
            "releases_with_assets": {"view_release", "create_release", "upload_assets", "publish_draft",
                                    "download_assets"},
        }
        self.assertEqual(set(covered), {f.name for f in dataclasses.fields(forge.ForgeCapabilities)})
        self.assertEqual(set().union(*covered.values()), methods)

    def test_adapter_for_unregistered_kind(self) -> None:
        with self.assertRaises(ForgeKindUnregisteredError) as cm:
            forge.adapter_for("gitlab")
        self.assertIsInstance(cm.exception, ControllerError)
        self.assertEqual(cm.exception.evidence, {"kind": "gitlab", "registered": ["github"]})
        self.assertIn("github", str(cm.exception))


class ContextForgeTest(unittest.TestCase):
    def test_default_context_builds_a_gh_forge(self) -> None:
        self.assertEqual(_context().forge_kind, "github")
        self.assertIsInstance(_context().forge("owner/name"), forge.GhForge)

    def test_unregistered_kind_refuses(self) -> None:
        with self.assertRaises(ForgeKindUnregisteredError) as cm:
            _context(forge_kind="gitlab").forge("owner/name")
        self.assertEqual(cm.exception.evidence["registered"], ["github"])

    def test_factory_wins_over_an_unregistered_kind(self) -> None:
        sentinel = object()
        ctx = _context(forge_kind="gitlab", forge_factory=lambda repository: sentinel)
        self.assertIs(ctx.forge("owner/name"), sentinel)


if __name__ == "__main__":
    unittest.main()
