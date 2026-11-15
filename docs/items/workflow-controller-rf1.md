# RF1: a short form for a disabled release: release {enabled: false}

Item id: `workflow-controller-rf1`.

Not yet planned. Requested by the Manager lane (Workflow repository), report 2026-11-15. Priority: low to medium. Complexity: low. Follows [VS1](workflow-controller-vs1.md) in order.

## Why

Today a disabled `release` block in `.workflow-controller/policy.json` still needs every key (`build`, `verify`, `artifacts`, `publication`). A repository that does not release has to carry inert placeholder commands; the Manager lane used inert `true` commands.

## Scope

One mechanism: admit `"release": {"enabled": false}` alone (schema and validation in `controller/repo_policy.py`, docs, tests), keeping the full form valid.

`release.enabled: false` with `auto_merge: true` stays admitted: the milestone then closes out without a release.

The sizing rule (at most one new mechanism, releasable alone) is on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
