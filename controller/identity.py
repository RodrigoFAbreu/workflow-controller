"""The pinned, immutable Controller source identity.

Every run that may write a job record or launch a worker executes from an
immutable, content-verified snapshot of the source tree, so a later import
or resource read cannot reach the mutable origin worktree at all. See
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, CP1's identity block, for the
full design this module implements.

Only two late resource reads are allowlisted anywhere in ``controller/*.py``,
both here, both performed before any orchestration: ``pin()``'s read of
``<source_root>/SOURCE_PIN.json``, and the ``generation_source: "worktree"``
fallback read of the *origin* tree's ``controller/GENERATION.json``.
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

from controller import runtime
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

#: The closed three-member `source_kind` enumeration.
SOURCE_KIND_COMMIT = "commit"
SOURCE_KIND_WORKTREE = "worktree"
SOURCE_KIND_UNPINNED = "unpinned"


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


def _is_git_repository(path: Path) -> bool:
    result = _run_git(["rev-parse", "--git-dir"], cwd=path)
    return result.returncode == 0


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
    whole worktree."""
    result = _run_git(
        ["status", "--porcelain", "--", *_SNAPSHOT_DIRS, *_SNAPSHOT_FILES], cwd=origin
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
    data = json.dumps(pin_body, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest / _SOURCE_PIN_NAME)
    finally:
        tmp.unlink(missing_ok=True)


def materialise(
    origin_source_root: Path, runtime_root: Path, *, allow_dirty: bool = False,
) -> Path:
    """Build (or reuse) the immutable, content-addressed snapshot a pinned
    run executes from. Returns the published snapshot directory. Raises
    ``DirtyControllerSourceError`` for a dirty tree with no
    ``--allow-dirty-source``, and ``SourceSnapshotError`` (``raised_by:
    "materialise"``) for every other refusal this function can reach."""
    origin_source_root = origin_source_root.resolve()
    dirty = _is_dirty(origin_source_root)
    if dirty and not allow_dirty:
        raise DirtyControllerSourceError(
            f"{origin_source_root} has uncommitted changes under controller/ or "
            f"pyproject.toml -- pass --allow-dirty-source to run from the working tree",
            evidence={"origin_source_root": str(origin_source_root)},
        )

    source_dir = runtime_root / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = source_dir / f".materialise-{secrets.token_hex(8)}.tmp"
    try:
        if dirty:
            _extract_dirty(origin_source_root, tmp_dir)
            source_kind = SOURCE_KIND_WORKTREE
            source_commit = None
        else:
            _extract_clean(origin_source_root, tmp_dir)
            source_kind = SOURCE_KIND_COMMIT
            source_commit = _run_git(["rev-parse", "HEAD"], cwd=origin_source_root).stdout.strip()

        tree_digest = compute_tree_digest(tmp_dir)
        generation, generation_source = _read_generation(origin_source_root)

        dest = source_dir / tree_digest
        pin_body = {
            "schema_version": 1,
            "source_kind": source_kind,
            "source_commit": source_commit,
            "origin_source_root": str(origin_source_root),
            "generation": generation,
            "generation_source": generation_source,
            "tree_digest": tree_digest,
            "materialised_at": _now(),
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
        )
    else:
        commit = None
        if _is_git_repository(source_root):
            result = _run_git(["rev-parse", "HEAD"], cwd=source_root)
            if result.returncode == 0:
                commit = result.stdout.strip()
        identity = ControllerIdentity(
            generation=None,
            source_root=source_root,
            origin_source_root=source_root,
            source_kind=SOURCE_KIND_UNPINNED,
            source_commit=commit,
            tree_digest=None,
            generation_source=None,
            pinned_at=_now(),
        )

    _cached_identity = identity
    return identity


def current() -> ControllerIdentity:
    """Return the cached pin, resolving it first if this is the first
    access in this process. Never re-resolves after the first call."""
    return pin()
