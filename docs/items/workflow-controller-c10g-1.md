# C10g-1: The functional tester on Codex without a per-release version block, step 1

Item id: `workflow-controller-c10g-1`.

## Roadmap row (moved verbatim)

Complexity: high; split again before it is planned ([#210](https://github.com/RodrigoFAbreu/workflow-controller/issues/210) is the first slice). The functional tester on Codex without a per-release version block, step 1: Codex sandbox enforcement and its proof ([#131](https://github.com/RodrigoFAbreu/workflow-controller/issues/131); extends [follow-up 61](../roadmap-archive.md#known-follow-ups-carried-forward)); the proof is also the Codex sandbox the Codex worker reuses (H4b) (`controller/codex_sandbox.py` builds the tester's restricted-read permissions profile, home, environment and `codex exec` argv, and opt-in live probes on `codex-cli 0.163.0` show `codex exec` applies it: the tester cannot write the clone or anything outside its scratch area, read the operator's home, environment or its credential copy, push or reach the network; nothing is wired, and the one open limit, the composed-configuration check, is built in C10g-3a; a `chore:` change with no release; [#210](https://github.com/RodrigoFAbreu/workflow-controller/issues/210))

Needs: C10f

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
