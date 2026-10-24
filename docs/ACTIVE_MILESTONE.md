# Active Milestone

## Status

**In progress.** `workflow-controller-determinism-parallel` ([#126](https://github.com/RodrigoFAbreu/workflow-controller/issues/126)):
the whole-catalogue determinism check runs its two passes at the same time. Plan revision 4 is approved. The plan is
`docs/ai-workflow/CONTROLLER_DETERMINISM_PARALLEL_PLAN.md`; `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth
record of state.

## Checkpoints

- **CP1 -- complete.** `tests/test_run_scenarios.py` gains `run_bounded` (the pass runs in its own session under a
  reaping subreaper with a pass-owned `TMPDIR`; deadline, grace, `SIGKILL` and a tagged sweep leave nothing behind),
  `run_concurrently`, and `CloneCase.runner(clone=, timeout=)`. `CatalogueDeterminismTest` runs pass A and pass B at the
  same time in two clones (`clone`, `pass-b`). The helpers have tests in `tests/test_catalogue_determinism.py`
  (`RunConcurrentlyTest`, `RunBoundedTest`). Verified: `taskset -c 0-3 python3 tools/run_tests.py main-check
  tests.test_catalogue_determinism` -- 10 tests, none skipped, all passed, 6 min 9 s wall.
- **CP2 -- pending.** The measurement on four CPUs, then the guide and the roadmap.
