"""The repository's backlog file (``workflow-controller-a2a``, D3 to D6).

``docs/backlog.yaml`` lists every row of the roadmap's Controller and orchestrator
tables as an item. These tests pin what a later edit must not break and nothing
that ordinary use of the backlog changes: the file validates with the Workflow's
own parser, every archive row (less the two named exclusions) is an item, ``order``
is unique, every row the archive marks done is ``done`` in the file, and the
selection on the live file equals the rule of ``select_next`` worked out from the
file's own data. No test names an item of the live file as the selected one.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The page the item rows are read from: the hand-written roadmap until it is
# replaced, then the archive that keeps its text.
ARCHIVE_PAGE = "docs/ROADMAP.md"
BACKLOG_FILE = "docs/backlog.yaml"
ID_PREFIX = "workflow-controller-"

# Rows that are not items (D3): both are rows that were dropped or replaced.
EXCLUDED_ROWS = {"A2c", "A5"}
# The rows the archive does not mark done at delivery (D4's open list).
OPEN_ROWS = {
    "A2a", "A2b", "H4e", "H4f", "H4g", "C10d-a", "C10d-b",
    "CL1a", "CL1b", "CL1c", "CL1d", "CL1e", "C12b", "C12c", "C12d", "C8c",
    "C11", "A3", "A4", "O1", "O2", "O3", "O4", "O5", "O6", "O7",
}

CONTROLLER_HEADER = "| # | Step | Needs | Section |"
ORCHESTRATOR_HEADER = "| # | Step | Needs |"

FIXTURE = """version: 1
items:
  - id: shipped
    title: A finished item
    status: done
    order: 1
  - id: running
    title: An item in progress
    status: in-progress
    order: 2
    depends_on: [shipped]
  - id: first
    title: The first next item
    status: next
    order: 3
    depends_on: [running]
  - id: second
    title: The second next item
    status: next
    order: 4
    depends_on: [shipped]
  - id: sketch
    title: An idea
    status: idea
    order: 5
"""


def _load_backlog_module():
    path = REPO_ROOT / "scripts" / "workflow_backlog.py"
    spec = importlib.util.spec_from_file_location("workflow_backlog_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backlog = _load_backlog_module()


def _table_rows(lines: list[str], header: str) -> list[list[str]]:
    """The cells of each data row of the table whose header line is ``header``."""
    start = lines.index(header)
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("| "):
            break
        rows.append(line[2:-2].split(" | "))
    return rows


def archive_rows() -> dict[str, str]:
    """Row id -> step cell for every row of the two tables."""
    lines = (REPO_ROOT / ARCHIVE_PAGE).read_text(encoding="utf-8").split("\n")
    rows: dict[str, str] = {}
    for header in (CONTROLLER_HEADER, ORCHESTRATOR_HEADER):
        for cells in _table_rows(lines, header):
            rows[cells[0]] = cells[1]
    return rows


def _parentheses(text: str) -> list[str]:
    groups, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char == "(":
            if depth == 0:
                start = index
            depth += 1
        elif char == ")" and depth:
            depth -= 1
            if depth == 0:
                groups.append(text[start + 1:index])
    return groups


def archive_marks_done(step: str) -> bool:
    """D4's rule: a leading ``**Done**``, or the word done or complete in lower
    case inside a parenthesis wherever it falls."""
    if step.startswith("**Done**"):
        return True
    return any(re.search(r"\b(done|complete)\b", group) for group in _parentheses(step))


def expected_selection(data: dict) -> str | None:
    """The rule of ``select_next`` worked out from the file's own data: the ready
    ``in-progress`` item with the lowest ``order``, else the ready ``next`` item
    with the lowest ``order``; ``None`` when nothing is ready."""
    status = {item["id"]: item["status"] for item in data["items"]}
    ordered = sorted(data["items"], key=lambda item: item["order"])

    def ready(item) -> bool:
        return all(status[dep] == "done" for dep in item.get("depends_on") or [])

    for wanted in ("in-progress", "next"):
        for item in ordered:
            if item["status"] == wanted and ready(item):
                return item["id"]
    return None


def selection(data: dict) -> str | None:
    try:
        return backlog.select_next(data)["item"]["id"]
    except backlog.BacklogError as exc:
        if exc.code != "no_ready_item":
            raise
        return None


def _live():
    raw = (REPO_ROOT / BACKLOG_FILE).read_bytes()
    return raw, *backlog.parse_and_validate(raw, REPO_ROOT, file=BACKLOG_FILE)


class LiveBacklogTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.data, self.lines = _live()
        self.items = {item["id"]: item for item in self.data["items"]}
        self.rows = archive_rows()

    def test_the_file_validates(self):
        self.assertEqual(self.data["version"], 1)
        self.assertTrue(self.data["items"])

    def test_every_archive_row_but_the_named_exclusions_is_an_item(self):
        missing = [row for row in self.rows
                   if row not in EXCLUDED_ROWS and ID_PREFIX + row.lower() not in self.items]
        self.assertEqual(missing, [])

    def test_the_exclusions_are_real_rows_and_not_items(self):
        for row in EXCLUDED_ROWS:
            self.assertIn(row, self.rows)
            self.assertNotIn(ID_PREFIX + row.lower(), self.items)

    def test_order_is_unique(self):
        orders = [item["order"] for item in self.data["items"]]
        self.assertEqual(len(orders), len(set(orders)))

    def test_a_row_the_archive_marks_done_is_done(self):
        wrong = [row for row, step in self.rows.items()
                 if row not in EXCLUDED_ROWS and archive_marks_done(step)
                 and self.items[ID_PREFIX + row.lower()]["status"] != "done"]
        self.assertEqual(wrong, [])

    def test_every_row_outside_the_open_list_is_marked_done_in_the_archive(self):
        # Catches a done row the rule above missed.
        unmarked = [row for row, step in self.rows.items()
                    if row not in EXCLUDED_ROWS and row not in OPEN_ROWS
                    and not archive_marks_done(step)]
        self.assertEqual(unmarked, [])
        for row in OPEN_ROWS:
            self.assertIn(row, self.rows)

    def test_selection_equals_the_rule_worked_out_from_the_data(self):
        self.assertEqual(selection(self.data), expected_selection(self.data))

    def test_selection_survives_reordering_open_items_and_accepting_one(self):
        open_ids = [item for item in self.data["items"] if item["status"] in ("next", "in-progress")]
        self.assertGreaterEqual(len(open_ids), 2)
        swapped = _copy(self.data)
        first, second = [i for i in swapped["items"] if i["id"] in (open_ids[0]["id"], open_ids[1]["id"])]
        first["order"], second["order"] = second["order"], first["order"]
        self.assertEqual(selection(swapped), expected_selection(swapped))
        accepted = _copy(self.data)
        for item in accepted["items"]:
            if item["id"] == open_ids[0]["id"]:
                item["status"] = "done"
        self.assertEqual(selection(accepted), expected_selection(accepted))

    def test_nothing_ready_is_the_refusal_not_an_error(self):
        finished = _copy(self.data)
        for item in finished["items"]:
            if item["status"] in ("next", "in-progress"):
                item["status"] = "done"
        self.assertIsNone(expected_selection(finished))
        self.assertIsNone(selection(finished))
        with self.assertRaises(backlog.BacklogError) as ctx:
            backlog.select_next(finished)
        self.assertEqual(ctx.exception.code, "no_ready_item")

    def test_an_item_absent_from_the_archive_keeps_the_pins_green(self):
        text = self.raw.decode("utf-8") + (
            "  - id: workflow-controller-brand-new\n    title: Added later\n"
            "    status: next\n    order: 100000\n")
        data, _ = backlog.parse_and_validate(text, REPO_ROOT, file=BACKLOG_FILE)
        ids = {item["id"] for item in data["items"]}
        self.assertTrue(all(ID_PREFIX + row.lower() in ids
                            for row in self.rows if row not in EXCLUDED_ROWS))

    def test_an_archive_row_marked_done_that_is_not_done_fails_the_pin(self):
        data = _copy(self.data)
        target = next(i for i in data["items"] if i["status"] == "done")
        target["status"] = "next"
        row = target["id"][len(ID_PREFIX):]
        done_rows = {r for r, step in self.rows.items() if archive_marks_done(step)}
        self.assertTrue(any(ID_PREFIX + r.lower() == target["id"] for r in done_rows), row)
        status = {i["id"]: i["status"] for i in data["items"]}
        wrong = [r for r in done_rows if r not in EXCLUDED_ROWS and status[ID_PREFIX + r.lower()] != "done"]
        self.assertTrue(wrong)

    def test_an_archive_row_that_is_not_an_item_fails_the_pin(self):
        data = _copy(self.data)
        data["items"] = [i for i in data["items"] if i["id"] != ID_PREFIX + "c1"]
        ids = {i["id"] for i in data["items"]}
        missing = [r for r in self.rows if r not in EXCLUDED_ROWS and ID_PREFIX + r.lower() not in ids]
        self.assertEqual(missing, ["C1"])


class ValidatorIsTheRealOneTests(unittest.TestCase):
    """A malformed copy is refused with the parser's line, so the pins above are
    looking at the Workflow's own validator."""

    def setUp(self):
        self.text = (REPO_ROOT / BACKLOG_FILE).read_text(encoding="utf-8")

    def _refused(self, text: str) -> str:
        with self.assertRaises(backlog.BacklogError) as ctx:
            backlog.parse_and_validate(text, REPO_ROOT, file=BACKLOG_FILE)
        self.assertEqual(ctx.exception.code, "backlog_invalid")
        return ctx.exception.message

    def test_a_duplicate_id_is_refused_with_its_line(self):
        text = self.text + ("  - id: workflow-controller-c1\n    title: Again\n"
                            "    status: done\n    order: 100000\n")
        message = self._refused(text)
        self.assertRegex(message, rf"{re.escape(BACKLOG_FILE)}:\d+:")
        self.assertIn("repeats", message)

    def test_an_unknown_dependency_is_refused_with_its_line(self):
        text = self.text + ("  - id: workflow-controller-lost\n    title: Lost\n"
                            "    status: next\n    order: 100000\n"
                            "    depends_on: [workflow-controller-nowhere]\n")
        message = self._refused(text)
        self.assertRegex(message, rf"{re.escape(BACKLOG_FILE)}:\d+:")
        self.assertIn("workflow-controller-nowhere", message)


class InlineFixtureTests(unittest.TestCase):
    """The delivered behaviour, on a backlog the test owns."""

    def _data(self, text: str = FIXTURE) -> dict:
        data, _ = backlog.parse_and_validate(text, REPO_ROOT, file="fixture")
        return data

    def test_an_in_progress_item_is_selected_before_a_next_item(self):
        self.assertEqual(selection(self._data()), "running")

    def test_with_it_done_the_next_item_with_met_dependencies_is_selected(self):
        data = self._data(FIXTURE.replace("status: in-progress", "status: done"))
        self.assertEqual(selection(data), "first")

    def test_a_waiting_next_item_is_skipped_not_a_block(self):
        # `first` waits for `running`; with `running` an idea it is skipped.
        data = self._data(FIXTURE.replace("status: in-progress", "status: idea"))
        self.assertEqual(selection(data), "second")

    def test_an_idea_is_never_selected(self):
        data = self._data(FIXTURE.replace("status: next", "status: done").replace(
            "status: in-progress", "status: done"))
        self.assertIsNone(selection(data))


def _copy(data: dict) -> dict:
    import copy
    return copy.deepcopy(data)


if __name__ == "__main__":
    unittest.main()
