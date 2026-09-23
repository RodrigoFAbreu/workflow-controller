"""Role-based worker routing (``workflow-controller-automatic-lifecycle-
orchestration`` CP6, "Worker routing").

Every worker the Controller launches has a *role*, derived from durable
state alone -- the observed phase, the selected command and the work
item's ``registry_complete`` (:func:`role_for`). Each role has a built-in
:class:`Route` in :data:`ROLE_ROUTES`: a model, an effort, and whether the
worker must run single-agent. :func:`resolve_route` applies the operator's
overrides to it, per field (model and effort independently), in this
order:

1. ``--role-model ROLE=M`` / ``--role-effort ROLE=E`` (``role-cli``);
2. ``--model`` / ``--effort`` (``cli``);
3. the ``--routing-config`` file's entry for the role (``config-role``);
4. that file's ``default`` entry (``config-default``);
5. the built-in :data:`ROLE_ROUTES` value (``default``);
6. ``inherit``: no flag is passed, so the ``claude`` CLI's own
   configuration applies.

``single_agent`` is never overridable: a review role, and the final
self-review pass of ``/milestone-implement``, always run with the
subagent-spawning tools (:data:`SUBAGENT_TOOLS`) disallowed. Model and
effort strings are passed through to the ``claude`` CLI unvalidated -- it
is the authority over which models and effort levels exist, as it already
is for ``--permission-mode`` -- except that a value must be a non-empty
string that does not begin with ``-``, so it can never be read as an
option of its own (:func:`check_value`). Role *names* are a closed set
(:data:`ROLES`).

Pure apart from :func:`load_routing_config`'s one read of the operator's
config file. Imports nothing from the package beyond ``controller.errors``
and ``controller.decision``.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from controller.decision import command_token
from controller.errors import RoutingConfigError

# ---------------------------------------------------------------------------
# Roles and their built-in routes.
# ---------------------------------------------------------------------------

#: ``/milestone-implement`` from ``IMPLEMENTING`` with a checkpoint
#: outstanding: checkpoint implementation, which may fan out bounded helpers.
MILESTONE_IMPLEMENT = "milestone-implement"
#: ``/milestone-implement`` from ``SELF_REVIEWING_IMPLEMENTATION``, or from
#: ``IMPLEMENTING`` with ``registry_complete`` true: the final pass that runs
#: the command's step-2 full-diff self-review -- judging, so single-agent.
MILESTONE_IMPLEMENT_SELF_REVIEW = "milestone-implement-self-review"
APPLY_PLAN_REVIEW = "apply-plan-review"
APPLY_IMPLEMENTATION_REVIEW = "apply-implementation-review"
REVIEW_PLAN = "review-plan"
REVIEW_IMPLEMENTATION = "review-implementation"
MILESTONE_PLAN = "milestone-plan"
RECORD_MANUAL_PLAN_REVIEW = "record-manual-plan-review"
RECORD_MANUAL_IMPLEMENTATION_REVIEW = "record-manual-implementation-review"

#: The built-in model and effort of the named lifecycle roles.
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "xhigh"


@dataclasses.dataclass(frozen=True)
class Route:
    """A role's built-in route. ``model``/``effort`` ``None`` means inherit
    (no flag passed)."""

    model: str | None
    effort: str | None
    single_agent: bool


_OPUS = Route(model=DEFAULT_MODEL, effort=DEFAULT_EFFORT, single_agent=False)
_OPUS_SINGLE_AGENT = Route(model=DEFAULT_MODEL, effort=DEFAULT_EFFORT, single_agent=True)
_INHERIT = Route(model=None, effort=None, single_agent=False)

#: Role -> built-in :class:`Route`. The roles the milestone scope does not
#: name inherit, which keeps today's behavior for them; the config file can
#: route them explicitly.
ROLE_ROUTES: Mapping[str, Route] = MappingProxyType({
    MILESTONE_IMPLEMENT: _OPUS,
    MILESTONE_IMPLEMENT_SELF_REVIEW: _OPUS_SINGLE_AGENT,
    APPLY_PLAN_REVIEW: _OPUS,
    APPLY_IMPLEMENTATION_REVIEW: _OPUS,
    REVIEW_PLAN: _OPUS_SINGLE_AGENT,
    REVIEW_IMPLEMENTATION: _OPUS_SINGLE_AGENT,
    MILESTONE_PLAN: _INHERIT,
    RECORD_MANUAL_PLAN_REVIEW: _INHERIT,
    RECORD_MANUAL_IMPLEMENTATION_REVIEW: _INHERIT,
})

#: The closed set of role names ``--role-model``/``--role-effort`` and the
#: config file's ``roles`` object accept.
ROLES: frozenset[str] = frozenset(ROLE_ROUTES)

#: The Claude Code tools that spawn a subagent or a workflow, disallowed
#: (``--disallowedTools``) for every single-agent worker. Confirmed against
#: the installed CLI (2.1.281): the subagent tool is ``Agent`` (its legacy
#: name ``Task`` is an alias of it), and ``Workflow`` runs a workflow
#: script. Whether ``Skill`` can reach a forked (subagent) skill, and so
#: belongs here too, is settled by CP9's live single-agent probe.
SUBAGENT_TOOLS: tuple[str, ...] = ("Agent", "Workflow")

# ---------------------------------------------------------------------------
# Role derivation.
# ---------------------------------------------------------------------------

_IMPLEMENTING = "IMPLEMENTING"
_SELF_REVIEWING_IMPLEMENTATION = "SELF_REVIEWING_IMPLEMENTATION"

#: Command stem -> role, for every command a selected action may be
#: launched for (``controller.decision.SELECTED_COMMANDS``, held equal by a
#: test). ``milestone-implement`` is refined by :func:`role_for`.
ROLE_BY_COMMAND_STEM: Mapping[str, str] = MappingProxyType({
    "milestone-plan": MILESTONE_PLAN,
    "review-plan": REVIEW_PLAN,
    "record-manual-plan-review": RECORD_MANUAL_PLAN_REVIEW,
    "apply-plan-review": APPLY_PLAN_REVIEW,
    "milestone-implement": MILESTONE_IMPLEMENT,
    "review-implementation": REVIEW_IMPLEMENTATION,
    "apply-implementation-review": APPLY_IMPLEMENTATION_REVIEW,
    "record-manual-implementation-review": RECORD_MANUAL_IMPLEMENTATION_REVIEW,
})


def role_for(phase: Any, command: str, registry_complete: bool | None) -> str:
    """The role of a worker launched for ``command`` (``Action.command``,
    e.g. ``"/milestone-implement wi-1"``) at ``phase``, from durable state
    only. ``/milestone-implement`` is the self-review role from
    ``SELF_REVIEWING_IMPLEMENTATION``, and from ``IMPLEMENTING`` when
    ``registry_complete is True`` (the invocation that runs the command's
    step 2); otherwise the checkpoint role.

    Every launchable command has a role, so a miss is an invariant
    violation, never an ordinary path (the same shape as
    ``controller.job._expected_outcome_for``); ``controller.job`` resolves
    the route before its ``PLANNED`` flush, so a miss leaves no record."""
    stem = command_token(command).removeprefix("/")
    role = ROLE_BY_COMMAND_STEM.get(stem)
    if role is None:
        raise AssertionError(f"no routing role is declared for the selected command {command!r}")
    if role == MILESTONE_IMPLEMENT and (
        phase == _SELF_REVIEWING_IMPLEMENTATION or (phase == _IMPLEMENTING and registry_complete is True)
    ):
        return MILESTONE_IMPLEMENT_SELF_REVIEW
    return role


# ---------------------------------------------------------------------------
# Values, role assignments and the config file.
# ---------------------------------------------------------------------------

#: The two routable fields.
FIELDS: tuple[str, ...] = ("model", "effort")


def check_value(value: object) -> str:
    """A model or effort value: a non-empty string that does not begin with
    ``-`` (it would otherwise be read as an option of its own). Its
    vocabulary is the ``claude`` CLI's to judge. Raises ``ValueError``."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected a non-empty string, got {value!r}")
    if value.startswith("-"):
        raise ValueError(f"{value!r} begins with '-', so it would be read as an option")
    return value


def check_role(role: object) -> str:
    """A role name from :data:`ROLES`. Raises ``ValueError``."""
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError(f"unknown role {role!r}; known roles: {', '.join(sorted(ROLES))}")
    return role


def parse_role_assignment(text: str) -> tuple[str, str]:
    """``ROLE=VALUE`` (``--role-model``/``--role-effort``) -> ``(role,
    value)``. Raises ``ValueError`` for a missing ``=``, an unknown role or
    an unusable value."""
    role, sep, value = text.partition("=")
    if not sep:
        raise ValueError(f"expected ROLE=VALUE, got {text!r}")
    return check_role(role), check_value(value)


CONFIG_SCHEMA_VERSION = 1
_CONFIG_TOP_LEVEL_KEYS = frozenset({"schema_version", "default", "roles"})


@dataclasses.dataclass(frozen=True)
class RoutingConfig:
    """A parsed ``--routing-config`` file: ``default`` and each ``roles``
    entry map a subset of :data:`FIELDS` to a value."""

    path: str
    default: Mapping[str, str]
    roles: Mapping[str, Mapping[str, str]]


def _refuse(path: str, problem: str, **evidence: Any) -> RoutingConfigError:
    return RoutingConfigError(
        f"the routing config {path} cannot be used: {problem}",
        evidence={"path": path, **evidence},
    )


def _field_entry(path: str, where: str, entry: object) -> Mapping[str, str]:
    if not isinstance(entry, dict):
        raise _refuse(path, f"{where} must be an object, got {entry!r}", where=where)
    unknown = sorted(set(entry) - set(FIELDS))
    if unknown:
        raise _refuse(path, f"{where} carries unknown key(s) {unknown} (only {list(FIELDS)} are routable)",
                      where=where, unknown_keys=unknown)
    checked: dict[str, str] = {}
    for field, value in entry.items():
        try:
            checked[field] = check_value(value)
        except ValueError as exc:
            raise _refuse(path, f"{where}.{field}: {exc}", where=f"{where}.{field}") from None
    return MappingProxyType(checked)


def parse_routing_config(text: str, *, path: str) -> RoutingConfig:
    """Parse and validate a routing config's text. Every problem is
    :class:`~controller.errors.RoutingConfigError`, never a partial
    config."""

    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
        keys = [key for key, _ in pairs]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise _refuse(path, f"duplicate key(s) {duplicates}", duplicate_keys=duplicates)
        return dict(pairs)

    try:
        data = json.loads(text, object_pairs_hook=no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise _refuse(path, f"it is not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise _refuse(path, "its top level must be a JSON object")
    unknown = sorted(set(data) - _CONFIG_TOP_LEVEL_KEYS)
    if unknown:
        raise _refuse(path, f"unknown top-level key(s) {unknown}", unknown_keys=unknown)
    version = data.get("schema_version")
    if type(version) is not int or version != CONFIG_SCHEMA_VERSION:
        raise _refuse(path, f"schema_version must be {CONFIG_SCHEMA_VERSION}, got {version!r}",
                      schema_version=version)
    default = _field_entry(path, "default", data.get("default", {}))
    roles_entry = data.get("roles", {})
    if not isinstance(roles_entry, dict):
        raise _refuse(path, f"roles must be an object, got {roles_entry!r}")
    unknown_roles = sorted(set(roles_entry) - ROLES)
    if unknown_roles:
        raise _refuse(path, f"unknown role(s) {unknown_roles}; known roles: {', '.join(sorted(ROLES))}",
                      unknown_roles=unknown_roles)
    roles = {role: _field_entry(path, f"roles.{role}", entry) for role, entry in roles_entry.items()}
    return RoutingConfig(path=path, default=default, roles=MappingProxyType(roles))


def load_routing_config(path: str | Path) -> RoutingConfig:
    """Read and parse the ``--routing-config`` file at ``path``. An
    unreadable file is :class:`~controller.errors.RoutingConfigError`
    too."""
    shown = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _refuse(shown, f"it cannot be read ({exc})") from None
    return parse_routing_config(text, path=shown)


# ---------------------------------------------------------------------------
# Resolution.
# ---------------------------------------------------------------------------

#: Where a resolved field came from (``worker_route.sources``), highest
#: precedence first.
SOURCE_ROLE_CLI = "role-cli"
SOURCE_CLI = "cli"
SOURCE_CONFIG_ROLE = "config-role"
SOURCE_CONFIG_DEFAULT = "config-default"
SOURCE_DEFAULT = "default"
SOURCE_INHERIT = "inherit"
SOURCES: tuple[str, ...] = (
    SOURCE_ROLE_CLI, SOURCE_CLI, SOURCE_CONFIG_ROLE, SOURCE_CONFIG_DEFAULT, SOURCE_DEFAULT, SOURCE_INHERIT,
)


@dataclasses.dataclass(frozen=True)
class ResolvedRoute:
    """A role's route after overrides, with where each field came from.
    ``model``/``effort`` ``None`` means the flag is omitted."""

    role: str
    model: str | None
    effort: str | None
    single_agent: bool
    model_source: str
    effort_source: str

    @property
    def disallowed_tools(self) -> tuple[str, ...]:
        """:data:`SUBAGENT_TOOLS` for a single-agent route, else none."""
        return SUBAGENT_TOOLS if self.single_agent else ()

    def to_record(self) -> dict:
        """The job record's ``worker_route``. ``fresh_session`` is always
        true: ``controller.worker.launch`` never passes a session-reuse
        flag."""
        return {
            "role": self.role,
            "model": self.model,
            "effort": self.effort,
            "single_agent": self.single_agent,
            "fresh_session": True,
            "sources": {"model": self.model_source, "effort": self.effort_source},
        }


def _resolve_field(
    field: str, role: str, *, role_cli: Mapping[str, str], cli: str | None,
    config: RoutingConfig | None, builtin: str | None,
) -> tuple[str | None, str]:
    if role in role_cli:
        return role_cli[role], SOURCE_ROLE_CLI
    if cli is not None:
        return cli, SOURCE_CLI
    if config is not None:
        role_entry = config.roles.get(role, {})
        if field in role_entry:
            return role_entry[field], SOURCE_CONFIG_ROLE
        if field in config.default:
            return config.default[field], SOURCE_CONFIG_DEFAULT
    if builtin is not None:
        return builtin, SOURCE_DEFAULT
    return None, SOURCE_INHERIT


def resolve_route(
    role: str, *, cli_model: str | None = None, cli_effort: str | None = None,
    cli_role_models: Mapping[str, str] | None = None, cli_role_efforts: Mapping[str, str] | None = None,
    config: RoutingConfig | None = None,
) -> ResolvedRoute:
    """``role``'s route after the overrides, model and effort resolved
    independently by the precedence in this module's docstring.
    ``single_agent`` is the built-in value, whatever the overrides."""
    check_role(role)
    builtin = ROLE_ROUTES[role]
    model, model_source = _resolve_field(
        "model", role, role_cli=cli_role_models or {}, cli=cli_model, config=config, builtin=builtin.model,
    )
    effort, effort_source = _resolve_field(
        "effort", role, role_cli=cli_role_efforts or {}, cli=cli_effort, config=config, builtin=builtin.effort,
    )
    return ResolvedRoute(
        role=role, model=model, effort=effort, single_agent=builtin.single_agent,
        model_source=model_source, effort_source=effort_source,
    )


def _frozen_assignments(assignments: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({check_role(role): check_value(value) for role, value in dict(assignments).items()})


@dataclasses.dataclass(frozen=True)
class RoutingOptions:
    """The operator's routing overrides for one Controller invocation: the
    command line's (``--model``, ``--effort``, ``--role-model``,
    ``--role-effort``) and the parsed ``--routing-config`` file, if any.
    :data:`NO_OVERRIDES` is the built-in routing alone."""

    cli_model: str | None = None
    cli_effort: str | None = None
    cli_role_models: Mapping[str, str] = dataclasses.field(default_factory=dict)
    cli_role_efforts: Mapping[str, str] = dataclasses.field(default_factory=dict)
    config: RoutingConfig | None = None

    def __post_init__(self) -> None:
        for name in ("cli_model", "cli_effort"):
            value = getattr(self, name)
            if value is not None:
                check_value(value)
        object.__setattr__(self, "cli_role_models", _frozen_assignments(self.cli_role_models))
        object.__setattr__(self, "cli_role_efforts", _frozen_assignments(self.cli_role_efforts))

    def resolve(self, role: str) -> ResolvedRoute:
        return resolve_route(
            role, cli_model=self.cli_model, cli_effort=self.cli_effort,
            cli_role_models=self.cli_role_models, cli_role_efforts=self.cli_role_efforts,
            config=self.config,
        )


NO_OVERRIDES = RoutingOptions()
