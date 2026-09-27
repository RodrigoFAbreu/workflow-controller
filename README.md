# Workflow Controller

Workflow Controller runs the [Workflow](docs/guide/concepts.md#three-pieces)
development process on a repository for you. It reads the repository's
Workflow state, starts a fresh Claude Code session to run the next Workflow
command, checks the result against what is committed, and keeps going until
it reaches a step only a human may take. Then it stops and tells you exactly
what to do.

```text
Workflow  ->  Workflow Manager  ->  Workflow Controller  ->  your repository
(the rules)   (installs them)       (drives them)
```

It never writes Workflow state itself, never runs a command reserved for a
human, never trusts a worker's own account of what it did, and never merges
a pull request.

**Contents:** [Install](#install) · [First run](#first-run) ·
[Commands](#commands) · [What runs automatically](#what-runs-automatically) ·
[Milestone branches, pull requests and releases](#milestone-branches-pull-requests-and-releases) ·
[When something goes wrong](#when-something-goes-wrong) ·
[Development](#development) · [Documentation](#documentation)

## Install

You need Python 3.12+, [pipx](https://pipx.pypa.io/), `git`, the `claude` CLI
and `workflow-manager`; `gh` too if the repository uses milestone branches.

Install a release. Check the wheel against the release's `SHA256SUMS` first:

```bash
VERSION=1.2.1   # see https://github.com/RodrigoFAbreu/workflow-controller/releases
BASE=https://github.com/RodrigoFAbreu/workflow-controller/releases/download/v$VERSION
curl -fLO "$BASE/workflow_controller-$VERSION-py3-none-any.whl"
curl -fLO "$BASE/SHA256SUMS"
sha256sum -c SHA256SUMS
pipx install ./workflow_controller-$VERSION-py3-none-any.whl
workflow-controller --version
```

**Upgrading:** check `workflow-controller status` shows `active: none`, then
`pipx install --force` the new wheel. **Rolling back** works the same way with
an older wheel.

**From a checkout**, only when you need unreleased code: build a wheel with
`python3 -m pip wheel --no-deps -w dist .` and `pipx install` it, or use
`pip install -e .` for development.

The full guide, including what an upgrade does to a Controller that is
running and what a rollback across generations needs, is
[Installing, upgrading and rolling back](docs/guide/installation.md).

## First run

From inside a repository that has Workflow installed through Workflow
Manager:

1. See where things stand. This is read-only and always safe:
   `workflow-controller explain .`
2. Run until the next human step: `workflow-controller run --follow .`
   `--follow` shows the worker's activity as it happens.
3. When it stops at a gate (exit `10`), do what it says, for example approve
   a plan or paste an external review verdict, then run it again.

From another terminal, `workflow-controller follow .` attaches to whatever
is running without affecting it, and `workflow-controller status` shows every
active run and job.

If the Controller or your terminal dies while a worker runs, the worker keeps
going. `workflow-controller resume .` re-attaches to it and finishes the job.

## Commands

| Command | What it does |
|---|---|
| `inspect <repo>` | verify the repository and summarise its Workflow state; read-only |
| `explain <repo>` | the next decision, its evidence, and at a gate what a human must do; read-only |
| `step <repo>` | perform exactly one automatic action, verify it, stop |
| `run <repo>` | repeat `step` until a gate, a failure, or `--max-steps` (default 20) |
| `resume <repo>` | re-attach to a worker left running, then reconcile unfinished job records |
| `follow <repo>` | watch a run or job, live or after the fact; changes nothing |
| `status` | the Controller's own view: its version, active runs and jobs |
| `milestone-binding` | the exits for a milestone whose pull request was closed or merged too early |

Global options go **before** the subcommand and the repository goes last, for
example `workflow-controller --work-item <id> explain <repo>` or
`workflow-controller --role-effort review-implementation=max run <repo>`.
Model and effort per worker role, the routing config file and every option
are in [Commands, options and worker routing](docs/guide/commands.md).

## What runs automatically

The Controller launches a Workflow command only when it can verify that
command's outcome from committed state. Today that covers planning, local
plan review and revision, recording an already-pasted external verdict,
implementation checkpoint by checkpoint, and local implementation review and
remediation. Everything else stops for a human: the external reviews
themselves, plan and technical approval, functional review, acceptance, and
merging.

Each worker is a fresh session. The Controller waits until the worker and
everything it started in the background have really finished before it
checks the result, and only one worker at a time may work on a repository.

- [What the Controller automates, and how it stays safe](docs/guide/automation.md):
  the dispatch rule, the phase table, the safety model.
- [Workers: lifecycle, recovery and observation](docs/guide/workers.md):
  sessions, background work, the lifecycle lock, `resume`, job records,
  `follow`.
- [Concepts](docs/guide/concepts.md): the three pieces, a milestone end to
  end, and a glossary.

## Milestone branches, pull requests and releases

A repository that commits `.workflow-controller/policy.json` (this one does)
also gets:

- one short-lived `milestone/<id>` branch and one Draft pull request per
  milestone, marked ready once the milestone is accepted and its checks
  pass. A human merges it, with a merge commit;
- a release published from `main` whenever `pyproject.toml`'s version
  changes: validated, built, tagged and published as a GitHub Release with
  the wheel and `SHA256SUMS`. A merge that does not change the version
  publishes nothing.

Details: [Milestone branches and pull requests](docs/guide/milestone-branches.md)
and [Continuous integration and releases](docs/guide/ci-and-releases.md),
which also describes this repository's protected `main`.

## When something goes wrong

Run `workflow-controller explain .` first: it names the problem and the
command that clears it. The exit codes you will meet most:

| Exit | Meaning |
|---|---|
| `0` | done |
| `10` | stopped at a human gate (normal) |
| `20` | refused, fail-closed: read the message |
| `30` | a worker ran and did not achieve its expected outcome |
| `45` | the repository is held by another Controller or a still-running worker |

[Troubleshooting](docs/guide/troubleshooting.md) covers every exit code and
the usual situations; the normative table is in
[ADR 0001](docs/adr/0001-controller-generation-1-architecture.md#exit-codes).

## Development

```bash
pip install -e .
python3 tools/run_tests.py      # the full suite, in parallel shards (about 1.5 min)
```

Do not set `PYTHONPATH=.`, and run the suite in the foreground. Pull
requests are validated by `.github/workflows/validate.yml`, whose jobs are
`plan`, `tests` (one per shard), `tests-result` and `package`. See
[Development and the test runner](docs/guide/development.md).

## Documentation

[`docs/README.md`](docs/README.md) is the map of all documentation: the
guides above, the design decisions (ADRs), the roadmap, and the Workflow
process documents.
