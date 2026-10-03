#!/usr/bin/env python3
"""Offline documentation checks (standard library only).

Run ``python3 tools/check_docs.py [--root DIR]``: exit 0 when clean, 1 with
one line per problem. ``tests/test_docs.py`` runs it on the repository and
exercises every rule on synthetic trees. Nothing here touches the network,
runs the Controller or resolves a runtime.

Rules:

1. Links and anchors: every relative Markdown link and ``#anchor`` in the
   scoped pages resolves to a file and, for Markdown targets, to a heading
   slug (GitHub's ``github-slugger`` rule). Links into the sibling
   repositories must be well formed; other hosts must be allow-listed.
2. Commands and flags: every ``workflow-controller`` line in a fenced code
   block of a task page parses under ``controller.cli.build_parser()``
   (never run), with every terminating action (``--version``, ``--help``)
   replaced by a recording no-op.
3. Page header and internal ids on the user pages.
4. The exit-code page agrees with ADR 0001 and ``controller.cli``.
5. The compatibility page carries exactly the release digests of
   ``controller.workflow_contract.RELEASE_CONTRACTS``.
6. The ``docs/guide/installation.md`` stub keeps exactly its three headings.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# The page lists, each with the checkpoint that activates it. A page is only
# checked once ``ACTIVE_THROUGH`` has reached its checkpoint, so an
# unfinished page never fails an earlier checkpoint. The link check is not
# gated: it runs on every file in scope from the start.
# ---------------------------------------------------------------------------

#: The checkpoint number the repository has reached. Raised by each checkpoint.
ACTIVE_THROUGH = 3

#: The user pages (marked U in the plan): header and internal-id rules.
USER_PAGES: tuple[tuple[str, int], ...] = (
    ("docs/exit-codes.md", 2),
    ("docs/glossary.md", 2),
    ("docs/compatibility.md", 2),
    ("docs/release-history.md", 2),
    ("docs/install.md", 3),
    ("docs/run.md", 3),
    ("docs/update.md", 3),
    ("docs/common-problems.md", 4),
    ("docs/guide/how-it-works.md", 4),
    ("docs/guide/commands.md", 4),
    ("docs/guide/runtime.md", 4),
    ("docs/guide/automation.md", 4),
    ("docs/guide/workers.md", 4),
    ("docs/guide/milestone-branches.md", 4),
    ("docs/guide/ci-and-releases.md", 4),
    ("docs/guide/development.md", 4),
    ("docs/guide/troubleshooting.md", 4),
    ("README.md", 5),
    ("docs/README.md", 5),
)

#: The pages whose fenced code blocks are command-checked.
COMMAND_PAGES: tuple[tuple[str, int], ...] = (
    ("docs/install.md", 3),
    ("docs/run.md", 3),
    ("docs/update.md", 3),
    ("README.md", 3),
    ("docs/common-problems.md", 4),
)

EXIT_CODES_PAGE = ("docs/exit-codes.md", 2)
COMPATIBILITY_PAGE = ("docs/compatibility.md", 2)
INSTALLATION_STUB = ("docs/guide/installation.md", 4)
INSTALLATION_STUB_HEADINGS = (
    "Supported Workflow releases",
    "Upgrade",
    "Moving a target to another Workflow release",
)
ADR_EXIT_TABLE = "docs/adr/0001-controller-generation-1-architecture.md"

#: Pages the link check skips: Workflow commands rewrite the first two, the
#: rest are historical records.
LINK_EXCLUDED = ("docs/ROADMAP.md", "docs/ACTIVE_MILESTONE.md")
LINK_GLOBS = ("README.md", "docs/*.md", "docs/guide/*.md", "docs/adr/*.md", "docs/releases/*.md")

#: External hosts allowed besides github.com repository links, seeded from
#: the hosts present when the checks were added.
ALLOWED_HOSTS = frozenset({"pipx.pypa.io"})
GITHUB_OWNER = "RodrigoFAbreu"
GITHUB_REPOS = frozenset({"workflow-controller", "workflow-manager", "workflow"})

HEADER_RE = re.compile(r"^> For: .+\. Last checked with: .+\.$")
INTERNAL_ID_RES = (re.compile(r"\bCP\d+\b"), re.compile(r"\b[A-Z]{2,5}-R\d+-\d+\b"))
SIGINT_EXIT_STATUS_FALLBACK = 130


def active(pages, through: int) -> list[str]:
    return [page for page, cp in pages if cp <= through]


# ---------------------------------------------------------------------------
# Markdown reading.
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")


def split_fences(text: str) -> list[tuple[str, str, list[str]]]:
    """``[(kind, info, lines)]`` with ``kind`` ``"text"`` or ``"fence"``;
    ``info`` is a fence's info string. A fence closes on a line of the same
    character, at least as long, with nothing after it."""
    out: list[tuple[str, str, list[str]]] = []
    current: list[str] = []
    fence: tuple[str, int, str] | None = None
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if fence is None:
            if m:
                if current:
                    out.append(("text", "", current))
                current = []
                fence = (m.group(1)[0], len(m.group(1)), m.group(2).strip())
            else:
                current.append(line)
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                out.append(("fence", fence[2], current))
                current = []
                fence = None
            else:
                current.append(line)
    if fence is not None:
        out.append(("fence", fence[2], current))
    elif current:
        out.append(("text", "", current))
    return out


def prose(text: str) -> str:
    """The text outside code fences."""
    return "\n".join("\n".join(lines) for kind, _, lines in split_fences(text) if kind == "text")


_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_LINK_TEXT_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def headings(text: str) -> list[tuple[int, str]]:
    """``[(level, heading text)]`` for every ATX heading outside fences."""
    found = []
    for line in prose(text).split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            found.append((len(m.group(1)), m.group(2)))
    return found


def slug_base(heading: str) -> str:
    """``github-slugger``: link markup reduced to its text, inline-code
    backticks removed but the code text kept, lowercased, every character
    but letters, digits, underscores, hyphens and spaces removed, spaces
    become hyphens."""
    text = _LINK_TEXT_RE.sub(r"\1", heading).replace("`", "")
    text = text.lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> list[str]:
    """Every anchor of a page, in heading order. A slug already taken gets
    the first ``-N`` (N from 1) that is itself not taken."""
    used: set[str] = set()
    result = []
    for _, heading in headings(text):
        base = slug_base(heading)
        slug = base
        n = 0
        while slug in used:
            n += 1
            slug = f"{base}-{n}"
        used.add(slug)
        result.append(slug)
    return result


_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
_LINK_RE = re.compile(r"\[(?:[^\]\n]|\n)*?\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")


def links(text: str) -> list[str]:
    """The target of every inline Markdown link outside code fences and
    inline code."""
    body = _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), prose(text))
    return [m.group(1) for m in _LINK_RE.finditer(body)]


# ---------------------------------------------------------------------------
# Rule 1: links and anchors.
# ---------------------------------------------------------------------------


def link_pages(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in LINK_GLOBS:
        found.update(p for p in root.glob(pattern) if p.is_file())
    excluded = {root / e for e in LINK_EXCLUDED}
    return sorted(p for p in found if p not in excluded)


def external_link_problem(target: str) -> str | None:
    """``None`` when the external link is acceptable."""
    m = re.match(r"^https?://([^/?#]+)(/[^?#]*)?(?:[?#].*)?$", target)
    if not m:
        return "malformed URL"
    host = m.group(1).lower()
    path = (m.group(2) or "").strip("/")
    if host in ALLOWED_HOSTS:
        return None
    if host != "github.com":
        return f"host {host!r} is not allow-listed"
    parts = path.split("/") if path else []
    if len(parts) < 2 or parts[0] != GITHUB_OWNER:
        return f"github.com link must name {GITHUB_OWNER}/<repository>"
    repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    if repo not in GITHUB_REPOS:
        return f"unknown repository {repo!r} (known: {', '.join(sorted(GITHUB_REPOS))})"
    rest = parts[2:]
    if not rest:
        return None
    kind = rest[0]
    if kind in ("blob", "tree"):
        return None if len(rest) >= 2 else f"{kind} link needs a ref"
    if kind == "releases":
        return None
    if kind in ("issues", "pull"):
        return None if len(rest) == 2 and rest[1].isdigit() else f"{kind} link needs a number"
    return f"unsupported github.com path form {kind!r}"


def check_links(root: Path) -> list[str]:
    problems: list[str] = []
    cache: dict[Path, list[str]] = {}

    def page_anchors(path: Path) -> list[str]:
        if path not in cache:
            cache[path] = anchors(path.read_text(encoding="utf-8"))
        return cache[path]

    for page in link_pages(root):
        rel = page.relative_to(root).as_posix()
        for target in links(page.read_text(encoding="utf-8")):
            if target.startswith("mailto:"):
                continue
            if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I):
                problem = external_link_problem(target)
                if problem:
                    problems.append(f"{rel}: external link {target!r}: {problem}")
                continue
            path_part, _, fragment = target.partition("#")
            path_part = unquote(path_part)
            dest = page if path_part == "" else (page.parent / path_part).resolve()
            if not dest.exists():
                problems.append(f"{rel}: link {target!r} points at a missing file")
                continue
            if fragment and dest.is_file() and dest.suffix == ".md":
                if fragment not in page_anchors(dest):
                    problems.append(f"{rel}: link {target!r} points at a missing heading")
    return problems


# ---------------------------------------------------------------------------
# Rule 2: commands and flags.
# ---------------------------------------------------------------------------

_NUMERIC_PLACEHOLDERS = frozenset({"n", "count", "seconds", "number", "max-steps", "steps"})
_PLACEHOLDER_RE = re.compile(r"<([^<>\s]+)>")
_VARIABLE_RE = re.compile(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)")


def command_lines(text: str, *, checked_info=lambda info: info.strip().split(" ")[0] != "text") -> list[str]:
    """Every Controller invocation in the fenced blocks of ``text``: the
    line (continuations joined, an optional ``$ `` and comments removed)
    starts ``workflow-controller``, carries no ``[``/``]`` (a synopsis) and is
    cut at the first shell operator.
    A block whose info string is ``text`` is not checked."""
    found = []
    for kind, info, lines in split_fences(text):
        if kind != "fence" or not checked_info(info):
            continue
        joined: list[str] = []
        pending = ""
        for raw in lines:
            if raw.rstrip().endswith("\\"):
                pending += raw.rstrip()[:-1] + " "
                continue
            joined.append(pending + raw)
            pending = ""
        if pending:
            joined.append(pending)
        for line in joined:
            line = line.strip()
            if line.startswith("$ "):
                line = line[2:].strip()
            if not line.startswith("workflow-controller"):
                continue
            if "[" in line or "]" in line:
                continue  # a usage synopsis, not a runnable line
            found.append(line)
    return found


def argv_for(line: str) -> list[str]:
    """The argument vector of an invocation line: ``<placeholders>`` and
    shell variables replaced by fixed words, comments dropped, cut at the
    first shell operator."""
    def placeholder(m: re.Match) -> str:
        return "1" if m.group(1).lower() in _NUMERIC_PLACEHOLDERS else "placeholder"

    line = _PLACEHOLDER_RE.sub(placeholder, line)
    line = _VARIABLE_RE.sub("placeholder", line)
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    tokens: list[str] = []
    for token in lexer:
        if token and all(c in lexer.punctuation_chars for c in token):
            break
        tokens.append(token)
    return tokens[1:] if tokens and tokens[0] == "workflow-controller" else tokens


class _Recorder(argparse.Action):
    """Stands in for a terminating action (``--version``, ``--help``): the
    flag parses and is recorded; nothing is printed, nothing exits, no
    runtime is resolved."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None, sink=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)
        self.sink = sink

    def __call__(self, parser, namespace, values, option_string=None):
        self.sink.append(option_string)


def _is_terminating(action: argparse.Action) -> bool:
    return (isinstance(action, argparse._HelpAction) or isinstance(action, argparse._VersionAction)
            or type(action).__name__ == "_VersionAction")


def _walk(parser: argparse.ArgumentParser):
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield from _walk(sub)


def neutralise(parser: argparse.ArgumentParser, sink: list, *, relax: bool) -> argparse.ArgumentParser:
    """Replace every terminating action in ``parser`` and its subparsers
    with a recorder writing to ``sink``; with ``relax``, also clear every
    ``required`` flag (positionals, required options, the subparsers action
    and every mutually exclusive group)."""
    for p in _walk(parser):
        for i, action in enumerate(p._actions):
            if _is_terminating(action):
                recorder = _Recorder(action.option_strings, help=action.help, sink=sink)
                p._actions[i] = recorder
                for option in action.option_strings:
                    p._option_string_actions[option] = recorder
        if relax:
            for action in p._actions:
                action.required = False
            for group in p._mutually_exclusive_groups:
                group.required = False
    return parser


class CommandChecker:
    """Validates invocation lines against freshly built parsers."""

    def __init__(self, build_parser) -> None:
        self.sink: list = []
        self.relaxed = neutralise(build_parser(), self.sink, relax=True)
        self.strict = neutralise(build_parser(), self.sink, relax=False)

    def _parse(self, parser, argv) -> str | None:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
                parser.parse_args(argv)
        except SystemExit:
            return buf.getvalue().strip().splitlines()[-1] if buf.getvalue().strip() else "usage error"
        return None

    def problem(self, line: str) -> str | None:
        """``None`` when ``line`` would be accepted by the real CLI up to
        its first terminating action."""
        argv = argv_for(line)
        self.sink.clear()
        error = self._parse(self.relaxed, argv)
        if error is not None:
            return error
        if self.sink:
            return None
        return self._parse(self.strict, argv)


def check_commands(root: Path, through: int, build_parser) -> list[str]:
    problems = []
    checker = CommandChecker(build_parser)
    for rel in active(COMMAND_PAGES, through):
        page = root / rel
        if not page.is_file():
            problems.append(f"{rel}: command page is missing")
            continue
        for line in command_lines(page.read_text(encoding="utf-8")):
            error = checker.problem(line)
            if error:
                problems.append(f"{rel}: command {line!r} does not parse: {error}")
    return problems


# ---------------------------------------------------------------------------
# Rule 3: page header and internal ids.
# ---------------------------------------------------------------------------


def work_item_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    state = root / "docs/ai-workflow/WORKFLOW_STATE.json"
    if state.is_file():
        try:
            ids.update(json.loads(state.read_text(encoding="utf-8")).get("work_items", {}))
        except (ValueError, AttributeError):
            pass
    completed = root / "docs/milestones/completed"
    if completed.is_dir():
        ids.update(p.stem for p in completed.glob("*.md"))
    return ids


def header_problem(text: str) -> str | None:
    lines = prose(text).split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^# \S", line):
            for following in lines[i + 1:]:
                if following.strip():
                    return None if HEADER_RE.match(following.strip()) else (
                        "the first line after the title must be '> For: <reader>. Last checked with: <versions>.'")
            return "no header after the title"
    return "no H1 title"


def internal_id_problems(text: str, ids: set[str]) -> list[str]:
    body = _LINK_RE.sub(lambda m: m.group(0).replace(m.group(1), ""), text)
    found = []
    for pattern in INTERNAL_ID_RES:
        found.extend(sorted({m.group(0) for m in pattern.finditer(body)}))
    for item in sorted(ids):
        if re.search(r"(?<![\w-])" + re.escape(item) + r"(?![\w-])", body):
            found.append(item)
    return found


def check_pages(root: Path, through: int) -> list[str]:
    problems = []
    ids = work_item_ids(root)
    for rel in active(USER_PAGES, through):
        page = root / rel
        if not page.is_file():
            problems.append(f"{rel}: user page is missing")
            continue
        text = page.read_text(encoding="utf-8")
        problem = header_problem(text)
        if problem:
            problems.append(f"{rel}: {problem}")
        for found in internal_id_problems(text, ids):
            problems.append(f"{rel}: internal id {found!r} on a user page")
    return problems


# ---------------------------------------------------------------------------
# Rule 4: exit codes. Rule 5: digests. Rule 6: the installation stub.
# ---------------------------------------------------------------------------


def table_codes(text: str) -> set[int]:
    """The integer first cells of the Markdown table rows of ``text``."""
    codes = set()
    for line in prose(text).split("\n"):
        m = re.match(r"^\|\s*(\d+)\s*\|", line)
        if m:
            codes.add(int(m.group(1)))
    return codes


def adr_exit_section(adr_text: str) -> str:
    m = re.search(r"^## Exit codes\s*$(.*?)(?=^## |\Z)", adr_text, re.S | re.M)
    return m.group(1) if m else ""


def cli_exit_codes(cli_module) -> tuple[set[int], int]:
    constants = {v for k, v in vars(cli_module).items() if k.startswith("EXIT_") and isinstance(v, int)}
    return constants, getattr(cli_module, "SIGINT_EXIT_STATUS", SIGINT_EXIT_STATUS_FALLBACK)


def check_exit_codes(root: Path, through: int, cli_module) -> list[str]:
    rel, cp = EXIT_CODES_PAGE
    if cp > through:
        return []
    page = root / rel
    if not page.is_file():
        return [f"{rel}: exit-code page is missing"]
    page_codes = table_codes(page.read_text(encoding="utf-8"))
    constants, sigint = cli_exit_codes(cli_module)
    problems = []
    expected = constants | {sigint}
    for code in sorted(expected - page_codes):
        problems.append(f"{rel}: exit status {code} is in controller.cli but not on the page")
    for code in sorted(page_codes - expected):
        problems.append(f"{rel}: exit status {code} is on the page but not in controller.cli")
    adr = root / ADR_EXIT_TABLE
    if adr.is_file():
        adr_codes = table_codes(adr_exit_section(adr.read_text(encoding="utf-8")))
        page_without_sigint = page_codes - {sigint}
        for code in sorted(adr_codes - page_without_sigint):
            problems.append(f"{rel}: ADR 0001 lists exit status {code}, the page does not")
        for code in sorted(page_without_sigint - adr_codes):
            problems.append(f"{rel}: the page lists exit status {code}, ADR 0001 does not")
    else:
        problems.append(f"{ADR_EXIT_TABLE}: missing")
    return problems


_HEX64_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{64}(?![0-9A-Za-z])")


def contract_digests(contracts) -> set[str]:
    digests: set[str] = set()
    for contract in contracts.values():
        if contract.query_script_sha256:
            digests.update(contract.query_script_sha256.values())
    return digests


def check_digests(root: Path, through: int, contracts) -> list[str]:
    rel, cp = COMPATIBILITY_PAGE
    if cp > through:
        return []
    page = root / rel
    if not page.is_file():
        return [f"{rel}: compatibility page is missing"]
    on_page = set(_HEX64_RE.findall(page.read_text(encoding="utf-8")))
    expected = contract_digests(contracts)
    problems = [f"{rel}: release digest {d} from RELEASE_CONTRACTS is not on the page" for d in sorted(expected - on_page)]
    problems += [f"{rel}: 64-hex string {d} is not a RELEASE_CONTRACTS digest" for d in sorted(on_page - expected)]
    return problems


def check_stub(root: Path, through: int) -> list[str]:
    rel, cp = INSTALLATION_STUB
    if cp > through:
        return []
    page = root / rel
    if not page.is_file():
        return [f"{rel}: installation stub is missing"]
    found = tuple(text for level, text in headings(page.read_text(encoding="utf-8")) if level == 2)
    if found != INSTALLATION_STUB_HEADINGS:
        return [f"{rel}: stub headings are {list(found)!r}, expected {list(INSTALLATION_STUB_HEADINGS)!r}"]
    return []


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def check_tree(root: Path, *, through: int = ACTIVE_THROUGH, build_parser=None, cli_module=None,
               contracts=None) -> list[str]:
    """Every problem in the tree at ``root``. ``build_parser``, ``cli_module``
    and ``contracts`` default to the live Controller's."""
    if build_parser is None or cli_module is None or contracts is None:
        sys.path.insert(0, str(REPO_ROOT))
        from controller import cli, workflow_contract
        build_parser = build_parser or cli.build_parser
        cli_module = cli_module or cli
        contracts = contracts if contracts is not None else workflow_contract.RELEASE_CONTRACTS
    problems = check_links(root)
    problems += check_commands(root, through, build_parser)
    problems += check_pages(root, through)
    problems += check_exit_codes(root, through, cli_module)
    problems += check_digests(root, through, contracts)
    problems += check_stub(root, through)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline documentation checks.")
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root to check")
    args = parser.parse_args(argv)
    problems = check_tree(Path(args.root).resolve())
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
