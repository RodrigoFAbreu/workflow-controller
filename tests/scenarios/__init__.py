"""The scenario registry (`workflow-controller-automated-lifecycle-scenarios`
D2): a scenario is ``run(kit) -> None`` registered under a flow id that the
Workflow's ingestion accepts.

Selftest scenarios (``selftest=True``) exercise the kit and the runner and
are never part of the catalogue the runner writes evidence for.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import re
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _flow_id_pattern() -> "re.Pattern[str]":
    """The Workflow's own ``FLOW_ID_RE`` (``scripts/workflow_gate_policy.py``),
    read from the module itself so the two cannot drift."""
    scripts = str(REPO_ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        spec = importlib.util.spec_from_file_location("_scenario_gate_policy", Path(scripts) / "workflow_gate_policy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module.FLOW_ID_RE


FLOW_ID_RE = _flow_id_pattern()


class ScenarioFailure(AssertionError):
    """A scenario assertion that failed, with a message that says what."""


@dataclasses.dataclass(frozen=True)
class Scenario:
    flow_id: str
    run: Callable
    covers: tuple[str, ...]
    selftest: bool


@dataclasses.dataclass
class Result:
    flow_id: str
    status: str  # "passed" | "failed"
    observations: list
    message: str = ""


REGISTRY: dict[str, Scenario] = {}


def scenario(flow_id: str, *, covers: tuple[str, ...] = (), selftest: bool = False):
    """Register ``run(kit)`` under ``flow_id``; a duplicate id or one the
    Workflow's ``FLOW_ID_RE`` refuses is a registration error."""
    if not isinstance(flow_id, str) or not FLOW_ID_RE.match(flow_id):
        raise ValueError(f"flow id {flow_id!r} does not match the Workflow's FLOW_ID_RE")
    if flow_id in REGISTRY:
        raise ValueError(f"flow id {flow_id!r} is registered twice")

    def register(func: Callable) -> Callable:
        REGISTRY[flow_id] = Scenario(flow_id, func, tuple(covers), selftest)
        return func
    return register


def catalogue() -> list[Scenario]:
    """The registered non-selftest scenarios, in registration order."""
    return [s for s in REGISTRY.values() if not s.selftest]
