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

*Timing data* is advisory: it decides where an atom runs, never whether it
runs. Each shard writes a result record (``validate_shard_result`` is its
schema); ``update_timings`` folds the records' passing, complete atoms into a
timing profile with an EWMA; ``load_timings`` falls back to the defaults,
with one warning, on any missing, unreadable or invalid file; and
``estimate_atoms`` gives every selected atom an estimate, from history or
from a per-test mean.

*Planning* (``build_plan``) turns a selection and its estimates into
``clamp(ceil(total / max(target, largest atom)), min, max)`` shards, never
more than there are atoms, and places whole atoms on them by deterministic
longest-processing-time-first assignment, each shard in canonical order.
Atoms registered in ``EXCLUSIVE_ATOMS`` are kept out of that assignment and
placed together, in canonical order, on one final ``exclusive`` shard, which
the local runner starts only once every other shard has finished. The
plan carries a ``plan_digest`` over its canonical JSON, so every process
given the same inputs computes a byte-identical plan, and the planner checks
that the plan partitions the selection exactly before it returns it.

*Execution* (``execute_shard``) runs one shard of a plan in the current
process: it loads each atom by name, refuses (exit 2) before running anything
unless the loaded ids equal the planned ids exactly, runs the controller atoms
through ``unittest``'s own text runner with a recording result that keys every
event to a planned id or to a fixture, runs each conformance suite as the
managed workflow does, and writes ``shard-<i>.json``. ``aggregate`` turns a
plan and its shards' records into verdicts, the run-time coverage proof (I2),
an exit status and a summary with reproduction commands.

Nothing in ``controller/`` imports this file.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections import deque
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Callable, Iterable, Mapping

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


#: Atoms that must run alone, keyed by atom, each with the reason and its
#: evidence. An entry is admissible only for a demonstrated need that cannot
#: be fixed in the test (decision D5); a timing flake is fixed at its cause
#: instead. ``build_inventory`` refuses an entry that names no atom or gives
#: no reason, and every run summary prints the registry's size.
EXCLUSIVE_ATOMS: dict[str, str] = {}

#: Result records and timing files.
RESULT_SCHEMA_VERSION = 1
TIMINGS_SCHEMA_VERSION = 1
OUTCOMES = frozenset({"pass", "fail", "error", "skip", "expected_failure",
                      "unexpected_success"})
#: Outcomes that are not failures, exactly as in ``unittest``.
PASSING_OUTCOMES = frozenset({"pass", "skip", "expected_failure"})

#: The committed CI profile, the only timing input CI planning reads.
CI_TIMINGS = Path("tools") / "test_timings.json"
#: The untracked local profile, under ``$XDG_CACHE_HOME`` (else ``~/.cache``).
LOCAL_TIMINGS_DIR = "workflow-controller-tests"
LOCAL_TIMINGS_FILE = "timings-local.json"

#: Estimates for atoms without history: the base commit's serial mean per
#: test (475 s / 1887 tests), and a flat figure per conformance suite.
DEFAULT_SECONDS_PER_TEST = 0.25
DEFAULT_CONFORMANCE_SECONDS = 30.0
#: Weight of a new observation in the EWMA.
EWMA_WEIGHT = 0.5
#: How many contributing plan digests a profile remembers, newest last, so
#: the local profile, updated after every run, does not grow without bound.
MAX_UPDATED_FROM = 20

#: Plans. The parameters per profile are justified in the plan's
#: "Adaptive shard planning" section: the local target keeps the measured
#: 8-shard win, the local maximum is one shard per CPU, and the CI target is
#: about the unavoidable conformance floor.
PLAN_SCHEMA_VERSION = 1
LOCAL = "local"
CI = "ci"
LOCAL_TARGET_SHARD_SECONDS = 60
LOCAL_MAX_SHARDS = 8
CI_TARGET_SHARD_SECONDS = 180
CI_MAX_SHARDS = 16
DEFAULT_MIN_SHARDS = 2
DEFAULTS_SOURCE = "defaults"
LOCAL_PROFILE_SOURCE = "local-profile"
_PLAN_KEYS = frozenset({"schema_version", "profile", "selection_names", "selected_ids",
                        "parameters", "shard_count", "shards", "timing_source", "plan_digest"})
_SHARD_KEYS = frozenset({"index", "atoms", "test_ids", "estimate_seconds"})
#: The optional key, always ``true`` when present, marking the exclusive shard.
EXCLUSIVE = "exclusive"

_HOLDER_RE = re.compile(r"^(\w+) \(([\w.]+)\)$")


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
    inventory = Inventory(test_ids=tuple(controller_ids + conformance_ids),
                          atoms=tuple(controller_atoms + conformance_atoms))
    problems = exclusive_atoms_problems(inventory)
    if problems:
        raise InventoryError("EXCLUSIVE_ATOMS: " + "; ".join(problems))
    return inventory


def exclusive_atoms_problems(inventory: Inventory,
                             registry: Mapping[str, str] | None = None) -> list[str]:
    """What is wrong with ``registry`` (default ``EXCLUSIVE_ATOMS``) against
    ``inventory``: an entry naming no atom, or with an empty reason."""
    registry = EXCLUSIVE_ATOMS if registry is None else registry
    keys = {atom.key for atom in inventory.atoms}
    problems = []
    for key, reason in sorted(registry.items()):
        if key not in keys:
            problems.append(f"{key!r} names no atom of the inventory")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{key!r} has no reason")
    return problems


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


# -- result records -------------------------------------------------------------------------


class ResultRecordError(Exception):
    """A shard result record does not match its schema; the message names every problem."""


_RESULT_KEYS = ("schema_version", "plan_digest", "shard", "argv", "started_at", "ended_at",
                "wall_seconds", "exit_status", "tests", "fixture_errors", "fixture_skips",
                "atoms", "leaked_processes")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_seconds(value) -> bool:
    """A finite, non-negative number of seconds."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _is_timestamp(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _entries(record: Mapping, key: str, required: dict[str, Callable],
             optional: dict[str, Callable] | None = None, *, problems: list[str]) -> None:
    """Check ``record[key]`` is a list of objects with exactly ``required``'s
    keys (plus any of ``optional``'s), each value passing its predicate."""
    optional = optional or {}
    value = record.get(key)
    if not isinstance(value, list):
        problems.append(f"{key} is not a list")
        return
    for index, entry in enumerate(value):
        where = f"{key}[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} is not an object")
            continue
        missing = sorted(set(required) - set(entry))
        unknown = sorted(set(entry) - set(required) - set(optional))
        if missing:
            problems.append(f"{where} lacks {', '.join(missing)}")
        if unknown:
            problems.append(f"{where} has unknown keys {', '.join(unknown)}")
        for name, check in {**required, **optional}.items():
            if name in entry and not check(entry[name]):
                problems.append(f"{where}.{name} is invalid: {entry[name]!r}")


def validate_shard_result(record) -> dict:
    """Return ``record`` if it is a well-formed ``shard-<i>.json``, else
    raise ``ResultRecordError`` naming every problem.

    This checks the shape only. Whether the reported ids cover the plan is
    the aggregate's coverage check, so duplicate or unplanned ids are left
    for it to name.
    """
    if not isinstance(record, dict):
        raise ResultRecordError("a shard result record is not a JSON object")
    problems = []
    missing = [key for key in _RESULT_KEYS if key not in record]
    unknown = sorted(set(record) - set(_RESULT_KEYS))
    if missing:
        problems.append("missing keys " + ", ".join(missing))
    if unknown:
        problems.append("unknown keys " + ", ".join(unknown))
    is_str = lambda value: isinstance(value, str)  # noqa: E731
    checks = {
        "schema_version": lambda value: value == RESULT_SCHEMA_VERSION,
        "plan_digest": lambda value: is_str(value) and bool(value),
        "shard": lambda value: _is_int(value) and value >= 0,
        "argv": lambda value: isinstance(value, list) and all(map(is_str, value)),
        "started_at": _is_timestamp,
        "ended_at": _is_timestamp,
        "wall_seconds": _is_seconds,
        "exit_status": _is_int,
        "atoms": lambda value: (isinstance(value, dict) and all(map(is_str, value))
                                and all(map(_is_seconds, value.values()))),
    }
    for key, check in checks.items():
        if key in record and not check(record[key]):
            problems.append(f"{key} is invalid: {record[key]!r}")
    _entries(record, "tests",
             {"id": lambda value: is_str(value) and bool(value),
              "outcome": lambda value: value in OUTCOMES, "seconds": _is_seconds},
             {"detail": is_str}, problems=problems)
    _entries(record, "fixture_errors", {"description": is_str, "traceback": is_str},
             problems=problems)
    _entries(record, "fixture_skips", {"description": is_str, "reason": is_str},
             problems=problems)
    _entries(record, "leaked_processes",
             {"pid": lambda value: _is_int(value) and value > 0,
              "argv": lambda value: isinstance(value, list) and all(map(is_str, value)),
              "age_seconds": _is_seconds}, problems=problems)
    if problems:
        raise ResultRecordError("the shard result record is invalid:\n  "
                                + "\n  ".join(problems))
    return record


def load_shard_result(path: Path) -> dict:
    """Read and validate one ``shard-<i>.json``."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResultRecordError(f"cannot read the shard result record {path}: {exc}")
    try:
        return validate_shard_result(record)
    except ResultRecordError as exc:
        raise ResultRecordError(f"{path}: {exc}")


def record_passes(record: Mapping) -> bool:
    """The verdict a record's own entries give: every test outcome is a
    non-failure and no fixture errored. Fixture skips never affect it."""
    return (all(entry["outcome"] in PASSING_OUTCOMES for entry in record["tests"])
            and not record["fixture_errors"])


def holder_target(description: str) -> str | None:
    """The dotted module or class an ``_ErrorHolder`` description names, for
    example ``tests.test_release_txn`` for ``tearDownModule
    (tests.test_release_txn)``, or ``None`` if it does not parse."""
    match = _HOLDER_RE.match(description)
    return match.group(2) if match else None


def _touches(target: str, atom: Atom) -> bool:
    """Whether a fixture on ``target`` (a module or a class) covers any of
    ``atom``'s tests: the same name, a module fixture over a class atom, or a
    class fixture inside a module atom."""
    return (target == atom.key or atom.key.startswith(target + ".")
            or target.startswith(atom.key + "."))


# -- timing profiles ------------------------------------------------------------------------


class TimingError(Exception):
    """A timing file cannot be read or does not match its schema."""


@dataclass(frozen=True)
class TimingProfile:
    """A timing file's content. ``atoms`` maps an atom key to ``{"seconds",
    "samples", "tests"}``. ``sha256`` is the file's own digest, or ``None``
    for the built-in defaults (no file, or a file that fell back)."""

    profile: str
    atoms: Mapping[str, Mapping]
    updated_from: tuple[str, ...] = ()
    sha256: str | None = None


DEFAULT_TIMINGS = TimingProfile(profile="defaults", atoms={})


def local_timings_path(environ: Mapping[str, str] = os.environ) -> Path:
    """``$XDG_CACHE_HOME/workflow-controller-tests/timings-local.json``,
    falling back to ``~/.cache`` when the variable is unset or empty."""
    cache = environ.get("XDG_CACHE_HOME") or str(Path(environ.get("HOME") or Path.home())
                                                 / ".cache")
    return Path(cache) / LOCAL_TIMINGS_DIR / LOCAL_TIMINGS_FILE


def parse_timings(data: bytes) -> TimingProfile:
    """Parse and validate a timing file's bytes, or raise ``TimingError``."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TimingError(f"not JSON: {exc}")
    if not isinstance(doc, dict):
        raise TimingError("not a JSON object")
    problems = []
    if set(doc) != {"schema_version", "profile", "atoms", "updated_from"}:
        problems.append("keys are not exactly schema_version, profile, atoms, updated_from")
    if doc.get("schema_version") != TIMINGS_SCHEMA_VERSION:
        problems.append(f"schema_version is not {TIMINGS_SCHEMA_VERSION}")
    if not isinstance(doc.get("profile"), str) or not doc.get("profile"):
        problems.append("profile is not a non-empty string")
    updated_from = doc.get("updated_from")
    if not isinstance(updated_from, list) or not all(isinstance(d, str) for d in updated_from):
        problems.append("updated_from is not a list of strings")
    atoms = doc.get("atoms")
    if not isinstance(atoms, dict):
        problems.append("atoms is not an object")
        atoms = {}
    for key, entry in atoms.items():
        if (not isinstance(entry, dict) or set(entry) != {"seconds", "samples", "tests"}
                or not _is_seconds(entry["seconds"])
                or not (_is_int(entry["samples"]) and entry["samples"] >= 1)
                or not (_is_int(entry["tests"]) and entry["tests"] >= 1)):
            problems.append(f"atom {key!r} is not {{seconds >= 0 finite, samples >= 1, "
                            f"tests >= 1}}: {entry!r}")
    if problems:
        raise TimingError("; ".join(problems))
    return TimingProfile(profile=doc["profile"], atoms=atoms, updated_from=tuple(updated_from),
                         sha256=hashlib.sha256(data).hexdigest())


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def load_timings(path: Path, *, warn: Callable[[str], None] = _warn) -> TimingProfile:
    """The timing profile at ``path``, or ``DEFAULT_TIMINGS`` with exactly one
    warning line naming the file when it is missing, unreadable or invalid.
    Never raises: timing data only ever affects balance (I3)."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        warn(f"warning: timing file {path} is unusable ({exc.strerror or exc}); "
             f"using the default estimates")
        return DEFAULT_TIMINGS
    try:
        return parse_timings(data)
    except TimingError as exc:
        warn(f"warning: timing file {path} is unusable ({exc}); using the default estimates")
        return DEFAULT_TIMINGS


def timings_document(profile: TimingProfile) -> dict:
    return {"schema_version": TIMINGS_SCHEMA_VERSION, "profile": profile.profile,
            "atoms": {key: dict(profile.atoms[key]) for key in sorted(profile.atoms)},
            "updated_from": list(profile.updated_from)}


def write_timings(path: Path, profile: TimingProfile) -> None:
    """Write ``profile`` to ``path`` atomically: an adjacent temporary file,
    ``fsync``, then ``os.replace``. Two concurrent writers can lose one
    update, never tear the file."""
    write_json_atomic(path, timings_document(profile))


def write_json_atomic(path: Path, document) -> None:
    """Write ``document`` as sorted, indented JSON to ``path``: an adjacent
    temporary file, ``fsync``, then ``os.replace``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(document, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _mergeable_atoms(record: Mapping, atoms_by_key: Mapping[str, Atom]) -> dict[str, float]:
    """The atoms of ``record`` whose duration is representative: every one
    of the atom's tests has exactly one passing entry (so a failed, errored,
    interrupted or partially selected atom is left out), and no fixture
    error or fixture skip touches it. A fixture holder that cannot be
    attributed withholds the whole record."""
    targets = []
    for entry in list(record["fixture_errors"]) + list(record["fixture_skips"]):
        target = holder_target(entry["description"])
        if target is None:
            return {}
        targets.append(target)
    outcomes: dict[str, list[str]] = {}
    for entry in record["tests"]:
        outcomes.setdefault(entry["id"], []).append(entry["outcome"])
    mergeable = {}
    for key, seconds in record["atoms"].items():
        atom = atoms_by_key.get(key)
        if atom is None:
            continue
        if not all(len(outcomes.get(test_id, ())) == 1
                   and outcomes[test_id][0] in PASSING_OUTCOMES for test_id in atom.test_ids):
            continue
        if any(_touches(target, atom) for target in targets):
            continue
        mergeable[key] = float(seconds)
    return mergeable


def update_timings(profile: TimingProfile, records: Iterable[Mapping], inventory: Inventory,
                   *, profile_name: str | None = None) -> TimingProfile:
    """Fold ``records`` (validated shard result records, oldest first) into
    ``profile`` and return the result.

    - A representative atom (see ``_mergeable_atoms``) moves its seconds by
      ``EWMA_WEIGHT`` towards the observation, and its ``samples`` grows;
    - an atom new to the profile, or whose test count in the inventory
      differs from the profile's, takes the observation outright;
    - any profile atom the inventory no longer contains is pruned.
    """
    atoms_by_key = {atom.key: atom for atom in inventory.atoms}
    atoms = {key: dict(entry) for key, entry in profile.atoms.items()}
    updated_from = list(profile.updated_from)
    for record in records:
        for key, observed in _mergeable_atoms(record, atoms_by_key).items():
            tests = len(atoms_by_key[key].test_ids)
            old = atoms.get(key)
            if old is None or old["tests"] != tests:
                atoms[key] = {"seconds": round(observed, 3), "samples": 1, "tests": tests}
            else:
                seconds = EWMA_WEIGHT * observed + (1 - EWMA_WEIGHT) * old["seconds"]
                atoms[key] = {"seconds": round(seconds, 3), "samples": old["samples"] + 1,
                              "tests": tests}
        digest = record["plan_digest"]
        if digest in updated_from:
            updated_from.remove(digest)
        updated_from.append(digest)
    atoms = {key: entry for key, entry in atoms.items() if key in atoms_by_key}
    return TimingProfile(profile=profile_name or profile.profile, atoms=atoms,
                         updated_from=tuple(updated_from[-MAX_UPDATED_FROM:]))


# -- estimates ------------------------------------------------------------------------------


def _mean_per_test(profile: TimingProfile, module: str | None) -> float | None:
    """Seconds per test over the profile's controller atoms, restricted to
    ``module`` (its module atom or its classes) when given."""
    seconds = tests = 0
    for key, entry in profile.atoms.items():
        if key.startswith(CONFORMANCE_PREFIX):
            continue
        if module is not None and key != module and not key.startswith(module + "."):
            continue
        seconds += entry["seconds"]
        tests += entry["tests"]
    return seconds / tests if tests else None


def estimate_atoms(atoms: Iterable[Atom], profiles: Iterable[TimingProfile]) -> dict[str, float]:
    """An estimate in seconds for every atom in ``atoms``, keyed by atom key,
    from ``profiles`` in priority order (local, then the committed CI
    profile). The key set is exactly the atoms given: an estimate never adds,
    drops or conditions a test (I3).

    - A known atom uses the first profile that records it; if the atom's
      test count differs from the recorded one (new tests, or a partial
      selection), the recorded seconds are scaled per test;
    - an unknown conformance atom uses ``DEFAULT_CONFORMANCE_SECONDS``;
    - an unknown controller atom uses ``tests × mean seconds per test``, the
      mean taken over its module in the first profile that has it, else over
      the first non-empty profile, else ``DEFAULT_SECONDS_PER_TEST``.
    """
    profiles = tuple(profiles)
    means: dict[tuple[int, str | None], float | None] = {}

    def mean(index: int, module: str | None) -> float | None:
        if (index, module) not in means:
            means[index, module] = _mean_per_test(profiles[index], module)
        return means[index, module]

    estimates = {}
    for atom in atoms:
        tests = len(atom.test_ids)
        known = next((profile.atoms[atom.key] for profile in profiles
                      if atom.key in profile.atoms), None)
        if known is not None:
            seconds = known["seconds"]
            estimates[atom.key] = (seconds if known["tests"] == tests
                                   else seconds / known["tests"] * tests)
        elif atom.family == CONFORMANCE:
            estimates[atom.key] = DEFAULT_CONFORMANCE_SECONDS
        else:
            per_test = next((m for m in (mean(i, atom.module) for i in range(len(profiles)))
                             if m is not None), None)
            if per_test is None:
                per_test = next((m for m in (mean(i, None) for i in range(len(profiles)))
                                 if m is not None), DEFAULT_SECONDS_PER_TEST)
            estimates[atom.key] = tests * per_test
    return estimates


# -- planning -------------------------------------------------------------------------------


class PlanError(Exception):
    """A plan cannot be built, or a plan is not a valid partition of its
    selection; the message names every problem."""


@dataclass(frozen=True)
class PlanParameters:
    """The planning inputs besides the selection and the timings. ``shards``
    pins the count; ``None`` lets the formula choose it."""

    profile: str
    target_shard_seconds: float
    min_shards: int
    max_shards: int
    shards: int | None = None

    def document(self) -> dict:
        doc = {"target_shard_seconds": self.target_shard_seconds,
               "min_shards": self.min_shards, "max_shards": self.max_shards}
        if self.shards is not None:
            doc["shards"] = self.shards
        return doc


def profile_parameters(profile: str, *, cpu_count: int | None = None,
                       target_shard_seconds: float | None = None,
                       min_shards: int | None = None, max_shards: int | None = None,
                       shards: int | None = None) -> PlanParameters:
    """``profile``'s parameters, each overridable. The local maximum is
    ``min(LOCAL_MAX_SHARDS, cpu_count)``: one shard per CPU is the measured
    safe default (``cpu_count`` defaults to ``os.cpu_count()``)."""
    if profile == LOCAL:
        cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
        defaults = (LOCAL_TARGET_SHARD_SECONDS, DEFAULT_MIN_SHARDS,
                    max(1, min(LOCAL_MAX_SHARDS, cpus)))
    elif profile == CI:
        defaults = (CI_TARGET_SHARD_SECONDS, DEFAULT_MIN_SHARDS, CI_MAX_SHARDS)
    else:
        raise PlanError(f"unknown profile {profile!r}; expected {LOCAL!r} or {CI!r}")
    parameters = PlanParameters(
        profile=profile,
        target_shard_seconds=defaults[0] if target_shard_seconds is None
        else target_shard_seconds,
        min_shards=defaults[1] if min_shards is None else min_shards,
        max_shards=defaults[2] if max_shards is None else max_shards,
        shards=shards)
    problems = []
    if not (_is_seconds(parameters.target_shard_seconds)
            and parameters.target_shard_seconds > 0):
        problems.append(f"target_shard_seconds must be positive and finite, "
                        f"not {parameters.target_shard_seconds!r}")
    for name in ("min_shards", "max_shards", "shards"):
        value = getattr(parameters, name)
        if not (value is None and name == "shards") and not (_is_int(value) and value >= 1):
            problems.append(f"{name} must be an integer >= 1, not {value!r}")
    if problems:
        raise PlanError("; ".join(problems))
    return parameters


def timings_for(profile: str, repo_root: Path = REPO_ROOT, *,
                environ: Mapping[str, str] = os.environ,
                warn: Callable[[str], None] = _warn) -> tuple[tuple[Path, TimingProfile], ...]:
    """The timing files ``profile`` plans from, in priority order, each with
    its loaded profile: CI reads only the committed CI profile; local reads
    the local profile, then the committed one."""
    paths = [Path(repo_root) / CI_TIMINGS]
    if profile == LOCAL:
        paths.insert(0, local_timings_path(environ))
    return tuple((path, load_timings(path, warn=warn)) for path in paths)


def timing_source(timings: Iterable[tuple[Path, TimingProfile]],
                  repo_root: Path = REPO_ROOT) -> list[dict] | str:
    """The plan's ``timing_source``: one ``{path, sha256}`` per timing file
    actually read, in priority order, or ``"defaults"`` when none was. A file
    inside the repository is named by its repository-relative POSIX path; any
    other (the local profile) as ``"local-profile"``, so the digest never
    depends on where the checkout or the cache lives."""
    root = Path(repo_root).resolve()
    sources = []
    for path, profile in timings:
        if profile.sha256 is None:
            continue
        try:
            name = Path(path).resolve().relative_to(root).as_posix()
        except ValueError:
            name = LOCAL_PROFILE_SOURCE
        sources.append({"path": name, "sha256": profile.sha256})
    return sources or DEFAULTS_SOURCE


def _milliseconds(seconds: float) -> int:
    return int(round(seconds * 1000))


def shard_count(estimates_ms: Iterable[int], parameters: PlanParameters) -> int:
    """``clamp(ceil(total / max(target, largest)), min, max)``, capped at the
    number of atoms so no shard is empty; ``parameters.shards`` pins the count
    (still capped). Integer milliseconds, so every platform agrees."""
    estimates_ms = list(estimates_ms)
    atoms = len(estimates_ms)
    if parameters.shards is not None:
        return min(parameters.shards, atoms)
    total = sum(estimates_ms)
    effective = max(_milliseconds(parameters.target_shard_seconds), max(estimates_ms, default=0))
    count = -(-total // effective) if effective else 0
    count = min(max(count, parameters.min_shards), parameters.max_shards)
    return min(count, atoms)


def assign_atoms(estimates_ms: list[int], count: int) -> list[list[int]]:
    """Deterministic LPT: atoms (by canonical index) sorted by ``(-estimate,
    index)``, each onto the shard with the least ``(load, shard index)``;
    each shard's atoms then back in canonical order."""
    heap = [(0, index) for index in range(count)]
    shards: list[list[int]] = [[] for _ in range(count)]
    for atom in sorted(range(len(estimates_ms)), key=lambda i: (-estimates_ms[i], i)):
        load, index = heapq.heappop(heap)
        shards[index].append(atom)
        heapq.heappush(heap, (load + estimates_ms[atom], index))
    return [sorted(shard) for shard in shards]


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def plan_digest(plan: Mapping) -> str:
    """SHA-256 of the canonical JSON of everything in ``plan`` but the digest."""
    return hashlib.sha256(canonical_json(
        {key: value for key, value in plan.items() if key != "plan_digest"})).hexdigest()


def build_plan(selection: Selection, parameters: PlanParameters,
               timings: Iterable[tuple[Path, TimingProfile]] = (),
               repo_root: Path = REPO_ROOT, *,
               exclusive: Mapping[str, str] | None = None) -> dict:
    """The plan for ``selection``: its shard count, a deterministic LPT
    assignment of whole atoms, and the ``plan_digest`` over all of it. Timing
    data (``timings``, ``(path, profile)`` in priority order) decides only
    where an atom runs (I3). The selected atoms registered in ``exclusive``
    (default ``EXCLUSIVE_ATOMS``) take no part in the count or the
    assignment: they go, in canonical order, on one extra final shard marked
    ``"exclusive": true``, so ``shard_count`` is one more than the formula's.
    The plan is validated as a partition of the selection (I1) before it is
    returned; a failure raises ``PlanError``."""
    if not selection.atoms:
        raise PlanError("the selection is empty; there is nothing to plan")
    exclusive = EXCLUSIVE_ATOMS if exclusive is None else exclusive
    timings = tuple(timings)
    estimates = estimate_atoms(selection.atoms, [profile for _, profile in timings])
    # At least 1 ms each: with positive loads LPT puts the N longest atoms on
    # N distinct shards, so no shard is left empty by zero estimates.
    estimates_ms = [max(1, _milliseconds(estimates[atom.key])) for atom in selection.atoms]
    parallel = [i for i, atom in enumerate(selection.atoms) if atom.key not in exclusive]
    alone = [i for i, atom in enumerate(selection.atoms) if atom.key in exclusive]
    groups = []
    if parallel:
        parallel_ms = [estimates_ms[i] for i in parallel]
        groups = [[parallel[i] for i in members]
                  for members in assign_atoms(parallel_ms, shard_count(parallel_ms, parameters))]
    if alone:
        groups.append(alone)
    shards = []
    for index, members in enumerate(groups):
        atoms = [selection.atoms[i] for i in members]
        shard = {"index": index, "atoms": [atom.key for atom in atoms],
                 "test_ids": [test_id for atom in atoms for test_id in atom.test_ids],
                 "estimate_seconds": sum(estimates_ms[i] for i in members) / 1000}
        if members is alone:
            shard[EXCLUSIVE] = True
        shards.append(shard)
    plan = {"schema_version": PLAN_SCHEMA_VERSION, "profile": parameters.profile,
            "selection_names": list(selection.names),
            "selected_ids": list(selection.test_ids),
            "parameters": parameters.document(), "shard_count": len(shards), "shards": shards,
            "timing_source": timing_source(timings, repo_root)}
    plan["plan_digest"] = plan_digest(plan)
    validate_plan(plan, selection, exclusive=exclusive)
    return plan


def is_exclusive(shard: Mapping) -> bool:
    return shard.get(EXCLUSIVE) is True


def validate_plan(plan, selection: Selection | None = None, *,
                  exclusive: Mapping[str, str] | None = None) -> dict:
    """Check that ``plan`` is a well-formed partition of its own
    ``selected_ids`` (I1): its keys and digest, ``shard_count`` shards indexed
    in order, none empty, pairwise disjoint, their union exactly the
    selection, each in canonical order, and at most one exclusive shard, the
    last. Given the ``selection`` it was planned from, also that the selected
    ids match, that every shard holds whole atoms, and that the exclusive
    shard holds exactly the selected atoms ``exclusive`` (default
    ``EXCLUSIVE_ATOMS``) registers. Returns ``plan``, or raises ``PlanError``
    naming every problem."""
    if not isinstance(plan, dict):
        raise PlanError("the plan is not a JSON object")
    problems = []
    if set(plan) != _PLAN_KEYS:
        problems.append("keys are not exactly " + ", ".join(sorted(_PLAN_KEYS)))
        raise PlanError("; ".join(problems))
    if plan["schema_version"] != PLAN_SCHEMA_VERSION:
        problems.append(f"schema_version is not {PLAN_SCHEMA_VERSION}")
    try:
        if plan["plan_digest"] != plan_digest(plan):
            problems.append("plan_digest does not match the plan's content")
    except (TypeError, ValueError) as exc:
        problems.append(f"the plan is not canonical JSON: {exc}")
    selected = plan["selected_ids"]
    shards = plan["shards"]
    if not isinstance(selected, list) or not isinstance(shards, list):
        problems.append("selected_ids and shards must be lists")
        raise PlanError("; ".join(problems))
    if not all(isinstance(test_id, str) for test_id in selected):
        problems.append("selected_ids must be strings")
        raise PlanError("; ".join(problems))
    position = {test_id: i for i, test_id in enumerate(selected)}
    if len(position) != len(selected):
        problems.append("selected_ids has duplicates")
    if plan["shard_count"] != len(shards):
        problems.append(f"shard_count is {plan['shard_count']!r} but there are "
                        f"{len(shards)} shards")
    seen: dict[str, int] = {}
    for i, shard in enumerate(shards):
        if not isinstance(shard, dict) or set(shard) - {EXCLUSIVE} != _SHARD_KEYS:
            problems.append(f"shards[{i}] keys are not exactly " + ", ".join(sorted(_SHARD_KEYS))
                            + f" and optionally {EXCLUSIVE}")
            continue
        if EXCLUSIVE in shard and (shard[EXCLUSIVE] is not True or i != len(shards) - 1):
            problems.append(f"shards[{i}].{EXCLUSIVE} must be true, and only on the last shard")
        if shard["index"] != i:
            problems.append(f"shards[{i}].index is {shard['index']!r}")
        ids = shard["test_ids"]
        if not all(isinstance(value, list) and all(isinstance(item, str) for item in value)
                   for value in (ids, shard["atoms"])):
            problems.append(f"shards[{i}].atoms and test_ids must be lists of strings")
            continue
        if not ids or not shard["atoms"]:
            problems.append(f"shard {i} is empty")
        for test_id in ids:
            if test_id not in position:
                problems.append(f"shard {i} holds unselected id {test_id!r}")
            elif test_id in seen:
                problems.append(f"{test_id!r} is in shards {seen[test_id]} and {i}")
            else:
                seen[test_id] = i
        order = [position[test_id] for test_id in ids if test_id in position]
        if order != sorted(order):
            problems.append(f"shard {i} is not in canonical order")
    missing = [test_id for test_id in selected if test_id not in seen]
    if missing:
        problems.append(f"{len(missing)} selected ids are in no shard, first {missing[0]!r}")
    if selection is not None and not problems:
        if tuple(selected) != selection.test_ids:
            problems.append("selected_ids differ from the selection")
        by_key = {atom.key: atom for atom in selection.atoms}
        for i, shard in enumerate(shards):
            atoms = [by_key.get(key) for key in shard["atoms"]]
            if None in atoms:
                problems.append(f"shard {i} names an atom outside the selection")
            elif shard["test_ids"] != [t for atom in atoms for t in atom.test_ids]:
                problems.append(f"shard {i} does not hold exactly its whole atoms")
        placed = [key for shard in shards for key in shard["atoms"]]
        if sorted(placed) != sorted(by_key):
            problems.append("the shards do not place every selected atom exactly once")
        registry = EXCLUSIVE_ATOMS if exclusive is None else exclusive
        wanted = [atom.key for atom in selection.atoms if atom.key in registry]
        alone = [key for shard in shards if is_exclusive(shard) for key in shard["atoms"]]
        if alone != wanted:
            problems.append(f"the exclusive shard holds {alone} but the selection's exclusive "
                            f"atoms are {wanted}")
    if problems:
        raise PlanError("; ".join(problems))
    return plan


# -- execution ------------------------------------------------------------------------------


#: The leak marker every shard's environment carries, ``<run_id>/<shard>``.
#: Each executor extends the marker it inherits with ``/<its pid>`` before it
#: runs anything, and a scan matches a marker or any marker under it, so a
#: runner finds all of a shard's descendants while an executor started inside
#: a test (a nested run) only ever finds its own, never its ancestors.
SHARD_MARKER_ENV = "WORKFLOW_CONTROLLER_TEST_SHARD"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_REFUSED = 2
EXIT_INTERRUPTED = 130
#: How much of a traceback (or a failing suite's output) the summary shows.
TRACEBACK_TAIL_LINES = 40
RUN_TESTS = "tools/run_tests.py"

_SubTest = unittest.case._SubTest
_ErrorHolder = unittest.suite._ErrorHolder


class ShardRefusedError(Exception):
    """An executor precondition failed, so the shard runs nothing (exit 2)."""


def result_path(results_dir: Path, index: int) -> Path:
    return Path(results_dir) / f"shard-{index}.json"


def log_path(results_dir: Path, index: int) -> Path:
    return Path(results_dir) / f"shard-{index}.log"


def shard_environment(environ: Mapping[str, str], results_dir: Path, run_id: str, index: int,
                      *, isolated_pip_cache: bool = False) -> dict[str, str]:
    """``environ`` shaped for shard ``index``: its own ``TMPDIR`` and
    ``XDG_STATE_HOME`` under the results directory, the leak marker, and a
    per-shard ``PIP_CACHE_DIR`` only with ``isolated_pip_cache``. Nothing
    else changes: ``HOME``, ``PATH``, ``PYTHONPATH``,
    ``WORKFLOW_CONTROLLER_HOME`` and an operator's own ``PIP_CACHE_DIR`` are
    inherited as they are."""
    base = Path(results_dir) / f"shard-{index}"
    env = dict(environ)
    env["TMPDIR"] = str(base / "tmp")
    env["XDG_STATE_HOME"] = str(base / "state")
    if isolated_pip_cache:
        env["PIP_CACHE_DIR"] = str(Path(results_dir) / f"pip-cache-{index}")
    env[SHARD_MARKER_ENV] = f"{run_id}/{index}"
    return env


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _is_substitute(test) -> bool:
    return (type(test).__name__ in SUBSTITUTE_TEST_TYPES
            or type(test).__module__ == "unittest.loader"
            or not isinstance(test, unittest.TestCase))


def load_shard(shard: Mapping, repo_root: Path = REPO_ROOT,
               loader: unittest.TestLoader | None = None) -> list[tuple[str, object]]:
    """Load ``shard``'s atoms, in its order, as ``(atom key, suite)`` pairs
    (``suite`` is ``None`` for a conformance atom), filtered to the planned
    ids. Raises ``ShardRefusedError`` naming the difference unless the loaded
    ids equal the planned ids exactly, in order, with no substituted test
    (``_FailedTest``, ``ModuleImportFailure``, ``ModuleSkipped``, ...)."""
    loader = loader or unittest.TestLoader()
    planned = list(shard["test_ids"])
    planned_set = set(planned)
    problems: list[str] = []
    loaded: list[str] = []
    units: list[tuple[str, object]] = []
    for key in shard["atoms"]:
        if key.startswith(CONFORMANCE_PREFIX):
            suite = key[len(CONFORMANCE_PREFIX):]
            if "/" in suite or not (Path(repo_root) / SCRIPTS_DIR / suite).is_file():
                problems.append(f"conformance suite {suite} is not a file in {SCRIPTS_DIR}/")
                continue
            loaded.append(key)
            units.append((key, None))
            continue
        errors_before = len(loader.errors)
        try:
            suite = loader.loadTestsFromName(key)
        except Exception as exc:  # noqa: BLE001 - any load failure refuses the shard
            problems.append(f"cannot load {key}: {type(exc).__name__}: {exc}")
            continue
        problems += [f"loading {key}: {(error.strip().splitlines() or [''])[-1]}"
                     for error in loader.errors[errors_before:]]
        tests = []
        for test in _flatten(suite):
            if _is_substitute(test):
                problems.append(f"{type(test).__name__} substituted for {test.id()} "
                                f"while loading {key}")
            elif test.id() in planned_set:
                tests.append(test)
                loaded.append(test.id())
        units.append((key, unittest.TestSuite(tests)))
    if loaded != planned:
        loaded_set = set(loaded)
        missing = [test_id for test_id in planned if test_id not in loaded_set]
        duplicates = sorted({test_id for test_id in loaded if loaded.count(test_id) > 1})
        if missing:
            problems.append(f"{len(missing)} planned ids were not loaded: "
                            + ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else ""))
        if duplicates:
            problems.append("ids loaded more than once: " + ", ".join(duplicates[:10]))
        if not missing and not duplicates:
            problems.append("the loaded ids are not in the planned order")
    if problems:
        raise ShardRefusedError("the shard is refused before running anything:\n  "
                                + "\n  ".join(problems))
    return units


class RecordingResult(unittest.TextTestResult):
    """A ``TextTestResult`` that keys every event to a planned id or to a
    fixture, never to an id ``unittest`` synthesises (the plan's recording
    rules):

    - an error, failure or skip on an ``_ErrorHolder`` (a module or class
      fixture) goes to ``fixture_errors`` or ``fixture_skips``;
    - a subtest's outcome is folded into its parent: ``error`` if any
      subtest errored, else ``fail`` if any failed, else the parent's own
      outcome (a skip inside a subtest, which leaves the parent without an
      outcome of its own, makes it ``skip``);
    - an entry is written at ``stopTest``, so a parent whose subtest failed
      (and which therefore gets no ``addSuccess``) is still reported;
    - any event on anything else is kept in ``unmapped``, which refuses the
      shard.
    """

    def __init__(self, stream, descriptions, verbosity, *, planned: Iterable[str], **kwargs):
        super().__init__(stream, descriptions, verbosity, **kwargs)
        self.planned = frozenset(planned)
        self.entries: dict[str, dict] = {}
        self.fixture_errors: list[dict] = []
        self.fixture_skips: list[dict] = []
        self.unmapped: list[str] = []
        self._running: dict[str, dict] = {}

    def _state(self, test, event: str) -> dict | None:
        parent = test.test_case if isinstance(test, _SubTest) else test
        state = self._running.get(parent.id()) if isinstance(parent, unittest.TestCase) else None
        if state is None:
            self.unmapped.append(f"{event} on {test!r}")
        return state

    def startTest(self, test):  # noqa: N802 - unittest's name
        super().startTest(test)
        test_id = test.id()
        if test_id not in self.planned or test_id in self.entries or test_id in self._running:
            self.unmapped.append(f"startTest on {test_id}, which is unplanned or already ran")
            return
        self._running[test_id] = {"start": time.monotonic(), "own": None, "subtests": set(),
                                  "detail": []}

    def _own(self, test, event: str, outcome: str, detail: str | None = None) -> None:
        state = self._state(test, event)
        if state is not None:
            state["own"] = outcome
            if detail:
                state["detail"].append(detail)

    def addSuccess(self, test):  # noqa: N802
        super().addSuccess(test)
        self._own(test, "addSuccess", "pass")

    def _add_problem(self, test, err, outcome: str) -> None:
        if isinstance(test, _ErrorHolder):
            self.fixture_errors.append({"description": test.description,
                                        "traceback": self._exc_info_to_string(err, test)})
        else:
            self._own(test, f"add{outcome.title()}", outcome, self._exc_info_to_string(err, test))

    def addFailure(self, test, err):  # noqa: N802
        super().addFailure(test, err)
        self._add_problem(test, err, "fail")

    def addError(self, test, err):  # noqa: N802
        super().addError(test, err)
        self._add_problem(test, err, "error")

    def addSkip(self, test, reason):  # noqa: N802
        super().addSkip(test, reason)
        if isinstance(test, _ErrorHolder):
            self.fixture_skips.append({"description": test.description, "reason": str(reason)})
        elif isinstance(test, _SubTest):
            state = self._state(test, "addSkip")
            if state is not None:
                state["subtests"].add("skip")
                state["detail"].append(f"{test.id()} skipped: {reason}")
        else:
            self._own(test, "addSkip", "skip", str(reason))

    def addExpectedFailure(self, test, err):  # noqa: N802
        super().addExpectedFailure(test, err)
        self._own(test, "addExpectedFailure", "expected_failure",
                  self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):  # noqa: N802
        super().addUnexpectedSuccess(test)
        self._own(test, "addUnexpectedSuccess", "unexpected_success",
                  "unexpected success of an expectedFailure test")

    def addSubTest(self, test, subtest, err):  # noqa: N802
        super().addSubTest(test, subtest, err)
        if err is None:
            return
        state = self._state(subtest, "addSubTest")
        if state is not None:
            kind = "fail" if issubclass(err[0], test.failureException) else "error"
            state["subtests"].add(kind)
            state["detail"].append(f"{subtest.id()}\n{self._exc_info_to_string(err, test)}")

    def stopTest(self, test):  # noqa: N802
        super().stopTest(test)
        state = self._running.pop(test.id(), None)
        if state is None:
            return
        own, subtests = state["own"], state["subtests"]
        if "error" in subtests or own == "error":
            outcome = "error"
        elif "fail" in subtests or own == "fail":
            outcome = "fail"
        elif own is not None:
            outcome = own
        elif "skip" in subtests:
            outcome = "skip"
        else:
            outcome = "error"
            state["detail"].append("unittest reported no outcome for this test")
        entry = {"id": test.id(), "outcome": outcome,
                 "seconds": round(time.monotonic() - state["start"], 6)}
        if state["detail"]:
            entry["detail"] = "\n".join(state["detail"])
        self.entries[test.id()] = entry


class _AtomSequence:
    """What ``TextTestRunner.run`` calls: each atom's suite as its own
    top-level run, timed including its class and module fixtures."""

    def __init__(self, units: list[tuple[str, unittest.TestSuite]]) -> None:
        self.units = units
        self.seconds: dict[str, float] = {}

    def __call__(self, result):
        for key, suite in self.units:
            start = time.monotonic()
            suite(result)
            # The top-level run tore this atom's class down; forget it, or the
            # next atom's first test would tear it down a second time.
            result._previousTestClass = None
            self.seconds[key] = round(time.monotonic() - start, 6)
        return result


def _covering(test_id: str, atom_key: str, entries: list[dict]) -> dict | None:
    """The first fixture entry whose holder covers ``test_id``: its class or
    module, or a module fixture over the atom the id was loaded from."""
    for entry in entries:
        target = holder_target(entry["description"])
        if target is not None and (test_id.startswith(target + ".") or target == atom_key
                                   or atom_key.startswith(target + ".")):
            return entry
    return None


def _backfill(result: RecordingResult, planned: list[str], atom_of: Mapping[str, str]) -> None:
    """Give every planned id a fixture error or skip kept from running an
    entry naming that fixture (plan: "setUpClass failure", "Fixture skips")."""
    for test_id in planned:
        if test_id in result.entries or test_id not in atom_of:
            continue
        error = _covering(test_id, atom_of[test_id], result.fixture_errors)
        if error is not None:
            result.entries[test_id] = {
                "id": test_id, "outcome": "error", "seconds": 0,
                "detail": f"not run: {error['description']} errored (see fixture_errors)"}
            continue
        skip = _covering(test_id, atom_of[test_id], result.fixture_skips)
        if skip is not None:
            result.entries[test_id] = {
                "id": test_id, "outcome": "skip", "seconds": 0,
                "detail": f"skipped by {skip['description']}: {skip['reason']}"}


def _run_conformance(key: str, repo_root: Path, log) -> tuple[dict, float]:
    """Run one frozen suite as the managed workflow does (``cd scripts &&
    python3 <file>``), with this interpreter and the inherited environment,
    appending its output to the log."""
    suite = key[len(CONFORMANCE_PREFIX):]
    log.write(f"\n=== {key}: {sys.executable} {suite} (in {SCRIPTS_DIR}/) ===\n")
    log.flush()
    start = time.monotonic()
    process = subprocess.Popen([sys.executable, suite], cwd=Path(repo_root) / SCRIPTS_DIR,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    tail: deque[str] = deque(maxlen=TRACEBACK_TAIL_LINES)
    for raw in process.stdout:
        line = raw.decode("utf-8", "replace")
        log.write(line)
        tail.append(line.rstrip("\n"))
    status = process.wait()
    seconds = round(time.monotonic() - start, 6)
    log.write(f"=== {key}: exit status {status} in {seconds:.3f}s ===\n")
    log.flush()
    entry = {"id": key, "outcome": "pass" if status == 0 else "fail", "seconds": seconds}
    if status:
        entry["detail"] = (f"{suite} exited with status {status}; its last output:\n"
                           + "\n".join(tail))
    return entry, seconds


def shard_status(record: Mapping, was_successful: bool | None,
                 problems: list[str]) -> int:
    """The executor's exit status for ``record``: ``2`` when ``problems`` is
    non-empty or when the record's controller-family verdict differs from
    ``unittest``'s own ``wasSuccessful()`` in either direction (appending
    that to ``problems``), else ``0`` if the record passes, else ``1``.
    ``was_successful`` is ``None`` when no controller test was run."""
    controller_passes = (all(entry["outcome"] in PASSING_OUTCOMES for entry in record["tests"]
                             if not entry["id"].startswith(CONFORMANCE_PREFIX))
                         and not record["fixture_errors"])
    expected = True if was_successful is None else was_successful
    if controller_passes != expected:
        problems.append(f"the record's controller verdict ({'pass' if controller_passes else 'fail'}) "
                        f"differs from unittest's wasSuccessful() ({expected})")
    if problems:
        return EXIT_REFUSED
    return EXIT_PASS if record_passes(record) else EXIT_FAIL


def execute_shard(plan: Mapping, index: int, results_dir: Path, repo_root: Path = REPO_ROOT,
                  *, argv: Iterable[str] = (), environ: Mapping[str, str] = os.environ
                  ) -> tuple[dict, int]:
    """Run shard ``index`` of ``plan`` in this process, write
    ``<results_dir>/shard-<index>.json`` (appending the runner output to
    ``shard-<index>.log``), and return the record and the exit status: ``0``
    pass, ``1`` fail, ``2`` refused. The loaded ids are checked against the
    planned ids before anything runs; any event the recording rules cannot
    map, any planned id left without an entry, or a disagreement with
    ``unittest``'s own verdict refuses the shard after it ran."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    shard = plan["shards"][index]
    planned = list(shard["test_ids"])
    started_at, start = _now(), time.monotonic()
    entries: dict[str, dict] = {}
    fixture_errors: list[dict] = []
    fixture_skips: list[dict] = []
    atoms: dict[str, float] = {}
    problems: list[str] = []
    was_successful = None
    with open(log_path(results_dir, index), "a", encoding="utf-8") as log:
        try:
            units = load_shard(shard, repo_root)
        except ShardRefusedError as exc:
            units = None
            problems.append(str(exc))
        if units is not None:
            controller = [(key, suite) for key, suite in units if suite is not None]
            if controller:
                atom_of = {test.id(): key for key, suite in controller for test in suite}
                sequence = _AtomSequence(controller)
                runner = unittest.TextTestRunner(
                    stream=log, verbosity=2,
                    resultclass=partial(RecordingResult, planned=atom_of))
                result = runner.run(sequence)
                was_successful = result.wasSuccessful()
                _backfill(result, planned, atom_of)
                entries.update(result.entries)
                fixture_errors, fixture_skips = result.fixture_errors, result.fixture_skips
                atoms.update(sequence.seconds)
                problems += [f"unmapped unittest event: {event}" for event in result.unmapped]
            for key, suite in units:
                if suite is None:
                    entries[key], atoms[key] = _run_conformance(key, repo_root, log)
            missing = [test_id for test_id in planned if test_id not in entries]
            if missing:
                problems.append(f"{len(missing)} planned ids have no outcome: "
                                + ", ".join(missing[:10]))
        leaks = []
        marker = environ.get(SHARD_MARKER_ENV)
        if marker:
            leaks = scan_marked_processes(marker, exclude={os.getpid()})
            kill_processes(leaks)
        record = {
            "schema_version": RESULT_SCHEMA_VERSION, "plan_digest": plan["plan_digest"],
            "shard": index, "argv": list(argv), "started_at": started_at, "ended_at": _now(),
            "wall_seconds": round(time.monotonic() - start, 6), "exit_status": EXIT_REFUSED,
            "tests": [entries[test_id] for test_id in planned if test_id in entries],
            "fixture_errors": fixture_errors, "fixture_skips": fixture_skips,
            "atoms": atoms, "leaked_processes": leaks}
        status = shard_status(record, was_successful, problems)
        record["exit_status"] = status
        for problem in problems:
            log.write(f"\nREFUSED: {problem}\n")
    validate_shard_result(record)
    write_json_atomic(result_path(results_dir, index), record)
    return record, status


# -- leaked processes -----------------------------------------------------------------------


def _process_age(pid: int) -> float:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        start_ticks = int(stat[stat.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return 0.0
    return round(max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK")), 3)


def scan_marked_processes(marker: str, *, exclude: Iterable[int] = ()) -> list[dict]:
    """Every live process whose environment carries ``<SHARD_MARKER_ENV>=
    <marker>`` or a marker under it (``<marker>/...``), as ``{pid, argv,
    age_seconds}``. A process started with a cleared environment is
    invisible to this scan, by construction."""
    needle = f"{SHARD_MARKER_ENV}={marker}".encode()
    nested = needle + b"/"
    exclude = set(exclude)
    found = []
    try:
        pids = sorted(int(name) for name in os.listdir("/proc") if name.isdigit())
    except OSError:
        return []
    for pid in pids:
        if pid in exclude:
            continue
        try:
            environ = Path(f"/proc/{pid}/environ").read_bytes()
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if not any(entry == needle or entry.startswith(nested)
                   for entry in environ.split(b"\0")):
            continue
        argv = [part.decode("utf-8", "replace") for part in cmdline.split(b"\0") if part]
        found.append({"pid": pid, "argv": argv, "age_seconds": _process_age(pid)})
    return found


def kill_processes(processes: Iterable[Mapping]) -> None:
    """SIGKILL each process's group, or the process alone when it shares
    this process's group."""
    own_group = os.getpgrp()
    for process in processes:
        pid = process["pid"]
        try:
            group = os.getpgid(pid)
            if group != own_group:
                os.killpg(group, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue


# -- aggregation ----------------------------------------------------------------------------


PASS = "PASS"
FAIL = "FAIL"
CRASHED = "CRASHED"
REFUSED = "REFUSED"
INTERRUPTED = "INTERRUPTED"
FAILING_OUTCOMES = frozenset({"fail", "error", "unexpected_success"})


@dataclass(frozen=True)
class ArtifactNames:
    """Where a CI run's evidence can be downloaded: the plan's artifact, and
    the prefix of each shard's results artifact (``<prefix><index>``)."""
    plan: str
    results_prefix: str


class AggregateError(Exception):
    """The results cannot be aggregated against the plan (exit 2)."""


@dataclass
class Aggregate:
    shards: list[dict]
    not_run: list[tuple[str, int]]
    violations: list[str]
    failures: list[dict]
    fixture_errors: list[tuple[int, dict]]
    fixture_skips: list[tuple[int, dict, int]]
    leaks: list[tuple[int, dict]]
    exit_status: int
    summary: str = ""
    notes: dict[int, str] = field(default_factory=dict)
    #: The coverage check's counts: ``planned``, ``missing``, ``unplanned``
    #: and ``duplicate``; it is exact when the last three are all zero.
    coverage: dict[str, int] = field(default_factory=dict)


def load_results(plan: Mapping, results_dir: Path, indexes: Iterable[int] | None = None
                 ) -> tuple[dict[int, dict | None], dict[int, str]]:
    """Each planned shard's record from ``results_dir`` (``None`` when it is
    missing or invalid), and a note naming why for each ``None``."""
    records: dict[int, dict | None] = {}
    notes: dict[int, str] = {}
    for index in (range(plan["shard_count"]) if indexes is None else indexes):
        path = result_path(results_dir, index)
        if not path.exists():
            records[index], notes[index] = None, "no result record"
            continue
        try:
            records[index] = load_shard_result(path)
        except ResultRecordError as exc:
            records[index], notes[index] = None, str(exc)
    return records, notes


def _verdict(index: int, planned: list[str], record: Mapping | None, interrupted: bool,
             notes: dict[int, str]) -> str:
    if interrupted:
        return INTERRUPTED
    if record is None:
        notes.setdefault(index, "no result record")
        return CRASHED
    if record["exit_status"] == EXIT_REFUSED:
        notes.setdefault(index, f"the executor refused (exit 2); see {log_path('', index).name}")
        return REFUSED
    reported = {entry["id"] for entry in record["tests"]}
    if not set(planned) <= reported:
        notes.setdefault(index, "the result record does not cover the shard's planned ids")
        return CRASHED
    passes = record_passes(record)
    if record["exit_status"] != (EXIT_PASS if passes else EXIT_FAIL):
        notes.setdefault(index, f"exit status {record['exit_status']} disagrees with the "
                                f"record's own verdict")
        return CRASHED
    return PASS if passes else FAIL


def aggregate(plan: Mapping, records: Mapping[int, Mapping | None], results_dir: Path, *,
              shards: Iterable[int] | None = None, interrupted: Iterable[int] = (),
              notes: Mapping[int, str] | None = None, wall_seconds: float | None = None,
              artifacts: ArtifactNames | None = None) -> Aggregate:
    """Verdicts, the run-time coverage proof (I2), the exit status and the
    summary for ``plan`` given its shards' ``records`` (``None`` for a shard
    that left none). ``shards`` restricts the run to those shards (a
    ``--replay --shard``). A record carrying another plan's digest, or
    another shard's index, refuses the whole aggregate. ``artifacts`` names
    the CI artifacts that hold each failure's log and the plan."""
    validate_plan(plan)
    indexes = list(range(plan["shard_count"]) if shards is None else shards)
    interrupted = set(interrupted)
    notes = dict(notes or {})
    for index in indexes:
        record = records.get(index)
        if record is None:
            continue
        if record["plan_digest"] != plan["plan_digest"]:
            raise AggregateError(f"shard {index}'s result carries plan_digest "
                                 f"{record['plan_digest']}, not the plan's {plan['plan_digest']}")
        if record["shard"] != index:
            raise AggregateError(f"the result for shard {index} says it is shard "
                                 f"{record['shard']}")
    rows, not_run, violations, failures = [], [], [], []
    fixture_errors, fixture_skips, leaks = [], [], []
    unplanned = duplicate = 0
    seen: dict[str, int] = {}
    for index in indexes:
        shard = plan["shards"][index]
        planned = shard["test_ids"]
        record = records.get(index)
        verdict = _verdict(index, planned, record, index in interrupted, notes)
        reported = []
        if record is not None:
            planned_set = set(planned)
            for entry in record["tests"]:
                test_id = entry["id"]
                reported.append(test_id)
                if test_id not in planned_set:
                    unplanned += 1
                    violations.append(f"shard {index} reported unplanned id {test_id}")
                elif test_id in seen:
                    duplicate += 1
                    violations.append(f"{test_id} was reported more than once "
                                      f"(shards {seen[test_id]} and {index})")
                else:
                    seen[test_id] = index
                if entry["outcome"] in FAILING_OUTCOMES:
                    failures.append({"id": test_id, "shard": index, "outcome": entry["outcome"],
                                     "detail": entry.get("detail", "")})
            fixture_errors += [(index, entry) for entry in record["fixture_errors"]]
            for entry in record["fixture_skips"]:
                target = holder_target(entry["description"]) or entry["description"]
                count = sum(1 for test in record["tests"] if test["outcome"] == "skip"
                            and (test["id"] == target or test["id"].startswith(target + ".")))
                fixture_skips.append((index, entry, count))
            leaks += [(index, entry) for entry in record["leaked_processes"]]
        reported_set = set(reported)
        not_run += [(test_id, index) for test_id in planned if test_id not in reported_set]
        rows.append({"index": index, "verdict": verdict, "tests": len(planned),
                     "wall_seconds": record["wall_seconds"] if record is not None else None,
                     "estimate_seconds": shard["estimate_seconds"],
                     "atoms": dict(record["atoms"]) if record is not None else {}})
    verdicts = {row["verdict"] for row in rows}
    if INTERRUPTED in verdicts:
        status = EXIT_INTERRUPTED
    elif verdicts & {REFUSED, CRASHED} or not_run or violations:
        status = EXIT_REFUSED
    elif FAIL in verdicts:
        status = EXIT_FAIL
    else:
        status = EXIT_PASS
    result = Aggregate(shards=rows, not_run=not_run, violations=violations, failures=failures,
                       fixture_errors=fixture_errors, fixture_skips=fixture_skips, leaks=leaks,
                       exit_status=status, notes=notes,
                       coverage={"planned": sum(row["tests"] for row in rows),
                                 "missing": len(not_run), "unplanned": unplanned,
                                 "duplicate": duplicate})
    result.summary = render_summary(plan, result, results_dir, wall_seconds=wall_seconds,
                                    artifacts=artifacts)
    return result


_STATUS_WORDS = {EXIT_PASS: "PASS", EXIT_FAIL: "FAIL", EXIT_REFUSED: "ERROR",
                 EXIT_INTERRUPTED: "INTERRUPTED"}


def _tail(text: str, lines: int = TRACEBACK_TAIL_LINES) -> str:
    return "\n".join(text.rstrip("\n").splitlines()[-lines:])


def _reproduce(name: str, index: int, results_dir: Path) -> list[str]:
    if name.startswith(CONFORMANCE_PREFIX):
        isolated = f"cd {SCRIPTS_DIR} && python3 {name[len(CONFORMANCE_PREFIX):]}"
    else:
        isolated = f"python3 -m unittest {name}"
    return [f"- reproduce alone: `{isolated}`",
            f"- reproduce in the shard's order: `python3 {RUN_TESTS} --replay "
            f"{Path(results_dir) / 'plan.json'} --shard {index}`"]


def shard_verdict(record: Mapping, status: int, results_dir: Path) -> str:
    """The executor's own short verdict for a shard that did not pass: its
    failing ids and fixture errors with their traceback tails, and its log,
    so a failed CI shard job says why in its own step log."""
    index = record["shard"]
    label = "REFUSED" if status == EXIT_REFUSED else "FAIL"
    lines = [f"shard {index}: {label} (exit {status}); log: {log_path(results_dir, index)}"]
    for entry in record["tests"]:
        if entry["outcome"] in FAILING_OUTCOMES:
            lines += [f"- {entry['outcome']}: {entry['id']}", _tail(entry.get("detail", ""))]
    for entry in record["fixture_errors"]:
        lines += [f"- fixture error: {entry['description']}", _tail(entry["traceback"])]
    if status == EXIT_REFUSED:
        lines.append("- the executor refused the shard; the log's REFUSED lines say why")
    return "\n".join(line for line in lines if line) + "\n"


def exclusive_registry_line(plan: Mapping) -> str:
    """The registry's audit line: its size, and how many atoms of ``plan``
    run on the exclusive shard."""
    alone = sum(len(shard["atoms"]) for shard in plan["shards"] if is_exclusive(shard))
    return (f"EXCLUSIVE_ATOMS: {len(EXCLUSIVE_ATOMS)} registered, {alone} in this plan's "
            f"exclusive shard")


def _where(index: int, results_dir: Path, artifacts: ArtifactNames | None) -> list[str]:
    lines = [f"- log: `{log_path(results_dir, index)}`"]
    if artifacts is not None:
        lines.append(f"- CI artifacts: the log is in `{artifacts.results_prefix}{index}`; "
                     f"`{artifacts.plan}` holds `plan.json` for the replay command")
    return lines


def _coverage_line(coverage: Mapping[str, int]) -> str:
    """The coverage check's verdict, stated whether or not it passed."""
    counts = (f"{coverage['missing']} missing, {coverage['unplanned']} unplanned, "
              f"{coverage['duplicate']} duplicate")
    if coverage["missing"] or coverage["unplanned"] or coverage["duplicate"]:
        return f"- coverage: NOT exact; {counts} (listed below)"
    return f"- coverage: exact; all {coverage['planned']} planned tests ran once; {counts}"


def render_summary(plan: Mapping, result: Aggregate, results_dir: Path, *,
                   wall_seconds: float | None = None,
                   artifacts: ArtifactNames | None = None) -> str:
    """The run's Markdown summary: one row per shard, then every failing
    test and fixture error with its traceback tail, log and reproduction
    commands (and, in CI, the artifacts holding the log and the plan), the
    NOT RUN ids, coverage violations, fixture skips and leaked
    processes, and the coverage verdict, wall time, largest atom and balance
    ratio."""
    results_dir = Path(results_dir)
    lines = [f"# Test run: {_STATUS_WORDS[result.exit_status]} (exit {result.exit_status})", "",
             f"- plan `{plan['plan_digest']}`, profile `{plan['profile']}`, "
             f"{len(plan['selected_ids'])} selected tests in {plan['shard_count']} shards",
             f"- results: `{results_dir}`",
             f"- {exclusive_registry_line(plan)}", "",
             "| shard | verdict | tests | wall s | estimate s |",
             "| --- | --- | --- | --- | --- |"]
    for row in result.shards:
        wall = "-" if row["wall_seconds"] is None else f"{row['wall_seconds']:.1f}"
        note = result.notes.get(row["index"])
        verdict = row["verdict"] + (f" ({note})" if note and row["verdict"] != PASS else "")
        index = f"{row['index']} ({EXCLUSIVE})" if is_exclusive(
            plan["shards"][row["index"]]) else row["index"]
        lines.append(f"| {index} | {verdict} | {row['tests']} | {wall} | "
                     f"{row['estimate_seconds']:.1f} |")
    walls = [row["wall_seconds"] for row in result.shards if row["wall_seconds"] is not None]
    atoms = [(seconds, key) for row in result.shards for key, seconds in row["atoms"].items()]
    lines.append("")
    lines.append(_coverage_line(result.coverage))
    if wall_seconds is not None:
        lines.append(f"- wall time: {wall_seconds:.1f} s")
    if atoms:
        seconds, key = max(atoms)
        lines.append(f"- largest atom: `{key}` ({seconds:.1f} s)")
    if walls and sum(walls) > 0:
        lines.append(f"- balance ratio (max / mean shard wall): "
                     f"{max(walls) / (sum(walls) / len(walls)):.2f}")
    if result.failures:
        lines += ["", "## Failures"]
        for failure in result.failures:
            lines += ["", f"### `{failure['id']}`", "",
                      f"- shard {failure['shard']}, outcome `{failure['outcome']}`",
                      *_where(failure["shard"], results_dir, artifacts),
                      *_reproduce(failure["id"], failure["shard"], results_dir),
                      "", "```text", _tail(failure["detail"]), "```"]
    if result.fixture_errors:
        lines += ["", "## Fixture errors"]
        for index, entry in result.fixture_errors:
            name = holder_target(entry["description"]) or entry["description"]
            lines += ["", f"### `{entry['description']}`", "", f"- shard {index}",
                      *_where(index, results_dir, artifacts),
                      *_reproduce(name, index, results_dir),
                      "", "```text", _tail(entry["traceback"]), "```"]
    if result.not_run:
        lines += ["", f"## NOT RUN ({len(result.not_run)})", ""]
        lines += [f"- `{test_id}` (shard {index})" for test_id, index in result.not_run]
    if result.violations:
        lines += ["", "## Coverage violations", ""]
        lines += [f"- {violation}" for violation in result.violations]
    if result.fixture_skips:
        lines += ["", "## Fixture skips (not failures)", ""]
        lines += [f"- `{entry['description']}` (shard {index}): {entry['reason']} "
                  f"({count} planned tests skipped)"
                  for index, entry, count in result.fixture_skips]
    if result.leaks:
        lines += ["", "## Leaked processes (warning, killed)", ""]
        lines += [f"- shard {index}: pid {leak['pid']}, age {leak['age_seconds']:.1f} s: "
                  f"`{' '.join(leak['argv'])}`" for index, leak in result.leaks]
    return "\n".join(lines) + "\n"
