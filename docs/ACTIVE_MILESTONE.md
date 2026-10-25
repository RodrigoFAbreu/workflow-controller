# Active Milestone

## Status

**In progress.** `workflow-controller-notification-fix` (NF1: review and functional-review waits send `review_wait`, an
info event, not `user_gate`; closes issue #149). Plan: `docs/ai-workflow/CONTROLLER_NOTIFICATION_FIX_PLAN.md` (revision
10, approved by gate policy). Checkpoints: CP1 (the event type, the mapping and the superseded-notice check) and CP2
(the guides and the roadmap) done. Self-review added the plan's missing production-path tests (the Controller-coded
gates over a row-14 decision stay `user_gate`; a production `step` in a new run after the upgrade sends no second notice
for a delivered `user_gate` and one `review_wait` after a failed or rejected one, and `resume` replays nothing) and ran
the superseded-notice and recovery checks for a tester stop too. The implementation review is approved (revision 3); the manual functional review is next.

The previous milestone, `workflow-controller-cleanup-3b` (CU3b), was accepted on 2026-10-24 (pull request #148); its
archive copy is in `docs/milestones/completed/`.

## Functional review checklist

All flows run offline in the repository clone; nothing needs `gh`, `claude`, `codex`, `ssh` or the network, and none
writes inside the clone (`PYTHONDONTWRITEBYTECODE=1` keeps Python from writing `__pycache__`).

**Setup.** `cd` to the repository root on `milestone/workflow-controller-notification-fix`;
`export PYTHONDONTWRITEBYTECODE=1`. No test data to seed: the tests build their own scratch repositories, run
directories and notification logs under `TMPDIR`.

| # | Flow | Expected |
| --- | --- | --- |
| flow-1 | `python3 -B -c "from controller import notify; print(notify.EVENT_TABLE['review_wait'])"` | Prints `state` (the LOW-severity class), not `user_gate`. |
| flow-2 | `python3 -B -m unittest tests.test_notifications_wiring.ReviewWaitDeriveTest` | OK. A Workflow-governed target waiting at `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` or `..._IMPLEMENTATION_REVIEW` derives `review_wait`; every tester fault stop except `isolation_breach` derives `review_wait`; `isolation_breach`, every other protocol gate, every legacy gate, the blocked disposition and pending checks keep their old events. |
| flow-3 | `python3 -B -m unittest tests.test_notifications_wiring.SupersededNoticeTest tests.test_notifications_wiring.TesterStopSupersededNoticeTest tests.test_notifications_wiring.ReviewWaitLiveUpgradeTest` | OK. A delivered old `user_gate` suppresses the `review_wait` (live and recovery); a failed or rejected one does not veto a fresh send; an unrelated delivered notice never suppresses; a symlinked run directory or log is not followed. |
| flow-4 | `python3 -B -m unittest tests.test_notifications_wiring.ReviewWaitProductionPathTest tests.test_notifications_wiring.BlockedDispositionProductionPathTest tests.test_notifications_wiring.ControllerCodedGateOverARow14DecisionTest` (slow; allow several minutes) | OK. The plan and implementation review waits are `review_wait`; their BLOCK gates, the review-mode refusal and the external round cap stay `user_gate`; a record written before this change is a `user_gate`. |
| flow-5 | `python3 -B -m unittest tests.test_protocol_job tests.test_functional_lifecycle tests.test_notify` | OK (a timing-only failure in a Ctrl-C or reap test under load is the known flake, not a defect). |
| flow-6 | `python3 -B tools/check_docs.py`; read `docs/guide/automation.md` ("Notifications from an unattended run" and the functional-review fault stops) and `docs/guide/commands.md` | The checker passes; the text matches flows 2-4 (`review_wait` is `INFO`/`LOW`; `isolation_breach` and `functional_unchanged` keep `user_gate` and `lane_blocked`). |

**Known limits / out of scope.** No real SignalHub delivery is exercised (no network); the legacy-target behaviour is
unchanged by design; the configurable reviewer brief (RB1, issue #150) is the next milestone.

## Next action

The manual functional review of this checklist. RB1 (issue #150) follows, then the
harness-neutral roles epic (H1 and H6 first).
