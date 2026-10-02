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
| CP3 The merge at readiness | Complete | See below |
| CP4 The release wait, close out and stop | Complete | See below |
| CP5 The bounded wait in `run`, and `status` | Complete | See below |
| CP6 Documentation and full verification | Complete | See below |

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

### CP3 -- the merge at readiness

- `controller/milestone_branch.py` (C.1-C.6):
  - `_merge_step(ctx, key, record, head, pr)` replaces both `_merge_gate` returns: the end of
    `_readiness` and the `READY` cell. Without `auto_merge_applies` it returns `_merge_gate`
    unchanged (I1, C.6), whatever the record's `merge` field says.
  - Otherwise it applies C.2's rows, first match deciding, after the cell's own `MERGED`,
    `CLOSED` and `_observe_branch` handling. In order:
    1. A local tip past `A` gates `post_acceptance_commits` with the `READY` text (C.4 b and c).
    2. A remote branch or pull request head that is not `A` gates `pr_head_not_accepted`.
    3. A draft gates `merge_held`.
    4. Readiness's own `_checks_gate`. `checks_failing` and `checks_cancelled` gain "the
       Controller merges once a re-run turns the checks green".
    5. `DIRTY` gates `merge_pending` at once. `BEHIND` gates `integration_required`.
    6. A `merge.state` of `accepted` gates `merge_pending`. The gate names the hand merge and
       `milestone-binding --new-pr` after closing.
    7. Any state other than `CLEAN`/`HAS_HOOKS` gates `merge_pending`, naming the state.
       `BLOCKED` names a requirement other than the checks.
    8. At three attempts, the refusal.
    9. Otherwise one send.
  - `_send_merge` (C.3):
    - It writes the intent first: `merge = {state: "sending", head: A, attempts: k+1,
      last_attempt_at}`, with `merge_sent` on the first attempt.
    - Then it sends `merge_squash(n, head=A, subject="<title> (#n)", body=<body>)`.
    - Success writes `accepted` (`merge_accepted`) and re-reads. `MERGED` goes into the
      existing merged-PR handling through `_pr_left_open`; still `OPEN` is the accepted
      `merge_pending`.
    - A `ForgeError` re-reads: merged is adopted. Otherwise `merge_refused` is written once
      per distinct message (stderr when present), and the outcome is the waitable
      `merge_pending` below three attempts, or a `BranchBindingError` at three. `attempts` is
      never reset.
  - A crash leaves `state: "sending"`. The next step's table decides as for any record.
  - `_readiness` and `_merge_step` return a record when the Controller merged. `_branch_cells`
    then continues into close-out.
  - `Gate.waitable` (default `False`) is set on the waitable outcomes listed in E.1. These are
    `pr_head_not_accepted`, `checks_pending` and `merge_pending` (except `DIRTY`). CP5's
    `waiting_preflight` reads it. Readiness's own gates are not marked here.
  - `GATE_CODES` gains `merge_pending` and `merge_held`. `decision.BRANCH_GATE_TEXTS` has a text
    for each.
  - `predict` handles a `READY` record when auto-merge applies: `merge` with no `merge` field or
    `sending`, and `wait_merge` (gate `merge_pending`, "as of") when `accepted`.
    `cli.cmd_explain` passes `merge.auto` into the context.
- Tests (`tests/test_pull_request_lifecycle.py`, `AutoMergeTest`, 20 cases;
  `AutoMergeUnchangedTest`):
  - The merge at readiness: the exact argv, no `--auto`, the events, closed out.
  - A held `READY` record merging at the next step, and `HAS_HOOKS`.
  - First match: a local commit past `A` with a failing check is `post_acceptance_commits`.
  - Moved heads:
    - a pushed local commit and an unpushed one are both `post_acceptance_commits`;
    - a push on GitHub only, while checks are pending, then green, is refused by
      `_observe_branch` with no merge call;
    - a lagging PR head and a deleted remote branch are `pr_head_not_accepted`.
  - A draft is `merge_held`, then merges once marked ready.
  - Checks after acceptance: pending, failing, and cancelled with `CLEAN`.
  - `UNKNOWN`, a stale `DRAFT`, `BLOCKED`, `UNSTABLE` and an unknown value are `merge_pending`
    (waitable). `DIRTY` is `merge_pending` (not waitable). `BEHIND` is `integration_required`.
  - A moved trunk without `BEHIND` merges and is verified.
  - Read lag: `accepted`, never re-sent, then adopted. A lost reply is adopted in the same step.
  - Crashes between the intent and the call, and between the call and the outcome (with and
    without lag; the re-send's "already merged" refusal is adopted).
  - The attempt budget: one `merge_refused` per distinct message, the refusal at three, never
    sent again, then a hand merge closes out.
  - A person's own auto-merge request is neither read nor withdrawn.
  - `merge.auto: false` gives 1.5.0's `merge_pull_request` exactly, also with a `merge` field.
  - `predict`, and a binding without the key unchanged.
- Verification:
  - New: `AutoMergeTest` and `AutoMergeUnchangedTest`, 21 tests, pass.
  - Related modules pass: `test_pull_request_lifecycle`, `test_milestone_branch`,
    `test_write_containment`, `test_no_rewrite_invariants`, `test_decision`, `test_cli`,
    `test_trunk_preflight` and `test_hints_parse`, 483 tests. `test_job` and
    `test_job_validation` (274) also pass.
  - `generate_no_policy_lifecycle.py --check` passes.

### CP4 -- the release wait, close out and stop

- `controller/release_txn.py` (D.2, D.3):
  - `classify` gains `verify_assets` (default `True`). With `False`, a tag at the commit with a
    published release is `ALREADY_RELEASED` "(assets not verified by the Controller)", and
    `release_problems` is never called: nothing is downloaded and no policy command runs. Every
    existing caller keeps the default.
  - `covering_tag(ctx, commit)` is the lowest-versioned matching remote tag whose commit is a strict
    descendant of `commit` on the fetched trunk, or `None`.
- `controller/milestone_branch.py`:
  - `_release_wait(ctx, key, record)` runs at the top of `_close_out_on_branch` and
    `_close_trunk_step3` (D.1). It returns the record, or a `release_pending` (waitable) or
    `release_failed` gate. A record with a `release` field is settled and skips it, as does a
    binding whose snapshot has no `auto_merge: true`.
  - D.1's table: a `MERGED` record of an auto-merge binding gets `release = {state: SKIPPED,
    reason}` and `release_wait_skipped`, and closes out as 1.5.0. `MERGED_REWRITTEN` never reaches
    close-out.
  - D.2: the policy committed at `m` decides. No policy, or releases off, settles `NONE`. Otherwise
    `classify(ReleaseContext(..., git_runner=ctx.runner), m, verify_assets=False)`.
    - `ALREADY_RELEASED` records state, version, tag and URL (`released`).
    - `NO_CHANGE`/`ABANDONED_VERSION` record state, version and detail (`release_settled`).
    - Any other state, or a `ReleaseTransactionError`, is a failure.
  - D.3 (`_decide_release`): `RELEASE_DUE`/`RESUME` read only
    `commit_runs(m, trunk, <snapshot release_workflow>)`.
    - None reported, or any not completed: `release_pending`.
    - All completed: classify again (the race); then the failed runs, or the published-nothing
      text.
    - Every failure is first checked against the covering tag. Published: settled `SUPERSEDED`
      (`version`, `tag`, `url`, `commit`). Unpublished: the covering commit's runs decide the same
      way.
    - A runs-based failure at `c` (`m` or the covering commit) is then checked against the trunk
      tip `d`. If `d` strictly descends from `c`, `d`'s runs pending is `release_pending`, naming
      `c`'s runs and `d`'s. Once they complete, `m` is classified again and the covering tag
      re-checked, else `release_failed` names both.
  - `release_failed` is written as an event once per distinct detail (the binding's last
    `release_failed` event). The record gains no field until the release settles.
  - The gates name the runs, a "re-run the failed jobs of <url>" exit per failed run, and the hand
    publication with `tools/release.py`.
  - D.4: `Proceed.stop`. After a close-out whose release settled (`stops_after_close`), the branch
    side returns `Proceed(binding=<CLOSED record>, action="closed_out", stop=True)` instead of the
    trunk start. `_on_trunk` returns the same when its reconciliation closed out such a binding.
  - `predict`: `wait_release` for a `MERGED_SQUASHED` record whose wait applies and has not
    settled. After it, `close_out` names the stop and the release.
  - `release_text(release)` words a settled release.
- `controller/decision.py`: gate texts for `release_pending` and `release_failed`. `GATE_CODES`
  gains both.
- `controller/job.py`:
  - `_execute_step_locked` returns `closed_out_decision(binding)` for `Proceed.stop`, after the
    release re-check and before `decide`. That is a no-action `Decision` with reason
    `closed_out_released` (`REASON_CLOSED_OUT_RELEASED`), phase `MILESTONE_COMPLETE`, the release
    as its evidence. No worker, no job record.
  - `controller/cli.py`'s `no_action` run event adds `release` for it.
- CP3's `test_the_merge_is_sent_at_readiness_and_closes_out` now expects the stop. Its policy
  releases nothing, so the wait settles `NONE`.
- Tests:
  - `tests/test_pull_request_lifecycle.py` `ReleaseWaitTest`, over a real `version_change`
    classification, tags in the bare origin and the fake forge's releases and runs:
    - released with tag, version and URL, with `release_problems`/`_run_policy_command` patched to
      fail and no `release download` call;
    - `NO_CHANGE` settling in the merge step;
    - releases turned off at `m` (`NONE`);
    - a failed `Workflow conformance` run, then no `Main` run, then a queued one, then success:
      pending throughout, never failed;
    - the snapshot's `release_workflow` (`release.yml`) as the only workflow read;
    - a failed publishing run (one event over two polls, then settled by a publication);
    - success with nothing published;
    - the re-classify race;
    - `INVALID_TRANSITION` and a `ReleaseTransactionError`;
    - a tag off the trunk (`COLLISION_TAG_ELSEWHERE`, not a descendant);
    - `classify` called with the preflight's `git_runner` and `verify_assets=False`;
    - the trunk side after a manual `git switch main`;
    - `predict`;
    - the next step's `trunk_start` with the explicit base.
  - `SupersededReleaseTest`:
    - `m`'s queued run cancelled by a later push whose run publishes at the descendant (pending,
      including before the run is listed, then `SUPERSEDED`);
    - the same with the descendant raising the version;
    - a failed run of `m` recovered by a later run;
    - a later run resuming `m`'s own tag (`ALREADY_RELEASED`);
    - a covering tag left unpublished by its failed run, then a newer run that publishes it, or
      completes without publishing (failed, naming both);
    - a failed or cancelled run with the trunk tip still `m` (failed at once).
  - `ReleaseWaitSkippedTest`: a hand merge commit (`SKIPPED`, `release_wait_skipped`, today's
    close-out and trunk start), and a rebase (`MERGED_REWRITTEN`, no wait).
  - `ReleaseWaitUnchangedTest`: a binding without the key closes out and starts the trunk.
  - `tests/test_release_txn.py`: `verify_assets=False` downloads and verifies nothing, and the
    default still reports the mismatch; `covering_tag`.
  - `tests/test_trunk_preflight.py`: the stop through `step` exits 0, launches no worker and writes
    no job record. The run's `no_action` event names the release. The next `step` launches
    `/milestone-plan <trunk tip>`.

- Verification (under a reaping subreaper, without `FORCE_COLOR`):
  - New and related: `test_pull_request_lifecycle`, `test_milestone_branch`, `test_release_txn`,
    `test_decision`, `test_trunk_preflight`, `test_cli`, `test_evidence`,
    `test_write_containment`, `test_no_rewrite_invariants`, `test_hints_parse`, `test_forge` and
    `test_trunk_orchestration_e2e`. 902 tests pass.
  - `test_job` and `test_job_validation` (274) pass.
  - `generate_no_policy_lifecycle.py --check` passes.

### CP5 -- the bounded wait in `run`, and `status`

- `controller/milestone_branch.py`:
  - `waiting_preflight(ctx, *, requested_work_item_id, on_wait, sleep, monotonic)` (E.2) wraps
    `repository_preflight`. While the outcome is a waitable gate and less than
    `ctx.wait_seconds` has passed since the step's first waitable gate, it calls
    `on_wait(code, deadline)` once per gate code, sleeps `min(ctx.poll_seconds, remaining)` and
    runs the full preflight again. One budget per step, across gate changes. A gate reached at the
    deadline is returned, not announced. `wait_seconds` 0 returns the first outcome without
    sleeping. The deadline is `ctx.clock()` plus the budget. The default sleep and clock are the
    module attributes `_sleep`/`_monotonic`, so a test can replace them for a whole `run`.
  - E.1 at readiness: `_readiness_wait` marks `checks_pending` (from the checks and from a title or
    body edit) and `pr_head_not_accepted` waitable when `auto_merge_applies`. CP3 and CP4 already
    mark the `READY`-cell and release-wait gates. Without auto-merge, or with `merge.auto: false`,
    readiness gates stay unwaitable.
  - F: `inspect`'s `milestone_branch` block and `status --json`'s `bindings` entries gain
    `merge` and `release` (the record's fields, `None` when absent). `merge_release_text` appends
    `, merge: <state> at <head> (attempt <n>)` and `, release: <release_text>` to the `status`
    `milestone:` line and `inspect`'s `milestone branch:` line, only when present.
- `controller/job.py`: `execute_step` and `_execute_step_locked` gain `on_wait`. The preflight
  runs through `waiting_preflight` under the lock the step already holds (E.4). Only the final
  outcome is recorded: a gate's job record, or none for the D.4 stop.
- `controller/cli.py`: `_run_one_step` passes `on_wait`, which writes the run event `waiting`
  (`gate`, `deadline`). `wait_seconds` is still `0` for `step` (CP1).
- `controller/observe.py`: `follow` renders `waiting at <gate> until <deadline>`.
- Ctrl-C (E.3) needs no new code. `KeyboardInterrupt` leaves the sleep, the lock's context
  manager releases the lock, and `cli.main`'s `finally` writes `run_interrupted`. No job record
  is written for the waiting step.
- No job-record field is added, so the no-policy golden is unchanged.
- Tests:
  - `tests/test_pull_request_lifecycle.py` `WaitingPreflightTest`, with a fake clock whose sleeps
    run scripted GitHub changes:
    - one call waits through `checks_pending` → `READY` → `merge_pending` (`UNKNOWN`) → merged →
      `release_pending` → released → closed out and stopped, announcing each gate once with the
      deadline;
    - the budget expiring at 100 s with a 30 s poll sleeps 30, 30, 30, 10 and returns the last
      gate;
    - a repeated gate is announced once;
    - `wait_seconds` 0 never sleeps;
    - `checks_failing`, `checks_cancelled` and `integration_required` return at once;
    - readiness's lagging pull request head is waitable.
  - `ReadinessNotWaitableTest`: a binding without the key, and `merge.auto: false`.
  - `tests/test_trunk_preflight.py` `RunWaitTest`, through `cli.main` with a release-wait policy:
    - one `run` step waits from `checks_pending` through `merge_pending` and `release_pending` to
      the stop. It exits 0, launches no worker and writes no job record. The run log reads
      `run_started`, `step_started`, three `waiting`, `no_action`, `run_ended`. `status` and
      `status --json` show the merge and the release;
    - the budget (`merge.wait_seconds: 50`) expiring writes the `GATE_BLOCKED` job record;
    - `step`, and `run` with `merge.wait_seconds: 0`, never sleep and log no `waiting`;
    - `inspect` (JSON and text) shows the merge;
    - Ctrl-C: a child `run` is sent `SIGINT` once its log shows `waiting`. The run ends
      `interrupted` with `run_interrupted` last, no job record is written and the binding is
      unchanged. The next `step` merges from the last written state.
  - `ObservationTest`: the `inspect` binding key set is the old one plus `merge` and `release`,
    both `None`. `status --json` entries carry them as `None`.
  - `tests/test_cli.py`: `on_wait` writes the `waiting` run event. `tests/test_evidence.py`: the
    `execute_step` parameter pin gains `on_wait`. `tests/test_observe.py`: the `waiting` text.

- Verification (under a reaping subreaper, without `FORCE_COLOR`):
  - The full suite: `python3 tools/run_tests.py`, 2793 tests in 6 shards, all pass.
  - The `ObservationTest`/status pins were then tightened, and `test_trunk_preflight` plus the two
    new `test_pull_request_lifecycle` classes were re-run: 45 tests pass.
  - `generate_no_policy_lifecycle.py --check` passes unchanged.

### CP6 -- documentation and full verification

- `docs/adr/0009-auto-merge-and-release-wait.md` (new): the two switches, the one head-bound
  merge and why GitHub's auto-merge request is never used, the narrowed invariant I3', a
  person's hold, the read-only release wait, close out then stop, the bounded wait in `run`,
  what stays human, and the rejected alternatives. `docs/README.md` gains its row and names the
  new guide section.
- `docs/guide/milestone-branches.md`: the auto-merge flow lines, the four new gates in the
  readiness table, "Merge and close-out" naming the opt-in, and a new section "Auto-merge and
  the release wait": the policy keys, `merge.auto`, the first-match merge table, the one `gh pr
  merge` call and its crash safety and three-attempt refusal, the branch-side-only merge (a
  `run` from `main` merges nothing), the release wait's rows, close out then stop, hand merges,
  the wait in `run`, what `explain`/`status`/`inspect` show, the events, and merge queues being
  unsupported.
- `docs/guide/ci-and-releases.md`: "Repository settings" (the Controller's own merge, never
  GitHub's request, squash merging required, `release_workflow`, merge queues unsupported, this
  repository not opted in yet), and resolving `release_failed` under "Checking a release by
  hand".
- `docs/guide/automation.md`: "Never merges, never rewrites" restated as I3'.
- `docs/guide/runtime.md`: the three `merge` rows, the boolean type, what each row does, the
  example file at generation 2, and the generation-2 fill.
- `docs/guide/commands.md`: `run`'s bounded wait and the stop; `status`'s `merge:`/`release:`
  suffixes and the `bindings` entries' fields. `docs/guide/troubleshooting.md`: the merge
  gates, the release gates, exit 45 while a `run` waits, and the stop after close-out.
- `docs/guide/concepts.md` and `README.md`: the merge is the Controller's when the policy opts
  in.
- The 1.6.0 notes are this narrative's `## Release notes` section. `release_notes.notes_problem`,
  `paragraph_problem` and `block_problem` all return `None`; the longest line is 71 bytes.
- The hints scan (`tests/test_hints_parse.py`) finds no new interpolation, so `SAMPLES` is
  unchanged: no new printed `workflow-controller ...` hint came with this milestone.
- Verification (under a reaping subreaper, without `FORCE_COLOR` or `PYTHONPATH`):
  - The full suite: `python3 tools/run_tests.py`, 2793 tests in 6 shards, all pass.
  - Goldens: `generate_no_policy_lifecycle.py --check`,
    `generate_external_implementation_review_decisions.py --check` and `--release 2.6.0
    --check`, and `generate_plan_stage_decisions.py --release 2.6.0 --check` are current.
    `generate_plan_stage_decisions.py --check` (2.5.1) reports its documented `AMENDING_PLAN`
    difference, the same as at the base; `tests.test_golden_plan_stage_decisions`, the check for
    that file, passes in the suite.
  - The protected-path diff from `854d25c` over `.workflow-controller/policy.json`,
    `pyproject.toml`, `setup.py` and `.github/workflows/` is empty (I9).

## Release notes

### Auto-merge and the wait for the release (1.6.0)

**The Controller can merge an accepted milestone.** A repository opts
in through its policy (`milestone_branches.pull_request.auto_merge`,
squash mode with green-check readiness only). The Controller then
squash-merges the ready pull request itself once GitHub reports it
mergeable, through one `gh pr merge --squash --match-head-commit` at
the acceptance commit per attempt, so GitHub merges exactly that commit
or refuses. It never enables GitHub's auto-merge request. A draft, a
commit after the acceptance commit, a red or cancelled check, a
conflict or a branch that must be updated stops it at a gate that names
the exit. New gates: `merge_pending` and `merge_held`. The settings
file's `merge.auto` turns the merge off on the machine.

**It waits for the release, then stops.** After the verified squash,
the Controller classifies the squash commit read-only, waits while the
publishing workflow (`release_workflow`, `main.yml` by default) runs,
records the published release in the binding, or stops at
`release_failed` naming the run. A later trunk run that publishes the
release covering the commit settles the wait. It downloads no asset and
runs no repository command. After close-out the step ends, exit 0,
instead of planning the next milestone. New gates: `release_pending`
and `release_failed`.

**`run` waits, `step` does not.** Inside `run`, the pending checks,
merge and release gates are polled every `merge.poll_seconds` (30) for
up to `merge.wait_seconds` (3600) per step, without a worker. `status`,
`status --json` and `inspect` show the merge and the release.

**Compatibility.** A policy without the key behaves exactly as 1.5.0.
Controller 1.5.x refuses a policy with the new keys, so install 1.6.0
before opting in. The settings file gains three `merge` rows (table
generation 2); a 1.5.0 Controller sharing it warns about them.
