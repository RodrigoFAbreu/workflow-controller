"""The catalogue's flow ids, read from the scenario modules' source without importing them.

The suite wrappers (``tests/test_scenarios.py``, ``tests/test_scenarios_negative.py``) must name one test class per
scenario at module load, which is before any isolation gate can run. Importing ``features``/``lifecycle`` there would
execute their top-level statements and decorators on the host, so the wrappers read the ids from the syntax tree:
nothing in this module imports, compiles or executes a scenario module. ``tests/test_scenarios.py`` asserts the result
equals what the sandboxed discovery reports, wherever isolation is available.
"""

from __future__ import annotations

import ast
from pathlib import Path

SCENARIOS_DIR = Path(__file__).resolve().parent
#: Registration order: ``features`` imports ``lifecycle`` first, so scenarios 1-5 register before 6-10.
CATALOGUE_MODULES = ("lifecycle", "features", "review")


def _is_scenario_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "scenario" and bool(node.args)


def _selftest(node: ast.Call) -> bool:
    return any(k.arg == "selftest" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
               for k in node.keywords)


def _literal_tuples(tree: ast.Module) -> dict:
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return found


def _ids_of(call: ast.Call, loops: dict) -> "list[str]":
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value]
    if isinstance(arg, ast.JoinedStr) and len(arg.values) == 2:
        head, hole = arg.values
        if isinstance(head, ast.Constant) and isinstance(hole, ast.FormattedValue) and isinstance(hole.value, ast.Name):
            return [head.value + str(item) for item in loops.get(hole.value.id, ())]
    raise ValueError(f"a scenario id at line {call.lineno} is neither a literal nor a literal-tuple loop variable")


def module_flow_ids(path: Path) -> "list[str]":
    tree = ast.parse(path.read_text(), filename=str(path))
    tuples = _literal_tuples(tree)
    loops: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Name):
            loops[node.target.id] = tuples.get(node.iter.id, ())
    calls = [n for n in ast.walk(tree) if _is_scenario_call(n) and not _selftest(n)]
    ids: list[str] = []
    for call in sorted(calls, key=lambda c: (c.lineno, c.col_offset)):
        ids += _ids_of(call, loops)
    return ids


def catalogue_flow_ids(directory: Path = SCENARIOS_DIR) -> "list[str]":
    ids: list[str] = []
    for name in CATALOGUE_MODULES:
        ids += module_flow_ids(directory / f"{name}.py")
    return ids
