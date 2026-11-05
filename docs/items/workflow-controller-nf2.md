# NF2: Notifications: a spent checks_pending wait is an info event, not lane_blocked

Item id: `workflow-controller-nf2`.

## Roadmap row (moved verbatim)

Complexity: low. Notifications (a `fix:` patch; [#170](https://github.com/RodrigoFAbreu/workflow-controller/issues/170) closed by its pull request): a spent `checks_pending` wait sends an info event instead of `lane_blocked` (CRITICAL); it becomes `lane_blocked` only when a check fails or the same wait lasts much longer than a fixed limit, so a critical event means something is wrong ([#170](https://github.com/RodrigoFAbreu/workflow-controller/issues/170); owner decision 2026-10-25). The upgrade duplicate guard (`_superseded_handled`) must keep working for the changed type

Needs: NF1

Section: [Review modes](../roadmap-archive.md#at-a-glance)
