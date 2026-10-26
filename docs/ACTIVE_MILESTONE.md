# Active Milestone

## Status

**In progress.** `workflow-controller-checks-wait` (NF2, [#170](https://github.com/RodrigoFAbreu/workflow-controller/issues/170)),
plan revision 3 (`docs/ai-workflow/CONTROLLER_CHECKS_WAIT_PLAN.md`): a spent pending-checks wait, or a pending-checks stop
in a repository without auto-merge, sends the info event `checks_wait` (INFO, LOW), and `lane_blocked` only once the checks
have been pending over four hours.

- CP1 (`checks_wait` and the long escalation): complete. `controller/notify.py` and `controller/job.py` carry the event and
  the age rule on both paths; the new and moved tests pass, and the full suite is green but for the known Git 2.56 trailer
  tests and the scenario tests that already fail at the base commit.
- CP2 (the guides and the roadmap): complete. `docs/guide/commands.md` and `docs/guide/automation.md` describe `checks_wait`
  and the four-hour escalation, and `docs/ROADMAP.md` marks NF2 done; `python3 tools/check_docs.py` passes.

- Self-review: one test added, a long-pending spent wait sending `lane_blocked` through the branch-gate path's real
  wiring (`BranchWiringTest`), which the plan named and only the short case covered.

## Next action

The implementation is complete and in review: implementation revision 1 goes to the local implementation review.

## Functional review checklist

Work item `workflow-controller-checks-wait` (NF2, #170), implementation revision 1. Every flow runs offline from a
checkout of the milestone branch: no network, no real `gh`, `claude`, `codex` or `ssh`, nothing written inside the clone
(set `PYTHONDONTWRITEBYTECODE=1`; use `-B`). Run from the repository root.

**Setup.** None beyond the checkout; no feature flag, no seed data. The flows use the installed test suite and small
`python3 -B` snippets against `controller.notify` and `controller.job`.

**Flow 1 -- the event exists and is a low, default-on info event.**
1. `python3 -B -c "from controller import notify; print(notify.EVENT_TABLE['checks_wait'])"`.
   Expected: `state` (the `STATE` kind, which resolves to INFO/LOW per `docs/guide/commands.md`).
2. `python3 -B -m unittest tests.test_notify`. Expected: OK.

**Flow 2 -- the four-hour limit.**
1. `python3 -B -c "from controller import job; f=job._pending_checks_long; print(job.CHECKS_PENDING_LONG_SECONDS, [f(x) for x in (None, 0, 14400, 14401, True)])"`.
   Expected: `14400 [False, False, False, True, False]` (a missing age, exactly four hours, and a bool are short).

**Flow 3 -- a spent wait / no-auto-merge stop sends `checks_wait`; long ones send `lane_blocked`.**
1. `python3 -B -m unittest tests.test_notifications_wiring tests.test_pr_report`. Expected: OK except the scenario tests
   that already fail at the base commit (see Known limitations); in particular these must pass: the branch-gate path
   (`BranchWiringTest`, short and long spent wait; non-waitable `checks_pending` short and long; non-waitable
   `merge_pending` DIRTY still `lane_blocked`; a waitable gate that did not wait sends nothing) and the job-record path
   (`PrReportNotificationTest`, a pending record from the real `_run_pr_report` with a fresh binding derives `checks_wait`).
2. Optional: `python3 -B -m unittest -v tests.test_notifications_wiring 2>&1 | grep -i checks` to read the test names and
   confirm each mapping in the plan (D1-D4) has a test.

**Flow 4 -- upgrade behaviour (identity and `supersedes`).**
1. In the same wiring tests, find the upgrade regression: a stop whose `lane_blocked` was already delivered sends no
   `checks_wait`; one never handled sends one `checks_wait`; a sent `checks_wait` does not stop a later long
   `lane_blocked`. Expected: all pass.

**Flow 5 -- the documentation.**
1. `python3 -B tools/check_docs.py`. Expected: passes.
2. `grep -n "checks_wait" docs/guide/commands.md docs/guide/automation.md docs/ROADMAP.md`. Expected: the event is in the
   `state` row of the table, the pending-checks sentence of `automation.md` states the four-hour rule and the upgrade
   note, and ROADMAP marks NF2 done with RB3 next.
3. Read the `automation.md` sentence once for accuracy (a spent wait, or a stop without auto-merge, is `checks_wait`;
   `lane_blocked` only over four hours since the last binding change).

**Flow 6 -- nothing else changed.** `git diff --stat 8552217a37030c590533e35b525290a6bfb9de09..HEAD -- controller tests docs/guide docs/ROADMAP.md`.
Expected: only `controller/job.py`, `controller/notify.py`, the three test files and the three docs named in the plan.

**Known limitations / out of scope.** No new setting for the limit; RB3 (#168), the lane's own watchdog, other wait
codes, `checks_failing` and the pull-request defect loop are unchanged. A real pull request with CI cannot be exercised
in the sandbox (no real GitHub). Known failures that are not this milestone's: the Git 2.56 trailer tests and the
scenario tests that already fail at the base commit; the suite's timing-sensitive tests can flake under load.
