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
- CP4-CP6: not started.

**Next action:** continue with `/milestone-implement` to select and
implement CP4 (pin `decision.py`/`evidence.py`'s already-correct 2.2
implementation-review classification, and the 2.1 single-stage parity
guarantee, with discriminating regression tests).
