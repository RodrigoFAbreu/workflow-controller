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

The milestone changes how the catalogue determinism test runs. It has no user-facing feature, so each flow runs the
test or the documentation directly. Every flow runs offline: the test makes its own local clones, and nothing pushes or
calls GitHub.

### Setup

- The milestone head checked out in a clean working tree, Python 3, `git`, `taskset` and `lscpu`.
- Linux user namespaces enabled: the scenario kit runs `unshare`. If the sandbox refuses them, flows 1 and 3 cannot run;
  that is an `environment` limit, not a `defect`.
- No services, no network and no `gh` are needed.

### Test data

None to seed. Each flow builds its own clones and output folders under the temporary directory and removes them.

### Flows

1. **The two passes run at the same time on the whole catalogue.** Run
   `taskset -c 0-3 python3 tools/run_tests.py main-check tests.test_catalogue_determinism`.
   Expected: exit 0, 10 tests run, none skipped, all passed. Wall time about 6 minutes on this machine (6 min 9 s at CP1).
2. **The helper runs jobs together and in order.** In the same module, `RunConcurrentlyTest` passes: the jobs overlap,
   results come back in job order, and a failure of either job, or of both, is raised only after both have finished.
3. **A stalled pass is bounded and leaves nothing behind.** In the same module, `RunBoundedTest` passes: a stalled child
   with two sleeping descendants, one in its own session, fails within the deadline plus the 10-second grace, and all
   three are gone before the runner returns. The variant whose reaper ignores `SIGTERM` passes too. Before and after the
   command, compare `ps -eo pid,ppid,args`: no process the run started remains.
4. **The leftover-process inventory is complete.** Run `tests.test_fake_claude_contract` with the same command form as
   flow 1; `test_the_bounds_cannot_regress_and_the_inventory_is_complete` passes.
5. **The documentation matches the behaviour.** `python3 tools/check_docs.py` exits 0. In
   `docs/guide/ci-and-releases.md` (the determinism section, around lines 85 to 140), both passes are described as
   running at the same time, with the measured ratio. `docs/ROADMAP.md` no longer lists #126 under "Next, in order".
6. **Optional, long: the full suite.** `python3 tools/run_tests.py` runs 4656 tests. Expected: only the three known
   failures listed below.

### Known limitations and out of scope

- The speed-up is measured on this machine, on four CPUs (`taskset -c 0-3`). The real effect on CI shows only in the
  `after-merge-determinism` job of the next `main` run (the target is near the 17 to 18 minutes of the mutations job,
  against 32.1 minutes before). The sandbox has no GitHub, so no flow can check it.
- Known failures that are not from this milestone: `TrailerRuleTest` and `SquashReleaseNotesTest` (the Git 2.56 trailer
  rule), and `test_claude_build`'s deep-`TMPDIR` test, which also fails on `main`.
- Out of scope: splitting the check into separate jobs (the D5 amendment path), the mutations job, the shard layout,
  `validate`'s timing, and any change to `.github/workflows/` or `tools/ci_workflows.py`.

### Automated verification and measurement (CP2 record)

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
