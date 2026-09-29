"""The pinned, immutable Controller source identity.

Every run that may write a job record or launch a worker executes from an
immutable, content-verified snapshot of the source tree, so a later import
or resource read cannot reach the mutable origin worktree at all. See
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, CP1's identity block, for the
full design this module implements.

The late resource reads allowlisted anywhere in ``controller/*.py`` are all
here, all performed before any orchestration: ``pin()``'s read of
``<source_root>/SOURCE_PIN.json``, the ``generation_source: "worktree"``
fallback read of the *origin* tree's ``controller/GENERATION.json``,
``resolve_runtime()``'s read of the running package's own
``controller/BUILD_INFO.json``, and ``_extract_package()``'s copy of the
installed package.

Which runtime kind is running -- ``source``, ``package`` or
``unidentified`` -- is decided only by the running package's own files and,
for ``source``, Git itself. Installer metadata (``direct_url.json``) is
never read. See ``docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md``,
"Runtime identity".
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path

from controller import buildinfo, runtime, version
from controller.errors import DirtyControllerSourceError, SourceSnapshotError

#: The pathspec every snapshot is scoped to -- fixed on both the clean and
#: dirty extraction branches so the two kinds are byte-comparable.
_SNAPSHOT_DIRS = ("controller",)
_SNAPSHOT_FILES = ("pyproject.toml",)
_SOURCE_PIN_NAME = "SOURCE_PIN.json"
_GENERATION_REL_PATH = "controller/GENERATION.json"

#: The single environment variable a re-execed child inherits from its
#: parent. Internal to the re-exec -- never an operator input.
EXEC_HANDOFF_ENV = "WORKFLOW_CONTROLLER_EXEC_HANDOFF"

#: The closed `source_kind` enumeration. `package` is a snapshot extracted
#: from an installed package rather than from a Git checkout.
SOURCE_KIND_COMMIT = "commit"
SOURCE_KIND_WORKTREE = "worktree"
SOURCE_KIND_UNPINNED = "unpinned"
SOURCE_KIND_PACKAGE = "package"

#: The closed `runtime_kind` enumeration: what kind of code is running.
RUNTIME_KIND_SOURCE = "source"
RUNTIME_KIND_PACKAGE = "package"
RUNTIME_KIND_UNIDENTIFIED = "unidentified"

_GENERATION_SOURCE_PACKAGE = "package"

#: The version an ``unidentified`` runtime reports when neither version
#: reader succeeds for its code root.
UNKNOWN_VERSION = "unknown"


@dataclasses.dataclass(frozen=True)
class ControllerIdentity:
    """What executed, and how it is provably identified.

    ``generation`` is ``None`` for an ``"unpinned"`` identity: an unpinned
    process has not materialised a snapshot, so it has not resolved a
    generation number from the origin's committed ``HEAD`` the way
    ``materialise()`` does. Read-only commands do not need it; a later
    checkpoint (CP8) resolves the origin's generation directly, from
    ``origin_source_root``, when it needs to compare against a running
    pin's own.
    """

    generation: int | None
    source_root: Path
    origin_source_root: Path | None
    source_kind: str
    source_commit: str | None
    tree_digest: str | None
    generation_source: str | None
    pinned_at: str
    #: Additive (release-runtime-observability CP2). A pin written before
    #: these fields existed can only be a source snapshot, hence the default.
    runtime_kind: str = RUNTIME_KIND_SOURCE
    #: The resolved runtime version -- the caller resolves it; there is no
    #: import-time default (trunk-branch-pr-release CP1).
    version: str = dataclasses.field(kw_only=True)
    #: The validated ``BUILD_INFO.json`` dict for a ``package`` runtime.
    build: dict | None = None
    #: Why an ``unidentified`` runtime is not ``source`` or ``package``.
    runtime_reason: str | None = None

    def resolution(self) -> RuntimeResolution:
        return RuntimeResolution(
            runtime_kind=self.runtime_kind, code_root=self.source_root, build=self.build,
            reason=self.runtime_reason, version=self.version,
        )


@dataclasses.dataclass(frozen=True)
class RuntimeResolution:
    """``resolve_runtime``'s answer: the kind, the validated build info
    (``package`` only), for ``unidentified`` the reason, and the version
    (``UNKNOWN_VERSION`` only for an ``unidentified`` runtime no reader
    could read)."""

    runtime_kind: str
    code_root: Path
    build: dict | None
    reason: str | None
    version: str


_cached_identity: ControllerIdentity | None = None


def _reset_for_tests() -> None:
    """Discard the cached pin. Named so it cannot be mistaken for
    production API -- there is no public way to re-resolve a pin once a
    process has one; this exists only so test processes can start clean."""
    global _cached_identity
    _cached_identity = None


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )


def _run_git_binary(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    """Like ``_run_git``, but captures stdout as raw bytes -- required for
    ``git archive``, whose stdout is a binary tar stream that
    ``text=True``'s UTF-8 decoding would risk corrupting."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=False, check=False
    )


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_exec_handoff() -> dict | None:
    """Read (never delete) ``WORKFLOW_CONTROLLER_EXEC_HANDOFF``. Deletion is
    ``cli.main``'s own responsibility, performed once, at entry, before
    anything can be launched -- this function may be called more than once
    (by ``pin()``'s own cross-check and by ``cli.main``'s exec-depth read)
    before that deletion happens."""
    raw = os.environ.get(EXEC_HANDOFF_ENV)
    if not raw:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Tree digest: a mapping, not a concatenation.
# ---------------------------------------------------------------------------


def compute_tree_digest(directory: Path) -> str:
    """SHA-256 over the canonical JSON serialisation of
    ``{<POSIX relative path>: {"mode": "100755"|"100644", "sha256": <hex>}}``,
    built over every file under ``directory`` except ``SOURCE_PIN.json`` at
    its root. Total over the directory: this is the digest that identifies
    the bytes that can execute, so nothing under it is silently excluded
    besides that one, stated exclusion."""
    mapping: dict[str, dict[str, str]] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        if rel == _SOURCE_PIN_NAME:
            continue
        mode = "100755" if os.access(path, os.X_OK) else "100644"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        mapping[rel] = {"mode": mode, "sha256": digest}
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# Dirty scoping and the generation-source rule.
# ---------------------------------------------------------------------------


def _is_dirty(origin: Path) -> bool:
    """Dirty iff ``git status --porcelain -- controller pyproject.toml``
    produces any output -- scoped to the snapshot's own pathspec, never the
    whole worktree. ``--no-optional-locks`` keeps the probe from refreshing
    the index, so read-only callers (``--version``, ``status``) write
    nothing into the checkout."""
    result = _run_git(
        ["--no-optional-locks", "status", "--porcelain", "--", *_SNAPSHOT_DIRS, *_SNAPSHOT_FILES],
        cwd=origin,
    )
    return bool(result.stdout.strip())


def _parse_generation(raw: str, *, source_desc: str) -> int:
    try:
        data = json.loads(raw)
        generation = data["generation"]
        if not isinstance(generation, int):
            raise ValueError("generation is not an integer")
    except Exception as exc:
        raise SourceSnapshotError(
            f"{source_desc} is malformed -- cannot read a generation number",
            evidence={"raised_by": "materialise", "source": source_desc, "error": str(exc)},
        ) from exc
    return generation


def _read_generation(origin: Path) -> tuple[int, str]:
    """The generation number is read from the origin's committed `HEAD`,
    never the worktree -- except the one stated fallback: `HEAD` carries no
    `controller/GENERATION.json` at all yet (this repository's own state
    during CP1). A malformed file at either source is a refusal, never a
    default of 1."""
    result = _run_git(["show", f"HEAD:{_GENERATION_REL_PATH}"], cwd=origin)
    if result.returncode == 0:
        return _parse_generation(result.stdout, source_desc=f"HEAD:{_GENERATION_REL_PATH}"), "head"

    worktree_path = origin / _GENERATION_REL_PATH
    if not worktree_path.exists():
        raise SourceSnapshotError(
            f"neither HEAD:{_GENERATION_REL_PATH} nor the worktree file exists -- cannot "
            f"resolve a generation number",
            evidence={
                "raised_by": "materialise",
                "git_stderr": result.stderr.strip(),
                "worktree_path": str(worktree_path),
            },
        )
    raw = worktree_path.read_text()
    return _parse_generation(raw, source_desc=str(worktree_path)), "worktree"


# ---------------------------------------------------------------------------
# resolve_runtime(): which kind of code is running.
# ---------------------------------------------------------------------------


def _build_info_path(code_root: Path) -> Path:
    return code_root / "controller" / buildinfo.BUILD_INFO_NAME


def _source_probe_failure(code_root: Path) -> str | None:
    """``None`` iff ``code_root`` is the top level of a Git work tree that
    tracks ``controller/__init__.py``; otherwise why not. The two probes are
    the only Git calls runtime resolution makes. An ``OSError`` from
    launching ``git`` (no ``git`` on ``PATH`` included) is an answer, never
    propagated: ``_run_git`` does not catch it."""
    try:
        toplevel = _run_git(["rev-parse", "--show-toplevel"], cwd=code_root)
        if toplevel.returncode != 0:
            return f"{code_root} is not a Git work tree: {toplevel.stderr.strip()}"
        top = Path(toplevel.stdout.strip())
        if top.resolve() != code_root.resolve():
            return f"the Git work tree containing {code_root} has its top level at {top}, not {code_root}"
        tracked = _run_git(["ls-files", "--error-unmatch", "controller/__init__.py"], cwd=code_root)
        if tracked.returncode != 0:
            return f"controller/__init__.py is not tracked in the Git checkout at {code_root}"
    except OSError as exc:
        return f"git could not be run to confirm {code_root} is a source checkout ({exc})"
    return None


def _best_effort_version(code_root: Path) -> str:
    """An ``unidentified`` runtime's version: whichever of the two readers
    succeeds for ``code_root``, else ``UNKNOWN_VERSION``. Reported only --
    an unidentified runtime cannot launch workers."""
    for reader in (version.source_version, version.package_version):
        try:
            return reader(code_root)
        except ValueError:
            continue
    return UNKNOWN_VERSION


def resolve_runtime(code_root: Path) -> RuntimeResolution:
    """Classify the code at ``code_root`` (``Path(__file__).parent.parent``
    of the running package) as ``package``, ``source`` or ``unidentified``.

    A ``controller/BUILD_INFO.json`` next to a ``.git`` is ambiguous and
    fail-closed: ``unidentified``, decided before any validation and
    without Git. Otherwise build info means ``package`` (if it validates
    against the installed distribution's metadata), a ``.git`` means
    ``source`` (if both probes agree and a version resolves), and anything
    else is ``unidentified``. The ``SOURCE_PIN.json``
    branch (a pinned snapshot) is ``pin()``'s own, checked before this.

    The version comes from where the code lives: a ``package`` runtime's is
    the metadata of the distribution installed in ``code_root`` and must
    equal ``BUILD_INFO.json``'s; a ``source`` runtime's is
    ``<code_root>/pyproject.toml``'s static version, else the one its tags
    derive (``version.source_version``), and never distribution metadata."""
    code_root = Path(code_root)
    build_path = _build_info_path(code_root)
    has_build_info = os.path.lexists(build_path)
    has_git = os.path.lexists(code_root / ".git")

    def unidentified(reason: str) -> RuntimeResolution:
        return RuntimeResolution(RUNTIME_KIND_UNIDENTIFIED, code_root, None, reason,
                                 _best_effort_version(code_root))

    if has_build_info and has_git:
        return unidentified(
            f"source checkout {code_root} contains controller/{buildinfo.BUILD_INFO_NAME}; "
            f"delete it to run from source"
        )
    if has_build_info:
        try:
            package_version = version.package_version(code_root)
        except ValueError as exc:
            return unidentified(f"the installed package at {code_root} has no readable "
                                f"distribution metadata: {exc}")
        try:
            raw = json.loads(build_path.read_bytes())
        except (OSError, ValueError) as exc:
            return unidentified(f"{build_path} is not valid build info: {exc}")
        recorded = raw.get("version") if isinstance(raw, dict) else None
        if isinstance(recorded, str) and recorded != package_version:
            return unidentified(
                f"the installed package at {code_root} is partially upgraded: its distribution "
                f"metadata says {package_version} but {build_path} says {recorded} (does not match)"
            )
        try:
            build = buildinfo.validate_build_info(raw, expected_version=package_version)
        except ValueError as exc:
            return unidentified(f"{build_path} is not valid build info: {exc}")
        return RuntimeResolution(RUNTIME_KIND_PACKAGE, code_root, build.to_dict(), None,
                                 package_version)
    if has_git:
        failure = _source_probe_failure(code_root)
        if failure is not None:
            return unidentified(failure)
        try:
            source_version = version.source_version(code_root)
        except ValueError as exc:
            return unidentified(f"the source checkout at {code_root} has no readable version: {exc}")
        return RuntimeResolution(RUNTIME_KIND_SOURCE, code_root, None, None, source_version)
    return unidentified(
        f"{code_root} is neither an installed workflow-controller package (no "
        f"controller/{buildinfo.BUILD_INFO_NAME}) nor a Git checkout"
    )


def _package_commit(build: dict) -> str | None:
    """A package's commit identifies it only when it was built clean."""
    return build["source_commit"] if build["source_dirty"] is False else None


# ---------------------------------------------------------------------------
# The recorded runtime block and its one-line description.
# ---------------------------------------------------------------------------


def _runtime_block(*, runtime_kind: str, runtime_version: str, source_kind: str,
                   source_commit: str | None, tree_digest: str | None, build: dict | None,
                   generation: int | None) -> dict:
    build = build or {}
    return {
        "runtime_kind": runtime_kind,
        "version": runtime_version,
        "source_kind": source_kind,
        "source_commit": source_commit,
        "tree_digest": tree_digest,
        "package_digest": build.get("package_digest"),
        "build_origin": build.get("build_origin"),
        "release_tag": build.get("release_tag"),
        "generation": generation,
    }


def runtime_record(ident: ControllerIdentity) -> dict:
    """The ``controller_runtime`` block written into ``SOURCE_PIN.json``,
    ``identity.json`` and every job record. Nothing validates against it:
    a record without it stays valid."""
    return _runtime_block(
        runtime_kind=ident.runtime_kind, runtime_version=ident.version,
        source_kind=ident.source_kind, source_commit=ident.source_commit,
        tree_digest=ident.tree_digest, build=ident.build, generation=ident.generation,
    )


def _short(commit: str | None) -> str | None:
    return commit[:12] if commit else None


def describe_runtime(ident: ControllerIdentity) -> str:
    """One line naming what is running, shared by ``--version`` (line 2)
    and ``status`` (its first line). For an unpinned source checkout this
    asks Git whether the snapshot pathspec is dirty."""
    if ident.runtime_kind == RUNTIME_KIND_PACKAGE and ident.build is not None:
        build = ident.build
        commit = _short(build["source_commit"])
        if build["build_origin"] == buildinfo.BUILD_ORIGIN_RELEASE:
            return (f"package (release {build['release_tag']}; built from {commit}; "
                    f"package {build['package_digest'][:12]})")
        if build["source_dirty"] is None:
            return "package (local build, unknown provenance)"
        suffix = ", uncommitted changes" if build["source_dirty"] else ""
        return f"package (local build from {commit}{suffix})"
    if ident.runtime_kind == RUNTIME_KIND_SOURCE:
        checkout = ident.origin_source_root or ident.source_root
        if ident.source_kind == SOURCE_KIND_UNPINNED:
            try:
                dirty = _is_dirty(checkout)
            except OSError:
                dirty = False
        else:
            dirty = ident.source_kind == SOURCE_KIND_WORKTREE
        at = f" @ {_short(ident.source_commit)}" if ident.source_commit else ""
        suffix = ", uncommitted changes" if dirty else ""
        return f"source ({checkout}{at}{suffix})"
    return f"unidentified ({ident.runtime_reason or 'no runtime identity could be established'})"


# ---------------------------------------------------------------------------
# materialise()
# ---------------------------------------------------------------------------


def _extract_clean(origin: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    archive = _run_git_binary(
        ["archive", "HEAD", "--", *_SNAPSHOT_DIRS, *_SNAPSHOT_FILES], cwd=origin
    )
    if archive.returncode != 0:
        raise SourceSnapshotError(
            f"git archive failed: {archive.stderr.decode(errors='replace').strip()}",
            evidence={"raised_by": "materialise",
                      "git_stderr": archive.stderr.decode(errors="replace").strip()},
        )
    tar = subprocess.run(
        ["tar", "-x", "-C", str(dest)], input=archive.stdout, capture_output=True, check=False,
    )
    if tar.returncode != 0:
        raise SourceSnapshotError(
            f"extracting the archived snapshot failed: {tar.stderr!r}",
            evidence={"raised_by": "materialise", "tar_stderr": str(tar.stderr)},
        )


def _extract_dirty(origin: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    listing = _run_git(
        ["ls-files", "--cached", "--others", "--exclude-standard", "--",
         *_SNAPSHOT_DIRS, *_SNAPSHOT_FILES],
        cwd=origin,
    )
    for rel in listing.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        src = origin / rel
        if not src.is_file():
            continue
        target = dest / rel
        runtime.assert_contained(dest, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        if os.access(src, os.X_OK):
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _verify_snapshot_directory(dest: Path, expected_digest: str) -> bool:
    if not dest.is_dir():
        return False
    recomputed = compute_tree_digest(dest)
    if recomputed != expected_digest or dest.name != expected_digest:
        return False
    pin_path = dest / _SOURCE_PIN_NAME
    pin_data = runtime.read_json(pin_path)
    if pin_data is None or pin_data.get("tree_digest") != expected_digest:
        return False
    return True


def _publish_source_pin(source_dir: Path, dest: Path, pin_body: dict) -> None:
    """Rewrite ``SOURCE_PIN.json`` on reuse. The temporary is written
    *outside* the snapshot directory -- ``<source_dir>/.SOURCE_PIN.<token>.tmp``
    -- and `os.replace`d in, so an interruption between `fsync` and the
    rename can never leave a stray file inside the digested set."""
    tmp = source_dir / f".{_SOURCE_PIN_NAME}.{secrets.token_hex(8)}.tmp"
    runtime.assert_contained(source_dir, tmp)
    data = json.dumps(pin_body, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        runtime.assert_contained(source_dir, dest)
        os.replace(tmp, dest / _SOURCE_PIN_NAME)
    finally:
        tmp.unlink(missing_ok=True)


def _extract_package(origin: Path, dest: Path) -> None:
    """Copy the installed ``<origin>/controller/`` into ``dest/controller/``
    without Git: regular files only, skipping ``__pycache__/`` and
    ``*.pyc``. A symlink or special file anywhere under it is a refusal --
    the snapshot must hold exactly the bytes the package digest covers."""
    package_dir = origin / "controller"
    try:
        _copy_package_tree(package_dir, dest)
    except OSError as exc:
        raise SourceSnapshotError(
            f"copying the installed package at {package_dir} failed: {exc}",
            evidence={"raised_by": "materialise", "path": str(package_dir), "os_error": str(exc)},
        ) from exc


def _copy_package_tree(package_dir: Path, dest: Path) -> None:
    if not stat.S_ISDIR(os.lstat(package_dir).st_mode):
        raise SourceSnapshotError(
            f"{package_dir} is not a directory -- cannot snapshot the installed package",
            evidence={"raised_by": "materialise", "path": str(package_dir)},
        )
    target_root = dest / "controller"
    def fail(exc: OSError) -> None:
        raise exc

    for dirpath, dirnames, filenames in os.walk(package_dir, onerror=fail):
        current = Path(dirpath)
        kept: list[str] = []
        for name in sorted(dirnames):
            if name == "__pycache__":
                continue
            if not stat.S_ISDIR(os.lstat(current / name).st_mode):
                raise SourceSnapshotError(
                    f"{current / name} in the installed package is a symlink -- refusing to "
                    f"snapshot it",
                    evidence={"raised_by": "materialise", "path": str(current / name)},
                )
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if name.endswith(".pyc"):
                continue
            src = current / name
            if not stat.S_ISREG(os.lstat(src).st_mode):
                raise SourceSnapshotError(
                    f"{src} in the installed package is not a regular file (a symlink or "
                    f"special file) -- refusing to snapshot it",
                    evidence={"raised_by": "materialise", "path": str(src)},
                )
            target = target_root / src.relative_to(package_dir)
            runtime.assert_contained(dest, target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            target.chmod(0o644)


def _read_package_generation(snapshot: Path) -> int:
    path = snapshot / _GENERATION_REL_PATH
    try:
        raw = path.read_text()
    except OSError as exc:
        raise SourceSnapshotError(
            f"the installed package has no readable {_GENERATION_REL_PATH} -- cannot resolve "
            f"a generation number",
            evidence={"raised_by": "materialise", "path": str(path), "error": str(exc)},
        ) from exc
    return _parse_generation(raw, source_desc=f"the installed package's {_GENERATION_REL_PATH}")


def _snapshot_version(snapshot: Path, runtime_kind: str, *, origin: Path,
                      commit: str | None) -> str:
    """The version ``SOURCE_PIN.json`` records, never read from this
    process: a source snapshot's own static ``pyproject.toml`` version (so a
    dirty snapshot reports what it holds), else -- a snapshot has no
    ``.git`` -- the version the origin checkout's tags derive at the
    snapshot's ``commit`` (``HEAD`` for a dirty snapshot); or a package
    snapshot's own ``controller/BUILD_INFO.json`` (a package snapshot has no
    ``*.dist-info``; ``resolve_runtime`` already checked that file against
    the distribution metadata)."""
    try:
        if runtime_kind == RUNTIME_KIND_PACKAGE:
            path = _build_info_path(snapshot)
            try:
                raw = json.loads(path.read_bytes())
            except (OSError, ValueError) as exc:
                raise ValueError(f"cannot read {path}: {exc}") from None
            value = raw.get("version") if isinstance(raw, dict) else None
            if not isinstance(value, str) or not version.SEMVER_RE.fullmatch(value):
                raise ValueError(f"{path} version is {value!r}, not MAJOR.MINOR.PATCH")
            return value
        static = version.static_pyproject_version(snapshot)
        return static if static is not None else version.tag_version(origin, commit or "HEAD")
    except ValueError as exc:
        raise SourceSnapshotError(
            f"cannot resolve the snapshot's version: {exc}",
            evidence={"raised_by": "materialise", "snapshot": str(snapshot), "error": str(exc)},
        ) from exc


def _pinned_version(data: dict, source_root: Path) -> str:
    """A pinned child's version: ``SOURCE_PIN.json``'s ``version``, with no
    fallback. Every pin ``materialise`` writes carries it."""
    value = data.get("version")
    if not isinstance(value, str) or not version.SEMVER_RE.fullmatch(value):
        raise SourceSnapshotError(
            f"the snapshot at {source_root} records no valid version in {_SOURCE_PIN_NAME} "
            f"({value!r}) -- re-run from the origin to re-materialise it",
            evidence={"raised_by": "pin", "source_root": str(source_root), "version": value},
        )
    return value


def _refuse_unverifiable_package(origin: Path, build: dict, *, allow_dirty: bool) -> None:
    """A package built from uncommitted changes, or without provenance, is
    not identified by a commit -- the same rule a dirty checkout follows."""
    if build["source_dirty"] is False or allow_dirty:
        return
    cause = ("built from uncommitted changes" if build["source_dirty"] is True
             else "built without verifiable source provenance")
    raise DirtyControllerSourceError(
        f"the installed package at {origin} was {cause} -- pass --allow-dirty-source to run it",
        evidence={"origin_source_root": str(origin), "runtime_kind": RUNTIME_KIND_PACKAGE,
                  "source_dirty": build["source_dirty"]},
    )


def materialise(
    origin_source_root: Path, runtime_root: Path, *, allow_dirty: bool = False,
    resolution: RuntimeResolution | None = None,
) -> Path:
    """Build (or reuse) the immutable, content-addressed snapshot a pinned
    run executes from. Returns the published snapshot directory.

    Dispatches on the runtime kind -- ``resolution``, or
    ``resolve_runtime(origin_source_root)`` when the caller has none:
    ``source`` archives or copies the checkout, ``package`` copies the
    installed package and verifies its digest, ``unidentified`` refuses.
    Raises ``DirtyControllerSourceError`` for a dirty tree or unverifiable
    package with no ``--allow-dirty-source``, and ``SourceSnapshotError``
    (``raised_by: "materialise"``) for every other refusal this function
    can reach."""
    origin_source_root = origin_source_root.resolve()
    if resolution is None:
        resolution = resolve_runtime(origin_source_root)
    runtime_kind = resolution.runtime_kind
    build = resolution.build

    if runtime_kind == RUNTIME_KIND_PACKAGE:
        _refuse_unverifiable_package(origin_source_root, build, allow_dirty=allow_dirty)
        dirty = False
    elif runtime_kind == RUNTIME_KIND_SOURCE:
        dirty = _is_dirty(origin_source_root)
        if dirty and not allow_dirty:
            raise DirtyControllerSourceError(
                f"{origin_source_root} has uncommitted changes under controller/ or "
                f"pyproject.toml -- pass --allow-dirty-source to run from the working tree",
                evidence={"origin_source_root": str(origin_source_root)},
            )
    else:
        raise SourceSnapshotError(
            f"cannot run from {origin_source_root}: {resolution.reason} -- install a wheel "
            f"built by this project, or run from a checkout",
            evidence={"raised_by": "materialise", "runtime_kind": runtime_kind,
                      "origin_source_root": str(origin_source_root), "reason": resolution.reason},
        )

    source_dir = runtime_root / "source"
    runtime.assert_contained(runtime_root, source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = source_dir / f".materialise-{secrets.token_hex(8)}.tmp"
    runtime.assert_contained(source_dir, tmp_dir)
    try:
        if runtime_kind == RUNTIME_KIND_PACKAGE:
            _extract_package(origin_source_root, tmp_dir)
            # The copy is checked, not the installed tree, so a concurrent
            # reinstall cannot change the bytes after the check.
            copied_digest = buildinfo.compute_package_digest(tmp_dir / "controller")
            if copied_digest != build["package_digest"]:
                raise SourceSnapshotError(
                    f"the installed package at {origin_source_root} does not match its own build "
                    f"info: package digest {copied_digest} != recorded {build['package_digest']} "
                    f"-- it was modified after it was built, or partially upgraded",
                    evidence={"raised_by": "materialise", "origin_source_root": str(origin_source_root),
                              "recomputed": copied_digest, "recorded": build["package_digest"]},
                )
            generation = _read_package_generation(tmp_dir)
            generation_source = _GENERATION_SOURCE_PACKAGE
            source_kind = SOURCE_KIND_PACKAGE
            source_commit = _package_commit(build)
        elif dirty:
            _extract_dirty(origin_source_root, tmp_dir)
            source_kind = SOURCE_KIND_WORKTREE
            source_commit = None
        else:
            _extract_clean(origin_source_root, tmp_dir)
            source_kind = SOURCE_KIND_COMMIT
            source_commit = _run_git(["rev-parse", "HEAD"], cwd=origin_source_root).stdout.strip()

        tree_digest = compute_tree_digest(tmp_dir)
        if runtime_kind == RUNTIME_KIND_SOURCE:
            generation, generation_source = _read_generation(origin_source_root)
        pin_version = _snapshot_version(tmp_dir, runtime_kind, origin=origin_source_root,
                                        commit=source_commit)

        dest = source_dir / tree_digest
        runtime.assert_contained(source_dir, dest)
        pin_body = {
            "schema_version": 1,
            "runtime_kind": runtime_kind,
            "version": pin_version,
            "build": build,
            "source_kind": source_kind,
            "source_commit": source_commit,
            "origin_source_root": str(origin_source_root),
            "generation": generation,
            "generation_source": generation_source,
            "tree_digest": tree_digest,
            "materialised_at": _now(),
            "controller_runtime": _runtime_block(
                runtime_kind=runtime_kind, runtime_version=pin_version,
                source_kind=source_kind, source_commit=source_commit, tree_digest=tree_digest,
                build=build, generation=generation,
            ),
        }

        if dest.exists():
            if not _verify_snapshot_directory(dest, tree_digest):
                raise SourceSnapshotError(
                    f"snapshot directory {dest} exists but fails re-verification -- its "
                    f"content no longer matches its own name",
                    evidence={"raised_by": "materialise", "directory": str(dest)},
                )
            _publish_source_pin(source_dir, dest, pin_body)
            return dest

        (tmp_dir / _SOURCE_PIN_NAME).write_text(
            json.dumps(pin_body, indent=2, sort_keys=True) + "\n"
        )
        os.replace(tmp_dir, dest)
        return dest
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# pin() / current()
# ---------------------------------------------------------------------------


def pin() -> ControllerIdentity:
    """Resolve this process's identity from where the running code actually
    is (``Path(__file__).parent.parent``), cache it, and return it. Cached
    in a module-level singleton -- a second call never re-resolves."""
    global _cached_identity
    if _cached_identity is not None:
        return _cached_identity

    source_root = Path(__file__).resolve().parent.parent
    pin_path = source_root / _SOURCE_PIN_NAME

    if pin_path.exists():
        data = runtime.read_json(pin_path)
        recomputed = compute_tree_digest(source_root)
        if recomputed != data.get("tree_digest") or recomputed != source_root.name:
            raise SourceSnapshotError(
                f"the snapshot at {source_root} no longer matches its own recorded "
                f"digest -- it may have been executed without -B, or tampered with",
                evidence={"raised_by": "pin", "source_root": str(source_root),
                          "recomputed": recomputed, "recorded": data.get("tree_digest")},
            )
        handoff = read_exec_handoff()
        if handoff is not None:
            observed = (data.get("source_kind"), data.get("source_commit"))
            carried = (handoff.get("source_kind"), handoff.get("source_commit"))
            if observed != carried:
                raise SourceSnapshotError(
                    "the reused snapshot's own source_kind/source_commit disagree with "
                    "what this run's parent recorded materialising it",
                    evidence={"raised_by": "pin", "observed": list(observed),
                              "carried": list(carried)},
                )
        identity = ControllerIdentity(
            generation=data["generation"],
            source_root=source_root,
            origin_source_root=Path(data["origin_source_root"]) if data.get("origin_source_root") else None,
            source_kind=data["source_kind"],
            source_commit=data.get("source_commit"),
            tree_digest=recomputed,
            generation_source=data.get("generation_source"),
            pinned_at=_now(),
            # A pin without `runtime_kind` was written by an earlier
            # Controller for its own (source) snapshot.
            runtime_kind=data.get("runtime_kind", RUNTIME_KIND_SOURCE),
            version=_pinned_version(data, source_root),
            build=data.get("build"),
        )
    else:
        # Git runs against `source_root` only for a `source` runtime: a
        # package's commit comes from its build info, and an unidentified
        # runtime has none.
        resolution = resolve_runtime(source_root)
        commit = None
        if resolution.runtime_kind == RUNTIME_KIND_SOURCE:
            result = _run_git(["rev-parse", "HEAD"], cwd=source_root)
            if result.returncode == 0:
                commit = result.stdout.strip()
        elif resolution.runtime_kind == RUNTIME_KIND_PACKAGE:
            commit = _package_commit(resolution.build)
        identity = ControllerIdentity(
            generation=None,
            source_root=source_root,
            origin_source_root=source_root,
            source_kind=SOURCE_KIND_UNPINNED,
            source_commit=commit,
            tree_digest=None,
            generation_source=None,
            pinned_at=_now(),
            runtime_kind=resolution.runtime_kind,
            version=resolution.version,
            build=resolution.build,
            runtime_reason=resolution.reason,
        )

    _cached_identity = identity
    return identity


def current() -> ControllerIdentity:
    """Return the cached pin, resolving it first if this is the first
    access in this process. Never re-resolves after the first call."""
    return pin()
