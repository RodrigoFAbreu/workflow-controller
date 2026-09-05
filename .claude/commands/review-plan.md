---
description: Independently review the current plan bundle as the LOCAL_MODEL_PLAN_REVIEW stage of the two-stage plan-review protocol ("2.1" work items only).
argument-hint: "[work-item-id]"
state_writer: true
review-subject: bundle
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `AWAITING_LOCAL_PLAN_REVIEW` state of
`docs/ai-workflow/MILESTONE_WORKFLOW.md` (`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s
`D-Plan-Review-Stages`). Implements the `LOCAL_MODEL_PLAN_REVIEW` **role**,
not a specific model: nothing in this contract, the written feedback
schema, or the state transition it produces names a model. Running it from
any capable Claude model produces the same schema and the same transition.
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
argument: `feedback/` is stage-agnostic and keeps the scoped-else-flat
rule for every stage alike.

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`).
2. **Governing-version guard**: if the resolved item's
   `governing_workflow_version` is not `"2.1"`, refuse cleanly, naming the
   actual version — `"1"` items have no local-review stage to run; use the
   existing single-stage `AWAITING_EXTERNAL_PLAN_REVIEW` flow instead
   (`workflow_state.validate_local_plan_review_preconditions` raises
   `WrongGoverningVersionForPlanReviewStageError`).
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
6. **Independently verify** every finding the plan document claims as
   addressed against the actual repository state — never take the
   disposition table's word for it — and search for new findings, exactly
   as thoroughly as `/apply-plan-review`'s own validation requirement.
7. **Decide the verdict** (`Status: APPROVE | REVISE | BLOCK`) and write
   `<feedback_dir>/REVIEW_FEEDBACK.md` per
   `docs/ai-workflow/REVIEW_PROTOCOL.md`'s required structure, **plus**
   these provenance fields this role always includes:
   - `Reviewer role: LOCAL_MODEL_PLAN_REVIEW` (never a model name here);
   - the three binding fields `docs/ai-workflow/REVIEW_PROTOCOL.md` now
     requires on every round (`Reviewed bundle ID:`, `Reviewed base
     commit:`, `Work item:`), stated with the recomputed `bundle_id`,
     `base_commit`, and `work_item_id` from step 5 (`WFR-03`), plus the
     recomputed plan-stage `review_content_id` as its own labelled line;
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
   still be caught.
   - `APPROVE`: `REVIEW_FEEDBACK.md`, plus — via
     `workflow_state.record_local_plan_review(..., verdict="APPROVE", ...)`
     — the resolved work item's `LOCAL_MODEL_PLAN_REVIEW` ledger fields and
     its phase transition to `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` in
     `WORKFLOW_STATE.json`.
   - `REVISE`: `REVIEW_FEEDBACK.md`, plus the phase transition to
     `REVISING_PLAN` (`record_local_plan_review(..., verdict="REVISE", ...)`
     — no ledger entry).
   - `BLOCK`: `REVIEW_FEEDBACK.md` only — `record_local_plan_review(...,
     verdict="BLOCK", ...)` is a true no-op; the work item stays at
     `AWAITING_LOCAL_PLAN_REVIEW`.
   Never the plan, registry, mapping, command, product, or bundle-content
   files, and never another work item's fields.
9. **Report and stop.** For an `APPROVE`: state the exact bundle path,
   `bundle_id`, and `review_content_id` the user must hand to the manual
   external reviewer (recommended: ChatGPT) — the same values just
   recorded in the ledger, so the user is never guessing which artifact to
   upload — and that `/record-manual-plan-review` is the next command,
   after the user pastes that reviewer's feedback into
   `REVIEW_FEEDBACK.md`. For a `REVISE`: state that `/apply-plan-review` is
   next. For a `BLOCK`: state that explicit user resolution is required
   before any further command runs. **Never** auto-continue to
   `/apply-plan-review` or to the manual-external stage in this same
   invocation.
