# Active Milestone

## Status

**Complete.** `workflow-controller-notification-fix` (NF1, pull request #153; closes issue #149) was accepted on
2026-10-25 by gate policy (digest `bae758b4f4ce`). The review and functional-review waits now send `review_wait`, a
low-severity info event, instead of `user_gate`; approvals, acceptance and merges that need the owner stay `user_gate`.
The six functional flows passed at head `5ffc546`. The plan is archived at
`docs/milestones/completed/workflow-controller-notification-fix.md`; the findings record is
`docs/milestones/completed/workflow-controller-notification-fix-findings.md`.

## Next action

Plan the next item in `docs/ROADMAP.md` with `/milestone-plan`: **RB1** (a configurable external-reviewer brief,
issue #150), then the harness-neutral roles epic.
