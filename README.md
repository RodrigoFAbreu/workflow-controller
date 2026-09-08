# Workflow Controller

Controller for orchestrating Workflow-managed repositories.

This repository is developed independently from the Workflow Manager and from consumer repositories such as RepFlow.

## What it does

```
Workflow  ->  Workflow Manager / Bootstrapper  ->  Workflow Controller  ->  managed development repositories
```

The Controller automates operation of the Workflow (frozen v2.3.1) against
a managed development repository. Generation 1's own scope is
deliberately narrow (see the ADR below for why):

- at the **plan-stage** phases (`PLANNING`, `REVISING_PLAN`,
  `AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`/
  `AWAITING_EXTERNAL_PLAN_REVIEW`) it decides, launches a fresh `claude`
  worker to run the real Workflow command, and verifies the resulting
  transition;
- at every other phase it observes, decides and reports -- it never
  launches a worker there.

Two invariants hold throughout: **the Controller never writes Workflow
lifecycle state itself** (every durable transition is made by a worker
running a real Workflow command), and **the Controller never crosses a
human gate** (`/approve-review` and `/accept-milestone` are never
selected or executed).

## Installation

```bash
pip install -e .
```

**Only a Controller installed from its own checkout can run `step`,
`run` or `resume`.** Those three commands materialise an immutable
snapshot of the Controller's own source before executing, and an
unpinned process resolves that source from wherever its own `controller`
package was imported -- for `pip install .` into `site-packages`, that is
not a Git repository, so all three refuse with `SourceSnapshotError`. A
`pip install .`/`pip install -e .` install of this checkout still serves
the three read-only commands (`inspect`, `explain`, `status`) from
anywhere.

## CLI surface

| Command | Behaviour |
|---|---|
| `workflow-controller inspect <repo>` | managed-repo verification + Workflow state summary; read-only |
| `workflow-controller explain <repo>` | the next-action decision with full evidence, and, at a gate, exactly what a human must do |
| `workflow-controller step <repo>` | execute exactly one automatic action, validate the transition, stop |
| `workflow-controller run <repo> [--max-steps N]` | repeat `step` until a gate, a declined phase, a no-action phase, a failure, an incomplete step, or a pending handoff |
| `workflow-controller resume <repo>` | reconcile non-terminal job records, then report |
| `workflow-controller status` | Controller-owned view: pinned identity, job records, pending handoff |

Global options -- declared on the top-level parser, so they are accepted
only *before* the subcommand: `--runtime-dir`, `--work-item`,
`--workflow-manager`, `--claude-binary`, `--permission-mode`, `--timeout`,
`--allow-dirty-source`, `--json`. `WORKFLOW_CONTROLLER_HOME` sets the
runtime root when `--runtime-dir` is absent (`--runtime-dir` >
`WORKFLOW_CONTROLLER_HOME` > default); it is the only environment
variable in the CLI's operator-facing contract.

`run` is a bounded loop, not a daemon: it terminates, it does not poll,
it holds no socket and it schedules nothing.

Exit codes are part of the CLI's contract and are normative in
[`docs/adr/0001-controller-generation-1-architecture.md`](docs/adr/0001-controller-generation-1-architecture.md)
-- read that table to write an outer supervisor's `case` statement.

## Controller-owned runtime state

The Controller keeps its own durable state -- pinned source identity, job
records, pending handoff -- under a runtime root resolved by a small
ladder (`--runtime-dir`, then `WORKFLOW_CONTROLLER_HOME`, then
`<origin checkout>/.controller/`). This tree is gitignored and disposable
by design; it is never part of any managed target repository's own state,
and the Controller never writes into a target repository's
`WORKFLOW_STATE.json` -- only a worker running a real Workflow command
does that.

## Safety model

- **Never writes Workflow state.** `controller/target_state.py` exposes
  no write function at all; every durable Workflow lifecycle change is
  made by a worker running a real Workflow command in the target
  repository.
- **Never crosses a human gate.** The decision engine never selects
  `/approve-review` or `/accept-milestone`, and the worker layer refuses
  to execute either even if handed one directly.
- **Never hot-reloads.** A running Controller generation executes from an
  immutable, content-addressed snapshot of its own source and never
  mutates or reloads it; a newer approved generation triggers an
  intentional stop (a durable handoff record, exit 50), never an
  in-process update.

## Development

```bash
python3 -m unittest discover -s tests -t .          # the Controller's own suite
CONTROLLER_LIVE_WORKER=1 python3 -m unittest tests.test_integration_disposable_repo -v  # opt-in: live claude, real spend
```

See `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md` for the full design record
and `docs/adr/0001-controller-generation-1-architecture.md` for the
decisions most likely to matter to a later generation.
