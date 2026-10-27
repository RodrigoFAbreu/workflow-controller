# Active Milestone

## Status

**Complete.** `workflow-controller-fallback-chain` (H9a, issue
[#197](https://github.com/RodrigoFAbreu/workflow-controller/issues/197)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): a role entry may list further `{harness, model, effort}` routes under `fallback`, in a
`--routing-config` file and the settings file's `routing` section; every entry is validated at load like the first route,
`RoutingOptions.chain(role)` returns the whole chain, the worker families and the mixed-lane reason count every entry, and the
settings table generation is 17. Nothing selects a later entry yet (that is H9b). The change is `chore:`, so nothing is
released. The plan is archived at `docs/milestones/completed/workflow-controller-fallback-chain.md`.

## In progress

`workflow-controller-fallback-select` (H9b, issue [#202](https://github.com/RodrigoFAbreu/workflow-controller/issues/202)),
plan revision 2, implementing.

- CP1 complete: `controller/route_select.py`, the pure selection over injected probes, and `tests/test_route_select.py`.
- CP2 complete: `_execute_step_locked` selects a chain entry through `route_select.select` before the record exists
  (a role without a chain takes the unchanged lines), the record's `worker_route` gains the `selection` block for a
  chain role, and `telemetry.dimensions` reports `chain_position`, `chain_skipped` and the worker record's own harness;
  `tests/test_route_select_job.py`.
- CP3 complete: the guides (`commands.md`, `runtime.md`, `workers.md`) say the chain is used, what skips an entry and the
  `selection` block; the roadmap marks H9b complete.

## Next action

H9b is implemented and awaits implementation review. After it, the next item of the harness-neutral roles epic in
`docs/ROADMAP.md` is H11 (the harness in telemetry), then the Codex tester (C10g-1 to C10g-3).
