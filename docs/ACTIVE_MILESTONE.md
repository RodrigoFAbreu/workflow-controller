# Active Milestone

## Status

**In progress.** `workflow-controller-ci-time` (C2b, `docs/ROADMAP.md`): bring a pull request's CI back to 15-20
minutes and fix the `FollowCommandTest` flake ([#119](https://github.com/RodrigoFAbreu/workflow-controller/issues/119),
[#121](https://github.com/RodrigoFAbreu/workflow-controller/issues/121)). Plan revision 8 is approved by policy; the
plan is `docs/ai-workflow/CONTROLLER_CI_TIME_PLAN.md`. `docs/ai-workflow/WORKFLOW_STATE.json` is the ground-truth record.

| Checkpoint | State |
| --- | --- |
| CP1 | Complete. The two timer-dependent `FollowCommandTest` cases (`test_a_running_run_is_chosen`, the orphan-job case) end the run from the follower's first heartbeat instead of a 0.3-second timer. Each has a delayed-follower regression (`test_a_follower_started_late_...`) that fails on the old timer shape in a scratch copy and passes on the new one; the class passes pinned to two CPUs with six parallel copies. |
| CP2 | Complete. `CatalogueDeterminismTest` moved to `tests/test_catalogue_determinism.py` (its `setUpClass` applies `kit.isolation_gate`, so it fails under `CI` instead of skipping); `tests/test_run_scenarios.py` keeps `SubsetDeterminismTest` (`scenario-settings-file-effect`, `scenario-human-gates-stop-and-resume`, `scenario-kill-and-resume-run-after`, about 10 seconds, asserts ingestion, the exact name set and equal summaries). `test_shards.MAIN` places `tests.test_catalogue_determinism` and `tests.test_scenarios_negative` outside the `--ci-placement` plan (they stay in a local full run: 4,584 tests locally, 4,501 in the CI plan). `tools/run_tests.py main-check MODULE` is the fail-closed command: it exits non-zero on an empty or short selection, any skip, or a failure, and an older tree without it refuses. `main.yml` gains `after-merge-determinism`/`-mutations` (pushed commit) and `release-target-determinism`/`-mutations` (the commit `release-plan` selected, only when it is an ancestor); `build` and `publish` need all four through `_coverage_condition()`. Run locally: determinism 3 tests in 11:50, mutations 46 tests in about 16 minutes, none skipped. A deliberate runner break (an unnormalised pid in the summary) fails the subset test in a scratch copy; a break of `Normaliser` masking alone does not, because none of the cheap flows observes a volatile value, which is the whole-catalogue check's job. The plan digest changes once (moved modules, new subset class); `tools/test_timings.json` only renames the moved atom's key, the refresh is CP5. |
| CP3 | Complete. `tools/test_shards.CI_MAX_SHARDS` is 6 and its budget comment states 6 shards + `package` + the managed `workflow-conformance` job, about 8 concurrent Linux runners on the account's 20-job limit. `tests/test_test_shards.py` asserts the six-shard CI default and the constant; `tests/test_scenarios.py` sets `MAX_CI_SHARDS = 6` and `test_the_ci_plan_stays_within_six_shards` still bounds the CI plan's parallel shards, scenario inventory included. The generated workflows do not change (the matrix is the planner's output). |
| CP4-CP6 | Not started. |

The previous milestone, `workflow-controller-automated-functional-review` (C10c, released as 1.18.0), is archived at
`docs/milestones/completed/workflow-controller-automated-functional-review.md`.
