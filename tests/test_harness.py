"""The harness adapter registry (workflow-controller-adapters-1 CP1)."""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

from controller import harness, review, review_mode, telemetry
from controller.errors import ControllerError, HarnessRoleError, UnknownHarnessError

ROOT = Path(__file__).resolve().parent.parent


class DeclaredFactsTest(unittest.TestCase):
    def test_claude_code(self) -> None:
        a = harness.ADAPTERS["claude-code"]
        self.assertEqual(
            (a.name, a.reviewer_name, a.family, a.roles, a.can_restrict_agents, a.can_restrict_tools, a.usage, a.pinned_version, a.fails_closed_on_unknown_version),
            ("claude-code", "claude", "anthropic", ("worker", "reviewer"), True, True, "reported", "2.1.296 (Claude Code)", True),
        )

    def test_codex(self) -> None:
        a = harness.ADAPTERS["codex"]
        self.assertEqual(
            (a.name, a.reviewer_name, a.family, a.roles, a.usage, a.pinned_version, a.fails_closed_on_unknown_version),
            ("codex", "codex", "openai", ("reviewer",), "derived", "codex-cli 0.162.1", True),
        )

    def test_registry_is_closed(self) -> None:
        self.assertEqual(set(harness.ADAPTERS), {"claude-code", "codex"})

    def test_adapter_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            harness.CODEX.family = "x"  # type: ignore[misc]


class DerivedValuesTest(unittest.TestCase):
    def test_derived_equal_old_literals(self) -> None:
        self.assertEqual(dict(review_mode.HARNESS_FAMILY), {"codex": "openai", "claude": "anthropic"})
        self.assertEqual(telemetry.HARNESS, "claude-code")


class PinnedDeclarationsTest(unittest.TestCase):
    def test_reviewer_names_equal_review_harnesses(self) -> None:
        self.assertEqual(set(review.HARNESSES), set(harness.REVIEWER_FAMILY))

    def test_verdict_family_pinned(self) -> None:
        for name, constructed in (("codex", review.CODEX), ("claude", review.CLAUDE)):
            with self.subTest(name):
                self.assertEqual(constructed.verdict_family, harness.get(name).family, f"{name}: verdict_family drifted from the adapter")

    def test_pinned_versions(self) -> None:
        self.assertEqual(review.PINNED_VERSION, harness.get("codex").pinned_version)
        self.assertEqual(review.CLAUDE_PINNED_VERSION, harness.get("claude").pinned_version)

    def test_worker_declarations(self) -> None:
        worker = harness.worker_adapter()
        self.assertEqual(review_mode.WORKER_FAMILY, worker.family)
        self.assertEqual(review_mode.SAME_PROVIDER_HARNESS, worker.reviewer_name)


class LookupTest(unittest.TestCase):
    def test_two_names_one_adapter(self) -> None:
        self.assertIs(harness.get("claude-code"), harness.get("claude"))

    def test_missing_role_is_typed(self) -> None:
        with self.assertRaises(HarnessRoleError) as ctx:
            harness.for_role("codex", "worker")
        self.assertIsInstance(ctx.exception, ControllerError)
        self.assertEqual(ctx.exception.evidence, {"harness": "codex", "role": "worker", "declared_roles": ["reviewer"]})
        self.assertIn("codex", ctx.exception.message)

    def test_declared_role_passes(self) -> None:
        self.assertIs(harness.for_role("claude-code", "worker"), harness.CLAUDE_CODE)

    def test_unknown_name(self) -> None:
        with self.assertRaises(UnknownHarnessError) as ctx:
            harness.get("gemini")
        self.assertIsInstance(ctx.exception, ControllerError)
        self.assertEqual(ctx.exception.evidence["registered"], ["claude-code", "codex"])


class ImportSetTest(unittest.TestCase):
    def test_imports_only_errors(self) -> None:
        tree = ast.parse((ROOT / "controller" / "harness.py").read_text())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("controller"):
                found.add(node.module)
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names if a.name.startswith("controller"))
        self.assertEqual(found, {"controller.errors"})

    def test_each_dependent_imports_first_in_a_fresh_interpreter(self) -> None:
        for module in ("controller.review_mode", "controller.telemetry", "controller.review"):
            with self.subTest(module):
                done = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
