"""The document-consistency property (CP9,
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, CP9's own "The
document-consistency property"). Five consecutive local plan-review
rounds (10-14) found stale checkpoint complexities, a stale exit-code
table and stale round counts in the plan's own prose -- every one
verifiable by a single mechanical comparison, and none caught, because
stating the discipline was not the same as running it.

Scoped to what is mechanically checkable, per the plan's own declared
criterion: a property that reads a **closed external artifact** with a
membership test, never a property that grades 3300 lines of prose by
description. Four halves, each compared against its own authority:

1. **Checkpoint complexities** stated in prose (the literal ``complexity
   N`` form) against the registry JSON. This suite's own extraction is
   deliberately narrower than the plan's own fullest specification of
   this half (which also treats a bare, unlabelled ``**N**`` as a
   free-standing figure in its own right): a bare bold decimal is used
   here *only* to detect a sentence's ``**N** today`` marking -- the
   convention this document uses to mark a superseded prose figure next
   to its current replacement -- and never compared against the registry
   on its own. Treating every bare bold decimal in a 3300-line document
   as a checkpoint-complexity claim is exactly the shape that produces
   false positives against unrelated bold numbers (line counts, revision
   numbers, round counts) that merely happen to share a sentence with a
   ``CPn`` token; this suite trades a small amount of the fullest
   specification's own coverage for zero false positives, and states the
   trade rather than silently narrowing it.
2. **The exit-codes table** against the ADR's own copy
   (``docs/adr/0001-controller-generation-1-architecture.md``).
3. **Round counts** (``executed/ran/has run it N times/rounds``) against
   the registry's own ``plan_revision - 1``.
4. **Controller invocation lines** (code spans or fenced lines beginning
   ``workflow-controller `` or ``python <flags> -m controller ``, naming
   one of the six CLI commands, carrying no ``[``/``]``) against
   ``controller.cli.build_parser()`` -- the strongest authority of the
   four, since it is the parser itself.

Every half's own live-document instance is asserted first (this document
must currently be green against its own claims), then each half's own
negative/positive instantiation pins the polarity a property that "cannot
fail" would hide.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import cli  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = REPO_ROOT / "docs" / "ai-workflow" / "CONTROLLER_GEN1_PLAN.md"
REGISTRY_PATH = (
    REPO_ROOT / "docs" / "ai-workflow" / "registry"
    / "workflow-controller-generation-1-registry.json"
)
ADR_PATH = REPO_ROOT / "docs" / "adr" / "0001-controller-generation-1-architecture.md"


# ---------------------------------------------------------------------------
# Half 1: checkpoint complexities.
# ---------------------------------------------------------------------------

_COMPLEXITY_RE = re.compile(r"complexity\s+\*{0,2}(\d+)\*{0,2}")
_CURRENT_MARK_RE = re.compile(r"\*\*(\d+)\*\*\s+today")
_CP_TOKEN_RE = re.compile(r"CP\d+B?")


def _blocks(text: str) -> list[str]:
    """A blank line ends a block; a line whose text begins ``|`` is a
    block of its own (a table row is never merged with its neighbours);
    every other block is a maximal run of consecutive non-blank,
    non-table lines."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if line.lstrip().startswith("|"):
            if current:
                blocks.append("\n".join(current))
                current = []
            blocks.append(line)
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _sentences(block_text: str) -> list[str]:
    """A maximal span of the block's own whitespace-normalised text,
    beginning at the start of the block or immediately after a full stop
    and a space."""
    norm = re.sub(r"\s+", " ", block_text).strip()
    segments = []
    start = 0
    for m in re.finditer(r"\. ", norm):
        segments.append(norm[start:m.end()])
        start = m.end()
    tail = norm[start:]
    if tail:
        segments.append(tail)
    return segments


def _attribute_checkpoint(pos: int, cp_tokens: list[tuple[int, int, str]]) -> str | None:
    """The checkpoint named by the nearest ``CPn``/``CPnB`` token
    preceding ``pos`` in the sentence, or, when none precedes it, the
    nearest one following it. ``None`` when the sentence names no
    checkpoint at all."""
    preceding = [t for t in cp_tokens if t[1] <= pos]
    if preceding:
        return max(preceding, key=lambda t: t[1])[2]
    following = [t for t in cp_tokens if t[0] >= pos]
    if following:
        return min(following, key=lambda t: t[0])[2]
    return None


def complexity_violations(plan_text: str, registry: dict) -> list[str]:
    registry_complexities = {cp["id"]: cp["complexity"] for cp in registry["checkpoints"]}
    violations: list[str] = []
    for block in _blocks(plan_text):
        for sentence in _sentences(block):
            cp_tokens = [(m.start(), m.end(), m.group(0)) for m in _CP_TOKEN_RE.finditer(sentence)]
            current_marks = []
            for m in _CURRENT_MARK_RE.finditer(sentence):
                cp = _attribute_checkpoint(m.start(), cp_tokens)
                if cp is not None:
                    current_marks.append((cp, int(m.group(1))))

            for m in _COMPLEXITY_RE.finditer(sentence):
                value = int(m.group(1))
                cp = _attribute_checkpoint(m.start(), cp_tokens)
                if cp is None:
                    continue  # out of scope: names no checkpoint
                superseded = any(
                    marked_cp == cp and marked_value != value
                    for marked_cp, marked_value in current_marks
                )
                if superseded:
                    continue  # excluded: superseded half of a marked pair
                registry_value = registry_complexities.get(cp)
                if registry_value is None:
                    violations.append(
                        f"prose states complexity {value} for {cp!r}, which is absent from "
                        f"the registry -- sentence: {sentence[:200]!r}"
                    )
                elif registry_value != value:
                    violations.append(
                        f"{cp}: prose states complexity {value}, registry says {registry_value} "
                        f"-- sentence: {sentence[:200]!r}"
                    )
    return violations


# ---------------------------------------------------------------------------
# Half 2: the Exit codes table, against the ADR's own copy.
# ---------------------------------------------------------------------------


def _find_heading_table(text: str, heading: str) -> list[str]:
    """The Markdown table following the first heading line whose text
    after its leading ``#`` run is exactly ``heading``; the table is the
    maximal run of consecutive lines beginning ``|`` starting after that
    heading, with its first two lines (header, delimiter) dropped."""
    lines = text.split("\n")
    heading_idx = None
    for i, line in enumerate(lines):
        m = re.match(r"^#+\s+(.*)$", line.strip())
        if m and m.group(1).strip() == heading:
            heading_idx = i
            break
    if heading_idx is None:
        raise AssertionError(f"no heading exactly {heading!r} found")
    j = heading_idx + 1
    while j < len(lines) and not lines[j].lstrip().startswith("|"):
        j += 1
    table_lines = []
    while j < len(lines) and lines[j].lstrip().startswith("|"):
        table_lines.append(lines[j])
        j += 1
    if len(table_lines) < 3:
        raise AssertionError(f"'{heading}' table has fewer than one data row")
    return table_lines[2:]


def _parse_two_cell_rows(table_lines: list[str]) -> list[tuple[int, str]]:
    rows = []
    for line in table_lines:
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if len(cells) != 2:
            raise AssertionError(f"exit-code row does not have exactly two cells: {line!r}")
        try:
            code = int(cells[0])
        except ValueError as exc:
            raise AssertionError(f"exit-code row's first cell is not an integer: {line!r}") from exc
        rows.append((code, cells[1]))
    return rows


def _normalize_meaning(text: str) -> str:
    t = re.sub(r"[`*]+", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def exit_code_violations(plan_text: str, adr_text: str) -> list[str]:
    plan_rows = {(c, _normalize_meaning(m)) for c, m in _parse_two_cell_rows(
        _find_heading_table(plan_text, "Exit codes"))}
    adr_rows = {(c, _normalize_meaning(m)) for c, m in _parse_two_cell_rows(
        _find_heading_table(adr_text, "Exit codes"))}
    violations = []
    for row in sorted(plan_rows - adr_rows):
        violations.append(f"plan states exit-code row {row!r}, absent (or differing) in the ADR")
    for row in sorted(adr_rows - plan_rows):
        violations.append(f"ADR states exit-code row {row!r}, absent (or differing) in the plan")
    return violations


# ---------------------------------------------------------------------------
# Half 3: round counts, against the registry's own `plan_revision - 1`.
# ---------------------------------------------------------------------------

_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}


def _word_to_number(word: str) -> int | None:
    if word in _ONES:
        return _ONES[word]
    if word in _TENS:
        return _TENS[word]
    if "-" in word:
        head, _, tail = word.partition("-")
        if head in _TENS and tail in _ONES:
            return _TENS[head] + _ONES[tail]
    return None


_ROUND_COUNT_RE = re.compile(r"(?:executed|ran|has run)(?: it)? ([a-z-]+) (?:times|rounds)")


def round_count_violations(plan_text: str, expected: int) -> list[str]:
    norm = re.sub(r"\s+", " ", plan_text)
    violations = []
    for m in _ROUND_COUNT_RE.finditer(norm):
        word = m.group(1)
        value = _word_to_number(word)
        if value is None:
            violations.append(f"round-count occurrence {m.group(0)!r} does not parse as a number word")
            continue
        if value != expected:
            violations.append(
                f"round-count occurrence {m.group(0)!r} states {value}, expected {expected} "
                f"(registry plan_revision - 1)"
            )
    return violations


# ---------------------------------------------------------------------------
# Half 4: Controller invocation lines, against `controller.cli`'s own
# parser.
# ---------------------------------------------------------------------------

_COMMAND_NAMES = frozenset({"inspect", "explain", "step", "run", "resume", "status"})
_INVOCATION_PREFIX_RE = re.compile(r"^(workflow-controller |python(?:\s+\S+)*?\s+-m\s+controller\s)")


def _paragraphs(text: str) -> list[str]:
    return re.split(r"\n\s*\n", text)


def extract_invocation_lines(text: str) -> list[str]:
    """Every code span whose text is a **Controller invocation line**:
    it begins ``workflow-controller `` or ``python <flags> -m
    controller ``; its tokens after that prefix contain, as a whole
    token, one of the six command names; and it contains neither ``[``
    nor ``]``. Backtick pairing is scoped to the paragraph (a blank-line
    -delimited span of the raw text), never to the whole document, so an
    unmatched backtick elsewhere cannot pair across a blank line and
    corrupt extraction -- this document carries exactly one paragraph
    with an odd backtick count (the command-line half's own declaring
    row, which discusses backticks by name), and paragraph scoping is
    what keeps that occurrence from misaligning every span after it."""
    lines: list[str] = []
    for paragraph in _paragraphs(text):
        norm = re.sub(r"\s+", " ", paragraph).strip()
        for span in re.findall(r"`([^`]+)`", norm):
            span = span.strip()
            m = _INVOCATION_PREFIX_RE.match(span)
            if not m:
                continue
            rest_tokens = span[m.end():].split()
            if not any(t in _COMMAND_NAMES for t in rest_tokens):
                continue
            if "[" in span or "]" in span:
                continue
            lines.append(span)
    return lines


def _argv_for(invocation_line: str) -> list[str]:
    tokens = invocation_line.split()
    if tokens[0] == "workflow-controller":
        return tokens[1:]
    idx = tokens.index("controller")
    return tokens[idx + 1:]


def command_line_violations(text: str, parser) -> list[str]:
    violations = []
    for line in extract_invocation_lines(text):
        argv = _argv_for(line)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                parser.parse_args(argv)
        except SystemExit as exc:
            violations.append(
                f"invocation line {line!r} (argv {argv!r}) failed to parse under "
                f"controller.cli.build_parser() -- exit {exc.code}: {buf.getvalue().strip()}"
            )
    return violations


# ---------------------------------------------------------------------------
# Live-document tests.
# ---------------------------------------------------------------------------


class LiveDocumentTest(unittest.TestCase):
    """The property run over the published plan, registry and ADR, in
    place -- must be green, since a property that reports its own
    document red on arrival is not one this checkpoint's gate can pass."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_text = PLAN_PATH.read_text()
        cls.registry = json.loads(REGISTRY_PATH.read_text())
        cls.adr_text = ADR_PATH.read_text()
        cls.parser = cli.build_parser()

    def test_complexities_agree_with_the_registry(self) -> None:
        violations = complexity_violations(self.plan_text, self.registry)
        self.assertEqual(violations, [])

    def test_exit_code_table_agrees_with_the_adr(self) -> None:
        violations = exit_code_violations(self.plan_text, self.adr_text)
        self.assertEqual(violations, [])

    def test_round_counts_agree_with_plan_revision_minus_one(self) -> None:
        expected = self.registry["plan_revision"] - 1
        violations = round_count_violations(self.plan_text, expected)
        self.assertEqual(violations, [])

    def test_every_command_line_parses_under_the_live_parser(self) -> None:
        violations = command_line_violations(self.plan_text, self.parser)
        self.assertEqual(violations, [])

    def test_at_least_one_invocation_line_is_actually_recognised(self) -> None:
        """A property that recognises nothing passes vacuously -- this
        pins that the recogniser actually fires on the live document
        (seven lines, per the plan's own measured count: CP8 step 2, CP9
        step 4, and five of CP9's own surface-table rows)."""
        lines = extract_invocation_lines(self.plan_text)
        self.assertEqual(len(lines), 7)


# ---------------------------------------------------------------------------
# Negative/positive instantiation -- a property that cannot fail is not one.
# ---------------------------------------------------------------------------


class ComplexityHalfInstantiationTest(unittest.TestCase):
    _REGISTRY = {"checkpoints": [{"id": "CP1", "complexity": 4}, {"id": "CP2", "complexity": 3}]}

    def test_deliberately_wrong_complexity_fails(self) -> None:
        text = "CP1 is at complexity 99 in this fixture sentence."
        violations = complexity_violations(text, self._REGISTRY)
        self.assertTrue(violations, "a wrong complexity must fail the half")

    def test_superseded_half_of_a_marked_pair_does_not_fail(self) -> None:
        # CP1's own current value (4) is marked "today"; the stale
        # `complexity 99` figure in the same sentence must not fail, since
        # it is the superseded half of a marked pair.
        text = "CP1 was at complexity 99 (CP1 is **4** today; 99 when first declared)."
        violations = complexity_violations(text, self._REGISTRY)
        self.assertEqual(violations, [])

    def test_two_checkpoints_one_marked_one_stale_unmarked_fails(self) -> None:
        # CP1's figure is marked current (and correct); CP2's is stale
        # and carries no marking at all -- the construct only excludes a
        # marked pair, so CP2's own stale figure must still fail.
        text = "CP1 is **4** today and CP2 is at complexity 99 in the same sentence."
        violations = complexity_violations(text, self._REGISTRY)
        self.assertTrue(
            any("CP2" in v for v in violations),
            "an unmarked stale figure sharing a sentence with a marked pair must still fail",
        )

    def test_occurrence_naming_no_checkpoint_is_out_of_scope(self) -> None:
        text = "Somewhere in this document, complexity 99 is mentioned with no checkpoint named."
        violations = complexity_violations(text, self._REGISTRY)
        self.assertEqual(violations, [])


class ExitCodeHalfInstantiationTest(unittest.TestCase):
    _ADR_TEXT = (
        "## Exit codes\n\n"
        "| Code | Meaning |\n"
        "|---|---|\n"
        "| 0 | ok |\n"
        "| 10 | gate |\n"
    )

    def test_a_row_present_in_only_one_document_fails(self) -> None:
        plan_text = (
            "### Exit codes\n\n"
            "| Code | Meaning |\n"
            "|---|---|\n"
            "| 0 | ok |\n"
            "| 10 | gate, but stated differently here |\n"
        )
        violations = exit_code_violations(plan_text, self._ADR_TEXT)
        self.assertTrue(violations)

    def test_emphasis_only_differences_do_not_fail(self) -> None:
        plan_text = (
            "### Exit codes\n\n"
            "| Code | Meaning |\n"
            "|---|---|\n"
            "| 0 | **ok** |\n"
            "| 10 | `gate` |\n"
        )
        violations = exit_code_violations(plan_text, self._ADR_TEXT)
        self.assertEqual(violations, [])


class RoundCountHalfInstantiationTest(unittest.TestCase):
    def test_stale_wrapped_round_count_fails(self) -> None:
        text = "This work item has run\nit sixty rounds without converging."
        violations = round_count_violations(text, expected=61)
        self.assertTrue(violations, "a stale wrapped round count must still be found and fail")

    def test_matching_round_count_does_not_fail(self) -> None:
        text = "This process executed forty-two times before it converged."
        violations = round_count_violations(text, expected=42)
        self.assertEqual(violations, [])

    def test_worker_exit_code_prose_is_not_mistaken_for_a_round_count(self) -> None:
        text = "The worker classification states exit 1 -> FAILURE, unrelated to any round count."
        violations = round_count_violations(text, expected=61)
        self.assertEqual(violations, [])


class CommandLineHalfInstantiationTest(unittest.TestCase):
    """The exact pair `OPUS-R34-B2` pins: a global option placed after
    the subcommand must fail; placed before it, must not."""

    def setUp(self) -> None:
        self.parser = cli.build_parser()

    def test_global_option_after_subcommand_fails(self) -> None:
        text = "the required run is `workflow-controller step <repo> --permission-mode X`."
        violations = command_line_violations(text, self.parser)
        self.assertTrue(violations)

    def test_global_option_before_subcommand_does_not_fail(self) -> None:
        text = "the required run is `workflow-controller --permission-mode X step <repo>`."
        violations = command_line_violations(text, self.parser)
        self.assertEqual(violations, [])

    def test_usage_synopsis_with_brackets_is_excluded_by_clause_iii(self) -> None:
        text = "the CLI table states `workflow-controller run <repo> [--max-steps N]`."
        violations = command_line_violations(text, self.parser)
        self.assertEqual(violations, [])
        self.assertEqual(extract_invocation_lines(text), [])

    def test_bare_route_mention_naming_no_command_is_excluded_by_clause_ii(self) -> None:
        text = "the re-exec route is `python -m controller`, named here with no subcommand."
        self.assertEqual(extract_invocation_lines(text), [])


if __name__ == "__main__":
    unittest.main()
