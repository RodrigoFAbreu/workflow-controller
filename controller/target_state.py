"""Read-only reader of a **target** repository's Workflow state
(capability 2, ``docs/ACTIVE_MILESTONE.md``).

This module is named ``target_state``, deliberately **not**
``workflow_state`` -- that name belongs to ``scripts/workflow_state.py``,
the Workflow's own state *writer* for *this* (the Controller's own)
repository. The two modules are unrelated: ``scripts/workflow_state.py``
writes and owns Workflow lifecycle transitions for this repository;
``controller/target_state.py`` only ever *reads*, and only ever reads a
**different**, managed repository's ``docs/ai-workflow/WORKFLOW_STATE.json``
and ``WORKFLOW_CONFIG.json``. Neither module imports the other; the
Controller never adds ``scripts/`` to ``sys.path``. Where this module needs
Workflow's own vocabulary (the closed set of known phases), it holds a
literal, test-verified copy rather than importing it.

``read()`` opens files in ``"r"`` mode only and this module defines no write
function -- see ``tests/test_target_state.py``'s AST-scan test for the
structural proof.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Mapping

from controller.decision import NO_PHASE, NO_PHASE_WIRE
from controller.errors import (
    AmbiguousWorkItemError,
    MalformedTargetRegistryError,
    MalformedWorkflowStateError,
    MissingWorkflowStateError,
    UnknownPhaseError,
)
from controller.managed_repo import ManagedRepository

_STATE_REL_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"
_CONFIG_REL_PATH = "docs/ai-workflow/WORKFLOW_CONFIG.json"

#: The closed set of all twenty phases the installed reference release's
#: own ``scripts/workflow_state.py:KNOWN_PHASES`` persists (re-derived at
#: revision 64 against the installed release by importing that module and
#: counting: twenty entries). A literal copy, not an import -- the
#: Controller must never import ``scripts/`` -- kept honest by a
#: two-directional set-equality test against the real module.
KNOWN_PHASES: frozenset[str] = frozenset({
    # v1 (docs/ai-workflow/MILESTONE_WORKFLOW.md)
    "PLANNING",
    "SELF_REVIEWING_PLAN",
    "AWAITING_EXTERNAL_PLAN_REVIEW",
    "REVISING_PLAN",
    "IMPLEMENTING",
    "SELF_REVIEWING_IMPLEMENTATION",
    "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
    "APPLYING_REVIEW_FEEDBACK",
    "AWAITING_FUNCTIONAL_REVIEW",
    "FIXING_FUNCTIONAL_FINDINGS",
    "AWAITING_USER_ACCEPTANCE",
    "MILESTONE_COMPLETE",
    # v2.1-only additions
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
    "AWAITING_TECHNICAL_APPROVAL",
    # D-Legacy phase 1 -- dormant, not terminal
    "LEGACY_READY",
    # workflow-2.4.0 addition (D-Plan-Amendment-1): real and persisted --
    # entered by request_plan_amendment alone, survives an interruption
    # between the amendment request and the first post-request
    # /milestone-plan call.
    "AMENDING_PLAN",
    # workflow-2.5.0 additions (D-Implementation-Review-Stages): "2.2"-only,
    # real and persisted, mirroring AWAITING_LOCAL_PLAN_REVIEW/
    # AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW's own local-then-manual-external
    # shape at the implementation stage.
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
})

#: Only ``MILESTONE_COMPLETE`` is terminal -- ``LEGACY_READY`` is
#: explicitly dormant, not terminal, matching the installed reference
#: release's own ``TERMINAL_PHASES``.
TERMINAL_PHASES: frozenset[str] = frozenset({"MILESTONE_COMPLETE"})


#: ``NO_PHASE``/``NO_PHASE_WIRE`` are declared in ``controller.decision``,
#: not here (revision 71 relocation): ``decision.decide_no_work_item``
#: needs the same sentinel object CP6/CP7 read back off disk, and
#: ``decision`` sits *earlier* than ``target_state`` in the dependency
#: order (``tests/test_package_structure.py``'s ``DEPENDENCY_ORDER``) --
#: ``target_state`` may import ``decision``, never the reverse. Re-exported
#: here (``target_state.NO_PHASE``) so every module that reads a target's
#: Workflow state can reach the one canonical sentinel through this
#: module's own vocabulary, without a second, divergent instance.


class _NoWorkItemYetType:
    """The sentinel type of :data:`NoWorkItemYet`.

    Revision 63 (B2, ``REQ-40``): :func:`select_work_item` returns this --
    never a :class:`WorkItemView` -- when zero non-terminal work items
    exist in the snapshot and no explicit ``work_item_id`` was given (and
    no ``active_work_item_id`` resolves one either). There is no work item
    to view, so there is no ``phase`` to key a decision on; a caller
    holding this sentinel is expected to take the distinct
    ``NoWorkItemYet``/bootstrap path (frozen ``/milestone-plan`` with no
    argument) rather than treat it as an ordinary work item observation.
    Disjoint from an explicit ``--work-item <id>`` naming an id absent from
    ``work_items``, which stays :class:`~controller.errors.AmbiguousWorkItemError`
    exactly as before -- the Controller never treats an operator's
    explicit, wrong name as an invitation to bootstrap one.
    """

    def __repr__(self) -> str:  # pragma: no cover -- diagnostic convenience
        return "NoWorkItemYet"


#: The single sentinel :func:`select_work_item` returns for the
#: zero-non-terminal-candidates, no-explicit-id case (see
#: :class:`_NoWorkItemYetType`).
NoWorkItemYet = _NoWorkItemYetType()


@dataclasses.dataclass(frozen=True)
class WorkItemView:
    """One work item's Workflow state, as read from the target
    repository. Every field is populated from ``WORKFLOW_STATE.json``
    except ``incomplete_children`` (a reverse lookup over every work item
    in the same snapshot) and ``registry_complete`` (this work item's own
    read-only derivation of the registry-completion half of the installed
    reference release's ``is_terminal`` predicate -- see its own docstring
    for the three-outcome contract)."""

    work_item_id: str
    work_item_type: str | None
    work_item_kind: str | None
    governing_workflow_version: str | None
    phase: str
    plan_revision: int | None
    implementation_revision: int | None
    functional_review_round: int | None
    base_commit: str | None
    reviewed_implementation_head: str | None
    current_checkpoint_id: str | None
    last_completed_checkpoint_id: str | None
    checkpoints: Mapping[str, dict]
    current_bundle_id: str | None
    plan_approval: dict | None
    technical_approval: dict | None
    functional_acceptance_status: str | None
    plan_review_stages: dict | None
    parent_work_item_id: str | None
    incomplete_children: tuple[str, ...]
    registry_complete: bool | None
    state_revision: int | None


@dataclasses.dataclass(frozen=True)
class WorkflowSnapshot:
    """The whole of a target repository's ``WORKFLOW_STATE.json``, plus
    ``WORKFLOW_CONFIG.json``'s ``default_workflow_version`` (``None`` if
    the config file is absent or unparseable -- there is no named refusal
    for it in CP3's taxonomy; it is read-only diagnostic context, never a
    load-bearing gate for anything CP3 itself decides)."""

    schema_version: int
    active_work_item_id: str | None
    work_items: Mapping[str, WorkItemView]
    default_workflow_version: str | None
    raw_path: Path


def _read_default_workflow_version(root: Path) -> str | None:
    config_path = root / _CONFIG_REL_PATH
    try:
        raw = config_path.read_text()
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("default_workflow_version")
    return version if isinstance(version, str) else None


def _resolve_registry_complete(root: Path, work_item_id: str, entry: dict) -> bool | None:
    """The three-outcome contract CP3's plan section states: ``None`` --
    no ``registry_path`` declared, vacuously terminal; ``True``/``False``
    -- a resolvable, readable, self-consistent registry, derived purely
    from the work item's own ``checkpoints`` map and the registry's own
    declared checkpoint ids (mirroring the installed reference release's
    own ``select_next_checkpoint``/``registry_completion_status``, without
    importing them); ``MalformedTargetRegistryError`` -- unresolvable,
    unreadable, unparseable, or cross-linked to a different work item.
    Deliberately does not reproduce ``StalePlanApprovalRegistryReadError``
    (the registry-covered-by-current-plan-approval check) -- out of
    scope, since answering it would mean recomputing Workflow's own
    ``review_content_id`` identity semantics."""
    registry_path_value = entry.get("registry_path")
    if registry_path_value is None:
        return None
    if not isinstance(registry_path_value, str) or not registry_path_value:
        raise MalformedTargetRegistryError(
            f"{work_item_id!r} declares a non-string or empty registry_path "
            f"{registry_path_value!r}",
            evidence={"work_item_id": work_item_id, "registry_path": registry_path_value},
        )

    root_resolved = root.resolve()
    candidate = (root / registry_path_value)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise MalformedTargetRegistryError(
            f"{work_item_id!r}'s registry_path {registry_path_value!r} does not resolve to a "
            f"path inside the repository at {root_resolved}",
            evidence={"work_item_id": work_item_id, "registry_path": registry_path_value},
        ) from exc

    if not resolved.is_file():
        raise MalformedTargetRegistryError(
            f"{work_item_id!r}'s declared registry_path {registry_path_value!r} does not "
            f"exist or is not a regular file",
            evidence={"work_item_id": work_item_id, "registry_path": registry_path_value},
        )

    try:
        registry_data = json.loads(resolved.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedTargetRegistryError(
            f"{work_item_id!r}'s registry at {registry_path_value!r} could not be read or "
            f"parsed as JSON: {exc}",
            evidence={"work_item_id": work_item_id, "registry_path": registry_path_value,
                      "error": str(exc)},
        ) from exc

    if not isinstance(registry_data, dict):
        raise MalformedTargetRegistryError(
            f"{work_item_id!r}'s registry at {registry_path_value!r} does not contain a JSON "
            f"object",
            evidence={"work_item_id": work_item_id, "registry_path": registry_path_value},
        )

    declared_id = registry_data.get("work_item_id")
    if declared_id != work_item_id:
        raise MalformedTargetRegistryError(
            f"{work_item_id!r}'s registry at {registry_path_value!r} declares work_item_id "
            f"{declared_id!r}, which disagrees with {work_item_id!r}",
            evidence={"work_item_id": work_item_id, "registry_path": registry_path_value,
                      "declared_work_item_id": declared_id},
        )

    checkpoints_spec = registry_data.get("checkpoints")
    if not isinstance(checkpoints_spec, list) or not checkpoints_spec:
        raise MalformedTargetRegistryError(
            f"{work_item_id!r}'s registry at {registry_path_value!r} does not declare a "
            f"non-empty checkpoints array",
            evidence={"work_item_id": work_item_id, "registry_path": registry_path_value},
        )

    checkpoint_ids: list[str] = []
    for checkpoint_entry in checkpoints_spec:
        checkpoint_id = checkpoint_entry.get("id") if isinstance(checkpoint_entry, dict) else None
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise MalformedTargetRegistryError(
                f"{work_item_id!r}'s registry at {registry_path_value!r} has a checkpoint "
                f"entry with no string 'id'",
                evidence={"work_item_id": work_item_id, "registry_path": registry_path_value},
            )
        checkpoint_ids.append(checkpoint_id)

    own_checkpoints = entry.get("checkpoints")
    if not isinstance(own_checkpoints, dict):
        raise MalformedTargetRegistryError(
            f"{work_item_id!r}'s own checkpoints field is not a JSON object, so registry "
            f"completion cannot be derived",
            evidence={"work_item_id": work_item_id},
        )

    return all(
        own_checkpoints.get(checkpoint_id, {}).get("status") == "COMPLETE"
        for checkpoint_id in checkpoint_ids
    )


def _build_work_item_view(
    root: Path, work_item_id: str, entry: dict, raw_work_items: dict,
) -> WorkItemView:
    incomplete_children = tuple(
        other_id for other_id, other_entry in raw_work_items.items()
        if other_entry.get("parent_work_item_id") == work_item_id
        and other_entry.get("phase") not in TERMINAL_PHASES
    )
    registry_complete = _resolve_registry_complete(root, work_item_id, entry)
    return WorkItemView(
        work_item_id=work_item_id,
        work_item_type=entry.get("work_item_type"),
        work_item_kind=entry.get("work_item_kind"),
        governing_workflow_version=entry.get("governing_workflow_version"),
        phase=entry.get("phase"),
        plan_revision=entry.get("plan_revision"),
        implementation_revision=entry.get("implementation_revision"),
        functional_review_round=entry.get("functional_review_round"),
        base_commit=entry.get("base_commit"),
        reviewed_implementation_head=entry.get("reviewed_implementation_head"),
        current_checkpoint_id=entry.get("current_checkpoint_id"),
        last_completed_checkpoint_id=entry.get("last_completed_checkpoint_id"),
        checkpoints=entry.get("checkpoints") or {},
        current_bundle_id=entry.get("current_bundle_id"),
        plan_approval=entry.get("plan_approval"),
        technical_approval=entry.get("technical_approval"),
        functional_acceptance_status=entry.get("functional_acceptance_status"),
        plan_review_stages=entry.get("plan_review_stages"),
        parent_work_item_id=entry.get("parent_work_item_id"),
        incomplete_children=incomplete_children,
        registry_complete=registry_complete,
        state_revision=entry.get("state_revision"),
    )


def read(managed_repo: ManagedRepository) -> WorkflowSnapshot:
    """Read ``<managed_repo.root>/docs/ai-workflow/WORKFLOW_STATE.json``
    (and, best-effort, ``WORKFLOW_CONFIG.json``) and return a frozen
    :class:`WorkflowSnapshot`. Fail-closed, in order:

    1. the state file is missing -> :class:`~controller.errors.MissingWorkflowStateError`;
    2. its bytes are not valid JSON, or do not parse to a JSON object,
       or ``schema_version`` is absent or not ``1`` ->
       :class:`~controller.errors.MalformedWorkflowStateError`;
    3. any ``work_items`` entry whose own ``work_item_id`` field
       disagrees with its key, or whose ``active_work_item_id`` names a
       work item absent from ``work_items`` ->
       :class:`~controller.errors.MalformedWorkflowStateError`;
    4. any work item's ``phase`` outside :data:`KNOWN_PHASES` ->
       :class:`~controller.errors.UnknownPhaseError`;
    5. any work item's own declared, unresolvable/unreadable/unparseable/
       cross-linked registry ->
       :class:`~controller.errors.MalformedTargetRegistryError`.

    Deliberately does **not** resolve ambiguity between multiple
    candidate work items -- that needs a caller-supplied ``--work-item``
    override this function has no way to receive, so it is
    :func:`select_work_item`'s job, not this one's."""
    root = managed_repo.root
    state_path = root / _STATE_REL_PATH
    if not state_path.is_file():
        raise MissingWorkflowStateError(
            f"{root} has no {_STATE_REL_PATH}",
            evidence={"root": str(root), "state_path": str(state_path)},
        )

    try:
        raw_text = state_path.read_text()
    except OSError as exc:
        raise MalformedWorkflowStateError(
            f"{state_path} could not be read: {exc}",
            evidence={"state_path": str(state_path), "error": str(exc)},
        ) from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MalformedWorkflowStateError(
            f"{state_path} is not valid JSON: {exc}",
            evidence={"state_path": str(state_path), "error": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise MalformedWorkflowStateError(
            f"{state_path} does not contain a JSON object",
            evidence={"state_path": str(state_path)},
        )

    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise MalformedWorkflowStateError(
            f"{state_path} declares schema_version={schema_version!r}, expected 1",
            evidence={"state_path": str(state_path), "schema_version": schema_version},
        )

    raw_work_items = data.get("work_items", {})
    if not isinstance(raw_work_items, dict):
        raise MalformedWorkflowStateError(
            f"{state_path}'s work_items is not a JSON object",
            evidence={"state_path": str(state_path)},
        )

    for work_item_id, entry in raw_work_items.items():
        if not isinstance(entry, dict):
            raise MalformedWorkflowStateError(
                f"{state_path}'s work_items[{work_item_id!r}] is not a JSON object",
                evidence={"state_path": str(state_path), "work_item_id": work_item_id},
            )
        declared_id = entry.get("work_item_id")
        if declared_id != work_item_id:
            raise MalformedWorkflowStateError(
                f"{state_path}'s work_items key {work_item_id!r} disagrees with its own "
                f"work_item_id field {declared_id!r}",
                evidence={"state_path": str(state_path), "key": work_item_id,
                          "declared_work_item_id": declared_id},
            )
        phase = entry.get("phase")
        if phase not in KNOWN_PHASES:
            raise UnknownPhaseError(
                f"{work_item_id!r} has phase {phase!r}, which is not in the Controller's "
                f"known set of {len(KNOWN_PHASES)} Workflow phases",
                evidence={"work_item_id": work_item_id, "phase": phase,
                          "known_phases": sorted(KNOWN_PHASES)},
            )

    active_work_item_id = data.get("active_work_item_id")
    if active_work_item_id is not None:
        if not isinstance(active_work_item_id, str):
            raise MalformedWorkflowStateError(
                f"{state_path}'s active_work_item_id {active_work_item_id!r} is not a string "
                f"or null",
                evidence={"state_path": str(state_path), "active_work_item_id": active_work_item_id},
            )
        if active_work_item_id not in raw_work_items:
            raise MalformedWorkflowStateError(
                f"{state_path}'s active_work_item_id {active_work_item_id!r} names a work "
                f"item absent from work_items",
                evidence={"state_path": str(state_path), "active_work_item_id": active_work_item_id,
                          "known_work_item_ids": sorted(raw_work_items)},
            )

    work_items = {
        work_item_id: _build_work_item_view(root, work_item_id, entry, raw_work_items)
        for work_item_id, entry in raw_work_items.items()
    }

    return WorkflowSnapshot(
        schema_version=schema_version,
        active_work_item_id=active_work_item_id,
        work_items=work_items,
        default_workflow_version=_read_default_workflow_version(root),
        raw_path=state_path,
    )


def select_work_item(
    snapshot: WorkflowSnapshot, *, work_item_id: str | None = None,
) -> WorkItemView | _NoWorkItemYetType:
    """Resolve the single target work item: an explicit ``work_item_id``
    wins -- and, naming an id absent from ``work_items``, is
    :class:`~controller.errors.AmbiguousWorkItemError`, never a bootstrap
    trigger; else ``snapshot.active_work_item_id`` (already validated by
    :func:`read` to name a real entry, if not ``None``); else, if exactly
    one non-terminal work item exists in the snapshot, that one; else, if
    **zero** non-terminal items exist, :data:`NoWorkItemYet` (revision 63,
    B2 -- there is no candidate to be ambiguous *among*); else (more than
    one) :class:`~controller.errors.AmbiguousWorkItemError`, naming every
    candidate considered.

    ``active_work_item_id`` is a resume-focus pointer, never an execution
    lock (D1) -- an explicit override always takes precedence over it,
    never the reverse."""
    if work_item_id is not None:
        view = snapshot.work_items.get(work_item_id)
        if view is None:
            raise AmbiguousWorkItemError(
                f"requested work item {work_item_id!r} is not present in the target "
                f"repository's Workflow state",
                evidence={"requested": work_item_id, "candidates": sorted(snapshot.work_items)},
            )
        return view

    if snapshot.active_work_item_id is not None:
        return snapshot.work_items[snapshot.active_work_item_id]

    non_terminal = [
        wid for wid, view in snapshot.work_items.items() if view.phase not in TERMINAL_PHASES
    ]
    if len(non_terminal) == 1:
        return snapshot.work_items[non_terminal[0]]

    if len(non_terminal) == 0:
        return NoWorkItemYet

    raise AmbiguousWorkItemError(
        "no active_work_item_id is set and the target repository's Workflow state does not "
        f"resolve to exactly one non-terminal work item ({len(non_terminal)} found)",
        evidence={"requested": None, "candidates": sorted(non_terminal)},
    )
