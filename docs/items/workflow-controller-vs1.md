# VS1: a manifest.json version source for the version_change release trigger

Item id: `workflow-controller-vs1`.

Not yet planned. Requested by the Manager lane (Workflow repository), report 2026-11-15. Priority: medium. Complexity: low to medium. Follows [BL1](workflow-controller-bl1.md) in order.

## Why

The Workflow repository releases when the `workflow_version` field of `manifest.json` changes. The Controller's `version_change` release trigger has only the `pyproject` version source (`VERSION_SOURCES = {"pyproject": ...}` in `controller/repo_policy.py`). The `conventional_commit` trigger would wait forever for tools-only `fix:` commits there.

So the Workflow repository (workflow#193) keeps `release.enabled` false and cannot have the release wait or the release step of auto-merge.

## Scope

One mechanism: a JSON-path version source, for example `{"kind": "json", "path": "manifest.json", "key": "workflow_version"}`, in:

- the policy schema and its validation (`controller/repo_policy.py`);
- the release classifier (`release_txn.classify` and `version_change`);
- tests;
- docs: [CI and releases](../guide/ci-and-releases.md) and [Milestone branches and pull requests](../guide/milestone-branches.md).

The sizing rule (at most one new mechanism, releasable alone) is on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
