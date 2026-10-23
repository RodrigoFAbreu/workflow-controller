# Active Milestone

## Status

**In progress.** `workflow-controller-sandbox-layout` (C10f, `docs/ROADMAP.md`; issue
[#129](https://github.com/RodrigoFAbreu/workflow-controller/issues/129)), plan revision 4 approved by gate policy.
`verify-claude` refuses Claude Code 2.1.304 and later because the tester's settings allow writes below a denied path.
The fix is a layout in which no allowed write location lies below a denied one: the writable locations live in
`<sandbox>/writable/<name>/` and the clone holds a link to each. Plan: `docs/ai-workflow/CONTROLLER_SANDBOX_LAYOUT_PLAN.md`.
Pull request title: `fix: lay out the functional tester's sandbox so Claude Code 2.1.304 and later pass verify-claude (#129)`.

| Checkpoint | State |
| --- | --- |
| CP1 -- the layout: settings invariant, external folders with links, the walk | complete (narrow checks green: sandbox, enforcement, review and claude-build tests) |
| CP2 -- the probes, the fake tester and the verification | pending |
| CP3 -- live measurement on the installed Claude builds | pending |
| CP4 -- documentation, roadmap and the whole suite | pending |

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record of checkpoint state. The previous milestone (C10e,
`workflow-controller-claude-build-verify`) is archived at `docs/milestones/completed/workflow-controller-claude-build-verify.md`.

## Next action

Run `/milestone-implement workflow-controller-sandbox-layout` for the next checkpoint (CP2). After C10f: #126, **CU3** and **A2** (see `docs/ROADMAP.md`).
