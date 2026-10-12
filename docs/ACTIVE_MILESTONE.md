# Active Milestone

## Status

**In progress.** `workflow-controller-signalhub-notifications` (`docs/ROADMAP.md` step C5): the Controller
becomes a SignalHub producer with per-event-type configurable notifications. Plan revision 16 was approved by
gate policy (`8056cc3`); the base commit is `e0f400e`. Plan: `docs/ai-workflow/CONTROLLER_SIGNALHUB_NOTIFICATIONS_PLAN.md`.

| Checkpoint | State |
| --- | --- |
| CP1 `controller/notify.py` and a fake SignalHub | complete (`tests.test_notify`, `tests.test_package_structure`) |
| CP2 `notifications.*` settings | complete (`tests.test_settings`, `tests.test_cli`) |
| CP3 deriving and emitting the events | complete (`tests.test_notifications_wiring`, `tests.test_job`, `tests.test_usage`, `tests.test_milestone_branch`, `tests.test_cli`) |
| CP4 the `notifications` command | complete (`tests.test_cli`) |
| CP5 documentation and roadmap | not started |

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for checkpoint status. The previous milestone's
summary is archived at `docs/milestones/completed/workflow-controller-test-leftover-lifetime.md`.

## Next action

Run `/milestone-implement workflow-controller-signalhub-notifications` for the next checkpoint (CP5).
