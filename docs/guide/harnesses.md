# Harnesses and the roles they can take

> For: anyone choosing which harness runs a role, or adding a harness. Last checked with: Controller 1.21.1; Workflow 2.19.2.

[Back to the documentation map](../README.md)

A harness is the command-line tool the Controller launches to do model work:
Claude Code or Codex today. Each one is declared once, as an adapter in
`controller/harness.py`, and the declaration says which roles it can take, what
it can restrict, how it reports usage and which version the Controller has
verified. This page shows those declarations as two tables. You do not edit the
tables: they are generated from the adapter registry and from the routing check,
so they show what a settings file, a `--routing-config` file and
`--role-harness` accept today.

## Reading the tables

The first table has one row per harness. `reviewer.harness` is the value that
chooses it as the external reviewer (a dash means it cannot review). The roles
column lists the adapter roles it declares; the legend below the second table
says what each one is.

The second table has one row per routing role, the role a model, effort and
harness are chosen for in the `routing` section of the settings file. Each
routing role is launched as one adapter role (`Runs as`), and a harness is
accepted for the routing role only when its adapter declares that adapter role.
`yes` means the pair is accepted; `no` means it is refused before anything
runs: exit `20` in a settings file or a `--routing-config` file, a usage error
(exit `2`) for `--role-harness`. See [the routing section](runtime.md#the-routing-section) for the file
format and [worker routing](commands.md#worker-routing) for the options.

The `Pinned version` column is the build the Controller's captures were taken
from (`codex-cli 0.163.0` for Codex). `Unknown version refused` does not mean a version string is compared: the
launch is refused when an update changes a checked surface (an enabled feature,
a configuration layer, a state entry or a flag), and an update that changes none
of them is accepted. After a refusal, review by hand until the pin is
re-measured. See [common problems](../common-problems.md#a-worker-failed-or-stopped-exit-30-or-35).

The `Models` and `Efforts` columns list what the adapter declares. A route is
refused at load, before any job record is written, when its model or effort is
not in the list of the harness that runs it (see
[the routing section](runtime.md#the-routing-section)). The effort list is fixed
by the adapter; a model outside the list is added with the `harness.models`
setting, which maps a harness name to a list of model names and is not shown in
the table.

The tables change when an adapter gains or loses a role, so a role that is
`no` today (the Codex worker, the Codex tester) becomes `yes` in the same change
that declares it.

## The tables

<!-- harness-table:begin -->
| Harness | `reviewer.harness` | Family | Roles | Restricts subagents | Restricts tools | Usage | Pinned version | Unknown version refused | Models | Efforts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-code` | `claude` | anthropic | `worker`, `reviewer`, `functional-tester` | yes | yes | reported exactly by the harness | 2.1.296 (Claude Code) | yes | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`, `opus`, `sonnet`, `haiku` | `low`, `medium`, `high`, `xhigh`, `max` |
| `codex` | `codex` | openai | `reviewer` | yes | yes | computed from the harness's output | codex-cli 0.163.0 | yes | `gpt-6.1-sol` | `high`, `xhigh` |

| Routing role | Runs as | `claude-code` | `codex` |
| --- | --- | --- | --- |
| `milestone-implement` | `worker` | yes | no |
| `milestone-implement-self-review` | `worker` | yes | no |
| `apply-plan-review` | `worker` | yes | no |
| `apply-implementation-review` | `worker` | yes | no |
| `review-plan` | `worker` | yes | no |
| `review-implementation` | `worker` | yes | no |
| `milestone-plan` | `worker` | yes | no |
| `record-manual-plan-review` | `worker` | yes | no |
| `record-manual-implementation-review` | `worker` | yes | no |
| `prepare-functional-review` | `worker` | yes | no |
| `apply-functional-review` | `worker` | yes | no |
| `apply-pr-review` | `worker` | yes | no |
| `satisfy-gate` | `worker` | yes | no |
| `satisfy-gate-acceptance` | `worker` | yes | no |
| `functional-tester` | `functional-tester` | yes | no |

- `worker`: runs a Workflow command (plan, implement, apply a review) as a background job
- `reviewer`: reviews a plan or an implementation and returns a verdict
- `functional-tester`: runs the functional-review checklist in a sandbox with a shell only
<!-- harness-table:end -->

## Keeping the page current

Run `python3 tools/harness_table.py` to print the tables without touching the
page, and `python3 tools/harness_table.py --write` to rewrite the region between
the two markers above. `python3 tools/check_docs.py` fails when that region is
not what the generator prints, so a change to the registry that is not followed
by a regeneration fails the test run. To add a harness, see
[Adding a harness or a git host](development.md#adding-a-harness-or-a-git-host).
