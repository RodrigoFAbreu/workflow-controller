"""The worker launch behind the harness adapter (H3): the launcher interface,
the registry and the call site in ``controller/job.py``. No process starts."""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path
from unittest import mock

from controller import harness, worker, worker_launch
from controller.errors import HarnessRoleError

ROOT = Path(__file__).resolve().parent.parent

LAUNCH_KEYWORDS = (
    "cwd", "permission_mode", "timeout", "stdout_path", "stderr_path", "claude_bin", "pass_fds", "on_spawn",
    "on_group_drain", "model", "effort", "disallowed_tools", "ownership_tag", "supervisor_lock_path",
    "on_state_change", "drain_detach_seconds",
)


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _package_imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "controller":
                found.update(f"controller.{a.name}" for a in node.names)
            elif node.module.startswith("controller."):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("controller"))
    return found


class ClaudeCodeLauncherTest(unittest.TestCase):
    def test_worker_launch_is_looked_up_at_call_time(self) -> None:
        launcher = worker_launch.ClaudeCodeLauncher()
        sentinel = object()
        with mock.patch.object(worker, "launch", return_value=sentinel) as patched:
            self.assertIs(launcher.launch("task", cwd="/x"), sentinel)
        patched.assert_called_once_with("task", cwd="/x")

    def test_every_keyword_reaches_worker_launch_unchanged(self) -> None:
        options = {name: object() for name in LAUNCH_KEYWORDS}
        with mock.patch.object(worker, "launch") as patched:
            worker_launch.ClaudeCodeLauncher().launch("task", **options)
        patched.assert_called_once_with("task", **options)

    def test_the_call_site_passes_keywords_worker_launch_accepts(self) -> None:
        launch_job = _function(ast.parse((ROOT / "controller" / "job.py").read_text()), "_launch_job")
        calls = [
            n for n in ast.walk(launch_job)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "launch"
        ]
        self.assertEqual(len(calls), 1)
        passed = [k.arg for k in calls[0].keywords]
        self.assertEqual(passed, list(LAUNCH_KEYWORDS))
        self.assertTrue(set(passed) <= set(inspect.signature(worker.launch).parameters))


class RegistryTest(unittest.TestCase):
    def test_launchers_are_exactly_the_worker_role_adapters(self) -> None:
        workers = {a.name for a in harness.ADAPTERS.values() if harness.WORKER in a.roles}
        self.assertEqual(set(worker_launch.LAUNCHERS), workers)

    def test_the_adapter_name_and_its_reviewer_alias_resolve_alike(self) -> None:
        self.assertIs(worker_launch.for_harness("claude-code"), worker_launch.for_harness("claude"))

    def test_codex_declares_no_worker_role(self) -> None:
        with self.assertRaises(HarnessRoleError) as ctx:
            worker_launch.for_harness("codex")
        self.assertIn("worker", str(ctx.exception))

    def test_a_worker_adapter_without_a_launcher_is_refused(self) -> None:
        with mock.patch.object(worker_launch, "LAUNCHERS", {}):
            with self.assertRaises(HarnessRoleError):
                worker_launch.for_harness("claude-code")


class SeamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = ast.parse((ROOT / "controller" / "job.py").read_text())

    def test_launch_job_launches_only_through_the_launcher(self) -> None:
        launch_job = _function(self.tree, "_launch_job")
        self.assertIn("launcher", [a.arg for a in launch_job.args.kwonlyargs])
        self.assertEqual(
            [ast.unparse(n.func) for n in ast.walk(launch_job)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "launch"],
            ["launcher.launch"],
        )
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "worker.launch":
                self.fail("controller/job.py still calls worker.launch directly")

    def test_execute_step_resolves_the_launcher_before_the_usage_gate_and_forwards_it(self) -> None:
        step = _function(self.tree, "_execute_step_locked")
        resolved = gate = forwarded = None
        for node in ast.walk(step):
            if isinstance(node, ast.Assign) and ast.unparse(node.value) == "worker_launch.for_harness(route.harness)":
                resolved = node.lineno
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "usage_gate":
                gate = node.lineno
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "_launch_job":
                forwarded = [k.arg for k in node.keywords]
        self.assertIsNotNone(resolved)
        self.assertIsNotNone(gate)
        self.assertLess(resolved, gate)
        self.assertIn("launcher", forwarded)


class ImportDisciplineTest(unittest.TestCase):
    def test_worker_launch_imports_only_harness_worker_and_errors(self) -> None:
        self.assertEqual(
            _package_imports(ROOT / "controller" / "worker_launch.py"),
            {"controller.harness", "controller.worker", "controller.errors"},
        )

    def test_harness_and_worker_do_not_import_it(self) -> None:
        for name in ("harness", "worker"):
            self.assertNotIn("controller.worker_launch", _package_imports(ROOT / "controller" / f"{name}.py"))


if __name__ == "__main__":
    unittest.main()
