---
description: Request an amendment to an already-approved plan, mid-implementation. User-only -- never invoked by Claude autonomously.
argument-hint: "[work-item-id]"
disable-model-invocation: true
state_writer: true
review-subject: none
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `AMENDING_PLAN` state of `docs/ai-workflow/MILESTONE_WORKFLOW.md`
(`workflow-2.4.0`, `D-Plan-Amendment-1`/`-2`/`-3`).

0. **Resolve the target work item**: the id named in `$ARGUMENTS`, or
   `active_work_item_id` from `docs/ai-workflow/WORKFLOW_STATE.json` if
   omitted -- refuse with a named error if neither resolves to an existing
   `work_items` entry. This mechanism has nothing to amend for a work item
   with no `docs/ai-workflow/WORKFLOW_STATE.json` entry at all (an ordinary
   `"1"` item that never routed through `workflow_state.route_work_item`):
   refuse, naming the work item, rather than inventing a phase for it.
   Applies uniformly to `governing_workflow_version: "1"`, `"2.1"`, and
   (`workflow-2.5.0`) `"2.2"` work items alike -- unlike the two-stage
   plan-review protocol, nothing about this mechanism is
   `"2.1"`/`"2.2"`-only (`D-Plan-Amendment-4`); only the downstream
   `/milestone-plan` re-entry that follows this command already branches on
   governing version, unchanged (and, per that command's own step 0, a
   `"2.2"` item takes the identical `"2.1"` branch there too).

**This command is user-only by construction**, the same authority shape
`/approve-review` and `/accept-milestone` already use, and deliberately
distinct from `USER_OVERRIDE` (`D-Plan-Amendment-2`). `disable-model-
invocation: true` is the primary, harness-enforced control (blocks the
SlashCommand tool). Claude must never invoke it on the user's own behalf,
including as a step of another command's execution, independent of that
flag. It never reads, writes, or compares against the literal string
`USER_OVERRIDE` -- that sentinel stays reserved for `/approve-review`'s
existing approval-basis fallback and is not reused, generalized, or
aliased here.

**User-only guard, mechanism (2)**: refuse to write anything unless the
user's own current-turn message supplies, together:

- literal confirmation text naming the exact `work_item_id` being amended
  and the exact literal word `amendment`;
- a required, non-empty free-text `reason` for the amendment, recorded
  verbatim into `amendment_history` -- this workflow's existing culture of
  never accepting an unexplained deviation.

Never fabricate, infer, or carry over either from a prior turn; if either
is missing this turn, ask for it naming the exact `work_item_id`, then stop
and wait.

1. **Preconditions, all refusals rather than silent handling**
   (`D-Plan-Amendment-1`). Read the resolved work item fresh.

   - **Phase**: `phase in {"IMPLEMENTING", "SELF_REVIEWING_IMPLEMENTATION"}`
     -- the two phases this release supports, and deliberately the only
     two. Checked again, authoritatively, inside
     `workflow_state.request_plan_amendment` itself
     (`WrongPhaseForAmendmentRequestError`, naming the actual phase) --
     this step's own read is for reporting the refusal early, not a
     separate source of truth.
   - **No checkpoint may be `IN_PROGRESS`, and no checkpoint claim may be
     outstanding** (widened, `I1-new`): read `work_item["checkpoints"]`
     for any entry with `status == "IN_PROGRESS"`, and call
     `workflow_state.resolve_claim(repo_root, work_item_id)`. Either
     non-empty: stop and report which checkpoint is still open or claimed,
     and that it must be finished with `/milestone-implement` or released
     through the existing claim-takeover mechanism before an amendment can
     be requested. Reconciliation (`D-Plan-Amendment-4`) is defined only
     over `COMPLETE` checkpoints; this is a scope-narrowing choice, not a
     new mechanism. **Checked again, authoritatively, inside
     `workflow_state.request_plan_amendment` itself**
     (`AmendmentCheckpointActiveError`, XMODEL-R4-B1) -- this step's own
     read is for reporting the refusal early, not a separate source of
     truth. **What actually closes the race, and its real scope
     (corrected, round 9 external implementation review, `IMPL9-R1`,
     superseding this step's own previous, round-4-era text)**: a
     checkpoint claim is published to the filesystem claims directory
     *before* `workflow_state.transition_checkpoint_in_progress` writes
     `WORKFLOW_STATE.json` (`/milestone-implement` step 1d's documented
     ordering), so a claim can become outstanding *after* this step's own
     read returns clean but *before* this command's own `state_transaction`
     call below acquires the state lock. Two independent guards, not one,
     matter here, and only one of them actually closes that window:
     `transition_checkpoint_in_progress`'s own independent
     `IllegalCheckpointStartPhaseError` guard *detects* a claim published
     into the window after the fact (the claim itself has already reached
     disk); `workflow_state.claim_checkpoint`'s own pre-publication phase
     check, run inside the identical `WORKFLOW_STATE.lock` this command's
     own `state_transaction` call below acquires, is what actually *closes*
     it, by serializing claim publication with this command's own
     supersede-and-commit so neither can complete while the other holds the
     lock (`XMODEL-R8-B1`). **That closure is real only within one worktree
     root, not across the repository (`XMODEL-R9-B1`)**: `WORKFLOW_STATE.lock`
     is per-worktree, so a checkpoint claimed from a *different* linked
     worktree of the same repository is not serialized against this
     command's own critical section, and `claim_checkpoint`'s phase check
     in that other worktree reads its own working-tree `WORKFLOW_STATE.json`,
     which cannot observe an `AMENDING_PLAN` this command committed only in
     this worktree. This step's own read of
     `workflow_state.resolve_claim(repo_root, work_item_id)` above *is*
     shared across worktrees (`claims_dir` is `git_common_dir`-rooted), so
     an *already-published* foreign-worktree claim is still caught here --
     what is not closed is a claim publication racing this command's own
     commit from another worktree, or a claim attempted from another
     worktree after this command's `AMENDING_PLAN` is durable in this one.
     This residual is deliberately left open for `2.4.0`; see
     `docs/defects/v2.4.0-002-amendment-claim-race-crosses-worktree-boundary.md`.
     An operator running this command and a concurrent `/milestone-implement`
     from two different worktrees of the same work item's repository should
     not rely on this command alone to prevent the race -- coordinate
     out-of-band (finish or release the checkpoint claim first, from
     whichever worktree holds it) when more than one worktree is in play.
   - **No open plan-approval transaction**: call `evidence =
     workflow_state.plan_approval_takeover_evidence(repo_root)`. A
     non-`None` `evidence["journal"]` means an `/approve-review plan`
     transaction from a prior, interrupted invocation is still open --
     report it (mirroring `approve-review.md` step 4b's own report) and
     **stop**; an amendment must never race an in-flight approval commit.
   - **The current `plan_approval`'s own approval commit must actually be
     reachable**: this is checked, authoritatively, inside
     `workflow_state.request_plan_amendment` itself
     (`AmendmentApprovalCommitUnreachableError`, naming the work item and
     its `base_commit`) -- *before* it supersedes anything. This step adds
     no separate check for it; do not call
     `workflow_state.implementing_entry_reachable`/`approval_is_current`
     here, which would incorrectly also refuse on a digest-only staleness
     this amendment is about to replace anyway (`D-Plan-Amendment-1`,
     `B-R12-1`).
   - **Every checkpoint id in the work item's own current registry must be
     of the shape `CP<digits>`** (IMPL2-R1): also checked, authoritatively,
     inside `workflow_state.request_plan_amendment` itself
     (`AmendmentCheckpointIdShapeError`, naming every offending id) --
     *before* it supersedes anything. `<!-- CPn -->`/`<!-- /CPn -->` is the
     *only* shape `/approve-review plan` step 4c's
     `validate_post_anchor_coverage` can ever match; a registry id of any
     other shape (e.g. `WF4a-i`) can never be given a well-formed anchor,
     so this refuses here rather than two review stages later with no
     in-band recovery. A registry-less work item has nothing to check. A
     registry row with no `id` key at all is a distinct refusal
     (`AmendmentRegistryMissingIdError`, IMPL4-O2) -- named separately from
     the shape check above because there is no id to check the shape of.

2. **Write the amendment request**: call `workflow_state.state_transaction(
   repo_root, lambda state: workflow_state.request_plan_amendment(state,
   work_item_id, reason, repo_root=repo_root, now=<now>))` and persist the
   returned state to `docs/ai-workflow/WORKFLOW_STATE.json`. In one
   transaction this: sets `plan_approval.status = "SUPERSEDED"`; appends
   one entry to `amendment_history` (bounded, content-addressed --
   `pre_amendment_approval_commit` plus the blob SHAs already inside
   `superseded_plan_approval.review_content_manifest`, never a stored copy
   of the plan/registry documents themselves, `D-Plan-Amendment-3`); sets
   `amendment_base_commit` to the current `HEAD`; and writes `phase =
   "AMENDING_PLAN"`.

   `AmendmentApprovalCommitUnreachableError`/
   `WrongPhaseForAmendmentRequestError`: stop and report the exception's
   own message verbatim -- neither is caught or reworded here.

3. **Commit the state write, alone**: stage exactly
   `docs/ai-workflow/WORKFLOW_STATE.json` (never a broader `git add`) and
   create one commit carrying a single `Workflow-Work-Item: <work_item_id>`
   trailer as the message's own final paragraph -- after any
   `Co-Authored-By:`/`Claude-Session:` lines, never before them
   (`OPUS-R129-001`, the same rule `milestone-implement.md` step 1f and
   `approve-review.md` step 6.4 already state). No
   `Workflow-Bundle-Generation-Record`, `Workflow-Supersedes`,
   `Workflow-Checkpoint` or `Workflow-Plan-Approval` trailer -- this is
   none of those.

4. **What happens next.** The work item now sits at `AMENDING_PLAN`; the
   very next `/milestone-plan [work-item-id]` invocation resumes it through
   that command's own existing dual-mode branch (step 0), exactly like any
   other non-terminal existing entry -- no new plan-review machinery, and
   the entire two-stage local-then-manual-external review protocol and
   `/approve-review plan` gate run completely unmodified for the amended
   plan (`D-Plan-Amendment-4`). While this amendment stays open (this
   entry's own `resolved_at_plan_revision` still `null`), the plan-stage
   bundle `scripts/prepare-ai-review.sh` produces for this work item gains
   one additional file, `AMENDMENT_DIFF.patch`, alongside the ordinary
   `current/` contents -- see `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
   "Bundle structure" for what it contains and how it is (and is not)
   authoritative. **The amended plan document itself must delimit every
   registry checkpoint id with a `<!-- CPn -->`/`<!-- /CPn -->` anchor pair**
   (one or more, non-overlapping, around that checkpoint's own content) --
   `/approve-review plan` step 4c's `validate_post_anchor_coverage` refuses
   approval outright, naming the first uncovered id, for any registry
   checkpoint with no well-formed pair in the plan text, so add the anchors
   while drafting or revising the amended plan, not after a refusal.
   Report the new phase and **stop** -- never chain into drafting the
   amended plan in the same invocation.
