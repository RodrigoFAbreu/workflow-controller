# H4e2: Codex worker, step 5b

Item id: `workflow-controller-h4e2`.

The four review-side roles (`review-plan`, `review-implementation`, `apply-plan-review`, `apply-implementation-review`) can be routed to Codex; a safety review and a live proof on a scratch repository (operator-run). This item needs H4e (the clone and its fast-forward landing), H4c2, H4d and H1. It was split from H4e on 2026-11-05 ([#303](https://github.com/RodrigoFAbreu/workflow-controller/issues/303)).

## Open question carried from H4e

H4e requires a clean managed tree, because a clone sees only committed state and a landing is a fast-forward (D2). The Workflow keeps plan files intent-to-add and `WORKFLOW_STATE.json` edits uncommitted between gates, and `.ai-review/` bundles are ignored, so a review-side role cannot run in a clone as it stands. This item must decide how those reach the clone, or whether the roles commit them first, before it routes any role.

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
