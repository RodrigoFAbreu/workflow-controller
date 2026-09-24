"""Static guards for I2 ("never rewrite") and I3 ("never merge to trunk")
(``workflow-controller-trunk-branch-pr-release-orchestration`` CP3).

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
  as the element immediately following ``"gh"``.
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


def argv_literals(source: str, filename: str = "<source>") -> list[tuple[int, list[str | None]]]:
    """``(line, elements)`` for every list/tuple literal holding at least one
    string constant; a non-constant element is ``None``."""
    found = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, (ast.List, ast.Tuple)):
            elements = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None
                        for e in node.elts]
            if any(e is not None for e in elements):
                found.append((node.lineno, elements))
    return found


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


def forge_violations(source: str, filename: str) -> list[str]:
    problems = []
    for line, elements in argv_literals(source, filename):
        where = f"{filename}:{line}"
        pairs = list(zip(elements, elements[1:]))
        for pair in _FORBIDDEN_GH_PAIRS:
            if pair in pairs:
                problems.append(f"{where}: gh {' '.join(pair)}")
        if ("gh", "api") in pairs:
            problems.append(f"{where}: gh api")
        if "--clobber" in elements:
            problems.append(f"{where}: --clobber")
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


if __name__ == "__main__":
    unittest.main()
