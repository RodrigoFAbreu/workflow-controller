"""Tests for ``tools/test_shards.py`` (``workflow-controller-adaptive-test-sharding``).

CP1: the inventory equals ``unittest discover``'s ids and order plus the
managed conformance suites, discovery errors refuse it, atoms are classes
except for module-fixture modules, selection by name, and the CI placement
partition. CP2: the shard result-record schema, the timing-profile schema,
EWMA update/merge/prune, estimates, and the fallback to defaults. CP3: the
shard count, deterministic LPT, the plan document and digest, the planner's
self-validation, and the equivalence regression suite (I1 over seeded random
cases, I4 across interpreters, LPT balance on the recorded baseline). CP5:
the ``EXCLUSIVE_ATOMS`` audit, and the exclusive shard's placement and
validation.

The synthetic-tree tests discover a throwaway package under a temporary
directory, then drop it from ``sys.modules`` and ``sys.path`` again.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import re
import subprocess
import sys
import tempfile
import textwrap
import time
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
            "schema_version": lambda r: r.update(schema_version=3),
            "missing keys run_attempt": lambda r: r.update(schema_version=2),
            "unknown keys run_attempt": lambda r: r.update(run_attempt=1),
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

    def test_version_2_requires_a_run_attempt_and_version_1_is_attempt_1(self) -> None:
        v1 = _record({self.A: 1.0})
        self.assertEqual(shards.record_attempt(v1), 1)
        v2 = dict(v1, schema_version=2, run_attempt=3)
        self.assertIs(shards.validate_shard_result(v2), v2)
        self.assertEqual(shards.record_attempt(v2), 3)
        for attempt in (0, -1, True, "2", 1.0):
            with self.subTest(attempt=attempt):
                with self.assertRaisesRegex(shards.ResultRecordError, "run_attempt is invalid"):
                    shards.validate_shard_result(dict(v2, run_attempt=attempt))

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



BASELINE_TIMINGS = fixtures.REPO_ROOT / "tests" / "golden" / "test_shards_baseline_timings.json"


def _selection(*atoms, names=()):
    return shards.Selection(names=tuple(names),
                            test_ids=tuple(i for a in atoms for i in a.test_ids), atoms=atoms)


def _timed(seconds: dict) -> tuple:
    """A ``timings`` argument recording each ``{Atom: seconds}`` exactly."""
    profile = _profile({atom.key: {"seconds": s, "samples": 1, "tests": len(atom.test_ids)}
                        for atom, s in seconds.items()})
    return ((Path("/nonexistent/timings.json"), profile),)


def _params(**overrides):
    base = dict(profile="ci", target_shard_seconds=10, min_shards=1, max_shards=32)
    base.update(overrides)
    return shards.PlanParameters(**base)


def _redigest(plan: dict) -> dict:
    plan["plan_digest"] = shards.plan_digest(plan)
    return plan


class ShardCountTest(unittest.TestCase):
    """``clamp(ceil(total / max(target, largest)), min, max)``, at most one
    shard per atom, and ``--shards`` pins it."""

    def count(self, estimates, **overrides) -> int:
        return shards.shard_count([int(s * 1000) for s in estimates], _params(**overrides))

    def test_the_formula_rounds_up_total_over_target(self) -> None:
        self.assertEqual(self.count([5] * 10, target_shard_seconds=10), 5)
        self.assertEqual(self.count([5] * 10 + [0.001], target_shard_seconds=10), 6)
        self.assertEqual(self.count([5] * 10, target_shard_seconds=25), 2)

    def test_the_largest_atom_is_a_floor_on_the_target(self) -> None:
        # total 100, largest 40 > target 10: effective 40, ceil(100/40) = 3
        self.assertEqual(self.count([40, 20, 20, 10, 10], target_shard_seconds=10), 3)
        self.assertEqual(self.count([40, 20, 20, 10, 10], target_shard_seconds=50), 2)

    def test_the_count_is_clamped_to_the_minimum_and_the_maximum(self) -> None:
        self.assertEqual(self.count([1] * 10, target_shard_seconds=100, min_shards=3), 3)
        self.assertEqual(self.count([5] * 10, target_shard_seconds=1, max_shards=4), 4)
        self.assertEqual(self.count([0] * 10, target_shard_seconds=1, min_shards=2), 2)

    def test_there_are_never_more_shards_than_atoms(self) -> None:
        self.assertEqual(self.count([5] * 3, target_shard_seconds=1, min_shards=8), 3)
        self.assertEqual(self.count([100], min_shards=2), 1)

    def test_a_pinned_count_wins_but_is_capped_at_the_atoms(self) -> None:
        self.assertEqual(self.count([5] * 10, shards=7, max_shards=2), 7)
        self.assertEqual(self.count([5] * 10, shards=1, min_shards=4), 1)
        self.assertEqual(self.count([5] * 3, shards=32), 3)

    def test_the_floor_comes_from_the_floor_estimates_only(self) -> None:
        # Design J: total 100, largest 40, target 10. A floor list without
        # the 40 s atom plans ceil(100/20) = 5; omitted, the floor is every
        # estimate (3, as before); empty, the effective length is the target.
        estimates = [40_000, 20_000, 20_000, 10_000, 10_000]
        params = _params(target_shard_seconds=10)
        self.assertEqual(shards.shard_count(estimates, params, [20_000, 10_000]), 5)
        self.assertEqual(shards.shard_count(estimates, params), 3)
        self.assertEqual(shards.shard_count(estimates, params, None), 3)
        self.assertEqual(shards.shard_count(estimates, params, []), 5)
        self.assertEqual(shards.shard_count(estimates, params, [40_000]), 3)


class ProfileParametersTest(unittest.TestCase):

    def test_the_profiles_defaults(self) -> None:
        local = shards.profile_parameters("local", cpu_count=16)
        self.assertEqual((local.target_shard_seconds, local.min_shards, local.max_shards,
                          local.shards), (60, 2, 8, None))
        self.assertEqual(shards.profile_parameters("local", cpu_count=4).max_shards, 4)
        self.assertEqual(shards.profile_parameters("local", cpu_count=1).max_shards, 1)
        ci = shards.profile_parameters("ci", cpu_count=1)
        self.assertEqual((ci.target_shard_seconds, ci.min_shards, ci.max_shards), (180, 2, 16))
        with unittest.mock.patch.object(shards.os, "cpu_count", return_value=None):
            self.assertEqual(shards.profile_parameters("local").max_shards, 1)

    def test_every_parameter_is_overridable(self) -> None:
        params = shards.profile_parameters("local", cpu_count=2, target_shard_seconds=5,
                                           min_shards=3, max_shards=12, shards=9)
        self.assertEqual(params, shards.PlanParameters("local", 5, 3, 12, 9))

    def test_invalid_parameters_refuse_naming_each(self) -> None:
        cases = {"unknown profile": dict(profile="nightly"),
                 "target_shard_seconds": dict(target_shard_seconds=0),
                 "min_shards": dict(min_shards=0),
                 "max_shards": dict(max_shards=True),
                 "shards must": dict(shards=-1)}
        for problem, overrides in cases.items():
            with self.subTest(problem=problem):
                kwargs = dict(profile="ci")
                kwargs.update(overrides)
                with self.assertRaisesRegex(shards.PlanError, problem):
                    shards.profile_parameters(**kwargs)
        with self.assertRaisesRegex(shards.PlanError, "target_shard_seconds"):
            shards.profile_parameters("ci", target_shard_seconds=math.inf)


class PlanTest(unittest.TestCase):
    A = _atom("tests.test_a.ATest", 3)
    B = _atom("tests.test_a.BTest", 2)
    C = _atom("tests.test_b.CTest", 1)
    D = _atom("tests.test_c", 4, kind="module")
    S = _atom("conformance:x_test.py", 1, family="conformance")

    def plan(self, seconds: dict, **overrides) -> dict:
        return shards.build_plan(_selection(*seconds), _params(**overrides), _timed(seconds))

    def test_lpt_assigns_longest_first_to_the_least_loaded_shard(self) -> None:
        plan = self.plan({self.A: 3, self.B: 5, self.C: 4, self.D: 2, self.S: 1}, shards=2)
        # B(5)->0, C(4)->1, A(3)->1, D(2)->0, S(1)->0; each shard in canonical order
        self.assertEqual([s["atoms"] for s in plan["shards"]],
                         [[self.B.key, self.D.key, self.S.key], [self.A.key, self.C.key]])
        self.assertEqual([s["estimate_seconds"] for s in plan["shards"]], [8.0, 7.0])
        self.assertEqual(plan["shards"][1]["test_ids"], list(self.A.test_ids + self.C.test_ids))

    def test_ties_break_by_canonical_index_then_shard_index(self) -> None:
        plan = self.plan({self.A: 1, self.B: 1, self.C: 1, self.D: 1}, shards=3)
        self.assertEqual([s["atoms"] for s in plan["shards"]],
                         [[self.A.key, self.D.key], [self.B.key], [self.C.key]])

    def test_zero_estimates_still_leave_no_shard_empty(self) -> None:
        plan = self.plan({self.A: 0, self.B: 0, self.C: 0, self.D: 0, self.S: 0}, shards=4)
        self.assertEqual([len(s["atoms"]) for s in plan["shards"]], [2, 1, 1, 1])
        self.assertEqual(plan["shards"][0]["estimate_seconds"], 0.002)

    def test_estimates_are_compared_in_whole_milliseconds(self) -> None:
        plan = self.plan({self.A: 1.0001, self.B: 1.0004}, shards=1)
        self.assertEqual(plan["shards"][0]["estimate_seconds"], 2.0)
        tied = self.plan({self.A: 1.0001, self.B: 1.0004, self.C: 1.0}, shards=2)
        self.assertEqual(tied["shards"][0]["atoms"], [self.A.key, self.C.key])

    def test_the_plan_document(self) -> None:
        selection = _selection(self.A, self.S, names=("tests.test_a.ATest", "conformance"))
        plan = shards.build_plan(selection, _params(), _timed({self.A: 30, self.S: 20}))
        self.assertEqual(set(plan), {"schema_version", "profile", "selection_names",
                                     "selected_ids", "parameters", "shard_count", "shards",
                                     "timing_source", "plan_digest"})
        self.assertEqual(plan["profile"], "ci")
        self.assertEqual(plan["selection_names"], ["tests.test_a.ATest", "conformance"])
        self.assertEqual(plan["selected_ids"], list(selection.test_ids))
        self.assertEqual(plan["parameters"],
                         {"target_shard_seconds": 10, "min_shards": 1, "max_shards": 32})
        self.assertEqual(plan["shard_count"], 2)
        self.assertEqual(plan["plan_digest"], hashlib.sha256(json.dumps(
            {k: v for k, v in plan.items() if k != "plan_digest"}, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest())
        json.dumps(plan, allow_nan=False)

    def test_only_a_pinned_count_enters_the_parameters(self) -> None:
        seconds = {self.A: 5, self.B: 5}
        free, pinned = self.plan(seconds), self.plan(seconds, shards=1)
        self.assertNotIn("shards", free["parameters"])
        self.assertEqual(pinned["parameters"]["shards"], 1)
        self.assertEqual(free["shard_count"], pinned["shard_count"])
        self.assertNotEqual(free["plan_digest"], pinned["plan_digest"])

    def test_every_planning_input_changes_the_digest(self) -> None:
        seconds = {self.A: 5, self.B: 5, self.C: 5}
        base = self.plan(seconds)["plan_digest"]
        variants = {
            "profile": shards.build_plan(_selection(*seconds), _params(profile="local"),
                                         _timed(seconds)),
            "target": self.plan(seconds, target_shard_seconds=11),
            "min": self.plan(seconds, min_shards=2),
            "max": self.plan(seconds, max_shards=31),
            "timings": self.plan({self.A: 5, self.B: 5, self.C: 6}),
            "names": shards.build_plan(_selection(*seconds, names=("x",)), _params(),
                                       _timed(seconds)),
        }
        for name, plan in variants.items():
            with self.subTest(changed=name):
                self.assertNotEqual(plan["plan_digest"], base)

    def test_the_timing_source_names_files_without_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            committed = root / "tools" / "test_timings.json"
            local = Path(tmp) / "cache" / "timings-local.json"
            for path in (committed, local):
                shards.write_timings(path, _profile({self.A.key: {
                    "seconds": 1.0, "samples": 1, "tests": 3}}, path.stem))
            timings = shards.timings_for("local", root, environ={
                "XDG_CACHE_HOME": str(Path(tmp) / "cache-root")}, warn=lambda _: None)
            self.assertEqual(shards.timing_source(timings, root), [
                {"path": "tools/test_timings.json", "sha256": timings[1][1].sha256}])
            loaded = [(local, shards.load_timings(local)),
                      (committed, shards.load_timings(committed))]
            self.assertEqual(shards.timing_source(loaded, root), [
                {"path": "local-profile", "sha256": loaded[0][1].sha256},
                {"path": "tools/test_timings.json", "sha256": loaded[1][1].sha256}])
            warnings = []
            ci = shards.timings_for("ci", Path(tmp), warn=warnings.append)
            self.assertEqual(len(ci), 1)
            self.assertEqual(shards.timing_source(ci, Path(tmp)), "defaults")
            self.assertEqual(len(warnings), 1)
        self.assertEqual(shards.timing_source([]), "defaults")

    def test_an_empty_selection_refuses(self) -> None:
        with self.assertRaisesRegex(shards.PlanError, "empty"):
            shards.build_plan(_selection(), _params())

    def test_the_planner_validates_its_own_plan(self) -> None:
        assign = shards.assign_atoms

        def drop_one(estimates, count):
            first, *rest = assign(estimates, count)
            return [first[1:], *rest]

        with unittest.mock.patch.object(shards, "assign_atoms", side_effect=drop_one):
            with self.assertRaisesRegex(shards.PlanError, "in no shard"):
                self.plan({self.A: 3, self.B: 2, self.C: 1}, shards=1)


class LocalFloorTest(unittest.TestCase):
    """Design J (amendment 2, LIR4-001): for the ``local`` profile only an
    estimate from a local-machine timing file sets the largest-atom floor; a
    committed (CI) estimate still counts in the total. The ``ci`` profile's
    floor is every atom."""

    BIG = _atom("conformance:big_test.py", 1, family="conformance")
    SMALL = [_atom(f"tests.test_s.S{i}Test", 1) for i in range(10)]

    def _plan(self, profile: str, local_big: bool) -> dict:
        # total 100 + 10 * 10 = 200 s, target 20 s, max 32 shards: the big
        # atom as the floor gives ceil(200/100) = 2, without it 10.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            cache = Path(tmp) / "cache"
            committed = {self.BIG.key: {"seconds": 100.0, "samples": 1, "tests": 1}}
            local = {atom.key: {"seconds": 10.0, "samples": 1, "tests": 1}
                     for atom in self.SMALL}
            if local_big:
                local[self.BIG.key] = committed[self.BIG.key]
            shards.write_timings(root / shards.CI_TIMINGS, _profile(committed, "ci"))
            shards.write_timings(shards.local_timings_path({"XDG_CACHE_HOME": str(cache)}),
                                 _profile(local, "local"))
            timings = shards.timings_for(profile, root, environ={"XDG_CACHE_HOME": str(cache)},
                                         warn=self.fail)
            return shards.build_plan(_selection(self.BIG, *self.SMALL),
                                     _params(profile=profile, target_shard_seconds=20),
                                     timings, root)

    def test_a_committed_only_atom_does_not_set_the_local_floor(self) -> None:
        plan = self._plan("local", local_big=False)
        self.assertEqual(plan["shard_count"], 10)
        # It still counts in the assignment: LPT gives it a shard of its own.
        self.assertEqual(plan["shards"][0]["atoms"], [self.BIG.key])
        self.assertEqual(plan["shards"][0]["estimate_seconds"], 100.0)

    def test_the_same_atom_recorded_locally_sets_the_local_floor(self) -> None:
        self.assertEqual(self._plan("local", local_big=True)["shard_count"], 2)

    def test_the_committed_atom_sets_the_ci_floor(self) -> None:
        self.assertEqual(self._plan("ci", local_big=False)["shard_count"], 2)

    def test_a_default_estimate_does_not_set_the_local_floor(self) -> None:
        plan = shards.build_plan(_selection(self.BIG, *self.SMALL),
                                 _params(profile="local", target_shard_seconds=20),
                                 _timed({atom: 10.0 for atom in self.SMALL}))
        # BIG unrecorded: the 30 s conformance default counts in the total
        # (130 s) but not in the floor (target 20 s) -> ceil(6.5) = 7 shards,
        # where the default as a floor would give ceil(130/30) = 5.
        self.assertEqual(plan["shard_count"], 7)


class RealInventoryLocalFloorTest(unittest.TestCase):
    """Amendment 2's regressions, on the real inventory and the committed
    ``tools/test_timings.json``: a cold local cache, and a local profile
    without the acceptance matrix, both plan 8 shards on 8 CPUs (both
    planned 5 before Design J). The CI plan is unchanged."""

    MATRIX = "conformance:workflow_acceptance_matrix_test.py"

    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = shards.build_inventory(fixtures.REPO_ROOT)
        cls.committed = shards.load_timings(fixtures.REPO_ROOT / shards.CI_TIMINGS,
                                            warn=lambda message: None)
        assert cls.MATRIX in cls.committed.atoms

    def _local_plan(self, local_atoms: dict | None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            environ = {"XDG_CACHE_HOME": tmp}
            if local_atoms is not None:
                shards.write_timings(shards.local_timings_path(environ),
                                     _profile(local_atoms, "local"))
            warnings = []
            timings = shards.timings_for("local", fixtures.REPO_ROOT, environ=environ,
                                         warn=warnings.append)
            self.assertEqual(len(warnings), 0 if local_atoms is not None else 1)
            return shards.build_plan(self.inventory.select(),
                                     shards.profile_parameters("local", cpu_count=8),
                                     timings, fixtures.REPO_ROOT)

    def test_a_cold_local_cache_plans_eight_shards(self) -> None:
        self.assertEqual(self._local_plan(None)["shard_count"], 8)

    def test_a_local_profile_without_the_matrix_plans_eight_shards(self) -> None:
        atoms = {key: dict(entry) for key, entry in self.committed.atoms.items()
                 if key != self.MATRIX}
        self.assertEqual(self._local_plan(atoms)["shard_count"], 8)

    def test_the_ci_plan_floors_on_the_matrix_over_every_atom(self) -> None:
        selection = self.inventory.select(ci_placement=True)
        parameters = shards.profile_parameters("ci")
        timings = shards.timings_for("ci", fixtures.REPO_ROOT, warn=self.fail)
        plan = shards.build_plan(selection, parameters, timings, fixtures.REPO_ROOT)
        estimates = shards.estimate_atoms(selection.atoms, [profile for _, profile in timings])
        parallel = [max(1, round(estimates[atom.key] * 1000)) for atom in selection.atoms
                    if atom.key not in shards.EXCLUSIVE_ATOMS]
        largest = max(parallel)
        self.assertEqual(largest, round(estimates[self.MATRIX] * 1000))
        self.assertGreater(largest, parameters.target_shard_seconds * 1000)
        expected = min(max(-(-sum(parallel) // largest), parameters.min_shards),
                       parameters.max_shards, len(parallel))
        exclusive = any(atom.key in shards.EXCLUSIVE_ATOMS for atom in selection.atoms)
        self.assertEqual(plan["shard_count"], expected + exclusive)


class PlanValidationTest(unittest.TestCase):
    """A tampered plan fails validation, whether or not its digest was
    recomputed, so the structural checks never rely on the digest."""

    A, B, C = PlanTest.A, PlanTest.B, PlanTest.C

    def setUp(self) -> None:
        self.selection = _selection(self.A, self.B, self.C)
        self.plan = shards.build_plan(self.selection, _params(shards=2),
                                      _timed({self.A: 3, self.B: 2, self.C: 2}))

    def test_a_valid_plan_validates_through_json(self) -> None:
        plan = json.loads(json.dumps(self.plan))
        self.assertIs(shards.validate_plan(plan, self.selection), plan)
        self.assertIs(shards.validate_plan(plan), plan)

    def test_tampering_is_refused(self) -> None:
        a0 = self.A.test_ids[0]
        cases = {
            "plan_digest does not match": (lambda p: p["shards"][0].update(
                estimate_seconds=0), False),
            "keys are not exactly": (lambda p: p.pop("timing_source"), True),
            "schema_version": (lambda p: p.update(schema_version=2), True),
            "shard_count is 3": (lambda p: p.update(shard_count=3), True),
            "shards\\[1\\].index": (lambda p: p["shards"][1].update(index=0), True),
            "shard 1 is empty": (lambda p: p["shards"][1].update(test_ids=[], atoms=[]), True),
            "in no shard": (lambda p: p["shards"][0]["test_ids"].remove(a0), True),
            "is in shards 0 and 1": (lambda p: p["shards"][1]["test_ids"].append(a0), True),
            "unselected id": (lambda p: p["shards"][1]["test_ids"].append("tests.x.Y.z"), True),
            "canonical order": (lambda p: p["shards"][0]["test_ids"].reverse(), True),
            "selected_ids has duplicates": (lambda p: p["selected_ids"].append(a0), True),
            "lists of strings": (lambda p: p["shards"][0].update(atoms="x"), True),
            "selected_ids must be strings": (lambda p: p["selected_ids"].append(1), True),
            "not canonical JSON": (lambda p: p["shards"][0].update(
                estimate_seconds=math.nan), False),
        }
        for problem, (tamper, redigest) in cases.items():
            with self.subTest(problem=problem):
                plan = json.loads(json.dumps(self.plan))
                tamper(plan)
                if redigest:
                    _redigest(plan)
                with self.assertRaisesRegex(shards.PlanError, problem):
                    shards.validate_plan(plan)
        with self.assertRaisesRegex(shards.PlanError, "not a JSON object"):
            shards.validate_plan([])

    def test_against_its_selection_a_split_or_foreign_atom_is_refused(self) -> None:
        split = json.loads(json.dumps(self.plan))
        moved = self.B.test_ids[-1]
        home = next(s for s in split["shards"] if moved in s["test_ids"])
        other = split["shards"][1 - home["index"]]
        home["test_ids"].remove(moved)
        other["test_ids"] = sorted(other["test_ids"] + [moved],
                                   key=self.selection.test_ids.index)
        _redigest(split)
        shards.validate_plan(split)
        with self.assertRaisesRegex(shards.PlanError, "whole atoms"):
            shards.validate_plan(split, self.selection)
        foreign = json.loads(json.dumps(self.plan))
        foreign["shards"][0]["atoms"].append("tests.test_z.Z")
        _redigest(foreign)
        with self.assertRaisesRegex(shards.PlanError, "outside the selection"):
            shards.validate_plan(foreign, self.selection)
        other_selection = _selection(self.A, self.B)
        with self.assertRaisesRegex(shards.PlanError, "differ from the selection"):
            shards.validate_plan(self.plan, other_selection)


class ExclusiveAtomsTest(unittest.TestCase):
    """CP5: the ``EXCLUSIVE_ATOMS`` registry is audited against the
    inventory, and its atoms go, alone and last, on one exclusive shard."""

    A, B, C, D = PlanTest.A, PlanTest.B, PlanTest.C, PlanTest.D

    def test_every_registered_atom_exists_and_has_a_reason(self) -> None:
        inventory = shards.build_inventory(fixtures.REPO_ROOT)
        self.assertEqual(shards.exclusive_atoms_problems(inventory), [])
        keys = {atom.key for atom in inventory.atoms}
        for key, reason in shards.EXCLUSIVE_ATOMS.items():
            with self.subTest(atom=key):
                self.assertIn(key, keys)
                self.assertTrue(reason.strip())

    def test_an_unknown_atom_or_an_empty_reason_is_named(self) -> None:
        inventory = shards.Inventory(test_ids=self.A.test_ids, atoms=(self.A,))
        self.assertEqual(shards.exclusive_atoms_problems(
            inventory, {self.A.key: " ", "tests.test_gone.GoneTest": "why"}), [
            f"{self.A.key!r} has no reason",
            "'tests.test_gone.GoneTest' names no atom of the inventory"])
        self.assertEqual(shards.exclusive_atoms_problems(inventory, {self.A.key: "why"}), [])

    def test_the_inventory_refuses_a_bad_registry(self) -> None:
        tree = SyntheticTree(self, {"test_x": PLAIN})
        with unittest.mock.patch.object(shards, "TESTS_DIR", tree.pkg), \
                unittest.mock.patch.object(shards, "conformance_family", return_value=([], [])), \
                unittest.mock.patch.dict(shards.EXCLUSIVE_ATOMS,
                                         {f"{tree.pkg}.test_x.ATest": ""}):
            with self.assertRaisesRegex(shards.InventoryError, "EXCLUSIVE_ATOMS: .* no reason"):
                shards.build_inventory(tree.top)

    def plan(self, seconds: dict, exclusive: dict, **overrides) -> dict:
        return shards.build_plan(_selection(*seconds), _params(**overrides), _timed(seconds),
                                 exclusive=exclusive)

    def test_exclusive_atoms_go_together_on_one_last_shard(self) -> None:
        seconds = {self.A: 3, self.B: 5, self.C: 4, self.D: 2}
        plan = self.plan(seconds, {self.D.key: "why", self.A.key: "why"}, shards=2)
        self.assertEqual(plan["shard_count"], 3)
        self.assertEqual([s["atoms"] for s in plan["shards"]],
                         [[self.B.key], [self.C.key], [self.A.key, self.D.key]])
        self.assertEqual([shards.is_exclusive(s) for s in plan["shards"]], [False, False, True])
        self.assertIs(plan["shards"][2]["exclusive"], True)
        self.assertNotIn("exclusive", plan["shards"][0])
        self.assertEqual(plan["shards"][2]["test_ids"], list(self.A.test_ids + self.D.test_ids))
        self.assertEqual(plan["shards"][2]["estimate_seconds"], 5.0)
        # The parallel shards are planned exactly as if the exclusive atoms
        # were not selected.
        without = self.plan({self.B: 5, self.C: 4}, {}, shards=2)
        self.assertEqual(plan["shards"][:2], without["shards"])

    def test_an_unselected_registered_atom_changes_nothing(self) -> None:
        seconds = {self.A: 3, self.B: 5}
        self.assertEqual(self.plan(seconds, {self.C.key: "why"}), self.plan(seconds, {}))

    def test_a_selection_of_only_exclusive_atoms_is_one_exclusive_shard(self) -> None:
        plan = self.plan({self.A: 3, self.B: 5}, {self.A.key: "why", self.B.key: "why"})
        self.assertEqual(plan["shard_count"], 1)
        self.assertTrue(shards.is_exclusive(plan["shards"][0]))

    def test_a_misplaced_exclusive_shard_is_refused(self) -> None:
        seconds = {self.A: 3, self.B: 5, self.C: 4}
        registry = {self.C.key: "why"}
        plan = self.plan(seconds, registry, shards=2)
        selection = _selection(*seconds)
        cases = {
            "only on the last shard": lambda p: p["shards"][0].update(exclusive=True),
            "must be true": lambda p: p["shards"][2].update(exclusive=1),
            "keys are not exactly": lambda p: p["shards"][0].update(alone=True),
        }
        for problem, tamper in cases.items():
            with self.subTest(problem=problem):
                tampered = json.loads(json.dumps(plan))
                tamper(tampered)
                _redigest(tampered)
                with self.assertRaisesRegex(shards.PlanError, problem):
                    shards.validate_plan(tampered)
        dropped = json.loads(json.dumps(plan))
        del dropped["shards"][2]["exclusive"]
        _redigest(dropped)
        shards.validate_plan(dropped)
        with self.assertRaisesRegex(shards.PlanError, "exclusive shard holds \\[\\]"):
            shards.validate_plan(dropped, selection, exclusive=registry)
        shards.validate_plan(plan, selection, exclusive=registry)
        with self.assertRaisesRegex(shards.PlanError, "exclusive atoms are \\[\\]"):
            shards.validate_plan(plan, selection, exclusive={})


class EquivalencePropertyTest(unittest.TestCase):
    """I1 over 500 seeded random cases of the real inventory: random name
    subsets (modules, classes, methods, conformance), with and without the CI
    placement; random, corrupt or empty timings; ``N`` pinned from 1 to 32 or
    left to random parameters. Checked directly here, not through
    ``validate_plan``."""

    CASES = 500
    SEED = 20260926

    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = shards.build_inventory(fixtures.REPO_ROOT)

    def _names(self, rng) -> list[str]:
        atoms = self.inventory.atoms
        pool = []
        for _ in range(rng.randint(1, 12)):
            atom = rng.choice(atoms)
            pool.append(rng.choice([atom.key, atom.module if atom.family == "controller"
                                    else "conformance", rng.choice(atom.test_ids)]))
        return pool

    def _timings(self, rng, tmp: Path) -> tuple:
        kind = rng.choice(["random", "corrupt", "empty", "missing"])
        path = tmp / f"{kind}.json"
        if kind == "random":
            atoms = {}
            for atom in rng.sample(self.inventory.atoms, rng.randint(0, len(self.inventory.atoms))):
                seconds = rng.choice([0.0, rng.uniform(0, 1), rng.uniform(0, 200), 1e6])
                atoms[atom.key] = {"seconds": seconds, "samples": 1,
                                   "tests": rng.choice([len(atom.test_ids), 1, 50])}
            shards.write_timings(path, _profile(atoms, "random"))
        elif kind == "corrupt":
            path.write_bytes(rng.choice([b"{", b"\xff\xfe", b'{"schema_version": 1}',
                                         b'{"schema_version": 1, "profile": "x", "atoms": '
                                         b'{"a": {"seconds": -1, "samples": 1, "tests": 1}}, '
                                         b'"updated_from": []}']))
        elif kind == "empty":
            shards.write_timings(path, _profile({}, "empty"))
        return ((path, shards.load_timings(path, warn=lambda _: None)),)

    def _parameters(self, rng):
        if rng.random() < 0.7:
            return _params(shards=rng.randint(1, 32))
        return _params(target_shard_seconds=rng.choice([0.001, 1, 60, 180, 1e5]),
                       min_shards=rng.randint(1, 8), max_shards=rng.randint(1, 32))

    def test_every_plan_partitions_its_selection_into_whole_atoms(self) -> None:
        rng = random.Random(self.SEED)
        planned = 0
        with tempfile.TemporaryDirectory() as tmp:
            for case in range(self.CASES):
                names = self._names(rng)
                selection = self.inventory.select(names, ci_placement=rng.random() < 0.3)
                timings = self._timings(rng, Path(tmp))
                parameters = self._parameters(rng)
                with self.subTest(case=case, names=names):
                    if not selection.atoms:
                        with self.assertRaises(shards.PlanError):
                            shards.build_plan(selection, parameters, timings)
                        continue
                    plan = shards.build_plan(selection, parameters, timings)
                    planned += 1
                    self._assert_partition(plan, selection, parameters)
        self.assertGreater(planned, self.CASES * 0.9)

    def _assert_partition(self, plan, selection, parameters) -> None:
        placed = [test_id for shard in plan["shards"] for test_id in shard["test_ids"]]
        self.assertEqual(len(placed), len(set(placed)), "a test is in two shards")
        self.assertEqual(set(placed), set(selection.test_ids))
        self.assertEqual(plan["selected_ids"], list(selection.test_ids))
        self.assertEqual(plan["shard_count"], len(plan["shards"]))
        self.assertTrue(all(shard["test_ids"] for shard in plan["shards"]), "an empty shard")
        if parameters.shards is not None:
            self.assertEqual(plan["shard_count"], min(parameters.shards, len(selection.atoms)))
        else:
            self.assertLessEqual(plan["shard_count"], parameters.max_shards)
        home = {test_id: shard["index"] for shard in plan["shards"]
                for test_id in shard["test_ids"]}
        placed_atoms = [key for shard in plan["shards"] for key in shard["atoms"]]
        self.assertEqual(sorted(placed_atoms), sorted(atom.key for atom in selection.atoms))
        for atom in selection.atoms:
            self.assertEqual(len({home[test_id] for test_id in atom.test_ids}), 1, atom.key)
        canonical = {test_id: i for i, test_id in enumerate(selection.test_ids)}
        for shard in plan["shards"]:
            order = [canonical[test_id] for test_id in shard["test_ids"]]
            self.assertEqual(order, sorted(order))


def _digest_cases(shards_module, repo_root: Path, tmp: Path) -> list[str]:
    """The plan digests of a fixed set of inputs, computed with
    ``shards_module``: the full local and CI selections on the defaults, and
    a named selection on a committed-profile file under ``tmp``."""
    inventory = shards_module.build_inventory(repo_root)
    quiet = lambda _: None
    profile = shards_module.TimingProfile(profile="ci", atoms={
        atom.key: {"seconds": (i * 7919 % 97) / 7, "samples": 1, "tests": len(atom.test_ids)}
        for i, atom in enumerate(inventory.atoms)})
    shards_module.write_timings(tmp / shards_module.CI_TIMINGS, profile)
    cases = [
        (inventory.select(), shards_module.profile_parameters("local", cpu_count=8),
         shards_module.timings_for("ci", tmp / "absent", warn=quiet)),
        (inventory.select(ci_placement=True), shards_module.profile_parameters("ci"),
         shards_module.timings_for("ci", tmp, warn=quiet)),
        (inventory.select(["tests.test_worker", "tests.test_lock", "conformance"]),
         shards_module.profile_parameters("ci", shards=5),
         shards_module.timings_for("ci", tmp, warn=quiet)),
    ]
    return [shards_module.build_plan(selection, parameters, timings, tmp)["plan_digest"]
            for selection, parameters, timings in cases]


class DeterminismTest(unittest.TestCase):
    """I4: the same inputs give the same digest, in this interpreter and in
    fresh ones under different ``PYTHONHASHSEED`` values."""

    def test_the_same_inputs_give_the_same_digest_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = _digest_cases(shards, fixtures.REPO_ROOT, Path(tmp))
            again = _digest_cases(shards, fixtures.REPO_ROOT, Path(tmp))
            self.assertEqual(first, again)
            self.assertEqual(len(set(first)), len(first))
            probe = (f"import sys\nsys.path.insert(0, {str(fixtures.REPO_ROOT)!r})\n"
                     f"sys.argv[1:] = [{tmp!r}]\n"
                     "from tests import test_test_shards as t\n"
                     "from pathlib import Path\n"
                     "print(t._digest_cases(t.shards, t.fixtures.REPO_ROOT, Path(sys.argv[1])))\n")
            for seed in ("0", "4242"):
                with self.subTest(PYTHONHASHSEED=seed):
                    env = dict(os.environ, PYTHONHASHSEED=seed)
                    out = subprocess.run([sys.executable, "-c", probe], env=env,
                                         cwd=fixtures.REPO_ROOT, capture_output=True,
                                         text=True, check=True).stdout
                    self.assertEqual(out.strip(), repr(first))


class BaselineBalanceTest(unittest.TestCase):
    """LPT on the recorded serial baseline (``tests/golden/``, measured at
    CP3) keeps the longest shard within 1.10 × the ideal ``total / N``
    whenever the largest atom is at most ``total / N``."""

    def test_lpt_balances_the_recorded_baseline(self) -> None:
        baseline = shards.load_timings(BASELINE_TIMINGS, warn=self.fail)
        inventory = shards.build_inventory(fixtures.REPO_ROOT)
        known = [atom for atom in inventory.atoms if atom.key in baseline.atoms]
        # Bounded on the frozen baseline's own atoms, so new test classes
        # never fail this test; only removing most of the baseline would.
        self.assertGreater(len(known), 0.9 * len(baseline.atoms))
        selection = inventory.select([atom.key for atom in known])
        estimates = shards.estimate_atoms(selection.atoms, [baseline])
        total, largest = sum(estimates.values()), max(estimates.values())
        checked = 0
        for count in range(2, 33):
            if largest > total / count:
                continue
            with self.subTest(shards=count):
                plan = shards.build_plan(selection, _params(shards=count),
                                         ((BASELINE_TIMINGS, baseline),))
                longest = max(shard["estimate_seconds"] for shard in plan["shards"])
                self.assertLessEqual(longest, 1.10 * total / count)
                checked += 1
        self.assertGreater(checked, 0)



# -- CP4: the executor, its recording rules, leaks and aggregation --------------------------


RECORDING_CASES = {
    "test_teardown_module": """
        import unittest

        def tearDownModule():
            raise AssertionError("a forbidden argv was recorded")

        class SafeTest(unittest.TestCase):
            def test_ok(self): pass
    """,
    "test_setup_class_error": """
        import unittest

        class BrokenTest(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("no fixture today")

            def test_a(self): pass
            def test_b(self): pass
    """,
    "test_subtests": """
        import unittest

        class MixedTest(unittest.TestCase):
            def test_one_subtest_fails(self):
                for value in (1, 2, 3):
                    with self.subTest(value=value):
                        self.assertNotEqual(value, 2)

        class AllPassTest(unittest.TestCase):
            def test_every_subtest_passes(self):
                for value in (1, 2):
                    with self.subTest(value=value):
                        self.assertTrue(value)
    """,
    "test_subtest_skip": """
        import unittest

        class SkipInsideSubTest(unittest.TestCase):
            def test_skips_in_a_subtest(self):
                with self.subTest(case=1):
                    self.skipTest("not here")
    """,
    "test_unexpected_success": """
        import unittest

        class SurpriseTest(unittest.TestCase):
            @unittest.expectedFailure
            def test_passes_anyway(self): pass
    """,
    "test_expected_failure": """
        import unittest

        class KnownTest(unittest.TestCase):
            @unittest.expectedFailure
            def test_fails_as_expected(self): self.fail("known")

            @unittest.skip("not today")
            def test_skipped(self): pass
    """,
    "test_setup_class_skip": """
        import unittest

        class NeedsWheelTest(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("setuptools>=70.1 is missing")

            def test_a(self): pass
            def test_b(self): pass

        class OtherTest(unittest.TestCase):
            def test_runs(self): pass
    """,
    "test_setup_module_skip": """
        import unittest

        def setUpModule():
            raise unittest.SkipTest("no packaging prerequisites")

        class ATest(unittest.TestCase):
            def test_a(self): pass

        class BTest(unittest.TestCase):
            def test_b(self): pass
    """,
}


def _execute(test_case: unittest.TestCase, tree: SyntheticTree, names=(), *, shards_count=1,
             index=0):
    """Plan ``names`` of ``tree`` and execute shard ``index`` in-process."""
    ids, atoms = tree.family()
    inventory = shards.Inventory(test_ids=tuple(ids), atoms=tuple(atoms))
    plan = shards.build_plan(inventory.select([f"{tree.pkg}.{name}" for name in names]),
                             _params(shards=shards_count))
    tmp = tempfile.TemporaryDirectory()
    test_case.addCleanup(tmp.cleanup)
    results = Path(tmp.name)
    record, status = shards.execute_shard(plan, index, results, tree.top, argv=["exec-shard"],
                                          environ={})
    return plan, record, status, results, inventory


def _plain_unittest_passes(name: str) -> bool:
    """What plain ``unittest`` says about ``name``, run on its own."""
    suite = unittest.TestLoader().loadTestsFromName(name)
    result = unittest.TestResult()
    suite.run(result)
    return result.wasSuccessful()


class ExecutorRecordingTest(unittest.TestCase):
    """The recording rules over synthetic modules: every event lands on a
    planned id or a fixture, coverage stays exact, and the shard's verdict
    equals plain ``unittest``'s ``wasSuccessful()`` in both directions."""

    def setUp(self) -> None:
        self.tree = SyntheticTree(self, RECORDING_CASES)
        self.p = self.tree.pkg

    def run_case(self, module: str):
        plan, record, status, results, inventory = _execute(self, self.tree, [module])
        self.assertEqual(status == 0, _plain_unittest_passes(f"{self.p}.{module}"))
        self.assertEqual(record["exit_status"], status)
        self.assertEqual([entry["id"] for entry in record["tests"]], plan["selected_ids"])
        self.assertEqual(shards.load_shard_result(shards.result_path(results, 0)), record)
        result = shards.aggregate(plan, {0: record}, results)
        self.assertEqual((result.not_run, result.violations), ([], []))
        self.assertEqual(result.exit_status, status)
        return plan, record, status, result, inventory

    def outcomes(self, record) -> dict[str, str]:
        return {entry["id"]: entry["outcome"] for entry in record["tests"]}

    def test_a_failing_teardown_module_is_a_fixture_error(self) -> None:
        _, record, status, result, _ = self.run_case("test_teardown_module")
        self.assertEqual(status, 1)
        self.assertEqual(set(self.outcomes(record).values()), {"pass"})
        description = f"tearDownModule ({self.p}.test_teardown_module)"
        self.assertEqual([e["description"] for e in record["fixture_errors"]], [description])
        self.assertIn("a forbidden argv was recorded", record["fixture_errors"][0]["traceback"])
        self.assertEqual(result.shards[0]["verdict"], shards.FAIL)
        self.assertIn(f"### `{description}`", result.summary)
        self.assertIn(f"python3 -m unittest {self.p}.test_teardown_module", result.summary)

    def test_a_setup_class_error_reports_every_test_of_the_class_once(self) -> None:
        _, record, status, result, _ = self.run_case("test_setup_class_error")
        self.assertEqual(status, 1)
        cls = f"{self.p}.test_setup_class_error.BrokenTest"
        self.assertEqual(self.outcomes(record), {f"{cls}.test_a": "error",
                                                 f"{cls}.test_b": "error"})
        for entry in record["tests"]:
            self.assertIn(f"setUpClass ({cls})", entry["detail"])
            self.assertEqual(entry["seconds"], 0)
        self.assertEqual([e["description"] for e in record["fixture_errors"]],
                         [f"setUpClass ({cls})"])
        self.assertEqual(result.shards[0]["verdict"], shards.FAIL)

    def test_subtests_fold_into_one_entry_per_parent(self) -> None:
        _, record, status, _, _ = self.run_case("test_subtests")
        self.assertEqual(status, 1)
        mod = f"{self.p}.test_subtests"
        self.assertEqual(self.outcomes(record), {
            f"{mod}.AllPassTest.test_every_subtest_passes": "pass",
            f"{mod}.MixedTest.test_one_subtest_fails": "fail"})
        self.assertFalse(any("(" in entry["id"] for entry in record["tests"]))
        failing = record["tests"][1]
        self.assertIn(f"{mod}.MixedTest.test_one_subtest_fails (value=2)", failing["detail"])

    def test_a_skip_inside_a_subtest_skips_the_parent(self) -> None:
        _, record, status, _, _ = self.run_case("test_subtest_skip")
        self.assertEqual(status, 0)
        self.assertEqual(list(self.outcomes(record).values()), ["skip"])

    def test_an_unexpected_success_fails_the_shard(self) -> None:
        _, record, status, result, _ = self.run_case("test_unexpected_success")
        self.assertEqual(status, 1)
        self.assertEqual(list(self.outcomes(record).values()), ["unexpected_success"])
        self.assertEqual(result.shards[0]["verdict"], shards.FAIL)

    def test_expected_failures_and_skips_pass(self) -> None:
        _, record, status, _, _ = self.run_case("test_expected_failure")
        self.assertEqual(status, 0)
        self.assertEqual(sorted(self.outcomes(record).values()), ["expected_failure", "skip"])

    def test_a_setup_class_skip_skips_its_tests_and_is_not_merged(self) -> None:
        _, record, status, result, inventory = self.run_case("test_setup_class_skip")
        self.assertEqual(status, 0)
        cls = f"{self.p}.test_setup_class_skip.NeedsWheelTest"
        other = f"{self.p}.test_setup_class_skip.OtherTest"
        self.assertEqual(self.outcomes(record), {f"{cls}.test_a": "skip", f"{cls}.test_b": "skip",
                                                 f"{other}.test_runs": "pass"})
        self.assertIn(f"setUpClass ({cls})", record["tests"][0]["detail"])
        self.assertEqual(record["fixture_skips"], [
            {"description": f"setUpClass ({cls})", "reason": "setuptools>=70.1 is missing"}])
        self.assertEqual(result.shards[0]["verdict"], shards.PASS)
        self.assertIn(f"`setUpClass ({cls})` (shard 0): setuptools>=70.1 is missing "
                      f"(2 planned tests skipped)", result.summary)
        merged = shards.update_timings(shards.DEFAULT_TIMINGS, [record], inventory)
        self.assertEqual(set(merged.atoms), {other})

    def test_a_setup_module_skip_skips_the_whole_module(self) -> None:
        _, record, status, result, inventory = self.run_case("test_setup_module_skip")
        self.assertEqual(status, 0)
        mod = f"{self.p}.test_setup_module_skip"
        self.assertEqual(set(self.outcomes(record).values()), {"skip"})
        self.assertEqual(len(record["tests"]), 2)
        self.assertEqual([e["description"] for e in record["fixture_skips"]],
                         [f"setUpModule ({mod})"])
        self.assertIn("(2 planned tests skipped)", result.summary)
        self.assertEqual(shards.update_timings(shards.DEFAULT_TIMINGS, [record],
                                               inventory).atoms, {})

    def test_a_record_that_disagrees_with_unittest_is_refused_either_way(self) -> None:
        passing = {"tests": [{"id": "m.C.t", "outcome": "pass", "seconds": 0}],
                   "fixture_errors": []}
        failing = {"tests": [{"id": "m.C.t", "outcome": "fail", "seconds": 0}],
                   "fixture_errors": []}
        for record, was_successful in ((passing, False), (failing, True)):
            with self.subTest(record=record["tests"][0]["outcome"]):
                problems: list[str] = []
                self.assertEqual(shards.shard_status(record, was_successful, problems), 2)
                self.assertIn("wasSuccessful()", problems[0])
        self.assertEqual(shards.shard_status(passing, True, []), 0)
        self.assertEqual(shards.shard_status(failing, False, []), 1)
        self.assertEqual(shards.shard_status(passing, None, []), 0)

    def test_the_atoms_are_timed_and_run_their_class_fixtures_once(self) -> None:
        tree = SyntheticTree(self, {"test_counts": """
            import os
            import unittest

            class CountTest(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    os.environ["PKG_SETUP"] = os.environ.get("PKG_SETUP", "") + "s"

                @classmethod
                def tearDownClass(cls):
                    os.environ["PKG_TEARDOWN"] = os.environ.get("PKG_TEARDOWN", "") + "t"

                def test_a(self): pass
                def test_b(self): pass

            class NextTest(unittest.TestCase):
                def test_c(self): pass
        """})
        self.addCleanup(os.environ.pop, f"{tree.pkg}_SETUP", None)
        self.addCleanup(os.environ.pop, f"{tree.pkg}_TEARDOWN", None)
        _, record, status, _, _ = _execute(self, tree)
        self.assertEqual(status, 0)
        self.assertEqual(os.environ[f"{tree.pkg}_SETUP"], "s")
        self.assertEqual(os.environ[f"{tree.pkg}_TEARDOWN"], "t")
        self.assertEqual(set(record["atoms"]), {f"{tree.pkg}.test_counts.CountTest",
                                                f"{tree.pkg}.test_counts.NextTest"})


class ExecutorRefusalTest(unittest.TestCase):
    """The loaded ids must equal the planned ids before anything runs."""

    def setUp(self) -> None:
        self.tree = SyntheticTree(self, {"test_one": PLAIN})

    def plan(self):
        ids, atoms = self.tree.family()
        inventory = shards.Inventory(test_ids=tuple(ids), atoms=tuple(atoms))
        return shards.build_plan(inventory.select(), _params(shards=1))

    def rewrite(self, source: str) -> None:
        (self.tree.start / "test_one.py").write_text(textwrap.dedent(source))
        for name in [n for n in sys.modules if n.startswith(f"{self.tree.pkg}.test_one")]:
            del sys.modules[name]
        # The package keeps the old module as an attribute, which the loader
        # would otherwise fall back to; a fresh shard process has neither.
        vars(sys.modules[self.tree.pkg]).pop("test_one", None)
        importlib.invalidate_caches()

    def execute(self, plan):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        record, status = shards.execute_shard(plan, 0, Path(tmp.name), self.tree.top,
                                              environ={})
        log = shards.log_path(Path(tmp.name), 0).read_text()
        return record, status, log

    def test_a_planned_id_that_no_longer_loads_refuses(self) -> None:
        plan = self.plan()
        self.rewrite("""
            import unittest

            class ATest(unittest.TestCase):
                def test_a(self): pass

            class BTest(unittest.TestCase):
                def test_c(self): pass
        """)
        record, status, log = self.execute(plan)
        self.assertEqual((status, record["exit_status"], record["tests"]), (2, 2, []))
        self.assertIn(f"1 planned ids were not loaded: {self.tree.pkg}.test_one.ATest.test_b", log)
        result = shards.aggregate(plan, {0: record}, Path("/r"))
        self.assertEqual(result.shards[0]["verdict"], shards.REFUSED)
        self.assertEqual(len(result.not_run), 3)
        self.assertEqual(result.exit_status, 2)

    def test_an_import_failure_refuses_naming_the_failed_test(self) -> None:
        plan = self.plan()
        self.rewrite("import no_such_module_for_the_executor\n")
        record, status, log = self.execute(plan)
        self.assertEqual((status, record["tests"]), (2, []))
        self.assertIn("_FailedTest substituted", log)
        self.assertIn("no_such_module_for_the_executor", log)

    def test_a_module_skipping_at_import_refuses(self) -> None:
        plan = self.plan()
        self.rewrite("import unittest\nraise unittest.SkipTest('gone')\n")
        record, status, log = self.execute(plan)
        self.assertEqual((status, record["tests"]), (2, []))
        self.assertIn("REFUSED", log)

    def test_the_loader_is_given_the_planned_order(self) -> None:
        plan = self.plan()
        shard = plan["shards"][0]
        swapped = dict(shard, test_ids=list(reversed(shard["test_ids"])))
        with self.assertRaisesRegex(shards.ShardRefusedError, "not in the planned order"):
            shards.load_shard(swapped, self.tree.top)


def _fake_record(plan, index, outcomes: dict[str, str] | None = None, *, exit_status=None,
                 digest=None, extra=(), attempt=None):
    """A valid record of shard ``index``: version 1 without ``attempt``,
    version 2 of that run attempt with it."""
    ids = plan["shards"][index]["test_ids"]
    outcomes = outcomes or {}
    tests = [{"id": test_id, "outcome": outcomes.get(test_id, "pass"), "seconds": 0.5}
             for test_id in list(ids) + list(extra) if outcomes.get(test_id) != "missing"]
    version = {"schema_version": 1} if attempt is None else {"schema_version": 2,
                                                             "run_attempt": attempt}
    record = {**version, "plan_digest": digest or plan["plan_digest"], "shard": index,
              "argv": ["exec-shard"], "started_at": "2026-09-26T00:00:00Z",
              "ended_at": "2026-09-26T00:00:01Z", "wall_seconds": 1.0, "exit_status": 0,
              "tests": tests, "fixture_errors": [], "fixture_skips": [],
              "atoms": {key: 0.5 for key in plan["shards"][index]["atoms"]},
              "leaked_processes": []}
    passes = shards.record_passes(record)
    record["exit_status"] = exit_status if exit_status is not None else (0 if passes else 1)
    return shards.validate_shard_result(record)


class AggregateTest(unittest.TestCase):
    """Verdicts, the run-time coverage proof (I2), exit statuses and the
    summary, over a two-shard plan of a synthetic tree."""

    def setUp(self) -> None:
        tree = SyntheticTree(self, {"test_one": PLAIN, "test_two": PLAIN})
        ids, atoms = tree.family()
        inventory = shards.Inventory(test_ids=tuple(ids), atoms=tuple(atoms))
        self.plan = shards.build_plan(inventory.select(), _params(shards=2))
        self.first = self.plan["shards"][0]["test_ids"][0]
        self.results = Path("/results/run")

    def run_aggregate(self, records, **kwargs):
        return shards.aggregate(self.plan, records, self.results, **kwargs)

    def test_exact_coverage_passes(self) -> None:
        result = self.run_aggregate({0: _fake_record(self.plan, 0), 1: _fake_record(self.plan, 1)})
        self.assertEqual(result.exit_status, 0)
        self.assertEqual([row["verdict"] for row in result.shards], ["PASS", "PASS"])
        self.assertIn("| 0 | PASS |", result.summary)
        self.assertIn("balance ratio (max / mean shard wall): 1.00", result.summary)

    def test_a_passing_summary_states_the_coverage_check(self) -> None:
        # Functional review F2: the check must be visible when it passes too.
        result = self.run_aggregate({0: _fake_record(self.plan, 0), 1: _fake_record(self.plan, 1)})
        planned = len(self.plan["selected_ids"])
        self.assertEqual(result.coverage, {"planned": planned, "missing": 0, "unplanned": 0,
                                           "duplicate": 0})
        self.assertIn(f"- coverage: exact; all {planned} planned tests ran once; 0 missing, "
                      f"0 unplanned, 0 duplicate\n", result.summary)

    def test_an_inexact_summary_counts_each_kind_of_coverage_violation(self) -> None:
        other = self.plan["shards"][1]["test_ids"][0]
        records = {0: _fake_record(self.plan, 0, {self.first: "missing"},
                                   extra=["x.Y.test_ghost"]),
                   1: _fake_record(self.plan, 1, extra=[other])}
        result = self.run_aggregate(records)
        self.assertEqual(result.coverage, {"planned": len(self.plan["selected_ids"]),
                                           "missing": 1, "unplanned": 1, "duplicate": 1})
        self.assertIn("- coverage: NOT exact; 1 missing, 1 unplanned, 1 duplicate (listed below)",
                      result.summary)
        self.assertNotIn("coverage: exact", result.summary)

    def test_a_failure_is_exit_1_and_the_summary_says_how_to_reproduce_it(self) -> None:
        records = {0: _fake_record(self.plan, 0, {self.first: "fail"}),
                   1: _fake_record(self.plan, 1)}
        records[0]["tests"][0]["detail"] = "\n".join(f"line {n}" for n in range(100))
        result = self.run_aggregate(records)
        self.assertEqual(result.exit_status, 1)
        summary = result.summary
        self.assertIn(f"### `{self.first}`", summary)
        self.assertIn("- shard 0, outcome `fail`", summary)
        self.assertIn(f"- log: `{self.results / 'shard-0.log'}`", summary)
        self.assertIn(f"`python3 -m unittest {self.first}`", summary)
        self.assertIn(f"`python3 tools/run_tests.py --replay {self.results / 'plan.json'} "
                      f"--shard 0`", summary)
        self.assertIn("line 99", summary)
        self.assertIn("line 60", summary)
        self.assertNotIn("line 59\n", summary)

    def test_a_ci_failure_names_the_artifacts_holding_its_log_and_plan(self) -> None:
        # Functional review F3: in CI the log path is inside the runner, so
        # the summary must also say which artifacts to download.
        records = {0: _fake_record(self.plan, 0, {self.first: "fail"}),
                   1: _fake_record(self.plan, 1)}
        line = ("- CI artifacts: the log is in `results-0-attempt-1`; `test-plan` holds "
                "`plan.json` for the replay command")
        self.assertNotIn("CI artifacts", self.run_aggregate(records).summary)
        result = self.run_aggregate(records, artifacts=shards.ArtifactNames(
            plan="test-plan", results_prefix="results-"))
        self.assertIn(f"- log: `{self.results / 'shard-0.log'}`\n{line}\n", result.summary)

    def test_a_missing_id_crashes_the_shard_and_is_not_run(self) -> None:
        records = {0: _fake_record(self.plan, 0, {self.first: "missing"}),
                   1: _fake_record(self.plan, 1)}
        result = self.run_aggregate(records)
        self.assertEqual(result.shards[0]["verdict"], shards.CRASHED)
        self.assertEqual(result.not_run, [(self.first, 0)])
        self.assertIn(f"- `{self.first}` (shard 0)", result.summary)
        self.assertEqual(result.exit_status, 2)

    def test_a_crashed_shard_names_every_id_it_did_not_run(self) -> None:
        result = self.run_aggregate({0: None, 1: _fake_record(self.plan, 1)})
        self.assertEqual(result.shards[0]["verdict"], shards.CRASHED)
        self.assertEqual([test_id for test_id, _ in result.not_run],
                         self.plan["shards"][0]["test_ids"])
        self.assertIn("NOT RUN", result.summary)
        self.assertEqual(result.exit_status, 2)

    def test_duplicate_and_unplanned_ids_are_coverage_violations(self) -> None:
        other = self.plan["shards"][1]["test_ids"][0]
        records = {0: _fake_record(self.plan, 0, extra=[other, "x.Y.test_ghost"]),
                   1: _fake_record(self.plan, 1)}
        result = self.run_aggregate(records)
        self.assertEqual(len(result.violations), 2)
        self.assertIn("unplanned id x.Y.test_ghost", result.summary)
        self.assertIn(f"shard 0 reported unplanned id {other}", result.summary)
        self.assertEqual(result.exit_status, 2)
        duplicated = _fake_record(self.plan, 1)
        duplicated["tests"].append(dict(duplicated["tests"][0]))
        result = self.run_aggregate({0: _fake_record(self.plan, 0), 1: duplicated})
        self.assertEqual(len(result.violations), 1)
        self.assertIn("reported more than once", result.violations[0])
        self.assertEqual(result.exit_status, 2)

    def test_exit_status_disagreeing_with_the_record_is_a_crash(self) -> None:
        records = {0: _fake_record(self.plan, 0, {self.first: "fail"}, exit_status=0),
                   1: _fake_record(self.plan, 1)}
        self.assertEqual(self.run_aggregate(records).shards[0]["verdict"], shards.CRASHED)

    def test_an_interrupted_run_is_130(self) -> None:
        result = self.run_aggregate({0: None, 1: _fake_record(self.plan, 1)}, interrupted={0})
        self.assertEqual(result.shards[0]["verdict"], shards.INTERRUPTED)
        self.assertEqual(result.exit_status, 130)
        self.assertEqual(len(result.not_run), len(self.plan["shards"][0]["test_ids"]))

    def test_a_result_from_another_plan_is_refused(self) -> None:
        records = {0: _fake_record(self.plan, 0, digest="f" * 64), 1: _fake_record(self.plan, 1)}
        with self.assertRaisesRegex(shards.AggregateError, "plan_digest"):
            self.run_aggregate(records)
        with self.assertRaisesRegex(shards.AggregateError, "says it is shard 1"):
            self.run_aggregate({0: _fake_record(self.plan, 1)}, shards=[0])

    def test_a_single_shard_replay_is_judged_on_that_shard_alone(self) -> None:
        result = self.run_aggregate({1: _fake_record(self.plan, 1)}, shards=[1])
        self.assertEqual((result.exit_status, len(result.shards)), (0, 1))

    def test_leaks_are_listed_as_a_warning(self) -> None:
        record = _fake_record(self.plan, 0)
        record["leaked_processes"] = [{"pid": 4242, "argv": ["sleep", "60"], "age_seconds": 3.0}]
        result = self.run_aggregate({0: record, 1: _fake_record(self.plan, 1)})
        self.assertEqual(result.exit_status, 0)
        self.assertIn("pid 4242, age 3.0 s: `sleep 60`", result.summary)

    def test_load_results_names_missing_and_invalid_records(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        results = Path(tmp.name)
        shards.result_path(results, 1).write_text("{not json")
        records, notes = shards.load_results(self.plan, results)
        self.assertEqual(records, {0: None, 1: None})
        self.assertEqual(notes[0], "no result record")
        self.assertIn("cannot read", notes[1])


class AttemptSelectionTest(unittest.TestCase):
    """A re-run of failed jobs counts (Design C): each shard's latest
    attempt decides (I4), the current attempt's job results are checked
    (I4a), superseded records are listed (I5), and ambiguity is refused
    (I7). Over the same two-shard plan as ``AggregateTest``."""

    def setUp(self) -> None:
        tree = SyntheticTree(self, {"test_one": PLAIN, "test_two": PLAIN})
        ids, atoms = tree.family()
        inventory = shards.Inventory(test_ids=tuple(ids), atoms=tuple(atoms))
        self.plan = shards.build_plan(inventory.select(), _params(shards=2))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.results = Path(tmp.name) / "shards"
        self.results.mkdir()
        self.failing = self.plan["shards"][1]["test_ids"][0]

    def put(self, index, attempt, outcomes=None, *, directory=None, record=None):
        """Write shard ``index``'s record of ``attempt`` into its artifact
        directory (``directory`` overrides the name; ``""`` is flat)."""
        if directory is None:
            directory = f"results-{index}-attempt-{attempt}"
        target = self.results / directory
        target.mkdir(parents=True, exist_ok=True)
        if record is None:
            record = _fake_record(self.plan, index, outcomes, attempt=attempt)
        shards.result_path(target, index).write_text(json.dumps(record))
        return target

    def select(self, **kwargs):
        return shards.select_results(self.plan, self.results, **kwargs)

    def run_aggregate(self, *, run_attempt=None, upstream=None):
        records, notes, superseded = self.select(run_attempt=run_attempt)
        return shards.aggregate(self.plan, records, self.results, notes=notes,
                                superseded=superseded, upstream=upstream,
                                artifacts=shards.ArtifactNames(plan="test-plan",
                                                               results_prefix="results-"))

    def replay_36483126576(self) -> None:
        """Run 36483126576's shape: shard 1 failed in attempt 1 and passed
        when "Re-run failed jobs" re-ran it; shard 0 ran once."""
        self.put(0, 1)
        self.put(1, 1, {self.failing: "fail"})
        self.put(1, 2)

    def test_the_run_36483126576_replay_passes_in_any_discovery_order(self) -> None:
        self.replay_36483126576()
        real = shards._candidates
        for order in ("sorted", "reversed"):
            with self.subTest(order=order), unittest.mock.patch.object(
                    shards, "_candidates",
                    lambda d: real(d)[::-1] if order == "reversed" else real(d)):
                result = self.run_aggregate(run_attempt=2, upstream={"plan": "success",
                                                                     "tests": "success"})
                self.assertEqual(result.exit_status, 0, result.summary)
                self.assertEqual([(row["verdict"], row["attempt"]) for row in result.shards],
                                 [("PASS", 1), ("PASS", 2)])
                self.assertEqual(result.failures, [])

    def test_a_superseded_failure_is_listed_not_hidden(self) -> None:
        self.replay_36483126576()
        result = self.run_aggregate(run_attempt=2)
        self.assertEqual(result.superseded, [{"shard": 1, "attempt": 1, "verdict": "FAIL",
                                              "failing": [self.failing], "note": ""}])
        self.assertIn("## Superseded attempts", result.summary)
        self.assertIn("- shard 1, attempt 1: FAIL; artifact `results-1-attempt-1`\n"
                      f"  - `{self.failing}`\n", result.summary)
        self.assertRegex(result.summary, r"\| 1 \| PASS \|[^\n]*\| 2 \|\n")

    def test_a_failing_later_attempt_fails_even_when_the_first_passed(self) -> None:
        self.put(0, 1)
        self.put(1, 1)
        self.put(1, 2, {self.failing: "fail"})
        result = self.run_aggregate(run_attempt=2)
        self.assertEqual(result.exit_status, 1)
        self.assertEqual([f["id"] for f in result.failures], [self.failing])
        self.assertIn("the log is in `results-1-attempt-2`", result.summary)
        self.assertEqual([(e["shard"], e["attempt"], e["verdict"]) for e in result.superseded],
                         [(1, 1, "PASS")])

    def test_two_records_of_one_shard_and_attempt_are_refused(self) -> None:
        self.put(0, 1)
        self.put(1, 1)
        self.put(1, 1, directory="elsewhere")
        with self.assertRaisesRegex(shards.AggregateError, "shard 1 has two records of attempt 1"):
            self.select()

    def test_a_flat_version_1_record_is_attempt_1_and_duplicates_a_per_attempt_one(self) -> None:
        self.put(0, 1)
        self.put(1, None, directory="")
        records, _, _ = self.select()
        self.assertEqual(shards.record_attempt(records[1]), 1)
        self.put(1, 1)
        with self.assertRaisesRegex(shards.AggregateError, "two records of attempt 1"):
            self.select()

    def test_an_attempt_above_the_run_attempt_is_refused(self) -> None:
        self.put(0, 1)
        self.put(1, 3)
        self.assertEqual(shards.record_attempt(self.select(run_attempt=3)[0][1]), 3)
        with self.assertRaisesRegex(shards.AggregateError, "attempt 3, above this run's attempt 2"):
            self.select(run_attempt=2)

    def test_a_directory_disagreeing_with_its_record_is_refused(self) -> None:
        cases = {
            "attempt": dict(index=1, attempt=3, directory="results-1-attempt-2"),
            "shard": dict(index=1, attempt=2, directory="results-0-attempt-2"),
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                for child in self.results.iterdir():
                    shutil.rmtree(child)
                self.put(**case)
                with self.assertRaisesRegex(shards.AggregateError, "but its directory"):
                    self.select()

    def test_a_record_disagreeing_with_its_file_name_is_refused(self) -> None:
        target = self.results / "results-0-attempt-1"
        target.mkdir()
        (target / "shard-0.json").write_text(
            json.dumps(_fake_record(self.plan, 1, attempt=1)))
        with self.assertRaisesRegex(shards.AggregateError, "says it is shard 1"):
            self.select()

    def test_another_plans_digest_is_refused_even_when_superseded(self) -> None:
        self.put(0, 1)
        self.put(1, 1, record=_fake_record(self.plan, 1, attempt=1, digest="f" * 64))
        self.put(1, 2)
        with self.assertRaisesRegex(shards.AggregateError, "plan_digest"):
            self.select(run_attempt=2)

    def test_an_unreadable_record_blocks_its_shard_unless_provably_older(self) -> None:
        self.put(0, 1)
        self.put(1, 2)
        broken = self.results / "results-1-attempt-1"
        broken.mkdir()
        (broken / "shard-1.json").write_text("{")
        records, notes, superseded = self.select(run_attempt=2)
        self.assertEqual(shards.record_attempt(records[1]), 2)
        self.assertEqual([(e["attempt"], e["verdict"]) for e in superseded], [(1, "CRASHED")])
        self.assertIn("cannot read", superseded[0]["note"])
        shutil.rmtree(self.results / "results-1-attempt-2")
        self.put(1, 1, directory="results-1-attempt-0-old")
        broken.rename(self.results / "results-1-attempt-2")
        records, notes, superseded = self.select(run_attempt=2)
        self.assertIsNone(records[1])
        self.assertIn("cannot read", notes[1])
        self.assertEqual([(e["attempt"], e["verdict"]) for e in superseded], [(1, "PASS")])

    def test_the_flat_layout_selects_what_load_results_loads(self) -> None:
        self.put(0, None, directory="")
        self.put(1, None, {self.failing: "fail"}, directory="")
        (self.results / "shard-0").mkdir()
        records, notes, superseded = self.select()
        self.assertEqual((records, notes), shards.load_results(self.plan, self.results))
        self.assertEqual(superseded, [])
        self.assertEqual(self.run_aggregate().exit_status, 1)
        empty, notes, _ = shards.select_results(self.plan, self.results / "absent")
        self.assertEqual((empty, notes), ({0: None, 1: None},
                                          {0: "no result record", 1: "no result record"}))

    # -- I4a: the current attempt's jobs ----------------------------------------------------

    def test_a_full_rerun_whose_shard_left_no_record_fails(self) -> None:
        # Attempt 2 re-ran everything; a tests job failed before uploading,
        # so only attempt 1's passing records are there.
        self.put(0, 1)
        self.put(1, 1)
        result = self.run_aggregate(run_attempt=2, upstream={"plan": "success",
                                                             "tests": "failure"})
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(result.upstream_failures, [("tests", "failure")])
        self.assertTrue(result.summary.startswith(
            "# Test run: ERROR (exit 2)\n\n- job `tests` of this attempt: `failure`. A job of "
            "this attempt did not succeed and left no fresh result; an earlier attempt's "
            "record does not count for it\n"), result.summary)

    def test_a_full_rerun_whose_plan_failed_fails(self) -> None:
        self.put(0, 1)
        self.put(1, 1)
        result = self.run_aggregate(run_attempt=2, upstream={"plan": "failure",
                                                             "tests": "skipped"})
        self.assertEqual(result.exit_status, 2)
        self.assertEqual(result.upstream_failures, [("plan", "failure"), ("tests", "skipped")])
        self.assertIn("- job `plan` of this attempt: `failure`", result.summary)
        self.assertIn("- job `tests` of this attempt: `skipped`", result.summary)

    def test_a_rerun_of_failed_jobs_that_passed_is_green(self) -> None:
        self.replay_36483126576()
        result = self.run_aggregate(run_attempt=2, upstream={"plan": "success",
                                                             "tests": "success"})
        self.assertEqual((result.exit_status, result.upstream_failures), (0, []))
        self.assertNotIn("of this attempt", result.summary)

    def test_failing_records_keep_exit_1_and_still_name_the_job(self) -> None:
        self.put(0, 1)
        self.put(1, 1, {self.failing: "fail"})
        result = self.run_aggregate(run_attempt=1, upstream={"plan": "success",
                                                             "tests": "failure"})
        self.assertEqual(result.exit_status, 1)
        self.assertIn("- job `tests` of this attempt: `failure`", result.summary)


class EnvironmentShapingTest(unittest.TestCase):
    BASE = {"HOME": "/home/op", "PATH": "/usr/bin", "PYTHONPATH": "/somewhere",
            "WORKFLOW_CONTROLLER_HOME": "/wch", "TMPDIR": "/tmp"}

    def test_each_shard_gets_its_own_tmp_state_and_marker_and_nothing_else(self) -> None:
        env = shards.shard_environment(self.BASE, Path("/r"), "run1", 3)
        self.assertEqual(env, {**self.BASE, "TMPDIR": "/r/shard-3/tmp",
                               "XDG_STATE_HOME": "/r/shard-3/state",
                               "WORKFLOW_CONTROLLER_TEST_SHARD": "run1/3"})

    def test_the_pip_cache_is_shared_unless_isolated(self) -> None:
        inherited = shards.shard_environment({**self.BASE, "PIP_CACHE_DIR": "/pc"}, Path("/r"),
                                             "run1", 0)
        self.assertEqual(inherited["PIP_CACHE_DIR"], "/pc")
        isolated = shards.shard_environment(self.BASE, Path("/r"), "run1", 2,
                                            isolated_pip_cache=True)
        self.assertEqual(isolated["PIP_CACHE_DIR"], "/r/pip-cache-2")


class LeakScanTest(unittest.TestCase):
    def test_a_marked_process_is_found_and_its_group_killed(self) -> None:
        marker = f"leak-test-{uuid.uuid4().hex}/0"
        env = {**os.environ, shards.SHARD_MARKER_ENV: marker}
        process = subprocess.Popen(["sleep", "300"], env=env, start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 10
        found = []
        while not found and time.monotonic() < deadline:
            found = shards.scan_marked_processes(marker)
        self.assertEqual([(leak["pid"], leak["argv"]) for leak in found],
                         [(process.pid, ["sleep", "300"])])
        self.assertEqual(shards.scan_marked_processes(marker + "0"), [])
        parent = marker.rsplit("/", 1)[0]
        self.assertEqual([leak["pid"] for leak in shards.scan_marked_processes(parent)],
                         [process.pid])
        self.assertEqual(shards.scan_marked_processes(parent[:-1]), [])
        nested = f"{marker}/123"
        child = subprocess.Popen(["sleep", "300"], env={**env, shards.SHARD_MARKER_ENV: nested},
                                 start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        deadline = time.monotonic() + 10
        while len(found) < 2 and time.monotonic() < deadline:
            found = shards.scan_marked_processes(marker)
        self.assertEqual(sorted(leak["pid"] for leak in found), sorted([process.pid, child.pid]))
        self.assertEqual([leak["pid"] for leak in shards.scan_marked_processes(nested)],
                         [child.pid])
        shards.kill_processes(found)
        self.assertEqual(process.wait(timeout=10), -9)
        self.assertEqual(shards.scan_marked_processes(marker), [])


class CommittedTimingsTest(unittest.TestCase):
    """The committed profile is advisory (I3): it must parse and name only
    existing atoms, but its test counts and coverage may drift until the
    next reviewed refresh without failing anything."""

    def test_the_committed_profile_is_valid_and_names_only_existing_atoms(self) -> None:
        path = fixtures.REPO_ROOT / shards.CI_TIMINGS
        profile = shards.parse_timings(path.read_bytes())
        inventory = shards.build_inventory(fixtures.REPO_ROOT)
        keys = {atom.key for atom in inventory.atoms}
        self.assertEqual(sorted(set(profile.atoms) - keys), [])

    def test_the_committed_profile_is_measured_on_ci(self) -> None:
        # Functional review F1: a local-machine seed underestimated CI by
        # 1.6x overall (2.6x for the acceptance matrix), so the CI plan put
        # the matrix on a shard with 106 s of other work.
        profile = shards.parse_timings((fixtures.REPO_ROOT / shards.CI_TIMINGS).read_bytes())
        self.assertEqual(profile.profile, shards.CI)
        self.assertTrue(profile.updated_from)

    def test_a_stale_test_count_still_plans_the_full_inventory(self) -> None:
        profile = shards.parse_timings((fixtures.REPO_ROOT / shards.CI_TIMINGS).read_bytes())
        inventory = shards.build_inventory(fixtures.REPO_ROOT)
        key = next(atom.key for atom in inventory.atoms
                   if atom.key in profile.atoms and len(atom.test_ids) > 1)
        atoms = {k: dict(v) for k, v in profile.atoms.items()}
        atoms[key]["tests"] += 1
        stale = shards.TimingProfile(profile.profile, atoms, profile.updated_from, profile.sha256)
        selection = inventory.select([atom.key for atom in inventory.atoms])
        estimate = shards.estimate_atoms(selection.atoms, [stale])[key]
        self.assertAlmostEqual(estimate, profile.atoms[key]["seconds"] / atoms[key]["tests"]
                               * profile.atoms[key]["tests"])
        plan = shards.build_plan(selection, _params(), ((shards.CI_TIMINGS, stale),))
        self.assertIs(shards.validate_plan(plan, selection), plan)


if __name__ == "__main__":
    unittest.main()
