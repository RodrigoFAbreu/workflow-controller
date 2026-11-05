# CL1a: Retention of finished work-item files

Item id: `workflow-controller-cl1a`.

## Roadmap row (moved verbatim)

Complexity: low. Retention of finished work-item files (registry, audit, old plans, fixtures): the written rule and its documentation, owned by the Workflow and followed by the Manager and the Controller ([#130](https://github.com/RodrigoFAbreu/workflow-controller/issues/130), [workflow#84](https://github.com/RodrigoFAbreu/workflow/issues/84), [workflow-manager#90](https://github.com/RodrigoFAbreu/workflow-manager/issues/90)); CL1 is split into five items (owner decision, 2026-10-23)

Needs: CU3

Section: [Clean-up milestones](../roadmap-archive.md#at-a-glance)

## Planning notes

Added at A2b from the issue [#130](https://github.com/RodrigoFAbreu/workflow-controller/issues/130) (the row is moved verbatim above). The issue's wanted list is one small releasable step each: (1) a retention rule, written once in the Workflow docs, for a work item that reached `MILESTONE_COMPLETE`: which files stay in the tree (the decision record, the dispositions), which move to a single archive (a tag, a release asset or an `archive/` branch, not the working tree) and which are deleted; (2) a `verify` check (CL1b); (3) a safe command (CL1c); (4) historical records that docs link to keep their links working (a redirect stub or an updated link, checked by `check_docs`). Not in scope: changing what a review records while the work item is open. The rule is identical in the three repositories (the Workflow owns it; the Manager and the Controller follow it).
