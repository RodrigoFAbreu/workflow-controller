# NF1: Notifications: review and functional-review waits are info events, not user_gate

Item id: `workflow-controller-nf1`.

## Roadmap row (moved verbatim)

Complexity: low. Notifications (accepted 2026-10-25 by gate policy, pull request #153 `fix: review and functional-review waits send review_wait, an info event, not user_gate (#149)`, closing #149): the review and functional-review waits send `review_wait`, a low-severity info event, `user_gate` stays for approvals, acceptance and merges that need the owner, and a normal merge is an info event at normal severity; the pending-checks wait is unchanged ([#149](https://github.com/RodrigoFAbreu/workflow-controller/issues/149); owner decision 2026-10-24)

Needs: CU3b

Section: [Notifications](../roadmap-archive.md#at-a-glance)
