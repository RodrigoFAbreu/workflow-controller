---
description: Apply external implementation-review feedback and prepare for another review round if needed.
argument-hint: "[work-item-id]"
state_writer: true
review-subject: verdict
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `APPLYING_REVIEW_FEEDBACK` state of
`docs/ai-workflow/MILESTONE_WORKFLOW.md` by calling
`workflow_state.state_transaction(repo_root, lambda state:
workflow_state.enter_applying_review_feedback(state, work_item_id, now=<now>))`
(OPUS-R101-001: this state's real, durable writer -- refuses outright,
naming the actual phase, unless the work item's current phase is
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`) and persisting the returned
state. Skip this call, and stay silent about phase, for a work item with
no `docs/ai-workflow/WORKFLOW_STATE.json` entry. **workflow-2.5.0 (I3,
round 4): the skip is conditional on the item's own current `phase`, never
on its `governing_workflow_version`** -- skip this call whenever `phase`
already equals `APPLYING_REVIEW_FEEDBACK` (a version-independent no-op
guard), and otherwise call it exactly as stated above, even for a `"2.2"`
item. For the normal `"2.2"` two-stage loop this is always the skip case:
`enter_applying_review_feedback`'s own only legal source phase is
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, which is never where a `"2.2"`
item sits when this command is invoked for a `REVISE`/`BLOCK`-turned-`REVISE`
round from either two-stage writer -- it arrives at
`APPLYING_REVIEW_FEEDBACK` already, written directly by
`/review-implementation`'s or `/record-manual-implementation-review`'s own
`REVISE` branch (`record_local_implementation_review`/
`record_manual_implementation_review`, mirroring the plan side's own
`REVISING_PLAN` write), so the call is skipped exactly as a version-keyed
check would also have skipped it. But a `"2.2"` item that has instead
reached the *terminal* `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` phase
(both implementation-review stages already `APPROVE`d, then a late problem
surfaces and a fix is committed after `/approve-review implementation`
closed the gate) sits at exactly the phase `enter_applying_review_feedback`'s
own guard names as legal -- a version-keyed skip stranded that item with no
in-band way back into `APPLYING_REVIEW_FEEDBACK`, even though
`MILESTONE_WORKFLOW.md`'s own unqualified "Exit" line for that phase
documents exactly this path. The phase-conditional check fires the call for
that item instead, restoring `APPLYING_REVIEW_FEEDBACK` and closing the
wedge. This command's own step 0 branch below states this explicitly; its
existing `"1"`/`"2.1"` step-0 dual-mode enumeration stays byte-unchanged,
correctly (`LOCAL_MODEL_PLAN_REVIEW` round 8, optional finding 3): steps 1-8
already run identically for `"1"`/`"2.1"` regardless of the two-stage
*plan*-review protocol, and that stays true verbatim -- the only thing this
checkpoint widens is which phase this command's own
`APPLYING_REVIEW_FEEDBACK` entry line above tolerates finding it already in.

`<bundle_dir>`/`<feedback_dir>` below resolve per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir`).
`resolve_feedback_dir` decides `<feedback_dir>` by the item's durable
`feedback_layout` (`D-Feedback-Layout`, workflow-2.6.0):
`.ai-review/<work_item_id>/feedback/` unconditionally, by construction, for
a `feedback_layout: "scoped"` item; the unchanged legacy scoped-else-flat
rule for an item without the field.

0. **Dual-mode branch** (Workflow v2.1, `WF4a-ii`): resolve the target work
   item first -- the id named in `$ARGUMENTS`, or `active_work_item_id` from
   `docs/ai-workflow/WORKFLOW_STATE.json` if omitted. Refuse with a named
   error if neither resolves to an existing, non-terminal `work_items`
   entry -- never guess. The explicit id is what makes a work item that is
   *not* `active_work_item_id` drivable at all (`D1`: the pointer is resume
   focus, not an execution lock), which is the only way a
   `<parent-id>-remediation-<n>` child's own `REVISE` round can be applied
   while its parent holds the pointer. Then read that work item's
   `governing_workflow_version` from
   `docs/ai-workflow/WORKFLOW_STATE.json`. Both `"1"` and `"2.1"` items run
   steps 1-8 identically — this command's exit target
   (`AWAITING_TECHNICAL_APPROVAL`) is amended only in its naming, per
   `D-Self-Governance`; there is no other version-specific behavior here.
   This step exists so the command's own dual-mode structure is explicit
   and testable per that enumeration. **workflow-2.5.0 addition, round-7
   optional finding 2**: a `"2.2"` item also runs steps 1-8 identically to
   the above -- the only two things this milestone changes for it are
   stated at the top of this file (`enter_applying_review_feedback`'s call
   being conditional on the item's actual `phase` rather than its version,
   per `I3` above -- in the normal two-stage loop this still means no call,
   as before; step 7's post-fix regeneration resolves to
   `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` via `record_bundle_generation`'s
   own version-dependent `bundle_generation_target_phase` resolver, not a
   second writer) -- neither changes steps 1-8's own text. Any
   `governing_workflow_version` other than `"1"`, `"2.1"`, `"2.2"`, or
   absent (no `WORKFLOW_STATE.json` entry at all): refuse cleanly, naming
   the actual value -- never guess which of the branches above applies. In
   practice this refusal branch is unreachable today, since every version
   this module accepts already runs steps 1-8 identically above; it exists
   for the same defensive-enumeration reason `/milestone-implement`'s and
   `/review-implementation`'s own step 0 state it.
1. Read `<feedback_dir>/REVIEW_FEEDBACK.md`. If it does not exist, stop
   and say so, printing the exact resolved path
   (`resolve_feedback_dir(repo_root, work_item_id)`, `D-Feedback-Layout`,
   workflow-2.6.0) where the reviewer's feedback must be placed, never a
   hard-coded flat path. Validate its binding with
   `workflow_state.assert_apply_review_feedback_binding(repo_root,
   work_item, work_item_id, stage="implementation", feedback_content=<the
   file's content>)` (`D-Apply-Binding`, workflow-2.7.0; `WFR-03`), which
   computes the current `bundle_id` itself. For every `"1"`/`"2.1"` item,
   and every verdict that is not a `"2.2"` `REVISE` stating a
   `review_content_id`, it is 2.6.0's bundle binding
   (`assert_feedback_matches_bundle` against the current recomputed
   `bundle_id`/`base_commit`/`work_item_id`), unchanged; so it is for a
   bundle whose `MANIFEST.md` names no `work_item_id`. A `"2.2"` `REVISE`
   that states a `review_content_id`, at `APPLYING_REVIEW_FEEDBACK`, is
   bound by content: its `review_content_id` must be the one the reviewed
   bundle's own `MANIFEST.md` records (not the moving current one -- a fix
   you have already committed moves it), and the manifest must name this
   item, its `base_commit` and the implementation stage; its
   `Reviewed bundle ID:`/`Reviewed base commit:` are advisory -- report
   the returned `advisory`, if any. Report a refusal by class and stop:
   `ReviewBundleManifestMismatchError` or `MissingRequiredBundleFileError`
   (the bundle on disk is not this item's reviewed bundle -- including
   another item's bundle in the shared flat `.ai-review/current/`: run
   from the worktree that holds it, or restore it; never regenerate it, a
   post-fix generation is this round's exit), `FeedbackContentMismatchError`,
   `MissingFeedbackBindingFieldError` or `FeedbackBundleMismatchError`
   (the verdict is not for the bundle being applied) -- stale or
   mismatched feedback is a reason to stop and say so, not to apply.
   Never edit a verdict's binding fields to make it bind: they attest to
   what its reviewer reviewed. The `BLOCK` pin below records the
   returned `bundle_id`.
   **`REJECTED`-bundle refusal, first of two** (`WFR-67`): also call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here; a `BundleRejectedError` stops the command, naming
   the marker path and its recorded detail.
   **Durable `BLOCK`-verdict pin** (`D2a`, `WF8c` item (a)): once the
   feedback is confirmed current, bundle-matching, and parse-valid, and
   before taking any other action, check its `status` field. If it is
   exactly `BLOCK` and this work item has a
   `docs/ai-workflow/WORKFLOW_STATE.json` entry: call
   `workflow_state.state_transaction(repo_root, lambda state:
   workflow_state.record_technical_review_block_pin(state, work_item_id,
   bundle_id=<the just-recomputed current bundle_id>,
   review_content_id=<the just-recomputed current implementation-stage
   review_content_id>, now=<now>))` and persist the returned state. This
   is idempotent — a pin already present for the exact `bundle_id` is
   returned unchanged (compare the result to the state read immediately
   before this call; if identical, no new commit is needed for this
   repeat observation). A genuine new pin is committed alone (stage
   exactly `docs/ai-workflow/WORKFLOW_STATE.json`, never a broader `git
   add`), carrying no special trailer beyond the ordinary
   `Workflow-Work-Item: <work_item_id>` — the same small,
   `WORKFLOW_STATE.json`-only durability shape every other small write in
   this design uses. This step never blocks the remediation steps below —
   pinning durably records the `BLOCK` observation so it can never later
   become override-eligible by an edit to the mutable feedback file; it
   does not change how Blocking/Important findings are triaged.
2. Reproduce and validate every Blocking and Important finding against the
   actual code/tests before changing anything. Do not apply a finding you
   cannot reproduce or verify — reject it with evidence instead.
3. Fix all validated Blocking and Important findings. Consider Optional
   findings and Missing Tests findings; apply them if cheap and low-risk,
   otherwise note why not.
4. For any rejected finding, record the rejection with concrete evidence
   (file/line/test/doc reference) in `<bundle_dir>/IMPLEMENTATION_SUMMARY.md`.
5. Rerun the relevant narrow tests for each fix, then the full suite used in
   `/milestone-implement` step 3 before closing this pass.
6. Commit coherent fixes (one commit per coherent fix, not one giant
   catch-all commit).
7. Regenerate the bundle at the `post-fix` stage. If this work item has a
   `docs/ai-workflow/WORKFLOW_STATE.json` entry: **`REJECTED`-bundle
   refusal, second of two, under this step's own mutation guard,
   immediately preceding `record_bundle_generation`** (`WFR-67`, one of
   the three named writer call sites): re-call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here, before the precondition call below — a withdrawal
   landing between step 1 and here must still be caught. **First**, before
   writing any bundle file, call `workflow_state.resolve_bundle_generation_outcome(
   repo_root, work_item, base_commit=<base-sha>, head=<current HEAD SHA>)`
   (WF8c (c), D-Commit-Provenance "Same-content post-fix republication") to
   learn which of the two legal outcomes applies — `("ordinary", None)` when
   the protected implementation-stage content genuinely changed this round,
   `("same_content", t)` when every finding was resolved via rejection-with-
   evidence or excluded-only content and the protected content nets out
   byte-identical to the currently-reviewed round. Then call
   `workflow_state.record_bundle_generation(state, work_item_id,
   stage="post-fix", head=<current HEAD SHA>, now=<now>, outcome=<the
   resolved outcome>)` (`WF4c`, D-Approval-Commits' sole writer of
   `reviewed_implementation_head`) -- **workflow-2.5.0**: this call's own
   target phase is `bundle_generation_target_phase("post-fix",
   governing_workflow_version)`, `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
   for `"1"`/`"2.1"` (byte-identical to before this checkpoint) and
   `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` for `"2.2"` -- a `"2.2"` item's
   post-fix regeneration re-enters local review, never going straight back
   to the terminal phase, so both implementation-review stages run again
   before `/approve-review implementation` is reachable. Persist the
   returned state to `WORKFLOW_STATE.json`, and commit it **alone** —
   stage exactly that one
   path (never a broader `git add`; **Item-scoped staging** (workflow-2.8.0, `LPR-R6-001`): stage the state file with `workflow_state.stage_scoped_state(repo_root, <work_item_id>)` in place of the bare `git add` of that path (it returns `False`, and the ordinary single-path `git add` runs, unless another work item holds uncommitted residue in the state file).) and create one commit carrying, for
   `"ordinary"`, `Workflow-Bundle-Generation-Record:
   <work_item_id>/<implementation_revision>` +
   `Workflow-Work-Item: <work_item_id>` trailers, no other trailer; for
   `"same_content"`, that same `Workflow-Bundle-Generation-Record` value
   (unchanged, never bumped) + `Workflow-Work-Item: <work_item_id>` +
   `Workflow-Supersedes: <t>` (`t` the commit `resolve_bundle_generation_outcome`
   returned), no other trailer. **These trailer lines must be the commit
   message's own final paragraph** — after any `Co-Authored-By:`/
   `Claude-Session:` lines, never before them (`OPUS-R129-001`, the same
   rule `milestone-implement.md` step 1f, `bootstrap-workflow-v2.md` step
   6, `approve-review.md` step 6.4, and `accept-milestone.md` step 6
   already state): Git's `git interpret-trailers --parse`, the exact
   mechanism `discover_current_bundle_generation_record_commit` uses,
   treats only the message's last paragraph as trailers, so a blank line
   after these lines (e.g. one followed by `Co-Authored-By:`) silently
   discards them and makes the bundle-generation-record commit
   undiscoverable. This durability commit must land *before* generation,
   never after (`WF8B-003`, resolved `D-Approval-Commits` revision 28) — a
   durability commit made after generation is by definition one commit
   ahead of the value it just wrote, permanently re-breaking
   `/approve-review implementation`'s provenance-interval check on every
   round; skip this whole step for a work item with no state entry.
   **Then refresh `<bundle_dir>/IMPLEMENTATION_SUMMARY.md`'s own
   `implementation_revision: <N>` line** to whatever the counter now
   reads -- advanced by one for `"ordinary"`, deliberately *unchanged*
   for `"same_content"`. This is a hard generator precondition
   (`assert_stage_completeness`, run from `finalize_bundle_generation`);
   a line left at the previous round's value does not warn, it
   *withdraws* the bundle, quarantining `current/` and deleting the
   archive. Refresh `<bundle_dir>/REVIEW_REQUEST.md`'s own
   `review_content_id: <hex>` line for this round too
   (`assert_review_request_states_review_content_id`), obtained from the
   single canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
   "Computing `review_content_id`" names for this stage -- never a second,
   ad hoc computation. If the implementation stage's gate is `automatic` and the effective `require` lists
   `distinct_reviewer_models` (`workflow_state.review_stage_gate_context(repo_root,
   state, work_item_id, "implementation")["requires_distinct"]`), also ask the reviewer, in
   that file, to state `Reviewer model: <vendor>/<model>` in the verdict's header
   block, because an `APPROVE` without it is refused at ingest (workflow-2.8.0,
   `LPR-R16-003`); under a human gate ask for nothing new. Then
   run `./scripts/prepare-ai-review.sh <base-sha> post-fix [work_item_id]`
   — required before `AWAITING_TECHNICAL_APPROVAL` can be reachable again.
8. If any Blocking finding remains unresolved, or the fix was structurally
   significant, stay in `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` and stop
   for another review round. Otherwise report readiness and that
   `AWAITING_TECHNICAL_APPROVAL` is the next state, and stop — do not
   auto-run `/approve-review` or `/prepare-functional-review`. Only the user
   invokes `/approve-review implementation`; let the user decide when to
   proceed.
