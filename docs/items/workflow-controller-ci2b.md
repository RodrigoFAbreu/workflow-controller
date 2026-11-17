# CI2b: run each test once, the duplicate-run removal

Item id: `workflow-controller-ci2b`.

Status: implemented on the milestone branch (three checkpoints); plan: [CONTROLLER_CI2B_PLAN.md](../ai-workflow/CONTROLLER_CI2B_PLAN.md) (revision 2). A slice of [CI2](workflow-controller-ci2.md), split at its planning. Depends on CI2 (the refreshed timings and the estimates). Sources: scope items 3, 4 and 7 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target). Complexity: medium.

## Scope

One mechanism: stop running the same test twice on a pull request.

3. Run the four functional flows once on pull requests: `tests/test_functional_lifecycle.py` (738 s) and the four `Scenario_scenario_functional_*` scenarios (737 s) run the same flows twice, 15.5 percent of the Controller test time (about 4 min).
4. Stop running the frozen conformance suites inside the shards, because the managed `workflow-conformance` job already gates them (3 to 8 min, medium risk). `tests-result` loses its coverage proof for those suites; the plan must record how the proof is kept: the managed job is the gate.
7. Cut the Codex loops and the duplicate test (about 1 min): `RecoveryTest` 90 s, `OutMemberPlantTest` 61 s, `PlanBoundaryTest` 34 s, the plant loops 104 s, and one exact duplicate test 20 s.

Estimates are the expected saving on pull request wall time. The planning rules (at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules); the sizing of the whole family is on the [CI2 page](workflow-controller-ci2.md).

## What was done

- **The conformance suites left the shards (scope item 4).** `CI_PLACEMENT["conformance"]` is `EXCLUDED` in `tools/test_shards.py`. The managed `workflow-conformance` job is the gate and runs the nine suites once; `tests-result` no longer proves their coverage. The kept proof is in `tests/test_ci_workflows.py`: the excluded conformance ids equal the suites the managed file lists, the file runs on `pull_request` and on `push` to `main` with one job, a CI plan holds no `conformance:` id and a local one holds all nine. That `workflow-conformance` is a required check is a ruleset setting, documented in the [CI and releases guide](../guide/ci-and-releases.md#repository-settings) and not tested.
- **The functional flows run once (scope item 3).** `CI_PLACEMENT["tests.test_functional_lifecycle"]` is `EXCLUDED`; the four `scenario-functional-*` scenarios keep the flows, and the leftover check moved into the isolation scenario so excluding `IsolationTest` loses no assertion. `tests/test_test_shards.py` pins that each `flow_*` body is called by exactly one scenario. A local full run keeps both copies.
- **Pages.** The [CI and releases guide](../guide/ci-and-releases.md) and the [development guide](../guide/development.md) state the placement, that the conformance lane is local-only under `--ci-placement`, and the moved-base residual below.

## The measured effect

The effect is read off the planner (`python3 tools/run_tests.py plan --profile ci --ci-placement`, committed timings), as CI2 did, and not promised. Figure sources: the committed timings give 691 s and 2,271 s below; the 738 s and 737 s of the scope text above are the CI2 sizing and are not comparable with them.

| plan | digest | shard 0 | shard 1 | shard 2 |
| --- | --- | --- | --- | --- |
| base (CI2) | `3198ddc7` | 3,494.0 s | 3,494.0 s | 3,494.0 s |
| after CP1 (conformance excluded) | `d01bdb71` | 3,038.2 s | 3,038.2 s | 3,038.2 s |
| after CP2 (and the lifecycle module excluded) | `edeab4d7` | 2,808.0 s | 2,808.0 s | 2,808.0 s |

The predicted gain is 686 s (about 11.4 minutes) off the longest shard. The excluded atoms and their committed seconds:

| atoms | seconds |
| --- | --- |
| the nine `conformance:` suites (`acceptance_matrix` 1,153.6, `protocol` 382.0, `state` 302.3, `gate_policy` 273.0, `fingerprint_generalization` 107.5, `completion_obligations` 36.0, `integration` 8.5, `fingerprint` 6.1, `test_harness` 2.4) | 2,271.4 |
| the four `tests.test_functional_lifecycle` classes (`NoRunTest` 204.4, `IsolationTest` 190.8, `CycleClosesTest` 160.3, `PersonStopsTest` 135.4) | 690.9 |

The second step is the lifecycle classes spread over three shards (690.9 / 3 = 230 s). The first step (456 s) is not the conformance total over three (757 s): the Controller lane was the longer lane on every shard (3,038.2 s), so the conformance lane added no seconds of its own and only switched on the 1.15 contention factor. Removing it removes that factor and nothing else: 3,038.2 s x 0.15 = 456 s. **The elapsed time of this pull request's own run against the CI2 baseline (55 min 41 s) is not yet measured**: the pull request is opened after the implementation review, and the figure is added here when its run finishes.

What remains: the lifecycle and Codex families are the bulk of the three shards, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target) stays open; this item does not close it.

## Owner decisions

- **O1, scope item 7 (the Codex loops and one duplicate test, about 1 minute).** Not in this item. The "exact duplicate test" is not identifiable from the source, and each loop variant is a distinct refusal, so cutting them is a coverage trade that needs a rule per loop. No owner answer to include it arrived before CP1, so it is added to `docs/backlog.yaml` as its own item when this one is accepted.
- **O2, the moved-base release residual.** Accepted as part of "the managed job is the gate". When `main` moved after a pull request's last run, a squash commit can be released although the conformance suites never ran on its tree, because `build-publish` does not wait for the managed job. No owner answer to "make the release wait" arrived before CP1, so it is added to `docs/backlog.yaml` as its own item when this one is accepted.
