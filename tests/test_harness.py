"""The harness adapter registry (workflow-controller-adapters-1 CP1)."""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

from controller import cli, functional_review, harness, review, review_mode, telemetry
from controller.errors import ControllerError, HarnessRoleError, UnknownHarnessError

ROOT = Path(__file__).resolve().parent.parent


class DeclaredFactsTest(unittest.TestCase):
    def test_claude_code(self) -> None:
        a = harness.ADAPTERS["claude-code"]
        self.assertEqual(
            (a.name, a.reviewer_name, a.family, a.roles, a.can_restrict_agents, a.can_restrict_tools, a.usage, a.pinned_version, a.fails_closed_on_unknown_version, a.build_verification),
            ("claude-code", "claude", "anthropic", ("worker", "reviewer", "functional-tester"), True, True, "reported", "2.1.296 (Claude Code)", True, "verify-claude"),
        )

    def test_codex(self) -> None:
        a = harness.ADAPTERS["codex"]
        self.assertEqual(
            (a.name, a.reviewer_name, a.family, a.roles, a.usage, a.pinned_version, a.fails_closed_on_unknown_version, a.build_verification),
            ("codex", "codex", "openai", ("reviewer", "functional-tester"), "derived", "codex-cli 0.163.0", True, "verify-codex"),
        )

    def test_registry_is_closed(self) -> None:
        self.assertEqual(set(harness.ADAPTERS), {"claude-code", "codex"})

    def test_adapter_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            harness.CODEX.family = "x"  # type: ignore[misc]


class DeclaredModelsAndEffortsTest(unittest.TestCase):
    def test_every_adapter_declares_models_and_efforts(self) -> None:
        for adapter in harness.ADAPTERS.values():
            with self.subTest(adapter=adapter.name):
                self.assertTrue(adapter.models)
                self.assertTrue(adapter.efforts)

    def test_claude_code_efforts_are_the_reviewer_efforts(self) -> None:
        self.assertEqual(harness.CLAUDE_CODE.efforts, review.CLAUDE_EFFORTS)

    def test_launch_defaults_are_declared_by_their_adapter(self) -> None:
        self.assertIn(review.DEFAULT_MODEL, harness.CODEX.models)
        self.assertIn(review.DEFAULT_EFFORT, harness.CODEX.efforts)
        self.assertIn(review.CLAUDE_DEFAULT_MODEL, harness.CLAUDE_CODE.models)
        self.assertIn(review.CLAUDE_DEFAULT_EFFORT, harness.CLAUDE_CODE.efforts)


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
        self.assertEqual(review_mode.worker_families(), frozenset({worker.family}))
        self.assertEqual(review_mode.SAME_PROVIDER_HARNESS, worker.reviewer_name)


class BuildVerificationTest(unittest.TestCase):
    def test_every_declared_command_is_a_controller_command(self) -> None:
        for adapter in harness.ADAPTERS.values():
            if adapter.build_verification is not None:
                with self.subTest(adapter=adapter.name):
                    self.assertIn(adapter.build_verification, cli._DISPATCH)

    def test_the_claude_command_is_the_one_the_refusal_names(self) -> None:
        with self.assertRaises(functional_review.TesterUnavailable) as ctx:
            functional_review.identity_check("/x/claude", is_native=lambda path: False)
        self.assertIn(f"`workflow-controller {harness.CLAUDE_CODE.build_verification}`", str(ctx.exception))

    def test_nothing_else_under_controller_reads_the_field(self) -> None:
        for path in sorted((ROOT / "controller").glob("*.py")):
            if path.name == "harness.py":
                continue
            with self.subTest(module=path.name):
                self.assertNotIn("build_verification", path.read_text())

    def test_the_command_admits_the_functional_tester_launch_only(self) -> None:
        # The statement in the declaration and the guides: only launch_functional
        # reads the recorded digests or runs the identity check.
        digest_readers, probe_callers = set(), set()
        for path in sorted((ROOT / "controller").glob("*.py")):
            if path.name in ("claude_build.py", "functional_review.py"):
                continue
            tree = ast.parse(path.read_text())
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and node.attr == "recorded_digests" and path.name != "cli.py":
                        digest_readers.add((path.name, fn.name))
                    if isinstance(node, ast.Attribute) and node.attr in ("identity_check", "static_probe"):
                        probe_callers.add((path.name, fn.name))
        self.assertEqual(digest_readers, {("review.py", "launch_functional")})
        self.assertEqual(probe_callers, {("review.py", "launch_functional")})


class LookupTest(unittest.TestCase):
    def test_two_names_one_adapter(self) -> None:
        self.assertIs(harness.get("claude-code"), harness.get("claude"))

    def test_missing_role_is_typed(self) -> None:
        with self.assertRaises(HarnessRoleError) as ctx:
            harness.for_role("codex", "worker")
        self.assertIsInstance(ctx.exception, ControllerError)
        self.assertEqual(ctx.exception.evidence, {"harness": "codex", "role": "worker", "declared_roles": ["reviewer", "functional-tester"]})
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
