---
description: Repair a stale generation_head at the implementation stage after a legitimate excluded-only commit lands past the current Workflow-Bundle-Generation-Record commit, without starting a new implementation round.
argument-hint: "[work-item-id]"
state_writer: true
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names, applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

`WFR-62`'s stale-`generation_head` recovery (`WF8c` (b)): after a work
item's implementation-stage bundle has already been generated and is
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, a later, entirely legitimate
excluded-only commit (e.g. a doc fix, a scripts/-prefixed change, anything
`docs/ai-workflow/registry/<work_item_id>-artifacts.json` classifies
`excluded` at the implementation stage) can land on top of the current
`Workflow-Bundle-Generation-Record` commit `T` without changing the
protected implementation-stage content at all. `T` is then no longer live
HEAD, so `workflow_state.verify_implementation_provenance_interval`
refuses via `HeadPastBundleGenerationRecordError` and
`/approve-review implementation` cannot proceed — not because anything is
genuinely wrong, but because no commit exists yet recording that the round
under review now spans the wider, still-content-identical interval. This
command creates that recording commit (`S2`) without starting a new
implementation round: `reviewed_implementation_head`/
`implementation_revision` are never touched, and the work item's phase
stays exactly `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` throughout. It is
the standalone counterpart of `WF8c` (c)'s
`resolve_bundle_generation_outcome`-driven `record_bundle_generation`
same-content path — that path only runs from `record_bundle_generation`'s
own legal source phases (`workflow-v2-3-followups` continued scope,
stage-specific: `SELF_REVIEWING_IMPLEMENTATION` for
`stage="implementation"`; `APPLYING_REVIEW_FEEDBACK`, or
`AWAITING_FUNCTIONAL_REVIEW` with a `STALE` `technical_approval`, for
`stage="post-fix"` — a caller already regenerating a bundle for other
reasons); this command is the only way to reach the identical
recovered-role commit shape from `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
itself, without re-entering any of those remediation cycles just to fix a
provenance pointer.

`<bundle_dir>` below resolves per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir`).

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`). Refuse cleanly if the item has
   no `WORKFLOW_STATE.json` entry — this command has nothing to recover for
   an item with no tracked round.
2. **Phase guard**: if the item's `phase` is not
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, stop and say so, naming the
   actual phase — this is the only legal source phase
   (`IllegalImplementationProvenanceRecoverySourcePhaseError`). A work item
   already at one of `record_bundle_generation`'s own legal source phases
   — still being written (`SELF_REVIEWING_IMPLEMENTATION`/
   `APPLYING_REVIEW_FEEDBACK`), or mid functional-review bounded fix
   (`AWAITING_FUNCTIONAL_REVIEW` with `technical_approval.status ==
   "STALE"`) — already has its own same-content path through
   `record_bundle_generation`'s `outcome="same_content"` (`WF8c` (c)) the
   next time its bundle is regenerated — never invoke this command from
   those phases.
3. **Diagnose, read-only, before asking for confirmation**: call
   `workflow_state.verify_implementation_provenance_recovery(repo_root,
   work_item, base_commit=<base_commit>, head=<current HEAD SHA>)`. This
   performs no state mutation and no Git write — it re-uses `WF8c` (c)'s
   own `resolve_bundle_generation_outcome` to confirm the protected
   implementation-stage content at live HEAD is byte-identical to
   `reviewed_implementation_head`'s and that every commit in the interval
   between the current `Workflow-Bundle-Generation-Record` commit `T` and
   live HEAD classifies implementation-stage excluded-only (or is itself a
   valid, chain-continuous prior recovery link). On any raised exception
   (`ImplementationProvenanceRecoveryNotApplicableError` — content
   genuinely changed, no prior round exists, or HEAD is already `T` itself
   with nothing to recover — or any of
   `resolve_bundle_generation_outcome`'s own named interval-classification
   exceptions), stop and report the concrete reason; never proceed, and
   never fall back to treating this as an ordinary bundle-regeneration
   round. On success this returns `t`, the commit the new recovery commit
   must supersede.
4. **User-confirmation gate** (`WFR-62`, missing-test item 251 -- "identical
   in spirit to `/approve-review`'s own `validate_user_confirmation` guard"):
   this command creates a new, durable Git commit and is not something to
   run silently. Before creating anything, show the user: the work item
   id, `t` (the commit being superseded), the number and subjects of the
   excluded-only commits found between `t` and live HEAD, and that
   `reviewed_implementation_head`/`implementation_revision` will be left
   completely unchanged. Then call
   `workflow_state.validate_implementation_provenance_recovery_confirmation(
   <this turn's literal message text>, work_item_id=<work_item_id>,
   superseded_commit=t)` — this requires the user's current-turn message to
   literally name both the exact `work_item_id` and the exact superseded
   commit SHA `t`; a `UserConfirmationRejectedError` stops the command and
   reports the concrete reason. Never treat silence, a prior unrelated
   approval, an earlier turn's confirmation, or a generic go-ahead with no
   literal SHA as sufficient — ask again, naming both values, and wait for
   this turn's reply before proceeding to step 5.
5. **Write the recovery commit**: call `workflow_state.state_transaction(
   repo_root, lambda state:
   workflow_state.apply_implementation_provenance_recovery(state,
   work_item_id, now=<now>))` (the recovered-role field set: only
   `state_revision`/`last_transition` change — `phase` stays
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` value-unchanged, unlike
   `record_bundle_generation`'s own legal source phases, every one of
   which transitions *into* it), persist the returned state, and commit it
   **alone** — stage exactly `docs/ai-workflow/WORKFLOW_STATE.json` (never
   a broader `git add`) — as the one first-parent child of live HEAD
   (never of `t`; ancestry is never rewritten), carrying exactly three
   trailers: the unchanged `Workflow-Bundle-Generation-Record:
   <work_item_id>/<implementation_revision>` value (never a new revision
   number), `Workflow-Work-Item: <work_item_id>`, and
   `Workflow-Supersedes: <t>` naming the commit step 3 returned. No other
   trailer, and no other path in the same commit. **These three trailer
   lines must be the commit message's own final paragraph** — after any
   `Co-Authored-By:`/`Claude-Session:` lines, never before them
   (`OPUS-R129-001`, the same rule `milestone-implement.md` step 1f,
   `bootstrap-workflow-v2.md` step 6, `approve-review.md` step 6.4, and
   `accept-milestone.md` step 6 already state): Git's `git
   interpret-trailers --parse`, the exact mechanism
   `discover_current_bundle_generation_record_commit` uses, treats only
   the message's last paragraph as trailers, so a blank line after these
   lines (e.g. one followed by `Co-Authored-By:`) silently discards them
   and makes the recovery commit undiscoverable.
6. **Regenerate the bundle at the new tip**: read `<bundle_dir>/MANIFEST.md`'s
   existing `stage:` field (recovery does not change what kind of round
   this is, only which commit its content is measured at) and run
   `./scripts/prepare-ai-review.sh <base_commit> <that stage>
   [work_item_id]` against the new HEAD (the commit step 5 just created).
   This is required — a stale bundle whose `MANIFEST.md` still names the
   superseded `t` as `generation_head` fails
   `assert_local_generation_matches`'s local staleness check the moment
   anyone re-derives it, exactly the failure mode this command exists to
   repair, one level removed if skipped here.
7. **Report and stop.** State the new `S2` commit's SHA, that
   `reviewed_implementation_head`/`implementation_revision` are unchanged,
   that the bundle's `bundle_id` has necessarily changed (a new
   `generation_head`), and that any previously-collected external
   implementation-review feedback is therefore stale against it — a fresh
   external implementation review is required before
   `/approve-review implementation` can evaluate the now-valid interval via
   `EXTERNAL_APPROVE`; `USER_OVERRIDE` remains available in the interim
   exactly as it already is for any other stale-feedback round. Never
   auto-invoke `/approve-review` in this same invocation.
