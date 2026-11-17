# CI2f: process starts per lifecycle step

Item id: `workflow-controller-ci2f`.

Not yet planned. A slice of [CI2](workflow-controller-ci2.md), split out of [CI2c](workflow-controller-ci2c.md) at its planning because it is its own mechanism and touches the production isolation path. Depends on CI2. Sources: scope items 8 and 9 of the owner's decision of 2026-11-16 on the CI2 page, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: a lifecycle step starts fewer processes.

8. Measure first, then maybe reuse bytecode for the vendored Workflow interpreters (3 to 8 min if confirmed, medium-high risk because it touches the production isolation path, `controller/workflow_contract.py:719`, `-B`). One `-X importtime` measurement on a runner decides.
9. Drop the extra `next_action` per test step and cache `inspect()` (about 1 min).

One lifecycle step runs about 10 Python interpreters, each compiling about 30k lines of vendored Workflow code, plus 60 to 90 git calls, about 3 s per step. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
