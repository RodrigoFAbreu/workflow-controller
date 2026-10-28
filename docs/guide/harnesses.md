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
chooses it as the external reviewer (a dash means it cannot review); a reviewer
is cross-model when its family differs from the family of the harnesses the
worker roles are routed to, and mixed worker families stop the review with
`review_mode_unavailable`. The roles
column lists the adapter roles it declares; the legend below the second table
says what each one is.

A worker role is launched through the launcher of the harness it is routed to
(`controller/worker_launch.py`); Claude Code's is the only launcher today.

The second table has one row per routing role, the role a model, effort and
harness are chosen for in the `routing` section of the settings file. Each
routing role is launched as one adapter role (`Runs as`), and a harness is
accepted for the routing role only when its adapter declares that adapter role.
`yes` means the pair is accepted; `no` means it is refused before anything
runs: exit `20` in a settings file or a `--routing-config` file, a usage error
(exit `2`) for `--role-harness`. See [the routing section](runtime.md#the-routing-section) for the file
format and [worker routing](commands.md#worker-routing) for the options.

The `Captured with` column is the build the Controller's captures were taken
from (`codex-cli 0.163.0` for Codex); it is for information and is not compared
at launch. The `Build verification` column names the Controller command that
admits a build of that harness where the Controller checks one. For Claude Code
that is `verify-claude`, and it admits the functional-tester launch only: the
worker and reviewer launches run any build on `PATH`. Codex has none
(`none: launch checks only`): its launch is refused by the checks of its
surfaces described next, until `verify-codex` ships. `Unknown version refused`
keeps its meaning and does not mean a version string is compared: the
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
| Harness | `reviewer.harness` | Family | Roles | Restricts subagents | Restricts tools | Usage | Captured with | Build verification | Unknown version refused | Models | Efforts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-code` | `claude` | anthropic | `worker`, `reviewer`, `functional-tester` | yes | yes | reported exactly by the harness | 2.1.296 (Claude Code) | `verify-claude` | yes | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`, `opus`, `sonnet`, `haiku` | `low`, `medium`, `high`, `xhigh`, `max` |
| `codex` | `codex` | openai | `reviewer` | yes | yes | computed from the harness's output | codex-cli 0.163.0 | none: launch checks only | yes | `gpt-6.1-sol` | `high`, `xhigh` |

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

## The Codex tester sandbox

`controller/codex_sandbox.py` builds the sandbox a Codex functional tester would run in, and it is proven but not
wired: no role is declared for it (`functional-tester` stays `no` for `codex` above), and nothing calls it except its
tests. It uses a restricted-read permissions profile (`default_permissions = "tester"`), which `codex exec` applies. On
`codex-cli 0.163.0`, with real attempts through `codex sandbox` (no model) and `codex exec` (one short turn), the
tester cannot write the clone, write outside its scratch area, read a file under the operator's home, read the
operator's environment variables, read its own credential copy, push, reach a host TCP or Unix socket, or make the
real `gh` work. The measured results are in `tests/harness_contract/codex/tester_sandbox.txt`; the live proof is opt-in
(`CONTROLLER_LIVE_CODEX_TESTS=1`, see [Development](development.md#working-from-a-checkout)) and CI does not run it.

Limits: `/usr`, `/etc` and the Codex and Controller install trees are readable; the stand-in `gh` refuses only by
name (`/usr/bin/gh` fails for lack of credentials and network, not because of the profile). One check is open (the
one entry of `KNOWN_LIMITS`): the composed configuration values, and managed layers that are not files, are verified
only for their MCP servers and features, so a managed layer could still change another setting. C10g-3 builds that
check before the sandbox is wired.

`controller/codex_build.py` names a Codex build and records which builds were verified, offline, and nothing calls it
except its tests. `build_digest` is a SHA-256 over every file a launch can run as Codex: the launcher's package and
each `optionalDependencies` package found where Node's resolution finds it in the launch environment. A layout it
cannot bind (a shell shim, a package that declares `dependencies`, a symlink leaving the package, an unreadable or
oversized tree) gives no digest, and the digest is recomputed from disk on every call. The interpreter that runs the
launcher (`node`) is not part of the identity; it only starts the native binary. The record is
`verified-codex-builds.json` in the runtime root, with `verify-claude`'s trust rules (a regular file you own with no
group or world write bit, created `0600`, never written through a symlink) and its own lock; `is_verified` answers
from a fresh digest and the record and writes nothing. Nothing writes the record until `verify-codex` (C10g-2b), and no
launch reads it until C10g-3.

## Keeping the page current

Run `python3 tools/harness_table.py` to print the tables without touching the
page, and `python3 tools/harness_table.py --write` to rewrite the region between
the two markers above. `python3 tools/check_docs.py` fails when that region is
not what the generator prints, so a change to the registry that is not followed
by a regeneration fails the test run. To add a harness, see
[Adding a harness or a git host](development.md#adding-a-harness-or-a-git-host).
