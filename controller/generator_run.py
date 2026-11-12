"""The guarded run of the managed repository's review-bundle generator (``workflow-controller-h4e3b``, CP1, D1 to D5a).

After a Codex apply role's landing and carry out, the bundle the role's next stage reads is stale, and
``scripts/prepare-ai-review.sh`` regenerates it. The generator starts ``python3`` thirteen times from the repository
root and, through ``PYTHONPATH``, over ``scripts/``, so a worker's landing could put code on what the interpreter
loads; and its two copy loops follow a link a landing wrote, so it could put a file from outside the repository into the
bundle it archives. This module is the Controller's proof that neither can happen, and the one place the run is made.

Three checks run before the generator and two of them again after it, all of them reading and never executing:

- :func:`check_landing` (D2): the landing's name-status diff touches nothing under ``scripts/`` or
  ``.workflow-manager/``. The installation record is a committed file, so the record alone would trust whatever the
  worker landed with it.
- :func:`check_tree` (D3): the present bytes of every file the record manages under ``scripts/`` are the record's
  digests, and the directory holds nothing else (a ``sitecustomize.py``, a module named like a standard-library one, a
  sourceless ``.pyc``, an extension module, a package directory).
- :func:`check_copy_sources` (D5a): every path the generator will copy (the changed paths, tracked and untracked, and the
  lines of the context list) is a regular file with no link on its way, inside the repository.

:func:`run` makes them in that order, runs the generator through ``workflow_contract.run_in_place`` (D4), and checks the
tree and the copy sources again (D5): the generator is the one process that ran with the managed tree writable under it.
Every refusal is a :class:`GeneratorRefused` with one cause of :data:`CAUSES`; the job (CP2) ends such a run at its
existing regeneration gate naming the cause.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import codex_review_role, gitrepo, protocol, workflow_contract
from .errors import ControllerError, GitOperationError, WorkflowQueryError

#: One cause per refusal; a test fails when one has no case.
CAUSES = (
    "surface_landing_touched", "surface_script_modified", "surface_foreign_entry", "surface_unreadable",
    "generator_not_managed", "git_not_isolated", "generator_failed", "generator_timed_out", "copy_source_unsafe",
    "surface_changed_during_run",
)

#: The stage the generator is run at for each of the two roles' stages (the two invocations the gate names).
STAGES = ("plan", "post-fix")

GENERATOR = "scripts/prepare-ai-review.sh"
ITEM_ROOT = ".ai-review"
SCRIPTS_DIR = "scripts"

#: D2: the paths a landing may not touch.
SURFACE_PREFIXES = ("scripts/", ".workflow-manager/")
SURFACE_ROOTS = ("scripts", ".workflow-manager")

#: D6 of the plan: the one fixed bound of a run, and the part of each stream kept.
TIMEOUT_SECONDS = 600.0
TAIL_BYTES = 4096

#: What the generator skips in the context list and the changed paths (WFR-16).
_SOURCE_PROPOSALS = ".ai-review/source/"

#: The reasons of ``copy_source_unsafe`` (D5a); ``unreadable`` is a path that could not be examined.
COPY_REASONS = ("absolute", "traversal", "link", "escapes", "unreadable")

_EVIDENCE_LIST = 20
_WITHHELD = "(output withheld: it contained private-key armour)"
#: The closing armour line, so a tail whose ``BEGIN`` line was cut off is still withheld.
_ARMOUR_END = re.compile(rb"-----END(?: [A-Z0-9]+)* PRIVATE KEY-----")


class GeneratorRefused(ControllerError):
    """A refusal of the guarded run; ``cause`` is one of :data:`CAUSES`, ``detail`` says what, and ``evidence`` carries
    both."""

    code = "GeneratorRefused"

    def __init__(self, cause: str, detail: str, **evidence: Any) -> None:
        super().__init__(f"{cause}: {detail}", evidence={**evidence, "cause": cause, "detail": detail})
        self.cause = cause
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class GeneratorRun:
    """The outcome of a run that exited ``0`` and passed the second check: the bounded, armour-free tails."""

    returncode: int
    stdout_tail: str
    stderr_tail: str


def _head(items: list[str]) -> list[str]:
    return sorted(items)[:_EVIDENCE_LIST]


# ---------------------------------------------------------------------------
# D2: the landing.
# ---------------------------------------------------------------------------


def on_surface(path: str) -> bool:
    """Whether ``path`` is under, or is, ``scripts/`` or ``.workflow-manager/``."""
    return path in SURFACE_ROOTS or path.startswith(SURFACE_PREFIXES)


def check_landing(root: Path, carry_base: str, landed_head: str | None) -> None:
    """Refuse (``surface_landing_touched``) a landing whose ``carry_base..landed_head`` diff touches the execution
    surface, whatever the status letter, naming each path; and a range the diff cannot read, or a head that is not a
    strict descendant of the base. A plan role makes no commit, so ``landed_head`` ``None`` (or the base itself) is an
    empty range and the tree check is its whole guard."""
    if landed_head is None or landed_head == carry_base:
        return
    try:
        descendant = gitrepo.strict_descendant(root, carry_base, landed_head)
        changes = gitrepo.diff_name_status(root, carry_base, landed_head) if descendant else []
    except GitOperationError as exc:
        raise GeneratorRefused("surface_landing_touched", f"the landing's range cannot be read ({exc.message})",
                               base=carry_base, head=landed_head) from exc
    if not descendant:
        raise GeneratorRefused("surface_landing_touched",
                               f"the landed head {landed_head} is not a descendant of the carry base {carry_base}",
                               base=carry_base, head=landed_head)
    touched = [f"{status} {path}" for status, path in changes if on_surface(path)]
    if touched:
        raise GeneratorRefused(
            "surface_landing_touched",
            f"the landing changed the execution surface: {', '.join(_head(touched))}",
            paths=_head(touched), base=carry_base, head=landed_head)


# ---------------------------------------------------------------------------
# D3: the tree.
# ---------------------------------------------------------------------------


def _read_regular(root: Path, rel: str) -> bytes:
    """The bytes of the regular file ``<root>/<rel>``, no link on the way and none followed. Raises ``FileNotFoundError``
    for an absent path and ``OSError`` for a link or anything not a regular file (the message says which)."""
    current = root
    parts = rel.split("/")
    for part in parts[:-1]:
        current = current / part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise OSError(f"{current} is a link")
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(f"{current} is not a directory")
    fd = os.open(current / parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{rel} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def _managed_scripts(root: Path) -> dict[str, str]:
    """The installation record's ``scripts/`` entries, as ``{path: sha256}``; refuses ``surface_unreadable`` for a record
    that cannot be read without following a link, is not the expected shape, or names a path outside one segment under
    ``scripts/``, and ``generator_not_managed`` when the generator is not one of them."""
    try:
        raw = _read_regular(root, protocol.INSTALLATION_RECORD)
        record = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        raise GeneratorRefused("generator_not_managed",
                               f"{protocol.INSTALLATION_RECORD} is absent, so no file of scripts/ is the installed "
                               "release's") from None
    except (OSError, ValueError) as exc:
        raise GeneratorRefused("surface_unreadable",
                               f"the installation record cannot be read ({exc})") from exc
    managed = record.get("managed") if isinstance(record, dict) else None
    if not isinstance(managed, dict):
        raise GeneratorRefused("generator_not_managed", "the installation record has no managed map")
    found: dict[str, str] = {}
    for key, value in managed.items():
        if not isinstance(key, str) or not key.startswith(SURFACE_PREFIXES[0]):
            continue
        name = key[len(SURFACE_PREFIXES[0]):]
        if not name or "/" in name or name in (".", "..") or "\0" in name:
            raise GeneratorRefused("surface_unreadable", f"the installation record names {key!r}, which is not one "
                                   "file directly under scripts/")
        digest = value.get("sha256") if isinstance(value, dict) else None
        if not isinstance(digest, str) or len(digest) != 64:
            raise GeneratorRefused("surface_unreadable", f"the installation record has no sha256 for {key}")
        found[key] = digest
    if GENERATOR not in found:
        raise GeneratorRefused("generator_not_managed", f"{GENERATOR} is not a file the installation record manages")
    return found


def check_tree(root: Path) -> None:
    """D3: every managed file under ``scripts/`` is a regular file, found without following a link, whose sha256 is the
    record's (``surface_script_modified``), and the directory holds no entry but those, ``__pycache__`` and names that
    start with a dot (``surface_foreign_entry``)."""
    managed = _managed_scripts(root)
    bad: list[str] = []
    for key, digest in sorted(managed.items()):
        try:
            data = _read_regular(root, key)
        except FileNotFoundError:
            bad.append(f"{key} (missing)")
            continue
        except OSError as exc:
            bad.append(f"{key} ({exc})")
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            bad.append(f"{key} (digest differs)")
    if bad:
        raise GeneratorRefused("surface_script_modified",
                               f"not the installed release's bytes: {', '.join(_head(bad))}", paths=_head(bad))
    try:
        if stat.S_ISLNK(os.lstat(root / SCRIPTS_DIR).st_mode):
            raise OSError("scripts is a link")
        names = [entry.name for entry in os.scandir(root / SCRIPTS_DIR)]
    except OSError as exc:
        raise GeneratorRefused("surface_unreadable", f"scripts/ cannot be listed ({exc})") from exc
    allowed = {key[len(SURFACE_PREFIXES[0]):] for key in managed} | {"__pycache__"}
    foreign = [name for name in names if name not in allowed and not name.startswith(".")]
    if foreign:
        raise GeneratorRefused("surface_foreign_entry",
                               f"scripts/ holds entries the installed release does not: {', '.join(_head(foreign))}",
                               paths=_head(foreign))


# ---------------------------------------------------------------------------
# D5a: what the generator will copy.
# ---------------------------------------------------------------------------


def _classify(root: Path, rel: str) -> str:
    """``missing``, ``link``, ``regular``, ``other`` or ``unreadable`` for ``<root>/<rel>``, examining each component
    with ``lstat`` and never following one: a link on the way, or as the last component, is ``link``."""
    current = root
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if not parts:
        return "other"
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = os.lstat(current)
        except (FileNotFoundError, NotADirectoryError):
            return "missing"
        except OSError:
            return "unreadable"
        if stat.S_ISLNK(info.st_mode):
            return "link"
        if index < len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                return "missing"
        else:
            return "regular" if stat.S_ISREG(info.st_mode) else "other"
    return "other"  # pragma: no cover - the loop returns


def _under(root_real: str, path: Path) -> bool:
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real + os.sep)


def _unsafe_reason(root: Path, root_real: str, rel: str) -> str | None:
    """Why the generator must not copy ``rel`` (a path as the generator would read it), or ``None``. A path that is
    absent, or not a regular file, is not copied by the generator and is accepted."""
    if rel.startswith("/"):
        return "absolute"
    if "\0" in rel or ".." in rel.split("/"):
        return "traversal"
    kind = _classify(root, rel)
    if kind == "link":
        return "link"
    if kind == "unreadable":
        return "unreadable"
    if kind == "regular" and not _under(root_real, root / rel):
        return "escapes"
    return None


def _context_lists(root: Path, stage: str, work_item_id: str) -> list[str]:
    """The context lists the generator may read, as repository-relative paths: for ``plan`` the plan inputs' list and,
    because the generator seeds an absent one from the live bundle, the live bundle's; for ``post-fix`` the live
    bundle's."""
    item = f"{ITEM_ROOT}/{work_item_id}"
    live = f"{item}/current/CONTEXT_FILES.txt"
    return [f"{item}/plan-inputs/CONTEXT_FILES.txt", live] if stage == "plan" else [live]


def _refuse_copy(source: str, path: str, reason: str) -> GeneratorRefused:
    return GeneratorRefused("copy_source_unsafe", f"{source} names {path!r}, which the generator would copy "
                            f"unsafely ({reason})", source=source, path=path, reason=reason)


def check_copy_sources(root: Path, stage: str, base_commit: str, work_item_id: str) -> None:
    """D5a: refuse (``copy_source_unsafe``, naming the list or the diff, the path and the reason) when a path the
    generator will copy does not resolve inside the repository. (a) The changed paths: the tracked diff against
    ``base_commit`` (with intent-to-add paths and renames both sides) and the untracked, non-ignored files, minus
    deletions and ``.ai-review/source/``. (b) The lines of the context list, read without following a link, minus
    ``.ai-review/source/`` lines. Reads only; the generator is never started."""
    if stage not in STAGES:
        raise ValueError(f"stage {stage!r} is not one of {STAGES}")
    root_real = os.path.realpath(root)
    try:
        changed = [path for status, path in gitrepo.diff_name_status(root, base_commit) if not status.startswith("D")]
        changed += gitrepo.untracked_files(root, no_optional_locks=True)
    except GitOperationError as exc:
        raise GeneratorRefused("copy_source_unsafe", f"the changed paths cannot be read ({exc.message})",
                               source="diff", reason="unreadable") from exc
    for path in changed:
        if path.startswith(_SOURCE_PROPOSALS):
            continue
        reason = _unsafe_reason(root, root_real, path)
        if reason:
            raise _refuse_copy("the changed-file list", path, reason)
    for list_path in _context_lists(root, stage, work_item_id):
        try:
            data = _read_regular(root, list_path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _refuse_copy(list_path, list_path, "unreadable" if "link" not in str(exc) else "link") from exc
        for raw in data.split(b"\n"):
            line = os.fsdecode(raw)
            if not line or line.startswith(_SOURCE_PROPOSALS):
                continue
            reason = _unsafe_reason(root, root_real, line)
            if reason:
                raise _refuse_copy(list_path, line, reason)


# ---------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------


def _recheck(root: Path, stage: str, base_commit: str, work_item_id: str) -> None:
    """D5: the tree and the copy sources, after the generator ran; a failure is ``surface_changed_during_run`` naming
    the first cause and its detail."""
    try:
        check_tree(root)
        check_copy_sources(root, stage, base_commit, work_item_id)
    except GeneratorRefused as exc:
        raise GeneratorRefused(
            "surface_changed_during_run",
            f"after the generator ran, {exc.cause}: {exc.detail}; the bundle is not trusted and is left for the "
            "operator to remove", was=exc.cause, was_detail=exc.detail) from exc


def _tail(data: bytes | str | None) -> str:
    """The last :data:`TAIL_BYTES` of ``data``, or a note when the whole stream holds a private-key armour line or the
    tail holds a closing one (a key whose ``BEGIN`` line falls just before the cut is still withheld)."""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    data = data or b""
    tail = data[-TAIL_BYTES:]
    if codex_review_role.ARMOUR.search(data) or _ARMOUR_END.search(tail):
        return _WITHHELD
    return tail.decode("utf-8", errors="replace")


def _streams(exc: WorkflowQueryError) -> tuple[bytes | str | None, bytes | str | None]:
    """The fullest output a failed start left: a timeout's own streams when the error carries them, otherwise the
    error's bounded tails."""
    cause = exc.__cause__
    if isinstance(cause, subprocess.TimeoutExpired):
        return cause.output, cause.stderr
    return exc.evidence.get("stdout_tail", ""), exc.evidence.get("stderr_tail", "")


def run(root: Path, *, stage: str, base_commit: str, work_item_id: str, carry_base: str | None = None,
        landed_head: str | None = None, progress: Callable[[str], None] | None = None,
        timeout: float = TIMEOUT_SECONDS) -> GeneratorRun:
    """The guarded run: D2, D3 and D5a before the generator, ``progress("checked")`` once they pass,
    ``progress("started")`` just before it starts, the generator ``scripts/prepare-ai-review.sh <base_commit> <stage>
    <work item>`` from the repository root under ``workflow_contract.run_in_place`` (D4), D5 after it, and the exit
    status last (a failed run that also changed the tree is reported as the change). Raises :class:`GeneratorRefused`
    with one cause of :data:`CAUSES`; a run that returns exited ``0`` and passed every check. ``stage`` is ``plan`` for
    the plan role and ``post-fix`` for the implementation role."""
    if stage not in STAGES:
        raise ValueError(f"stage {stage!r} is not one of {STAGES}")
    if carry_base is not None:
        check_landing(root, carry_base, landed_head)
    check_tree(root)
    check_copy_sources(root, stage, base_commit, work_item_id)
    if progress:
        progress("checked")
        progress("started")
    context = {
        "release": "managed", "query": "the review bundle generator", "work_item_id": work_item_id,
        "cwd": str(root), "argv": None, "returncode": None, "stdout_tail": "", "stderr_tail": "",
    }
    argv = ["bash", str(root / GENERATOR), base_commit, stage, work_item_id]
    try:
        completed = workflow_contract.run_in_place(root, argv, timeout=timeout, context=context)
    except WorkflowQueryError as exc:
        reason = exc.evidence.get("reason")
        cause = {"query_git_not_isolated": "git_not_isolated", "query_timeout": "generator_timed_out"}.get(
            reason, "generator_failed")
        out, err = _streams(exc)
        raise GeneratorRefused(cause, exc.message, reason=reason, stdout_tail=_tail(out),
                               stderr_tail=_tail(err)) from exc
    _recheck(root, stage, base_commit, work_item_id)
    stdout, stderr = _tail(completed.stdout), _tail(completed.stderr)
    if completed.returncode != 0:
        raise GeneratorRefused("generator_failed",
                               f"the generator exited {completed.returncode}: "
                               f"{(stderr or stdout).strip()[-500:] or '(no output)'}",
                               returncode=completed.returncode, stdout_tail=stdout, stderr_tail=stderr)
    return GeneratorRun(completed.returncode, stdout, stderr)
