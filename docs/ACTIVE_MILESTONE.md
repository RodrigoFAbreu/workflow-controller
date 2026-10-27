# Active Milestone

## Status

**Complete.** `workflow-controller-worker-launch` (H3, issue
[#191](https://github.com/RodrigoFAbreu/workflow-controller/issues/191)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the worker launch now sits behind the harness adapter. `controller/worker_launch.py` holds the
`WorkerLauncher` interface, the Claude Code launcher (which calls `worker.launch` unchanged) and the registry of launchers,
and `controller/job.py` resolves the launcher from the routed harness before the usage gate. Behaviour is unchanged, so the
squash commit is `chore:` and nothing is released. The plan is archived at
`docs/milestones/completed/workflow-controller-worker-launch.md`.

## In progress

`workflow-controller-reviewer-independence` (H10,
[#194](https://github.com/RodrigoFAbreu/workflow-controller/issues/194)), plan revision 3 approved. CP1 is complete: the
mixed-lane stop now names the stage's local review role, `reviewer.harness` and their families
(`review_mode.mixed_lane_reason`, used in `job._choose_review`), verified by `tests.test_review_independence`. CP2 (guide,
troubleshooting page, roadmap) is next.

## Next action

Run `/milestone-implement` for CP2 of `workflow-controller-reviewer-independence`.
