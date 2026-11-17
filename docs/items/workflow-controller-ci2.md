# CI2: fresh shard timings, a measured contention estimate and the CI1b review follow-ups

Item id: `workflow-controller-ci2`.

Status: implemented on the milestone branch (three checkpoints); plan: [CONTROLLER_CI2_PLAN.md](../ai-workflow/CONTROLLER_CI2_PLAN.md) (revision 12). Complexity: medium. Sources: [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target, stays open until the last slice merges) and [#324](https://github.com/RodrigoFAbreu/workflow-controller/issues/324) (done here). A `ci:` change that edits the committed timings, tests and pages only; it publishes no release.

CI2 was large and said "split at planning". By the sizing rule (at most one new mechanism per releasable item) the owner's fourteen scope items (below) are split into five slices:

| slice | scope items | mechanism |
| --- | --- | --- |
| this item, `workflow-controller-ci2` | 1 (refresh and estimates), 2 (contention) | the planner estimates from measured data |
| [`workflow-controller-ci2b`](workflow-controller-ci2b.md) | 3, 4, 7 | run each test once |
| [`workflow-controller-ci2c`](workflow-controller-ci2c.md) | 5, 6, 8, 9, 10 | fixture snapshots and process starts |
| [`workflow-controller-ci2d`](workflow-controller-ci2d.md) | 11, 12, 13 and the automation of the timings refresh | after-merge and trigger policy |
| [`workflow-controller-ci2e`](workflow-controller-ci2e.md) | [#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322) | a runtime check replacing the static repo-reach heuristic |

Scope item 1 is split on purpose: this slice refreshes the timings once by hand (a one-off measurement) and ci2d automates the refresh. Item 14 ("not worth doing") needs no work. Each later slice depends on this one and is planned after it.

## What was done

- **Timings refreshed from real CI.** `tools/test_timings.json` was merged from three code pull request runs of `ci.yml` (43855824846, 43864443959 and 43879797542, oldest first, nine distinct shard records; the operator downloaded the artifacts) and went from 1,179 to 1,291 atoms, with the 113 Codex atoms measured at 2,097 s now in it. The untimed share of the CI-placed tests went from 9.9 percent (657 of 6,654) to 5 of 6,654.
- **A staleness ratchet.** `MAX_UNTIMED_TEST_SHARE` is 20 percent in `CommittedTimingsTest` (derivation in the [development guide](../guide/development.md#the-test-runner), "Staleness ratchet"); negative cases show it can fail and that `main`, `package` and excluded atoms are not counted.
- **One class split.** The refresh pushed `tests.test_codex_apply_regeneration.PlanRegenerationTest` (210.6 s over 10 tests) over the 200 s class ratchet, so it is split test-only into `PlanRegenerationTest` and `PlanRegenerationRecordTest`, five tests each (amendment 1, owner decision of 2026-11-16).
- **The contention was measured and no factor is justified** (amendment 2, owner decision of 2026-11-17); see the next section. `tools/test_shards.py` is unchanged.
- **The #324 follow-ups** are made on the [CI1b page](workflow-controller-ci1b.md) and in the development guide.
- **Pages and backlog.** The guides state the refresh date, the ratchet and the measured result; the four follow-up slices above are in `docs/backlog.yaml` and `docs/ROADMAP.md`.

## The measured contention

The planner multiplies a shard's estimate by `LANE_CONTENTION = 1.15` when both lanes have work, and the earlier figure was that the shard holding the acceptance matrix runs about 1.50 times slower than estimated. That figure is a shard-level one, so it was measured at the shard: each shard's observed `wall_seconds` over its uncontended estimate (the longer of its two lanes), from the shard records and plans of the three runs, with the refreshed timings. The historical plans name the original ten-test `PlanRegenerationTest` atom, so each was first mapped to both replacement atoms on the same shard (`split_historical_atoms` in `tests/test_test_shards.py`, one mapping for the ratios and for the rescoring below).

| run | matrix shard | other two shards |
| --- | --- | --- |
| 43855824846 | 0.69 | 1.15 and 1.18 |
| 43864443959 | 1.08 | 0.97 and 1.14 |
| 43879797542 | 1.07 | 0.93 and 0.85 |

The matrix shard was never above 1.15 and no suite was above 1.15 in two runs, so **no matrix-specific factor is justified on these three runs** and `LANE_CONTENTION = 1.15` stays for every suite. The earlier 1.50 came from the six-shard layout and is not visible at the shard level in the three-shard layout. The result is in-sample (the runs are folded into the timings that feed the estimate) and on three runs only: it does not claim the matrix is never slower, only that these runs do not qualify a constant. A later item may revisit it with more runs.

## The predicted effect

The planner balances exactly the numbers it reports, so two self-balanced plans always show a longest shard of about 1.00 times the mean, and comparing them shows only the estimator's level. The predicted gain is therefore the **base plan rescored with the after estimator**: the plan CI built at the base commit (`run_tests.py plan --profile ci --ci-placement`, plan digest `ef75968f`, three shards each estimated at 3,260.1 s), with the same atoms on each shard (the split class as both halves), scored with the refreshed timings through the unchanged estimator.

| plan | shard 0 (matrix) | shard 1 | shard 2 | longest | mean | longest over mean |
| --- | --- | --- | --- | --- | --- | --- |
| base plan, base estimator | 3,260.1 s | 3,260.1 s | 3,260.1 s | 3,260.1 s | 3,260.1 s | 1.00 |
| base plan, rescored with the refreshed timings | 4,092.6 s | 3,119.4 s | 3,253.5 s | 4,092.6 s | 3,488.5 s | 1.17 |
| new plan (`--ci-placement`, refreshed timings) | 3,493.9 s | 3,493.9 s | 3,493.9 s | 3,493.9 s | 3,493.9 s | 1.00 |

The predicted gain is **598.7 s (about 10 minutes) off the longest shard**: the old placement would have run at 1.17 times the mean under the refreshed estimates, and the new one is level at 3,494 s. The mean rises from 3,260 s to 3,494 s because the refreshed timings are higher (the 113 Codex atoms were underestimated).

Each shard's observed `wall_seconds` next to its estimate, before (the base timings, base plan unchanged) and after (the refreshed timings, split class mapped), for the runs the refresh used (every run used the base plan, so the estimate before is 3,260 s on each shard):

| run | shard | observed | before | observed over before | after | observed over after |
| --- | --- | --- | --- | --- | --- | --- |
| 43855824846 | 0 (matrix) | 2,467.0 s | 3,260.0 s | 0.76 | 4,092.6 s | 0.60 |
| 43855824846 | 1 | 3,132.8 s | 3,260.0 s | 0.96 | 3,119.4 s | 1.00 |
| 43855824846 | 2 | 3,344.8 s | 3,260.0 s | 1.03 | 3,253.5 s | 1.03 |
| 43864443959 | 0 (matrix) | 3,851.2 s | 3,260.0 s | 1.18 | 4,092.6 s | 0.94 |
| 43864443959 | 1 | 2,644.1 s | 3,260.0 s | 0.81 | 3,119.4 s | 0.85 |
| 43864443959 | 2 | 3,215.9 s | 3,260.0 s | 0.99 | 3,253.5 s | 0.99 |
| 43879797542 | 0 (matrix) | 3,820.5 s | 3,260.0 s | 1.17 | 4,092.6 s | 0.93 |
| 43879797542 | 1 | 2,519.7 s | 3,260.0 s | 0.77 | 3,119.4 s | 0.81 |
| 43879797542 | 2 | 2,391.0 s | 3,260.0 s | 0.73 | 3,253.5 s | 0.73 |

Across the three runs the shard that held the matrix ran from 2,467 s to 3,851 s and the others from 2,391 s to 3,345 s, so the imbalance the goal names (1.08 to 1.50 times the average) is large and varies from run to run; the observed spread of a single shard between runs (2,467 to 3,851 s) is larger than the estimator's change, which is why the gain above is a prediction and not a promise.

**Elapsed CI time**, one interval for both sides: the workflow run's `run_started_at` to the `completed_at` of its `tests-result` job. The 35 to 41 minute figure in the owner's notes below was measured at six shards, before CI3 moved CI to three, so the baseline here is the three three-shard code pull request runs the refresh used, measured the same way:

| run | head | elapsed | shard 0 | shard 1 | shard 2 | longest over shortest shard |
| --- | --- | --- | --- | --- | --- | --- |
| 43855824846 (baseline) | `989d74e` | 57 min 12 s | 41.6 min | 52.4 min | 56.0 min | 1.35 |
| 43864443959 (baseline) | `db4da6c` | 65 min 16 s | 64.4 min | 44.3 min | 53.8 min | 1.45 |
| 43879797542 (baseline) | `703c9e7` | 64 min 40 s | 63.9 min | 42.5 min | 40.1 min | 1.59 |
| 44062553881 (this pull request, refreshed timings) | `429f97f` | **55 min 41 s** | 47.1 min | 50.6 min | 54.5 min | 1.16 |

Shard times are the `tests (i)` jobs' `started_at` to `completed_at`. This pull request's run is 1.5 to 9.6 minutes shorter than each baseline run (6.7 minutes below their 62.4-minute mean) and its longest shard is 54.5 minutes against 56.0 to 64.4 (6.9 minutes below their 61.4-minute mean), close to the planner's predicted 598.7 s (10.0 minutes) but smaller. The shards are also more level (1.16 times longest over shortest against 1.35 to 1.59), and every shard finished under the planner's 3,494 s (58.2 minutes). This is one run against three, and a single shard varied by up to 23 minutes between the baseline runs, so the gain is within runner noise of a few minutes and is reported as measured, not as proven. PR CI is still far from the 20-minute target of [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317); the later slices (ci2b to ci2e) are what cut the work itself. The run tested `429f97f`; the self-review commit after it changes only a test helper and this page, not the timings or the planner.

## The owner's decision of 2026-11-16 (test-suite performance)

The owner widened this item so that the work covers the test suites themselves, not only sharding and Actions. The item keeps its order (after CI3).

### Measured findings

- PR CI grew from 24-27 min on 9-10 Nov to 35-41 min. Almost all of that time is the shard step itself. Checkout, setup, install and queueing take under 0.3 min each, so caching pip, the venv or the plan is not worth doing.
- The committed `tools/test_timings.json` is stale and lacks 113 Codex atoms that take 2,097 s. The longest shard is 1.08 to 1.50 times the average, which costs about 8 min.
- The acceptance-matrix shard runs 1.50 times slower than the planner estimates.
- The nine frozen conformance suites run twice per PR: in the managed `workflow-conformance` job and again in the shards (2,324 s).
- Four functional-review flows run twice: `tests/test_functional_lifecycle.py` (738 s) and the four `Scenario_scenario_functional_*` scenarios (737 s), 15.5% of the Controller test time.
- 434 tests of at least 5 s carry 73% of the 9,505 s Controller test time: the lifecycle family 45%, the Codex family 28%, the scenarios 9%.
- The slow tests spawn processes rather than sleep. One lifecycle step runs about 10 Python interpreters, each compiling about 30k lines of vendored Workflow code (`-B`, `controller/workflow_contract.py:719`), plus 60 to 90 git calls, about 3 s per step.
- Snapshot reuse misses: `restore_snapshot` in `tests/fixtures.py` refuses an in-use path, the keys include inputs that only the last step uses, reuse is switched off under the scenario kit, and `_ReviewCase.setUp` (`tests/test_review_job.py:121-129`) has no snapshot for about 126 tests at about 7 s each.
- Codex tests re-run a full job in loops: `RecoveryTest` 90 s, `OutMemberPlantTest` 61 s, `PlanBoundaryTest` 34 s, the plant loops 104 s, and one exact duplicate test 20 s.
- Waste outside the suite: 101 cancelled CI runs since 12 Nov cost about 5,500 runner-minutes; documentation-only merges to main still run all shards (about 155 runner-min); the after-merge determinism job (12.6-15.7 min) and mutations job (11.3-17.2 min) delay releases.

### Scope the owner approved

All four open decisions were answered yes. Each item is one mechanism; the estimate is the expected saving on PR wall time.

1. Refresh `tools/test_timings.json`, automate its refresh, and give unknown atoms better estimates (5-10 min, very low risk).
2. A per-suite contention factor, or keep the acceptance matrix apart (2-5 min).
3. Run the four functional flows once on PRs (about 4 min).
4. Stop running the frozen conformance suites inside the shards, because the managed `workflow-conformance` job already gates them (3-8 min, medium risk). `tests-result` loses its coverage proof for those suites; the plan must record how the proof is kept: the managed job is the gate.
5. Fix the snapshot-reuse misses (4-7 min).
6. Snapshot `_ReviewCase.setUp` (2-3 min).
7. Cut the Codex loops and the duplicate test (about 1 min).
8. Measure first, then maybe reuse bytecode for the vendored Workflow interpreters (3-8 min if confirmed, medium-high risk because it touches the production isolation path). One `-X importtime` measurement on a runner decides.
9. Drop the extra `next_action` per test step and cache `inspect()` (about 1 min).
10. Put tests that share a snapshot key on the same shard (about 1 min).
11. After the merge, split the mutation job into 2-3 jobs within the six-runner cap (release wait from about 17 to 6-9 min).
12. Skip the test shards on main for documentation-only commits; keep the managed conformance job.
13. Do not run the full suite on every intermediate Draft-PR push; run it when the PR is ready for review and on main.
14. Not worth doing: pip or venv caching, plan caching, trimming setup steps.

### Projection (inferred)

After items 1 to 10 the PR wall time is about 30-39 min at three shards, which still misses the 15-20 min target. Reaching 20 min needs about 60 more runner-minutes of work removed at three shards: either more runners, or cutting how many interpreters each lifecycle step starts (running most Workflow queries in-process and keeping one module with real subprocesses).

### Small notes

- `docs/guide/ci-and-releases.md` said the acceptance matrix is the floor of the longest shard; CI2 corrected it (the Controller lane is always the longer one at three shards).
- The managed conformance job also runs on documentation-only PRs, so their checks finish after 12-19 min.
- `test_buildinfo.EditableModeEarlyReturnTest` is skipped on CI because setuptools is missing.

## Out of scope

The duplicate runs, the fixture snapshots, the after-merge and trigger policy and the runtime reach check (the four slices above), `CI_MAX_SHARDS` (the owner's CI3 choice: raising it needs an owner answer) and the timing flakes (FX1).

## Planning rules

The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
