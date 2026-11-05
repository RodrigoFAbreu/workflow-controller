# CL1c: A safe prune command

Item id: `workflow-controller-cl1c`.

## Roadmap row (moved verbatim)

Complexity: medium. A safe prune command: dry run by default, never an open work item or anything the state file references

Needs: CL1b

Section: [Clean-up milestones](../roadmap-archive.md#at-a-glance)

## Planning notes

From [#130](https://github.com/RodrigoFAbreu/workflow-controller/issues/130), wanted step 3: apply the rule safely: dry run by default, one commit, never touching an open work item, never deleting anything the state file still references; historical records that docs link to keep their links working (wanted step 4).

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
