"""Generation handoff primitive (capability 9, CP8).

A running Controller generation must never mutate or reload the
implementation it is currently executing (CP1's immutable-snapshot
mechanism). This module supplies the other half of that guarantee: the
means by which a running generation notices that the *approved* Controller
source has moved to a newer generation, and stops intentionally rather
than continuing to run stale code or somehow adopting the new bytes in
place.

``detect()`` is read-only -- it performs no write of its own. The caller
(``controller.cli``'s ``run`` loop) is the one that decides *when* to call
it (CP6's own orchestration boundary: after a job finishes, before the
next one starts, never mid-job) and, on a positive detection, calls
:func:`write_handoff_record` to publish ``<runtime_root>/handoff.json``
before stopping with exit code 50.

See ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "CP8 -- Generation
handoff primitive", for the full design this module implements.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

from controller import buildinfo, runtime, version
from controller.errors import GenerationHandoffPendingError, SourceSnapshotError

#: The single file whose committed content at the origin's `HEAD` this
#: module ever reads -- deliberately the same relative path
#: `controller.identity` resolves the *pinned* generation from, so both
#: halves of the comparison this module exists to make are read from the
#: same coordinate system.
_GENERATION_REL_PATH = "controller/GENERATION.json"
_VERSION_REL_PATH = "pyproject.toml"

#: ``identity.RUNTIME_KIND_PACKAGE``, spelled here so this module keeps
#: reading only the identity's attributes (see :func:`detect`).
_RUNTIME_KIND_PACKAGE = "package"


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )


@dataclasses.dataclass(frozen=True)
class Handoff:
    """A detected, pending generation handoff: the running generation is
    older than the one the origin source repository's committed `HEAD`
    now declares approved.

    ``from_commit`` is ``None`` exactly when the running identity's own
    ``source_commit`` is (a ``"worktree"``-kind pin, materialised from a
    dirty source under ``--allow-dirty-source``, or a package built without
    a clean commit) -- carried through unchanged, never invented.
    ``to_commit`` is ``None`` only for an installed package that was not
    built from a clean commit. The two versions are additive and ``None``
    where they cannot be read.
    """

    from_generation: int
    from_commit: str | None
    to_generation: int
    to_commit: str | None
    source_root: Path
    from_version: str | None = None
    to_version: str | None = None


def _read_approved_generation(source_root: Path) -> tuple[int, str]:
    """The approved generation number and commit, read from the origin
    source repository's own committed `HEAD` -- **never** the worktree.
    An uncommitted edit to ``controller/GENERATION.json`` is not an
    approved generation, and must not trigger a handoff, so unlike
    ``controller.identity``'s own generation read (which falls back to
    the worktree for this repository's own pre-commit bootstrap state),
    this one raises rather than falling back."""
    commit_result = _run_git(["rev-parse", "HEAD"], cwd=source_root)
    if commit_result.returncode != 0:
        raise SourceSnapshotError(
            f"cannot resolve HEAD in the origin source repository at {source_root} -- "
            f"{commit_result.stderr.strip()}",
            evidence={"raised_by": "detect", "source_root": str(source_root),
                      "git_stderr": commit_result.stderr.strip()},
        )
    commit = commit_result.stdout.strip()

    generation_result = _run_git(["show", f"HEAD:{_GENERATION_REL_PATH}"], cwd=source_root)
    if generation_result.returncode != 0:
        raise SourceSnapshotError(
            f"HEAD:{_GENERATION_REL_PATH} does not exist in the origin source repository at "
            f"{source_root} (commit {commit}) -- cannot resolve the approved generation number",
            evidence={"raised_by": "detect", "source_root": str(source_root), "commit": commit,
                      "git_stderr": generation_result.stderr.strip()},
        )
    try:
        data = json.loads(generation_result.stdout)
        generation = data["generation"]
        if not isinstance(generation, int):
            raise ValueError("generation is not an integer")
    except Exception as exc:
        raise SourceSnapshotError(
            f"HEAD:{_GENERATION_REL_PATH} in the origin source repository at {source_root} "
            f"(commit {commit}) is malformed -- cannot resolve the approved generation number",
            evidence={"raised_by": "detect", "source_root": str(source_root), "commit": commit,
                      "error": str(exc)},
        ) from exc
    return generation, commit


def _read_committed_version(source_root: Path) -> str | None:
    """The static ``[project].version`` ``HEAD:pyproject.toml`` declares,
    else the version the tags reachable from ``HEAD`` derive, or ``None``.
    Reported in the handoff record only -- never a refusal."""
    result = _run_git(["show", f"HEAD:{_VERSION_REL_PATH}"], cwd=source_root)
    if result.returncode != 0:
        return None
    try:
        static = version.parse_static_version(result.stdout, where=f"HEAD:{_VERSION_REL_PATH}")
        return static if static is not None else version.tag_version(source_root, "HEAD")
    except ValueError:
        return None


def _read_installed_generation(package_root: Path) -> tuple[int, str | None, str]:
    """A package runtime's approved generation: the *currently installed*
    package's own ``controller/GENERATION.json`` under ``package_root`` (the
    site-packages directory the running snapshot was extracted from).
    Installing a package is that runtime's approval act. Returns
    ``(generation, commit, version)`` -- the commit is the build's
    ``source_commit`` when it was built clean, else ``None``. A missing or
    unreadable installation fails closed. No Git runs here."""
    generation_path = package_root / _GENERATION_REL_PATH
    build_path = package_root / "controller" / buildinfo.BUILD_INFO_NAME
    try:
        generation_data = json.loads(generation_path.read_bytes())
        generation = generation_data["generation"]
        if not isinstance(generation, int):
            raise ValueError("generation is not an integer")
        raw_build = json.loads(build_path.read_bytes())
        installed_version = raw_build.get("version") if isinstance(raw_build, dict) else None
        build = buildinfo.validate_build_info(raw_build, expected_version=installed_version)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SourceSnapshotError(
            f"the installed package at {package_root} cannot be read -- cannot resolve the "
            f"approved generation number ({exc})",
            evidence={"raised_by": "detect", "source_root": str(package_root), "error": str(exc)},
        ) from exc
    commit = build.source_commit if build.source_dirty is False else None
    return generation, commit, build.version


def detect(identity: Any, source_root: Path) -> Handoff | None:
    """Compare the running (pinned) generation against the *approved*
    generation the origin source repository's committed ``HEAD`` now
    declares, resolved fresh on every call (never cached -- the whole
    point is to notice a change that happened *after* this process
    pinned).

    ``identity`` is an already-resolved
    ``controller.identity.ControllerIdentity`` (typed ``Any`` here, the
    same deliberate shadow-avoidance ``controller.job.execute_step``
    uses, since this function only ever reads its attributes and never
    calls back into ``controller.identity``). Unreachable at
    ``source_kind == "unpinned"`` in production -- every command that
    reaches an orchestration boundary is one CP1's positive guard already
    requires a pinned snapshot for -- but an unpinned identity (a
    ``None`` ``generation``) still fails closed here rather than being
    compared against ``None``.

    For a ``"package"`` runtime the approved generation is the installed
    package's under ``source_root`` (:func:`_read_installed_generation`) and
    the source checkout is never consulted; for a ``"source"`` runtime it is
    the origin checkout's committed ``HEAD``.

    Three outcomes:

    - **approved > pinned** -- returns a :class:`Handoff` describing the
      move. The caller stops the run loop and publishes it.
    - **approved == pinned** -- returns ``None``. A different commit at
      the same generation is ordinary in-generation development, not a
      handoff; the run loop continues.
    - **approved < pinned** -- raises ``GenerationHandoffPendingError``:
      the source repository declares an older generation than the one
      running, which means an assumption elsewhere is wrong.
    """
    if identity.generation is None:
        raise SourceSnapshotError(
            "detect() requires a pinned identity -- an unpinned process has resolved no "
            "generation to compare against",
            evidence={"raised_by": "detect", "source_kind": identity.source_kind},
        )

    if getattr(identity, "runtime_kind", None) == _RUNTIME_KIND_PACKAGE:
        approved_generation, approved_commit, approved_version = _read_installed_generation(source_root)
    else:
        approved_generation, approved_commit = _read_approved_generation(source_root)
        approved_version = None
    pinned_generation = identity.generation

    if approved_generation > pinned_generation:
        if getattr(identity, "runtime_kind", None) != _RUNTIME_KIND_PACKAGE:
            approved_version = _read_committed_version(source_root)
        return Handoff(
            from_generation=pinned_generation,
            from_commit=identity.source_commit,
            to_generation=approved_generation,
            to_commit=approved_commit,
            source_root=source_root,
            from_version=getattr(identity, "version", None),
            to_version=approved_version,
        )
    if approved_generation == pinned_generation:
        return None
    raise GenerationHandoffPendingError(
        f"the source repository at {source_root} declares generation {approved_generation}, "
        f"older than the running generation {pinned_generation} -- the source repository "
        f"declares an older generation than the one running",
        evidence={
            "source_root": str(source_root),
            "pinned_generation": pinned_generation,
            "pinned_commit": identity.source_commit,
            "approved_generation": approved_generation,
            "approved_commit": approved_commit,
        },
    )


def write_handoff_record(
    runtime_root: Path,
    handoff: Handoff,
    *,
    jobs_complete: list[str],
    jobs_open: list[str],
    next_generation_command: str,
    now: str,
) -> dict:
    """Publish ``<runtime_root>/handoff.json`` -- precisely the milestone
    brief's own list: running generation and commit, approved generation
    and commit, the source root, the timestamp, the list of job records
    that are complete and the list still open, and the exact command to
    start the next generation.

    ``jobs_complete``/``jobs_open`` are supplied by the caller rather than
    recomputed here: classifying a job record's own status as terminal or
    not is ``controller.job``'s own closed enumeration
    (``TERMINAL_STATUSES``/``NON_TERMINAL_STATUSES``), and ``job`` is
    later than ``handoff`` in the package's own dependency order (``job ->
    {..., handoff} -> decision -> ...``), so this module never imports it.
    """
    record = {
        "schema_version": 1,
        "running": {"generation": handoff.from_generation, "commit": handoff.from_commit,
                    "version": handoff.from_version},
        "approved": {"generation": handoff.to_generation, "commit": handoff.to_commit,
                     "version": handoff.to_version},
        "source_root": str(handoff.source_root),
        "detected_at": now,
        "jobs_complete": sorted(jobs_complete),
        "jobs_open": sorted(jobs_open),
        "next_generation_command": next_generation_command,
    }
    runtime.write_json(runtime_root, "handoff.json", record)
    return record
