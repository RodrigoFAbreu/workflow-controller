# Commands, options and worker routing

> For: anyone using the command line, and anyone choosing worker models. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0.

[Back to the documentation map](../README.md)

## CLI surface

| Command | Behaviour |
|---|---|
| `workflow-controller inspect <repo>` | managed-repo verification + Workflow state summary, and the lifecycle lock's state; read-only. For a protocol target (Workflow 2.7.0 and later) it also shows `workflow_mode`, the protocol version, the managed-script digest map and, as an advisory, any action id `describe` lists that this release cannot launch (`unknown_action_ids`): an invented id, or one of the four catalogue ids it has no command for |
| `workflow-controller explain <repo>` | the pending job files (each with the command that clears it), the lifecycle lock's state, then the next-action decision with full evidence, and, at a gate, exactly what a human must do; for a protocol-mode target also the Workflow's row, disposition and action (a `protocol` block with `--json`); read-only; exits 0, but refuses (exit `20`) like every other command when the repository is not admitted |
| `workflow-controller step [--follow] <repo>` | execute exactly one automatic action, validate the transition, stop |
| `workflow-controller run [--follow] [--max-steps N] <repo>` | repeat `step` until a gate (every implementation-stage human gate included), a declined action, a no-action phase, a failure, an incomplete step, a refusal, or a pending handoff |
| `workflow-controller resume [--drain-timeout SECONDS] <repo>` | re-attach to a job whose worker still runs with no Controller supervising it and supervise it to its end, then reconcile this target's non-terminal job records and report; never launches a worker. `--drain-timeout` bounds this re-attach's drain (see [the drain bound](workers.md#owned-processes-the-daemon-list-and-the-drain-bound)) |
| `workflow-controller resume --abandon JOB_ID [--acknowledge-unverifiable-worker] <repo>` | mark one pending job file terminal instead of reconciling it (see [Job dispositions](workers.md#job-dispositions)) |
| `workflow-controller status` | Controller-owned view: the running Controller, pinned identity, job records, pending handoff, active runs and jobs, the last job's telemetry, milestone bindings; writes only `identity.json` (see [`status`](#status)) |
| `workflow-controller follow [--job JOB_ID \| --run RUN_ID] [--from-start] [<repo>]` | render a run's or job's events and worker output (see [Observing workers](workers.md#observing-workers)); writes nothing |
| `workflow-controller --work-item <id> milestone-binding --new-pr <repo>` | continue a milestone whose binding is in a refusal state on the same branch, with a new Draft PR (see [Milestone branches and pull requests](milestone-branches.md)); launches no worker and touches no ref or pull request |
| `workflow-controller --work-item <id> milestone-binding --abandon <repo>` | retire such a binding, under the preconditions stated there; exactly one of `--new-pr`/`--abandon` is required |
| `workflow-controller settings show\|path\|clean` | the user settings file (see [`settings`](#settings)); `show` and `path` are read-only |
| `workflow-controller telemetry [--run RUN_ID] [--since ISO] [--by role\|model\|role,model] [<repo>]` | the recorded jobs' turns, tokens, cost and time (see [`telemetry`](#telemetry)); read-only |
| `workflow-controller usage [--provider claude\|codex\|all] [--check \| --wait] [--reserve] [--release TOKEN] ...` | the usage budget's readings, and the gate a manually started worker passes (see [`usage`](#usage)); writes only the shared usage record |
| `workflow-controller --version` | the version, then the running runtime (see [Runtime identity](runtime.md#runtime-identity)) |

Global options -- declared on the top-level parser, so they are accepted
only *before* the subcommand: `--runtime-dir`, `--work-item`,
`--workflow-manager`, `--claude-binary`, `--permission-mode` (default
`auto` for lifecycle workers; an explicit value such as `acceptEdits` is
passed through unchanged), `--timeout` (seconds; the default is the `worker.timeout_seconds`
setting, which is no limit unless set, see
[Concurrency and worker lifecycle](workers.md#concurrency-and-worker-lifecycle)), `--model`, `--effort`,
`--role-model ROLE=MODEL`, `--role-effort ROLE=EFFORT`, `--routing-config
PATH` (see [Worker routing](#worker-routing)), `--settings PATH` (the
user settings file, see
[The settings file](runtime.md#the-settings-file)), `--allow-dirty-source`, `--json`.
`WORKFLOW_CONTROLLER_HOME` sets the runtime root when `--runtime-dir` is absent (`--runtime-dir` >
`WORKFLOW_CONTROLLER_HOME` > default). `WORKFLOW_CONTROLLER_SETTINGS`
names the settings file when `--settings` is absent. These two are the
environment variables in the CLI's operator-facing contract.

`--timeout`, `run --max-steps` and `resume --drain-timeout` each take a
positive integer. `0`, a negative number or a non-integer is a usage
error (exit `2`). Without the flag, each takes its value from the
settings file, else its built-in default.

A gate's safe resume command that names `explain` with `--work-item`
after the subcommand is shorthand: run it with the global option first
and the repository last, as in
`workflow-controller --work-item <id> explain <repo>`.

`run` is a bounded loop, not a daemon: it terminates, it holds no socket
and it schedules nothing. Its convergence is bounded by
`--max-steps` (default: the `run.max_steps` setting, 20): a local `REVISE` -> remediation -> review cycle
is two jobs, so the default allows about ten rounds before exit 16.
Its one poll is bounded too: in a repository that opts in to auto-merge,
a `run` step at a pending checks, merge or release gate re-reads GitHub
every `merge.poll_seconds` for up to `merge.wait_seconds`, launching no
worker, and the run log records a `waiting` event per gate (see
[Auto-merge and the release wait](milestone-branches.md#auto-merge-and-the-release-wait)).
`step` never waits. After an auto-merged milestone closes out, the step
ends with exit `0` and `run` stops, instead of planning the next
milestone in the same run.

Exit codes are part of the CLI's contract. The one table of them is
[exit codes](../exit-codes.md), whose normative copy is
[ADR 0001](../adr/0001-controller-generation-1-architecture.md#exit-codes);
read it to write an outer supervisor's `case` statement. `follow` uses only
`0`, `2` and `20`.

## `status`

`workflow-controller status` reads the runtime root. The one file it
writes there is `identity.json`, the record of the running Controller,
which every command except `follow`, `settings` and `telemetry` writes
before it runs; the root is created if it is missing. Its text output,
in order:

- `controller:` the running Controller, as `--version`'s line 2. With no
  runtime state yet, one more line says so, and that is all. That
  `status` still writes `identity.json`, so a later `status` finds
  runtime state and shows that record as `pinned identity:
  source_kind=unpinned ...`;
- `pinned identity:` the identity recorded by the last command that
  wrote `identity.json`;
- `jobs: <n> recorded` (or `jobs: none`), then the 10 newest jobs, newest
  first, one per line: id, status, command and work item, then
  `age 12 min` while the job is active, or `wall 1400 s, cost $11.66`
  once it has ended (the cost only when the job has telemetry figures);
  a protocol-mode job also shows `[reconcile <class>]` once reconciled,
  with the invalid reasons' codes after the class (`reconcile_class` and
  `invalid_reasons` with `--json`);
- `handoff:` a pending handoff, or `none`;
- `active:` each running run, with its start time and its Controller's
  liveness, and each non-terminal job, with its command, work item,
  start time and what its worker is doing; each with the `follow`
  command that watches it. `active: none` when nothing runs;
- `last job telemetry:` the newest job that carries a telemetry block,
  with its cost and wall times (see
  [Telemetry](workers.md#telemetry)), only when one exists;
- one `milestone:` line per milestone binding, only when one exists.
  An auto-merge binding's line ends with `, merge: <state> at <head>
  (attempt <n>)` once the Controller has sent a merge, and `, release:
  <tag> <url>` (or the settled state) once its release wait settled;
- `runtime root:` the root and its ladder row.

The jobs counted and listed are those recorded when `status` started.

With the global `--json`, `status` prints one object with the same
fields: `controller`, `runtime_root`, `ladder_row` and `runtime_state`,
and, when there is runtime state, `pinned_identity`, `jobs` (`count` and
`recent`), `handoff`, `active` (`runs` and `jobs`), `last_job_telemetry`
(or `null`) and `bindings`. Each `bindings` entry carries the binding's
`merge` and `release` fields (`null` when absent).

## `settings`

`workflow-controller settings show|path|clean` works on the user
settings file (see [The settings file](runtime.md#the-settings-file)).
It touches nothing else: no target repository and no runtime root.

- `settings show` prints the file's path, then each setting as
  `<key> = <value> (<source>)`, where the source is `cli`, `file` or
  `default`. Flags given on the same command line count as `cli`, for
  example `workflow-controller --timeout 3600 settings show`. With
  `--json` it prints `path`, `exists`, `sha256`, `values` and `sources`.
  It writes nothing.
- `settings path` prints the path in use (with `--json`, as `{"path":
  ...}`). It writes nothing, and the file need not exist.
- `settings clean` fills the file, then removes the keys this release
  does not know, and prints what it removed. It refuses (exit `20`) a
  file last filled by a newer release.

## `telemetry`

`workflow-controller telemetry [--run RUN_ID] [--since ISO] [--by role|model|role,model] [<repo>]`
sums up what the recorded workers cost: turns, tokens, cost, API time
and wall time, as totals and per-job means. It reads the job records
under the runtime root, the way `follow` finds it, and writes nothing.
What each figure means is in [Telemetry](workers.md#telemetry).

- With no filter it counts every job that launched a worker. `<repo>`
  keeps one target's jobs, the global `--work-item` one work item's,
  `--run` one run's, and `--since` the jobs created at or after a UTC
  date (`2026-10-01`) or time (`2026-10-01T12:00:00Z`). Filters combine.
  A bad `--since` is a usage error (exit `2`).
- `--by` groups the jobs by role, model or both; without it there is one
  group. Each group shows its job count and how many have no figures
  (`telemetry unavailable`). Totals and means count only the jobs that
  carry each figure.
- With the global `--json` it prints `by`, `rows` (one per job, with its
  telemetry block) and `groups`.

A job recorded before telemetry existed has no block. Its figures are
derived from its `worker.stdout` each time, marked `"derived": true`,
and nothing is written back. A job whose stream is gone has no figures.

## `usage`

`workflow-controller usage` shows the usage budget and gates workers the
Controller did not start. The settings are in
[The settings file](runtime.md#what-it-holds); the design is
[ADR 0011](../adr/0011-usage-budget.md). `run` and `step` need none of
this: they pass the same gate by themselves before every job, and stop
with exit `17` when it holds (see [exit codes](../exit-codes.md)).

With no gate option it only shows. For each provider it prints the
five-hour and weekly windows (percent, reset time in local time as well
as epoch seconds, and the reading's age), the live reservations, and the
forecast per `(provider, role, model)` from the ledger. `--provider`
chooses `claude`, `codex` or `all` (the default for showing; `claude` for
a gate). `--json` prints one JSON object. It reads Claude's windows from
the shared record and Codex's from `$CODEX_HOME/sessions` (else
`~/.codex/sessions`).

To gate a worker, name the context it runs in. The account windows and
the live reservations are always evaluated; a cap is evaluated only when
you name what it counts:

- `--role ROLE` and `--model MODEL` select the forecast (else the
  provider's default);
- `--repo PATH` selects the repository cap and its spend, and `--run-id
  ID` the run cap and its spend. Left out, that cap is not evaluated and
  the output says so;
- `--usage-cap PERCENT` and `--usage-codex-cap PERCENT` override the run
  cap for this call.

The modes:

- `--check` evaluates once. It exits `17` on a hold and `0` otherwise,
  and reserves nothing unless `--reserve` is given;
- `--wait` loops: on a timed hold it sleeps to the reset plus
  `usage.resume_grace_seconds`, reads again and re-evaluates, because
  another lane may have taken the headroom meanwhile. It exits `0` only
  when the re-evaluation is a go, and `17` on a cap or when
  `usage.max_wait_seconds` is used up;
- `--reserve`, with either, reserves the forecast in the same critical
  section as the go and prints the token as the only line on stdout (the
  rest goes to stderr);
- `--renew TOKEN` extends a reservation's lease and revives a lapsed one;
- `--release TOKEN [--outcome ok|not_started] [--stream PATH]` accounts
  the worker and releases the reservation. `--stream` measures the
  worker's stream-json output; `not_started` releases without charging.
  It is idempotent and works on a lapsed token. A token the record does
  not know, or `--renew` of one already accounted, exits `1` and changes
  nothing.

A lane script therefore reads:

```bash
token=$(workflow-controller usage --wait --reserve --role review --model opus --repo "$PWD" --run-id "$RUN")
# run the worker, saving its stream to $STREAM
workflow-controller usage --release "$token" --stream "$STREAM"
```

A reservation never released lapses after `usage.reservation_seconds`
without a `--renew`; a lapsed one stops counting against the account
windows but still counts against the run and repository caps until it is
released or, after seven days, charged at its forecast. Every pause,
resume and release is appended to `usage-events.jsonl` in the runtime
root, so a wait leaves a record without a run. `usage` writes only the
shared record and that file.

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
| `apply-functional-review` | `/apply-functional-review` (protocol mode only, 1.7.0 and later) | `claude-opus-5-5` | `xhigh` | no |
| `prepare-functional-review` | `/prepare-functional-review` (protocol mode only, 1.7.0 and later) | inherit | inherit | no |

In protocol mode the role comes from the Workflow's action id rather than
from the command: `implementation.self_review` is
`milestone-implement-self-review`, and each other action takes its
command's role. The two functional-review roles exist only there, since
legacy mode never launches either command
([Protocol mode](automation.md#protocol-mode-workflow-27-and-later)). Both
are accepted by `--role-model`/`--role-effort`, a `--routing-config` file
and the settings file's `routing` section, and `telemetry --by role`
groups their jobs under these names. A Controller before 1.7.0 rejects
them in a `--routing-config` file and ignores them, with a warning, in the
settings file.

"Inherit" means no flag is passed, so the `claude` CLI's own
configuration applies. Model and effort are resolved separately, and the
first of these that sets a value wins:

1. `--role-model ROLE=MODEL` / `--role-effort ROLE=EFFORT` (each
   repeatable, once per role);
2. `--model` / `--effort`, for every role;
3. the routing config's entry for the role: the `--routing-config`
   file when given, else the settings file's `routing` section (see
   [The settings file](runtime.md#the-routing-section));
4. that config's `default` entry;
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

The settings file's `routing` section has the same shape, without
`schema_version`; `--routing-config` replaces it whole. Only `step` and
`run` use these options to launch workers. Model and effort values are
passed to `claude` unvalidated -- the `claude` CLI is the authority over
which exist -- except that a value must be non-empty and must not begin
with `-`. An unknown role, a missing `=`, a role assigned twice, or an
unusable value on the command line is a usage error (exit 2). The same
problems in the `--routing-config` file, or any other malformed config,
are `RoutingConfigError` (exit 20). In the settings file's section an
unknown role or field is only a warning, and the other problems are
`SettingsError` (exit 20). Either way no job record is written and no
worker runs.

Single-agent cannot be overridden: the review roles and the final
self-review pass always run with the subagent-spawning tools disallowed
(`--disallowedTools Agent,Workflow,Skill`). `Skill` is on the list
because a `context: fork` skill runs in a subagent; the task's own slash
command still runs, since the CLI expands it without that tool. Every
worker is a fresh session: the Controller never passes `--resume`,
`--continue`, `--fork-session` or `--session-id`. The resolved route,
with the level each value came from, is recorded in the job record's
`worker_route`. Its `config_source` says which routing config was in
force: `settings`, `routing-config` or `none`. The levels `config-role`
and `config-default` mean that config, whichever it was.
