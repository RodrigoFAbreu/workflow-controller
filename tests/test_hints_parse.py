"""I10: every command line the Controller prints for an operator to run
parses under the live parser (``workflow-controller-settings-and-telemetry``
CP5, Design E.1).

Two halves. The scan reads every string and f-string in ``controller/``
(docstrings aside) holding ``workflow-controller <subcommand or option>``,
renders each interpolation from a table of sample values -- an
interpolation the table does not know fails the test, so a new hint has to
be added here -- and runs the command, up to its closing backtick or the end
of the string, through ``build_parser().parse_args``. The builders half
calls the hint-building functions themselves, with a repository path that
needs quoting.
"""

from __future__ import annotations

import ast
import contextlib
import io
import itertools
import re
import shlex
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli, decision, job, milestone_branch  # noqa: E402

CONTROLLER_DIR = REPO_ROOT / "controller"

#: Each interpolation found inside a hint, by its source text, with every
#: value it can take (a tuple of alternatives is tried exhaustively).
SAMPLES: dict[str, tuple[str, ...]] = {
    "runtime_root": ("/tmp/runtime",),
    "repo": ("/tmp/repo",),
    "root": ("/tmp/repo",),
    "repo_root": ("/tmp/repo",),
    "target_repo": ("/tmp/repo",),
    "event.get('target_repo') or '<repo>'": ("/tmp/repo",),
    "last['run_id']": ("run-1",),
    "job_id": ("job-1",),
    "flag": ("", " --acknowledge-unverifiable-worker"),
    "work_item_id": ("wi-1",),
    "shlex.quote(work_item_id)": ("wi-1",),
    "shlex.quote(str(root))": ("'/tmp/my repo'",),
    "shlex.quote(repository)": ("'/tmp/my repo'",),
    "disposition": (milestone_branch.NEW_PR, milestone_branch.ABANDON),
}

#: Words after ``workflow-controller`` that make it prose, not a command.
PROSE = frozenset({"package"})

_HINT_RE = re.compile(r"workflow-controller (--|[a-z])")
_TOKEN_RE = re.compile(r"\x00(\d+)\x00")


def _docstring_ids(tree: ast.AST) -> set[int]:
    return {id(node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}


def _templates(path: Path):
    """``(lineno, template, expressions)`` for each string in ``path`` that
    holds a hint: interpolations become ``\\x00N\\x00`` tokens indexing
    ``expressions``. Nested string parts of an f-string are skipped, since
    the f-string itself is scanned."""
    tree = ast.parse(path.read_text())
    docstrings = _docstring_ids(tree)
    nested = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text, expressions = "", []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    text += part.value
                else:
                    text += f"\x00{len(expressions)}\x00"
                    expressions.append(ast.unparse(part.value))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings and id(node) not in nested):
            text, expressions = node.value, []
        else:
            continue
        if _HINT_RE.search(text):
            yield node.lineno, text, expressions


def _commands(template: str) -> list[str]:
    """Each hint in ``template``: from ``workflow-controller`` to the next
    backtick or the end of the string, prose mentions left out."""
    found = []
    for match in _HINT_RE.finditer(template):
        rest = template[match.start():]
        command = rest.split("`", 1)[0].rstrip(" .;")
        if command.split()[1] not in PROSE:
            found.append(command)
    return found


def _renderings(command: str, expressions: list[str]) -> list[str]:
    indices = [int(i) for i in _TOKEN_RE.findall(command)]
    unknown = [expressions[i] for i in indices if expressions[i] not in SAMPLES]
    if unknown:
        raise AssertionError(f"no sample value for {unknown!r}: add it to SAMPLES")
    renderings = []
    for choice in itertools.product(*(SAMPLES[expressions[i]] for i in indices)):
        values = dict(zip(indices, choice))
        renderings.append(_TOKEN_RE.sub(lambda m: values[int(m.group(1))], command))
    return renderings


class HintsParseTest(unittest.TestCase):
    def assert_parses(self, command: str, where: str) -> None:
        argv = shlex.split(command)
        self.assertEqual(argv[0], "workflow-controller", where)
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                cli.build_parser().parse_args(argv[1:])
        except SystemExit as exc:
            self.fail(f"{where}: {command!r} does not parse (exit {exc.code}): {stderr.getvalue().strip()}")

    def test_every_hint_in_the_source_parses(self) -> None:
        checked = 0
        for path in sorted(CONTROLLER_DIR.glob("*.py")):
            for lineno, template, expressions in _templates(path):
                for command in _commands(template):
                    for rendered in _renderings(command, expressions):
                        with self.subTest(where=f"{path.name}:{lineno}", command=rendered):
                            self.assert_parses(rendered, f"{path.name}:{lineno}")
                            checked += 1
        # The scan must keep seeing the hints it exists for.
        self.assertGreaterEqual(checked, 10)

    def test_no_hint_puts_work_item_after_explain(self) -> None:
        for path in sorted(CONTROLLER_DIR.glob("*.py")):
            self.assertNotIn("explain --work-item", path.read_text(), path.name)

    def test_the_hint_builders_parse(self) -> None:
        root, runtime_root = Path("/tmp/my repo"), Path("/tmp/runtime")
        built = [
            decision.explain_gate_command(root, "wi-1"),
            job._resume_command(Path("/tmp/repo")),
            job._resume_command(Path("/tmp/repo"), runtime_root),
            job.follow_command(runtime_root, Path("/tmp/repo")),
            job._abandon_command("job-1", Path("/tmp/repo")),
            job._abandon_command("job-1", Path("/tmp/repo"), acknowledge=True),
            cli._next_generation_command(runtime_root, "/tmp/repo"),
        ] + [milestone_branch._cli("wi-1", disposition, Path("/tmp/repo")).strip("`")
             for disposition in (milestone_branch.NEW_PR, milestone_branch.ABANDON)]
        for command in built:
            with self.subTest(command=command):
                self.assert_parses(command, "builder")

    def test_explain_hint_names_the_repository_and_work_item(self) -> None:
        args = cli.build_parser().parse_args(
            shlex.split(decision.explain_gate_command(Path("/tmp/my repo"), "wi-1"))[1:])
        self.assertEqual((args.command, args.repo, args.work_item), ("explain", "/tmp/my repo", "wi-1"))

    def test_resume_hint_carries_runtime_dir_as_follow_does(self) -> None:
        runtime_root, repo = Path("/tmp/runtime"), Path("/tmp/repo")
        parser = cli.build_parser()
        resume = parser.parse_args(shlex.split(job._resume_command(repo, runtime_root))[1:])
        follow = parser.parse_args(shlex.split(job.follow_command(runtime_root, repo))[1:])
        self.assertEqual((resume.command, resume.runtime_dir, resume.repo), ("resume", str(runtime_root), str(repo)))
        self.assertEqual(resume.runtime_dir, follow.runtime_dir)


if __name__ == "__main__":
    unittest.main()
