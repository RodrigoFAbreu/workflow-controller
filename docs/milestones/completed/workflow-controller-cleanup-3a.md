# Clean-up, part 1: test races, test leaks and small test tidy-ups (Revision 6)

Work item: `workflow-controller-cleanup-3a`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `8a5ac30a10775df2d0f178c793be56fd9f4f6d78` ("docs: split CU3 into CU3a, CU3b and CU3c, and add C10j and C10k (#145)"), the tip of `main`, passed explicitly as `/milestone-plan 8a5ac30…`.
Lifecycle authority: this repository's installed Workflow **2.18.6**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` "Next, in order" item 1, CU3a (the first of the three parts of CU3).
Released baseline: `workflow-controller 1.19.1`. The change touches tests, test helpers and documentation only, so the squash commit is a `test:` change and releases nothing.
Pull request title: `test: fix four test races and leaks and tidy small test spots (#100, #118, #125, #138, #143)`

## Goal

Close the test-only part of the clean-up the roadmap's limit calls for: five open issues in full ([#100](https://github.com/RodrigoFAbreu/workflow-controller/issues/100), [#118](https://github.com/RodrigoFAbreu/workflow-controller/issues/118), [#125](https://github.com/RodrigoFAbreu/workflow-controller/issues/125), [#138](https://github.com/RodrigoFAbreu/workflow-controller/issues/138), [#143](https://github.com/RodrigoFAbreu/workflow-controller/issues/143)) and the two test points of [#140](https://github.com/RodrigoFAbreu/workflow-controller/issues/140) (the fake tester's `..` handling and the two identical link tests). Nothing under `controller/` changes. #140's first point (`runtime.remove_tree`) is a production primitive and is C10k; this item does not close #140.

Three of the issues are timing or leak defects that make a passing run red or leave a dead process behind (#100, #138, #143); the rest are small tidy-ups with no behaviour change.

## Decisions

Each decision states one principle.

- **D1. A file another process waits for appears whole or not at all (#100).** `tests/fake_claude.py` writes the pid of its hang child to `FAKE_CLAUDE_HANG_CHILD_PID_FILE` through a temporary file in the same folder and `os.replace`, so a reader that sees the path sees all of its content. The waiters (`tests/test_fake_claude_contract.py`, `tests/test_worker.py`) are not changed: the writer is the single place that makes the file safe. A focused test decides the question on every run: it creates the target path beforehand holding a sentinel, notes its inode, runs the fake in hang mode, and requires that the path ends up with a different inode holding an integer (`os.replace` passes; an in-place `open(..., "w")` keeps the inode and fails). A second check reads the file the moment it exists, many times over, and requires an integer every time; it is kept as a guard, not as the proof, because the old in-place write holds an empty file only for microseconds.
- **D2. A wait for a child's end is bounded long, and a miss prints what the child said (#138).** `ConformanceLaneExecutionTest.run_directly` (`tests/test_test_shards.py`) sends the executor's output to a file in its sandbox folder, waits for the child's exit with an outer bound of 180 seconds (a named constant, not a 30-second guess), and when the bound is hit ends the child's process group and fails with the last of that output. A longer bound must not let a fixture finish by itself: every fixture the executor is signalled for, that is every fixture `run_directly` signals (`direct_plan`'s `test_waits`, the lane-done test's `test_sleeps_after_the_lane_is_done` and the second-run test's `test_sleeps_unless_told_not_to`, each of which sleeps 60 seconds or less today), blocks for a named constant longer than the bound (600 seconds, as the helper process beside it already does), so the child ends within the bound only if the executor handles the signal. Otherwise a lost `SIGINT` or `SIGTERM` would let the fixture end after its 60 seconds and pass inside 180. The `exit` mode is unchanged: the test ends itself with `os._exit`. The "suite started" wait is a readiness signal the child produces and is left alone. The check "a helper or suite process outlived the executor" is unchanged.
- **D3. A forced stop leaves no process, and no zombie that the test could have reaped (#143).** In `run_bounded` (`tests/test_run_scenarios.py`): (a) in the forced path the tagged descendants are killed first, by a `_sweep` pass made while the reaper is still alive and still their parent, and only then the group and the reaper. That first pass must not touch the reaper: the reaper is started with `TMPDIR` set to the pass root, so it carries the root tag and `kit.tagged_pids` would return it with its descendants (and, having the lowest pid, usually first); `_sweep` therefore takes `exclude={proc.pid}` and passes it on to `kit.tagged_pids`, which already accepts one. No `_sweep` pass calls `waitpid`: reaping `proc.pid` before `Popen.wait()` would give `Popen` an `ECHILD` and an exit status of 0 for a reaper that was killed, and a pass before the reaper is waited has no pid of its own to reap, because the live reaper reaps its descendants itself. Only (b)'s bounded loop, which runs after `proc.wait()`, calls `waitpid`; (b) the call keeps one set of every pid it sent `SIGKILL`, in (a) and in every later `_sweep` pass (`_sweep` takes the set and adds to it), because a later pass cannot find a zombie again (`kit.tagged_pids` drops state `Z`, and a zombie's `environ` and `cmdline` are empty); after the reaper is waited, a bounded `waitpid` loop works on that set, not on a fresh tag lookup, and reaps a pid only while `/proc/<pid>/stat` shows state `Z` and a parent pid equal to `os.getpid()` (so a recorded pid its own parent already reaped, and that a new child of the test process may since have reused, is never taken; a pid that is not such a child is left to its parent), instead of one `WNOHANG` call; (c) for the whole call the test process is a child subreaper (`prctl(PR_SET_CHILD_SUBREAPER, 1)`, scoped by a counted context manager so that two passes in two threads set it once and the last one out restores the previous value), so the descendants of a reaper that was killed are re-parented to the test process, where (b) can reap them, instead of to an outside process that may never reap. A new helper in `tests/process_fixtures.py` lists the zombie children of a process (parent pid from `/proc/<pid>/stat`, state `Z`), and `RunBoundedTest` requires that no pid is in the list after the call that was not in the list taken before it (a set comparison, not a length one: a zombie an earlier test left is not this call's, and an older one reaped during the call must not hide a new one) after the unresponsive-reaper case, after the stalled case and after a clean exit. The unresponsive-reaper case also requires that the reaper's `returncode` is non-zero, which fails if any pass reaps `proc.pid` before `Popen` does (`Popen` then gets `ECHILD` and records 0). The expected value is 137, not a negative one: that reaper ignores only `SIGTERM` and is blocked in `child.wait()`, so (a)'s `SIGKILL` of the real command makes `child.wait()` return `-9`, the reaper reaps its adopted descendants and ends itself with `sys.exit(128 - code)`, which is 137, before `proc.kill()` runs (`Popen.send_signal` polls first and skips a process that has exited); when the reaper is instead killed first, as under the named baseline, it ends at `-9`. The test therefore accepts `{137, -9}` (any non-zero value would do) and says why in a comment. `run_bounded` raises `PassFailure` on this path and returns no `proc`, so `PassFailure` carries the reaper's `returncode` as an attribute (`reaper_returncode`, set on every raise after `proc.wait()`), and the test reads it from the caught exception. The existing `_gone` helper (which counts a zombie as gone) is not changed. Zombies under a parent outside the test process stay out of reach, and the guide's wording does not claim otherwise. If the subreaper scope turns out to disturb another test in the same process, the amendment path is to keep (a) and (b), drop (c), say so in the findings record, and leave a follow-up issue for the rest of #143.
- **D4. The fake tester resolves a path the way a sandbox mount does (#140, point 2).** `Sandbox.allows` in `tests/fake_functional_tester.py` makes a relative path absolute by joining it onto the current folder, without collapsing `..` lexically, and then resolves it component by component through links (`os.path.realpath`), so `clone/build/back/../src/app.py` with `writable/build/back -> clone/src` is denied, as a real sandbox denies it. A path whose last component is `..` is judged fully resolved, not by its parent. A focused test in `tests/test_functional_sandbox.py` builds that exact layout and requires the denial, and that the same path without the link in the middle is allowed: with `back` a plain folder, `clone/build/back/../src/app.py` resolves to `writable/build/src/app.py`.
- **D5. Two tests that mutate the same thing are two tests of two causes (#140, point 3).** In the fake `claude` of `tests/test_claude_build.py`, `link_replaced` replaces the clone's link with a directory but still makes the permitted write, so the only problem the product reports is "was replaced or removed"; `link_in_clone` leaves the link in place and skips the permitted write, so the only problem is "did not happen". Both rows of `test_each_broken_probe_fails_closed_and_names_the_probe` also require that the other message is absent from the evidence's `problems`.
- **D6. The small tidy-ups are made as the issues describe them, and what already holds is not done twice (#118, #125).**
  - #118: the import order in `tests/test_protocol_job.py` (`json` before `subprocess`); `NotRunTest` in `tests/test_functional_review.py` passes a `log_dir` different from the output folder and asserts that the log of a flow left out lies under it. The model-free test of `tester_message_excerpt` through the fake CLI already exists (`RunTest.test_the_final_message_is_captured_flattened_and_bounded` and `test_a_short_final_message_is_kept_whole` in `tests/test_functional_sandbox.py`, which use the real launch path with `final_text`); CP4 re-reads them against the issue's text, adds an assertion only if one point is not covered, and says in its issue comment which test covers it.
  - #125: the class that owns `wait_seconds` (`LoopLifecycle`) adds it to its `snapshot_inputs()` key, so a drive with a different wait never reuses another's tree; `SatisfyGateLifecycleTest.refused_validation_run` gets the one-line comment that explains why the `/review-plan` launch reports "ok" after `life.report` and `life.refused` are set; the two identical branches of `MainWorkflowTest.test_every_job_downstream_of_reuse_states_its_condition` fold into one loop body, keeping the comment.
- **D7. The documentation and the issues follow the change.** No guide describes these helpers, so `docs/guide/` does not change; `docs/ROADMAP.md` marks CU3a done and drops the closed issues from its follow-up list, and `docs/ACTIVE_MILESTONE.md` is updated at acceptance. The pull request closes #100, #118, #125, #138 and #143 and only refers to #140.

## Out of scope

Anything under `controller/` (including `runtime.remove_tree`, C10k), `verify-claude` (CU3b), the usage and settings tidy-ups (CU3c), the CI workflow files, and a general rewrite of the test fixtures.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | The atomic pid file and the bounded executor wait | - | 1 | 1 |
| CP2 | No zombie descendant after a forced stop | CP1 | 2 | 1 |
| CP3 | The fake tester's path resolution and the two link tests | CP2 | 1 | 1 |
| CP4 | The small tidy-ups, the roadmap and the issue comments | CP3 | 1 | 1 |

<!-- CP1 -->
### CP1 -- The atomic pid file and the bounded executor wait (#100, #138)

**Files:** `tests/fake_claude.py`, `tests/test_fake_claude_contract.py`, `tests/test_test_shards.py`

D1, D2. The fake writes the hang-child pid file whole (temporary file in the same folder, `os.replace`), with a focused test that requires a new inode holding an integer where a sentinel file stood (decisive on every run) and a second test that reads it the moment it exists, repeatedly (a guard). `run_directly` captures the executor's output in a file, waits up to 180 seconds for its exit, and on a miss ends the process group and fails with the tail of the output. The three signalled fixtures block for 600 seconds, so only the executor's own termination ends them inside the bound.

Done when: the inode test passes and fails against the old in-place write on every run (checked by reverting the fake locally, three runs in a row); the executor test passes in all three modes; with the delivery of `SIGINT` suppressed (the `send_signal` call replaced by a no-op, locally), the `SIGINT` cases of `direct_plan`, of the lane-done test and of `test_a_second_run_in_the_same_results_directory_runs_the_suite_again` fail at the 180-second bound, and likewise for `SIGTERM`, checked once for each signal, so no case can pass through the fixture's own completion; the `taskset -c 0,1` run of `tests.test_test_shards.ConformanceLaneExecutionTest` with four parallel copies passes three times in a row.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- No zombie descendant after a forced stop (#143)

**Files:** `tests/test_run_scenarios.py`, `tests/process_fixtures.py`, `tests/test_catalogue_determinism.py`

D3. The forced path kills the tagged descendants before the group and the reaper, in a first `_sweep` pass that excludes the reaper's pid and reaps nothing; `_sweep` records every pid it kills in a set the call keeps, and a bounded `waitpid` loop, run only after the reaper is waited and only on a pid that is a zombie child of the test process, reaps that set; `run_bounded` runs with the test process as a counted child subreaper; `process_fixtures` gains the zombie-children listing; `RunBoundedTest` requires no pid in that list that was not in the list taken before, after each of its cases and has a test of the counted scope (two overlapping calls restore the previous subreaper value only when both are done).

Done when: `RunBoundedTest` passes, the unresponsive-reaper case fails the new zombie check under the named baseline, and passes on the new code with a non-zero reaper `returncode` (137, or -9 when the reaper is killed first; read from `PassFailure.reaper_returncode`; a 0 means a pass reaped the reaper behind `Popen`'s back) (checked once locally). The baseline reverts the kill order (reaper and group first, descendants after) and the single `WNOHANG` `_sweep`, and keeps the subreaper scope (c) and the new check: the old `run_bounded` as it stands never makes the test process a subreaper, so its zombies are children of an outside process and the check, which lists children of the test process, would pass on it; the whole of `tests.test_catalogue_determinism` still passes, and the process shows no zombie child afterwards.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- The fake tester's path resolution and the two link tests (#140)

**Files:** `tests/fake_functional_tester.py`, `tests/test_functional_sandbox.py`, `tests/test_claude_build.py`

D4, D5. `Sandbox.allows` joins onto the current folder without collapsing `..` and resolves through links; a focused test builds the `back` link layout. The fake `claude` of the build test separates `link_replaced` from `link_in_clone`, and each row requires that the other message is absent.

Done when: the focused test denies `clone/build/back/../src/app.py` and fails against the old `abspath` (checked once locally); both link rows pass with exactly one problem each; the existing sandbox and build tests pass.
<!-- /CP3 -->

<!-- CP4 -->
### CP4 -- The small tidy-ups, the roadmap and the issue comments (#118, #125)

**Files:** `tests/test_protocol_job.py`, `tests/test_functional_review.py`, `tests/test_protocol_lifecycle.py`, `tests/test_ci_workflows.py`, `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md`

D6, D7. The tidy-ups as D6 lists them; the roadmap marks CU3a done and removes the closed issues from the follow-up list; the pull request text names the issues it closes and the one it only refers to.

Done when: `python3 tools/check_docs.py` passes, the full suite is green but for the known Git 2.56 trailer tests, and every point of the five issues is either changed or named as already covered, with the covering test.
<!-- /CP4 -->
