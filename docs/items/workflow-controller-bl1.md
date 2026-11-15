# BL1: owner-requested backlog and docs changes run through the Controller

Item id: `workflow-controller-bl1`.

Not yet planned. Owner decision 2026-11-15. Priority: medium-high. Complexity: medium. May need splitting in planning. Follows [AO1](workflow-controller-ao1.md) in order.

## Why

The orchestrator is only for escalation, monitoring and fixing problems in the workflow or the Controller. Pull requests are opened by the Controller.

Today the Controller opens only milestone pull requests. Owner-requested backlog items, item pages and similar docs changes have been done by the orchestrator's helper agents from separate clones. That caused parallel backlog pull requests that conflicted on `docs/backlog.yaml` and `docs/ROADMAP.md`, because every new item inserts a unique order and shifts later ones.

## Scope

One mechanism, to decide in planning: a way to give the Controller a backlog or docs change as a small work item or command (for example adding an item with its page, regenerating the roadmap, opening and merging the pull request), so that changes are serialized, reviewed by the same gates and carried out by the Controller.

Questions for planning:

- Does a docs-only milestone type exist or fit? See the D1 documentation milestone ([D1](workflow-controller-d1.md)) and [Milestone branches and pull requests](../guide/milestone-branches.md).
- Does `scripts/workflow_backlog.py` need an `add` command that handles order shifting?

The sizing rule (at most one new mechanism, releasable alone) is on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
