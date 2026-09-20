"""Regression coverage for CP4's checklist-corrections deliverable
(``docs/ai-workflow/CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md``),
per ``docs/ai-workflow/CONTROLLER_GEN1_HARDENING_PLAN.md``'s CP4 section.

Three of the four corrections are mechanically checkable and are bound
here to real, independent evidence, not only to the file's own prose:

1. ``--json`` placement -- a regex over the file's own text.
2. ``explain``'s exit code -- the file's own prose, plus an AST walk over
   the live ``controller/cli.py:cmd_explain`` source.
3. ``inspect --json``'s payload description -- the file's own prose, plus
   a direct call into ``controller.cli._work_item_payload`` with a stub
   carrying its twelve real attributes.

The fourth (Flow 3's precondition) is prose-only per the plan's own M1
disposition: a presence check that the file names both
``FUNCTIONAL_REVIEW.md`` and the unconsumed-findings precondition.
"""

from __future__ import annotations

import ast
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli  # noqa: E402

_CHECKLIST_PATH = REPO_ROOT / "docs" / "ai-workflow" / "CONTROLLER_GEN1_HARDENING_CHECKLIST_CORRECTIONS.md"
_CLI_SOURCE = REPO_ROOT / "controller" / "cli.py"

_SUBCOMMANDS = ("inspect", "explain", "step", "run", "resume", "status")


class ChecklistFileExistsTest(unittest.TestCase):
    def test_checklist_corrections_file_exists(self) -> None:
        self.assertTrue(_CHECKLIST_PATH.is_file(), f"missing {_CHECKLIST_PATH}")


class JsonPlacementTest(unittest.TestCase):
    """REQ-7: no ``--json`` example may place the flag after a subcommand
    token on the same line."""

    def test_no_backwards_json_placement(self) -> None:
        text = _CHECKLIST_PATH.read_text()
        violations: list[str] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for span in re.findall(r"`([^`]+)`", line):
                if "--json" not in span:
                    continue
                json_index = span.index("--json")
                for subcommand in _SUBCOMMANDS:
                    match = re.search(rf"\b{subcommand}\b", span)
                    if match is not None and match.start() < json_index:
                        violations.append(f"line {lineno}: {span!r}")
        self.assertEqual(violations, [])

    def test_file_actually_documents_the_correct_placement(self) -> None:
        text = _CHECKLIST_PATH.read_text()
        self.assertIn("--json inspect", text)
        self.assertIn("--json explain", text)


class ExplainExitCodeTest(unittest.TestCase):
    """REQ-8: the checklist states exit code 0; independently, every
    ``Return`` in the live ``cmd_explain`` returns ``EXIT_OK``."""

    def test_checklist_states_exit_code_zero(self) -> None:
        text = _CHECKLIST_PATH.read_text()
        self.assertIn("`explain` exits `0`", text)
        self.assertNotRegex(text, r"`explain`[^\n]*exits `10`")

    def test_cmd_explain_always_returns_exit_ok(self) -> None:
        tree = ast.parse(_CLI_SOURCE.read_text(), filename=str(_CLI_SOURCE))
        cmd_explain = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_explain"
        )
        returns = [node for node in ast.walk(cmd_explain) if isinstance(node, ast.Return)]
        self.assertTrue(returns, "cmd_explain has no Return nodes to check")
        for node in returns:
            value = node.value
            self.assertIsInstance(value, ast.Name, f"line {node.lineno}: non-name return value")
            self.assertEqual(value.id, "EXIT_OK", f"line {node.lineno}: returns {value.id!r}, not EXIT_OK")


class InspectJsonPayloadTest(unittest.TestCase):
    """REQ-9: the checklist names the four excluded fields; independently,
    a stub carrying ``_work_item_payload``'s real twelve attributes proves
    none of the four excluded names appear in the returned dict's keys."""

    _EXCLUDED_FIELDS = (
        "plan_approval", "technical_approval",
        "functional_acceptance_status", "blocking_decisions",
    )

    def test_checklist_names_all_four_excluded_fields(self) -> None:
        text = _CHECKLIST_PATH.read_text()
        for field in self._EXCLUDED_FIELDS:
            self.assertIn(f"`{field}`", text)

    def test_work_item_payload_excludes_the_four_fields(self) -> None:
        stub = types.SimpleNamespace(
            work_item_id="wi-1",
            work_item_type="product",
            work_item_kind="product",
            governing_workflow_version="2.1",
            phase="IMPLEMENTING",
            plan_revision=1,
            implementation_revision=None,
            functional_review_round=None,
            current_checkpoint_id="CP1",
            last_completed_checkpoint_id=None,
            registry_complete=False,
            incomplete_children=[],
        )
        payload = cli._work_item_payload(stub)
        for field in self._EXCLUDED_FIELDS:
            self.assertNotIn(field, payload)


class FunctionalReviewPreconditionTest(unittest.TestCase):
    """REQ-9's Flow-3 clause (prose-only per M1): the checklist names both
    the artifact and the unconsumed-findings precondition. The mapping
    (``docs/ai-workflow/requirements/workflow-controller-gen1-correctness-
    hardening-mapping.json``) runs REQ-1..REQ-9 only; this clause lives in
    REQ-9's own text, not in a separate requirement."""

    def test_checklist_names_functional_review_artifact_and_precondition(self) -> None:
        text = _CHECKLIST_PATH.read_text()
        self.assertIn("FUNCTIONAL_REVIEW.md", text)
        self.assertIn("unconsumed", text.lower())


if __name__ == "__main__":
    unittest.main()
