"""The models a harness's own CLI lists (``workflow-controller-cm1``).

A route that names a model the adapter does not declare makes the Controller
ask the harness what it lists (today ``codex debug models``), keep the answer
in ``<runtime root>/model-catalog/<harness>.json`` with a timestamp, and hand
the result back as a :class:`controller.routing.ModelDiscovery` record. The
pinned list in :mod:`controller.harness` stays the floor: every failure here
is a record with a reason, never an exception to the caller.

The record type and the source names live in :mod:`controller.routing`, which
comes before this module in the dependency order (this module needs
:mod:`controller.review`).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import secrets
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from controller import review, routing, runtime
from controller.anchor import OWNERSHIP_VAR
from controller.routing import ModelDiscovery

#: The cache is fresh for this long (D4).
CACHE_TTL = datetime.timedelta(hours=24)
#: The hard deadline of one lookup, in seconds (D5).
LOOKUP_TIMEOUT = 20.0
#: An answer longer than this is refused after the read (D5).
MAX_ANSWER_BYTES = 1 << 20
CACHE_SCHEMA_VERSION = 1
CACHE_DIR = "model-catalog"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclasses.dataclass(frozen=True)
class Lookup:
    """How to ask one harness's CLI for its models."""

    binary: str
    args: tuple[str, ...]


#: The harnesses that have a lookup, by adapter name.
LOOKUPS: Mapping[str, Lookup] = {"codex": Lookup(review.CODEX.default_binary, ("debug", "models"))}

Runner = Callable[..., "review.RunResult"]
Resolver = Callable[[str], Path]


class _Unreadable(Exception):
    """An answer or a cache that cannot be used (internal)."""


def _slug(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("-"):
        raise _Unreadable(f"a model slug {value!r} is not a non-empty string that avoids a leading '-'")
    return value


def parse_answer(raw: bytes) -> tuple[str, ...]:
    """The slugs ``codex debug models`` lists (``visibility: "list"``), in order.
    Raises ``_Unreadable`` for an oversize, undecodable, malformed or empty answer."""
    if len(raw) > MAX_ANSWER_BYTES:
        raise _Unreadable(f"the answer is over {MAX_ANSWER_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise _Unreadable(f"the answer is not JSON: {exc}") from exc
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise _Unreadable("the answer has no 'models' list")
    listed: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _Unreadable("a model entry is not an object")
        if entry.get("visibility") == "list":
            slug = _slug(entry.get("slug"))
            if slug not in listed:
                listed.append(slug)
    if not listed:
        raise _Unreadable("the answer lists no model")
    return tuple(listed)


def _cache_path(runtime_root: Path, harness_name: str) -> Path:
    return runtime_root / CACHE_DIR / f"{harness_name}.json"


def _parse_time(value: object) -> datetime.datetime:
    if not isinstance(value, str):
        raise _Unreadable("fetched_at is not a string")
    try:
        return datetime.datetime.strptime(f"{value}+0000", TIMESTAMP_FORMAT + "%z")  # a Z-suffixed UTC time
    except ValueError as exc:
        raise _Unreadable("fetched_at is not UTC ISO 8601") from exc


def _read_cache(runtime_root: Path, harness_name: str) -> tuple[tuple[str, ...], str, datetime.datetime] | None:
    """``(models, fetched_at, parsed time)`` of a valid cache, else ``None``
    (absent, unreadable or invalid -- all the same to the caller)."""
    try:
        data = runtime.read_json(_cache_path(runtime_root, harness_name))
        if data is None:
            return None
        if not isinstance(data, dict) or data.get("schema_version") != CACHE_SCHEMA_VERSION:
            return None
        fetched = _parse_time(data.get("fetched_at"))
        models = data.get("models")
        if not isinstance(models, list) or not models:
            return None
        return tuple(_slug(m) for m in models), data["fetched_at"], fetched
    except (ValueError, OSError, _Unreadable):  # ValueError covers JSON and UTF-8 decode errors
        return None


def _is_fresh(fetched: datetime.datetime, now: datetime.datetime) -> bool:
    return datetime.timedelta(0) <= now - fetched < CACHE_TTL  # a future timestamp is never fresh


def _stamp(now: datetime.datetime) -> str:
    return now.astimezone(datetime.timezone.utc).strftime(TIMESTAMP_FORMAT)


def _environment(binary: Path, environ: Mapping[str, str]) -> dict[str, str]:
    dirs = [str(binary.parent)]
    interp = review.interpreter_dir(binary)
    if interp is not None and str(interp) not in dirs:
        dirs.append(str(interp))
    env = {"PATH": ":".join([*dirs, review.SYSTEM_PATH])}
    for name in ("HOME", "CODEX_HOME"):
        if name in environ:
            env[name] = environ[name]
    tag = f"model-lookup-{os.getpid()}-{secrets.token_hex(8)}"
    inherited = environ.get(OWNERSHIP_VAR)
    env[OWNERSHIP_VAR] = f"{inherited}:{tag}" if inherited else tag
    return env


def _run_lookup(lookup: Lookup, runtime_root: Path, run: Runner, resolve: Resolver,
                environ: Mapping[str, str]) -> tuple[str, ...]:
    """The listed slugs, or ``_Unreadable`` carrying a one-line reason."""
    try:
        binary = resolve(lookup.binary)
    except Exception as exc:  # noqa: BLE001 -- an unresolvable binary is a failed lookup
        raise _Unreadable(f"{lookup.binary} cannot be resolved: {exc}") from exc
    command: Sequence[str] = [str(binary), *lookup.args]
    try:
        result = run(command, cwd=runtime_root, env=_environment(binary, environ), timeout=LOOKUP_TIMEOUT)
    except OSError as exc:
        raise _Unreadable(f"{lookup.binary} could not be started: {exc}") from exc
    if result.timed_out:
        raise _Unreadable(f"{' '.join(lookup.args)} did not answer within {LOOKUP_TIMEOUT:g} seconds")
    if result.exit_code != 0:
        raise _Unreadable(f"{' '.join(lookup.args)} exited with {result.exit_code}")
    return parse_answer(result.stdout)


def lookup(harness_name: str, runtime_root: Path, *, now: datetime.datetime, run: Runner = review.run_bounded,
           resolve: Resolver = review.resolve_binary, allow_run: bool = True,
           environ: Mapping[str, str] | None = None) -> ModelDiscovery:
    """The models ``harness_name``'s CLI lists, from a fresh cache or a new
    lookup. Never raises. With ``allow_run=False`` nothing is run or written:
    a fresh cache answers ``cache``, a stale one ``stale``, otherwise
    ``not_looked_up``."""
    spec = LOOKUPS.get(harness_name)
    if spec is None:
        return ModelDiscovery(source=routing.SOURCE_UNSUPPORTED, reason="the harness has no model lookup")
    cached = _read_cache(runtime_root, harness_name)
    if cached is not None and _is_fresh(cached[2], now):
        return ModelDiscovery(cached[0], routing.SOURCE_CACHE, cached[1])
    if not allow_run:
        if cached is not None:
            return ModelDiscovery(cached[0], routing.SOURCE_STALE, cached[1], "the cache is stale and no lookup was run")
        return ModelDiscovery(source=routing.SOURCE_NOT_LOOKED_UP, reason="no lookup was run")
    try:
        models = _run_lookup(spec, runtime_root, run, resolve, os.environ if environ is None else environ)
    except _Unreadable as exc:
        if cached is not None:
            return ModelDiscovery(cached[0], routing.SOURCE_STALE, cached[1], str(exc))
        return ModelDiscovery(source=routing.SOURCE_UNAVAILABLE, reason=str(exc))
    fetched_at = _stamp(now)
    runtime.write_json_best_effort(runtime_root, f"{CACHE_DIR}/{harness_name}.json", {
        "schema_version": CACHE_SCHEMA_VERSION, "fetched_at": fetched_at, "models": list(models),
    })
    return ModelDiscovery(models, routing.SOURCE_DISCOVERED, fetched_at)
