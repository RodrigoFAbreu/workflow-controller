"""``controller.release_notes`` (``workflow-controller-settings-and-
telemetry`` CP4, Design D): the section extraction, the block, the I8
checks (the per-paragraph trailer parse with no Git configuration, the
length and the line rules), the commit-message parser with its pairing and
anchoring, the range resolution, and ``tools/release.py notes-block``."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import gitrepo, milestone_branch as mb, release_notes as rn  # noqa: E402
from tests import fixtures  # noqa: E402
from tools import release as release_tool  # noqa: E402

WID = "demo-milestone"
NOTES = "The settings file arrives.\n\n- `settings show` prints it.\n- Telemetry v0 records each job."


def github_wrap(text: str, width: int = 72) -> str:
    """GitHub's wrap of a squash commit body, as measured (Investigation):
    every line over ``width`` characters broken greedily at spaces; every
    existing line break kept, no lines joined."""
    out = []
    for line in text.split("\n"):
        if len(line) <= width:
            out.append(line)
            continue
        current = ""
        for word in line.split(" "):
            if current and len(current) + 1 + len(word) > width:
                out.append(current)
                current = word
            else:
                current = f"{current} {word}" if current else word
        out.append(current)
    return "\n".join(out)


def readiness_body(notes: str = NOTES, wid: str = WID) -> str:
    return mb.squash_body(wid, f"docs/plans/{wid}.md", accepted="a" * 40, branch=f"milestone/{wid}",
                          notes_block=rn.render_block(wid, notes))


def squash_message(body: str, title: str = "feat: the milestone (#7)") -> bytes:
    """A squash commit's message as GitHub writes it: the title, the
    wrapped body, the rule and the co-author trailer."""
    return (f"{title}\n\n{github_wrap(body).rstrip(chr(10))}\n\n---------\n\n"
            f"Co-authored-by: Someone <someone@example.invalid>\n").encode()


def block_bytes(wid: str, notes: str, *, digest: str | None = None) -> str:
    marker = rn.START_MARKER.format(work_item_id=wid, digest=digest or rn.digest(notes))
    return f"{marker}\n{notes}\n{rn.END_MARKER}"


class SectionTest(unittest.TestCase):
    def test_the_section_runs_to_the_next_level_two_heading_trimmed(self) -> None:
        text = ("# Milestone\n\n## Goal\n\nx\n\n## Release notes\n\n\nFirst line.\n\n### Sub\n- item\n  \n\n"
                "## Next\nno\n")
        self.assertEqual(rn.extract_section(text, "Release notes"), "First line.\n\n### Sub\n- item")
        self.assertEqual(rn.extract_section("## Release notes\nonly\n", "Release notes"), "only")

    def test_absent_and_empty_sections(self) -> None:
        self.assertIsNone(rn.extract_section("## Release notes and more\nx\n", "Release notes"))
        self.assertIsNone(rn.extract_section("### Release notes\nx\n", "Release notes"))
        self.assertEqual(rn.extract_section("## Release notes\n\n   \n## Next\n", "Release notes"), "")

    def test_the_block_binds_the_work_item_and_the_digest(self) -> None:
        block = rn.render_block(WID, NOTES)
        first, *rest = block.split("\n")
        self.assertEqual(first, f"<!-- workflow-controller: release-notes work_item={WID} "
                                f"sha256={rn.digest(NOTES)} -->")
        self.assertEqual("\n".join(rest[:-1]), NOTES)
        self.assertEqual(rest[-1], "<!-- workflow-controller: release-notes end -->")
        self.assertEqual(len(rn.END_MARKER), 47)
        self.assertEqual(len(rn.MARKER_LINE_PREFIX), 39)


class LineRuleTest(unittest.TestCase):
    def assertRule(self, notes: str, fragment: str, line: int | None) -> None:
        problem = rn.notes_problem(notes)
        self.assertIsNotNone(problem, notes)
        self.assertIn(fragment, problem.rule)
        self.assertEqual(problem.line, line)

    def test_72_bytes_pass_and_73_do_not(self) -> None:
        self.assertIsNone(rn.notes_problem("x" * 72))
        self.assertRule("ok\n" + "x" * 73, "73 bytes", 2)

    def test_a_72_character_line_with_an_accent_is_over_72_bytes(self) -> None:
        line = "é" + "x" * 71
        self.assertEqual(len(line), 72)
        self.assertRule(line, "73 bytes", 1)

    def test_trailing_space_tab_carriage_return_and_marker_text(self) -> None:
        self.assertRule("a line \nnext", "ends in a space", 1)
        self.assertRule("a\tb", "tab", 1)
        self.assertRule("one\r\ntwo", "carriage return", 1)
        self.assertRule("see <!-- workflow-controller: x -->", "marker text", 1)

    def test_each_line_rule_names_its_own_remedy(self) -> None:
        """Functional review F3: wrapping fixes only the length rule."""
        remedies = {notes: rn.notes_problem(notes).remedy for notes in ("a line \nnext", "a\tb", "x" * 73)}
        self.assertEqual(remedies, {"a line \nnext": "remove the trailing space",
                                    "a\tb": "replace the tab with spaces",
                                    "x" * 73: "wrap the section at 72 columns or remove the text"})

    def test_notes_need_a_non_blank_line(self) -> None:
        for notes in ("", "\n", "  \n \n"):
            self.assertRule(notes, "no non-blank line", None)
        self.assertIsNone(rn.notes_problem(NOTES))


class TrailerRuleTest(unittest.TestCase):
    """The per-paragraph parse, measured with the real ``git``."""

    def problem(self, notes: str) -> rn.NotesProblem | None:
        return rn.paragraph_problem(readiness_body(notes))

    def test_a_trailer_paragraph_in_the_middle_is_refused_though_the_whole_body_parses_clean(self) -> None:
        notes = "Intro.\n\nFixes: the thing\nBreaking-Change: none\n\nOutro."
        body = readiness_body(notes)
        whole = subprocess.run(["git", "interpret-trailers", "--parse"], input=body, capture_output=True,
                               text=True, check=True).stdout
        self.assertEqual(whole, "")
        problem = self.problem(notes)
        self.assertIsNotNone(problem)
        self.assertEqual(problem.excerpt, "Fixes: the thing")
        self.assertIn("reword the paragraph or join it", problem.remedy)

    def test_a_bare_url_and_a_one_line_note_are_refused(self) -> None:
        for paragraph in ("https://example.com/x", "Note: this release changes the default."):
            with self.subTest(paragraph):
                self.assertIsNotNone(self.problem(f"Intro.\n\n{paragraph}\n\nOutro."))

    def test_ordinary_shapes_pass(self) -> None:
        for notes in ("A colon: inside prose is fine, as Git decides.",
                      "- a list\nFixes: the thing",
                      "Upgrade note: read the guide.",
                      "**Breaking:** none",
                      "| a | b |\n|---|---|\n| 1 | 2 |",
                      NOTES):
            with self.subTest(notes):
                self.assertIsNone(self.problem(notes))

    def test_the_markers_whole_or_reflowed_pass(self) -> None:
        body = github_wrap(readiness_body())
        self.assertIsNone(rn.paragraph_problem(body))

    def test_the_length_limit(self) -> None:
        problem = rn.paragraph_problem("x" * (rn.MAX_BODY_CHARS + 1), parse=lambda _: "")
        self.assertIn("65536", problem.rule)
        self.assertIsNone(rn.paragraph_problem("x" * rn.MAX_BODY_CHARS, parse=lambda _: ""))

    def test_no_git_configuration_changes_the_parse(self) -> None:
        """``trailer.separators = :=`` from a global file, the repository's
        configuration and ``GIT_CONFIG_COUNT`` each make ``see=thing`` a
        trailer for a plain ``git``; never for the check."""
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            home = root / "home"
            home.mkdir()
            (home / ".gitconfig").write_text("[trailer]\n\tseparators = :=\n")
            repo = root / "repo"
            fixtures.git_init(repo)
            subprocess.run(["git", "-C", str(repo), "config", "trailer.separators", ":="], check=True)
            settings = {
                "global file": ({"HOME": str(home), "XDG_CONFIG_HOME": str(home / "xdg")}, None),
                "repository": ({}, repo),
                "GIT_CONFIG_COUNT": ({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "trailer.separators",
                                      "GIT_CONFIG_VALUE_0": ":="}, None),
                "GIT_DIR": ({"GIT_DIR": str(repo / ".git")}, None),
            }
            base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            for name, (extra, cwd) in settings.items():
                with self.subTest(name):
                    env = {**base, **extra}
                    plain = subprocess.run(["git", "interpret-trailers", "--parse"], input="\nsee=thing\n",
                                           capture_output=True, text=True, env=env, cwd=cwd or root, check=True)
                    self.assertEqual(plain.stdout.strip(), "see: thing")
                    previous = os.getcwd()
                    os.chdir(cwd or root)
                    try:
                        with mock.patch.dict(os.environ, extra):
                            self.assertEqual(gitrepo.parse_trailers("see=thing"), "")
                            self.assertIsNone(rn.paragraph_problem(readiness_body("see=thing")))
                    finally:
                        os.chdir(previous)
        self.assertEqual(gitrepo.parse_trailers("see=thing"), "")


class ParseMessageTest(unittest.TestCase):
    def assertRoundTrip(self, body: str, notes: str = NOTES, wid: str = WID) -> None:
        parsed = rn.parse_message(squash_message(body))
        self.assertEqual(parsed.unattributable, ())
        self.assertEqual([(b.work_item_id, b.notes, b.digest, b.problem) for b in parsed.blocks],
                         [(wid, notes, rn.digest(notes), None)])

    def test_the_round_trip_as_written_and_as_github_wraps_it(self) -> None:
        body = readiness_body()
        self.assertRoundTrip(body)
        wrapped = github_wrap(body)
        self.assertNotEqual(wrapped, body)  # the start marker and the Controller's lines were wrapped
        self.assertTrue(any(line == "-->" or line.startswith("sha256=") for line in wrapped.split("\n")))
        self.assertRoundTrip(wrapped)
        resolution = rn.resolve([("c1", squash_message(body))])
        self.assertEqual(resolution.text, NOTES)

    def test_the_wrap_shapes_of_0de0fd5_and_e8cd8f9(self) -> None:
        """``PR_MARKER`` split before its ``-->`` and after its first token;
        a start marker split the same two ways."""
        start = rn.start_marker(WID, NOTES)
        head, _, digest_part = start.rpartition(" sha256=")
        for marker_lines in ([start[:-4], "-->"], [head, "sha256=" + digest_part],
                             [rn.MARKER_LINE_PREFIX, start[len(rn.MARKER_LINE_PREFIX) + 1:]]):
            with self.subTest(marker_lines):
                body = "\n".join([*marker_lines, NOTES, rn.END_MARKER, "", "Milestone x.",
                                  "<!-- workflow-controller:", f"work_item={WID} -->"])
                self.assertRoundTrip(body)

    def test_a_message_with_no_marker_line_contributes_nothing_in_any_encoding(self) -> None:
        self.assertEqual(rn.parse_message(b"docs: x\n\nplain \xff bytes\n"), rn.ParsedMessage())
        quoted = f"docs: x\n\nThe marker `{rn.start_marker(WID, NOTES)}` is quoted.\n".encode()
        self.assertEqual(rn.parse_message(quoted), rn.ParsedMessage())
        self.assertEqual(rn.parse_message(b"x\n\n <!-- workflow-controller: release-notes end -->\n"),
                         rn.ParsedMessage())

    def test_a_marker_line_in_a_non_utf8_message_raises(self) -> None:
        raw = squash_message(readiness_body()) + b"\xff\n"
        with self.assertRaises(rn.NotUtf8Error):
            rn.parse_message(raw)

    def test_pairing_a_damaged_start_consumes_its_end(self) -> None:
        bad = block_bytes(WID, NOTES, digest="F" * 64)
        parsed = rn.parse_message(f"x\n\n{bad}\n".encode())
        self.assertEqual(parsed.unattributable, ())
        (block,) = parsed.blocks
        self.assertTrue(block.damaged)
        self.assertIn("sha256=", block.problem)

    def test_a_start_with_no_end_before_the_next_start_is_damaged_and_the_next_parses(self) -> None:
        other = "other-item"
        text = f"x\n\n{rn.start_marker(WID, NOTES)}\n{NOTES}\n{block_bytes(other, 'Other notes.')}\n"
        parsed = rn.parse_message(text.encode())
        self.assertEqual([(b.work_item_id, b.damaged) for b in parsed.blocks], [(WID, True), (other, False)])
        self.assertEqual(parsed.blocks[1].notes, "Other notes.")

    def test_unattributable_markers(self) -> None:
        cases = {
            "orphan end": f"x\n\n{rn.END_MARKER}\n",
            "slash id": f"x\n\n{block_bytes('a/b', NOTES)}\n",
            "dotdot id": f"x\n\n{block_bytes('..', NOTES)}\n",
            "no id": f"x\n\n{rn.MARKER_LINE_PREFIX} sha256={'a' * 64} -->\n{NOTES}\n{rn.END_MARKER}\n",
            "garbage": f"x\n\n{rn.MARKER_LINE_PREFIX}-ish -->\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                parsed = rn.parse_message(text.encode())
                self.assertEqual(parsed.blocks, ())
                self.assertEqual(len(parsed.unattributable), 1)

    def test_empty_notes_and_two_blocks_for_one_item_are_damaged(self) -> None:
        empty = block_bytes(WID, "")
        self.assertIn("empty", rn.parse_message(f"x\n\n{empty}\n".encode()).blocks[0].problem)
        blank = block_bytes(WID, "  ")
        self.assertTrue(rn.parse_message(f"x\n\n{blank}\n".encode()).blocks[0].damaged)
        two = f"x\n\n{block_bytes(WID, 'One.')}\n\n{block_bytes(WID, 'Two.')}\n"
        (block,) = rn.parse_message(two.encode()).blocks
        self.assertIn("more than one block", block.problem)

    def test_several_items_in_one_message_keep_message_order(self) -> None:
        text = f"x\n\n{block_bytes('b-item', 'B.')}\n\n{block_bytes('a-item', 'A.')}\n"
        self.assertEqual([b.work_item_id for b in rn.parse_message(text.encode()).blocks], ["b-item", "a-item"])


class ResolveTest(unittest.TestCase):
    def message(self, *blocks: str) -> bytes:
        return ("docs: notes\n\n" + "\n\n".join(blocks) + "\n").encode()

    def refusal(self, messages) -> rn.Refusal:
        with self.assertRaises(rn.NotesRefused) as caught:
            rn.resolve(messages)
        return caught.exception.refusal

    def test_missing(self) -> None:
        refusal = self.refusal([("c1", b"feat: x\n"), ("c2", b"docs: y\n\nbody\n")])
        self.assertEqual((refusal.outcome, refusal.supersedable), (rn.MISSING, True))
        self.assertEqual(self.refusal([]).outcome, rn.MISSING)

    def test_the_newest_block_supersedes_and_order_is_by_commit(self) -> None:
        resolution = rn.resolve([
            ("c1", self.message(block_bytes("b-item", "Old B."))),
            ("c2", self.message(block_bytes("a-item", "A."))),
            ("c3", self.message(block_bytes("c-item", "C."), block_bytes("b-item", "New B."))),
        ])
        self.assertEqual([(u.commit, u.block.work_item_id) for u in resolution.used],
                         [("c2", "a-item"), ("c3", "c-item"), ("c3", "b-item")])
        self.assertEqual(resolution.superseded, (("c1", "b-item"),))
        self.assertEqual(resolution.text, "### a-item\n\nA.\n\n### c-item\n\nC.\n\n### b-item\n\nNew B.")
        self.assertIn("superseded b-item (c1)", resolution.summary())

    def test_a_digest_mismatch_and_a_damaged_block_refuse_and_are_supersedable(self) -> None:
        edited = block_bytes(WID, NOTES).replace("arrives", "arrived")
        refusal = self.refusal([("c1", self.message(edited))])
        self.assertEqual((refusal.outcome, refusal.supersedable, refusal.work_items), (rn.UNVERIFIED, True, (WID,)))
        self.assertIn(rn.digest(NOTES), refusal.problems[0])
        fixed = rn.resolve([("c1", self.message(edited)), ("c2", self.message(block_bytes(WID, NOTES)))])
        self.assertEqual(fixed.text, NOTES)
        empty = block_bytes(WID, "")
        self.assertEqual(self.refusal([("c1", self.message(empty))]).outcome, rn.UNVERIFIED)
        self.assertEqual(rn.resolve([("c1", self.message(empty)), ("c2", self.message(block_bytes(WID, "N.")))]).text,
                         "N.")

    def test_unattributable_and_non_utf8_are_not_supersedable(self) -> None:
        orphan = self.refusal([("c1", self.message(rn.END_MARKER)),
                               ("c2", self.message(block_bytes(WID, NOTES)))])
        self.assertEqual((orphan.outcome, orphan.supersedable), (rn.UNVERIFIED, False))
        bad = self.message(block_bytes(WID, NOTES)) + b"\xff"
        refusal = self.refusal([("c1", bad), ("c2", self.message(block_bytes(WID, NOTES)))])
        self.assertEqual((refusal.outcome, refusal.supersedable), (rn.UNREADABLE, False))


class NotesBlockToolTest(unittest.TestCase):
    def run_tool(self, text: bytes | None, work_item: str = WID) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "notes.md"
            if text is not None:
                path.write_bytes(text)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = release_tool.main(["notes-block", "--work-item", work_item, str(path)])
        return code, out.getvalue(), err.getvalue()

    def test_it_prints_a_block_the_parser_reads_back(self) -> None:
        code, out, _ = self.run_tool(f"\n\n{NOTES}\n\n".encode())
        self.assertEqual(code, 0)
        self.assertEqual(out, rn.render_block(WID, NOTES) + "\n")
        self.assertEqual(rn.resolve([("c", f"docs: notes\n\n{out}".encode())]).text, NOTES)

    def test_it_refuses_failed_checks_and_empty_files(self) -> None:
        cases = {
            "empty": (b"", "holds no notes"),
            "blank lines": (b"\n  \n\n", "holds no notes"),
            "long line": (b"x" * 73, "bytes of UTF-8"),
            "trailer": (b"Intro.\n\nNote: this changes.\n\nOutro.\n", "trailer"),
            "not utf-8": (b"\xff", "not valid UTF-8"),
            "missing": (None, "cannot read"),
        }
        for name, (text, fragment) in cases.items():
            with self.subTest(name):
                code, out, err = self.run_tool(text)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("refused", err)
                self.assertIn(fragment, err)
        code, _, err = self.run_tool(NOTES.encode(), work_item="Bad/Id")
        self.assertEqual(code, 1)
        self.assertIn("work-item id", err)


if __name__ == "__main__":
    unittest.main()
