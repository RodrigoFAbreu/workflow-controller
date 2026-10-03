# Workers: lifecycle, recovery and observation

[Back to the documentation map](../README.md)

How the Controller runs a worker, knows when it has really finished, recovers after
a crash or Ctrl-C, and lets you watch it. Sections: [concurrency and worker
lifecycle](#concurrency-and-worker-lifecycle), [job dispositions](#job-dispositions)
(recovering job records), [observing workers](#observing-workers) and
[telemetry](#telemetry) (what each worker cost).

## Concurrency and worker lifecycle

A worker's lifecycle action is finished only when the worker has really
finished, not when one of its turns ends. Workers routinely start long
verification in the background and end a turn saying they will continue
when it completes. The Controller keeps that session alive, lets the
harness resume it, and reconciles the job only once the worker and every
process it owns have ended. The design record is
[`docs/adr/0004-worker-lifecycle-ownership.md`](../adr/0004-worker-lifecycle-ownership.md).

**At most one lifecycle worker per target worktree.** `step`, `run`,
`resume` and `resume --abandon` take an exclusive `flock` on the target
worktree's own git directory (`git rev-parse --absolute-git-dir`). The
lock writes nothing into the target, and it excludes every Controller on
the machine working on that worktree, whatever runtime root each uses.
The lock is held for the job's whole *owned lifetime*, not only while
the Controller runs: the `claude` process and the job's *stdin anchor*
(below) each hold a copy of its descriptor, and the kernel keeps a
`flock` held while any process holds one. The harness's own tool
processes do **not** inherit it (measured: `claude` starts them with no
descriptor beyond 0-2), so the lock never follows a background command;
the anchor carries it for them. "The previous worker is still alive" is
answered by the kernel and by the job record, never inferred from
silence or elapsed time.

The lock is per worktree, which is stricter than per work item: two work
items in one worktree share its index and working tree. It does not
exclude the same work item driven from two different worktrees by
Controllers with different runtime roots. That residual is documented,
not closed: there, only the Workflow's own checkpoint claims (for
`/milestone-implement`) and the review commands' binding checks stand
between two workers.

### One session per worker, kept open while it owns work

Every worker runs as one `claude` process and one session, from its
task to its end:

```text
claude -p --input-format stream-json --output-format stream-json --verbose \
    --permission-mode <mode> [--model <m>] [--effort <e>] \
    --append-system-prompt <worker lifecycle note> --disallowedTools <list>
```

The task (the slash command) is written to the worker's stdin as one
stream-json user message, and stdin stays open. With stdin open the
harness does not kill background work at the end of a turn: when a
background task, a `Monitor` event, a background subagent or a
`ScheduleWakeup` fire completes, the harness itself starts a new turn in
the same session and delivers the result. The Controller never resumes,
relaunches or forks a session (`--resume`, `--continue`, `--session-id`
and `--fork-session` are never passed), and `resume` never launches one.

The job record stays `LAUNCHED` throughout, and a separate, closed
`worker_state` says where the worker is:

```text
STARTING -> RUNNING --turn ends, owned work remains--> WAITING --owned work completes--> RUNNING
               |
               +--turn ends, nothing owned remains--> ENDING --> DRAINING --> ENDED
```

- **`RUNNING`**: a turn is open.
- **`WAITING`**: no turn is open, and the worker still *owns work*: a
  background task the harness lists and has not finished (background
  Bash, `Monitor`, a background subagent), a scheduled wakeup that has
  not settled (below), or a harness command still open.
- **`ENDING`**: a quiescent terminal turn: no turn open, nothing owned,
  no turn queued. Only here does the Controller end the session, by
  closing the worker's stdin. A notification turn the harness had
  already queued is followed to its end and judged, never assumed clean.
- **`DRAINING`**: `claude` has exited, and processes it owned are still
  alive (below).
- **`ENDED`**: everything is gone. Only now is the worker's outcome
  decided and the job reconciled against durable state, so `run` can
  never start the next action while the previous one's verification
  still runs.

The job record and its events log (`worker_running`, `worker_waiting`,
`worker_ending`, `worker_draining`, `worker_ended`) record each change,
what the worker is waiting on and every owned process, before the
Controller relies on it.

Every worker also gets a fixed system note (`--append-system-prompt`):
the Controller delivers every background notification, so the worker
must not schedule fallback wakeups "in case a notification never
arrives", and should cancel any wakeup it no longer needs
(`ScheduleWakeup` with `stop: true`) before its final turn ends. A
worker that ignores the note is still correct, only slower: its fallback
wakeup is owned work, so the session waits for it to fire, the worker
runs one more turn, and the job ends at the next quiescent turn.

`CronCreate`, `CronDelete` and `RemoteTrigger` are disallowed for every
worker, whatever its route: a recurring or remote schedule never
finishes, so it cannot be owned to its end. `Monitor`, `ScheduleWakeup`,
background Bash and background subagents stay allowed.

### The stdin anchor

The worker's stdin is a pipe whose write end is held by a small
Controller-spawned process, the job's *stdin anchor*, recorded as
`worker_anchor`. It also holds a copy of the lifecycle lock. It runs
with an empty environment, depends on nothing but the Python standard
library, and does two separate things:

- it closes the worker's stdin when the supervising Controller asks, at
  `ENDING`, which is the only normal way a session ends;
- it keeps the lock until the Controller ends it after the drain.

So if the Controller dies or is interrupted, the anchor keeps the
session open and the worktree held, the waiting worker keeps waiting,
and nothing owned is killed. `resume` can then re-attach (below). An
anchor left with nothing to guard ends itself: once its worker and every
process carrying the job's ownership tag have been gone for 60 s, with
no Controller attached, it exits, so it can never hold the lock for
good. An anchor that disappears while its worker waits gives the worker
end-of-input early; the harness then kills the open work, and the run is
classified `AMBIGUOUS` (`stdin_closed_while_waiting`), which fails
closed.

### Owned processes, the daemon list and the drain bound

A process is owned by the job, and the job waits for it, when it is:

- a member of the worker's process group; or
- a same-user process whose environment carries the job's tag in
  `WORKFLOW_CONTROLLER_OWNERSHIP` (a `:`-separated list, so a worker
  running a nested Controller stays owned by the outer job too); or
- a child adopted by the supervising Controller, which marks itself a
  child subreaper for the worker's lifetime; or
- an entry of the record's `worker_state.owned_processes` whose pid and
  start time still match. Once seen as owned, a process stays owned for
  as long as it lives, whichever Controller supervises.

This is what catches a background command that detaches itself
(`setsid`): the harness reports it `killed`, but it keeps running in its
own session, outside the worker's process group. The Controller never
trusts the harness's `killed` status and scans `/proc` itself.

Each owned entry records the process's command line. A process first
seen inside an `execve` reads an empty one, so its entry is recorded
with an empty command line and takes the first non-empty read, once:
a recorded command line is never replaced, and never by an empty read.

**Recognised daemons are never owned.** Long-lived tool daemons are
shared infrastructure that later jobs reuse, so a process whose command
line matches `worker.RECOGNISED_DAEMONS` is not waited for, never ended
by `--timeout` or abandon, and never holds a later job. The list is
closed: an `argv[0]` of `gpg-agent`, `keyboxd`, `dirmngr`, `scdaemon` or
`ssh-agent`, or an argument `fsmonitor--daemon`,
`org.gradle.launcher.daemon.bootstrap.GradleDaemon` or
`org.jetbrains.kotlin.daemon.KotlinCompileDaemon`. Each one seen is
recorded in `worker_state.excluded_processes`, so what was not owned is
on the record. Recognition is by name: a daemon the list does not name
is owned like any other process, and one that imitates a listed name is
not waited for. Extending the list is a Controller change.

**Collecting finished children.** While it supervises a worker, the
Controller collects every finished child it holds as a subreaper on
every tick, in every worker state, each by its own pid (never
`waitpid(-1)`). Collecting a zombie is not owning it: what the job owns
and waits for is decided as above, and the collection publishes nothing.
It collects once more after it gives up the subreaper, and between
launches a background reaper keeps collecting the recorded children that
are still running until none is left, whatever the gap between steps.
It never collects a child it spawned in its own session, so an
embedder's or a callback's ordinary subprocesses keep their exit
statuses. An orphan such a subprocess leaves behind in that session is
collected once the Controller has seen it as that subprocess's
descendant. A child that an embedder starts in a *new* session while a
launch runs in the same process, and whose exit status it reads, must be
spawned through `worker.exclude_from_reaping(spawn)`: enter the block
before the spawn, pass a callable that returns the `Popen`, and wait for
the child inside the block.

**The drain bound.** After `claude` exits, the Controller waits for
the remaining owned processes for at most the drain bound: the
`worker.drain_detach_seconds` setting, 3 hours (10800 s) by default (see
[The settings file](runtime.md#the-settings-file)). If any is still alive
then, it ends **nothing**. It *detaches*: the record stays `LAUNCHED` at
`DRAINING` with `drain_detached_at`, and the bound it applied as
`drain_detach_seconds`. A `worker_drain_detached` event names the
processes, the anchor keeps the lock, and the command exits 45 naming
each pid with its command line. Either run `workflow-controller resume
<repo>`, which re-attaches and drains again with a fresh bound, or end
the processes and then run `resume`. No later action can start while
they run. Every message that prints the bound (the exit-45 message, and
the activity line in `status` and `follow`, in any later invocation)
reads the recorded one, so it shows the bound that was really applied. A
record from before 1.5 has no such field and shows 3 hours.

`--timeout` bounds only the drain of the `step` or `run` that launched
the worker. `resume` takes no `--timeout`, and its re-attach drain has
none, so `resume` may wait in the foreground for up to the drain bound
per call. `resume --drain-timeout SECONDS` sets that bound for this
re-attach only. To stop sooner, end the named pids (the drain then
finishes at once), or interrupt `resume` with Ctrl-C: that ends only the
Controller, nothing it owns, and the job stays `LAUNCHED` at `DRAINING`
for a later `resume`.

### Scheduled wakeups

A `ScheduleWakeup` is owned work from its successful `tool_result`
until it *settles*. The harness emits no task event for a wakeup, so
the Controller recognises the scheduling call by tool name, and a
*fire* by the only evidence the stream carries: every measured fire
turn is enclosed in a `command_lifecycle` `started`/`completed` pair
sharing one `command_uuid`, and no other measured turn is. The stream
never says *which* wakeup a fire belongs to, so a *regular* bracket
(exactly one turn inside it, not overlapping another, opening at or
after a pending wakeup's due time less 5 s) is matched to the
earliest-due such wakeup. The turn's `result` `origin` is never evidence.

A match is not proof, so a matched wakeup is only `fire_matched` and
stays owned. It settles in one of two ways:

- **a successful `ScheduleWakeup {stop: true}`** settles every wakeup
  at once. The harness reports how many it cancelled
  (`cancelledWakeups`), and the Controller compares that with its own
  count of wakeups still scheduled (a stop inside a fire turn leaves out
  the wakeup whose fire is running). A disagreement is the anomaly
  `wakeup_count_mismatch`, and the run is `AMBIGUOUS`;
- **the settle window**: 305 s (`worker.WAKEUP_SETTLE_SECONDS`) of idle
  time after the matching bracket closed, with no turn, task or bracket
  open, and no bracket arriving. This is the cost of a worker that lets
  a wakeup fire and then ends without a stop: it waits up to 305 s of
  idle time before `ENDING`. A worker that follows the system note
  cancels from its fire turn and settles at once.

The window equals the lateness the Controller already accepts for a
fire (300 s) plus the match skew. If a bracket around some other turn
was wrongly matched, the wakeup's real fire is still due at the harness
and arrives inside the window, while the session is still open, and its
bracket then matches nothing (`unmatched_bracket`): the run fails
closed rather than ending before the fire.

The bracket fails closed in every other case too. A bracket that breaks
any of the conditions above, a `command_lifecycle` event with a missing
`command_uuid` or an unknown `state`, a `completed` with no `started`,
and a bracket that matches no due wakeup are each a sticky anomaly
(`stream_diagnosis.command_lifecycle_anomalies`), and the run is
`AMBIGUOUS` (`command_lifecycle_irregular`). If the harness stops
bracketing fires, each wakeup goes overdue instead.

**Double-breach residue.** One sequence is not caught: the harness
brackets some non-fire turn after a wakeup's due time, *and* then
delays that wakeup's real fire by more than 300 s of task-free idle
time, with no stop in between. The second is itself the breach the
overdue rule exists to declare, but by then nothing is left pending to
declare it on, so the wrongly matched wakeup settles and the session
may end before the real fire. Both halves are unmeasured; the opt-in
live probe re-measures the bracket.

A wakeup scheduled some way the Controller does not recognise (a
renamed tool, for example) is not seen at all, so it is not owned and
may be lost when the session ends. The tool name is pinned by the
harness-contract fixtures and the live probe.

### Time is not termination

There is no worker timeout by default: `step` and `run` wait for as
long as the worker owns work. A timeout is an explicit opt-in, over the
whole owned lifetime: `--timeout SECONDS`, or the
`worker.timeout_seconds` setting (`null`, no limit, by default); when it fires, the Controller ends the
worker's process group, every owned process (never a recognised
daemon) and the anchor, reaps them, and classifies the run
`INTERRUPTED`.

The Controller ends a session on time in exactly two cases, both
harness-contract breaches that fail closed (`AMBIGUOUS`):

- a scheduled wakeup still not fired 300 s past its due time, counted
  only while no turn and no task is open (`wakeup_not_delivered`);
- a harness command (`command_lifecycle` bracket) stalled 300 s with no
  turn open inside it and no task open (`command_lifecycle_unterminated`).

The settle window is not a third case: it ends nothing, it only decides
when a matched wakeup stops being owned, and the session still ends
only at a quiescent turn. The drain bound ends nothing either.

### Restart: `resume` re-attaches

A Controller that dies, or is interrupted, leaves the worker and its
anchor running, and the job `LAUNCHED`. Each supervising Controller
holds the job's own supervisor lock, `jobs/<job_id>/supervisor.lock`,
which dies with it. `workflow-controller resume <repo>` works in two
phases:

1. **Supervision, before the lifecycle lock** (which the anchor still
   holds). For a job whose supervisor lock is free, `resume` takes it,
   rebuilds the worker's state by replaying `worker.stdout` from the
   start, and carries on supervising exactly as the original Controller
   would: it waits while the worker owns work, ends the session at a
   quiescent turn, drains owned processes, ends the anchor, and records
   `COMPLETED`. It is not the worker's parent, so it cannot read the
   exit status; a session that a supervisor ended at a quiescent turn is
   then judged from the stream alone, and any other exit is
   `AMBIGUOUS`. Timers the lost Controller held (the settle window, a
   bracket's stall time) restart at re-attach, so a re-attached
   supervisor may wait longer, never shorter. If another Controller
   holds the supervisor lock, `resume` exits 45 naming it.
2. **Reconciliation**, under the lifecycle lock, exactly as before.

`step` and `run` never re-attach: while a job is non-terminal they exit
20 (a pending job) or 45 (the worktree is held), and name `resume`.

**Exit 45** means the worktree is held, and nothing was launched,
reconciled or abandoned. Its message names:

- the lock path, and how to find every holder:
  `fuser -v <git-dir>` or `lsof +d <git-dir>`. A holder that is not a
  recorded worker is that job's recorded stdin anchor, which holds the
  lock for the job's whole owned lifetime, or a member of the worker's
  own process group. The message names a live recorded anchor as "job
  `<id>`'s stdin anchor", with what clears it: `resume` while its worker
  or owned work lives and no Controller is attached, or nothing at all
  once they have ended (it ends itself within 60 s, or `resume` ends it
  now);
- the recorded worker's pid and process group, only when a `LAUNCHED`
  job record's worker is `active` (see [Job dispositions](#job-dispositions)). When the
  recorded leader process itself is running, the message says to wait
  for it, or to end that group (`kill -TERM -- -<pgid>`), then run
  `resume`. When the leader is gone and only other running members of
  the recorded group were found, that group number may have been reused
  since the job started, so the message lists the members and asks you
  to verify them (`ps -o pid,pgid,lstart,args -g <pgid>`) before ending
  the group;
- no process group at all for any other verdict. After a reboot, a
  recorded group number may name an unrelated live group of yours;
- after a drain detach, the owned processes still alive, each with its
  command line.

**The lifecycle lock report.** `explain` and `inspect` print
`lifecycle lock: held`, `free` or `unknown` (and `--json` carries
`lifecycle_lock`). The report never takes the lock, because `flock` has
no non-acquiring test and an acquire-and-release probe could make a
concurrent `step` exit 45 spuriously. It reads `/proc/locks` instead,
matching the git directory by the device and inode the kernel reports
for it. `held` means a holder entry matched. `unknown` means the probe
could not prove the lock free, never that it is free: it could not
establish the lock's device or inode, a `/proc` read failed, or no entry
matched but it runs outside the init pid namespace. In a
container, `/proc/locks` omits a lock whose taker has died, even while an
orphaned worker still holds it, so there `unknown` is the usual answer
when nobody visibly holds the lock. `step` decides by acquiring, never by
this report.

**A lock that cannot be taken** for any reason other than contention is
`LifecycleLockError` (exit 20), naming the path, the operation and the
errno; a git directory Git cannot resolve is
`GitDirectoryUnresolvableError` (exit 20). The Controller never skips
the lock on a worktree that exists. One consequence: a worktree on a
filesystem whose
`flock` emulation refuses an exclusive lock on a read-only directory
descriptor cannot be driven. NFS without `local_lock=flock` or
`local_lock=all` is one (NFS emulates `flock` with byte-range locks,
which need a writable descriptor). Mount it with `local_lock=flock` (the
lock then excludes only Controllers on the same machine), or keep the
worktree on a local filesystem.

**Ctrl-C ends only the Controller.** The worker runs in its own session,
and its anchor in another, so both keep running headless, holding the
lock and the worktree, and a waiting worker keeps waiting. The
Controller does not forward the interrupt: ending the worker is your
decision. On Ctrl-C during a worker, the Controller prints, on stderr
and before the interrupt propagates, the worker's pid and process group,
the anchor's pid with the command that re-attaches, and the `follow`
command. The job record under `<runtime>/jobs/` names them too. Run
`workflow-controller resume <repo>` to re-attach and let the job finish,
or end the worker's group first and then `resume`.

### What is not solved here

These need OS-level containment or a future harness adapter, and are
recorded in ADR 0004:

- the whole model rests on the harness keeping a session open while
  stdin is open (measured, not documented);
- wakeup fires are recognised by an undocumented `command_lifecycle`
  event, matched by time, with the double-breach residue above;
- a descendant that scrubs its environment (`env -i`) and is orphaned
  while no Controller is supervising, or while a re-attached `resume`
  supervises (it is not the worker's ancestor, so its subreaper adopts
  nothing), and was never seen owned, is not owned. A per-job cgroup
  would close this;
- a tagged descendant that makes itself non-dumpable and has left the
  worker's process group is not owned by tag;
- daemon recognition is by name.

## Job dispositions

Every job is recorded under `<runtime>/jobs/<job_id>.json` before its
worker starts (`PLANNED`, then `LAUNCHED`), the worker's process identity
is added as soon as it is spawned, and `resume` never launches a worker.

- **No launch over an unreconciled job.** `step`/`run` refuse
  (`PendingJobReconciliationError`, exit 20) while any job file for the
  target is pending reconciliation: a non-terminal record for this
  target, or any `jobs/*.json` entry whose target cannot be read. The
  refusal, and `explain` ahead of its decision, name each pending file
  with the command that clears it. `explain --json` carries them as
  `pending_jobs`.
- **`workflow-controller resume <repo>`** first re-attaches to a job whose
  worker or owned work is still live and no Controller supervises (see
  [Restart: `resume` re-attaches](#restart-resume-re-attaches)), supervises it to its end and records
  it `COMPLETED`. It then reconciles each pending record against the
  target's durable state, under the lock. A record that still owns work
  (its worker `active` or `unverifiable`, a recorded or tagged owned
  process alive, or an owned-process scan that could not complete) is
  never reconciled: `resume` exits 45 and names it. A record written by
  this version that it cannot reconcile belongs to a worker that has
  definitively ended, so `resume` persists it `FAILED`
  (`UnreconcilableJobError`) and exits 20 once. The next `resume` passes
  it, and the next `step` decides from evidence.
- **A protocol-mode job** (1.7.0 and later; its record has a `protocol`
  block) is reconciled by the Workflow's own `reconcile` on the decision
  it was launched for, the same on launch and on `resume`
  ([Protocol mode](automation.md#protocol-mode-workflow-27-and-later)). A
  record without the block, including one written by 1.6.0, takes the
  legacy path unchanged. A plain `resume` also accepts a drifted
  installation, only to end a pending protocol job whose managed Workflow
  scripts changed as `FAILED` `workflow_release_changed`, running no
  Workflow script; anything else still refuses on the drift
  ([Troubleshooting](troubleshooting.md#workflow_release_changed-and-workflow_release_changed)).
- **`workflow-controller resume --abandon JOB_ID <repo>`** is the operator
  disposition for every pending job file `resume` cannot reconcile: a
  record written before this version, one failing validation, an unknown
  status, an unparseable file, and a record whose worker is
  `unverifiable`. `JOB_ID` is the bare stem of a regular file directly
  under `jobs/`. A record is marked `FAILED` (`OperatorAbandoned`) in
  place. A file with no trustworthy field (unparseable, or an unknown
  schema) has its bytes set aside under `jobs/abandoned/` and is replaced
  by a minimal terminal record; its target cannot be read, so any
  target's `--abandon` accepts it. `--abandon` launches nothing and writes
  only the Controller's own runtime. It refuses (exit 20) a terminal
  record, another target's record, a `JOB_ID` that names no pending file
  directly, and a record written by a newer Controller generation (that
  generation's own `resume` clears it). It refuses with exit 45 while the
  lock is held, while the recorded worker is `active` (no flag overrides
  that), and while it is `unverifiable` without the flag below. For a
  record carrying `worker_state`, it also refuses (exit 45, naming the
  pids) while any owned process is alive. Once the worker and every
  owned process are gone, it ends a leftover stdin anchor itself, before
  taking the lock, so it never waits for the anchor to end on its own.
- **A launch that never started a worker** (for example a wrong
  `--claude-binary`) is `FAILED` (`WorkerNotStarted`) at once, with the
  same exit code as before, and needs no `resume`.

**The liveness verdict** of a recorded worker compares the context
recorded at spawn with the current one, boot first:

- **`active`**: a running member of the recorded process group is
  observed in this boot and pid namespace. A worker that exited but was
  never reaped (a zombie, for example under a container PID 1 that does
  not reap) does not count as running, so it never holds a record
  `active`.
- **`inactive`**: no running member exists. After a reboot, crash or
  power loss during a worker, the recorded boot is over, so on the same
  host `resume` alone reconciles the record.
- **`unverifiable`**: the record comes from another host or pid
  namespace, or has no boot identity while its recorded group may be
  live, or only `killpg` could be asked and it cannot tell a running
  member from a zombie. A host is its hostname *and* its
  `/etc/machine-id` together, so a host with no readable machine id, or
  two machines sharing a hostname, fall here too; such a host pays one
  acknowledged `--abandon` after a reboot during a worker. `resume` exits
  45 without naming a process group, and
  `workflow-controller resume --abandon JOB_ID --acknowledge-unverifiable-worker <repo>`
  is your statement that the worker is gone. The flag never overrides an
  `active` worker or a held lock.

**No job file wedges a target permanently**, with the two exceptions at
the end of this paragraph. Every refusal above either ends by itself
(the lock is held, or a running worker is observed, and either ends when
that process exits or is ended), or names the Controller generation that
can act, or the acknowledgement that overrides it, or the pending files a
malformed `JOB_ID` should have named. The exceptions need a human. A
`jobs/` entry that is not a regular file (a symlink, directory, FIFO or
other special file) is never opened or replaced, and every refusal names
it for removal by hand. A regular job file the Controller cannot read
(after a permissions change, say) has to be made readable again, or
removed, by hand: `--abandon` cannot set unreadable bytes aside, so it
refuses (exit 20), and the pending report, `resume` and `--abandon` each
name that manual step.

**Job records from earlier versions.** A record without `worker_state`
(written by Controller 1.1.x or earlier, whose worker ran in print mode)
is handled exactly as before: it is never re-attached, and `resume`
exits 45 while its recorded worker is `active` or `unverifiable`. Upgrade
the Controller between jobs, not during one.

## Observing workers

Workers run `claude -p --input-format stream-json --output-format
stream-json --verbose ...` (see [Concurrency and worker lifecycle](#concurrency-and-worker-lifecycle)), and
write their stdout and stderr straight into files under the runtime
root. The Controller also appends a lifecycle event log per job and per
`step`/`run`:

| File | Contents |
|---|---|
| `jobs/<job_id>/worker.stdout` | the worker's stream, one JSON event per line, verbatim |
| `jobs/<job_id>/worker.stderr` | the worker's stderr, verbatim |
| `jobs/<job_id>/events.jsonl` | the job's lifecycle events (`planned`, `launched`, `worker_spawned`, `worker_running`, `worker_waiting`, `worker_ending`, `worker_draining`, `worker_ended`, `worker_drain_detached`, `completed`, `finished`, ...) |
| `runs/<run_id>.json` | one record per `step`/`run`: its Controller process, state, exit code and jobs |
| `runs/<run_id>/events.jsonl` | the run's events (`run_started`, `step_started`, `job_started`, `job_ended`, `run_ended`, ...) |

The log files are created with mode `0o600`, since tool output can
contain secrets. These files are written the same way whether or not
anyone is watching, and a failed event write is a warning, never a
change of outcome. There is no retention or pruning.

Three ways to watch:

- **`workflow-controller step --follow <repo>`** and
  **`workflow-controller run --follow <repo>`** render the run on stderr
  while it runs, so `--json` stdout stays machine-readable.
- **`workflow-controller follow <repo>`**, from any terminal, attaches to
  what is running for `<repo>` (default `.`): the newest running run,
  else a job whose worker is still active (for example one left running
  after Ctrl-C). With nothing active it names the last run and the
  command that replays it, and exits `0`. `--run RUN_ID` or `--job
  JOB_ID` follows that run or job, live or finished. It replays the last
  events before going live, as many as the `follow.replay_events`
  setting (20 by default; `0` replays none); `--from-start` replays
  everything. With
  the global `--json` it prints normalised events, one JSON object per
  line. It ends with exit `0` when the followed run or job ends, printing
  the run's own exit code rather than returning it. Ctrl-C, or a stdout
  whose reader has gone (`follow | head`), ends it with exit `0`, and the
  run is unaffected. An unknown id, another target's
  record or an unreadable record is exit `20`.
- **`workflow-controller status`** lists what is active across the
  runtime root under `active:` (or `active: none`): each running run with
  its start time and its Controller's liveness, each non-terminal job
  with its command, work item, start time and what its worker is doing,
  and the exact `follow` command for each (see
  [`status`](commands.md#status)). When `resume`, a
  held lock (exit `45`) or a Ctrl-C reports a running worker, it prints
  the `follow` command too.

For a job whose record carries `worker_state`, `status`, `follow`'s
heartbeat, `explain` (after each pending job, as `activity:`) and a
`jobs:` block in `inspect` say what the worker is doing: running (with
its turn), waiting (on which background tasks, which wakeups and their
due times, which harness commands, and which wakeups are presumed fired
but not yet settled), draining (which owned processes are still alive,
and which recognised daemons are not owned), whether a Controller is
attached (else `no Controller attached -- workflow-controller
--runtime-dir <root> resume <repo> re-attaches`), and whether the job
only awaits reconciliation. The `resume` command these lines print
carries `--runtime-dir`, as the `follow` command does, so it works from
any terminal.
Stall and settle times are shown only while a Controller is attached,
since only it keeps them. `--json` carries the same fields. For the
newest finished job of the target, `explain` also prints its stream
diagnosis when it holds an anomaly, a wakeup or a harness-contract
breach: the reason, each `command_lifecycle` anomaly with its
`command_uuid`, and how each wakeup ended. `follow` keeps following a
waiting worker, and a job whose worker exited while its owned processes
drain, until the record is terminal; background-task events and harness
commands are rendered as `background task` and `harness command <uuid8>
started|completed` lines.

`follow` must find the runtime root `step` used. It resolves it the same
way, so from the same install with the same `--runtime-dir` or
`WORKFLOW_CONTROLLER_HOME` it finds it without options; `status` prints
the full command.

The rendering shows the worker's session, its text, each tool call
(`tool Bash: <command>`), each tool result (the first 20 lines), its
stderr, and each result with turns, cost and duration, interleaved
with the Controller's job and run events. The job's `COMPLETED` line
adds the whole session's totals (`; session: cost $11.66, 144 turns,
...`, or `telemetry unavailable`; see [Telemetry](#telemetry)). After
the `follow.heartbeat_seconds` setting (30 s by default) with no new
event it prints a heartbeat naming the worker's pid and elapsed time. Thinking
blocks are never rendered, not even as a marker. The Controller requests
no thinking output, and the raw `worker.stdout` keeps whatever the CLI
emitted, as evidence.

**Observation is presentation-only.** No lifecycle decision reads the
logs or run records; `--follow` is read in exactly one function, which
starts the renderer; whether anyone followed is recorded nowhere; and
`follow` writes nothing, takes no lock and sends no signal. The
in-process renderer writes to its own duplicate of fd 2 without Python's
stream locks, and it disables itself rather than let a stalled stderr
reader block the Controller. Killing a follower, or closing the pipe it
writes to, changes neither the worker nor the exit code. A test runs the
same lifecycle unfollowed, with `--follow` and with a `follow` attached
and killed mid-job, and requires identical durable results.

## Telemetry

Every job that completes records what its worker's session cost, in a
`telemetry` block on the job record. The figures come from the worker's
own stream (`worker.stdout`). Nothing is sent anywhere, and no
lifecycle decision reads them. `workflow-controller telemetry` sums them
up (see [`telemetry`](commands.md#telemetry)). `status` shows the
newest such job's cost and wall times as `last job telemetry:`, and
`inspect` the newest for its target.

**Session totals, not the last turn.** A session can end many turns, and
each turn's end is a `result` event. The block covers every one:

- `turns`, `duration_ms` and the token counts (`tokens`: `input`,
  `output`, `cache_creation`, `cache_read`, from each result's `usage`)
  are per turn, so they are summed;
- `cost_usd` (`total_cost_usd`), `duration_api_ms` and each model's
  figures in `models` (from `modelUsage`) are running totals, so each is
  the **maximum** over all results. The last result's value is not used:
  a subagent's handback can repeat it unchanged, or lower;
- `results` is the number of results counted.

A figure the stream does not give, or gives as something other than a
number, is `null`, and an entry in `problems` names the field and the
result. A session with no result has `null` figures and a `no_result`
problem. A result that reports token usage but whose running totals did
not move adds a `cumulative_not_advanced` problem with its
`result_index`; it is reported, not corrected.

The block also holds:

- the wall times: `job_seconds`, from the job's creation to its
  completion, and `worker_seconds`, from the worker's spawn to its exit
  (`null` when the spawn time is not known, for example a re-attached
  job whose `worker_spawned` event is missing);
- the dimensions kept apart for comparison: `role`, `model` and `effort`
  (from `worker_route`), `harness` (`claude-code`), `workflow_version`
  (the target's Workflow release) and `controller_version`.

**Telemetry never changes a job's outcome.** If reading the stream or
computing the figures fails, the block is
`{"version": 1, "failed": true, "problems": [{"kind": "telemetry_failed", ...}]}`
with no figures. The job's status, outcome, events and reconciliation
are exactly what they would be without telemetry. Readers count such a
job as `telemetry unavailable`.

The job record's `worker` block keeps its old meaning: the **last**
result's figures, as before. Use `telemetry` for the session.

The `completed` event carries a summary of the block, and `follow`
prints it on the job's `COMPLETED` line. A job recorded before
telemetry existed has no block; the `telemetry` command derives the same
figures from its `worker.stdout` each time it runs, marked
`"derived": true`, and writes nothing back.

**Also on the job record.** Each launched job records the settings it
ran with, in a `controller_settings` block: the settings file's `path`,
the `sha256` of the bytes read (`null` with no file), and each effective
value with its source (`cli`, `file` or `default`). Its `worker_route`
gains `config_source`: `settings`, `routing-config` or `none`. Both are
optional, and older records lack them.
