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

## Functional review checklist

Implementation revision 6 (technical approval `d0a4479`, base `e0f400e`). Findings go in the feedback file
named below the checklist.

**Setup.** From the repository root: `export PYTHONPATH=$PWD`, then use `python3 -m controller` as
`workflow-controller`. For a safe server, start the project's fake SignalHub (`tests/fake_signalhub.py`,
`FakeSignalHub`) in a Python shell, print its URL, and write any non-empty string to a key file
(`mktemp`). Use a scratch settings file and a scratch repository; do not point at the real SignalHub or at
the lanes' `events.json`.

**Flows.**

1. *Unconfigured.* With no `notifications.url` and no `SIGNALHUB_URL`, run `notifications`. Expected: it
   reports not configured, lists the event types with their resolved enabled/category/severity, and
   `notifications --test` exits 2.
2. *Configured test event.* Set `notifications.url` and `notifications.api_key_file` (settings file or the
   `SIGNALHUB_URL`/`SIGNALHUB_API_KEY_FILE` variables). `notifications --test` exits 0 and the fake server
   records one `POST /api/v1/events` with `Authorization: Bearer <key>`, an `Idempotency-Key`, and an INFO
   test event. The key never appears in the settings file, run output or run log.
3. *Failure codes.* Script the fake server to answer 400/422 (exit 1), then 500 or a reset (exit 3 after a
   bounded retry), and stop it entirely (exit 3). Expected: no hang beyond the bounded retry.
4. *Per-event configuration.* Set `notifications.events.kinds.state.enabled=false` and
   `events.milestone_accepted.severity="HIGH"`; run `notifications`. Expected: the listing shows the
   override winning over the kind, `usage_released` and `usage_reservation_expired` off by default, and
   `notifications.enabled: false` turns every event type off. An invalid value (`severity: "LOUD"`) is
   refused with exit 20; an unknown event type, kind or `_`-prefixed field is ignored (a warning for the
   unknown ones); pasting the lanes' `events.json` object works.
5. *Events from a real step.* In a scratch Workflow-managed repository with the server configured, run
   `step` and `run --max-steps 1`. Expected: a finished step, a human gate (`user_gate`), a blocker
   (`lane_blocked` on a refusal) and `run_finished` reach the fake server with the right category and
   severity; `step .` names the repository directory, not `.`, in the context. Merge and release events
   appear on a merge-mode milestone.
6. *No double send.* Re-run the same step or `resume` a run; the repeated event reuses its
   `Idempotency-Key` (the fake answers 200, stores one event).
7. *Delivery never steers.* Run a step with the server down, slow (delayed answer) and returning 500.
   Expected: the step's outcome, exit status and run log are identical to the no-notification case;
   only a bounded wait at exit.
8. *Usage events.* `usage --release`, `usage --renew` and a plain `usage` that sweeps an expired
   reservation: with the two off-by-default types enabled, `usage_released` and
   `usage_reservation_expired` are sent; disabled, nothing is sent.
9. *Documentation.* `python3 tools/check_docs.py` exits 0; `docs/run.md#notifications`,
   `docs/guide/runtime.md#the-notifications-section`, `docs/guide/automation.md`,
   `docs/guide/commands.md#notifications`, `docs/exit-codes.md`, `docs/common-problems.md`, ADR 0012 and
   the ROADMAP C5 entry agree with what flows 1-8 showed.

**Automated evidence.** `python3 tools/run_tests.py` (3514 tests, 7 shards); the only failures are the two
known Git 2.56 trailer-rule tests (follow-up 12). Notification suites: `tests.test_notify`,
`tests.test_notifications_wiring`, `tests.test_settings`, `tests.test_cli`.

**Known limitations / out of scope.** No persistent outbox (a process killed after the state change but
before sending loses that event); no SignalHub changes; the lanes keep sending their own events until the
post-release coordination step; the CLI-level `usage --wait`/`run` pause counts and the `repository_cap`
rollover over a real run are covered by unit tests only; no notifications for the Manager or Workflow
repositories.

## Next action

Technical approval is recorded; the item waits at
`AWAITING_FUNCTIONAL_REVIEW` for the manual functional review above.
