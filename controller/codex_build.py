"""The identity of a Codex build (``workflow-controller-verify-codex``, D1).

An npm install of Codex is not one file: the ``codex`` launcher is a symlink to a
``#!/usr/bin/env node`` script that finds a platform package and spawns the native
executable inside it. :func:`build_digest` names exactly that set of files -- the
launcher's package plus each ``optionalDependencies`` package found where Node's
resolution finds it in the launch environment -- so a verified build and a launched
build are the same bytes. A layout it cannot bind gives ``None``: no digest, never a
guess. The digest is computed from disk on every call.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path

from . import codex_sandbox, review, runtime

#: Bumped whenever the listing's shape changes, so an old digest never matches a new listing.
DIGEST_VERSION = "codex-build-digest-v1"
#: More entries than this in all gives no digest; nothing is truncated.
MAX_ENTRIES = 20000
_CHUNK = 1 << 20
#: The record of verified Codex builds, and its lock, inside the runtime root (D2).
RECORD_NAME = "verified-codex-builds.json"
LOCK_NAME = "verified-codex-builds.lock"
SCHEMA_VERSION = 1


class _Unbound(Exception):
    """The layout cannot be bound to a digest."""


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _walk(root: str) -> Iterator[tuple[str, os.DirEntry]]:
    """Every non-directory entry under ``root`` (symlinks not followed), with its path relative to ``root``."""
    stack = [("", root)]
    while stack:
        prefix, directory = stack.pop()
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            rel = prefix + entry.name
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                yield rel, entry
            else:
                stack.append((rel + "/", entry.path))


def _list_root(root: str, label: str, exempt: frozenset[str], out: list[tuple[str, str]]) -> None:
    for rel, entry in _walk(root):
        if len(out) >= MAX_ENTRIES:
            raise _Unbound("too many entries")
        name = label + rel
        if entry.is_symlink():
            real = os.path.realpath(entry.path)
            if not _inside(real, root) and real not in exempt:
                raise _Unbound("a symlink leaves its root")
            out.append((name, f"{name}\0L\0{os.readlink(entry.path)}\n"))
            continue
        info = entry.stat(follow_symlinks=False)
        if not entry.is_file(follow_symlinks=False):
            raise _Unbound("a special file")
        exe = "x" if info.st_mode & 0o111 else "-"
        out.append((name, f"{name}\0{exe}\0{_file_sha256(entry.path)}\n"))


def _shebang(path: str) -> list[str] | None:
    with open(path, "rb") as fh:
        first = fh.readline(512)
    if not first.startswith(b"#!"):
        return None
    return first[2:].decode("utf-8", "replace").split()


def _executable(path: Path) -> bool:
    """A file the launch can execute: ``env`` skips a candidate that is not, so lookup must too."""
    return path.is_file() and os.access(path, os.X_OK)


def _interpreter(launcher: Path, parts: list[str], system_path: str) -> str:
    """The resolved interpreter of a ``#!`` line, found the way the launch finds it."""
    if not parts:
        raise _Unbound("an empty #! line")
    first = parts[0]
    if first.endswith("/env"):
        if len(parts) < 2 or parts[1].startswith("-"):
            raise _Unbound("an env option")
        name = parts[1]
        dirs = [str(launcher.parent), *[d for d in system_path.split(":") if d]]
        found = next((Path(d) / name for d in dirs if _executable(Path(d) / name)), None)
    else:
        found = Path(first) if _executable(Path(first)) else None
    if found is None:
        raise _Unbound("the interpreter is found nowhere")
    return os.path.realpath(found)


def _find_dependency(name: str, start: Path, interpreter: str | None) -> str | None:
    """Node's CommonJS search for ``<name>/package.json`` from ``start``: each ancestor's ``node_modules`` (not one
    inside a directory itself named ``node_modules``), then the interpreter's global ``<prefix>/lib/node``."""
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise _Unbound("an unusable dependency name")
    candidates = [d / "node_modules" / name for d in (start, *start.parents) if d.name != "node_modules"]
    if interpreter is not None:
        candidates.append(Path(interpreter).parent.parent / "lib" / "node" / name)
    found = next((c for c in candidates if (c / "package.json").is_file()), None)
    return os.path.realpath(found) if found is not None else None


def _declares(manifest: dict, key: str) -> bool:
    value = manifest.get(key)
    return bool(value)


def _digest(binary: Path, system_path: str) -> str:
    entry = os.path.realpath(binary)
    if not os.path.isfile(entry):
        raise _Unbound("the entry point is not a file")
    entry_path = Path(entry)
    modules = next((p for p in entry_path.parents if p.name == "node_modules"), None)
    root = codex_sandbox.package_root(entry_path)
    in_package = modules is not None and root != modules and modules in root.parents and (root / "package.json").is_file()
    parts = _shebang(entry)
    out: list[tuple[str, str]] = []
    if not in_package:
        if parts is not None:
            raise _Unbound("a script outside a package")
        info = os.stat(entry)
        exe = "x" if info.st_mode & 0o111 else "-"
        out.append((entry_path.name, f"{entry_path.name}\0{exe}\0{_file_sha256(entry)}\n"))
    else:
        manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise _Unbound("package.json is not an object")
        if _declares(manifest, "dependencies") or _declares(manifest, "peerDependencies"):
            raise _Unbound("the package declares dependencies")
        optional = manifest.get("optionalDependencies") or {}
        if not isinstance(optional, dict):
            raise _Unbound("optionalDependencies is not an object")
        interpreter = _interpreter(Path(binary), parts, system_path) if parts is not None else None
        found = {}
        for name in sorted(optional):
            real = _find_dependency(name, entry_path.parent, interpreter)
            if real is not None:
                found[name] = real
        exempt = frozenset(found.values())
        _list_root(os.path.realpath(root), "", exempt, out)
        for name, real in found.items():
            _list_root(real, f"dep:{name}/", exempt, out)
    out.sort(key=lambda item: item[0])
    digest = hashlib.sha256(DIGEST_VERSION.encode() + b"\n")
    for _, line in out:
        digest.update(line.encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def build_digest(binary: Path, *, system_path: str = review.SYSTEM_PATH) -> str | None:
    """The SHA-256 naming every file a launch of ``binary`` (the launcher path ``review.resolve_binary`` returns,
    symlink kept) can run as Codex, or ``None`` when the layout cannot be bound. Reads only; recomputed every call."""
    try:
        return _digest(Path(binary), system_path)
    except (_Unbound, OSError, ValueError):
        return None


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def read_verified(runtime_root: Path) -> tuple[dict[str, dict], str | None]:
    """The recorded Codex builds (digest to entry) and, when a record exists but cannot be trusted, why it was
    ignored. A missing record is empty with no reason; an unreadable, symlinked, foreign-owned, other-writable,
    malformed or wrong-schema record is empty **with** a reason, so a damaged record never admits a build."""
    path = Path(runtime_root).resolve() / RECORD_NAME
    try:
        data = runtime.read_owned_file(path)
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"{RECORD_NAME} was ignored: {exc}"
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return {}, f"{RECORD_NAME} was ignored: it is not valid JSON ({exc})"
    builds = obj.get("builds") if isinstance(obj, dict) else None
    if not isinstance(obj, dict) or obj.get("schema_version") != SCHEMA_VERSION or not isinstance(builds, dict):
        return {}, f"{RECORD_NAME} was ignored: it is not a schema_version {SCHEMA_VERSION} record"
    admitted = {digest: entry for digest, entry in builds.items() if _is_digest(digest) and isinstance(entry, dict)}
    if len(admitted) != len(builds):
        return {}, f"{RECORD_NAME} was ignored: an entry is not a SHA-256 digest with an object"
    return admitted, None


def record_verified(runtime_root: Path, digest: str, entry: Mapping) -> Path:
    """Record ``digest`` with ``entry``, merging under the exclusive lock so concurrent runs cannot lose an entry.
    Refuses (``OSError``, nothing written) when the record is a symlink; an untrustworthy existing record is
    replaced by one holding only the new entry."""
    if not _is_digest(digest):
        raise ValueError(f"not a SHA-256 digest: {digest!r}")
    with runtime.builds_lock(runtime_root, LOCK_NAME):
        builds, _note = read_verified(runtime_root)
        builds[digest] = dict(entry)
        return runtime.write_private_json_nofollow(
            Path(runtime_root), RECORD_NAME, {"schema_version": SCHEMA_VERSION, "builds": builds})


@dataclasses.dataclass(frozen=True)
class Verified:
    """The answer of :func:`is_verified`: ``reason`` says why when ``ok`` is false."""
    ok: bool
    digest: str | None
    reason: str | None = None


def is_verified(runtime_root: Path, binary: Path, *, system_path: str = review.SYSTEM_PATH) -> Verified:
    """Whether the build ``binary`` names was verified: its fresh digest is in a trustworthy record. Reads only --
    writes nothing, starts no process."""
    digest = build_digest(binary, system_path=system_path)
    if digest is None:
        return Verified(False, None, "the Codex build cannot be bound to a digest")
    builds, note = read_verified(runtime_root)
    if note is not None:
        return Verified(False, digest, note)
    if not (Path(runtime_root).resolve() / RECORD_NAME).exists():
        return Verified(False, digest, f"no {RECORD_NAME} record exists")
    if digest not in builds:
        return Verified(False, digest, "this build's digest is not recorded")
    return Verified(True, digest)
