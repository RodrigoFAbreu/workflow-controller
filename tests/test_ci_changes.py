"""Tests for ``tools/ci_changes.py`` (CI1a, #317): the documentation-only
classifier, the change detection on real temporary repositories, the exit rule
of the aggregate job, and the guard that keeps the documentation set honest.

The guard (D2) scans the repository's Python for readers of a documentation
path with an AST scan, one rule for path positions, resolved names and
folding, a fixture table for temporary repositories, and an allow-list of
readers. The scanner is defined here, below, and is itself exercised on small
sources, so a regression in it fails a named test rather than passing silently.
"""

from __future__ import annotations

import ast
import contextlib
import fnmatch
import importlib.util
import io
import itertools
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

_spec = importlib.util.spec_from_file_location("ci_changes", fixtures.REPO_ROOT / "tools" / "ci_changes.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cc
_spec.loader.exec_module(cc)


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

class ClassifyTests(unittest.TestCase):
    def test_every_member_of_the_set_is_documentation(self):
        for path in ("docs/items/workflow-controller-a1.md", "docs/items/sub/x.md",
                     "docs/releases/1.2.3.md", "docs/run.md", "docs/README.md", "docs/ROADMAP.md",
                     "docs/release-history.md", "docs/backlog.yaml"):
            with self.subTest(path=path):
                self.assertEqual(cc.classify([path]), "docs")

    def test_everything_else_is_code(self):
        for path in ("docs/guide/commands.md", "docs/guide/ci-and-releases.md", "docs/adr/0001-x.md",
                     "docs/roadmap-archive.md", "docs/ACTIVE_MILESTONE.md",
                     "docs/ai-workflow/WORKFLOW_STATE.json", "docs/milestones/x.md",
                     "docs/other.yaml", "docs/sub/page.md", "docs/items", "docs/items/",
                     "README.md", "pyproject.toml", "tools/x.py", "tests/test_x.py",
                     "controller/x.py", "scripts/x.py", ".github/workflows/validate.yml",
                     ".claude/commands/x.md", "docs/items/../tools/x.py", "/docs/run.md",
                     "docs\\run.md", "docs//run.md", "", "nothing.txt"):
            with self.subTest(path=path):
                self.assertEqual(cc.classify([path]), "code")

    def test_one_code_path_makes_the_change_code(self):
        self.assertEqual(cc.classify(["docs/run.md", "docs/items/x.md"]), "docs")
        self.assertEqual(cc.classify(["docs/run.md", "tools/x.py"]), "code")
        self.assertEqual(cc.classify(["docs/run.md", "docs/guide/commands.md"]), "code")

    def test_the_empty_list_is_code(self):
        self.assertEqual(cc.classify([]), "code")
        self.assertEqual(cc.classify(iter(())), "code")

    def test_every_documented_exception_is_code(self):
        for path, reason in cc.DOCS_TOP_LEVEL_EXCEPTIONS:
            with self.subTest(path=path):
                self.assertTrue(reason)
                self.assertEqual(cc.classify([path]), "code")
        for prefix, reason in cc.NOT_DOCUMENTATION:
            with self.subTest(prefix=prefix):
                self.assertTrue(reason)
                self.assertEqual(cc.classify([prefix + "x.md" if prefix.endswith("/") else prefix]), "code")

    def test_every_entry_of_the_set_has_a_reason(self):
        for table in (cc.DOCS_PREFIXES, cc.DOCS_FILES, cc.GATE_MODULES, cc.GATE_TOOLS):
            for entry in table:
                with self.subTest(entry=entry):
                    self.assertTrue(entry[-1].strip())


# ---------------------------------------------------------------------------
# detect, on real temporary repositories
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return fixtures.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args], env=env).stdout


def _apply(repo: Path, changes: dict[str, str | None]) -> None:
    """Write (or, for ``None``, delete) each repository-relative path."""
    for rel, text in changes.items():
        target = repo / rel
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)


def _commit(repo: Path, changes: dict[str, str | None], message: str = "c") -> None:
    _apply(repo, changes)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


class DetectRepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = fixtures.git_init(Path(self._tmp.name) / "repo", "-b", "main")
        _commit(self.repo, {"docs/run.md": "run\n", "docs/items/a.md": "a\n", "docs/items/b.md": "b\n",
                            "tools/x.py": "x = 1\n", "docs/guide/g.md": "g\n"}, "base")
        self.env = {"GITHUB_EVENT_NAME": "pull_request"}

    def merge(self, changes: dict[str, str | None], *, main_after: dict[str, str | None] | None = None,
              branch_message: str = "pr") -> None:
        """Leave ``HEAD`` on a merge commit, as a pull request's merge ref is."""
        _git(self.repo, "checkout", "-q", "-b", "pr")
        _commit(self.repo, changes, branch_message)
        _git(self.repo, "checkout", "-q", "main")
        if main_after is not None:
            _commit(self.repo, main_after, "main moves")
        _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")

    def decide(self, env: dict | None = None) -> str:
        return cc.detect(self.env if env is None else env, cwd=self.repo)[0]

    def test_a_documentation_only_merge_is_false(self):
        self.merge({"docs/run.md": "new\n", "docs/items/c.md": "c\n"})
        self.assertEqual(self.decide(), "false")

    def test_a_mixed_change_is_true(self):
        self.merge({"docs/run.md": "new\n", "tools/x.py": "x = 2\n"})
        self.assertEqual(self.decide(), "true")

    def test_a_guide_change_is_true(self):
        self.merge({"docs/guide/g.md": "new\n"})
        self.assertEqual(self.decide(), "true")

    def test_deleting_a_documentation_file_is_false_and_a_code_file_true(self):
        self.merge({"docs/items/a.md": None})
        self.assertEqual(self.decide(), "false")
        _git(self.repo, "reset", "-q", "--hard", "HEAD^1")
        _git(self.repo, "branch", "-q", "-D", "pr")
        self.merge({"tools/x.py": None})
        self.assertEqual(self.decide(), "true")

    def test_a_rename_counts_under_both_names(self):
        # From a documentation path into code: the old name is documentation,
        # the new one is not.
        _git(self.repo, "checkout", "-q", "-b", "pr")
        _git(self.repo, "mv", "docs/run.md", "tools/run.py")
        _git(self.repo, "commit", "-q", "-m", "rename")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")
        self.assertEqual(cc.changed_paths(self.repo).__len__(), 2)
        self.assertEqual(sorted(cc.changed_paths(self.repo)), ["docs/run.md", "tools/run.py"])
        self.assertEqual(self.decide(), "true")

    def test_a_rename_inside_the_set_is_false(self):
        _git(self.repo, "checkout", "-q", "-b", "pr")
        _git(self.repo, "mv", "docs/items/a.md", "docs/items/z.md")
        _git(self.repo, "commit", "-q", "-m", "rename")
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "merge", "-q", "--no-ff", "-m", "merge ref", "pr")
        self.assertEqual(self.decide(), "false")

    def test_an_empty_diff_is_true(self):
        self.merge({})
        self.assertEqual(cc.changed_paths(self.repo), [])
        self.assertEqual(self.decide(), "true")

    def test_a_head_that_is_not_a_merge_commit_is_true(self):
        _commit(self.repo, {"docs/run.md": "only docs\n"})
        with self.assertRaises(cc.DetectError):
            cc.changed_paths(self.repo)
        self.assertEqual(self.decide(), "true")

    def test_a_stale_recorded_base_does_not_change_the_result(self):
        # main moves on (code) after the pull request branched; the merge
        # ref's first parent is the new tip, so the diff is only the pull
        # request's documentation. The branch point would show the code.
        branch_point = _git(self.repo, "rev-parse", "HEAD").strip()
        self.merge({"docs/run.md": "new\n"}, main_after={"tools/x.py": "x = 3\n"})
        self.assertEqual(self.decide(), "false")
        stale = _git(self.repo, "diff", "--name-only", branch_point, "HEAD").split()
        self.assertIn("tools/x.py", stale)

    def test_an_event_that_is_not_a_pull_request_is_true_without_reading_git(self):
        def never(args, cwd):
            raise AssertionError("git must not be called")

        for env in ({"GITHUB_EVENT_NAME": "push"}, {"GITHUB_EVENT_NAME": "workflow_dispatch"},
                    {"GITHUB_EVENT_NAME": "pull_request_target"}, {"GITHUB_EVENT_NAME": ""}, {}):
            with self.subTest(env=env):
                self.assertEqual(cc.detect(env, cwd=self.repo, git=never)[0], "true")

    def test_a_failing_git_is_true(self):
        def broken(args, cwd):
            raise cc.DetectError("boom")

        self.assertEqual(cc.detect(self.env, cwd=self.repo, git=broken)[0], "true")
        self.assertEqual(cc.detect(self.env, cwd=self.repo, git=lambda a, c: 1 / 0)[0], "true")
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(cc.detect(self.env, cwd=Path(plain))[0], "true")

    def test_the_command_line_writes_the_output_file(self):
        self.merge({"docs/run.md": "new\n"})
        for event, expected in (("pull_request", "false"), ("push", "true")):
            with self.subTest(event=event), tempfile.TemporaryDirectory() as out:
                target = Path(out) / "github_output"
                env = {**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_OUTPUT": str(target)}
                done = fixtures.run([sys.executable, str(fixtures.REPO_ROOT / "tools" / "ci_changes.py"), "detect"],
                                    cwd=self.repo, env=env)
                self.assertEqual(target.read_text(), f"code={expected}\n")
                self.assertIn(f"code={expected}", done.stdout)


# ---------------------------------------------------------------------------
# require
# ---------------------------------------------------------------------------

class RequireTests(unittest.TestCase):
    def test_only_two_successes_and_a_boolean_pass(self):
        results = ("success", "failure", "cancelled", "skipped", "")
        outputs = ("true", "false", "", "maybe", "True")
        for changes, docs, code in itertools.product(results, results, outputs):
            expected = changes == "success" and docs == "success" and code in ("true", "false")
            with self.subTest(changes=changes, docs=docs, code=code):
                self.assertEqual(cc.require(changes, docs, code)[0], expected)

    def test_the_exit_status_of_the_command_line(self):
        def run(*argv):
            err, out = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                return cc.main(["require", *argv])

        self.assertEqual(run("--changes", "success", "--docs", "success", "--code", "false"), 0)
        self.assertEqual(run("--changes", "success", "--docs", "success", "--code", "true"), 0)
        self.assertEqual(run("--changes", "success", "--docs", "failure", "--code", "true"), 1)
        self.assertEqual(run("--changes", "failure", "--docs", "success", "--code", ""), 1)
        self.assertEqual(run("--changes", "success", "--docs", "success"), 1)
        self.assertEqual(run(), 1)


# ---------------------------------------------------------------------------
# The guard (D2): the scan
# ---------------------------------------------------------------------------

ROOT = fixtures.REPO_ROOT
SCAN_DIRS = ("tests", "scripts", "tools", "controller")
#: Frozen copies of released trees, run by no test of this repository.
SCAN_EXCLUDED_PREFIX = "tests/workflow_releases/"
WILDCARDS = "*?["
FILE_WRAPPERS = frozenset({"resolve", "absolute", "expanduser"})
PATH_CLASSES = frozenset({"Path", "PurePath", "PurePosixPath", "PosixPath"})


@dataclass(frozen=True)
class Chain:
    """A folded path: ``lit`` (constant segments only), ``expr`` (a root
    expression, written as text, then segments), ``rootval`` (a repository-root
    value, then segments) or ``dir`` (a directory below the root: never a
    candidate). ``level`` is the number of path components of a ``dir``."""

    kind: str
    root: str | None
    segs: tuple[str, ...] = ()
    level: int = 0


def _segments(text: str) -> tuple[str, ...]:
    return tuple(part for part in text.split("/") if part)


def _join(base: Chain, segs: tuple[str, ...]) -> Chain:
    if base.kind == "dir":
        return base
    return Chain(base.kind, base.root, base.segs + segs, base.level)


def _climb(base: Chain, steps: int) -> Chain:
    """``steps`` ``.parent`` steps from a repository-root value or a ``dir``."""
    if base.kind == "dir":
        level = base.level - steps
        return Chain("dir", None, (), level) if level > 0 else Chain("rootval", "<root>", (), level)
    if base.kind == "rootval":
        return Chain("rootval", "<root>", (), base.level - steps)
    return Chain("expr", f"{base.root}{'.parent' * steps}", (), 0)


class Scope:
    """The names one body binds. A name bound exactly once, to an expression,
    is resolvable; any other binding makes it unresolved."""

    def __init__(self, body: list[ast.stmt], parent: "Scope | None", params: tuple[str, ...] = ()):
        self.parent = parent
        self.bindings: dict[str, list[ast.AST | None]] = {}
        for name in params:
            self.bindings.setdefault(name, []).append(None)
        for stmt in body:
            self._collect(stmt)

    def _bind(self, name: str, value: ast.AST | None) -> None:
        self.bindings.setdefault(name, []).append(value)

    def _targets(self, target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._targets(item, None)
        elif isinstance(target, ast.Starred):
            self._targets(target.value, None)

    def _collect(self, node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self._bind(node.name, None)
            return
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._targets(target, node.value)
        elif isinstance(node, ast.AnnAssign):
            self._targets(node.target, node.value)
        elif isinstance(node, ast.AugAssign):
            self._targets(node.target, None)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._targets(node.target, None)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            self._targets(node.optional_vars, None)
        elif isinstance(node, ast.NamedExpr):
            self._targets(node.target, None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                self._bind((alias.asname or alias.name).split(".")[0], None)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self._bind(node.name, None)
        for child in ast.iter_child_nodes(node):
            self._collect(child)


class ModuleScan:
    """The candidate sites of one module: a path of the set (or a pattern that
    can match one) at a path position, with the root it was built from."""

    def __init__(self, rel: str, source: str):
        self.rel = rel
        self.tree = ast.parse(source)
        self.depth = len(Path(rel).parts)
        self.module_scope = Scope(self.tree.body, None)
        self.scopes: dict[int, Scope] = {}
        self.functions: dict[str, ast.FunctionDef] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.setdefault(node.name, node)
        self._helper_cache: dict[str, bool] = {}
        self.sites: list[dict] = []
        self._visit(self.tree, self.module_scope)

    # -- scopes ------------------------------------------------------------
    def _scope_for(self, func: ast.AST, parent: Scope) -> Scope:
        args = func.args
        names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
        names += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
        scope = self.scopes.get(id(func))
        if scope is None:
            scope = self.scopes[id(func)] = Scope(func.body, parent, tuple(names))
        return scope

    # -- folding -----------------------------------------------------------
    def _lookup(self, name: str, scope: Scope | None):
        while scope is not None:
            if name in scope.bindings:
                return scope, scope.bindings[name]
            scope = scope.parent
        return None, None

    def _is_helper(self, name: str, seen: frozenset) -> bool:
        """A module function whose ``return`` is a repository-root value."""
        if name in self._helper_cache:
            return self._helper_cache[name]
        func = self.functions.get(name)
        result = False
        if func is not None and name not in seen:
            scope = self._scope_for(func, self.module_scope)
            for node in ast.walk(func):
                if isinstance(node, ast.Return) and node.value is not None:
                    chain = self.fold(node.value, scope, seen | {name})
                    if chain is not None and chain.kind == "rootval":
                        result = True
        self._helper_cache[name] = result
        return result

    def fold(self, node: ast.AST, scope: Scope, seen: frozenset = frozenset()) -> Chain | None:
        if isinstance(node, ast.Constant):
            return Chain("lit", None, _segments(node.value)) if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return self._fold_name(node.id, scope, seen)
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                base = self.fold(node.value, scope, seen)
                if base is not None and base.kind in ("dir", "rootval"):
                    return _climb(base, 1)
            return Chain("expr", ast.unparse(node), ())
        if isinstance(node, ast.Subscript):
            if (isinstance(node.value, ast.Attribute) and node.value.attr == "parents"
                    and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int)):
                base = self.fold(node.value.value, scope, seen)
                if base is not None and base.kind in ("dir", "rootval"):
                    return _climb(base, node.slice.value + 1)
            return Chain("expr", ast.unparse(node), ())
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = self.fold(node.left, scope, seen), self.fold(node.right, scope, seen)
            if left is None or right is None or right.kind != "lit":
                return None
            return _join(left, right.segs)
        if isinstance(node, ast.Call):
            return self._fold_call(node, scope, seen)
        return Chain("expr", ast.unparse(node), ())

    def _fold_name(self, name: str, scope: Scope, seen: frozenset) -> Chain:
        owner, values = self._lookup(name, scope)
        if owner is not None and len(values) == 1 and values[0] is not None and (id(owner), name) not in seen:
            chain = self.fold(values[0], owner, seen | {(id(owner), name)})
            if chain is not None and (chain.kind in ("lit", "rootval", "dir") or chain.segs):
                return chain
        return Chain("expr", name, ())

    def _fold_args(self, base: Chain | None, args: list[ast.expr], scope: Scope, seen: frozenset) -> Chain | None:
        if base is None:
            return None
        for arg in args:
            part = self.fold(arg, scope, seen)
            if part is None or part.kind != "lit":
                return None
            base = _join(base, part.segs)
        return base

    def _fold_call(self, node: ast.Call, scope: Scope, seen: frozenset) -> Chain | None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        text = ast.unparse(func)
        if name in PATH_CLASSES and (isinstance(func, ast.Name) or text.startswith("pathlib.")):
            if not node.args:
                return Chain("lit", None, ())
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id == "__file__":
                base = Chain("dir", None, (), self.depth)
            else:
                base = self._unwrap_path_arg(first, scope, seen)
            return self._fold_args(base, node.args[1:], scope, seen)
        if text in ("os.path.join", "posixpath.join"):
            if not node.args:
                return None
            return self._fold_args(self._unwrap_path_arg(node.args[0], scope, seen), node.args[1:], scope, seen)
        if text in ("os.path.dirname", "os.path.abspath", "os.path.realpath", "os.fspath", "str") and node.args:
            base = self._unwrap_path_arg(node.args[0], scope, seen)
            if base is not None and text == "os.path.dirname" and base.kind in ("dir", "rootval"):
                return _climb(base, 1)
            return base
        if isinstance(func, ast.Attribute):
            if func.attr in FILE_WRAPPERS and not node.args:
                return self.fold(func.value, scope, seen)
            if func.attr == "joinpath":
                return self._fold_args(self.fold(func.value, scope, seen), node.args, scope, seen)
        if isinstance(func, ast.Name) and self._is_helper(func.id, seen):
            return Chain("rootval", "<root>", (), 0)
        return Chain("expr", ast.unparse(node), ())

    def _unwrap_path_arg(self, node: ast.expr, scope: Scope, seen: frozenset) -> Chain | None:
        if isinstance(node, ast.Name) and node.id == "__file__":
            return Chain("dir", None, (), self.depth)
        return self.fold(node, scope, seen)

    # -- sites -------------------------------------------------------------
    def _position(self, node: ast.AST, scope: Scope) -> tuple[Chain | None, str | None]:
        """The folded chain of a path position, and the glob kind."""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.fold(node, scope), None
        if not isinstance(node, ast.Call):
            return None, None
        func = node.func
        text = ast.unparse(func)
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in PATH_CLASSES or text in ("os.path.join", "posixpath.join") or (
                isinstance(func, ast.Attribute) and func.attr == "joinpath"):
            return self.fold(node, scope), None
        if isinstance(func, ast.Name) and func.id == "open" and node.args:
            return self.fold(node.args[0], scope), None
        if isinstance(func, ast.Attribute) and func.attr in ("glob", "rglob") and node.args:
            base = self.fold(func.value, scope)
            pattern = self.fold(node.args[0], scope)
            if base is None or pattern is None or pattern.kind != "lit":
                return None, None
            segs = pattern.segs if func.attr == "glob" else ("**",) + pattern.segs
            return _join(base, segs), func.attr
        return None, None

    def _visit(self, node: ast.AST, scope: Scope) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = self._scope_for(node, scope)
            for child in node.body:
                self._visit(child, inner)
            for expr in (*node.decorator_list, *node.args.defaults, *[d for d in node.args.kw_defaults if d]):
                self._visit(expr, scope)
            return
        chain, glob = self._position(node, scope)
        if chain is not None:
            self._record(node, chain, glob)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                for arg in node.args[1:]:
                    self._visit(arg, scope)
                for kw in node.keywords:
                    self._visit(kw.value, scope)
            return
        for child in ast.iter_child_nodes(node):
            self._visit(child, scope)

    def _record(self, node: ast.AST, chain: Chain, glob: str | None) -> None:
        if chain.kind == "dir":
            return
        if not touches_set(chain.segs, chain.kind):
            return
        self.sites.append({"module": self.rel, "line": node.lineno, "path": "/".join(chain.segs),
                           "root": chain.root if chain.kind == "expr" else None, "kind": chain.kind})

    # -- the reach of a repository-root value --------------------------------
    def reached(self) -> set[str]:
        """The expressions a repository-root value reaches, by the four ways
        of D2: assignment, a helper's return, an attribute, a call argument,
        and transitively."""
        reached: set[str] = set()
        params = {name: [a.arg for a in (*f.args.posonlyargs, *f.args.args)] for name, f in self.functions.items()}

        def is_root(node: ast.AST, scope: Scope) -> bool:
            chain = self.fold(node, scope)
            return (chain is not None and chain.kind == "rootval") or ast.unparse(node) in reached

        def walk(node: ast.AST, scope: Scope) -> bool:
            changed = False
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = self._scope_for(node, scope)
                return any([walk(child, inner) for child in node.body])
            if isinstance(node, ast.Assign) and is_root(node.value, scope):
                for target in node.targets:
                    text = ast.unparse(target) if isinstance(target, (ast.Name, ast.Attribute)) else None
                    if text and text not in reached:
                        reached.add(text)
                        changed = True
            if isinstance(node, ast.Call):
                callee = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else None)
                if callee in params:
                    names = params[callee]
                    if isinstance(node.func, ast.Attribute) and names and names[0] in ("self", "cls"):
                        names = names[1:]
                    for index, arg in enumerate(node.args):
                        if index < len(names) and is_root(arg, scope) and names[index] not in reached:
                            reached.add(names[index])
                            changed = True
                    wanted = set(params[callee])
                    for kw in node.keywords:
                        if kw.arg in wanted and is_root(kw.value, scope) and kw.arg not in reached:
                            reached.add(kw.arg)
                            changed = True
            for child in ast.iter_child_nodes(node):
                changed = walk(child, scope) or changed
            return changed

        while walk(self.tree, self.module_scope):
            pass
        return reached


def _top_level_members() -> tuple[str, ...]:
    """The names directly under ``docs/`` that are members of the set: every
    such file in the checkout, and two samples so a pattern such as ``*.md``
    still counts when the checkout lacks a file it would match."""
    found = {path.name for path in (ROOT / "docs").iterdir()
             if path.is_file() and cc.is_documentation(f"docs/{path.name}")}
    return tuple(sorted(found | {"x.md", "backlog.yaml"}))


def touches_set(segs: tuple[str, ...], kind: str = "expr") -> bool:
    """Whether a folded path, or a glob pattern, can name a member of the set.
    A bare parent such as ``docs`` is not a candidate."""
    if not segs:
        return False
    prefixes = [prefix.rstrip("/") for prefix, _ in cc.DOCS_PREFIXES]
    wild = next((i for i, seg in enumerate(segs) if seg == "**" or any(c in seg for c in WILDCARDS)), None)
    if wild is None:
        path = "/".join(segs)
        return cc.is_documentation(path) or path in prefixes
    literal, rest = segs[:wild], segs[wild:]
    lit_path = "/".join(literal)
    if any(lit_path == p or lit_path.startswith(p + "/") for p in prefixes):
        return True
    if literal == ("docs",):
        if rest[0] == "**" or len(rest) > 1:
            return rest[0] == "**" or any(fnmatch.fnmatch(p.split("/")[1], rest[0]) for p in prefixes)
        return any(fnmatch.fnmatch(name, rest[0]) for name in _top_level_members())
    if literal == () and kind == "rootval":
        # A glob over the repository root itself reaches the documentation.
        return rest[0] == "**" or fnmatch.fnmatch("docs", rest[0])
    return False


@dataclass
class Tables:
    gate_modules: tuple[str, ...]
    gate_tools: tuple[str, ...]
    mentions: tuple[tuple[str, str], ...]
    fixtures: tuple[tuple[str, str], ...]

    @classmethod
    def committed(cls) -> "Tables":
        return cls(tuple(m for m, _ in cc.GATE_MODULES), tuple(m for m, _ in cc.GATE_TOOLS),
                   tuple((m, p) for m, p, _ in cc.MENTIONS), tuple((m, e) for m, e, _ in cc.FIXTURES))


@dataclass
class Report:
    sites: list[dict]
    reads: list[dict]
    fixture_sites: list[dict]
    violations: list[dict]
    refused: list[tuple[str, str]]
    stale: list[tuple[str, str, str]]


def evaluate(sources: dict[str, str], tables: Tables) -> Report:
    """Scan ``sources`` (module path to text) and judge the result."""
    scans = {rel: ModuleScan(rel, text) for rel, text in sources.items()}
    sites = [site for scan in scans.values() for site in scan.sites]
    fixture_roots = {(m, e) for m, e in tables.fixtures}
    reads, fixture_sites, violations = [], [], []
    for site in sites:
        if site["kind"] == "expr" and (site["module"], site["root"]) in fixture_roots:
            fixture_sites.append(site)
            continue
        reads.append(site)
        allowed = (site["module"] in tables.gate_modules or site["module"] in tables.gate_tools
                   or (site["module"], site["path"]) in tables.mentions)
        if not allowed:
            violations.append(site)
    refused = []
    for module, expr in tables.fixtures:
        scan = scans.get(module)
        if scan is not None and expr in scan.reached():
            refused.append((module, expr))
    stale = []
    for module in tables.gate_modules:
        if module not in scans:
            stale.append(("gate module", module, ""))
    for tool in tables.gate_tools:
        if tool not in scans:
            stale.append(("gate tool", tool, ""))
    for module, path in tables.mentions:
        if not any(s["module"] == module and s["path"] == path for s in sites):
            stale.append(("mention", module, path))
    for module, expr in tables.fixtures:
        if not any(s["module"] == module and s["root"] == expr and s["kind"] == "expr" for s in sites):
            stale.append(("fixture", module, expr))
    return Report(sites, reads, fixture_sites, violations, refused, stale)


_REPO_SOURCES: dict[str, str] | None = None


def repository_sources() -> dict[str, str]:
    global _REPO_SOURCES
    if _REPO_SOURCES is None:
        found = {}
        for top in SCAN_DIRS:
            for path in sorted((ROOT / top).rglob("*.py")):
                rel = path.relative_to(ROOT).as_posix()
                if rel.startswith(SCAN_EXCLUDED_PREFIX) or "__pycache__" in path.parts:
                    continue
                found[rel] = path.read_text(encoding="utf-8")
        _REPO_SOURCES = found
    return _REPO_SOURCES


def describe(site: dict) -> str:
    return f"{site['module']}:{site['line']} reads {site['path']} (root {site['root'] or site['kind']})"


# ---------------------------------------------------------------------------
# The guard on the repository
# ---------------------------------------------------------------------------

class DocumentationSetGuardTests(unittest.TestCase):
    """D2: no module outside the documentation gate reads a path of the set."""

    @classmethod
    def setUpClass(cls):
        cls.report = evaluate(repository_sources(), Tables.committed())

    def test_no_module_outside_the_gate_reads_a_path_of_the_set(self):
        self.assertEqual(
            self.report.violations, [],
            "a module outside the documentation gate reads a path of the documentation set; "
            "remove that path from the set in tools/ci_changes.py (or add the module to the gate "
            "only by amending the plan):\n" + "\n".join(describe(s) for s in self.report.violations))

    def test_no_fixture_entry_is_reached_by_a_repository_root_value(self):
        self.assertEqual(self.report.refused, [],
                         "a repository-root value reaches a fixture-table root: that root is not temporary")

    def test_no_entry_is_stale(self):
        self.assertEqual(self.report.stale, [], "stale entries must be removed in the same change")

    def test_the_list_of_mentions_stays_short(self):
        self.assertLessEqual(len(cc.MENTIONS), 10,
                             "more than ten mentions: the plan is amended, there is no weaker guard")

    def test_every_table_entry_names_a_reason(self):
        for table in (cc.MENTIONS, cc.FIXTURES):
            for entry in table:
                self.assertTrue(entry[-1].strip(), entry)

    def test_the_gate_tools_are_named_not_discovered(self):
        for tool, _ in cc.GATE_TOOLS:
            self.assertTrue((ROOT / tool).is_file(), tool)

    def test_the_first_run_matches_the_expected_result(self):
        by_module: dict[str, int] = {}
        for site in self.report.fixture_sites:
            by_module[site["module"]] = by_module.get(site["module"], 0) + 1
        reads = [s for s in self.report.reads]
        self.assertEqual({s["module"] for s in reads}, {"tests/test_backlog.py"})
        self.assertEqual(len(reads), 5, [describe(s) for s in reads])
        self.assertEqual(len(by_module), 7, by_module)
        # The table is one entry per module and root expression, about ten
        # to twelve; it is counted apart from the list of mentions.
        self.assertLessEqual(len(cc.FIXTURES), 12)


# ---------------------------------------------------------------------------
# The scanner on small sources
# ---------------------------------------------------------------------------

def scan(source: str, module: str = "tests/test_snippet.py", *, fixtures_table=(), mentions=(),
         gate_modules=(), gate_tools=()) -> Report:
    return evaluate({module: textwrap.dedent(source)},
                    Tables(tuple(gate_modules), tuple(gate_tools), tuple(mentions), tuple(fixtures_table)))


class ScannerFlagsTests(unittest.TestCase):
    def assertFlagged(self, source: str, path: str, **kwargs):
        report = scan(source, **kwargs)
        self.assertIn(path, [s["path"] for s in report.violations], report.sites)

    def test_split_segments(self):
        self.assertFlagged("""
            from tests.fixtures import REPO_ROOT
            page = REPO_ROOT / "docs" / "items" / "x.md"
        """, "docs/items/x.md")

    def test_a_constant(self):
        self.assertFlagged("""
            from tests.fixtures import REPO_ROOT
            SOME_CONST = "docs/backlog.yaml"
            page = REPO_ROOT / SOME_CONST
        """, "docs/backlog.yaml")

    def test_an_alias(self):
        self.assertFlagged("""
            from tests.fixtures import REPO_ROOT
            DOCS = REPO_ROOT / "docs"
            page = DOCS / "run.md"
        """, "docs/run.md")

    def test_a_directory_alias_with_a_glob(self):
        self.assertFlagged("""
            from tests.fixtures import REPO_ROOT
            GUIDE = REPO_ROOT / "docs" / "items"
            pages = list(GUIDE.glob("*.md"))
        """, "docs/items/*.md")

    def test_a_parameter_root(self):
        self.assertFlagged("""
            def page(root):
                return root / "docs" / "run.md"
            page(REPO_ROOT)
        """, "docs/run.md")

    def test_an_attribute_root(self):
        self.assertFlagged("""
            class T:
                def setUp(self):
                    self.root = REPO_ROOT
                def test(self):
                    return (self.root / "docs" / "run.md").read_text()
        """, "docs/run.md")

    def test_a_root_helper(self):
        self.assertFlagged("""
            from pathlib import Path
            def _repo_root():
                return Path(__file__).resolve().parent.parent
            def f():
                return _repo_root() / "docs" / "run.md"
        """, "docs/run.md")

    def test_a_file_root_even_when_the_module_lists_a_fixture_entry(self):
        report = scan("""
            from pathlib import Path
            def _repo_root():
                return Path(__file__).resolve().parent.parent
            def f():
                return _repo_root() / "docs" / "run.md"
            def g(root):
                return root / "docs" / "run.md"
        """, fixtures_table=(("tests/test_snippet.py", "root"),))
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(len(report.fixture_sites), 1)

    def test_a_path_call(self):
        self.assertFlagged("""
            from pathlib import Path
            page = Path(REPO_ROOT, "docs/run.md")
        """, "docs/run.md")

    def test_os_path_join(self):
        self.assertFlagged("""
            import os
            page = os.path.join(REPO_ROOT, "docs", "run.md")
        """, "docs/run.md")

    def test_joinpath(self):
        self.assertFlagged("""
            page = REPO_ROOT.joinpath("docs", "run.md")
        """, "docs/run.md")

    def test_open(self):
        self.assertFlagged("""
            PAGE = "docs/run.md"
            handle = open(PAGE)
        """, "docs/run.md")

    def test_rglob_over_the_docs_directory(self):
        self.assertFlagged("""
            pages = (REPO_ROOT / "docs").rglob("*.md")
        """, "docs/**/*.md")

    def test_the_same_literal_inside_path_is_a_path(self):
        self.assertFlagged("""
            from pathlib import Path
            page = Path("docs/run.md")
        """, "docs/run.md")

    def test_a_chain_from_the_module_file(self):
        self.assertFlagged("""
            from pathlib import Path
            page = Path(__file__).resolve().parent.parent / "docs" / "run.md"
        """, "docs/run.md")
        self.assertFlagged("""
            from pathlib import Path
            ROOT = Path(__file__).resolve().parents[1]
            page = ROOT / "docs" / "items" / "x.md"
        """, "docs/items/x.md")


class ScannerAcceptsTests(unittest.TestCase):
    def test_a_fixture_writer_whose_root_is_on_the_table(self):
        report = scan("""
            def make(root):
                (root / "docs" / "items" / "x.md").write_text("x")
        """, fixtures_table=(("tests/test_snippet.py", "root"),))
        self.assertEqual(report.violations, [])
        self.assertEqual(len(report.fixture_sites), 1)

    def test_a_whole_literal_outside_a_path_position(self):
        report = scan("""
            DECLARED = {"docs/run.md": 1, "docs/backlog.yaml": 2}
            names = ["docs/run.md"]
            message = "see docs/run.md"
            check("docs/run.md")
        """)
        self.assertEqual(report.sites, [])

    def test_a_bare_parent_is_not_a_candidate(self):
        report = scan("""
            DOCS = REPO_ROOT / "docs"
            GUIDE = REPO_ROOT / "docs" / "guide" / "commands.md"
            ARCHIVE = REPO_ROOT / "docs" / "roadmap-archive.md"
        """)
        self.assertEqual(report.sites, [])

    def test_a_directory_name_is_never_a_candidate(self):
        report = scan("""
            from pathlib import Path
            SCRIPTS = Path(__file__).resolve().parent
            page = SCRIPTS / "docs" / "run.md"
        """, module="scripts/x_test.py")
        self.assertEqual(report.sites, [])

    def test_the_gate_tools_and_modules_are_allowed_readers(self):
        source = """
            page = REPO_ROOT / "docs" / "run.md"
        """
        self.assertEqual(scan(source, module="tests/test_docs.py", gate_modules=("tests/test_docs.py",)).violations, [])
        self.assertEqual(scan(source, module="tools/check_docs.py", gate_tools=("tools/check_docs.py",)).violations, [])

    def test_a_named_mention_is_allowed_for_that_path_only(self):
        source = """
            a = REPO_ROOT / "docs" / "run.md"
            b = REPO_ROOT / "docs" / "install.md"
        """
        report = scan(source, mentions=(("tests/test_snippet.py", "docs/run.md"),))
        self.assertEqual([s["path"] for s in report.violations], ["docs/install.md"])

    def test_a_name_bound_twice_is_unresolved(self):
        report = scan("""
            NAME = "docs/run.md"
            NAME = "other"
            page = REPO_ROOT / NAME
        """)
        self.assertEqual(report.sites, [])

    def test_a_non_constant_segment_is_not_seen(self):
        report = scan("""
            for rel in ("run.md",):
                page = REPO_ROOT / "docs" / rel
        """)
        self.assertEqual(report.sites, [])


class StaleEntryTests(unittest.TestCase):
    SOURCE = """
        def make(root):
            (root / "docs" / "run.md").write_text("x")
        page = REPO_ROOT / "docs" / "install.md"
    """
    MODULE = "tests/test_snippet.py"

    def kinds(self, **kwargs) -> set[str]:
        return {kind for kind, *_ in scan(self.SOURCE, **kwargs).stale}

    def test_a_live_set_of_entries_is_not_stale(self):
        report = scan(self.SOURCE, fixtures_table=((self.MODULE, "root"),),
                      mentions=((self.MODULE, "docs/install.md"),), gate_modules=(self.MODULE,))
        self.assertEqual(report.stale, [])

    def test_a_gate_module_whose_file_is_gone_is_stale(self):
        self.assertEqual(self.kinds(gate_modules=("tests/test_gone.py",)), {"gate module"})

    def test_a_gate_tool_whose_file_is_gone_is_stale(self):
        self.assertEqual(self.kinds(gate_tools=("tools/gone.py",)), {"gate tool"})

    def test_a_mention_the_scan_no_longer_finds_is_stale(self):
        self.assertEqual(self.kinds(mentions=((self.MODULE, "docs/gone.md"),)), {"mention"})

    def test_a_fixture_entry_the_scan_no_longer_finds_is_stale(self):
        self.assertEqual(self.kinds(fixtures_table=((self.MODULE, "other"),)), {"fixture"})

    def test_the_gate_tools_are_not_stale_for_having_no_candidate(self):
        # Present in the scan, no candidate read in them: not refused.
        sources = {"tools/check_docs.py": "x = 1\n", "scripts/workflow_backlog.py": "y = 2\n"}
        report = evaluate(sources, Tables((), ("tools/check_docs.py", "scripts/workflow_backlog.py"), (), ()))
        self.assertEqual(report.stale, [])

    def test_the_committed_gate_tools_are_in_the_scan(self):
        for tool, _ in cc.GATE_TOOLS:
            self.assertIn(tool, repository_sources())


class FixtureRefusalTests(unittest.TestCase):
    MODULE = "scripts/x_test.py"

    def refused(self, source: str, expr: str = "root") -> bool:
        report = scan(source, module=self.MODULE, fixtures_table=((self.MODULE, expr),))
        return (self.MODULE, expr) in report.refused

    def test_a_name_bound_from_the_root_chain_is_refused(self):
        self.assertTrue(self.refused("""
            from pathlib import Path
            root = Path(__file__).resolve().parent.parent
            (root / "docs" / "run.md").read_text()
        """))

    def test_the_helper_form_is_refused(self):
        self.assertTrue(self.refused("""
            from pathlib import Path
            def _repo_root():
                return Path(__file__).resolve().parent.parent
            root = _repo_root()
            (root / "docs" / "run.md").read_text()
        """))

    def test_an_attribute_is_refused(self):
        self.assertTrue(self.refused("""
            from pathlib import Path
            def _repo_root():
                return Path(__file__).resolve().parent.parent
            class T:
                def setUp(self):
                    self.root = _repo_root()
                def test(self):
                    (self.root / "docs" / "run.md").read_text()
        """, expr="self.root"))

    def test_a_root_value_passed_into_the_table_root_is_refused(self):
        self.assertTrue(self.refused("""
            from pathlib import Path
            ROOT = Path(__file__).resolve().parent.parent
            def build(root):
                (root / "docs" / "run.md").write_text("x")
            build(ROOT)
        """))
        self.assertTrue(self.refused("""
            from pathlib import Path
            def build(root):
                (root / "docs" / "run.md").write_text("x")
            build(root=Path(__file__).resolve().parents[1])
        """))

    def test_a_transitive_assignment_is_refused(self):
        self.assertTrue(self.refused("""
            from pathlib import Path
            base = Path(__file__).resolve().parent.parent
            root = base
            (root / "docs" / "run.md").read_text()
        """))

    def test_a_directory_name_is_not_refused(self):
        self.assertFalse(self.refused("""
            from pathlib import Path
            SCRIPTS = Path(__file__).resolve().parent
            def make(root):
                (root / "docs" / "run.md").write_text("x")
        """))

    def test_a_module_that_only_reads_through_its_helper_is_not_refused(self):
        source = """
            from pathlib import Path
            def _repo_root():
                return Path(__file__).resolve().parent.parent
            def make(root):
                (root / "docs" / "run.md").write_text("x")
            def real():
                return _repo_root() / "docs" / "run.md"
        """
        self.assertFalse(self.refused(source))
        report = scan(source, module=self.MODULE, fixtures_table=((self.MODULE, "root"),))
        self.assertEqual([s["path"] for s in report.violations], ["docs/run.md"])

    def test_the_committed_fixture_entries_are_none_refused(self):
        report = evaluate(repository_sources(), Tables.committed())
        self.assertEqual(report.refused, [])

    def test_the_obligations_helper_is_a_read_root_not_on_the_table(self):
        module = "scripts/workflow_state_completion_obligations_test.py"
        scanned = ModuleScan(module, repository_sources()[module])
        self.assertTrue(scanned._is_helper("_repo_root", frozenset()))
        self.assertNotIn("_repo_root", {e for m, e, _ in cc.FIXTURES if m == module})


class TopLevelGlobTests(unittest.TestCase):
    """LIR-R1-001/I1: a constant glob directly under docs/ is a reader of
    every member it matches, not only of two sample names."""

    def test_a_constant_glob_that_reaches_a_member_is_flagged(self):
        for pattern in ("ROAD*.md", "[A-Z]*.md", "ROADMA?.md", "*.md"):
            with self.subTest(pattern=pattern):
                report = scan(f"""
                    from pathlib import Path
                    ROOT = Path(__file__).resolve().parent.parent
                    def read():
                        return list((ROOT / "docs").glob("{pattern}"))
                """)
                self.assertTrue(report.violations, report.sites)

    def test_a_glob_that_reaches_no_member_is_not_flagged(self):
        for pattern in ("ACTIVE*.md", "ZZZ*.md"):
            with self.subTest(pattern=pattern):
                self.assertFalse(touches_set(("docs", pattern)))


class TouchesSetTests(unittest.TestCase):
    def test_paths_and_patterns(self):
        for segs in (("docs", "run.md"), ("docs", "items", "x.md"), ("docs", "items"), ("docs", "backlog.yaml"),
                     ("docs", "items", "*.md"), ("docs", "*.md"), ("docs", "**", "*.md"), ("docs", "*"),
                     ):
            with self.subTest(segs=segs):
                self.assertTrue(touches_set(segs))
        for segs in (("**", "*.md"), ("*", "run.md")):
            with self.subTest(segs=segs, kind="rootval"):
                self.assertTrue(touches_set(segs, "rootval"))
            with self.subTest(segs=segs, kind="expr"):
                self.assertFalse(touches_set(segs, "expr"))
        for segs in ((), ("docs",), ("docs", "guide", "commands.md"), ("docs", "guide", "*.md"),
                     ("docs", "adr", "*.md"), ("tools", "x.py"), ("docs", "roadmap-archive.md"),
                     ("scripts", "*.py")):
            with self.subTest(segs=segs):
                self.assertFalse(touches_set(segs))


if __name__ == "__main__":
    unittest.main()
