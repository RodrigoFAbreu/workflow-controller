# Active Milestone

## Status

**In progress.** `workflow-controller-protocol-2-2-compatibility`
(`governing_workflow_version: "2.2"`) is `IMPLEMENTING`. Plan revision 2 was
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
- CP3-CP6: not started.

**Next action:** continue with `/milestone-implement` to select and
implement CP3 (surface `WORKFLOW_CONFIG.json`'s `supported_versions` in
`target_state.py`'s `WorkflowSnapshot`).
