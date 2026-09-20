# Active Milestone

## Status

**In progress.** `workflow-controller-protocol-2-2-compatibility`
(`governing_workflow_version: "2.2"`) is `SELF_REVIEWING_IMPLEMENTATION`:
all six checkpoints are `COMPLETE`. Plan revision 2 was
approved via the two-stage plan-review protocol (`LOCAL_MODEL_PLAN_REVIEW`
round 2 APPROVE, `MANUAL_EXTERNAL_PLAN_REVIEW` round 1 APPROVE), recorded at
commit `7bfa238`. Base commit: `21a304d50a8bb9c08e465e78a4636e353e16b5e9`.

Goal: make Controller Generation 1 (`controller/`) correctly understand and
orchestrate Workflow protocol `2.2`'s split implementation-review lifecycle,
while continuing to drive `"1"`/`"2.1"`-governed work items exactly as
before. Full plan:
`docs/ai-workflow/CONTROLLER_GEN1_PROTOCOL_2_2_COMPAT_PLAN.md`. Registry:
`docs/ai-workflow/registry/workflow-controller-protocol-2-2-compatibility-registry.json`
(six checkpoints, `CP1`-`CP6`).

### Checkpoint progress

- **CP1 -- COMPLETE.** Added four `"2.2"` rows to `controller/job.py`'s
  `EXPECTED_OUTCOMES` table (`PLANNING`, `AWAITING_LOCAL_PLAN_REVIEW`,
  `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `REVISING_PLAN`), each
  byte-identical to its `"2.1"` counterpart apart from
  `governing_version`, closing the uncaught `AssertionError` a
  `"2.2"`-governed work item hit today the moment Controller tried to
  drive it automatically through plan review. The table grew from seven
  rows to eleven; every row-count-dependent comment naming "seven" that
  actually describes this table was updated to "eleven"
  (`job.py`'s `_expected_outcome_for` docstring, the
  `_INCOMPLETE_EFFECT_PHASES` comment, and both `execute_step`/`resume`
  narrative comments) -- the unrelated `TERMINAL_STATUSES` "remaining
  seven members" comment (job status enumeration, not this table) was
  left untouched, as the plan specifies. Verified: all pre-existing
  `tests/test_job_validation.py` structural properties
  (`property_table_violations`, `property_record_completeness_violations`,
  `property_declaration_against_artifact_violations`, reachability,
  transition-verification) pass unchanged against the eleven-row table,
  and all pre-existing `tests/test_job.py` tests pass. The one expected
  failure, `ExpectedOutcomesTableStructureTest.test_seven_rows`
  (`11 != 7`), is CP2's own row-count-assertion update, explicitly out of
  CP1's scope per the plan.
- **CP2 -- COMPLETE.** Regression-tested CP1's four new `"2.2"` rows on
  both the execute-time and resume/reconciliation paths, and closed the
  stale seven-row cardinality assertion. Renamed
  `tests/test_job_validation.py`'s `ExpectedOutcomesTableStructureTest.
  test_seven_rows` to `test_eleven_rows` (asserts
  `len(job.EXPECTED_OUTCOMES) == 11`) and updated the class docstring.
  Added `TwoPointTwoPlanReviewTransitionTest` to
  `tests/test_job_validation.py`: six end-to-end `execute_step` cases
  driving a `"2.2"`-governed item through every member of all four new
  rows' own `to_any_of` sets (`PLANNING` -> `AWAITING_LOCAL_PLAN_REVIEW`;
  `AWAITING_LOCAL_PLAN_REVIEW` -> `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` /
  `REVISING_PLAN` / the `BLOCK`-predicate-satisfied same-phase case;
  `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` -> `AWAITING_PLAN_APPROVAL`;
  `REVISING_PLAN` -> `AWAITING_LOCAL_PLAN_REVIEW`), each verified
  `FINISHED` with the correct `observed_phase_after`. Added
  `tests/test_job.py::MissingGoverningVersionRowRegressionTest`, the
  permanent negative-regression proof: `unittest.mock.patch.dict` shrinks
  `_EXPECTED_OUTCOMES_BY_KEY` to the seven pre-CP1 entries and asserts
  `_expected_outcome_for("PLANNING", "2.2", ...)` raises `AssertionError`
  against that patched table -- reproduced from data, not from reverting
  a commit, so it keeps proving CP1's fix as the table grows further.
  Added `tests/test_resume.py::ReconcileLaunchedTest.
  test_2_2_planning_launched_record_reconciles_to_finished_never_relaunches`,
  proving the second, independent call site (`resume`'s own
  `_expected_outcome_for_record` lookup) reconciles a `"2.2"`-governed
  `LAUNCHED` `PLANNING` record correctly. Verified: the full
  `tests/test_job.py`, `tests/test_job_validation.py`, and
  `tests/test_resume.py` suites pass (108 tests), and the repository's
  full test suite passes (434 tests, 1 skipped -- the live-Claude-gated
  integration test, `CLAUDE_BIN` unset in this environment).
- **CP3 -- COMPLETE.** Added `WorkflowSnapshot.supported_versions:
  tuple[str, ...] | None` to `controller/target_state.py`, populated by a
  new `_read_supported_versions(root)` helper that mirrors
  `_read_default_workflow_version`'s existing fail-soft contract exactly
  (`None` if `WORKFLOW_CONFIG.json` is missing/unparseable/not a dict, or
  if `supported_versions` is absent or not a list of strings; otherwise
  the tuple of strings, in file order). Wired into `read()` alongside the
  existing `default_workflow_version=...` construction. Purely
  diagnostic, read-only context -- confirmed still true by the module's
  existing AST-scan `ReadOnlySourceScanTest`, which continues to pass
  unchanged; neither field is consumed by any decision, gate, or CLI
  print path. Added `tests/test_target_state.py` coverage through the
  real `target_state.read()` path against fixture target repositories:
  `test_2_2_supported_versions_and_governing_version_read_correctly`
  confirms `WorkflowSnapshot.default_workflow_version == "2.2"` and
  `WorkflowSnapshot.supported_versions == ("1", "2.1", "2.2")` when a
  fixture `WORKFLOW_CONFIG.json` declares them (values chosen to match
  this repository's own real post-`21a304d` file, but read from the
  fixture, not from it) and `WorkItemView.governing_workflow_version ==
  "2.2"` when a work-item entry declares it; the pre-existing happy-path
  test now also asserts `supported_versions == ("1", "2.1")` against its
  own fixture; two new fail-soft tests
  (`test_supported_versions_absent_yields_none`,
  `test_supported_versions_not_a_list_of_strings_yields_none`) cover the
  same shape of malformed-config cases `default_workflow_version` already
  fails soft on. No other production file changes (confirmed by grep:
  neither `default_workflow_version` nor `supported_versions` is
  referenced anywhere else in `controller/`). Verified:
  `tests/test_target_state.py` (42 tests), `tests/test_decision.py`,
  `tests/test_cli.py`, `tests/test_write_containment.py`,
  `tests/test_job.py` (102 tests total across those four), and the
  repository's full test suite (437 tests, 1 skipped -- the
  live-Claude-gated integration test, `CLAUDE_BIN` unset in this
  environment) all pass.
- **CP4 -- COMPLETE.** Audit-and-harden pass over `decision.py`/
  `evidence.py`'s already-correct `"2.2"` implementation-review
  classification (per the plan's own investigation, no production code
  changed) -- three genuinely new regression tests, no others needed.
  Added `tests/test_evidence.py::AwaitingManualExternalImplementationReviewTest.
  test_revise_status_also_names_record_manual_implementation_review`, the
  one sub-case that class's existing coverage left uncovered
  (`Status: REVISE` on file with the admissible
  `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` role): falls into the same
  non-`BLOCK` branch as `APPROVE`, report-only, naming
  `/record-manual-implementation-review`. Added
  `tests/test_evidence.py::AwaitingExternalImplementationReviewTest.
  test_2_2_item_reports_identically_to_2_1_at_this_reused_terminal_phase`,
  the concrete regression guard for `MILESTONE_WORKFLOW.md:425-434`'s
  "byte-for-byte" reuse claim: `_decide_awaiting_external_implementation_
  review` consults neither `governing_workflow_version` nor the
  `implementation_review_stages` ledger, so a `"1"`, a `"2.1"`, and a
  `"2.2"` item in the identical bundle state decide identically --
  genuinely new coverage, since that class had no `"2.2"` fixture
  anywhere before this. Closed the explicit `"1"`/`"2.1"` parity half
  with a new `tests/test_decision.py::ProtocolTwoTwoCompatibilityParityTest`:
  `decide()`'s `DECLINED_PHASES`/`GATE_REPORT_PHASES` classification is
  keyed on `work_item.phase` alone (never on `governing_workflow_version`),
  pinned by asserting every member of both sets classifies identically
  for `"1"`, `"2.1"`, and `"2.2"` -- including the two `"2.2"`-only
  phases (`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`/
  `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`) a `"1"`/`"2.1"` item
  never actually reaches in practice -- plus a direct `KNOWN_PHASES`
  cardinality/membership pin. No production file changes; this
  checkpoint is its own test addition, exactly as the plan specifies.
  Verified: the three new tests plus their surrounding classes
  (`tests/test_decision.py::ProtocolTwoTwoCompatibilityParityTest`,
  `tests/test_evidence.py::AwaitingManualExternalImplementationReviewTest`,
  `tests/test_evidence.py::AwaitingExternalImplementationReviewTest`, 15
  tests) pass; `tests/test_decision.py`+`tests/test_evidence.py` together
  (101 tests) pass; the repository's full test suite (442 tests, 1
  skipped -- the live-Claude-gated integration test, `CLAUDE_BIN` unset
  in this environment) passes.
- **CP5 -- COMPLETE.** Disposable-repository integration coverage of a
  genuine `"2.2"`-governed work item through the split
  implementation-review lifecycle. Added
  `tests/test_integration_disposable_repo.py::Protocol22ImplementationReviewGatesTest.
  test_five_seeded_states_report_or_gate_and_resume_never_self_approves`:
  builds one real git-committed disposable target repo with a real
  `WORKFLOW_STATE.json` entry, registry/mapping/artifacts-declaration
  files, and real `.ai-review/` `MANIFEST.md`/`REVIEW_FEEDBACK.md` files
  (via `tests.fixtures`'s existing builders -- no new fixture helper
  earned its keep), then drives the same `"2.2"` work item directly
  through `controller.job.execute_step` at all five states the plan
  names -- `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` (no ledger, declined,
  names `/review-implementation`); `AWAITING_MANUAL_EXTERNAL_
  IMPLEMENTATION_REVIEW` with no feedback (gate, names
  `/record-manual-implementation-review`); the same phase with `Status:
  BLOCK` on file (gate reports BLOCK, still declines); the same phase
  with `Status: APPROVE` not yet ingested (gate reports a verdict is on
  file, never invents the approval); and the reused terminal
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` with `APPROVE` on file (gate
  names the user-only `/approve-review implementation`) -- asserting at
  every step that `selected_action.automatic` is `False` and no command
  ever names `/approve-review`/`/accept-milestone`. A final
  `controller.job.resume` pass across the same five job records asserts
  none is reconciled, relaunched, or marked malformed, and none becomes
  an automatic self-approval. Also added
  `DisposableRepoRealWorkflowActionTest.
  test_real_review_plan_reaches_the_2_2_plan_review_expected_outcomes_row`
  (same live-Claude/`CLAUDE_BIN` gate as its existing sibling): seeds
  `WORKFLOW_CONFIG.json` with `default_workflow_version: "2.2"` and
  `"2.2"` added to `supported_versions` (both required, per
  `validate_governing_version`), then runs two real `controller step`
  calls -- row 7's `NoWorkItemYet` bootstrap (unaffected by CP1, proves
  nothing alone), then the discriminating step through
  `("AWAITING_LOCAL_PLAN_REVIEW", "2.2", "/review-plan")`, CP1's own
  added row, asserting no uncaught-exception crash (the pre-CP1 defect)
  and a durable reconciliation to one of that row's `to_any_of`
  outcomes. Verified: `tests/test_integration_disposable_repo.py` (14
  tests, 2 skipped -- both live-Claude-gated, `CLAUDE_BIN` unset in this
  environment) and the repository's full test suite (444 tests, 2
  skipped) pass with no regressions.
- **CP6 -- COMPLETE.** Full verification, verification-only (no source
  file changed by this checkpoint). Run from the repository root at
  `839777b`, in this repository's `unittest` idiom (there is no `pytest`
  here):
  1. Controller suite -- `python3 -m unittest discover -s tests -t .`:
     **OK, 444 tests, 2 skipped** (both skips are
     `DisposableRepoRealWorkflowActionTest`'s live-Claude-gated tests,
     which item 3 below then runs un-skipped).
  2. The frozen Workflow conformance suites under `scripts/`, each
     invoked directly as `python3 scripts/<name>_test.py` (`unittest
     discover`'s default `test*.py` pattern matches none of them). The
     set was **re-listed at implementation time** rather than taken from
     the plan's count: `find . -maxdepth 2 -iname "*_test.py"` returns
     nine files, and minus the two excluded below that is exactly the
     seven the plan names -- `workflow_acceptance_matrix_test.py` (OK,
     146, 18 skipped), `workflow_fingerprint_generalization_test.py`
     (OK, 79), `workflow_fingerprint_test.py` (OK, 218),
     `workflow_integration_test.py` (OK, 260, 1 skipped),
     `workflow_state_completion_obligations_test.py` (OK, 106),
     `workflow_state_test.py` (OK, 853), `workflow_test_harness_test.py`
     (OK, 19). **Zero failures across (1) and all seven** -- this
     checkpoint's exit condition.
  3. One live-worker end-to-end run, with a real `CLAUDE_BIN`
     (`/home/rodrigo/.local/bin/claude`) and `CONTROLLER_LIVE_WORKER=1`:
     `python3 -m unittest tests.test_integration_disposable_repo` --
     **OK, 14 tests in 632.7s, zero skipped**, so both live-Claude tests
     really executed rather than being gated out. The first emitted
     `DISPOSABLE_REPO_INTEGRATION_EVIDENCE` with
     `worker_outcome_classification: "SUCCESS"`, `pre_phase:
     "__NO_PHASE__"` -> `post_phase: "AWAITING_LOCAL_PLAN_REVIEW"`,
     `worker_session_id: 8a6ad7e7-f2f5-475b-a8f9-a0f83442489d`,
     `wall_clock_seconds: 306.5`, `controller_source_commit:
     839777b957d3dd80665f6a34d2471130f58692ea`; the second,
     `test_real_review_plan_reaches_the_2_2_plan_review_expected_outcomes_row`,
     passed too, confirming the seeded-state assertions and the
     live-orchestration path agree rather than each merely passing in
     isolation.

  **Excluded from the exit condition entirely**, not merely allowed to
  fail, and named here rather than silently dropped:
  `scripts/workflow_fingerprint_demo_test.py` and
  `scripts/workflow_state_demo_test.py`. Both assert against the
  **Workflow** repository's own real artifacts (a `workflow-v2-1-core`
  work item, `.ai-review/current` bundle content), none of which exists
  in this repository; `scripts/` is this work item's declared
  `implementation_stage.excluded_prefixes` entry and repairing them is
  out of scope per Non-goals. Measured at `839777b`:
  `workflow_fingerprint_demo_test.py` **Ran 15, FAILED, errors=5,
  skipped=4** (identical to the plan's base-commit baseline) and
  `workflow_state_demo_test.py` **Ran 47, FAILED, failures=11,
  errors=16** (baseline recorded `errors=17`, i.e. one *fewer* error
  than at base -- not a regression, and not a narrowing of the list).

### Self-review of the full milestone diff

Performed at `SELF_REVIEWING_IMPLEMENTATION` over `21a304d..HEAD`. **No
blocking findings.** Two Important findings and one Minor one, all three
fixed in the tree before bundle generation:

- **Important -- stale row-count prose CP1's own sweep missed, in
  `controller/job.py`.** The plan scoped CP1's prose sweep by `grep -n
  "seven\|six-triple" job.py`, which does not match the `ExpectedOutcome`
  table's own section banner at `job.py:306`: it read "Six rows, one per
  automatic `(from_phase, governing_workflow_version, action)` triple the
  combined CP4/CP4B decision engine can ever produce" -- correct before CP1
  (six automatic triples, with row 7's `NO_PHASE` bootstrap deliberately
  not counted among them), false after it, since the four new `"2.2"` rows
  are four more such triples. Restated to name all eleven rows and the
  three groups they come from.
- **Important -- the same staleness in `controller/decision.py:301`.** The
  plan's sweep was scoped to `job.py` alone, so it could not see
  `SELECTED_COMMANDS`' own comment ("Seven is the *row* count of the
  automatic mapping above -- two of these four files are selected from two
  rows each, and `milestone-plan.md` from three"). Both the count and the
  per-file breakdown moved with CP1: the mapping now has eleven rows, and
  `milestone-plan.md` is selected from four of them, `apply-plan-review.md`
  from three, `review-plan.md`/`record-manual-plan-review.md` from two
  each. Restated, and the dangling "above" replaced with an explicit,
  documentation-only cross-reference to `job.EXPECTED_OUTCOMES` (naming the
  direction of the real dependency, `job` -> `decision`, so the note cannot
  be misread as an import). The *conclusion* the comment draws is unchanged
  and still correct: the partition is over files, so four more rows leave
  `SELECTED_COMMANDS` at four members.
- **Minor -- CP5's seeded fixture wrote its mapping file to the wrong
  directory.** `Protocol22ImplementationReviewGatesTest._seed` placed
  `<id>-mapping.json` under `docs/ai-workflow/registry/` while its own
  docstring claimed the files sit "at the same paths a genuine work item's
  own entry would name"; real mapping files live under
  `docs/ai-workflow/requirements/` (this repository's own work item
  included). Inert either way -- no Controller code path reads
  `mapping_path` -- but the fixture now matches the convention it claims to
  reproduce.

No production behaviour changed in this pass: both Important fixes are
comment-only, and the Minor one moves a test fixture file. The eleven-row
table, `WorkflowSnapshot.supported_versions`, and every test CP1-CP5 added
are byte-identical to their checkpoint commits.

**One out-of-scope observation, recorded for the reviewer, deliberately not
fixed here.** `controller/evidence.py`'s `AWAITING_FUNCTIONAL_REVIEW`
branch returns `automatic=True` with `/apply-functional-review
<work-item-id>` when `FUNCTIONAL_REVIEW.md` findings are present and
unconsumed (pinned by `tests/test_evidence.py::...::
test_unconsumed_findings_are_automatic`), but `job.EXPECTED_OUTCOMES` has
no row for that action at any `governing_workflow_version`, so
`job.execute_step` would reach `_expected_outcome_for` and raise the same
uncaught `AssertionError` CP1 fixed for `"2.2"` plan review. This predates
this milestone's base commit `21a304d`, is entirely
version-independent (it affects `"1"`, `"2.1"` and `"2.2"` items alike),
and is not in this milestone's diff or plan -- adding a row is new
behaviour needing its own plan approval, so it is reported rather than
silently fixed.

### Full verification

Run from the repository root at the self-reviewed tree, in this
repository's `unittest` idiom, exactly as CP6's own section specifies:

1. `python3 -m unittest discover -s tests -t .` -- **OK, 444 tests, 2
   skipped** (both skips are `DisposableRepoRealWorkflowActionTest`'s
   live-Claude-gated tests, `CLAUDE_BIN` not exported in this session).
2. The seven frozen Workflow conformance suites, each as `python3
   scripts/<name>_test.py`: `workflow_acceptance_matrix_test.py` (OK, 146,
   18 skipped), `workflow_fingerprint_generalization_test.py` (OK, 79),
   `workflow_fingerprint_test.py` (OK, 218), `workflow_integration_test.py`
   (OK, 260, 1 skipped), `workflow_state_completion_obligations_test.py`
   (OK, 106), `workflow_state_test.py` (OK, 853),
   `workflow_test_harness_test.py` (OK, 19).

**Zero failures across (1) and all seven.** `scripts/
workflow_fingerprint_demo_test.py` and `scripts/workflow_state_demo_test.py`
remain excluded for the reason CP6 records above, unchanged by this pass.

The live-worker run (`CONTROLLER_LIVE_WORKER=1`, real `CLAUDE_BIN`) was
**not** repeated at this tree: CP6 already ran it once end to end at
`839777b` (14 tests, zero skipped, 632.7s), which is the "at least once"
the plan asks for, and this self-review pass changed only comments in
`controller/` and one fixture path inside the *offline*
`Protocol22ImplementationReviewGatesTest` -- the offline half of that same
module re-ran green in (1) above. No orchestration behaviour the live test
exercises was touched.

**Next action:** the self-review and full verification are complete and the
implementation-review bundle has been generated. This work item is
`"2.2"`-governed, so the phase is now
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`: run `/review-implementation` for
the authoritative `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage, which on
`APPROVE` hands off to `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`.
