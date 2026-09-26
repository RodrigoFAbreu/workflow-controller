# Workflow Controller

Controller for orchestrating Workflow-managed repositories.

This repository is developed independently from the Workflow Manager and from consumer repositories such as RepFlow.

## What it does

```
Workflow  ->  Workflow Manager / Bootstrapper  ->  Workflow Controller  ->  managed development repositories
```

The Controller automates operation of the Workflow (frozen v2.5.1 --
`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES` is the admission
gate, and a target on any other release is refused) against a managed
development repository. At each step it reads the target's durable
Workflow state, decides the next action, and either launches a fresh
`claude` worker to run the real Workflow command and verifies the result,
or stops and reports what a human must do:

- at the **plan-stage** phases (`PLANNING`, `REVISING_PLAN`,
  `AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`/
  `AWAITING_EXTERNAL_PLAN_REVIEW`) it launches the plan-stage commands, as
  Generation 1 always has;
- at the **implementation stage** it launches `/milestone-implement` from
  `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` (governing workflow
  version `"2.1"`/`"2.2"`), and, for a `"2.2"` work item, the local
  implementation review, the ingestion of an already-pasted manual
  external verdict, and the remediation of a `REVISE`. So one
  `workflow-controller run <repo>` carries a `"2.2"` work item from
  `IMPLEMENTING` through every checkpoint, the final self-review pass,
  local implementation review and local `REVISE` remediation, up to the
  next genuine human gate;
- at every other phase it observes, decides and reports. It never
  performs the manual external review, and it never runs a user-only
  command.

Which selected action is launched is decided by one rule, described under
"Automatic dispatch" below.

A repository that commits a `.workflow-controller/policy.json` also gets
one short-lived `milestone/<work-item-id>` branch and one Draft pull
request per milestone, and a release published from `main` whenever the
version changes (see "Milestone branches and pull requests" and
"Releasing"). A repository without that file behaves exactly as under
1.1.1.

Two invariants hold throughout: **the Controller never writes Workflow
lifecycle state itself** (every durable transition is made by a worker
running a real Workflow command), and **the Controller never crosses a
human gate** (the user-only denylist is *derived* from the installed
`.claude/commands/*.md`, and at the 2.5.1 reference release it is four
commands -- `/approve-review`, `/accept-milestone`,
`/recover-implementation-provenance` and `/request-plan-amendment` --
none of which is ever selected or executed).

## Installation

The Controller is released as a wheel attached to a GitHub Release
(`https://github.com/RodrigoFAbreu/workflow-controller/releases`). Each
release carries the wheel, `workflow_controller-<version>-py3-none-any.whl`,
and a `SHA256SUMS` file. Install it with pipx, so the Controller runs in
its own venv and never from a checkout:

```bash
BASE=https://github.com/RodrigoFAbreu/workflow-controller/releases/download/v1.1.1
pipx install "$BASE/workflow_controller-1.1.1-py3-none-any.whl"
```

**Verify** a release before you install it. Download both assets, check
the wheel against `SHA256SUMS`, install the verified file, then check
what is running:

```bash
curl -fLO "$BASE/workflow_controller-1.1.1-py3-none-any.whl"
curl -fLO "$BASE/SHA256SUMS"
sha256sum -c SHA256SUMS
pipx install ./workflow_controller-1.1.1-py3-none-any.whl
workflow-controller --version
```

`--version` prints `workflow-controller 1.1.1` on its first line and the
runtime on its second, for a release
`runtime: package (release v1.1.1; built from <commit>; package <digest>)`
(see "Runtime identity").

**Upgrade.** First check that nothing is running: `workflow-controller status`
must show `active: none`. Then install the new wheel over the old one:

```bash
pipx install --force "$BASE/workflow_controller-<new version>-py3-none-any.whl"
```

What an upgrade does to a Controller that is already running:

- A running `step` or `run` executes from its own snapshot of the
  installed package, so the new bytes never reach it.
- A new release with the same generation (`controller/GENERATION.json`)
  is ignored by a running `run`, which finishes on the old version.
- A new generation stops a running `run` at its next orchestration
  boundary with exit `50` and a handoff record, the same as a newer
  committed generation does for a source checkout. Rerun `run` with the
  new version.
- Two cases fail closed instead. Both surface as `SourceSnapshotError`
  (exit `20`, `raised_by: "detect"`), not `50`, and in both you simply
  rerun `run`:
  - `pipx install --force` deletes and recreates the venv, so a `run`
    whose boundary check lands in that window finds no installed package;
  - an upgrade that changes Python's minor version moves the
    `site-packages` path the snapshot recorded as `origin_source_root`,
    so the installed package is no longer where the run looks for it.

**Rollback** is the same command with an older wheel:
`pipx install --force <older wheel URL>`. Going back across a
generation has two consequences:

- a job record written by the newer generation is refused as
  `StaleJobRecordError` by the older one, and `resume --abandon` refuses
  it too. Reinstall the newer version and let its own `resume` clear the
  record, then roll back. Running `resume` before the rollback avoids
  this;
- a `run` still executing the newer generation raises
  `GenerationHandoffPendingError` (exit `20`) at its next boundary, since
  the installed generation is now older than the running one.

**Development install.** From a checkout:

```bash
pip install -e .
```

An editable install runs the checkout itself, so it is a **source**
runtime. `step`, `run` and `resume` snapshot the checkout's committed
`HEAD` with `git archive`. With uncommitted changes to the Controller's
own files they refuse (`DirtyControllerSourceError`) unless you pass
`--allow-dirty-source`, which snapshots the working tree instead. Its
runtime root is `<checkout>/.controller/` (ladder row 3).

A plain `pip install .` of a checkout builds a local wheel and installs
it as a **package** runtime, like a release. The earlier limitation, that
only a Controller installed from its own checkout could run `step`, `run`
or `resume`, is gone.

## CLI surface

| Command | Behaviour |
|---|---|
| `workflow-controller inspect <repo>` | managed-repo verification + Workflow state summary, and the lifecycle lock's state; read-only |
| `workflow-controller explain <repo>` | the pending job files (each with the command that clears it), the lifecycle lock's state, then the next-action decision with full evidence, and, at a gate, exactly what a human must do; read-only, always exits 0 |
| `workflow-controller step [--follow] <repo>` | execute exactly one automatic action, validate the transition, stop |
| `workflow-controller run [--follow] [--max-steps N] <repo>` | repeat `step` until a gate (every implementation-stage human gate included), a declined action, a no-action phase, a failure, an incomplete step, a refusal, or a pending handoff |
| `workflow-controller resume <repo>` | re-attach to a job whose worker still runs with no Controller supervising it and supervise it to its end, then reconcile this target's non-terminal job records and report; never launches a worker |
| `workflow-controller resume --abandon JOB_ID [--acknowledge-unverifiable-worker] <repo>` | mark one pending job file terminal instead of reconciling it (see "Job dispositions") |
| `workflow-controller status` | Controller-owned view: the running Controller, pinned identity, job records, pending handoff, active runs and jobs; read-only |
| `workflow-controller follow [--job JOB_ID \| --run RUN_ID] [--from-start] [<repo>]` | render a run's or job's events and worker output (see "Observing workers"); writes nothing |
| `workflow-controller --work-item <id> milestone-binding --new-pr <repo>` | continue a milestone whose binding is in a refusal state on the same branch, with a new Draft PR (see "Milestone branches and pull requests"); launches no worker and touches no ref or pull request |
| `workflow-controller --work-item <id> milestone-binding --abandon <repo>` | retire such a binding, under the preconditions stated there; exactly one of `--new-pr`/`--abandon` is required |
| `workflow-controller --version` | the version, then the running runtime (see "Runtime identity") |

Global options -- declared on the top-level parser, so they are accepted
only *before* the subcommand: `--runtime-dir`, `--work-item`,
`--workflow-manager`, `--claude-binary`, `--permission-mode` (default
`auto` for lifecycle workers; an explicit value such as `acceptEdits` is
passed through unchanged), `--timeout` (seconds; the default is no limit,
see "Concurrency and worker lifecycle"), `--model`, `--effort`,
`--role-model ROLE=MODEL`, `--role-effort ROLE=EFFORT`, `--routing-config
PATH` (see "Worker routing"), `--allow-dirty-source`, `--json`.
`WORKFLOW_CONTROLLER_HOME` sets the runtime root when `--runtime-dir` is absent (`--runtime-dir` >
`WORKFLOW_CONTROLLER_HOME` > default); it is the only environment
variable in the CLI's operator-facing contract.

A gate's safe resume command that names `explain` with `--work-item`
after the subcommand is shorthand: run it with the global option first
and the repository last, as in
`workflow-controller --work-item <id> explain <repo>`.

`run` is a bounded loop, not a daemon: it terminates, it does not poll,
it holds no socket and it schedules nothing. Its convergence is bounded by
`--max-steps` (default 20): a local `REVISE` -> remediation -> review cycle
is two jobs, so the default allows about ten rounds before exit 16.

Exit codes are part of the CLI's contract and are normative in
[`docs/adr/0001-controller-generation-1-architecture.md`](docs/adr/0001-controller-generation-1-architecture.md)
-- read that table to write an outer supervisor's `case` statement. Exit
45 (the target worktree is held) came with the automatic lifecycle
orchestration work. Version 1.1 adds no exit code: `follow` uses only
`0`, `2` and `20`
([`docs/adr/0002-release-runtime-identity-and-observability.md`](docs/adr/0002-release-runtime-identity-and-observability.md)).

## Automatic dispatch

Each phase handler selects the next action from the target's evidence, or
returns a human gate. Whether a selected action is launched is decided by
one rule, the same at every phase: it is launched **iff** a verifiable
expected outcome is declared for `(phase, governing workflow version,
command)` (`controller.decision.AUTOMATIC_TRIPLES`, held equal to the
keys of `controller.job.EXPECTED_OUTCOMES` by a test). Otherwise it is
reported as declined (exit 15), with a reason that names the phase, the
command and the missing triple. Unknown or ambiguous states still fail
closed (exit 20).

At the 2.5.1 reference release, that makes these launches automatic:

| Phase | Version | Command |
|---|---|---|
| no work item yet | none | `/milestone-plan` |
| `PLANNING` | `"1"`, `"2.1"`, `"2.2"` | `/milestone-plan` |
| `AWAITING_LOCAL_PLAN_REVIEW` | `"2.1"`, `"2.2"` | `/review-plan` |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `"2.1"`, `"2.2"` | `/record-manual-plan-review` |
| `REVISING_PLAN` | `"2.1"`, `"2.2"` | `/apply-plan-review` |
| `AWAITING_EXTERNAL_PLAN_REVIEW` | `"1"` | `/apply-plan-review` |
| `IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION` | `"2.1"`, `"2.2"` | `/milestone-implement`, once the plan approval is `CURRENT` and `HEAD` records every checkpoint completion and phase transition the working tree records |
| `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `"2.2"` | `/review-implementation` |
| `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `"2.2"` | `/record-manual-implementation-review`, only for an already-pasted, admissible manual `APPROVE`/`REVISE` |
| `APPLYING_REVIEW_FEEDBACK` | `"2.2"` | `/apply-implementation-review`, for an admissible `REVISE` |

Every one of these is still gated first when its evidence says so: a
`REJECTED` bundle marker, a stale or incoherent bundle, a `BLOCK` verdict,
an inadmissible verdict, a missing or stale plan approval, a checkpoint
`COMPLETE` or a `SELF_REVIEWING_IMPLEMENTATION` transition that the
working tree's `WORKFLOW_STATE.json` records but `HEAD` does not. Everything
else is a gate or a decline. That includes performing the manual external
review, `/approve-review`, `/accept-milestone`, the functional-review
stage (`/apply-functional-review` is declined), `AMENDING_PLAN`, and
consuming a `"2.1"` external implementation verdict. A `"1"` work item's
`/milestone-implement` is declined, because that branch writes no state
the Controller could verify.

A launched job is verified against durable Workflow and Git state, never
against what the worker says. A phase change alone is not success: each
implementation-stage outcome also checks the artifacts it promises (the
checkpoint completion committed at `HEAD`, a coherent implementation
bundle generated at the live `HEAD`, a review ledger bound to the
bundle's content). A stale, withdrawn, `REJECTED` or mis-bound artifact
makes the job `FAILED` (exit 30), and `run` stops there. The next decision
launches nothing either. After an uncommitted checkpoint completion or
`SELF_REVIEWING_IMPLEMENTATION` transition, for example, it is a gate
naming each uncommitted fact, because another `/milestone-implement`
worker could build on the completion or commit it. A human commits the
completion as the Workflow step would have, with its trailers, or
discards it and reruns the step.

## Worker routing

Every launched worker has a role, derived from durable state alone, and
each role has a built-in model and effort:

| Role | Launched for | Model | Effort | Single-agent |
|---|---|---|---|---|
| `milestone-implement` | `/milestone-implement` with a checkpoint outstanding | `claude-opus-5-5` | `xhigh` | no |
| `milestone-implement-self-review` | `/milestone-implement` from `SELF_REVIEWING_IMPLEMENTATION`, or from `IMPLEMENTING` with every checkpoint complete (the final full-diff self-review pass) | `claude-opus-5-5` | `xhigh` | yes |
| `apply-plan-review` | `/apply-plan-review` | `claude-opus-5-5` | `xhigh` | no |
| `apply-implementation-review` | `/apply-implementation-review` | `claude-opus-5-5` | `xhigh` | no |
| `review-plan` | `/review-plan` | `claude-opus-5-5` | `xhigh` | yes |
| `review-implementation` | `/review-implementation` | `claude-opus-5-5` | `xhigh` | yes |
| `milestone-plan`, `record-manual-plan-review`, `record-manual-implementation-review` | those commands | inherit | inherit | no |

"Inherit" means no flag is passed, so the `claude` CLI's own
configuration applies. Model and effort are resolved separately, and the
first of these that sets a value wins:

1. `--role-model ROLE=MODEL` / `--role-effort ROLE=EFFORT` (each
   repeatable, once per role);
2. `--model` / `--effort`, for every role;
3. the `--routing-config` file's entry for the role;
4. that file's `default` entry;
5. the built-in value in the table above;
6. inherit.

For example, `workflow-controller --role-effort review-implementation=max run <repo>`
raises only the implementation reviewer's effort, and
`workflow-controller --routing-config routing.json run <repo>` reads a
file shaped like this (both `default` and `roles` are optional; each
entry sets `model`, `effort` or both):

```json
{
  "schema_version": 1,
  "default": {"effort": "high"},
  "roles": {"review-implementation": {"model": "claude-opus-5-5", "effort": "max"}}
}
```

Only `step` and `run` use these options. Model and effort values are
passed to `claude` unvalidated -- the `claude` CLI is the authority over
which exist -- except that a value must be non-empty and must not begin
with `-`. An unknown role, a missing `=`, a role assigned twice, or an
unusable value on the command line is a usage error (exit 2). The same
problems in the config file, or any other malformed config, are
`RoutingConfigError` (exit 20). Either way no job record is written and
no worker runs.

Single-agent cannot be overridden: the review roles and the final
self-review pass always run with the subagent-spawning tools disallowed
(`--disallowedTools Agent,Workflow,Skill`). `Skill` is on the list
because a `context: fork` skill runs in a subagent; the task's own slash
command still runs, since the CLI expands it without that tool. Every
worker is a fresh session: the Controller never passes `--resume`,
`--continue`, `--fork-session` or `--session-id`. The resolved route,
with the level each value came from, is recorded in the job record's
`worker_route`.

## Implementation-review apply rounds

`/apply-implementation-review` at a `"2.2"` `APPLYING_REVIEW_FEEDBACK`
has three safeguards:

- **The pending-write addendum.** After an automated local `REVISE`, or
  an ingested manual `REVISE`, the review-stage write that moved the work
  item to `APPLYING_REVIEW_FEEDBACK` is normally still uncommitted. A
  worker that followed the command text literally would then land a
  malformed generation-record commit. So, only while `HEAD` does not yet
  record `APPLYING_REVIEW_FEEDBACK`, the worker's task is the command
  followed by a pinned Controller note. The note authorizes exactly one
  extra act: commit that pending state write alone, with a
  `Workflow-Work-Item` trailer, before any other commit. It names no
  user-only command. `explain` prints it, and the job record keeps it as
  `selected_action.task_addendum`. This is the only place a worker's task
  is not the bare selected command.
- **The malformed-`T` gate.** If a worker lands the generation-record
  commit `T` anyway, with no phase change in its own diff (`OPUS-R101-001`),
  the next decision names that commit and says that no Workflow command
  repairs it. A human repairs the unpushed history so the pending write
  lands alone before `T`, then reruns the command's step 7. The
  Controller does not offer the ordinary regeneration steps there,
  because the generator would refuse them.
- **The relaunch bound.** An `/apply-implementation-review` attempt that
  did not verify is never relaunched automatically against the bundle it
  started from. Once such an attempt passed its own step 4, the bundle no
  longer matches its manifest, so every retry would refuse at step 1. The
  gate names the earlier job and the human's two ways on: restore the
  edited bundle file from `review-bundle.tar.gz` and rerun the command in
  a supervised session, or complete the round by hand. A new bundle
  generation lifts the bound.

## Concurrency and worker lifecycle

A worker's lifecycle action is finished only when the worker has really
finished, not when one of its turns ends. Workers routinely start long
verification in the background and end a turn saying they will continue
when it completes. The Controller keeps that session alive, lets the
harness resume it, and reconciles the job only once the worker and every
process it owns have ended. The design record is
[`docs/adr/0004-worker-lifecycle-ownership.md`](docs/adr/0004-worker-lifecycle-ownership.md).

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

**The drain bound.** After `claude` exits, the Controller waits for
the remaining owned processes for at most 600 s
(`worker.DRAIN_DETACH_SECONDS`). If any is still alive then, it ends
**nothing**. It *detaches*: the record stays `LAUNCHED` at `DRAINING`
with `drain_detached_at`, a `worker_drain_detached` event names the
processes, the anchor keeps the lock, and the command exits 45 naming
each pid with its command line. Either run `workflow-controller resume
<repo>`, which re-attaches and drains again with a fresh bound, or end
the processes and then run `resume`. No later action can start while
they run.

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

There is no default worker timeout: `step` and `run` wait for as long
as the worker owns work. `--timeout SECONDS` is an explicit opt-in, now
over the whole owned lifetime; when it fires, the Controller ends the
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
  job record's worker is `active` (see "Job dispositions"). When the
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
  "Restart: `resume` re-attaches"), supervises it to its end and records
  it `COMPLETED`. It then reconciles each pending record against the
  target's durable state, under the lock. A record that still owns work
  (its worker `active` or `unverifiable`, a recorded or tagged owned
  process alive, or an owned-process scan that could not complete) is
  never reconciled: `resume` exits 45 and names it. A record written by
  this version that it cannot reconcile belongs to a worker that has
  definitively ended, so `resume` persists it `FAILED`
  (`UnreconcilableJobError`) and exits 20 once. The next `resume` passes
  it, and the next `step` decides from evidence.
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
(written by Controller 1.2.x or earlier, whose worker ran in print mode)
is handled exactly as before: it is never re-attached, and `resume`
exits 45 while its recorded worker is `active` or `unverifiable`. Upgrade
the Controller between jobs, not during one.

## Controller-owned runtime state

The Controller keeps its own durable state under a runtime root: pinned
source identity, job records (`jobs/`, with abandoned originals under
`jobs/abandoned/`), per-job logs (`jobs/<job_id>/`), run records
(`runs/`), milestone binding records (`repositories/`, below) and the
pending handoff. The root is resolved by a ladder, and
the first row that applies wins:

1. `--runtime-dir`;
2. `WORKFLOW_CONTROLLER_HOME`;
3. `<origin checkout>/.controller/`, only for a **source** runtime;
4. `$XDG_STATE_HOME/workflow-controller`, or
   `~/.local/state/workflow-controller` when `XDG_STATE_HOME` is unset.

A package runtime (a pipx or other wheel install) never uses row 3, even
when its venv sits inside a Git checkout, so without the first two rows
it uses row 4. Before 1.1 a wheel installed into a venv inside a
checkout resolved to `<site-packages>/.controller`. Every acting command
from such an install failed, so that directory holds no job history, and
it can be deleted. `status` prints the resolved root and its ladder row.

This tree is disposable by design. It is never part of any managed
target repository's own state, and the Controller never writes into a
target repository's `WORKFLOW_STATE.json` -- only a worker running a
real Workflow command does that. The lifecycle lock is an `flock` on the
target's existing git directory and creates nothing there. Deleting
`runs/` or `jobs/<job_id>/` loses presentation history only: no
lifecycle decision reads them.

`repositories/<repo_key>/milestones/` holds one binding record per
milestone of a policy-enabled target (`<work_item_id>.json`), its
append-only `<work_item_id>/events.jsonl`, and any
`<work_item_id>.abandoned-<n>.json` left by a re-planned id. `repo_key`
is the SHA-256 of the target's canonical `git rev-parse --git-common-dir`,
so every worktree of one repository shares it. Unlike `runs/`, these
records **are** read by lifecycle decisions: they are the Controller's
only memory of which branch and pull request belong to a milestone. Do
not delete them while a milestone is in flight, and include them in any
backup of the runtime root. If they are lost anyway (a new machine, for
example), the next step with `HEAD` on the milestone branch re-adopts the
binding and its one open pull request. A branch that exists only on the
remote, or two merged pull requests for one branch, refuses: the
Controller cannot tell which is its own, and the recovery is to restore
`repositories/<repo_key>/` from the backup. A 1.1.1 runtime ignores the
directory.

## Runtime identity

Every Controller process knows what code it is running, and records it.
There are three runtime kinds:

- **`package`**: an installed wheel. The wheel carries
  `controller/BUILD_INFO.json`, written by the build (`setup.py`'s
  `build_py` hook), which records the version, the source commit, whether
  the build's inputs had uncommitted changes, a digest of the package's
  files, the build origin (`release` or `local`) and the release tag.
  A package runtime never runs Git and never consults the checkout it was
  built from. `step`, `run` and `resume` copy the installed package into
  a snapshot and check the copy against the recorded package digest; an
  installation edited after it was built is refused (exit `20`). A wheel
  built from uncommitted changes, or with no verifiable provenance (for
  example from an sdist), needs `--allow-dirty-source`, like a dirty
  checkout.
- **`source`**: a Git checkout whose top level holds the running
  `controller/` package, tracked. This is what `pip install -e .` gives
  you.
- **`unidentified`**: anything else, for example a wheel built without the
  hook, or a checkout that contains a stray `controller/BUILD_INFO.json`
  (delete it to run from source). Read-only commands still work and print
  the reason. `step`, `run` and `resume` refuse with exit `20`.

The Controller never reads installer metadata such as `direct_url.json`.

`workflow-controller --version` prints two lines and writes nothing:

```
workflow-controller 1.1.1
runtime: package (release v1.1.1; built from 0123456789ab; package 3f2a1c9d0b7e)
```

Line 1 is always `workflow-controller <version>`. Line 2 is one of
`package (release ...)`, `package (local build from <commit>)` with
`, uncommitted changes` when that applies, `package (local build, unknown
provenance)`, `source (<checkout> @ <commit>)` with the same suffix, or
`unidentified (<reason>)`. `status` opens with the same text:
`controller: workflow-controller 1.1.1 -- package (...)`.

Every job record carries a `controller_runtime` block (`runtime_kind`,
`version`, `source_kind`, `source_commit`, `tree_digest`,
`package_digest`, `build_origin`, `release_tag`, `generation`), and so do
`identity.json` and `SOURCE_PIN.json`. `inspect --json` and
`explain --json` carry it as `controller`. The older
`controller_generation`/`controller_source_commit`/`controller_source_tree_digest`
fields stay.

What `build_origin: "release"` proves is limited. The build sets it from
an environment variable, so any local build can claim it. The proof that
a wheel is a release is its checksum in the GitHub Release's
`SHA256SUMS`, not `--version`. Build-provenance attestation may come
later.

The version (`pyproject.toml`'s static `[project].version`, `MAJOR.MINOR.PATCH`) and the
generation (`controller/GENERATION.json`) are separate. The generation is
the compatibility axis that handoff and job-record validation compare.
Version 1.1.1 is still generation 1.

## Observing workers

Workers run `claude -p --input-format stream-json --output-format
stream-json --verbose ...` (see "Concurrency and worker lifecycle"), and
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
  20 events before going live; `--from-start` replays everything. With
  the global `--json` it prints normalised events, one JSON object per
  line. It ends with exit `0` when the followed run or job ends, printing
  the run's own exit code rather than returning it. Ctrl-C, or a stdout
  whose reader has gone (`follow | head`), ends it with exit `0`, and the
  run is unaffected. An unknown id, another target's
  record or an unreadable record is exit `20`.
- **`workflow-controller status`** lists what is active across the
  runtime root under `active:` (or `active: none`): each running run with
  its Controller's liveness, each non-terminal job with its worker's
  liveness, and the exact `follow` command for each. When `resume`, a
  held lock (exit `45`) or a Ctrl-C reports a running worker, it prints
  the `follow` command too.

For a job whose record carries `worker_state`, `status`, `follow`'s
heartbeat, `explain` (after each pending job, as `activity:`) and a
`jobs:` block in `inspect` say what the worker is doing: running (with
its turn), waiting (on which background tasks, which wakeups and their
due times, which harness commands, and which wakeups are presumed fired
but not yet settled), draining (which owned processes are still alive,
and which recognised daemons are not owned), whether a Controller is
attached (else `no Controller attached -- workflow-controller resume
<repo> re-attaches`), and whether the job only awaits reconciliation.
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
stderr, and the final result with turns, cost and duration, interleaved
with the Controller's job and run events. After 30 s with no new event
it prints a heartbeat naming the worker's pid and elapsed time. Thinking
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

## Safety model

- **Never writes Workflow state.** `controller/target_state.py` exposes
  no write function at all; every durable Workflow lifecycle change is
  made by a worker running a real Workflow command in the target
  repository.
- **Never crosses a human gate.** The decision engine launches only by
  the automatic-dispatch rule, whose declared outcomes cover only
  commands in its eight-command selected set (`/milestone-plan`,
  `/review-plan`, `/record-manual-plan-review`, `/apply-plan-review`,
  `/milestone-implement`, `/review-implementation`,
  `/apply-implementation-review`, `/record-manual-implementation-review`).
  The worker layer refuses to execute any of the four user-only commands
  (`controller.worker.USER_ONLY_COMMANDS`) even if handed one directly,
  including inside a task's text -- two independent mechanisms fed by two
  different sources.
- **Never trusts a worker's word.** Every launched job is verified
  against durable Workflow and Git state, including the artifacts its
  command promises; the worker's own report is never read to decide.
- **One worker per worktree, and never a replacement too early.** The
  lifecycle lock, the recorded worker and the processes it owns decide
  when the previous worker has ended, never elapsed time or the end of a
  turn. A job is reconciled only after its worker has ended and its owned
  work has drained.
- **Never hot-reloads.** A running Controller generation executes from an
  immutable, content-addressed snapshot of its own source and never
  mutates or reloads it; a newer approved generation triggers an
  intentional stop (a durable handoff record, exit 50), never an
  in-process update.
- **Never merges, never rewrites.** No Controller code path merges a pull
  request or pushes to the trunk ref, and the GitHub boundary
  (`controller/forge.py`) has no merge operation. The Controller never
  force-pushes, resets, rebases, amends, deletes a ref or moves a tag:
  every branch push is a fast-forward and every tag push creates a new
  ref. A human merges every pull request.

## Milestone branches and pull requests

This behaviour is **off** unless the target commits
`.workflow-controller/policy.json` with `milestone_branches.enabled`. The
policy is read from the committed tree at `HEAD`, never from the working
tree, and an inadmissible policy (an unknown schema, key or adapter kind,
or an invalid value) refuses every lifecycle command rather than being
ignored. This repository's own policy is the reference configuration:
trunk `main` on `origin`, forge `github`, branches
`milestone/{work_item_id}`, Draft pull requests that become ready only
with green checks.

With the policy active, one milestone runs like this:

```text
main -- /milestone-plan -- bind milestone/<id> -- plan reviews -- plan approval (on the branch)
     -- push, Draft PR -- implementation, reviews, acceptance (each step pushed)
     -- readiness: PR marked ready -- HUMAN merges ("Create a merge commit")
     -- close-out: switch to main, fast-forward -- next /milestone-plan on main
```

- **Trunk start.** Before a bare `/milestone-plan`, `HEAD` must be on
  `main`, the tracked tree clean, and local `main` equal to
  `origin/main`. A `main` behind the remote gates `fast_forward_trunk`
  (the Controller does not fast-forward at start: you may have local
  work), and a diverged one refuses.
- **Bind.** After `/milestone-plan` has written the plan, and before
  anything about it is committed, the Controller creates
  `milestone/<id>` at `main`'s tip and switches to it, carrying the
  uncommitted plan along. Workflow 2.5.1 derives the work-item id itself,
  so the branch cannot be created earlier. A plan approval commit that
  already sits on `main` refuses with manual-recovery guidance.
- **Draft PR.** As soon as the branch has a commit beyond `origin/main`
  (normally the plan approval commit), the Controller pushes it and
  opens one Draft PR titled with the work-item id, whose body carries a
  `<!-- workflow-controller: work_item=<id> -->` marker line. Every later
  step first fast-forwards the remote branch to the local tip. A PR is
  the milestone's only if its number, head, base and repository match and
  it is not from a fork; zero matches create, exactly one open match is
  adopted, anything else refuses.
- **Drift.** Each step records whether `origin/main` is still an
  ancestor of the branch and how far behind the branch is. Before
  acceptance this is informational (`inspect`, `explain` and `status`
  show it).
- **Readiness.** Once the branch's own committed state records the work
  item `MILESTONE_COMPLETE`, the PR is marked ready only when the local
  and remote tips are exactly the acceptance commit (the
  `/accept-milestone` commit, `Workflow-Work-Item` trailer included),
  `origin/main` is an ancestor of it, and at least one check is reported
  for that head with every check passing or skipped. Otherwise the step
  gates:

  | Gate | Meaning | What you do |
  |---|---|---|
  | `checks_pending` | no check reported yet, or one still running; the common case just after the final push | wait, then `run` again |
  | `checks_failing` | a check failed | re-run it on GitHub if it is flaky; a fix committed on the branch follows the acceptance commit, so readiness then gates `post_acceptance_commits` |
  | `checks_cancelled` | a check was cancelled, none pending or failing | re-run it on GitHub |
  | `post_acceptance_commits` | commits follow the acceptance commit | merge anyway on GitHub ("Create a merge commit"), or leave the gate standing; the Controller never removes them |
  | `integration_required` | `main` moved after the branch point | the manual merge procedure below |
  | `pr_head_not_accepted` | GitHub does not show the pushed acceptance commit as the PR head yet | `run` again once it does |
  | `merge_pull_request` | the PR is ready | merge it on GitHub with "Create a merge commit" |

- **Merge and close-out.** You merge; the Controller never does. The
  next step checks that the merged head contains the acceptance commit
  and is itself on `origin/main`, then (with a clean tree, and the
  branch tip equal to the merged head) switches to `main`, fast-forwards
  it and records the binding `CLOSED`. The usual manual path, "merge on
  GitHub, then `git switch main && git pull`", converges the same way
  from `main`. Neither branch is deleted: GitHub's "automatically delete
  head branches" setting is your choice. Close-out gates `dirty_tree` on
  an unclean tree, and `unmerged_commits` on local branch commits that
  were never merged (move them onto a new branch, then
  `git reset --keep <merged-head>` on `milestone/<id>`). A squash or
  rebase merge takes the reviewed commits off `main`: the Controller
  records it (`merge_method_rewrote_history`, shown once), does not
  switch, and blocks nothing afterwards. With `HEAD` still on a finished
  milestone's branch, every step gates `switch_to_trunk`.

**`integration_required` is the normal path, not an error.** Under
Workflow 2.5.1 there is no Workflow transition that re-establishes review
against a moved base, so the Controller never integrates `main` into a
milestone branch. Whenever anything lands on `main` during a milestone --
including this repository's own post-acceptance release commits -- the
milestone ends at `integration_required`, and the supported procedure is:
on GitHub, mark the PR ready and merge it with "Create a merge commit".
Close-out then converges as for any merge. This stays the contract until
the follow-up integration milestone binds integration to the released
Workflow 2.6.x (`docs/ROADMAP.md`).

**Do not press GitHub's "Update branch" button** while a milestone is in
flight. It pushes a merge commit to the milestone branch on the server,
which the next step refuses (the remote branch is no longer an ancestor
of the local tip), and the refusal names the button as the likely cause.
Use the `integration_required` procedure instead.

While a binding is active, workers additionally run with `gh`,
`git push`, `git rebase`, `git switch`, `git checkout -b` and
`git reset --hard` disallowed. That is defence in depth only; the
guarantee is that after every worker job `HEAD` must still be on the
bound branch and the new tip must descend from the old one, or the job
fails (`BranchInvariantViolated`).

### When a milestone gets stuck: the refusal-state exits

A binding can reach one of two refusal states, which block every step --
from the branch and from `main` -- until one exit runs. The exits never
touch a ref or a pull request:

- **`pr_closed_unmerged`**: the Draft PR was closed without being merged.
  The three exits are **exclusive** -- choose one:
  1. **reopen** the PR on GitHub; the next step on the branch notices and
     continues. GitHub cannot reopen a PR whose head branch was deleted
     on GitHub: restore the branch there first, or take one of the other
     exits;
  2. `workflow-controller --work-item <id> milestone-binding --new-pr <repo>`:
     continue on the same branch with a new Draft PR, opened with the
     next branch commit. The old PR is recorded as superseded and is
     never adopted again; do **not** reopen it afterwards (a reopened
     superseded PR is refused by name until you close it again). If the
     branch was already merged into `main` by hand, without a PR, this is
     the exit to take: the next step finds nothing to open and records
     the milestone `CLOSED`;
  3. `workflow-controller --work-item <id> milestone-binding --abandon <repo>`:
     retire the binding. Admitted only while `origin/main`'s committed
     `WORKFLOW_STATE.json` has no unfinished entry for the work item:
     Workflow 2.5.1 cannot retire a work item whose state already reached
     trunk.
- **`merged_before_acceptance`**: the PR was merged before the
  `/accept-milestone` commit. Later branch commits would never reach
  `main`. The exit is `--new-pr`, which continues the work item on the
  same branch (its next PR will meet `integration_required`, because
  `main` holds the merge commit). `--abandon` is named only when its
  precondition holds. If `origin/main` already records the item
  `MILESTONE_COMPLETE` without an acceptance commit (a phase set by
  hand), `--new-pr` would only lead back here, so the gate names
  `--abandon` alone and `milestone-binding --new-pr` refuses.

Two more situations have their own exits:

- **`bound_item_missing`**: the plan was discarded (or stashed) after the
  bind, so the bound work item is gone from the branch's working tree.
  The Controller will not plan a second milestone on the bound branch.
  Restore the plan files (`git restore`, or `git stash pop`), or, when
  nothing was committed or pushed on the branch yet, run `--abandon` and
  then delete the leftover local `milestone/<id>` before planning the
  same id again.
- **A bind interrupted by a crash** leaves a `BRANCH_PLANNED` record. The
  next step completes it when it can; otherwise the refusal names the
  exit for what it observes: switch to a `milestone/<id>` the bind
  created (at the recorded branch point or a descendant); remove or
  rename one it did not create; when none exists, create
  `milestone/<id>` at the current `main` tip (or at the recorded branch
  point if `main` no longer descends from it) and switch to it; or
  `--abandon` if the plan was discarded. Creating the branch after
  `main` was rewound below the work item's `base_commit` opens a Draft
  PR in the plan-stage window that carries the commits the rewind
  removed from `main`.

The Controller never deletes a branch. To plan an abandoned id again,
first delete its old branches yourself: `git branch -d milestone/<id>`
(`-D` only if you mean to discard commits on it) and
`git push origin --delete milestone/<id>`. The bind then renames the old
record to `<work_item_id>.abandoned-<n>.json`, and that binding's PR
numbers stay excluded from every later binding of the id.

## Continuous integration

`.github/workflows/validate.yml` is the single definition of required
validation. It is called, never triggered directly, and has four jobs,
each on `ubuntu-latest` with Python 3.12 and read-only permissions:

- `plan`: plans the whole CI selection with
  `tools/run_tests.py plan --profile ci --ci-placement`: every test
  `python3 -m unittest discover -s tests -t .` loads, plus the seven frozen
  Workflow conformance suites, in duration-balanced shards, from the
  committed `tools/test_timings.json`. Its outputs are the shard indexes,
  their count and the plan's digest, and it uploads `plan.json`;
- `tests`: one matrix job per planned shard (`fail-fast: false`). Each
  recomputes the plan from the checked-out commit, refuses to run unless
  its digest equals `plan`'s, runs its shard and uploads its result
  record;
- `tests-result`: runs even when a shard failed or never started, and
  aggregates every shard's result. It passes only if every planned test
  ran exactly once and passed; a missing plan or a missing shard result
  fails it. It uploads the run's durations as the `timings-ci` artifact.
  `tools/test_shards.py`'s CI placement keeps `tests.test_packaged_runtime`
  (run by `package`) and `tests.test_integration_disposable_repo` (which
  needs the live `claude` binary and real spend) out of the plan;
- `package`: builds the wheel, verifies it with
  `tools/release.py verify-wheel --local`, runs the package-placed
  modules (`tests.test_packaged_runtime`), then installs the wheel with
  pipx and checks `--version`'s first line.

Two workflows call it:

- `ci.yml` runs on every pull request, milestone branches included (they
  are validated through their Draft PR). A newer push to the same ref
  cancels the run in progress. Pushes to other branches without a pull
  request are not validated.
- `main.yml` runs on every push to `main` and on `workflow_dispatch`
  (no inputs: it classifies the current tip of `main`). After `validate`,
  its `release-plan` job classifies the commit, and its `build` and
  `publish` jobs run only when a release is due or resumable (see
  "Releasing"). Its runs never cancel one another (concurrency group
  `main-release`). GitHub keeps one running and one pending run per
  group, so a burst of merges can skip a middle commit's run; that loses
  nothing, because classification compares with the last release tag in
  the commit's history, not with the previous push. `publish` is the
  only job with write permission, and every action outside `validate` is
  pinned to a commit SHA.

`workflow-conformance.yml` is managed by the Workflow Manager and is
untouched. It still runs the seven conformance suites serially, in one
job, on every pull request (about 5 minutes), so it, not `validate`, is
the floor on when all of a pull request's checks finish. `validate` runs
the same suites through the planner as well; the acceptance-matrix suite
(about 3 minutes on CI) is its own largest shard. `release.yml` is gone:
pushing a `v*` tag by hand triggers nothing.

The CI plan uses the `ci` profile: about 180 s of work per shard, at
least 2 and at most 16 shards, from the committed `tools/test_timings.json`
only. A stale or missing entry only costs balance. The plan is
deterministic: each `tests` job recomputes it and refuses to run on a
digest mismatch, so the matrix carries only shard indexes. A failing run's
`tests-result` summary names each failing test, its shard, the shard's
log (in the `results-<i>` artifact) and the reproduction commands.

The three workflow files are generated. Edit the model in
`tools/ci_workflows.py`, then run `python3 tools/ci_workflows.py --write`;
`python3 tools/ci_workflows.py --check` (and a test) fails when a
committed file differs from the model.

The checks are `validate / plan`, `validate / tests (<i>)`,
`validate / tests-result` and `validate / package`. A branch-protection
rule should require `validate / tests-result` (its status stands for the
Controller and conformance tests, whatever the shard count) and
`validate / package`. Branch protection is not required by anything here.

## Releasing

A release is **driven by a version change on `main`**. The repository
policy's `release` section names the version source, the tag format,
the build and verify commands, the artifacts and the publication target;
for this repository that is `pyproject.toml`'s static
`[project].version` (plain `MAJOR.MINOR.PATCH`, no pre-releases), tags
`v{version}`, and a GitHub Release carrying the wheel and `SHA256SUMS`.
The generic transaction is `controller/release_txn.py`;
`tools/release.py` is this repository's thin CLI over it.

For maintainers:

1. Bump the version in `pyproject.toml` in a commit that reaches `main`
   through a pull request (or, as for 1.1.1, a release-preparation
   commit).
2. The push to `main` runs `main.yml`. `release-plan` runs
   `tools/release.py classify --commit <sha>`, which compares the
   version with the release tags in the commit's history and prints one
   state (`--commit` takes a full 40-hex SHA of a commit already on
   `origin/main`):

   | State | Meaning | Outcome |
   |---|---|---|
   | `NO_CHANGE` | the version's tag is at an earlier commit and released | success, nothing published (the ordinary merge) |
   | `RELEASE_DUE` | a new, higher version with no tag yet | build, verify, tag, publish |
   | `RESUME` | the version's tag exists (here or at an earlier commit) with no release, or only a draft | build and verify **the tag's own commit**, then complete publication |
   | `ALREADY_RELEASED` | the tag is at this commit and its published assets are consistent | success, no-op |
   | `ABANDONED_VERSION` | the version's tag is acknowledged in `abandoned_tags` (today only `v1.1.0`) | success, nothing published |
   | `BASELINE_UNRELEASED` | a lower tag in the history has no published release and is not acknowledged | fail, naming every such tag (resolutions below) |
   | `INVALID_TRANSITION` | a new version that is not higher than the highest tag in the history | fail |
   | `COLLISION_TAG_ELSEWHERE`, `COLLISION_RELEASE_WITHOUT_TAG`, `RELEASE_MISMATCH`, `ABANDONED_TAG_INCONSISTENT` | the tags, releases and policy contradict one another | fail, for a human |

3. For `RELEASE_DUE`/`RESUME`, `build` checks out exactly the target
   commit, runs the policy's build and verify commands (for this
   repository `verify-wheel --tag --commit`), smoke-tests the wheel with
   pipx and writes `SHA256SUMS`. `publish` reclassifies with fresh reads,
   refuses a target that differs from what was built, re-verifies the
   artifacts, and only then (for `RELEASE_DUE`) creates the annotated tag
   at that commit and pushes it, then publishes the release and verifies
   the published assets.

The tag is created only after validation and artifact verification, and
it is never moved or deleted. A run interrupted at any point -- before
the tag push, or after it with no release or a half-uploaded draft -- is
completed by the next push to `main` that still carries the version, or
by running `main.yml` from the Actions tab (`workflow_dispatch`): both
classify `RESUME` at the tag's own commit. A draft is completed from
its present assets, which must verify; a draft that does not is left
for a human, never deleted. A failed or bad *published* release is fixed
with a new PATCH version.

`build_origin: "release"` in a wheel's `BUILD_INFO.json` is not proof of
origin (see "Runtime identity"): check the wheel against the release's
`SHA256SUMS`.

### An unreleased tag below a new version: `BASELINE_UNRELEASED`

A tag without a published release is never skipped over. Suppose
`v1.2.0` was pushed but its publication failed, and `main` then moved to
1.2.1 (by a bump, or by a hand-pushed `v1.2.1`). Every later run fails
`BASELINE_UNRELEASED`, naming `v1.2.0`, until one of two commits on
`main` resolves it:

- **resume** it: set the version back to `1.2.0`. That commit classifies
  `RESUME` at `v1.2.0`'s own commit and publishes it; the bump to 1.2.1
  is then `RELEASE_DUE` again;
- **acknowledge** it: add `"v1.2.0"` to the policy's
  `release.abandoned_tags`. It then counts as settled, and the run
  proceeds with 1.2.1.

**Acknowledging a tag settles only that tag.** Every lower unreleased
tag in the history is checked, not just the highest: if `v1.1.5` is
also unreleased, acknowledging `v1.2.0` still leaves the next run
`BASELINE_UNRELEASED`, naming `v1.1.5`, until it too is resumed or
acknowledged. A release published by hand, outside the transaction, is
outside this guarantee.

### Runbook: the first automatic release

1.2.0 is the first release published by `main.yml`. The installed
Controller stays 1.1.1 until then; before it, install a locally built
wheel only for disposable trials.

1. **One-time repository settings** (Settings, General; free, not branch
   protection):
   - turn on **immutable releases**. It makes a published release's
     assets and tag unchangeable, even by an admin, and a workflow cannot
     turn it on for itself;
   - under "Pull Requests", turn **off** "Allow squash merging" and
     "Allow rebase merging", leaving "Allow merge commits" on. Squash and
     rebase merges take the reviewed Workflow commits off `main`; close-out
     detects them (`merge_method_rewrote_history`), but cannot undo them.
2. **Check the current state.** Once `origin/main` carries the policy
   (`classify` reads `.workflow-controller/policy.json` committed at the
   classified commit, and refuses a commit without one), on an
   up-to-date `main`, before any bump:

   ```bash
   git fetch --tags origin
   python3 tools/release.py classify --commit "$(git rev-parse origin/main)"
   ```

   This needs an authenticated `gh`. It must print `NO_CHANGE` (`v1.1.1`
   released at an earlier commit; `v1.1.0` is acknowledged in
   `abandoned_tags`). Anything else is resolved first.
3. **Prepare the release commit** on `main`: set `version = "1.2.0"` in
   `pyproject.toml`, and nothing else. `classify` only accepts a commit
   already on `origin/main`, so step 2 is the pre-push check; the push
   itself is the first `RELEASE_DUE` run.
4. **Push**, then watch `main.yml`: `validate`, `release-plan`
   (`RELEASE_DUE`), `build`, `publish`. Check that the release `v1.2.0`
   exists with the wheel and `SHA256SUMS`, and that `v1.2.0` peels to the
   release commit.
5. **If a job fails**, fix the cause and re-run `main.yml` from the
   Actions tab, or let the next push to `main` do it; never re-push or
   move the tag.
6. **Install it**: `pipx install` the released wheel (or
   `pipx upgrade`), check `workflow-controller --version`, and verify the
   wheel against `SHA256SUMS`.

A release commit that lands on `main` while a milestone is in flight
moves `main` under that milestone: it ends at `integration_required`
(see "Milestone branches and pull requests"), which is expected.

## Development

```bash
pip install -e .
python3 tools/run_tests.py                          # everything, in parallel shards (about 1.5 min)
python3 -m unittest discover -s tests -t .          # the Controller's own suite, serially (about 8 min)
CONTROLLER_REQUIRE_PACKAGING_TESTS=1 python3 -m unittest tests.test_packaged_runtime -v  # wheel build + venv install
CONTROLLER_LIVE_WORKER=1 python3 -m unittest tests.test_integration_disposable_repo -v  # opt-in: live claude, real spend
python -m pip wheel --no-deps -w dist .             # a local wheel (build_origin "local")
```

The packaging tests skip when a build prerequisite is missing, unless
`CONTROLLER_REQUIRE_PACKAGING_TESTS=1` makes that a failure. A local wheel
build refuses a stale `build/` directory left by an earlier build that
held files this one does not; delete `build/` and rebuild.

Do not set `PYTHONPATH=.`: `tests.test_identity`'s decoy-package test
then imports its decoy and fails. Some tests need package-index access,
because `fixtures.editable_install` runs a build-isolated `pip install -e`.

### The test runner

`tools/run_tests.py` (with its library `tools/test_shards.py`, both
stdlib only and outside the wheel) runs a selection of tests in
duration-balanced parallel shards. `python3 -m unittest ...` and
`cd scripts && python3 <suite>` keep working unchanged; the runner is
additive. The design record is
[ADR 0005](docs/adr/0005-adaptive-test-sharding.md).

```bash
python3 tools/run_tests.py                                  # the full selection
python3 tools/run_tests.py tests.test_worker tests.test_cli.RunRecordCtrlCTest conformance:workflow_state_test.py
python3 tools/run_tests.py --serial [names]                 # the same selection in one process, in order
python3 tools/run_tests.py --plan-only [names]              # print the plan and exit
python3 tools/run_tests.py --replay RESULTS/plan.json [--shard i]   # re-run a recorded plan, or one shard of it
```

- **Selection.** With no names the selection is every test
  `python3 -m unittest discover -s tests -t .` loads plus the seven frozen
  Workflow conformance suites. A name is a unittest dotted name (`tests`,
  a module, a class or a test), `conformance` (every suite) or
  `conformance:<file>`. A name that matches nothing is refused.
- **Shards.** A class is the smallest unit of placement (a whole module
  when it defines `setUpModule`/`tearDownModule`, a whole file for a
  conformance suite), so class fixtures run once, as serially. Each
  shard is a separate process in its own session, with its own `TMPDIR`
  and `XDG_STATE_HOME`. The shard count is planned from recorded
  durations: about 60 s of work per shard, at least 2, at most
  `min(8, CPUs)`. `--shards N` pins it, `--jobs J` runs at most `J` at
  once, and `--target-seconds`, `--min-shards` and `--max-shards`
  override the planning parameters.
- **Proof.** A run passes only if every planned test ran exactly once and
  passed; a missing, extra, duplicated or substituted test id fails the
  run and is named. A failed test is never retried. The exit status is
  0 (pass), 1 (a test or fixture failed), 2 (refused, crashed, not run,
  or a coverage violation) or 130 (interrupted: Ctrl-C stops every
  shard's process group).
- **Results.** Each run writes `plan.json`, one `shard-<i>.json` and
  `shard-<i>.log` per shard, and `SUMMARY.md` under
  `$TMPDIR/workflow-controller-tests/<run_id>/` (or `--results-dir`). The
  summary names every failing test with its traceback, its shard's log,
  and two reproduction commands: `python3 -m unittest <id>` alone, and
  `--replay ... --shard <i>` for the exact co-resident order. A process
  that outlives its shard is reported, then killed, as a warning.
- **Timings.** Durations only decide where a test runs, never whether it
  runs. Local runs plan from the untracked
  `$XDG_CACHE_HOME/workflow-controller-tests/timings-local.json`
  (updated after every run), falling back to the committed
  `tools/test_timings.json`, which is all CI plans from. A missing or
  corrupt file falls back to defaults with a warning. The committed
  profile changes only through an explicit, reviewed refresh, for
  example from a CI run's `timings-ci` artifact:
  `python3 tools/run_tests.py timings merge --into tools/test_timings.json DIR...`.
  A test pins each committed entry's test count, so a change to a test
  class's size needs that refresh for the class.
- **Serialization.** A test runs alone only if `EXCLUSIVE_ATOMS` in
  `tools/test_shards.py` lists its class, with a reason. It is empty;
  an entry needs evidence that the race cannot be fixed in the test.

See `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md` for the full design record,
`docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md`
for the implementation-stage automation, routing and concurrency design,
`docs/ai-workflow/CONTROLLER_RELEASE_RUNTIME_OBSERVABILITY_PLAN.md` for
the release, runtime-identity and observation design,
`docs/ai-workflow/CONTROLLER_TRUNK_BRANCH_PR_RELEASE_PLAN.md` for the
milestone-branch, pull-request and release-transaction design,
`docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md` for the
worker lifecycle ownership design,
`docs/adr/0001-controller-generation-1-architecture.md` for the
decisions most likely to matter to a later generation,
`docs/adr/0002-release-runtime-identity-and-observability.md` for
1.1's, `docs/adr/0003-trunk-branch-pr-release-orchestration.md` for
the trunk, pull-request and release decisions, and
`docs/adr/0004-worker-lifecycle-ownership.md` for the worker ownership
model and the harness limitations it documents, and
`docs/adr/0005-adaptive-test-sharding.md` for the test inventory,
planner and runner.
