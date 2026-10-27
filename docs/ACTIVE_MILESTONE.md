# Active Milestone

## Status

**Implementing.** `workflow-controller-scenario-base` (SC1, issue
[#188](https://github.com/RodrigoFAbreu/workflow-controller/issues/188)): the scenario tests measure from the checkout and a
sandbox-free guard checks the fixture's classification. Plan revision 1 approved by gate policy (6721ebd).

- CP1 (pin the base to the checkout, guard the classification): complete. `tests/test_run_scenarios.py` gains
  `pin_base_to_checkout`, `fixture_files`, `FixtureClassificationTest` and `PinBaseToCheckoutTest`; `tests.test_run_scenarios`
  (53 tests) and `tests.test_catalogue_determinism` (11 tests) pass on a machine whose probe passes.
- CP2 (guide and roadmap): pending.

## Next action

Run `/milestone-implement workflow-controller-scenario-base` for CP2 (the guide and the roadmap).
