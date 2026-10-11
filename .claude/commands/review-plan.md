---
description: Independently review the current plan bundle as the LOCAL_MODEL_PLAN_REVIEW stage of the two-stage plan-review protocol ("2.1"/"2.2" work items).
argument-hint: "[work-item-id]"
state_writer: true
review-subject: bundle
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `AWAITING_LOCAL_PLAN_REVIEW` state of
`docs/ai-workflow/MILESTONE_WORKFLOW.md` (`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s
`D-Plan-Review-Stages`). Implements the `LOCAL_MODEL_PLAN_REVIEW` **role**,
not a specific model: nothing in this contract, the written feedback
schema, or the state transition it produces names a model, except the
`Reviewer model:` line when the gate policy requires distinct reviewer models
(step 7, workflow-2.8.0). Running it from any capable Claude model produces the
same schema and the same transition.
Recommended in a **fresh session** for genuine independence from the
session that wrote the plan — strongly recommended operational guidance,
not a verified precondition; no check here depends on session freshness.

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

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`).
2. **Governing-version guard**: if the resolved item's
   `governing_workflow_version` is not a member of
   `workflow_state.TWO_STAGE_PLAN_REVIEW_VERSIONS` (`"2.1"`/`"2.2"`,
   widened workflow-2.5.0 from a bare `"2.1"` check), refuse cleanly,
   naming the actual version — `"1"` items have no local-review stage to run;
   use the existing single-stage `AWAITING_EXTERNAL_PLAN_REVIEW` flow instead
   (`workflow_state.validate_local_plan_review_preconditions` raises
   `WrongGoverningVersionForPlanReviewStageError`). A `"2.2"` item
   runs this identical stage: the two-stage plan-review protocol does not
   distinguish `"2.1"`/`"2.2"`.
3. **Phase guard**: if the item's `phase` is not
   `AWAITING_LOCAL_PLAN_REVIEW`, refuse cleanly, naming the actual phase —
   including "already completed this round" (`WrongPhaseForPlanReviewStageError`).
   Never silently re-run.
4. **Read**: the authoritative plan doc (`plan_path`), `<bundle_dir>/REVIEW_REQUEST.md`,
   `<bundle_dir>/MANIFEST.md`, the required-context file list, any
   prior `<feedback_dir>/REVIEW_FEEDBACK.md` (for continuity across
   rounds), and `docs/ai-workflow/REVIEW_PROTOCOL.md`'s feedback-structure
   contract.
5. **Recompute fresh, before writing anything**: the current `bundle_id`
   and the plan-stage `review_content_id`, the latter through the single
   canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
   "Computing `review_content_id`" names for this stage — never a second,
   ad hoc computation, and never the value `MANIFEST.md` already states
   (that is the value being checked, not the value to check it with).
   Refuse, naming both a recomputed
   and a stale value, if the bundle directory does not match what
   `MANIFEST.md`/`REVIEW_REQUEST.md` claim (the same staleness discipline
   `/approve-review` applies, run one stage earlier) — a missing/unreadable
   bundle, or a work item resolved from the wrong worktree
   (`WORKTREE_IDENTITY.json`'s existing local-staleness check), are the
   same class of refusal. This command also runs inside a real, current
   worktree, so also call
   `workflow_fingerprint.assert_local_generation_matches(repo_root,
   <bundle_dir>/MANIFEST.md)` and stop, naming both, on a
   `WorktreeOrHeadMismatchError` (`D-Bundle-Manifest`, `WFR-17`).
   **`REJECTED`-bundle refusal, first of two** (`WFR-67`): also call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here; a `BundleRejectedError` stops the command, naming
   the marker path and its recorded detail.
   **Bundle-bound check** (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0,
   section 5.3 item 4): call
   `workflow_state.validate_local_plan_review_preconditions_bound(repo_root,
   work_item)` -- steps 2-3's version and phase guards, then
   `assert_plan_review_bundle_bound`, which re-runs the bundle verifier and
   requires a `BOUND` `plan_review_binding` record for exactly the
   bundle's `review_content_id` (a `2.5.1` item at this phase with no
   record is accepted when its bundle verifies; nothing is written). Report
   its returned advisory, if any -- a `bundle_id` differing from
   `current_bundle_id` (a wrapper-only regeneration after the bind) never
   blocks. On a refusal, stop and report the error's message, which names
   the remedy: `ReviewedContentDriftError` (row 4a: the worktree drifted
   from the bound content -- restore the bound bytes from
   `<bundle_dir>/files/<path>`, or withdraw with `/milestone-plan <id>`),
   `PlanReviewBundleUnverifiedError` (rows 4b/4c: regenerate, or
   withdraw), `PlanReviewBindingInconsistentError` (row 4d: withdraw with
   `/milestone-plan <id>`).
6. **Independently verify** every finding the plan document claims as
   addressed against the actual repository state — never take the
   disposition table's word for it — and search for new findings, exactly
   as thoroughly as `/apply-plan-review`'s own validation requirement.
7. **Decide the verdict** (`Status: APPROVE | REVISE | BLOCK`) and write
   `<feedback_dir>/REVIEW_FEEDBACK.md` per
   `docs/ai-workflow/REVIEW_PROTOCOL.md`'s required structure, **plus**
   these provenance fields this role always includes:
   - `Reviewer role: LOCAL_MODEL_PLAN_REVIEW` (never a model name here);
   - **only when** `workflow_state.review_stage_gate_context(repo_root,
     state, work_item_id, "plan")["requires_distinct"]` is true (the plan gate
     is `automatic` and the effective `require` lists
     `distinct_reviewer_models`; with the default policy it is): the line
     `Reviewer model: <vendor>/<model>`, naming the vendor and model of this
     reviewing session itself (for example `anthropic/claude-opus-5-5`), in the
     header block before the first `## ` section. The vendor prefix is what
     makes the "family" a vendor; a bare model id still parses. Write no such
     line otherwise -- under a human gate the file is exactly 2.7.0's;
   - the three binding fields `docs/ai-workflow/REVIEW_PROTOCOL.md` now
     requires on every round (`Reviewed bundle ID:`, `Reviewed base
     commit:`, `Work item:`), stated with the recomputed `bundle_id`,
     `base_commit`, and `work_item_id` from step 5 (`WFR-03`), plus the
     recomputed plan-stage `review_content_id` as its own labelled line,
     with exactly the pinned label `Reviewed review_content_id: <hex>`
     (workflow-2.7.0, `v2.6.0-002`), all before the first `## ` section;
   - the round/sequence number (one more than the highest prior
     `LOCAL_MODEL_PLAN_REVIEW` round on record, counting a round entry under
     either casing of the stage key, or `1` if none);
   - a completion timestamp.
   Malformed prior feedback is reported and stops rather than guessed at.
8. **Write set, exact.** **`REJECTED`-bundle refusal, second of two,
   under this step's own mutation guard** (`WFR-67`): immediately before
   the first write below, re-call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` — a withdrawal landing between step 5 and here must
   still be caught. **Ownership guard, immediately before the write**
   (workflow-2.5.0, mirroring `/review-implementation`'s own advisory-branch
   step 7): resolve `<feedback_dir>` via the existing, unmodified
   `resolve_feedback_dir(repo_root, work_item_id)`, read whatever
   `REVIEW_FEEDBACK.md` already sits there (`None` if nothing does), and call
   `workflow_fingerprint.assert_feedback_not_owned_by_other_work_item(
   existing_content, work_item_id=work_item_id, state=<the parsed
   docs/ai-workflow/WORKFLOW_STATE.json>)` against it — a genuine
   cross-work-item collision at a legacy item's scoped-else-flat path (live
   whenever no scoped `.ai-review/<work_item_id>/feedback/` directory exists
   yet for a work item without `feedback_layout`; a `feedback_layout:
   "scoped"` item's directory is private by construction) is refused rather
   than silently overwritten. **Bounded terminal-owner relaxation**
   (`D-Feedback-Layout`, workflow-2.6.0): for a legacy writer only, a
   foreign owner whose own `state` entry sits at the terminal phase
   `MILESTONE_COMPLETE` does not block — terminal state proves no consumer
   of that file remains, and this write replaces it whole with this item's
   own binding fields; a non-terminal owner, an owner absent from `state`,
   or any foreign file in a scoped writer's directory still refuses. On a
   `FeedbackOwnedByOtherWorkItemError` here, stop naming both work item ids;
   see `/review-implementation`'s own step 7 "Recovery from an ownership
   refusal" for the disposition (wait for the blocking work item to reach a
   terminal phase, or judge a dormant/untracked blocker by hand). Once the
   guard passes, call `workflow_fingerprint.ensure_feedback_dir(repo_root,
   work_item_id)` (`D-Feedback-Layout`) so the resolved `<feedback_dir>`
   exists before the write — a scoped item's directory is created by no
   earlier step.
   - `APPROVE`: `REVIEW_FEEDBACK.md`, plus — via
     `workflow_state.record_local_plan_review(..., verdict="APPROVE", ...)`
     — the resolved work item's `LOCAL_MODEL_PLAN_REVIEW` ledger fields and
     its phase transition to `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` in
     `WORKFLOW_STATE.json`. **Audit keys** (workflow-2.8.0, `D-GP-Trust`):
     the mutator passes `audit=workflow_state.local_review_audit(repo_root,
     state, work_item_id, "plan", feedback_text=<the REVIEW_FEEDBACK.md text
     just written>, feedback_path=<the resolved REVIEW_FEEDBACK.md path>)` --
     `None` while the plan gate is human (the entry is then 2.7.0's bytes),
     otherwise `verdict_sha256`, `run_ref` (`session:local <path>`) and, when
     the line above was written, `reviewer_model`.
   - `REVISE`: `REVIEW_FEEDBACK.md`, plus the phase transition to
     `REVISING_PLAN` (`record_local_plan_review(..., verdict="REVISE", ...)`
     — no ledger entry) and, in the same write, the `CONSUMED`
     `plan_review_binding` record for this `review_content_id`
     (workflow-2.6.0, `D-Plan-Review-Bundle-Binding`), so the reviewed
     content can never re-bind without an edit. The id is also added to
     the item's durable `consumed_plan_review_content_ids` history
     (workflow-2.7.0), so a later round never releases it.
   - `BLOCK`: `REVIEW_FEEDBACK.md` only — `record_local_plan_review(...,
     verdict="BLOCK", ...)` is a true no-op; the work item stays at
     `AWAITING_LOCAL_PLAN_REVIEW`.
   Plan-stage writes are not committed here (workflow-2.9.1,
   `REVIEW_PROTOCOL.md`'s commit rule for review-stage writes): there is no
   generation-record commit at the plan stage, the plan-approval commit
   takes the whole working-tree state, and a commit of its own would raise
   `bundle_generation_mismatch` at the plan-approval gate.
   Never the plan, registry, mapping, command, product, or bundle-content
   files, and never another work item's fields.
9. **Report and stop.** For an `APPROVE`: state the exact bundle path,
   `bundle_id`, and `review_content_id` the user must hand to the manual
   external reviewer (recommended: ChatGPT) — the same values just
   recorded in the ledger, so the user is never guessing which artifact to
   upload — and that `/record-manual-plan-review` is the next command,
   after the user pastes that reviewer's feedback into
   `REVIEW_FEEDBACK.md` — print the exact resolved path,
   `<feedback_dir>/REVIEW_FEEDBACK.md` from `resolve_feedback_dir(repo_root,
   work_item_id)` (`D-Feedback-Layout`, workflow-2.6.0), never a hard-coded
   flat path. For a `REVISE`: state that `/apply-plan-review` is
   next. For a `BLOCK`: state that explicit user resolution is required
   before any further command runs -- then either re-review the unchanged
   content, or edit the plan and withdraw it with `/milestone-plan
   <work_item_id>`; `/apply-plan-review` never applies a plan-stage
   `BLOCK` (workflow-2.6.0). **Never** auto-continue to
   `/apply-plan-review` or to the manual-external stage in this same
   invocation.
