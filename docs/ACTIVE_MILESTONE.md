# Active Milestone

## Status

**Awaiting functional review.** `workflow-controller-auto-merge-release-wait` (`docs/ROADMAP.md`
step C4, section 11.3). The plan is `docs/ai-workflow/CONTROLLER_AUTO_MERGE_RELEASE_WAIT_PLAN.md`,
revision 7, approved at `a06aeb3` (`EXTERNAL_APPROVE`, review content id `fcdfd33b`). The base
commit is `854d25c`. Governing workflow version `2.2`, lifecycle authority Workflow 2.6.0. Pull
request title `feat: auto-merge an accepted milestone and wait for its release` (1.6.0).

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for phase and checkpoint status. The
previous milestone's narrative is archived at
`docs/milestones/completed/workflow-controller-settings-and-telemetry.md`.

All six checkpoints are complete, and implementation revision 6 has technical approval (`3d875a7`,
`EXTERNAL_APPROVE` of bundle `c6153442`, review content id `42a96a79`). The functional review
checklist is below.

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

### Self-review (`SELF_REVIEWING_IMPLEMENTATION`)

The whole diff from `854d25c` was reviewed before implementation revision 1's bundle. No
Blocking findings. One Important finding is fixed:
- I1: the release wait's `release_failed` once-per-detail check (`_release_failed_shown`) parsed
  the binding's events file with a strict `json.loads`. That log is presentation only
  (`runtime.append_jsonl`: one `write()` per line, no `fsync`), so a torn line made every later
  step of the binding fail with a `JSONDecodeError`. It now skips a line that does not parse or
  is not an object; at worst the event is written once more. The new
  `ReleaseWaitTest.test_a_torn_event_line_does_not_stop_the_release_wait` errors without the fix
  and passes with it.

### Local implementation review, round 4 (implementation revision 5)

`REVISE` on bundle `3db4fff4` (review content id `41282e0c`), no Blocking findings:
- Important 1 (fixed, `4fd9bbe`): revision 4 recorded a merge as `accepted` only when GitHub's
  refusal of a duplicate said "already merged", a text plan C.3 says no code keys on. A lost
  reply exits like a refusal, so duplicates refused with any other wording still reached the
  terminal manual-merge refusal for a merged pull request. Before counting a failed send as a
  refusal, and before sending a `sending` record again, the Controller now fetches the trunk and
  looks for the pull request's squash commit (`_squash_on_trunk`); when it is there, the merge is
  `accepted` (`merge.squash_commit`) and nothing more is sent. The text match is removed. New
  tests: `AutoMergeTest.test_a_merge_whose_reply_is_lost_at_the_budget_is_adopted_whatever_the_text`
  (fails at revision 4 with the terminal refusal) and
  `test_a_crash_before_the_last_send_waits_and_sends_no_more`; three existing tests now expect
  one send where a duplicate used to be refused.
- Important 2 (fixed): this record and `TEST_RESULTS.md` carry revision 5's clean run.
- Optional 1 (fixed, `4fd9bbe`): the "may already have merged" text no longer names a lost reply.
- Optional 2 (fixed, `085c6f2`): `docs/guide/troubleshooting.md` names both waits.
- Verification, colour off, under a reaping subreaper: `tools/run_tests.py` exit 0, 2802 tests,
  6 shards, every shard PASS on attempt 1, coverage exact. Goldens as at CP6 (the 2.5.1
  plan-stage generator's documented `AMENDING_PLAN` difference only). The protected-path diff
  from `854d25c` is empty.

### Manual external implementation review, round 3 (implementation revision 6)

Codex `REVISE` on bundle `ed2169dd` (review content id `707a0ad1`), no Blocking findings:
- Important 1 (fixed, `a595551`): `_squash_on_trunk` took any first-parent trunk commit whose
  subject ended in ` (#<n>)` for the pull request's squash, so an unrelated commit carrying the
  suffix, followed by a real refusal, was recorded as `accepted` and stalled at `merge_pending`.
  A candidate is now adopted only when it has one parent and its tree is the acceptance commit
  squashed onto that parent (`_squash_content`, the content check `verified_squash` already
  made, factored out). New test:
  `AutoMergeTest.test_an_unrelated_trunk_commit_with_the_suffix_is_not_adopted` (fails at
  revision 5 with the `accepted` gate, passes with the fix); the guide names the content check.
- Verification, colour off, under a reaping subreaper: `tools/run_tests.py` exit 0, 2803 tests,
  6 shards, every shard PASS on attempt 1, coverage exact.

## Functional review checklist

Round 1, implementation revision 6: technical approval `3d875a7`, reviewed implementation head
`e67ba68`, implementation bundle `c6153442`, PR #18 (draft) at `3d875a7`. Every expected result
below was measured on 2026-10-02 against `3d875a7` in a scratch directory (`/tmp/c4-fr`), by
running the blocks exactly as written. Values that cannot repeat are shown as `<...>`, and the
helpers print the loaded target's own paths and commits as `<R>` (its clone), `<RT>` (its runtime
root), `<A>` (the acceptance commit), `<B0>` (the branch point), `<m>` (the squash commit), `<d>`
(a later trunk commit) and `<L>` (a later branch commit); times print as `<t>`.

The automated state is current: the last full run (`TEST_RESULTS.md`, `e67ba68`: 2803 tests, 6
shards, every shard PASS on attempt 1) covers the same code, since only
`docs/ai-workflow/WORKFLOW_STATE.json` changed after it (`6e51568`, `3d875a7`). Flow M reruns this
milestone's own test modules.

Nothing in flows A-P writes to this repository, its remote, GitHub, the user's settings file
(`~/.config/workflow-controller/`) or the shared runtime root
(`~/.local/state/workflow-controller/`), and no flow uses the installed Controller (1.5.0) other
than to compare with it. Every command runs under `/tmp/c4-fr`, with `XDG_CONFIG_HOME` pointing
into it, an explicit `--runtime-dir` and an explicit `--settings` file per target. This repository
is only read (flows A, M, N and P). The only GitHub access is the read-only `gh pr checks 18`
(flow O). Flow Q is an **OPTIONAL LIVE** flow on GitHub; it is not run without your explicit
authorization.

How the merge and the release are exercised: `drive stop <name> <kind>` builds a disposable
target (a bare origin and a clone carrying the real Workflow 2.5.1 tree and a policy) and drives
it with the local build, the scripted fake worker (`tests/fake_claude.py`) and the stub Workflow
Manager through the plan, the implementation and the acceptance commit, leaving pull request #1 a
draft with no checks. `<kind>` is `none` (squash mode, no `auto_merge`: 1.5.0's behaviour),
`auto` (`auto_merge: true`, releases off) or `release` (`auto_merge: true`, releases on with
Conventional Commits, `v1.0.0` tagged and published at the trunk tip). GitHub is the suite's fake
`gh` (`tests/fake_gh.py`): it merges only `pr merge <n> --squash --match-head-commit <sha>` at a
matching head and a `CLEAN`/`HAS_HOOKS` merge state, refuses `--auto`, and serves the workflow
runs and the releases. `act <what>` plays GitHub's and other people's side (checks, merge states,
drafts, read lag, a lost reply, pushes, hand merges, workflow runs, publications).

### Setup

Run each code block below as one non-interactive `bash` script (for example, save it to a file
and run `bash <file>`), in the order given: not in `zsh`, whose `$VAR:r`-style modifiers break
the refspecs, and not pasted line by line. Every block after setup starts with
`. /tmp/c4-fr/fr.sh`, which unsets `FORCE_COLOR`, `PYTHONPATH`, `WORKFLOW_CONTROLLER_SETTINGS`,
`GH_TOKEN`, `GITHUB_OUTPUT` and `FAKE_GH_FAIL` and points `XDG_CONFIG_HOME` into the scratch
directory. Never set `PYTHONPATH=.`, and run everything in the foreground. Each flow builds its
own targets, so the flows are independent and can run in any order after setup; a rerun replaces
a target of the same name.

**Setup.** The scratch directory, the helpers and the driver (both saved from this file, below), a
scratch bare origin pinned to the base and the head, a clone of the head, a local build of it in a
throwaway virtual environment, the offline stub Workflow Manager and a reaping-subreaper wrapper
for the unit-test runs (a Controller-launched session needs it: its orphans otherwise stay
zombies), then one smoke target:

```bash
S=/tmp/c4-fr; R0=/home/rodrigo/Workspace/workflow-controller; rm -rf "$S"; mkdir -p "$S"; cd "$S"
for f in fr.sh drive.py; do
  awk -v f="$f" '$0 == "<!-- " f " begin -->" {on = 1; next} $0 == "<!-- " f " end -->" {on = 0} on' \
    "$R0/docs/ACTIVE_MILESTONE.md" | sed '1d;$d' > "$S/$f"
done
. "$S/fr.sh"
git init -q --bare "$S/origin.git"
git --git-dir="$S/origin.git" fetch -q --no-tags "$R0" "+$BASE:refs/heads/main" "+refs/tags/v*:refs/tags/v*" \
  "+$H:refs/heads/milestone/workflow-controller-auto-merge-release-wait"
git clone -q "$S/origin.git" "$S/src" && git -C "$S/src" checkout -q --detach "$H"
python3 -m venv "$S/venv" && "$S/venv/bin/pip" -q install "$S/src"
printf '%s\n' '#!/bin/sh' 'case "$1" in verify|status) echo "workflow 2.6.0 (full profile) -- clean"; exit 0;; *) echo "stub workflow-manager: unknown subcommand $1" >&2; exit 1;; esac' > "$S/wm"; chmod +x "$S/wm"
cat > "$S/reap.py" <<'EOF'
"""Run argv as a child under a reaping subreaper: orphans re-parented here are collected."""
import ctypes, os, sys
ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
pid = os.fork()
if pid == 0:
    os.execvp(sys.argv[1], sys.argv[1:])
status = 1
while True:
    try:
        reaped, st = os.waitpid(-1, 0)
    except ChildProcessError:
        break
    if reaped == pid:
        status = os.waitstatus_to_exitcode(st)
        break
sys.exit(status)
EOF
head -1 "$S/drive.py"; grep -c . "$S/fr.sh"; "$W" --version
drive stop smoke auto; use smoke; act show
```
Expected: `"""Functional-review driver for workflow-controller-auto-merge-release-wait:`, `47`
(the helper file's non-empty lines), `workflow-controller 1.5.0` and
`runtime: package (local build from 3d875a7106b5)`: the version stays tag-derived until the
`v1.6.0` tag exists. Then eight `-- driver: workflow-controller step -> exit <n>` lines (`0`, `0`,
`10`, `0`, `0`, `0`, `0`, `10`: the plan, the bind and `/review-plan`, the manual plan-review
gate, the plan approval and CP1 with the Draft PR, CP2, the bundle, the local review, the manual
implementation-review gate), `-- driver: smoke (auto) accepted at <...>; PR #1 is a draft with no
checks; now run: use smoke`, and
`PR #1 OPEN draft=True head=<...> mergeStateStatus=DRAFT; pr merge calls: 0; origin main <B0> 'Policy: auto'`
(the acceptance commit is not pushed yet).

The helpers (`fr.sh`) and the driver (`drive.py`), extracted by the setup block:

<!-- fr.sh begin -->
```bash
unset FORCE_COLOR PYTHONPATH WORKFLOW_CONTROLLER_SETTINGS GH_TOKEN GITHUB_OUTPUT FAKE_GH_FAIL
export S=/tmp/c4-fr R0=/home/rodrigo/Workspace/workflow-controller
export W=$S/venv/bin/workflow-controller XDG_CONFIG_HOME=$S/xdg
export H=3d875a7106b5c3e1be0413dd12a50253dfa311a7 BASE=854d25cf4c53ec63a7272a34be31c618f04c8993
# drive stop NAME KIND: a fresh target (KIND none, auto or release) driven by $W to the acceptance commit
drive() { (cd /tmp && python3 "$S/drive.py" "$@"); }
# use NAME: load a target: $R its clone, $O its bare origin, $RT its runtime root, $T its directory,
# $A the acceptance commit, $B0 the branch point; the fake gh first on PATH
use() { . "$S/t/$1/env"; export PATH="$GHBIN:$PATH" N=$1; M=; D=; L=; }
# act WHAT ARGS: GitHub's side of the scenario, on the loaded target (see drive.py)
act() { (cd /tmp && python3 "$S/drive.py" act "$N" "$@") | norm; }
# wcx ARGS: the local build against the loaded target's runtime root and settings file, the fake worker
wcx() { (cd /tmp && "$W" --runtime-dir "$RT" --settings "$T/settings.json" --workflow-manager "$SM" \
  --claude-binary "$S/src/tests/fake_claude.py" --timeout 60 "$@"); }
# norm: the loaded target's paths and commits as <R>, <RT>, <A>, <B0>, <m>, <d>, <L>; times as <t>
norm() { local e=() v; for v in RT:RT R:R A:A B0:B0 M:m D:d L:L; do local x=${v%%:*}; local s=${!x}
  [ -n "$s" ] && e+=(-e "s#$s#<${v#*:}>#g") && [ ${#s} = 40 ] && e+=(-e "s#${s:0:12}#<${v#*:}>#g"); done
  sed "${e[@]}" -e 's/20[0-9][0-9]-[01][0-9]-[0-3][0-9]T[0-9:]*Z/<t>/g'; }
# gate: the newest job record's status and, at a gate, its full text
gate() { python3 - "$RT" <<'EOF' | norm
import glob, json, os, sys
r = json.load(open(max(glob.glob(sys.argv[1] + "/jobs/*.json"), key=os.path.getmtime)))
g = r.get("human_gate_pending") or {}
print(r["status"], "--", g.get("what_is_required") or r["selected_action"]["command"])
EOF
}
# rec: the binding record's state, merge and release; ev: the binding's events, in order
rec() { python3 - "$RT" <<'EOF' | norm
import glob, json, sys
r = json.load(open(glob.glob(sys.argv[1] + "/repositories/*/milestones/wi-1.json")[0]))
print(r["state"], "merge:", json.dumps(r.get("merge"), sort_keys=True), "release:", json.dumps(r.get("release"), sort_keys=True))
EOF
}
ev() { python3 - "$RT" <<'EOF'
import glob, json, sys
p = glob.glob(sys.argv[1] + "/repositories/*/milestones/wi-1/events.jsonl")[0]
print(" ".join(json.loads(l)["event"] for l in open(p) if l.strip()))
EOF
}
# njobs: the number of job records; since N: the seconds since the epoch time N
njobs() { ls "$RT"/jobs/*.json 2>/dev/null | wc -l; }
# held NAME [KIND]: a fresh target, accepted, checks green, GitHub reporting BLOCKED: READY, nothing sent
held() { drive stop "$1" "${2:-auto}" >/dev/null; use "$1"; wcx step "$R"; act checks pass; act mstate BLOCKED
  wcx step "$R"; echo "held: exit $? $(rec | cut -d' ' -f1-3)"; }
# merged NAME: a fresh release target that the Controller merged; its release pending; $M the squash commit
merged() { drive stop "$1" release >/dev/null; use "$1"; wcx step "$R"; act checks pass; wcx step "$R"
  echo "merged: exit $? $(rec | cut -d' ' -f1)"; M=$(git --git-dir="$O" rev-parse main); }
```
<!-- fr.sh end -->

<!-- drive.py begin -->
```python
"""Functional-review driver for workflow-controller-auto-merge-release-wait:
disposable policy-enabled targets driven by the local build up to the
acceptance commit, and GitHub's side of each scenario (the fake gh's state,
a second clone of the origin). Run as: python3 $S/drive.py <command> <name> ..."""
import json, os, shlex, shutil, subprocess, sys, tempfile
from pathlib import Path

S, W = Path(os.environ["S"]), os.environ["W"]
sys.path.insert(0, str(S / "src"))
from tests import fake_gh, fixtures  # noqa: E402
from tests import test_trunk_orchestration_e2e as e2e  # noqa: E402
from tests.test_pull_request_lifecycle import squash_policy  # noqa: E402

WI, BRANCH, TITLE = e2e.WI, e2e.BRANCH, "feat: the auto-merge lifecycle"
RUNS = f"https://github.com/{fixtures.FAKE_GH_REPOSITORY}/actions/runs"


def policy_for(kind: str) -> dict:
    """``none``: squash, no auto_merge (1.5.0); ``auto``: auto_merge, releases
    off; ``release``: auto_merge, releases on (Conventional Commits, tags
    ``v{version}``, publishing workflow main.yml)."""
    data = squash_policy()
    if kind in ("auto", "release"):
        data["milestone_branches"]["pull_request"]["auto_merge"] = True
    data["release"]["enabled"] = kind == "release"
    return data


class Case(e2e.SquashLifecycleTest):
    TITLE = TITLE
    kind = "auto"

    def runTest(self) -> None:
        pass

    def build_target(self, tmp: Path) -> tuple[Path, Path]:
        origin, clone = e2e._E2ECase.build_target(self, tmp)
        (clone / e2e.POLICY).write_text(json.dumps(policy_for(self.kind), indent=2) + "\n")
        fixtures.commit_all(clone, f"Policy: {self.kind}")
        fixtures.run(["git", "push", "-q", "origin", "main"], cwd=clone)
        return origin, clone

    def invoke(self, argv):
        env = {k: v for k, v in {**os.environ, **self.env()}.items()
               if k != "PYTHONPATH" and not k.startswith("GIT_")}
        argv = ["--settings", str(self.tmp_root / "settings.json"), *argv]
        r = subprocess.run([W, *argv], env=env, cwd="/tmp", capture_output=True, text=True)
        print(f"-- driver: workflow-controller {argv[argv.index('60') + 1]} -> exit {r.returncode}")
        return r.returncode, r.stdout, r.stderr


def stop(name: str, kind: str) -> None:
    d = S / "t" / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    tempfile.tempdir = str(d)
    Case.kind = kind
    Case.setUpClass()
    Case._class_tmp._finalizer.detach()
    c = Case()
    c.setUp()
    c._tmp._finalizer.detach()
    if kind == "release":  # 1.0.0 is tagged and published at the trunk tip
        fixtures.run(["git", "--git-dir", str(c.origin), "tag", "v1.0.0", c.trunk_tip])
        data = c.gh_state()
        data["releases"].append({"tagName": "v1.0.0", "isDraft": False, "assets": [], "title": "v1.0.0",
                                 "notes": "pkg v1.0.0", "url": f"{data['url']}/releases/tag/v1.0.0"})
        fake_gh.write_state(Path(c.gh_env["FAKE_GH_STATE"]), data)
    c.drive_to_pr()
    c.drive_implementation()
    a = c.accept()
    fixtures.write_worker_script(c.lc.script_path, c.lc.script)
    env = {**{k: c.env()[k] for k in ("FAKE_GH_STATE", "FAKE_GH_ORIGIN", "FAKE_GH_LOG", "FAKE_CLAUDE_SCRIPT",
                                      "FAKE_CLAUDE_INVOCATIONS_FILE", "FAKE_CLAUDE_DIAG_LOG")},
           "GHBIN": str(Path(c.gh_env["FAKE_GH_STATE"]).parent / "bin"), "R": str(c.root), "O": str(c.origin),
           "RT": str(c.lc.runtime), "SM": str(c.stub_manager), "HUMAN": str(c.tmp_root / "human"),
           "T": str(c.tmp_root), "A": a, "B0": c.trunk_tip, "FAKE_GH_FAIL": "{}"}
    (d / "env").write_text("".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items()))
    (Path(c.tmp_root) / "entry.json").write_text(json.dumps(c.lc.entry))
    print(f"-- driver: {name} ({kind}) accepted at {a[:12]}; PR #1 is a draft with no checks; now run: use {name}")


def git(*args: str, cwd=None) -> str:
    return fixtures.run(["git", *args], cwd=cwd).stdout.strip()


def act(name: str, what: str, *args: str) -> None:
    env = {}
    for line in (S / "t" / name / "env").read_text().splitlines():
        k, v = line[len("export "):].split("=", 1)
        env[k] = shlex.split(v)[0]
    state, origin = Path(env["FAKE_GH_STATE"]), env["O"]
    data = fake_gh.read_state(state)
    pr = data["prs"][-1]  # the newest pull request

    def human() -> Path:
        h = Path(env["HUMAN"])
        if not h.exists():
            fixtures.run(["git", "clone", "-q", origin, str(h)])
            git("config", "user.name", "Human", cwd=h)
            git("config", "user.email", "human@example.invalid", cwd=h)
        git("fetch", "-q", "origin", cwd=h)
        git("switch", "-q", "main", cwd=h)
        git("reset", "-q", "--hard", "origin/main", cwd=h)
        return h

    def commit(rev: str) -> str:
        return git("--git-dir", origin, "rev-parse", {"m": "refs/heads/main", "main": "refs/heads/main"}.get(rev, rev))

    if what == "checks":  # checks pass|fail|pending|cancel|none
        pr["checks"] = None if args[0] == "none" else [
            {"name": "validate", "state": {"pass": "SUCCESS", "fail": "FAILURE", "pending": "PENDING",
                                           "cancel": "CANCELLED"}[args[0]], "bucket": args[0]}]
    elif what == "mstate":  # mstate CLEAN|BLOCKED|...|default
        if args[0] == "default":
            pr.pop("mergeStateStatus", None)
        else:
            pr["mergeStateStatus"] = args[0]
    elif what == "draft":
        pr["isDraft"] = args[0] == "true"
    elif what == "lag":  # the next N reads of the PR after GitHub merges it still show it open
        pr["merge_read_lag"] = int(args[0])
    elif what == "lost":  # the merge happens, the reply is lost (exit 1, a network error)
        pr["merge_reply_lost"] = True
    elif what == "head-lag":  # the next N reads show the PR's head at the branch point
        view = fake_gh._pr_view(pr)
        view["headRefOid"] = env["B0"]
        pr.update(lagged_reads=int(args[0]), lagged_view=view)
    elif what == "push-github":  # someone pushes a commit to the branch on GitHub only
        h = human()
        git("switch", "-q", "-C", "update", f"origin/{BRANCH}", cwd=h)
        (h / "update.txt").write_text("update branch\n")
        sha = fixtures.commit_all(h, "Update branch")
        git("push", "-q", "origin", f"update:{BRANCH}", cwd=h)
        print(f"-- driver: {BRANCH} on the origin moved to {sha[:12]}")
    elif what == "trunk":  # trunk SUBJECT [FILE]: someone lands a commit on main
        h = human()
        (h / (args[1] if len(args) > 1 else "later.txt")).write_text(args[0] + "\n")
        sha = fixtures.commit_all(h, args[0])
        git("push", "-q", "origin", "main", cwd=h)
        print(f"-- driver: main is now {sha[:12]} ({args[0]})")
    elif what == "close":  # a person closes PR #1 without merging it
        pr.update(state="CLOSED", headRefOid=commit(f"refs/heads/{BRANCH}"))
        pr.pop("mergeStateStatus", None)
    elif what == "hand-merge":  # a person presses "Create a merge commit" on GitHub
        head = commit(f"refs/heads/{BRANCH}")
        h = human()
        git("merge", "-q", "--no-ff", "-m", f"Merge pull request #{pr['number']}", f"origin/{BRANCH}", cwd=h)
        git("push", "-q", "origin", "main", cwd=h)
        pr.update(state="MERGED", isDraft=False, headRefOid=head, mergedAt="2026-10-02T12:00:00Z",
                  mergeCommit={"oid": git("rev-parse", "HEAD", cwd=h)})
        pr.pop("mergeStateStatus", None)
        print(f"-- driver: PR #{pr['number']} merged by hand with a merge commit")
    elif what == "hand-squash":  # a person presses "Squash and merge" on GitHub (title (#1), then the body)
        head = commit(f"refs/heads/{BRANCH}")
        h = human()
        git("merge", "-q", "--squash", f"origin/{BRANCH}", cwd=h)
        git("commit", "-q", "--cleanup=verbatim", "-m", f"{pr['title']} (#{pr['number']})\n\n{pr['body']}", cwd=h)
        git("push", "-q", "origin", "main", cwd=h)
        pr.update(state="MERGED", isDraft=False, headRefOid=head, mergedAt="2026-10-02T12:00:00Z",
                  mergeCommit={"oid": git("rev-parse", "HEAD", cwd=h)})
        pr.pop("mergeStateStatus", None)
        print(f"-- driver: PR #{pr['number']} squash-merged by hand")
    elif what == "run":  # run COMMIT STATUS [CONCLUSION] [WORKFLOW FILE]
        sha = commit(args[0])
        run_id = 100 + len(data["runs"])
        workflow, file = (args[3], args[4]) if len(args) > 4 else ("Main", "main.yml")
        data["runs"].append({"databaseId": run_id, "workflowName": workflow, "workflowFile": file, "headSha": sha,
                             "headBranch": "main", "event": "push", "status": args[1],
                             "conclusion": args[2] if len(args) > 2 else "", "attempt": 1,
                             "url": f"{RUNS}/{run_id}"})
        print(f"-- driver: run {run_id} of {file} for {sha[:12]}: {args[1]} {args[2] if len(args) > 2 else ''}".rstrip())
    elif what == "finish":  # finish RUN_ID CONCLUSION
        for run in data["runs"]:
            if run["databaseId"] == int(args[0]):
                run.update(status="completed", conclusion=args[1])
    elif what == "publish":  # publish TAG COMMIT: the publishing run tags COMMIT and creates the release
        sha = commit(args[1])
        git("--git-dir", origin, "tag", args[0], sha)
        data["releases"].append({"tagName": args[0], "isDraft": False, "assets": [], "title": args[0],
                                 "notes": f"pkg {args[0]}", "url": f"{data['url']}/releases/tag/{args[0]}"})
        print(f"-- driver: {args[0]} tagged at {sha[:12]} and published")
    elif what == "next-plan":  # script the next milestone's /milestone-plan <main tip>
        base = commit("main")
        entry = json.loads((Path(env["T"]) / "entry.json").read_text())
        nxt = dict(entry, work_item_id=e2e.WI_NEXT, phase=e2e.AWAITING_LOCAL_PLAN, plan_approval=None,
                   base_commit="{HEAD}", checkpoints={}, registry_path=fixtures.registry_rel_path(e2e.WI_NEXT),
                   implementation_revision=None, reviewed_implementation_head=None,
                   implementation_review_stages=None, plan_review_stages=None, last_completed_checkpoint_id=None)
        stub = Case.__new__(Case)
        script_path = Path(env["FAKE_CLAUDE_SCRIPT"])
        script = json.loads(script_path.read_text())
        script.setdefault(f"/milestone-plan {base}", []).append(
            stub.plan_actions(e2e.WI_NEXT, {WI: entry, e2e.WI_NEXT: nxt}))
        fixtures.write_worker_script(script_path, script)
        print(f"-- driver: /milestone-plan {base[:12]} is scripted")
    elif what == "show":
        merges = [c for c in fake_gh.invocations(Path(env["FAKE_GH_LOG"])) if c[:2] == ["pr", "merge"]]
        print(f"PR #{pr['number']} {pr['state']} draft={pr['isDraft']} head={fake_gh._pr_view(pr)['headRefOid'][:12]} "
              f"mergeStateStatus={fake_gh._merge_state(pr)}; pr merge calls: {len(merges)}; "
              f"origin main {commit('main')[:12]} {git('--git-dir', origin, 'log', '-1', '--format=%s', 'main')!r}")
        return
    else:
        raise SystemExit(f"unknown act {what}")
    fake_gh.write_state(state, data)


if __name__ == "__main__":
    command, rest = sys.argv[1], sys.argv[2:]
    {"stop": lambda: stop(*rest), "act": lambda: act(*rest)}[command]()
```
<!-- drive.py end -->

No other test data is needed: every flow builds its own targets with `drive stop`, and flow N
builds its own release sandbox.

### Flows

**A. The local build, and this repository unchanged (I9).**

```bash
. /tmp/c4-fr/fr.sh; cd "$R0"
"$W" --version
for c in inspect explain; do
  workflow-controller --runtime-dir "$S/rt-old" "$c" "$R0" > "$S/$c-old.txt" 2>&1; e1=$?
  "$W" --runtime-dir "$S/rt-new" "$c" "$R0" > "$S/$c-new.txt" 2>&1; e2=$?
  echo "$c: 1.5.0 exit $e1, local exit $e2"; cmp "$S/$c-old.txt" "$S/$c-new.txt" && echo "$c: byte-identical"
done
git diff --stat "$BASE" "$H" -- .workflow-controller/ pyproject.toml setup.py .github/; echo "policy/packaging/CI diff above (expect none)"
(cd "$S/src" && python3 tools/ci_workflows.py --check; echo "ci_workflows --check exit $?")
```
Expected:
- `workflow-controller 1.5.0` and `runtime: package (local build from 3d875a7106b5)`;
- `inspect: 1.5.0 exit 0, local exit 0`, `inspect: byte-identical`, and the same two lines for
  `explain`: this repository's policy has no `auto_merge` key, so both builds read it alike
  (each with an empty scratch runtime root);
- no `git diff` output before `policy/packaging/CI diff above (expect none)`: the policy,
  `pyproject.toml`, `setup.py` and `.github/` are byte-unchanged from the base; then
  `ci_workflows --check exit 0`.

**B. The settings rows (A.2): `merge.auto`, `merge.wait_seconds`, `merge.poll_seconds`.**

```bash
. /tmp/c4-fr/fr.sh; rm -rf "$S/set"; mkdir -p "$S/set"; cd "$S/set"
"$W" settings show | tail -4
"$W" --json settings show | python3 -c "import json,sys; d=json.load(sys.stdin); print({k: (d['values'][k], d['sources'][k]) for k in sorted(d['values']) if k.startswith('merge')})"
echo "--- a generation-1 file written by 1.5.0, filled by the local build"
workflow-controller --settings g1.json settings clean
python3 -c "import json; d=json.load(open('g1.json')); print(d['_table_generation'], len(d['_defaults_written']), 'merge' in d)"
cp g1.json g1.before; "$W" --settings g1.json settings clean; echo "exit $?"
python3 - <<'EOF'
import json
a, b = json.load(open("g1.before")), json.load(open("g1.json"))
print(b["_table_generation"], len(b["_defaults_written"]), b["merge"])
print({k: v for k, v in b["_defaults_written"].items() if k.startswith("merge")})
print("everything else unchanged:", all(b["_defaults_written"][k] == v for k, v in a["_defaults_written"].items())
      and all(b[k] == a[k] for k in a if k not in ("_defaults_written", "_table_generation")))
EOF
echo "--- 1.5.0 sharing the generation-2 file"
workflow-controller --settings g1.json settings show >/dev/null; echo "show exit $?"
workflow-controller --settings g1.json settings clean; echo "clean exit $?"
echo "--- refused, the file unchanged"
for c in 'auto:1' 'auto:"yes"' 'auto:null' 'wait_seconds:-1' 'wait_seconds:86401' 'wait_seconds:true' 'poll_seconds:9' 'poll_seconds:601' 'poll_seconds:30.0'; do
  k=${c%%:*}; v=${c#*:}; printf '{"merge": {"%s": %s}}\n' "$k" "$v" > bad.json; h=$(sha256sum < bad.json)
  "$W" --settings bad.json settings show >/dev/null 2>bad.err; e=$?
  echo "$k=$v: exit $e, unchanged: $([ "$h" = "$(sha256sum < bad.json)" ] && echo yes || echo NO): $(sed 's#^error: the settings file /tmp/c4-fr/set/bad.json cannot be used: ##' bad.err)"
done
echo "--- the bounds admitted"
for c in 'auto:false' 'wait_seconds:0' 'wait_seconds:86400' 'poll_seconds:10' 'poll_seconds:600'; do
  k=${c%%:*}; v=${c#*:}; printf '{"merge": {"%s": %s}}\n' "$k" "$v" > ok.json; "$W" --settings ok.json settings show | grep "^merge.$k"; done
```
Expected, in order:
- `merge.auto = true (default)`, `merge.wait_seconds = 3600 (default)`,
  `merge.poll_seconds = 30 (default)`, `routing = {"default": {}, "roles": {}} (default)`, and
  `{'merge.auto': (True, 'default'), 'merge.poll_seconds': (30, 'default'), 'merge.wait_seconds': (3600, 'default')}`
  (a JSON boolean for `merge.auto`);
- the generation-1 file: `nothing to remove from /tmp/c4-fr/set/g1.json`, `1 10 False` (1.5.0
  wrote ten rows and no `merge` section); the local build's `clean` fills it: the same `nothing to
  remove ...` line, `exit 0`, then `2 13 {'auto': True, 'poll_seconds': 30, 'wait_seconds': 3600}`,
  `{'merge.auto': {'generation': 2, 'value': True}, 'merge.poll_seconds': {'generation': 2, 'value': 30}, 'merge.wait_seconds': {'generation': 2, 'value': 3600}}`
  and `everything else unchanged: True` (the additive fill moves nothing);
- 1.5.0 on that file: `workflow-controller: warning: the settings file /tmp/c4-fr/set/g1.json has unknown key(s) merge; they are ignored (the file was last filled by a newer Controller release (table generation 2, this release's is 1), whose keys these probably are)`,
  `show exit 0`; its `clean` refuses:
  `` error: the settings file /tmp/c4-fr/set/g1.json cannot be used: it was last filled by a newer Controller release (table generation 2, this release's is 1), so this release cannot tell that release's keys from retired ones; run `workflow-controller settings clean` from the newest installed release ``,
  `clean exit 20` (the migration note: a 1.5.0 sharing the file never removes the rows);
- every refusal `exit 20, unchanged: yes`, with the tails
  `merge.auto must be true or false, got 1` (and `got "yes"`, `got null`),
  `merge.wait_seconds must be an integer from 0 to 86400, got -1` (and `got 86401`, `got true`),
  `merge.poll_seconds must be an integer from 10 to 600, got 9` (and `got 601`, `got 30.0`);
- the bounds admitted: `merge.auto = false (file)`, `merge.wait_seconds = 0 (file)`,
  `merge.wait_seconds = 86400 (file)`, `merge.poll_seconds = 10 (file)`,
  `merge.poll_seconds = 600 (file)`.

**C. The policy keys (A.1).** `inspect` on targets whose committed policy is the suite's squash
policy with one edit each, with the local build and with the installed 1.5.0:

```bash
. /tmp/c4-fr/fr.sh; P=$S/pol; rm -rf "$P"; mkdir -p "$P"
mkpol() {  # mkpol NAME PYTHON: a target whose policy is the suite's squash policy, edited by PYTHON (pr: pull_request)
  git init -q -b main "$P/$1"; git -C "$P/$1" config user.email fr@example.invalid; git -C "$P/$1" config user.name fr
  mkdir -p "$P/$1/.workflow-manager" "$P/$1/docs/ai-workflow" "$P/$1/.workflow-controller"
  printf '{\n  "schema_version": 1,\n  "workflow_version": "2.6.0",\n  "profile": "full"\n}\n' > "$P/$1/.workflow-manager/installation.json"
  printf '{\n  "schema_version": 1,\n  "active_work_item_id": null,\n  "work_items": {}\n}\n' > "$P/$1/docs/ai-workflow/WORKFLOW_STATE.json"
  python3 - "$P/$1/.workflow-controller/policy.json" "$2" <<'EOF'
import json, sys
sys.path.insert(0, "/tmp/c4-fr/src")
from tests.test_pull_request_lifecycle import squash_policy
pol = squash_policy(); pr = pol["milestone_branches"]["pull_request"]
exec(sys.argv[2])
json.dump(pol, open(sys.argv[1], "w"), indent=2)
EOF
  git -C "$P/$1" add -A && git -C "$P/$1" commit -qm initial
}
mkpol ok 'pr["auto_merge"] = True'
mkpol okwf 'pr.update(auto_merge=True, release_workflow="release.yml")'
mkpol off 'pr["auto_merge"] = False'
mkpol merge 'pr["auto_merge"] = True; del pr["merge_method"]'
mkpol nogreen 'pr.update(auto_merge=True, ready_requires_green_checks=False)'
mkpol str 'pr["auto_merge"] = "true"'
mkpol slash 'pr.update(auto_merge=True, release_workflow=".github/workflows/main.yml")'
mkpol empty 'pr.update(auto_merge=True, release_workflow="")'
mkpol int 'pr.update(auto_merge=True, release_workflow=1)'
for t in ok okwf off merge nogreen str slash empty int; do
  "$W" --runtime-dir "$P/rt" --workflow-manager "$S/wm" inspect "$P/$t" > "$P/$t.out" 2>&1
  echo "$t: local exit $?: $(grep -E '^error|^repository policy' "$P/$t.out" | sed 's/sha256 [0-9a-f]*/sha256 <...>/')"
done
for t in ok okwf off; do
  workflow-controller --runtime-dir "$P/rt-old" --workflow-manager "$S/wm" inspect "$P/$t" > "$P/$t.old" 2>&1
  echo "$t: 1.5.0 exit $?: $(grep -E '^error' "$P/$t.old")"
done
```
Expected:
- `ok`, `okwf` (`release_workflow: "release.yml"`) and `off` (`auto_merge: false`): `local exit
  0`, with `repository policy: .workflow-controller/policy.json (HEAD, sha256 <...>) milestone_branches=True release=False trunk=origin/main forge=example-owner/example-repo`;
- each refusal `local exit 20`, with `error: .workflow-controller/policy.json: milestone_branches.pull_request.` followed by:

  | Target | Message tail |
  |---|---|
  | `merge` (no `merge_method`) | `merge_method: must be 'squash' when auto_merge is true: the squash verification and the release wait are squash-mode only` |
  | `nogreen` | `ready_requires_green_checks: must be true when auto_merge is true: the Controller merges only after it has seen green checks` |
  | `str` | `auto_merge: must be true or false, not 'true'` |
  | `slash` | `release_workflow: '.github/workflows/main.yml' must be a workflow file name under .github/workflows/, without a '/'` |
  | `empty` | `release_workflow: must be a non-empty string, not ''` |
  | `int` | `release_workflow: must be a non-empty string, not 1` |

- 1.5.0 refuses the new keys (the fail-closed migration order): `ok: 1.5.0 exit 20: error: .workflow-controller/policy.json: milestone_branches.pull_request: unknown key(s) ['auto_merge']; the known keys are ['draft', 'merge_method', 'ready_requires_green_checks', 'release_notes']`,
  the same for `off`, and `unknown key(s) ['auto_merge', 'release_workflow']` for `okwf`.

**D. No key: 1.5.0's behaviour, byte for byte (I1).** The same no-key lifecycle twice, once with
the local build and once with the installed 1.5.0 driving every step (`W` overridden), each
transcript normalised: readiness, `explain` at `READY`, a hand "Squash and merge", and the step
that closes out.

```bash
. /tmp/c4-fr/fr.sh
one() {  # one NAME: a no-key target to READY, a hand squash, and the close-out step
  drive stop "$1" none >/dev/null; use "$1"; wcx step "$R"; act checks pass; wcx step "$R"
  { echo "exit $?"; gate; rec; act show
    python3 -c "import glob,json,sys; print(sorted(json.load(open(glob.glob(sys.argv[1]+'/repositories/*/milestones/wi-1.json')[0]))))" "$RT"
    wcx explain "$R" | sed -n 2p | norm
    act hand-squash; act next-plan >/dev/null; wcx step "$R"; echo "exit $?"; gate | sed 's/[0-9a-f]\{40\}/<sha>/'; rec; ev
    git -C "$R" branch --show-current; } > "$S/$1.txt" 2>&1
}
one d-new
W=$(command -v workflow-controller) one d-old
diff "$S/d-old.txt" "$S/d-new.txt" && echo "the 1.5.0 and local transcripts are identical"; cat "$S/d-new.txt"
use d-old; python3 -c "import glob,json,os,sys; r=json.load(open(max(glob.glob(sys.argv[1]+'/jobs/*.json'), key=os.path.getmtime))); print(r['controller_runtime']['release_tag'])" "$RT"
use d-new; python3 -c "import glob,json,os,sys; r=json.load(open(max(glob.glob(sys.argv[1]+'/jobs/*.json'), key=os.path.getmtime))); print(r['controller_runtime']['build_origin'], r['controller_runtime']['source_commit'][:12])" "$RT"
```
Expected: `the 1.5.0 and local transcripts are identical`, then the transcript:
- `exit 10` and
  `GATE_BLOCKED -- merge the pull request on GitHub with "Squash and merge" (merge_pull_request): pull request #1 (https://github.com/example-owner/example-repo/pull/1) is ready; a human merges it on GitHub with "Squash and merge". The squash commit's subject is the pull request's title and its body the pull request's body. The Controller never merges`;
- `READY merge: null release: null`;
  `PR #1 OPEN draft=False head=<A> mergeStateStatus=CLEAN; pr merge calls: 0; origin main <B0> 'Policy: none'`
  (GitHub reports it mergeable, and nothing is sent);
- the record's keys `['accepted_head', 'binding_generation', 'branch', 'branch_point', 'last_observation', 'merged_head', 'policy', 'pr', 'repository', 'schema_version', 'state', 'superseded_prs', 'trunk', 'updated_at', 'work_item_id', 'workflow_base_commit']`
  (no `merge`, no `release`);
- `repository preflight: gate (merge_pull_request) -- the pull request is ready; a human merges it (as of <t>)`;
- `-- driver: PR #1 squash-merged by hand`, `exit 0`, `FINISHED -- /milestone-plan <sha>`: the
  close-out step goes on to plan the next milestone, as in 1.5.0 (no stop);
- `CLOSED merge: null release: null`; the events
  `bind_planned bound push_intent pushed pr_planned pr_created push_intent pushed push_intent pushed push_intent pushed push_intent pushed pr_edited ready merged_squashed closed`;
  `main`.

The last two lines prove which build ran: `v1.5.0` (the installed release), then
`local 3d875a7106b5`.

**E. Opt-in: the head-bound merge, close-out, the stop, and the next step from the trunk (C.3,
D.4).** An `auto` target (releases off).

```bash
. /tmp/c4-fr/fr.sh; drive stop e1 auto >/dev/null; use e1
wcx step "$R"; echo "exit $?"; gate; rec
act checks pass; n=$(njobs)
wcx step "$R"; echo "exit $?, new job records: $(( $(njobs) - n ))"; M=$(git --git-dir="$O" rev-parse main); rec; ev; act show
python3 -c "import json,sys; [print(json.dumps(c)) for c in map(json.loads, open(sys.argv[1])) if c[:2] == ['pr', 'merge']]" "$FAKE_GH_LOG" | norm
grep -c -e '--auto' -e '--disable-auto' "$FAKE_GH_LOG"
git -C "$R" branch --show-current; git -C "$R" rev-parse HEAD | norm; git -C "$R" log -1 --format=%s; git -C "$R" log -1 --format=%b | head -3 | norm
wcx status | grep '^milestone:' | norm
wcx explain "$R" | sed -n 2,6p | norm
act next-plan; wcx step "$R"; echo "the next step: exit $?"; gate
```
Expected:
- `exit 10`, `GATE_BLOCKED -- wait for the pull request's checks to finish (checks_pending): pull request #1's title or body was just updated; its checks re-run`
  and `PR_OPEN merge: null release: null` (readiness pushed the acceptance commit and synced the
  body);
- after green checks, one step does everything: `exit 0, new job records: 0` (the stop is a
  no-action outcome: no worker, no job record), then
  `CLOSED merge: {"attempts": 1, "head": "<A>", "last_attempt_at": "<t>", "state": "accepted"} release: {"state": "NONE"}`;
- the events end `... pr_edited ready merge_sent merge_accepted merged_squashed release_settled closed`;
- `PR #1 MERGED draft=False head=<A> mergeStateStatus=UNKNOWN; pr merge calls: 1; origin main <m> 'feat: the auto-merge lifecycle (#1)'`;
- the one `gh pr merge` argv, exactly:
  `` ["pr", "merge", "1", "--squash", "--match-head-commit", "<A>", "--subject", "feat: the auto-merge lifecycle (#1)", "--body", "Milestone `wi-1`, planned in `docs/plans/wi-1.md`, driven by workflow-controller.\nAccepted at <A> on `milestone/wi-1`; squash-merged into the trunk as one commit.\n\n<!-- workflow-controller: work_item=wi-1 -->\n", "--repo", "example-owner/example-repo"] ``
  and `0` (no `--auto` or `--disable-auto` anywhere in the gh log);
- `main`, `<m>` (close-out switched to the trunk and fast-forwarded), the squash subject
  `feat: the auto-merge lifecycle (#1)` and its body's first lines (`Milestone ...`,
  `Accepted at <A> on ...`);
- `milestone: wi-1 CLOSED on milestone/wi-1, pull request #1 (worktree <R>), merge: accepted at <A> (attempt 1), release: NONE (the policy at the squash commit does not enable releases)`;
- `explain`: `repository preflight: proceed -- main equals origin/main (as of the last fetch of origin/main)`,
  `phase: NO_PHASE`, a reason ending `based on the trunk tip <m>`, and
  `next automatic action: /milestone-plan <m>`;
- `-- driver: /milestone-plan <m> is scripted`, `the next step: exit 0`,
  `FINISHED -- /milestone-plan <m>`: the next `step` plans from the trunk with the explicit base.

**F. `merge.auto: false` turns the merge off (A.2, C.6).** An `auto` target whose settings file
turns the merge off, then on again.

```bash
. /tmp/c4-fr/fr.sh; drive stop f1 auto >/dev/null; use f1; printf '{"merge": {"auto": false}}\n' > "$T/settings.json"
wcx step "$R"; act checks pass; wcx step "$R"; echo "exit $?"; gate; rec; act show
wcx settings show | grep '^merge.auto'; wcx explain "$R" | sed -n 2p | norm
sed -i 's/"auto": false/"auto": true/' "$T/settings.json"; wcx explain "$R" | sed -n 2p | norm
wcx step "$R"; echo "merge.auto back on: exit $?"; rec | cut -d' ' -f1; act show
```
Expected: `exit 10` and the exact `merge_pull_request` gate text of flow D (a person merges);
`READY merge: null release: null`;
`PR #1 OPEN draft=False head=<A> mergeStateStatus=CLEAN; pr merge calls: 0; origin main <B0> 'Policy: auto'`;
`merge.auto = false (file)`; `explain` predicts
`repository preflight: gate (merge_pull_request) -- the pull request is ready; a human merges it (as of <t>)`,
and with the setting back on
`repository preflight: merge -- the Controller merges the pull request at <A> if GitHub reports it mergeable with green checks (as of <t>)`.
The next step merges: `merge.auto back on: exit 0`, `CLOSED`,
`PR #1 MERGED ...; pr merge calls: 1; origin main <...> 'feat: the auto-merge lifecycle (#1)'`.

**G. Nothing is sent while GitHub or a person holds the merge (C.2, C.5).** `held` drives a fresh
`auto` target to `READY` with green checks while GitHub reports `BLOCKED`; then each merge state,
each check outcome and a draft, in turn, on the same target, and finally ready for review and
`CLEAN`.

```bash
. /tmp/c4-fr/fr.sh; held g1; gate
wcx explain "$R" | sed -n 2p | norm
for s in UNKNOWN UNSTABLE DRAFT DIRTY BEHIND; do act mstate $s; wcx step "$R"; echo "$s: exit $?"; gate; done
act mstate BLOCKED
for c in fail cancel pending none; do act checks $c; wcx step "$R"; echo "checks $c: exit $?"; gate; done
act checks pass; act draft true; act mstate DRAFT; wcx step "$R"; echo "draft: exit $?"; gate
act checks pending; wcx step "$R"; echo "draft, checks pending: exit $?"; gate | cut -c1-80
act show; rec
act checks pass; act draft false; act mstate default; wcx step "$R"; echo "ready for review, CLEAN: exit $?"; rec | cut -d' ' -f1; act show
```
Expected: `held: exit 10 READY merge: null`, then each gate below with `exit 10`, and every gate
text starts with its title. The `merge_pending` title is
`the Controller has not merged the ready pull request yet; wait, or merge it on GitHub with "Squash and merge" (merge_pending):`:

| State | Gate text after the title |
|---|---|
| `BLOCKED` (green checks) | `pull request #1 is not mergeable yet: GitHub reports it BLOCKED with every check green: a branch-protection requirement other than the checks is outstanding (a required approving review, conversation resolution, signed commits or a deployment). The Controller sent no merge; the next step merges it once GitHub reports it mergeable` |
| `UNKNOWN` | `pull request #1 is not mergeable yet: GitHub has not computed its mergeability yet (UNKNOWN). The Controller sent no merge; the next step merges it once GitHub reports it mergeable` |
| `UNSTABLE` | `pull request #1 is not mergeable yet: GitHub reports it UNSTABLE: a status that is not required is not green, and the Controller merges only a pull request with nothing outstanding. The Controller sent no merge; the next step merges it once GitHub reports it mergeable` |
| `DRAFT` (stale, no longer a draft) | `pull request #1 is not mergeable yet: GitHub has not computed its mergeability yet (DRAFT). ...` (as `UNKNOWN`) |
| `DIRTY` | `pull request #1 cannot be merged: GitHub reports a conflict with main (DIRTY). The Controller does not integrate and sent no merge` |

`explain` at `BLOCKED`:
`repository preflight: merge -- the Controller merges the pull request at <A> if GitHub reports it mergeable with green checks (as of <t>)`.

The others:
- `BEHIND`: `GATE_BLOCKED -- the trunk moved past the milestone's base; mark the pull request ready and merge it on GitHub with "Squash and merge" (integration_required): GitHub reports pull request #1 BEHIND origin/main: branch protection requires the branch to be up to date before a merge, and the Controller does not integrate (Workflow 2.5.1 and 2.6.0 have no transition that moves a work item's base), so it sent no merge. The manual procedure: bring the pull request up to date on GitHub and merge it with "Squash and merge"`;
- `checks fail`: `GATE_BLOCKED -- fix the pull request's failing checks (checks_failing): pull request #1 has failing checks: validate. The Controller merges the pull request once a re-run turns the checks green (the next step)`;
- `checks cancel`: `GATE_BLOCKED -- re-run the pull request's cancelled checks on GitHub (checks_cancelled): pull request #1 has cancelled checks: validate. The Controller merges the pull request once a re-run turns the checks green (the next step)`;
- `checks pending`: `GATE_BLOCKED -- wait for the pull request's checks to finish (checks_pending): pull request #1 has pending checks: validate`;
  `checks none`: the same gate, `pull request #1 has no checks reported yet`;
- `draft`: `GATE_BLOCKED -- the ready pull request was converted back to a draft; mark it ready for review, or merge it on GitHub with "Squash and merge" (merge_held): pull request #1 was converted back to a draft after it was marked ready, which holds the merge; the Controller does not merge it. Exits: mark it ready for review on GitHub, after which the next step merges it; or merge pull request #1 on GitHub with "Squash and merge"`;
  a draft with pending checks is still `merge_held` (the draft row comes first);
- after all of them: `PR #1 OPEN draft=True head=<A> mergeStateStatus=DRAFT; pr merge calls: 0; origin main <B0> 'Policy: auto'`
  and `READY merge: null release: null`: nothing was sent and the record is untouched;
- ready for review with green checks and `CLEAN`: `ready for review, CLEAN: exit 0`, `CLOSED`,
  `PR #1 MERGED ...; pr merge calls: 1; ...'feat: the auto-merge lifecycle (#1)'`.

**H. A moved head: the Controller merges only the acceptance commit (C.4).**

```bash
. /tmp/c4-fr/fr.sh
echo "== H1 a local commit, not pushed"; held h1
echo later > "$R/later.txt"; git -C "$R" add later.txt; git -C "$R" commit -qm "A later commit"; L=$(git -C "$R" rev-parse HEAD)
act mstate CLEAN; wcx step "$R"; echo "exit $?"; gate; act show; git --git-dir="$O" rev-parse refs/heads/milestone/wi-1 | norm
echo "== H2 pushed as well"; git -C "$R" push -q origin milestone/wi-1; wcx step "$R"; echo "exit $?"; gate | cut -c1-120; act show
echo "== H3 a push on GitHub only"; held h3; act checks pending; act push-github; act checks pass; act mstate CLEAN
wcx step "$R" 2>&1 | sed 's/[0-9a-f]\{40\}/<sha>/g'; echo "exit ${PIPESTATUS[0]}"; act show; rec
echo "== H4 the pull request's head lagging"; held h4; act mstate CLEAN; act head-lag 1
wcx step "$R"; echo "exit $?"; gate; act show
wcx step "$R"; echo "the next step: exit $?"; rec | cut -d' ' -f1
echo "== H5 READY, from the trunk"; held h5; act mstate CLEAN; git -C "$R" switch -q main
wcx step "$R" 2>&1 | norm; echo "exit ${PIPESTATUS[0]}"; act show
```
Expected:
- H1, a local commit not pushed: `exit 10`,
  `GATE_BLOCKED -- commits follow the acceptance commit; merge anyway on GitHub with "Squash and merge", or leave the pull request unready (post_acceptance_commits): milestone/wi-1 has commits after the acceptance commit <A>: <L>. The pull request was marked ready at <A>, and the Controller merges only <A>, so it sends no merge while the branch carries later commits. A human decides: merge anyway on GitHub with "Squash and merge", after which the merged-PR handling converges; otherwise this gate persists`;
  `pr merge calls: 0`, and the origin's branch is still `<A>` (nothing pushed past it, I7);
- H2, the same commit pushed: the same gate; the pull request's head is now `<L>`; `pr merge calls: 0`;
- H3, a push on GitHub only, then green checks and `CLEAN`:
  `` error: origin/milestone/wi-1 (<sha>) is not an ancestor of the local tip <sha>. The likely cause is GitHub's "Update branch" button, which pushes a merge commit to the milestone branch on the server Exit: bring milestone/wi-1 and origin/milestone/wi-1 back into a fast-forward relation. ``,
  `exit 20`, `pr merge calls: 0`, the pull request still `OPEN`, `READY merge: null release: null`;
- H4, GitHub's read of the pull request still shows the branch point: `exit 10`,
  `GATE_BLOCKED -- wait until the pull request shows the acceptance commit (pr_head_not_accepted): pull request #1 does not show the acceptance commit <A> (origin/milestone/wi-1 is <A>; the pull request is at <B0>); the Controller sends no merge until it does`,
  `pr merge calls: 0`; once the read catches up, `the next step: exit 0`, `CLOSED`;
- H5, `READY` with `HEAD` on `main`:
  `` error: wi-1 is bound to milestone/wi-1 (binding state READY), and its pull request #1 is open, so the trunk cannot start a milestone. Exit: switch to it (`git switch milestone/wi-1`). ``,
  `exit 20`, `pr merge calls: 0`: the merge happens only from the branch (C.1).

**I. Refusals, the three-attempt budget, an unrelated `(#1)` trunk commit, and `--new-pr` (C.3).**
`FAKE_GH_FAIL` makes the fake `gh` fail every `pr merge` with an HTTP 500 before merging. In I1 a
commit titled `fix unrelated issue (#1)` lands on the trunk first: it carries the pull request's
suffix but not its content, so it must never be taken for the squash.

```bash
. /tmp/c4-fr/fr.sh
echo "== I1 three refusals, with an unrelated (#1) commit on the trunk"; held i1
act trunk "fix unrelated issue (#1)" unrelated.txt; act mstate CLEAN
for i in 1 2 3 4; do FAKE_GH_FAIL='{"pr merge": "server_error"}' wcx step "$R" 2>"$T/err"; echo "step $i: exit $?"; norm < "$T/err"; [ $i -le 2 ] && gate; rec; done
act show; ev | tr ' ' '\n' | grep -c merge_refused
act hand-squash; wcx step "$R"; echo "after the hand squash: exit $?"; rec | cut -d' ' -f1; git -C "$R" branch --show-current
echo "== I2 the pull request closed after the refusals, --new-pr"; held i2; act mstate CLEAN
for i in 1 2 3; do FAKE_GH_FAIL='{"pr merge": "server_error"}' wcx step "$R" 2>/dev/null; done; rec | cut -c1-60
act close; wcx step "$R"; echo "closed: exit $?"; gate | cut -c1-110
wcx --work-item wi-1 milestone-binding --new-pr "$R"; echo "exit $?"; rec
wcx step "$R"; echo "exit $?"; act checks pass; wcx step "$R"; echo "exit $?"; rec; act show
```
Expected, I1:
- `step 1: exit 10` and `step 2: exit 10`, each
  `GATE_BLOCKED -- the Controller has not merged the ready pull request yet; wait, or merge it on GitHub with "Squash and merge" (merge_pending): GitHub refused the Controller's merge of pull request #1 (attempt 1 of 3): HTTP 500: Internal Server Error (https://api.github.com/graphql). The next step decides again, and sends again only when GitHub reports it mergeable at <A> with green checks`
  (`attempt 2 of 3` the second time), and the record
  `READY merge: {"attempts": 1, "head": "<A>", "last_attempt_at": "<t>", "refusal": "HTTP 500: Internal Server Error (https://api.github.com/graphql)", "refusals": 1, "state": "sending"} release: null`
  (then `2`/`2`): never `accepted`;
- `step 3: exit 20` and `step 4: exit 20`, each
  `error: GitHub refused the Controller's merge of pull request #1 3 times (last: HTTP 500: Internal Server Error (https://api.github.com/graphql)); it sends no more; after a person merges it, the next step closes out as for any merge Exit: merge pull request #1 on GitHub with "Squash and merge".`,
  with `"attempts": 3, ... "refusals": 3, "state": "sending"` both times;
- `PR #1 OPEN ...; pr merge calls: 3; origin main <...> 'fix unrelated issue (#1)'` (no fourth
  send), and `1` (one `merge_refused` event for the one distinct message);
- the hand squash then closes out: `after the hand squash: exit 0`, `CLOSED`, `main`.

I2: `READY merge: {"attempts": 3, ...`, `closed: exit 10` with the
`pr_closed_unmerged` gate (`the milestone's pull request was closed without merge; reopen it, or run milestone-binding --n...`);
`milestone-binding --new-pr: the milestone/wi-1 binding of wi-1 is now BRANCH_BOUND`, `exit 0`,
`BRANCH_BOUND merge: null release: null` (the spent budget does not carry over); `exit 10`
(pull request #2 opens, checks pending), then `exit 0` and
`CLOSED merge: {"attempts": 1, "head": "<A>", "last_attempt_at": "<t>", "state": "accepted"} release: {"state": "NONE"}`,
`PR #2 MERGED ...; pr merge calls: 4; origin main <...> 'feat: the auto-merge lifecycle (#2)'`.

**J. A lost reply and stale reads (C.3 step 4).** J1: GitHub merges, but `gh` exits 1 with a
network error, and the next three reads of the pull request still show it open. J2: the merge
succeeds and the next two reads lag.

```bash
. /tmp/c4-fr/fr.sh
echo "== J1 GitHub merges, the reply is lost, reads lag"; held j1; act lost; act lag 3; act mstate CLEAN
for i in 1 2 3 4; do wcx step "$R"; echo "step $i: exit $?"; [ $i = 1 ] && gate; rec | sed 's/"squash_commit": "[0-9a-f]*"/"squash_commit": "<m>"/'; done
act show; ev | tr ' ' '\n' | grep -E '^merge_' | tr '\n' ' '; echo
echo "== J2 GitHub merges, reads lag"; held j2; act lag 2; act mstate CLEAN
wcx step "$R"; echo "step 1: exit $?"; rec; wcx explain "$R" | sed -n 2p | norm
wcx step "$R"; echo "step 2: exit $?"; wcx step "$R"; echo "step 3: exit $?"; rec | cut -d' ' -f1; act show
```
Expected:
- J1 `step 1: exit 10`:
  `` GATE_BLOCKED -- the Controller has not merged the ready pull request yet; wait, or merge it on GitHub with "Squash and merge" (merge_pending): GitHub accepted the merge of pull request #1 at <A>; it is not visible yet. The Controller does not send it again. If it never appears: merge pull request #1 on GitHub with "Squash and merge", or close it on GitHub and recover the binding with `workflow-controller --work-item wi-1 milestone-binding --new-pr <R>` ``,
  and `READY merge: {"attempts": 1, "head": "<A>", "last_attempt_at": "<t>", "squash_commit": "<m>", "state": "accepted"} release: null`:
  the failed send was adopted from the trunk's squash commit, not counted as a refusal; steps 2
  and 3 `exit 10` with the same record; `step 4: exit 0` with `CLOSED ... release: {"state": "NONE"}`;
  `pr merge calls: 1` (never re-sent), and the merge events `merge_sent merge_accepted` (no
  `merge_refused`);
- J2 `step 1: exit 10`, `READY merge: {"attempts": 1, "head": "<A>", "last_attempt_at": "<t>", "state": "accepted"} release: null`;
  `explain`: `repository preflight: wait_merge (merge_pending) -- GitHub accepted the merge at <A>; the step waits for it to be visible (as of <t>)`;
  `step 2: exit 10`, `step 3: exit 0`, `CLOSED`, `pr merge calls: 1`.

**K. The release wait, close out and stop (D).** `merged` drives a fresh `release` target to the
Controller's merge: `v1.0.0` is published at the base, so the `feat:` squash commit `<m>`
classifies `RELEASE_DUE` 1.1.0. The runs (`act run`) and the publication (`act publish`: the tag
and the release, as the publishing run makes them) are GitHub's side.

```bash
. /tmp/c4-fr/fr.sh
echo "== K1 pending, then released; another workflow's failure is ignored"; merged k1; gate; rec; git -C "$R" branch --show-current
wcx explain "$R" | sed -n 2p | norm; wcx inspect "$R" | grep '^milestone branch' | norm
act run m queued; wcx step "$R"; echo "exit $?"; gate
act run m completed failure "Workflow conformance" conformance.yml; wcx step "$R"; echo "exit $?"; gate | cut -c1-150
act finish 100 success; act publish v1.1.0 m; n=$(njobs)
wcx step "$R"; echo "exit $?, new job records: $(( $(njobs) - n ))"; rec; ev | tr ' ' '\n' | tail -5 | tr '\n' ' '; echo
git -C "$R" branch --show-current; git -C "$R" rev-parse HEAD | norm
grep -c -e '"release", "download"' -e '"release", "create"' "$FAKE_GH_LOG"
python3 -c "import json,sys; print(sorted({c[c.index('--workflow') + 1] for c in map(json.loads, open(sys.argv[1])) if c[:2] == ['run', 'list']}))" "$FAKE_GH_LOG"
wcx status | grep '^milestone:' | norm
wcx --json status | python3 -c "import json,sys; b=json.load(sys.stdin)['bindings'][0]; print(sorted(b), b['merge']['state'], b['release']['tag'])"
act next-plan; wcx step "$R"; echo "the next step: exit $?"; gate
echo "== K2 the publishing run failed, then a re-run publishes"; merged k2; act run m completed failure
wcx step "$R"; echo "exit $?"; gate; wcx step "$R"; echo "again: exit $?"; ev | tr ' ' '\n' | grep -c release_failed; git -C "$R" branch --show-current
act publish v1.1.0 m; wcx step "$R"; echo "after the re-run published: exit $?"; rec | cut -d' ' -f1
echo "== K3 the publishing run succeeded and published nothing"; merged k3; act run m completed success; wcx step "$R"; echo "exit $?"; gate
echo "== K4 the run of m cancelled by a later push, whose run publishes"; merged k4; act run m queued
act trunk "fix: a later change" later.txt; D=$(git --git-dir="$O" rev-parse main); act finish 100 cancelled
wcx step "$R"; echo "exit $?"; gate
act run main in_progress; wcx step "$R"; echo "exit $?"; gate | sed 's/.*the release covering <m>: //'
act publish v1.1.0 main; act finish 101 success; wcx step "$R"; echo "exit $?"; rec; git -C "$R" rev-parse HEAD | norm
echo "== K5 a failed run of m, recovered by a later run"; merged k5; act run m completed failure
act trunk "fix: a later change" later.txt; D=$(git --git-dir="$O" rev-parse main); act run main in_progress
wcx step "$R"; echo "exit $?"; gate; ev | tr ' ' '\n' | grep -c release_failed
act publish v1.1.0 main; act finish 101 success; wcx step "$R"; echo "exit $?"; rec | sed 's/ merge: .* release:/ ... release:/'
echo "== K6 from the trunk, after a manual switch"; merged k6; git -C "$R" switch -q main
wcx step "$R"; echo "exit $?"; gate | cut -c1-140; git -C "$R" rev-parse HEAD | norm
act run m in_progress; act publish v1.1.0 m; act finish 100 success; wcx step "$R"; echo "exit $?"; rec | cut -d' ' -f1
python3 -c "import glob,json,sys; print([e.get('side') for e in map(json.loads, open(glob.glob(sys.argv[1]+'/repositories/*/milestones/wi-1/events.jsonl')[0])) if e['event'] == 'closed'])" "$RT"
git -C "$R" rev-parse HEAD | norm; wcx step "$R"; echo "the next step: exit $?"; gate | cut -c1-150
echo "== K7 a person merges with a merge commit"; drive stop k7 release >/dev/null; use k7; wcx step "$R"; act checks pass; act mstate BLOCKED; wcx step "$R"
act hand-merge; act next-plan >/dev/null; wcx step "$R"; echo "exit $?"; gate | sed 's/[0-9a-f]\{40\}/<sha>/'; rec; ev | tr ' ' '\n' | tail -4 | tr '\n' ' '; echo
grep -c '"run", "list"' "$FAKE_GH_LOG"
```
Expected, K1:
- `merged: exit 10 MERGED_SQUASHED`;
  `` GATE_BLOCKED -- the merged milestone's release is not published yet; wait for the publishing workflow, or publish it by hand (release_pending): pull request #1 was squash-merged as <m>; its release is not settled yet: v1.1.0 is not published yet (RELEASE_DUE), and the main.yml run(s) for <m> are not reported yet. The Controller builds, tags and publishes nothing, and closes out once the release is published. If no run of `main.yml` appears, publish by hand with `tools/release.py` (`docs/guide/ci-and-releases.md`, "Checking a release by hand"); the next step classifies the commit again and settles ``;
  `MERGED_SQUASHED merge: {"attempts": 1, "head": "<A>", "last_attempt_at": "<t>", "state": "accepted"} release: null`;
  still on `milestone/wi-1` (no close-out yet);
- `repository preflight: wait_release -- the step classifies the squash commit <m> and waits for its release, then closes out and stops (as of <t>)`
  and `milestone branch: milestone/wi-1 for wi-1 (MERGED_SQUASHED), branch point <B0>, pull request #1 https://github.com/example-owner/example-repo/pull/1, fresh (0 behind the trunk), observed <t>, merge: accepted at <A> (attempt 1)`;
- with run 100 queued: the same gate, now `... and the main.yml run(s) for <m> are run 100 (queued) https://github.com/example-owner/example-repo/actions/runs/100. ...`;
- a failed `Workflow conformance` run changes nothing: still `release_pending`;
- once run 100 succeeded and `v1.1.0` is published: `exit 0, new job records: 0`,
  `CLOSED merge: {...} release: {"state": "ALREADY_RELEASED", "tag": "v1.1.0", "url": "https://github.com/example-owner/example-repo/releases/tag/v1.1.0", "version": "1.1.0"}`,
  the events ending `merge_sent merge_accepted merged_squashed released closed`, `main`, `<m>`;
- `0`: no `release download` and no `release create` (I4); `['main.yml']`: the only workflow
  whose runs were read;
- `milestone: wi-1 CLOSED on milestone/wi-1, pull request #1 (worktree <R>), merge: accepted at <A> (attempt 1), release: v1.1.0 https://github.com/example-owner/example-repo/releases/tag/v1.1.0`
  and `['branch', 'merge', 'pull_request', 'release', 'state', 'work_item_id', 'worktree_root'] accepted v1.1.0`
  (`status --json`);
- the next step plans from the trunk: `the next step: exit 0`, `FINISHED -- /milestone-plan <m>`.

K2: `exit 10` and
`GATE_BLOCKED -- the merged milestone's release did not publish (a publishing run failed, or one succeeded without publishing); re-run the failed run on GitHub, or publish it by hand (release_failed): pull request #1 was squash-merged as <m>, but its release did not publish: the main.yml run(s) for <m> did not succeed: run 100 (completed, failure) https://github.com/example-owner/example-repo/actions/runs/100. The Controller builds, tags and publishes nothing, and does not close out; the next step classifies the commit again, so a re-run, a hand publication or a later trunk run that publishes the release settles this gate`;
`again: exit 10`, `1` (one `release_failed` event for one detail), still `milestone/wi-1`; once
the release exists, `after the re-run published: exit 0`, `CLOSED`.

K3: `exit 10` and the same title with `... but its release did not publish: the publishing workflow main.yml completed successfully for <m> and published nothing covering <m>, which it does only when its own classification differed (for example, the policy at <m> is not the one the Controller read). The Controller builds, ...`.

K4: after the cancel, `exit 10` and
`... (release_pending): pull request #1 was squash-merged as <m>; its release is not settled yet: the main.yml run(s) for <m> ended without publishing (run 100 (completed, cancelled) https://github.com/example-owner/example-repo/actions/runs/100); the run(s) for <d> on the trunk can still publish the release covering <m>: not reported yet. ...`;
with `<d>`'s run listed, the tail `run 101 (in_progress) https://github.com/example-owner/example-repo/actions/runs/101. The Controller builds, ...`;
once `<d>`'s run publishes `v1.1.0` at `<d>`: `exit 0`,
`CLOSED merge: {...} release: {"commit": "<d>", "state": "SUPERSEDED", "tag": "v1.1.0", "url": "https://github.com/example-owner/example-repo/releases/tag/v1.1.0", "version": "1.1.0"}`,
and the clone at `<d>`.

K5: `exit 10` with `... the main.yml run(s) for <m> ended without publishing (run 100 (completed, failure) https://github.com/example-owner/example-repo/actions/runs/100); the run(s) for <d> on the trunk can still publish the release covering <m>: run 101 (in_progress) https://github.com/example-owner/example-repo/actions/runs/101. ...`,
`0` (no `release_failed` while the later run can still publish); then `exit 0` and
`CLOSED ... release: {"commit": "<d>", "state": "SUPERSEDED", ...}`.

K6: `exit 10` with the `release_pending` gate from the trunk, the clone still at `<B0>`; once
published, `exit 0`, `CLOSED`, `['trunk']` (closed out from the trunk side), still `<B0>`; the
next step is 1.5.0's trunk-side behaviour:
`GATE_BLOCKED -- fast-forward the trunk to its remote before a milestone starts (fast_forward_trunk): main is 1 commit(s) behind origin/main; ...`.

K7, a person's "Create a merge commit": `exit 0`, `FINISHED -- /milestone-plan <sha>` (closed out
and planned in the same step, as in 1.5.0),
`CLOSED merge: null release: {"reason": "not a verified squash merge", "state": "SKIPPED"}`,
the events ending `ready merged release_wait_skipped closed`, and `0` (no run was read).

**L. `run` waits, `step` does not (E).** About two and a half minutes. `runlog` prints the newest
run's state, exit code, job count and events.

```bash
. /tmp/c4-fr/fr.sh
runlog() { python3 -c "import json,sys; r=json.load(open(sys.argv[1]+'.json')); print(r['state'], r['exit_code'], len(r['job_ids']), [(e['event'], e.get('gate') or e.get('reason') or '') for e in map(json.loads, open(sys.argv[1]+'/events.jsonl'))])" "$(ls -td "$RT"/runs/*/ | head -1 | sed 's#/$##')"; }
echo "== L1 the budget, step, a no-key binding and wait_seconds 0"; drive stop l1 auto >/dev/null; use l1
printf '{"merge": {"wait_seconds": 25, "poll_seconds": 10}}\n' > "$T/settings.json"; wcx step "$R"
n=$(njobs); s=$(date +%s); wcx step "$R"; echo "step: exit $? after $(( $(date +%s) - s )) s, job records +$(( $(njobs) - n ))"
n=$(njobs); s=$(date +%s); wcx run "$R"; echo "run: exit $? after $(( $(date +%s) - s )) s, job records +$(( $(njobs) - n ))"; gate; runlog
printf '{"merge": {"wait_seconds": 0}}\n' > "$T/settings.json"; s=$(date +%s); wcx run "$R"; echo "run, wait_seconds 0: exit $? after $(( $(date +%s) - s )) s"; runlog
drive stop l1n none >/dev/null; use l1n; printf '{"merge": {"wait_seconds": 25, "poll_seconds": 10}}\n' > "$T/settings.json"; wcx step "$R"
s=$(date +%s); wcx run "$R"; echo "run, no key: exit $? after $(( $(date +%s) - s )) s"; gate; runlog
echo "== L2 one run from pending checks to the released, closed-out milestone"; drive stop l2 release >/dev/null; use l2
printf '{"merge": {"wait_seconds": 300, "poll_seconds": 10}}\n' > "$T/settings.json"; n=$(njobs); s=$(date +%s)
wcx run --follow "$R" 2> "$T/follow.txt" & pid=$!
sleep 12; act checks pass
until [ "$(git --git-dir="$O" log -1 --format=%s main)" = "feat: the auto-merge lifecycle (#1)" ]; do sleep 1; done
act run main in_progress; sleep 12; act publish v1.1.0 main; act finish 100 success
wait $pid; echo "run: exit $? after $(( $(date +%s) - s )) s, job records +$(( $(njobs) - n ))"; M=$(git --git-dir="$O" rev-parse main)
rec | sed 's/ merge: .* release:/ ... release:/'; git -C "$R" branch --show-current; act show; runlog
grep no_action "$(ls -td "$RT"/runs/*/ | head -1)events.jsonl" | python3 -c "import json,sys; e=json.load(sys.stdin); print(e['reason'], '|', e['release'])"
sed "s/^[0-9:]* //; s/ until [0-9TZ:-]*\$/ until <deadline>/; s/[0-9]\{8\}T[0-9]\{6\}Z-[0-9a-f]\{8\}/<id>/g" "$T/follow.txt" | norm
echo "== L3 a second step while a run waits"; drive stop l3 auto >/dev/null; use l3
printf '{"merge": {"wait_seconds": 20, "poll_seconds": 10}}\n' > "$T/settings.json"; wcx step "$R"
wcx run "$R" & pid=$!; sleep 4
wcx step "$R" 2>&1 | norm | cut -c1-100; echo "second step: exit ${PIPESTATUS[0]}"
wcx status | sed -n '/^active:/,/^[a-z]/p' | head -2 | sed 's/[0-9]\{8\}T[0-9]\{6\}Z-[0-9a-f]\{8\}/<run>/; s/pid [0-9]*/pid <pid>/; s/started [0-9TZ:-]*/started <t>/' | norm
RUN=$(python3 -c "import glob,json,sys; print(*[json.load(open(f))['run_id'] for f in glob.glob(sys.argv[1]+'/runs/*.json') if json.load(open(f))['state'] == 'running'])" "$RT")
wcx follow --run "$RUN" "$R" 2>&1 | sed 's/^[0-9:]* //; s/[0-9]\{8\}T[0-9]\{6\}Z-[0-9a-f]\{8\}/<id>/g; s/ until [0-9TZ:-]*$/ until <deadline>/'; echo "follow: exit ${PIPESTATUS[0]}"
wait $pid; echo "run: exit $?"
echo "== L4 Ctrl-C while a run waits"; drive stop l4 auto >/dev/null; use l4
printf '{"merge": {"wait_seconds": 300, "poll_seconds": 10}}\n' > "$T/settings.json"; wcx step "$R"; n=$(njobs); s=$(date +%s)
(cd /tmp && timeout -s INT --preserve-status 15 "$W" --runtime-dir "$RT" --settings "$T/settings.json" --workflow-manager "$SM" \
  --claude-binary "$S/src/tests/fake_claude.py" --timeout 60 run "$R") 2> "$T/err"
echo "run: exit $? after $(( $(date +%s) - s )) s, job records +$(( $(njobs) - n ))"; tail -3 "$T/err"; runlog
wcx status | grep -E '^(active|milestone):' | norm; rec
act checks pass; wcx step "$R"; echo "the next step: exit $?"; rec | cut -d' ' -f1
```
Expected:
- L1: `step: exit 10 after 0 s, job records +1` (or `1 s`); `run: exit 10 after 25 s, job records +1` (or `26 s`)
  (the budget, `merge.wait_seconds: 25`, spent at `checks_pending`; one job record for the final
  gate), `GATE_BLOCKED -- wait for the pull request's checks to finish (checks_pending): pull request #1 has no checks reported yet`,
  and `ended 10 1 [('run_started', ''), ('step_started', ''), ('waiting', 'checks_pending'), ('job_started', ''), ('job_ended', ''), ('run_ended', '')]`;
  `run, wait_seconds 0: exit 10 after 1 s` (or `0 s`) with no `waiting` event; the no-key target:
  `run, no key: exit 10 after 1 s` (or `0 s`), the same gate, no `waiting` event (a gate is
  waitable only in an auto-merge binding);
- L2: the driver's two lines, then `run: exit 0 after 42 s, job records +0` (about 40-45 s: one
  run, one step, no job record), `CLOSED ... release: {"state": "ALREADY_RELEASED", "tag": "v1.1.0", ...}`,
  `main`, `PR #1 MERGED ...; pr merge calls: 1; origin main <m> 'feat: the auto-merge lifecycle (#1)'`,
  `ended 0 0 [('run_started', ''), ('step_started', ''), ('waiting', 'checks_pending'), ('waiting', 'release_pending'), ('no_action', 'closed_out_released'), ('run_ended', '')]`,
  `closed_out_released | v1.1.0 https://github.com/example-owner/example-repo/releases/tag/v1.1.0`,
  and the `--follow` rendering:
  `run    run <id> started`, `run    step 1`, `run    waiting at checks_pending until <deadline>`,
  `run    waiting at release_pending until <deadline>`,
  `run    no action at MILESTONE_COMPLETE: closed_out_released`, `run    run ended: exit 0`;
- L3: `error: another Controller or a previous worker holds the lifecycle lock on <R>/.git -- `...
  and `second step: exit 45`; `active:` and
  `  run <run> (run, target <R>, started <t>): controller pid <pid> active`; `follow --run`
  replays and follows the waiting run to its end: `run    run <id> started`, `run    step 1`,
  `run    waiting at checks_pending until <deadline>`, `run    job <id> started`,
  `job    gate: job <id> GATE_BLOCKED -- pull request #1 has no checks reported yet; required: wait for the pull request's checks to finish (checks_pending): pull request #1 has no checks reported yet`,
  `run    job <id> ended: GATE_BLOCKED`, `run    run ended: exit 10`, `follow: exit 0`; `run: exit 10`;
- L4: `run: exit 130 after 15 s, job records +0`; stderr ends with Python's `KeyboardInterrupt`
  traceback through `sleep(min(ctx.poll_seconds, remaining))` (Ctrl-C anywhere in a step ends
  this way, as in 1.5.0); `interrupted None 0 [('run_started', ''), ('step_started', ''), ('waiting', 'checks_pending'), ('run_interrupted', '')]`;
  `active: none`, `milestone: wi-1 PR_OPEN on milestone/wi-1, pull request #1 (worktree <R>)`,
  `PR_OPEN merge: null release: null`; then `the next step: exit 0` and `CLOSED`: the next step
  continues from the last written binding state.

**M. The automated evidence**, in `$S/src` under the reaping wrapper (about three and a half
minutes):

```bash
. /tmp/c4-fr/fr.sh; cd "$S/src"
python3 "$S/reap.py" python3 -m unittest tests.test_settings tests.test_repo_policy tests.test_cli.SettingsWiringTest 2>&1 | tail -3
python3 "$S/reap.py" python3 -m unittest tests.test_forge tests.test_no_rewrite_invariants 2>&1 | tail -3
python3 "$S/reap.py" python3 -m unittest tests.test_pull_request_lifecycle tests.test_release_txn tests.test_trunk_preflight 2>&1 | tail -3
python3 "$S/reap.py" python3 -m unittest tests.test_observe tests.test_hints_parse tests.test_observation_equivalence tests.test_trunk_orchestration_e2e 2>&1 | tail -3
python3 tests/golden/generate_no_policy_lifecycle.py --check >/dev/null; echo "no-policy golden --check: exit $?"
python3 tests/golden/generate_external_implementation_review_decisions.py --check >/dev/null; echo "external review golden --check: exit $?"
python3 tests/golden/generate_plan_stage_decisions.py --release 2.6.0 --check >/dev/null; echo "plan-stage golden --release 2.6.0 --check: exit $?"
git status --short | wc -l
```
Expected: `Ran 133 tests` OK (the settings rows and the bool type, the policy keys, the wiring of
the three rows into `execute_step`, only `run` waiting); `Ran 52 tests` OK (`merge_squash` and
`commit_runs` argv and parsing, and I3' narrowed to the one argv shape in `controller/forge.py`,
with the synthetic-source cases both ways); `Ran 325 tests` OK (every C.2 row, the send, the
crash rows, the refusals and adoption from the trunk, the release wait's D.2/D.3 rows,
`verify_assets=False`, `covering_tag`, the waiting preflight); `Ran 112 tests` OK (status,
observation and hint parsing, and the end-to-end lifecycles including the no-policy golden
equivalence). The three golden checks each `exit 0`, and `0` (nothing in `$S/src` changed). The
full suite is PR #18's CI (flow O).

**N. It releases 1.6.0, and its notes pass** (the classification over a scratch origin with the
real tags and `v1.1.1`-`v1.5.0` published, as for 1.5.0):

```bash
. /tmp/c4-fr/fr.sh; X=$S/ver; rm -rf "$X"; mkdir -p "$X/bin"
git init -q --bare "$X/origin.git"
git --git-dir="$X/origin.git" fetch -q --no-tags "$R0" "+$BASE:refs/heads/main" "+refs/tags/v*:refs/tags/v*"
git clone -q "$X/origin.git" "$X/work"; cd "$X/work"; git config user.name fr; git config user.email fr@example.invalid
cp "$S/src/tests/fake_gh.py" "$X/bin/gh"; chmod +x "$X/bin/gh"
python3 - "$X/gh-state.json" <<'EOF'
import json, sys
repo = "RodrigoFAbreu/workflow-controller"
rel = [{"tagName": t, "isDraft": False, "url": f"https://github.com/{repo}/releases/tag/{t}", "assets": [], "title": t,
        "notes": f"workflow-controller {t}"} for t in ("v1.1.1", "v1.2.0", "v1.2.1", "v1.3.0", "v1.4.0", "v1.4.1", "v1.4.2", "v1.5.0")]
json.dump({"repository": repo, "url": f"https://github.com/{repo}", "next_number": 19, "prs": [], "releases": rel, "runs": []},
          open(sys.argv[1], "w"), indent=2)
EOF
export PATH="$X/bin:$PATH" FAKE_GH_STATE="$X/gh-state.json" FAKE_GH_ORIGIN="$X/origin.git" FAKE_GH_LOG="$X/gh.log" FAKE_GH_FAIL='{}'
git fetch -q "$S/origin.git" "$H"; git checkout -q --detach "$H"
python3 tools/release.py check-title "feat: auto-merge an accepted milestone and wait for its release"
Q=$(git commit-tree "$H^{tree}" -p origin/main -m "feat: auto-merge an accepted milestone and wait for its release (#18)")
git push -q origin "$Q:refs/heads/main"; git fetch -q origin; git checkout -q --detach "$Q"
python3 tools/release.py version; python3 tools/release.py classify --commit "$Q" | sed "s/$Q/<Q>/"; echo "exit ${PIPESTATUS[0]}"
git -C "$R0" show "$H:docs/ACTIVE_MILESTONE.md" > "$S/narrative.md"
"$S/venv/bin/python" - <<'EOF'
from controller import release_notes as rn
notes = rn.extract_section(open("/tmp/c4-fr/narrative.md", encoding="utf-8").read(), "Release notes")
print("lines", len(notes.split("\n")), "| first:", notes.split("\n")[0], "| problem:", rn.notes_problem(notes))
EOF
```
Expected: `ok: feat → minor`; `1.5.0` (the highest reachable tag); then
`ok: RELEASE_DUE: 1.6.0 has no tag and no release` (on stderr), `state=RELEASE_DUE`,
`version=1.6.0`, `tag=v1.6.0`, `commit=<Q>`, `exit 0`; and
`lines 33 | first: ### Auto-merge and the wait for the release (1.6.0) | problem: None` (the
narrative's `## Release notes` section at `3d875a7`). This repository's policy does not opt in to
release notes, so the 1.6.0 release itself publishes the fixed text `workflow-controller v1.6.0`.

**O. PR #18.** `gh pr checks 18` and `gh pr view 18 --json title,isDraft,headRefOid`
(read-only). Expected: eleven checks, every one `pass`: `PR title`, `validate / package`,
`validate / plan`, `validate / tests (0)` to `(5)`, `validate / tests-result` and
`workflow-conformance` (measured at `3d875a7`). The pull request is a draft at `3d875a7`
(`"isDraft":true`, `"headRefOid":"3d875a7106b5c3e1be0413dd12a50253dfa311a7"`), titled with the
plan's declared `feat: auto-merge an accepted milestone and wait for its release`.

**P. The documentation agrees with A-N.** Read each and check it against the flows:
- `docs/guide/milestone-branches.md`, "Auto-merge and the release wait": the two policy keys and
  their refusals (C), the operator's switch (F), the numbered merge decision list (G, H, J; note
  that row 4, the accepted merge, comes before the checks and the merge state, which is why J's
  lagging reads keep waiting), the one `gh pr merge` argv and the intent record (E, I, J), the
  three attempts and `--new-pr` (I), branch-side only (H5), the release wait's bullets (K), close
  out then stop (E, K1, L2), a hand merge (K7, I1), `run` waits and `step` does not (L), what you
  see (E, J2, K1), merge queues unsupported;
- `docs/guide/ci-and-releases.md`, "Repository settings" (squash merging, no auto-merge request,
  `release_workflow`, merge queues) and how `release_failed` is resolved (K2, K3);
- `docs/guide/automation.md`: the bullet "Merges only the accepted head, never rewrites" (1.5.0's
  "Never merges, never rewrites", restated as I3'; E: one argv shape, only at the acceptance
  commit);
- `docs/guide/runtime.md`: the three `merge.*` rows, their bounds and the boolean type (B);
- `docs/guide/commands.md` (`run` waits, `step` never does; `status` lines) and
  `docs/guide/troubleshooting.md` (`merge_pending`, `merge_held`, `release_pending`/`release_failed`,
  "`step` exits 45 while a `run` waits", the stop after close-out) (G-L);
- `docs/guide/concepts.md` and `README.md` where they mention the merge;
- `docs/adr/0009-auto-merge-and-release-wait.md` (the decision, I3', the two switches, the stop,
  what stays human); `docs/README.md` lists ADR 0009;
- the `## Release notes` section below (the 1.6.0 notes, flow N): each claim matches a flow.

**Q. OPTIONAL LIVE: GitHub's own merge-refusal texts. Run only with the user's explicit
authorization.** Plan C.3 and the Verification section leave one observation to functional
review: the exact texts `gh` and GitHub give when a head-bound squash merge is refused, for the
guide (no code keys on them). This flow writes to GitHub: it pushes two throwaway branches,
`fr-c4-live-base` (at `main`'s tip) and `fr-c4-live-head` (one commit adding `fr-c4-live.txt`),
opens one throwaway draft pull request **into `fr-c4-live-base`, never `main`**, probes `gh pr
merge --squash --match-head-commit` four times, merges it into the throwaway base, and deletes
both branches. It never touches `main`, PR #18 or any tag, and it runs no Controller. Opening the
pull request starts this repository's pull-request workflows (`ci.yml`, `pr-title.yml`,
`workflow-conformance.yml`) once; the merge into `fr-c4-live-base` starts nothing (`main.yml`
runs only for `main`). The merged throwaway pull request stays in the repository's pull-request
list. If you prefer it closed unmerged, stop after Q2 and run `gh pr close "$P" --repo "$GHR"
--delete-branch`, then `git push -q origin --delete fr-c4-live-base`.

```bash
. /tmp/c4-fr/fr.sh; LV=$S/live; GHR=RodrigoFAbreu/workflow-controller; B=fr-c4-live-base; HB=fr-c4-live-head
rm -rf "$LV"; git clone -q --no-tags --single-branch --branch main "$(git -C "$R0" remote get-url origin)" "$LV"; cd "$LV"
git ls-remote origin refs/heads/main > "$S/live-main.txt"; git ls-remote --tags origin | sha256sum > "$S/live-tags.txt"
git push -q origin "HEAD:refs/heads/$B"
git switch -q -c "$HB"; echo "throwaway functional-review probe, merged into $B only" > fr-c4-live.txt
git add fr-c4-live.txt; git commit -qm "chore: functional review probe (throwaway)"; HS=$(git rev-parse HEAD)
git push -q origin "$HB"
URL=$(gh pr create --repo "$GHR" --base "$B" --head "$HB" --draft \
  --title "chore: functional review probe (throwaway, never merged into main)" \
  --body "Throwaway pull request of the workflow-controller-auto-merge-release-wait functional review. It records GitHub's merge refusal texts; its base is the throwaway branch $B, never main.")
P=${URL##*/}; echo "PR #$P $URL"
probe() { echo "== $1"; shift; gh pr merge "$P" --repo "$GHR" --squash "$@" --subject "chore: functional review probe (#$P)" --body "probe"; echo "exit $?"; git ls-remote origin "refs/heads/$B" | cut -c1-12; }
probe "Q1 a draft" --match-head-commit "$HS"
gh pr ready "$P" --repo "$GHR"
probe "Q2 a wrong head" --match-head-commit "$(git rev-parse HEAD~1)"
gh pr view "$P" --repo "$GHR" --json state,isDraft,mergeStateStatus,headRefOid
probe "Q3 the right head" --match-head-commit "$HS"
probe "Q4 the same merge again" --match-head-commit "$HS"
gh pr view "$P" --repo "$GHR" --json state,baseRefName,mergeCommit
git ls-remote origin refs/heads/main | cmp - "$S/live-main.txt" && echo "main unchanged"
git ls-remote --tags origin | sha256sum | cmp - "$S/live-tags.txt" && echo "tags unchanged"
git push -q origin --delete "$HB" "$B"; git ls-remote origin "refs/heads/fr-c4-live-*" | wc -l
```
Expected (not measured: this flow was not run when the checklist was written): `PR #<P> <url>`;
Q1 (a draft) and Q2 (a head that is not the pull request's) each a non-zero exit with `gh`'s or
GitHub's refusal text, and `fr-c4-live-base` unchanged (the same 12 characters as before); the
`gh pr view` JSON (`OPEN`, `isDraft: false`, the merge state GitHub computed); Q3 `exit 0` and
`fr-c4-live-base` moved; Q4 (the same merge again) a non-zero exit with GitHub's "already merged"
text; `"state":"MERGED"` with `"baseRefName":"fr-c4-live-base"`; `main unchanged`, `tags
unchanged`, and `0` (both throwaway branches deleted). **Record each probe's exact output and
exit code in `FUNCTIONAL_REVIEW.md`**; the texts are for `docs/guide/milestone-branches.md`, and a
difference from the fake `gh`'s wording is not a defect (the Controller keys on no refusal text).

### Known limitations and out of scope

- GitHub is the suite's fake `gh` in A-P: it models the merge, the merge states, read lag, a lost
  reply, the runs and the releases the way the plan's investigation measured them, but its
  refusal texts are its own. GitHub's real texts are flow Q's observation.
- The installed Controller stays 1.5.0 and this repository's policy is unchanged (I9): the
  opt-in here is a later `chore:` pull request, after 1.6.0 is released and installed into the
  shared install (the plan's migration order). Until the `v1.6.0` tag exists the local build
  reports `workflow-controller 1.5.0`.
- Ctrl-C in a waiting `run` (L4) ends with Python's `KeyboardInterrupt` traceback and exit `130`,
  like any Ctrl-C inside a step in 1.5.0 (`cli.main` re-raises it after closing the run).
- Not in this milestone: the fix loop for a red pull request after acceptance and automatic
  acceptance (C10), integrating `main` into a milestone branch (`integration_required` stays
  manual), merge mode, merge queues (unsupported, fail closed at `MERGED_REWRITTEN`), publishing or
  retrying a release, and notifications (C5).
- A close-out from the trunk does not fast-forward `main` (K6); the next step's
  `fast_forward_trunk` gate is 1.5.0's behaviour.
- `tests/golden/generate_plan_stage_decisions.py --check` without `--release` reports its
  `AMENDING_PLAN` cases differ in this environment, as at the base; the `--release 2.6.0` form
  (flow M) and `tests.test_golden_plan_stage_decisions` pass.

### After testing

Findings go in `.ai-review/workflow-controller-auto-merge-release-wait/feedback/FUNCTIONAL_REVIEW.md`.
`/apply-functional-review` routes each one: a bounded fix in this work item, or a
`workflow-controller-auto-merge-release-wait-remediation-<n>` child for new or wider scope. When
testing is clean, `/accept-milestone` is the only acceptance command; every checkpoint is already
`COMPLETE`.

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
`release_failed` (naming each run that failed, or saying the workflow
succeeded and published nothing). A later trunk run that publishes the
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
generation 2); a 1.5.0 Controller sharing it warns about them, and its
`settings clean` refuses the generation-2 file (exit 20).
