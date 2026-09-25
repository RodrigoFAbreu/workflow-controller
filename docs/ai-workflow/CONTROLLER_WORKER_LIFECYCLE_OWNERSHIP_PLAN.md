# Controller worker lifecycle ownership: waiting workers, owned background work, and restart-safe supervision (Revision 16)

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

**Amendment 0 (revision 8).** Revision 7 was approved at `14205f3` and superseded by
`/request-plan-amendment` at `6db4f6b` (`amendment_history[0]`). CP1's mandatory harness-contract
probe disproved one assumption of revision 7: a `ScheduleWakeup` fire turn's `result` carries **no
`origin`** at all, so revision 7's `origin.kind` wakeup-fire recogniser cannot exist. Revision 8
replaces it with the correlated `command_lifecycle` bracket that the same probe measured around
every fire turn, and only around fire turns (B, decision 12). Every other decision, invariant,
checkpoint and its order, and the scope are revision 7's, unchanged unless this recogniser
touches them. The measured evidence is the untracked `tests/harness_contract/` fixtures that CP1
captured. They are preserved byte for byte and adopted by CP1 as they are; nothing is
re-captured to fit this design. The amendment's own finding table is under "Plan review
decisions".
Revision 9 applies round 8's local review of the amendment. It corrects one misstated fixture
fact (a fire turn has no *opening* `user` event, but can hold `tool_result`s for its own tool
calls), re-words the CP1 contract test that pinned the misstatement, and lets the fake's fire
turn carry tool steps. The recogniser is unchanged.
Revision 10 applies round 9's manual external review. Its one Important finding (I1) is that a
regular bracket around some *other* turn, opening after a pending wakeup's due time, could
resolve that wakeup, let the worker quiesce and end the session before the real fire. Revision
10 closes that path in B and C, not in reconciliation. A matched bracket no longer removes a
wakeup from owned work. It makes the wakeup `fire_matched`, and a `fire_matched` wakeup stays
owned until it *settles*: a successful `ScheduleWakeup {stop: true}` whose harness-reported
`cancelledWakeups` agrees with the Controller's own count, or `WAKEUP_SETTLE_SECONDS` of
supervisor time after the bracket's `completed(X)` with no contradicting bracket (decision 13).
No new probe is needed, and the fixtures are unchanged.
Revision 11 applies round 10's local review, two Important findings in those settlement rules.
A stop issued from inside a fire turn now evaluates that bracket's match provisionally, so the
wakeup whose fire is running is left out of the count compared with the harness's
`cancelledWakeups` and the bracket's match is the wakeup the stop settled (I1). The settle
timer now runs only under the overdue rule's own idleness predicate, so an open task pauses it
too (I2). No probe, fixture, checkpoint or requirement changes.
Revision 12 applies round 11's manual external review. Its one Important finding (I1) is that
revision 11's expected count for a stop inside a fire turn rests on a harness fact no fixture
measures, and that only CP8 had to measure it, after CP2-CP7 were built on it. CP1 now captures
it before CP2 starts: a new probe, P12, captured into two new fixtures, one fire turn that
stops its own wakeup and one that schedules a second wakeup first and then stops (CP1). Their
`cancelledWakeups` must be `0` and `1`. Any other observation stops CP1 and triggers a plan
amendment before CP2. The existing fixtures stay byte for byte. The settlement design is
unchanged.
Revision 13 applies round 12's local review. Its one Important finding (I1) is that
revision 12's P12 shape test counted harness answers (a successful stop, exactly one bracket
pair) as model shape, so an error or a later fire, both listed as amendment triggers, would
have been discarded and recaptured instead. The shape test now judges only the model's own
actions, and every harness answer goes to the gate. The `PROBE-DONE` marker's placement and
the linger window's start are also stated (O1). The settlement design, the checkpoints and
the requirements are unchanged.
Revision 14 applies round 13's manual external review. Its one Important finding (I1) is that
revision 13's shape test still required harness structure (a first bracket pair around one
turn, a `tool_result` answering the stop), so a fire that never came, a missing `tool_result`
or a multi-turn bracket failed the shape test and was recaptured instead of reaching the gate.
Admission now reads only the model's own `ScheduleWakeup` calls, compared with the exact
arguments each prompt names, and turn 1's reply. Everything the harness does after that is a
gate observation. One new pure module, `tests/harness_contract/p12_admission.py`, holds both
decisions, and the contract test covers a no-fire capture, a missing `tool_result` and a
two-turn bracket (CP1). Both P12 probe definitions carry `"linger": 200` explicitly (O1), and
the assertion after `completed(X)` now has the gate's scope (O2). The settlement design, the
checkpoints and the requirements are unchanged.
Revision 15 applies round 14's local review. Its one Important finding (I1) is that revision
14's `admit` judged the model's whole call sequence. So a model reacting to a harness answer
that the plan names as an observation (retrying after the exact named arguments were rejected,
or stopping again in a later fire turn) made the capture a recapture, three times over, instead
of reaching the amendment gate. Admission now stops at a cut point: the named sequence is
complete, or an exact named call is answered with `is_error`. Every call after the cut goes to
the gate. The prompts also tell the model never to retry and to call no tool in any other turn.
The contract test gains the retry and later-fire cases (CP1). The user's options after three
recaptures are stated (O1). The settlement design, the checkpoints and the requirements are
unchanged.
Revision 16 applies round 15's local review. Its one Important finding (I1) is that revision
15's own `PROBE-EXTRA` instruction made the model's correct reply to a turn it cannot identify
as ALPHA's fire (an unasked turn with no fire after it, or a fire whose prompt the model cannot
see) count as a missing call, so the capture was recaptured instead of reaching the gate. With
no cut point, admission now excuses a missing call when the model made no call after turn 1
and every reply text it wrote after turn 1 is exactly `PROBE-EXTRA`; that capture is `ADOPT`
and the gate reports the missing or unidentified fire. The gate also reports a turn before the
bracket that the plan did not ask for. The contract test gains the `PROBE-EXTRA`-only case and
its off-script counterpart (CP1). The line-number base of the P9 citation is stated (O1), and
"completes the named sequence" is defined as exact equality (O2). The settlement design, the
checkpoints and the requirements are unchanged.

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
- version bumps. A patch release is a post-acceptance release-preparation commit, as for 1.1.1;
- the Workflow defect found while this plan was amended: `/request-plan-amendment` refuses while
  a checkpoint is `IN_PROGRESS`, and no Workflow operation can abandon an `IN_PROGRESS`
  checkpoint, so a checkpoint that must stop for an amendment deadlocks. It was cleared once, by
  an operator-authorized reset. It is a Workflow Manager correctness follow-up, and nothing in
  this milestone works around it (I10).

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
  CP1's capture (`p5_p11_wakeup_fires.jsonl`) adds that every fire turn is enclosed in a
  `command_lifecycle` pair (P11 below).
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

Measured by CP1's capture script on 2026-09-25 (`claude` 2.1.282, the production argv of A with
the model overridden to haiku, or to opus where haiku lacks `--permission-mode auto`). The
fixtures are in `tests/harness_contract/`, one `<probe>.jsonl` (the redacted stream) and one
`<probe>.meta.json` (argv, exit status, per-line arrival offsets) each. Revision 7 made these the
preconditions of CP2. P8, P9 and P10 behaved as revision 7 predicted. P11's fire-turn result
contradicted it, and that contradiction is this amendment:
- **P8 -- worker-initiated stops** (`p8_task_stop`, `p8_monitor_timeout`). A `TaskStop` of the
  worker's own background Bash task emits `background_tasks_changed []`,
  `task_updated {status: killed}` and `task_notification {status: stopped}` mid-turn, and the
  same turn goes on to its own `result`. A `Monitor` that reaches its own timeout emits the same
  three events between turns, followed by a new turn (`system/init`, turn, `result` with
  `origin.kind: task-notification`). The session continues in both cases.
- **P9 -- wakeup cancellation** (`p9_wakeup_cancel`). `ScheduleWakeup {stop: true}` after a
  pending `ScheduleWakeup {delaySeconds: 60}` (harness-stated "in 107s") answers
  `{stopped: true, cancelledWakeups: 1}` in its `tool_use_result`, and its text says "cancelled
  1 pending wakeup(s)". That count is the harness's own statement of how many wakeups it still
  held, which revision 10 uses as a consistency check (B, decision 13). The capture held the
  session open for 200 s past the last event, well beyond the due time. No fire turn came, and
  no `command_lifecycle` event.
- **P10 -- the Controller's system note** (`p6_p10_slash_command`). `--append-system-prompt
  <text>` is accepted with `--input-format stream-json`. A slash command sent as the user
  message still expands exactly as in P6, and emits no `command_lifecycle` event.
- **P11 -- turn origins and the wakeup-fire bracket in streaming mode** (round 2's I2, widened
  by round 3's I2 and round 4's I1, re-scoped by round 5's I1, keyed by round 6's I1; the
  result that triggered amendment 0). Four follow-up turn kinds were captured in streaming
  mode. For each, the table gives the `result` `origin` and the events around the turn:

  | turn kind | fixture | `result.origin` | enclosing `command_lifecycle` pair |
  | --- | --- | --- | --- |
  | `ScheduleWakeup` fire, prompt `P11 fire ALPHA` | `p5_p11_wakeup_fires` lines 13-24 | **absent** | `started`/`completed`, `command_uuid` `2cde4b9e-...` |
  | `ScheduleWakeup` fire, prompt `P11 fire BRAVO second` | `p5_p11_wakeup_fires` lines 25-32 | **absent** | `started`/`completed`, `command_uuid` `6ff491e4-...` |
  | task completion (P3) | `p3_streaming_background_bash` lines 16-21 | `{"kind": "task-notification"}` | none |
  | per-event Monitor turn (P4), three turns | `p4_monitor` lines 11-13, 14-16, 20-28 | `{"kind": "task-notification"}` | none |
  | Monitor timeout (P8) | `p8_monitor_timeout` lines 21-27 | `{"kind": "task-notification"}` | none |
  | background subagent hand-back (`Agent`, `run_in_background: true`) | `p11_subagent_handback` lines 18-22 | `{"kind": "task-notification"}` | none |

  (Line numbers are 0-based stream lines.) What the fixtures show about the pair:
  - **Shape.** Each event is one line `{"type": "command_lifecycle", "command_uuid": <uuid>,
    "state": "started" | "completed", "uuid": <event uuid>, "session_id": <session>}`. It has
    no `subtype` and no `timestamp`. `uuid` differs on every event, as on every other event
    type. `command_uuid` is the one correlating field.
  - **Order.** For each fire: `command_lifecycle started(X)`, then at once (5 ms later by
    arrival) the fire turn's `system/init`, the fire turn, its `result`, and then
    `command_lifecycle completed(X)` (under 1 s after the `result`). Exactly one turn lies
    between the two. The next fire's `started(Y)` comes only after `completed(X)`, so the two
    brackets never overlap. `X` and `Y` differ, and neither recurs.
  - **The fire turn itself.** It opens with `system/init` and has **no opening `user` event**:
    no user message delivers the wakeup's `prompt`, and the turn's first event after
    `system/init` that is not `system/thinking_tokens` is an `assistant` event. Its only `user`
    events are `tool_result`s for `tool_use`s the fire turn itself issues. ALPHA's fire turn
    (lines 14-23) holds one, line 19, the `tool_result` for the `ScheduleWakeup` it issued at
    line 18 to schedule BRAVO; BRAVO's (lines 26-31) holds none (round 8's I1). The
    `ScheduleWakeup` `prompt` text appears in the stream only inside the scheduling `tool_use`
    input (lines 5 and 18), never in a `user` event. So revision 7's candidate alternative
    recogniser, "the fire turn's opening `user` event carrying the `prompt` text", does not
    exist either. The fire turn's first timestamped event is an `assistant` event: 15:38:02.412
    for ALPHA, due 15:38:00 (harness-stated "in 112s" from the 15:36:08.320 `tool_result`), and
    15:40:01.522 for BRAVO, due 15:40:00 ("in 117s" from 15:38:03.127). The BRAVO wakeup was
    scheduled *inside* the ALPHA bracket.
  - **Where it never appears.** Across all 18 fixtures, `command_lifecycle` occurs only in
    `p5_p11_wakeup_fires` (four events, two pairs). It is absent from the Controller-initiated
    first turn of every stream (the task written to stdin, including P6's slash command), from
    every `task-notification` turn above, from P8's mid-turn `TaskStop`, from P9's cancelled
    wakeup, and from the seven imported print-mode job streams.
  - **Origins.** Every non-fire follow-up turn measured in streaming mode carries
    `{"kind": "task-notification"}`, with no other field. That includes the subagent hand-back,
    which carried `peer` only in the print-mode records. Origins are recorded for diagnostics,
    and B never reads them for recognition (below).

  Context only, from before CP1: no record in the runtime root holds a wakeup fire (print mode
  kills the session first). Every `task-notification` and `peer` origin on record comes from a
  print-mode stream (`aef056f0`, `5d4a976a`; `73845184`, `2857a730`, `bad842f1`). Across every
  `worker.stdout` in the runtime root, `result.origin.kind` is absent on 73 results, `peer` on 3
  and `task-notification` on 2, and no `command_lifecycle` event occurs at all. `73845184` holds
  a background subagent and a pending `ScheduleWakeup {delaySeconds: 1800}` in one session, on a
  multi-agent route (`routing.worker_disallowed_tools` disallows `Agent` only for single-agent
  routes, `controller/routing.py:343-345`).

  Not measured, and handled fail-closed by B rather than assumed: two wakeups pending at once
  (every measured fire had one pending wakeup that was due), including whether a second
  `ScheduleWakeup` replaces the first ("Next wakeup scheduled" hints at one slot); a missing,
  duplicated, reordered or overlapping `command_lifecycle` event; a `command_lifecycle` pair
  around any turn kind other than a fire (for example an inbound `SendMessage`, or a background
  `Workflow` or forked `Skill` completion on a multi-agent route); and a `ScheduleWakeup
  {stop: true}` with no wakeup pending (whether it succeeds with `cancelledWakeups: 0` or is an
  error). Nothing in the stream identifies *which* wakeup a fire belongs to: the fire turn
  carries no `prompt` text, and neither the wakeup's scheduling `tool_use` id nor its
  `scheduledFor` value occurs anywhere in its own fire bracket (ALPHA's occur only at lines 5-6,
  BRAVO's only at lines 18-19). A fire is therefore recognised as *a* fire, and
  matched to a wakeup by time, never identified exactly (round 9's I1, decision 13).

  Also not measured by these fixtures: a `ScheduleWakeup {stop: true}` issued from inside a
  fire turn, which is how a wakeup-driven worker normally finishes. B's settlement count
  depends on it ("A stop inside a bracket"). CP1 captures it as P12, in two new fixtures,
  before CP2 starts, and a result other than the one B assumes is a plan-amendment trigger
  (round 11's I1).

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
  harness-contract breach, an overdue wakeup or an unterminated `command_lifecycle` bracket
  (decision 5), and it fails closed. The drain
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
- **I11 -- a wakeup stops being owned work only on positive evidence** (amendment 0; round 9's
  I1). A wakeup leaves owned work only when it *settles*: by a successful `ScheduleWakeup
  {stop: true}` whose reported count agrees with the Controller's (B), or when a *regular*
  `command_lifecycle` bracket that B matched to it has closed and `WAKEUP_SETTLE_SECONDS` of
  idle time (no turn, bracket or task open) have passed with no bracket contradicting the
  match (C). A matched bracket alone makes the wakeup
  `fire_matched`, which is still owned work, so no single bracket, fire or not, can make the
  worker quiescent. A `result`'s `origin`, present or absent, and whatever its `kind`, is never
  evidence either way. Any `command_lifecycle` event B cannot fit into a regular bracket matches
  nothing and makes the run's classification non-verifying (I7). An open bracket is owned work,
  so the worker is never quiescent while the harness is still inside a command.

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
- `wakeups`: every successful `ScheduleWakeup` (a `tool_use` whose paired `tool_result` is not
  `is_error`), keyed by its `tool_use` id. Its time sources are fixed (round 1's I3), and they
  are the stream's own:
  - `scheduled_at` is the paired `tool_result` event's `timestamp`;
  - the due time is `scheduled_at` plus the harness-stated `in Ns` from the `tool_result` text
    when present, else plus `clamp(delaySeconds, 60, 3600)`;
  - the tool's structured `tool_use_result.scheduledFor` (epoch milliseconds, P5) is recorded in
    `wakeups_seen` for diagnostics only. The due time keeps revision 7's sources above, so
    replay and the fake need nothing new.

  Each wakeup is in exactly one of three states (round 9's I1, decision 13), and only the last
  one is not owned work:
  - **`pending`**, from its successful `tool_result`. The "pending wakeups" below are these;
  - **`fire_matched`**, once a **regular `command_lifecycle` bracket** has been matched to it
    (below; amendment 0), at that bracket's `completed(X)`. A match is B's best judgement that
    this wakeup fired, and it is not proof: the stream never says which wakeup a fire belongs
    to (Investigation), and a regular bracket around some other turn kind would match in exactly
    the same way. So a `fire_matched` wakeup is **still owned work**. It is no longer overdue
    (C's overdue rule reads `pending` wakeups only), but it keeps the worker from being
    quiescent until it settles;
  - **`settled`**, and no longer owned, in exactly two ways:
    1. a later successful `ScheduleWakeup {stop: true}` settles every wakeup, `pending` and
       `fire_matched` alike (P9: the harness holds none after it). B compares the stop's
       `tool_use_result.cancelledWakeups` with its own *expected count*: the number of
       `pending` wakeups B holds at that moment, less the stop's provisional match, if any
       (below, "A stop inside a bracket"). If an integer count differs from the expected
       count, the harness and B disagree about what is still scheduled. That is the anomaly
       `wakeup_count_mismatch` (below): the run is row 3 `AMBIGUOUS`, and the stop still
       settles everything, since the harness now holds nothing. A higher harness count is
       exactly the misattribution round 9's I1 describes (an unrelated bracket took a wakeup
       whose real fire was still scheduled). A missing or non-integer count is recorded and
       compared with nothing;
    2. the supervisor fact `settled_wakeups` names it (C, "Settling matched wakeups"). The
       supervisor declares a `fire_matched` wakeup settled only after the worker has been idle
       (no turn, bracket or task open, the overdue rule's own predicate) for
       `WAKEUP_SETTLE_SECONDS` since its bracket's `completed(X)`, with no bracket arriving.
       If the match was wrong, the wakeup's real fire is owed inside that window by the same
       lateness bound the overdue rule already relies on, and its bracket then arrives while the
       session is still open, where B judges it (below).
  This is the only tool-specific recogniser (limitation H2);
- `command_lifecycles` (amendment 0, decision 12): the wakeup-fire bracket state, keyed by
  `command_uuid`. It is defined only from what P11 measured:
  - **Which events.** A line is a lifecycle event exactly when its `type` is
    `"command_lifecycle"`. It is *well-formed* when `command_uuid` is a non-empty string and
    `state` is exactly `"started"` or `"completed"`. No other field is read. `uuid` and
    `session_id` are per-event, and are recorded only. A lifecycle event that is not
    well-formed (missing or non-string `command_uuid`, a missing `state`, or a `state` other
    than those two) is an **anomaly** (`malformed_lifecycle_event`). It opens and closes
    nothing.
  - **Correlation.** `started(X)` opens bracket `X`. The next `completed(X)` closes it. The
    `command_uuid` is the only correlation key, and a uuid is used once: brackets are never
    matched by position, by content or across uuids.
  - **The bracketed turn.** The turn inside bracket `X` is the turn whose opening event (its
    `system/init`, or B's other turn-opening rule) comes after `started(X)` and whose `result`
    comes before `completed(X)`. Its open time is B's usual one: the `timestamp` of its first
    timestamped event. P11 measured an `assistant` event there in both fires. The turn's
    `result` `origin` is recorded in the bracket's diagnosis and never read (I11).
  - **Regular bracket.** Bracket `X` is *regular* exactly when all of these hold:
    1. `X` was never seen before `started(X)`;
    2. no turn was open at `started(X)`;
    3. no other bracket was open at `started(X)`, and none opens before `completed(X)`, so
       brackets are sequential, as measured;
    4. exactly one turn opens and closes between `started(X)` and `completed(X)`, and it is
       not the Controller-initiated first turn;
    5. that turn's open time is known;
    6. at `completed(X)` some wakeup is `pending` whose due time minus `WAKEUP_SKEW_SECONDS`
       (5 s) is at or before that open time.
    A regular bracket matches exactly one wakeup, the one with the earliest due time among
    those condition 6 admits, and it does so at `completed(X)`, never earlier (a stop inside
    the bracket fixes the candidate earlier, but the match is still decided at `completed(X)`;
    below). It moves that
    wakeup from `pending` to `fire_matched`, and nothing further: the wakeup stays owned work
    until it settles (above), so a match never by itself makes the worker quiescent (round 9's
    I1). Each measured fire meets all six conditions. For ALPHA, `started` at line 13, one turn at lines 14-23 opening at
    15:38:02.412 against a due time of 15:38:00.320, and `completed` at line 24. BRAVO, lines
    25-32, is the same.
  - **A stop inside a bracket** (round 10's I1). A worker whose wakeup has fired often has
    nothing left to wait for, and `WORKER_LIFECYCLE_NOTE` tells it to cancel what it no longer
    needs before its final turn ends. So a successful `ScheduleWakeup {stop: true}` inside a
    fire turn is the expected case, not an edge. By then the harness has already fired the
    wakeup it is running, but B has not matched it yet (the match waits for `completed(X)`).
    So B would count that wakeup as `pending` against a harness count that no longer holds it,
    and at `completed(X)` it would find nothing `pending` for condition 6. Without a rule, a
    correct worker would get two spurious anomalies. The rule:
    1. When a successful stop's `tool_result` arrives while bracket `X` is open, `X`'s
       bracketed turn is open, `X` has broken none of conditions 1-5 so far (it is the only
       open bracket, it has held only this one turn, and that turn's open time is known), and
       `X` has no provisional match yet, B fixes `X`'s *provisional match*. That is
       condition 6 evaluated at that moment, against that turn's open time: the earliest-due
       `pending` wakeup whose due time minus `WAKEUP_SKEW_SECONDS` is at or before it. There may
       be none.
    2. The stop's expected count leaves the provisional match out, because the harness has
       already fired it. Every other `pending` wakeup, including one scheduled earlier in the
       same fire turn (ALPHA's fire scheduled BRAVO, P5 lines 18-19), is counted. The stop
       then settles every wakeup as usual, the provisional match included.
    3. At `completed(X)`, conditions 1-5 are judged as usual. If they hold, condition 6 is
       met by the provisional match, and `X` is `closed_regular` with it as its match. The
       wakeup is already `settled` by `stop`, so it never passes through `fire_matched`.
       `wakeups_seen` records both the stop and `X`'s `command_uuid`. If conditions 1-5 fail,
       `X` is irregular with that condition's anomaly (row 3 already), and the provisional
       match changes nothing else.
    4. If there is no provisional candidate, nothing changes: the expected count is every
       `pending` wakeup, and at `completed(X)` condition 6 is judged as usual, so a bracket
       with nothing `pending` is still `unmatched_bracket`. A later stop inside the same
       bracket fixes nothing new: `X`'s provisional match, if any, is already `settled` and so
       no longer counted.
    Round 9's fail-closed argument is unchanged, because a provisional match is only a match
    made earlier. If `X` is the real fire of a wakeup `W` that an earlier bracket wrongly
    matched, `W` is `fire_matched`, not `pending`, so step 1 finds no candidate (or finds
    another due wakeup, which moves the argument there, as in "A wrong match cannot end the
    session"). The harness has fired `W`, and it reports `0` against B's `0`, so no count
    mismatch arises. But `completed(X)` is still `unmatched_bracket`, row 3. If instead `X` is
    a spurious bracket whose turn opened after `W`'s due time, and the stop comes from inside
    it, step 1 takes `W` as the provisional match. The harness still holds `W`, since `W` has
    not fired, so it reports one more than B expects: `wakeup_count_mismatch`, row 3. The stop
    has cancelled `W` at the harness too, so no real fire is lost.
    What the harness reports for a stop inside a fire turn is **not measured** by the
    amendment-0 fixtures: P9's stop ran in an ordinary turn. The rule assumes it reports the
    wakeups scheduled and not yet fired, which excludes the one whose fire is running, as P9's
    count was. CP1 measures this before CP2 starts, with the two P12 fixtures (round 11's
    I1): a fire turn that stops its own wakeup must report `cancelledWakeups: 0`, and a fire
    turn that schedules a second wakeup and then stops must report `1`. Any other
    observation stops CP1 and triggers a plan amendment before CP2, so CP2-CP7 are never
    built on an unmeasured count. If a later harness changes it, every such stop is
    `wakeup_count_mismatch`. The run then fails closed (row 3), never open, and the live
    probe (CP8) re-measures it. A difference is a plan-amendment trigger, like any other
    change in the bracket contract;
  - **Irregular bracket.** A bracket that breaks any condition is *irregular*. It matches
    nothing. The break is an anomaly named after the condition: `duplicate_started` (1),
    `started_mid_turn` (2), `overlapping_brackets` (3, for both brackets), `no_bracketed_turn`
    or `multiple_bracketed_turns` or `bracketed_first_turn` (4), `bracketed_turn_open_time_unknown`
    (5), `unmatched_bracket` (6). Further anomalies: `completed_without_started`, for a
    `completed(X)` with no open bracket `X`, which covers a missing `started`, a duplicated
    `completed` and a `completed` reordered before its `started` (the late `started` is then a
    `duplicate_started`). A `completed(X)` delayed past the next turn's opening is condition 4's
    `multiple_bracketed_turns`. A `duplicate_started(X)` while `X` is open opens nothing, and `X`
    stays open until its `completed(X)`. Once `X` is closed, or was seen only in a
    `completed_without_started`, a `duplicate_started(X)` opens a new bracket under the same
    uuid that is irregular from the start. Its `completed(X)` is then expected, and closes it
    without a further anomaly. **Every
    anomaly is sticky.** It is recorded in `stream_diagnosis.command_lifecycle_anomalies`
    (kind, `command_uuid` when known, stream offset), and it makes the terminal classification
    `AMBIGUOUS` (row 3, `command_lifecycle_irregular`), whatever happens later in the stream.
    Matching is the unsafe direction, because a matched wakeup can go on to settle and let the
    worker be declared quiescent and ended. So an irregular bracket never matches. A wakeup
    left pending as a result either still fires in a later regular bracket or reaches C's
    overdue rule. The same stickiness applies to `wakeup_count_mismatch`, the one anomaly that
    comes from a `ScheduleWakeup {stop: true}` rather than from a lifecycle event; it is
    recorded in the same list.
  - **Open brackets are owned work.** A bracket from `started(X)` until `completed(X)` is open,
    whether regular so far or not, and `owned_work()` includes it. So the worker is never
    quiescent between a fire turn's `result` and its `completed(X)` (under 1 s in P11), and
    never while a `started(X)` has no turn yet (5 ms in P11). A bracket that stays open with no
    turn open is bounded by C's lifecycle grace, not by the stream.
  - **Delayed events.** A late event is harmless while order holds. A late `started(X)` still
    precedes its turn, and a late `completed(X)` still precedes the next turn, so the bracket
    is regular and only holds `WAITING` longer, up to C's grace. A late event that crosses a
    turn boundary is a reordering, and is irregular as above. Print mode can reorder `result`
    events (H7), but no print-mode stream has ever held a lifecycle event, because print mode
    kills the session before any wakeup can fire. The same rules apply in both modes, so a
    lifecycle event in a print-mode stream is judged exactly as in streaming mode.
  - **Several uuids.** The map holds any number of `command_uuid`s, each with its own state:
    `open`, `closed_regular` (with the wakeup it matched), or `irregular` (with its
    anomalies). Two brackets open at once are both irregular (condition 3). They still count
    as owned work until each one's `completed` arrives.
  - **Nothing else matches a wakeup.** A turn with no bracket matches nothing, whatever its
    `origin`, its open time or its content. That includes a `task-notification` turn after the
    due time, and a turn that looks like P11's fire in every other respect. A bracket around
    any turn kind other than a fire is not assumed away. It is irregular when it matches no due
    `pending` wakeup (condition 6), which is how an unmeasured bracket source announces itself.
  - **A wrong match cannot end the session** (round 9's I1). The case B cannot tell apart is a
    regular bracket around some other turn that opens after a `pending` wakeup `W`'s due time:
    it matches `W`. Under revision 9 that emptied owned work, so the worker could quiesce and
    the Controller could close stdin before `W`'s real fire, which was then lost with nothing
    left to fail the run. Now `W` is only `fire_matched`, still owned, so the worker stays
    `WAITING`. `W`'s real fire is still scheduled at the harness and already due, so it
    arrives inside `W`'s settle window. Its bracket finds no `pending` wakeup that condition 6
    admits (`W` is no longer `pending`), so it is `unmatched_bracket`, row 3, and the run fails
    closed. If another due wakeup `W2` is `pending`, the real fire matches `W2` instead, and the
    same argument moves to `W2`, which settles no earlier than its own window. A worker that
    cancels in the meantime (`ScheduleWakeup {stop: true}`), whether after the spurious bracket
    or from inside it ("A stop inside a bracket"), exposes the wrong match through the
    harness's count (`wakeup_count_mismatch`). Either way a wrong match ends `AMBIGUOUS`, never
    in a session ended early. What remains is H2's double-breach residue;
- `owned_work()`: open tasks, `pending` and `fire_matched` wakeups, and open
  `command_lifecycle` brackets;
- `quiescent()`: no turn open, at least one `result`, `owned_work()` empty, and the last
  `result`'s `queued_turn_count` absent or `0` (round 1's O4: a turn already queued is never
  declared quiescent).

The state is a pure function of the lines and the supervisor facts. No wall clock enters it.
`command_lifecycle` events carry no `timestamp` (P11), so nothing about a bracket's timing is
read from the stream beyond its bracketed turn's open time. The three supervisor facts that
depend on time, "a wakeup was declared overdue", "a `command_lifecycle` bracket was declared
unterminated" and "these `fire_matched` wakeups were declared settled" (`settled_wakeups`, C),
are passed in as supervisor facts. All three are persisted with `ENDING`. A supervisor reaches
a *quiescent* `ENDING` only once every `fire_matched` wakeup has settled, so a settlement
declared earlier and lost with its Controller is simply declared again after a re-attach (E).
The breach paths (an overdue wakeup, an unterminated bracket; C) end the session "as in
`ENDING`" without waiting for settlement, so there a `fire_matched` wakeup may still be
unsettled at exit. The run is already row 4, and that wakeup adds row 7 to `secondary_reasons`
(round 10's O1). A re-attach after such an `ENDING` replays the same flushed facts and reaches
the same classification. A replay with
those facts reaches the same state.

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
  `--timeout` fired, whether it declared a wakeup overdue or a `command_lifecycle` bracket
  unterminated (C), which `fire_matched` wakeups it declared settled (`settled_wakeups`, C),
  and the stream offset at which it
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
| 3 | a line is not one JSON object, or there is no `result` at all, or the stream holds any `command_lifecycle` anomaly or `wakeup_count_mismatch` (B) | `AMBIGUOUS` | `malformed_line` / `no_result` / `command_lifecycle_irregular` |
| 4 | the supervisor declared a pending wakeup overdue, or a `command_lifecycle` bracket unterminated (C) | `AMBIGUOUS` | `wakeup_not_delivered` / `command_lifecycle_unterminated` |
| 5 | `streaming` mode only: the process exited, or the stream reached EOF, with no `ending_offset` (the supervisor never ended the session: the anchor was lost) | `AMBIGUOUS` | `stdin_closed_while_waiting` |
| 6 | the stream ended with a turn open | `AMBIGUOUS` | `exited_mid_turn` |
| 7 | a task reached `killed`/`stopped` **after** `ending_offset`, or `owned_work()` is non-empty at exit (an open task, a `pending` or unsettled `fire_matched` wakeup, or an open `command_lifecycle` bracket) | `AMBIGUOUS` | `owned_work_killed_at_exit` |
| 8 | the last `result` has `is_error: true`, or lacks `session_id`/`is_error` | `FAILURE` / `AMBIGUOUS` | `result_is_error` / `result_incomplete` |
| 9 | otherwise: a quiescent terminal turn, then an exit after the supervisor ended the session (`streaming`), or after the last `result` (`print`) | `SUCCESS` | `quiescent_terminal_turn` |

`stream_diagnosis.secondary_reasons` lists, in row order, every later row that also matched, so
an overdue wakeup whose session end then killed a task records `wakeup_not_delivered` with
`owned_work_killed_at_exit` beside it. Row 3's `command_lifecycle_irregular` sits with the other
malformed-stream reasons, because an anomaly is a break in the measured protocol and not a
consequence of anything the supervisor did. A worker whose session ended with a bracket still
open, whether through the anchor's loss or after `ENDING`, is row 5 or row 7, and is not an
anomaly. A bracket that opens after `ENDING` has no pending wakeup to match, because `ENDING`
required none (`quiescent()`). If it closes, it is `unmatched_bracket` (row 3). If it stays open,
it is row 7. Row 7 is positional (round 1's I2): a task the worker
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
`harness_stated`/`clamp`, the harness's `scheduledFor` when present, its final state, and how
it got there: the matching bracket's `command_uuid` for `fire_matched`, and for `settled` either
`settle_window` (with that `command_uuid`) or `stop` (with the stop's reported
`cancelledWakeups` and B's expected count, and, when the stop came from inside a bracket that
matched this wakeup provisionally, that bracket's `command_uuid`); or `pending`), `command_lifecycles` (per
`command_uuid`: its `started`/`completed` offsets, its state `open`/`closed_regular`/`irregular`,
the bracketed turn's index, open time and `origin` as recorded, and the wakeup it matched),
`command_lifecycle_anomalies` (B), `unknown_events`, and, per `result`, its
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
     the `pending` wakeups and their due times, each `fire_matched` wakeup still settling (the
     matching bracket's `command_uuid` and the time left in its settle window, by this
     supervisor's clock), and each open `command_lifecycle` bracket (its `command_uuid`,
     whether its turn has been seen, and since when, by this supervisor's clock, no turn has
     been open inside it);
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

**Overdue wakeups.** A `pending` wakeup that is `WAKEUP_GRACE_SECONDS` (300) past due, while no
turn opens and no task is open, is a harness-contract breach (P5 measured a 26 s lateness at
60 s). Other wakeups, `pending` or `fire_matched`, do not defer it. The supervisor flushes
`wakeup_overdue_declared_at` with `ENDING`, ends the session as in `ENDING`, and row 4 of B
classifies the run `AMBIGUOUS`. This is decision 5. The fire of a wakeup whose bracket B found
irregular does not match it (I11), so that wakeup reaches this rule in the same way. The run is
already `AMBIGUOUS` by row 3, and this rule only bounds how long it waits.

**Settling matched wakeups** (round 9's I1, decision 13). A `fire_matched` wakeup is owned work
until it settles (B). The supervisor keeps one *settle timer* per `fire_matched` wakeup, by its
own clock. It starts when the supervisor consumes the matching bracket's `completed(X)`, and it
runs only while the worker is idle by the overdue rule's own predicate, plus brackets: no turn
open, no task open and no bracket open (round 10's I2). A turn or a bracket that opens
restarts it from zero when it closes, because that turn or bracket may be the one that decides
the match (the real fire of a wrongly matched wakeup). A task that opens restarts it too, from
zero once no task is open. That costs nothing: an open task already keeps the worker from
being quiescent. It matters because the overdue rule declares nothing while a task is open,
so the harness's lateness bound, which the window is sized from, is only ever enforced over
task-free idle time. A timer that ran during a long task could settle a wrongly matched
wakeup whose real fire the harness is still entitled to defer. The fire could then follow the
task's completion turn as a turn not yet queued, after the worker had already gone quiescent. When a timer reaches
`WAKEUP_SETTLE_SECONDS`, the supervisor declares that wakeup settled: it adds the wakeup's
`tool_use` id to its `settled_wakeups` fact and feeds the fact to B. Nothing is flushed then.
`settled_wakeups` is flushed with `ENDING`, like the other two time facts. A supervisor
reaches a quiescent `ENDING` only once every `fire_matched` wakeup has settled, by its window
or by a stop. On the breach paths (the overdue rule, an unterminated bracket) it ends the
session without waiting, and an unsettled `fire_matched` wakeup is then row 7, secondary to
row 4 (B; round 10's O1).

`WAKEUP_SETTLE_SECONDS` is `WAKEUP_GRACE_SECONDS + WAKEUP_SKEW_SECONDS` (305 s). That makes the
window exactly as long as the lateness the overdue rule already accepts. Condition 6 admits a
match only when the matched wakeup's due time is at most `WAKEUP_SKEW_SECONDS` after the
bracketed turn opened, which is before `completed(X)`. If the match was wrong, the wakeup is
still scheduled at the harness and already due, and the harness owes its fire within
`WAKEUP_GRACE_SECONDS` of idleness past that due time, the bound whose breach the overdue rule
declares. Idleness means the same thing in both rules: no turn and no task open (the settle
timer also waits out open brackets, which only lengthens it). The timer measures one
unbroken idle stretch of 305 s starting after `completed(X)`, which is after the due time
minus the skew, so it holds at least `WAKEUP_GRACE_SECONDS` of idleness past the due time. So
the real fire, and with it B's `unmatched_bracket` (or, if another due wakeup is
`pending`, the next match, which gets its own window), always arrives inside the window, while
the session is still open. The window ends the session on no time of its own: it only decides
when a match counts as settled, and the session still ends only at a quiescent turn.

A worker that follows `WORKER_LIFECYCLE_NOTE` cancels any wakeup it no longer needs with
`ScheduleWakeup {stop: true}` before ending, and a successful stop settles every wakeup at
once, so it never waits for a window. That includes a stop from inside the fire turn itself
(B, "A stop inside a bracket"). A worker that lets a wakeup fire and then ends with no
stop is correct and pays `WAKEUP_SETTLE_SECONDS` of idle time before `ENDING`. This is the
same kind of accepted delay as decision 11's, and README documents it (CP8).

**Unterminated `command_lifecycle` brackets** (amendment 0, decision 5). The stream cannot time
a bracket, because its events carry no `timestamp`. So the supervisor times it by its own clock,
as it does the overdue rule. A bracket is *stalled* while it is open and no turn is open: before
its turn's `system/init`, or after its turn's `result`, still waiting for `completed(X)`. The
bracketed turn itself is never timed, since a fire turn may run as long as any other turn. P11
measured both stalled gaps at 5 ms and under 1 s. A bracket stalled for
`COMMAND_LIFECYCLE_GRACE_SECONDS` (300), while no task is open, is a harness-contract breach,
like an overdue wakeup. A pending wakeup does not defer it, because the bracket that should
match that wakeup is the one that stalled. The supervisor flushes `command_lifecycle_overdue_declared_at` and the
bracket's `command_uuid` with `ENDING`, ends the session as in `ENDING`, and row 4 classifies the
run `AMBIGUOUS` (`command_lifecycle_unterminated`). While a task is open, a stalled bracket only
keeps the worker `WAITING`, as a pending wakeup does. The stall timer starts afresh whenever the
bracket's state changes (its turn opens or closes), and at a re-attach (E). It is never carried
across Controllers, so a re-attaching supervisor may wait longer than the grace, and never
shorter. That keeps the stream state pure and the declaration a supervisor fact. Settle timers
follow the same rule: a re-attaching supervisor starts one afresh for each `fire_matched`
wakeup it finds after replay, so it may settle later than the lost Controller would have, never
earlier.

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
  worker whose anchor was lost reaches `DRAINING`/`ENDED` without one). A record carrying
  `wakeup_overdue_declared_at`, `command_lifecycle_overdue_declared_at` or a non-empty
  `settled_wakeups` must also carry `ending_offset`, because all three are flushed with
  `ENDING` (C). The second must name its `command_uuid` as a non-empty string (amendment 0), and
  `settled_wakeups` must be a list of distinct non-empty strings (round 9's I1). A violation is `StaleJobRecordError`, like every other case-3 breach.
- `worker_state.waiting_on` carries `tasks`, `wakeups` and `command_lifecycles`, the open
  brackets of C's `details` (amendment 0). Each `wakeups` entry names its state, `pending` (with
  its due time) or `fire_matched` (with the matching `command_uuid` and the settle time left,
  round 9's I1). It is presentation data, rewritten at each state
  change, and never an input to the stream state, which replay rebuilds (E).
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
   supervisor facts (`ending_offset`, `wakeup_overdue_declared_at`,
   `command_lifecycle_overdue_declared_at`, `settled_wakeups`). Replay is deterministic, because the state is a
   pure function of the lines and those facts (B). That includes every `command_lifecycle`
   bracket (amendment 0). A bracket `started` before the Controller was lost is open again after
   replay, with the same `command_uuid`, and is completed by the `completed(X)` the live stream
   later brings. A bracket both opened and closed while no Controller was attached is judged
   from the stream alone, exactly as live supervision would have judged it: regular brackets
   match their wakeups, and anomalies stay anomalies. Replay reads only complete lines, so a
   Controller lost mid-line never splits a lifecycle event. The only things re-attach cannot
   recover are the lost Controller's timers, so each open bracket's stall timer, and each
   unsettled `fire_matched` wakeup's settle timer (round 9's I1), starts at re-attach (C). A
   `fire_matched` wakeup that the lost Controller had already settled, but had not yet flushed
   with `ENDING`, is therefore settled again one full window later, never earlier;
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
   - an open bracket at re-attach is owned work like any other (B). With the worker alive,
     supervision continues and the bracket completes or stalls (C). With the worker gone, it is
     classified by B's rows as for any exit with owned work (row 5 with no `ending_offset`, else
     row 7), never assumed to have completed;
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
| `waiting` | `WAITING` | `worker pid P waiting on 1 background task (b1b5d6hjp "Run the full test suite", 6:10) and 0 wakeups`; with an open bracket (amendment 0) also `and 1 harness command (command_uuid 6ff491e4, no turn yet \| turn ended, awaiting completion; stalled 0:04 of 5:00)`; with a `fire_matched` wakeup (round 9's I1) also `and 1 wakeup presumed fired, not yet settled (matched by harness command 2cde4b9e; settles in 4:12 unless another harness command contradicts it; ScheduleWakeup stop:true settles it now)` |
| `draining` | `ENDING`/`DRAINING` | `worker ended; 2 owned processes still running (pids 4101, 4107)`; after a drain detach (C step 3) also `detached after 10:00 -- end them, then workflow-controller resume <repo>`; recognised daemons left running are listed as `not owned` |
| `unsupervised` | any of the above with the supervisor lock `unattached` | the line above plus `no Controller attached -- workflow-controller resume <repo> re-attaches` |
| `pending reconciliation` | `COMPLETED`, or `LAUNCHED` with no owned work | `worker ended (SUCCESS); pending reconciliation -- workflow-controller resume <repo>` |
| `terminal` | terminal status | as today |

- `status`: each non-terminal job's line uses the presenter, with `follow:` as today.
- `inspect`: gains a `jobs:` block, one line per non-terminal job for the target, beside
  `lifecycle lock:`. It stays read-only.
- `explain`: `_print_pending_jobs` appends the activity to each pending job. `--json` gains
  `pending_jobs[].activity`, `worker_state`, `waiting_on` (including `command_lifecycles`) and
  `owned_processes`.
- **Unresolved lifecycle pairs** (amendment 0). Each surface shows an open bracket as above. A
  bracket's stall time is known only to an attached supervisor, so an `unsupervised` record shows
  the bracket with `stall time unknown (no Controller attached)`, never a number. The same holds
  for a `fire_matched` wakeup's settle time, shown as `settle time unknown (no Controller
  attached; restarts on resume)`. A worker held only by a `fire_matched` wakeup is never
  presented as quiescent or finished: the `waiting` line says the wakeup is presumed fired and
  not yet settled, because the harness does not say which wakeup a fire belongs to (round 9's
  I1). `explain` also prints `wakeup_count_mismatch` with both counts. For a
  terminal or `COMPLETED` record, `explain` (text and `--json`) prints
  `stream_diagnosis.command_lifecycle_anomalies` as one line per anomaly (kind, `command_uuid`,
  stream offset), and each wakeup's final state and how it got there (`fire_matched` with its
  `command_uuid`; `settled` by `settle_window` or by `stop` with both counts, and with the
  bracket's `command_uuid` when a stop inside a fire turn settled its own match; `pending`). It also
  renders `command_lifecycle_irregular` and `command_lifecycle_unterminated` beside the
  existing reasons, with the offending `command_uuid`. An operator can therefore tell "the
  harness never closed this command", "the harness produced a pair the Controller could not
  match" and "the harness still held a wakeup the Controller had matched" from a wakeup that
  simply never fired.
- `follow`: the job loop ends only at a terminal record. While `WAITING`, it keeps following and
  prints the waiting heartbeat. When the supervisor is gone, it says so and names `resume`
  (replacing "worker exited; job X awaits resume" for records that carry `worker_state`).
  `normalise` renders `task_started`/`task_notification`/`background_tasks_changed` as compact
  `background task` lines, `command_lifecycle` events as `harness command <uuid8> started` /
  `completed` lines, and the new job events through `_job_text`.
- Every presenter is read-only. `follow` stays presentation-only (roadmap 1.3 rule).

## Harness limitations (documented, not solved here)

These need the future Harness Adapter Protocol, or OS-level containment, to close. Each is
pinned by a CP1 fixture or probe, so a harness change surfaces as a failing contract test, not as
a silent behaviour change.

- **H1 -- the ownership model depends on streaming input.** Only
  `--input-format stream-json` with stdin held open keeps a session alive across background
  work (P1 vs P3). This behaviour is measured, not documented by the harness. The opt-in live
  probe (CP1/CP8) re-measures it against the installed `claude`.
- **H2 -- scheduled wakeups are invisible to the task stream** (P5). Scheduling is recognised by
  tool name from `tool_use`/`tool_result`, the one tool-specific rule in `worker_stream`. A fire
  is recognised only by its `command_lifecycle` bracket (P11, amendment 0), an event the harness
  does not document. Its name, fields, values and placement are pinned by the P5/P11 fixture and
  re-measured by the opt-in live probe. The stream never says *which* wakeup a fire belongs to,
  so a fire is recognised as a fire and matched to a wakeup by time; that match is never
  treated as proof (B's `fire_matched`, round 9's I1). The bracket's failure modes are not
  symmetric:
  - if the harness drops, renames or reshapes the pair, fires stop matching wakeups, each
    pending wakeup reaches the overdue rule, and the run fails closed (`AMBIGUOUS`). A
    lifecycle event with a changed `state` vocabulary or a missing `command_uuid` is an anomaly,
    and fails the run closed at once (row 3);
  - if the harness starts bracketing some *other* turn kind, a bracket that matches no due
    wakeup is `unmatched_bracket` (row 3). A non-fire bracket whose turn opens after a pending
    wakeup's due time does match that wakeup, which B cannot prevent. It cannot end the
    session, though: the wakeup is only `fire_matched` and stays owned work until it settles
    (C, decision 13). Its real fire is already due, so it arrives inside the settle window,
    while the session is open, and its bracket is `unmatched_bracket` (row 3). A stop in the
    meantime exposes the wrong match through the harness's own count
    (`wakeup_count_mismatch`, row 3). Either way the run fails closed, and never through a
    session ended before the fire;
  - **the double-breach residue.** The settle window is sized by the lateness the overdue rule
    accepts (`WAKEUP_GRACE_SECONDS`, plus the match skew), and it is measured over the same
    idle time: no turn and no task open (round 10's I2). A fire the harness defers while a task
    runs therefore never counts against the window. A wrong match is missed only when
    the harness *both* brackets a non-fire turn after a due wakeup *and* then delays that
    wakeup's real fire past the grace of task-free idle time, with no stop issued in between. The second is the very
    breach the overdue rule declares; here nothing is left pending to declare it on. Both are
    unmeasured, and the live probe re-measures the first (CP1). This is the only way an
    unrelated bracket can still lead to `ENDING` before a real fire, and CP3 pins it as a
    documented residue rather than leaving it implicit;
  - two wakeups pending at once were not measured. If the harness fires only the later one
    (for example, because a second schedule replaced the first), that fire matches the earlier
    (earliest-due rule, B), the later one reaches the overdue rule and fails closed, and a stop
    in the meantime reports a count that disagrees (`wakeup_count_mismatch`).
  A renamed scheduling tool, or a wakeup scheduled some other way, would be missed entirely:
  nothing would be owned, the worker would look quiescent, the Controller would end it at its
  idle turn, and the wakeup would be lost. Ownership cannot close that gap, because nothing in
  the stream says a wakeup exists. It is guarded only by the pinned tool name (P5, P7's
  inventory) and the live probe, which fails when `ScheduleWakeup` changes. Workflow
  reconciliation is not a substitute for it (round 9's I1): where F's same-phase predicate
  verifies (`IMPLEMENTING -> IMPLEMENTING`), a worker ended before such an unseen wakeup would
  be judged on durable state like any quiescent worker. That is why the recogniser's own
  guarantee, not reconciliation, is what this plan relies on for every wakeup it *can* see.
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
  per-turn `result` placement (B). The one ordering B does rely on is the `command_lifecycle`
  bracket's (`started`, one turn, `completed`), which was in order in both captured fires. A
  bracket out of that order is irregular and fails closed (B). One known false-`AMBIGUOUS`
  source follows (round 8's O3): a `task-notification` turn that the harness opens between a
  fire's `result` and its `completed(X)` puts two turns in the bracket, which is
  `multiple_bracketed_turns`. P11 measured that gap at 0 ms, so it is unlikely, but an operator
  who sees `command_lifecycle_irregular` next to a background task that ended at the same
  moment should suspect it before suspecting the harness.
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
| CP1 | Harness contract evidence and fake harness: the measured claude 2.1.282 stream-json-input contract recorded as redacted fixtures (the real failing jobs' stream tails, background Bash, Monitor, ScheduleWakeup, slash-command and escaped-descendant probes), adopted byte for byte from the CP1 capture, with the wakeup fire's command_lifecycle started/completed bracket pinned as the fire's only recogniser evidence, tests/fake_claude.py streaming-input mode (scripted turns, background tasks as real tagged processes with task events, wakeups whose fires are enclosed in the measured command_lifecycle bracket with injectable bracket faults, kill-at-EOF, escaped descendants, no descriptor inheritance by tool processes), the stop-inside-a-fire probe (P12) captured into two new fixtures whose cancelledWakeups counts must match the settlement rule before CP2 starts (any other count is a plan-amendment trigger), and the opt-in live contract probe | - | 3 | 1 |
| CP2 | Worker stream state machine: controller/worker_stream.py, a pure incremental reader tracking turns, owned background tasks, pending wakeups, command_lifecycle brackets correlated by command_uuid (a wakeup matched only by a regular bracket whose turn opens at or after its due time, never by result origin, and still owned until a count-checked stop or a settle window with no contradicting bracket settles it) and quiescence, with terminal classification into the closed four outcomes plus a structured stream_diagnosis (several results accepted, owned work killed at exit, exit while waiting, overdue wakeup, irregular or unterminated command lifecycle, malformed stream); replaces worker._parse_worker_stream | CP1 | 3 | 1 |
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

<!-- CP1 -->
### CP1 -- harness contract evidence and fake harness

Files: `tests/harness_contract/` (new: redacted `.jsonl` fixtures, a `README` naming each
fixture's source, and `p12_admission.py`), `tests/fake_claude.py`, `tests/test_fake_claude_contract.py` (new),
`tests/test_integration_disposable_repo.py` (the opt-in live probe).

**The measured fixtures already exist** (amendment 0). CP1's first run captured them into the
still-untracked `tests/harness_contract/`: `capture.py`, the seven imported job streams and
P1-P6 and P8-P11, 18 `.jsonl` and 18 `.meta.json` files. CP1 adopts them byte for byte, and
commits them as its evidence. It never re-captures, regenerates, edits or deletes one to fit
this design. The one exception is a later, separate re-measurement for a new `claude` version,
which is out of this milestone. CP1 adds the `README`, and records each fixture's SHA-256 in it.
A contract test checks those digests, so any later change to a fixture is a visible test
failure, not a silent re-baseline. `capture.py` may change only in its `pins` descriptions and
its `README` cross-references. Its probe definitions stay as they were when the fixtures were
captured. The one addition is P12's two new probe definitions (round 11's I1, below); no
existing definition changes. P12's admission and gate rules live in a new module beside it,
`tests/harness_contract/p12_admission.py`, not in `capture.py` (round 13's I1). The fixture list and every bullet below are otherwise revision 7's.

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
    it, so H1's measurement covers the argv the Controller actually sends (round 1's O7). P8-P11 were the preconditions the Investigation lists. They are
    now measured (Investigation), and P11's fire-turn result is what amendment 0 resolves. The
    contract that CP2 relies on from P11 is the `command_lifecycle` bracket (B), not an
    `origin`. **The amendment's stop trigger stays**, re-keyed on the bracket. If the live
    probe (below), or any re-measurement, finds a fire turn that is not enclosed in exactly one
    `started`/`completed` pair with one shared `command_uuid`, or finds a pair around a
    non-fire turn kind, that is a new plan-amendment trigger, not an implementation-time
    judgement.
- **P12 -- a stop inside a fire turn, captured before CP2** (round 11's I1). This is the one
  harness fact B's settlement count relies on that the amendment-0 fixtures do not hold (B,
  "A stop inside a bracket"). CP1 captures it itself, with the capture script and argv above,
  into two **new** fixtures beside the existing ones, never by editing one:
  - `p12_stop_inside_fire_single`: schedule wakeup ALPHA (`delaySeconds` 60) and end the turn;
    when ALPHA fires, call `ScheduleWakeup {stop: true}` in that fire turn, then end it. B's
    assumption: `cancelledWakeups: 0`, because the harness has already fired ALPHA and holds
    nothing else. This also measures a stop with no wakeup pending, which the Investigation
    lists as unmeasured;
  - `p12_stop_inside_fire_nested`: the P5 shape. Schedule ALPHA and end the turn; when ALPHA
    fires, schedule BRAVO (`delaySeconds` 60) and then call `ScheduleWakeup {stop: true}` in
    that same fire turn, then end it. B's assumption: `cancelledWakeups: 1` (BRAVO, not ALPHA).
  **Probe definitions** (round 13's O1). Each prompt names every `ScheduleWakeup` argument the
  model is to pass, exactly: ALPHA `{delaySeconds: 60, prompt: "P12 fire ALPHA", reason:
  "contract probe alpha", noop: false}`, BRAVO `{delaySeconds: 60, prompt: "P12 fire BRAVO",
  reason: "contract probe bravo", noop: false}`, and the stop `{stop: true}`. `noop` is named
  because the harness rejected P9's first call for leaving it out (`p9_wakeup_cancel.jsonl`
  0-based line 8, the `tool_result` answering line 7's call); P5's model added `noop: false` unasked, and P9's accepted stop was `{stop: true}`.
  Both definitions carry `"linger": 200` as an explicit key. `run_probe` reads
  `float(probe.get("linger", 0))`, and P5's definition has no `linger` key, so a P12 definition
  copied from P5 without it would close stdin at `PROBE-DONE` and see no later fire by
  construction.
  Each prompt also tells the model to make each named call exactly once, and never to retry or
  change its arguments, whatever a tool returns. In any turn other than the two named ones, the
  model replies with exactly `PROBE-EXTRA` and calls no tool (round 14's I1). This makes a
  reaction to a harness answer unlikely. What keeps such a reaction out of admission is the cut
  point below, not the prompt.
  Each probe's prompt puts `PROBE-DONE` in the final reply of the fire turn, and in no other
  reply: the first turn ends with `WAITING-ALPHA`, as P5's prompt does. `capture.py`'s
  watchdog starts its `linger` (200 s) from the first `result` whose text holds `PROBE-DONE`
  (`done_at`), so the window runs 200 s from the fire turn's `PROBE-DONE` `result` (round 12's
  O1). A marker in the first turn would start the window before ALPHA fires, and no marker at
  all would run the probe to `max_seconds` (600 s), a longer window. 200 s is well past
  BRAVO's 60 s delay, so the capture also shows whether any later fire or `command_lifecycle`
  event arrives.
  **Admission reads the model's actions only, up to the cut point** (round 12's I1, round
  13's I1, round 14's I1).
  `p12_admission.admit(probe_name, events)` returns `RECAPTURE` with its reasons, or `ADOPT`.
  It reads what the model controls: the model's `tool_use` events with their names and inputs,
  the text of turn 1's reply (turn 1 is every event up to the first `result`), and, after turn
  1, only whether each `text` block of the model's `assistant` events is exactly `PROBE-EXTRA`
  (surrounding whitespace ignored; round 15's I1). It reads one
  harness fact, and only to find the cut point: the `is_error` flag of a `tool_result` whose
  `tool_use_id` answers an *exact named* call. It never reads a `command_lifecycle` event, any
  other `user` event, a `tool_result`'s content or `tool_use_result`, a `cancelledWakeups`, an
  exit status, or where the harness put a turn boundary after turn 1.
  The **named sequence** is `[ALPHA, stop]` in the single probe and `[ALPHA, BRAVO, stop]` in
  the nested one, each with the prompt's exact arguments. The **cut point** is the first of:
  the model's call that completes the named sequence, meaning the call after which the model's
  calls so far, in stream order, *equal* the named sequence exactly (not merely contain it as
  a subsequence; round 15's O2); or an exact named call that a
  `tool_result` answers with `is_error: true`. The model's actions up to and including the
  cut-point call are admission input. Everything after it, calls and reply text alike, is not:
  from there on the model is reacting to a harness answer (a rejection, or a turn the plan did
  not ask for), and `gate_deviations` reports it. With no cut point, every model action in the
  capture is admission input. A capture is `RECAPTURE` exactly when the model's admission
  input does not do what the prompt asked:
  - turn 1's calls are not exactly one `ScheduleWakeup` call with ALPHA's named arguments, or
    turn 1's reply holds `PROBE-DONE`. Either is judged only if it lies before the cut point:
    after ALPHA's exact call is rejected, a retried ALPHA or a changed reply is a reaction;
  - the harness produced at least one `assistant` event after turn 1, and the model's calls
    after turn 1 and up to the cut point, in stream order and whichever turns the harness put
    them in, are not a prefix of the rest of the named sequence ending at the cut point. With
    no cut point, they are not the whole rest, except in the `PROBE-EXTRA`-only case below. An
    extra, missing, reordered or differently argued call before the cut point, or a call to any
    other tool there, is the model's failure. A missing call is a failure only when there is no
    cut point: after the exact BRAVO is rejected, a missing stop is the harness's doing.
    **The `PROBE-EXTRA`-only case** (round 15's I1). With no cut point, if the model made no
    call at all after turn 1, and it wrote at least one `text` block after turn 1 and every
    such block is exactly `PROBE-EXTRA`, the missing calls are not the model's failure. The
    model did what the prompt says for a turn that is not a named one. The turn it answered was
    either not a fire (an unasked turn, with ALPHA never firing after it) or a fire it could
    not identify as ALPHA's (for example, a `claude` that no longer shows the wakeup's `prompt`
    to the model; P11 already shows that text is absent from the stream). Both are harness
    behaviour, so the capture is `ADOPT`, and `gate_deviations` reports the missing bracket, or
    the bracketed turn with no stop in it. Any other reply after turn 1 with no call, whether
    free text (including `PROBE-DONE`), an empty reply with no `text` block, or a call to
    another tool, stays the model's failure. So a delivered fire turn answered without the stop
    is still `RECAPTURE`. Admission reads no turn boundary after turn 1, so it judges the
    replies after turn 1 together: a fire turn with an empty reply followed by a `PROBE-EXTRA`
    turn is `ADOPT`. That is the stated trade's direction below: the error is sent to the
    amendment, where it is visible, never to a recapture.
  **The trade this cut takes, stated** (round 14's I1). A spontaneous extra call *after* the
  named sequence completes, for example a second stop in the same fire turn with no rejection
  in between, is past the cut point. It is therefore reported by `gate_deviations` and goes to
  the amendment, not to a recapture. Admission could only tell that call from a reaction to a
  harness answer by reading more of the harness's output (the stop's result content, turn
  boundaries after turn 1), which is what rounds 12 and 13 removed. The two errors are not
  equal. A model call sent to the amendment by mistake is visible and recorded, and the user
  sees it at the amendment gate as a model call. A harness answer sent to a recapture by
  mistake is hidden, and a transient one is replaced by a clean run. The cut takes the first
  error and never the second. The alternative, judging only the shortest prefix that completes
  the named sequence without reading any `is_error`, makes the same trade after completion.
  But it leaves a retry *before* completion (a rejected BRAVO retried, then the stop) to be
  judged against the named sequence, which is the defect itself. So it is not taken.
  Everything else is harness behaviour, and the capture is `ADOPT` whatever it shows. That
  includes: ALPHA never firing, with no `command_lifecycle` event at all (no `assistant` event
  after turn 1, so the model had no turn to act in); a turn after turn 1 that the model
  answered `PROBE-EXTRA` with no call, whether or not a fire follows it (the
  `PROBE-EXTRA`-only case above); no pair, or a pair enclosing zero, two or
  more turns, around the model's later calls; malformed, unmatched, duplicated or reordered
  lifecycle events; a stop or BRAVO `tool_use` that no `tool_result` answers; any `is_error`
  `tool_result` on an exact named call, including the rejection itself (those arguments are
  this plan's contract, so the harness refusing them is a contract observation, not a model
  mistake), and whatever the model does after it; a missing, non-integer or unexpected
  `cancelledWakeups`; any later event or turn, and whatever the model does in it; and a
  non-zero exit of `claude`. A `RECAPTURE` capture is discarded, its reasons go in the
  `README`, and the probe is run again. After three consecutive `RECAPTURE`s of one probe, CP1
  stops and reports the prompt, the three captures' reasons and the model's calls to the user
  instead of running it a fourth time. **The user's options there** (round 14's O1): reword the
  prompt without changing anything the plan names (the exact arguments, the named sequence,
  the markers `WAITING-ALPHA`/`PROBE-DONE`/`PROBE-EXTRA`, the `"linger": 200` key and the argv).
  That is a CP1 implementation change, and the count of consecutive recaptures restarts at zero
  for the reworded prompt. Or change any of those named items, which is a change to this plan's
  contract and goes to `/request-plan-amendment` first. Either way, the `README` keeps every
  discarded attempt with its reasons and the prompt text it ran under, across every rewrite.
  An `ADOPT` capture is committed as that probe's fixture, its SHA-256 goes in the `README`
  like every other fixture's, and that probe is not run again in CP1. So nothing the harness
  did, and nothing the model did in reaction to it, can make a capture be re-run. A transient
  harness fault cannot be replaced by a later clean run.
  **The gate.** `p12_admission.gate_deviations(probe_name, events)` returns every way an
  adopted capture differs from the contract the P12 contract-test sub-bullet (below) pins, and
  an empty list is the only pass. CP1 is complete only when both P12 fixtures are adopted and
  committed and both lists are empty. A non-empty list stops CP1 there: the deviations, and
  the bracket and turn each came from, are recorded in the `README`, and the work item goes to
  `/request-plan-amendment` before CP2 starts. That is the same trigger as the bracket
  contract's, and it is not an implementation-time judgement. The `README` keeps the two
  outcomes apart: a recaptured model mistake, with its reasons, and an adopted harness
  observation, with its deviations.
  The opt-in live probe re-runs both P12 probes at CP8 as a final re-measurement. It is not
  the first measurement.
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
    - `wakeup {delay, fire_turn}` and `wakeup_stop`, which emit the `ScheduleWakeup`
      `tool_use`/`tool_result` pair with a `timestamp`, the harness-stated `in Ns` text and a
      `tool_use_result` (`scheduledFor`, `clampedDelaySeconds`, `wasClamped`; `{stopped: true,
      cancelledWakeups: n}` for a stop), as P5 and P9 measured. The fake's `n` is its own count
      of wakeups scheduled and not yet fired, the truth the harness reports, never the
      Controller's view, so a test can make the two disagree (round 9's I1). A wakeup counts
      as fired from the moment the fake emits its `started(X)`, so a `wakeup_stop` inside a
      fire turn does not count the wakeup being fired (B's assumption for a stop inside a
      bracket, which CP1's P12 fixtures measure; round 10's and round 11's I1). The **fire** (amendment 0) is
      played exactly as P11 measured it: `{"type": "command_lifecycle", "command_uuid": <fresh
      uuid4>, "state": "started", "uuid": ..., "session_id": ...}`, then the fire turn, then
      the matching `completed` event with the same `command_uuid`. The fire turn opens with
      `system/init` and no opening `user` event; its first non-`system` event is a timestamped
      `assistant` event, and it ends with a `result` with **no `origin`**. Its body is the optional
      `fire_turn`, a list of the same steps as any scripted turn (default: one `text` step),
      played *inside* the bracket, so a fire turn can call tools. A tool step there emits its
      `tool_use` and its `user` `tool_result` inside the bracket, exactly as ALPHA's fire turn
      did when it scheduled BRAVO (P5, lines 18-19; round 8's I1). A nested `wakeup` in
      `fire_turn` schedules the next wakeup from within the fire, which is how the fake
      reproduces `p5_p11_wakeup_fires`. Every fire gets its own `command_uuid`, which is never
      reused;
    - `lifecycle_fault {kind, ...}` (amendment 0), which perturbs the *next* wakeup fire's
      bracket, for CP2-CP5's fail-closed tests: `omit_started`, `omit_completed`,
      `duplicate_started`, `duplicate_completed`, `completed_before_started`,
      `delay_completed {seconds}` (still in order), `delay_completed_past_next_turn`,
      `reuse_uuid` (the previous fire's `command_uuid`), `overlap` (the next fire's `started`
      before this one's `completed`), `empty` (a pair with no turn), `malformed {field}`, and
      `bracket_turn {kind}`, which brackets a task-completion, Monitor or hand-back turn
      instead, to model an unmeasured bracket source. Round 9's I1 adds two more.
      `spurious_bracket {turn}` wraps the next task-completion, Monitor or hand-back turn in a
      regular-shaped pair with a fresh `command_uuid` *and leaves the wakeup's own fire in
      place*, so the real fire, in its own measured bracket, still comes afterwards; the
      scripted turn's timing decides whether it opens before or after the wakeup's due time.
      `delay_fire {seconds}` holds the next fire that many seconds past its due time, to model
      a late fire. A fault is never the default. The unfaulted fire is the measured one;
    - `subagent_handback {after}`, a hand-back turn (round 2's I2);
    - the task-completion turn (after a `bash_bg` completes), each `monitor` tick turn and the
      `subagent_handback` turn carry `origin: {"kind": "task-notification"}`, which P11 measured
      for all three in streaming mode, and no `command_lifecycle` event (round 3's I2, round 4's
      I1, amendment 0). A test may still override a turn's origin explicitly, for example to
      `origin.kind: peer` as in the print-mode records, or give a fire an `origin`. B ignores
      origins, so neither changes a recognition, and CP2 asserts that;
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
  fixture's event-type sequence, with `command_lifecycle` events and their `state`s in place.
  The comparison is structural, and its projection is fixed (round 9's O2): each event maps to
  `(type, subtype)`, plus `state` for `command_lifecycle`, and `is_error` for a `user`
  `tool_result`. Before comparing, **both** sequences drop the non-structural event kinds the
  fixtures hold, `system/thinking_tokens`, `rate_limit_event`, `system/task_progress`,
  `tool_progress`, `system/commands_changed` and `system/vcs_state_changed`. They depend on the
  model, the account and the machine, B never reads them, and the fake does not emit them. Any
  other kind is compared exactly, so a fixture event outside both lists that the fake cannot
  produce fails the test rather than being ignored. A separate assertion runs B over each
  fixture with and without the dropped kinds and checks identical states and classifications,
  which is what makes dropping them safe.
  For `p5_p11_wakeup_fires` that includes ALPHA's fire turn scheduling BRAVO inside its bracket
  (a `wakeup` step in `fire_turn`, its `tool_use` and `user` `tool_result` between `started`
  and `completed`; round 8's I1). This keeps the fake honest against the measured harness. The opt-in live probe
  (`CONTROLLER_LIVE_WORKER=1`, haiku, the production argv as above) re-runs P3-P6 and P8-P11
  and checks the same sequences against the installed `claude`, including the bracket facts
  below.
- **The recogniser's evidence, pinned from the fixtures** (amendment 0).
  `tests/test_fake_claude_contract.py` asserts, from the committed fixture files alone, each
  fact decision 12 rests on:
  - `p5_p11_wakeup_fires` holds exactly two `command_lifecycle` pairs. Each pair has one
    `command_uuid`, `started` before `completed`, and exactly one turn between them, and the
    two uuids differ. Each fire turn's `result` has no `origin` key. No `user` event occurs
    between `started(X)` and the bracketed turn's first `assistant` event (the fire turn has no
    opening `user` event). Every `user` event inside a bracket is a `tool_result` whose
    `tool_use_id` matches a `tool_use` issued earlier in the same turn; in the fixture that is
    exactly line 19, answering ALPHA's own line-18 `ScheduleWakeup` (round 8's I1). Neither
    `prompt` text (`P11 fire ALPHA`, `P11 fire BRAVO second`) occurs in any `user` event, or
    anywhere in `p5_p11_wakeup_fires.jsonl` outside its scheduling `tool_use` input. The
    assertion reads the `.jsonl` event stream only: the matching `.meta.json` is capture
    metadata, and legitimately holds both texts in its recorded `prompt` (round 9's O1). Each
    wakeup's scheduling `tool_use` id and its `tool_use_result.scheduledFor` value occur only
    in its own scheduling pair (ALPHA's at lines 5-6, BRAVO's at lines 18-19) and on no line of
    its own fire bracket (13-24 and 25-32 respectively). That is the fact decision 13 rests on:
    a fire does not identify its wakeup;
  - no fixture other than `p5_p11_wakeup_fires` and the two P12 fixtures holds a
    `command_lifecycle` event. That covers the other ten probes and the seven job streams. Every non-first `result` of `p3_streaming_background_bash`,
    `p4_monitor`, `p8_monitor_timeout` and `p11_subagent_handback` carries
    `origin == {"kind": "task-notification"}` and lies inside no bracket;
  - `p9_wakeup_cancel` holds no fire turn and no `command_lifecycle` event;
  - each P12 fixture is `ADOPT` under `admit` and passes the gate's assertions, which are
    exactly what `gate_deviations` checks. These are gate assertions, not admission criteria:
    the fixture was admitted on the model's actions alone, so a failure here is the
    plan-amendment trigger, never a reason to recapture (round 12's and round 13's I1). The fixture
    holds exactly one `command_lifecycle` pair with one `command_uuid`, and one turn between
    `started(X)` and `completed(X)`. Before `started(X)` it holds no turn event (`system/init`,
    `assistant`, `user`, `result`) outside turn 1, so an unasked turn before the fire, even one
    the model answered `PROBE-EXTRA`, is a deviation (round 15's I1). That turn has no opening `user` event, and its `result`
    has no `origin`, as in P11. The stop's `ScheduleWakeup` `tool_use` (`stop: true`) and its
    `user` `tool_result`, with a matching `tool_use_id`, both lie inside that turn. The
    `tool_result` is not `is_error`, and its `tool_use_result` has `stopped: true` and an
    integer `cancelledWakeups`: `0` in `p12_stop_inside_fire_single`, and `1` in
    `p12_stop_inside_fire_nested`. In the nested fixture, BRAVO's scheduling pair lies inside
    the same turn, before the stop, and BRAVO's `tool_result` is not `is_error`. The model's
    `tool_use` calls in the whole fixture are exactly the named sequence with the named
    arguments and nothing else, so any call past `admit`'s cut point (a retry after a
    rejection, a second stop, a call in a later turn) is a deviation that names the call and
    the answer it followed (round 14's I1). After
    `completed(X)` the fixture holds no `command_lifecycle` event and no turn event
    (`system/init`, `assistant`, `user`, `result`), so no later fire and no later bracket
    (round 13's O2). The six non-structural kinds the replay comparison drops are ignored
    there, and an event of any kind outside both lists is a deviation, as it is in the replay
    comparison. A failure of any of these is fixed by the amendment, not by editing the test or
    the fixture;
  - `admit` and `gate_deviations` separate the model's mistakes from harness observations
    (round 13's I1). The cases are synthetic event lists built in memory from
    `p5_p11_wakeup_fires.jsonl`'s own events (turn 1 with ALPHA's call, and ALPHA's bracketed
    fire turn), with the P12 arguments and the stop spliced in; no fixture file is written.
    Harness observations, each `ADOPT` with a non-empty `gate_deviations` naming it, which is
    the amendment trigger and never a recapture: ALPHA correctly scheduled and turn 1 ended,
    then no fire, no bracket and no later turn; the stop's `tool_use` inside the bracket with
    no `tool_result` answering it; a bracket enclosing two turns, the stop in the second; the
    stop's `tool_result` `is_error` on the exact named arguments; `cancelledWakeups` missing,
    and `1` in the single probe; a second `command_lifecycle` pair after `completed(X)`. A
    `rate_limit_event` or `system/thinking_tokens` after `completed(X)` adds no deviation.
    Harness observations followed by the model's reaction, each also `ADOPT` with a non-empty
    `gate_deviations` naming both the answer and the reaction, never `RECAPTURE` (round 14's
    I1): ALPHA's exact call rejected with `is_error`, then retried in turn 1 with different
    arguments (the `p9_wakeup_cancel.jsonl` shape, 0-based lines 7, 8 and 13); ALPHA's exact
    call rejected and turn 1's reply then holding `PROBE-DONE`; the single probe's exact stop
    rejected, then retried; in the nested probe, the exact BRAVO rejected, then retried, then
    the stop; in the nested probe, the exact BRAVO rejected and no stop at all; ALPHA's
    bracket, then a second fire turn in which the model calls the stop again; the same second
    fire turn with the reply `PROBE-EXTRA` and no call; and, pinning the stated trade, a
    second stop in the same fire turn after the named sequence completed, with no rejection in
    between.
    The `PROBE-EXTRA`-only case (round 15's I1), each `ADOPT` with a non-empty
    `gate_deviations`: ALPHA correctly scheduled, then one unbracketed turn after turn 1 whose
    reply is `PROBE-EXTRA` with no call, and nothing after it (the deviations name the unasked
    turn and the missing bracket); ALPHA's bracketed fire turn answered `PROBE-EXTRA` with no
    call (the fire the model could not identify; the deviations name the bracketed turn with
    no stop); an unasked `PROBE-EXTRA` turn, then ALPHA's fire and the stop (the deviation
    names the turn before `started(X)`).
    Model mistakes, each `RECAPTURE` with its reason: turn 1 without ALPHA's call; ALPHA
    called with `delaySeconds` 30; ALPHA called with `delaySeconds` 30 and that call rejected
    (a rejection of arguments the prompt did not name is no cut point); a stop in turn 1 with
    ALPHA not rejected; `PROBE-DONE` in turn 1's reply with ALPHA not rejected; a delivered
    fire turn with no stop and no rejection, whose reply is off-script free text; the same
    fire turn with the reply `PROBE-DONE` and no call; the same with an empty reply (no `text`
    block) and no call; one unbracketed turn after turn 1 with an off-script reply and no call,
    and nothing after it (the off-script counterpart of the first `PROBE-EXTRA`-only case); in
    the single probe, a fire turn with the reply `PROBE-EXTRA` and a call to another tool; in
    the nested probe, BRAVO called in the fire turn, no stop, and then a `PROBE-EXTRA` turn
    (the model did act after turn 1, so the missing stop is its failure); in the nested probe, the stop before BRAVO; in the
    nested probe, BRAVO called twice with no rejection between (an extra call before the
    sequence completes). A further case shows that a model mistake before the cut point and a
    harness fault in the same capture are `RECAPTURE`. `admit`'s independence is tested in
    its narrowed form: its verdict is unchanged when every `command_lifecycle` event, and every
    `user` event other than the `tool_result`s answering exact named calls, is removed; when
    those `tool_result`s' content and `tool_use_result` are replaced, keeping `is_error`; and
    when the `is_error` of a `tool_result` answering a call that is not an exact named call is
    flipped. That shows it reads nothing of the harness except the one flag that sets the cut
    point. Its verdict is also unchanged when an off-script `text` block after turn 1 is
    replaced by different off-script text, so after turn 1 it reads only whether a block is
    exactly `PROBE-EXTRA` (round 15's I1);
  - every fixture's SHA-256 equals the one its `README` records.
  The live probe asserts the first three facts against the installed `claude`, and at CP8 it
  re-runs both P12 probes and asserts the same counts. A mismatch is reported as the
  plan-amendment trigger above. CP2 runs B itself over the same fixtures.
- **Test-teardown guarantee** (round 1's I7). `tests/process_fixtures.py` gains
  `reap_recorded_workers(runtime_root)`: it reads every job record under a test's runtime root
  and identity-checked-`SIGKILL`s any recorded worker, anchor and tagged process still alive.
  Every test class that launches a streaming worker registers it with `addCleanup` through one
  shared mixin, so a test that SIGKILLs its Controller never leaks an anchor onto the machine or
  into an outer lifecycle job.

<!-- /CP1 -->

<!-- CP2 -->
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
  - P5's ALPHA wakeup is `pending` until `completed(2cde4b9e-...)` (line 24), not merely until
    the fire turn's `result` (line 23), and `fire_matched` from there. BRAVO is `pending` until
    `completed(6ff491e4-...)` (line 32), and `fire_matched` from there. Both brackets are
    `closed_regular`, each matching its own wakeup, and there are no anomalies (amendment 0).
    After line 32 the worker is still `WAITING`, never quiescent, with no supervisor facts; with
    `settled_wakeups` naming ALPHA only it is still `WAITING`; naming both, it is quiescent
    (round 9's I1);
- row precedence (round 1's I1): a supervisor-declared overdue wakeup whose session end also
  killed a task gives `wakeup_not_delivered`, with `owned_work_killed_at_exit` in
  `secondary_reasons`; a streaming stream with no `ending_offset` whose EOF killed open tasks,
  or left a wakeup pending, gives `stdin_closed_while_waiting`, with the kill as secondary;
  a supervisor-declared overdue wakeup `W2` while an earlier wakeup `W` is still
  `fire_matched` and unsettled (the breach path ends the session without waiting for `W`'s
  window) gives `wakeup_not_delivered`, with `owned_work_killed_at_exit` in
  `secondary_reasons` for `W` (round 10's O1);
- positional kills (round 1's I2): a streaming session in which the worker `TaskStop`s a
  background task, and separately a `Monitor` reaches its timeout (P8's statuses), then ends
  with a clean quiescent turn and a supervisor end is `SUCCESS`; the same stop recorded after
  `ending_offset` is `owned_work_killed_at_exit`;
- wakeup time sources (round 1's I3): the due time is the harness-stated `in 1257s` when the
  `tool_result` carries it and the clamp otherwise (`due_source` recorded, `scheduledFor`
  recorded beside it); a regular-shaped bracket whose turn opens 30 s before the due time is
  `unmatched_bracket` and matches nothing; a bracketed turn with no timestamped event is
  `bracketed_turn_open_time_unknown`; the state never reads a clock (the classifier is run
  under a patched `time` that raises);
- origins are never evidence (amendment 0, replacing revision 7's origin-keyed tests; I11):
  - a turn with no bracket that opens **after** a pending wakeup's due time leaves the wakeup
    `pending`, whatever its `result` `origin`. The cases are no `origin` (shaped exactly like
    P11's fire turn: `system/init`, no opening `user` event, a `result` without `origin`),
    `task-notification`, `peer`, and an unknown `kind`. The worker is `WAITING`, never
    quiescent;
  - the P5 fixture with an `origin` of each of those kinds injected into both fire `result`s
    gives exactly the same bracket states, resolutions and outcome as the unmodified fixture;
  - the Controller-initiated first turn, even with a patched timestamp after the due time,
    never matches a wakeup, and a bracket around it is `bracketed_first_turn`;
- bracket correlation and ordering (amendment 0), each case built from the P5 fixture by
  editing lines, and each checked for its bracket state, its anomaly kind, and its final
  classification (row 3 `AMBIGUOUS` for every anomaly, whatever the rest of the stream):
  - missing `started`: `completed_without_started`, the wakeup stays pending;
  - missing `completed`: the bracket stays `open`, the worker stays `WAITING` (never
    quiescent), and at EOF it is row 5 or row 7 (not an anomaly);
  - duplicated `started(X)` (while open, and after `completed(X)`): `duplicate_started`;
    duplicated `completed(X)`: `completed_without_started` for the second; a `command_uuid`
    reused by a later fire: `duplicate_started`, and the later wakeup stays pending;
  - `completed(X)` before `started(X)`: `completed_without_started`, then `duplicate_started`;
  - `completed(X)` delayed past the next turn's opening: `multiple_bracketed_turns`;
    `completed(X)` delayed but still in order (other lines, no turn, in between): regular,
    and the wakeup is matched only at `completed(X)`;
  - `started(X)` inside an open turn: `started_mid_turn`;
  - two overlapping brackets: both `overlapping_brackets`, neither matches, and both count
    as owned work until their `completed`;
  - a pair with no turn: `no_bracketed_turn`; a pair around two turns:
    `multiple_bracketed_turns`;
  - a bracket around a task-completion turn with no wakeup pending: `unmatched_bracket`;
  - **the wrong match, round 9's I1** (replacing round 8's O2 residue test). The dangerous
    sequence, built from the P5 fixture: wakeup `W` scheduled; `W` due; a regular-shaped
    bracket around a task-completion turn that opens *after* `W`'s due time; that task was the
    only other owned work. At the spurious bracket's `completed`, `W` is `fire_matched`, not
    settled, and `owned_work()` still holds it, so `quiescent()` is **false** at that line
    and at every line after it until a settlement, at every chunking. Then:
    - the real fire's bracket arrives. It is `unmatched_bracket` (no `pending` wakeup left),
      and the stream classifies row 3 `AMBIGUOUS`/`command_lifecycle_irregular`;
    - the same prefix, followed instead by a successful `ScheduleWakeup {stop: true}` whose
      `cancelledWakeups` is 1 (the harness still held `W`): `wakeup_count_mismatch`, row 3
      `AMBIGUOUS`, and `W` settled by `stop`, with both counts in `wakeups_seen`;
    - the same prefix with `settled_wakeups` naming `W` and nothing after it: quiescent. This
      is the double-breach residue (H2), which only a supervisor fact can reach; C decides
      when that fact may be declared, and CP3 pins it;
    - with a second wakeup `W2` pending and due, the real fire of `W` matches `W2`; both are
      `fire_matched`, and the worker is quiescent only once `settled_wakeups` names both.
    The twin with the task-completion turn opening before the due time minus
    `WAKEUP_SKEW_SECONDS` is `unmatched_bracket` at its own `completed`, `W` stays `pending`,
    the real fire's later regular bracket matches it, and the stream is still row 3 (sticky);
- settlement (round 9's I1):
  - a successful stop after a clean fire, with `cancelledWakeups: 0` and no wakeup `pending`,
    settles the `fire_matched` wakeup with no anomaly; with `cancelledWakeups: 1` and one
    wakeup `pending` (P9's own fixture), it settles it with no anomaly; the P9 fixture itself
    classifies with no anomaly;
  - **a stop inside a fire turn** (round 10's I1), built from the P5 fixture by an edit made
    in the test (the committed fixture is untouched): a successful `ScheduleWakeup {stop:
    true}` pair inserted inside ALPHA's bracket, after BRAVO's nested schedule (lines 18-19)
    and before ALPHA's `result`, reporting `cancelledWakeups: 1` (BRAVO), with BRAVO's fire
    bracket removed. No anomaly: ALPHA's provisional match is ALPHA, the expected count is 1,
    ALPHA's bracket is `closed_regular` with ALPHA as its match, ALPHA is `settled` by `stop`
    with that bracket's `command_uuid`, BRAVO is `settled` by `stop`, and the stream is
    quiescent after ALPHA's `completed` with no supervisor fact;
  - its single-wakeup twin (ALPHA's fire turn without BRAVO's schedule, the stop reporting
    `0`): the same, with no anomaly;
  - the two committed P12 fixtures (round 11's I1), unedited: each classifies with no
    anomaly. ALPHA is the provisional match and ALPHA's bracket is `closed_regular` with ALPHA
    as its match. The expected count is `0` (single) or `1` (nested, BRAVO), and it equals
    the recorded `cancelledWakeups`. Every wakeup is `settled` by `stop`, and the stream is
    quiescent after `completed(X)` with no supervisor fact. These are the measured
    counterparts of the two edited-P5 cases above, which stay as they are;
  - the same stop reporting `cancelledWakeups: 1` in the single-wakeup twin (a harness that
    still counts the running wakeup): `wakeup_count_mismatch`, row 3, and ALPHA's bracket still
    `closed_regular`;
  - **the wrong-match variant**: the round 9 dangerous sequence (above: `W` `fire_matched`
    by the spurious bracket), then `W`'s real fire bracket, whose turn issues
    the stop reporting `0`. No provisional candidate (`W` is not `pending`), so the expected
    count is 0 and there is no count mismatch, and at `completed` the bracket is still
    `unmatched_bracket`: row 3 `AMBIGUOUS`/`command_lifecycle_irregular`;
  - the spurious-bracket variant: the stop issued from inside the spurious bracket's turn,
    reporting `1` (the harness still holds `W`). `W` is the provisional match, so the expected
    count is 0: `wakeup_count_mismatch`, row 3;
  - a stop inside a bracket that has already broken a condition (a second turn inside it, or
    a second open bracket) fixes no provisional match, and compares every `pending` wakeup;
  - `cancelledWakeups` higher, or lower, than B's expected count is `wakeup_count_mismatch`;
    a stop whose `tool_use_result` has no `cancelledWakeups`, or a non-integer one, settles
    everything and compares nothing; an `is_error` stop settles nothing;
  - `settled_wakeups` naming a `pending` wakeup, or an unknown id, settles nothing (only a
    `fire_matched` wakeup can settle by the window); the fact is recorded in
    `stream_diagnosis` as ignored;
  - at exit, a `fire_matched` wakeup that no fact or stop settled makes row 7 match
    (`owned_work_killed_at_exit`), with `ending_offset` set and without it (row 5 primary);
  - a malformed lifecycle event (no `command_uuid`, a non-string one, `state: "running"`, no
    `state`): `malformed_lifecycle_event`, which opens and closes nothing;
  - an anomaly early in a stream that otherwise ends cleanly and quiescently is still
    `AMBIGUOUS`/`command_lifecycle_irregular` (sticky), with the later rows in
    `secondary_reasons`;
- several `command_uuid`s: three sequential regular brackets match three wakeups in due
  order. With two wakeups pending and one bracket whose turn opens after both due times, the
  earliest-due one is matched, and the other stays `pending` (H2's unmeasured case);
- open brackets are owned work: `quiescent()` is false between a fire turn's `result` and its
  `completed(X)`, and between `started(X)` and the turn's `system/init`, at every chunking;
- the supervisor fact `command_lifecycle_overdue_declared_at` gives row 4's
  `command_lifecycle_unterminated`, with `owned_work_killed_at_exit` secondary when the ending
  killed a task;
- task openness (round 2's O1): `2857a730`'s stream, whose 20 foreground `task_started`/
  `task_notification` pairs are never listed in a `background_tasks_changed`, never makes the
  worker `WAITING` because of them, and a task that is listed stays open until its terminal
  status or a list without it;
- `queued_turn_count: 1` on the last `result` is not quiescent (round 1's O4), and
  `queued_turn_count`, `terminal_reason` and `origin` are carried into `stream_diagnosis`;
- incremental and whole-stream parsing agree: feeding a stream line by line, in any chunking,
  gives the same states as feeding it all at once. A partial trailing line is never consumed;
- `quiescent()` is false while a turn is open, while any task is open, while a wakeup is
  `pending` or `fire_matched` and unsettled, and while a `command_lifecycle` bracket is open. An
  unknown event type never makes it true;
- a `ScheduleWakeup` whose `tool_result` is an error is not pending. A successful `stop: true`
  settles every `pending` and `fire_matched` wakeup. Without a harness-stated time, a wakeup's
  due time uses the clamp (60 s to 3600 s);
- the old single-result streams (legacy fixtures in `tests/test_worker.py`), classified with
  `mode="print"`, still give exactly today's outcomes; row 5 never applies to them. Neither they
  nor the seven job fixtures hold a lifecycle event, so no anomaly arises in any of them.

<!-- /CP2 -->

<!-- CP3 -->
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
  Covered for `bash_bg`, `monitor` (three ticks, one turn each) and `wakeup`. For `wakeup` the
  fire turn ends with a `wakeup_stop`, so it settles at once: the fake reports
  `cancelledWakeups: 0` (the wakeup has fired), B's expected count leaves out the provisional
  match and is 0, and `wakeups_seen` records the wakeup `settled` by `stop` with the fire's
  `command_uuid`, with no anomaly (round 10's I1). A second variant also schedules a fallback
  `wakeup {delay: 1200}` before the first fires and cancels it from the fire turn: the fake
  reports `1`, the expected count is 1, and the result is still `SUCCESS`. The variant without
  the stop, with
  `WAKEUP_SETTLE_SECONDS` patched to 1 s, sees `RUNNING, WAITING, RUNNING, WAITING, ENDING,
  ENDED`, the second `WAITING`'s `details` naming the `fire_matched` wakeup and its settle time,
  and `ENDING` no earlier than 1 s after the fire's `completed(X)` (round 9's I1);
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
- `command_lifecycle` supervision (amendment 0), with the fake's measured bracket:
  - a `wakeup` fire does not let `launch` reach `ENDING` before the fake's `completed(X)`.
    With `lifecycle_fault {kind: delay_completed, seconds: 2}` and `WAKEUP_SETTLE_SECONDS`
    patched to 1 s, `on_state_change` shows `WAITING` with the open bracket in `details` for
    those 2 s, then `WAITING` on the `fire_matched` wakeup for the settle window, and then
    `ENDING`. The result is `SUCCESS`, and `wakeups_seen` says `settled` by `settle_window`;
  - `omit_completed`, with `COMMAND_LIFECYCLE_GRACE_SECONDS` patched to 1 s: the stalled
    bracket is declared, `command_lifecycle_overdue_declared_at` and its `command_uuid` are
    flushed with `ENDING`, and the result is `AMBIGUOUS`/`command_lifecycle_unterminated`.
    The same fault with a `bash_bg` task still open is not declared until the task ends;
  - a bracketed turn that runs longer than the patched grace (a long `text` step inside the
    fire turn) is never declared, because only a stalled bracket is timed;
  - `omit_started`, and separately `reuse_uuid`, `overlap` and `bracket_turn {kind:
    task_completion}` (its bracketed task-completion turn scripted to open before the pending
    wakeup's due time minus `WAKEUP_SKEW_SECONDS`, so condition 6 rejects it; opening after is
    the wrong match below; round 8's O2): the wakeup is not matched, the overdue rule (patched to 1 s) ends the
    session, and the result is `AMBIGUOUS` with `command_lifecycle_irregular` primary and
    `wakeup_not_delivered` secondary. `bracket_turn` with no wakeup ever scheduled still ends
    normally at quiescence, and is `AMBIGUOUS`/`command_lifecycle_irregular`;
- **a wrong match never ends the session** (round 9's I1, the reviewer's dangerous sequence),
  with `WAKEUP_SETTLE_SECONDS` patched to 3 s and every other grace left long. The script
  schedules `wakeup {delay: 1}` (the fake states `in 1s`, which B prefers to the clamp) and a
  `bash_bg` that completes after the due time, then ends its turn; `lifecycle_fault {kind: spurious_bracket,
  turn: task_completion}` wraps the task-completion turn, which ends with nothing else owned:
  - with the fake's real fire following 1 s after the spurious `completed` (inside the window):
    `on_state_change` never reports `ENDING` between the spurious bracket's `completed` and
    the real fire's `completed` (asserted on the ordered state log against the stream offsets
    of both events), stdin is not closed in that interval (`FAKE_CLAUDE_DIAG_FILE`'s
    `stdin_at_eof` stays false until after the real fire's turn), the real fire's turn is
    consumed, and the result is `AMBIGUOUS`/`command_lifecycle_irregular` with
    `unmatched_bracket` naming the real fire's `command_uuid`;
  - the same, with the task-completion turn ending in `wakeup_stop` instead: the fake reports
    `cancelledWakeups: 1` (it still holds the wakeup), while B's expected count is 0 (the stop
    comes from inside the spurious bracket, whose provisional match is `W`), the result is
    `AMBIGUOUS`/`command_lifecycle_irregular` with `wakeup_count_mismatch`, and the session
    then ends at quiescence without waiting for the window;
  - a Monitor tick turn inside the window restarts the settle timer: `ENDING` comes no
    earlier than 3 s after that turn's `result`;
  - **an open task pauses the window** (round 10's I2): the spurious bracket's turn also
    starts a second `bash_bg` of 6 s, longer than the patched window, and ends; the fake's
    real fire is delayed (`delay_fire {seconds: 8}`, past the second task's end at about 7 s
    past due) so that it arrives within about 1 s of that task's completion turn ending, well
    inside the window as the corrected rule measures it. `on_state_change` never reports `ENDING` before the real fire's `completed`, the
    settle timer never fires while the task is open (`settled_wakeups` stays empty until
    then), and the result is `AMBIGUOUS`/`command_lifecycle_irregular` with
    `unmatched_bracket` naming the real fire's `command_uuid`. Under revision 10's predicate
    this test would have ended the session at the task's completion turn;
  - **the double-breach residue, pinned** (H2): the same spurious bracket with
    `lifecycle_fault {kind: delay_fire, seconds: 30}`, so the real fire is later than the
    patched window. The worker is `WAITING` for the full window after the spurious
    `completed`, then `ENDING`; the result is `SUCCESS` with `W` `settled` by
    `settle_window` and the spurious bracket's `command_uuid` recorded as its match. The test's
    docstring names this as the one documented path by which an unrelated bracket can precede
    `ENDING`: it needs the fire to breach the lateness bound the window is sized from;
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

<!-- /CP3 -->

<!-- CP4 -->
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
  events. In the `wakeup` variant the fire is the fake's measured bracket, and the job's
  `stream_diagnosis.wakeups_seen` names the matching `command_uuid` (amendment 0) and the
  settlement: `stop` when the continuation turn ends with `wakeup_stop` (the fire turn itself,
  so the stop is inside the bracket and B's expected count leaves the fired wakeup out; the
  result is `SUCCESS` with no anomaly, round 10's I1), `settle_window` in a
  second variant without it (`WAKEUP_SETTLE_SECONDS` patched to 1 s), whose history holds a
  second `WAITING` before `ENDING` (round 9's I1). Its fail-closed twin, the same script with `lifecycle_fault {kind: omit_started}` and the overdue
  grace patched short, ends `FAILED` with `worker_outcome` evidence
  `command_lifecycle_irregular`, even though the continuation turn committed. That holds for
  `IMPLEMENTING` too, because the outcome is non-verifying before any predicate runs (I7);
- **R14b, a wrong wakeup match cannot finish a job** (round 9's I1). The dangerous sequence of
  CP3, driven through `execute_step` in a disposable managed repository, with
  `WAKEUP_SETTLE_SECONDS` patched to 3 s. The `IMPLEMENTING` worker completes and commits the
  checkpoint on its first turn, so the durable state already satisfies F's
  `IMPLEMENTING -> IMPLEMENTING` predicate. It schedules a wakeup `W` and a `bash_bg`, and ends
  its turn. `W` becomes due, the task-completion turn is wrapped in a `spurious_bracket` and
  ends with nothing else owned, and the fake then delivers `W`'s real fire inside the window:
  - between the spurious bracket's `completed` and the real fire's `completed`, the record
    stays `LAUNCHED` with `worker_state` `WAITING` (its `waiting_on.wakeups` naming `W` as
    `fire_matched`), no `worker_ending` event is appended, no `COMPLETED` record is written,
    and the predicate is never evaluated (spied: `_row_clauses_failure` is not called before
    `worker.launch` returns);
  - the job ends `FAILED` with `worker_outcome` evidence `command_lifecycle_irregular`
    (`unmatched_bracket`), although the checkpoint is committed and the predicate would pass.
    Ownership, not reconciliation, decided it (I7);
  - the same script in `SELF_REVIEWING_IMPLEMENTATION` ends `FAILED` in the same way;
  - the same script in which the continuation instead issues `wakeup_stop` ends `FAILED` with
    `wakeup_count_mismatch`;
  - a concurrent `step` from a second process during the settle window exits 45 (the lock is
    held and the job is not terminal), so no later action starts while `W` is still owned;
- **lock lifetime.** While a job is `WAITING`, `lock.probe_lifecycle_lock` reports `held`. After
  the Controller process is SIGKILLed mid-wait (subprocess Controller), it still reports `held`,
  and `fuser`-style evidence names the anchor. The test's cleanup ends the worker and the
  anchor through `reap_recorded_workers` (CP1).

<!-- /CP4 -->

<!-- CP5 -->
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
  and the same resolution as live supervision. For the `wakeup` variant the truncation points
  include the two that fall inside the fire's bracket: after `started(X)` and before its turn,
  and after the fire turn's `result` and before `completed(X)`. At each, the replayed bracket is
  `open` with the same `command_uuid`, the worker is `WAITING`, and the resolution happens only
  at the live `completed(X)`. The P5 fixture itself, truncated at every line boundary, gives
  the same bracket states as whole-stream parsing (amendment 0);
- **Controller loss with a lifecycle pair incomplete** (amendment 0). A subprocess Controller
  is SIGKILLed while the fake holds a fire's bracket open, first before the fire turn and then
  after its `result` (`delay_completed {seconds: 5}`):
  - `resume` re-attaches without a new `FAKE_CLAUDE_INVOCATIONS_FILE` line, sees the open
    bracket after replay, and reconciles `FINISHED` once `completed(X)` arrives;
  - with `omit_completed` and the grace patched to 1 s, the re-attached supervisor's own stall
    timer, started at re-attach, declares the bracket. `command_lifecycle_overdue_declared_at`
    is flushed with `ENDING`, and the job ends `FAILED` with `command_lifecycle_unterminated`;
  - a bracket opened and closed entirely while no Controller was attached is judged by replay
    alone: regular, the job reconciles `FINISHED`; `duplicate_started` injected, the job ends
    `FAILED` with `command_lifecycle_irregular`;
  - a Controller SIGKILLed after flushing `ENDING` with `command_lifecycle_overdue_declared_at`
    re-attaches with that fact, and the classification is the same `AMBIGUOUS` as the live
    run's;
- **Controller loss while a matched wakeup settles** (round 9's I1), `WAKEUP_SETTLE_SECONDS`
  patched to 3 s:
  - SIGKILLed 2 s into a `fire_matched` wakeup's window: `resume` re-attaches, replay shows the
    wakeup `fire_matched`, the record's `waiting_on` names it, and `ENDING` comes no earlier
    than 3 s after the re-attach (the timer restarted, never carried); the job reconciles
    `FINISHED`;
  - the same, with the wrong-match script of CP4's R14b and the real fire arriving after the
    re-attach: `FAILED` with `unmatched_bracket`, exactly as the live run;
  - SIGKILLed after flushing `ENDING` with `settled_wakeups`, before the worker exits:
    replay with that fact classifies `SUCCESS` (`exit_status_known: false`), and the same
    replay without the fact would be row 7, which the test also asserts, so the fact is
    shown to be load-bearing;
  - `validate_record` refuses a record whose `settled_wakeups` is non-empty without
    `ending_offset` (`StaleJobRecordError`);
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

<!-- /CP5 -->

<!-- CP6 -->
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

<!-- /CP6 -->

<!-- CP7 -->
### CP7 -- operator diagnostics

Files: `controller/observe.py`, `controller/cli.py`, `tests/test_observe.py`, `tests/test_cli.py`,
`tests/test_observation_equivalence.py`.

Tests:
- each activity row of G has a `status`, `explain` (text and `--json`), `inspect` and `follow`
  test on a record fixture, plus live ones for `waiting` and `unsupervised` on the R14 and R15
  runs, and for a drain-detached `draining` record (CP4) listing its pids and a recognised daemon
  as `not owned`;
- unresolved lifecycle pairs (amendment 0): a `waiting` record whose `waiting_on` holds an open
  bracket shows the `harness command` clause in `status`, `follow`, `inspect` and `explain`
  (text and `--json` `waiting_on.command_lifecycles`). The stall time is shown with a supervisor
  attached, and `stall time unknown (no Controller attached)` without one. A `waiting` record
  held only by a `fire_matched` wakeup shows the "presumed fired, not yet settled" clause with
  its `command_uuid`, the settle time left (or `settle time unknown` when `unsupervised`), and
  never a quiescent or finished label (round 9's I1); a terminal record with
  `wakeup_count_mismatch` shows both counts in `explain`. A terminal record
  with `command_lifecycle_irregular` or `command_lifecycle_unterminated` shows the reason,
  the `command_uuid` and every anomaly line in `explain`. `follow` renders `command_lifecycle`
  stream events as `harness command <uuid8> started`/`completed`;
- presenter strings for records without `worker_state` are unchanged, pinned by the existing
  tests;
- following stays presentation-only: the observation-equivalence suite (`step` with and without
  `--follow`) still produces identical records and decisions, now including a `WAITING` run.

<!-- /CP7 -->

<!-- CP8 -->
### CP8 -- documentation and full verification (terminal checkpoint)

Files: `README.md` ("Concurrency and worker lifecycle" rewritten, including L343/L362's
descendant-inherits-the-lock text, the recognised-daemon list, the drain bound and the
fallback-wakeup delay of decision 11, the wakeup-fire bracket with its fail-closed cases, and
decision 13's settle window, its cost for a worker that ends without a stop, and H2's
double-breach residue;
"Job dispositions" and the command table updated), `docs/adr/0004-worker-lifecycle-ownership.md` (new: the ownership model,
I1-I11, H1-H9 (H5 with both windows, including re-attached supervision; H2 with the bracket's
failure modes and double-breach residue), the daemon policy, the drain bound, decision 12's
measured evidence and rejected alternatives, and decision 13), `docs/ROADMAP.md` (section 1.4: the hotfix recorded; the four existing patches
stay listed as open), `docs/ACTIVE_MILESTONE.md`.

Verification:
- the full suite;
- the packaged-runtime suite under `CONTROLLER_REQUIRE_PACKAGING_TESTS=1`;
- `tools/ci_workflows.py --check`;
- the opt-in live contract probe against the installed `claude`, including its re-run of
  CP1's two P12 probes (a final re-measurement; CP1's fixtures were the first), with its output
  recorded for the functional review.

<!-- /CP8 -->

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
5. **An overdue wakeup (300 s past due) ends the session and fails closed.** So does an
   unterminated `command_lifecycle` bracket, one stalled 300 s with no turn open (C, amendment 0).
   These are the only places the Controller ends a session on time. Both are justified as
   harness-contract breaches, not budgets, and both are the same class (I5's one exception,
   widened by one case). The settle window (decision 13) is not a third: it ends nothing, and
   only decides when a matched wakeup stops being owned work, after which the session still
   ends only at a quiescent turn. The alternative is to wait forever on a wakeup, or a harness command,
   that never finishes.
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
12. **The wakeup-fire recogniser is the correlated `command_lifecycle` bracket** (amendment 0,
    replacing revision 7's `origin.kind` key). The question the amendment had to answer is
    whether the pair is stable enough to be a positive recogniser. The answer is **yes, as a
    conjunctive and fail-closed recogniser, not on its own**. The evidence, all from the
    committed fixtures (P11):
    - **Positive and distinguishing.** Both captured fires, under different `prompt` texts, are
      enclosed in a pair. No other turn in the 18 fixtures is: no task completion, Monitor event,
      Monitor timeout, subagent hand-back, Controller-initiated first turn or slash command.
      Nor is P9's cancelled wakeup, and none of the 80 `worker.stdout` files among the 176
      print-mode job directories in the runtime root holds a lifecycle event (counted at
      `6db4f6b`, round 8's O1);
    - **Stable fields.** `type` and the two `state` values are identical across both fires. The
      per-instance fields (`command_uuid`, `uuid`, `session_id`) are used only as the
      correlation key, or not at all. Nothing that varied between the two fires is compared;
    - **Correlatable.** The pair shares one `command_uuid`, that uuid is unique per fire, and
      brackets were sequential and never overlapped.
    Evidence from two fires on one `claude` version is thin, so the recogniser does not stand
    alone. A bracket matches a wakeup only when it is regular *and* its turn opens at or after a
    pending wakeup's due time, the revision 7 time condition, kept. Everything else is an
    anomaly that fails the run closed (row 3), or a stall that the supervisor declares (row 4).
    A match is not the end of ownership (decision 13): it makes the wakeup `fire_matched`, and
    the unsafe direction, letting a wakeup that has not fired stop being owned work, needs the
    settle window to pass without the real fire, which is H2's double-breach residue. The safe
    direction, not matching one that did fire, always ends `AMBIGUOUS`.
    Rejected, each against the fixtures:
    - `result.origin` / `origin.kind`, revision 7's key: both fire `result`s have no `origin`.
      Absence is not positive (round 3's I2, round 5's I1), because the Controller-initiated
      first turn also has none;
    - the fire turn's opening `user` event carrying the `prompt` text, revision 7's named
      fallback: the fire turn has no opening `user` event (no user message delivers the
      `prompt`). Its only `user` events are `tool_result`s for its own `tool_use`s (ALPHA's
      line 19), and the `prompt` text occurs only in the scheduling `tool_use`;
    - "any turn after the due time with no `task-notification` origin": inference from absence
      again, and it would accept an unmeasured turn kind (`SendMessage`, `Workflow`, forked
      `Skill`);
    - the bracket as a sole recogniser, with no due-time condition: it would match a wakeup on
      any future non-fire bracket, silently.
    The live probe (CP1, CP8) re-measures the pair. A change is a plan-amendment trigger, not a
    silent re-baseline (CP1).
13. **A matched wakeup stays owned until it settles** (round 9's I1, revision 10). Revision 9
    let a regular bracket remove its matched wakeup from owned work at `completed(X)`. Because
    the stream does not identify which wakeup a fire belongs to (Investigation), a regular
    bracket around some other turn opening after a wakeup's due time could empty owned work,
    let the worker quiesce, and close stdin before the real fire. The real fire's
    `unmatched_bracket` could then never be observed, and in `IMPLEMENTING -> IMPLEMENTING` a
    durable checkpoint could still let reconciliation verify. Revision 10 fixes the ownership
    rule itself, so reconciliation is never the guard:
    - a match makes the wakeup `fire_matched`, which is still owned work (B, I11);
    - it settles only by a successful `ScheduleWakeup {stop: true}`, whose harness-reported
      `cancelledWakeups` (measured in P9) must agree with B's expected count or the run is
      `AMBIGUOUS` (`wakeup_count_mismatch`), or by `WAKEUP_SETTLE_SECONDS` (305 s) of idle
      supervisor time after `completed(X)` with no bracket arriving (C). The expected count is
      the `pending` wakeups, less the provisional match of a stop issued inside a fire turn,
      whose wakeup the harness has already fired (B, "A stop inside a bracket"; round 10's I1).
      So a worker that cancels from its fire turn, as `WORKER_LIFECYCLE_NOTE` asks, settles
      at once with no anomaly;
    - idle means the overdue rule's own predicate, no turn and no task open, and also no
      bracket open (round 10's I2). The window equals the lateness the overdue rule already
      accepts plus the match skew, measured over the same idle time, so a wrongly matched
      wakeup's real fire arrives inside it, while the session is open, and is judged
      `unmatched_bracket`.
    This is the reviewer's option 2 (conservative ownership), bounded by option 3's argument.
    The settle window's timing needs no new harness evidence. The stop-inside-a-fire count
    does: it is the one fact the expected count assumes that no amendment-0 fixture measures.
    So CP1 captures P12 into two new fixtures before CP2 starts, and a different count is a
    plan-amendment trigger (round 11's I1). The existing fixtures stay byte for byte.
    Its cost is latency, not correctness: a worker that lets a wakeup fire and ends without a
    stop waits up to 305 s of idle time before `ENDING`. `WORKER_LIFECYCLE_NOTE` already tells
    workers to cancel wakeups they no longer need, and its text is unchanged (it is part of the
    captured production argv). Rejected:
    - option 1, a stronger positive correlation: no field in the captured fire identifies its
      wakeup (the fire turn carries no `prompt` text, and neither the scheduling `tool_use` id
      nor `scheduledFor` occurs in the wakeup's own bracket; `command_uuid` is fresh per fire,
      CP1 pins all three). A new probe could
      only look for a field the capture already shows is absent;
    - settling a match only by a stop (no window): every worker that lets a wakeup fire and
      ends without a stop would then wait for the overdue rule and fail `AMBIGUOUS`, turning
      decision 11's "correct, only slower" into a failed job;
    - failing closed on every match that no stop confirms: the same outcome, stated differently;
    - having the Controller write a message to the worker's stdin to make it issue a stop: it
      would add a Controller-authored turn to the worker's conversation. The task line and the
      system note (A) are the Controller's only inputs to a worker, by design.

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

Per checkpoint: the full Controller suite, plus the checkpoint's own new tests. At CP1, before
CP2 starts: the P12 capture against the installed `claude`, run by hand with the capture script,
and its contract-test gate (round 11's I1). At CP8: the full suite, the packaged-runtime suite,
`tools/ci_workflows.py --check`, and the opt-in live contract probe. Nothing in the default suite needs network or the real `claude`: every worker is the CP1
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

### Amendment 0 (`/request-plan-amendment` at `6db4f6b`, from `IMPLEMENTING` at CP1) -- applied in revision 8

Trigger: CP1's P11 capture measured a wakeup-fire `result` with no `origin`. That is the
contradiction revision 7's B and CP1 named as a plan-amendment trigger. Revision 7's approval
(`14205f3`) is `SUPERSEDED`. No checkpoint had completed (`checkpoints_snapshot: {}`).

| finding (measured) | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| A1 the fire turn's `result` has no `origin`, in both captures under different `prompt` texts | accepted: every `origin`-based recognition is removed, and `origin` is never evidence, present or absent (I11) | `tests/harness_contract/p5_p11_wakeup_fires.jsonl` lines 23 and 31: no `origin` key; the first `result` (line 12) also has none | Investigation (P11 table); I11; B (`pending_wakeups`, `command_lifecycles`); decision 12 (rejected alternatives); CP2 tests ("origins are never evidence") |
| A2 each fire is enclosed in `command_lifecycle started(X)` ... `completed(X)` with one shared, unique `command_uuid` | accepted as the recogniser, conjunctive with revision 7's due-time condition | same fixture, lines 13/24 (`2cde4b9e-...`) and 25/32 (`6ff491e4-...`); exactly one turn in each; no overlap; per-line arrival offsets in the `.meta.json` (5 ms before the turn, under 1 s after its `result`) | B (`command_lifecycles`: fields, correlation, the bracketed turn, the six regularity conditions); decision 12 |
| A3 no other turn kind is bracketed | accepted as the evidence for "distinguishing", not as a completeness claim | `grep -c command_lifecycle` over all 18 fixtures: only `p5_p11_wakeup_fires` (4); 0 of the 80 `worker.stdout` files in the runtime root's 176 job directories (corrected in round 8, O1); P3, P4, P8 and the hand-back turns carry `origin: {"kind": "task-notification"}` and no bracket; P9 holds no fire and no bracket over 200 s past due | Investigation (P11); B ("Nothing else resolves a wakeup", `unmatched_bracket`); H2; CP1 fixture-pinned contract tests |
| A4 revision 7's named fallback (the fire turn's opening `user` event carrying the `prompt` text) does not exist | accepted: rejected as a recogniser | the fire turns (lines 14-23, 26-31) hold no opening `user` event; their only `user` event is line 19, the `tool_result` for ALPHA's own line-18 `ScheduleWakeup` (corrected in round 8, I1); the `prompt` texts occur only in the scheduling `tool_use` inputs (lines 5 and 18) | decision 12; CP1 contract test |
| A5 missing, duplicated, reordered, delayed, overlapping or malformed pairs, and several uuids, were not measured | accepted: each is specified fail-closed rather than assumed | none in the fixtures (by A2/A3) | B (anomaly kinds, sticky row 3, open brackets as owned work, delay rule); C (stall grace); CP1 `lifecycle_fault`; CP2 correlation tests; CP3 supervision tests |
| A6 lifecycle events carry no `timestamp` | accepted: bracket timing is a supervisor fact | fixture lines 13, 24, 25, 32 | B (purity note); C ("Unterminated `command_lifecycle` brackets"); D (`command_lifecycle_overdue_declared_at`); E (replay; stall timer restarts at re-attach) |
| A7 a Controller lost inside an incomplete pair | accepted | E's replay already rebuilds B from the stream | E ("Re-attach" step 2 and the open-bracket bullet); CP5 tests |
| A8 operators cannot see an unresolved pair | accepted | G's revision 7 table had no such row | D (`waiting_on.command_lifecycles`); G (waiting line, "Unresolved lifecycle pairs", `follow` rendering); CP7 tests |
| A9 the streaming hand-back turn carries `task-notification`, not `peer` | accepted: the fake follows the measurement | `p11_subagent_handback.jsonl` line 22 | CP1 (fake turn origins) |
| A10 P8, P9 and P10 matched revision 7's expectations | no change | `p8_task_stop`, `p8_monitor_timeout`, `p9_wakeup_cancel`, `p6_p10_slash_command` | Investigation (recorded as measured) |

Preserved unchanged: Goal; Non-goals (one entry added, the Workflow checkpoint-abandon deadlock,
out of scope); invariants I1-I10 (I5's exception names the bracket stall); A; B's task, turn,
quiescence and classification structure (row 3 and row 4 each gain one reason, and row 7's
owned work includes open brackets); C, D and E other than the bracket additions; F in full,
including the `SELF_REVIEWING_IMPLEMENTATION`/`APPLYING_REVIEW_FEEDBACK` self-loops staying
`phase_not_in_to_any_of`; decisions 1-4 and 6-11; the eight checkpoints, their ids, order and
dependencies. Per-checkpoint HTML comment anchor pairs are added for the amendment's
reconciliation. The registry names of CP1 and CP2 now mention the bracket. Requirement R4's
description names it, and requirement R18 is new (the recogniser, measured and fail-closed). The
fixtures in `tests/harness_contract/` were not re-captured, edited or deleted.

### Round 8 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 8 (amendment 0), `REVISE`) -- applied in revision 9

No blocking findings. The reviewer re-derived the amendment's measured facts from the untracked
fixtures and agreed with decision 12's design; the findings are one misstated fact and three
refinements.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 the plan says in five places that a fire turn holds no `user` event, and CP1 pins that as a contract test that would fail against the byte-pinned fixture | accepted: the fact is restated as "no *opening* `user` event; its only `user` events are `tool_result`s for its own `tool_use`s" | `tests/harness_contract/p5_p11_wakeup_fires.jsonl` (0-based) line 19 is a `user` `tool_result` for `toolu_01THbmXUTbAcfJKqyCRVcAYc`, the `ScheduleWakeup` `tool_use` at line 18, inside ALPHA's bracket (13-24) and fire turn (14-23); BRAVO's fire turn (26-31) holds no `user` event; both fire turns' first non-`system` event is `assistant` (lines 17, 29); the `prompt` texts occur only at lines 5 and 18. The recogniser (B) never reads `user` events, so the design is unaffected | Investigation (P11, "The fire turn itself"); CP1 fake harness (`wakeup {delay, fire_turn}`: the fire turn runs scripted steps inside the bracket, including a nested `wakeup`); CP1 contract tests (replay reproduces ALPHA scheduling BRAVO in its bracket; no `user` event before the bracketed turn's first `assistant` event; every bracketed `user` event is a `tool_result` answering a `tool_use` earlier in the same turn; no `prompt` text in any `user` event); CP2 test wording ("no opening `user` event"); decision 12 (rejected alternatives); amendment row A4's evidence cell |
| O1 the runtime-root count is wrong in two places | accepted | `~/.local/state/workflow-controller/jobs/` held 176 job directories and 80 `worker.stdout` files at `6db4f6b`; none contains `command_lifecycle`. (Re-counted during this round: 178 and 81; the one newer file is this apply session's own stream, whose only `command_lifecycle` occurrences are quoted text inside `tool_use`/`tool_result` content, not lifecycle events) | decision 12 ("Positive and distinguishing"); amendment row A3's evidence cell |
| O2 CP3's `bracket_turn {kind: task_completion}` case needs its timing pinned, and H2's residue needs a CP2 test | accepted | B condition 6 admits a bracketed turn opening at or after a pending wakeup's due time minus `WAKEUP_SKEW_SECONDS`, so the CP3 outcome depends on the turn's open time; H2 documented the residue in prose only | CP3 test (the bracketed task-completion turn opens before the due time minus 5 s); CP2 test ("H2's residue, pinned", with its before-due twin) |
| O3 a `task-notification` turn queued between a fire's `result` and its `completed(X)` becomes `multiple_bracketed_turns` | accepted: documented as a known false-`AMBIGUOUS` source | B condition 4; P11's `result`-to-`completed` gap is 0 ms by arrival offset (119.473/119.473, 236.926/236.926) | H7 |

Answers to the review request's questions are taken as given: two fires suffice for CP2 under
the conjunctive recogniser; sticky row 3 stays; the 300 s supervisor-timed stall grace stays;
H2's residue is now also test-pinned (O2).

Consequential changes beyond the findings: none. No checkpoint was added, removed, renamed or
reordered, and no requirement changed, so the registry and mapping are regenerated at revision 9
with the same checkpoint and requirement sets. The fixtures in `tests/harness_contract/` were not
modified.

### Round 9 (`MANUAL_EXTERNAL_PLAN_REVIEW`, plan revision 9 (amendment 0), `REVISE`) -- applied in revision 10

Revision 9 had passed local review (round 9, `APPROVE`). The manual external review
of the same bundle (`925016fd...`) found no blocking finding, one Important and two Optional.
Every finding was checked against the fixtures and the plan text before it was applied. All
are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 a regular bracket around an unrelated turn, opening after a pending wakeup's due time, resolves that wakeup; if that empties owned work the worker quiesces, stdin is closed and the real fire is lost, and in `IMPLEMENTING -> IMPLEMENTING` durable state could still verify | accepted; the reviewer's option 2 (conservative ownership) with option 3's bound: a match makes the wakeup `fire_matched`, still owned; it settles only by a count-checked stop or by `WAKEUP_SETTLE_SECONDS` of idle time with no bracket arriving; the window equals the overdue rule's accepted lateness plus the match skew, so a wrongly matched wakeup's real fire arrives while the session is open and is `unmatched_bracket`. Option 1 is rejected: no field identifies a fire's wakeup | `p5_p11_wakeup_fires.jsonl`: ALPHA's scheduling id `toolu_01MmH8...` and `scheduledFor` `1790350680000` occur only at lines 5-6, BRAVO's (`toolu_01THbm...`, `1790350800000`) only at 18-19, none on any line of the wakeup's own bracket (13-24, 25-32); neither `prompt` text occurs in the stream outside the scheduling `tool_use`. `p9_wakeup_cancel.jsonl`: the stop's `tool_use_result` is `{stopped: true, cancelledWakeups: 1}` with one wakeup pending, so the harness's own count is measured. Revision 9's H2 named the lost-fire path itself, and its closing "never a false success" rested on reconciliation | I11; B (`wakeups`: `pending`/`fire_matched`/`settled`; condition 6 and the match; "A wrong match cannot end the session"; `owned_work()`; purity note; row 3 and row 7; `wakeups_seen`; `wakeup_count_mismatch`); C (`WAITING` details; overdue rule reads `pending` only; "Settling matched wakeups"; settle timers restart at re-attach); D (`settled_wakeups` validation, `waiting_on.wakeups` state); E (replay facts, timers); G (the "presumed fired, not yet settled" clause, `explain`); H2 rewritten (no reconciliation argument; the double-breach residue stated); decision 5 (the window is not a time-based end); decision 12 (match, not resolution); decision 13 (new); CP1 (fake `spurious_bracket`, `delay_fire`, truthful `cancelledWakeups`; a contract test pinning that no field identifies a fire's wakeup); CP2 (the wrong-match sequence, settlement tests, quiescence); CP3 (the dangerous sequence through `launch`, count mismatch, timer restart, the pinned residue); CP4 (R14 settlement variants; R14b through `execute_step`, including the committed `IMPLEMENTING` checkpoint whose predicate would pass); CP5 (Controller loss while settling); CP7 (diagnostics); CP8 (README, ADR) |
| O1 the prompt-text assertion should be scoped to the `.jsonl` | accepted | `p5_p11_wakeup_fires.meta.json`'s `prompt` holds both texts, as capture metadata | CP1 contract tests (the assertion reads `p5_p11_wakeup_fires.jsonl` only) |
| O2 the replay comparator's treatment of non-structural events is unspecified | accepted: filtered from both sequences before comparison, by a fixed list, with a guard test | event kinds across the 18 fixtures: `system/thinking_tokens` (1285), `rate_limit_event` (84), `system/task_progress` (134), `tool_progress` (19), `system/commands_changed` (1), `system/vcs_state_changed` (1), none read by B | CP1 contract tests (projection, drop list, exact comparison of every other kind, B run with and without the dropped kinds) |

The reviewer's required acceptance property is now stated as I11 and B's "A wrong match cannot
end the session": a regular, time-compatible bracket is, by itself, never enough to remove a
wakeup from owned work. The reviewer's other acceptance criteria are kept: the fixtures in
`tests/harness_contract/` were not modified and no probe was added; the core invariants are
unchanged; and revision 10 goes back through local plan review first.

Consequential changes beyond the findings: CP2's registry name now says a matched wakeup stays
owned until it settles, and requirement R18's description names the settle rule. No checkpoint
was added, removed, reordered or re-dependent, so the registry and mapping are regenerated at
revision 10 with the same checkpoint and requirement sets.

### Round 10 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 10 (amendment 0), `REVISE`) -- applied in revision 11

The local review of revision 10's bundle (`5014a575...`) found no blocking finding, two
Important and one Optional. Every finding was checked against the plan text and the fixtures
before it was applied. All are accepted.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 a successful `ScheduleWakeup {stop: true}` inside a fire turn is always `AMBIGUOUS`: B still counts the running wakeup as `pending` against a harness count that no longer holds it (`wakeup_count_mismatch`), and the stop settles it before `completed(X)`, so the bracket is `unmatched_bracket`; CP3's and CP4's `wakeup` variants expect `SUCCESS` for exactly that script | accepted, with the reviewer's provisional-match rule: a stop inside an open bracket whose turn is open, which has broken none of conditions 1-5 and has a known open time, fixes that bracket's provisional match (condition 6 at that moment) and leaves it out of the expected count; at `completed(X)` the provisional match is the bracket's match. With no candidate, the count and the bracket are exactly revision 10's, so a stop in the real fire of a wrongly matched wakeup is still `unmatched_bracket`, and a stop inside a spurious bracket is still `wakeup_count_mismatch`. The harness's count for a stop inside a fire turn is stated as unmeasured, assumed to be the not-yet-fired count, and checked by the live probe; a disagreement fails closed | revision 10's B: a match only at `completed(X)`, the stop compared with "the number of `pending` wakeups B holds at that moment" and settling every wakeup; CP3's `wakeup` variant ("the fire turn ends with a `wakeup_stop`, so it settles at once"); CP4's R14 (`stop` when the continuation turn ends with `wakeup_stop`); the fake's `n` ("scheduled and not yet fired"). `p5_p11_wakeup_fires.jsonl`: BRAVO is scheduled at lines 18-19, inside ALPHA's bracket (13-24), so a fire turn scheduling and cancelling is the measured shape. `p9_wakeup_cancel.jsonl`'s stop ran in an ordinary turn, so no fixture measures a stop inside a fire | B (settlement way 1's expected count; the match rule; "A stop inside a bracket", new; "A wrong match cannot end the session"; `wakeups_seen`); C ("a stop from inside the fire turn itself"); G (`explain`); decision 13; CP1 (the fake counts a wakeup fired from its `started(X)`; the live probe's stop-inside-a-fire check); CP2 (stop inside a fire, with and without a nested schedule; the count-mismatch twin; the wrong-match and spurious-bracket variants; a bracket already irregular); CP3 (`wakeup` variant `SUCCESS` with counts stated, plus a fallback-cancelling variant; the spurious-bracket stop's expected count); CP4 (R14) |
| I2 the settle timer runs while a task is open, but the lateness bound it is sized from (the overdue rule) is enforced only while no task is open; a spurious match followed by a long background task could settle the wakeup, and a fire the harness deferred until after the task's completion turn would then arrive after `ENDING` | accepted: the settle timer runs only under the overdue rule's idleness predicate plus brackets (no turn, no task, no bracket open), and an opened task restarts it from zero once no task is open. The sizing argument now states the shared predicate and shows the unbroken 305 s idle stretch after `completed(X)` holds at least `WAKEUP_GRACE_SECONDS` of idleness past the due time | revision 10's C: overdue "while no turn opens and no task is open"; settle timer "only while the worker is idle: no turn open and no bracket open"; the sizing argument's "within `WAKEUP_GRACE_SECONDS` of idleness past that due time". An open task already keeps `quiescent()` false (B), so the change delays no `ENDING` | I11; B (settlement way 2); C ("Settling matched wakeups" and the sizing argument); H2's double-breach residue; decision 13; CP3 (an open task pauses the window) |
| O1 "a supervisor reaches `ENDING` only once every `fire_matched` wakeup has settled" is false on the breach paths, which end the session "as in `ENDING`" without waiting | accepted: qualified as a *quiescent* `ENDING`; on the breach paths an unsettled `fire_matched` wakeup is row 7 in `secondary_reasons`, behind row 4. The replay argument is unchanged, since the breach path's facts are flushed with its `ENDING` | revision 10's B purity note and C's closing sentence; C's overdue rule ("other wakeups, `pending` or `fire_matched`, do not defer it") and unterminated-bracket rule (both "end the session as in `ENDING`") | B (purity note); C ("Settling matched wakeups"); CP2 (row precedence) |

The reviewer's acceptance criteria are kept: the fixtures in `tests/harness_contract/` were not
modified (the CP2 stop-inside-a-fire cases edit a copy of the P5 stream inside the test), and
revision 11 goes back through local plan review first.

Consequential changes beyond the findings: the opt-in live probe gains one check (a stop from
inside a fire turn), which extends CP1's and CP8's existing live probe and adds no fixture. No
checkpoint was added, removed, renamed, reordered or re-dependent, and no requirement changed,
so the registry and mapping are regenerated at revision 11 with the same checkpoint and
requirement sets.

### Round 11 (`MANUAL_EXTERNAL_PLAN_REVIEW`, plan revision 11 (amendment 0), `REVISE`) -- applied in revision 12

The manual external review of revision 11's bundle (`ca11563e...`) found no blocking finding,
one Important and two Optional. Every finding was checked against the plan text and the
fixtures before it was applied.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 the stop-inside-fire `cancelledWakeups` contract is unmeasured, and only CP8 had to measure it, after CP2-CP7 were built on it. CP3's and CP4's `SUCCESS` paths rest on it | accepted. CP1 captures a new probe, P12, into two new fixtures. In one, a fire turn stops its own wakeup, and B expects `cancelledWakeups: 0`. In the other, a fire turn schedules a second wakeup and then stops, and B expects `1`. The contract test pins both counts, with the stop and its `tool_result` inside the one bracketed turn. CP1 is complete only when both pass. Any other observation stops CP1 and goes to `/request-plan-amendment` before CP2. A capture is discarded only for shape (the stop was not inside the fire), never for its count. CP8's live probe re-runs P12 as a final re-measurement. Decision 13 no longer says the whole settlement rule needs no new evidence | revision 11's CP1: the stop-inside-fire check was only in the opt-in live probe (`CONTROLLER_LIVE_WORKER=1`), and "Verification" required that probe only at CP8. Decision 13: "It needs no new harness evidence, so CP1 adds no probe". B: "What the harness reports for a stop inside a fire turn is **not measured**". `p9_wakeup_cancel.jsonl` line 18: the stop ran in an ordinary turn (`cancelledWakeups: 1`, one wakeup pending). `p5_p11_wakeup_fires.jsonl` holds no stop. `capture.py`'s `PROBES` has no stop-inside-fire definition | revision header; Investigation (the unmeasured list); B ("A stop inside a bracket"); CP1 (the `capture.py` exception, the new P12 bullet and gate, the fake's `n`, the contract-test facts, the live probe); CP2 (the P12 fixtures replayed unedited); CP8 (verification); decision 13; "Verification" |
| O1 scope the "appears nowhere else" prompt-text assertion to the `.jsonl` event stream | no change needed: revision 10 already does this. CP1's recogniser-evidence bullet says the assertion "reads the `.jsonl` event stream only: the matching `.meta.json` is capture metadata, and legitimately holds both texts in its recorded `prompt` (round 9's O1)" | CP1, "The recogniser's evidence, pinned from the fixtures", first sub-bullet | none |
| O2 define how replay treats the non-structural event kinds | no change needed: revision 10 already does this. CP1's contract-test bullet names the six dropped kinds (`system/thinking_tokens`, `rate_limit_event`, `system/task_progress`, `tool_progress`, `system/commands_changed`, `system/vcs_state_changed`). **Both** sequences drop them before comparing, and every other kind is compared exactly. A separate assertion shows that B's states are the same with and without them. The ALPHA-schedules-BRAVO replay uses that same comparison, so it has one pass condition | CP1, "Contract tests" (round 9's O2) | none |

The reviewer's acceptance criteria are kept. The existing fixtures in `tests/harness_contract/`
are not modified. P12 adds two separately named fixture pairs, and `capture.py` gains only
their two probe definitions. Revision 12 goes back through local plan review first.

Consequential changes beyond the findings: CP1's registry name now mentions the P12 capture and
its gate. No checkpoint was added, removed, reordered or re-dependent, and no requirement
changed. The registry and mapping are regenerated at revision 12 with the same checkpoint and
requirement sets.

### Round 12 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 12 (amendment 0), `REVISE`) -- applied in revision 13

The local review of revision 12's bundle (`fe270ed7...`) found no blocking finding, one
Important and two Optional. Every finding was checked against the plan text and `capture.py`
before it was applied.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 the P12 shape test is written partly in terms of the harness's answers (a "successful" stop `tool_result`, "exactly one `command_lifecycle` pair"), so an `is_error` stop or a later fire, both listed as gate triggers, fails the shape test and is recaptured, possibly forever, and a capture could be re-run until the harness gives the expected answer | accepted, with the reviewer's split. Shape covers only the model's actions: the first pair encloses one turn holding the stop's `tool_use` and a `tool_result` answering it (any `is_error`); in the nested probe BRAVO's schedule comes first in that turn; the model did not stop elsewhere or skip a step. The only `is_error` that is a shape failure is an input-validation error on the model's own arguments, quoted in the `README`. Any other error, a missing or non-integer `cancelledWakeups`, an unexpected count, any later `command_lifecycle` event or turn, and a fire that never came are gate observations that trigger `/request-plan-amendment`. The contract test keeps "exactly one pair" and "not `is_error`" as gate assertions, not admission criteria | revision 12's CP1: "exactly one `command_lifecycle` pair; the stop's `tool_use` and its successful `user` `tool_result`" in the shape test, against "an error, or no `cancelledWakeups`, or a successful stop that is followed by a later fire" in the gate. `p9_wakeup_cancel.jsonl`'s first `ScheduleWakeup` call was rejected on its own arguments ("`noop` is required when `stop` is not true"), which is the one error a model can cause | CP1 (P12 "Shape before count", "The gate", the P12 contract-test sub-bullet) |
| O1 the 200 s window starts from the `PROBE-DONE` `result`, not from "its last event", so the marker must be in the fire turn's reply only | accepted | `tests/harness_contract/capture.py`: `DONE = "PROBE-DONE"` (line 83); `done_at` is set by the first `result` holding it (line 354), and stdin closes `linger` seconds after `done_at` with no task open (line 339); `max_seconds` defaults to 600 (line 321). P5's prompt ends its first turn with `WAITING-ALPHA` | CP1 (P12 bullet: marker placement and the window's start) |
| O2 `TEST_RESULTS.md` still says `plan_revision` 11 | accepted | the round-12 bundle's `TEST_RESULTS.md`, "Workflow state"; `WORKFLOW_STATE.json` held 12 | the revision-13 bundle's `TEST_RESULTS.md` |

The reviewer's acceptance criteria are kept. The existing fixtures in `tests/harness_contract/`
are not modified, and revision 13 goes back through local plan review first.

Consequential changes beyond the findings: none. No checkpoint was added, removed, renamed,
reordered or re-dependent, and no requirement changed. The registry and mapping are
regenerated at revision 13 with the same checkpoint and requirement sets.

### Round 13 (`MANUAL_EXTERNAL_PLAN_REVIEW`, plan revision 13 (amendment 0), `REVISE`) -- applied in revision 14

The manual external review of revision 13's bundle (`1db1769f...`) found no blocking finding,
one Important and two Optional. Every finding was checked against the plan text, `capture.py`
and the captured fixtures before it was applied.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 P12's shape test still requires harness structure (the first `command_lifecycle` pair exists and encloses exactly one turn, and a `tool_result` answers the stop), so ALPHA never firing, a missing `tool_result`, a multi-turn bracket or malformed bracket evidence fails the shape test and is discarded and re-run, although the gate names "ALPHA never fires" as an amendment trigger | accepted, with the reviewer's split. Admission (`p12_admission.admit`) reads only the model's `tool_use` calls with their inputs, compared with the exact arguments the prompt names, and turn 1's reply text. It never reads a `command_lifecycle` event, a `tool_result`, a count, an exit status or a turn boundary after turn 1. A missing stop is a model mistake only when the harness delivered a later turn. Everything else is `ADOPT`, and `gate_deviations` decides the amendment trigger. A rejection of the exact named arguments is an observation, because those arguments are the plan's contract; only arguments that differ from the named ones are the model's mistake, which replaces revision 13's error-text judgement. An adopted probe is not run again in CP1, and three consecutive recaptures stop CP1 for the user, so neither side can loop. A capture with both a model mistake and a harness fault is recaptured: its harness answer was given to calls the plan did not ask for, and a persistent fault recurs in the next capture, where it is adopted. (Corrected in revision 15 by round 14's I1: this holds only for a model mistake *before* the cut point. A model action that reacts to a rejection of an exact named call, or that comes after the named sequence completed, is past the cut point and goes to the gate, so a persistent fault cannot be hidden behind the model's reaction to it.) The contract test covers no fire, a missing `tool_result`, a two-turn bracket and the other harness cases as `ADOPT` with deviations, the model cases as `RECAPTURE`, and `admit`'s independence from lifecycle and `user` events | revision 13's CP1 "Shape before count": "the first `command_lifecycle` pair encloses exactly one turn, and that turn holds [...] a `user` `tool_result` answering it", followed by "A capture that fails the shape test is discarded and the probe is run again", against "The gate": "If ALPHA never fires at all and the model did not stop it, that is an observation too". `p9_wakeup_cancel.jsonl` 1-based line 9 (the `noop` rejection of arguments the prompt had named; 0-based line 8) and 1-based line 8's input without `noop` (0-based line 7) | revision header; CP1 (Files, the `capture.py` exception, P12 "Probe definitions", "Admission reads the model's actions only", "The gate", the P12 contract-test sub-bullets) |
| O1 the 200 s linger is described, but `capture.py` defaults `linger` to 0 and P5's definition sets none | accepted: both P12 definitions carry `"linger": 200` explicitly, and the prompts name `noop: false` | `tests/harness_contract/capture.py`: `linger = float(probe.get("linger", 0))` (line 322); `p9_wakeup_cancel` sets `"linger": 200` (line 202); `p5_p11_wakeup_fires` (lines 204-215) sets none | CP1 (P12 "Probe definitions") |
| O2 the contract test forbids any event after `completed(X)`, while the gate names only a later `command_lifecycle` event or turn | accepted, matching the gate: no later `command_lifecycle` event and no later turn event; the six dropped non-structural kinds are ignored; any kind outside both lists is a deviation, as in the replay comparison | revision 13's contract-test wording "No event follows `completed(X)` except the session's own end"; the drop list in CP1's "Contract tests" (round 9's O2) | CP1 (the P12 contract-test sub-bullet) |

The reviewer's acceptance criteria are kept. The existing fixtures in `tests/harness_contract/`
are not modified; P12 adds only its two separately named fixture pairs and the new
`p12_admission.py`. Revision 14 goes back through local plan review first.

Consequential changes beyond the findings: the prompts name `noop: false` on both scheduling
calls (so the exact-argument comparison has a complete argument set), and the three-recapture
stop. No checkpoint was added, removed, renamed, reordered or re-dependent, and no requirement
changed. The registry and mapping are regenerated at revision 14 with the same checkpoint and
requirement sets.

### Round 14 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 14 (amendment 0), `REVISE`) -- applied in revision 15

The local review of revision 14's bundle (`46895b25...`) found no blocking finding, one
Important and one Optional. Every finding was checked against the plan text and the captured
fixtures before it was applied.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 `admit` judges the model's whole call sequence, so a model action that reacts to a harness answer the plan names as an observation is judged as a model mistake. Examples are a retry after the exact named arguments are rejected, or a second stop in a later fire turn that the prompt's own "when ALPHA fires, call stop" invites. Such a capture is recaptured, three times over, and CP1 stops with a "prompt problem" instead of reaching `/request-plan-amendment`. A transient version is replaced by a later clean run | accepted, with the reviewer's first mechanism. `admit` judges the model's actions only up to the **cut point**: the call that completes the named sequence (`[ALPHA, stop]` or `[ALPHA, BRAVO, stop]`), or the first exact named call answered with `is_error: true`, whichever comes first. Everything after the cut point, calls and reply text alike, is `gate_deviations` input. `admit` reads one harness fact, the `is_error` flag of `tool_result`s answering exact named calls, and the independence test is narrowed to match. A rejection of arguments the prompt did not name is no cut point, so the model's own argument mistake stays a recapture. The trade is stated in the plan: a spontaneous extra call after the sequence completes goes to the amendment, not to a recapture. That error is visible and recorded, and the opposite error hides a harness answer. The shortest-prefix alternative is rejected because it still judges a retry that comes before completion. The prompts also say to make each call once, never retry, and in any other turn reply `PROBE-EXTRA` with no call. New contract cases: ALPHA rejected then retried, or then `PROBE-DONE`; the single stop rejected then retried; BRAVO rejected then retried, or then no stop; a second fire turn with a stop, or with `PROBE-EXTRA`; a same-turn second stop after completion. All are `ADOPT` with deviations. "An extra stop in a later turn" is replaced by a pre-completion extra call (BRAVO twice, no rejection between), which stays `RECAPTURE` | `tests/harness_contract/p9_wakeup_cancel.jsonl` 0-based line 7 (the `ScheduleWakeup` call without `noop`), line 8 (its `is_error` rejection, "`noop` is required when `stop` is not true.") and line 13 (the same call retried at once, in the same turn, with `noop: false`). This is a measured model reacting to a rejection. Revision 14's CP1 "Admission reads the model's actions only": "the model's tool calls after turn 1, in stream order [...] are not exactly the probe's sequence"; the model-mistake case "an extra stop in a later turn"; the Investigation's list of a stop with nothing pending as unmeasured | revision header; CP1 (P12 prompt instructions, "Admission reads the model's actions only, up to the cut point", the stated trade, the gate sub-bullet's "exactly the named sequence" assertion, the `admit`/`gate_deviations` contract-test cases and the narrowed independence test); round 13's disposition sentence on mixed captures, corrected in place |
| O1 the three-recapture stop does not say what the user may do next, or whether the count resets after a prompt rewrite | accepted. A rewording that changes nothing the plan names (the exact arguments, the named sequence, the three markers, `"linger": 200`, the argv) is a CP1 implementation change, and the count restarts at zero for the reworded prompt. Changing any named item is a plan change and goes to `/request-plan-amendment` first. The `README` keeps every discarded attempt, its reasons and its prompt text across every rewrite | revision 14's CP1: "After three consecutive `RECAPTURE`s of one probe, CP1 stops and reports the prompt to the user", with nothing following | CP1 ("The user's options there") |

The reviewer's acceptance criteria are kept. The existing fixtures in `tests/harness_contract/`
are not modified, and the settlement design (B, C, decision 13) is unchanged. Revision 15 goes
back through local plan review first.

Consequential changes beyond the findings: the `PROBE-EXTRA` marker. It holds no `PROBE-DONE`,
so `capture.py`'s `done_at` and the 200 s window are unaffected. The gate now also asserts that
the fixture's calls are exactly the named sequence. No checkpoint was added, removed, renamed,
reordered or re-dependent, and no requirement changed. The registry and mapping are
regenerated at revision 15 with the same checkpoint and requirement sets.

### Round 15 (`LOCAL_MODEL_PLAN_REVIEW`, plan revision 15 (amendment 0), `REVISE`) -- applied in revision 16

The local review of revision 15's bundle (`2e0dc1f3...`) found no blocking finding, one
Important and two Optional. Every finding was checked against the plan text and the captured
fixtures before it was applied.

| finding | disposition | evidence checked | where it is resolved |
| --- | --- | --- | --- |
| I1 a turn after turn 1 that the model correctly answers `PROBE-EXTRA` with no call, with no recognisable fire after it (an unasked turn and ALPHA never firing, or a fire whose prompt the model cannot see), has no cut point, so its calls after turn 1 (`[]`) are not the whole rest and the capture is `RECAPTURE`. That contradicts the plan's own `ADOPT` list ("ALPHA never firing", "any later event or turn") and its claim that nothing the harness did can make a capture be re-run | accepted, with the reviewer's direction. With no cut point, a missing call is excused when the model made no call after turn 1 and wrote at least one `text` block after turn 1, every one exactly `PROBE-EXTRA`. That text is model-controlled, so admission reads no more harness output, and the narrowed independence property is kept (with one added case: replacing off-script text by other off-script text does not change the verdict). Free text (including `PROBE-DONE`), an empty reply, a call to another tool, or a partial named sequence followed by `PROBE-EXTRA` stays `RECAPTURE`, so "a delivered fire turn with no stop and no rejection" is still a model mistake. Because admission reads no turn boundary after turn 1, a fire turn with an empty reply followed by a `PROBE-EXTRA` turn is `ADOPT`, which is the stated trade's direction (amendment, visible) rather than a recapture. The alternative of reading lifecycle events to locate the fire turn is not taken: it reopens round 13's I1, since a malformed or missing bracket would again decide admission. The gate gains one assertion: no turn event before `started(X)` outside turn 1, so an unasked turn before the fire is a deviation. New contract cases: `ADOPT` for a lone unbracketed `PROBE-EXTRA` turn with nothing after it, a bracketed fire answered `PROBE-EXTRA`, and a `PROBE-EXTRA` turn before the fire and stop (with the new gate deviation); `RECAPTURE` for the same lone turn with an off-script reply, a fire turn answered `PROBE-DONE` or with an empty reply, a `PROBE-EXTRA` reply with a call to another tool, and BRAVO with no stop followed by a `PROBE-EXTRA` turn | revision 15's CP1 second `RECAPTURE` bullet ("With no cut point, they are not the whole rest"), its prompt instruction ("In any turn other than the two named ones, the model replies with exactly `PROBE-EXTRA` and calls no tool"), its `ADOPT` list and its "nothing the harness did [...] can make a capture be re-run". `tests/harness_contract/p5_p11_wakeup_fires.jsonl` 0-based lines 12-14: turn 1's `result` is followed directly by `started(X)` and the fire's `system/init`, with no turn event between, so the new gate assertion holds on the measured shape | revision header; CP1 (what `admit` reads, "The `PROBE-EXTRA`-only case", the `ADOPT` list, the gate sub-bullet's pre-bracket assertion, the `admit`/`gate_deviations` contract cases and the independence test) |
| O1 the P12 "Probe definitions" bullet cites `p9_wakeup_cancel.jsonl` "line 9" (1-based) while round 14's disposition and the contract case cite the same event as 0-based line 8 | accepted. The bullet now reads "0-based line 8, the `tool_result` answering line 7's call", and round 13's disposition row is annotated in place with both bases | `tests/harness_contract/p9_wakeup_cancel.jsonl`: 0-based line 7 is the `ScheduleWakeup` call without `noop`, 0-based line 8 its `is_error` `tool_result` ("`noop` is required when `stop` is not true.") | CP1 ("Probe definitions"); round 13's disposition row |
| O2 "the model's call that completes the named sequence" could be read as containing it as a subsequence | accepted. The cut-point definition now says the model's calls so far, in stream order, equal the named sequence exactly | revision 15's CP1 cut-point definition and the gate's "exactly the named sequence" assertion | CP1 (the cut-point definition) |

The reviewer's acceptance criteria are kept. The existing fixtures in `tests/harness_contract/`
are not modified, and the settlement design (B, C, decision 13) is unchanged. Revision 16 goes
back through local plan review first.

Consequential changes beyond the findings: the gate's pre-bracket assertion above. No
checkpoint was added, removed, renamed, reordered or re-dependent, and no requirement changed.
The registry and mapping are regenerated at revision 16 with the same checkpoint and
requirement sets.
