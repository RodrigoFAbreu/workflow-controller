# `workflow-v2-3` Requirements Ledger

Mutable, human-readable execution record for the `workflow-v2-3` work item.
Physically separate from the immutable, machine-readable
`workflow-v2-3-mapping.json` in this same directory (D4b): the mapping file
is the approved requirement↔checkpoint binding and is plan-stage protected
(editing it changes `review_content_id`); this ledger is never a
fingerprint input — it resolves under
`docs/ai-workflow/requirements/` in both `PLAN_STAGE_EXCLUDED_PREFIXES` and
`workflow-v2-3-artifacts.json`'s own `implementation_stage.excluded_prefixes`.
Editing this file never stales a plan approval or a technical approval and
never requires a fresh review round.

Each checkpoint appends one section here as it completes: implementation
evidence, verification results, review findings, and (where applicable)
functional-verification outcome. This is a log, not a status source —
`WORKFLOW_STATE.json`'s `checkpoints[id]` remains the sole record of
checkpoint status (D-Registry); nothing here is re-derived from or
overrides it.

## `CP1` — Add /review-implementation command

- **Implementation evidence:**
  - Widened `docs/ai-workflow/registry/workflow-v2-3-artifacts.json`'s
    `implementation_stage` (mirroring
    `workflow-v2-1-core-artifacts.json`'s own equivalent sections, per the
    plan's "Revision 3"-"Revision 13" dispositions), and made this item's
    one and only edit to `workflow-v2-1-core-artifacts.json`'s
    `implementation_stage` (four `excluded_paths`/`excluded_prefixes`
    additions). Two self-discovered classification gaps beyond the plan's
    own text were found and fixed the same way: `docs/ai-workflow/MILESTONE_WORKFLOW.md`/
    `docs/ai-workflow/REVIEW_PROTOCOL.md` needed `protected_paths` entries
    (present in the plan text but missed in an earlier draft of this
    widening), and a previously-undocumented untracked file,
    `docs/ai-workflow/WORKFLOW_V2_3_FOLLOWUPS.md`, needed an
    `implementation_stage.excluded_paths` entry in both artifacts files
    (analogous to "Revision 11"'s own self-discovered gap, but found live
    during CP1 rather than during plan review). The final
    `workflow-v2-3-artifacts.json` `implementation_stage` declaration,
    after both gaps, is 18 `excluded_paths`, 13 `excluded_prefixes`, 4
    `protected_paths`, 2 `protected_prefixes`.
  - Added `WORKFLOW_V2_1_CORE_COMPLETION_COMMIT` constant and a
    `_blob_at_commit` helper to both `scripts/workflow_fingerprint_demo_test.py`
    and `scripts/workflow_state_demo_test.py`; re-anchored the seven named
    real-repository tests to that fixed commit instead of live `HEAD`/the
    worktree.
  - Grandfathered `27f051eba897d77c742ead8b160ed519c0671ee4` (this item's
    own `base_commit`) in `_GRANDFATHERED_WORKFLOW_TRAILER_LOOKALIKE_VIOLATIONS`;
    re-scanned `162154d3..HEAD` at the actual plan-approval commit and
    confirmed no further violation outside the grandfathered set.
  - Added five new tests: two active-work-item-scoped changed-set
    classification tests (implementation-stage and plan-stage) in
    `workflow_state_demo_test.py`; a standalone plan-stage
    classification-gap assertion at the fixed completion commit; a
    no-live-anchor static-conformance scan
    (`TestDemoTestNoLiveAnchorStaticConformance` in
    `workflow_integration_test.py`, AST/`inspect.signature`-based, no
    hand-maintained positional-index map); and a hermetic
    commit-vs-dirty-worktree cross-source regression test in
    `workflow_fingerprint_test.py`.
  - Created `.claude/commands/review-implementation.md` (report-only,
    model-independent, `state_writer: false`, `review-subject: bundle`).
  - Registered it in `REVIEW_SUBJECT_ROSTER`, `EXPECTED_CALL_SITES` (three
    live callers now), `TestReviewSubjectDeclarationsLive`'s
    `EXPECTED`/`EXPECTED_ASSERTION_COUNT` (fourteen files), a new
    `TestReviewImplementationCommandStaticConformance` class, and
    `_GOLDEN_COMMAND_FILE_SHA256`.
  - Updated four documentation sites to name the third
    `assert_local_generation_matches` caller
    (`WorktreeOrHeadMismatchError`'s docstring,
    `assert_local_generation_matches`'s own docstring,
    `assert_bundle_not_rejected`'s docstring,
    `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Generation diagnostic
    metadata" section) and added `MILESTONE_WORKFLOW.md`'s
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` "Allowed actions" bullet.
  - Created this ledger file itself (did not exist before this
    checkpoint).
- **Verification results:** `python3 -m unittest workflow_integration_test
  workflow_fingerprint_test workflow_state_test
  workflow_state_completion_obligations_test workflow_test_harness_test
  workflow_fingerprint_generalization_test` — 1108 tests, all green.
  `workflow_fingerprint_demo_test.py`/`workflow_state_demo_test.py`
  (real-repository suites) — every test green except
  `TestReviewSubjectDeclarationsLive`'s two live-`HEAD`-scoped tests and
  `TestReconciliationTableLedgerStatusAgreement`'s two pinned-worktree
  re-executions, all four of which read `review-implementation.md`/this
  checkpoint's own committed content at git `HEAD` and are expected to
  fail until this checkpoint's own commit lands (the same "commit,
  *then* run the real-repository suites" ordering `/milestone-implement`
  step CP1 requires) — re-verified green immediately after the commit
  below; see `git log --grep=CP1` for the exact commit and its own
  recorded suite output.
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).

## `CP2` — Add /review-functional command, shared docs, final verification

- **Implementation evidence:**
  - Created `.claude/commands/review-functional.md` (report-only,
    model-independent, `state_writer: false`, `review-subject: bundle`),
    mirroring `/review-implementation`'s step 1 exactly (single clean
    refusal, no untracked-item branch) per the plan's "Revision 10"
    (`GPT-R9-001`) decision, and using work-item-neutral functional-
    acceptance wording ("checklist's required functional flows", an
    app walkthrough or command/process checks, never the bare phrase
    "the Android app") per "Revision 10" (`GPT-R9-002`). Step 3 refuses
    cleanly on stale checklist evidence (a blob mismatch against
    `docs/ACTIVE_MILESTONE.md`'s live content) rather than reviewing it,
    per "Revision 10" (O1). Step 6 calls
    `workflow_fingerprint.assert_bundle_not_rejected` exactly once,
    immediately before composing the report; the command never calls
    `assert_local_generation_matches` (no bundle/`MANIFEST.md` exists at
    this stage).
  - Registered `review-functional.md` in `REVIEW_SUBJECT_ROSTER`
    (`scripts/workflow_state.py`).
  - Updated `scripts/workflow_state_demo_test.py::TestReviewSubjectDeclarationsLive`:
    added `review-functional.md: "bundle"` to `EXPECTED`,
    `review-functional.md: 1` to `EXPECTED_ASSERTION_COUNT`, renamed the
    "fourteen" test method/docstring to "fifteen" and updated the
    "ten-consumer" note to "eleven-consumer, now that /review-functional
    has landed". `len(exempt)` stays 4, unchanged.
  - Added a new `TestReviewFunctionalCommandStaticConformance` class to
    `scripts/workflow_integration_test.py` (frontmatter, model-
    independence, report-only constraint, phase guard, "writes nothing"
    statement — mirroring `TestReviewImplementationCommandStaticConformance`'s
    pattern) plus the two round-9 missing-tests textual-presence checks
    (revision 10): the single coherent untracked-item policy (contains the
    step 1 clean-refusal phrase, never "read the checklist") and the
    work-item-neutral functional-acceptance wording (contains "checklist's
    required functional flows", never the bare phrase "the Android app").
    Added `review-functional.md`'s entry to `_GOLDEN_COMMAND_FILE_SHA256`.
    `EXPECTED_CALL_SITES` (`TestAssertLocalGenerationMatchesCallSiteConformance`)
    is unchanged — `review-functional.md` never calls
    `assert_local_generation_matches`.
  - Updated `scripts/workflow_fingerprint.py`'s `assert_bundle_not_rejected`
    docstring to add `/review-functional` to its "every required consumer"
    enumeration (the `WorktreeOrHeadMismatchError`/
    `assert_local_generation_matches` docstrings are correctly left
    unchanged — `/review-functional` is not a consumer of that function).
  - `docs/ai-workflow/MILESTONE_WORKFLOW.md`: added the
    `AWAITING_FUNCTIONAL_REVIEW` "Allowed actions" bullet for
    `/review-functional`, and a "Hard gates summary" clarifying sentence
    (both new commands are optional actions inside existing gates; the
    hard-gate count stays exactly 6).
  - `docs/ai-workflow/REVIEW_PROTOCOL.md`: added a "Local reviewer commands
    (operator ergonomics)" section describing both commands, their
    report-only nature, how a user may hand-copy a report into
    `<feedback_dir>/REVIEW_FEEDBACK.md` if they choose to treat it as the
    authoritative round, the two commands' deliberately differently-shaped
    reports (`Status: APPROVE | REVISE | BLOCK` vs. a checklist-completeness
    opinion), and (in prose, not as a mechanical constraint) the
    `/review-functional` vs. `/prepare-functional-review` `review-subject:`
    contrast.
  - `CLAUDE.md`: brought the "Slash commands" list fully current — all
    sixteen real command names, including the five this list previously
    omitted (`bootstrap-workflow-v2`, `record-manual-plan-review`,
    `review-plan`, `accept-scoped-remediation`,
    `recover-implementation-provenance`) alongside `review-implementation`
    and `review-functional`.
  - Added this checkpoint's own section to this ledger.
- **Verification results:** `python3 -m unittest workflow_fingerprint_test
  workflow_fingerprint_generalization_test workflow_state_test
  workflow_state_completion_obligations_test workflow_integration_test
  workflow_test_harness_test` — 1115 tests, all green.
  `workflow_fingerprint_demo_test.py` (real-repository suite) — 15 tests,
  all green (4 skipped, pre-existing/unrelated).
  `workflow_state_demo_test.py` (real-repository suite) — 43/45 green;
  the two failures are `TestReviewSubjectDeclarationsLive`'s
  `test_all_fifteen_command_files_declare_the_expected_value` and
  `test_recover_implementation_provenance_has_no_declaration`, both
  reading `review-functional.md`'s content at git `HEAD` and therefore
  expected red until this checkpoint's own commit lands (the same
  "commit, *then* run the real-repository suites" ordering
  `/milestone-implement` step CP2 requires, and the same pattern CP1's
  own two-file, four-test version of this same gap showed) — re-verified
  green immediately after the commit below; see `git log --grep=CP2` for
  the exact commit and its own recorded suite output.
- **Review findings:** none yet — pending this checkpoint's own review
  round.
- **Functional-verification outcome:** not applicable (process checkpoint,
  no product-facing behavior).
