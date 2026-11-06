# H4e2: Codex worker, step 5b

Item id: `workflow-controller-h4e2`.

The four review-side roles (`review-plan`, `review-implementation`, `apply-plan-review`, `apply-implementation-review`) can be routed to Codex; a safety review and a live proof on a scratch repository (operator-run). This item needs H4e (the clone and its fast-forward landing), H4c2, H4d and H1. It was split from H4e on 2026-11-05 ([#303](https://github.com/RodrigoFAbreu/workflow-controller/issues/303)). Tracking issue: [#312](https://github.com/RodrigoFAbreu/workflow-controller/issues/312). Complexity: medium; if the plan exceeds 5 checkpoints, split it into CPs.

## Scope, one mechanism

The one mechanism is routing. This item:

- routes the four review-side roles through the routing config;
- reuses the H4e preparer (`job.CODEX_WORKTREE_PREPARER`) and the fast-forward landing unchanged;
- adds a safety review of the routed path;
- adds an operator-run live proof on a scratch repository.

## Open question carried from H4e

H4e requires a clean managed tree, because a clone sees only committed state and a landing is a fast-forward (D2). The Workflow keeps plan files intent-to-add and `WORKFLOW_STATE.json` edits uncommitted between gates, and `.ai-review/` bundles are ignored, so a review-side role cannot run in a clone as it stands. This item must decide how a role gets what it reads and how its writes (stage ledger, feedback, plan edits) come back. The options are:

1. Commit first: the roles commit that state before the clone is made.
2. Copy in and out under the Controller: the Controller copies the files into the clone and the results back.
3. Review roles read-only: the Controller writes the state itself.

The plan must settle this first, before it routes any role. If the choice would weaken an approval check, the plan asks the owner through SignalHub.

## Decisions the plan must settle

- Which roles write what (`review-*` advisory, `apply-*` edits) and what each landing must verify.
- Protected-path rules: a worker's change to a protected file must not weaken any approval check.
- The safety review (credentials, symlink, submodule, hook, crafted ref), with a test for every refusal.
- The usage cost of the Codex review loop against the weekly hold.

## Out of scope

`milestone-implement` on Codex (H4f), the options UI, and the Manager's use of this.

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
