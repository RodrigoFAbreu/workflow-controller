"""Tests for ``tools/test_shards.py`` (``workflow-controller-adaptive-test-sharding``).

CP1: the inventory equals ``unittest discover``'s ids and order plus the
managed conformance suites, discovery errors refuse it, atoms are classes
except for module-fixture modules, selection by name, and the CI placement
partition. CP2: the shard result-record schema, the timing-profile schema,
EWMA update/merge/prune, estimates, and the fallback to defaults.

The synthetic-tree tests discover a throwaway package under a temporary
directory, then drop it from ``sys.modules`` and ``sys.path`` again.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
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


def _atom(key: str, n: int, *, family: str = "controller", kind: str = "class",
          module: str | None = None):
    if family == "conformance":
        return shards.Atom(key=key, family=family, kind="suite", module="conformance",
                           test_ids=(key,))
    module = module or (key if kind == "module" else key.rsplit(".", 1)[0])
    return shards.Atom(key=key, family=family, kind=kind, module=module,
                       test_ids=tuple(f"{key}.test_{i}" for i in range(n)))


def _inventory(*atoms):
    return shards.Inventory(test_ids=tuple(i for a in atoms for i in a.test_ids), atoms=atoms)


def _record(atoms, *, outcomes=None, digest="d1", fixture_errors=(), fixture_skips=(),
            omit=()):
    """A valid shard record running ``atoms`` ({Atom: seconds}); ``outcomes``
    overrides single ids' outcomes and ``omit`` drops ids (interrupted)."""
    outcomes = outcomes or {}
    tests = [{"id": i, "outcome": outcomes.get(i, "pass"), "seconds": 0.01}
             for atom in atoms for i in atom.test_ids if i not in omit]
    return shards.validate_shard_result({
        "schema_version": 1, "plan_digest": digest, "shard": 0,
        "argv": ["tools/run_tests.py", "exec-shard"],
        "started_at": "2026-09-26T18:00:00Z", "ended_at": "2026-09-26T18:01:00Z",
        "wall_seconds": 60.0, "exit_status": 0, "tests": tests,
        "fixture_errors": [{"description": d, "traceback": "Traceback"} for d in fixture_errors],
        "fixture_skips": [{"description": d, "reason": "no wheel"} for d in fixture_skips],
        "atoms": {atom.key: seconds for atom, seconds in atoms.items()},
        "leaked_processes": []})


def _profile(atoms: dict, name: str = "ci") -> "shards.TimingProfile":
    return shards.TimingProfile(profile=name, atoms=atoms)


class ResultRecordTest(unittest.TestCase):
    A = _atom("tests.test_a.ATest", 2)

    def test_a_well_formed_record_validates_and_round_trips(self) -> None:
        record = _record({self.A: 1.5})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shard-0.json"
            path.write_text(json.dumps(record))
            self.assertEqual(shards.load_shard_result(path), record)
        record["tests"][0]["detail"] = "reason"
        record["leaked_processes"] = [{"pid": 7, "argv": ["sleep", "9"], "age_seconds": 1.0}]
        self.assertIs(shards.validate_shard_result(record), record)

    def test_every_malformation_is_named(self) -> None:
        cases = {
            "missing keys leaked_processes": lambda r: r.pop("leaked_processes"),
            "unknown keys extra": lambda r: r.update(extra=1),
            "schema_version": lambda r: r.update(schema_version=2),
            "shard": lambda r: r.update(shard=True),
            "started_at": lambda r: r.update(started_at="yesterday"),
            "wall_seconds": lambda r: r.update(wall_seconds=math.inf),
            "tests\\[0\\].outcome": lambda r: r["tests"][0].update(outcome="flaky"),
            "tests\\[1\\].seconds": lambda r: r["tests"][1].update(seconds=-1),
            "tests\\[0\\] has unknown keys why": lambda r: r["tests"][0].update(why=""),
            "fixture_errors\\[0\\] lacks traceback":
                lambda r: r["fixture_errors"].append({"description": "x"}),
            "fixture_skips is not a list": lambda r: r.update(fixture_skips={}),
            "atoms is invalid": lambda r: r["atoms"].update({"x": math.nan}),
            "leaked_processes\\[0\\].pid":
                lambda r: r["leaked_processes"].append({"pid": 0, "argv": [], "age_seconds": 0}),
        }
        for problem, mutate in cases.items():
            with self.subTest(problem=problem):
                record = _record({self.A: 1.0})
                mutate(record)
                with self.assertRaisesRegex(shards.ResultRecordError, problem):
                    shards.validate_shard_result(record)
        with self.assertRaisesRegex(shards.ResultRecordError, "not a JSON object"):
            shards.validate_shard_result([])

    def test_an_unreadable_record_is_refused_naming_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shard-3.json"
            for content in (None, "{", "{}"):
                with self.subTest(content=content):
                    if content is not None:
                        path.write_text(content)
                    with self.assertRaisesRegex(shards.ResultRecordError, "shard-3.json"):
                        shards.load_shard_result(path)

    def test_the_verdict_counts_failures_and_fixture_errors_but_not_skips(self) -> None:
        a0, a1 = self.A.test_ids
        for outcome, passes in (("pass", True), ("skip", True), ("expected_failure", True),
                                ("fail", False), ("error", False), ("unexpected_success", False)):
            with self.subTest(outcome=outcome):
                self.assertIs(shards.record_passes(_record({self.A: 1}, outcomes={a1: outcome})),
                              passes)
        self.assertFalse(shards.record_passes(
            _record({self.A: 1}, fixture_errors=["tearDownModule (tests.test_a)"])))
        self.assertTrue(shards.record_passes(
            _record({self.A: 1}, fixture_skips=["setUpClass (tests.test_a.ATest)"])))

    def test_holder_descriptions(self) -> None:
        self.assertEqual(shards.holder_target("tearDownModule (tests.test_release_txn)"),
                         "tests.test_release_txn")
        self.assertEqual(shards.holder_target("setUpClass (tests.test_packaged_runtime.WheelTest)"),
                         "tests.test_packaged_runtime.WheelTest")
        self.assertIsNone(shards.holder_target("something odd"))


class TimingFileTest(unittest.TestCase):
    GOOD = {"schema_version": 1, "profile": "ci", "updated_from": ["d1"],
            "atoms": {"tests.test_a.ATest": {"seconds": 1.5, "samples": 2, "tests": 3}}}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.warnings: list[str] = []

    def _load(self, content):
        path = self.dir / "timings.json"
        if content is not None:
            path.write_bytes(content if isinstance(content, bytes)
                             else json.dumps(content).encode())
        return shards.load_timings(path, warn=self.warnings.append), path

    def test_a_valid_file_loads_with_its_digest(self) -> None:
        profile, path = self._load(self.GOOD)
        self.assertEqual(profile.profile, "ci")
        self.assertEqual(profile.atoms, self.GOOD["atoms"])
        self.assertEqual(profile.updated_from, ("d1",))
        self.assertEqual(len(profile.sha256), 64)
        self.assertEqual(self.warnings, [])

    def test_every_unusable_file_falls_back_to_the_defaults_with_one_warning(self) -> None:
        def with_atom(entry):
            return {**self.GOOD, "atoms": {"tests.test_a.ATest": entry}}

        cases = {
            "missing": None,
            "not json": b"{",
            "not utf-8": b"\xff\xfe",
            "not an object": [],
            "wrong version": {**self.GOOD, "schema_version": 2},
            "extra key": {**self.GOOD, "extra": 1},
            "negative": with_atom({"seconds": -1, "samples": 1, "tests": 1}),
            "non-finite": b'{"schema_version": 1, "profile": "ci", "updated_from": [], '
                          b'"atoms": {"x": {"seconds": NaN, "samples": 1, "tests": 1}}}',
            "infinite": b'{"schema_version": 1, "profile": "ci", "updated_from": [], '
                        b'"atoms": {"x": {"seconds": Infinity, "samples": 1, "tests": 1}}}',
            "zero tests": with_atom({"seconds": 1, "samples": 1, "tests": 0}),
            "string seconds": with_atom({"seconds": "1", "samples": 1, "tests": 1}),
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                self.warnings.clear()
                (self.dir / "timings.json").unlink(missing_ok=True)
                profile, path = self._load(content)
                self.assertIs(profile, shards.DEFAULT_TIMINGS)
                self.assertEqual(len(self.warnings), 1, self.warnings)
                self.assertIn(str(path), self.warnings[0])
        (self.dir / "unreadable").mkdir()
        self.warnings.clear()
        self.assertIs(shards.load_timings(self.dir / "unreadable", warn=self.warnings.append),
                      shards.DEFAULT_TIMINGS)
        self.assertEqual(len(self.warnings), 1)

    def test_the_default_warning_goes_to_stderr(self) -> None:
        with unittest.mock.patch("sys.stderr") as stderr:
            shards.load_timings(self.dir / "absent.json")
        written = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertEqual(written.count("\n"), 1)
        self.assertIn("absent.json", written)

    def test_write_is_atomic_canonical_and_reloadable(self) -> None:
        path = self.dir / "nested" / "timings.json"
        profile = shards.TimingProfile(profile="ci", atoms=self.GOOD["atoms"],
                                       updated_from=("d1",))
        shards.write_timings(path, profile)
        self.assertEqual(os.listdir(path.parent), ["timings.json"])
        text = path.read_text()
        self.assertEqual(json.loads(text), self.GOOD)
        self.assertTrue(text.endswith("\n"))
        reloaded = shards.load_timings(path, warn=self.warnings.append)
        self.assertEqual((reloaded.atoms, reloaded.updated_from), (profile.atoms, ("d1",)))
        shards.write_timings(path, profile)
        self.assertEqual(path.read_text(), text)

    def test_a_failed_write_leaves_the_old_file_and_no_temporary(self) -> None:
        path = self.dir / "timings.json"
        path.write_text("old")
        with unittest.mock.patch.object(shards.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                shards.write_timings(path, shards.DEFAULT_TIMINGS)
        self.assertEqual(path.read_text(), "old")
        self.assertEqual(os.listdir(self.dir), ["timings.json"])

    def test_the_local_profile_path(self) -> None:
        self.assertEqual(shards.local_timings_path({"XDG_CACHE_HOME": "/c", "HOME": "/h"}),
                         Path("/c/workflow-controller-tests/timings-local.json"))
        for environ in ({"HOME": "/h"}, {"XDG_CACHE_HOME": "", "HOME": "/h"}):
            self.assertEqual(shards.local_timings_path(environ),
                             Path("/h/.cache/workflow-controller-tests/timings-local.json"))
        self.assertFalse(shards.local_timings_path({"HOME": "/h"}).is_relative_to(
            fixtures.REPO_ROOT))

    def test_the_ci_profile_lives_in_tools(self) -> None:
        self.assertEqual(shards.CI_TIMINGS, Path("tools/test_timings.json"))


class TimingUpdateTest(unittest.TestCase):
    A = _atom("tests.test_a.ATest", 2)
    B = _atom("tests.test_a.BTest", 1)
    M = _atom("tests.test_m", 2, kind="module")
    C = _atom("conformance:x_test.py", 1, family="conformance")

    def _update(self, profile, *records, inventory=None):
        inventory = inventory or _inventory(self.A, self.B, self.M, self.C)
        return shards.update_timings(profile, records, inventory)

    def test_new_atoms_take_the_observation(self) -> None:
        updated = self._update(shards.DEFAULT_TIMINGS, _record({self.A: 4.0, self.C: 80.0}))
        self.assertEqual(updated.atoms, {
            self.A.key: {"seconds": 4.0, "samples": 1, "tests": 2},
            self.C.key: {"seconds": 80.0, "samples": 1, "tests": 1}})
        self.assertEqual(updated.updated_from, ("d1",))

    def test_the_ewma_moves_halfway_and_counts_samples(self) -> None:
        old = _profile({self.A.key: {"seconds": 4.0, "samples": 3, "tests": 2}})
        updated = self._update(old, _record({self.A: 2.0}, digest="d2"),
                               _record({self.A: 1.0}, digest="d3"))
        self.assertEqual(updated.atoms[self.A.key], {"seconds": 2.0, "samples": 5, "tests": 2})
        self.assertEqual(updated.profile, "ci")
        self.assertEqual(updated.updated_from, ("d2", "d3"))

    def test_a_changed_test_count_resets_the_atom(self) -> None:
        old = _profile({self.A.key: {"seconds": 40.0, "samples": 9, "tests": 5}})
        self.assertEqual(self._update(old, _record({self.A: 3.0})).atoms[self.A.key],
                         {"seconds": 3.0, "samples": 1, "tests": 2})

    def test_atoms_absent_from_the_inventory_are_pruned(self) -> None:
        old = _profile({"tests.test_gone.GoneTest": {"seconds": 1, "samples": 1, "tests": 1},
                        self.B.key: {"seconds": 1, "samples": 1, "tests": 1}})
        gone = _atom("tests.test_gone.GoneTest", 1)
        updated = self._update(old, _record({self.A: 2.0, gone: 5.0}))
        self.assertEqual(set(updated.atoms), {self.A.key, self.B.key})
        self.assertEqual(set(self._update(old).atoms), {self.B.key})

    def test_unrepresentative_atoms_are_not_merged(self) -> None:
        a0, a1 = self.A.test_ids
        old = _profile({self.A.key: {"seconds": 4.0, "samples": 1, "tests": 2},
                        self.M.key: {"seconds": 6.0, "samples": 1, "tests": 2}})
        cases = {
            "failed": dict(outcomes={a1: "fail"}),
            "errored": dict(outcomes={a0: "error"}),
            "unexpected success": dict(outcomes={a0: "unexpected_success"}),
            "interrupted": dict(omit=(a1,)),
            "class fixture skip": dict(fixture_skips=["setUpClass (tests.test_a.ATest)"]),
            "module fixture skip": dict(fixture_skips=["setUpModule (tests.test_a)"]),
            "class fixture error": dict(fixture_errors=["tearDownClass (tests.test_a.ATest)"]),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                updated = self._update(old, _record({self.A: 99.0, self.B: 7.0}, **kwargs))
                self.assertEqual(updated.atoms[self.A.key], old.atoms[self.A.key])
                if name == "module fixture skip":
                    self.assertNotIn(self.B.key, updated.atoms)
                else:
                    self.assertEqual(updated.atoms[self.B.key]["seconds"], 7.0)
        duplicated = _record({self.A: 99.0})
        duplicated["tests"].append(dict(duplicated["tests"][0]))
        self.assertEqual(self._update(old, duplicated).atoms[self.A.key], old.atoms[self.A.key])

    def test_a_class_fixture_inside_a_module_atom_withholds_the_module(self) -> None:
        record = _record({self.M: 50.0, self.A: 2.0},
                         fixture_errors=["setUpClass (tests.test_m.Inner)"])
        updated = self._update(_profile({}), record)
        self.assertEqual(set(updated.atoms), {self.A.key})

    def test_an_unattributable_fixture_withholds_the_whole_record(self) -> None:
        record = _record({self.A: 2.0}, fixture_errors=["??"])
        self.assertEqual(self._update(_profile({}), record).atoms, {})

    def test_a_partial_selection_is_not_merged(self) -> None:
        partial = shards.Atom(key=self.A.key, family="controller", kind="class",
                              module="tests.test_a", test_ids=self.A.test_ids[:1])
        self.assertEqual(self._update(_profile({}), _record({partial: 1.0})).atoms, {})

    def test_updated_from_is_ordered_unique_and_bounded(self) -> None:
        records = [_record({self.B: 1.0}, digest=f"d{i}") for i in range(30)]
        records.append(_record({self.B: 1.0}, digest="d25"))
        updated = self._update(_profile({}), *records)
        self.assertEqual(len(updated.updated_from), shards.MAX_UPDATED_FROM)
        self.assertEqual(updated.updated_from[-2:], ("d29", "d25"))

    def test_the_profile_name_can_be_replaced(self) -> None:
        updated = shards.update_timings(_profile({}, "seed-local"), [],
                                        _inventory(self.A), profile_name="ci")
        self.assertEqual(updated.profile, "ci")


class EstimateTest(unittest.TestCase):
    A = _atom("tests.test_a.ATest", 4)
    A2 = _atom("tests.test_a.NewTest", 3)
    Z = _atom("tests.test_z.ZTest", 2)
    M = _atom("tests.test_m", 2, kind="module")
    C = _atom("conformance:x_test.py", 1, family="conformance")

    def test_a_known_atom_uses_its_recorded_seconds(self) -> None:
        profile = _profile({self.A.key: {"seconds": 8.0, "samples": 2, "tests": 4},
                            self.C.key: {"seconds": 81.0, "samples": 1, "tests": 1}})
        self.assertEqual(shards.estimate_atoms([self.A, self.C], [profile]),
                         {self.A.key: 8.0, self.C.key: 81.0})
        partial = shards.Atom(key=self.A.key, family="controller", kind="class",
                              module="tests.test_a", test_ids=self.A.test_ids[:1])
        self.assertEqual(shards.estimate_atoms([partial], [profile]), {self.A.key: 2.0})

    def test_an_unknown_atom_in_a_known_module_uses_the_module_mean(self) -> None:
        profile = _profile({self.A.key: {"seconds": 8.0, "samples": 1, "tests": 4},
                            "tests.test_other.O": {"seconds": 100.0, "samples": 1, "tests": 1}})
        self.assertEqual(shards.estimate_atoms([self.A2], [profile]), {self.A2.key: 6.0})
        module_atom = _profile({self.M.key: {"seconds": 10.0, "samples": 1, "tests": 5}})
        new_class = _atom("tests.test_m.Other", 3, module="tests.test_m")
        self.assertEqual(shards.estimate_atoms([new_class], [module_atom]),
                         {new_class.key: 6.0})

    def test_an_unknown_module_uses_the_profile_mean_without_conformance(self) -> None:
        profile = _profile({self.A.key: {"seconds": 8.0, "samples": 1, "tests": 4},
                            self.C.key: {"seconds": 900.0, "samples": 1, "tests": 1}})
        self.assertEqual(shards.estimate_atoms([self.Z], [profile]), {self.Z.key: 4.0})

    def test_without_history_the_defaults_apply(self) -> None:
        estimates = shards.estimate_atoms([self.Z, self.C], [shards.DEFAULT_TIMINGS])
        self.assertEqual(estimates, {self.Z.key: 2 * shards.DEFAULT_SECONDS_PER_TEST,
                                     self.C.key: shards.DEFAULT_CONFORMANCE_SECONDS})
        self.assertEqual(shards.estimate_atoms([self.Z], []), {self.Z.key: 0.5})

    def test_profiles_are_consulted_in_priority_order(self) -> None:
        local = _profile({self.A.key: {"seconds": 1.0, "samples": 1, "tests": 4}}, "local")
        ci = _profile({self.A.key: {"seconds": 9.0, "samples": 1, "tests": 4},
                       self.Z.key: {"seconds": 5.0, "samples": 1, "tests": 2}})
        self.assertEqual(shards.estimate_atoms([self.A, self.Z], [local, ci]),
                         {self.A.key: 1.0, self.Z.key: 5.0})
        self.assertEqual(shards.estimate_atoms([self.A2], [local, ci]), {self.A2.key: 0.75})


class RealInventoryTimingTest(unittest.TestCase):
    """The defaults, and any corrupt file, give every selected atom a finite
    positive estimate and never change the selection (I3)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = shards.build_inventory(fixtures.REPO_ROOT)

    def _assert_estimates_cover(self, selection, profiles) -> None:
        estimates = shards.estimate_atoms(selection.atoms, profiles)
        self.assertEqual(list(estimates), [atom.key for atom in selection.atoms])
        for key, seconds in estimates.items():
            self.assertTrue(math.isfinite(seconds) and seconds > 0, (key, seconds))

    def test_the_defaults_cover_the_full_and_ci_selections(self) -> None:
        for selection in (self.inventory.select(), self.inventory.select(ci_placement=True)):
            self._assert_estimates_cover(selection, [shards.DEFAULT_TIMINGS])

    def test_a_corrupt_file_yields_the_same_selection_and_valid_estimates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timings.json"
            path.write_text('{"schema_version": 1, "atoms": {"x": {"seconds": -3}}}')
            warnings = []
            profile = shards.load_timings(path, warn=warnings.append)
        before = self.inventory.select()
        self._assert_estimates_cover(before, [profile])
        self.assertEqual(self.inventory.select(), before)
        self.assertEqual(len(warnings), 1)

    def test_a_record_of_the_real_inventory_merges_every_atom(self) -> None:
        selection = self.inventory.select(["tests.test_forge", "tests.test_lock",
                                           "conformance:workflow_state_test.py"])
        record = _record({atom: 1.0 for atom in selection.atoms})
        updated = shards.update_timings(shards.DEFAULT_TIMINGS, [record], self.inventory,
                                        profile_name="seed-local")
        self.assertEqual(set(updated.atoms), {atom.key for atom in selection.atoms})
        self._assert_estimates_cover(self.inventory.select(), [updated])


if __name__ == "__main__":
    unittest.main()
