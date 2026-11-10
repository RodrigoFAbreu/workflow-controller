"""The worker launch behind the harness adapter (workflow-controller-worker-launch, H3).

``controller.harness`` declares which harness can take the worker role; this
module turns the routed harness into the object that launches it. One
interface (:class:`WorkerLauncher`), one implementation today
(:class:`ClaudeCodeLauncher`, which is ``worker.launch``), a registry keyed by
adapter name and role (:data:`LAUNCHERS`) and the lookup (:func:`for_harness`). Nothing
here changes what a launch does: the options pass through unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from controller import codex_worker, harness, worker
from controller.errors import HarnessRoleError


class WorkerLauncher(Protocol):
    """Launches one lifecycle worker and returns what ``worker.launch`` returns."""

    def launch(self, task: str, **options: Any) -> Any: ...


class ClaudeCodeLauncher:
    """Claude Code's launcher: ``worker.launch``, read as an attribute on every
    call so a test that patches ``worker.launch`` still reaches the patch."""

    def launch(self, task: str, **options: Any) -> Any:
        return worker.launch(task, **options)


class CodexReviewLauncher(codex_worker.CodexLauncher):
    """The Codex launcher of the local review roles (workflow-controller-h4e2b, D1). It exists so a review route is admitted
    like any Codex route; it never spawns, because the job's review branch runs ``codex_review_role.run_review`` instead."""

    def launch(self, task: str, **options: Any) -> Any:
        raise RuntimeError("a Codex review is run by codex_review_role.run_review, never launched as a worker")


#: ``(adapter name, adapter role)`` -> launcher. A test requires the keys to be
#: exactly the launchable roles each adapter declares (the worker role and the
#: local-reviewer role); ``codex`` is registered for the local-reviewer role
#: only, so a worker role still cannot be routed to it.
LAUNCHERS: Mapping[tuple[str, str], WorkerLauncher] = {
    (harness.CLAUDE_CODE.name, harness.WORKER): ClaudeCodeLauncher(),
    (harness.CLAUDE_CODE.name, harness.LOCAL_REVIEWER): ClaudeCodeLauncher(),
    (harness.CODEX.name, harness.LOCAL_REVIEWER): CodexReviewLauncher(),
}


def for_harness(name: str, role: str = harness.WORKER) -> WorkerLauncher:
    """The launcher of the harness ``name`` (an adapter name or its reviewer
    alias) for the adapter role ``role`` (default ``worker``). Raises
    ``HarnessRoleError`` for an adapter without the role, or one declaring it
    with no launcher registered."""
    adapter = harness.for_role(name, role)
    launcher = LAUNCHERS.get((adapter.name, role))
    if launcher is None:
        raise HarnessRoleError(
            f"harness {adapter.name!r} declares the {role!r} role but registers no launcher",
            evidence={"harness": adapter.name, "role": role, "launchers": sorted(LAUNCHERS)},
        )
    return launcher
