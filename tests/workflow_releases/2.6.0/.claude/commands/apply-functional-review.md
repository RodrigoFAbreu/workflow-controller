---
description: Classify and fix user functional-testing findings, then return to the functional-review gate.
argument-hint: "[work-item-id]"
state_writer: true
review-subject: bundle
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `FIXING_FUNCTIONAL_FINDINGS` state of
`docs/ai-workflow/MILESTONE_WORKFLOW.md`.

`<bundle_dir>`/`<feedback_dir>` below resolve per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir`). The
only generation this command drives is the bounded branch's `post-fix`
round, so `<bundle_dir>` is resolved with **no** `stage` argument --
`resolve_bundle_dir(repo_root, work_item_id)`, the scoped-else-flat
compatibility rule -- exactly as `/milestone-implement` and
`/apply-implementation-review` resolve it for the same two stages, never
the plan stage's `stage="plan"` form.
`resolve_feedback_dir` decides `<feedback_dir>` by the item's durable
`feedback_layout` (`D-Feedback-Layout`, workflow-2.6.0):
`.ai-review/<work_item_id>/feedback/` unconditionally, by construction, for
a `feedback_layout: "scoped"` item; the unchanged legacy scoped-else-flat
rule for an item without the field.

0. **Dual-mode branch** (Workflow v2.1, `WF4c`, `D-Functional-Remediation`):
   resolve the target work item — the id named in `$ARGUMENTS`, or
   `active_work_item_id` from `docs/ai-workflow/WORKFLOW_STATE.json` if
   omitted (refuse with a named error if neither resolves to an existing,
   non-terminal `work_items` entry — never guess).
   - **No `docs/ai-workflow/WORKFLOW_STATE.json` entry exists** for this
     work item (an ordinary `"1"` item never routed through
     `workflow_state.route_work_item`): steps 1-7 below execute exactly as
     written, with no state reads or writes — this branch is inert by
     construction.
   - **An entry exists** (either governing version — `D-Functional-
     Remediation` applies uniformly, it is not a `"2.1"`-only mechanism):
     also read `docs/ai-workflow/WORKFLOW_CONFIG.json`
     (`workflow_state.load_config`). Step 4 is replaced by the per-finding
     three-way branch below; steps 1-3 and 5-7 execute unchanged.

1. Read `<feedback_dir>/FUNCTIONAL_REVIEW.md`. If it does not exist,
   stop and say so, printing the exact resolved path
   (`resolve_feedback_dir(repo_root, work_item_id)`, `D-Feedback-Layout`,
   workflow-2.6.0) where the user's findings must be placed, never a
   hard-coded flat path. **Already-applied refusal** (O3,
   `workflow-v2-3-followups` continued scope): call
   `workflow_fingerprint.assert_functional_review_not_already_consumed(
   repo_root, work_item_id)` — a `FunctionalReviewAlreadyAppliedError`
   means this exact content was already classified and acted on by a
   prior round; stop and say so rather than re-processing findings that
   already have a disposition. This is what keeps a `FUNCTIONAL_REVIEW.md`
   left in place after a bounded fix from being misread as fresh the next
   time this item reaches `AWAITING_FUNCTIONAL_REVIEW`.
2. Classify each finding as one of: **defect**, **usability issue**,
   **missing requirement**, **enhancement**, or **expected behavior**. State
   the classification and reasoning for each.
3. Reproduce each defect/usability finding where practical before fixing it.
4. Fix defects, usability issues, and missing requirements. Add a regression
   test for each defect. Do not silently implement enhancements — flag them
   for the user to prioritize instead, unless trivially in-scope.

**[state-tracked items only] Step 4, replaced — the three-way branch**
(`D-Functional-Remediation`): decide, *before* touching any source/test
file, which branch each defect/usability-issue/missing-requirement finding
falls into. The three branches are per-finding-group, not mutually
exclusive within one round — a single round can resolve some findings as
"no code change," fix others as "bounded," and defer the rest as "broad,"
all in the same invocation.

- **No code change**: the finding needs no source/test edit at all (e.g.
  resolved by a narrative-only checklist correction, or turns out on
  reproduction to already be correct). `technical_approval` is left
  completely untouched — there is nothing to stale. Proceed straight to
  step 5 for this finding.

- **Bounded code change**: a normal, contained fix — comparable in size to
  an ordinary implementation-review remediation round.
  1. **Stale-before-edit ordering, hard requirement**: before the first
     source/test edit for this finding lands, call
     `workflow_state.mark_technical_approval_stale(state, work_item_id,
     now=<now>)` and persist the result to
     `docs/ai-workflow/WORKFLOW_STATE.json` immediately — on disk before
     any edit, not merely decided — so an interrupted session still shows
     `STALE` rather than a `CURRENT` record whose reviewed content no
     longer matches the working tree. If an edit already landed before
     this call ran, that is a process violation: make the stale write
     now, before continuing, rather than backfilling it after the fact.
  2. Make the fix (step 4's normal work: regression test included).
  3. Commit the fix (one coherent commit; do not bundle it with an
     unrelated finding's fix).
  4. Regenerate the bundle at the `post-fix` stage. **`REJECTED`-bundle
     refusal, this branch's sole assertion, immediately preceding
     `record_bundle_generation`** (`WFR-67`, one of the three named writer
     call sites; this branch is a consumer scoped by act, since it creates
     the refused bundle rather than receiving one): call
     `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
     work_item_id)` here, before the precondition call below. **First**,
     before writing any bundle file, call `workflow_state.resolve_bundle_generation_outcome(
     repo_root, work_item, base_commit=<base-sha>, head=<current HEAD SHA>)`
     (WF8c (c), D-Commit-Provenance "Same-content post-fix republication")
     to learn which of the two legal outcomes applies — `("ordinary", None)`
     when the fix genuinely changed protected implementation-stage content,
     `("same_content", t)` when the fix nets out byte-identical to the
     currently-reviewed round (e.g. reverted before publication). Then call
     `workflow_state.record_bundle_generation(state, work_item_id,
     stage="post-fix", head=<current HEAD SHA>, now=<now>, outcome=<the
     resolved outcome>)` — this is the step that writes the new
     `reviewed_implementation_head` for the `"ordinary"` outcome
     (D-Approval-Commits' sole writer; left unchanged for `"same_content"`).
     **Legal from `AWAITING_FUNCTIONAL_REVIEW` specifically because of
     step 1's stale-before-edit write** (self-discovered during
     `workflow-v2-3-followups`'s own `/accept-milestone` pre-flight,
     closed as continued scope):
     `BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE["post-fix"]` includes
     `AWAITING_FUNCTIONAL_REVIEW`, and `record_bundle_generation` itself
     additionally verifies `technical_approval.status == "STALE"` for that
     phase — refusing with `BundleGenerationRequiresStaleTechnicalApprovalError`
     if step 1's write never landed. Never invoke this call with a
     `CURRENT` `technical_approval` still in place. Nothing else makes
     `/approve-review implementation` reachable again. **workflow-2.5.0
     (resolves I2)**: for a `"2.2"` item, this call's own target phase is
     `bundle_generation_target_phase("post-fix", "2.2")` ==
     `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, never straight back to the
     terminal `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` -- a `"2.2"`
     functional-review bounded fix re-enters both implementation-review
     stages (`/review-implementation` then
     `/record-manual-implementation-review`) before
     `/approve-review implementation` is reachable again, exactly as an
     ordinary post-fix round does for it. For `"1"`/`"2.1"`, the target
     phase is `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, byte-identical to
     before this checkpoint. Persist the returned state to
     `WORKFLOW_STATE.json`, and commit it
     **alone**: stage exactly that one path (never a broader `git add`) and
     create one commit carrying, for `"ordinary"`,
     `Workflow-Bundle-Generation-Record: <work_item_id>/<implementation_revision>`
     + `Workflow-Work-Item: <work_item_id>` trailers, no other trailer; for
     `"same_content"`, that same `Workflow-Bundle-Generation-Record` value
     (unchanged) + `Workflow-Work-Item: <work_item_id>` +
     `Workflow-Supersedes: <t>`, no other trailer. **These trailer lines
     must be the commit message's own final paragraph** — after any
     `Co-Authored-By:`/`Claude-Session:` lines, never before them
     (`OPUS-R129-001`, the same rule `milestone-implement.md` step 1f,
     `bootstrap-workflow-v2.md` step 6, `approve-review.md` step 6.4, and
     `accept-milestone.md` step 6 already state): Git's `git
     interpret-trailers --parse`, the exact mechanism
     `discover_current_bundle_generation_record_commit` uses, treats only
     the message's last paragraph as trailers, so a blank line after these
     lines (e.g. one followed by `Co-Authored-By:`) silently discards them
     and makes the bundle-generation-record commit undiscoverable. This
     durability commit must land *before* generation, never after
     (`WF8B-003`, resolved `D-Approval-Commits` revision 28) — a
     durability commit made after generation is by definition one commit
     ahead of the value it just wrote, permanently re-breaking
     `/approve-review implementation`'s provenance-interval check on every
     round.
     **Then refresh `<bundle_dir>/IMPLEMENTATION_SUMMARY.md`'s own
     `implementation_revision: <N>` line** to whatever the counter now
     reads — advanced by one for `"ordinary"`, deliberately *unchanged*
     for `"same_content"`, whose whole purpose is to pin the round while
     the generation head moves. This is a hard generator precondition
     (`assert_stage_completeness`, run from `finalize_bundle_generation`);
     a line left at the previous round's value does not warn, it
     *withdraws* the bundle, quarantining `current/` and deleting the
     archive. Refresh `<bundle_dir>/REVIEW_REQUEST.md`'s own
     `review_content_id: <hex>` line for this round too
     (`assert_review_request_states_review_content_id`), obtained from the
     single canonical entry point `docs/ai-workflow/REVIEW_PROTOCOL.md`'s
     "Computing `review_content_id`" names for this stage -- never a
     second, ad hoc computation: an `"ordinary"`
     round's fix changed protected implementation-stage content, so the
     digest moved with it, while a `"same_content"` round recomputes the
     same value the file already states. That check runs earlier still,
     from `--write-manifest`, and *refuses* the whole generation (nothing
     published, nothing withdrawn) rather than correcting the stale line.
     Neither input is ever silently fixed up on the author's behalf: a
     bounded fix regenerated without refreshing either one stops at
     `ReviewContentIdMismatchError` first and, once only that is
     corrected, at the `assert_stage_completeness` withdrawal above. Then
     run `./scripts/prepare-ai-review.sh <base-sha> post-fix
     [work_item_id]`.
  5. **Mark this round's `FUNCTIONAL_REVIEW.md` consumed** (O3): call
     `workflow_fingerprint.mark_functional_review_consumed(repo_root,
     work_item_id)` — every finding this round classified now has a
     disposition (fixed here, or already handled by an earlier "no code
     change"/"broad" branch in this same invocation), so this exact
     content must never be re-read as fresh on a later pass. This finding
     now requires a fresh implementation-review round: report readiness
     and **stop** — this re-enters
     `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` exactly like an ordinary
     post-fix round (`/apply-implementation-review` drives it from here).
     A fresh `/approve-review implementation` must succeed before
     `AWAITING_FUNCTIONAL_REVIEW` is reachable again for this item. Do not
     proceed to step 6/7 in this same invocation once any finding takes
     this branch — the stop is for the whole round, not just this finding.

- **Broad/multi-finding remediation**: the fix spans substantially more
  work than a single contained round — multiple checkpoints' worth, work
  that cuts across unrelated areas, or genuinely uncertain in scope. Do
  not guess past the point of confidence; a genuinely ambiguous case is
  treated as broad, letting the child item's own planning stage resolve
  the scope properly rather than under-scoping a fix here.
  1. Do not edit source/test files for this finding in the parent's
     working tree at all — broad remediation happens entirely inside the
     child work item, planned and reviewed on its own.
  2. Call `workflow_state.create_remediation_child_work_item(state,
     config, parent_work_item_id=<this item's id>, plan_path=<a fresh
     plan-doc path following this item's own naming convention, e.g.
     `docs/milestones/<child-id>-execution.md` for a product item>,
     registry_path="docs/ai-workflow/registry/<child-id>-registry.json",
     base_commit=<current HEAD>, now=<now>)` and persist the returned
     state. The child id is derived deterministically
     (`<parent-id>-remediation-<n>`, never caller-numbered) — report it
     back verbatim. This call never touches the parent's own
     `registry_path`/`plan_path` files or `active_work_item_id`.
  3. Note the deferral in the functional-review checklist (step 6): which
     findings, and the exact child work-item id they were routed to —
     this is what satisfies "addressed or explicitly deferred with
     rationale" for these specific findings.
  4. Tell the user the next action for that scope is `/milestone-plan
     <child-id>`, which drafts and routes the remediation plan through the
     full independent review cycle — never implemented inline in this
     command. The parent's own registry, mapping, and completed-checkpoint
     history are never touched by this branch.

     **Name the child id on every command in that cycle, not just the
     first.** `create_remediation_child_work_item` deliberately does not
     repoint `active_work_item_id` (the parent keeps it), so every command
     that defaults to the active item would otherwise drive the *parent*.
     The sanctioned child sequence is therefore, in full:
     `/milestone-plan <child-id>` → `/review-plan <child-id>` →
     `/record-manual-plan-review <child-id>` (a `"1"`-governed child uses
     `/apply-plan-review <child-id>` instead of those two) →
     `/approve-review plan <child-id>` → `/milestone-implement <child-id>`
     (× N) → `/review-implementation <child-id>` (optional for a `"1"`/
     `"2.1"` child; a `"2.2"`-governed child instead uses it as the
     authoritative `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage writer,
     `workflow-2.5.0`) → `/record-manual-implementation-review <child-id>`
     (a `"2.2"`-governed child only, mirroring the plan side's own
     `/record-manual-plan-review`) →
     `/approve-review implementation <child-id>` →
     `/prepare-functional-review <child-id>` → `/review-functional
     <child-id>` (optional) → `/apply-functional-review <child-id>` (only
     if the child's own functional pass produces findings; its own three
     branches apply recursively) → `/accept-milestone <child-id>`. A
     `REVISE` at either review stage diverts through `/apply-plan-review
     <child-id>` or `/apply-implementation-review <child-id>`
     respectively, and `/recover-implementation-provenance <child-id>`
     repairs a stale generation head without starting a new round. The child enters review at
     `AWAITING_LOCAL_PLAN_REVIEW` when its
     `governing_workflow_version` is `"2.1"` (the config default at
     creation) and at `AWAITING_EXTERNAL_PLAN_REVIEW` when it is `"1"` --
     the ordinary per-version writers, not a special remediation rule: for
     a `"1"` child, `publish_plan_revision`'s `"1"` branch writes
     `AWAITING_EXTERNAL_PLAN_REVIEW`; for a two-stage child,
     `publish_plan_revision` is mirror-only and `/milestone-plan <child-id>`
     step 6's `bind_plan_review_bundle` writes `AWAITING_LOCAL_PLAN_REVIEW`
     once the child's bundle verifies (`D-Plan-Review-Bundle-Binding`,
     workflow-2.6.0, `LPR-R1-006`). **workflow-2.5.0**: a `"2.2"` child
     (the config default once a repository has activated `"2.2"`) enters
     review at `AWAITING_LOCAL_PLAN_REVIEW` identically to a `"2.1"` child
     -- the same `TWO_STAGE_PLAN_REVIEW_VERSIONS` publish-then-bind path
     covers both, not a third one. Only
     `/accept-milestone <child-id>` clears the
     parent's `IncompleteChildWorkItemError` block, so the parent cannot
     complete until the child does.

     **`workflow-2.4.0` addendum: the child may amend its own plan too.**
     A remediation child is a work item like any other, so once it reaches
     `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` it may request its own
     plan amendment on exactly the terms `D-Plan-Amendment-1`/`-2` set for
     any work item: `/request-plan-amendment <child-id>` followed by
     `/milestone-plan <child-id>`, re-entering the sanctioned sequence
     above at its own `/milestone-plan <child-id>` step. This is not a
     remediation-specific mechanism and grants the child no authority its
     parent lacks.

5. Commit coherent fixes for any "no code change"/narrative-only findings
   resolved in step 4 (the bounded branch already committed its own fix
   above; a round that is entirely bounded- or broad-branch findings may
   have nothing left to commit here).
6. Update the functional-review checklist in `docs/ACTIVE_MILESTONE.md` to
   reflect what changed, what was deferred to a remediation child (name the
   child work-item id), and what still needs re-testing.
7. **Mark this round's `FUNCTIONAL_REVIEW.md` consumed** (O3): call
   `workflow_fingerprint.mark_functional_review_consumed(repo_root,
   work_item_id)` — every finding this round classified now has a
   disposition ("no code change", fixed inline, or deferred to a
   remediation child), so this exact content must never be re-read as
   fresh on a later pass. Report and return to `AWAITING_FUNCTIONAL_REVIEW`
   — stop for the user to re-test, unless the user explicitly waives
   another round. If any finding this round took the bounded branch, that
   stop already happened above instead (a fresh implementation-review
   round is required first); do not report both stops as satisfied by the
   same invocation.
