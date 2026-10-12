# ADR 0012: SignalHub notifications: a closed event table, idempotent and off the lifecycle's thread

Status: accepted (2026-10-12). See
`docs/ai-workflow/CONTROLLER_SIGNALHUB_NOTIFICATIONS_PLAN.md` for the full
design record (work item `workflow-controller-signalhub-notifications`,
`docs/ROADMAP.md` step C5, section 11.4). The code ships in 1.9.0. This
document records what the Controller notifies, how an event is identified
so that a retry or a `resume` never notifies twice, why delivery is
asynchronous with no outbox, and where the credentials come from. The
operator's view is [the `notifications` command](../guide/commands.md#notifications),
[the settings](../guide/runtime.md#what-it-holds),
[unattended runs](../guide/automation.md#notifications-from-an-unattended-run)
and [the troubleshooting page](../common-problems.md#no-notifications-arrive).

## Context

Once runs are unattended the operator must learn what is happening, and when
something is blocked, without watching a terminal. Until 1.9.0 the lane
orchestrator sessions did it by hand, through a script that reads
`~/.config/signalhub/events.json` and posts to SignalHub. SignalHub is the
owner's notification platform and accepts events from any producer over
`POST /api/v1/events`. The Controller already records everything worth
announcing: run-log lines, job records, the branch-binding events a step's
preflight writes and the lines of `usage-events.jsonl`.

## Decisions

1. **One leaf module.** `controller/notify.py` holds the event table, the
   per-event resolution, the idempotency keys, the credential lookup, the
   sender and the outcome log. It imports `runtime`, `errors` and the
   standard library, never `settings` (which imports it), `usage`, `job`,
   `observe` or `cli`. Every path, clock and transport is a parameter. A
   process-wide notifier is installed once in `cli.main`; with none
   installed, as in a test that does not install one, `emit` does nothing.
   The Controller calls the HTTP API with `urllib` directly: no dependency
   is added.

2. **A closed event table, derived from records the Controller already
   keeps.** Each event type has a kind (`state`, `info`, `gate`, `blocker`)
   and one source, so no event has two. The names reuse the lanes' own.

   | event type | kind | when |
   |---|---|---|
   | `step_done` | state | a job finished |
   | `usage_paused`, `usage_resumed` | state | the usage budget held a job, and released the hold |
   | `usage_released`, `usage_reservation_expired` | state | **off by default**: `usage_released` is written for every job |
   | `run_finished` | state | a run closed; not sent for exit 16 (`--max-steps`) |
   | `milestone_started`, `milestone_planned`, `approval_recorded`, `milestone_accepted` | info | the matching lifecycle transition, read from the job's validated states |
   | `pr_merged` | info | a person merged on GitHub, or the Controller's squash merge was seen to land |
   | `release_published` | info | the auto-merge release wait closed out a release |
   | `user_gate` | gate | a human gate, or a merge the policy leaves to a person |
   | `lane_blocked` | blocker | a failed or incomplete job, a fail-closed refusal (exit 20) of `step`, `run` or `resume`, and every branch gate that needs a person to fix something |

   A held lock (45), a pending handoff (50), `--max-steps` (16) and a
   declined action (15) are a run's normal ends and are not notifications.
   `merge_accepted` only means the Controller's own merge call was accepted,
   so it sends nothing: the later `merged_squashed` does. A branch gate that
   is only a wait (`checks_pending`, `merge_pending`, `release_pending`,
   `pr_head_not_accepted`) sends
   nothing, unless a run that waited ends on one when its wait is spent. No
   read-only command emits.

3. **Every event type is configurable.** The `notifications.*` settings
   hold the master switch, the server address, the path of a key file and
   the delivery bounds, and `notifications.events` holds a `kinds` layer and
   an `events` layer of `{enabled, category, severity}`. Resolution is, field
   by field: the built-in default of the kind, the file's kind entry, then the
   file's event entry. The defaults are the mapping the lanes used by hand:
   `state` INFO/LOW, `info` INFO/NORMAL, `gate` ACTION_REQUIRED/HIGH and
   `blocker` BLOCKED/CRITICAL. A known field with a bad value is refused
   (exit 20); an unknown event name or field is ignored with the usual
   unknown-key warning, and a field that starts with `_` silently, so an
   `events.json` object can be pasted in whole. The Controller does not read
   `events.json`.

4. **Idempotency: the identity is the occurrence, and the body is a function
   of the identity.** SignalHub keeps an `Idempotency-Key` for as long as its
   event exists and answers `200` with no push when the stored fields are
   identical, but `422` when a different body arrives under a used key. So
   the key is `wc-<event type>-<repository>-<identity>`, where the identity
   names the thing notified and changes when it recurs: a job id for a failed
   job, the run id for `run_finished`, the work item and pull request for
   `pr_merged`, the release tag for `release_published`, for `usage_paused`
   the provider, window, repository and the hold's occurrence (plus the run
   for a `run_cap`), never the line's `at` or `reason`, a hash of the line's
   own fields for the other usage events, and for a branch gate the work item,
   binding generation, the pull request head or the binding's latest event
   time, and a streak counter for gates (red checks) that nothing else tells
   apart. The title, message, metadata and link carry no job id, run id,
   clock or wait deadline, so an event derived again by another process
   repeats the stored body byte for byte and gets `200`. A `422` is recorded
   as `conflict`, not as a duplicate, and is never retried.

   Five bounded residuals of the usage and recovery keys are accepted and
   named here: (a) more than 50 foreign `usage-events.jsonl` lines between
   two pauses of one hold start a new occurrence chain, so one extra
   `usage_paused` is sent; (b) a repository cap with no reading whose
   accounting window is re-placed by a later charge sends one extra notice per
   move; (c) a reservations-only cap pause less than five hours after a
   matching pause, with no `usage_resumed` between, sends no notice, so the
   promise of one `usage_paused` and one `usage_resumed` per hold is
   qualified accordingly; (d) two processes appending at once may each record
   their own occurrence, which sends one extra notice; (e) an event type that
   was disabled when its record was written and is enabled within 30 minutes
   sends one late notice on `resume`.

   The repository in the key is the directory name, which is also what the
   body's `context` carries. Two checkouts whose directory names are the same,
   or sanitise to the same text, share keys and contexts. That is a named
   limitation, not a repository identity; a path hash would make the keys of
   one repository differ between its checkouts.

5. **Delivery is off the lifecycle's thread.** `emit` builds the body and puts
   it on a bounded queue (256) and returns. One daemon thread sends one event
   at a time, with a timeout per attempt and a bounded number of attempts
   with exponential waits. Network errors, timeouts, `429` and `5xx` are
   retried; any other `4xx` is a rejection. A circuit breaker stops
   attempting for 300 seconds after three events in a row spend their
   attempts. The run's close flushes the queue for at most the timeout plus
   five seconds, shared by every flush in the process, and after a Ctrl-C the
   flush waits at most two seconds. Delivery can therefore never block, fail,
   delay or change a step.

6. **Where outcomes are recorded.** One line per event outcome (`sent`,
   `duplicate`, `conflict`, `rejected`, `failed`, `skipped`, `dropped`,
   `not_configured`, `disabled`) goes to `runs/<run_id>/notifications.jsonl`
   (outside a run, `<runtime root>/notifications.jsonl`), each with the
   event's key and never the API key. A run with a configured notifier starts
   its log with a `notifier_active` line, and `RunRecord.close` adds a
   `notifications_summary` event with the counts to the run's own
   `events.jsonl`. These files are presentation history like the rest of
   `runs/`: nothing in the lifecycle reads them, except the recovery below.

7. **No outbox.** A persistent outbox that replays what failed to send would
   add a second durable record that could disagree with the first, a replay
   process to own, and a place a notice could outlive its meaning. An event
   whose attempts were spent is `failed`, recorded and not retried. What is
   recovered is a different loss: an event whose authoritative record
   outlived the process that was to send it, and it is recovered by deriving
   it again from that record, never from a spool.

8. **Recovery from the authoritative record.** `resume` derives again the
   notice of a job it reconciled to a terminal state, and of an
   already-terminal job last updated within 30 minutes, when the run's log
   shows a configured notifier and records no outcome that spent attempts or
   that the server holds for that key. `step`, `run` and `resume` derive
   `pr_merged` from a binding already in `MERGED` or `MERGED_SQUASHED` at
   command entry. A branch-gate job record cannot be derived again (it does
   not hold the binding generation, head or streak), and the next `step`
   observes the same gate and sends it under its own key, so it is excluded.
   One loss is accepted and named: if the process dies after a close-out
   persists `CLOSED` and before the `pr_merged` is queued, that one notice
   is lost, because nothing keeps the merge evidence.

9. **Credentials never enter the repository or the settings file.** The URL
   is `$SIGNALHUB_URL`, else `notifications.url`, else the first line of
   `<signalhub config dir>/url`. The key is `$SIGNALHUB_API_KEY`, else the
   file named by `$SIGNALHUB_API_KEY_FILE`, else the file named by
   `notifications.api_key_file` (a path), else `<signalhub config dir>/api-key`.
   The config directory is `$SIGNALHUB_CONFIG_DIR` or `~/.config/signalhub`.
   One Controller process uses one key, whichever repository it drives. The
   key is read once, held in memory and sent as `Authorization: Bearer <key>`.
   The lanes' kill switch file `<signalhub config dir>/disabled` is honoured,
   so one switch silences every producer. Without a URL or a key the notifier
   is inert after one `not_configured` line that names the places it looked.

10. **The context names the repository.** Every body carries `context`
    `<repository>/<work item id>` (or the repository name alone), and
    `metadata.lane` `workflow-controller`, so the Controller's notices group
    with the lanes' for the same work item.

## What is deferred

- **The lane scripts.** After the release the lane orchestrators stop
  hand-sending the events the Controller now sends and keep only what no
  Controller observes (Codex review rounds, their own approvals). Their
  restart path should run `resume` before `run --max-steps 1`, which is what
  repairs a notice lost between a job's record and its queue. A key must also
  be findable on every launch path: set `notifications.api_key_file` once, or
  have `start-run.sh` export `SIGNALHUB_API_KEY_FILE`. That is a lane change
  outside this repository.
- **Other producers.** The Manager and Workflow repositories send nothing.
- **Notifications for C7, C8b and C8c**, which do not exist yet.

## Consequences

- Settings table generation 5 adds the `notifications.*` rows, so the fill
  writes them into an existing settings file with their defaults.
- Recovery on `resume` is the only repair for a lost notice, and the lane
  watchdog hands back without `resume` after a killed process; until the
  lanes change, such a notice is repaired by the next manual `resume`.
- `notifications --test` is the operator's check that the wiring works.
