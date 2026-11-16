# CI2: CI follow-ups, the 20-minute PR CI target, the test suites and the review follow-ups of CI1a to CI1c

Item id: `workflow-controller-ci2`.

Not yet planned. Decide how to bring PR CI toward the 20-minute target and close the open CI follow-ups. Sources: [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322), [#324](https://github.com/RodrigoFAbreu/workflow-controller/issues/324). Complexity: large; widened on 2026-11-16 to cover the test suites themselves (see [Test-suite performance](#test-suite-performance-owner-decision-2026-11-16)); split at planning.

## Where CI stands

CI1b and CI1c measured no gain: PR CI took 27 min 23 s against a baseline of 27 to 30 minutes. The floor is the `acceptance_matrix` conformance atom, about 21.5 minutes with contention, so splitting or rebalancing the other shards cannot reach 20 minutes.

## Options (one mechanism each)

- A larger runner: needs an owner billing decision.
- Work on the `acceptance_matrix` atom itself (the floor).
- Replace the CI1a static repo-reach heuristic in `tests/test_ci_changes.py` with a runtime check ([#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322)): a suite run with the tree unreadable, or a hash before and after.
- The CI1b review follow-ups ([#324](https://github.com/RodrigoFAbreu/workflow-controller/issues/324)), a small documentation change: reword the item page's pre-plan summary, say "blends" for the next merge, add the two missing D4 rule parts and the "base without test methods" wording to the development guide, and record the fixture keys and the derivation inputs.

## Test-suite performance (owner decision 2026-11-16)

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

- `docs/guide/ci-and-releases.md` wrongly says the acceptance matrix is the floor of the longest shard; the measurements show the Controller lane is always the longer one.
- The managed conformance job also runs on documentation-only PRs, so their checks finish after 12-19 min.
- `test_buildinfo.EditableModeEarlyReturnTest` is skipped on CI because setuptools is missing.

### Planning

The work is sized for splitting at planning. By the sizing rule (at most one new mechanism per releasable item) a suggested split is: timings and balance (items 1, 2, 10), duplicate-run removal (3, 4, 7), fixture snapshots (5, 6, 8, 9), and after-merge and trigger policy (11, 12, 13). Plan it after CI3, because the six-runner cap and the three shards come from CI3. The Controller carries it out.

## Out of scope

The timing flakes (FX1) and raising `CI_MAX_SHARDS` without an owner answer.

## Not yet planned

Not yet planned. The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
