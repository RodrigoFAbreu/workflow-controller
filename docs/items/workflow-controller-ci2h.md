# CI2h: split the after-merge determinism check

Item id: `workflow-controller-ci2h`.

Not yet planned. A follow-up of [CI2d](workflow-controller-ci2d.md) (its open decision O1 and design decision D5). Depends on CI2d. Source: [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: split `tests.test_catalogue_determinism`, which runs every catalogue scenario twice, in two clones, and compares the normalised summaries inside one test (12.6 to 15.7 minutes measured), so the after-merge determinism job stops bounding the release wait. After CI2d splits the mutation check in three legs, the release wait is bounded by this job. Splitting it means partitioning the catalogue and comparing across legs, a different mechanism from CI2d's `main-check --shard/--of`, and the six-runner proof (`RunnerLimitTest`) is already at its limit on a push to `main`, so the split must fit it (for example by running the legs of one pass and of the other pass in the same job set). The item page of CI2 hoped for a release wait of 6 to 9 minutes; this item is what reaches it. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
