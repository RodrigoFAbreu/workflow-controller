"""Throwaway Git repositories never run automatic maintenance
(child-process-reaping, Design A, invariant I7).

Git starts ``git maintenance run --auto``/``git gc --auto`` in the
background after ordinary commands. Under a Controller that is a child
subreaper, each detached run is re-parented to it; the tests make
hundreds of repositories, so these orphans were most of the zombies that
filled the process table on 2026-09-29. ``fixtures.git_init`` and
``fixtures.git_clone`` write ``maintenance.auto=false`` and ``gc.auto=0``
into each repository's own config, which no test's replacement
environment can undo.

The guard scans every ``tests/**/*.py`` outside ``tests/workflow_releases/``
(vendored Workflow releases, byte-identical to the release) and fails on
any repository-creating site outside those two fixtures, by three rules:

1. token sequences: in a list or tuple literal, or a call's positional
   arguments, an element ``"git"`` whose subcommand -- the first later
   element that is not an option token -- is the constant ``"init"`` or
   ``"clone"``;
2. helpers: a call to ``git``, ``_git`` or ``*_git`` reads as if its
   positional arguments followed an implicit ``"git"``;
3. shell strings: any non-docstring string or f-string matching
   :data:`SHELL_PATTERN`.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

from tests import fixtures

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The vendored Workflow releases: never scanned, by exact path prefix.
EXCLUDED_PREFIX = "tests/workflow_releases/"

#: The one module allowed to create repositories, and where in it.
FIXTURES_REL = "tests/fixtures.py"
ALLOWED_FUNCTIONS = frozenset({"git_init", "git_clone"})

#: Options that consume the element (or word) that follows them.
VALUE_OPTIONS = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace"})

SHELL_PATTERN = re.compile(
    r"\bgit(\s+(-c|-C|--git-dir|--work-tree|--namespace)\s+\S+|\s+-\S+)*\s+(init|clone)\b"
)

_CREATING = frozenset({"init", "clone"})


def _leading_text(node: ast.AST) -> str | None:
    """A string constant's text, or an f-string's leading literal part."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _creating_subcommand(elements: list[ast.AST]) -> bool:
    """Rule 1 over one sequence: a ``"git"`` element whose subcommand is
    the constant ``"init"`` or ``"clone"``."""
    for index, element in enumerate(elements):
        if not (isinstance(element, ast.Constant) and element.value == "git"):
            continue
        rest = iter(elements[index + 1:])
        for later in rest:
            text = _leading_text(later)
            if isinstance(later, ast.Constant) and later.value in VALUE_OPTIONS:
                next(rest, None)
                continue
            if text is not None and text.startswith("-"):
                continue
            if isinstance(later, ast.Constant) and later.value in _CREATING:
                return True
            break
    return False


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_git_helper(name: str | None) -> bool:
    return name is not None and (name in ("git", "_git") or name.endswith("_git"))


def _shell_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}"
            for part in node.values
        )
    return None


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                found.add(id(body[0].value))
    return found


def _allowed_nodes(tree: ast.AST) -> set[int]:
    """Every node inside ``fixtures.git_init``/``fixtures.git_clone``."""
    allowed = set()
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, ast.FunctionDef) and node.name in ALLOWED_FUNCTIONS:
            allowed.update(id(inner) for inner in ast.walk(node))
    return allowed


def scan_source(source: str, rel_path: str) -> list[tuple[int, str]]:
    """``(line, rule)`` for every repository-creating site in ``source``
    outside the two fixtures."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    allowed = _allowed_nodes(tree) if rel_path == FIXTURES_REL else set()
    inside_fstring = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                      for part in ast.walk(node) if part is not node}
    sites = []
    for node in ast.walk(tree):
        if id(node) in allowed:
            continue
        if isinstance(node, (ast.List, ast.Tuple)) and _creating_subcommand(node.elts):
            sites.append((node.lineno, "sequence"))
        elif isinstance(node, ast.Call):
            if _creating_subcommand(node.args):
                sites.append((node.lineno, "call arguments"))
            if _is_git_helper(_callee_name(node)) and _creating_subcommand(
                    [ast.Constant(value="git"), *node.args]):
                sites.append((node.lineno, "helper"))
        if id(node) in docstrings or id(node) in inside_fstring:
            continue
        text = _shell_text(node)
        if text is not None and SHELL_PATTERN.search(text):
            sites.append((node.lineno, "shell string"))
    return sites


def scanned_files(root: Path) -> list[Path]:
    """Every ``tests/**/*.py`` under ``root`` outside the excluded prefix."""
    return sorted(
        path for path in (root / "tests").rglob("*.py")
        if not path.relative_to(root).as_posix().startswith(EXCLUDED_PREFIX)
    )


def scan_tree(root: Path) -> list[str]:
    findings = []
    for path in scanned_files(root):
        rel = path.relative_to(root).as_posix()
        for line, rule in scan_source(path.read_text(), rel):
            findings.append(f"{rel}:{line}: {rule}")
    return findings


class RepositoryCreationGuardTest(unittest.TestCase):
    def test_every_repository_is_created_through_the_fixtures(self):
        self.assertEqual(scan_tree(REPO_ROOT), [],
                         "create throwaway repositories with fixtures.git_init/git_clone")

    def test_the_scan_covers_the_suite(self):
        scanned = {path.relative_to(REPO_ROOT).as_posix() for path in scanned_files(REPO_ROOT)}
        self.assertIn(FIXTURES_REL, scanned)
        self.assertIn("tests/test_runtime.py", scanned)
        self.assertFalse(any(rel.startswith(EXCLUDED_PREFIX) for rel in scanned))


class GuardSelfTest(unittest.TestCase):
    def flagged(self, source: str, rel_path: str = "tests/test_x.py") -> bool:
        """``GIT`` in ``source`` stands for ``git`` (so this module's own
        literals never match the shell rule)."""
        return bool(scan_source(textwrap.dedent(source).replace("GIT ", "git "), rel_path))

    def test_creating_forms_are_flagged(self):
        for source in (
            'subprocess.run(["git", "init", p])',
            'x = ["git", "-c", f"x={v}", "clone", a, b]',
            '_git("init", "-q", p)',
            'os.system("GIT init -q x")',
            'os.system(f"GIT init -q {p}")',
            'subprocess.run(["bash", "-c", f"cd {d} && GIT clone {a} b"])',
            'step = {"command": "GIT init -q x"}',
            'cmd = "GIT clone a b"',
            'run(("git", "--git-dir", d, "-q", "init"))',
            'script_git("clone", a, b)',
            'git("-C", d, "init")',
        ):
            with self.subTest(source=source):
                self.assertTrue(self.flagged(source))

    def test_other_forms_are_not_flagged(self):
        for source in (
            'subprocess.run(["git", "log", "--grep", "clone"])',
            'x = "GIT --no-pager log --grep clone"',
            'def f():\n    """Runs ``GIT init`` first."""\n',
            '"""Module text: GIT clone a b."""\n',
            'subprocess.run(["git", *args, "init"])',
            'x = "legit clone"',
            'git_init(p)',
        ):
            with self.subTest(source=source):
                self.assertFalse(self.flagged(source))

    def test_the_fixtures_themselves_are_allowed_only_in_fixtures(self):
        source = 'def git_init(path):\n    run(["git", "init", "-q", path])\n'
        self.assertFalse(self.flagged(source, FIXTURES_REL))
        self.assertTrue(self.flagged(source, "tests/test_x.py"))
        self.assertTrue(self.flagged('def other(path):\n    run(["git", "init", path])\n',
                                     FIXTURES_REL))

    def test_only_the_vendored_release_prefix_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("tests/workflow_releases/2.6.0/scripts/a.py",
                        "tests/workflow_releases_x/a.py",
                        "tests/other/workflow_releases/a.py"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text('run(["git", "init", p])\n')
            self.assertEqual(scan_tree(root), [
                "tests/other/workflow_releases/a.py:1: sequence",
                "tests/workflow_releases_x/a.py:1: sequence",
            ])


class QuietMaintenanceReadBackTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        cleared = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG")}
        patcher = unittest.mock.patch.dict(os.environ, cleared, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_quiet(self, repo: Path) -> None:
        for key, value in fixtures.QUIET_MAINTENANCE.items():
            with self.subTest(repo=repo.name, key=key):
                read = subprocess.run(["git", "config", "--local", "--get", key], cwd=repo,
                                      capture_output=True, text=True, check=True)
                self.assertEqual(read.stdout.strip(), value)

    def test_init_clone_and_bare_init_write_both_keys(self):
        source = fixtures.git_init(self.tmp / "source")
        fixtures.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                      "commit", "-q", "--allow-empty", "-m", "c"], cwd=source)
        bare = fixtures.git_init(self.tmp / "bare.git", "--bare")
        clone = fixtures.git_clone(source, self.tmp / "clone")
        for repo in (source, bare, clone):
            self.assert_quiet(repo)


if __name__ == "__main__":
    unittest.main()
