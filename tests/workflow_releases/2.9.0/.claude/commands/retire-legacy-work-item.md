---
description: Retire a dormant legacy work item as already finished, keeping its legacy approval untouched.
argument-hint: "<work-item-id>"
disable-model-invocation: true
state_writer: true
review-subject: none
---

**State-writer discipline (D1, item 354):** the one `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs is made by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `mutator` is `lambda state: workflow_state.retire_legacy_work_item(state, <work_item_id>, <now>, <user_confirmation>)`, applied to freshly re-read state under `.ai-review/runtime/WORKFLOW_STATE.lock`.

Moves a dormant `LEGACY_READY` work item (imported with `import_legacy_work_item`,
governing version `1`, technical approval basis `LEGACY_V1`) to
`MILESTONE_COMPLETE` as already finished (workflow-2.9.0). Use it for a legacy
item that is done and will never be adopted. The motivating case is a
milestone built and reviewed entirely under Workflow v1 whose protected content
has since changed, so `/prepare-functional-review` would refuse to promote it
(`LegacyAdoptionStaleApprovalError`).

**This command is user-only by construction**, the same two mechanisms as
`/accept-milestone`: (1) `disable-model-invocation: true`; (2) refuse to write
anything unless the user's own current-turn message supplies literal
confirmation text naming the exact `work_item_id` and the word `retirement`.
The writer validates it itself with
`workflow_state.validate_user_only_confirmation(text, work_item_id=..., stage="retirement")`,
which requires the id as an exact token (a confirmation naming only
`milestone-80` or `milestone-8-b` never authorizes `milestone-8`). Never
fabricate, infer or carry over this text from a prior turn. If it is missing,
ask for it and stop.

1. **Resolve the id** from `$ARGUMENTS` only, never from the confirmation text
   (the confirmation is checked *against* the resolved id). Refuse with a named
   error when it is missing or is not a key of `work_items` in
   `docs/ai-workflow/WORKFLOW_STATE.json`. Never fall back to
   `active_work_item_id`: a dormant legacy item is never the active one.
2. **Require the confirmation** (above) and pass it verbatim to the writer.
3. **Retire**: call the writer inside `workflow_state.state_transaction`. It
   refuses, writing nothing, with `UserConfirmationRejectedError` (checked
   first), `LegacyRetirementWrongPhaseError` (any phase other than exactly
   `LEGACY_READY`: a promoted, already retired or finished item and every
   other phase), `LegacyRetirementActiveItemError` or
   `LegacyRetirementUnfinishedChildrenError`. Report the refusal and stop. It
   never runs the stale-approval or branch-reconciliation checks, never calls
   `promote_legacy_work_item` or `complete_work_item`, and takes no lifecycle
   lock (a `LEGACY_READY` item has no checkpoint, claim or amendment; a
   sibling's lifecycle is covered by the unfinished-children refusal).
4. **Commit alone**: stage exactly `docs/ai-workflow/WORKFLOW_STATE.json`, never
   a broader `git add`, with `workflow_state.stage_scoped_state(repo_root, <work_item_id>)`
   in place of the bare `git add` of that path (it returns `False`, and the
   ordinary single-path `git add` runs, unless another work item holds
   uncommitted residue in the state file). The message body carries one line
   `Retirement-Confirmation: <the user's confirmation text, on one line>`,
   then the trailers `Workflow-Legacy-Retirement: <work_item_id>` and
   `Workflow-Work-Item: <work_item_id>` as the message's **final paragraph**,
   after any `Co-Authored-By:` lines (`OPUS-R129-001`: Git's
   `git interpret-trailers --parse` treats only the last paragraph as
   trailers, so a blank line after them discards both). **Gate-policy content
   check**: right after the commit, call
   `workflow_state.assert_gate_policy_fields_unchanged_or_tightened(repo_root, <commit>)`.
   `workflow_state.validate_legacy_retirement_commit` later checks this commit
   and `workflow_state.discover_legacy_retirement_commit` finds it.
5. **Report** what was retired, that its `LEGACY_V1` technical approval was
   kept untouched and nothing was adopted or promoted. A retired item stays
   closed: no later pull-request evidence reopens it
   (`reopen_retired_legacy_item`); further work is a new work item. An older
   release (2.8.0 or earlier) has no such guard.
