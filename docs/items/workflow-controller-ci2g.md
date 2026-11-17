# CI2g: shard affinity for tests that share a snapshot

Item id: `workflow-controller-ci2g`.

Not yet planned. A slice of [CI2](workflow-controller-ci2.md), split out of [CI2c](workflow-controller-ci2c.md) at its planning because it is a planner change (`tools/test_shards.py`), not a fixture. Depends on [CI2c](workflow-controller-ci2c.md): it only pays once the snapshot keys are the trimmed ones. Sources: scope item 10 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: put the tests that share a snapshot key on the same shard (about 1 min). A snapshot is kept per test process, so a row is built once on each shard that runs a test reaching it; CI2c's counters show the saving is small until those tests sit on one shard. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
