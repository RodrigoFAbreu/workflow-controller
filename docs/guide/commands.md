# Commands, options and worker routing

[Back to the documentation map](../README.md)

## CLI surface

| Command | Behaviour |
|---|---|
| `workflow-controller inspect <repo>` | managed-repo verification + Workflow state summary, and the lifecycle lock's state; read-only |
| `workflow-controller explain <repo>` | the pending job files (each with the command that clears it), the lifecycle lock's state, then the next-action decision with full evidence, and, at a gate, exactly what a human must do; read-only, always exits 0 |
| `workflow-controller step [--follow] <repo>` | execute exactly one automatic action, validate the transition, stop |
| `workflow-controller run [--follow] [--max-steps N] <repo>` | repeat `step` until a gate (every implementation-stage human gate included), a declined action, a no-action phase, a failure, an incomplete step, a refusal, or a pending handoff |
| `workflow-controller resume <repo>` | re-attach to a job whose worker still runs with no Controller supervising it and supervise it to its end, then reconcile this target's non-terminal job records and report; never launches a worker |
| `workflow-controller resume --abandon JOB_ID [--acknowledge-unverifiable-worker] <repo>` | mark one pending job file terminal instead of reconciling it (see [Job dispositions](workers.md#job-dispositions)) |
| `workflow-controller status` | Controller-owned view: the running Controller, pinned identity, job records, pending handoff, active runs and jobs; read-only |
| `workflow-controller follow [--job JOB_ID \| --run RUN_ID] [--from-start] [<repo>]` | render a run's or job's events and worker output (see [Observing workers](workers.md#observing-workers)); writes nothing |
| `workflow-controller --work-item <id> milestone-binding --new-pr <repo>` | continue a milestone whose binding is in a refusal state on the same branch, with a new Draft PR (see [Milestone branches and pull requests](milestone-branches.md)); launches no worker and touches no ref or pull request |
| `workflow-controller --work-item <id> milestone-binding --abandon <repo>` | retire such a binding, under the preconditions stated there; exactly one of `--new-pr`/`--abandon` is required |
| `workflow-controller --version` | the version, then the running runtime (see [Runtime identity](runtime.md#runtime-identity)) |

Global options -- declared on the top-level parser, so they are accepted
only *before* the subcommand: `--runtime-dir`, `--work-item`,
`--workflow-manager`, `--claude-binary`, `--permission-mode` (default
`auto` for lifecycle workers; an explicit value such as `acceptEdits` is
passed through unchanged), `--timeout` (seconds; the default is no limit,
see [Concurrency and worker lifecycle](workers.md#concurrency-and-worker-lifecycle)), `--model`, `--effort`,
`--role-model ROLE=MODEL`, `--role-effort ROLE=EFFORT`, `--routing-config
PATH` (see [Worker routing](#worker-routing)), `--allow-dirty-source`, `--json`.
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
[`docs/adr/0001-controller-generation-1-architecture.md`](../adr/0001-controller-generation-1-architecture.md)
-- read that table to write an outer supervisor's `case` statement. Exit
45 (the target worktree is held) came with the automatic lifecycle
orchestration work. Version 1.1 adds no exit code: `follow` uses only
`0`, `2` and `20`
([`docs/adr/0002-release-runtime-identity-and-observability.md`](../adr/0002-release-runtime-identity-and-observability.md)).

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
