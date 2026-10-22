# Active Milestone

## Status

**In progress.** `workflow-controller-ci-time` (C2b, `docs/ROADMAP.md`): bring a pull request's CI back to 15-20
minutes and fix the `FollowCommandTest` flake ([#119](https://github.com/RodrigoFAbreu/workflow-controller/issues/119),
[#121](https://github.com/RodrigoFAbreu/workflow-controller/issues/121)). Plan revision 8 is approved by policy; the
plan is `docs/ai-workflow/CONTROLLER_CI_TIME_PLAN.md`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record.

| Checkpoint | State |
| --- | --- |
| CP1 | Complete. The two timer-dependent `FollowCommandTest` cases (`test_a_running_run_is_chosen`, the orphan-job case) end the run from the follower's first heartbeat instead of a 0.3-second timer. Each has a delayed-follower regression (`test_a_follower_started_late_...`) that fails on the old timer shape in a scratch copy and passes on the new one; the class passes pinned to two CPUs with six parallel copies. |
| CP2-CP6 | Not started. |

The previous milestone, `workflow-controller-automated-functional-review` (C10c, released as 1.18.0), is archived at
`docs/milestones/completed/workflow-controller-automated-functional-review.md`.
