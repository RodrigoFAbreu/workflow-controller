# CI1b: split the giant test atoms and refresh the shard timings

Item id: `workflow-controller-ci1b`.

A `ci:` change with test-only edits. Implemented on the milestone branch (three checkpoints). Cut the few giant test atoms into smaller ones, refresh the timings, and raise the shard count only if the planner's estimate pays and the owner agrees. Complexity: medium. The planning rules apply: at most one new mechanism, releasable alone. Target for the three CI1 items together: PR CI under 20 minutes ([#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [CI time budget](../planning-rules.md#next-in-order-and-the-sizing-rules)).

## Scope

One mechanism: finer bricks for the shard planner. Atoms are classes and are never split, so six multi-test classes, among them `PrDefectLoopLifecycleTest`, are split along their test methods into smaller `TestCase` classes with no change in what they assert (the single-test `functional_lifecycle` atoms `IsolationTest`, `CycleClosesTest` and `PersonStopsTest` stay whole, D3). Then `tools/test_timings.json` is refreshed from recent CI (the `timings-ci-attempt-1` artifacts), and `CI_MAX_SHARDS` in `tools/test_shards.py` is raised only if the planner's estimate pays and the owner agrees (it stayed 6 here, D6, with no owner answer; CI3 later set it to 3). The conformance `acceptance_matrix` suite sets a 872 s floor on its shard and may need its `shards` option; that option belongs to the frozen Workflow content, so an upstream Workflow change is a separate step and is named, not done, here.

## Numbers (profile in [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317))

Total atom time is 9346 s over 5717 tests; the shards ran 925 to 1455 s (slowest over fastest about 1.6) against a 1810 s planner estimate, so the committed timings are about 1.3 times too high. The top atoms are conformance `acceptance_matrix` 872 s, `protocol` 533 s, `PrDefectLoopLifecycleTest` 428 s, `gate_policy` 320 s, `functional_lifecycle` `IsolationTest` 268 s; the top twelve are about 3.7k of 9.3k s. With 8 shards the ideal is about 1170 s, but only after the big atoms are split. Target: 3 to 6 minutes saved on code pull requests.

## Out of scope

The docs-only skip (CI1a); parallel workers inside a shard (CI1c); a change to the frozen Workflow conformance content; setup or caching (under 1 percent of a shard, not worth it).

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Measurements (CI1b)

- **Split.** Six multi-test classes became fourteen classes along their test methods, with no assertion changed (the AST of every moved body matched the base, except the class name in the two `ReportNotificationTest` bodies): `PrDefectLoopLifecycleTest` into Reopen, Report and Stop; `SatisfyGateLifecycleTest` into Approval and Refusal; `OutcomesTest` into Evidence and Failure; `TheFaultStopsTest` into Record and Sandbox; `ReportTest` into Record, Refusal and Scope; `WaitTest` into Budget and Reports.
- **Timings.** Merged from the `timings-ci-attempt` artifacts of two CI runs (42441435062, then 42443476424). A third download, of run 42446936171 on `main`, holds shard records byte for byte the same as 42443476424's (the `main` run reused the pull request's results), so it adds no observation and was not folded in again. The new classes' entries are apportioned from the old classes' folded base by local share (`{seconds, samples: 1, tests}`): lifecycle 90.7 / 117.4 / 138.2 s and 80.3 / 95.7 s, job 81.6 / 109.2 s and 50.2 / 88.0 s, report 34.2 / 32.7 / 36.3 s and 48.7 / 44.0 s. The next merge from CI blends them with observed seconds (EWMA, `EWMA_WEIGHT = 0.5`; the first observation is taken outright when the class's test count changed).
- **Planner estimates** (`run_tests.py plan --profile ci --ci-placement --max-shards N`, longest shard): 6 shards 1439 s, 7 shards 1288 s, 8 shards 1233 s (the shortest of the eight is 1120 s). Seven lowers the estimate by 151 s, so the 90 s rule of D6 holds, but raising the budget from 8 to 9 runners needs the owner's yes. `CI_MAX_SHARDS` stays 6 and **7 is recommended**. At seven every shard is about 1288 s, the conformance `acceptance_matrix` atom (1119.7 s) times the lane contention 1.15 of a shard it shares with Controller tests. At eight the atom runs alone (1120 s) and the longest shard is 1233 s, only 55 s below seven for a tenth runner. **Owner's answer:** the orchestrator asked on SignalHub during CP2 (choices 6 or 7, default 6) and no answer came within the 30-minute bound, so `CI_MAX_SHARDS` stays 6 as D6 says.
- **Ratchet.** `CommittedTimingsTest` now fails for a multi-test class over 200 s, and a negative case proves the check can fail.
- **Extra CPU time of the repeated snapshot drives.** Each old class at the base commit and each new class at the head was run alone in its own process, on one machine in one batch (eight at a time): the six old classes built 24 snapshot trees and took 521 s, the fourteen new classes build 36 and take 647 s, so the split costs 12 more drives and about 126 s of local CPU (54 s `PrDefectLoopLifecycle`, 29 s `Outcomes`, 27 s `Report`, 8 s each `TheFaultStops` and `Wait`, none for `SatisfyGateLifecycle`). Scaled by each group's committed CI seconds over its local seconds, that is about 200 s of CI CPU spread over the shards (CPU time, not elapsed time: the siblings run on different shards in parallel).
- **Elapsed CI time (D8)**, one interval for both sides: the workflow run's `run_started_at` to the `completed_at` of its `tests-result` job. This pull request (run 42458047289, head `e0e5fbf`): 2026-11-08T07:15:33Z to 07:42:56Z = **27 min 23 s**. Baseline, the two code pull request runs of CI1a before the refresh: run 42441435062, 03:08:21Z to 03:35:29Z = 27 min 08 s; run 42443476424, 03:37:08Z to 04:07:07Z = 29 min 59 s. The refresh and the split did **not** shorten the pull-request CI in this measurement: it sits inside the baseline's spread, which is runner noise of several minutes. The planner's estimate for the longest shard is 1438.9 s at 6 shards (about 24 min), so the floor is the `acceptance_matrix` conformance atom, not the split classes; 7 shards would lower it by 151 s (owner decision not given, so it stays 6). Reaching 20 minutes needs CI1c (parallel workers inside a shard, and the `acceptance_matrix` atom), not further splitting.

## Review follow-ups recorded later ([#324](https://github.com/RodrigoFAbreu/workflow-controller/issues/324), done in CI2)

The six optional findings of the CI1b reviews were documentation changes and were made in [CI2](workflow-controller-ci2.md): the summary and the Scope paragraph above now say what was done and not the pre-plan wording (F-dce8244460bb); the development guide's apportioning note says "blends" and states the two missing parts of the D4 rule (F-038599eb356f, F-ff034800cbc8); its ratchet note says "a base without test methods" (F-fcfda08fc110); and the two tables below record the fixture keys and the derivation inputs (F-605cecd0feda, F-eb34323ce69b).

### Fixture keys built per sibling (local single-class runs)

| Class | Snapshot builds |
| --- | --- |
| `PrDefectLoopLifecycleReopenTest` | 4 keys (`to_acceptance` and `to_refused_acceptance`, policy `null` and `requires_pr_approved`, checks pending) |
| `PrDefectLoopLifecycleReportTest` | 6 keys (checks green with `requires_pr_approved`; checks empty with `require_ci: false`; checks pending with policy `null`) |
| `PrDefectLoopLifecycleStopTest` | 7 keys (checks green and pending, policy `null` and `requires_pr_approved`, with and without the pull request flag) |
| `SatisfyGateLifecycleApprovalTest` | 2 keys (`to_acceptance`, checks pending and green) |
| `SatisfyGateLifecycleRefusalTest` | 1 key (`to_plan_gate`, green) |
| `OutcomesEvidenceTest` | 2 keys (`to_38e`, one and two flows) |
| `OutcomesFailureTest` | 3 keys (`to_38e`, one, two and three flows) |
| `TheFaultStopsRecordTest` | 2 keys (`to_38e`, one and three flows) |
| `TheFaultStopsSandboxTest` | 4 builds of one key (`to_38e`, one flow) |
| `ReportRecordTest`, `ReportRefusalTest`, `ReportScopeTest`, `WaitBudgetTest`, `WaitReportsTest` | 1 key each (`to_acceptance`, checks pending), the shared `at_38f()` tree |

The total is 36 builds, against 24 for the six old classes (the figure in the measurements above).

### The D4 derivation inputs

Artifacts folded, oldest first: `timings-ci-attempt-1` of runs 42441435062 then 42443476424 (run 42446936171 on `main` reused 42443476424's results and was not folded). `EWMA_WEIGHT = 0.5`. The count rule applies to `TheFaultStopsTest` (committed 9 tests, source 11) and `ReportTest` (8 against 9): the first observation is taken outright. Each new class's entry is `base x local / local sum`, written as `{seconds, samples: 1, tests}`. Every base came from the artifacts; none fell back to the committed value.

| Old class | Committed s (tests) | Observed 42441435062 | Observed 42443476424 | Base | New class: local s (tests), entry s |
| --- | --- | --- | --- | --- | --- |
| `test_protocol_lifecycle.PrDefectLoopLifecycleTest` | 464.306 (9) | 430.657 | 245.009 | 346.245 | Reopen 47.5 (2), 90.665; Report 61.5 (3), 117.387; Stop 72.4 (4), 138.193 |
| `test_protocol_lifecycle.SatisfyGateLifecycleTest` | 256.645 (8) | 202.120 | 122.534 | 175.958 | Approval 36.1 (3), 80.304; Refusal 43.0 (5), 95.654 |
| `test_protocol_job.OutcomesTest` | 269.627 (8) | 177.288 | 158.089 | 190.773 | Evidence 35.2 (4), 81.594; Failure 47.1 (4), 109.179 |
| `test_protocol_job.TheFaultStopsTest` | 266.961 (9; source 11) | 168.209 (taken outright) | 108.187 | 138.198 | Record 35.7 (6), 50.190; Sandbox 62.6 (5), 88.008 |
| `test_pr_report.ReportTest` | 262.410 (8; source 9) | 83.094 (taken outright) | 123.392 | 103.243 | Record 24.3 (3), 34.227; Refusal 23.2 (3), 32.677; Scope 25.8 (3), 36.339 |
| `test_pr_report.WaitTest` | 219.638 (7) | 70.377 | 40.365 | 92.686 | Budget 25.3 (4), 48.651; Reports 22.9 (3), 44.035 |
