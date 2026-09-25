# Controller worker lifecycle ownership: waiting workers, owned background work, and restart-safe supervision (Revision 7)

Work item: `workflow-controller-worker-lifecycle-ownership`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `4280bb6081530c1e437273348035d28c92d69c5c` ("Accept milestone for
workflow-controller-trunk-branch-pr-release-orchestration"), the previous milestone's acceptance
commit and the tip of `main` when this plan was written.
Lifecycle authority: installed Workflow 2.5.1 (`.claude/commands/`, `scripts/workflow_state.py`,
`scripts/workflow_fingerprint.py`, `scripts/prepare-ai-review.sh`) and each work item's own
`governing_workflow_version`.
Roadmap slot: `docs/ROADMAP.md` section 1.4, the follow-up patch bucket. This milestone fills that
slot with one urgent correctness hotfix. The four patches already listed there stay deferred (see
"Non-goals").
Measured against: Claude Code `2.1.282` (the installed `claude`).

## Goal

A worker's lifecycle action is terminal only when the action has really finished. The end of a
harness turn is not that point.

Today the Controller treats "the `claude` process returned control" as "the lifecycle action
finished". Under unattended operation, workers routinely start long verification in the
background, say they will continue when it finishes, and end their turn. The harness then kills
the background task and exits. The Controller either marks the job `AMBIGUOUS`/`FAILED` while the
work is half done, or finishes it and starts the next action while processes from the previous
one are still running. This milestone replaces that with an ownership model:

```text
LAUNCHED job, worker_state:
  STARTING -> RUNNING --turn ends, owned work remains--> WAITING --owned work completes--> RUNNING
                 |                                                                        |
                 +--turn ends, nothing owned remains (quiescent)--> ENDING --> DRAINING --> ENDED
                                                                              (tagged descendants
                                                                               still alive)
ENDED -> COMPLETED -> FINISHED | FAILED    (reconciliation happens only here)
```

The worker stays one `claude` process and one session from start to end. When its background task
completes, the harness itself resumes it: a new turn in the same session, with the task's result
delivered as a notification. The Controller never relaunches or resumes a session. It keeps the
session alive while the worker owns work, and ends it only at a quiescent terminal turn.

The fix is in the ownership model, not in the phase tables. It adds no special case for
`IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION` or `APPLYING_REVIEW_FEEDBACK`.

## Non-goals

Carried from the milestone request:
- Workflow 2.6 compatibility, or any wait on the Workflow 2.6.x release (roadmap section 1.6);
- Workflow Orchestration Protocol v1 (section 1.7);
- a generic Harness Adapter Protocol, Codex/Copilot, or multi-provider support (section 5). This
  milestone's harness coupling is kept in one module (`controller/worker_stream.py`) and listed
  under "Harness limitations", so that protocol can later absorb it;
- a Controller-native validation runner, or making tests faster;
- a Forge Adapter redesign, the Observation API, or the dashboard;
- unrelated roadmap cleanup. That includes the four patches already listed in section 1.4
  (misordered `--work-item` resume hints, manual-external gate ledger coherence, the
  apply-review relaunch-bound tests, general active-job presentation). They remain listed there.
  CP7 changes job presentation only where the new worker states need it.

Also out of scope:
- any edit to `scripts/`, `.claude/commands/`, `.workflow-manager/` or
  `.github/workflows/workflow-conformance.yml`. These are managed Workflow release content, and
  this milestone makes no Workflow Manager upgrade;
- changing Workflow lifecycle semantics or the expected-outcome table's phase sets (see CP6);
- cgroup/systemd-scope process containment (see limitation H5);
- rate-limit or session-limit handling. A worker that hits a usage limit stays a genuine
  `FAILURE` (observed in job `20260924T211904Z-73845184`);
- version bumps. A patch release is a post-acceptance release-preparation commit, as for 1.1.1.

## Investigation

All facts below were measured on this machine on 2026-09-25, either from the Controller's real job
records under `~/.local/state/workflow-controller/jobs/` (the package-runtime root, ladder row 4)
or from small `claude -p --model haiku` probes run in a scratch directory. CP1 turns each probe
into a pinned fixture.

### The observed failures, from their own job records

Every `AMBIGUOUS` lifecycle job in the runtime root has the same shape. The worker's last turn
ended while its background task list was non-empty. The harness then emitted the final `result`,
and after it `system/background_tasks_changed` (`tasks: []`), `system/task_updated`
(`status: killed`) and `system/task_notification` (`status: stopped`) for every open task. Then
it exited 0.

| job | repository | phase | tasks open at the final `result` |
| --- | --- | --- | --- |
| `20260925T100548Z-8b244f42` | workflow-controller | `IMPLEMENTING` | 2 `local_bash` ("Run the seven conformance suites", "Run full suite then packaged runtime tests") |
| `20260925T085148Z-5d4a976a` | workflow-manager | `IMPLEMENTING` | 1 `local_bash` |
| `20260925T111936Z-66988e17` | workflow-manager | `SELF_REVIEWING_IMPLEMENTATION` | 2 `local_bash` |
| `20260925T113603Z-6082a90b` | workflow-manager | `SELF_REVIEWING_IMPLEMENTATION` | 1 `local_bash` |
| `20260925T115439Z-cb43fe49` | workflow-manager | `SELF_REVIEWING_IMPLEMENTATION` | 1 `local_bash` ("Run the full test suite in background"; the last text says it takes about 35 minutes) |
| `20260925T113445Z-12a9f268` | workflow-controller | `APPLYING_REVIEW_FEEDBACK` | 1 `local_bash` ("Run full suite, packaging, conformance and CI check") |

The last assistant text in each says that verification is still running and the worker will
continue when it finishes (e.g. `12a9f268`: "The full suite is still running; I'll continue when
it finishes.").

Job `20260924T221915Z-2857a730` (workflow-manager, `IMPLEMENTING`) is a different shape. The
worker ended a turn while a background subagent was still writing tests. The harness waited for
the subagent and ran a second turn, in which the worker committed CP4 (`0bd8a1e`, `COMPLETE`).
The stream therefore holds **two** `result` events, and the first turn's `result` was written
after the second turn's assistant events. The Controller classified it `AMBIGUOUS` and failed
the job, although the durable progress was real.

### Contributing defects

The defect is not confined to reconciliation. Tracing the lifecycle end to end finds these
separate contributors:

- **D1 -- print mode ends the session at the first idle turn.** `worker.launch` runs
  `claude -p "<task>" ... < /dev/null`. In that mode the harness does not wait for background
  Bash tasks: at the end of a turn it emits `result`, kills the open tasks and exits (probe P1).
  The worker therefore cannot wait for its own verification, and the Controller cannot tell
  "finished" from "stopped waiting".
- **D2 -- the stream reader requires exactly one `result`, as the last line**
  (`worker._parse_worker_stream`). A legitimate multi-turn session (`2857a730`) is
  `AMBIGUOUS`. A session whose tasks were killed after the `result` is also `AMBIGUOUS`, but
  without any diagnosis of why, so operators and tests can't tell the two apart.
- **D3 -- `worker_outcome` is decided before owned work has ended.** `execute_step` goes from
  `worker.launch` straight to `COMPLETED` and verification. Nothing models a worker that is
  waiting, so no state between "running" and "terminal" exists to hold it.
- **D4 -- descendant ownership is process-group-only.** The phase-2 drain in `worker.launch` and
  every liveness check (`worker.process_test`, `_worker_liveness_hold`, `abandon`) key on the
  worker's recorded process group. The harness puts each Bash task in its own session, and
  processes the task orphans are reparented to the user's subreaper (`systemd --user`, pid 1345
  here). Probe P2 shows an orphaned descendant surviving the harness's "killed" status. It was in
  its own session and process group, and no Controller check could see it. That is how background
  suites "continued after the Controller had already failed the job".
- **D5 -- the lifecycle lock is assumed to follow descendants, and does not.** `lock.py`,
  `worker.launch`, `job._reconcile_launched`, `_OTHER_HOLDER_SENTENCE`, README and
  `tests/fake_claude.py` all state or model that descendants inherit the lock's descriptor. The
  `claude` process does inherit it (`pass_fds`), but the harness's tool processes do not (probe
  P2: `holds_lock False`). An escaped descendant therefore holds neither the group nor the lock.
  Depending on timing, a later `step` either proceeds (the lock is free) or exits 45 because of
  some other holder. This is the "inconsistent lock behaviour" of observation 4.
  `tests/test_job.py::test_a_setsid_descendant_holding_the_lock_is_not_waited_for_and_the_next_step_exits_45`
  pins today's behaviour, using a fake that does pass the descriptor on.
- **D6 -- the next action can start while owned work runs.** `run` continues as soon as a job is
  `FINISHED` (`cli.cmd_run`). With D3-D5 a job can be `FINISHED` while its verification is still
  running, so checkpoint N+1 can start while N's suite is still running, which is the provenance
  hole of observation 1.
- **D7 -- recovery cannot see or keep a waiting job.** If the Controller dies, `resume` judges a
  `LAUNCHED` record only by the worker's process group. It judges a `COMPLETED` record not at all
  (`_resume_records` goes straight to `_reconcile_completed`). `pending_reconciliation_jobs`
  never evaluates liveness. Nothing can re-attach to a live worker, so the only outcomes are
  "wait with exit 45" or a fail-closed reconciliation that forces manual recovery, as in the
  `APPLYING_REVIEW_FEEDBACK` case.
- **D8 -- same-phase progress is judged at the wrong moment.** `_predicate_checkpoint_completed_durably`
  is correct for a finished worker. It requires a checkpoint newly `COMPLETE` in the state
  committed at a moved `HEAD`. But under D1-D3 it runs before the worker's continuation has
  committed anything (`2857a730`), and when it fails it reports only `predicate_not_satisfied`,
  with no detail. The one `predicate_not_satisfied` `IMPLEMENTING` record in the runtime root
  (`20260925T002601Z-657c640e`) is a *correct* fail-closed result: the worker stopped on an
  unrelated working-tree edit. The rule itself therefore stays, and what changes is when it runs
  and what it reports (CP6).
- **D9 -- diagnostics have no vocabulary for any of this.** `status`/`follow` know only
  "worker running" and "process group draining". `explain` shows every non-terminal record as
  "not yet reconciled". `inspect` shows no jobs at all (full surface map in CP7).

### The harness contract the fix relies on (probes P1-P7)

- **P1 -- print mode.** `-p "<prompt>"`, stdin `/dev/null`, one background Bash task. The stream
  is: `background_tasks_changed [task]`, `task_started`, `result`,
  `background_tasks_changed []`, `task_updated {status: killed}`,
  `task_notification {status: stopped}`, then exit 0 after about 11 s. The task's command never
  completed.
- **P2 -- escaped descendants.** A background command that orphans a `setsid` child. After exit,
  the child is still alive (`ppid 1345`, its own session and group) and does not hold the lock
  descriptor passed to `claude`. A child that was still attached to the task's process tree when
  the harness killed it did die (the harness kills the task tree, not only its group). With the
  supervising process marked `PR_SET_CHILD_SUBREAPER`, the orphan's `ppid` is the supervisor
  instead. An environment variable set on `claude` (`WORKFLOW_CONTROLLER_JOB=<tag>`) is present in
  the orphan's `/proc/<pid>/environ`.
- **P3 -- streaming input.** `claude -p --input-format stream-json --output-format stream-json
  --verbose`, the task sent as one `{"type":"user","message":{...}}` line, stdin kept open. The
  first turn ends with `result` ("STARTED") while `background_tasks_changed` still lists the
  task, and **the task is not killed**. When it completes, the harness emits
  `background_tasks_changed []`, `task_updated {status: completed}`, `task_notification`, a new
  `system/init` and a second turn ending in its own `result` ("FINISHED"). The process exits
  only after stdin is closed.
- **P4 -- Monitor.** A `Monitor` tool call is listed as a `local_bash` background task. Each
  monitored event starts a new turn with its own `result`. When the monitored command ends, the
  task is `completed` and the list is `[]`, as in P3.
- **P5 -- ScheduleWakeup.** `ScheduleWakeup {delaySeconds: 60}` succeeds in streaming mode and
  fires about 86 s later as a new turn (`system/init`, turn, `result`). It emits **no** task
  event: the only trace of a pending wakeup is the tool's own `tool_use`/`tool_result` pair.
- **P6 -- slash commands.** A project command sent as the stream-json user message
  (`/probecmd alpha-42`) expands exactly as a `-p` prompt argument does, `$ARGUMENTS` included.
  It still expands with `--disallowedTools Agent,Workflow,Skill`, the single-agent route.
- **P7 -- tool inventory.** The `system/init` tool list offered to a worker includes `Monitor`,
  `ScheduleWakeup`, `CronCreate`, `CronDelete`, `RemoteTrigger`, `Task*`, `SendMessage` and
  `PushNotification`, next to the tools the Controller already manages.
- **Timestamps** (revision 2, round 1's I3). In the real job streams, `user` and `assistant`
  events carry a `timestamp`; `system/init` and `result` do not. The `ScheduleWakeup`
  `tool_result` states the harness's own schedule, rounded to a minute boundary: `8b244f42`'s
  `delaySeconds: 1200` answered "Next wakeup scheduled for 11:33:00 (in 1257s)".
- **Mid-session stops** (round 1's I2). `5d4a976a` has two `result` events (stream lines 274
  and 283). Between them, Monitor task `bw1ao4690` goes `task_updated {status: killed}` /
  `task_notification {status: stopped}` (lines 276-277), and the session then runs another
  turn. A `killed`/`stopped` status is therefore not, by itself, a kill at exit.
- **Result fields** (round 1's O4). Real `result` events carry `queued_turn_count`,
  `terminal_reason` (e.g. `api_error`) and `origin` (`origin.kind` values `task-notification`
  and `peer`).
- **Unreadable `environ`** (round 1's I5). Same-uid processes whose `/proc/<pid>/environ` is
  unreadable are normal on a desktop session: measured here, `systemd --user` (pid 1345),
  `(sd-pam)`, `kwin_wayland` and `polkit-kde-auth`, all non-dumpable.

Still to be measured, by CP1's capture script, before CP2 relies on them (a result that
contradicts the expectation below is a plan-amendment trigger, not an implementation-time
judgement):
- **P8 -- worker-initiated stops.** A worker that `TaskStop`s its own background Bash task, and
  a `Monitor` that reaches its own timeout, mid-session. The capture fixes the statuses they emit
  and that the session continues.
- **P9 -- wakeup cancellation.** `ScheduleWakeup {stop: true}` after a pending
  `ScheduleWakeup {delaySeconds: 60}` produces no fire turn.
- **P11 -- turn origins in streaming mode** (round 2's I2, widened by round 3's I2 and round
  4's I1, re-scoped by round 5's I1, keyed by round 6's I1). The `result` `origin` (present or
  absent, its `origin.kind`, and its full object verbatim) of four turn kinds, all in
  streaming mode: P5's wakeup-fire turn, captured at least twice with different
  `ScheduleWakeup` `prompt` texts, P3's task-completion turn, P4's per-event Monitor turns, and
  the turn that follows a background subagent's hand-back (`Agent` with
  `run_in_background: true`). B compares `origin.kind` only. The full objects show which other
  fields vary per instance, and are input to a plan amendment should B need one. For the fire
  turn it also records the turn's opening `user` event verbatim (its content, and whether it
  carries the `ScheduleWakeup` `prompt` text). The three non-fire kinds set the fake's turn
  origins (CP1). They are not a positive condition for accepting the fire recogniser; they
  only trigger the collision amendment in B, when one shares the fire's `origin.kind`. No record in the runtime root holds a wakeup fire (print mode kills
  the session first), and every `task-notification` and `peer` origin on record comes from a
  print-mode stream (`aef056f0`, `5d4a976a`; `73845184`, `2857a730`, `bad842f1`), because every
  Controller launch to date is `-p <task>` with stdin `DEVNULL` (`controller/worker.py:435`,
  `:464`). So all four are measured fresh, and none is taken from the print-mode records. B's
  wakeup recogniser is keyed on the fire turn's measurement. On record, and used only as context: across every
  `worker.stdout` in the runtime root, `result.origin.kind` is absent on 73 results, `peer` on 3
  and `task-notification` on 2, and the first `result` of every multi-result stream (the
  Controller-initiated turn) has no `origin`. `73845184` shows the combination the hand-back
  measurement guards: a background subagent and a pending `ScheduleWakeup {delaySeconds: 1800}`
  in the same session, on a multi-agent route (`routing.worker_disallowed_tools` disallows
  `Agent` only for single-agent routes, `controller/routing.py:343-345`).
- **P10 -- the Controller's system note.** `--append-system-prompt <text>` is accepted with
  `--input-format stream-json`, and a slash command sent as the user message still expands
  exactly as in P6.

### Baseline

On the base commit the Controller suite (`python3 -m unittest discover -s tests -t .`) is green:
1600 tests, `OK (skipped=6)`, 165 s. It was run with only this plan's uncommitted documentation
files added. The packaged-runtime suite runs under
`CONTROLLER_REQUIRE_PACKAGING_TESTS=1`.

## Invariants

- **I1 -- one worker, one session, one process.** A job launches at most one `claude` process.
  The Controller never passes `--resume`/`--continue`/`--session-id`/`--fork-session`, and
  re-attaching after a restart never launches anything (README's existing "fresh session"
  guarantee is preserved).
- **I2 -- turn end is not job end.** A job's `worker_outcome` is decided only once the worker is
  `ENDED`. That requires a quiescent terminal turn, the `claude` process exited, and every owned
  process gone. Verification (`_verify_transition`/`_row2_verified`) never runs earlier.
- **I3 -- ownership is recorded before it is relied on.** Every identity the Controller will later
  wait for or end is flushed to the job record before the wait begins. That covers the worker,
  the stdin anchor, the ownership tag and observed descendants. The existing `on_spawn` discipline
  is extended, not replaced.
- **I4 -- the lock lives as long as the owned work.** The lifecycle lock's open file description
  is held by the Controller, the worker and the stdin anchor. The anchor gives up stdin at
  `ENDING` but is ended only after owned work has drained, so the lock stays held if the
  Controller dies at any point before that. The lock covers what the anchor can see: the worker
  and every tagged process. The job record covers the rest (round 2's O3): a process owned only by
  adoption (untagged, H5) passes to the user's subreaper when its Controller exits, the anchor
  cannot see it, and the anchor may end itself while it lives. That process still blocks every
  later `step`/`run`, because it is in the non-terminal record's `owned_processes` and the
  ownership hold (E) checks recorded pids as well as the tag scan (I6). The anchor never outlives
  the owned work it can see by more than a bound: with no supervisor attached, it ends itself once
  its worker and every tagged process are gone (A, "Orphan lifetime"), so it can never strand the
  lock.
- **I5 -- the Controller ends owned work only on an explicit operator decision**
  (`--timeout`, or ending processes before `resume --abandon`). It never ends it because of
  elapsed time or silence ("Time is not termination" stands). The one exception is a
  harness-contract breach, the overdue wakeup (decision 5), and it fails closed. The drain
  bound (decision 9) ends nothing: it only stops *this Controller* waiting, and leaves the job
  held.
- **I6 -- no later action while owned work is alive.** `execute_step` refuses to decide or launch
  while any job for the target is non-terminal (the existing `_refuse_pending_reconciliation`),
  and `resume` refuses to reconcile a job whose worker or owned processes are alive. A live
  anchor with nothing else alive is disposed of in `resume`/`abandon`'s phase 1 (E), never
  reconciled around.
- **I7 -- fail closed on anything undecidable.** An unreadable `/proc`, a stream that breaks the
  pinned contract, an anchor that disappeared, or owned work the harness killed all leave
  `worker_outcome` non-verifying (`AMBIGUOUS`/`INTERRUPTED`). Recovery then takes the existing
  fail-closed paths.
- **I8 -- closed enumerations stay closed.** The ten job statuses and the four worker outcomes are
  unchanged. `worker_state` is a new, separately closed enumeration.
- **I9 -- a record without `worker_state`** (written by Controller 1.2.x or earlier) is reconciled
  exactly as today.
- **I10 -- no Workflow edits.** Everything here is Controller-side and proven against the
  installed Workflow 2.5.1.

## Design

### A. Streaming-input launch (`controller/worker.py`, CP3)

The argv becomes

```text
claude -p --input-format stream-json --output-format stream-json --verbose \
    --permission-mode <mode> [--model <m>] [--effort <e>] \
    --append-system-prompt <WORKER_LIFECYCLE_NOTE> --disallowedTools <list>
```

with no prompt argument. `WORKER_LIFECYCLE_NOTE` is one fixed Controller string (decision 11):
the Controller delivers every background-task notification to the worker, so a worker must not
schedule fallback wakeups, and must cancel any wakeup it no longer needs
(`ScheduleWakeup {stop: true}`) before ending its final turn. It is a system note, not a
`task_addendum`, so the task string (and every test keyed on it) is unchanged. Probe P10 (CP1)
confirms the flag in streaming mode. The task, still refused by `_assert_not_user_only` first, is written
as one line to the worker's stdin:
`{"type":"user","message":{"role":"user","content":"<task>"}}\n`. Probe P6 shows that
this runs the slash command exactly as the positional prompt did, single-agent disallow list
included.

**The stdin anchor.** stdin is an anonymous pipe. The Controller writes the task line (far
below the pipe buffer), then spawns the *anchor*: a minimal process that inherits the pipe's
write end and the lifecycle-lock descriptor. It is
`[sys.executable, "-I", "-c", <source>, <arguments>]`, run with `start_new_session=True` and
`close_fds` apart from those two descriptors. `<source>` is the text of
`controller/anchor.py`, a standalone, stdlib-only module that never imports the `controller`
package, so the anchor does not depend on the install layout (pipx or source tree). Its
arguments are the worker's `(pid, start_ticks)`, the job's ownership tag and the job's
supervisor-lock path (E). Its script: on `SIGUSR1`, close the stdin write end; otherwise sleep
in `ANCHOR_POLL_SECONDS` (5 s) steps, checking the orphan-lifetime rule below. The Controller
then closes its own write end.

**Anchor environment** (round 1's I7). The anchor runs with an **empty** environment
(`env={}`): it carries no ownership tag, neither its own job's nor any inherited outer tag in
`WORKFLOW_CONTROLLER_OWNERSHIP`. Its own job owns it through `worker_anchor`, not a tag; and an
outer job (a nested Controller, as this repository's suite runs) must not wait on it by tag. If
the nested Controller dies, its anchor is still adopted by the outer supervising Controller's
subreaper, so the outer job owns it by adoption until the orphan-lifetime rule ends it.

**Orphan lifetime** (round 1's B1 and I7). An anchor ends itself, with no signal, once all of
these have held on every check for `ANCHOR_ORPHAN_SECONDS` (60 s):
- the worker's recorded `(pid, start_ticks)` is gone;
- no same-uid process's readable `environ` carries the job's tag (C's per-pid rule applies);
- no supervisor is attached: the anchor's non-blocking `flock` on the supervisor lock succeeds,
  and it releases it at once. A Controller taking that lock in that instant retries (E).
While a supervisor is attached, the anchor never ends itself, because only the supervisor sees
adopted, untagged processes. So an orphaned anchor holds the lifecycle lock for at most about a
minute after the last owned process it can see, and never for good.

The anchor therefore has two separately ended roles:

- **stdin.** The worker sees EOF only when the anchor closes its write end. The supervisor asks
  for that with `SIGUSR1` (identity-checked on the recorded pid and start ticks) at a quiescent
  terminal turn, after flushing `worker_state: ENDING` (I3). That is the only normal way a
  session ends, and the persisted `ENDING` is how a re-attaching supervisor (E) knows a
  supervisor ended it;
- **lock.** The anchor keeps its copy of the lifecycle lock through `ENDING` and `DRAINING`. The
  supervisor ends it (identity-checked `SIGKILL`) only after the drain has found no owned
  process. So the lock is held for the whole owned lifetime, even when the Controller dies while
  tagged descendants are still draining (I4);
- a Controller that dies, or is Ctrl-C'd, leaves the anchor holding the stdin pipe (unless it
  was already released) and the lock. The worker keeps waiting or running exactly as before, and nothing owned is killed (P1's
  EOF-kill cannot happen). That is what makes re-attach (E) possible;
- if the anchor disappears without the Controller ending it, the worker sees EOF early. The
  harness then kills any open tasks, as P1 shows, and the stream reader classifies the run
  `AMBIGUOUS` with `reason: stdin_closed_while_waiting` (B's row 5, which takes precedence over
  the kill it causes), which fails closed.

The worker still inherits the lifecycle-lock descriptor (unchanged). The anchor is recorded as
`worker_anchor: {pid, start_ticks, boot_id, pid_namespace, ...}`, the `WorkerProcess` shape,
through the existing `on_spawn` flush, which gains the anchor. `ownership_tag` (the job id, known
before `Popen`) and `worker_state: STARTING` are flushed earlier, in the pre-spawn `LAUNCHED`
write that already records `worker_streams` (round 1's O1). A Controller that dies between spawn
and `on_spawn` therefore leaves a record that says it is a new-shape job, and whose tagged
processes the ownership hold (E) still finds; it never takes the I9 legacy path.

`launch` calls `on_spawn(worker_process, anchor=..., ownership_tag=...)`. Both are keyword
parameters with a `None` default on every callback (round 2's I3). The one production caller,
`job.execute_step`'s `on_spawn` closure, changes in the same checkpoint (CP3) to accept and
persist both (round 1's I6). Its defaults keep the two fake `launch` doubles in
`tests/test_job.py` (~L1900, ~L1925), which call `on_spawn(process)` alone, green unchanged. The
two one-argument callbacks that the real `launch` calls must accept the keywords, so CP3 rewrites
them: `tests/test_worker.py`'s `recording_on_spawn` (~L427, which then forwards only the process
to each test's own `on_spawn`), and the live probe's `lambda process: ...` in
`tests/test_integration_disposable_repo.py` (~L2202).

`launch` also gains the keyword `supervisor_lock_path` (default `None`), the path of the job's
supervisor lock (E), which the caller already holds and which `launch` passes to the anchor.
With `None` (the direct callers: `tests/test_worker.py` and the live probe), `launch` creates and
holds a private supervisor lock in a temporary directory for its own duration, so the anchor's
orphan-lifetime rule works the same way.

**Disallowed tools.** Every worker, whatever its route, also disallows `CronCreate`,
`CronDelete` and `RemoteTrigger`. A recurring or remote schedule has no point at which the owned
work is finished, so it cannot be owned to termination (limitation H3). The list stays one
comma-joined argv element, placed last, so every route now passes `--disallowedTools`, including
the inherit routes that pass none today. `routing.worker_disallowed_tools` gains the new tuple
`ASYNC_UNOWNABLE_TOOLS`. `Monitor`, `ScheduleWakeup`, background Bash and background
subagents stay allowed: the harness keeps them owned (A-C).

### B. The stream state machine (`controller/worker_stream.py`, CP2)

A new, pure module (no I/O) that consumes the worker's stdout one complete line at a time and
maintains:

- `turn_open`: whether a turn is in progress. A turn opens at `system/init` or at the first
  `assistant`/`user` event after a `result`, and closes at `result`. A turn's **open time** is
  the `timestamp` of its first timestamped event; until one arrives, the open time is unknown;
- `results`: every `result` event, in order;
- `background_tasks`: the latest `system/background_tasks_changed.tasks` list (id, type,
  description), plus `task_started`/`task_updated`/`task_notification` status by task id. **A task
  is open exactly when it is listed in the latest `background_tasks_changed` list and has no
  terminal status yet** (round 2's O1). The list decides membership; the per-id status only
  closes a task early. A `task_started` task that no list ever names is a foreground tool call
  that ran past the harness's background threshold, not owned work: in `2857a730`, 20 of the 23
  `task_started` tasks are never listed. A listed task stays open until a terminal status or a
  later list without it, whichever comes first. A task that reaches a terminal status
  (`completed`, `killed`, `stopped`, `failed`) is no longer open, wherever in the stream that
  happens; B's row 7 alone decides whether a kill counts against the run;
- `pending_wakeups`: every successful `ScheduleWakeup` (a `tool_use` whose paired `tool_result`
  is not `is_error`). Its time sources are fixed (round 1's I3), and they are the stream's own:
  - `scheduled_at` is the paired `tool_result` event's `timestamp`;
  - the due time is `scheduled_at` plus the harness-stated `in Ns` from the `tool_result` text
    when present, else plus `clamp(delaySeconds, 60, 3600)`;
  - a wakeup is resolved by the first **wakeup-fire turn** whose open time is at or after its
    due time minus `WAKEUP_SKEW_SECONDS` (5 s). A wakeup-fire turn is recognised positively
    (round 2's I2). **The comparison key is `origin.kind`, and only `origin.kind`** (round 6's
    I1): a turn is a fire turn exactly when its `result`'s `origin.kind` is the value P11 (CP1)
    records for a `ScheduleWakeup` fire. Every other `origin` field is per-instance (every
    `peer` origin on record carries its own `from`, `senderTaskId`, `body` and `handback`), so
    none is ever compared: they are recorded in `stream_diagnosis` only. "No `origin`" is
    **never** a recogniser (round 3's I2, round 5's I1): the Controller-initiated first turn
    carries none, and so may any non-initial turn kind the worker's allowed tools can produce,
    measured or not (a background `Workflow` completion, a forked `Skill` hand-back, an inbound
    `SendMessage` turn). A turn whose `result` carries no `origin` resolves no wakeup, whatever
    it follows. Two P11 results contradict the plan, and each stops CP1 for a plan amendment,
    as for any P8-P11 contradiction: the fire turn's `result` carries no `origin`, or its
    `origin.kind` equals the `origin.kind` P11 measures for any of the other three turn kinds.
    The amendment then picks a positive recogniser from what P11 captured, naming the field and
    its measured value, for example a fixed prefix of the fire origin's `body`, or the fire
    turn's opening `user` event carrying the `ScheduleWakeup` `prompt` text, and shows from the
    captured fires that the named field does not vary between fires. So B keeps no list of
    non-fire turn kinds that must be complete. A turn whose `result` carries any other
    `origin.kind`, among them `task-notification` (a task completion) and `peer` (a subagent's
    hand-back, in `2857a730`, `bad842f1` and `73845184`), or whose open time is still unknown,
    resolves no wakeup. The Controller-initiated first turn
    never resolves a wakeup. It is excluded by position, and its open time also precedes any due
    time, because every wakeup it could resolve is scheduled inside it. An origin kind the harness
    adds later therefore never resolves one early: the wakeup stays pending, and at worst the
    overdue rule (C) fails the run closed;
  - a later successful `ScheduleWakeup {stop: true}` resolves all of them (P9).
  This is the only tool-specific recogniser (limitation H2);
- `owned_work()`: open tasks, plus pending wakeups;
- `quiescent()`: no turn open, at least one `result`, `owned_work()` empty, and the last
  `result`'s `queued_turn_count` absent or `0` (round 1's O4: a turn already queued is never
  declared quiescent).

The state is a pure function of the lines alone. No wall clock enters it. The one supervisor
fact that depends on time, "a wakeup was declared overdue" (C), is persisted in the record and
passed back in as a supervisor fact, so a replay (E) reaches the same state.

The supervisor (C) asks one question after each batch of lines: is the worker quiescent? Unknown
event types and subtypes are tolerated *mid-stream* (the harness adds event kinds over time)
and are recorded in `stream_diagnosis.unknown_events`. They can never make a worker quiescent
early, because quiescence is defined positively.

**Terminal classification** replaces `_parse_worker_stream` + `_classify` and keeps the closed
four outcomes. It runs once, after the process has exited. Its inputs are the complete stream,
a `mode`, the supervisor's own facts, and the exit status when it is known:
- `mode` is `streaming` for every launch from CP3 on, and `print` for a print-mode stream: the
  CP1 fixtures of the real failing jobs, the legacy single-result test streams, and every launch
  before CP3 switches `launch` (round 1's I6). The mode is an explicit input, never guessed from
  the stream;
- the supervisor's facts, which only it knows and the stream cannot show: whether its
  `--timeout` fired, whether it declared a wakeup overdue, and the stream offset at which it
  ended the session (`ending_offset`), if it did. `ending_offset` is persisted with
  `worker_state: ENDING`, so a re-attaching supervisor has it too. In `print` mode there is no
  `ending_offset`; the last `result`'s offset stands in for it.

It returns `(outcome, terminal_result, stream_diagnosis)`, where `terminal_result` is the
**last** `result` event (its fields fill `WorkerResult` as today). Rows are tried in order and
the first match wins. The supervisor-attributed rows (4-5) come before the stream-structural
ones (6-7), because they name the cause, and the kill they provoke is only its consequence
(round 1's I1):

| order | condition | outcome | `stream_diagnosis.reason` |
| --- | --- | --- | --- |
| 1 | the supervisor's `--timeout` fired, or the exit status is known and negative (a signal) | `INTERRUPTED` | `timeout` / `signal` |
| 2 | the exit status is known and non-zero | `FAILURE` | `exit_status` |
| 3 | a line is not one JSON object, or there is no `result` at all | `AMBIGUOUS` | `malformed_line` / `no_result` |
| 4 | the supervisor declared a pending wakeup overdue (C) | `AMBIGUOUS` | `wakeup_not_delivered` |
| 5 | `streaming` mode only: the process exited, or the stream reached EOF, with no `ending_offset` (the supervisor never ended the session: the anchor was lost) | `AMBIGUOUS` | `stdin_closed_while_waiting` |
| 6 | the stream ended with a turn open | `AMBIGUOUS` | `exited_mid_turn` |
| 7 | a task reached `killed`/`stopped` **after** `ending_offset`, or `owned_work()` is non-empty at exit | `AMBIGUOUS` | `owned_work_killed_at_exit` |
| 8 | the last `result` has `is_error: true`, or lacks `session_id`/`is_error` | `FAILURE` / `AMBIGUOUS` | `result_is_error` / `result_incomplete` |
| 9 | otherwise: a quiescent terminal turn, then an exit after the supervisor ended the session (`streaming`), or after the last `result` (`print`) | `SUCCESS` | `quiescent_terminal_turn` |

`stream_diagnosis.secondary_reasons` lists, in row order, every later row that also matched, so
an overdue wakeup whose session end then killed a task records `wakeup_not_delivered` with
`owned_work_killed_at_exit` beside it. Row 7 is positional (round 1's I2): a task the worker
stopped itself mid-session (`TaskStop`, a `Monitor` timeout, P8) reaches a terminal status
before `ending_offset`, stops being open, and never counts. In `print` mode, a kill after the
last `result` counts, which is exactly the observed failure shape.

An unknown exit status happens only after a re-attach (E; decision 6). It skips rules 1-2 on
the exit status, but rules 3-9 still apply unchanged, and `exit_status_known: false` is recorded.

Several `result` events are legitimate (D2, `2857a730`). Because print mode can write one turn's
`result` after the next turn's events, the classification never pairs a `result` with "its"
turn. It uses only the last `result` and the final task and wakeup state. `stream_diagnosis` also
carries `mode`, `turns`, `result_count`, `tasks_seen` (id, type, description, final status and
whether it ended before or after `ending_offset`), `wakeups_seen` (scheduled, due, due source
`harness_stated`/`clamp`, resolution), `unknown_events`, and, per `result`, its
`queued_turn_count`, `terminal_reason` and `origin` when present (round 1's O4). It is written into the job record's `worker` block and read
by the diagnostics (G).

### C. Supervision (`worker.launch`, CP3)

`launch` keeps its synchronous contract, and its signature gains only keyword parameters with
defaults (A). "Returning control" becomes this loop:

1. **Spawn.** Mark the supervising Controller `PR_SET_CHILD_SUBREAPER` for the duration of the
   launch, then restore the previous value. Where `prctl` is unavailable, keep going: the tag scan
   is the authority, and the subreaper is a best-effort helper. Build the worker's environment
   with the ownership tag appended to `WORKFLOW_CONTROLLER_OWNERSHIP`, a `:`-separated list of
   tags, so a worker that runs a nested Controller, as this repository's own test suite does,
   stays owned by the outer job too. The tag is `<job_id>`. Spawn the worker, then the anchor,
   then call `on_spawn(worker_process, anchor=anchor_process, ownership_tag=tag)` (A).
2. **Supervise.** Poll the stdout file every `_SUPERVISE_POLL_SECONDS` (0.2 s), feed complete
   lines to the state machine, and call `on_state_change(state, details)` whenever
   `worker_state` changes:
   - `RUNNING` while a turn is open;
   - `WAITING` while no turn is open and `owned_work()` is non-empty. `details` names the tasks,
     the pending wakeups and their due times;
   - at a quiescent turn, `ENDING`: flush it together with `ending_offset` (the byte offset of
     the stream consumed so far), then signal the anchor to release stdin, which gives the
     worker EOF, and wait for the `claude` process to exit. A turn that opens after that (a
     notification the harness had already queued) is followed to its end. Its classification
     is decided by rows 6-7 of B, never assumed to be clean.
3. **Drain.** Once `claude` has exited, enter `DRAINING` if any *owned process* is alive, and wait
   until none is, up to the drain bound below. An owned process is:
   - a member of the worker's recorded process group (today's drain, unchanged); or
   - a process of the same uid whose `/proc/<pid>/environ` lists the job's tag in
     `WORKFLOW_CONTROLLER_OWNERSHIP`, excluding the anchor; or
   - a child adopted by the subreaper, other than the worker or the anchor; or
   - an entry of the record's `worker_state.owned_processes` whose `(pid, start_ticks)` still
     matches, identity-checked like every other recorded process (round 3's I1). Once a process
     has been seen as owned by any of the first three tests, it stays owned for as long as it
     lives, whichever Controller supervises;

   and it is **not** a recognised daemon (below). Every owned process is flushed to
   `owned_processes` when it is first seen (I3). The record stays bounded (round 4's O1):
   - **One write per scan, at most.** All pids a scan newly finds owned are batched into that
     scan's single flush, together with the pruning below. A scan that changes nothing writes
     nothing, so there is at most one write per `_OWNERSHIP_SCAN_SECONDS`.
   - **Dead entries are pruned at each flush.** An entry whose pid is gone, or whose start ticks
     no longer match, grants nothing (it fails the identity check), so it is dropped. An entry
     whose check cannot complete (the per-pid rule's unreadable cases) is kept. The list is
     therefore the live owned set at the last flush, and each write is bounded by the number of
     owned processes alive at once, not by the number the run ever spawned.
   - **History is a count and a bounded sample.** `worker_state.owned_processes_seen_count`
     counts every process ever recorded. `WorkerResult.owned_processes_seen` (step 4) is that
     count plus the first `OWNED_PROCESS_SAMPLE` (20) entries seen. `on_state_change`'s `details`
     and G's `--json` `owned_processes` carry the same pruned list.

   So the rule is the same for the original
   supervisor and for a re-attaching `resume` (E). A re-attaching `resume` is not an ancestor of
   anything the worker spawned, so its subreaper adopts nothing, and an untagged escapee that the
   original supervisor owned only by adoption is owned now only through its record entry. The
   ownership hold (E) uses these same four sources. The Controller reaps only adopted zombies, and
   only by specific pid (`os.waitpid(pid, WNOHANG)`), never `waitpid(-1)`, so it can never steal
   the worker's own exit status from `Popen`. That includes adopted daemons: whenever the
   Controller finds one of its adopted pids a zombie, in this launch or a later one in the same
   `run`, it reaps it. `details` lists the owned pids with their start ticks (the pruned list). The ownership scan
   runs every `_OWNERSHIP_SCAN_SECONDS` (1 s) in `WAITING` and `DRAINING`, and at each
   transition.

   **Per-pid rule** (round 1's I5). The scan reads `/proc/<pid>/stat` (uid via the directory's
   owner, ppid, pgid, start ticks) and `/proc/<pid>/environ` separately:
   - `ENOENT`/`ESRCH` on either: the pid is gone (it exited during the scan);
   - an unreadable `environ` (`EACCES`/`EPERM`, the non-dumpable processes the Investigation
     measured): the pid is **not owned by tag**. Group membership and adoption, both read from
     `stat`, still apply to it;
   - the scan is `unverifiable` only when `/proc` itself cannot be listed, or a same-uid pid's
     `stat` is unreadable for any other reason. Then the state stays `DRAINING` (or `WAITING`)
     and is reported, never treated as "none alive" (I7).
   A tagged descendant that makes itself non-dumpable (`ssh-agent`, `gpg-agent` call
   `prctl(PR_SET_DUMPABLE, 0)`) and has also left the group and was not adopted is therefore not
   owned: limitation H8.

   **Daemon policy** (round 1's B2; decision 9). A long-lived tool daemon started by the worker
   (the Gradle and Kotlin compile daemons, `gpg-agent`/`keyboxd`/`dirmngr`/`scdaemon`,
   `ssh-agent`, `git fsmonitor--daemon`) meets the tag and adoption tests by construction, and
   later jobs reuse it. It is shared infrastructure, not this job's work, so:
   - **Recognised daemons are excluded.** `worker.RECOGNISED_DAEMONS` is a closed tuple of
     patterns over `/proc/<pid>/cmdline`: an `argv[0]` basename (`gpg-agent`, `keyboxd`,
     `dirmngr`, `scdaemon`, `ssh-agent`), or an argv element (`fsmonitor--daemon`,
     `org.gradle.launcher.daemon.bootstrap.GradleDaemon`,
     `org.jetbrains.kotlin.daemon.KotlinCompileDaemon`). A matching process is never owned: not
     waited for, never ended by `--timeout` or abandon, and never part of a later job's
     ownership hold. It is recorded in `worker_state.excluded_processes` (pid, start ticks,
     matched pattern), so what was not owned is on the record rather than silent. A worker that
     disguises a process as a daemon only makes the Controller stop waiting for it; it gains
     nothing else;
   - **Everything else is bounded.** Once `claude` has exited, the Controller waits for the
     remaining owned processes for at most `DRAIN_DETACH_SECONDS` (600 s). If any is still alive
     then, it ends **nothing** (I5). It *detaches*: it leaves the anchor holding the lifecycle
     lock, and `launch` returns the named `DrainDetached` result, carrying `drain_detached_at`
     and the remaining pids. `launch` releases nothing: the supervisor lock is the caller's
     (E). `execute_step` then writes the record, still `LAUNCHED` (never `COMPLETED`), at
     `worker_state: DRAINING` with `drain_detached_at` and the pids, appends
     `worker_drain_detached`, and only then releases the supervisor lock (D, round 2's I1).
     The command exits 45
     naming the pids with their command lines, and the two ways on:
     `workflow-controller resume <repo>` (re-attach and keep draining, with a fresh bound), or
     ending the processes and then `resume`. The job stays held (I6), so no later action can
     start while the escapee runs, and no Controller process waits unboundedly.
   Two existing tests pin the opposite (not waited for), and both go through the real `launch`,
   so CP3 rewrites both to "owned and waited for, within the drain bound" (round 2's I3):
   `tests/test_worker.py::test_a_setsid_descendant_is_not_waited_for` (~L823) and
   `tests/test_job.py::test_a_setsid_descendant_holding_the_lock_is_not_waited_for_and_the_next_step_exits_45`
   (~L2165).
4. **End.** `ENDED`: end the anchor (its lock copy goes with it; the Controller's own copy stays
   held until `execute_step` finishes), and `wait()` it (round 2's O4). The anchor is the
   Controller's own `Popen` child, not an adopted one, so the adopted-zombie reaper never
   reaches it, and an unreaped zombie anchor would pass the `start_ticks` liveness test and show
   as live in `status`/`inspect`. The same identity-checked `SIGKILL` plus `wait()` applies on
   `--timeout` and whenever the supervising Controller ends an anchor it spawned. Then classify
   the stream (B), and return the `WorkerResult`,
   which now also carries `stream_diagnosis` and `owned_processes_seen`.

**Overdue wakeups.** A pending wakeup that is `WAKEUP_GRACE_SECONDS` (300) past due, while no
turn opens and nothing else is owned, is a harness-contract breach (P5 measured a 26 s lateness
at 60 s). The supervisor flushes `wakeup_overdue_declared_at` with `ENDING`, ends the session as
in `ENDING`, and row 4 of B classifies the run `AMBIGUOUS`. This is decision 5.

**`--timeout`** keeps its meaning of an explicit operator budget, now over the whole owned
lifetime. When it expires, the supervisor ends the worker's process group, every tagged or
adopted owned process (never a recognised daemon) and the anchor, reaps what it can, and returns `INTERRUPTED`
(`stream_diagnosis.reason: timeout`). The return code stays the worker's real exit status, as
today.

**Interruption.** `KeyboardInterrupt` during supervision propagates unchanged after the existing
orphan announcement. The announcement names the anchor too and prints the re-attach command
(E). Nothing is killed.

`on_group_drain` keeps its contract (round 2's I3). The phase-2 drain logic becomes the
process-group half of the owned-process scan, and a streaming `launch` still calls
`on_group_drain(pid, remaining)` exactly when today's does: once, when `claude` has exited and
members of its process group remain; never when none remain; and if it raises, the group is
ended and the exception propagates. `execute_step` still persists `worker_group_drain` from it.
Owned processes outside the group are reported through `on_state_change` (`DRAINING`), not
through `on_group_drain`. So every existing group-drain test stays green unchanged:
`tests/test_worker.py` ~L800-899, `tests/test_job.py`'s `worker_group_drain` assertions (~L2084,
~L2131-2138, ~L2163, ~L2366) and the group-drain presenter lines in `tests/test_cli.py` and
`tests/test_observe.py`. A new record carries both fields; the presenters (G) read
`worker_state` when it is present, and `worker_group_drain` only for records without it (I9).

### D. Job integration (`controller/job.py`, CP4)

- `worker_state` joins the `LAUNCHED` record, persisted by the job's `on_state_change` with the
  same authoritative write-then-event discipline as `on_spawn`. It holds
  `{"state", "since", "turns", "waiting_on": {...}, "owned_processes": [...],
  "owned_processes_seen_count"}` (`owned_processes` pruned and bounded as in C step 3). The event log
  gains `worker_running`, `worker_waiting`, `worker_ending`, `worker_draining` and `worker_ended`,
  each with a compact `details`. `worker_exited` is kept for old records only.
- The record also gains `ownership_tag` and `worker_state: STARTING`, written in the pre-spawn
  `LAUNCHED` write, and `worker_anchor`, written in the `on_spawn` flush (I3, round 1's O1). The
  `on_spawn` closure's new signature lands in CP3 with `launch`'s; CP4 adds the rest.
- A `DrainDetached` return (C, step 3) leaves the record `LAUNCHED` at `DRAINING` with
  `drain_detached_at`, and appends a `worker_drain_detached` event. Both are written while
  `execute_step` still holds the job's supervisor lock, which it releases only after them (E).
  It then exits 45 through the new
  `OwnedWorkDetachedError` (a `LifecycleWorkerActiveError` subclass, so the exit code and every
  existing `except` stay right). `run` stops on it, as on any exit 45.
- The status sequence is unchanged: `PLANNED -> LAUNCHED (x2+) -> COMPLETED -> FINISHED|FAILED`.
  The record simply stays `LAUNCHED` through `RUNNING`/`WAITING`/`ENDING`/`DRAINING`, and
  `COMPLETED` is written only after `worker.launch` returns with `ENDED` (I2).
- `validate_record` case 3 gains a check: a `LAUNCHED`/`COMPLETED` record that carries
  `worker_state` must name a member of the closed `WORKER_STATES`, a `COMPLETED` one must say
  `ENDED`, and a record carrying `ending_offset` must be at `ENDING`, `DRAINING` or `ENDED` (a
  worker whose anchor was lost reaches `DRAINING`/`ENDED` without one). A violation is `StaleJobRecordError`, like every other case-3 breach.
- `execute_step` still wraps the whole step in the lifecycle lock. Because `worker.launch` now
  returns only after owned work has drained, the lock is held for the whole owned lifetime by the
  Controller, and by the worker and the anchor if the Controller dies (I4). `run`'s
  continue-on-`FINISHED` rule is unchanged, and now correct: a job cannot be `FINISHED` while it
  owns work (D6).
- The real-world regressions (R12-R14) live here. They are listed under CP4.

### E. Restart recovery (`controller/job.py`, `controller/cli.py`, CP5)

**Supervisor lock.** Each supervising Controller holds `jobs/<job_id>/supervisor.lock`, an
`flock` on a file the runtime module creates. It is `O_CLOEXEC` and never passed on, so it dies
with the Controller. The primitive that opens or creates the lock file is added to
`controller/runtime.py`, because `tests/test_write_containment.py` confines new file primitives
there, in CP3.

**Supervisor-lock scope** (round 2's I1; CP3's `job.py` scope says the same, word for word). The
job's original Controller takes the job's supervisor lock before the pre-spawn `LAUNCHED` write
and holds it until after its last write to that record: the terminal flush (`FINISHED`/`FAILED`),
or the `DrainDetached` record write and its `worker_drain_detached` event. Only then does it
release it. If `execute_step` leaves by any other path (an exception, Ctrl-C), the lock is
released as it unwinds, after its last record write. So, for the whole time `execute_step`
writes `ENDED`, `COMPLETED` and runs verification, a `resume` phase 1 finds the lock `attached`
and exits 45 without touching the record. A re-attaching `resume` holds it the same way, from
before its first write to the record until after its `COMPLETED` flush (Re-attach, step 5).
Nothing in phase 1 writes a `COMPLETED` record, and phase 2 writes only under the lifecycle
lock, so no record write happens without one of the two locks.

Taking it is non-blocking but retried for up to
`SUPERVISOR_LOCK_RETRY_SECONDS` (1 s), so the anchor's momentary orphan-lifetime check (A) can
never make a `resume` report a phantom holder. Whether `resume` or `abandon` may act on a record
(re-attach, anchor disposal) is decided by that retried acquisition alone, never by the probe
below (round 3's O1). A read-only probe (`/proc/locks`, reusing `lock.probe_lifecycle_lock`'s
machinery on a file path) reports `attached`, `unattached` or `unknown`. It is used only for
presentation (G) and for naming a holder in an exit-45 message. It discounts a holder whose pid
and start ticks are the record's `worker_anchor`: the anchor's momentary orphan-lifetime hold
reads as `unattached`, never as an attached Controller.

**Ownership hold, generalised.** `_worker_liveness_hold` becomes `_owned_work_hold(record)`. It
applies to `LAUNCHED` records and to `COMPLETED` records (D7), in `resume`, in `abandon`, and in
`pending_reconciliation_jobs`' reason text. It is active when the recorded worker is
`active`/`unverifiable` (today's verdict), when any recorded or tag-scanned owned process is
alive (recognised daemons excluded, C), or when the scan is unverifiable. A live anchor alone is
**not** owned work: it is Controller infrastructure, handled by the pre-pass below. The owned processes it
checks are exactly C step 3's: the worker's process group, the tag, adoption (for a Controller
that is supervising) and the recorded `owned_processes` entries whose identity still matches
(round 3's I1). A held record is never reconciled.

**Two phases: supervision before the lifecycle lock** (round 1's B1). Today `resume` and
`abandon` take the target's lifecycle lock before they read any record (`job.resume`,
`job.abandon`: `with _acquire_lifecycle_lock(...)`), and a held lock exits 45 with nothing done.
Under this plan the anchor holds that lock, by design, for as long as the worker or its owned
work lives, so a lock-first `resume` could never re-attach, and a lock-first `abandon` could
never end a leftover anchor. Both are restructured into two phases:

1. **Phase 1, the supervision pre-pass.** No lifecycle lock. It reads only job records, and for
   each record it acts on it takes that job's supervisor lock first. If another Controller holds
   it, the command exits 45 naming that Controller (its pid, from the probe), as today. Phase 1
   never reconciles anything, never writes `COMPLETED`/`FINISHED`/`FAILED`, and touches only the
   record it holds the supervisor lock for. It considers only non-terminal records that carry
   `worker_state` (I9: legacy records go straight to phase 2, as today).
2. **Phase 2, reconciliation.** Exactly today's lock-first path: take the lifecycle lock, then
   `_resume_records` (or `_abandon_locked`) over every record, with the generalised hold. Every
   record other than the one phase 1 supervised, and that one too once phase 1 is done with it,
   is reconciled **only** here, under the lock. If the lifecycle lock is still held, the exit-45
   text now names a recorded `worker_anchor` among the holders as "job <id>'s stdin anchor",
   with the command that clears it (`workflow-controller resume <repo>` while its worker or
   owned work lives, else "it ends itself within `ANCHOR_ORPHAN_SECONDS` of its last owned
   process"), through `_recorded_worker_detail`.

There is at most one supervised job per target, because only one job can have held the
lifecycle lock at a time; phase 1 therefore supervises at most one record per call.

**A `STARTING` record** (round 2's O2) with no `worker_process` and no `worker_anchor` is a
Controller lost between spawn and the `on_spawn` flush. Phase 1 cannot re-attach to it: it has
no recorded identity to follow, and the anchor has an empty environment, so no tag scan finds it.
Phase 1 tag-scans for the record's `ownership_tag`. If it finds a live tagged process, it exits 45
naming the worker's pid and command line, and says to end that worker by hand, after which the
anchor ends itself within `ANCHOR_ORPHAN_SECONDS` and a later `resume` reconciles the record. If it
finds none, it leaves the record to phase 2, which fails it closed through today's
`_reconcile_launched` (I7). The window is one flush wide and fails closed either way.

**Re-attach** (`resume`, phase 1). For a `LAUNCHED` record that carries `worker_state` and
whose supervisor lock is `unattached`:

1. take the job's supervisor lock (above);
2. rebuild the state machine by replaying `worker.stdout` from the start, with the persisted
   supervisor facts (`ending_offset`, `wakeup_overdue_declared_at`). Replay is deterministic,
   because the state is a pure function of the lines and those facts (B);
3. decide:
   - **the worker is alive**: continue supervision exactly as C does from the replayed state.
     `resume` is not the worker's parent, so it follows the worker's liveness through the
     recorded identity (`process_test`) and cannot read its exit status. Classification then
     runs with the exit status unknown (the note under B's table). A missing status never
     upgrades to `SUCCESS` by itself: rows 3-9 must still hold (decision 6);
   - **the worker is gone and `ending_offset` is recorded** (the supervisor ended the session:
     the record is at `ENDING`, `DRAINING` or `ENDED`, whether or not the anchor is still
     alive): resume at the drain step, then classify from the stream with the exit status
     unknown. The drain uses C step 3's owned set, recorded entries included (round 3's I1). If
     any owned process is alive, among them a recorded, untagged escapee that only the original
     supervisor adopted, the re-attaching `resume` enters `DRAINING` exactly as the original
     supervisor does, and after `DRAIN_DETACH_SECONDS` it detaches in the same way: it writes
     `drain_detached_at` and `worker_drain_detached`, releases the supervisor lock and exits 45
     naming the pids. It never writes `ENDED` or `COMPLETED` while a recorded owned process is
     alive. This includes the window where `ENDED` was persisted and the anchor ended but the
     Controller died before the `COMPLETED` flush (round 1's O2): the stream proves a
     supervisor-ended quiescent session, so it is classified, not failed;
   - **the worker is gone and there is no `ending_offset`** (the session was not ended by a
     supervisor): no re-attach. Wait for owned work (C step 3's set, recorded entries included)
     to drain, with the same bound and detach as C. Then end the anchor if
     it is still alive (identity-checked), and leave the record to phase 2, where today's
     `_reconcile_launched` fails it closed (I7);
4. at quiescence, flush `ENDING` with `ending_offset` (as in C), signal the recorded anchor to
   release stdin, follow the worker to its exit, and drain owned processes;
5. end the anchor (identity-checked), then flush `worker_state: ENDED` and `COMPLETED` with the
   classification (round 1's O6: the `COMPLETED` flush comes before any lifecycle-lock attempt),
   and release the supervisor lock;
6. phase 2 then takes the lifecycle lock, which is free now that the anchor and the worker, its
   only other holders, are gone, and reconciles that record with the unchanged
   `_reconcile_completed`, together with every other pending record. If the lock cannot be
   taken, the record stays `COMPLETED`, exit 45 names the holder, and the next `resume`
   reconciles it (I6 still holds, because `COMPLETED` is non-terminal).

`resume` never launches a worker (I1). `run`/`step` never re-attach: they keep their lock-first
`execute_step`, exit 45 (lock held) or 20 (pending job), and name `resume` as the next command.

**Abandon** (`resume --abandon JOB_ID`, round 1's B1). It keeps its contract and gains the same
phase 1:
- **phase 1**, for the named record only, under its supervisor lock: refuse while the worker or
  any owned process is `active`, naming the pids and the commands to end them. An
  `unverifiable` verdict still needs `--acknowledge-unverifiable-worker`. Once the worker and
  every owned process are verified gone, end a leftover anchor (identity-checked `SIGKILL`: the
  anchor is Controller infrastructure, not work), before any lifecycle-lock attempt;
- **phase 2**: take the lifecycle lock, now free, and mark the record `FAILED`/`OperatorAbandoned`
  exactly as today (`_abandon_locked`).
So `abandon` with only an orphaned anchor left succeeds without waiting for the anchor to end
itself.

### F. Same-phase durable progress (`controller/job.py`, CP6)

The rule stays the one `_predicate_checkpoint_completed_durably` already states: an
`IMPLEMENTING -> IMPLEMENTING` job verifies exactly when at least one checkpoint is newly
`COMPLETE` in the state committed at a `HEAD` that moved. A checkpoint completion advances
`last_completed_checkpoint_id`, so that is the canonical example. CP6 changes three things:

- **one site.** `execute_step` and `_row2_verified` already share `_row_clauses_failure`. CP6 adds
  a property test pinning that every row with a self-loop reaches its predicate only through that
  helper, so the two paths cannot drift;
- **the right moment.** With I2, the predicate sees the state after the worker's last turn,
  whether the worker kept owned work or not. `2857a730`'s shape (a continuation turn commits the
  checkpoint) becomes `FINISHED`;
- **a reason.** On failure, `reconciliation_evidence.predicate_detail` names the first unmet
  condition: `pre_state_incomplete`, `head_unchanged`, `state_unreadable`,
  `no_newly_completed_checkpoint`, or `completion_not_committed_at_head`. If
  `last_completed_checkpoint_id` advanced but the checkpoint it names is not `COMPLETE` in the
  committed state, the detail says so (`last_completed_not_committed`). That catches the
  "working-tree-only completion" shape without loosening anything.

`SELF_REVIEWING_IMPLEMENTATION -> SELF_REVIEWING_IMPLEMENTATION` and
`APPLYING_REVIEW_FEEDBACK -> APPLYING_REVIEW_FEEDBACK` stay non-verifying
(`phase_not_in_to_any_of`). Under Workflow 2.5.1 neither has a durable same-phase completion, and
the failures observed there were D1-D3, which are fixed upstream of verification. No phase set
changes (I10, and the design principle). If a genuinely unfinished worker reaches a quiescent
turn, it is still `FAILED` exactly as today (AC9).

### G. Diagnostics (`controller/observe.py`, `controller/cli.py`, CP7)

One shared presenter, `observe.job_activity(record, runtime_root)`, derives an activity label
from the record, the supervisor probe and a fresh owned-work scan:

| activity | when | example line (`status`, `follow` heartbeat, `explain`) |
| --- | --- | --- |
| `active` | `LAUNCHED`, `worker_state` `RUNNING`/`STARTING` | `worker pid P running (turn 3), elapsed 12:04, last event 4s ago` |
| `waiting` | `WAITING` | `worker pid P waiting on 1 background task (b1b5d6hjp "Run the full test suite", 6:10) and 0 wakeups` |
| `draining` | `ENDING`/`DRAINING` | `worker ended; 2 owned processes still running (pids 4101, 4107)`; after a drain detach (C step 3) also `detached after 10:00 -- end them, then workflow-controller resume <repo>`; recognised daemons left running are listed as `not owned` |
| `unsupervised` | any of the above with the supervisor lock `unattached` | the line above plus `no Controller attached -- workflow-controller resume <repo> re-attaches` |
| `pending reconciliation` | `COMPLETED`, or `LAUNCHED` with no owned work | `worker ended (SUCCESS); pending reconciliation -- workflow-controller resume <repo>` |
| `terminal` | terminal status | as today |

- `status`: each non-terminal job's line uses the presenter, with `follow:` as today.
- `inspect`: gains a `jobs:` block, one line per non-terminal job for the target, beside
  `lifecycle lock:`. It stays read-only.
- `explain`: `_print_pending_jobs` appends the activity to each pending job. `--json` gains
  `pending_jobs[].activity`, `worker_state`, `waiting_on` and `owned_processes`.
- `follow`: the job loop ends only at a terminal record. While `WAITING`, it keeps following and
  prints the waiting heartbeat. When the supervisor is gone, it says so and names `resume`
  (replacing "worker exited; job X awaits resume" for records that carry `worker_state`).
  `normalise` renders `task_started`/`task_notification`/`background_tasks_changed` as compact
  `background task` lines, and the new job events through `_job_text`.
- Every presenter is read-only. `follow` stays presentation-only (roadmap 1.3 rule).

## Harness limitations (documented, not solved here)

These need the future Harness Adapter Protocol, or OS-level containment, to close. Each is
pinned by a CP1 fixture or probe, so a harness change surfaces as a failing contract test, not as
a silent behaviour change.

- **H1 -- the ownership model depends on streaming input.** Only
  `--input-format stream-json` with stdin held open keeps a session alive across background
  work (P1 vs P3). This behaviour is measured, not documented by the harness. The opt-in live
  probe (CP1/CP8) re-measures it against the installed `claude`.
- **H2 -- scheduled wakeups are invisible to the task stream** (P5). They are recognised by tool
  name from `tool_use`/`tool_result`, the one tool-specific rule in `worker_stream`. A renamed
  tool, or a wakeup scheduled some other way, would be missed: the worker would look quiescent,
  the Controller would end it at its idle turn, and the wakeup would be lost. That path is
  fail-closed, not always judged (round 5's O1). Where F's same-phase predicate verifies
  (`IMPLEMENTING -> IMPLEMENTING`), the job is judged on durable state, as any other quiescent
  worker is. The `SELF_REVIEWING_IMPLEMENTATION` and `APPLYING_REVIEW_FEEDBACK` self-loops stay
  non-verifying (`phase_not_in_to_any_of`, F), so a worker ended early there fails the job. A
  missed or mis-recognised wakeup is therefore never a false success, but it can be a failed
  job, and the recogniser's precision still matters.
- **H3 -- recurring and remote schedules (`CronCreate`, `RemoteTrigger`) cannot be owned to
  termination**, so they are disallowed for every worker.
- **H4 -- the harness's `killed` status does not mean the processes are gone** (P2). The
  Controller never trusts it and scans for itself.
- **H5 -- escape residue.** A descendant that both scrubs its environment (`env -i`) and is
  reparented where no supervising Controller's subreaper adopts it is not owned. That happens in
  two windows:
  - while no Controller is supervising;
  - while a re-attached `resume` supervises (round 3's O2). A re-attaching `resume` is not the
    worker's ancestor, so its subreaper adopts nothing.
  While the original Controller supervises, its subreaper adopts such a child, and the child is
  recorded in `owned_processes` when first seen. It stays owned through that record entry after
  the Controller exits (a drain detach, C step 3, or Ctrl-C), even though it has passed to the
  user's subreaper (C step 3's fourth source). An escapee that scrubs its environment and is
  orphaned between two ownership scans, and exits or escapes before the next one, is never
  recorded. A per-job cgroup or systemd scope would close this, and is out of scope.
- **H6 -- the harness's tool processes do not inherit extra descriptors** (P2), so lock
  ownership cannot follow descendants. The anchor carries the lock instead (I4).
- **H7 -- ordering.** Print mode can write one turn's `result` after the next turn's events
  (`2857a730`). Streaming mode was in order in every probe. The classification never depends on
  per-turn `result` placement (B).
- **H8 -- non-dumpable descendants** (round 1's I5). A tagged descendant whose `environ` is
  unreadable (`prctl(PR_SET_DUMPABLE, 0)`) is not owned by tag. It is still owned while it stays
  in the worker's process group or is adopted by the supervising Controller.
- **H9 -- daemon recognition is by name** (decision 9). `worker.RECOGNISED_DAEMONS` is a closed
  list of command-line patterns. A daemon it does not name is owned like any escapee, and holds
  the job until the drain bound detaches; a process that imitates a listed name is not waited
  for. Extending the list is a Controller change with its own test.

## Checkpoints

<!-- generated by workflow_state.render_registry_markdown(registry) -- do not edit by hand -->
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Harness contract evidence and fake harness: the measured claude 2.1.282 stream-json-input contract recorded as redacted fixtures (the real failing jobs' stream tails, background Bash, Monitor, ScheduleWakeup, slash-command and escaped-descendant probes), tests/fake_claude.py streaming-input mode (scripted turns, background tasks as real tagged processes with task events, wakeups, kill-at-EOF, escaped descendants, no descriptor inheritance by tool processes), and the opt-in live contract probe | - | 3 | 1 |
| CP2 | Worker stream state machine: controller/worker_stream.py, a pure incremental reader tracking turns, owned background tasks, pending wakeups and quiescence, with terminal classification into the closed four outcomes plus a structured stream_diagnosis (several results accepted, owned work killed at exit, exit while waiting, overdue wakeup, malformed stream); replaces worker._parse_worker_stream | CP1 | 3 | 1 |
| CP3 | Streaming-input worker launch and supervision: worker.launch sends the task as one stream-json user message on a pipe whose write end is held by a Controller-spawned stdin anchor that also holds the lifecycle lock and ends itself once nothing it can see is owned and no supervisor is attached; it supervises RUNNING, WAITING, ENDING and DRAINING from the stream, ends the session only at a quiescent terminal turn, owns descendants through an environment ownership tag and a child subreaper, excludes recognised tool daemons, drains tagged descendants and the process group up to a drain bound and then detaches without ending anything, ends all owned work on an explicit timeout, adds the Controller's worker lifecycle system note, and disallows CronCreate, CronDelete and RemoteTrigger for every worker | CP2 | 4 | 1 |
| CP4 | Job lifecycle integration: execute_step persists worker_state transitions, the anchor and owned-process snapshots while the record stays LAUNCHED, reconciles only after the worker has ENDED and its owned work has drained, keeps the lifecycle lock for the whole owned lifetime, and run cannot start the next action early; regressions for the observed IMPLEMENTING, SELF_REVIEWING_IMPLEMENTATION and APPLYING_REVIEW_FEEDBACK failures, for checkpoint N+1 blocked by checkpoint N's background verification, and for a waiting worker that resumes and finishes | CP3 | 4 | 1 |
| CP5 | Restart recovery: a per-job supervisor lock; resume and abandon run supervision and anchor disposal first, under the supervisor lock and before the lifecycle lock, then reconcile under the lifecycle lock; resume re-attaches to a live unsupervised job without launching a worker, rebuilds its state by replaying the stream, ends it and reconciles; the ownership hold over the worker and its tagged descendants applies to LAUNCHED and COMPLETED records in resume, the pending-job refusal and abandon; regressions for Controller loss during WAITING and DRAINING, for a lost anchor and for an orphaned anchor | CP4 | 4 | 1 |
| CP6 | Same-phase durable progress: one progress rule shared by execute_step and resume and evaluated only for an ended worker; IMPLEMENTING -> IMPLEMENTING is accepted exactly when a checkpoint newly reaches COMPLETE in the state committed at a moved HEAD (last_completed_checkpoint_id advancing); a structured predicate_detail on failure; no phase-specific exception for SELF_REVIEWING_IMPLEMENTATION or APPLYING_REVIEW_FEEDBACK | CP4 | 2 | 1 |
| CP7 | Operator diagnostics: status, inspect, explain and follow distinguish an active worker, a waiting worker and what it waits on, owned background processes, an ended worker still draining, pending reconciliation and whether a supervisor is attached; the new job events are rendered, the heartbeat follows worker_state, JSON output carries the same fields, and every hint names the re-attach command | CP5, CP6 | 3 | 1 |
| CP8 | Documentation and full verification under Workflow 2.5.1 (terminal checkpoint): README's concurrency and worker lifecycle section rewritten, ADR 0004 worker lifecycle ownership with the harness limitations, docs/ROADMAP.md section 1.4 updated, full suite, packaged runtime, and the opt-in live contract probe against the installed claude | CP7 | 2 | 1 |

Every checkpoint ends with the full Controller suite green (`python3 -m unittest discover -s
tests -t .`) and is committed by `/milestone-implement` in the usual per-checkpoint commit. A
checkpoint that changes behaviour an existing test pins rewrites that test in the same
checkpoint and names it in the commit message. It never deletes such a test silently.

### CP1 -- harness contract evidence and fake harness

Files: `tests/harness_contract/` (new: redacted `.jsonl` fixtures and a `README` naming each
fixture's source), `tests/fake_claude.py`, `tests/test_fake_claude_contract.py` (new),
`tests/test_integration_disposable_repo.py` (the opt-in live probe).

- **Fixtures.**
  - The six observed failing jobs' streams and `2857a730`'s. Structural events are kept
    verbatim: `system/*`, `result`, and `tool_use`/`tool_result` names and ids. Assistant text,
    tool inputs and tool outputs are replaced by placeholders, so no repository content is
    copied.
  - P1-P6 and P8-P11 captured freshly against the installed `claude` by a small capture script
    kept beside them (`tests/harness_contract/capture.py`, never run by the suite). Its argv is
    the production argv of A (`--permission-mode auto`, the route's effort,
    `--append-system-prompt` with `WORKER_LIFECYCLE_NOTE`, the full disallow list), overriding
    only the model to haiku. CP3 extracts `launch`'s argv construction into
    `worker.build_worker_argv(...)`, and from then on the capture script and the live probe call
    it, so H1's measurement covers the argv the Controller actually sends (round 1's O7). P8-P11 are the preconditions the Investigation lists; a
    result that contradicts one stops the checkpoint for a plan amendment. P11 records the
    streaming-mode `result` `origin` of four turn kinds: the wakeup fire, P3's task completion,
    P4's per-event Monitor turns (round 3's I2) and the turn after a background subagent's
    hand-back (round 4's I1, captured with `Agent` `run_in_background: true`), each as its full
    `origin` object, with at least two fires under different `prompt` texts, and the fire
    turn's opening `user` event verbatim (round 5's I1). The comparison key is `origin.kind`
    (B, round 6's I1). A fire turn whose `result` carries no `origin`, or whose `origin.kind`
    equals the `origin.kind` measured for any of the other three kinds, is such a
    contradiction (B): CP1 stops, and the plan amendment names the distinguishing field and its
    measured value, stable across the captured fires. The print-mode `peer` records are not a
    substitute for the hand-back capture.
- **Fake harness.** `tests/fake_claude.py` gains a streaming-input mode, used whenever
  `--input-format stream-json` is on its argv. It reads the task line from stdin (and records it
  in the diagnostic file). `_task()` returns the `-p` prompt argument when argv carries one, and
  otherwise that stream-json user message's content, read once (round 1's I6), so
  `FAKE_CLAUDE_SCRIPT`'s existing task-keyed lookup keeps working unchanged in streaming mode.
  - **Scripted turns** come from a **new** variable, `FAKE_CLAUDE_TURNS`. `FAKE_CLAUDE_SCRIPT`
    already exists (`tests/fake_claude.py`'s `_run_script`: a map from exact task string to a
    list of per-invocation action lists, used by six test files) and keeps its schema and
    meaning. `FAKE_CLAUDE_TURNS` is a JSON list of turns. Each turn is a list of steps:
    - `text`;
    - `bash_bg {id, seconds, orphan: none|setsid|reparent|daemon, argv0, write_file}`, which
      starts a *real* background process carrying the inherited environment, and emits
      `background_tasks_changed`/`task_started`. `orphan: daemon` double-forks a long-lived
      grandchild, optionally under the `argv0` given (for the daemon-policy tests);
    - `task_stop {id}`, a worker-initiated stop, emitting P8's measured statuses;
    - `monitor {id, ticks, interval, timeout}`;
    - `wakeup {delay}` and `wakeup_stop`, which emit the `ScheduleWakeup` `tool_use`/`tool_result`
      pair with a `timestamp` and the harness-stated `in Ns` text. The fire turn's `result`
      carries P11's measured `origin.kind`, and the fake varies every other `origin` field
      between fire turns (a per-turn counter in each), so a whole-object match cannot pass
      (round 6's I1);
    - `subagent_handback {after}`, a hand-back turn (round 2's I2);
    - the task-completion turn (after a `bash_bg` completes), each `monitor` tick turn and the
      `subagent_handback` turn carry the `origin` P11 measured for that turn kind in streaming
      mode, or none if P11 measured none (round 3's I2, round 4's I1). A test may still override
      a turn's origin explicitly, for example to `origin.kind: peer` as in the print-mode records;
    - `commit {message}` / `write {path, text}`;
    - `end_turn`, which emits `result` (with `queued_turn_count: 0`).
    `user`/`assistant` events carry a `timestamp`; `system/init` and `result` do not, as
    measured.
  - **Default streaming behaviour for every existing variable** (round 1's I6).
    `FAKE_CLAUDE_STDOUT`, `FAKE_CLAUDE_HANG`, `FAKE_CLAUDE_DESCENDANT` and `FAKE_CLAUDE_SCRIPT`
    are each played as one turn, exactly as in print mode. Then, if that output contained a
    `result` line, the fake waits for stdin EOF and exits with its configured status; if it did
    not (a malformed or truncated stream), it exits at once, as a crashed harness would. So a
    test written for print mode sees the same classification in streaming mode (row 3 for a
    missing `result`, row 9 for a clean one), and the ~40 existing fake-driven tests in
    `test_worker`, `test_job`, `test_cli`, `test_resume`, `test_job_validation`,
    `test_observation_equivalence` and `test_lifecycle_orchestration` keep passing when CP3
    switches `launch`.
  - On a task's completion the fake emits the P3 sequence and starts the next scripted turn. On
    stdin EOF with tasks open, it emits the P1 kill sequence, kills its own tasks (never
    orphans) and exits 0. In streaming mode, tool processes, including
    `FAKE_CLAUDE_DESCENDANT`'s, are spawned with `close_fds=True`, so they no longer inherit the
    lifecycle-lock descriptor (H6). Print mode keeps today's descriptor inheritance for the
    print-mode tests until CP3.
- **Contract tests.** Replaying each fixture through the fake's own event writer reproduces the
  fixture's event-type sequence. This keeps the fake honest against the measured harness. The
  opt-in live probe (`CONTROLLER_LIVE_WORKER=1`, haiku, the production argv as above) re-runs
  P3-P6 and P8-P11 and checks the same sequences against the installed `claude`.
- **Test-teardown guarantee** (round 1's I7). `tests/process_fixtures.py` gains
  `reap_recorded_workers(runtime_root)`: it reads every job record under a test's runtime root
  and identity-checked-`SIGKILL`s any recorded worker, anchor and tagged process still alive.
  Every test class that launches a streaming worker registers it with `addCleanup` through one
  shared mixin, so a test that SIGKILLs its Controller never leaks an anchor onto the machine or
  into an outer lifecycle job.

### CP2 -- worker stream state machine

Files: `controller/worker_stream.py` (new), `controller/worker.py`
(`_parse_worker_stream`/`_classify` are delegated to it), `tests/test_worker_stream.py` (new),
`tests/test_worker.py` (tests pinning "exactly one result" are rewritten).

Until CP3, `worker.launch` calls the classifier with `mode="print"`, so every print-mode launch
classifies exactly as today.

Tests:
- every CP1 fixture classifies as the table in B says:
  - the six observed failures (`mode="print"`) give `AMBIGUOUS`/`owned_work_killed_at_exit`,
    with the killed task ids and descriptions in `tasks_seen`;
  - `2857a730` (`mode="print"`) gives `SUCCESS` with `result_count: 2`;
  - `5d4a976a`'s mid-session Monitor stop (lines 276-277) is recorded as ending before the last
    `result`; the run is still `AMBIGUOUS` only because of the later kill after that `result`;
  - P3 and P4 give `SUCCESS` after a `WAITING` phase;
  - P5 is `WAITING` on one wakeup until the fire turn;
- row precedence (round 1's I1): a supervisor-declared overdue wakeup whose session end also
  killed a task gives `wakeup_not_delivered`, with `owned_work_killed_at_exit` in
  `secondary_reasons`; a streaming stream with no `ending_offset` whose EOF killed open tasks,
  or left a wakeup pending, gives `stdin_closed_while_waiting`, with the kill as secondary;
- positional kills (round 1's I2): a streaming session in which the worker `TaskStop`s a
  background task, and separately a `Monitor` reaches its timeout (P8's statuses), then ends
  with a clean quiescent turn and a supervisor end is `SUCCESS`; the same stop recorded after
  `ending_offset` is `owned_work_killed_at_exit`;
- wakeup time sources (round 1's I3): the due time is the harness-stated `in 1257s` when the
  `tool_result` carries it and the clamp otherwise (`due_source` recorded); a turn whose `result`
  has `origin.kind: task-notification`, or which opens 30 s before the due time, does not
  resolve the wakeup; a turn with no timestamped event resolves nothing; the state never reads a
  clock (the classifier is run under a patched `time` that raises);
- wakeup-fire recognition (round 2's I2): a `peer`-origin turn, and separately a
  `task-notification`-origin turn, that opens **after** a pending wakeup's due time leaves the
  wakeup pending (the worker is `WAITING`, never quiescent), and the later turn carrying P11's
  fire `origin.kind` resolves it; a turn with an unknown `origin.kind` resolves nothing;
- the comparison key (round 6's I1): two fire turns, each after its own wakeup's due time,
  whose `origin`s share P11's fire `origin.kind` but differ in every other field, both resolve
  their wakeups; a turn whose `origin` copies every other field of P11's fire capture but
  carries a non-fire `origin.kind` resolves nothing;
- no `origin` resolves nothing (round 3's I2, round 5's I1): a fire-like turn with no `origin`
  (no `result` `origin`, opening after the wakeup's due time, with an opening `user` event
  shaped like P11's fire capture) leaves the wakeup pending, and so do a task-completion
  turn, a Monitor-event turn and a subagent hand-back turn each forced to carry no `origin`;
  the Controller-initiated first turn, even with a patched timestamp after the due time,
  never resolves one;
- task openness (round 2's O1): `2857a730`'s stream, whose 20 foreground `task_started`/
  `task_notification` pairs are never listed in a `background_tasks_changed`, never makes the
  worker `WAITING` because of them, and a task that is listed stays open until its terminal
  status or a list without it;
- `queued_turn_count: 1` on the last `result` is not quiescent (round 1's O4), and
  `queued_turn_count`, `terminal_reason` and `origin` are carried into `stream_diagnosis`;
- incremental and whole-stream parsing agree: feeding a stream line by line, in any chunking,
  gives the same states as feeding it all at once. A partial trailing line is never consumed;
- `quiescent()` is false while a turn is open, while any task is open, and while a wakeup is
  pending. An unknown event type never makes it true;
- a `ScheduleWakeup` whose `tool_result` is an error is not pending. `stop: true` resolves all
  pending wakeups. Without a harness-stated time, a wakeup's due time uses the clamp (60 s to
  3600 s);
- the old single-result streams (legacy fixtures in `tests/test_worker.py`), classified with
  `mode="print"`, still give exactly today's outcomes; row 5 never applies to them.

### CP3 -- streaming-input launch and supervision

Files: `controller/worker.py`, `controller/anchor.py` (new), `controller/routing.py`
(`ASYNC_UNOWNABLE_TOOLS`), `controller/runtime.py` (the supervisor-lock file primitive, E: the
anchor's orphan-lifetime rule needs it from the first streaming launch), `controller/job.py`
(only the `on_spawn` closure, which accepts and persists `anchor`/`ownership_tag`, the pre-spawn
write of `ownership_tag`/`STARTING`, and the supervisor lock: the job's original Controller takes
the job's supervisor lock before the pre-spawn `LAUNCHED` write and holds it until after its last
write to that record: the terminal flush (`FINISHED`/`FAILED`), or the `DrainDetached` record
write and its `worker_drain_detached` event. Only then does it release it. Round 1's I6 and O1,
round 2's I1), `tests/test_worker.py`, `tests/test_write_containment.py` (only if its
allow list names primitives), `tests/test_routing.py`, `tests/process_fixtures.py`,
`tests/test_job.py`, `tests/test_trunk_preflight.py`, `tests/test_integration_disposable_repo.py`
(the live probe's `on_spawn` lambda only).

Until CP4 adds the `DrainDetached` record write and `OwnedWorkDetachedError`, CP3's
`execute_step` treats a `DrainDetached` return as today's exit 45 after a still-live worker, and
its tests reach the drain bound only through `launch` directly.

Tests this checkpoint rewrites because they pin the argv, the disallow list, the `on_spawn`
signature or the setsid behaviour it changes (round 1's I6, round 2's I3), each named in the
commit message:
- `tests/test_job.py` ~L950: the single-agent disallow list is now
  `SUBAGENT_TOOLS + ASYNC_UNOWNABLE_TOOLS`;
- `tests/test_job.py` ~L952: a route with no disallow list today now passes exactly
  `ASYNC_UNOWNABLE_TOOLS`;
- `tests/test_job.py` ~L966: the exact argv becomes the streaming form (no `/milestone-plan wi-1`
  prompt argument, `--input-format stream-json`, `--append-system-prompt`, the disallow list);
- `tests/test_trunk_preflight.py` ~L318: the branch-bound list is now
  `BRANCH_GUARD_TOOLS + ASYNC_UNOWNABLE_TOOLS`;
- the `stdin_at_eof` launch-mechanics tests in `tests/test_worker.py` (below);
- `tests/test_worker.py::test_a_setsid_descendant_is_not_waited_for` (~L823): renamed
  `test_a_setsid_descendant_is_owned_and_waited_for`. With `setsid:5`, `launch` returns only after
  the descendant exits (`exited_at` recorded) and still `SUCCESS`; `drains` stays `[]`, because
  the descendant is outside the process group (the `on_group_drain` contract, C);
- `tests/test_job.py::test_a_setsid_descendant_holding_the_lock_is_not_waited_for_and_the_next_step_exits_45`
  (~L2165): renamed `test_a_setsid_descendant_is_owned_and_waited_for_and_the_next_step_proceeds`.
  It goes through the real `launch` (`_spied_step`), so it breaks here, not in CP4. The job is
  `FINISHED` only after the descendant's `exited_at`, `worker_group_drain` is still absent, and
  the next `step` is not refused;
- `tests/test_worker.py`'s `recording_on_spawn` wrapper (~L427) accepts and ignores the new
  keywords, forwarding only the process to each test's own `on_spawn`, and the live probe's
  `on_spawn` lambda in `tests/test_integration_disposable_repo.py` (~L2202) does the same (A).

Kept green unchanged, and asserted so in the commit message: the fake `launch` doubles in
`tests/test_job.py` (~L1900, ~L1925), by the closure's `None` defaults; every group-drain test
(`tests/test_worker.py` ~L800-899 other than ~L823, `tests/test_job.py`'s `worker_group_drain`
assertions, the `tests/test_cli.py` and `tests/test_observe.py` presenter lines), by the kept
`on_group_drain` contract (C).

Tests (against the CP1 fake):
- argv is the streaming form, with no prompt argument. stdin receives exactly one task line, then
  EOF only at `ENDING`. The launch-mechanics tests that pin "stdin at EOF" (`FAKE_CLAUDE_DIAG_FILE`'s
  `stdin_at_eof`) are rewritten to that contract. The user-only denylist still refuses before
  anything is spawned;
- the anchor is spawned, recorded through `on_spawn`, and holds the pipe and the lock. After the
  worker's quiescent terminal turn, the anchor releases stdin (`SIGUSR1`), the worker exits 0,
  the anchor still holds the lock during `DRAINING` (probe reports `held` with the Controller's
  own copy closed in a subprocess variant), and it is ended at `ENDED` and reaped: after
  `launch` returns, and after a `--timeout`, the anchor pid is neither alive nor a zombie
  (round 2's O4);
- a background task keeps `launch` from returning until it completes. The fake's next turn runs.
  The result is `SUCCESS`, and `on_state_change` saw `RUNNING, WAITING, RUNNING, ENDING, ENDED`.
  Covered for `bash_bg`, `monitor` (three ticks, one turn each) and `wakeup`;
- an escaped descendant (`orphan: setsid` and `orphan: reparent`) keeps `launch` in `DRAINING`
  until it exits. It is found by tag, and while supervising, adopted as a child. A subreaper-less
  run (prctl forced unavailable) still finds it by tag;
- recorded ownership (round 3's I1): a process that `on_state_change` recorded in
  `owned_processes` stays owned after it leaves the group and, with the subreaper patched off and
  its tag scrubbed, passes none of the other three tests. `launch` stays `DRAINING` until it
  exits. The same pid reused by a new process (start ticks differ) is not owned;
- record bound (round 4's O1): a `bash_bg` that spawns 500 short-lived tagged children, at most
  a few alive at once, over the run. Across every `on_state_change` and record flush,
  `owned_processes` never holds more than the owned processes alive at that scan plus the kept
  unverifiable entries; dead entries are gone at the next flush; the number of flushes is at
  most the number of scans; `owned_processes_seen_count` equals the number of distinct processes
  recorded; and `owned_processes_seen` carries that count and at most 20 entries;
- the adopted-zombie reaper never reaps the worker (`Popen.returncode` is always the fake's real
  exit status);
- an unreadable-`/proc` scan (patched) keeps `DRAINING` and reports `unverifiable`, never
  "empty";
- per-pid rule (round 1's I5): one unrelated same-uid pid whose `environ` read is patched to
  `EACCES` does not make the scan `unverifiable`, and the job ends normally; a tagged process in
  the worker's group whose `environ` is patched to `EACCES` is still owned through the group;
- daemon policy (round 1's B2): a `bash_bg` with `orphan: daemon` and `argv0: gpg-agent`
  (double-fork, long-lived, tagged) does not hold `launch`: the result is `SUCCESS` promptly,
  the daemon is listed in `excluded_processes`, and it is still alive afterwards (the test ends
  it). The same double-fork with an unrecognised `argv0`, `DRAIN_DETACH_SECONDS` patched to 2 s,
  returns `DrainDetached` after the bound with the pid named, ends nothing, and the anchor still
  holds the lifecycle lock;
- anchor environment and orphan lifetime (round 1's I7): the anchor's `/proc/<pid>/environ` is
  empty, including under an inherited `WORKFLOW_CONTROLLER_OWNERSHIP`; with the supervisor lock
  released and the worker and its tagged processes gone, the anchor exits by itself within the
  patched `ANCHOR_ORPHAN_SECONDS` (2 s); while the supervisor lock is held, or a tagged process
  lives, it does not;
- `--timeout` during `WAITING` and during `DRAINING` ends the group, the tagged processes and the
  anchor. The result is `INTERRUPTED`;
- an anchor killed externally during `WAITING` gives `AMBIGUOUS`/`stdin_closed_while_waiting`
  (the fake emits the kill sequence);
- an overdue wakeup (fake never fires, grace patched to 1 s) gives `AMBIGUOUS`/
  `wakeup_not_delivered`;
- `CronCreate,CronDelete,RemoteTrigger` are in every route's disallow list, after the
  single-agent and branch-guard entries, still one argv element placed last, and
  `--append-system-prompt WORKER_LIFECYCLE_NOTE` precedes it;
- a fallback wakeup cancelled before the final turn (round 1's I4): a script that schedules a
  `wakeup {delay: 1200}`, completes its `bash_bg`, then `wakeup_stop`s and ends its turn makes
  `launch` return promptly (well under the delay) with `SUCCESS`; without the `wakeup_stop` it
  stays `WAITING` (asserted against a patched clock and a short delay);
- a nested tag list: a fake worker that itself launches a fake worker (with a second tag) leaves
  both tags on the grandchild, and the outer `launch` waits for it;
- a nested outer job is not held by a leaked inner anchor (round 1's I7): the inner Controller
  is SIGKILLed after its worker ends; the outer `launch` owns the inner anchor only by adoption,
  and returns once the inner anchor ends itself (patched `ANCHOR_ORPHAN_SECONDS`).

### CP4 -- job lifecycle integration

Files: `controller/job.py`, `controller/lock.py` (module docstring only, O3), `controller/errors.py`
(`OwnedWorkDetachedError`), `tests/test_job.py`, `tests/test_job_validation.py`,
`tests/test_lifecycle_orchestration.py`, `tests/test_lock.py`, `tests/fake_claude.py` (only if
a scripted step is missing).

- `worker_state`, `worker_anchor` and `ownership_tag` are persisted as in D, and so are the new
  events. Validation case 3 covers `worker_state`.
- The setsid lock test was rewritten in CP3 (round 2's I3). CP4 adds its drain-bound
  counterpart below ("Drain detach").
  `test_background_grandchild_keeps_the_lock...` in `tests/test_lock.py` stays valid for a
  same-group member and is kept.
- The descendant-inherits-the-lock text D5 identifies is rewritten here (round 1's O3), so no
  Controller string contradicts the new model after this checkpoint:
  `_OTHER_HOLDER_SENTENCE` (`controller/job.py` ~L2770: a non-worker holder is now the job's
  stdin anchor, or a same-group member, never "a stray background descendant"), and the
  `controller/lock.py` module docstring's inheritance paragraph (L9: the worker and the anchor
  hold the description, a tool process does not). README's matching lines (L343, L362) follow
  in CP8 with the rest of that section. `controller/lock.py` joins this checkpoint's files for
  that docstring only.
- **Drain detach** (round 1's B2). A job whose worker leaves an unrecognised, long-lived
  escapee (patched `DRAIN_DETACH_SECONDS`) ends `step` with exit 45 (`OwnedWorkDetachedError`),
  the record `LAUNCHED`/`DRAINING` with `drain_detached_at`, the pid and its command line in
  the message, and a following `step` refused. The record write and the `worker_drain_detached`
  event both precede the supervisor lock's release (asserted by probing the lock from a spy on
  the event append). A recognised daemon left by the same job does not
  hold it: the job is `FINISHED`, the daemon is in `excluded_processes`, and a second job
  started while the daemon still runs is not held by it either.

Regression tests (fake `claude`, disposable managed repository, Workflow 2.5.1 fixtures as the
existing lifecycle tests use):
- **R12a IMPLEMENTING.** The worker completes and commits the checkpoint, starts full
  verification with `bash_bg`, ends its turn saying it will continue, and, after the task
  completes, ends a second turn. The job is `FINISHED`, with the verification's completion time
  before `COMPLETED`. Against the unfixed code the same script is `AMBIGUOUS`/`FAILED`: the test
  asserts the new outcome, and its docstring cites `8b244f42`/`5d4a976a`;
- **R12b SELF_REVIEWING_IMPLEMENTATION.** The worker starts the full regression suite in the
  background, says it is waiting, ends its turn, then records the self-review transition on the
  continuation turn. The job is `FINISHED` (`66988e17`, `6082a90b`, `cb43fe49`);
- **R12c APPLYING_REVIEW_FEEDBACK.** The remediation worker applies the fixes, starts full
  verification, ends its turn, then completes `/apply-implementation-review`'s own state writes
  on the continuation turn. The job is `FINISHED`, and the relaunch-bound view counts one
  verified attempt (`12a9f268`);
- **R12d, fail-closed variants.**
  - The same three scripts with the anchor killed mid-wait: `AMBIGUOUS`, then `FAILED` with
    `worker_outcome` evidence;
  - R12c's variant must also be refused by the apply relaunch bound exactly as today (AC9).
- **R13.** With `run` driving two `IMPLEMENTING` checkpoints, checkpoint N's worker commits N and
  leaves a background verification that writes a timestamp when done. The worker for N+1 (from
  `FAKE_CLAUDE_INVOCATIONS_FILE`) starts strictly after that timestamp. A concurrent `step` from
  a second process during N's `WAITING` exits 45. A second variant uses a reparented orphan
  instead of a task, and N+1 starts only after the orphan exits;
- **R14.** A worker `WAITING` on `bash_bg`, and separately on `wakeup`, resumes on the
  completion turn, commits and finishes. The job is `FINISHED`, with `worker_state` history
  `RUNNING -> WAITING -> RUNNING -> ENDING -> ENDED`, and the event log carries the matching
  events;
- **lock lifetime.** While a job is `WAITING`, `lock.probe_lifecycle_lock` reports `held`. After
  the Controller process is SIGKILLed mid-wait (subprocess Controller), it still reports `held`,
  and `fuser`-style evidence names the anchor. The test's cleanup ends the worker and the
  anchor through `reap_recorded_workers` (CP1).

### CP5 -- restart recovery

Files: `controller/job.py` (two-phase `resume`/`abandon`, re-attach, the generalised hold,
`_recorded_worker_detail` naming anchors), `controller/lock.py` (the file-path probe),
`controller/cli.py` (`resume` wording and exit codes), `tests/test_resume.py`,
`tests/test_cli.py`. The supervisor-lock file primitive itself landed in CP3.

Tests:
- **R15.** A subprocess Controller is SIGKILLed while its job is `WAITING`. Then:
  - `status` shows `waiting` and `unsupervised`. `step` exits 45, and `run` exits 45;
  - `resume` re-attaches (no new `FAKE_CLAUDE_INVOCATIONS_FILE` line), follows the worker through
    its continuation turn, ends the anchor, drains, and reconciles `FINISHED`;
  - a second `resume` started during the first's supervision exits 45, naming the attached
    Controller.
- the same with the SIGKILL during `DRAINING` (worker gone, a tagged orphan alive). `resume`
  waits for the orphan, then reconciles;
- the same with the anchor also killed. The worker's stream shows the kill sequence, there is no
  re-attach, the job fails closed (`INTERRUPTED` if phase and `HEAD` are unchanged, otherwise
  `FAILED`/`UnreconcilableJobError`), and the operator hint names `resume --abandon`;
- a `COMPLETED` record with a live tagged orphan is held (`resume` exits 45 with the pids), not
  reconciled. This is D7;
- **two-phase `resume`** (round 1's B1): with the anchor holding the lifecycle lock after
  Controller loss, `resume` re-attaches (phase 1) and never exits 45 at the lock; a second,
  unrelated pending record of the same target (a `COMPLETED` fixture) is **not** reconciled
  while phase 1 supervises (its file is unchanged at a probe point mid-supervision) and is
  reconciled in phase 2, under the lock, in the same call;
- **`abandon` with only an orphaned anchor left** (round 1's B1): the worker and every owned
  process are gone and the anchor is alive (orphan lifetime patched long): `resume --abandon
  <job>` ends the anchor in phase 1, takes the lock and marks the record
  `FAILED`/`OperatorAbandoned`; with an owned process still alive it refuses, naming the pid;
- **the exit-45 text** when the lifecycle lock is held by a recorded anchor (e.g. `step` during
  an unsupervised `WAITING`) names "job <id>'s stdin anchor" and `workflow-controller resume
  <repo>`;
- `pending_reconciliation_jobs` reports activity;
- replay determinism: re-attach from a stream truncated at every line boundary of R14's fixture
  reaches the same state as live supervision, including a variant **with a pending wakeup**
  whose due time is the harness-stated one (round 1's I3), where replay gives the same due time
  and the same resolution as live supervision;
- a Controller SIGKILLed after flushing `ENDING` but before the worker exits: `resume`
  re-attaches, sees the persisted `ENDING` and `ending_offset`, drains and reconciles
  `FINISHED` with `exit_status_known: false`;
- a Controller SIGKILLed after flushing `ENDED` and ending the anchor, but before the `COMPLETED`
  flush (round 1's O2): `resume` classifies from the stream (`SUCCESS`,
  `exit_status_known: false`) and reconciles `FINISHED`, never today's fail-closed
  `_reconcile_launched`;
- a drain-detached job (CP4): after the escapee is ended, `resume` re-attaches at `DRAINING`
  and reconciles; while it still runs, `resume` re-attaches, detaches again after the bound,
  and exits 45;
- Ctrl-C during `WAITING` (the `step` subprocess gets SIGINT): the worker and the anchor keep
  running, the stderr line names the worker, the anchor and `resume`, and the run record is
  `interrupted`;
- **supervisor-lock scope** (round 2's I1):
  - a `resume` started while `execute_step` is between its `ENDED` write and its terminal flush
    (verification patched to block on an event) exits 45, names the attached Controller, and
    leaves the record file byte-identical; the blocked step then finishes `FINISHED`;
  - the same with `execute_step` held between the `DrainDetached` return and its record write
    (the write patched to block): `resume` exits 45 and the record is byte-identical; after the
    write and the release, a `resume` re-attaches at `DRAINING`;
- **a `STARTING` record** (round 2's O2), the Controller SIGKILLed between spawn and the
  `on_spawn` flush (spawn patched to stop there): `resume` exits 45 naming the tag-scanned worker
  pid and the by-hand remedy; after the worker is ended and the anchor has ended itself (patched
  `ANCHOR_ORPHAN_SECONDS`), `resume` reconciles the record fail-closed;
- **a recorded escapee outlives the anchor** (round 2's O3, extended by round 3's I1): an
  untagged, adopted process (`env -i`) recorded in `owned_processes` at a drain detach. After the
  anchor has ended itself (patched `ANCHOR_ORPHAN_SECONDS`) and the lifecycle lock is free:
  - `step` still exits 20 (the pending, held job), naming the pid;
  - `resume` re-attaches, stays `DRAINING`, and detaches after the patched
    `DRAIN_DETACH_SECONDS` with exit 45 naming the pid. The record is still `LAUNCHED`/`DRAINING`
    with a fresh `drain_detached_at`, and it never reached `ENDED`/`COMPLETED`;
  - once the escapee is ended, a further `resume` reconciles `FINISHED`;
- **the anchor's momentary hold** (round 3's O1): with the anchor patched to hold the supervisor
  lock for its orphan check at the moment the probe samples `/proc/locks`, the probe reports
  `unattached` (the holder is the recorded `worker_anchor`), and `status` still shows
  `unsupervised`; a `resume` racing that hold re-attaches through the retried acquisition;
- a record without `worker_state` (a 1.2.x-shape fixture) takes today's paths unchanged (I9).

### CP6 -- same-phase durable progress

Files: `controller/job.py`, `tests/test_job_validation.py`, `tests/test_resume.py`.

Tests:
- `IMPLEMENTING -> IMPLEMENTING`, `last_completed_checkpoint_id` `CP3 -> CP4`, with CP4 `COMPLETE`
  and committed: `FINISHED`, through both `execute_step` and a `LAUNCHED`-record `resume`;
- the same with the completion only in the working tree: `FAILED`,
  `predicate_detail: completion_not_committed_at_head` (or `last_completed_not_committed`);
- `HEAD` unchanged: `head_unchanged`. No checkpoint newly `COMPLETE`:
  `no_newly_completed_checkpoint`. `657c640e`'s shape (the worker stops on an unrelated edit)
  stays `FAILED`;
- `SELF_REVIEWING_IMPLEMENTATION -> SELF_REVIEWING_IMPLEMENTATION` and
  `APPLYING_REVIEW_FEEDBACK -> APPLYING_REVIEW_FEEDBACK` stay `phase_not_in_to_any_of` for a
  quiescent worker (no exception added);
- a property test: every `EXPECTED_OUTCOMES` row with a self-loop has a predicate, and it is
  evaluated only through `_row_clauses_failure` (both call sites).

### CP7 -- operator diagnostics

Files: `controller/observe.py`, `controller/cli.py`, `tests/test_observe.py`, `tests/test_cli.py`,
`tests/test_observation_equivalence.py`.

Tests:
- each activity row of G has a `status`, `explain` (text and `--json`), `inspect` and `follow`
  test on a record fixture, plus live ones for `waiting` and `unsupervised` on the R14 and R15
  runs, and for a drain-detached `draining` record (CP4) listing its pids and a recognised daemon
  as `not owned`;
- presenter strings for records without `worker_state` are unchanged, pinned by the existing
  tests;
- following stays presentation-only: the observation-equivalence suite (`step` with and without
  `--follow`) still produces identical records and decisions, now including a `WAITING` run.

### CP8 -- documentation and full verification (terminal checkpoint)

Files: `README.md` ("Concurrency and worker lifecycle" rewritten, including L343/L362's
descendant-inherits-the-lock text, the recognised-daemon list, the drain bound and the
fallback-wakeup delay of decision 11; "Job dispositions" and the command table updated), `docs/adr/0004-worker-lifecycle-ownership.md` (new: the ownership model,
I1-I10, H1-H9 (H5 with both windows, including re-attached supervision), the daemon policy and
the drain bound), `docs/ROADMAP.md` (section 1.4: the hotfix recorded; the four existing patches
stay listed as open), `docs/ACTIVE_MILESTONE.md`.

Verification:
- the full suite;
- the packaged-runtime suite under `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`;
- `tools/ci_workflows.py --check`;
- the opt-in live contract probe against the installed `claude`, with its output recorded for
  the functional review.

## Decisions for the reviewer and the user

This repository has no `docs/TECHNICAL_DECISIONS.md`, so there are no "Open decision" rows to
check against. The choices below are made in this plan and flagged rather than silently
finalised:

1. **Streaming input replaces print mode for every worker**, with no dual path. A fallback to
   print mode would keep D1 alive under a flag. The live probe (H1) is the guard against a
   harness change.
2. **The stdin anchor.** It adds one Controller-owned process per job. It is what lets a waiting
   worker survive Controller loss (AC10) and keeps the lock held for the owned lifetime (AC6).
   The alternative, giving the worker its own stdin write end and ending the session by signal,
   was rejected: the exit status would become a signal death, and rule 1 of B would then need a
   special case. The anchor runs with an empty environment and ends itself once nothing it can
   see is owned and no supervisor is attached (A), so it can never strand the lock.
3. **`resume` re-attaches by default, before it takes the lifecycle lock.** Today's report-only
   exit 45 for a live worker is exactly the dead end of D7. Because the anchor holds the
   lifecycle lock by design, `resume` and `abandon` become two-phase (E): supervision and anchor
   disposal under the per-job supervisor lock, then reconciliation under the lifecycle lock,
   unchanged. `run`/`step` never re-attach and stay lock-first.
4. **`CronCreate`, `CronDelete` and `RemoteTrigger` are disallowed for every worker** (H3). No
   lifecycle command uses them.
5. **An overdue wakeup (300 s past due) ends the session and fails closed.** This is the one
   place the Controller ends a session on time. It is justified as a harness-contract breach, not
   a budget. The alternative is to wait forever on a wakeup that is never delivered.
6. **A re-attached supervisor cannot read the worker's exit status**, because it is not the
   parent. A worker that reaches a quiescent terminal turn and exits after a supervisor released
   the anchor's stdin (a persisted `ending_offset`) is classified from the stream alone (row 9), with
   `exit_status_known: false` recorded. If the process vanished any other way, it is `AMBIGUOUS`.
   The alternative (always `AMBIGUOUS` after a re-attach) would defeat AC10 for the common case.
7. **The job-status enumeration stays at ten.** Waiting and draining live in `worker_state`
   inside `LAUNCHED` (I8). A new job status would ripple through about twenty closed tables and
   their tests for no behavioural gain.
8. **No generation bump.** `controller/GENERATION.json` stays at 1. New fields are additive, and
   old records take today's paths (I9).
9. **Daemon policy and drain bound** (revision 2, replacing revision 1's "no default limit";
   round 1's B2). Environment-inheritance ownership would otherwise own every tool daemon the
   worker starts (Gradle, Kotlin, `gpg-agent`, `ssh-agent`, `git fsmonitor--daemon`), and hold
   the job for hours or for ever, while a later job reuses the same daemon. So:
   - a closed list of recognised daemons (`worker.RECOGNISED_DAEMONS`, by command-line pattern)
     is never owned, and each one seen is recorded in `excluded_processes`;
   - every other owned process that outlives the worker is waited for up to
     `DRAIN_DETACH_SECONDS` (600 s). Then the Controller detaches: it ends nothing (I5), leaves
     the job `LAUNCHED`/`DRAINING` and held (I6, the anchor keeps the lock), and exits 45 naming
     the pids and `resume`.
   Rejected: ownership limited to processes still attached to the task tree or group. That
   gives up exactly the `setsid` escapees D4 is about (P2). Rejected: no bound, revision 1's
   choice, because one unrecognised daemon makes a Controller process wait indefinitely with
   nothing to show. Recognition by name is limitation H9. This narrows AC5 ("descendant
   processes ... stay owned by the job") for recognised daemons only: they are shared
   infrastructure reused across jobs, not the pending action's work, and treating them as owned
   would make AC5 and AC7 contradict each other on the next job. Flagged for the reviewer.
10. **Section 1.4's slot.** This hotfix is the section 1.4 milestone. The four patches already
    listed there stay deferred rather than being folded in (the request's "keep it narrow").
11. **Fallback wakeups** (round 1's I4). Both `ScheduleWakeup` uses in the real job records are
    fallbacks ("in case the background test runs never notify", `8b244f42`; "in case the ...
    completion notification never arrives", `73845184`). Under B they are owned work, so a
    finished worker would idle 20-30 minutes and run one more turn. The Controller now
    guarantees task-notification delivery, so every worker gets the fixed system note
    `WORKER_LIFECYCLE_NOTE` (`--append-system-prompt`, A): do not schedule fallback wakeups, and
    cancel any no longer needed before ending. A worker that ignores it is still correct, only
    slower: the wakeup fires, the worker runs one more turn, and the job ends at the next
    quiescent turn. That residual delay is accepted and documented in README. Rejected: a
    `task_addendum`, which would change the task string that slash-command expansion and every
    task-keyed test depend on.

## Artifact declaration

`docs/ai-workflow/registry/workflow-controller-worker-lifecycle-ownership-artifacts.json` starts
from `generate_artifacts_declarations(..., work_item_type="product")` and is fitted to this plan's
footprint, as the previous Controller milestones' were:

- **Plan stage:** protected = this plan, its registry and its mapping. It inherits the
  exclusions, and adds `controller/`, `tests/`, `tools/`, `.workflow-controller/`,
  `pyproject.toml` and `setup.py` as excluded, because they are implementation content.
- **Implementation stage:**
  - protected prefixes: `controller/`, `tests/`, `tools/`, `.workflow-controller/`, and the
    inherited `docs/adr/`;
  - protected paths: `README.md` (moved from the template's exclusions), `pyproject.toml`,
    `setup.py`, the three rendered `.github/workflows/` files, and the artifacts file itself;
  - plus the inherited product-template entries the JSON keeps (round 1's O5): the prefixes
    `app/`, `config/` and `gradle/`, the Gradle build files, `docs/DOMAIN_GLOSSARY.md` and
    `docs/UX_FLOWS.md`. This milestone edits none of them.
  `docs/ROADMAP.md`, `docs/ACTIVE_MILESTONE.md` and `docs/ai-workflow/` stay excluded
  (narrative and bookkeeping). This milestone makes no Workflow Manager upgrade, so `scripts/`,
  `.claude/commands/`, `.workflow-manager/` and `workflow-conformance.yml` keep their inherited
  classification and are not edited.

## Verification

Per checkpoint: the full Controller suite, plus the checkpoint's own new tests. At CP8: the full
suite, the packaged-runtime suite, `tools/ci_workflows.py --check`, and the opt-in live contract
probe. Nothing in the default suite needs network or the real `claude`: every worker is the CP1
fake, driven by real processes, pipes and `/proc`.

## Migration / data-integrity notes

- No stored-data migration. Job records gain optional fields (`worker_state`, `worker_anchor`,
  `ownership_tag`, `worker.stream_diagnosis`). A record without them takes today's paths.
- A `LAUNCHED` record left by a 1.2.x Controller whose print-mode worker is still running is
  handled exactly as today (exit 45, then reconcile). It is never re-attached, because it has no
  anchor and no `worker_state`.
- Upgrade between jobs, not during one. A 1.3.x Controller never re-attaches to a job it cannot
  replay.

## This milestone's own rollout

This milestone is implemented while the orchestrating Controller (release 1.1.x, pipx) still
has the defect. Until it ships, the same failures can recur while it is being implemented. Two
mitigations need no Controller change:
- during this milestone, lifecycle workers run verification in the foreground. The Controller
  suite takes about 3 minutes (Baseline), well inside the Bash tool's 10-minute per-call limit.
  The packaged-runtime suite is run as its own call;
- or drive this milestone's checkpoints through the orchestrating session's own workers (Agent
  tool, per the standing orchestration policy), which are not subject to D1.

Which one to use is the operator's choice at implementation time. It is not a plan decision.

## Plan review decisions

### Round 1 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 1, `REVISE`) -- applied in revision 2

Every finding was checked against the repository before it was applied; all are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| B1 lock-first `resume`/`abandon` vs the anchor's lock | accepted | `job.resume` (`controller/job.py` ~L2352) and `job.abandon` (~L2978) both enter `with _acquire_lifecycle_lock(...)` before reading a record | E ("Two phases", "Re-attach", "Abandon"); A ("Orphan lifetime"); decision 3; CP5 tests (two-phase `resume`, `abandon` with only an orphaned anchor, anchor-naming exit-45 text) |
| B2 daemons owned by tag/adoption, unbounded drain | accepted | this repository is itself a Gradle project (`build.gradle.kts`, `gradlew`); the drain as written had no bound (revision 1's decision 9) | C step 3 ("Daemon policy"); decision 9 rewritten; D (`DrainDetached`); H5, H9; CP3 and CP4 tests (daemonising grandchild recognised and unrecognised) |
| I1 rows 5/6 shadowed by row 4 | accepted: supervisor-attributed rows now precede the kill row | B's revision 1 table order | B's table (rows 4-5 before 6-7), `secondary_reasons`; CP2 precedence tests |
| I2 mid-session stops counted as kills at exit | accepted: row 7 is positional | `5d4a976a` stream lines 274-283 (Monitor `bw1ao4690` killed/stopped between two `result`s) | Investigation; B (`background_tasks`, row 7); P8 in CP1; CP2 positional tests |
| I3 wall-clock wakeup due time | accepted | `8b244f42`: "Next wakeup scheduled for 11:33:00 (in 1257s)" for `delaySeconds: 1200`; `system/init` has no `timestamp`, `user`/`assistant` do | Investigation; B (`pending_wakeups` time sources, purity note); CP2 and CP5 tests |
| I4 fallback wakeups idle finished jobs | accepted: system note plus documented residual delay | `8b244f42` and `73845184` reasons ("fallback in case ...") | A (`--append-system-prompt`); decision 11; P9-P10 in CP1; CP3 test |
| I5 unreadable per-pid `environ` | accepted | measured here: pids 1345 (`systemd`), 1347 (`(sd-pam)`), 1677 (`kwin_wayland`), 2180 (`polkit-kde-auth`) unreadable | C step 3 ("Per-pid rule"); H8; CP3 tests |
| I6 CP2/CP3 breakages not named | accepted, every item | `tests/fake_claude.py` ~L124/L434/L441 (`FAKE_CLAUDE_SCRIPT`, `_task()`, `_run_script`) and its six users; `tests/test_job.py` ~L950/L952/L966; `tests/test_trunk_preflight.py` ~L318; `job.execute_step`'s `on_spawn(worker_process)` (~L3839) | CP1 (`FAKE_CLAUDE_TURNS`, `_task()`, default streaming behaviour); B (`mode`); CP2 (`mode="print"` until CP3); CP3 files and named rewritten tests; A (`on_spawn` signature) |
| I7 anchor environment, orphan lifetime, test leaks | accepted | revision 1's A said only "no ownership tag" | A ("Anchor environment", "Orphan lifetime"); CP1 (`reap_recorded_workers`); CP3 tests (empty environ, self-termination, nested outer job) |
| O1 flush tag and `STARTING` before spawn | accepted | `execute_step` already persists a pre-spawn `LAUNCHED` write carrying `worker_streams` (~L3822) | A; D |
| O2 `ENDED` persisted, anchor gone, no `COMPLETED` | accepted: classify from the stream | revision 1's E routed it to `_reconcile_launched` | E ("Re-attach" step 3); CP5 test |
| O3 descendant-inherits-lock strings | accepted, in CP4 | `_OTHER_HOLDER_SENTENCE` (`controller/job.py` ~L2770); `controller/lock.py` docstring L9; README L343/L362 | CP4 (Controller strings); CP8 (README) |
| O4 `queued_turn_count`/`terminal_reason`/`origin` | accepted | fields present in the real `result` events | Investigation; B (`quiescent()`, `stream_diagnosis`); CP2 test |
| O5 artifact prose omits inherited entries | accepted | the artifacts JSON protects `app/`, `config/`, `gradle/`, the Gradle files, `docs/DOMAIN_GLOSSARY.md`, `docs/UX_FLOWS.md` | "Artifact declaration" |
| O6 E step 4 ordering | accepted | revision 1's E step 4 | E ("Re-attach" steps 5-6: `COMPLETED` flushed before the lock attempt) |
| O7 live probe argv; decision 6 wording | accepted | revision 1's CP1 probe used a hand-built argv | CP1 (`worker.build_worker_argv`, from CP3); decision 6 reworded ("released the anchor's stdin") |

Consequential changes beyond the findings: the supervisor-lock file primitive moves from CP5
to CP3 (the anchor's orphan-lifetime rule needs it from the first streaming launch); CP4 gains
`controller/errors.py` (`OwnedWorkDetachedError`) and `controller/lock.py` (docstring only).
No checkpoint was added, removed or reordered.

### Round 2 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 2, `REVISE`) -- applied in revision 3

Every finding was checked against the repository and the runtime records before it was applied;
all are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 supervisor-lock scope stated two ways | accepted: one scope, until after the last record write | `execute_step` writes `COMPLETED` (`controller/job.py` ~L3895-3900) and runs verification after `launch` returns, inside the lifecycle-lock `with` (~L3590) that phase 1 does not take | E ("Supervisor-lock scope"); C step 3 (`launch` releases nothing; `execute_step` writes, then releases); D (`DrainDetached` bullet); CP3's `job.py` scope (same wording as E); CP4 and CP5 tests |
| I2 a `peer`-origin turn resolves a wakeup | accepted: positive wakeup-fire recogniser | `origin.kind: peer` hand-back turns in `2857a730`, `bad842f1`, `73845184`; no wakeup fire exists in any runtime record (print mode kills first), so its origin is measured fresh | B (`pending_wakeups`); P11 in the Investigation and CP1; fake `subagent_handback`; CP2 test |
| I3 CP3 breaks tests it does not name | accepted: `on_group_drain` kept; setsid rewrites moved to CP3; keyword defaults | `tests/test_worker.py` ~L823 and ~L800-899; `tests/test_job.py` ~L2165 (`_spied_step`, real `launch`), ~L2084-2163, ~L2366; `tests/test_job.py` ~L1900/~L1925; `tests/test_worker.py` ~L427 `recording_on_spawn`; `tests/test_integration_disposable_repo.py` ~L2202 | A (`on_spawn` keywords, `supervisor_lock_path`); C (step 3, `on_group_drain` paragraph); CP3 rewritten and kept-green lists; CP4 bullet |
| O1 which source makes a task open | accepted: listed and not terminal | `2857a730`: 23 `task_started`, 20 never listed in a `background_tasks_changed`, all 23 with a terminal `task_notification` | B (`background_tasks`); CP2 test |
| O2 phase 1 on a `STARTING` record | accepted | revision 2's E covered only records with a recorded worker | E ("A `STARTING` record"); CP5 test |
| O3 I4 overclaims after a detach | accepted: I4 reworded | H5 (adopted, untagged processes pass to the user's subreaper) and E's hold over recorded pids | I4; CP5 test |
| O4 the Controller reaps its own anchor | accepted | the anchor is a direct `Popen` child; C's reaper handles adopted pids only | C step 4; CP3 test |

Consequential changes beyond the findings: `launch` gains the keyword `supervisor_lock_path`
(A), because the anchor needs the caller-held lock's path; CP3 gains
`tests/test_integration_disposable_repo.py` (the `on_spawn` lambda only). No checkpoint was
added, removed or reordered.

### Round 3 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 3, `REVISE`) -- applied in revision 4

Every finding was checked against the repository, the plan text and the runtime records before
it was applied. All are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 a re-attached drain ignores recorded, untagged owned processes | accepted: recorded pids are the fourth ownership source | revision 3's C step 3 listed group, tag and adoption only, while E's hold already checked recorded pids; a re-attaching `resume` is not the worker's ancestor, so `PR_SET_CHILD_SUBREAPER` adopts nothing for it (P2) | C step 3 (fourth source, first-seen flush, re-attach note); E (hold names the same four sources; "Re-attach" step 3, second and third bullets: `DRAINING`, detach, never `ENDED`/`COMPLETED`); CP3 test (recorded ownership); CP5 test (O3's setup extended through `resume`) |
| I2 the "no `origin`" fallback is not positive | accepted: not pre-accepted; P11 widened | re-tallied `result.origin.kind` over every `worker.stdout` in the runtime root: absent on 72, `peer` on 3, `task-notification` on 2; the first `result` of all five multi-result streams (`73845184`, `2857a730`, `bad842f1`, `aef056f0`, `5d4a976a`) has none; both `task-notification` origins are print-mode | Investigation (P11); B (`pending_wakeups`: conditional fallback, amendment trigger, first turn excluded); CP1 (P11 scope, fake turn origins); CP2 test (the "no `origin`" branch) |
| O1 the anchor's momentary hold reads as `attached` | accepted | A ("Orphan lifetime": the anchor takes the supervisor lock every `ANCHOR_POLL_SECONDS`); revision 3's E did not say which of probe or acquisition decides eligibility | E ("Supervisor-lock scope": eligibility is decided by the retried acquisition alone; the probe discounts the recorded `worker_anchor`); CP5 test |
| O2 H5 omits re-attached supervision | accepted | same subreaper fact as I1 | H5 (two windows, the record-entry rule); CP8 (ADR 0004 text) |

Consequential changes beyond the findings: none. No checkpoint was added, removed or reordered,
and no checkpoint's name or dependencies changed, so the registry's checkpoint set is unchanged.

### Round 4 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 4, `REVISE`) -- applied in revision 5

Every finding was checked against the repository, the plan text and the runtime records before
it was applied. All are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 the "no `origin`" condition leans on print-mode `peer` records | accepted: the hand-back turn is measured in streaming mode | today's argv is `-p <task>` (`controller/worker.py:435`) with stdin `DEVNULL` (`:464`), so all three `peer` origins (`73845184`, `2857a730`, `bad842f1`) are print-mode; `73845184`'s stream holds an `Agent` with `run_in_background: true` and a `ScheduleWakeup {delaySeconds: 1800}` in one session; `SUBAGENT_TOOLS` is disallowed only for single-agent routes (`controller/routing.py:343-345`) | Investigation (P11: four turn kinds, print-mode records context only); B (`pending_wakeups`: the condition names the three streaming-measured kinds); CP1 (P11 scope, contradiction rule over any of the three, fake `subagent_handback` origin from P11); CP2 test (the "no `origin`" branch covers a hand-back turn) |
| O1 `owned_processes` has no bound or write rate | accepted | C step 3 (revision 4) flushed each owned process when first seen, with the scan every `_OWNERSHIP_SCAN_SECONDS` (1 s) in `WAITING`; `cb43fe49` waited about 35 minutes on a background test suite | C step 3 (one batched write per scan at most, dead entries pruned at each flush, unverifiable entries kept, `owned_processes_seen_count` and a 20-entry `owned_processes_seen` sample); D (`worker_state` shape); CP3 test (record bound) |

Consequential changes beyond the findings: none. No checkpoint was added, removed or reordered,
and no checkpoint's name or dependencies changed, so the registry's checkpoint set is unchanged.

### Round 5 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 5, `REVISE`) -- applied in revision 6

Every finding was checked against the repository and the plan text before it was applied. Both
are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 the "no `origin`" branch depends on an incomplete list of non-initial turn kinds | accepted, the preferred option: the conditional branch is dropped, and a fire turn with no `origin` is a plan-amendment trigger | `SUBAGENT_TOOLS` is `("Agent", "Workflow", "Skill")` (`controller/routing.py:113`), and its own comment records a `context: fork` skill running in a subagent; it is disallowed only for single-agent routes (`:343-345`), so `Workflow` completions and forked-`Skill` hand-backs reach multi-agent workers; P7's inventory lists `SendMessage`, and A's `ASYNC_UNOWNABLE_TOOLS` leaves it allowed; none of these turn kinds is in P11 | Investigation (P11: the fire turn's opening `user` event recorded; the other three kinds no longer gate a recogniser); B (`pending_wakeups`: "no `origin`" is never a recogniser; an absent or colliding fire origin stops CP1 for an amendment); CP1 (P11 scope and contradiction rule); CP2 test ("no `origin`" resolves nothing, including a fire-like turn) |
| O1 H2's "fail-safe" wording is broader than F guarantees | accepted | F leaves `SELF_REVIEWING_IMPLEMENTATION` and `APPLYING_REVIEW_FEEDBACK` self-loops `phase_not_in_to_any_of`; only `IMPLEMENTING -> IMPLEMENTING` verifies | H2 (fail-closed, not always judged; a failed job on the non-verifying self-loops) |

Consequential changes beyond the findings: none. No checkpoint was added, removed or reordered,
and no checkpoint's name or dependencies changed, so the registry's checkpoint set is unchanged.

### Round 6 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 6, `REVISE`) -- applied in revision 7

Every finding was checked against the repository and the plan text before it was applied. Both
are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 B does not say what "the same `origin`" means, and real origins carry per-instance fields | accepted, the preferred option: the comparison key is `origin.kind`, everywhere | re-read the `result` `origin` objects in the runtime root's `worker.stdout` files: every `peer` origin is `{kind, from, senderTaskId, body, handback}`, with `from`/`senderTaskId` 17-character ids and `body` a 13-17 KB hand-back text that differ per turn, so whole-object equality could never match two real turns | B (`pending_wakeups`: the key is `origin.kind` only, other fields recorded in `stream_diagnosis` and never compared; the collision rule compares `origin.kind`; the amendment names the distinguishing field, its measured value and its stability across fires); Investigation (P11: full `origin` objects, at least two fires under different `prompt` texts); CP1 (P11 scope and contradiction rule keyed on `origin.kind`; the fake varies every non-`kind` fire `origin` field between fire turns); CP2 test (the comparison key: two differing fire origins both resolve; a non-fire kind with the fire's other fields resolves nothing) |
| O1 leftover wording from the dropped branch | accepted | B's "Either way," pointed at the two branches revision 6 removed; P11 said the non-fire kinds are "not a condition for accepting any recogniser", yet a `kind` clash rejects the fire recogniser | B ("Either way," removed); Investigation (P11: "not a positive condition ... they only trigger the collision amendment") |

Consequential changes beyond the findings: none. No checkpoint was added, removed or reordered,
and no checkpoint's name or dependencies changed, so the registry's checkpoint set is unchanged.
