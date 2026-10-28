# Active Milestone

## Status

**In review.** `workflow-controller-codex-tester-sandbox` (C10g-1, issue
[#210](https://github.com/RodrigoFAbreu/workflow-controller/issues/210)) is implemented (CP1 to CP3). `controller/codex_sandbox.py`
builds the Codex tester's restricted-read permissions profile, home, environment and `codex exec` argv, with its own
pre-launch controls, and opt-in live probes on `codex-cli 0.163.0` (`CONTROLLER_LIVE_CODEX_TESTS=1`) show `codex exec`
applies the profile: the tester cannot write the clone or anything outside its scratch area, read the operator's home,
environment or credential copy, push, or reach the network. Nothing is wired; the composed-configuration check is the
one open limit and C10g-3 builds it before wiring. The change is `chore:` with no release. The measured record is
`tests/harness_contract/codex/tester_sandbox.txt`; the plan is `docs/ai-workflow/CONTROLLER_CODEX_TESTER_SANDBOX_PLAN.md`.

## Next action

Implementation review of `workflow-controller-codex-tester-sandbox`. After acceptance the next item of the harness-neutral
roles epic is C10g-2 (`verify-codex`).
