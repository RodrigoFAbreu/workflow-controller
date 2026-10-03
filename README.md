# Workflow Controller

> For: anyone new to the Controller. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

Workflow Controller runs the Workflow development process on a repository for
you. It reads the repository's Workflow state, starts a fresh Claude Code
session to run the next Workflow command, checks the result against what is
committed, and keeps going until it reaches a step only a person may take. Then
it stops and tells you exactly what to do. It never writes Workflow state
itself, never runs a command reserved for a person, never trusts a worker's own
account of what it did, and merges a pull request only when the repository opts
in, and then only the accepted commit.

**How the pieces fit.** [Workflow](https://github.com/RodrigoFAbreu/workflow#readme)
is the development process and its commands, installed into a repository.
[Workflow Manager](https://github.com/RodrigoFAbreu/workflow-manager#readme)
installs, updates and verifies the Workflow from published, digest-pinned
releases. [Workflow Controller](https://github.com/RodrigoFAbreu/workflow-controller#readme)
runs the Workflow's lifecycle steps automatically and stops at every
approval gate.

```text
Workflow  ->  Workflow Manager  ->  Workflow Controller  ->  your repository
(the rules)   (installs them)       (drives them)
```

The lifecycle itself is drawn in the
[lifecycle diagram](docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg);
[How it works](docs/guide/how-it-works.md) walks a milestone end to end.

**Contents:** [Quick start](#quick-start) · [Documentation](#documentation) ·
[What runs automatically](#what-runs-automatically) ·
[Milestone branches, pull requests and releases](#milestone-branches-pull-requests-and-releases) ·
[When something goes wrong](#when-something-goes-wrong) · [Development](#development)

## Quick start

About five minutes, from nothing to a first look at a repository.

1. Install a release and check it. You need Python 3.12+, [pipx](https://pipx.pypa.io/),
   `git`, the `claude` CLI and `workflow-manager`; `gh` too if the repository uses
   milestone branches. Download the wheel and `SHA256SUMS` from the
   [releases page](https://github.com/RodrigoFAbreu/workflow-controller/releases), then:

   ```bash
   sha256sum -c SHA256SUMS
   pipx install ./workflow_controller-<version>-py3-none-any.whl
   workflow-controller --version
   ```

   The steps in full, with the settings file and uninstall, are in [install](docs/install.md).

2. Look at a repository that has Workflow installed through Workflow Manager. This is
   read-only and always safe:

   ```bash
   workflow-controller explain .
   ```

3. Run until the next person's step. `--follow` shows the worker's activity as it happens:

   ```bash
   workflow-controller run --follow .
   ```

4. When it stops at an [approval gate](docs/glossary.md#approval-gate) (exit `10`), do what it says, for example approve a
   plan or paste an external review verdict, then run it again.

From another terminal, `workflow-controller follow .` attaches to whatever is running
without affecting it, and `workflow-controller status` shows every active run and job.
If the Controller or your terminal dies while a worker runs, the worker keeps going:
`workflow-controller resume .` re-attaches to it and finishes the job.

## Documentation

| I want to ... | Read |
|---|---|
| install the Controller, set up the settings file, uninstall | [Install](docs/install.md) |
| run it on a repository: `step` versus `run`, following, approval gates | [Run](docs/run.md) |
| upgrade or roll back, or move a repository to a newer Workflow | [Update](docs/update.md) |
| know which Workflow releases a Controller release accepts | [Compatibility](docs/compatibility.md) |
| fix a run that stopped | [Common problems](docs/common-problems.md), then [Troubleshooting](docs/guide/troubleshooting.md) |
| know what an exit status means | [Exit codes](docs/exit-codes.md) |
| look up a term | [Glossary](docs/glossary.md) |
| see what each release changed | [Release history](docs/release-history.md) |
| understand how the pieces work together | [How it works](docs/guide/how-it-works.md) and the [lifecycle diagram](docs/ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg) |
| see every command and option, and worker routing | [Commands, options and worker routing](docs/guide/commands.md) |

[`docs/README.md`](docs/README.md) is the full map: the guides, the design
decisions (ADRs), the roadmap and the Workflow process documents.

Global options go **before** the subcommand and the repository goes last, for
example `workflow-controller --work-item <id> explain <repo>` or
`workflow-controller --role-effort review-implementation=max run <repo>`.

## What runs automatically

The Controller launches a Workflow command only when it can verify that
command's outcome from committed state: planning, local plan review and
revision, recording an already-pasted external verdict, implementation
checkpoint by checkpoint, and local implementation review and remediation.
Everything else stops for a person: the external reviews themselves, plan and
implementation approval, functional review, acceptance, and merging. From
Workflow 2.8 a gate policy can make an approval gate automatic; Controller
1.7.0 itself has not changed.

On a Workflow release that ships the orchestration protocol, such as 2.7.0,
the Workflow itself says what comes next and whether each job made progress
([Protocol mode](docs/guide/automation.md#protocol-mode-workflow-27-and-later));
on 2.5.1 and 2.6.0 the Controller decides by its own rules. Each worker is a
fresh session, and only one worker at a time may work on a repository. The
detail is in [What the Controller automates](docs/guide/automation.md) and
[Workers](docs/guide/workers.md).

## Milestone branches, pull requests and releases

A repository that commits `.workflow-controller/policy.json` (this one does)
also gets one short-lived `milestone/<id>` branch and one Draft pull request
per milestone, marked ready once the milestone is accepted and its checks
pass. A person merges it with "Squash and merge", or, when the policy opts in
to auto-merge, the Controller merges the accepted commit itself. A release is
published from `main` when the squash commit's Conventional Commit title asks
for one (`feat` a minor release, `fix` a patch, `!` a major); a
`docs`/`chore`/`ci` title publishes nothing. See
[Milestone branches and pull requests](docs/guide/milestone-branches.md) and
[Continuous integration and releases](docs/guide/ci-and-releases.md).

## When something goes wrong

Run `workflow-controller explain .` first: it names the problem and the
command that clears it. Exit `10` is the normal stop at an approval gate;
[Common problems](docs/common-problems.md) gives the quick fix for every other
stop and [Exit codes](docs/exit-codes.md) the full table.

## Development

```bash
pip install -e .
python3 tools/run_tests.py      # the full suite, in parallel shards
python3 tools/check_docs.py     # links, anchors and the commands shown on the task pages
```

Do not set `PYTHONPATH=.`, and run the suite in the foreground. Pull requests
are validated by `.github/workflows/validate.yml`, whose jobs are `plan`,
`tests` (one per shard), `tests-result` and `package`; the documentation
checks run inside `tests`. See
[Development and the test runner](docs/guide/development.md).
