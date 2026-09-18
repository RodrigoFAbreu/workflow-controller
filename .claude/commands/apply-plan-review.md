---
description: Apply external plan-review feedback and revise the plan.
argument-hint: "[work-item-id]"
state_writer: true
review-subject: verdict
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `REVISING_PLAN` state of `docs/ai-workflow/MILESTONE_WORKFLOW.md`.

`<bundle_dir>`/`<feedback_dir>` below resolve per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir(repo_root, work_item_id,
stage="plan")`/`resolve_feedback_dir(repo_root, work_item_id)`). This is a
plan-stage command, so `<bundle_dir>` is **always**
`.ai-review/<work_item_id>/current/` -- the `stage="plan"` argument is
required and load-bearing, never decorative: without it the resolver takes
its compatibility branch and answers the flat `.ai-review/current/` for a
work item with nothing under `.ai-review/<work_item_id>/` yet (a
brand-new milestone's first plan bundle, or the first
`/milestone-plan <child-id>` on a remediation child), while the generator
writes and validates the scoped one. `<feedback_dir>` takes no stage
argument: `feedback/` is stage-agnostic and keeps the scoped-else-flat
rule for every stage alike.

0. **Dual-mode branch** (Workflow v2.1, `WF4a-ii`/`WF4a-iv`): resolve the
   target work item first -- the id named in `$ARGUMENTS`, or
   `active_work_item_id` from `docs/ai-workflow/WORKFLOW_STATE.json` if
   omitted. Refuse with a named error if neither resolves to an existing,
   non-terminal `work_items` entry -- never guess. The explicit id is what
   makes a work item that is *not* `active_work_item_id` drivable at all
   (`D1`: the pointer is resume focus, not an execution lock), which is the
   only way a `<parent-id>-remediation-<n>` child's own `REVISE` round can
   be applied while its parent holds the pointer. Then read that work
   item's `governing_workflow_version` from
   `docs/ai-workflow/WORKFLOW_STATE.json` (missing entirely is equivalent to
   `"1"`).
   - **`governing_workflow_version: "1"`**: steps 1-7 execute exactly as
     written, exiting to `AWAITING_PLAN_APPROVAL`.
   - **`governing_workflow_version: "2.1"`**: steps 1-6 execute identically;
     step 7 is replaced by the revised exit step below — the two-stage
     local-then-manual-external plan-review protocol (`D-Plan-Review-Stages`,
     `/review-plan`, `/record-manual-plan-review`). This branch is not, and
     never was, inert in general (`workflow-2.5.0` correction,
     `LOCAL_MODEL_PLAN_REVIEW` round 5): it governs every `"2.1"`-governed
     work item anywhere this Workflow is installed, and this repository now
     carries two of its own (`plan-amendment-mechanism`,
     `implementation-review-two-stage`) whose own plan-stage `REVISE`
     rounds exercise it directly.
   - **`governing_workflow_version: "2.2"`** (workflow-2.5.0,
     `D-Implementation-Review-Version-Activation`): takes the identical
     `"2.1"` branch immediately above -- `TWO_STAGE_PLAN_REVIEW_VERSIONS`
     already covers both, and the `"2.2"` implementation-review bump
     changes nothing about the plan-review protocol this command drives.
   - **Any other `governing_workflow_version`** (round-7 optional
     finding 2): refuse cleanly, naming the actual value -- never guess
     which branch above applies.
1. Read `<feedback_dir>/REVIEW_FEEDBACK.md`. If it does not exist, stop
   and say so — do not proceed on assumed feedback. Validate its binding
   fields (`workflow_fingerprint.parse_review_feedback_binding_fields`/
   `assert_feedback_matches_bundle` against the current recomputed
   `bundle_id`/`base_commit`/`work_item_id`, `WFR-03`) — stale or
   mismatched feedback is a reason to stop and say so, not to apply.
   **`REJECTED`-bundle refusal, first of two** (`WFR-67`): also call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here; a `BundleRejectedError` stops the command, naming
   the marker path and its recorded detail.
2. For every Blocking, Important, and Optional finding: validate it against
   the actual repository (read the relevant code/docs, do not take the
   finding's premise on faith).
3. **`REJECTED`-bundle refusal, second of two, under this step's own
   mutation guard** (`WFR-67`): immediately before the first edit below,
   re-call `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` — a withdrawal landing between step 1 and here must
   still be caught. Apply accepted findings to the **authoritative**
   execution/reference plan document (`plan_path`) only -- never to
   `<bundle_dir>/PLAN.md`, which since `WFR-67` the generator derives
   unconditionally from a private pinned snapshot of every plan-stage
   protected path. An edit made to the bundle copy is silently
   overwritten by the next generation and never reaches the reviewer.
4. For any finding you reject, write the rejection with concrete repository
   evidence (file path, line, existing test, or doc reference) directly in
   the plan doc's decisions section — not a separate rebuttal file.
5. Whenever applying this round's findings advances the plan's own
   revision counter, regenerate `registry_path`/`mapping_path` at the new
   `plan_revision` (`workflow_state.generate_registry(...)`/
   `generate_mapping(...)`/`write_registry_and_mapping(...)`, unchanged
   checkpoints/requirements unless this round's accepted findings changed
   them), **and re-embed `workflow_state.render_registry_markdown(registry)`'s
   output into the plan document, replacing the table already there**
   (workflow system audit, convergence pass 12, ledger row `I22`).
   `/milestone-plan` step 3 calls that table the plan document's own
   "generated, human-readable checkpoint table — never hand-edited"; this
   step regenerated the registry behind it and never said to refresh it,
   so a revision that added, removed or renamed a checkpoint published a
   plan bundle whose table silently disagreed with the authoritative
   registry. Nothing detects that: the table is not hashed separately, and
   `assert_stage_completeness` checks only the document's `(Revision N)`
   marker, so the external reviewer reviews the stale table as if it were
   the plan. Re-embed **unconditionally** whenever the registry is
   regenerated: the render is a pure function of the checkpoint set, so it
   is a byte-identical no-op when this round changed no checkpoint, and
   making the step conditional only reintroduces the judgement call that
   produced the stale table. Then call `workflow_state.publish_plan_revision(state,
   work_item_id, plan_revision, now)` and persist the returned state to
   `docs/ai-workflow/WORKFLOW_STATE.json` — in the same operation, before
   the bundle below is regenerated, and on both this step's governing-version
   branches alike (`D-Plan-Revision-Publication`, `WFR-65`). This step
   applies only to a work item with an existing `WORKFLOW_STATE.json`
   entry (`registry_path` non-null); an ordinary `"1"`-governed milestone
   with no such entry is unaffected, unchanged. If this revision creates
   any of the four plan-stage files for the first time, apply
   `/milestone-plan` step 3's staging step to it as well (`git add -N`,
   salvage audit `B6`) -- an untracked declared path makes
   `resolve_plan_stage_metadata` refuse before the regeneration below
   writes anything. Then refresh **both**
   author-written files the generator's own closing checks bind to this
   round -- a stale entry does not warn, it *withdraws* the bundle
   (`finalize_bundle_generation` quarantines `current/` and deletes the
   archive):
   - `<bundle_dir>/REVIEW_REQUEST.md`, restating this round's
     `review_content_id: <hex>`
     (`assert_review_request_states_review_content_id`), obtained from the
     single canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
     "Computing `review_content_id`" names for this stage -- never a
     second, ad hoc computation, and never the previous round's value;
   - `<bundle_dir>/TEST_RESULTS.md`, restating **both** of the labelled
     lines `assert_test_results_consistent_with_plan_review_request`
     requires -- `stage: plan (revision N)` with `N` equal to the
     `plan_revision` just published, and `head: <sha>` equal to this
     generation's own HEAD. Both values change every round, so a copy
     carried forward from the previous round fails this check even when
     the revision alone would still match.

   Then rerun
   `./scripts/prepare-ai-review.sh <base-sha> plan <work_item_id>` to refresh the
   bundle (`work_item_id` is **required** for the plan stage, never
   resolved from the live `active_work_item_id` -- `D-Fingerprint-Generalization`).
6. If the `Status` was `BLOCK`, or if you made major structural changes to
   the plan, stay in `AWAITING_EXTERNAL_PLAN_REVIEW` and stop for another
   review round.
7. **`governing_workflow_version: "1"`**: report the plan as ready and that
   `AWAITING_PLAN_APPROVAL` is the next state, and stop — do not auto-run
   `/approve-review` or `/milestone-implement`. Only the user invokes
   `/approve-review plan`; let the user decide when to proceed.
7'. **`governing_workflow_version: "2.1"`/`"2.2"`, revised exit step**
    (resolves `GPT-R11-003`/`-007`; widened workflow-2.5.0 from a bare
    `"2.1"` check to cover `"2.2"` too -- the identical step, since the
    plan-review protocol does not distinguish between them): this command
    never self-declares plan readiness,
    regardless of how large or small a "structural change" judgment would
    call the edit:
    1. the recomputed `review_content_id` already differs from whatever
       `plan_review_stages` last recorded, so both stages already read as
       absent (`plan_approval_gate_reachable`'s recomputation rule — no
       explicit ledger clear performed or needed);
    2. the bundle is regenerated (`./scripts/prepare-ai-review.sh <base-sha>
       plan <work_item_id>`, same as step 5, unchanged mechanism);
    3. call `workflow_state.transition_to_awaiting_local_plan_review(state,
       work_item_id, now)` and persist the returned state to
       `docs/ai-workflow/WORKFLOW_STATE.json`;
    4. report the work item's phase as `AWAITING_LOCAL_PLAN_REVIEW` and
       stop — the same "stop, do not auto-continue" pattern step 7 uses for
       `"1"`. This is the sole path back to `AWAITING_LOCAL_PLAN_REVIEW`,
       whether the edit was driven by a local-model or a manual-external
       `REVISE` — no path re-enters manual-external review without a fresh
       local pass first.

Do not implement product code in this command.
