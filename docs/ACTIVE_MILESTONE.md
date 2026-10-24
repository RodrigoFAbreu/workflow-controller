# Active Milestone

## Status

**In progress.** `workflow-controller-notification-fix` (NF1: review and functional-review waits send `review_wait`, an
info event, not `user_gate`; closes issue #149). Plan: `docs/ai-workflow/CONTROLLER_NOTIFICATION_FIX_PLAN.md` (revision
10, approved by gate policy). Checkpoints: CP1 (the event type, the mapping and the superseded-notice check) and CP2
(the guides and the roadmap) done. Self-review added the plan's missing production-path tests (the Controller-coded
gates over a row-14 decision stay `user_gate`; a production `step` in a new run after the upgrade sends no second notice
for a delivered `user_gate` and one `review_wait` after a failed or rejected one, and `resume` replays nothing) and ran
the superseded-notice and recovery checks for a tester stop too. The implementation review bundle is next.

The previous milestone, `workflow-controller-cleanup-3b` (CU3b), was accepted on 2026-10-24 (pull request #148); its
archive copy is in `docs/milestones/completed/`.

## Next action

The implementation review of revision 1's bundle. RB1 (issue #150) follows, then the
harness-neutral roles epic (H1 and H6 first).
