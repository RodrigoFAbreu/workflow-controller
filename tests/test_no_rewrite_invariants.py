"""Static guards for I2 ("never rewrite") and I3' ("merge only the
accepted head, only through GitHub") (``workflow-controller-trunk-branch-
pr-release-orchestration`` CP3; narrowed by ``workflow-controller-auto-
merge-release-wait`` CP2).

``tests/test_write_containment.py`` cannot see what a subprocess writes, so
target-repository Git and GitHub mutation get their own source scan. Both
scans read the **argv literals** in ``controller/*.py`` -- list and tuple
literals holding string constants -- never identifiers or prose:

- the Git scan fails on ``--force``/``--force-with-lease``, a
  ``+``-prefixed refspec, ``reset --hard``, ``rebase``, ``commit --amend``,
  ``branch -D``/``-d``, ``tag -f``/``-d``, ``push --delete`` and
  ``update-ref -d``, and on any mutating Git subcommand outside
  ``controller/gitrepo.py``;
- the forge scan fails on the element pairs ``"pr", "merge"``, ``"pr",
  "close"`` and ``"release", "delete"``, on ``"--clobber"``, and on ``"api"``
  as the element immediately following ``"gh"``. The one exception is
  ``"pr", "merge"`` in ``controller/forge.py``, and there only in a **list**
  literal that also holds ``"--squash"`` and ``"--match-head-commit"`` and
  none of ``"--auto"``, ``"--disable-auto"``, ``"--admin"`` or
  ``"--delete-branch"``. In ``controller/forge.py`` the scan also fails on
  a ``"merge"`` that is not a plain element of such a list: the constant
  anywhere else (a variable, a tuple, a call argument), or a string that
  concatenation, ``%``, ``str.format``, ``str.join`` or an f-string of
  constants builds containing the word.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONTROLLER_DIR = Path(__file__).resolve().parent.parent / "controller"
GIT_BOUNDARY = "gitrepo.py"

#: Git subcommands that change a ref, the index, the working tree or a
#: remote. Only ``controller/gitrepo.py`` may spell them.
MUTATING_GIT_SUBCOMMANDS = frozenset({
    "push", "fetch", "pull", "switch", "checkout", "merge", "tag", "commit", "branch", "update-ref",
    "reset", "rebase", "cherry-pick", "revert", "am", "restore", "stash", "clean", "rm", "mv",
})
#: ``(subcommand, flag)``: the flag anywhere after the subcommand refuses.
_FORBIDDEN_GIT_FLAGS = (
    ("reset", "--hard"), ("commit", "--amend"), ("branch", "-D"), ("branch", "-d"),
    ("tag", "-f"), ("tag", "-d"), ("push", "--delete"), ("update-ref", "-d"),
)
_FORBIDDEN_GH_PAIRS = (("pr", "merge"), ("pr", "close"), ("release", "delete"))
#: The one file that may spell ``gh pr merge``, and the shape it must have.
FORGE_BOUNDARY = "forge.py"
_MERGE_REQUIRED = ("--squash", "--match-head-commit")
_MERGE_FORBIDDEN = ("--auto", "--disable-auto", "--admin", "--delete-branch")


def _argv_nodes(source: str, filename: str) -> list[tuple[ast.List | ast.Tuple, list[str | None]]]:
    found = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, (ast.List, ast.Tuple)):
            elements = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None
                        for e in node.elts]
            if any(e is not None for e in elements):
                found.append((node, elements))
    return found


def argv_literals(source: str, filename: str = "<source>") -> list[tuple[int, list[str | None]]]:
    """``(line, elements)`` for every list/tuple literal holding at least one
    string constant; a non-constant element is ``None``."""
    return [(node.lineno, elements) for node, elements in _argv_nodes(source, filename)]


def _git_subcommand(elements: list[str | None]) -> str | None:
    """The Git subcommand an argv literal spells: the element after
    ``"git"`` (skipping ``-C <path>``), or the literal's first element when
    it is passed to a ``git`` helper as the argument list."""
    if "git" in elements:
        i = elements.index("git") + 1
        while i < len(elements) and elements[i] == "-C":
            i += 2
        return elements[i] if i < len(elements) else None
    return elements[0] if elements else None


def git_violations(source: str, filename: str) -> list[str]:
    problems = []
    boundary = Path(filename).name == GIT_BOUNDARY
    for line, elements in argv_literals(source, filename):
        where = f"{filename}:{line}"
        for i, element in enumerate(elements):
            if element is None:
                continue
            if element.startswith("--force"):
                problems.append(f"{where}: {element}")
            if element.startswith("+") and (":" in element or element.startswith("+refs/")):
                problems.append(f"{where}: forced refspec {element}")
            if element in ("rebase", "--amend"):
                problems.append(f"{where}: {element}")
        for subcommand, flag in _FORBIDDEN_GIT_FLAGS:
            if subcommand in elements and flag in elements[elements.index(subcommand) + 1:]:
                problems.append(f"{where}: {subcommand} {flag}")
        subcommand = _git_subcommand(elements)
        if not boundary and subcommand in MUTATING_GIT_SUBCOMMANDS:
            problems.append(f"{where}: mutating git {subcommand} outside {GIT_BOUNDARY}")
    return problems


def allowed_merge_argv(node: ast.AST) -> bool:
    """Whether ``node`` is the one admitted ``gh pr merge`` argv: a list
    literal holding ``"pr", "merge"`` with ``--squash`` and
    ``--match-head-commit`` and none of the forbidden flags."""
    if not isinstance(node, ast.List):
        return False
    elements = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None
                for e in node.elts]
    return (("pr", "merge") in zip(elements, elements[1:])
            and all(flag in elements for flag in _MERGE_REQUIRED)
            and not any(flag in elements for flag in _MERGE_FORBIDDEN))


def _folded(node: ast.AST) -> str | None:
    """The string a constant expression builds (concatenation, ``%``,
    ``str.format``, ``str.join`` of a literal, an f-string), or ``None``
    when any part is not a constant."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left, right = _folded(node.left), _folded(node.right)
        if isinstance(node.op, ast.Add):
            return None if left is None or right is None else left + right
        if left is not None and isinstance(node.right, (ast.Tuple, ast.List)):
            parts = [_folded(e) for e in node.right.elts]
            return None if None in parts else left % tuple(parts)
        return None if left is None or right is None else left % right
    if isinstance(node, ast.JoinedStr):
        parts = [_folded(v.value if isinstance(v, ast.FormattedValue) else v) for v in node.values]
        return None if None in parts else "".join(parts)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("format", "join") and not node.keywords):
        receiver = _folded(node.func.value)
        if receiver is None:
            return None
        if node.func.attr == "join":
            if len(node.args) != 1 or not isinstance(node.args[0], (ast.List, ast.Tuple)):
                return None
            parts = [_folded(e) for e in node.args[0].elts]
            return None if None in parts else receiver.join(parts)
        parts = [_folded(a) for a in node.args]
        return None if None in parts else receiver.format(*parts)
    return None


def built_merge_spellings(source: str, filename: str) -> list[str]:
    """Every ``"merge"`` in ``source`` that is not a plain element of an
    :func:`allowed_merge_argv` list: the bare constant elsewhere, or a
    built string containing the word. Docstrings are prose, not argv."""
    tree = ast.parse(source, filename=filename)
    allowed: set[int] = set()
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if allowed_merge_argv(node):
            allowed.update(id(e) for e in node.elts if isinstance(e, ast.Constant))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant):
            docstrings.add(id(node.body[0].value))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if node.value == "merge" and id(node) not in allowed:
                problems.append(f"{filename}:{node.lineno}: \"merge\" outside the admitted gh pr merge argv")
        elif isinstance(node, (ast.BinOp, ast.JoinedStr, ast.Call)) and id(node) not in docstrings:
            built = _folded(node)
            if built is not None and "merge" in built.replace(",", " ").split():
                problems.append(f"{filename}:{node.lineno}: a built \"merge\" spelling ({built!r})")
    return problems


def forge_violations(source: str, filename: str) -> list[str]:
    problems = []
    boundary = Path(filename).name == FORGE_BOUNDARY
    if boundary:
        problems += built_merge_spellings(source, filename)
    for node, elements in _argv_nodes(source, filename):
        where = f"{filename}:{node.lineno}"
        pairs = list(zip(elements, elements[1:]))
        for pair in _FORBIDDEN_GH_PAIRS:
            if pair in pairs and not (boundary and pair == ("pr", "merge") and allowed_merge_argv(node)):
                problems.append(f"{where}: gh {' '.join(pair)}")
        if ("gh", "api") in pairs:
            problems.append(f"{where}: gh api")
        if "--clobber" in elements:
            problems.append(f"{where}: --clobber")
        for flag in ("--auto", "--disable-auto"):
            if flag in elements:
                problems.append(f"{where}: {flag}")
    return problems


def _controller_sources() -> list[tuple[str, str]]:
    return [(str(p.relative_to(CONTROLLER_DIR.parent)), p.read_text())
            for p in sorted(CONTROLLER_DIR.glob("*.py"))]


class ControllerScanTest(unittest.TestCase):
    def test_no_rewriting_git_spelling_and_mutation_only_in_gitrepo(self) -> None:
        problems = [p for name, text in _controller_sources() for p in git_violations(text, name)]
        self.assertEqual(problems, [])

    def test_no_merge_close_delete_clobber_or_api_gh_spelling(self) -> None:
        problems = [p for name, text in _controller_sources() for p in forge_violations(text, name)]
        self.assertEqual(problems, [])

    def test_forge_spells_exactly_one_admitted_merge(self) -> None:
        # Guards the narrowed scan: the exception is used once, by
        # ``GhForge.merge_squash``, and nowhere else.
        tree = ast.parse((CONTROLLER_DIR / FORGE_BOUNDARY).read_text())
        self.assertEqual(len([n for n in ast.walk(tree) if allowed_merge_argv(n)]), 1)
        others = [name for name, text in _controller_sources() if Path(name).name != FORGE_BOUNDARY
                  and any(allowed_merge_argv(n) for n in ast.walk(ast.parse(text)))]
        self.assertEqual(others, [])

    def test_gitrepo_does_spell_its_mutations(self) -> None:
        # Guards the scan itself: were argv extraction broken, the tests
        # above would pass vacuously.
        text = (CONTROLLER_DIR / GIT_BOUNDARY).read_text()
        spelled = {_git_subcommand(e) for _, e in argv_literals(text)}
        self.assertTrue({"push", "fetch", "switch", "merge", "tag"} <= spelled, spelled)
        self.assertTrue(git_violations(text, "controller/elsewhere.py"))


PLANTED = '''\
"""Never run gh api, gh pr merge or git push --force from here."""
import subprocess

api = "https://api.github.com"
merge = "pr merge"


def bad():
    subprocess.run(["gh", "api", "repos/x/y"])
    subprocess.run(["gh", "pr", "merge", "1", "--repo", "x/y"])
    subprocess.run(["gh", "release", "upload", "v1", "f", "--clobber"])
    argv = ("release", "delete", "v1")
    subprocess.run(["git", "-C", root, "push", "--force", "origin", "main"])
    subprocess.run(["git", "push", "origin", "+refs/heads/main:refs/heads/main"])
    _git(root, ["reset", "--hard", "HEAD"])
    _git(root, ["tag", "-a", "-f", "v1"])
    _git(root, ["switch", "main"])


def fine(api):
    subprocess.run(["gh", "pr", "view", "1"])
    subprocess.run(["git", "-C", root, "rev-parse", "HEAD"])
    return api.merge
'''


class PlantedFixtureTest(unittest.TestCase):
    def test_the_forge_scan_flags_argv_literals_only(self) -> None:
        problems = forge_violations(PLANTED, "planted.py")
        self.assertEqual(sorted(p.split(": ", 1)[1] for p in problems),
                         ["--clobber", "gh api", "gh pr merge", "gh release delete"])
        self.assertEqual({int(p.split(":")[1]) for p in problems}, {9, 10, 11, 12})

    def test_the_git_scan_flags_rewrites_and_misplaced_mutations(self) -> None:
        problems = git_violations(PLANTED, "planted.py")
        text = "\n".join(problems)
        for expected in ("--force", "forced refspec +refs/heads/main:refs/heads/main", "reset --hard",
                         "tag -f", "mutating git switch outside", "mutating git push outside"):
            self.assertIn(expected, text)
        # Only ``bad()``'s literals (lines 13-17); never the prose, the
        # identifiers or ``fine()``.
        self.assertEqual({int(p.split(":")[1]) for p in problems}, {13, 14, 15, 16, 17})


ADMITTED = '''\
"""The forge. It merges a pull request only bound to its head."""


def merge_squash(self, number, head, subject, body):
    """Squash merge, never a later merge."""
    self._ok(["pr", "merge", str(number), "--squash", "--match-head-commit", head,
              "--subject", subject, "--body", body])
    return {"fields": "mergedAt,mergeCommit,mergeStateStatus", "message": "unexpected merge commit"}
'''

_ADMITTED_ARGV = ('["pr", "merge", str(number), "--squash", "--match-head-commit", head,\n'
                  '              "--subject", subject, "--body", body]')


class MergeShapeTest(unittest.TestCase):
    """The narrowed I3' guard, both ways, over synthetic sources."""

    def scan(self, source: str, filename: str = "controller/forge.py") -> list[str]:
        return forge_violations(source, filename)

    def with_argv(self, argv: str) -> str:
        self.assertIn(_ADMITTED_ARGV, ADMITTED)
        return ADMITTED.replace(_ADMITTED_ARGV, argv)

    def test_the_admitted_shape_passes_in_forge_only(self) -> None:
        self.assertEqual(self.scan(ADMITTED), [])
        self.assertEqual([p.split(": ", 1)[1] for p in self.scan(ADMITTED, "controller/milestone_branch.py")],
                         ["gh pr merge"])

    def test_a_bare_merge_still_fails_in_forge(self) -> None:
        for argv in ('["gh", "pr", "merge", "1"]', '["pr", "merge", str(number), "--squash"]',
                     '["pr", "merge", str(number), "--match-head-commit", head]',
                     '("pr", "merge", str(number), "--squash", "--match-head-commit", head)'):
            with self.subTest(argv=argv):
                problems = self.scan(self.with_argv(argv))
                self.assertIn("gh pr merge", "\n".join(problems))

    def test_a_forbidden_flag_fails_the_admitted_shape(self) -> None:
        for flag in ("--auto", "--disable-auto", "--admin", "--delete-branch"):
            with self.subTest(flag=flag):
                argv = _ADMITTED_ARGV.replace('"--body", body]', f'"--body", body, "{flag}"]')
                self.assertIn("gh pr merge", "\n".join(self.scan(self.with_argv(argv))))

    def test_auto_merge_flags_fail_anywhere(self) -> None:
        for flag in ("--auto", "--disable-auto"):
            with self.subTest(flag=flag):
                source = f'run(["gh", "pr", "view", "1", "{flag}"])\n'
                self.assertEqual([p.split(": ", 1)[1] for p in self.scan(source, "controller/other.py")], [flag])

    def test_a_built_merge_fails_in_forge(self) -> None:
        for built in ('"mer" + "ge"', '"%sge" % "mer"', '"%s%s" % ("mer", "ge")', '"{}ge".format("mer")',
                      '"".join(["mer", "ge"])', 'f"{\'mer\'}ge"', '"pr " + "merge"'):
            with self.subTest(built=built):
                source = f"VERB = {built}\n\n\ndef bad(self):\n    self._ok([\"pr\", VERB, \"1\"])\n"
                problems = self.scan(source)
                self.assertTrue(problems, built)
                self.assertTrue(all(":1: " in p for p in problems), problems)

    def test_a_merge_constant_outside_the_admitted_list_fails_in_forge(self) -> None:
        for source in ('VERB = "merge"\n', 'run(("pr", "merge", "1", "--squash", "--match-head-commit", h))\n',
                       'run(["pr"] + ["merge"] + ["--squash", "--match-head-commit", h])\n',
                       ADMITTED + '\nEXTRA = ["merge"]\n'):
            with self.subTest(source=source):
                self.assertTrue(self.scan(source))

    def test_prose_and_field_names_are_not_argv(self) -> None:
        # The admitted source's docstrings and its "mergeCommit"-style
        # constants pass (test_the_admitted_shape_passes_in_forge_only); a
        # merge word in a plain message constant is prose too.
        self.assertEqual(self.scan('MESSAGE = "gh pr merge failed"\n'), [])


if __name__ == "__main__":
    unittest.main()
