# Active Milestone

## Status

**In implementation review.** `workflow-controller-harness-models` (H8) is implemented from
`docs/ai-workflow/CONTROLLER_HARNESS_MODELS_PLAN.md` (revision 2, plan approved by gate policy); every checkpoint is
complete and the self-review findings are applied.

- CP1 -- the declarations, the check and the refusal at load: complete. `HarnessAdapter` declares `models` and
  `efforts`; `routing.check_route_values` judges a resolved route and `RoutingOptions` refuses an unsupported pair with
  exit 20 before any job record is written. D8 check of the real `.controller/routing.json`: loaded through
  `routing.load_routing_config` and built into `RoutingOptions`, it printed `ok`, and `cmp` found it identical to
  `tests/fixtures/lane_routing.json`.
- CP2 -- the `harness.models` setting: complete. Settings row `harness.models` (type `harness-models`, default `{}`, no flag, table generation 16) is strict on shape, leaves out and reports an unknown harness name, and reaches `RoutingOptions.harness_models` through `cli._routing_options`.
- CP3 -- the table columns, the pages and the roadmap: complete. The harness table gains `Models` and `Efforts` columns; runtime, commands, compatibility ("Upgrading to 1.22.0"), common-problems, README and the H8 roadmap row are current.

## Next action

The implementation review of implementation revision 1 (local, then external).
