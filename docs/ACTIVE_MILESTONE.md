# Active Milestone

## In progress

`workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)), plan
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. CP1 done: `controller/codex_tester.py` runs the launch
controls and `codex exec` and classifies the outcome; `Built` records the credential digest, model and effort. CP2
done: `run_tester` also collects the readings and sub-agent rollouts, copies the credential back and removes the
credential copy on every path, and returns the Claude launch's result shape. CP3 (live test, documents) remains. The text below is the previous
milestone's record and is replaced by CP3.

## Status

**Complete.** `workflow-controller-codex-tester-bridge` (C10g-3b, issue
[#222](https://github.com/RodrigoFAbreu/workflow-controller/issues/222)) is implemented (CP1, CP2, the self-review
fixes and the functional review) and accepted by policy.
`controller/codex_bridge.py` adds `build_bridge`, `check_bound` and `prompt`, sharing `clone_candidate`,
`bound_differences` and `tester_prompt` with the Claude tester. Nothing calls the bridge until C10g-3b2. The change is a
`chore:` with no release. The plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_BRIDGE_PLAN.md`.

## Next action

`/milestone-plan` for C10g-3b2 (the Codex tester's launch and results, [#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)).
