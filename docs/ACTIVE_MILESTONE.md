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
- CP3-CP6 -- not started.
