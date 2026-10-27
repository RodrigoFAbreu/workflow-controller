"""The worker launch behind the harness adapter (workflow-controller-worker-launch, H3).

``controller.harness`` declares which harness can take the worker role; this
module turns the routed harness into the object that launches it. One
interface (:class:`WorkerLauncher`), one implementation today
(:class:`ClaudeCodeLauncher`, which is ``worker.launch``), a registry keyed by
adapter name (:data:`LAUNCHERS`) and the lookup (:func:`for_harness`). Nothing
here changes what a launch does: the options pass through unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from controller import harness, worker
from controller.errors import HarnessRoleError


class WorkerLauncher(Protocol):
    """Launches one lifecycle worker and returns what ``worker.launch`` returns."""

    def launch(self, task: str, **options: Any) -> Any: ...


class ClaudeCodeLauncher:
    """Claude Code's launcher: ``worker.launch``, read as an attribute on every
    call so a test that patches ``worker.launch`` still reaches the patch."""

    def launch(self, task: str, **options: Any) -> Any:
        return worker.launch(task, **options)


#: Adapter name -> launcher. A test requires the keys to be exactly the
#: adapters that declare ``harness.WORKER``.
LAUNCHERS: Mapping[str, WorkerLauncher] = {harness.CLAUDE_CODE.name: ClaudeCodeLauncher()}


def for_harness(name: str) -> WorkerLauncher:
    """The launcher of the worker-role harness ``name`` (an adapter name or its
    reviewer alias). Raises ``HarnessRoleError`` for an adapter without the
    worker role, or one declaring it with no launcher registered."""
    adapter = harness.for_role(name, harness.WORKER)
    launcher = LAUNCHERS.get(adapter.name)
    if launcher is None:
        raise HarnessRoleError(
            f"harness {adapter.name!r} declares the {harness.WORKER!r} role but registers no launcher",
            evidence={"harness": adapter.name, "role": harness.WORKER, "launchers": sorted(LAUNCHERS)},
        )
    return launcher
