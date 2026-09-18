---
description: Ingest an already-pasted manual external reviewer's verdict as the MANUAL_EXTERNAL_PLAN_REVIEW stage of the two-stage plan-review protocol ("2.1"/"2.2" work items).
argument-hint: "[work-item-id]"
state_writer: true
review-subject: verdict
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the exit of `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`
(`docs/ai-workflow/MILESTONE_WORKFLOW.md`,
`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s `D-Plan-Review-Stages`). A small,
mechanical, model-independent command: it never evaluates plan content
itself (the external reviewer already did that) and never edits the plan
(`/apply-plan-review` does, unchanged, exactly as it reads and applies
today's single-stage external feedback).

**Not a user-authority gate**: no `disable-model-invocation` guard and no
`user_confirmation` requirement — it grants no approval itself, it only
records a verdict the user already obtained externally and already pasted
into `REVIEW_FEEDBACK.md` in the current turn. `/approve-review plan`
remains the sole, separate user-authority gate, entirely unchanged by this
command's existence: its own `plan_review_stages` check re-verifies the
same ledger invariant this command writes, as a restated invariant, not a
second ingestion path.

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
   `governing_workflow_version` is not a member of
   `workflow_state.TWO_STAGE_PLAN_REVIEW_VERSIONS` (`"2.1"`/`"2.2"`,
   widened workflow-2.5.0 from a bare `"2.1"` check), refuse cleanly,
   naming the actual version (`WrongGoverningVersionForPlanReviewStageError`).
3. **Phase guard**: if the item's `phase` is not
   `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, refuse cleanly, naming the
   actual phase — including "already ingested this round" and "no local
   `APPROVE` on record" (`WrongPhaseForPlanReviewStageError`).
4. **Read**: `<feedback_dir>/REVIEW_FEEDBACK.md` (must declare
   `Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW` -- the legacy
   `manual_external_plan_review` is also accepted), `<bundle_dir>/MANIFEST.md`,
   `<bundle_dir>/REVIEW_REQUEST.md`, and the ledger's existing
   `LOCAL_MODEL_PLAN_REVIEW` entry.
5. **Recompute fresh**: the current `bundle_id` and plan-stage
   `review_content_id`, identical in mechanism to `/review-plan`'s own
   (staleness/wrong-worktree handling included) — which means the same
   single canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
   "Computing `review_content_id`" names for this stage, never a second,
   ad hoc computation.
   **`REJECTED`-bundle refusal, first of two** (`WFR-67`): also call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here; a `BundleRejectedError` stops the command, naming
   the marker path and its recorded detail.
6. **Validate before writing anything**
   (`workflow_state.validate_manual_plan_review_preconditions`), in order:
   - the feedback's declared role is either the canonical
     `MANUAL_EXTERNAL_PLAN_REVIEW` or the legacy `manual_external_plan_review`
     (`WrongReviewerRoleError` otherwise, naming what was declared instead);
   - the feedback's `review_content_id` matches the current recomputed
     value — **hard**, blocks ingestion (`StaleReviewContentIdError`,
     naming both values);
   - a current `LOCAL_MODEL_PLAN_REVIEW` `APPROVE` is recorded for the
     same `review_content_id` (`MissingLocalApprovalForManualStageError` —
     a restated invariant, since entry to this phase already required it;
     defends against a corrupted or hand-edited state file);
   - no `MANUAL_EXTERNAL_PLAN_REVIEW` stage is already recorded against
     the current `review_content_id` (`DuplicateManualStageIngestionError`
     — a second invocation after a completed `APPROVE`/`REVISE` normally
     fails the phase guard first; this only fires for a hand-edited or
     race-condition state).
   Separately, check the feedback's `bundle_id` against the current
   recomputed one via `workflow_state.check_manual_stage_bundle_id_advisory`:
   a mismatch is **advisory only** — report the warning naming both
   values, never block on it (a wrapper-only bundle regeneration between
   upload and paste, new `bundle_id`/unchanged `review_content_id`, must
   not invalidate the manual stage).
7. **Write set, exact.** **`REJECTED`-bundle refusal, second of two,
   under this step's own mutation guard** (`WFR-67`): immediately before
   the first write below, re-call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` — a withdrawal landing between step 5/6 and here must
   still be caught.
   - `APPROVE`: via `workflow_state.record_manual_plan_review(...,
     verdict="APPROVE", bundle_id=<the feedback's own bundle_id,
     verbatim>, ...)` — the resolved work item's `MANUAL_EXTERNAL_PLAN_REVIEW`
     ledger fields (recording the feedback's actual `bundle_id` regardless
     of whether it matched the current recomputed one, so the ledger
     records what the reviewer actually saw) and its phase transition to
     `AWAITING_PLAN_APPROVAL`.
   - `REVISE`: only the phase transition to `REVISING_PLAN`
     (`record_manual_plan_review(..., verdict="REVISE", ...)` — no ledger
     write).
   - `BLOCK`: nothing (`record_manual_plan_review(..., verdict="BLOCK",
     ...)` is a true no-op; the work item stays at
     `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`).
   Never `REVIEW_FEEDBACK.md` (already user-authored), the plan, registry,
   mapping, command, product, or bundle-content files.
8. **Report and stop.** For an `APPROVE`: state that `AWAITING_PLAN_APPROVAL`
   is next and that only the user can invoke `/approve-review plan`. For a
   `REVISE`: state that `/apply-plan-review` is next. For a `BLOCK`: state
   that explicit user resolution is required before any further command
   runs. **Never** auto-continue to `/apply-plan-review` or
   `/approve-review` in this same invocation.
