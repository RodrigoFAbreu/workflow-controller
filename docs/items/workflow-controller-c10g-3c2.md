# C10g-3c2: The Codex tester's fallback to the other harness along H9's chain, its telemetry and usage readings, and one live functional round on Codex that proves it

Item id: `workflow-controller-c10g-3c2`.

## Roadmap row (moved verbatim)

Complexity: low ([#236](https://github.com/RodrigoFAbreu/workflow-controller/issues/236)). The Codex tester's fallback to the other harness along H9's chain before the spawn and its selection-time causes, the telemetry dimensions and Codex usage readings for a functional job, and one operator-run functional round on Codex (`CONTROLLER_LIVE_CODEX_TESTS=1`) that proves it (`codex_tester.selection_error` and `job._tester_probe` walk the chain before the spawn, `usage_rollouts` give a Codex tester a `usage_record`, a launched Codex tester has a `telemetry` block, and the round ran to `findings` with real figures; a `feat:` change, released as a minor version)

Needs: C10g-3c1

Section: [11.9](../roadmap-archive.md#119-automated-functional-review)
