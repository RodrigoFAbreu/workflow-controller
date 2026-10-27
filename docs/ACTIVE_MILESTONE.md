# Active Milestone

## Status

**Complete.** `workflow-controller-reviewer-independence` (H10, issue
[#194](https://github.com/RodrigoFAbreu/workflow-controller/issues/194)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the mixed-lane stop of `review_mode_unavailable` now names the stage's local review role,
`reviewer.harness` and their families, and says whether they are of one family (`review_mode.mixed_lane_reason`, used in
`job._choose_review`). `manual` and every single-family lane are unchanged. The change is `chore:`, so nothing is released.
The plan is archived at `docs/milestones/completed/workflow-controller-reviewer-independence.md`.

## Next action

Next incomplete milestone in `docs/ROADMAP.md`: H9 (a fallback chain per role). Run `/milestone-plan` to start planning.
