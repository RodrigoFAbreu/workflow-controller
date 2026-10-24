# Active Milestone

## Status

**In progress.** `workflow-controller-notification-fix` (NF1: review and functional-review waits send `review_wait`, an
info event, not `user_gate`; closes issue #149). Plan: `docs/ai-workflow/CONTROLLER_NOTIFICATION_FIX_PLAN.md` (revision
10, approved by gate policy). Checkpoints: CP1 (the event type, the mapping and the superseded-notice check) and CP2
(the guides and the roadmap) done; the full verification and the implementation review bundle are next.

The previous milestone, `workflow-controller-cleanup-3b` (CU3b), was accepted on 2026-10-24 (pull request #148); its
archive copy is in `docs/milestones/completed/`.

## Next action

Run `/milestone-implement workflow-controller-notification-fix` to enter self-review and prepare the bundle. RB1 (issue #150) follows, then the
harness-neutral roles epic (H1 and H6 first).
