"""Tests for ``tools/check_docs.py``: the repository's own documentation is
clean, and every rule fails on a synthetic tree that breaks it and passes on
one that does not. The command rule must never run the Controller, resolve a
runtime or start a process: the tests forbid ``identity.pin`` and every
subprocess while it runs."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from controller import cli, identity, workflow_contract  # noqa: E402
from tests import test_plan_document_consistency as legacy  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_docs", REPO_ROOT / "tools" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
sys.modules["check_docs"] = check_docs
_spec.loader.exec_module(check_docs)

HEADER = "> For: operators. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0."


class Tree:
    """A throwaway documentation tree."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def check(self, **kwargs) -> list[str]:
        # Synthetic trees model the first checkpoint unless a test says otherwise,
        # so raising ACTIVE_THROUGH in the repository never changes them.
        kwargs.setdefault("through", 1)
        return check_docs.check_tree(self.root, **kwargs)

    def close(self) -> None:
        self._tmp.cleanup()


class TreeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = Tree()
        self.addCleanup(self.tree.close)


class LiveRepositoryTest(TreeTestCase):
    def test_the_repository_documentation_is_clean(self) -> None:
        self.assertEqual(check_docs.check_tree(REPO_ROOT), [])

    def test_the_command_line_entry_point_exits_zero_on_the_repository(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_docs.main(["--root", str(REPO_ROOT)]), 0)

    def test_the_command_line_entry_point_exits_one_with_a_problem(self) -> None:
        self.tree.write("README.md", "# T\n\n[x](missing.md)\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_docs.main(["--root", str(self.tree.root)]), 1)

    def test_every_activated_page_has_a_checkpoint_and_the_lists_are_constants(self) -> None:
        for pages in (check_docs.USER_PAGES, check_docs.COMMAND_PAGES):
            for page, checkpoint in pages:
                self.assertIsInstance(page, str)
                self.assertIn(checkpoint, range(1, 6))
        self.assertEqual(check_docs.active(check_docs.USER_PAGES, 1), [])


class SlugTest(unittest.TestCase):
    def test_underscores_and_literal_hyphens_are_kept(self) -> None:
        self.assertEqual(check_docs.slug_base("A_B-c d"), "a_b-c-d")
        self.assertEqual(check_docs.slug_base("Exit 10: gate (human)"), "exit-10-gate-human")

    def test_existing_underscore_bearing_anchors(self) -> None:
        text = (REPO_ROOT / "docs/guide/troubleshooting.md").read_text(encoding="utf-8")
        self.assertIn("workflow_query_failed-and-workflow_query_failed", check_docs.anchors(text))

    def test_inline_code_headings_keep_the_code_text(self) -> None:
        self.assertEqual(check_docs.anchors("### `WORKFLOW_QUERY_FAILED` and `workflow_query_failed`\n"),
                         ["workflow_query_failed-and-workflow_query_failed"])

    def test_collisions_are_allocated_against_used_anchors(self) -> None:
        self.assertEqual(check_docs.anchors("# foo-1\n# foo\n# foo\n"), ["foo-1", "foo", "foo-2"])

    def test_headings_in_fences_are_not_anchors(self) -> None:
        self.assertEqual(check_docs.anchors("# a\n```\n# b\n```\n"), ["a"])


class LinkRuleTest(TreeTestCase):
    def test_a_good_tree_passes(self) -> None:
        self.tree.write("README.md", "# R\n\n[g](docs/guide/g.md#sub-title) [self](#r)\n")
        self.tree.write("docs/guide/g.md", "# G\n\n## Sub title\n")
        self.assertEqual(self.tree.check(), [])

    def test_a_missing_file_fails(self) -> None:
        self.tree.write("README.md", "# R\n\n[g](docs/none.md)\n")
        self.assertTrue(any("missing file" in p for p in self.tree.check()))

    def test_a_missing_anchor_fails(self) -> None:
        self.tree.write("README.md", "# R\n\n[g](docs/guide/g.md#nope)\n")
        self.tree.write("docs/guide/g.md", "# G\n")
        self.assertTrue(any("missing heading" in p for p in self.tree.check()))

    def test_anchors_are_case_sensitive(self) -> None:
        self.tree.write("README.md", "# R\n\n[g](docs/guide/g.md#Upgrade)\n")
        self.tree.write("docs/guide/g.md", "# G\n\n## Upgrade\n")
        self.assertTrue(self.tree.check())

    def test_links_in_code_are_skipped(self) -> None:
        self.tree.write("README.md", "# R\n\n`[a](gone.md)`\n\n```\n[b](gone.md)\n```\n")
        self.assertEqual(self.tree.check(), [])

    def test_a_stale_link_into_adr_and_releases_is_flagged(self) -> None:
        self.tree.write("docs/adr/0001.md", "# A\n\n[old](../guide/removed.md#x)\n")
        self.tree.write("docs/releases/1.0.0.md", "# N\n\n[old](../guide/removed.md#x)\n")
        problems = self.tree.check()
        self.assertEqual(len(problems), 2)
        self.assertTrue(any(p.startswith("docs/adr/0001.md") for p in problems))
        self.assertTrue(any(p.startswith("docs/releases/1.0.0.md") for p in problems))

    def test_the_stub_keeps_the_historical_links_valid(self) -> None:
        self.tree.write("docs/guide/installation.md",
                        "# Installation\n\n## Supported Workflow releases\n\n## Upgrade\n\n"
                        "## Moving a target to another Workflow release\n")
        self.tree.write("docs/adr/0001.md",
                        "# A\n\n[a](../guide/installation.md#upgrade) "
                        "[b](../guide/installation.md#moving-a-target-to-another-workflow-release)\n")
        self.assertEqual(self.tree.check(), [])

    def test_roadmap_and_active_milestone_are_out_of_scope(self) -> None:
        self.tree.write("docs/ROADMAP.md", "# R\n\n[x](gone.md)\n")
        self.tree.write("docs/ACTIVE_MILESTONE.md", "# R\n\n[x](gone.md)\n")
        self.tree.write("docs/milestones/completed/a.md", "# R\n\n[x](gone.md)\n")
        self.assertEqual(self.tree.check(), [])

    def test_external_links(self) -> None:
        good = ["https://github.com/RodrigoFAbreu/workflow-manager#readme",
                "https://github.com/RodrigoFAbreu/workflow",
                "https://github.com/RodrigoFAbreu/workflow-controller/releases",
                "https://github.com/RodrigoFAbreu/workflow-controller/blob/main/README.md",
                "https://github.com/RodrigoFAbreu/workflow-controller/issues/3",
                "https://github.com/RodrigoFAbreu/workflow-controller/pull/24",
                "https://pipx.pypa.io/"]
        bad = ["https://github.com/RodrigoFAbreu/SignalHub",
               "https://github.com/Other/workflow",
               "https://github.com/RodrigoFAbreu/workflow-controller/issues/x",
               "https://github.com/RodrigoFAbreu/workflow-controller/wiki",
               "https://example.com/x",
               "https://github.com/RodrigoFAbreu"]
        for url in good:
            self.assertIsNone(check_docs.external_link_problem(url), url)
        for url in bad:
            self.assertIsNotNone(check_docs.external_link_problem(url), url)


class _NoRuntime:
    """Forbids runtime resolution and every subprocess."""

    def __enter__(self):
        def boom(*args, **kwargs):
            raise AssertionError("the documentation check ran a process or resolved a runtime")

        self._patches = [mock.patch.object(identity, "pin", boom),
                         mock.patch.object(subprocess, "Popen", boom),
                         mock.patch.object(subprocess, "run", boom)]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class CommandRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with _NoRuntime():
            cls.checker = check_docs.CommandChecker(cli.build_parser)

    def problem(self, line: str):
        with _NoRuntime():
            return self.checker.problem(line)

    def test_valid_lines_pass(self) -> None:
        for line in ["workflow-controller --version",
                     "workflow-controller --help",
                     "workflow-controller step --help",
                     "workflow-controller settings --help",
                     "workflow-controller milestone-binding . --help",
                     "workflow-controller step . --help",
                     "workflow-controller step .",
                     "workflow-controller run . --follow --max-steps 3",
                     "workflow-controller --json inspect <repo>",
                     "workflow-controller run <repo> --max-steps <n>",
                     "workflow-controller follow --run $RUN_ID",
                     "workflow-controller status",
                     "workflow-controller settings show",
                     "workflow-controller milestone-binding . --new-pr",
                     "workflow-controller step . # a comment",
                     "workflow-controller step . | tee out"]:
            self.assertIsNone(self.problem(line), line)

    def test_invalid_lines_fail(self) -> None:
        for line in ["workflow-controller --version --bogus",
                     "workflow-controller --help --bogus",
                     "workflow-controller step --help --bogus",
                     "workflow-controller milestone-binding . --help --bogus",
                     "workflow-controller milestone-binding . --help --new-pr --abandon",
                     "workflow-controller stpe --help",
                     "workflow-controller step",
                     "workflow-controller milestone-binding .",
                     "workflow-controller step . --nope",
                     "workflow-controller run . --max-steps zero",
                     "workflow-controller settings",
                     "workflow-controller"]:
            self.assertIsNotNone(self.problem(line), line)

    def test_the_real_parser_accepts_what_the_check_rejects_on_purpose(self) -> None:
        with mock.patch.object(cli, "version_text", lambda ident=None: "workflow-controller x"), \
                mock.patch.object(cli, "_describe_running_runtime", lambda: "x"), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.build_parser().parse_args(["--version", "--bogus"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIsNotNone(self.problem("workflow-controller --version --bogus"))

    def test_extraction_handles_fences_continuations_and_text_blocks(self) -> None:
        text = ("```bash\n$ workflow-controller run . \\\n    --follow\nls\n```\n\n"
                "```text\nworkflow-controller nonsense\n```\n\n"
                "```\nworkflow-controller step . [--follow]\n```\n")
        self.assertEqual(check_docs.command_lines(text), ["workflow-controller run .      --follow"])

    def test_a_task_page_with_a_bad_command_fails(self) -> None:
        tree = Tree()
        self.addCleanup(tree.close)
        tree.write("docs/install.md", "# I\n\n" + HEADER + "\n\n```bash\nworkflow-controller nope\n```\n")
        with _NoRuntime():
            problems = check_docs.check_commands(tree.root, 3, cli.build_parser)
        self.assertTrue(any("docs/install.md" in p for p in problems))
        self.assertTrue(any("README.md: command page is missing" in p for p in problems))

    def test_the_check_agrees_with_the_legacy_extractor_on_what_an_invocation_is(self) -> None:
        cases = [("workflow-controller --json inspect repo", True),
                 ("workflow-controller step <repo>", True),
                 ("workflow-controller run . [--max-steps N]", False),
                 ("workflow-controller --runtime-dir /x status", True),
                 ("ls -l", False)]
        for line, expected in cases:
            legacy_says = bool(legacy.extract_invocation_lines(f"Run `{line}` now.\n"))
            new_says = bool(check_docs.command_lines(f"```bash\n{line}\n```\n"))
            self.assertEqual(legacy_says, expected, line)
            self.assertEqual(new_says, expected, line)


class PageRuleTest(TreeTestCase):
    def page(self, rel: str, body: str, header: str | None = HEADER) -> None:
        self.tree.write(rel, "# Title\n\n" + (header + "\n\n" if header else "") + body + "\n")

    def test_a_good_page_passes(self) -> None:
        self.page("docs/glossary.md", "Words.")
        problems = check_docs.check_pages(self.tree.root, 2)
        self.assertFalse([p for p in problems if "glossary" in p])

    def test_missing_header_fails(self) -> None:
        self.page("docs/glossary.md", "Words.", header=None)
        self.assertTrue(any("first line after the title" in p for p in check_docs.check_pages(self.tree.root, 2)))

    def test_header_must_be_first_after_the_title(self) -> None:
        self.tree.write("docs/glossary.md", "# Title\n\nIntro.\n\n" + HEADER + "\n")
        self.assertTrue([p for p in check_docs.check_pages(self.tree.root, 2) if "glossary" in p])

    def test_internal_ids_fail(self) -> None:
        self.page("docs/glossary.md", "See CP3 and LPR-R1-002 and the-real-item.")
        self.tree.write("docs/milestones/completed/the-real-item.md", "# x\n")
        problems = [p for p in check_docs.check_pages(self.tree.root, 2) if "glossary" in p]
        self.assertEqual(len(problems), 3)

    def test_work_item_ids_from_state_are_flagged_as_whole_tokens(self) -> None:
        self.tree.write("docs/ai-workflow/WORKFLOW_STATE.json", json.dumps({"work_items": {"item-one": {}}}))
        self.page("docs/glossary.md", "item-one and item-one-more and `workflow-controller-tests/timings-local.json`.")
        problems = [p for p in check_docs.check_pages(self.tree.root, 2) if "glossary" in p]
        self.assertEqual(len(problems), 1)
        self.assertIn("'item-one'", problems[0])

    def test_link_targets_are_not_scanned(self) -> None:
        self.tree.write("docs/milestones/completed/the-real-item.md", "# x\n")
        self.page("docs/glossary.md", "[plan](milestones/completed/the-real-item.md)")
        self.assertFalse([p for p in check_docs.check_pages(self.tree.root, 2) if "glossary" in p])

    def test_pages_are_checked_only_once_active(self) -> None:
        self.assertEqual(check_docs.check_pages(self.tree.root, 1), [])
        self.assertTrue(check_docs.check_pages(self.tree.root, 2))


class ExitCodeRuleTest(TreeTestCase):
    CLI = types.SimpleNamespace(EXIT_OK=0, EXIT_USAGE=2, EXIT_GATE=10, SIGINT_EXIT_STATUS=130)
    ADR = "# ADR\n\n## Exit codes\n\n| Code | Meaning |\n|---|---|\n| 0 | a |\n| 2 | b |\n| 10 | c |\n"

    def write(self, codes: list[int], adr: str | None = None) -> None:
        rows = "".join(f"| {c} | x |\n" for c in codes)
        self.tree.write("docs/exit-codes.md", f"# E\n\n{HEADER}\n\n| Code | Meaning |\n|---|---|\n{rows}")
        self.tree.write(check_docs.ADR_EXIT_TABLE, adr or self.ADR)

    def problems(self) -> list[str]:
        return check_docs.check_exit_codes(self.tree.root, 2, self.CLI)

    def test_agreement_including_sigint_passes(self) -> None:
        self.write([0, 2, 10, 130])
        self.assertEqual(self.problems(), [])

    def test_missing_sigint_fails(self) -> None:
        self.write([0, 2, 10])
        self.assertTrue(any("130" in p for p in self.problems()))

    def test_a_code_not_in_the_cli_fails(self) -> None:
        self.write([0, 2, 10, 99, 130])
        self.assertTrue(any("99" in p for p in self.problems()))

    def test_a_code_missing_from_the_page_fails_against_both(self) -> None:
        self.write([0, 2, 130])
        self.assertEqual(len([p for p in self.problems() if "10" in p]), 2)

    def test_adr_disagreement_fails(self) -> None:
        self.write([0, 2, 10, 130], adr=self.ADR + "| 45 | d |\n")
        self.assertTrue(any("ADR 0001 lists exit status 45" in p for p in self.problems()))

    def test_the_live_repository_constants_are_what_the_check_reads(self) -> None:
        constants, sigint = check_docs.cli_exit_codes(cli)
        self.assertEqual(sigint, cli.SIGINT_EXIT_STATUS)
        self.assertEqual(constants, {0, 2, 10, 15, 16, 20, 30, 35, 40, 45, 50})


class DigestRuleTest(TreeTestCase):
    D1, D2 = "a" * 64, "b" * 64
    CONTRACTS = {"2.6.0": types.SimpleNamespace(query_script_sha256={"s": "a" * 64, "f": "b" * 64}),
                 "2.5.1": types.SimpleNamespace(query_script_sha256=None)}

    def write(self, *digests: str) -> None:
        self.tree.write("docs/compatibility.md", "# C\n\n" + HEADER + "\n\n" + "\n".join(digests) + "\n")

    def test_both_directions(self) -> None:
        self.write(self.D1, self.D2)
        self.assertEqual(check_docs.check_digests(self.tree.root, 2, self.CONTRACTS), [])
        self.write(self.D1)
        self.assertTrue(any("not on the page" in p for p in check_docs.check_digests(self.tree.root, 2, self.CONTRACTS)))
        self.write(self.D1, self.D2, "c" * 64)
        self.assertTrue(any("not a RELEASE_CONTRACTS digest" in p
                            for p in check_docs.check_digests(self.tree.root, 2, self.CONTRACTS)))

    def test_the_live_contract_has_digests(self) -> None:
        self.assertEqual(len(check_docs.contract_digests(workflow_contract.RELEASE_CONTRACTS)), 2)


class StubRuleTest(TreeTestCase):
    def test_exactly_three_headings(self) -> None:
        body = "\n".join(f"## {h}\n" for h in check_docs.INSTALLATION_STUB_HEADINGS)
        self.tree.write("docs/guide/installation.md", f"# Installation\n\n{body}")
        self.assertEqual(check_docs.check_stub(self.tree.root, 4), [])
        self.tree.write("docs/guide/installation.md", f"# Installation\n\n{body}\n## Extra\n")
        self.assertTrue(check_docs.check_stub(self.tree.root, 4))
        self.tree.write("docs/guide/installation.md", f"# Installation\n\n{body}\n### Extra\n")
        self.assertTrue(check_docs.check_stub(self.tree.root, 4))

    def test_inactive_before_its_checkpoint(self) -> None:
        self.assertEqual(check_docs.check_stub(self.tree.root, 3), [])


if __name__ == "__main__":
    unittest.main()
