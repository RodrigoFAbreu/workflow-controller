"""Choosing one entry of a role's fallback chain before a worker is spawned
(H9b, ``workflow-controller-fallback-select``).

The selection is a pure function over injected probes: it reads no file,
clock or registry of its own, so every case is a table test. Entries are
tried in order. An entry whose launcher cannot be resolved is skipped; an
entry whose provider the usage gate holds is skipped, and so is every later
entry of that provider, without a second admission (the readings are
account-wide, and a different model's forecast is not a model-specific
limit). The first admitted entry is chosen. When none is, the result is
today's single-entry outcome: a hold, or the first entry's launcher error.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any

from controller import harness
from controller.routing import ResolvedRoute

CAUSE_LAUNCHER_UNAVAILABLE = "launcher_unavailable"
CAUSE_USAGE_HELD = "usage_held"
CAUSES = (CAUSE_LAUNCHER_UNAVAILABLE, CAUSE_USAGE_HELD)

CHOSEN = "chosen"
HELD = "held"
UNAVAILABLE = "unavailable"

#: Mirrors ``usage.PROVIDER_CLAUDE`` / ``usage.PROVIDER_CODEX`` (a test pins
#: them); this module imports neither the budget nor the job.
_PROVIDER_CLAUDE = "claude"
_PROVIDER_CODEX = "codex"
_ANTHROPIC_FAMILY = "anthropic"


def usage_provider(harness_name: str) -> str:
    """The usage provider that runs ``harness_name``, derived from the
    adapter's family as the review job derives it: ``anthropic`` is Claude,
    any other family is Codex. An unknown name raises the registry's own
    ``UnknownHarnessError``."""
    family = harness.get(harness_name).family
    return _PROVIDER_CLAUDE if family == _ANTHROPIC_FAMILY else _PROVIDER_CODEX


@dataclasses.dataclass(frozen=True)
class Selection:
    """The outcome: ``chosen`` (``position``, ``route``, ``token``),
    ``held`` (``hold``) or ``unavailable`` (``error``). ``skipped`` is a tuple
    of ``(position, harness, cause)`` in chain order, for every outcome."""

    outcome: str
    skipped: tuple[tuple[int, str, str], ...] = ()
    position: int | None = None
    route: ResolvedRoute | None = None
    token: str | None = None
    hold: Any = None
    error: BaseException | None = None


def _waitable(hold: Any) -> bool:
    return bool(hold.waitable) and hold.resume_at is not None


def _reported_hold(holds: Sequence[Any]) -> Any:
    """The earliest waitable hold (the earlier chain position on a tie), else
    the first hold (a cap is not waitable)."""
    waitable = [h for h in holds if _waitable(h)]
    if waitable:
        return min(waitable, key=lambda h: h.resume_at)  # min keeps the first of equals
    return holds[0]


def select(chain: Sequence[ResolvedRoute],
           unavailable: Callable[[ResolvedRoute], BaseException | None],
           admit: Callable[[ResolvedRoute, str], Any]) -> Selection:
    """Walk ``chain`` in order. ``unavailable(entry)`` returns the launcher
    error or ``None``; ``admit(entry, provider)`` returns a token (a string,
    or ``None`` when the budget is disabled) or a hold (anything with
    ``resume_at`` and ``waitable``)."""
    skipped: list[tuple[int, str, str]] = []
    holds: list[Any] = []
    held_providers: set[str] = set()
    first_error: BaseException | None = None
    for position, entry in enumerate(chain):
        error = unavailable(entry)
        if error is not None:
            first_error = first_error or error
            skipped.append((position, entry.harness, CAUSE_LAUNCHER_UNAVAILABLE))
            continue
        provider = usage_provider(entry.harness)
        if provider in held_providers:
            skipped.append((position, entry.harness, CAUSE_USAGE_HELD))
            continue
        admitted = admit(entry, provider)
        if admitted is None or isinstance(admitted, str):
            return Selection(CHOSEN, tuple(skipped), position, entry, admitted)
        held_providers.add(provider)
        holds.append(admitted)
        skipped.append((position, entry.harness, CAUSE_USAGE_HELD))
    if holds:
        return Selection(HELD, tuple(skipped), hold=_reported_hold(holds))
    return Selection(UNAVAILABLE, tuple(skipped), error=first_error)
