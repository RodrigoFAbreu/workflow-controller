#!/usr/bin/env python3
"""Workflow v2.1 state-file schema and validator core (WF1a), extended
with work-item routing and the registry/mapping generator (WF1b), D2/
D-States' approval-record schema and gate logic (WF4a-ii), and the
approval/checkpoint-completion Git-lifecycle mechanics (WF4a-iii).

Implements the `docs/ai-workflow/WORKFLOW_V2_PLAN.md` D3 schema for
`docs/ai-workflow/WORKFLOW_CONFIG.json` (repository-level default,
pre-activation fail-safe, `supported_versions` enforcement),
`docs/ai-workflow/WORKFLOW_STATE.json` (multi-item work-item map,
`checkpoints` map as the sole writable checkpoint-status record), the
local-only `.ai-review/runtime/WORKTREE_IDENTITY.json` schema (D3;
its writer is WF2, not built here), and D-Commit-Provenance's exact,
scoped, ancestry-limited `Workflow-Checkpoint`/`Workflow-Work-Item`
trailer search with its duplicate-trailer tie-break.

WF1b adds D1's work-item routing (create-or-resume semantics for
`/milestone-plan`, completion/reset), and D-Registry/D4b's registry/
mapping JSON generator (the sole writer either file should ever have,
running the coverage/topological-order checks at generation time rather
than leaving them to be discovered at review time).

WF4a-ii adds D2's unified `plan_approval`/`technical_approval` record
schema, the non-circular gate-reachability check for both new D-States
gates (`AWAITING_PLAN_APPROVAL`/`AWAITING_TECHNICAL_APPROVAL`), the
EXTERNAL_APPROVE/USER_OVERRIDE basis decision, and the mechanism-
independent user-only guard's second control (literal, specific
`user_confirmation` text).

WF4a-iii adds D-Commit-Provenance's trailer search generalized beyond
checkpoint trailers to the `Workflow-Plan-Approval`/
`Workflow-Technical-Approval` trailers `/approve-review` creates
(`discover_approval_commits`, sharing the same first-parent-ancestor
tie-break and genuine-ambiguity recovery as `discover_checkpoint_commits`
via one private helper), D-States' "Recomputation rule, stated per
stage" (`approval_review_content_id`/`approval_is_current`, resolving
`OPUS-R6-003`'s self-invalidation defect), the full `IMPLEMENTING` entry
condition D-Approval-Commits names (`implementing_entry_reachable`:
ancestry to the discovered plan-approval commit, plus freshness), the
"no protected path is dirty" gate condition's actual Git-derived boolean
(`any_protected_path_dirty`, consumed by WF4a-ii's own
`technical_approval_gate_reachable`), and WFR-06's post-approval-commit
manifest-match verification (`verify_post_approval_manifest_match`).
WF4a-iv adds the two-stage local-then-manual-external plan-review
protocol's own ledger writers (D-Plan-Review-Stages): `record_local_plan_review`/
`record_manual_plan_review` implement the six-row verdict/state transition
table (`/review-plan`'s and `/record-manual-plan-review`'s sole state
write set, resolving `GPT-R12-001/002/003`), each guarded by its own
precondition validator (`WrongGoverningVersionForPlanReviewStageError`,
`WrongPhaseForPlanReviewStageError`, `StaleReviewContentIdError` (hard),
`WrongReviewerRoleError`, `MissingLocalApprovalForManualStageError`,
`DuplicateManualStageIngestionError`); `check_manual_stage_bundle_id_advisory`
implements the manual stage's advisory-only (never blocking) `bundle_id`
mismatch warning (`OPUS-R14-005`); `transition_to_awaiting_local_plan_review`
implements `/apply-plan-review`'s `"2.1"`-only revised exit step (D-Plan-
Review-Stages, resolves `GPT-R11-003`/`-007`) -- the sole writer that
re-enters `AWAITING_LOCAL_PLAN_REVIEW` after an accepted plan edit, at
either stage's `REVISE` origin, since the recomputation rule already makes
a prior stage's ledger entry read as stale the moment the edit changes
`review_content_id` (no explicit ledger clear needed). Still not this
checkpoint's concern: `/approve-review`'s own commit-creation step (the
Claude session running `git commit` per `.claude/commands/approve-review.md`,
never a Python-side commit writer, consistent with this module's decide-
and-validate-only design).

WF2 adds D-Selection's deterministic four-rule checkpoint-selection
algorithm (`select_next_checkpoint`), the `IN_PROGRESS`/`COMPLETE` state
writers (`transition_checkpoint_in_progress`/`complete_checkpoint`,
implementing checkpoint-complete-vs-all-complete semantics), and D3's
worktree-scoped dirty-resume mechanics: `write_worktree_identity` (the
named writer for `.ai-review/runtime/WORKTREE_IDENTITY.json`, previously
schema-only) and its read-side counterpart `verify_dirty_resume_safety`.

WF4c adds D-Functional-Remediation's general functional-remediation cycle:
`mark_technical_approval_stale` (the bounded-code-change branch's
stale-before-edit write, persisted before the first source/test edit) and
`record_bundle_generation` (`reviewed_implementation_head`'s sole writer,
closing the loop `OPUS-R6-013` found -- nothing wrote this field before,
so `AWAITING_TECHNICAL_APPROVAL`'s entry condition was unreachable for any
real `"2.1"` item; wired into the bundle generator at exactly the
`"implementation"`/`"post-fix"` stage). `create_remediation_child_work_item`
implements the broad/multi-finding branch: a distinct child work item
(`"<parent>-remediation-<n>"`, `n` derived deterministically, never
caller-supplied) with its own registry/mapping/approval lifecycle,
routed through the ordinary commands from there, never mutating the
parent's own registry/mapping/completed-checkpoint history.
`incomplete_children`/`complete_work_item`'s extended check implement
"parent acceptance blocks on an incomplete child" (resolves `GPT-R9-016`).

WF8b adds the first production slice of `D-Checkpoint-Ownership`
(revisions 69-72 of the plan's "The origination reference" and "The read
fails closed, and the partition is total"): `checkpoint_origination_provable`
and its supporting `origination_reference_commits`/
`_checkpoint_status_at_commit`, scanning every commit
`git rev-list --all --full-history -- <state path>` enumerates for a
decidable observation that a checkpoint's `IN_PROGRESS` was ever supplied
by a checkout, merge, or reset rather than genuinely originated in this
worktree. Wired into `/milestone-implement`'s `[2.1 step 1]` step 1c,
alongside `verify_dirty_resume_safety`, on the resume path only.

A second WF8b slice adds "The record" and "Fencing: the mutation/handoff
guard" (revisions 63-72), plus the explicit takeover and the abandoned-
guard recovery it depends on: the shared, never-committed claim record
under `$(git rev-parse --git-common-dir)/ai-workflow/checkpoint-claims/`
(`claim_checkpoint`/`resolve_claim`/`observe_claim`/`release_checkpoint`,
atomic `os.link`/`os.rename` publication, symlink/directory/EACCES all
failing closed rather than reading as absent), the per-work-item mutation/
handoff guard (`acquire_guard`/`assert_claim_owner`/`owner_mutation`'s one
fixed acquire-assert-mutate-release window, `GPT-R81-001`'s fenced
handoff), the observation-bound, evidence-first explicit takeover
(`takeover_evidence`/`take_over_claim`), and the distinct abandoned-
`"destructive"`-guard recovery for a holder worktree that no longer
exists (`recover_abandoned_destructive_guard`, `OPUS-R82-001`) -- gated
on `git worktree list`, never on a timeout or a liveness guess. Also
fixes `OPUS-R86-002`'s `WORKTREE_IDENTITY.json` lost-update defect
(12/12 threaded trials lost an entry under the prior plain read-modify-
write): `write_worktree_identity` now serializes its whole load-validate-
mutate-publish sequence under `identity_document_lock`
(`.ai-review/runtime/WORKTREE_IDENTITY.lock`, a stable, never-unlinked,
per-worktree `fcntl.flock` leaf) and publishes by a single `os.replace`
(`_publish_worktree_identity`, `OPUS-R86-005`); `repair_worktree_identity`
is the authorized repair-by-overwrite a rotating operation uses when the
taking worktree's own identity document is undecidable
(`OPUS-R86-003`/`-004`).

A third WF8b slice adds `adopt_claim` ("Reconciling the two authorities",
revisions 63-72): the one-time migration path for a checkpoint
interrupted before this mechanism existed, publishing a claim for a
checkpoint this worktree already holds `IN_PROGRESS` -- guarded by the
same origination reference the first slice built
(`checkpoint_origination_provable`, re-evaluated a second time at
publication under `guard_mutation_lock`), never by the dry-run
prototype's superseded `committed_checkpoint_status`-only check.

A fourth WF8b slice adds `/milestone-implement`'s own step 1c/1d/1f
wiring ("Where the check belongs, and the ordering"):
`resolve_checkpoint_ownership` (the full "Reconciling the two
authorities" table -- RESUME/FRESH/CONTINUE_CLAIM/NO_CHECKPOINT, the
foreign-claim refusal, the adopt-then-resume branch, the single
automatic release, and the two defensive mismatch refusals) and
`_attach_ownership_evidence`/`_ownership_escape_hint`, which annotate
every refusal 1c raises with `.ownership_evidence` unconditionally,
including on an absent claim, plus origination-specific evidence
components for `CheckpointOriginationUnprovableError` specifically.

A fifth WF8b slice adds `WFR-66`'s identity-query enforcement: the
identity queries' own read partition (`_identity_query_at_commit`/
`_scan_identity_reference`, distinct from and disjoint with the
origination test's own partition, though both scan the same reference),
the two permanent, unescapable observed-id refusals
(`WorkItemIdReusedError`/`CheckpointIdReusedError`, wired into
`route_work_item`'s fresh-id branch and `write_registry_and_mapping`'s
delta-scoped checkpoint-id check), and the evidence-bound,
non-replayable, repository-serialized escape from the undecidable case
(`authorize_identity_reference_gap`, `recover_abandoned_destructive_
guard`'s own signature) with its durable record under
`$(git rev-parse --git-common-dir)/ai-workflow/identity-gap-authorizations/`.

Deliberately still bounded: no WFR-66 enforcement wired into any
`.claude/commands/*.md` operator flow beyond the two library call sites
above, and executing `v2-1-dry-run`'s `S14`/`S15` scenarios for real
against this design remains future `WF8b` scope.

Stdlib-only, mirroring `scripts/workflow_fingerprint.py`'s own
`docs/TECHNICAL_DECISIONS.md`-recorded constraint.

Run the hermetic suite: python3 scripts/workflow_state_test.py
Run the real-repository demonstration: python3 scripts/workflow_state_demo_test.py
"""

from __future__ import annotations

import ast
import base64
import contextlib
import copy
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, NamedTuple

import workflow_fingerprint as fingerprint
from workflow_fingerprint import (  # noqa: F401 - re-exported for callers
    InvalidWorkItemIdError,
    InvalidWorkItemTypeError,
    validate_work_item_id,
    validate_work_item_type,
)

# state_writer: "publisher"
# This module is the sole declared publisher on the `scripts/**` state-
# writer surface (item 357(h)): it defines `state_lock`/`state_transaction`,
# the serialization helper `discover_state_writers`'s own projection names,
# and `resolve_completion_obligations`/the `WFO-STATE-SERIALIZATION`
# conformance itself. No file under `scripts/` other than this one writes
# `docs/ai-workflow/WORKFLOW_STATE.json`.

DEFAULT_CONFIG_PATH = Path("docs/ai-workflow/WORKFLOW_CONFIG.json")
DEFAULT_STATE_PATH = Path("docs/ai-workflow/WORKFLOW_STATE.json")
WORKTREE_IDENTITY_PATH = Path(".ai-review/runtime/WORKTREE_IDENTITY.json")

SCHEMA_VERSION = 1

# The fail-safe value D3 names explicitly: "Missing or corrupt config
# before activation -> validator fails closed to default_workflow_version:
# '1'" (resolves OPUS-R6-015).
PRE_ACTIVATION_FAILSAFE_VERSION = "1"

WORK_ITEM_TYPES = frozenset({"process", "product"})
# work_item_kind is a controlled vocabulary distinct from work_item_type
# (resolves GPT-R9-013); "synthetic" is WF8b's isolated dry-run item.
WORK_ITEM_KINDS = frozenset({"process", "product", "synthetic"})


def validate_work_item_kind(work_item_kind: str) -> None:
    """`work_item_kind`'s counterpart of `fingerprint.validate_work_item_type`,
    with the same fail-closed non-`str` guard (salvage audit `I2`): the
    three call sites below all read this value straight out of an
    unvalidated `WORKFLOW_STATE.json` or a caller argument, so a corrupt
    JSON value must raise this module's own documented error rather than
    a raw `TypeError` from the set-membership test."""
    if not isinstance(work_item_kind, str) or work_item_kind not in WORK_ITEM_KINDS:
        raise InvalidWorkItemTypeError(f"unknown work_item_kind: {work_item_kind!r}")

CHECKPOINT_STATUSES = frozenset({"IN_PROGRESS", "COMPLETE", "NEEDS_REVALIDATION"})

# D2's unified plan_approval/technical_approval record shape.
# "SUPERSEDED" is additive (D-Plan-Amendment-3, workflow-2.4.0): an
# explicit, authorized `/request-plan-amendment` retired this approval on
# purpose, distinct from "STALE" (the same reviewed plan document changed
# under us, by accident or a later REVISE). No live writer ever produces
# "STALE" for `plan_approval` today (`apply_plan_approval` always writes
# "CURRENT"), so this addition disambiguates a value that was previously
# only theoretical, not one any existing record actually held.
APPROVAL_STATUSES = frozenset({"CURRENT", "STALE", "SUPERSEDED"})
APPROVAL_BASES = frozenset({"EXTERNAL_APPROVE", "USER_OVERRIDE", "LEGACY_V1"})
# Narrowed per OPUS-R10-014: no_content_id removed -- the only basis that
# uses waivers (LEGACY_V1) always backfills a real content ID at import
# (D-Legacy), so no reachable state can ever emit it.
WAIVED_GUARANTEES = frozenset({"no_bundle_id", "no_telemetry"})
# D2's mechanism-independent guard names "plan"/"implementation" explicitly
# for /approve-review; "acceptance" extends the identical mechanism to
# /accept-milestone's AWAITING_USER_ACCEPTANCE gate, which the same guard
# sentence names in the same breath without itself enumerating a third
# stage keyword (a real gap in the plan text, resolved here rather than
# left unimplemented -- flagged to the user in this checkpoint's report).
# A fourth stage keyword, "scoped_remediation", existed for
# `/accept-scoped-remediation` until that command was retired (ledger
# `I10`): its gate was unreachable through every supported lifecycle, so
# the stage it named could never be confirmed. It is deliberately *not*
# kept as a vestigial vocabulary entry -- an unreachable stage keyword an
# operator could still be asked to type is exactly the dead contract the
# retirement removes.
APPROVAL_STAGES = frozenset({"plan", "implementation", "acceptance"})

# The functional-review checklist's fixed location (MILESTONE_WORKFLOW.md's
# AWAITING_FUNCTIONAL_REVIEW section names this path) -- never read from
# any work item's own fields, so a hand-edited or foreign path can never
# substitute for it.
FUNCTIONAL_CHECKLIST_PATH = "docs/ACTIVE_MILESTONE.md"

_GIT_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{40}$")

# D-Plan-Review-Stages' verdict/state transition table: the only three
# verdicts either `/review-plan` or `/record-manual-plan-review` ever
# ingest from REVIEW_FEEDBACK.md's `Status:` field.
PLAN_REVIEW_VERDICTS = frozenset({"APPROVE", "REVISE", "BLOCK"})

# workflow-2.5.0 CP3: `D-Implementation-Review-Stages`' own verdict set,
# mirroring `PLAN_REVIEW_VERDICTS` exactly -- the identical three verdicts,
# ingested by `/review-implementation`/`/record-manual-implementation-review`
# instead of `/review-plan`/`/record-manual-plan-review`.
IMPLEMENTATION_REVIEW_VERDICTS = frozenset({"APPROVE", "REVISE", "BLOCK"})

# Canonical, SCREAMING_SNAKE_CASE `plan_review_stages` key casing (item 8,
# workflow-v2-3-followups CP3): every write past this checkpoint uses these
# two constants, never the legacy lowercase literals directly. Legacy
# lowercase keys remain readable via `normalize_plan_review_stages` --
# `workflow-v2-3`'s own terminal record, and any hand-authored fixture,
# never need rewriting.
LOCAL_MODEL_PLAN_REVIEW = "LOCAL_MODEL_PLAN_REVIEW"
MANUAL_EXTERNAL_PLAN_REVIEW = "MANUAL_EXTERNAL_PLAN_REVIEW"

# workflow-2.5.0 (D-Implementation-Review-Version-Activation, "Inheritance
# rule, general"): the single named membership constant replacing the exact
# `governing_workflow_version == "2.1"` / `!= "2.1"` literal at every site
# that gates the two-stage plan-review protocol -- `publish_plan_revision`,
# `plan_approval_gate_reachable`, `_require_v2_1_plan_review`, and
# `_validate_plan_review_stages`. A `"2.2"` item is a `"2.1"` item for plan
# review purposes: it runs the identical two-stage mechanism, unchanged.
TWO_STAGE_PLAN_REVIEW_VERSIONS = frozenset({"2.1", "2.2"})

# Canonical, SCREAMING_SNAKE_CASE `implementation_review_stages` key casing
# (workflow-2.5.0, D-Implementation-Review-Stages), mirroring
# `LOCAL_MODEL_PLAN_REVIEW`/`MANUAL_EXTERNAL_PLAN_REVIEW` above exactly.
# This ledger is introduced fresh at `"2.2"` -- no legacy lowercase variant
# has ever existed for it -- but `normalize_implementation_review_stages`
# still mirrors `normalize_plan_review_stages`'s own collision-aware read
# contract below, so a future legacy alias (if one is ever introduced) is
# handled by the same discipline from day one rather than bolted on later.
# Each stage's ledger key and its own `REVIEW_FEEDBACK.md` `Reviewer role:`
# string are deliberately the identical one name -- exactly as they already
# are on the plan side -- so no document, command or validator ever has two
# names for one stage to keep in step.
LOCAL_MODEL_IMPLEMENTATION_REVIEW = "LOCAL_MODEL_IMPLEMENTATION_REVIEW"
MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW = "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"

# Only MILESTONE_COMPLETE is terminal -- LEGACY_READY is explicitly
# "dormant, not terminal" (D-Legacy phase 1, resolves GPT-R9-005).
TERMINAL_PHASES = frozenset({"MILESTONE_COMPLETE"})

# Union of the current v1 state machine (docs/ai-workflow/
# MILESTONE_WORKFLOW.md, unmodified by this work item) and the v2.1-only
# phases this plan adds (D-States/D-Plan-Review-Stages/D-Legacy) -- a
# single validator must accept either vocabulary since a work item's
# governing_workflow_version selects which one its own commands use
# (D-Self-Governance's dual-mode branching). Full entry/exit transition
# enforcement between these phases is out of WF1a's scope (see module
# docstring) -- this is an allowlist, not a transition graph.
KNOWN_PHASES = frozenset({
    # v1 (docs/ai-workflow/MILESTONE_WORKFLOW.md, unchanged by this work item)
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
    # v2.1-only additions (D-States, D-Plan-Review-Stages)
    "AWAITING_LOCAL_PLAN_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW",
    "AWAITING_PLAN_APPROVAL",
    "AWAITING_TECHNICAL_APPROVAL",
    # D-Legacy phase 1 -- dormant, not terminal
    "LEGACY_READY",
    # workflow-2.4.0 addition (D-Plan-Amendment-1): real and persisted,
    # unlike the four vocabulary-only states above, because the mechanism
    # must survive an interruption between the request and the first
    # post-request /milestone-plan call. Entered by /request-plan-amendment
    # alone (its sole writer, `request_plan_amendment`), left by the very
    # next /milestone-plan invocation reusing that command's existing
    # step 3/[2.1] machinery unchanged.
    "AMENDING_PLAN",
    # workflow-2.5.0 addition (D-Implementation-Review-Stages): "2.2"-only,
    # real and persisted. Entered in place of
    # AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW for a "2.2"-governed item's
    # implementation review, mirroring AWAITING_LOCAL_PLAN_REVIEW/
    # AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW's own local-then-manual-external
    # shape at the implementation stage. "Terminal-phase naming, decided"
    # keeps AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW itself as the "2.2"
    # terminal phase too -- its entry condition, not its name, grows the
    # extra ledger check (see D-Implementation-Review-Version-Activation).
    "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
    "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
})


class CorruptJsonError(Exception):
    """Raised for unparseable JSON in a tracked workflow file -- D3's
    'corrupt/unparseable JSON (fails closed)' validator-reject rule."""


class UnknownPhaseError(Exception):
    """Raised when a work item's `phase` is not in `KNOWN_PHASES`."""


class UnknownCheckpointStatusError(Exception):
    """Raised when a checkpoint's `status` is not in `CHECKPOINT_STATUSES`."""


class ActiveWorkItemInvalidError(Exception):
    """Raised when `active_work_item_id` names a nonexistent entry or one
    whose phase is terminal (D3's validator-reject rule)."""


class WorkItemIdKeyMismatchError(Exception):
    """Raised when a `work_items` entry's own `work_item_id` field
    disagrees with its containing map key (restated per OPUS-R10-015)."""


class MultipleInProgressCheckpointsError(Exception):
    """Raised when a single work item has more than one `IN_PROGRESS`
    checkpoint at once (D3's validator-reject rule)."""


class CheckpointDependencyNotCompleteError(Exception):
    """Raised when a checkpoint is `COMPLETE` while the registry names a
    dependency of its that is not (D3's validator-reject rule)."""


class CheckpointNotReachableError(Exception):
    """Raised when a checkpoint marked `COMPLETE` has no reachable commit
    carrying its `Workflow-Checkpoint` trailer -- D3's "completion without
    a reachable commit (D-Commit-Provenance)" validator-reject rule."""


class AmbiguousCheckpointTrailerError(Exception):
    """Raised when more than one commit in range carries the same
    (`Workflow-Checkpoint`, `Workflow-Work-Item`) trailer pair and the
    first-parent-ancestor tie-break does not resolve to exactly one
    (D-Commit-Provenance's stated recovery: a `Workflow-Supersedes:`
    trailer or an explicit `WORKFLOW_STATE.json` annotation, never a
    silent pick)."""


class AmbiguousApprovalTrailerError(Exception):
    """Raised when more than one commit in range carries the same
    (`Workflow-Plan-Approval` or `Workflow-Technical-Approval`,
    `Workflow-Work-Item`) trailer pair and the first-parent-ancestor
    tie-break does not resolve to exactly one -- the same genuine-
    ambiguity recovery `AmbiguousCheckpointTrailerError` names, extended
    by WF4a-iii to the approval trailers (resolves `OPUS-R6-022`,
    missing-test item 39)."""


class PostApprovalManifestMismatchError(Exception):
    """Raised when a just-created approval commit's own committed content
    does not recompute to the `review_content_id` its approval record
    claims -- WFR-06's post-commit parity check, run once immediately
    after `/approve-review` creates the commit, never trusted on the
    record's word alone."""


class DirtyIndexBeforeStagingError(Exception):
    """`/approve-review`'s Git index-isolation precondition: the index
    already differs from `HEAD` *before* this invocation stages anything
    of its own -- generalizes `D-Approval-Commits`' bootstrap
    transaction's own index-isolation precondition (revision 54,
    `GPT-R71-001`) to the ordinary `/approve-review plan` commit. Checked
    *before* this call's own staging, giving a clearer diagnostic (a
    pre-existing dirty index, distinct from this call's own staging
    picking up something unexpected) for a path whose staged content
    genuinely differs from `HEAD`. Deliberately does **not** claim
    coverage for a re-staged, content-*unchanged* path: re-staging bytes
    already identical to `HEAD` produces an index entry indistinguishable
    from `HEAD`'s own tree, not a separate state this or any diff-based
    check could detect -- confirmed empirically
    (`test_restaging_unrelated_unchanged_content_is_a_true_no_op_not_a_gap`)
    after an earlier draft of this docstring claimed otherwise. Also
    confirmed not to reject this work item's own legitimate pending
    intent-to-add paths (`/milestone-plan`'s own staging step, run before
    `/approve-review`): `git diff --cached` does not report intent-to-add
    entries either, so they pass this precondition exactly like a clean
    index, and this call's own subsequent `git add` then stages their
    real content
    (`test_precondition_does_not_reject_this_work_items_own_pending_intent_to_add_paths`)."""


class UnexpectedStagedPathSetError(Exception):
    """`/approve-review`'s post-staging assertion: the Git index's staged
    diff relative to `HEAD` names a path outside the resolved plan-stage
    approval-commit member set -- generalizes `D-Approval-Commits`'
    bootstrap transaction's own "exact staged-set assertion" (revision
    54, `GPT-R71-001`) to the ordinary `/approve-review plan` commit, so
    an unrelated staged-and-changed path (this work item's own leftover,
    or a concurrent work item's, D1) is caught and refused rather than
    silently absorbed by a pathspec-free `git commit`."""


class StagedBlobMismatchError(Exception):
    """The conditional fifth member's *staged* (Git index) blob does not
    match the sha256 `resolve_plan_stage_approval_commit_paths` pinned
    immediately after resolving it -- defense against a race between
    resolution and staging, mirroring `D-Approval-Commits`' bootstrap's
    own pre-commit declaration pin (revision 53, `GPT-R70-001`),
    generalized here."""


class CommittedBlobMismatchError(Exception):
    """The conditional fifth member's *committed* blob does not match the
    sha256 pinned before staging -- defense in depth beyond
    `verify_post_approval_manifest_match`'s own full projection-digest
    check, isolating exactly which member diverged if the two ever
    disagree."""


class CommittedPathSetMismatchError(Exception):
    """The just-created commit's own changed-path set (relative to its
    sole parent) names a path outside the resolved approval-commit member
    set -- `D-Approval-Commits`' bootstrap transaction's own
    "committed-path-set assertion" (missing-test item 347's own named
    requirement), generalized here as a subset check (an expected member
    byte-identical to the parent commit produces no entry at all, which
    is correct, not an omission). Distinct from, and not redundant with,
    `stage_plan_approval_commit_paths`'s pre-commit staged-diff checks:
    those verify the *index* immediately before `git commit` runs, not
    the commit `git commit` actually produces -- a pre-commit or
    commit-msg hook that itself edits and re-stages files between that
    verification and the commit's own tree write would defeat the
    pre-commit checks alone but not this one (the same class of gap
    `D-Approval-Commits` revision 56 independently found and fixed for
    the bootstrap transaction)."""


class NonTopologicalRegistryOrderError(Exception):
    """Raised when a registry's `checkpoints` array does not list every
    checkpoint after all of its own `depends_on` entries (D-Selection
    point 3; missing-test items 29/73)."""


class NoCheckpointReadyError(Exception):
    """D-Selection rule 4: raised when no checkpoint is `COMPLETE` yet none
    is selectable either -- every remaining checkpoint has an incomplete
    dependency. Names the blocked checkpoint and its unmet dependencies
    rather than ever silently idling.

    Carries a structured `checkpoint_id` attribute (revision 27,
    `D-Scoped-Remediation-Acceptance`) naming the specific blocked
    checkpoint, so `registry_completion_status` never has to parse this
    exception's own message text to recover it."""

    def __init__(self, message: str, checkpoint_id: str) -> None:
        super().__init__(message)
        self.checkpoint_id = checkpoint_id


class WorktreeIdentityMissingError(Exception):
    """D3's worktree-scoped dirty-resume rule: raised when resuming an
    `IN_PROGRESS` checkpoint finds no local `WORKTREE_IDENTITY.json`, or
    one with no entry for this exact work item (`OPUS-R10-008`'s keyed
    design -- a different work item's entry never satisfies this)."""


class WorktreeIdentityMismatchError(Exception):
    """D3's worktree-scoped dirty-resume rule: raised when a local
    `WORKTREE_IDENTITY.json` exists but its recorded git identity
    (`repo_root`/`git_common_dir`/`worktree_root`) does not match this
    worktree's actual identity -- the file was inherited from, or copied
    from, a different worktree/clone."""


class UnmappedRequirementError(Exception):
    """Raised when a requirement in the mapping JSON names a
    `checkpoint_id` absent from the registry (D3's validator-reject rule,
    resolves OPUS-R10-006)."""


class UnownedCheckpointError(Exception):
    """Raised when a registry checkpoint owns zero requirements in the
    mapping JSON (D3's validator-reject rule, resolves OPUS-R10-006)."""


class PlanReviewStagesInvalidForVersionError(Exception):
    """Raised when `plan_review_stages` is non-null on a work item whose
    `governing_workflow_version` is not one of `TWO_STAGE_PLAN_REVIEW_
    VERSIONS` (resolves GPT-R11-001; widened workflow-2.5.0 from a bare
    `"2.1"` check to the membership constant)."""


class ManualStageWithoutLocalStageError(Exception):
    """Raised when `MANUAL_EXTERNAL_PLAN_REVIEW` is recorded while
    `LOCAL_MODEL_PLAN_REVIEW` is absent (resolves GPT-R11-001)."""


class ImplementationReviewStagesInvalidForVersionError(Exception):
    """workflow-2.5.0 (D-Implementation-Review-Stages): the
    `implementation_review_stages` ledger's own ledger-shape counterpart of
    `PlanReviewStagesInvalidForVersionError`, a new exception class rather
    than a reuse -- unlike `WrongReviewerRoleError`/`StaleReviewContentIdError`/
    `check_manual_stage_bundle_id_advisory`, which are already stage-
    agnostic, `PlanReviewStagesInvalidForVersionError`'s own message names
    `plan_review_stages` specifically. Raised when `implementation_review_
    stages` is non-null on a work item whose `governing_workflow_version`
    is not `"2.2"` -- unlike the plan-review ledger, this one is never
    valid for `"2.1"`: the two-stage *implementation*-review protocol is
    `"2.2"`-only (D-Implementation-Review-Version-Activation)."""


class ManualImplementationStageWithoutLocalStageError(Exception):
    """workflow-2.5.0: the `implementation_review_stages` counterpart of
    `ManualStageWithoutLocalStageError`. Raised when `MANUAL_EXTERNAL_
    IMPLEMENTATION_REVIEW` is recorded while `LOCAL_MODEL_IMPLEMENTATION_REVIEW`
    is absent."""


class StageVerdictNotApproveError(Exception):
    """Raised when a recorded plan-review stage's `verdict` is anything
    other than `"APPROVE"` (resolves GPT-R14-010, missing-test item 116)."""


class UnknownPlanReviewVerdictError(Exception):
    """Raised when a verdict passed to `record_local_plan_review`/
    `record_manual_plan_review` is not one of `PLAN_REVIEW_VERDICTS`."""


class WrongGoverningVersionForPlanReviewStageError(Exception):
    """Raised when `/review-plan` or `/record-manual-plan-review` is
    invoked against a work item whose `governing_workflow_version` is not
    `"2.1"` -- v1 items have no two-stage plan-review protocol to run
    (D-Plan-Review-Stages, missing-test item 90's v2.1-side refusal)."""


class WrongPhaseForPlanReviewStageError(Exception):
    """Raised when `/review-plan` is invoked outside `phase ==
    AWAITING_LOCAL_PLAN_REVIEW`, or `/record-manual-plan-review` outside
    `phase == AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` -- covers "already
    completed this round" (the phase has already moved on) and a local
    `REVISE`'s work item never reaching the manual stage (WFR-41)."""


class StaleReviewContentIdError(Exception):
    """Raised when the bundle/feedback's `review_content_id` does not
    match the freshly recomputed current one -- hard, blocks ingestion at
    either stage (D-Plan-Review-Stages transition table; distinct from the
    manual stage's advisory-only `bundle_id` check, `OPUS-R14-005`)."""


# ---------------------------------------------------------------------------
# workflow-2.4.0: D-Plan-Amendment-1..8 -- amending an approved plan after
# implementation has begun. New error family, amendment-prefixed (I-R32-1)
# to stay clear of this module's own pre-existing, unrelated
# `ReconciliationTableParseError`/`_RECONCILIATION_STATUS_TOKENS` vocabulary.
# ---------------------------------------------------------------------------


class WrongPhaseForAmendmentRequestError(Exception):
    """Raised when `/request-plan-amendment` is invoked outside `phase in
    {"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"}` (D-Plan-Amendment-1) --
    the two phases this release supports, and deliberately the only two.
    Also the refusal a second, redundant `/request-plan-amendment` against
    an item already at `AMENDING_PLAN` hits, since that phase is not a
    member of the allowed set either."""


class AmendmentApprovalCommitUnreachableError(Exception):
    """Raised by `request_plan_amendment` when the work item's current
    `plan_approval`'s own approval commit is not discoverable, or not an
    ancestor of `HEAD` (D-Plan-Amendment-1's widened precondition,
    corrected revision 12, B-R12-1). Checked, and this error raised,
    *before* superseding anything -- `plan_approval.status` is never set
    to `SUPERSEDED` when this fires, so the item never wedges at
    `AMENDING_PLAN` with an unreproducible `pre_amendment_approval_commit`."""


class AmendmentAlreadyResolvedError(Exception):
    """Raised when reconciliation (folded into `apply_plan_approval`) finds
    `amendment_history[-1]["resolved_at_plan_revision"]` already non-`None`
    -- the same "wrong state, refuse and name it" discipline
    `WrongPhaseForAmendmentRequestError` applies to a re-run
    `/request-plan-amendment`, applied here to a re-run reconciliation
    (D-Plan-Amendment-3's write-once-field discipline)."""


class AmendmentReconciliationInputsMissingError(Exception):
    """Raised by `apply_plan_approval` when the work item has an open
    amendment (`amendment_history` non-empty, last entry's
    `resolved_at_plan_revision` still `None`) but one or more of
    `pre_registry`/`pre_plan_text`/`post_registry`/`post_plan_text` is
    `None` -- an internal-caller bug (the command procedure failed to read
    and pass all four), never a data condition this silently tolerates or
    skips reconciliation for (D-Plan-Amendment-4, B2-new)."""


class AmendmentPreSnapshotUnreproducibleError(Exception):
    """Raised by `load_pre_amendment_snapshot` when a pinned blob SHA
    cannot be retrieved via `git cat-file -p`, or the cross-check
    (`git ls-tree <pre_amendment_approval_commit> -- <path>`) disagrees
    with the manifest's own pinned blob -- naming the path and the blob SHA
    it could not reproduce, rather than silently substituting empty
    content or crashing on an unhandled `git` failure (D-Plan-Amendment-3,
    EXT-R6-I1's bounded, content-addressed reference redesign)."""


class AmendmentAnchorCoverageError(Exception):
    """Raised by `validate_post_anchor_coverage` (called from
    `apply_plan_approval`, before it computes any reconciliation outcome)
    when a checkpoint id present in `post_registry` has zero well-formed
    `<!-- CP<n> -->`/`<!-- /CP<n> -->` anchor pairs in `post_plan_text` --
    a forgotten anchor on the side where a refusal is always actionable
    (D-Plan-Amendment-4, B5-new)."""


class AmendmentAnchorMalformedError(Exception):
    """Raised by `parse_checkpoint_anchor_spans` (in `strict` mode, used
    only for `post_plan_text`) when a checkpoint id's own anchor tags are
    not a closed, non-nesting, per-id balanced grammar: an unmatched open
    tag, an orphan close tag, or a tag nested inside another open span for
    the same id (D-Plan-Amendment-4, B5-new/I5-new)."""


class AmendmentPostRegistryMalformedError(Exception):
    """Raised by `apply_plan_approval`'s amendment-reconciliation branch
    when `post_registry` has no `"checkpoints"` key at all (IMPL2-O2):
    `validate_post_anchor_coverage` reads it via `.get("checkpoints", [])`
    and would pass vacuously, but `validate_registry_topological_order`
    reads `registry["checkpoints"]` directly and would raise an unnamed
    `KeyError` for the identical malformed input -- named here, once,
    before either validator runs, matching every other refusal in this
    branch."""


class AmendmentCheckpointIdShapeError(Exception):
    """Raised by `request_plan_amendment` when the work item's own
    registry already contains a checkpoint id that is not of the shape
    `CP<digits>` (IMPL2-R1): `_CHECKPOINT_ANCHOR_RE`'s grammar can only
    ever produce an anchor tag keyed `"CP" + digits`, so
    `validate_post_anchor_coverage` is unsatisfiable for any such id --
    there is no text an author could write in the amended plan that would
    ever satisfy it. Raised *before* `plan_approval` is superseded, the
    same "refuse before any supersede" discipline
    `AmendmentApprovalCommitUnreachableError` already follows, naming
    every offending id at once rather than wedging the item at
    `AMENDING_PLAN` two review stages later with no in-band recovery."""


class AmendmentRegistryMissingIdError(Exception):
    """Raised by `request_plan_amendment` when its own current registry
    (loaded via `_load_authoritative_registry_or_none`) has a checkpoint
    entry with no `id` key (IMPL3-O2, renamed IMPL4-O2): an amendment-
    specific refusal, distinct from `RegistryCoverageError`, whose own
    vocabulary is documented primarily around completion-accounting call
    sites (`resolve_own_registry_completion_status`/
    `resolve_completion_obligations`) this check has nothing to do with.
    Named in `.claude/commands/request-plan-amendment.md`'s own refusal
    list alongside `AmendmentCheckpointIdShapeError`, the sibling check it
    runs immediately before."""


class AmendmentCheckpointActiveError(Exception):
    """Raised by `request_plan_amendment` (XMODEL-R4-B1, merged round-4
    review) when this work item already has a checkpoint `IN_PROGRESS` in
    `WORKFLOW_STATE.json`, or has an outstanding shared checkpoint claim
    (`resolve_claim`) -- checked *before* superseding anything, the same
    "refuse before any supersede" discipline every other precondition in
    this function follows.

    Both halves are checked, not only the state-only one, because they are
    two different synchronization domains: `claim_checkpoint` is published
    to the filesystem claims directory *before*
    `transition_checkpoint_in_progress` writes `WORKFLOW_STATE.json`
    (`/milestone-implement` step 1d's documented ordering), so
    `resolve_checkpoint_ownership`'s own supported `CONTINUE_CLAIM` outcome
    is a window in which a claim is real and outstanding while state still
    looks completely idle. A state-only `IN_PROGRESS` check alone would
    miss exactly that window and let `plan_approval` be superseded while a
    checkpoint start is already in flight. The independent second half of
    this fix -- refusing a checkpoint's own `IN_PROGRESS` publication once
    the work item has left `IMPLEMENTING` -- is
    `IllegalCheckpointStartPhaseError` on `transition_checkpoint_in_progress`
    itself."""


class WrongReviewerRoleError(Exception):
    """Raised when `REVIEW_FEEDBACK.md`'s declared `Reviewer role:` does
    not match the stage being ingested (e.g. a local-role or unlabeled
    file handed to `/record-manual-plan-review`, or vice versa)."""


class MissingLocalApprovalForManualStageError(Exception):
    """Raised when `/record-manual-plan-review` is asked to ingest an
    `APPROVE` while no current `LOCAL_MODEL_PLAN_REVIEW` `APPROVE` is
    recorded for the same `review_content_id` -- a restated invariant,
    since entry to `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` already requires
    it; defends against a corrupted or hand-edited state file."""


class DuplicateManualStageIngestionError(Exception):
    """Raised when a `MANUAL_EXTERNAL_PLAN_REVIEW` stage is already
    recorded against the current `review_content_id` -- rejects duplicate
    ingestion (a second invocation after a completed `APPROVE`/`REVISE`
    normally fails the phase precondition first; this only fires for a
    hand-edited or race-condition state, GPT-R12-002/-003)."""


class AmbiguousPlanReviewStageKeyError(Exception):
    """Raised by `normalize_plan_review_stages` when a `plan_review_stages`
    dict holds both a legacy-cased and a canonical-cased raw key that
    normalize to the same stage, with conflicting values -- never resolved
    by dict key-iteration order (resolves `GPT-FUP-R6-I02`, workflow-v2-3-
    followups CP3). A byte-identical duplicate under both raw keys is
    tolerated and silently collapsed instead; only a genuine conflict
    raises."""


class AmbiguousImplementationReviewStageKeyError(Exception):
    """workflow-2.5.0: `normalize_implementation_review_stages`'s own
    counterpart of `AmbiguousPlanReviewStageKeyError`, raised on a genuine
    conflict between two raw keys that normalize to the same
    `implementation_review_stages` canonical stage. No legacy-cased key
    has ever existed for this ledger, so this is unreachable through any
    supported write path today -- defined for structural parity with the
    plan-review ledger's own collision-aware read contract, exercised only
    by a direct unit-test construction of a conflicting dict."""


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP3: `D-Implementation-Review-Stages`' own review-stage
# writer exceptions -- implementation-stage-named siblings of the
# plan-review exceptions whose own name is literally plan-specific
# (`WrongPhaseForPlanReviewStageError`, `WrongGoverningVersionForPlanReviewStageError`,
# `MissingLocalApprovalForManualStageError`, `DuplicateManualStageIngestionError`,
# `UnknownPlanReviewVerdictError`); `WrongReviewerRoleError`/
# `StaleReviewContentIdError`/`check_manual_stage_bundle_id_advisory` are
# already stage-agnostic and reused verbatim below, unchanged.
# ---------------------------------------------------------------------------


class UnknownImplementationReviewVerdictError(Exception):
    """Raised when a verdict passed to `record_local_implementation_review`/
    `record_manual_implementation_review` is not one of
    `IMPLEMENTATION_REVIEW_VERDICTS` -- the implementation-stage counterpart
    of `UnknownPlanReviewVerdictError`."""


class WrongGoverningVersionForImplementationReviewStageError(Exception):
    """Raised when `/review-implementation` or
    `/record-manual-implementation-review` is invoked, in its authoritative
    `"2.2"` role, against a work item whose `governing_workflow_version` is
    not exactly `"2.2"` -- unlike the plan-review protocol
    (`TWO_STAGE_PLAN_REVIEW_VERSIONS`, valid for both `"2.1"`/`"2.2"`), the
    two-stage *implementation*-review protocol is `"2.2"`-only
    (D-Implementation-Review-Version-Activation): a `"1"`/`"2.1"` item has
    no two-stage implementation-review protocol to run. The
    implementation-stage counterpart of
    `WrongGoverningVersionForPlanReviewStageError`."""


class WrongPhaseForImplementationReviewStageError(Exception):
    """Raised when `/review-implementation` is invoked (in its `"2.2"`
    authoritative role) outside `phase == AWAITING_LOCAL_IMPLEMENTATION_REVIEW`,
    or `/record-manual-implementation-review` outside `phase ==
    AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` -- covers "already
    completed this round" and a local `REVISE`'s work item never reaching
    the manual stage. The implementation-stage counterpart of
    `WrongPhaseForPlanReviewStageError`."""


class MissingLocalApprovalForManualImplementationStageError(Exception):
    """Raised when `/record-manual-implementation-review` is asked to
    ingest an `APPROVE` while no current `LOCAL_MODEL_IMPLEMENTATION_REVIEW`
    `APPROVE` is recorded for the same `review_content_id` -- a restated
    invariant, since entry to `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
    already requires it; defends against a corrupted or hand-edited state
    file. The implementation-stage counterpart of
    `MissingLocalApprovalForManualStageError`."""


class DuplicateManualImplementationStageIngestionError(Exception):
    """Raised when a `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` stage is
    already recorded against the current `review_content_id` -- rejects
    duplicate ingestion. The implementation-stage counterpart of
    `DuplicateManualStageIngestionError`."""


class ConfigMissingAfterActivationError(Exception):
    """Raised when `WORKFLOW_CONFIG.json` is missing or corrupt *after*
    Workflow activation -- a hard stop, never a silent downgrade (resolves
    OPUS-R6-015). Generalized (workflow-2.5.0,
    `D-Implementation-Review-Version-Activation`) off the original
    hard-coded `"Workflow v2.1"` text: `load_config`'s raise message names
    the resolved destination version when the latest activation/rollback
    event resolves to one (e.g. `"Workflow 2.2"`), and names the
    unresolved `Workflow-Rollback` trailer's own value verbatim in the
    fail-closed miss case -- never a version name that, in the miss case,
    by construction does not exist."""


class AlreadyActivatedError(Exception):
    """Raised by `build_activated_config` when the config's
    `default_workflow_version` is already the requested `target_version`
    (default `"2.1"`, generalized workflow-2.5.0 for the `"2.1"` -> `"2.2"`
    bump) -- `WF-Activate` is a sole, one-time boundary (WFR-10), never an
    idempotent no-op call."""


class NotActivatedError(Exception):
    """Raised by `build_rolled_back_config` when the config's
    `default_workflow_version` is not the requested `target_version`
    (default `"2.1"`, generalized workflow-2.5.0 for the `"2.1"` -> `"2.2"`
    bump) -- there is nothing to roll back (WFR-10/WFR-12's rollback is a
    real state reversal, not a bare field reset)."""


class UnsupportedGoverningVersionError(Exception):
    """Raised when a work item's `governing_workflow_version` is outside
    the config's `supported_versions` (resolves OPUS-R6-024, optional)."""


class WorkItemTerminalReuseError(Exception):
    """Raised when routing names a `work_item_id` that already exists at a
    terminal phase -- ids are not reused after `MILESTONE_COMPLETE` (D1)."""


class WorkItemDeclarationFactConflictError(Exception):
    """Raised when `route_work_item`'s resume branch is supplied a
    `plan_path`/`registry_path`/`mapping_path`/`base_commit` value that
    disagrees with an already-non-null stored value -- a declaration fact
    is immutable once set (`D-Fingerprint-Generalization`, `OPUS-R28-002`),
    consistent with the existing identity-field immutability rule."""


class PlanRevisionMirrorMismatchError(Exception):
    """Raised when `WORKFLOW_STATE.json`'s own `plan_revision` field for a
    work item disagrees with that item's own registry JSON's
    `plan_revision` -- `WORKFLOW_STATE.json`'s copy is a non-authoritative
    display mirror only (`OPUS-R25-002`), and a divergence between the two
    is a data-integrity defect, not a legitimate state."""


class TerminalPlanRevisionPublicationError(Exception):
    """Raised by `publish_plan_revision` (`D-Plan-Revision-Publication`,
    `WFR-65`) when the named work item's `phase` is already terminal
    (`MILESTONE_COMPLETE`) -- a plan revision can never be published
    against an item that has already reached its own terminal state."""


class MissingRegistryForPlanRevisionMirrorCheckError(Exception):
    """Raised by `validate_state`'s `repo_root`-driven whole-state
    plan-revision-mirror check (`GPT-R31-003`) when a work item declares a
    non-null `registry_path` whose file does not exist or is unreadable at
    `repo_root` -- fails closed rather than silently skipping that item's
    own mirror check, which would let "omitted registry coverage" pass as
    if it had been verified. Also raised when `registry_path` fails the
    shared safe-path resolver (`GPT-R32-001`): an absolute path, a `../`
    traversal, a symlink at any path component, or any other
    repository-boundary escape is exactly as unusable a "registry" as one
    that is simply missing. Also raised when `registry_path` resolves to
    an existing regular file that is not Git-tracked (`GPT-R33-002`) -- an
    untracked file is absent from commits, review bundles, and approval
    provenance, so it cannot serve as authoritative mirror-check content
    even though it is readable. Also raised for a resolved registry file
    whose content is malformed JSON or a JSON value that is not an object
    (`GPT-R33-004`), instead of letting a raw `JSONDecodeError`/
    `AttributeError` escape this check."""


class InvalidRegistryPlanRevisionError(Exception):
    """Raised by `validate_state`'s `repo_root`-driven whole-state
    plan-revision-mirror check (`GPT-R32-002`) when a work item's own
    registry resolves but its `plan_revision` field is missing, `null`, not
    an integer (including `bool`, which Python's `int` subclasses), or
    below the schema's minimum of 1. A malformed registry cannot satisfy
    Revision 21's state-mirror invariant, so this fails closed instead of
    silently treating "no revision to compare" as "nothing to check"."""


class InvalidApprovalRecordError(Exception):
    """Raised when a `plan_approval`/`technical_approval` record does not
    match D2's shape, including the plan-stage's permanently-null
    `reviewed_content_commit` rule (GPT-R9-006) and the `waived_guarantees`
    controlled vocabulary (OPUS-R6-025/OPUS-R10-014)."""


class BlockCannotApproveError(Exception):
    """Raised when the most recently reviewed round's status is `BLOCK`, or
    a durable `technical_review_block_pins` (`D2a`) entry exists for the
    current bundle -- D-States: `BLOCK` never reaches either approval basis
    by any path, and a pinned bundle can never become override-eligible
    again by any later edit of the mutable feedback file (`WF8c` item (a),
    `OPUS-R102-002`)."""


class PinLedgerMonotonicityError(Exception):
    """Raised by `state_transaction`'s durability enforcement (`D2a`,
    revision 40, `GPT-R56-004`) when a candidate state's
    `technical_review_block_pins` for some work item disagrees with the
    freshly re-read pre-mutation state by more than the append of zero or
    one new, previously-unseen pin -- a missing, mutated, or removed prior
    entry all raise this. The ledger is append-only and permanent: every
    previously-committed pin must remain present and field-identical
    forever, an invariant enforced on every state write (`state_transaction`
    is D1's sole write choke point), not only on
    `record_technical_review_block_pin`'s own call site."""


class DuplicateTechnicalReviewBlockPinError(Exception):
    """Raised when a work item's `technical_review_block_pins` ledger
    contains more than one entry for the same `bundle_id` -- D2a's writer
    (`record_technical_review_block_pin`) is idempotent by construction and
    never produces this; a duplicate here means the field was written by
    some other path."""


class UserConfirmationRejectedError(Exception):
    """Raised when literal `user_confirmation` text is missing, empty, or
    does not name the exact `work_item_id` and stage being approved
    (resolves OPUS-R6-010, corrected by GPT-R9-008/OPUS-R10-010/GPT-R11-004)."""


class LegacyReconciliationError(Exception):
    """D-Legacy's branch-reconciliation precondition failed: either the
    reviewed legacy commit is not a reachable ancestor of head, or the
    reachable `docs/ACTIVE_MILESTONE.md` content does not contain the
    caller-expected substring. Re-checked at both import (WF-M8a) and
    adoption (WF-M8b) time -- never trusted once and cached."""


class LegacyImportAlreadyExistsError(Exception):
    """Raised when D-Legacy's import step is invoked for a `work_item_id`
    that already has an entry -- import is a one-time act, never a silent
    re-import or resume (unlike `route_work_item`'s ordinary
    create-or-resume semantics)."""


class LegacyAdoptionWrongPhaseError(Exception):
    """Raised when D-Legacy phase 2 (`WF-M8b`) adoption is invoked against
    a work item whose `phase` is not `LEGACY_READY` -- adoption only ever
    promotes a dormant legacy entry; a command targeting a different work
    item, or this same item outside `LEGACY_READY`, never touches it
    (D-Legacy: "adoption never runs implicitly")."""


class LegacyAdoptionStaleApprovalError(Exception):
    """Raised when D-Legacy phase 2 adoption recomputes the dormant
    entry's `technical_approval` freshness and finds it `STALE` --
    protected implementation-stage content changed between import and
    adoption. Adoption stops outright and requires a fresh
    implementation-review round through the normal `technical_approval`
    lifecycle (`/apply-implementation-review` + `/approve-review
    implementation`); it never re-imports (D-Legacy phase 2, resolves
    `WFR-08`'s "no basis branch" rule applied to promotion time, not just
    import time)."""


class InvalidBundleGenerationStageError(Exception):
    """Raised when `record_bundle_generation` is called with a `stage`
    other than `"implementation"`/`"post-fix"` -- `reviewed_implementation_head`
    is written at exactly those two bundle-generation points, never any
    other (D-Approval-Commits, WF4c)."""


class InvalidBundleGenerationOutcomeError(Exception):
    """Raised when `record_bundle_generation` is called with an `outcome`
    other than `"ordinary"`/`"same_content"` (WF8c (c), D-Commit-Provenance
    "Same-content post-fix republication" -- the function's own two, and
    only two, legal outcomes)."""


BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE = {
    # "implementation" (a round's first bundle) is legal only from
    # SELF_REVIEWING_IMPLEMENTATION -- never from APPLYING_REVIEW_FEEDBACK
    # (already mid-remediation) or AWAITING_FUNCTIONAL_REVIEW (past
    # technical approval; a first-round bundle for this work item's own
    # implementation content already exists by construction there).
    "implementation": frozenset({"SELF_REVIEWING_IMPLEMENTATION"}),
    # "post-fix" is legal from APPLYING_REVIEW_FEEDBACK (an ordinary
    # implementation-review REVISE round) and, self-discovered during
    # workflow-v2-3-followups's own /accept-milestone pre-flight,
    # AWAITING_FUNCTIONAL_REVIEW -- the only phase /apply-functional-
    # review's own "bounded code change" branch (D-Functional-Remediation)
    # can ever be entered from, since that is its own documented
    # precondition. The AWAITING_FUNCTIONAL_REVIEW branch additionally
    # requires technical_approval.status == "STALE" (record_bundle_
    # generation's own check below) -- the bounded-fix marker
    # mark_technical_approval_stale writes before the first edit lands,
    # so this phase alone is never sufficient on its own.
    "post-fix": frozenset({"APPLYING_REVIEW_FEEDBACK", "AWAITING_FUNCTIONAL_REVIEW"}),
}

BUNDLE_GENERATION_LEGAL_SOURCE_PHASES = frozenset.union(
    *BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE.values()
)


class IllegalBundleGenerationSourcePhaseError(Exception):
    """Raised when `record_bundle_generation` is called from a phase that
    is not legal for the requested `stage`
    (`BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE`) -- OPUS-R101-001,
    missing-test item 275, widened by workflow-v2-3-followups's own
    continued scope: a behavioral refusal, never a silent proceed, naming
    the actual phase, the requested stage, and the phase(s) legal for
    that stage."""


class IllegalSelfReviewEntryPhaseError(Exception):
    """Raised when `enter_self_reviewing_implementation` is called from a
    phase that is neither `IMPLEMENTING` (the one legal source) nor
    `SELF_REVIEWING_IMPLEMENTATION` (the already-there no-op) -- salvage
    audit `B8`, convergence pass 7. Names the actual phase and the legal
    source, the same behavioural-refusal shape
    `IllegalBundleGenerationSourcePhaseError` uses one transition later."""


class IncompleteCheckpointsForSelfReviewError(Exception):
    """Raised when `enter_self_reviewing_implementation` is called from
    `IMPLEMENTING` while at least one registry checkpoint is not yet
    `COMPLETE` -- salvage audit `B8`, convergence pass 7.

    `SELF_REVIEWING_IMPLEMENTATION`'s documented entry condition
    (`docs/ai-workflow/MILESTONE_WORKFLOW.md`) is "all checkpoints
    implemented", and this is the same all-complete predicate
    `complete_checkpoint` applies on the ordinary path -- so this writer
    can never be used to skip outstanding work, which is exactly what the
    acceptance matrix's own pre-repair `C5` fixture did by hand-writing
    the phase (salvage audit `O6`). Carries a structured
    `outstanding_checkpoint_id` attribute so a caller reports the blocked
    checkpoint without parsing this exception's message text."""

    def __init__(self, message: str, outstanding_checkpoint_id: str | None = None) -> None:
        super().__init__(message)
        self.outstanding_checkpoint_id = outstanding_checkpoint_id


class BundleGenerationRequiresStaleTechnicalApprovalError(Exception):
    """Raised when `record_bundle_generation(stage="post-fix", ...)` is
    called from `AWAITING_FUNCTIONAL_REVIEW` while `technical_approval.status`
    is not `"STALE"` -- the functional-review bounded-fix marker
    `mark_technical_approval_stale` writes before the first edit lands
    (`D-Functional-Remediation`). `AWAITING_FUNCTIONAL_REVIEW` alone is
    not sufficient: a `CURRENT` approval at this phase means no bounded
    fix is actually in flight, so a post-fix regeneration would silently
    fabricate provenance for a round that never happened."""


class IllegalApplyingReviewFeedbackEntryPhaseError(Exception):
    """Raised when `enter_applying_review_feedback` is called from a phase
    other than `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
    (`docs/ai-workflow/MILESTONE_WORKFLOW.md`'s documented entry
    condition: "implementation review feedback exists")."""


class AmbiguousBundleGenerationRecordTrailerError(Exception):
    """Raised by `discover_bundle_generation_record_commits` when more than
    one commit reachable in `base_commit..head` carries a
    `Workflow-Bundle-Generation-Record` trailer for the same
    `<work_item_id>/<implementation_revision>` value and the first-parent-
    ancestor tie-break does not resolve to exactly one (D-Commit-
    Provenance; WF8B-003 remediation) -- e.g. a genuine second, unrelated
    generation-record commit for the same round."""


class BundleGenerationRecordNotFoundError(Exception):
    """Raised when no `Workflow-Bundle-Generation-Record` commit is
    reachable for the work item's current `implementation_revision` (or
    when `reviewed_implementation_head`/`implementation_revision` is not
    yet set at all) -- `AWAITING_TECHNICAL_APPROVAL`'s provenance-interval
    check has nothing to validate against."""


class HeadPastBundleGenerationRecordError(Exception):
    """Raised when live HEAD is not exactly the discovered
    `Workflow-Bundle-Generation-Record` commit `T` -- a further commit
    landed after `T` carrying no provenance record of its own (D-Commit-
    Provenance condition 1: HEAD must equal `T` exactly, never merely a
    descendant of it)."""


class ReviewedImplementationHeadNotAncestorError(Exception):
    """Raised when `reviewed_implementation_head` is not an ancestor at
    all of the discovered `Workflow-Bundle-Generation-Record` commit `T`
    -- the provenance interval `reviewed_implementation_head..T` does not
    exist."""


class NonFirstParentProvenanceIntervalError(Exception):
    """Raised when the provenance interval `reviewed_implementation_head..T`
    is not a plain first-parent chain -- `reviewed_implementation_head` is
    reachable from `T` only through a merge or a non-first-parent path, or
    a commit inside the interval itself has more than one parent
    (D-Commit-Provenance condition 2: "no widen-the-search fallback")."""


class ProtectedPathInProvenanceIntervalError(Exception):
    """Raised when a non-terminal commit inside the provenance interval
    `reviewed_implementation_head..T` touches an implementation-stage
    `protected` or unclassified path -- only the terminal
    `Workflow-Bundle-Generation-Record` commit itself may exist between
    `reviewed_implementation_head` and live HEAD; every other commit must
    be excluded-only (D-Commit-Provenance condition 3)."""


class MalformedBundleGenerationRecordCommitError(Exception):
    """Raised when a discovered `Workflow-Bundle-Generation-Record` commit
    fails its own role-specific contract (D-Commit-Provenance condition 4,
    both roles): an **ordinary**-role commit must touch only
    `WORKFLOW_STATE.json`, its own `work_items[work_item_id]` field
    changes must be a non-empty subset of `{phase,
    reviewed_implementation_head, implementation_revision, state_revision,
    last_transition, implementation_review_stages}` (workflow-2.5.0 CP3
    widened the admitted set with the last field, unconditionally)
    including `phase`, its committed `phase` must equal
    `bundle_generation_target_phase(stage, governing_workflow_version)`'s
    resolved value (read from the commit's own committed
    `governing_workflow_version`, `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
    for `"1"`/`"2.1"`, byte-identical to before CP3), and it must carry
    exactly the two-trailer ordinary set (`Workflow-Bundle-Generation-Record`,
    `Workflow-Work-Item`). A **recovered**-role commit (WF8c (c)/(b)) must
    touch only `WORKFLOW_STATE.json`, its own field changes must be a
    non-empty subset of `{phase, state_revision, last_transition,
    implementation_review_stages}` -- `reviewed_implementation_head`/
    `implementation_revision` must be byte-identical to its parent -- its
    committed `phase` must be a member of
    `bundle_generation_recovered_role_legal_committed_phases(
    governing_workflow_version)` (a single-valued
    `{AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW}` for `"1"`/`"2.1"`,
    byte-identical to before CP3; the three-phase `"2.2"` set otherwise)
    and its parent's committed `phase` must be one of the (additively
    widened, workflow-2.5.0 CP3) legal recovered-role source phases, and
    it must carry exactly the three-trailer recovered set
    (`Workflow-Bundle-Generation-Record`, `Workflow-Work-Item`,
    `Workflow-Supersedes`). A commit whose trailer set matches neither
    role's exact shape is rejected outright, naming the offending trailer
    set, never silently coerced into either role."""


class MalformedProvenanceSupersessionChainError(Exception):
    """Raised when a provenance interval contains more than one
    `Workflow-Bundle-Generation-Record` commit for the same
    `(work_item_id, implementation_revision)` pair whose roles/
    `Workflow-Supersedes` edges don't form a clean chronological chain
    (D-Commit-Provenance "Multiple sequential recoveries / supersession
    chain"): an **ordinary**-role member must be the chain's earliest
    (chronologically first) generation-record commit; every later member
    must be **recovered**-role and its `Workflow-Supersedes` trailer must
    name exactly the immediately preceding generation-record commit found
    earlier in the same walk -- never an arbitrary earlier link, never a
    second ordinary-role member, never a missing/mismatched supersession
    edge. Names the offending commit and what was expected."""


class RemediationChildAlreadyExistsError(Exception):
    """Raised when `create_remediation_child_work_item`'s deterministically
    derived `<parent>-remediation-<n>` id already names an existing entry
    -- should not happen given the function's own internal, gap-free
    numbering, so this is a defensive guard against a corrupted/hand-edited
    state file, not a normal control-flow path (D-Functional-Remediation)."""


class IncompleteChildWorkItemError(Exception):
    """Raised by `complete_work_item` when at least one work item names the
    target as its `parent_work_item_id` and has not itself reached
    `MILESTONE_COMPLETE` -- D-Functional-Remediation's "parent acceptance
    blocks on an incomplete child" rule (resolves `GPT-R9-016`, `WFR-35`).
    Names every blocking child, never just the first."""


class DanglingParentWorkItemError(Exception):
    """Raised when a work item's `parent_work_item_id` names an id absent
    from `work_items` -- a corrupted or hand-edited state file, since every
    writer that sets `parent_work_item_id`
    (`create_remediation_child_work_item`) also creates the parent-naming
    entry in the same state dict."""


# ---------------------------------------------------------------------------
# D-Scoped-Remediation-Acceptance (new, resolves `WF8B-002`; hardened,
# resolves `GPT-R36-001`/`-002`/`-003`, `GPT-R37-001`/`-002`/`-003`/`-004`,
# `GPT-R38-001`/`-002`/`-003`, `GPT-R39-001`/`-002`, `GPT-R40-001`/`-002`):
# the non-terminal acceptance path for continued-scope remediation landed
# as extra scope on an already-COMPLETE checkpoint while the work item's
# own last checkpoint is still outstanding.
# ---------------------------------------------------------------------------


class RegistryCoverageError(Exception):
    """`complete_work_item`'s fail-closed own-registry resolution guard
    (revision 24, `GPT-R37-001`, correcting revision 23's `GPT-R36-001`
    fix, which was fail-closed only against a caller-supplied dict, not
    against the trust gap of accepting one at all): the work item's own
    declared `registry_path` must resolve via the shared tracked-metadata
    safe-path validator, exist, be readable, parse as a JSON object, and
    declare this exact work item's own `work_item_id` -- any failure
    refuses outright rather than silently treating an unresolvable or
    foreign registry as "nothing to check.\""""


class StalePlanApprovalRegistryReadError(Exception):
    """`resolve_own_registry_completion_status`'s fail-closed binding of
    registry-derived terminality to the current plan approval (revision
    27, `GPT-R43-002`, correcting `RegistryCoverageError`'s own gap: that
    check proves the registry is safe/well-formed/self-declared, never
    that its *bytes* are the ones `plan_approval` actually covers). Raised
    when `plan_approval` is missing or not `CURRENT`, when `registry_path`
    is not named in `plan_approval.review_content_manifest` at all, or
    when the registry's live `git hash-object` blob disagrees with that
    manifest's recorded blob for the same path -- the last case catches
    both a dirty tracked registry edited after approval and a clean,
    committed-but-unapproved registry mutation alike, since `git
    hash-object` always reads the working tree's current bytes regardless
    of commit status. Registry-derived terminality (`complete_work_item`,
    and the advisory pre-flight `/accept-milestone` names) must never be
    trusted while this would raise."""


class IncompleteOwnCheckpointsError(Exception):
    """`complete_work_item`'s own-checkpoint-completion block
    (`D-Scoped-Remediation-Acceptance`, resolves `WF8B-002`): raised when
    the work item's own registry has an incomplete checkpoint --
    `MILESTONE_COMPLETE` is unreachable until every one of the item's own
    checkpoints is `COMPLETE`, independent of, and in addition to, the
    pre-existing `incomplete_children` check. Names the outstanding
    checkpoint, and the supported ways forward: `/milestone-implement` for
    a checkpoint still in this milestone's scope, or
    `/apply-functional-review`'s bounded branch (same scope) / broad branch
    (a remediation child work item) for a functional-review finding. It
    deliberately does **not** name `/accept-scoped-remediation`, which was
    retired as an unreachable dead contract (ledger `I10`)."""


class AmbiguousFunctionalChecklistTrailerError(Exception):
    """Raised when more than one commit in range carries the same
    `Workflow-Functional-Checklist`/`Workflow-Work-Item` trailer pair and
    the first-parent-ancestor tie-break does not resolve to exactly one --
    a genuine identical-content duplicate reachable by more than one
    first-parent path (e.g. a rebase/cherry-pick), never a routine outcome
    of an ordinary content revision (revision 26, `GPT-R39-001`: the
    trailer value embeds the checklist's own committed blob, so a real
    content revision is a distinct key outright)."""


class NonFirstParentFunctionalChecklistEvidenceError(Exception):
    """Raised by `discover_current_functional_checklist_evidence` (revision
    27 correction, `GPT-R41-002`) when evidence commits for the exact live
    round exist somewhere in `base_commit..head`, but none sits on `head`'s
    first-parent chain -- e.g. a merged side branch whose own evidence
    commit was never carried onto the resulting branch's first-parent line.
    Distinct from "no evidence commit exists for this round at all", which
    `discover_current_functional_checklist_evidence` reports by returning
    `None`: here evidence was genuinely prepared, it just never became a
    first-parent workflow transition, so the remedy is not "run
    /prepare-functional-review" but an explicit re-provenance action
    (re-commit the evidence directly on the first-parent line). Never
    silently resolved by ordinary reachable-history order -- that would let
    side-branch evidence become authoritative without ever appearing as a
    first-parent transition."""


def _run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout


def _run_bytes(args: list[str], cwd: Path) -> bytes:
    """Binary-safe counterpart of `_run` -- `git cat-file -p` on a blob
    must never go through `text=True`'s own decode, since
    `load_pre_amendment_snapshot` (workflow-2.4.0) needs the exact bytes a
    Git blob holds, before this function's own caller decodes them."""
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True)
    return result.stdout


def _load_json(path: Path):
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptJsonError(f"{path}: {exc}") from exc


# ---------------------------------------------------------------------------
# D1: the state-writer serialization primitive (missing-test item 354(a),
# `GPT-R74-002`/`GPT-R76-002`/`GPT-R76-003`). `.ai-review/runtime/
# WORKFLOW_STATE.lock` is the repository-scoped `fcntl.flock(LOCK_EX)`
# every production writer of `WORKFLOW_STATE.json` holds across its
# complete read -> mutate -> canonical-serialize -> atomic-publish
# critical section. Revision 58's persistent `os.link` lock with
# owner-token takeover is superseded and is deliberately not
# reintroduced here: it was reproduced losing an update (writer A took
# the lock and read `{a:0,b:0}`, ownership was taken over, writer B read
# and published `{a:0,b:1}`, and the still-live A then published its
# stale `{a:1,b:0}`), because a generic state writer has no journal, no
# owner-progress record and no mutation guard, and therefore no
# mechanical way to tell a crashed holder from a paused one. This
# primitive contains no takeover, no owner token and no user
# authorization: a live holder is never displaced (a second writer
# blocks until the first releases) and a crashed holder's lock is
# released by the kernel the moment its file descriptor closes.
# ---------------------------------------------------------------------------

STATE_LOCK_PATH = Path(".ai-review/runtime/WORKFLOW_STATE.lock")


class StateLockReentrancyError(Exception):
    """Raised instead of deadlocking when the current process already
    holds `WORKFLOW_STATE.lock` and attempts to acquire it again. A
    generic `fcntl.flock(LOCK_EX)` blocks a second acquisition on the
    same inode regardless of whether it comes from this process or a
    foreign one (it serializes by open file description, not by pid), so
    an unguarded nested `state_lock()` call would hang this process
    against itself forever rather than fail -- item 354(b)'s "nested
    state writes are asserted to raise an explicit re-entrancy refusal
    rather than deadlock"."""


def state_lock_path(repo_root: Path, path: Path = STATE_LOCK_PATH) -> Path:
    return repo_root / path


# Process-local reentrancy guard, keyed by the resolved lock file's own
# path string. Not thread-local: nothing in this module's own design
# calls into `state_lock`/`state_transaction` from more than one thread
# of the same process -- every concurrency scenario item 354(b) requires
# is exercised with real, separate *processes*.
_state_lock_held: set[str] = set()


@contextlib.contextmanager
def state_lock(repo_root: Path, *, lock_path: Path = STATE_LOCK_PATH):
    """`D1`'s state-writer primitive (item 354(a)): the lock file is
    opened `O_CREAT | O_RDWR` and held under `fcntl.flock(LOCK_EX)` for
    the whole `with` block, released by `LOCK_UN`/close on every exit
    path including refusals and exceptions -- callers are required to
    perform their **complete** read -> mutate -> canonical-serialize ->
    atomic-publish sequence inside this block (`state_transaction` below
    is the documented, reusable way to do that). Blocking: a contending
    writer waits rather than fails. See the module section header above
    for why no takeover/owner-token mechanism exists here."""
    full = state_lock_path(repo_root, lock_path)
    key = str(full)
    if key in _state_lock_held:
        raise StateLockReentrancyError(
            f"{full} is already held by this process -- nested state "
            f"writes are refused rather than deadlocked"
        )
    full.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(full, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    _state_lock_held.add(key)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        _state_lock_held.discard(key)


def _serialize_state(state: dict) -> bytes:
    """The one canonical `WORKFLOW_STATE.json` serialization (`WFR-63`
    item 353, `GPT-R73-002`; corrected in place this round -- see below):
    2-space indent, insertion key order, `ensure_ascii=False` (deliberately
    chosen, not the `json.dumps` default it replaces -- this repository's
    live file already carries prose with non-ASCII characters, e.g.
    `→`/`—`, as literal UTF-8 bytes, never escaped `\\uXXXX` sequences),
    and a trailing newline. The single source of truth for both production
    publication (`_publish_state_file`) and the hermetic test fixture that
    writes `WORKFLOW_STATE.json` directly (`_commit_state_only`), so the two
    can never disagree -- unlike `WORKTREE_IDENTITY.json`, this file is not
    `sort_keys=True`. `state_transaction` (item 354) is the single required
    entry point for every one of the twelve production writers `D1` names,
    and it always publishes through this function, so this is the one
    place the canonical form needs stating for the "every writer" property
    item 353 requires -- never twelve separately-audited call sites.

    Previously `ensure_ascii=True` (own docstring self-contradictorily
    called this "not the json.dumps default-by-accident it replaces" while
    setting the exact value that default already is) -- confirmed a
    genuine bug, not a deliberate choice: `docs/ai-workflow/WORKFLOW_V2_PLAN.md`
    states `ensure_ascii=False` as canonical consistently across every
    revision from `GPT-R73-002` (revision 56) through revision 90 (its own
    "Bootstrap plan-approval procedure" pseudocode, `D1`'s state-writer
    discipline paragraph, and item 353 itself all agree), with no later
    revision ever reversing it. Harmless today only because the live file
    currently carries zero non-ASCII bytes (both forms produce identical
    output on pure-ASCII content) -- fixed here before any future non-ASCII
    write would have silently diverged from the plan's own canonical form."""
    return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _publish_state_file(full_path: Path, state: dict) -> None:
    """The canonical serialization + single `os.replace` publication for
    `WORKFLOW_STATE.json`: a same-directory temp file, written whole and
    `fsync`ed, then renamed over the target in one atomic step -- the
    same shape `_publish_worktree_identity` uses."""
    full_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(full_path.parent), prefix=f".{full_path.name}-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_serialize_state(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, full_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _assert_technical_review_block_pins_monotonic(previous_state: dict, new_state: dict) -> None:
    """D2a's durability enforcement (revision 40, `GPT-R56-004`): every
    `technical_review_block_pins` entry present in the freshly re-read
    pre-mutation state must remain present and field-identical in the
    mutator's output, for every work item, with the only legal difference
    being the append of zero or one new, previously-unseen pin (by
    `bundle_id`). Wired into `state_transaction` itself (D1's sole write
    choke point, item 354) rather than only into
    `record_technical_review_block_pin`'s own call site, so "append-only,
    permanent, never cleared" is an enforced invariant of every write, not
    a prose claim a different mutator could quietly violate."""
    prev_work_items = (previous_state or {}).get("work_items") or {}
    new_work_items = (new_state or {}).get("work_items") or {}
    for work_item_id in set(prev_work_items) | set(new_work_items):
        prev_work_item = prev_work_items.get(work_item_id) or {}
        prev_pins = prev_work_item.get("technical_review_block_pins") or []
        new_work_item = new_work_items.get(work_item_id)
        if new_work_item is None:
            if prev_pins:
                raise PinLedgerMonotonicityError(
                    f"{work_item_id}: technical_review_block_pins had {len(prev_pins)} "
                    f"entr{'y' if len(prev_pins) == 1 else 'ies'} in the pre-state but the "
                    f"work item is absent from the candidate state"
                )
            continue
        new_pins = new_work_item.get("technical_review_block_pins") or []
        new_by_bundle = {pin.get("bundle_id"): pin for pin in new_pins}
        for prev_pin in prev_pins:
            bundle_id = prev_pin.get("bundle_id")
            new_pin = new_by_bundle.get(bundle_id)
            if new_pin is None:
                raise PinLedgerMonotonicityError(
                    f"{work_item_id}: technical_review_block_pins entry for bundle_id "
                    f"{bundle_id!r} present in the pre-state is missing from the "
                    f"candidate state -- pins are append-only and permanent"
                )
            if new_pin != prev_pin:
                raise PinLedgerMonotonicityError(
                    f"{work_item_id}: technical_review_block_pins entry for bundle_id "
                    f"{bundle_id!r} was mutated -- pins must stay field-identical once "
                    f"written (was {prev_pin!r}, now {new_pin!r})"
                )
        added = len(new_pins) - len(prev_pins)
        if added not in (0, 1):
            raise PinLedgerMonotonicityError(
                f"{work_item_id}: technical_review_block_pins changed size by {added} "
                f"in one state write -- at most one new pin may be appended per write"
            )


def state_transaction(repo_root: Path, mutator, *, path: Path = DEFAULT_STATE_PATH) -> dict:
    """The single required entry point for every production writer of
    `WORKFLOW_STATE.json` (item 354): holds `state_lock` across the
    **complete** critical section -- re-read `path` from disk, call
    `mutator(state) -> new_state` (every existing transition helper in
    this module already has exactly this shape, so a caller passes one
    of them, partially applied), canonically serialize, atomically
    publish, then release. The re-read happens *inside* the lock, never
    before it, which is what makes this the "acquires the primitive,
    performs the read that feeds its write inside the critical section,
    and holds it through the publish" shape item 354(c)'s conformance
    requires -- the shape it must specifically reject is a writer that
    locks only around its final write while publishing a value derived
    from an earlier, unguarded read. Also enforces D2a's pin-ledger
    monotonicity (`_assert_technical_review_block_pins_monotonic`) against
    every candidate before it is ever published, for every caller, whether
    or not this particular write touches that field."""
    full_path = repo_root / path
    with state_lock(repo_root):
        state = _load_json(full_path) or {}
        new_state = mutator(state)
        _assert_technical_review_block_pins_monotonic(state, new_state)
        _publish_state_file(full_path, new_state)
    return new_state


# ---------------------------------------------------------------------------
# D-Commit-Provenance: exact, scoped, ancestry-limited trailer search
# ---------------------------------------------------------------------------


def _commit_trailers(repo_root: Path, commit: str) -> dict[str, str]:
    """Parse a commit's trailers via `git interpret-trailers`, the same
    plain-Git-metadata mechanism D-Commit-Provenance relies on -- never a
    substring `git log --grep` match."""
    body = _run(["git", "log", "-1", "--format=%B", commit], cwd=repo_root)
    parsed = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        cwd=repo_root, input=body, capture_output=True, text=True, check=True,
    ).stdout
    trailers: dict[str, str] = {}
    for line in parsed.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        trailers[key.strip()] = value.strip()
    return trailers


def _first_parent_commits_ordered(repo_root: Path, head: str = "HEAD") -> list[str]:
    """First-parent ancestors of `head`, newest first (git log's natural
    order) -- used both for the tie-break's ancestor-membership test and
    for "most recent by first-parent ancestry" event search."""
    out = _run(["git", "log", "--first-parent", "--format=%H", head], cwd=repo_root)
    return [line for line in out.splitlines() if line]


def _ambiguous_trailer_recovery_hint(trailer_key: str) -> str:
    """OPUS-R129-M02: the ambiguity errors previously advertised "a
    Workflow-Supersedes trailer or an explicit WORKFLOW_STATE.json
    annotation" for every trailer family alike. Neither is actually
    implemented for `Workflow-Checkpoint`/`Workflow-Plan-Approval`/
    `Workflow-Technical-Approval`: `Workflow-Supersedes` is honoured only
    inside `discover_bundle_generation_record_commits`'s own
    `_bundle_generation_record_chain_tip` verification, and no
    `WORKFLOW_STATE.json` annotation reader exists anywhere in this
    module for any family. Naming a recovery mechanism that does not
    exist sends an operator hitting the error to a dead end -- this
    returns an accurate hint per family instead."""
    if trailer_key == "Workflow-Bundle-Generation-Record":
        return (
            "needs a Workflow-Supersedes trailer (the only recovery mechanism this "
            "trailer family honors -- see _bundle_generation_record_chain_tip / "
            "/recover-implementation-provenance)"
        )
    return (
        "has no automated recovery mechanism for this trailer family -- resolve manually "
        "with a corrective forward commit that produces exactly one verified survivor "
        "(history rewriting is not available; OPUS-R129-001's own remediation is a worked "
        "example)"
    )


def _discover_trailer_commits(
    repo_root: Path, trailer_key: str, work_item_id: str, base_commit: str,
    head: str, *, ambiguous_error_cls: type[Exception],
    verify: Callable[[Path, str, str, list[str]], bool] | None = None,
) -> dict[str, str]:
    """Generic D-Commit-Provenance search shared by checkpoint-, approval-,
    and bundle-generation-record-trailer discovery (WF4a-iii generalizes
    the WF1a checkpoint-only search): every commit reachable in
    `base_commit..head` carrying an exact `trailer_key: <value>` +
    `Workflow-Work-Item: <work_item_id>` trailer pair, requiring exactly
    one match per trailer value after two filters applied in order: (1)
    prefer a first-parent ancestor of `head`; (2) if more than one
    first-parent-ancestor candidate still remains, an optional
    caller-supplied `verify(repo_root, commit, value, candidates) -> bool`
    role-specific predicate narrows further -- exactly one verified
    survivor resolves, zero or more than one is still genuine ambiguity.
    `candidates` is the full first-parent-ancestor candidate list for this
    `value` (itself, included), letting a predicate reason about sibling
    candidates together, not just the one commit being tested --
    `_bundle_generation_record_chain_tip` (WF8c (c)/(b)) needs this to
    resolve a supersession chain's tip; `_checkpoint_commit_claims_complete`
    ignores it, evaluating each candidate independently. This helper stays
    role-neutral by design: `verify` is `None` for every call site except
    `discover_checkpoint_commits` and `discover_bundle_generation_record_commits`,
    which alone know what "this candidate is the real one" means for their
    own trailer (item 39; WF8c (c)/(b)). Returns `{trailer_value:
    commit_sha}`. Genuine ambiguity (more than one candidate survives both
    filters, or filter (1) alone already leaves more than one with no
    `verify` to break the tie) raises `ambiguous_error_cls` rather than
    silently picking (resolves OPUS-R6-022; missing-test items 39, 50, 62,
    64).

    `verify` is a **tie-break only, never a precondition on the resolved
    commit** (`OPUS-R129-M03`): both short-circuits above (`len(candidates)
    == 1` and `len(tie_broken) == 1`) resolve before `verify` is ever
    consulted, so a single-candidate value resolves even when its own
    committed state does not record whatever `verify` would have checked.
    `WF0` is the live instance this is true and expected for (its
    completion commit predates `WORKFLOW_STATE.json`'s own existence, so
    no committed state could ever record it `COMPLETE`) -- `WF0` still
    resolves at 1 candidate / 0 verified, correctly. The property is
    general, not `WF0`-specific, and deliberately not tightened into a
    post-resolution assertion here: doing so would need an explicit
    legacy exemption for `WF0` (and any future single-commit, pre-state
    checkpoint), which is a real design decision belonging to whichever
    change actually needs `verify` to be more than a tie-break, not a
    silent side effect of this docstring."""
    out = _run(["git", "log", "--format=%H", f"{base_commit}..{head}"], cwd=repo_root)
    commits = [line for line in out.splitlines() if line]

    matches_by_value: dict[str, list[str]] = {}
    for commit in commits:
        trailers = _commit_trailers(repo_root, commit)
        if trailers.get("Workflow-Work-Item") != work_item_id:
            continue
        value = trailers.get(trailer_key)
        if not value:
            continue
        matches_by_value.setdefault(value, []).append(commit)

    first_parent = set(_first_parent_commits_ordered(repo_root, head))
    resolved: dict[str, str] = {}
    for value, candidates in matches_by_value.items():
        if len(candidates) == 1:
            resolved[value] = candidates[0]
            continue
        tie_broken = [c for c in candidates if c in first_parent]
        if len(tie_broken) == 1:
            resolved[value] = tie_broken[0]
            continue
        if len(tie_broken) > 1 and verify is not None:
            verified = [c for c in tie_broken if verify(repo_root, c, value, tie_broken)]
            if len(verified) == 1:
                resolved[value] = verified[0]
                continue
            raise ambiguous_error_cls(
                f"{trailer_key} {value!r} for work item {work_item_id!r} has "
                f"{len(candidates)} trailer matches in {base_commit}..{head}, "
                f"{len(tie_broken)} first-parent-ancestor candidates, and "
                f"{len(verified)} that pass role-specific verification "
                f"(candidates: {candidates}); {_ambiguous_trailer_recovery_hint(trailer_key)}"
            )
        raise ambiguous_error_cls(
            f"{trailer_key} {value!r} for work item {work_item_id!r} has "
            f"{len(candidates)} trailer matches in {base_commit}..{head}, and "
            f"{len(tie_broken)} remain after the first-parent-ancestor "
            f"tie-break (candidates: {candidates}); {_ambiguous_trailer_recovery_hint(trailer_key)}"
        )
    return resolved


def _checkpoint_commit_claims_complete(
    repo_root: Path, commit: str, checkpoint_id: str, work_item_id: str,
) -> bool:
    """Role-specific verification predicate for checkpoint-trailer
    tie-breaking (item 39): whether `commit`'s own *committed*
    `WORKFLOW_STATE.json` -- never live/worktree state -- records
    `checkpoint_id` as `COMPLETE` for `work_item_id`. Reuses
    `_read_json_at_commit_or_empty`, the same committed-state inspection
    mechanism `_work_item_field_diff`/bundle-generation validation already
    rely on, rather than trusting anything the working tree currently
    says."""
    state = _read_json_at_commit_or_empty(repo_root, commit, DEFAULT_STATE_PATH.as_posix())
    entry = state.get("work_items", {}).get(work_item_id, {}).get("checkpoints", {}).get(checkpoint_id, {})
    return entry.get("status") == "COMPLETE"


def discover_checkpoint_commits(
    repo_root: Path, work_item_id: str, base_commit: str, head: str = "HEAD",
) -> dict[str, str]:
    """Full D-Commit-Provenance search: every commit reachable in
    `base_commit..head` carrying an exact `Workflow-Checkpoint: <id>` +
    `Workflow-Work-Item: <work_item_id>` trailer pair, requiring exactly
    one match per checkpoint id after the stated two-filter tie-break:
    (1) prefer a first-parent ancestor of `head`; (2) if more than one
    first-parent-ancestor candidate remains, prefer the one whose own
    committed `WORKFLOW_STATE.json` records this checkpoint `COMPLETE`
    for `work_item_id` (`_checkpoint_commit_claims_complete`, item 39).
    Returns `{checkpoint_id: commit_sha}`. Genuine ambiguity (more than
    one candidate survives both filters, or the result remains
    undecidable) raises rather than silently picking (resolves
    OPUS-R6-022; missing-test items 39, 50, 62, 64)."""
    return _discover_trailer_commits(
        repo_root, "Workflow-Checkpoint", work_item_id, base_commit, head,
        ambiguous_error_cls=AmbiguousCheckpointTrailerError,
        verify=lambda root, commit, checkpoint_id, _candidates: _checkpoint_commit_claims_complete(
            root, commit, checkpoint_id, work_item_id,
        ),
    )


def discover_approval_commits(
    repo_root: Path, trailer_key: str, work_item_id: str, base_commit: str, head: str = "HEAD",
) -> dict[str, str]:
    """The approval-trailer counterpart of `discover_checkpoint_commits`
    (WF4a-iii, resolves `OPUS-R6-022` for approval commits too): searches
    for `trailer_key` (`"Workflow-Plan-Approval"` or
    `"Workflow-Technical-Approval"`) instead of `"Workflow-Checkpoint"`.
    Returns `{review_content_id: commit_sha}` -- a work item can carry more
    than one approval commit per stage over its lifetime (an approval that
    later staled and was re-approved after a plan revision), each keyed by
    the distinct `review_content_id` it approved."""
    return _discover_trailer_commits(
        repo_root, trailer_key, work_item_id, base_commit, head,
        ambiguous_error_cls=AmbiguousApprovalTrailerError,
    )


def discover_plan_approval_commit(
    repo_root: Path, work_item_id: str, review_content_id: str, base_commit: str, head: str = "HEAD",
) -> str | None:
    """The specific plan-approval commit carrying
    `Workflow-Plan-Approval: <review_content_id>` for this work item, or
    `None` if none is reachable."""
    matches = discover_approval_commits(repo_root, "Workflow-Plan-Approval", work_item_id, base_commit, head)
    return matches.get(review_content_id)


def discover_technical_approval_commit(
    repo_root: Path, work_item_id: str, review_content_id: str, base_commit: str, head: str = "HEAD",
) -> str | None:
    """The specific technical-approval commit carrying
    `Workflow-Technical-Approval: <review_content_id>` for this work item,
    or `None` if none is reachable."""
    matches = discover_approval_commits(repo_root, "Workflow-Technical-Approval", work_item_id, base_commit, head)
    return matches.get(review_content_id)


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """Whether `ancestor` is `descendant` itself or a (non-first-parent-
    restricted) ancestor of it -- used by `implementing_entry_reachable`
    to confirm current HEAD is the plan-approval commit or a checkpoint-
    commit descendant of it (D-Approval-Commits)."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root, capture_output=True,
    )
    return result.returncode == 0


def load_pre_amendment_snapshot(
    repo_root: Path, work_item_id: str, plan_path: str, registry_path: str, entry: dict,
) -> tuple[str, dict]:
    """workflow-2.4.0, D-Plan-Amendment-3's bounded, content-addressed
    reference model (EXT-R6-I1): given one `amendment_history` entry,
    reads the pinned `blob` SHA for `plan_path`/`registry_path` out of
    `entry["superseded_plan_approval"]["review_content_manifest"]` (a
    lookup by `path`, never a re-derivation of the manifest), retrieves
    each blob's bytes via `git cat-file -p <blob>`, and cross-checks each
    retrieved blob is still reachable from
    `entry["pre_amendment_approval_commit"]` via `git ls-tree
    <pre_amendment_approval_commit> -- <path>` reporting the identical SHA,
    before decoding. Returns `(pre_plan_text, pre_registry)` -- the plan
    bytes decoded as UTF-8, the registry bytes parsed as JSON.

    Raises `AmendmentPreSnapshotUnreproducibleError`, naming the path and
    the blob SHA it could not reproduce, rather than silently substituting
    empty content or crashing on an unhandled `git` failure."""
    manifest = entry.get("superseded_plan_approval", {}).get("review_content_manifest") or []
    manifest_by_path = {m.get("path"): m.get("blob") for m in manifest if isinstance(m, dict)}
    commit = entry.get("pre_amendment_approval_commit")

    def _resolve_blob(path: str) -> bytes:
        blob = manifest_by_path.get(path)
        if not blob:
            raise AmendmentPreSnapshotUnreproducibleError(
                f"{work_item_id}: {path!r} has no pinned blob in this amendment's own "
                f"superseded_plan_approval.review_content_manifest"
            )
        try:
            ls_tree_out = _run(["git", "ls-tree", commit, "--", path], cwd=repo_root)
        except subprocess.CalledProcessError as exc:
            raise AmendmentPreSnapshotUnreproducibleError(
                f"{work_item_id}: could not run 'git ls-tree {commit} -- {path}' to "
                f"cross-check pinned blob {blob}: {exc}"
            ) from exc
        fields = ls_tree_out.strip().split(None, 3)
        found_blob = fields[2] if len(fields) >= 3 else None
        if found_blob != blob:
            raise AmendmentPreSnapshotUnreproducibleError(
                f"{work_item_id}: pinned blob {blob} for {path!r} is not reachable from "
                f"pre_amendment_approval_commit {commit} (git ls-tree reports {found_blob!r})"
            )
        try:
            return _run_bytes(["git", "cat-file", "-p", blob], cwd=repo_root)
        except subprocess.CalledProcessError as exc:
            raise AmendmentPreSnapshotUnreproducibleError(
                f"{work_item_id}: could not retrieve blob {blob} for {path!r} via "
                f"'git cat-file -p': {exc}"
            ) from exc

    plan_bytes = _resolve_blob(str(plan_path))
    registry_bytes = _resolve_blob(str(registry_path))
    try:
        pre_plan_text = plan_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AmendmentPreSnapshotUnreproducibleError(
            f"{work_item_id}: {plan_path!r}'s pinned blob is not valid UTF-8"
        ) from exc
    try:
        pre_registry = json.loads(registry_bytes)
    except json.JSONDecodeError as exc:
        raise AmendmentPreSnapshotUnreproducibleError(
            f"{work_item_id}: {registry_path!r}'s pinned blob is not valid JSON"
        ) from exc
    return pre_plan_text, pre_registry


def verify_checkpoint_completions(
    work_item: dict, repo_root: Path, base_commit: str, head: str = "HEAD",
) -> None:
    """D3's "completion without a reachable commit" validator-reject rule:
    every checkpoint recorded `COMPLETE` in state must have a reachable,
    exactly-matching trailer commit."""
    work_item_id = work_item["work_item_id"]
    discovered = discover_checkpoint_commits(repo_root, work_item_id, base_commit, head)
    for checkpoint_id, entry in work_item.get("checkpoints", {}).items():
        if entry.get("status") == "COMPLETE" and checkpoint_id not in discovered:
            raise CheckpointNotReachableError(
                f"{work_item_id}/{checkpoint_id} is COMPLETE in state but no "
                f"commit in {base_commit}..{head} carries a matching "
                f"Workflow-Checkpoint/Workflow-Work-Item trailer pair"
            )


# ---------------------------------------------------------------------------
# WF4a-iii: D-States' "Recomputation rule, stated per stage" and
# D-Approval-Commits' IMPLEMENTING entry condition (resolves OPUS-R6-003;
# missing-test items 9, 10, 11)
# ---------------------------------------------------------------------------


def approval_review_content_id(
    repo_root: Path, *, stage: str, base_commit: str, work_item_type: str,
    work_item_id: str, head: str = "HEAD", artifacts_path: Path,
) -> str:
    """Recomputes the current `review_content_id` at `head` for the given
    approval `stage`, commit-source (never worktree-source -- this checks
    committed content, exactly what a fresh session sees, never
    uncommitted local edits). D-States' "Recomputation rule, stated per
    stage": `stage="plan"` hashes only the plan-stage projection
    (protected plan/audit/decisions documents), invariant under checkpoint
    commits because those never touch those documents; `stage=
    "implementation"` hashes the implementation-stage projection (source/
    test/build/migration/workflow-command files, WF4a-i's scope), loaded
    from the tracked artifact-declarations file rather than adapted from
    the plan-stage sets (`OPUS-R20-003`: the two stages' sets are
    near-inverses, never derived from one another).

    `stage="plan"` no longer accepts a `plan_revision` parameter
    (`OPUS-R25-002`, `D-Fingerprint-Generalization`): the resolved value
    comes from `fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item`'s
    own call into `resolve_plan_stage_metadata`, sourcing
    `work_item_type`/`plan_revision`/the protected-path sets entirely from
    `WORKFLOW_STATE.json`/`<work_item_id>-artifacts.json` -- never a
    caller-supplied literal naming any other work item's identity.
    `artifacts_path` is required (no default -- `DEFAULT_ARTIFACTS_PATH`
    is retired as a live default here) and used only for
    `stage="implementation"`; both actual callers
    (`approval_is_current`/`verify_post_approval_manifest_match`) resolve
    it per work item via `fingerprint.artifacts_path_for_work_item(work_item_id)`
    (`OPUS-R27-002`). `implementation_revision` is never part of either
    projection (D-States: "meaningless before implementation starts and
    mutating during it")."""
    if stage == "plan":
        digest, _ = fingerprint.compute_review_content_id_plan_stage_at_commit_for_work_item(
            repo_root, work_item_id, head, base=base_commit,
        )
        return digest
    if stage == "implementation":
        impl_protected_paths, impl_protected_prefixes, impl_excluded_paths, impl_excluded_prefixes = (
            fingerprint.load_implementation_stage_classification(repo_root, artifacts_path)
        )
        digest, _ = fingerprint.compute_review_content_id_implementation_stage_at_commit(
            repo_root, base_commit, head, work_item_type, work_item_id,
            impl_protected_paths, impl_protected_prefixes, impl_excluded_paths, impl_excluded_prefixes,
        )
        return digest
    raise InvalidApprovalRecordError(f"unknown approval stage: {stage!r}")


def approval_is_current(
    repo_root: Path, work_item: dict, *, stage: str, base_commit: str, head: str = "HEAD",
) -> bool:
    """D2/D-States durability check for either approval record: `status ==
    CURRENT` plus a freshly recomputed, stage-appropriate
    `review_content_id` matching `approved_review_content_id` exactly.
    `False` whenever no record exists yet or its status is already
    `STALE` -- this function only ever detects fresh staleness, it never
    clears a status a caller previously set. Missing-test item 10: a
    plan-document edit after a checkpoint commit changes the plan-stage
    projection, so this returns `False`. Missing-test item 11: an
    `implementation_revision` bump touches no hashed field of either
    projection, so this keeps returning `True`. Missing-test item 150: a
    second work item's own `plan_approval`/`technical_approval` record
    gates independently of `workflow-v2-1-core`'s (`D-Fingerprint-
    Generalization`)."""
    record_field = "plan_approval" if stage == "plan" else "technical_approval"
    record = work_item.get(record_field)
    if record is None or record.get("status") != "CURRENT":
        return False
    current_id = approval_review_content_id(
        repo_root, stage=stage, base_commit=base_commit,
        work_item_type=work_item["work_item_type"], work_item_id=work_item["work_item_id"],
        head=head,
        artifacts_path=fingerprint.artifacts_path_for_work_item(work_item["work_item_id"]),
    )
    return current_id == record["approved_review_content_id"]


def implementing_entry_reachable(
    repo_root: Path, work_item: dict, base_commit: str, head: str = "HEAD",
) -> bool:
    """D-Approval-Commits' `IMPLEMENTING` entry condition, in full:
    current HEAD (derived live) is the plan-approval commit or a
    checkpoint-commit descendant of it, `plan_approval.status ==
    CURRENT`, and a freshly recomputed plan-stage `review_content_id`
    matches `plan_approval.approved_review_content_id` (missing-test item
    9: true at checkpoints 1, 2, and N in a fresh session, since the
    plan-approval commit stays a first-ancestor-chain ancestor of every
    later checkpoint commit and the plan-stage projection stays
    unchanged by them)."""
    plan_approval = work_item.get("plan_approval")
    if plan_approval is None or plan_approval.get("status") != "CURRENT":
        return False
    approval_commit = discover_plan_approval_commit(
        repo_root, work_item["work_item_id"],
        plan_approval["approved_review_content_id"], base_commit, head,
    )
    if approval_commit is None or not _is_ancestor(repo_root, approval_commit, head):
        return False
    return approval_is_current(repo_root, work_item, stage="plan", base_commit=base_commit, head=head)


def verify_post_approval_manifest_match(
    repo_root: Path, work_item: dict, *, stage: str, base_commit: str, commit: str,
) -> None:
    """WFR-06: "the committed plan exactly matches the reviewed working-
    tree content after the plan-approval commit", generalized to either
    approval stage. Recomputes `review_content_id` from `commit`'s own
    committed content and asserts it equals the approval record's
    `approved_review_content_id` exactly -- run once, immediately after
    `/approve-review` creates the commit, never trusted on the record's
    word alone."""
    record_field = "plan_approval" if stage == "plan" else "technical_approval"
    record = work_item[record_field]
    expected = record["approved_review_content_id"]
    actual = approval_review_content_id(
        repo_root, stage=stage, base_commit=base_commit,
        work_item_type=work_item["work_item_type"], work_item_id=work_item["work_item_id"],
        head=commit,
        artifacts_path=fingerprint.artifacts_path_for_work_item(work_item["work_item_id"]),
    )
    if actual != expected:
        raise PostApprovalManifestMismatchError(
            f"{work_item['work_item_id']}/{stage}: commit {commit} recomputes to "
            f"{actual!r}, expected {expected!r} (approved_review_content_id)"
        )


# ---------------------------------------------------------------------------
# `/approve-review plan`'s generic conditional-fifth-member commit
# mechanics (missing-test item 347's "permanent `/approve-review`"
# obligation, `D-Approval-Commits`' "Conditional fifth commit member"):
# stage exactly the resolved member set, pin-then-verify the conditional
# member's identity across staging and the commit, and provide a
# deterministic rollback for a failure after the state write.
# ---------------------------------------------------------------------------


def _read_committed_bytes(repo_root: Path, commit: str, rel_path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit}:{rel_path}"], cwd=repo_root, capture_output=True, check=True,
    ).stdout


def stage_plan_approval_commit_paths(repo_root: Path, paths: tuple[str, ...]) -> None:
    """Stages exactly `paths` (`resolve_plan_stage_approval_commit_paths`'
    resolved member set -- four or five, per work item, per round) for a
    plain, pathspec-free `git commit` to pick up next. Two checks, both
    against the same `git diff --name-only --cached HEAD` view (which
    reports a path exactly when its staged content genuinely differs from
    `HEAD` -- never for an unstaged intent-to-add marker, and never for a
    re-staged, content-unchanged path, since neither produces a
    distinguishable index state to report; see
    `DirtyIndexBeforeStagingError`'s docstring):

    1. **Pre-staging index-isolation precondition**
       (`DirtyIndexBeforeStagingError`): that view must already be empty
       before this call stages anything of its own -- an unrelated path
       already staged *with real changed content* (this work item's own
       leftover, or a concurrent work item's write, D1) fails closed here,
       before a single `git add` of this call's own runs, giving a
       clearer diagnostic than discovering it only after staging (mirrors
       `D-Approval-Commits`' bootstrap transaction's own index-isolation
       precondition, revision 54, `GPT-R71-001`, generalized here). Never
       rejects this work item's own legitimate pending intent-to-add
       paths (`/milestone-plan`'s own staging step) -- confirmed
       empirically, not merely asserted.
    2. **Post-staging diff assertion** (`UnexpectedStagedPathSetError`):
       after `git add -- <paths>`, that same view must name no path
       outside `paths` -- a *subset* check, not exact equality, since a
       member whose bytes happen to already be byte-identical at `HEAD`
       legitimately produces no diff entry at all (staging it is then a
       correct no-op, not an omission); requiring every expected path to
       appear would reject a coincidentally-unchanged member for no real
       reason.

    Never `git add -A`/`git add .`."""
    dirty = _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo_root)
    already_staged = {line for line in dirty.splitlines() if line}
    if already_staged:
        raise DirtyIndexBeforeStagingError(
            f"Git index already differs from HEAD before staging began: "
            f"{sorted(already_staged)} -- resolve or unstage these first"
        )
    _run(["git", "add", "--", *paths], cwd=repo_root)
    staged = _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo_root)
    actual = {line for line in staged.splitlines() if line}
    expected = set(paths)
    unexpected = actual - expected
    if unexpected:
        raise UnexpectedStagedPathSetError(
            f"staged diff includes unexpected paths {sorted(unexpected)}, outside "
            f"the resolved approval-commit member set {sorted(expected)}"
        )


def verify_staged_blob_sha256(repo_root: Path, rel_path: str, expected_sha256: str) -> None:
    """Re-verifies the conditional fifth member's *staged* (Git index)
    content against the sha256 `resolve_plan_stage_approval_commit_paths`
    pinned right after resolving it -- call after
    `stage_plan_approval_commit_paths`, before creating the commit, to
    close the race window between resolution and staging. Raises
    `StagedBlobMismatchError` on a mismatch."""
    content = subprocess.run(
        ["git", "show", f":{rel_path}"], cwd=repo_root, capture_output=True, check=True,
    ).stdout
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_sha256:
        raise StagedBlobMismatchError(
            f"{rel_path}: staged blob sha256 {actual} does not match the sha256 "
            f"{expected_sha256} pinned before staging"
        )


def verify_committed_blob_sha256(
    repo_root: Path, commit: str, rel_path: str, expected_sha256: str,
) -> None:
    """Re-verifies the conditional fifth member's *committed* content
    against the sha256 pinned before staging -- defense in depth beyond
    `verify_post_approval_manifest_match`'s own full projection-digest
    check (which would also catch this), isolating exactly which member
    diverged if the two ever disagree. Raises `CommittedBlobMismatchError`
    on a mismatch."""
    actual = hashlib.sha256(_read_committed_bytes(repo_root, commit, rel_path)).hexdigest()
    if actual != expected_sha256:
        raise CommittedBlobMismatchError(
            f"{rel_path} at {commit}: committed blob sha256 {actual} does not "
            f"match the sha256 {expected_sha256} pinned before staging"
        )


def assert_committed_path_set_matches(
    repo_root: Path, commit: str, expected_paths: tuple[str, ...],
) -> None:
    """The structural half of item 347's committed-path-set assertion:
    `commit`'s own changed-path set relative to its sole parent
    (`git diff-tree --no-commit-id --name-only -r`) must name no path
    outside `expected_paths` -- a *subset* check, not exact equality, for
    the same reason `stage_plan_approval_commit_paths`'s own post-staging
    assertion is a subset check: an expected member whose bytes are
    byte-identical to the parent commit produces no entry in the commit's
    own diff at all (not a distinct, detectable tree state), so requiring
    every expected path to appear would reject a coincidentally-unchanged
    member for no real reason (confirmed empirically, the same way that
    same false assumption was caught and corrected for the pre-commit
    check earlier in this fix's own development --
    `test_declaration_never_committed_resolves_five_member_set` first
    caught this one too). Call immediately after the commit is created,
    alongside `verify_post_approval_manifest_match` (step 6a): this
    checks the commit's own tree shape, a hook-editing scenario the
    pre-commit staging checks alone cannot see (a pre-commit/commit-msg
    hook can still edit and re-stage a file after
    `stage_plan_approval_commit_paths` already verified the index, before
    `git commit` writes the final tree). Raises
    `CommittedPathSetMismatchError` naming the unexpected paths and the
    full expected set on a violation."""
    changed = _run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit], cwd=repo_root)
    actual = {line for line in changed.splitlines() if line}
    expected = set(expected_paths)
    unexpected = actual - expected
    if unexpected:
        raise CommittedPathSetMismatchError(
            f"commit {commit}'s own changed-path set includes unexpected paths "
            f"{sorted(unexpected)}, outside the resolved approval-commit member "
            f"set {sorted(expected)}"
        )


def rollback_plan_approval_write(
    repo_root: Path, state_path: Path, pre_write_bytes: bytes, *, commit_created: bool,
) -> None:
    """Deterministic recovery for a plan-approval attempt that fails after
    `apply_plan_approval`'s state write but before
    `verify_post_approval_manifest_match` succeeds (`/approve-review`
    step 6b): `git reset` back to the commit immediately before this
    invocation's own commit if one was created (undoing exactly that one
    commit, never an earlier one -- mixed reset, so the working tree is
    left untouched, only the branch ref and the index move), or a bare
    `git reset` (unstage back to `HEAD`) if no commit was created yet;
    then restores `state_path`'s working-tree bytes to `pre_write_bytes`
    exactly -- the bytes captured immediately before `apply_plan_approval`
    ran, this invocation's own first durable mutation. Leaves the
    repository byte-identical to its state immediately before that first
    mutation: no partial commit, no partial state write, safe to retry
    from a fresh operator session. Never touches any other file this
    invocation did not itself stage."""
    if commit_created:
        _run(["git", "reset", "HEAD~1"], cwd=repo_root)
    else:
        _run(["git", "reset", "HEAD"], cwd=repo_root)
    (repo_root / state_path).write_bytes(pre_write_bytes)


# ---------------------------------------------------------------------------
# WF8c (g), part 1: WFR-63's plan-approval failure-atomicity transaction --
# the durable journal, the ownership assertion, the three-way outcome
# classifier, and an index-only rollback (missing-test items 349/350),
# built once at the permanent site's future home
# (`.claude/commands/approve-review.md` steps 4a/6, `D-Approval-Commits`'
# "Bootstrap plan-approval procedure").
#
# Deliberately bounded to this part: no mutation/handoff guard or explicit-
# takeover contract (D-Approval-Commits' "Fencing"/"Takeover" sections --
# this journal's own exclusive `os.link` publish is already safe against
# concurrent *opening*, which is all a single-owner, non-interrupted flow
# needs; serializing later steps against each other and against a takeover
# is a distinct concern left to the command-integration follow-up that
# wires this into `/approve-review` for real -- built in part 2, below), no
# index-pinned-blob writer for `WORKFLOW_STATE.json` itself or step 8b's
# materialization (the "never write the working tree before the commit
# exists" redesign), and no amend recovery (item 347's 3-way interruption
# classification) -- both still deferred past part 2.
# `rollback_plan_approval_write` above is the OLD model (restores
# `pre_write_bytes` to the working tree) and stays exactly as it is for
# its own existing callers -- it is not reused here: this transaction
# never writes `WORKFLOW_STATE.json` to the working tree before a durable
# commit exists, so there is nothing for a rollback to restore, only a
# read-only safety assertion (`rollback_plan_approval_transaction` below).
# ---------------------------------------------------------------------------


PLAN_APPROVAL_JOURNAL_PATH = Path(".ai-review/runtime/PLAN_APPROVAL_JOURNAL.json")
PLAN_APPROVAL_JOURNAL_SCHEMA_VERSION = 3

PLAN_APPROVAL_OUTCOME_COMMITTED = "COMMITTED"
PLAN_APPROVAL_OUTCOME_NOT_COMMITTED = "NOT_COMMITTED"
PLAN_APPROVAL_OUTCOME_AMBIGUOUS = "AMBIGUOUS"


class PlanApprovalTransactionInProgressError(Exception):
    """Raised by `open_plan_approval_journal` when a journal already
    exists -- a transaction is already mid-flight for this repository
    (the journal is repository-scoped, not per-work-item: only
    `workflow-v2-1-core` ever opens one, and only one at a time).
    Refuses rather than overwriting; resuming or taking over an existing
    transaction is a distinct, explicit operation this slice does not
    yet implement."""


class PlanApprovalJournalUnavailableError(Exception):
    """Raised when the journal file exists but cannot be read/parsed, or
    does not carry the expected schema/fields -- fails closed rather
    than guessing at a torn or foreign-shaped record."""


class NoPlanApprovalTransactionError(Exception):
    """Raised by an operation that requires an open journal (ownership
    assertion, rollback) when none exists."""


class PlanApprovalOwnershipError(Exception):
    """Raised when a caller's own `owner_token` does not match the
    journal's current one -- this session is not (or is no longer) the
    transaction's owner."""


class PlanApprovalRollbackInvariantViolationError(Exception):
    """Raised by `rollback_plan_approval_transaction`'s pre-reset safety
    assertion if the live state's own entry for this work item already
    carries the transaction's own post-approval identity under `phase:
    "IMPLEMENTING"` -- that combination would mean the approval already
    took durable effect despite classification finding `NOT_COMMITTED`,
    an invariant violation this function refuses to paper over."""


class PlanApprovalRollbackVerificationError(Exception):
    """Raised by `rollback_plan_approval_transaction` when the post-reset
    repository state does not match what a genuine rollback must
    produce (a clean index, live `HEAD` back at the journal's own
    `pre_procedure_head`) -- the journal is left in place and nothing is
    reported as rolled back, rather than claiming success on an
    unverified reset."""


def plan_approval_journal_path(repo_root: Path, path: Path = PLAN_APPROVAL_JOURNAL_PATH) -> Path:
    return repo_root / path


def open_plan_approval_journal(
    repo_root: Path, *, work_item_id: str, base_commit: str, pre_state: dict,
    record: dict, approval_now: str, expected_bundle_id: str,
    expected_review_content_id: str, applicable_paths: tuple[str, ...],
    fifth_member_applies: bool, fifth_member_sha256: str | None,
    user_confirmation: str, quiescence_authorization: str,
    path: Path = PLAN_APPROVAL_JOURNAL_PATH,
    pre_registry: dict | None = None, pre_plan_text: str | None = None,
    post_registry: dict | None = None, post_plan_text: str | None = None,
) -> dict:
    """Opens the durable, crash-resumable plan-approval transaction
    journal (`WFR-63`, missing-test item 349): this invocation's own
    **first** durable mutation, called before `apply_plan_approval`/any
    Git staging. Captures everything a fresh, resuming, or rolling-back
    session needs without ever re-trusting `WORKFLOW_STATE.json`'s own
    live content for it: the exact pre-transaction commit
    (`pre_procedure_head`), the exact pre-transaction state bytes
    (`pre_procedure_state_b64`/`_sha256`, serialized via the same
    canonical `_serialize_state` every production writer uses), and --
    computed here, before any mutation, via the same pure
    `apply_plan_approval` the eventual write uses -- the exact expected
    post-approval state bytes (`expected_post_state_b64`/`_sha256`), so
    every later step verifies against a value pinned before the
    transaction began rather than recomputing it (and potentially
    drifting) along the way.

    Published by the same exclusive, no-partial-write primitive
    `D-Checkpoint-Ownership`'s `_publish_claim_exclusive` uses for
    checkpoint claims: write a complete temp file in the same directory,
    `fsync` it, then `os.link` it into place (`FileExistsError` means a
    transaction is already open --
    `PlanApprovalTransactionInProgressError`, never a silent overwrite
    or a block), then `fsync` the containing directory. There is no
    window in which a reader can observe a partially-written journal,
    and a crash between the two file operations leaves nothing at the
    final pathname at all. Gitignored (`.ai-review/`), worktree-local,
    never a `WORKFLOW_STATE.json` field.

    workflow-2.4.0, D-Plan-Amendment-4: `pre_registry`/`pre_plan_text`/
    `post_registry`/`post_plan_text` are a pure pass-through into the one
    internal `apply_plan_approval` call above -- this function performs no
    read of its own to obtain them and no amendment-specific logic; it
    stays a thin, journal-writing orchestrator exactly as it already is
    for `record`/`base_commit`/every other caller-supplied argument."""
    full_path = plan_approval_journal_path(repo_root, path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    repo_root_id, git_common_dir, worktree_root = _git_identity(repo_root)
    pre_procedure_head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
    pre_state_bytes = _serialize_state(pre_state)
    expected_post_state = apply_plan_approval(
        pre_state, work_item_id, record, approval_now,
        pre_registry=pre_registry, pre_plan_text=pre_plan_text,
        post_registry=post_registry, post_plan_text=post_plan_text,
    )
    expected_post_state_bytes = _serialize_state(expected_post_state)
    journal = {
        "schema_version": PLAN_APPROVAL_JOURNAL_SCHEMA_VERSION,
        "owner_token": secrets.token_hex(16),
        "takeover_count": 0,
        "previous_owner_tokens": [],
        "quiescence_authorization": quiescence_authorization,
        "work_item_id": work_item_id,
        "stage": "plan",
        "repo_root": repo_root_id,
        "git_common_dir": git_common_dir,
        "worktree_root": worktree_root,
        "pre_procedure_head": pre_procedure_head,
        "pre_procedure_state_b64": base64.b64encode(pre_state_bytes).decode("ascii"),
        "pre_procedure_state_sha256": hashlib.sha256(pre_state_bytes).hexdigest(),
        "base_commit": base_commit,
        "expected_bundle_id": expected_bundle_id,
        "expected_review_content_id": expected_review_content_id,
        "applicable_paths": sorted(applicable_paths),
        "fifth_member_applies": fifth_member_applies,
        "fifth_member_sha256": fifth_member_sha256,
        "user_confirmation": user_confirmation,
        "approval_now": approval_now,
        "expected_post_state_b64": base64.b64encode(expected_post_state_bytes).decode("ascii"),
        "expected_post_state_sha256": hashlib.sha256(expected_post_state_bytes).hexdigest(),
        "created_at": approval_now,
    }
    payload = (json.dumps(journal, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(full_path.parent), prefix=".plan-approval-journal-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp_name, full_path)
        except FileExistsError:
            raise PlanApprovalTransactionInProgressError(
                f"{full_path} already exists -- a plan-approval transaction is already open "
                f"for this repository; resume or take over the existing one rather than "
                f"opening a second one"
            )
        dir_fd = os.open(str(full_path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return journal


_PLAN_APPROVAL_JOURNAL_REQUIRED_STR_FIELDS = (
    "owner_token", "quiescence_authorization", "work_item_id", "stage",
    "repo_root", "git_common_dir", "worktree_root", "pre_procedure_head",
    "pre_procedure_state_b64", "pre_procedure_state_sha256", "base_commit",
    "expected_bundle_id", "expected_review_content_id", "user_confirmation",
    "approval_now", "expected_post_state_b64", "expected_post_state_sha256",
    "created_at",
)


def read_plan_approval_journal(repo_root: Path, path: Path = PLAN_APPROVAL_JOURNAL_PATH) -> dict | None:
    """Reads and validates the journal, or returns `None` if none is
    open. Fails closed (`PlanApprovalJournalUnavailableError`) on any
    unreadable, unparseable, or malformed record -- an undecidable
    journal must never be mistaken for "no transaction in progress"."""
    full_path = plan_approval_journal_path(repo_root, path)
    try:
        raw = full_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PlanApprovalJournalUnavailableError(f"cannot read {full_path} ({exc})") from exc
    try:
        journal = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanApprovalJournalUnavailableError(
            f"{full_path} could not be parsed as JSON ({exc})"
        ) from exc
    if not isinstance(journal, dict) or journal.get("schema_version") != PLAN_APPROVAL_JOURNAL_SCHEMA_VERSION:
        raise PlanApprovalJournalUnavailableError(
            f"{full_path} has an unsupported shape/schema_version "
            f"(expected {PLAN_APPROVAL_JOURNAL_SCHEMA_VERSION})"
        )
    missing = [
        field for field in _PLAN_APPROVAL_JOURNAL_REQUIRED_STR_FIELDS
        if not isinstance(journal.get(field), str)
    ]
    if not isinstance(journal.get("applicable_paths"), list):
        missing.append("applicable_paths")
    if not isinstance(journal.get("fifth_member_applies"), bool):
        missing.append("fifth_member_applies")
    takeover_count = journal.get("takeover_count")
    if not isinstance(takeover_count, int) or isinstance(takeover_count, bool):
        missing.append("takeover_count")
    previous_owner_tokens = journal.get("previous_owner_tokens")
    if not isinstance(previous_owner_tokens, list) or not all(
        isinstance(token, str) for token in previous_owner_tokens
    ):
        missing.append("previous_owner_tokens")
    if missing:
        raise PlanApprovalJournalUnavailableError(f"{full_path} is missing/malformed fields {missing}")
    return journal


def close_plan_approval_journal(repo_root: Path, path: Path = PLAN_APPROVAL_JOURNAL_PATH) -> None:
    """Deletes the journal -- idempotent, an already-absent journal is
    success. Callers close only after a transaction has genuinely
    reached a durable terminal state (a verified commit, or a verified
    rollback); this function performs no such check itself."""
    plan_approval_journal_path(repo_root, path).unlink(missing_ok=True)


def assert_plan_approval_journal_owner(
    repo_root: Path, owner_token: str, path: Path = PLAN_APPROVAL_JOURNAL_PATH,
) -> dict:
    """Re-reads the journal and requires it to be open and to name
    `owner_token` as its current owner -- the ownership check every
    mutating step of the transaction must pass immediately before it
    acts. Raises `NoPlanApprovalTransactionError` if no journal is open,
    `PlanApprovalOwnershipError` if one is open under a different
    token. Returns the freshly re-read journal."""
    journal = read_plan_approval_journal(repo_root, path)
    if journal is None:
        raise NoPlanApprovalTransactionError(
            "no plan-approval transaction is open -- nothing to assert ownership of"
        )
    if journal["owner_token"] != owner_token:
        raise PlanApprovalOwnershipError(
            f"this session's owner_token {owner_token!r} does not match the journal's "
            f"current owner_token {journal['owner_token']!r}"
        )
    return journal


def classify_plan_approval_outcome(repo_root: Path, journal: dict) -> str:
    """The three-way outcome classifier missing-test item 350 requires:
    decides whether the transaction `journal` describes actually landed,
    from durable Git state alone -- **never** from a command's exit
    status and **never** from `WORKFLOW_STATE.json`'s own live content.
    Shared by both the in-session post-commit check and a fresh/
    resuming session's own recovery classification -- both call this
    function against the same journal and get the same answer for the
    same repository state.

    - `COMMITTED`: exactly one commit carries
      `Workflow-Plan-Approval: <journal['expected_review_content_id']>`
      + `Workflow-Work-Item: <journal['work_item_id']>`, reachable in
      `journal['base_commit']..HEAD` (`discover_plan_approval_commit`,
      already `D-Commit-Provenance`'s exact/scoped/ancestry-limited
      search), and that commit's own first parent is exactly
      `journal['pre_procedure_head']` -- the approval commit this
      journal itself describes, built directly on the exact state this
      transaction pinned, nothing else.
    - `NOT_COMMITTED`: no such commit exists, and live `HEAD` is still
      exactly `journal['pre_procedure_head']` -- nothing happened, safe
      to roll back.
    - `AMBIGUOUS`: everything else -- a matching commit exists but its
      first parent disagrees (something else landed between journal-open
      and commit-creation), or no matching commit exists but `HEAD` has
      moved anyway, or the underlying trailer search itself cannot
      resolve a single commit (`AmbiguousApprovalTrailerError`). Never
      auto-resolved: a caller must stop and report, never guess."""
    try:
        commit = discover_plan_approval_commit(
            repo_root, journal["work_item_id"], journal["expected_review_content_id"],
            journal["base_commit"], head="HEAD",
        )
    except AmbiguousApprovalTrailerError:
        return PLAN_APPROVAL_OUTCOME_AMBIGUOUS
    live_head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
    if commit is None:
        if live_head == journal["pre_procedure_head"]:
            return PLAN_APPROVAL_OUTCOME_NOT_COMMITTED
        return PLAN_APPROVAL_OUTCOME_AMBIGUOUS
    first_parent = _run(["git", "rev-parse", f"{commit}^"], cwd=repo_root).strip()
    if first_parent == journal["pre_procedure_head"]:
        return PLAN_APPROVAL_OUTCOME_COMMITTED
    return PLAN_APPROVAL_OUTCOME_AMBIGUOUS


def rollback_plan_approval_transaction(
    repo_root: Path, *, owner_token: str, state_path: Path = DEFAULT_STATE_PATH,
    path: Path = PLAN_APPROVAL_JOURNAL_PATH,
) -> None:
    """The failure-atomicity transaction's own rollback (missing-test
    item 350): call only once `classify_plan_approval_outcome` has
    returned `NOT_COMMITTED` for the open journal. Writes **zero**
    `WORKFLOW_STATE.json` bytes -- unlike the older, retired-for-this-
    purpose `rollback_plan_approval_write` above, this transaction never
    writes that file to the working tree before a commit exists, so
    there is nothing to restore; only a read-only safety assertion that
    the live state does not already, impossibly, carry this
    transaction's own post-approval identity.

    1. Ownership (`assert_plan_approval_journal_owner`) -- only this
       transaction's own owner may roll it back.
    2. Safety assertion: the live work item's own entry must not already
       be `phase: "IMPLEMENTING"` with
       `plan_approval.approved_review_content_id ==
       journal['expected_review_content_id']`
       (`PlanApprovalRollbackInvariantViolationError` otherwise).
    3. `git reset --mixed HEAD` -- the whole index back to `HEAD`,
       working tree untouched (named explicitly; never `--soft`/
       `--hard`).
    4. Verify before believing: the index is clean and live `HEAD` is
       still exactly `journal['pre_procedure_head']`. Either failing
       means the repository is not in the state this rollback expects --
       stop, leave the journal in place, raise
       (`PlanApprovalRollbackVerificationError`) rather than claim
       success.
    5. Close the journal -- reached only once (4) has verified."""
    journal = assert_plan_approval_journal_owner(repo_root, owner_token, path)
    state = _load_json(repo_root / state_path) or {}
    work_item = (state.get("work_items") or {}).get(journal["work_item_id"]) or {}
    plan_approval = work_item.get("plan_approval") or {}
    if (work_item.get("phase") == "IMPLEMENTING"
            and plan_approval.get("approved_review_content_id") == journal["expected_review_content_id"]):
        raise PlanApprovalRollbackInvariantViolationError(
            f"{journal['work_item_id']}'s live state already carries phase IMPLEMENTING with "
            f"approved_review_content_id {journal['expected_review_content_id']!r} -- this "
            f"transaction's own post-approval identity is already live despite classification "
            f"finding NOT_COMMITTED; refusing to roll back an approval that may have already "
            f"taken effect"
        )
    _run(["git", "reset", "--mixed", "HEAD"], cwd=repo_root)
    staged = _run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=repo_root).strip()
    live_head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
    if staged or live_head != journal["pre_procedure_head"]:
        raise PlanApprovalRollbackVerificationError(
            f"post-reset verification failed: staged={staged!r}, HEAD={live_head!r}, "
            f"expected HEAD {journal['pre_procedure_head']!r} -- the journal is left in "
            f"place, nothing is reported as rolled back"
        )
    close_plan_approval_journal(repo_root, path)
    _close_plan_approval_owner_progress(repo_root, journal["owner_token"])


# ---------------------------------------------------------------------------
# WF8c (g), part 2: WFR-63's transaction mutation/handoff guard, owner
# progress record, and explicit-takeover contract (`D-Approval-Commits`'
# "Owner progress record" / "Transaction mutation/handoff guard" / "Refuse
# by default; takeover is explicit", revision 57-59). Structurally mirrors
# `D-Checkpoint-Ownership`'s already-built, already-tested guard/takeover
# machinery below (`guard_path`/`acquire_guard`/`release_guard`/
# `take_over_claim`) rather than inventing a second design: the same
# publish-exclusive / reclaim-on-superseded-epoch / refuse-on-current-epoch
# shape, adapted to a single fixed guard (only `workflow-v2-1-core` ever
# opens a plan-approval transaction, so there is one guard, never one per
# work item) whose "current owner" comes from the plan-approval journal
# above rather than a checkpoint claim, plus the journal's own owner
# progress record, which `D-Checkpoint-Ownership` has no equivalent of.
#
# Deliberately bounded to this part: no amend recovery, and no
# `.claude/commands/approve-review.md` integration -- those remain a
# follow-up session's scope (the index-pinned-blob writer for
# `WORKFLOW_STATE.json` and step 8b's materialization are built below,
# part 3). This part exercises the guard/takeover machinery directly
# against the journal part 1 already built. There is deliberately no
# in-band recovery for an abandoned `"destructive"` guard
# (`D-Checkpoint-Ownership`'s `recover_abandoned_destructive_guard` has
# one; this transaction's own guard contract explicitly does not -- "a
# `"destructive"` guard abandoned in the current epoch has no in-band
# resolution, and that is deliberate": only an out-of-band manual
# `os.unlink` once a human has independently established the holding
# session is gone).
# ---------------------------------------------------------------------------


PLAN_APPROVAL_GUARD_PATH = Path(".ai-review/runtime/PLAN_APPROVAL_MUTATION.lease")
PLAN_APPROVAL_GUARD_LOCK_PATH = Path(".ai-review/runtime/PLAN_APPROVAL_MUTATION.guardlock")

# Exhaustive by the plan's own standing rule ("no category word may stand
# in for the list, the same standing rule step 7's verification set
# already carries"): every step any part of this transaction acquires the
# guard for must be named in exactly one of these two sets, or
# `plan_approval_step_class` refuses rather than guess. `step-6.5-commit`,
# `step-7d-amend-commit`, `step-8b-materialize`, `rollback-index-reset` and
# `step-8a-close-journal` are declared here as the classification a later
# part's guarded steps must use; only `rollback-close-journal` -- folded
# into this part's own `rollback_plan_approval_transaction` -- and
# `"takeover"` are actually acquired by code that exists yet.
PLAN_APPROVAL_DESTRUCTIVE_STEPS = frozenset({
    "step-6.5-commit",
    "step-7d-amend-commit",
    "step-8b-materialize",
    "rollback-index-reset",
    "rollback-close-journal",
    "step-8a-close-journal",
})
PLAN_APPROVAL_ORDINARY_STEPS = frozenset({
    "step-5-declaration-pin",
    "step-5-stage-and-pin",
    "step-6.1b-state-pin",
    "step-6.2-stage-ordinary",
    "step-7b-amend-stage",
    "takeover",
})


class PlanApprovalGuardUnavailableError(Exception):
    """Raised when the guard file exists but cannot be read/parsed, or a
    step name is not classified into either step-class set -- fails
    closed rather than guessing."""


class PlanApprovalGuardHeldError(Exception):
    """Raised when the guard is held by another epoch/session and this
    acquisition attempt is not authorized to reclaim it."""


class PlanApprovalTakeoverRefusedError(Exception):
    """Raised by `take_over_plan_approval_transaction` when the required
    authorization is missing, wrong, or stale, or when the observed guard
    is `"destructive"` -- refuses having mutated nothing."""


class PlanApprovalTakeoverWorkItemMismatchError(PlanApprovalTakeoverRefusedError):
    """Raised when the open journal belongs to a different work item than
    the one the invoking command resolved as its target (convergence
    repair, optional finding 3). A subclass of
    `PlanApprovalTakeoverRefusedError` because it is a refusal of the same
    kind and at the same point -- observed, authorized, nothing mutated --
    not a new takeover mode."""


class PlanApprovalTakeoverInProgressError(Exception):
    """Raised when another session's takeover of the same transaction
    epoch is already claiming it (`os.link`'s exclusivity)."""


def plan_approval_step_class(step: str) -> str:
    if step in PLAN_APPROVAL_DESTRUCTIVE_STEPS:
        return DESTRUCTIVE
    if step in PLAN_APPROVAL_ORDINARY_STEPS:
        return ORDINARY
    raise PlanApprovalGuardUnavailableError(f"unclassified plan-approval mutation step {step!r}")


def plan_approval_guard_path(repo_root: Path, path: Path = PLAN_APPROVAL_GUARD_PATH) -> Path:
    return repo_root / path


def read_plan_approval_guard(repo_root: Path, path: Path = PLAN_APPROVAL_GUARD_PATH) -> dict | None:
    """The guard body, or `None` if the guard is not held. Fails closed on
    a torn guard exactly as the journal does -- an undecidable guard is
    never read as an absent one."""
    full_path = plan_approval_guard_path(repo_root, path)
    try:
        raw = full_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PlanApprovalGuardUnavailableError(f"cannot read {full_path} ({exc})") from exc
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanApprovalGuardUnavailableError(
            f"{full_path} exists but could not be read as JSON ({exc}) -- refusing to mutate "
            f"behind an undecidable mutation guard"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("lease_id"), str):
        raise PlanApprovalGuardUnavailableError(f"{full_path} is not a well-formed mutation guard")
    return data


def _publish_plan_approval_guard(repo_root: Path, body: dict, path: Path) -> None:
    full_path = plan_approval_guard_path(repo_root, path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(full_path.parent), prefix=".plan-approval-guard-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp_name, full_path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


@contextlib.contextmanager
def _plan_approval_guard_lock(repo_root: Path, path: Path = PLAN_APPROVAL_GUARD_LOCK_PATH):
    """The stable object every guard *mutation* (acquire's reclaim branch)
    serializes on, mirroring `D-Checkpoint-Ownership`'s
    `guard_mutation_lock` (`OPUS-R83-001`): a compare-and-remove-then-
    publish must be one indivisible step, and locking the guard file
    itself would defeat that, since the guard's whole lifecycle is
    create-and-remove and two `flock`s on two different inodes that
    briefly shared one pathname are not serialized at all. Created once
    and never unlinked; released by the kernel on process death."""
    full_path = repo_root / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(full_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def acquire_plan_approval_guard(
    repo_root: Path, *, holder_owner_token: str, step: str, now: str, role: str = "owner",
    authorized_lease_id: str | None = None, guard_path: Path = PLAN_APPROVAL_GUARD_PATH,
    journal_path: Path = PLAN_APPROVAL_JOURNAL_PATH,
) -> dict:
    """One acquisition attempt -- no retry loop, no timeout, no wall clock
    beyond the single automatic retry the guard contract itself grants a
    superseded-epoch or own-leftover reclaim. Mirrors
    `D-Checkpoint-Ownership.acquire_guard`'s decision table, adapted to
    the plan-approval journal's single fixed guard:

    - **superseded epoch** (the held guard's `holder_owner_token` is not
      the journal's current `owner_token`): the guard was provably left by
      a session whose ownership has already rotated away -- reclaimed with
      no authorization, then acquisition is retried exactly once.
    - **`role="owner"`, same token**: this session's own leftover guard
      from an interrupted earlier step. Reclaimed and retried once; the
      window's first act is `assert_owner`, so a stale belief about
      ownership is caught immediately regardless.
    - **`role="takeover"`, current epoch, `"destructive"`**: refuses
      unconditionally, by anyone, under any authorization.
    - **`role="takeover"`, current epoch, `"ordinary"`**: released only
      when the observed `lease_id` is exactly the one the authorization
      quoted; any other value proves the owner released and re-acquired,
      i.e. is live, and refuses.
    - anything else refuses, naming the held guard."""
    step_class = plan_approval_step_class(step)
    # Salvage audit `O8`: this guard is repository-scoped -- one fixed
    # path, one at a time, whichever work item is being approved -- so the
    # body carried a hardcoded `"work_item_id": "workflow-v2-1-core"`
    # that was simply false for any other item's plan approval. Nothing
    # ever read it (`read_plan_approval_guard` validates `lease_id`
    # alone; the release literal names `lease_id`/`step`/`step_class`;
    # the takeover literal names the *journal's* own `work_item_id`,
    # which is per-item and correct), so it was diagnostic text that
    # could only mislead a human inspecting the lease. Dropped rather
    # than plumbed through: the journal, read under this same lock, is
    # already the per-item authority, and the guard has no business
    # asserting an identity it does not know.
    body = {
        "lease_id": secrets.token_hex(16),
        "holder_owner_token": holder_owner_token,
        "stage": "plan",
        "step": step,
        "step_class": step_class,
        "acquired_at": now,
    }
    with _plan_approval_guard_lock(repo_root):
        return _acquire_plan_approval_guard_locked(
            repo_root, body=body, holder_owner_token=holder_owner_token, role=role,
            authorized_lease_id=authorized_lease_id, guard_path=guard_path, journal_path=journal_path,
        )


def _acquire_plan_approval_guard_locked(
    repo_root: Path, *, body: dict, holder_owner_token: str, role: str,
    authorized_lease_id: str | None, guard_path: Path, journal_path: Path,
) -> dict:
    """`acquire_plan_approval_guard`'s decision and publication, run under
    `_plan_approval_guard_lock` so compare-and-remove-then-publish is one
    indivisible step rather than two statements a preemption can be
    scheduled between."""
    try:
        _publish_plan_approval_guard(repo_root, body, guard_path)
        return body
    except FileExistsError:
        pass

    held = read_plan_approval_guard(repo_root, guard_path)
    if held is None:                       # released between the two operations
        _publish_plan_approval_guard(repo_root, body, guard_path)
        return body

    current_journal = read_plan_approval_journal(repo_root, journal_path)
    current_owner_token = current_journal["owner_token"] if current_journal is not None else None
    reclaim = False
    if held.get("holder_owner_token") != current_owner_token:
        reclaim = True                     # superseded epoch
    elif role == "owner" and held.get("holder_owner_token") == holder_owner_token:
        reclaim = True                     # this session's own leftover guard
    elif role == "takeover":
        if held.get("step_class") == DESTRUCTIVE:
            raise PlanApprovalTakeoverRefusedError(
                f"the owner is inside destructive step {held.get('step')!r} (lease "
                f"{held.get('lease_id')!r}) -- no takeover authorization breaks that window; "
                f"resume in the owning session, or, if it is genuinely gone, remove "
                f"{plan_approval_guard_path(repo_root, guard_path)} by hand once that is "
                f"independently established"
            )
        if authorized_lease_id is not None and authorized_lease_id == held.get("lease_id"):
            reclaim = True                 # the authorized break, exactly as quoted
        else:
            raise PlanApprovalTakeoverRefusedError(
                f"the mutation guard is held (lease {held.get('lease_id')!r}, step "
                f"{held.get('step')!r}) and does not match the authorized lease "
                f"{authorized_lease_id!r} -- the owner is live; refusing to break it"
            )
    if not reclaim:
        raise PlanApprovalGuardHeldError(
            f"the plan-approval mutation guard is held by lease {held.get('lease_id')!r} "
            f"(step {held.get('step')!r}, class {held.get('step_class')!r}) -- refusing to "
            f"mutate the transaction concurrently with its owner"
        )

    _release_plan_approval_guard_locked(repo_root, held.get("lease_id"), guard_path)
    try:
        _publish_plan_approval_guard(repo_root, body, guard_path)
    except FileExistsError as exc:
        raise PlanApprovalGuardHeldError(
            "the plan-approval mutation guard was re-acquired by another session during "
            "reclamation -- refusing, having mutated nothing"
        ) from exc
    return body


def _release_plan_approval_guard_locked(repo_root: Path, lease_id: str, path: Path) -> None:
    """Compare-and-delete on `lease_id`: never remove a guard this session
    does not hold. **Caller must hold `_plan_approval_guard_lock`.** An
    absent `lease_id` is a refusal, never a wildcard that removes whatever
    guard is present."""
    if not isinstance(lease_id, str) or not lease_id:
        raise PlanApprovalGuardUnavailableError(
            f"releasing the plan-approval mutation guard requires the exact lease_id being "
            f"released, got {lease_id!r}"
        )
    full_path = plan_approval_guard_path(repo_root, path)
    try:
        held = read_plan_approval_guard(repo_root, path)
    except PlanApprovalGuardUnavailableError:
        return
    if held is None or held.get("lease_id") != lease_id:
        return
    full_path.unlink(missing_ok=True)


def release_plan_approval_guard(
    repo_root: Path, lease: dict, path: Path = PLAN_APPROVAL_GUARD_PATH,
) -> None:
    with _plan_approval_guard_lock(repo_root):
        _release_plan_approval_guard_locked(repo_root, lease.get("lease_id"), path)


def plan_approval_owner_progress_path(repo_root: Path, owner_token: str) -> Path:
    return repo_root / f".ai-review/runtime/PLAN_APPROVAL_OWNER.{owner_token}.json"


def read_plan_approval_owner_progress(repo_root: Path, owner_token: str) -> dict | None:
    """`{owner_token, step, step_seq, updated_at}`, or `None` if this
    owner has not yet completed any guarded mutation. Its absence is
    itself meaningful and legitimate: it means the owner published the
    journal but never *completed* a mutating step."""
    full_path = plan_approval_owner_progress_path(repo_root, owner_token)
    try:
        raw = full_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PlanApprovalGuardUnavailableError(f"cannot read {full_path} ({exc})") from exc
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanApprovalGuardUnavailableError(
            f"{full_path} could not be parsed as JSON ({exc})"
        ) from exc
    if (not isinstance(data, dict) or data.get("owner_token") != owner_token
            or not isinstance(data.get("step"), str)
            or not isinstance(data.get("step_seq"), int) or isinstance(data.get("step_seq"), bool)
            or not isinstance(data.get("updated_at"), str)):
        raise PlanApprovalGuardUnavailableError(f"{full_path} is not a well-formed owner progress record")
    return data


def _advance_plan_approval_owner_progress(repo_root: Path, owner_token: str, step: str, now: str) -> dict:
    """Written **inside** the mutation guard, after that step's own
    mutation has completed and before the guard is released -- so the
    record only ever claims a step has *finished*, never merely started.
    `step_seq` is a monotonically increasing integer starting at `1`,
    scoped to this one `owner_token` (a takeover's fresh token starts its
    own progress record fresh, by construction: the path embeds the
    token). Atomic temp-file write plus `os.replace`, same directory."""
    full_path = plan_approval_owner_progress_path(repo_root, owner_token)
    previous = read_plan_approval_owner_progress(repo_root, owner_token)
    record = {
        "owner_token": owner_token,
        "step": step,
        "step_seq": (previous["step_seq"] + 1) if previous is not None else 1,
        "updated_at": now,
    }
    full_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(full_path.parent), prefix=".plan-approval-owner-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, full_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return record


def _close_plan_approval_owner_progress(repo_root: Path, owner_token: str) -> None:
    """Deleted alongside the journal at step 8a and by the rollback --
    idempotent, an already-absent record is success."""
    plan_approval_owner_progress_path(repo_root, owner_token).unlink(missing_ok=True)


@contextlib.contextmanager
def plan_approval_guarded_mutation(
    repo_root: Path, *, owner_token: str, step: str, now: str,
    journal_path: Path = PLAN_APPROVAL_JOURNAL_PATH, guard_path: Path = PLAN_APPROVAL_GUARD_PATH,
):
    """The one fixed window shape every guarded step of the transaction
    runs inside:

        acquire the guard -> assert_owner(T), re-reading the journal
        *under* the guard -> perform the mutation -> advance the owner
        progress record -> release the guard

    Every pause, stall, or crash a session can suffer between its
    assertion and its mutation is therefore inside a window a takeover
    cannot enter. Windows are strictly non-nested: a session holds at most
    one guard at a time. If the caller's own mutation (the `yield` body)
    raises, or `assert_owner` fails, the progress record is left
    unadvanced and the guard is still released via `finally` -- a takeover
    then sees either no evidence of this step or the *previous* step's
    evidence, never a claim that this one finished."""
    lease = acquire_plan_approval_guard(
        repo_root, holder_owner_token=owner_token, step=step, now=now, role="owner",
        guard_path=guard_path, journal_path=journal_path,
    )
    try:
        assert_plan_approval_journal_owner(repo_root, owner_token, journal_path)
        yield lease
        _advance_plan_approval_owner_progress(repo_root, owner_token, step, now)
    finally:
        release_plan_approval_guard(repo_root, lease, guard_path)


def plan_approval_takeover_evidence(
    repo_root: Path, journal_path: Path = PLAN_APPROVAL_JOURNAL_PATH,
    guard_path: Path = PLAN_APPROVAL_GUARD_PATH,
) -> dict:
    """Everything a human needs to decide a plan-approval takeover,
    gathered without mutating anything. `journal` is `None` when there is
    no open transaction, i.e. nothing to take over."""
    journal = read_plan_approval_journal(repo_root, journal_path)
    if journal is None:
        return {"journal": None, "owner_token": None, "progress": None, "guard": None, "outcome": None}
    owner_token = journal["owner_token"]
    progress = read_plan_approval_owner_progress(repo_root, owner_token)
    guard = read_plan_approval_guard(repo_root, guard_path)
    outcome = classify_plan_approval_outcome(repo_root, journal)
    return {"journal": journal, "owner_token": owner_token, "progress": progress,
            "guard": guard, "outcome": outcome}


def plan_approval_takeover_authorization_literal(evidence: dict) -> str:
    """The exact literal a user must produce, derived from the evidence
    they were shown: names the observed `owner_token` and the observed
    `step_seq` (or the literal `none`), so it cannot be written from
    memory or replayed against a later transaction. `evidence["journal"]`
    is always present by the time this is called (`take_over_plan_
    approval_transaction` already refuses on `journal is None` first) --
    its own `work_item_id` is the permanent, work-item-general source for
    the literal's named subject (`OPUS-R129-M04`; this primitive is not
    `workflow-v2-1-core`-specific the way a hardcoded literal would
    imply, the same hardcoded-default class `OPUS-R28-011` retired
    elsewhere in this module)."""
    progress = evidence.get("progress")
    step_seq = progress["step_seq"] if progress else "none"
    return (f"take over {evidence['journal']['work_item_id']} plan-approval transaction "
            f"owner {evidence.get('owner_token')} step_seq {step_seq}")


def plan_approval_guard_release_authorization_literal(guard: dict) -> str:
    return (f"release plan-approval guard {guard.get('lease_id')} step {guard.get('step')} "
            f"class {guard.get('step_class')}")


def take_over_plan_approval_transaction(
    repo_root: Path, *, work_item_id: str, now: str, user_authorization: str | None,
    evidence: dict | None = None,
    guard_release_authorization: str | None = None, journal_path: Path = PLAN_APPROVAL_JOURNAL_PATH,
    guard_path: Path = PLAN_APPROVAL_GUARD_PATH,
) -> str:
    """The only ownership transfer this transaction ever performs
    (`D-Approval-Commits`' "Refuse by default; takeover is explicit").
    Returns the fresh `owner_token` `T'`.

    `work_item_id` is the target the *invoking command* resolved, and it
    is a **required** keyword argument, not an optional cross-check: the
    journal is a single, repository-wide object (`.ai-review/runtime/
    PLAN_APPROVAL_JOURNAL.json`), so `/approve-review B plan` observes an
    interrupted transaction belonging to work item A exactly as readily as
    one of its own, and every step from `6a` onward then drives A's pinned
    record, A's paths and A's expected post-state under B's invocation
    (convergence repair, optional finding 3). Nothing else refused that:
    the authorization literal names A, but naming A is not the same as
    checking that the operator meant A. An optional argument would leave
    the same forget-to-pass-it hole this repair exists to close, so the
    argument is required and the mismatch is refused at step 1a, before
    the claim, the guard, or any rotation -- having mutated nothing.

    1. **Observe** -- `plan_approval_takeover_evidence`.
    1a. **Target** -- the open journal's own `work_item_id` must equal the
       caller's resolved `work_item_id`; otherwise
       `PlanApprovalTakeoverWorkItemMismatchError`, naming both.
    2. **Authorize** -- the literal must be exactly
       `plan_approval_takeover_authorization_literal(evidence)`. When a
       guard was observed, a separate guard-release authorization quoting
       its `lease_id`/`step`/`step_class` is required too, and a
       `"destructive"` guard is refused outright -- no wording of an
       authorization changes that.
    3. **Claim, exclusively** -- `os.link` the journal onto
       `PLAN_APPROVAL_JOURNAL.claim.<T>`, scoping this attempt to the one
       observed epoch; a live concurrent takeover of the same epoch
       refuses here, having mutated nothing. A claim left by a takeover
       attempt that died before rotating (the journal still carries `T`)
       is provably dead and is cleared, once, before retrying.
    3a. **Acquire the mutation guard** -- the same primitive every
        mutating step acquires, `step="takeover"`, `step_class="ordinary"`.
    4. **Re-verify under the guard** -- the journal's `owner_token` and
       the progress record's `step_seq`/`updated_at` must be exactly the
       values the authorization quoted; either having advanced proves the
       owner is live: refuse, mutate nothing.
    5. **Rotate, still under the guard** -- every journal field carried
       over byte-identically except `owner_token` (fresh), `takeover_count`
       (incremented), `previous_owner_tokens` (`T` appended).
    5a. **Release the guard.**"""
    if evidence is None:
        evidence = plan_approval_takeover_evidence(repo_root, journal_path, guard_path)
    if evidence.get("journal") is None:
        raise NoPlanApprovalTransactionError(
            "no plan-approval transaction is open -- nothing to take over"
        )
    journal_work_item_id = evidence["journal"]["work_item_id"]
    if journal_work_item_id != work_item_id:
        raise PlanApprovalTakeoverWorkItemMismatchError(
            f"the open plan-approval transaction belongs to work item "
            f"{journal_work_item_id!r}, but this invocation resolved "
            f"{work_item_id!r} as its target -- refusing to take over, and "
            f"complete, another work item's approval under this one's "
            f"invocation; re-run the command targeting {journal_work_item_id!r} "
            f"if that transaction is the one that should be resumed"
        )
    expected = plan_approval_takeover_authorization_literal(evidence)
    if user_authorization != expected:
        raise PlanApprovalTakeoverRefusedError(
            f"explicit takeover requires the literal authorization {expected!r} derived from "
            f"the evidence just presented -- refusing to take over a transaction on an "
            f"inference or a remembered literal"
        )
    guard = evidence.get("guard")
    if guard is not None:
        if guard.get("step_class") == DESTRUCTIVE:
            raise PlanApprovalTakeoverRefusedError(
                f"the owner is inside destructive step {guard.get('step')!r} -- no takeover "
                f"authorization breaks that window; resume in the owning session, or, if it is "
                f"genuinely gone, remove {plan_approval_guard_path(repo_root, guard_path)} by "
                f"hand once that is independently established"
            )
        expected_guard_release = plan_approval_guard_release_authorization_literal(guard)
        if guard_release_authorization != expected_guard_release:
            raise PlanApprovalTakeoverRefusedError(
                f"a mutation guard was observed; takeover additionally requires the literal "
                f"{expected_guard_release!r}"
            )

    T = evidence["owner_token"]
    full_journal_path = plan_approval_journal_path(repo_root, journal_path)
    claim_path = full_journal_path.parent / f"PLAN_APPROVAL_JOURNAL.claim.{T}"
    try:
        os.link(full_journal_path, claim_path)
    except FileExistsError:
        current = read_plan_approval_journal(repo_root, journal_path)
        if current is not None and current.get("owner_token") == T:
            # A prior takeover attempt of this same epoch died before
            # rotating -- it provably mutated nothing (rotation is the
            # single atomic replace at step 5), so this fresh attempt,
            # with its own fresh authorization, may clear the dead claim
            # and retry exactly once.
            claim_path.unlink(missing_ok=True)
            try:
                os.link(full_journal_path, claim_path)
            except FileExistsError as exc:
                raise PlanApprovalTakeoverInProgressError(
                    "another session is already taking over this same plan-approval "
                    "transaction epoch"
                ) from exc
        else:
            raise PlanApprovalTakeoverInProgressError(
                "another session is already taking over this same plan-approval transaction "
                "epoch, or the transaction has already moved on"
            )

    lease = acquire_plan_approval_guard(
        repo_root, holder_owner_token=T, step="takeover", now=now, role="takeover",
        authorized_lease_id=guard.get("lease_id") if guard else None,
        guard_path=guard_path, journal_path=journal_path,
    )
    try:
        current_journal = read_plan_approval_journal(repo_root, journal_path)
        current_progress = read_plan_approval_owner_progress(repo_root, T)
        observed_progress = evidence.get("progress")
        if (current_journal is None or current_journal.get("owner_token") != T
                or (current_progress or {}).get("step_seq") != (observed_progress or {}).get("step_seq")
                or (current_progress or {}).get("updated_at") != (observed_progress or {}).get("updated_at")):
            raise PlanApprovalTakeoverRefusedError(
                "the transaction changed between the evidence the user authorized and this "
                "takeover -- refusing, having mutated nothing; present fresh evidence and "
                "obtain a fresh authorization"
            )
        new_token = secrets.token_hex(16)
        rotated = dict(current_journal)
        rotated["owner_token"] = new_token
        rotated["takeover_count"] = current_journal["takeover_count"] + 1
        rotated["previous_owner_tokens"] = list(current_journal["previous_owner_tokens"]) + [T]
        _replace_plan_approval_journal(repo_root, rotated, journal_path)
        return new_token
    finally:
        release_plan_approval_guard(repo_root, lease, guard_path)


def _replace_plan_approval_journal(
    repo_root: Path, journal: dict, path: Path = PLAN_APPROVAL_JOURNAL_PATH,
) -> None:
    """`os.replace` is correct here, and only here: the takeover's own
    claim (step 3) has already serialized every contender for this epoch,
    and the mutation guard (step 3a) excludes every concurrent mutation,
    so there is no remaining window for `os.replace` to race."""
    full_path = plan_approval_journal_path(repo_root, path)
    payload = (json.dumps(journal, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(full_path.parent), prefix=".plan-approval-journal-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, full_path)
        dir_fd = os.open(str(full_path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# WF8c (g), part 3: WFR-63's index-pinned-blob writer for
# `WORKFLOW_STATE.json` (step-6.1b-state-pin) and step 8b's materialization
# (`D-Approval-Commits` revision 56's "no intermediate state can satisfy the
# currently-installed `/bootstrap-workflow-v2`'s own durability guard": the
# post-approval state is pinned into the Git index and materialized into the
# working tree only after a verified durable approval commit exists, so the
# working-tree copy of `WORKFLOW_STATE.json` never shows post-approval bytes
# before there is a commit to back them).
#
# `pin_plan_approval_state_blob` deliberately does not use
# `stage_plan_approval_commit_paths`'s ordinary `git add` (which always
# reads the working tree): it writes the journal's own pinned
# `expected_post_state_b64` bytes as a Git blob object
# (`git hash-object -w --stdin`) and stages that blob directly into the
# index (`git update-index --cacheinfo`), leaving the working-tree file
# byte-identical to `HEAD` throughout. `materialize_plan_approval_state`
# is the one and only later write to that file's working-tree copy,
# performed from the approval commit's own committed bytes, never a fresh
# re-serialization -- so what lands on disk is always exactly what the
# commit already carries.
#
# Three independent sha256 checks against the journal's own
# `expected_post_state_sha256`, mirroring the conditional fifth member's
# staged/committed pair above (`verify_staged_blob_sha256`/
# `verify_committed_blob_sha256`) plus one further check this transaction's
# redesign specifically requires: staged (`verify_staged_plan_approval_state_blob`),
# committed (`verify_committed_plan_approval_state_blob`), and materialized
# (`materialize_plan_approval_state`'s own post-write re-read). Deliberately
# bounded to this part: these are library primitives only, exercised
# directly against real `ScratchRepo` git history -- the caller that will
# wrap each in `plan_approval_guarded_mutation(..., step="step-6.1b-state-pin"
# | "step-8b-materialize", ...)`, the amend recovery (item 347's 3-way
# interruption classification), and the `.claude/commands/approve-review.md`
# integration itself all remain a follow-up session's scope.
# ---------------------------------------------------------------------------


#: `workflow-2.5.0` CP9 (`v2.3.1-003`'s own first stated portable form,
#: `docs/defects/v2.3.1-003-plan-approval-requires-precommitted-state-file.md`):
#: the file mode `pin_plan_approval_state_blob` falls back to when
#: `state_path` has no `HEAD` entry to read a real mode from -- the mode
#: every other tracked path this Workflow ever commits already uses, per
#: the defect record's own observation, not a guess.
_FALLBACK_STATE_BLOB_MODE = "100644"


class PlanApprovalStateBlobUnavailableError(Exception):
    """No longer raised by `pin_plan_approval_state_blob` itself
    (`workflow-2.5.0` CP9, `v2.3.1-003`): a `state_path` absent at `HEAD`
    -- this repository's own genuinely-first plan approval, before
    `docs/ai-workflow/WORKFLOW_STATE.json` has ever been committed -- now
    falls back to `_FALLBACK_STATE_BLOB_MODE` instead of refusing
    unconditionally, since every other tracked path this Workflow ever
    commits already uses that same mode. Retained as a documented
    exception type for this module's own public contract; no code path in
    this Workflow release raises it."""


class StagedStateBlobMismatchError(Exception):
    """`WORKFLOW_STATE.json`'s *staged* (Git index) content, immediately
    after `pin_plan_approval_state_blob`, does not sha256-match the plan-
    approval journal's own pinned `expected_post_state_sha256` -- defense
    against a race between journal-open and staging, mirroring
    `StagedBlobMismatchError`'s role for the conditional fifth member,
    built as a separate, dedicated class rather than shared with it: the
    two check different paths for different reasons (an ordinary `git
    add` there; a direct index-blob pin here) and their messages should
    say so."""


class CommittedStateBlobMismatchError(Exception):
    """`WORKFLOW_STATE.json`'s *committed* content does not sha256-match
    the sha256 pinned in the plan-approval journal -- raised by
    `verify_committed_plan_approval_state_blob`, and again, as its own
    precondition, by `materialize_plan_approval_state`, which refuses to
    copy unverified committed content into the working tree."""


class MaterializedStateBlobMismatchError(Exception):
    """Raised by `materialize_plan_approval_state` when the bytes it just
    wrote to `WORKFLOW_STATE.json`'s working-tree copy, re-read from disk,
    do not sha256-match what was written -- the third and final of the
    three checks `WFR-63` requires (staged, committed, materialized),
    verified rather than assumed."""


def pin_plan_approval_state_blob(
    repo_root: Path, expected_state_bytes: bytes, *, state_path: Path = DEFAULT_STATE_PATH,
) -> str:
    """step-6.1b-state-pin: writes `expected_state_bytes` -- the plan-
    approval journal's own pinned `expected_post_state_b64`, decoded by
    the caller -- as a Git blob object and stages it at `state_path`
    directly in the index (`git update-index --add --cacheinfo`), without
    ever writing those bytes to the working-tree copy of that file. This
    is the transaction's one deliberate departure from
    `stage_plan_approval_commit_paths`'s ordinary `git add` (which always
    reads the working tree): `WORKFLOW_STATE.json`'s post-approval content
    must never be observable on disk before a durable, verified commit
    exists, so the working-tree file is left exactly as it was -- still
    the pre-approval bytes -- for the whole window between this call and
    `materialize_plan_approval_state` (step 8b).

    Precondition: `state_path`'s own staged content must already equal
    `HEAD` (`git diff --cached HEAD -- state_path` empty) --
    `DirtyIndexBeforeStagingError` otherwise, the same exception
    `stage_plan_approval_commit_paths`'s own analogous precondition
    raises, for the same reason: a clearer diagnostic than discovering a
    stray earlier pin only in a later staged-set assertion.

    Returns the written blob's Git object id (informational only --
    verification against the journal's own pinned identity is
    `verify_staged_plan_approval_state_blob`, by content sha256, not this
    object id, so it is unaffected by which hash algorithm the repository
    itself uses for Git objects).

    `workflow-2.5.0` CP9 (`v2.3.1-003`): when `state_path` has no entry at
    `HEAD` at all -- a repository's own genuinely-first plan approval,
    before `WORKFLOW_STATE.json` has ever been committed -- this no
    longer refuses unconditionally. It falls back to
    `_FALLBACK_STATE_BLOB_MODE` (`100644`), the mode every other tracked
    path this Workflow ever commits already uses, and proceeds exactly as
    if that mode had been read from `HEAD`."""
    already = _run(
        ["git", "diff", "--name-only", "--cached", "HEAD", "--", str(state_path)], cwd=repo_root,
    ).strip()
    if already:
        raise DirtyIndexBeforeStagingError(
            f"{state_path} is already staged and differs from HEAD before "
            f"step-6.1b-state-pin ran -- resolve or unstage it first"
        )
    mode_and_sha = _blob_mode_and_sha_at_commit(repo_root, "HEAD", str(state_path))
    mode = mode_and_sha[0] if mode_and_sha is not None else _FALLBACK_STATE_BLOB_MODE
    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo_root,
        input=expected_state_bytes, capture_output=True, check=True,
    ).stdout.decode("ascii").strip()
    _run(
        ["git", "update-index", "--add", "--cacheinfo", f"{mode},{blob_sha},{state_path}"],
        cwd=repo_root,
    )
    return blob_sha


def verify_staged_plan_approval_state_blob(
    repo_root: Path, expected_sha256: str, *, state_path: Path = DEFAULT_STATE_PATH,
) -> None:
    """Re-verifies `state_path`'s *staged* (Git index) content against
    `expected_sha256` -- call after `pin_plan_approval_state_blob`, before
    creating the commit, to close the race window between pinning and
    commit creation. Raises `StagedStateBlobMismatchError` on a
    mismatch."""
    content = subprocess.run(
        ["git", "show", f":{state_path}"], cwd=repo_root, capture_output=True, check=True,
    ).stdout
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_sha256:
        raise StagedStateBlobMismatchError(
            f"{state_path}: staged blob sha256 {actual} does not match the sha256 "
            f"{expected_sha256} pinned in the plan-approval journal"
        )


def verify_committed_plan_approval_state_blob(
    repo_root: Path, commit: str, expected_sha256: str, *, state_path: Path = DEFAULT_STATE_PATH,
) -> None:
    """Re-verifies `state_path`'s *committed* content at `commit` against
    `expected_sha256` -- defense in depth beyond
    `verify_post_approval_manifest_match`'s own full projection-digest
    check, isolating exactly this member if the two ever disagree. Raises
    `CommittedStateBlobMismatchError` on a mismatch."""
    actual = hashlib.sha256(_read_committed_bytes(repo_root, commit, str(state_path))).hexdigest()
    if actual != expected_sha256:
        raise CommittedStateBlobMismatchError(
            f"{state_path} at {commit}: committed blob sha256 {actual} does not match "
            f"the sha256 {expected_sha256} pinned in the plan-approval journal"
        )


def materialize_plan_approval_state(
    repo_root: Path, commit: str, expected_sha256: str, *, state_path: Path = DEFAULT_STATE_PATH,
) -> None:
    """step-8b-materialize: the transaction's one and only write to
    `state_path`'s actual working-tree copy, performed only after the
    approval commit is durable (`classify_plan_approval_outcome` ==
    `COMMITTED`). Reads `commit`'s own committed bytes for `state_path`
    and, as its own precondition -- independent of whether the caller
    already ran `verify_committed_plan_approval_state_blob` --
    sha256-verifies them against `expected_sha256`
    (`CommittedStateBlobMismatchError` on a mismatch: refuses to
    materialize unverified content). Writes those exact bytes, never a
    fresh re-serialization, via the same same-directory-temp-file-plus-
    `fsync`-plus-`os.replace` publication `_publish_state_file` uses, so a
    crash mid-write leaves either the old bytes or the new ones, never a
    torn file. Re-reads the result and sha256-verifies it before returning
    (`MaterializedStateBlobMismatchError` on a mismatch) -- the third and
    final of the three checks `WFR-63` requires (staged, committed,
    materialized)."""
    committed_bytes = _read_committed_bytes(repo_root, commit, str(state_path))
    actual = hashlib.sha256(committed_bytes).hexdigest()
    if actual != expected_sha256:
        raise CommittedStateBlobMismatchError(
            f"{state_path} at {commit}: committed blob sha256 {actual} does not match "
            f"the sha256 {expected_sha256} pinned in the plan-approval journal -- "
            f"refusing to materialize unverified content"
        )
    full_path = repo_root / state_path
    fd, tmp_name = tempfile.mkstemp(
        dir=str(full_path.parent), prefix=f".{full_path.name}-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(committed_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, full_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    materialized_sha256 = hashlib.sha256(full_path.read_bytes()).hexdigest()
    if materialized_sha256 != expected_sha256:
        raise MaterializedStateBlobMismatchError(
            f"{state_path}: materialized working-tree bytes sha256 {materialized_sha256} "
            f"do not match the expected sha256 {expected_sha256} immediately after writing"
        )


class PlanApprovalMaterializeDivergentEntryError(Exception):
    """Raised by `classify_plan_approval_materialize_target` when this
    work item's own `work_items[work_item_id]` entry in the *current*
    working-tree state neither matches the journal's pre-transaction
    entry nor its already-materialized post-transaction entry --
    something else wrote to this exact work item's own entry between
    journal-open and materialization (a second concurrent approval
    attempt, a manual edit, or a genuine bug elsewhere). The caller must
    refuse to write: the durable approval commit already exists and is
    not at risk, only the working-tree copy is stale and ambiguous to
    reconcile automatically."""


PLAN_APPROVAL_MATERIALIZE_NOOP = "NOOP"
PLAN_APPROVAL_MATERIALIZE_WRITE = "WRITE"


def classify_plan_approval_materialize_target(
    repo_root: Path, work_item_id: str, pre_state: dict, post_state: dict,
    *, state_path: Path = DEFAULT_STATE_PATH,
) -> str:
    """`/approve-review plan`'s permanent-site step 8b (`WF8c` item
    348(hh)'s target-scoped three-way classification, the mechanism that
    makes materialization safe against *other* work items' legitimate
    concurrent state writes): compares only `work_items[work_item_id]`
    -- never the whole file -- across the *current* working-tree state,
    the journal's `pre_state`, and its `post_state`, so a concurrent
    write to a *different* work item's own entry never factors into
    this work item's own classification. A whole-file classification
    ("write only if the file equals the pre-state, no-op only if it
    equals the post-state") is confirmed independently to refuse
    permanently the moment any other work item's own entry has ever
    moved on since this transaction's `pre_state` snapshot -- there is
    then no byte-identical whole-file state left to recognise as either
    branch, so the journal could never close without discarding that
    other work item's write.

    Returns `PLAN_APPROVAL_MATERIALIZE_NOOP` when this work item's own
    entry already equals `post_state`'s (already materialized, or
    reconciled by hand -- idempotent under repeated interruption) or
    `PLAN_APPROVAL_MATERIALIZE_WRITE` when it still equals `pre_state`'s
    (the ordinary case, safe to *attempt* -- the caller must still run
    `plan_approval_state_matches_pre_transaction`'s own fresh, whole-file
    compare-and-swap immediately before the actual write, since that is
    what actually detects and refuses on a different work item's own
    concurrent update; this function only ever inspects one work item's
    own entry, by design). Raises
    `PlanApprovalMaterializeDivergentEntryError` when neither matches --
    this work item's own entry was touched by something else entirely,
    a case no automatic reconciliation is safe to attempt."""
    current_state = _load_json(repo_root / state_path) or {}
    current_entry = (current_state.get("work_items") or {}).get(work_item_id)
    pre_entry = (pre_state.get("work_items") or {}).get(work_item_id)
    post_entry = (post_state.get("work_items") or {}).get(work_item_id)
    if current_entry == post_entry:
        return PLAN_APPROVAL_MATERIALIZE_NOOP
    if current_entry == pre_entry:
        return PLAN_APPROVAL_MATERIALIZE_WRITE
    raise PlanApprovalMaterializeDivergentEntryError(
        f"{work_item_id}'s own work_items entry in the current working tree matches "
        f"neither the journal's pre-transaction entry nor its post-transaction entry -- "
        f"something else wrote to this work item's own state between journal-open and "
        f"materialization; refusing to guess which value is correct"
    )


def plan_approval_state_matches_pre_transaction(
    repo_root: Path, expected_pre_procedure_state_sha256: str, *, state_path: Path = DEFAULT_STATE_PATH,
) -> bool:
    """The whole-file freshness re-check `WF8c` item 348(mm) requires
    immediately before step 8b's actual materialize write (and,
    analogously, item 348(gg)'s sub-step 6.1a immediately before the
    pre-commit state-blob pin): a fresh, whole-file byte comparison
    against the journal's own `pre_procedure_state_sha256`, narrowing
    the race window between "decided it is safe" and "actually wrote"
    to as small as the single re-read this function performs, rather
    than trusting an earlier read taken further back in the procedure."""
    return hashlib.sha256(
        (repo_root / state_path).read_bytes()
    ).hexdigest() == expected_pre_procedure_state_sha256


# ---------------------------------------------------------------------------
# workflow-2.4.0, D-Plan-Amendment-4: paired-anchor checkpoint-content
# hashing and checkpoint reconciliation after an amendment. Every plan
# document written or amended under this mechanism marks each checkpoint's
# own design-decision prose with paired anchor comments, `<!-- CP<n> -->`
# immediately before and `<!-- /CP<n> -->` immediately after each block of
# prose that describes it -- a checkpoint may have any number of such
# disjoint, non-contiguous pairs.
# ---------------------------------------------------------------------------

_CHECKPOINT_ANCHOR_RE = re.compile(r"<!--\s*(/?)CP(\d+)\s*-->")

#: Shape a checkpoint id must have for `_CHECKPOINT_ANCHOR_RE` to ever be
#: able to produce a matching anchor tag for it (IMPL2-R1): the grammar
#: only ever emits/consumes `"CP" + digits`, so any other id shape (e.g.
#: `workflow-v2-1-core`'s own real `WF4a-i`) can never have a well-formed
#: anchor pair -- `checkpoint_id_supports_anchor` below is the single
#: place that fact is checked, so `request_plan_amendment` can refuse
#: early rather than leave `validate_post_anchor_coverage` as the only,
#: much later, signal.
_ANCHOR_COMPATIBLE_CHECKPOINT_ID_RE = re.compile(r"^CP\d+\Z")


def checkpoint_id_supports_anchor(checkpoint_id: str) -> bool:
    """True iff `checkpoint_id` has the one shape (`CP<digits>`) the
    paired-anchor grammar (`_CHECKPOINT_ANCHOR_RE`/
    `parse_checkpoint_anchor_spans`) can ever match. False for any other
    shape -- e.g. `WF4a-i` -- for which `validate_post_anchor_coverage` is
    unconditionally unsatisfiable, no matter what the plan document says.

    `\\Z` rather than `$` (IMPL4-O3): Python's `$` matches immediately
    before a trailing `\\n` as well as at the true end of string, so an id
    of `"CP1\\n"` would otherwise pass this shape gate while remaining
    unsatisfiable by `_CHECKPOINT_ANCHOR_RE`'s own anchor-tag parser, which
    has no such allowance."""
    return bool(_ANCHOR_COMPATIBLE_CHECKPOINT_ID_RE.match(checkpoint_id))


def parse_checkpoint_anchor_spans(text: str, *, strict: bool = True) -> dict[str, list[tuple[int, int]]]:
    """Parses `text` for `<!-- CP<n> -->`/`<!-- /CP<n> -->` anchor pairs,
    returning `{checkpoint_id: [(content_start, content_end), ...]}` in
    document order -- the content *between* each matched pair, never
    including the tags themselves.

    Grammar (D-Plan-Amendment-4, B5-new/I5-new), a closed, non-nesting,
    per-id balanced-tag grammar with no undefined case: for a given id,
    every open tag must be followed, before any other open/close tag for
    that same id and before end of document, by exactly one matching close
    tag. An unmatched open tag, an orphan close tag, or a tag nested inside
    another open span for the same id is malformed. Any number of disjoint,
    non-overlapping, well-formed pairs for the same id is legal.

    When `strict` is true (the only mode used for `post_plan_text` --
    D-Plan-Amendment-4's post side is "validated, and a refusal here is
    always actionable"), a malformed tag raises
    `AmendmentAnchorMalformedError`. When `strict` is false (the only mode
    used for `pre_amendment_plan_text` -- "never a refusal, always
    conservative"), a checkpoint id with a malformed shape is simply
    omitted from the returned map -- indistinguishable, to this function's
    caller, from an id with zero anchors at all, which is exactly the
    fail-closed "no information" direction the pre side requires."""
    spans: dict[str, list[tuple[int, int]]] = {}
    open_at: dict[str, int] = {}
    malformed: set[str] = set()
    for match in _CHECKPOINT_ANCHOR_RE.finditer(text):
        is_close = match.group(1) == "/"
        # The registry's own checkpoint ids are "CP<n>" strings (e.g.
        # "CP1"); the anchor tag's own digits are joined back onto that
        # prefix so this map's keys line up with `depends_on`/registry
        # `id` values directly, never a bare digit that would silently
        # never match anything.
        checkpoint_id = "CP" + match.group(2)
        if not is_close:
            if checkpoint_id in open_at:
                # Nested/overlapping open tag for the same id.
                if strict:
                    raise AmendmentAnchorMalformedError(
                        f"{checkpoint_id}: nested <!-- {checkpoint_id} --> tag "
                        f"(an earlier span for this id is still open)"
                    )
                malformed.add(checkpoint_id)
                continue
            open_at[checkpoint_id] = match.end()
        else:
            if checkpoint_id not in open_at:
                # Orphan close tag with no preceding matching open.
                if strict:
                    raise AmendmentAnchorMalformedError(
                        f"{checkpoint_id}: <!-- /{checkpoint_id} --> with no "
                        f"preceding matching <!-- {checkpoint_id} -->"
                    )
                malformed.add(checkpoint_id)
                continue
            start = open_at.pop(checkpoint_id)
            spans.setdefault(checkpoint_id, []).append((start, match.start()))
    for checkpoint_id in open_at:
        # Unterminated final anchor.
        if strict:
            raise AmendmentAnchorMalformedError(
                f"{checkpoint_id}: <!-- {checkpoint_id} --> with no matching "
                f"<!-- /{checkpoint_id} --> before end of document"
            )
        malformed.add(checkpoint_id)
    for checkpoint_id in malformed:
        spans.pop(checkpoint_id, None)
    return spans


def checkpoint_content_hash(text: str, checkpoint_id: str, *, strict: bool = False) -> str | None:
    """The per-checkpoint content hash D-Plan-Amendment-4's "identical
    checkpoint content" test uses: sha256 over the concatenation, in
    document order, of every well-formed anchor span for `checkpoint_id`.
    Returns `None` when the id has zero well-formed spans in `text` -- the
    "no information" case, which every caller must treat as "content
    changed" (conservatively), never as "unchanged"."""
    spans = parse_checkpoint_anchor_spans(text, strict=strict)
    ids_spans = spans.get(checkpoint_id)
    if not ids_spans:
        return None
    content = "".join(text[start:end] for start, end in ids_spans)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_post_anchor_coverage(post_plan_text: str, post_registry: dict) -> None:
    """D-Plan-Amendment-4's post-side validation, run by `apply_plan_approval`
    before it computes any reconciliation outcome: every checkpoint id
    present in `post_registry` must have at least one well-formed anchor
    pair in `post_plan_text`. A missing id raises
    `AmendmentAnchorCoverageError`; a malformed tag anywhere in
    `post_plan_text` raises `AmendmentAnchorMalformedError` (propagated
    from the strict parse below) -- both before any silent partial
    reconciliation.

    An id introduced by the amendment itself (not merely one inherited
    from the pre-amendment registry) that is not of the shape `CP<digits>`
    raises `AmendmentCheckpointIdShapeError` instead of the coverage error
    (IMPL3-O1): `request_plan_amendment`'s own id-shape precondition only
    ever inspects the *pre*-amendment registry, so an anchor-incompatible
    id authored during the amendment would otherwise reach this function
    and get the unactionable "add an anchor" message for an id no anchor
    text can ever satisfy -- checked here, ahead of the coverage check, for
    the same reason `request_plan_amendment` checks it early."""
    spans = parse_checkpoint_anchor_spans(post_plan_text, strict=True)
    for entry in post_registry.get("checkpoints", []):
        checkpoint_id = entry["id"]
        if not checkpoint_id_supports_anchor(checkpoint_id):
            raise AmendmentCheckpointIdShapeError(
                f"{checkpoint_id} is not of the shape 'CP<digits>' -- the "
                f"plan-amendment anchor grammar (D-Plan-Amendment-4) can never be "
                f"satisfied for this id no matter what the amended plan document "
                f"says; rename it via another /milestone-plan round"
            )
        if not spans.get(checkpoint_id):
            raise AmendmentAnchorCoverageError(
                f"{checkpoint_id} has no well-formed anchor pair in the plan document "
                f"being approved -- every registry checkpoint id must have at least one"
            )


_RECONCILIATION_ROW_FIELDS = ("name", "depends_on", "complexity", "session_target")


def reconcile_checkpoints_after_amendment(
    pre_registry: dict, post_registry: dict, pre_plan_text: str, post_plan_text: str, checkpoints: dict,
) -> dict:
    """workflow-2.4.0, D-Plan-Amendment-4's reconciliation algorithm, folded
    into `apply_plan_approval`'s own computation. Computes, per checkpoint
    id, one of four outcomes -- never a free-text operator claim -- and
    returns `{"checkpoints": <new map>, "outcome": {id: "retained" |
    "needs_revalidation" | "needs_revalidation_dependency" | "dropped" |
    "new"}, "dropped": [id, ...]}`. `"needs_revalidation"` and
    `"needs_revalidation_dependency"` are deliberately distinct tokens
    (`IMPL6-B1`): both leave the checkpoint's own `status` at
    `NEEDS_REVALIDATION`, but the former means *this* id's own registry row
    or checkpoint content changed, and the latter means this id was itself
    unchanged and was flipped only because a dependency it names was
    demoted or dropped -- the "which flips came from the dependency-closure
    pass" distinction `/approve-review plan`'s own report (see
    `apply_plan_approval`) requires and that a single shared token could
    not otherwise recover.

    - id present in both, identical registry row and identical checkpoint
      content (by `checkpoint_content_hash`, pre-side non-strict/
      conservative, post-side already validated strict by
      `validate_post_anchor_coverage`) -- untouched (`retained`).
    - id present in both, registry row changed or checkpoint content
      changed (including the zero-anchor legacy default: `pre_plan_text`
      has no anchors anywhere, so every shared id is conservatively
      `needs_revalidation`) -- if it was `COMPLETE`, rewritten to
      `NEEDS_REVALIDATION`; any other status is left as-is (nothing to
      revalidate that has not already completed).
    - id removed from the amended registry -- dropped from the live
      `checkpoints` map (its history survives in this amendment's own
      `checkpoints_snapshot` and in git history via commit trailers).
    - id new to the amended registry -- absent from `checkpoints`, picked
      up by `select_next_checkpoint` exactly as any new checkpoint always
      is; reported as `"new"`.

    Then a single forward pass over `post_registry`'s own order (already a
    valid topological order) propagates dependency closure: any checkpoint
    left `COMPLETE` whose own `depends_on` includes an id that is not
    itself `COMPLETE` in the resulting map (rewritten, or absent because
    just removed) is also rewritten to `NEEDS_REVALIDATION`, reported as
    `"needs_revalidation_dependency"` regardless of what the direct pass
    above recorded for that same id (a closure-derived demotion always
    supersedes a direct one in the report, since the closure pass runs
    strictly after and the id's `status` ends at `NEEDS_REVALIDATION`
    either way). No fixed-point loop needed, since every dependency
    precedes its dependents in that order (B6.3) -- a precondition this
    function itself does not re-check, but which its sole caller,
    `apply_plan_approval`, now enforces mechanically immediately before
    calling this function (`validate_registry_topological_order(post_registry)`,
    `OPUS-R145-002`) rather than relying on `write_registry_and_mapping`-time
    validation of a document that, by the time this runs, has already been
    re-read raw off the working tree."""
    pre_rows = {entry["id"]: entry for entry in pre_registry.get("checkpoints", [])}
    post_rows = {entry["id"]: entry for entry in post_registry.get("checkpoints", [])}

    new_checkpoints = copy.deepcopy(checkpoints)
    outcome: dict[str, str] = {}

    for checkpoint_id in post_rows:
        if checkpoint_id not in pre_rows:
            outcome[checkpoint_id] = "new"
            continue
        pre_row = pre_rows[checkpoint_id]
        post_row = post_rows[checkpoint_id]
        row_changed = any(pre_row.get(field) != post_row.get(field) for field in _RECONCILIATION_ROW_FIELDS)
        pre_hash = checkpoint_content_hash(pre_plan_text, checkpoint_id, strict=False)
        post_hash = checkpoint_content_hash(post_plan_text, checkpoint_id, strict=True)
        content_changed = pre_hash is None or pre_hash != post_hash
        entry = new_checkpoints.get(checkpoint_id)
        if row_changed or content_changed:
            if entry is not None and entry.get("status") == "COMPLETE":
                new_checkpoints[checkpoint_id] = dict(entry, status="NEEDS_REVALIDATION")
            outcome[checkpoint_id] = "needs_revalidation"
        else:
            outcome[checkpoint_id] = "retained"

    dropped = [checkpoint_id for checkpoint_id in pre_rows if checkpoint_id not in post_rows]
    for checkpoint_id in dropped:
        new_checkpoints.pop(checkpoint_id, None)
        outcome[checkpoint_id] = "dropped"

    complete_ids = {cid for cid, entry in new_checkpoints.items() if entry.get("status") == "COMPLETE"}
    for row in post_registry.get("checkpoints", []):
        checkpoint_id = row["id"]
        if checkpoint_id not in complete_ids:
            continue
        depends_on = row.get("depends_on", [])
        if any(dep not in complete_ids for dep in depends_on):
            new_checkpoints[checkpoint_id] = dict(new_checkpoints[checkpoint_id], status="NEEDS_REVALIDATION")
            complete_ids.discard(checkpoint_id)
            outcome[checkpoint_id] = "needs_revalidation_dependency"

    return {"checkpoints": new_checkpoints, "outcome": outcome, "dropped": dropped}


# ---------------------------------------------------------------------------
# WF2: D-Selection's deterministic four-rule checkpoint-selection algorithm,
# the IN_PROGRESS/COMPLETE state writers, and D3's worktree-scoped dirty-
# resume mechanics (WORKTREE_IDENTITY.json writer + resume check).
# ---------------------------------------------------------------------------


def select_next_checkpoint(work_item: dict, registry: dict) -> str | None:
    """D-Selection, rules 1-2-4 in full (rule 3 -- the registry JSON's own
    order being a valid topological order -- is a separate precondition,
    `validate_registry_topological_order`, checked once at registry-write
    time rather than on every selection call).

    Pure: reads only `work_item`/`registry`, touches no filesystem or Git
    state, so two independent fresh sessions given byte-identical inputs
    always select the same checkpoint (missing-test item 28) without any
    tie-break beyond rule 3's own topological-order guarantee. Deliberately
    does *not* perform the worktree-identity check rule 1 defers to --
    that is `verify_dirty_resume_safety`'s job, kept separate so this
    determinism guarantee is testable with no worktree fixture at all.

    Returns:
    - the `current_checkpoint_id`, unchanged, if it is set and its own
      status is `IN_PROGRESS` (rule 1 -- an interrupted checkpoint resumes
      without any human reconciliation of narrative/doc state, missing-
      test item 31);
    - otherwise the first checkpoint in the registry JSON array's own
      order whose status is not `COMPLETE` and whose every `depends_on`
      entry is `COMPLETE` (rule 2);
    - `None` if every registry checkpoint is already `COMPLETE` -- a
      legitimate, distinct outcome from being blocked, signaling the
      caller to drive the `IMPLEMENTING` -> `SELF_REVIEWING_IMPLEMENTATION`
      transition by calling `enter_self_reviewing_implementation`, which
      owns that edge and is a no-op when the phase is already there.

      This clause used to add that `complete_checkpoint` "already does
      this the moment the last checkpoint completes, so a caller only ever
      observes `None` here on a stale/out-of-band re-check". That was
      false and load-bearing (salvage audit `B8`): `None` is also the
      correct answer on the *first* selection after a plan re-approval on
      an item whose checkpoints are all already `COMPLETE`, where
      `apply_plan_approval` has legitimately just written `IMPLEMENTING`
      and `complete_checkpoint` has no checkpoint left to fire on. Both
      wrap-up branches took the sentence at face value, named no writer,
      and wedged the item.

    Raises `NoCheckpointReadyError` (rule 4) when at least one checkpoint
    remains incomplete but none is currently selectable -- names the
    specific blocked checkpoint and its unmet dependencies, never silently
    idling.
    """
    checkpoints = work_item.get("checkpoints", {})
    current_id = work_item.get("current_checkpoint_id")
    if current_id is not None and checkpoints.get(current_id, {}).get("status") == "IN_PROGRESS":
        return current_id

    complete = {cid for cid, entry in checkpoints.items() if entry.get("status") == "COMPLETE"}
    for entry in registry["checkpoints"]:
        checkpoint_id = entry["id"]
        if checkpoint_id in complete:
            continue
        depends_on = entry.get("depends_on", [])
        if all(dep in complete for dep in depends_on):
            return checkpoint_id

    incomplete = [entry for entry in registry["checkpoints"] if entry["id"] not in complete]
    if not incomplete:
        return None

    blocked = incomplete[0]
    unmet = [dep for dep in blocked.get("depends_on", []) if dep not in complete]
    raise NoCheckpointReadyError(
        f"no checkpoint is currently selectable: {blocked['id']!r} is the first "
        f"incomplete entry in registry order, blocked on incomplete "
        f"dependencies {unmet}",
        checkpoint_id=blocked["id"],
    )


# ---------------------------------------------------------------------------
# workflow-2.4.0 round 4 (XMODEL-R4-B1): closing the amendment-vs-checkpoint-
# start race. `request_plan_amendment`'s own new guard (below, in the
# amendment error family) refuses to supersede `plan_approval` while a
# checkpoint is IN_PROGRESS or a shared checkpoint claim is outstanding, but
# that guard alone cannot close the race: `claim_checkpoint` is published
# (step 1d, "before `transition_checkpoint_in_progress`") in a *separate*
# synchronization domain (the filesystem claims directory) from
# `WORKFLOW_STATE.json`'s own lock, so a claim can be outstanding while
# state still looks idle (`resolve_checkpoint_ownership`'s own supported
# `CONTINUE_CLAIM` window). The second, independent half of the fix lives
# here: `transition_checkpoint_in_progress` itself refuses to publish
# `IN_PROGRESS` once the work item has left a legal checkpoint-execution
# phase -- so even if `request_plan_amendment`'s state_transaction commits
# first (observing no claim yet), the checkpoint worker's own later
# state_transaction, now reading `AMENDING_PLAN`, is refused rather than
# publishing live implementation state on top of a superseded plan.
# `IMPLEMENTING` is the only legal source phase: `SELF_REVIEWING_
# IMPLEMENTATION` is reached only once every registry checkpoint is already
# `COMPLETE` (`complete_checkpoint`), and no supported path ever restarts a
# checkpoint from there.
# ---------------------------------------------------------------------------

CHECKPOINT_START_LEGAL_PHASES = frozenset({"IMPLEMENTING"})


class IllegalCheckpointStartPhaseError(Exception):
    """Raised when `transition_checkpoint_in_progress` is called from a
    phase other than `IMPLEMENTING` (`CHECKPOINT_START_LEGAL_PHASES`,
    XMODEL-R4-B1) -- most importantly `AMENDING_PLAN`, which a checkpoint
    claim published before this work item's amendment transition committed
    can otherwise reach, publishing live `IN_PROGRESS` implementation state
    on top of an already-superseded `plan_approval`. Names the actual phase
    and the legal set, the same behavioural-refusal shape
    `IllegalBundleGenerationSourcePhaseError`/`IllegalSelfReviewEntryPhaseError`
    use for their own phase-guarded writers.

    Also raised, for the same reason and against the same legal set, by
    `claim_checkpoint` itself (`XMODEL-R8-B1`): that function's own
    pre-publication phase check is this error's second, independent call
    site, closing the window this docstring's first paragraph describes
    rather than merely detecting it after the fact -- within one worktree
    root only; `XMODEL-R9-B1` (`docs/defects/v2.4.0-002-amendment-claim-
    race-crosses-worktree-boundary.md`) records that the same claim
    published from a different linked worktree of the same repository is
    not caught by either call site -- a claim published into that window
    would otherwise still be refused here, later, by
    `transition_checkpoint_in_progress`, but would already have leaked
    onto disk with nothing left to release it."""


def transition_checkpoint_in_progress(
    state: dict, work_item_id: str, checkpoint_id: str, start_commit: str, now: str,
) -> dict:
    """The state half of D3's `IN_PROGRESS` transition: records
    `checkpoints[checkpoint_id] = {status: IN_PROGRESS, start_commit}` and
    sets `current_checkpoint_id`. The filesystem half --
    `write_worktree_identity` -- is a separate call the caller makes
    alongside this one, since it touches a local, gitignored file this
    module's other state writers never touch. Returns a new state dict.

    Refuses (`IllegalCheckpointStartPhaseError`) unless the work item's
    current phase is in `CHECKPOINT_START_LEGAL_PHASES` -- XMODEL-R4-B1's
    second, independent guard, checked here against the freshly re-read
    state inside this function's own `state_transaction`, so it applies
    even when a checkpoint claim was published before an amendment
    transition landed (see the section comment above)."""
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    phase = work_item.get("phase")
    if phase not in CHECKPOINT_START_LEGAL_PHASES:
        raise IllegalCheckpointStartPhaseError(
            f"{work_item_id!r} is at phase {phase!r} -- a checkpoint can only start "
            f"IN_PROGRESS from phase in {sorted(CHECKPOINT_START_LEGAL_PHASES)}"
        )
    work_item["checkpoints"][checkpoint_id] = {"status": "IN_PROGRESS", "start_commit": start_commit}
    work_item["current_checkpoint_id"] = checkpoint_id
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    return new_state


def complete_checkpoint(
    state: dict, work_item_id: str, checkpoint_id: str, registry: dict, now: str, *, repo_root: Path,
) -> dict:
    """D3's checkpoint-complete-vs-all-complete semantics: marks
    `checkpoint_id` `COMPLETE`, resets `current_checkpoint_id` to `null`
    (phase stays `IMPLEMENTING` between checkpoints), and additionally
    transitions `phase` to `SELF_REVIEWING_IMPLEMENTATION` only when every
    checkpoint named in the registry is now `COMPLETE` -- the two
    conditions are deliberately distinct: completing one checkpoint is
    never itself evidence the whole work item is done. Returns a new
    state dict.

    `WFR-69` (`WF8c` item (o)): before any write, a checkpoint whose own
    registry entry declares `completion_obligations` must first pass a
    second, non-approval-gated pre-flight per obligation -- re-derived
    directly against the **live working tree** at the moment of
    completion, deliberately never a call into
    `resolve_completion_obligations`/the approval-bound verifier-authority
    chain `WFO-LEDGER-COVERAGE`'s own eventual `technical_approval` owns,
    since that approval cannot exist yet the first time a checkpoint
    reaches `COMPLETE` (implementation always precedes
    `SELF_REVIEWING_IMPLEMENTATION`/technical approval, never the other
    way around). An obligation id with no pre-flight bound to it fails
    closed -- it blocks completion rather than passing vacuously, the same
    fail-closed default `UNKNOWN_OBLIGATION` applies elsewhere in this
    module. Raises `UnsatisfiedCompletionObligationError`, naming the
    checkpoint and every unresolved obligation/item, before
    `checkpoint_id`'s status is written and before `current_checkpoint_id`
    resets -- so a refused completion leaves the checkpoint selectable and
    the registry non-terminal (`select_next_checkpoint` keeps returning
    it). This pre-flight is deliberately weaker than, and never a
    substitute for, `WFO-LEDGER-COVERAGE`'s own approval-bound verdict:
    passing it is necessary but never sufficient for `MILESTONE_COMPLETE`,
    which still independently requires `complete_work_item`'s own
    `resolve_completion_obligations` call to derive `PASS`."""
    entry_spec = next((c for c in registry.get("checkpoints", []) if c["id"] == checkpoint_id), None)
    obligation_ids = (entry_spec or {}).get("completion_obligations", [])
    if obligation_ids:
        # OPUS-R129-M01: a relative repo_root reaches _load_and_run_named_test,
        # which runs its evidence-test subprocess with cwd=<repo_root>/scripts
        # -- against a relative repo_root, the child resolves that relative
        # path against its own (already scripts/-rooted) cwd and silently
        # imports nothing, failing every evidence lookup closed but with a
        # maximally alarming, spurious "almost everything unresolved" report.
        # Resolving here, once, before any pre-flight runs, fixes every
        # caller regardless of how repo_root was spelled.
        repo_root = Path(repo_root).resolve()
        outstanding: dict[str, list] = {}
        for obligation_id in obligation_ids:
            pre_flight = _PRE_CHECKPOINT_COMPLETION_PRE_FLIGHT.get(obligation_id)
            if pre_flight is None:
                outstanding[obligation_id] = ["<no WFR-69 pre-flight bound to this obligation id>"]
                continue
            unresolved_items = pre_flight(repo_root, work_item_id)
            if unresolved_items:
                outstanding[obligation_id] = unresolved_items
        if outstanding:
            raise UnsatisfiedCompletionObligationError(
                f"{checkpoint_id!r} cannot reach checkpoint-COMPLETE -- WFR-69 pre-flight "
                f"found undischarged reconciliation-table item(s) per obligation: {outstanding}"
            )

    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    entry = work_item["checkpoints"].setdefault(checkpoint_id, {})
    entry["status"] = "COMPLETE"
    work_item["current_checkpoint_id"] = None
    work_item["last_completed_checkpoint_id"] = checkpoint_id
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    all_ids = [entry["id"] for entry in registry["checkpoints"]]
    if all(work_item["checkpoints"].get(cid, {}).get("status") == "COMPLETE" for cid in all_ids):
        work_item["phase"] = "SELF_REVIEWING_IMPLEMENTATION"
    return new_state


def enter_self_reviewing_implementation(
    state: dict, work_item_id: str, registry: dict, now: str,
) -> dict:
    """`SELF_REVIEWING_IMPLEMENTATION`'s **second** writer, and the one
    `/milestone-implement`'s and `/bootstrap-workflow-v2`'s own
    `NO_CHECKPOINT` terminal-wrap-up branches call (salvage audit `B8`,
    convergence pass 7).

    Before this existed, `complete_checkpoint` above was the phase's sole
    writer, reachable only as a side effect of a checkpoint *transitioning*
    to `COMPLETE`. Both commands' wrap-up branches say "enter
    `SELF_REVIEWING_IMPLEMENTATION`" in prose and named no state writer at
    all, which is invisible on the ordinary path -- the branch is entered
    on the invocation *after* the last checkpoint completed, when
    `complete_checkpoint` has already written the phase -- but wedges the
    work item permanently the moment the branch is entered from a real
    `IMPLEMENTING`: a plan re-approval (`apply_plan_approval`, which sets
    `IMPLEMENTING` unconditionally and correctly) on an item whose every
    registry checkpoint is already `COMPLETE`. `select_next_checkpoint`
    then returns `None`, `resolve_checkpoint_ownership` returns
    `NO_CHECKPOINT`, and every outgoing lifecycle path refuses:
    `record_bundle_generation` at both stages
    (`IllegalBundleGenerationSourcePhaseError`),
    `enter_applying_review_feedback`
    (`IllegalApplyingReviewFeedbackEntryPhaseError`), and
    `/accept-milestone` (`milestone_complete_gate_reachable` is `False`
    for `IMPLEMENTING`, whatever the registry says).

    The three outcomes, in the order they are decided:

    - `phase == "SELF_REVIEWING_IMPLEMENTATION"` already: **returns the
      input state unchanged**, with no `state_revision` bump and no
      `last_transition` rewrite. This is the ordinary path's own case, and
      it must stay a true no-op -- the wrap-up branch is re-entered on
      every subsequent invocation until the bundle is generated, and a
      writer that bumped the revision each time would manufacture a state
      change out of a read-only re-check.
    - `phase == "IMPLEMENTING"` and every checkpoint named in `registry`
      is `COMPLETE`: writes the transition, bumps `state_revision`, sets
      `last_transition`. Same all-complete predicate `complete_checkpoint`
      applies, deliberately re-derived from `registry` rather than trusted
      from any stored phase value.
    - `phase == "IMPLEMENTING"` with an outstanding checkpoint:
      `IncompleteCheckpointsForSelfReviewError`, naming it. There is real
      work left; the caller must implement it, not skip it.

    Any other phase raises `IllegalSelfReviewEntryPhaseError`. Returns a
    new state dict (or the input, unchanged, for the no-op case)."""
    work_item = state["work_items"][work_item_id]
    phase = work_item.get("phase")
    if phase == "SELF_REVIEWING_IMPLEMENTATION":
        return state
    if phase != "IMPLEMENTING":
        raise IllegalSelfReviewEntryPhaseError(
            f"enter_self_reviewing_implementation invoked for {work_item_id!r} from phase "
            f"{phase!r}, but the only legal source phase is 'IMPLEMENTING' (a work item "
            f"already at 'SELF_REVIEWING_IMPLEMENTATION' is a no-op)"
        )
    is_terminal, outstanding = registry_completion_status(work_item, registry)
    if not is_terminal:
        raise IncompleteCheckpointsForSelfReviewError(
            f"{work_item_id!r} cannot enter SELF_REVIEWING_IMPLEMENTATION -- checkpoint "
            f"{outstanding!r} is not COMPLETE; that phase's entry condition is "
            f"'all checkpoints implemented', so the outstanding checkpoint must be "
            f"implemented through /milestone-implement, never skipped",
            outstanding_checkpoint_id=outstanding,
        )
    new_state = copy.deepcopy(state)
    new_work_item = new_state["work_items"][work_item_id]
    new_work_item["phase"] = "SELF_REVIEWING_IMPLEMENTATION"
    new_work_item["state_revision"] = new_work_item.get("state_revision", 1) + 1
    new_work_item["last_transition"] = now
    return new_state


def _git_identity(repo_root: Path) -> tuple[str, str, str]:
    """`(repo_root, git_common_dir, worktree_root)` as `WORKTREE_IDENTITY.json`
    stores them -- `git_common_dir` resolved to an absolute path since Git
    reports it relative to `cwd` for a normal (non-worktree) checkout."""
    repo_root_out = _run(["git", "rev-parse", "--show-toplevel"], cwd=repo_root).strip()
    raw_common_dir = _run(["git", "rev-parse", "--git-common-dir"], cwd=repo_root).strip()
    git_common_dir = raw_common_dir if Path(raw_common_dir).is_absolute() else str((repo_root / raw_common_dir).resolve())
    return repo_root_out, git_common_dir, repo_root_out


def _hash_dirty_paths(repo_root: Path) -> list[dict]:
    """`{path, sha256}` for every currently dirty path this module's own
    `_dirty_paths` reports, hashing the working-tree file's real bytes
    directly (not `git hash-object`) -- a deleted-but-dirty path is simply
    omitted, since there is no content left to hash."""
    entries = []
    for path in sorted(_dirty_paths(repo_root)):
        full = repo_root / path
        if not full.is_file():
            continue
        entries.append({"path": path, "sha256": hashlib.sha256(full.read_bytes()).hexdigest()})
    return entries


WORKTREE_IDENTITY_LOCK_PATH = Path(".ai-review/runtime/WORKTREE_IDENTITY.lock")


def identity_document_lock_path(repo_root: Path, path: Path = WORKTREE_IDENTITY_LOCK_PATH) -> Path:
    return repo_root / path


@contextlib.contextmanager
def identity_document_lock(repo_root: Path, *, lock_path: Path = WORKTREE_IDENTITY_LOCK_PATH):
    """`D-Checkpoint-Ownership` ("Because it is now load-bearing, its own
    writes must be serialized", `OPUS-R86-002`): `WORKTREE_IDENTITY.json`
    holds every work item's entry in one document, and a plain
    read-modify-write lost an entry in 12/12 threaded trials -- the window
    held open by the `git` subprocesses `_hash_dirty_paths` spawns between
    the read and the write. Every writer of this document (this module's
    own `write_worktree_identity`/`repair_worktree_identity`, and any
    future one) must hold this lock across its whole
    load -> validate -> mutate -> publish sequence.

    A **stable, never-unlinked** object beside the document, per-worktree
    (not per work item, since that is the scope of the document being
    protected), `fcntl.flock`-serialized. Advisory, so it is defense in
    depth layered under the publish primitive's own atomicity, not a
    substitute for it. Released by the kernel on process death, so a crash
    while holding it cannot become a second class of permanent lockout.
    "Never unlinked" is a property of this lock's own writers, not of the
    filesystem: an external wipe of `.ai-review/` (`git clean -xdf`, a
    stale worktree cleanup) removes it like anything else and a
    recreated file gets a new inode, so no serialization claim survives
    such a wipe -- that is an identity-record-destroying event this
    design already treats as the operator's own act.

    It is a **leaf lock**: never held across a `WORKFLOW_STATE.json`
    write, so it can never be held at the same time as `D1`'s state-file
    lock, and no other lock in this design is ever acquired while it is
    held."""
    full = identity_document_lock_path(repo_root, lock_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(full, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _publish_worktree_identity(full_path: Path, document: dict) -> None:
    """The **single** `os.replace` publication (`OPUS-R86-005`): the
    snapshot is built and validated in memory before this is ever called,
    and this is the only statement in the sequence that ever touches the
    final pathname -- a same-directory temp file, written whole, then
    `os.replace`d over the target. A writer that writes the final
    pathname directly and only *then* stages/replaces a temp file (as an
    earlier draft here did) satisfies no atomicity property at all; this
    is the corrected, single-write form."""
    full_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(full_path.parent), prefix=f".{full_path.name}-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, full_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_worktree_identity(
    repo_root: Path, work_item_id: str, *, now: str, path: Path = WORKTREE_IDENTITY_PATH,
) -> dict:
    """D3's named writer (resolves `OPUS-R6-026`, optional): the command
    that transitions a checkpoint to `IN_PROGRESS` (WF2's own
    `/milestone-implement`, or the bootstrap command for
    `workflow-v2-1-core`'s own checkpoints) creates or refreshes **only
    its own work item's entry** here -- `expected_dirty_paths_by_work_item`
    is keyed by `work_item_id` precisely so two work items with interleaved
    `IN_PROGRESS` dirty work never disturb each other's entry when this is
    called (`OPUS-R10-008`, missing-test item 71).

    The snapshot recorded per path (`path`, `sha256` of the file's current
    bytes) is diagnostic audit data, not a resume-blocking equality gate:
    `verify_dirty_resume_safety` never compares it against a later dirty
    state. An exact dirty-set match would break resuming an interrupted
    checkpoint (missing-test item 31) -- the whole point of resuming is to
    keep editing, so the dirty set is expected to keep changing between
    the moment this snapshot is taken and the next time it's read.

    The whole load -> validate -> mutate -> publish sequence runs inside
    `identity_document_lock` (`OPUS-R86-002`) and publishes by the single
    `os.replace` `_publish_worktree_identity` performs (`OPUS-R86-005`),
    so two work items' interleaved writes -- the routine, expected case --
    can no longer lose an entry to a lost update. Returns the full,
    now-validated `WORKTREE_IDENTITY.json` document."""
    full_path = repo_root / path
    with identity_document_lock(repo_root):
        existing = _load_json(full_path) or {}
        repo_root_id, git_common_dir, worktree_root = _git_identity(repo_root)
        existing["repo_root"] = repo_root_id
        existing["git_common_dir"] = git_common_dir
        existing["worktree_root"] = worktree_root
        expected = existing.setdefault("expected_dirty_paths_by_work_item", {})
        expected[work_item_id] = _hash_dirty_paths(repo_root)
        existing["generated_at"] = now
        validate_worktree_identity(existing)
        _publish_worktree_identity(full_path, existing)
    return existing


def verify_dirty_resume_safety(
    repo_root: Path, work_item_id: str, *, path: Path = WORKTREE_IDENTITY_PATH,
) -> None:
    """D3's worktree-scoped dirty-resume rule, the read half of
    `write_worktree_identity` (missing-test items 31, 43, 71): a clean
    (`COMPLETE`) checkpoint is portable anywhere; an `IN_PROGRESS`
    checkpoint's uncommitted work is worktree-local, so resuming it
    (D-Selection rule 1) requires this worktree's own local
    `WORKTREE_IDENTITY.json` to exist, to record this exact worktree's own
    Git identity, and to carry an entry for this exact `work_item_id`
    (never another item's). Raises `WorktreeIdentityMissingError` for a
    missing file or missing per-work-item entry, `WorktreeIdentityMismatchError`
    for an identity mismatch; returns `None` (no exception) on a clean
    match. Never inspects `expected_dirty_paths_by_work_item`'s per-path
    content -- see `write_worktree_identity`'s docstring for why."""
    full_path = repo_root / path
    data = _load_json(full_path)
    if data is None:
        raise WorktreeIdentityMissingError(
            f"{path} not found -- cannot resume {work_item_id!r}'s IN_PROGRESS "
            f"checkpoint from a worktree with no local identity record"
        )
    validate_worktree_identity(data)
    repo_root_id, git_common_dir, worktree_root = _git_identity(repo_root)
    if (data["repo_root"], data["git_common_dir"], data["worktree_root"]) != (repo_root_id, git_common_dir, worktree_root):
        raise WorktreeIdentityMismatchError(
            f"{path} records a different worktree's identity than this one -- "
            f"resume {work_item_id!r}'s IN_PROGRESS checkpoint from the worktree that started it"
        )
    if work_item_id not in data.get("expected_dirty_paths_by_work_item", {}):
        raise WorktreeIdentityMissingError(
            f"{path} has no expected_dirty_paths_by_work_item entry for {work_item_id!r} -- "
            f"this worktree never started (or lost the record for) this work item's IN_PROGRESS checkpoint"
        )


# ---------------------------------------------------------------------------
# WF8b: D-Checkpoint-Ownership -- the origination reference only (revisions
# 69-72 of docs/ai-workflow/WORKFLOW_V2_PLAN.md's "The origination
# reference" and "The read fails closed, and the partition is total").
#
# This is a bounded first production slice, not the full mechanism: no
# shared claim record, no mutation/handoff guard, no explicit takeover, no
# adoption. It answers exactly one question -- can this worktree prove a
# locally `IN_PROGRESS` checkpoint was never observably supplied by a
# checkout, merge, or reset from committed history? -- and raises rather
# than deciding RESUME/FRESH/adopt itself, since those outcomes depend on
# the still-unimplemented shared claim. See "Where the check belongs, and
# the ordering" in the plan for the full reconciliation table this slice
# is one input to.
# ---------------------------------------------------------------------------


class CheckpointOriginationUnprovableError(Exception):
    """Raised when a checkpoint this worktree records `IN_PROGRESS` cannot
    be proven to have been originated here: either its `IN_PROGRESS` is
    observed at some commit in the origination reference (a checkout,
    merge, or reset could have supplied it) or that reference's read is
    itself undecidable at some commit. Per the plan's precedence rule, an
    observed route always wins over an undecidable one found in the same
    scan. Carries `.evidence` (`route`, the commit and status/reason, the
    state path, and how many commits were examined) for the caller to
    report. D-Checkpoint-Ownership's explicit-takeover escape is not yet
    implemented, so this refusal currently has no automated recovery --
    the caller must stop and report."""

    def __init__(self, message: str, *, evidence: dict):
        super().__init__(message)
        self.evidence = evidence


def origination_reference_commits(repo_root: Path, state_rel_path: str) -> list[str]:
    """`D-Checkpoint-Ownership`'s origination reference, stated exactly:
    every commit reachable from every ref in the repository (`--all`,
    every linked worktree's own `HEAD` included, detached included) that
    is not TREESAME to a parent for the state document (`--full-history`
    retains merges and follows every parent, so a commit reachable only
    through a merge's non-mainline side for this path is never silently
    dropped by History Simplification's default pruning). Raises
    `CheckpointOriginationUnprovableError` if the invocation itself cannot
    be resolved -- an unavailable reference is refused, never read as
    empty."""
    try:
        out = _run(
            ["git", "rev-list", "--all", "--full-history", "--", state_rel_path],
            cwd=repo_root,
        )
    except subprocess.CalledProcessError as exc:
        raise CheckpointOriginationUnprovableError(
            f"origination reference could not be resolved for {state_rel_path!r}: {exc}",
            evidence={"route": "reference_unresolvable", "state_rel_path": state_rel_path},
        ) from exc
    return [line for line in out.splitlines() if line]


def _checkpoint_status_at_commit(
    repo_root: Path, commit: str, work_item_id: str, checkpoint_id: str, state_rel_path: str,
) -> tuple[str, dict]:
    """One commit's contribution to the origination read, partitioned
    exactly per the plan's "The read fails closed, and the partition is
    total" table. Returns `(outcome, detail)`:

    - `"in_progress"` -- the checkpoint's status is decidably `IN_PROGRESS`
      at this commit (the observed refusal route);
    - `"undecidable"` -- this commit's contribution cannot be decided:
      unlistable tree/unresolvable commit, a state path present but not a
      readable regular-file blob, an unparseable or non-object document, a
      present-but-non-object member at any of the four levels
      (`work_items`/the work item/`checkpoints`/the checkpoint entry), or
      a checkpoint entry whose `status` is absent, not a string, or
      outside `CHECKPOINT_STATUSES`;
    - `"decidable"` -- this commit admits: the state path, or one of the
      four keys, is decidably absent from this commit, or the checkpoint's
      status is decidably something other than `IN_PROGRESS`.

    Absence at a level is always a missing **key**, checked with `in`,
    never a falsy or non-object value -- a present `null`/list/string/
    number at any of the four levels is `"undecidable"`, not absence."""
    listing = subprocess.run(
        ["git", "ls-tree", commit, "--", state_rel_path],
        cwd=repo_root, capture_output=True, text=True,
    )
    if listing.returncode != 0:
        return "undecidable", {"commit": commit, "reason": "tree unlistable or commit unresolvable"}
    line = listing.stdout.strip()
    if not line:
        return "decidable", {"commit": commit, "reason": "state path absent from tree"}
    meta, _, _ = line.partition("\t")
    mode = meta.split()[0]
    blob_sha = meta.split()[2]
    if mode not in ("100644", "100755"):
        return "undecidable", {"commit": commit, "reason": f"state path is not a regular-file blob (mode {mode})"}
    blob = subprocess.run(["git", "cat-file", "-p", blob_sha], cwd=repo_root, capture_output=True, text=True)
    if blob.returncode != 0:
        return "undecidable", {"commit": commit, "reason": "blob could not be read"}
    try:
        doc = json.loads(blob.stdout)
    except json.JSONDecodeError:
        return "undecidable", {"commit": commit, "reason": "state document unparseable"}
    if not isinstance(doc, dict):
        return "undecidable", {"commit": commit, "reason": "state document is not an object"}

    if "work_items" not in doc:
        return "decidable", {"commit": commit, "reason": "no work_items key"}
    work_items = doc["work_items"]
    if not isinstance(work_items, dict):
        return "undecidable", {"commit": commit, "reason": "work_items is not an object"}

    if work_item_id not in work_items:
        return "decidable", {"commit": commit, "reason": f"no {work_item_id!r} entry"}
    work_item = work_items[work_item_id]
    if not isinstance(work_item, dict):
        return "undecidable", {"commit": commit, "reason": f"{work_item_id!r} entry is not an object"}

    if "checkpoints" not in work_item:
        return "decidable", {"commit": commit, "reason": "no checkpoints key"}
    checkpoints = work_item["checkpoints"]
    if not isinstance(checkpoints, dict):
        return "undecidable", {"commit": commit, "reason": "checkpoints is not an object"}

    if checkpoint_id not in checkpoints:
        return "decidable", {"commit": commit, "reason": f"no {checkpoint_id!r} entry"}
    entry = checkpoints[checkpoint_id]
    if not isinstance(entry, dict):
        return "undecidable", {"commit": commit, "reason": f"{checkpoint_id!r} entry is not an object"}

    status = entry.get("status")
    if not isinstance(status, str) or status not in CHECKPOINT_STATUSES:
        return "undecidable", {"commit": commit, "reason": f"status is not a readable checkpoint status ({status!r})"}
    if status == "IN_PROGRESS":
        return "in_progress", {"commit": commit, "status": status}
    return "decidable", {"commit": commit, "status": status}


def checkpoint_origination_provable(
    repo_root: Path, work_item_id: str, checkpoint_id: str, *, state_rel_path: str | None = None,
) -> dict:
    """Whether this worktree can prove it originated `checkpoint_id`'s
    local `IN_PROGRESS`, per `D-Checkpoint-Ownership`'s origination
    reference. Scans every commit `origination_reference_commits`
    enumerates; a decidable `IN_PROGRESS` observation anywhere refuses
    unconditionally -- no supersession, no recency, no scoping to a
    lifecycle instance, the reduction rule stated normatively in the plan
    -- and, when both an observed and an undecidable commit exist in the
    same reference, the observed route is reported in preference to the
    undecidable one. Returns an evidence dict on success (admit); raises
    `CheckpointOriginationUnprovableError` (carrying the same evidence
    shape) on refusal.

    This function answers only the origination question. It deliberately
    does not itself decide `RESUME` vs `FRESH` vs adoption -- those
    outcomes, per the plan's reconciliation table, also depend on the
    shared claim record and mutation guard, neither of which this slice
    implements."""
    state_rel_path = state_rel_path or DEFAULT_STATE_PATH.as_posix()
    commits = origination_reference_commits(repo_root, state_rel_path)
    if not commits:
        return {
            "decision": "admit", "route": "no_reference_commits",
            "state_rel_path": state_rel_path, "examined_commits": 0,
        }

    observed = None
    undecidable = None
    for commit in commits:
        outcome, detail = _checkpoint_status_at_commit(repo_root, commit, work_item_id, checkpoint_id, state_rel_path)
        if outcome == "in_progress" and observed is None:
            observed = detail
        elif outcome == "undecidable" and undecidable is None:
            undecidable = detail

    if observed is not None:
        raise CheckpointOriginationUnprovableError(
            f"{work_item_id!r} checkpoint {checkpoint_id!r} is IN_PROGRESS at commit "
            f"{observed['commit']} in the origination reference -- a checkout, merge, or "
            f"reset could have supplied this worktree's own IN_PROGRESS state, so automatic "
            f"resume cannot be trusted (D-Checkpoint-Ownership's explicit-takeover escape is "
            f"not yet implemented -- stop and reconcile manually)",
            evidence={
                "route": "observed", "commit": observed["commit"], "status": "IN_PROGRESS",
                "state_rel_path": state_rel_path, "examined_commits": len(commits),
            },
        )
    if undecidable is not None:
        raise CheckpointOriginationUnprovableError(
            f"{work_item_id!r} checkpoint {checkpoint_id!r}'s origination reference contains "
            f"an undecidable commit ({undecidable['commit']}: {undecidable['reason']}) -- "
            f"origination is unprovable, not proved",
            evidence={
                "route": "undecidable", "commit": undecidable["commit"],
                "reason": undecidable["reason"], "state_rel_path": state_rel_path,
                "examined_commits": len(commits),
            },
        )
    return {
        "decision": "admit", "route": "scanned_all_decidable",
        "state_rel_path": state_rel_path, "examined_commits": len(commits),
    }


# ---------------------------------------------------------------------------
# WF8b: D-Checkpoint-Ownership -- the shared claim record and the
# mutation/handoff guard ("The record" and "Fencing: the mutation/handoff
# guard" in docs/ai-workflow/WORKFLOW_V2_PLAN.md, revisions 63-72),
# plus the explicit takeover and the abandoned-guard recovery it depends
# on. Authored fresh against the approved revision-80 text; the unwired
# dry-run prototype (docs/ai-workflow/dry-run/wf8b-s14-repro/
# checkpoint_ownership.py) is reference/reproduction evidence only.
#
# Scope, deliberately bounded per this session's own direction, continuing
# the prior session's origination-reference slice: no `resolve_ownership`/
# `classify_selection` (the `/milestone-implement` step 1c/1d/1f wiring
# lives under "Where the check belongs, and the ordering"), and no
# `WFR-66` identity-query enforcement (a distinct implementation surface,
# `authorize_identity_reference_gap`). Those remain future WF8b scope.
# `adopt_claim` itself ("Reconciling the two authorities") is defined
# further below, after the claim-publication and mutation-guard
# primitives it composes. This slice makes
# `take_over_claim`/`recover_abandoned_destructive_guard`/`adopt_claim`
# available and independently tested, which is the prerequisite the plan
# names for actually taking `v2-1-dry-run`'s interrupted `S-CP3` over for
# real.
# ---------------------------------------------------------------------------


class CheckpointOwnedByOtherWorktreeError(Exception):
    """Raised when claiming, asserting, or releasing a work item's
    checkpoint that is already claimed by a different worktree of this
    repository."""


class CheckpointOwnershipStateMismatchError(Exception):
    """Raised when this worktree's own claim cannot be reconciled with
    this worktree's own state -- reported, never guessed."""


class CheckpointOwnershipUnavailableError(Exception):
    """Raised when the shared claim/guard record cannot be read or
    written -- fails closed rather than proceeding unprotected."""


class CheckpointClaimTakeoverRefusedError(Exception):
    """Raised when an explicit takeover, guard clearance, or abandoned-
    guard recovery is attempted without the exact authorization its own
    contract requires."""


class CheckpointNotInProgressLocallyError(Exception):
    """Raised by `adopt_claim` when this worktree's own local
    `WORKFLOW_STATE.json` does not record the checkpoint being adopted as
    `IN_PROGRESS` -- adoption never invents an ownership fact where none
    exists."""


CLAIMS_RELDIR = "ai-workflow/checkpoint-claims"
CLAIM_SCHEMA_VERSION = 3

ORDINARY = "ordinary"
DESTRUCTIVE = "destructive"
GUARD_STEP_CLASSES = frozenset({ORDINARY, DESTRUCTIVE})

ABSENT_OBSERVATION = "absent"
# `OPUS-R83-002`: an observation of a record whose *bytes* cannot be read
# is hashed under its own domain tag, so it can never collide with the
# sha256 of some real record's content -- an authorization bound to
# "there is a symlink here" is never satisfiable by a byte-readable
# record, or the reverse.
UNREADABLE_OBSERVATION_DOMAIN = b"unreadable-checkpoint-record-v1\x00"
# Which unreadable kinds a rotation (`os.rename` onto the name) can
# actually replace. A symlink and an unreadable regular file are replaced
# by the rename itself, which never follows the link and never writes
# through it. A directory is not: `rename` refuses, and this design never
# removes a directory it did not create.
REPLACEABLE_UNREADABLE_KINDS = frozenset({"symlink", "unreadable-file", "not-a-regular-file"})


def _worktree_git_dir(repo_root: Path) -> str:
    """`git rev-parse --absolute-git-dir` -- the per-worktree admin
    directory. Recorded as **diagnostic** identity only, never as the
    ownership key: it survives `git worktree move`, which the recorded
    `worktree_root` does not, and that is exactly what lets a takeover
    tell "the holder was deleted" from "the holder was relocated" instead
    of guessing."""
    return _run(["git", "rev-parse", "--absolute-git-dir"], cwd=repo_root).strip()


def claims_dir(repo_root: Path) -> Path:
    _, common_dir, _ = _git_identity(repo_root)
    return Path(common_dir) / CLAIMS_RELDIR


def claim_path(repo_root: Path, work_item_id: str) -> Path:
    """One file per work item, named by digest so no `work_item_id`
    value -- including `../../escape`, `a/b/c`, or an absolute path --
    can address anything outside the claims directory (the same "token,
    not a path" discipline `D-Bundle-Manifest`'s `bundles/<token>`
    already uses)."""
    token = hashlib.sha256(work_item_id.encode()).hexdigest()
    return claims_dir(repo_root) / f"{token}.json"


def guard_path(repo_root: Path, work_item_id: str) -> Path:
    """The mutation/handoff guard (`GPT-R81-001`): one **fixed** pathname
    per work item, deliberately not token-scoped, because its whole
    purpose is to be the single object an owner's mutation and a
    takeover's rotation contend for."""
    token = hashlib.sha256(work_item_id.encode()).hexdigest()
    return claims_dir(repo_root) / f"{token}.lease"


def guard_mutation_lock_path(repo_root: Path, work_item_id: str) -> Path:
    """`OPUS-R83-001`: the stable object every guard **mutation**
    serializes on -- deliberately a different file from the guard itself,
    and deliberately created once and never unlinked. Locking the guard
    file would be useless for exactly the reason the finding exists: the
    guard's whole lifecycle is create-and-remove, and two processes
    holding `flock` on two different inodes that briefly shared one
    pathname are not serialized at all."""
    token = hashlib.sha256(work_item_id.encode()).hexdigest()
    return claims_dir(repo_root) / f"{token}.guardlock"


@contextlib.contextmanager
def guard_mutation_lock(repo_root: Path, work_item_id: str):
    """`D1`'s process-scoped `fcntl.flock` primitive, held across a whole
    guard mutation so **compare-and-remove is one indivisible step**
    rather than two statements a preemption can be scheduled between
    (`OPUS-R83-001`).

    Not the fence and must never be mistaken for one -- held for a
    handful of syscalls entirely inside one guard operation, while the
    *guard* is what spans a mutation window. Never held across a
    `WORKFLOW_STATE.json` write, so it is never held at the same time as
    `D1`'s state-file lock and the existing guard-then-`flock` ordering
    is untouched. Advisory, so `os.link`'s `EEXIST` exclusivity is
    retained underneath it as defense in depth rather than replaced by
    it. Released by the kernel on process death."""
    path = guard_mutation_lock_path(repo_root, work_item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_not_symlink(path.parent, "claims directory")
    _assert_not_symlink(path, "guard mutation lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _assert_not_symlink(path: Path, what: str) -> None:
    """The claims directory and the claim/guard file must all be real
    objects -- never follow a link out of the claims directory. A claim
    reached through one is not this repository's coordination state,
    whatever it contains."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CheckpointOwnershipUnavailableError(f"cannot stat {path} ({exc})") from exc
    if stat.S_ISLNK(st.st_mode):
        raise CheckpointOwnershipUnavailableError(
            f"{what} {path} is a symbolic link -- refusing to read or publish checkpoint "
            f"ownership through a link out of the claims directory"
        )


def _read_claim_bytes(path: Path) -> bytes | None:
    _assert_not_symlink(path.parent, "claims directory")
    _assert_not_symlink(path, "claim record")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise CheckpointOwnershipUnavailableError(
                f"{path} is a symbolic link -- refusing to resolve checkpoint ownership "
                f"through it"
            ) from exc
        raise CheckpointOwnershipUnavailableError(f"cannot read {path} ({exc})") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            return handle.read()
    except OSError as exc:
        # A directory `open`s successfully and fails at `fdopen`/`read`
        # with `EISDIR` -- refuse with the declared error rather than an
        # undeclared `IsADirectoryError` escaping to the caller.
        try:
            os.close(fd)
        except OSError:
            pass
        raise CheckpointOwnershipUnavailableError(
            f"cannot read {path} ({exc}) -- refusing to resolve checkpoint ownership "
            f"against a record whose bytes cannot be read") from exc


def _validate_claim(path: Path, data, work_item_id: str) -> dict:
    if not isinstance(data, dict) or data.get("schema_version") != CLAIM_SCHEMA_VERSION:
        raise CheckpointOwnershipUnavailableError(
            f"{path} has an unsupported shape/schema_version (expected {CLAIM_SCHEMA_VERSION})")
    required = ("work_item_id", "checkpoint_id", "repo_root", "git_common_dir",
                "worktree_root", "worktree_git_dir", "claimed_at", "owner_token")
    missing = [field for field in required if not isinstance(data.get(field), str)]
    if missing:
        raise CheckpointOwnershipUnavailableError(f"{path} is missing/malformed fields {missing}")
    if data["work_item_id"] != work_item_id:
        raise CheckpointOwnershipUnavailableError(
            f"{path} records work item {data['work_item_id']!r}, not {work_item_id!r}")
    return data


def resolve_claim(repo_root: Path, work_item_id: str) -> dict | None:
    """The one read every ownership decision performs. `None` means
    unclaimed. Every other failure mode -- unreadable, torn, wrong
    schema, wrong work item, reached through a symlink -- raises rather
    than returning `None`, so an undecidable claim can never be mistaken
    for an absent one."""
    path = claim_path(repo_root, work_item_id)
    raw = _read_claim_bytes(path)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointOwnershipUnavailableError(
            f"{path} exists but could not be read as JSON ({exc}) -- refusing to start or "
            f"resume any checkpoint against an unreadable claim record; clear it with the "
            f"explicit takeover operation, never by guessing"
        ) from exc
    return _validate_claim(path, data, work_item_id)


def describe_unreadable_record(path: Path) -> dict | None:
    """What `lstat` alone can say about a record whose bytes cannot be
    read (`OPUS-R83-002`). Returns `None` when the record is absent or
    genuinely byte-readable -- in which case `observation_id` over its
    bytes applies. Derived from durable, re-verifiable facts only, and
    for a symlink from the raw link target rather than anything read
    *through* it: the target is never opened, so an off-tree file is
    never touched."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return {"kind": "unstattable", "errno": errno.errorcode.get(exc.errno, str(exc.errno))}
    if stat.S_ISLNK(st.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            target = None
        return {"kind": "symlink", "link_target": target}
    if stat.S_ISDIR(st.st_mode):
        return {"kind": "directory"}
    if not stat.S_ISREG(st.st_mode):
        return {"kind": "not-a-regular-file", "st_mode": stat.S_IFMT(st.st_mode)}
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        return {"kind": "unreadable-file",
                "errno": errno.errorcode.get(exc.errno, str(exc.errno)),
                "st_mode": stat.S_IMODE(st.st_mode)}
    os.close(fd)
    return None


def unreadable_observation_id(descriptor: Mapping) -> str:
    payload = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(UNREADABLE_OBSERVATION_DOMAIN + payload).hexdigest()


def observation_id(raw: bytes | None) -> str:
    """The immutable identity of *the exact record a human reviewed*
    (`GPT-R81-002`). Defined over raw bytes rather than parsed content,
    so an unreadable or torn record has an equally binding observation
    instead of falling back to a reusable generic authorization.
    `"absent"` is itself an observation: authorizing a takeover of "no
    claim" must not survive somebody publishing one in the meantime."""
    if raw is None:
        return ABSENT_OBSERVATION
    return hashlib.sha256(raw).hexdigest()


def observe_claim(repo_root: Path, work_item_id: str) -> tuple[str, dict | None, str | None, dict | None]:
    """Read the claim **and** its observation id in one pass, reporting
    rather than raising on an undecidable record: returns
    `(observation_id, parsed_claim_or_None, error_or_None,
    unreadable_descriptor_or_None)`. `resolve_claim` still fails closed
    for ordinary ownership decisions; this exists for the operations that
    must be able to *describe* a record they refuse to act on
    (`takeover_evidence` and a rotation's own re-read)."""
    path = claim_path(repo_root, work_item_id)
    try:
        raw = _read_claim_bytes(path)
    except CheckpointOwnershipUnavailableError as exc:
        descriptor = describe_unreadable_record(path)
        if descriptor is None:
            raise
        return unreadable_observation_id(descriptor), None, str(exc), descriptor
    oid = observation_id(raw)
    if raw is None:
        return oid, None, None, None
    try:
        data = json.loads(raw)
        return oid, _validate_claim(path, data, work_item_id), None, None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return oid, None, f"unparseable claim record ({exc})", None
    except CheckpointOwnershipUnavailableError as exc:
        return oid, None, str(exc), None


def claim_is_this_worktree(repo_root: Path, claim: dict) -> bool:
    repo_root_id, common, worktree_root = _git_identity(repo_root)
    return (claim.get("repo_root"), claim.get("git_common_dir"), claim.get("worktree_root")) == (
        repo_root_id, common, worktree_root)


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def _build_claim_record(repo_root: Path, work_item_id: str, checkpoint_id: str, now: str,
                        *, adopted: bool = False, taken_over_from: dict | None = None,
                        takeover_count: int = 0,
                        previous_owner_tokens: tuple[str, ...] | list[str] = ()) -> dict:
    repo_root_id, common, worktree_root = _git_identity(repo_root)
    record = {
        "schema_version": CLAIM_SCHEMA_VERSION,
        "work_item_id": work_item_id,
        "checkpoint_id": checkpoint_id,
        "repo_root": repo_root_id,
        "git_common_dir": common,
        "worktree_root": worktree_root,
        "worktree_git_dir": _worktree_git_dir(repo_root),
        "claimed_at": now,
        "adopted": adopted,
        # `GPT-R81-001`: ownership is durable data a fresh session can
        # present, not a process-lifetime property. Minted here and
        # nowhere else; a takeover rotates it, which is what makes the
        # displaced owner's next assertion fail.
        "owner_token": secrets.token_hex(16),
        "takeover_count": takeover_count,
        "previous_owner_tokens": list(previous_owner_tokens),
    }
    if taken_over_from is not None:
        record["taken_over_from"] = taken_over_from
    return record


def _stage_claim_payload(path: Path, record: dict, *, allow_unreadable_target: bool = False) -> Path:
    """Stage the payload in the claims directory. The claims **directory**
    must always be a real directory -- that assertion is never relaxed.
    `allow_unreadable_target` relaxes the assertion on the final *name*
    only, for the two authorized operations that exist to replace an
    undecidable record (`OPUS-R83-002`): safe because staging happens at
    a fresh temp name and publication is `os.rename` onto the final one,
    which replaces the link, never follows it, never writes through it."""
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    _assert_not_symlink(path.parent, "claims directory")
    if not allow_unreadable_target:
        _assert_not_symlink(path, "claim record")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".claim-", suffix=".tmp")
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise CheckpointOwnershipUnavailableError(
            f"cannot stage a claim next to {path} ({exc}) -- refusing to start a checkpoint "
            f"whose claim cannot be made discoverable to this repository's other worktrees"
        ) from exc
    return Path(tmp_name)


def _publish_claim_replacing(path: Path, record: dict, *, allow_unreadable_target: bool = False) -> None:
    """The takeover's/recovery's publication. `os.rename` over the
    existing name is atomic and never leaves the path absent, so an
    authorized rotation that fails mid-way leaves the *previous* claim in
    force rather than silently unprotecting the work item. Never used for
    ordinary acquisition, which must fail rather than replace."""
    tmp = _stage_claim_payload(path, record, allow_unreadable_target=allow_unreadable_target)
    try:
        os.rename(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise CheckpointOwnershipUnavailableError(
            f"cannot publish the replacement claim at {path} ({exc}) -- the previous claim is "
            f"left in force; nothing was released"
        ) from exc


def _publish_claim_exclusive(path: Path, record: dict) -> None:
    """Atomic in **both** senses -- complete content, and create-if-absent.
    Write the whole payload to a temp file in the same directory, then
    `os.link` it into place: the link either creates the final name with
    fully-written content, or fails `EEXIST` because somebody else won.
    There is no window in which a reader can observe a partially-written
    claim, so a crash can never leave a record that fails closed for
    everybody."""
    tmp = _stage_claim_payload(path, record)
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise
    except OSError as exc:
        raise CheckpointOwnershipUnavailableError(
            f"cannot publish {path} ({exc}) -- refusing to start a checkpoint whose claim "
            f"cannot be made discoverable to this repository's other worktrees"
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)


def _claim_or_refuse(repo_root: Path, work_item_id: str, record: dict) -> dict:
    """IMPL9-O2: the contended branch below reuses `record["worktree_root"]`
    -- already resolved once, outside any lock, by `_build_claim_record`'s
    own `_git_identity` call before either caller (`claim_checkpoint`,
    `adopt_claim`) enters its locked critical section -- rather than
    spawning a second `git rev-parse` subprocess while `claim_checkpoint`'s
    `state_lock` is held. `state_lock`'s own docstring frames the critical
    section as a short read -> mutate -> publish window; a contending
    writer should not additionally wait for a process spawn only needed to
    format this function's own refusal message."""
    path = claim_path(repo_root, work_item_id)
    try:
        _publish_claim_exclusive(path, record)
    except FileExistsError:
        existing = resolve_claim(repo_root, work_item_id)
        worktree_root = record["worktree_root"]
        if existing is not None and not claim_is_this_worktree(repo_root, existing):
            raise CheckpointOwnedByOtherWorktreeError(
                f"{work_item_id!r} checkpoint {existing.get('checkpoint_id')!r} is already "
                f"claimed by worktree {existing.get('worktree_root')!r} (this worktree is "
                f"{worktree_root!r}) -- resume it there, or take the claim over explicitly"
            ) from None
        if existing is not None and existing.get("checkpoint_id") != record["checkpoint_id"]:
            raise CheckpointOwnershipStateMismatchError(
                f"this worktree already claims {work_item_id!r} checkpoint "
                f"{existing.get('checkpoint_id')!r}; refusing to silently repoint it at "
                f"{record['checkpoint_id']!r}"
            ) from None
        return existing if existing is not None else record
    return record


def claim_checkpoint(repo_root: Path, work_item_id: str, checkpoint_id: str, *, now: str) -> dict:
    """Acquire a fresh-start claim. Intended to be called at step 1d
    **after** this worktree's own identity record is established and
    **before** `transition_checkpoint_in_progress` -- both orderings are
    load-bearing, not stylistic, per "Where the check belongs, and the
    ordering" (future WF8b scope wires this call site; this function is
    the primitive it will call).

    `XMODEL-R8-B1`: publication itself now runs inside `state_lock` --
    the identical `WORKFLOW_STATE.lock` `state_transaction` holds across
    `request_plan_amendment`'s complete authoritative-quiescence-read ->
    supersede -> write critical section. Before round 8, this function
    published the claim through `_claim_or_refuse` alone, which
    synchronizes claim publication against other claim publications
    (`os.link`'s own atomicity) but against nothing in the
    `WORKFLOW_STATE.json` domain at all, so a claim could still be
    published in the exact window between
    `request_plan_amendment`'s own `resolve_claim(...)` read (which
    observes no claim) and that same call's later `AMENDING_PLAN` commit
    -- both reads/writes of `state`, and this function's own state read
    below, are real Git-repo-relative file operations, not in-memory
    values, so nothing but a shared lock can order them. Sharing the one
    lock file makes the two operations strictly ordered: whichever
    acquires it first completes its entire critical section --
    including this function's own claim publication, or
    `request_plan_amendment`'s entire supersede-and-commit -- before the
    other's begins. A work item with no `WORKFLOW_STATE.json` entry at
    all has no amendment mechanism that could race this call, so the
    phase check below is skipped for it (matching every other
    state-aware precondition in this module that stays silent absent an
    entry) -- most of this module's own claim-record unit tests exercise
    exactly that unregistered-work-item shape, by design.

    **Scoped to one worktree root, not the repository (`XMODEL-R9-B1`,
    narrowing the claim above, round 9 external implementation review).**
    `STATE_LOCK_PATH` resolves as `repo_root / ".ai-review/runtime/
    WORKFLOW_STATE.lock"` -- one lock file *per worktree* -- while
    `claims_dir(repo_root)` (this function's own claim-record home) is
    `git_common_dir`-rooted and shared by every linked worktree of the
    same repository. Two processes in two different worktrees therefore
    take `flock` on two different inodes and are not serialized at all,
    and this function's own phase check below reads `repo_root /
    DEFAULT_STATE_PATH` -- *this worktree's own working-tree copy* of
    `WORKFLOW_STATE.json` -- which cannot observe a phase committed only
    to another worktree's own branch, so even perfect serialization would
    not by itself make an amendment made from worktree A visible to a
    claim attempted from worktree B. The guarantee above is real and
    load-bearing *only within one worktree root*; deliberately left open
    across linked worktrees for `2.4.0` rather than half-fixed --
    `docs/defects/v2.4.0-002-amendment-claim-race-crosses-worktree-
    boundary.md` records the residual, the two independent reasons above,
    and what a future release closing it would need (a `claims_dir`-rooted
    serialization primitive plus a repo-global witness for `AMENDING_PLAN`,
    neither of which this release adds).

    Raises `IllegalCheckpointStartPhaseError` -- naming the actual phase
    and `CHECKPOINT_START_LEGAL_PHASES` -- and publishes nothing, when a
    registered work item's current phase (re-read fresh, under the lock,
    never from a caller-supplied snapshot) is not legal for a checkpoint
    start. This is what leaves no orphaned claim behind when the
    amendment side of the race wins **in the same worktree**: `AMENDING_PLAN`
    is already durable in this worktree's own working-tree state by the
    time this function's own lock acquisition succeeds, so the claim this
    function would otherwise publish is refused before a single byte of
    it reaches disk.

    IMPL9-O3: two other functions publish a claim without this check --
    `adopt_claim` (through `_claim_or_refuse` directly) and
    `take_over_claim` (through `_publish_claim_replacing`). Both are safe
    only *transitively*, and only within one worktree: `adopt_claim`
    refuses unless this worktree's own local state already shows the
    checkpoint `IN_PROGRESS`, and `request_plan_amendment` refuses on any
    checkpoint `IN_PROGRESS`; `take_over_claim` requires a pre-existing
    claim, which the amendment also refuses on. Neither argument is
    checked here, and under `XMODEL-R9-B1` neither holds across
    worktrees either."""
    record = _build_claim_record(repo_root, work_item_id, checkpoint_id, now)
    with state_lock(repo_root):
        state = _load_json(repo_root / DEFAULT_STATE_PATH) or {}
        work_item = state.get("work_items", {}).get(work_item_id)
        if work_item is not None:
            phase = work_item.get("phase")
            if phase not in CHECKPOINT_START_LEGAL_PHASES:
                raise IllegalCheckpointStartPhaseError(
                    f"{work_item_id!r} is at phase {phase!r} -- a checkpoint claim can only be "
                    f"published while phase is in {sorted(CHECKPOINT_START_LEGAL_PHASES)} "
                    f"(checked under WORKFLOW_STATE.lock immediately before publication, "
                    f"XMODEL-R8-B1, so a claim can never be published in the window between an "
                    f"amendment's authoritative quiescence read and its AMENDING_PLAN commit -- "
                    f"within one worktree root; see XMODEL-R9-B1 and "
                    f"docs/defects/v2.4.0-002-amendment-claim-race-crosses-worktree-boundary.md "
                    f"for the cross-worktree residual this check does not close)"
                )
        return _claim_or_refuse(repo_root, work_item_id, record)


def release_checkpoint(repo_root: Path, work_item_id: str, checkpoint_id: str, *,
                       owner_token: str, now: str = "release") -> None:
    """Release this session's own claim -- intended to be called at step
    1f **after** the checkpoint commit exists.

    A **compare-and-delete**, not a read-then-unlink (`GPT-R81-001`): the
    token the caller holds is asserted *inside* the mutation guard and
    the unlink happens in that same window, so a takeover interleaved
    between the read and the delete can no longer let a displaced owner
    remove the replacement owner's claim."""
    existing = resolve_claim(repo_root, work_item_id)
    if existing is None:
        return
    if not claim_is_this_worktree(repo_root, existing):
        raise CheckpointOwnedByOtherWorktreeError(
            f"refusing to release {work_item_id!r}'s claim held by "
            f"{existing.get('worktree_root')!r} from a different worktree")
    try:
        with owner_mutation(repo_root, work_item_id, owner_token,
                            checkpoint_id=checkpoint_id, step="1f-release",
                            step_class=ORDINARY, now=now):
            claim_path(repo_root, work_item_id).unlink(missing_ok=True)
    except OSError as exc:
        raise CheckpointOwnershipUnavailableError(
            f"cannot release {work_item_id!r}'s claim ({exc}) -- the checkpoint's own "
            f"completion is unaffected; the claim is released on the next invocation from "
            f"this worktree once the completion is durable"
        ) from exc


# ---------------------------------------------------------------------------
# Adoption -- "Reconciling the two authorities" (revisions 63-72). The
# one-time migration path for a checkpoint interrupted before this
# mechanism existed, including the real S-CP3 -- and the reason it can be
# protected without recreating it. Authored fresh against the approved
# revision-80 text; the unwired dry-run prototype's revision-68/69 draft
# (checkpoint_ownership.py) checked origination against
# `committed_checkpoint_status` alone, which the plan's own revision-69
# correction superseded before this slice was written -- this function
# calls `checkpoint_origination_provable` instead, never the superseded
# check.
# ---------------------------------------------------------------------------


def adopt_claim(repo_root: Path, work_item_id: str, checkpoint_id: str, *, now: str,
                state_rel_path: str | None = None) -> dict:
    """Publish a claim for a checkpoint this worktree already holds
    `IN_PROGRESS` but never claimed. Guarded so it can only ever run in
    the originating worktree:

    1. this worktree's own local `WORKFLOW_STATE.json` -- the working
       tree, never `HEAD` -- must actually record `checkpoint_id`
       `IN_PROGRESS` for `work_item_id`;
    2. `verify_dirty_resume_safety` must pass -- **first**, so the
       classes a foreign worktree sees here are unchanged;
    3. `checkpoint_origination_provable` must admit: that same
       `IN_PROGRESS` must be absent from the origination reference, and
       that read must be decidable;
    4. the same origination test is **re-evaluated at publication**,
       under `guard_mutation_lock`, not only at evidence time -- no
       observable gap separates the final check from the write;
    5. `_claim_or_refuse`'s own foreign-claim/state-mismatch refusals
       apply unchanged -- no foreign claim may exist.

    Idempotent: an existing self-claim for this exact checkpoint is
    returned unchanged (`_claim_or_refuse`'s own idempotence, unmodified
    here). Writes **no** authoritative state -- not
    `WORKFLOW_STATE.json`, not `WORKTREE_IDENTITY.json`, not the working
    tree -- and never invents an ownership fact where none exists. Runs
    automatically inside the future step-1c resume wiring, and is
    separately invocable as an explicit setup operation for an
    interrupted checkpoint that must be protected *without* being
    resumed -- exactly `S14`'s need."""
    state_rel_path = state_rel_path or DEFAULT_STATE_PATH.as_posix()
    state = _load_json(repo_root / Path(state_rel_path)) or {}
    work_items = state.get("work_items")
    work_item = work_items.get(work_item_id) if isinstance(work_items, dict) else None
    status = None
    if isinstance(work_item, dict):
        checkpoints = work_item.get("checkpoints")
        if isinstance(checkpoints, dict):
            entry = checkpoints.get(checkpoint_id)
            if isinstance(entry, dict):
                status = entry.get("status")
    if status != "IN_PROGRESS":
        raise CheckpointNotInProgressLocallyError(
            f"{work_item_id!r} checkpoint {checkpoint_id!r} is not IN_PROGRESS in this "
            f"worktree's own {state_rel_path} (status: {status!r}) -- there is nothing to "
            f"adopt; adoption never invents an ownership fact where none exists"
        )

    verify_dirty_resume_safety(repo_root, work_item_id)
    checkpoint_origination_provable(repo_root, work_item_id, checkpoint_id, state_rel_path=state_rel_path)

    record = _build_claim_record(repo_root, work_item_id, checkpoint_id, now, adopted=True)
    with guard_mutation_lock(repo_root, work_item_id):
        checkpoint_origination_provable(repo_root, work_item_id, checkpoint_id, state_rel_path=state_rel_path)
        return _claim_or_refuse(repo_root, work_item_id, record)


def committed_checkpoint_status(repo_root: Path, work_item_id: str, checkpoint_id: str,
                                *, state_rel_path: str | None = None) -> str | None:
    """The checkpoint's status in the `WORKFLOW_STATE.json` **committed at
    `HEAD`** -- never the working tree's. The difference between "the
    checkpoint is done" and "somebody typed that it is done"."""
    state_rel_path = state_rel_path or DEFAULT_STATE_PATH.as_posix()
    try:
        blob = _run(["git", "show", f"HEAD:{state_rel_path}"], cwd=repo_root)
    except subprocess.CalledProcessError:
        return None
    try:
        state = json.loads(blob)
    except json.JSONDecodeError:
        return None
    item = state.get("work_items", {}).get(work_item_id)
    if not isinstance(item, dict):
        return None
    return item.get("checkpoints", {}).get(checkpoint_id, {}).get("status")


# ---------------------------------------------------------------------------
# Fencing: the mutation/handoff guard (`GPT-R81-001`)
# ---------------------------------------------------------------------------


def read_guard(repo_root: Path, work_item_id: str) -> dict | None:
    """The guard body, or `None` if the guard is not held. Fails closed
    on a torn or symlinked guard exactly as the claim does -- an
    undecidable guard is never read as an absent one."""
    path = guard_path(repo_root, work_item_id)
    raw = _read_claim_bytes(path)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointOwnershipUnavailableError(
            f"{path} exists but could not be read as JSON ({exc}) -- refusing to mutate "
            f"checkpoint state behind an undecidable mutation guard") from exc
    if not isinstance(data, dict) or not isinstance(data.get("lease_id"), str):
        raise CheckpointOwnershipUnavailableError(f"{path} is not a well-formed mutation guard")
    return data


def _publish_guard(repo_root: Path, work_item_id: str, body: dict) -> None:
    _publish_claim_exclusive(guard_path(repo_root, work_item_id), body)


def _current_owner_token(repo_root: Path, work_item_id: str) -> tuple[str | None, bool]:
    """`(current owner_token, epoch_is_decidable)`. `None` with `True`
    means there is genuinely no claim, so **any** guard is superseded. An
    undecidable claim returns `False`, and a guard whose epoch cannot be
    judged is then never reclaimed -- the fail-closed direction."""
    try:
        claim = resolve_claim(repo_root, work_item_id)
    except CheckpointOwnershipUnavailableError:
        return None, False
    return (claim.get("owner_token") if claim is not None else None), True


def acquire_guard(repo_root: Path, work_item_id: str, *, holder_owner_token: str | None,
                  checkpoint_id: str | None, step: str, step_class: str, now: str,
                  role: str = "owner", authorized_lease_id: str | None = None) -> dict:
    """One acquisition attempt. **No retry loop, no timeout, no wall
    clock** -- every branch decides from durable data alone.

    - **superseded epoch** (`holder_owner_token` is not the claim's
      current token): the guard was provably left by a session whose
      ownership has already been rotated away, since only a rotation
      changes that token and a rotation only ever happens while holding
      this same guard. Reclaimed with no authorization, then acquisition
      is retried exactly **once**. Deliberately not narrowed to a
      worktree, so no post-takeover recovery is stranded.
    - **`role="owner"`, same token *and* same worktree**: the caller's
      own leftover guard from an interrupted earlier step. Reclaimed and
      retried once; the window's first act is `assert_claim_owner`, so a
      stale belief about ownership is caught immediately regardless.
      `holder_worktree_git_dir` is a required conjunct (`OPUS-R82-002`):
      the token is world-readable coordination data any worktree can
      read out of the claim, so token equality alone would let a
      *foreign* worktree reclaim a live `"destructive"` guard.
    - **`role="takeover"`, current epoch**: `"destructive"` refuses
      unconditionally. `"ordinary"` releases only when the observed
      `lease_id` is exactly the one the authorization quoted; a
      *different* `lease_id` proves the owner released and re-acquired,
      i.e. is demonstrably live, and refuses.
    - **`role="recovery"`, current epoch** (`OPUS-R82-001`): the one path
      that may reclaim a `"destructive"` guard from another worktree,
      reachable only from `recover_abandoned_destructive_guard`, which
      has already established the holder worktree is no longer
      registered and bound the authorization to both durable
      observations.
    - anything else refuses, naming the held guard."""
    body = {
        "lease_id": secrets.token_hex(16),
        "holder_owner_token": holder_owner_token,
        "holder_worktree_git_dir": _worktree_git_dir(repo_root),
        "work_item_id": work_item_id,
        "checkpoint_id": checkpoint_id,
        "step": step,
        "step_class": step_class,
        "acquired_at": now,
    }
    if step_class not in GUARD_STEP_CLASSES:
        raise CheckpointOwnershipUnavailableError(f"unknown guard step_class {step_class!r}")
    # `OPUS-R83-001`: the reclaim-and-republish sequence below removes a
    # guard and publishes another, and those two steps must be
    # indivisible with respect to the `lease_id` this session compared
    # against. Everything from the first observation to the publication
    # runs inside the serialization.
    with guard_mutation_lock(repo_root, work_item_id):
        return _acquire_guard_locked(repo_root, work_item_id, body=body,
                                     holder_owner_token=holder_owner_token, role=role,
                                     authorized_lease_id=authorized_lease_id)


def _acquire_guard_locked(repo_root: Path, work_item_id: str, *, body: dict,
                          holder_owner_token: str | None, role: str,
                          authorized_lease_id: str | None) -> dict:
    """`acquire_guard`'s decision and publication, run under
    `guard_mutation_lock`. Split out so the lock is taken exactly once by
    the operation rather than re-entered by each helper (`OPUS-R83-001`)."""
    try:
        _publish_guard(repo_root, work_item_id, body)
        return body
    except FileExistsError:
        pass

    held = read_guard(repo_root, work_item_id)
    if held is None:                       # released between the two operations
        _publish_guard(repo_root, work_item_id, body)
        return body

    current, decidable = _current_owner_token(repo_root, work_item_id)
    reclaim = False
    if decidable and (current is None or held.get("holder_owner_token") != current):
        # superseded epoch -- `current is None` (no claim at all) is
        # unconditional: a guard with no backing claim has no possible
        # legitimate holder regardless of what token it names, including
        # the degenerate case where it names none at all (`item 372(a)`,
        # a guard "planted by a session holding no current token" must
        # never become a permanent false lock rather than superseding by
        # construction only when its token happens to differ from
        # `current`, which is vacuously false when both are `None`).
        reclaim = True
    elif (role == "owner" and held.get("holder_owner_token") == holder_owner_token
          and held.get("holder_worktree_git_dir") == _worktree_git_dir(repo_root)):
        reclaim = True                     # this worktree's own leftover guard
    elif role == "owner" and held.get("holder_owner_token") == holder_owner_token:
        # `OPUS-R82-002`: same token, different worktree. The token
        # proves the epoch, never the holder.
        raise CheckpointOwnershipUnavailableError(
            f"{work_item_id!r}'s mutation guard is held by worktree "
            f"{held.get('holder_worktree_git_dir')!r} (lease {held.get('lease_id')!r}, step "
            f"{held.get('step')!r}, class {held.get('step_class')!r}); this worktree is "
            f"{_worktree_git_dir(repo_root)!r} -- presenting the claim's token is not being the "
            f"holder, and no worktree breaks another worktree's window here")
    elif role == "takeover":
        if held.get("step_class") == DESTRUCTIVE:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s owner is inside the destructive step "
                f"{held.get('step')!r} (lease {held.get('lease_id')!r}) -- no takeover "
                f"authorization breaks that window; resume in the owning session, or, if that "
                f"worktree is gone, use the abandoned-guard recovery")
        if authorized_lease_id is not None and authorized_lease_id == held.get("lease_id"):
            reclaim = True                 # the authorized break, exactly as quoted
        else:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s mutation guard is held (lease {held.get('lease_id')!r}, step "
                f"{held.get('step')!r}) and does not match the authorized lease "
                f"{authorized_lease_id!r} -- the owner is live; refusing to break it")
    elif role == "recovery":
        if authorized_lease_id is not None and authorized_lease_id == held.get("lease_id"):
            reclaim = True
        else:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s mutation guard (lease {held.get('lease_id')!r}) is no longer "
                f"the abandoned one the recovery authorized ({authorized_lease_id!r}) -- "
                f"refusing, having mutated nothing")
    if not reclaim:
        raise CheckpointOwnershipUnavailableError(
            f"{work_item_id!r}'s mutation guard is held by lease {held.get('lease_id')!r} "
            f"(step {held.get('step')!r}, class {held.get('step_class')!r}) -- refusing to mutate "
            f"checkpoint state concurrently with its owner")

    _release_guard_path_locked(repo_root, work_item_id, held.get("lease_id"))
    try:
        _publish_guard(repo_root, work_item_id, body)
    except FileExistsError as exc:
        raise CheckpointOwnershipUnavailableError(
            f"{work_item_id!r}'s mutation guard was re-acquired by another session during "
            f"reclamation -- refusing, having mutated nothing") from exc
    return body


def _release_guard_path_locked(repo_root: Path, work_item_id: str, lease_id: str) -> None:
    """Compare-and-delete on `lease_id`: never remove a guard this
    session does not hold. **Caller must hold `guard_mutation_lock`**
    (`OPUS-R83-001`) -- a bare read-then-`unlink` names the *pathname*
    rather than the identity the comparison just established, so a guard
    published between the two statements is removed instead, including a
    live `"destructive"` one belonging to the work item's current owner
    in another worktree.

    An undecidable guard is left in place rather than deleted -- a
    release path that silently drops a record it could not read would be
    the one place fail-closed quietly became fail-open; the documented
    recovery is `clear_malformed_guard`.

    **An absent `lease_id` is a refusal, never a wildcard** (`OPUS-R84`
    non-blocking observation 1, extended per `OPUS-R85` non-blocking
    observation 1 to cover `""` as well as `None`): "every removal of a
    guard, on every path, names the `lease_id` it just observed"."""
    if not isinstance(lease_id, str) or not lease_id:
        raise CheckpointOwnershipUnavailableError(
            f"releasing {work_item_id!r}'s mutation guard requires the exact lease_id being "
            f"released, got {lease_id!r} -- an absent lease id is a refusal, never a wildcard "
            f"that removes whatever guard is present")
    path = guard_path(repo_root, work_item_id)
    try:
        held = read_guard(repo_root, work_item_id)
    except CheckpointOwnershipUnavailableError:
        return
    if held is None:
        return
    if held.get("lease_id") != lease_id:
        return
    path.unlink(missing_ok=True)


def _release_guard_path(repo_root: Path, work_item_id: str, lease_id: str) -> None:
    with guard_mutation_lock(repo_root, work_item_id):
        _release_guard_path_locked(repo_root, work_item_id, lease_id)


def guard_clearance_authorization_literal(evidence: dict) -> str:
    return (f"clear malformed checkpoint guard {evidence.get('work_item_id')} "
            f"observation {evidence.get('guard_observation_id')}")


def _guard_observation_id(path: Path) -> str:
    """`observation_id` over a guard file's raw bytes, extended to the
    unreadable case the same way `observe_claim` extends it for claims
    (`OPUS-R83-002`, `item 372(b)`/`(c)`): a symlinked or otherwise
    unreadable guard gets a domain-tagged observation over what `lstat`
    alone can say, rather than letting `_read_claim_bytes`'s raise
    propagate uncontrolled out of `clear_malformed_guard` -- the one
    caller here exists specifically to act on an undecidable guard, so
    it must be able to observe one, not only a well-formed one."""
    try:
        raw = _read_claim_bytes(path)
    except CheckpointOwnershipUnavailableError:
        descriptor = describe_unreadable_record(path)
        if descriptor is None:
            raise
        return unreadable_observation_id(descriptor)
    return observation_id(raw)


def clear_malformed_guard(repo_root: Path, work_item_id: str, *, user_authorization: str | None) -> None:
    """The defined recovery for a guard this implementation cannot have
    written -- torn, wrong shape, symlinked, or otherwise undecidable.
    Publication is a same-directory temp file plus `os.link`, so a
    partially written guard is not producible here; a guard that *is*
    undecidable was corrupted by something else, and it fails closed for
    every session including the legitimate owner. Left there, that is a
    permanent lockout.

    The escape is explicit, user-authorized and observation-bound, never
    automatic and never time-based: the literal must quote the exact
    `guard_observation_id` the evidence reported, and the bytes must
    still hash to it at the moment of removal. A guard kind this design
    never removes on its own -- a directory, matching `resolve_claim`'s
    own `REPLACEABLE_UNREADABLE_KINDS` philosophy -- is refused rather
    than unlinked."""
    path = guard_path(repo_root, work_item_id)
    evidence = {"work_item_id": work_item_id, "guard_observation_id": _guard_observation_id(path)}
    if user_authorization != guard_clearance_authorization_literal(evidence):
        raise CheckpointClaimTakeoverRefusedError(
            f"clearing a malformed mutation guard requires the literal authorization "
            f"{guard_clearance_authorization_literal(evidence)!r} -- refusing to remove a "
            f"guard on an inference")
    with guard_mutation_lock(repo_root, work_item_id):
        current_oid = _guard_observation_id(path)
        if current_oid != evidence["guard_observation_id"]:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s mutation guard changed between the evidence the user "
                f"authorized ({evidence['guard_observation_id']}) and this clearance "
                f"({current_oid}) -- refusing, having removed nothing")
        try:
            read_guard(repo_root, work_item_id)
        except CheckpointOwnershipUnavailableError:
            descriptor = describe_unreadable_record(path)
            if descriptor is not None and descriptor.get("kind") not in REPLACEABLE_UNREADABLE_KINDS:
                raise CheckpointClaimTakeoverRefusedError(
                    f"{work_item_id!r}'s mutation guard at {path} is a "
                    f"{descriptor.get('kind')!r} -- this operation never removes that kind "
                    f"of object; clear it by hand once that is independently established"
                ) from None
            path.unlink(missing_ok=True)
            return
    raise CheckpointClaimTakeoverRefusedError(
        f"{work_item_id!r}'s mutation guard is well-formed -- this operation only ever "
        f"removes an undecidable record; use the ordinary takeover contract instead")


def release_guard(repo_root: Path, work_item_id: str, lease: dict) -> None:
    _release_guard_path(repo_root, work_item_id, lease.get("lease_id"))


def assert_claim_owner(repo_root: Path, work_item_id: str, owner_token: str) -> dict:
    """Re-read the claim at its final pathname and require its
    `owner_token` to equal exactly the token this session holds. On any
    failure -- absent claim, undecidable claim, rotated token -- stop
    immediately and mutate nothing.

    This is the mechanism by which an owner whose claim was taken over
    refuses instead of continuing: after a takeover the claim carries a
    rotated token, so the displaced owner's very next assertion fails.
    Meaningful only **inside** the guard; called outside it, it is a
    check-before-use, which `GPT-R81-001` reproduced as insufficient."""
    claim = resolve_claim(repo_root, work_item_id)
    if claim is None:
        raise CheckpointOwnershipStateMismatchError(
            f"this session holds {work_item_id!r} owner token {owner_token!r} but no claim "
            f"record exists -- refusing to mutate")
    if claim.get("owner_token") != owner_token:
        if not claim_is_this_worktree(repo_root, claim):
            raise CheckpointOwnedByOtherWorktreeError(
                f"{work_item_id!r}'s claim now belongs to worktree "
                f"{claim.get('worktree_root')!r} with owner token {claim.get('owner_token')!r}; "
                f"this session holds {owner_token!r} and is therefore no longer the owner -- "
                f"refusing to write authoritative state or commit")
        raise CheckpointOwnershipStateMismatchError(
            f"{work_item_id!r}'s claim carries owner token {claim.get('owner_token')!r}, not the "
            f"{owner_token!r} this session holds -- ownership was rotated; refusing to mutate")
    return claim


@contextlib.contextmanager
def owner_mutation(repo_root: Path, work_item_id: str, owner_token: str, *,
                   checkpoint_id: str | None, step: str, step_class: str, now: str):
    """The one fixed window shape every ownership-bearing mutation runs
    inside:

        acquire the guard -> assert_claim_owner(T) *under* the guard ->
        perform the mutation -> release the guard

    Every pause, stall or crash between the assertion and the mutation is
    therefore inside a window a takeover cannot enter. Windows are
    strictly non-nested: a session holds at most one guard at a time."""
    lease = acquire_guard(repo_root, work_item_id, holder_owner_token=owner_token,
                          checkpoint_id=checkpoint_id, step=step, step_class=step_class,
                          now=now, role="owner")
    try:
        yield assert_claim_owner(repo_root, work_item_id, owner_token)
    finally:
        release_guard(repo_root, work_item_id, lease)


# ---------------------------------------------------------------------------
# Explicit takeover -- never automatic, always evidence-first
# ---------------------------------------------------------------------------


def registered_worktrees(repo_root: Path) -> list[dict]:
    """`git worktree list --porcelain`, parsed -- the shared registry
    every linked worktree can read, used to tell a deleted holder from a
    live one."""
    out = _run(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
    entries: list[dict] = []
    current: dict = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def local_identity_observation(repo_root: Path, *, path: Path = WORKTREE_IDENTITY_PATH) -> dict:
    """What the **taking** worktree's own identity document is, as a
    durable, re-verifiable observation (`OPUS-R86-003`). A rotating
    operation must establish this worktree's own identity record, and
    that write owes validate-before-mutate -- so when the document is
    undecidable the operation refuses unless the repair is separately
    authorized. Observed here, never concluded: the id is the sha256 of
    the exact bytes the human is shown, so the authorization it derives
    is bound to that document and to no other, and is non-replayable
    once the repair changes those bytes."""
    full_path = repo_root / path
    try:
        raw = _read_claim_bytes(full_path)
    except CheckpointOwnershipUnavailableError:
        descriptor = describe_unreadable_record(full_path)
        return {"state": "undecidable", "path": str(full_path),
                "identity_observation_id": (unreadable_observation_id(descriptor)
                                            if descriptor is not None else ABSENT_OBSERVATION),
                "unreadable": descriptor, "error": "the document is not a readable regular file"}
    if raw is None:
        return {"state": "absent", "path": str(full_path), "identity_observation_id": ABSENT_OBSERVATION}
    oid = observation_id(raw)
    try:
        data = json.loads(raw)
        validate_worktree_identity(data)
    except Exception as exc:  # noqa: BLE001 -- every decidability failure is one state
        return {"state": "undecidable", "path": str(full_path), "identity_observation_id": oid,
                "error": f"{type(exc).__name__}: {exc}"}
    return {"state": "valid", "path": str(full_path), "identity_observation_id": oid}


def repair_worktree_identity(repo_root: Path, work_item_id: str, *, now: str,
                             path: Path = WORKTREE_IDENTITY_PATH) -> dict:
    """The authorized repair-by-overwrite of this worktree's **own**
    identity document (`OPUS-R86-003`). Deliberately a separate entry
    point from `write_worktree_identity`, which must keep refusing on a
    corrupt existing document rather than silently replacing it
    (`OPUS-R86-004`): this one discards the undecidable document and
    builds a fresh one, and it is reachable only from a rotating
    operation whose authorization literal carried the repair component
    bound to that exact document's observation. Publishes through the
    same lock and the same single `os.replace` as every other write of
    this file."""
    full_path = repo_root / path
    with identity_document_lock(repo_root):
        repo_root_id, git_common_dir, worktree_root = _git_identity(repo_root)
        document = {
            "repo_root": repo_root_id,
            "git_common_dir": git_common_dir,
            "worktree_root": worktree_root,
            "expected_dirty_paths_by_work_item": {work_item_id: _hash_dirty_paths(repo_root)},
            "generated_at": now,
        }
        validate_worktree_identity(document)
        _publish_worktree_identity(full_path, document)
    return document


def _establish_or_repair_identity(repo_root: Path, work_item_id: str, *, now: str,
                                  evidence: dict, operation: str) -> None:
    """The identity half of both rotating operations, run **before** the
    rotation publishes (`OPUS-R86-003`). A decidable document is
    established the ordinary way. An undecidable one is repaired only if
    the authorization the caller already validated carried the repair
    component, and only if the document is still byte-identical to the
    one that component named."""
    observed = evidence.get("local_identity") or local_identity_observation(repo_root)
    if observed.get("state") != "undecidable":
        write_worktree_identity(repo_root, work_item_id, now=now)
        return
    fresh = local_identity_observation(repo_root)
    if fresh.get("identity_observation_id") != observed.get("identity_observation_id"):
        raise CheckpointClaimTakeoverRefusedError(
            f"this worktree's own identity document changed between the evidence the user "
            f"authorized ({observed.get('identity_observation_id')}) and this {operation} "
            f"({fresh.get('identity_observation_id')}) -- refusing, having mutated nothing; "
            f"present fresh evidence and obtain a fresh authorization")
    repair_worktree_identity(repo_root, work_item_id, now=now)


def _identity_repair_clause(evidence: dict) -> str:
    identity = evidence.get("local_identity") or {}
    if identity.get("state") != "undecidable":
        return ""
    return f" repairing identity {identity.get('identity_observation_id')}"


def takeover_evidence(repo_root: Path, work_item_id: str) -> dict:
    """Everything a human needs to decide a takeover, gathered without
    changing anything. Deliberately reports rather than concludes."""
    path = claim_path(repo_root, work_item_id)
    oid, claim, error, unreadable = observe_claim(repo_root, work_item_id)
    gpath = guard_path(repo_root, work_item_id)
    try:
        guard_oid = observation_id(_read_claim_bytes(gpath))
    except CheckpointOwnershipUnavailableError:
        guard_descriptor = describe_unreadable_record(gpath)
        guard_oid = (unreadable_observation_id(guard_descriptor)
                     if guard_descriptor is not None else ABSENT_OBSERVATION)
    evidence = {"claim_path": str(path), "readable": error is None, "claim": None,
                "holder_path_exists": None, "holder_registered": None,
                "holder_current_path": None,
                "claim_observation_id": oid,
                "claim_unreadable": unreadable,
                "claim_replaceable": unreadable is None or (
                    unreadable.get("kind") in REPLACEABLE_UNREADABLE_KINDS),
                "observed_checkpoint_id": claim.get("checkpoint_id") if claim else None,
                "observed_owner_token": claim.get("owner_token") if claim else None,
                "work_item_id": work_item_id,
                "guard": None,
                "guard_observation_id": guard_oid,
                "guard_holder_registered": None,
                "guard_holder_current_path": None,
                "guard_error": None,
                "local_identity": local_identity_observation(repo_root)}
    try:
        evidence["guard"] = read_guard(repo_root, work_item_id)
    except CheckpointOwnershipUnavailableError as exc:
        evidence["guard_error"] = str(exc)
    registered = registered_worktrees(repo_root)

    def _registered_match(recorded_root, recorded_git_dir):
        for entry in registered:
            wt_path = entry.get("worktree")
            if wt_path is None:
                continue
            try:
                admin = _worktree_git_dir(Path(wt_path)) if Path(wt_path).exists() else None
            except subprocess.CalledProcessError:
                admin = None
            if wt_path == recorded_root or (admin and recorded_git_dir and admin == recorded_git_dir):
                return True, wt_path
        return False, None

    guard = evidence["guard"]
    if guard is not None:
        guard_registered, guard_path_now = _registered_match(None, guard.get("holder_worktree_git_dir"))
        evidence["guard_holder_registered"] = guard_registered
        evidence["guard_holder_current_path"] = guard_path_now
    if error is not None:
        evidence["error"] = error
        return evidence
    if claim is None:
        evidence["claim"] = None
        return evidence
    evidence["claim"] = claim
    holder_root = Path(claim["worktree_root"])
    evidence["holder_path_exists"] = holder_root.exists()
    matched, wt_path = _registered_match(claim["worktree_root"], claim["worktree_git_dir"])
    evidence["holder_registered"] = matched
    if matched:
        evidence["holder_current_path"] = wt_path
    return evidence


def takeover_authorization_literal(work_item_id: str, evidence: dict, checkpoint_id: str) -> str:
    """The exact literal a user must produce, derived **from the
    evidence they were shown** (`GPT-R81-002`). Names the observed
    record's identity and the checkpoint that record holds, so it cannot
    be written from memory, cannot be replayed against a later record,
    and cannot displace a holder or checkpoint nobody reviewed. Grows a
    repair component when this worktree's own identity document is
    undecidable (`OPUS-R86-003`)."""
    displaced = evidence.get("observed_checkpoint_id") or "none"
    literal = (f"take over {work_item_id} claim {evidence.get('claim_observation_id')} "
               f"holding {displaced} as {checkpoint_id}")
    return literal + _identity_repair_clause(evidence)


def guard_release_authorization_literal(guard: dict) -> str:
    return (f"release checkpoint guard {guard.get('lease_id')} step {guard.get('step')} "
            f"class {guard.get('step_class')}")


def take_over_claim(repo_root: Path, work_item_id: str, checkpoint_id: str, *, now: str,
                    user_authorization: str | None, evidence: dict | None = None,
                    guard_release_authorization: str | None = None) -> dict:
    """The ordinary way a claim this worktree does not own is removed --
    and the only one that ever applies while the holder worktree is
    still registered. (`recover_abandoned_destructive_guard` is the
    single other route, for a destructive window abandoned by a worktree
    that no longer exists; `clear_malformed_guard` removes an
    undecidable guard, never a claim.)

    Never triggered by a timeout, a heartbeat, an age threshold, or an
    inference that the holder "looks gone" -- every one of those is an
    assumption that can discard a live claim, and this design does not
    make any of them.

    1. **Observe** -- `takeover_evidence`, whose `claim_observation_id`
       is the sha256 of the exact record bytes the human is shown.
    2. **Authorize** -- the literal must be exactly
       `takeover_authorization_literal(...)` for *that* observation. When
       a guard was observed, a separate guard-release authorization
       quoting its `lease_id`/`step`/`step_class` is required too, and a
       `"destructive"` guard is refused outright.
    3. **Acquire the same mutation guard** every owner mutation acquires.
    4. **Re-verify under the guard** -- re-read the raw bytes and require
       the observation id to be unchanged. Anything else is stale
       evidence: refuse, having mutated nothing.
    5. **Rotate** by atomic replace, minting a fresh `owner_token`,
       incrementing `takeover_count`, and recording the displaced record
       in `taken_over_from`."""
    if evidence is None:
        evidence = takeover_evidence(repo_root, work_item_id)
    expected = takeover_authorization_literal(work_item_id, evidence, checkpoint_id)
    if user_authorization != expected:
        raise CheckpointClaimTakeoverRefusedError(
            f"explicit takeover requires the literal authorization {expected!r} derived from the "
            f"evidence just presented -- refusing to remove a claim on an inference, on a "
            f"remembered literal, or on evidence the user did not review"
        )
    observed_oid = evidence.get("claim_observation_id")
    guard = evidence.get("guard")
    if guard is not None:
        if guard.get("step_class") == DESTRUCTIVE:
            raise CheckpointClaimTakeoverRefusedError(
                f"the holder is inside destructive step {guard.get('step')!r} -- no takeover "
                f"authorization breaks that window; resume in the owning session, or, if that "
                f"worktree is gone, use the abandoned-guard recovery")
        if guard_release_authorization != guard_release_authorization_literal(guard):
            raise CheckpointClaimTakeoverRefusedError(
                f"a mutation guard was observed; takeover additionally requires the literal "
                f"{guard_release_authorization_literal(guard)!r}")

    path = claim_path(repo_root, work_item_id)
    if not evidence.get("claim_replaceable", True):
        descriptor = evidence.get("claim_unreadable") or {}
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s claim path is a {descriptor.get('kind')!r}, which no documented "
            f"operation replaces: a rotation publishes by `os.rename` onto that name, and this "
            f"design never removes a directory it did not create. Remove {str(path)!r} yourself "
            f"once you have confirmed it holds nothing of yours, then re-run the takeover")
    if evidence.get("claim_unreadable") is None:
        _assert_not_symlink(path, "claim record")

    lease = acquire_guard(repo_root, work_item_id,
                          holder_owner_token=evidence.get("observed_owner_token"),
                          checkpoint_id=checkpoint_id, step="takeover", step_class=ORDINARY,
                          now=now, role="takeover",
                          authorized_lease_id=guard.get("lease_id") if guard else None)
    try:
        current_oid, previous, _error, _unreadable = observe_claim(repo_root, work_item_id)
        if current_oid != observed_oid:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s claim changed between the evidence the user authorized "
                f"({observed_oid}) and this takeover ({current_oid}) -- refusing, having mutated "
                f"nothing; present fresh evidence and obtain a fresh authorization")
        taken_over_from = None
        if previous is not None:
            taken_over_from = {
                "worktree_root": previous.get("worktree_root"),
                "worktree_git_dir": previous.get("worktree_git_dir"),
                "checkpoint_id": previous.get("checkpoint_id"),
                "claimed_at": previous.get("claimed_at"),
                "owner_token": previous.get("owner_token"),
                "claim_observation_id": current_oid,
                "holder_registered": evidence.get("holder_registered"),
                "authorized_at": now,
            }
        elif current_oid != ABSENT_OBSERVATION:
            taken_over_from = {"unreadable_record": True, "claim_observation_id": current_oid,
                               "authorized_at": now}
        record = _build_claim_record(
            repo_root, work_item_id, checkpoint_id, now, taken_over_from=taken_over_from,
            takeover_count=(previous or {}).get("takeover_count", 0) + 1,
            previous_owner_tokens=list((previous or {}).get("previous_owner_tokens", []))
            + ([previous["owner_token"]] if previous else []))
        _establish_or_repair_identity(repo_root, work_item_id, now=now,
                                      evidence=evidence, operation="takeover")
        _publish_claim_replacing(path, record,
                                 allow_unreadable_target=evidence.get("claim_unreadable") is not None)
        return record
    finally:
        release_guard(repo_root, work_item_id, lease)


def abandoned_guard_recovery_authorization_literal(work_item_id: str, evidence: dict) -> str:
    """Distinct from the takeover literal in every component, and bound
    to **both** durable observations plus the destructive step being
    abandoned, so it can neither be written from memory nor reused for
    an ordinary takeover (`OPUS-R82-001`)."""
    guard = evidence.get("guard") or {}
    return (f"recover abandoned destructive guard {work_item_id} step {guard.get('step')} "
            f"guard {evidence.get('guard_observation_id')} "
            f"claim {evidence.get('claim_observation_id')}"
            + _identity_repair_clause(evidence))


def recover_abandoned_destructive_guard(repo_root: Path, work_item_id: str, checkpoint_id: str, *,
                                        now: str, user_authorization: str | None,
                                        evidence: dict | None = None) -> dict:
    """The one escape from a `"destructive"` guard left behind by a
    holder worktree that no longer exists (`OPUS-R82-001`). Without it,
    `take_over_claim`'s unconditional destructive refusal and
    `clear_malformed_guard`'s well-formed refusal combine into a work
    item no documented operation can start, resume or hand over.

    Introduces **no** liveness inference: still no timeout, no
    heartbeat, no age threshold, no `claimed_at` comparison, no "the
    holder looks gone". The precondition is a durable, human-made fact
    instead -- the holder worktree is not in `git worktree list`, i.e.
    the operator (or the machine's loss) deregistered it. While it is
    still registered this refuses and names what to do instead, so a
    live destructive window is never broken.

    Mechanically a takeover whose guard reclamation is `role="recovery"`:
    once the claim rotates, the abandoned guard is superseded by
    construction, so the existing epoch rule -- not a second removal
    primitive -- finally clears it."""
    if evidence is None:
        evidence = takeover_evidence(repo_root, work_item_id)
    guard = evidence.get("guard")
    if evidence.get("guard_error") is not None or guard is None:
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r} has no well-formed mutation guard to recover "
            f"({evidence.get('guard_error') or 'no guard is held'}) -- an undecidable guard is "
            f"cleared by `clear_malformed_guard`, and a work item with no guard needs the "
            f"ordinary takeover, not this operation")
    if guard.get("step_class") != DESTRUCTIVE:
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s guard is {guard.get('step_class')!r}, not {DESTRUCTIVE!r} -- an "
            f"ordinary guard is released by the takeover's own guard-release authorization; this "
            f"operation exists only for the window that authorization can never break")
    claim = evidence.get("claim")
    claim_undecidable = evidence.get("error") is not None
    if claim is None and not claim_undecidable:
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r} has a guard but no claim at all -- a guard with no claim is "
            f"superseded by construction and is already reclaimable with no authorization; "
            f"this operation is only for an abandoned window of the current epoch")
    if claim_undecidable and not evidence.get("claim_replaceable", True):
        descriptor = evidence.get("claim_unreadable") or {}
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s claim path is a {descriptor.get('kind')!r}, which no documented "
            f"operation replaces: a rotation publishes by `os.rename` onto that name, and this "
            f"design never removes a directory it did not create. Remove "
            f"{evidence.get('claim_path')!r} yourself once you have confirmed it holds nothing "
            f"of yours, then re-run this recovery")
    if claim_undecidable:
        if guard.get("checkpoint_id") != checkpoint_id:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s claim is undecidable, so the abandoned guard's own "
                f"checkpoint {guard.get('checkpoint_id')!r} is the only durable one -- refusing "
                f"to recover it as {checkpoint_id!r}")
        if evidence.get("guard_holder_registered") is not False:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s guard is held by worktree "
                f"{guard.get('holder_worktree_git_dir')!r}, which is still registered "
                f"(currently {evidence.get('guard_holder_current_path')!r}) -- a registered "
                f"holder's destructive window is never broken from outside, and the claim being "
                f"undecidable does not change that. Resume in that worktree, move it back to "
                f"its recorded path if it was relocated, or `git worktree remove` it if you know "
                f"it is dead and re-run this recovery")
    if claim is not None and guard.get("holder_owner_token") != claim.get("owner_token"):
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s guard belongs to a superseded epoch (guard token "
            f"{guard.get('holder_owner_token')!r}, claim token {claim.get('owner_token')!r}) -- "
            f"it is already reclaimable with no authorization; this operation is only for a "
            f"guard of the **current** epoch")
    if claim is not None and claim_is_this_worktree(repo_root, claim):
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s claim is held by this worktree -- an interrupted step of this "
            f"worktree's own epoch is reclaimed by simply re-entering it; nothing is abandoned "
            f"from here")
    if claim is not None and evidence.get("holder_registered") is not False:
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s holder is still a registered worktree of this repository "
            f"(recorded {claim.get('worktree_root')!r}, currently "
            f"{evidence.get('holder_current_path')!r}) -- a registered holder's destructive "
            f"window is never broken from outside. Resume in that worktree (a session there "
            f"reclaims its own interrupted guard), move it back to its recorded path if it was "
            f"relocated, or `git worktree remove` it if you know it is dead and re-run this "
            f"recovery")
    expected = abandoned_guard_recovery_authorization_literal(work_item_id, evidence)
    if user_authorization != expected:
        raise CheckpointClaimTakeoverRefusedError(
            f"recovering an abandoned destructive guard requires the literal authorization "
            f"{expected!r}, derived from the evidence just presented -- refusing to break a "
            f"destructive window on an inference or on a remembered literal")

    observed_oid = evidence.get("claim_observation_id")
    path = claim_path(repo_root, work_item_id)
    if not claim_undecidable:
        _assert_not_symlink(path, "claim record")

    fresh = takeover_evidence(repo_root, work_item_id)
    fresh_registered = (fresh.get("guard_holder_registered") if claim_undecidable
                        else fresh.get("holder_registered"))
    if fresh_registered is not False:
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s holder worktree was re-registered at "
            f"{fresh.get('guard_holder_current_path') or fresh.get('holder_current_path')!r} "
            f"after the evidence was taken -- refusing, having mutated nothing")
    if fresh.get("guard_observation_id") != evidence.get("guard_observation_id"):
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s mutation guard changed between the evidence the user authorized "
            f"({evidence.get('guard_observation_id')}) and this recovery "
            f"({fresh.get('guard_observation_id')}) -- refusing, having mutated nothing")
    if fresh.get("claim_observation_id") != observed_oid:
        raise CheckpointClaimTakeoverRefusedError(
            f"{work_item_id!r}'s claim changed between the evidence the user authorized "
            f"({observed_oid}) and this recovery ({fresh.get('claim_observation_id')}) -- "
            f"refusing, having mutated nothing; present fresh evidence and obtain a fresh "
            f"authorization")

    lease = acquire_guard(repo_root, work_item_id,
                          holder_owner_token=evidence.get("observed_owner_token"),
                          checkpoint_id=checkpoint_id, step="recover-abandoned-guard",
                          step_class=ORDINARY, now=now, role="recovery",
                          authorized_lease_id=guard.get("lease_id"))
    try:
        current_oid, previous, _error, current_unreadable = observe_claim(repo_root, work_item_id)
        if current_oid != observed_oid:
            raise CheckpointClaimTakeoverRefusedError(
                f"{work_item_id!r}'s claim changed between the evidence the user authorized "
                f"({observed_oid}) and this recovery ({current_oid}) -- refusing; the claim was "
                f"modified outside this contract, since no sanctioned rotation is possible "
                f"without the guard this operation holds")
        previous = previous or {}
        taken_over_from = {
            "worktree_root": previous.get("worktree_root"),
            "worktree_git_dir": previous.get("worktree_git_dir"),
            "checkpoint_id": previous.get("checkpoint_id"),
            "claimed_at": previous.get("claimed_at"),
            "owner_token": previous.get("owner_token"),
            "claim_observation_id": current_oid,
            "holder_registered": False,
            "authorized_at": now,
            "claim_was_undecidable": claim_undecidable,
            "claim_unreadable": current_unreadable,
            "epoch_chain_lost": claim_undecidable,
            "recovered_from_abandoned_guard": {
                "lease_id": guard.get("lease_id"),
                "step": guard.get("step"),
                "step_class": guard.get("step_class"),
                "guard_observation_id": evidence.get("guard_observation_id"),
                "holder_worktree_git_dir": guard.get("holder_worktree_git_dir"),
            },
        }
        displaced_tokens = list(previous.get("previous_owner_tokens", []))
        if previous.get("owner_token"):
            displaced_tokens.append(previous["owner_token"])
        elif guard.get("holder_owner_token"):
            displaced_tokens.append(guard["holder_owner_token"])
        record = _build_claim_record(
            repo_root, work_item_id, checkpoint_id, now, taken_over_from=taken_over_from,
            takeover_count=previous.get("takeover_count", 0) + 1,
            previous_owner_tokens=displaced_tokens)
        _establish_or_repair_identity(repo_root, work_item_id, now=now,
                                      evidence=evidence, operation="recovery")
        _publish_claim_replacing(path, record, allow_unreadable_target=claim_undecidable)
        return record
    finally:
        release_guard(repo_root, work_item_id, lease)


# ---------------------------------------------------------------------------
# WF8b: D-Checkpoint-Ownership -- "Where the check belongs, and the
# ordering" (docs/ai-workflow/WORKFLOW_V2_PLAN.md, revisions 63-80). This
# is `/milestone-implement`'s step 1c: the resolution step that
# reconciles `WORKFLOW_STATE.json` against the shared claim, composing
# every primitive the three prior WF8b sessions built (the origination
# reference, the claim/guard/takeover machinery, `adopt_claim`) into the
# outcome `step 1d`/`1f` act on.
#
# Authored fresh against the approved revision-80 text; the unwired
# dry-run prototype's own `resolve_ownership`/`_attach_ownership_evidence`
# (docs/ai-workflow/dry-run/wf8b-s14-repro/checkpoint_ownership.py) are
# reference/reproduction evidence only, and predate two corrections this
# implementation does not repeat: `_attach_ownership_evidence` returning
# immediately on an absent claim (corrected, revision 70, `OPUS-R87-003`)
# and the origination-specific evidence components revision 71
# (`OPUS-R88-005`) adds for `CheckpointOriginationUnprovableError`.
#
# Scope, deliberately bounded per this session's own direction: no
# `WFR-66` identity-query enforcement (a distinct implementation surface,
# `authorize_identity_reference_gap`), and not yet wired into
# `.claude/commands/milestone-implement.md`'s step 1d/1f orchestration of
# the guarded state write / guarded commit -- this function is the
# resolution primitive that wiring calls.
# ---------------------------------------------------------------------------

RESUME = "RESUME"
FRESH = "FRESH"
CONTINUE_CLAIM = "CONTINUE_CLAIM"
NO_CHECKPOINT = "NO_CHECKPOINT"
CHECKPOINT_OWNERSHIP_OUTCOMES = frozenset({RESUME, FRESH, CONTINUE_CLAIM, NO_CHECKPOINT})


def _ownership_escape_hint(repo_root: Path, work_item_id: str, claim: dict | None) -> str:
    """The escape named alongside every ownership-evidence attachment
    (`OPUS-R84-001`, generalized to an absent claim by `OPUS-R87-003`):
    what the operator does next, for every claim state -- absent,
    self-owned, or foreign."""
    if claim is None:
        return (
            f"no claim exists for {work_item_id!r} -- the escape for a locally IN_PROGRESS "
            f"checkpoint this worktree cannot prove it originated is the explicit takeover "
            f"(take_over_claim)"
        )
    if claim_is_this_worktree(repo_root, claim):
        return (
            f"{work_item_id!r} checkpoint {claim.get('checkpoint_id')!r} is claimed by this "
            f"worktree ({claim.get('worktree_root')!r}); continue it here -- no takeover "
            f"applies, and nothing needs to be removed by hand"
        )
    return (
        f"{work_item_id!r} checkpoint {claim.get('checkpoint_id')!r} is claimed by worktree "
        f"{claim.get('worktree_root')!r}; resume it there, or take the claim over explicitly "
        f"(take_over_claim) after reviewing the takeover evidence"
    )


def _attach_ownership_evidence(repo_root: Path, exc: Exception, work_item_id: str, claim: dict | None) -> None:
    """Annotate every refusal step 1c raises with the ownership evidence a
    reviewer must be able to read off the exception rather than
    re-deriving by hand: the claim record, the holder, the claimed
    checkpoint, and the escape that applies (`OPUS-R84-001`). Attached
    unconditionally, including when the claim is absent -- the prototype's
    early return on `claim is None` is exactly the defect revision 70
    (`OPUS-R87-003`) withdrew, since an absent claim is a load-bearing
    observation for `CheckpointOriginationUnprovableError` and excluding
    it left the class this repository is actually in (no claim for
    `workflow-v2-1-core`) reporting nothing.

    For `CheckpointOriginationUnprovableError` specifically, also attaches
    the components revision 71 (`OPUS-R88-005`) adds: this worktree's own
    local identity observation, and whether the claim is absent, foreign,
    or self-owned -- so an operator reading the refusal is told the whole
    picture, not just that origination is unprovable.

    Idempotent: a refusal already carrying evidence (raised, then
    re-raised through an outer handler) keeps its first attachment."""
    if getattr(exc, "ownership_evidence", None) is not None:
        return
    exc.ownership_evidence = {
        "claim": claim,
        "holder": claim.get("worktree_root") if claim is not None else None,
        "claimed_checkpoint": claim.get("checkpoint_id") if claim is not None else None,
        "escape": _ownership_escape_hint(repo_root, work_item_id, claim),
    }
    if isinstance(exc, CheckpointOriginationUnprovableError):
        if claim is None:
            claim_state = "absent"
        elif claim_is_this_worktree(repo_root, claim):
            claim_state = "self_owned"
        else:
            claim_state = "foreign"
        exc.ownership_evidence["local_identity"] = local_identity_observation(repo_root)
        exc.ownership_evidence["claim_state"] = claim_state
        exc.ownership_evidence["origination"] = dict(exc.evidence)


def resolve_checkpoint_ownership(
    repo_root: Path, work_item: dict, work_item_id: str, selected_id: str | None, *, now: str,
) -> tuple[str, str | None, str | None]:
    """`/milestone-implement`'s step 1c. `select_next_checkpoint` (1b)
    stays pure and untouched; this is the new resolution step that
    reconciles its result against the shared claim.

    Returns `(outcome, checkpoint_id, owner_token)` where `outcome` is
    `RESUME`, `FRESH`, `CONTINUE_CLAIM`, or the terminal `NO_CHECKPOINT`.
    No mutation-capable outcome ever carries a `None` checkpoint id
    (`GPT-R81-003`): an exhausted registry is `NO_CHECKPOINT`, which
    re-enters the command's existing "nothing to implement this
    invocation" path and never reaches step 1d. `owner_token` is `None`
    only for `NO_CHECKPOINT` and `FRESH`, where step 1d mints it by
    acquiring the claim -- before any state write.

    The internal order is the security property: whenever either
    authority (the local `WORKFLOW_STATE.json` or the shared claim) says
    this work item is contended, `verify_dirty_resume_safety` -- the sole
    proof of worktree *instance* origination (revision 68, `OPUS-R85-001`;
    a self-owned claim is never a substitute, since it proves only a
    *location*) -- runs before any branch that can mutate. Every refusal
    raised from that point on carries the ownership evidence
    (`_attach_ownership_evidence`)."""
    checkpoints = work_item.get("checkpoints", {})
    current_id = work_item.get("current_checkpoint_id")
    local_in_progress = (
        current_id if checkpoints.get(current_id, {}).get("status") == "IN_PROGRESS" else None
    )
    claim = resolve_claim(repo_root, work_item_id)

    if claim is None and local_in_progress is None:
        # The ordinary uncontended case, which must stay exactly as
        # permissive as it is today.
        if selected_id is None:
            return NO_CHECKPOINT, None, None
        return FRESH, selected_id, None

    try:
        verify_dirty_resume_safety(repo_root, work_item_id)

        # A foreign claim is refused here even though `verify_dirty_resume_
        # safety` already passed: a worktree carrying a valid identity
        # record of its own for this work item -- a displaced owner after a
        # takeover, typically -- passes that check and is refused one line
        # later, by `CheckpointOwnedByOtherWorktreeError`, instead.
        if claim is not None and not claim_is_this_worktree(repo_root, claim):
            raise CheckpointOwnedByOtherWorktreeError(
                f"{work_item_id!r} checkpoint {claim.get('checkpoint_id')!r} is claimed by "
                f"worktree {claim.get('worktree_root')!r}"
            )

        if local_in_progress is not None:
            if claim is None:
                # Locally interrupted with no shared claim: a checkpoint
                # started before this mechanism existed (the real
                # `workflow-v2-1-core:WF8b`, and `v2-1-dry-run:S-CP3`), or a
                # claim lost out of band. `adopt_claim` owns the full
                # origination test (and re-evaluates it at publication) --
                # this branch does not duplicate it.
                if selected_id is not None and selected_id != local_in_progress:
                    raise CheckpointOwnershipStateMismatchError(
                        f"{work_item_id!r}'s WORKFLOW_STATE.json records {local_in_progress!r} "
                        f"IN_PROGRESS but selection resolved {selected_id!r} -- reporting rather "
                        f"than guessing which is right"
                    )
                adopted = adopt_claim(repo_root, work_item_id, local_in_progress, now=now)
                return RESUME, local_in_progress, adopted.get("owner_token")

            claimed_id = claim.get("checkpoint_id")
            if local_in_progress != claimed_id or (selected_id is not None and selected_id != claimed_id):
                raise CheckpointOwnershipStateMismatchError(
                    f"this worktree claims {work_item_id!r} checkpoint {claimed_id!r}, its "
                    f"WORKFLOW_STATE.json records {local_in_progress!r} IN_PROGRESS, and "
                    f"selection resolved {selected_id!r} -- reporting rather than guessing "
                    f"which is right"
                )
            return RESUME, claimed_id, claim.get("owner_token")

        # Nothing is locally IN_PROGRESS. Since the uncontended branch above
        # already excluded "claim is None and local_in_progress is None",
        # and the foreign-claim check above already excluded a foreign
        # claim, the claim here is self-owned.
        claimed_id = claim.get("checkpoint_id")
        local_status = checkpoints.get(claimed_id, {}).get("status")

        if local_status is None:
            # Crashed between publishing the claim and writing the state
            # (1d's own ordering exists to keep this window narrow, never
            # to close it). The owner finishes its own acquisition -- this
            # releases nothing and discards nothing, so it is not a
            # stale-claim release.
            if selected_id is not None and selected_id != claimed_id:
                raise CheckpointOwnershipStateMismatchError(
                    f"this worktree claims {work_item_id!r} checkpoint {claimed_id!r} with no "
                    f"local state entry, but selection resolved {selected_id!r} -- reporting "
                    f"rather than guessing"
                )
            return CONTINUE_CLAIM, claimed_id, claim.get("owner_token")

        if local_status == "COMPLETE":
            # The single automatic release in the whole design: a
            # self-owned claim on a checkpoint whose completion is
            # **durable** (committed at HEAD), i.e. a crash between step
            # 1f's commit and its release. The working tree alone saying
            # `COMPLETE` is not enough -- `select_next_checkpoint` already
            # ran against this same local state, so `selected_id` is
            # already the concrete next checkpoint (or `None` because the
            # registry is exhausted), and reselecting here would duplicate
            # rather than trust that.
            durable = committed_checkpoint_status(repo_root, work_item_id, claimed_id)
            if durable == "COMPLETE":
                release_checkpoint(repo_root, work_item_id, claimed_id,
                                   owner_token=claim.get("owner_token"), now=now)
                if selected_id is None:
                    return NO_CHECKPOINT, None, None
                return FRESH, selected_id, None
            raise CheckpointOwnershipStateMismatchError(
                f"this worktree's claim on {work_item_id!r} checkpoint {claimed_id!r} shows "
                f"COMPLETE in the working tree but {durable!r} in the committed state at HEAD "
                f"-- refusing to release a claim whose completion is not durable"
            )

        raise CheckpointOwnershipStateMismatchError(
            f"this worktree holds a claim on {work_item_id!r} checkpoint {claimed_id!r}, but "
            f"its WORKFLOW_STATE.json records status {local_status!r} with nothing IN_PROGRESS "
            f"-- reporting rather than guessing whether to resume or discard"
        )
    except Exception as exc:
        _attach_ownership_evidence(repo_root, exc, work_item_id, claim)
        raise


# ---------------------------------------------------------------------------
# WF8b: D-Checkpoint-Ownership -- WFR-66's identity-query enforcement
# (docs/ai-workflow/WORKFLOW_V2_PLAN.md, "The origination reference"'s
# "The contract covers the identity queries too" / "The identity queries'
# own read partition", plus D1's/D-Registry's own enforcement-point text).
#
# Reuses `origination_reference_commits` -- the identity queries and the
# origination test share one reference, just two different read
# partitions over it (`OPUS-R90-003`): the origination test asks whether
# a *status* was ever IN_PROGRESS; these ask whether a *key* -- a
# work_item_id, or a (work_item_id, checkpoint_id) pair -- was ever
# present at all, which fails closed in the opposite direction (key
# presence is itself the observation, undecidability is scoped to the
# containers strictly *above* the queried key, and the two partitions
# disagree on four of the document shapes explicitly enumerated in the
# plan's "two tables genuinely diverge" paragraph).
#
# Two permanent, unescapable refusals name a decidable observation
# directly (`WorkItemIdReusedError`/`CheckpointIdReusedError`); one
# escapable refusal (`IdentityReferenceUndecidableError`) is cleared only
# by the evidence-bound, non-replayable, repository-serialized
# `authorize_identity_reference_gap` -- `recover_abandoned_destructive_
# guard`'s own signature, deliberately, per `OPUS-R91-003`.
# ---------------------------------------------------------------------------


class IdentityReferenceUndecidableError(Exception):
    """Raised by a WFR-66 identity-enforcement query (work-item creation
    or registry checkpoint-id validation) when D-Checkpoint-Ownership's
    origination reference cannot establish, at one or more commits,
    whether the queried key -- a work_item_id, or a (work_item_id,
    checkpoint_id) pair -- is present or absent. Distinct from a
    decidable observation, which is a permanent, unescapable refusal this
    exception is never raised for (`WorkItemIdReusedError`/
    `CheckpointIdReusedError` instead, since it names a binding rather
    than a repairable repository condition). Carries `.evidence` (the
    undecidable commits and their failure classes, the identity queried,
    and the examined-commit count) for `authorize_identity_reference_gap`'s
    digest and for the operator to inspect. Cleared only by that
    operation's explicit, evidence-bound, non-replayable authorization."""

    def __init__(self, message: str, *, evidence: dict):
        super().__init__(message)
        self.evidence = evidence


class WorkItemIdReusedError(Exception):
    """Raised when work-item creation names a `work_item_id` decidably
    observed at some commit in D-Checkpoint-Ownership's origination
    reference -- ids are permanently non-reusable (D1, revision 71,
    `OPUS-R88-004`), a stronger and historical-evidence-bound check than
    `WorkItemTerminalReuseError`'s narrower, live-state-only one."""


class CheckpointIdReusedError(Exception):
    """Raised when a registry write reintroduces a `checkpoint_id`
    decidably observed for that work item at some commit in
    D-Checkpoint-Ownership's origination reference -- checkpoint ids are
    permanently non-reusable within their work item, but only for ids
    genuinely new to this revision: an id kept live across revisions is
    in-place redefinition, not reuse, and stays legal (D-Registry,
    revision 72, `OPUS-R89-005`)."""


class IdentityGapAuthorizationUnavailableError(Exception):
    """Raised when the durable identity-reference-gap-authorization
    record cannot be read or written -- fails closed rather than
    proceeding unprotected."""


IDENTITY_GAP_AUTHORIZATIONS_RELDIR = "ai-workflow/identity-gap-authorizations"
IDENTITY_GAP_LOCK_RELPATH = "ai-workflow/identity-gap.lock"


def _identity_query_at_commit(
    repo_root: Path, commit: str, state_rel_path: str, work_item_id: str, checkpoint_id: str | None,
) -> tuple[str, dict]:
    """One commit's contribution to an identity-reference query -- "has
    this work_item_id (checkpoint_id=None) or this (work_item_id,
    checkpoint_id) pair ever been observed?" -- per "The identity
    queries' own read partition". Returns `(outcome, detail)`:

    - `"observed"` -- the queried key is decidably **present** at its own
      level, whatever its value -- key presence is the whole of what an
      existence query asks, so this wins unescapably over every other
      row for the same commit, and is checked directly with `in` before
      the value is ever inspected, which is what keeps the rows disjoint
      by construction rather than by a separately-stated precedence rule;
    - `"undecidable"` -- the reader cannot establish presence or absence
      of the queried key itself: an unlistable tree, an unresolvable
      commit, a state path present but not a readable regular-file blob,
      an unparseable or non-object document, or a non-object container
      strictly *above* the queried key (`work_items` for the work-item
      query; `work_items`, the work item entry, or `checkpoints` for the
      pair query);
    - `"decidable"` -- the queried key is decidably **absent**: a missing
      key, established with `in`, at every level examined on the way to
      it."""
    listing = subprocess.run(
        ["git", "ls-tree", commit, "--", state_rel_path],
        cwd=repo_root, capture_output=True, text=True,
    )
    if listing.returncode != 0:
        return "undecidable", {"commit": commit, "failure_class": "tree unlistable or commit unresolvable"}
    line = listing.stdout.strip()
    if not line:
        return "decidable", {"commit": commit, "reason": "state path absent from tree"}
    meta, _, _ = line.partition("\t")
    mode = meta.split()[0]
    blob_sha = meta.split()[2]
    if mode not in ("100644", "100755"):
        return "undecidable", {"commit": commit,
                                "failure_class": f"state path is not a regular-file blob (mode {mode})"}
    blob = subprocess.run(["git", "cat-file", "-p", blob_sha], cwd=repo_root, capture_output=True, text=True)
    if blob.returncode != 0:
        return "undecidable", {"commit": commit, "failure_class": "blob could not be read"}
    try:
        doc = json.loads(blob.stdout)
    except json.JSONDecodeError:
        return "undecidable", {"commit": commit, "failure_class": "state document unparseable"}
    if not isinstance(doc, dict):
        return "undecidable", {"commit": commit, "failure_class": "state document is not an object"}

    if "work_items" not in doc:
        return "decidable", {"commit": commit, "reason": "no work_items key"}
    work_items = doc["work_items"]
    if not isinstance(work_items, dict):
        return "undecidable", {"commit": commit, "failure_class": "work_items is not an object"}

    if checkpoint_id is None:
        if work_item_id in work_items:
            return "observed", {"commit": commit, "reason": f"{work_item_id!r} key present in work_items"}
        return "decidable", {"commit": commit, "reason": f"no {work_item_id!r} entry"}

    if work_item_id not in work_items:
        return "decidable", {"commit": commit, "reason": f"no {work_item_id!r} entry"}
    work_item = work_items[work_item_id]
    if not isinstance(work_item, dict):
        return "undecidable", {"commit": commit, "failure_class": f"{work_item_id!r} entry is not an object"}

    if "checkpoints" not in work_item:
        return "decidable", {"commit": commit, "reason": "no checkpoints key"}
    checkpoints = work_item["checkpoints"]
    if not isinstance(checkpoints, dict):
        return "undecidable", {"commit": commit, "failure_class": "checkpoints is not an object"}

    if checkpoint_id in checkpoints:
        return "observed", {"commit": commit, "reason": f"{checkpoint_id!r} key present in checkpoints"}
    return "decidable", {"commit": commit, "reason": f"no {checkpoint_id!r} entry"}


def _scan_identity_reference(
    repo_root: Path, work_item_id: str, checkpoint_id: str | None = None, *,
    state_rel_path: str | None = None,
) -> dict:
    """Scans every commit `origination_reference_commits` enumerates for
    WFR-66's two enforcement points. A decidable observation anywhere
    binds -- no supersession, no recency, no scoping to a lifecycle
    instance, the same reduction rule the origination test states, shared
    without exception -- and wins over any undecidable commit found in
    the same scan, since the two routes here differ in whether an escape
    exists at all rather than merely in which refusal is reported.

    Returns an evidence dict with `"decision"` `"observed"` or `"admit"`.
    Raises `IdentityReferenceUndecidableError` (never returns
    `"undecidable"`) when the reference itself cannot be resolved or when
    any commit is undecidable for the queried key itself and no commit
    decidably observes it."""
    state_rel_path = state_rel_path or DEFAULT_STATE_PATH.as_posix()
    try:
        commits = origination_reference_commits(repo_root, state_rel_path)
    except CheckpointOriginationUnprovableError as exc:
        raise IdentityReferenceUndecidableError(
            f"the identity reference could not be resolved for {state_rel_path!r}: {exc}",
            evidence={
                "route": "undecidable", "work_item_id": work_item_id, "checkpoint_id": checkpoint_id,
                "state_rel_path": state_rel_path, "examined_commits": 0,
                "reference_unresolvable": True, "undecidable_commits": [],
            },
        ) from exc
    if not commits:
        return {
            "decision": "admit", "route": "no_reference_commits", "work_item_id": work_item_id,
            "checkpoint_id": checkpoint_id, "state_rel_path": state_rel_path, "examined_commits": 0,
        }

    observed = None
    undecidable_commits: list[dict] = []
    for commit in commits:
        outcome, detail = _identity_query_at_commit(repo_root, commit, state_rel_path, work_item_id, checkpoint_id)
        if outcome == "observed" and observed is None:
            observed = detail
        elif outcome == "undecidable":
            undecidable_commits.append({"commit": detail["commit"], "failure_class": detail["failure_class"]})

    if observed is not None:
        return {
            "decision": "observed", "commit": observed["commit"], "reason": observed["reason"],
            "work_item_id": work_item_id, "checkpoint_id": checkpoint_id,
            "state_rel_path": state_rel_path, "examined_commits": len(commits),
        }
    if undecidable_commits:
        identity = f"{work_item_id!r}" + (f"/{checkpoint_id!r}" if checkpoint_id is not None else "")
        raise IdentityReferenceUndecidableError(
            f"the identity reference for {identity} is undecidable at {len(undecidable_commits)} "
            f"commit(s) -- neither observed nor provably never observed; "
            f"authorize_identity_reference_gap is the one explicit, evidence-bound escape",
            evidence={
                "route": "undecidable", "work_item_id": work_item_id, "checkpoint_id": checkpoint_id,
                "state_rel_path": state_rel_path, "examined_commits": len(commits),
                "reference_unresolvable": False, "undecidable_commits": undecidable_commits,
            },
        )
    return {
        "decision": "admit", "route": "scanned_all_decidable", "work_item_id": work_item_id,
        "checkpoint_id": checkpoint_id, "state_rel_path": state_rel_path, "examined_commits": len(commits),
    }


def gap_observation_id(evidence: Mapping) -> str:
    """The digest `authorize_identity_reference_gap`'s literal and
    durable record both bind to: the exact set of undecidable commits and
    the failure class observed at each, **plus the identity being
    authorized** (`work_item_id`, and `checkpoint_id` when the query is
    the pair query) -- so an authorization written against one damaged
    commit can never clear a different one that appeared since, and can
    never be replayed against a different identity (`OPUS-R91-003`)."""
    commits = sorted(
        (
            {"commit": entry["commit"], "failure_class": entry["failure_class"]}
            for entry in evidence.get("undecidable_commits", [])
        ),
        key=lambda entry: entry["commit"],
    )
    payload = {
        "work_item_id": evidence.get("work_item_id"),
        "checkpoint_id": evidence.get("checkpoint_id"),
        "reference_unresolvable": bool(evidence.get("reference_unresolvable", False)),
        "undecidable_commits": commits,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def identity_reference_gap_authorization_literal(
    work_item_id: str, checkpoint_id: str | None, evidence: Mapping,
) -> str:
    """`authorize identity reference gap <work_item_id> [checkpoint
    <checkpoint_id>] gap <gap_observation_id>` -- distinct in every
    component from the takeover's and the abandoned-guard recovery's own
    literals, unwritable from memory, and validated against the
    re-derived digest rather than merely parsed."""
    oid = gap_observation_id(evidence)
    if checkpoint_id is not None:
        return f"authorize identity reference gap {work_item_id} checkpoint {checkpoint_id} gap {oid}"
    return f"authorize identity reference gap {work_item_id} gap {oid}"


def identity_gap_authorizations_dir(repo_root: Path) -> Path:
    _, common_dir, _ = _git_identity(repo_root)
    return Path(common_dir) / IDENTITY_GAP_AUTHORIZATIONS_RELDIR


def identity_gap_authorization_path(repo_root: Path, gap_oid: str) -> Path:
    """Named by a digest of the digest -- the same "token, not a path"
    discipline the claim record uses -- so no crafted
    `gap_observation_id` value can ever address anything outside this
    directory."""
    token = hashlib.sha256(gap_oid.encode()).hexdigest()
    return identity_gap_authorizations_dir(repo_root) / f"{token}.json"


def identity_gap_lock_path(repo_root: Path) -> Path:
    _, common_dir, _ = _git_identity(repo_root)
    return Path(common_dir) / IDENTITY_GAP_LOCK_RELPATH


@contextlib.contextmanager
def identity_gap_lock(repo_root: Path):
    """The repository-level `fcntl.flock` leaf `authorize_identity_
    reference_gap` runs its re-derive-then-publish sequence inside,
    because at work-item creation there is no work item, no claim and no
    per-work-item mutation guard to serialize on -- the entire
    concurrency apparatus this design otherwise relies on is keyed on an
    entity that does not exist yet at this call site (`OPUS-R91-003`).
    Never acquired while the per-work-item mutation guard, the
    per-worktree identity `flock`, or `D1`'s state-writer `flock` is
    held, and none of those three is acquired while this one is held --
    see `D-Approval-Commits`' single lock-ordering site for the complete
    set. Stable, never unlinked, process-scoped; released by the kernel
    on process death."""
    path = identity_gap_lock_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _stage_identity_gap_authorization_payload(path: Path, record: dict) -> Path:
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    _assert_not_symlink(path.parent, "identity-gap-authorizations directory")
    _assert_not_symlink(path, "identity-gap-authorization record")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".gap-", suffix=".tmp")
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise IdentityGapAuthorizationUnavailableError(
            f"cannot stage an identity-reference-gap authorization next to {path} ({exc})"
        ) from exc
    return Path(tmp_name)


def _publish_identity_gap_authorization_exclusive(path: Path, record: dict) -> None:
    """Atomic in both senses, exactly as the claim record's own exclusive
    publication is: the whole payload is staged at a same-directory temp
    name and then `os.link`ed into place, so `os.link`'s `EEXIST` **is**
    the non-replayability check -- the test and the write are one
    operation, with no read-then-write window between them."""
    tmp = _stage_identity_gap_authorization_payload(path, record)
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise
    except OSError as exc:
        raise IdentityGapAuthorizationUnavailableError(f"cannot publish {path} ({exc})") from exc
    finally:
        tmp.unlink(missing_ok=True)


def _read_identity_gap_authorization(path: Path) -> dict | None:
    _assert_not_symlink(path.parent, "identity-gap-authorizations directory")
    _assert_not_symlink(path, "identity-gap-authorization record")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise IdentityGapAuthorizationUnavailableError(f"cannot read {path} ({exc})") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise IdentityGapAuthorizationUnavailableError(f"cannot read {path} ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IdentityGapAuthorizationUnavailableError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise IdentityGapAuthorizationUnavailableError(
            f"{path} has an unsupported shape/schema_version (expected 1)")
    return data


def authorize_identity_reference_gap(
    repo_root: Path, work_item_id: str, checkpoint_id: str | None = None, *,
    now: str, user_authorization: str | None, evidence: Mapping,
) -> dict:
    """WFR-66's one explicit, evidence-bound, non-replayable escape from
    `IdentityReferenceUndecidableError` (`recover_abandoned_destructive_
    guard`'s own signature, deliberately -- every parameter that sibling
    needs, this one needs for the same reason, `OPUS-R91-003`).

    `evidence` is the `.evidence` a caller's own `IdentityReferenceUndecidableError`
    carried; this function never re-scans the reference to build its own
    evidence from scratch -- the caller already did, and a fresh scan here
    would let a second, cheaper read silently substitute for the one the
    user was actually shown. It re-derives the digest and re-scans only to
    check the evidence is still current, never to source it.

    Never overrides a decidable observation: with the gap authorized, the
    query still refuses if any decidable commit observes the identity --
    this only ever admits the undecidability, never the binding.
    Idempotent rather than consumed on read: a crash between this
    record's publication and the creation it authorizes resumes, on
    retry, by recognising its own completed authorization."""
    if evidence.get("route") != "undecidable":
        raise CheckpointClaimTakeoverRefusedError(
            "authorize_identity_reference_gap requires evidence of an undecidable identity "
            "reference read -- there is nothing to authorize")
    if evidence.get("work_item_id") != work_item_id or evidence.get("checkpoint_id") != checkpoint_id:
        raise CheckpointClaimTakeoverRefusedError(
            f"the supplied evidence was taken for "
            f"({evidence.get('work_item_id')!r}, {evidence.get('checkpoint_id')!r}), not "
            f"({work_item_id!r}, {checkpoint_id!r}) -- refusing to authorize a gap against an "
            f"identity the evidence never observed")

    oid = gap_observation_id(evidence)
    expected = identity_reference_gap_authorization_literal(work_item_id, checkpoint_id, evidence)
    if user_authorization != expected:
        raise CheckpointClaimTakeoverRefusedError(
            f"authorizing an identity-reference gap requires the literal authorization "
            f"{expected!r}, derived from the evidence just presented -- refusing to admit an "
            f"unprovable identity on an inference or on a remembered literal")

    state_rel_path = evidence.get("state_rel_path") or DEFAULT_STATE_PATH.as_posix()

    with identity_gap_lock(repo_root):
        path = identity_gap_authorization_path(repo_root, oid)
        existing = _read_identity_gap_authorization(path)
        if existing is not None:
            if existing.get("work_item_id") != work_item_id or existing.get("checkpoint_id") != checkpoint_id:
                raise CheckpointClaimTakeoverRefusedError(
                    f"a gap authorization already exists at {path} bound to a different "
                    f"identity -- this should be unreachable, since the digest binds the "
                    f"identity")
            return existing

        try:
            fresh = _scan_identity_reference(repo_root, work_item_id, checkpoint_id,
                                             state_rel_path=state_rel_path)
        except IdentityReferenceUndecidableError as exc:
            if gap_observation_id(exc.evidence) != oid:
                raise CheckpointClaimTakeoverRefusedError(
                    f"the identity reference changed between the evidence the user authorized "
                    f"({oid}) and this authorization ({gap_observation_id(exc.evidence)}) -- "
                    f"refusing, having recorded nothing; present fresh evidence and obtain a "
                    f"fresh authorization"
                ) from exc
            # still undecidable, same digest -- proceed to publish below.
        else:
            identity = f"{work_item_id!r}" + (f"/{checkpoint_id!r}" if checkpoint_id is not None else "")
            if fresh["decision"] == "observed":
                raise CheckpointClaimTakeoverRefusedError(
                    f"{identity} is now decidably observed at commit {fresh['commit']} -- "
                    f"refusing to authorize a gap against a binding that now exists")
            raise CheckpointClaimTakeoverRefusedError(
                f"{identity} is now decidably absent from the identity reference -- no gap "
                f"remains to authorize; the query now admits on its own")

        record = {
            "schema_version": 1,
            "gap_observation_id": oid,
            "work_item_id": work_item_id,
            "checkpoint_id": checkpoint_id,
            "undecidable_commits": list(evidence.get("undecidable_commits", [])),
            "authorized_at": now,
            "authorizing_worktree_git_dir": _worktree_git_dir(repo_root),
            "consumed": False,
        }
        _publish_identity_gap_authorization_exclusive(path, record)
        return record


def mark_identity_reference_gap_consumed(repo_root: Path, gap_oid: str) -> None:
    """Diagnostic bookkeeping only, run once the identity/registry
    creation the gap authorized has completed -- the refusal a
    *different* identity receives never depends on this flag, so a crash
    before this runs simply leaves `consumed: false` on an authorization
    whose creation already happened, with no correctness consequence."""
    with identity_gap_lock(repo_root):
        path = identity_gap_authorization_path(repo_root, gap_oid)
        record = _read_identity_gap_authorization(path)
        if record is None or record.get("consumed"):
            return
        record["consumed"] = True
        payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".gap-", suffix=".tmp")
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)


def identity_reference_admits(
    repo_root: Path, work_item_id: str, checkpoint_id: str | None = None, *,
    state_rel_path: str | None = None,
) -> dict:
    """The one check `D1`'s work-item-creation refusal and `D-Registry`'s
    checkpoint-id-reuse refusal both perform (`OPUS-R89-005`/`OPUS-R90-003`):
    has this `work_item_id` (`checkpoint_id=None`) or this
    `(work_item_id, checkpoint_id)` pair ever been observed in
    D-Checkpoint-Ownership's origination reference? A decidable
    observation is a permanent, unescapable refusal
    (`WorkItemIdReusedError`/`CheckpointIdReusedError`). An undecidable
    reference refuses with `IdentityReferenceUndecidableError` unless a
    matching `authorize_identity_reference_gap` record already exists, in
    which case the undecidability is recognised as authorized and the
    query admits -- never a decidable observation, only ever the gap."""
    try:
        result = _scan_identity_reference(repo_root, work_item_id, checkpoint_id,
                                          state_rel_path=state_rel_path)
    except IdentityReferenceUndecidableError as exc:
        oid = gap_observation_id(exc.evidence)
        record = _read_identity_gap_authorization(identity_gap_authorization_path(repo_root, oid))
        if record is None:
            raise
        if record.get("work_item_id") != work_item_id or record.get("checkpoint_id") != checkpoint_id:
            raise
        return {"decision": "admit", "route": "authorized_gap", "gap_observation_id": oid,
                "work_item_id": work_item_id, "checkpoint_id": checkpoint_id}
    if result["decision"] == "observed":
        if checkpoint_id is None:
            raise WorkItemIdReusedError(
                f"work_item_id {work_item_id!r} has already appeared in D-Checkpoint-Ownership's "
                f"origination reference (commit {result['commit']}) -- work-item ids are "
                f"permanently non-reusable (D1, OPUS-R88-004)")
        raise CheckpointIdReusedError(
            f"checkpoint id {checkpoint_id!r} has already been observed for work item "
            f"{work_item_id!r} in D-Checkpoint-Ownership's origination reference (commit "
            f"{result['commit']}) -- checkpoint ids this work item has ever had observed are "
            f"permanently non-reusable, even after removal or renaming (D-Registry, "
            f"OPUS-R88-004/OPUS-R89-005)")
    return result


# ---------------------------------------------------------------------------
# WF4a-iii: D-States' "no protected path is dirty" gate condition
# ---------------------------------------------------------------------------


def _dirty_paths(repo_root: Path) -> set[str]:
    """Paths with uncommitted changes relative to HEAD -- staged,
    unstaged, and untracked-but-not-ignored -- used by the
    `AWAITING_TECHNICAL_APPROVAL` entry condition's "no protected path is
    dirty" clause (D3). Distinct from `workflow_fingerprint`'s own
    `base_commit..worktree` diff, which measures change since plan
    approval, not uncommitted state."""
    changed = _run(["git", "diff", "--name-only", "-z", "HEAD"], cwd=repo_root)
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo_root)
    paths = {p for p in changed.split("\x00") if p}
    paths |= {p for p in untracked.split("\x00") if p}
    return paths


def any_protected_path_dirty(
    repo_root: Path,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> bool:
    """`AWAITING_TECHNICAL_APPROVAL`'s entry condition (D-States): "no
    protected path is dirty", `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json`
    dirtiness never blocking this by construction -- both are declared
    `excluded_paths` in the implementation-stage classification, never
    `protected` (missing-test item 16). Fails closed (`UnclassifiedPathError`)
    on a dirty path neither set names, the same discipline
    `classify_path_implementation_stage` already applies to the
    `base..worktree` diff."""
    for path in sorted(_dirty_paths(repo_root)):
        classification = fingerprint.classify_path_implementation_stage(
            path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
        )
        if classification == "protected":
            return True
    return False


def _changed_paths_between(repo_root: Path, base: str, head: str) -> set[str]:
    out = _run(["git", "diff", "--name-only", "-z", base, head], cwd=repo_root)
    return {p for p in out.split("\x00") if p}


def any_protected_path_changed_since(
    repo_root: Path, base: str, head: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> bool:
    """D-Legacy phase 2's own freshness primitive (`WF-M8b`,
    `promote_legacy_work_item`): a committed-history counterpart of
    `any_protected_path_dirty` -- "did any protected implementation-stage
    path change between `base` and `head`" -- deliberately **not** an
    exact-hash-reproduction check like `approval_is_current`. That
    function embeds the full classification config itself in its hashed
    projection, so widening a work item's own artifact-declarations file
    to correctly exclude paths a *concurrent* work item introduces while
    a legacy entry sits dormant (exactly `milestone-8-artifacts.json`'s
    own situation: workflow-v2-1-core's later `.claude/commands/`,
    `scripts/`, `docs/ai-workflow/` commits were never anticipated by the
    narrow set `WF-M8a` originally authored) would otherwise permanently
    and incorrectly read as `STALE` even with zero real protected-content
    drift -- the classification text itself would no longer hash-match,
    regardless of whether `app/`/`gradle/` content actually changed. This
    function never compares against a stored digest; it only answers
    whether a `protected` path is present in the diff, so widening the
    *excluded* side of a classification is safe here in a way it is not
    for `approval_is_current`. Fails closed (`UnclassifiedPathError`,
    propagated from `classify_path_implementation_stage`) on a changed
    path neither set names, same discipline as `any_protected_path_dirty`."""
    for path in sorted(_changed_paths_between(repo_root, base, head)):
        classification = fingerprint.classify_path_implementation_stage(
            path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
        )
        if classification == "protected":
            return True
    return False


# ---------------------------------------------------------------------------
# D-Self-Governance: activation/rollback event search
# ---------------------------------------------------------------------------


# workflow-2.5.0 (D-Implementation-Review-Version-Activation): the explicit,
# declared predecessor mapping generalizing the prior hard-coded `"1"`
# rollback-destination literal. Rollback targets the version activation
# actually superseded -- `"2.2"` rolls back to `"2.1"`, `"2.1"` rolls back
# to `"1"` -- never a derived value and never a version-string ordering
# comparison (lexicographic comparison is unsound for these values:
# `"2.10" < "2.2"`). This is also the sole legal domain for a
# `Workflow-Rollback` trailer's own value: a value outside it (bare/empty,
# a typo, or an unrecognized future version) is a resolution *miss*,
# handled explicitly and fail-closed everywhere it is looked up below,
# never a silent `dict.get` fall-through and never an uncaught `KeyError`.
ACTIVATION_ROLLBACK_PREDECESSOR = {"2.1": "1", "2.2": "2.1"}


def find_latest_activation_event(
    repo_root: Path, head: str = "HEAD",
) -> tuple[str, str | None, str] | None:
    """Most recent `Workflow-Activation`/`Workflow-Rollback` trailer event
    by first-parent ancestry from `head` (resolves GPT-R9-007's missing-
    config recovery rule: "the latest event", not just "any activation
    trailer"). Returns `(kind, destination_version, commit)` where `kind`
    is `"activation"` or `"rollback"`, or `None` if neither has ever
    landed.

    `destination_version` (workflow-2.5.0, D-Implementation-Review-
    Version-Activation, version-aware event model) is the event's own
    resolved destination: an activation trailer's value, used directly --
    though `is_activated` reports `True` for the activation direction
    unconditionally, for every value including `"1"`, never conditioned on
    this field for that direction; a rollback trailer's value, resolved
    through `ACTIVATION_ROLLBACK_PREDECESSOR`'s explicit domain, where
    `is_activated` reports `True` whenever this resolved destination is not
    `"1"`. `destination_version` is `None` only for a `"rollback"` kind
    whose trailer value falls outside that domain -- the fail-closed miss
    case `is_activated` treats as activated, never as not-activated."""
    for commit in _first_parent_commits_ordered(repo_root, head):
        trailers = _commit_trailers(repo_root, commit)
        if "Workflow-Activation" in trailers:
            return ("activation", trailers["Workflow-Activation"].strip(), commit)
        if "Workflow-Rollback" in trailers:
            value = trailers["Workflow-Rollback"].strip()
            return ("rollback", ACTIVATION_ROLLBACK_PREDECESSOR.get(value), commit)
    return None


def _activation_event_description(repo_root: Path, event: tuple[str, str | None, str]) -> tuple[bool, str]:
    """Shared by `is_activated` and `load_config`'s missing-config recovery
    message (workflow-2.5.0): resolves a non-`None`
    `find_latest_activation_event` result to `(activated, description)`.
    `description` names the resolved destination version (e.g.
    `"Workflow 2.2"`) when one exists, or the unresolvable trailer's own
    raw value verbatim in either fail-closed miss case -- a blank
    `Workflow-Activation` trailer (`destination_version == ""`) or an
    unresolvable `Workflow-Rollback` trailer (`destination_version is
    None`) -- never a version name that, in either miss case, by
    construction does not exist. This is the module's one computation of
    "is this event activated", never duplicated at either call site.

    The activation direction is fail-closed by construction, exactly like
    `2.4.0`'s binary `kind == "activation"` check: an `"activation"` event
    reports activated for *every* trailer value, including `"1"` (I2 --
    reading `destination_version != "1"` unconditionally regressed this one
    value fail-*open* against `2.4.0`, the only direction nothing declared
    a deliberate difference for). Only the rollback direction's *resolved*
    destination may ever report not-activated; an unresolvable rollback
    trailer still fails closed, same as before."""
    kind, destination_version, commit = event
    if kind == "activation":
        if destination_version:
            return True, f"Workflow {destination_version}"
        # A blank Workflow-Activation trailer (destination_version == "").
        raw_value = _commit_trailers(repo_root, commit).get("Workflow-Activation")
        return True, f"an unresolvable Workflow-Activation trailer value {raw_value!r}"
    # kind == "rollback": destination_version is the predecessor resolved
    # through ACTIVATION_ROLLBACK_PREDECESSOR's explicit domain, or None
    # for a trailer value outside that domain.
    if destination_version:
        return destination_version != "1", f"Workflow {destination_version}"
    raw_value = _commit_trailers(repo_root, commit).get("Workflow-Rollback")
    return True, f"an unresolvable Workflow-Rollback trailer value {raw_value!r}"


def is_activated(repo_root: Path, head: str = "HEAD") -> bool:
    event = find_latest_activation_event(repo_root, head)
    if event is None:
        return False
    activated, _description = _activation_event_description(repo_root, event)
    return activated


def build_activated_config(config: dict, target_version: str = "2.1") -> dict:
    """`WF-Activate`'s own transform, generalized (workflow-2.5.0,
    D-Implementation-Review-Version-Activation) to a `target_version`
    parameter -- default `"2.1"`, preserving the original `"1"` -> `"2.1"`
    call shape byte-for-byte -- rather than the prior hard-coded `"2.1"`
    literal, reused (not duplicated) for the `"2.1"` -> `"2.2"` bump.
    Sets `default_workflow_version` to `target_version` and appends
    `target_version` to `supported_versions` if not already present -- a
    genuinely new piece of behavior, since neither activation wrote that
    field before; every other field is untouched. The caller writes the
    returned dict to `WORKFLOW_CONFIG.json` and commits it carrying a
    `Workflow-Activation: <target_version>` trailer (D-Self-Governance) --
    this function only computes the new content, never touches Git or the
    filesystem itself, matching every other state-transform function in
    this module. Rejects a config already at `target_version`: activation
    is a sole, one-time boundary, not an idempotent setter
    (`AlreadyActivatedError`)."""
    if target_version not in ACTIVATION_ROLLBACK_PREDECESSOR:
        raise ValueError(
            f"build_activated_config: unsupported target_version {target_version!r}, "
            f"expected one of {sorted(ACTIVATION_ROLLBACK_PREDECESSOR)}"
        )
    if config.get("default_workflow_version") == target_version:
        raise AlreadyActivatedError(
            f"config.default_workflow_version is already {target_version!r} -- "
            f"WF-Activate is a one-time boundary, not an idempotent call"
        )
    supported_versions = list(config.get("supported_versions", []))
    if target_version not in supported_versions:
        supported_versions.append(target_version)
    return {
        **config,
        "default_workflow_version": target_version,
        "supported_versions": supported_versions,
    }


def build_rolled_back_config(config: dict, target_version: str = "2.1") -> dict:
    """The rollback counterpart of `build_activated_config`, generalized
    (workflow-2.5.0) the same way: rolls `default_workflow_version` back
    from `target_version` (default `"2.1"`, preserving the original
    `"2.1"` -> `"1"` call shape byte-for-byte) to `target_version`'s own
    predecessor under `ACTIVATION_ROLLBACK_PREDECESSOR`'s explicit domain
    -- `"2.1"` -> `"1"`, `"2.2"` -> `"2.1"` -- never the prior hard-coded
    `"1"` literal and never a version-string ordering comparison
    (lexicographic comparison is unsound: `"2.10" < "2.2"`). The caller
    commits the result carrying a `Workflow-Rollback: <target_version>`
    trailer. This reverts only the repository-level default -- it never
    touches any work item's own `governing_workflow_version`, fixed at
    creation and immune to this change (D-Self-Governance). Does not
    remove `target_version` from `supported_versions` -- asymmetric with
    activation's append, deliberately: `validate_governing_version` is
    only ever called with `config["default_workflow_version"]` itself, so
    a stale `supported_versions` member grants nothing today. Rejects a
    config not currently at `target_version`: there is nothing to roll
    back (`NotActivatedError`)."""
    if config.get("default_workflow_version") != target_version:
        raise NotActivatedError(
            f"config.default_workflow_version is "
            f"{config.get('default_workflow_version')!r}, not {target_version!r} -- "
            f"nothing to roll back"
        )
    predecessor = ACTIVATION_ROLLBACK_PREDECESSOR.get(target_version)
    if predecessor is None:
        raise ValueError(
            f"build_rolled_back_config: unsupported target_version {target_version!r}, "
            f"expected one of {sorted(ACTIVATION_ROLLBACK_PREDECESSOR)}"
        )
    return {**config, "default_workflow_version": predecessor}


# ---------------------------------------------------------------------------
# WORKFLOW_CONFIG.json
# ---------------------------------------------------------------------------


def default_config() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "default_workflow_version": PRE_ACTIVATION_FAILSAFE_VERSION,
        "supported_versions": ["1", "2.1"],
    }


def validate_config(config: dict) -> None:
    if config.get("schema_version") != SCHEMA_VERSION:
        raise CorruptJsonError(f"unknown config schema_version: {config.get('schema_version')!r}")
    supported = config.get("supported_versions")
    if not isinstance(supported, list) or not all(isinstance(v, str) for v in supported):
        raise CorruptJsonError(f"supported_versions must be a list of strings: {supported!r}")
    default_version = config.get("default_workflow_version")
    if default_version not in supported:
        raise CorruptJsonError(
            f"default_workflow_version {default_version!r} not in supported_versions {supported!r}"
        )


def load_config(repo_root: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load `WORKFLOW_CONFIG.json` with D3's pre-/post-activation fail-safe
    rule: missing or corrupt before activation -> default to
    `default_workflow_version: "1"`; missing or corrupt after activation
    -> hard stop (resolves OPUS-R6-015). The raise message is generalized
    (workflow-2.5.0, D-Implementation-Review-Version-Activation) off the
    prior hard-coded `"Workflow v2.1"` text: it names the resolved
    destination version when the latest activation/rollback event
    resolves to one, and the unresolved `Workflow-Rollback` trailer's own
    value verbatim in the fail-closed miss case (`_activation_event_
    description`, shared with `is_activated` so the two never disagree)."""
    try:
        config = _load_json(repo_root / config_path)
    except CorruptJsonError:
        config = None
    if config is not None:
        validate_config(config)
        return config
    event = find_latest_activation_event(repo_root)
    if event is not None:
        activated, description = _activation_event_description(repo_root, event)
        if activated:
            raise ConfigMissingAfterActivationError(
                f"{config_path} is missing or corrupt, and {description} is activated -- "
                f"restore or recreate it from the activation commit's own tree, "
                f"per D-Self-Governance's missing-config recovery rule"
            )
    return default_config()


def validate_governing_version(governing_workflow_version: str, config: dict) -> None:
    """Resolves OPUS-R6-024 (optional): a work item's governing version
    must be one of the config's supported_versions, checked at creation."""
    supported = config.get("supported_versions", [])
    if governing_workflow_version not in supported:
        raise UnsupportedGoverningVersionError(
            f"governing_workflow_version {governing_workflow_version!r} not in "
            f"supported_versions {supported!r}"
        )


# ---------------------------------------------------------------------------
# Registry: topological order + registry x mapping bidirectional coverage
# ---------------------------------------------------------------------------


def validate_registry_topological_order(registry: dict) -> None:
    """D-Selection point 3: the registry JSON's `checkpoints` array order
    is normative and must be a valid topological order of the
    `depends_on` column (missing-test items 29/73)."""
    seen: set[str] = set()
    for entry in registry["checkpoints"]:
        checkpoint_id = entry["id"]
        for dependency in entry.get("depends_on", []):
            if dependency not in seen:
                raise NonTopologicalRegistryOrderError(
                    f"checkpoint {checkpoint_id!r} depends on {dependency!r}, which does "
                    f"not appear earlier in the registry's checkpoints array"
                )
        seen.add(checkpoint_id)


def validate_registry_mapping_coverage(registry: dict, mapping: dict) -> None:
    """D3's validator-reject rule (resolves OPUS-R10-006): every
    requirement names at least one real checkpoint, and every checkpoint
    owns at least one requirement."""
    checkpoint_ids = {entry["id"] for entry in registry["checkpoints"]}
    owned: set[str] = set()
    for requirement_id, requirement in mapping.get("requirements", {}).items():
        for checkpoint_id in requirement.get("checkpoint_ids", []):
            if checkpoint_id not in checkpoint_ids:
                raise UnmappedRequirementError(
                    f"{requirement_id} names checkpoint {checkpoint_id!r}, absent from the registry"
                )
            owned.add(checkpoint_id)
    unowned = checkpoint_ids - owned
    if unowned:
        raise UnownedCheckpointError(f"checkpoints owned by no requirement: {sorted(unowned)}")


def render_registry_markdown(registry: dict) -> str:
    """A generated, human-readable view of the registry JSON (D-Registry).
    Never itself hashed -- reformatting this output must never change
    `review_content_id`, since only the JSON file is a protected path."""
    lines = ["| id | name | depends_on | complexity | session_target |",
             "| --- | --- | --- | --- | --- |"]
    for entry in registry["checkpoints"]:
        deps = ", ".join(entry.get("depends_on", [])) or "-"
        lines.append(
            f"| {entry['id']} | {entry['name']} | {deps} | "
            f"{entry['complexity']} | {entry['session_target']} |"
        )
    return "\n".join(lines) + "\n"


def generate_registry(work_item_id: str, plan_revision: int, checkpoints: list[dict]) -> dict:
    """Builds the registry JSON structure (D-Registry) and validates
    D-Selection point 3 (topological order) before returning -- a bad
    order fails at generation time, never silently written."""
    validate_work_item_id(work_item_id)
    registry = {
        "schema_version": SCHEMA_VERSION,
        "work_item_id": work_item_id,
        "plan_revision": plan_revision,
        "checkpoints": checkpoints,
    }
    validate_registry_topological_order(registry)
    return registry


def generate_mapping(work_item_id: str, requirements: dict, *, registry: dict) -> dict:
    """Builds the requirements-mapping JSON (D4b) and validates
    bidirectional registry x mapping coverage before returning (resolves
    `OPUS-R10-006`: the coverage query runs at generation time, not review
    time -- an unmapped requirement or unowned checkpoint fails here,
    before either file is ever written)."""
    validate_work_item_id(work_item_id)
    mapping = {
        "schema_version": SCHEMA_VERSION,
        "work_item_id": work_item_id,
        "requirements": requirements,
    }
    validate_registry_mapping_coverage(registry, mapping)
    return mapping


def write_registry_and_mapping(
    repo_root: Path, registry_path: Path, mapping_path: Path, registry: dict, mapping: dict,
) -> None:
    """The sole writer D-Registry names for either file: byte-for-byte, no
    independent reformatting (the exact lesson `OPUS-R8-002` already
    taught this design once). Re-validates immediately before writing as a
    fail-closed guard against a stale or hand-built argument, even though
    `generate_registry`/`generate_mapping` should already have validated
    their own output.

    **Checkpoint-id reuse** (new, revision 72, `OPUS-R89-005`): before
    writing, every checkpoint id in `registry` that is *not already
    present* in whatever registry currently sits at `registry_path` (none,
    for the first-ever write) is checked against `D-Checkpoint-Ownership`'s
    origination reference via `identity_reference_admits`. This is
    deliberately a **delta** check, scoped to ids genuinely new to this
    revision: an id kept live across revisions -- `workflow-v2-1-core`'s
    own `WF8b`, redefined across dozens of revisions -- is in-place
    redefinition, not reuse, and the plan states this boundary explicitly
    rather than refusing ordinary plan revision. An id that *is* new to
    this revision and decidably observed historically (renamed back,
    reintroduced after removal) refuses with `CheckpointIdReusedError`; an
    undecidable reference read refuses with `IdentityReferenceUndecidableError`,
    cleared only by `authorize_identity_reference_gap`."""
    validate_registry_topological_order(registry)
    validate_registry_mapping_coverage(registry, mapping)

    full_registry_path = repo_root / registry_path
    previous_ids: set[str] = set()
    if full_registry_path.exists():
        previous = json.loads(full_registry_path.read_text())
        previous_ids = {checkpoint["id"] for checkpoint in previous.get("checkpoints", [])}
    new_ids = [
        checkpoint["id"] for checkpoint in registry.get("checkpoints", [])
        if checkpoint["id"] not in previous_ids
    ]
    work_item_id = registry.get("work_item_id")
    for checkpoint_id in new_ids:
        identity_reference_admits(repo_root, work_item_id, checkpoint_id)

    # Salvage audit `O3`: this is the sole sanctioned writer of either
    # file, and `/milestone-plan` step 3 calls it before anything else has
    # necessarily created `docs/ai-workflow/registry/` or
    # `docs/ai-workflow/requirements/`. A repository adopting the workflow
    # for the first time otherwise gets a bare `FileNotFoundError` out of
    # the one step that is supposed to create these artifacts.
    full_mapping_path = repo_root / mapping_path
    full_registry_path.parent.mkdir(parents=True, exist_ok=True)
    full_mapping_path.parent.mkdir(parents=True, exist_ok=True)
    full_registry_path.write_text(json.dumps(registry, indent=2) + "\n")
    full_mapping_path.write_text(json.dumps(mapping, indent=2) + "\n")


# Salvage audit `B4`/`B5` (cluster `C3`): the default template's own
# classification vocabulary. Both stages inherit an already-reviewed set
# rather than an empty or hand-authored one -- the same reasoning the
# plan-stage half has always used -- but the implementation-stage half now
# actually names something, and both halves now name every path this
# workflow's own commands are *guaranteed* to write. Fail-closed is
# preserved throughout: these are closed sets, and a path under any
# directory they do not name still raises `UnclassifiedPathError`.

_WORKFLOW_MACHINERY_JUSTIFICATION = (
    "written by this workflow's own commands (state/config persistence, the "
    "registry/requirements artifacts, the functional-review checklist, the "
    "roadmap/milestone archival) -- excluded so the machinery's own "
    "bookkeeping writes can never stale this item's approvals"
)

# Every other work item's plan-stage content lives under here, alongside
# this workflow's own bookkeeping. Excluded at *both* stages in the
# generated template (salvage audit `B7`): a repository running two work
# items at once -- which `D1` names as the normal case, not the exception
# -- otherwise has to hand-enumerate every sibling's plan document in
# every new item's declaration, and a missed one fails the *reader's*
# classification closed, not the writer's. This item's own artifacts file
# is carved out by exact path in `implementation_stage.protected_paths`
# and checked first, so its self-protection is unaffected; this item's own
# plan/registry/mapping paths are `plan_stage.protected_paths` members and
# `classify_path` checks protected first, so they stay protected too.
WORKFLOW_DOCS_PREFIX = "docs/ai-workflow/"

_SIBLING_WORKFLOW_DOCS_JUSTIFICATION = (
    "another work item's own plan-stage content and this workflow's own "
    "bookkeeping -- owned, reviewed and bound by whichever item declares it, "
    "never this item's own reviewed content. A work item whose deliverable "
    "genuinely *is* a workflow design document under this prefix must move "
    "that exact path into implementation_stage.protected_paths during "
    "SELF_REVIEWING_PLAN, exactly as workflow-v2-1-core-artifacts.json does "
    "for MILESTONE_WORKFLOW.md and REVIEW_PROTOCOL.md (salvage audit B7)"
)

_PLAN_GOVERNED_JUSTIFICATION = (
    "plan-stage-governed content for this or another work item -- reviewed and "
    "bound at the plan stage, never an implementation deliverable in its own right"
)

_SHARED_TERRITORY_JUSTIFICATION = (
    "repository-wide territory neither work_item_type owns exclusively -- "
    "agent instructions, CI configuration, the agent-context inventory and "
    "the improvement backlog; a concurrent work item of either type may "
    "write here, so it is excluded for both rather than protected by one "
    "(salvage audit I8). An item whose deliverable genuinely is one of these "
    "must move that exact path into implementation_stage.protected_paths "
    "during SELF_REVIEWING_PLAN."
)

_OTHER_TYPE_JUSTIFICATION = (
    "outside this work item's own deliverable tree -- a concurrent work item of "
    "the other work_item_type may write here; excluded so it never stales this "
    "item's technical_approval (the implementation-stage counterpart of "
    "PRODUCT_SCOPE_JUSTIFICATION's own plan-stage reasoning)"
)

# Paths this workflow's own commands write, at either stage, for any work
# item: `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` (every state writer),
# `FUNCTIONAL_CHECKLIST_PATH` (`/prepare-functional-review`'s own mandatory
# evidence commit), `docs/ROADMAP.md` (`/accept-milestone` step 3).
_WORKFLOW_MACHINERY_PATHS = (
    "docs/ai-workflow/WORKFLOW_STATE.json",
    "docs/ai-workflow/WORKFLOW_CONFIG.json",
    FUNCTIONAL_CHECKLIST_PATH,
    "docs/ROADMAP.md",
)

_WORKFLOW_MACHINERY_PREFIXES = (
    "docs/ai-workflow/registry/",
    "docs/ai-workflow/requirements/",
    "docs/ai-workflow/archive/",
    "docs/milestones/",
)

# The deliverable tree each work_item_type owns at the implementation
# stage -- "the kind of content technical_approval binds to" for that type.
# `process`: this repository's own workflow tooling, exactly the pair
# `workflow-v2-1-core-artifacts.json` itself protects. `product`: the
# application source, its build configuration, and the product
# documentation a product milestone genuinely authors.
#
# Salvage audit `I8` (convergence pass 7): these two tables are the *only*
# per-type input to `_implementation_stage_default`, and the "other" type's
# entries become that type's exclusions by construction. Before this, the
# per-type branching was one-directional -- the `process` branch excluded a
# hand-listed slice of product documentation territory, and the `product`
# branch had no mirror at all -- so a product milestone editing
# `docs/UX_FLOWS.md`, `docs/DOMAIN_GLOSSARY.md`, `docs/adr/` or a
# repository-root build file reached `UnclassifiedPathError` at its first
# implementation bundle, after the plan-approval hard gate had already
# advanced durable state. Making the two types symmetric table lookups
# removes the whole class rather than adding a second hand-list.
IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES = MappingProxyType({
    "process": MappingProxyType({
        ".claude/commands/": "this repository's own workflow command files -- process deliverable",
        "scripts/": "this repository's own workflow tooling scripts -- process deliverable",
    }),
    "product": MappingProxyType({
        "app/": "application source and its own tests -- product deliverable",
        "gradle/": "build configuration for the application -- product deliverable",
        "config/": "static-analysis/build configuration for the application -- product deliverable",
        "docs/adr/":
            "architecture decision records for the application -- product design "
            "content a product milestone authors and a product technical review "
            "binds (salvage audit I8)",
    }),
})

# The exact-path half of the same per-type deliverable declaration. Kept
# separate from the prefix half because these are repository-root files and
# single documents: a prefix would swallow suffixed siblings, exactly the
# `OPUS-R8-006` reasoning `PLAN_STAGE_EXCLUDED_PATHS` already states. A
# root build file this table does not name still fails closed, by design.
IMPLEMENTATION_STAGE_DELIVERABLE_PATHS = MappingProxyType({
    "process": MappingProxyType({}),
    "product": MappingProxyType({
        "build.gradle.kts":
            "the application's own root build script -- product deliverable "
            "(salvage audit I8)",
        "settings.gradle.kts":
            "the application's own Gradle settings -- product deliverable "
            "(salvage audit I8)",
        "gradle.properties":
            "the application's own Gradle properties -- product deliverable "
            "(salvage audit I8)",
        "gradlew":
            "the application's own Gradle wrapper entry point -- product "
            "deliverable (salvage audit I8)",
        "gradlew.bat":
            "the application's own Gradle wrapper entry point -- product "
            "deliverable (salvage audit I8)",
        "docs/DOMAIN_GLOSSARY.md":
            "the application's own domain terminology and business rules -- "
            "product design content a product technical review binds "
            "(salvage audit I8)",
        "docs/UX_FLOWS.md":
            "the application's own navigation/UI/interaction flows -- product "
            "design content a product technical review binds (salvage audit I8)",
    }),
})

# Territory neither work_item_type owns exclusively: repository-wide agent
# instructions, CI configuration, the agent-context inventory, and the
# improvement backlog. A concurrent work item of *either* type may write
# here, so both types exclude it -- the same treatment `AGENTS.md`/
# `CLAUDE.md`/`README.md` already get below, and the third category
# alongside product-owned and process-owned. An item whose deliverable
# genuinely is one of these must move that exact path into
# `implementation_stage.protected_paths` during `SELF_REVIEWING_PLAN`, the
# same escape `WORKFLOW_DOCS_PREFIX` documents.
_SHARED_REPOSITORY_PREFIXES = (
    ".github/",
    "docs/agent-context/",
    "docs/improvements/",
)


# A third `work_item_type` added to `WORK_ITEM_TYPES` without a
# deliverable tree here would reach `_implementation_stage_default` as a
# bare `KeyError` instead of a named refusal -- the exact class the
# salvage audit's `I2` closed for the validators. Checked at import, the
# same way `workflow_fingerprint` validates its own exclusion constants.
# Both halves of the declaration are checked (salvage audit `I8`): the
# exact-path table is read for `work_item_type` *and* for the other type
# on every call, so a missing entry is exactly as fatal as a missing
# prefix entry.
assert set(IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES) == set(WORK_ITEM_TYPES), (
    "IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES must name exactly WORK_ITEM_TYPES"
)
assert set(IMPLEMENTATION_STAGE_DELIVERABLE_PATHS) == set(WORK_ITEM_TYPES), (
    "IMPLEMENTATION_STAGE_DELIVERABLE_PATHS must name exactly WORK_ITEM_TYPES"
)

# One type's protected deliverable is the other's exclusion, so a path or
# prefix claimed by both types would make the generated classification
# depend on dictionary insertion order rather than on the declaration.
# Checked at import rather than left to a test.
assert not (
    set(IMPLEMENTATION_STAGE_DELIVERABLE_PATHS["process"])
    & set(IMPLEMENTATION_STAGE_DELIVERABLE_PATHS["product"])
), "IMPLEMENTATION_STAGE_DELIVERABLE_PATHS entries must be owned by exactly one type"
assert not (
    set(IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES["process"])
    & set(IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES["product"])
), "IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES entries must be owned by exactly one type"
assert not (
    set(_SHARED_REPOSITORY_PREFIXES)
    & (set(IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES["process"])
       | set(IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES["product"]))
), "_SHARED_REPOSITORY_PREFIXES must not claim either type's deliverable tree"


def _implementation_stage_default(
    work_item_type: str, own_artifacts_path: str,
    plan_path: str, registry_path: str, mapping_path: str,
) -> dict:
    """The type-appropriate `implementation_stage` half of
    `generate_artifacts_declarations`' template (salvage audit `B4`).

    Before this, the generated half named nothing but the declarations
    file protecting itself -- `protected_prefixes`/`excluded_paths`/
    `excluded_prefixes` were all empty -- so the *first*
    implementation-stage computation for any freshly created work item
    raised `UnclassifiedPathError` on the item's own deliverable, after
    the plan-approval hard gate. The generator was also never given
    `work_item_type`, so it could not have emitted a type-appropriate set
    even in principle.

    What the template classifies, and deliberately what it does not:

    - **protected**: this file itself (unchanged self-protection), plus
      the deliverable tree this `work_item_type` owns
      (`IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES`).
    - **excluded**: every path this workflow's own commands are
      *guaranteed* to write for any work item (`_WORKFLOW_MACHINERY_*`),
      this item's own three plan-stage declaration paths, the repository
      housekeeping/product-brief documents the plan stage's own closed set
      already names, and the *other* `work_item_type`'s deliverable tree
      and documentation territory.
    - **neither**: everything else, which still fails closed via
      `UnclassifiedPathError` -- any path under a directory the template
      does not name at all.

    The one judgment the template *does* make rather than defer is
    `docs/ai-workflow/` (salvage audit `B7`): excluded at both stages,
    because everything under it is either this workflow's own bookkeeping
    or *another* work item's plan-stage content, owned and bound by
    whichever item declares it. Leaving it unclassified instead was tried
    and is worse: it makes two concurrent work items -- `D1`'s normal
    case -- impossible without hand-enumerating every sibling's plan
    document in every new declaration, which is exactly what every real
    work item in this repository ended up doing, one of them missing a
    sibling and being caught only at plan review. A `process` item whose
    deliverable genuinely *is* a workflow design document under this
    prefix must move that exact path into
    `implementation_stage.protected_paths` during `SELF_REVIEWING_PLAN`,
    the way `workflow-v2-1-core-artifacts.json` does for
    `MILESTONE_WORKFLOW.md` and `REVIEW_PROTOCOL.md`."""
    other_type = "product" if work_item_type == "process" else "process"
    protected_prefixes = dict(IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES[work_item_type])

    excluded_paths = {path: _WORKFLOW_MACHINERY_JUSTIFICATION for path in _WORKFLOW_MACHINERY_PATHS}
    for path in (plan_path, registry_path, mapping_path):
        excluded_paths[path] = _PLAN_GOVERNED_JUSTIFICATION
    # Repository housekeeping and top-level product-brief documents any
    # concurrent work item may write -- the same closed set the plan stage
    # already names, restated here rather than derived from it (the two
    # stages' sets are near-inverses and must never be adapted from one
    # another, `OPUS-R20-003`).
    for path in (
        ".gitignore", "AGENTS.md", "CLAUDE.md", "README.md",
        "docs/PROJECT_BRIEF.md", "docs/TECHNICAL_DECISIONS.md",
    ):
        excluded_paths.setdefault(path, _OTHER_TYPE_JUSTIFICATION)
    # The other type's own exact-path deliverables (salvage audit `I8`) --
    # the mirror of the prefix-shaped rule below, and symmetric for both
    # types rather than hand-listed for one of them.
    for path in IMPLEMENTATION_STAGE_DELIVERABLE_PATHS[other_type]:
        excluded_paths.setdefault(path, _OTHER_TYPE_JUSTIFICATION)

    excluded_prefixes = {
        prefix: _WORKFLOW_MACHINERY_JUSTIFICATION for prefix in _WORKFLOW_MACHINERY_PREFIXES
    }
    for prefix in IMPLEMENTATION_STAGE_DELIVERABLE_PREFIXES[other_type]:
        excluded_prefixes[prefix] = _OTHER_TYPE_JUSTIFICATION
    for prefix in _SHARED_REPOSITORY_PREFIXES:
        excluded_prefixes.setdefault(prefix, _SHARED_TERRITORY_JUSTIFICATION)
    excluded_prefixes.setdefault(WORKFLOW_DOCS_PREFIX, _SIBLING_WORKFLOW_DOCS_JUSTIFICATION)
    # `workflow-2.5.0` CP9 (`v2.4.0-001`'s own implementation-stage symmetry
    # widening, docs/defects/v2.4.0-001-workflow-manager-installation-record-
    # unclassified-at-plan-stage.md): the `2.4.0` fix above widened only the
    # *plan*-stage default; a freshly generated declarations file's
    # implementation-stage half stayed silent on
    # `.workflow-manager/installation.json`, so an `update()`-driven commit
    # to it landing inside a live item's own implementation-stage interval
    # still raised `UnclassifiedPathError`, exactly as the plan-stage gap
    # once did. Excluded here, in the generated template (the same "widen
    # the template, not an existing item's own already-generated
    # declarations file" rule the plan-stage fix already established): no
    # existing work item's own declarations file changes, so no existing
    # approval's identity moves. Forward-only -- this does not by itself
    # repair a work item whose own declarations file predates this fix; see
    # the defect record for that residual and its operator mitigations.
    excluded_prefixes.setdefault(
        ".workflow-manager/",
        "workflow_manager's own installation-record bookkeeping (which release is "
        "installed, managed/generated/merged file digests) -- tooling identity, "
        "never this or any other work item's own implementation-stage deliverable "
        "(workflow-2.5.0 CP9, symmetric with the plan-stage exclusion above)",
    )

    # The item's own declarations file is carved out by exact path and
    # checked first by `classify_path_implementation_stage`, so the
    # `docs/ai-workflow/registry/` exclusion above never weakens its
    # self-protection.
    protected_paths = {
        own_artifacts_path:
            "this file's own concrete, spelled-out path -- an editable-under-"
            "no-approval declarations file would let someone widen an "
            "exclusion and re-bless the resulting digest in the same "
            "session, with no gate ever having seen the classification "
            "change (OPUS-R25-006, OPUS-R26-004)",
    }
    # This type's own exact-path deliverables, added *after* the
    # self-protection entry and never overwriting it: `setdefault` keeps
    # the declarations file's own justification intact even if a future
    # table entry ever collided with it (salvage audit `I8`).
    for path, justification in IMPLEMENTATION_STAGE_DELIVERABLE_PATHS[work_item_type].items():
        protected_paths.setdefault(path, justification)
    return {
        "protected_paths": protected_paths,
        "protected_prefixes": protected_prefixes,
        "excluded_paths": excluded_paths,
        "excluded_prefixes": excluded_prefixes,
    }


def generate_artifacts_declarations(
    work_item_id: str, plan_path: str, registry_path: str, mapping_path: str,
    *, work_item_type: str,
) -> dict:
    """The default `<work_item_id>-artifacts.json` template
    (`D-Fingerprint-Generalization`, `OPUS-R25-004`): the item's own three
    artifact paths as `plan_stage.protected_paths` (satisfying
    `resolve_plan_stage_metadata`'s path-to-role binding check by
    construction at creation time), `workflow-v2-1-core`'s *current*
    `excluded_paths`/`excluded_prefixes` inherited verbatim as a starting
    point (not because they are correct for every future item unmodified,
    but because an inherited, previously-reviewed set fails closed on any
    genuinely novel path exactly as before, and is strictly safer than an
    empty or hand-authored one -- `SELF_REVIEWING_PLAN` must confirm the
    inherited set actually fits the new item's own plan). Also emits the
    file's own concrete, self-referential `implementation_stage.protected_paths`
    entry (`OPUS-R25-006`/`OPUS-R26-004`) so the file protects itself under
    `technical_approval` by construction, the same pattern
    `workflow-v2-1-core-artifacts.json`'s own migrated entry uses.

    **`work_item_type` is required** (salvage audit `B4`): the
    implementation-stage half is type-specific -- a `process` item's
    deliverable tree is this repository's own workflow tooling, a
    `product` item's is the application source -- and the generator
    previously never saw the type at all, so it emitted an
    implementation-stage classification that named nothing but its own
    file and failed closed on the item's own deliverable at the first
    implementation-stage computation. See `_implementation_stage_default`.

    **Plan-stage completeness** (salvage audit `B5`): the inherited
    `PLAN_STAGE_EXCLUDED_PATHS` was authored as the complement of
    `fingerprint.PLAN_STAGE_PROTECTED`, but this template's own
    `protected_paths` is a *different, smaller* set -- the item's own
    three declaration paths. The three documents `PLAN_STAGE_PROTECTED`
    names therefore landed in neither set and failed closed the moment any
    of them changed, including `docs/TECHNICAL_DECISIONS.md`, which
    `/milestone-plan` step 5 explicitly directs the planner to engage
    with. They are excluded here, by exact path, as another work item's
    plan-stage content -- unless this item's own `plan_path`/
    `registry_path`/`mapping_path` names one of them, in which case
    `classify_path`'s protected-first ordering keeps it protected."""
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    own_artifacts_path = str(fingerprint.artifacts_path_for_work_item(work_item_id).as_posix())
    own_declaration_paths = {plan_path, registry_path, mapping_path}

    plan_stage_excluded_paths = dict(fingerprint.PLAN_STAGE_EXCLUDED_PATHS)
    plan_stage_excluded_prefixes = dict(fingerprint.PLAN_STAGE_EXCLUDED_PREFIXES)
    plan_stage_excluded_prefixes.setdefault(
        WORKFLOW_DOCS_PREFIX, _SIBLING_WORKFLOW_DOCS_JUSTIFICATION,
    )
    # CP8 (`plan-amendment-mechanism`), disposable-repository update-path
    # validation surfaced this: `workflow_manager.install`'s own
    # `.workflow-manager/installation.json` record (never Workflow-
    # distributed content, and unknown to `fingerprint.PLAN_STAGE_EXCLUDED_
    # PREFIXES`, whose inherited set predates this tool entirely) changes on
    # every `update()`, so an `update()` landing inside any in-flight work
    # item's plan-approval..HEAD interval otherwise makes plan-stage
    # `review_content_id` recomputation -- `implementing_entry_reachable`
    # included -- raise `UnclassifiedPathError` rather than cleanly
    # excluding a path this item never wrote. Excluded here, in the
    # generated template (same "widen the template, not the frozen
    # constant" rule salvage audit I7/I8 already established above), so no
    # existing declaration file changes and no existing approval's identity
    # moves. This does not by itself repair a work item whose own
    # declarations file was already generated before this fix landed (any
    # work item created under `2.3.1`, or under an earlier `2.4.0` overlay
    # revision) -- see docs/defects/v2.4.0-001-workflow-manager-installation-
    # record-unclassified-at-plan-stage.md for that residual, pre-existing-
    # item gap and why it is not retroactively repaired here.
    plan_stage_excluded_prefixes.setdefault(
        ".workflow-manager/",
        "workflow_manager's own installation-record bookkeeping (which release is "
        "installed, managed/generated/merged file digests) -- tooling identity, "
        "never this or any other work item's own plan-stage content",
    )
    for path in sorted(fingerprint.PLAN_STAGE_PROTECTED):
        if path in own_declaration_paths:
            continue
        plan_stage_excluded_paths.setdefault(
            path,
            "another work item's own plan-stage protected design content, not this "
            "item's -- excluded, not unmentioned, so a concurrent edit to it never "
            "leaves this item's own plan-stage classification unresolvable "
            "(salvage audit B5)",
        )
    # The repository-root build files (salvage audit `I8`). The inherited
    # `PLAN_STAGE_EXCLUDED_PATHS` is `workflow-v2-1-core`'s own frozen
    # constant and names none of them, so they were unclassified at the
    # *plan* stage too, for both work-item types -- and the plan-stage
    # projection is recomputed all the way through implementation
    # (`implementing_entry_reachable`, `/accept-milestone`'s registry
    # coverage check), so a product milestone that bumps `build.gradle.kts`
    # mid-implementation failed its own plan-stage freshness check closed.
    # Excluded here, in the generated template, rather than by widening the
    # frozen constant: no existing declaration file changes, so no existing
    # approval's identity moves (salvage audit `I7`'s own rule).
    for path in sorted(IMPLEMENTATION_STAGE_DELIVERABLE_PATHS["product"]):
        if path in own_declaration_paths or path in fingerprint.PLAN_STAGE_PROTECTED:
            continue
        plan_stage_excluded_paths.setdefault(
            path,
            "product build configuration and product documentation -- implementation-"
            "stage content owned by whichever product work item declares it, never "
            "plan-stage design content for this item (salvage audit I8)",
        )

    return {
        "schema_version": 2,
        "work_item_id": work_item_id,
        "plan_stage": {
            "protected_paths": sorted(own_declaration_paths),
            "excluded_paths": plan_stage_excluded_paths,
            "excluded_prefixes": plan_stage_excluded_prefixes,
        },
        "implementation_stage": _implementation_stage_default(
            work_item_type, own_artifacts_path, plan_path, registry_path, mapping_path,
        ),
    }


# ---------------------------------------------------------------------------
# D1: work-item routing (create-or-resume) and completion/reset
# ---------------------------------------------------------------------------


def default_work_item(
    *, work_item_id: str, work_item_type: str, work_item_kind: str,
    plan_path: str, registry_path: str | None, governing_workflow_version: str,
    plan_revision: int, last_transition: str,
    mapping_path: str | None = None, base_commit: str | None = None,
) -> dict:
    """D3's full per-item field list, defaulted for a freshly created work
    item (D1). `governing_workflow_version` is the caller's concern to fix
    from the then-current repository-level default -- this function never
    reads config itself (D-Self-Governance: fixed at creation, never
    re-read afterward). `mapping_path`/`base_commit` (`D-Fingerprint-
    Generalization`) are the third and fourth declaration facts a process
    work item needs to fingerprint its own plan-stage content -- the same
    mechanism as `plan_path`/`registry_path`, not a new one."""
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    validate_work_item_kind(work_item_kind)
    return {
        "work_item_type": work_item_type,
        "work_item_kind": work_item_kind,
        "work_item_id": work_item_id,
        "parent_work_item_id": None,
        "plan_path": plan_path,
        "registry_path": registry_path,
        "mapping_path": mapping_path,
        "governing_workflow_version": governing_workflow_version,
        "phase": "PLANNING",
        "plan_revision": plan_revision,
        "implementation_revision": None,
        "functional_review_round": None,
        "base_commit": base_commit,
        "reviewed_implementation_head": None,
        "current_checkpoint_id": None,
        "last_completed_checkpoint_id": None,
        "checkpoints": {},
        "current_bundle_id": None,
        "plan_approval": None,
        "technical_approval": None,
        "technical_review_block_pins": [],
        "plan_review_stages": None,
        "functional_acceptance_status": None,
        "blocking_decisions": [],
        "state_revision": 1,
        "last_transition": last_transition,
    }


def route_work_item(
    state: dict, config: dict, *, work_item_id: str, work_item_type: str,
    work_item_kind: str, plan_path: str, registry_path: str,
    plan_revision: int, now: str,
    mapping_path: str | None = None, base_commit: str | None = None,
    repo_root: Path | None = None,
) -> dict:
    """D1's routing text, in full: `/milestone-plan` creates or updates the
    item under `work_items[id]`. Returns a new state dict (does not mutate
    the input, so a caller can inspect the pre-route state on failure).

    - A fresh id creates a new `work_items[id]` entry, fixing
      `governing_workflow_version` from the config's current
      `default_workflow_version` (validated against `supported_versions`,
      `OPUS-R6-024`) and never re-read afterward.
    - An id naming an existing *non-terminal* entry resumes it: only
      `plan_revision`/`state_revision`/`last_transition` advance
      unconditionally -- identity fields (`work_item_type`/`kind`/
      `governing_workflow_version`/`parent_work_item_id`) are immutable
      after creation. **`mapping_path`/`base_commit`, and now
      `plan_path`/`registry_path` too, on the resume branch** (new,
      `D-Fingerprint-Generalization`, `OPUS-R28-002`): for each of the
      four declaration facts, independently, if the stored value is
      `null` and a non-null argument is supplied here, it is written; if
      the stored value is non-null and the supplied argument disagrees,
      `WorkItemDeclarationFactConflictError` (a declaration fact is
      immutable once set); if no argument is supplied for a field, that
      field is left untouched. This makes the resume branch usable for a
      pre-declared, non-terminal entry that has some or all of these four
      facts still `null` (e.g. `v2-1-dry-run`, created with only
      `plan_path` set) -- the same call this function's caller makes for
      a fresh id also works, idempotently, for a resumed one.
    - An id naming an existing *terminal* entry is a hard error: ids are
      not reused after `MILESTONE_COMPLETE`.
    - `active_work_item_id` is set to this id only if no other item is
      currently active (or this item already is) -- routing never steals
      focus from unrelated in-flight, non-terminal work (D1's "resume-focus
      pointer, not an execution lock").
    - **A fresh id is additionally checked against `D-Checkpoint-Ownership`'s
      origination reference** (new, revision 71, `OPUS-R88-004`): an id
      that has ever appeared there is permanently non-reusable, refused
      with `WorkItemIdReusedError` -- a stronger, historical check than
      the terminal-phase one above, which only ever sees *live* state.
      This check runs only when `repo_root` is supplied; a caller that
      omits it gets the unchanged, repo-independent routing this function
      always had (an in-memory/testing convenience, never the production
      call site's own path). An undecidable reference read raises
      `IdentityReferenceUndecidableError`, cleared only by the explicit
      `authorize_identity_reference_gap`.
    """
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    validate_work_item_kind(work_item_kind)

    new_state = copy.deepcopy(state)
    work_items = new_state.setdefault("work_items", {})
    existing = work_items.get(work_item_id)

    if existing is not None and existing.get("phase") in TERMINAL_PHASES:
        raise WorkItemTerminalReuseError(
            f"{work_item_id!r} already exists at a terminal phase "
            f"({existing.get('phase')!r}) -- use a new work_item_id"
        )

    if existing is None:
        if repo_root is not None:
            identity_reference_admits(repo_root, work_item_id, None)
        validate_governing_version(config["default_workflow_version"], config)
        work_items[work_item_id] = default_work_item(
            work_item_id=work_item_id, work_item_type=work_item_type,
            work_item_kind=work_item_kind, plan_path=plan_path,
            registry_path=registry_path,
            governing_workflow_version=config["default_workflow_version"],
            plan_revision=plan_revision, last_transition=now,
            mapping_path=mapping_path, base_commit=base_commit,
        )
    else:
        for field_name, supplied in (
            ("plan_path", plan_path), ("registry_path", registry_path),
            ("mapping_path", mapping_path), ("base_commit", base_commit),
        ):
            if supplied is None:
                continue
            stored = existing.get(field_name)
            if stored is None:
                existing[field_name] = supplied
            elif stored != supplied:
                raise WorkItemDeclarationFactConflictError(
                    f"{work_item_id}.{field_name} is already {stored!r}, "
                    f"cannot set to {supplied!r}"
                )
        existing["plan_revision"] = plan_revision
        existing["state_revision"] = existing.get("state_revision", 1) + 1
        existing["last_transition"] = now

    active = new_state.get("active_work_item_id")
    if active is None or active == work_item_id:
        new_state["active_work_item_id"] = work_item_id
    # else: a different item is active and non-terminal -- left alone;
    # repointing active_work_item_id is always an explicit, separate act.

    return new_state


def publish_plan_revision(state: dict, work_item_id: str, plan_revision: int, now: str) -> dict:
    """`D-Plan-Revision-Publication`'s single sanctioned writer of a
    plan-revision bump (`WFR-65`): the operation that writes a new
    `plan_revision` into a work item's registry must, in the same
    operation and before any bundle is generated, publish that same value
    to `WORKFLOW_STATE.json`'s non-authoritative mirror through this one
    entry point -- never as a plain JSON edit. Sets `plan_revision` to the
    given value and `phase` to `AWAITING_EXTERNAL_PLAN_REVIEW` for a
    `"1"`-governed item or `AWAITING_LOCAL_PLAN_REVIEW` for a
    `TWO_STAGE_PLAN_REVIEW_VERSIONS`-governed (`"2.1"`/`"2.2"`) one
    (`D-Plan-Review-Stages` enters local review first; workflow-2.5.0
    widens this from a bare `"2.1"` literal to the membership constant --
    a `"2.2"` item is a `"2.1"` item for plan review purposes).

    Exhaustive call sites (named, not left to convention): `/milestone-plan`
    step 3's `[2.1]` registry write; `/apply-plan-review` step 5, on both
    branches, whenever the revision counter advances as part of applying
    feedback; `/bootstrap-workflow-v2`'s step 1 state-sync, for a
    self-discovered revision of the permanently-`"1"`-governed
    `workflow-v2-1-core` opened while `IMPLEMENTING`.

    Idempotent: re-running with the same `plan_revision` and the resulting
    phase already reached is a true no-op -- no `state_revision`/
    `last_transition` bump -- so an interrupted revision is retried rather
    than repaired. Refuses a terminal-phase item
    (`TerminalPlanRevisionPublicationError`) and, by construction, touches
    no `work_items` entry other than `work_item_id`'s own."""
    work_item = state["work_items"][work_item_id]
    if work_item.get("phase") in TERMINAL_PHASES:
        raise TerminalPlanRevisionPublicationError(
            f"{work_item_id!r} is at terminal phase {work_item.get('phase')!r} -- "
            f"a plan revision can never be published against a completed work item"
        )
    governing_version = work_item.get("governing_workflow_version")
    if governing_version in TWO_STAGE_PLAN_REVIEW_VERSIONS:
        target_phase = "AWAITING_LOCAL_PLAN_REVIEW"
    elif governing_version == "1":
        target_phase = "AWAITING_EXTERNAL_PLAN_REVIEW"
    else:
        raise UnsupportedGoverningVersionError(
            f"{work_item_id!r} has governing_workflow_version {governing_version!r}, "
            f"expected \"1\" or one of {sorted(TWO_STAGE_PLAN_REVIEW_VERSIONS)}"
        )

    if work_item.get("plan_revision") == plan_revision and work_item.get("phase") == target_phase:
        return state

    new_state = copy.deepcopy(state)
    new_work_item = new_state["work_items"][work_item_id]
    new_work_item["plan_revision"] = plan_revision
    new_work_item["phase"] = target_phase
    new_work_item["state_revision"] = new_work_item.get("state_revision", 1) + 1
    new_work_item["last_transition"] = now
    return new_state


def incomplete_children(state: dict, work_item_id: str) -> list[str]:
    """D-Functional-Remediation: every work item naming `work_item_id` as
    its own `parent_work_item_id` and not yet at a terminal phase --
    reverse lookup over `work_items`, no new field needed on the parent
    (the plan's own stated approach). Returns ids in insertion order,
    empty if there are no children or all of them are `MILESTONE_COMPLETE`."""
    return [
        child_id for child_id, child in state.get("work_items", {}).items()
        if child.get("parent_work_item_id") == work_item_id
        and child.get("phase") not in TERMINAL_PHASES
    ]


def registry_completion_status(work_item: dict, registry: dict) -> tuple[bool, str | None]:
    """The sole place the terminal/non-terminal question is answered --
    every other function in this section consumes its result rather than
    re-deriving it. Returns
    `(is_terminal, outstanding_checkpoint_id)`: `(True, None)` when
    `select_next_checkpoint` reports every registry checkpoint already
    `COMPLETE`; `(False, <checkpoint_id>)` otherwise, whether the next
    checkpoint is simply unselected yet (`select_next_checkpoint` returns
    an id) or blocked (`NoCheckpointReadyError`, whose own structured
    `checkpoint_id` attribute is read directly rather than parsed from its
    message text)."""
    try:
        next_checkpoint_id = select_next_checkpoint(work_item, registry)
    except NoCheckpointReadyError as exc:
        return False, exc.checkpoint_id
    if next_checkpoint_id is None:
        return True, None
    return False, next_checkpoint_id


def milestone_complete_gate_reachable(*, phase: str, is_terminal: bool) -> bool:
    """`/accept-milestone`'s gate function, mirroring
    `approval_gate_reachable`/`technical_approval_gate_reachable`'s
    existing non-circular pattern (D-States): `True` iff `is_terminal` and
    `phase` is `AWAITING_FUNCTIONAL_REVIEW` or `AWAITING_USER_ACCEPTANCE`
    (the latter included for forward compatibility only -- no function in
    this codebase writes it, a pre-existing gap this decision does not
    attempt to close)."""
    return is_terminal and phase in ("AWAITING_FUNCTIONAL_REVIEW", "AWAITING_USER_ACCEPTANCE")


def resolve_own_registry_completion_status(repo_root: Path, work_item: dict) -> tuple[bool, str | None]:
    """The single place a work item's own registry is resolved and loaded
    to answer `registry_completion_status` (`D-Scoped-Remediation-
    Acceptance`, revision 24, `GPT-R37-001`): `complete_work_item`'s
    authoritative gate and `/accept-milestone`'s/`/accept-scoped-
    remediation`'s own advisory pre-flight reads all call this one
    function rather than each re-deriving the resolution/loading logic.

    `work_item["registry_path"]` is `None` (a registry-less item, e.g. the
    legacy `milestone-8` shape): no load is attempted, vacuously terminal
    -- `(True, None)`.

    Otherwise the path is resolved via the same shared tracked-metadata
    safe-path validator `D3`'s whole-state mirror check already uses
    (`fingerprint._validate_plan_stage_metadata_path`), read, and parsed as
    JSON. A failure at safe-path resolution, existence/readability, or
    JSON-object parsing raises `RegistryCoverageError`, naming the work
    item and its declared `registry_path` and the specific failure. The
    loaded registry's own `work_item_id` field disagreeing with the work
    item actually being resolved is the same `RegistryCoverageError`,
    naming both the expected and the foreign registry's declared id --
    since the registry was loaded from the item's own declared
    `registry_path`, not supplied by any caller, this case can now only
    mean the on-disk file itself is misconfigured or cross-linked, never a
    caller-side substitution (closing the trust gap `GPT-R37-001` found in
    revision 23's caller-supplied-dict design).

    Before the loaded registry's completion status is trusted, its live
    bytes must also be exactly the ones the current `plan_approval`
    covers (revision 27, `GPT-R43-002`) -- `_assert_registry_covered_by_
    current_plan_approval` raises `StalePlanApprovalRegistryReadError`
    otherwise, whether the registry is a dirty tracked edit made after
    approval or a clean, committed-but-unapproved mutation. `RegistryCoverageError`
    alone (the checks above) only proves the file is safe/well-formed/
    self-declaring, never that it is the approved one."""
    registry_data = _load_authoritative_registry_or_none(repo_root, work_item)
    if registry_data is None:
        return True, None
    return registry_completion_status(work_item, registry_data)


def _load_authoritative_registry_or_none(
    repo_root: Path, work_item: dict, *, require_plan_approval_coverage: bool = True,
) -> dict | None:
    """The loading half of `resolve_own_registry_completion_status`,
    factored out so `resolve_completion_obligations` (item 356's own "no
    registry parameter" requirement) can resolve the same authoritative
    registry through the identical safe-path/existence/JSON-object/
    self-declared-id/plan-approval-coverage checks rather than trusting a
    caller-supplied dict -- the same trust boundary `GPT-R37-001` already
    removed from `complete_work_item` one layer up. `None` means a
    registry-less work item (e.g. the legacy `milestone-8` shape), never
    a load failure -- a load failure always raises.

    `require_plan_approval_coverage` (IMPL3-R1): the plan-approval-coverage
    check (`_assert_registry_covered_by_current_plan_approval`) is a
    completion-accounting trust boundary that belongs to
    `resolve_own_registry_completion_status`/`resolve_completion_obligations`
    -- both of the other two call sites, which need a proof the registry
    bytes are the approved ones before trusting them for terminality.
    `/request-plan-amendment`'s own `request_plan_amendment` needs only the
    registry's self-declared checkpoint *ids* for its early anchor-shape
    check (`AmendmentCheckpointIdShapeError`), never a coverage proof --
    that command's own design (`D-Plan-Amendment-1`, `B-R12-1`) is to
    *not* refuse on approval-manifest staleness, since an amendment is
    precisely what is about to supersede and replace it. Passing `False`
    here skips only that one assertion; safe-path resolution, existence,
    JSON-object shape, and self-declared `work_item_id` are still checked
    unconditionally for every caller."""
    work_item_id = work_item["work_item_id"]
    registry_path = work_item.get("registry_path")
    if registry_path is None:
        return None

    try:
        fingerprint._validate_plan_stage_metadata_path(
            repo_root, "registry_path", registry_path, at_commit=None,
        )
    except fingerprint.InvalidPlanStageMetadataPathError as exc:
        raise RegistryCoverageError(
            f"work_items[{work_item_id!r}].registry_path {registry_path!r} failed "
            f"safe-path resolution: {exc}"
        ) from exc

    registry_full = repo_root / registry_path
    try:
        registry_bytes = registry_full.read_text()
    except OSError as exc:
        raise RegistryCoverageError(
            f"work_items[{work_item_id!r}].registry_path {registry_path!r} does not "
            f"exist or is unreadable at {registry_full}"
        ) from exc
    try:
        registry_data = json.loads(registry_bytes)
    except json.JSONDecodeError as exc:
        raise RegistryCoverageError(
            f"work_items[{work_item_id!r}].registry_path {registry_path!r} is not "
            f"valid JSON: {exc}"
        ) from exc
    if not isinstance(registry_data, dict):
        raise RegistryCoverageError(
            f"work_items[{work_item_id!r}].registry_path {registry_path!r} does not "
            f"contain a JSON object (found {type(registry_data).__name__})"
        )

    registry_work_item_id = registry_data.get("work_item_id")
    if registry_work_item_id != work_item_id:
        raise RegistryCoverageError(
            f"work_items[{work_item_id!r}].registry_path {registry_path!r} declares "
            f"work_item_id {registry_work_item_id!r}, expected {work_item_id!r}"
        )

    if require_plan_approval_coverage:
        _assert_registry_covered_by_current_plan_approval(repo_root, work_item, registry_path)

    return registry_data


def _assert_registry_covered_by_current_plan_approval(
    repo_root: Path, work_item: dict, registry_path: str,
) -> None:
    """`GPT-R43-002`'s own fix: the registry bytes
    `resolve_own_registry_completion_status` is about to trust for
    terminality must be exactly the bytes `plan_approval` covers, not
    merely a safely-resolvable, well-formed, self-declaring tracked file.
    Reuses `plan_approval.review_content_manifest`'s own per-path
    exists/mode/blob record (already the durable, approval-time snapshot
    every plan approval writes) rather than recomputing a whole fresh
    plan-stage projection with a guessed `base_commit` -- this work
    item's own continued-scope rounds compute that projection against the
    plan-approval commit, not `work_item["base_commit"]`
    (`REVIEW_REQUEST.md`'s own documented convention), so there is no
    single `base_commit` value this helper could safely assume; a direct
    per-path snapshot comparison needs none.

    Items 231/232 (`WF8c`): checks *every* path named in the manifest,
    not merely `registry_path` -- a different plan-stage protected
    document (the plan, the mapping, `TECHNICAL_DECISIONS.md`, the audit
    doc) carrying a dirty or committed-but-never-approved mutation must
    refuse registry-derived completion exactly as a tampered registry
    itself would, even though the registry's own bytes are unchanged.

    **The real runtime guard against a malformed persisted `review_content_
    manifest`** (`workflow-v2-3-followups` continued scope, external
    cross-model review round 4, `OPUS-R25-007`'s own precedent): this is
    the exact function `/accept-milestone` reaches through
    `resolve_own_registry_completion_status`, over the
    *live* `WORKFLOW_STATE.json` -- `validate_state`'s own shape check
    is never on this call path, so the guard below, not that one, is what
    actually stops a malformed manifest from being iterated as a list of
    dicts and crashing with a bare `AttributeError`."""
    work_item_id = work_item["work_item_id"]
    plan_approval = work_item.get("plan_approval")
    if plan_approval is None or plan_approval.get("status") != "CURRENT":
        raise StalePlanApprovalRegistryReadError(
            f"work_items[{work_item_id!r}] has no CURRENT plan_approval -- "
            f"registry-derived completion cannot be trusted"
        )
    shape_error = _describe_malformed_review_content_manifest_shape(
        plan_approval.get("review_content_manifest")
    )
    if shape_error is not None:
        raise StalePlanApprovalRegistryReadError(
            f"work_items[{work_item_id!r}].plan_approval.review_content_manifest is "
            f"malformed, cannot be trusted for registry-derived completion: {shape_error}"
        )
    manifest = plan_approval.get("review_content_manifest") or []
    approved_entry = next(
        (entry for entry in manifest if entry.get("path") == registry_path), None,
    )
    if approved_entry is None:
        raise StalePlanApprovalRegistryReadError(
            f"work_items[{work_item_id!r}].registry_path {registry_path!r} is not "
            f"named in the current plan_approval.review_content_manifest -- cannot "
            f"prove the read registry bytes are plan-approved"
        )
    for entry in manifest:
        path = entry.get("path")
        live_snapshot = fingerprint._snapshot_worktree(repo_root, path)
        approved_snapshot = {
            "exists": entry.get("exists"), "mode": entry.get("mode"), "blob": entry.get("blob"),
        }
        if live_snapshot != approved_snapshot:
            raise StalePlanApprovalRegistryReadError(
                f"work_items[{work_item_id!r}]'s plan-stage protected path {path!r} "
                f"live state {live_snapshot!r} does not match the current "
                f"plan_approval's recorded manifest entry {approved_snapshot!r} -- "
                f"registry-derived completion cannot be trusted while any plan-stage "
                f"protected document carries unapproved content, even when "
                f"{registry_path!r} itself is unchanged"
            )


# ---------------------------------------------------------------------------
# D-Completion-Obligations (missing-test items 356/357/358/360/361, new
# revision 59-62): completion-blocking obligations whose verdict is
# *derived by execution*, by an independently technical-approval-authorized
# verifier, against an immutable Git-object content identity -- never read
# from a cached evidence record. `WF8b` declares exactly one this revision:
# `WFO-STATE-SERIALIZATION`, item 354's state-writer-serialization
# invariant.
# ---------------------------------------------------------------------------


class StateWriterDeclarationError(Exception):
    """A file on a declared state-writer surface (`.claude/commands/**`,
    `scripts/**` excluding `*_test.py`) has a missing, self-contradictory,
    or otherwise unparseable `state_writer` declaration -- item
    354(c)/357(g): this is a conformance failure, never a default, so an
    undeclared new surface member cannot silently pass as a non-writer."""


class VerifierClosureUnresolvableError(Exception):
    """The `WFO-STATE-SERIALIZATION` verifier's static import-closure
    analysis could not follow a construct (`importlib.import_module`, a
    bare `__import__`, `exec`, a relative import) or could not read/parse
    a blob it needed -- item 361(c): classifies `VERIFIER_UNRESOLVABLE`
    rather than censusing optimistically."""


class VerifierExecutionError(Exception):
    """The isolated verifier subprocess failed to produce a well-formed
    result -- classifies `VERIFIER_UNRESOLVABLE`/`REPLAY_UNRESOLVABLE`
    at the call site rather than escaping."""


class UnsatisfiedCompletionObligationError(Exception):
    """Raised by `complete_work_item` when
    `completion_obligations_satisfied(...)` is `False` -- naming every
    outstanding obligation, its classification, and (for `FAIL`) the
    specific failing conformance assertions (item 356(a)). Marking a
    checkpoint `COMPLETE` by any means is therefore not sufficient by
    itself to reach `MILESTONE_COMPLETE` while a declared obligation is
    outstanding."""


def _hardened_run(args: list[str], cwd: Path) -> str:
    """Every Git object read this section performs is issued with
    `--no-replace-objects`/`GIT_NO_REPLACE_OBJECTS=1` (items 356(p)/360/
    361(i)): `git replace` refs rewrite what an object id resolves to and
    would otherwise defeat exactly the content-addressing this whole
    mechanism rests on -- both directions are asserted in this section's
    own tests, including the dangerous one (substituting honest bytes
    over a failing blob)."""
    env = dict(os.environ)
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=cwd, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result.stdout


def _hardened_run_bytes(args: list[str], cwd: Path) -> bytes:
    env = dict(os.environ)
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=cwd, env=env, capture_output=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result.stdout


def _sha256_canonical(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# The obligation's own bound conformance and the verifier that executes
# it (item 358(e): "declared constants of the implementation rather than
# caller parameters"). Today's derived closure is exactly these two
# files -- a recorded observation of this repository, never a
# hard-coded pair the closure resolver trusts blindly (item 361(a)).
VERIFIER_ENTRY = "scripts/workflow_state.py"
VERIFIER_SOURCE_ROOT = "scripts"
COMPLETION_OBLIGATION_CONFORMANCE = {
    "WFO-STATE-SERIALIZATION": "verify_wfo_state_serialization",
    "WFO-LEDGER-COVERAGE": "verify_wfo_ledger_coverage",
}
OBLIGATION_ID_RE = re.compile(r"^WFO-[A-Z0-9][A-Z0-9-]{0,47}$")


# ---------------------------------------------------------------------------
# One live writer-set discovery implementation, feeding both consumers
# (item 357, `GPT-R77-002`'s required correction (1)/(5))
# ---------------------------------------------------------------------------

STATE_WRITER_SURFACE_PREFIXES = (".claude/commands/", "scripts/")

_STATE_WRITER_DECLARATION_RE = re.compile(
    r'(?m)^[ \t]*#{0,2}[ \t]*state_writer:[ \t]*(true|false|"publisher")[ \t]*$'
)


def _parse_state_writer_declarations(text: str) -> list[str]:
    """Every declared value found in `text`, in encounter order -- a
    caller checks both "missing" (empty result) and "contradictory"
    (more than one distinct value) in one pass, matching item 357(g)'s
    "a missing or contradictory declaration is a conformance failure,
    never a default"."""
    return [m.group(1).strip('"') for m in _STATE_WRITER_DECLARATION_RE.finditer(text)]


def _ls_tree_at_commit(repo_root: Path, commit: str, prefix: str) -> list[dict]:
    """Every tracked blob under `prefix` at `commit`, as `{path, mode,
    blob}` -- hardened against `git replace`."""
    out = _hardened_run(["ls-tree", "-r", "-z", commit, "--", prefix], cwd=repo_root)
    entries = []
    for line in out.split("\x00"):
        if not line:
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) != 3:
            continue
        mode, obj_type, blob = parts
        if obj_type != "blob":
            continue
        entries.append({"path": path, "mode": mode, "blob": blob})
    return entries


def _blob_mode_and_sha_at_commit(repo_root: Path, commit: str, path: str) -> tuple[str, str] | None:
    """`(mode, blob)` for `path` at `commit`, or `None` if `path` does not
    exist there -- `git ls-tree` exits `0` with empty output for a
    missing path, unlike `cat-file -e`, so emptiness (not a nonzero
    return code) is the absence signal."""
    out = _hardened_run(["ls-tree", commit, "--", path], cwd=repo_root).strip()
    if not out:
        return None
    meta, _, _ = out.split("\n")[0].partition("\t")
    mode, _obj_type, blob = meta.split()
    return mode, blob


class Discovery(NamedTuple):
    """`discover_state_writers`'s return value -- simultaneously (i) the
    set the conformance validates and (ii) the projection hashed into
    `obligation_content_id`, so the two cannot drift (item 357)."""
    surface_census: tuple[dict, ...]
    writers: tuple[str, ...]
    publisher: str
    non_writers: tuple[str, ...]


def discover_state_writers(repo_root: Path, commit: str) -> Discovery:
    """The **only** inventory of `WORKFLOW_STATE.json` writers anywhere in
    this system (item 357): every tracked file under the two declared
    surfaces at `commit` (`.claude/commands/**`, recursive and
    extension-agnostic; `scripts/**` excluding `*_test.py`), cross-checked
    against each file's own `state_writer` declaration
    (`true`/`false`/`"publisher"`). A missing or contradictory declaration
    fails closed (`StateWriterDeclarationError`) rather than defaulting to
    non-writer. Returns the **complete** surface census (every tracked
    file, not only the writers -- item 357(e): an undeclared file changes
    the identity even though it declares nothing at all), the declared
    writer subset, the sole declared publisher (required to be
    `VERIFIER_ENTRY`), and the declared non-writer subset."""
    census: list[dict] = []
    for prefix in STATE_WRITER_SURFACE_PREFIXES:
        for entry in _ls_tree_at_commit(repo_root, commit, prefix):
            if prefix == "scripts/" and entry["path"].endswith("_test.py"):
                continue
            census.append(entry)
    census.sort(key=lambda e: e["path"])

    writers: list[str] = []
    non_writers: list[str] = []
    publisher: str | None = None
    for entry in census:
        path = entry["path"]
        text = _hardened_run(["cat-file", "blob", entry["blob"]], cwd=repo_root)
        declared = _parse_state_writer_declarations(text)
        distinct = set(declared)
        if len(distinct) != 1:
            raise StateWriterDeclarationError(
                f"{path!r} at {commit} has a missing or contradictory "
                f"state_writer declaration (found: {declared!r})"
            )
        value = distinct.pop()
        if value == "true":
            writers.append(path)
        elif value == "false":
            non_writers.append(path)
        else:  # "publisher"
            if publisher is not None:
                raise StateWriterDeclarationError(
                    f'more than one file declares state_writer: "publisher" '
                    f"({publisher!r} and {path!r}) -- exactly one is required"
                )
            publisher = path

    if publisher is None:
        raise StateWriterDeclarationError(
            'no file on any declared surface declares state_writer: "publisher"'
        )
    if publisher != VERIFIER_ENTRY:
        raise StateWriterDeclarationError(
            f"the declared publisher {publisher!r} is not {VERIFIER_ENTRY!r}, "
            f"the serialization helper the projection names"
        )

    return Discovery(
        surface_census=tuple(census), writers=tuple(sorted(writers)),
        publisher=publisher, non_writers=tuple(sorted(non_writers)),
    )


# Any surface member -- writer or non-writer -- that documents or performs a
# write of the state path outside `_publish_state_file` is a direct-write
# violation (OPUS-R101-003): a Python write-mode `open(...)`, a direct
# `_publish_state_file` call, a shell redirection onto the state path, or an
# in-place `sed -i` against it. Widened beyond Python syntax because
# `scripts/prepare-ai-review.sh` (a non-writer) is on the surface today and a
# `.sh` member could otherwise bypass detection entirely.
_DIRECT_WRITE_VIOLATION_RE = re.compile(
    r"_publish_state_file|"
    r"""open\([^)]*WORKFLOW_STATE\.json[^)]*['"]w|"""
    r">{1,2}\s*\S*WORKFLOW_STATE\.json|"
    r"sed\s+-i[^\n]*WORKFLOW_STATE\.json"
)

# A declared non-writer must additionally never itself call the sanctioned
# publisher wrapper -- that alone would mean it is initiating a write despite
# declaring `state_writer: false`.
_NON_WRITER_VIOLATION_RE = re.compile(
    _DIRECT_WRITE_VIOLATION_RE.pattern + r"|state_transaction\s*\("
)


# ---------------------------------------------------------------------------
# `WFR-67`'s machine-readable `review-subject: bundle | verdict | none`
# header line (`WF8c` item (h), part 1) -- discovered the same fail-closed
# way `discover_state_writers` discovers `state_writer:` above: a missing
# or contradictory declaration is a conformance failure, never a default.
#
# Deliberately scoped to a **fixed named roster**, not "every tracked
# `.claude/commands/*.md` file" the way `discover_state_writers`' own
# `STATE_WRITER_SURFACE_PREFIXES` scans its whole prefix: `WFR-67`'s own
# revision-80 text named an exact, closed set of thirteen files (nine
# bundle/verdict consumers, four exempt). The roster is a deliberately
# extended list, not eternally frozen at that revision-80 snapshot: two
# later command files -- `.claude/commands/review-implementation.md` and
# `.claude/commands/review-functional.md` (`workflow-v2-3` CP1/CP2), both
# bundle-report consumers -- were added when they were built, bringing the
# roster to fifteen files (eleven bundle/verdict consumers, four exempt).
# Retiring `/accept-scoped-remediation` (ledger `I10`) removed one exempt
# entry, leaving the roster at its current fourteen files (eleven bundle/
# verdict consumers, three exempt): a deleted command file cannot carry a
# declaration, and leaving it on the roster would make the discovery
# function refuse for a file that no longer exists. Adding a file here is a
# deliberate per-command decision at the time that command is written, not
# automatic for everything that postdates any prior snapshot.
# `.claude/commands/recover-implementation-provenance.md` (added earlier,
# `WF8c` item (b)) was considered and left
# off: it is a `state_writer: true` recovery action, and its classification
# against `WFR-67`'s roster is deliberately left unassigned rather than
# resolved -- its own step 6 does read `<bundle_dir>/MANIFEST.md`'s existing
# `stage:` field over a bundle directory it did not itself generate, which
# reaches the middle of the three semantic disjuncts, so whether it belongs
# in the roster at all is the same "re-derive from this file's own prose
# against the three semantic disjuncts" judgment call this function's own
# docstring already defers, not something a scan-everything default should
# decide by silently demanding a declaration this roster's own history
# never assigned it.
#
# This discovery function covers the *declaration* half only. WFR-67's own
# text additionally requires the declaration to be "cross-checked against
# the three semantic disjuncts and against the presence of the shared
# assertion" -- deriving each file's expected value from its own prose
# (does it read `REVIEW_FEEDBACK.md`? recompute/compare `bundle_id`?
# present a bundle as ready for review?) and confirming
# `workflow_fingerprint.assert_bundle_not_rejected` actually appears at the
# right points. That derivation is separate, deferred `WF8c` scope; this
# function and its conformance test instead pin the **known-correct**
# classification (the eleven consumers/three exempt split over the current
# fourteen-file roster above -- nine/four over thirteen files at `WFR-67`'s
# own revision-80 text, extended since) as an explicit expected-value
# table, so a file that drifts from it is still caught, even though the
# check is against a recorded table rather than re-derived from first
# principles.
# ---------------------------------------------------------------------------

REVIEW_SUBJECT_ROSTER = frozenset({
    ".claude/commands/accept-milestone.md",
    ".claude/commands/apply-functional-review.md",
    ".claude/commands/apply-implementation-review.md",
    ".claude/commands/apply-plan-review.md",
    ".claude/commands/approve-review.md",
    ".claude/commands/bootstrap-workflow-v2.md",
    ".claude/commands/milestone-implement.md",
    ".claude/commands/milestone-plan.md",
    ".claude/commands/prepare-functional-review.md",
    ".claude/commands/prepare-review.md",
    ".claude/commands/record-manual-plan-review.md",
    ".claude/commands/review-functional.md",
    ".claude/commands/review-implementation.md",
    ".claude/commands/review-plan.md",
    # workflow-2.4.0's own new command, `/request-plan-amendment.md`
    # (CP3), is deliberately *not* added here: it never reads
    # `REVIEW_FEEDBACK.md`, never recomputes/compares a `bundle_id`, and
    # never presents a bundle as ready for review -- none of the three
    # semantic disjuncts this roster exists to classify apply to it. Its
    # own review-subject posture is `none` in substance, exactly like a
    # command already absent from this frozenset; recorded here so the
    # omission reads as a decision, not an oversight (D-Plan-Amendment,
    # CP2's own compatibility-audit deliverable).
})

_REVIEW_SUBJECT_DECLARATION_RE = re.compile(
    r'(?m)^[ \t]*review-subject:[ \t]*(bundle|verdict|none)[ \t]*$'
)


class ReviewSubjectDeclarationError(Exception):
    """A file on `REVIEW_SUBJECT_ROSTER` has a missing, contradictory, or
    unrecognized `review-subject:` declaration (`WFR-67`), or is absent
    from `commit` entirely."""


def _parse_review_subject_declarations(text: str) -> list[str]:
    """Every declared `review-subject:` value found in `text`, in
    encounter order -- mirrors `_parse_state_writer_declarations`'s own
    "missing vs. contradictory, never a default" discipline."""
    return list(_REVIEW_SUBJECT_DECLARATION_RE.findall(text))


def discover_review_subject_declarations(repo_root: Path, commit: str) -> dict[str, str]:
    """`REVIEW_SUBJECT_ROSTER`'s own declared `review-subject:` value at
    `commit`, keyed by path. A missing or contradictory declaration fails
    closed (`ReviewSubjectDeclarationError`, naming what was found) rather
    than defaulting to `"none"`; a roster path absent from `commit`
    entirely fails closed the same way."""
    declarations: dict[str, str] = {}
    for path in sorted(REVIEW_SUBJECT_ROSTER):
        resolved = _blob_mode_and_sha_at_commit(repo_root, commit, path)
        if resolved is None:
            raise ReviewSubjectDeclarationError(f"{path!r} does not exist at {commit}")
        _mode, blob = resolved
        text = _hardened_run(["cat-file", "blob", blob], cwd=repo_root)
        declared = _parse_review_subject_declarations(text)
        distinct = set(declared)
        if len(distinct) != 1:
            raise ReviewSubjectDeclarationError(
                f"{path!r} at {commit} has a missing or contradictory "
                f"review-subject declaration (found: {declared!r})"
            )
        declarations[path] = distinct.pop()
    return declarations


# ---------------------------------------------------------------------------
# WF8c (m): the reconciliation table's own machine-readable
# `{item: (status, owner)}` universe -- WFR-68 property (i)'s "re-derives
# this item set from the reconciliation table itself at the pinned commit,
# never from the artifact's own enumeration" requirement. `docs/ai-workflow/
# registry/<work_item_id>-ledger-status.json` (generated from this parse) is
# WFO-LEDGER-COVERAGE's subject artifact. The obligation's full verifier
# (`verify_wfo_ledger_coverage`, below `verify_wfo_state_serialization`,
# properties (ii)-(iv) and the adversarial arms) is now bound in
# `COMPLETION_OBLIGATION_CONFORMANCE` -- real evidence population for the
# reconciliation table's 211 items (both `WF8b`'s historically-`IMPLEMENTED`
# entries and `WF8c`'s own incremental delivery) remains separate, ongoing
# work: `WFR-69`'s pre-flight above correctly keeps refusing `WF8c`'s own
# checkpoint completion until every open item is evidenced, exactly as
# designed.
# ---------------------------------------------------------------------------


class ReconciliationTableParseError(Exception):
    """The '### Reconciliation table' markdown in `WORKFLOW_V2_PLAN.md`
    could not be parsed into a well-formed `{item: (status, owner)}`
    mapping -- fails closed rather than silently resolving a malformed or
    reformatted table, since this parse is the sole source WFR-68 property
    (i) trusts."""


RECONCILIATION_TABLE_HEADING = "### Reconciliation table"
RECONCILIATION_TABLE_HEADER_ROW = "| Items | Topic | Status | Owner | Evidence / rationale |"
_RECONCILIATION_STATUS_TOKENS = ("IMPLEMENTED", "ABSENT", "PARTIAL", "SUPERSEDED")
_RECONCILIATION_STATUS_RE = re.compile(r"[A-Z]+")


def _split_markdown_table_row(line: str) -> list[str]:
    """Splits one `| a | b | c |` row on unquoted `|` only -- a cell's own
    inline code span (`` `review-subject: bundle|verdict|none` ``, item
    376's own row) can legitimately contain a literal `|`, which a naive
    `str.split('|')` would mistake for an extra cell boundary."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    for ch in body:
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
        elif ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def _expand_reconciliation_items_cell(cell: str) -> list[int]:
    """`'229-230, 233-241'` / `'231, 232'` / `'166 (pre-167, outside the
    umbrella range, checked because it recurred)'` -> the sorted individual
    item numbers a row's `Items` column names; a trailing parenthetical is
    explanatory prose, never part of the range."""
    cell = cell.split("(")[0].strip()
    items: list[int] = []
    for part in cell.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            items.extend(range(int(lo.strip()), int(hi.strip()) + 1))
        else:
            items.append(int(part))
    return items


def _resolve_reconciliation_status(status_cell: str) -> str:
    """The row's canonical status is its first all-caps token once bold
    markup (`**`) is stripped -- `IMPLEMENTED`/`ABSENT`/`PARTIAL`/
    `SUPERSEDED` -- with the remaining prose (parenthetical qualifiers like
    `'(368(d) only)'`) left as the row's own historical rationale, never
    further subdivided (the "per-item resolution rule", `WORKFLOW_V2_PLAN.md`
    revision 82, `OPUS-R102-007`)."""
    match = _RECONCILIATION_STATUS_RE.search(status_cell.replace("*", ""))
    if not match or match.group(0) not in _RECONCILIATION_STATUS_TOKENS:
        raise ReconciliationTableParseError(f"unrecognized reconciliation-table status cell: {status_cell!r}")
    return match.group(0)


def _resolve_reconciliation_owner(status: str, owner_cell: str) -> str:
    """Per-item resolution rule (revision 82): a `SUPERSEDED` row's items
    resolve to owner `'none'`; a `PARTIAL` row's items resolve to the
    checkpoint owing the *remaining* work -- every `PARTIAL` row this table
    currently carries names `WF8c` for that role, whether alone
    (`'WF8c (267 only)'`) or second in a compound cell (`'WF8b` for the
    delivered core; `WF8c` for ...'`); otherwise the row's single stated
    owner is whichever of `WF8b`/`WF8c` occurs *first* in the cell -- not a
    fixed preference order, since a cell can legitimately mention the other
    checkpoint later, in explanatory prose (item 376's own
    `'WF8c (WFR-67, reassigned from WF8b)'` row, where a fixed WF8b-first
    check would misresolve)."""
    if status == "SUPERSEDED":
        return "none"
    if status == "PARTIAL":
        if "WF8c" not in owner_cell:
            raise ReconciliationTableParseError(
                f"PARTIAL row's owner cell names no WF8c remaining-work owner: {owner_cell!r}"
            )
        return "WF8c"
    positions = [(owner_cell.index(token), token) for token in ("WF8b", "WF8c") if token in owner_cell]
    if positions:
        return min(positions)[1]
    if owner_cell.strip().lower().startswith("none"):
        return "none"
    raise ReconciliationTableParseError(f"cannot resolve owner from reconciliation-table cell: {owner_cell!r}")


def _parse_reconciliation_table_text(text: str) -> dict[int, dict[str, str]]:
    """The shared parse core `parse_reconciliation_table` (pinned-commit)
    and `_parse_reconciliation_table_working_tree` (`WFR-69`'s own
    live-working-tree pre-flight) both call -- identical row/status/owner
    resolution either way, differing only in where `text` came from."""
    lines = text.splitlines()
    try:
        heading_idx = next(i for i, line in enumerate(lines) if line.strip() == RECONCILIATION_TABLE_HEADING)
    except StopIteration:
        raise ReconciliationTableParseError(f"no {RECONCILIATION_TABLE_HEADING!r} heading found")
    try:
        header_idx = next(
            i for i in range(heading_idx, len(lines)) if lines[i].strip() == RECONCILIATION_TABLE_HEADER_ROW
        )
    except StopIteration:
        raise ReconciliationTableParseError("no reconciliation table header row found after the heading")

    result: dict[int, dict[str, str]] = {}
    for line in lines[header_idx + 2:]:  # skip the header row and the '|---|...' separator row
        if not line.startswith("|"):
            break
        cells = _split_markdown_table_row(line)
        if len(cells) < 4:
            raise ReconciliationTableParseError(f"malformed reconciliation table row: {line!r}")
        items_cell, topic_cell, status_cell, owner_cell = cells[0], cells[1], cells[2], cells[3]
        status = _resolve_reconciliation_status(status_cell)
        owner = _resolve_reconciliation_owner(status, owner_cell)
        for item in _expand_reconciliation_items_cell(items_cell):
            if item in result:
                raise ReconciliationTableParseError(
                    f"item {item} appears in more than one reconciliation table row"
                )
            result[item] = {"status": status, "owner": owner, "topic": topic_cell}
    if not result:
        raise ReconciliationTableParseError("reconciliation table parsed to zero rows")
    return result


def parse_reconciliation_table(
    repo_root: Path, commit: str, *, plan_path: str = "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
) -> dict[int, dict[str, str]]:
    """Re-derives `WORKFLOW_V2_PLAN.md`'s '### Reconciliation table' at the
    pinned `commit` into `{item: {"status": ..., "owner": ..., "topic":
    ...}}` -- WFR-68 property (i)'s own item-set source of truth, read
    directly from the plan document rather than trusted from
    `<work_item_id>-ledger-status.json`'s own enumeration (which is exactly
    what the "omission attack"/"relabel attack" adversarial arms exist to
    keep from being self-certifying). Raises `ReconciliationTableParseError`
    on any row this cannot resolve -- the table changes only alongside a
    fresh approved plan revision, and a parse failure here should stop a
    technical review rather than silently degrade into a partial item set."""
    text = _hardened_run(["show", f"{commit}:{plan_path}"], cwd=repo_root)
    return _parse_reconciliation_table_text(text)


def _parse_reconciliation_table_working_tree(
    repo_root: Path, *, plan_path: str = "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
) -> dict[int, dict[str, str]]:
    """Same parse as `parse_reconciliation_table`, read directly off the
    **live working tree** rather than a pinned commit -- `WFR-69`'s own
    pre-flight runs at the moment `complete_checkpoint` is called, which is
    typically *before* the checkpoint's own plan/registry edits are
    committed at all (`bootstrap-workflow-v2.md` step 6 calls this, then
    commits, in that order)."""
    text = (Path(repo_root) / plan_path).read_text()
    return _parse_reconciliation_table_text(text)


def ledger_status_path_for_work_item(work_item_id: str) -> Path:
    """`docs/ai-workflow/registry/<work_item_id>-ledger-status.json` --
    `WFR-68`'s subject artifact, named the same way
    `fingerprint.artifacts_path_for_work_item` names its own registry
    artifact."""
    fingerprint.validate_work_item_id(work_item_id)
    return Path(f"docs/ai-workflow/registry/{work_item_id}-ledger-status.json")


def wf8c_evidence_path_for_work_item(work_item_id: str) -> Path:
    """`docs/ai-workflow/registry/<work_item_id>-wf8c-evidence.json` --
    `WFR-68`'s companion artifact, populated only as `WF8c` is actually
    implemented, binding each `WF8c`-owned reconciliation-table item to
    the exact test/function that discharges it. Named the same way
    `ledger_status_path_for_work_item` names its own sibling artifact; the
    literal `wf8c` segment is deliberate (the plan's own name for this
    file), not a `work_item_id`-derived token."""
    fingerprint.validate_work_item_id(work_item_id)
    return Path(f"docs/ai-workflow/registry/{work_item_id}-wf8c-evidence.json")


def _read_json_from_working_tree(repo_root: Path, rel_path: Path | str) -> dict:
    """Reads and parses a JSON file directly off the **live working tree**
    (never a pinned commit). A missing or malformed file resolves to `{}`
    -- an absent/garbled evidence artifact is exactly "no evidence
    recorded" for every caller of this helper, not a hard error."""
    full = Path(repo_root) / rel_path
    if not full.is_file():
        return {}
    try:
        return json.loads(full.read_text())
    except json.JSONDecodeError:
        return {}


def _reconciliation_evidence_by_item(repo_root: Path, work_item_id: str) -> dict[int, str]:
    """Combines `<work_item_id>-ledger-status.json`'s own per-entry
    `evidence` field with the companion `<work_item_id>-wf8c-evidence.json`
    (`WFR-68`'s two-artifact split) into one `{item: evidence_test_id}`
    map, read live off the working tree. A ledger-status.json entry's own
    `evidence`, when present, wins; the companion file only ever fills in
    an item the ledger has not itself recorded evidence for. Each
    evidence value is a dotted unittest test id (`module.Case.method`, the
    same form `python3 -m unittest <id>` accepts) -- the "resolvable,
    executable" reference `WFR-68`/`WFR-69` both require."""
    evidence: dict[int, str] = {}
    ledger = _read_json_from_working_tree(repo_root, ledger_status_path_for_work_item(work_item_id))
    for record in ledger.get("entries", []):
        item = record.get("item")
        ev = record.get("evidence")
        if isinstance(item, int) and isinstance(ev, str) and ev:
            evidence[item] = ev
    companion = _read_json_from_working_tree(repo_root, wf8c_evidence_path_for_work_item(work_item_id))
    for record in companion.get("entries", []):
        item = record.get("item")
        ev = record.get("evidence")
        if isinstance(item, int) and isinstance(ev, str) and ev and item not in evidence:
            evidence[item] = ev
    return evidence


def _load_and_run_named_test(repo_root: Path, test_id: str) -> tuple[bool, str]:
    """`WFR-69`'s own "resolvable, executable ... re-executes and derives
    green" requirement for a single ledger-evidence reference. `test_id`
    is a dotted unittest test id (`module.TestCase.test_method`), resolved
    and run against the **live working tree's** `scripts/` directory in a
    fresh, isolated interpreter (`-I -B -S`, mirroring
    `_run_verifier_driver`'s own isolation so an already-imported module in
    *this* process can never mask a real import failure) -- never against a
    pinned commit, since this pre-flight is deliberately not the
    approval-bound chain `WFO-LEDGER-COVERAGE`'s own verifier owns.
    Returns `(passed, detail)`; any resolution or execution failure is
    `(False, <reason>)`, never an exception -- an unresolvable or erroring
    evidence reference is exactly the case this pre-flight exists to
    catch, not propagate."""
    scripts_dir = Path(repo_root) / "scripts"
    driver_dir = Path(tempfile.mkdtemp(prefix="wfr69-evidence-driver-"))
    try:
        driver_path = driver_dir / "run_evidence.py"
        driver_path.write_text(
            "import json, sys, unittest\n"
            f"sys.path.insert(0, {str(scripts_dir)!r})\n"
            "try:\n"
            f"    _suite = unittest.TestLoader().loadTestsFromName({test_id!r})\n"
            "except Exception as _exc:\n"
            "    print(json.dumps({'ok': False, 'detail': 'unresolvable: ' + repr(_exc)}))\n"
            "    sys.exit(0)\n"
            "if _suite.countTestCases() == 0:\n"
            "    print(json.dumps({'ok': False, 'detail': 'resolved to zero test cases'}))\n"
            "    sys.exit(0)\n"
            "_result = unittest.TestResult()\n"
            "_suite.run(_result)\n"
            "_ok = _result.wasSuccessful()\n"
            "_detail = '' if _ok else str(_result.failures + _result.errors)\n"
            "print(json.dumps({'ok': _ok, 'detail': _detail}))\n"
        )
        env = {"PATH": os.environ.get("PATH", "")}
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-S", str(driver_path)],
            capture_output=True, text=True, env=env, timeout=120, cwd=str(scripts_dir),
        )
    finally:
        shutil.rmtree(driver_dir, ignore_errors=True)

    stdout = proc.stdout.strip()
    if not stdout:
        return False, f"isolated evidence process produced no output: rc={proc.returncode} stderr={proc.stderr!r}"
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return False, f"isolated evidence process produced non-JSON output: {stdout!r}"
    return bool(payload.get("ok")), payload.get("detail", "")


def _pre_flight_wfo_ledger_coverage(repo_root: Path, work_item_id: str) -> list[int]:
    """`WFR-69`'s own non-approval-gated pre-flight for the
    `WFO-LEDGER-COVERAGE` obligation: re-derives, directly against the
    live working tree, whether every item the reconciliation table names
    already satisfies the disposition the table's own per-item resolution
    rule assigns it -- status-conditional (revision 88, `OPUS-R110-001`):
    a `SUPERSEDED`/`none` item needs no evidence at all (the table's own
    recorded supersession already satisfies it, exactly as item 355 clause
    (b2) and `WFR-68` property (iv) already treat that disposition);
    every other item -- `IMPLEMENTED` or a `WF8c`-owned `ABSENT`/`PARTIAL`
    -- needs a resolvable, executable evidence entry that re-executes
    green. Returns the sorted list of item numbers still undischarged
    (empty means every item is satisfied) -- deliberately never raises
    itself, so the caller can report every unresolved item in one refusal
    rather than stopping at the first."""
    table = _parse_reconciliation_table_working_tree(repo_root)
    evidence = _reconciliation_evidence_by_item(repo_root, work_item_id)
    unresolved: list[int] = []
    for item, row in sorted(table.items()):
        if row["status"] == "SUPERSEDED" and row["owner"] == "none":
            continue
        test_id = evidence.get(item)
        if not test_id:
            unresolved.append(item)
            continue
        ok, _detail = _load_and_run_named_test(repo_root, test_id)
        if not ok:
            unresolved.append(item)
    return unresolved


# Dispatch table for `complete_checkpoint`'s own `WFR-69` pre-flight, keyed
# by completion-obligation id -- deliberately separate from
# `COMPLETION_OBLIGATION_CONFORMANCE` (that one is approval-bound, this one
# is not). An obligation id with no entry here fails closed inside
# `complete_checkpoint` itself, never by a KeyError escaping this dict.
_PRE_CHECKPOINT_COMPLETION_PRE_FLIGHT = {
    "WFO-LEDGER-COVERAGE": _pre_flight_wfo_ledger_coverage,
}


CHECKPOINT_REACHABILITY_MISSING_TESTS_HEADING = "## Missing tests"
_MISSING_TEST_ITEM_RE = re.compile(r"^(\d+)\. ")


def _highest_missing_test_item(
    repo_root: Path, commit: str, *, plan_path: str = "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
) -> int:
    """The highest-numbered top-level list item under `WORKFLOW_V2_PLAN.md`'s
    own '## Missing tests' section, at the pinned `commit` -- item 355
    clause (c)'s comparison value. Scoped to that one section (the heading
    itself through the next '## ' heading) because plain `N. ` numbered
    lists recur throughout the document for unrelated finding-disposition
    prose, at numbers that reset and would otherwise be mistaken for
    missing-test items."""
    text = _hardened_run(["show", f"{commit}:{plan_path}"], cwd=repo_root)
    lines = text.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines) if line.startswith(CHECKPOINT_REACHABILITY_MISSING_TESTS_HEADING)
        )
    except StopIteration:
        raise ReconciliationTableParseError(
            f"no {CHECKPOINT_REACHABILITY_MISSING_TESTS_HEADING!r} heading found in {plan_path}"
        )
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    numbers = [int(m.group(1)) for line in lines[start:end] if (m := _MISSING_TEST_ITEM_RE.match(line))]
    if not numbers:
        raise ReconciliationTableParseError(
            f"{CHECKPOINT_REACHABILITY_MISSING_TESTS_HEADING!r} section parsed to zero numbered items"
        )
    return max(numbers)


def verify_checkpoint_reachability_conformance(
    repo_root: str | Path, commit: str, work_item_id: str,
) -> dict:
    """Item 355's own checkpoint-reachability conformance (`WF8c` clause
    (m), part 2) -- re-derives, at the pinned `commit`, whether the
    reconciliation table's per-item resolved `(status, owner)` pairs
    (`parse_reconciliation_table`, which already applies the per-item
    resolution rule) still satisfy item 355's own clauses:

    (a) every open (non-`IMPLEMENTED`), *undischarged* item's resolved
        owner is the *live* umbrella checkpoint `select_next_checkpoint`
        actually returns today -- never a hardcoded id that silently goes
        stale the moment that checkpoint completes (`OPUS-R101-002`'s own
        defect class, one level up from what this conformance protects
        against here);
    (b1) no open, undischarged item names an already-`COMPLETE` checkpoint
         as its owner;
    (b2) every open, undischarged item names a real, reachable,
         non-`COMPLETE` registry owner, unless its status is `SUPERSEDED`
         (owner `none` -- whether the design section it tests is genuinely
         superseded is a plan-text judgment call this mechanism does not
         re-litigate);

    "Discharged" (`OPUS-R129-003`): an item the reconciliation table
    itself still marks `ABSENT`/`PARTIAL` forever -- `WF8c`'s own
    resolution rule (revision 88, `OPUS-R110-001`) closes such an item by
    a recorded, re-executable evidence reference, never by flipping the
    table's frozen text to `IMPLEMENTED` -- so once its owning checkpoint
    finishes, the item is legitimately no longer *live* open work, even
    though the table's status column never changes (a fresh plan revision
    is the only thing that ever could). A discharged item is excluded from
    all three clauses here. Discharge means only "an evidence reference is
    recorded" (`_reconciliation_evidence_ids_at_commit`); it does not
    re-execute it -- re-execution green-ness is `WFO-LEDGER-COVERAGE`'s
    own separately-verified, non-duplicated property (ii)
    (`verify_wfo_ledger_coverage`/`_pre_flight_wfo_ledger_coverage`, which
    already gate checkpoint completion and technical approval on it). An
    item with no recorded evidence is never discharged and still fails
    (a)/(b1)/(b2) exactly as before once its owner completes -- the real
    staleness/omission bug these clauses exist to catch.
    (c) the reconciliation table's own upper bound tracks the 'Missing
        tests' list's highest item, so adding an item without extending
        the table's range fails closed rather than silently orphaning it;
    (d) `WFR-63`'s own `checkpoint_ids` -- the plan's worked example for
        this clause, unchanged since revision 59 -- still names the live
        umbrella owner alongside its historical `WF4a-iii` entry, so its
        still-open obligations are never silently read as inherited from a
        checkpoint that already completed and never delivered them.

    Mirrors `verify_wfo_state_serialization`'s `{"status", "detail",
    "failing_assertions"}` return shape."""
    repo_root = Path(repo_root)
    failing: list[str] = []

    state = _read_json_at_commit_or_empty(repo_root, commit, "docs/ai-workflow/WORKFLOW_STATE.json")
    work_item = state.get("work_items", {}).get(work_item_id)
    if work_item is None:
        detail = f"no work_items[{work_item_id!r}] in WORKFLOW_STATE.json at {commit}"
        return {"status": "FAIL", "detail": detail, "failing_assertions": [detail]}

    registry_path = work_item.get("registry_path")
    if not registry_path:
        detail = f"work_items[{work_item_id!r}] carries no registry_path at {commit}"
        return {"status": "FAIL", "detail": detail, "failing_assertions": [detail]}
    registry = _read_json_at_commit_or_empty(repo_root, commit, registry_path)
    if not registry.get("checkpoints"):
        detail = f"no registry checkpoints at {registry_path!r} at {commit}"
        return {"status": "FAIL", "detail": detail, "failing_assertions": [detail]}

    registry_ids = {entry["id"] for entry in registry["checkpoints"]}
    complete_ids = {
        cid for cid, entry in work_item.get("checkpoints", {}).items() if entry.get("status") == "COMPLETE"
    }

    try:
        current_owner = select_next_checkpoint(work_item, registry)
    except NoCheckpointReadyError as exc:
        failing.append(f"clause (a): select_next_checkpoint(...) is blocked: {exc}")
        current_owner = None

    table = parse_reconciliation_table(repo_root, commit)
    evidence_ids = _reconciliation_evidence_ids_at_commit(repo_root, commit, work_item_id)

    open_owners = {
        row["owner"] for item, row in table.items()
        if row["status"] != "IMPLEMENTED" and row["owner"] != "none" and item not in evidence_ids
    }
    if current_owner is None:
        if open_owners:
            failing.append(
                f"clause (a): select_next_checkpoint(...) returned None (every registry "
                f"checkpoint COMPLETE) but the table still assigns open work to {sorted(open_owners)}"
            )
    else:
        stale = open_owners - {current_owner}
        if stale:
            failing.append(
                f"clause (a): table assigns open work to {sorted(stale)}, but the live "
                f"umbrella owner select_next_checkpoint(...) resolves to today is {current_owner!r}"
            )

    b1_violations = sorted(
        item for item, row in table.items()
        if row["status"] != "IMPLEMENTED" and row["owner"] in complete_ids and item not in evidence_ids
    )
    if b1_violations:
        failing.append(f"clause (b1): items {b1_violations} resolve to an already-COMPLETE owner")

    b2_violations = sorted(
        item for item, row in table.items()
        if row["status"] not in ("IMPLEMENTED", "SUPERSEDED")
        and (row["owner"] == "none" or row["owner"] not in registry_ids or row["owner"] in complete_ids)
        and item not in evidence_ids
    )
    if b2_violations:
        failing.append(f"clause (b2): items {b2_violations} name no real, reachable, non-COMPLETE owner")

    non_166 = [item for item in table if item != 166]
    table_upper_bound = max(non_166) if non_166 else 166
    try:
        missing_tests_upper_bound = _highest_missing_test_item(repo_root, commit)
    except ReconciliationTableParseError as exc:
        failing.append(f"clause (c): {exc}")
    else:
        if table_upper_bound != missing_tests_upper_bound:
            failing.append(
                f"clause (c): reconciliation table's own upper bound is {table_upper_bound}, but "
                f"the 'Missing tests' list's highest item is {missing_tests_upper_bound}"
            )

    mapping_path = work_item.get("mapping_path")
    if mapping_path and current_owner is not None:
        mapping = _read_json_at_commit_or_empty(repo_root, commit, mapping_path)
        wfr63 = mapping.get("requirements", {}).get("WFR-63", {})
        wfr63_checkpoint_ids = wfr63.get("checkpoint_ids", [])
        if wfr63_checkpoint_ids and all(cid in complete_ids for cid in wfr63_checkpoint_ids):
            failing.append(
                f"clause (d): WFR-63's checkpoint_ids {wfr63_checkpoint_ids} names only "
                f"already-COMPLETE checkpoints -- its still-open obligations (owed to "
                f"{current_owner!r} per the reconciliation table) would be silently read as "
                f"inherited from a checkpoint that never delivered them"
            )

    if failing:
        return {"status": "FAIL", "detail": "; ".join(failing), "failing_assertions": failing}
    return {"status": "PASS", "detail": "checkpoint-reachability conformance holds", "failing_assertions": []}


def verify_wfo_state_serialization(repo_root: str | Path, commit: str) -> dict:
    """`WFO-STATE-SERIALIZATION`'s bound conformance (items 354(c), 356,
    357) -- the function `resolve_completion_obligations` materializes
    and executes in an isolated interpreter, with the live repository off
    the module path (item 358(c)). Runs entirely off pinned-commit `git`
    reads against `repo_root` (hardened against `git replace`); it never
    touches the live state file or the live `WORKFLOW_STATE.lock` (item
    356(n)), so it is safe to run from inside the terminal transition's
    own held lock.

    **What is actually checked, and why.** `WORKFLOW_STATE.json`'s twelve
    writers are `.claude/commands/*.md` procedures an AI agent executes
    from prose, not deterministic scripts a hermetic suite can run end to
    end -- no test harness can "execute" natural language. The
    mechanically checkable property is therefore that each declared
    writer's *documented* critical section names `state_transaction`/
    `state_lock`, the one primitive whose own process-level concurrency,
    crash-release and re-entrancy properties are independently proven
    against real processes (see `state_lock`/`state_transaction`'s own
    hermetic suite in `workflow_state_test.py`) -- never a claim that a
    given invocation of the command happened correctly, which is outside
    any hermetic test's reach for an AI-followed procedure. A declared
    non-writer must not itself call the publisher or open the state path
    for writing; a plain **read** reference (as `scripts/prepare-ai-review.sh`
    legitimately makes, to cross-check `record_bundle_generation` already
    ran) does not violate this -- see that file's own `state_writer: false`
    note."""
    repo_root = Path(repo_root)
    failing: list[str] = []
    try:
        discovery = discover_state_writers(repo_root, commit)
    except StateWriterDeclarationError as exc:
        return {"status": "FAIL", "detail": str(exc), "failing_assertions": [str(exc)]}

    census_by_path = {e["path"]: e for e in discovery.surface_census}

    for path in discovery.writers:
        text = _hardened_run(["cat-file", "blob", census_by_path[path]["blob"]], cwd=repo_root)
        if "state_transaction" not in text and "state_lock" not in text:
            failing.append(
                f"{path}: declared state_writer: true but its documented "
                f"procedure does not name state_transaction/state_lock"
            )
        if _DIRECT_WRITE_VIOLATION_RE.search(text):
            failing.append(
                f"{path}: declared state_writer: true but its documented "
                f"procedure also describes a direct write of the state path "
                f"outside state_transaction/_publish_state_file"
            )

    for path in discovery.non_writers:
        text = _hardened_run(["cat-file", "blob", census_by_path[path]["blob"]], cwd=repo_root)
        if _NON_WRITER_VIOLATION_RE.search(text):
            failing.append(
                f"{path}: declared state_writer: false but references the "
                f"publisher or a write-mode open of the state path"
            )

    publisher_entry = census_by_path.get(discovery.publisher)
    if publisher_entry is None:
        failing.append(f"declared publisher {discovery.publisher!r} not found in the surface census")
    else:
        publisher_text = _hardened_run(["cat-file", "blob", publisher_entry["blob"]], cwd=repo_root)
        if "def state_lock(" not in publisher_text or "def state_transaction(" not in publisher_text:
            failing.append(
                f"declared publisher {discovery.publisher!r} does not define "
                f"state_lock/state_transaction"
            )

    if failing:
        return {"status": "FAIL", "detail": "; ".join(failing), "failing_assertions": failing}
    return {
        "status": "PASS",
        "detail": (
            f"{len(discovery.writers)} writer(s), 1 publisher, "
            f"{len(discovery.non_writers)} non-writer(s) all conform"
        ),
        "failing_assertions": [],
    }


# ---------------------------------------------------------------------------
# WFO-LEDGER-COVERAGE's own bound conformance (WFR-68 properties (i)-(iv);
# WF8c item (m)'s remaining scope) -- the approval-bound counterpart to
# `_pre_flight_wfo_ledger_coverage` above: that one gates a single
# checkpoint's own completion against the *live working tree*; this one
# gates `complete_work_item` against a *pinned, technically-approved*
# commit, through the same `resolve_completion_obligations` machinery
# `WFO-STATE-SERIALIZATION` already exercises. The obligation is
# permanently bound to this one work item by construction -- the
# reconciliation table and its ledger are `workflow-v2-1-core`-specific
# artifacts, not a general per-work-item mechanism -- so, exactly like
# `VERIFIER_ENTRY`/`VERIFIER_SOURCE_ROOT` above (item 358(e)), the work
# item id is a declared constant of this implementation rather than a
# parameter the fixed `(repo_root, commit)` verifier signature has no
# room for.
# ---------------------------------------------------------------------------

LEDGER_COVERAGE_WORK_ITEM_ID = "workflow-v2-1-core"


def _git_show_json_at_commit(repo_root: Path, commit: str, rel_path: Path | str):
    """Reads and parses a JSON file at a **pinned commit** (never the
    working tree) via `git show`. Returns `None` when the path does not
    exist at `commit`; raises `json.JSONDecodeError` on malformed JSON --
    unlike the working-tree helper `_read_json_from_working_tree` (which
    treats either case as "no evidence recorded"), the approval-bound
    verifier must not silently treat a corrupted artifact as absent."""
    try:
        text = _hardened_run(["show", f"{commit}:{rel_path}"], cwd=repo_root)
    except subprocess.CalledProcessError:
        return None
    return json.loads(text)


def _reconciliation_evidence_ids_at_commit(
    repo_root: Path, commit: str, work_item_id: str,
) -> dict[int, str]:
    """Commit-pinned analog of `_reconciliation_evidence_by_item` (which
    reads the live working tree): the same `<work_item_id>-ledger-
    status.json` entry-`evidence`-wins, `<work_item_id>-wf8c-evidence.json`
    companion-fills-gaps merge, read instead via `_git_show_json_at_commit`
    at the pinned `commit`. Presence here means only "an evidence reference
    is recorded" -- it deliberately does not re-execute it. Re-execution
    green-ness is `WFO-LEDGER-COVERAGE`'s own separately-verified property
    (ii) (`verify_wfo_ledger_coverage`/`_pre_flight_wfo_ledger_coverage`);
    duplicating that here would make this function recurse into every
    other item's evidence test on every call, which is not this clause's
    concern (ownership/reachability consistency, not evidence validity)."""
    evidence: dict[int, str] = {}
    ledger = _git_show_json_at_commit(repo_root, commit, ledger_status_path_for_work_item(work_item_id))
    if isinstance(ledger, dict):
        for record in ledger.get("entries", []):
            if not isinstance(record, dict):
                continue
            item, ev = record.get("item"), record.get("evidence")
            if isinstance(item, int) and isinstance(ev, str) and ev:
                evidence[item] = ev
    companion = _git_show_json_at_commit(repo_root, commit, wf8c_evidence_path_for_work_item(work_item_id))
    if isinstance(companion, dict):
        for record in companion.get("entries", []):
            if not isinstance(record, dict):
                continue
            item, ev = record.get("item"), record.get("evidence")
            if isinstance(item, int) and isinstance(ev, str) and ev and item not in evidence:
                evidence[item] = ev
    return evidence


class PinnedEvidenceWorktreeIntegrityError(Exception):
    """`GPT-R132-001`/`-002`/`OPUS-R133`'s shared fail-closed signal:
    raised whenever a freshly materialized pinned evidence checkout
    cannot be proven byte-identical to its expected commit. Left uncaught
    by `verify_wfo_ledger_coverage`, this propagates out of the isolated
    verifier driver (`_run_verifier_driver`'s own `except Exception`
    wrapping) as `VERIFIER_UNRESOLVABLE`, never `PASS` -- the same
    fail-closed path `VerifierExecutionError` already uses, so a
    materialization failure can never be silently swallowed into a false
    positive."""


def _git_blob_sha1(data: bytes) -> str:
    """Git's own blob identity (`sha1(b"blob <len>\0" + content)`),
    computed in pure Python -- never by invoking `git hash-object` or any
    other Git command against the bytes, so no smudge/clean filter can
    run and no result can depend on one (`OPUS-R133-M01`)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _git_ls_tree_blobs(scratch_dir: Path, commit: str) -> dict[str, tuple[str, str]]:
    """Every blob `commit` provides in `scratch_dir`'s own object
    database, keyed by POSIX-relative path to `(mode, blob_sha1)`.
    `git ls-tree` reads tree objects directly out of the object database;
    it never touches the working tree, so -- unlike `git status` or
    `git diff` -- it applies no smudge/clean filter and fires no hook,
    whatever `scratch_dir`'s own `.git/config`, `.git/info/attributes` or
    `.git/hooks/` currently claim. `core.hooksPath` is still pinned to the
    checkout's own emptied `hooks/` directory here anyway, purely for
    defense in depth: every Git invocation this module issues against a
    pinned clone pins it, with no case-by-case exceptions to reason
    about. Issued through `_hardened_run`
    (`--no-replace-objects`/`GIT_NO_REPLACE_OBJECTS=1`, `OPUS-R136-M02`)
    so a replacement ref present in the clone cannot substitute a
    different tree/blob for `commit` than the literal object it names --
    the same hardening this module's other object readers already
    apply."""
    hooks_dir = scratch_dir / ".git" / "hooks"
    output = _hardened_run(
        ["-c", f"core.hooksPath={hooks_dir}", "ls-tree", "-r", "-z", "--full-tree", commit],
        cwd=scratch_dir,
    )
    entries: dict[str, tuple[str, str]] = {}
    for record in output.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        mode, _obj_type, blob = meta.split(" ")
        entries[path] = (mode, blob)
    return entries


def _verify_pinned_worktree_clean(scratch_dir: Path, commit: str) -> None:
    """Fails closed unless `scratch_dir` is byte-identical to `commit`'s
    own tree -- resolved against `scratch_dir`'s own object database,
    never the live `repo_root` -- and detached exactly at `commit`.

    `OPUS-R133-001`/`-002` found the previous `git status --porcelain`-
    based version of this check trusted the checkout's own `.git/` state:
    a clone-local `core.hooksPath`, or a matched clean/smudge filter pair
    registered via `.git/info/attributes`, could make `git status` report
    a tampered tree as clean, or could make the act of checking itself
    execute the attacker's hook. Neither is reachable here: the expected
    side of the comparison comes only from `_git_ls_tree_blobs` (a pure
    object-database read, immune to every filter/hook/attribute a working
    directory can carry) and the actual side comes only from a plain
    filesystem walk hashed in pure Python (`_git_blob_sha1`, no Git
    process touches the on-disk bytes at all) -- there is no git
    invocation left in this function whose output a clone-local
    config/filter/attribute/hook could influence.

    Called immediately after every materialization (closes
    `GPT-R132-001`: a hook that rewrote tracked bytes during checkout
    leaves the checkout's blobs mismatched, caught before any evidence
    executes). `verify_wfo_ledger_coverage` materializes an independent
    checkout per evidence id and never reuses one (`OPUS-R133-001`), so
    this is the only point in that checkout's lifetime this assertion
    needs to run.

    Every Git invocation this function issues -- including its own two
    `rev-parse` calls, not only `_git_ls_tree_blobs`'s -- pins
    `core.hooksPath` to the checkout's own emptied `hooks/` directory, so
    `_git_ls_tree_blobs`'s "every Git invocation this module issues
    against a pinned clone pins it, with no case-by-case exceptions to
    reason about" is true of this whole function too (`OPUS-R134-M02`).
    Both `rev-parse` calls also go through `_hardened_run`
    (`--no-replace-objects`, `OPUS-R136-M02`), matching
    `_git_ls_tree_blobs`, so "detached exactly at `commit`" means the
    literal object `commit` names even if a replacement ref exists in the
    clone.

    The filesystem walk below prunes only the checkout root's own
    administrative `.git/` -- not every directory anywhere in the tree
    that happens to be named `.git` -- and raises on any directory (at
    any depth) left with no files and no subdirectories of its own
    (`OPUS-R136-M03`): a clean checkout of `commit` never produces a
    nested `.git`-named directory or a genuinely empty one, since Git
    does not track directories, only the file paths they hold."""
    hooks_dir = scratch_dir / ".git" / "hooks"
    resolved = _hardened_run(["-c", f"core.hooksPath={hooks_dir}", "rev-parse", commit], cwd=scratch_dir).strip()
    head = _hardened_run(["-c", f"core.hooksPath={hooks_dir}", "rev-parse", "HEAD"], cwd=scratch_dir).strip()
    if head != resolved:
        raise PinnedEvidenceWorktreeIntegrityError(
            f"pinned evidence checkout at {scratch_dir} has HEAD {head!r}, "
            f"expected {resolved!r} (commit {commit!r})"
        )

    expected = _git_ls_tree_blobs(scratch_dir, commit)
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(scratch_dir):
        rel_dir = Path(dirpath).relative_to(scratch_dir).as_posix()
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d != ".git"]
        elif not dirnames and not filenames:
            raise PinnedEvidenceWorktreeIntegrityError(
                f"pinned evidence checkout at {scratch_dir} has untracked empty "
                f"directory {rel_dir!r} -- commit {commit!r}'s own tree cannot "
                f"produce one"
            )
        for filename in filenames:
            full_path = Path(dirpath) / filename
            rel_path = full_path.relative_to(scratch_dir).as_posix()
            seen.add(rel_path)
            if rel_path not in expected:
                raise PinnedEvidenceWorktreeIntegrityError(
                    f"pinned evidence checkout at {scratch_dir} has untracked path "
                    f"{rel_path!r} not present in commit {commit!r}'s tree"
                )
            expected_mode, expected_blob = expected[rel_path]
            if full_path.is_symlink() or expected_mode not in ("100644", "100755"):
                raise PinnedEvidenceWorktreeIntegrityError(
                    f"pinned evidence checkout at {scratch_dir} path {rel_path!r} is not "
                    f"a plain regular file at commit {commit!r} (mode {expected_mode!r})"
                )
            file_stat = full_path.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                # OPUS-R134-M04: a FIFO/socket/device planted at a tracked
                # path passes the symlink/mode-membership check above (its
                # tracked mode is a plain 100644/100755) but would block
                # `read_bytes()` below forever, or read garbage from a
                # device. Fail closed on the filesystem entry type instead.
                raise PinnedEvidenceWorktreeIntegrityError(
                    f"pinned evidence checkout at {scratch_dir} path {rel_path!r} is not "
                    f"a regular file on disk at commit {commit!r} (st_mode {file_stat.st_mode:#o})"
                )
            actual_mode = "100755" if file_stat.st_mode & 0o111 else "100644"
            if actual_mode != expected_mode:
                raise PinnedEvidenceWorktreeIntegrityError(
                    f"pinned evidence checkout at {scratch_dir} path {rel_path!r} has mode "
                    f"{actual_mode!r}, commit {commit!r} declares {expected_mode!r}"
                )
            actual_blob = _git_blob_sha1(full_path.read_bytes())
            if actual_blob != expected_blob:
                raise PinnedEvidenceWorktreeIntegrityError(
                    f"pinned evidence checkout at {scratch_dir} path {rel_path!r} does not "
                    f"match commit {commit!r}'s own blob (expected {expected_blob}, found {actual_blob})"
                )
    missing = set(expected) - seen
    if missing:
        raise PinnedEvidenceWorktreeIntegrityError(
            f"pinned evidence checkout at {scratch_dir} is missing tracked path(s) from "
            f"commit {commit!r}: {sorted(missing)!r}"
        )


def _materialize_pinned_worktree_at_commit(repo_root: Path, commit: str) -> Path:
    """A real, detached, hook-sanitized Git checkout of `commit` -- a
    faithful repository context for evidence ids that are themselves
    repository-history-dependent (`git rev-parse`, `git diff` against
    another commit, reads of tracked files outside `scripts/`), not only
    the tracked `scripts/` tree a single verifier's own static import
    closure needs (`_materialize_census`). The prior scripts/-only
    scratch tree (`GPT-R131-001`) left every such evidence id structurally
    unable to execute at all.

    `GPT-R132-001` found the previous `git worktree add --detach`
    implementation still trusted mutable live-repository state: a linked
    worktree shares its common Git directory -- including `.git/hooks`
    -- with `repo_root`, so a `post-checkout` hook installed there after
    technical approval could rewrite tracked evidence bytes during
    materialization, undetected. This now clones into an independent
    repository instead of linking a worktree: `git clone` never copies a
    source repository's own hook scripts (only inert `*.sample`
    templates), so the clone starts with no hooks that could execute at
    all; the `hooks/` directory is then deleted and recreated empty
    regardless, and the checkout pins `core.hooksPath` to that
    now-guaranteed-empty directory, so nothing -- not a template, not
    inherited global/system config -- can reintroduce a live hook path.

    `OPUS-R133-001` found that reusing one such clone across every
    evidence id (resetting and cleaning it between ids) left the reused
    checkout's own `.git/config`/`.git/info/attributes`/`.git/hooks/`
    under the control of whichever evidence id ran previously -- a
    channel `git reset --hard`/`git clean -fdx` never touch, since they
    operate on the work tree, not on `.git/` itself. `verify_wfo_ledger_
    coverage` therefore calls this function once per evidence id and
    tears the result down immediately after
    (`_remove_pinned_worktree`) instead of resetting and reusing it:
    every evidence id gets a brand-new clone that no prior evidence
    process has ever had the chance to touch, so nothing inside a prior
    evidence checkout is reused by a later evidence id.

    `GPT-R135-001` found that the fresh clone this produces was still not
    a fully independent object store: a same-filesystem local `git clone`
    hardlinks `.git/objects` by default rather than copying it, so the
    clone and `repo_root` could hold two pathnames for the same inode --
    an in-place write through the clone's object path could corrupt
    `repo_root`'s own object bytes, and removing the clone afterward would
    not undo that. `--no-hardlinks` forces a real copy of every object
    file into the clone, so its object database has no filesystem-level
    aliasing with `repo_root`'s.

    `GPT-R136-001` found that `--no-hardlinks` alone is not sufficient
    when `repo_root` itself borrows objects through its own
    `objects/info/alternates` (e.g. a source repository created with
    Git's `--reference`): a local clone copies that alternate
    relationship verbatim, so both `repo_root` and the evidence clone
    keep depending on the same external object database. `--dissociate`
    makes Git copy every object reachable through an inherited alternate
    into the clone's own object store and drop the alternate file, so
    the resulting clone never depends on `repo_root`'s object database by
    any path -- hardlink or alternate.

    The initial clone itself is also pinned to a throwaway empty
    `core.hooksPath` (`OPUS-R136-M01`): the clone's own destination
    `.git/hooks/` does not exist yet when the clone command runs, so it
    cannot be pinned to that not-yet-created path the way every later
    call is pinned to it. Without an explicit pin here, inherited
    global/system Git configuration could reintroduce an executable hook
    path during the one Git invocation that had no pin at all.

    The checkout is verified byte-identical to `commit`
    (`_verify_pinned_worktree_clean`) before this function returns, so a
    still-successful hook injection during this specific clone's own
    checkout fails closed here rather than silently producing evidence
    from unreviewed bytes. Caller owns cleanup of a successfully returned
    `scratch_dir` via `_remove_pinned_worktree`; a failure raised from
    within this function (clone, checkout, or the cleanliness check
    itself) removes its own partial `scratch_dir` before propagating,
    since the caller never receives a path to clean up in that case
    (`OPUS-R134-M03`)."""
    scratch_dir = Path(tempfile.mkdtemp(prefix="wfo-ledger-evidence-worktree-"))
    materialized = False
    try:
        clone_hooks_dir = Path(tempfile.mkdtemp(prefix="wfo-ledger-evidence-clone-hooks-"))
        try:
            _run(
                [
                    "git", "-c", f"core.hooksPath={clone_hooks_dir}", "clone", "--quiet",
                    "--no-hardlinks", "--dissociate", "--no-checkout", str(repo_root), str(scratch_dir),
                ],
                cwd=repo_root,
            )
        finally:
            shutil.rmtree(clone_hooks_dir, ignore_errors=True)
        hooks_dir = scratch_dir / ".git" / "hooks"
        shutil.rmtree(hooks_dir, ignore_errors=True)
        hooks_dir.mkdir(parents=True, exist_ok=True)
        _hardened_run(
            ["-c", f"core.hooksPath={hooks_dir}", "checkout", "--quiet", "--detach", commit],
            cwd=scratch_dir,
        )
        _verify_pinned_worktree_clean(scratch_dir, commit)
        materialized = True
    finally:
        if not materialized:
            shutil.rmtree(scratch_dir, ignore_errors=True)
    return scratch_dir


def _remove_pinned_worktree(repo_root: Path, scratch_dir: Path) -> None:
    """Undoes `_materialize_pinned_worktree_at_commit`. `scratch_dir` is
    now an independent clone, not a worktree linked to `repo_root`'s own
    common Git directory (`GPT-R132-001`), so cleanup is a plain
    recursive removal -- there is no `git worktree` registration against
    `repo_root` left to unregister. `repo_root` is kept as a parameter
    for call-site stability even though this implementation no longer
    uses it."""
    del repo_root
    shutil.rmtree(scratch_dir, ignore_errors=True)


def _run_named_test_in_scratch(scratch_dir: Path, test_id: str) -> tuple[bool, str]:
    """Same "resolvable, executable, re-executes green" requirement
    `_load_and_run_named_test` enforces for `WFR-69`'s live-working-tree
    pre-flight, run instead against an already-materialized pinned-commit
    worktree (`_materialize_pinned_worktree_at_commit`) -- `WFO-LEDGER-
    COVERAGE` property (ii)'s own approval-bound evidence re-execution,
    which must never trust the live working tree (item 358(c)'s isolation
    rationale applies identically here). A fresh, isolated interpreter per
    test id, exactly as `_load_and_run_named_test` uses; `cwd` is the
    worktree root (not `scratch_dir / "scripts"`) so a `_repo_root()`-style
    evidence test's own `git rev-parse --show-toplevel` resolves the
    pinned worktree, not this process's live one."""
    driver_dir = Path(tempfile.mkdtemp(prefix="wfo-ledger-evidence-driver-"))
    try:
        driver_path = driver_dir / "run_evidence.py"
        driver_path.write_text(
            "import json, sys, unittest\n"
            f"sys.path.insert(0, {str(scratch_dir / 'scripts')!r})\n"
            "try:\n"
            f"    _suite = unittest.TestLoader().loadTestsFromName({test_id!r})\n"
            "except Exception as _exc:\n"
            "    print(json.dumps({'ok': False, 'detail': 'unresolvable: ' + repr(_exc)}))\n"
            "    sys.exit(0)\n"
            "if _suite.countTestCases() == 0:\n"
            "    print(json.dumps({'ok': False, 'detail': 'resolved to zero test cases'}))\n"
            "    sys.exit(0)\n"
            "_result = unittest.TestResult()\n"
            "_suite.run(_result)\n"
            "_ok = _result.wasSuccessful()\n"
            "_detail = '' if _ok else str(_result.failures + _result.errors)\n"
            "print(json.dumps({'ok': _ok, 'detail': _detail}))\n"
        )
        env = {"PATH": os.environ.get("PATH", "")}
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-S", str(driver_path)],
            capture_output=True, text=True, env=env, timeout=120, cwd=str(scratch_dir),
        )
    finally:
        shutil.rmtree(driver_dir, ignore_errors=True)

    stdout = proc.stdout.strip()
    if not stdout:
        return False, f"isolated evidence process produced no output: rc={proc.returncode} stderr={proc.stderr!r}"
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return False, f"isolated evidence process produced non-JSON output: {stdout!r}"
    return bool(payload.get("ok")), payload.get("detail", "")


def verify_wfo_ledger_coverage(repo_root: str | Path, commit: str) -> dict:
    """`WFO-LEDGER-COVERAGE`'s bound conformance (`WFR-68` properties
    (i)-(iv); `WF8c` item (m)'s remaining scope) -- re-derives, at the
    pinned `commit`, whether `docs/ai-workflow/registry/workflow-v2-1-
    core-ledger-status.json` is a total, injective, honest transcription
    of `WORKFLOW_V2_PLAN.md`'s own '### Reconciliation table' at that
    same commit.

    (i) **Totality and uniqueness**: every item the reconciliation table
    names (`parse_reconciliation_table`, never the artifact's own
    enumeration) has exactly one ledger entry; a table item with no
    ledger entry (the omission attack), a ledger entry naming no real
    table item, or two ledger entries for the same item (the duplication
    attack, `OPUS-R103-003`) are each a named `FAIL`.

    (ii) **Evidence for every non-`SUPERSEDED`/`none` item** (widened,
    `GPT-R131-002`, to match `WFR-68` property (ii)'s own text -- an
    `IMPLEMENTED` entry must be evidenced "exactly as a `WF8c`-owned entry
    must"): an entry whose ledger `status` is `IMPLEMENTED`, `ABSENT`, or
    `PARTIAL` must name a test id -- its own `evidence` field, or (only
    when that is absent) the companion `<work_item_id>-wf8c-evidence.json`'s
    entry for the same item -- that resolves and re-executes green against
    a real, pinned, detached Git worktree checked out at the commit being
    verified, in a fresh isolated interpreter; an unevidenced or failing
    entry is `FAIL`, never a pass (the relabel attack, and -- previously
    unchecked here at all -- the same silent-omission attack against the
    96 real `WF8c`-owned `ABSENT`/`PARTIAL` items this property now also
    covers). Only `SUPERSEDED` with owner `none` is exempt, satisfied by
    the approved reconciliation table's own recorded supersession alone
    (the identical disposition `WFR-69`'s own pre-flight already treats
    this way).

    (iii)/(iv) **Monotonicity and governance scope**: for every item
    whose ledger `status` differs from the table's own recorded status,
    the *only* permitted difference is an evidenced move to `IMPLEMENTED`
    -- catching the downward-relabel attack (`GPT-R106-001`, e.g. a
    `SUPERSEDED`/`none` rewrite of a still-open item with no corresponding
    plan-revision change) and any other divergence alike. `owner_
    checkpoint` must equal the table's own resolved owner for every item
    regardless of status, since only a fresh approved plan revision may
    change it. The table itself *is* this obligation's baseline --
    property (iv)'s own stated rule for "no prior artifact exists yet",
    true throughout `WF8c`'s own first build of this verifier, since the
    table's per-item `(status, owner)` pairs have not changed since
    revision 82 and no prior `ledger-status.json` was ever itself the
    subject of a technical approval."""
    repo_root = Path(repo_root)
    failing: list[str] = []

    try:
        table = parse_reconciliation_table(repo_root, commit)
    except ReconciliationTableParseError as exc:
        detail = f"reconciliation table unparseable at {commit}: {exc}"
        return {"status": "FAIL", "detail": detail, "failing_assertions": [detail]}

    ledger_path = ledger_status_path_for_work_item(LEDGER_COVERAGE_WORK_ITEM_ID)
    try:
        ledger = _git_show_json_at_commit(repo_root, commit, ledger_path)
    except json.JSONDecodeError as exc:
        detail = f"{ledger_path} malformed at {commit}: {exc}"
        return {"status": "FAIL", "detail": detail, "failing_assertions": [detail]}
    if not isinstance(ledger, dict) or not isinstance(ledger.get("entries"), list):
        detail = f"{ledger_path} does not exist, or has no 'entries' list, at {commit}"
        return {"status": "FAIL", "detail": detail, "failing_assertions": [detail]}

    companion_path = wf8c_evidence_path_for_work_item(LEDGER_COVERAGE_WORK_ITEM_ID)
    try:
        companion = _git_show_json_at_commit(repo_root, commit, companion_path)
    except json.JSONDecodeError as exc:
        detail = f"{companion_path} malformed at {commit}: {exc}"
        return {"status": "FAIL", "detail": detail, "failing_assertions": [detail]}
    companion_evidence: dict[int, str] = {}
    if isinstance(companion, dict):
        for record in companion.get("entries", []):
            if not isinstance(record, dict):
                continue
            c_item, c_ev = record.get("item"), record.get("evidence")
            if isinstance(c_item, int) and isinstance(c_ev, str) and c_ev:
                companion_evidence.setdefault(c_item, c_ev)

    occurrences: dict[int, int] = {}
    by_item: dict[int, dict] = {}
    for record in ledger["entries"]:
        if not isinstance(record, dict) or not isinstance(record.get("item"), int):
            failing.append(f"ledger entry with no well-formed integer 'item': {record!r}")
            continue
        item = record["item"]
        occurrences[item] = occurrences.get(item, 0) + 1
        by_item.setdefault(item, record)

    for item, count in sorted(occurrences.items()):
        if count > 1:
            failing.append(f"item {item}: {count} ledger entries name the same item (duplicate entry)")

    table_items, ledger_items = set(table.keys()), set(occurrences.keys())
    for item in sorted(table_items - ledger_items):
        failing.append(f"item {item}: in the reconciliation table but has no ledger entry (omission)")
    for item in sorted(ledger_items - table_items):
        failing.append(f"item {item}: ledger entry names no real reconciliation-table item")

    for item, table_row in sorted(table.items()):
        if occurrences.get(item) != 1:
            continue  # already flagged above (duplicate or missing)
        entry = by_item[item]
        entry_status, entry_owner = entry.get("status"), entry.get("owner_checkpoint")
        table_status, table_owner = table_row["status"], table_row["owner"]

        # (iii)/(iv) governance scope: the ledger's status must equal the
        # table's own, or be an evidenced upgrade to IMPLEMENTED -- no
        # other divergence is ever permitted, regardless of whether the
        # table itself already says IMPLEMENTED (a historically-delivered
        # item) or something still open.
        if entry_status != table_status and entry_status != "IMPLEMENTED":
            failing.append(
                f"item {item}: ledger status {entry_status!r} diverges from the reconciliation "
                f"table's {table_status!r} -- the only permitted divergence is an evidenced move "
                f"to IMPLEMENTED"
            )
            continue

        # (iv) owner_checkpoint must always equal the table's own
        # resolved owner -- only a fresh approved plan revision may
        # change it, whatever the entry's status.
        if entry_owner != table_owner:
            failing.append(
                f"item {item}: ledger owner_checkpoint {entry_owner!r} disagrees with the "
                f"reconciliation table's {table_owner!r}"
            )
            continue

        # (ii) evidence for every non-SUPERSEDED/none item (widened,
        # GPT-R131-002, to match WFR-69's own status-conditional rule
        # and WFR-68 property (ii)'s own text -- an IMPLEMENTED entry
        # must be evidenced "exactly as a WF8c-owned entry must"):
        # SUPERSEDED/none is the only disposition no evidence source
        # can ever discharge, satisfied by the table's own recorded
        # supersession alone; every other status -- IMPLEMENTED or a
        # WF8c-owned ABSENT/PARTIAL -- must be evidenced whether the
        # table already said so (historically-delivered/frozen-baseline)
        # or this is a fresh upgrade from an open status.
        if entry_status == "SUPERSEDED" and entry_owner == "none":
            continue
        entry_evidence = entry.get("evidence")
        evidence = entry_evidence if isinstance(entry_evidence, str) and entry_evidence else companion_evidence.get(item)
        if not isinstance(evidence, str) or not evidence:
            failing.append(f"item {item}: {entry_status} with no evidence entry")
            continue

        # OPUS-R133-001: a fresh, independent clone per evidence id,
        # materialized and torn down within this one iteration -- never
        # reused across evidence ids. There is no "restore between ids"
        # step left to bypass: no evidence id's own execution (a tracked-
        # file rewrite, a clone-local Git config/filter/attribute/hook)
        # can leave anything behind for a later evidence id to inherit,
        # because the checkout it ran in no longer exists by the time the
        # next one is materialized.
        scratch_dir = _materialize_pinned_worktree_at_commit(repo_root, commit)
        try:
            ok, detail = _run_named_test_in_scratch(scratch_dir, evidence)
        finally:
            _remove_pinned_worktree(repo_root, scratch_dir)
        if not ok:
            failing.append(f"item {item}: evidence {evidence!r} does not re-execute green: {detail}")

    if failing:
        return {"status": "FAIL", "detail": "; ".join(failing), "failing_assertions": failing}
    return {
        "status": "PASS",
        "detail": f"all {len(table)} reconciliation-table items accounted for in {ledger_path}",
        "failing_assertions": [],
    }


# ---------------------------------------------------------------------------
# The verifier census is a dependency closure, not a file list (item 361,
# `GPT-R79-002`)
# ---------------------------------------------------------------------------


def _blob_exists_at_commit(repo_root: Path, commit: str, path: str) -> bool:
    env = dict(os.environ)
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", "--no-replace-objects", "cat-file", "-e", f"{commit}:{path}"],
        cwd=repo_root, env=env, capture_output=True,
    )
    return result.returncode == 0


def _module_candidate_paths(source_root: str, dotted_name: str) -> list[str]:
    base = dotted_name.replace(".", "/")
    return [f"{source_root}/{base}.py", f"{source_root}/{base}/__init__.py"]


def _package_tracked_subtree(repo_root: Path, commit: str, package_dir: str) -> list[str]:
    return [e["path"] for e in _ls_tree_at_commit(repo_root, commit, package_dir + "/") if e["path"].endswith(".py")]


def _follow_import_name(
    repo_root: Path, commit: str, source_root: str, dotted_name: str,
    pending: list[str], seen: set[str],
) -> None:
    """Resolves `dotted_name`'s top-level component against `source_root`
    at `commit`. A name that does not resolve there is a stdlib/
    third-party dependency, legitimately outside the repository-local
    closure (item 361(a)) -- isolated execution (item 358(c)) is what
    catches a *wrongly* omitted repository-local dependency, not this
    resolver refusing an ordinary standard-library import."""
    top = dotted_name.split(".")[0]
    for candidate in _module_candidate_paths(source_root, top):
        if not _blob_exists_at_commit(repo_root, commit, candidate):
            continue
        if candidate.endswith("/__init__.py"):
            if candidate not in seen:
                pending.append(candidate)
            package_dir = candidate[: -len("/__init__.py")]
            for sub in _package_tracked_subtree(repo_root, commit, package_dir):
                if sub not in seen and sub != candidate:
                    pending.append(sub)
        elif candidate not in seen:
            pending.append(candidate)
        return


def _static_import_closure(repo_root: Path, commit: str, entry_path: str, source_root: str) -> list[str]:
    """Items 361(a)/(b)/(c)/(d): static `ast` import closure over blobs at
    `commit`, seeded at `entry_path`, following every `import X`/`from X
    import ...` -- module level, function bodies, class bodies, `try:`
    blocks alike -- that resolves to a tracked module or package on
    `source_root`, transitively. A construct static analysis cannot
    follow (`importlib.import_module`, a bare `__import__`, `exec`, a
    relative import) raises `VerifierClosureUnresolvableError` rather
    than being censused optimistically. Returns the ordered list of
    repository-local paths visited (entry point first)."""
    visited: list[str] = []
    seen: set[str] = set()
    pending = [entry_path]
    while pending:
        path = pending.pop(0)
        if path in seen:
            continue
        seen.add(path)
        visited.append(path)
        if not _blob_exists_at_commit(repo_root, commit, path):
            raise VerifierClosureUnresolvableError(f"{path!r} not found at {commit}")
        text = _hardened_run(["show", f"{commit}:{path}"], cwd=repo_root)
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            raise VerifierClosureUnresolvableError(f"{path!r}: cannot parse at {commit} -- {exc}") from exc

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _follow_import_name(repo_root, commit, source_root, alias.name, pending, seen)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    raise VerifierClosureUnresolvableError(
                        f"{path!r}: relative import (level {node.level}) cannot be "
                        f"followed by static closure analysis"
                    )
                if node.module:
                    _follow_import_name(repo_root, commit, source_root, node.module, pending, seen)
            elif isinstance(node, ast.Call):
                func = node.func
                dynamic_name = None
                if isinstance(func, ast.Attribute) and func.attr == "import_module":
                    dynamic_name = "importlib.import_module"
                elif isinstance(func, ast.Name) and func.id in ("__import__", "exec", "eval"):
                    dynamic_name = func.id
                if dynamic_name:
                    raise VerifierClosureUnresolvableError(
                        f"{path!r}: dynamic-import construct {dynamic_name}(...) "
                        f"cannot be followed by static closure analysis"
                    )
    return visited


def _census_module_names(census: list[dict]) -> list[str]:
    """The importable module name each census entry materializes to under
    `VERIFIER_SOURCE_ROOT`, for the isolated driver's own after-the-fact
    "nothing resolved outside the scratch tree" self-check (item 361(e))."""
    names = []
    prefix = VERIFIER_SOURCE_ROOT.rstrip("/") + "/"
    for entry in census:
        rel = entry["path"]
        rel = rel[len(prefix):] if rel.startswith(prefix) else rel
        if rel.endswith("/__init__.py"):
            names.append(rel[: -len("/__init__.py")].replace("/", "."))
        elif rel.endswith(".py"):
            names.append(rel[:-3].replace("/", "."))
    return names


def _materialize_census(scratch_dir: Path, repo_root: Path, census: list[dict]) -> None:
    """Writes every `{path, blob}` census entry's bytes into `scratch_dir`,
    preserving the module/package import layout relative to
    `VERIFIER_SOURCE_ROOT`. Reads each blob directly by its recorded sha
    (`git cat-file blob <blob>`), never via `<commit>:<path>` -- so replay
    (`replay_completion_obligation`) materializes the *recorded* bytes
    regardless of what `path` resolves to in later history (item
    356(u)/361(i))."""
    prefix = VERIFIER_SOURCE_ROOT.rstrip("/") + "/"
    for entry in census:
        rel = entry["path"]
        rel = rel[len(prefix):] if rel.startswith(prefix) else rel
        dest = scratch_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_hardened_run_bytes(["cat-file", "blob", entry["blob"]], cwd=repo_root))


def _build_verifier_driver(
    scratch_dir: Path, entry_module: str, conformance_name: str,
    repo_root: Path, commit: str, module_names: list[str],
) -> str:
    """A tiny, self-contained driver script (never itself part of the
    census/closure -- it carries no authority, it only sets `sys.path`
    and calls the recorded verifier) that the isolated interpreter
    executes: imports the materialized verifier entry module, calls its
    named conformance function, and asserts from *inside itself* that
    every census module resolved inside the scratch tree (item 361(e))
    before printing the JSON result."""
    return (
        "import json, sys\n"
        f"sys.path.insert(0, {str(scratch_dir)!r})\n"
        "try:\n"
        f"    import {entry_module} as _verifier_module\n"
        f"    _result = _verifier_module.{conformance_name}({str(repo_root)!r}, {commit!r})\n"
        "except Exception as _exc:\n"
        "    print(json.dumps({'status': 'ERROR', 'detail': repr(_exc)}))\n"
        "    sys.exit(1)\n"
        f"_scratch = {str(scratch_dir)!r}\n"
        f"for _name in {module_names!r}:\n"
        "    _mod = sys.modules.get(_name)\n"
        "    _f = getattr(_mod, '__file__', None) if _mod is not None else None\n"
        "    if _f is not None and not str(_f).startswith(_scratch):\n"
        "        print(json.dumps({'status': 'ERROR', 'detail': "
        "'module ' + _name + ' resolved outside the scratch tree: ' + str(_f)}))\n"
        "        sys.exit(1)\n"
        "print(json.dumps(_result))\n"
    )


def _run_verifier_driver(scratch_dir: Path, driver_src: str, *, extra_env: dict | None = None) -> dict:
    """Executes `driver_src` in a **fresh interpreter process** -- never
    in-process, so an already-imported module cannot mask a missing
    dependency -- launched in Python's isolated mode (`-I`, ignoring
    `PYTHONPATH` and user site directories even when `extra_env` sets one
    deliberately, item 358(c)/361(f)), with no bytecode writing (`-B`)
    and no site processing (`-S`). `sys.path` is reset inside the driver
    itself to the scratch tree plus the standard library alone -- the
    live repository is not a module source."""
    driver_dir = Path(tempfile.mkdtemp(prefix="wfo-verifier-driver-"))
    try:
        driver_path = driver_dir / "run_verifier.py"
        driver_path.write_text(driver_src)
        env = {"PATH": os.environ.get("PATH", "")}
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-S", str(driver_path)],
            capture_output=True, text=True, env=env, timeout=120,
        )
    finally:
        shutil.rmtree(driver_dir, ignore_errors=True)

    stdout = result.stdout.strip()
    if not stdout:
        raise VerifierExecutionError(
            f"isolated verifier process produced no output: rc={result.returncode} "
            f"stderr={result.stderr!r}"
        )
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise VerifierExecutionError(
            f"isolated verifier process produced non-JSON output: {stdout!r}"
        ) from exc
    if payload.get("status") == "ERROR":
        raise VerifierExecutionError(payload.get("detail", "unknown isolated-verifier error"))
    return payload


def _execute_verifier_isolated(
    repo_root: Path, commit: str, census: list[dict], conformance_name: str,
    *, extra_env: dict | None = None,
) -> dict:
    """Materializes `census` from live Git objects at `commit` and
    executes the named conformance function in isolation (items 358(c),
    361(e))."""
    scratch_dir = Path(tempfile.mkdtemp(prefix="wfo-verifier-scratch-"))
    try:
        _materialize_census(scratch_dir, repo_root, census)
        entry_module = Path(VERIFIER_ENTRY).stem
        driver_src = _build_verifier_driver(
            scratch_dir, entry_module, conformance_name, repo_root, commit,
            _census_module_names(census),
        )
        return _run_verifier_driver(scratch_dir, driver_src, extra_env=extra_env)
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def replay_completion_obligation(repo_root: Path, obligation_id: str, recorded: dict) -> dict:
    """Item 356(u)/(z): re-executes the **recorded** `verifier_census` --
    read by blob sha, never by `<commit>:<path>`, so later history cannot
    substitute different bytes for the same path -- and reproduces the
    recorded verdict exactly, regardless of what today's live
    `scripts/workflow_state.py`/`workflow_fingerprint.py` say. Never calls
    today's `derive_obligation_verdict`-equivalent with the recorded
    commit; it materializes and runs the exact recorded implementation. A
    recorded census blob that is genuinely no longer reachable classifies
    `REPLAY_UNRESOLVABLE` rather than falling back to whatever is
    running now."""
    conformance_name = COMPLETION_OBLIGATION_CONFORMANCE.get(obligation_id)
    if conformance_name is None:
        return {"status": "UNKNOWN_OBLIGATION"}
    census = recorded["verifier_census"]
    scratch_dir = Path(tempfile.mkdtemp(prefix="wfo-verifier-replay-"))
    try:
        try:
            _materialize_census(scratch_dir, repo_root, census)
        except subprocess.CalledProcessError as exc:
            return {"status": "REPLAY_UNRESOLVABLE", "detail": str(exc)}
        entry_module = Path(VERIFIER_ENTRY).stem
        driver_src = _build_verifier_driver(
            scratch_dir, entry_module, conformance_name, repo_root,
            recorded["source_commit"], _census_module_names(census),
        )
        try:
            return _run_verifier_driver(scratch_dir, driver_src)
        except VerifierExecutionError as exc:
            return {"status": "REPLAY_UNRESOLVABLE", "detail": str(exc)}
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# The gate itself: authority resolution, the DIVERGED precondition, and
# verdict derivation (items 356, 358, 360)
# ---------------------------------------------------------------------------

_TECHNICAL_APPROVAL_AUTHORITY_FIELDS = (
    "status", "basis", "approved_review_content_id", "review_content_manifest",
    "reviewed_content_commit", "reviewed_bundle_id", "user_confirmation",
    "legacy_evidence", "waived_guarantees",
)


class ObligationVerdict(NamedTuple):
    """`derive_obligation_verdict(obligation_id, subject_identity,
    verifier_identity)`'s result, extended with the audit fields
    `complete_work_item` records into `completion_obligations_accepted`
    (item 356(m))."""
    classification: str
    detail: str
    source_commit: str | None = None
    obligation_content_id: str | None = None
    verifier_identity_id: str | None = None
    verifier_entry: str | None = None
    verifier_source_root: str | None = None
    verifier_census: tuple[dict, ...] = ()
    technical_approval_commit: str | None = None
    reviewed_content_commit: str | None = None
    approved_review_content_id: str | None = None
    base_commit: str | None = None
    writer_count: int | None = None
    failing_assertions: tuple[str, ...] = ()


def _working_tree_diverges_from_commit(repo_root: Path, commit: str, surface_census) -> bool:
    """Item 356's `DIVERGED` precondition: every path on a declared writer
    surface must be byte-identical between the working tree and `commit`,
    and no untracked file may exist on a surface. A precondition, never a
    race fix -- the derivation itself never reads the working tree at
    all; this only ever causes a refusal, keeping "the content that was
    validated" and "the content that is installed" the same thing at
    evaluation time."""
    for entry in surface_census:
        full = repo_root / entry["path"]
        if not full.is_file() or full.is_symlink():
            return True
        if fingerprint._hash_object(repo_root, entry["path"]) != entry["blob"]:
            return True
    known = {e["path"] for e in surface_census}
    for prefix in STATE_WRITER_SURFACE_PREFIXES:
        # No `--exclude-standard`: a `.gitignore`d file on a declared surface
        # must still be visible to this scan (OPUS-R101-003 -- an ignored
        # thirteenth writer would otherwise hide from both this precondition
        # and the census, changing nothing about `obligation_content_id`).
        # The one genuinely ignorable byproduct this repository's own
        # surfaces produce is `scripts/__pycache__/*.pyc` (created merely by
        # importing/running the census's own `.py` files) -- excluded by
        # name, not by `.gitignore` membership, since it can never be
        # mistaken for a real `.py`/`.md` surface source file.
        out = _run(["git", "ls-files", "--others", "-z", "--", prefix], cwd=repo_root)
        for path in out.split("\x00"):
            if not path:
                continue
            if prefix == "scripts/" and path.endswith("_test.py"):
                continue
            if path.endswith(".pyc") or "/__pycache__/" in path:
                continue
            if path not in known:
                return True
    return False


def _resolve_one_obligation(repo_root: Path, work_item: dict, obligation_id: str, commit: str) -> ObligationVerdict:
    conformance_name = COMPLETION_OBLIGATION_CONFORMANCE.get(obligation_id)
    if conformance_name is None:
        return ObligationVerdict("UNKNOWN_OBLIGATION", f"no conformance is bound to {obligation_id!r}")

    owner_id = work_item["work_item_id"]

    try:
        recomputed_id = approval_review_content_id(
            repo_root, stage="implementation", base_commit=work_item["base_commit"],
            work_item_type=work_item["work_item_type"], work_item_id=owner_id, head=commit,
            artifacts_path=fingerprint.artifacts_path_for_work_item(owner_id),
        )
    except Exception as exc:  # noqa: BLE001 -- any failure to evaluate at all is VERIFIER_UNRESOLVABLE (item 358(d))
        return ObligationVerdict("VERIFIER_UNRESOLVABLE", f"cannot recompute implementation-stage identity at {commit}: {exc!r}")

    try:
        approval_commit = discover_technical_approval_commit(
            repo_root, owner_id, recomputed_id, work_item["base_commit"], commit,
        )
    except AmbiguousApprovalTrailerError as exc:
        return ObligationVerdict("VERIFIER_UNAPPROVED", f"ambiguous Workflow-Technical-Approval commits: {exc}")
    if approval_commit is None or not _is_ancestor(repo_root, approval_commit, commit):
        return ObligationVerdict(
            "VERIFIER_UNAPPROVED",
            "no reachable, unambiguous Workflow-Technical-Approval commit carries "
            "the recomputed implementation-stage identity",
        )

    try:
        durable_state = json.loads(
            _hardened_run(["cat-file", "blob", f"{approval_commit}:docs/ai-workflow/WORKFLOW_STATE.json"], cwd=repo_root)
        )
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return ObligationVerdict("VERIFIER_UNAPPROVED", f"committed WORKFLOW_STATE.json at {approval_commit} is unreadable/malformed: {exc}")
    durable_owner = durable_state.get("work_items", {}).get(owner_id)
    durable_record = (durable_owner or {}).get("technical_approval")
    if durable_record is None:
        return ObligationVerdict("VERIFIER_UNAPPROVED", f"{approval_commit} commits no technical_approval record for {owner_id!r}")

    if durable_record.get("status") != "CURRENT":
        return ObligationVerdict("VERIFIER_UNAPPROVED", "durable technical_approval.status != CURRENT")
    if durable_record.get("approved_review_content_id") != recomputed_id:
        return ObligationVerdict("VERIFIER_UNAPPROVED", "durable approved_review_content_id does not match the recomputed identity")
    reviewed_content_commit = durable_record.get("reviewed_content_commit")
    if not (isinstance(reviewed_content_commit, str) and _GIT_OBJECT_ID_RE.match(reviewed_content_commit)):
        return ObligationVerdict("VERIFIER_UNAPPROVED", "durable reviewed_content_commit is null or malformed")
    # workflow-v2-3-followups continued scope, external cross-model review
    # round 4 (I2's committed-blob half, OPUS-R25-007's own precedent):
    # `durable_record` was read from a historical Git blob (`approval_commit`
    # above), never from the live WORKFLOW_STATE.json validate_state ever
    # sees -- a review_content_manifest malformed at approval time and now
    # immutable history reaches this exact iteration below regardless of
    # what the live file holds. Shape-check before the dict comprehension
    # ever runs, returning the same typed VERDICT every other refusal in
    # this function already returns, never a bare AttributeError.
    shape_error = _describe_malformed_review_content_manifest_shape(
        durable_record.get("review_content_manifest")
    )
    if shape_error is not None:
        return ObligationVerdict("VERIFIER_UNAPPROVED", f"durable review_content_manifest is malformed: {shape_error}")
    manifest = durable_record.get("review_content_manifest") or []
    if not manifest:
        return ObligationVerdict("VERIFIER_UNAPPROVED", "durable review_content_manifest is empty")

    live_record = work_item.get("technical_approval")
    if live_record is None:
        return ObligationVerdict("VERIFIER_UNAPPROVED", "no live technical_approval record")
    diverging = [f for f in _TECHNICAL_APPROVAL_AUTHORITY_FIELDS if live_record.get(f) != durable_record.get(f)]
    if diverging:
        return ObligationVerdict("VERIFIER_UNAPPROVED", f"live technical_approval diverges from the durable record on: {diverging}")
    if work_item.get("base_commit") != (durable_owner or {}).get("base_commit"):
        return ObligationVerdict("VERIFIER_UNAPPROVED", "live base_commit does not equal the one the approval commit committed")

    try:
        closure_paths = _static_import_closure(repo_root, commit, VERIFIER_ENTRY, VERIFIER_SOURCE_ROOT)
    except VerifierClosureUnresolvableError as exc:
        return ObligationVerdict("VERIFIER_UNRESOLVABLE", str(exc))

    manifest_by_path = {entry.get("path"): entry for entry in manifest}
    census: list[dict] = []
    for path in closure_paths:
        found = _blob_mode_and_sha_at_commit(repo_root, commit, path)
        if found is None:
            return ObligationVerdict("VERIFIER_UNAPPROVED", f"verifier dependency {path!r} absent at {commit}")
        mode, blob_at_c = found
        if path in manifest_by_path:
            approved_blob = manifest_by_path[path].get("blob")
            if blob_at_c != approved_blob:
                return ObligationVerdict(
                    "VERIFIER_UNAPPROVED",
                    f"verifier dependency {path!r} at {commit} ({blob_at_c}) is not "
                    f"the approved blob ({approved_blob})",
                )
            source = "manifest"
        else:
            base_found = _blob_mode_and_sha_at_commit(repo_root, work_item["base_commit"], path)
            if base_found is None or base_found[1] != blob_at_c:
                return ObligationVerdict(
                    "VERIFIER_UNAPPROVED",
                    f"verifier dependency {path!r} is outside the approved manifest "
                    f"and has changed since base_commit",
                )
            source = "unchanged_since_base"
        census.append({"path": path, "mode": mode, "blob": blob_at_c, "source": source})

    verifier_identity_id = _sha256_canonical({
        "verifier_entry": VERIFIER_ENTRY, "verifier_source_root": VERIFIER_SOURCE_ROOT, "verifier_census": census,
    })

    try:
        discovery = discover_state_writers(repo_root, commit)
    except StateWriterDeclarationError as exc:
        return ObligationVerdict("UNRESOLVABLE", f"discover_state_writers failed at {commit}: {exc}")
    if _working_tree_diverges_from_commit(repo_root, commit, discovery.surface_census):
        return ObligationVerdict("DIVERGED", "the working tree differs from the pinned commit on a declared writer surface")

    conformance_blob = fingerprint._hash_object(repo_root, VERIFIER_ENTRY)
    obligation_content_id = _sha256_canonical({
        "obligation_id": obligation_id,
        "surfaces": list(STATE_WRITER_SURFACE_PREFIXES),
        "surface_census": [dict(e) for e in discovery.surface_census],
        "writers": list(discovery.writers),
        "publisher": discovery.publisher,
        "conformance_blob": conformance_blob,
    })

    try:
        result = _execute_verifier_isolated(repo_root, commit, census, conformance_name)
    except VerifierExecutionError as exc:
        return ObligationVerdict("VERIFIER_UNRESOLVABLE", str(exc))

    classification = "PASS" if result.get("status") == "PASS" else "FAIL"
    return ObligationVerdict(
        classification, result.get("detail", ""), source_commit=commit,
        obligation_content_id=obligation_content_id, verifier_identity_id=verifier_identity_id,
        verifier_entry=VERIFIER_ENTRY, verifier_source_root=VERIFIER_SOURCE_ROOT,
        verifier_census=tuple(census), technical_approval_commit=approval_commit,
        reviewed_content_commit=reviewed_content_commit, approved_review_content_id=recomputed_id,
        base_commit=work_item.get("base_commit"), writer_count=len(discovery.writers),
        failing_assertions=tuple(result.get("failing_assertions", ())),
    )


def resolve_completion_obligations(
    repo_root: Path, work_item: dict, *, expected_commit: str | None = None,
) -> dict[str, ObligationVerdict]:
    """`derive_obligation_verdict(obligation_id, subject_identity,
    verifier_identity)`, for every obligation the work item's own
    authoritatively-loaded registry declares (item 356). No `registry`
    parameter and no `commit` parameter: both are resolved internally
    (the registry through the same authoritative
    `_load_authoritative_registry_or_none` resolution
    `resolve_own_registry_completion_status` uses; the commit as the
    live `HEAD`) so a caller cannot hand in a registry declaring no
    obligations, or an identity at which a stale conformance once
    passed, and satisfy the gate vacuously. `expected_commit`, if given,
    is an **assertion** only -- a mismatch against the resolved `HEAD`
    classifies every obligation `PIN_MOVED`, never a redirection of the
    derivation. Returns `{}` for a work item that declares no obligation
    (the verifier-authority requirement attaches to obligations, never to
    work items -- item 358's "Authority is resolved per declared
    obligation")."""
    registry = _load_authoritative_registry_or_none(repo_root, work_item)
    obligation_ids = sorted({
        oid
        for checkpoint in (registry or {}).get("checkpoints", [])
        for oid in checkpoint.get("completion_obligations", [])
    })
    if not obligation_ids:
        return {}

    try:
        head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
    except subprocess.CalledProcessError as exc:
        return {oid: ObligationVerdict("UNRESOLVABLE", f"cannot resolve HEAD: {exc}") for oid in obligation_ids}
    if expected_commit is not None and expected_commit != head:
        return {
            oid: ObligationVerdict("PIN_MOVED", f"expected_commit {expected_commit!r} != live HEAD {head!r}")
            for oid in obligation_ids
        }

    return {oid: _resolve_one_obligation(repo_root, work_item, oid, head) for oid in obligation_ids}


def completion_obligations_satisfied(repo_root: Path, work_item: dict) -> tuple[bool, list[str]]:
    """`True` only when **every** declared obligation derives `PASS`;
    otherwise `False` plus the sorted outstanding obligation ids. A work
    item declaring no obligation returns `(True, [])` without resolving
    any technical approval at all (item 358, stress pass 3)."""
    verdicts = resolve_completion_obligations(repo_root, work_item)
    outstanding = sorted(oid for oid, v in verdicts.items() if v.classification != "PASS")
    return (not outstanding, outstanding)


def work_item_completion_status(repo_root: Path, work_item: dict) -> tuple[bool, str | None, list[str]]:
    """The single authoritative terminal predicate: terminal **iff** the
    registry status is terminal **and** every completion obligation
    derives `PASS`. Composes `resolve_own_registry_completion_status`
    and `completion_obligations_satisfied` rather than re-deriving either
    (`select_next_checkpoint`/`registry_completion_status` keep their
    exact current, registry-only semantics unchanged -- item 356(f)).
    Performs no locking of its own: it is called *by* the terminal
    transition while that transition already holds `state_lock`, and the
    conformance it runs never touches the live state file or lock, so it
    cannot deadlock against its own caller."""
    is_terminal, outstanding_checkpoint_id = resolve_own_registry_completion_status(repo_root, work_item)
    satisfied, outstanding_obligations = completion_obligations_satisfied(repo_root, work_item)
    return (is_terminal and satisfied, outstanding_checkpoint_id, outstanding_obligations)


def complete_work_item(state: dict, work_item_id: str, now: str, *, repo_root: Path) -> dict:
    """D1's completion/reset text: on `MILESTONE_COMPLETE` (or
    process-completion archival), the entry's phase becomes terminal and,
    if it was `active_work_item_id`, that pointer resets to `null` so the
    next `/milestone-plan` creates a fresh entry and claims the pointer.
    Returns a new state dict.

    D-Functional-Remediation's "parent acceptance blocks on an incomplete
    child" rule (resolves `GPT-R9-016`, `WFR-35`): refuses outright, naming
    every still-incomplete child, rather than completing a parent whose
    broad remediation work is still open elsewhere.

    The own-checkpoint-completion block (`D-Scoped-Remediation-Acceptance`,
    resolves `WF8B-002`, hardened `GPT-R37-001`; the one part of that
    decision that outlived `/accept-scoped-remediation`'s retirement,
    ledger `I10`, because it guards `/accept-milestone` rather than the
    retired command): independent of, and in
    addition to, the child-completion check above, this now also resolves
    and loads the work item's **own** registry authoritatively (never a
    caller-supplied dict -- `resolve_own_registry_completion_status`,
    `repo_root`-driven) and refuses via `IncompleteOwnCheckpointsError`,
    naming the outstanding checkpoint, when the item's own registry has any
    checkpoint that is not `COMPLETE`.

    `D-Completion-Obligations` (item 356(a)): independent of, and in
    addition to, both checks above, every completion obligation the
    item's own registry declares must derive `PASS` --
    `UnsatisfiedCompletionObligationError` otherwise, naming each
    outstanding obligation, its classification, and (for `FAIL`) the
    specific failing conformance assertions. Marking every registry
    checkpoint `COMPLETE` -- by hand, by `complete_checkpoint(...)`, or
    by any other means -- is therefore not sufficient by itself. This is
    the gate the terminal transition's own single critical section
    requires (item 356(j)): the caller of this function is expected to
    hold `state_lock` for the whole re-read -> this call -> publish
    sequence, since this function itself never acquires it and the
    obligation conformance it runs never touches the live state file or
    lock (item 356(n))."""
    blocking = incomplete_children(state, work_item_id)
    if blocking:
        raise IncompleteChildWorkItemError(
            f"{work_item_id!r} cannot reach MILESTONE_COMPLETE while child work "
            f"item(s) {blocking} have not themselves reached MILESTONE_COMPLETE"
        )

    work_item = state["work_items"][work_item_id]
    is_terminal, outstanding_checkpoint_id = resolve_own_registry_completion_status(repo_root, work_item)
    if not is_terminal:
        raise IncompleteOwnCheckpointsError(
            f"{work_item_id!r} cannot reach MILESTONE_COMPLETE -- its own checkpoint "
            f"{outstanding_checkpoint_id!r} is not COMPLETE. Finish it with "
            f"/milestone-implement if it is still part of this milestone; for a "
            f"functional-review finding, use /apply-functional-review -- its bounded "
            f"branch for a same-scope fix, or its broad branch, which creates a "
            f"remediation child work item, for new or wider scope"
        )

    verdicts = resolve_completion_obligations(repo_root, work_item)
    outstanding = {oid: v for oid, v in verdicts.items() if v.classification != "PASS"}
    if outstanding:
        details = "; ".join(
            f"{oid} ({v.classification}): {v.detail}"
            + (f" -- failing: {list(v.failing_assertions)}" if v.classification == "FAIL" else "")
            for oid, v in sorted(outstanding.items())
        )
        raise UnsatisfiedCompletionObligationError(
            f"{work_item_id!r} cannot reach MILESTONE_COMPLETE -- outstanding "
            f"completion obligation(s): {details}"
        )

    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    work_item["phase"] = "MILESTONE_COMPLETE"
    work_item["current_checkpoint_id"] = None
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    if verdicts:
        work_item["completion_obligations_accepted"] = {
            oid: dict(v._asdict()) for oid, v in verdicts.items()
        }
    if new_state.get("active_work_item_id") == work_item_id:
        new_state["active_work_item_id"] = None
    return new_state


def create_remediation_child_work_item(
    state: dict, config: dict, *, parent_work_item_id: str,
    plan_path: str, registry_path: str, base_commit: str, now: str,
) -> tuple[dict, str]:
    """D-Functional-Remediation's broad/multi-finding remediation branch:
    creates a new, separate work item -- `work_item_id:
    "<parent_work_item_id>-remediation-<n>"`, `n` derived deterministically
    as one past however many remediation children this parent already has
    (never caller-supplied, so numbering can never collide or skip) -- with
    the same `work_item_type`/`work_item_kind` as the parent, carrying the
    new `parent_work_item_id` field, its own `base_commit` (the parent's
    implementation head at the moment broad remediation was needed), and
    otherwise `default_work_item`'s ordinary fresh-`PLANNING`-phase shape.
    `governing_workflow_version` is fixed from the config's current
    default, exactly like any other freshly created work item (D1) --
    because a remediation child *is* one, routed through the full normal
    plan/implement/review/accept cycle by the ordinary commands from here.

    Deliberately does not touch `active_work_item_id` -- the parent
    typically stays the operator's focus; the child is addressed by its
    own id until the operator chooses otherwise, exactly D1's "resume-focus
    pointer, not an execution lock" text.

    The parent's own registry/mapping/completed-checkpoint history is
    untouched by construction: this function only adds a new key to
    `work_items`, never reads or writes the parent's `registry_path`/
    `plan_path` contents."""
    parent = state["work_items"][parent_work_item_id]
    existing_children = [
        wid for wid in state.get("work_items", {})
        if state["work_items"][wid].get("parent_work_item_id") == parent_work_item_id
    ]
    remediation_number = len(existing_children) + 1
    child_id = f"{parent_work_item_id}-remediation-{remediation_number}"
    validate_work_item_id(child_id)

    new_state = copy.deepcopy(state)
    work_items = new_state.setdefault("work_items", {})
    if child_id in work_items:
        raise RemediationChildAlreadyExistsError(
            f"{child_id!r} already exists -- remediation-child numbering should "
            f"never collide; check for a hand-edited state file"
        )
    validate_governing_version(config["default_workflow_version"], config)
    child = default_work_item(
        work_item_id=child_id, work_item_type=parent["work_item_type"],
        work_item_kind=parent["work_item_kind"], plan_path=plan_path,
        registry_path=registry_path,
        governing_workflow_version=config["default_workflow_version"],
        plan_revision=1, last_transition=now,
    )
    child["parent_work_item_id"] = parent_work_item_id
    child["base_commit"] = base_commit
    work_items[child_id] = child
    return new_state, child_id


# ---------------------------------------------------------------------------
# workflow-v2-3-followups CP3: `plan_review_stages` key-casing compatibility
# -- one dedicated normalization-and-collision-detection helper, used at
# every read site and by the one-time migration function below, so
# compatibility-reading and migration agree on what "canonical" means by
# construction (resolves `GPT-FUP-R6-O01`/`-I02`).
# ---------------------------------------------------------------------------


def _normalize_plan_review_stage_key(key: str) -> str:
    """Maps the two known legacy lowercase `plan_review_stages` tokens to
    their canonical `SCREAMING_SNAKE_CASE` form; any other key (including
    an already-canonical one, or `review_content_id`) passes through
    unchanged -- idempotent by construction."""
    if key == "local_model_plan_review":
        return LOCAL_MODEL_PLAN_REVIEW
    if key == "manual_external_plan_review":
        return MANUAL_EXTERNAL_PLAN_REVIEW
    return key


def normalize_plan_review_stages(stages: dict) -> dict:
    """Reads a `plan_review_stages` dict tolerant of both legacy lowercase
    and canonical `SCREAMING_SNAKE_CASE` keys, returning a dict keyed
    entirely canonically. `review_content_id` passes through unchanged --
    it is a value, never a stage key. When two raw keys normalize to the
    same canonical stage, a byte-identical (`==`) duplicate collapses
    silently to that one shared value; a genuine conflict raises
    `AmbiguousPlanReviewStageKeyError`, naming the canonical stage and both
    raw keys/values, independent of dict insertion order -- never resolved
    by key-iteration order. Used at every `plan_review_stages` read site
    and by `migrate_plan_review_stage_keys`."""
    normalized: dict = {}
    raw_key_by_canonical: dict[str, str] = {}
    for key, value in stages.items():
        if key == "review_content_id":
            normalized[key] = value
            continue
        canonical = _normalize_plan_review_stage_key(key)
        if canonical in normalized:
            if normalized[canonical] != value:
                raise AmbiguousPlanReviewStageKeyError(
                    f"plan_review_stages: raw keys {raw_key_by_canonical[canonical]!r} and "
                    f"{key!r} both normalize to {canonical!r} but disagree: "
                    f"{normalized[canonical]!r} vs. {value!r}"
                )
            continue
        normalized[canonical] = value
        raw_key_by_canonical[canonical] = key
    return normalized


def migrate_plan_review_stage_keys(state: dict) -> dict:
    """One-time normalization step (workflow-v2-3-followups CP3, closes
    migration requirement #2/#4): for every work item whose `phase` is not
    in `TERMINAL_PHASES`, if its `plan_review_stages` dict is not `None`,
    replaces it with `normalize_plan_review_stages`'s result -- the same
    collision-aware helper the read sites use, so migration and
    compatibility-reading agree on what "canonical" means. An already-
    ambiguous non-terminal work item's ledger makes this raise
    `AmbiguousPlanReviewStageKeyError` rather than silently pick a winner;
    run via `state_transaction`, so a raised exception leaves
    `WORKFLOW_STATE.json` completely unwritten, not partially migrated.
    Idempotent (an already-canonical dict round-trips unchanged) and
    generic (does not hardcode any one work item's id). Terminal-phase
    records (e.g. `workflow-v2-3`'s own) are skipped and stay
    byte-unchanged -- immutable historical review evidence, not live
    state."""
    new_state = copy.deepcopy(state)
    for work_item in new_state.get("work_items", {}).values():
        if work_item.get("phase") in TERMINAL_PHASES:
            continue
        stages = work_item.get("plan_review_stages")
        if stages is None:
            continue
        work_item["plan_review_stages"] = normalize_plan_review_stages(stages)
    return new_state


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP2: `implementation_review_stages` ledger normalize/read
# helpers, mirroring `normalize_plan_review_stages`/
# `_normalize_plan_review_stage_key` exactly. CP3 (D-Implementation-Review-
# Stages' own review-stage writers and gate widening) is the ledger's sole
# writer set (`record_local_implementation_review`/
# `record_manual_implementation_review`); this plumbing exists ahead of
# those writers so CP3 imports one already-reviewed normalize/read
# contract rather than deriving its own.
# ---------------------------------------------------------------------------


def _normalize_implementation_review_stage_key(key: str) -> str:
    """Identity mapping today -- no legacy lowercase variant has ever
    existed for `implementation_review_stages`, introduced fresh at
    `"2.2"` (unlike `plan_review_stages`, which inherited two real legacy
    keys from a pre-`SCREAMING_SNAKE_CASE` era). Kept as its own named
    function, mirroring `_normalize_plan_review_stage_key`'s own shape,
    so `normalize_implementation_review_stages` below never needs to
    change if a legacy alias is ever introduced later."""
    return key


def normalize_implementation_review_stages(stages: dict) -> dict:
    """Reads an `implementation_review_stages` dict, mirroring
    `normalize_plan_review_stages`'s own collision-aware read contract
    exactly: `review_content_id` passes through unchanged as a value,
    never a stage key; a byte-identical (`==`) duplicate under two raw
    keys that normalize to the same canonical stage collapses silently;
    a genuine conflict raises `AmbiguousImplementationReviewStageKeyError`,
    independent of dict insertion order. Used at every
    `implementation_review_stages` read site CP3 adds."""
    normalized: dict = {}
    raw_key_by_canonical: dict[str, str] = {}
    for key, value in stages.items():
        if key == "review_content_id":
            normalized[key] = value
            continue
        canonical = _normalize_implementation_review_stage_key(key)
        if canonical in normalized:
            if normalized[canonical] != value:
                raise AmbiguousImplementationReviewStageKeyError(
                    f"implementation_review_stages: raw keys "
                    f"{raw_key_by_canonical[canonical]!r} and {key!r} both normalize "
                    f"to {canonical!r} but disagree: {normalized[canonical]!r} vs. {value!r}"
                )
            continue
        normalized[canonical] = value
        raw_key_by_canonical[canonical] = key
    return normalized


# ---------------------------------------------------------------------------
# D-States: non-circular gate-reachability for AWAITING_PLAN_APPROVAL /
# AWAITING_TECHNICAL_APPROVAL (never reads plan_approval/technical_approval
# themselves -- resolves OPUS-R6-004/-011)
# ---------------------------------------------------------------------------


def approval_gate_reachable(latest_round_status: str) -> bool:
    """Shared entry condition for both new gates: reachable whenever the
    most recently reviewed round's status is `REVISE` or `APPROVE` (never
    `BLOCK`), regardless of whether any approval record exists yet -- a
    `REVISE` round with zero blocking findings left reaches the gate
    exactly as readily as an `APPROVE` round (missing-test items 12/27)."""
    return latest_round_status in ("REVISE", "APPROVE")


def technical_approval_gate_reachable(
    *, latest_round_status: str, protected_path_dirty: bool,
    head_matches_reviewed_implementation_head: bool,
    pinned_block: bool = False,
    governing_workflow_version: str | None,
    implementation_review_stages: dict | None,
    current_review_content_id: str | None,
) -> bool:
    """`AWAITING_TECHNICAL_APPROVAL`'s entry condition: the shared
    reachability rule above, plus "no protected path is dirty" (D3;
    `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` dirtiness never blocks
    this) and current committed content matching
    `reviewed_implementation_head` exactly -- plus, now, D2a's durable
    `BLOCK`-verdict pin (`WF8c` item (a)): refuses whenever `pinned_block`
    is `True`, **regardless of what `latest_round_status` says**. The
    caller computes `pinned_block` via
    `is_technical_review_block_pinned(work_item, current_bundle_id)`
    against the just-recomputed current `bundle_id` -- this function stays
    pure and never reads state/bundles itself. A pinned bundle can never
    become reachable again by any later edit of the mutable feedback file,
    including a status-preserving-binding overwrite that changes only
    `Status:` from `BLOCK` to `REVISE` (`GPT-R55-002`).

    workflow-2.5.0 CP3, widened exactly like `plan_approval_gate_reachable`
    already is for `TWO_STAGE_PLAN_REVIEW_VERSIONS`: for a `"2.2"` item
    only, this gate additionally requires the `implementation_review_stages`
    ledger to record both `LOCAL_MODEL_IMPLEMENTATION_REVIEW` and
    `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` completed (`verdict: APPROVE`)
    against the *current* implementation-stage `review_content_id`
    (D-Implementation-Review-Stages). `governing_workflow_version` equal to
    `None` (a legitimately absent/legacy value) or anything other than
    `"2.2"` runs exactly today's shared rule, unchanged.

    workflow-2.5.0 REVISE round 7's own `I1`: these three parameters are
    **required**, exactly like `plan_approval_gate_reachable`'s equivalent
    three (`governing_workflow_version`, `plan_review_stages`,
    `current_review_content_id`) always have been -- deliberately no
    longer defaulted to `None`, the one asymmetry between this function and
    the one its own docstring claims to mirror "exactly". A caller that
    omits `governing_workflow_version` used to fall through the `!= "2.2"`
    branch and return `True` with the `"2.2"` ledger never consulted at
    all -- fail *open*, precisely backwards for an approval gate, and
    reachable in practice (round 7's own reproduction: a call passing
    `implementation_review_stages=None` and a real
    `current_review_content_id` while omitting only
    `governing_workflow_version` returned `True`). Requiring all three
    turns that omission into an immediate `TypeError` instead of a silent
    approval-gate opening, for every caller, present and future -- not
    merely `approve-review.md`'s own now-corrected call (see that file's
    step 0/1), which is the one call site this repository ships but not
    the only one this signature has to defend against. Passing `None` for
    any of the three (a `"1"`/`"2.1"` item's `governing_workflow_version`
    and `implementation_review_stages`, most commonly) remains exactly as
    valid as before -- only *omitting* the keyword argument entirely is now
    refused, by Python's own call mechanics, before this function's body
    ever runs."""
    if pinned_block or not approval_gate_reachable(latest_round_status):
        return False
    if protected_path_dirty or not head_matches_reviewed_implementation_head:
        return False
    if governing_workflow_version != "2.2":
        return True
    if implementation_review_stages is None:
        return False
    stages = normalize_implementation_review_stages(implementation_review_stages)
    if stages.get("review_content_id") != current_review_content_id:
        return False
    local = stages.get(LOCAL_MODEL_IMPLEMENTATION_REVIEW)
    manual = stages.get(MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW)
    return (
        local is not None and local.get("verdict") == "APPROVE"
        and manual is not None and manual.get("verdict") == "APPROVE"
    )


def plan_approval_gate_reachable(
    *, latest_round_status: str, governing_workflow_version: str,
    plan_review_stages: dict | None, current_review_content_id: str,
) -> bool:
    """`AWAITING_PLAN_APPROVAL`'s entry condition: the shared reachability
    rule above, plus -- for a `TWO_STAGE_PLAN_REVIEW_VERSIONS`-governed
    (`"2.1"`/`"2.2"`, widened workflow-2.5.0 from a bare `"2.1"` literal)
    work item only (resolves GPT-R11-001/-003) -- the `plan_review_stages`
    ledger must record both `LOCAL_MODEL_PLAN_REVIEW` and
    `MANUAL_EXTERNAL_PLAN_REVIEW` completed (`verdict: APPROVE`) against
    the *current* plan-stage `review_content_id`. A `"1"` item's condition
    is exactly the shared rule, unchanged. Tolerant of legacy lowercase
    keys via `normalize_plan_review_stages` (workflow-v2-3-followups CP3)."""
    if not approval_gate_reachable(latest_round_status):
        return False
    if governing_workflow_version not in TWO_STAGE_PLAN_REVIEW_VERSIONS:
        return True
    if plan_review_stages is None:
        return False
    stages = normalize_plan_review_stages(plan_review_stages)
    if stages.get("review_content_id") != current_review_content_id:
        return False
    local = stages.get(LOCAL_MODEL_PLAN_REVIEW)
    manual = stages.get(MANUAL_EXTERNAL_PLAN_REVIEW)
    return (
        local is not None and local.get("verdict") == "APPROVE"
        and manual is not None and manual.get("verdict") == "APPROVE"
    )


# ---------------------------------------------------------------------------
# D2: unified plan_approval/technical_approval record -- shape, basis
# decision, and the mechanism-independent user-only guard's second control
# ---------------------------------------------------------------------------


def validate_user_confirmation(text: str, *, work_item_id: str, stage: str) -> None:
    """Second of the two named, real user-only-guard mechanisms (D2): the
    text must be non-empty and must literally name both the exact
    `work_item_id` and the exact `stage` being approved. A trust boundary,
    not a cryptographic guarantee (D2) -- rejects an empty/generic string
    and a confirmation naming the wrong item/stage, but a legitimate literal
    repeat across two different, genuine approvals is accepted (deliberately
    not a novelty check, per OPUS-R10-010/GPT-R11-004)."""
    if stage not in APPROVAL_STAGES:
        raise InvalidApprovalRecordError(f"unknown approval stage: {stage!r}")
    if not text or not text.strip():
        raise UserConfirmationRejectedError(
            f"user_confirmation is empty -- must name work_item_id {work_item_id!r} "
            f"and stage {stage!r} explicitly"
        )
    if work_item_id not in text:
        raise UserConfirmationRejectedError(
            f"user_confirmation does not name work_item_id {work_item_id!r}: {text!r}"
        )
    if stage not in text:
        raise UserConfirmationRejectedError(
            f"user_confirmation does not name stage {stage!r}: {text!r}"
        )


def resolve_approval_basis(
    *, latest_round_status: str, feedback_bundle_id: str | None, current_bundle_id: str,
    user_confirmation: str | None, work_item_id: str, stage: str,
    pinned_block: bool = False,
) -> str:
    """D2's basis decision, run *inside* the approval gate, never as an
    entry precondition (OPUS-R6-004): `EXTERNAL_APPROVE` only when the
    latest reviewed round's status is exactly `APPROVE` and its recorded
    `bundle_id` equals the just-recomputed current bundle_id exactly;
    otherwise `USER_OVERRIDE`. `BLOCK` never reaches either basis (the
    round's findings are, by definition, not resolved). The mechanism-
    independent guard's `user_confirmation` specificity check applies to
    every write regardless of basis (D2's record shape: "every basis") --
    `EXTERNAL_APPROVE` does not additionally prompt for override
    *justification* text (missing-test item 13), but still requires the
    same named-item/stage confirmation already gathered as part of
    invoking `/approve-review` in the current turn.

    `pinned_block` (`D2a`, `WF8c` item (a), `OPUS-R102-002`) is the
    positive-membership fix for the laundering path a bare `latest_round_status
    == "BLOCK"` check cannot close on its own: `latest_round_status` is
    whatever the caller read from `REVIEW_FEEDBACK.md` *this turn*, and that
    mutable file can be edited from `BLOCK` to `REVISE` after a `BLOCK` was
    already durably pinned (`record_technical_review_block_pin`) for the
    exact same `current_bundle_id`, leaving every binding field --
    including `Reviewed bundle ID:` -- unchanged. The caller computes
    `pinned_block` via `is_technical_review_block_pinned(work_item,
    current_bundle_id)`; when `True`, this function refuses exactly like a
    literal `BLOCK`, regardless of what `latest_round_status` says."""
    if pinned_block:
        raise BlockCannotApproveError(
            f"{work_item_id}/{stage}: a durable BLOCK-verdict pin exists for the "
            f"current bundle ({current_bundle_id!r}) -- neither EXTERNAL_APPROVE nor "
            f"USER_OVERRIDE is reachable, regardless of what REVIEW_FEEDBACK.md "
            f"currently says"
        )
    if latest_round_status == "BLOCK":
        raise BlockCannotApproveError(
            f"{work_item_id}/{stage}: latest reviewed round status is BLOCK -- "
            f"neither EXTERNAL_APPROVE nor USER_OVERRIDE is reachable"
        )
    validate_user_confirmation(user_confirmation or "", work_item_id=work_item_id, stage=stage)
    if (
        latest_round_status == "APPROVE"
        and feedback_bundle_id is not None
        and feedback_bundle_id == current_bundle_id
    ):
        return "EXTERNAL_APPROVE"
    return "USER_OVERRIDE"


def build_approval_record(
    *, basis: str, stage: str, user_confirmation: str, now: str,
    reviewed_bundle_id: str | None = None,
    approved_review_content_id: str | None = None,
    review_content_manifest: object | None = None,
    reviewed_content_commit: str | None = None,
    legacy_evidence: object | None = None,
    waived_guarantees: list[str] | None = None,
) -> dict:
    """Builds a D2-shaped `plan_approval`/`technical_approval` record and
    validates it before returning -- `reviewed_content_commit` is the
    caller's concern to omit for the plan stage (permanently null,
    GPT-R9-006) and to supply for the implementation stage."""
    record = {
        "status": "CURRENT",
        "basis": basis,
        "reviewed_bundle_id": reviewed_bundle_id,
        "approved_review_content_id": approved_review_content_id,
        "review_content_manifest": review_content_manifest,
        "reviewed_content_commit": reviewed_content_commit,
        "legacy_evidence": legacy_evidence,
        "user_confirmation": user_confirmation,
        "waived_guarantees": list(waived_guarantees or []),
        "recorded_at": now,
    }
    validate_approval_record(record, stage=stage)
    return record


def _describe_malformed_review_content_manifest_shape(manifest: object) -> str | None:
    """The one rule every `review_content_manifest` consumer shares --
    `None` (legitimately absent) or a flat list of `{"path": ...}`
    entries is well-formed; anything else (most notably the whole
    projection object `workflow_fingerprint.compute_review_content_id_*`
    returns, which happens to carry a field of the identical name one
    level up) is not. Returns a diagnostic description of the malformed
    shape, or `None` when the value is fine. Factored out (`workflow-v2-3-
    followups` continued scope, external cross-model review round 4,
    `OPUS-R25-007`'s own precedent: "the same widened rule" shared
    between a relocated read-path guard and its write-time/`validate_state`
    backstop, never a duplicated copy that can drift) so `validate_approval_
    record` (the write chokepoint), `_assert_registry_covered_by_current_
    plan_approval` (the live-state read consumer `/accept-milestone`
    reaches), and `_resolve_one_obligation`
    (the committed-blob read consumer, malformed state arriving from a
    historical commit rather than the live file) all refuse the identical
    malformed shape the identical way, rather than three separately
    maintained copies of the same check."""
    if manifest is None:
        return None
    if isinstance(manifest, list) and all(
        isinstance(entry, dict) and "path" in entry for entry in manifest
    ):
        return None
    shape = f"dict with keys {sorted(manifest.keys())!r}" if isinstance(manifest, dict) else type(manifest).__name__
    return (
        f"review_content_manifest must be a flat list of {{'path': ...}} entries -- got a "
        f"{shape}. This is the exact shape workflow_fingerprint.compute_review_content_id_"
        f"*'s own returned projection['review_content_manifest'] holds -- pass that field's "
        f"value, never the whole projection object it lives inside of"
    )


def validate_approval_record(record: dict, *, stage: str) -> None:
    """D2's shape check: known `status`/`basis`, the plan-stage's
    permanently-null `reviewed_content_commit` rule (GPT-R9-006), a
    non-`LEGACY_V1` basis requiring `reviewed_bundle_id`/
    `approved_review_content_id`/`review_content_manifest`, a non-empty
    `user_confirmation` ("every basis"), the `waived_guarantees`
    controlled vocabulary (OPUS-R6-025, narrowed OPUS-R10-014), and (this
    item's own `workflow-v2-3-followups` continued scope, self-discovered
    during `/accept-milestone`'s pre-flight) `review_content_manifest`'s
    own flat-list shape via `_describe_malformed_review_content_manifest_shape`
    -- closing the gap that twice let a caller pass `workflow_fingerprint.
    compute_review_content_id_*`'s whole returned projection object
    instead of that projection's own inner manifest list, silently, with
    no shape check anywhere in the write path.

    **This is the write chokepoint only** (external cross-model review
    round 4, `OPUS-R25-007`'s own precedent): this function, and
    `validate_state`/`_validate_work_item` which calls it over every
    persisted approval record, are never reached by any production read
    path -- `validate_state` has no production caller anywhere in this
    repository; it is a write-time/harness backstop, not a runtime guard.
    The actual runtime protection for a malformed record already
    persisted in `WORKFLOW_STATE.json` (an old backup, import, hand edit,
    or pre-fix tooling) lives at the two real consumer chokepoints instead:
    `_assert_registry_covered_by_current_plan_approval` (live-state,
    `/accept-milestone`) and `_resolve_one_obligation` (committed-blob,
    historical state)."""
    if stage not in APPROVAL_STAGES:
        raise InvalidApprovalRecordError(f"unknown approval stage: {stage!r}")
    if record.get("status") not in APPROVAL_STATUSES:
        raise InvalidApprovalRecordError(f"unknown approval status: {record.get('status')!r}")
    basis = record.get("basis")
    if basis not in APPROVAL_BASES:
        raise InvalidApprovalRecordError(f"unknown approval basis: {basis!r}")
    if stage == "plan" and record.get("reviewed_content_commit") is not None:
        raise InvalidApprovalRecordError(
            "a plan-stage approval record must never set reviewed_content_commit "
            "-- it stays permanently null (GPT-R9-006)"
        )
    if basis != "LEGACY_V1":
        for field in ("reviewed_bundle_id", "approved_review_content_id", "review_content_manifest"):
            if record.get(field) is None:
                raise InvalidApprovalRecordError(
                    f"a {basis} approval record must set {field} (only LEGACY_V1 may leave it null)"
                )
    shape_error = _describe_malformed_review_content_manifest_shape(record.get("review_content_manifest"))
    if shape_error is not None:
        raise InvalidApprovalRecordError(shape_error)
    if not record.get("user_confirmation"):
        raise InvalidApprovalRecordError("approval record must set a non-empty user_confirmation")
    for guarantee in record.get("waived_guarantees") or []:
        if guarantee not in WAIVED_GUARANTEES:
            raise InvalidApprovalRecordError(f"unknown waived_guarantees entry: {guarantee!r}")


# ---------------------------------------------------------------------------
# D2a: durable BLOCK-verdict pin (`technical_review_block_pins`) -- keyed
# by bundle_id alone, permanent, disjoint from REVIEW_FEEDBACK.md (mutable
# workspace state) and from plan_approval/technical_approval (which record
# an *approval* decision, never a BLOCK verdict). WF8c item (a).
# ---------------------------------------------------------------------------


def is_technical_review_block_pinned(work_item: dict, bundle_id: str) -> bool:
    """Whether `technical_review_block_pins` contains an entry for the
    exact `bundle_id` -- D2a's "regardless of what REVIEW_FEEDBACK.md
    currently says" refusal predicate, computed once so both
    `technical_approval_gate_reachable` and `resolve_approval_basis` can
    consult the identical fact."""
    pins = work_item.get("technical_review_block_pins") or []
    return any(pin.get("bundle_id") == bundle_id for pin in pins)


def record_technical_review_block_pin(
    state: dict, work_item_id: str, *, bundle_id: str, review_content_id: str, now: str,
) -> dict:
    """D2a's writer: durably pins a `BLOCK` verdict against the exact
    `bundle_id` it was observed for, once and permanently. Called by both
    `/apply-implementation-review` (the ordinary first reader of freshly
    placed feedback) and `/approve-review implementation`'s own gate
    evaluation (defense in depth), before either takes any other action --
    a small `WORKFLOW_STATE.json`-only commit, the same shape every other
    small durability write in this design already uses.

    Idempotent: a pin already present for the exact `bundle_id` is never
    duplicated -- this returns the identical `state` object, unchanged, so
    a caller can compare the result against what it passed in (`is`/`==`)
    to decide that no new commit is needed for a repeat observation of an
    already-pinned `BLOCK`. What this does not claim: a pin is written
    only once some command actually parses the `BLOCK` feedback -- a
    feedback file edited from `BLOCK` to `REVISE` before any workflow
    command ever reads it leaves no pin, identically to the file having
    been written as `REVISE` from the start."""
    work_item = state["work_items"][work_item_id]
    if is_technical_review_block_pinned(work_item, bundle_id):
        return state
    new_state = copy.deepcopy(state)
    new_work_item = new_state["work_items"][work_item_id]
    new_work_item.setdefault("technical_review_block_pins", [])
    new_work_item["technical_review_block_pins"].append({
        "bundle_id": bundle_id,
        "review_content_id": review_content_id,
        "recorded_at": now,
    })
    new_work_item["state_revision"] = new_work_item.get("state_revision", 1) + 1
    new_work_item["last_transition"] = now
    return new_state


def apply_plan_approval(
    state: dict, work_item_id: str, record: dict, now: str, *,
    pre_registry: dict | None = None, pre_plan_text: str | None = None,
    post_registry: dict | None = None, post_plan_text: str | None = None,
) -> dict:
    """`/approve-review plan`'s state-write step (D-Approval-Commits): sets
    `plan_approval` and transitions the work item to `IMPLEMENTING`. Commit
    creation itself is `/approve-review`'s own concern (D-Approval-Commits),
    not this function's.

    workflow-2.4.0, D-Plan-Amendment-4: four new, optional, keyword-only
    parameters. For a work item with no open amendment (`amendment_history`
    empty, or its last entry's `resolved_at_plan_revision` already set),
    all four stay `None` and are never consulted -- this function's
    behavior for that case is byte-for-byte unchanged from v2.3.1, so no
    existing call site needs to change. For a work item *with* an open
    amendment, all four must be non-`None` (`AmendmentReconciliationInputsMissingError`
    naming which is absent), `validate_post_anchor_coverage` runs against
    `post_plan_text`/`post_registry` before any outcome is computed, and
    `reconcile_checkpoints_after_amendment` folds its own outcome -- the
    rewritten `checkpoints` map, `current_checkpoint_id`/
    `last_completed_checkpoint_id` nulled if either named a dropped id,
    `amendment_history[-1]["resolved_at_plan_revision"]` set to this work
    item's own live `plan_revision`, and (`IMPL6-B1`) that same
    reconciliation call's own `{id: outcome}` map recorded verbatim as
    `amendment_history[-1]["reconciliation_outcome"]` -- into the state
    this function returns, alongside its own unchanged write set
    (`plan_approval`, `phase`, `state_revision`, `last_transition`).
    `reconciliation_outcome`'s tokens already distinguish a direct
    row/content demotion (`"needs_revalidation"`) from a
    dependency-closure-derived one (`"needs_revalidation_dependency"`),
    so `/approve-review plan`'s own step 7 can report "it ran and did X"
    (by id, closure flips distinguishable from direct ones) without this
    function computing anything further -- it stores the map
    `reconcile_checkpoints_after_amendment` already returns, unmodified.
    `plan_revision` itself is never written here -- it stays
    `publish_plan_revision`'s alone.

    A caller that supplies either pre-side reconciliation input
    (`pre_registry`/`pre_plan_text`) against a work item whose last
    `amendment_history` entry is *already* resolved -- a re-run
    reconciliation, the one case `AmendmentAlreadyResolvedError` names in
    its own docstring -- is refused before anything else runs (checked
    ahead of, and independent of, `has_open_amendment`'s own gate below;
    that gate alone can never observe this state, since it is true only
    when the last entry is *not* yet resolved). The guard is keyed on the
    pre-side pair specifically, never on `post_registry`/`post_plan_text`
    alone (`OPUS-R145-001`): the real caller, `approve-review.md` step 4c,
    reads and forwards `post_plan_text`/`post_registry` *unconditionally*,
    from the working tree, on every plan-stage approval regardless of
    amendment state -- only `pre_registry`/`pre_plan_text` are gated there
    on an open amendment. Keying this guard on "any of the four" made it
    fire from that real caller for *any* plan approval following a
    resolved amendment, non-amendment or not, degenerating into "a work
    item that has ever amended once can never have a plan approval applied
    again"; keying it on the pre-side pair alone matches the only shape a
    genuine re-run reconciliation can take (a caller that itself believed
    an amendment was still open). A caller that supplies neither pre-side
    value against an already-resolved amendment stays the ordinary,
    unconsulted no-op case -- the case the real caller always presents
    once past its first, ordinary (never-amended) round."""
    validate_approval_record(record, stage="plan")
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    amendment_history = work_item.get("amendment_history") or []
    pre_side_supplied = pre_registry is not None or pre_plan_text is not None
    if (
        amendment_history and amendment_history[-1].get("resolved_at_plan_revision") is not None
        and pre_side_supplied
    ):
        raise AmendmentAlreadyResolvedError(
            f"{work_item_id}'s last amendment_history entry is already resolved at "
            f"plan_revision {amendment_history[-1]['resolved_at_plan_revision']!r} -- "
            f"refusing a re-run reconciliation"
        )
    has_open_amendment = bool(amendment_history) and amendment_history[-1].get("resolved_at_plan_revision") is None
    if has_open_amendment:
        missing = [
            name for name, value in (
                ("pre_registry", pre_registry), ("pre_plan_text", pre_plan_text),
                ("post_registry", post_registry), ("post_plan_text", post_plan_text),
            ) if value is None
        ]
        if missing:
            raise AmendmentReconciliationInputsMissingError(
                f"{work_item_id} has an open amendment -- reconciliation requires all of "
                f"pre_registry/pre_plan_text/post_registry/post_plan_text; missing: {missing}"
            )
        # IMPL2-O2: `validate_post_anchor_coverage` reads
        # `post_registry.get("checkpoints", [])` (vacuously passes a
        # registry missing the key), but `validate_registry_topological_order`
        # reads `registry["checkpoints"]` directly and would raise an
        # unnamed `KeyError` for the same malformed input -- against this
        # branch's own "refuse and name it" discipline. Named here, once,
        # before either validator runs.
        if "checkpoints" not in post_registry:
            raise AmendmentPostRegistryMalformedError(
                f"{work_item_id}'s post_registry has no 'checkpoints' key -- cannot "
                f"validate anchor coverage or topological order for this amendment"
            )
        # IMPL4-O1: both `validate_post_anchor_coverage` and
        # `reconcile_checkpoints_after_amendment` directly read `entry["id"]`
        # from `post_registry["checkpoints"]` -- a row with no `id` key
        # would otherwise escape as a bare, unnamed `KeyError` from either
        # call below. Named here, once, ahead of both call paths, the same
        # "refuse and name it" discipline the "checkpoints"-key check just
        # above already applies to the coarser malformation.
        missing_id_indices = [
            i for i, entry in enumerate(post_registry.get("checkpoints", []))
            if "id" not in entry
        ]
        if missing_id_indices:
            raise AmendmentPostRegistryMalformedError(
                f"{work_item_id}'s post_registry has checkpoint entries with no 'id' "
                f"key at index/indices {missing_id_indices} -- cannot validate anchor "
                f"coverage or topological order for this amendment"
            )
        validate_post_anchor_coverage(post_plan_text, post_registry)
        validate_registry_topological_order(post_registry)
        reconciliation = reconcile_checkpoints_after_amendment(
            pre_registry, post_registry, pre_plan_text, post_plan_text,
            work_item.get("checkpoints", {}),
        )
        work_item["checkpoints"] = reconciliation["checkpoints"]
        if work_item.get("current_checkpoint_id") in reconciliation["dropped"]:
            work_item["current_checkpoint_id"] = None
        if work_item.get("last_completed_checkpoint_id") in reconciliation["dropped"]:
            work_item["last_completed_checkpoint_id"] = None
        resolved_entry = copy.deepcopy(amendment_history[-1])
        resolved_entry["resolved_at_plan_revision"] = work_item.get("plan_revision")
        # IMPL6-B1: record the reconciliation outcome, by id, onto the
        # resolved amendment_history entry itself -- the durable home
        # `/approve-review plan`'s own step 7 reads to report "it ran and
        # did X" rather than only the new phase. Recorded verbatim; this
        # function performs no further summarization of it.
        resolved_entry["reconciliation_outcome"] = reconciliation["outcome"]
        work_item["amendment_history"] = amendment_history[:-1] + [resolved_entry]
    work_item["plan_approval"] = record
    work_item["phase"] = "IMPLEMENTING"
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    return new_state


def apply_technical_approval(state: dict, work_item_id: str, record: dict, now: str) -> dict:
    """`/approve-review implementation`'s state-write step: sets
    `technical_approval` and transitions the work item to
    `AWAITING_FUNCTIONAL_REVIEW`."""
    validate_approval_record(record, stage="implementation")
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    work_item["technical_approval"] = record
    work_item["phase"] = "AWAITING_FUNCTIONAL_REVIEW"
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    return new_state


# ---------------------------------------------------------------------------
# workflow-2.4.0, D-Plan-Amendment-1/2/3: /request-plan-amendment's sole
# writer -- amending an approved plan after implementation has begun.
# ---------------------------------------------------------------------------


_AMENDMENT_REQUEST_ALLOWED_PHASES = frozenset({"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"})


def request_plan_amendment(
    state: dict, work_item_id: str, reason: str, *, repo_root: Path, now: str,
) -> dict:
    """`/request-plan-amendment`'s sole writer (D-Plan-Amendment-1/2/3).

    Entry condition: `phase in {"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"}`
    -- the two phases this release supports, and deliberately the only two
    (`WrongPhaseForAmendmentRequestError` otherwise, naming the actual
    phase; this is also what refuses a second, redundant request against
    an item already at `AMENDING_PLAN`).

    Widened precondition (revision 11/12, I-R11-1/B-R12-1): the current
    `plan_approval`'s own approval commit must actually be discoverable and
    an ancestor of `HEAD` -- checked directly via
    `discover_plan_approval_commit`/`_is_ancestor`, the identical pair
    `implementing_entry_reachable` itself evaluates, never through that
    four-exit composite (which would also, incorrectly, refuse on a
    digest-only staleness this amendment is about to replace anyway).
    Raises `AmendmentApprovalCommitUnreachableError` *before* superseding
    anything -- `plan_approval.status` is never set to `SUPERSEDED` when
    this fires.

    Checkpoint-id-shape precondition (IMPL2-R1, narrowed by IMPL3-R1): before
    either of the above, loads the work item's own current registry (via
    `_load_authoritative_registry_or_none(repo_root, work_item,
    require_plan_approval_coverage=False)` -- `None` for a registry-less
    work item, which skips this check) and raises
    `AmendmentCheckpointIdShapeError`, naming every offending id, if any
    checkpoint id in it is not of the shape `CP<digits>`. Such an id can
    never satisfy `validate_post_anchor_coverage`'s anchor grammar no
    matter what the amended plan document says, so refusing here -- before
    `plan_approval` is superseded -- replaces a refusal that would
    otherwise surface only after both plan-review stages have already
    been spent on the amended plan, with no in-band recovery.

    `require_plan_approval_coverage=False` (IMPL3-R1): this call needs only
    the registry's self-declared checkpoint ids, not a proof the registry
    bytes are the ones the current `plan_approval` covers.
    `_load_authoritative_registry_or_none`'s default coverage check
    (`_assert_registry_covered_by_current_plan_approval`) is
    `resolve_own_registry_completion_status`/`resolve_completion_obligations`'s
    own completion-accounting trust boundary; reusing it verbatim here
    reintroduced, through a different door, exactly the digest-only-
    staleness refusal `.claude/commands/request-plan-amendment.md` step 1
    explicitly forbids (`D-Plan-Amendment-1`, `B-R12-1`) -- an operator who
    has started editing `plan_path`/`registry_path` before running this
    command (the single most likely working-tree state for one about to
    request an amendment) was refused with a `StalePlanApprovalRegistryReadError`
    whose message talks about "registry-derived completion", though nothing
    about this command is completion-accounting. Passing `False` restores
    the narrower, id-shape-only read this precondition was designed for;
    the coverage checks other two call sites still need are unaffected.

    In one `state_transaction`-compatible mutation: sets
    `plan_approval.status = "SUPERSEDED"`; appends one entry to the
    work item's own append-only `amendment_history` list (bounded,
    content-addressed reference model, EXT-R6-I1: `superseded_plan_approval`
    is a deep copy of the record just superseded -- its own
    `review_content_manifest` already pins the exact blob SHAs of
    `plan_path`/`registry_path` at the moment it was made;
    `pre_amendment_approval_commit` is the single commit SHA that
    reproduces those bytes later via `load_pre_amendment_snapshot`, never
    a stored copy of the documents themselves); sets `amendment_base_commit`
    to the current `HEAD`; and writes `phase = "AMENDING_PLAN"` as a direct
    string literal (never through a local name), so the AST-derived phase
    census resolves it without a third hardcoded compensation entry.

    This function performs one read of Git identity (`HEAD`'s own SHA, and
    the reachability check above) -- both are needed to compute the exact
    `amendment_history` entry this function itself writes, the same
    narrow exception to pure-`state`-only mutators D-Plan-Amendment-3
    grants this one writer and no other."""
    work_item = state["work_items"][work_item_id]
    phase = work_item.get("phase")
    if phase not in _AMENDMENT_REQUEST_ALLOWED_PHASES:
        raise WrongPhaseForAmendmentRequestError(
            f"{work_item_id} is at phase {phase!r} -- /request-plan-amendment requires "
            f"phase in {sorted(_AMENDMENT_REQUEST_ALLOWED_PHASES)}"
        )

    # XMODEL-R4-B1: refuse before anything else -- and before any of the
    # checks below -- if a checkpoint is already IN_PROGRESS in state, or a
    # shared filesystem checkpoint claim is outstanding for this work item.
    # See `AmendmentCheckpointActiveError`'s own docstring for why both
    # halves are checked (they are two different synchronization domains).
    if any(
        entry.get("status") == "IN_PROGRESS"
        for entry in work_item.get("checkpoints", {}).values()
    ):
        raise AmendmentCheckpointActiveError(
            f"{work_item_id!r} has a checkpoint IN_PROGRESS "
            f"({work_item.get('current_checkpoint_id')!r}) -- /request-plan-amendment "
            f"refuses while implementation is live"
        )
    outstanding_claim = resolve_claim(repo_root, work_item_id)
    if outstanding_claim is not None:
        raise AmendmentCheckpointActiveError(
            f"{work_item_id!r} has an outstanding checkpoint claim "
            f"(checkpoint {outstanding_claim.get('checkpoint_id')!r}, worktree "
            f"{outstanding_claim.get('worktree_root')!r}) -- /request-plan-amendment "
            f"refuses while a checkpoint start is in flight, even though "
            f"WORKFLOW_STATE.json may not show it IN_PROGRESS yet"
        )

    # IMPL2-R1: refuse by name, before anything is superseded, if the
    # work item's own current registry already names a checkpoint id that
    # is not of the shape `CP<digits>` -- `validate_post_anchor_coverage`
    # would refuse the eventual amended plan for exactly this id, but only
    # after both plan-review stages have been spent on it, with no anchor
    # text able to fix it. A registry-less work item (`registry_path` is
    # `None`) has nothing to check here.
    registry = _load_authoritative_registry_or_none(
        repo_root, work_item, require_plan_approval_coverage=False,
    )
    if registry is not None:
        # IMPL3-O2: `_load_authoritative_registry_or_none` validates the
        # registry's own envelope (safe path, JSON object, self-declared
        # `work_item_id`) but never its checkpoint-row shape, so a row
        # missing `id` must be named here rather than escaping as an
        # unnamed `KeyError` from the comprehension below -- the same
        # "refuse and name it" violation IMPL2-O2 was raised about, in a
        # different registry read.
        missing_id_indices = [
            i for i, entry in enumerate(registry.get("checkpoints", []))
            if "id" not in entry
        ]
        if missing_id_indices:
            raise AmendmentRegistryMissingIdError(
                f"{work_item_id}'s registry ({work_item.get('registry_path')}) has "
                f"checkpoint entries with no 'id' key at index/indices "
                f"{missing_id_indices} -- cannot check anchor-shape compatibility"
            )
        unsupported_ids = [
            entry["id"] for entry in registry.get("checkpoints", [])
            if not checkpoint_id_supports_anchor(entry["id"])
        ]
        if unsupported_ids:
            raise AmendmentCheckpointIdShapeError(
                f"{work_item_id}'s registry ({work_item.get('registry_path')}) names "
                f"checkpoint id(s) {unsupported_ids!r} that are not of the shape "
                f"'CP<digits>' -- the plan-amendment anchor grammar "
                f"(D-Plan-Amendment-4) can never be satisfied for these, so "
                f"/request-plan-amendment refuses before superseding plan_approval"
            )

    plan_approval = work_item.get("plan_approval") or {}
    base_commit = work_item["base_commit"]
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
    approval_commit = discover_plan_approval_commit(
        repo_root, work_item_id, plan_approval.get("approved_review_content_id"), base_commit, head=head,
    )
    if approval_commit is None or not _is_ancestor(repo_root, approval_commit, head):
        raise AmendmentApprovalCommitUnreachableError(
            f"{work_item_id}'s current plan_approval commit is not discoverable in "
            f"{base_commit}..{head}, or not an ancestor of it -- refusing before "
            f"superseding anything"
        )

    new_state = copy.deepcopy(state)
    new_work_item = new_state["work_items"][work_item_id]
    history = new_work_item.setdefault("amendment_history", [])
    entry = {
        "amendment_id": str(len(history)),
        "requested_at": now,
        "requested_from_phase": phase,
        "reason": reason,
        "superseded_plan_revision": new_work_item.get("plan_revision"),
        "superseded_plan_approval": copy.deepcopy(new_work_item.get("plan_approval")),
        "checkpoints_snapshot": copy.deepcopy(new_work_item.get("checkpoints", {})),
        "pre_amendment_approval_commit": approval_commit,
        "resolved_at_plan_revision": None,
    }
    history.append(entry)
    new_work_item["plan_approval"]["status"] = "SUPERSEDED"
    new_work_item["amendment_base_commit"] = head
    new_work_item["phase"] = "AMENDING_PLAN"
    new_work_item["state_revision"] = new_work_item.get("state_revision", 1) + 1
    new_work_item["last_transition"] = now
    return new_state


# ---------------------------------------------------------------------------
# WF4c: D-Functional-Remediation -- the bounded-fix branch's stale-before-
# edit write and the bundle generator's sole reviewed_implementation_head
# writer (closes the loop OPUS-R6-013 found: nothing wrote this field
# before, so AWAITING_TECHNICAL_APPROVAL's entry condition was permanently
# unreachable for any real "2.1" item).
# ---------------------------------------------------------------------------


def mark_technical_approval_stale(state: dict, work_item_id: str, now: str) -> dict:
    """D-Functional-Remediation's bounded-code-change branch, stale-before-
    edit ordering: the caller must persist this write to
    `docs/ai-workflow/WORKFLOW_STATE.json` *before* touching a single
    source/test file, so an interrupted session still shows `STALE` rather
    than a `CURRENT` record whose reviewed content no longer matches the
    working tree. Requires an existing `technical_approval` record (there
    is nothing to stale otherwise) and leaves every other field of it
    untouched -- only `status` flips."""
    work_item = state["work_items"][work_item_id]
    record = work_item.get("technical_approval")
    if record is None:
        raise InvalidApprovalRecordError(
            f"{work_item_id!r} has no technical_approval record to stale -- "
            f"the bounded-fix branch only applies after a prior technical approval"
        )
    new_state = copy.deepcopy(state)
    new_work_item = new_state["work_items"][work_item_id]
    new_work_item["technical_approval"]["status"] = "STALE"
    new_work_item["state_revision"] = new_work_item.get("state_revision", 1) + 1
    new_work_item["last_transition"] = now
    return new_state


def bundle_generation_target_phase(stage: str, governing_workflow_version: str | None) -> str:
    """workflow-2.5.0 CP3 (D-Implementation-Review-Stages "Provenance-
    interval interaction"): the single function of `(stage,
    governing_workflow_version)` replacing `record_bundle_generation`'s
    and `validate_bundle_generation_record_commit`'s previously
    hard-coded `"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"` target-phase
    literal. Returns `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` for
    `"1"`/`"2.1"` (both `stage` values, byte-identical to pre-CP3
    behavior) and `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` for `"2.2"` (both
    `stage` values -- a `"2.2"` item's post-fix regeneration re-enters
    local review exactly like its first-round generation does, never
    going straight back to the terminal phase). `governing_workflow_version`
    absent (`None`) resolves as the `"1"`/`"2.1"` literal, never a raise --
    the same convention this module's other version-dependent resolvers
    (e.g. `plan_approval_gate_reachable`) already follow for a work item
    with no recorded version. `stage` itself must still be one of
    `"implementation"`/`"post-fix"` (`InvalidBundleGenerationStageError`);
    note that, for any single `governing_workflow_version`, both `stage`
    values always resolve to the identical target -- the distinction
    matters only for legal-source-phase checking
    (`BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE`), never for the
    target phase itself."""
    if stage not in ("implementation", "post-fix"):
        raise InvalidBundleGenerationStageError(
            f"reviewed_implementation_head is written only at the "
            f"\"implementation\"/\"post-fix\" bundle-generation stage, got {stage!r}"
        )
    if governing_workflow_version == "2.2":
        return "AWAITING_LOCAL_IMPLEMENTATION_REVIEW"
    return "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"


def bundle_generation_recovered_role_legal_committed_phases(
    governing_workflow_version: str | None,
) -> frozenset[str]:
    """workflow-2.5.0 CP3 (D-Implementation-Review-Stages "Provenance-
    interval interaction", third fix): the recovered-role committed-phase
    membership test replacing `validate_bundle_generation_record_commit`'s
    former single-valued equality, and the identical set
    `/recover-implementation-provenance`'s own invocation guard
    (`verify_implementation_provenance_recovery`/
    `apply_implementation_provenance_recovery`) admits. For `"1"`/`"2.1"`
    (and an absent/`None` version, the same convention
    `bundle_generation_target_phase` follows): the single-member set
    `{AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW}`, byte-identical to pre-CP3
    behavior. For `"2.2"`: the three-phase set
    `{AWAITING_LOCAL_IMPLEMENTATION_REVIEW,
    AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW,
    AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW}` -- every phase a `"2.2"` item
    can occupy between a generation-record commit `T` and technical
    approval, since recovery never changes `phase`'s value and so must be
    invocable from whichever of those three phases the round is currently
    sitting at. The resulting invariant: for the recovered role, the
    command guard and this function's own return value are the identical
    set, and both are always a subset of the additively-widened
    `RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES` (the legal
    *parent* phases, a superset covering every source phase recovery's own
    same-content interval walk may cross, not only the phase recovery is
    invoked *from*)."""
    if governing_workflow_version == "2.2":
        return frozenset({
            "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
            "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
            "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        })
    return frozenset({"AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"})


def record_bundle_generation(
    state: dict, work_item_id: str, *, stage: str, head: str, now: str, outcome: str = "ordinary",
) -> dict:
    """`reviewed_implementation_head`'s sole writer (D-Approval-Commits),
    called by the bundle generator at exactly the `"implementation"` (first
    round) or `"post-fix"` (every remediation round after, whether driven
    by an implementation-review finding or a functional-review bounded
    fix) stage -- never any other. Also `phase`'s sole writer for this
    transition (OPUS-R101-001, widened by workflow-v2-3-followups's own
    continued scope): legality is stage-specific
    (`BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE`) -- `"implementation"`
    only from `SELF_REVIEWING_IMPLEMENTATION`; `"post-fix"` from
    `APPLYING_REVIEW_FEEDBACK`, or from `AWAITING_FUNCTIONAL_REVIEW` when
    (and only when) `technical_approval.status == "STALE"`, the bounded-fix
    marker `/apply-functional-review`'s own branch writes before its first
    edit lands. Refuses outright otherwise, naming the actual phase, the
    requested stage, and the phase(s) legal for it
    (`IllegalBundleGenerationSourcePhaseError`), or naming the non-`STALE`
    status for the `AWAITING_FUNCTIONAL_REVIEW` case specifically
    (`BundleGenerationRequiresStaleTechnicalApprovalError`). Always sets
    the durable target `bundle_generation_target_phase(stage,
    governing_workflow_version)` -- version-dependent since workflow-2.5.0
    CP3 (`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` for `"1"`/`"2.1"`,
    byte-identical to before; `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` for
    `"2.2"`) -- the "Ordinary bundle-publication phase transition"
    contract, `WFR-61`'s five-field mutation.

    `outcome` (WF8c (c), D-Commit-Provenance "Same-content post-fix
    republication") selects which of this function's two legal outcomes
    applies -- a decision the *caller* makes by calling
    `resolve_bundle_generation_outcome` first (a read-only Git-inspecting
    query this function itself deliberately stays free of, so it remains
    a pure `state -> state` mutator callable as a `state_transaction`
    mutator): `"ordinary"` (default, backward-compatible) records live
    `head` as the new `reviewed_implementation_head` and bumps
    `implementation_revision` (`None` -> `1` on the first call,
    incrementing on every call after -- `implementation_revision` itself
    is never part of either fingerprint projection, so this bump alone
    never stales `technical_approval`, `approval_is_current`'s own
    missing-test item 11). `"same_content"` leaves
    `reviewed_implementation_head`/`implementation_revision`
    byte-identical -- only `phase`/`state_revision`/`last_transition`
    change, exactly the recovered-role commit's own allowed field set --
    since the caller has already established (via
    `resolve_bundle_generation_outcome`) that the protected content at
    `head` is identical to the currently-reviewed round and the
    intervening commits are all legitimately excluded-only; `head` itself
    is otherwise unused in this branch, kept only for call-shape symmetry.
    Both outcomes always perform a real `phase` transition into
    `bundle_generation_target_phase`'s own resolved value, from whichever
    legal source phase for the requested `stage` was current -- never
    value-wise unchanged, since neither
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` nor (for `"2.2"`)
    `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` is ever itself one of this
    function's own legal source phases for any stage."""
    if stage not in ("implementation", "post-fix"):
        raise InvalidBundleGenerationStageError(
            f"reviewed_implementation_head is written only at the "
            f"\"implementation\"/\"post-fix\" bundle-generation stage, got {stage!r}"
        )
    if outcome not in ("ordinary", "same_content"):
        raise InvalidBundleGenerationOutcomeError(
            f"record_bundle_generation's outcome must be 'ordinary' or "
            f"'same_content' (D-Commit-Provenance's own two outcomes), got {outcome!r}"
        )
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    current_phase = work_item.get("phase")
    legal_phases_for_stage = BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE[stage]
    if current_phase not in legal_phases_for_stage:
        raise IllegalBundleGenerationSourcePhaseError(
            f"record_bundle_generation invoked from phase {current_phase!r} for stage "
            f"{stage!r}, but the only legal source phase(s) for this stage are "
            f"{sorted(legal_phases_for_stage)}"
        )
    if current_phase == "AWAITING_FUNCTIONAL_REVIEW":
        technical_approval_status = (work_item.get("technical_approval") or {}).get("status")
        if technical_approval_status != "STALE":
            raise BundleGenerationRequiresStaleTechnicalApprovalError(
                f"record_bundle_generation invoked from phase 'AWAITING_FUNCTIONAL_REVIEW' "
                f"requires technical_approval.status == 'STALE' (the functional-review "
                f"bounded-fix marker) -- got {technical_approval_status!r}"
            )
    work_item["phase"] = bundle_generation_target_phase(
        stage, work_item.get("governing_workflow_version"),
    )
    if outcome == "ordinary":
        work_item["reviewed_implementation_head"] = head
        work_item["implementation_revision"] = (work_item.get("implementation_revision") or 0) + 1
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    return new_state


def enter_applying_review_feedback(state: dict, work_item_id: str, now: str) -> dict:
    """`APPLYING_REVIEW_FEEDBACK`'s durable writer (OPUS-R101-001):
    `/apply-implementation-review` step 0's "Enter the
    `APPLYING_REVIEW_FEEDBACK` state" had no writer behind it at all before
    this. Refuses outright from any phase other than
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` -- the phase
    `MILESTONE_WORKFLOW.md` documents as this state's entry condition
    ("implementation review feedback exists")."""
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    current_phase = work_item.get("phase")
    if current_phase != "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW":
        raise IllegalApplyingReviewFeedbackEntryPhaseError(
            f"enter_applying_review_feedback invoked from phase "
            f"{current_phase!r}, but the only legal source phase is "
            f"'AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW'"
        )
    work_item["phase"] = "APPLYING_REVIEW_FEEDBACK"
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    return new_state


# ---------------------------------------------------------------------------
# D-Commit-Provenance / D-Approval-Commits, WF8B-003 remediation (WF8b):
# the `Workflow-Bundle-Generation-Record` trailer and the provenance-
# interval check that replaces a bare `reviewed_implementation_head ==
# HEAD` comparison. `record_bundle_generation` above still writes
# `reviewed_implementation_head`/`implementation_revision` into `state`
# exactly as before; the caller is now responsible for persisting that
# write as its own dedicated commit (touching only `WORKFLOW_STATE.json`,
# carrying the trailers below) *before* generating the bundle, so no
# later commit can ever land between the write and the commit that makes
# it durable (D-Approval-Commits, WF8B-003's own worked contradiction).
# Both the "ordinary" role and the recovered/superseded
# (`Workflow-Supersedes`) role are implemented here (WF8c (c)/(b)): the
# dedicated `/recover-implementation-provenance` command's own two
# primitives (`verify_implementation_provenance_recovery`/
# `apply_implementation_provenance_recovery`, WF8c (b)) live further below
# in this same section, past `resolve_bundle_generation_outcome`.
# ---------------------------------------------------------------------------


ORDINARY_BUNDLE_GENERATION_RECORD_FIELDS = frozenset({
    "phase", "reviewed_implementation_head", "implementation_revision",
    "state_revision", "last_transition",
    # workflow-2.5.0 CP3: widened unconditionally (not "2.2"-scoped) to
    # admit a "2.2" REVISE loop's stale, uncommitted implementation_review_
    # stages residue in a commit's own field diff -- see D-Implementation-
    # Review-Stages' "Provenance-interval interaction", second fix. Safe
    # for "1"/"2.1": that vocabulary is never written by their own
    # state_transaction mutators, so it is always absent from their field
    # diffs regardless of what this set admits -- and this is not merely a
    # reachability argument: `_validate_implementation_review_stages` (run
    # from `validate_state`) rejects any non-"2.2" item carrying a non-null
    # `implementation_review_stages` at read time, so the vocabulary is
    # rejected, not just unreached.
    "implementation_review_stages",
})

RECOVERED_BUNDLE_GENERATION_RECORD_FIELDS = frozenset({
    "phase", "state_revision", "last_transition",
    # Same unconditional widening as ORDINARY_BUNDLE_GENERATION_RECORD_FIELDS
    # above, for the identical reason -- a "2.2" same-content post-fix
    # republication (record_bundle_generation(outcome="same_content"), the
    # recovered role) can carry the same stale ledger residue.
    "implementation_review_stages",
})

RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES = (
    # workflow-2.5.0 CP3: additively widened with the two new "2.2" phases
    # (D-Implementation-Review-Stages' "Provenance-interval interaction",
    # third fix) -- AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW remains a legal
    # parent phase for "1"/"2.1" (and for "2.2", as the terminal phase).
    BUNDLE_GENERATION_LEGAL_SOURCE_PHASES | {
        "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW",
        "AWAITING_LOCAL_IMPLEMENTATION_REVIEW",
        "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
    }
)


def _bundle_generation_record_role(trailers: dict[str, str]) -> str | None:
    """Classifies a commit's own trailer set as the `"ordinary"` two-trailer
    role, the `"recovered"` three-trailer role (WF8c (c)/(b), adds
    `Workflow-Supersedes`), or `None` for any other shape -- a commit whose
    trailer set matches neither exactly is never coerced into one."""
    keys = set(trailers)
    if keys == {"Workflow-Bundle-Generation-Record", "Workflow-Work-Item"}:
        return "ordinary"
    if keys == {"Workflow-Bundle-Generation-Record", "Workflow-Work-Item", "Workflow-Supersedes"}:
        return "recovered"
    return None


def _bundle_generation_record_chain_tip(
    repo_root: Path, commit: str, value: str, candidates: list[str],
) -> bool:
    """WF8c (c)/(b) supersession-chain tie-break predicate for
    `_discover_trailer_commits`'s `verify` callback: true iff no *other*
    candidate sharing this exact `Workflow-Bundle-Generation-Record` value
    carries a `Workflow-Supersedes: <commit>` trailer -- i.e. `commit` is
    the chain's current, non-superseded tip among `candidates` (D-Commit-
    Provenance "the current record ... is the unique commit ... that is
    not itself named by any other such commit's Workflow-Supersedes
    trailer"). A fork (two candidates each unsuperseded) or a cycle (zero
    candidates unsuperseded) both naturally leave `_discover_trailer_commits`
    with other-than-exactly-one verified survivor, refused as genuine
    ambiguity by its own existing machinery -- no separate fork/cycle
    detection needed here. Strict role/field validity of the edge itself
    is `validate_bundle_generation_record_commit`'s job, not this
    tie-break's: this predicate only resolves *which* commit is current."""
    for other in candidates:
        if other == commit:
            continue
        if _commit_trailers(repo_root, other).get("Workflow-Supersedes") == commit:
            return False
    return True


def discover_bundle_generation_record_commits(
    repo_root: Path, work_item_id: str, base_commit: str, head: str = "HEAD",
) -> dict[str, str]:
    """Every commit reachable in `base_commit..head` carrying an exact
    `Workflow-Bundle-Generation-Record: <work_item_id>/<implementation_revision>`
    + `Workflow-Work-Item: <work_item_id>` trailer pair, requiring exactly
    one match per `<work_item_id>/<implementation_revision>` value after
    the shared first-parent-ancestor tie-break, generalized (WF8c (c)/(b))
    with `_bundle_generation_record_chain_tip` as the role-specific
    verification predicate: once a same-content-republication or recovery
    commit lands, more than one commit legitimately shares the same
    trailer value (a `Workflow-Bundle-Generation-Record`-stable
    supersession chain), and this predicate resolves the chain's current
    tip exactly as `discover_checkpoint_commits` resolves its own
    role-specific "claims complete" tie-break. Returns
    `{"<work_item_id>/<implementation_revision>": commit_sha}`."""
    return _discover_trailer_commits(
        repo_root, "Workflow-Bundle-Generation-Record", work_item_id, base_commit, head,
        ambiguous_error_cls=AmbiguousBundleGenerationRecordTrailerError,
        verify=_bundle_generation_record_chain_tip,
    )


def discover_current_bundle_generation_record_commit(
    repo_root: Path, work_item_id: str, base_commit: str, head: str, implementation_revision: int,
) -> str | None:
    """The specific `Workflow-Bundle-Generation-Record` commit for this
    work item's *current* `implementation_revision`, or `None` if none is
    reachable."""
    matches = discover_bundle_generation_record_commits(repo_root, work_item_id, base_commit, head)
    return matches.get(f"{work_item_id}/{implementation_revision}")


def _commit_own_changed_paths(repo_root: Path, commit: str) -> set[str]:
    """The paths a single commit itself changes, relative to its own
    (first) parent -- distinct from `_changed_paths_between`, which
    diffs two arbitrary endpoints of a range."""
    parent = _run(["git", "rev-parse", f"{commit}^"], cwd=repo_root).strip()
    return _changed_paths_between(repo_root, parent, commit)


def _read_json_at_commit_or_empty(repo_root: Path, commit: str, rel_path: str) -> dict:
    """`git show <commit>:<rel_path>`, parsed as JSON, or `{}` if the path
    does not exist at that commit (e.g. a work item's very first
    Workflow-Bundle-Generation-Record commit, whose parent predates
    WORKFLOW_STATE.json's own creation)."""
    result = subprocess.run(
        ["git", "show", f"{commit}:{rel_path}"], cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {}
    return json.loads(result.stdout)


def _work_item_field_diff(repo_root: Path, commit: str, work_item_id: str) -> set[str]:
    """Field names that differ in `work_items[work_item_id]` between a
    commit's own parent and the commit itself, read from each side's
    actually-committed `WORKFLOW_STATE.json` -- never the working tree."""
    parent = _run(["git", "rev-parse", f"{commit}^"], cwd=repo_root).strip()
    state_rel = DEFAULT_STATE_PATH.as_posix()
    before = _read_json_at_commit_or_empty(repo_root, parent, state_rel)
    after = _read_json_at_commit_or_empty(repo_root, commit, state_rel)
    before_item = before.get("work_items", {}).get(work_item_id, {})
    after_item = after.get("work_items", {}).get(work_item_id, {})
    keys = set(before_item) | set(after_item)
    return {k for k in keys if before_item.get(k) != after_item.get(k)}


def _forbidden_state_mutation(repo_root: Path, commit: str, work_item_id: str) -> str | None:
    """Item 267's cross-work-item/top-level forbidden-mutation case:
    `_work_item_field_diff` only ever inspects `work_items[work_item_id]`,
    so a commit that *also* changes a top-level routing field (e.g.
    `active_work_item_id`) or a *different* work item's own entry in the
    same commit would pass that check unnoticed -- it is invisible to a
    diff scoped to one work item's own fields. Returns a description of
    the first such change found, or `None` if the commit's own
    `WORKFLOW_STATE.json` diff is contained entirely within
    `work_items[work_item_id]`."""
    parent = _run(["git", "rev-parse", f"{commit}^"], cwd=repo_root).strip()
    state_rel = DEFAULT_STATE_PATH.as_posix()
    before = _read_json_at_commit_or_empty(repo_root, parent, state_rel)
    after = _read_json_at_commit_or_empty(repo_root, commit, state_rel)
    before_top = {k: v for k, v in before.items() if k != "work_items"}
    after_top = {k: v for k, v in after.items() if k != "work_items"}
    changed_top = sorted(
        k for k in set(before_top) | set(after_top) if before_top.get(k) != after_top.get(k)
    )
    if changed_top:
        return f"top-level field(s) {changed_top}"
    before_items = before.get("work_items", {})
    after_items = after.get("work_items", {})
    other_ids = (set(before_items) | set(after_items)) - {work_item_id}
    changed_others = sorted(
        wid for wid in other_ids if before_items.get(wid) != after_items.get(wid)
    )
    if changed_others:
        return f"other work item(s) {changed_others}"
    return None


TECHNICAL_APPROVAL_COMMIT_FIELDS = frozenset({
    "technical_approval", "phase", "state_revision", "last_transition",
    # workflow-2.5.0 CP12 (disposable-repository functional validation):
    # widened unconditionally, mirroring ORDINARY_BUNDLE_GENERATION_RECORD_
    # FIELDS/RECOVERED_BUNDLE_GENERATION_RECORD_FIELDS' own identical
    # widening and identical reasoning -- a "2.2" item's ordinary positive
    # path always leaves MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW's own ledger
    # write (`record_manual_implementation_review`, itself never its own
    # dedicated durability commit -- see review-implementation.md's "2.2"
    # authoritative branch, step A6, and record-manual-implementation-
    # review.md's identical shape) sitting uncommitted until the very next
    # commit, which for a "2.2" item is always this one:
    # `/approve-review implementation`'s own technical-approval commit. Found
    # by CP12's own disposable-repository end-to-end scenario, which
    # exercises the real local-approve-then-manual-approve-then-approve-
    # sequence a hand-authored dict-state fixture never drove: every "2.2"
    # item's very first technical-approval commit failed
    # MalformedTechnicalApprovalCommitError outright before this widening,
    # since `implementation_review_stages` was missing from this set even
    # though its sibling generation-record sets already admit the identical
    # residue for the identical reason. Safe for "1"/"2.1": that vocabulary
    # is never written by their own state_transaction mutators, so it is
    # always absent from their own field diffs regardless of what this set
    # admits -- and this is not merely a reachability argument:
    # `_validate_implementation_review_stages` (run from `validate_state`)
    # rejects any non-"2.2" item carrying a non-null
    # `implementation_review_stages` at read time, so the vocabulary is
    # rejected, not just unreached.
    "implementation_review_stages",
})


class MalformedTechnicalApprovalCommitError(Exception):
    """Raised by `validate_technical_approval_commit` (`WF8c`, missing-test
    item 285) when a discovered `/approve-review implementation` commit's
    own `WORKFLOW_STATE.json` diff exceeds `apply_technical_approval`'s
    exhaustive field set -- mirroring items 267/254's exact-field-set
    discipline for generation-record commits, applied here to the
    technical-approval commit `D-States`'s own "Exit" bullet already names
    exhaustively (`GPT-R51-001`): `technical_approval`, `phase`
    (`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` ->
    `AWAITING_FUNCTIONAL_REVIEW`), `state_revision`, `last_transition` --
    plus, for a "2.2" item only, the uncommitted `implementation_review_
    stages` ledger residue a "2.2" item's own manual-approve round always
    leaves behind (workflow-2.5.0 CP12, mirroring the generation-record
    commit's own identical widening) -- no other field, and never another
    work item's own entry or a top-level routing field in the same
    commit."""


def validate_technical_approval_commit(repo_root: Path, commit: str, work_item_id: str) -> None:
    """`WF8c`, missing-test item 285: the technical-approval commit's own
    exhaustive field-mutation check, the sibling
    `validate_bundle_generation_record_commit` already provides for
    generation-record commits. Refuses via
    `MalformedTechnicalApprovalCommitError` when the commit's own
    `work_items[work_item_id]` diff is not a non-empty subset of
    `TECHNICAL_APPROVAL_COMMIT_FIELDS`, when a forbidden field named by
    that set is entirely absent from the diff (`technical_approval`/`phase`
    must both actually change -- a commit that sets one without the other
    is not a real technical-approval transition), or when the commit
    additionally touches a top-level routing field or a *different* work
    item's own entry (`_forbidden_state_mutation`, item 267's exact check,
    reused rather than duplicated)."""
    outside_diff = _forbidden_state_mutation(repo_root, commit, work_item_id)
    if outside_diff is not None:
        raise MalformedTechnicalApprovalCommitError(
            f"{commit} is a technical-approval commit for {work_item_id!r} but also "
            f"changed {outside_diff} -- a technical-approval commit may only ever "
            f"touch its own work item's fields (item 267)"
        )
    field_diff = _work_item_field_diff(repo_root, commit, work_item_id)
    if not field_diff or not field_diff <= TECHNICAL_APPROVAL_COMMIT_FIELDS:
        raise MalformedTechnicalApprovalCommitError(
            f"{commit}'s own {work_item_id!r} field changes are {sorted(field_diff)}, "
            f"not a non-empty subset of {sorted(TECHNICAL_APPROVAL_COMMIT_FIELDS)}"
        )
    if {"technical_approval", "phase"} - field_diff:
        raise MalformedTechnicalApprovalCommitError(
            f"{commit}'s own {work_item_id!r} field changes {sorted(field_diff)} do not "
            f"include both 'technical_approval' and 'phase' -- a technical-approval "
            f"commit must always record the approval and transition phase in the same "
            f"commit (mirrors OPUS-R101-001's identical requirement for generation-"
            f"record commits)"
        )


def validate_bundle_generation_record_commit(repo_root: Path, commit: str, work_item_id: str) -> None:
    """Validates a discovered `Workflow-Bundle-Generation-Record` commit
    against its own role-specific contract (D-Approval-Commits/D-Commit-
    Provenance condition 4, revision 28 onward for the ordinary role;
    WF8c (c)/(b) for the recovered role, `MalformedBundleGenerationRecordCommitError`'s
    own docstring has the full per-role field/phase rules): touches only
    `WORKFLOW_STATE.json`; changes nothing outside
    `work_items[work_item_id]` -- no top-level routing field and no
    *other* work item's own entry (item 267, `WF8c`); carries exactly one
    of the two legal trailer sets (`_bundle_generation_record_role`); and
    its own field changes / phase transition satisfy that role's exact
    contract. Raises `MalformedBundleGenerationRecordCommitError` naming
    the concrete mismatch otherwise -- a commit matching neither role's
    trailer set is rejected immediately, never coerced into one."""
    changed_paths = _commit_own_changed_paths(repo_root, commit)
    state_rel = DEFAULT_STATE_PATH.as_posix()
    if changed_paths != {state_rel}:
        raise MalformedBundleGenerationRecordCommitError(
            f"{commit} carries a Workflow-Bundle-Generation-Record trailer but "
            f"touches {sorted(changed_paths)}, not exactly {{{state_rel!r}}}"
        )
    outside_diff = _forbidden_state_mutation(repo_root, commit, work_item_id)
    if outside_diff is not None:
        raise MalformedBundleGenerationRecordCommitError(
            f"{commit} carries a Workflow-Bundle-Generation-Record trailer for "
            f"{work_item_id!r} but also changed {outside_diff} -- a "
            f"generation-record commit may only ever touch its own work item's "
            f"fields (item 267)"
        )
    trailers = _commit_trailers(repo_root, commit)
    role = _bundle_generation_record_role(trailers)
    if role is None:
        raise MalformedBundleGenerationRecordCommitError(
            f"{commit} carries trailer set {sorted(trailers)}, not exactly the "
            f"ordinary {{'Workflow-Bundle-Generation-Record', 'Workflow-Work-Item'}} "
            f"set or the recovered {{'Workflow-Bundle-Generation-Record', "
            f"'Workflow-Work-Item', 'Workflow-Supersedes'}} set"
        )
    field_diff = _work_item_field_diff(repo_root, commit, work_item_id)
    after = _read_json_at_commit_or_empty(repo_root, commit, state_rel)
    committed_work_item = after.get("work_items", {}).get(work_item_id, {})
    committed_phase = committed_work_item.get("phase")
    # workflow-2.5.0 CP3: read anchored to this exact commit's own
    # committed work_items[work_item_id] dict, never the live entry
    # (D-Implementation-Review-Stages "Provenance-interval interaction").
    governing_workflow_version = committed_work_item.get("governing_workflow_version")
    if role == "ordinary":
        if not field_diff or not field_diff <= ORDINARY_BUNDLE_GENERATION_RECORD_FIELDS:
            raise MalformedBundleGenerationRecordCommitError(
                f"{commit}'s own {work_item_id!r} field changes are {sorted(field_diff)}, "
                f"not a non-empty subset of {sorted(ORDINARY_BUNDLE_GENERATION_RECORD_FIELDS)} "
                f"(ordinary role)"
            )
        if "phase" not in field_diff:
            raise MalformedBundleGenerationRecordCommitError(
                f"{commit}'s own {work_item_id!r} field changes {sorted(field_diff)} do not "
                f"include 'phase' -- an ordinary bundle-generation-record commit must always "
                f"transition phase (OPUS-R101-001)"
            )
        # Stage is irrelevant here -- bundle_generation_target_phase's
        # value depends only on governing_workflow_version, identical for
        # both "implementation"/"post-fix" at any single version -- so
        # "implementation" is passed as an arbitrary, invariant witness.
        required_target = bundle_generation_target_phase("implementation", governing_workflow_version)
        if committed_phase != required_target:
            raise MalformedBundleGenerationRecordCommitError(
                f"{commit} sets {work_item_id!r}'s phase to {committed_phase!r}, not the "
                f"required target {required_target!r} (governing_workflow_version "
                f"{governing_workflow_version!r})"
            )
        return
    # role == "recovered" (WF8c (c)/(b), D-Commit-Provenance condition 4's
    # recovered-role clause): reviewed_implementation_head/implementation_revision
    # must never appear in field_diff -- excluding them from
    # RECOVERED_BUNDLE_GENERATION_RECORD_FIELDS enforces that directly.
    if not field_diff or not field_diff <= RECOVERED_BUNDLE_GENERATION_RECORD_FIELDS:
        raise MalformedBundleGenerationRecordCommitError(
            f"{commit}'s own {work_item_id!r} field changes are {sorted(field_diff)}, "
            f"not a non-empty subset of {sorted(RECOVERED_BUNDLE_GENERATION_RECORD_FIELDS)} "
            f"(recovered role) -- reviewed_implementation_head/implementation_revision "
            f"must never change in a recovered-role commit"
        )
    # workflow-2.5.0 CP3: a membership test, not a single-valued equality
    # (D-Implementation-Review-Stages "Provenance-interval interaction",
    # third fix) -- {AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW} for "1"/"2.1",
    # the three-phase "2.2" set otherwise.
    legal_committed_phases = bundle_generation_recovered_role_legal_committed_phases(
        governing_workflow_version,
    )
    if committed_phase not in legal_committed_phases:
        raise MalformedBundleGenerationRecordCommitError(
            f"{commit} sets {work_item_id!r}'s phase to {committed_phase!r}, not one of "
            f"the required target phases {sorted(legal_committed_phases)} "
            f"(governing_workflow_version {governing_workflow_version!r})"
        )
    parent = _run(["git", "rev-parse", f"{commit}^"], cwd=repo_root).strip()
    parent_phase = _read_json_at_commit_or_empty(repo_root, parent, state_rel).get(
        "work_items", {},
    ).get(work_item_id, {}).get("phase")
    if parent_phase not in RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES:
        raise MalformedBundleGenerationRecordCommitError(
            f"{commit}'s parent {parent} has {work_item_id!r}'s phase as {parent_phase!r}, "
            f"not one of the legal recovered-role source phases "
            f"{sorted(RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES)}"
        )


def _generation_record_interval_first_parent_members(repo_root: Path, older: str, newer: str) -> list[str]:
    """The strict `older..newer` first-parent interval, newest-first,
    excluding `older` and including `newer` -- shared by
    `verify_implementation_provenance_interval` (`reviewed_implementation_head..T`)
    and same-content republication's own precondition
    (WF8c (c), `T..HEAD`; `resolve_bundle_generation_outcome`). Raises
    `ReviewedImplementationHeadNotAncestorError` if `older` is not
    reachable from `newer` at all, `NonFirstParentProvenanceIntervalError`
    if reachable only off `newer`'s first-parent chain."""
    if not _is_ancestor(repo_root, older, newer):
        raise ReviewedImplementationHeadNotAncestorError(
            f"{older} is not an ancestor of {newer}"
        )
    first_parent_chain = _first_parent_commits_ordered(repo_root, newer)
    if older not in first_parent_chain:
        raise NonFirstParentProvenanceIntervalError(
            f"{older} is reachable from {newer} but not via {newer}'s first-parent "
            f"chain -- the interval crosses a merge or a non-first-parent path"
        )
    older_index = first_parent_chain.index(older)
    return first_parent_chain[:older_index]  # newest-first: [newer, ..., commit-right-after-older]


def _classify_generation_record_interval(
    repo_root: Path, work_item_id: str, implementation_revision: int,
    chronological_commits: list[str],
    impl_classification: tuple[Mapping[str, str], Mapping[str, str], Mapping[str, str], Mapping[str, str]],
) -> str | None:
    """D-Commit-Provenance condition 3, generalized by role (WF8c (c)/(b)):
    validates every commit in `chronological_commits` (oldest first) --
    shared by `verify_implementation_provenance_interval`'s own
    non-terminal `P..T` walk and same-content republication's own
    precondition-2 `T..HEAD` walk (D-Commit-Provenance "run the exact same
    two-part precondition ... over the interval T..HEAD in place of
    reviewed_implementation_head..HEAD"). A commit carrying a
    `Workflow-Bundle-Generation-Record` trailer for this exact
    `(work_item_id, implementation_revision)` pair must satisfy its own
    role-specific commit contract (`validate_bundle_generation_record_commit`)
    *and* chain-continuity (D-Commit-Provenance "Multiple sequential
    recoveries / supersession chain"): the chain's earliest member must be
    ordinary-role, every later member must be recovered-role with its
    `Workflow-Supersedes` trailer naming exactly the immediately preceding
    generation-record commit found earlier in this same walk
    (`MalformedProvenanceSupersessionChainError` otherwise -- no fork, no
    cycle, no skipped link). Every other commit must classify
    implementation-stage excluded-only (`ProtectedPathInProvenanceIntervalError`),
    and every commit must have exactly one parent
    (`NonFirstParentProvenanceIntervalError` on a merge). Returns the last
    generation-record commit found in the walk (or `None` if none was),
    for the caller to chain-continuity-check its own terminal commit
    against (see `_assert_generation_record_terminal_chain_continuity`)."""
    impl_protected_paths, impl_protected_prefixes, impl_excluded_paths, impl_excluded_prefixes = (
        impl_classification
    )
    pair_value = f"{work_item_id}/{implementation_revision}"
    last_generation_record_commit: str | None = None
    for commit in chronological_commits:
        parents = _run(["git", "rev-parse", f"{commit}^@"], cwd=repo_root).split()
        if len(parents) != 1:
            raise NonFirstParentProvenanceIntervalError(
                f"{commit} in the provenance interval is a merge commit "
                f"({len(parents)} parents) -- the interval must be a plain first-parent chain"
            )
        trailers = _commit_trailers(repo_root, commit)
        if (
            trailers.get("Workflow-Work-Item") == work_item_id
            and trailers.get("Workflow-Bundle-Generation-Record") == pair_value
        ):
            validate_bundle_generation_record_commit(repo_root, commit, work_item_id)
            role = _bundle_generation_record_role(trailers)
            if role == "ordinary":
                if last_generation_record_commit is not None:
                    raise MalformedProvenanceSupersessionChainError(
                        f"{commit} is an ordinary-role Workflow-Bundle-Generation-Record "
                        f"commit for {pair_value!r}, but {last_generation_record_commit} "
                        f"already precedes it in this provenance interval -- only the "
                        f"chain's earliest member may be ordinary-role"
                    )
            else:  # "recovered"
                supersedes = trailers.get("Workflow-Supersedes")
                if last_generation_record_commit is None or supersedes != last_generation_record_commit:
                    raise MalformedProvenanceSupersessionChainError(
                        f"{commit}'s Workflow-Supersedes trailer names {supersedes!r}, but "
                        f"the immediately preceding generation-record commit in this "
                        f"provenance interval is {last_generation_record_commit!r}"
                    )
            last_generation_record_commit = commit
            continue
        for path in sorted(_commit_own_changed_paths(repo_root, commit)):
            try:
                classification = fingerprint.classify_path_implementation_stage(
                    path, impl_protected_paths, impl_protected_prefixes,
                    impl_excluded_paths, impl_excluded_prefixes,
                )
            except fingerprint.UnclassifiedPathError:
                # Item 248 (WF8c): `classify_path_implementation_stage` fails
                # closed by raising rather than returning "unclassified", so
                # without this branch an unclassified path's own commit was
                # never named in the refusal the way a protected path's is.
                raise ProtectedPathInProvenanceIntervalError(
                    f"{commit} in the provenance interval touches {path!r}, classified "
                    f"'unclassified', not excluded"
                ) from None
            if classification != "excluded":
                raise ProtectedPathInProvenanceIntervalError(
                    f"{commit} in the provenance interval touches {path!r}, classified "
                    f"{classification!r}, not excluded"
                )
    return last_generation_record_commit


def _assert_generation_record_terminal_chain_continuity(
    repo_root: Path, work_item_id: str, terminal_commit: str, last_generation_record_commit: str | None,
) -> None:
    """Extends `_classify_generation_record_interval`'s chain-continuity
    rule to a walk's own terminal member -- `verify_implementation_provenance_interval`'s
    `T`, always itself a generation-record commit, unlike an ordinary
    `T..HEAD` same-content-republication walk's own terminal member
    (`HEAD`, not yet a generation-record commit, so this check does not
    apply there). Assumes
    `validate_bundle_generation_record_commit(repo_root, terminal_commit, work_item_id)`
    already succeeded, so `_bundle_generation_record_role` cannot return
    `None` here."""
    trailers = _commit_trailers(repo_root, terminal_commit)
    role = _bundle_generation_record_role(trailers)
    if role == "ordinary":
        if last_generation_record_commit is not None:
            raise MalformedProvenanceSupersessionChainError(
                f"{terminal_commit} is an ordinary-role terminal "
                f"Workflow-Bundle-Generation-Record commit, but "
                f"{last_generation_record_commit} already precedes it in the provenance "
                f"interval -- only the chain's earliest member may be ordinary-role"
            )
        return
    supersedes = trailers.get("Workflow-Supersedes")
    if last_generation_record_commit is None or supersedes != last_generation_record_commit:
        raise MalformedProvenanceSupersessionChainError(
            f"{terminal_commit}'s Workflow-Supersedes trailer names {supersedes!r}, but "
            f"the immediately preceding generation-record commit in the provenance "
            f"interval is {last_generation_record_commit!r}"
        )


def _other_revision_found_suffix(
    repo_root: Path, work_item_id: str, base_commit: str, head: str, implementation_revision: int,
) -> str:
    """Item 220 (`WF8c`): when no `Workflow-Bundle-Generation-Record`
    commit matches the *current* `implementation_revision`, but this work
    item's trailer discovery still turned up commit(s) for some *other*
    revision (the previous round's, or a skipped-ahead value), name them
    too -- the caller should see what was actually found, not only what
    was expected, to tell "the previous round's stale trailer, never
    superseded" apart from "genuinely nothing recorded yet"."""
    other_values = sorted(
        v for v in discover_bundle_generation_record_commits(repo_root, work_item_id, base_commit, head)
        if v != f"{work_item_id}/{implementation_revision}"
    )
    if not other_values:
        return ""
    return f" (found instead: {', '.join(other_values)})"


def verify_implementation_provenance_interval(
    repo_root: Path, work_item: dict, base_commit: str, head: str = "HEAD",
) -> str:
    """D-Commit-Provenance's "Provenance interval" check (revision 28
    onward, `WF8B-003`) -- the real replacement for a bare
    `reviewed_implementation_head == HEAD` comparison, which the
    contradiction below has been proven to permanently re-break:
    `record_bundle_generation`'s write must become a durable commit for
    fresh-session resumption, and that durability commit is, by
    definition, one commit ahead of whatever `reviewed_implementation_head`
    itself names.

    Given `P = reviewed_implementation_head` and the discovered current
    `Workflow-Bundle-Generation-Record` commit `T` for this work item's
    `implementation_revision`:

    1. Live `head` must equal `T` exactly -- not merely a descendant of it
       (`HeadPastBundleGenerationRecordError`).
    2. `P` must be reachable from `T` via `T`'s own first-parent chain,
       never merely reachable by some other path
       (`ReviewedImplementationHeadNotAncestorError` if not reachable at
       all, `NonFirstParentProvenanceIntervalError` if reachable only off
       the first-parent chain); every commit strictly inside the interval
       must itself have exactly one parent
       (`NonFirstParentProvenanceIntervalError` on a merge).
    3. Every non-terminal commit in the interval (strictly between `P`
       and `T`) either classifies implementation-stage excluded-only, in
       its own right -- never merely net-unchanged across the whole
       interval (`ProtectedPathInProvenanceIntervalError`) -- or, if it is
       itself a historical `Workflow-Bundle-Generation-Record` commit for
       this exact pair (a prior same-content-republication/recovery link,
       WF8c (c)/(b)), satisfies its own role-specific contract and
       chain-continuity instead (`_classify_generation_record_interval`).
    4. `T` itself must pass `validate_bundle_generation_record_commit`.
    5. If `T` is itself recovered-role, its `Workflow-Supersedes` trailer
       must name exactly the immediately preceding generation-record
       commit found by condition 3's walk -- the chain's own linked-list
       property, checked at the terminus
       (`_assert_generation_record_terminal_chain_continuity`).

    Returns `T` on success. Raises `BundleGenerationRecordNotFoundError`
    if `reviewed_implementation_head`/`implementation_revision` is not
    yet set, or no matching commit is discoverable at all."""
    work_item_id = work_item["work_item_id"]
    p = work_item.get("reviewed_implementation_head")
    implementation_revision = work_item.get("implementation_revision")
    if not p or not implementation_revision:
        raise BundleGenerationRecordNotFoundError(
            f"{work_item_id!r} has no reviewed_implementation_head/implementation_revision yet"
        )
    t = discover_current_bundle_generation_record_commit(
        repo_root, work_item_id, base_commit, head, implementation_revision,
    )
    if t is None:
        raise BundleGenerationRecordNotFoundError(
            f"no Workflow-Bundle-Generation-Record commit found for "
            f"{work_item_id}/{implementation_revision} in {base_commit}..{head}"
            f"{_other_revision_found_suffix(repo_root, work_item_id, base_commit, head, implementation_revision)}"
        )
    live_head = _run(["git", "rev-parse", head], cwd=repo_root).strip()
    if live_head != t:
        raise HeadPastBundleGenerationRecordError(
            f"live HEAD {live_head} is not exactly the discovered "
            f"Workflow-Bundle-Generation-Record commit {t} for "
            f"{work_item_id}/{implementation_revision} -- a further commit landed "
            f"after it carrying no provenance record of its own"
        )
    interval = _generation_record_interval_first_parent_members(repo_root, p, t)
    non_terminal = interval[1:]  # excludes t itself
    impl_classification = fingerprint.load_implementation_stage_classification(
        repo_root, fingerprint.artifacts_path_for_work_item(work_item_id),
    )
    last_generation_record_commit = _classify_generation_record_interval(
        repo_root, work_item_id, implementation_revision,
        list(reversed(non_terminal)),  # chronological: oldest (right after p) first
        impl_classification,
    )
    validate_bundle_generation_record_commit(repo_root, t, work_item_id)
    _assert_generation_record_terminal_chain_continuity(
        repo_root, work_item_id, t, last_generation_record_commit,
    )
    return t


def implementation_provenance_interval_reachable(
    repo_root: Path, work_item: dict, base_commit: str, head: str = "HEAD",
) -> bool:
    """Boolean wrapper for `technical_approval_gate_reachable`'s
    `head_matches_reviewed_implementation_head` argument: `True` exactly
    when `verify_implementation_provenance_interval` finds a valid
    interval, `False` for any of its named refusal reasons. A caller that
    needs the concrete refusal reason (e.g. `/approve-review implementation`'s
    own step 1 report) should call `verify_implementation_provenance_interval`
    directly instead."""
    try:
        verify_implementation_provenance_interval(repo_root, work_item, base_commit, head)
        return True
    except (
        BundleGenerationRecordNotFoundError,
        HeadPastBundleGenerationRecordError,
        ReviewedImplementationHeadNotAncestorError,
        NonFirstParentProvenanceIntervalError,
        ProtectedPathInProvenanceIntervalError,
        MalformedBundleGenerationRecordCommitError,
        MalformedProvenanceSupersessionChainError,
        AmbiguousBundleGenerationRecordTrailerError,
    ):
        return False


def resolve_bundle_generation_outcome(
    repo_root: Path, work_item: dict, *, base_commit: str, head: str,
) -> tuple[str, str | None]:
    """D-Commit-Provenance's "Same-content post-fix republication" (WF8c
    (c)): determines which of `record_bundle_generation`'s two legal
    outcomes a candidate `head` produces for this work item, *before* the
    caller decides which commit-trailer set to write and which `outcome`
    to pass `record_bundle_generation`. Deliberately kept separate from
    `record_bundle_generation` itself (a pure `state -> state` mutator,
    never reaching into Git) -- this function is the read-only,
    Git-inspecting half of the same decision.

    Recomputes the protected implementation-stage `review_content_id` at
    candidate `head` and compares it to the value recomputed from the
    commit `reviewed_implementation_head` currently names (`P`):

    - No prior round yet (`reviewed_implementation_head`/
      `implementation_revision` unset) or the two digests genuinely
      differ -> `("ordinary", None)`: this is a new round (or the very
      first), handled entirely by the existing single-commit path.
    - The two digests are byte-identical -> discovers the work item's
      current `Workflow-Bundle-Generation-Record` commit `T` and runs the
      *same* role-aware interval classification
      `verify_implementation_provenance_interval` itself uses
      (`_classify_generation_record_interval`, "the same precondition ...
      over the interval T..HEAD in place of reviewed_implementation_head..HEAD"),
      never a separately maintained duplicate rule. If the whole `T..HEAD`
      interval classifies cleanly, returns `("same_content", t)` -- `t`
      the commit the caller's new recovered-role commit must supersede.
      If it does not, this function raises outright (naming the offending
      commit via whichever of `NonFirstParentProvenanceIntervalError`/
      `ProtectedPathInProvenanceIntervalError`/
      `MalformedBundleGenerationRecordCommitError`/
      `MalformedProvenanceSupersessionChainError` the classification
      itself raises) -- "a fail-closed backstop that should not be
      reachable through any documented `/apply-implementation-review`
      disposition path, not an expected outcome" (plan text), never a
      silent fall-back to `"ordinary"`.

    Raises `BundleGenerationRecordNotFoundError` if the digests match but
    no current `Workflow-Bundle-Generation-Record` commit is discoverable
    at all for this work item's `implementation_revision` -- genuinely
    unreachable in practice (identical protected content implies a prior
    round already ran and recorded one), kept as a fail-closed backstop
    rather than silently treated as `"ordinary"`."""
    work_item_id = work_item["work_item_id"]
    p = work_item.get("reviewed_implementation_head")
    implementation_revision = work_item.get("implementation_revision")
    if not p or not implementation_revision:
        return "ordinary", None
    artifacts_path = fingerprint.artifacts_path_for_work_item(work_item_id)
    work_item_type = work_item["work_item_type"]
    head_digest = approval_review_content_id(
        repo_root, stage="implementation", base_commit=base_commit,
        work_item_type=work_item_type, work_item_id=work_item_id,
        head=head, artifacts_path=artifacts_path,
    )
    p_digest = approval_review_content_id(
        repo_root, stage="implementation", base_commit=base_commit,
        work_item_type=work_item_type, work_item_id=work_item_id,
        head=p, artifacts_path=artifacts_path,
    )
    if head_digest != p_digest:
        return "ordinary", None
    t = discover_current_bundle_generation_record_commit(
        repo_root, work_item_id, base_commit, head, implementation_revision,
    )
    if t is None:
        raise BundleGenerationRecordNotFoundError(
            f"{work_item_id!r}'s candidate head {head} has protected implementation-stage "
            f"content identical to reviewed_implementation_head {p}, but no current "
            f"Workflow-Bundle-Generation-Record commit is discoverable for "
            f"{work_item_id}/{implementation_revision} in {base_commit}..{head} to supersede"
        )
    interval = _generation_record_interval_first_parent_members(repo_root, t, head)
    impl_classification = fingerprint.load_implementation_stage_classification(
        repo_root, artifacts_path,
    )
    _classify_generation_record_interval(
        repo_root, work_item_id, implementation_revision,
        list(reversed(interval)),  # chronological: oldest (right after t) first
        impl_classification,
    )
    return "same_content", t


# ---------------------------------------------------------------------------
# D-Commit-Provenance "Stale generation_head recovery" (WF8c (b), `WFR-62`):
# the dedicated `/recover-implementation-provenance` command's own two
# primitives -- a read-only precondition (`verify_implementation_provenance_
# recovery`) and the recovered-role commit's state half
# (`apply_implementation_provenance_recovery`). Both reuse WF8c (c)'s own
# `resolve_bundle_generation_outcome`/`_classify_generation_record_interval`
# machinery rather than a second, separately maintained copy of the same
# content-equality-plus-interval-classification check -- the two commands
# differ only in *which* phase may invoke them and in leaving `phase`
# value-unchanged rather than transitioning it.
# ---------------------------------------------------------------------------


class IllegalImplementationProvenanceRecoverySourcePhaseError(Exception):
    """Raised when `verify_implementation_provenance_recovery`/
    `apply_implementation_provenance_recovery` is invoked from a phase
    outside `bundle_generation_recovered_role_legal_committed_phases(
    governing_workflow_version)` -- for `"1"`/`"2.1"` (and an absent
    version) that is the single phase `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`,
    byte-identical to pre-workflow-2.5.0 behavior; for `"2.2"` it is the
    three phases a `"2.2"` item can occupy between a generation-record
    commit `T` and technical approval
    (`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`,
    `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`,
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) -- the only phases
    `/recover-implementation-provenance` (WF8c (b)) may run from: recovery
    repairs a stale `generation_head` for the round *currently* awaiting
    (local, manual-external, or external) review, never a round still
    being written (`SELF_REVIEWING_IMPLEMENTATION`/`APPLYING_REVIEW_FEEDBACK`
    already have their own same-content path through
    `record_bundle_generation`'s `outcome="same_content"`, WF8c (c)) and
    never any other phase."""


class ImplementationProvenanceRecoveryNotApplicableError(Exception):
    """Raised when `resolve_bundle_generation_outcome` returns anything
    other than `("same_content", t)` for the candidate recovery head --
    either no prior round exists yet, or the protected implementation-stage
    content at the candidate head genuinely differs from
    `reviewed_implementation_head`'s, or the candidate head is already the
    current `Workflow-Bundle-Generation-Record` commit itself (nothing to
    recover -- an idempotent-retry no-op, never a redundant second `S2`).
    Recovery exists only to repair a `generation_head` staled by a
    legitimate excluded-only commit landing after `T`; content that has
    genuinely changed needs a fresh bundle-generation round
    (`record_bundle_generation` with `outcome="ordinary"`), never this
    command."""


def verify_implementation_provenance_recovery(
    repo_root: Path, work_item: dict, *, base_commit: str, head: str = "HEAD",
) -> str:
    """`/recover-implementation-provenance`'s own read-only precondition
    pair (WF8c (b), `WFR-62`): refuses outright unless (a) the work item's
    current phase is exactly `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
    (`IllegalImplementationProvenanceRecoverySourcePhaseError`), (b)
    `resolve_bundle_generation_outcome` -- the identical content-equality-
    plus-interval-classification precondition WF8c (c) already built and
    tested for `record_bundle_generation`'s own same-content path, never a
    second, separately maintained copy -- resolves to `("same_content",
    t)` for candidate `head` against live `T`, and (c) `t` is not already
    `head` itself (`ImplementationProvenanceRecoveryNotApplicableError` for
    either failure, or whichever of `resolve_bundle_generation_outcome`'s
    own named exceptions the classification itself raises: a fail-closed
    backstop, never a silent proceed). Returns `t`, the commit the
    caller's `S2` recovery commit must supersede, on success -- performs
    no state mutation and no Git write of its own."""
    work_item_id = work_item["work_item_id"]
    phase = work_item.get("phase")
    legal_phases = bundle_generation_recovered_role_legal_committed_phases(
        work_item.get("governing_workflow_version"),
    )
    if phase not in legal_phases:
        raise IllegalImplementationProvenanceRecoverySourcePhaseError(
            f"recover_implementation_provenance invoked for {work_item_id!r} from phase "
            f"{phase!r}, but the only legal source phase(s) are {sorted(legal_phases)}"
        )
    outcome, t = resolve_bundle_generation_outcome(
        repo_root, work_item, base_commit=base_commit, head=head,
    )
    if outcome != "same_content" or t is None:
        raise ImplementationProvenanceRecoveryNotApplicableError(
            f"{work_item_id!r}'s candidate head {head} does not qualify for recovery -- "
            f"resolve_bundle_generation_outcome returned {(outcome, t)!r}, not "
            f"('same_content', <commit>); protected implementation-stage content has "
            f"genuinely changed (or no prior round exists), so a fresh bundle-generation "
            f"round is required instead of recovery"
        )
    resolved_head = _run(["git", "rev-parse", head], cwd=repo_root).strip()
    if t == resolved_head:
        raise ImplementationProvenanceRecoveryNotApplicableError(
            f"{work_item_id!r}'s candidate head {resolved_head} is already the current "
            f"Workflow-Bundle-Generation-Record commit -- nothing to recover"
        )
    return t


def validate_implementation_provenance_recovery_confirmation(
    text: str, *, work_item_id: str, superseded_commit: str,
) -> None:
    """`/recover-implementation-provenance`'s own user-confirmation guard
    (`WF8c` (b), missing-test item 251): "identical in spirit to
    `/approve-review`'s own `validate_user_confirmation` guard" -- the text
    must be non-empty and must literally name both the exact `work_item_id`
    and the exact superseded commit SHA `t` (`verify_implementation_provenance_recovery`'s
    own return value), never merely a generic go-ahead. Recovery is not an
    approval stage (`validate_user_confirmation`'s own `stage` parameter
    requires membership in `APPROVAL_STAGES`, which this operation is not
    a member of), so this is a dedicated sibling rather than a call to that
    function with a stage name shoehorned in."""
    if not text or not text.strip():
        raise UserConfirmationRejectedError(
            f"user_confirmation is empty -- must name work_item_id {work_item_id!r} "
            f"and the exact superseded commit {superseded_commit!r} explicitly"
        )
    if work_item_id not in text:
        raise UserConfirmationRejectedError(
            f"user_confirmation does not name work_item_id {work_item_id!r}: {text!r}"
        )
    if superseded_commit not in text:
        raise UserConfirmationRejectedError(
            f"user_confirmation does not name the exact superseded commit "
            f"{superseded_commit!r}: {text!r}"
        )


def apply_implementation_provenance_recovery(state: dict, work_item_id: str, now: str) -> dict:
    """The state half of `/recover-implementation-provenance`'s `S2`
    commit (WF8c (b), `WFR-62`): recovery never changes `phase`'s *value*
    -- the work item was already at one of
    `bundle_generation_recovered_role_legal_committed_phases(
    governing_workflow_version)` and remains there -- only
    `state_revision`/`last_transition` change, the recovered-role field
    set `record_bundle_generation`'s own `same_content` outcome already
    uses, minus `phase` itself since there is no transition to *perform*
    here (unlike that function's own legal source phases -- `SELF_
    REVIEWING_IMPLEMENTATION` for `stage="implementation"`;
    `APPLYING_REVIEW_FEEDBACK`, or `AWAITING_FUNCTIONAL_REVIEW` with a
    `STALE` `technical_approval`, for `stage="post-fix"` -- every one of
    which does transition into this phase; `workflow-v2-3-followups`
    continued scope widened this from two to three).
    `reviewed_implementation_head`/`implementation_revision` are never
    touched, exactly as the recovered role requires. Refuses via
    `IllegalImplementationProvenanceRecoverySourcePhaseError` if the
    freshly re-read state's phase is not a member of that legal set at the
    moment this mutator actually runs inside `state_transaction`'s lock --
    an independent check, never merely trusting the caller's own
    already-passed `verify_implementation_provenance_recovery`
    precondition, since a race could have moved the phase between that
    read and this write."""
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    phase = work_item.get("phase")
    legal_phases = bundle_generation_recovered_role_legal_committed_phases(
        work_item.get("governing_workflow_version"),
    )
    if phase not in legal_phases:
        raise IllegalImplementationProvenanceRecoverySourcePhaseError(
            f"apply_implementation_provenance_recovery invoked for {work_item_id!r} from "
            f"phase {phase!r}, but the only legal source phase(s) are {sorted(legal_phases)}"
        )
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    return new_state


# ---------------------------------------------------------------------------
# Functional-checklist evidence trailer discovery: `/prepare-functional-
# review`'s dedicated, content-idempotent checklist-evidence commit, and the
# round-scoped lookup `/prepare-functional-review` and `/review-functional`
# both read it back through.
#
# `D-Scoped-Remediation-Acceptance`'s own machinery -- the scoped-remediation
# trailer discovery, the pre-commit evidence guard and its live snapshot, the
# confirmation binding-field parser, the round replay/duplicate classifier
# and the acceptance record writer -- was removed with
# `/accept-scoped-remediation` itself (ledger `I10`): every one of those
# helpers had exactly one caller, that command, and its gate was unreachable
# through every supported lifecycle. The evidence *production* and *lookup*
# below stay: both are reached by supported commands, and both survive a
# bounded functional-fix round, which advances `implementation_revision` and
# so needs its own round-scoped evidence (ledger `I7`).
# ---------------------------------------------------------------------------

def discover_functional_checklist_commits(
    repo_root: Path, work_item_id: str, base_commit: str, head: str = "HEAD",
) -> dict[str, str]:
    """Every commit reachable in `base_commit..head` carrying an exact
    `Workflow-Functional-Checklist: <work_item_id>/<implementation_revision>/
    <checklist_blob>` + `Workflow-Work-Item: <work_item_id>` trailer pair
    (revision 26, `GPT-R39-001`'s content-scoped trailer value). Returns
    `{"<work_item_id>/<implementation_revision>/<blob>": commit_sha}` --
    raises only for a genuine identical-*content* duplicate reachable by
    more than one first-parent path, never for an ordinary content
    revision, since each distinct checklist content is its own distinct
    key by construction."""
    return _discover_trailer_commits(
        repo_root, "Workflow-Functional-Checklist", work_item_id, base_commit, head,
        ambiguous_error_cls=AmbiguousFunctionalChecklistTrailerError,
    )


def discover_current_functional_checklist_evidence(
    repo_root: Path, work_item_id: str, base_commit: str, head: str, implementation_revision: int,
) -> dict[str, str] | None:
    """Revision 26 (`GPT-R39-001`)'s round-scoped, content-identity-aware
    lookup: enumerates every `Workflow-Functional-Checklist` trailer value
    starting with the round-scoped prefix `<work_item_id>/
    <implementation_revision>/` (not a single exact-key lookup, since the
    trailer value also embeds the checklist's own blob), and returns the
    entry whose commit is nearest `head` -- the round's *current* evidence.
    Multiple historical evidence commits for the same round (an older,
    superseded checklist plus a newer, corrected one) coexist without
    ambiguity by construction, since each has its own distinct trailer
    value. Returns `{"commit_sha": ..., "blob": ...}`, or `None` if no
    evidence commit exists for the round at all. Raises
    `NonFirstParentFunctionalChecklistEvidenceError` (revision 27 correction,
    `GPT-R41-002`) when round-scoped evidence commits exist in
    `base_commit..head` but none sits on `head`'s first-parent chain --
    e.g. evidence prepared on a side branch that was merged without ever
    becoming a first-parent transition. Never falls back to picking one
    such candidate by ordinary reachable-history order."""
    matches = discover_functional_checklist_commits(repo_root, work_item_id, base_commit, head)
    prefix = f"{work_item_id}/{implementation_revision}/"
    candidates = {value: commit for value, commit in matches.items() if value.startswith(prefix)}
    if not candidates:
        return None
    commit_to_blob = {commit: value[len(prefix):] for value, commit in candidates.items()}
    for commit in _first_parent_commits_ordered(repo_root, head):
        if commit in commit_to_blob:
            return {"commit_sha": commit, "blob": commit_to_blob[commit]}
    # Every candidate is reachable in base_commit..head (the discovery call
    # above already proved that) but none sits on head's first-parent
    # chain -- e.g. a merged side branch whose evidence commit never became
    # a first-parent transition. Fail closed with a named, actionable error
    # rather than picking one candidate by ordinary reachable-history order
    # (GPT-R41-002): silently accepting a non-first-parent candidate would
    # let side-branch evidence become authoritative despite the resolver's
    # own first-parent contract.
    raise NonFirstParentFunctionalChecklistEvidenceError(
        f"{len(commit_to_blob)} evidence commit(s) found for {prefix.rstrip('/')} in "
        f"{base_commit}..{head}, but none is on {head}'s first-parent chain: "
        f"{sorted(commit_to_blob)} -- re-commit the checklist evidence directly on "
        f"the first-parent line"
    )


# ---------------------------------------------------------------------------
# WF4a-iv: D-Plan-Review-Stages -- two-stage local-then-manual-external
# plan-review protocol's ledger writers and verdict/state transition table
# ---------------------------------------------------------------------------


def _require_v2_1_plan_review(work_item: dict) -> None:
    """Widened workflow-2.5.0 from a bare `governing_workflow_version !=
    "2.1"` check to `TWO_STAGE_PLAN_REVIEW_VERSIONS` membership: a `"2.2"`
    item runs the identical two-stage plan-review protocol a `"2.1"` item
    does (D-Implementation-Review-Version-Activation's inheritance rule).
    Function name kept unchanged -- it is a private helper, and every
    caller's own name (`validate_local_plan_review_preconditions`, etc.)
    already reads as version-neutral."""
    if work_item.get("governing_workflow_version") not in TWO_STAGE_PLAN_REVIEW_VERSIONS:
        raise WrongGoverningVersionForPlanReviewStageError(
            f"{work_item['work_item_id']}: governing_workflow_version is "
            f"{work_item.get('governing_workflow_version')!r}, not one of "
            f"{sorted(TWO_STAGE_PLAN_REVIEW_VERSIONS)} -- the two-stage plan-review "
            f"protocol applies to \"2.1\"/\"2.2\" work items alike"
        )


def validate_local_plan_review_preconditions(work_item: dict) -> None:
    """`/review-plan`'s resolution/phase preconditions (D-Plan-Review-Stages):
    the item must be `"2.1"`-governed and currently at
    `AWAITING_LOCAL_PLAN_REVIEW`. Bundle/manifest staleness is a separate,
    generic check (D-Bundle-Manifest, reused unchanged) run by the command
    itself before this, not duplicated here."""
    _require_v2_1_plan_review(work_item)
    if work_item.get("phase") != "AWAITING_LOCAL_PLAN_REVIEW":
        raise WrongPhaseForPlanReviewStageError(
            f"{work_item['work_item_id']}: phase is {work_item.get('phase')!r}, "
            f"not \"AWAITING_LOCAL_PLAN_REVIEW\" -- /review-plan refuses rather "
            f"than silently re-running (e.g. already completed this round)"
        )


def record_local_plan_review(
    state: dict, work_item_id: str, *, verdict: str, bundle_id: str,
    review_content_id: str, round: int, now: str,
) -> dict:
    """`/review-plan`'s sole state write set (D-Plan-Review-Stages transition
    table, resolves `GPT-R12-001`/`-002`):

    - `APPROVE`: records the completed `LOCAL_MODEL_PLAN_REVIEW` stage
      against `review_content_id` (starting a fresh ledger scoped to this
      content id -- any prior `MANUAL_EXTERNAL_PLAN_REVIEW` entry
      necessarily belonged to a different, now-stale content id under the
      correct flow, so it is not carried forward) and transitions to
      `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`.
    - `REVISE`: no ledger write; transitions to `REVISING_PLAN`. Can never
      reach `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` (missing-test item 94).
    - `BLOCK`: no ledger write, no phase transition -- a true no-op
      (missing-test item 95); the returned state is unchanged.
    """
    if verdict not in PLAN_REVIEW_VERDICTS:
        raise UnknownPlanReviewVerdictError(f"unknown plan-review verdict: {verdict!r}")
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    validate_local_plan_review_preconditions(work_item)

    if verdict == "APPROVE":
        work_item["plan_review_stages"] = {
            "review_content_id": review_content_id,
            LOCAL_MODEL_PLAN_REVIEW: {
                "bundle_id": bundle_id, "verdict": "APPROVE",
                "round": round, "completed_at": now,
            },
            MANUAL_EXTERNAL_PLAN_REVIEW: None,
        }
        work_item["phase"] = "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW"
    elif verdict == "REVISE":
        work_item["phase"] = "REVISING_PLAN"
    else:  # BLOCK
        return state

    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    _validate_plan_review_stages(work_item)
    return new_state


def validate_manual_plan_review_preconditions(
    work_item: dict, *, current_review_content_id: str, feedback_role: str,
    feedback_review_content_id: str,
) -> None:
    """`/record-manual-plan-review`'s resolution/phase/role/staleness/
    invariant preconditions (D-Plan-Review-Stages transition table,
    resolves `GPT-R12-002`/`-003`), checked before writing anything:

    - `"2.1"`-governed and currently at `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`.
    - the feedback's declared role is either the canonical
      `MANUAL_EXTERNAL_PLAN_REVIEW` or the legacy `manual_external_plan_review`
      (rejects a local-role, unlabeled, or any other feedback file).
    - the feedback's `review_content_id` matches the current recomputed
      value -- **hard**, blocks ingestion (distinct from the advisory-only
      `bundle_id` check, `check_manual_stage_bundle_id_advisory`, never
      performed here).
    - a current `LOCAL_MODEL_PLAN_REVIEW` `APPROVE` is recorded for the
      same `review_content_id` (restated invariant -- entry to this phase
      already required it; defends against a corrupted/hand-edited state).
    - no `MANUAL_EXTERNAL_PLAN_REVIEW` stage is already recorded against
      the current `review_content_id` (rejects duplicate ingestion).
    """
    _require_v2_1_plan_review(work_item)
    if work_item.get("phase") != "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW":
        raise WrongPhaseForPlanReviewStageError(
            f"{work_item['work_item_id']}: phase is {work_item.get('phase')!r}, "
            f"not \"AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW\""
        )
    if _normalize_plan_review_stage_key(feedback_role) != MANUAL_EXTERNAL_PLAN_REVIEW:
        raise WrongReviewerRoleError(
            f"REVIEW_FEEDBACK.md declares Reviewer role: {feedback_role!r}, "
            f"expected \"{MANUAL_EXTERNAL_PLAN_REVIEW}\" (or legacy "
            f"\"manual_external_plan_review\")"
        )
    if feedback_review_content_id != current_review_content_id:
        raise StaleReviewContentIdError(
            f"feedback review_content_id {feedback_review_content_id!r} does not "
            f"match the current recomputed value {current_review_content_id!r} -- "
            f"this is a hard block, unlike the manual stage's advisory bundle_id check"
        )
    stages = normalize_plan_review_stages(work_item.get("plan_review_stages") or {})
    local = stages.get(LOCAL_MODEL_PLAN_REVIEW)
    if (
        stages.get("review_content_id") != current_review_content_id
        or local is None or local.get("verdict") != "APPROVE"
    ):
        raise MissingLocalApprovalForManualStageError(
            f"{work_item['work_item_id']}: no current LOCAL_MODEL_PLAN_REVIEW "
            f"APPROVE recorded for review_content_id {current_review_content_id!r}"
        )
    if stages.get(MANUAL_EXTERNAL_PLAN_REVIEW) is not None:
        raise DuplicateManualStageIngestionError(
            f"{work_item['work_item_id']}: MANUAL_EXTERNAL_PLAN_REVIEW is already "
            f"recorded against review_content_id {current_review_content_id!r}"
        )


def check_manual_stage_bundle_id_advisory(
    feedback_bundle_id: str, current_bundle_id: str,
) -> str | None:
    """The manual stage's `bundle_id` check: advisory only, never blocking
    (resolves `OPUS-R14-005`, corrects the plan-stage-equality symmetry a
    naive reading of `/approve-review`'s own hard `bundle_id` check might
    suggest) -- a wrapper-only bundle regeneration between upload and
    paste (new `bundle_id`, unchanged `review_content_id`) must not
    invalidate the manual stage (`GPT-R11-006`). Returns a warning string
    naming both values on a mismatch, or `None` when they match."""
    if feedback_bundle_id != current_bundle_id:
        return (
            f"bundle_id mismatch (advisory only, does not block ingestion): "
            f"feedback bundle_id={feedback_bundle_id!r}, current recomputed "
            f"bundle_id={current_bundle_id!r}"
        )
    return None


def record_manual_plan_review(
    state: dict, work_item_id: str, *, verdict: str, bundle_id: str, round: int,
    now: str, current_review_content_id: str, feedback_role: str,
    feedback_review_content_id: str,
) -> dict:
    """`/record-manual-plan-review`'s sole state write set (D-Plan-Review-
    Stages transition table, resolves `GPT-R12-002`/`-003`):

    - `APPROVE`: records the completed `MANUAL_EXTERNAL_PLAN_REVIEW` stage
      -- including the feedback's own `bundle_id` **verbatim**, regardless
      of whether it matches the current recomputed one, so the ledger
      records what the reviewer actually saw (`OPUS-R14-005`, missing-test
      item 113) -- and transitions to `AWAITING_PLAN_APPROVAL`.
    - `REVISE`: no ledger write; transitions to `REVISING_PLAN`.
    - `BLOCK`: no ledger write, no phase transition -- a true no-op
      (missing-test item 99); the returned state is unchanged.
    """
    if verdict not in PLAN_REVIEW_VERDICTS:
        raise UnknownPlanReviewVerdictError(f"unknown plan-review verdict: {verdict!r}")
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    validate_manual_plan_review_preconditions(
        work_item, current_review_content_id=current_review_content_id,
        feedback_role=feedback_role, feedback_review_content_id=feedback_review_content_id,
    )

    if verdict == "APPROVE":
        work_item["plan_review_stages"][MANUAL_EXTERNAL_PLAN_REVIEW] = {
            "bundle_id": bundle_id, "verdict": "APPROVE",
            "round": round, "completed_at": now,
        }
        work_item["phase"] = "AWAITING_PLAN_APPROVAL"
    elif verdict == "REVISE":
        work_item["phase"] = "REVISING_PLAN"
    else:  # BLOCK
        return state

    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    _validate_plan_review_stages(work_item)
    return new_state


def transition_to_awaiting_local_plan_review(state: dict, work_item_id: str, now: str) -> dict:
    """`/apply-plan-review`'s revised exit step for a `TWO_STAGE_PLAN_
    REVIEW_VERSIONS`-governed (`"2.1"`/`"2.2"`) item alike (D-Plan-Review-
    Stages, resolves `GPT-R11-003`/`-007`; corrected workflow-2.5.0 from
    describing this as `"2.1"`-only -- the exit step is identical for
    `"2.2"`, D-Implementation-Review-Version-Activation's inheritance
    rule): after every accepted plan edit, the work item transitions to
    `AWAITING_LOCAL_PLAN_REVIEW` -- never
    self-declaring plan readiness. The stale `plan_review_stages` ledger
    (if any) is left as-is, never explicitly cleared: its own
    `review_content_id` no longer matches the freshly recomputed one the
    moment the edit lands, so both stages already read as absent by the
    recomputation rule (`plan_approval_gate_reachable`). This is the sole
    path back to `AWAITING_LOCAL_PLAN_REVIEW`, whether the edit was driven
    by a local-model `REVISE` or a manual-external `REVISE` (missing-test
    item 89) -- no path re-enters manual-external review without a fresh
    local pass first."""
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    work_item["phase"] = "AWAITING_LOCAL_PLAN_REVIEW"
    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    return new_state


# ---------------------------------------------------------------------------
# workflow-2.5.0 CP3: D-Implementation-Review-Stages -- two-stage
# local-then-manual-external *implementation*-review protocol's ledger
# writers and gate widening, mirroring WF4a-iv's plan-review-stage section
# above function-for-function, substituted for the implementation stage
# (`"2.2"`-only -- see `WrongGoverningVersionForImplementationReviewStageError`
# for why this protocol never applies to `"1"`/`"2.1"`, unlike the
# plan-review protocol, which is `TWO_STAGE_PLAN_REVIEW_VERSIONS`-governed
# for both). No separate `transition_to_awaiting_local_implementation_review`
# writer exists: `record_bundle_generation`'s own version-dependent
# `bundle_generation_target_phase` resolver (further below in this module)
# is the sole writer for both entries into `AWAITING_LOCAL_IMPLEMENTATION_
# REVIEW`, first-round and post-fix alike.
# ---------------------------------------------------------------------------


def _require_implementation_review_stage_version(work_item: dict) -> None:
    """The implementation-stage counterpart of `_require_v2_1_plan_review`:
    unlike that check (membership in `TWO_STAGE_PLAN_REVIEW_VERSIONS`, both
    `"2.1"`/`"2.2"`), this ledger's two-stage protocol is `"2.2"`-only, so
    this is a single-valued equality, never a membership test."""
    if work_item.get("governing_workflow_version") != "2.2":
        raise WrongGoverningVersionForImplementationReviewStageError(
            f"{work_item['work_item_id']}: governing_workflow_version is "
            f"{work_item.get('governing_workflow_version')!r}, not \"2.2\" -- the "
            f"two-stage implementation-review protocol applies only to \"2.2\" "
            f"work items"
        )


def validate_local_implementation_review_preconditions(work_item: dict) -> None:
    """`/review-implementation`'s resolution/phase preconditions, in its
    `"2.2"` authoritative role (D-Implementation-Review-Stages): the item
    must be `"2.2"`-governed and currently at
    `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`. Bundle/manifest staleness is a
    separate, generic check (D-Bundle-Manifest, reused unchanged) run by
    the command itself before this, not duplicated here -- the exact
    discipline `validate_local_plan_review_preconditions` already
    follows."""
    _require_implementation_review_stage_version(work_item)
    if work_item.get("phase") != "AWAITING_LOCAL_IMPLEMENTATION_REVIEW":
        raise WrongPhaseForImplementationReviewStageError(
            f"{work_item['work_item_id']}: phase is {work_item.get('phase')!r}, "
            f"not \"AWAITING_LOCAL_IMPLEMENTATION_REVIEW\" -- /review-implementation "
            f"refuses rather than silently re-running (e.g. already completed this "
            f"round)"
        )


def record_local_implementation_review(
    state: dict, work_item_id: str, *, verdict: str, bundle_id: str,
    review_content_id: str, round: int, now: str,
) -> dict:
    """`/review-implementation`'s sole state write set in its `"2.2"`
    authoritative role (D-Implementation-Review-Stages transition table),
    mirroring `record_local_plan_review` exactly, substituted for the
    implementation stage:

    - `APPROVE`: records the completed `LOCAL_MODEL_IMPLEMENTATION_REVIEW`
      stage against `review_content_id` (starting a fresh ledger scoped to
      this content id) and transitions to
      `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`.
    - `REVISE`: no ledger write; transitions directly to
      `APPLYING_REVIEW_FEEDBACK` -- unlike the plan side's `REVISING_PLAN`,
      since this writer already sets that phase directly.
      `/apply-implementation-review`'s own step 0 then finds `phase`
      already `APPLYING_REVIEW_FEEDBACK` and skips its own
      `enter_applying_review_feedback` call under that command's
      version-independent phase-conditional guard -- not because of a
      `"1"`/`"2.1"` vs `"2.2"` branch; there is no version branch at step 0
      any more. Can never reach
      `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`.
    - `BLOCK`: no ledger write, no phase transition -- a true no-op; the
      returned state is unchanged.
    """
    if verdict not in IMPLEMENTATION_REVIEW_VERDICTS:
        raise UnknownImplementationReviewVerdictError(
            f"unknown implementation-review verdict: {verdict!r}"
        )
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    validate_local_implementation_review_preconditions(work_item)

    if verdict == "APPROVE":
        work_item["implementation_review_stages"] = {
            "review_content_id": review_content_id,
            LOCAL_MODEL_IMPLEMENTATION_REVIEW: {
                "bundle_id": bundle_id, "verdict": "APPROVE",
                "round": round, "completed_at": now,
            },
            MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW: None,
        }
        work_item["phase"] = "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW"
    elif verdict == "REVISE":
        work_item["phase"] = "APPLYING_REVIEW_FEEDBACK"
    else:  # BLOCK
        return state

    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    _validate_implementation_review_stages(work_item)
    return new_state


def validate_manual_implementation_review_preconditions(
    work_item: dict, *, current_review_content_id: str, feedback_role: str,
    feedback_review_content_id: str,
) -> None:
    """`/record-manual-implementation-review`'s resolution/phase/role/
    staleness/invariant preconditions (D-Implementation-Review-Stages
    transition table), checked before writing anything -- mirroring
    `validate_manual_plan_review_preconditions` exactly, substituted for
    the implementation stage:

    - `"2.2"`-governed and currently at
      `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`.
    - the feedback's declared role is exactly the canonical
      `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` (rejects a local-role,
      unlabeled, or any other feedback file) -- reuses
      `WrongReviewerRoleError`, already stage-agnostic.
    - the feedback's `review_content_id` matches the current recomputed
      value -- **hard**, blocks ingestion (reuses `StaleReviewContentIdError`,
      already stage-agnostic; distinct from the advisory-only `bundle_id`
      check, `check_manual_stage_bundle_id_advisory`, also reused verbatim
      and never performed here).
    - a current `LOCAL_MODEL_IMPLEMENTATION_REVIEW` `APPROVE` is recorded
      for the same `review_content_id` (restated invariant).
    - no `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` stage is already recorded
      against the current `review_content_id` (rejects duplicate
      ingestion).
    """
    _require_implementation_review_stage_version(work_item)
    if work_item.get("phase") != "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW":
        raise WrongPhaseForImplementationReviewStageError(
            f"{work_item['work_item_id']}: phase is {work_item.get('phase')!r}, "
            f"not \"AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW\""
        )
    if feedback_role != MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW:
        raise WrongReviewerRoleError(
            f"REVIEW_FEEDBACK.md declares Reviewer role: {feedback_role!r}, "
            f"expected \"{MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW}\""
        )
    if feedback_review_content_id != current_review_content_id:
        raise StaleReviewContentIdError(
            f"feedback review_content_id {feedback_review_content_id!r} does not "
            f"match the current recomputed value {current_review_content_id!r} -- "
            f"this is a hard block, unlike the manual stage's advisory bundle_id check"
        )
    stages = normalize_implementation_review_stages(work_item.get("implementation_review_stages") or {})
    local = stages.get(LOCAL_MODEL_IMPLEMENTATION_REVIEW)
    if (
        stages.get("review_content_id") != current_review_content_id
        or local is None or local.get("verdict") != "APPROVE"
    ):
        raise MissingLocalApprovalForManualImplementationStageError(
            f"{work_item['work_item_id']}: no current LOCAL_MODEL_IMPLEMENTATION_REVIEW "
            f"APPROVE recorded for review_content_id {current_review_content_id!r}"
        )
    if stages.get(MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW) is not None:
        raise DuplicateManualImplementationStageIngestionError(
            f"{work_item['work_item_id']}: MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW is "
            f"already recorded against review_content_id {current_review_content_id!r}"
        )


def record_manual_implementation_review(
    state: dict, work_item_id: str, *, verdict: str, bundle_id: str, round: int,
    now: str, current_review_content_id: str, feedback_role: str,
    feedback_review_content_id: str,
) -> dict:
    """`/record-manual-implementation-review`'s sole state write set
    (D-Implementation-Review-Stages transition table), mirroring
    `record_manual_plan_review` exactly, substituted for the
    implementation stage:

    - `APPROVE`: records the completed `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
      stage -- including the feedback's own `bundle_id` **verbatim**,
      regardless of whether it matches the current recomputed one, so the
      ledger records what the reviewer actually saw -- and transitions to
      `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, the terminal "ready for
      approval" phase (reused, not a fresh name -- see
      `D-Implementation-Review-Version-Activation`'s "terminal-phase
      naming" decision). Unlike the plan side's own manual-`APPROVE` exit
      (`AWAITING_PLAN_APPROVAL`, a distinct gate phase from the ledger
      itself), the implementation side has no separate pre-existing
      terminal-phase name to promote (the disposition record's own
      divergence 2), so it reuses the phase name that already existed.
    - `REVISE`: no ledger write; transitions directly to
      `APPLYING_REVIEW_FEEDBACK`.
    - `BLOCK`: no ledger write, no phase transition -- a true no-op; the
      returned state is unchanged.
    """
    if verdict not in IMPLEMENTATION_REVIEW_VERDICTS:
        raise UnknownImplementationReviewVerdictError(
            f"unknown implementation-review verdict: {verdict!r}"
        )
    new_state = copy.deepcopy(state)
    work_item = new_state["work_items"][work_item_id]
    validate_manual_implementation_review_preconditions(
        work_item, current_review_content_id=current_review_content_id,
        feedback_role=feedback_role, feedback_review_content_id=feedback_review_content_id,
    )

    if verdict == "APPROVE":
        work_item["implementation_review_stages"][MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW] = {
            "bundle_id": bundle_id, "verdict": "APPROVE",
            "round": round, "completed_at": now,
        }
        work_item["phase"] = "AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW"
    elif verdict == "REVISE":
        work_item["phase"] = "APPLYING_REVIEW_FEEDBACK"
    else:  # BLOCK
        return state

    work_item["state_revision"] = work_item.get("state_revision", 1) + 1
    work_item["last_transition"] = now
    _validate_implementation_review_stages(work_item)
    return new_state


# ---------------------------------------------------------------------------
# WF-M8a: D-Legacy phase 1 -- Milestone 8 legacy import (dormant
# LEGACY_READY, resolves GPT-R9-005/OPUS-R6-006/OPUS-R6-009/OPUS-R10-011).
# Phase 2 (the adoption transition on /prepare-functional-review) is
# WF-M8b's own scope, not built here.
# ---------------------------------------------------------------------------


def verify_legacy_branch_reconciliation(
    repo_root: Path, *, reviewed_content_commit: str,
    required_active_milestone_substring: str, head: str = "HEAD",
    active_milestone_path: str = "docs/ACTIVE_MILESTONE.md",
) -> None:
    """D-Legacy's branch-reconciliation precondition, in full: (1) the
    user integrates the legacy branch into `main` themselves -- this
    function only ever verifies that already happened, it never performs
    the integration; (2) the reviewed legacy commit must be a real,
    reachable ancestor of `head`; (3) `active_milestone_path`'s content at
    `head` must contain the caller-supplied substring naming the expected
    integrated/accepted narrative -- a substring check, not exact-content
    equality, since the branch's own final doc state (e.g. a later
    acceptance/waiver update) can legitimately land in a commit after
    `reviewed_content_commit` itself. Raises `LegacyReconciliationError`,
    naming both values, on either failure -- never proceeds past a failed
    check. Re-checked at both import (WF-M8a) time and adoption (WF-M8b)
    time, never trusted from one to the other (a dormant item can sit for
    a long time between the two)."""
    if not _is_ancestor(repo_root, reviewed_content_commit, head):
        raise LegacyReconciliationError(
            f"reviewed_content_commit {reviewed_content_commit!r} is not a reachable "
            f"ancestor of {head!r} -- integrate the branch into main before "
            f"importing/adopting this legacy work item"
        )
    try:
        actual = _run(["git", "show", f"{head}:{active_milestone_path}"], repo_root)
    except subprocess.CalledProcessError as exc:
        raise LegacyReconciliationError(
            f"{active_milestone_path!r} is not readable at {head!r}: {exc}"
        ) from exc
    if required_active_milestone_substring not in actual:
        raise LegacyReconciliationError(
            f"{active_milestone_path!r} at {head!r} does not contain the expected "
            f"substring {required_active_milestone_substring!r}"
        )


def import_legacy_work_item(
    state: dict, *, work_item_id: str, plan_path: str, registry_path: str | None,
    base_commit: str, reviewed_content_commit: str, approved_review_content_id: str,
    legacy_evidence: object, user_confirmation: str, now: str,
) -> dict:
    """D-Legacy phase 1 (WF-M8a): creates a dormant `LEGACY_READY` entry
    (D3: non-terminal, addressable by id, not `active_work_item_id`) for a
    product milestone fully built and reviewed entirely under Workflow v1.
    `governing_workflow_version` is fixed to `"1"` (factually correct, and
    makes the later adoption transition an ordinary, auditable version
    change rather than a first-time assignment, resolving `OPUS-R10-011`).
    `active_work_item_id` is deliberately left untouched -- import is not
    activation, D-Legacy phase 2 (`WF-M8b`) owns that transition.

    Refuses a second import of the same id outright
    (`LegacyImportAlreadyExistsError`): unlike `route_work_item`'s
    ordinary create-or-resume semantics, importing an already-imported id
    is never a resume.

    The caller is responsible for having already run
    `verify_legacy_branch_reconciliation` and for computing
    `approved_review_content_id` via the implementation-stage projection
    (`approval_review_content_id(..., stage="implementation", ...)`,
    scoped to this work item's own `base_commit`..`reviewed_content_commit`
    and its own artifact-declarations file) -- this function only shapes
    and writes the resulting state, exactly like `apply_technical_approval`
    does for an ordinary approval."""
    validate_work_item_id(work_item_id)
    new_state = copy.deepcopy(state)
    work_items = new_state.setdefault("work_items", {})
    if work_item_id in work_items:
        raise LegacyImportAlreadyExistsError(
            f"{work_item_id!r} already exists -- legacy import is a one-time act, "
            f"never a re-import"
        )
    technical_approval = build_approval_record(
        basis="LEGACY_V1", stage="implementation", user_confirmation=user_confirmation, now=now,
        reviewed_bundle_id=None, approved_review_content_id=approved_review_content_id,
        review_content_manifest=None, reviewed_content_commit=reviewed_content_commit,
        legacy_evidence=legacy_evidence, waived_guarantees=["no_bundle_id", "no_telemetry"],
    )
    work_item = default_work_item(
        work_item_id=work_item_id, work_item_type="product", work_item_kind="product",
        plan_path=plan_path, registry_path=registry_path,
        governing_workflow_version="1", plan_revision=1, last_transition=now,
    )
    work_item["phase"] = "LEGACY_READY"
    work_item["technical_approval"] = technical_approval
    work_item["base_commit"] = base_commit
    work_items[work_item_id] = work_item
    return new_state


# ---------------------------------------------------------------------------
# WF-M8b: D-Legacy phase 2 -- Milestone 8 adoption (dormant LEGACY_READY ->
# active, ordinary v2.1 routing). Resolves OPUS-R10-005/-011, GPT-R9-005.
# ---------------------------------------------------------------------------


def promote_legacy_work_item(
    state: dict, repo_root: Path, *, work_item_id: str,
    required_active_milestone_substring: str, artifacts_path: Path, now: str,
) -> dict:
    """D-Legacy phase 2 (`WF-M8b`): promotes a dormant `LEGACY_READY` entry
    to active, ordinary Workflow v2.1 routing. Called by
    `/prepare-functional-review`'s selector step only when the resolved
    target's `phase` is already `LEGACY_READY` -- adoption never runs
    implicitly against any other item or phase (`LegacyAdoptionWrongPhaseError`
    guards misuse defensively, mirroring `import_legacy_work_item`'s own
    `LegacyImportAlreadyExistsError` guard).

    Re-validates, never trusts from import time:
    1. the branch-reconciliation precondition (`verify_legacy_branch_
       reconciliation`) -- a dormant item can sit for a long time between
       import and adoption;
    2. `technical_approval` freshness, via `any_protected_path_changed_
       since(reviewed_content_commit, "HEAD", ...)` scoped to
       `work_item_id`'s own `artifacts_path` -- deliberately not
       `approval_is_current`'s exact-hash-reproduction, which would
       permanently misfire here (see that function's own docstring): a
       dormant legacy entry's declarations file legitimately needs
       widening, after import, to classify paths a concurrent work item
       introduces while it waits, and that widening alone must never read
       as staleness. A real `protected` path change since
       `reviewed_content_commit` raises `LegacyAdoptionStaleApprovalError`
       and stops outright: adoption never re-imports, a stale legacy
       approval is handled exactly like a stale ordinary one, through the
       normal `technical_approval` lifecycle (D-Approval-Commits).

    On success: `active_work_item_id` is set to `work_item_id`,
    `governing_workflow_version` transitions `"1"` -> `"2.1"` (an
    ordinary, auditable version transition -- resolves `OPUS-R10-011`), and
    `phase` transitions to `AWAITING_FUNCTIONAL_REVIEW` -- `technical_approval`
    itself is preserved exactly as imported (`basis: LEGACY_V1`, untouched);
    adoption changes routing, never the approval record.

    **The `"2.1"` destination is a deliberate literal, corrected
    workflow-2.5.0 (resolves `LOCAL_MODEL_PLAN_REVIEW` round 6, optional
    finding 1): never `config["default_workflow_version"]`.** A legacy
    item adopted here is, by construction, already past both
    implementation-review stages `D-Implementation-Review-Stages` adds at
    `"2.2"` -- it was built and reviewed entirely under Workflow v1, then
    imported with a `LEGACY_V1`-basis `technical_approval` already in hand
    (`import_legacy_work_item`), so there is no implementation-review round
    left for it to run through, whatever the repository's own current
    default version is. Promoting it to the current default instead --
    even after this repository has separately activated `"2.2"`
    (`workflow_state_activate`/`WF-Activate`) -- would retroactively assign
    it a two-stage implementation-review obligation it can never satisfy,
    since it has no future implementation round left in which to satisfy
    it. `"2.1"` is therefore always correct: it is the version at which
    `D-Legacy` phase 2 first became possible, still ahead of
    `D-Implementation-Review-Stages`' own additional scope, and it names
    that fact directly rather than deferring to whatever configuration
    happens to be active at adoption time.

    **`artifacts_path` has no default** (salvage audit `I6`): it used to
    default to `fingerprint.DEFAULT_ARTIFACTS_PATH`, `workflow-v2-1-core`'s
    own declarations file, even though `/prepare-functional-review` step
    0a's own text says to pass "this item's own artifact-declarations
    file, not `workflow-v2-1-core`'s". A caller that omitted it evaluated
    check 2 against a *process* item's classification, in which `app/` is
    `excluded` -- so a legacy *product* item whose product code genuinely
    changed since `reviewed_content_commit` was promoted with a `CURRENT`
    legacy approval instead of being refused. Reproduced by execution; the
    argument is now required, so omitting it is a loud `TypeError` rather
    than a silent wrong answer -- the `GPT-R30-005` treatment."""
    work_item = state["work_items"][work_item_id]
    if work_item.get("phase") != "LEGACY_READY":
        raise LegacyAdoptionWrongPhaseError(
            f"{work_item_id!r} is not LEGACY_READY (phase={work_item.get('phase')!r}) -- "
            f"adoption only promotes a dormant legacy entry"
        )
    technical_approval = work_item["technical_approval"]
    verify_legacy_branch_reconciliation(
        repo_root, reviewed_content_commit=technical_approval["reviewed_content_commit"],
        required_active_milestone_substring=required_active_milestone_substring,
    )
    protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
        fingerprint.load_implementation_stage_classification(repo_root, artifacts_path)
    )
    if any_protected_path_changed_since(
        repo_root, technical_approval["reviewed_content_commit"], "HEAD",
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
    ):
        raise LegacyAdoptionStaleApprovalError(
            f"{work_item_id!r}'s legacy technical_approval is no longer current -- a "
            f"protected implementation-stage path changed since {technical_approval['reviewed_content_commit']!r}; "
            f"adoption requires a fresh implementation-review round and /approve-review "
            f"implementation, never a re-import"
        )
    new_state = copy.deepcopy(state)
    new_state["active_work_item_id"] = work_item_id
    new_work_item = new_state["work_items"][work_item_id]
    new_work_item["governing_workflow_version"] = "2.1"
    new_work_item["phase"] = "AWAITING_FUNCTIONAL_REVIEW"
    new_work_item["state_revision"] = new_work_item.get("state_revision", 1) + 1
    new_work_item["last_transition"] = now
    return new_state


# ---------------------------------------------------------------------------
# WORKFLOW_STATE.json
# ---------------------------------------------------------------------------


def _validate_plan_review_stages(work_item: dict) -> None:
    stages = work_item.get("plan_review_stages")
    if stages is None:
        return
    if work_item.get("governing_workflow_version") not in TWO_STAGE_PLAN_REVIEW_VERSIONS:
        raise PlanReviewStagesInvalidForVersionError(
            f"{work_item['work_item_id']}: plan_review_stages is non-null but "
            f"governing_workflow_version is {work_item.get('governing_workflow_version')!r}, "
            f"not one of {sorted(TWO_STAGE_PLAN_REVIEW_VERSIONS)}"
        )
    stages = normalize_plan_review_stages(stages)
    local = stages.get(LOCAL_MODEL_PLAN_REVIEW)
    manual = stages.get(MANUAL_EXTERNAL_PLAN_REVIEW)
    if manual is not None and local is None:
        raise ManualStageWithoutLocalStageError(
            f"{work_item['work_item_id']}: {MANUAL_EXTERNAL_PLAN_REVIEW} is recorded "
            f"while {LOCAL_MODEL_PLAN_REVIEW} is absent"
        )
    for stage_name, stage in ((LOCAL_MODEL_PLAN_REVIEW, local), (MANUAL_EXTERNAL_PLAN_REVIEW, manual)):
        if stage is not None and stage.get("verdict") != "APPROVE":
            raise StageVerdictNotApproveError(
                f"{work_item['work_item_id']}.{stage_name}.verdict is "
                f"{stage.get('verdict')!r}, expected \"APPROVE\" -- only a completed "
                f"APPROVE is ever recorded at either stage (GPT-R14-010)"
            )


def _validate_implementation_review_stages(work_item: dict) -> None:
    """workflow-2.5.0 CP2: the `implementation_review_stages` ledger's own
    shape check, mirroring `_validate_plan_review_stages` exactly except
    for its version domain -- this ledger is `"2.2"`-only, never valid for
    `"2.1"` (unlike `plan_review_stages`, valid for both). CP3's own
    writers (`record_local_implementation_review`/
    `record_manual_implementation_review`) are this ledger's sole write
    path; this validator exists ahead of them, exercised today only by a
    directly-constructed test fixture, so a shape defect introduced by
    CP3's writers is caught here from the moment they land, rather than
    only once a test happens to cover it."""
    stages = work_item.get("implementation_review_stages")
    if stages is None:
        return
    if work_item.get("governing_workflow_version") != "2.2":
        raise ImplementationReviewStagesInvalidForVersionError(
            f"{work_item['work_item_id']}: implementation_review_stages is non-null "
            f"but governing_workflow_version is "
            f"{work_item.get('governing_workflow_version')!r}, not \"2.2\""
        )
    stages = normalize_implementation_review_stages(stages)
    local = stages.get(LOCAL_MODEL_IMPLEMENTATION_REVIEW)
    manual = stages.get(MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW)
    if manual is not None and local is None:
        raise ManualImplementationStageWithoutLocalStageError(
            f"{work_item['work_item_id']}: {MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW} is "
            f"recorded while {LOCAL_MODEL_IMPLEMENTATION_REVIEW} is absent"
        )
    for stage_name, stage in (
        (LOCAL_MODEL_IMPLEMENTATION_REVIEW, local), (MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW, manual),
    ):
        if stage is not None and stage.get("verdict") != "APPROVE":
            raise StageVerdictNotApproveError(
                f"{work_item['work_item_id']}.{stage_name}.verdict is "
                f"{stage.get('verdict')!r}, expected \"APPROVE\" -- only a completed "
                f"APPROVE is ever recorded at either stage"
            )


def _validate_technical_review_block_pins(work_item: dict) -> None:
    """D2a's shape check: `technical_review_block_pins`, if present, is a
    list of `{bundle_id, review_content_id, recorded_at}` objects with no
    extra/missing keys and no duplicate `bundle_id` (pins are keyed by
    `bundle_id` alone, permanently -- `record_technical_review_block_pin`
    is idempotent by construction and never produces a duplicate itself)."""
    pins = work_item.get("technical_review_block_pins")
    if pins is None:
        return
    if not isinstance(pins, list):
        raise CorruptJsonError(
            f"work_items[{work_item['work_item_id']!r}].technical_review_block_pins must be a list"
        )
    seen: set[str] = set()
    for pin in pins:
        if not isinstance(pin, dict) or set(pin) != {"bundle_id", "review_content_id", "recorded_at"}:
            raise CorruptJsonError(
                f"work_items[{work_item['work_item_id']!r}].technical_review_block_pins entry "
                f"has unexpected shape: {pin!r}"
            )
        bundle_id = pin["bundle_id"]
        if bundle_id in seen:
            raise DuplicateTechnicalReviewBlockPinError(
                f"work_items[{work_item['work_item_id']!r}].technical_review_block_pins has "
                f"more than one entry for bundle_id {bundle_id!r} -- pins are keyed by "
                f"bundle_id alone"
            )
        seen.add(bundle_id)


def _validate_work_item(work_item_id: str, work_item: dict) -> None:
    if work_item.get("work_item_id") != work_item_id:
        raise WorkItemIdKeyMismatchError(
            f"work_items[{work_item_id!r}].work_item_id == {work_item.get('work_item_id')!r}"
        )
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item["work_item_type"])
    kind = work_item.get("work_item_kind")
    if kind is not None:
        validate_work_item_kind(kind)
    phase = work_item.get("phase")
    if phase not in KNOWN_PHASES:
        raise UnknownPhaseError(f"work_items[{work_item_id!r}].phase == {phase!r}")

    in_progress = []
    for checkpoint_id, entry in work_item.get("checkpoints", {}).items():
        status = entry.get("status")
        if status not in CHECKPOINT_STATUSES:
            raise UnknownCheckpointStatusError(f"checkpoints[{checkpoint_id!r}].status == {status!r}")
        if status == "IN_PROGRESS":
            in_progress.append(checkpoint_id)
    if len(in_progress) > 1:
        raise MultipleInProgressCheckpointsError(
            f"work_items[{work_item_id!r}] has multiple IN_PROGRESS checkpoints: {in_progress}"
        )

    _validate_plan_review_stages(work_item)
    _validate_implementation_review_stages(work_item)
    _validate_technical_review_block_pins(work_item)

    # I2 (workflow-v2-3-followups continued scope, external cross-model
    # review rounds 2 and 4): validate_approval_record's shape check
    # protected only newly *constructed* records, never one already
    # persisted in `WORKFLOW_STATE.json` -- a malformed plan_approval/
    # technical_approval from an old backup, import, hand edit, or
    # pre-fix tooling could still reach a downstream consumer (e.g.
    # _assert_registry_covered_by_current_plan_approval) and reproduce
    # the original bare AttributeError this milestone's own first defect
    # closed for the write path only. Every non-null approval record on
    # every work item is shape-checked here too, so validate_state
    # itself rejects a malformed persisted record cleanly -- **but this
    # is the write-time/harness backstop, not what actually protects a
    # production consumer** (round 4's own correction: validate_state
    # has no production caller anywhere in this repository). The real
    # runtime protection lives at the two consumer chokepoints
    # themselves, guarded directly and independently of this function:
    # _assert_registry_covered_by_current_plan_approval (live-state) and
    # _resolve_one_obligation (committed-blob, historical state).
    plan_approval = work_item.get("plan_approval")
    if plan_approval is not None:
        validate_approval_record(plan_approval, stage="plan")
    technical_approval = work_item.get("technical_approval")
    if technical_approval is not None:
        validate_approval_record(technical_approval, stage="implementation")


def validate_state(state: dict, *, registry: dict | None = None, repo_root: Path | None = None) -> None:
    """D3's "Validator rejects" list, to the extent checkable from schema
    and (optionally) the registry alone. Corrupt/unparseable JSON is the
    caller's concern (`_load_json`/`CorruptJsonError`) -- this function
    receives already-parsed data.

    **This function has no production caller anywhere in this repository**
    (external cross-model review, `workflow-v2-3-followups` continued
    scope, round 4): it is a write-time/harness backstop only -- test
    suites and the test harness call it, no `.claude/commands/*.md` file,
    shell script, or other production function does, and `state_transaction`
    (the documented single required entry point for every production
    writer) does not call it either. Do not infer that a check added here
    protects any real read path; a check that must protect production
    consumption belongs at that consumer's own chokepoint too
    (`OPUS-R25-007`'s own precedent -- see e.g.
    `_assert_registry_covered_by_current_plan_approval`/
    `_resolve_one_obligation`'s own `review_content_manifest` shape
    guards, which this function's own equivalent check does not reach).

    `registry`, if given, is a single caller-supplied registry dict used
    for the checkpoint-dependency check below and, if its own
    `work_item_id` names a known item, that one item's plan-revision
    mirror -- unchanged from before `GPT-R31-003`, kept for callers that
    only ever validate one work item's checkpoints against its own
    registry.

    `repo_root`, if given, is the **whole-state** plan-revision-mirror
    check `GPT-R31-003` requires: every work item with a non-null
    `registry_path` has that path resolved and read from disk (relative to
    `repo_root`), independent of whichever single `registry` a caller
    happened to also pass -- so a caller validating `workflow-v2-1-core`'s
    own registry can no longer leave a different work item's stale mirror
    undetected. `registry_path` is resolved through
    `fingerprint._validate_plan_stage_metadata_path`, the same *tracked*
    metadata-path validator plan-stage resolution uses (`GPT-R32-001`,
    tightened by `GPT-R33-002`) -- an absolute path, a `../` traversal, a
    symlink at any path component, a path that does not exist as a
    regular file, or a path that exists but is not a Git-tracked file all
    fail closed as `MissingRegistryForPlanRevisionMirrorCheckError` rather
    than silently reading whatever `repo_root / registry_path` happens to
    join to (an untracked file would otherwise become the authoritative
    comparison source for a mirror check meant to police tracked,
    committed metadata). Malformed JSON or a JSON value that is not an
    object fails the same way, by name, instead of a raw
    `JSONDecodeError`/`AttributeError` (`GPT-R33-004`). The loaded
    registry must also declare the exact same `work_item_id` as the
    state-map key that named it (`RegistryWorkItemIdMismatchError`,
    `GPT-R32-003`) and a valid integer `plan_revision >= 1`
    (`InvalidRegistryPlanRevisionError`, `GPT-R32-002`) -- neither
    cross-wired registry ownership nor a malformed revision are silently
    treated as "nothing to check". A `null` `registry_path` remains
    exempt, by design, from any mirror check at all."""
    if state.get("schema_version") != SCHEMA_VERSION:
        raise CorruptJsonError(f"unknown state schema_version: {state.get('schema_version')!r}")

    work_items = state.get("work_items", {})
    for work_item_id, work_item in work_items.items():
        _validate_work_item(work_item_id, work_item)
        parent_id = work_item.get("parent_work_item_id")
        if parent_id is not None and parent_id not in work_items:
            raise DanglingParentWorkItemError(
                f"work_items[{work_item_id!r}].parent_work_item_id names "
                f"{parent_id!r}, which is not a known work item"
            )

    # D-Fingerprint-Generalization (`OPUS-R25-007`): a non-null plan_path/
    # registry_path/mapping_path may not be claimed by more than one work
    # item, checked independently per field, not only as a triple. This is
    # a write-time, whole-map structural belt-and-suspenders check --
    # `resolve_plan_stage_metadata`'s own step 9 is the read path's actual
    # enforcement point, run per lookup; this one catches a hand-edited or
    # half-written state file that never passed through `route_work_item`.
    for field_name in ("plan_path", "registry_path", "mapping_path"):
        seen: dict[str, str] = {}
        for work_item_id, work_item in work_items.items():
            value = work_item.get(field_name)
            if value is None:
                continue
            if value in seen:
                raise fingerprint.DuplicateWorkItemArtifactPathError(
                    f"{field_name} {value!r} is claimed by both {seen[value]!r} and {work_item_id!r}"
                )
            seen[value] = work_item_id

    if repo_root is not None:
        # GPT-R31-003: the single-`registry`-param mirror check above only
        # ever covers the one item that `registry` itself names -- a
        # caller validating with only `workflow-v2-1-core`'s own registry
        # never sees a *different* work item's stale mirror. This is the
        # whole-state counterpart: every non-null `registry_path` in
        # `state` is resolved and read from disk, independent of which
        # single `registry` (if any) the caller also passed.
        for work_item_id, work_item in work_items.items():
            registry_path = work_item.get("registry_path")
            if registry_path is None:
                continue
            # GPT-R32-001/GPT-R33-002: reuse the authoritative *tracked*
            # metadata-path validator (grammar, symlink, existence, git
            # tracking) instead of the filesystem-only safe-path resolver.
            # The filesystem-only helper alone would let an untracked JSON
            # file dropped anywhere in the worktree become the
            # authoritative comparison source for this state mirror --
            # exactly as unusable a "registry" as a missing one, since it
            # is absent from commits, review bundles, fresh sessions, and
            # approval provenance.
            try:
                fingerprint._validate_plan_stage_metadata_path(
                    repo_root, "registry_path", registry_path, at_commit=None,
                )
            except fingerprint.InvalidPlanStageMetadataPathError as exc:
                raise MissingRegistryForPlanRevisionMirrorCheckError(
                    f"work_items[{work_item_id!r}].registry_path {registry_path!r} "
                    f"failed safe-path resolution: {exc}"
                ) from exc
            registry_full = repo_root / registry_path
            try:
                registry_bytes = registry_full.read_text()
            except OSError as exc:
                raise MissingRegistryForPlanRevisionMirrorCheckError(
                    f"work_items[{work_item_id!r}].registry_path {registry_path!r} "
                    f"does not exist or is unreadable at {registry_full}"
                ) from exc
            # GPT-R33-004: malformed JSON or a well-formed value that is
            # not a JSON object must fail through this check's own named,
            # work-item-specific error, not a raw `JSONDecodeError`/
            # `AttributeError` that bypasses the workflow's fail-closed
            # error model.
            try:
                registry_data = json.loads(registry_bytes)
            except json.JSONDecodeError as exc:
                raise MissingRegistryForPlanRevisionMirrorCheckError(
                    f"work_items[{work_item_id!r}].registry_path {registry_path!r} "
                    f"is not valid JSON: {exc}"
                ) from exc
            if not isinstance(registry_data, dict):
                raise MissingRegistryForPlanRevisionMirrorCheckError(
                    f"work_items[{work_item_id!r}].registry_path {registry_path!r} "
                    f"does not contain a JSON object (found {type(registry_data).__name__})"
                )

            # GPT-R32-003: the loaded registry must declare the exact same
            # work_item_id as the state-map key that named it -- otherwise
            # item A could point at item B's registry and pass whenever the
            # two happened to share a revision number.
            registry_work_item_id = registry_data.get("work_item_id")
            if registry_work_item_id != work_item_id:
                raise fingerprint.RegistryWorkItemIdMismatchError(
                    f"work_items[{work_item_id!r}].registry_path {registry_path!r} "
                    f"declares work_item_id {registry_work_item_id!r}, expected {work_item_id!r}"
                )

            # GPT-R32-002: a registry with no valid `plan_revision` cannot
            # satisfy Revision 21's state-mirror invariant -- fail closed
            # rather than silently `continue`-ing past unverified coverage.
            registry_plan_revision = registry_data.get("plan_revision")
            if (
                not isinstance(registry_plan_revision, int)
                or isinstance(registry_plan_revision, bool)
                or registry_plan_revision < 1
            ):
                raise InvalidRegistryPlanRevisionError(
                    f"work_items[{work_item_id!r}].registry_path {registry_path!r} has no "
                    f"valid 'plan_revision' (an integer >= 1): found {registry_plan_revision!r}"
                )
            state_plan_revision = work_item.get("plan_revision")
            if state_plan_revision != registry_plan_revision:
                raise PlanRevisionMirrorMismatchError(
                    f"work_items[{work_item_id!r}].plan_revision (state mirror) == "
                    f"{state_plan_revision!r}, but registry {registry_path!r} declares "
                    f"plan_revision == {registry_plan_revision!r}"
                )

    active_id = state.get("active_work_item_id")
    if active_id is not None:
        active_item = work_items.get(active_id)
        if active_item is None or active_item.get("phase") in TERMINAL_PHASES:
            raise ActiveWorkItemInvalidError(
                f"active_work_item_id {active_id!r} names a nonexistent or terminal-phase entry"
            )

    if registry is not None:
        # D-Fingerprint-Generalization (`OPUS-R25-002`/`-009`, missing-test
        # item 147): `WORKFLOW_STATE.json`'s own `plan_revision` field is a
        # non-authoritative display mirror of the registry's own
        # `plan_revision` (the authoritative value `load_plan_revision`
        # resolves from). A caller-supplied `registry` names the work item
        # it belongs to via its own `work_item_id` field; if that item is
        # present in `state`, the two must agree before any fingerprint
        # call runs, so a stale/hand-edited mirror fails closed here rather
        # than silently letting a later command act on the wrong revision.
        registry_work_item_id = registry.get("work_item_id")
        registry_plan_revision = registry.get("plan_revision")
        if registry_work_item_id is not None and registry_plan_revision is not None:
            mirrored_work_item = work_items.get(registry_work_item_id)
            if mirrored_work_item is not None:
                state_plan_revision = mirrored_work_item.get("plan_revision")
                if state_plan_revision != registry_plan_revision:
                    raise PlanRevisionMirrorMismatchError(
                        f"work_items[{registry_work_item_id!r}].plan_revision "
                        f"(state mirror) == {state_plan_revision!r}, but registry "
                        f"declares plan_revision == {registry_plan_revision!r}"
                    )
        depends_on_by_id = {entry["id"]: entry.get("depends_on", []) for entry in registry["checkpoints"]}
        for work_item in work_items.values():
            checkpoints = work_item.get("checkpoints", {})
            for checkpoint_id, entry in checkpoints.items():
                if entry["status"] != "COMPLETE":
                    continue
                for dependency in depends_on_by_id.get(checkpoint_id, []):
                    dep_status = checkpoints.get(dependency, {}).get("status")
                    if dep_status != "COMPLETE":
                        raise CheckpointDependencyNotCompleteError(
                            f"{work_item['work_item_id']}/{checkpoint_id} is COMPLETE but its "
                            f"dependency {dependency!r} is {dep_status!r}"
                        )


# ---------------------------------------------------------------------------
# .ai-review/runtime/WORKTREE_IDENTITY.json schema (the writer,
# `write_worktree_identity`, and its resume-side check,
# `verify_dirty_resume_safety`, live in WF2's own section above)
# ---------------------------------------------------------------------------


def validate_worktree_identity(data: dict) -> None:
    """Schema check for D3's local-only, gitignored WORKTREE_IDENTITY.json,
    shared by `write_worktree_identity` (which validates before writing)
    and `verify_dirty_resume_safety` (which validates before trusting a
    file it did not just write)."""
    for key in ("repo_root", "git_common_dir", "worktree_root", "generated_at"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise CorruptJsonError(f"WORKTREE_IDENTITY.json missing/invalid {key!r}")
    expected = data.get("expected_dirty_paths_by_work_item")
    if not isinstance(expected, dict):
        raise CorruptJsonError("WORKTREE_IDENTITY.json.expected_dirty_paths_by_work_item must be a dict")
    for work_item_id, entries in expected.items():
        if not isinstance(entries, list):
            raise CorruptJsonError(f"expected_dirty_paths_by_work_item[{work_item_id!r}] must be a list")
        for entry in entries:
            if set(entry) != {"path", "sha256"}:
                raise CorruptJsonError(
                    f"expected_dirty_paths_by_work_item[{work_item_id!r}] entry has unexpected keys: {entry!r}"
                )


# ---------------------------------------------------------------------------
# D-Review-Material-Lifecycle's review-material-lifecycle marker
# (`workflow-2.5.0` CP1 fixes this representation, as one canonical
# render/parse definition, before `D-Review-Material-Lifecycle` itself
# exists as a design section -- CP1's own required disposition-record
# marker must be written in *some* concrete representation, and that
# representation cannot remain a later checkpoint's decision. CP6 imports
# and reuses this identical pair for every marker it writes and for its
# own lint's parsing; it is never re-derived as a second, independent
# grammar. See `docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s
# `D-Implementation-Review-Stages` `2.5.0 disposition record` subsection.)
# ---------------------------------------------------------------------------

REVIEW_MATERIAL_LIFECYCLE_STATES = ("CURRENT", "HISTORICAL")

_REVIEW_MATERIAL_LIFECYCLE_MARKER_RE = re.compile(
    r"<!--\s*review-material-lifecycle:\s*(CURRENT|HISTORICAL)\s*-->"
)


def render_marker(state: str) -> str:
    """Render the canonical `review-material-lifecycle` marker for `state`.

    `state` must be exactly one of `REVIEW_MATERIAL_LIFECYCLE_STATES`
    (`"CURRENT"` or `"HISTORICAL"`). This function's own output bytes are
    the sole specification of the marker's representation -- every marker
    any checkpoint writes (CP1's own disposition-record instance, and
    every marker D-Review-Material-Lifecycle's own marking pass writes)
    is required to equal `render_marker`'s output for its respective
    state, never hand-typed independently of it.
    """
    if state not in REVIEW_MATERIAL_LIFECYCLE_STATES:
        raise ValueError(
            f"render_marker: state must be one of {REVIEW_MATERIAL_LIFECYCLE_STATES}, got {state!r}"
        )
    return f"<!-- review-material-lifecycle: {state} -->"


def parse_marker(text: str) -> str | None:
    """Parse a `render_marker` marker out of `text`, or return `None`.

    Returns `"CURRENT"` or `"HISTORICAL"` for a well-formed marker
    matching `render_marker`'s own output shape exactly; returns `None`
    for absent, malformed, or out-of-contract input (a mutated delimiter,
    a third state value, or truncated/duplicated marker text). Callers
    fail closed on `None` by treating the classification as `CURRENT`
    (D-Review-Material-Lifecycle's fail-closed default), never as an
    error: classification can only narrow what a reviewer sees, never
    silently widen what a reviewer does not see.
    """
    matches = _REVIEW_MATERIAL_LIFECYCLE_MARKER_RE.findall(text)
    if len(matches) != 1:
        return None
    state = matches[0]
    if state not in REVIEW_MATERIAL_LIFECYCLE_STATES:
        return None
    return state


# ---------------------------------------------------------------------------
# D-Review-Material-Lifecycle (`workflow-2.5.0` CP6): unit parsing,
# classification, the marker-presence obligation, the marking pass, the
# narrative-content check, and the governing-version enumeration sweep. All
# of this imports and reuses `render_marker`/`parse_marker` above -- never a
# second, independently-derived grammar. See `docs/ai-workflow/
# WORKFLOW_V2_PLAN.md`'s `D-Review-Material-Lifecycle` design section.
# ---------------------------------------------------------------------------

_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class MarkdownUnit:
    """One Markdown heading-delimited unit: `own_text` is everything
    between this heading's line and the next heading line at *any* level
    (never a descendant unit's own text); `full_text` additionally includes
    every descendant unit, down to the next heading at a level <= this
    unit's own (or end of document). Classification (`unit_state`) is
    decided from `own_text` alone, exactly so a nested unit's own marker
    never gets folded into its container's search."""

    __slots__ = ("level", "heading", "own_text", "full_text", "start_line", "children")

    def __init__(self, level, heading, own_text, full_text, start_line):
        self.level = level
        self.heading = heading
        self.own_text = own_text
        self.full_text = full_text
        self.start_line = start_line
        self.children: list["MarkdownUnit"] = []


def parse_markdown_units(text: str) -> list[MarkdownUnit]:
    """Parse `text` into a flat list of every heading-delimited unit at
    every level, each carrying its own `MarkdownUnit.children` (the units
    whose heading is the next thing encountered at a deeper level, before
    the next heading at <= this unit's level closes it). A document with no
    heading at all yields an empty list -- callers that need "the whole
    document" as a single unit when it has no heading of its own handle
    that case separately (see `document_level_unit`)."""
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []  # (line_index, level, heading_text)
    for i, line in enumerate(lines):
        m = _MARKDOWN_HEADING_RE.match(line.rstrip("\n"))
        if m:
            headings.append((i, len(m.group(1)), m.group(2).strip()))

    units: list[MarkdownUnit] = []
    for idx, (line_i, level, heading) in enumerate(headings):
        # own_text: up to the very next heading of any level (or EOF).
        own_end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        own_text = "".join(lines[line_i + 1:own_end])

        # full_text: up to the next heading at level <= this one (or EOF).
        full_end = len(lines)
        for later_i, later_level, _ in headings[idx + 1:]:
            if later_level <= level:
                full_end = later_i
                break
        full_text = "".join(lines[line_i:full_end])

        units.append(MarkdownUnit(level, heading, own_text, full_text, line_i))

    # Wire up direct children: the nearest following unit at level+? that is
    # not itself enclosed by an intervening same-or-shallower unit. Simple
    # stack-based construction, standard heading-nesting algorithm.
    stack: list[MarkdownUnit] = []
    for unit in units:
        while stack and stack[-1].level >= unit.level:
            stack.pop()
        if stack:
            stack[-1].children.append(unit)
        stack.append(unit)
    return units


def document_level_unit(text: str) -> MarkdownUnit:
    """Treat the whole document as a single unit, for a document (like
    `IMPLEMENTATION_REVIEW_WORKFLOW.md`) whose marker-presence subject is
    "the whole document" rather than an enumerated set of named sections.

    A document opening with exactly one top-level heading (the ordinary
    `# Title` case) treats that heading's own `own_text` -- the prose
    directly under the title, before its first subsection -- as the
    document-level unit's own text, since that is where the document's own
    top-of-file marker lives; every subsection below it is a child,
    classified independently, exactly like any other nested unit. A
    document with no heading, or more than one top-level heading, falls
    back to the raw text preceding the first heading (or the whole text,
    absent any heading)."""
    units = parse_markdown_units(text)
    if not units:
        return MarkdownUnit(0, "(document)", text, text, 0)

    top_level = min(u.level for u in units)
    roots = [u for u in units if u.level == top_level]
    if len(roots) == 1:
        doc = MarkdownUnit(0, "(document)", roots[0].own_text, text, 0)
        doc.children = roots[0].children
        return doc

    lines = text.splitlines(keepends=True)
    own_text = "".join(lines[:units[0].start_line])
    doc = MarkdownUnit(0, "(document)", own_text, text, 0)
    doc.children = roots
    return doc


def _first_nonblank_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line
    return ""


def unit_state(unit: MarkdownUnit) -> tuple[str, bool]:
    """`(state, explicit)`: `state` is `parse_marker` of the *first
    non-blank line* of `unit.own_text` if that line is an explicit marker,
    else the fail-closed `"CURRENT"` default; `explicit` says which. Never
    looks at `unit.full_text` or any descendant's own text -- a nested
    unit's own marker classifies only that nested unit
    (`D-Review-Material-Lifecycle`'s "Unit and nesting").

    Restricted to the *first* non-blank line, not a search over the whole
    of `own_text`, precisely so the boundary-redrawing guarantee holds
    mechanically rather than by convention alone: merging a `CURRENT`
    region into an adjacent `HISTORICAL`-marked unit by deleting or
    demoting the heading between them (with no marker edit anywhere in the
    diff) leaves that unit's own marker no longer the first line of the
    merged `own_text` -- content from the former `CURRENT` region now
    precedes it -- so the merge falls to the fail-closed default instead of
    inheriting the marker its own text still happens to contain deeper
    in. Every marker this checkpoint or CP1 writes is placed as the first
    line after its heading for exactly this reason."""
    marker = parse_marker(_first_nonblank_line(unit.own_text))
    if marker is not None:
        return marker, True
    return "CURRENT", False


def find_named_top_level_units(units: list[MarkdownUnit], names: tuple[str, ...]) -> dict[str, MarkdownUnit | None]:
    """Resolve each of `names` (an exact heading-text prefix, e.g.
    `"### D-Review-Material-Lifecycle"`'s own heading text without the
    leading `#`s) to the first top-level unit whose heading starts with it,
    or `None` if absent. Used for `WORKFLOW_V2_PLAN.md`'s enumerable,
    registry-named subject set -- never a diff or a cross-corpus match."""
    stripped_names = [name.lstrip("#").strip() for name in names]
    result: dict[str, MarkdownUnit | None] = {name: None for name in names}
    for unit in units:
        for name, stripped in zip(names, stripped_names):
            if result[name] is None and unit.heading.startswith(stripped):
                result[name] = unit
    return result


class MissingLifecycleMarkerError(Exception):
    """Raised by `check_marker_presence` (or returned as a finding list by
    its non-raising sibling) naming every in-scope unit with no explicit
    marker of its own."""


def check_marker_presence_whole_document(text: str) -> bool:
    """`IMPLEMENTATION_REVIEW_WORKFLOW.md`'s own marker-presence subject:
    the whole document, read as one unit. Returns True iff that unit's own
    text (excluding every top-level heading's own subtree, each classified
    independently) carries an explicit marker of either value."""
    doc = document_level_unit(text)
    _, explicit = unit_state(doc)
    return explicit


def check_marker_presence_plan_sections(text: str, section_names: tuple[str, ...]) -> list[str]:
    """`WORKFLOW_V2_PLAN.md`'s own marker-presence subject: each of
    `section_names` (registry-named top-level `### D-*` headings). Returns
    the list of names with no explicit marker of their own -- empty when
    every named section is explicitly marked. A missing name entirely
    (the section does not exist in `text` at all) is also reported, since
    an absent registry-named section can never satisfy the obligation."""
    units = parse_markdown_units(text)
    resolved = find_named_top_level_units(units, section_names)
    missing = []
    for name, unit in resolved.items():
        if unit is None:
            missing.append(name)
            continue
        _, explicit = unit_state(unit)
        if not explicit:
            missing.append(name)
    return missing


_FORBIDDEN_NARRATIVE_RE = re.compile(
    r"\b(?:corrected|revised|narrowed|widened|reassigned|added)\b[^.\n]{0,120}"
    r"\brevision\s+\d+\b[^.\n]{0,160}\bfinding\b",
    re.IGNORECASE,
)


def find_forbidden_narrative(text: str) -> list[str]:
    """The concrete textual shape `D-Review-Material-Lifecycle`'s
    narrative-content guarantee forbids inside an explicit-`CURRENT` unit:
    a "corrected/revised/narrowed/widened/reassigned/added at revision N
    ... finding X"-shaped sentence. Returns every matched snippet (empty
    when none found)."""
    return [m.group(0) for m in _FORBIDDEN_NARRATIVE_RE.finditer(text)]


def check_narrative_content(unit: MarkdownUnit) -> list[str]:
    """Guarantee (vi): asserted only over a unit carrying an *explicit*
    `CURRENT` marker (never over one `CURRENT` only by the fail-closed
    default) -- checked over `own_text` plus every descendant unit's
    `own_text` that is not itself separately, explicitly marked (a
    descendant with its own explicit marker -- `CURRENT` or `HISTORICAL`
    -- is a unit of its own, checked independently, never folded into this
    walk). Returns every forbidden-narrative snippet found; empty means the
    guarantee holds."""
    state, explicit = unit_state(unit)
    if not (explicit and state == "CURRENT"):
        return []

    findings = list(find_forbidden_narrative(unit.own_text))

    def walk(u: MarkdownUnit) -> None:
        for child in u.children:
            _, child_explicit = unit_state(child)
            if child_explicit:
                continue  # separately classified unit; not this guarantee's concern
            findings.extend(find_forbidden_narrative(child.own_text))
            walk(child)

    walk(unit)
    return findings


def mark_missing_units_current(text: str, section_names: tuple[str, ...]) -> str:
    """CP6's marking pass: for each of `section_names` present in `text`
    with no explicit marker of its own, insert `render_marker("CURRENT")`
    as the first line of its `own_text` (immediately after the heading
    line, before any other content). Never touches a section that already
    carries an explicit marker, and never touches any other text. Returns
    the modified document text unchanged if every named section is already
    explicitly marked (or absent)."""
    lines = text.splitlines(keepends=True)
    units = parse_markdown_units(text)
    resolved = find_named_top_level_units(units, section_names)

    insertions = []  # (line_index, marker_line) sorted descending so indices stay valid
    for name, unit in resolved.items():
        if unit is None:
            continue
        _, explicit = unit_state(unit)
        if explicit:
            continue
        # The leading "\n" terminates the heading line itself, so this is
        # correct whether or not that line already ends in a newline (a
        # heading that is the file's own last line, unterminated, included).
        insertions.append((unit.start_line, f"\n{render_marker('CURRENT')}\n"))

    for line_index, marker_block in sorted(insertions, key=lambda t: -t[0]):
        lines.insert(line_index + 1, marker_block)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Governing-version enumeration sweep: no document may present a bare
# "2.1" governing-version reference as exhaustive of the two-stage
# plan-review protocol's own applicability, now that
# TWO_STAGE_PLAN_REVIEW_VERSIONS = {"2.1", "2.2"}.
# ---------------------------------------------------------------------------

_EXHAUSTIVE_ENUMERATION_RE = re.compile(
    r'"1"\s*(?:,|/|\band\b|\bor\b)\s*"2\.1"|"2\.1"\s*(?:,|/|\band\b|\bor\b)\s*"1"'
)
_BARE_21_SCOPED_RE = re.compile(
    r'(?:only[\s-]+`?"2\.1"`?|`?"2\.1"`?[\s-]+only|scoped entirely to\s+`?"2\.1"`?)',
    re.IGNORECASE,
)
_NEGATION_CUE_RE = re.compile(r"\bnot\b|\bnever\b|\bindependent(?:ly|ence)?\b", re.IGNORECASE)
_CONTEXT_WINDOW = 200

# Both detection forms exist to catch a stale claim about the *two-stage
# plan-review protocol's own applicability* specifically -- not every
# unrelated "1"/"2.1" enumeration anywhere in the corpus (the dual-mode
# implementation-review branches, for instance, correctly distinguish
# "1"/"2.1" from "2.2" for a wholly different reason: which review contract
# `/review-implementation` runs, not which items get two-stage plan
# review). An occurrence counts only when its surrounding context is
# actually about plan review.
_PLAN_REVIEW_CONTEXT_RE = re.compile(
    r"plan[\s_-]*review|plan[\s_-]*approval|AWAITING_(?:LOCAL|MANUAL_EXTERNAL)_PLAN|"
    r"/review-plan\b|/apply-plan-review\b|/record-manual-plan-review\b|"
    r"TWO_STAGE_PLAN_REVIEW_VERSIONS",
    re.IGNORECASE,
)


class GoverningVersionSweepFinding:
    __slots__ = ("path", "offset", "line", "form", "snippet")

    def __init__(self, path, offset, line, form, snippet):
        self.path = path
        self.offset = offset
        self.line = line
        self.form = form
        self.snippet = snippet

    def __repr__(self):
        return f"GoverningVersionSweepFinding({self.path!r}, line={self.line!r}, form={self.form!r})"


WHOLE_DOCUMENT_ALLOWLIST = (
    "WORKFLOW_V2_3_PLAN.md",
    "WORKFLOW_V2_3_FOLLOWUPS_PLAN.md",
    "WORKFLOW_V2_AUDIT.md",
)


def _line_number_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_allowlisted_occurrence(text: str, offset: int, historical_spans: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in historical_spans)


def _historical_spans(text: str) -> list[tuple[int, int]]:
    """Byte-offset spans of every unit (at any nesting depth) classified
    `HISTORICAL` -- computed over the unit's *heading line plus* `own_text`,
    mapped back to the full document, used by the sweep's occurrence-level
    allowlist. The heading line itself is included in the span (not just
    the text after it): this corpus authors some headings as a single,
    very long ATX line whose "rest of the line" carries the bulk of a
    changelog entry's own prose -- an occurrence sitting inside the heading
    line's own text must still count as inside that heading's own unit."""
    lines = text.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    spans: list[tuple[int, int]] = []
    units = parse_markdown_units(text)
    for unit in units:
        state, explicit = unit_state(unit)
        if explicit and state == "HISTORICAL":
            heading_start = line_offsets[unit.start_line]
            own_text_start = line_offsets[unit.start_line + 1] if unit.start_line + 1 < len(line_offsets) else len(text)
            # own_text runs to the next heading at any level (or EOF); reuse
            # its length to compute the end offset precisely.
            end = own_text_start + len(unit.own_text)
            spans.append((heading_start, end))
    return spans


def find_governing_version_occurrences(
    path: str, text: str, *, allowlist_whole_document: tuple[str, ...] = WHOLE_DOCUMENT_ALLOWLIST,
) -> list[GoverningVersionSweepFinding]:
    """Both detection forms, occurrence-granular, with the sweep's own
    allowlist applied (whole-document for the three named closed-history
    documents; occurrence-inside-a-HISTORICAL-unit otherwise). A negated or
    version-independence assertion near the match is never an occurrence of
    either form."""
    basename = os.path.basename(path)
    if basename in allowlist_whole_document:
        return []

    historical_spans = _historical_spans(text)
    findings: list[GoverningVersionSweepFinding] = []
    for form, pattern in (("exhaustive_enumeration", _EXHAUSTIVE_ENUMERATION_RE), ("bare_21_scoped", _BARE_21_SCOPED_RE)):
        for m in pattern.finditer(text):
            window_start = max(0, m.start() - _CONTEXT_WINDOW)
            window_end = min(len(text), m.end() + _CONTEXT_WINDOW)
            window = text[window_start:window_end]
            if not _PLAN_REVIEW_CONTEXT_RE.search(window):
                continue
            if _NEGATION_CUE_RE.search(text[max(0, m.start() - 40):m.start()]):
                continue
            if form == "bare_21_scoped" and ('"2.2"' in window or '"1"' in window):
                continue
            if _is_allowlisted_occurrence(text, m.start(), historical_spans):
                continue
            findings.append(
                GoverningVersionSweepFinding(
                    path=path, offset=m.start(), line=_line_number_at(text, m.start()),
                    form=form, snippet=text[max(0, m.start() - 40):m.end() + 40].replace("\n", " "),
                )
            )
    return findings


def sweep_governing_version_enumeration(paths_and_texts: dict[str, str]) -> list[GoverningVersionSweepFinding]:
    """Run `find_governing_version_occurrences` over every `(path, text)`
    pair. Returns the combined, still-flagged finding list -- empty means
    the sweep is clean."""
    findings: list[GoverningVersionSweepFinding] = []
    for path, text in paths_and_texts.items():
        findings.extend(find_governing_version_occurrences(path, text))
    return findings


# ---------------------------------------------------------------------------
# workflow-2.5.0 REVISE round 7, Missing-tests item 1 / B1's own negative-
# corpus sweep: no document may claim `enter_applying_review_feedback` is
# skipped for a `"2.2"` item as an absolute, version-keyed rule ("`"1"`/
# `"2.1"` only", "never needs this call", "for it at all", "not called at
# all", a bare "no `enter_applying_review_feedback` call") without also
# stating the qualifier that makes the claim true: the skip is
# phase-conditional, and the `"2.2"` terminal-phase escape *does* call it.
# Five rounds of point-fixes to this exact prose (rounds 3, 5, 6, and two
# more instances in round 7 itself) are the evidence this needs a standing
# sweep rather than another one-off reword.
# ---------------------------------------------------------------------------

_MARKDOWN_EMPHASIS_STRIP_RE = re.compile(r"[*`]")


def _strip_markdown_emphasis(text: str) -> str:
    """Removes only `*` (bold/italic) and backtick (code-span) markup
    characters -- deliberately never `_`, which is both Markdown's other
    emphasis character *and* a literal character inside every snake_case
    identifier this sweep must keep intact (`enter_applying_review_feedback`
    itself, `implementation_review_stages`, ...). Round 6's own post-fix
    grep for the literal substring `"no enter_applying_review_feedback
    call"` missed round 7's own `makes **no**
    \\`enter_applying_review_feedback\\` call` for exactly this reason: the
    bold asterisks and code-span backticks around the words split the
    substring the naive grep needed intact. Stripping only `*`/backtick
    (not `_`) fixes that gap without corrupting any identifier."""
    return _MARKDOWN_EMPHASIS_STRIP_RE.sub("", text)


_APPLYING_REVIEW_FEEDBACK_REFERENCE_CUE = "enter_applying_review_feedback"

# Round 7's own two `B1` instances, verbatim once markup is stripped, plus
# the wordings round 5/6's own instances used -- named directly from round
# 7's Missing-tests item (a): "the reference cue", (b): "absolute-quantifier
# cues".
_APPLYING_REVIEW_FEEDBACK_ABSOLUTE_CUES = (
    re.compile(r'"1"\s*/\s*"2\.1"\s*only'),
    re.compile(r"never needs this call"),
    re.compile(r"for it at all"),
    re.compile(r"not called at all"),
    re.compile(r"no\s+enter_applying_review_feedback\s+call"),
)

# Round 7's Missing-tests item (c): qualifier cues that mean the surrounding
# claim is the *correct*, scoped statement of the rule, not the superseded
# absolute one. Deliberately narrower than the Missing-tests item's own
# suggested list, which also named the bare word "already": round 7's own
# `B1` instance (1) reads "a `\"2.2\"` item instead arrives at
# `APPLYING_REVIEW_FEEDBACK` **already** ... so this command makes no ...
# call for it at all" -- "already" appears in that *false*, absolute claim
# too, so treating it as a blanket qualifier would silently un-catch the
# exact instance this sweep exists for. `"finds phase"` (the two-word
# phrase `MILESTONE_WORKFLOW.md:349`'s correct passage actually uses --
# "since it finds `phase` already there") is precise enough to keep that
# true negative without reopening the false-negative "already" causes.
_APPLYING_REVIEW_FEEDBACK_QUALIFIER_CUES = (
    "terminal", "escape", "phase-conditional", "finds phase",
    # `review-implementation.md`'s and `WORKFLOW_V2_1_OPERATOR_REFERENCE.md
    # :464`'s own correct, narrower claim -- "this one writer's REVISE
    # branch never itself calls it, because its own only legal source
    # phase is wrong for this specific branch" -- is true without needing
    # the terminal-escape caveat: it is not a claim about every "2.2" item
    # anywhere, only about one writer's one action. Both passages share
    # this exact phrase.
    "legal source phase",
)

_APPLYING_REVIEW_FEEDBACK_CONTEXT_WINDOW = 300


class ApplyingReviewFeedbackVersionClaimFinding:
    __slots__ = ("path", "offset", "line", "snippet")

    def __init__(self, path, offset, line, snippet):
        self.path = path
        self.offset = offset
        self.line = line
        self.snippet = snippet

    def __repr__(self):
        return (
            f"ApplyingReviewFeedbackVersionClaimFinding({self.path!r}, "
            f"line={self.line!r}, snippet={self.snippet!r})"
        )


def find_applying_review_feedback_version_claims(
    path: str, text: str,
) -> list[ApplyingReviewFeedbackVersionClaimFinding]:
    """Flags every occurrence of an absolute-quantifier cue that (a) sits
    within `_APPLYING_REVIEW_FEEDBACK_CONTEXT_WINDOW` characters of a
    reference to `enter_applying_review_feedback` -- so an unrelated "not
    called at all" elsewhere in the document is never in scope -- and (b)
    has no qualifier cue anywhere in that same window. Both the cue match
    and the window are computed over markup-stripped text
    (`_strip_markdown_emphasis`), so Markdown emphasis/code-span characters
    between the cue's own words can never defeat the match the way they
    defeated round 6's literal-substring grep. The window, not a
    sentence split, is what lets a qualifier stated in the *next* sentence
    of the same bullet (`WORKFLOW_V2_1_OPERATOR_REFERENCE.md`'s own
    already-correct `:439`/`:464` passages, where "terminal" sits just
    outside the sentence carrying the absolute-sounding "no ... call")
    still suppress the finding."""
    stripped = _strip_markdown_emphasis(text)
    findings: list[ApplyingReviewFeedbackVersionClaimFinding] = []
    for cue_re in _APPLYING_REVIEW_FEEDBACK_ABSOLUTE_CUES:
        for m in cue_re.finditer(stripped):
            window_start = max(0, m.start() - _APPLYING_REVIEW_FEEDBACK_CONTEXT_WINDOW)
            window_end = min(len(stripped), m.end() + _APPLYING_REVIEW_FEEDBACK_CONTEXT_WINDOW)
            window = stripped[window_start:window_end]
            if _APPLYING_REVIEW_FEEDBACK_REFERENCE_CUE not in window:
                continue
            if any(cue in window.lower() for cue in _APPLYING_REVIEW_FEEDBACK_QUALIFIER_CUES):
                continue
            findings.append(
                ApplyingReviewFeedbackVersionClaimFinding(
                    path=path, offset=m.start(), line=_line_number_at(stripped, m.start()),
                    snippet=window.strip(),
                )
            )
    return findings


def sweep_applying_review_feedback_version_claims(
    paths_and_texts: dict[str, str],
) -> list[ApplyingReviewFeedbackVersionClaimFinding]:
    """Run `find_applying_review_feedback_version_claims` over every
    `(path, text)` pair. Returns the combined, still-flagged finding list --
    empty means the sweep is clean."""
    findings: list[ApplyingReviewFeedbackVersionClaimFinding] = []
    for path, text in paths_and_texts.items():
        findings.extend(find_applying_review_feedback_version_claims(path, text))
    return findings
