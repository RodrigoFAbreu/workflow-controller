# Two-Stage Plan-Review Protocol (`governing_workflow_version: "2.1"`/`"2.2"` only)

Concise operator guide for the local-then-manual-external plan-review
protocol. Full design and rationale live in
`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s `D-Plan-Review-Stages`; full state
definitions and the verdict/state transition table live in
`docs/ai-workflow/MILESTONE_WORKFLOW.md`. This document only describes the
normal flow, operationally — it does not repeat either.

Documents the already-reviewed design for operators; it is not itself the
design (same treatment as `MILESTONE_WORKFLOW.md`/`REVIEW_PROTOCOL.md`) —
writing or editing this file never changes plan-stage `review_content_id`.

Scoped entirely to work items whose `governing_workflow_version` is in
`TWO_STAGE_PLAN_REVIEW_VERSIONS = {"2.1", "2.2"}` (`"2.2"` added by
`workflow-2.5.0`'s `D-Implementation-Review-Version-Activation` — plan
review for a `"2.2"` item runs this identical mechanism, unchanged; only the
later, implementation-stage review this item's plan cannot see is new for
`"2.2"`, documented in `docs/ai-workflow/IMPLEMENTATION_REVIEW_WORKFLOW.md`).
A `"1"` item (including this repository's own `workflow-v2-1-core` work
item, fixed at `"1"` for its entire execution) uses the existing
single-stage `AWAITING_EXTERNAL_PLAN_REVIEW` → `AWAITING_PLAN_APPROVAL` flow
unchanged; `/review-plan` and `/record-manual-plan-review` both refuse
cleanly if invoked against a `"1"` item.

## Normal flow

```text
Sonnet: /milestone-plan
Opus (fresh session): /review-plan
  REVISE → Sonnet: /apply-plan-review → Opus (fresh session): /review-plan (repeat)
  APPROVE → continue below
User: upload the exact approved current bundle to ChatGPT
User: paste ChatGPT's feedback into REVIEW_FEEDBACK.md
Sonnet: /record-manual-plan-review
  REVISE → Sonnet: /apply-plan-review → Opus (fresh session): /review-plan (return to the top)
  APPROVE → continue below
User: /approve-review plan only after the complete sequence approves
```

`REVIEW_FEEDBACK.md` here always means `<feedback_dir>/REVIEW_FEEDBACK.md`
(`D-Feedback-Layout`, workflow-2.6.0): `.ai-review/<work_item_id>/feedback/`
for every work item created under `2.6.0` or later, by construction; a
legacy item may still resolve the flat `.ai-review/feedback/`
(`REVIEW_PROTOCOL.md` "Feedback directory"). `/record-manual-plan-review` prints the exact
path to paste into; `python3 scripts/workflow_fingerprint.py
--resolve-feedback-path <work-item-id>` prints it at any time.

A fresh session is strongly recommended for both `/review-plan` and any
reviewer role generally — genuine independence from the session that wrote
or last revised the plan is the property the local stage exists to add.
This is operational guidance only: no command contract, state transition,
or validator rule depends on detecting session freshness.

## At every stop

Whichever command stops, it states, in its own report:

- the current work item (`work_item_id`);
- the review stage just completed or awaited;
- the bundle path, `bundle_id`, and `review_content_id` relevant to the
  next action;
- the exact next command to run, or the exact manual action required (e.g.
  "upload this bundle to ChatGPT").

## What each command does, in one line

- **`/review-plan`**: the `LOCAL_MODEL_PLAN_REVIEW` role. Independently
  re-verifies the plan against the repository, writes
  `REVIEW_FEEDBACK.md`, and — for an `APPROVE` only — records the local
  stage in the `plan_review_stages` ledger and advances to
  `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`. Model-independent: nothing in its
  contract names a specific model.
- **`/record-manual-plan-review`**: mechanical ingestion of an
  already-pasted manual verdict. Never evaluates plan content itself (the
  external reviewer already did) and never edits the plan. Not a
  user-authority gate — `/approve-review plan` remains the sole approval
  gate, unchanged.
- **`/apply-plan-review`**: unchanged mechanism, `"2.1"`/`"2.2"`-alike
  revised exit: after an accepted edit, never self-declares plan readiness —
  always returns to `AWAITING_LOCAL_PLAN_REVIEW`, requiring a fresh local
  pass before manual-external review can run again. Since `workflow-2.6.0`
  it applies only a `REVISE` verdict (a `BLOCK` or `APPROVE` refuses with
  `FeedbackStatusNotApplicableError`), and it reaches
  `AWAITING_LOCAL_PLAN_REVIEW` through the bind below.

## Publication, binding and withdrawal (`workflow-2.6.0`)

`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s `D-Plan-Review-Bundle-Binding` is
the design; operationally:

- **Publish, then bind.** `/milestone-plan` (at its publication point,
  after self-review) and `/apply-plan-review` (step 5, every round) call
  `publish_plan_revision`, which only mirrors the revision and records the
  content as `PUBLISHED` — the phase does not move. The plan bundle is then
  generated, and `bind_plan_review_bundle` writes
  `AWAITING_LOCAL_PLAN_REVIEW` only for a verified bundle of that published
  content. Content already reviewed, amended away or withdrawn is
  `CONSUMED` and never binds again without an edit. Every consumed
  `review_content_id` is kept in the work item's
  `consumed_plan_review_content_ids` history (workflow-2.7.0), so a later
  consumption does not release an earlier one: restoring withdrawn or
  revised content byte for byte is refused, and any edit gives it a new id.
- **Author inputs live in `plan-inputs/`.** Write the plan stage's
  `REVIEW_REQUEST.md`, `TEST_RESULTS.md` and `CONTEXT_FILES.txt` under
  `.ai-review/<work_item_id>/plan-inputs/`, never into `current/`. The
  generator builds the bundle in a staging directory and swaps it in only
  on success, so a failed generation leaves the previous bundle intact and
  writes no `REJECTED` marker.
- **A failed generation after the publish** leaves the item at
  `PUBLISHED_UNBOUND`. Re-run the same command with the explicit id —
  `/milestone-plan <id>` or `/apply-plan-review <id>` — which regenerates
  and binds without advancing the revision again. Not `/review-plan`: it
  refuses at a non-ready phase.
- **Readers check the binding.** `/review-plan`,
  `/record-manual-plan-review` and `/approve-review plan` refuse a bundle
  that is not the bound one, naming the remedy. A `bundle_id` that differs
  from the bound one only because the bundle was regenerated with unchanged
  content is a warning, never a block.
- **Withdrawing from review.** To change a plan that is already at a
  review or approval phase, run `/milestone-plan <id>` with the explicit
  id. It returns the item to `REVISING_PLAN` (or `AMENDING_PLAN` during an
  amendment), discards both recorded stages, and consumes the reviewed
  content. It refuses without the explicit id, and while `/approve-review
  plan` has an open approval transaction for the item. To undo an
  accidental edit instead, restore the bound bytes from
  `current/files/<path>`; no state change is needed.
- **`BLOCK`.** A plan-stage `BLOCK` changes nothing. After resolving it
  with the user, either re-run the review on the unchanged content, or
  edit the plan and withdraw with `/milestone-plan <id>`.
- **Where am I?** `python3 scripts/workflow_state.py
  --plan-review-publication-status <work-item-id>` prints the item's
  publication status, its row, and the remedy, as one JSON object, and
  writes nothing.

## Plan-stage writes are not committed (`workflow-2.9.1`)

No plan-stage review write is committed on its own: there is no generation
record at the plan stage, the plan-approval commit takes the whole
working-tree state, and a commit of its own raises `bundle_generation_mismatch`
at the plan-approval gate. A plan-stage `REVISE` therefore lives in the working
tree until approval. The rule for every review stage is in `REVIEW_PROTOCOL.md`
("Which review-stage writes are committed").

## Staleness, in one line

Both ledger stages are valid only while the ledger's stored
`review_content_id` equals the freshly recomputed current one. Any
protected-plan-doc edit changes that id, so both stages read as absent
immediately, with no explicit clear step — this is what makes "any
protected plan edit clears both stages" true by construction. The manual
stage's `bundle_id` check is the one exception: a mismatch there is
advisory only (warns, never blocks), since a wrapper-only bundle
regeneration between upload and paste is expected, not a staleness signal.
