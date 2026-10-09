#!/usr/bin/env python3
# state_writer: false
"""Prototype: review-identity fingerprint for Workflow v2.1 core.

Implements compute_review_content_id() (plan stage) and compute_bundle_id(),
built in direct response to round-6 external review (`OPUS-R6-001`,
`OPUS-R6-002`, `OPUS-R6-008`), round-7's follow-up prototype-correction
review (`PROTO-R7-001` through `PROTO-R7-004`), round-8's second
correction pass (`OPUS-R8-001` through `OPUS-R8-019`), round-9's
integration-focused pass (`GPT-R9-001`, `-002`, `-009`, `-010`, `-014`;
the rest of round 9's findings are lifecycle/state-machine design fixes,
applied in `docs/ai-workflow/WORKFLOW_V2_PLAN.md` rather than here), and
round-10's independent re-verification, which found the identity
algorithm itself finished and correct (all five of its own round-8
regression probes re-run and confirmed fixed by execution) and one
remaining classification-completeness gap: `PLAN_STAGE_EXCLUDED_PATHS`/
`PREFIXES` covered only today's working tree, not the paths this
milestone's own later checkpoints will create, which made plan-approval
durability unevaluable the moment the first implementation-phase file
landed (`OPUS-R10-001`). Both lists are now populated with the concrete
future paths this milestone's registry declares -- see the exclusion
constants below. Per round 10's own assessment, the identity subsystem
should now be treated as **frozen**: no further changes to
`compute_bundle_id()`/`compute_review_content_id()` unless a failing test
forces one; the remaining work is wiring it into commands (WF4a-i), not
refining it.

Round 11 (a manual external reviewer, evaluating a proposed two-stage
local-then-manual-external plan-review protocol alongside the frozen
identity subsystem) made exactly one narrowly-scoped classification
addition, explicitly endorsed as compatible with the freeze
(`GPT-R11-008`, acceptance criterion 13): `PLAN_STAGE_PROTECTED` now also
names the concise plan-review operator guide's final path
(`docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md`), pre-declared before the file
itself exists, the same proactive-classification pattern `OPUS-R10-001`
established. No other change to either function or either classification
list.

Round 7 found the first draft fail-open (unclassified paths silently
ignored instead of raising), non-deterministic (dependent on local
`core.abbrev` and on how the caller spelled the base commit), unable to
prove worktree/commit parity, and reporting an ID that had gone stale
relative to the actual final bundle. Round 8 found that the round-7 fixes
were real but incomplete at the boundaries: `bundle_id`'s self-exclusion was
scoped to one file instead of one field-pattern; the text-based
normalizers collapsed byte-distinct inputs (CRLF/LF, trailing newline,
non-UTF-8 bytes) to the same hash; worktree mode came from the filesystem
instead of from Git's own `core.fileMode` semantics, breaking parity on a
normal Git configuration; a symlinked *directory* bypassed the fail-closed
symlink check because `Path.is_dir()` follows symlinks and was tested
before `is_symlink()`; the commit-source identity entry point never called
the classifier at all; the exclusion list was prefix-matched against exact
paths, silently over-excluding suffixed siblings; gitignored paths were
invisible to classification while a dead, unreachable `.ai-review/`
exclusion entry implied otherwise; exclusion carried no cost or
justification; identity-bearing scalars defaulted instead of being
required; and the protected/excluded sets that determine the manifest were
themselves outside the hashed projection.

This third draft's design changes:
  - `compute_bundle_id()`'s self-reference exclusion is now a single
    field-pattern (`^bundle_id: [0-9a-f]{64}$`), normalized out of *every*
    bundle file, not just `MANIFEST.md` -- resolves `OPUS-R8-001`/`-013`.
    `MANIFEST.md` is now a required bundle file; its absence fails closed.
  - Byte-level normalization throughout `compute_bundle_id()`: split only
    on `b"\n"`, never decode, never use `str.splitlines()` (which also
    treats `\r`, `\x0b`, `\x0c` as line breaks) -- resolves `OPUS-R8-002`.
  - Worktree file mode is derived from Git's own `core.fileMode` semantics
    (executable bit is meaningful only when that config is true) rather
    than raw `os.access()` -- resolves `OPUS-R8-003`.
  - Bundle-entry classification checks `is_symlink()` before `is_dir()`,
    and rejects any non-regular, non-directory entry (FIFOs, sockets) --
    resolves `OPUS-R8-004`.
  - The commit-source identity entry point now calls the same fail-closed
    classifier as the worktree entry point, scoped to `base..commit`
    -- resolves `OPUS-R8-005`.
  - The exclusion list is split into an exact-path set and a
    directory-prefix set (each prefix required to end in `/`, validated at
    import); every entry carries a one-line justification -- resolves
    `OPUS-R8-006`/`-008`.
  - The dead `.ai-review/` prefix entry (unreachable because `.gitignore`
    already hides it from untracked-path enumeration) is removed rather
    than kept for appearance -- resolves `OPUS-R8-007`.
  - `docs/TECHNICAL_DECISIONS.md` moved from excluded to **protected**: a
    technical-decision entry created specifically to satisfy a review
    finding is durable design content the reviewer approving the plan is
    binding, not incidental housekeeping -- resolves `OPUS-R8-008`
    bonus-answer 5.
  - `CHANGED_FILES.txt`'s `generated:` line is found by searching the
    header block for the field, and fails closed if it is missing or
    duplicated, rather than silently no-opping when its position shifts
    -- resolves `OPUS-R8-009`.
  - `work_item_type`/`work_item_id`/`plan_revision` have no defaults; the
    caller must supply them. `work_item_id` is validated against the
    slug grammar -- resolves `OPUS-R8-010`.
  - The protected path set and the exclusion sets are themselves part of
    the hashed `review_content_id` projection -- resolves `OPUS-R8-014`.
  - `PLAN_STAGE_PROTECTED`/`PLAN_STAGE_EXCLUDED_*` are frozen (frozenset /
    `MappingProxyType`), never a literal mutable default -- resolves
    `OPUS-R8-015`.
  - Bundle entry keys use `Path.as_posix()`, never the OS path separator
    -- resolves `OPUS-R8-017`.

Round 9 found the round-8 fixes correct as far as they went, but two of
them created new problems at a different boundary, plus two smaller gaps:
  - `bundle_id`'s self-reference exclusion, generalized in round 8 to every
    bundle file, made a *wrong or stale* `bundle_id:`-shaped line in any
    non-manifest file invisible rather than flagged -- confirmed live, in
    this very round's own bundle (see the "Round 8/9 disposition" sections
    of `WORKFLOW_V2_PLAN.md`). Narrowed back to exactly one legal location:
    `MANIFEST.md`'s own field is still stripped; the same pattern appearing
    in *any other* bundle file now raises `ForeignBundleIdFieldError`
    instead of being silently ignored -- resolves `GPT-R9-001`.
  - `compute_bundle_id()`'s and `_snapshot_worktree()`'s mode detection
    used `os.access(path, os.X_OK)`, which reflects the *current process's
    effective* access (user, group, ACLs) rather than the file's own
    stored mode bits -- two reviewers on different filesystems/users could
    derive different logical modes for the same bytes. Both now read the
    owner-execute stat bit directly (`st_mode & 0o100`), matching exactly
    what Git itself consults -- resolves `GPT-R9-009`.
  - Importing this module to verify a bundle could write a `.pyc` file
    inside an extracted bundle's `files/` copy, changing the very
    `bundle_id` being verified. `sys.dont_write_bytecode = True` is now set
    unconditionally at import time -- resolves `GPT-R9-010`.
  - `work_item_type` was accepted as any string; now validated against the
    controlled vocabulary `{"process", "product"}` before hashing --
    resolves `GPT-R9-014`.

Deliberately stdlib-only (no new dependency category; recorded explicitly
in `docs/TECHNICAL_DECISIONS.md`). Not yet wired into any command; this is
a prototype exercised by `workflow_fingerprint_test.py` (hermetic unit
suite) and `workflow_fingerprint_demo_test.py` (explicit real-repository
integration demo).

Path-name policy vs. bundle-content policy (`OPUS-R8-019`): path names
handled via `git ls-files -z`/`git diff -z` are decoded as UTF-8 and will
raise loudly (uncaught `UnicodeDecodeError`) on a non-UTF-8 path -- this
repository's tracked paths are expected to be UTF-8, and a silent
byte-preserving fallback here would hide a genuinely unusual path rather
than surface it. Bundle *file content* is the opposite case -- content
such as `git status --short` output routinely contains non-UTF-8 bytes
that are not a defect -- so `compute_bundle_id()` treats content as opaque
bytes throughout and never decodes it. Two different kinds of data,
deliberately two different policies, stated once here.

Implementation stage (`compute_review_content_id` for `stage="implementation"`)
remains out of scope for this prototype: this milestone has not started
implementation, there is no real changed-file diff to validate a
commit-source diff algorithm against, and building one against a fabricated
example would not be executable evidence. Deferred to WF4a-i, alongside
real implementation-stage fixtures.

`WF4a-i` (this checkpoint): the identity algorithm itself stays frozen, per
round 10's own instruction -- nothing above this paragraph changed. Three
additions, all previously recorded as forward-looking obligations:
  - **Atomic manifest write** (resolves `OPUS-R20-001`, missing-test item
    138): `compute_bundle_id()` gained an optional
    `manifest_content_override` parameter so `bundle_id` can be computed
    against a manifest that has not been written to disk yet.
    `write_manifest_with_verified_identifiers` now computes and recomputes
    both identifiers entirely in memory and only ever touches the real
    `MANIFEST.md` once, at the very end, via a temp file + `os.replace()`
    -- a protected-path edit landing anywhere in the compute/recompute
    window still raises, but the real file is now provably untouched
    rather than merely "detectable as stale."
  - **Root-level build files, pinned** (resolves `OPUS-R20-002`,
    missing-test item 139): a regression test confirms
    `build.gradle.kts`/`settings.gradle.kts`/`gradlew`/`Makefile` still
    raise `UnclassifiedPathError` under the plan-stage classifier -- the
    deliberate, previously-only-documented decision that these stay
    unclassified rather than being added to `PLAN_STAGE_EXCLUDED_*`.
  - **Implementation-stage manifest, built** (resolves `OPUS-R20-003`,
    missing-test item 140, and the "remaining scope" this module's own
    docstring named above): `load_implementation_stage_classification()`,
    `classify_path_implementation_stage()`, and
    `compute_review_content_id_implementation_stage()`/`_at_commit()` are
    new, independent functions -- never a reuse or adaptation of the
    plan-stage constants/functions, per D-Fingerprint's explicit
    instruction that the two stages' sets are near-inverses of each other.
    The protected/excluded sets are loaded from a tracked, machine-readable
    declarations file
    (`docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`) rather
    than a second hardcoded Python constant -- the generalization
    `D-Registry`'s and `D-Fingerprint`'s future-work notes named as this
    checkpoint's own scope (`GPT-R9-011`). That file lives under
    `docs/ai-workflow/registry/`, which is excluded at the plan stage --
    so its own *bytes* are never a member of the plan-stage content
    projection. That is **not** the same as "editing it never stales the
    plan approval", which is what this docstring used to claim (salvage
    audit `I9`): the plan-stage projection also hashes the classification
    sets themselves (`sorted(protected_paths)`, `sorted(excluded_paths)`,
    `sorted(excluded_prefixes)` -- see
    `compute_review_content_id_plan_stage`), and those sets are read out
    of this very file. Editing the `plan_stage` half therefore *does*
    change `review_content_id` and *does* stale the plan approval, by
    design: what is excluded is a reviewed fact, not silently mutable.
    Editing only the `implementation_stage` half leaves the plan-stage
    digest untouched and stales `technical_approval` instead, once one
    exists. Exercised
    against this milestone's own real `WF0`/`WF1a`/`WF1b` commits in
    `workflow_fingerprint_demo_test.py` -- the real implementation-stage
    fixture this docstring previously said did not exist yet.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, NamedTuple

# A bundle-verification run must never write bytecode into a bundle
# directory it is hashing -- that would change bundle_id as a side effect
# of verifying it. Set unconditionally, not left to the caller's
# environment (resolves GPT-R9-010).
sys.dont_write_bytecode = True


class UnclassifiedPathError(Exception):
    """Raised when a changed or untracked path is neither protected nor
    excluded — fails closed per PROTO-R7-001's requirement that this
    actually be reachable, not merely defined."""


class UnsupportedPathTypeError(Exception):
    """Raised for a protected path or bundle entry whose Git object type
    this prototype does not model (anything but a regular file or a
    symlink for protected paths; anything but a regular file or directory
    for bundle entries) — fails closed per PROTO-R7-007/OPUS-R8-004."""


class AbsentProtectedPathError(Exception):
    """Raised when a plan-stage protected path does not exist. At plan
    stage every protected path is design content the reviewer is meant to
    be reading right now, not a placeholder for work the approved plan has
    yet to produce — a silent `exists: False` tombstone here would let an
    approval bind to content nobody has seen (resolves OPUS-R14-001/-006).
    This does not apply to the (not yet built) implementation-stage
    projection, where a deletion is a real, representable change and a
    tombstone is the correct representation."""


class AmbiguousBaseError(Exception):
    """Raised when a caller-supplied base spelling does not resolve to
    exactly one commit — resolves PROTO-R7-002."""


class InvalidWorkItemIdError(Exception):
    """Raised when a work_item_id does not match the slug grammar
    `^[a-z0-9][a-z0-9_-]{0,63}$` — resolves OPUS-R8-010."""


class InvalidWorkItemTypeError(Exception):
    """Raised when work_item_type is not one of the controlled values
    `{"process", "product"}` — resolves GPT-R9-014."""


class InvalidBundleStageError(Exception):
    """Raised when a `stage` argument to `resolve_bundle_dir` is neither
    `None` nor one of the four controlled generation stages
    `prepare-ai-review.sh` itself accepts. Fails closed rather than
    falling through to the flat-compatibility branch, so a typo
    (`"Plan"`, `"plan-stage"`) can never silently reintroduce the
    first-generation misresolution this argument exists to close."""


class ForeignBundleIdFieldError(Exception):
    """Raised when a bundle file other than `MANIFEST.md` contains a line
    matching the `bundle_id: <64-hex-chars>` contract. Exactly one bundle
    file may ever report the bundle's own identity; a lookalike line
    anywhere else is far more likely a stale or tampered value than
    legitimate content, and round 8's blanket exclusion made exactly that
    case invisible instead of flagged — resolves GPT-R9-001."""


class MissingRequiredBundleFileError(Exception):
    """Raised when a bundle directory is missing a required file (e.g.
    `MANIFEST.md`) — resolves OPUS-R8-001."""


class MalformedBundleIdFieldError(Exception):
    """Raised when a bundle file contains more than one line matching the
    `bundle_id: <64-hex-chars>` contract — resolves OPUS-R8-013."""


class MalformedExclusionConstantError(Exception):
    """Raised at import time if a directory-prefix exclusion entry does
    not end in `/` — resolves OPUS-R8-006."""


class MissingGeneratedTimestampError(Exception):
    """Raised when `CHANGED_FILES.txt`'s header has no `generated:` line
    — resolves OPUS-R8-009 (fail closed instead of silently no-opping)."""


class DuplicateGeneratedTimestampError(Exception):
    """Raised when `CHANGED_FILES.txt`'s header has more than one
    `generated:` line — resolves OPUS-R8-009."""


class PlanRevisionMismatchError(Exception):
    """Raised when the registry JSON's declared `plan_revision` cannot be
    read, or disagrees with the plan document's own title. Round 15
    deferred sourcing `plan_revision` from the registry on the premise
    that no real caller existed yet to source it from anywhere else; by
    round 18 `__main__` was a real caller writing the authoritative
    manifest with a hardcoded, already-stale literal — resolves
    `OPUS-R18-003`."""


class ReviewContentIdNotIdempotentError(Exception):
    """Raised when `review_content_id`, recomputed immediately after
    being written into `MANIFEST.md`, does not match the value just
    written — the same compute-last-and-assert discipline round 8
    established for `bundle_id`, extended to the identifier that actually
    gates approval durability (`OPUS-R18-001`)."""


class BundleIdNotIdempotentError(Exception):
    """Raised when `bundle_id`, recomputed immediately after being
    written into `MANIFEST.md`, does not match the value just written.
    Previously only printed as a boolean; the write path now fails
    closed on this instead of merely reporting it, matching the
    treatment `review_content_id` needed (`OPUS-R18-001`, applied
    symmetrically)."""


class MissingReviewContentIdStatementError(Exception):
    """Raised when `REVIEW_REQUEST.md` states no `review_content_id`
    value at all — resolves `OPUS-R18-005`: a reviewer needs a second,
    checkable copy that does not depend on the one file capable of being
    silently mutated."""


class ReviewContentIdMismatchError(Exception):
    """Raised when `REVIEW_REQUEST.md`'s stated `review_content_id`
    disagrees with `MANIFEST.md`'s (or states more than one disagreeing
    value) — resolves `OPUS-R18-005`."""


class WorktreeOrHeadMismatchError(Exception):
    """Raised by a **repository-local** consumer (`/approve-review`,
    `/review-plan`, `/review-implementation`, and -- workflow-2.7.0 --
    `/record-manual-plan-review`/`/record-manual-implementation-review`
    through `workflow_state.ingest_manual_review_verdict`) when the current worktree
    root or HEAD SHA differs from what `MANIFEST.md` recorded at
    bundle-generation time — the actual first-party Milestone-8 incident
    (a stale bundle read from a different worktree) this check exists to
    catch (`D-Bundle-Manifest`, resolves `OPUS-R6-016`). Never raised for
    an external reviewer consuming a portable extracted archive — that
    consumer treats the recorded values as diagnostic metadata only
    (`GPT-R9-015`)."""


class StageCompletenessError(Exception):
    """Raised when a bundle's author-written stage document (`PLAN.md` at
    the plan stage, `IMPLEMENTATION_SUMMARY.md` at the implementation/
    post-fix stages) does not state the revision the authoritative source
    currently declares — a stale, unrefreshed copy inside the bundle is
    exactly the class of mistake round 5's `R5-PLAN-015` named this check
    to catch."""


class TestResultsStaleError(Exception):
    """Raised when a plan-stage bundle's `TEST_RESULTS.md` does not state
    this round's own stage/revision/HEAD (item 272): an empty stub, a
    copy left over from a previous plan-review round, or stale
    implementation-stage evidence carried forward into a plan-only
    round, must never publish silently — `GPT-R42-001`'s consistency-
    guard precedent, extended to the one author-written file that
    previously carried no machine-checked marker at all."""


class MissingFeedbackBindingFieldError(Exception):
    """Raised when `REVIEW_FEEDBACK.md` is missing one of its three
    required binding fields (`Reviewed bundle ID:`, `Reviewed base
    commit:`, `Work item:`) — resolves `OPUS-R14-002`'s binding-field
    requirement, generalized from WF0's bootstrap-only check to every
    ordinary review round (`WFR-03`)."""


class FeedbackBundleMismatchError(Exception):
    """Raised when `REVIEW_FEEDBACK.md`'s stated `Reviewed bundle ID:`,
    `Reviewed base commit:`, or `Work item:` disagrees with the bundle
    actually being approved against — names both the feedback's own value
    and the current value so the mismatch is diagnosable, never silently
    absorbed (`WFR-03`, resolves `OPUS-R6-021`/`GPT-R9-015`'s underlying
    concern applied to feedback matching rather than worktree/HEAD)."""


class FeedbackOwnedByOtherWorkItemError(Exception):
    """Raised when `/review-implementation`'s write would overwrite a
    `REVIEW_FEEDBACK.md` whose own `Work item:` binding field names a
    different work item — `resolve_feedback_dir`'s scoped-else-flat rule
    means two work items can resolve the identical flat path before
    either has its own scoped feedback directory; this refuses the write
    rather than relocating it, leaving `resolve_feedback_dir` and every
    other command's resolution against it completely untouched
    (`GPT-FUP-R6-I01`, `LPR-R7-B01`)."""


class UnknownFeedbackLayoutError(Exception):
    """Raised when a work item's `feedback_layout` field is present but is
    not one of `FEEDBACK_LAYOUT_VALUES` (`D-Feedback-Layout`, INV-3). An
    absent field means legacy; a present, unrecognized value -- including
    `null` -- is never guessed at."""


class FeedbackLayoutUndecidableError(Exception):
    """Raised when `docs/ai-workflow/WORKFLOW_STATE.json` exists but cannot
    decide a work item's feedback layout: a symlink, bytes that are not
    JSON, a top level that is not an object, a `work_items` that is not an
    object, or the item's own entry that is not an object
    (`D-Feedback-Layout`, INV-3). The resolver refuses rather than falling
    back to the legacy rule, since a scoped item misresolved to the flat
    path would read or overwrite another item's feedback."""


class ManualFeedbackForeignWorkItemError(Exception):
    """Raised by `/record-manual-plan-review` and
    `/record-manual-implementation-review` when the pasted
    `REVIEW_FEEDBACK.md` carries a `Work item:` binding field naming a
    different work item (`D-Feedback-Layout`, "Manual-record binding"). A
    pasted file without that field is not refused here: the hard
    `review_content_id` check still binds it."""


# ---------------------------------------------------------------------------
# D-Fingerprint-Generalization (Revision 21, `WF8B-S1-001`): per-work-item
# plan-stage metadata resolution. `resolve_plan_stage_metadata` is the one
# place a `work_item_id` turns into the facts needed to fingerprint its own
# plan-stage content -- fail-closed matrix conditions 1-9, 11 below.
# ---------------------------------------------------------------------------


class UnknownWorkItemError(Exception):
    """Raised when a `work_item_id` names no entry in `WORKFLOW_STATE.json`'s
    `work_items` map, or a resolved entry's own `work_item_id` field
    disagrees with the map key that resolved it — fail-closed matrix
    condition 1."""


class PlanStageNotApplicableError(Exception):
    """Raised when the resolved work item's `work_item_type` is not one of
    the plan-stage-applicable types (`"process"`, `"product"`) —
    `work_item_kind` is not consulted, so a `"synthetic"`-kind item
    (whichever `work_item_type` it carries) resolves exactly like any
    other item of that same type — condition 2."""


class MissingPlanStageMetadataError(Exception):
    """Raised when the resolved work item's `plan_path`/`registry_path`/
    `mapping_path`/`base_commit` is `null` — naming which field —
    condition 3."""


class InvalidPlanStageMetadataPathError(Exception):
    """Raised when a declared `plan_path`/`registry_path`/`mapping_path`
    fails the shared path-grammar validator: not repo-relative, uses a
    non-POSIX separator, contains a `.`/`..` path component, names a
    symlink, or does not exist as a tracked regular file at the resolved
    source — condition 4."""


class MissingWorkItemArtifactsDeclarationError(Exception):
    """Raised when `<work_item_id>-artifacts.json` is absent at the
    resolved source, or present but declares no `plan_stage` key —
    condition 5 (the latter is also this finding's stated condition-11
    pre-migration boundary: a pre-migration artifacts file has no
    `plan_stage` key at all, which is not a distinct mechanism)."""


class StaleArtifactsDeclarationError(Exception):
    """`D-Approval-Commits`' "Conditional fifth commit member" freshness
    condition: raised when `<work_item_id>-artifacts.json`'s current
    working-tree bytes are pending (differ from `HEAD`) but are *not*
    byte-identical to the copy the current bundle already captured —
    committing them would bind bytes no reviewer ever saw. Names both the
    stale worktree path and the bundle's captured copy path."""


class RegistryWorkItemIdMismatchError(Exception):
    """Raised when the registry JSON's own `work_item_id` disagrees with
    the map key that resolved it — condition 6."""


class MappingWorkItemIdMismatchError(Exception):
    """Mapping-JSON counterpart of `RegistryWorkItemIdMismatchError` —
    condition 6."""


class ArtifactsWorkItemIdMismatchError(Exception):
    """Artifacts-declarations-JSON counterpart of
    `RegistryWorkItemIdMismatchError` — condition 6."""


class DuplicateWorkItemArtifactPathError(Exception):
    """Raised when a non-null `plan_path`/`registry_path`/`mapping_path`
    is claimed by more than one work item, checked independently per
    field, not only as a triple — condition 8."""


class PlanStageMetadataNotProtectedError(Exception):
    """Raised when `plan_path`/`registry_path`/`mapping_path` is not a
    member of the resolved protected-path set, or the three are not
    pairwise distinct — condition 7."""


class BundleWorkItemMismatchError(Exception):
    """Raised when a bundle's existing `MANIFEST.md` disagrees with the
    resolved work item — naming a different `work_item_id`, no
    `work_item_id` at all ("unbound"), or a different `base_commit` —
    conditions 12/13."""


class BundleRelocationDestinationExistsError(Exception):
    """Raised by `relocate_flat_bundle_to_scoped_layout` (migration step
    8a) when the destination `.ai-review/<work_item_id>/current/` already
    exists and is non-empty -- refuses rather than nesting the flat
    source directory inside it silently."""


class BundleRelocationPartialMoveError(Exception):
    """Raised by `relocate_flat_bundle_to_scoped_layout` when a post-move
    verification finds the destination's file set or content disagrees
    with the pre-move source snapshot -- an interrupted/partial move is
    left diagnosable rather than bound to a manifest as if complete."""


class BundleRejectedError(Exception):
    """Raised by `assert_bundle_not_rejected` (`WFR-67`) when a work
    item's `REJECTED` marker is present, or its presence could not be
    determined -- refuses before the caller's own first durable write,
    read, or report, naming the marker path and whatever diagnostic
    content it holds."""


class FunctionalReviewAlreadyAppliedError(Exception):
    """Raised by `assert_functional_review_not_already_consumed` (O3,
    `workflow-v2-3-followups` continued scope, external cross-model
    review round 2) when `<feedback_dir>/FUNCTIONAL_REVIEW.md`'s current
    content byte-for-byte matches what `mark_functional_review_consumed`
    already recorded as applied -- refuses before `/apply-functional-
    review` re-processes findings it has already acted on."""


class PlanStageDocumentStaleError(Exception):
    """Raised by `assert_plan_stage_document_matches_pin` (`WFR-67`,
    generator-side stage-document binding, part 3) when `bundle_dir/PLAN.md`,
    its `files/` copy, or the archive's own extracted copy is no longer
    byte-identical to the pinned `plan_path` snapshot the generation
    derived it from -- the binding that makes `review_content_id`
    (computed from that same pin) actually describe the document a
    reviewer reads."""


class BundleWithdrawalError(Exception):
    """Raised by `withdraw_bundle` (`WFR-67`, part 3b) when a withdrawal
    step cannot complete -- the exception always follows a `REJECTED`
    marker update naming the failed step and the surviving path, so a
    partial withdrawal is never silent."""


WORK_ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# D1's controlled vocabulary. "synthetic" (WF8b's dry-run item) is a
# work_item_kind, not a work_item_type -- a "synthetic"-kind item whose
# work_item_type is "process" (e.g. v2-1-dry-run) resolves and fingerprints
# its own plan-stage content exactly like any other process or product item,
# once it has plan_path/registry_path/mapping_path/base_commit declared
# (D-Fingerprint-Generalization, OPUS-R25-011).
WORK_ITEM_TYPES = frozenset({"process", "product"})


def validate_work_item_id(work_item_id: str) -> None:
    """Fails closed on a non-`str` before the regex match (salvage audit
    `I2`): both validators below are reached with values read straight out
    of an unvalidated `WORKFLOW_STATE.json`, so a corrupt or hand-edited
    JSON value (a list, a dict, a number) must produce this module's own
    documented error rather than a raw `TypeError` from `re`/`frozenset`.
    `workflow-v2-3-1` CP1's round-1 remediation added exactly this guard
    to `resolve_plan_stage_metadata`'s own inline condition-2 check; it
    belongs at the shared validator too, which every implementation-stage
    reader goes through."""
    if not isinstance(work_item_id, str) or not WORK_ITEM_ID_RE.match(work_item_id):
        raise InvalidWorkItemIdError(work_item_id)


def validate_work_item_type(work_item_type: str) -> None:
    """See `validate_work_item_id` -- same fail-closed rule, same reason
    (salvage audit `I2`)."""
    if not isinstance(work_item_type, str) or work_item_type not in WORK_ITEM_TYPES:
        raise InvalidWorkItemTypeError(work_item_type)


def _owner_executable(st_mode: int) -> bool:
    """Whether the owner-execute stat bit is set, read directly from the
    file's own stored mode bits -- not `os.access()`, which reflects the
    *current process's effective* access (user/group/ACLs) and can differ
    from the file's actual mode across users or filesystems. This is
    exactly the bit Git itself consults when deciding `100644` vs.
    `100755` — resolves GPT-R9-009."""
    return bool(st_mode & 0o100)


# The global pathspec modes Git refuses to combine with
# `--literal-pathspecs` (exit 128, "global 'literal' pathspec setting is
# incompatible with all other global pathspec settings"). Every literal
# declared-path read drops them from its environment, so an operator's
# `GIT_GLOB_PATHSPECS=1` neither aborts approval/generation nor surfaces as
# a misleading "not a tracked path" (implementation review round 3, `O1`).
CONFLICTING_PATHSPEC_ENV = ("GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS")


def literal_pathspec_env() -> dict[str, str]:
    """`os.environ` minus `CONFLICTING_PATHSPEC_ENV` -- the environment for
    every `git --literal-pathspecs` call on a declared path."""
    return {key: value for key, value in os.environ.items() if key not in CONFLICTING_PATHSPEC_ENV}


def _run(args: list[str], cwd: Path, input_bytes: bytes | None = None) -> str:
    if input_bytes is None:
        result = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)
        return result.stdout
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True, input=input_bytes)
    return result.stdout.decode()


def _canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def resolve_base(repo_root: Path, base: str) -> str:
    """Resolve any base spelling (short SHA, full SHA, branch, tag, `HEAD`)
    to its full, canonical 40-character commit SHA. Two different spellings
    of the same commit must resolve to the same value, and an ambiguous or
    invalid base must fail before any manifest work starts — resolves
    PROTO-R7-002."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
            cwd=repo_root, check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise AmbiguousBaseError(
            f"base {base!r} does not resolve to exactly one commit"
        ) from exc
    resolved = out.strip()
    if len(resolved) != 40:
        raise AmbiguousBaseError(f"base {base!r} resolved to unexpected value: {resolved!r}")
    return resolved


def _hash_object(repo_root: Path, rel_path: str) -> str:
    return _run(["git", "hash-object", "--", rel_path], cwd=repo_root).strip()


def _hash_blob_bytes(repo_root: Path, data: bytes) -> str:
    return _run(
        ["git", "hash-object", "--stdin", "-t", "blob"], cwd=repo_root, input_bytes=data
    ).strip()


def _untracked_paths(repo_root: Path) -> set[str]:
    """Untracked, non-gitignored paths. Gitignored paths are categorically
    outside plan-stage content identity (OPUS-R8-007): they are invisible
    here by the same `--exclude-standard` flag that makes the `.gitignore`
    entry classify anything real, and no exclusion-list entry claims to
    cover them."""
    out = _run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo_root)
    return {p for p in out.split("\x00") if p}


def _changed_tracked_paths(repo_root: Path, base_full: str) -> set[str]:
    out = _run(["git", "diff", "--name-only", "-z", base_full], cwd=repo_root)
    return {p for p in out.split("\x00") if p}


def _changed_tracked_paths_between(repo_root: Path, base_full: str, target: str) -> set[str]:
    out = _run(["git", "diff", "--name-only", "-z", base_full, target], cwd=repo_root)
    return {p for p in out.split("\x00") if p}


def _core_file_mode_enabled(repo_root: Path) -> bool:
    """Whether Git honours the filesystem executable bit in this repository
    — resolves OPUS-R8-003. Git's own default is true; `--type=bool`
    normalizes any spelling (`true`/`1`/`yes`) to `true`/`false`."""
    result = subprocess.run(
        ["git", "config", "--type=bool", "core.fileMode"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return True
    return result.stdout.strip() == "true"


PLAN_TITLE_REVISION_RE = re.compile(r"\(Revision (\d+)\)")

# Retired as live defaults (`OPUS-R28-011`): a bare `load_plan_revision(repo_root)`
# call silently meant "workflow-v2-1-core", the same defect class this whole
# revision exists to remove. Kept only as named migration-comparison
# fixtures for hermetic tests that still want this milestone's own real
# paths spelled out explicitly -- never a live fallback for any caller this
# migration reaches (`load_plan_revision`'s two path parameters are
# required, no default).
DEFAULT_REGISTRY_PATH = Path("docs/ai-workflow/registry/workflow-v2-1-core-registry.json")
DEFAULT_PLAN_PATH = Path("docs/ai-workflow/WORKFLOW_V2_PLAN.md")


def _to_posix(path: Path | str) -> str:
    return path.as_posix() if isinstance(path, Path) else str(path)


def _read_bytes_at_source(repo_root: Path, rel_path: Path | str, at_commit: str | None) -> bytes:
    """Read a tracked file's bytes either from the live working tree
    (`at_commit is None`) or from a specific commit via `git show` -- the
    one choice-of-source threaded through every read
    `resolve_plan_stage_metadata` performs (missing-test item 145: a
    commit-source computation never reads the live working tree or the
    live `WORKFLOW_STATE.json` for any of these facts)."""
    rel = _to_posix(rel_path)
    if at_commit is None:
        return (repo_root / rel).read_bytes()
    result = subprocess.run(
        ["git", "show", f"{at_commit}:{rel}"], cwd=repo_root, capture_output=True,
    )
    if result.returncode != 0:
        raise FileNotFoundError(f"{rel!r} not found at {at_commit!r}")
    return result.stdout


def _path_exists_at_source(repo_root: Path, rel_path: Path | str, at_commit: str | None) -> bool:
    rel = _to_posix(rel_path)
    if at_commit is None:
        full = repo_root / rel
        return full.is_file() and not full.is_symlink()
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{at_commit}:{rel}"], cwd=repo_root, capture_output=True,
    )
    return result.returncode == 0


def _load_workflow_state_work_items(repo_root: Path, at_commit: str | None) -> tuple[dict, str | None]:
    """The minimal `WORKFLOW_STATE.json` reader `resolve_plan_stage_metadata`
    needs: parse the JSON, return `(work_items, active_work_item_id)`.
    Deliberately does not import or reuse `workflow_state.validate_state`,
    which stays `workflow_state.py`'s own, heavier concern (`OPUS-R25-010`'s
    module-placement correction: this avoids the import cycle
    `workflow_state.py` already has on this module)."""
    data = json.loads(_read_bytes_at_source(repo_root, "docs/ai-workflow/WORKFLOW_STATE.json", at_commit))
    return data.get("work_items", {}), data.get("active_work_item_id")


def load_plan_revision(
    repo_root: Path,
    registry_path: Path,
    plan_path: Path,
    *,
    at_commit: str | None = None,
) -> int:
    """Read `plan_revision` from the tracked, protected registry JSON —
    already machine-written and already part of the hashed projection —
    rather than accepting it as a caller-supplied literal that can
    silently drift from the document it identifies (`OPUS-R18-003`).
    Cross-checked against the plan document's own declared
    `(Revision N)` title so a registry/document disagreement fails
    bundle generation instead of binding an approval to the wrong
    revision. `registry_path`/`plan_path` are relative to `repo_root` and
    required -- no default (`OPUS-R28-011`), since a default here is
    exactly one work item's own literal. `at_commit`, if given, reads both
    files from that commit instead of the live working tree."""
    registry_rel = _to_posix(registry_path)
    plan_rel = _to_posix(plan_path)
    registry = json.loads(_read_bytes_at_source(repo_root, registry_rel, at_commit))
    if "plan_revision" not in registry:
        raise PlanRevisionMismatchError(
            f"{registry_rel} has no 'plan_revision' field"
        )
    revision = registry["plan_revision"]
    title_line = _read_bytes_at_source(repo_root, plan_rel, at_commit).decode().splitlines()[0]
    match = PLAN_TITLE_REVISION_RE.search(title_line)
    if match is None:
        raise PlanRevisionMismatchError(
            f"{plan_rel} title has no '(Revision N)' marker: {title_line!r}"
        )
    if int(match.group(1)) != revision:
        raise PlanRevisionMismatchError(
            f"registry {registry_rel} declares plan_revision={revision!r}, "
            f"but {plan_rel} title declares Revision {match.group(1)}"
        )
    return revision


# ---------------------------------------------------------------------------
# Path grammar for a declared plan_path/registry_path/mapping_path
# (`OPUS-R25-008`): repo-relative, POSIX separators, no "."/".." component,
# no symlink, exists as a tracked regular file at the resolved source.
# Closes the traversal/aliasing gap a bare `repo_root / registry_path` join
# would otherwise leave open.
# ---------------------------------------------------------------------------


def _validate_repo_relative_path_grammar(field_name: str, value: str) -> None:
    """Pure string-level path-grammar check shared by every declared
    repo-relative path in this design (`GPT-R32-001`): repo-relative (no
    leading `/`), POSIX separators only, no empty/`.`/`..` path component.
    Touches no filesystem -- callers needing the exists/symlink/tracked
    layers on top of this add those themselves, since what "safely
    resolved" means past the string grammar differs by caller (a live
    working-tree file vs. a specific historical commit's tree)."""
    if value.startswith("/"):
        raise InvalidPlanStageMetadataPathError(f"{field_name} {value!r} is not repo-relative")
    if "\\" in value:
        raise InvalidPlanStageMetadataPathError(f"{field_name} {value!r} must use POSIX separators")
    parts = value.split("/")
    if any(part in (".", "..", "") for part in parts):
        raise InvalidPlanStageMetadataPathError(
            f"{field_name} {value!r} must contain no '.'/'..' path component"
        )


def _validate_repo_relative_file(repo_root: Path, field_name: str, value: str) -> Path:
    """Grammar plus filesystem-safety validation for a repo-relative path
    resolved against the live working tree: applies
    `_validate_repo_relative_path_grammar`, then rejects a symlink at
    *any* path component (not only the final one) or a path that is not
    an existing regular file, and returns the resolved `Path` so callers
    don't re-join `repo_root / value` themselves. Does not check
    git-tracking -- see `_validate_plan_stage_metadata_path` for the
    stricter, tracked-metadata variant plan-stage callers need; this one
    is also the safe resolver `workflow_state.validate_state`'s
    whole-state registry check reuses (`GPT-R32-001`) instead of a second,
    weaker `repo_root / registry_path` join.

    `Path.is_symlink()` on the final joined path only ever inspects that
    path's own last component -- an intermediate symlinked directory
    (e.g. `registry -> /outside/somewhere` with `value ==
    "registry/item.json"`) joins to a real, non-symlink regular file and
    passed silently before this fix (`GPT-R33-001`). Every component is
    now checked as the path is built up incrementally, and the fully
    resolved path is additionally proven to remain inside the canonical
    (symlink-resolved) repository root, so a component that is itself
    reachable only by following an already-rejected symlink can never
    launder its way back in through `Path.resolve()`."""
    _validate_repo_relative_path_grammar(field_name, value)
    canonical_root = repo_root.resolve()
    current = repo_root
    for part in value.split("/"):
        current = current / part
        if current.is_symlink():
            raise InvalidPlanStageMetadataPathError(f"{field_name} {value!r} names a symlink")
    full = repo_root / value
    if not full.is_file():
        raise InvalidPlanStageMetadataPathError(
            f"{field_name} {value!r} does not exist as a regular file"
        )
    resolved = full.resolve()
    if resolved != canonical_root and canonical_root not in resolved.parents:
        raise InvalidPlanStageMetadataPathError(
            f"{field_name} {value!r} resolves outside the repository root"
        )
    return full


def _validate_plan_stage_metadata_path(
    repo_root: Path, field_name: str, value: str, at_commit: str | None,
) -> None:
    if at_commit is None:
        _validate_repo_relative_file(repo_root, field_name, value)
        tracked = subprocess.run(
            ["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", value],
            cwd=repo_root, capture_output=True, env=literal_pathspec_env(),
        )
        if tracked.returncode != 0:
            raise InvalidPlanStageMetadataPathError(f"{field_name} {value!r} is not a tracked path")
    else:
        _validate_repo_relative_path_grammar(field_name, value)
        out = subprocess.run(
            ["git", "--literal-pathspecs", "ls-tree", at_commit, "--", value],
            cwd=repo_root, check=True, capture_output=True, text=True, env=literal_pathspec_env(),
        ).stdout.strip()
        if not out:
            raise InvalidPlanStageMetadataPathError(
                f"{field_name} {value!r} does not exist as a tracked regular file at {at_commit}"
            )
        meta, _, _found_path = out.partition("\t")
        mode, obj_type, _blob = meta.split(" ")
        if obj_type != "blob" or mode == "120000":
            raise InvalidPlanStageMetadataPathError(
                f"{field_name} {value!r} is not a regular file at {at_commit}"
            )


def artifacts_path_for_work_item(work_item_id: str) -> Path:
    """Pure string templating, no I/O — shared by both the plan-stage
    resolver and the implementation-stage `approval_review_content_id`
    branch (`OPUS-R27-002`) — collision between two different
    `work_item_id`s is structurally impossible since `work_items` map keys
    are already unique."""
    validate_work_item_id(work_item_id)
    return Path(f"docs/ai-workflow/registry/{work_item_id}-artifacts.json")


def load_plan_stage_classification(
    repo_root: Path, artifacts_path: Path, *, at_commit: str | None = None,
) -> tuple[frozenset[str], Mapping[str, str], Mapping[str, str]]:
    """Load the plan-stage protected/excluded path and prefix sets from
    `<work_item_id>-artifacts.json`'s `plan_stage` key -- the migrated,
    generalized counterpart of the frozen `PLAN_STAGE_PROTECTED`/
    `PLAN_STAGE_EXCLUDED_PATHS`/`PLAN_STAGE_EXCLUDED_PREFIXES` module
    constants, which remain only as a named migration-comparison fixture
    (missing-test item 141). A file present but missing its `plan_stage`
    key (a pre-migration, schema-version-1 file) fails the same way as a
    wholly absent file — condition 11's stated boundary, not a distinct
    mechanism."""
    rel = _to_posix(artifacts_path)
    if not _path_exists_at_source(repo_root, rel, at_commit):
        raise MissingWorkItemArtifactsDeclarationError(rel)
    data = json.loads(_read_bytes_at_source(repo_root, rel, at_commit))
    plan_stage = data.get("plan_stage")
    if plan_stage is None:
        raise MissingWorkItemArtifactsDeclarationError(f"{rel} has no 'plan_stage' key")
    protected = frozenset(plan_stage.get("protected_paths", []))
    excluded_paths = MappingProxyType(dict(plan_stage.get("excluded_paths", {})))
    excluded_prefixes = MappingProxyType(dict(plan_stage.get("excluded_prefixes", {})))
    _validate_exclusion_prefixes(excluded_prefixes)
    return protected, excluded_paths, excluded_prefixes


class PlanStageMetadata(NamedTuple):
    """Named, immutable result of `resolve_plan_stage_metadata` — never a
    positional tuple (`GPT-R29-001`): an added or reordered field is a
    loud attribute-access error at every call site instead of a silent
    positional misread."""

    work_item_id: str
    work_item_type: str
    plan_path: str
    registry_path: str
    mapping_path: str
    base_commit: str
    plan_revision: int
    protected_paths: frozenset[str]
    excluded_paths: Mapping[str, str]
    excluded_prefixes: Mapping[str, str]


def resolve_plan_stage_metadata(
    repo_root: Path, work_item_id: str, *, at_commit: str | None = None,
) -> PlanStageMetadata:
    """`D-Fingerprint-Generalization`'s thirteen-step resolution algorithm:
    turns a bare `work_item_id` into every fact needed to fingerprint its
    own plan-stage content, sourced entirely from `WORKFLOW_STATE.json`'s
    `work_items[work_item_id]` entry and its own declared
    `<work_item_id>-artifacts.json`, never from a literal naming any other
    work item. Fails closed on every one of the thirteen matrix
    conditions this design names (1-9, 11 raised directly here; 12-13 are
    the manifest-write path's own concern, `write_manifest_with_verified_identifiers`)."""
    validate_work_item_id(work_item_id)
    work_items, _active = _load_workflow_state_work_items(repo_root, at_commit)
    entry = work_items.get(work_item_id)
    if entry is None:
        raise UnknownWorkItemError(work_item_id)
    if entry.get("work_item_id") != work_item_id:
        raise UnknownWorkItemError(
            f"work_items[{work_item_id!r}].work_item_id == {entry.get('work_item_id')!r}"
        )
    work_item_type = entry.get("work_item_type")
    if not isinstance(work_item_type, str) or work_item_type not in WORK_ITEM_TYPES:
        raise PlanStageNotApplicableError(
            f"{work_item_id!r} has work_item_type {work_item_type!r}, "
            f"not one of {sorted(WORK_ITEM_TYPES)!r}"
        )

    declared = {
        "plan_path": entry.get("plan_path"),
        "registry_path": entry.get("registry_path"),
        "mapping_path": entry.get("mapping_path"),
        "base_commit": entry.get("base_commit"),
    }
    for field_name, value in declared.items():
        if value is None:
            raise MissingPlanStageMetadataError(f"{work_item_id}.{field_name} is null")
    plan_path = declared["plan_path"]
    registry_path = declared["registry_path"]
    mapping_path = declared["mapping_path"]
    base_commit = declared["base_commit"]

    for field_name, value in (
        ("plan_path", plan_path), ("registry_path", registry_path), ("mapping_path", mapping_path),
    ):
        _validate_plan_stage_metadata_path(repo_root, field_name, value, at_commit)

    artifacts_path = artifacts_path_for_work_item(work_item_id)
    artifacts_rel = _to_posix(artifacts_path)
    if not _path_exists_at_source(repo_root, artifacts_rel, at_commit):
        raise MissingWorkItemArtifactsDeclarationError(artifacts_rel)

    registry_data = json.loads(_read_bytes_at_source(repo_root, registry_path, at_commit))
    if registry_data.get("work_item_id") != work_item_id:
        raise RegistryWorkItemIdMismatchError(
            f"expected {work_item_id!r}, found {registry_data.get('work_item_id')!r}"
        )
    mapping_data = json.loads(_read_bytes_at_source(repo_root, mapping_path, at_commit))
    if mapping_data.get("work_item_id") != work_item_id:
        raise MappingWorkItemIdMismatchError(
            f"expected {work_item_id!r}, found {mapping_data.get('work_item_id')!r}"
        )
    artifacts_data = json.loads(_read_bytes_at_source(repo_root, artifacts_rel, at_commit))
    if artifacts_data.get("work_item_id") != work_item_id:
        raise ArtifactsWorkItemIdMismatchError(
            f"expected {work_item_id!r}, found {artifacts_data.get('work_item_id')!r}"
        )

    for other_id, other in work_items.items():
        if other_id == work_item_id:
            continue
        for field_name, value in (
            ("plan_path", plan_path), ("registry_path", registry_path), ("mapping_path", mapping_path),
        ):
            if other.get(field_name) is not None and other.get(field_name) == value:
                raise DuplicateWorkItemArtifactPathError(
                    f"{field_name} {value!r} is claimed by both {work_item_id!r} and {other_id!r}"
                )

    protected_paths, excluded_paths, excluded_prefixes = load_plan_stage_classification(
        repo_root, artifacts_path, at_commit=at_commit,
    )

    for field_name, value in (
        ("plan_path", plan_path), ("registry_path", registry_path), ("mapping_path", mapping_path),
    ):
        if value not in protected_paths:
            raise PlanStageMetadataNotProtectedError(
                f"{work_item_id}.{field_name} ({value!r}) is not a member of its own "
                f"resolved plan-stage protected-path set"
            )
    if len({plan_path, registry_path, mapping_path}) != 3:
        raise PlanStageMetadataNotProtectedError(
            f"{work_item_id}'s plan_path/registry_path/mapping_path must be pairwise "
            f"distinct: {plan_path!r}, {registry_path!r}, {mapping_path!r}"
        )

    plan_revision = load_plan_revision(
        repo_root, Path(registry_path), Path(plan_path), at_commit=at_commit,
    )

    return PlanStageMetadata(
        work_item_id=work_item_id,
        work_item_type=work_item_type,
        plan_path=plan_path,
        registry_path=registry_path,
        mapping_path=mapping_path,
        base_commit=base_commit,
        plan_revision=plan_revision,
        protected_paths=protected_paths,
        excluded_paths=excluded_paths,
        excluded_prefixes=excluded_prefixes,
    )


class PlanApprovalCommitPlan(NamedTuple):
    """Named, immutable result of `resolve_plan_stage_approval_commit_paths`
    — the exact `git add`/commit member set plus the pinned identity of
    the conditional artifacts-declaration member, if any.
    `artifacts_declaration_path`/`artifacts_declaration_sha256` are both
    `None` together (no declaration member) or both set together — never
    one without the other, so a caller can branch on either field
    interchangeably.

    workflow-2.6.0 (`D-Plan-Approval-Closure`): `paths` is every member,
    removals included. `protected_paths` is the declared
    `plan_stage.protected_paths` subset of it, and `removal_paths` the
    subset staged as deletions. Both default to empty so a caller
    constructing a plan by hand keeps working."""

    paths: tuple[str, ...]
    artifacts_declaration_path: str | None
    artifacts_declaration_sha256: str | None
    protected_paths: tuple[str, ...] = ()
    removal_paths: tuple[str, ...] = ()


def resolve_plan_stage_approval_commit_paths(
    repo_root: Path, work_item_id: str, state_path: Path,
) -> PlanApprovalCommitPlan:
    """`D-Approval-Commits`' "Conditional fifth commit member" contract
    (`GPT-R67-001`, `WORKFLOW_V2_PLAN.md` revision 50), generalized to
    every `"process"` or `"product"` work item's own plan-stage approval
    commit — never a literal naming any specific work item (missing-test item 347's
    "permanent `/approve-review`" obligation). Resolves the *complete*
    member set a plan-stage approval commit must contain, so a caller can
    check it — and refuse cleanly — **before its first durable mutation**
    (`/approve-review` step 4a), rather than let a missing or stale
    declaration surface for the first time at post-commit verification.

    The base four members are this work item's own declared
    `plan_path`/`registry_path`/`mapping_path` (`resolve_plan_stage_metadata`,
    the same resolver step (1)'s `bundle_id`/`review_content_id`
    recomputation already uses — never re-derived independently) plus
    `state_path` (`docs/ai-workflow/WORKFLOW_STATE.json`, the approval
    record itself). `<work_item_id>-artifacts.json` joins them as a fifth
    member exactly when both of the following hold, checked in order —
    otherwise the base four-member set is returned unchanged, which is
    also the answer for a work item whose declaration is not itself
    pending (the ordinary case on every round after its first):

    1. **Pending-change condition**: the declaration's current
       working-tree bytes differ from its content at `HEAD` (including
       "absent at `HEAD`" as a difference) — an unchanged declaration is
       never added, so an ordinary approval round never gains a
       gratuitous fifth member.
    2. **Freshness condition**: the declaration's current working-tree
       bytes are byte-identical to the copy the current bundle already
       captured (`<bundle_dir>/files/<path>` — the same "final copies of
       changed files" `scripts/prepare-ai-review.sh` always writes,
       excluded-path or not). A mismatch raises
       `StaleArtifactsDeclarationError` naming both paths — committing
       bytes the reviewer never saw is exactly what this condition exists
       to prevent — rather than silently staging or silently dropping the
       member.

    Callers needing this work item's own bundle-captured copy to exist at
    all (i.e. every "2.1" or `"1"` `"process"` or `"product"` work item
    plan-stage approval) already got a hard failure earlier, at step (1)'s worktree-
    source `resolve_plan_stage_metadata` call, if the declaration is
    missing from the working tree entirely — `MissingWorkItemArtifactsDeclarationError`,
    unchanged by this function, which never re-raises it: by the time this
    function runs, the working-tree file is already known to exist.

    **workflow-2.6.0, `D-Plan-Approval-Closure` (section 5.4 item 1).** The
    member set is no longer fixed at four or five. It is:

    - every declared `plan_stage.protected_paths` entry of the worktree
      declaration the approved identity was computed from (the plan
      document, registry and mapping always among them, and first, in that
      order -- `resolve_plan_stage_metadata` refuses a declaration that
      does not protect them -- then any further protected path, sorted);
    - `state_path`;
    - `<work_item_id>-artifacts.json`, under the conditional rule above,
      unchanged;
    - **removals**: every path protected under `HEAD`'s committed
      declaration and tracked at `HEAD` that is absent from both the
      current declaration and the worktree. A rename is therefore a
      removal plus an addition. A path the current declaration no longer
      protects but that is still present in the worktree is not a member.
      With no declaration at `HEAD` (a first approval) the removal set is
      empty by definition.

    This function only resolves. The per-member freshness check against
    the bound bundle (section 5.4 item 2) is
    `workflow_state.resolve_fresh_plan_approval_members`, which calls it."""
    metadata = resolve_plan_stage_metadata(repo_root, work_item_id)
    state_rel = _to_posix(state_path)
    ordered_protected = (metadata.plan_path, metadata.registry_path, metadata.mapping_path) + tuple(
        sorted(metadata.protected_paths - {metadata.plan_path, metadata.registry_path, metadata.mapping_path})
    )

    artifacts_rel = _to_posix(artifacts_path_for_work_item(work_item_id))
    removal_paths = _plan_approval_removal_paths(
        repo_root, artifacts_rel, metadata.protected_paths,
    )

    def _plan(declaration_member: str | None, declaration_sha256: str | None) -> PlanApprovalCommitPlan:
        members: list[str] = []
        for member in ordered_protected + (state_rel,) + (
            (declaration_member,) if declaration_member else ()
        ) + removal_paths:
            if member not in members:
                members.append(member)
        return PlanApprovalCommitPlan(
            tuple(members), declaration_member, declaration_sha256,
            protected_paths=ordered_protected, removal_paths=removal_paths,
        )

    worktree_bytes = (repo_root / artifacts_rel).read_bytes()
    head_bytes = (
        _read_bytes_at_source(repo_root, artifacts_rel, "HEAD")
        if _path_exists_at_source(repo_root, artifacts_rel, "HEAD")
        else None
    )
    if head_bytes == worktree_bytes:
        # Condition 1 fails: unchanged since HEAD, no declaration member.
        return _plan(None, None)

    bundle_dir = resolve_bundle_dir(repo_root, work_item_id, stage="plan")
    captured_path = repo_root / bundle_dir / "files" / artifacts_rel
    captured_bytes = captured_path.read_bytes() if captured_path.is_file() else None
    if captured_bytes != worktree_bytes:
        raise StaleArtifactsDeclarationError(
            f"{artifacts_rel} is pending (differs from HEAD) but its working-tree "
            f"bytes do not match the copy the current bundle captured at "
            f"{captured_path} — regenerate the bundle before approving, or "
            f"revert the declaration to what the bundle/reviewer actually saw"
        )
    return _plan(artifacts_rel, hashlib.sha256(worktree_bytes).hexdigest())


def _plan_approval_removal_paths(
    repo_root: Path, artifacts_rel: str, current_protected: frozenset[str],
) -> tuple[str, ...]:
    """`D-Plan-Approval-Closure`'s removal members, sorted: protected under
    `HEAD`'s committed declaration, tracked at `HEAD`, and absent from both
    `current_protected` and the worktree (`os.path.lexists`, so a dangling
    symlink still counts as present). No declaration at `HEAD`, or one with
    no `plan_stage` key, protects nothing, so the set is empty."""
    if not _path_exists_at_source(repo_root, artifacts_rel, "HEAD"):
        return ()
    head_declaration = json.loads(_read_bytes_at_source(repo_root, artifacts_rel, "HEAD"))
    if not isinstance(head_declaration, dict) or head_declaration.get("plan_stage") is None:
        return ()
    head_protected, _, _ = load_plan_stage_classification(
        repo_root, Path(artifacts_rel), at_commit="HEAD",
    )
    removals = []
    for path in sorted(head_protected - current_protected):
        if os.path.lexists(repo_root / path):
            continue
        if _snapshot_commit(repo_root, "HEAD", path)["exists"]:
            removals.append(path)
    return tuple(removals)


# ---------------------------------------------------------------------------
# Path classification — resolves PROTO-R7-001, OPUS-R8-006/007/008
# ---------------------------------------------------------------------------

# Explicit, exhaustive protected path set for the plan stage. Frozen so no
# caller can corrupt the shared default by mutating it (OPUS-R8-015).
PLAN_STAGE_PROTECTED: frozenset[str] = frozenset({
    "docs/ai-workflow/WORKFLOW_V2_PLAN.md",
    "docs/ai-workflow/WORKFLOW_V2_AUDIT.md",
    # A technical-decision entry created specifically to satisfy a review
    # finding is durable design content the plan-stage reviewer is binding,
    # not incidental housekeeping -- resolves OPUS-R8-008 bonus-answer 5.
    "docs/TECHNICAL_DECISIONS.md",
    # The plan declares these two JSON files normative (D-Registry, D4b);
    # they must be part of what the reviewer approves, not generated for
    # the first time inside a later approval command -- resolves
    # GPT-R9-003.
    "docs/ai-workflow/registry/workflow-v2-1-core-registry.json",
    "docs/ai-workflow/requirements/workflow-v2-1-core-mapping.json",
})

# Shared justification for the closed set of top-level product
# artifacts named below (OPUS-R18-004): D-Fingerprint's rule 2 covers
# every path a concurrent work item may write, not only the four
# OPUS-R16-001 happened to name, and the same sanctioned scenario
# (Milestone 8 adoption while this item is mid-implementation)
# continues one step further into FIXING_FUNCTIONAL_FINDINGS's writes to
# product code, product tests, and product documentation. Stated once
# here rather than repeated per entry, per the finding's own acceptance
# criterion.
PRODUCT_SCOPE_JUSTIFICATION = (
    "product/repository content outside this process work item's "
    "declared scope -- concurrent work items (product milestones, "
    "functional-finding fixes, other tooling changes) may write here; "
    "fail-closed is preserved for any path outside this named set "
    "(resolves OPUS-R18-004)"
)

# Exact-path exclusions, each with a mandatory one-line justification
# (OPUS-R8-008). Exact match only -- never a prefix -- so a suffixed
# sibling (e.g. "docs/TECHNICAL_DECISIONS.md.orig") fails closed instead of
# silently inheriting the exclusion (OPUS-R8-006).
PLAN_STAGE_EXCLUDED_PATHS: Mapping[str, str] = MappingProxyType({
    ".gitignore":
        "repository housekeeping, not design content",
    ".github/workflows/ci.yml":
        "operational CI wiring for the prototype's test suite -- the design "
        "decision it implements (stdlib-only Python, tested every PR) is "
        "recorded in the protected docs/TECHNICAL_DECISIONS.md; the workflow "
        "file itself also carries unrelated Android build/lint/test steps "
        "that must not stale this milestone's approval",
    "docs/ai-workflow/WORKFLOW_STATE.json":
        "future runtime-mutable per-work-item state (WF1a) -- excluded so "
        "ordinary phase/progress writes never stale a plan approval",
    "docs/ai-workflow/WORKFLOW_CONFIG.json":
        "future runtime-mutable repository-level config (WF1a/WF-Activate) "
        "-- same reasoning as WORKFLOW_STATE.json",
    "CLAUDE.md":
        "WF4a-ii's gate-count depointer is a small mechanical edit that "
        "implements the design, not part of the design being reviewed here",
    "docs/ai-workflow/REVIEW_PROTOCOL.md":
        "WF5's bundle-mechanics updates implement D-Bundle-Manifest, not "
        "design content themselves",
    "docs/ai-workflow/MILESTONE_WORKFLOW.md":
        "WF4a-ii applies the already-reviewed D-States blocks verbatim; the "
        "design is reviewed here, the application of it is not",
    "docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md":
        "the concise two-stage plan-review operator guide documents the "
        "already-reviewed design for operators (D-Plan-Review-Stages), it "
        "is not itself the design -- same reasoning as MILESTONE_WORKFLOW.md/"
        "REVIEW_PROTOCOL.md above. Moved here from PLAN_STAGE_PROTECTED "
        "(resolves OPUS-R14-001/-009): pre-declaring it protected while "
        "unwritten made D-States' 'checkpoints never touch protected "
        "documents' invariance false the moment WF4a-iv actually created "
        "it, since that creation is itself a checkpoint touching a "
        "protected path. Excluding it (like the other operator-facing docs "
        "above) means WF4a-iv can write it without staling the plan "
        "approval, and a plan-stage bundle never needs to tombstone a "
        "not-yet-written protected path in the first place",
    "docs/ACTIVE_MILESTONE.md":
        "product-milestone narrative, explicitly out of scope for this "
        "process work item (CLAUDE.md's own routing) -- excluded, not "
        "unmentioned, so a concurrent product work item's write to it (e.g. "
        "Milestone 8 adoption/functional-review-checklist writes, D-Legacy "
        "phase 2) never blocks this work item's own classification, since "
        "active_work_item_id is a resume-focus pointer, not an execution "
        "lock (D1), and concurrent activity is the normal case (resolves "
        "OPUS-R16-001)",
    "docs/ROADMAP.md":
        "product milestone ordering, same reasoning and same concurrent-"
        "work-item exposure as docs/ACTIVE_MILESTONE.md above (resolves "
        "OPUS-R16-001)",
    # Top-level product docs a concurrent product work item may write
    # (e.g. a functional-finding fix updating domain/UX documentation) --
    # see PRODUCT_SCOPE_JUSTIFICATION above and the matching prefixes in
    # PLAN_STAGE_EXCLUDED_PREFIXES below (resolves OPUS-R18-004).
    "AGENTS.md": PRODUCT_SCOPE_JUSTIFICATION,
    "README.md": PRODUCT_SCOPE_JUSTIFICATION,
    "docs/PROJECT_BRIEF.md": PRODUCT_SCOPE_JUSTIFICATION,
    "docs/DOMAIN_GLOSSARY.md": PRODUCT_SCOPE_JUSTIFICATION,
    "docs/UX_FLOWS.md": PRODUCT_SCOPE_JUSTIFICATION,
})

# Directory-prefix exclusions, each required to end in "/" (validated at
# import below) and each with a justification. Populated this round
# (resolves OPUS-R10-001, which found the previous empty set meant plan-
# approval durability became permanently unevaluable the moment WF0's own
# commit created a single implementation-phase file): every path this
# milestone's own checkpoints are known to create or modify past the plan
# stage is covered here, so classification stays exhaustive across the
# entire registry order, not just against today's working tree. The
# protected JSON files under `docs/ai-workflow/registry/` and
# `docs/ai-workflow/requirements/` are checked first, by exact path, in
# `classify_path` -- a prefix here only ever catches siblings, never
# overrides an exact protected match. **Future work** (WF1b, tied to the
# same generator that emits the registry/mapping JSON): derive this list
# from the registry's own per-checkpoint artifact declarations instead of
# hand-maintaining it, so the two cannot drift.
PLAN_STAGE_EXCLUDED_PREFIXES: Mapping[str, str] = MappingProxyType({
    ".claude/commands/":
        "workflow command files this milestone creates (the bootstrap "
        "command, /approve-review, /accept-milestone) or modifies (the "
        "eight existing v1 commands, per D-States/D-Self-Governance) -- "
        "implementation of the approved design, not the design itself",
    "scripts/":
        "this milestone's own tooling scripts, present (the fingerprint "
        "prototype, reviewed as bundle evidence) and future (WF1a's state "
        "validator and any other workflow script) -- same reasoning as the "
        "prototype's own exclusion, generalized now that this milestone "
        "will create more of them",
    "docs/ai-workflow/requirements/":
        "the mutable execution ledger (WF4b) and any other non-immutable "
        "requirements artifact -- the immutable mapping file itself is "
        "separately protected by exact path, checked first",
    "docs/ai-workflow/registry/":
        "any future non-immutable registry artifact -- the immutable "
        "registry file itself is separately protected by exact path, "
        "checked first",
    "docs/milestones/":
        "product milestone plan/execution artifacts (e.g. "
        "docs/milestones/completed/, written by /accept-milestone for any "
        "product work item, D-Legacy phase 2 included) -- explicitly out "
        "of scope for this process work item, same concurrent-work-item "
        "reasoning as docs/ACTIVE_MILESTONE.md/docs/ROADMAP.md above "
        "(resolves OPUS-R16-001)",
    "docs/ai-workflow/archive/":
        "this work item's own completed-content archival (D1's "
        "process-completion archival), written at or after this work "
        "item's own MILESTONE_COMPLETE -- excluded, not protected, so that "
        "future write is never itself a plan-approval-staling event "
        "(resolves OPUS-R16-001, and closes the loop OPUS-R16-002 opens on "
        "retiming it)",
    "docs/ai-workflow/dry-run/":
        "WF8b's own dry-run scenario checklist/evidence and the isolated "
        "synthetic v2-1-dry-run (and v2-1-dry-run-legacy) work items' entire "
        "plan/registry/mapping artifact tree -- operational execution "
        "evidence for a separate, throwaway work item, not design content "
        "for workflow-v2-1-core's own plan, so a write here must never "
        "stale this work item's plan approval",
    # The closed set of top-level product directories a concurrent
    # product work item may write -- app code, its own tests, ADRs,
    # stale agent-context docs, product-improvement audits, and
    # build/lint config -- per PRODUCT_SCOPE_JUSTIFICATION above
    # (resolves OPUS-R18-004; docs/improvements/ added post-round-18
    # after Milestone 8's FUNCTIONAL_FEATURE_AUDIT.md landed on this
    # branch as a genuinely novel concurrent-write path). A path under
    # any *other* directory not named here, or not named in
    # PLAN_STAGE_EXCLUDED_PATHS, still fails closed via
    # UnclassifiedPathError -- this widens the named set, it does not
    # relax the fail-closed default.
    "app/": PRODUCT_SCOPE_JUSTIFICATION,
    "docs/adr/": PRODUCT_SCOPE_JUSTIFICATION,
    "docs/agent-context/": PRODUCT_SCOPE_JUSTIFICATION,
    "docs/improvements/": PRODUCT_SCOPE_JUSTIFICATION,
    "gradle/": PRODUCT_SCOPE_JUSTIFICATION,
    "config/": PRODUCT_SCOPE_JUSTIFICATION,
    ".github/": PRODUCT_SCOPE_JUSTIFICATION,
})


def _validate_exclusion_prefixes(prefixes: Mapping[str, str]) -> None:
    for prefix in prefixes:
        if not prefix.endswith("/"):
            raise MalformedExclusionConstantError(prefix)


_validate_exclusion_prefixes(PLAN_STAGE_EXCLUDED_PREFIXES)


# Release-derived, exact-path terminal fallback (workflow-2.6.0,
# `D-Tooling-Ambient-Classification`, closes the legacy-item half of
# `v2.4.0-001`). `workflow_manager` writes `.workflow-manager/installation.json`
# into every managed repository on install and on every committed
# `update`; artifact declarations authored before that path was declared
# (every `2.3.1`-shaped item, and the implementation stage of every
# `2.4.0`-shaped item) never classify it, so the first committed update made
# every gate reaching a classifier raise `UnclassifiedPathError`. Both
# classifiers consult this set **only after every declared classification
# has failed, immediately before the raise**, so:
#   - it never overrides a declared protected or excluded entry;
#   - it never enters any hashed classification set (the projections hash
#     the declared key strings, never this constant), so every digest
#     `2.5.1` could compute is byte-identical, and a digest that used to
#     raise only because of this path now returns;
#   - it persists nothing and applies to every governing version, because
#     classification is not version-dispatched.
# Exact paths only, never a prefix: `.workflow-manager/installation.json.tmp`,
# every other `.workflow-manager/*` path and every other unclassified path
# still fail closed.
TOOLING_AMBIENT_EXCLUDED_PATHS = frozenset({".workflow-manager/installation.json"})


def classify_path(
    path: str,
    protected: frozenset[str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> str:
    if path in protected:
        return "protected"
    if path in excluded_paths:
        return "excluded"
    if any(path.startswith(prefix) for prefix in excluded_prefixes):
        return "excluded"
    if path in TOOLING_AMBIENT_EXCLUDED_PATHS:
        return "excluded"
    raise UnclassifiedPathError(path)


def assert_all_changed_paths_classified_worktree(
    repo_root: Path,
    base_full: str,
    protected: frozenset[str] = PLAN_STAGE_PROTECTED,
    excluded_paths: Mapping[str, str] = PLAN_STAGE_EXCLUDED_PATHS,
    excluded_prefixes: Mapping[str, str] = PLAN_STAGE_EXCLUDED_PREFIXES,
) -> None:
    """Worktree-source classification gate: every changed tracked path and
    every untracked path must be protected or explicitly excluded, or
    computation stops here. Resolves PROTO-R7-001."""
    changed = _changed_tracked_paths(repo_root, base_full) | _untracked_paths(repo_root)
    for path in sorted(changed):
        classify_path(path, protected, excluded_paths, excluded_prefixes)


def assert_all_changed_paths_classified_commit(
    repo_root: Path,
    base_full: str,
    commit: str,
    protected: frozenset[str] = PLAN_STAGE_PROTECTED,
    excluded_paths: Mapping[str, str] = PLAN_STAGE_EXCLUDED_PATHS,
    excluded_prefixes: Mapping[str, str] = PLAN_STAGE_EXCLUDED_PREFIXES,
) -> None:
    """Commit-source counterpart of `assert_all_changed_paths_classified_worktree`,
    scoped to `base..commit` rather than base..worktree. Previously missing
    entirely, which meant an unclassified file present at the approval
    commit was invisible to the one check meant to catch it — resolves
    OPUS-R8-005."""
    changed = _changed_tracked_paths_between(repo_root, base_full, commit)
    for path in sorted(changed):
        classify_path(path, protected, excluded_paths, excluded_prefixes)


# ---------------------------------------------------------------------------
# review_content_id — plan stage, snapshot-based (resolves PROTO-R7-003/007)
# ---------------------------------------------------------------------------


def _snapshot_worktree(repo_root: Path, rel_path: str) -> dict:
    """Final on-disk state of one protected path: exists / mode / blob.
    Symlinks are hashed as Git would store them — the link target string,
    not the referent file's content. Regular-file mode is derived from
    Git's own `core.fileMode` semantics (OPUS-R8-003): the executable bit
    is meaningful only when that config is true, matching exactly what Git
    itself would record on commit, so worktree/commit parity holds
    regardless of the repository's `core.fileMode` setting."""
    abs_path = repo_root / rel_path
    if abs_path.is_symlink():
        target = os.readlink(abs_path)
        return {"exists": True, "mode": "120000", "blob": _hash_blob_bytes(repo_root, target.encode())}
    if not abs_path.exists():
        return {"exists": False, "mode": None, "blob": None}
    if not abs_path.is_file():
        raise UnsupportedPathTypeError(rel_path)
    executable = _core_file_mode_enabled(repo_root) and _owner_executable(abs_path.stat().st_mode)
    mode = "100755" if executable else "100644"
    return {"exists": True, "mode": mode, "blob": _hash_object(repo_root, rel_path)}


def _snapshot_commit(repo_root: Path, commit: str, rel_path: str) -> dict:
    """Final state of one protected path at a commit, via `git ls-tree` —
    a direct snapshot read, not a diff, so it needs no abbreviation flag
    and no status interpretation. `--literal-pathspecs`: a path such as
    `:x` names that file, never pathspec magic for `x` (implementation
    review round 2, `I1`); the output's own (possibly quoted) path field
    is never read."""
    out = subprocess.run(
        ["git", "--literal-pathspecs", "ls-tree", commit, "--", rel_path],
        cwd=repo_root, check=True, capture_output=True, text=True, env=literal_pathspec_env(),
    ).stdout.strip()
    if not out:
        return {"exists": False, "mode": None, "blob": None}
    meta, _, _path = out.partition("\t")
    mode, obj_type, blob = meta.split(" ")
    if obj_type != "blob":
        raise UnsupportedPathTypeError(rel_path)
    return {"exists": True, "mode": mode, "blob": blob}


def compute_review_content_manifest_plan_stage_worktree(
    repo_root: Path, protected: frozenset[str] = PLAN_STAGE_PROTECTED
) -> list[dict]:
    """Fails closed on an absent protected path (resolves
    OPUS-R14-001/-006) -- see `AbsentProtectedPathError`."""
    manifest = []
    for path in sorted(protected):
        entry = _snapshot_worktree(repo_root, path)
        if not entry["exists"]:
            raise AbsentProtectedPathError(path)
        manifest.append({"path": path, **entry})
    return manifest


def compute_review_content_manifest_plan_stage_commit(
    repo_root: Path, commit: str, protected: frozenset[str] = PLAN_STAGE_PROTECTED
) -> list[dict]:
    """Commit-source counterpart; same fail-closed rule (resolves
    OPUS-R14-001/-006) -- a protected path absent at the reviewed commit is
    exactly as invalid as one absent from the worktree."""
    manifest = []
    for path in sorted(protected):
        entry = _snapshot_commit(repo_root, commit, path)
        if not entry["exists"]:
            raise AbsentProtectedPathError(path)
        manifest.append({"path": path, **entry})
    return manifest


def compute_review_content_id_plan_stage(
    repo_root: Path,
    base: str,
    work_item_type: str,
    work_item_id: str,
    plan_revision: int,
    protected: frozenset[str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> tuple[str, dict]:
    """No identity-bearing scalar has a default (OPUS-R8-010): the caller
    must supply `work_item_type`/`work_item_id`/`plan_revision` explicitly.
    `work_item_id` is validated against the slug grammar. The protected and
    exclusion sets are themselves part of the hashed projection
    (OPUS-R8-014), so editing either changes `review_content_id` even when
    no file content changes -- and, for the same reason, `GPT-R30-005`
    retired their former `PLAN_STAGE_*` defaults: a generic caller
    omitting them used to silently compute `workflow-v2-1-core`'s own
    identity instead of failing loudly. Every production entry point
    (`compute_review_content_id_plan_stage_for_work_item` and its
    commit-source counterpart) already resolves and passes its own
    work item's sets explicitly, so this is a no-op for real callers;
    fixtures that genuinely want `workflow-v2-1-core`'s own sets pass
    `PLAN_STAGE_PROTECTED`/`PLAN_STAGE_EXCLUDED_PATHS`/`PLAN_STAGE_EXCLUDED_PREFIXES`
    explicitly (or, in the hermetic unit suite, via `ScratchRepo.compute()`'s
    own explicitly-scoped defaults). `work_item_type` is validated against
    the controlled vocabulary (GPT-R9-014)."""
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    base_full = resolve_base(repo_root, base)
    assert_all_changed_paths_classified_worktree(
        repo_root, base_full, protected, excluded_paths, excluded_prefixes
    )
    manifest = compute_review_content_manifest_plan_stage_worktree(repo_root, protected)
    projection = {
        "stage": "plan",
        "work_item_type": work_item_type,
        "work_item_id": work_item_id,
        "plan_revision": plan_revision,
        "base_commit": base_full,
        "reviewed_implementation_head": None,
        "review_content_manifest": manifest,
        "protected_paths": sorted(protected),
        "excluded_paths": sorted(excluded_paths),
        "excluded_prefixes": sorted(excluded_prefixes),
    }
    digest = hashlib.sha256(_canonical_json(projection)).hexdigest()
    return digest, projection


def compute_review_content_id_plan_stage_at_commit(
    repo_root: Path,
    base: str,
    commit: str,
    work_item_type: str,
    work_item_id: str,
    plan_revision: int,
    protected: frozenset[str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> tuple[str, dict]:
    """The commit-source counterpart of `compute_review_content_id_plan_stage`,
    used for post-approval-commit parity verification: same projection
    shape, snapshot read from a commit instead of the working tree, and
    (OPUS-R8-005) the same fail-closed classification precondition, scoped
    to `base..commit`. Same no-default discipline as the worktree-source
    function above (`GPT-R30-005`).

    workflow-2.6.0 (`D-Plan-Approval-Closure` item 3): `commit` may be any
    tree-ish, not only a commit -- every read here (`git ls-tree`, `git
    diff <base> <tree-ish>`, `git show <tree-ish>:<path>`) accepts a bare
    tree object, which is how `/approve-review plan` proves the staged
    index (`git write-tree`) before any commit exists. Only `base` must
    resolve to a commit."""
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    base_full = resolve_base(repo_root, base)
    assert_all_changed_paths_classified_commit(
        repo_root, base_full, commit, protected, excluded_paths, excluded_prefixes
    )
    manifest = compute_review_content_manifest_plan_stage_commit(repo_root, commit, protected)
    projection = {
        "stage": "plan",
        "work_item_type": work_item_type,
        "work_item_id": work_item_id,
        "plan_revision": plan_revision,
        "base_commit": base_full,
        "reviewed_implementation_head": None,
        "review_content_manifest": manifest,
        "protected_paths": sorted(protected),
        "excluded_paths": sorted(excluded_paths),
        "excluded_prefixes": sorted(excluded_prefixes),
    }
    digest = hashlib.sha256(_canonical_json(projection)).hexdigest()
    return digest, projection


def compute_review_content_id_plan_stage_for_work_item(
    repo_root: Path, work_item_id: str, *, base: str | None = None,
) -> tuple[str, dict]:
    """Work-item-generic entry point (`D-Fingerprint-Generalization`):
    resolves `work_item_id`'s own plan-stage metadata exactly once, then
    computes its `review_content_id` from the resolved values -- never a
    hardcoded literal naming any other work item. `base`, if given,
    overrides the resolved item's own declared `base_commit`; omitted, it
    is read from the one `resolve_plan_stage_metadata` call already made
    here, never a second, independent read of `WORKFLOW_STATE.json`."""
    metadata = resolve_plan_stage_metadata(repo_root, work_item_id)
    base_to_use = base if base is not None else metadata.base_commit
    return compute_review_content_id_plan_stage(
        repo_root, base_to_use, metadata.work_item_type, metadata.work_item_id,
        metadata.plan_revision, metadata.protected_paths,
        metadata.excluded_paths, metadata.excluded_prefixes,
    )


def compute_review_content_id_plan_stage_at_commit_for_work_item(
    repo_root: Path, work_item_id: str, commit: str, *, base: str | None = None,
) -> tuple[str, dict]:
    """Commit-source counterpart of
    `compute_review_content_id_plan_stage_for_work_item`: resolves
    `work_item_id`'s metadata *as declared at `commit`*, never from the
    live working tree (missing-test item 145)."""
    metadata = resolve_plan_stage_metadata(repo_root, work_item_id, at_commit=commit)
    base_to_use = base if base is not None else metadata.base_commit
    return compute_review_content_id_plan_stage_at_commit(
        repo_root, base_to_use, commit, metadata.work_item_type, metadata.work_item_id,
        metadata.plan_revision, metadata.protected_paths,
        metadata.excluded_paths, metadata.excluded_prefixes,
    )


# ---------------------------------------------------------------------------
# review_content_id — implementation stage (WF4a-i's own scope, deferred
# from the plan-stage-only prototype: "there is no real changed-file diff
# to validate a commit-source diff algorithm against" no longer holds now
# that WF0/WF1a/WF1b have actually landed). Built as an independent
# mechanism, not adapted from the plan-stage constants/functions above,
# per D-Fingerprint's explicit instruction that the two stages' sets are
# near-inverses of each other (resolves OPUS-R20-003, missing-test item
# 140): app/, scripts/, .claude/commands/, gradle/, config/ are *excluded*
# at plan stage (not approval-critical before implementation exists) and
# *protected* at implementation stage (exactly the "source, test, build,
# migration, workflow-command file" content D-Commit-Provenance names as
# what technical_approval binds to).
# ---------------------------------------------------------------------------

DEFAULT_ARTIFACTS_PATH = Path("docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json")


def load_implementation_stage_classification(
    repo_root: Path, artifacts_path: Path,
) -> tuple[Mapping[str, str], Mapping[str, str], Mapping[str, str], Mapping[str, str]]:
    """Load the implementation-stage protected/excluded path and prefix
    sets from a tracked, machine-readable artifact-declarations file,
    rather than a second hardcoded Python constant adapted from
    `PLAN_STAGE_*` by inspection. This is the generalization both
    `D-Registry`'s and `D-Fingerprint`'s future-work notes name as
    `WF4a-i`'s own scope (`GPT-R9-011`, `OPUS-R20-003`): the classification
    is derived from a validated, tracked declaration, not re-typed as a
    Python literal every time it needs to change.

    The declarations file lives under `docs/ai-workflow/registry/`, which
    is already a `PLAN_STAGE_EXCLUDED_PREFIXES` entry ("any future
    non-immutable registry artifact -- the immutable registry file itself
    is separately protected by exact path, checked first"), so the file's
    own bytes never enter either stage's content projection.

    **That does not mean editing it is identity-neutral** (salvage audit
    `I9`, which reproduced the opposite against this repository's own
    history). Both stages hash their classification *sets* alongside the
    protected content, and both sets are read out of this file:

    - editing `implementation_stage` changes
      `compute_review_content_id_implementation_stage*`'s digest and so
      stales an existing `technical_approval`; it leaves the plan-stage
      digest alone;
    - editing `plan_stage.protected_paths`/`excluded_paths`/
      `excluded_prefixes` changes
      `compute_review_content_id_plan_stage*`'s digest and so stales an
      existing `plan_approval`; it leaves the implementation-stage digest
      alone.

    Both are the intended cryptographic contract, not a defect: a
    classification change is a change to what was reviewed. A declaration
    repair made after an approval must therefore be carried back through
    that stage's own review/approval gate, exactly like any other change
    to reviewed content -- see `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
    "Repairing an artifact declaration after an approval".

    Returns `(protected_paths, protected_prefixes, excluded_paths,
    excluded_prefixes)`, each a path/prefix -> category-or-justification
    mapping, in the same shape `classify_path_implementation_stage`
    consumes. Every prefix key must end in `/` (validated, same rule as
    `PLAN_STAGE_EXCLUDED_PREFIXES`).

    Reads from the file's `implementation_stage` sub-key (schema_version 2,
    `D-Fingerprint-Generalization` migration step 2/3) -- the counterpart
    of `load_plan_stage_classification`'s `plan_stage` sub-key. A
    pre-migration (schema_version 1, flat) file has no `implementation_stage`
    key and fails closed the same way a wholly absent file would, per
    condition 11's stated pre-migration boundary. A wholly absent file
    (`GPT-R30-003`/`-004`, "wrong artifacts declarations") raises the
    same `MissingWorkItemArtifactsDeclarationError` its plan-stage
    sibling (`load_plan_stage_classification`) already does for the
    equivalent absent-file case -- never a raw, undocumented
    `FileNotFoundError` leaking past this function's own contract.

    **`artifacts_path` has no default** (salvage audit `I6`, the same
    `GPT-R30-005` reasoning that retired `compute_review_content_id_plan_stage`'s
    own `PLAN_STAGE_*` defaults, and the same treatment
    `workflow_state.approval_review_content_id` already applies to its own
    `artifacts_path` per `OPUS-R27-002`): it used to default to
    `DEFAULT_ARTIFACTS_PATH`, `workflow-v2-1-core`'s own file, so a
    generic caller that omitted it silently computed a *different* work
    item's classification instead of failing loudly. That was not
    hypothetical -- `workflow_state.promote_legacy_work_item` carried the
    same default through into its own signature, and a legacy *product*
    item adopted without an explicit path had its `technical_approval`
    freshness evaluated against a *process* item's sets, in which `app/`
    is `excluded`: a real product commit landing after the reviewed
    content read as "not stale" and adoption proceeded. Every caller now
    names the work item whose classification it means, via
    `artifacts_path_for_work_item(work_item_id)`."""
    full = repo_root / artifacts_path
    if not full.is_file():
        raise MissingWorkItemArtifactsDeclarationError(_to_posix(artifacts_path))
    data = json.loads(full.read_text())
    implementation_stage = data.get("implementation_stage")
    if implementation_stage is None:
        raise MissingWorkItemArtifactsDeclarationError(
            f"{full} has no 'implementation_stage' key"
        )
    protected_paths = MappingProxyType(dict(implementation_stage.get("protected_paths", {})))
    protected_prefixes = MappingProxyType(dict(implementation_stage.get("protected_prefixes", {})))
    excluded_paths = MappingProxyType(dict(implementation_stage.get("excluded_paths", {})))
    excluded_prefixes = MappingProxyType(dict(implementation_stage.get("excluded_prefixes", {})))
    _validate_exclusion_prefixes(protected_prefixes)
    _validate_exclusion_prefixes(excluded_prefixes)
    return protected_paths, protected_prefixes, excluded_paths, excluded_prefixes


def classify_path_implementation_stage(
    path: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> str:
    """Implementation-stage counterpart of `classify_path`, built as an
    independent function rather than a shared code path -- the frozen
    plan-stage `classify_path` (round 10's freeze) is never modified to
    grow a "protected prefixes" concept it was never designed to need,
    and this function is never used to derive the plan-stage sets or vice
    versa (resolves `OPUS-R20-003`). "Protected" here means "the kind of
    content `technical_approval` binds to" (source, test, build,
    migration, workflow-command files), not "the reviewer read these
    exact bytes" the way plan-stage protection means. Fails closed via
    `UnclassifiedPathError` exactly like the plan-stage classifier, after
    the same terminal `TOOLING_AMBIENT_EXCLUDED_PATHS` fallback
    (workflow-2.6.0, `D-Tooling-Ambient-Classification`)."""
    if path in protected_paths:
        return "protected"
    if any(path.startswith(prefix) for prefix in protected_prefixes):
        return "protected"
    if path in excluded_paths:
        return "excluded"
    if any(path.startswith(prefix) for prefix in excluded_prefixes):
        return "excluded"
    if path in TOOLING_AMBIENT_EXCLUDED_PATHS:
        return "excluded"
    raise UnclassifiedPathError(path)


def _implementation_stage_protected_changed_paths(
    repo_root: Path,
    base_full: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> list[str]:
    """Every changed/untracked path must classify (fail-closed, mirroring
    `assert_all_changed_paths_classified_worktree`); the subset that
    classifies `protected` is what the implementation-stage manifest
    snapshots. Unlike the plan-stage manifest builders, an absent
    protected path is a real, representable deletion here, not an error
    (`AbsentProtectedPathError` is a plan-stage-only rule, per
    `OPUS-R14-006`'s own scoping note) -- so this function does not raise
    on absence, only on a path neither set names at all."""
    changed = sorted(_changed_tracked_paths(repo_root, base_full) | _untracked_paths(repo_root))
    protected = []
    for path in changed:
        classification = classify_path_implementation_stage(
            path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
        )
        if classification == "protected":
            protected.append(path)
    return protected


def compute_review_content_manifest_implementation_stage_worktree(
    repo_root: Path,
    base_full: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> list[dict]:
    """Snapshots every changed/untracked path classifying as
    implementation-stage `protected`, from the working tree. A deletion
    (path no longer present) is represented as a real tombstone entry
    (`{"exists": False, ...}`, via `_snapshot_worktree`, already
    stage-agnostic), never raised as an error -- the opposite rule from
    the plan-stage manifest builders (`OPUS-R14-006` scoped
    `AbsentProtectedPathError` to plan stage only, precisely because a
    deletion is a legitimate implementation-stage change)."""
    protected = _implementation_stage_protected_changed_paths(
        repo_root, base_full, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
    )
    return [{"path": path, **_snapshot_worktree(repo_root, path)} for path in protected]


def compute_review_content_manifest_implementation_stage_commit(
    repo_root: Path,
    base_full: str,
    commit: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> list[dict]:
    """Commit-source counterpart, scoped to `base_full..commit` rather
    than base..worktree, mirroring the plan-stage worktree/commit pair."""
    changed = sorted(_changed_tracked_paths_between(repo_root, base_full, commit))
    protected = []
    for path in changed:
        classification = classify_path_implementation_stage(
            path, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
        )
        if classification == "protected":
            protected.append(path)
    return [{"path": path, **_snapshot_commit(repo_root, commit, path)} for path in protected]


def compute_review_content_id_implementation_stage(
    repo_root: Path,
    base: str,
    work_item_type: str,
    work_item_id: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> tuple[str, dict]:
    """The implementation-stage counterpart of
    `compute_review_content_id_plan_stage`: hashes the current working
    tree's protected (source/test/build/migration/workflow-command)
    content, scoped to what changed since `base`, rather than a fixed
    small set of design documents. `reviewed_implementation_head` is
    always `None` in this projection, exactly like the plan-stage
    function's own projection -- it is never derived from live `HEAD` and
    hashed here, deliberately: `HEAD == reviewed_implementation_head` is a
    separate, exact-equality freshness gate `/approve-review implementation`
    checks against the field `WORKFLOW_STATE.json` stores
    (D-Approval-Commits, whose sole writer is the bundle generator, a
    later concern), not part of *this* content identity. Folding live HEAD
    into the hash here would make `review_content_id` change on every new
    commit regardless of content -- exactly the concurrent-excluded-write
    durability property `TestWidenedConcurrentWriteClosure` proves at the
    plan stage would break at the implementation stage too. No
    identity-bearing scalar defaults, matching the plan-stage function's
    own discipline (`OPUS-R8-010`)."""
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    base_full = resolve_base(repo_root, base)
    manifest = compute_review_content_manifest_implementation_stage_worktree(
        repo_root, base_full, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
    )
    projection = {
        "stage": "implementation",
        "work_item_type": work_item_type,
        "work_item_id": work_item_id,
        "base_commit": base_full,
        "reviewed_implementation_head": None,
        "review_content_manifest": manifest,
        "protected_paths": sorted(protected_paths),
        "protected_prefixes": sorted(protected_prefixes),
        "excluded_paths": sorted(excluded_paths),
        "excluded_prefixes": sorted(excluded_prefixes),
    }
    digest = hashlib.sha256(_canonical_json(projection)).hexdigest()
    return digest, projection


def compute_review_content_id_implementation_stage_at_commit(
    repo_root: Path,
    base: str,
    commit: str,
    work_item_type: str,
    work_item_id: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> tuple[str, dict]:
    """Commit-source counterpart, used for post-technical-approval parity
    verification, mirroring `compute_review_content_id_plan_stage_at_commit`.
    Scoped to `base..commit` rather than base..worktree; `commit` selects
    *which snapshot* to hash, but -- exactly like the worktree-source
    function above -- is not itself folded into the hashed projection, so
    two different commits with byte-identical protected content still
    produce the same `review_content_id` (the property a post-approval
    parity check actually needs)."""
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    base_full = resolve_base(repo_root, base)
    commit_full = resolve_base(repo_root, commit)
    manifest = compute_review_content_manifest_implementation_stage_commit(
        repo_root, base_full, commit_full, protected_paths, protected_prefixes, excluded_paths, excluded_prefixes
    )
    projection = {
        "stage": "implementation",
        "work_item_type": work_item_type,
        "work_item_id": work_item_id,
        "base_commit": base_full,
        "reviewed_implementation_head": None,
        "review_content_manifest": manifest,
        "protected_paths": sorted(protected_paths),
        "protected_prefixes": sorted(protected_prefixes),
        "excluded_paths": sorted(excluded_paths),
        "excluded_prefixes": sorted(excluded_prefixes),
    }
    digest = hashlib.sha256(_canonical_json(projection)).hexdigest()
    return digest, projection


# ---------------------------------------------------------------------------
# Bundle layout — WF5's `.ai-review/<work_item_id>/{current,feedback}/`
# relayout, with a stated compatibility fallback (resolves `OPUS-R6-021`).
# `.claude/commands/*.md` and `docs/ai-workflow/REVIEW_PROTOCOL.md` state
# this same rule in prose; this is the one real, testable implementation of
# it, rather than leaving path resolution to an executing agent's own
# reading of that prose. Once a work item's own `.ai-review/<id>/` layout
# exists, it is authoritative; the flat legacy layout is read only for a
# work item that has never had the new layout created yet -- this
# milestone's own in-flight bundle during WF5's own landing, in practice.
#
# The compatibility fallback is correct only where the flat layout is a
# *reachable* generation target. It is, for the three stages whose
# `work-item-id` argument to `prepare-ai-review.sh` is optional
# (`implementation`, `post-fix`, `functional-review`): omitting it is a
# documented, supported invocation that really does write
# `.ai-review/current/`, so a work item with no scoped layout yet may
# genuinely be on the flat one. It is *not* reachable for the plan stage,
# whose `work-item-id` argument is required (`D-Fingerprint-
# Generalization`, `OPUS-R27-003`) -- `prepare-ai-review.sh`'s own
# `ROOT_DIR` and `write_manifest_with_verified_identifiers_for_work_item`
# both template `.ai-review/<work_item_id>/` directly there, and
# `REVIEW_PROTOCOL.md` states the same rule ("For the plan stage
# specifically, the bundle directory is never the flat fallback").
# ---------------------------------------------------------------------------

GENERATION_STAGES: frozenset[str] = frozenset(
    {"plan", "implementation", "post-fix", "functional-review"}
)

#: The stages whose bundle directory is per-work-item scoped by
#: construction rather than by existence -- today just the plan stage, the
#: one stage `prepare-ai-review.sh` requires a `work-item-id` for.
SCOPED_BY_CONSTRUCTION_STAGES: frozenset[str] = frozenset({"plan"})


def resolve_bundle_dir(repo_root: Path, work_item_id: str, *, stage: str | None = None) -> Path:
    """The current bundle directory for `work_item_id`, repo-root-relative.

    For a stage whose bundle directory is scoped by construction (today
    `stage="plan"`), this is always `.ai-review/<work_item_id>/current`,
    with no existence gate at all: that stage's generator has no flat
    branch to fall back to, so a gated answer would send the *authoring*
    half of `/milestone-plan` to `.ai-review/current/` on a work item's
    very first plan bundle while the generator wrote and validated the
    scoped directory -- the first-generation split this argument closes.

    For every other stage, and for a caller that names no stage at all,
    the compatibility rule still applies: the scoped layout once this work
    item is on it, else the flat compatibility path `.ai-review/current`.
    Those stages' `work-item-id` argument to `prepare-ai-review.sh` is
    optional, so the flat layout remains a reachable generation target for
    them and cannot simply be dropped.

    "Is on the scoped layout" is decided from the work item's **own root
    directory** (`.ai-review/<work_item_id>/`), never from the transient
    existence of the `current/` inside it. `withdraw_bundle` renames
    `current/` to a `current.rejected-<token>/` sibling, so gating on
    `current/` flipped an item that had demonstrably been generating
    scoped bundles back onto the flat path for its next round -- while
    `prepare-ai-review.sh`, given the same `[work-item-id]` argument the
    round before, still wrote `.ai-review/<work_item_id>/current/`. That
    is the plan stage's own first-generation split reappearing at the
    implementation/post-fix stages after any withdrawal, and it withdrew
    the regenerated bundle too (the author-written
    `IMPLEMENTATION_SUMMARY.md` landing at the flat path, leaving
    `assert_stage_completeness` reading the generator's own empty stub).
    A work item that has never had a scoped directory created still
    resolves flat, exactly as before, so nothing about the genuinely
    flat-generated legacy layout changes.

    An unrecognized `stage` is refused (`InvalidBundleStageError`) rather
    than treated as "no stage": silently taking the compatibility branch
    on a typo would reintroduce exactly the defect this argument closes.
    """
    validate_work_item_id(work_item_id)
    if stage is not None and stage not in GENERATION_STAGES:
        raise InvalidBundleStageError(
            f"unknown bundle stage {stage!r} -- expected one of {sorted(GENERATION_STAGES)} or None"
        )
    scoped_root = Path(".ai-review") / work_item_id
    scoped = scoped_root / "current"
    if stage in SCOPED_BY_CONSTRUCTION_STAGES:
        return scoped
    if (repo_root / scoped_root).is_dir():
        return scoped
    return Path(".ai-review/current")


# D-Feedback-Layout (workflow-2.6.0, CP3): the durable per-work-item stamp
# `route_work_item`'s fresh-id branch and `create_remediation_child_work_item`
# write at creation, and the three layouts `resolve_feedback_layout` reports.
FEEDBACK_LAYOUT_SCOPED = "scoped"
FEEDBACK_LAYOUT_VALUES = frozenset({FEEDBACK_LAYOUT_SCOPED})
FEEDBACK_LAYOUT_LEGACY_SCOPED = "legacy-scoped"
FEEDBACK_LAYOUT_LEGACY_FLAT = "legacy-flat"
FLAT_FEEDBACK_DIR = Path(".ai-review/feedback")

# The phases at which a work item provably has no remaining consumer of its
# feedback file -- the bounded relaxation in
# `assert_feedback_not_owned_by_other_work_item`. Kept equal to
# `workflow_state.TERMINAL_PHASES` (pinned by a test); duplicated here only
# because this module never imports `workflow_state` (the import runs the
# other way).
FEEDBACK_OWNER_TERMINAL_PHASES = frozenset({"MILESTONE_COMPLETE"})


def _load_feedback_layout_work_items(repo_root: Path) -> dict | None:
    """The live worktree's `WORKFLOW_STATE.json` `work_items` map, or `None`
    when no state file exists (a pre-activation repository). Everything
    else that cannot decide a layout refuses with
    `FeedbackLayoutUndecidableError`, never a fallback (INV-3)."""
    state_path = Path(repo_root) / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    if state_path.is_symlink():
        raise FeedbackLayoutUndecidableError(f"{state_path} is a symlink -- refusing to follow it")
    try:
        raw = state_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FeedbackLayoutUndecidableError(f"{state_path} is unreadable: {exc}") from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeedbackLayoutUndecidableError(f"{state_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FeedbackLayoutUndecidableError(f"{state_path}'s top level is not a JSON object")
    work_items = data.get("work_items", {})
    if not isinstance(work_items, dict):
        raise FeedbackLayoutUndecidableError(f"{state_path}'s work_items is not a JSON object")
    return work_items


def resolve_feedback_layout(repo_root: Path, work_item_id: str) -> str:
    """`D-Feedback-Layout`'s one decision: which of the three layouts
    governs `work_item_id`'s feedback directory.

    - `"scoped"`: the item's state entry carries `feedback_layout:
      "scoped"`, stamped at creation by `2.6.0` or later. Resolution is
      `.ai-review/<id>/feedback` by construction -- no existence gate.
    - `"legacy-scoped"` / `"legacy-flat"`: the item's entry lacks the
      field, there is no entry at all, or there is no state file (a
      pre-activation repository or a `"1"`-governed item). The unchanged
      `2.5.1` rule applies: scoped if `.ai-review/<id>/feedback/` already
      exists, else flat -- so an active legacy item keeps finding its
      unconsumed flat file.

    A present but unknown field value raises `UnknownFeedbackLayoutError`;
    a state file that cannot decide raises
    `FeedbackLayoutUndecidableError` (INV-3)."""
    validate_work_item_id(work_item_id)
    work_items = _load_feedback_layout_work_items(repo_root)
    entry = work_items.get(work_item_id) if work_items is not None else None
    if entry is not None:
        if not isinstance(entry, dict):
            raise FeedbackLayoutUndecidableError(
                f"work_items[{work_item_id!r}] is not a JSON object"
            )
        if "feedback_layout" in entry:
            layout = entry["feedback_layout"]
            if not isinstance(layout, str) or layout not in FEEDBACK_LAYOUT_VALUES:
                raise UnknownFeedbackLayoutError(
                    f"work_items[{work_item_id!r}].feedback_layout == {layout!r} -- "
                    f"expected one of {sorted(FEEDBACK_LAYOUT_VALUES)} or absent"
                )
            return layout
    if (Path(repo_root) / ".ai-review" / work_item_id / "feedback").is_dir():
        return FEEDBACK_LAYOUT_LEGACY_SCOPED
    return FEEDBACK_LAYOUT_LEGACY_FLAT


def resolve_feedback_dir(repo_root: Path, work_item_id: str) -> Path:
    """The one authoritative feedback-directory resolver
    (`D-Feedback-Layout`), repo-root-relative. `"scoped"` and
    `"legacy-scoped"` items resolve `.ai-review/<id>/feedback`;
    `"legacy-flat"` items resolve the shared `.ai-review/feedback`. See
    `resolve_feedback_layout` for which items take which layout.

    The feedback directory counterpart of `resolve_bundle_dir` -- kept
    as an independent function (not derived from the bundle dir's parent)
    because a caller may need to resolve the feedback path before any
    bundle has ever been generated in the scoped layout."""
    layout = resolve_feedback_layout(repo_root, work_item_id)
    if layout == FEEDBACK_LAYOUT_LEGACY_FLAT:
        return FLAT_FEEDBACK_DIR
    return Path(".ai-review") / work_item_id / "feedback"


def ensure_feedback_dir(repo_root: Path, work_item_id: str) -> Path:
    """Create `resolve_feedback_dir`'s resolved directory (and its
    parents) and return it, repo-root-relative. Every feedback writer
    calls this before writing: `/review-plan`, both `/review-implementation`
    writers, `mark_functional_review_consumed` and
    `/prepare-functional-review`. It creates only the *resolved* directory,
    so a legacy-flat item is never flipped to the scoped layout by it."""
    feedback_dir = resolve_feedback_dir(repo_root, work_item_id)
    (Path(repo_root) / feedback_dir).mkdir(parents=True, exist_ok=True)
    return feedback_dir


def resolve_feedback_path_contract(repo_root: Path, work_item_id: str) -> dict:
    """The machine-readable feedback-location contract for external
    consumers (Controller or any other tool), printed as one JSON object
    by `workflow_fingerprint.py --resolve-feedback-path <work-item-id>`.
    Built from `resolve_feedback_layout`/`resolve_feedback_dir` alone --
    there is no second implementation. Every path is POSIX,
    repo-root-relative. Read-only: creates nothing."""
    layout = resolve_feedback_layout(repo_root, work_item_id)
    feedback_dir = resolve_feedback_dir(repo_root, work_item_id)
    return {
        "work_item_id": work_item_id,
        "layout": layout,
        "feedback_dir": feedback_dir.as_posix(),
        "review_feedback_path": (feedback_dir / "REVIEW_FEEDBACK.md").as_posix(),
        "functional_review_path": (feedback_dir / "FUNCTIONAL_REVIEW.md").as_posix(),
    }


def resolve_functional_review_consumed_marker_path(repo_root: Path, work_item_id: str) -> Path:
    """O3 (`workflow-v2-3-followups` continued scope, external cross-model
    review round 2): sibling to `resolve_feedback_dir`'s own
    `FUNCTIONAL_REVIEW.md`, repo-root-relative, mirroring
    `resolve_rejected_marker_path`'s own sibling-to-the-artifact-it-
    describes placement. `FUNCTIONAL_REVIEW.md` has no binding fields of
    its own to bind against (`docs/ai-workflow/REVIEW_PROTOCOL.md`: "no
    binding-field requirement, since functional review has no
    `bundle_id`/`review_content_id` of its own") and lives entirely
    outside Git (`.ai-review/` is gitignored), so this marker -- not a
    commit trailer, the mechanism `/prepare-functional-review`'s own
    checklist-evidence binding uses for the Git-tracked
    `docs/ACTIVE_MILESTONE.md` -- is the smallest mechanism consistent
    with both existing conventions at once."""
    return resolve_feedback_dir(repo_root, work_item_id) / "FUNCTIONAL_REVIEW.consumed"


def assert_functional_review_not_already_consumed(repo_root: Path, work_item_id: str) -> None:
    """Refuses if `<feedback_dir>/FUNCTIONAL_REVIEW.md`'s current content
    is byte-identical to what `mark_functional_review_consumed` last
    recorded as applied -- mirroring `assert_bundle_not_rejected`'s own
    presence-then-content read shape. A missing `FUNCTIONAL_REVIEW.md` is
    the caller's own concern (`/apply-functional-review` step 1 already
    stops and says so before this would ever run); a missing or
    non-matching marker means this content has not been recorded as
    applied yet -- proceed normally in both cases, since a marker whose
    hash simply differs (genuinely new findings written since the last
    round) is exactly the case this check must let through."""
    feedback_dir = resolve_feedback_dir(repo_root, work_item_id)
    review_rel = (feedback_dir / "FUNCTIONAL_REVIEW.md").as_posix()
    review_path = Path(repo_root) / review_rel
    marker_path = Path(repo_root) / resolve_functional_review_consumed_marker_path(repo_root, work_item_id)
    try:
        recorded_hash = marker_path.read_text().strip()
    except FileNotFoundError:
        return
    current_hash = _hash_object(repo_root, review_rel)
    if recorded_hash and recorded_hash == current_hash:
        raise FunctionalReviewAlreadyAppliedError(
            f"{marker_path} records this exact content of {review_path} (blob "
            f"{current_hash}) as already applied by a prior /apply-functional-review "
            f"round -- write fresh findings to {review_path} before running it again, "
            f"or if this file is genuinely unprocessed leftover from a stale round, "
            f"remove the marker by hand after confirming that by hand"
        )


def mark_functional_review_consumed(repo_root: Path, work_item_id: str) -> None:
    """Records `<feedback_dir>/FUNCTIONAL_REVIEW.md`'s current content
    hash as applied -- called once `/apply-functional-review` has
    classified and acted on every finding in this round (fixed, deferred
    to a remediation child, or rejected with evidence), immediately
    before whichever of its two exit points this round actually takes
    (the bounded branch's own early stop, or the normal step 7), mirroring
    `mark_identity_reference_gap_consumed`'s own "run once the work the
    marker describes has actually completed" discipline. Idempotent:
    writing the same content's hash twice is a no-op in effect."""
    feedback_dir = ensure_feedback_dir(repo_root, work_item_id)
    review_rel = (feedback_dir / "FUNCTIONAL_REVIEW.md").as_posix()
    content_hash = _hash_object(repo_root, review_rel)
    marker_path = Path(repo_root) / resolve_functional_review_consumed_marker_path(repo_root, work_item_id)
    fd, tmp_name = tempfile.mkstemp(dir=str(marker_path.parent), prefix=".functional-review-consumed-", suffix=".tmp")
    with os.fdopen(fd, "w") as handle:
        handle.write(content_hash + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_name, marker_path)


def _snapshot_directory_file_hashes(directory: Path) -> dict[str, str]:
    """Every regular file under `directory`, keyed by POSIX-relative path,
    mapped to its sha256 -- the pre-move snapshot
    `relocate_flat_bundle_to_scoped_layout`'s post-move verification
    compares the destination against."""
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*")) if p.is_file()
    }


def verify_relocation_file_set_complete(
    source_snapshot: Mapping[str, str], dest_dir: Path,
) -> None:
    """The post-move verification half of migration step 8a
    (`OPUS-R28-004`/`-006`, missing-test item 165), factored out as its
    own function so a partial/interrupted move can be exercised directly
    against a hand-constructed destination, without having to actually
    interrupt a real move mid-flight. `source_snapshot` is a path->sha256
    mapping (`_snapshot_directory_file_hashes`'s own return shape),
    captured *before* the move starts. Raises
    `BundleRelocationPartialMoveError` naming every missing or
    content-mismatched path -- never silently accepting a subset."""
    dest_snapshot = _snapshot_directory_file_hashes(dest_dir) if dest_dir.is_dir() else {}
    missing = sorted(set(source_snapshot) - set(dest_snapshot))
    mismatched = sorted(
        path for path in (set(source_snapshot) & set(dest_snapshot))
        if source_snapshot[path] != dest_snapshot[path]
    )
    if missing or mismatched:
        raise BundleRelocationPartialMoveError(
            f"post-move verification failed for {dest_dir}: "
            f"missing={missing!r}, content_mismatched={mismatched!r}"
        )


def relocate_flat_bundle_to_scoped_layout(repo_root: Path, work_item_id: str) -> None:
    """One-time migration helper (`D-Fingerprint-Generalization` migration
    step 8a, `OPUS-R27-003`/`-005`, missing-test item 165): moves the flat
    `.ai-review/current/` directory and `.ai-review/review-bundle.tar.gz`
    to `.ai-review/<work_item_id>/current/` and
    `.ai-review/<work_item_id>/review-bundle.tar.gz`. Refuses with
    `BundleRelocationDestinationExistsError`, naming both paths, if the
    destination directory already exists and is non-empty, rather than
    nesting the source inside it silently. Snapshots every source file's
    content hash *before* moving anything, then calls
    `verify_relocation_file_set_complete` against the real post-move
    destination -- a move interrupted partway (crash, disk-full, etc.)
    is caught here, diagnosably, before any caller proceeds to rebind a
    manifest to it. Never touches `.ai-review/feedback/` (stage-agnostic,
    stays flat, `OPUS-R28-005`) or any other `.ai-review/` entry
    (`source/`, `runtime/`, another work item's own scoped directory)."""
    validate_work_item_id(work_item_id)
    flat_dir = repo_root / ".ai-review" / "current"
    flat_archive = repo_root / ".ai-review" / "review-bundle.tar.gz"
    dest_root = repo_root / ".ai-review" / work_item_id
    dest_dir = dest_root / "current"
    dest_archive = dest_root / "review-bundle.tar.gz"

    if dest_dir.is_dir() and any(dest_dir.iterdir()):
        raise BundleRelocationDestinationExistsError(
            f"destination {dest_dir} already exists and is non-empty; "
            f"refusing to relocate {flat_dir} into it"
        )
    if not flat_dir.is_dir():
        raise MissingRequiredBundleFileError([str(flat_dir)])

    source_snapshot = _snapshot_directory_file_hashes(flat_dir)

    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(flat_dir), str(dest_dir))
    if flat_archive.is_file():
        shutil.move(str(flat_archive), str(dest_archive))

    verify_relocation_file_set_complete(source_snapshot, dest_dir)


# ---------------------------------------------------------------------------
# `REJECTED`-marker consumer-side assertion (`WFR-67`, `WF8c` item (h), part
# 1) -- the marker itself is work-item-scoped rather than stage-scoped (it
# sits beside the `current/` every stage shares), so one shared resolver and
# one shared assertion serve every consumer and every writer alike, exactly
# as `assert_local_generation_matches` does for worktree/HEAD staleness. The
# generator-side write of this marker -- the ordered, fail-closed quarantine
# withdrawal `prepare-ai-review.sh` performs on a failed generation, and the
# plan-stage pinned-projection binding that makes the withdrawal's trigger
# meaningful -- is built below (`WF8c` item (h), part 2).
# ---------------------------------------------------------------------------


def resolve_rejected_marker_path(repo_root: Path, work_item_id: str) -> Path:
    """The work-item-scoped `REJECTED` marker path, repo-root-relative --
    sibling to `resolve_bundle_dir`'s own `current/` (`.ai-review/
    <work_item_id>/REJECTED`, or the flat `.ai-review/REJECTED` under the
    same compatibility rule `resolve_bundle_dir` itself uses), so the two
    resolvers can never disagree about which layout a work item is on.

    Agreement stays **by derivation** -- this is still literally
    `resolve_bundle_dir`'s own answer's parent, never a second copy of the
    layout rule that could drift from it. What changed is the rule both
    now share: `resolve_bundle_dir` decides a work item's layout from its
    own root directory rather than from the transient `current/` inside
    it, which is what makes this marker path stable through a withdrawal.
    `withdraw_bundle` renames `current/` away and only then removes the
    marker it wrote before the first removal; while the layout was keyed
    on `current/`, that rename moved this resolved path, mid-withdrawal,
    from the scoped location the marker had just been written at to the
    flat one -- so a crash in the window between the successful rename and
    the marker's removal left a scoped marker that every later
    `assert_bundle_not_rejected` resolved past, failing **open** on
    exactly the residue the marker exists to refuse. No transaction
    machinery is involved; the shared resolver simply stopped tracking a
    directory that a withdrawal is defined to remove."""
    return resolve_bundle_dir(repo_root, work_item_id).parent / "REJECTED"


def assert_bundle_not_rejected(repo_root: Path, work_item_id: str) -> None:
    """`WFR-67`'s shared consumer-side assertion: refuse, before the
    caller's own first durable write, read, or report, if `work_item_id`'s
    `REJECTED` marker is present. Presence alone refuses -- an empty,
    truncated, or unreadable marker degrades the diagnostic, it never
    passes as "not rejected" -- and a presence check that cannot complete
    (`EACCES`/`ENOTDIR`/`ELOOP` on the marker's parent directory) is
    treated as present rather than absent, since the obvious fail-open
    reading is wrong for a refusal marker. This uses `os.stat` directly
    rather than `Path.is_file()`: pathlib's own `is_file()`/`is_dir()`
    silently swallow `ENOTDIR`/`ELOOP` (returning `False`, indistinguishable
    from a genuinely absent marker), which would defeat exactly the
    "cannot complete" case this assertion must treat as present. Every
    required consumer (`/review-plan`, `/review-implementation`,
    `/review-functional`, `/record-manual-plan-review`, `/apply-plan-review`,
    `/approve-review` at both stages, `/apply-implementation-review`, the
    hand-off reports of `/milestone-plan`/`/milestone-implement`/
    `/prepare-review`, and `/apply-functional-review`'s bounded-fix branch)
    and every writer immediately preceding a `record_bundle_generation`
    call shares this one function, so the policy cannot drift command by
    command."""
    marker_path = resolve_rejected_marker_path(repo_root, work_item_id)
    full_path = Path(repo_root) / marker_path
    try:
        os.stat(full_path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BundleRejectedError(
            f"{marker_path}'s presence could not be determined ({exc!r}); "
            f"treated as present -- {work_item_id}'s current bundle is "
            f"refused until a fresh, successful generation completes and "
            f"clears it"
        ) from exc
    try:
        detail = full_path.read_text().strip()
    except OSError as exc:
        detail = f"<unreadable: {exc!r}>"
    if not detail:
        detail = "<empty marker>"
    raise BundleRejectedError(
        f"{work_item_id}'s current bundle is REJECTED ({marker_path}: "
        f"{detail}) -- refusing before this operation's first durable "
        f"write, read, or report; a fresh, successful generation is "
        f"required before this work item's bundle is reviewable, "
        f"ingestible, or approvable again"
    )


# ---------------------------------------------------------------------------
# Generator-side stage-document binding (`WFR-67`, `WF8c` item (h), part 2):
# `bundle_dir/PLAN.md` is *derived*, not authored, from a private pinned
# snapshot of every plan-stage protected path -- read exactly once, at
# derivation, so `PLAN.md`, its `files/` copy, and `review_content_id`
# (computed from that same pin) can never independently disagree about which
# bytes they describe (`OPUS-R89-001` through `OPUS-R92-003`). A failed
# closing binding assertion withdraws the bundle rather than leaving a
# stale-but-self-verifying `current/` and archive behind (part 3b,
# `OPUS-R90-001`/`OPUS-R91-004`/`OPUS-R93-003`/`OPUS-R94-001`).
# ---------------------------------------------------------------------------


def _pin_dir_for_work_item(repo_root: Path, work_item_id: str) -> Path:
    """The private pinned-snapshot directory a plan-stage generation
    captures its protected paths into -- gitignored (`.ai-review/`),
    scoped per work item, never itself part of any bundle or fingerprint
    input."""
    return repo_root / ".ai-review" / work_item_id / ".pin"


def capture_plan_stage_pin(
    repo_root: Path, work_item_id: str, metadata: PlanStageMetadata, *, pin_dir: Path | None = None,
) -> Path:
    """Reads every one of `metadata.protected_paths` exactly once, from
    the live worktree, into a private snapshot directory -- so a
    plan-stage generation's derived `PLAN.md`, its `files/` copies of any
    protected path, and its `review_content_id` all source the identical
    bytes, never three independently-timed worktree reads (`OPUS-R91-001`:
    pinning `plan_path` alone left four of the five protected inputs in
    the same window). Fails closed, before writing anything, if a
    protected path -- most importantly `plan_path` -- is absent, a
    symlink, or not a regular file (this doubles as the derivation's own
    fail-closed precondition, part 2). Replaces any prior pin atomically
    (`os.replace` onto the final name), so an interrupted capture never
    leaves a partial pin looking current. Returns the pin directory.

    `pin_dir` (workflow-2.6.0, `D-Plan-Review-Bundle-Binding` item 5)
    redirects the capture to a staging generation's own
    `.pin.staging-<token>/`, which only a successful finalization renames
    onto `.pin`; `None` keeps the in-place `.pin`."""
    if pin_dir is None:
        pin_dir = _pin_dir_for_work_item(repo_root, work_item_id)
    tmp_pin_dir = pin_dir.with_name(pin_dir.name + ".tmp")
    if tmp_pin_dir.exists():
        shutil.rmtree(tmp_pin_dir)
    tmp_pin_dir.mkdir(parents=True)
    for rel_path in sorted(metadata.protected_paths):
        abs_path = repo_root / rel_path
        if abs_path.is_symlink() or not abs_path.is_file():
            shutil.rmtree(tmp_pin_dir, ignore_errors=True)
            raise AbsentProtectedPathError(rel_path)
        dest = tmp_pin_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_path, dest)
    if pin_dir.exists():
        shutil.rmtree(pin_dir)
    os.replace(tmp_pin_dir, pin_dir)
    return pin_dir


def derive_plan_stage_document(pin_dir: Path, bundle_dir: Path, metadata: PlanStageMetadata) -> None:
    """Part 1 of the generator-side stage-document binding:
    `bundle_dir/PLAN.md` is derived unconditionally from the pinned
    `plan_path` snapshot -- overwritten whether or not a file is already
    there, leaving the create-if-missing stub list every other
    author-written plan-stage file remains on. Must run after
    `capture_plan_stage_pin` for the same generation, and before any
    other file under `bundle_dir` is written (part 2's fail-closed
    ordering places this at `prepare-ai-review.sh`'s round-identity
    preflight position; the pre-existing `mkdir -p` remains the sole
    exception). Also runs `assert_stage_completeness` immediately after
    derivation (part 3), giving that check its production call site."""
    pinned_plan_bytes = (pin_dir / metadata.plan_path).read_bytes()
    (bundle_dir / "PLAN.md").write_bytes(pinned_plan_bytes)
    assert_stage_completeness(bundle_dir, "plan", plan_revision=metadata.plan_revision)


def refresh_files_copy_from_pin(pin_dir: Path, bundle_dir: Path, metadata: PlanStageMetadata) -> None:
    """After `prepare-ai-review.sh`'s generic diff-based `files/` copy
    loop runs, overwrite any protected path's copy under `bundle_dir/files/`
    with the pinned bytes it was captured with earlier in this same
    generation -- closing `OPUS-R91-001`'s "four of the five inputs
    stayed in exactly the window" gap for the four protected paths
    `PLAN.md`'s own derivation does not otherwise touch. A protected path
    absent from `files/` (not part of this round's diff) is left absent;
    this never creates a `files/` entry the diff-based copy did not."""
    for rel_path in sorted(metadata.protected_paths):
        files_copy = bundle_dir / "files" / rel_path
        if files_copy.is_file():
            files_copy.write_bytes((pin_dir / rel_path).read_bytes())


def _snapshot_pin(repo_root: Path, pin_dir: Path, rel_path: str) -> dict:
    """Like `_snapshot_worktree`, but reads from the private pinned
    snapshot directory `capture_plan_stage_pin` captured at one earlier
    moment in this generation run, instead of the live worktree -- the
    read `compute_review_content_id_plan_stage_from_pin` uses so
    `review_content_id` is provably computed from the same bytes
    `PLAN.md` was derived from, not a second, independently-timed read."""
    abs_path = pin_dir / rel_path
    if not abs_path.is_file():
        return {"exists": False, "mode": None, "blob": None}
    executable = _core_file_mode_enabled(repo_root) and _owner_executable(abs_path.stat().st_mode)
    mode = "100755" if executable else "100644"
    data = abs_path.read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return {"exists": True, "mode": mode, "blob": blob}


def compute_review_content_manifest_plan_stage_from_pin(
    repo_root: Path, pin_dir: Path, protected: frozenset[str] = PLAN_STAGE_PROTECTED,
) -> list[dict]:
    """Pin-sourced counterpart of
    `compute_review_content_manifest_plan_stage_worktree` -- same
    fail-closed-on-absence rule, sourced from the pin instead of the live
    worktree."""
    manifest = []
    for path in sorted(protected):
        entry = _snapshot_pin(repo_root, pin_dir, path)
        if not entry["exists"]:
            raise AbsentProtectedPathError(path)
        manifest.append({"path": path, **entry})
    return manifest


def compute_review_content_id_plan_stage_from_pin(
    repo_root: Path,
    pin_dir: Path,
    base: str,
    work_item_type: str,
    work_item_id: str,
    plan_revision: int,
    protected: frozenset[str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
) -> tuple[str, dict]:
    """Pin-sourced counterpart of `compute_review_content_id_plan_stage`:
    identical projection shape, manifest read from the private pinned
    snapshot a generation captured earlier in the same run instead of a
    fresh, independently-timed worktree read -- the fix `OPUS-R91-001`
    requires (all five protected paths read exactly once, at derivation,
    into a single pinned projection, with `review_content_id` computed
    from that projection rather than a later independent snapshot).
    Deliberately skips `assert_all_changed_paths_classified_worktree`:
    the caller (`write_manifest_with_verified_identifiers`) always
    follows this with a fresh, live-worktree recompute via
    `compute_review_content_id_plan_stage` for its own idempotence check,
    which already performs that classification precondition -- and whose
    disagreement with this pin-sourced digest is itself the staleness
    signal part 3's closing re-read exists to catch."""
    validate_work_item_id(work_item_id)
    validate_work_item_type(work_item_type)
    base_full = resolve_base(repo_root, base)
    manifest = compute_review_content_manifest_plan_stage_from_pin(repo_root, pin_dir, protected)
    projection = {
        "stage": "plan",
        "work_item_type": work_item_type,
        "work_item_id": work_item_id,
        "plan_revision": plan_revision,
        "base_commit": base_full,
        "reviewed_implementation_head": None,
        "review_content_manifest": manifest,
        "protected_paths": sorted(protected),
        "excluded_paths": sorted(excluded_paths),
        "excluded_prefixes": sorted(excluded_prefixes),
    }
    digest = hashlib.sha256(_canonical_json(projection)).hexdigest()
    return digest, projection


def assert_plan_stage_document_matches_pin(
    pin_dir: Path, bundle_dir: Path, metadata: PlanStageMetadata, archive_extracted_root: Path,
) -> None:
    """Part 3 of the generator-side stage-document binding -- the closing
    reproducibility check's fourth assertion: `bundle_dir/PLAN.md`,
    `bundle_dir/files/<plan_path>` (when present -- absence alone is not
    a failure, since a plan-only round need not touch every protected
    path), and the archive's own extracted `current/PLAN.md` must each be
    byte-identical to the pinned `plan_path` snapshot this generation
    derived `PLAN.md` from. This is the binding itself: without it,
    `review_content_id` (computed from the pin) and the document a
    reviewer actually reads (`PLAN.md`) could silently diverge whenever
    something touches `plan_path` between derivation and this check."""
    pinned_bytes = (pin_dir / metadata.plan_path).read_bytes()
    mismatches = []

    plan_md = bundle_dir / "PLAN.md"
    if not plan_md.is_file() or plan_md.read_bytes() != pinned_bytes:
        mismatches.append(str(plan_md))

    files_copy = bundle_dir / "files" / metadata.plan_path
    if files_copy.is_file() and files_copy.read_bytes() != pinned_bytes:
        mismatches.append(str(files_copy))

    archived_plan_md = archive_extracted_root / "PLAN.md"
    if not archived_plan_md.is_file() or archived_plan_md.read_bytes() != pinned_bytes:
        mismatches.append(str(archived_plan_md))

    if mismatches:
        raise PlanStageDocumentStaleError(
            f"bundle PLAN.md is not byte-identical to the pinned "
            f"plan_path ({metadata.plan_path!r}) snapshot this generation "
            f"derived it from: {mismatches}"
        )


def withdraw_bundle(repo_root: Path, work_item_id: str, reason: str) -> Path:
    """Part 3b of the generator-side stage-document binding: a failed
    closing binding assertion must leave no review-ready artifact.
    Writes the `REJECTED` marker *before* the first removal, naming the
    withdrawal about to be performed -- unreachable failure handlers
    (`SIGKILL`, a closed terminal) cannot un-write history, but a marker
    written first is visible at every instant of the sequence, not only
    the ones a handler reaches. Removes the archive, then `MANIFEST.md`
    -- in that order, because the archive is the complete,
    self-contained, self-verifying artifact that actually leaves the
    machine, so it is the more dangerous of the two to leave behind if a
    crash lands between the two removals. Then quarantines `current/` by
    renaming it to a sibling `current.rejected-<token>/`, never deleting
    it. On any step that cannot complete, updates the marker naming the
    failed step and the surviving path, then raises
    `BundleWithdrawalError` -- a partial withdrawal is never silent. Only
    a withdrawal that completes every step removes its own marker: once
    `current/` is renamed away, nothing named `current/` remains for the
    marker to protect against, and `resolve_bundle_dir` sees an ordinary
    absent bundle. Returns the quarantine directory."""
    bundle_dir = repo_root / resolve_bundle_dir(repo_root, work_item_id)
    root_dir = bundle_dir.parent
    marker_path = repo_root / resolve_rejected_marker_path(repo_root, work_item_id)
    archive_path = root_dir / "review-bundle.tar.gz"
    manifest_path = bundle_dir / MANIFEST_FILENAME
    quarantine_path = root_dir / f"current.rejected-{uuid.uuid4().hex}"

    def _mark(text: str) -> None:
        marker_path.write_text(text)

    def _fail(step: str, exc: Exception, surviving: str) -> None:
        _mark(
            f"REJECTED: withdrawal FAILED at step {step!r}\n"
            f"reason: {reason}\nerror: {exc!r}\nsurviving: {surviving}\n"
        )
        raise BundleWithdrawalError(f"withdrawal failed at step {step!r}: {exc!r}") from exc

    _mark(f"REJECTED: withdrawal in progress\nreason: {reason}\nstep: starting\n")

    if archive_path.exists():
        try:
            archive_path.unlink()
        except OSError as exc:
            _fail("remove archive", exc, f"{archive_path}, {manifest_path}, {bundle_dir}")

    _mark(f"REJECTED: withdrawal in progress\nreason: {reason}\nstep: archive removed\n")

    if manifest_path.exists():
        try:
            manifest_path.unlink()
        except OSError as exc:
            _fail("remove MANIFEST.md", exc, f"{manifest_path}, {bundle_dir}")

    _mark(f"REJECTED: withdrawal in progress\nreason: {reason}\nstep: MANIFEST.md removed\n")

    try:
        os.rename(bundle_dir, quarantine_path)
    except OSError as exc:
        _fail("quarantine current/", exc, str(bundle_dir))

    marker_path.unlink()
    return quarantine_path


def clear_rejected_marker_if_present(repo_root: Path, work_item_id: str) -> None:
    """Work-item-scoped, not stage-scoped: any generation, at any stage,
    that has itself completed and verified its own end state supersedes
    what a pre-existing `REJECTED` marker names, because it has rewritten
    the `MANIFEST.md` the marked residue's own self-verification depended
    on. Called only from a generation's own successful end, after its
    closing checks pass and its own `MANIFEST.md` is written -- never
    before."""
    marker_path = repo_root / resolve_rejected_marker_path(repo_root, work_item_id)
    if marker_path.exists():
        marker_path.unlink()


def _closing_bundle_generation_check(
    repo_root: Path, bundle_dir: Path, archive_path: Path, stage: str, work_item_id: str | None,
    *, pin_dir: Path | None = None,
) -> tuple[str | None, str | None]:
    """The checks `finalize_bundle_generation` and
    `finalize_staged_plan_bundle_generation` share: the three-way
    `bundle_id` reproducibility check; for the plan stage the pin
    byte-identity binding (when a pin exists at `pin_dir`, default the
    in-place `.pin`) and the `TEST_RESULTS.md`/`REVIEW_REQUEST.md`
    consistency check; for implementation/post-fix the stage-completeness
    check. Returns `(recorded_bundle_id, mismatch_detail)`;
    `recorded_bundle_id` is `None` when `MANIFEST.md` records none."""
    recorded = read_manifest_identifiers(bundle_dir / MANIFEST_FILENAME)
    recorded_bundle_id = recorded.get("bundle_id")
    if recorded_bundle_id is None:
        return None, "MANIFEST.md has no recorded bundle_id"

    ondisk_bundle_id, _ = compute_bundle_id(bundle_dir)

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive_path) as tf:
            tf.extractall(tmp)
        extracted_root = Path(tmp) / "current"
        extracted_bundle_id, _ = compute_bundle_id(extracted_root)

        mismatch_detail = None
        if not (recorded_bundle_id == ondisk_bundle_id == extracted_bundle_id):
            mismatch_detail = (
                f"bundle_id mismatch: manifest={recorded_bundle_id} "
                f"ondisk={ondisk_bundle_id} extracted={extracted_bundle_id}"
            )
        elif stage == "plan" and work_item_id is not None:
            metadata = resolve_plan_stage_metadata(repo_root, work_item_id)
            if pin_dir is None:
                pin_dir = _pin_dir_for_work_item(repo_root, work_item_id)
            if pin_dir.is_dir():
                try:
                    assert_plan_stage_document_matches_pin(
                        pin_dir, bundle_dir, metadata, extracted_root,
                    )
                except PlanStageDocumentStaleError as exc:
                    mismatch_detail = str(exc)
            if mismatch_detail is None:
                _, current_head = current_worktree_root_and_head(repo_root)
                try:
                    assert_test_results_consistent_with_plan_review_request(
                        bundle_dir, metadata.plan_revision, current_head,
                    )
                except TestResultsStaleError as exc:
                    mismatch_detail = str(exc)
        elif stage in ("implementation", "post-fix"):
            # OPUS-R133-003: `assert_stage_completeness`'s implementation/
            # post-fix branch existed (documented, implemented, tested) but
            # had no live caller anywhere in the generation path, so a
            # bundle whose IMPLEMENTATION_SUMMARY.md declared a stale
            # implementation_revision published anyway -- this is that
            # branch's first live caller, mirroring the plan stage's own
            # revision-consistency check immediately above.
            #
            # OPUS-R136-M04: `REVIEW_PROTOCOL.md` documents `work-item-id`
            # as optional for these two stages, but the original version of
            # this branch only ran the check `and work_item_id is not
            # None` -- the documented omitted-id invocation therefore
            # skipped the completeness check entirely rather than
            # resolving a target for it, silently reintroducing the exact
            # stale-summary publication class OPUS-R133-003 closed. When
            # `work_item_id` is omitted, resolve the same
            # `active_work_item_id` fallback the read-only inspection CLI
            # already uses, and still run the check against it; if no
            # active work item is recorded either, fail closed (mismatch)
            # instead of publishing unchecked. This never widens what
            # `withdraw_bundle` touches below, which still keys off the
            # caller's own `work_item_id` argument, not this resolved
            # fallback -- an omitted-id caller's bundle is reported as a
            # mismatch, never quarantined under a work item it never named.
            work_items, active_work_item_id = _load_workflow_state_work_items(repo_root, None)
            completeness_work_item_id = work_item_id if work_item_id is not None else active_work_item_id
            if completeness_work_item_id is None:
                mismatch_detail = (
                    "work_item_id was omitted and WORKFLOW_STATE.json has no "
                    "active_work_item_id to resolve implementation_revision from -- "
                    "cannot verify implementation/post-fix stage completeness"
                )
            else:
                entry = work_items.get(completeness_work_item_id)
                implementation_revision = entry.get("implementation_revision") if entry else None
                try:
                    assert_stage_completeness(
                        bundle_dir, stage, implementation_revision=implementation_revision,
                    )
                except StageCompletenessError as exc:
                    mismatch_detail = str(exc)
    return recorded_bundle_id, mismatch_detail


def finalize_bundle_generation(
    repo_root: Path, bundle_dir: Path, archive_path: Path, stage: str, work_item_id: str | None,
) -> dict:
    """The closing half of one in-place `prepare-ai-review.sh` generation
    run -- since workflow-2.6.0 the implementation and post-fix stages
    only (the plan stage generates into staging and finalizes through
    `finalize_staged_plan_bundle_generation`); a direct call at the plan
    stage keeps its `2.5.1` behavior. The checks are
    `_closing_bundle_generation_check`'s: the pre-existing three-way
    `bundle_id` reproducibility check (manifest / on-disk / archived);
    for the plan stage, additionally the byte-identity binding check
    (part 3, when a pin was captured) and the `TEST_RESULTS.md`/
    `REVIEW_REQUEST.md` consistency check (item 272, unconditional --
    does not depend on a pin); for the implementation/post-fix stages,
    `assert_stage_completeness`'s `IMPLEMENTATION_SUMMARY.md` revision-
    consistency check (`OPUS-R133-003`: this branch existed since WF5 but
    had no live caller before this, so a bundle whose author-written
    summary stated a stale `implementation_revision` published anyway);
    on any failure, and only when `work_item_id` is given (the marker
    mechanism has no flat-compatibility-layout counterpart), withdrawal
    (part 3b) rather than leaving a stale-but-self-verifying artifact in
    place; on success, clearing any pre-existing `REJECTED` marker for
    this work item. Returns a dict describing the outcome; never
    swallows a withdrawal
    step's own `BundleWithdrawalError`."""
    recorded_bundle_id, mismatch_detail = _closing_bundle_generation_check(
        repo_root, bundle_dir, archive_path, stage, work_item_id,
    )
    if recorded_bundle_id is None:
        return {"status": "error", "message": mismatch_detail}

    if mismatch_detail is not None:
        if work_item_id is not None:
            quarantine_path = withdraw_bundle(repo_root, work_item_id, mismatch_detail)
            return {
                "status": "withdrawn",
                "message": mismatch_detail,
                "quarantine": str(quarantine_path),
            }
        return {"status": "mismatch", "message": mismatch_detail}

    if work_item_id is not None:
        clear_rejected_marker_if_present(repo_root, work_item_id)
    return {"status": "ok", "bundle_id": recorded_bundle_id}


# ---------------------------------------------------------------------------
# workflow-2.6.0 CP4: D-Plan-Review-Bundle-Binding item 5 -- a recoverable
# plan-stage generator. The plan stage assembles its bundle, pin, archive and
# AMENDMENT_DIFF.patch in a per-run staging area and renames them into place
# only after the closing checks pass. A failed generation removes the
# staging area and leaves the previous `current/`, archive and `.pin`
# byte-identical: it withdraws nothing and writes no `REJECTED` marker (the
# revised `WFR-67`), because its own artifacts never became review-ready.
# The implementation and post-fix stages keep the in-place generation and
# `withdraw_bundle` failure path above, unchanged.
# ---------------------------------------------------------------------------

PLAN_REVIEW_INPUT_FILES: tuple[str, ...] = (
    "REVIEW_REQUEST.md", "IMPLEMENTATION_SUMMARY.md", "TEST_RESULTS.md", "CONTEXT_FILES.txt",
)
_STAGING_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_LEFTOVER_STAGING_PREFIXES: tuple[str, ...] = ("current.staging-", ".pin.staging-", "current.old-", ".pin.old-")


class InvalidStagingTokenError(Exception):
    """Raised for a plan-stage staging token that is not 32 lowercase hex
    characters -- the token is interpolated into sibling directory names
    and never trusted beyond that grammar."""


class PlanReviewInputPathError(Exception):
    """Raised when a `plan-inputs/` author file (or its `current/` seed) is
    a symlink or not a regular file -- it is copied byte-for-byte into
    the bundle, so only a regular file is accepted."""


class PlanStagePromotionError(Exception):
    """Raised when a staged plan-stage generation that passed every
    closing check cannot be renamed into place. What was already renamed
    stays; the verifier (`workflow_state.verify_plan_review_bundle`)
    refuses a `current/`/manifest/archive disagreement, and the remedy is
    to regenerate."""


def resolve_plan_review_inputs_dir(repo_root: Path, work_item_id: str) -> Path:
    """The plan stage's author-input directory,
    `.ai-review/<work_item_id>/plan-inputs/` (repo-root-relative) -- a
    sibling of `current/`, never inside it. `/milestone-plan` step 6 and
    `/apply-plan-review` step 5 write `REVIEW_REQUEST.md`,
    `TEST_RESULTS.md`, `CONTEXT_FILES.txt` (and, if ever needed,
    `IMPLEMENTATION_SUMMARY.md`) here, never into `current/`; the
    generator copies each byte-for-byte into its staging bundle."""
    validate_work_item_id(work_item_id)
    return Path(".ai-review") / work_item_id / "plan-inputs"


def plan_stage_staging_paths(repo_root: Path, work_item_id: str, token: str) -> dict[str, Path]:
    """Absolute paths of one staged plan-stage generation, keyed
    `root` (the staging area, `.ai-review/<id>/current.staging-<token>/`),
    `bundle_dir` (its `current/` child, so the archive's member root stays
    the bare literal `current`), `archive` (the temporary archive),
    `amendment_diff` and `pin_dir` (`.ai-review/<id>/.pin.staging-<token>/`)."""
    validate_work_item_id(work_item_id)
    if not isinstance(token, str) or not _STAGING_TOKEN_RE.match(token):
        raise InvalidStagingTokenError(f"invalid plan-stage staging token {token!r}")
    item_root = Path(repo_root) / ".ai-review" / work_item_id
    staging_root = item_root / f"current.staging-{token}"
    return {
        "root": staging_root,
        "bundle_dir": staging_root / "current",
        "archive": staging_root / "review-bundle.tar.gz",
        "amendment_diff": staging_root / "AMENDMENT_DIFF.patch",
        "pin_dir": item_root / f".pin.staging-{token}",
    }


def remove_leftover_plan_stage_staging(repo_root: Path, work_item_id: str) -> list[str]:
    """Removes every leftover staging or superseded directory of a previous,
    interrupted plan-stage generation for `work_item_id`
    (`current.staging-*`, `.pin.staging-*`, `current.old-*`, `.pin.old-*`).
    Nothing reads them; the next generation removes them. Returns the
    removed names."""
    validate_work_item_id(work_item_id)
    item_root = Path(repo_root) / ".ai-review" / work_item_id
    removed: list[str] = []
    if not item_root.is_dir():
        return removed
    for entry in sorted(item_root.iterdir()):
        if entry.name.startswith(_LEFTOVER_STAGING_PREFIXES):
            if entry.is_symlink() or not entry.is_dir():
                entry.unlink()
            else:
                shutil.rmtree(entry)
            removed.append(entry.name)
    return removed


def discard_plan_stage_staging(repo_root: Path, work_item_id: str, token: str) -> None:
    """Removes one staged generation's staging area and staging pin -- the
    failure path of the revised `WFR-67`. Never touches `current/`, the
    archive, `.pin` or the `REJECTED` marker. Idempotent."""
    paths = plan_stage_staging_paths(repo_root, work_item_id, token)
    for path in (paths["root"], paths["pin_dir"]):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)


def seed_plan_review_inputs(repo_root: Path, work_item_id: str, bundle_dir: Path) -> dict[str, str]:
    """Copies each of `PLAN_REVIEW_INPUT_FILES` byte-for-byte (content and
    owner-executable bit, which `compute_bundle_id` hashes) into a staging
    `bundle_dir`: from `plan-inputs/<file>` when present; else from the
    existing `current/<file>` (read-only -- the migration path for an
    author who has not moved yet); else an empty stub, exactly as `2.5.1`
    stubbed a missing author file. Returns `{file: source}` with source
    `"plan-inputs"`, `"current"` or `"stub"`. A symlinked or non-regular
    source refuses (`PlanReviewInputPathError`)."""
    inputs_dir = Path(repo_root) / resolve_plan_review_inputs_dir(repo_root, work_item_id)
    current_dir = Path(repo_root) / ".ai-review" / work_item_id / "current"
    sources: dict[str, str] = {}
    for name in PLAN_REVIEW_INPUT_FILES:
        dest = bundle_dir / name
        for label, source in (("plan-inputs", inputs_dir / name), ("current", current_dir / name)):
            if source.is_symlink() or (source.exists() and not source.is_file()):
                raise PlanReviewInputPathError(f"{source} is a symlink or not a regular file")
            if source.is_file():
                dest.write_bytes(source.read_bytes())
                os.chmod(dest, 0o755 if _owner_executable(source.stat().st_mode) else 0o644)
                sources[name] = label
                break
        else:
            dest.write_bytes(b"")
            sources[name] = "stub"
    return sources


def _promote_plan_stage_staging(repo_root: Path, work_item_id: str, token: str) -> None:
    """Renames a verified staging generation into place: the pin, then
    `current/`, then the archive, then `AMENDMENT_DIFF.patch` (removed
    when this generation wrote none), then removes the superseded copies
    and the staging area. The renames are not atomic as a set; INV-2 rests
    on the bind verifier, which refuses the `current/`/manifest/archive
    disagreement a crash between them leaves."""
    paths = plan_stage_staging_paths(repo_root, work_item_id, token)
    item_root = Path(repo_root) / ".ai-review" / work_item_id
    live_current = item_root / "current"
    live_pin = item_root / ".pin"
    old_current = item_root / f"current.old-{token}"
    old_pin = item_root / f".pin.old-{token}"
    step = "start"
    try:
        step = "rename pin"
        if paths["pin_dir"].is_dir():
            if live_pin.exists():
                os.rename(live_pin, old_pin)
            os.rename(paths["pin_dir"], live_pin)
        step = "rename current/"
        if live_current.exists():
            os.rename(live_current, old_current)
        os.rename(paths["bundle_dir"], live_current)
        step = "rename archive"
        os.replace(paths["archive"], item_root / "review-bundle.tar.gz")
        step = "rename AMENDMENT_DIFF.patch"
        if paths["amendment_diff"].is_file():
            os.replace(paths["amendment_diff"], item_root / "AMENDMENT_DIFF.patch")
        else:
            (item_root / "AMENDMENT_DIFF.patch").unlink(missing_ok=True)
        step = "remove superseded copies"
        for leftover in (old_current, old_pin, paths["root"]):
            if leftover.is_dir():
                shutil.rmtree(leftover)
    except OSError as exc:
        raise PlanStagePromotionError(
            f"promoting staged plan-stage generation {token} for {work_item_id!r} failed at step "
            f"{step!r} ({exc!r}) -- regenerate; the verifier refuses the partial result"
        ) from exc


def finalize_staged_plan_bundle_generation(repo_root: Path, work_item_id: str, token: str) -> dict:
    """The plan stage's closing half (workflow-2.6.0): runs
    `_closing_bundle_generation_check` against the staging bundle, staging
    archive and staging pin. On any failure it discards the staging area
    and staging pin (`discard_plan_stage_staging`) and returns
    `{"status": "failed", ...}` -- **no** `withdraw_bundle`, **no**
    `REJECTED` marker, and the previous `current/`, archive and `.pin`
    byte-identical (the revised `WFR-67`). On success it renames everything
    into place (`_promote_plan_stage_staging`), then clears any
    pre-existing `REJECTED` marker, and returns `{"status": "ok",
    "bundle_id": ...}`."""
    paths = plan_stage_staging_paths(repo_root, work_item_id, token)
    try:
        recorded_bundle_id, mismatch_detail = _closing_bundle_generation_check(
            repo_root, paths["bundle_dir"], paths["archive"], "plan", work_item_id,
            pin_dir=paths["pin_dir"],
        )
    except Exception as exc:  # noqa: BLE001 -- any closing-check failure discards the staging area
        recorded_bundle_id, mismatch_detail = None, f"{type(exc).__name__}: {exc}"
    if recorded_bundle_id is None or mismatch_detail is not None:
        discard_plan_stage_staging(repo_root, work_item_id, token)
        return {
            "status": "failed",
            "message": mismatch_detail,
            "note": "staging discarded; the previous current/, archive and .pin are unchanged; no REJECTED marker written",
        }
    _promote_plan_stage_staging(repo_root, work_item_id, token)
    clear_rejected_marker_if_present(repo_root, work_item_id)
    return {"status": "ok", "bundle_id": recorded_bundle_id}


# ---------------------------------------------------------------------------
# Generation diagnostic metadata — worktree_root/HEAD recorded in
# MANIFEST.md, portability-vs-local-staleness split (resolves
# `OPUS-R6-016`/`GPT-R9-015`). Deliberately never folded into either
# fingerprint's hashed projection: these two values identify *which
# generation run* produced the bundle, not the reviewed content itself, and
# an external reviewer consuming a portable extracted archive must never be
# blocked by local-path equality (`WFR-17`).
# ---------------------------------------------------------------------------

_WORKTREE_ROOT_LINE_RE = re.compile(r"^worktree_root: (.+)$", re.MULTILINE)
_GENERATION_HEAD_LINE_RE = re.compile(r"^generation_head: ([0-9a-f]{40})$", re.MULTILINE)

# Strict local-generation metadata mode (`GPT-R62-001`, `WF8c` item (k)):
# occurrence-counting siblings of the two well-formed regexes above -- these
# match a field's line regardless of whether its value is well-formed, so a
# duplicate line (one well-formed, one malformed) is counted as two
# occurrences rather than hidden behind the well-formed one alone. The
# `worktree_root` grammar itself (absolute path) is a separate regex;
# `generation_head`'s existing `_GENERATION_HEAD_LINE_RE` already is its own
# grammar check, reused directly for that purpose.
_WORKTREE_ROOT_ANY_LINE_RE = re.compile(r"^worktree_root:.*$", re.MULTILINE)
_GENERATION_HEAD_ANY_LINE_RE = re.compile(r"^generation_head:.*$", re.MULTILINE)
_WORKTREE_ROOT_WELLFORMED_RE = re.compile(r"^worktree_root: (/.+)$", re.MULTILINE)


def current_worktree_root_and_head(repo_root: Path) -> tuple[str, str]:
    """The absolute worktree root and current HEAD SHA, as recorded into a
    freshly generated bundle's `MANIFEST.md`."""
    root = _run(["git", "rev-parse", "--show-toplevel"], cwd=repo_root).strip()
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).strip()
    return root, head


def read_manifest_generation_metadata(manifest_path: Path) -> dict[str, str]:
    """Read whichever of `worktree_root`/`generation_head` a `MANIFEST.md`
    currently states, without writing anything -- mirrors
    `read_manifest_identifiers`'s read-only contract."""
    if not manifest_path.is_file():
        return {}
    content = manifest_path.read_text()
    fields: dict[str, str] = {}
    root_match = _WORKTREE_ROOT_LINE_RE.search(content)
    if root_match:
        fields["worktree_root"] = root_match.group(1)
    head_match = _GENERATION_HEAD_LINE_RE.search(content)
    if head_match:
        fields["generation_head"] = head_match.group(1)
    return fields


def _assert_strict_metadata_field(
    manifest_path: Path, content: str, field_name: str,
    any_line_re: "re.Pattern[str]", wellformed_re: "re.Pattern[str]",
) -> None:
    """One field's occurrence-then-grammar sub-check for
    `assert_local_generation_matches`'s `require_metadata=True` mode
    (`GPT-R62-001`). Occurrence is checked first and independently of
    grammar, so a duplicate line -- one well-formed, one not -- is caught
    by the count rather than passed because *a* well-formed line exists."""
    occurrences = any_line_re.findall(content)
    if len(occurrences) != 1:
        raise WorktreeOrHeadMismatchError(
            f"{manifest_path} must record exactly one {field_name}: line "
            f"under strict local-generation metadata mode; found {len(occurrences)}"
        )
    if not wellformed_re.search(content):
        raise WorktreeOrHeadMismatchError(
            f"{manifest_path}'s {field_name}: line is not well-formed "
            f"under strict local-generation metadata mode"
        )


def assert_local_generation_matches(
    repo_root: Path, manifest_path: Path, *, require_metadata: bool = False,
) -> None:
    """**Repository-local commands only** (`/approve-review`,
    `/review-plan`, `/review-implementation`, and -- workflow-2.7.0 --
    `/record-manual-plan-review`/`/record-manual-implementation-review` and
    the protocol's `record-external-result`, all through
    `workflow_state.ingest_manual_review_verdict`, and the
    repository-aware gate wrappers `workflow_state.plan_approval_gate_status`/
    `technical_approval_gate_status`, which `/approve-review` and the
    orchestration protocol's `next-action` call): stop if the current worktree
    root or HEAD SHA differs from what `MANIFEST.md` recorded at
    generation time, naming both. Never call this from a path that also
    serves external reviewers -- see `WorktreeOrHeadMismatchError` and
    `WFR-17`.

    `require_metadata=False` (the default -- all three callers above use
    it, unchanged): a `MANIFEST.md` missing either field's line entirely
    records nothing to compare for that field, so the pre-metadata legacy
    shape passes with no comparison performed. `require_metadata=True`
    (`GPT-R62-001`, strict local-generation metadata mode -- no live
    caller of this mode exists in this repository; the one caller this
    mode was designed for, `D-Approval-Commits`' atomic-bundle-publication
    current-round binding check, was superseded, revision 82,
    `OPUS-R102-001`, before being built): exactly one well-formed
    `worktree_root:` line and exactly one well-formed `generation_head:`
    line must be present -- zero, more than one, or a malformed line for
    either field is itself a mismatch, checked by two independent
    per-field sub-checks (occurrence, then grammar)."""
    if require_metadata:
        content = manifest_path.read_text() if manifest_path.is_file() else ""
        _assert_strict_metadata_field(
            manifest_path, content, "worktree_root",
            _WORKTREE_ROOT_ANY_LINE_RE, _WORKTREE_ROOT_WELLFORMED_RE,
        )
        _assert_strict_metadata_field(
            manifest_path, content, "generation_head",
            _GENERATION_HEAD_ANY_LINE_RE, _GENERATION_HEAD_LINE_RE,
        )
    recorded = read_manifest_generation_metadata(manifest_path)
    current_root, current_head = current_worktree_root_and_head(repo_root)
    if "worktree_root" in recorded and recorded["worktree_root"] != current_root:
        raise WorktreeOrHeadMismatchError(
            f"MANIFEST.md was generated in worktree {recorded['worktree_root']!r}, "
            f"this is {current_root!r}"
        )
    if "generation_head" in recorded and recorded["generation_head"] != current_head:
        raise WorktreeOrHeadMismatchError(
            f"MANIFEST.md was generated at HEAD {recorded['generation_head']!r}, "
            f"current HEAD is {current_head!r}"
        )


# ---------------------------------------------------------------------------
# Stage-completeness / revision-consistency check (WF5): `PLAN.md`'s stated
# revision must agree with the plan document's current `plan_revision`
# before a plan-stage bundle is considered complete; `IMPLEMENTATION_SUMMARY.md`'s
# stated `implementation_revision` must agree with the work item's current
# counter before an implementation/post-fix-stage bundle is.
# ---------------------------------------------------------------------------

_IMPLEMENTATION_REVISION_LINE_RE = re.compile(r"^implementation_revision: (\d+)$", re.MULTILINE)


def assert_stage_completeness(
    bundle_dir: Path,
    stage: str,
    *,
    plan_revision: int | None = None,
    implementation_revision: int | None = None,
) -> None:
    """Fails closed if the bundle's own author-written stage document does
    not state the revision the authoritative source currently declares —
    an unrefreshed copy left over from an earlier round is exactly the
    staleness this check exists to catch (unchanged design from round 5's
    `R5-PLAN-015`). No-ops for `functional-review`, which has no revision
    counter of its own."""
    if stage == "plan":
        if plan_revision is None:
            raise StageCompletenessError("plan_revision is required for the plan stage")
        plan_path = bundle_dir / "PLAN.md"
        content = plan_path.read_text() if plan_path.is_file() else ""
        match = PLAN_TITLE_REVISION_RE.search(content)
        if match is None:
            raise StageCompletenessError(
                f"{plan_path} states no '(Revision N)' marker -- expected Revision {plan_revision}"
            )
        if int(match.group(1)) != plan_revision:
            raise StageCompletenessError(
                f"{plan_path} states Revision {match.group(1)}, "
                f"the authoritative plan document currently declares Revision {plan_revision}"
            )
    elif stage in ("implementation", "post-fix"):
        if implementation_revision is None:
            raise StageCompletenessError(
                "implementation_revision is required for the implementation/post-fix stage"
            )
        summary_path = bundle_dir / "IMPLEMENTATION_SUMMARY.md"
        content = summary_path.read_text() if summary_path.is_file() else ""
        match = _IMPLEMENTATION_REVISION_LINE_RE.search(content)
        if match is None:
            raise StageCompletenessError(
                f"{summary_path} states no 'implementation_revision: N' line -- "
                f"expected {implementation_revision}"
            )
        if int(match.group(1)) != implementation_revision:
            raise StageCompletenessError(
                f"{summary_path} states implementation_revision: {match.group(1)}, "
                f"the work item's current counter is {implementation_revision}"
            )
    elif stage == "functional-review":
        return
    else:
        raise StageCompletenessError(f"unknown stage: {stage!r}")


# ---------------------------------------------------------------------------
# Plan-stage TEST_RESULTS.md/REVIEW_REQUEST.md consistency (item 272):
# unlike PLAN.md and IMPLEMENTATION_SUMMARY.md, TEST_RESULTS.md previously
# carried no machine-checked marker at all, so an empty stub (created by
# prepare-ai-review.sh's own "create if missing" fallback) or a copy left
# over from an earlier round -- including a previous implementation
# round's evidence carried forward unexamined -- published silently.
# ---------------------------------------------------------------------------

_TEST_RESULTS_STAGE_LINE_RE = re.compile(r"^stage: plan \(revision (\d+)\)$", re.MULTILINE)
_TEST_RESULTS_HEAD_LINE_RE = re.compile(r"^head: ([0-9a-f]{40})$", re.MULTILINE)


def assert_test_results_consistent_with_plan_review_request(
    bundle_dir: Path, plan_revision: int, generation_head: str,
) -> None:
    """Item 272: a plan-stage bundle's `TEST_RESULTS.md` must be
    regenerated fresh for every plan-review round, never an empty stub
    or a copy carried forward from a different round. Requires two
    labelled lines, the same discipline `PLAN.md`'s own `(Revision N)`
    marker and `IMPLEMENTATION_SUMMARY.md`'s own `implementation_revision:`
    line already apply:

    - `stage: plan (revision N)`, `N` equal to this round's
      `plan_revision` -- a missing/empty file, a different round, and
      implementation-stage evidence carried forward (which would state a
      different `stage:` value entirely, never literally `plan`) are all
      caught by this one line-and-value match;
    - `head: <40-hex>`, equal to this generation's own HEAD -- so a
      bundle regenerated at an unchanged `plan_revision` but a new HEAD
      (ordinary commits landing between two rounds at the same revision)
      cannot silently carry forward a previous generation's evidence.

    Called from `finalize_bundle_generation`'s closing checks, alongside
    the pin-sourced `PLAN.md` byte-identity assertion -- a failure here
    triggers the same `withdraw_bundle` quarantine, never a silently
    published bundle."""
    test_results_path = bundle_dir / "TEST_RESULTS.md"
    content = test_results_path.read_text() if test_results_path.is_file() else ""
    stage_match = _TEST_RESULTS_STAGE_LINE_RE.search(content)
    if stage_match is None:
        raise TestResultsStaleError(
            f"{test_results_path} states no 'stage: plan (revision N)' line -- "
            f"expected revision {plan_revision}. A missing/empty file, or content "
            f"describing a different stage (e.g. carried-forward implementation-"
            f"stage evidence), is exactly what this check exists to catch (item 272)"
        )
    if int(stage_match.group(1)) != plan_revision:
        raise TestResultsStaleError(
            f"{test_results_path} states 'stage: plan (revision {stage_match.group(1)})', "
            f"the authoritative plan document currently declares revision {plan_revision}"
        )
    head_match = _TEST_RESULTS_HEAD_LINE_RE.search(content)
    if head_match is None:
        raise TestResultsStaleError(
            f"{test_results_path} states no 'head: <sha>' line -- expected "
            f"{generation_head}"
        )
    if head_match.group(1) != generation_head:
        raise TestResultsStaleError(
            f"{test_results_path} states 'head: {head_match.group(1)}', this "
            f"generation's own HEAD is {generation_head}"
        )


# ---------------------------------------------------------------------------
# Feedback binding fields (WFR-03): `REVIEW_FEEDBACK.md` must state which
# exact bundle it reviewed, so stale/missing feedback is rejected naming
# both the feedback's value and the current one, rather than a reviewer's
# free-text claim being trusted at face value.
# ---------------------------------------------------------------------------

_FEEDBACK_STATUS_RE = re.compile(r"^Status:\s*(APPROVE|REVISE|BLOCK)\s*$", re.MULTILINE)
_FEEDBACK_BUNDLE_ID_RE = re.compile(r"^Reviewed bundle ID:\s*([0-9a-f]{64})\s*$", re.MULTILINE)
_FEEDBACK_BASE_COMMIT_RE = re.compile(r"^Reviewed base commit:\s*([0-9a-f]{40})\s*$", re.MULTILINE)
_FEEDBACK_WORK_ITEM_RE = re.compile(r"^Work item:\s*(\S+)\s*$", re.MULTILINE)


def parse_review_feedback_binding_fields(content: str) -> dict[str, str | None]:
    """Extract `REVIEW_FEEDBACK.md`'s `Status:`, `Reviewed bundle ID:`,
    `Reviewed base commit:`, and `Work item:` fields. Each key is `None` if
    the field is absent -- callers decide whether absence is fatal (most
    are, per `WFR-03`; WF0's bootstrap-only check already enforces this for
    its own one-time approving feedback, per `OPUS-R14-002`)."""
    status_match = _FEEDBACK_STATUS_RE.search(content)
    bundle_id_match = _FEEDBACK_BUNDLE_ID_RE.search(content)
    base_commit_match = _FEEDBACK_BASE_COMMIT_RE.search(content)
    work_item_match = _FEEDBACK_WORK_ITEM_RE.search(content)
    return {
        "status": status_match.group(1) if status_match else None,
        "reviewed_bundle_id": bundle_id_match.group(1) if bundle_id_match else None,
        "reviewed_base_commit": base_commit_match.group(1) if base_commit_match else None,
        "work_item": work_item_match.group(1) if work_item_match else None,
    }


_FEEDBACK_REVIEW_CONTENT_ID_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?(?:Reviewed[ \t]+)?"
    r"(?:`?review_content_id`?|review[ \t]+content[ \t]+ID)"
    r":[ \t]*`?([0-9a-f]{64})`?[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_FEEDBACK_FIELD_LINE_RE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?`?[A-Za-z][A-Za-z0-9_ \t]*`?:[ \t]*\S")
_FEEDBACK_REVIEWER_ROLE_RE = re.compile(r"^Reviewer role:\s*(\S+)\s*$", re.MULTILINE)

#: The label `/review-plan` and `/review-implementation` write, and every
#: two-stage stage verdict must carry (`D-Feedback-Label`, workflow-2.7.0,
#: `v2.6.0-002`). `Reviewed review content ID:` stays a legacy alias.
FEEDBACK_REVIEW_CONTENT_ID_LABEL = "Reviewed review_content_id:"


def feedback_header_block(content: str) -> str:
    """The header block of a `REVIEW_FEEDBACK.md` (`D-Feedback-Label`,
    workflow-2.7.0): every line before the first `## ` heading that
    follows a field line (`key: value`). A verdict that opens with
    `## Review Decision` and states its fields under it keeps those fields
    in its header; only a `## ` heading after a field line ends it."""
    lines = content.splitlines(keepends=True)
    seen_field = False
    for index, line in enumerate(lines):
        if line.startswith("## ") and seen_field:
            return "".join(lines[:index])
        if _FEEDBACK_FIELD_LINE_RE.match(line):
            seen_field = True
    return content


def parse_feedback_review_content_id(content: str) -> str | None:
    """The `review_content_id` a `REVIEW_FEEDBACK.md` states as its own
    labelled line in its header block (`feedback_header_block`): the
    pinned `Reviewed review_content_id: <hex>`, the bare
    `review_content_id: <hex>`, or the legacy alias
    `Reviewed review content ID: <hex>`, case-insensitively, optionally as a
    list bullet or in backticks. Used by `/apply-plan-review`'s durable
    feedback check (workflow-2.6.0); header-only since workflow-2.7.0
    (`v2.6.0-002`), so a finding that quotes another ID cannot disturb it.
    `None` when absent, or when the header states more than one distinct
    value -- an ambiguous statement never matches."""
    header = feedback_header_block(content)
    values = {match.group(1).lower() for match in _FEEDBACK_REVIEW_CONTENT_ID_RE.finditer(header)}
    return values.pop() if len(values) == 1 else None


_FEEDBACK_REVIEWER_MODEL_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?`?Reviewer model:`?[ \t]*(\S.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE)


def parse_feedback_reviewer_model(content: str) -> str | None:
    """The `Reviewer model: <vendor>/<model>` a `REVIEW_FEEDBACK.md` declares
    (workflow-2.8.0, `D-GP-Gates`, `LPR-R17-O2`), header-only exactly like
    `parse_feedback_review_content_id`: only within the leading header block
    (`feedback_header_block`), never from a body line, so a verdict that
    quotes the text in its body declares nothing. The value is returned as
    written, trimmed. `None` when absent, or when the header states more than
    one distinct value -- an ambiguous statement never matches."""
    header = feedback_header_block(content)
    values = {match.group(1).strip() for match in _FEEDBACK_REVIEWER_MODEL_RE.finditer(header)}
    return values.pop() if len(values) == 1 else None


def parse_review_feedback_header(content: str) -> dict[str, str | None]:
    """The single verdict parser (`D-Feedback-Label`, workflow-2.7.0):
    `status`, `reviewed_bundle_id`, `reviewed_base_commit` and `work_item`
    from `parse_review_feedback_binding_fields` (whole-file, as in 2.6.0),
    `reviewer_role` by the same whole-file first-match convention, and
    `review_content_id` from `parse_feedback_review_content_id` and
    `reviewer_model` from `parse_feedback_reviewer_model`, the two
    header-only fields. Each key is `None` when absent."""
    fields: dict[str, str | None] = dict(parse_review_feedback_binding_fields(content))
    role_match = _FEEDBACK_REVIEWER_ROLE_RE.search(content)
    fields["reviewer_role"] = role_match.group(1) if role_match else None
    fields["review_content_id"] = parse_feedback_review_content_id(content)
    fields["reviewer_model"] = parse_feedback_reviewer_model(content)
    return fields


def assert_manual_feedback_names_work_item(content: str, *, work_item_id: str) -> None:
    """`D-Feedback-Layout`'s manual-record binding: `/record-manual-plan-review`
    and `/record-manual-implementation-review` refuse a pasted
    `REVIEW_FEEDBACK.md` whose `Work item:` field is present and names a
    different work item (`ManualFeedbackForeignWorkItemError`, naming
    both). An absent field is not refused here -- a hand-pasted manual
    verdict may omit it, and the hard `review_content_id` check those
    commands already run still binds it."""
    named = parse_review_feedback_binding_fields(content).get("work_item")
    if named is not None and named != work_item_id:
        raise ManualFeedbackForeignWorkItemError(
            f"the pasted feedback names work item {named!r}, not {work_item_id!r} -- "
            f"refusing to record another item's verdict"
        )


def assert_feedback_matches_bundle(
    feedback_fields: Mapping[str, str | None],
    *,
    bundle_id: str,
    base_commit: str,
    work_item_id: str,
) -> None:
    """Reject feedback missing any of the three binding fields, or whose
    values disagree with the bundle actually being approved against —
    naming both the feedback's own value and the current one in every case
    (`WFR-03`)."""
    for key in ("reviewed_bundle_id", "reviewed_base_commit", "work_item"):
        if feedback_fields.get(key) is None:
            raise MissingFeedbackBindingFieldError(key)
    if feedback_fields["reviewed_bundle_id"] != bundle_id:
        raise FeedbackBundleMismatchError(
            f"feedback reviewed bundle_id {feedback_fields['reviewed_bundle_id']!r}, "
            f"current bundle_id is {bundle_id!r}"
        )
    if feedback_fields["reviewed_base_commit"] != base_commit:
        raise FeedbackBundleMismatchError(
            f"feedback reviewed base commit {feedback_fields['reviewed_base_commit']!r}, "
            f"current base commit is {base_commit!r}"
        )
    if feedback_fields["work_item"] != work_item_id:
        raise FeedbackBundleMismatchError(
            f"feedback names work item {feedback_fields['work_item']!r}, "
            f"expected {work_item_id!r}"
        )


def assert_feedback_not_owned_by_other_work_item(
    existing_content: str | None, *, work_item_id: str, state: Mapping | None = None,
) -> None:
    """Refuse `/review-implementation`'s write when whatever content
    already sits at the resolved `<feedback_dir>/REVIEW_FEEDBACK.md` path
    belongs to a *different* work item — checked immediately before the
    write, alongside a second `assert_bundle_not_rejected` call, so a
    genuine cross-work-item collision at `resolve_feedback_dir`'s
    scoped-else-flat path is refused rather than silently overwritten
    (`GPT-FUP-R6-I01`).

    `existing_content` is `None` when no file sits at the resolved path
    yet, in which case this returns immediately. Otherwise the content is
    parsed with `parse_review_feedback_binding_fields`; a present `work_item`
    field that disagrees with `work_item_id` raises
    `FeedbackOwnedByOtherWorkItemError` naming both. A missing/unparsed
    `work_item` field (a hand-authored file, or one predating the binding-
    field convention) is treated as unowned and does not block the write —
    matching how a same-work-item overwrite already behaves today via
    `/review-plan` step 8's guard-then-overwrite pattern. `resolve_feedback_dir`
    itself is never touched by this function or by any caller of it
    (`LPR-R7-B01`).

    `state` (`D-Feedback-Layout`, workflow-2.6.0): the parsed
    `WORKFLOW_STATE.json`, optional. When supplied, a foreign owner whose
    own entry sits at a phase in `FEEDBACK_OWNER_TERMINAL_PHASES` does not
    block the write -- terminal state proves no consumer of that file
    remains -- **but only for a legacy writer** (an entry without
    `feedback_layout`, or no entry). A scoped writer's directory is
    private by construction, so a foreign file inside it is never
    relaxed. A non-terminal owner, or an owner absent from `state`, still
    refuses. The relaxed write replaces the file whole with the writer's
    own binding fields; it is never reinterpreted as the writer's."""
    if existing_content is None:
        return
    existing_work_item = parse_review_feedback_binding_fields(existing_content).get("work_item")
    if existing_work_item is not None and existing_work_item != work_item_id:
        if state is not None:
            work_items = state.get("work_items", {})
            writer = work_items.get(work_item_id)
            owner = work_items.get(existing_work_item)
            writer_is_legacy = not (isinstance(writer, Mapping) and "feedback_layout" in writer)
            if (writer_is_legacy and isinstance(owner, Mapping)
                    and owner.get("phase") in FEEDBACK_OWNER_TERMINAL_PHASES):
                return
        raise FeedbackOwnedByOtherWorkItemError(
            f"existing feedback at this path belongs to work item {existing_work_item!r}, "
            f"not {work_item_id!r} -- refusing to overwrite"
        )


# ---------------------------------------------------------------------------
# bundle_id — resolves PROTO-R7-004/006/007, OPUS-R8-001/002/004/013/017
# ---------------------------------------------------------------------------

REQUIRED_BUNDLE_FILES: frozenset[str] = frozenset({"MANIFEST.md"})

# Exactly one line of this form may appear in any bundle file: the
# canonical way any file is allowed to report the bundle's own identity
# (OPUS-R8-001/-013). Anything else -- a Markdown list item, an indented
# line inside a fenced block, a table cell -- is simply not this field, and
# is therefore real hashed content, which is the desired behavior: the
# contract is enforced by what the pattern matches, not asserted in prose.
BUNDLE_ID_FIELD_RE = re.compile(rb"^bundle_id: [0-9a-f]{64}$")


def _split_lines_preserve_trailing(content: bytes) -> tuple[list[bytes], bool]:
    """Byte-level split on `b"\\n"` only, preserving whether the content
    ended with a trailing newline. Never decodes, never treats `\\r`,
    `\\x0b`, or `\\x0c` as a line terminator the way `str.splitlines()`
    does — resolves OPUS-R8-002's CRLF/trailing-newline/non-UTF-8 collision
    classes."""
    if not content:
        return [], False
    had_trailing_nl = content.endswith(b"\n")
    body = content[:-1] if had_trailing_nl else content
    return body.split(b"\n"), had_trailing_nl


def _join_lines_preserve_trailing(lines: list[bytes], had_trailing_nl: bool) -> bytes:
    joined = b"\n".join(lines)
    if had_trailing_nl:
        joined += b"\n"
    return joined


def _normalize_changed_files_header(content: bytes) -> bytes:
    """`CHANGED_FILES.txt`'s header block (everything before the first
    blank line) must contain exactly one `generated:` line; that line is
    normalized to a fixed placeholder so bundle regeneration is idempotent.
    Zero or more than one such line fails closed instead of silently
    no-opping — resolves OPUS-R8-009, which found the previous
    fixed-line-index version went silent the moment the header format
    changed shape."""
    lines, had_trailing_nl = _split_lines_preserve_trailing(content)
    header_end = len(lines)
    for i, line in enumerate(lines):
        if line == b"":
            header_end = i
            break
    header = lines[:header_end]
    matches = [i for i, line in enumerate(header) if line.startswith(b"generated:")]
    if not matches:
        raise MissingGeneratedTimestampError(
            "no 'generated:' line found in CHANGED_FILES.txt header block"
        )
    if len(matches) > 1:
        raise DuplicateGeneratedTimestampError(
            f"multiple 'generated:' lines found in CHANGED_FILES.txt header: {matches}"
        )
    lines[matches[0]] = b"generated: <normalized>"
    return _join_lines_preserve_trailing(lines, had_trailing_nl)


MANIFEST_FILENAME = "MANIFEST.md"


def _strip_manifest_self_reference(content: bytes) -> bytes:
    """Normalize out `MANIFEST.md`'s own self-reported identity line —
    the **only** bundle file allowed to carry one (resolves GPT-R9-001,
    narrowing OPUS-R8-001's blanket every-file exclusion back to a single
    schema-defined location). Enforces the field contract from
    OPUS-R8-013: more than one matching line fails closed rather than
    silently stripping all of them."""
    lines, had_trailing_nl = _split_lines_preserve_trailing(content)
    matches = [i for i, line in enumerate(lines) if BUNDLE_ID_FIELD_RE.match(line)]
    if len(matches) > 1:
        raise MalformedBundleIdFieldError(
            f"multiple 'bundle_id: <hex>' lines found ({len(matches)})"
        )
    if not matches:
        return content
    filtered = [line for i, line in enumerate(lines) if i not in matches]
    return _join_lines_preserve_trailing(filtered, had_trailing_nl)


def _reject_foreign_bundle_id_field(rel: str, content: bytes) -> None:
    """Any bundle file *other than* `MANIFEST.md` must not contain a line
    matching the `bundle_id: <64-hex-chars>` contract. Round 8's blanket
    exclusion made a wrong, stale, or tampered value in a narrative file
    (e.g. `TEST_RESULTS.md`) invisible to the identifier instead of
    flagged — confirmed live in round 9's own reviewed bundle. Failing
    closed here means that class of mistake cannot recur silently —
    resolves GPT-R9-001."""
    lines, _ = _split_lines_preserve_trailing(content)
    matches = [i for i, line in enumerate(lines) if BUNDLE_ID_FIELD_RE.match(line)]
    if matches:
        raise ForeignBundleIdFieldError(rel, matches)


def compute_bundle_id(
    bundle_dir: Path,
    *,
    manifest_content_override: bytes | None = None,
) -> tuple[str, dict]:
    """Hash every file under bundle_dir, keyed by POSIX-style relative path
    (OPUS-R8-017), each entry carrying mode and content hash (PROTO-R7-007).
    Only `MANIFEST.md` may report the bundle's own identity; that one field
    is normalized out of that one file (GPT-R9-001, narrowing
    OPUS-R8-001/-013); the same pattern in any other file fails closed.
    `CHANGED_FILES.txt`'s generated-time line is normalized, failing closed
    if missing/duplicated (OPUS-R8-009). Symlinks are checked *before*
    directories, so a symlinked directory fails closed instead of silently
    bypassing the check the way `Path.is_dir()`-first ordering allowed
    (OPUS-R8-004); any other non-regular, non-directory entry (FIFO,
    socket, device node) fails closed too. Mode is read from the file's own
    stat bits, not effective-access `os.access()` (GPT-R9-009). A bundle
    missing a required file (currently `MANIFEST.md`) fails validation
    rather than returning an identifier for an incomplete bundle
    (OPUS-R8-001).

    `manifest_content_override`, if given, substitutes for `MANIFEST.md`'s
    on-disk content (whether or not that file exists yet) instead of
    reading it from `bundle_dir` -- lets a caller compute what `bundle_id`
    would be for a manifest it has not written anywhere, so the actual
    write can be deferred until every check has passed (resolves
    `OPUS-R20-001`, missing-test item 138: without this, the generator had
    to write a placeholder to the real `MANIFEST.md` path before it could
    even compute `bundle_id`, so a protected-path edit landing in that
    window left the real file on disk stating an identifier the function
    itself was about to declare wrong)."""
    entries: dict[str, dict] = {}
    for p in sorted(bundle_dir.rglob("*")):
        rel = p.relative_to(bundle_dir).as_posix()
        if p.is_symlink():
            raise UnsupportedPathTypeError(rel)
        if p.is_dir():
            continue
        if not p.is_file():
            raise UnsupportedPathTypeError(rel)
        mode = "100755" if _owner_executable(p.stat().st_mode) else "100644"
        if rel == MANIFEST_FILENAME and manifest_content_override is not None:
            content = manifest_content_override
        else:
            content = p.read_bytes()
        if rel == "CHANGED_FILES.txt":
            content = _normalize_changed_files_header(content)
        if rel == MANIFEST_FILENAME:
            content = _strip_manifest_self_reference(content)
        else:
            _reject_foreign_bundle_id_field(rel, content)
        entries[rel] = {"mode": mode, "sha256": hashlib.sha256(content).hexdigest()}
    if manifest_content_override is not None and MANIFEST_FILENAME not in entries:
        # MANIFEST.md doesn't exist on disk yet (first-ever generation for
        # this bundle directory) -- represent it from the override alone,
        # as a fresh, non-executable text file.
        stripped = _strip_manifest_self_reference(manifest_content_override)
        entries[MANIFEST_FILENAME] = {"mode": "100644", "sha256": hashlib.sha256(stripped).hexdigest()}
    missing_required = sorted(REQUIRED_BUNDLE_FILES - entries.keys())
    if missing_required:
        raise MissingRequiredBundleFileError(missing_required)
    digest = hashlib.sha256(_canonical_json(entries)).hexdigest()
    return digest, entries


def render_manifest_md(
    *,
    review_content_id: str,
    protected: frozenset[str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
    bundle_id: str | None = None,
    work_item_id: str | None = None,
    work_item_type: str | None = None,
    plan_revision: int | None = None,
    base_commit: str | None = None,
    worktree_root: str | None = None,
    generation_head: str | None = None,
) -> str:
    """Render `MANIFEST.md` content. If `bundle_id` is supplied, it is
    written using the exact contract `_strip_bundle_id_field` recognizes
    (`bundle_id: <64-hex-chars>`), so writing it in after computing
    `compute_bundle_id()` never invalidates the value just computed --
    the field is excluded from the hash by construction, not by
    convention.

    `work_item_id`/`work_item_type`/`plan_revision`/`base_commit`, if
    supplied, are written as plain `field: value` header lines
    (`D-Fingerprint-Generalization`, `OPUS-R25-005`/`OPUS-R26-003`) --
    ordinary hashed bundle content for `bundle_id` like every other bundle
    file, never part of `review_content_id` (that digest is computed
    before rendering, over the protected-path manifest only, unchanged).
    A bundle with no self-declared `work_item_id` is exactly what let
    `OPUS-R25-005`'s failure scenario stay silent -- `write_manifest_with_verified_identifiers`
    reads this same field back (`_read_manifest_binding_fields`) to bind a
    directory to the work item it belongs to.

    `worktree_root`/`generation_head`, if supplied, are written as plain
    diagnostic lines (`worktree_root: <path>` / `generation_head: <sha>`)
    -- neither is part of any identity-bearing field pattern, so they are
    ordinary hashed bundle content for `bundle_id` and never touch
    `review_content_id` at all. Repository-local commands read them back
    via `assert_local_generation_matches`; an external reviewer treats
    them as informational only (`WFR-17`)."""
    # `stage: plan` is hardcoded, not a parameter: this function has
    # exactly one production caller (`write_manifest_with_verified_
    # identifiers`, the plan-stage writer) -- the implementation-stage
    # counterpart is `render_manifest_md_implementation_stage`, an
    # independent function per this codebase's established per-stage
    # split (`OPUS-R20-003`), never a shared code path with a stage
    # parameter a caller could pass wrong (`GPT-R30-002`).
    lines = ["# Bundle Manifest", "", "stage: plan"]
    if bundle_id is not None:
        lines.append(f"bundle_id: {bundle_id}")
    lines.append(f"review_content_id: {review_content_id}")
    if work_item_id is not None:
        lines.append(f"work_item_id: {work_item_id}")
    if work_item_type is not None:
        lines.append(f"work_item_type: {work_item_type}")
    if plan_revision is not None:
        lines.append(f"plan_revision: {plan_revision}")
    if base_commit is not None:
        lines.append(f"base_commit: {base_commit}")
    if worktree_root is not None:
        lines.append(f"worktree_root: {worktree_root}")
    if generation_head is not None:
        lines.append(f"generation_head: {generation_head}")
    lines.append("")
    lines.append("## Protected paths")
    for path in sorted(protected):
        lines.append(f"- {path}")
    lines.append("")
    lines.append("## Excluded paths (exact match)")
    for path, reason in sorted(excluded_paths.items()):
        lines.append(f"- `{path}` — {reason}")
    lines.append("")
    lines.append("## Excluded prefixes (directories)")
    if excluded_prefixes:
        for path, reason in sorted(excluded_prefixes.items()):
            lines.append(f"- `{path}` — {reason}")
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


_MANIFEST_FIELD_RE = re.compile(r"^(bundle_id|review_content_id): ([0-9a-f]{64})$")
_MANIFEST_WORK_ITEM_ID_LINE_RE = re.compile(r"^work_item_id: (\S+)$", re.MULTILINE)
_MANIFEST_BASE_COMMIT_LINE_RE = re.compile(r"^base_commit: ([0-9a-f]{40})$", re.MULTILINE)


def _read_manifest_binding_fields(manifest_path: Path) -> dict[str, str | None]:
    """Read `MANIFEST.md`'s `work_item_id:`/`base_commit:` **header line**
    specifically -- never a substring scan (`OPUS-R25-005`: this
    repository's own real manifest names `work_item_id` only inside an
    unrelated exclusion-justification sentence, a decoy a naive substring
    reader would false-positive on). `None` for a field with no such
    line -- including a wholly absent file."""
    if not manifest_path.is_file():
        return {"work_item_id": None, "base_commit": None}
    content = manifest_path.read_text()
    wid_match = _MANIFEST_WORK_ITEM_ID_LINE_RE.search(content)
    base_match = _MANIFEST_BASE_COMMIT_LINE_RE.search(content)
    return {
        "work_item_id": wid_match.group(1) if wid_match else None,
        "base_commit": base_match.group(1) if base_match else None,
    }


def _assert_manifest_binding_agrees(
    manifest_path: Path, *, work_item_id: str, base_commit: str,
) -> None:
    """Fail-closed matrix conditions 12/13: an *existing* `MANIFEST.md`
    must either be absent (the ordinary first-write case, handled by the
    caller) or already agree with the resolved work item on both
    `work_item_id` and `base_commit`. Disagreement -- including "present
    but declares no `work_item_id` at all" (an unbound/legacy manifest) --
    refuses, naming the resolved item, the directory, and either the
    disagreeing value or "unbound"."""
    if not manifest_path.is_file():
        return
    recorded = _read_manifest_binding_fields(manifest_path)
    recorded_work_item_id = recorded["work_item_id"]
    if recorded_work_item_id is None:
        raise BundleWorkItemMismatchError(
            f"{manifest_path} exists but declares no work_item_id (unbound); "
            f"expected {work_item_id!r}"
        )
    if recorded_work_item_id != work_item_id:
        raise BundleWorkItemMismatchError(
            f"{manifest_path} is bound to work_item_id {recorded_work_item_id!r}, "
            f"expected {work_item_id!r}"
        )
    recorded_base_commit = recorded["base_commit"]
    if recorded_base_commit is not None and recorded_base_commit != base_commit:
        raise BundleWorkItemMismatchError(
            f"{manifest_path} is bound to base_commit {recorded_base_commit!r}, "
            f"expected {base_commit!r} for work item {work_item_id!r}"
        )


def read_manifest_identifiers(manifest_path: Path) -> dict[str, str]:
    """Read whichever of `bundle_id`/`review_content_id` a `MANIFEST.md`
    currently states, without writing anything -- the read-only
    counterpart callers use to report an existing bundle's recorded
    identifiers alongside a freshly recomputed value (`OPUS-R18-002`)."""
    if not manifest_path.is_file():
        return {}
    fields: dict[str, str] = {}
    for line in manifest_path.read_text().splitlines():
        match = _MANIFEST_FIELD_RE.match(line)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


_MANIFEST_STAGE_LINE_RE = re.compile(r"^stage: (\S+)$", re.MULTILINE)
_MANIFEST_PLAN_REVISION_LINE_RE = re.compile(r"^plan_revision: ([0-9]+)$", re.MULTILINE)


def read_plan_stage_manifest_fields(manifest_path: Path) -> dict:
    """Read-only (workflow-2.6.0, `D-Plan-Review-Bundle-Binding`): the
    header fields `workflow_state.verify_plan_review_bundle` checks --
    `stage`, `work_item_id`, `plan_revision` (an `int`), `bundle_id` and
    `review_content_id`. A field with no header line is `None`; so is
    every field of an absent file."""
    fields: dict = {
        "stage": None, "work_item_id": None, "plan_revision": None,
        "bundle_id": None, "review_content_id": None,
    }
    if not manifest_path.is_file():
        return fields
    content = manifest_path.read_text()
    stage_match = _MANIFEST_STAGE_LINE_RE.search(content)
    revision_match = _MANIFEST_PLAN_REVISION_LINE_RE.search(content)
    fields["stage"] = stage_match.group(1) if stage_match else None
    fields["work_item_id"] = _read_manifest_binding_fields(manifest_path)["work_item_id"]
    fields["plan_revision"] = int(revision_match.group(1)) if revision_match else None
    fields.update(read_manifest_identifiers(manifest_path))
    return fields


def read_plan_stage_manifest_base_commit(manifest_path: Path) -> str | None:
    """Read-only (workflow-2.6.0, `D-Plan-Approval-Closure`): the
    `base_commit:` header line of a bundle's `MANIFEST.md` -- the commit the
    generator diffed against to decide which paths it captured under
    `files/`. `None` for an absent file or line."""
    return _read_manifest_binding_fields(manifest_path)["base_commit"]


def read_plan_stage_manifest_protected_paths(manifest_path: Path) -> frozenset[str] | None:
    """Read-only (workflow-2.6.0, `D-Plan-Approval-Closure`): the entries of
    a plan-stage `MANIFEST.md`'s `## Protected paths` section, i.e. the
    declared protected set the reviewer saw. `None` when the file or the
    section is absent, so a caller can refuse rather than read "nothing was
    protected"."""
    if not manifest_path.is_file():
        return None
    section: set[str] | None = None
    for line in manifest_path.read_text().splitlines():
        if line.startswith("## "):
            if section is not None:
                break
            if line.strip() == "## Protected paths":
                section = set()
            continue
        if section is not None and line.startswith("- "):
            section.add(line[2:].strip())
    return frozenset(section) if section is not None else None


def compute_archived_bundle_id(archive_path: Path) -> str:
    """`compute_bundle_id` over the `current/` member of a bundle archive,
    extracted into a private temporary directory (read-only with respect
    to the repository). Raises if the archive is absent or unreadable, or
    its `current/` does not hash."""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive_path) as tf:
            try:
                tf.extractall(tmp, filter="data")
            except TypeError:  # a Python without extraction filters
                tf.extractall(tmp)
        bundle_id, _ = compute_bundle_id(Path(tmp) / "current")
    return bundle_id


_REVIEW_CONTENT_ID_STATEMENT_RE = re.compile(r"^review_content_id: ([0-9a-f]{64})$", re.MULTILINE)


def assert_review_request_states_review_content_id(bundle_dir: Path, review_content_id: str) -> None:
    """`REVIEW_REQUEST.md` must state the same `review_content_id` as
    `MANIFEST.md` -- a second, checkable copy that doesn't depend on the
    one file capable of being silently mutated (`OPUS-R18-002`). This is
    a labelled `review_content_id: <hex>` line, not a `bundle_id:
    <hex>`-shaped one -- `GPT-R9-001`'s one-location rule is specifically
    about that field pattern and is not violated here (`OPUS-R18-005`)."""
    review_request_path = bundle_dir / "REVIEW_REQUEST.md"
    content = review_request_path.read_text() if review_request_path.is_file() else ""
    matches = _REVIEW_CONTENT_ID_STATEMENT_RE.findall(content)
    if not matches:
        raise MissingReviewContentIdStatementError(str(review_request_path))
    if len(set(matches)) > 1:
        raise ReviewContentIdMismatchError(
            f"{review_request_path} states disagreeing review_content_id values: {sorted(set(matches))}"
        )
    stated = matches[0]
    if stated != review_content_id:
        raise ReviewContentIdMismatchError(
            f"{review_request_path} states review_content_id={stated!r}, "
            f"manifest computes {review_content_id!r}"
        )


def write_manifest_with_verified_identifiers(
    repo_root: Path,
    bundle_dir: Path,
    base: str,
    work_item_type: str,
    work_item_id: str,
    plan_revision: int,
    protected: frozenset[str] = PLAN_STAGE_PROTECTED,
    excluded_paths: Mapping[str, str] = PLAN_STAGE_EXCLUDED_PATHS,
    excluded_prefixes: Mapping[str, str] = PLAN_STAGE_EXCLUDED_PREFIXES,
    *,
    allow_rebind: bool = False,
    pin_dir: Path | None = None,
) -> tuple[str, str]:
    """The **only** code path allowed to write `MANIFEST.md` (`OPUS-R18-002`
    — every other entry point, including the CLI's default invocation, is
    read-only). Computes both identifiers last, writes them, then
    recomputes each from the final artifact and asserts equality before
    returning — the `bundle_id` discipline round 8 established, now
    applied symmetrically to `review_content_id` too (`OPUS-R18-001`):
    a protected-path edit landing between the first computation and this
    recompute-and-assert step is caught here rather than silently
    surviving into a written manifest. Also asserts `REVIEW_REQUEST.md`
    already states the same `review_content_id` **before any write
    happens** (`OPUS-R18-005`) — checked first, not last, so a stale
    `REVIEW_REQUEST.md` fails closed without leaving `MANIFEST.md`
    partially rewritten.

    **`pin_dir`** (`WFR-67`, generator-side stage-document binding): when
    given, the *first* `review_content_id` computation below sources its
    manifest from this pinned snapshot (`compute_review_content_id_plan_stage_from_pin`)
    instead of a fresh worktree read — the same pin `PLAN.md` was already
    derived from earlier in this generation run. The recompute-and-assert
    step below is unchanged: it always re-reads the **live worktree**, so
    a pin-vs-live disagreement here is exactly the staleness part 3's
    closing re-read exists to catch, reusing this function's own
    pre-existing idempotence machinery rather than a second, separate
    check. `None` (the default) preserves this function's original,
    always-live-read behavior for every other caller.

    **Write sequence is atomic across the idempotence check itself**
    (resolves `OPUS-R20-001`, missing-test item 138): every identifier
    below is computed and recomputed purely in memory, via
    `compute_bundle_id`'s `manifest_content_override` — nothing is
    written to `manifest_path` at all until both recompute-and-assert
    steps have already passed. A protected-path edit landing anywhere in
    that window still raises exactly as before, but now leaves the real
    `MANIFEST.md` completely untouched rather than holding a placeholder
    or a since-falsified value. The final write itself goes through a
    temp file in the same directory and `os.replace()`, so even the write
    step cannot leave a partially-written file behind.

    **Bound to an explicit work item** (`D-Fingerprint-Generalization`,
    `OPUS-R25-005`): before any of the above, an *existing* `MANIFEST.md`
    at `bundle_dir` must either be absent or already agree with
    `work_item_id`/the resolved `base_commit` -- disagreement, or a
    present-but-unbound manifest (no `work_item_id:` line at all, the
    state of every manifest predating this revision), refuses with
    `BundleWorkItemMismatchError` rather than silently proceeding. This
    check is skipped entirely when `allow_rebind=True` -- the one
    legitimate exception, scoped to the one-time migration that binds a
    currently-unbound bundle directory to the work item it has always
    actually belonged to, never the ordinary write path's default
    behavior. Returns `(review_content_id, bundle_id)`."""
    manifest_path = bundle_dir / MANIFEST_FILENAME
    base_full = resolve_base(repo_root, base)

    if not allow_rebind:
        _assert_manifest_binding_agrees(manifest_path, work_item_id=work_item_id, base_commit=base_full)

    if pin_dir is not None:
        digest, _projection = compute_review_content_id_plan_stage_from_pin(
            repo_root, pin_dir, base_full, work_item_type=work_item_type, work_item_id=work_item_id,
            plan_revision=plan_revision, protected=protected,
            excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        )
    else:
        digest, _projection = compute_review_content_id_plan_stage(
            repo_root, base_full, work_item_type=work_item_type, work_item_id=work_item_id,
            plan_revision=plan_revision, protected=protected,
            excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        )
    assert_review_request_states_review_content_id(bundle_dir, digest)

    # Computed once and reused for both renders below, so the placeholder
    # and final MANIFEST.md content never disagree with each other on
    # worktree_root/generation_head purely because of when in this
    # function's own execution each was read (WFR-17).
    worktree_root, generation_head = current_worktree_root_and_head(repo_root)

    placeholder_content = render_manifest_md(
        review_content_id=digest, protected=protected,
        excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        work_item_id=work_item_id, work_item_type=work_item_type,
        plan_revision=plan_revision, base_commit=base_full,
        worktree_root=worktree_root, generation_head=generation_head,
    ).encode()
    bundle_id, _entries = compute_bundle_id(
        bundle_dir, manifest_content_override=placeholder_content
    )

    final_content = render_manifest_md(
        review_content_id=digest, protected=protected,
        excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        bundle_id=bundle_id,
        work_item_id=work_item_id, work_item_type=work_item_type,
        plan_revision=plan_revision, base_commit=base_full,
        worktree_root=worktree_root, generation_head=generation_head,
    )

    recomputed_bundle_id, _ = compute_bundle_id(
        bundle_dir, manifest_content_override=final_content.encode()
    )
    if recomputed_bundle_id != bundle_id:
        raise BundleIdNotIdempotentError(bundle_id, recomputed_bundle_id)

    recomputed_digest, _ = compute_review_content_id_plan_stage(
        repo_root, base, work_item_type=work_item_type, work_item_id=work_item_id,
        plan_revision=plan_revision, protected=protected,
        excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
    )
    if recomputed_digest != digest:
        raise ReviewContentIdNotIdempotentError(digest, recomputed_digest)

    tmp_path = bundle_dir / f".{MANIFEST_FILENAME}.tmp-{os.getpid()}"
    tmp_path.write_text(final_content)
    os.replace(tmp_path, manifest_path)

    return digest, bundle_id


REQUIRED_GENERATION_FILES: frozenset[str] = frozenset(
    {"REVIEW_REQUEST.md", "PLAN.md", "DIFF.patch", "TEST_RESULTS.md"}
)


def write_manifest_with_verified_identifiers_for_work_item(
    repo_root: Path, work_item_id: str, *, base: str | None = None, allow_rebind: bool = False,
    bundle_dir: Path | None = None, pin_dir: Path | None = None,
) -> tuple[str, str]:
    """Work-item-generic entry point for `write_manifest_with_verified_identifiers`
    (`D-Fingerprint-Generalization`): resolves `bundle_dir` internally as
    `.ai-review/<work_item_id>/current` by direct templating — never
    `resolve_bundle_dir`'s existence-gated scoped-else-flat fallback, so
    the write path can never target the wrong (flat) directory for a new
    item. Does **not** create that directory or anything inside it: the
    bind precondition is that the resolved directory already contains the
    complete required generation file set `prepare-ai-review.sh` (or an
    equivalent full-generation step) writes — `REVIEW_REQUEST.md`,
    `PLAN.md`, `DIFF.patch`, `TEST_RESULTS.md` — whether that directory is
    entirely absent (every required file trivially missing) or exists but
    incomplete (an interrupted prior generation run); both raise
    `MissingRequiredBundleFileError`, naming the first missing file, never
    a distinct "creates it" outcome (`GPT-R29-002`, acceptance criterion
    22). `base`, if given, overrides the resolved item's own declared
    `base_commit` (used for the one-time migration rebind, where the
    bundle's real base commit is `workflow-v2-1-core`'s own -- never a
    silent default for any other caller).

    **Pin auto-detection** (`WFR-67`): if `capture_plan_stage_pin` already
    ran for this work item in the same generation (`.ai-review/<work_item_id>/.pin`
    exists), it is passed through to `write_manifest_with_verified_identifiers`
    automatically -- no separate flag needed, and no behavior change for
    any caller that never captured one (the ordinary case for every
    existing test and for every non-plan-stage caller).

    **Staging** (workflow-2.6.0, `D-Plan-Review-Bundle-Binding` item 5):
    `bundle_dir`/`pin_dir`, when given, name a staged plan-stage
    generation's own bundle directory and pin (`plan_stage_staging_paths`)
    instead of the in-place ones. The staging directory holds no manifest
    yet, so the work-item/base binding check (fail-closed matrix
    conditions 12/13) is run against the live `current/MANIFEST.md` the
    staging generation will replace -- the same check, at the same
    moment, as an in-place generation."""
    resolved_bundle_dir = bundle_dir if bundle_dir is not None else repo_root / ".ai-review" / work_item_id / "current"
    missing = sorted(
        f for f in REQUIRED_GENERATION_FILES if not (resolved_bundle_dir / f).is_file()
    )
    if missing:
        raise MissingRequiredBundleFileError(missing)

    metadata = resolve_plan_stage_metadata(repo_root, work_item_id)
    base_to_use = base if base is not None else metadata.base_commit
    if pin_dir is None:
        pin_dir = _pin_dir_for_work_item(repo_root, work_item_id)
    if bundle_dir is not None and not allow_rebind:
        _assert_manifest_binding_agrees(
            repo_root / ".ai-review" / work_item_id / "current" / MANIFEST_FILENAME,
            work_item_id=work_item_id, base_commit=resolve_base(repo_root, base_to_use),
        )

    return write_manifest_with_verified_identifiers(
        repo_root, resolved_bundle_dir, base_to_use,
        metadata.work_item_type, metadata.work_item_id, metadata.plan_revision,
        metadata.protected_paths, metadata.excluded_paths, metadata.excluded_prefixes,
        allow_rebind=allow_rebind,
        pin_dir=pin_dir if pin_dir.is_dir() else None,
    )


def render_manifest_md_implementation_stage(
    *,
    review_content_id: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
    bundle_id: str | None = None,
    work_item_id: str | None = None,
    work_item_type: str | None = None,
    base_commit: str | None = None,
    reviewed_implementation_head: str | None = None,
    implementation_revision: int | None = None,
    worktree_root: str | None = None,
    generation_head: str | None = None,
) -> str:
    """Implementation-stage counterpart of `render_manifest_md` — an
    independent function, not a shared code path with a `stage`
    parameter a caller could pass wrong, matching `classify_path_
    implementation_stage`'s own precedent (`OPUS-R20-003`). The
    implementation-stage classification is a four-set (protected paths,
    protected prefixes, excluded paths, excluded prefixes) mapping to
    justification strings, so every section is rendered with its
    justification, and there is a "Protected prefixes" section the plan
    stage has no counterpart for. Always states `stage: implementation`
    (`GPT-R30-002`) — this function has no other caller. `
    reviewed_implementation_head`, if supplied, is
    `WORKFLOW_STATE.json`'s own
    `work_items[work_item_id].reviewed_implementation_head` at generation
    time: the commit whose protected implementation-stage content this
    round reviews, and the exact value `/approve-review implementation`
    writes into `technical_approval.reviewed_content_commit` (salvage
    audit `I1`). It is deliberately **not** the commit `review_content_id`
    was measured at — under `D-Approval-Commits`' own ordering the
    durability commit always lands *before* generation, so the measured
    commit is at least one commit ahead of the reviewed round's head, and
    further ahead after a same-content republication. The measured commit
    is `generation_head`, which already carried exactly that value, so
    this field previously duplicated it under a name that contradicted
    `WORKFLOW_STATE.json`'s field of the same name. Still a plain header
    line, never part of the hashed projection itself, same
    diagnostic-only discipline as `worktree_root`/`generation_head`
    (`WFR-17`). `implementation_revision`, if supplied, is
    `work_items[work_item_id].implementation_revision` at generation time
    -- another plain, non-hashed header line (never part of the hashed
    projection, matching `record_bundle_generation`'s own "never part of
    either fingerprint projection" discipline), durably recording each
    generation round's own revision so the *next* generation's own
    round-identity preflight (`scripts/prepare-ai-review.sh`, GPT-R43-001)
    has something to compare against without depending on
    `WORKFLOW_STATE.json` history."""
    lines = ["# Bundle Manifest", "", "stage: implementation"]
    if bundle_id is not None:
        lines.append(f"bundle_id: {bundle_id}")
    lines.append(f"review_content_id: {review_content_id}")
    if work_item_id is not None:
        lines.append(f"work_item_id: {work_item_id}")
    if work_item_type is not None:
        lines.append(f"work_item_type: {work_item_type}")
    if base_commit is not None:
        lines.append(f"base_commit: {base_commit}")
    if reviewed_implementation_head is not None:
        lines.append(f"reviewed_implementation_head: {reviewed_implementation_head}")
    if implementation_revision is not None:
        lines.append(f"implementation_revision: {implementation_revision}")
    if worktree_root is not None:
        lines.append(f"worktree_root: {worktree_root}")
    if generation_head is not None:
        lines.append(f"generation_head: {generation_head}")
    lines.append("")
    lines.append("## Protected paths")
    if protected_paths:
        for path, reason in sorted(protected_paths.items()):
            lines.append(f"- `{path}` — {reason}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Protected prefixes (directories)")
    if protected_prefixes:
        for path, reason in sorted(protected_prefixes.items()):
            lines.append(f"- `{path}` — {reason}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Excluded paths (exact match)")
    if excluded_paths:
        for path, reason in sorted(excluded_paths.items()):
            lines.append(f"- `{path}` — {reason}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Excluded prefixes (directories)")
    if excluded_prefixes:
        for path, reason in sorted(excluded_prefixes.items()):
            lines.append(f"- `{path}` — {reason}")
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def write_manifest_with_verified_identifiers_implementation_stage(
    repo_root: Path,
    bundle_dir: Path,
    base: str,
    head: str,
    work_item_type: str,
    work_item_id: str,
    protected_paths: Mapping[str, str],
    protected_prefixes: Mapping[str, str],
    excluded_paths: Mapping[str, str],
    excluded_prefixes: Mapping[str, str],
    *,
    allow_rebind: bool = False,
    implementation_revision: int | None = None,
    reviewed_implementation_head: str | None = None,
) -> tuple[str, str]:
    """Implementation-stage counterpart of `write_manifest_with_verified_
    identifiers` (`GPT-R30-001`/`-002`) — the only code path allowed to
    write an implementation-stage `MANIFEST.md`. `review_content_id` is
    computed via `compute_review_content_id_implementation_stage_at_commit`,
    commit-source and anchored at `head` (the final reviewed
    implementation HEAD) rather than worktree-source, so the identifier
    is reproducible from a fresh clone or archive extraction and does not
    depend on uncommitted local state — the defect `GPT-R30-001` reported
    (a stale, hand-carried plan-stage manifest reused for an
    implementation bundle) cannot recur, since this is the only writer
    and it always recomputes both identifiers fresh, in memory, before
    any write, exactly mirroring the plan-stage writer's own write
    sequence (idempotence-checked, atomic replace). `head` is the commit
    the digest is measured at and becomes the diagnostic-only
    `generation_head`; the separate `reviewed_implementation_head`
    keyword carries `WORKFLOW_STATE.json`'s own field of that name, a
    *different* commit under `D-Approval-Commits`' own ordering (salvage
    audit `I1`) — passing `None` omits the line rather than guessing a
    value. Returns `(review_content_id, bundle_id)`."""
    manifest_path = bundle_dir / MANIFEST_FILENAME
    base_full = resolve_base(repo_root, base)
    head_full = resolve_base(repo_root, head)

    if not allow_rebind:
        _assert_manifest_binding_agrees(manifest_path, work_item_id=work_item_id, base_commit=base_full)

    digest, _projection = compute_review_content_id_implementation_stage_at_commit(
        repo_root, base_full, head_full, work_item_type, work_item_id,
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
    )
    assert_review_request_states_review_content_id(bundle_dir, digest)

    worktree_root, generation_head = current_worktree_root_and_head(repo_root)

    placeholder_content = render_manifest_md_implementation_stage(
        review_content_id=digest, protected_paths=protected_paths, protected_prefixes=protected_prefixes,
        excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        work_item_id=work_item_id, work_item_type=work_item_type, base_commit=base_full,
        reviewed_implementation_head=reviewed_implementation_head,
        implementation_revision=implementation_revision,
        worktree_root=worktree_root, generation_head=generation_head,
    ).encode()
    bundle_id, _entries = compute_bundle_id(
        bundle_dir, manifest_content_override=placeholder_content
    )

    final_content = render_manifest_md_implementation_stage(
        review_content_id=digest, protected_paths=protected_paths, protected_prefixes=protected_prefixes,
        excluded_paths=excluded_paths, excluded_prefixes=excluded_prefixes,
        bundle_id=bundle_id,
        work_item_id=work_item_id, work_item_type=work_item_type, base_commit=base_full,
        reviewed_implementation_head=reviewed_implementation_head,
        implementation_revision=implementation_revision,
        worktree_root=worktree_root, generation_head=generation_head,
    )

    recomputed_bundle_id, _ = compute_bundle_id(
        bundle_dir, manifest_content_override=final_content.encode()
    )
    if recomputed_bundle_id != bundle_id:
        raise BundleIdNotIdempotentError(bundle_id, recomputed_bundle_id)

    recomputed_digest, _ = compute_review_content_id_implementation_stage_at_commit(
        repo_root, base_full, head_full, work_item_type, work_item_id,
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
    )
    if recomputed_digest != digest:
        raise ReviewContentIdNotIdempotentError(digest, recomputed_digest)

    tmp_path = bundle_dir / f".{MANIFEST_FILENAME}.tmp-{os.getpid()}"
    tmp_path.write_text(final_content)
    os.replace(tmp_path, manifest_path)

    return digest, bundle_id


def write_manifest_with_verified_identifiers_implementation_stage_for_work_item(
    repo_root: Path, work_item_id: str, base: str, *, head: str = "HEAD", allow_rebind: bool = False,
) -> tuple[str, str]:
    """Work-item-generic entry point for `write_manifest_with_verified_
    identifiers_implementation_stage`, mirroring the plan-stage wrapper's
    own bind precondition: the resolved `.ai-review/<work_item_id>/current`
    directory must already contain the complete required generation file
    set (`REQUIRED_GENERATION_FILES`) before this call, or it refuses with
    `MissingRequiredBundleFileError`, naming the first missing file —
    never a distinct "creates it" outcome. `base` is required, with no
    resolved default: unlike the plan stage, there is no `WORKFLOW_STATE.json`-
    declared implementation-stage base commit to fall back to — the only
    real caller (`prepare-ai-review.sh`) already has it, as every stage's
    own required first positional argument."""
    resolved_bundle_dir = repo_root / ".ai-review" / work_item_id / "current"
    missing = sorted(
        f for f in REQUIRED_GENERATION_FILES if not (resolved_bundle_dir / f).is_file()
    )
    if missing:
        raise MissingRequiredBundleFileError(missing)

    work_items, _active = _load_workflow_state_work_items(repo_root, None)
    entry = work_items.get(work_item_id)
    if entry is None:
        raise UnknownWorkItemError(work_item_id)
    work_item_type = entry.get("work_item_type")
    if work_item_type is None:
        raise UnknownWorkItemError(f"work_items[{work_item_id!r}].work_item_type is null")

    artifacts_path = artifacts_path_for_work_item(work_item_id)
    protected_paths, protected_prefixes, excluded_paths, excluded_prefixes = (
        load_implementation_stage_classification(repo_root, artifacts_path)
    )

    return write_manifest_with_verified_identifiers_implementation_stage(
        repo_root, resolved_bundle_dir, base, head, work_item_type, work_item_id,
        protected_paths, protected_prefixes, excluded_paths, excluded_prefixes,
        allow_rebind=allow_rebind, implementation_revision=entry.get("implementation_revision"),
        reviewed_implementation_head=entry.get("reviewed_implementation_head"),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Compute a work item's plan-stage review_content_id/bundle_id "
            "(D-Fingerprint-Generalization). Read-only by default "
            "(OPUS-R18-002): prints identifiers and writes nothing. Pass "
            "--write-manifest to (re)generate MANIFEST.md -- the one "
            "invocation that mutates the bundle directory; a reviewer "
            "inspecting a bundle should never need it."
        )
    )
    parser.add_argument(
        "base", nargs="?", default=None,
        help=(
            "base commit/ref override (default: the resolved work item's "
            "own declared base_commit -- never a literal naming any other "
            "item, OPUS-R26-003)"
        ),
    )
    parser.add_argument(
        "--write-manifest", action="store_true",
        help="write MANIFEST.md (the only invocation that writes anything)",
    )
    parser.add_argument(
        "--work-item-id", default=None,
        help=(
            "work item to resolve. For the read-only inspection path, "
            "omitted resolves to the live active_work_item_id "
            "(WORKFLOW_STATE.json), never a hardcoded literal "
            "(OPUS-R25-005). For --write-manifest, this argument is "
            "required and is never defaulted (OPUS-R28-010)."
        ),
    )
    parser.add_argument(
        "--rebind", action="store_true",
        help=(
            "one-time migration use only: bind an existing, currently-"
            "unbound MANIFEST.md to --work-item-id instead of refusing on "
            "disagreement. Never the ordinary write path's default."
        ),
    )
    parser.add_argument(
        "--stage", choices=["plan", "implementation"], default="plan",
        help=(
            "which manifest to (re)generate with --write-manifest "
            "(GPT-R30-001/002). 'plan' (default) is the only mode every "
            "existing caller uses. 'implementation' requires the "
            "positional base argument (no resolved default -- the caller "
            "always already has it) and anchors review_content_id at the "
            "current HEAD, recorded as reviewed_implementation_head."
        ),
    )
    parser.add_argument(
        "--derive-plan-stage-document", action="store_true",
        help=(
            "WFR-67 generator-side stage-document binding, plan-stage-only "
            "pre-generation step: captures a private pinned snapshot of "
            "every plan-stage protected path and derives bundle_dir/PLAN.md "
            "unconditionally from it. Must run before any other file under "
            "bundle_dir is written; --write-manifest later in the same "
            "generation run automatically consumes the same pin. Requires "
            "--work-item-id."
        ),
    )
    parser.add_argument(
        "--finalize-bundle", nargs=2, metavar=("BUNDLE_DIR", "ARCHIVE_PATH"),
        help=(
            "closing half of one generation run (WFR-67): the pre-existing "
            "three-way bundle_id reproducibility check, the plan-stage "
            "byte-identity binding check when a pin was captured, REJECTED-"
            "marker withdrawal on failure, and REJECTED-marker clearing on "
            "success. --generation-stage/--work-item-id select the mode."
        ),
    )
    parser.add_argument(
        "--generation-stage", choices=["plan", "implementation", "post-fix", "functional-review"],
        default="plan",
        help="the bundle stage being finalized with --finalize-bundle (mirrors prepare-ai-review.sh's own $STAGE)",
    )
    parser.add_argument(
        "--staging-token", default=None,
        help=(
            "workflow-2.6.0 plan-stage staging (D-Plan-Review-Bundle-Binding): "
            "with --derive-plan-stage-document, --write-manifest (plan stage) "
            "or --finalize-bundle --generation-stage plan, act on the staged "
            "generation .ai-review/<id>/current.staging-<token>/ and its "
            ".pin.staging-<token>/ instead of the in-place current/ and .pin. "
            "--finalize-bundle then promotes on success and discards the "
            "staging area on failure (no withdrawal, no REJECTED marker)."
        ),
    )
    parser.add_argument(
        "--resolve-feedback-path", metavar="WORK_ITEM_ID", default=None,
        help=(
            "D-Feedback-Layout's machine-readable contract: print one JSON "
            "object {work_item_id, layout, feedback_dir, review_feedback_path, "
            "functional_review_path} for WORK_ITEM_ID (repo-root-relative "
            "POSIX paths; layout is scoped, legacy-scoped or legacy-flat) "
            "and exit. Read-only: creates nothing."
        ),
    )
    args = parser.parse_args()

    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    )

    if args.resolve_feedback_path is not None:
        print(json.dumps(resolve_feedback_path_contract(repo_root, args.resolve_feedback_path), sort_keys=True))
        raise SystemExit(0)

    if args.derive_plan_stage_document:
        if not args.work_item_id:
            raise SystemExit(
                "error: --work-item-id is required with --derive-plan-stage-document"
            )
        bundle_dir = repo_root / ".ai-review" / args.work_item_id / "current"
        staging_pin_dir = None
        if args.staging_token is not None:
            staging = plan_stage_staging_paths(repo_root, args.work_item_id, args.staging_token)
            bundle_dir, staging_pin_dir = staging["bundle_dir"], staging["pin_dir"]
        metadata = resolve_plan_stage_metadata(repo_root, args.work_item_id)
        pin_dir = capture_plan_stage_pin(repo_root, args.work_item_id, metadata, pin_dir=staging_pin_dir)
        derive_plan_stage_document(pin_dir, bundle_dir, metadata)
        print("=== derived plan-stage document (WFR-67) ===")
        print(f"work_item_id: {args.work_item_id}")
        print(f"pinned snapshot: {pin_dir}")
        print(f"wrote: {bundle_dir / 'PLAN.md'}")
        raise SystemExit(0)

    if args.finalize_bundle and args.staging_token is not None:
        if args.generation_stage != "plan" or not args.work_item_id:
            raise SystemExit(
                "error: --staging-token with --finalize-bundle requires "
                "--generation-stage plan and --work-item-id"
            )
        result = finalize_staged_plan_bundle_generation(repo_root, args.work_item_id, args.staging_token)
        print(f"status: {result['status']}")
        for key, value in result.items():
            if key != "status":
                print(f"{key}: {value}")
        raise SystemExit(0 if result["status"] == "ok" else 1)

    if args.finalize_bundle:
        bundle_dir_arg, archive_arg = args.finalize_bundle
        result = finalize_bundle_generation(
            repo_root, Path(bundle_dir_arg), Path(archive_arg),
            args.generation_stage, args.work_item_id,
        )
        print(f"status: {result['status']}")
        for key, value in result.items():
            if key != "status":
                print(f"{key}: {value}")
        raise SystemExit(0 if result["status"] == "ok" else 1)

    if args.write_manifest:
        if not args.work_item_id:
            raise SystemExit(
                "error: --work-item-id is required with --write-manifest "
                "-- never resolved from the live active_work_item_id on the write path"
            )
        if args.stage == "implementation":
            if not args.base:
                raise SystemExit(
                    "error: the base positional argument is required with "
                    "--write-manifest --stage implementation -- there is no "
                    "resolved implementation-stage base commit to fall back to"
                )
            digest, bundle_id = write_manifest_with_verified_identifiers_implementation_stage_for_work_item(
                repo_root, args.work_item_id, args.base, allow_rebind=args.rebind,
            )
        elif args.staging_token is not None:
            staging = plan_stage_staging_paths(repo_root, args.work_item_id, args.staging_token)
            digest, bundle_id = write_manifest_with_verified_identifiers_for_work_item(
                repo_root, args.work_item_id, base=args.base, allow_rebind=args.rebind,
                bundle_dir=staging["bundle_dir"], pin_dir=staging["pin_dir"],
            )
        else:
            digest, bundle_id = write_manifest_with_verified_identifiers_for_work_item(
                repo_root, args.work_item_id, base=args.base, allow_rebind=args.rebind,
            )
        manifest_path = repo_root / ".ai-review" / args.work_item_id / "current" / MANIFEST_FILENAME
        if args.staging_token is not None and args.stage == "plan":
            manifest_path = plan_stage_staging_paths(repo_root, args.work_item_id, args.staging_token)["bundle_dir"] / MANIFEST_FILENAME
        print(f"=== wrote MANIFEST.md (stage: {args.stage}) ===")
        print(f"work_item_id: {args.work_item_id}")
        print(f"wrote: {manifest_path}")
        print(f"review_content_id (write -> recompute -> equal): {digest}")
        print(f"bundle_id (write -> recompute -> equal): {bundle_id}")
        raise SystemExit(0)

    work_item_id = args.work_item_id
    if work_item_id is None:
        _work_items, active_work_item_id = _load_workflow_state_work_items(repo_root, None)
        if active_work_item_id is None:
            raise SystemExit(
                "error: --work-item-id was omitted and WORKFLOW_STATE.json "
                "has no active_work_item_id to fall back to"
            )
        work_item_id = active_work_item_id

    digest, projection = compute_review_content_id_plan_stage_for_work_item(
        repo_root, work_item_id, base=args.base,
    )
    print("=== compute_review_content_id_plan_stage_for_work_item ===")
    print(f"work_item_id: {work_item_id}")
    print(f"base_commit (resolved, full): {projection['base_commit']}")
    print(f"plan_revision (from registry JSON, cross-checked against plan title): {projection['plan_revision']}")
    print(f"review_content_id: {digest}")
    print(f"protected_paths: {projection['protected_paths']}")
    print(f"excluded_paths: {projection['excluded_paths']}")
    print(f"excluded_prefixes: {projection['excluded_prefixes']}")
    print("review_content_manifest:")
    for entry in projection["review_content_manifest"]:
        print(f"  {entry}")

    bundle_dir = repo_root / resolve_bundle_dir(repo_root, work_item_id, stage="plan")
    manifest_path = bundle_dir / "MANIFEST.md"
    print()

    print("=== read-only bundle inspection (no files written) ===")
    if not bundle_dir.is_dir():
        print(f"no bundle directory at {bundle_dir}")
        raise SystemExit(0)
    existing = read_manifest_identifiers(manifest_path)
    if "review_content_id" in existing:
        match = "matches" if existing["review_content_id"] == digest else "DIFFERS -- protected content changed since MANIFEST.md was last written"
        print(f"MANIFEST.md's recorded review_content_id: {existing['review_content_id']} ({match})")
    else:
        print(f"{manifest_path} has no recorded review_content_id yet -- run with --write-manifest first")
    try:
        bundle_id, entries = compute_bundle_id(bundle_dir)
        print(f"bundle_id (recomputed from {bundle_dir}, not written): {bundle_id}")
        print(f"file count: {len(entries)}")
        if "bundle_id" in existing:
            match = "matches" if existing["bundle_id"] == bundle_id else "DIFFERS -- bundle changed since MANIFEST.md was last written"
            print(f"MANIFEST.md's recorded bundle_id: {existing['bundle_id']} ({match})")
    except MissingRequiredBundleFileError:
        print(f"{manifest_path} does not exist yet -- run with --write-manifest first")
    generation_meta = read_manifest_generation_metadata(manifest_path)
    if generation_meta:
        current_root, current_head = current_worktree_root_and_head(repo_root)
        print(f"MANIFEST.md's recorded worktree_root: {generation_meta.get('worktree_root')} "
              f"(current: {current_root}, "
              f"{'matches' if generation_meta.get('worktree_root') == current_root else 'DIFFERS -- see WorktreeOrHeadMismatchError'})")
        print(f"MANIFEST.md's recorded generation_head: {generation_meta.get('generation_head')} "
              f"(current: {current_head}, "
              f"{'matches' if generation_meta.get('generation_head') == current_head else 'DIFFERS -- see WorktreeOrHeadMismatchError'})")
        print("(diagnostic only here -- a repository-local command like /approve-review "
              "enforces this via assert_local_generation_matches; an external reviewer "
              "consuming a portable archive ignores it, WFR-17)")
    print()
    print(f"This command never writes to {bundle_dir} -- safe to re-run "
          "at any time. Verify from an extracted archive copy for a second, "
          "independent check.")
