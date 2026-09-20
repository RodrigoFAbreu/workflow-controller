"""Package-wide write-path containment scan (CP1/REQ-2,
``docs/ai-workflow/CONTROLLER_GEN1_HARDENING_PLAN.md``).

Generalizes ``tests/test_target_state.py``'s ``ReadOnlySourceScanTest``
AST-walk pattern from one always-read-only module to every module under
``controller/``, where the property being proved is weaker: not "never
writes", but "every write is contained". A write-call site (the same
recognized write-form set that scanner uses, widened with ``unlink``) is
clean iff it is either inside ``runtime.py`` itself, a call into
``runtime.py``'s own already-containment-checked public write API
(``runtime.<name>``), or dominated -- syntactically, within the same
function's own body, never across a function-call boundary -- by a call
to ``runtime.assert_contained``/``runtime._assert_contained``. A
``subprocess``/``os.system``-mediated write is not a recognized write-call
form and is never flagged or cleared by this scanner (``identity.py``'s
``_extract_clean`` tar invocation is the one instance in ``controller/``
today). Its containment rests on two conjuncts, not one: the directory
guard already placed on ``tmp_dir`` before ``tar`` is dispatched into it
*and* the fact that the paths ``tar`` extracts are ``HEAD``'s own
committed tree, scoped by ``git archive``'s own pathspec argument -- a git
tree entry cannot carry a ``..`` component or an absolute path, so every
extracted member is relative and already normalized. The guard alone
bounds only where ``tar`` is told to write, not what it is told to write
there; the pathspec-normalization conjunct is what rules out a malicious
or corrupted archive member steering ``tar`` outside that root.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CONTROLLER_DIR = REPO_ROOT / "controller"
_EXEMPT_FILENAME = "runtime.py"

#: Adopted literally from ``ReadOnlySourceScanTest._WRITE_CALL_SUFFIXES``,
#: widened with ``unlink`` (``identity.py``'s ``tmp.unlink(missing_ok=True)``
#: is the same class of destructive write and belongs in the set an honest
#: widening produces -- plan review round 1, finding I2).
_WRITE_CALL_SUFFIXES = {
    "write_text", "write_bytes", "replace", "rename", "remove",
    "copy", "copy2", "copyfile", "copytree", "move", "rmtree", "unlink",
}
_WRITE_MODE_CHARS = set("wax+")
_GUARD_SUFFIXES = {"assert_contained", "_assert_contained"}


def _dotted_name(node: ast.AST):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _direct_calls(func_node: ast.AST) -> list[ast.Call]:
    """Every ``ast.Call`` written directly in ``func_node``'s own body, in
    source order -- never reaching into a nested function/lambda's own
    body, a separate scope with its own guard state. This is what makes
    the domination check below syntactic and scoped to one function's own
    body, not a real control-flow analysis (plan CP1 item 4)."""
    calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.AST) -> None:
            if node is func_node:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AST) -> None:
            self.visit_FunctionDef(node)

        def visit_Lambda(self, node: ast.AST) -> None:
            return

        def visit_Call(self, node: ast.Call) -> None:
            calls.append(node)
            self.generic_visit(node)

    _Visitor().visit(func_node)
    calls.sort(key=lambda n: (n.lineno, n.col_offset))
    return calls


def _write_call_description(node: ast.Call):
    """``(callee, description)`` if ``node`` is a recognized write-call
    form, else ``None``."""
    callee = _dotted_name(node.func)
    if callee is None:
        return None
    attr = callee.rsplit(".", 1)[-1]

    if attr == "open" or callee == "open":
        args = list(node.args)
        mode_arg = args[1] if len(args) > 1 else None
        for kw in node.keywords:
            if kw.arg == "mode":
                mode_arg = kw.value
        if mode_arg is None:
            return None  # default mode "r" -- not a write
        if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
            if _WRITE_MODE_CHARS & set(mode_arg.value):
                return callee, f"open(..., mode={mode_arg.value!r})"
            return None
        return callee, "open(...) with a non-literal mode argument"

    if attr in _WRITE_CALL_SUFFIXES:
        return callee, f"{callee}(...)"
    return None


def _is_guard_call(callee: str) -> bool:
    return callee.rsplit(".", 1)[-1] in _GUARD_SUFFIXES


def _is_exempt_runtime_api_call(callee: str) -> bool:
    """``runtime.<name>`` for any name other than the guard itself -- a
    call into ``runtime.py``'s own already-containment-checked public
    write API (e.g. ``runtime.write_bytes``), treated as safe on the same
    basis as living inside ``runtime.py``, never requiring a redundant
    local guard at its call site."""
    head = callee.split(".", 1)[0]
    return head == "runtime" and not _is_guard_call(callee)


def scan_module(source: str, *, exempt: bool) -> list[str]:
    """Return a violation description per unguarded write-call site found
    in ``source``. ``exempt=True`` (the module is ``runtime.py`` itself)
    short-circuits to no violations."""
    if exempt:
        return []
    tree = ast.parse(source)
    violations: list[str] = []
    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        guarded = False
        for call in _direct_calls(func_node):
            callee = _dotted_name(call.func)
            if callee and _is_guard_call(callee):
                guarded = True
                continue
            described = _write_call_description(call)
            if described is None:
                continue
            write_callee, desc = described
            if _is_exempt_runtime_api_call(write_callee):
                continue
            if guarded:
                continue
            violations.append(f"line {call.lineno}: {desc}")
    return violations


class PackageWideWriteContainmentScanTest(unittest.TestCase):
    """CP1/REQ-2: every durable-write call anywhere under ``controller/``
    is inside ``runtime.py``, a call into its public write API, or
    dominated by a containment-guard call."""

    def test_no_unguarded_write_call_anywhere_under_controller(self) -> None:
        violations: dict[str, list[str]] = {}
        for path in sorted(CONTROLLER_DIR.glob("*.py")):
            found = scan_module(path.read_text(), exempt=(path.name == _EXEMPT_FILENAME))
            if found:
                violations[path.name] = found
        self.assertEqual(violations, {})


class SyntheticInstantiationTest(unittest.TestCase):
    """The three synthetic instantiations plan CP1's "Regression coverage"
    names, each a source string parsed via ``ast.parse`` -- mirroring
    ``ReadOnlySourceScanTest.test_scanner_flags_a_synthetic_write_call``'s
    own existing pattern exactly, never a real module mutated at test
    time (plan review round 1, finding M2)."""

    def test_a_write_call_with_no_preceding_guard_is_flagged(self) -> None:
        source = "def f():\n    Path('x').write_text('y')\n"
        self.assertEqual(scan_module(source, exempt=False), ["line 2: write_text(...)"])

    def test_the_same_call_preceded_by_a_guard_is_not_flagged(self) -> None:
        source = (
            "def f(root, path):\n"
            "    runtime.assert_contained(root, path)\n"
            "    Path('x').write_text('y')\n"
        )
        self.assertEqual(scan_module(source, exempt=False), [])

    def test_a_bare_runtime_write_api_call_is_not_flagged(self) -> None:
        source = "def f():\n    runtime.write_bytes(root, 'rel', b'data')\n"
        self.assertEqual(scan_module(source, exempt=False), [])


if __name__ == "__main__":
    unittest.main()
