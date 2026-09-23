# Active Milestone

## Milestone

`workflow-controller-automatic-lifecycle-orchestration` -- Controller automatic lifecycle
orchestration (governing workflow version `"2.2"`).

## Goal

Controller Generation 1 reports every implementation-stage phase and launches workers only for
the plan stage. This milestone makes it drive the automation-safe implementation-stage actions.
One `workflow-controller run` then carries a `"2.2"` work item from `IMPLEMENTING` through
checkpoint implementation, the final self-review pass, local implementation review and local
`REVISE` remediation, up to the next genuine human gate, without weakening any Workflow
guarantee. It adds one general automatic-dispatch rule, implementation-stage expected outcomes
with artifact postconditions, protocol-2.2 correctness fixes, worker lifecycle and concurrency
safety, and role-based model/effort routing.

The approved plan is the scope authority:
`docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md` (revision 5, approved at
`035c320`), with its registry and requirements mapping under `docs/ai-workflow/registry/` and
`docs/ai-workflow/requirements/`. Its "Non-goals" section lists what stays out of scope. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-worker-execution-hardening.md`.

## Checkpoint progress

Ground truth is `docs/ai-workflow/WORKFLOW_STATE.json`.

- **CP1 -- complete.** Plan-stage decision golden, and the implementation-stage evidence
  readers. None of the readers is wired into a decision yet.
  - **First act, before any other change:** `tests/golden/generate_plan_stage_decisions.py`
    generated `tests/golden/plan_stage_decisions.json` from the base commit's unchanged
    `decision.py`/`evidence.py`. It holds 37 evidence scenarios (from the existing plan-stage
    tests: verdicts, admissibility clauses, stale-bundle recovery variants, the `REJECTED`
    marker) at all 16 reachable plan-stage `(phase, version)` combinations: 592 cases, 62
    distinct decisions. Each decision body is stored once under a content-derived id, which
    keeps the file at about 150 KB instead of about 480 KB.
    `tests/test_golden_plan_stage_decisions.py` re-derives every case, normalises it with the
    generator's own `normalise` (temp root -> `<ROOT>`, 40-hex tokens -> `<SHA>`), and requires
    the result to be byte-equal to the golden.
  - `WorkItemView` gains `implementation_review_stages` (verbatim) and
    `technical_review_block_pins` (a verbatim tuple, `()` when absent). A present pins value
    that is not a list of objects is `MalformedWorkflowStateError`, so it is never read as "no
    pins".
  - `evidence`:
    - `_MANIFEST_LABELS` gains `review_content_id`, `implementation_revision`,
      `reviewed_implementation_head` and `worktree_root`.
    - `implementation_bundle_coherence(...) -> (coherent, clause, detail)`, with the clause
      codes `IMPLEMENTATION_BUNDLE_CLAUSES`.
    - `read_review_request_fields`, which uses the generator's own whole-file
      `review_content_id:` pattern.
    - `read_implementation_review_ledger -> LedgerView`. It is fail closed: a malformed ledger
      reads as no stage recorded, and `malformed` says why.
    - `committed_work_item` and `committed_checkpoint_statuses`.
    - `commit_trailers`, `bundle_generation_record_role` and
      `generation_record_view -> GenerationRecordView`.
    - `evaluate_manual_implementation_stage_admissibility` and
      `evaluate_apply_implementation_review_admissibility`.
  - `tests/fixtures.py`: `build_manifest_text` gains the implementation-stage labels as
    optional lines (output is byte-identical when they are omitted), and the new helpers are
    `target_worktree_root`, `build_implementation_manifest_text` and
    `write_implementation_manifest`.
  - Judgment calls, where the plan text left a detail open:
    - The manual evaluator takes an extra keyword, `current_worktree_root`. It is pure, and
      its `worktree_root` clause needs the live value.
    - `committed_checkpoint_statuses` is keyed by `(work_item_id, checkpoint_id)` tuples, as
      the plan's prose says.
    - `GenerationRecordView` also carries `head`, so CP4B's malformed-T gate can name the
      commit.
    - `newer_records` counts every commit that carries this work item's record trailer pair,
      whatever its role, the same way Workflow's own discovery does.
    - `build_manifest_text` gains four optional labels. The plan says three, but the
      implementation manifest helper needs all four.
  - Verified with `python3 -m unittest tests.test_golden_plan_stage_decisions
    tests.test_evidence tests.test_target_state`: 199 tests OK.
    `tests.test_write_containment tests.test_package_structure`: 17 tests OK.
    `python3 tests/golden/generate_plan_stage_decisions.py --check` reports the golden as
    current.
  - Mutation checks run in a scratch copy were all caught: changed plan-stage gate or reason
    text fails the golden, and removing the work-item check from the record role, the
    worktree-root clause, the hard `REVISE` bundle-id clause, the ledger's `APPROVE`-only rule
    or the committed read each fail the new tests.
- **CP2 -- complete.** Expected outcomes for the implementation stage, in `controller/job.py`
  only. No decision changes yet: `evidence.decide` still declines or gates every
  implementation-stage phase until CP3's dispatch rule, and the plan-stage golden is unchanged.
  - `ExpectedOutcome.postcondition`/`postcondition_phases` became
    `postconditions: tuple[(frozenset[phase], PostconditionFn), ...]`. The seven plan-stage rows
    moved to single-entry tuples with unchanged behaviour. `_row_clauses_failure` evaluates the
    one entry whose phase set holds the observed phase (`_postcondition_for_phase`), so
    `execute_step`, `_reconcile_completed` and `_reconcile_launched` still share one helper.
    `property_table_violations` requires each entry to be a non-empty phase set paired with a
    callable. The phase sets must be pairwise disjoint and each a subset of `to_any_of`. A row
    may declare none.
  - `PRE_STATE_FIELDS` gains `bundle_manifest_bundle_id`, the pre-state manifest's `bundle_id`
    (`None` for the bootstrap). The field count goes from 17 to 18. A record without the field
    reads as "not satisfied".
  - Plan-stage `BLOCK`-predicate fix. Rows 3/3' and row 16 share
    `_block_feedback_bound_to_pre_state_bundle`. It compares the feedback's
    `Reviewed bundle ID` with a non-null `bundle_manifest_bundle_id`, never `current_bundle_id`.
    The plan-stage role is normalised as before, and the implementation role must match
    exactly. Rows 3/3' `predicate_inputs` are now `{bundle_manifest_bundle_id}`.
  - Seven rows, 12-18. `EXPECTED_OUTCOMES` goes from 11 to 18 rows, as in the plan table, with
    no `"1"` row:
    - `_predicate_checkpoint_completed_durably`;
    - `_postcondition_self_review_entered_durably`;
    - `_postcondition_implementation_bundle_coherent` (the `REJECTED` marker first, then
      `implementation_bundle_coherence` at the live `HEAD`);
    - `_postcondition_implementation_bundle_regenerated`;
    - `_predicate_local_implementation_block_current`;
    - the local and manual `APPROVE`/`REVISE` postconditions.

    `_INCOMPLETE_EFFECT_PHASES` stays empty.
  - `WriterCall.trailing_calls` is an explicit `(function, justification)` allowlist. Step 1f
    lists `committed_checkpoint_status` and `release_checkpoint`. Property 5 skips only those
    names, and only after the declared call. An entry that does not occur there is reported
    as stale. The table property also rejects an entry with no justification, a duplicate, a
    non-identifier, or the declared call itself.
  - `tests/fixtures.py` adds:
    - `write_registry`, `update_workflow_state`, `implementation_review_ledger`, `commit_paths`;
    - `build_implementation_target`, which builds a committed seed with `.ai-review/` ignored;
    - the scripted-worker helpers `forced_automatic_action`, `scripted_worker`,
      `complete_checkpoint_effect`, `generation_effect` and `review_writes_effect`, plus
      `state_entry`.
  - Judgment calls, where the plan text left a detail open:
    - The row tests drive the real `execute_step` with `evidence.decide` patched to the
      row's automatic action, because selection is CP3's. The worker is `fake_claude.py`,
      preceded by a scripted side effect. The resume tests capture the pre-state for real with
      `_capture_pre_state`, then apply the same effect.
    - Registry completion at `HEAD` reuses `target_state._resolve_registry_complete` over the
      committed statuses. That function is the one reader of the registry's checkpoint ids.
    - No `BranchSpec.within` was added. The first `BLOCK` bullet in `review-implementation.md`
      is A6's own, and a test pins that.
  - Changed test assertions. Each is an intended change:
    - the row count (11 -> 18);
    - the pre-state field count (17 -> 18);
    - the pre-CP1 table filter in `MissingGoverningVersionRowRegressionTest` now also excludes
      the new implementation-stage rows;
    - the postcondition-shape tests were rewritten for the per-phase form;
    - plan-stage `BLOCK` fixtures no longer seed `current_bundle_id="b" * 64`, which no real
      Workflow state has.
  - Verified with `python3 -m unittest tests.test_job tests.test_job_validation tests.test_resume`:
    209 tests OK. The full suite (`python3 -m unittest discover -s tests -t .`) ran 655 tests:
    OK, 2 skipped. `python3 tests/golden/generate_plan_stage_decisions.py --check` reports the
    golden as current.
  - Mutation checks run in a scratch copy. Each of these fails at least one test:
    - dropping the predicate's committed clause, its `HEAD`-moved clause or its
      already-complete exclusion;
    - dropping either committed clause of the self-review postcondition;
    - dropping the regeneration `generation_head` clause, or the `REJECTED` clause;
    - reverting row 3 to `bundle_id`, or dropping its null guard;
    - accepting any role at row 16;
    - evaluating a postcondition at any phase;
    - dropping the stale-`trailing_calls` check or the overlap check;
    - dropping the local `APPROVE` content clause, the manual `APPROVE` feedback-binding
      clause, the manual `REVISE` unrecorded-stage clause, or the local `REVISE` work-item
      clause;
    - not capturing `bundle_manifest_bundle_id`.
- **CP3-CP9 -- pending**, in registry order.
