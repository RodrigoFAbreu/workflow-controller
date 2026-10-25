"""Tests for ``tools/harness_table.py``: the tables mirror the adapter
registry and the routing checks, the page region is rewritten and compared
exactly, and a registry change shows up in the render."""

from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import harness, routing  # noqa: E402

_spec = importlib.util.spec_from_file_location("harness_table_under_test", REPO_ROOT / "tools" / "harness_table.py")
ht = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ht)


def table_rows(text: str) -> list[list[list[str]]]:
    """The tables of ``text`` as lists of rows of cells (header and rule dropped)."""
    tables, current = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            current.append([c.strip() for c in line.strip("|").split("|")])
        elif current:
            tables.append(current[2:])
            current = []
    return tables


def with_codex_roles(*roles: str):
    added = dataclasses.replace(harness.CODEX, roles=roles)
    return mock.patch.object(harness, "ADAPTERS", {"claude-code": harness.CLAUDE_CODE, "codex": added})


class RenderTest(unittest.TestCase):
    def test_first_table_has_a_row_per_adapter_with_its_fields(self) -> None:
        first = table_rows(ht.render())[0]
        self.assertEqual(len(first), len(harness.ADAPTERS))
        for row, a in zip(first, harness.ADAPTERS.values()):
            self.assertEqual(row[0], f"`{a.name}`")
            self.assertEqual(row[1], f"`{a.reviewer_name}`")
            self.assertEqual(row[2], a.family)
            self.assertEqual(row[3], ", ".join(f"`{r}`" for r in a.roles))
            self.assertEqual(row[4], "yes" if a.can_restrict_agents else "no")
            self.assertEqual(row[5], "yes" if a.can_restrict_tools else "no")
            self.assertEqual(row[6], ht.USAGE_WORDING[a.usage])
            self.assertEqual(row[7], a.pinned_version)
            self.assertEqual(row[8], "yes" if a.fails_closed_on_unknown_version else "no")

    def test_an_adapter_that_cannot_review_shows_a_dash(self) -> None:
        silent = dataclasses.replace(harness.CODEX, name="silent", reviewer_name=None, roles=(harness.WORKER,))
        with mock.patch.object(harness, "ADAPTERS", {"silent": silent}):
            self.assertEqual(table_rows(ht.render())[0][0][1], "-")

    def test_second_table_follows_check_role_harness_in_route_order(self) -> None:
        second = table_rows(ht.render())[1]
        self.assertEqual([r[0] for r in second], [f"`{role}`" for role in routing.ROLE_ROUTES])
        for row in second:
            role = row[0].strip("`")
            self.assertEqual(row[1], f"`{routing.ADAPTER_ROLE[role]}`")
            self.assertEqual(row[2:], ["yes", "no"])

    def test_the_second_table_follows_the_registry(self) -> None:
        before = table_rows(ht.render())[1]
        with with_codex_roles(harness.REVIEWER, harness.FUNCTIONAL_TESTER):
            after = table_rows(ht.render())[1]
        changed = [(b[0], a[3]) for b, a in zip(before, after) if b != a]
        self.assertEqual(changed, [("`functional-tester`", "yes")])

    def test_every_constant_has_wording(self) -> None:
        for role in (harness.WORKER, harness.REVIEWER, harness.FUNCTIONAL_TESTER):
            self.assertIn(role, ht.ROLE_WORDING)
        for usage in (harness.USAGE_REPORTED, harness.USAGE_DERIVED, harness.USAGE_NONE):
            self.assertIn(usage, ht.USAGE_WORDING)

    def test_legend_order_and_usage_sentences(self) -> None:
        text = ht.render()
        legend = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual([line.split("`")[1] for line in legend], ["worker", "reviewer", "functional-tester"])
        for row, a in zip(table_rows(text)[0], harness.ADAPTERS.values()):
            self.assertIn(ht.USAGE_WORDING[a.usage], row)

    def test_a_role_only_the_routing_table_names_is_listed_last(self) -> None:
        only_reviewer = {"codex": harness.CODEX}
        with mock.patch.object(harness, "ADAPTERS", only_reviewer):
            legend = [line.split("`")[1] for line in ht.render().splitlines() if line.startswith("- ")]
        self.assertEqual(legend, ["reviewer", "worker", "functional-tester"])

    def test_an_unknown_role_or_usage_raises_naming_it(self) -> None:
        with with_codex_roles("novel-role"):
            with self.assertRaisesRegex(ValueError, "novel-role"):
                ht.render()
        odd = dataclasses.replace(harness.CODEX, usage="novel-usage")
        with mock.patch.object(harness, "ADAPTERS", {"claude-code": harness.CLAUDE_CODE, "codex": odd}):
            with self.assertRaisesRegex(ValueError, "novel-usage"):
                ht.render()

    def test_deterministic_and_one_trailing_newline(self) -> None:
        text = ht.render()
        self.assertEqual(text, ht.render())
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))


class CheckWordingTest(unittest.TestCase):
    def test_a_missing_wording_is_one_problem_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / ht.PAGE
            page.parent.mkdir(parents=True)
            page.write_text(ht.write(f"{ht.BEGIN}\n{ht.END}\n"), encoding="utf-8")
            odd = dataclasses.replace(harness.CODEX, usage="novel-usage")
            with mock.patch.object(harness, "ADAPTERS", {"claude-code": harness.CLAUDE_CODE, "codex": odd}):
                problems = ht.check(Path(tmp))
        self.assertEqual(len(problems), 1)
        self.assertIn("novel-usage", problems[0])


class WriteTest(unittest.TestCase):
    PAGE = f"# Title\n\nbefore\n{ht.BEGIN}\nold\n{ht.END}\nafter\n"

    def test_replaces_only_the_region_and_is_idempotent(self) -> None:
        out = ht.write(self.PAGE)
        self.assertEqual(out, f"# Title\n\nbefore\n{ht.BEGIN}\n{ht.render()}{ht.END}\nafter\n")
        self.assertEqual(ht.write(out), out)

    def test_refuses_a_page_with_a_bad_marker_set(self) -> None:
        for page in (f"x\n{ht.BEGIN}\n", f"x\n{ht.END}\n", f"{ht.END}\n{ht.BEGIN}\n",
                     f"{ht.BEGIN}\n{ht.BEGIN}\n{ht.END}\n", f"{ht.BEGIN}\n{ht.END}\n{ht.END}\n", "no markers\n"):
            with self.assertRaises(ValueError, msg=page):
                ht.write(page)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.page = self.root / ht.PAGE
        self.page.parent.mkdir(parents=True)

    def run_main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ht.main(list(argv))
        return code, out.getvalue()

    def test_check_write_check(self) -> None:
        self.page.write_text(f"intro\n{ht.BEGIN}\nstale\n{ht.END}\n", encoding="utf-8")
        code, out = self.run_main("--check", "--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn(ht.FIX_COMMAND, out)
        self.assertEqual(self.run_main("--write", "--root", str(self.root)), (0, ""))
        self.assertEqual(self.run_main("--check", "--root", str(self.root)), (0, ""))

    def test_write_refuses_a_page_without_markers(self) -> None:
        self.page.write_text("intro\n", encoding="utf-8")
        code, _ = self.run_main("--write", "--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(self.page.read_text(encoding="utf-8"), "intro\n")

    def test_no_flag_prints_the_render(self) -> None:
        self.assertEqual(self.run_main(), (0, ht.render()))

    def test_check_reports_a_missing_page(self) -> None:
        code, out = self.run_main("--check", "--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn(ht.PAGE, out)


if __name__ == "__main__":
    unittest.main()
