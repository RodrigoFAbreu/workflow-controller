# ADR 0011: The usage budget: reservations, a ledger and a gate before every job

Status: accepted (2026-10-10). See
`docs/ai-workflow/CONTROLLER_USAGE_BUDGET_PLAN.md` for the full design
record (work item `workflow-controller-usage-budget`, `docs/ROADMAP.md`
step C8, section 11.6). The code ships in 1.8.0. This document records
how the Controller reads the Claude and Codex usage windows, decides
whether a job fits, shares that decision between lanes, and accounts for
what a job cost. It adds one exit code, `17`, which is also in the table
of [ADR 0001](0001-controller-generation-1-architecture.md#exit-codes),
the normative exit-code contract. The operator's view is in
[the `usage` command](../guide/commands.md#usage),
[the settings](../guide/runtime.md#what-it-holds) and
[run](../run.md#pausing-for-usage-limits).

## Context

The lanes and the kanban runner (C11) share one Claude plan, with a
five-hour and a weekly window, and one Codex allowance. Until 1.8.0 two
shell scripts outside the Controller read the newest worker streams and
paused at a fixed 85% between steps. They had no forecast, so a long step
could reach the limit half way. They had no view of the other lanes'
running work, so two lanes could pass on the same headroom. They had no
Codex budget, and workers started outside the Controller were not counted.

## Decisions

1. **One leaf module and one shared record.** `controller/usage.py` holds
   the readers, the record, the window arithmetic, the forecast, admission
   and the pure decision. It imports `runtime`, `errors` (for the unreadable-record error)
   and the standard library, never `job`, `observe` or `cli`, which import it. Every path is a
   parameter, so no test reads the real home or runtime root. The record is
   `<runtime root>/usage.json`, written whole under `runtime.usage_lock`, so
   every change is all-or-nothing. All lanes and repositories share a runtime
   root, so they share the record.

2. **Readings.** Claude's windows come from the `rate_limit_event`s in a
   worker stream (`utilization` is a 0-1 fraction); Codex's from the last
   `rate_limits` object in its newest session files (`primary`, 300 minutes,
   is the five-hour window; `secondary`, 10080, the weekly one). A
   `rejected` status counts as 100% for the window it names and no other.
   A reading whose reset time is past is 0%, because that window has reset.
   Two readings are the same window when their reset times differ by at
   most 120 seconds, which the real samples need. For one window the higher
   percent wins (usage in a window never falls), and otherwise the later
   window wins, so a delayed writer cannot lower a reading.

3. **The decision is pure and strictest-wins.** A job is held when the
   five-hour reading, the live reservations and the job's forecast reach
   the five-hour threshold; when the same holds for the weekly window; when
   the run's spend plus outstanding work plus the forecast passes the run
   cap; or when the repository's does so for the repository cap. The caps
   count outstanding liabilities, not only completed spend, so two workers
   cannot each pass on one cap's headroom. **A cap never has a resume time
   and dominates every timed hold**, so a run is never left sleeping on a
   window while a cap would stop it afterwards. A missing reading disables
   only its own account check, never a cap or the other window: failing open
   on missing data is deliberate, since a fresh machine must run.

4. **Admission reserves.** Under the lock, `usage.admit` re-bases the
   reservations whose window has reset, moves expired ones to `lapsed`,
   evaluates the decision against everyone else's live reservations, and,
   on a go, writes its own reservation with the forecast of each window as
   an independent component. Reservations are what keep one lane's share
   from being spent by another. A reset **rolls a live reservation over**
   into the new window (the forecast is held again there) rather than
   releasing it, because a job that is still running consumes there too.

5. **A reservation is a lease.** A running job renews it on a timer owned
   by `job.py`, since the worker-state flush fires only when the worker's
   signature changes and a long quiet turn produces none. One whose lease
   lapses stops counting against the account windows (its holder is
   presumed gone) but **still counts against the run and repository caps**
   until it is settled, or abandoned after seven days and charged at its
   forecast. Renewing a lapsed reservation revives it. Nothing is ever free.

6. **The ledger forecasts; separate totals cap.** Each completion adds a
   ledger entry (1000 entries, 30 days) from which the forecast for a
   `(provider, role, model)` is the mean of the most recent jobs, with the
   settings' defaults before there are any. The ledger is for forecasts and
   audit only. The authoritative run and repository totals are separate, and
   a set of settled tokens (90 days) makes completion idempotent, so
   evicting a ledger entry can never reopen a cap. A job's delta is
   `measured` when start and end are readings of the same window and the end
   is newer; `after_reset` when a reset lies between them; and `unknown`
   otherwise, which is charged at least the forecast and kept out of the
   forecast. Repository spend is window-scoped; run spend survives resets.

7. **A single charging authority and crash-safe accounting.** A charge is
   made only against a reservation or lapsed entry still in the record. A
   job record's `usage` block alone, an abandoned token or a pruned
   `settled` entry never charges. The `COMPLETED` write that reads the
   stream carries the end figures through every later terminal write, and a
   sweep, run at the start of every step and twice in `resume`, settles any
   terminal record not yet accounted. A record that never launched is
   released without a charge; one that may have run is charged. A crash at
   any point charges exactly once.

8. **Where the gate runs.** In `job.execute_step`, after the route is
   resolved and before the job record exists, so a hold leaves nothing for
   `resume` to reconcile. A hold never waits inside `execute_step`: hours
   later the repository may have changed. `run` waits, with no lock held,
   until the reset plus `usage.resume_grace_seconds`, then goes through the
   whole boundary again (handoff detection, fresh state, a fresh decision, a
   fresh admission), and the wait costs no step. `step`, a wait longer than
   `usage.max_wait_seconds`, and a cap exit `17` with nothing started.
   `usage_resumed` is recorded only after a fresh admission succeeds.

9. **Workers outside the Controller use the same gate.** `workflow-controller
   usage --wait --reserve` and `usage --release` give a manual worker the
   forecast and reservation a Controller job has, with its role, model,
   repository and run named explicitly. A cap it is not told about is not
   evaluated, and the output says so.

10. **Events are the notification contract.** `usage_paused`,
    `usage_resumed`, `usage_released` and `usage_reservation_expired` are
    appended to `usage-events.jsonl`, and run waits also write the run's own
    log. Delivering them as notifications is C5.

11. **Settings.** Sixteen `usage.*` rows (settings table generation 4), all
    additive, Claude and Codex separate at every level. `--usage-cap`
    overrides the run cap on `run`, `step` and `usage`, and
    `--usage-codex-cap` on `usage` only, because the Controller launches no
    Codex worker yet.

## What was left out, and who owns it

- **C8b**: recognising a usage-limit `result` in a worker stream,
  classifying the job distinctly, waiting for the stream's reset and having
  `resume` accept it. Today such a job fails with exit 30. It changes
  `controller/job.py` supervision and the reconciliation contract.
- **C8c**: a per-repository cap map and per-lane budget files. Here a
  repository cap is one number, set in that repository's settings file.
- **C7**: a Codex worker started by the Controller must call the same gate
  (`usage.admit`, `usage.complete`). Until then Codex is enforced only
  through `usage`.
- **C5**: notifications for the events above, with per-event settings.

## Consequences

- A run no longer starts a job that would not fit, and lanes cannot both
  spend the same headroom. The price is a shared file every job touches, so
  deleting `usage.json` forgets reservations and spend (the guide says so).
- The budget forecasts; it cannot stop a job that exceeds its forecast. That
  is C8b's to recover.
- Older releases ignore the new settings rows with their unknown-key
  warning, and a 1.7.x `settings clean` refuses the filled file.
