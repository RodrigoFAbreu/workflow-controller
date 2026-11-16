# CI3: align CI with the agreed standard: CI, Tests and Build and publish

Item id: `workflow-controller-ci3`.

Status: implemented on the milestone branch, in review ([PR #370](https://github.com/RodrigoFAbreu/workflow-controller/pull/370)); plan: [CONTROLLER_CI3_PLAN.md](../ai-workflow/CONTROLLER_CI3_PLAN.md) (revision 3). Owner decision 2026-11-15. Priority: high. Complexity: medium. Follows [AO1](workflow-controller-ao1.md) in order, and should be planned before [CI2](workflow-controller-ci2.md).

## Request

The owner's words:

> Update this repository to follow our agreed CI standard, using the reusable workflows in RodrigoFAbreu/action-workflows at @main where applicable. Provide three manually dispatchable entry points: (1) CI: runs automatically on pull requests and pushes to main; runs Tests first, then Build and publish after success on main; a manual CI dispatch from main should also publish. (2) Tests: reusable through workflow_call; runs tests and checks. (3) Build and publish: reusable through workflow_call; handles building and publishing; test orchestration belongs in CI. Preserve existing test coverage, release rules, required check names and repository-specific safeguards. Keep custom validation in this repository when the shared workflows cannot cover it. Limit simultaneous runners to six per repository. Inspect the current setup first, implement the remaining changes, validate them, and open a PR for the owner's review. Avoid unrelated changes.

## Where the repository stands

[PR #357](https://github.com/RodrigoFAbreu/workflow-controller/pull/357) (merged) already standardized the workflows around `ci.yml`, with `validate.yml` (named "Validate") and `build-publish.yml`. The remaining differences from the standard:

- The reusable test workflow is named "Validate"; the standard calls it "Tests". The required check names (`validate / plan`, `validate / tests-result`, `validate / package`, `workflow-conformance` and `PR title`) must stay stable: they come from the calling job name `validate` in CI.
- `build-publish.yml` runs its own full validation and after-merge checks when dispatched by hand. The standard says test orchestration belongs in CI and a manual CI dispatch from main should also publish, so the manual publish path becomes a CI dispatch from main and Build and publish only builds and publishes.
- Nothing limits simultaneous runners to six per repository. (Correction, CI3: CI ran six shards, set by `CI_MAX_SHARDS` before this item; with `package`, the managed `workflow-conformance` job and the after-merge jobs the peak still exceeded six.) Options to evaluate in planning: `max-parallel` on the shard matrix, a repository-wide concurrency group, fewer shards, or the shared workflow capabilities.

## Notes

- The workflows are generated: changes go through `tools/ci_workflows.py` and the generation-drift check (see [CI and releases](../guide/ci-and-releases.md) and [Development](../guide/development.md)).
- Order: after AO1, because the Controller's auto-merge opt-in needs `release_workflow` set to `ci.yml`, which stays the entry point. It touches the same area as CI2 (CI follow-ups, 20-minute target), so plan CI2 after this item.
- Carry this out through the Controller as a work item, not by the orchestrator.

The sizing rule (at most one new mechanism, releasable alone) is on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## O1: the runner-limit option

Owner's choice, recorded 2026-11-16 through SignalHub (event `01a1e3c3-0bd2-7b74-b24d-a04cee0054f5`): **option B**, `CI_MAX_SHARDS = 3` (3 shards, one wave) with `max-parallel` always emitted on the shard matrix. The plan estimates about 54 minutes for the pull request against the 15-20 minute target; CP3 records the measured run and CI2 starts from it.

The table the plan gave (planner estimates, with the measured six-shard run of C2b, 11.0 to 19.0 minutes per shard, in brackets):

| option | settings | shards in flight | planner estimate |
| --- | --- | --- | --- |
| A | 6 shards, `TEST_MAX_PARALLEL = 3` | 3, two waves | about 54 minutes (about 38 measured-based) |
| B (chosen) | `CI_MAX_SHARDS = 3`, 3 shards | 3, one wave | about 54 minutes, fewer runner-minutes than A |
| C | `CI_MAX_SHARDS = 4`, the docs lane ordered around the shards | 4 | about 41 minutes plus the lane |
| D | the owner raises `RUNNER_LIMIT` | 6 | about 27 minutes (the pre-CI3 figure) |

## What was done

- CP1: the reusable workflow is `tests.yml`, named `Tests` (`validate.yml` is retired); `ci.yml`'s calling job is still `validate`, so the required check names are unchanged.
- CP2: `build-publish.yml` only builds and publishes. It runs no test job and each of `build`, `publish` and `release-target-*` requires `validate-result == 'success'`, so a direct dispatch cannot publish and a manual `CI` dispatch from `main` does.
- CP3: `RUNNER_LIMIT = 6`, `TEST_MAX_PARALLEL = 3`, `max-parallel` always on the shard matrix, `CI_MAX_SHARDS = 3`, `package` after `docs` (`if: !cancelled()`), the two `after-merge-*` jobs after `validate`; `RunnerLimitTest` proves the limit from the generated models.
- CP4: the guides, the README and this page. See [CI and releases](../guide/ci-and-releases.md#the-six-runner-limit).

Consequence for releases: the `after-merge-*` jobs now run after `validate`, so an ordinary release waits `validate` plus about 15 to 17 minutes instead of the slower of the two.

## Measured run

Not measured yet when this checkpoint was written: the pull request's `CI` run at its head was still queued. The figure is recorded here, and CI2 starts from it, once that run has finished; the planner estimate for option B is about 54 minutes.
