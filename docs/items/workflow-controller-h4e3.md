# H4e3: Codex worker, step 5d

Item id: `workflow-controller-h4e3`.

Not yet planned. Run `apply-plan-review` and `apply-implementation-review` on Codex. This item needs H4e2b. It was split from H4e2 on 2026-11-06 ([#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315)) by owner decision: the apply roles move to a follow-up. Complexity: medium.

## Scope, one mechanism

`apply-plan-review` edits plan files that are uncommitted (intent-to-add), and a clone sees only committed state, so how those edits reach a Codex clone and leave it again is the question this item settles. `apply-implementation-review` commits work, which the Controller lands fast-forward under the H4e rules. The Controller still writes the stage ledger, state and feedback itself, as in H4e2.

## Out of scope

`milestone-implement` (H4f), the options UI, and the Manager's use of this.

## Planning notes

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
