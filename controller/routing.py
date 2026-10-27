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

The ``harness`` field (H1) is a third routable field: which agent harness
runs the role. Its vocabulary is not the ``claude`` CLI's but this
repository's, the registry ``controller.harness.ADAPTERS`` (the reviewer
alias ``claude`` is not accepted), and a routing entry is checked against the
roles the named adapter declares when the routing is loaded
(:func:`check_role_harness`). It resolves independently of model and effort,
from ``--role-harness`` (``role-cli``), the config's role entry
(``config-role``), its ``default`` (``config-default``), then the built-in
``claude-code`` (``default``); there is no global option.

``single_agent`` is never overridable: a review role, and the final
self-review pass of ``/milestone-implement``, always run with the
subagent-spawning tools (:data:`SUBAGENT_TOOLS`) disallowed. Model and
effort strings are checked against the harness that will run the role
(:func:`check_route_values`, H8): the adapter's declared ``models`` and
``efforts``, plus the models the ``harness.models`` setting adds, so a route
the harness cannot take is refused when the options are built, before any job
record is written; the ``claude`` CLI is no longer the only authority. A value
must also be a non-empty
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

from controller import harness
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
#: The two roles only a protocol target reaches (orchestration-protocol-v1
#: C.3): the legacy decision path never launches either command, so neither
#: is in :data:`ROLE_BY_COMMAND_STEM`.
PREPARE_FUNCTIONAL_REVIEW = "prepare-functional-review"
APPLY_FUNCTIONAL_REVIEW = "apply-functional-review"
#: The pull-request defect role (pr-defect-loop D3): ``/apply-pr-review``
#: fixes code on the bounded branch and delegates like the other apply roles.
APPLY_PR_REVIEW = "apply-pr-review"
#: The validator roles (gate-policy D4, cleanup-2 D2): ``/satisfy-gate`` records
#: a decision in a transaction that commits, so it is single-agent like the
#: reviews. Plan and technical approval are a mechanical check (Haiku);
#: acceptance also writes the narrative, roadmap and findings table (Sonnet).
SATISFY_GATE = "satisfy-gate"
SATISFY_GATE_ACCEPTANCE = "satisfy-gate-acceptance"
#: The independent functional tester (workflow-controller-automated-
#: functional-review D2): a Controller-run job in an isolated sandbox, not a
#: lifecycle worker. It judges and reports, so single-agent; Sonnet at medium
#: effort because a test run is cheaper than an implementation (user rule of
#: 2026-10-09). No command stem maps to it: only the step at row 38e launches
#: it, so it is not in :data:`ROLE_BY_COMMAND_STEM`.
FUNCTIONAL_TESTER = "functional-tester"

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
_HAIKU_SINGLE_AGENT = Route(model="claude-haiku-5-5", effort="high", single_agent=True)
_SONNET_SINGLE_AGENT = Route(model="claude-sonnet-5-5", effort="medium", single_agent=True)
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
    # It writes a checklist, as `milestone-plan` and the `record-manual-*`
    # commands write their records.
    PREPARE_FUNCTIONAL_REVIEW: _INHERIT,
    # It classifies and fixes findings, as `apply-plan-review` does.
    APPLY_FUNCTIONAL_REVIEW: _OPUS,
    # It reopens a work item and fixes a pull request's defect, as the other
    # apply roles do.
    APPLY_PR_REVIEW: _OPUS,
    SATISFY_GATE: _HAIKU_SINGLE_AGENT,
    SATISFY_GATE_ACCEPTANCE: _SONNET_SINGLE_AGENT,
    FUNCTIONAL_TESTER: _SONNET_SINGLE_AGENT,
})

#: The closed set of role names ``--role-model``/``--role-effort`` and the
#: config file's ``roles`` object accept.
ROLES: frozenset[str] = frozenset(ROLE_ROUTES)

#: The Claude Code tools that spawn a subagent or a workflow, disallowed
#: (``--disallowedTools``) for every single-agent worker. Confirmed against
#: the installed CLI (2.1.281): the subagent tool is ``Agent`` (the session's
#: tool list names it ``Task``, and disallowing ``Agent`` removes it), and
#: ``Workflow`` runs a workflow script. ``Skill`` belongs here too: CP9's
#: live single-agent probe showed a worker with only ``Agent`` and
#: ``Workflow`` disallowed running a ``context: fork`` skill in a subagent.
#: Disallowing ``Skill`` leaves the task's own slash command working: the
#: CLI expands a ``-p`` prompt's command itself, without the tool.
SUBAGENT_TOOLS: tuple[str, ...] = ("Agent", "Workflow", "Skill")

#: The Bash prefixes disallowed for every worker launched under an active
#: milestone-branch binding (``workflow-controller-trunk-branch-pr-release-
#: orchestration`` CP8): the forge CLI, and the Git commands that publish,
#: rewrite or move the branch. Claude Code matches ``Bash(<prefix>:*)``
#: against the start of the command, so ``Bash(git push:*)`` blocks every
#: ``git push ...``. ``git merge``, ``git branch`` and ``git tag`` are
#: deliberately absent: their prefixes would also block the read-only
#: ``git merge-base``, ``git branch --show-current`` and ``git tag -l`` that
#: Workflow commands run. This is defense in depth only -- prefix matching
#: is bypassable (``bash -c``); the post-step branch verification is the
#: guarantee.
BRANCH_GUARD_TOOLS: tuple[str, ...] = (
    "Bash(gh:*)",
    "Bash(git push:*)",
    "Bash(git rebase:*)",
    "Bash(git switch:*)",
    "Bash(git checkout -b:*)",
    "Bash(git reset --hard:*)",
)


#: The Claude Code tools disallowed for every worker, whatever its route
#: (``workflow-controller-worker-lifecycle-ownership`` CP3, limitation H3):
#: a recurring or remote schedule has no point at which the owned work is
#: finished, so it cannot be owned to termination. ``Monitor``,
#: ``ScheduleWakeup``, background Bash and background subagents stay
#: allowed: the Controller keeps them owned.
ASYNC_UNOWNABLE_TOOLS: tuple[str, ...] = ("CronCreate", "CronDelete", "RemoteTrigger")


def worker_disallowed_tools(route: "ResolvedRoute", *, branch_bound: bool) -> tuple[str, ...]:
    """The launched worker's ``--disallowedTools``: the route's own
    (:data:`SUBAGENT_TOOLS` for a single-agent route), plus
    :data:`BRANCH_GUARD_TOOLS` when a milestone-branch binding governs the
    step, plus :data:`ASYNC_UNOWNABLE_TOOLS` for every worker, last."""
    tools = route.disallowed_tools
    if branch_bound:
        tools = tools + tuple(tool for tool in BRANCH_GUARD_TOOLS if tool not in tools)
    return tools + tuple(tool for tool in ASYNC_UNOWNABLE_TOOLS if tool not in tools)

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

#: The three routable fields.
FIELDS: tuple[str, ...] = ("model", "effort", "harness")

#: The key of a role entry that lists the role's further routes, in order
#: (workflow-controller-fallback-chain D1). Not a routable field: it is
#: read and removed before the entry's own fields are checked.
FALLBACK_KEY = "fallback"
#: The keys a fallback entry may hold; ``effort`` is the only optional one.
FALLBACK_ENTRY_KEYS: tuple[str, ...] = ("harness", "model", "effort")


def check_value(value: object) -> str:
    """A model or effort value: a non-empty string that does not begin with
    ``-`` (it would otherwise be read as an option of its own). Its
    vocabulary is the ``claude`` CLI's to judge. Raises ``ValueError``."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected a non-empty string, got {value!r}")
    if value.startswith("-"):
        raise ValueError(f"{value!r} begins with '-', so it would be read as an option")
    return value


def check_harness(value: object) -> str:
    """A harness name: a key of :data:`controller.harness.ADAPTERS`. Unlike a
    model or an effort, the vocabulary belongs to this repository; the
    reviewer alias ``claude`` is not a harness name. Raises ``ValueError``."""
    if not isinstance(value, str) or value not in harness.ADAPTERS:
        raise ValueError(f"expected a registered harness name, got {value!r}; registered: {sorted(harness.ADAPTERS)}")
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


#: The adapter role the Controller launches each routing role as: the
#: functional tester is its own, every other routing role is a lifecycle
#: worker session. Held equal to :data:`ROLES` by a test.
ADAPTER_ROLE: Mapping[str, str] = MappingProxyType({
    role: harness.FUNCTIONAL_TESTER if role == FUNCTIONAL_TESTER else harness.WORKER for role in ROLE_ROUTES
})


def check_role_harness(role: str, name: str) -> str:
    """``name`` must be a registered harness whose adapter declares the
    adapter role ``role`` is launched as (:data:`ADAPTER_ROLE`). Raises
    ``ValueError`` naming the role, the harness and what it declares."""
    check_harness(name)
    adapter_role = ADAPTER_ROLE[role]
    declared = harness.ADAPTERS[name].roles
    if adapter_role not in declared:
        raise ValueError(
            f"role {role!r} runs as a {adapter_role!r} and harness {name!r} declares only {list(declared)}"
        )
    return name


def parse_role_harness(text: str) -> tuple[str, str]:
    """``ROLE=HARNESS`` (``--role-harness``) -> ``(role, harness)``. Raises
    ``ValueError`` for a missing ``=``, an unknown role, an unregistered
    harness or a pair the registry refuses (:func:`check_role_harness`)."""
    role, sep, name = text.partition("=")
    if not sep:
        raise ValueError(f"expected ROLE=HARNESS, got {text!r}")
    return check_role(role), check_role_harness(role, name)


CONFIG_SCHEMA_VERSION = 1


@dataclasses.dataclass(frozen=True)
class RoutingConfig:
    """A parsed ``--routing-config`` file: ``default`` and each ``roles``
    entry map a subset of :data:`FIELDS` to a value."""

    path: str
    default: Mapping[str, str]
    roles: Mapping[str, Mapping[str, str]]
    #: Role -> its fallback entries, in order (each a mapping of
    #: :data:`FALLBACK_ENTRY_KEYS`); a role without a chain is absent.
    chains: Mapping[str, tuple[Mapping[str, str], ...]] = dataclasses.field(
        default_factory=lambda: MappingProxyType({}))


def _refuse(path: str, problem: str, **evidence: Any) -> RoutingConfigError:
    return RoutingConfigError(
        f"the routing config {path} cannot be used: {problem}",
        evidence={"path": path, **evidence},
    )


#: :func:`validate_routing_mapping`'s two ways with a key it does not know.
UNKNOWN_REFUSE = "refuse"
UNKNOWN_IGNORE = "ignore"

#: The keys a routing mapping may hold besides ``schema_version``. With
#: :data:`ROLES` and :data:`FIELDS`, part of the settings table
#: ``controller.settings`` pins: a release that adds one raises its
#: ``TABLE_GENERATION``.
SECTION_KEYS: tuple[str, ...] = ("default", "roles")


class _Validator:
    """One validation of a parsed routing mapping. ``where`` prefixes every
    location it names (``""`` for a ``--routing-config`` file, so that
    file's messages and evidence stay what they were; ``"routing."`` for
    the settings file's section, whose refusals also carry the dotted
    ``key``)."""

    def __init__(self, path: str, where: str, unknown: str) -> None:
        if unknown not in (UNKNOWN_REFUSE, UNKNOWN_IGNORE):
            raise ValueError(f"unknown must be {UNKNOWN_REFUSE!r} or {UNKNOWN_IGNORE!r}, got {unknown!r}")
        self.path, self.where, self.unknown = path, where, unknown
        self.ignored: list[str] = []

    def refuse(self, problem: str, key: str, **evidence: Any) -> RoutingConfigError:
        if self.where:
            evidence["key"] = key
        return _refuse(self.path, problem, **evidence)

    def field_entry(self, where: str, entry: object) -> Mapping[str, str]:
        if not isinstance(entry, dict):
            raise self.refuse(f"{where} must be an object, got {entry!r}", where, where=where)
        unknown = sorted(set(entry) - set(FIELDS))
        if unknown and self.unknown == UNKNOWN_REFUSE:
            raise self.refuse(f"{where} carries unknown key(s) {unknown} (only {list(FIELDS)} are routable)",
                              where, where=where, unknown_keys=unknown)
        self.ignored.extend(f"{where}.{field}" for field in unknown)
        checked: dict[str, str] = {}
        for field, value in entry.items():
            if field not in FIELDS:
                continue
            try:
                checked[field] = check_harness(value) if field == "harness" else check_value(value)
            except ValueError as exc:
                raise self.refuse(f"{where}.{field}: {exc}", f"{where}.{field}", where=f"{where}.{field}") from None
        return MappingProxyType(checked)

    def fallback_chain(self, role: str, where: str, raw: object) -> tuple[Mapping[str, str], ...]:
        """One role's ``fallback`` list (``where`` is its location, e.g.
        ``roles.review-plan.fallback``): a non-empty list of objects, each
        naming a ``harness`` the role may take and a ``model``, with an
        optional ``effort`` and no other key -- refused under either unknown
        rule, since an unknown key could change which route the entry means
        (D2). Entries are counted from 1 after the role's own route."""
        if not isinstance(raw, list) or not raw:
            raise self.refuse(f"{where} must be a non-empty list, got {raw!r}", where, where=where, role=role)
        entries: list[Mapping[str, str]] = []
        for position, entry in enumerate(raw, start=1):
            here = f"{where}[{position}]"
            evidence: dict[str, Any] = {"where": here, "role": role, "entry": position}
            if not isinstance(entry, dict):
                raise self.refuse(f"{here} must be an object, got {entry!r}", here, **evidence)
            unknown = sorted(set(entry) - set(FALLBACK_ENTRY_KEYS))
            if unknown:
                key = f"{here}.{unknown[0]}"
                raise self.refuse(
                    f"{key} is not a key of a fallback entry (only {list(FALLBACK_ENTRY_KEYS)} are), so the "
                    f"route it would change cannot be known", key, **{**evidence, "unknown_keys": unknown})
            for needed in ("harness", "model"):
                if needed not in entry:
                    raise self.refuse(f"{here} must name a {needed}", f"{here}.{needed}", **evidence)
            checked: dict[str, str] = {}
            for field in FALLBACK_ENTRY_KEYS:
                if field not in entry:
                    continue
                key = f"{here}.{field}"
                try:
                    checked[field] = check_harness(entry[field]) if field == "harness" else check_value(entry[field])
                except ValueError as exc:
                    raise self.refuse(f"{key}: {exc}", key, **{**evidence, "where": key}) from None
            self._check_pair(f"{here}.harness", role, checked["harness"], entry=position)
            frozen = MappingProxyType(checked)
            if any(dict(frozen) == dict(earlier) for earlier in entries):
                raise self.refuse(f"{here} repeats an earlier fallback entry of {where}", here, **evidence)
            entries.append(frozen)
        return tuple(entries)

    def check_harness_pairs(self, default_where: str, default: Mapping[str, str], roles_where: str,
                            roles: Mapping[str, Mapping[str, str]]) -> None:
        """Refuse a ``harness`` the registry does not let its role take: each
        role entry's own, and ``default``'s against every role with no
        ``harness`` of its own (first in sorted order, counting the rest).
        Deliberately ignorant of ``--role-harness`` (D4)."""
        for role in sorted(roles):
            name = roles[role].get("harness")
            if name is not None:
                self._check_pair(f"{roles_where}.{role}.harness", role, name)
        name = default.get("harness")
        if name is None:
            return
        covered = [role for role in sorted(ROLES) if "harness" not in roles.get(role, {})]
        for position, role in enumerate(covered):
            try:
                check_role_harness(role, name)
            except ValueError:
                others = len(covered) - position - 1
                self._check_pair(f"{default_where}.harness", role, name,
                                 also=f" (and {others} further role(s) the default would apply to)" if others else "")

    def _check_pair(self, key: str, role: str, name: str, also: str = "", **extra: Any) -> None:
        try:
            check_role_harness(role, name)
        except ValueError as exc:
            raise self.refuse(f"{key}: {exc}{also}", key, where=key,
                              **_pair_refusal_evidence(role, name), **extra) from None


def _pair_refusal_evidence(role: str, name: str) -> dict[str, Any]:
    return {"role": role, "adapter_role": ADAPTER_ROLE[role], "harness": name,
            "declared_roles": list(harness.ADAPTERS[name].roles)}


def validate_routing_mapping(
    data: object, *, path: str, where: str = "", require_schema_version: bool = True,
    unknown: str = UNKNOWN_REFUSE,
) -> tuple[RoutingConfig, list[str]]:
    """Validate an already-parsed routing mapping: the ``--routing-config``
    file's top level (``require_schema_version=True``, ``unknown="refuse"``,
    ``where=""``), or the settings file's ``routing`` section
    (``require_schema_version=False``, ``unknown="ignore"``,
    ``where="routing."``). Returns the config and the dotted paths left out
    as unknown (always empty under ``"refuse"``).

    Under ``"ignore"`` an unknown key directly under the mapping, an unknown
    role name and an unknown route field are left out, so a newer
    release's additions never make a shared file unusable. Everything else
    stays strict: a section or entry that is not an object, a value
    :func:`check_value` refuses, and -- when ``require_schema_version`` is
    false -- a ``schema_version`` key, which is reserved (it marks a
    ``--routing-config`` file pasted in whole). Every problem is
    :class:`~controller.errors.RoutingConfigError`, never a partial
    config."""
    v = _Validator(path, where, unknown)
    if not isinstance(data, dict):
        if where:
            raise v.refuse(f"{where.rstrip('.')} must be an object, got {data!r}", where.rstrip("."))
        raise _refuse(path, "its top level must be a JSON object")
    allowed = set(SECTION_KEYS) | ({"schema_version"} if require_schema_version else set())
    unknown_keys = sorted(set(data) - allowed - {"schema_version"})
    if unknown_keys and unknown == UNKNOWN_REFUSE:
        raise _refuse(path, f"unknown top-level key(s) {unknown_keys}", unknown_keys=unknown_keys)
    v.ignored.extend(f"{where}{key}" for key in unknown_keys)
    if require_schema_version:
        version = data.get("schema_version")
        if type(version) is not int or version != CONFIG_SCHEMA_VERSION:
            raise _refuse(path, f"schema_version must be {CONFIG_SCHEMA_VERSION}, got {version!r}",
                          schema_version=version)
    elif "schema_version" in data:
        raise v.refuse(
            f"{where}schema_version is reserved: a routing section carries no schema_version "
            f"(it looks like a --routing-config file pasted in whole; drop the key)",
            f"{where}schema_version",
        )
    default_entry = data.get("default", {})
    if isinstance(default_entry, dict) and FALLBACK_KEY in default_entry:
        raise v.refuse(
            f"{where}default.{FALLBACK_KEY} is not allowed: a chain in default would have to be valid for every "
            f"role, and the roles do not run as the same adapter role; give the role its own {FALLBACK_KEY}",
            f"{where}default.{FALLBACK_KEY}", where=f"{where}default.{FALLBACK_KEY}")
    default = v.field_entry(f"{where}default", default_entry)
    roles_entry = data.get("roles", {})
    if not isinstance(roles_entry, dict):
        if where:
            raise v.refuse(f"{where}roles must be an object, got {roles_entry!r}", f"{where}roles")
        raise _refuse(path, f"roles must be an object, got {roles_entry!r}")
    unknown_roles = sorted(set(roles_entry) - ROLES)
    if unknown_roles and unknown == UNKNOWN_REFUSE:
        raise _refuse(path, f"unknown role(s) {unknown_roles}; known roles: {', '.join(sorted(ROLES))}",
                      unknown_roles=unknown_roles)
    v.ignored.extend(f"{where}roles.{role}" for role in unknown_roles)
    roles: dict[str, Mapping[str, str]] = {}
    chains: dict[str, tuple[Mapping[str, str], ...]] = {}
    for role, entry in roles_entry.items():
        if role not in ROLES:
            continue
        if isinstance(entry, dict) and FALLBACK_KEY in entry:
            chains[role] = v.fallback_chain(role, f"{where}roles.{role}.{FALLBACK_KEY}", entry[FALLBACK_KEY])
            entry = {key: value for key, value in entry.items() if key != FALLBACK_KEY}
        roles[role] = v.field_entry(f"{where}roles.{role}", entry)
    v.check_harness_pairs(f"{where}default", default, f"{where}roles", roles)
    config = RoutingConfig(path=path, default=default, roles=MappingProxyType(roles),
                           chains=MappingProxyType(chains))
    return config, v.ignored


def parse_routing_config(text: str, *, path: str) -> RoutingConfig:
    """Parse and validate a ``--routing-config`` file's text, strictly
    (:func:`validate_routing_mapping` with ``schema_version`` required and
    every unknown key refused). Every problem is
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
    config, _ignored = validate_routing_mapping(data, path=path)
    return config


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
#: A later entry of a role's fallback chain (:meth:`RoutingOptions.chain`).
SOURCE_CONFIG_CHAIN = "config-chain"
SOURCE_DEFAULT = "default"
SOURCE_INHERIT = "inherit"
#: The harness a role runs on when no routing entry names one.
DEFAULT_HARNESS = harness.CLAUDE_CODE.name
SOURCES: tuple[str, ...] = (
    SOURCE_ROLE_CLI, SOURCE_CLI, SOURCE_CONFIG_ROLE, SOURCE_CONFIG_DEFAULT, SOURCE_CONFIG_CHAIN, SOURCE_DEFAULT,
    SOURCE_INHERIT,
)


#: Which routing config was in force (``worker_route.config_source``,
#: settings-and-telemetry CP2): the settings file's ``routing`` section,
#: the ``--routing-config`` file, or none.
CONFIG_SOURCE_SETTINGS = "settings"
CONFIG_SOURCE_ROUTING_CONFIG = "routing-config"
CONFIG_SOURCE_NONE = "none"
CONFIG_SOURCES: tuple[str, ...] = (CONFIG_SOURCE_SETTINGS, CONFIG_SOURCE_ROUTING_CONFIG, CONFIG_SOURCE_NONE)


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
    harness: str = DEFAULT_HARNESS
    harness_source: str = SOURCE_DEFAULT

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
            "harness": self.harness,
            "single_agent": self.single_agent,
            "fresh_session": True,
            "sources": {
                "model": self.model_source, "effort": self.effort_source, "harness": self.harness_source,
            },
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
    cli_role_harnesses: Mapping[str, str] | None = None, config: RoutingConfig | None = None,
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
    route_harness, harness_source = _resolve_field(
        "harness", role, role_cli=cli_role_harnesses or {}, cli=None, config=config, builtin=DEFAULT_HARNESS,
    )
    return ResolvedRoute(
        role=role, model=model, effort=effort, single_agent=builtin.single_agent,
        model_source=model_source, effort_source=effort_source,
        harness=route_harness or DEFAULT_HARNESS, harness_source=harness_source,
    )


def check_route_values(
    role: str, harness_name: str, model: str | None, effort: str | None,
    extra_models: Mapping[str, tuple[str, ...]],
) -> None:
    """Judge a resolved route against the harness that runs it (H8).
    ``None`` for the model or the effort means "inherit" and is never
    refused. A model passes when the adapter declares it or ``extra_models``
    (the ``harness.models`` setting) adds it for that harness; an effort
    passes when the adapter declares it. Raises ``ValueError`` naming the
    role, the harness, the field, the value and what the harness declares.
    Reads the registry at call time."""
    adapter = harness.get(harness_name)
    if model is not None:
        allowed = tuple(adapter.models) + tuple(extra_models.get(harness_name, ()))
        if model not in allowed:
            raise ValueError(
                f"role {role!r} routes the model {model!r} to the harness {harness_name!r}, "
                f"which declares {list(allowed)}"
            )
    if effort is not None and effort not in adapter.efforts:
        raise ValueError(
            f"role {role!r} routes the effort {effort!r} to the harness {harness_name!r}, "
            f"which declares {list(adapter.efforts)}"
        )


def _source_words(source: str) -> str:
    return "the built-in default" if source == SOURCE_DEFAULT else f"the {source} value"


def _frozen_assignments(assignments: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({check_role(role): check_value(value) for role, value in dict(assignments).items()})


def _frozen_harness_assignments(assignments: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({
        check_role(role): check_role_harness(role, name) for role, name in dict(assignments).items()
    })


@dataclasses.dataclass(frozen=True)
class RoutingOptions:
    """The operator's routing overrides for one Controller invocation: the
    command line's (``--model``, ``--effort``, ``--role-model``,
    ``--role-effort``, ``--role-harness``) and the routing config in force, if any -- the
    ``--routing-config`` file, else the settings file's ``routing`` section
    (settings-and-telemetry CP2, I1). ``config_source`` names which
    (:data:`CONFIG_SOURCES`; ``None`` derives it: ``none`` without a config,
    else ``routing-config``). :data:`NO_OVERRIDES` is the built-in routing
    alone."""

    cli_model: str | None = None
    cli_effort: str | None = None
    cli_role_models: Mapping[str, str] = dataclasses.field(default_factory=dict)
    cli_role_efforts: Mapping[str, str] = dataclasses.field(default_factory=dict)
    cli_role_harnesses: Mapping[str, str] = dataclasses.field(default_factory=dict)
    config: RoutingConfig | None = None
    config_source: str | None = None
    #: The ``harness.models`` setting: models added per harness name (H8).
    harness_models: Mapping[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.config_source is None:
            derived = CONFIG_SOURCE_NONE if self.config is None else CONFIG_SOURCE_ROUTING_CONFIG
            object.__setattr__(self, "config_source", derived)
        if self.config_source not in CONFIG_SOURCES:
            raise ValueError(f"config_source {self.config_source!r} is not one of {CONFIG_SOURCES}")
        for name in ("cli_model", "cli_effort"):
            value = getattr(self, name)
            if value is not None:
                check_value(value)
        object.__setattr__(self, "cli_role_models", _frozen_assignments(self.cli_role_models))
        object.__setattr__(self, "cli_role_efforts", _frozen_assignments(self.cli_role_efforts))
        object.__setattr__(self, "cli_role_harnesses", _frozen_harness_assignments(self.cli_role_harnesses))
        object.__setattr__(self, "harness_models", MappingProxyType({
            name: tuple(models) for name, models in dict(self.harness_models).items()
        }))
        for role in sorted(ROLES):
            route = self.resolve(role)
            try:
                check_route_values(role, route.harness, route.model, route.effort, self.harness_models)
            except ValueError as exc:
                raise RoutingConfigError(
                    f"{exc} (model from {_source_words(route.model_source)}, effort from "
                    f"{_source_words(route.effort_source)}, harness from {_source_words(route.harness_source)})"
                    + ("; add a model the harness accepts with the harness.models setting"
                       if "routes the model" in str(exc) else ""),
                    evidence={
                        "role": role, "harness": route.harness, "model": route.model, "effort": route.effort,
                        "sources": {
                            "model": route.model_source, "effort": route.effort_source,
                            "harness": route.harness_source,
                        },
                    },
                ) from exc
            for position, later in enumerate(self.chain(role)[1:], start=1):
                try:
                    check_route_values(role, later.harness, later.model, later.effort, self.harness_models)
                except ValueError as exc:
                    raise RoutingConfigError(
                        f"{exc} (fallback entry {position} of the role's chain)"
                        + ("; add a model the harness accepts with the harness.models setting"
                           if "routes the model" in str(exc) else ""),
                        evidence={
                            "role": role, "entry": position, "harness": later.harness, "model": later.model,
                            "effort": later.effort,
                            "sources": {
                                "model": later.model_source, "effort": later.effort_source,
                                "harness": later.harness_source,
                            },
                        },
                    ) from exc

    def resolve(self, role: str) -> ResolvedRoute:
        return resolve_route(
            role, cli_model=self.cli_model, cli_effort=self.cli_effort,
            cli_role_models=self.cli_role_models, cli_role_efforts=self.cli_role_efforts,
            cli_role_harnesses=self.cli_role_harnesses, config=self.config,
        )

    def chain(self, role: str) -> tuple[ResolvedRoute, ...]:
        """``role``'s whole route chain (workflow-controller-fallback-chain
        D5): entry 0 is exactly :meth:`resolve`, each later entry is one of
        the configuration's fallback entries, sourced ``config-chain`` for
        the fields it names (an omitted effort is ``inherit``). The
        command-line overrides change entry 0 only. Nothing selects a later
        entry yet."""
        first = self.resolve(role)
        if self.config is None:
            return (first,)
        single = ROLE_ROUTES[check_role(role)].single_agent
        later = tuple(
            ResolvedRoute(
                role=role, model=entry["model"], effort=entry.get("effort"), single_agent=single,
                model_source=SOURCE_CONFIG_CHAIN,
                effort_source=SOURCE_CONFIG_CHAIN if "effort" in entry else SOURCE_INHERIT,
                harness=entry["harness"], harness_source=SOURCE_CONFIG_CHAIN,
            ) for entry in self.config.chains.get(role, ())
        )
        return (first, *later)


NO_OVERRIDES = RoutingOptions()
