# Archived milestone narrative — `workflow-controller-worker-execution-hardening`

Archived verbatim from `docs/ACTIVE_MILESTONE.md` on 2026-09-23, as part of
`/accept-milestone`'s own step 5, at this work item's own acceptance. This
file's content below this notice is unedited from the version
`docs/ACTIVE_MILESTONE.md` carried immediately before this acceptance
(functional review: checklist evidence commit `26a28d3b54506c9345badbbf48ca7b9a2a44a45e`,
overall **PASS**). Round 1 found two checklist defects and no implementation
defect (F1, F2). They were applied as a no-code-change correction in
`26a28d3`, and the re-test of the corrected checklist passed with no new
findings. Round 1's `.ai-review/feedback/FUNCTIONAL_REVIEW.md` is marked
consumed.

**Not archived here, deliberately** (same reasoning the prior archived
milestones' own files already state for their own milestones):
`docs/ai-workflow/CONTROLLER_WORKER_EXECUTION_HARDENING_PLAN.md`, its registry
(`docs/ai-workflow/registry/workflow-controller-worker-execution-hardening-registry.json`),
and its requirements mapping
(`docs/ai-workflow/requirements/workflow-controller-worker-execution-hardening-mapping.json`)
all remain at their original paths, unmoved and unmodified —
`docs/ai-workflow/WORKFLOW_STATE.json`'s own
`work_items["workflow-controller-worker-execution-hardening"]` entry still
declares these exact paths as its `plan_path`/`registry_path`/
`mapping_path`, and `plan_approval.review_content_manifest` pins their blobs
at these same paths, so moving any of them would make that historical
approval record's own manifest unresolvable. The
`workflow-controller-worker-execution-hardening` entry in
`docs/ai-workflow/WORKFLOW_STATE.json` (`work_items` map, phase
`MILESTONE_COMPLETE`) and the full Git history of its approvals are
likewise untouched by this archival.

---

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

## Functional review checklist

Implementation revision 2, technical approval `920c8fd` (both implementation-review stages
`APPROVE`, `review_content_id` `0aa60044...`). Automated state is current: 514 Controller
tests OK (2 live skips) and the seven frozen Workflow suites green at `7904088`. Only
`WORKFLOW_STATE.json` and this file changed after that. Record findings in `.ai-review/feedback/FUNCTIONAL_REVIEW.md`.

**Round 1 findings (applied, no code change).** The first pass found two defects in this
checklist and none in the implementation. Its `FUNCTIONAL_REVIEW.md` is now marked consumed.
- F1: `python3 -P -m controller` failed as written with `No module named controller`. `-P` keeps
  `$C` off `sys.path`, and here the Controller is installed only through pipx. Setup step 2 now
  exports `PYTHONPATH=$C`.
- F2: `inspect`/`explain` had no `--runtime-dir`, so they wrote `$C/.controller/identity.json`.
  They now use `$RT`, and step 8 checks that `$C/.controller/` is untouched.

No expected result changed. That pass saw every expected result in flows 1-7 with `PYTHONPATH=$C`
added. The new lines are Setup steps 2 and 4, the `explain` command form, and the step 8 check.

### Setup

1. In this checkout (`C=/home/rodrigo/Workspace/workflow-controller`), `git status --porcelain`
   is empty. `workflow-manager` and `claude` are on `PATH`.
2. In one shell, used for every later step:
   `C=/home/rodrigo/Workspace/workflow-controller; export PYTHONPATH=$C; W=$(mktemp -d); T=$W/target; RT=$W/rt; RTF=$W/rt-fake; touch $W/.start`.
   `python3 -P` keeps the current directory off `sys.path`. Unless `controller` is installed into
   the system `python3` (here it is installed only through pipx), `PYTHONPATH=$C` is what lets
   `-m controller` import this checkout. The Controller's own tests use the same form
   (`tests/test_integration_disposable_repo.py`). Workers never inherit it.
3. Build a disposable `"2.2"` target with the same fixture the live tests use:
   `cd $C && python3 -c "from pathlib import Path; from tests.test_integration_disposable_repo import _seed_target_2_2; _seed_target_2_2(Path('$T'))"`.
   Expected: `workflow-manager verify $T` exits 0 (Workflow 2.5.1). `$T` is clean.
   `docs/ACTIVE_MILESTONE.md` there names the trivial `hello-file` milestone.
4. `cd $C && python3 -P -m controller --runtime-dir $RT inspect $T` exits 0 and reports no work
   item yet. Below, `explain` always means `python3 -P -m controller --runtime-dir $RT explain $T`,
   run from `$C`. Every Controller invocation passes `--runtime-dir`, so none of them writes
   `$C/.controller/`.

### Test data

Only the `hello-file` milestone seeded in step 3. Flows 4-6 hand-edit bundle files, and only in
the disposable `$T`, never in this repository. Let `B=$T/.ai-review/hello-file`.

### Flows (no cost except flow 3)

1. **Default worker permission mode is `auto`.** Use a fake worker, so this costs nothing:
   `cd $C && FAKE_CLAUDE_DIAG_FILE=$W/argv-default.json python3 -P -m controller --runtime-dir $RTF --claude-binary $C/tests/fake_claude.py step $T; echo exit=$?`,
   then `python3 -c "import json; print(json.load(open('$W/argv-default.json'))['argv'])"`.
   Expected: the argv contains `/milestone-plan` and `--permission-mode`, `auto`. The exit is
   non-zero, usually 30 (`FAILED`), because the fake worker changes nothing. That is expected.
   `git -C $T status --porcelain` stays empty.
2. **An explicit override passes through unchanged.** Run flow 1 again with
   `--permission-mode acceptEdits` before `step` and `argv-override.json` as the diag file.
   Expected: the argv contains `--permission-mode`, `acceptEdits`, and no `auto`.
3. **A real default-`auto` worker can run Workflow operations.** This is live: one real
   `claude` `/milestone-plan` session, about 2 minutes, and it costs money.
   `cd $C && python3 -P -m controller --runtime-dir $RT step $T; echo exit=$?`
   (no `--permission-mode`).
   Expected:
   - The exit is 0.
   - The newest record in `$RT/jobs/` has `status: FINISHED`, `transition_verified: true`,
     `worker.permission_denials: []` and `worker.is_error: false`.
   - `$T`'s `hello-file` is at `AWAITING_LOCAL_PLAN_REVIEW`, `plan_revision` 1.
   - `$B/current/MANIFEST.md` says `plan_revision: 1`.
   - `explain` reports `next automatic action: /review-plan hello-file`.
4. **Zero-byte stubs select the "write" recovery (the round-1 manual `I1` fix).** Needs flow 3.
   `mv $B/current $B/current.rejected-manualcheck && mkdir $B/current && : > $B/current/REVIEW_REQUEST.md && : > $B/current/TEST_RESULTS.md && : > $B/current/CONTEXT_FILES.txt`.
   Then run `explain`, and
   `python3 -P -m controller --runtime-dir $RT step $T; echo exit=$?`.
   Expected:
   - `explain` shows a human gate naming `.../current/MANIFEST.md is missing or unreadable`.
   - Its steps are: step 0 `write .../CONTEXT_FILES.txt, restoring the previous round's author
     files from .../current.rejected-manualcheck/`; steps 1-2 `write ...` (naming the
     "Review request format"); step 3 `scripts/prepare-ai-review.sh <base> plan hello-file`.
   - No step says `refresh`.
   - `step` exits 10 with a `GATE_BLOCKED` job record and no worker session.
5. **The gate clears once the bundle is coherent (control).**
   `rm -rf $B/current && mv $B/current.rejected-manualcheck $B/current`.
   Expected: `explain` again reports `next automatic action: /review-plan hello-file`, with no gate.
6. **A stale manifest revision selects the "refresh" recovery.** This is simulated.
   `sed -i 's/^plan_revision: 1$/plan_revision: 0/' $B/current/MANIFEST.md`, then run `explain`.
   Expected:
   - A gate naming `manifest plan_revision 0 != state plan_revision 1`.
   - Steps `1. refresh ...REVIEW_REQUEST.md`, `2. refresh ...TEST_RESULTS.md`
     (`stage: plan (revision 1)`, `head:`), and `3.` the generator. There is no step 0.

   Undo with `sed -i 's/^plan_revision: 0$/plan_revision: 1/' $B/current/MANIFEST.md`, and
   `explain` is back to `/review-plan hello-file`.
7. **The observed partial `/apply-plan-review` is caught (automated, needs a misbehaving
   worker).** `cd $C && python3 -m unittest -v tests.test_job.PartialApplyPlanReviewExecuteTest tests.test_resume.PartialApplyPlanReviewResumeTest tests.test_cli.PartialApplyPlanReviewCliTest`.
   Expected: all OK. They pin these results:
   - `FAILED` / `postcondition_not_satisfied` naming revisions 10 and 11;
   - then `GATE_BLOCKED` with no worker;
   - `controller step` exit 30;
   - the resume paths.
8. `find $C/.controller -newer $W/.start 2>/dev/null` prints nothing, because this checkout's
   runtime root was never written. Then clean up with `rm -rf $W`.

### Known limitations and out of scope

- The stale/withdrawn-bundle gate fires only at `AWAITING_LOCAL_PLAN_REVIEW` and
  `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`. It does not fire at `AWAITING_EXTERNAL_PLAN_REVIEW`
  (`"1"`) or at any implementation-stage phase.
- The Controller never repairs a bundle itself. The recovery steps are for a human.
- Controller Generation 1 still does not orchestrate implementation review. Its
  `APPLYING_REVIEW_FEEDBACK` gate text is stale for `"2.2"`. Both are known and outside this
  milestone.
- If `auto` is unavailable for an account, the worker fails visibly (`FAILED`). The remedy is an
  explicit `--permission-mode`. `bypassPermissions` stays reserved for disposable repositories.
- `REJECTED`-marker wording (round-1 manual `O1`) is covered by `tests.test_evidence` only.
  There is no manual flow for it.
- Deferred optional review findings:
  - the "write" `TEST_RESULTS.md` step cites the "Review request format" section;
  - a deliberately empty `CONTEXT_FILES.txt` is treated as absent (the conservative choice),
    and this is not yet documented.
- Out of scope for this milestone:
  - Workflow Manager's classification of `.workflow-manager/installation.json`;
  - `/apply-plan-review` publication ordering;
  - multi-harness work.
