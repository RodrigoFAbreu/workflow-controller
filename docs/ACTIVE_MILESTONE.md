# Active Milestone

## Status

**Complete.** `workflow-controller-fallback-chain` (H9a, issue
[#197](https://github.com/RodrigoFAbreu/workflow-controller/issues/197)) was accepted on 2026-10-27 by gate policy
(`policy:bae758b4f4ce`): a role entry may list further `{harness, model, effort}` routes under `fallback`, in a
`--routing-config` file and the settings file's `routing` section; every entry is validated at load like the first route,
`RoutingOptions.chain(role)` returns the whole chain, the worker families and the mixed-lane reason count every entry, and the
settings table generation is 17. Nothing selects a later entry yet (that is H9b). The change is `chore:`, so nothing is
released. The plan is archived at `docs/milestones/completed/workflow-controller-fallback-chain.md`.

## Next action

No milestone is active. Next is H9b of the harness-neutral roles epic in `docs/ROADMAP.md` (the selection of a fallback chain's
entry; its open question is the narrow definition of "unavailable or held by a limit",
[#202](https://github.com/RodrigoFAbreu/workflow-controller/issues/202)); run `/milestone-plan` for it.
