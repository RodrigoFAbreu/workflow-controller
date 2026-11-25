# CI2g: shard affinity for tests that share a snapshot

Item id: `workflow-controller-ci2g`.

Status: implemented on the milestone branch (four checkpoints); plan: [CONTROLLER_CI2G_PLAN.md](../ai-workflow/CONTROLLER_CI2G_PLAN.md) (revision 2). A `ci:` change that edits `tools/`, tests and pages only; it publishes no release. A slice of [CI2](workflow-controller-ci2.md), split out of [CI2c](workflow-controller-ci2c.md) at its planning because it is a planner change (`tools/test_shards.py`), not a fixture. Depends on [CI2c](workflow-controller-ci2c.md): it only pays once the snapshot keys are the trimmed ones. Sources: scope item 10 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: put the tests that share a snapshot key on the same shard (about 1 min). A snapshot is kept per test process, so a row is built once on each shard that runs a test reaching it; CI2c's counters show the saving is small until those tests sit on one shard. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## What was done

- **Measurement (CP1).** With `WFC_SNAPSHOT_REPORT` set, each report line gains a `users` object from a snapshot key label to the ids of the tests that reached it. Nothing reads it; no decision depends on it.
- **Placement (CP2).** `build_plan` places a group of `SNAPSHOT_AFFINITY` as one unit of the members that fit `AFFINITY_MAX_SHARE` (0.5) of the mean shard load, splits the smallest unit back while there are fewer units than shards, and keeps the count of the formula. An empty table gives the plan it gave before.
- **The table (CP3).** Four groups, measured, committed in `tools/test_shards.py` and guarded by `CommittedSnapshotAffinityTest`.
- **The pages (CP4).** [Placing the classes that share a snapshot](../guide/development.md#placing-the-classes-that-share-a-snapshot) states the mechanism, the bound, that it is advisory and how to refresh it.

## The table

| group (key label) | classes | modules |
| --- | --- | --- |
| `to_38e#76299b5276` | 5 | `test_protocol_job` |
| `to_38e#bb58c678ce` | 3 | `test_functional_codex_route`, `test_functional_select` |
| `to_acceptance#bae3c65c23` | 8 | `test_pr_report` (7), `test_protocol_lifecycle` (1) |
| `to_manual_plan_review#596d6480e1` | 26 | the review modules and `test_protocol_job`, `test_usage_record_job` |

## Measured

The 21 modules that reach a row, CI-profile plan, 3 shards, 1204 tests, on the local machine, one run each, table emptied then filled, counts summed over the shard processes:

| row | `built` before | `built` after |
| --- | --- | --- |
| `to_38e` | 12 | 8 |
| `to_acceptance` | 10 | 9 |
| `to_refused_acceptance` | 7 | 6 |
| `to_manual_plan_review` | 4 | 4 |
| `to_plan_gate` | 1 | 1 |
| total | 34 | 28 |

`restored` went from 208 to 213. The planner's estimates are the same before and after (1327.398, 1327.398 and 1327.397 s per shard); measured wall time went from 499.2 s to 492.2 s and the balance ratio from 1.05 to 1.10. These are local figures from one run each, not a runner's: the saving in CI depends on runner contention (see [the CI2 page](workflow-controller-ci2.md)) and is not claimed here. The owner's estimate was about one minute of pull request time; the local wall-time drop is about 7 s, within run-to-run noise, so no pull request saving is claimed.

Commands, the same before and after (the table emptied by passing it empty for the first run):

```
python3 tools/run_tests.py plan --profile ci --ci-placement --output plan.json <modules>
WFC_SNAPSHOT_REPORT=report.jsonl python3 tools/run_tests.py --replay plan.json --jobs 3 --results-dir res
```
