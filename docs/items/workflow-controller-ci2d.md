# CI2d: after-merge and trigger policy, and the automated timings refresh

Item id: `workflow-controller-ci2d`.

Not yet planned. A slice of [CI2](workflow-controller-ci2.md), split at its planning. Depends on CI2. Sources: scope items 11, 12 and 13 of the owner's decision of 2026-11-16 on the CI2 page, the automation of scope item 1, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: what runs, and when, after a merge or a push.

11. After the merge, split the mutation job into 2 or 3 jobs within the six-runner cap (release wait from about 17 to 6 to 9 min).
12. Skip the test shards on `main` for documentation-only commits; keep the managed conformance job.
13. Do not run the full suite on every intermediate Draft-PR push; run it when the pull request is ready for review and on `main`.
- **The automated timings refresh** (the part of scope item 1 that [CI2](workflow-controller-ci2.md) leaves out): refresh `tools/test_timings.json` automatically so the staleness ratchet's limit (`MAX_UNTIMED_TEST_SHARE`) stays unreachable. CI2 refreshes the file once by hand.

Waste outside the suite that motivates this slice: 101 cancelled CI runs since 12 Nov cost about 5,500 runner-minutes, documentation-only merges to `main` still run all shards (about 155 runner-min), and the after-merge determinism job (12.6 to 15.7 min) and mutations job (11.3 to 17.2 min) delay releases. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
