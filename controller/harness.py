"""The harness adapter registry (workflow-controller-adapters-1, D2-D4).

One declaration of what each agent harness is called, which model family it
belongs to, which roles it can take, what it can enforce and how it reports
usage. The module holds plain declarations and imports only
``controller.errors``; the objects that launch a harness (``review.CODEX``,
``review.CLAUDE``, the worker launch) stay where they are, and tests pin
their literals to this registry.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from controller.errors import HarnessRoleError, UnknownHarnessError

WORKER = "worker"
REVIEWER = "reviewer"
#: The Controller-run functional tester (automated-functional-review D2): a
#: shell-only, sandboxed ``claude -p`` job. Only Claude Code declares it.
FUNCTIONAL_TESTER = "functional-tester"

#: How a harness reports usage: exactly, computed from its output, or not.
USAGE_REPORTED = "reported"
USAGE_DERIVED = "derived"
USAGE_NONE = "none"


@dataclasses.dataclass(frozen=True)
class HarnessAdapter:
    """What a harness declares. ``name`` is what the worker records and
    ``telemetry`` prints; ``reviewer_name`` is the key of
    ``review.HARNESSES``, the ``reviewer.harness`` setting and the verdict's
    harness (``None`` for an adapter that cannot review)."""

    name: str
    reviewer_name: str | None
    family: str
    roles: tuple[str, ...]
    can_restrict_agents: bool
    can_restrict_tools: bool
    usage: str
    #: The build the harness's captures (``tests/harness_contract/``) were taken
    #: from, for information. Nothing compares it at launch.
    pinned_version: str
    fails_closed_on_unknown_version: bool
    #: The Controller command that admits a build of this harness where the
    #: Controller checks one, or ``None`` (H7). A declaration only: nothing reads
    #: it at launch, and it covers just the launches that check a build.
    build_verification: str | None
    #: The model ids and effort levels the harness accepts (H8). A route is
    #: checked against them by ``routing.check_route_values``; the operator
    #: adds a model with the ``harness.models`` setting, never an effort.
    models: tuple[str, ...]
    efforts: tuple[str, ...]

    def require_role(self, role: str) -> None:
        if role not in self.roles:
            raise HarnessRoleError(
                f"harness {self.name!r} cannot take the {role!r} role; it declares {list(self.roles)}",
                evidence={"harness": self.name, "role": role, "declared_roles": list(self.roles)},
            )


CLAUDE_CODE = HarnessAdapter(
    name="claude-code", reviewer_name="claude", family="anthropic",
    roles=(WORKER, REVIEWER, FUNCTIONAL_TESTER), can_restrict_agents=True, can_restrict_tools=True,
    usage=USAGE_REPORTED, pinned_version="2.1.296 (Claude Code)",
    fails_closed_on_unknown_version=True,
    # ``verify-claude`` admits the functional-tester launch only (it reads the
    # digests it records); the worker and reviewer launches run any build on PATH.
    build_verification="verify-claude",
    models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1", "opus", "sonnet", "haiku"),
    efforts=("low", "medium", "high", "xhigh", "max"),
)
CODEX = HarnessAdapter(
    name="codex", reviewer_name="codex", family="openai",
    roles=(REVIEWER,), can_restrict_agents=True, can_restrict_tools=True,
    usage=USAGE_DERIVED, pinned_version="codex-cli 0.163.0",
    fails_closed_on_unknown_version=True,
    # ``verify-codex`` exists (C10g-2b) but nothing at launch consults its record
    # yet: Codex keeps the fail-closed feature, layer, state and flag checks of
    # its review launch until C10g-3 declares the tester role, which then
    # changes this one value.
    build_verification=None,
    # tests/harness_contract/codex/: exec_argv.json (model gpt-6.1-sol, effort
    # high) and sandbox_probe.txt (the catalog's effort xhigh).
    models=("gpt-6.1-sol",),
    efforts=("high", "xhigh"),
)

#: The closed registry, keyed by ``name``.
ADAPTERS: Mapping[str, HarnessAdapter] = {a.name: a for a in (CLAUDE_CODE, CODEX)}


def get(name: str) -> HarnessAdapter:
    """The adapter registered under ``name`` or, for a reviewer, under its
    ``reviewer_name`` (``claude-code`` and ``claude`` are one adapter)."""
    adapter = ADAPTERS.get(name)
    if adapter is None:
        adapter = next((a for a in ADAPTERS.values() if a.reviewer_name == name), None)
    if adapter is None:
        raise UnknownHarnessError(
            f"no harness named {name!r}; registered: {sorted(ADAPTERS)}",
            evidence={"harness": name, "registered": sorted(ADAPTERS)},
        )
    return adapter


def for_role(name: str, role: str) -> HarnessAdapter:
    """``get(name)``, refusing a role the adapter lacks."""
    adapter = get(name)
    adapter.require_role(role)
    return adapter


def worker_adapter() -> HarnessAdapter:
    """The registry's one worker-role adapter."""
    workers = [a for a in ADAPTERS.values() if WORKER in a.roles]
    return workers[0]


#: ``reviewer_name`` -> family, over the reviewer-role adapters.
REVIEWER_FAMILY: Mapping[str, str] = {
    a.reviewer_name: a.family for a in ADAPTERS.values() if a.reviewer_name is not None
}
