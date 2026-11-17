# CI2d: after-merge and trigger policy, and the automated timings refresh

Item id: `workflow-controller-ci2d`.

Status: implemented on the milestone branch (five checkpoints); plan: [CONTROLLER_CI2D_PLAN.md](../ai-workflow/CONTROLLER_CI2D_PLAN.md) (revision 4). A `ci:` change that edits CI tooling, the generated workflows, tests and pages; it publishes no release. A slice of [CI2](workflow-controller-ci2.md), split at its planning. Depends on CI2. Sources: scope items 11, 12 and 13 of the owner's decision of 2026-11-16 on the CI2 page, the automation of scope item 1, and [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317). Complexity: medium.

## Scope

One mechanism: what runs, and when, after a merge or a push.

11. After the merge, split the mutation job into 2 or 3 jobs within the six-runner cap (release wait from about 17 to 6 to 9 min).
12. Skip the test shards on `main` for documentation-only commits; keep the managed conformance job.
13. Do not run the full suite on every intermediate Draft-PR push; run it when the pull request is ready for review and on `main`.
- **The automated timings refresh** (the part of scope item 1 that [CI2](workflow-controller-ci2.md) leaves out): refresh `tools/test_timings.json` automatically so the staleness ratchet's limit (`MAX_UNTIMED_TEST_SHARE`) stays unreachable. CI2 refreshes the file once by hand.

Waste outside the suite that motivates this slice: 101 cancelled CI runs since 12 Nov cost about 5,500 runner-minutes, documentation-only merges to `main` still run all shards (about 155 runner-min), and the after-merge determinism job (12.6 to 15.7 min) and mutations job (11.3 to 17.2 min) delay releases. The planning rules are on [Planning rules](../planning-rules.md#next-in-order-and-the-sizing-rules).

## What was done

- **A push is classified over the pushed range (CP1).** `tools/ci_changes.py detect` reads the event payload: a push whose range `before..HEAD` changes documentation only skips the plan and the shards; a new branch, a force-push, an unreadable payload and any doubt run them. The `after-merge-*` jobs and the managed `workflow-conformance` job still run on such a push, so the release gate is unchanged.
- **A Draft pull request runs the shards from the first review-bound head (CP1, CP2).** Intermediate heads in the plan and implementation phases skip them; the review-bound set and its partition with the excluded set are in `tools/ci_changes.py`. `ci.yml` also runs on `ready_for_review`. The literal owner scope ("run it when the pull request is ready for review") is adjusted for the Controller's order (green checks, then ready); see O3.
- **The mutation check runs in three legs (CP3).** `main-check MODULE --shard I --of N`; `after-merge-mutations` and `release-target-mutations` are a matrix of three, the job ids and the release gate unchanged. The determinism check is not split (D5).
- **The timings refresh is automated (CP4).** `tools/timings_refresh.py` and the weekly `timings-refresh.yml` open a pull request when the merged file differs and passes the staleness ratchet.

The mechanisms are described on [Continuous integration and releases](../guide/ci-and-releases.md#documentation-only-pull-requests) and [The automated timings refresh](../guide/ci-and-releases.md#the-automated-timings-refresh).

## Estimates and measurements

The planner's estimate for the mutation module (`tests.test_scenarios_negative`, committed timings, `ci` profile): one shard 959.7 s, three legs 319.8 s, 320.1 s and 319.8 s (13, 17 and 16 tests; longest over mean 1.0006). That is an estimate; the real job time is read from the first `main` run after the merge and is added to this page by the next slice that edits it.

The measured times that motivate the item (CI2 sizing): the after-merge mutations job took 11.3 to 17.2 minutes and the determinism job 12.6 to 15.7 minutes; 101 cancelled CI runs since 12 November cost about 5,500 runner-minutes; a documentation-only merge to `main` ran every shard (about 155 runner-minutes) and runs about 30 runner-minutes now. These are the figures the change predicts to improve, not promises: [#317](https://github.com/RodrigoFAbreu/workflow-controller/issues/317) stays open until its last slice merges.

## Open points

- **O1. The determinism split** leaves the release wait at the determinism job's 12 to 16 minutes, not the 6 to 9 the scope hoped for; it is its own backlog item, [`workflow-controller-ci2h`](workflow-controller-ci2h.md). Drop it from `docs/backlog.yaml` if it is not wanted.
- **O2. The refresh token.** The owner creates the repository secret `TIMINGS_REFRESH_TOKEN` before the first scheduled run; the workflow fails with a message naming it until then. That a pull request opened with it collects all five required checks is **unproven** until the first run.
- **O3. The draft rule.** If the Controller should mark a pull request ready first and wait for checks after (a change to `controller/milestone_branch.py`, its own item), the rule becomes "no shards on any draft".
