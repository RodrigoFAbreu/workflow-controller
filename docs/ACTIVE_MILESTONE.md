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

## Functional review checklist

Work item `workflow-controller-fallback-select` (H9b), implementation revision 1. Every flow is offline and unit level:
no `claude`, `codex`, `ssh`, network or real `gh` is needed, and nothing is written inside the clone.

**Setup.** Run from the clone root with the system `python3`. Do not set `PYTHONPATH`. Point `TMPDIR` at a scratch folder.

**Test data.** None to seed. The tests build their own chains, fake launchers, fake gates and a patched harness registry
with a second worker harness.

| # | Flow | Command | Expected |
| --- | --- | --- | --- |
| 1 | The pure selection (D2 to D4, D7) | `python3 -m unittest tests.test_route_select -v` | All pass: first admitted entry chosen; a held provider skips every later entry of that provider with no second admission; an unavailable launcher is skipped; the earliest waitable hold is reported; every launcher unavailable returns the first entry's error; a disabled budget returns token `None`; `usage_provider` covers every registered adapter. |
| 2 | The launch call site and the record (D1, D4, D5) | `python3 -m unittest tests.test_route_select_job -v` | All pass: a role without a chain has no `selection` key; a free Claude chain picks position 0 with nothing skipped; a held Claude account with a free second-harness entry launches the second entry, records `usage_held` for position 0 and holds one reservation; a held account with only same-provider entries returns the unchanged `UsageHold` with no record and no reservation; a worker that fails is not retried; `resume` never reselects. |
| 3 | Telemetry (D6) | same module as flow 2, the telemetry cases | A record with a `selection` block reports `chain_position` and `chain_skipped`; one without reports today's dimensions only; a courier review record whose route harness is `codex` still reports `claude-code`. |
| 4 | Nothing existing moved | `python3 -m unittest tests.test_routing_chain tests.test_telemetry tests.test_usage_gate tests.test_usage_limit_mid_job tests.test_worker_launch tests.test_job tests.test_harness` | All pass, none of those files edited by this milestone. |
| 5 | Docs | `python3 tools/check_docs.py`, then read the "Worker routing" section of `docs/guide/commands.md`, the routing section of `docs/guide/runtime.md` and "The selection block" in `docs/guide/workers.md` | The check passes; the three pages agree with each other and with the behaviour above (order, what skips an entry, a hold skips the whole provider, nothing retried after a worker started, a role with no `fallback` unchanged). |

**Known limitations, out of scope.** With the harness registry as shipped only `claude-code` takes the worker role, so a
real chain of Claude entries always records position 0 and waits exactly as today; a later entry is chosen only with a
second worker harness (H4). A model-specific limit is not detected, so a same-account entry is never a fallback for a hold.
The Codex tester's own fallback and build check (C10g-3), a retry after a worker started, and H11 (the harness in telemetry
for review records) are separate items.
