# CL1b: A `verify` check that warns and then fails above a limit

Item id: `workflow-controller-cl1b`.

## Roadmap row (moved verbatim)

Complexity: medium. A `verify` check that warns and then fails above a limit

Needs: CL1a

Section: [Clean-up milestones](../roadmap-archive.md#at-a-glance)

## Planning notes

From [#130](https://github.com/RodrigoFAbreu/workflow-controller/issues/130), wanted step 2: report completed-work-item files still in the working tree past the rule, and untracked review folders older than N completed items; warn first, then fail above a limit.

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
