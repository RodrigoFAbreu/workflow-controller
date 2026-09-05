# `workflow-v2-3-followups` Requirements Ledger

Mutable, human-readable execution record for the `workflow-v2-3-followups`
work item. Physically separate from the immutable, machine-readable
`workflow-v2-3-followups-mapping.json` in this same directory (D4b): the
mapping file is the approved requirement↔checkpoint binding and is
plan-stage protected (editing it changes `review_content_id`); this ledger
is never a fingerprint input — it resolves under
`docs/ai-workflow/requirements/` in `PLAN_STAGE_EXCLUDED_PREFIXES` and in
this item's own plan-stage approval's `excluded_prefixes`. Editing this
file never stales a plan approval or a technical approval and never
requires a fresh review round.

Each checkpoint appends one section here as it completes: implementation
evidence, verification results, review findings, and (where applicable)
functional-verification outcome. This is a log, not a status source —
`WORKFLOW_STATE.json`'s `checkpoints[id]` remains the sole record of
checkpoint status (D-Registry); nothing here is re-derived from or
overrides it.

## `CP1` — Fix /approve-review plan fifth-member staging bug

- **Implementation evidence, grouped by sub-obligation**
  (`GPT-FUP-R6-O02`):
  - **Fifth-member staging fix (REQ-1/REQ-11)**: merged
    `.claude/commands/approve-review.md` steps 5 and 6.1 into a single
    guarded window (new step, `"Plan stage only — stage every non-state
    approval member, then pin the fifth, if resolved"`) that stages the
    plan doc, registry JSON, mapping file, and the fifth member (if
    resolved) in **one** call to
    `workflow_state.stage_plan_approval_commit_paths`, then pins the
    fifth member's SHA256 inside the same window if one was resolved —
    the exact safe workaround `WORKFLOW_V2_3_FOLLOWUPS.md` documents
    using live, now the command's own documented sequence. Step 6's own
    sub-steps renumbered to two (6.2 state-pin, 6.3 staged-set assertion,
    6.4 commit) plus the unguarded structural assertion; step 6a's
    amend-recovery paragraph reworded to name the merged staging step
    instead of the retired "6.1's staging"; two now-stale numeric ranges
    (`4b/4c/5/6.1-6.4/...` and "hook re-staging a file after 6.1-6.3
    ran") corrected to `6.2-6.4` and "step 5 through 6.3" respectively.
  - **New guard-step label (REQ-1)**: added `"step-5-stage-and-pin"` to
    `PLAN_APPROVAL_ORDINARY_STEPS` in `scripts/workflow_state.py`
    (`LPR-R3-B02`) — a new classified entry, not a repurposed one;
    `"step-5-declaration-pin"`/`"step-6.2-stage-ordinary"` are
    deliberately left in the frozenset, unacquired by any real command
    step, per the plan's stated minimal-change disposition (matching
    `workflow_integration_test.py`'s existing direct-literal call sites
    for both labels).
  - **Trailer final-paragraph sentence (REQ-14 via "Executing this
    plan"/`LPR-R3-B01`)**: added the same "these two lines must be the
    commit message's own final paragraph" sentence
    `milestone-implement.md`/`bootstrap-workflow-v2.md` already state
    verbatim to `approve-review.md` step 6.4's commit instruction, naming
    `discover_plan_approval_commit` as the exact mechanism at risk.
  - **Baseline housekeeping (REQ-15 via `LPR-R3-B01`/`LPR-R3-I01`)**:
    added `fb134ac4f7cdabb7861d170bb61331bb8d9f5a14`
    (`workflow-v2-3`'s own `/accept-milestone` commit, this item's own
    `base_commit`) to `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`
    in `scripts/workflow_state_demo_test.py`, with a rationale comment
    matching the set's existing `workflow-v2-1-core` entry's convention;
    added `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md` to
    `docs/ai-workflow/registry/workflow-v2-1-core-artifacts.json`'s
    `implementation_stage.excluded_paths`, matching commit `aaa1247`'s
    precedent for the three sibling paths already there.
  - **Conformance surface (`LPR-R1-B02`)**: updated
    `_GOLDEN_COMMAND_FILE_SHA256["approve-review.md"]` in
    `scripts/workflow_integration_test.py` to the new post-edit hash,
    with a rationale comment naming CP1 and the specific hunks.
  - **Regression coverage**: added
    `TestPlanApprovalCommitTrailerFinalParagraphConformance` (three
    tests: `approve-review.md`/`milestone-implement.md`/
    `bootstrap-workflow-v2.md` each state the final-paragraph
    requirement — new coverage for all three, not just
    `approve-review.md`). In `TestPlanApprovalPermanentSiteEndToEnd`:
    updated the existing happy-path test to drive the merged
    `"step-5-stage-and-pin"` step instead of the retired
    `"step-6.2-stage-ordinary"` (no-fifth-member path unaffected, proven
    unchanged in behavior); added
    `test_five_member_fixture_succeeds_through_the_real_merged_step_5`
    (a genuine five-member fixture — fifth member pending and fresh —
    driven through the real merged step 5 all the way to a committed,
    materialized, journal-closed outcome, including the fifth member's
    committed-blob verification); added
    `test_merged_step_5_still_refuses_on_unrelated_dirty_index` (unrelated
    pre-staged content still raises `DirtyIndexBeforeStagingError` before
    the merged call runs, the guard releases without advancing progress,
    and step 6b's rollback then resets cleanly); added
    `test_new_step_label_classifies_ordinary_and_acquires_the_guard`
    (`plan_approval_step_class("step-5-stage-and-pin")` returns
    `"ordinary"` and `plan_approval_guarded_mutation` successfully
    acquires under it — the pre-existing generic frozenset-iteration test
    covers exhaustiveness but not this specific label).
  - Created this ledger file itself (did not exist before this
    checkpoint).
- **Verification results:** `python3 -m unittest workflow_integration_test
  workflow_state_test workflow_state_demo_test workflow_fingerprint_test
  workflow_fingerprint_demo_test workflow_fingerprint_generalization_test
  workflow_state_completion_obligations_test workflow_test_harness_test`
  — 1181 tests, all green (4 skipped, pre-existing/unrelated).
  `workflow_state_demo_test.py` run standalone — 45 tests, all green: the
  plan's own documented pre-existing red baseline
  (`test_no_new_workflow_trailer_lookalike_violations_beyond_the_grandfathered_set`
  and
  `test_real_implementation_stage_classification_has_no_unclassified_dirty_path`)
  is now clean, as this checkpoint's own baseline-housekeeping sub-
  obligation requires — a plain full pass, no baseline-relative carve-out.
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).

## `CP2` — `/review-implementation` canonical feedback writeback + plan-conformance read

- **Implementation evidence, grouped by sub-obligation:**
  - **New ownership guard (REQ-4/REQ-21)**: added
    `assert_feedback_not_owned_by_other_work_item` and the new
    `FeedbackOwnedByOtherWorkItemError` exception to
    `scripts/workflow_fingerprint.py`, beside
    `MissingFeedbackBindingFieldError`/`FeedbackBundleMismatchError` and
    `parse_review_feedback_binding_fields`/`assert_feedback_matches_bundle`
    respectively (`LPR-R8-O01`). Pure function over already-read content —
    never touches `resolve_feedback_dir` or the filesystem itself.
  - **Command rewrite (REQ-3/REQ-4/REQ-6/REQ-17/REQ-20)**:
    `.claude/commands/review-implementation.md` — step 3 gained a
    `PLAN.md`/`plan_path` read; step 5 gained a plan-conformance search
    arm; step 6's provenance clause no longer describes a hypothetical
    hand-copy; the frontmatter `description:` line no longer reads
    "Report-only"; a new step 7 re-calls `assert_bundle_not_rejected`
    (second of two, `WFR-67` classification change from "once" to
    "twice") then the new ownership guard, immediately before an
    unconditional write of `<feedback_dir>/REVIEW_FEEDBACK.md`, with
    refusal behavior stated per guard (suppress the report on a
    `BundleRejectedError`; still print it in full on a
    `FeedbackOwnedByOtherWorkItemError`) and recovery keyed on a live A
    reaching the terminal phase `MILESTONE_COMPLETE`; step 8 (was step 7)
    no longer claims the command writes nothing.
  - **Doc sync (REQ-7/REQ-19/REQ-23)**:
    `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Local reviewer commands"
    section split its shared "writes nothing"/"that choice is always the
    user's" sentences by command; `docs/ai-workflow/MILESTONE_WORKFLOW.md`
    updated at three sites — the `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
    "Allowed actions" clause, the "Hard gates summary" sentence, and the
    "Feedback file locations" section — each stating `/review-implementation`'s
    new write precisely while leaving `/review-functional`'s own equivalent
    text true and untouched.
  - **Housekeeping (REQ-13)**: `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md`
    git-added and tracked for the first time this checkpoint. Item 4's
    Status closed; item 2's Status closed except required follow-up #8
    (explicitly deferred, untracked operator-reference/diagram scratch
    content out of scope); item 2's required follow-up items 1–7 and 9
    marked closed, #8 marked deferred with its own disposition recorded.
  - **Conformance surface (`LPR-R1-B02`)**: updated
    `_GOLDEN_COMMAND_FILE_SHA256["review-implementation.md"]` in
    `scripts/workflow_integration_test.py` to the new post-edit hash, with
    a rationale comment naming CP2 and the changed steps; rewrote
    `test_states_the_report_only_constraint`/`test_states_it_writes_nothing`
    to the new, narrower invariant; updated
    `EXPECTED_ASSERTION_COUNT["...review-implementation.md"]` in
    `scripts/workflow_state_demo_test.py` from `1` to `2`, with the
    explanatory comment above the table corrected to match (REQ-5).
  - **Regression coverage**: `scripts/workflow_integration_test.py`'s
    `TestReviewImplementationCommandStaticConformance` gained four new
    doc-assertion methods (plan-conformance read, step 6/7 no longer
    describing manual installation + frontmatter no longer "Report-only",
    the write step naming the ownership guard, and the refusal-path prose
    stated per guard with terminal-phase-keyed recovery).
    `scripts/workflow_fingerprint_test.py` gained three new test classes:
    `TestFeedbackNotOwnedByOtherWorkItem` (no-existing-file, same-work-item
    overwrite, unparseable `Work item:` field, different-work-item refusal
    naming both ids — four cases), `TestReviewImplementationWritebackCrossWorkItemIsolation`
    (a genuine flat-path collision between two work items via the real
    `resolve_feedback_dir`, proving the refused write leaves the other
    work item's feedback byte-identical and creates no scoped
    `.ai-review/<work_item_id>/feedback/` directory as a side effect, per
    `LPR-R8-I01`; plus a same-work-item-overwrite-succeeds case), and
    `TestReviewImplementationFeedbackBindingRoundTrip` (a freshly composed
    `REVIEW_FEEDBACK.md` in the command's own step-6 shape, including the
    non-binding `Reviewed review content ID:` line, binds successfully
    through the shared parsers). The three "stale/rejected/wrong-phase
    implementation review writes nothing" negative paths REQ-5 names are
    covered by the pre-existing, unmodified, generically-tested
    `assert_bundle_not_rejected`/`assert_local_generation_matches`
    machinery and the phase-guard doc-assertion (unchanged by this
    checkpoint) rather than duplicated under a review-implementation-specific
    fixture — this checkpoint adds a second call site to the first two
    functions and no new call site to the third; their own existing
    coverage in `workflow_fingerprint_test.py`/`workflow_integration_test.py`
    already proves each raises/refuses correctly and is unaffected by
    which command calls them.
- **Verification results:** `python3 -m unittest workflow_fingerprint_test
  workflow_fingerprint_generalization_test workflow_state_test
  workflow_state_completion_obligations_test workflow_integration_test
  workflow_test_harness_test` — 1132 tests, all green. `workflow_state_demo_test.py`
  run standalone (this checkpoint's own required addition, REQ-5's
  sequencing note) — 45 tests, all green, including
  `test_all_fifteen_command_files_declare_the_expected_value` and
  `test_every_non_exempt_file_calls_the_shared_assertion_the_expected_number_of_times`
  against the updated `EXPECTED_ASSERTION_COUNT` table.
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior; `/review-implementation`'s own new write
  behavior is exercised for real by this milestone's own functional
  review, per `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS_PLAN.md`).

## `CP3` — Normalize review-role/review-stage identifiers to `SCREAMING_SNAKE_CASE`

- **Implementation evidence, grouped by sub-obligation:**
  - **Canonical constants + read/write repointing (REQ-8)**: added
    `LOCAL_MODEL_PLAN_REVIEW`/`MANUAL_EXTERNAL_PLAN_REVIEW` module-level
    constants to `scripts/workflow_state.py`; repointed both write sites
    (`record_local_plan_review`'s full-dict replace,
    `record_manual_plan_review`'s in-place key assignment) and all three
    read-site lookup literals (`plan_approval_gate_reachable`,
    `validate_manual_plan_review_preconditions`, `_validate_plan_review_stages`)
    to the constants.
  - **Compatibility-normalization helper + collision rule (REQ-8/REQ-22)**:
    added `_normalize_plan_review_stage_key` (single-key/value primitive)
    and `normalize_plan_review_stages` (dict-level, used at all three read
    sites and by the migration function below) — passes
    `review_content_id` through unchanged, collapses a byte-identical
    legacy+canonical duplicate silently, and raises the new
    `AmbiguousPlanReviewStageKeyError` (beside its ten sibling
    plan-review-stage exceptions) naming both raw keys/values on a genuine
    conflict, independent of dict insertion order. Applied
    `_normalize_plan_review_stage_key` directly to
    `validate_manual_plan_review_preconditions`'s `feedback_role`
    comparison (a value, not a dict key) so either casing is accepted;
    reworded its docstring bullet and `WrongReviewerRoleError` message to
    state the two-value acceptance instead of "exactly".
  - **One-time migration (REQ-8/REQ-18, `LPR-R2-I02`)**: added
    `migrate_plan_review_stage_keys(state)` (skips `TERMINAL_PHASES`
    work items, reuses `normalize_plan_review_stages` for every live
    non-terminal one) and ran it for real via
    `workflow_state.state_transaction(repo_root,
    migrate_plan_review_stage_keys)` against the live
    `docs/ai-workflow/WORKFLOW_STATE.json` in this same commit —
    `workflow-v2-3-followups`'s own live, non-terminal `plan_review_stages`
    ledger (populated by this item's own plan-review rounds, before CP1)
    is now canonical-cased; `workflow-v2-3`'s own terminal record is
    confirmed byte-unchanged (verified directly against the real
    repository file, not only the fixture test below).
  - **Template/doc casing (REQ-9)**: `.claude/commands/review-plan.md`
    (frontmatter `description:`, the role-name prose, the `Reviewer
    role:` template literal, the round-computation prose reworded to
    count a round entry under either casing, the ledger-fields prose) and
    `.claude/commands/record-manual-plan-review.md` (frontmatter
    `description:`, the read/validate/write-set prose, the exact-match
    expectation prose reworded to state two-value acceptance) repointed
    to canonical casing throughout; `docs/ai-workflow/MILESTONE_WORKFLOW.md`
    and `docs/ai-workflow/PLAN_REVIEW_WORKFLOW.md` repointed likewise,
    with the transition table's "is exactly `manual_external_plan_review`"
    cell corrected to state the two-value acceptance instead of overstating
    it as exact-only. Left untouched, per the plan's own disposition:
    `docs/ai-workflow/WORKFLOW_V2_PLAN.md`, `docs/ai-workflow/WORKFLOW_V2_3_PLAN.md`,
    `docs/ai-workflow/dry-run/*`, every registry/requirements JSON prose
    mention, `workflow-v2-3`'s own terminal `plan_review_stages` block, the
    two real-repository demo-suite comments (`workflow_state_demo_test.py:36`,
    `workflow_fingerprint_demo_test.py:51`), `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md`'s
    own five source-of-requirements mentions, and the untracked
    `WORKFLOW_V2_1_OPERATOR_REFERENCE.md`/`diagrams/`.
  - **Housekeeping (REQ-13)**: `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md`
    item 3's `Status` updated — closed for required follow-ups #1-#6 and
    #8, and for the "tests, fixtures"/tracked-operator-documentation
    halves of #7; the untracked-artifacts half of #7 remains explicitly
    deferred, same disposition as item 2's own #8.
  - **Conformance surface (`LPR-R1-B02`)**: updated
    `_GOLDEN_COMMAND_FILE_SHA256["review-plan.md"]`/`["record-manual-plan-review.md"]`
    in `scripts/workflow_integration_test.py` to their new post-edit
    hashes, with a rationale comment naming CP3.
  - **Regression coverage (REQ-10/REQ-22)**: converted all 40 existing
    `scripts/workflow_state_test.py` fixture/assertion occurrences (and
    the one non-immutable occurrence in `scripts/workflow_integration_test.py`;
    the `WORKFLOW_V2_PLAN.md` doc-substring assertion at the other
    occurrence is immutable and correctly left alone) to canonical casing.
    Added `TestPlanReviewStageKeyNormalization` (compatibility-read proof
    for a fully-legacy-cased dict, idempotence on an already-canonical
    dict, identical-duplicate collapse and conflicting-duplicate refusal
    proven both insertion orders, `plan_approval_gate_reachable`
    tolerating legacy keys, a non-terminal legacy-cased work item reading
    cleanly through `validate_state` and a further `REVISE` transition,
    `WrongReviewerRoleError`'s two-value acceptance plus a clean refusal
    on a genuine third value, and a documented regression proving
    `record_manual_plan_review`'s `APPROVE` branch raises
    `AmbiguousPlanReviewStageKeyError` on a not-yet-migrated legacy ledger
    — the accepted, by-design edge case the plan's write-site disposition
    relies on instead of changing the write site itself) and
    `TestMigratePlanReviewStageKeys` (migrates every live non-terminal
    record and leaves a terminal one byte-unchanged, is idempotent, leaves
    a null ledger untouched, raises on an already-ambiguous non-terminal
    ledger, and collapses a byte-identical conflicting duplicate) — 14 new
    tests total.
- **Verification results:** `python3 -m unittest workflow_fingerprint_test
  workflow_fingerprint_generalization_test workflow_state_test
  workflow_state_completion_obligations_test workflow_integration_test
  workflow_test_harness_test` — 1146 tests, all green (up from 1132 at
  CP2, +14 new). `workflow_fingerprint_demo_test.py` and
  `workflow_state_demo_test.py` also re-run standalone this checkpoint
  (ahead of CP4's own required run) — 15/15 (4 skipped, pre-existing/
  unrelated) and 45/45 respectively, both clean.
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).

## `CP4` — Final cross-cutting verification and protocol/workflow doc coherence check

- **Implementation evidence:** no new logic — this checkpoint is
  verification-only, per the plan's own "No new logic" instruction. No
  source or documentation file was modified.
  - **Doc coherence cross-check (REQ-7/REQ-19/REQ-23)**: re-read all four
    named sites together — `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Local
    reviewer commands" section, `docs/ai-workflow/MILESTONE_WORKFLOW.md`'s
    "Hard gates summary" sentence, its
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` "Allowed actions" clause, and
    its "Feedback file locations" section. All four state
    `/review-implementation`'s new write behavior
    (`.ai-review/feedback/REVIEW_FEEDBACK.md`, once its own pre-write
    guards pass, still no `WORKFLOW_STATE.json` write and no `phase`
    advance) consistently with each other, and all confirm
    `/review-functional`'s own `AWAITING_FUNCTIONAL_REVIEW` "Allowed
    actions" clause is unchanged ("it writes nothing and never advances
    this state, so it adds no new gate").
  - **Hard-gate count (REQ-11)**: `MILESTONE_WORKFLOW.md`'s "Hard gates
    summary" still lists exactly six numbered gates and explicitly states
    `/review-implementation`/`/review-functional` add no new one.
  - **Scope-creep grep sweep (REQ-11)**: swept the CP1–CP3 diff
    (`fb134ac..HEAD` on `scripts/workflow_state.py`, `.claude/commands/`,
    `MILESTONE_WORKFLOW.md`, `REVIEW_PROTOCOL.md`) plus a whole-repo grep
    for `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, `quorum`, and
    `Reviewer role:`. Every `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`/quorum
    hit is prose in `WORKFLOW_V2_3_FOLLOWUPS.md`/`WORKFLOW_V2_3_FOLLOWUPS_PLAN.md`/
    the mapping JSON explicitly declining to introduce it (REQ-11 itself);
    every `Reviewer role:` hit outside those declining-prose sites belongs
    to the pre-existing plan-review-stage vocabulary
    (`LOCAL_MODEL_PLAN_REVIEW`/`MANUAL_EXTERNAL_PLAN_REVIEW`, CP3's own
    casing normalization) — no implementation-review `Reviewer role:`
    marker exists in `.claude/commands/review-implementation.md`. No new
    implementation-review lifecycle state, ledger stage, reviewer-quorum
    concept, or controller-lifecycle content was introduced anywhere
    across CP1–CP3.
  - **`WORKFLOW_V2_3_FOLLOWUPS.md` status accuracy (REQ-13)**: re-read all
    four items' `Status` sections. Item 1: closed by CP1. Item 2: closed
    by CP2, except required follow-up #8 (operator reference / lifecycle
    diagram sync), explicitly deferred. Item 3: closed by CP3 for required
    follow-ups #1–#6 and #8, and for the "tests, fixtures" and
    tracked-operator-documentation halves of #7; the untracked-artifacts
    half of #7 explicitly deferred. Item 4: closed by CP2. Matches this
    checkpoint's own expected disposition exactly — no item overstates
    what CP1–CP3 actually closed.
- **Verification results:** `python3 -m unittest workflow_fingerprint_test
  workflow_fingerprint_generalization_test workflow_state_test
  workflow_state_completion_obligations_test workflow_integration_test
  workflow_test_harness_test` — 1146 tests, all green, zero regressions
  from CP1–CP3 combined (matches CP3's own recorded total exactly, since
  this checkpoint added no test). `workflow_fingerprint_demo_test.py` and
  `workflow_state_demo_test.py` (both real-repository demo suites)
  re-run standalone — 15/15 (4 skipped, pre-existing/unrelated) and 45/45
  respectively, both clean.
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior; this checkpoint is itself the milestone's
  final cross-cutting verification pass).

## Self-review (`SELF_REVIEWING_IMPLEMENTATION`, milestone-wide)

Full-diff self-review of `fb134ac..HEAD` (CP1–CP4 combined) for
correctness, layer boundaries, missing tests, and maintainability.

- **Findings, one important, now fixed:** CP2's rewrite of
  `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Local reviewer commands"
  section narrowed a pre-existing "either command's printed report" hand-
  copy sentence down to `/review-functional` alone, leaving that command
  as the sole subject of a claim that is false for it: the sentence told
  an operator they could install `/review-functional`'s report at
  `<feedback_dir>/REVIEW_FEEDBACK.md` "as the authoritative external
  round". Two paragraphs later the same section states that report is a
  checklist-completeness opinion and "never a `Status:` verdict", and the
  functional gate's own artifact is the user-written
  `FUNCTIONAL_REVIEW.md`, which only `/apply-functional-review` consumes.
  The sentence was true of `/review-implementation` before CP2 changed
  that command's write behavior; after the narrowing it survived attached
  to the one command it does not describe. Fixed in place: the paragraph
  now states plainly that `/review-functional`'s report has no
  authoritative round to become and that `REVIEW_FEEDBACK.md` is not its
  destination, and points the operator at revising their own
  `FUNCTIONAL_REVIEW.md` checklist by hand instead. `/review-functional`
  itself is untouched — requirement 5's out-of-scope boundary is about
  the command, and this is a sentence in a document CP2 already rewrote
  and this item already declares implementation-stage protected.
- **No blocking findings.** No layer-boundary concern arises (this item
  touches no `app/` code at all), and the mixed-casing
  `plan_review_stages` write hazard reviewed here is deliberate,
  fail-loud, and covered by
  `test_record_manual_plan_review_approve_raises_on_a_legacy_cased_ledger`.
- **Verification results:** see the milestone-wide full verification
  recorded in `TEST_RESULTS.md` for this bundle — the Python suites were
  re-run after this fix (1146 + 45 + 15 tests, all green) alongside the
  full Gradle gate (`spotlessCheck detekt lintDebug testDebugUnitTest`,
  `BUILD SUCCESSFUL`).
