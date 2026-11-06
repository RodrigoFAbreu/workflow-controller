# H4e2: Codex worker, step 5b: the review recorder and the read-only run

Item id: `workflow-controller-h4e2`.

The review recorder (`controller/review_recorder.py`: the Controller runs the Workflow's own local-stage functions) and the read-only run of `review-plan` and `review-implementation` in the H4e clone, with the verdict header written by the Controller and the deny set. This item needs H4e (the clone and its fast-forward landing), H4c2, H4d and H1. It was split from H4e on 2026-11-05 ([#303](https://github.com/RodrigoFAbreu/workflow-controller/issues/303)) and narrowed on 2026-11-06 ([#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315)): routing and the safety tests are H4e2b, the two `apply-*` roles are H4e3. Tracking issue: [#312](https://github.com/RodrigoFAbreu/workflow-controller/issues/312). Complexity: medium; checkpoints 1-2 of the redraft. Still dark: no role is routed to Codex.

## Scope, one mechanism

The one mechanism is the recorder. This item:

- adds `controller/review_recorder.py`, in which the Controller runs the Workflow's own local-stage functions and writes the stage ledger, `WORKFLOW_STATE.json` and feedback itself;
- runs `review-plan` and `review-implementation` read-only in the H4e clone, with the verdict header written by the Controller and the deny set;
- reuses the H4e preparer (`job.CODEX_WORKTREE_PREPARER`) and the fast-forward landing unchanged;
- routes nothing: no role reaches Codex until H4e2b.

## Decision (2026-11-06)

H4e requires a clean managed tree, because a clone sees only committed state and a landing is a fast-forward (D2). The Workflow keeps plan files intent-to-add and `WORKFLOW_STATE.json` edits uncommitted between gates, and `.ai-review/` bundles are ignored, so a review-side role cannot write its state from a clone. H4e left open how a role gets what it reads and how its writes come back. The options were:

1. Commit first: the roles commit that state before the clone is made. Rejected for the reason below.
2. Copy in and out under the Controller: the Controller copies the files into the clone and the results back. Rejected for the reason below.
3. **Chosen: review roles read-only, the Controller writes the state itself.**

The owner chose option 3 on 2026-11-06 (SignalHub answer "rescope"). The four review-side roles run read-only in the Codex clone. They never write the ledger, `WORKFLOW_STATE.json`, plan files or feedback inside the managed repository. The Controller writes the stage ledger, state and feedback itself from what the role returns. The role returns its verdict and feedback; an `apply-*` role also returns its edits as committed work, which the Controller lands fast-forward under the H4e rules. There is no per-role field allowlist and no value rules on state fields.

Reason: options 1 and 2 both let a role write state fields, which needs a per-role field allowlist. Two local plan reviews (rounds 6 and 7, cap reached) kept finding defects in such an allowlist: a `review-plan` could forge the external stage, and the value rule refused the real writer's output. That is a repeated finding class, so the plan removes the allowlist instead of patching it.

## Decisions the plan must settle

- How the Controller obtains each role's output (verdict, feedback) and writes the stage ledger, `WORKFLOW_STATE.json` and feedback itself.
- Protected-path rules: a worker's change to a protected file must not weaken any approval check.
- The Optional items stay in scope only as notes.

## Out of scope

Routing the roles, the adversarial safety tests, the live proof and the cost table (H4e2b); `apply-plan-review` and `apply-implementation-review` on Codex (H4e3); `milestone-implement` (H4f), the options UI, and the Manager's use of this.

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

Notes from local plan review round 9 (the plan did not converge at the cap of 9 and was split):

- Important I1: the D6 endings must name `--abandon`, drift and `sandbox_breach`.
- O1: a `credential_in_verdict` ending leaves the session rollouts in `codex-home/sessions`; say so.
- O2: `launch_codex_worker` returns no raw bytes field; the plan must not assume one.
- O3: the regenerate command is `workflow_backlog.py generate`.
