# CI2e: a runtime check replacing the static repo-reach heuristic

Item id: `workflow-controller-ci2e`.

Not yet planned. A slice of [CI2](workflow-controller-ci2.md), split at its planning. Depends on CI2. Source: [#322](https://github.com/RodrigoFAbreu/workflow-controller/issues/322); also [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: replace the CI1a static repo-reach heuristic in `tests/test_ci_changes.py` with a runtime check, for example a suite run with the tree unreadable, or a hash of the tree before and after, so a documentation-only classification is proven and not inferred from source text.

The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
