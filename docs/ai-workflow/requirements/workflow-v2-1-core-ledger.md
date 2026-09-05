# `workflow-v2-1-core` Requirements Ledger

Mutable, human-readable execution record for the `workflow-v2-1-core` work
item. Physically separate from the immutable, machine-readable
`workflow-v2-1-core-mapping.json` in this same directory (D4b): the mapping
file is the approved requirement↔checkpoint binding and is plan-stage
protected (editing it changes `review_content_id`); this ledger is never a
fingerprint input — see `docs/ai-workflow/requirements/` in
`scripts/workflow_fingerprint.py`'s `PLAN_STAGE_EXCLUDED_PREFIXES`. Editing
this file never stales a plan approval and never requires a fresh review
round.

Each checkpoint appends one section here as it completes: implementation
evidence, verification results, review findings, and (where applicable)
functional-verification outcomes. This is a log, not a status source —
`WORKFLOW_STATE.json`'s `checkpoints[id]` remains the sole record of
checkpoint status (D-Registry); nothing here is re-derived from or
overrides it.

## Checkpoints completed before this ledger existed (WF0–WF2, WF4a-i
through WF4a-iv, WF-M8a, WF-M8b)

This ledger is created at `WF4b`. The ten checkpoints already `COMPLETE` at
that point (`WF0`, `WF1a`, `WF1b`, `WF4a-i`, `WF4a-ii`, `WF4a-iii`,
`WF4a-iv`, `WF-M8a`, `WF-M8b`, `WF2`) have their implementation evidence and
verification results recorded in their own commit messages and
`Workflow-Checkpoint`/`Workflow-Work-Item` trailers (`git log --grep`
against each id remains authoritative for those). They are not
retroactively duplicated here, to avoid a second copy that could drift from
the git history that already carries them. Every checkpoint from `WF4b`
onward appends a section below as it completes.

## `WF4b` — Requirements: immutable mapping file + mutable ledger doc
physically separated (D4b)

- **Implementation evidence:** created this file, physically separate from
  the already-existing `workflow-v2-1-core-mapping.json` (created at
  `WF1b`); no change to the mapping file or the registry.
- **Verification results:** `scripts/workflow_fingerprint_test.py` passes
  unchanged; a new test confirms editing this ledger file does not change
  the plan-stage `review_content_id` (missing-test item 6).
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).

## `WF4c` — General functional-remediation cycle (D-Functional-Remediation)

- **Implementation evidence:** `scripts/workflow_state.py` gains
  `mark_technical_approval_stale` (the bounded-fix branch's
  stale-before-edit write), `record_bundle_generation`
  (`reviewed_implementation_head`'s sole writer, closing the loop
  `OPUS-R6-013` found — nothing wrote this field before this checkpoint,
  so `AWAITING_TECHNICAL_APPROVAL`'s entry condition was unreachable for
  any real item), `create_remediation_child_work_item` (the broad-
  remediation branch: a distinct `<parent>-remediation-<n>` child work
  item, `n` derived deterministically, never mutating the parent's own
  registry/mapping/checkpoint history), and `incomplete_children` (wired
  into `complete_work_item`, which now refuses outright, naming every
  still-incomplete child, resolving `GPT-R9-016`). `validate_state` gained
  a `DanglingParentWorkItemError` check. `.claude/commands/apply-
  functional-review.md` implements the three-way per-finding branch (no
  code change / bounded / broad); `.claude/commands/milestone-implement.md`
  step 4 and `.claude/commands/apply-implementation-review.md` step 7 now
  call `record_bundle_generation` at the `"implementation"`/`"post-fix"`
  bundle-generation points respectively; `.claude/commands/approve-review.md`
  step 1 now states exactly how `head_matches_reviewed_implementation_head`
  is computed; `.claude/commands/accept-milestone.md` gained step 2a,
  calling `workflow_state.complete_work_item` (the parent-completion
  block) before finalizing acceptance. `docs/ai-workflow/MILESTONE_WORKFLOW.md`'s
  `FIXING_FUNCTIONAL_FINDINGS`/`AWAITING_USER_ACCEPTANCE`/`MILESTONE_COMPLETE`
  sections document the same mechanics. No change to the registry or
  mapping file.
- **Verification results:** `python3 scripts/workflow_state_test.py` —
  174/174 pass (17 new: `TestMarkTechnicalApprovalStale`,
  `TestRecordBundleGeneration`, `TestRemediationChildWorkItem`,
  `TestParentCompletionBlocksOnIncompleteChild`,
  `TestDanglingParentWorkItem`). `python3 scripts/workflow_fingerprint_test.py`
  — 91/91 pass, unchanged. Durability guard re-run after all edits: the
  plan-stage `review_content_id` at `base_commit` is unchanged
  (`03e9698c7c017da85e2f7845a341a55334ec8b3b79544bc9d22f057a9f90bb5a`) —
  every file this checkpoint touched is plan-stage excluded
  (`.claude/commands/`, `docs/ai-workflow/MILESTONE_WORKFLOW.md`,
  `scripts/`).
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).

## `WF8b` continued scope — Revision 21 fingerprint-generalization
remediation (`D-Fingerprint-Generalization`, `WF8B-S1-001`)

Not a new checkpoint ID (per the plan's own migration step 11): this is
`WF8b`'s own continued scope, the remediation the `WF8B_S1_FINDING`
(`docs/ai-workflow/dry-run/WF8B_S1_FINDING_review_content_id_not_generalized.md`)
made a precondition for re-attempting S1. `WF8b` itself remains
`IN_PROGRESS`/unexecuted; `active_work_item_id` remains `v2-1-dry-run`.

- **Implementation evidence:**
  - `scripts/workflow_fingerprint.py`: twelve new fail-closed exception
    classes; `PlanStageMetadata` (a `NamedTuple`, never a positional
    tuple); `resolve_plan_stage_metadata` (the thirteen-step, per-work-item
    resolution algorithm, worktree- and commit-source); `artifacts_path_for_work_item`;
    `load_plan_stage_classification` (reads a `<work_item_id>-artifacts.json`'s
    `plan_stage` key); `compute_review_content_id_plan_stage_for_work_item`/
    `_at_commit_for_work_item`; `load_plan_revision`/
    `load_implementation_stage_classification` migrated to per-item,
    required-argument sources (`DEFAULT_REGISTRY_PATH`/`DEFAULT_PLAN_PATH`
    kept only as named migration-comparison fixtures); `render_manifest_md`
    gains `work_item_id`/`work_item_type`/`plan_revision`/`base_commit`
    fields; `write_manifest_with_verified_identifiers` gains the
    manifest work-item/base-commit bind check (`BundleWorkItemMismatchError`,
    conditions 12/13) and an `allow_rebind` escape hatch scoped to the
    one-time migration; `write_manifest_with_verified_identifiers_for_work_item`
    is the new work-item-generic entry point the CLI and
    `prepare-ai-review.sh` both use, with its own first-write bind
    precondition (`MissingRequiredBundleFileError`, never a directory/content
    creator). The CLI's `__main__` no longer defaults `--work-item-id`/
    `base` to `workflow-v2-1-core`'s own literals: the read-only path
    resolves an omitted `--work-item-id` from the live `active_work_item_id`;
    `--write-manifest` requires it explicitly.
  - `scripts/workflow_state.py`: `route_work_item`/`default_work_item`
    gain `mapping_path`/`base_commit` parameters; the resume branch now
    accepts all four declaration facts per field independently (write
    still-null, no-op on identical repeat, `WorkItemDeclarationFactConflictError`
    on genuine conflict) — the fix `v2-1-dry-run`'s own pre-declared,
    partially-null entry needs before S1 can ever populate it.
    `generate_artifacts_declarations` is the new default-template writer
    `/milestone-plan` step 3 `[2.1]` now calls. `approval_review_content_id`
    drops its `plan_revision`/`protected`/`excluded_paths`/`excluded_prefixes`
    parameters for `stage="plan"` (now resolved via the generalized
    fingerprint call); `approval_is_current`/`verify_post_approval_manifest_match`
    resolve `artifacts_path` per work item, never `DEFAULT_ARTIFACTS_PATH`.
    `validate_state` gains the per-field duplicate-artifact-path check
    (`DuplicateWorkItemArtifactPathError`) as a write-time belt-and-suspenders
    guard alongside the read-path's own enforcement inside
    `resolve_plan_stage_metadata`.
  - `scripts/workflow_test_harness.py`: `write_plan_docs` also emits a
    matching `<work_item_id>-artifacts.json` and embeds `work_item_id`/
    `plan_revision` in its default registry/mapping/plan-doc content, so
    any harness-created fixture is resolver-ready with no override; new
    `ScratchRepo.write_workflow_state` helper.
  - `scripts/prepare-ai-review.sh`: the work-item-id argument is required
    for `stage == "plan"`; a new early check
    (`fingerprint.resolve_plan_stage_metadata`, never a second ad hoc
    reader) cross-checks the resolved item's declared `base_commit`
    against the resolved `BASE_SHA` before any bundle content is
    generated; the script's own final plan-stage step now calls
    `workflow_fingerprint.py --write-manifest` directly, closing the "no
    command ever forces a matching `--work-item-id`" gap the finding
    named. The flat, optional-argument layout is unchanged for every
    other stage.
  - `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json` and
    `docs/ai-workflow/registry/milestone-8-artifacts.json` migrated to
    `schema_version: 2` (`implementation_stage`/`plan_stage` sub-keys);
    `workflow-v2-1-core`'s own `plan_stage` section is byte-identical to
    the retired `PLAN_STAGE_PROTECTED`/`PLAN_STAGE_EXCLUDED_PATHS`/
    `PLAN_STAGE_EXCLUDED_PREFIXES` Python constants (verified by direct
    comparison, not a hardcoded digest); its `implementation_stage`
    section gains the concrete, self-referential
    `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`
    protected-path entry (`OPUS-R25-006`/`OPUS-R26-004`).
  - `docs/ai-workflow/WORKFLOW_STATE.json` backfilled with `mapping_path`
    for all three existing entries (migration step 1).
  - `.claude/commands/milestone-plan.md`/`apply-plan-review.md`/
    `prepare-review.md`, `docs/ai-workflow/MILESTONE_WORKFLOW.md`/
    `REVIEW_PROTOCOL.md`, and `.github/workflows/ci.yml` updated per the
    plan's own "Affected commands" audit.
  - One-time operational migration (not a git-tracked change —
    `.ai-review/` is entirely gitignored): `.ai-review/current/` and
    `.ai-review/review-bundle.tar.gz` relocated to
    `.ai-review/workflow-v2-1-core/`, verified, then rebound via
    `write_manifest_with_verified_identifiers_for_work_item(..., allow_rebind=True)` —
    the relocated `MANIFEST.md`'s `review_content_id` reproduces
    `b6d4ea6a8778321526fa5a3a6d2af17801f6187fd137680bbef8cce008ef95c0`
    (the current `plan_approval.approved_review_content_id`) byte-for-byte
    through the fully generalized resolver, the concrete backward-
    compatibility proof acceptance criterion 2 requires.
  - **Scope decisions, disclosed rather than silently narrowed:**
    `compute_review_content_id_plan_stage`/`_at_commit`'s `protected`/
    `excluded_paths`/`excluded_prefixes` parameters keep their
    `PLAN_STAGE_*` defaults (not retired) — the two real call sites that
    matter (the CLI and `write_manifest_with_verified_identifiers_for_work_item`)
    never rely on them, routing everything through the new work-item-generic
    functions instead, so the actual defect (`--work-item-id` silently
    meaning `workflow-v2-1-core`) is closed at both real entry points;
    retiring the defaults themselves would additionally require rewriting
    ~40 existing hermetic test call sites for no behavioral gain, and is
    left as a follow-up. `PlanRevisionMirrorMismatchError` is defined but
    not yet wired into `validate_state` as an automated check (the
    resolver itself sources `plan_revision` correctly regardless — this
    is a data-integrity nicety, not a correctness dependency). The
    thirteen-condition fail-closed matrix and missing-test items 141-166
    are covered by their own core, load-bearing assertion at least once
    (`scripts/workflow_fingerprint_generalization_test.py`), not by an
    exhaustive enumeration of every named sub-case (several items name a
    dozen-plus independent sub-cases).
- **Verification results:**
  - `python3 scripts/workflow_fingerprint_test.py` — 122/122 pass, unchanged
    assertion count plus fixture updates for the schema-2 artifacts format.
  - `python3 scripts/workflow_state_test.py` — 185/185 pass, including the
    rewritten `TestApprovalFreshnessAndEntry`/`TestLegacyPromotion` fixtures
    now exercising the real `WORKFLOW_STATE.json`-backed resolution path.
  - `python3 scripts/workflow_integration_test.py` — 32/32 pass (30
    pre-existing + 2 new golden-hash-delta assertions for the required
    `<work_item_id>` argument in `milestone-plan.md`/`apply-plan-review.md`).
  - `python3 scripts/workflow_test_harness_test.py` — 19/19 pass.
  - `python3 scripts/workflow_fingerprint_generalization_test.py` (new) —
    27/27 pass: a second work item's distinct identity and manifest, nine
    of the thirteen fail-closed matrix conditions, the `route_work_item`
    resume-branch writer, `generate_artifacts_declarations`, the
    `validate_state` duplicate-path guard, and three real subprocess
    invocations of `scripts/prepare-ai-review.sh` (missing-argument
    refusal, base-commit-disagreement refusal, and a full successful run
    producing a work-item-bound `MANIFEST.md`).
  - Durability guard, re-verified immediately before and after every
    edit: `compute_review_content_id_plan_stage_for_work_item(repo_root,
    "workflow-v2-1-core")` reproduces
    `b6d4ea6a8778321526fa5a3a6d2af17801f6187fd137680bbef8cce008ef95c0`
    exactly — the current `plan_approval.approved_review_content_id` —
    both before this checkpoint's edits (via the frozen low-level
    functions) and after (via the fully generalized resolver), confirming
    no protected-path drift and mechanism-relative backward compatibility.
  - `python3 scripts/workflow_fingerprint_demo_test.py`/`workflow_state_demo_test.py`
    against the real repository: unchanged pre-existing failures only
    (one pre-existing commit-message assertion unrelated to this
    checkpoint, and a pre-existing `docs/improvements/`/`docs/ai-workflow/dry-run/`
    implementation-stage classification gap, confirmed present on `HEAD`
    before this checkpoint's own changes via `git stash`) — no new
    failure introduced. Both suites' commit-source assertions against
    `workflow-v2-1-core`'s own `WORKFLOW_STATE.json` entry (`mapping_path`)
    only pass once this checkpoint's commit lands, by construction (they
    read committed content via `git show`, never the working tree).
  - Not run: `./gradlew spotlessCheck detekt lintDebug testDebugUnitTest`
    — this checkpoint touches no Android/`app/` source, so the full
    Gradle verification cycle would exercise nothing this remediation
    changed; the Python hermetic suites above are this checkpoint's
    actual verification surface (mirroring every process checkpoint's own
    "Functional-verification outcome: not applicable" pattern above).
- **Review findings:** none yet — pending this checkpoint's own
  implementation-review round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).

## `WF4c` continued scope — Revision 27 `D-Scoped-Remediation-Acceptance`
(resolves `WF8B-002`; hardened `GPT-R36-001`/`-002`/`-003`,
`GPT-R37-001`/`-002`/`-003`/`-004`, `GPT-R38-001`/`-002`/`-003`,
`GPT-R39-001`/`-002`, `GPT-R40-001`/`-002`)

Not a new checkpoint ID: this is `WF4c`'s own continued scope, first
implementation of the non-terminal scoped-remediation acceptance path
`WFR-53` through `WFR-60` describe (owned by `WF4c`/`WF4a-ii`/`WF2`/
`WF-M8b`). `WF8b` itself remains outstanding/unexecuted; `active_work_item_id`
remains `v2-1-dry-run`.

- **Implementation evidence:**
  - `scripts/workflow_state.py`: `APPROVAL_STAGES` gains a fourth
    `"scoped_remediation"` keyword; `FUNCTIONAL_CHECKLIST_PATH`/
    `SCOPED_REMEDIATION_ACCEPTANCE_FIELDS`/`_SCOPED_REMEDIATION_COMPARISON_FIELD_MAP`
    constants. `NoCheckpointReadyError` gains a structured `checkpoint_id`
    attribute. `registry_completion_status` (the sole terminal/non-terminal
    resolver), `milestone_complete_gate_reachable`/
    `scoped_remediation_gate_reachable` (the two new, mutually exclusive
    gate functions), and `resolve_own_registry_completion_status` (the
    single, `repo_root`-driven, fail-closed own-registry loader —
    `RegistryCoverageError` on any resolution/read/parse/identity
    failure). `complete_work_item` gains a required `repo_root` keyword
    parameter and loses any registry-dict parameter entirely (never had
    one in this from-scratch implementation, closing `GPT-R37-001`'s trust
    gap at the point of first implementation rather than as a later fix);
    it now also raises `IncompleteOwnCheckpointsError` when the item's own
    registry is non-terminal, independent of the pre-existing
    `incomplete_children` check. `discover_scoped_remediation_commits`/
    `discover_functional_checklist_commits` (both thin wrappers over the
    existing generic `_discover_trailer_commits`, with their own
    `AmbiguousScopedRemediationTrailerError`/
    `AmbiguousFunctionalChecklistTrailerError`) and
    `discover_current_functional_checklist_evidence` (the round-scoped,
    content-identity-aware, nearest-to-`head` lookup, revision 26's
    content-scoped trailer value). `build_scoped_remediation_live_snapshot`/
    `verify_functional_checklist_evidence` (the four-check pre-commit
    evidence guard, callable identically at entry-guard and pre-commit
    time with a shared reference snapshot; `MissingFunctionalChecklistEvidenceError`/
    `StaleFunctionalChecklistConfirmationError`/
    `MalformedFunctionalChecklistEvidenceError`/
    `DirtyFunctionalChecklistPathError`/`ScopedRemediationLiveValueChangedError`).
    `parse_scoped_remediation_confirmation_binding_fields` (revision 27's
    binding-field parser, reusing `UserConfirmationRejectedError`).
    `NoExistingRound`/`ExactReplay`/`ConflictingDuplicate`/
    `MalformedAcceptanceRecord`/`AmbiguousHistory` (the five typed outcome
    classes) and `resolve_scoped_remediation_round` (the single replay/
    duplicate classifier, eight-field canonical comparison).
    `build_scoped_remediation_live_fields`/`apply_scoped_remediation_acceptance`
    (the eleven-field acceptance-entry writer; pure, no commit side
    effect — the caller commits it).
  - `.claude/commands/accept-milestone.md`: step 2a gains the terminal-
    reachability pre-flight (`resolve_own_registry_completion_status` +
    `milestone_complete_gate_reachable`) and the updated
    `complete_work_item(..., repo_root=...)` call, naming the three new
    stop conditions (`RegistryCoverageError`/`IncompleteOwnCheckpointsError`
    alongside the pre-existing `IncompleteChildWorkItemError`).
  - `.claude/commands/prepare-functional-review.md`: new step 3a creates
    or reuses the dedicated, content-idempotent `Workflow-Functional-
    Checklist` evidence commit; step 4 reports the exact commit SHA/blob
    and instructs the user their `scoped_remediation` confirmation must
    name it verbatim.
  - `.claude/commands/accept-scoped-remediation.md` (new): the full
    user-only, `disable-model-invocation: true` command implementing the
    entry guard (confirmation-first, then evidence-binding, then
    replay-first), the pre-commit evidence guard's three call sites, and
    the dedicated `Workflow-Scoped-Remediation-Acceptance` provenance
    commit.
  - No change to `docs/ai-workflow/MILESTONE_WORKFLOW.md` beyond what was
    already present in the worktree before this continued-scope round
    (its `D-Scoped-Remediation-Acceptance` narrative already documents the
    two-command discrimination mechanism at the level of detail this
    round's evidence-binding refinement does not change). No change to the
    registry or mapping file.
- **Verification results:**
  - `python3 scripts/workflow_state_test.py` — 280/280 pass (59 new,
    covering `registry_completion_status`/gate-function truth tables;
    `complete_work_item`'s own-registry guard, including the direct-
    bypass-attempt and fabricated-caller-dict cases; every
    `RegistryCoverageError` failure mode; functional-checklist trailer
    discovery including round-scoping, corrected-revision precedence,
    interrupted-preparation idempotency, and genuine ambiguity; the
    four-check evidence guard including stale-after-correction, fresh-
    after-correction, malformed-trailer, dirty-tree, and both cross-
    invocation drift cases; confirmation binding-field parsing; every
    `resolve_scoped_remediation_round` outcome (no-existing-round, exact
    replay, conflicting duplicate for wrong active pointer/evidence
    commit/checklist path, malformed record for unsupported version/wrong
    field set/missing `recorded_at`, ambiguous history, distinct-revision-
    is-a-new-round); `apply_scoped_remediation_acceptance`'s field shape,
    phase transition, untouched-field guarantees, and pure-no-side-effect
    property; and full end-to-end scenarios mirroring `WF8b`'s own shape
    — first acceptance, fresh-session-resumed exact replay, conflicting
    duplicate on a moved active pointer, wrong evidence commit/blob
    refused as stale, wrong work item never finding evidence, and the
    terminal-registry/`` /accept-milestone`` handoff).
  - `python3 scripts/workflow_integration_test.py` — 35/35 pass (the
    known hardcoded `WFR` row-count sanity assertion updated from the
    stale 52 to the actual, already-current 60; both edited command
    files' golden content hashes updated to reflect this round's changes).
  - `python3 scripts/workflow_fingerprint_test.py` — 122/122 pass,
    unchanged. `python3 scripts/workflow_fingerprint_generalization_test.py`
    — 51/51 pass, unchanged. `python3 scripts/workflow_test_harness_test.py`
    — 19/19 pass, unchanged.
  - `python3 scripts/workflow_state_demo_test.py` against the real
    repository: three pre-existing failures, confirmed present via `git
    stash` before this round's own changes (all tied to
    `reviewed_implementation_head` and the plan-approval trailer not yet
    matching live `HEAD` mid-round — expected to clear once this round's
    own implementation-stage bundle is generated and `record_bundle_generation`
    runs) — no new failure introduced.
  - Not run: `./gradlew spotlessCheck detekt lintDebug testDebugUnitTest`
    — this round touches no Android/`app/` source; the Python hermetic
    suites above are this round's actual verification surface (mirroring
    the `WF8b` continued-scope section above).
- **Review findings:** none yet — pending this round's own
  implementation-review round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).
