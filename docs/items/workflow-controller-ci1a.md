# CI1a: skip the test shards for docs-only changes

Item id: `workflow-controller-ci1a`.

A `ci:` change. Not yet planned. Skip the six test shards when a pull request changes only documentation. This item needs no other item. Complexity: small. The planning rules apply: at most one new mechanism, releasable alone. Target for the three CI1 items together: PR CI under 20 minutes ([#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317), [CI time budget](../planning-rules.md#next-in-order-and-the-sizing-rules)).

## Scope

One mechanism: change detection at job level. A cheap `changes` job reports whether any path outside the documentation changed, and the `tests` jobs carry an `if:` on its output. A workflow-level `paths-ignore` is not allowed: the required check is the `tests-result` job under `validate / ...`, and a skipped workflow would leave it pending forever. So `tests-result` must still succeed when the shards are skipped, and `python3 tools/check_docs.py` still runs on every pull request. The workflow files are generated: the edit goes through `tools/ci_workflows.py` and `python3 tools/ci_workflows.py --write`, never by hand.

## Numbers (profile in [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317))

Docs-only pull requests (313, 314, 316) ran the same jobs as code pull requests: plan, six test shards of about 20 CPU-minutes each, package, `tests-result`; CI took 24.9, 25.6 and 25.8 minutes. About half of the recent pull requests touch no code. Expected saving: about 24 minutes on a docs-only pull request, nothing on a code pull request. Main still runs everything.

## Out of scope

Changing the shards, the timings or the test atoms (CI1b); parallel workers or test selection by changed paths (CI1c); the conformance workflow, which runs in parallel and is not on the critical path.

## Planning notes

The rules that apply to this item (the sizing rule: at most one new mechanism, releasable alone) are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
