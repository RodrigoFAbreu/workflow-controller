# Active Milestone

## Status

**Complete.** `workflow-controller-scenario-base` (SC1, issue
[#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): the scenario tests now measure from the checkout instead of an old pinned base commit, and a
sandbox-free guard checks that every file the fixture commits is classified. Only tests and one development-guide
paragraph changed; the squash commit is `test:`, so nothing is released. The plan is archived at
`docs/milestones/completed/workflow-controller-scenario-base.md`.

## In progress: `workflow-controller-worker-launch` (H3)

Plan: `docs/ai-workflow/CONTROLLER_WORKER_LAUNCH_PLAN.md`. CP1 (the launcher interface, the Claude Code launcher and the
call site in `controller/job.py`) and CP2 (the development and harness guides, the runtime guide sentence and the roadmap row)
are implemented and verified; the full verification and the implementation review bundle are next.

## Next action

`workflow-controller-worker-launch` is waiting for its implementation review. After it, H10 is next in the
harness-neutral roles epic in `docs/ROADMAP.md`; run `/milestone-plan` for it.
