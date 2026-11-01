"""Tests for ``controller.command_files`` (workflow-controller-codex-command-render CP1).

Fixture command files are written to a temporary directory; the real
``.claude/commands/`` directory of this repository is rendered too. Run with
``TMPDIR`` set to a writable scratch area.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import command_files, decision, harness, worker, worker_launch
from tests import test_worker
from controller.errors import CommandRenderError, MissingCommandsDirectoryError, UserOnlyCommandError

REPO = Path(__file__).resolve().parent.parent
REAL_COMMANDS = REPO / ".claude" / "commands"
CONTRACT = REPO / "docs" / "ai-workflow" / "COMMAND_FILES.md"

#: Pinned on purpose (plan D4a): a Workflow release that changes either
#: recogniser's verdict on a file must fail here, not change what Codex may run.
ELEVEN = frozenset({
    "accept-milestone", "adopt-gate-policy", "approve-review", "archive-finished-item",
    "prune-archives", "release-checkpoint-start", "request-plan-amendment",
    "resume-implementation", "retire-legacy-work-item",
    "recover-implementation-provenance", "satisfy-gate",
})

PLAIN = (
    "---\ndescription: Plain.\nargument-hint: \"[id]\"\nstate_writer: false\n"
    "review-subject: none\n---\nBody $ARGUMENTS and again $ARGUMENTS.\n"
)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def put(self, name: str, text: str) -> None:
        (self.dir / f"{name}.md").write_text(text, encoding="utf-8")

    def render(self, name: str, arguments: str = ""):
        return command_files.render_command(name, arguments, self.dir)

    def cause(self, name: str, arguments: str = "") -> str:
        with self.assertRaises(CommandRenderError) as ctx:
            self.render(name, arguments)
        return ctx.exception.evidence["cause"]


class SubstitutionTest(Fixture):
    def test_body_unchanged_apart_from_the_substitution(self) -> None:
        self.put("c", PLAIN)
        rendered = self.render("c", "7")
        self.assertEqual(rendered.prompt, "Body 7 and again 7.\n")
        self.assertEqual(rendered.name, "c")
        self.assertEqual(rendered.description, "Plain.")
        self.assertEqual(rendered.argument_hint, "[id]")

    def test_arguments_are_verbatim_and_not_substituted_again(self) -> None:
        self.put("c", PLAIN)
        arguments = "a  b \"q\" 'r'\nline2 $ARGUMENTS"
        self.assertEqual(self.render("c", arguments).prompt,
                         f"Body {arguments} and again {arguments}.\n")

    def test_empty_arguments_substitute_the_empty_string(self) -> None:
        self.put("c", PLAIN)
        self.assertEqual(self.render("c").prompt, "Body  and again .\n")

    def test_a_body_without_the_token_is_unchanged_for_empty_arguments(self) -> None:
        self.put("c", "---\nstate_writer: true\n---\nNo token here.\n")
        self.assertEqual(self.render("c").prompt, "No token here.\n")

    def test_non_empty_arguments_for_a_hintless_command_are_refused(self) -> None:
        self.put("c", "---\nstate_writer: true\n---\nBody $ARGUMENTS\n")
        self.assertEqual(self.cause("c", "x"), "arguments_not_accepted")

    def test_non_empty_arguments_for_a_body_without_the_token_are_refused(self) -> None:
        self.put("c", "---\nargument-hint: \"[x]\"\nstate_writer: true\n---\nNo token.\n")
        self.assertEqual(self.cause("c", "x"), "arguments_not_accepted")

    def test_frontmatter_arguments_token_is_left_alone(self) -> None:
        self.put("c", "---\ndescription: keep $ARGUMENTS\nargument-hint: \"[x]\"\nstate_writer: true\n---\n$ARGUMENTS\n")
        rendered = self.render("c", "v")
        self.assertEqual(rendered.prompt, "v\n")
        self.assertEqual(rendered.description, "keep $ARGUMENTS")


class FrontmatterTest(Fixture):
    def test_unknown_key_is_ignored(self) -> None:
        self.put("c", "---\nfuture-key: whatever\nstate_writer: true\n---\nB\n")
        self.assertEqual(self.render("c").prompt, "B\n")

    def test_state_writer_and_review_subject_are_carried(self) -> None:
        self.put("c", "---\nstate_writer: true\nreview-subject: verdict\n---\nB\n")
        rendered = self.render("c")
        self.assertIs(rendered.state_writer, True)
        self.assertEqual(rendered.review_subject, "verdict")
        self.put("d", "---\nstate_writer: 'false'\n---\nB\n")
        self.assertIs(self.render("d").state_writer, False)
        self.assertIsNone(self.render("d").review_subject)

    def test_each_malformed_case_is_refused_with_the_line_named(self) -> None:
        cases = {
            "no_open": ("state_writer: true\n---\nB\n", 1),
            "no_close": ("---\nstate_writer: true\nB\n", 3),
            "no_colon": ("---\nstate_writer: true\nbroken\n---\nB\n", 3),
            "dup": ("---\nstate_writer: true\nstate_writer: false\n---\nB\n", 3),
            "bad_key": ("---\nstate_writer: true\nbad key!: 1\n---\nB\n", 3),
            "no_state_writer": ("---\ndescription: x\n---\nB\n", 3),
            "bad_state_writer": ("---\nstate_writer: maybe\n---\nB\n", 2),
            "bad_flag": ("---\nstate_writer: true\ndisable-model-invocation: yes\n---\nB\n", 3),
            "bad_subject": ("---\nstate_writer: true\nreview-subject: other\n---\nB\n", 3),
            "empty": ("", 1),
        }
        for label, (text, line) in cases.items():
            with self.subTest(label):
                self.put("c", text)
                with self.assertRaises(CommandRenderError) as ctx:
                    self.render("c")
                self.assertEqual(ctx.exception.evidence["cause"], "malformed_frontmatter")
                self.assertIn("c.md", ctx.exception.evidence["file"])
                self.assertEqual(ctx.exception.evidence["line"], line)
                self.assertIn(f"line {line}", str(ctx.exception))

    def test_crlf_body_is_preserved_exactly(self) -> None:
        raw = "---\r\nstate_writer: true\r\nargument-hint: x\r\n---\r\nFirst\r\nSecond $ARGUMENTS\r\n"
        (self.dir / "c.md").write_bytes(raw.encode("utf-8"))
        self.assertEqual(self.render("c").prompt, "First\r\nSecond \r\n")
        self.assertEqual(self.render("c", "A").prompt, "First\r\nSecond A\r\n")

    def test_disable_model_invocation_false_is_not_refused(self) -> None:
        for spelling in ("false", '"false"'):
            with self.subTest(spelling):
                self.put("c", f"---\nstate_writer: true\ndisable-model-invocation: {spelling}\n---\nB\n")
                self.assertEqual(self.render("c").prompt, "B\n")


class UserOnlyTest(Fixture):
    def test_flag_guard_and_both_are_refused(self) -> None:
        self.put("flag", "---\nstate_writer: false\ndisable-model-invocation: true\n---\nB\n")
        self.put("guard", "---\nstate_writer: false\n---\nCall workflow_state.validate_user_confirmation(x).\n")
        self.put("both-malformed", "disable-model-invocation: true\nworkflow_state.validate_user_confirmation\n")
        for name in ("flag", "guard", "both-malformed"):
            with self.subTest(name), self.assertRaises(UserOnlyCommandError):
                self.render(name)

    def test_the_flag_in_any_quoting_is_refused(self) -> None:
        for spelling in ("true", '"true"', "'true'"):
            with self.subTest(spelling):
                self.put("q", f"---\nstate_writer: false\ndisable-model-invocation: {spelling}\n---\nNo guard text.\n")
                with self.assertRaises(UserOnlyCommandError):
                    self.render("q")

    def test_a_quoted_flag_on_a_copy_of_prune_archives_is_refused(self) -> None:
        text = (REAL_COMMANDS / "prune-archives.md").read_text(encoding="utf-8")
        lines = text.split("\n")
        lines = ['disable-model-invocation: "true"' if ln == "disable-model-invocation: true" else ln for ln in lines]
        copy = "\n".join(lines)
        self.assertNotEqual(copy, text)
        self.assertFalse(decision.carries_disable_model_invocation_flag(copy))
        if decision.carries_user_confirmation_guard(copy):
            # Strip the guard text so neither recogniser claims the copy.
            copy = "\n".join(ln for ln in copy.split("\n") if "workflow_state." not in ln or "confirmation" not in ln)
        self.put("prune-copy", copy)
        with self.assertRaises(UserOnlyCommandError):
            self.render("prune-copy")

    def test_arguments_naming_any_user_only_command_are_refused(self) -> None:
        self.put("ok", PLAIN)
        self.put("prune-archives", "---\nstate_writer: false\ndisable-model-invocation: true\n---\nB\n")
        self.put("adopt-gate-policy", "---\nstate_writer: false\ndisable-model-invocation: true\n---\nB\n")
        for argument in ("/approve-review plan", "run `/prune-archives`.", "then /adopt-gate-policy,",
                         "adopt-gate-policy", "x\n/accept-milestone"):
            with self.subTest(argument), self.assertRaises(UserOnlyCommandError):
                self.render("ok", argument)

    def test_arguments_naming_an_allowed_command_pass(self) -> None:
        self.put("ok", PLAIN)
        self.put("milestone-plan", "---\nstate_writer: true\n---\nB\n")
        self.assertIn("/milestone-plan", self.render("ok", "/milestone-plan item").prompt)

    def test_a_malformed_sibling_is_treated_as_user_only(self) -> None:
        self.put("ok", PLAIN)
        self.put("odd", "not frontmatter at all\n")
        with self.assertRaises(UserOnlyCommandError):
            self.render("ok", "/odd")

    def test_the_launch_scan_is_exercised_for_every_literal_name(self) -> None:
        self.put("ok", PLAIN)
        for name in worker.USER_ONLY_COMMANDS:
            with self.subTest(name), self.assertRaises(UserOnlyCommandError):
                self.render("ok", f"/{name}")
            with self.assertRaises(UserOnlyCommandError):
                worker._assert_not_user_only(f"/ok /{name}")


class UserOnlySpellingTest(Fixture):
    """TK1 (#247): the renderer finds every user-only name of the real command
    directory in every spelling, and keeps a longer word or path unrefused."""

    def names(self) -> frozenset[str]:
        """The names derived from a copy of the real command files, so the
        renderer's own scan sees all eleven (the fixture directory alone
        derives only the launch scan's four)."""
        for path in REAL_COMMANDS.glob("*.md"):
            (self.dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        names = command_files._user_only_names(self.dir)
        self.assertEqual(names, ELEVEN)
        return names

    def test_every_derived_name_is_refused_in_every_spelling(self) -> None:
        names = self.names()
        self.put("ok", PLAIN)
        for name in sorted(names):
            for spelling in test_worker.REFUSED_SPELLINGS:
                arguments = spelling.format(n=name)
                with self.subTest(arguments=arguments), self.assertRaises(UserOnlyCommandError):
                    self.render("ok", arguments)

    def test_a_spelling_only_the_renderer_scan_catches(self) -> None:
        """`satisfy-gate` is not one of the launch scan's four names."""
        self.names()
        self.put("ok", PLAIN)
        self.assertNotIn("satisfy-gate", worker.USER_ONLY_COMMANDS)
        for spelling in ("/{n}!", "(/{n})", "--x=/{n}", "\u201c/{n}\u201d"):
            arguments = spelling.format(n="satisfy-gate")
            with self.subTest(arguments=arguments):
                worker._assert_not_user_only(arguments)
                with self.assertRaises(UserOnlyCommandError):
                    self.render("ok", arguments)

    def test_a_longer_word_or_path_is_not_refused(self) -> None:
        names = self.names()
        self.put("ok", PLAIN)
        for name in sorted(names):
            for spelling in test_worker.UNREFUSED_SPELLINGS:
                arguments = spelling.format(n=name)
                with self.subTest(arguments=arguments):
                    self.assertIn(arguments, self.render("ok", arguments).prompt)


class NamesTest(Fixture):
    def test_invalid_names_and_absent_files_are_unknown_command(self) -> None:
        self.put("ok", PLAIN)
        for name in ("/ok", "ok.md", "Ok", "a/b", "../ok", "", ".ok", "missing"):
            with self.subTest(name):
                self.assertEqual(self.cause(name), "unknown_command")

    def test_an_absent_directory_is_the_existing_error(self) -> None:
        with self.assertRaises(MissingCommandsDirectoryError):
            command_files.render_command("ok", "", self.dir / "nowhere")

    def test_an_invalid_name_wins_over_an_absent_directory(self) -> None:
        with self.assertRaises(CommandRenderError) as ctx:
            command_files.render_command("Bad/Name", "", self.dir / "nowhere")
        self.assertEqual(ctx.exception.evidence["cause"], "unknown_command")

    def test_an_unreadable_file_is_refused(self) -> None:
        (self.dir / "bin.md").write_bytes(b"---\n\xff\xfe\n")
        self.assertEqual(self.cause("bin"), "unreadable")


class RealDirectoryTest(unittest.TestCase):
    def test_every_file_is_rendered_or_refused_for_the_contracts_reason(self) -> None:
        files = sorted(REAL_COMMANDS.glob("*.md"))
        self.assertTrue(files)
        union = set()
        refused = set()
        for path in files:
            text = path.read_text(encoding="utf-8")
            if decision.carries_disable_model_invocation_flag(text) or decision.carries_user_confirmation_guard(text):
                union.add(path.stem)
            try:
                rendered = command_files.render_command(path.stem, "", REAL_COMMANDS)
            except UserOnlyCommandError:
                refused.add(path.stem)
                continue
            self.assertIsInstance(rendered.state_writer, bool, path.stem)
        self.assertEqual(refused, union)
        section = CONTRACT.read_text(encoding="utf-8").split("## The user-only commands", 1)[1].split("The control each", 1)[0]
        listed = set(re.findall(r"^- `/([a-z0-9-]+)`$", section, re.M))
        self.assertEqual(len(listed), 9)
        self.assertEqual(refused, listed | {"recover-implementation-provenance", "satisfy-gate"})
        self.assertEqual(refused, ELEVEN)

    def test_arguments_naming_each_pinned_user_only_command_are_refused(self) -> None:
        for user_only in sorted(ELEVEN):
            for argument in (f"/{user_only}", f"see {user_only}."):
                with self.subTest(user_only, argument=argument), self.assertRaises(UserOnlyCommandError):
                    command_files.render_command("milestone-plan", argument, REAL_COMMANDS)

    def test_hinted_commands_render_with_a_representative_argument(self) -> None:
        for path in sorted(REAL_COMMANDS.glob("*.md")):
            try:
                base = command_files.render_command(path.stem, "", REAL_COMMANDS)
            except UserOnlyCommandError:
                continue
            if not base.argument_hint:
                continue
            rendered = command_files.render_command(path.stem, "SENTINEL-ARG", REAL_COMMANDS)
            self.assertIn("SENTINEL-ARG", rendered.prompt, path.stem)
            self.assertNotIn("$ARGUMENTS", rendered.prompt, path.stem)

    def test_a_body_naming_a_user_only_command_is_sent_verbatim(self) -> None:
        text = (REAL_COMMANDS / "milestone-plan.md").read_text(encoding="utf-8")
        self.assertIn("/approve-review", text)
        lines = text.split("\n")
        closing = lines.index("---", 1)
        body = "\n".join(lines[closing + 1:])
        rendered = command_files.render_command("milestone-plan", "", REAL_COMMANDS)
        self.assertEqual(rendered.prompt, "".join(body.split("$ARGUMENTS")))
        self.assertIn("/approve-review", rendered.prompt)

    def test_no_rendered_prompt_names_the_model_or_the_harness_tool(self) -> None:
        # COMMAND_FILES.md, "Neutrality rules": the word Claude not followed
        # by -Session, and the harness tool name.
        model = re.compile(r"\bClaude\b(?!-Session)")
        for path in sorted(REAL_COMMANDS.glob("*.md")):
            try:
                rendered = command_files.render_command(path.stem, "", REAL_COMMANDS)
            except UserOnlyCommandError:
                continue
            self.assertIsNone(model.search(rendered.prompt), path.stem)
            self.assertNotIn("SlashCommand", rendered.prompt, path.stem)


class WiringTest(unittest.TestCase):
    def test_codex_still_declares_no_worker_role_and_launchers_are_unchanged(self) -> None:
        self.assertNotIn(harness.WORKER, harness.CODEX.roles)
        self.assertEqual(set(worker_launch.LAUNCHERS), {a.name for a in harness.ADAPTERS.values() if harness.WORKER in a.roles})

    def test_nothing_in_controller_imports_the_module_but_the_package_init_and_the_codex_worker_launch(self) -> None:
        # H4b2: the Codex worker launch is the one consumer (it renders the command it launches).
        for path in sorted((REPO / "controller").glob("*.py")):
            if path.stem in {"command_files", "__init__", "codex_worker"}:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, (ast.Import, ast.ImportFrom)) else []
                module = getattr(node, "module", None) or ""
                self.assertFalse("command_files" in names or module.endswith("command_files"), path.name)


if __name__ == "__main__":
    unittest.main()
