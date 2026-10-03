#!/usr/bin/env python3
# state_writer: true
"""Orchestration Protocol v1: the Workflow's versioned public contract for
an orchestrator (`docs/ai-workflow/ORCHESTRATION_PROTOCOL_V1_PLAN.md`,
D-OP-Surface).

    python3 scripts/workflow_protocol.py [--repo-root PATH] [--protocol-major N] <operation> [operation args]

An orchestrator runs this script as a subprocess and never imports it. It
imports `workflow_state` and `workflow_fingerprint` from its own directory
and never shells out to them. stdout is exactly one JSON document, the
envelope; diagnostics go to stderr. Exit codes: 0 `ok: true`; 3 a refusal
with a stable code; 2 `invalid_request`; 1 `internal_error`.

Operations: `describe`, `verify`, `resolve-artifact`, `next-action`,
`reconcile` and `record-external-result`. Every read path here is
read-only: the state is read under a shared lock on `WORKFLOW_STATE.lock`,
never the write lock, and nothing is written. `record-external-result` is
the one writer: it calls `workflow_state.ingest_manual_review_verdict`,
which holds `state_lock` and publishes through `state_transaction`, as the
record-manual commands do.

Stdlib-only.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import workflow_fingerprint  # noqa: E402
import workflow_state  # noqa: E402

#: The Workflow release these bytes are. A build refuses when it differs
#: from the manifest's `workflow_version` (CP7); it never reads the
#: installation record.
WORKFLOW_RELEASE = "2.7.0"

PROTOCOL_NAME = "workflow-orchestration"
PROTOCOL_MAJOR = 1
PROTOCOL_VERSION = "1.0"
SUPPORTED_PROTOCOL_MAJORS = (PROTOCOL_MAJOR,)

#: The single source of the governing versions the protocol supports
#: (D-OP-Describe): never a repository's `WORKFLOW_CONFIG.json`.
SUPPORTED_GOVERNING_VERSIONS = ("1", "2.1", "2.2")

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_INVALID_REQUEST = 2
EXIT_REFUSED = 3

#: D-OP-Errors: every v1 error code and whether it is retryable.
ERROR_CODES = {
    "unsupported_protocol": False,
    "invalid_request": False,
    "unknown_work_item": False,
    "no_work_item": False,
    "state_unreadable": False,
    "state_invalid": False,
    "stale_decision": True,
    "not_applicable": False,
    "unsupported_result_kind": False,
    "refused": False,
    "internal_error": False,
}

#: The explicit Workflow-exception-to-code table, keyed by class name. A
#: Workflow exception with no entry here is `refused`, never
#: `internal_error` (D-OP-Errors).
WORKFLOW_EXCEPTION_CODES = {
    "InvalidWorkItemIdError": "invalid_request",
    "FeedbackLayoutUndecidableError": "state_unreadable",
    "UnknownFeedbackLayoutError": "state_invalid",
}

#: D-OP-Next's dispositions. `validation` is W2 vocabulary that 2.7.0
#: never emits (`OD-W1-7`).
DISPOSITIONS = ("automatic", "validation", "human_gate", "external_gate", "blocked", "complete")

#: D-OP-External's result kinds, each with the review stage it ingests,
#: and the kinds reserved for W2.
EXTERNAL_RESULT_KIND_STAGES = {
    "plan_review_verdict": "plan",
    "implementation_review_verdict": "implementation",
}
EXTERNAL_RESULT_KINDS = tuple(sorted(EXTERNAL_RESULT_KIND_STAGES))
RESERVED_RESULT_KINDS = ("functional_evidence", "pr_review_result")

#: D-OP-Next's worker roles.
WORKER_ROLES = ("planner", "implementer", "self_reviewer", "independent_reviewer", "applier", "user", "external")


def _action_spec(command: str | None, invocation: str | None, role: str, *, fresh_session: bool = False,
                 independent_of: tuple[str, ...] = (), user_only: bool = False) -> dict:
    return {"command": command, "invocation": invocation, "role": role, "fresh_session": fresh_session,
            "independent_of": list(independent_of), "user_only": user_only}


#: The action catalogue's actions (D-OP-Next): the command each runs (its
#: `.claude/commands/<command>.md`, `None` for a gate with no command), the
#: rendered invocation (`{id}` is the work item), and the worker. A
#: `user_only` action always has role `user`, and its command file carries
#: `disable-model-invocation: true`.
ACTIONS = {
    "plan.start": _action_spec("milestone-plan", "/milestone-plan", "planner"),
    "plan.author": _action_spec("milestone-plan", "/milestone-plan {id}", "planner"),
    "plan.withdraw": _action_spec("milestone-plan", "/milestone-plan {id}", "planner"),
    "plan.apply_review": _action_spec("apply-plan-review", "/apply-plan-review {id}", "applier"),
    "plan.review.local": _action_spec(
        "review-plan", "/review-plan {id}", "independent_reviewer",
        fresh_session=True, independent_of=("planner",)),
    "plan.record_external": _action_spec(
        "record-manual-plan-review", "/record-manual-plan-review {id}", "applier"),
    "plan.review.external": _action_spec(None, None, "external"),
    "plan.approve": _action_spec("approve-review", "/approve-review plan {id}", "user", user_only=True),
    "review.resolve_block": _action_spec(None, None, "user"),
    "implementation.checkpoint": _action_spec("milestone-implement", "/milestone-implement {id}", "implementer"),
    "implementation.self_review": _action_spec(
        "milestone-implement", "/milestone-implement {id}", "self_reviewer"),
    "implementation.review.local": _action_spec(
        "review-implementation", "/review-implementation {id}", "independent_reviewer",
        fresh_session=True, independent_of=("implementer", "self_reviewer")),
    "implementation.record_external": _action_spec(
        "record-manual-implementation-review", "/record-manual-implementation-review {id}", "applier"),
    "implementation.review.external": _action_spec(None, None, "external"),
    "implementation.approve": _action_spec(
        "approve-review", "/approve-review implementation {id}", "user", user_only=True),
    "implementation.recover_provenance": _action_spec(
        "recover-implementation-provenance", "/recover-implementation-provenance {id}", "implementer"),
    "implementation.apply_review": _action_spec(
        "apply-implementation-review", "/apply-implementation-review {id}", "applier"),
    "functional.prepare": _action_spec("prepare-functional-review", "/prepare-functional-review {id}", "implementer"),
    "functional.apply_findings": _action_spec("apply-functional-review", "/apply-functional-review {id}", "applier"),
    "functional.review": _action_spec(None, None, "user"),
    "functional.review.advisory": _action_spec(
        "review-functional", "/review-functional {id}", "independent_reviewer",
        fresh_session=True, independent_of=("implementer",)),
    "milestone.accept": _action_spec("accept-milestone", "/accept-milestone {id}", "user", user_only=True),
}

#: The action catalogue's ids (D-OP-Next).
ACTION_IDS = tuple(sorted(ACTIONS))

REVIEW_FEEDBACK_FILE = "REVIEW_FEEDBACK.md"
FUNCTIONAL_REVIEW_FILE = "FUNCTIONAL_REVIEW.md"

#: The phases whose review bundle is the plan stage's (`resolve-artifact`'s
#: `review_bundle`): the two-stage plan-review partition plus the `"1"`
#: plan phases.
PLAN_STAGE_PHASES = (
    workflow_state.PLAN_REVIEW_READY_PHASES
    | workflow_state.PLAN_REVIEW_NON_READY_PHASES
    | {"SELF_REVIEWING_PLAN", "AWAITING_EXTERNAL_PLAN_REVIEW"}
)


class ProtocolError(Exception):
    """A refusal with a stable code. `native` is the Workflow exception
    behind it, or `None`."""

    def __init__(self, code: str, message: str, native: BaseException | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.native = native


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """argparse with its errors raised (as `invalid_request`) instead of
    printed, and no `--help`: stdout carries the envelope only."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise _ArgumentError(message)


# ---------------------------------------------------------------------------
# The Workflow exception domain (D-OP-Errors)
# ---------------------------------------------------------------------------


def is_workflow_exception(exc: BaseException) -> bool:
    """A Workflow exception is an instance of a class `C` with
    `issubclass(C, Exception)` whose `__module__` is the `__name__` of the
    `workflow_state` or `workflow_fingerprint` module object this module
    imported -- read from the module objects, never from literals."""
    if not isinstance(exc, Exception):
        return False
    return type(exc).__module__ in {workflow_state.__name__, workflow_fingerprint.__name__}


def code_for_workflow_exception(exc: BaseException) -> str:
    return WORKFLOW_EXCEPTION_CODES.get(type(exc).__name__, "refused")


def native_of(exc: BaseException | None) -> dict | None:
    if exc is None:
        return None
    return {"exception": type(exc).__name__, "message": str(exc)}


# ---------------------------------------------------------------------------
# Reading the state (the one read that maps `OSError` to `state_unreadable`)
# ---------------------------------------------------------------------------


def read_state_and_config(repo_root: Path) -> tuple[dict, dict | None]:
    """Read `WORKFLOW_STATE.json` and the raw `WORKFLOW_CONFIG.json` (or
    `None` when absent) under a shared lock on `WORKFLOW_STATE.lock`, so a
    read never interleaves with a `state_transaction` publication. The lock
    file is opened read-only and never created: when it is absent, no
    writer has ever run and the read proceeds without it.

    Every refusal is `state_unreadable`: a missing or corrupt state file, a
    state that is not a JSON object, a corrupt config, or a lock file that
    cannot be opened. This is the single protocol read where an `OSError`
    is `state_unreadable` rather than `internal_error`."""
    lock_path = workflow_state.state_lock_path(repo_root)
    state_path = repo_root / workflow_state.DEFAULT_STATE_PATH
    config_path = repo_root / workflow_state.DEFAULT_CONFIG_PATH
    fd = None
    try:
        try:
            fd = os.open(lock_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            fd = None
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_SH)
        state = workflow_state._load_json(state_path)
        config = workflow_state._load_json(config_path)
    except OSError as exc:
        raise ProtocolError("state_unreadable", f"cannot read the Workflow state: {exc}") from exc
    except workflow_state.CorruptJsonError as exc:
        raise ProtocolError("state_unreadable", str(exc), exc) from exc
    finally:
        if fd is not None:
            os.close(fd)
    if state is None:
        raise ProtocolError(
            "state_unreadable", f"{workflow_state.DEFAULT_STATE_PATH.as_posix()} does not exist")
    if not isinstance(state, dict):
        raise ProtocolError(
            "state_unreadable", f"{workflow_state.DEFAULT_STATE_PATH.as_posix()} is not a JSON object")
    if config is not None and not isinstance(config, dict):
        raise ProtocolError(
            "state_unreadable", f"{workflow_state.DEFAULT_CONFIG_PATH.as_posix()} is not a JSON object")
    return state, config


def load_valid_state(repo_root: Path) -> dict:
    """The state, read and schema-validated: `validate_state(state)` without
    the whole-state registry-mirror check, which `verify` runs and which a
    plan being edited may legitimately fail mid-round. A refusal is
    `state_invalid`."""
    state, _config = read_state_and_config(repo_root)
    try:
        workflow_state.validate_state(state)
    except Exception as exc:
        if not is_workflow_exception(exc):
            raise
        raise ProtocolError("state_invalid", str(exc), exc) from exc
    return state


def work_item_of(state: dict, work_item_id: str) -> dict:
    work_items = state.get("work_items") or {}
    if work_item_id not in work_items:
        raise ProtocolError("unknown_work_item", f"no work item {work_item_id!r} in work_items")
    return work_items[work_item_id]


# ---------------------------------------------------------------------------
# State identity and the decision basis (D-OP-Identity)
# ---------------------------------------------------------------------------


def state_identity(work_item_id: str, state: dict) -> str:
    """sha256 of the canonical JSON of one work item, its id and the state's
    `schema_version`. Unrelated items and `active_work_item_id` are not part
    of it (`OD-W1-6`)."""
    return hashlib.sha256(workflow_state._canonical_json_bytes({
        "schema_version": state.get("schema_version"),
        "work_item_id": work_item_id,
        "work_item": state["work_items"][work_item_id],
    })).hexdigest()


def head_commit(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def basis(repo_root: Path, state: dict, work_item_id: str) -> dict:
    """The decision basis of a response about a work item. Staleness is
    decided by `state_identity` alone; `head` and `checkpoints` are
    informational."""
    work_item = state["work_items"][work_item_id]
    return {
        "work_item_id": work_item_id,
        "state_revision": work_item.get("state_revision"),
        "state_identity": state_identity(work_item_id, state),
        "phase": work_item.get("phase"),
        "head": head_commit(repo_root),
        "checkpoints": {
            checkpoint_id: entry.get("status")
            for checkpoint_id, entry in sorted((work_item.get("checkpoints") or {}).items())
        },
    }


# ---------------------------------------------------------------------------
# describe (D-OP-Describe)
# ---------------------------------------------------------------------------


def op_describe(repo_root: Path, args: argparse.Namespace) -> dict:
    return {
        "workflow_release": WORKFLOW_RELEASE,
        "protocol_version": PROTOCOL_VERSION,
        "supported_protocol_majors": sorted(SUPPORTED_PROTOCOL_MAJORS),
        "supported_governing_versions": sorted(SUPPORTED_GOVERNING_VERSIONS),
        "capabilities": {
            "operations": sorted(OPERATIONS),
            "dispositions": sorted(DISPOSITIONS),
            "action_ids": sorted(ACTION_IDS),
            "artifact_kinds": sorted(ARTIFACT_KINDS),
            "external_result_kinds": sorted(EXTERNAL_RESULT_KINDS),
            "reserved_result_kinds": sorted(RESERVED_RESULT_KINDS),
            "error_codes": sorted(ERROR_CODES),
        },
    }


# ---------------------------------------------------------------------------
# verify (D-OP-Verify)
# ---------------------------------------------------------------------------


VERIFY_CHECK_IDS = (
    "state_readable",
    "state_valid",
    "config_valid",
    "active_item_resolvable",
    "checkpoint_completions_provable",
    "installation_release_matches",
    "protocol_ready",
)


def _check(check_id: str, status: str, detail: str) -> dict:
    return {"id": check_id, "status": status, "detail": detail}


def _refusal_detail(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        return f"git {' '.join(map(str, exc.cmd))} failed: {stderr or exc.returncode}"
    return f"{type(exc).__name__}: {exc}"


def _is_check_refusal(exc: BaseException) -> bool:
    """A check fails on a Workflow refusal, or on Git refusing a commit or
    object the state names (a repository fault, not a defect). Anything
    else propagates as `internal_error`."""
    return is_workflow_exception(exc) or isinstance(exc, subprocess.CalledProcessError)


def _checkpoint_start_descendant(repo_root: Path, commit: str, checkpoint_id: str, starts: dict[str, str]) -> bool:
    """Whether `commit` is the checkpoint's own latest completion: it
    strictly descends from `checkpoints[<id>].start_commit`, which an amendment
    revalidation rewrites. The earlier completion commit of a demoted and
    re-completed checkpoint precedes that start and is rejected. A
    checkpoint with no recorded start is not narrowed."""
    start = starts.get(checkpoint_id)
    return start is None or (
        commit != start and workflow_state._is_ancestor(repo_root, start, commit))


def prove_checkpoint_completions(
        work_item: dict, repo_root: Path, base_commit: str, only: list[str] | None = None) -> None:
    """`workflow_state.verify_checkpoint_completions`, made aware of the
    Workflow's own amendment revalidation. A revalidated checkpoint has two
    `Workflow-Checkpoint` commits, both first-parent ancestors that record it
    `COMPLETE`, which `discover_checkpoint_commits` calls ambiguous. The
    tie-break here adds a third filter, descent from the recorded
    `start_commit`, ANDed into the discovery's verify predicate. A history
    2.6.0 resolved resolves identically unless the commit discovery
    resolves (a sole trailer candidate, a sole first-parent candidate among
    several, or the one first-parent candidate recording `COMPLETE`) does
    not descend from the live `start_commit`, which only a malformed history
    produces; this proof raises there. Discovery accepts a sole trailer
    candidate without consulting the tie-break, so a reopened checkpoint
    already marked `COMPLETE` whose only trailer commit is its earlier one
    is caught by the explicit descent check after discovery. `only` limits
    the proof to the named checkpoints (reconcile proves the ones this step
    completed); the tie-break still sees every checkpoint's start."""
    work_item_id = work_item["work_item_id"]
    checkpoints = work_item.get("checkpoints") or {}
    starts = {cid: entry["start_commit"] for cid, entry in checkpoints.items()
              if isinstance(entry, dict) and isinstance(entry.get("start_commit"), str)}
    discovered = workflow_state._discover_trailer_commits(
        repo_root, "Workflow-Checkpoint", work_item_id, base_commit, "HEAD",
        ambiguous_error_cls=workflow_state.AmbiguousCheckpointTrailerError,
        verify=lambda root, commit, checkpoint_id, _candidates: (
            workflow_state._checkpoint_commit_claims_complete(root, commit, checkpoint_id, work_item_id)
            and _checkpoint_start_descendant(root, commit, checkpoint_id, starts)),
    )
    for checkpoint_id, entry in checkpoints.items():
        if only is not None and checkpoint_id not in only:
            continue
        if entry.get("status") == "COMPLETE" and checkpoint_id not in discovered:
            raise workflow_state.CheckpointNotReachableError(
                f"{work_item_id}/{checkpoint_id} is COMPLETE in state but no "
                f"commit in {base_commit}..HEAD carries a matching "
                f"Workflow-Checkpoint/Workflow-Work-Item trailer pair")
        if (entry.get("status") == "COMPLETE"
                and not _checkpoint_start_descendant(repo_root, discovered[checkpoint_id], checkpoint_id, starts)):
            raise workflow_state.CheckpointNotReachableError(
                f"{work_item_id}/{checkpoint_id} is COMPLETE in state but its resolved "
                f"completion commit {discovered[checkpoint_id]} does not strictly "
                f"descend from the checkpoint's start_commit {starts[checkpoint_id]}")


def op_verify(repo_root: Path, args: argparse.Namespace) -> dict:
    checks: list[dict] = []
    state = None
    config = None
    try:
        state, config = read_state_and_config(repo_root)
        checks.append(_check("state_readable", "pass", "the state and the config parse"))
    except ProtocolError as exc:
        checks.append(_check("state_readable", "fail", exc.message))

    if state is None:
        checks.append(_check("state_valid", "skip", "the state is unreadable"))
    else:
        try:
            workflow_state.validate_state(state, repo_root=repo_root)
            checks.append(_check("state_valid", "pass", "validate_state accepts the state"))
        except Exception as exc:
            if not _is_check_refusal(exc):
                raise
            checks.append(_check("state_valid", "fail", _refusal_detail(exc)))

    if state is None:
        checks.append(_check("config_valid", "skip", "the state is unreadable"))
    else:
        try:
            if config is not None:
                workflow_state.validate_config(config)
                checks.append(_check("config_valid", "pass", "validate_config accepts the config"))
            else:
                workflow_state.load_config(repo_root)
                checks.append(_check(
                    "config_valid", "pass", "no config file; the pre-activation default applies"))
        except Exception as exc:
            if not _is_check_refusal(exc):
                raise
            checks.append(_check("config_valid", "fail", _refusal_detail(exc)))

    work_items = state.get("work_items") if state is not None else None
    if state is None:
        checks.append(_check("active_item_resolvable", "skip", "the state is unreadable"))
    else:
        active = state.get("active_work_item_id")
        if active is None:
            checks.append(_check("active_item_resolvable", "pass", "no active work item"))
        elif not isinstance(active, str) or not isinstance(work_items, dict) or active not in work_items:
            checks.append(_check(
                "active_item_resolvable", "fail", f"active_work_item_id {active!r} names no work item"))
        elif not isinstance(work_items[active], dict) or \
                work_items[active].get("phase") in workflow_state.TERMINAL_PHASES:
            checks.append(_check(
                "active_item_resolvable", "fail",
                f"active_work_item_id {active!r} names a terminal or malformed work item"))
        else:
            checks.append(_check("active_item_resolvable", "pass", f"{active} is non-terminal"))

    if state is None:
        checks.append(_check("checkpoint_completions_provable", "skip", "the state is unreadable"))
    elif not isinstance(work_items, dict):
        checks.append(_check("checkpoint_completions_provable", "skip", "work_items is not an object"))
    else:
        failures: list[str] = []
        proven: list[str] = []
        for work_item_id, work_item in sorted(work_items.items()):
            if not isinstance(work_item, dict) or work_item.get("phase") in workflow_state.TERMINAL_PHASES:
                continue
            checkpoints = work_item.get("checkpoints")
            if not isinstance(checkpoints, dict) or not any(
                    isinstance(entry, dict) and entry.get("status") == "COMPLETE"
                    for entry in checkpoints.values()):
                continue
            base_commit = work_item.get("base_commit")
            if not isinstance(base_commit, str):
                failures.append(f"{work_item_id}: COMPLETE checkpoints but no base_commit")
                continue
            try:
                prove_checkpoint_completions(work_item, repo_root, base_commit)
                proven.append(work_item_id)
            except Exception as exc:
                if not _is_check_refusal(exc):
                    raise
                failures.append(f"{work_item_id}: {_refusal_detail(exc)}")
        if failures:
            checks.append(_check("checkpoint_completions_provable", "fail", "; ".join(failures)))
        else:
            checks.append(_check(
                "checkpoint_completions_provable", "pass",
                "every COMPLETE checkpoint has a reachable trailer commit"
                + (f" ({', '.join(proven)})" if proven else " (none recorded)")))

    record_path = repo_root / workflow_state.INSTALLATION_RECORD_PATH
    if not record_path.exists():
        checks.append(_check("installation_release_matches", "skip", "no installation record"))
    else:
        try:
            record = json.loads(record_path.read_text())
            version = record.get("workflow_version") if isinstance(record, dict) else None
        except (OSError, ValueError) as exc:
            checks.append(_check(
                "installation_release_matches", "fail", f"the installation record is unreadable: {exc}"))
        else:
            if version == WORKFLOW_RELEASE:
                checks.append(_check(
                    "installation_release_matches", "pass", f"installed release {version}"))
            else:
                checks.append(_check(
                    "installation_release_matches", "fail",
                    f"the installation record names {version!r}, these scripts are {WORKFLOW_RELEASE}"))

    ready = all(c["status"] == "pass" for c in checks[:4])
    checks.append(_check(
        "protocol_ready", "pass" if ready else "fail",
        "checks 1-4 passed" if ready else "a check among 1-4 did not pass"))
    return {"healthy": all(c["status"] != "fail" for c in checks), "checks": checks}


# ---------------------------------------------------------------------------
# resolve-artifact (D-OP-Artifacts)
# ---------------------------------------------------------------------------


def _feedback_file(name: str):
    def resolve(repo_root: Path, work_item_id: str, work_item: dict) -> Path:
        return workflow_fingerprint.resolve_feedback_dir(repo_root, work_item_id) / name
    return resolve


def _review_bundle(repo_root: Path, work_item_id: str, work_item: dict) -> Path:
    if work_item.get("phase") in PLAN_STAGE_PHASES:
        return workflow_fingerprint.resolve_bundle_dir(repo_root, work_item_id, stage="plan")
    return workflow_fingerprint.resolve_bundle_dir(repo_root, work_item_id)


def _plan_review_inputs(repo_root: Path, work_item_id: str, work_item: dict) -> Path:
    return workflow_fingerprint.resolve_plan_review_inputs_dir(repo_root, work_item_id)


def _plan_document(repo_root: Path, work_item_id: str, work_item: dict) -> Path | None:
    plan_path = work_item.get("plan_path")
    return Path(plan_path) if isinstance(plan_path, str) and plan_path else None


def _functional_checklist(repo_root: Path, work_item_id: str, work_item: dict) -> Path:
    return Path(workflow_state.FUNCTIONAL_CHECKLIST_PATH)


#: The v1 artifact kinds, each resolved by the Workflow's own helper only.
ARTIFACT_KINDS = {
    "review_feedback": _feedback_file(REVIEW_FEEDBACK_FILE),
    "functional_review": _feedback_file(FUNCTIONAL_REVIEW_FILE),
    "review_bundle": _review_bundle,
    "plan_review_inputs": _plan_review_inputs,
    "plan_document": _plan_document,
    "functional_checklist": _functional_checklist,
}


def op_resolve_artifact(repo_root: Path, args: argparse.Namespace) -> dict:
    if args.kind not in ARTIFACT_KINDS:
        raise ProtocolError(
            "invalid_request",
            f"unknown artifact kind {args.kind!r}; expected one of {sorted(ARTIFACT_KINDS)}")
    state = load_valid_state(repo_root)
    work_item = work_item_of(state, args.work_item)
    path = ARTIFACT_KINDS[args.kind](repo_root, args.work_item, work_item)
    return {
        "kind": args.kind,
        "path": path.as_posix() if path is not None else None,
        "exists": path is not None and (repo_root / path).exists(),
        "basis": basis(repo_root, state, args.work_item),
    }


# ---------------------------------------------------------------------------
# next-action: the action catalogue (D-OP-Next)
# ---------------------------------------------------------------------------

VOCABULARY_ONLY_PHASES = frozenset({
    "SELF_REVIEWING_PLAN", "AWAITING_TECHNICAL_APPROVAL", "FIXING_FUNCTIONAL_FINDINGS", "AWAITING_USER_ACCEPTANCE",
})
TWO_STAGE_VERSIONS = frozenset({"2.1", "2.2"})
ALL_VERSIONS = frozenset(SUPPORTED_GOVERNING_VERSIONS)
PLAN_READY_PHASES = frozenset(workflow_state.PLAN_REVIEW_READY_PHASES)
TWO_STAGE_PLAN_PHASES = PLAN_READY_PHASES | {"PLANNING", "AMENDING_PLAN", "REVISING_PLAN"}
#: Row 6's seven review phases (its two apply phases are `APPLY_PHASES`).
REVIEW_PHASES = frozenset({
    "AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_PLAN_APPROVAL",
    "AWAITING_EXTERNAL_PLAN_REVIEW", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
})
APPLY_PHASES = frozenset({"REVISING_PLAN", "APPLYING_REVIEW_FEEDBACK"})
IMPLEMENTATION_REVIEW_PHASES_2_2 = frozenset({
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
})

#: Row 4: the `(phase, gv)` pairs no writer persists (grounded in
#: `TestPersistedPhaseWriterCensus.EXPECTED_WRITERS`; `SELF_REVIEWING_IMPLEMENTATION`
#: at `"1"` in the commands).
ILLEGAL_PHASE_VERSIONS = {
    "REVISING_PLAN": frozenset({"1"}),
    "AWAITING_PLAN_APPROVAL": frozenset({"1"}),
    "AWAITING_LOCAL_PLAN_REVIEW": frozenset({"1"}),
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW": frozenset({"1"}),
    "AWAITING_EXTERNAL_PLAN_REVIEW": frozenset({"2.1", "2.2"}),
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW": frozenset({"1", "2.1"}),
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW": frozenset({"1", "2.1"}),
    "SELF_REVIEWING_IMPLEMENTATION": frozenset({"1"}),
}


def phase_legal_for_version(phase: str, governing_workflow_version: str) -> bool:
    """False exactly for row 4's pairs."""
    return governing_workflow_version not in ILLEGAL_PHASE_VERSIONS.get(phase, frozenset())


def _c(function: str, kind: str, *classes: str) -> dict:
    return {"function": function, "kind": kind, "classes": list(classes)}


_PARSER = "parse_review_feedback_header"
_P = "plan_review_publication_status"
_I = "approval_review_content_id"
_B = "compute_bundle_id"
_FB_BINDING = ("FeedbackBundleMismatchError", "MissingFeedbackBindingFieldError")
_APPLY_PLAN_ACCEPTANCE = ("FeedbackStatusNotApplicableError", "FeedbackNotForConsumedContentError")

#: The per-row condition calls (D-OP-Next, `LPR-R5-001`): each row's calls
#: in evaluation order, each with its kind and the exception classes that
#: carry the kind's meaning -- a guard's listed class makes the condition
#: true, an acceptance's makes its sub-condition false, a value's names a
#: reason. Any other Workflow exception ends the evaluation at the row as
#: `condition_refused`. The predicates evaluate a call only through this
#: table. A row may declare one function twice, once per kind, where one
#: invocation's refusals divide between a guard's and an acceptance's
#: classes (rows 7a and 8a).
CONDITION_CALLS = {
    "1": [_c("load_config", "value")],
    "1a": [_c("load_config", "value")],
    "2": [], "3": [], "4": [],
    "5": [_c(_P, "guard", "PlanReviewBindingInconsistentError")],
    "6": [_c("assert_bundle_not_rejected", "guard", "BundleRejectedError")],
    "6a": [], "7": [],
    "7a": [
        _c(_P, "value"), _c(_PARSER, "value"),
        _c("assert_apply_plan_review_feedback", "acceptance", *_APPLY_PLAN_ACCEPTANCE),
        _c("assert_apply_review_feedback_binding", "guard",
           "MissingRequiredBundleFileError", "ReviewBundleManifestMismatchError"),
        _c("assert_apply_review_feedback_binding", "acceptance", "FeedbackContentMismatchError", *_FB_BINDING),
    ],
    "8": [
        _c(_P, "value"), _c(_PARSER, "value"),
        _c("assert_apply_plan_review_feedback", "acceptance", *_APPLY_PLAN_ACCEPTANCE),
        _c("assert_apply_review_feedback_binding", "acceptance", "FeedbackContentMismatchError", *_FB_BINDING),
    ],
    "8a": [
        _c(_P, "value"), _c(_PARSER, "value"),
        _c("assert_apply_plan_review_feedback", "acceptance", *_APPLY_PLAN_ACCEPTANCE),
        _c("assert_apply_review_feedback_binding", "guard", *_FB_BINDING),
        _c("assert_apply_review_feedback_binding", "acceptance", "FeedbackContentMismatchError"),
    ],
    "9": [],
    "10": [_c(_P, "value")],
    "11": [_c(_PARSER, "value"), _c(_P, "value")],
    "11a": [_c(_PARSER, "value"), _c(_P, "value"),
            _c("assert_local_generation_matches", "guard", "WorktreeOrHeadMismatchError")],
    "12": [],
    "13": [_c(_PARSER, "value"), _c(_P, "value")],
    "14": [],
    "15": [_c("plan_approval_gate_status", "value")],
    "16": [_c("plan_approval_gate_status", "value")],
    "16a": [_c(_B, "guard", "MissingRequiredBundleFileError")],
    "17": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING)],
    "18": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING)],
    "19": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
           _c("plan_approval_gate_status", "value")],
    "20": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
           _c("plan_approval_gate_status", "value")],
    "20a": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
            _c("plan_approval_gate_status", "value")],
    "21": [],
    "22": [_c("implementing_entry_status", "value")],
    "23": [_c("registry_completion_status", "value"), _c("select_next_checkpoint", "value")],
    "24": [],
    "25": [_c(_PARSER, "value"), _c(_I, "value")],
    "25a": [_c(_PARSER, "value"), _c(_I, "value"),
            _c("assert_local_generation_matches", "guard", "WorktreeOrHeadMismatchError")],
    "25b": [_c("verify_implementation_review_bundle", "guard", "ImplementationReviewBundleUnverifiedError")],
    "26": [],
    "27": [_c(_PARSER, "value"), _c(_I, "value")],
    "28": [],
    "29": [_c("technical_approval_gate_status", "value")],
    "30": [_c("technical_approval_gate_status", "value")],
    "30a": [_c(_B, "guard", "MissingRequiredBundleFileError")],
    "31": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
           _c("is_technical_review_block_pinned", "value")],
    "31a": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
            _c("is_technical_review_block_pinned", "value")],
    "32": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING)],
    "33": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
           _c("technical_approval_gate_status", "value")],
    "34": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
           _c("technical_approval_gate_status", "value")],
    "35": [_c(_PARSER, "value"), _c(_B, "value"), _c("assert_feedback_matches_bundle", "acceptance", *_FB_BINDING),
           _c("technical_approval_gate_status", "value")],
    "35a": [_c(_PARSER, "value"),
            _c("assert_apply_review_feedback_binding", "value",
               "MissingRequiredBundleFileError", "ReviewBundleManifestMismatchError",
               "FeedbackContentMismatchError", *_FB_BINDING)],
    "36": [],
    "37": [_c("discover_current_functional_checklist_evidence", "value")],
    "38": [_c("resolve_feedback_dir", "value"),
           _c("assert_functional_review_not_already_consumed", "acceptance", "FunctionalReviewAlreadyAppliedError")],
    "38a": [_c("resolve_own_registry_completion_status", "value",
               "StalePlanApprovalRegistryReadError", "RegistryCoverageError")],
    "38b": [_c("resolve_own_registry_completion_status", "value")],
    "38c": [_c("resolve_own_registry_completion_status", "value")],
    "39": [_c("resolve_own_registry_completion_status", "value")],
    "40": [],
}

#: The callables behind `CONDITION_CALLS`' function names, resolved on the
#: imported modules at call time (so a test can patch them).
_CONDITION_FUNCTION_MODULES = {
    "load_config": workflow_state,
    _P: workflow_state,
    "assert_bundle_not_rejected": workflow_fingerprint,
    _PARSER: workflow_fingerprint,
    "assert_apply_plan_review_feedback": workflow_state,
    "assert_apply_review_feedback_binding": workflow_state,
    "assert_local_generation_matches": workflow_fingerprint,
    "plan_approval_gate_status": workflow_state,
    _B: workflow_fingerprint,
    "assert_feedback_matches_bundle": workflow_fingerprint,
    "implementing_entry_status": workflow_state,
    "registry_completion_status": workflow_state,
    "select_next_checkpoint": workflow_state,
    _I: workflow_state,
    "verify_implementation_review_bundle": workflow_state,
    "technical_approval_gate_status": workflow_state,
    "is_technical_review_block_pinned": workflow_state,
    "discover_current_functional_checklist_evidence": workflow_state,
    "resolve_feedback_dir": workflow_fingerprint,
    "assert_functional_review_not_already_consumed": workflow_fingerprint,
    "resolve_own_registry_completion_status": workflow_state,
}


def _fn(name: str):
    return getattr(_CONDITION_FUNCTION_MODULES[name], name)


class _ConditionRefused(Exception):
    """A Workflow exception no declared class of the row covers: the
    evaluation ends at the row as `condition_refused`."""

    def __init__(self, row_id: str, exc: BaseException):
        super().__init__(str(exc))
        self.row_id = row_id
        self.exc = exc


class _CallResult:
    """One condition call's outcome: its value, or a declared exception
    together with the kind its class was declared under."""

    def __init__(self, value=None, error: BaseException | None = None, kind: str | None = None):
        self.value = value
        self.error = error
        self.kind = kind

    @property
    def ok(self) -> bool:
        return self.error is None


class _Context:
    """One `next-action` evaluation: the state read once, and every
    condition call made through `call`, which enforces the row's declared
    calls and memoizes values a later row reuses (`LPR-R6-003`)."""

    def __init__(self, repo_root: Path, state: dict, work_item_id: str | None):
        self.repo_root = repo_root
        self.state = state
        self.work_item_id = work_item_id
        self.work_item = state["work_items"][work_item_id] if work_item_id is not None else None
        self.phase = self.work_item.get("phase") if self.work_item else None
        self.gv = self.work_item.get("governing_workflow_version") if self.work_item else None
        self.row_id: str | None = None
        self.in_call: str | None = None
        self.reached: list[tuple[str, str]] = []
        self._memo: dict = {}

    def call(self, function: str, thunk, *, key=None) -> _CallResult:
        entries = [entry for entry in CONDITION_CALLS[self.row_id] if entry["function"] == function]
        if not entries:
            raise AssertionError(f"catalogue row {self.row_id} calls {function}, which its CONDITION_CALLS entry does not declare")
        self.reached.append((self.row_id, function))
        memo_key = (function, key)
        if memo_key not in self._memo:
            self.in_call = function
            try:
                self._memo[memo_key] = (thunk(), None)
            except Exception as exc:
                if not is_workflow_exception(exc):
                    raise
                self._memo[memo_key] = (None, exc)
            finally:
                self.in_call = None
        value, error = self._memo[memo_key]
        if error is None:
            return _CallResult(value=value)
        for entry in entries:
            if type(error).__name__ in entry["classes"]:
                return _CallResult(error=error, kind=entry["kind"])
        raise _ConditionRefused(self.row_id, error)

    def memo_value(self, function: str, key=None):
        value, error = self._memo.get((function, key), (None, None))
        return None if error is not None else value

    # -- shared terms ------------------------------------------------------

    @property
    def base_commit(self) -> str:
        return self.work_item["base_commit"]

    def fb(self) -> dict:
        """The verdict parser over the item's `REVIEW_FEEDBACK.md`, resolved
        by `resolve_feedback_dir`: `{"path", "text", "fields", "present"}`;
        `present` is false for an absent file or one whose `Work item:`
        names another item (no fb)."""
        def parse():
            path = workflow_fingerprint.resolve_feedback_dir(self.repo_root, self.work_item_id) / REVIEW_FEEDBACK_FILE
            try:
                text = (self.repo_root / path).read_text()
            except FileNotFoundError:
                text = None
            fields = _fn(_PARSER)(text) if text is not None else {}
            named = fields.get("work_item")
            return {"path": path.as_posix(), "text": text, "fields": fields,
                    "present": text is not None and named in (None, self.work_item_id)}
        return self.call(_PARSER, parse).value

    def publication(self) -> _CallResult:
        return self.call(_P, lambda: _fn(_P)(self.repo_root, self.state, self.work_item_id))

    def P(self) -> str | None:
        return self.publication().value.get("fresh_review_content_id")

    def I(self) -> str:
        work_item = self.work_item
        return self.call(_I, lambda: _fn(_I)(
            self.repo_root, stage="implementation", base_commit=self.base_commit,
            work_item_type=work_item["work_item_type"], work_item_id=self.work_item_id, head="HEAD",
            artifacts_path=workflow_fingerprint.artifacts_path_for_work_item(self.work_item_id),
        )).value

    def plan_bundle_dir(self) -> Path:
        return workflow_fingerprint.resolve_bundle_dir(self.repo_root, self.work_item_id, stage="plan")

    def implementation_bundle_dir(self) -> Path:
        return workflow_fingerprint.resolve_bundle_dir(self.repo_root, self.work_item_id)

    def B(self, stage: str) -> _CallResult:
        bundle_dir = self.plan_bundle_dir() if stage == "plan" else self.implementation_bundle_dir()
        return self.call(_B, lambda: _fn(_B)(self.repo_root / bundle_dir)[0], key=stage)

    def bundle_binding_accepts(self, stage: str) -> bool:
        """"fb is current" at the `"1"` plan stage and the `"1"`/`"2.1"`
        implementation stage: `assert_feedback_matches_bundle` against
        **B**, the item's `base_commit` and its id."""
        fb = self.fb()
        if not fb["present"]:
            return False
        bundle = self.B(stage)
        if not bundle.ok:
            return False
        result = self.call("assert_feedback_matches_bundle", lambda: _fn("assert_feedback_matches_bundle")(
            workflow_fingerprint.parse_review_feedback_binding_fields(fb["text"]),
            bundle_id=bundle.value, base_commit=self.base_commit, work_item_id=self.work_item_id,
        ))
        return result.ok

    def content_current(self, current_id: str | None) -> bool:
        """"fb is current" at a two-stage stage: fb's `review_content_id`
        equals **P** or **I**; its `bundle_id` is advisory."""
        fb = self.fb()
        return fb["present"] and current_id is not None and fb["fields"].get("review_content_id") == current_id

    def unrecorded_manual_verdict(self, stage: str) -> bool:
        """fb is current, its `Reviewer role:` normalizes to the stage's
        manual role, its `Status:` is `APPROVE` or `REVISE`, and the ledger
        records no manual stage for its content."""
        if stage == "plan":
            current_id = self.P()
            normalize_key = workflow_state._normalize_plan_review_stage_key
            manual = workflow_state.MANUAL_EXTERNAL_PLAN_REVIEW
            stages = workflow_state.normalize_plan_review_stages(self.work_item.get("plan_review_stages") or {})
        else:
            current_id = self.I()
            normalize_key = workflow_state._normalize_implementation_review_stage_key
            manual = workflow_state.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW
            stages = workflow_state.normalize_implementation_review_stages(
                self.work_item.get("implementation_review_stages") or {})
        if not self.content_current(current_id):
            return False
        fields = self.fb()["fields"]
        role = fields.get("reviewer_role")
        if role is None or normalize_key(role) != manual or fields.get("status") not in ("APPROVE", "REVISE"):
            return False
        return not (stages.get("review_content_id") == current_id and stages.get(manual) is not None)

    def generation_refuses(self, stage: str) -> _CallResult:
        bundle_dir = self.plan_bundle_dir() if stage == "plan" else self.implementation_bundle_dir()
        return self.call("assert_local_generation_matches", lambda: _fn("assert_local_generation_matches")(
            self.repo_root, self.repo_root / bundle_dir / workflow_fingerprint.MANIFEST_FILENAME,
        ), key=stage)

    def plan_gate(self) -> dict:
        return self.call("plan_approval_gate_status", lambda: _fn("plan_approval_gate_status")(
            self.repo_root, self.state, self.work_item_id)).value

    def technical_gate(self) -> dict:
        return self.call("technical_approval_gate_status", lambda: _fn("technical_approval_gate_status")(
            self.repo_root, self.state, self.work_item_id)).value

    def registry(self) -> dict | None:
        """The item's own registry (`registry_path`), or `None` for a
        registry-less item. A missing or corrupt declared file is a
        Workflow refusal."""
        registry_path = self.work_item.get("registry_path")
        if registry_path is None:
            return None
        registry = workflow_state._load_json(self.repo_root / registry_path)
        if not isinstance(registry, dict):
            raise workflow_state.CorruptJsonError(f"{registry_path} is missing or not a JSON object")
        return registry

    def regenerate_implementation_command(self) -> str:
        """`./scripts/prepare-ai-review.sh <base> <stage> <id>`, the stage
        of the round being regenerated (`LPR-R6-004`): `implementation` at
        `implementation_revision` 1, `post-fix` after."""
        revision = self.work_item.get("implementation_revision")
        stage = "post-fix" if isinstance(revision, int) and revision > 1 else "implementation"
        return f"./scripts/prepare-ai-review.sh {self.base_commit} {stage} {self.work_item_id}"


class Row:
    """One catalogue row: the `(phase, gv)` pairs it covers, its predicate
    (`None` for no match, else the match's reason and action details), its
    disposition and action, and the commands its prose names (`remedy_commands`,
    `refusing_commands`; catalogue data, not envelope fields)."""

    def __init__(self, row_id: str, phases, versions, disposition: str, action_id: str | None, predicate, *,
                 unconditional: bool = False, remedy_commands=(), refusing_commands=(), pairs=None):
        self.row_id = row_id
        self.no_item = phases is None and pairs is None
        self.phases = frozenset(phases) if phases is not None else frozenset()
        self.versions = frozenset(versions) if versions is not None else frozenset()
        self.pairs = frozenset(pairs) if pairs is not None else frozenset(
            (phase, version) for phase in self.phases for version in self.versions)
        self.disposition = disposition
        self.action_id = action_id
        self.predicate = predicate
        self.unconditional = unconditional
        self.remedy_commands = remedy_commands
        self.refusing_commands = tuple(refusing_commands)

    def covers(self, phase: str, version: str) -> bool:
        return (phase, version) in self.pairs

    def remedy_commands_for(self, phase: str, version: str) -> tuple[str, ...]:
        """The commands this row's remedy and alternatives name at
        `(phase, version)`; `none_exists` when no route exists."""
        if callable(self.remedy_commands):
            return tuple(self.remedy_commands(phase, version))
        return tuple(self.remedy_commands)


def _match(code: str, text: str = "", remedy: str | None = None, *, alternatives=(), arguments=None,
           satisfied_by: str | None = None, action_id: str | None = None) -> dict:
    return {"reason": {"code": code, "text": text, "remedy": remedy}, "alternatives": list(alternatives),
            "arguments": arguments or {}, "satisfied_by": satisfied_by, "action_id": action_id}


def _alt(action_id: str, **extra) -> tuple[str, dict]:
    return (action_id, extra)


def _withdraw_alt() -> tuple[str, dict]:
    return _alt("plan.withdraw")


# -- the predicates, in catalogue order -------------------------------------


def _row_1(ctx):
    config = ctx.call("load_config", lambda: _fn("load_config")(ctx.repo_root)).value
    if config.get("default_workflow_version") in TWO_STAGE_VERSIONS:
        return _match("plan_start", "no active work item; /milestone-plan creates one at the two-stage default")
    return None


def _row_1a(ctx):
    config = ctx.call("load_config", lambda: _fn("load_config")(ctx.repo_root)).value
    return _match(
        "plan_start_not_tracked",
        f"no active work item, and the config's default_workflow_version is "
        f"{config.get('default_workflow_version')!r}: a \"1\" milestone-plan run writes no state",
        "a \"1\" milestone-plan run creates no work item: activate a two-stage default_workflow_version, "
        "or run the \"1\" milestone outside the protocol",
    )


def _row_2(ctx):
    return _match("invalid_state", f"{ctx.phase} is a vocabulary-only phase; no writer persists it")


def _row_3(ctx):
    return _match("legacy_item_not_activated", f"{ctx.work_item_id} is a dormant legacy entry (LEGACY_READY)",
                  "promote it (D-Legacy phase 2) before driving it")


def _row_4(ctx):
    return _match("phase_not_legal_for_governing_version",
                  f"no writer persists {ctx.phase} for a governing_workflow_version {ctx.gv!r} item")


def _row_5(ctx):
    """The remedy is the error's own: a withdrawal only for publication row
    4d (a ready phase); at a non-ready phase (publication row 6) the
    `/milestone-plan` writers refuse the same record, so none is offered."""
    result = ctx.publication()
    if result.ok:
        return None
    ready = ctx.phase in PLAN_READY_PHASES
    return _match("plan_review_binding_inconsistent", str(result.error),
                  f"withdraw with /milestone-plan {ctx.work_item_id}" if ready else
                  "none exists: repair the plan_review_binding record by hand",
                  alternatives=[_withdraw_alt()] if ready else [])


def _row_6(ctx):
    result = ctx.call("assert_bundle_not_rejected", lambda: _fn("assert_bundle_not_rejected")(
        ctx.repo_root, ctx.work_item_id))
    if result.ok:
        return None
    alternatives = []
    remedy = str(result.error)
    if ctx.gv in TWO_STAGE_VERSIONS and ctx.phase in TWO_STAGE_PLAN_PHASES:
        alternatives = [_withdraw_alt()]
        remedy += f"; a successful generation clears the marker (/milestone-plan {ctx.work_item_id})"
    return _match("bundle_rejected", str(result.error), remedy, alternatives=alternatives)


def _row_6a(ctx):
    failing = (
        "the \"1\" branch of milestone-plan writes no state, so no \"1\" command publishes the plan"
        if ctx.phase in ("PLANNING", "AMENDING_PLAN") else
        "record_bundle_generation(stage=\"implementation\") refuses from IMPLEMENTING "
        "(IllegalBundleGenerationSourcePhaseError)"
    )
    return _match("v1_state_not_advanced",
                  f"{ctx.phase} at governing_workflow_version \"1\": {failing}",
                  "none exists: no 2.6.0 \"1\" command advances this state (defect v2.6.0-003)")


def _row_7(ctx):
    return _match("plan_needs_authoring", f"{ctx.work_item_id} is at {ctx.phase}")


def _apply_plan_round(ctx) -> dict:
    """Rows 7a, 8, 8a and 9's shared evaluation of `/apply-plan-review`'s
    step-1 acceptance at `REVISING_PLAN`. Returns `{"fb", "mode",
    "binding"}`: `mode` is `None` when the acceptance refuses (or there is
    no fb), and `binding` the binding call's result in `"bundle"` mode."""
    publication = ctx.publication().value
    fb = ctx.fb()
    outcome = {"fb": fb, "mode": None, "binding": None}
    if fb["text"] is None:
        return outcome
    accepted = ctx.call("assert_apply_plan_review_feedback", lambda: _fn("assert_apply_plan_review_feedback")(
        ctx.work_item, ctx.work_item_id, feedback_content=fb["text"], publication_status=publication["status"]))
    if not accepted.ok:
        return outcome
    outcome["mode"] = accepted.value
    if accepted.value == "bundle":
        outcome["binding"] = ctx.call(
            "assert_apply_review_feedback_binding",
            lambda: _fn("assert_apply_review_feedback_binding")(
                ctx.repo_root, ctx.work_item, ctx.work_item_id, stage="plan", feedback_content=fb["text"]))
    return outcome


def _row_7a(ctx):
    outcome = _apply_plan_round(ctx)
    binding = outcome["binding"]
    if binding is None or binding.ok or binding.kind != "guard":
        return None
    return _match(
        "bundle_unverified", f"{type(binding.error).__name__}: {binding.error}",
        f"run from the worktree that holds the reviewed plan bundle, or restore its "
        f".ai-review/{ctx.work_item_id}/current/; or withdraw with /milestone-plan {ctx.work_item_id}",
        alternatives=[_withdraw_alt()],
    )


def _row_8(ctx):
    outcome = _apply_plan_round(ctx)
    if outcome["mode"] == "durable":
        return _match("review_feedback_to_apply", "the recorded REVISE is bound to the consumed content")
    binding = outcome["binding"]
    if binding is not None and binding.ok:
        advisory = binding.value.get("advisory")
        return _match("review_feedback_to_apply",
                      f"the REVISE binds by {binding.value['binding']}" + (f" ({advisory})" if advisory else ""))
    return None


def _row_8a(ctx):
    outcome = _apply_plan_round(ctx)
    binding = outcome["binding"]
    fields = outcome["fb"]["fields"]
    if binding is None or binding.ok or binding.kind != "guard":
        return None
    if getattr(binding.error, "binding", None) != "bundle" or fields.get("work_item") != ctx.work_item_id:
        return None
    expected_bundle = getattr(binding.error, "bundle_id", None)
    return _match(
        "review_feedback_unbound",
        f"the REVISE cannot be bound to a round: it states no review_content_id the content binding "
        f"can use, and its bundle fields do not name the reviewed bundle ({type(binding.error).__name__}: "
        f"{binding.error}). Expected Reviewed bundle ID {expected_bundle!r}, Reviewed base commit "
        f"{ctx.base_commit!r}, Work item {ctx.work_item_id!r}; the verdict states "
        f"{fields.get('reviewed_bundle_id') or 'absent'!r}, {fields.get('reviewed_base_commit') or 'absent'!r}, "
        f"{fields.get('work_item') or 'absent'!r}",
        "obtain a verdict for the reviewed bundle from its reviewer, stating this round's "
        "review_content_id or the three binding fields as the reviewer saw them; a verdict's binding "
        f"fields attest to what its reviewer reviewed. Or discard it knowingly: /milestone-plan {ctx.work_item_id}",
        alternatives=[_withdraw_alt()],
    )


def _row_9(ctx):
    return _match("plan_needs_authoring",
                  "no applicable REVISE: a withdrawal, an absent feedback file, or a verdict of another round")


_PUBLICATION_BLOCKING_STATUSES = ("CONTENT_DRIFTED", "BUNDLE_UNVERIFIED", "LEGACY_UNVERIFIED")


def _row_10(ctx):
    publication = ctx.publication().value
    if publication["status"] not in _PUBLICATION_BLOCKING_STATUSES:
        return None
    return _match(publication["status"].lower(), publication.get("detail") or publication["status"],
                  publication["remedy"], alternatives=[_withdraw_alt()])


def _row_11(ctx):
    if ctx.content_current(ctx.P()) and ctx.fb()["fields"].get("status") == "BLOCK":
        return _match("review_blocked", "a current BLOCK verdict: explicit user resolution required",
                      alternatives=[_withdraw_alt()])
    return None


def _row_11a(ctx):
    if ctx.phase == "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW" and not ctx.unrecorded_manual_verdict("plan"):
        return None
    result = ctx.generation_refuses("plan")
    if result.ok:
        return None
    return _match(
        "bundle_generation_mismatch", str(result.error),
        f"run from the generating worktree, or regenerate the plan bundle at the current HEAD "
        f"(./scripts/prepare-ai-review.sh {ctx.base_commit} plan {ctx.work_item_id}; a wrapper-only "
        f"regeneration keeps the binding); or withdraw with /milestone-plan {ctx.work_item_id}",
        alternatives=[_withdraw_alt()],
    )


def _row_12(ctx):
    return _match("local_review_due", "the bound plan bundle awaits its local model review")


def _row_13(ctx):
    if ctx.unrecorded_manual_verdict("plan"):
        return _match("manual_verdict_unrecorded", "a manual external verdict for the current content is unrecorded")
    return None


def _row_14(ctx):
    return _match("awaiting_external_review", "awaiting the manual external plan-review verdict",
                  satisfied_by="plan_review_verdict")


def _row_15(ctx):
    if ctx.plan_gate()["reachable"]:
        return _match("approval_gate_reachable", "the plan approval gate is reachable")
    return None


def _gate_blocked(ctx, gate: dict, **kwargs) -> dict:
    return _match(gate["cause"], f"the approval gate is unreachable: {gate['cause']}", **kwargs)


def _row_16(ctx):
    gate = ctx.plan_gate()
    return _gate_blocked(ctx, gate, remedy=f"resolve {gate['cause']}, or withdraw with /milestone-plan {ctx.work_item_id}",
                         alternatives=[_withdraw_alt()])


def _row_16a(ctx):
    result = ctx.B("plan")
    if result.ok:
        return None
    return _match("bundle_unverified", str(result.error),
                  f"regenerate the plan bundle (./scripts/prepare-ai-review.sh {ctx.base_commit} plan {ctx.work_item_id})")


def _v1_plan_fb(ctx) -> tuple[dict, bool]:
    fb = ctx.fb()
    return fb, ctx.bundle_binding_accepts("plan")


def _row_17(ctx):
    fb, current = _v1_plan_fb(ctx)
    if current and fb["fields"].get("status") == "BLOCK":
        return _match("review_blocked", "a current BLOCK verdict, applied by /apply-plan-review for a \"1\" item")
    return None


def _row_18(ctx):
    fb, current = _v1_plan_fb(ctx)
    if current and fb["fields"].get("status") == "REVISE":
        return _match("review_feedback_to_apply", "a current REVISE verdict")
    return None


def _v1_applied_revise(fb: dict, current: bool) -> bool:
    return fb["present"] and not current and fb["fields"].get("status") == "REVISE"


def _row_19(ctx):
    fb, current = _v1_plan_fb(ctx)
    if current and fb["fields"].get("status") == "APPROVE" and ctx.plan_gate()["reachable"]:
        return _match("approval_gate_reachable", "a current APPROVE verdict; the plan approval gate is reachable")
    return None


def _row_20(ctx):
    fb, current = _v1_plan_fb(ctx)
    if _v1_applied_revise(fb, current) and ctx.plan_gate()["reachable"]:
        return _match(
            "revise_applied", "the REVISE was applied (the plan bundle was regenerated); approval resolves "
            "the basis as USER_OVERRIDE", alternatives=[_alt("plan.review.external", satisfied_by="plan_review_verdict")])
    return None


def _row_20a(ctx):
    fb, current = _v1_plan_fb(ctx)
    if (current and fb["fields"].get("status") == "APPROVE") or _v1_applied_revise(fb, current):
        gate = ctx.plan_gate()
        return _gate_blocked(ctx, gate, remedy=f"resolve {gate['cause']}, or obtain another external round",
                             alternatives=[_alt("plan.review.external", satisfied_by="plan_review_verdict")])
    return None


def _row_21(ctx):
    return _match("awaiting_external_review", "no current verdict for the plan bundle",
                  satisfied_by="plan_review_verdict")


_IMPLEMENTING_ENTRY_REMEDIES = {
    "plan_approval_not_current": "obtain a current plan approval",
    "plan_content_drifted": "restore the approved plan-stage bytes, or /request-plan-amendment {id}",
    "plan_approval_commit_unreachable": "restore the history that contains the plan-approval commit",
}


def _row_22(ctx):
    status = ctx.call("implementing_entry_status", lambda: _fn("implementing_entry_status")(
        ctx.repo_root, ctx.work_item, ctx.base_commit)).value
    if status["reachable"]:
        return None
    return _match(status["cause"], f"the IMPLEMENTING entry condition fails: {status['cause']}",
                  _IMPLEMENTING_ENTRY_REMEDIES[status["cause"]].format(id=ctx.work_item_id))


def _registry_completion(ctx):
    return ctx.call("registry_completion_status", lambda: (
        (True, None) if ctx.registry() is None
        else _fn("registry_completion_status")(ctx.work_item, ctx.registry()))).value


def _row_23(ctx):
    if ctx.phase != "IMPLEMENTING":
        return None
    terminal, _outstanding = _registry_completion(ctx)
    if terminal:
        return None
    checkpoint_id = ctx.call("select_next_checkpoint", lambda: _fn("select_next_checkpoint")(
        ctx.work_item, ctx.registry())).value
    return _match("checkpoint_outstanding", f"checkpoint {checkpoint_id} is next",
                  arguments={"checkpoint_id": checkpoint_id})


def _row_24(ctx):
    return _match("self_review_due", "every checkpoint is complete; self-review and the bundle are next")


def _row_25(ctx):
    if ctx.content_current(ctx.I()) and ctx.fb()["fields"].get("status") == "BLOCK":
        return _match("review_blocked", "a current BLOCK verdict: explicit user resolution required")
    return None


def _row_25a(ctx):
    if ctx.phase == "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW" and not ctx.unrecorded_manual_verdict("implementation"):
        return None
    result = ctx.generation_refuses("implementation")
    if result.ok:
        return None
    return _match(
        "bundle_generation_mismatch", str(result.error),
        f"run from the generating worktree; for an excluded-only commit past generation_head, "
        f"/recover-implementation-provenance {ctx.work_item_id}",
        alternatives=[_alt("implementation.recover_provenance")],
    )


def _row_25b(ctx):
    result = ctx.call("verify_implementation_review_bundle", lambda: _fn("verify_implementation_review_bundle")(
        ctx.repo_root, ctx.work_item_id, state=ctx.state))
    if result.ok:
        return None
    return _match("bundle_unverified", str(result.error),
                  f"regenerate the implementation bundle ({ctx.regenerate_implementation_command()})")


def _row_26(ctx):
    return _match("local_review_due", "the implementation bundle awaits its local model review")


def _row_27(ctx):
    if ctx.unrecorded_manual_verdict("implementation"):
        return _match("manual_verdict_unrecorded", "a manual external verdict for the current content is unrecorded")
    return None


def _row_28(ctx):
    return _match("awaiting_external_review", "awaiting the manual external implementation-review verdict",
                  satisfied_by="implementation_review_verdict")


def _row_29(ctx):
    if ctx.technical_gate()["reachable"]:
        return _match("approval_gate_reachable", "the technical approval gate is reachable")
    return None


_RECOVERABLE_CAUSES = ("bundle_generation_mismatch", "protected_path_dirty", "implementation_provenance_stale")


def _row_30(ctx):
    gate = ctx.technical_gate()
    cause = gate["cause"]
    alternatives = []
    remedy = f"resolve {cause}"
    if cause == "bundle_unverified":
        remedy = f"regenerate the implementation bundle ({ctx.regenerate_implementation_command()})"
    elif cause in ("bundle_generation_mismatch", "implementation_provenance_stale"):
        alternatives = [_alt("implementation.recover_provenance")]
        remedy = f"for an excluded-only commit past generation_head, /recover-implementation-provenance {ctx.work_item_id}"
    elif cause in ("review_blocked", "review_block_pinned"):
        alternatives = [_alt("implementation.apply_review")]
        remedy = f"the late-fix re-entry: /apply-implementation-review {ctx.work_item_id}"
    return _gate_blocked(ctx, gate, remedy=remedy, alternatives=alternatives)


def _row_30a(ctx):
    result = ctx.B("implementation")
    if result.ok:
        return None
    return _match("bundle_unverified", str(result.error),
                  f"regenerate the implementation bundle ({ctx.regenerate_implementation_command()})")


def _v1_implementation_fb(ctx) -> tuple[dict, bool]:
    return ctx.fb(), ctx.bundle_binding_accepts("implementation")


def _v1_pinned(ctx) -> bool:
    bundle_id = ctx.B("implementation").value
    return ctx.call("is_technical_review_block_pinned", lambda: _fn("is_technical_review_block_pinned")(
        ctx.work_item, bundle_id)).value


def _row_31(ctx):
    fb, current = _v1_implementation_fb(ctx)
    pinned = _v1_pinned(ctx)
    if current and (fb["fields"].get("status") == "BLOCK" or pinned):
        code = "review_blocked" if fb["fields"].get("status") == "BLOCK" else "review_block_pinned"
        return _match(code, "the implementation apply command pins and remediates a current BLOCK")
    return None


def _row_31a(ctx):
    _v1_implementation_fb(ctx)
    if _v1_pinned(ctx):
        return _match("review_block_pinned", "the bundle carries a pinned BLOCK and no current verdict",
                      "place the BLOCK feedback back, or obtain a new verdict for this bundle",
                      satisfied_by="implementation_review_verdict")
    return None


def _row_32(ctx):
    fb, current = _v1_implementation_fb(ctx)
    if current and fb["fields"].get("status") == "REVISE":
        return _match("review_feedback_to_apply", "a current REVISE verdict")
    return None


def _row_33(ctx):
    fb, current = _v1_implementation_fb(ctx)
    if current and fb["fields"].get("status") == "APPROVE" and ctx.technical_gate()["reachable"]:
        return _match("approval_gate_reachable", "a current APPROVE verdict; the technical approval gate is reachable")
    return None


def _row_34(ctx):
    fb, current = _v1_implementation_fb(ctx)
    if not (current and fb["fields"].get("status") == "APPROVE"):
        return None
    gate = ctx.technical_gate()
    alternatives = [_alt("implementation.recover_provenance")] if gate["cause"] in _RECOVERABLE_CAUSES else []
    remedy = (f"for an excluded-only commit past generation_head, /recover-implementation-provenance {ctx.work_item_id}"
              if alternatives else f"resolve {gate['cause']}")
    return _gate_blocked(ctx, gate, remedy=remedy, alternatives=alternatives)


def _row_35(ctx):
    fb, current = _v1_implementation_fb(ctx)
    alternatives = [_alt("implementation.review.local")]
    if _v1_applied_revise(fb, current) and ctx.technical_gate()["reachable"]:
        alternatives.append(_alt("implementation.approve"))
    return _match("awaiting_external_review", "no current verdict for the implementation bundle",
                  satisfied_by="implementation_review_verdict", alternatives=alternatives)


def _row_35a(ctx):
    fb = ctx.fb()
    if not fb["present"]:
        held = "no verdict" if fb["text"] is None else \
            f"the verdict names work item {fb['fields'].get('work_item')!r}, not {ctx.work_item_id!r},"
        return _match("review_feedback_missing", f"{held} at {fb['path']}",
                      f"place the verdict being applied at {fb['path']}, or run from the "
                      f"worktree that holds it")
    result = ctx.call("assert_apply_review_feedback_binding", lambda: _fn("assert_apply_review_feedback_binding")(
        ctx.repo_root, ctx.work_item, ctx.work_item_id, stage="implementation", feedback_content=fb["text"]))
    if result.ok:
        return None
    name = type(result.error).__name__
    if name in ("MissingRequiredBundleFileError", "ReviewBundleManifestMismatchError"):
        return _match("bundle_unverified", f"{name}: {result.error}",
                      f"run from the worktree that holds the reviewed bundle, or restore its "
                      f".ai-review/{ctx.work_item_id}/ directory")
    return _match("review_feedback_not_current", f"{name}: {result.error}",
                  "place the verdict for the bundle being applied, as its reviewer wrote it, or obtain a new "
                  "verdict for the reviewed bundle")


def _row_36(ctx):
    return _match("review_feedback_to_apply", "the verdict binds to the reviewed bundle")


def _row_37(ctx):
    evidence = ctx.call(
        "discover_current_functional_checklist_evidence",
        lambda: _fn("discover_current_functional_checklist_evidence")(
            ctx.repo_root, ctx.work_item_id, ctx.base_commit, "HEAD", ctx.work_item.get("implementation_revision"),
        )).value
    if evidence is not None and evidence["blob"] == _worktree_blob(ctx.repo_root, workflow_state.FUNCTIONAL_CHECKLIST_PATH):
        return None
    return _match("functional_checklist_due", "no committed checklist evidence matches the current checklist")


def _worktree_blob(repo_root: Path, rel_path: str) -> str | None:
    if not (repo_root / rel_path).is_file():
        return None
    result = subprocess.run(["git", "hash-object", "--", rel_path], cwd=repo_root, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _row_38(ctx):
    feedback_dir = ctx.call("resolve_feedback_dir", lambda: _fn("resolve_feedback_dir")(
        ctx.repo_root, ctx.work_item_id)).value
    if not (ctx.repo_root / feedback_dir / FUNCTIONAL_REVIEW_FILE).is_file():
        return None
    result = ctx.call("assert_functional_review_not_already_consumed",
                      lambda: _fn("assert_functional_review_not_already_consumed")(ctx.repo_root, ctx.work_item_id))
    if not result.ok:
        return None
    return _match("functional_findings_to_apply", "FUNCTIONAL_REVIEW.md holds findings not yet applied")


_FUNCTIONAL_ALTERNATIVES = [_alt("functional.apply_findings"), _alt("functional.review.advisory")]


def _own_registry(ctx) -> _CallResult:
    return ctx.call("resolve_own_registry_completion_status",
                    lambda: _fn("resolve_own_registry_completion_status")(ctx.repo_root, ctx.work_item))


def _row_38a(ctx):
    result = _own_registry(ctx)
    if result.ok:
        return None
    if type(result.error).__name__ == "StalePlanApprovalRegistryReadError":
        return _match(
            "plan_content_drifted", f"{result.error}. 2.6.0 has no in-phase route to amend the plan from "
            f"AWAITING_FUNCTIONAL_REVIEW (/request-plan-amendment refuses at this phase)",
            "restore the approved plan-stage bytes", alternatives=[_alt("functional.review.advisory")])
    return _match("registry_unreadable", str(result.error),
                  f"restore the item's own registry_path file ({ctx.work_item.get('registry_path')})",
                  alternatives=[_alt("functional.review.advisory")])


def _row_38b(ctx):
    terminal, outstanding = _own_registry(ctx).value
    if terminal:
        return None
    return _match(
        "v1_state_not_advanced",
        f"checkpoint {outstanding} is not COMPLETE and no \"1\" command writes checkpoint statuses, so "
        f"/accept-milestone can never pass its pre-flight for this item (defect v2.6.0-003)",
        "none exists in 2.6.0 (defect v2.6.0-003)", alternatives=_FUNCTIONAL_ALTERNATIVES)


def _row_38c(ctx):
    terminal, outstanding = _own_registry(ctx).value
    if terminal:
        return None
    return _match(
        "registry_incomplete",
        f"checkpoint {outstanding} is not COMPLETE at AWAITING_FUNCTIONAL_REVIEW. No 2.6.0 command completes "
        f"a checkpoint from this phase: /milestone-implement cannot start one here, /request-plan-amendment "
        f"refuses at this phase, and /accept-milestone's step 2a refuses. Ordinary flow cannot leave "
        f"IMPLEMENTING with a checkpoint outstanding; the reachable origins are a legacy promotion "
        f"(promote_legacy_work_item) or a hand-constructed state (defect v2.6.0-003)",
        "none exists in 2.6.0 (defect v2.6.0-003)", alternatives=_FUNCTIONAL_ALTERNATIVES)


def _row_39(ctx):
    return _match("functional_review_due", "the registry is terminal; the user's functional review is the gate",
                  alternatives=[_alt("milestone.accept"), *_FUNCTIONAL_ALTERNATIVES])


def _row_40(ctx):
    return _match("milestone_complete", f"{ctx.work_item_id} is MILESTONE_COMPLETE")


_PLAN_EXTERNAL = ("AWAITING_EXTERNAL_PLAN_REVIEW",)
_IMPL_EXTERNAL = ("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",)
_AFR = ("AWAITING_FUNCTIONAL_REVIEW",)
_V1 = ("1",)
_V1_21 = ("1", "2.1")
_V22 = ("2.2",)

#: The catalogue (D-OP-Next): one ordered list; the printed order is the
#: evaluation order, and the first row whose phase, `gv` and condition all
#: match wins.
CATALOGUE = [
    Row("1", None, None, "automatic", "plan.start", _row_1),
    Row("1a", None, None, "blocked", None, _row_1a, unconditional=True),
    Row("2", VOCABULARY_ONLY_PHASES, ALL_VERSIONS, "blocked", None, _row_2, unconditional=True),
    Row("3", ("LEGACY_READY",), ALL_VERSIONS, "blocked", None, _row_3, unconditional=True),
    Row("4", None, None, "blocked", None, _row_4, unconditional=True,
        pairs=[(phase, version) for phase, versions in ILLEGAL_PHASE_VERSIONS.items() for version in versions]),
    Row("5", TWO_STAGE_PLAN_PHASES, TWO_STAGE_VERSIONS, "blocked", None, _row_5,
        remedy_commands=lambda phase, version: ("milestone-plan",) if phase in PLAN_READY_PHASES else ("none_exists",),
        refusing_commands=()),
    Row("6", REVIEW_PHASES | APPLY_PHASES, ALL_VERSIONS, "blocked", None, _row_6,
        remedy_commands=lambda phase, version: (
            ("milestone-plan",) if version in TWO_STAGE_VERSIONS and phase in TWO_STAGE_PLAN_PHASES else ())),
    Row("6a", ("PLANNING", "AMENDING_PLAN", "IMPLEMENTING"), _V1, "blocked", None, _row_6a, unconditional=True,
        remedy_commands=("none_exists",)),
    Row("7", ("PLANNING", "AMENDING_PLAN"), TWO_STAGE_VERSIONS, "automatic", "plan.author", _row_7, unconditional=True),
    Row("7a", ("REVISING_PLAN",), TWO_STAGE_VERSIONS, "blocked", None, _row_7a,
        remedy_commands=("milestone-plan",), refusing_commands=("apply-plan-review",)),
    Row("8", ("REVISING_PLAN",), TWO_STAGE_VERSIONS, "automatic", "plan.apply_review", _row_8),
    Row("8a", ("REVISING_PLAN",), TWO_STAGE_VERSIONS, "blocked", None, _row_8a,
        remedy_commands=("milestone-plan",), refusing_commands=("apply-plan-review",)),
    Row("9", ("REVISING_PLAN",), TWO_STAGE_VERSIONS, "automatic", "plan.author", _row_9, unconditional=True),
    Row("10", PLAN_READY_PHASES, TWO_STAGE_VERSIONS, "blocked", None, _row_10,
        remedy_commands=("milestone-plan",)),
    Row("11", ("AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"), TWO_STAGE_VERSIONS,
        "human_gate", "review.resolve_block", _row_11, remedy_commands=("milestone-plan",)),
    Row("11a", ("AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"), TWO_STAGE_VERSIONS,
        "blocked", None, _row_11a, remedy_commands=("milestone-plan",),
        refusing_commands=("review-plan", "record-manual-plan-review")),
    Row("12", ("AWAITING_LOCAL_PLAN_REVIEW",), TWO_STAGE_VERSIONS, "automatic", "plan.review.local", _row_12,
        unconditional=True),
    Row("13", ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",), TWO_STAGE_VERSIONS, "automatic", "plan.record_external",
        _row_13),
    Row("14", ("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",), TWO_STAGE_VERSIONS, "external_gate", "plan.review.external",
        _row_14, unconditional=True),
    Row("15", ("AWAITING_PLAN_APPROVAL",), TWO_STAGE_VERSIONS, "human_gate", "plan.approve", _row_15),
    Row("16", ("AWAITING_PLAN_APPROVAL",), TWO_STAGE_VERSIONS, "blocked", None, _row_16, unconditional=True,
        remedy_commands=("milestone-plan",)),
    Row("16a", _PLAN_EXTERNAL, _V1, "blocked", None, _row_16a),
    Row("17", _PLAN_EXTERNAL, _V1, "automatic", "plan.apply_review", _row_17),
    Row("18", _PLAN_EXTERNAL, _V1, "automatic", "plan.apply_review", _row_18),
    Row("19", _PLAN_EXTERNAL, _V1, "human_gate", "plan.approve", _row_19),
    Row("20", _PLAN_EXTERNAL, _V1, "human_gate", "plan.approve", _row_20),
    Row("20a", _PLAN_EXTERNAL, _V1, "blocked", None, _row_20a),
    Row("21", _PLAN_EXTERNAL, _V1, "external_gate", "plan.review.external", _row_21, unconditional=True),
    Row("22", ("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"), TWO_STAGE_VERSIONS, "blocked", None, _row_22,
        remedy_commands=("request-plan-amendment",)),
    Row("23", ("IMPLEMENTING",), TWO_STAGE_VERSIONS, "automatic", "implementation.checkpoint", _row_23),
    Row("24", ("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"), TWO_STAGE_VERSIONS, "automatic",
        "implementation.self_review", _row_24, unconditional=True),
    Row("25", IMPLEMENTATION_REVIEW_PHASES_2_2, _V22, "human_gate", "review.resolve_block", _row_25),
    Row("25a", IMPLEMENTATION_REVIEW_PHASES_2_2, _V22, "blocked", None, _row_25a,
        remedy_commands=("recover-implementation-provenance",)),
    Row("25b", IMPLEMENTATION_REVIEW_PHASES_2_2, _V22, "blocked", None, _row_25b,
        refusing_commands=("review-implementation", "record-manual-implementation-review")),
    Row("26", ("AWAITING_LOCAL_IMPLEMENTATION_REVIEW",), _V22, "automatic", "implementation.review.local", _row_26,
        unconditional=True),
    Row("27", ("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",), _V22, "automatic", "implementation.record_external",
        _row_27),
    Row("28", ("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",), _V22, "external_gate",
        "implementation.review.external", _row_28, unconditional=True),
    Row("29", _IMPL_EXTERNAL, _V22, "human_gate", "implementation.approve", _row_29),
    Row("30", _IMPL_EXTERNAL, _V22, "blocked", None, _row_30, unconditional=True,
        remedy_commands=("recover-implementation-provenance", "apply-implementation-review")),
    Row("30a", _IMPL_EXTERNAL, _V1_21, "blocked", None, _row_30a,
        refusing_commands=("apply-implementation-review", "approve-review")),
    Row("31", _IMPL_EXTERNAL, _V1_21, "automatic", "implementation.apply_review", _row_31),
    Row("31a", _IMPL_EXTERNAL, _V1_21, "external_gate", "implementation.review.external", _row_31a),
    Row("32", _IMPL_EXTERNAL, _V1_21, "automatic", "implementation.apply_review", _row_32),
    Row("33", _IMPL_EXTERNAL, _V1_21, "human_gate", "implementation.approve", _row_33),
    Row("34", _IMPL_EXTERNAL, _V1_21, "blocked", None, _row_34,
        remedy_commands=("recover-implementation-provenance",)),
    Row("35", _IMPL_EXTERNAL, _V1_21, "external_gate", "implementation.review.external", _row_35, unconditional=True,
        remedy_commands=("review-implementation", "approve-review")),
    Row("35a", ("APPLYING_REVIEW_FEEDBACK",), ALL_VERSIONS, "blocked", None, _row_35a,
        refusing_commands=("apply-implementation-review",)),
    Row("36", ("APPLYING_REVIEW_FEEDBACK",), ALL_VERSIONS, "automatic", "implementation.apply_review", _row_36,
        unconditional=True),
    Row("37", _AFR, ALL_VERSIONS, "automatic", "functional.prepare", _row_37),
    Row("38", _AFR, ALL_VERSIONS, "automatic", "functional.apply_findings", _row_38),
    Row("38a", _AFR, ALL_VERSIONS, "blocked", None, _row_38a,
        remedy_commands=("review-functional",), refusing_commands=("request-plan-amendment", "accept-milestone")),
    Row("38b", _AFR, _V1, "blocked", None, _row_38b,
        remedy_commands=("none_exists", "apply-functional-review", "review-functional"),
        refusing_commands=("accept-milestone",)),
    Row("38c", _AFR, TWO_STAGE_VERSIONS, "blocked", None, _row_38c,
        remedy_commands=("none_exists", "apply-functional-review", "review-functional"),
        refusing_commands=("milestone-implement", "request-plan-amendment", "accept-milestone")),
    Row("39", _AFR, ALL_VERSIONS, "human_gate", "functional.review", _row_39, unconditional=True,
        remedy_commands=("accept-milestone", "apply-functional-review", "review-functional")),
    Row("40", ("MILESTONE_COMPLETE",), ALL_VERSIONS, "complete", None, _row_40, unconditional=True),
]

ROW_IDS = tuple(row.row_id for row in CATALOGUE)
ROWS_BY_ID = {row.row_id: row for row in CATALOGUE}


# ---------------------------------------------------------------------------
# The legal-edge table (D-OP-Reconcile)
# ---------------------------------------------------------------------------


def _edge(source: str | None, target: str | None, versions) -> dict:
    return {"from": source, "to": target, "versions": sorted(versions)}


def _unchanged(phases, versions) -> list[dict]:
    return [_edge(phase, phase, versions) for phase in phases]


_PROGRESS_GATE_NONE = ["progress", "gate_reached", "no_progress"]

#: For every automatic action id: its legal edges (`from_phase -> to_phase`,
#: by `gv`; `None` is "no item"), its same-phase proof of progress, and its
#: `allowed_results`, which `next-action` copies into `action.allowed_results`.
EDGES = {
    "plan.start": {
        "edges": [_edge(None, "PLANNING", TWO_STAGE_VERSIONS),
                  _edge(None, "AWAITING_LOCAL_PLAN_REVIEW", TWO_STAGE_VERSIONS),
                  _edge(None, None, TWO_STAGE_VERSIONS)],
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
    "plan.author": {
        "edges": [_edge(phase, "AWAITING_LOCAL_PLAN_REVIEW", TWO_STAGE_VERSIONS)
                  for phase in ("PLANNING", "AMENDING_PLAN", "REVISING_PLAN")]
        + [_edge(phase, "AWAITING_EXTERNAL_PLAN_REVIEW", _V1) for phase in ("PLANNING", "AMENDING_PLAN")]
        + _unchanged(("PLANNING", "AMENDING_PLAN", "REVISING_PLAN"), TWO_STAGE_VERSIONS)
        + _unchanged(("PLANNING", "AMENDING_PLAN"), _V1),
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
    "plan.apply_review": {
        "edges": [_edge("REVISING_PLAN", "AWAITING_LOCAL_PLAN_REVIEW", TWO_STAGE_VERSIONS)]
        + _unchanged(("REVISING_PLAN",), TWO_STAGE_VERSIONS) + _unchanged(_PLAN_EXTERNAL, _V1),
        "proof": "plan_revision_advanced", "allowed_results": _PROGRESS_GATE_NONE,
    },
    "plan.review.local": {
        "edges": [_edge("AWAITING_LOCAL_PLAN_REVIEW", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", TWO_STAGE_VERSIONS),
                  _edge("AWAITING_LOCAL_PLAN_REVIEW", "REVISING_PLAN", TWO_STAGE_VERSIONS)]
        + _unchanged(("AWAITING_LOCAL_PLAN_REVIEW",), TWO_STAGE_VERSIONS),
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
    "plan.record_external": {
        "edges": [_edge("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "AWAITING_PLAN_APPROVAL", TWO_STAGE_VERSIONS),
                  _edge("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "REVISING_PLAN", TWO_STAGE_VERSIONS)]
        + _unchanged(("AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",), TWO_STAGE_VERSIONS),
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
    "implementation.checkpoint": {
        "edges": _unchanged(("IMPLEMENTING",), TWO_STAGE_VERSIONS)
        + [_edge("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION", TWO_STAGE_VERSIONS)],
        "proof": "checkpoint_completed", "allowed_results": ["progress", "no_progress"],
    },
    "implementation.self_review": {
        "edges": [_edge("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION", TWO_STAGE_VERSIONS)]
        + [_edge(phase, "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", _V22)
           for phase in ("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION")]
        + [_edge(phase, "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", ("2.1",))
           for phase in ("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION")]
        + _unchanged(("IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"), TWO_STAGE_VERSIONS),
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
    "implementation.review.local": {
        "edges": [_edge("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", _V22),
                  _edge("AWAITING_LOCAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK", _V22)]
        + _unchanged(("AWAITING_LOCAL_IMPLEMENTATION_REVIEW",), _V22),
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
    "implementation.record_external": {
        "edges": [_edge("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", _V22),
                  _edge("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK", _V22)]
        + _unchanged(("AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",), _V22),
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
    "implementation.apply_review": {
        "edges": _unchanged(("APPLYING_REVIEW_FEEDBACK",), ALL_VERSIONS)
        + [_edge("AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", "APPLYING_REVIEW_FEEDBACK", _V1_21),
           _edge("APPLYING_REVIEW_FEEDBACK", "AWAITING_LOCAL_IMPLEMENTATION_REVIEW", _V22),
           _edge("APPLYING_REVIEW_FEEDBACK", "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW", _V1_21)]
        + _unchanged(_IMPL_EXTERNAL, _V1_21),
        "proof": "implementation_revision_advanced", "allowed_results": _PROGRESS_GATE_NONE,
    },
    "functional.prepare": {
        "edges": _unchanged(_AFR, ALL_VERSIONS),
        "proof": None, "allowed_results": ["gate_reached", "no_progress"],
    },
    "functional.apply_findings": {
        "edges": _unchanged(_AFR, ALL_VERSIONS)
        + [_edge("AWAITING_FUNCTIONAL_REVIEW", workflow_state.bundle_generation_target_phase("post-fix", version),
                 (version,)) for version in sorted(ALL_VERSIONS)],
        "proof": None, "allowed_results": _PROGRESS_GATE_NONE,
    },
}

#: The automatic action ids, and the `(action id, phase, gv)` triples some
#: automatic row can emit (a decision outside them is not a `next-action`
#: result).
AUTOMATIC_ACTION_IDS = tuple(sorted(EDGES))
AUTOMATIC_EMISSIONS = frozenset(
    (row.action_id, phase, version)
    for row in CATALOGUE if row.disposition == "automatic" and not row.no_item
    for phase, version in row.pairs
)


def edge_is_legal(action_id: str, source: str | None, target: str | None, version: str | None) -> bool:
    for edge in EDGES[action_id]["edges"]:
        if edge["from"] != source or edge["to"] != target:
            continue
        if target is None or version in edge["versions"]:
            return True
    return False


# ---------------------------------------------------------------------------
# next-action (D-OP-Next)
# ---------------------------------------------------------------------------


def render_action(action_id: str, work_item_id: str | None, *, arguments: dict | None = None) -> dict:
    spec = ACTIONS[action_id]
    rendered_arguments = {"work_item_id": work_item_id} if work_item_id is not None else {}
    rendered_arguments.update(arguments or {})
    invocation = spec["invocation"]
    return {
        "id": action_id,
        "arguments": rendered_arguments,
        "invocation": invocation.format(id=work_item_id) if invocation is not None else None,
        "worker": {
            "role": spec["role"], "fresh_session": spec["fresh_session"],
            "independent_of": list(spec["independent_of"]), "user_only": spec["user_only"],
        },
        "allowed_results": list(EDGES[action_id]["allowed_results"]) if action_id in EDGES else [],
    }


def _snapshot(ctx: _Context) -> dict:
    work_item = ctx.work_item
    registry = ctx.memo_value("registry_completion_status")
    publication = ctx.memo_value(_P)

    def _status(field: str):
        record = work_item.get(field)
        return record.get("status") if isinstance(record, dict) else None

    return {
        "phase": ctx.phase,
        "governing_workflow_version": ctx.gv,
        "work_item_type": work_item.get("work_item_type"),
        "plan_revision": work_item.get("plan_revision"),
        "implementation_revision": work_item.get("implementation_revision"),
        "current_checkpoint_id": work_item.get("current_checkpoint_id"),
        "next_checkpoint_id": ctx.memo_value("select_next_checkpoint"),
        "registry_complete": registry[0] if registry is not None else None,
        "plan_approval": _status("plan_approval"),
        "technical_approval": _status("technical_approval"),
        "plan_review_publication_status": publication["status"] if publication is not None else None,
    }


def _decision(ctx: _Context, row: Row, match: dict) -> dict:
    action_id = match["action_id"] or row.action_id
    alternatives = []
    for alternative_id, extra in match["alternatives"]:
        alternative = render_action(alternative_id, ctx.work_item_id)
        alternative.update(extra)
        alternatives.append(alternative)
    result = {
        "row": row.row_id,
        "disposition": row.disposition,
        "action": render_action(action_id, ctx.work_item_id, arguments=match["arguments"]) if action_id else None,
        "satisfied_by": match["satisfied_by"],
        "alternatives": alternatives,
        "reason": match["reason"],
    }
    return result


def _condition_refused(row: Row, refused: _ConditionRefused) -> dict:
    exc = refused.exc
    return {
        "row": row.row_id,
        "disposition": "blocked",
        "action": None,
        "satisfied_by": None,
        "alternatives": [],
        "reason": {
            "code": "condition_refused",
            "text": f"{type(exc).__name__}: {exc}",
            "remedy": None,
            "native": native_of(exc),
        },
    }


def evaluate_catalogue(repo_root: Path, state: dict, work_item_id: str | None) -> tuple[dict, _Context]:
    """The first catalogue row whose phase, `gv` and condition match, as a
    decision (without `basis`/`snapshot`), and the evaluation context."""
    ctx = _Context(repo_root, state, work_item_id)
    for row in CATALOGUE:
        if row.no_item != (work_item_id is None):
            continue
        if not row.no_item and not row.covers(ctx.phase, ctx.gv):
            continue
        ctx.row_id = row.row_id
        try:
            match = row.predicate(ctx)
        except _ConditionRefused as refused:
            return _condition_refused(row, refused), ctx
        if match is not None:
            return _decision(ctx, row, match), ctx
    raise AssertionError(f"no catalogue row matched {work_item_id!r} at ({ctx.phase!r}, {ctx.gv!r})")


def decide(repo_root: Path, state: dict, work_item_id: str | None = None, *,
           expect_state_identity: str | None = None) -> dict:
    """`next-action`'s result for `work_item_id` (or the active item; with
    neither, the no-item rows 1 and 1a)."""
    if work_item_id is None:
        work_item_id = state.get("active_work_item_id")
    if work_item_id is None:
        if expect_state_identity is not None:
            raise ProtocolError(
                "invalid_request", "--expect-state-identity names a work item's state, and no work item is "
                "named or active")
        decision, _ctx = evaluate_catalogue(repo_root, state, None)
        return {"snapshot": {"work_item_ids": sorted(state.get("work_items") or {})}, **decision}
    work_item = work_item_of(state, work_item_id)
    current_identity = state_identity(work_item_id, state)
    if expect_state_identity is not None and expect_state_identity != current_identity:
        raise ProtocolError(
            "stale_decision",
            f"{work_item_id}'s state identity is {current_identity}, not the decision's "
            f"{expect_state_identity}: re-decide with next-action")
    version = work_item.get("governing_workflow_version")
    if version not in SUPPORTED_GOVERNING_VERSIONS:
        raise ProtocolError(
            "not_applicable",
            f"{work_item_id}'s governing_workflow_version {version!r} is not one of "
            f"{sorted(SUPPORTED_GOVERNING_VERSIONS)}")
    phase = work_item.get("phase")
    if phase not in workflow_state.KNOWN_PHASES:
        raise ProtocolError("state_invalid", f"{work_item_id}'s phase {phase!r} is not a known phase")
    decision, ctx = evaluate_catalogue(repo_root, state, work_item_id)
    return {"basis": basis(repo_root, state, work_item_id), "snapshot": _snapshot(ctx), **decision}


def op_next_action(repo_root: Path, args: argparse.Namespace) -> dict:
    state = load_valid_state(repo_root)
    return decide(repo_root, state, args.work_item, expect_state_identity=args.expect_state_identity)


# ---------------------------------------------------------------------------
# reconcile (D-OP-Reconcile)
# ---------------------------------------------------------------------------

RECONCILE_CLASSES = ("progress", "no_progress", "gate_reached", "invalid")

#: The ledger stage a review/record action records when it moves the phase.
_RECORDED_STAGES = {
    "plan.review.local": workflow_state.LOCAL_MODEL_PLAN_REVIEW,
    "plan.record_external": workflow_state.MANUAL_EXTERNAL_PLAN_REVIEW,
    "implementation.review.local": workflow_state.LOCAL_MODEL_IMPLEMENTATION_REVIEW,
    "implementation.record_external": workflow_state.MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW,
}

_HEX64 = frozenset("0123456789abcdef")


def _is_hex(value, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= _HEX64


def _invalid_request(message: str) -> ProtocolError:
    return ProtocolError("invalid_request", f"the decision is not a well-formed automatic next-action result: {message}")


def read_decision(path: str) -> dict:
    try:
        decision = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ProtocolError("invalid_request", f"cannot read the decision file {path!r}: {exc}") from exc
    if isinstance(decision, dict) and decision.get("operation") == "next-action" and "result" in decision:
        decision = decision["result"]
    if not isinstance(decision, dict):
        raise _invalid_request("it is not a JSON object")
    return decision


def validate_decision(decision: dict) -> str | None:
    """Refuse (`invalid_request`) anything that is not a well-formed,
    automatic `next-action` result: an unknown or non-automatic action, a
    disposition other than `automatic`, an action whose rendering differs
    from the catalogue's, or a basis the action could not have been decided
    from. Returns the decision's work item id (`None` for `plan.start`)."""
    if decision.get("disposition") != "automatic":
        raise ProtocolError(
            "invalid_request",
            f"reconcile accepts an automatic decision only, not {decision.get('disposition')!r}: a gate is "
            f"resolved outside the run, and the orchestrator then calls next-action again")
    action = decision.get("action")
    if not isinstance(action, dict) or action.get("id") not in EDGES:
        raise _invalid_request(f"its action {action.get('id') if isinstance(action, dict) else action!r} is not an automatic action")
    action_id = action["id"]
    snapshot = decision.get("snapshot")
    if not isinstance(snapshot, dict):
        raise _invalid_request("it carries no snapshot")
    row_id = decision.get("row")
    if action_id == "plan.start":
        if decision.get("basis") is not None:
            raise _invalid_request("a plan.start decision carries no basis")
        ids = snapshot.get("work_item_ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or ids != sorted(set(ids)):
            raise _invalid_request("a plan.start snapshot names the sorted work_item_ids at decision time")
        if action != render_action("plan.start", None):
            raise _invalid_request("its plan.start action differs from the catalogue's")
        if row_id is not None and row_id != "1":
            raise _invalid_request(f"plan.start is emitted by row 1 only, not row {row_id!r}")
        return None
    decision_basis = decision.get("basis")
    if not isinstance(decision_basis, dict):
        raise _invalid_request("it carries no basis")
    work_item_id = decision_basis.get("work_item_id")
    phase = decision_basis.get("phase")
    if not isinstance(work_item_id, str) or not _is_hex(decision_basis.get("state_identity"), 64):
        raise _invalid_request("its basis names no work item or state identity")
    if not isinstance(decision_basis.get("checkpoints"), dict):
        raise _invalid_request("its basis carries no checkpoints")
    if phase not in workflow_state.KNOWN_PHASES or snapshot.get("phase") != phase:
        raise _invalid_request("its basis and snapshot do not name the same known phase")
    version = snapshot.get("governing_workflow_version")
    if (action_id, phase, version) not in AUTOMATIC_EMISSIONS:
        raise _invalid_request(f"no automatic row emits {action_id} at ({phase}, {version!r})")
    if row_id is not None:
        row = ROWS_BY_ID.get(row_id)
        if row is None or row.action_id != action_id or row.disposition != "automatic" or not row.covers(phase, version):
            raise _invalid_request(f"row {row_id!r} does not emit {action_id} at ({phase}, {version!r})")
    expected = render_action(action_id, work_item_id, arguments={
        key: value for key, value in (action.get("arguments") or {}).items() if key != "work_item_id"})
    if action != expected:
        raise _invalid_request(f"its {action_id} action differs from the catalogue's rendering for {work_item_id}")
    return work_item_id


def _same_phase_proof(proof: str | None, decision: dict, work_item: dict, completed: list[str]) -> bool:
    snapshot = decision["snapshot"]
    if proof == "plan_revision_advanced":
        before = snapshot.get("plan_revision")
        after = work_item.get("plan_revision")
        return work_item.get("governing_workflow_version") == "1" and isinstance(after, int) and \
            isinstance(before, int) and after > before
    if proof == "checkpoint_completed":
        return bool(completed) and work_item.get("last_completed_checkpoint_id") in completed
    if proof == "implementation_revision_advanced":
        before = snapshot.get("implementation_revision") or 0
        after = work_item.get("implementation_revision") or 0
        return after > before
    return False


def _reconcile_result(cls: str, *, source, target, evidence, invalid_reasons, result_basis, next_result) -> dict:
    return {"class": cls, "from": source, "to": target, "evidence": evidence,
            "invalid_reasons": invalid_reasons, "basis": result_basis, "next": next_result}


def reconcile(repo_root: Path, decision: dict, *, work_item_id: str | None = None) -> dict:
    decided_item = validate_decision(decision)
    if work_item_id is not None and decided_item is not None and work_item_id != decided_item:
        raise ProtocolError(
            "invalid_request", f"the decision is for {decided_item!r}, not --work-item {work_item_id!r}")
    action_id = decision["action"]["id"]
    state, _config = read_state_and_config(repo_root)
    empty_evidence = {"completed_checkpoints": [], "started_checkpoints": [], "recorded_stage": None}
    try:
        workflow_state.validate_state(state)
    except Exception as exc:
        if not is_workflow_exception(exc):
            raise
        return _reconcile_result(
            "invalid", source=None, target=None, evidence=empty_evidence,
            invalid_reasons=[{"code": "state_invalid", "text": f"{type(exc).__name__}: {exc}"}],
            result_basis=None, next_result=None)
    work_items = state.get("work_items") or {}

    if decided_item is None:  # plan.start
        new_ids = sorted(set(work_items) - set(decision["snapshot"]["work_item_ids"]))
        active = state.get("active_work_item_id")
        if not new_ids and active is None:
            next_result = decide(repo_root, state)
            return _reconcile_result(
                "no_progress", source=None, target=None, evidence=empty_evidence, invalid_reasons=[],
                result_basis=None, next_result=next_result)
        if len(new_ids) != 1 or active != new_ids[0]:
            return _reconcile_result(
                "invalid", source=None, target=None, evidence=empty_evidence,
                invalid_reasons=[{"code": "plan_start_item_ambiguous",
                                  "text": f"new work items {new_ids}, active_work_item_id {active!r}: exactly one "
                                          f"new item, the active one, is the only legal outcome"}],
                result_basis=None, next_result=None)
        item_id = new_ids[0]
        source = None
        source_phase = None
        before_checkpoints: dict = {}
    else:
        if decided_item not in work_items:
            raise ProtocolError("invalid_request", f"the decision is for {decided_item!r}, which is not a work item here")
        item_id = decided_item
        source = {"phase": decision["basis"]["phase"], "state_identity": decision["basis"]["state_identity"]}
        source_phase = source["phase"]
        before_checkpoints = decision["basis"]["checkpoints"]

    work_item = work_items[item_id]
    target_phase = work_item.get("phase")
    version = work_item.get("governing_workflow_version")
    target = {"phase": target_phase, "state_identity": state_identity(item_id, state)}
    checkpoints = work_item.get("checkpoints") or {}
    completed = sorted(cid for cid, entry in checkpoints.items()
                       if entry.get("status") == "COMPLETE" and before_checkpoints.get(cid) != "COMPLETE")
    started = sorted(cid for cid, entry in checkpoints.items()
                     if entry.get("status") == "IN_PROGRESS" and before_checkpoints.get(cid) != "IN_PROGRESS")
    recorded_stage = _RECORDED_STAGES.get(action_id) if target_phase != source_phase else None
    evidence = {"completed_checkpoints": completed, "started_checkpoints": started, "recorded_stage": recorded_stage}

    invalid_reasons: list[dict] = []
    if not edge_is_legal(action_id, source_phase, target_phase, version):
        invalid_reasons.append({"code": "illegal_edge",
                                "text": f"{action_id} has no legal edge {source_phase} -> {target_phase} at {version!r}"})
    if completed:
        try:
            prove_checkpoint_completions(work_item, repo_root, work_item["base_commit"], only=completed)
        except Exception as exc:
            if not _is_check_refusal(exc):
                raise
            invalid_reasons.append({"code": "checkpoint_completion_unproven", "text": _refusal_detail(exc)})
    if target_phase in REVIEW_PHASES:
        try:
            workflow_fingerprint.assert_bundle_not_rejected(repo_root, item_id)
        except workflow_fingerprint.BundleRejectedError as exc:
            invalid_reasons.append({"code": "bundle_rejected", "text": str(exc)})
    if version in TWO_STAGE_VERSIONS and target_phase in PLAN_READY_PHASES:
        try:
            status = workflow_state.plan_review_publication_status(repo_root, state, item_id)["status"]
        except Exception as exc:
            if not is_workflow_exception(exc):
                raise
            status = f"{type(exc).__name__}: {exc}"
        if status != workflow_state.PLAN_REVIEW_STATUS_BOUND:
            invalid_reasons.append({"code": "plan_review_not_bound",
                                    "text": f"{target_phase} with plan_review_publication_status {status}"})

    result_basis = basis(repo_root, state, item_id)
    next_result = decide(repo_root, state, item_id)
    if invalid_reasons:
        cls = "invalid"
    elif next_result["disposition"] in ("human_gate", "external_gate"):
        cls = "gate_reached"
    elif target_phase != source_phase or _same_phase_proof(EDGES[action_id]["proof"], decision, work_item, completed):
        cls = "progress"
    else:
        cls = "no_progress"
    if cls != "invalid" and cls not in EDGES[action_id]["allowed_results"]:
        invalid_reasons.append({"code": "result_not_allowed",
                                "text": f"{cls} is not among {action_id}'s allowed_results"})
        cls = "invalid"
    return _reconcile_result(cls, source=source, target=target, evidence=evidence, invalid_reasons=invalid_reasons,
                             result_basis=result_basis, next_result=next_result)


def op_reconcile(repo_root: Path, args: argparse.Namespace) -> dict:
    return reconcile(repo_root, read_decision(args.decision), work_item_id=args.work_item)


# ---------------------------------------------------------------------------
# record-external-result (D-OP-External)
# ---------------------------------------------------------------------------

#: The ingest's refusals that mean "no ingest row accepts this verdict
#: here" -- no row for the item's governing version and phase, or the
#: stage already recorded for the current content -- reported as
#: `not_applicable`, never `refused`.
_NOT_APPLICABLE_INGEST_REFUSALS = (
    workflow_state.WrongPhaseForPlanReviewStageError,
    workflow_state.WrongPhaseForImplementationReviewStageError,
    workflow_state.WrongGoverningVersionForPlanReviewStageError,
    workflow_state.WrongGoverningVersionForImplementationReviewStageError,
    workflow_state.DuplicateManualStageIngestionError,
    workflow_state.DuplicateManualImplementationStageIngestionError,
)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_result_kind(kind: str) -> None:
    if kind in RESERVED_RESULT_KINDS:
        raise ProtocolError("unsupported_result_kind", f"{kind!r} is reserved for a later protocol version")
    if kind not in EXTERNAL_RESULT_KIND_STAGES:
        raise ProtocolError(
            "invalid_request", f"unknown result kind {kind!r}; the v1 kinds are {list(EXTERNAL_RESULT_KINDS)}")


def record_external_result(repo_root: Path, work_item_id: str, kind: str, verdict_text: str) -> dict:
    """Ingest an external result of `kind` for `work_item_id`. A reserved
    kind is `unsupported_result_kind`, any other unknown kind
    `invalid_request`. The verdict kinds call
    `workflow_state.ingest_manual_review_verdict`, the ingest the
    record-manual commands call, which selects the row, runs its guards and
    writes the feedback file and the state; the orchestrator never reads or
    writes a feedback path itself."""
    _check_result_kind(kind)
    work_item = work_item_of(load_valid_state(repo_root), work_item_id)
    version = work_item.get("governing_workflow_version")
    if version not in SUPPORTED_GOVERNING_VERSIONS:
        raise ProtocolError(
            "not_applicable",
            f"{work_item_id}'s governing_workflow_version {version!r} is not one of "
            f"{sorted(SUPPORTED_GOVERNING_VERSIONS)}")
    try:
        outcome = workflow_state.ingest_manual_review_verdict(
            repo_root, work_item_id, stage=EXTERNAL_RESULT_KIND_STAGES[kind], verdict_text=verdict_text,
            now=_utc_now())
    except _NOT_APPLICABLE_INGEST_REFUSALS as exc:
        raise ProtocolError("not_applicable", str(exc), exc) from exc
    state, _config = read_state_and_config(repo_root)
    result = {key: outcome[key] for key in ("stage", "verdict", "review_content_id", "round", "bundle_id", "advisory")}
    result["basis"] = basis(repo_root, state, work_item_id)
    return result


def op_record_external_result(repo_root: Path, args: argparse.Namespace) -> dict:
    _check_result_kind(args.kind)
    try:
        verdict_text = Path(args.input).read_text()
    except (OSError, UnicodeDecodeError) as exc:
        raise ProtocolError("invalid_request", f"cannot read the input file {args.input!r}: {exc}") from exc
    return record_external_result(repo_root, args.work_item, args.kind, verdict_text)


# ---------------------------------------------------------------------------
# The CLI and the envelope (D-OP-Surface)
# ---------------------------------------------------------------------------


OPERATIONS = {
    "describe": op_describe,
    "verify": op_verify,
    "resolve-artifact": op_resolve_artifact,
    "next-action": op_next_action,
    "reconcile": op_reconcile,
    "record-external-result": op_record_external_result,
}


def _global_parser() -> _Parser:
    parser = _Parser(prog="workflow_protocol.py")
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--protocol-major", type=int, default=None)
    return parser


def _parser() -> _Parser:
    parser = _global_parser()
    sub = parser.add_subparsers(dest="operation", required=True, parser_class=_Parser)
    sub.add_parser("describe")
    sub.add_parser("verify")
    p = sub.add_parser("resolve-artifact")
    p.add_argument("--work-item", required=True)
    p.add_argument("--kind", required=True)
    p = sub.add_parser("next-action")
    p.add_argument("--work-item", default=None)
    p.add_argument("--expect-state-identity", default=None)
    p = sub.add_parser("reconcile")
    p.add_argument("--decision", required=True)
    p.add_argument("--work-item", default=None)
    p = sub.add_parser("record-external-result")
    p.add_argument("--work-item", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--input", required=True)
    return parser


def envelope(operation: str | None, *, result: dict | None = None,
             error: ProtocolError | None = None) -> dict:
    body = {
        "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
        "workflow_release": WORKFLOW_RELEASE,
        "operation": operation,
        "ok": error is None,
    }
    if error is None:
        body["result"] = result
    else:
        body["error"] = {
            "code": error.code,
            "message": error.message,
            "retryable": ERROR_CODES[error.code],
            "native": native_of(error.native),
        }
    return body


def exit_code_for(code: str) -> int:
    if code == "invalid_request":
        return EXIT_INVALID_REQUEST
    if code == "internal_error":
        return EXIT_INTERNAL
    return EXIT_REFUSED


def _operation_hint(argv: list[str]) -> str | None:
    for token in argv:
        if token in OPERATIONS:
            return token
    return None


def run(argv: list[str]) -> tuple[dict, int]:
    """Parse `argv`, run the operation and return `(envelope, exit code)`.
    Never raises: every outcome is an envelope."""
    operation = _operation_hint(argv)
    try:
        try:
            known, _rest = _global_parser().parse_known_args(argv)
        except _ArgumentError as exc:
            raise ProtocolError("invalid_request", str(exc)) from exc
        if known.protocol_major is not None and known.protocol_major not in SUPPORTED_PROTOCOL_MAJORS:
            raise ProtocolError(
                "unsupported_protocol",
                f"protocol major {known.protocol_major} is not supported; "
                f"supported: {sorted(SUPPORTED_PROTOCOL_MAJORS)}")
        try:
            args = _parser().parse_args(argv)
        except _ArgumentError as exc:
            raise ProtocolError("invalid_request", str(exc)) from exc
        operation = args.operation
        repo_root = (args.repo_root or Path.cwd()).resolve()
        if not repo_root.is_dir():
            raise ProtocolError("invalid_request", f"--repo-root {repo_root} is not a directory")
        try:
            result = OPERATIONS[operation](repo_root, args)
        except ProtocolError:
            raise
        except Exception as exc:
            if not is_workflow_exception(exc):
                raise
            raise ProtocolError(code_for_workflow_exception(exc), str(exc), exc) from exc
        return envelope(operation, result=result), EXIT_OK
    except ProtocolError as exc:
        return envelope(operation, error=exc), exit_code_for(exc.code)
    except Exception as exc:  # an unexpected exception is a defect
        traceback.print_exc(file=sys.stderr)
        error = ProtocolError("internal_error", f"{type(exc).__name__}: {exc}")
        return envelope(operation, error=error), EXIT_INTERNAL


def main(argv: list[str] | None = None) -> int:
    body, code = run(sys.argv[1:] if argv is None else argv)
    sys.stdout.write(json.dumps(body, sort_keys=True, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
