---
description: Implement the approved plan checkpoint by checkpoint, then stop for external implementation review.
argument-hint: [work-item-id]
state_writer: true
review-subject: bundle
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `IMPLEMENTING` state of `docs/ai-workflow/MILESTONE_WORKFLOW.md`.
Requires an approved plan (from `/milestone-plan` + `/apply-plan-review`).

0. **Dual-mode branch** (Workflow v2.1, `WF2`): resolve the target work
   item -- the id named in `$ARGUMENTS`, or `active_work_item_id` from
   `docs/ai-workflow/WORKFLOW_STATE.json` if omitted (refuse with a named
   error if neither resolves to an existing, non-terminal `work_items`
   entry -- never guess) -- and read its `governing_workflow_version`.
   - **`governing_workflow_version: "1"`**: steps 1-5 below execute
     exactly as written, with no `WORKFLOW_STATE.json`/
     `WORKFLOW_CONFIG.json` reads or writes beyond the one just
     performed -- this branch is v1-inert by construction, verified by a
     golden-output test (`WF8a-ii`).
   - **`governing_workflow_version: "2.1"`**: step 1 is replaced entirely
     by the resumable, one-checkpoint-per-invocation sequence marked
     **[2.1 step 1]** below -- Workflow v2.1 core's own
     "one-resumable-checkpoint session model", the same discipline the
     bootstrap command uses for its own one work item. Steps 2-5 are
     entered only on whichever invocation first observes every registry
     checkpoint `COMPLETE` -- never in the same invocation that completed
     the last checkpoint (this command never loops across a
     checkpoint-vs-wrap-up boundary any more than it loops across
     checkpoints). Steps 3-5 execute unchanged; **step 2 additionally
     performs one `[2.1]`-marked state write**
     (`enter_self_reviewing_implementation`, salvage audit `B8`) -- the
     only difference between the branches after step 1, marked `[2.1]`
     inline exactly as step 1's own replacement is. The `"1"` branch stays
     v1-inert: it performs no `WORKFLOW_STATE.json` read or write beyond
     step 0's own.
   - **`governing_workflow_version: "2.2"`** (workflow-2.5.0,
     round-6 plan-review-inheritance widening, `LOCAL_MODEL_PLAN_REVIEW`
     round 6, finding B1(a)/(c)): takes the identical `"2.1"` branch
     immediately above -- the resumable, one-checkpoint-per-invocation
     session model and the checkpoint-vs-wrap-up boundary discipline are
     entirely independent of which review protocol (single-stage,
     two-stage plan-only, or two-stage plan-and-implementation) governs
     this work item's own review stages. This is a distinct item from step
     4 below's own version-dependent `record_bundle_generation(stage=
     "implementation")` documentation -- that is about which phase step 4
     writes; this is about which branch of *this* step selects.
   - **Any other `governing_workflow_version`** (round-7 optional
     finding 2): refuse cleanly, naming the actual value -- never guess
     which branch above applies.

1. For each checkpoint in the approved plan, in order:
   - implement it, following `CLAUDE.md`/`AGENTS.md`/`.github/copilot-instructions.md`/
     `.github/instructions/*` for layer boundaries, migrations, and enum
     persistence rules;
   - run the narrowest relevant check (single test class/method, not the
     full suite);
   - review the resulting diff and fix confirmed defects;
   - update `docs/ACTIVE_MILESTONE.md` (checkpoint complete, verified state);
   - create the authorized intermediate commit for that checkpoint;
   - continue to the next checkpoint without stopping, unless a stop
     condition from `AGENTS.md` applies (ambiguous product behavior,
     architecture change, new dependency category, unapproved schema
     migration, destructive operation, repeated verification failure,
     unrelated working-tree changes).

**[2.1 step 1] Resumable single-checkpoint implementation** (D-Selection,
D3's `IN_PROGRESS`/`COMPLETE` state writers, D3's worktree-scoped
dirty-resume rule, `WF2`):

1a. **Entry validation**: call
    `workflow_state.implementing_entry_status(repo_root, work_item,
    base_commit)` (workflow-2.7.0, `LPR-R3-002`; the same function the
    orchestration protocol's `next-action` calls, row 22;
    `workflow_state.implementing_entry_reachable` is its `reachable` field).
    `reachable: false` stops here -- report its `cause` and that cause's
    remedy: `plan_approval_not_current` (no `plan_approval`, or it is
    `STALE`: obtain a current plan approval),
    `plan_approval_commit_unreachable` (the approval commit is not
    HEAD or an ancestor of it: restore the history that contains it), or
    `plan_content_drifted` (the plan-stage content no longer matches the
    approved content: restore the approved plan-stage bytes, or
    `/request-plan-amendment <id>`); never proceed on a stale or
    unreachable plan approval. Runs on every invocation, not only the
    first (missing-test item 9: reachability must hold identically at
    checkpoints 1, 2, and N).
1b. **Select the checkpoint**: load
    `docs/ai-workflow/registry/<work_item_id>-registry.json` and call
    `workflow_state.select_next_checkpoint(work_item, registry)`
    (D-Selection's rules 1/2/4 -- pure and deterministic, missing-test
    item 28).
    - Returns `None`: every registry checkpoint is already `COMPLETE`
      *in this worktree's own local state* -- pass `None` through to 1c
      as `selected_id` regardless (do not skip to step 2 yet: `1c` may
      still release a stale self-owned claim and return a concrete
      `FRESH` checkpoint or the terminal `NO_CHECKPOINT`).
    - Raises `NoCheckpointReadyError`: stop, report the named blocked
      checkpoint and its unmet dependencies verbatim -- never silently
      idle (rule 4).
    - Otherwise: the returned id is passed through to 1c as
      `selected_id`. Classification (resume vs. fresh start vs.
      continuing an interrupted claim) is decided wholly by 1c, not
      here.
1c. **Resolve ownership** (`D-Checkpoint-Ownership`, "Where the check
    belongs, and the ordering", `WF8b`): call
    `workflow_state.resolve_checkpoint_ownership(repo_root, work_item,
    work_item_id, selected_id, now=<now>)`. Returns `(outcome,
    checkpoint_id, owner_token)` where `outcome` is `RESUME`, `FRESH`,
    `CONTINUE_CLAIM`, or the terminal `NO_CHECKPOINT`. No
    mutation-capable outcome ever carries a `None` checkpoint id.
    - `NO_CHECKPOINT`: nothing to implement this invocation -- skip
      straight to step 2 below (do not re-implement anything, do not
      re-select). Step 2's own
      `enter_self_reviewing_implementation` call is what makes the phase
      match: this branch is reached both on the ordinary invocation after
      the last checkpoint completed (phase already
      `SELF_REVIEWING_IMPLEMENTATION`, a no-op) and on the first
      invocation after a plan re-approval on an already-complete registry
      (phase `IMPLEMENTING`, a real transition). Never assume the phase is
      already correct here (salvage audit `B8`).
    - `RESUME`: this worktree's own claim already covers `checkpoint_id`
      and the local state already records it `IN_PROGRESS`. Skip 1d
      entirely and go straight to 1e.
    - `FRESH`: an ordinary, uncontended start. Proceed to 1d's
      fresh-start branch. `owner_token` is `None` here -- 1d mints it.
    - `CONTINUE_CLAIM`: this worktree's own claim exists for
      `checkpoint_id` but the local state write never happened (a crash
      between claim publication and the state write). Proceed to 1d's
      continue-claim branch, using the returned `owner_token` directly --
      never re-acquire a claim that already exists.
    - Any exception (`WorktreeIdentityMissingError`,
      `WorktreeIdentityMismatchError`, `CorruptJsonError`,
      `CheckpointOwnedByOtherWorktreeError`,
      `CheckpointOwnershipStateMismatchError`,
      `CheckpointOriginationUnprovableError`,
      `CheckpointOwnershipUnavailableError`): stop immediately, before
      touching anything else. Report the attached
      `exc.ownership_evidence` in full (`claim`, `holder`,
      `claimed_checkpoint`, `escape`, and -- for
      `CheckpointOriginationUnprovableError` -- also `local_identity`,
      `claim_state`, and `origination`) and require the user to act on
      the named escape: resume from the worktree that actually holds the
      claim, explicitly take the claim over
      (`workflow_state.take_over_claim`), or reconcile manually. Never
      guessed (D3, missing-test items 31, 43, 71).
    - A `workflow_state.LifecycleRefusalError` subclass (workflow-2.6.0):
      the `adopt_claim` this step may run, and a `take_over_claim` of an
      absent claim, run the same amendment-witness check `claim_checkpoint`
      does. Stop and report exactly as step 1d's "Lifecycle refusals"
      paragraph says, with `exc.evidence`.
1d. **Establish identity, acquire, then write state under the guard**
    (`FRESH`/`CONTINUE_CLAIM` only -- skipped entirely for `RESUME`).
    Both mutations below are covered by the fenced compare-and-delete
    guard described in "Which mutations are guarded, exhaustively":
    - **`FRESH`**, in this exact order (both orderings are load-bearing,
      not stylistic -- claim before state so a crash between them still
      leaves behind the authority that protects the checkpoint; identity
      before claim so the crash window `CONTINUE_CLAIM` exists for can
      never be entered without it):
      1. call `workflow_state.write_worktree_identity(repo_root,
         work_item_id, now=<now>)` -- the establishing write, outside any
         guard, since no claim/token exists yet;
      2. call `workflow_state.claim_checkpoint(repo_root, work_item_id,
         checkpoint_id, now=<now>)`, which mints `owner_token` and returns
         the published claim record -- `owner_token` is that record's
         `owner_token` field, never the record itself;
      3. inside `workflow_state.owner_mutation(repo_root, work_item_id,
         owner_token, checkpoint_id=checkpoint_id, step="1d",
         step_class=workflow_state.DESTRUCTIVE, now=<now>)`: refresh
         `write_worktree_identity` again, call
         `workflow_state.transition_checkpoint_in_progress(state,
         work_item_id, checkpoint_id, start_commit=<current HEAD>,
         now=<now>)`, and persist the returned state to
         `docs/ai-workflow/WORKFLOW_STATE.json`.
    - **`CONTINUE_CLAIM`**: identity and the claim already exist and
      `owner_token` is already known from 1c -- only step 3 above runs
      (the guarded state write: refresh `write_worktree_identity` +
      `transition_checkpoint_in_progress` + persist). Never re-acquire
      the claim.

    **Lifecycle refusals at `claim_checkpoint`** (workflow-2.6.0,
    `D-Repo-Global-Lifecycle`, closing `v2.4.0-002`): the claim is published
    under the repository-global lifecycle lock (primitive 9), after the
    mixed-release lag probe and the amendment witness's predicate list and
    before the local phase check. So this step refuses an amendment in
    flight in *any* linked worktree, even while this worktree's own state
    still says `IMPLEMENTING`. `adopt_claim` (reached from 1c) and an
    absent-claim `take_over_claim` run the same check. Any of these
    refusals publishes nothing: stop immediately, before step 3, and report
    the exception's message and `exc.evidence` in full:
    - `AmendmentInFlightError`: an amendment of this work item is open, or
      its resolution reserved, somewhere in the repository. Evidence names
      the requesting (or resolving) worktree, its branch and the sequence.
      Remedy: let that amendment finish (`/milestone-plan`, review,
      `/approve-review plan`), then merge its approval into this branch.
      Only when the evidence carries a `literal` (the requester or
      resolver worktree is gone, unreadable, detached, or switched branch,
      so the orphan test cannot decide) may the user clear an abandoned
      witness with that exact literal, through
      `workflow_state.clear_amendment_witness` /
      `workflow_state.clear_amendment_resolution`, which re-check under the
      lock that nothing still holds it;
    - `StaleLifecycleStateError`: an amendment was resolved elsewhere and
      this worktree's `HEAD` does not show it. Remedy: merge the resolved
      amendment first, then re-run;
    - `AmendmentResolutionConflictError`: this branch carries a different
      resolution of an amendment than the one recorded repository-wide.
      Remedy: discard the divergent approval and merge the recorded one --
      never collapsed, and no literal;
    - `AmendmentResolutionReservedError`: the only visible resolution is
      another worktree's own approval commit awaiting amend recovery.
      Remedy: wait for that `/approve-review plan` transaction to finish;
    - `LaggingWorktreeAmendmentError`: a worktree still on a pre-`2.6.0`
      release holds an unresolved amendment the witness does not record.
      Remedy: finish or discard it there, or merge the `2.6.0` update into
      that worktree's branch;
    - `AmendmentBootstrapConflictError`: the first lifecycle check after
      updating from `2.5.1` found worktrees whose `amendment_history`
      disagree. Remedy: finish or discard the divergent amendment or
      approval on all but one branch;
    - `AmendmentWitnessUnavailableError`/`LifecycleStateUnreadableError`: a
      torn, symlinked or unknown-shaped witness, or an unreadable state
      file -- refused, never guessed (INV-3). Remedy: inspect and repair it
      by hand;
    - `LifecycleLockOrderError`: this session already holds another
      lock-order primitive -- a caller bug; the claim is never published
      from inside another primitive's window.
1e. **Implement exactly that one checkpoint**: follow
    `CLAUDE.md`/`AGENTS.md`/`.github/copilot-instructions.md`/
    `.github/instructions/*` for layer boundaries, migrations, and enum
    persistence rules; run the narrowest relevant check (single test
    class/method, not the full suite); review the resulting diff and fix
    confirmed defects; update the work item's own narrative record
    (`docs/ACTIVE_MILESTONE.md` for a product item; the requirements
    ledger, `WF4b`, for a process item) -- unless a stop condition from
    `AGENTS.md` applies (ambiguous product behavior, architecture change,
    new dependency category, unapproved schema migration, destructive
    operation, repeated verification failure, unrelated working-tree
    changes), in which case stop here instead of committing.
1f. **Commit, verify durable, then release** (`D-Checkpoint-Ownership`'s
    fenced compare-and-delete, `WF8b`). Inside
    `workflow_state.owner_mutation(repo_root, work_item_id, owner_token,
    checkpoint_id=checkpoint_id, step="1f-commit",
    step_class=workflow_state.DESTRUCTIVE, now=<now>)`:
    - call `workflow_state.complete_checkpoint(state, work_item_id,
      checkpoint_id, registry, now=<now>, repo_root=repo_root)` and persist
      the returned state to `docs/ai-workflow/WORKFLOW_STATE.json` --
      checkpoint-complete-vs-all-complete semantics: `phase` stays
      `IMPLEMENTING` unless every registry checkpoint is now `COMPLETE`, in
      which case it becomes `SELF_REVIEWING_IMPLEMENTATION` as part of this
      same write. `repo_root` also drives `WFR-69`'s own non-approval-gated
      pre-flight for any checkpoint whose registry entry declares
      `completion_obligations` -- vacuous for a checkpoint that declares
      none, which is every checkpoint besides `workflow-v2-1-core`'s own
      `WF8b`/`WF8c`. The state file is the sole writable record of
      checkpoint status from this point on; the trailer below is
      verification evidence, never a
      second source of truth;
    - then create one commit for this checkpoint's changes, carrying
      `Workflow-Checkpoint: <id>` + `Workflow-Work-Item: <work_item_id>`
      trailers (D-Commit-Provenance's exact trailer shape -- this command
      only writes the trailer, it never needs to search for one itself;
      the exact scoped lookup later checks use is
      `workflow_state.discover_checkpoint_commits`, `WF4a-iii`). **These
      two lines must be the commit message's final paragraph** -- after
      any `Co-Authored-By:`/`Claude-Session:` lines, never before them
      (`OPUS-R129-001`): Git's `git interpret-trailers --parse`, the exact
      mechanism `discover_checkpoint_commits` uses, treats only the
      message's last paragraph as trailers, so a blank line after these
      two lines (e.g. one followed by `Co-Authored-By:`) silently discards
      both and makes the checkpoint undiscoverable.

    This commit keeps whole-file staging. **Gate-policy content check** (workflow-2.8.0, D-GP-Policy): right after this commit, call `workflow_state.assert_gate_policy_fields_unchanged_or_tightened(repo_root, <commit>)`; it refuses a commit that changed `gate_policy_adoption` without a valid, chained record or loosened `gate_policy_floor`.

    Both acts happen inside the same `"destructive"` guard window so a
    takeover landing between them cannot leave two worktrees each
    believing they completed the checkpoint. Once the guard is released
    (on exiting `owner_mutation`), verify the completion is durable --
    `workflow_state.committed_checkpoint_status(repo_root, work_item_id,
    checkpoint_id) == "COMPLETE"` -- and only then call
    `workflow_state.release_checkpoint(repo_root, work_item_id,
    checkpoint_id, owner_token=owner_token, now=<now>)`. Releasing before
    the commit is durable would hand the work item to another worktree
    while the completion is still uncommitted.
1g. **Stop immediately** -- never continue to the next checkpoint, and
    never continue into step 2 in the same invocation even when this was
    the last checkpoint (a later invocation observes the phase change and
    proceeds from step 2, per this command's own dual-mode note above).
    Report the checkpoint just completed, its commit SHA, and the
    resulting phase. Continuing requires invoking this command again.

2. When all checkpoints are implemented, enter
   `SELF_REVIEWING_IMPLEMENTATION`.

   **[2.1] The phase transition is a state write, not narrative**
   (salvage audit `B8`): call
   `workflow_state.enter_self_reviewing_implementation(state,
   work_item_id, registry, now=<now>)` and persist the returned state to
   `docs/ai-workflow/WORKFLOW_STATE.json`, through
   `state_transaction` like every other write this command performs.
   `registry` is the same
   `docs/ai-workflow/registry/<work_item_id>-registry.json` step 1b
   loaded. Behaviour:
   - already `SELF_REVIEWING_IMPLEMENTATION` — the ordinary case, since
     step 1f's own `complete_checkpoint` wrote it when the last
     checkpoint completed — it is a **true no-op**: the returned state is
     the input state, with no `state_revision` bump, so re-entering this
     step on a later invocation costs nothing and writes nothing;
   - `IMPLEMENTING` with every registry checkpoint `COMPLETE` — the case
     that used to wedge the item permanently, reached whenever
     `/approve-review plan` re-approves a revised plan for an item whose
     checkpoints are all already done (`apply_plan_approval` sets
     `IMPLEMENTING` unconditionally, and correctly) — it performs the
     transition, and this step is the only place that transition happens;
   - `IMPLEMENTING` with an outstanding checkpoint —
     `IncompleteCheckpointsForSelfReviewError`, naming it. Stop and
     report: there is real work left, and step 1 is where it happens.
     Never hand-write the phase to get past this;
   - any other phase — `IllegalSelfReviewEntryPhaseError`. Stop.

   **When (and only when) the call actually transitioned the phase**,
   commit `docs/ai-workflow/WORKFLOW_STATE.json` **alone** — stage exactly
   that one path, never a broader `git add` — carrying a single
   `Workflow-Work-Item: <work_item_id>` trailer as the message's final
   paragraph, and **no** `Workflow-Bundle-Generation-Record`,
   `Workflow-Supersedes` or `Workflow-Checkpoint` trailer (this is not a
   generation-record commit). A no-op call commits nothing. This commit
   keeps whole-file staging. **Gate-policy content check** (workflow-2.8.0, D-GP-Policy): right after this commit, call `workflow_state.assert_gate_policy_fields_unchanged_or_tightened(repo_root, <commit>)`; it refuses a commit that changed `gate_policy_adoption` without a valid, chained record or loosened `gate_policy_floor`.

   The durability is load-bearing, for the same reason step 1f commits
   `complete_checkpoint`'s own write with the checkpoint commit: step 4's
   generation-record commit is validated against **its parent's committed
   phase** whenever the round resolves `same_content`
   (`_classify_generation_record_interval`'s recovered-role clause
   requires the parent to record one of
   `RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES`). Left
   uncommitted, the source phase would only ever exist in the working
   tree, the parent would still record `IMPLEMENTING`, and the whole
   recovery this step exists to enable would refuse one step later — and
   `same_content` is the *ordinary* outcome after a plan-only re-approval,
   whose protected implementation content is by definition unchanged
   (salvage audit `B8`).

   Then review the full milestone diff for correctness, layer-boundary
   violations, missing tests, and maintainability. Fix all blocking and
   important findings.
3. Run the full required verification for the milestone (narrow checks are
   not sufficient at this point): `./gradlew spotlessCheck detekt lintDebug
   testDebugUnitTest`, plus `connectedDebugAndroidTest` if a device/emulator
   is available and the plan touches persistence/migrations. Report exactly
   what ran and its real result — never claim a check passed that did not
   run.
4. Enter `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` **for a `"1"`/`"2.1"`
   item, or `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` for a `"2.2"` item**
   (workflow-2.5.0, `D-Implementation-Review-Stages`; documented here, not a
   separate writer): the phase this step's own `record_bundle_generation`
   call below actually writes is
   `workflow_state.bundle_generation_target_phase("implementation",
   governing_workflow_version)`'s resolved value -- `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
   for `"1"`/`"2.1"` (byte-identical to before this checkpoint) and
   `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` for `"2.2"` -- never a bare
   hard-coded literal. This section's own heading names the `"1"`/`"2.1"`
   case for continuity with the rest of this file; the `"2.2"` case is the
   identical call, resolving differently, not a second code path:
   - if this work item has a `docs/ai-workflow/WORKFLOW_STATE.json` entry:
     **`REJECTED`-bundle refusal, this command's sole assertion, immediately
     preceding `record_bundle_generation`** (`WFR-67`, one of the three
     named writer call sites; this consuming act is a report following a
     write rather than a mid-operation guard, so the single assertion
     immediately preceding the write below is also the assertion that
     guards the report in step 5): call
     `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
     work_item_id)` here. **First**, before writing any bundle file, call
     `workflow_state.record_bundle_generation(state, work_item_id,
     stage="implementation", head=<current HEAD SHA>, now=<now>)` (`WF4c`,
     D-Approval-Commits' sole writer of `reviewed_implementation_head`),
     persist the returned state to `WORKFLOW_STATE.json`, and commit it
     **alone** — stage exactly that one path (never a broader `git add`;
     **Item-scoped staging** (workflow-2.8.0, `LPR-R6-001`): stage the state file with `workflow_state.stage_scoped_state(repo_root, <work_item_id>)` in place of the bare `git add` of that path (it returns `False`, and the ordinary single-path `git add` runs, unless another work item holds uncommitted residue in the state file).)
     and create one commit carrying `Workflow-Bundle-Generation-Record:
     <work_item_id>/<implementation_revision>` +
     `Workflow-Work-Item: <work_item_id>` trailers, no other trailer.
     **These two trailer lines must be the commit message's own final
     paragraph** -- after any `Co-Authored-By:`/`Claude-Session:` lines,
     never before them (`OPUS-R129-001`, the same rule step 1f above,
     `bootstrap-workflow-v2.md` step 6, `approve-review.md` step 6.4, and
     `accept-milestone.md` step 6 already state): Git's `git
     interpret-trailers --parse`, the exact mechanism
     `discover_current_bundle_generation_record_commit` uses, treats only
     the message's last paragraph as trailers, so a blank line after these
     two lines (e.g. one followed by `Co-Authored-By:`) silently discards
     both and makes the bundle-generation-record commit undiscoverable.
     This durability commit must land *before* generation, never after
     (`WF8B-003`, resolved `D-Approval-Commits` revision 28): a durability
     commit made after generation is by definition one commit ahead of the
     value it just wrote, permanently re-breaking
     `/approve-review implementation`'s provenance-interval check on every
     round. Skip this whole step for a work item with no state entry
     (nothing to track);
   - write `<bundle_dir>/IMPLEMENTATION_SUMMARY.md` (what was built,
     per checkpoint, and why). It must state
     `implementation_revision: <N>` as a plain labelled line, `N` equal
     to the counter `record_bundle_generation` just wrote -- this is a
     hard generator precondition (`assert_stage_completeness`, run from
     `finalize_bundle_generation`), and a missing or stale line does not
     warn: it *withdraws* the bundle, quarantining `current/` and
     deleting the archive;
   - write `<bundle_dir>/TEST_RESULTS.md` (exact commands + results);
   - write `<bundle_dir>/CONTEXT_FILES.txt` with only the unchanged
     docs a reviewer needs;
   - write `<bundle_dir>/REVIEW_REQUEST.md` per
     `docs/ai-workflow/REVIEW_PROTOCOL.md` (stage: `implementation`),
     whose `review_content_id: <hex>` line is obtained from the single
     canonical entry point that document's "Computing `review_content_id`"
     names for this stage -- never a second, ad hoc computation. If the implementation stage's gate is `automatic` and the effective `require` lists
     `distinct_reviewer_models` (`workflow_state.review_stage_gate_context(repo_root,
     state, work_item_id, "implementation")["requires_distinct"]`), also ask the reviewer, in
     that file, to state `Reviewer model: <vendor>/<model>` in the verdict's header
     block, because an `APPROVE` without it is refused at ingest (workflow-2.8.0,
     `LPR-R16-003`); under a human gate ask for nothing new.
   - run `./scripts/prepare-ai-review.sh <base-sha> implementation
     [work_item_id]`, where `<base-sha>` is the milestone's starting
     commit; `<bundle_dir>` here resolves per
     `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
     (`workflow_fingerprint.resolve_bundle_dir`). Bundle files themselves
     are never committed (gitignored, disposable, regeneratable) — only
     the generation-record commit above is real Git history.
5. Report the bundle location and **stop**. This is a hard gate — do not
   mark the milestone accepted, do not start the next milestone.
