# Active Milestone

## Status

**Implementing.** `workflow-controller-auto-merge-release-wait` (`docs/ROADMAP.md` step C4,
section 11.3). The plan is `docs/ai-workflow/CONTROLLER_AUTO_MERGE_RELEASE_WAIT_PLAN.md`, revision
7, approved at `a06aeb3` (`EXTERNAL_APPROVE`, review content id `fcdfd33b`). The base commit is
`854d25c`. Governing workflow version `2.2`, lifecycle authority Workflow 2.6.0. Pull request title
`feat: auto-merge an accepted milestone and wait for its release` (1.6.0).

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-settings-and-telemetry.md`.

## Goal

After `/accept-milestone`, nothing is left to decide, yet today a person still merges the pull
request, watches the release and runs the Controller again to close out. When a repository opts in
(`milestone_branches.pull_request.auto_merge`) and the operator's settings have not turned it off
(`merge.auto`), the Controller:
- squash-merges the ready pull request itself, only at the acceptance commit, through one
  head-bound `gh pr merge --squash --match-head-commit <A>` per attempt (never GitHub's own
  auto-merge request);
- stops and names the exit when something goes wrong after acceptance (a red check, a draft, a
  conflict, a moved head);
- waits for the publishing workflow's release of the squash commit, and records it or stops at
  `release_failed`;
- closes out and stops, instead of launching the next `/milestone-plan` in the same run;
- inside `run`, polls the pending states for up to `merge.wait_seconds` without a model token.

A policy without the key behaves exactly as 1.5.0 (I1).

## Checkpoint progress

| Checkpoint | Status | Notes |
|---|---|---|
| CP1 The two switches | Complete | See below |
| CP2 The forge surface | Not started | |
| CP3 The merge at readiness | Not started | |
| CP4 The release wait, close out and stop | Not started | |
| CP5 The bounded wait in `run`, and `status` | Not started | |
| CP6 Documentation and full verification | Not started | |

### CP1 -- the two switches

- `controller/repo_policy.py`: `milestone_branches.pull_request` admits two optional keys (A.1).
  - `auto_merge` (`MilestoneBranches.auto_merge`, default `false`) must be a boolean. `true`
    requires `merge_method: "squash"` and `ready_requires_green_checks: true`. Otherwise the policy
    is refused, naming that field and the reason. `true` with `release.enabled: false` is admitted.
  - `release_workflow` (`MilestoneBranches.release_workflow`, default `"main.yml"`,
    `DEFAULT_RELEASE_WORKFLOW`) is a non-empty file name without a `/` (and not `.` or `..`).
- `controller/settings.py`: the new type `bool` (only JSON `true`/`false`; an integer, string or
  `null` is refused) and three rows at generation 2 (A.2). `TABLE_GENERATION` is now 2.
  - `merge.auto`: boolean, default `true`.
  - `merge.wait_seconds`: 0-86400, default 3600.
  - `merge.poll_seconds`: 10-600, default 30.
  A generation-1 file gains the three rows through the existing additive fill, and nothing else
  moves. A 1.5.0 Controller sharing the file warns about the unknown `merge` section, and its
  `clean` refuses.
- `controller/milestone_branch.py`:
  - `Context` gains `auto_merge` (default `True`), `wait_seconds` (`0`) and `poll_seconds` (`30`).
  - `release_wait_applies(record)` is the binding's policy snapshot's `auto_merge`, so a policy
    edit never changes an in-flight milestone (I8).
  - `auto_merge_applies(record, ctx)` additionally requires `ctx.auto_merge`.
  Nothing calls them yet; CP3 and CP4 do.
- `controller/job.py`: `execute_step` (and `_execute_step_locked`) take `auto_merge`,
  `wait_seconds` and `poll_seconds` and put them on the preflight's `Context`.
- `controller/cli.py`: `_run_one_step` passes `merge.auto` and `merge.poll_seconds` from the
  effective settings. It passes `merge.wait_seconds` only when called with `wait=True`, which only
  `run` does. `step` always passes `0` (I6).
- Tests:
  - `tests/test_repo_policy.py` `AutoMergePolicyTest`: an absent key is off and `main.yml`, and the
    reference policy is unchanged; `true` is admitted with squash and green checks, and with
    releases off; it is refused without squash, without green checks, and as a non-boolean;
    `release_workflow` accepted and refused values; and the predicates over a snapshot with
    `true`, `false` and no key, under `merge.auto` on and off.
  - `tests/test_settings.py`: the pinned table now holds the generation-2 rows. `MergeRowsTest`
    covers the bool type both ways, the defaults, the fill of a generation-1 file (adds the three
    rows, moves nothing), `clean` of one, a generation-1 release sharing the file, and `show`.
    There are new refusal cases for the bool and the bounds. The two-release tests now stand in
    generation 3 for release N+1.
  - `tests/test_cli.py` `SettingsWiringTest`: the three rows reach `execute_step`, and only
    `run` gets the wait; `execute_step` puts them on the preflight's `Context`.
  - `tests/test_evidence.py`: the pinned `execute_step` parameter list gains the three keywords.
- The no-policy golden `tests/golden/no_policy_lifecycle.json` was regenerated (I1, revision 6).
  Its diff from the base is exactly the three `merge.*` keys in each of the 8 job records'
  `controller_settings` `values` and `sources`. `tests/test_trunk_preflight.py`
  `NoPolicyGoldenMergeRowsTest` pins this: with those keys removed, the golden's SHA-256 equals
  the base golden's (`41c17699…`). The generator's docstring records the rewrite.
- Verification:
  - Narrow: `tests.test_settings`, `test_repo_policy`, `test_cli`, `test_milestone_branch` and
    `test_trunk_preflight` (343 tests) pass, and
    `tests/golden/generate_no_policy_lifecycle.py --check` passes.
  - Full suite (`tools/run_tests.py` under a reaping subreaper, without `FORCE_COLOR`): the first
    run had one failure, the `execute_step` parameter pin in
    `tests.test_evidence.WorkflowQueryFeedbackPathTest`. After updating the pin, the failed shard
    (5) passed on replay; shards 0-4 had already passed. That makes 2713 tests green.
