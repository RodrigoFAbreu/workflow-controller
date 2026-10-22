# Active Milestone

## Status

**In progress.** `workflow-controller-claude-build-verify` (C10e, `docs/ROADMAP.md`; issues
[#113](https://github.com/RodrigoFAbreu/workflow-controller/issues/113) and
[#114](https://github.com/RodrigoFAbreu/workflow-controller/issues/114)) has implemented all four checkpoints
(CP1-CP4) and awaits implementation review.

- `workflow-controller verify-claude [--force]` runs the live sandbox enforcement probes against the `claude` a
  launch would run and records its digest in `verified-claude-builds.json` in the runtime root only when every probe
  holds and the build's bytes are unchanged by the run. It never touches the settings file.
- The static probe and the launch admit the built-in `VERIFIED_NATIVE_BUILDS` plus the recorded digests, and both
  examine and execute one resolved real path.
- The plan is `docs/ai-workflow/CONTROLLER_CLAUDE_BUILD_VERIFY_PLAN.md` (revision 3, approved at `e1e10e2`); the
  base commit is `679b34c`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of the phase.

The previous milestone, C2b (`workflow-controller-ci-time`), is archived at
`docs/milestones/completed/workflow-controller-ci-time.md`.

## Next action

Implementation review of C10e, then acceptance and the 1.19.0 release. Then A2, the backlog.
