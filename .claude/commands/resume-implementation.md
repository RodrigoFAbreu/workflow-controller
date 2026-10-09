---
description: Resume implementation of an outstanding checkpoint from the functional gate. User-only -- never invoked by Claude autonomously.
argument-hint: "<work-item-id>"
disable-model-invocation: true
state_writer: true
review-subject: none
---

**State-writer discipline (D1, item 354):** the one `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs is made by `workflow_state.resume_implementation(repo_root, <work_item_id>, <now>, <user_confirmation>)`, which holds `workflow_state.lifecycle_lock` and calls `workflow_state.state_transaction(repo_root, mutator)` for the write, never a separate read-then-write: `mutator` applies the pure `workflow_state.resume_implementation_state` to freshly re-read state under `.ai-review/runtime/WORKFLOW_STATE.lock`.

Returns a `2.1`/`2.2` work item that sits at `AWAITING_FUNCTIONAL_REVIEW` with a
registry checkpoint still outstanding to `IMPLEMENTING`, and marks its technical
approval `STALE` (workflow-2.9.0, `v2.6.0-003` (b)). From there
`/milestone-implement` runs unchanged: claim, `IN_PROGRESS`, completion,
self-review, bundle, both reviews, technical approval, functional review.

The state is reachable only from a hand-constructed or hand-edited state that
carries a `CURRENT` plan approval covering its registry. Ordinary flow cannot
leave `IMPLEMENTING` with a checkpoint outstanding, and a promoted legacy item
has no plan approval (the writer refuses it).

**This command is user-only by construction**, the same two mechanisms as
`/accept-milestone` and `/retire-legacy-work-item`: (1)
`disable-model-invocation: true`; (2) refuse to write anything unless the
user's own current-turn message supplies literal confirmation text naming the
exact resolved `work_item_id` and the word `resumption`. Claude must never
invoke this command on the user's behalf, including as a step of another
command's execution. The writer validates the confirmation itself with
`workflow_state.validate_user_only_confirmation(text, work_item_id=..., stage="resumption")`,
which requires the id as an exact token (a confirmation naming only
`milestone-80` or `milestone-8-b` never authorizes `milestone-8`) and the
stage word as a whole word. Never fabricate, infer or carry over this text
from a previous turn or from other command output. If it is missing, ask for
it, naming the exact `work_item_id`, and stop.

1. **Resolve the id** from `$ARGUMENTS` only, never from the confirmation text
   (the confirmation is checked *against* the resolved id) and never from
   `active_work_item_id`. Refuse with a named error when it is missing or is not
   a key of `work_items` in `docs/ai-workflow/WORKFLOW_STATE.json`.
2. **Require the confirmation** (above) and pass it verbatim to the writer.
3. **Resume**: call `workflow_state.resume_implementation`. It refuses, writing
   nothing, with `UserConfirmationRejectedError` (checked first), then the
   claim side's lifecycle refusals (it joins the claim side of the
   repository-global lifecycle, like `claim_checkpoint`, because
   `IMPLEMENTING` is the phase from which a checkpoint is claimed):
   `AmendmentInFlightError`, `StaleLifecycleStateError`,
   `LaggingWorktreeAmendmentError`, `AmendmentWitnessUnavailableError` (report
   `exc.evidence` in full and apply `/milestone-implement` step 1d's remedies),
   then `ResumeImplementationWrongPhaseError` (any phase other than exactly
   `AWAITING_FUNCTIONAL_REVIEW`), `ResumeImplementationUnsupportedVersionError`
   (a `1` item has no resume route), `StalePlanApprovalRegistryReadError` (no
   `CURRENT` plan approval covering the registry, which is what a promoted
   legacy item lacks), `ResumeImplementationRegistryTerminalError` (nothing is
   outstanding) and `ResumeWithoutTechnicalApprovalError` (no technical
   approval to stale). Report the refusal and stop.
   It writes `phase`, `technical_approval.status` (an already-`STALE` record is
   accepted as it is), `state_revision` and `last_transition` only. It clears
   nothing: `current_checkpoint_id`, the plan approval, `gate_evidence`,
   `reopenings` and `implementation_review_stages` stay as they are, because
   earlier evidence is bound to the content identity it reviewed and cannot
   satisfy a gate for the new content.
4. **Commit alone**: stage exactly `docs/ai-workflow/WORKFLOW_STATE.json`, never
   a broader `git add`, with `workflow_state.stage_scoped_state(repo_root, <work_item_id>)`
   in place of the bare `git add` of that path (it returns `False`, and the
   ordinary single-path `git add` runs, unless another work item holds
   uncommitted residue in the state file). The message body carries one line
   `Resume-Confirmation: <the user's confirmation text, on one line>`, then the
   single trailer `Workflow-Work-Item: <work_item_id>` as the message's **final
   paragraph**, after any `Co-Authored-By:` lines (`OPUS-R129-001`: Git's
   `git interpret-trailers --parse` treats only the last paragraph as trailers,
   so a blank line after it discards it). **Gate-policy content check**: right
   after the commit, call
   `workflow_state.assert_gate_policy_fields_unchanged_or_tightened(repo_root, <commit>)`.
   `workflow_state.validate_resume_implementation_commit` later checks this
   commit.
5. **Report** the resolved id and the transition (`AWAITING_FUNCTIONAL_REVIEW`
   to `IMPLEMENTING`, technical approval marked `STALE`), the commit SHA, and
   that the next step is `/milestone-implement <work_item_id>`. The technical
   approval is re-earned through the normal gate, because the checkpoint's work
   changes protected content; `/accept-milestone` refuses until then.
