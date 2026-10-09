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
`resolve_feedback_dir` decides `<feedback_dir>` by the item's durable
`feedback_layout` (`D-Feedback-Layout`, workflow-2.6.0):
`.ai-review/<work_item_id>/feedback/` unconditionally, by construction, for
a `feedback_layout: "scoped"` item; the unchanged legacy scoped-else-flat
rule for an item without the field.

Steps 2-7 below are **one call**,
`workflow_state.ingest_manual_review_verdict(repo_root, work_item_id,
stage="implementation", verdict_text=<the pasted file's text>, now=<now>,
two_stage_only=True, run_ref=<`session:local` plus the resolved paste path>)`
(workflow-2.7.0, `D-OP-External`; `run_ref` workflow-2.8.0): the same ingest the
orchestration protocol's `record-external-result` calls, so there is one
ingest and no duplicated guard sequence. The steps document what it does,
in its order; the guard order itself is documented once, in the function.
It holds `workflow_state.state_lock` from step 2's guard through step 7's
publication: the row selection, every guard and the pure
`record_manual_implementation_review` call run on the state re-read under
the lock, the pasted file is rewritten atomically (identical bytes, the
ordinary case here, are a no-op) in `state_transaction`'s `before_publish`
hook, and the state is then published by `state_transaction` itself. A
second, concurrent ingest therefore re-reads the state this one published
and refuses before writing anything.

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`). Refuse with a named error if
   neither resolves to an existing `work_items` entry -- never guess.
2. **Governing-version guard** (`two_stage_only=True`): if the resolved
   item's `governing_workflow_version` is not exactly `"2.2"`, refuse
   cleanly, naming the actual version — unlike the plan-review protocol
   (valid for both `"2.1"`/`"2.2"`), the two-stage *implementation*-review
   protocol is `"2.2"`-only
   (`WrongGoverningVersionForImplementationReviewStageError`). A `"1"` item
   has no local-review stage to run at all; a `"2.1"` item's implementation
   stage stays single-stage — use the existing
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` flow for either instead (its
   verdict is a feedback-only row of `record-external-result`, never this
   command).
3. **Phase guard** (the ingest's row selection): if the item's `phase` is
   not `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, refuse cleanly,
   naming the actual phase — including "already ingested this round" and
   "no local `APPROVE` on record"
   (`WrongPhaseForImplementationReviewStageError`).
4. **Read**: `<feedback_dir>/REVIEW_FEEDBACK.md`, whose text is the
   ingest's `verdict_text`.
   **Resolved paste path, printed** (`D-Feedback-Layout`, workflow-2.6.0):
   `<feedback_dir>` is `workflow_fingerprint.resolve_feedback_dir(repo_root,
   work_item_id)` (equivalently the `review_feedback_path` field of
   `python3 scripts/workflow_fingerprint.py --resolve-feedback-path
   <work_item_id>`); if no `REVIEW_FEEDBACK.md` sits there, stop and print
   that exact resolved path as the one the user must paste into -- never a
   hard-coded flat `.ai-review/feedback/` path.
   **Required header fields** (`ManualVerdictHeaderError` otherwise, nothing
   written): `Status:`, `Reviewer role: MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
   — there is no legacy-cased alias to accept here: unlike
   `plan_review_stages`, the `implementation_review_stages` ledger is
   introduced fresh at `"2.2"` with no pre-`SCREAMING_SNAKE_CASE` history
   behind it, so a role string that does not match this exact spelling is
   refused outright, never silently normalized — and the reviewed content
   as `Reviewed review_content_id: <hex>`, the pinned label (workflow-2.7.0,
   `v2.6.0-002`; the legacy alias `Reviewed review content ID:` is still
   accepted). Tell the reviewer that these header fields come before the
   first `## ` section that follows them, since the `review_content_id` is
   read from the header block only. `Work item:`, `Reviewed bundle ID:`,
   `Reviewed base commit:` and `Round:` are optional; a `Round:` that is
   present must be a positive integer.
   **Foreign-`Work item:` refusal, before any write**:
   `workflow_fingerprint.assert_manual_feedback_names_work_item` -- a pasted
   file whose `Work item:` field is present and names a different work
   item stops the command (`ManualFeedbackForeignWorkItemError`, naming
   both ids); a file without that field is not refused here, since step
   6's hard `review_content_id` check still binds it.
5. **Recompute fresh**: the current `bundle_id` and implementation-stage
   `review_content_id`, identical in mechanism to `/review-implementation`'s
   own — the same single canonical entry point
   `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Computing `review_content_id`"
   names for this stage, never a second, ad hoc computation. In
   `/review-implementation`'s order:
   **Bundle check** (`LPR-R5-002`):
   `workflow_state.verify_implementation_review_bundle`, `/review-implementation`
   step 4's check, which refuses an absent or incomplete bundle, a bundle
   whose recomputed `bundle_id` is not its `MANIFEST.md`'s, and a manifest
   whose `review_content_id` is not the current one
   (`ImplementationReviewBundleUnverifiedError`; remedy: regenerate the
   implementation bundle). It runs before the generation check, so a
   bundle that fails both reports this refusal.
   **Staleness/wrong-worktree handling**:
   `workflow_fingerprint.assert_local_generation_matches` over
   `<bundle_dir>/MANIFEST.md` (`WorktreeOrHeadMismatchError` when `HEAD`
   moved past its `generation_head` or this is another worktree).
   **`REJECTED`-bundle refusal, first of two** (`WFR-67`):
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)`; a `BundleRejectedError` stops the command, naming the
   marker path and its recorded detail.
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
     phase guard first; this only fires for a hand-edited state).
   Separately, the feedback's `Reviewed bundle ID:`, when present, is
   checked against the current recomputed one via
   `workflow_state.check_manual_stage_bundle_id_advisory` (already
   stage-agnostic, reused verbatim): a mismatch is **advisory only** —
   reported in the returned `advisory`, naming both values, never blocking
   (a wrapper-only bundle regeneration between upload and paste, new
   `bundle_id`/unchanged `review_content_id`, must not invalidate the
   manual stage). When it is absent, the ingest records `bundle_id: null`,
   runs no advisory check and reports the advisory
   `"Reviewed bundle ID: absent"`. `round` is the feedback's `Round:` when
   stated, otherwise the round of the `LOCAL_MODEL_IMPLEMENTATION_REVIEW`
   `APPROVE` of the same content (`LPR-R3-005`).
   **Gate-policy audit and the reviewer model** (workflow-2.8.0, `D-GP-Gates`,
   `D-GP-Trust`, `D-GP-Ingest`): the ingest is called with
   `run_ref="session:local <feedback_dir>/REVIEW_FEEDBACK.md"` (the resolved
   path, `workflow_state.LOCAL_RUN_REF_PREFIX`; the orchestration protocol's
   `record-external-result` passes the reporter's own `--run-ref` instead).
   While the technical gate is `automatic` (`GATE_POLICY.json`, the effective
   policy), an `APPROVE` ledger entry also records `verdict_sha256` (the
   sha256 of the pasted verdict's own bytes), `run_ref` and, when the
   effective `require` lists `distinct_reviewer_models`, the verdict's
   `Reviewer model: <vendor>/<model>` header line as `reviewer_model`. Under
   that requirement an `APPROVE` that states no such line, or the same family
   (the value up to the first `/`, `:` or space, lowercased) as the other
   stage's recorded one, is refused **before any write**
   (`DistinctReviewerModelsRequiredError`: `WORKFLOW_STATE.json` byte-identical,
   the phase unchanged); the message names the two remedies that work at this
   phase -- re-submit with a second family's line, or turn the gate human
   (`"human_approval": true` for it, immediate) and re-submit. A `REVISE` or
   `BLOCK` is admitted with no line. Under a human gate nothing changes: no
   key, no line, no refusal (2.7.0's bytes).
7. **Write set, exact.** **`REJECTED`-bundle refusal, second of two, under
   this step's own mutation guard** (`WFR-67`): immediately before the
   first write below, the ingest re-calls
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` — a withdrawal landing between step 5/6 and here must
   still be caught. The pasted file is then rewritten in place (a no-op for
   identical bytes) and the state published through
   `workflow_state.state_transaction`, its mutator being
   `workflow_state.record_manual_implementation_review(state, work_item_id,
   verdict=<Status>, bundle_id=<the feedback's own bundle_id, verbatim, or
   null>, round=<round>, now=<now>,
   current_review_content_id=<review_content_id>,
   feedback_role=<the feedback's declared role>,
   feedback_review_content_id=<the feedback's declared review_content_id>)`:
   - `APPROVE`: the resolved work item's
     `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` ledger fields (recording the
     feedback's actual `bundle_id` regardless of whether it matched the
     current recomputed one, so the ledger records what the reviewer
     actually saw) and its phase transition to the terminal
     `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` phase — the existing phase
     name, reused rather than a new one (`D-Implementation-Review-Version-Activation`'s
     "terminal-phase naming" decision).
   - `REVISE`: only the phase transition directly to
     `APPLYING_REVIEW_FEEDBACK` (no ledger write).
   - `BLOCK`: nothing (a true no-op; the work item stays at
     `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`).
   Never the plan, registry, mapping, command, product, or bundle-content
   files.
8. **Report and stop.** Report the ingest's returned `round`, `bundle_id`
   and `advisory`. For an `APPROVE`: state that
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is next and that only the user
   can invoke `/approve-review implementation` -- or, where the technical gate is
   automatic and every requirement is met, that `/satisfy-gate implementation`
   records the approval from the policy instead (the technical-approval gate
   for a `"2.2"` item additionally requires both ledger stages current --
   already satisfied the moment this write lands). For a `REVISE`: state
   that `/apply-implementation-review` is next. For a `BLOCK`: state that
   explicit user resolution is required before any further command runs.
   **Never** auto-continue to `/apply-implementation-review` or
   `/approve-review` in this same invocation.

Do not implement product or test code in this command. Do not edit the
plan/registry/mapping/artifacts files or any other command file.
