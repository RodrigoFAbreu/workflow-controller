# CI2b: run each test once, the duplicate-run removal

Item id: `workflow-controller-ci2b`.

Not yet planned. A slice of [CI2](workflow-controller-ci2.md), split at its planning. Depends on CI2 (the refreshed timings and the estimates). Sources: scope items 3, 4 and 7 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target). Complexity: medium.

## Scope

One mechanism: stop running the same test twice on a pull request.

3. Run the four functional flows once on pull requests: `tests/test_functional_lifecycle.py` (738 s) and the four `Scenario_scenario_functional_*` scenarios (737 s) run the same flows twice, 15.5 percent of the Controller test time (about 4 min).
4. Stop running the frozen conformance suites inside the shards, because the managed `workflow-conformance` job already gates them (3 to 8 min, medium risk). `tests-result` loses its coverage proof for those suites; the plan must record how the proof is kept: the managed job is the gate.
7. Cut the Codex loops and the duplicate test (about 1 min): `RecoveryTest` 90 s, `OutMemberPlantTest` 61 s, `PlanBoundaryTest` 34 s, the plant loops 104 s, and one exact duplicate test 20 s.

Estimates are the expected saving on pull request wall time. The planning rules (at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules); the sizing of the whole family is on the [CI2 page](workflow-controller-ci2.md).
