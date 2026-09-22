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
- **CP4 -- complete.** `evidence.decide` now gates at `AWAITING_LOCAL_PLAN_REVIEW`/
  `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` when `plan_bundle_coherence` fails, after the
  `REJECTED`-marker check and ahead of the per-phase handlers (so ahead of the local BLOCK
  gate). The gate's `what_is_required`/`safe_resume_command` carry
  `_plan_bundle_recovery_steps`: refresh `REVIEW_REQUEST.md` (`review_content_id` from
  `compute_review_content_id_plan_stage_for_work_item`), refresh `TEST_RESULTS.md`
  (`stage: plan (revision N)`, `head:`), then `prepare-ai-review.sh <base_commit> plan <id>`
  with the preflight-refusal clause. When `current/` or any author file is absent, the steps
  say "write", name the "Review request format", and a step 0 writes `CONTEXT_FILES.txt`
  from the newest `current.rejected-*` quarantine, if one exists. At those two phases the
  `REJECTED` gate keeps its precedence but now advertises the marker detail plus the same
  steps. Elsewhere it is unchanged. `AWAITING_EXTERNAL_PLAN_REVIEW` (`"1"`) and
  implementation-stage phases are not gated. Existing fixtures at the gated phases now write
  coherent plan manifests (`tests/test_evidence.py`, `tests/test_job_validation.py`; no
  assertion weakened). Verified: `python3 -m unittest tests.test_evidence
  tests.test_job_validation tests.test_decision` -- 191 tests OK. Full Controller suite:
  503 tests OK, 2 live tests skipped. With the gate disabled, 10 of the new tests fail.
- **CP5 -- complete.** The RepFlow case (a `"2.2"` item at `REVISING_PLAN`, revision 10,
  whose `/apply-plan-review` worker publishes revision 11 but leaves the revision-10
  bundle) is pinned end to end. `tests/test_job.py::PartialApplyPlanReviewExecuteTest`:
  `execute_step` -> `FAILED` / `postcondition_not_satisfied` naming revisions 10 and 11;
  the next `execute_step` -> `GATE_BLOCKED` with no worker launched (a fake worker that
  would fail on `FAKE_CLAUDE_REQUIRE_FILE`), and its gate carries CP4's three `refresh`/`run`
  steps with revision 11 and the seeded `base_commit`; a positive control with a revision-11
  manifest -> `FINISHED`; the pre-fix demonstration (`patch.dict` over
  `_EXPECTED_OUTCOMES_BY_KEY` with only this row's postcondition stripped) -> `FINISHED`.
  `tests/test_resume.py::PartialApplyPlanReviewResumeTest`: `COMPLETED` -> `FAILED`;
  `LAUNCHED` -> `UnreconcilableJobError` with `postcondition_detail`, with a revision-11
  positive control -> `FINISHED`; the `"1"` self-loop with unchanged phase and HEAD ->
  `INTERRUPTED`, then the next decision is the automatic `/apply-plan-review wi-1`.
  `tests/test_cli.py::PartialApplyPlanReviewCliTest`: `controller step` exits
  `EXIT_WORKER_FAILED` (30), then `explain` renders the recovery steps. Verified:
  `python3 -m unittest tests.test_job tests.test_resume tests.test_cli` -- 120 tests OK. Full
  Controller suite: 512 tests OK, 2 live tests skipped. With `plan_bundle_coherence` forced
  coherent, the 5 fail-closed tests fail. The 4 controls still pass.
- **CP6 -- complete** (verification only, no code change). Run 2026-09-22/23 at HEAD
  `6598638`:
  1. `python3 -m unittest discover -s tests -t .` -- 512 tests OK, 2 live tests skipped.
  2. Frozen Workflow conformance suites, each `python3 scripts/<name>.py`, all exit 0:
     `workflow_acceptance_matrix_test` 146 OK (18 skipped),
     `workflow_fingerprint_generalization_test` 79 OK, `workflow_fingerprint_test` 218 OK,
     `workflow_integration_test` 260 OK (1 skipped),
     `workflow_state_completion_obligations_test` 106 OK, `workflow_state_test` 853 OK,
     `workflow_test_harness_test` 19 OK. `workflow_fingerprint_demo_test.py` and
     `workflow_state_demo_test.py` excluded as the plan names (they already fail at base).
  3. `CONTROLLER_LIVE_WORKER=1 python3 -m unittest -v tests.test_integration_disposable_repo`
     with the real `claude` binary: 14 tests OK (both live tests pass `bypassPermissions`
     explicitly; the `/milestone-plan` job was `FINISHED`, worker `SUCCESS`, session
     `632a3e62-...`). Manual live `python3 -P -m controller --runtime-dir <rt> step
     <target>` with **no** `--permission-mode` (so `DEFAULT_PERMISSION_MODE = "auto"`,
     `controller/cli.py:376`) against a disposable Workflow 2.5.1 / `"2.2"` target: exit 0,
     job `FINISHED`, `transition_verified: true`, `worker.permission_denials: []`, worker
     `SUCCESS` (session `a61e56c1-...`, 17 Bash tool calls, 125 s),
     `__NO_PHASE__ -> AWAITING_LOCAL_PLAN_REVIEW` (work item `hello-file`). The stop
     condition did not fire.
  4. Stale-bundle recovery drill on that same target, frozen Workflow entry points only
     (`generate_registry`/`generate_mapping`/`write_registry_and_mapping`, the plan table
     re-embedded from `render_registry_markdown`, `(Revision N)` bumped,
     `publish_plan_revision` through `state_transaction`):
     - Stale leg: revision 2 published over the revision-1 bundle. `controller step` exit
       10 (`GATE_BLOCKED`, no worker), gate `manifest plan_revision 1 != state
       plan_revision 2` with the three `refresh`/`refresh`/`run` steps
       (`stage: plan (revision 2)`, `prepare-ai-review.sh 5aa8a73... plan hello-file`).
       Those steps performed verbatim -> generator exit 0, manifest `plan_revision: 2`;
       `controller explain` -> `next automatic action: /review-plan hello-file`.
     - Withdrawal leg: revision 3 published; only `REVIEW_REQUEST.md` refreshed; the
       generator withdrew the bundle (`TEST_RESULTS.md states 'stage: plan (revision
       2)'`, `current/` quarantined to `current.rejected-0bfff56b...`). `controller step`
       exit 10 with the withdrawn variant: `current is absent (withdrawn or never
       generated)`, step 0 `write .../CONTEXT_FILES.txt` naming the quarantine directory,
       steps 1-2 `write ... (in REVIEW_PROTOCOL.md's "Review request format" shape)`,
       step 3 the generator -- never the bare generator. Those steps performed verbatim
       (author files restored from the quarantine, then the two lines refreshed) ->
       generator exit 0, manifest `plan_revision: 3`; `controller explain` ->
       `/review-plan hello-file`.
     Neither leg needed a step the gate did not name.
