"""Structural properties of `controller/*.py` that must hold no matter
which checkpoint is adding modules: the dependency graph, eager loading,
no late resource reads, no hot reload / no `USER_OVERRIDE`, and the
`SourceSnapshotError` `raised_by` invariant.

These are source scans over the *live* `controller/` package, not fixture
copies -- so a later checkpoint that violates one of these properties
fails this suite directly, without needing its own instrument.
"""

from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_DIR = REPO_ROOT / "controller"

#: The full dependency order the plan's "Dependency direction" diagram
#: describes (`cli -> job -> {...} -> decision -> {identity, runtime,
#: errors}`), read fundamentals-first -- the same order
#: `controller/__init__.py`'s own eager-import literal follows. A module at
#: index *i* may import (from `controller`) only a module at an index < i.
DEPENDENCY_ORDER = [
    "buildinfo", "version", "errors", "runtime", "lock", "identity", "decision", "managed_repo",
    "target_state", "evidence", "routing", "worker", "handoff", "job", "observe", "cli",
]

_ALLOWLISTED_RESOURCE_READ_SITES = {
    ("identity.py", "pin"),
    ("identity.py", "_read_generation"),
    # Release-runtime-observability CP2. Both run before any orchestration:
    # `resolve_runtime` reads the running package's own BUILD_INFO.json
    # while `pin()` resolves the unpinned identity, and `_extract_package`
    # copies the installed package into the snapshot inside `materialise`,
    # before the re-exec.
    ("identity.py", "resolve_runtime"),
    ("identity.py", "_extract_package"),
}

_RELOAD_LITERALS = {"importlib.reload", "imp.reload"}
_RESOURCE_LITERALS = {"importlib.resources", "pkgutil.get_data"}


def _module_files() -> list[Path]:
    return sorted(
        p for p in CONTROLLER_DIR.glob("*.py")
        if p.stem not in {"__init__", "__main__"}
    )


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _dotted_name(node: ast.AST) -> str | None:
    """Resolve an Attribute/Name chain to its dotted string, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


class DependencyGraphTest(unittest.TestCase):
    def test_no_module_imports_one_later_in_the_dependency_order(self) -> None:
        index = {name: i for i, name in enumerate(DEPENDENCY_ORDER)}
        for path in _module_files():
            stem = path.stem
            if stem not in index:
                continue  # a module the plan's own order doesn't yet name
            tree = _parse(path)
            for node in ast.walk(tree):
                imported: set[str] = set()
                if isinstance(node, ast.ImportFrom) and node.module == "controller":
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is None and node.level == 1:
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("controller."):
                            imported.add(alias.name.split(".")[1])
                for name in imported:
                    if name not in index:
                        continue
                    self.assertLess(
                        index[name], index[stem],
                        f"{stem}.py imports {name}, which is later in the dependency order "
                        f"({DEPENDENCY_ORDER}) -- {stem} may only import earlier modules",
                    )

    def test_leaf_modules_import_nothing_from_the_package(self) -> None:
        # `buildinfo` and `version` are also loaded by file path from
        # `setup.py`, where the package itself is not importable.
        for leaf in ("buildinfo", "version", "errors"):
            tree = _parse(CONTROLLER_DIR / f"{leaf}.py")
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "controller", leaf)
                    self.assertFalse(node.module is None and node.level >= 1, leaf)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.startswith("controller"), leaf)


class EagerLoadTest(unittest.TestCase):
    def test_init_imports_exactly_the_modules_present(self) -> None:
        import controller

        present = {p.stem for p in CONTROLLER_DIR.glob("*.py")} - {"__init__", "__main__"}
        self.assertEqual(set(controller.__all__), present)
        for name in controller.__all__:
            self.assertIn(f"controller.{name}", sys.modules)

    def test_init_order_matches_the_dependency_order(self) -> None:
        import controller

        present_in_order = [m for m in DEPENDENCY_ORDER if m in controller.__all__]
        self.assertEqual(list(controller.__all__), present_in_order)


class NoLateResourceReadTest(unittest.TestCase):
    """`importlib.resources`/`pkgutil.get_data`, and any file read whose
    path traces back to `Path(__file__).parent` within the same function,
    are forbidden everywhere except the two allowlisted call sites."""

    def test_no_resource_module_is_imported_or_referenced(self) -> None:
        for path in _module_files():
            tree = _parse(path)
            for node in ast.walk(tree):
                dotted = None
                if isinstance(node, (ast.Import,)):
                    for alias in node.names:
                        self.assertNotIn(alias.name, _RESOURCE_LITERALS,
                                          f"{path.name} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module in _RESOURCE_LITERALS:
                        self.fail(f"{path.name} imports from {node.module}")
                elif isinstance(node, ast.Attribute):
                    dotted = _dotted_name(node)
                    if dotted in _RESOURCE_LITERALS:
                        self.fail(f"{path.name} references {dotted}")

    def test_file_reads_rooted_at_dunder_file_are_allowlisted(self) -> None:
        for path in _module_files():
            tree = _parse(path)
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # names in this function body bound to an expression rooted
                # at Path(__file__).parent
                rooted_names: set[str] = set()
                for stmt in ast.walk(func):
                    if isinstance(stmt, ast.Assign) and _is_dunder_file_rooted(stmt.value):
                        for target in stmt.targets:
                            if isinstance(target, ast.Name):
                                rooted_names.add(target.id)
                for call in ast.walk(func):
                    if not isinstance(call, ast.Call):
                        continue
                    callee = _dotted_name(call.func)
                    is_open_call = callee == "open"
                    is_read_call = isinstance(call.func, ast.Attribute) and call.func.attr in (
                        "read_text", "read_bytes",
                    )
                    if not (is_open_call or is_read_call):
                        continue
                    args = list(call.args)
                    if is_read_call:
                        args = [call.func.value]
                    for arg in args:
                        rooted = _is_dunder_file_rooted(arg) or (
                            isinstance(arg, ast.Name) and arg.id in rooted_names
                        )
                        if rooted:
                            site = (path.name, func.name)
                            self.assertIn(
                                site, _ALLOWLISTED_RESOURCE_READ_SITES,
                                f"{path.name}:{func.name} reads a path derived from "
                                f"Path(__file__).parent outside the allowlist",
                            )


def _is_dunder_file_rooted(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "__file__":
            return True
    return False


class NoHotReloadOrUserOverrideTest(unittest.TestCase):
    def test_no_reload_call_anywhere(self) -> None:
        for path in _module_files():
            tree = _parse(path)
            for node in ast.walk(tree):
                dotted = None
                if isinstance(node, ast.Attribute):
                    dotted = _dotted_name(node)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        dotted = f"{node.module}.{alias.name}"
                        self.assertNotIn(dotted, _RELOAD_LITERALS, f"{path.name}: {dotted}")
                    continue
                if dotted in _RELOAD_LITERALS:
                    self.fail(f"{path.name} references {dotted}")

    def test_user_override_literal_appears_nowhere(self) -> None:
        for path in _module_files():
            text = path.read_text()
            self.assertNotIn("USER_OVERRIDE", text, f"{path.name} mentions USER_OVERRIDE")


# ---------------------------------------------------------------------------
# The `SourceSnapshotError` `raised_by` invariant.
# ---------------------------------------------------------------------------


def _has_raised_by_inline(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "evidence" and isinstance(kw.value, ast.Dict):
            for key in kw.value.keys:
                if isinstance(key, ast.Constant) and key.value == "raised_by":
                    return True
    return False


def scan_raised_by_violations(tree: ast.Module) -> list[str]:
    """Returns a list of human-readable violation descriptions -- direct
    ``raise SourceSnapshotError(...)`` call sites that do not carry an
    inline ``evidence={'raised_by': ...}`` literal at the raise itself.

    Scoped deliberately to the ladder's limb 1 (a direct `Call`) -- the
    shape every production call site in this package actually uses. The
    plan's full ladder additionally resolves bare-`Name` raises through a
    single-binding rule (limbs 2-4); that refinement is not implemented
    here, so a `Name`-valued raise is silently out of this scan's scope
    rather than flagged, which is a narrower, intentionally simplified
    property than the plan's own."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        expr = node.exc
        if not isinstance(expr, ast.Call):
            continue
        callee = _dotted_name(expr.func)
        if callee != "SourceSnapshotError":
            continue
        if not _has_raised_by_inline(expr):
            violations.append(f"line {node.lineno}: direct call missing inline raised_by")
    return violations


class RaisedByInvariantTest(unittest.TestCase):
    def test_every_source_snapshot_error_raise_in_production_code_sets_raised_by(self) -> None:
        for path in _module_files():
            tree = _parse(path)
            violations = scan_raised_by_violations(tree)
            self.assertEqual(violations, [], f"{path.name}: {violations}")

    def test_scanner_flags_a_synthetic_violation(self) -> None:
        source = (
            "def f():\n"
            "    raise SourceSnapshotError('boom', evidence=dict(raised_by='materialise'))\n"
        )
        tree = ast.parse(source)
        violations = scan_raised_by_violations(tree)
        self.assertEqual(len(violations), 1)

    def test_scanner_admits_a_conforming_site(self) -> None:
        source = (
            "def f():\n"
            "    raise SourceSnapshotError('boom', evidence={'raised_by': 'pin'})\n"
        )
        tree = ast.parse(source)
        self.assertEqual(scan_raised_by_violations(tree), [])

    def test_scanner_ignores_other_taxonomy_members_and_bare_reraise(self) -> None:
        source = (
            "def f():\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        raise\n"
            "    raise DirtyControllerSourceError('x', evidence={})\n"
        )
        tree = ast.parse(source)
        self.assertEqual(scan_raised_by_violations(tree), [])


if __name__ == "__main__":
    unittest.main()
