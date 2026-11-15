# AO1: opt this repository in to the Controller's auto-merge

Item id: `workflow-controller-ao1`.

Not yet planned. Owner decision 2026-11-15. Priority: high. Complexity: low. Follows [CX2](workflow-controller-cx2.md) in order.

## Why

The Controller already has an opt-in auto-merge (release 1.6.0, [ADR 0009](../adr/0009-auto-merge-and-release-wait.md), [Auto-merge and the release wait](../guide/milestone-branches.md#auto-merge-and-the-release-wait)). The policy keys are `milestone_branches.pull_request.auto_merge: true` and `release_workflow`, both in `.workflow-controller/policy.json`. With them the Controller sends one head-bound squash merge of the accepted commit (never GitHub's auto-merge request; GitHub's "Allow auto-merge" setting is not used), waits for the release, closes the milestone out, then stops. The operator switch is `merge.auto` in the settings file (default true).

This repository does not use it today: pull requests wait for a human.

## Scope

One mechanism: add `"auto_merge": true` and `"release_workflow": "ci.yml"` to the `pull_request` block of `.workflow-controller/policy.json`. The policy requires `merge_method` `squash` and `ready_requires_green_checks` true; both are already set.

- Update the docs that say this repository does not opt in: the Repository settings section of [CI and releases](../guide/ci-and-releases.md), and any statement that `main.yml` is the release workflow.
- Verify with a milestone that the policy is read from the committed snapshot at bind time.

## Dependency and checks

- [PR #357](https://github.com/RodrigoFAbreu/workflow-controller/pull/357) (CI standardized around `ci.yml`, retiring `main.yml`) must be merged first, because `release_workflow` must name `ci.yml`. It is merged on main.
- Check the repository's required-checks ruleset "Main Protection" (`validate / plan`, `validate / tests-result`, `validate / package`, `workflow-conformance`, `PR title`) so the merge is not refused.

## Caveat

An opted-in binding stops after close-out, so the lane still restarts between items. A milestone bound before the change never auto-merges.

The sizing rule (at most one new mechanism, releasable alone) is on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).
