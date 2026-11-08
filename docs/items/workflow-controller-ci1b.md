# CI1b: split the giant test atoms and refresh the shard timings

Item id: `workflow-controller-ci1b`.

A `ci:` change with test-only edits. Implemented on the milestone branch (three checkpoints). Cut the few giant test atoms into smaller ones, refresh the timings, then raise the shard count. Complexity: medium. The planning rules apply: at most one new mechanism, releasable alone. Target for the three CI1 items together: PR CI under 20 minutes ([#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [CI time budget](../planning-rules.md#next-in-order-and-the-sizing-rules)).

## Scope

One mechanism: finer bricks for the shard planner. Atoms are classes and are never split, so `PrDefectLoopLifecycleTest` and the `functional_lifecycle` classes are split into smaller `TestCase` classes with no change in what they assert. Then `tools/test_timings.json` is refreshed from recent CI (the `timings-ci-attempt-1` artifacts) and `max_shards` in `tools/test_shards.py` is raised (the file mentions about 8 runners of concurrency per pull request). The conformance `acceptance_matrix` suite sets a 872 s floor on its shard and may need its `shards` option; that option belongs to the frozen Workflow content, so an upstream Workflow change is a separate step and is named, not done, here.

## Numbers (profile in [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317))

Total atom time is 9346 s over 5717 tests; the shards ran 925 to 1455 s (slowest over fastest about 1.6) against a 1810 s planner estimate, so the committed timings are about 1.3 times too high. The top atoms are conformance `acceptance_matrix` 872 s, `protocol` 533 s, `PrDefectLoopLifecycleTest` 428 s, `gate_policy` 320 s, `functional_lifecycle` `IsolationTest` 268 s; the top twelve are about 3.7k of 9.3k s. With 8 shards the ideal is about 1170 s, but only after the big atoms are split. Target: 3 to 6 minutes saved on code pull requests.

## Out of scope

The docs-only skip (CI1a); parallel workers inside a shard (CI1c); a change to the frozen Workflow conformance content; setup or caching (under 1 percent of a shard, not worth it).

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## Measurements (CI1b)

- **Split.** Six multi-test classes became fourteen classes along their test methods, with no assertion changed (the AST of every moved body matched the base, except the class name in the two `ReportNotificationTest` bodies): `PrDefectLoopLifecycleTest` into Reopen, Report and Stop; `SatisfyGateLifecycleTest` into Approval and Refusal; `OutcomesTest` into Evidence and Failure; `TheFaultStopsTest` into Record and Sandbox; `ReportTest` into Record, Refusal and Scope; `WaitTest` into Budget and Reports.
- **Timings.** Merged from the `timings-ci-attempt` artifacts of three CI runs (42441435062, 42443476424, 42446936171), oldest first. The new classes' entries are apportioned from the old classes' folded base by local share (`{seconds, samples: 1, tests}`): lifecycle 90.7 / 117.4 / 138.2 s and 80.3 / 95.7 s, job 81.6 / 109.2 s and 50.2 / 88.0 s, report 34.2 / 32.7 / 36.3 s and 48.7 / 44.0 s. The next merge from CI replaces them with observed seconds.
- **Planner estimates** (`run_tests.py plan --profile ci --ci-placement --max-shards N`, longest shard): 6 shards 1439 s, 7 shards 1288 s, 8 shards 1233 s (the shortest of the eight is 1120 s). Seven lowers the estimate by 151 s, so the 90 s rule of D6 holds, but raising the budget from 8 to 9 runners needs the owner's yes. `CI_MAX_SHARDS` stays 6 and **7 is recommended**. At seven the longest estimate equals the conformance `acceptance_matrix` atom (1119.7 s times the lane contention 1.15, about 1288 s), so an eighth shard buys nothing for the longest shard.
- **Ratchet.** `CommittedTimingsTest` now fails for a multi-test class over 200 s, and a negative case proves the check can fail.
- **Extra CPU time of the repeated snapshot drives** is not measured separately here; the pull request's own CI is the proof (D8), and the page will record the baseline and the pull request's run ids, timestamps and interval when the CI of this pull request has finished.
