# CI2: CI follow-ups, the 20-minute PR CI target and the review follow-ups of CI1a to CI1c

Item id: `workflow-controller-ci2`.

Not yet planned. Decide how to bring PR CI toward the 20-minute target and close the open CI follow-ups. Sources: [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322), [#324](https://github.com/RodrigoFAbreu/workflow-controller/issues/324). Complexity: medium; split by option at planning.

## Where CI stands

CI1b and CI1c measured no gain: PR CI took 27 min 23 s against a baseline of 27 to 30 minutes. The floor is the `acceptance_matrix` conformance atom, about 21.5 minutes with contention, so splitting or rebalancing the other shards cannot reach 20 minutes.

## Options (one mechanism each)

- A larger runner: needs an owner billing decision.
- Work on the `acceptance_matrix` atom itself (the floor).
- Replace the CI1a static repo-reach heuristic in `tests/test_ci_changes.py` with a runtime check ([#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322)): a suite run with the tree unreadable, or a hash before and after.
- The CI1b review follow-ups ([#324](https://github.com/RodrigoFAbreu/workflow-controller/issues/324)), a small documentation change: reword the item page's pre-plan summary, say "blends" for the next merge, add the two missing D4 rule parts and the "base without test methods" wording to the development guide, and record the fixture keys and the derivation inputs.

## Out of scope

The timing flakes (FX1) and raising `CI_MAX_SHARDS` without an owner answer.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
