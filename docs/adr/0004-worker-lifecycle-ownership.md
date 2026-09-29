# ADR 0004: Worker lifecycle ownership

Status: accepted. See
`docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md` for the
full design record (work item `workflow-controller-worker-lifecycle-ownership`,
`docs/ROADMAP.md` section 1.4). This document records the ownership model,
the invariants it keeps, the harness limitations it documents rather than
solves, and the alternatives it rejected. It adds no exit code: the table in
[ADR 0001](0001-controller-generation-1-architecture.md) stays the normative
exit-code contract. A drain detach and an owned-work hold reuse exit `45`
(`OwnedWorkDetachedError` is a `LifecycleWorkerActiveError`). The job-status
enumeration (ten statuses) and the four worker outcomes are unchanged, and
`controller/GENERATION.json` stays at 1.

## Context

Up to 1.2.x the Controller treated "the `claude` process returned control"
as "the lifecycle action finished". Workers ran in print mode
(`claude -p "<task>" < /dev/null`). Under unattended operation they
routinely started long verification in the background, said they would
continue when it finished, and ended their turn. The harness then killed
the background task and exited 0. Every `AMBIGUOUS` lifecycle job in the
runtime root on 2026-09-25 had that shape, across `IMPLEMENTING`,
`SELF_REVIEWING_IMPLEMENTATION` and `APPLYING_REVIEW_FEEDBACK`. A different
job (`2857a730`) legitimately ran two turns, committed its checkpoint in the
second, and was still failed, because the stream reader required exactly
one `result`.

Tracing the lifecycle found nine contributors (plan D1-D9). The central
ones: print mode ends the session at the first idle turn (D1); the
outcome was decided before owned work ended (D3); descendant ownership was
process-group-only, while the harness puts each background task in its own
session and orphans survive its `killed` status (D4); the lifecycle lock
was assumed to follow descendants, and the harness's tool processes do not
inherit it (D5); so `run` could start checkpoint N+1 while N's suite still
ran (D6), and recovery could neither see nor keep a waiting job (D7).

The fix is in the ownership model. It adds no special case for any phase.

## Decisions

### The ownership model

```text
LAUNCHED job, worker_state:
  STARTING -> RUNNING --turn ends, owned work remains--> WAITING --owned work completes--> RUNNING
                 |
                 +--turn ends, nothing owned remains (quiescent)--> ENDING --> DRAINING --> ENDED
ENDED -> COMPLETED -> FINISHED | FAILED    (reconciliation happens only here)
```

- **Streaming input replaces print mode for every worker, with no dual
  path.** The task is written to stdin as one stream-json user message and
  stdin stays open. With stdin open the harness keeps background work alive
  and resumes the session itself, as a new turn, when the work completes.
  The Controller never relaunches, resumes or forks a session. A print-mode
  fallback would have kept D1 alive under a flag.
- **The stream state machine** (`controller/worker_stream.py`) is pure: its
  state is a function of the stream's complete lines and three supervisor
  facts, and no clock enters it. It tracks turns, every `result`, owned
  background tasks (a task is open exactly while the latest
  `background_tasks_changed` list names it and it has no terminal status),
  scheduled wakeups and `command_lifecycle` brackets. A worker is
  *quiescent* when no turn is open, at least one `result` exists, nothing is
  owned and no turn is queued. Terminal classification applies nine ordered
  rows, supervisor-attributed causes before stream-structural ones, and
  returns a structured `stream_diagnosis`.
- **Supervision** (`worker.launch`) ends the session only at a quiescent
  terminal turn, then drains owned processes, then classifies. The job
  record stays `LAUNCHED` throughout, with `worker_state` a separate closed
  enumeration, and every state change is flushed before it is relied on.
- **Same-phase durable progress** is unchanged in substance: an
  `IMPLEMENTING -> IMPLEMENTING` job verifies exactly when a checkpoint is
  newly `COMPLETE` in the state committed at a moved `HEAD`. It now runs only
  for an ended worker, through one shared helper, and reports a
  `predicate_detail` on failure. No phase set changed.

### Invariants

- **I1 -- one worker, one session, one process.** A job launches at most one
  `claude` process; `--resume`, `--continue`, `--session-id` and
  `--fork-session` are never passed; re-attaching never launches anything.
- **I2 -- turn end is not job end.** `worker_outcome` is decided only at
  `ENDED`: a quiescent terminal turn, the process exited, every owned process
  gone. Verification never runs earlier.
- **I3 -- ownership is recorded before it is relied on.** The worker, the
  stdin anchor, the ownership tag and observed descendants are flushed to the
  job record before any wait begins.
- **I4 -- the lock lives as long as the owned work.** The lifecycle lock is
  held by the Controller, the worker and the stdin anchor. The anchor gives
  up stdin at `ENDING` and is ended only after the drain. What the anchor
  cannot see (an untagged process owned only by adoption) is held by the
  non-terminal record's `owned_processes`, which the ownership hold checks.
  With no supervisor attached, the anchor ends itself once nothing it can
  see is owned, so it can never strand the lock.
- **I5 -- owned work is ended only on an explicit operator decision**
  (`--timeout`, or ending processes before `resume --abandon`), never on
  elapsed time or silence. The exceptions are the two harness-contract
  breaches below, which fail closed. The drain bound ends nothing.
- **I6 -- no later action while owned work is alive.** `execute_step` refuses
  while any job for the target is non-terminal, and `resume` never
  reconciles a job whose worker or owned processes are alive.
- **I7 -- fail closed on anything undecidable.** An unreadable `/proc`, a
  stream that breaks the pinned contract, a lost anchor, or owned work the
  harness killed leave the outcome non-verifying.
- **I8 -- closed enumerations stay closed.** Ten job statuses, four worker
  outcomes; `worker_state` is new and separately closed.
- **I9 -- a record without `worker_state`** (Controller 1.2.x or earlier) is
  reconciled exactly as before and never re-attached.
- **I10 -- no Workflow edits.** Everything is Controller-side, proven against
  the installed Workflow 2.5.1.
- **I11 -- a wakeup stops being owned only on positive evidence**: a
  count-checked stop, or a settle window after a regular bracket matched it.
  A `result`'s `origin`, present or absent, is never evidence.

### The stdin anchor

The worker's stdin is a pipe whose write end is held by a Controller-spawned
anchor process (`controller/anchor.py`, stdlib-only, run with
`sys.executable -I -c <source>` so it does not depend on the install
layout). It also holds a copy of the lifecycle lock, runs with an empty
environment (so no ownership tag, its own or an outer job's, owns it), and
ends itself after `ANCHOR_ORPHAN_SECONDS` (60 s) with its worker gone, no
tagged process alive other than a recognised daemon (the daemon policy
below; the anchor applies the same `RECOGNISED_DAEMONS` list) and no
supervisor attached. It has two separately
ended roles: closing stdin at `ENDING` (on `SIGUSR1`), and holding the lock
until the Controller ends it after the drain.

Rejected: giving the worker's stdin write end to the Controller and ending
the session by signal. A Controller loss would then close stdin and kill the
owned work (the harness's end-of-input behaviour, P1), and a session ended
by signal exits as a signal death, which the classification's first row
would have needed a special case for.

### Owned processes and the daemon policy

A process is owned when it is a member of the worker's process group, a
same-uid process whose `/proc/<pid>/environ` carries the job's tag in
`WORKFLOW_CONTROLLER_OWNERSHIP` (a `:`-separated list, so nested
Controllers stay owned by the outer job), a child adopted by the
supervising Controller (`PR_SET_CHILD_SUBREAPER`, best effort), or a
recorded `owned_processes` entry whose pid and start ticks still match.
Once owned, always owned while it lives, whichever Controller supervises.
The Controller never trusts the harness's `killed` status (P2), reaps only
its own adopted zombies by specific pid, and keeps the recorded list pruned
and bounded (one write per scan at most; history as a count plus a sample
of 20).

Amendment (1.4.1, 2026-09-29, `workflow-controller-child-process-reaping`):
1.4.0 recorded adopted children only when an ownership scan ran and reaped
them only when the job drained or ended, so a worker that orphaned many
short-lived processes while `RUNNING` filled the per-user process limit with
zombies. The Controller now
records and reaps its adopted children on every supervision tick, in every
state, once more after it gives up the subreaper, and between launches
until none is left. It still reaps only by specific pid, never a child it
spawned in its own session, and collecting a zombie is still not owning it.
The decision itself is unchanged.

**Daemon policy** (plan decision 9). Tool daemons a worker starts meet the
tag and adoption tests by construction, and later jobs reuse them. A closed
tuple of command-line patterns, `worker.RECOGNISED_DAEMONS`, is never owned:
an `argv[0]` basename of `gpg-agent`, `keyboxd`, `dirmngr`, `scdaemon` or
`ssh-agent`, or an argument `fsmonitor--daemon`,
`org.gradle.launcher.daemon.bootstrap.GradleDaemon` or
`org.jetbrains.kotlin.daemon.KotlinCompileDaemon`. Each one seen is recorded
in `worker_state.excluded_processes`.

**Drain bound.** Every other owned process that outlives `claude` is waited
for up to `DRAIN_DETACH_SECONDS` (10800 s). Then the Controller detaches: it
ends nothing, leaves the record `LAUNCHED` at `DRAINING` with
`drain_detached_at`, leaves the anchor holding the lock, and exits 45 naming
the pids and `resume`. The bound was 600 s until amendment 1 of
`workflow-controller-adaptive-test-sharding` raised it to 3 hours as an
interim constant, because legitimate background verification outlived it;
making it configurable is deferred (`docs/ROADMAP.md` 1.4).

Rejected: ownership limited to processes still attached to the task tree or
group, which gives up exactly the `setsid` escapees D4 is about; and no
bound, where one unrecognised daemon makes a Controller wait indefinitely.
This narrows "descendants stay owned" for recognised daemons only, which
are shared infrastructure, not the pending action's work.

### Disallowed tools and the worker lifecycle note

`CronCreate`, `CronDelete` and `RemoteTrigger` are disallowed for every
worker (`routing.ASYNC_UNOWNABLE_TOOLS`): a recurring or remote schedule
has no point at which the owned work is finished.

Both `ScheduleWakeup` uses in the real job records were fallbacks "in case
the notification never arrives". Every worker therefore gets one fixed
system note, `worker.WORKER_LIFECYCLE_NOTE`, via `--append-system-prompt`:
the Controller delivers every notification, so do not schedule fallback
wakeups, and cancel any wakeup no longer needed before ending. A worker that
ignores it is still correct, only slower. Rejected: a task addendum, which
would change the task string that slash-command expansion and every
task-keyed test depend on.

### The wakeup-fire recogniser (decision 12)

The harness emits no task event for a wakeup (P5). Scheduling is recognised
by tool name, the one tool-specific rule in `worker_stream`. The capture
that was meant to confirm revision 7's recogniser (`result.origin.kind`)
disproved it: a fire turn's `result` carries no `origin` at all. That was
plan amendment 0.

The measured evidence (P11, `claude` 2.1.282): both captured fires, under
different prompts, are each enclosed in a `command_lifecycle`
`started`/`completed` pair sharing one fresh `command_uuid`, with exactly one
turn between them; no other turn in the 18 fixtures is (task completions,
Monitor events and timeouts, subagent hand-backs, the Controller's first
turn, slash commands, a cancelled wakeup), and none of the 80 print-mode
`worker.stdout` files in the runtime root holds a lifecycle event. The pair
is used only as a conjunctive, fail-closed recogniser: a bracket matches a
wakeup only when it is *regular* (never seen before, no turn or other
bracket open at `started`, exactly one non-first turn inside, its open time
known) *and* that turn opens no earlier than a `pending` wakeup's due time
less `WAKEUP_SKEW_SECONDS` (5 s). It matches the earliest-due such wakeup, at
`completed(X)`. Any break is a sticky anomaly, and the run is `AMBIGUOUS`.

Rejected, each against the fixtures: `result.origin`/`origin.kind` (absent
on fires, and absent on the Controller's first turn too, so absence is not
positive); the fire turn's opening `user` event carrying the prompt (there is
none; the prompt occurs only in the scheduling `tool_use`); "any turn after
the due time with no `task-notification` origin" (inference from absence,
which would accept unmeasured turn kinds); the bracket alone, with no
due-time condition (it would silently match on any future non-fire bracket).

### A matched wakeup stays owned until it settles (decision 13)

No field identifies *which* wakeup a fire belongs to: the fire carries no
prompt, and neither the scheduling `tool_use` id nor `scheduledFor` occurs in
the fire's own bracket. So a match is B's best judgement, not proof, and a
regular bracket around some other turn would match in the same way. A
matched wakeup is therefore `fire_matched`, still owned work, and settles
only by:

- a successful `ScheduleWakeup {stop: true}`, which settles every wakeup.
  Its harness-reported `cancelledWakeups` must equal the Controller's
  expected count, else `wakeup_count_mismatch` (`AMBIGUOUS`). A stop issued
  inside a fire turn fixes that bracket's match provisionally and leaves the
  firing wakeup out of the count. P9 measured the count for an ordinary
  stop (1 with one pending); CP1's P12 captures measured it inside a fire
  turn before any code relied on it: 0 for a fire that stops its own
  wakeup, 1 when the fire first schedules a second wakeup;
- `WAKEUP_SETTLE_SECONDS` (305 s = the overdue grace plus the skew) of idle
  supervisor time after `completed(X)`, idle meaning no turn, task or
  bracket open, with no bracket arriving. If the match was wrong, the real
  fire is owed within the overdue grace of that same idle time, so it
  arrives while the session is open and is `unmatched_bracket`.

The window ends nothing on its own; the session still ends only at a
quiescent turn. Its cost is latency: a worker that lets a wakeup fire and
ends without a stop waits up to 305 s of idle time. Rejected: a stronger
positive correlation (no such field exists in the capture); settling only
by a stop (every such worker would reach the overdue rule and fail,
turning "correct, only slower" into a failed job); failing closed on every
unconfirmed match (the same outcome); and writing a message to the worker's
stdin to make it stop (a Controller-authored turn in the worker's
conversation; the task line and the system note are its only inputs by
design).

### Time-based ends (decision 5)

The Controller ends a session on time in exactly two cases, both
harness-contract breaches, both fail closed (`AMBIGUOUS`, row 4): a
`pending` wakeup `WAKEUP_GRACE_SECONDS` (300 s) past due over task-free idle
time (`wakeup_not_delivered`; P5 measured 26 s of lateness), and a
`command_lifecycle` bracket stalled `COMMAND_LIFECYCLE_GRACE_SECONDS`
(300 s) with no turn inside it and no task open
(`command_lifecycle_unterminated`). The stream carries no timestamps on
lifecycle events, so these are supervisor facts, flushed with `ENDING`. The
alternative is to wait forever on a harness that never delivers.
`--timeout` stays an explicit operator budget, now over the whole owned
lifetime.

### Restart recovery (decisions 3 and 6)

Each supervising Controller holds `jobs/<job_id>/supervisor.lock`
(`O_CLOEXEC`, dies with it). Because the anchor holds the lifecycle lock by
design, `resume` and `resume --abandon` are two-phase: supervision and
anchor disposal first, under the supervisor lock and without the lifecycle
lock; then reconciliation under the lifecycle lock, unchanged. `resume`
re-attaches by default: it replays `worker.stdout` from the start with the
persisted supervisor facts, and continues supervision. Lost timers (settle
windows, stall timers) restart at re-attach, so a re-attached supervisor may
wait longer, never shorter. `run` and `step` never re-attach.

A re-attached supervisor is not the worker's parent and cannot read its exit
status. A session a supervisor ended at a quiescent turn (a persisted
`ending_offset`) is classified from the stream alone, with
`exit_status_known: false`; any other disappearance is `AMBIGUOUS`. Always
`AMBIGUOUS` after a re-attach would have defeated recovery for the common
case.

Rejected: a new job status for waiting or draining. It would ripple through
about twenty closed tables and their tests for no behavioural gain; the
states live in `worker_state` inside `LAUNCHED`.

## Harness limitations

These need a future Harness Adapter Protocol, or OS-level containment, to
close. Each is pinned by a fixture in `tests/harness_contract/` or by the
opt-in live contract probe (`CONTROLLER_LIVE_WORKER=1`,
`LiveHarnessContractProbeTest`), so a harness change surfaces as a failing
contract test, not as a silent behaviour change.

- **H1 -- the model depends on streaming input.** Only `--input-format
  stream-json` with stdin held open keeps a session alive across background
  work (P1 vs P3). This is measured, not documented by the harness.
- **H2 -- wakeups are invisible to the task stream.** A fire is recognised
  only by the undocumented `command_lifecycle` bracket, and matched to a
  wakeup by time. The failure modes are not symmetric:
  - if the harness drops, renames or reshapes the pair, fires stop matching,
    each pending wakeup reaches the overdue rule, and the run fails closed;
    a malformed lifecycle event fails it closed at once;
  - if the harness starts bracketing another turn kind, a bracket matching
    no due wakeup is `unmatched_bracket`. One whose turn opens after a
    pending wakeup's due time does match it, but only as `fire_matched`: the
    real fire arrives inside the settle window and is `unmatched_bracket`,
    or a stop exposes the wrong match as `wakeup_count_mismatch`;
  - **the double-breach residue.** A wrong match is missed only when the
    harness *both* brackets a non-fire turn after a due wakeup *and* then
    delays that wakeup's real fire past the grace of task-free idle time,
    with no stop in between. The second is the very breach the overdue rule
    declares, but nothing is left pending to declare it on. Both halves are
    unmeasured. This is the only way an unrelated bracket can lead to
    `ENDING` before a real fire; CP3 pins it as a documented residue;
  - two wakeups pending at once were not measured. If the harness fires only
    the later one, that fire matches the earlier, the later reaches the
    overdue rule, and a stop meanwhile reports a disagreeing count;
  - a wakeup scheduled by a renamed tool, or some other way, is not seen at
    all: nothing is owned, and the wakeup may be lost when the session ends.
    Only the pinned tool name and the live probe guard this. Reconciliation
    is not a substitute: in `IMPLEMENTING -> IMPLEMENTING`, a worker ended
    before an unseen wakeup is judged on durable state like any other.
- **H3 -- recurring and remote schedules** cannot be owned to termination,
  so they are disallowed.
- **H4 -- `killed` does not mean gone** (P2). The Controller scans for
  itself.
- **H5 -- escape residue.** A descendant that both scrubs its environment
  (`env -i`) and is reparented where no supervising Controller's subreaper
  adopts it is not owned. That happens in two windows: while no Controller
  supervises, and while a re-attached `resume` supervises (it is not the
  worker's ancestor, so its subreaper adopts nothing). While the original
  Controller supervises, its subreaper adopts such a child, which is
  recorded in `owned_processes` when first seen and stays owned through that
  record entry after the Controller exits (a drain detach or Ctrl-C). An
  escapee orphaned between two ownership scans that exits or escapes before
  the next is never recorded. A per-job cgroup or systemd scope would close
  this, and is out of scope.
- **H6 -- tool processes inherit no extra descriptors** (P2), so the lock
  cannot follow descendants; the anchor carries it instead.
- **H7 -- ordering.** Print mode can write one turn's `result` after the next
  turn's events, so the classification never pairs a `result` with its
  turn. The bracket's own order (`started`, one turn, `completed`) is relied
  on, and a bracket out of order is irregular. One known false-`AMBIGUOUS`
  source: a `task-notification` turn opened between a fire's `result` and its
  `completed(X)` puts two turns in the bracket (`multiple_bracketed_turns`);
  P11 measured that gap at 0 ms.
- **H8 -- non-dumpable descendants.** A tagged process whose `environ` is
  unreadable (`PR_SET_DUMPABLE` 0, as `ssh-agent` and `gpg-agent` do) is not
  owned by tag; it stays owned while in the worker's group or adopted.
- **H9 -- daemon recognition is by name.** A daemon the list does not name
  holds the job until the drain bound detaches; a process imitating a listed
  name is not waited for. Extending the list is a Controller change with its
  own test.

## Consequences

- A worker can run long verification in the background and continue when
  it completes; the job ends when the work does, and `run` can no longer
  start the next action while the previous one's processes run.
- A lost or interrupted Controller costs nothing but a `resume`: the anchor
  keeps the session and the lock, and `resume` re-attaches.
- Each job has one more Controller-owned process (the anchor), and the job
  record carries `worker_state`, `worker_anchor`, `ownership_tag` and a
  `stream_diagnosis`. Records from earlier versions take the old paths.
- Upgrade the Controller between jobs, not during one.
- Two latencies are accepted: a worker that lets a fallback wakeup fire
  runs one more turn, and a worker that ends after a fire without a stop
  waits up to 305 s of idle time.
- The recogniser rests on two measured fires on one harness version. Any
  change the opt-in live probe detects in the bracket contract is a plan
  amendment, not a silent re-baseline.
- Process containment (cgroups), a Harness Adapter Protocol and
  multi-harness support remain future work (`docs/ROADMAP.md`).
