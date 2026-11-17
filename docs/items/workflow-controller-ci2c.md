# CI2c: fixture snapshot reuse

Item id: `workflow-controller-ci2c`.

Status: implemented on the milestone branch (four checkpoints); plan: [CONTROLLER_CI2C_PLAN.md](../ai-workflow/CONTROLLER_CI2C_PLAN.md) (revision 2). Complexity: medium. A slice of [CI2](workflow-controller-ci2.md). Depends on CI2. Sources: scope items 5 and 6 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) (the 20-minute target, stays open). A `ci:` change that edits tests, test helpers and pages only; it publishes no release.

By the sizing rule (one new mechanism per releasable item) the scope items that are not the snapshot mechanism moved out at planning (owner decision O1 of the plan, no owner answer to keep them here): scope items 8 and 9 are [`workflow-controller-ci2f`](workflow-controller-ci2f.md) (process starts per lifecycle step) and scope item 10 is [`workflow-controller-ci2g`](workflow-controller-ci2g.md) (shard affinity for tests that share a snapshot).

## What was done

- **Counters (CP1).** `tests/fixtures.py` counts the outcome of every `take_snapshot` and `restore_snapshot` call by row name (`snapshot_counts()`); `WFC_SNAPSHOT_REPORT` names a file and an `atexit` hook appends one JSON line per process (off by default). `Lifecycle.reach` counts `kit_drove`.
- **A key holds what the drive reads (CP2).** `snapshot_inputs()` is a mapping and `reach(name, drive, *, late=())` leaves the `late` parts out of the key; `life.late(name)` raises while a drive that declared the part late runs. `checks` is late for `to_plan_gate` and `to_acceptance`, `refused` for `to_plan_gate`; the guard did not trip in the three lifecycle modules. `LoopLifecycle`'s `world` stays in the key.
- **The review case reaches its row once (CP3).** `_ReviewCase.setUp` goes through `reach("to_manual_plan_review", ...)`; the clock the drive wrote its records at is saved with the tree (`review_now`). One class needed `SHARE_SNAPSHOT = False`: `AdoptedPolicyLoopTest` in `tests/test_review_same_provider.py`. Its seed adopts a lowered gate policy (`run_workflow_seed` patched before `super().setUp()`), which the snapshot key (`release`, `report`) does not carry, so a default-policy tree and a lowered-policy tree shared one key in either direction. The defect was found by the pull request's CI (`validate / tests (0)`), not by the `taskset` run the plan relied on; it also explains the `DefaultPolicyRefusalTest` failure of the self-review full run, which was first put down to cross-shard interference.
- **The path in use is inherent.** A tree records its own path, so two live copies of one row in one test cannot exist and the second drives fresh: 4 of the tests in the two measured modules at the base, 0 in the third. This is correct and not fixed.
- **The scenario kit stays as it is.** `kit_drove` is 0 in the three modules.

## Measured figures

Counts are summed over the shard processes (`python3 tools/run_tests.py` of `tests.test_protocol_lifecycle`, `tests.test_protocol_job` and `tests.test_functional_codex_route` in 8 shards, `WFC_SNAPSHOT_REPORT` on), so `built` and `missing` are larger than the single-process figures of the plan (14 built and 8 restored in `tests.test_protocol_lifecycle`; 6 built, 32 restored and 4 refused in the other two together).

| row | baseline (CP1) built / restored | after CP2 built / restored | in use |
| --- | --- | --- | --- |
| `to_acceptance` | 11 / 1 | 10 / 2 | 0 |
| `to_refused_acceptance` | 8 / 0 | 8 / 0 | 0 |
| `to_plan_gate` | 1 / 4 | 1 / 4 | 0 |
| `to_38e` | 14 / 24 | 14 / 24 | 4 |
| kit (`kit_drove`) | 0 | 0 | |

The gain of CP2 is small: each shard process builds a row once whatever the key, so the saving needs the tests that share a row on one shard, which is [CI2g](workflow-controller-ci2g.md).

The review case (`tests.test_review_job`, 84 tests, 5 shards, `python3 tools/run_tests.py tests.test_review_job`, this machine): elapsed 54.6 s before and 37.0 s after CP3; `to_manual_plan_review` built 6, restored 74 (no baseline counter existed). Under `taskset -c 0,1` with two parallel copies both passed in 172 s each.

**Caveat: local figures.** All numbers are from this machine, not a CI runner, and a single run each; the runner is slower and noisier (a single shard varied by up to 23 minutes between runs of CI2), so the saving on a pull request is not measured here and is read from the first pull request run after the merge. [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) stays open.

## Planning rules

The rules that apply (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
