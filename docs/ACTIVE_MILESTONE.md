# Active Milestone

## Status

**Complete.** `workflow-controller-codex-tester-bridge` (C10g-3b, issue
[#222](https://github.com/RodrigoFAbreu/workflow-controller/issues/222)) is implemented (CP1, CP2, the self-review
fixes and the functional review) and accepted by policy.
`controller/codex_bridge.py` adds `build_bridge`, `check_bound` and `prompt`, sharing `clone_candidate`,
`bound_differences` and `tester_prompt` with the Claude tester. Nothing calls the bridge until C10g-3b2. The change is a
`chore:` with no release. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_BRIDGE_PLAN.md`.

## Next action

`/milestone-plan` for C10g-3b2 (the Codex tester's launch and results, [#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)).
