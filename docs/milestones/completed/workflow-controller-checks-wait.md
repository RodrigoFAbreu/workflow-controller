# Checks wait: a spent pending-checks wait is an info event (Revision 3)

Work item: `workflow-controller-checks-wait`
Work item type: `product`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json` default at creation)
Base commit: `8552217a37030c590533e35b525290a6bfb9de09` ("docs: next action is NF2, then RB3, then H7 (#176)"), the tip of `main`, passed explicitly as `/milestone-plan 8552217…`.
Lifecycle authority: this repository's installed Workflow **2.20.0**, automatic gates.
Roadmap slot: `docs/ROADMAP.md` "Next, in order" item 1, NF2 (RB3 follows).
Released baseline: `workflow-controller 1.22.0`. The change alters which notification a stop sends, so the squash commit is a `fix:` change and releases a patch (1.22.1).
Pull request title: `fix: a pending-checks stop sends the info event checks_wait, and lane_blocked only after four hours (#170)`

## Goal

Close [#170](https://github.com/RodrigoFAbreu/workflow-controller/issues/170) as the owner decided it on 2026-10-25: a critical (`lane_blocked`) event must mean something is wrong. Four of the six `lane_blocked` events of one day were a pull request whose checks were still running when the run's wait budget was spent; CI takes about 25 minutes, so nothing was wrong.

One concern only: **which event a `checks_pending` stop sends** (a spent wait, or a step in a repository without auto-merge that cannot wait). No new setting, no change to an exit code, a step count, a gate identity, the wait itself, the other wait codes, or the pull-request defect loop.

## Decisions

Each decision states one principle.

- **D1. A spent checks wait is its own low event.** `controller/notify.py` gains the event type `checks_wait` in `EVENT_TABLE`, of kind `state` (INFO, LOW), on by default; the `events` section of the settings file already accepts any type in the table, so an operator can still raise, lower or switch it off. Title `Waiting for the pull request's checks (<work item>)` (no suffix without a work item), body the gate's own message and next step, as the blocker's was.
- **D2. Long is a fixed age of the pending checks, not a setting.** A spent wait becomes `lane_blocked` only when the checks have been pending longer than `CHECKS_PENDING_LONG_SECONDS` (four hours: ten times the usual CI run and four times the default `merge.wait_seconds`), measured as the seconds from the latest event of the work item's binding record (`at` of its newest event: the last change the Controller recorded on the binding, such as a push or the pull request marked ready or edited, not the start of a re-run begun by hand on GitHub; a later event only shortens the age, the safe direction) to the clock when the step ended. One pure helper in `controller/job.py` holds the limit and the comparison; both paths call it. An unreadable or missing age counts as short (the info event is still sent, so the stop is not lost). A failed check keeps its own path (`checks_failing`, the defect loop), unchanged.
- **D3. Both paths decide the same way.** The branch-gate path (`derive_branch_gate_event`, for `checks_pending` only) takes the age from `_notify_branch_gate`, which already reads the binding events. The job-record path takes the same age from `_binding_events(runtime_root, key, binding)`, already in reach in `_run_pr_report`, so both paths share one measure (never the record's `updated_at`). The job-record path (`derive_job_event` on a pull-request-report record that waited) cannot read live state, so `_run_pr_report` writes the age into the record's `wait` block as the additive field `pending_seconds`; a record without it (written before this change) is short. **A `checks_pending` gate that cannot wait follows the same rule.** `milestone_branch._readiness_wait` makes `checks_pending` waitable only when auto-merge applies; otherwise `classify` returns a blocker and the base sends `lane_blocked` on every step while CI runs (the #170 class: nothing is wrong). That gate sends `checks_wait` when the age is short and `lane_blocked` when it is long, whether or not the step waited, with the same identity and `supersedes`. A *waitable* `checks_pending` gate whose step did not wait or whose wait is not spent still sends nothing. `merge_pending` with GitHub's `DIRTY` (non-waitable) stays a blocker: it needs a person. The other wait codes (`merge_pending`, `release_pending`, `pr_head_not_accepted`) are unchanged.
- **D4. The identity is the stop's own, and the old notice still counts.** `checks_wait` and the long `lane_blocked` use the identity the blocker used, so a re-derived event cannot duplicate. `checks_wait` carries `supersedes="lane_blocked"`, so after an upgrade a stop whose `lane_blocked` was already delivered is not announced again as the info event, by the same `_superseded_handled` check NF1 added (live and recovery paths unchanged). The long `lane_blocked` carries no `supersedes`: it must still be sent after a `checks_wait` of the same stop.
- **D5. The documentation says the rule.** `docs/guide/commands.md` (the event table), `docs/guide/automation.md` (the pending-checks sentence) and `docs/ROADMAP.md` (NF2 done, next is RB3) change; the release note, history row and compatibility line belong to the docs pull request after the 1.22.1 release, as for every earlier release. The pull request closes #170.

## Out of scope

RB3 (#168), the lane's own watchdog duplicate (in the lane script), any other event type or wait code, a setting for the limit, the pull-request defect loop, and tracking how long a pending state lasts across head changes.

## Checkpoints

The table below is generated from the registry; never edit it by hand.

| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | checks_wait and the long escalation | - | 2 | 1 |
| CP2 | The guides and the roadmap | CP1 | 1 | 1 |

<!-- CP1 -->
### CP1 -- `checks_wait` and the long escalation (#170)

**Files:** `controller/notify.py`, `controller/job.py`, `tests/test_notify.py`, `tests/test_notifications_wiring.py`, `tests/test_pr_report.py`

D1, D2, D3, D4. Add `checks_wait` to `EVENT_TABLE` as a `state` event. Add `CHECKS_PENDING_LONG_SECONDS` and one helper that answers "is this age long" next to `_pending_checks_wait`. `derive_branch_gate_event` gains `pending_seconds` and, for `checks_pending` that is either waitable with `waited and spent` or non-waitable (no auto-merge), returns `checks_wait` (with `supersedes="lane_blocked"`) or, when the age is long, `lane_blocked` as today; `_notify_branch_gate` computes the age from the binding events and a `now` passed in by its caller, `_run_branch_step`, from `branch_ctx.clock()` taken after the preflight returns (the clock the preflight used), so the long-age tests drive it by a fake clock and patch no module global. `_run_pr_report` writes `wait.pending_seconds`, taking the age from `_binding_events(runtime_root, key, binding)`; `derive_job_event` maps a waited pending-checks record the same way. Tests, one per mapping, through the real writers: the branch-gate path (the `_notify_branch_gate` send against the fake hub) and the job-record path (a record from the production `_run_pr_report`, read back and derived): a spent wait with a short age sends one `checks_wait` (INFO, LOW through `notify.resolve`) with the old identity; the same with an age over the limit sends `lane_blocked`; a waitable gate whose step did not wait sends nothing; **a non-waitable `checks_pending` gate (no auto-merge) sends `checks_wait` for a short age and `lane_blocked` for a long one, and a non-waitable `merge_pending` (`DIRTY`) still sends `lane_blocked`**; a missing age is short; the other wait codes and `checks_failing` derive what they derived at the base commit. The existing assertions that a spent `checks_pending` wait is `lane_blocked` (`tests/test_notifications_wiring.py`, the wait-gate and pending-record tests; and in `tests/test_pr_report.py` `PrReportNotificationTest.test_pending_checks_left_after_the_wait_budget_is_spent_are_a_blocker` and `test_notifications_follow_the_real_pending_check_records`, whose `wait_seconds=25` row, a record from the production `_run_pr_report`, becomes the job-record test with a fresh binding and expects `checks_wait`) move to the new mapping. **Upgrade regression:** a stop whose `lane_blocked` was delivered in an earlier run sends no `checks_wait` on the next step; a stop whose `lane_blocked` was never handled sends one `checks_wait`; and a `checks_wait` already sent does not stop the later long `lane_blocked`. The event-table test in `tests/test_notify.py` covers the new type's default.

Done when: the new tests pass and fail against the base commit (checked once by stashing the source edits); `python3 -m unittest tests.test_notify tests.test_notifications_wiring` is green; the full suite is green but for the known Git 2.56 trailer tests, with no edit outside the declared files.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- The guides and the roadmap (#170)

**Files:** `docs/guide/commands.md`, `docs/guide/automation.md`, `docs/ROADMAP.md`

D5. `docs/guide/commands.md` lists `checks_wait` in the `state` row of the event-type table and adds `checks_wait` (a spent wait, or a stop without auto-merge, for a pull request's checks) to the exception at the sentence that says ADR 0012 covers every event type "except `review_wait`, added later"; `docs/guide/automation.md` replaces "a `run` whose `merge.wait_seconds` budget ran out with only the checks pending sends `lane_blocked`" with the new rule (a spent wait, or a stop in a repository without auto-merge, sends `checks_wait`, INFO/LOW; `lane_blocked` only when the checks have been pending over four hours, counted from the last change the Controller recorded on the binding, such as a push or the pull request marked ready or edited) and the upgrade note; `docs/ROADMAP.md` marks NF2 done, moves "Next, in order" item 1 to RB3, and amends the C10b done note ("pending checks send nothing, or `lane_blocked` once a wait budget is spent") in place to say it holds until NF2. Edited in place, with no stale narrative. The pull request text names #170 as closed.

Done when: `python3 tools/check_docs.py` passes and a search of the guides for `lane_blocked` finds no sentence that still says a spent checks wait sends it.
<!-- /CP2 -->
