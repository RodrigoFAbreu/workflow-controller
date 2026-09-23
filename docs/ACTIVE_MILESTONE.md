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
- **CP2-CP9 -- pending**, in registry order.
