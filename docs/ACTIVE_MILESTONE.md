# Active Milestone

## Status

**Complete.** `workflow-controller-worker-launch` (H3, issue
[#191](https://github.com/RodrigoFAbreu/workflow-controller/issues/191)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the worker launch now sits behind the harness adapter. `controller/worker_launch.py` holds the
`WorkerLauncher` interface, the Claude Code launcher (which calls `worker.launch` unchanged) and the registry of launchers,
and `controller/job.py` resolves the launcher from the routed harness before the usage gate. Behaviour is unchanged, so the
squash commit is `chore:` and nothing is released. The plan is archived at
`docs/milestones/completed/workflow-controller-worker-launch.md`.

## Next action

No milestone is active. Next is H10 of the harness-neutral roles epic in `docs/ROADMAP.md`; run `/milestone-plan` for it.
