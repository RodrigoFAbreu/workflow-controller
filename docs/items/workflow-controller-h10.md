# H10: The independence rule, read as the Workflow's `distinct_reviewer_models` pair

Item id: `workflow-controller-h10`.

## Roadmap row (moved verbatim)

Complexity: low to medium ([#194](https://github.com/RodrigoFAbreu/workflow-controller/issues/194)). The independence rule, read as the Workflow's `distinct_reviewer_models` pair (the stage's local review role against the external reviewer, not a worker against its reviewer) (the mixed-lane stop of `review_mode_unavailable` now names the stage's local review role, `reviewer.harness` and their families, and says whether they are of one family; `review_mode.mixed_lane_reason` is used in `job._choose_review`, `manual` and every single-family lane are unchanged; a `chore:` change with no release; the per-stage replacement of the set comparison, which would make a mixed lane usable, is the follow-up for H4 or H9 and an owner decision)

Needs: H1

Section: [Adapters and backlog](../roadmap-archive.md#at-a-glance)
