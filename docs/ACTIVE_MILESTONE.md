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
- **CP2 -- complete.** The speed-up was measured on four CPUs, and the guide and the roadmap now describe the
  behaviour (`docs/guide/ci-and-releases.md`; `docs/ROADMAP.md` drops #126 from "Next, in order").

## Functional-review checklist

Measured on an AMD Ryzen 7 9800X3D (16 logical CPUs, 8 cores with two threads each; `taskset -c 0-3` selects four
different physical cores, `lscpu -e` shows CPU 0-3 on cores 0-3, so no two share a core). Command, in a clone of each
commit: `taskset -c 0-3 python3 tools/run_tests.py main-check tests.test_catalogue_determinism`, alternating base and
head. Base is `6aff67e` (sequential passes, 3 tests); head is the milestone head (concurrent passes, 10 tests). Every
run exited 0 with none skipped.

| Run | Base (sequential) | Head (concurrent) |
| --- | --- | --- |
| 1 | 851 s | 369 s |
| 2 | 689 s | 367 s |
| 3 | 692 s | 368 s |
| Median | 692 s | 368 s |

Ratio of medians: 0.53, within the 0.70 limit of D5. The machine was not fully idle at the start of base run 1 (another
lane's tests had just finished; load 1.8 at the start), which explains its 851 s; it does not change either median.

Full suite (`python3 tools/run_tests.py`, 4656 tests): the one failure this milestone caused, the leftover-process
inventory (`test_the_bounds_cannot_regress_and_the_inventory_is_complete`, missing rows for the new test code), is fixed
with rows in `tests/process_fixtures.py` and passes. Three failures are known and not from this milestone: two Git 2.56
trailer tests (`TrailerRuleTest`, `SquashReleaseNotesTest`) and `test_claude_build`'s deep-`TMPDIR` test, which also
fails on `main`. `python3 tools/check_docs.py` passes.
