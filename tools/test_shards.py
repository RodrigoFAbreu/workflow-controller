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
longest-processing-time-first assignment, each shard in canonical order. The
plan carries a ``plan_digest`` over its canonical JSON, so every process
given the same inputs computes a byte-identical plan, and the planner checks
that the plan partitions the selection exactly before it returns it.

Nothing in ``controller/`` imports this file.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from dataclasses import dataclass, replace
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(timings_document(profile), indent=2, sort_keys=True) + "\n"
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
               repo_root: Path = REPO_ROOT) -> dict:
    """The plan for ``selection``: its shard count, a deterministic LPT
    assignment of whole atoms, and the ``plan_digest`` over all of it. Timing
    data (``timings``, ``(path, profile)`` in priority order) decides only
    where an atom runs (I3). The plan is validated as a partition of the
    selection (I1) before it is returned; a failure raises ``PlanError``."""
    if not selection.atoms:
        raise PlanError("the selection is empty; there is nothing to plan")
    timings = tuple(timings)
    estimates = estimate_atoms(selection.atoms, [profile for _, profile in timings])
    # At least 1 ms each: with positive loads LPT puts the N longest atoms on
    # N distinct shards, so no shard is left empty by zero estimates.
    estimates_ms = [max(1, _milliseconds(estimates[atom.key])) for atom in selection.atoms]
    count = shard_count(estimates_ms, parameters)
    shards = []
    for index, members in enumerate(assign_atoms(estimates_ms, count)):
        atoms = [selection.atoms[i] for i in members]
        shards.append({"index": index, "atoms": [atom.key for atom in atoms],
                       "test_ids": [test_id for atom in atoms for test_id in atom.test_ids],
                       "estimate_seconds": sum(estimates_ms[i] for i in members) / 1000})
    plan = {"schema_version": PLAN_SCHEMA_VERSION, "profile": parameters.profile,
            "selection_names": list(selection.names),
            "selected_ids": list(selection.test_ids),
            "parameters": parameters.document(), "shard_count": count, "shards": shards,
            "timing_source": timing_source(timings, repo_root)}
    plan["plan_digest"] = plan_digest(plan)
    validate_plan(plan, selection)
    return plan


def validate_plan(plan, selection: Selection | None = None) -> dict:
    """Check that ``plan`` is a well-formed partition of its own
    ``selected_ids`` (I1): its keys and digest, ``shard_count`` shards indexed
    in order, none empty, pairwise disjoint, their union exactly the
    selection, each in canonical order. Given the ``selection`` it was
    planned from, also that the selected ids match and that every shard
    holds whole atoms. Returns ``plan``, or raises ``PlanError`` naming every
    problem."""
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
        if not isinstance(shard, dict) or set(shard) != _SHARD_KEYS:
            problems.append(f"shards[{i}] keys are not exactly " + ", ".join(sorted(_SHARD_KEYS)))
            continue
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
    if problems:
        raise PlanError("; ".join(problems))
    return plan
