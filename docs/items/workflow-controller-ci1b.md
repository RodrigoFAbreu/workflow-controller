# CI1b: split the giant test atoms and refresh the shard timings

Item id: `workflow-controller-ci1b`.

A `ci:` change with test-only edits. Not yet planned. Cut the few giant test atoms into smaller ones, refresh the timings, then raise the shard count. Complexity: medium. The planning rules apply: at most one new mechanism, releasable alone. Target for the three CI1 items together: PR CI under 20 minutes ([#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [CI time budget](../planning-rules.md#next-in-order-and-the-sizing-rules)).

## Scope

One mechanism: finer bricks for the shard planner. Atoms are classes and are never split, so `PrDefectLoopLifecycleTest` and the `functional_lifecycle` classes are split into smaller `TestCase` classes with no change in what they assert. Then `tools/test_timings.json` is refreshed from recent CI (the `timings-ci-attempt-1` artifacts) and `max_shards` in `tools/test_shards.py` is raised (the file mentions about 8 runners of concurrency per pull request). The conformance `acceptance_matrix` suite sets a 872 s floor on its shard and may need its `shards` option; that option belongs to the frozen Workflow content, so an upstream Workflow change is a separate step and is named, not done, here.

## Numbers (profile in [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317))

Total atom time is 9346 s over 5717 tests; the shards ran 925 to 1455 s (slowest over fastest about 1.6) against a 1810 s planner estimate, so the committed timings are about 1.3 times too high. The top atoms are conformance `acceptance_matrix` 872 s, `protocol` 533 s, `PrDefectLoopLifecycleTest` 428 s, `gate_policy` 320 s, `functional_lifecycle` `IsolationTest` 268 s; the top twelve are about 3.7k of 9.3k s. With 8 shards the ideal is about 1170 s, but only after the big atoms are split. Target: 3 to 6 minutes saved on code pull requests.

## Out of scope

The docs-only skip (CI1a); parallel workers inside a shard (CI1c); a change to the frozen Workflow conformance content; setup or caching (under 1 percent of a shard, not worth it).

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
