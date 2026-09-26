"""Tests for ``tools/test_shards.py`` (``workflow-controller-adaptive-test-sharding`` CP1):
the inventory equals ``unittest discover``'s ids and order plus the managed
conformance suites, discovery errors refuse it, atoms are classes except for
module-fixture modules, selection by name, and the CI placement partition.

The synthetic-tree tests discover a throwaway package under a temporary
directory, then drop it from ``sys.modules`` and ``sys.path`` again.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures  # noqa: E402

TEST_SHARDS_PY = fixtures.REPO_ROOT / "tools" / "test_shards.py"
_spec = importlib.util.spec_from_file_location("test_shards", TEST_SHARDS_PY)
shards = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = shards
_spec.loader.exec_module(shards)

CONFORMANCE_YML = fixtures.REPO_ROOT / ".github" / "workflows" / "workflow-conformance.yml"
MODULE_FIXTURE_MODULES = {"tests.test_release_txn", "tests.test_forge"}


def _discover_ids(start: Path, top: Path) -> list[str]:
    ids = []

    def walk(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                walk(item)
            else:
                ids.append(item.id())

    walk(unittest.TestLoader().discover(str(start), top_level_dir=str(top)))
    return ids


class SyntheticTree:
    """A throwaway test package ``<pkg>`` under a temporary directory."""

    def __init__(self, test_case: unittest.TestCase, modules: dict[str, str]) -> None:
        self.pkg = f"shardpkg_{uuid.uuid4().hex[:12]}"
        tmp = tempfile.TemporaryDirectory()
        test_case.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.start = self.top / self.pkg
        self.start.mkdir()
        (self.start / "__init__.py").write_text("")
        for name, source in modules.items():
            text = textwrap.dedent(source).replace("PKG", self.pkg)
            (self.start / f"{name}.py").write_text(text)
        test_case.addCleanup(self._forget)

    def _forget(self) -> None:
        for name in [name for name in sys.modules if name.split(".")[0] == self.pkg]:
            del sys.modules[name]
        while str(self.top) in sys.path:
            sys.path.remove(str(self.top))

    def family(self):
        return shards.controller_family(self.start, self.top)


PLAIN = textwrap.dedent("""
    import unittest

    class ATest(unittest.TestCase):
        def test_b(self): pass
        def test_a(self): pass

    class BTest(unittest.TestCase):
        def test_c(self): pass
""")


class SyntheticInventoryTest(unittest.TestCase):
    def test_a_clean_tree_matches_discover_and_forms_class_atoms(self) -> None:
        tree = SyntheticTree(self, {"test_one": PLAIN, "test_two": PLAIN})
        ids, atoms = tree.family()
        self.assertEqual(ids, _discover_ids(tree.start, tree.top))
        p = tree.pkg
        self.assertEqual([(a.key, a.kind, a.test_ids) for a in atoms], [
            (f"{p}.test_one.ATest", "class",
             (f"{p}.test_one.ATest.test_a", f"{p}.test_one.ATest.test_b")),
            (f"{p}.test_one.BTest", "class", (f"{p}.test_one.BTest.test_c",)),
            (f"{p}.test_two.ATest", "class",
             (f"{p}.test_two.ATest.test_a", f"{p}.test_two.ATest.test_b")),
            (f"{p}.test_two.BTest", "class", (f"{p}.test_two.BTest.test_c",)),
        ])

    def test_an_import_error_refuses(self) -> None:
        tree = SyntheticTree(self, {"test_ok": PLAIN,
                                    "test_broken": "import no_such_module_anywhere\n"})
        with self.assertRaises(shards.InventoryError) as raised:
            tree.family()
        message = str(raised.exception)
        self.assertRegex(message, rf"_FailedTest substituted for \S*\b{tree.pkg}\.test_broken\b")
        self.assertIn("no_such_module_anywhere", message)

    def test_a_failed_test_without_a_loader_error_refuses(self) -> None:
        tree = SyntheticTree(self, {"test_sneaky": """
            import unittest

            def load_tests(loader, tests, pattern):
                failed = loader.loadTestsFromName("PKG.test_sneaky.Missing")
                loader.errors.clear()
                return unittest.TestSuite([failed])
        """})
        with self.assertRaises(shards.InventoryError) as raised:
            tree.family()
        self.assertRegex(str(raised.exception), r"_FailedTest substituted for \S*\bMissing\b")
        self.assertNotIn("discovery error", str(raised.exception))

    def test_an_error_holder_refuses(self) -> None:
        tree = SyntheticTree(self, {"test_holder": """
            import unittest
            from unittest.suite import _ErrorHolder

            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([_ErrorHolder("setUpModule (PKG.test_holder)")])
        """})
        with self.assertRaisesRegex(shards.InventoryError, r"_ErrorHolder substituted"):
            tree.family()

    def test_a_module_skipped_at_import_refuses(self) -> None:
        tree = SyntheticTree(self, {"test_ok": PLAIN, "test_skipped": """
            import unittest
            raise unittest.SkipTest("not here")
        """})
        with self.assertRaisesRegex(shards.InventoryError,
                                    rf"ModuleSkipped substituted for .*{tree.pkg}\.test_skipped"):
            tree.family()

    def test_a_duplicate_id_refuses(self) -> None:
        tree = SyntheticTree(self, {"test_a": PLAIN, "test_b": "from PKG.test_a import ATest\n"})
        with self.assertRaisesRegex(shards.InventoryError,
                                    rf"duplicate test id {tree.pkg}\.test_a\.ATest\.test_a"):
            tree.family()

    def test_module_fixtures_form_one_module_atom(self) -> None:
        for fixture in ("setUpModule", "tearDownModule"):
            with self.subTest(fixture=fixture):
                tree = SyntheticTree(self, {"test_fixture": PLAIN + f"\ndef {fixture}(): pass\n",
                                            "test_plain": PLAIN})
                ids, atoms = tree.family()
                p = tree.pkg
                self.assertEqual([(a.key, a.kind, len(a.test_ids)) for a in atoms], [
                    (f"{p}.test_fixture", "module", 3),
                    (f"{p}.test_plain.ATest", "class", 2),
                    (f"{p}.test_plain.BTest", "class", 1),
                ])
                self.assertEqual([i for a in atoms for i in a.test_ids], ids)

    def test_a_class_is_keyed_by_the_module_that_loads_it(self) -> None:
        tree = SyntheticTree(self, {"helpers": """
            import unittest

            class HelperTest(unittest.TestCase):
                def test_x(self): pass
        """, "test_user": "from PKG.helpers import HelperTest\n"})
        ids, atoms = tree.family()
        p = tree.pkg
        self.assertEqual(ids, [f"{p}.helpers.HelperTest.test_x"])
        self.assertEqual([(a.key, a.module) for a in atoms],
                         [(f"{p}.test_user.HelperTest", f"{p}.test_user")])

    def test_a_class_bound_under_another_name_refuses(self) -> None:
        tree = SyntheticTree(self, {"helpers": PLAIN,
                                    "test_alias": "from PKG.helpers import BTest as Renamed\n"})
        with self.assertRaisesRegex(shards.InventoryError,
                                    rf"not bound as {tree.pkg}\.test_alias\.BTest"):
            tree.family()

    def test_an_interleaved_atom_refuses(self) -> None:
        tree = SyntheticTree(self, {"test_mixed": PLAIN + textwrap.dedent("""
            def load_tests(loader, tests, pattern):
                a = loader.loadTestsFromTestCase(ATest)
                b = loader.loadTestsFromTestCase(BTest)
                first, second = list(a)
                return unittest.TestSuite([first, *b, second])
        """)})
        with self.assertRaisesRegex(shards.InventoryError, r"ATest is not contiguous"):
            tree.family()


class ConformanceFamilyTest(unittest.TestCase):
    def _workflow(self, text: str) -> tuple[Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts").mkdir()
        for name in ("a_test.py", "b_test.py"):
            (root / "scripts" / name).write_text("")
        (root / "wf.yml").write_text(textwrap.dedent(text))
        return root / "wf.yml", root / "scripts"

    def test_the_real_family_equals_the_managed_run_lines(self) -> None:
        managed = re.findall(r"(?m)^\s*run: python3 (\S+)\s*$",
                             CONFORMANCE_YML.read_text(encoding="utf-8"))
        self.assertTrue(managed)
        ids, atoms = shards.conformance_family(CONFORMANCE_YML, fixtures.REPO_ROOT / "scripts")
        self.assertEqual(ids, [f"conformance:{suite}" for suite in managed])
        self.assertEqual([(a.key, a.kind, a.family, a.test_ids) for a in atoms],
                         [(i, "suite", "conformance", (i,)) for i in ids])

    def test_order_is_the_workflow_order(self) -> None:
        wf, scripts = self._workflow("""
            steps:
              - name: b
                run: python3 b_test.py
              - name: a
                run: python3 a_test.py
        """)
        self.assertEqual(shards.conformance_suites(wf, scripts), ["b_test.py", "a_test.py"])

    def test_refusals(self) -> None:
        cases = {
            "no suite": ("steps: []\n", r"declares no"),
            "duplicate": ("run: python3 a_test.py\nrun: python3 a_test.py\n",
                          r"duplicate conformance suite a_test\.py"),
            "missing file": ("run: python3 gone_test.py\n", r"gone_test\.py is not a file"),
        }
        for label, (text, pattern) in cases.items():
            with self.subTest(label):
                wf, scripts = self._workflow(text)
                with self.assertRaisesRegex(shards.InventoryError, pattern):
                    shards.conformance_suites(wf, scripts)

    def test_an_unreadable_workflow_refuses(self) -> None:
        with self.assertRaisesRegex(shards.InventoryError, r"cannot read"):
            shards.conformance_suites(Path("/nonexistent/wf.yml"), Path("/nonexistent"))


class RealInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = shards.build_inventory(fixtures.REPO_ROOT)

    def test_controller_ids_equal_discover_in_discover_order(self) -> None:
        controller = [i for i in self.inventory.test_ids if not i.startswith("conformance:")]
        self.assertEqual(controller, _discover_ids(fixtures.REPO_ROOT / "tests", fixtures.REPO_ROOT))
        self.assertEqual(len(controller), len(set(controller)))

    def test_conformance_ids_follow_the_controller_ids(self) -> None:
        ids = list(self.inventory.test_ids)
        first = next(n for n, i in enumerate(ids) if i.startswith("conformance:"))
        self.assertTrue(all(i.startswith("conformance:") for i in ids[first:]))
        self.assertEqual(ids[first:], shards.conformance_family(
            CONFORMANCE_YML, fixtures.REPO_ROOT / "scripts")[0])

    def test_atoms_partition_the_ids_in_canonical_order(self) -> None:
        self.assertEqual([i for a in self.inventory.atoms for i in a.test_ids],
                         list(self.inventory.test_ids))
        keys = [a.key for a in self.inventory.atoms]
        self.assertEqual(len(keys), len(set(keys)))

    def test_only_the_module_fixture_modules_are_module_atoms(self) -> None:
        by_kind: dict[str, set[str]] = {}
        for atom in self.inventory.atoms:
            by_kind.setdefault(atom.kind, set()).add(atom.key)
        self.assertEqual(by_kind["module"], MODULE_FIXTURE_MODULES)
        self.assertEqual({a.family for a in self.inventory.atoms if a.kind == "class"}, {"controller"})
        self.assertEqual({a.kind for a in self.inventory.atoms if a.family == "conformance"}, {"suite"})
        for atom in self.inventory.atoms:
            if atom.kind == "class":
                self.assertEqual(atom.key.rsplit(".", 1)[0], atom.module, atom.key)
                # The atom's name selects exactly its own ids.
                self.assertTrue(all(i.startswith(atom.key + ".") for i in atom.test_ids), atom.key)
                self.assertNotIn(atom.module, MODULE_FIXTURE_MODULES)


class SelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = shards.build_inventory(fixtures.REPO_ROOT)

    def _ids(self, *names: str) -> tuple[str, ...]:
        return self.inventory.select(names).test_ids

    def test_no_names_selects_everything(self) -> None:
        selection = self.inventory.select()
        self.assertEqual(selection.test_ids, self.inventory.test_ids)
        self.assertEqual(selection.atoms, self.inventory.atoms)

    def test_module_class_and_method(self) -> None:
        module = self._ids("tests.test_worker")
        self.assertTrue(module)
        self.assertTrue(all(i.startswith("tests.test_worker.") for i in module))
        cls = self._ids("tests.test_worker.OwnershipTest")
        self.assertTrue(set(cls) < set(module))
        self.assertTrue(all(i.startswith("tests.test_worker.OwnershipTest.") for i in cls))
        self.assertEqual(self._ids(cls[0]), (cls[0],))
        self.assertEqual([a.key for a in self.inventory.select([cls[0]]).atoms],
                         ["tests.test_worker.OwnershipTest"])

    def test_tests_selects_the_controller_family(self) -> None:
        self.assertEqual(self._ids("tests"),
                         tuple(i for i in self.inventory.test_ids if not i.startswith("conformance:")))

    def test_conformance_names(self) -> None:
        suites = tuple(i for i in self.inventory.test_ids if i.startswith("conformance:"))
        self.assertEqual(self._ids("conformance"), suites)
        self.assertEqual(self._ids("conformance:workflow_state_test.py"),
                         ("conformance:workflow_state_test.py",))

    def test_names_union_in_canonical_order(self) -> None:
        both = self._ids("conformance:workflow_state_test.py", "tests.test_lock", "tests.test_lock")
        self.assertEqual(both, self._ids("tests.test_lock") + ("conformance:workflow_state_test.py",))

    def test_a_prefix_matches_only_whole_components(self) -> None:
        with self.assertRaisesRegex(shards.SelectionError, r"'tests\.test_work'"):
            self._ids("tests.test_work")

    def test_unmatched_names_refuse_naming_each(self) -> None:
        for names in (("tests.test_nope",), ("tests.test_lock", "conformance:nope.py", "Nope"),
                      ("conformance:",), ("",)):
            with self.subTest(names=names):
                with self.assertRaises(shards.SelectionError) as raised:
                    self._ids(*names)
                for name in names:
                    if name != "tests.test_lock":
                        self.assertIn(repr(name), str(raised.exception))

    def test_a_partial_module_atom_keeps_its_key_with_the_subset(self) -> None:
        atom = next(a for a in self.inventory.atoms if a.key == "tests.test_forge")
        cls = atom.test_ids[0].rsplit(".", 1)[0]
        selection = self.inventory.select([cls])
        self.assertEqual([(a.key, a.kind) for a in selection.atoms], [("tests.test_forge", "module")])
        self.assertEqual(selection.atoms[0].test_ids,
                         tuple(i for i in atom.test_ids if i.startswith(cls + ".")))


class CiPlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = shards.build_inventory(fixtures.REPO_ROOT)

    def test_the_real_inventory_is_partitioned_exactly(self) -> None:
        partition = shards.ci_partition(self.inventory)
        self.assertEqual(set(partition), {"shards", "package", "excluded"})
        placed = [i for ids in partition.values() for i in ids]
        self.assertEqual(sorted(placed), sorted(self.inventory.test_ids))
        self.assertEqual(len(placed), len(set(placed)))
        modules = {where: {a.module for a in self.inventory.atoms if set(a.test_ids) & set(ids)}
                   for where, ids in partition.items()}
        self.assertEqual(modules["package"], {"tests.test_packaged_runtime"})
        self.assertEqual(modules["excluded"], {"tests.test_integration_disposable_repo"})
        self.assertIn("conformance", modules["shards"])

    def test_the_ci_selection_is_the_shard_part(self) -> None:
        self.assertEqual(self.inventory.select(ci_placement=True).test_ids,
                         shards.ci_partition(self.inventory)["shards"])
        self.assertEqual(self.inventory.select(["tests.test_packaged_runtime"],
                                               ci_placement=True).test_ids, ())

    def test_every_placement_has_a_reason(self) -> None:
        self.assertEqual(shards.CI_PLACEMENT["tests.test_packaged_runtime"][0], "package")
        self.assertEqual(shards.CI_PLACEMENT["tests.test_integration_disposable_repo"][0], "excluded")
        self.assertIn("claude", shards.CI_PLACEMENT["tests.test_integration_disposable_repo"][1])
        for module, (_, reason) in shards.CI_PLACEMENT.items():
            self.assertTrue(reason.strip(), module)

    def test_a_stale_placement_refuses(self) -> None:
        atom = shards.Atom(key="tests.test_x.T", family="controller", kind="class",
                           module="tests.test_x", test_ids=("tests.test_x.T.test_a",))
        inventory = shards.Inventory(test_ids=atom.test_ids, atoms=(atom,))
        with self.assertRaisesRegex(shards.InventoryError, r"tests\.test_packaged_runtime"):
            shards.ci_partition(inventory)


if __name__ == "__main__":
    unittest.main()
