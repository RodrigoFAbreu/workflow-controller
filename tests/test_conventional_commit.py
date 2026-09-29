"""Conventional Commit titles (squash-merge-tag-versioning CP1, Design A).

The grammar is SignalHub's, checked against every example its release
script accepts and rejects; the bump comes from the policy's
``change_types`` table, and ``!`` on a type that releases nothing refuses.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import conventional_commit as cc  # noqa: E402
from controller import repo_policy  # noqa: E402
from controller.errors import ControllerError, InvalidTitleError  # noqa: E402
from tests import fixtures  # noqa: E402

#: The post-cutover table (Design A).
CHANGE_TYPES = repo_policy.parse_policy(fixtures.CONVENTIONAL_POLICY).release.change_types


class GrammarTest(unittest.TestCase):
    def assertRejected(self, subject: str) -> InvalidTitleError:
        with self.assertRaises(cc.InvalidTitle) as caught:
            cc.parse(subject)
        self.assertIsInstance(caught.exception, ControllerError)
        self.assertEqual(caught.exception.code, "INVALID_TITLE")
        self.assertEqual(caught.exception.evidence, {"subject": subject, "problem": "grammar"})
        self.assertIn("type(scope)!: description", caught.exception.message)
        return caught.exception

    def test_accepted(self) -> None:
        cases = {
            "feat: add a thing": cc.Subject("feat", None, False, "add a thing"),
            "fix(client): x": cc.Subject("fix", "client", False, "x"),
            "feat(a.b_c/d-9)!: x": cc.Subject("feat", "a.b_c/d-9", True, "x"),
            "feat!: drop it": cc.Subject("feat", None, True, "drop it"),
            # The squash suffix is part of the description.
            "feat(client): add x (#94)": cc.Subject("feat", "client", False, "add x (#94)"),
            "feat: x (#12)": cc.Subject("feat", None, False, "x (#12)"),
            # An unknown type still parses; the table refuses it (``bump``).
            "feature: x": cc.Subject("feature", None, False, "x"),
            # Only a trailing newline is removed; trailing spaces are kept.
            "feat: x\n": cc.Subject("feat", None, False, "x"),
            "feat: x ": cc.Subject("feat", None, False, "x "),
        }
        for subject, expected in cases.items():
            with self.subTest(subject=subject):
                self.assertEqual(cc.parse(subject), expected)

    def test_rejected(self) -> None:
        for subject in (
            "Feat: x", "FEAT: x", "feat:add", "feat: ", "feat:", "feat add", "feat:  x",
            'Revert "feat: x"', "feat(): x", "feat():", "feat!(scope): x", "feat(Scope): x",
            "feat(a b): x", " feat: x", "\tfeat: x", "feat : x", "feat: x\n\n", "feat: x\nbody",
            "feat1: x", "", "feat!!: x",
        ):
            with self.subTest(subject=subject):
                self.assertRejected(subject)


class BumpTest(unittest.TestCase):
    def test_every_row_of_the_table(self) -> None:
        expected = {
            "feat": "minor",
            "fix": "patch", "perf": "patch", "refactor": "patch", "revert": "patch", "build": "patch",
            "style": "patch",
            "docs": "none", "chore": "none", "ci": "none", "test": "none",
        }
        self.assertEqual(dict(CHANGE_TYPES), expected)
        for type_, release in expected.items():
            with self.subTest(type=type_):
                self.assertEqual(cc.bump(f"{type_}: x", CHANGE_TYPES), release)
                self.assertEqual(cc.bump(f"{type_}(scope): x (#3)", CHANGE_TYPES), release)

    def test_breaking_on_a_releasing_type_is_major(self) -> None:
        for type_, release in CHANGE_TYPES.items():
            if release == "none":
                continue
            with self.subTest(type=type_):
                self.assertEqual(cc.bump(f"{type_}!: x", CHANGE_TYPES), "major")
                self.assertEqual(cc.bump(f"{type_}(s)!: x", CHANGE_TYPES), "major")

    def test_breaking_on_a_none_type_refuses(self) -> None:
        for type_ in ("docs", "chore", "ci", "test"):
            with self.subTest(type=type_):
                with self.assertRaises(cc.InvalidTitle) as caught:
                    cc.bump(f"{type_}!: x", CHANGE_TYPES)
                self.assertIn("a breaking change must release: use `feat!` or `fix!`",
                              caught.exception.message)
                self.assertEqual(caught.exception.evidence["problem"], "breaking_none")

    def test_an_unknown_type_refuses_naming_the_allowed_types(self) -> None:
        for subject in ("feature: x", "wip: x", "release(x)!: y"):
            with self.subTest(subject=subject):
                with self.assertRaises(cc.InvalidTitle) as caught:
                    cc.bump(subject, CHANGE_TYPES)
                self.assertIn(str(sorted(CHANGE_TYPES)), caught.exception.message)
                self.assertEqual(caught.exception.evidence["problem"], "unknown_type")

    def test_the_table_is_the_callers(self) -> None:
        self.assertEqual(cc.bump("style: x", {"style": "none"}), "none")
        with self.assertRaises(cc.InvalidTitle):
            cc.bump("feat: x", {"fix": "patch"})

    def test_a_grammar_failure_refuses_before_the_table(self) -> None:
        with self.assertRaises(cc.InvalidTitle) as caught:
            cc.bump("Feat: x", CHANGE_TYPES)
        self.assertEqual(caught.exception.evidence["problem"], "grammar")


if __name__ == "__main__":
    unittest.main()
