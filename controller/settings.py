"""The user settings file (``workflow-controller-settings-and-telemetry``
CP1, ``docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md`` Design
A).

One user-level JSON file holds every operational tunable and the routing
defaults. :data:`TABLE` is the closed list of them (I3): each
:class:`Setting` has a type, a default, bounds, the CLI flag that overrides
it, and the *generation* of the release that last set its default.

- **Location** (:func:`resolve_path`): ``--settings PATH``, then
  ``$WORKFLOW_CONTROLLER_SETTINGS``, then
  ``$XDG_CONFIG_HOME/workflow-controller/settings.json``, then
  ``~/.config/workflow-controller/settings.json``.
- **Validation** (I2): a missing file is never an error; an invalid one is
  :class:`~controller.errors.SettingsError` (exit 20) and is never
  rewritten.
- **The fill** (:func:`fill`, A.4): additive, forward only, and the whole
  read-modify-write under the file's lock (I4). It adds each missing key
  with its default, recording ``{value, generation}`` in
  ``_defaults_written``, and moves an untouched value to a newer default
  only when this release's generation for the key is greater than the
  recorded one, so two releases sharing the file never undo each other.
- **Unknown keys** (A.5) are ignored with one warning per invocation;
  :func:`clean` removes them, but refuses a file last filled by a newer
  release (its ``_table_generation`` is greater than ours).
- **The routing section** goes through
  :func:`controller.routing.validate_routing_mapping` with no
  ``schema_version`` and unknown roles, fields and keys ignored.

The read-only commands call :func:`load`; the writing ones call
:func:`fill`. Every file primitive is ``controller.runtime``'s.

**Wiring** (CP2, Design B): ``cli.main`` resolves :class:`EffectiveSettings`
once and passes the values that have a call path explicitly. The leaf
values that have none -- the git, release-command and Workflow-query
timeouts and the ``gh pr list`` limit -- are process-wide:
:func:`apply_process_defaults` sets them once, before any thread starts,
and each leaf module reads its value at call time. :func:`process_defaults`
is the tests' only other writer, and restores them.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from controller import forge, gitrepo, release_txn, routing, runtime, workflow_contract
from controller.errors import RoutingConfigError, RuntimeContainmentError, SettingsError

SCHEMA_VERSION = 1

#: The environment variable naming the settings file (location row 2).
ENV_SETTINGS = "WORKFLOW_CONTROLLER_SETTINGS"

#: The settings file under ``$XDG_CONFIG_HOME`` or ``~/.config``.
RELATIVE_PATH = Path("workflow-controller") / "settings.json"

TYPE_INT = "int"
#: An integer, or ``null``.
TYPE_OPTIONAL_INT = "optional-int"
#: The routing section (:func:`controller.routing.validate_routing_mapping`).
TYPE_ROUTING = "routing"

ROUTING_KEY = "routing"

#: Where an effective value came from (``settings show``, the job record).
SOURCE_CLI = "cli"
SOURCE_FILE = "file"
SOURCE_DEFAULT = "default"

#: The file's own bookkeeping keys, never settings.
_SCHEMA_KEY = "schema_version"
_DEFAULTS_WRITTEN = "_defaults_written"
_TABLE_GENERATION = "_table_generation"


@dataclasses.dataclass(frozen=True)
class Setting:
    """One row of :data:`TABLE`. ``key`` is dotted (``section.name``), or
    ``routing``. ``minimum``/``maximum`` bound an integer (both inclusive);
    ``generation`` is the release generation that last set ``default``."""

    key: str
    type: str
    default: Any
    minimum: int | None
    maximum: int | None
    cli_flag: str | None
    generation: int


#: The closed table (I3). Every default equals the constant it replaces, so
#: a default-filled file behaves as no file did.
#:
#: The compatibility rule (A.3): a release never moves a default outside the
#: bounds any earlier generation gave the key and never changes a key's
#: type; a change of type or range retires the key and adds a new one.
#: Changing a default raises that row's ``generation``; adding or retiring a
#: key, a routing role, a route field or a key directly under ``routing``
#: raises :data:`TABLE_GENERATION`.
TABLE: tuple[Setting, ...] = (
    Setting("worker.drain_detach_seconds", TYPE_INT, 10800, 60, 604800, "resume --drain-timeout", 1),
    Setting("worker.timeout_seconds", TYPE_OPTIONAL_INT, None, 60, 172800, "--timeout", 1),
    Setting("run.max_steps", TYPE_INT, 20, 1, 1000, "run --max-steps", 1),
    Setting("follow.heartbeat_seconds", TYPE_INT, 30, 1, 3600, None, 1),
    Setting("follow.replay_events", TYPE_INT, 20, 0, 10000, "follow --from-start", 1),
    Setting("timeouts.git_seconds", TYPE_INT, 600, 30, 7200, None, 1),
    Setting("timeouts.release_command_seconds", TYPE_INT, 1800, 60, 21600, None, 1),
    Setting("timeouts.workflow_query_seconds", TYPE_INT, 120, 10, 3600, None, 1),
    Setting("forge.pr_list_limit", TYPE_INT, 200, 50, 1000, None, 1),
    Setting(ROUTING_KEY, TYPE_ROUTING, {"default": {}, "roles": {}}, None, None,
            "--routing-config, --model, --effort, --role-model, --role-effort", 1),
)

#: The highest ``generation`` in :data:`TABLE`, raised as well by a release
#: that adds or retires a key. Only ever increases.
TABLE_GENERATION = 1


def _table() -> dict[str, Setting]:
    return {setting.key: setting for setting in TABLE}


def _sections() -> set[str]:
    return {setting.key.partition(".")[0] for setting in TABLE if "." in setting.key}


def default_value(setting: Setting) -> Any:
    """A fresh copy of ``setting``'s default (the routing default is a
    mutable mapping)."""
    return copy.deepcopy(setting.default)


# ---------------------------------------------------------------------------
# Location.
# ---------------------------------------------------------------------------


def resolve_path(cli_path: str | os.PathLike | None = None, *, env: Mapping[str, str] | None = None,
                 home: Path | None = None) -> Path:
    """The settings file's absolute path: the first set of ``--settings``,
    ``$WORKFLOW_CONTROLLER_SETTINGS``, ``$XDG_CONFIG_HOME/...`` and
    ``~/.config/...`` (A.1). ``env`` and ``home`` are for tests."""
    env = os.environ if env is None else env
    if cli_path is not None:
        return Path(os.path.abspath(Path(cli_path).expanduser()))
    named = env.get(ENV_SETTINGS)
    if named:
        return Path(os.path.abspath(Path(named).expanduser()))
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(os.path.abspath(Path(xdg).expanduser())) / RELATIVE_PATH
    return (Path.home() if home is None else home) / ".config" / RELATIVE_PATH


# ---------------------------------------------------------------------------
# Paths inside the file.
# ---------------------------------------------------------------------------


def _segments(key: str) -> tuple[str, ...]:
    return tuple(key.split("."))


def _lookup(data: Mapping, segments: tuple[str, ...]) -> tuple[bool, Any]:
    node: Any = data
    for segment in segments:
        if not isinstance(node, dict) or segment not in node:
            return False, None
        node = node[segment]
    return True, node


def _store(data: dict, segments: tuple[str, ...], value: Any) -> None:
    node = data
    for segment in segments[:-1]:
        node = node.setdefault(segment, {})
    node[segments[-1]] = value


def _drop(data: dict, segments: tuple[str, ...]) -> None:
    node: Any = data
    for segment in segments[:-1]:
        node = node.get(segment) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(segments[-1], None)


def _routing_segments(dotted: str) -> tuple[str, ...]:
    """The segments of a dotted path :func:`routing.validate_routing_mapping`
    left out. Role names may hold a dot only when unknown, so an unknown
    role's path is ``routing.roles.<the rest>`` unless the rest starts with
    a known role (then it is that role's unknown field)."""
    rest = dotted.removeprefix(ROUTING_KEY + ".")
    for section in ("default", "roles"):
        prefix = section + "."
        if not rest.startswith(prefix):
            continue
        tail = rest.removeprefix(prefix)
        if section == "default":
            return (ROUTING_KEY, "default", tail)
        for role in routing.ROLES:
            if tail.startswith(role + "."):
                return (ROUTING_KEY, "roles", role, tail.removeprefix(role + "."))
        return (ROUTING_KEY, "roles", tail)
    return (ROUTING_KEY, rest)


def _same(left: Any, right: Any) -> bool:
    """JSON equality: ``1`` and ``true`` differ."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True).encode("utf-8") + b"\n"


# ---------------------------------------------------------------------------
# Parsing and validation (I2).
# ---------------------------------------------------------------------------


def _refuse(path: Path, key: str, problem: str, **evidence: Any) -> SettingsError:
    return SettingsError(
        f"the settings file {path} cannot be used: {problem}",
        evidence={"path": str(path), "key": key, **evidence},
    )


def _parse(raw: bytes, path: Path) -> dict:
    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
        keys = [key for key, _ in pairs]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise _refuse(path, duplicates[0], f"duplicate key(s) {duplicates}", duplicate_keys=duplicates)
        return dict(pairs)

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise _refuse(path, "", f"it is not UTF-8 ({exc})") from None
    except json.JSONDecodeError as exc:
        raise _refuse(path, "", f"it is not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise _refuse(path, "", "its top level must be a JSON object")
    return data


def _is_int(value: Any) -> bool:
    return type(value) is int


def _check_setting(path: Path, setting: Setting, value: Any) -> None:
    if setting.type == TYPE_OPTIONAL_INT and value is None:
        return
    bounds = f"from {setting.minimum} to {setting.maximum}"
    expected = f"an integer {bounds}" + (", or null" if setting.type == TYPE_OPTIONAL_INT else "")
    if not _is_int(value) or not setting.minimum <= value <= setting.maximum:
        raise _refuse(path, setting.key, f"{setting.key} must be {expected}, got {json.dumps(value)}",
                      value=value, minimum=setting.minimum, maximum=setting.maximum)


def _check_routing(path: Path, value: Any) -> tuple[routing.RoutingConfig, list[str]]:
    try:
        return routing.validate_routing_mapping(
            value, path=str(path), where=ROUTING_KEY + ".", require_schema_version=False,
            unknown=routing.UNKNOWN_IGNORE,
        )
    except RoutingConfigError as exc:
        problem = exc.message.removeprefix(f"the routing config {path} cannot be used: ")
        raise _refuse(path, exc.evidence.get("key", ROUTING_KEY), problem,
                      routing_evidence=dict(exc.evidence)) from None


@dataclasses.dataclass(frozen=True)
class _Checked:
    values: Mapping[str, Any]
    routing_config: routing.RoutingConfig | None
    unknown: tuple[tuple[str, tuple[str, ...]], ...]
    table_generation: int | None


def _validate(data: dict, path: Path) -> _Checked:
    """Validate a parsed file, or raise :class:`SettingsError`. Returns
    the known settings present, the routing config (when the section is
    present), the unknown keys (dotted, with their segments) and the stored
    ``_table_generation``."""
    if _SCHEMA_KEY in data and not (_is_int(data[_SCHEMA_KEY]) and data[_SCHEMA_KEY] == SCHEMA_VERSION):
        raise _refuse(path, _SCHEMA_KEY,
                      f"schema_version must be {SCHEMA_VERSION}, got {json.dumps(data[_SCHEMA_KEY])}")
    table_generation = data.get(_TABLE_GENERATION)
    if _TABLE_GENERATION in data and not (_is_int(table_generation) and table_generation >= 1):
        raise _refuse(path, _TABLE_GENERATION,
                      f"_table_generation must be a positive integer, got {json.dumps(table_generation)}")

    table, sections = _table(), _sections()
    unknown: list[tuple[str, tuple[str, ...]]] = []
    for key in sorted(data):
        if key in (_SCHEMA_KEY, _DEFAULTS_WRITTEN, _TABLE_GENERATION, ROUTING_KEY):
            continue
        if key not in sections:
            unknown.append((key, (key,)))
            continue
        if not isinstance(data[key], dict):
            raise _refuse(path, key, f"{key} must be an object, got {json.dumps(data[key])}")
        for name in sorted(data[key]):
            dotted = f"{key}.{name}"
            if dotted not in table:
                unknown.append((dotted, (key, name)))

    values: dict[str, Any] = {}
    routing_config = None
    for setting in TABLE:
        present, value = _lookup(data, _segments(setting.key))
        if not present:
            continue
        if setting.type == TYPE_ROUTING:
            routing_config, ignored = _check_routing(path, value)
            unknown.extend((dotted, _routing_segments(dotted)) for dotted in ignored)
            value = routing_mapping(routing_config)
        else:
            _check_setting(path, setting, value)
        values[setting.key] = value

    if _DEFAULTS_WRITTEN in data:
        written = data[_DEFAULTS_WRITTEN]
        if not isinstance(written, dict):
            raise _refuse(path, _DEFAULTS_WRITTEN,
                          f"_defaults_written must be an object, got {json.dumps(written)}")
        for key in sorted(written):
            entry = written[key]
            where = f"{_DEFAULTS_WRITTEN}.{key}"
            if not (isinstance(entry, dict) and set(entry) == {"value", "generation"}
                    and _is_int(entry["generation"]) and entry["generation"] >= 1):
                raise _refuse(path, where,
                              f"{where} must be {{\"value\": ..., \"generation\": <positive integer>}}, "
                              f"got {json.dumps(entry)}")
            if not _lookup(data, _segments(key))[0]:
                raise _refuse(path, where, f"{where} records a default for {key}, which the file does not hold")
    return _Checked(values=MappingProxyType(values), routing_config=routing_config,
                    unknown=tuple(unknown), table_generation=table_generation)


def routing_mapping(config: routing.RoutingConfig) -> dict:
    """A routing config as the plain ``{"default": ..., "roles": ...}``
    mapping it was parsed from, minus what was ignored."""
    return {
        "default": dict(config.default),
        "roles": {role: dict(entry) for role, entry in sorted(config.roles.items())},
    }


# ---------------------------------------------------------------------------
# The loaded file.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SettingsFile:
    """A validated settings file as read (or as just written). ``values``
    holds the known settings present in it, the routing section as its
    validated mapping; ``routing_config`` is that section parsed, or
    ``None`` when the file has none. ``sha256`` is of the file's bytes, or
    ``None`` when there is no file."""

    path: Path
    exists: bool
    sha256: str | None
    values: Mapping[str, Any]
    routing_config: routing.RoutingConfig | None
    unknown: tuple[str, ...]
    table_generation: int | None


def _settings_file(path: Path, raw: bytes | None) -> tuple[SettingsFile, dict, _Checked]:
    data = {} if raw is None else _parse(raw, path)
    checked = _validate(data, path)
    loaded = SettingsFile(
        path=path, exists=raw is not None, sha256=None if raw is None else hashlib.sha256(raw).hexdigest(),
        values=checked.values, routing_config=checked.routing_config,
        unknown=tuple(dotted for dotted, _ in checked.unknown), table_generation=checked.table_generation,
    )
    return loaded, data, checked


def _read(path: Path) -> bytes | None:
    try:
        return runtime.read_settings_bytes(path)
    except OSError as exc:
        raise _refuse(path, "", f"it cannot be read ({exc})", os_error=str(exc)) from None


#: ``(path, dotted key)`` pairs already warned about in this process, so
#: each unknown key is reported once per invocation.
_warned: set[tuple[str, str]] = set()


def _warn(text: str) -> None:
    try:
        print(f"workflow-controller: warning: {text}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 -- a closed stderr is not a configuration failure
        pass


def warn_unknown(loaded: SettingsFile) -> None:
    """The A.5 warning: one stderr line naming the unknown keys not yet
    reported by this process. A file last filled by a newer release says
    so, since its keys are probably that release's."""
    fresh = [key for key in loaded.unknown if (str(loaded.path), key) not in _warned]
    if not fresh:
        return
    _warned.update((str(loaded.path), key) for key in fresh)
    if loaded.table_generation is not None and loaded.table_generation > TABLE_GENERATION:
        why = (f"the file was last filled by a newer Controller release (table generation "
               f"{loaded.table_generation}, this release's is {TABLE_GENERATION}), whose keys these probably are")
    else:
        why = "`workflow-controller settings clean` removes them"
    _warn(f"the settings file {loaded.path} has unknown key(s) {', '.join(fresh)}; they are ignored ({why})")


def empty(path: Path) -> SettingsFile:
    """The loaded form of no file at ``path``: every setting takes its
    built-in default. Reads nothing."""
    return _settings_file(path, None)[0]


def load(path: Path) -> SettingsFile:
    """Read and validate the settings file without writing anything (the
    read-only commands, A.4). A missing file loads as empty: every setting
    takes its built-in default."""
    loaded, _data, _checked = _settings_file(path, _read(path))
    warn_unknown(loaded)
    return loaded


def _filled(data: dict) -> dict:
    """``data`` with every missing setting added at its default and every
    untouched value moved forward to a newer default (A.4 steps 3-5)."""
    new = copy.deepcopy(data)
    new.setdefault(_SCHEMA_KEY, SCHEMA_VERSION)
    written = new.setdefault(_DEFAULTS_WRITTEN, {})
    for setting in TABLE:
        segments = _segments(setting.key)
        present, value = _lookup(new, segments)
        record = {"value": default_value(setting), "generation": setting.generation}
        if not present:
            _store(new, segments, default_value(setting))
            written[setting.key] = record
            continue
        recorded = written.get(setting.key)
        if (
            setting.type != TYPE_ROUTING and recorded is not None
            and _same(value, recorded["value"]) and not _same(value, setting.default)
            and setting.generation > recorded["generation"]
        ):
            _store(new, segments, default_value(setting))
            written[setting.key] = record
    new[_TABLE_GENERATION] = max(new.get(_TABLE_GENERATION) or 0, TABLE_GENERATION)
    return new


def _rewrite(path: Path, raw: bytes | None, data: dict, new: dict) -> SettingsFile:
    """Write ``new`` when it differs from ``data`` (I4), then return it
    validated."""
    if raw is not None and _canonical(new) == _canonical(data):
        return _settings_file(path, raw)[0]
    runtime.write_settings_atomically(path, new)
    return _settings_file(path, _canonical(new))[0]


def fill(path: Path) -> SettingsFile:
    """Fill the settings file (the writing commands, A.4): under its lock,
    re-read, validate, add the missing settings, move untouched values to a
    newer default, and write only when something changed.

    An invalid file is :class:`SettingsError` and is not rewritten. When
    the fill cannot write (an unwritable directory or file), one warning
    says so and the file is loaded read-only instead: its values still
    apply, and missing keys take their built-in defaults (I2)."""
    try:
        with runtime.settings_lock(path):
            raw = _read(path)
            _loaded, data, _checked = _settings_file(path, raw)
            loaded = _rewrite(path, raw, data, _filled(data))
    except (OSError, RuntimeContainmentError) as exc:
        _warn(f"could not fill the settings file {path} ({exc}); using its current values, "
              f"and the built-in defaults for missing keys")
        return load(path)
    warn_unknown(loaded)
    return loaded


def clean(path: Path) -> tuple[SettingsFile, list[str]]:
    """``settings clean`` (A.5): under the lock, fill the file, then remove
    every unknown key and its ``_defaults_written`` entry. Returns the
    file and the dotted keys removed.

    Refuses, removing nothing, when the file was last filled by a newer
    release: this release cannot tell that release's keys from retired
    ones."""
    with runtime.settings_lock(path):
        raw = _read(path)
        _loaded, data, checked = _settings_file(path, raw)
        stored = checked.table_generation
        if stored is not None and stored > TABLE_GENERATION:
            raise _refuse(
                path, _TABLE_GENERATION,
                f"it was last filled by a newer Controller release (table generation {stored}, this "
                f"release's is {TABLE_GENERATION}), so this release cannot tell that release's keys from "
                f"retired ones; run `workflow-controller settings clean` from the newest installed release",
                file_generation=stored, table_generation=TABLE_GENERATION,
            )
        new = _filled(data)
        unknown = _validate(new, path).unknown
        for dotted, segments in unknown:
            _drop(new, segments)
            for key in [key for key in new[_DEFAULTS_WRITTEN] if key == dotted or key.startswith(dotted + ".")]:
                del new[_DEFAULTS_WRITTEN][key]
        return _rewrite(path, raw, data, new), [dotted for dotted, _ in unknown]


# ---------------------------------------------------------------------------
# Effective values.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class EffectiveSettings:
    """Every setting's effective value and its source (I1): a CLI flag
    given on the command line, else the file, else the built-in default.
    ``routing_config`` is the routing in force: ``--routing-config``'s
    file when given (it replaces the section whole), else the file's
    section, else ``None`` (the built-in routes)."""

    path: Path
    sha256: str | None
    values: Mapping[str, Any]
    sources: Mapping[str, str]
    routing_config: routing.RoutingConfig | None

    def __getitem__(self, key: str) -> Any:
        return self.values[key]


def resolve(loaded: SettingsFile, *, cli: Mapping[str, Any] | None = None,
            cli_routing: routing.RoutingConfig | None = None) -> EffectiveSettings:
    """The effective settings from a loaded file and the CLI's overrides
    (``cli``: key -> value, ``None`` meaning not given)."""
    cli = cli or {}
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for setting in TABLE:
        if setting.type == TYPE_ROUTING:
            continue
        if cli.get(setting.key) is not None:
            values[setting.key], sources[setting.key] = cli[setting.key], SOURCE_CLI
        elif setting.key in loaded.values:
            values[setting.key], sources[setting.key] = loaded.values[setting.key], SOURCE_FILE
        else:
            values[setting.key], sources[setting.key] = default_value(setting), SOURCE_DEFAULT
    if cli_routing is not None:
        config, source = cli_routing, SOURCE_CLI
    elif loaded.routing_config is not None:
        config, source = loaded.routing_config, SOURCE_FILE
    else:
        config, source = None, SOURCE_DEFAULT
    values[ROUTING_KEY] = default_value(_table()[ROUTING_KEY]) if config is None else routing_mapping(config)
    sources[ROUTING_KEY] = source
    return EffectiveSettings(
        path=loaded.path, sha256=loaded.sha256, values=MappingProxyType(values),
        sources=MappingProxyType(sources), routing_config=config,
    )


# ---------------------------------------------------------------------------
# The process-wide leaf values (CP2, Design B).
# ---------------------------------------------------------------------------


#: Each leaf setting with no options path, and the module attribute its
#: process-wide value lives in (``None``: the module's built-in constant).
PROCESS_DEFAULTS: tuple[tuple[str, Any, str], ...] = (
    ("timeouts.git_seconds", gitrepo, "process_timeout_seconds"),
    ("timeouts.release_command_seconds", release_txn, "process_command_timeout_seconds"),
    ("timeouts.workflow_query_seconds", workflow_contract, "process_query_timeout_seconds"),
    ("forge.pr_list_limit", forge, "process_pr_list_limit"),
)


def _set_process_values(values: Mapping[str, Any]) -> None:
    for key, module, attribute in PROCESS_DEFAULTS:
        setattr(module, attribute, values.get(key))


def apply_process_defaults(effective: EffectiveSettings) -> None:
    """Set the process-wide leaf values from ``effective``. Called once, by
    ``cli.main``, after the settings are resolved and before any thread
    starts."""
    _set_process_values({key: effective[key] for key, _module, _attribute in PROCESS_DEFAULTS})


@contextlib.contextmanager
def process_defaults(values: Mapping[str, Any]):
    """The tests' writer of the process-wide leaf values: set ``values``
    (setting key -> value; a key left out is the built-in default) for the
    block, then restore what was there."""
    saved = {key: getattr(module, attribute) for key, module, attribute in PROCESS_DEFAULTS}
    _set_process_values(values)
    try:
        yield
    finally:
        _set_process_values(saved)
