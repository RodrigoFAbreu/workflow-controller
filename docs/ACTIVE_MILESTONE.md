# Active Milestone

## Status

**Complete.** `workflow-controller-determinism-parallel` (issue
[#126](https://github.com/RodrigoFAbreu/workflow-controller/issues/126), pull request #142) was accepted on 2026-10-24
by gate policy (digest `bae758b4f4ce`). The whole-catalogue determinism check now runs its two passes at the same time,
each bounded by a deadline, a grace period and a sweep that leaves no process behind. On four CPUs the median wall time
fell from 692 s to 368 s, a ratio of 0.53 against the limit of 0.70. The five functional flows passed at head `a451d7f`
(run `c126-functional-round1`). The plan is archived at
`docs/milestones/completed/workflow-controller-determinism-parallel.md`; the findings record is
`docs/milestones/completed/workflow-controller-determinism-parallel-findings.md`.

The real effect on CI shows only in the `after-merge-determinism` job of the first `main` run after the merge. It is
recorded in the documentation pull request that follows. This is a `ci:` change, so it has no release.

## Next action

Plan the next item in `docs/ROADMAP.md` with `/milestone-plan`: **CU3**, then the harness-neutral roles epic.
