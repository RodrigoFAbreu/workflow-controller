# CI2c: fixture snapshots and process starts

Item id: `workflow-controller-ci2c`.

Not yet planned. A slice of [CI2](workflow-controller-ci2.md), split at its planning. Depends on CI2. Sources: scope items 5, 6, 8, 9 and 10 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: large; it may need splitting again at planning.

## Scope

One mechanism: make the slow tests start fewer processes and rebuild fewer fixtures.

5. Fix the snapshot-reuse misses (4 to 7 min): `restore_snapshot` in `tests/fixtures.py` refuses an in-use path, the keys include inputs that only the last step uses, and reuse is switched off under the scenario kit.
6. Snapshot `_ReviewCase.setUp` (2 to 3 min): `tests/test_review_job.py:121-129` has no snapshot for about 126 tests at about 7 s each.
8. Measure first, then maybe reuse bytecode for the vendored Workflow interpreters (3 to 8 min if confirmed, medium-high risk because it touches the production isolation path, `controller/workflow_contract.py:719`, `-B`). One `-X importtime` measurement on a runner decides.
9. Drop the extra `next_action` per test step and cache `inspect()` (about 1 min).
10. Put tests that share a snapshot key on the same shard (about 1 min).

One lifecycle step runs about 10 Python interpreters, each compiling about 30k lines of vendored Workflow code, plus 60 to 90 git calls, about 3 s per step. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
