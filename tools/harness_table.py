#!/usr/bin/env python3
"""The role-by-harness tables of ``docs/guide/harnesses.md`` (standard library
plus the Controller's own modules).

``python3 tools/harness_table.py`` prints the tables, ``--write`` rewrites the
region of the page between the two markers, and ``--check`` exits 0 when that
region is what ``render()`` prints, else 1 with the first differing line and
the command that fixes it. Every cell is read from ``controller.harness`` and
``controller.routing``; the only text kept here is one sentence per adapter
role and one per usage mode.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from controller import harness, routing  # noqa: E402

PAGE = "docs/guide/harnesses.md"
BEGIN = "<!-- harness-table:begin -->"
END = "<!-- harness-table:end -->"
FIX_COMMAND = "python3 tools/harness_table.py --write"

ROLE_WORDING = {
    harness.WORKER: "runs a Workflow command (plan, implement, apply a review) as a background job",
    harness.REVIEWER: "reviews a plan or an implementation and returns a verdict",
    harness.FUNCTIONAL_TESTER: "runs the functional-review checklist in a sandbox with a shell only",
}

USAGE_WORDING = {
    harness.USAGE_REPORTED: "reported exactly by the harness",
    harness.USAGE_DERIVED: "computed from the harness's output",
    harness.USAGE_NONE: "not reported",
}


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [_row(header), _row(["---"] * len(header))] + [_row(r) for r in rows]


def _wording(table: dict[str, str], key: str, what: str) -> str:
    if key not in table:
        raise ValueError(f"no wording for the {what} {key!r}; add it to tools/harness_table.py")
    return table[key]


def _accepted(role: str, name: str) -> bool:
    try:
        routing.check_role_harness(role, name)
    except ValueError:
        return False
    return True


def render() -> str:
    adapters = list(harness.ADAPTERS.values())
    harness_rows = [[
        f"`{a.name}`",
        f"`{a.reviewer_name}`" if a.reviewer_name is not None else "-",
        a.family,
        ", ".join(f"`{r}`" for r in a.roles),
        _yes_no(a.can_restrict_agents),
        _yes_no(a.can_restrict_tools),
        _wording(USAGE_WORDING, a.usage, "usage mode"),
        a.pinned_version,
        _yes_no(a.fails_closed_on_unknown_version),
    ] for a in adapters]
    route_rows = [
        [f"`{role}`", f"`{routing.ADAPTER_ROLE[role]}`"] + [_yes_no(_accepted(role, a.name)) for a in adapters]
        for role in routing.ROLE_ROUTES
    ]
    legend_roles: list[str] = []
    for a in adapters:
        legend_roles += [r for r in a.roles if r not in legend_roles]
    legend_roles += [r for r in dict.fromkeys(routing.ADAPTER_ROLE.values()) if r not in legend_roles]

    lines = _table(
        ["Harness", "`reviewer.harness`", "Family", "Roles", "Restricts subagents", "Restricts tools",
         "Usage", "Pinned version", "Unknown version refused"],
        harness_rows,
    )
    lines.append("")
    lines += _table(["Routing role", "Runs as"] + [f"`{a.name}`" for a in adapters], route_rows)
    lines.append("")
    lines += [f"- `{r}`: {_wording(ROLE_WORDING, r, 'adapter role')}" for r in legend_roles]
    return "\n".join(lines) + "\n"


def _split(page_text: str) -> tuple[str, str, str]:
    """``(before, region, after)`` around the markers; ``ValueError`` when the
    page does not hold exactly one begin and one end marker, in that order."""
    lines = page_text.splitlines(keepends=True)
    begins = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if len(begins) != 1 or len(ends) != 1:
        raise ValueError(f"expected exactly one {BEGIN} and one {END} line, found {len(begins)} and {len(ends)}")
    if begins[0] > ends[0]:
        raise ValueError(f"{END} comes before {BEGIN}")
    return ("".join(lines[:begins[0] + 1]), "".join(lines[begins[0] + 1:ends[0]]), "".join(lines[ends[0]:]))


def write(page_text: str) -> str:
    before, _, after = _split(page_text)
    if before and not before.endswith("\n"):
        before += "\n"
    return before + render() + after


def check(root: Path) -> list[str]:
    """One line per problem with the page under ``root``."""
    page = Path(root) / PAGE
    if not page.is_file():
        return [f"{PAGE}: the harness page is missing"]
    try:
        _, region, _ = _split(page.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{PAGE}: {exc}"]
    expected = render()
    if region == expected:
        return []
    have, want = region.splitlines(), expected.splitlines()
    for number, (a, b) in enumerate(zip(have, want), start=1):
        if a != b:
            break
    else:
        number = min(len(have), len(want)) + 1
    return [f"{PAGE}: the generated table is not current (first difference at line {number} of the region); "
            f"run {FIX_COMMAND}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The role-by-harness tables.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help=f"rewrite the region of {PAGE}")
    mode.add_argument("--check", action="store_true", help=f"exit 1 when the region of {PAGE} is stale")
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root holding the page")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.check:
        problems = check(root)
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    if args.write:
        page = root / PAGE
        try:
            page.write_text(write(page.read_text(encoding="utf-8")), encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"{PAGE}: {exc}")
            return 1
        return 0
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
