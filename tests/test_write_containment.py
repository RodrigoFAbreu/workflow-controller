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


#: The only flag names a read-only ``os.open`` may combine
#: (`workflow-controller-automatic-lifecycle-orchestration` CP5): ``lock.py``
#: opens the target's git directory with ``os.O_RDONLY | os.O_DIRECTORY |
#: os.O_CLOEXEC``, because Python's ``open()`` cannot open a directory.
#: ``workflow_contract._copy_index`` reads the target's index with
#: ``os.O_RDONLY | os.O_NONBLOCK``, so a planted FIFO is refused rather than
#: waited on (`workflow-controller-workflow-2-6-integration`, implementation
#: review round 1). ``protocol.script_set`` reads each managed script with
#: ``os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK``, so a symlink or a FIFO
#: planted in its place is refused (`workflow-controller-orchestration-
#: protocol-v1` CP1). None of these flags can create, truncate or write.
_READ_ONLY_OS_OPEN_FLAGS = {"os.O_RDONLY", "os.O_DIRECTORY", "os.O_CLOEXEC", "os.O_NONBLOCK", "os.O_NOFOLLOW"}


def _or_operands(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _or_operands(node.left) + _or_operands(node.right)
    return [node]


def _is_read_only_os_open(callee: str, node: ast.Call) -> bool:
    """The scan's one ``os.open`` acceptance: a flags argument that is
    ``os.O_RDONLY`` alone, or a ``|``-combination of ``os.O_RDONLY`` with
    only ``os.O_DIRECTORY``, ``os.O_CLOEXEC`` and ``os.O_NONBLOCK``.
    ``O_WRONLY``, ``O_RDWR``,
    ``O_CREAT``, ``O_TRUNC``, ``O_APPEND`` or a non-literal flags name is
    still flagged."""
    if callee != "os.open" or len(node.args) < 2:
        return False
    names = [_dotted_name(operand) for operand in _or_operands(node.args[1])]
    return "os.O_RDONLY" in names and all(name in _READ_ONLY_OS_OPEN_FLAGS for name in names)


#: The one append-only ``os.open`` form, and the only ``(module, function)``
#: site it is accepted at (`workflow-controller-release-runtime-
#: observability` CP4): ``worker.launch`` opens the worker's two stream
#: files ``os.O_WRONLY | os.O_APPEND`` and hands the descriptors to the
#: worker. It has no runtime root to check them against; containment is
#: enforced where they are created (``runtime.create_log_file``, checked and
#: ``O_EXCL``), and without ``O_CREAT``/``O_TRUNC`` this open cannot create
#: or truncate anything. Any other flag, or the same form anywhere else, is
#: still flagged.
_APPEND_ONLY_OS_OPEN_FLAGS = {"os.O_WRONLY", "os.O_APPEND"}
_APPEND_ONLY_OS_OPEN_SITES = {("worker.py", "launch")}


def _is_append_only_os_open(callee: str, node: ast.Call) -> bool:
    if callee != "os.open" or len(node.args) < 2:
        return False
    names = [_dotted_name(operand) for operand in _or_operands(node.args[1])]
    return sorted(names) == sorted(_APPEND_ONLY_OS_OPEN_FLAGS)


def _write_call_description(node: ast.Call, *, append_only_site: bool = False):
    """``(callee, description)`` if ``node`` is a recognized write-call
    form, else ``None``. ``append_only_site`` accepts the append-only
    ``os.open`` form (:data:`_APPEND_ONLY_OS_OPEN_SITES`)."""
    callee = _dotted_name(node.func)
    if callee is None:
        return None
    attr = callee.rsplit(".", 1)[-1]

    if _is_read_only_os_open(callee, node):
        return None
    if append_only_site and _is_append_only_os_open(callee, node):
        return None

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


#: ``runtime.py``'s own containment-checked public write API -- both routed
#: through the guarded ``_atomic_write`` (round-2 implementation-review
#: finding O4: a blanket ``runtime.<name>`` head-match would silently
#: exempt a future ``runtime.remove``/``runtime.unlink``/``runtime.move``
#: added without a guard; this allowlist matches only the entry points that
#: are actually checked today).
_EXEMPT_RUNTIME_API_NAMES = {"write_json", "write_bytes"}


def _is_exempt_runtime_api_call(callee: str) -> bool:
    """``runtime.<name>`` for ``name`` in ``_EXEMPT_RUNTIME_API_NAMES`` -- a
    call into ``runtime.py``'s own already-containment-checked public
    write API, treated as safe on the same basis as living inside
    ``runtime.py``, never requiring a redundant local guard at its call
    site."""
    head, _, name = callee.partition(".")
    return head == "runtime" and name in _EXEMPT_RUNTIME_API_NAMES


def scan_module(source: str, *, exempt: bool, module: str | None = None) -> list[str]:
    """Return a violation description per unguarded write-call site found
    in ``source``. ``exempt=True`` (the module is ``runtime.py`` itself)
    short-circuits to no violations. ``module`` is the file name, which
    :data:`_APPEND_ONLY_OS_OPEN_SITES` is keyed on."""
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
            described = _write_call_description(
                call, append_only_site=(module, func_node.name) in _APPEND_ONLY_OS_OPEN_SITES,
            )
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
            found = scan_module(path.read_text(), exempt=(path.name == _EXEMPT_FILENAME), module=path.name)
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

    def test_a_read_only_os_open_is_a_read(self) -> None:
        """Automatic-lifecycle-orchestration CP5: ``lock.py``'s own form,
        and a lone ``os.O_RDONLY``, are accepted; so is
        ``workflow_contract``'s non-blocking index read."""
        for flags in ("os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC", "os.O_RDONLY",
                      "os.O_RDONLY | os.O_CLOEXEC", "os.O_DIRECTORY | os.O_RDONLY",
                      "os.O_RDONLY | os.O_NONBLOCK", "os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK"):
            with self.subTest(flags=flags):
                source = f"def f(path):\n    return os.open(path, {flags})\n"
                self.assertEqual(scan_module(source, exempt=False), [])

    def test_a_writing_or_non_literal_os_open_is_still_flagged(self) -> None:
        for flags in ("os.O_WRONLY", "os.O_RDWR", "os.O_RDONLY | os.O_CREAT", "os.O_CREAT",
                      "os.O_RDONLY | os.O_TRUNC", "os.O_RDONLY | os.O_APPEND", "flags",
                      "os.O_DIRECTORY | os.O_CLOEXEC", "os.O_NONBLOCK", "os.O_WRONLY | os.O_NONBLOCK"):
            with self.subTest(flags=flags):
                source = f"def f(path, flags):\n    return os.open(path, {flags})\n"
                self.assertEqual(len(scan_module(source, exempt=False)), 1, flags)

    def test_lock_py_opens_the_git_directory_read_only(self) -> None:
        """The live ``lock.py`` carries the accepted form, and it is clean."""
        source = (CONTROLLER_DIR / "lock.py").read_text()
        self.assertIn("os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)", source)
        self.assertEqual(scan_module(source, exempt=False), [])

    def test_the_append_only_os_open_is_accepted_only_in_worker_launch(self) -> None:
        """Release-runtime-observability CP4: ``os.O_WRONLY | os.O_APPEND``
        is accepted in ``worker.py``'s ``launch`` and nowhere else, and any
        added flag there is still flagged."""
        accepted = "def launch(path):\n    return os.open(path, os.O_WRONLY | os.O_APPEND)\n"
        self.assertEqual(scan_module(accepted, exempt=False, module="worker.py"), [])
        self.assertEqual(len(scan_module(accepted, exempt=False, module="job.py")), 1)
        self.assertEqual(len(scan_module(accepted, exempt=False)), 1)
        elsewhere = accepted.replace("def launch", "def other")
        self.assertEqual(len(scan_module(elsewhere, exempt=False, module="worker.py")), 1)
        for flags in ("os.O_WRONLY | os.O_APPEND | os.O_CREAT", "os.O_WRONLY | os.O_APPEND | os.O_TRUNC",
                      "os.O_WRONLY", "os.O_RDWR | os.O_APPEND", "flags"):
            with self.subTest(flags=flags):
                source = f"def launch(path, flags):\n    return os.open(path, {flags})\n"
                self.assertEqual(len(scan_module(source, exempt=False, module="worker.py")), 1, flags)

    def test_worker_launch_carries_the_accepted_append_only_form(self) -> None:
        source = (CONTROLLER_DIR / "worker.py").read_text()
        self.assertIn("os.open(path, os.O_WRONLY | os.O_APPEND)", source)
        self.assertEqual(scan_module(source, exempt=False, module="worker.py"), [])

    def test_a_runtime_call_outside_the_checked_api_is_still_flagged(self) -> None:
        """Round-2 implementation-review finding O4: a hypothetical
        ``runtime.remove`` with no local guard must be flagged -- the
        allowlist is scoped to the two entry points actually routed
        through ``_atomic_write``, not a blanket ``runtime.<name>``
        head-match."""
        source = "def f():\n    runtime.remove('x')\n"
        self.assertEqual(scan_module(source, exempt=False), ["line 2: runtime.remove(...)"])


if __name__ == "__main__":
    unittest.main()
