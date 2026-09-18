---
description: Ingest an already-pasted manual external reviewer's verdict as the MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW stage of the two-stage implementation-review protocol ("2.2" work items only).
argument-hint: "[work-item-id]"
state_writer: true
review-subject: verdict
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the exit of `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
(`docs/ai-workflow/MILESTONE_WORKFLOW.md`,
`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s `D-Implementation-Review-Stages`,
`workflow-2.5.0`). Mirrors `/record-manual-plan-review` exactly, substituted
for the implementation stage: a small, mechanical, model-independent
command. It never evaluates implementation content itself (the external
reviewer already did that) and never edits source/test/plan content
(`/apply-implementation-review` does, unchanged, exactly as it reads and
applies today's single-stage external feedback for a `"1"`/`"2.1"` item).

**Not a user-authority gate**: no `disable-model-invocation` guard and no
`user_confirmation` requirement — it grants no approval itself, it only
records a verdict the user already obtained externally and already pasted
into `REVIEW_FEEDBACK.md` in the current turn. `/approve-review
implementation` remains the sole, separate user-authority gate, entirely
unchanged by this command's existence: its own `implementation_review_stages`
check (for a `"2.2"` item) re-verifies the same ledger invariant this
command writes, as a restated invariant, not a second ingestion path.

`<bundle_dir>`/`<feedback_dir>` below resolve per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir(repo_root, work_item_id)`/
`resolve_feedback_dir(repo_root, work_item_id)`). This is the
**implementation** stage, so `resolve_bundle_dir` is called with **no**
`stage` argument -- the scoped-else-flat compatibility rule -- never the
plan stage's `stage="plan"` form.

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`). Refuse with a named error if
   neither resolves to an existing `work_items` entry -- never guess.
2. **Governing-version guard**: if the resolved item's
   `governing_workflow_version` is not exactly `"2.2"`, refuse cleanly,
   naming the actual version — unlike the plan-review protocol (valid for
   both `"2.1"`/`"2.2"`), the two-stage *implementation*-review protocol is
   `"2.2"`-only (`workflow_state.validate_manual_implementation_review_preconditions`
   raises `WrongGoverningVersionForImplementationReviewStageError`). A `"1"`
   item has no local-review stage to run at all; a `"2.1"` item's
   implementation stage stays single-stage, unchanged by this milestone —
   use the existing `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` flow for
   either instead.
3. **Phase guard**: if the item's `phase` is not
   `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, refuse cleanly, naming
   the actual phase — including "already ingested this round" and "no local
   `APPROVE` on record" (`WrongPhaseForImplementationReviewStageError`).
4. **Read**: `<feedback_dir>/REVIEW_FEEDBACK.md` (must declare `Reviewer
   role: MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` — there is no legacy-cased
   alias to accept here: unlike `plan_review_stages`, the
   `implementation_review_stages` ledger is introduced fresh at `"2.2"` with
   no pre-`SCREAMING_SNAKE_CASE` history behind it, so a role string that
   does not match this exact spelling is refused outright, never silently
   normalized), `<bundle_dir>/MANIFEST.md`, `<bundle_dir>/REVIEW_REQUEST.md`,
   and the ledger's existing `LOCAL_MODEL_IMPLEMENTATION_REVIEW` entry.
5. **Recompute fresh**: the current `bundle_id` and implementation-stage
   `review_content_id`, identical in mechanism to `/review-implementation`'s
   own (staleness/wrong-worktree handling included) — the same single
   canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Computing
   `review_content_id`" names for this stage, never a second, ad hoc
   computation.
   **`REJECTED`-bundle refusal, first of two** (`WFR-67`): also call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here; a `BundleRejectedError` stops the command, naming
   the marker path and its recorded detail.
6. **Validate before writing anything**
   (`workflow_state.validate_manual_implementation_review_preconditions`),
   in order:
   - the feedback's declared role is exactly the canonical
     `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` (`WrongReviewerRoleError`
     otherwise, naming what was declared instead — reuses the same,
     already stage-agnostic exception `/record-manual-plan-review` uses);
   - the feedback's `review_content_id` matches the current recomputed
     value — **hard**, blocks ingestion (`StaleReviewContentIdError`,
     naming both values);
   - a current `LOCAL_MODEL_IMPLEMENTATION_REVIEW` `APPROVE` is recorded for the
     same `review_content_id`
     (`MissingLocalApprovalForManualImplementationStageError` — a restated
     invariant, since entry to this phase already required it; defends
     against a corrupted or hand-edited state file);
   - no `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` stage is already recorded
     against the current `review_content_id`
     (`DuplicateManualImplementationStageIngestionError` — a second
     invocation after a completed `APPROVE`/`REVISE` normally fails the
     phase guard first; this only fires for a hand-edited or race-condition
     state).
   Separately, check the feedback's `bundle_id` against the current
   recomputed one via `workflow_state.check_manual_stage_bundle_id_advisory`
   (already stage-agnostic, reused verbatim): a mismatch is **advisory
   only** — report the warning naming both values, never block on it (a
   wrapper-only bundle regeneration between upload and paste, new
   `bundle_id`/unchanged `review_content_id`, must not invalidate the
   manual stage).
7. **Write set, exact.** **`REJECTED`-bundle refusal, second of two, under
   this step's own mutation guard** (`WFR-67`): immediately before the
   first write below, re-call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` — a withdrawal landing between step 5/6 and here must
   still be caught.
   - `APPROVE`: via `workflow_state.state_transaction(repo_root, lambda
     state: workflow_state.record_manual_implementation_review(state,
     work_item_id, verdict="APPROVE", bundle_id=<the feedback's own
     bundle_id, verbatim>, round=<round>, now=<now>,
     current_review_content_id=<review_content_id>,
     feedback_role=<the feedback's declared role>,
     feedback_review_content_id=<the feedback's declared review_content_id>))`
     and persisting the returned state — the resolved work item's
     `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` ledger fields (recording the
     feedback's actual `bundle_id` regardless of whether it matched the
     current recomputed one, so the ledger records what the reviewer
     actually saw) and its phase transition to the terminal
     `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` phase — the existing phase
     name, reused rather than a new one (`D-Implementation-Review-Version-Activation`'s
     "terminal-phase naming" decision).
   - `REVISE`: only the phase transition directly to
     `APPLYING_REVIEW_FEEDBACK`
     (`record_manual_implementation_review(..., verdict="REVISE", ...)` —
     no ledger write).
   - `BLOCK`: nothing (`record_manual_implementation_review(...,
     verdict="BLOCK", ...)` is a true no-op; the work item stays at
     `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`).
   Never `REVIEW_FEEDBACK.md` (already user-authored), the plan, registry,
   mapping, command, product, or bundle-content files.
8. **Report and stop.** For an `APPROVE`: state that
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is next and that only the user
   can invoke `/approve-review implementation` (the technical-approval gate
   for a `"2.2"` item additionally requires both ledger stages current --
   already satisfied the moment this write lands). For a `REVISE`: state
   that `/apply-implementation-review` is next. For a `BLOCK`: state that
   explicit user resolution is required before any further command runs.
   **Never** auto-continue to `/apply-implementation-review` or
   `/approve-review` in this same invocation.

Do not implement product or test code in this command. Do not edit the
plan/registry/mapping/artifacts files or any other command file.
