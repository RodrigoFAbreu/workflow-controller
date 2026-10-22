"""The record of Claude builds an operator verified for the functional tester
(``workflow-controller-claude-build-verify``, D3).

``<runtime root>/verified-claude-builds.json`` holds
``{"schema_version": 1, "builds": {<sha256>: {"version", "verified_at",
"controller_version", "probes"}}}``. The functional launch admits the built-in
list (``functional_review.VERIFIED_NATIVE_BUILDS``) plus the digests recorded
here. The record is host-level evidence about one binary. The principal it is
defended against is the tester, which is sandboxed and cannot write the runtime
root; the principal it trusts is the operator's own session (the
``verify-claude`` command and the lifecycle workers, which run with the
operator's authority over the settings file and the Controller itself), so it
carries no key or signature. What is enforced, fail closed: the record is read
through one ``O_NOFOLLOW`` descriptor and checked for a regular file owned by
the effective user with no group or world write bit; it is never written
through a symlink and is created mode ``0600``; an unreadable, malformed or
wrong-schema record counts as empty and the caller is told why.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from . import runtime

#: The record's file name inside the runtime root.
RECORD_NAME = "verified-claude-builds.json"
SCHEMA_VERSION = 1


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def read_verified(runtime_root: Path) -> tuple[dict[str, dict], str | None]:
    """The recorded builds (digest to entry) and, when a record exists but cannot
    be trusted, why it was ignored. A missing record is empty with no reason; an
    unreadable, symlinked, foreign-owned, other-writable, malformed or
    wrong-schema record is empty **with** a reason, so a damaged record can
    never admit a build."""
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


def recorded_digests(runtime_root: Path) -> tuple[frozenset[str], str | None]:
    """Just the digests of :func:`read_verified`, and the reason it was ignored."""
    builds, note = read_verified(runtime_root)
    return frozenset(builds), note


def record_verified(runtime_root: Path, digest: str, entry: Mapping) -> Path:
    """Record ``digest`` with ``entry``, merging with what is there under the
    exclusive lock so concurrent runs cannot lose an entry. Refuses (``OSError``,
    nothing written) when the record is a symlink; an untrustworthy existing
    record is replaced by one holding only the new entry."""
    if not _is_digest(digest):
        raise ValueError(f"not a SHA-256 digest: {digest!r}")
    with runtime.builds_lock(runtime_root):
        builds, _note = read_verified(runtime_root)
        builds[digest] = dict(entry)
        return runtime.write_private_json_nofollow(
            Path(runtime_root), RECORD_NAME, {"schema_version": SCHEMA_VERSION, "builds": builds})


def file_digest(path: str | os.PathLike) -> str | None:
    """SHA-256 of the file ``path`` resolves to, or ``None`` when unreadable."""
    import hashlib

    digest = hashlib.sha256()
    try:
        with open(os.path.realpath(path), "rb") as handle:
            while chunk := handle.read(1 << 22):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()
