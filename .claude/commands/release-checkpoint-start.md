---
description: Release an uncommitted checkpoint start (its IN_PROGRESS entry and its claim). User-only -- never invoked by the agent autonomously.
argument-hint: "<work-item-id> <checkpoint-id>"
disable-model-invocation: true
state_writer: true
review-subject: none
---

**State-writer discipline (D1, item 354):** the one
`docs/ai-workflow/WORKFLOW_STATE.json` write this command performs is made by
`workflow_state.release_checkpoint_start_confirmed(repo_root, <work_item_id>,
<checkpoint_id>, user_confirmation=<text>, now=<now>)`, which calls
`workflow_state.release_checkpoint_start`: that holds
`workflow_state.lifecycle_lock` and one destructive `release-start` guard, and
calls `workflow_state.state_transaction(repo_root, mutator)` for the write,
never a separate read-then-write. The command commits nothing.

Releases a checkpoint start that was never committed, so that
`/request-plan-amendment` is no longer refused with
`AmendmentCheckpointActiveError` by a start its own approved plan forbids
completing. It restores the checkpoint's entry to the state `HEAD` records
(absent, or the `NEEDS_REVALIDATION` entry of a demoted checkpoint) and removes
the claim, in that order. Run it from the worktree that holds the claim.

**This command is user-only by construction**, the same two mechanisms as
`/resume-implementation` and `/retire-legacy-work-item`: (1)
`disable-model-invocation: true`, an additional layer where the harness
enforces it; (2) refuse to write anything unless the
user's own current-turn message supplies the literal sentence of step 3, which
names the exact resolved `work_item_id`, the exact `checkpoint_id` and the claim
that was shown. Mechanism (2) is the control this command carries itself, under
every harness; the flag does not replace it. The agent must never invoke this
command on the user's behalf, including as a step of another command's
execution, whatever the flag says. The writer validates the sentence itself with
`workflow_state.validate_checkpoint_start_release_confirmation(text,
work_item_id=..., checkpoint_id=..., claim_ref=...)`, which is
whitespace-normalised and case-insensitive and requires the whole sentence.
Never fabricate, infer or carry over this text from a previous turn or from
other command output. If it is missing, ask for it, naming the exact sentence,
and stop.

1. **Resolve the ids** from `$ARGUMENTS` only (`<work-item-id> <checkpoint-id>`),
   never from the confirmation text (the confirmation is checked *against* them)
   and never from `active_work_item_id`. Refuse with a named error when either is
   missing or the work item is not a key of `work_items` in
   `docs/ai-workflow/WORKFLOW_STATE.json`.
2. **Observe**: call `workflow_state.observe_checkpoint_start_release(repo_root,
   <work_item_id>, <checkpoint_id>)` and show everything it returns: the observed
   claim (checkpoint, worktree, `claimed_at`, whether it was adopted, and the
   claim reference `claim_ref`, the first 12 characters of its `owner_token`); the
   kind of start (`entry`: an `IN_PROGRESS` entry; `baseline`: an entry already
   equal to the one `HEAD` records, so a start that died before its state write
   or a half-finished release); the guard (none, or its step, class and whether
   its holder is running); and the probe result.
   - Stop on a classification refusal it raises and report it verbatim.
   - Stop, without asking for anything, when the probe says a guarded window of
     the work item is running: a live owner window is never overtaken.
   - Stop, as a success, on kind `released`: this worktree holds no claim and the
     checkpoint's entry is `HEAD`'s, so the start is already released (or none was
     held). Name any dead `release-start` guard as a harmless leftover this command
     did not touch. State that the pre-release obligation of step 3 is lifted and
     that `/request-plan-amendment <work_item_id>` or `/milestone-implement
     <work_item_id>` is next. No sentence is asked and nothing is written.
   - Every other kind (`entry`, `baseline`) goes on to step 3: while a claim
     exists, no step is skipped.
3. **Say, then ask for the sentence.** Before asking, print, in this order:
   - what the sentence attests: that the *owner session* is stopped and stays
     stopped until the release has completed. The owner session is any session
     other than the one running this command that started, resumed or may resume
     the checkpoint, in this worktree or another. The session running this command
     counts as stopped only while it runs nothing else for the work item, even when
     it is the very session that started the checkpoint;
   - that an interrupted release must be finished by running this command again,
     and that no session may resume this checkpoint, from this worktree or any
     other, until the command has reported success (the step 5 report, or the step
     2 `released` outcome);
   - then the exact sentence, built by
     `workflow_state.checkpoint_start_release_confirmation(<work_item_id>,
     <checkpoint_id>, <claim_ref>)`:

     ```
     Release checkpoint start <checkpoint_id> of <work_item_id> held under claim <claim_ref>; its owner session is stopped and stays stopped until this release has completed.
     ```

   Stop if the current turn's message does not carry it. The interruption wording
   is printed here, before the release, because a call that is killed prints
   nothing afterwards. The attestation is a stated precondition, not a check: the
   disk cannot show an owner session that is alive and idle.
4. **Release**: call `workflow_state.release_checkpoint_start_confirmed`, passing
   the sentence verbatim. It refuses, writing nothing, with the classification
   refusals of step 2, then `UserConfirmationRejectedError` (a missing or wrong
   sentence, including one given for an earlier claim of the same checkpoint),
   then the engine's own: `CheckpointOwnershipStateMismatchError` (the claim
   changed since it was confirmed, or a commit followed the start),
   `CheckpointOwnershipUnavailableError` (a window is running, or the claim or
   guard changed mid-release) and `CheckpointOwnedByOtherWorktreeError` (run it
   from the worktree that holds the claim). Report the refusal and stop.
5. **Report** what changed: the entry restored to `HEAD`'s state (absent, or the
   demoted entry), the claim removed, nothing committed, and that
   `/request-plan-amendment <work_item_id>` or `/milestone-implement
   <work_item_id>` is next. A refusal that the checkpoint is `COMPLETE` names two
   cases: a committed completion (`/milestone-implement` step 1c releases the
   claim) and an uncommitted one (resume step 1f in the owner's worktree).
6. **After an interruption that returns a refusal** (for example
   `CheckpointOwnershipUnavailableError` after the restore), repeat the
   obligation of step 3: do not start the owner session again; run this command
   again. It shows the interrupted release and, while the claim still exists, asks
   for the same attestation with the fresh claim; if the claim is already gone it
   reports the `released` outcome of step 2 and the obligation ends. An
   interruption that returns nothing, and a release whose report was lost after
   the engine returned, are covered by step 3 and by running this command again.
