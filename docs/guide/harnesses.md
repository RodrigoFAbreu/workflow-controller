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
worker and reviewer launches run any build on `PATH`. For Codex it is
`verify-codex`, and it admits the tester launch only (`launch_codex_functional`
refuses a build it has not recorded); the tester role is declared but routing
refuses it until C10g-3c. The Codex reviewer's launch is refused by the checks
of its surfaces described next. `Unknown version refused`
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
| `codex` | `codex` | openai | `reviewer`, `functional-tester` | yes | yes | computed from the harness's output | codex-cli 0.163.0 | `verify-codex` | yes | `gpt-6.1-sol` | `high`, `xhigh` |

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
name (`/usr/bin/gh` fails for lack of credentials and network, not because of the profile). The composed
configuration is checked by `check_composed` (`controller/codex_sandbox.py`): it reads what Codex composes from the
tester's home, flags and any managed layer (`codex app-server`, `config/read`, no model request), compares every
security-relevant value with the pinned tester profile leaf by leaf, and refuses a difference naming the value and its
layer. It is a separate control from `check_launch`; `run_tester` runs both before it starts `codex exec`. One
limit remains (the one entry of `KNOWN_LIMITS`, `composition_window`): the configuration is read by the `app-server`
just before the launch, so a layer that changes in that window, or a setting only `exec` composes differently, is not
seen. The reviewer's control makes the same assumption.

`controller/codex_bridge.py` is the Codex tester's sandbox bridge. From the functional review's captured candidate,
`build_bridge` builds the tester's folder: the read-only clone (the Claude tester's own `clone_candidate`, so the two
share one set of checkout rules), the scratch bare origin, the output folder and the writable-location targets inside
the profile's one write area, the Controller-only check directory and item record outside it, the stand-ins on the
`PATH`, and everything `codex_sandbox.build` writes and checks. A fresh clone that differs from the manifest, or any
other failure, removes the folder before anything runs. `check_bound` reports, after a run, any difference between the
clone and the candidate in the shared words (`bound_differences`); the scratch origin and output folder are not part of
it. `prompt` is the tester's one shared prompt with the Codex sandbox's own limits (`codex_bridge.SANDBOX_LIMITS`: `HOME`
is not writable and not a readable location, so a flow that needs a writable home sets `HOME` inside `TMPDIR`), and a
test ties those limits to the profile.

`controller/codex_tester.py` is the Codex tester's run. `run_tester` takes a bridge and the flows, rebuilds the pinned
command from the model and effort the bridge recorded and compares it with the one `build` made, runs `check_argv`,
`check_launch` and `check_composed` in that order (a refusal propagates and starts nothing), then starts `codex exec`
under a deadline with the bridge's prompt on standard input. The outcome is `timeout` when the deadline passed, else
`limit` when a rollout records a usage limit, else `failed` when the exit code is not zero or the run wrote no final
message (an absent, empty or blank `-o` file), else `ran`; the final message is the `-o` file, never the transcript. The
result has the Claude tester launch's shape (outcome, detail, message, readings, sub-agents, credential copy-back) and
the credential copy in the job's Codex home is removed on every path. Whether the output folder holds valid results is
not decided here. One opt-in test (`tests/test_codex_tester_live.py`, `CONTROLLER_LIVE_CODEX_TESTS=1`, one model turn,
skipped when `CI` is set) runs a real tester turn.

`launch_codex_functional` (same module) is the tester's surroundings and returns what `review.launch_functional`
returns for the Claude tester, plus `subagents` and `exit_code`. In order: it checks that `harness.CODEX` declares the
tester role, resolves the `codex` launcher (the symlink is kept, as `verify-codex` records it), and gates the build:
`codex_build.is_verified` must admit it, and nothing is written or started before it does. It then sweeps a sandbox an
earlier breach kept, checks the runtime root against the sandbox folders, snapshots the real repository (worktree,
`HEAD`, refs) and the local origin's refs, builds the bridge, calls `run_tester`, snapshots again, runs the binding
check and collects the stand-ins' call log. A *breach* is a difference between the snapshots, or a build that stops
being the verified one during the run (the record removed, the digest changed, the launcher re-pointed); a breach sets
`keep` and the sandbox is left for `review.settle_functional`. A changed clone is `unbound`. Every refusal inside the
launch (an unverified build, no binary, a control, an altered argv, a clone mismatch, an existing job folder) is
`functional_tester_unavailable`, with the sandbox and the credential copy removed. The adapter declares the tester
role and `verify-codex` as its build verification, but routing refuses the pair (`NOT_YET_ROUTABLE`) until C10g-3c, so
nothing calls the launch; the reviewer, `verify-claude`, `verify-codex`, settings, options, exit codes and gates are
unchanged.

`controller/codex_build.py` names a Codex build and records which builds were verified, offline; the Codex tester launch
and its tests call it. `build_digest` is a SHA-256 over every file a launch can run as Codex: the launcher's package and
each `optionalDependencies` package found where Node's resolution finds it in the launch environment. A layout it
cannot bind (a shell shim, a package that declares `dependencies`, a symlink leaving the package, an unreadable or
oversized tree) gives no digest, and the digest is recomputed from disk on every call. The interpreter that runs the
launcher (`node`) is not part of the identity; it only starts the native binary. The record is
`verified-codex-builds.json` in the runtime root, with `verify-claude`'s trust rules (a regular file you own with no
group or world write bit, created `0600`, never written through a symlink) and its own lock; `is_verified` answers
from a fresh digest and the record and writes nothing. `workflow-controller verify-codex` writes the record (see
[`verify-codex`](commands.md#verify-codex)); the Codex tester launch reads it (a build it has not recorded is refused)
and no other launch does.

## Keeping the page current

Run `python3 tools/harness_table.py` to print the tables without touching the
page, and `python3 tools/harness_table.py --write` to rewrite the region between
the two markers above. `python3 tools/check_docs.py` fails when that region is
not what the generator prints, so a change to the registry that is not followed
by a regeneration fails the test run. To add a harness, see
[Adding a harness or a git host](development.md#adding-a-harness-or-a-git-host).
