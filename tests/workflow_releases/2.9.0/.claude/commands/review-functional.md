---
description: Independently review the current functional-review checklist's completeness and evidence reproducibility for the active (or named) work item and print an advisory report. Report-only -- never writes state, never approves, never applies findings.
argument-hint: "[work-item-id]"
state_writer: false
review-subject: bundle
---

Enters no new state — this is an optional, non-gating action available
while the resolved item's `phase` is exactly `AWAITING_FUNCTIONAL_REVIEW`
(`docs/ai-workflow/MILESTONE_WORKFLOW.md`). Implements a model-independent
**review role**, not a specific model: nothing in this contract, in the
report it produces, or in any check it performs names a model — running it
from any capable Claude model produces the same behavior.

Only the user can actually exercise the manual flows the checklist names —
an Android-app walkthrough for product/UI work, or command/process checks
(running workflow commands, inspecting repository state transitions and
evidence, confirming operator behavior) for a process work item such as
`workflow-v2-3` itself. This command cannot replace that. What it *can* do
is exactly what `/review-plan` already does for a plan bundle, adapted to
this stage's different artifact shape: independently re-run every
automated-verification command the checklist cites and confirm the real
output, and independently assess whether the checklist's manual-flow list
actually covers the behavioral surface of the diff since the item's
`base_commit` (or since the prior round's own checklist-evidence commit,
for a continued round) — catching a stale command, a missing setup step, or
an uncovered code path before the user spends time on the manual pass.

**Review and report only.** This command never writes
`docs/ACTIVE_MILESTONE.md`, `<feedback_dir>/FUNCTIONAL_REVIEW.md`, or
`docs/ai-workflow/WORKFLOW_STATE.json`, never fixes findings, and never
advances `phase`. `/apply-functional-review` remains the sole authoritative
remediation path, and `/accept-milestone` remains the sole, user-only
acceptance gate — all entirely unchanged and unaffected by this command's
existence.

There is no bundle, `bundle_id`, or `MANIFEST.md` at this stage in the
normal milestone-gated flow, so this command never calls
`workflow_fingerprint.assert_local_generation_matches` — there is no
recorded worktree/HEAD metadata to compare the current worktree against.
It is still a required consumer of the shared `REJECTED`-marker guard
(step 6 below): that marker is work-item-scoped, not bundle-scoped, so a
withdrawn work item must not receive an advisory functional-review opinion
either — the same reason `/apply-functional-review`'s bounded-fix branch is
a required consumer of the same guard.

`<feedback_dir>` below resolves per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_feedback_dir`).
`resolve_feedback_dir` decides `<feedback_dir>` by the item's durable
`feedback_layout` (`D-Feedback-Layout`, workflow-2.6.0):
`.ai-review/<work_item_id>/feedback/` unconditionally, by construction, for
a `feedback_layout: "scoped"` item; the unchanged legacy scoped-else-flat
rule for an item without the field.

1. **Resolve the work item**: `$ARGUMENTS`, if given, names the
   `work_item_id`; otherwise use `active_work_item_id`
   (`docs/ai-workflow/WORKFLOW_STATE.json`). Refuse cleanly, naming the
   problem, if neither resolves to an existing `work_items` entry — this
   command needs a live `phase` value to guard against; an ordinary `"1"`
   item that never got a state entry has no `phase` to check, so use
   `/prepare-review` for an ad-hoc, untracked review of that kind of item
   instead.
2. **Phase guard**: if the resolved item's `phase` is not exactly
   `AWAITING_FUNCTIONAL_REVIEW`, refuse cleanly, naming the actual phase.
   This guard is version-independent by design: reachable for a
   `governing_workflow_version` of `"1"`, `"2.1"`, or (`workflow-2.5.0`)
   `"2.2"` alike, since `docs/ai-workflow/MILESTONE_WORKFLOW.md` defines
   this state identically for all three.
3. **Locate the checklist's current evidence**: read
   `implementation_revision` live from the resolved work item; call
   `workflow_state.discover_current_functional_checklist_evidence(repo_root,
   work_item_id, work_item["base_commit"], head=<current HEAD>,
   implementation_revision=<that value>)`.
   - No result: refuse cleanly, naming the round — no checklist evidence is
     committed to review yet; report that `/prepare-functional-review` has
     not completed its checklist-evidence provenance commit for this round.
   - `NonFirstParentFunctionalChecklistEvidenceError`: refuse cleanly,
     naming that cause directly rather than surfacing an uncaught
     traceback — inherited from `/prepare-functional-review` step 3a, which
     calls the same function and is equally silent about this exception;
     not a new gap introduced here.
   - A result: compare its `"blob"` against `git hash-object
     docs/ACTIVE_MILESTONE.md`'s live working-tree content. A mismatch
     means the checklist has been edited since its own evidence commit —
     **refuse rather than review stale evidence**: name both blobs
     prominently and tell the user to rerun `/prepare-functional-review` so
     this review is anchored to committed checklist evidence, rather than
     silently reviewing the live working-tree text instead.
4. **Read**: the checklist's current round section in
   `docs/ACTIVE_MILESTONE.md` (at the evidence commit located in step 3),
   the diff since `base_commit` (or since the prior round's own
   checklist-evidence commit, for a continued round) via `git diff`/
   `git log`, and `technical_approval` — must be `status: CURRENT`; if
   `STALE`, report this prominently, since the checklist may predate a
   since-invalidated implementation.
5. **Independently verify**:
   - re-run every automated-verification command the checklist cites
     yourself, and confirm the actual output matches what the checklist
     claims — never trust the claim at face value;
   - assess the checklist's manual-flow coverage against the actual diff —
     does every behaviorally-significant change since the relevant base
     have at least one flow that would exercise it; name any gap;
   - flag any claimed-passing check you could not reproduce, any missing
     setup/test-data step, and any ambiguous or untestable "expected
     result" wording.
6. **`REJECTED`-marker refusal, this command's sole assertion, immediately
   preceding the report**: call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here, before composing the report. If this refuses
   unexpectedly, report both the scoped (`.ai-review/<work_item_id>/REJECTED`)
   and flat (`.ai-review/REJECTED`) candidate marker paths, not only the
   one actually found.
7. **Compose the report**: a checklist-completeness/evidence-reproducibility
   opinion — **not** a bundle-style `Status: APPROVE | REVISE | BLOCK`
   verdict, since there is no bundle or ledger stage to gate here (state
   this explicitly, so the report is never mistaken for a
   `REVIEW_FEEDBACK.md`-shaped verdict) — covering: which automated checks
   reproduced cleanly and which did not; which behavioral changes the
   checklist covers and which it does not; concrete gaps the user should
   address in the checklist before spending time on the manual pass; and an
   explicit, prominent reminder that only the user's own manual execution
   of the checklist's required functional flows can actually satisfy this
   gate — an app walkthrough for product/UI work items, command/process
   checks for process work items — this report is a pre-check, not a
   substitute for it.
8. **Report only — writes nothing.** Print the composed report directly in
   this turn's response. Nothing at `docs/ACTIVE_MILESTONE.md`,
   `<feedback_dir>/FUNCTIONAL_REVIEW.md`, `docs/ai-workflow/WORKFLOW_STATE.json`,
   or any other repository file is touched. Never approve, never fix
   findings, never transition `phase`, never auto-continue to any other
   command — `/apply-functional-review` is the only command that ever acts
   on a real `FUNCTIONAL_REVIEW.md`, and only the user can invoke
   `/accept-milestone`.

Do not implement product or test code in this command. Do not edit the
plan/registry/mapping/artifacts files or any other command file.
