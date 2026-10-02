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
| CP2 The forge surface | Complete | See below |
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

### CP2 -- the forge surface

- `controller/forge.py` (B.1, B.2):
  - `PullRequest.merge_state` reads `mergeStateStatus`, which `PR_FIELDS` now requests. Any
    string passes through unvalidated; a missing or non-string value is undecidable, like every
    other field.
  - `Run` is `{id, workflow, status, conclusion, attempt, url}`. `conclusion` is `None` until the
    run completes (`gh` reports `""`).
  - `commit_runs(commit, branch, workflow)` runs `gh run list --commit --branch --event push
    --workflow --json databaseId,workflowName,status,conclusion,attempt,url --limit
    <forge.pr_list_limit>`. A full page is undecidable, like `list_prs`.
  - `merge_squash(number, *, head, subject, body)` runs `gh pr merge <n> --squash
    --match-head-commit <head> --subject <s> --body <b>`. It never re-reads and never sends
    `--auto`. A head that is not a full commit id is a `ValueError`, and nothing is sent.
  - Both are on the `Forge` protocol. The module docstring states the one merge.
- `tests/fake_gh.py` (B.4):
  - `mergeStateStatus` is a PR's stored value. Without one it is `UNKNOWN` for a closed or merged
    PR, `DRAFT` for a draft and `CLEAN` otherwise.
  - `pr merge` is modelled only in the admitted shape. It refuses unless the PR is open, its
    origin head is `--match-head-commit` and its state is `CLEAN`/`HAS_HOOKS`. A merge squashes
    the head onto the base in the bare origin (`git merge-tree`; a conflict refuses), with the
    subject and body as the message, and stores the PR as merged at that commit.
  - `--auto`, `--disable-auto`, `--admin` and `--delete-branch` are refused outright.
  - A PR's `merge_read_lag: N` makes the next N reads show the pre-merge PR.
    `merge_reply_lost` makes the merge happen and then exit 1 with a network error.
  - `run list --commit --branch --event --workflow` filters the state's `runs`, newest first.
- `tests/test_no_rewrite_invariants.py` (B.3): the `"pr", "merge"` pair is admitted only in
  `controller/forge.py`, and there only in a list literal that holds `--squash` and
  `--match-head-commit` and none of `--auto`, `--disable-auto`, `--admin` or `--delete-branch`.
  - In `forge.py`, the scan also fails on any `"merge"` constant outside such a list, and on a
    string built from constants (`+`, `%`, `str.format`, `str.join` of a literal, f-string) that
    contains the word.
  - `--auto`/`--disable-auto` in any argv literal anywhere in `controller/` fail.
  - `MergeShapeTest` pins both directions over synthetic sources. `ControllerScanTest` pins that
    `forge.py` holds exactly one admitted list and no other file holds one.
- Tests (`tests/test_forge.py`):
  - `MergeTest`: the exact argv through a runner spy (one record per call), with no re-read. The
    squash commit's parent, tree and message. A moved head is refused. Only `CLEAN`/`HAS_HOOKS`
    merge. An already-merged PR is refused. Read lag, a lost reply and a short head. The fake
    refuses the forbidden flags and the other merge shapes.
  - `RunsTest`: the exact argv, filtering by workflow, commit, branch and event, newest first.
    Also the full-page refusal and malformed records.
  - The merge state is read and passed through, including an unknown value. Malformed values
    are undecidable. `UndecidableTest` covers `run list` and `pr merge`.
  - `NoMergeOperationTest` now pins `merge_squash` as the forge's only merge operation. The
    module's teardown check refuses any other `gh pr merge` shape.
- Verification:
  - Narrow: `tests.test_forge` (39) and `tests.test_no_rewrite_invariants` (13) pass.
  - The dependent modules pass: `test_pull_request_lifecycle`, `test_milestone_branch`,
    `test_trunk_preflight`, `test_trunk_orchestration_e2e`, `test_release_txn` and the scan, 342
    tests.
  - `generate_no_policy_lifecycle.py --check` and
    `generate_external_implementation_review_decisions.py --check` pass.
  - `generate_plan_stage_decisions.py --check` reports a difference. It reports the same at the
    base, with this checkpoint's changes stashed, so this checkpoint did not cause it. It is left
    for CP6's full generator pass.
