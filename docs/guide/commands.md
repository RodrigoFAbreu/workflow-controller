# Commands, options and worker routing

> For: anyone using the command line, and anyone choosing worker models. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0.

[Back to the documentation map](../README.md)

## CLI surface

| Command | Behaviour |
|---|---|
| `workflow-controller inspect <repo>` | managed-repo verification + Workflow state summary, and the lifecycle lock's state; read-only. For a protocol target (Workflow 2.7.0 and later) it also shows `workflow_mode`, the protocol version, the managed-script digest map and, as an advisory, any action id `describe` lists that this release cannot launch (`unknown_action_ids`): an invented id, or one of the four catalogue ids it has no command for |
| `workflow-controller explain [--review-mode MODE] <repo>` | the pending job files (each with the command that clears it), the lifecycle lock's state, then the next-action decision with full evidence, and, at a gate, exactly what a human must do; for a protocol-mode target also the Workflow's row, disposition and action (a `protocol` block with `--json`); at an external review gate it also prints the review mode, its source and what the next step does (a `review_mode` block with `--json`); read-only; exits 0, but refuses (exit `20`) like every other command when the repository is not admitted |
| `workflow-controller step [--follow] [--review-round-cap N] [--review-local-round-cap N] [--review-external-round-cap N] [--review-mode MODE] <repo>` | execute exactly one automatic action, validate the transition, stop. With `reviewer.harness` set, the two external review gates (rows 14 and 28) run a Codex review job instead of stopping (see [the reviewer settings](runtime.md#what-it-holds)). `--review-mode auto\|cross-model\|single-provider\|manual` chooses the external review for that command (see [the mode table](runtime.md#what-it-holds)) |
| `workflow-controller run [--follow] [--max-steps N] [--review-round-cap N] [--review-local-round-cap N] [--review-external-round-cap N] [--review-mode MODE] <repo>` | (with `reviewer.harness` set, the external review gates are `step`s too) repeat `step` until a gate (every implementation-stage human gate included), a declined action, a no-action phase, a failure, an incomplete step, a refusal, or a pending handoff |
| `workflow-controller resume [--drain-timeout SECONDS] <repo>` | re-attach to a job whose worker still runs with no Controller supervising it and supervise it to its end, then reconcile this target's non-terminal job records and report; never launches a worker. `--drain-timeout` bounds this re-attach's drain (see [the drain bound](workers.md#owned-processes-the-daemon-list-and-the-drain-bound)) |
| `workflow-controller resume --abandon JOB_ID [--acknowledge-unverifiable-worker] <repo>` | mark one pending job file terminal instead of reconciling it (see [Job dispositions](workers.md#job-dispositions)) |
| `workflow-controller status` | Controller-owned view: the running Controller, pinned identity, job records, pending handoff, active runs and jobs, the last job's telemetry, milestone bindings; writes only `identity.json` (see [`status`](#status)) |
| `workflow-controller follow [--job JOB_ID \| --run RUN_ID] [--from-start] [<repo>]` | render a run's or job's events and worker output (see [Observing workers](workers.md#observing-workers)); writes nothing |
| `workflow-controller --work-item <id> milestone-binding --new-pr <repo>` | continue a milestone whose binding is in a refusal state on the same branch, with a new Draft PR (see [Milestone branches and pull requests](milestone-branches.md)); launches no worker and touches no ref or pull request |
| `workflow-controller --work-item <id> milestone-binding --abandon <repo>` | retire such a binding, under the preconditions stated there; exactly one of `--new-pr`/`--abandon` is required |
| `workflow-controller settings show\|path\|clean` | the user settings file (see [`settings`](#settings)); `show` and `path` are read-only |
| `workflow-controller telemetry [--run RUN_ID] [--since ISO] [--by GROUPING] [<repo>]` | the recorded jobs' turns, tokens, cost and time (see [`telemetry`](#telemetry)); read-only |
| `workflow-controller verify-claude [--force]` | run the live sandbox probes against the `claude` a launch would run and record its digest when every probe holds (see [`verify-claude`](#verify-claude)); takes no repository |
| `workflow-controller notifications [--test]` | the effective SignalHub notification table and where the URL and key were found (never the key); `--test` sends one `info` test event through the real transport (see [`notifications`](#notifications)); takes no repository and writes nothing |
| `workflow-controller usage [--provider claude\|codex\|all] [--check \| --wait] [--reserve] [--release TOKEN] ...` | the usage budget's readings, and the gate a manually started worker passes (see [`usage`](#usage)); writes only the shared usage record, its event file and the accounting mark of a job record a crash left unaccounted |
| `workflow-controller --version` | the version, then the running runtime (see [Runtime identity](runtime.md#runtime-identity)) |

Global options -- declared on the top-level parser, so they are accepted
only *before* the subcommand: `--runtime-dir`, `--work-item`,
`--workflow-manager`, `--claude-binary`, `--permission-mode` (default
`auto` for lifecycle workers; an explicit value such as `acceptEdits` is
passed through unchanged), `--timeout` (seconds; the default is the `worker.timeout_seconds`
setting, which is no limit unless set, see
[Concurrency and worker lifecycle](workers.md#concurrency-and-worker-lifecycle)), `--model`, `--effort`,
`--role-model ROLE=MODEL`, `--role-effort ROLE=EFFORT`,
`--role-harness ROLE=HARNESS`, `--routing-config
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

`step` and `run` also take `--review-local-round-cap N` and
`--review-external-round-cap N` (1 to 100 each; else the
`review.local_round_cap` and `review.external_round_cap` settings, else 6 and 4):
the protocol review loop of a stage stops when a round count reaches its cap
with the `review_round_cap_reached` gate (see
[a run stopped at the review round cap](../common-problems.md#a-run-stopped-at-the-review-round-cap)).
`--review-round-cap N` still sets both caps below an option of its own; it and
the `review.round_cap` setting are deprecated and retired after 1.17.0.
A bad value is a usage error (exit `2`).

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

`workflow-controller telemetry [--run RUN_ID] [--since ISO] [--by GROUPING] [<repo>]`
sums up what the recorded workers cost: turns, tokens, cost, API time
and wall time, as totals and per-job means. It reads the job records
under the runtime root, the way `follow` finds it, and writes nothing.
What each figure means is in [Telemetry](workers.md#telemetry).

- With no filter it counts every job that launched a worker. `<repo>`
  keeps one target's jobs, the global `--work-item` one work item's,
  `--run` one run's, and `--since` the jobs created at or after a UTC
  date (`2026-10-01`) or time (`2026-10-01T12:00:00Z`). Filters combine.
  A bad `--since` is a usage error (exit `2`).
- `--by` groups the jobs: `role`, `model`, `role,model`, `harness`,
  `role,harness`, `role,harness,model`, `action` (the
  protocol action id), `stage` (`plan`, `implementation`, `functional` or
  `none`), `stage,round` (a round counted from job records is
  marked `~` and kept apart from the Workflow's own), `work_item,action` or
  `review_mode` (the review's mode and whether it was lowered; a review record
  without them reads `cross-model`, a job that is not a review `none`);
  without it there is one group. The harness groupings compare the harnesses
  a job ran on: a worker's is its route's harness, a review's the reviewer
  that ran it. Each job row also carries `harness`, `action`,
  `stage`, `round` and `round_source`, derived from the job records and
  never written back. A Claude review job also carries the `telemetry` block of its
  cost and tokens; a Codex review has none, so a `codex` group shows its job
  count with every job `telemetry unavailable` and no figures. Each group shows its job count and how many
  have no figures (`telemetry unavailable`). Totals and means count only the jobs that
  carry each figure.
- With the global `--json` it prints `by`, `rows` (one per job, with its
  telemetry block) and `groups`.

A job recorded before telemetry existed has no block. Its figures are
derived from its `worker.stdout` each time, marked `"derived": true`,
and nothing is written back. A job whose stream is gone has no figures.

## `verify-claude`

`workflow-controller verify-claude` makes a newer `claude` build usable by the automated functional review without
waiting for a Controller release. It takes no repository. It resolves `claude` on `PATH` (the build a launch would
run; `--claude-binary` names a different build to verify and needs no `claude` on `PATH`: the command then notes
that a launch still runs the one on `PATH`, or, when there is none, that functional review cannot start until a
`claude` is put there), prints its resolved path and SHA-256 digest, and then:

- a build already admitted, built in or recorded, is reported and **not probed**; `--force` probes it again;
- otherwise it runs the live enforcement probes (writes and sockets the sandbox must refuse, the permitted
  writes, the tool list, the missing-component refusal and the retry) in a scratch home. **Cost:** up to two short
  model turns at low effort with your Claude credentials; the usage budget gate is not consulted;
- only when every probe holds and the build's bytes are unchanged by the run, it records the digest, version and
  probe results in `verified-claude-builds.json` in the runtime root (see [runtime](runtime.md)) and exits `0`;
- a failing probe exits `20`, names the probe and records nothing; so does a setup failure while the probe run is
  built (a full disk, an unwritable folder, a listener that cannot bind), which is reported as the `host` probe; the
  scratch folder is removed either way. The
  probes run with a private `TMPDIR` (a `wcf-*` folder, mode `0700`) under an eligible `$XDG_RUNTIME_DIR`, otherwise
  `/tmp`, because Claude's sockets need a short path; it is removed with the run.

The settings file is never created, filled or updated by this command. The record is per runtime root: a source
checkout and the shared install each need their own run. The record defends against the sandboxed tester and
against redirection, not against a lifecycle worker running as you, and a build file rewritten in place between the
hash and the exec is not caught (see [runtime](runtime.md#controller-owned-runtime-state)).

## `notifications`

`workflow-controller notifications` prints the effective notification
configuration: `notifications.enabled`, the SignalHub URL and API key
discovered (naming the source of each -- the environment variable, the
`notifications.api_key_file` path or the file under the SignalHub config
directory -- and never the key), whether the lanes' kill switch file
`<signalhub config dir>/disabled` exists, then one row per event type with its
kind, whether it is on, its category and severity, and which layer set each
(`default`, `kind` or `event`). Without a URL or key it lists the places it
looked. `--json` prints the same as one object.

`notifications --test` sends one `info` test event (`milestone_started`, so it
follows your own setting for that type) through the real transport and exits
`0` on `sent` or `duplicate`, `1` on `rejected` or `conflict` (a `422`), `3`
on `failed`, and `2` when nothing was sent: not configured, disabled (the
kill switch or `notifications.enabled: false`), or the test event's type
disabled by `notifications.events`. It takes no repository, opens no run
record and writes nothing under the runtime directory.

The event types, with the kind each belongs to (which sets its default
category and severity, see
[the notifications section](runtime.md#the-notifications-section)):

| kind | event types |
|---|---|
| `state` | `step_done`, `run_finished`, `review_wait`, `checks_wait`, `usage_paused`, `usage_resumed`, and, off by default, `usage_released` and `usage_reservation_expired` |
| `info` | `milestone_started`, `milestone_planned`, `approval_recorded`, `milestone_accepted`, `pr_merged`, `release_published` |
| `gate` | `user_gate` |
| `blocker` | `lane_blocked` |

[ADR 0012](../adr/0012-signalhub-notifications.md) says what each one is
derived from, except `review_wait` and `checks_wait`, added later:
`review_wait` is a stop that waits for an external reviewer or for the
functional tester's fault to be looked at, `checks_wait` is a spent wait, or a
stop in a repository without auto-merge, for a pull request's checks,
and [automation](automation.md#notifications-from-an-unattended-run) says which stops send it.

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
  It works on a lapsed token, and repeating a measured or `ok` release is
  harmless (`already accounted; nothing changed`, exit `0`). A
  `not_started` release forgets the token, so repeating it exits `1`
  like any token the record does not know. `--renew` of a token already
  accounted also exits `1` and changes nothing.

A lane script therefore reads:

```bash
token=$(workflow-controller usage --wait --reserve --role review --model opus --repo "$PWD" --run-id "$RUN")
# run the worker, saving its stream to $STREAM
workflow-controller usage --release "$token" --stream "$STREAM"
```

`--stream` reads a Claude stream, so it is refused (exit `2`) for a Codex reservation.

A reservation never released lapses after `usage.reservation_seconds`
without a `--renew`; a lapsed one stops counting against the account
windows but still counts against the run and repository caps until it is
released or, after seven days, charged at its forecast. Every pause,
resume and release is appended to `usage-events.jsonl` in the runtime
root, so a wait leaves a record without a run. `usage` writes only the
shared record and that file, apart from the replay sweep it runs first: a
finished job whose charge a crash left owing is charged then, and its job
record is marked accounted.

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
| `satisfy-gate` | `/satisfy-gate plan\|implementation` (protocol mode only: the Workflow's `plan.satisfy` and `implementation.satisfy` actions, a mechanical check and one transaction) | `claude-haiku-5-5` | `high` | yes |
| `satisfy-gate-acceptance` | `/satisfy-gate acceptance` (protocol mode only: the `acceptance.satisfy` action, which also writes the milestone narrative, roadmap and findings table) | `claude-sonnet-5-5` | `medium` | yes |
| `apply-pr-review` | `/apply-pr-review` (protocol mode only: the Workflow's `pr.apply_review` action, row 38d) | `claude-opus-5-5` | `xhigh` | no |
| `milestone-plan`, `record-manual-plan-review`, `record-manual-implementation-review` | those commands | inherit | inherit | no |
| `functional-tester` | the Controller-run functional tester at row 38e (protocol mode only, 1.18.0 and later; not a Workflow command: a sandboxed `claude -p` job that runs the checklist's flows with `Bash` only) | `claude-sonnet-5-5` | `medium` | yes |
| `apply-functional-review` | `/apply-functional-review` (protocol mode only, 1.7.0 and later) | `claude-opus-5-5` | `xhigh` | no |
| `prepare-functional-review` | `/prepare-functional-review` (protocol mode only, 1.7.0 and later) | inherit | inherit | no |

In protocol mode the role comes from the Workflow's action id rather than
from the command: `implementation.self_review` is
`milestone-implement-self-review`, `acceptance.satisfy` is
`satisfy-gate-acceptance`, and each other action takes its
command's role. Six roles exist only there, since legacy mode never
launches their commands: the two functional-review roles, `functional-tester`, `satisfy-gate`,
`satisfy-gate-acceptance` and `apply-pr-review`
([Protocol mode](automation.md#protocol-mode-workflow-27-and-later)). All
six are accepted by `--role-model`/`--role-effort`, a `--routing-config` file
and the settings file's `routing` section, and `telemetry --by role`
groups their jobs under these names. A Controller before 1.7.0 rejects
the functional-review roles in a `--routing-config` file and ignores them, with a warning, in the
settings file; a Controller older than the release that adds `satisfy-gate`, `apply-pr-review` or
`satisfy-gate-acceptance` does the same with that role.
A `satisfy-gate` value you pinned earlier now applies to plan and implementation approval only;
acceptance takes its own built-in route until you name `satisfy-gate-acceptance`.

"Inherit" means no flag is passed, so the `claude` CLI's own
configuration applies. Model, effort and harness are resolved separately, and
the first of these that sets a value wins:

1. `--role-model ROLE=MODEL` / `--role-effort ROLE=EFFORT` (each
   repeatable, once per role);
2. `--model` / `--effort`, for every role;
3. the routing config's entry for the role: the `--routing-config`
   file when given, else the settings file's `routing` section (see
   [The settings file](runtime.md#the-routing-section));
4. that config's `default` entry;
5. the built-in value in the table above;
6. inherit.

The harness has its own list, shorter by one and ending in a built-in value:

1. `--role-harness ROLE=HARNESS` (repeatable, once per role);
2. the routing config's entry for the role;
3. that config's `default` entry;
4. the built-in `claude-code`.

There is no `--harness` for every role: a single harness cannot be valid for
every role unless it is `claude-code`, so such an option would be redundant or
always refused. A harness is accepted for a role only when its adapter declares
the adapter role the routing role runs as; today that is `claude-code` for every
role and `codex` for none (see
[The routing section](runtime.md#the-routing-section)). An unregistered harness
or a refused pair is a usage error (exit 2) on the command line.

For example, `workflow-controller --role-effort review-implementation=max run <repo>`
raises only the implementation reviewer's effort, and
`workflow-controller --routing-config routing.json run <repo>` reads a
file shaped like this (both `default` and `roles` are optional; each
entry sets `model`, `effort`, `harness` or any of them):

```json
{
  "schema_version": 1,
  "default": {"effort": "high"},
  "roles": {"review-implementation": {"model": "claude-opus-5-5", "effort": "max"}}
}
```

A role entry may also list further routes under `fallback`, the role's
**fallback chain**: a non-empty list of objects, each naming `harness` and
`model` and optionally `effort` (omitted means the harness's own default).
The role entry's own `harness`, `model` and `effort` stay the first route.

```json
{"roles": {"review-plan": {"model": "claude-opus-5-5",
  "fallback": [{"harness": "claude-code", "model": "claude-sonnet-5-5"}]}}}
```

Nothing is filled into a fallback entry from `default` or the built-in table,
an unknown key inside one is refused (in the settings file too), `default`
takes no `fallback`, and the harness, model and effort of every entry are
checked at load exactly as the first route's are; a refusal names the role and
the entry's position, counted from 1 after the first route. The command-line
options (`--role-model`, `--role-effort`, `--role-harness`, `--model` and
`--effort`) change the first route only and leave every later entry as the
configuration wrote it. The chain is validated and counted in the worker
families that the review modes compare, and `step` and `run` **use it to choose
the route of a worker**.

The entries are tried in order, before the worker is spawned and never after.
An entry is skipped when its harness cannot launch the role (the launcher
cannot be resolved), or when the usage gate holds its provider; a hold skips
every later entry on the same provider as well, since the readings are
per account and a model-specific limit is not detected. The first entry that
is neither is launched, and the job record names it (see
[the job record](workers.md#telemetry)). Nothing is retried once a
worker has started: a worker that fails, or stops on a usage limit, ends as it
did before, and the next `step` or `run` selects from the first entry again.
When no entry can be launched the outcome is the single-route outcome: if any
entry was held, the hold whose reset is earliest is returned and the wait is the
existing one; if every launcher is unavailable, the first entry's error is
raised. A role with no `fallback` is unchanged.

The settings file's `routing` section has the same shape, without
`schema_version`; `--routing-config` replaces it whole. Only `step` and
`run` use these options to launch workers. A model or effort value must be non-empty, must not begin with `-`, and must be
one the resolved harness declares (a model also passes when the `harness.models`
setting adds it; see [the routing section](runtime.md#the-routing-section)); a
pair the harness does not declare is refused with exit 20 before any job record is
written. An unknown role, a missing `=`, a role assigned twice, or an
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
