---
description: Ad-hoc review bundle for work outside the milestone workflow gates.
argument-hint: "<base-sha> <stage> [work-item-id]"
state_writer: false
review-subject: bundle
---

For milestone-gated reviews (plan, implementation, post-fix, functional),
prefer `/milestone-plan`, `/milestone-implement`, `/apply-implementation-review`,
or `/prepare-functional-review` instead — they populate the review-bundle
content correctly for each state in `docs/ai-workflow/MILESTONE_WORKFLOW.md`.

Use this command only for a one-off review of work that isn't part of a
tracked milestone checkpoint.

`<bundle_dir>` below is `.ai-review/<work_item_id>/current/` if a work
item ID applies and that layout already exists, else the flat
compatibility path `.ai-review/current/`
(`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location";
`workflow_fingerprint.resolve_bundle_dir(repo_root, work_item_id)`).
Ad-hoc reviews commonly have no tracked work item at all — omit the
script's `[work-item-id]` argument in that case and use the flat path
throughout, for any stage **other than** `plan` (see step 3). When
`<stage>` *is* `plan`, a work item ID is required, and `<bundle_dir>` is
resolved as `resolve_bundle_dir(repo_root, work_item_id, stage="plan")` —
always `.ai-review/<work_item_id>/current/`, with no existence gate and no
flat branch, matching what the generator writes for that stage even on a
work item that has no scoped directory yet.

1. Determine the correct base commit for what's being reviewed (usually the
   commit before this work started).
2. Write `<bundle_dir>/REVIEW_REQUEST.md` per the format in
   `docs/ai-workflow/REVIEW_PROTOCOL.md`, and `IMPLEMENTATION_SUMMARY.md`/
   `TEST_RESULTS.md`/`CONTEXT_FILES.txt` as applicable. Never claim a check
   passed unless it actually ran.
3. Run `./scripts/prepare-ai-review.sh <base-sha> <stage> [work-item-id]`
   (pick the closest matching stage: `plan`, `implementation`, `post-fix`,
   or `functional-review`) to generate `<bundle_dir>/` and its
   `review-bundle.tar.gz`. The work-item-id is **required when `<stage>`
   is `plan`** (never resolved from the live `active_work_item_id` for
   that stage, `D-Fingerprint-Generalization`) and remains optional for
   every other stage.
4. **`REJECTED`-bundle refusal, this command's sole assertion, immediately
   preceding the report** (`WFR-67`): when a tracked `work-item-id`
   applies, call `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here — for a report-only consuming act, this single
   assertion is also the mutation-guard assertion. Skip this call for a
   genuinely ad-hoc review with no tracked work item at all (nothing to
   resolve a marker path against). Report the bundle location. Do not
   commit `.ai-review/`.
