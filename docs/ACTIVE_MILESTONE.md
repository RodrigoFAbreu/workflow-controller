# Active Milestone

## Status

**In progress.** `workflow-controller-codex-tester-surroundings` (C10g-3b2b, issue
[#231](https://github.com/RodrigoFAbreu/workflow-controller/issues/231)): CP1 complete (Codex declares the tester role
with `verify-codex`, routing refuses the pair until C10g-3c, the three carried findings fixed); CP2 complete (`codex_tester.launch_codex_functional`: the build gate, the
before/after snapshots, the binding check and the refusal mapping, with `tests/test_codex_tester_launch.py`); CP3 complete (the guides, the generated
table and the roadmap state the new facts in place). Nothing calls the launch until C10g-3c; the change is a `chore:` with no release.
The previous item is recorded below.

**Complete.** `workflow-controller-codex-tester-run` (C10g-3b2a, issue
[#228](https://github.com/RodrigoFAbreu/workflow-controller/issues/228)) is implemented (CP1 to CP3, the self-review fixes and the functional review) and accepted by policy. The plan is
`docs/ai-workflow/CONTROLLER_CODEX_TESTER_RUN_PLAN.md`. `controller/codex_tester.py` adds `run_tester`: the launch
controls, `codex exec` under a deadline, the outcome (`ran`, `timeout`, `limit`, `failed`), the final message, the
readings, the credential copy-back and the removal of the credential copy on every path, in the Claude launch's result
shape. `Built` records the credential digest, model and effort. CP3 adds the opt-in live test
(`tests/test_codex_tester_live.py`, one model turn; run once: `ran` with a final message), rewords the last
`codex_bridge.SANDBOX_LIMITS` entry, adds the read-roots assertion to the prompt test and updates the guide and
roadmap. Nothing calls `run_tester`; the change is a `chore:` with no release.

## Next action

`/milestone-plan` for C10g-3c ([#223](https://github.com/RodrigoFAbreu/workflow-controller/issues/223), the Codex tester chosen through routing), after this item is accepted.
