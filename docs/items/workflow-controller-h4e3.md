# H4e3: Codex worker, step 5d

Item id: `workflow-controller-h4e3`.

Not yet planned. Run `apply-plan-review` and `apply-implementation-review` on Codex, with the carry in and out and the recovery, and without the Controller running the managed repository's generator. This item needs H4e2b. It was split from H4e2 on 2026-11-06 ([#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315)) by owner decision: the apply roles move to a follow-up. It was split again on 2026-11-10 ([#334](https://github.com/RodrigoFAbreu/workflow-controller/issues/334)); see the decision below. Complexity: medium.

## Scope, one mechanism

`apply-plan-review` edits plan files that are uncommitted (intent-to-add), and a clone sees only committed state, so how those edits reach a Codex clone and leave it again is the question this item settles. `apply-implementation-review` commits work, which the Controller lands fast-forward under the H4e rules. The Controller still writes the stage ledger, state and feedback itself, as in H4e2.

The one mechanism is the carry in and out and the recovery of the two apply roles. The Controller stops after the landing. It never runs the managed repository's `scripts/prepare-ai-review.sh` or any other generator of that repository; regenerating the review bundle is left to the existing Claude path or the operator. Running the generator safely is [H4e3b](workflow-controller-h4e3b.md).

## Out of scope

Running the managed repository's generator after a landing (H4e3b), `milestone-implement` (H4f), the options UI, and the Manager's use of this.

## Decision (2026-11-10)

Owner decision (SignalHub 01a1c55d, answer "split"), recorded in [#334](https://github.com/RodrigoFAbreu/workflow-controller/issues/334). The H4e3 plan did not converge. Local plan rounds 5 and 6 each found a new way a file written by the Codex worker is executed by the managed repository's generator, which the plan had the Controller run right after landing the worker's commits (design decision D6b): a `scripts/sitecustomize.py` in round 5 (H4E3-R5-001), then a module that shadows the standard library and a forged `.pyc` in round 6 (H4E3-R6-001). That is an open-ended adversarial class, found at the cap of six local rounds. H4e3 therefore keeps the carry and the recovery and drops the generator run; the generator run moves to H4e3b, which needs its own safety design. Order: H4e3, H4e3b, then H4f.

## Planning notes

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
