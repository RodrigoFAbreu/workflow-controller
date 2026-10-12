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
| CP5 documentation and roadmap | complete (`tools/check_docs.py`, full suite) |

`docs/ai-workflow/WORKFLOW_STATE.json` is the ground truth for checkpoint status. The previous milestone's
summary is archived at `docs/milestones/completed/workflow-controller-test-leftover-lifetime.md`.

## Self-review

All five checkpoints are done, so the item is in `SELF_REVIEWING_IMPLEMENTATION`. The self-review of the full
diff fixed one defect and added the plan-listed tests that were missing:

- `job.notify_refusal` named the repository from the raw argument, so `step .` (the lanes' form) sent its
  exit-20 `lane_blocked` under the context `workflow-controller`; the path is now resolved first.
- `tests/test_notifications_wiring.py`: `pr_merged` is sent once on a step that ends at a branch gate, on a
  Ctrl-C during the release wait after the merge, on a refusal the preflight raises after the merge, and on the
  changed-release refusal (F1); a draft toggled back without a push is a new `user_gate` (F5 of round 10); the
  trunk-side bind leaves no observation and an adoption stores the observation and a Proceed `gate` (F1 of
  rounds 8 and 9); a record reconciled in this call is recovered whatever its age, and a different refusal is a
  new key.

Not added (unit-covered elsewhere): the CLI-level `usage --wait`/`run` pause counts, the `repository_cap`
rollover over a real run, and the process-death-after-`CLOSED` residual.

Full verification: `python3 tools/run_tests.py`, 3514 tests in 7 shards, coverage exact; the only failures are the
two known Git 2.56 trailer-rule tests (follow-up 12). `python3 tools/check_docs.py` exit 0.

## Next action

The implementation review bundle (revision 1) is generated; the item waits at
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`.
