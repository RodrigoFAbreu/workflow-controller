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
  pass before manual-external review can run again.

## Staleness, in one line

Both ledger stages are valid only while the ledger's stored
`review_content_id` equals the freshly recomputed current one. Any
protected-plan-doc edit changes that id, so both stages read as absent
immediately, with no explicit clear step — this is what makes "any
protected plan edit clears both stages" true by construction. The manual
stage's `bundle_id` check is the one exception: a mismatch there is
advisory only (warns, never blocks), since a wrapper-only bundle
regeneration between upload and paste is expected, not a staleness signal.
