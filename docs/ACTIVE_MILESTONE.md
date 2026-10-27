# Active Milestone

## Status

**Complete.** `workflow-controller-reviewer-family` (H2, issue
[#185](https://github.com/RodrigoFAbreu/workflow-controller/issues/185)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the review mode now derives the workers' provider family from the routing
(`review_mode.worker_families`) instead of the constant `WORKER_FAMILY`; mixed families stop with `review_mode_unavailable`,
and with today's registry no outcome changes. The squash commit is `chore:`, so nothing is released. The plan is archived at
`docs/milestones/completed/workflow-controller-reviewer-family.md`.

## Next action

No milestone is active. Next is SC1 in `docs/ROADMAP.md`
([#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188), the scenario-test fix), then H3 of the harness-neutral roles epic;
run `/milestone-plan` for it.
