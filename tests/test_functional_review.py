"""The functional tester's pure parts
(``workflow-controller-automated-functional-review`` CP1, D4-D8).

Everything here needs no process, no sandbox and no model: the checklist
reader and its four flow shapes (over synthetic checklists and this
repository's own committed ones, vendored under
``tests/fixtures/functional_checklists/``), the ``RESULTS.json`` validator,
the "not run" entries, the evidence and findings documents, the derivation of
the writable locations from this repository's ``.gitignore`` and the prompt.
"""

from __future__ import annotations

import ast
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import functional_review as fr, protocol_schema  # noqa: E402
from controller.errors import ControllerError  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "functional_checklists"
HEAD = "a" * 40
BLOB = "b" * 40
JOB = "job-123"


def checklist(body: str, title: str = "## Functional review checklist") -> str:
    return f"# Active\n\n{title}\n\n{body}\n\n## Next section\n\nnot a flow\n"


class SectionTest(unittest.TestCase):
    def test_the_section_ends_at_a_heading_of_the_same_or_higher_level(self) -> None:
        text = "# A\n## Functional review checklist\nx\n### Sub\ny\n## Other\nz\n"
        section = fr.checklist_section(text)
        self.assertEqual((section.level, section.lines), (2, ("x", "### Sub", "y")))
        deeper = "### Functional review checklist\nx\n## Higher\n"
        self.assertEqual(fr.checklist_section(deeper).lines, ("x",))

    def test_no_section_is_none_and_the_title_is_case_insensitive(self) -> None:
        self.assertIsNone(fr.checklist_section("## Something else\n| A | b |\n"))
        self.assertIsNotNone(fr.checklist_section("## FUNCTIONAL REVIEW CHECKLIST\nx\n"))

    def test_a_heading_inside_a_fence_neither_starts_nor_ends_the_section(self) -> None:
        text = "## Functional review checklist\n```bash\n# comment\n## Other\n```\n**A. one.** go\n## End\n"
        section = fr.checklist_section(text)
        self.assertIn("**A. one.** go", section.lines)
        self.assertEqual([f.label for f in fr.read_flows(text)], ["A"])
        fenced_title = "```\n## Functional review checklist\n**A. x.**\n```\n"
        self.assertIsNone(fr.checklist_section(fenced_title))


class FlowShapesTest(unittest.TestCase):
    def test_table_rows_below_a_header_and_separator(self) -> None:
        rows = "| # | do | expect |\n| --- | --- | --- |\n| A | one | ok |\n| 2 | two | ok |\n"
        flows = fr.read_flows(checklist(rows + "\nafter"))
        self.assertEqual([(f.flow_id, f.label) for f in flows], [("flow-1", "A"), ("flow-2", "2")])
        self.assertEqual(flows[0].text, "| A | one | ok |")
        with self.assertRaises(fr.ChecklistUnrecognised) as caught:
            fr.read_flows(checklist(rows + "| long | no | label |\n\nafter"))
        evidence = caught.exception.evidence
        self.assertEqual((evidence["shape"], evidence["recognised"], evidence["total"], evidence["unrecognised"]),
                         ("table", 2, 3, ["long"]))

    def test_bold_labelled_paragraphs_with_each_punctuation(self) -> None:
        text = checklist("**Setup.** not a flow\n\n**A. First.** run it\nsecond line\n\n**B) Second.** go\n\n"
                         "**3: Third.** go\n\n**Flows.** nope\n\n**Test data.** nope")
        flows = fr.read_flows(text)
        self.assertEqual([f.label for f in flows], ["A", "B", "3"])
        self.assertEqual(flows[0].text, "**A. First.** run it\nsecond line")

    def test_headings_below_the_sections_own(self) -> None:
        text = checklist("### Setup\nnot\n\n### Flows\nnot\n\n### A. First\ndo a\n\n#### detail\nmore\n\n"
                         "### Flow 2\ndo b\n\n### Test data\nno\n\n### Known limitations\nno\n\n### Notes\nno")
        flows = fr.read_flows(text)
        self.assertEqual([f.label for f in flows], ["A", "2"])
        self.assertIn("#### detail", flows[0].text)
        self.assertNotIn("do b", flows[0].text)

    def test_a_top_level_numbered_list(self) -> None:
        text = checklist("1. first\n   continued\n2. second\n\n   still second\n3) third\n\nA closing paragraph.")
        flows = fr.read_flows(text)
        self.assertEqual([f.label for f in flows], ["1", "2", "3"])
        self.assertEqual(flows[1].text, "2. second\n\n   still second")
        self.assertEqual(flows[0].text, "1. first\n   continued")

    def test_the_first_rule_that_finds_a_flow_decides(self) -> None:
        # Each other shape's item lies where it is accounted for (FL2): inside a flow read, or in a setup region.
        nested = checklist("**A. bold.** x\n\n1. numbered\n\n**B. more.** y\n")
        self.assertEqual([f.label for f in fr.read_flows(nested)], ["A", "B"])
        held = checklist("### Setup\n\n1. numbered\n\n**A. bold.** x\n\n| # | d |\n| --- | --- |\n| B | y |\n")
        # Rule 1 (the table) wins over a bold paragraph that holds its rows.
        self.assertEqual([f.label for f in fr.read_flows(held)], ["B"])
        no_table = checklist("### Setup\n\n1. numbered\n\n**A. bold.** x\n")
        self.assertEqual([f.label for f in fr.read_flows(no_table)], ["A"])

    def test_fenced_lines_are_never_a_flow_for_any_rule(self) -> None:
        fenced = "```\n# heading\n| A | x |\n| --- | --- |\n| B | y |\n**C. bold.** x\n1. item\n```\n"
        for rule, body in (
            ("table", "| # | d |\n| --- | --- |\n| Q | q |\n" + fenced),
            ("bold", fenced + "**D. real.** go\n"),
            ("heading", "### A. real\n" + fenced),
            ("list", "1. real\n" + fenced),
        ):
            with self.subTest(rule=rule):
                labels = [f.label for f in fr.read_flows(checklist(body))]
                self.assertEqual(len(labels), 1)
                self.assertNotIn(labels[0], ("B", "C"))
        tilde = checklist("~~~\n**A. no.**\n~~~\n**B. yes.** go\n")
        self.assertEqual([f.label for f in fr.read_flows(tilde)], ["B"])

    def test_a_bold_paragraph_with_a_blank_line_inside_a_fence_stays_whole(self) -> None:
        text = checklist("**A. One.** run:\n```\nx\n\ny\n```\nthen\n\n**B. Two.** go\n")
        flows = fr.read_flows(text)
        self.assertIn("y\n```\nthen", flows[0].text)

    def test_a_bold_expected_paragraph_after_a_blank_line_stays_in_its_flow(self) -> None:
        text = checklist("**A. Local version.** Run the version command.\n\n"
                         "**Expected:** output must be workflow-controller 1.18.0 and exit 0.\n\n"
                         "**B. Other.** go\n\n**Known limitations**\nnone\n")
        flows = fr.read_flows(text)
        self.assertEqual([f.label for f in flows], ["A", "B"])
        self.assertIn("**Expected:** output must be workflow-controller 1.18.0 and exit 0.", flows[0].text)
        self.assertNotIn("Expected", flows[1].text)
        self.assertNotIn("Known limitations", flows[1].text)
        prompt = fr.tester_prompt(
            flows, clone_dir=Path("/c"), output_dir=Path("/o"), head="h", checklist_blob="b", writable=(),
            refused=(), timeout_seconds=60)
        self.assertIn("**Expected:** output must be workflow-controller 1.18.0 and exit 0.", prompt)

    def test_any_bold_label_after_a_blank_line_stays_in_its_flow_until_a_section(self) -> None:
        for label in ("Expectation:", "Check:", "Verify:", "Success:", "Then:", "Expected result:"):
            with self.subTest(label=label):
                text = checklist(f"**A. Local version.** Run it.\n\n**{label}** exit 0.\n\n**B. Other.** go\n")
                flows = fr.read_flows(text)
                self.assertIn(f"**{label}** exit 0.", flows[0].text)
                self.assertNotIn("exit 0", flows[1].text)
        for section in ("Known limitations", "Setup", "Test data", "Out of scope"):
            with self.subTest(section=section):
                text = checklist(f"**A. One.** go\n\n**{section}**\nnote\n")
                self.assertNotIn(section, fr.read_flows(text)[0].text)

    def test_a_word_is_ignored_and_a_digit_token_that_is_no_label_is_refused(self) -> None:
        word = checklist("**AB. no.** x\n\n**12. yes.** x\n")
        self.assertEqual([f.label for f in fr.read_flows(word)], ["12"])
        with self.assertRaises(fr.ChecklistUnrecognised) as caught:
            fr.read_flows(checklist("**AB. no.** x\n\n**123. no.** x\n\n**12. yes.** x\n"))
        self.assertEqual(caught.exception.evidence["unrecognised"], ["123"])

    def test_a_checklist_with_no_flow_is_unrecognised(self) -> None:
        for body in ("Just prose.", "### Setup\nnothing", "", "- bullets only", "**Setup.** only"):
            with self.subTest(body=body), self.assertRaises(fr.ChecklistUnrecognised) as caught:
                fr.read_flows(checklist(body))
            self.assertIsInstance(caught.exception, ControllerError)
            self.assertEqual(caught.exception.code, "checklist_unrecognised")
            message = caught.exception.message
            self.assertIn(fr.SECTION_TITLE, message)
            for shape in fr.FLOW_SHAPES:
                self.assertIn(shape, message)
            self.assertIn("functional.automated", message)
        with self.assertRaises(fr.ChecklistUnrecognised) as missing:
            fr.read_flows("# Active\n\n## Goal\n\n**A. x.** y\n")
        self.assertIn("missing", missing.exception.message)


def labels(text: str) -> list[str]:
    return [flow.label for flow in fr.read_flows(text)]


def refusal(text: str) -> dict:
    with unittest.TestCase().assertRaises(fr.ChecklistUnrecognised) as caught:
        fr.read_flows(text)
    assert caught.exception.code == "checklist_unrecognised"
    return caught.exception.evidence


TABLE_HEAD = "| id | do | expect |\n| --- | --- | --- |\n"
ACCEPTED = ("A", "7", "F1", "H10", "3a", "6.1", "6.1b", "6b-split", "F1-retry")


class LabelGrammarTest(unittest.TestCase):
    """D2: the label forms real checklists use, in each of the four shapes."""

    def test_every_accepted_label_form_in_each_shape(self) -> None:
        for label in ACCEPTED:
            with self.subTest(label=label):
                table = checklist(TABLE_HEAD + f"| {label} | do | ok |\n| Z9 | do | ok |\n")
                self.assertEqual(labels(table), [label, "Z9"])
                bold = checklist(f"**{label}. One.** go\n\n**Z9. Two.** go\n")
                self.assertEqual(labels(bold), [label, "Z9"])
                heading = checklist(f"### {label}. One\ngo\n\n### Z9. Two\ngo\n")
                self.assertEqual(labels(heading), [label, "Z9"])
                flows = fr.read_flows(bold)
                self.assertEqual([f.flow_id for f in flows], ["flow-1", "flow-2"])
        for label in ("1", "12", "123", "3a", "6.1", "6b-split", "6.1b"):
            with self.subTest(numbered=label):
                self.assertEqual(labels(checklist(f"{label}. one\n{label}) two\n")), [label, label])

    def test_flow_headings_take_the_same_label(self) -> None:
        text = checklist("### Flow 6.1\ndo a\n\n### Flow 6.2\ndo b\n\n### Flow 3\nc\n\n### Flow\nd\n")
        self.assertEqual(labels(text), ["6.1", "6.2", "3", "Flow"])

    def test_a_compact_title_after_the_closing_mark_keeps_its_flow(self) -> None:
        for title in ("Run", "Run2", "Step.Run"):
            with self.subTest(title=title):
                bold = fr.read_flows(checklist(f"**A.{title}.** do A\n\n**B. Other.** do B\n"))
                self.assertEqual([f.label for f in bold], ["A", "B"])
                self.assertIn("do A", bold[0].text)
                heading = fr.read_flows(checklist(f"### A.{title}.\ndo A\n\n### B. Other\ndo B\n"))
                self.assertEqual([f.label for f in heading], ["A", "B"])
                self.assertIn("do A", heading[0].text)

    def test_a_label_longer_than_the_candidate_bound_is_a_label_in_every_shape(self) -> None:
        long = "12.12.12b-abcdefghijklmnop"
        self.assertGreater(len(long), 24)
        shapes = (
            TABLE_HEAD + f"| {long} | do | ok |\n| Z9 | do | ok |\n",
            f"**{long}. One.** go\n\n**Z9. Two.** go\n",
            f"### {long}. One\ngo\n\n### Z9. Two\ngo\n",
            f"{long}. one\n{long}) two\n",
        )
        for body in shapes:
            with self.subTest(body=body[:20]):
                self.assertEqual(labels(checklist(body))[0], long)
        self.assertEqual(len(labels(checklist(shapes[3]))), 2)

    def test_flow_headings_that_are_not_a_whole_label_keep_the_old_label(self) -> None:
        text = checklist("### Flow F1-retry-again\na\n\n### Flow 6.1.123\nb\n\n### Flow 6.1. Title\nc\n")
        self.assertEqual(labels(text), ["F1-retry-again", "6", "6.1"])

    def test_a_label_after_a_flow_header_ends_the_flow(self) -> None:
        text = checklist("**A. One.** go\n\n**Optional, F1. Extra.** later\n\n**B. Two.** go\n")
        flows = fr.read_flows(text)
        self.assertEqual([f.label for f in flows], ["A", "F1", "B"])
        self.assertNotIn("Optional", flows[0].text)
        self.assertTrue(flows[1].text.startswith("**Optional, F1."))

    def test_the_manager_checklists_read_as_five_flows(self) -> None:
        for name in ("table_f_labels_cache_miss_message.md", "table_f_labels_http_read_test_strength.md"):
            with self.subTest(name=name):
                text = (FIXTURES / name).read_text(encoding="utf-8")
                flows = fr.read_flows(text)
                self.assertEqual([f.label for f in flows], ["F1", "F2", "F3", "F4", "F5"])
                self.assertEqual([f.flow_id for f in flows], [f"flow-{n}" for n in range(1, 6)])
                relabelled = text.replace("| F3 |", "| F3 and F4 |", 1)
                self.assertNotEqual(relabelled, text)
                self.assertEqual(refusal(relabelled)["unrecognised"], ["F3 and F4"])

    def test_a_table_of_no_labels_names_its_first_cells(self) -> None:
        text = checklist(TABLE_HEAD + "| 6 `step` | do | ok |\n| 1 (optional) | do | ok |\n")
        evidence = refusal(text)
        self.assertEqual(evidence["first_cells"], ["6 `step`", "1 (optional)"])
        self.assertIn("6 `step`", evidence["reason"])
        self.assertNotIn("recognised", {key for key in evidence if key == "recognised"})


class FlowEndsTest(unittest.TestCase):
    """D1, D2: a qualified flow header starts a flow, and text the bold shape would leave in no flow is refused."""

    def interior(self, body: str) -> dict:
        evidence = refusal(checklist(body))
        self.assertIn("interior", evidence)
        return evidence

    def test_a_section_line_between_two_flows_is_refused(self) -> None:
        for line in ("**Setup** for this flow: make a directory.", "**Setup: none needed.**", "**Known limitations**",
                     "**Out of scope.** later", "**Test data.**"):
            with self.subTest(line=line):
                evidence = self.interior(f"**A. One.** go\n\n{line}\n\n**B. Two.** go")
                self.assertEqual((evidence["shape"], evidence["recognised"]), ("bold", 2))
                self.assertEqual(evidence["interior"], [{"line": 4, "before": "A", "after": "B"}])
                self.assertIn("only partly recognised", self.message(f"**A. One.** go\n\n{line}\n\n**B. Two.** go"))
                self.assertIn(line[:30], evidence["reason"])

    def message(self, body: str) -> str:
        with self.assertRaises(fr.ChecklistUnrecognised) as caught:
            fr.read_flows(checklist(body))
        return caught.exception.message

    def test_the_same_lines_before_the_first_and_after_the_last_flow_are_read(self) -> None:
        text = ("**Setup** for this flow: make a directory.\n\n**A. One.** go\n\n**B. Two.** go\n\n"
                "**Setup: none needed.**\n\n**Known limitations**\nnotes\n")
        self.assertEqual(labels(checklist(text)), ["A", "B"])

    def test_text_under_a_heading_or_a_flows_line_between_two_flows_is_refused(self) -> None:
        for body in ("### Setup\nmake it", "### Notes for A\nsee A", "**Flows:** run it again with x",
                     "### Notes\n\n**Flows:** run it again with x", "### Notes\n\nsee A"):
            with self.subTest(body=body):
                evidence = self.interior(f"**A. One.** go\n\n{body}\n\n**B. Two.** go")
                self.assertEqual(evidence["interior"][0]["before"], "A")
                self.assertEqual(evidence["interior"][0]["after"], "B")

    def test_a_lone_heading_or_a_lone_flows_line_between_two_flows_is_read(self) -> None:
        for body in ("### Notes", "**Flows:**", "**Flows and expected results**", "### Notes\n\n**Flows:**"):
            with self.subTest(body=body):
                self.assertEqual(labels(checklist(f"**A. One.** go\n\n{body}\n\n**B. Two.** go")), ["A", "B"])

    def test_the_evidence_holds_twenty_lines_at_most(self) -> None:
        body = "**A. One.** go\n\n### Notes\n" + "".join(f"line {n}\n" for n in range(30)) + "\n**B. Two.** go"
        evidence = self.interior(body)
        self.assertEqual(len(evidence["interior"]), 20)
        self.assertIn("30 line(s)", evidence["reason"])
        self.assertIn("'line 0'", evidence["reason"])

    def test_the_named_line_is_cut_at_sixty_characters(self) -> None:
        evidence = self.interior("**A. One.** go\n\n### Notes\n" + "x" * 90 + "\n\n**B. Two.** go")
        self.assertIn("'" + "x" * 60 + "'", evidence["reason"])
        self.assertNotIn("x" * 61, evidence["reason"])

    def test_a_qualified_header_starts_a_flow_and_ends_the_one_before(self) -> None:
        headers = {
            "**Then, 2. Run it again.** go": "2",
            "**Optional K. Title.** go": "K",
            "**Optional K.** Title": "K",
            "**Optional, K.** Title": "K",
            "**Optional K: Title.** go": "K",
            "**Optional extra, K. Title.** go": "K",
            "**optional K. Title.** go": "K",
            "**Required K) Title.** go": "K",
            "**CONDITIONAL K. Title.** go": "K",
        }
        for header, label in headers.items():
            with self.subTest(header=header):
                flows = fr.read_flows(checklist(f"**A. One.** go\n\n{header}\n\n**B. Two.** go"))
                self.assertEqual([f.label for f in flows], ["A", label, "B"])
                self.assertNotIn(header[:12], flows[0].text)
                self.assertTrue(flows[1].text.startswith(header), flows[1].text)
                self.assertNotIn("**B.", flows[1].text)

    def test_a_qualified_header_directly_after_a_flow_line_ends_it_too(self) -> None:
        flows = fr.read_flows(checklist("**A. One.** go\n**Optional K. Title.** go\n**B. Two.** go"))
        self.assertEqual([f.label for f in flows], ["A", "K", "B"])

    def test_a_header_with_more_words_stays_in_its_flow(self) -> None:
        for line in ("**Optional extra K. Title.**", "**If it fails, a. Retry it.**",
                     "**Check step 2. It prints the version.**", "**Verify exit code 0. Output is empty.**",
                     "**Optional.** later"):
            with self.subTest(line=line):
                flows = fr.read_flows(checklist(f"**A. One.** go\n\n{line} more\n\n**B. Two.** go"))
                self.assertEqual([f.label for f in flows], ["A", "B"])
                self.assertIn(line, flows[0].text)

    def test_a_header_spelled_with_a_space_after_the_stars_stays_in_its_flow(self) -> None:
        """It starts no flow, so it must not end one: after the last flow it would be read by nothing."""
        for line in ("** K. Title.** x", "** Then, K. Title.** x", "** Optional K. Title.** x"):
            for after in ("", "\n\n**B. Two.** go"):
                with self.subTest(line=line, after=after):
                    flows = fr.read_flows(checklist(f"**A. One.** go\n\n{line}{after}"))
                    self.assertIn(line, flows[0].text)

    def test_a_qualified_header_with_a_bad_label_is_partly_recognised(self) -> None:
        evidence = refusal(checklist("**A. One.** go\n\n**Optional, 123. Title.** x\n\n**B. Two.** go"))
        self.assertEqual((evidence["shape"], evidence["recognised"], evidence["unrecognised"]), ("bold", 2, ["123"]))
        self.assertNotIn("interior", evidence)

    def test_a_qualified_bold_line_beside_another_shape_is_refused(self) -> None:
        for body in ("### A. One\nrun\n\n**Then, 2. Run it again.**\n\n### B. Two\nrun",
                     "1. a\n2. b\n\n**Optional, K. Extra.** y",
                     TABLE_HEAD + "| A | do | ok |\n| B | do | ok |\n\n**Optional, K. Extra.** y"):
            with self.subTest(body=body[:30]):
                evidence = refusal(checklist(body))
                self.assertIn("dropped", evidence)

    def test_a_plain_bold_line_in_a_heading_flow_stays_flow_text(self) -> None:
        self.assertEqual(labels(checklist("### A. One\nrun\n\n**Expected.** ok\n\n### B. Two\nrun")), ["A", "B"])


class CompletenessTest(unittest.TestCase):
    """D1, D3: a shape that recognises some items recognises all or refuses."""

    def check(self, evidence: dict, shape: str, recognised: int, names: list[str]) -> None:
        self.assertEqual((evidence["shape"], evidence["recognised"], evidence["total"]),
                         (shape, recognised, recognised + len(names)))
        self.assertEqual(evidence["unrecognised"], names)
        self.assertEqual(evidence["section"], fr.SECTION_TITLE)

    def test_a_table_of_nine_rows_with_three_labels(self) -> None:
        rows = "".join(f"| {label} | do | ok |\n" for label in ("A", "B", "C", "3.x", "F1 b2", "Flow 4", "x 1", "y 1", "z 1"))
        evidence = refusal(checklist(TABLE_HEAD + rows))
        self.check(evidence, "table", 3, ["3.x", "F1 b2", "Flow 4", "x 1", "y 1", "z 1"])
        self.assertIn("recognised 3 of 9 items", evidence["reason"])

    def test_the_refusal_names_eight_and_counts_the_rest(self) -> None:
        names = [f"w{n} x" for n in range(12)]
        rows = "| A | do | ok |\n" + "".join(f"| {name} | d | o |\n" for name in names)
        evidence = refusal(checklist(TABLE_HEAD + rows))
        self.assertEqual(len(evidence["unrecognised"]), 12)
        self.assertIn("and 4 more", evidence["reason"])
        self.assertIn("`w7 x`", evidence["reason"])
        self.assertNotIn("`w8 x`", evidence["reason"])

    def test_one_bad_item_in_each_other_shape(self) -> None:
        self.check(refusal(checklist("**A. One.** go\n\n**123. Two.** go\n")), "bold", 1, ["123"])
        self.check(refusal(checklist("### A. One\ngo\n\n### F1b2. Two\ngo\n")), "heading", 1, ["F1b2"])
        self.check(refusal(checklist("1. one\n\n1234. two\n")), "numbered", 1, ["1234"])

    def test_the_message_says_partly_and_the_lead_for_none_stays(self) -> None:
        partial = fr.ChecklistUnrecognised
        with self.assertRaises(partial) as caught:
            fr.read_flows(checklist("**A. One.** go\n\n**123. Two.** go\n"))
        message = caught.exception.message
        self.assertIn("only partly recognised", message)
        self.assertNotIn("no functional flow", message)
        for shape in fr.FLOW_SHAPES:
            self.assertIn(shape, message)
        self.assertIn("functional.automated", message)
        with self.assertRaises(partial) as none:
            fr.read_flows(checklist("Just prose."))
        self.assertIn("no functional flow can be recognised", none.exception.message)
        self.assertNotIn("recognised", none.exception.evidence)

    def test_edge_cases_of_the_candidate_rule(self) -> None:
        continuation = checklist(TABLE_HEAD + "| A | do | ok |\n| | more | steps |\n")
        self.check(refusal(continuation), "table", 1, ["(empty)"])
        self.check(refusal(checklist("**A. One.** go\n\n**Step1.** x\n")), "bold", 1, ["Step1"])
        self.assertEqual(labels(checklist("**A. One.** go\n\n**S1:** x\n")), ["A", "S1"])
        optional = checklist("**A. One.** go\n\n**Optional, K. Title.** extra\n\n**B. Two.** go\n")
        self.assertEqual(labels(optional), ["A", "K", "B"])

    def test_what_is_not_refused(self) -> None:
        bold = checklist("**A. One.** go\n\n**Expected:** x\n\n**Verify.** y\n\n"
                         "| case | want |\n| --- | --- |\n| a b | c |\n\nStep 3. is prose\n\n**B. Two.** go\n")
        self.assertEqual(labels(bold), ["A", "B"])
        # a section after the last flow is not between two flows (D2)
        self.assertEqual(labels(checklist("**A. One.** go\n\n**Setup.** z\n\nprose\n")), ["A"])
        empty_row = checklist(TABLE_HEAD + "| A | do | ok |\n| | | |\n| B | do | ok |\n")
        self.assertEqual(labels(empty_row), ["A", "B"])
        heading = checklist("### A. One\ngo\n\n### Environment, not defects\nnotes\n")
        self.assertEqual(labels(heading), ["A"])
        self.assertEqual(labels(checklist("**AB. word.** x\n\n**B. real.** y\n")), ["B"])
        numbered = checklist("1. one\n   continued 12. here\n2. two\n\nA closing 3. paragraph.\n")
        self.assertEqual(labels(numbered), ["1", "2"])


class ShapeChoiceTest(unittest.TestCase):
    """D7: which shape decides, and a table row only the widened label accepts."""

    REPO_DOC = REPO_ROOT / "docs" / "milestones" / "completed"

    def section_of(self, name: str) -> str:
        path = self.REPO_DOC / name
        if not path.exists():
            self.skipTest(f"{name} is not in this clone")
        return path.read_text(encoding="utf-8")

    def test_a_nested_new_form_table_stays_in_its_heading(self) -> None:
        text = checklist("### A. One\nrun it\n\n| id | do |\n| --- | --- |\n| A1 | x |\n\n"
                         "### C. Read-through\nread the whole guide\n")
        flows = fr.read_flows(text)
        self.assertEqual([f.label for f in flows], ["A", "C"])
        self.assertIn("read the whole guide", flows[1].text)
        self.assertIn("| A1 | x |", flows[0].text)

    def test_a_nested_new_form_row_in_a_bold_or_numbered_flow(self) -> None:
        table = "| id | do |\n| --- | --- |\n| F1 | x |\n"
        self.assertEqual(labels(checklist("**A. One.** go\n\n" + table + "\n**B. Two.** go\n")), ["A", "B"])
        self.assertEqual(labels(checklist("1. one\n   " + table.replace("\n", "\n   ").rstrip() + "\n2. two\n")),
                         ["1", "2"])

    def test_an_old_form_row_anywhere_makes_the_table_decide(self) -> None:
        text = checklist("**A. One.** go\n\n| id | do |\n| --- | --- |\n| B | x |\n")
        self.assertEqual(labels(text), ["B"])
        cases = checklist("### A. One\ngo\n\n| id | do |\n| --- | --- |\n| 1 | x |\n| 2 | x |\n| 2a | x |\n")
        self.assertEqual(labels(cases), ["1", "2", "2a"])

    def test_a_standalone_new_form_table_decides(self) -> None:
        text = checklist(TABLE_HEAD + "| F1 | a | b |\n| F2 | a | b |\n\nA prose line.\n")
        self.assertEqual(labels(text), ["F1", "F2"])

    def test_a_new_form_row_outside_every_span_is_refused(self) -> None:
        before = checklist(TABLE_HEAD + "| F1 | a | b |\n\n### A. Setup\ngo\n")
        self.assertEqual(refusal(before)["unrecognised"], ["F1"])
        numbered = checklist("1. Build the tool.\n2. Create a scratch directory.\n\n" + TABLE_HEAD
                             + "".join(f"| F{n} | a | b |\n" for n in range(1, 6)))
        evidence = refusal(numbered)
        self.assertEqual((evidence["shape"], evidence["recognised"], evidence["unrecognised"]),
                         ("numbered", 2, [f"F{n}" for n in range(1, 6)]))
        ended = checklist("### A. One\ngo\n\n### Notes\n\n" + TABLE_HEAD + "| A1 | a | b |\n")
        self.assertEqual(refusal(ended)["unrecognised"], ["A1"])

    def test_a_row_nested_in_a_shape_that_did_not_decide_is_refused(self) -> None:
        text = checklist("### A. One\ngo\n\n" + TABLE_HEAD + "| F1 | a | b |\n\n**B. Two.** x\n")
        # the bold shape decides; the table lies in the heading's span only
        self.assertEqual(refusal(text)["shape"], "bold")

    def test_the_documentation_reorganisation_checklist_keeps_its_headings(self) -> None:
        text = self.section_of("workflow-controller-documentation-reorganisation.md")
        flows = fr.read_flows(text)
        self.assertEqual([f.label for f in flows], list("ABCDEFGHI"))
        self.assertIn("Read-through", flows[2].text)

    def test_the_settings_and_telemetry_checklist_reads_its_sub_steps_as_flows(self) -> None:
        text = self.section_of("workflow-controller-settings-and-telemetry.md")
        flows = fr.read_flows(text)
        self.assertEqual(len(flows), 29)
        names = [f.label for f in flows]
        start = names.index("B")
        self.assertEqual(names[start:start + 7], ["B", "B1", "B2", "B3", "B4", "B5", "B6"])
        self.assertEqual(len(flows[start].text.strip().splitlines()), 1)


class CrossShapeTest(unittest.TestCase):
    """FL2: a flow another shape recognised is accounted for by position, or refused. Each test states the
    position of its item."""

    OLD = TABLE_HEAD + "| 1 | do | ok |\n| 2 | do | ok |\n"

    def dropped(self, body: str) -> dict:
        evidence = refusal(checklist(body))
        self.assertNotEqual(evidence["recognised"], 0)
        return evidence

    def test_the_heading_before_a_bold_flow_is_refused(self) -> None:
        # position: starts before the bold flow it runs into
        for body in ("### A. One\ngo\n\n**B. Two.** x", "### A. One\ngo\n\n**B. Two.** x\n\n### Notes\nhi"):
            with self.subTest(body=body):
                evidence = self.dropped(body)
                self.assertEqual((evidence["shape"], evidence["recognised"], evidence["total"]), ("bold", 1, 2))
                self.assertEqual(evidence["unrecognised"], ["A"])
                self.assertEqual(evidence["dropped"], [{"shape": "heading", "label": "A"}])

    def test_the_heading_after_a_bold_flow_is_refused(self) -> None:
        # position: starts after the bold flow, which a heading ends
        self.assertEqual(self.dropped("**B. Two.** x\n\n### A. One\ngo")["unrecognised"], ["A"])

    def test_a_heading_that_groups_bold_flows_is_refused(self) -> None:
        # position: starts before the bold flows
        grouped = self.dropped("### A. Group\n\n**A1.** check x\n\n**A2.** check y")
        self.assertEqual((grouped["shape"], grouped["unrecognised"]), ("bold", ["A"]))
        self.assertEqual(labels(checklist("### A. One\n\n**Expected.** check x\n")), ["A"])

    def test_the_second_case_of_the_issue(self) -> None:
        # heading A holds the rows of the table that decided; heading C starts outside every flow
        body = "### A. Login\ngo\n\n" + self.OLD + "\n### C. Read\nread the guide\n"
        evidence = self.dropped(body)
        self.assertEqual((evidence["shape"], evidence["recognised"], evidence["total"]), ("table", 2, 3))
        self.assertEqual(evidence["dropped"], [{"shape": "heading", "label": "C"}])
        self.assertEqual(labels(checklist("### A. Login\ngo\n\n" + self.OLD)), ["1", "2"])
        self.assertEqual(labels(checklist("**A. Login.** go\n\n" + self.OLD)), ["1", "2"])

    def test_one_refusal_per_deciding_shape(self) -> None:
        # table: a bold paragraph ended before the table by a heading, and a numbered item before the table
        ended = self.dropped("**A. One.** go\n\n### Notes\n\n" + self.OLD)
        self.assertEqual(ended["dropped"], [{"shape": "bold", "label": "A"}])
        before = self.dropped("1. Build.\n2. Scratch.\n\n" + self.OLD)
        self.assertEqual(before["unrecognised"], ["1", "2"])
        # bold: a numbered item before the first flow, and a heading before it
        self.assertEqual(self.dropped("1. Build.\n\n**A. One.** go")["dropped"],
                         [{"shape": "numbered", "label": "1"}])
        # heading: a numbered item before the first heading flow
        self.assertEqual(self.dropped("1. Build.\n\n### A. One\ngo")["dropped"],
                         [{"shape": "numbered", "label": "1"}])
        # Flows is no non-flow region
        for flows in ("### Flows", "**Flows.**"):
            with self.subTest(flows=flows):
                self.assertEqual(self.dropped(f"**A. One.** go\n\n{flows}\n\n1. f\n2. g\n")["unrecognised"],
                                 ["1", "2"])
        # a numbered list in a table holder's prose is not passed to the tester (LMPR-R6-O2)
        holder = self.dropped("### A. Login\n\n1. open\n2. type\n\n" + self.OLD)
        self.assertEqual((holder["shape"], holder["unrecognised"]), ("table", ["1", "2"]))

    def test_flows_after_a_region_are_candidates(self) -> None:
        for setup in ("**Setup.**", "**Setup**"):
            for flows in ("**Flows.**", "**Flows and expected results.**", "**Flows and expected results**"):
                for decider in ("**A. One.** go\n\n", self.OLD + "\n"):
                    with self.subTest(setup=setup, flows=flows, decider=decider[:6]):
                        body = f"{decider}{setup}\n\n1. prepare\n\n{flows}\n\n1. f\n2. g\n"
                        evidence = self.dropped(body)
                        self.assertEqual(evidence["unrecognised"], ["1", "2"])

    def test_forms_that_are_not_refused(self) -> None:
        nested = "**A. One.** go\n1. step\n\n| id | do |\n| --- | --- |\n| F1 | x |\n\n**B. Two.** go"
        self.assertEqual(labels(checklist(nested)), ["A", "B"])  # a numbered list after the first line
        in_heading = "### A. One\nrun\n\n1. step\n\n| id | do |\n| --- | --- |\n| A1 | x |\n\n**Expected.** ok\n"
        self.assertEqual(labels(checklist(in_heading)), ["A"])
        for region in ("### Setup", "### Test data", "### Known limitations", "### Out of scope"):
            with self.subTest(region=region):
                self.assertEqual(labels(checklist(f"{region}\n\n1. a\n2. b\n\n**A. One.** go")), ["A"])
        for opener in ("**Setup.**", "**Out of scope**"):
            for end in ("### Next", "**Test data.**", "**Flows.**"):
                with self.subTest(opener=opener, end=end):
                    text = checklist(f"**A. One.** go\n\n{opener}\n\n1. a\n2. b\n\n{end}\n")
                    self.assertEqual(labels(text)[0], "A")
            # between two flows the section's text is read by no flow, so it is refused (D2)
            with self.subTest(opener=opener, end="flow"):
                refusal(checklist(f"**A. One.** go\n\n{opener}\n\n1. a\n2. b\n\n**B. flow.** x\n"))
        pr_defect = checklist("### Setup\n\n1. a\n2. b\n3. c\n\n" + self.OLD)
        self.assertEqual(labels(pr_defect), ["1", "2"])
        outside = refusal(checklist("1. a\n2. b\n\n" + self.OLD))
        self.assertEqual(outside["unrecognised"], ["1", "2"])

    def test_the_evidence_names_eight_then_the_count(self) -> None:
        body = "".join(f"### {c}. h\nx\n\n" for c in "BCDEFGHIJK") + "**A. One.** go"
        evidence = refusal(checklist(body))
        self.assertEqual((evidence["total"], evidence["unrecognised"]), (11, list("BCDEFGHIJK")))
        with self.assertRaises(fr.ChecklistUnrecognised) as caught:
            fr.read_flows(checklist(body))
        self.assertIn("`I` (heading), and 2 more", caught.exception.message)

    def test_a_partly_recognised_deciding_shape_is_refused_first(self) -> None:
        evidence = refusal(checklist("**A. One.** go\n\n**B2x3x.** bad\n\n### C. Two\n"))
        self.assertNotIn("dropped", evidence)

    def test_the_historical_checklists_read_as_before(self) -> None:
        done = REPO_ROOT / "docs" / "milestones" / "completed"
        expected = {
            "workflow-controller-pr-defect-loop.md": [str(n) for n in range(1, 13)],
            "workflow-controller-ci-reliability.md": list("ABCDEFGHIJ"),
        }
        for name, want in expected.items():
            path = done / name
            if not path.exists():
                continue
            with self.subTest(name=name):
                self.assertEqual(labels(path.read_text(encoding="utf-8")), want)
        for name in ("workflow-controller-round-caps.md", "workflow-controller-codex-reviewer.md"):
            path = done / name
            if path.exists():
                with self.subTest(name=name):
                    self.assertTrue(labels(path.read_text(encoding="utf-8")))
        self.assertEqual(labels((FIXTURES / "bold_labelled_454ffe7.md").read_text(encoding="utf-8")),
                         list("ABCDEFGHIJK"))


class DroppedRowsTest(unittest.TestCase):
    """D6: two committed checklists the old reader read "completely" while
    dropping rows whose label it did not know (``L1``..``L4``, ``7b``)."""

    def read(self, name: str) -> list[str]:
        path = REPO_ROOT / "docs" / "milestones" / "completed" / name
        if not path.exists():
            self.skipTest(f"{name} is not in this clone")
        return labels(path.read_text(encoding="utf-8"))

    def test_rows_the_old_reader_dropped_are_now_flows(self) -> None:
        sharding = self.read("workflow-controller-adaptive-test-sharding.md")
        self.assertEqual(sharding[-4:], ["L1", "L2", "L3", "L4"])
        self.assertEqual(len(sharding), 17)
        limit = self.read("workflow-controller-usage-limit-mid-job.md")
        self.assertEqual(limit, ["1", "2", "3", "4", "5", "6", "7", "7b", "8", "9"])


class LinkedChecklistTest(unittest.TestCase):
    def reader(self, files: dict[str, str]):
        asked: list[str] = []

        def read(path: str) -> str | None:
            asked.append(path)
            return files.get(path)

        read.asked = asked  # type: ignore[attr-defined]
        return read

    def test_a_section_that_only_links_a_repository_file_is_read_from_it(self) -> None:
        read = self.reader({"docs/checklist.md": "# Checklist\n\n**A. one.** x\n\n**B. two.** y\n"})
        text = checklist("See [the checklist](docs/checklist.md#top).")
        # Prose around a link is not "only a link": it has no flow.
        with self.assertRaises(fr.ChecklistUnrecognised):
            fr.read_flows(text, read_file=read)
        for body in ("[the checklist](docs/checklist.md)", "- [the checklist](./docs/../docs/checklist.md#x)"):
            with self.subTest(body=body):
                flows = fr.read_flows(checklist(body), read_file=read)
                self.assertEqual([f.label for f in flows], ["A", "B"])
        self.assertEqual(set(read.asked), {"docs/checklist.md"})

    def test_a_linked_file_uses_the_same_rules_including_headings_below_its_title(self) -> None:
        read = self.reader({"c.md": "# Title\n\n## A. one\nx\n\n## B. two\ny\n"})
        flows = fr.read_flows(checklist("[c](c.md)"), read_file=read)
        self.assertEqual([f.label for f in flows], ["A", "B"])

    def joining_reader(self, files: dict[str, str], folder: str = "docs"):
        """A reader that joins the link to the checklist's folder and refuses a result outside the repository, as
        ``candidate_flows.read_file`` does."""
        asked: list[str] = []

        def read(link: str) -> str | None:
            asked.append(link)
            joined = posixpath.normpath(posixpath.join(folder, link))
            if posixpath.isabs(joined) or joined == ".." or joined.startswith("../"):
                return None
            return files.get(joined)

        read.asked = asked  # type: ignore[attr-defined]
        return read

    def test_a_link_that_cannot_be_a_repository_path_is_refused_without_asking_the_reader(self) -> None:
        read = self.reader({"c.md": "**A. one.** x\n"})
        for target in ("/etc/passwd", "https://example.com/c.md", "~/c.md", "#anchor"):
            with self.subTest(target=target), self.assertRaises(fr.ChecklistUnrecognised):
                fr.read_flows(checklist(f"[x]({target})"), read_file=read)
        self.assertEqual(read.asked, [])
        with self.assertRaises(fr.ChecklistUnrecognised):
            fr.read_flows(checklist("[x](c.md)"))  # no reader: nothing can be read

    def test_a_missing_link_is_refused_with_the_containment_wording(self) -> None:
        read = self.reader({"c.md": "**A. one.** x\n"})
        with self.assertRaises(fr.ChecklistUnrecognised) as caught:
            fr.read_flows(checklist("[x](missing.md)"), read_file=read)
        self.assertEqual(read.asked, ["missing.md"])
        self.assertIn("resolves from the checklist's own folder", str(caught.exception))
        self.assertIn("inside the repository", str(caught.exception))

    def test_a_link_one_folder_up_is_followed_when_the_join_stays_inside_the_repository(self) -> None:
        read = self.joining_reader({"tests/checklist.md": "**A. one.** x\n\n**B. two.** y\n",
                                    "c.md": "**A. one.** x\n\n**B. two.** y\n"})
        for target, asked in (("../tests/checklist.md", "../tests/checklist.md"), ("a/../../c.md", "../c.md")):
            with self.subTest(target=target):
                flows = fr.read_flows(checklist(f"[x]({target})"), read_file=read)
                self.assertEqual([f.label for f in flows], ["A", "B"])
                self.assertEqual(read.asked[-1], asked)

    def test_a_link_that_leaves_the_repository_is_refused_after_the_join(self) -> None:
        read = self.joining_reader({"c.md": "**A. one.** x\n"})
        for target in ("../../outside.md", "a/../../../c.md"):
            with self.subTest(target=target), self.assertRaises(fr.ChecklistUnrecognised) as caught:
                fr.read_flows(checklist(f"[x]({target})"), read_file=read)
            self.assertIn("inside the repository", str(caught.exception))
            self.assertEqual(caught.exception.evidence["link"], posixpath.normpath(target))

    def real_candidate(self, tmp: str, target: str) -> fr.Candidate:
        """A committed repository whose ``docs/ACTIVE_MILESTONE.md`` links ``target``, with a file of flows beside the
        repository that a link must never reach."""
        flows = "**A. one.** x\n\n**B. two.** y\n"
        repo = Path(tmp) / "repo"
        (repo / "docs").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "docs" / "ACTIVE_MILESTONE.md").write_text(checklist(f"[c]({target})"), encoding="utf-8")
        (repo / "tests" / "checklist.md").write_text(flows, encoding="utf-8")
        (repo / "c.md").write_text(flows, encoding="utf-8")
        (Path(tmp) / "outside.md").write_text(flows, encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        for argv in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
            subprocess.run(["git", "-C", str(repo), *argv], check=True, env=env, capture_output=True)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()
        return fr.capture_candidate(repo, head)

    def test_candidate_flows_follows_a_link_that_stays_inside_a_real_tree(self) -> None:
        for target in ("../tests/checklist.md", "a/../../c.md"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                flows = fr.candidate_flows(self.real_candidate(tmp, target))
                self.assertEqual([f.label for f in flows], ["A", "B"])

    def test_candidate_flows_refuses_a_link_that_leaves_a_real_tree(self) -> None:
        for target in ("../../outside.md", "a/../../../outside.md", "../.."):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                candidate = self.real_candidate(tmp, target)
                with self.assertRaises(fr.ChecklistUnrecognised) as caught:
                    fr.candidate_flows(candidate)
                self.assertIn("inside the repository", str(caught.exception))
                self.assertNotIn("recognised", caught.exception.evidence)

    def test_candidate_flows_refuses_a_link_to_a_folder_of_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(fr.ChecklistUnrecognised) as caught:
                fr.candidate_flows(self.real_candidate(tmp, "../tests"))
            self.assertIn("a tracked file", str(caught.exception))


class MissingSectionTest(unittest.TestCase):
    def refusal(self, text: str) -> fr.ChecklistUnrecognised:
        with self.assertRaises(fr.ChecklistUnrecognised) as caught:
            fr.read_flows(text)
        return caught.exception

    def test_the_reason_names_the_heading_to_write_and_lists_near_misses_with_lines(self) -> None:
        text = ("# Active\n\n## Functional-review checklist (round 2)\n\n**A. one.** x\n\n"
                "### Review checklist\n\n## Unrelated functional notes\n\n## Checklist\n")
        error = self.refusal(text)
        self.assertIn("'Functional review checklist'", str(error))
        self.assertEqual(error.evidence["near_headings"], [
            {"line": 3, "heading": "Functional-review checklist (round 2)"}, {"line": 7, "heading": "Review checklist"}])
        self.assertIn("line 3", str(error))
        self.assertNotIn("Unrelated", str(error))
        self.assertNotIn("'Checklist'", str(error))

    def test_a_fenced_heading_is_not_listed_and_the_list_is_bounded(self) -> None:
        fenced = self.refusal("```\n## Functional checklist\n```\n")
        self.assertEqual(fenced.evidence["near_headings"], [])
        many = self.refusal("\n".join(f"## Review checklist {n}" for n in range(9)))
        self.assertEqual(len(many.evidence["near_headings"]), fr.NEAR_MISS_HEADINGS)


class CommittedChecklistsTest(unittest.TestCase):
    """The numbering over this repository's own checklists (D4): the sections
    vendored verbatim, so the test never depends on history."""

    EXPECTED = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    #: The bold fixture ends with ``**Optional, K. ...**``, which is a flow (D1); the table fixture stays A to J.
    BOLD_EXPECTED = EXPECTED + ["K"]

    def numbered(self, name: str) -> list[fr.Flow]:
        return fr.read_flows((FIXTURES / name).read_text(encoding="utf-8"))

    def test_bold_labelled_paragraphs_a_to_j(self) -> None:
        flows = self.numbered("bold_labelled_454ffe7.md")
        self.assertEqual([f.label for f in flows], self.BOLD_EXPECTED)
        self.assertEqual([f.flow_id for f in flows], [f"flow-{n}" for n in range(1, 12)])
        for flow in flows:
            if flow.label != "K":
                self.assertTrue(flow.text.startswith(f"**{flow.label}."), flow.text[:20])
        self.assertTrue(flows[-1].text.startswith("**Optional, K."), flows[-1].text[:20])
        for word in ("Setup", "Flows", "Test data", "Known limitations"):
            self.assertFalse(any(flow.text.startswith(f"**{word}") for flow in flows))

    def test_a_bold_flow_keeps_its_table_across_blank_lines(self) -> None:
        flows = {f.label: f for f in self.numbered("bold_labelled_454ffe7.md")}
        text = flows["E"].text
        for case in ("| `feat: add x` | `ok: feat → minor`, exit 0 |", "| `Update stuff` |",
                     "In the repository itself (the pre-cutover"):
            self.assertIn(case, text)
        self.assertNotIn("Optional, K.", flows["J"].text)
        self.assertNotIn("Known limitations", flows["K"].text)

    def test_a_bold_expectation_paragraph_is_never_dropped(self) -> None:
        text = ("## Functional review checklist\n\n**A. Local version.** Run it.\n\n"
                "**Verify exit code 0. Output is empty.**\n\n**Check step 2. It prints the version.**\n\n"
                "**Then run it with option b. Nothing changes.**\n\n**Setup is unchanged afterwards.**\n\n"
                "**B. Other.** go\n\n**Optional, K. Title.** extra\n\n**Setup**\nnotes\n")
        flows = fr.read_flows(text)
        by_label = {f.label: f.text for f in flows}
        for kept in ("Verify exit code 0. Output is empty.", "Check step 2. It prints the version.",
                     "Then run it with option b. Nothing changes.", "Setup is unchanged afterwards."):
            self.assertIn(kept, by_label["A"])
        self.assertNotIn("Optional, K.", by_label["B"])
        self.assertNotIn("Setup", by_label["B"])

    def test_table_rows_a_to_j(self) -> None:
        flows = self.numbered("table_rows_eeeb4fa.md")
        self.assertEqual([f.label for f in flows], self.EXPECTED)
        for flow in flows:
            self.assertTrue(flow.text.startswith(f"| {flow.label} |"), flow.text[:20])

    def _from_git(self, commit: str, name: str) -> None:
        shown = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{commit}:docs/ACTIVE_MILESTONE.md"],
                               capture_output=True, text=True)
        if shown.returncode != 0:
            self.skipTest(f"{commit} is not in this clone")
        section = fr.checklist_section(shown.stdout)
        vendored = (FIXTURES / name).read_text(encoding="utf-8")
        self.assertEqual("## Functional review checklist\n" + "\n".join(section.lines).rstrip("\n") + "\n", vendored)

    def test_the_vendored_sections_are_byte_for_byte_the_commits(self) -> None:
        self._from_git("454ffe7", "bold_labelled_454ffe7.md")
        self._from_git("eeeb4fa", "table_rows_eeeb4fa.md")


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return path


FLOWS = [fr.Flow("flow-1", "A", "**A. one.** x"), fr.Flow("flow-2", "B", "**B. two.** y"),
         fr.Flow("flow-3", "C", "**C. three.** z")]


class ValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "out"
        self.out.mkdir()
        self.outside = Path(self._tmp.name) / "outside.log"
        self.outside.write_text("host file\n")

    def results(self, entries) -> None:
        write(self.out / "RESULTS.json", json.dumps(entries))

    def good(self, flow_id: str = "flow-1", **extra) -> dict:
        write(self.out / f"{flow_id}.log", f"log of {flow_id}\n")
        return {"flow_id": flow_id, "status": "passed", "summary": "ok", "log": f"{flow_id}.log", **extra}

    def refused(self) -> fr.ResultsInvalid:
        with self.assertRaises(fr.ResultsInvalid) as caught:
            fr.validate_results(self.out, FLOWS)
        self.assertEqual(caught.exception.code, "results_invalid")
        return caught.exception

    def test_a_valid_file_gives_digested_entries(self) -> None:
        failed = self.good("flow-2", status="failed", reason="defect")
        self.results([self.good("flow-1"), failed])
        entries = fr.validate_results(self.out, FLOWS)
        self.assertEqual([(e.flow_id, e.status, e.reason) for e in entries],
                         [("flow-1", "passed", None), ("flow-2", "failed", "defect")])
        self.assertEqual(entries[0].log_digest, fr.sha256_bytes(b"log of flow-1\n"))
        self.assertEqual(entries[0].log_path, self.out / "flow-1.log")
        self.assertIsNone(entries[0].log_content)

    def test_every_structural_fault_is_refused(self) -> None:
        good = self.good("flow-1")
        cases = {
            "no file": None,
            "not json": "{nope",
            "not a list": {"flow_id": "flow-1"},
            "not an object entry": ["flow-1"],
            "unknown key": [{**good, "extra": 1}],
            "missing key": [{k: v for k, v in good.items() if k != "summary"}],
            "duplicate id": [good, dict(good)],
            "id is no flow": [{**good, "flow_id": "flow-9"}],
            "bad status": [{**good, "status": "skipped"}],
            "failed without reason": [{**good, "status": "failed"}],
            "failed with bad reason": [{**good, "status": "failed", "reason": "flaky"}],
            "reason on a passed entry": [{**good, "reason": "defect"}],
            "summary not a string": [{**good, "summary": 3}],
            "log outside the folder": [{**good, "log": "../outside.log"}],
            "log absolute": [{**good, "log": str(self.outside)}],
            "log missing": [{**good, "log": "nope.log"}],
            "log a directory": [{**good, "log": "."}],
            "log not a string": [{**good, "log": None}],
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                if document is None:
                    (self.out / "RESULTS.json").unlink(missing_ok=True)
                elif isinstance(document, str):
                    write(self.out / "RESULTS.json", document)
                else:
                    self.results(document)
                self.refused()

    def test_a_duplicate_json_key_is_refused(self) -> None:
        write(self.out / "RESULTS.json", '[{"flow_id": "flow-1", "flow_id": "flow-2", "status": "passed", '
                                          '"summary": "s", "log": "x"}]')
        self.assertIn("duplicate", self.refused().message)

    def test_a_log_symlink_outside_or_inside_the_folder_is_refused_and_never_opened(self) -> None:
        inside = write(self.out / "real.log", "real\n")
        (self.out / "link-out.log").symlink_to(self.outside)
        (self.out / "link-in.log").symlink_to(inside)
        for name in ("link-out.log", "link-in.log"):
            with self.subTest(log=name):
                self.results([{**self.good("flow-1"), "log": name}])
                opened: list[str] = []
                real_open = os.open

                def spy(path, *args, **kwargs):
                    opened.append(str(path))
                    return real_open(path, *args, **kwargs)

                with mock.patch.object(fr.os, "open", spy):
                    self.assertIn("symlink", self.refused().message)
                self.assertNotIn(str(self.out / name), opened)
                self.assertNotIn(str(self.outside), opened)

    def test_a_log_through_a_symlinked_directory_is_refused(self) -> None:
        (self.out / "dir").symlink_to(self.outside.parent)
        self.results([{**self.good("flow-1"), "log": "dir/outside.log"}])
        self.assertIn("symlink", self.refused().message)

    def test_a_results_file_symlink_is_refused_with_its_target_never_opened(self) -> None:
        target = write(Path(self._tmp.name) / "elsewhere.json", json.dumps([self.good("flow-1")]))
        (self.out / "RESULTS.json").symlink_to(target)
        opened: list[str] = []
        real_open = os.open

        def spy(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        with mock.patch.object(fr.os, "open", spy):
            self.assertIn("symlink", self.refused().message)
        self.assertEqual(opened, [])
        # A dangling symlink is a symlink too, not "not written".
        (self.out / "RESULTS.json").unlink()
        (self.out / "RESULTS.json").symlink_to(Path(self._tmp.name) / "absent.json")
        self.assertIn("symlink", self.refused().message)

    def test_a_fifo_log_is_refused_without_blocking(self) -> None:
        os.mkfifo(self.out / "pipe.log")
        self.results([{**self.good("flow-1"), "log": "pipe.log"}])
        self.assertIn("not a regular file", self.refused().message)

    def test_an_oversized_results_file_is_refused(self) -> None:
        write(self.out / "RESULTS.json", b"[" + b" " * (fr.MAX_RESULTS_BYTES + 1) + b"]")
        self.assertIn("larger", self.refused().message)


class NotRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def entry(self, flow_id: str, status: str = "passed", reason=None) -> fr.Entry:
        log = write(self.out / f"{flow_id}.log", f"{flow_id}\n")
        return fr.Entry(flow_id, status, "s", reason, log, fr.sha256_file(log))

    def test_a_flow_left_out_is_a_failed_environment_entry_with_a_valid_digest(self) -> None:
        log_dir = self.out / "controller-logs"
        completed = fr.complete_entries(log_dir, FLOWS, [self.entry("flow-2")], timed_out=False, job_id=JOB)
        self.assertEqual([e.flow_id for e in completed], ["flow-1", "flow-2", "flow-3"])
        left = completed[0]
        self.assertEqual((left.status, left.reason), ("failed", "environment"))
        self.assertEqual(left.summary, "not run: the tester left this flow out of the results file")
        # The log lies under the caller's log folder, which is not the output folder the tester writes.
        self.assertEqual(left.log_path, log_dir / "flow-1.not-run.log")
        self.assertNotEqual(left.log_path.parent, self.out)
        self.assertEqual(left.log_digest, fr.sha256_bytes(left.log_content))
        self.assertRegex(left.log_digest, r"^[0-9a-f]{64}$")
        self.assertIn(JOB.encode(), left.log_content)
        self.assertIs(completed[1], completed[1])
        self.assertIsNone(completed[1].log_content)

    def test_a_timeout_names_itself_and_the_setting(self) -> None:
        completed = fr.complete_entries(self.out, FLOWS[:1], [], timed_out=True, job_id=JOB, timeout_seconds=900)
        self.assertEqual(completed[0].summary, "not run: the tester ended at the timeout")
        self.assertIn(b"functional.timeout_seconds: 900", completed[0].log_content)
        left_out = fr.complete_entries(self.out, FLOWS[:1], [], timed_out=False, job_id=JOB, timeout_seconds=900)
        self.assertNotIn(b"timeout_seconds", left_out[0].log_content)

    def test_the_entry_is_a_well_formed_evidence_document_not_malformed(self) -> None:
        completed = fr.complete_entries(self.out, FLOWS[:1], [], timed_out=False, job_id=JOB)
        document = fr.evidence_input(completed[0], head=HEAD, ran_at="2026-10-19T12:00:00Z", job_id=JOB,
                                     version="1.17.0", checklist_blob=BLOB)
        protocol_schema.SCHEMA._validate(document, protocol_schema.SCHEMA._resolve(EVIDENCE_POINTER, "#"),
                                         EVIDENCE_POINTER, "$")
        self.assertEqual(document["summary"]["reason"], "environment")

    def test_the_outcome_of_the_entries(self) -> None:
        passed = self.entry("flow-1")
        defect = self.entry("flow-2", "failed", "defect")
        env = self.entry("flow-3", "failed", "environment")
        self.assertEqual(fr.outcome_of([passed]), fr.RECORDED)
        self.assertEqual(fr.outcome_of([passed, env]), fr.ENVIRONMENT_LIMITED)
        self.assertEqual(fr.outcome_of([passed, defect, env]), fr.FINDINGS)


EVIDENCE_POINTER = "#/$defs/inputs/properties/functional_evidence"


class SummaryBoundTest(ValidatorTest):
    """D5: a tester's summary is bounded where it enters the Controller."""

    def entry(self, summary: str, **extra) -> fr.Entry:
        write(self.out / "flow-1.log", "log of flow-1\n")
        self.results([{"flow_id": "flow-1", "status": "failed", "reason": "defect", "summary": summary,
                       "log": "flow-1.log", **extra}])
        return fr.validate_results(self.out, FLOWS)[0]

    def test_a_summary_at_the_limit_is_kept_byte_for_byte(self) -> None:
        summary = "é" * fr.SUMMARY_LIMIT
        self.assertEqual(self.entry(summary).summary, summary)

    def test_one_character_over_is_cut_with_a_marker_naming_the_omitted_count(self) -> None:
        summary = "a" * (fr.SUMMARY_LIMIT + 1)
        bounded = self.entry(summary).summary
        self.assertTrue(bounded.startswith("a" * fr.SUMMARY_LIMIT))
        self.assertEqual(bounded[fr.SUMMARY_LIMIT:], "[... 1 character omitted]")
        long = self.entry("b" * (fr.SUMMARY_LIMIT + 4000)).summary
        self.assertIn("4000 characters omitted", long)
        self.assertLess(len(long), fr.SUMMARY_LIMIT + 100)

    def test_the_findings_text_and_the_evidence_record_carry_the_bounded_string(self) -> None:
        entry = self.entry("secret " * 1000)
        self.assertEqual(len(entry.summary.split("[...")[0]), fr.SUMMARY_LIMIT)
        document = fr.evidence_input(entry, head=HEAD, ran_at="2026-10-19T12:00:00Z", job_id=JOB,
                                     version="1.17.0", checklist_blob=BLOB)
        self.assertEqual(document["summary"]["text"], entry.summary)
        text = fr.findings_text([entry], FLOWS, head=HEAD, checklist_blob=BLOB, job_id=JOB)
        self.assertIn(f"Tester summary: {entry.summary}", text)
        self.assertNotIn("secret " * 1000, text)

    def test_the_log_digest_is_the_digest_of_the_untouched_log(self) -> None:
        entry = self.entry("c" * (fr.SUMMARY_LIMIT * 3))
        self.assertEqual(entry.log_digest, fr.sha256_bytes(b"log of flow-1\n"))

    def test_a_non_string_summary_is_still_refused(self) -> None:
        write(self.out / "flow-1.log", "x\n")
        self.results([{"flow_id": "flow-1", "status": "passed", "summary": ["x" * 5000], "log": "flow-1.log"}])
        self.assertIn("summary is not a string", self.refused().message)


class EvidenceInputTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        log = write(Path(self._tmp.name) / "flow-1.log", "output\n")
        self.passed = fr.Entry("flow-1", "passed", "it works", None, log, fr.sha256_file(log))
        self.failed = fr.Entry("flow-2", "failed", "it broke", "defect", log, fr.sha256_file(log))

    def document(self, entry: fr.Entry) -> dict:
        return fr.evidence_input(entry, head=HEAD, ran_at="2026-10-19T12:00:00Z", job_id=JOB, version="1.17.0",
                                 checklist_blob=BLOB)

    def test_the_document_matches_the_vendored_schema(self) -> None:
        node = protocol_schema.SCHEMA._resolve(EVIDENCE_POINTER, "#")
        for entry in (self.passed, self.failed):
            document = self.document(entry)
            protocol_schema.SCHEMA._validate(document, node, EVIDENCE_POINTER, "$")
            self.assertEqual(set(document), set(node["required"]))
        broken = {k: v for k, v in self.document(self.passed).items() if k != "log_digest"}
        with self.assertRaises(protocol_schema.SchemaViolation):
            protocol_schema.SCHEMA._validate(broken, node, EVIDENCE_POINTER, "$")

    def test_the_fields(self) -> None:
        document = self.document(self.failed)
        self.assertEqual(document["flow_id"], "flow-2")
        self.assertEqual(document["status"], "failed")
        self.assertEqual(document["head"], HEAD)
        self.assertEqual(document["run_ref"], f"controller:{JOB}")
        self.assertEqual(document["reporter"], "workflow-controller 1.17.0")
        self.assertEqual(document["summary"], {"text": "it broke", "checklist_blob": BLOB, "reason": "defect"})
        self.assertEqual(document["log_digest"], self.failed.log_digest)
        self.assertEqual(self.document(self.passed)["summary"], {"text": "it works", "checklist_blob": BLOB})

    def test_the_document_has_the_fields_the_vendored_recorder_checks(self) -> None:
        # The vendored 2.12.0 ingest checks exactly ``_FUNCTIONAL_FIELDS`` and
        # a 64-hex ``log_digest``. Read as source, never imported: importing
        # would write bytecode into the vendored tree, which
        # ``workflow_releases check`` refuses.
        source = (REPO_ROOT / "tests" / "workflow_releases" / "2.12.0" / "scripts"
                  / "workflow_gate_policy.py").read_text(encoding="utf-8")
        fields = ast.literal_eval(re.search(r"^_FUNCTIONAL_FIELDS = (\(.*\))$", source, re.M).group(1))
        self.assertEqual(set(self.document(self.passed)), set(fields))
        self.assertRegex(self.document(self.passed)["log_digest"], r"^[0-9a-f]{64}$")
        self.assertIn('_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")', source)


class FindingsTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        out = Path(self._tmp.name)
        self.defect_log = write(out / "d.log", "line\nFAILED: expected 3 got 4\n")
        self.env_log = write(out / "e.log", "no network\n")
        self.ok_log = write(out / "p.log", "fine\n")

    def entries(self, defect_summary: str = "wrong count") -> list[fr.Entry]:
        return [
            fr.Entry("flow-1", "passed", "fine", None, self.ok_log, fr.sha256_file(self.ok_log)),
            fr.Entry("flow-2", "failed", defect_summary, "defect", self.defect_log, fr.sha256_file(self.defect_log)),
            fr.Entry("flow-3", "failed", "needs the network", "environment", self.env_log,
                     fr.sha256_file(self.env_log)),
        ]

    def text(self, entries=None, *, head: str = HEAD, job: str = JOB) -> str | None:
        return fr.findings_text(entries if entries is not None else self.entries(), FLOWS, head=head,
                                checklist_blob=BLOB, job_id=job)

    def test_only_defect_flows_are_findings(self) -> None:
        text = self.text()
        self.assertIn("## flow-2 (B): failed", text)
        self.assertIn("Tester summary: wrong count", text)
        self.assertIn("FAILED: expected 3 got 4", text)
        self.assertNotIn("flow-1", text)
        self.assertNotIn("flow-3", text)
        self.assertNotIn("needs the network", text)
        self.assertTrue(text.endswith("\n"))

    def test_no_findings_when_every_failure_is_environment(self) -> None:
        only_env = [e for e in self.entries() if e.flow_id != "flow-2"]
        self.assertIsNone(self.text(only_env))
        self.assertIsNone(self.text([]))

    def test_the_header_names_the_head_the_blob_and_the_run(self) -> None:
        text = self.text()
        self.assertEqual(text.splitlines()[0], "# Functional review findings (automated tester)")
        for line in (f"Tested head: {HEAD}", f"Checklist blob: {BLOB}", f"Run: controller:{JOB}"):
            self.assertIn(line, text.splitlines()[:6])

    def test_identical_failure_text_at_two_heads_gives_two_different_files(self) -> None:
        first = self.text(head="1" * 40, job="job-1")
        second = self.text(head="2" * 40, job="job-2")
        self.assertNotEqual(first, second)
        self.assertNotEqual(fr.sha256_bytes(first.encode()), fr.sha256_bytes(second.encode()))
        again = self.text(head="1" * 40, job="job-1")
        self.assertEqual(first, again)

    def test_a_backtick_run_in_the_log_cannot_close_the_excerpt_fence(self) -> None:
        write(self.defect_log, "before\n```\ninjected\n```\n")
        text = self.text()
        self.assertIn("````text", text)
        self.assertIn("injected", text)

    def test_the_excerpt_is_the_tail_of_the_log(self) -> None:
        write(self.defect_log, "x" * 5000 + "TAIL")
        excerpt = fr.log_excerpt(self.entries()[1])
        self.assertEqual(len(excerpt), fr.LOG_EXCERPT_CHARS)
        self.assertTrue(excerpt.endswith("TAIL"))

    def test_the_text_is_in_the_shape_the_apply_command_reads(self) -> None:
        # ``/apply-functional-review`` reads ``FUNCTIONAL_REVIEW.md`` from the
        # path the Workflow's contract answers and classifies each finding;
        # the vendored command names no stricter format, so the text is a
        # markdown findings document with one section per flow.
        command = (REPO_ROOT / ".claude" / "commands" / "apply-functional-review.md").read_text(encoding="utf-8")
        self.assertIn("FUNCTIONAL_REVIEW.md", command)
        self.assertIn("Classify each finding", command)
        sections = [line for line in self.text().splitlines() if line.startswith("## ")]
        self.assertEqual(sections, ["## flow-2 (B): failed"])


GITIGNORE = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


class WritableLocationsTest(unittest.TestCase):
    def derive(self, text: str, manifest=("controller/a.py", "README.md")):
        return fr.derive_writable_locations(text, manifest)

    def test_this_repository_s_gitignore(self) -> None:
        manifest = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True,
                                  check=True).stdout.split()
        admitted, refused = self.derive(GITIGNORE, manifest)
        self.assertEqual(admitted, ("__pycache__", ".venv", ".idea", ".vscode", ".ai-review", ".controller",
                                    "build", "dist"))
        patterns = [item.pattern for item in refused]
        for pattern in ("*.py[cod]", "*.pyc", "*.egg-info/", "controller/BUILD_INFO.json",
                        ".claude/settings.local.json"):
            self.assertIn(pattern, patterns)
        self.assertEqual(len(patterns), len(set(patterns)))
        self.assertFalse(set(patterns) & {f"{name}/" for name in admitted})

    def test_refusals_carry_their_reason(self) -> None:
        text = "!keep/\n*.log\nfile.txt\nsub/dir/\n.git/\n/a/b/\nq?/\nw[ab]/\ne\\ /\nREADME.md/\n"
        admitted, refused = self.derive(text)
        self.assertEqual(admitted, ())
        reasons = {item.pattern: item.reason for item in refused}
        self.assertEqual(reasons["!keep/"], "a negation")
        self.assertEqual(reasons["*.log"], "a glob")
        self.assertEqual(reasons["q?/"], "a glob")
        self.assertEqual(reasons["w[ab]/"], "a glob")
        self.assertIn("file pattern", reasons["file.txt"])
        self.assertIn("single directory", reasons["sub/dir/"])
        self.assertIn("single directory", reasons["/a/b/"])
        self.assertIn(".git", reasons[".git/"])
        self.assertIn("tracked", reasons["README.md/"])

    def test_a_leading_slash_is_admitted_and_comments_and_blanks_are_skipped(self) -> None:
        admitted, refused = self.derive("# comment\n\n/out/\nout/\ncache/   \n")
        self.assertEqual(admitted, ("out", "cache"))
        self.assertEqual(refused, ())

    def test_a_directory_that_holds_a_manifest_path_is_refused(self) -> None:
        admitted, refused = self.derive("src/\nbuild/\n", ["src/main.py", "buildx/y.py"])
        self.assertEqual(admitted, ("build",))
        self.assertEqual([item.pattern for item in refused], ["src/"])

    def test_the_derivation_reads_only_what_it_is_given(self) -> None:
        # Root ``.gitignore`` text only: a nested file's lines are the
        # caller's to withhold, and nothing is read from disk here.
        with mock.patch("builtins.open", side_effect=AssertionError("opened a file")):
            self.assertEqual(self.derive("a/\n")[0], ("a",))


class PromptTest(unittest.TestCase):
    def prompt(self, **kwargs) -> str:
        defaults = dict(clone_dir=Path("/sb/clone"), output_dir=Path("/sb/out"), head=HEAD, checklist_blob=BLOB,
                        writable=("build", "dist"), refused=(fr.RefusedPattern("*.pyc", "a glob"),),
                        timeout_seconds=3600)
        return fr.tester_prompt(FLOWS, **{**defaults, **kwargs})

    def test_without_limits_the_text_is_what_it_was_before_the_keyword(self) -> None:
        import hashlib
        text = fr.tester_prompt(
            [fr.Flow("flow-1", "A", "Run it.")], clone_dir=Path("/c"), output_dir=Path("/o"), head="h",
            checklist_blob="b", writable=("build",), refused=(fr.RefusedPattern("*.pyc", "a glob"),),
            timeout_seconds=60)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),
                         "126f26958ef022bd85f1eef1ef172eae8ca4a9106210ba508c2991527c933068")

    def test_given_limits_replace_the_sandbox_limits(self) -> None:
        text = self.prompt(limits=("only this limit",))
        self.assertIn("- only this limit", text)
        self.assertNotIn(fr.SANDBOX_LIMITS[0], text)

    def test_it_carries_the_flows_the_format_and_the_locations(self) -> None:
        text = self.prompt()
        for flow in FLOWS:
            self.assertIn(f"### {flow.flow_id} (checklist label {flow.label})", text)
            self.assertIn(flow.text, text)
        for needle in ("/sb/clone", "/sb/out", HEAD, BLOB, "RESULTS.json", "build/, dist/", "`*.pyc` (a glob)",
                       "3600 seconds", '"status": "passed" | "failed"', "`reason`"):
            self.assertIn(needle, text)

    def test_it_states_the_sandbox_limits_and_the_copy_rule(self) -> None:
        text = self.prompt()
        for limit in fr.SANDBOX_LIMITS:
            self.assertIn(limit, text)
        self.assertIn("cp -a", text)
        self.assertIn("No flow edits product source", text)
        self.assertIn("never `defect`", text)
        self.assertIn("never choose flow ids", text.lower())

    def test_none_is_said_when_nothing_is_writable_or_refused(self) -> None:
        text = self.prompt(writable=(), refused=())
        self.assertIn("Writable locations in the clone root, each a link to a folder outside the clone: none", text)
        self.assertIn("Refused as writable locations (so a flow that writes there is `environment`): none", text)

    def test_the_row_37_note_shares_the_limits(self) -> None:
        from controller import codex_bridge
        note = fr.sandbox_limits_note()
        self.assertEqual(fr.SHARED_SANDBOX_LIMITS, fr.SANDBOX_LIMITS[:5])
        for limit in fr.SHARED_SANDBOX_LIMITS:
            self.assertIn(limit, note)
            self.assertIn(limit, codex_bridge.SANDBOX_LIMITS)
        for limit in fr.SANDBOX_LIMITS[5:]:
            self.assertNotIn(limit, note)
        self.assertIn("offline", note)


class ConstantsTest(unittest.TestCase):
    def test_the_vocabulary(self) -> None:
        self.assertEqual((fr.ACTION_ID, fr.SATISFIED_BY, fr.ROW), ("functional.evidence.external",
                                                                    "functional_evidence", "38e"))
        self.assertEqual(fr.OUTCOMES, ("recorded", "findings", "environment_limited", "unchanged"))
        self.assertEqual(len(fr.FAULT_GATES), 8)
        self.assertEqual(fr.GATES, fr.FAULT_GATES + (fr.GATE_UNCHANGED,))
        self.assertEqual(len(set(fr.GATES)), 9)
        self.assertEqual(fr.ROUND_BOUND, 3)
        self.assertEqual(fr.JOB_KIND, "functional_test")

    def test_the_digest_helpers(self) -> None:
        self.assertEqual(fr.sha256_bytes(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        self.assertEqual(fr.git_blob_id(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")
        self.assertEqual(fr.git_blob_id(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")


if __name__ == "__main__":
    unittest.main()
