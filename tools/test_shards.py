#!/usr/bin/env python3
"""The Controller's test inventory, shared by the local runner and CI. Stdlib only.

One deterministic inventory, with two families whose id syntaxes never
collide:

- ``controller``: every test ``unittest.TestLoader().discover("tests",
  top_level_dir=<repo>)`` loads, identified by ``TestCase.id()`` and in
  exactly ``discover``'s order. A discovery error of any kind (an import
  failure, a ``_FailedTest``, an ``_ErrorHolder``, a module that raises
  ``SkipTest`` at import, a duplicate id) refuses the whole inventory; it
  is never planned around;
- ``conformance``: one id per frozen Workflow suite, ``conformance:<file>``,
  read from the managed ``.github/workflows/workflow-conformance.yml``'s own
  ``run: python3 <file>`` lines, in that file's order.

An *atom* is the unit of placement and is never split: a ``TestCase`` class
keyed ``<loading module>.<ClassName>``, the whole module when the module
defines ``setUpModule`` or ``tearDownModule``, or one conformance suite. The
canonical order is ``discover``'s order followed by the conformance suites,
and every atom's ids are contiguous in it.

A *selection* is set semantics over canonical ids: no names selects
everything; each name is a unittest dotted name (prefix match over
controller ids), ``conformance`` or ``conformance:<file>``; a name that
matches nothing refuses. ``CI_PLACEMENT`` moves a few modules out of the CI
shard matrix, and ``ci_partition`` proves the inventory is partitioned
exactly into CI shards, ``package`` and ``excluded``.

Nothing in ``controller/`` imports this file.
"""

from __future__ import annotations

import re
import sys
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = "tests"
SCRIPTS_DIR = "scripts"
CONFORMANCE_WORKFLOW = Path(".github") / "workflows" / "workflow-conformance.yml"

CONTROLLER = "controller"
CONFORMANCE = "conformance"
CONFORMANCE_PREFIX = f"{CONFORMANCE}:"

#: Atom kinds.
CLASS_ATOM = "class"
MODULE_ATOM = "module"
SUITE_ATOM = "suite"

#: A module defining either name runs a module-level fixture, so the whole
#: module is one atom: splitting it would change what the fixture asserts.
MODULE_FIXTURES = ("setUpModule", "tearDownModule")

#: The managed conformance workflow's own step command, the same pattern
#: ``tests.test_ci_workflows`` checks.
CONFORMANCE_RUN_RE = re.compile(r"(?m)^\s*run: python3 (\S+)\s*$")

#: Tests unittest substitutes for something it could not load. Any of them
#: in the loaded suite refuses the inventory.
SUBSTITUTE_TEST_TYPES = frozenset({"_FailedTest", "ModuleImportFailure", "_ErrorHolder",
                                   "ModuleSkipped"})

#: CI placement, keyed by module: where a module runs instead of the CI shard
#: matrix, and why. Local full runs keep every module, as ``discover`` does.
PACKAGE = "package"
EXCLUDED = "excluded"
SHARDS = "shards"
CI_PLACEMENT = {
    "tests.test_packaged_runtime": (
        PACKAGE, "needs the wheel the package job builds, and runs there under "
                 "CONTROLLER_REQUIRE_PACKAGING_TESTS=1"),
    "tests.test_integration_disposable_repo": (
        EXCLUDED, "needs the live claude binary, network access and real spend "
                  "(CONTROLLER_LIVE_WORKER=1)"),
}


class InventoryError(Exception):
    """The inventory cannot be built; the message names every cause."""


class SelectionError(Exception):
    """A selection name matched nothing, or was malformed."""


@dataclass(frozen=True)
class Atom:
    key: str
    family: str
    kind: str
    module: str
    test_ids: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    names: tuple[str, ...]
    test_ids: tuple[str, ...]
    atoms: tuple[Atom, ...]


@dataclass(frozen=True)
class Inventory:
    test_ids: tuple[str, ...]
    atoms: tuple[Atom, ...]

    def select(self, names: tuple[str, ...] | list[str] = (), *,
               ci_placement: bool = False) -> Selection:
        """The selection ``names`` resolves to, in canonical order.

        With ``ci_placement`` the modules ``CI_PLACEMENT`` moves out of the
        shard matrix are dropped after resolving, so a name inside one of
        them still resolves (and selects nothing there).
        """
        names = tuple(names)
        if not names:
            chosen = set(self.test_ids)
        else:
            chosen = set()
            unmatched = []
            for name in names:
                matched = _resolve(name, self.test_ids)
                if not matched:
                    unmatched.append(name)
                chosen.update(matched)
            if unmatched:
                raise SelectionError("no test matches " + ", ".join(map(repr, unmatched)))
        atoms = []
        for atom in self.atoms:
            if ci_placement and atom.module in CI_PLACEMENT:
                continue
            ids = tuple(test_id for test_id in atom.test_ids if test_id in chosen)
            if ids:
                atoms.append(atom if ids == atom.test_ids else replace(atom, test_ids=ids))
        return Selection(names=names,
                         test_ids=tuple(test_id for atom in atoms for test_id in atom.test_ids),
                         atoms=tuple(atoms))


def _resolve(name: str, test_ids: tuple[str, ...]) -> list[str]:
    if name == CONFORMANCE:
        return [test_id for test_id in test_ids if test_id.startswith(CONFORMANCE_PREFIX)]
    if name.startswith(CONFORMANCE_PREFIX):
        return [test_id for test_id in test_ids if test_id == name]
    if not name or ":" in name:
        return []
    prefix = name + "."
    return [test_id for test_id in test_ids
            if not test_id.startswith(CONFORMANCE_PREFIX)
            and (test_id == name or test_id.startswith(prefix))]


class _ModuleTaggingLoader(unittest.TestLoader):
    """``TestLoader`` whose per-module suites remember their module, so a
    class is keyed by the module that loaded it, not the one defining it."""

    def loadTestsFromModule(self, module, *args, **kwargs):  # noqa: N802 - unittest's name
        suite = super().loadTestsFromModule(module, *args, **kwargs)
        suite._test_shards_module = module
        return suite


def _flatten(suite) -> list[unittest.TestCase]:
    tests = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(_flatten(item))
        else:
            tests.append(item)
    return tests


def controller_family(start_dir: Path, top_level_dir: Path) -> tuple[list[str], list[Atom]]:
    """Discover ``start_dir`` exactly as ``unittest discover`` does and return
    its ids and atoms in discover's order, or raise ``InventoryError``."""
    loader = _ModuleTaggingLoader()
    suite = loader.discover(str(start_dir), top_level_dir=str(top_level_dir))
    problems = [f"discovery error: {(error.strip().splitlines() or [''])[-1]}"
                for error in loader.errors]
    ids: list[str] = []
    atoms: list[Atom] = []
    seen: set[str] = set()
    closed_atoms: set[str] = set()
    for module_suite in suite:
        module = getattr(module_suite, "_test_shards_module", None)
        for test in _flatten(module_suite):
            kind_name = type(test).__name__
            if kind_name in SUBSTITUTE_TEST_TYPES or type(test).__module__ == "unittest.loader":
                problems.append(f"{kind_name} substituted for {test.id()}")
                continue
            if module is None or not isinstance(test, unittest.TestCase):
                problems.append(f"{test.id()} was not loaded from a module as a TestCase")
                continue
            test_id = test.id()
            if test_id in seen:
                problems.append(f"duplicate test id {test_id}")
                continue
            seen.add(test_id)
            ids.append(test_id)
            if getattr(module, type(test).__name__, None) is not type(test):
                problems.append(f"{test_id}: its class is not bound as "
                                f"{module.__name__}.{type(test).__name__}")
                continue
            if any(callable(getattr(module, name, None)) for name in MODULE_FIXTURES):
                key, kind = module.__name__, MODULE_ATOM
            else:
                key, kind = f"{module.__name__}.{type(test).__name__}", CLASS_ATOM
            if atoms and atoms[-1].key == key:
                atoms[-1] = replace(atoms[-1], test_ids=atoms[-1].test_ids + (test_id,))
            elif key in closed_atoms:
                problems.append(f"atom {key} is not contiguous in discover's order at {test_id}")
            else:
                if atoms:
                    closed_atoms.add(atoms[-1].key)
                atoms.append(Atom(key=key, family=CONTROLLER, kind=kind, module=module.__name__,
                                  test_ids=(test_id,)))
    if problems:
        raise InventoryError("the controller inventory is refused:\n  " + "\n  ".join(problems))
    return ids, atoms


def conformance_suites(workflow: Path, scripts_dir: Path) -> list[str]:
    """The managed workflow's ``run: python3 <file>`` suites, in its order."""
    try:
        text = workflow.read_text(encoding="utf-8")
    except OSError as exc:
        raise InventoryError(f"cannot read the managed conformance workflow {workflow}: {exc}")
    suites = CONFORMANCE_RUN_RE.findall(text)
    problems = []
    if not suites:
        problems.append(f"{workflow} declares no 'run: python3 <file>' suite")
    problems += [f"duplicate conformance suite {suite}"
                 for suite in sorted({s for s in suites if suites.count(s) > 1})]
    problems += [f"conformance suite {suite} is not a file in {scripts_dir}"
                 for suite in suites if "/" in suite or not (scripts_dir / suite).is_file()]
    if problems:
        raise InventoryError("the conformance inventory is refused:\n  " + "\n  ".join(problems))
    return suites


def conformance_family(workflow: Path, scripts_dir: Path) -> tuple[list[str], list[Atom]]:
    ids = [CONFORMANCE_PREFIX + suite for suite in conformance_suites(workflow, scripts_dir)]
    atoms = [Atom(key=test_id, family=CONFORMANCE, kind=SUITE_ATOM, module=CONFORMANCE,
                  test_ids=(test_id,)) for test_id in ids]
    return ids, atoms


def build_inventory(repo_root: Path = REPO_ROOT) -> Inventory:
    """The full inventory: the controller family, then the conformance family."""
    repo_root = Path(repo_root).resolve()
    controller_ids, controller_atoms = controller_family(repo_root / TESTS_DIR, repo_root)
    conformance_ids, conformance_atoms = conformance_family(repo_root / CONFORMANCE_WORKFLOW,
                                                            repo_root / SCRIPTS_DIR)
    return Inventory(test_ids=tuple(controller_ids + conformance_ids),
                     atoms=tuple(controller_atoms + conformance_atoms))


def ci_partition(inventory: Inventory) -> dict[str, tuple[str, ...]]:
    """The inventory's ids split into ``shards``, ``package`` and
    ``excluded``, each in canonical order. A placed module the inventory does
    not contain refuses, so a stale placement cannot go unnoticed."""
    loaded_modules = {atom.module for atom in inventory.atoms}
    stale = sorted(module for module in CI_PLACEMENT if module not in loaded_modules)
    if stale:
        raise InventoryError("CI_PLACEMENT names modules the inventory does not contain: "
                             + ", ".join(stale))
    partition: dict[str, list[str]] = {SHARDS: [], PACKAGE: [], EXCLUDED: []}
    for atom in inventory.atoms:
        where = CI_PLACEMENT.get(atom.module, (SHARDS, ""))[0]
        partition[where].extend(atom.test_ids)
    return {where: tuple(ids) for where, ids in partition.items()}
