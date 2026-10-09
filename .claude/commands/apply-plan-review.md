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
argument: `feedback/` is stage-agnostic and resolves by the item's durable
`feedback_layout` for every stage alike (`D-Feedback-Layout`,
workflow-2.6.0): `.ai-review/<work_item_id>/feedback/` unconditionally, by
construction, for a `feedback_layout: "scoped"` item (every item created
under workflow-2.6.0); the unchanged legacy scoped-else-flat rule for an
item without the field. `REVIEW_PROTOCOL.md`'s "Bundle location" is the
normative definition.

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
   - **`governing_workflow_version: "2.1"`**: steps 1-5 execute identically,
     plus the two-stage additions marked **[2.1]** in this step and in
     steps 1 and 5; step 6 does **not** apply (it is `"1"`-only since
     workflow-2.6.0, `D-Plan-Review-Bundle-Binding`, `LPR-R5-001`: a
     two-stage `BLOCK` is resolved at its ready phase and never reaches
     this command, and every successful round ends bound, so step 7'
     always runs after step 5);
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
   - **[2.1] Plan-review entry** (`TWO_STAGE_PLAN_REVIEW_VERSIONS` items
     only; `D-Plan-Review-Bundle-Binding`, workflow-2.6.0), in order, before
     step 1 and before any other write:
     1. **Row-1 refusal** (`LPR-R4-002`): call
        `workflow_state.assert_plan_review_entry_phase(work_item,
        work_item_id, command="/apply-plan-review")`. A phase outside both
        the ready set (`AWAITING_LOCAL_PLAN_REVIEW`,
        `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL`)
        and the non-ready set (`PLANNING`, `REVISING_PLAN`,
        `AMENDING_PLAN`) refuses with `PlanReviewPhaseNotPlanStageError`,
        writing nothing; its message names `/request-plan-amendment <id>`
        only at `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION`.
     2. **Marker**: call `workflow_state.state_transaction(repo_root,
        lambda state: workflow_state.ensure_plan_review_binding_marker(state,
        work_item_id, now))` -- a `2.5.1` item in `REVISING_PLAN`/
        `AMENDING_PLAN` with no `plan_review_binding` record (row 5,
        `LEGACY_UNMARKED`) gets the fail-closed legacy marker, or, for an
        open amendment with a recorded `approved_review_content_id`, the
        non-legacy `CONSUMED` record from it; otherwise a no-op.
     3. **The status, evaluated once**: call
        `workflow_state.plan_review_publication_status(repo_root, state,
        work_item_id)` (the same table `python3 scripts/workflow_state.py
        --plan-review-publication-status <id>` prints) and route on it:
        - **a ready row** (2, 3, 4a-4c): this command applies nothing.
          `BOUND` (rows 2/3): report the phase and the next review command
          and stop. `CONTENT_DRIFTED` (4a), `BUNDLE_UNVERIFIED` (4b) or
          `LEGACY_UNVERIFIED` (4c): report the status and its `remedy` --
          restore the bound bytes from `current/files/<path>`, regenerate,
          or withdraw with `/milestone-plan <id>` -- and stop;
        - `NEEDS_EDIT` (rows 7, 10) and `EDIT_IN_PROGRESS` (row 11): the
          normal path, steps 1-5 and 7';
        - `NEEDS_REVISION` (row 8): the edits were complete when the
          registry was regenerated; run step 1, then resume at step 5 at
          the registry's revision (its regeneration and re-embed are
          byte-identical no-ops if already done), then publish;
        - `PUBLISHED_UNBOUND` (row 9): run step 1, then resume at step 5's
          generation -- skipped when the status reports a bundle that
          already verifies for the published content -- and then the bind
          in step 7'; never re-apply findings, never re-advance the revision;
        - `PlanReviewBindingInconsistentError` (rows 4d and 6): report it
          and stop; row 4d's remedy is `/milestone-plan <id>`'s withdrawal.
1. Read `<feedback_dir>/REVIEW_FEEDBACK.md`. If it does not exist, stop
   and say so — do not proceed on assumed feedback — printing the exact
   resolved path (`resolve_feedback_dir(repo_root, work_item_id)`,
   `D-Feedback-Layout`, workflow-2.6.0) where the reviewer's feedback must
   be placed, never a hard-coded flat path. Validate its binding
   fields with `workflow_state.assert_apply_review_feedback_binding(repo_root,
   work_item, work_item_id, stage="plan", feedback_content=<the file's
   content>)` (`D-Apply-Binding`, workflow-2.7.0; `WFR-03`), for a `"1"`
   item and, for a two-stage item, when the acceptance rule below returns
   `"bundle"`. It applies 2.6.0's bundle binding (`assert_feedback_matches_bundle`
   against the current recomputed `bundle_id`/`base_commit`/`work_item_id`)
   to every `"1"` item, every legacy marker, every verdict that states no
   `review_content_id`, and every bundle whose `MANIFEST.md` names no
   `work_item_id`. A two-stage `REVISE` that states a `review_content_id`
   is bound by content instead: it must be the consumed content and the
   one the bundle's own `MANIFEST.md` records, the manifest must name this
   item and the plan stage, and its `Reviewed bundle ID:`/`Reviewed base
   commit:` are advisory -- report the returned `advisory`, if any. Report
   a refusal by class and stop: `FeedbackContentMismatchError` (the verdict
   is not for the round being applied), `ReviewBundleManifestMismatchError`
   or `MissingRequiredBundleFileError` (the bundle on disk is not this
   item's reviewed bundle: run from the worktree that holds it, or restore
   it -- never regenerate it, since the verdict binds to it),
   `MissingFeedbackBindingFieldError`/`FeedbackBundleMismatchError` (stale
   or mismatched feedback) -- a reason to stop and say so, not to apply.
   Never edit a verdict's binding fields to make it bind: they attest to
   what its reviewer reviewed.
   **`REJECTED`-bundle refusal, first of two** (`WFR-67`): also call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here; a `BundleRejectedError` stops the command, naming
   the marker path and its recorded detail.
   **[2.1] Acceptance rule, before any write** (`D-Plan-Review-Bundle-Binding`,
   workflow-2.6.0, `LPR-R2-007`/`LPR-R5-001`): first call
   `workflow_state.assert_apply_plan_review_feedback(work_item,
   work_item_id, feedback_content=<the file's content>,
   publication_status=<step 0's status>)`. Only `Status: REVISE` is ever
   applied to a two-stage item: `BLOCK` or `APPROVE` refuses with
   `FeedbackStatusNotApplicableError`, and `WORKFLOW_STATE.json` is left
   byte-identical -- a plan-stage `BLOCK` writes no transition and is
   resolved at its ready phase, by re-reviewing the unchanged content or by
   editing and withdrawing with `/milestone-plan <id>`, which the message
   names. It returns `"durable"` under `PUBLISHED_UNBOUND`/`EDIT_IN_PROGRESS`
   (rows 9 and 11), where the on-disk bundle may already have been
   regenerated: the feedback was checked against the durable `CONSUMED`
   record instead (its `review_content_id` equals the consumed one; for a
   legacy marker, its `Work item:` names this item), a mismatch refusing
   with `FeedbackNotForConsumedContentError`, and the on-disk bundle
   binding above is not applied. It returns `"bundle"` otherwise, and the
   binding above against the on-disk bundle -- still the reviewed one --
   applies.
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
   produced the stale table. If this revision creates
   any of the four plan-stage files for the first time, apply
   `/milestone-plan` step 3's staging step to it as well (`git
   --literal-pathspecs add -N`,
   salvage audit `B6`) -- **before** the publish below (workflow-2.6.0,
   `LPR-R3-003`): an untracked declared path makes
   `resolve_plan_stage_metadata` refuse, and both the two-stage publish's
   own fresh-id computation and the regeneration below read through it.
   Then publish, in the same operation and before the bundle below is
   regenerated (`D-Plan-Revision-Publication`, `WFR-65`), by calling
   `workflow_state.state_transaction(repo_root, lambda state:
   workflow_state.publish_plan_revision(state, work_item_id, plan_revision,
   now, ...))`:
   - **`"1"`**: whenever the revision counter advances, exactly as before
     (`review_content_id` is not passed);
   - **[2.1]** on **every** round, whether or not it advanced the revision
     -- the "advances the revision counter" condition above now governs
     only the registry regeneration (`D-Plan-Review-Bundle-Binding`,
     workflow-2.6.0, section 5.3 item 2) -- with `review_content_id=F`,
     the fresh plan-stage id computed inside that same mutator, after the
     regeneration, the re-embed and the staging step, through the single
     canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
     "Computing `review_content_id`" names for this stage. This call is
     the author's "edits declared complete" act: it is **mirror-only**,
     writes the `PUBLISHED` record, and leaves the phase at
     `REVISING_PLAN`/`AMENDING_PLAN` (the bind in step 7' writes
     `AWAITING_LOCAL_PLAN_REVIEW`). No later step of this command edits a
     plan-stage protected path. It refuses before any write with
     `ConsumedPlanReviewContentError` when the content is still the
     reviewed (consumed) content -- apply the accepted edits first. For an
     item carrying the fail-closed legacy marker (`consumed.legacy: true`,
     a `2.5.1` item mid-round at update, written by step 0's
     `ensure_plan_review_binding_marker`) the same error also fires when
     `plan_revision` does not exceed the marker's: that marker records no
     `review_content_id`, so this round **must advance the revision**
     (an edit plus the registry regeneration above) even if it would
     otherwise not bump -- a non-bumping round against a legacy marker is
     refused, safely and naming that remedy. It also refuses
     with `LegacyPlanReviewBindingUnknownError`,
     `PlanReviewInProgressError`, `PlanReviewPhaseNotPlanStageError` or
     `PlanReviewBindingInconsistentError` as step 0's status would predict.

   This step applies only to a work item with an existing `WORKFLOW_STATE.json`
   entry (`registry_path` non-null); an ordinary `"1"`-governed milestone
   with no such entry is unaffected, unchanged. Then refresh **both**
   author-written files the generator's own closing checks bind to this
   round, in `<plan_inputs_dir>` =
   `workflow_fingerprint.resolve_plan_review_inputs_dir(repo_root,
   work_item_id)` (`.ai-review/<work_item_id>/plan-inputs/`, never
   `<bundle_dir>`: the plan-stage generator copies them byte-for-byte into
   its staging directory, so nothing written here touches `current/`,
   workflow-2.6.0, `REVIEW_PROTOCOL.md`'s "Bundle location") -- a stale
   entry does not warn, it fails the generation, which at the plan stage
   discards only its own staging directory and never withdraws the
   previous `current/` (no `REJECTED` marker, the deliberately revised
   plan-stage `WFR-67` semantics):
   - `<plan_inputs_dir>/REVIEW_REQUEST.md`, restating this round's
     `review_content_id: <hex>`
     (`assert_review_request_states_review_content_id`), obtained from the
     single canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
     "Computing `review_content_id`" names for this stage -- never a
     second, ad hoc computation, and never the previous round's value. If the plan stage's gate is `automatic` and the effective `require` lists
     `distinct_reviewer_models` (`workflow_state.review_stage_gate_context(repo_root,
     state, work_item_id, "plan")["requires_distinct"]`), also ask the reviewer, in
     that file, to state `Reviewer model: <vendor>/<model>` in the verdict's header
     block, because an `APPROVE` without it is refused at ingest (workflow-2.8.0,
     `LPR-R16-003`); under a human gate ask for nothing new.
   - `<plan_inputs_dir>/TEST_RESULTS.md`, restating **both** of the labelled
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
   **[2.1]** This is the round's **single** generation (workflow-2.6.0,
   `MPR-R1-O1`): step 7' binds this bundle and never regenerates it. **If
   the generator fails** (`LPR-R5-001`), the item is left at its non-ready
   phase with a `PUBLISHED` record -- row 9, `PUBLISHED_UNBOUND`,
   recoverable. Report the failure and name the explicit-id re-run,
   `/apply-plan-review <work_item_id>`, whose entry resumes at row 9
   (regenerate, then bind, never re-advancing the revision) -- never a
   review command, since `/review-plan` refuses at a non-ready phase. Stop.

   **Step 6 below is `"1"`-only** (workflow-2.6.0, `LPR-R5-001`). For a
   `TWO_STAGE_PLAN_REVIEW_VERSIONS` item it does not apply, whatever the
   verdict or the size of the change: step 1 has already refused a
   `BLOCK`, and step 7' always runs after this step, so every successful
   round ends bound at `AWAITING_LOCAL_PLAN_REVIEW` -- no exit remains
   between the publish above and that bind except the reporting
   generator-failure exit just stated.
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
    2. **bind the bundle step 5 generated** (`D-Plan-Review-Bundle-Binding`,
       workflow-2.6.0, section 5.3 item 3; `MPR-R1-O1`: `2.5.1`'s second
       generation here is removed, so step 5 is the round's single
       generation and nothing between the publish and this bind can exit
       unreported): call `binding =
       workflow_state.verify_plan_review_bundle(repo_root, work_item_id)`,
       then `workflow_state.state_transaction(repo_root, lambda state:
       workflow_state.bind_plan_review_bundle(state, work_item_id,
       binding=binding, now=now))` -- the **sole** writer of
       `AWAITING_LOCAL_PLAN_REVIEW`, writing that phase, `current_bundle_id`
       and the `BOUND` record only for a verified bundle of the published
       content. (`transition_to_awaiting_local_plan_review`, `2.5.1`'s bare
       phase flip, is retired and always raises
       `PlanReviewWriterRetiredError`.) The verifier refuses by cause with
       `PlanReviewBundleUnverifiedError` or `ReviewedContentDriftError`; the
       bind with `PlanReviewNotPublishedError`,
       `ConsumedPlanReviewContentError`,
       `LegacyPlanReviewBindingUnknownError`, `PlanReviewAlreadyReadyError`,
       `PlanReviewPhaseNotPlanStageError` or
       `PlanReviewBindingInconsistentError`. On any refusal -- a crash or an
       operator edit between step 5 and here -- the item stays at row 9
       (`PUBLISHED_UNBOUND`), or row 11 after an edit; report the refusal
       and name the explicit-id re-run, `/apply-plan-review
       <work_item_id>`, and stop;
    3. report the work item's phase as `AWAITING_LOCAL_PLAN_REVIEW`, with
       the bound `bundle_id` and `review_content_id`, and
       stop — the same "stop, do not auto-continue" pattern step 7 uses for
       `"1"`. This is the sole path back to `AWAITING_LOCAL_PLAN_REVIEW`,
       whether the edit was driven by a local-model or a manual-external
       `REVISE` — no path re-enters manual-external review without a fresh
       local pass first.

Do not implement product code in this command.
