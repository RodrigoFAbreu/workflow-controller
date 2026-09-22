# Active Milestone

## Milestone

`workflow-controller-worker-execution-hardening`

## Goal

Harden Workflow Controller worker execution and reconciliation based on defects observed during real RepFlow dogfooding.

### Scope

1. **Default lifecycle-worker permission mode**
   - Controller lifecycle workers currently default/effectively launch with `--permission-mode acceptEdits`.
   - This allowed file edits but denied required Bash/Python Workflow operations in non-interactive workers.
   - Change the default lifecycle-worker permission mode to `auto`.
   - Preserve explicit user-supplied `--permission-mode` overrides.
   - Add regression coverage proving the default worker invocation uses `--permission-mode auto` and explicit overrides remain respected.

2. **Post-worker reconciliation correctness**
   - Controller can currently mark a job `FINISHED` when the expected Workflow phase transition is observed even if required durable postconditions are incomplete.
   - RepFlow dogfooding reproduced `/apply-plan-review` transitioning `REVISING_PLAN -> AWAITING_LOCAL_PLAN_REVIEW` while bundle regeneration failed, leaving Workflow state at plan revision 11 with the current review bundle still at revision 10.
   - Strengthen reconciliation so phase transition alone is not sufficient for successful completion where the action requires coherent review/bundle artifacts.
   - Use authoritative Workflow state/artifacts and existing invariants rather than trusting worker prose or duplicating Workflow business logic unnecessarily.
   - Add regression coverage reproducing the observed partial `/apply-plan-review` case.

### Out of Scope

- Workflow Manager bootstrap classification of `.workflow-manager/installation.json`.
- Changes to Workflow `/apply-plan-review` publication ordering.
- Agent/harness portability or multi-harness runtime work.

## Checkpoint progress

Ground truth is `docs/ai-workflow/WORKFLOW_STATE.json`; plan:
`docs/ai-workflow/CONTROLLER_WORKER_EXECUTION_HARDENING_PLAN.md` (revision 3, approved).

- **CP1 -- complete** (commit `5c14e70`). `job.DEFAULT_PERMISSION_MODE` is `auto`;
  explicit `--permission-mode` values pass through unchanged.
- **CP2 -- complete.** `evidence.read_manifest_fields` additively reads the manifest's
  `stage:`/`work_item_id:`/`plan_revision:` lines; `evidence.plan_bundle_coherence(root,
  work_item_id, plan_revision)` is the single revision-level coherence reader CP3/CP4 will
  call. `tests/fixtures.build_manifest_text` gained the matching optional lines
  (byte-identical output when omitted). Verified: `python3 -m unittest tests.test_evidence
  tests.test_job_validation tests.test_resume` -- 162 tests OK.
- **CP3 -- complete.** `ExpectedOutcome` gained a `postcondition`/`postcondition_phases`
  column; `_postcondition_plan_bundle_coherent` (row 7 resolves the single new work item
  key) calls `evidence.plan_bundle_coherence` and is attached to all seven
  plan-bundle-producing rows. One shared helper, `_row_clauses_failure`, now evaluates the
  phase/predicate/postcondition clauses for both `_verify_transition` (`execute_step`) and
  `_row2_verified` (resume): a failed postcondition is `FAILED` /
  `postcondition_not_satisfied` with `postcondition_detail` on execute and `COMPLETED`
  resume; on `LAUNCHED` resume, moved state raises `UnreconcilableJobError` carrying
  `postcondition_detail`, unchanged phase+HEAD stays `INTERRUPTED`.
  `property_table_violations` checks the column's shape (with negative tests).
  `tests/fake_claude.py` gained `FAKE_CLAUDE_WRITES`; 11 existing `FINISHED` fixtures now
  also write the coherent manifest (no assertion weakened). Verified: `python3 -m unittest
  tests.test_job tests.test_job_validation tests.test_resume` OK, and the full Controller
  suite (`python3 -m unittest discover -s tests -t .`) -- 490 tests OK, 2 live tests
  skipped. With postconditions stripped from the table, 22 of the new assertions fail.
- CP4-CP6 -- not started.
