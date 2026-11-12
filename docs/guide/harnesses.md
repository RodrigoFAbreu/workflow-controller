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
refuses a build it has not recorded); routing accepts `codex` for the tester
role. The Codex reviewer's launch is refused by the checks
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
| `claude-code` | `claude` | anthropic | `worker`, `local-reviewer`, `local-applier`, `reviewer`, `functional-tester` | yes | yes | reported exactly by the harness | 2.1.296 (Claude Code) | `verify-claude` | yes | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`, `opus`, `sonnet`, `haiku` | `low`, `medium`, `high`, `xhigh`, `max` |
| `codex` | `codex` | openai | `local-reviewer`, `local-applier`, `reviewer`, `functional-tester` | yes | yes | computed from the harness's output | codex-cli 0.163.0 | `verify-codex` | yes | `gpt-6.1-sol` | `high`, `xhigh` |

| Routing role | Runs as | `claude-code` | `codex` |
| --- | --- | --- | --- |
| `milestone-implement` | `worker` | yes | no |
| `milestone-implement-self-review` | `worker` | yes | no |
| `apply-plan-review` | `local-applier` | yes | yes |
| `apply-implementation-review` | `local-applier` | yes | yes |
| `review-plan` | `local-reviewer` | yes | yes |
| `review-implementation` | `local-reviewer` | yes | yes |
| `milestone-plan` | `worker` | yes | no |
| `record-manual-plan-review` | `worker` | yes | no |
| `record-manual-implementation-review` | `worker` | yes | no |
| `prepare-functional-review` | `worker` | yes | no |
| `apply-functional-review` | `worker` | yes | no |
| `apply-pr-review` | `worker` | yes | no |
| `satisfy-gate` | `worker` | yes | no |
| `satisfy-gate-acceptance` | `worker` | yes | no |
| `functional-tester` | `functional-tester` | yes | yes |

- `worker`: runs a Workflow command (plan, implement, apply a review) as a background job
- `local-reviewer`: reviews a plan or an implementation in the lifecycle (the local stage), read-only, and records the verdict
- `local-applier`: applies a review's findings to the plan or the implementation in the lifecycle (the local stage) and records the change
- `reviewer`: reviews a plan or an implementation and returns a verdict
- `functional-tester`: runs the functional-review checklist in a sandbox with a shell only
<!-- harness-table:end -->

## The Codex tester sandbox

`controller/codex_sandbox.py` builds the sandbox a Codex functional tester would run in, and the `functional-tester`
role is declared for `codex` above and routing accepts the pair (the `yes` in the routing column).
`launch_codex_functional` (through `codex_bridge`) is the only caller, and `job._run_functional` calls that launch for
a route whose harness is `codex`. It uses a restricted-read permissions profile (`default_permissions = "tester"`), which `codex exec` applies. On
`codex-cli 0.163.0`, with real attempts through `codex sandbox` (no model) and `codex exec` (one short turn), the
tester cannot write the clone, write outside its scratch area, read a file under the operator's home, read the
operator's environment variables, read its own credential copy, push, reach a host TCP or Unix socket, or make the
real `gh` work. The measured results are in `tests/harness_contract/codex/tester_sandbox.txt`; the live proof is opt-in
(`CONTROLLER_LIVE_CODEX_TESTS=1`, see [Development](development.md#working-from-a-checkout)) and CI does not run it.

The read deny that protects the Claude tester's credential copy and your Claude home
(`sandbox.filesystem.denyRead`, see [Automation](automation.md#what-the-controller-automates-and-how-it-stays-safe))
is for the Claude tester only. The Codex tester does not need it: its profile is restricted-read (`:minimal` plus the named
roots), so your home and its own credential copy are not readable at all. The bound on a tester's `summary` and the
`file://` origin check apply to both testers.

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
skipped when `CI` is set) runs a real tester turn; a second test in that module runs `launch_codex_functional` through
`job._functional_launch`'s Codex entry and asserts `ran`, no breach, nothing unbound, the sandbox removed and non-empty
`usage_rollouts`. `_settle` keeps the rollouts of its one scan as `usage_rollouts`, which the job turns into the tester's
`usage_record`.

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
role and `verify-codex` as its build verification, and routing accepts the pair; the reviewer, `verify-claude`, `verify-codex`, settings, options, exit codes and gates are
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


## The Codex worker sandbox

`controller/codex_worker_sandbox.py` (H4b1, [#252](https://github.com/RodrigoFAbreu/workflow-controller/issues/252)) builds and
proves the sandbox a Codex **worker** would run in. Nothing calls it: the `codex` harness still declares no worker role,
no launcher is registered and no setting, option, exit code or gate changes. The launcher is H4b2
([#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255)), described [below](#the-codex-worker-launcher).

The `worker` permissions profile (`default_permissions = "worker"`) differs from the tester's in one thing: the job's
worktree is writable. Under it a worker can edit, create, rename and delete files in the worktree and commit there; it cannot
write anywhere else, read outside the readable roots, read the operator's home, environment or its own credential copy, push,
reach a TCP or Unix socket, or make the real `gh` work (the network is off). The worktree must have a `.git` directory of its
own (`check_worktree` refuses a missing `.git`, a symlink and a `.git` file such as a linked worktree's); how a real job gets
such a tree is an owner decision needed before H4e (the launcher requires it to exist already; H4c1 leaves a seam).

Measured on `codex-cli 0.163.0` (`tests/harness_contract/codex/worker_sandbox.txt`; opt-in, see
[Development](development.md#working-from-a-checkout), skipped by default and on CI): the same probe command gave the same
result under `codex sandbox -P worker` and one `codex exec` turn. Two facts came out of it. Codex keeps `.git` read-only inside
a writable root unless it is listed, so the profile lists the worktree's `.git` as `write`; without it `git commit` cannot
create `index.lock`. With that entry, a nested read-only `.git/hooks` and `.git/config` **held**: a write to either was
refused.

A limit measured on one build is not a guarantee on the next, so a second line of defence exists. `guard_digest` hashes every
entry of `.git` except what a commit or a plain `git gc` writes (`objects/`, `refs/`, `logs/`, `index`, `HEAD`, `ORIG_HEAD`,
`COMMIT_EDITMSG`, `packed-refs`, `info/refs`, lock files) and records, for those, only their root types, any symlink or
special file and any `alternates` file. `check_guard(before, after)` refuses any other change, naming the entry (a new hook, an
edited `config`, a `commondir`, a `.git` swapped for a file or symlink). A launcher compares the two digests around every run.

The two limits H4b1 left open are closed by H4b2, in the same module. `check_composed` composes the worker's configuration
with `codex app-server --strict-config` (the exec's own `--disable` and `-c` items) and compares every value leaf by leaf with
`effective_policy`, naming the leaf and its origin layer; a layer outside the home, a forbidden key set by a layer other than
the packaged defaults, or a re-enabled disabled feature refuses. Its remaining window: the composition is read from a separate
app-server process started just before the launch, not from the `exec` itself. `nested_git_entries` and `check_nested` refuse
a `.git` entry of any type (directory, file, symlink) below the worktree root, before and after the run, because `guard_digest`
covers only the top-level `.git`; a worktree that vendors a repository is refused.

## The Codex worker launcher

`controller/codex_worker.py` (H4b2, [#255](https://github.com/RodrigoFAbreu/workflow-controller/issues/255)) launches one
rendered command as a headless `codex exec` in the job's worktree and returns the `WorkerResult` the Claude launch returns.
It is registered for a **fixture role only**: `CODEX.roles`, `worker_launch.LAUNCHERS`, the settings, options, exit codes and
gates are unchanged, and nothing an operator runs reaches it.

The order is fixed and nothing is written before the gate: the task must be `/<name> [arguments]` and is rendered from
`commands_dir` (default `<cwd>/.claude/commands`; a user-only command is refused); the binary is resolved; the build must be
recorded by `workflow-controller verify-codex`, else the launch is refused naming that command; `cwd` must be the layout's
worktree (`<runtime>/worker/<job>/tree`), with its own `.git`, no nested `.git`, the runtime root outside it, and no
`codex-home/` yet (a launch takes a fresh job id); the guard digest is taken; the sandbox is built with the job id as the
ownership tag; `check_argv`, `check_launch` and `check_composed` run; `codex exec` runs under the deadline; the credential is
copied back and its copy removed on every path, including a refusal; the guard digest, the nested scan and the build are
compared. A refusal before `exec` raises `ReviewLaunchRefusedError` (or the renderer's own `CommandRenderError`) and no `exec` has started.

The four outcomes follow the Claude classifier's precedence: the deadline or a death by signal is `INTERRUPTED`; a usage-limit
record or a non-zero exit is `FAILURE`; a zero exit with no final message is `AMBIGUOUS`; otherwise `SUCCESS`. A breach found
after the run (a changed guard digest, a nested `.git`, a changed build) turns any outcome into `FAILURE` with `subtype`
`sandbox_breach` and the entries in `stream_diagnosis`, because the worktree can no longer be trusted. The opt-in live proof is
`tests.test_codex_worker_live` (see [Development](development.md#working-from-a-checkout)); its measured result goes in
`tests/harness_contract/codex/worker_launch.txt`.

### A Codex worker run as a job record

`controller/job.py` (H4c1, [#260](https://github.com/RodrigoFAbreu/workflow-controller/issues/260)) records one such run as a
job, behind the same seam and for a fixture role only. `job._is_codex_launcher(launcher)` is true for a `CodexLauncher`, and the
job layer branches on it in three places and nowhere else: `execute_step` asks the usage gate with the Codex provider (see the next section),
`_run_codex_worker` replaces the call-and-wait block, and the completion write takes the `worker`, `telemetry` and
`usage_record` fields from `controller/codex_worker_job.py` (pure helpers: the option mapping, the stream-file text and the usage
builder). Everything around them is shared with the Claude path, so a Claude record is the record it was. The record carries
`worker_outcome`, a `worker` block (`session_id`, `num_turns`, `total_cost_usd`, `permission_denials`, `terminal_reason`,
`stop_reason` and `owned_processes_seen` are `None` for Codex; `subtype` is the launcher's), the two stream files holding the
run's plain text with `worker_streams.format` `text`, `worker_state` `ENDED`, an `ownership_tag` equal to the job id and
`worker_process`. The `usage_record` is derived from the job's rollouts and flagged `derived` (`unknown` when there is no rollout,
`usage_record_failed` when the build raises); the `usage` block is described in the next section. A Codex job has no deadline
unless a timeout is set, as for a Claude worker.

What the job runs in, and what becomes of its commits, is decided by H4e
([#303](https://github.com/RodrigoFAbreu/workflow-controller/issues/303)) and described in the next section: a private clone
that the Controller prepares from the module seam `job.CODEX_WORKTREE_PREPARER` and lands from `job.CODEX_LANDER`. Both seams
are still dark: the preparer's default is `None`, which refuses the launch before it starts and leaves a terminal
`WorkerNotStarted` record, and only a test or the live proof sets it. **Ownership** is described in the sections below.
The Codex home comes from `job.CODEX_HOME_RESOLVER` (the CLI's own `$CODEX_HOME` rule). The opt-in live proof of the record is
`tests.test_codex_worker_job_live`; its measured result is `tests/harness_contract/codex/worker_job.txt`.

### A Codex worker's private clone and its landing

H4e prepares a clone for a Codex worker and lands its commits, behind the same seams and for a fixture role only; no setting,
option, exit code or gate is new, and nothing real reaches the branch.

- **The clone.** `controller/codex_worker_tree.py` (`prepare`) makes `<runtime>/worker/<job>/tree` with
  `git clone --no-checkout --no-local --no-hardlinks --no-tags --single-branch`: a separate repository with its own `.git`
  directory, no remote (the only one is removed), no alternates, no hooks (`core.hooksPath=/dev/null`) and only the objects
  reachable from the managed branch (`--no-local` makes the clone fetch through `upload-pack`, so it copies no object store). It
  is checked out on the managed branch at the commit the Controller captured before the job. Its own config carries the author
  `Codex worker <codex-worker@controller.invalid>`, so a plain `git commit` in the sandboxed shell, which has no identity,
  succeeds. A worker that sets another author with `-c` still lands: the landing does not judge authors.
- **The clean-tree requirement.** A clone sees only committed state, and a landing is a fast-forward of the branch, so
  `prepare` refuses unless the managed repository is attached to a branch that is still at the captured commit, has no tracked
  change, staged change, intent-to-add entry or untracked non-ignored file, holds no config hook and does not contain the
  Controller's runtime root. The cost is real: the Workflow keeps plan files intent-to-add and `WORKFLOW_STATE.json` edits
  uncommitted between gates, and ignored `.ai-review/` bundles are invisible to a clone, so a role that needs them cannot run in
  a clone yet. H4e2 must settle that before it routes a role.
- **The landing.** When the worker has ended, `controller/codex_worker_landing.py` (`land`) runs under the lifecycle lock the job
  already holds. In order it refuses (each refusal names a `cause`) a run the launcher judged a breach (`sandbox_breach`), a
  clone whose `.git` changed other than by commits (`clone_git_changed`), a clone with a ref other than its branch
  (`extra_refs`), a managed branch that moved (`managed_branch_moved`), a managed tree that is not clean
  (`managed_tree_not_clean`), a runtime root inside the managed root (`runtime_inside_managed`), a configured hook
  (`managed_hooks_configured`), a tip that is not a strict descendant of the base (`not_a_descendant`), a merge commit
  (`merge_commit`), more than 200 commits (`too_many_commits`) and a submodule or gitlink in any commit (`submodule`). It then
  fetches the clone's branch into `refs/controller/landing/<job id>` (`fetch_failed`, `tip_moved`) and moves the managed branch
  with `merge --ff-only --no-overwrite-ignore` (`would_overwrite_ignored`, `fast_forward_failed`, `merge_left_tree_dirty`,
  `landing_unverified`, `landing_error`). Every write in the managed repository goes through one hardened option set
  (`gitrepo.LANDING_CONFIG`: no hook, no fsmonitor, no automatic maintenance), and the range checks read with the commit graph
  off. The clone's object store is the worker's and Git does not verify an object's id on those reads, so the range is checked
  twice: in the clone before the fetch, and again in the managed repository after it, where the base is authentic and the fetch
  verified every object it took. The fetch passes `--no-recurse-submodules`, so a submodule the base already holds is never
  fetched into or updated, whatever the managed repository's `fetch.recurseSubmodules`. A clone whose branch is the base lands `nothing`, a success. The check after the move wants the branch at the tip, `HEAD`
  on it and a clean tree, so a range that removes a path from `.gitignore` lands and then ends `landing_unverified` with `moved`
  true: the files it stops ignoring are untracked. The hook suppression was measured against Git 2.56. A Git without `hook.*`
  keys ignores them harmlessly, and `core.hooksPath` then turns off the file hooks.
- **Integrity, not content.** The landing judges the integrity of the move. The Workflow judges the content exactly as it does
  for an in-place worker, because a fast-forward keeps every commit id and tree: a worker that changes a protected file stales
  the approval it covered here as it would there, and the landing weakens no approval check.
- **The record.** `codex_worker.clone` is `{path, branch, base, guard}`, written before the launch. Before the fetch the Controller
  writes the intent `landing = {outcome: "started", base, tip, branch}`; after the landing, the final block `{outcome:
  "landed" | "nothing" | "refused", cause, base, tip, commits, branch, clone, clone_kept, clone_removed, moved, ref_left, dirty}`. `landed` and
  `nothing` go on to the unchanged verification (the legacy transition check, or the protocol's `reconcile`), which now sees the
  landed commits; a job that landed nothing keeps `phase_not_in_to_any_of` or `no_progress`. A refused landing, whatever the
  worker's own outcome, ends the job `FAILED` with `reconciliation_evidence.code` `CodexLandingRefused`, the `cause`, and the
  clone's path in `reconciliation_evidence.clone`, in one write with the `refused` block. A recovery by `resume` or `--abandon` that
  refuses a landing whose clone is no longer on disk (an earlier recovery removed it before it was killed, or you did) says so
  instead: `clone_kept: false`, `clone_removed: true`, `clone: null` in the block and `clone: null` in the evidence, with the
  message "the clone is not on disk (an earlier recovery or the operator removed it)". The Controller reads the path and cannot
  tell those two causes apart.
- **Cleanup.** The clone is removed through `runtime.remove_tree` before the job's terminal write on `landed`, `nothing`, an
  ordinary worker failure, a launcher refusal before the spawn and a `clone_failed`; the sessions the usage gate reads are kept.
  It is kept, and named in the record, after a refused landing and after an exception once the worker was spawned. A kept
  clone is removed by the operator: `rm -rf` of the exact path in `reconciliation_evidence.clone`. Every ending that leaves a
  clone on disk names it there, after the tree was settled: a `FINISHED` record that kept its clone (a removal that failed, for
  one) has `reconciliation_evidence` of `{"code": "CodexCloneKept", "clone": <path>, "message": ...}`, and any other ending
  carries `clone` beside its own evidence. A record whose tree is gone names no clone and never says one is kept. One window
  stays: for a `LAUNCHED` record, a `COMPLETED` record's recovery and a reconciliation that raised, the name is a second write
  after the terminal record, so a kill between the two leaves a terminal record without it; the path is then in
  `codex_worker.clone.path`, and `resume` does not return to a terminal record. There is no `runtime`
  command and no retention or pruning ([Workers](workers.md#observing-workers)).
- **Crash windows.** `resume` never fetches or merges. Over a `COMPLETED` Codex record it reads the refs and the tree and
  finishes the landing's bookkeeping: killed before the fetch or after it and before the merge, the leftover landing ref is
  deleted, nothing lands and the record ends `CodexLandingRefused` `landing_interrupted` with the clone kept; killed inside the
  merge, between its tree update and its ref update, the branch is at the base with the worker's content in the tree, which is
  `merge_left_tree_dirty` with nothing repaired; killed after the merge, the same post-move check as the live path gives the
  final block (`landed` with `recovered`, or `landing_unverified`); an unreadable repository or a malformed `clone` or `landing`
  block is `landing_unreadable`, which deletes nothing and keeps the clone. A `LAUNCHED` record lands nothing: it ends
  as its reconciliation decides (`INTERRUPTED` when nothing durable happened) and names the clone it kept. [Common problems](../common-problems.md#a-codex-workers-landing-was-refused)
  has the causes and their remedies.

The opt-in live proof is `tests.test_codex_worker_clone_live`: a real `codex exec` makes one plain commit in the clone and the
commit lands with the fixed author; its measured result is `tests/harness_contract/codex/worker_clone.txt`.

### A Codex worker on the usage budget

H4d ([#266](https://github.com/RodrigoFAbreu/workflow-controller/issues/266)) puts the same Codex worker job on the Codex usage
budget, behind the same seam and for a fixture role only; no setting, option, exit code or gate is new.

- **Admission.** `execute_step` asks the gate with `usage_gate(route, provider="codex")` for a Codex launcher; a Claude launcher
  keeps the call with the route alone. A hold returns a `UsageHold` of provider `codex` (exit 17, as for any held step) before
  any record, reservation or process exists. A fallback chain that selects the Codex entry carries that entry's token and is
  accounted like a single route.
- **Accounting.** The reservation is bound to the job, renewed while the worker runs, and settled at the end from the readings of
  the job's own Codex home (`<runtime>/worker/<job>/codex-home`). The record's `usage` block has the keys of a Claude worker's,
  with `accounting` `done` and one ledger entry of provider `codex`. A job that never started (a refused tree or build) is
  released with no charge; a run that fails after the spawn is charged as `unknown`; `resume` settles an orphaned Codex record's
  reservation. The end readings are merged into the shared record, so the next admission sees the percent the worker spent.
- **A limit reached mid-job.** The launcher adds a `usage_limit` fact to the diagnosis only when Codex's own limit record
  classified the run and a window reads 100 percent or more; the fact's reset is that window's `resets_at` (the weekly one on a
  tie). No reset is invented: with no readable window, or with a sandbox breach, the run is an ordinary failure. The existing
  rule then applies unchanged: a limited job that changed nothing is `INTERRUPTED` and held, one that moved the phase is `FAILED`
  naming the limit; `run` waits for the Codex reset, repeats the step, emits `usage_paused` naming provider `codex`, and stops
  at the third limited attempt; `step` exits 17 and waits for nothing. The mid-job hold takes its provider from the record's
  routed harness (Claude when the record has none).
- **Not proven live.** A real usage limit was never captured (`stderr_shapes.txt`), so the limit path is proven against the fake
  Codex only. The opt-in `tests.test_codex_worker_usage_live` runs one real turn under a real gate over a scratch runtime and
  checks the admission and the finished `usage` block; its measured result is `tests/harness_contract/codex/worker_usage.txt`.

### A Codex worker on `resume` and `--abandon`

H4c2 ([#263](https://github.com/RodrigoFAbreu/workflow-controller/issues/263)) decides what `resume` and `resume --abandon` do
with that record, so a lost Controller never leaves an orphan `codex` or `bwrap` process of the job's tag.

- **The `codex_worker` block.** `_run_codex_worker` persists `codex_worker` (`operator_home`, the `captured_digest` of the
  operator's credential file, and `lock_inherited: false`) as its first write, before the tree is prepared and so before any
  spawn. The digest is what the credential copy-back needs and a lost Controller cannot rebuild it. The block is also the
  predicate: `job._is_codex_worker_record` is true for a non-review, non-tester record that carries it, whatever the adapter's
  name. `lock_inherited` is a statement for operators and `inspect` only; the Controller never reads it. A Codex worker is
  started with `close_fds=True` and so does not hold the lifecycle lock's descriptor, which is why the liveness verdict reads
  only `worker_process` and the ownership tag, never the lock.
- **`resume` ends what it finds; it never re-attaches.** A Codex record has no stream to replay (the files are filled once,
  after the run), no stdin anchor and no Controller to read the exit status, so a live worker found by `resume` is an orphan,
  not something to wait for. `resume` ends the recorded process group and every process carrying the tag (the rule it already
  applies to a review or tester record), verifies them gone, copies a refreshed credential back (only over an operator file that
  still has the captured digest), removes the credential copy and hands the record to the shared rows, so an unlanded clone ends
  `INTERRUPTED` and a protocol decision reaches the Workflow's own `reconcile`. A Claude worker is still followed, on purpose. A
  verdict of `unverifiable`, or an unlistable `/proc`, is refused with exit 45 and nothing is reconciled; under a drifted
  installation nothing about a Codex record changes and a live orphan is not ended until the installation is repaired.
- **`resume --abandon`** follows Claude's rules: a live worker or owned process is refused naming `resume` (which ends an
  orphan); with the target root gone the refusal names the pid, the pgid and the by-hand end, then `resume --abandon`;
  `unverifiable` needs `--acknowledge-unverifiable-worker`; an `inactive` or acknowledged worker is marked `FAILED`
  (`OperatorAbandoned`) after the credentials are settled.
- **An interrupt before the spawn** (in the `codex_worker` persist, the tree preparer or the Codex-home resolver) leaves the
  record `LAUNCHED` at `STARTING` with no `worker_process`; `resume` ends it `INTERRUPTED`, as for a Claude worker, and never
  `FAILED`, which would count a run that never started against the apply relaunch bound.

Nothing real reaches the branch: the `codex` adapter still declares no worker
role, no setting selects it and no real launcher is registered. The opt-in live proof is
`tests.test_codex_worker_ownership_live`: it kills a child Controller that is running a real worker and checks that `resume`
leaves no process of the tag; its measured result is `tests/harness_contract/codex/worker_ownership.txt` (the real worker
outlives its killed Controller, and `resume` ends it).

### The review recorder and the read-only run of the two local review roles

H4e2 ([#312](https://github.com/RodrigoFAbreu/workflow-controller/issues/312)) adds two building blocks, and H4e2b
([#315](https://github.com/RodrigoFAbreu/workflow-controller/issues/315)) lets routing select them. The routing is below
the two blocks; **the default is unchanged**: with no routing config, setting or `--role-harness`, both roles run on
`claude-code` and every Claude job record is as before.

- **The recorder** (`controller/review_recorder.py`) records a local review verdict (`LOCAL_MODEL_PLAN_REVIEW` or
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW`) in the managed repository through the Workflow's own writers, run from the installed
  script set by `workflow_contract.run_in_private_copy`. `check` writes nothing; `record` takes the path of a composed
  verdict and makes the in-place command's write set. The Controller decides no state field. It refuses with one cause each:
  `recorder_branch_unsupported`, `recorder_feedback_foreign`, `recorder_index_dirty`, `recorder_identity_mismatch`,
  `recorder_interface_changed`, `state_identity_changed`, `workflow_release_changed`, `recorder_commit_pending` and
  `workflow_refused`. The runner's own errors propagate unchanged.
- **The read-only run** (`controller/codex_review_role.py`, `run_review`) runs one of the two roles as a Codex worker in the
  private clone of [a Codex worker's private clone](#a-codex-workers-private-clone-and-its-landing), made with
  `read_only=True`, which skips only the `managed_tree_not_clean` refusal (a read-only job lands nothing; the dirty paths are
  returned beside the clone). The clone gets a copy of the bound bundle, of the verified `CHANGES_SINCE_REVIEW.md` and of
  the previous `REVIEW_FEEDBACK.md`, with an input manifest, and nothing is ever copied back. The role is asked for no file:
  its final message (read from `last-message.md` as bytes) holds a `Status:` line, an optional `Review scope:` line and the
  eight sections, and the Controller writes the header itself (`Reviewer model: openai/<model>` exactly when the gate
  requires distinct reviewer models) before the recorder records it.
- **The deny set** (`codex_review_role.CAUSES`), one cause per refusal. On the way in, before the launch:
  `special_file`, `submodule`, `credential_input` (a credential-like name, or a private-key armour line in the content),
  `path_escape` and `input_over_bound` (2000 paths, 64 MiB, 8 MiB a file). On the way out, the verdict is discarded and
  nothing is recorded: `verdict_over_bound` (1 MiB), `verdict_unparsed` (invalid UTF-8, a bad `Status:` or `Review scope:`, extra lines before the first section, or any of the eight protocol sections missing), `credential_in_verdict` (armour in the message, the
  worker's `stdout` or `stderr`, checked first on every result; the result is returned with those fields emptied, though the
  session rollouts stay in `codex-home/sessions` for the usage admission) and `read_only_violated` (the clone is not as the
  run left it: a changed file, found by comparing the tree with the base commit through a fresh index the worker never touched, so an index flag such as `--skip-worktree` or `--assume-unchanged`, or a rewritten index entry, hides nothing; a new ref, a moved `HEAD`, a changed or extra input).
- **The endings** (`Ending(code, cause, worker, evidence)`), the clone removed on each: `CodexReviewNotStarted`,
  `CodexReviewWorkerFailed` (`timeout`, `terminated_by_signal`, `usage_limit`, `non_zero_exit`, `no_final_message`,
  `sandbox_breach`), `CodexReviewRefused`, `ReviewStateIdentityChanged`, `workflow_release_changed`,
  `CodexReviewRecordRefused` (the verdict is in the evidence), `CodexReviewRecordPending` (an implementation `REVISE` whose
  commit refused: the feedback and state are written, the next role commits the pending entry) and `CodexReviewRecorded`.
  `recover` ends a record holding the `codex_review` block on `resume`, `--abandon` and a drifted Workflow release
  (`CodexReviewNotRecorded`, `CodexReviewRecordInterrupted`, `OperatorAbandoned`, `workflow_release_changed`); the job
  calls it at every recovery site (see below).

#### Routing the two local review roles to Codex

`review-plan` and `review-implementation` run as the adapter role `local-reviewer`, which both adapters declare, so
`--role-harness review-plan=codex` (or a routing config, or a `review-plan` fallback chain naming `codex`) is accepted
with a model and an effort the adapter lists (`gpt-6.1-sol`, `high` or `xhigh`); a Claude-only model or no effort is
refused as for any Codex route. `codex` is still refused for every other worker role: the launcher registry is keyed by
`(adapter, role)` and holds `(codex, local-reviewer)` (`CodexReviewLauncher`, which never spawns) but not `(codex, worker)`.
The usage gate is asked on the Codex provider for such a route, on the single-route and on the chain path. The independence
check (`review_mode.worker_families`) counts the two review roles, so a Codex local stage with a Codex external reviewer
still meets the mixed-lane stop and names the collision.

A routed review runs `run_review` in place of the launch, inside the job's own record (the `codex_review` block, the
`worker_outcome`, the `usage` and `usage_record` from the Codex rollouts). It needs a protocol decision; without one the job
is `FAILED` with `WorkerNotStarted`. The managed repository is written only by the recorder, and no landing runs. The status
of each ending:

| ending | job status and what the record says |
| --- | --- |
| `CodexReviewRecorded` | worker outcome success; the Workflow's `reconcile` judges the phase (`FINISHED` when it moved as expected) |
| `CodexReviewRecordPending` | as `CodexReviewRecorded`; the evidence names `recorder_commit_pending` and says the next role commits the entry |
| `CodexReviewNotStarted` | `FAILED` with `WorkerNotStarted` and the cause; nothing started, nothing to resume |
| `CodexReviewWorkerFailed` | worker outcome failure with the launcher's subtype; `usage_limit` takes the existing wait path, `sandbox_breach` is `FAILED` |
| `CodexReviewRefused` | `FAILED`, the deny-set cause in the evidence, nothing recorded, no line of an armoured block in the record |
| `ReviewStateIdentityChanged` | `FAILED`, nothing recorded; the next run decides again |
| `workflow_release_changed` | `FAILED` with `WORKFLOW_RELEASE_CHANGED_REASON`, nothing recorded |
| `CodexReviewRecordRefused` | `FAILED`, the Workflow's class as the cause; the composed verdict is kept once at `reviews/<job id>-verdict.md` (mode 0600) and its path and SHA-256 are in the evidence, so the operator can record it by hand |

A routed review keeps the verdict text nowhere else: the job's `worker.stdout` is empty (non-`--json` `codex exec` prints
only the final message there) and `worker.stderr` keeps the banner and transcript without the lines of the final message. Only those lines are dropped: the
transcript's intermediate agent messages and command output stay, so a draft of the verdict that Codex printed earlier
can still appear in `worker.stderr`.

The recovery sites (`resume` of a launched record, `abandon`, a drifted Workflow release and the post-spawn exception
handler) call `recover`, record nothing again and leave no clone: `CodexReviewNotRecorded` (stage `preparing`) is
`INTERRUPTED`; `CodexReviewRecordInterrupted` (stage `recording`) takes `reconcile`'s judgement of the phase; a `recorded`
stage takes the row of its stored result; `OperatorAbandoned` is the job's own abandoned outcome. A review record never
reaches the worker reconciler. The routed path is proved adversarially through the job in `tests/test_codex_review_safety.py`
(credentials, symlinks and special files, submodules, hooks, crafted refs, forged headers; one test per cause of the deny set).

**The live proof** is opt-in and operator-run: `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_review_live -v`
(one real Codex turn per stage, skipped on `CI`). Its measured result belongs in `tests/harness_contract/codex/review_role.txt`.
**It has not been run yet** (it spends the operator's Codex usage), so that file does not exist and no figure below is measured.

#### What a Codex review round costs

One local review round per stage. A figure is filled in only from the live proof's measurement, never estimated; until the
operator runs the proof every measured cell reads `not measured` (reason: the live proof was not run, so no token count
exists). Costs are API list-price equivalents at `controller/prices.json` (read 2026-11-03), not what a subscription charges.

| stage | input tokens | cached-input tokens | output tokens | cost at `gpt-6.1-sol` rates | same tokens at the Claude reviewer's rates | worst case of a loop (6 local rounds) | percent of the Codex weekly reading for one job |
| --- | --- | --- | --- | --- | --- | --- | --- |
| plan | not measured | not measured | not measured | not measured | not measured | not measured | not measured |
| implementation | not measured | not measured | not measured | not measured | not measured | not measured | not measured |

The formulas, which need only the measured tokens: round cost = `input x input rate + cached-input x cache_read rate +
output x output rate`, with the rates of `gpt-6.1-sol` (2.0, 0.1 and 10.0 USD per million tokens) or of the Claude model
the review is routed to (`claude-opus-5-5`: 4.0, 0.2 and 20.0). The worst case of a loop is the round cost times the local
round cap (6 by default; the `review.local_round_cap` setting or `--review-local-round-cap` sets it). The weekly hold applies to the Codex provider's own
reading (`usage.PROVIDER_CODEX`, held by `usage.codex_weekly_pause_at_percent`, 95 by default): a loop pauses when that
reading reaches the cap, so the number of rounds before the hold is `(cap - reading now) / (percent of one job)`. The
percent of one job comes from the usage reading before and after the live run, and reads `not measured` where the proof had
no reading.

#### Routing the two apply roles to Codex

H4e3 ([#334](https://github.com/RodrigoFAbreu/workflow-controller/issues/334)) lets routing select Codex for
`apply-plan-review` and `apply-implementation-review`. They run as the adapter role `local-applier`, which both adapters
declare, so `--role-harness apply-plan-review=codex` (or a routing config or a fallback chain naming `codex`) is accepted with
a model and an effort the adapter lists. `codex` is still refused for every other worker role. **The default is unchanged**:
with no routing config, setting or `--role-harness`, both roles run on `claude-code` and every Claude job record is as before.

A review applier edits files that a clone made from committed state does not hold (the plan role's plan edits are uncommitted),
so the job *carries* a closed set of files into the Codex clone and back (`controller/codex_carry.py`):

- **The set.** The plan role's set is derived from the managed state: the plan, the registry, the mapping and the artifacts
  files of the work item, and the state file. The implementation role has none: the Controller first commits a pending feedback
  entry through the Workflow's own writer, so the clone starts from a clean, committed state. Both roles also get the item's
  `feedback/`, `plan-inputs/` and `current/` directories as read-only inputs. What is carried back is the role's
  author-written inputs only: for the plan role `plan-inputs/REVIEW_REQUEST.md`, `TEST_RESULTS.md` and `CONTEXT_FILES.txt`; for
  the implementation role `current/REVIEW_REQUEST.md`, `IMPLEMENTATION_SUMMARY.md`, `TEST_RESULTS.md`, `CONTEXT_FILES.txt` and
  `PLAN.md`. Generated files (the manifest, the bundle, `.pin/`, staging leftovers) are discarded, never carried back.
- **The guards.** Before the launch a dirty path outside the set refuses the job. Every member is read without following a link
  and refused if it is a symbolic link, a special file, a submodule, outside the repository (a path whose parent directory is a link out is outside), over the size bound or holds a
  private-key armour line; a hardlink is read as bytes, and a member is always written by a rename, never through a link. After the run, before anything is written, the clone is checked: no commit, branch or tag made by the plan role, no
  change to the clone's `.git`, the same intent-to-add set, nothing changed outside the set, and the managed repository's members
  and this work item's own entry of the state file not changed by someone else meanwhile (another item's concurrent entry is
  deliberately kept). Only the changed members are written, atomically, with the state file
  last and only this work item's entry replaced, under the Workflow's own state lock (another item's concurrent entry is kept).
- **The refusals.** One cause each, listed with their remedies in
  [common problems](../common-problems.md#a-codex-apply-was-refused-carry_): `carry_foreign_dirt`,
  `carry_commit_pending_failed`, `carry_member_refused`, `carry_unexpected_commit`, `clone_git_changed`,
  `carry_index_changed`, `carry_out_of_set`, `carry_managed_changed`, `carry_state_foreign` and `carry_clone_gone`. A refusal
  after the worker ran is `FAILED` with the clone kept and named.
- **The endings.** After the carry out (and, for the implementation role, after the existing landing of the worker's commits)
  the Controller asks the Workflow's `reconcile`. A `FINISHED` answer, and the plan role's one expected `invalid` answer (the
  bundle is stale because the revision moved), end `GATE_BLOCKED` at the Controller's own gate
  `carry_bundle_regeneration_pending` unless the guarded generator run
  ([below](#what-the-controller-runs-after-a-landing)) regenerated the bundle first; the gate then names the cause and the steps
  the operator runs. Any other answer is the resolution's own ending. The gate is the Controller's, not a refusal, and is not
  one of the carry causes.
- **Recovery.** The record's `codex_carry` block holds the stage (`preparing`, `carried_in`, `worker_ran`, `out`, `done`).
  `resume`, `--abandon`, a drifted Workflow release and the post-spawn exception handler recover through the carry: a carry out
  that was killed at a member boundary is finished by `resume`; `--abandon` and a drifted release write nothing into the managed
  repository and keep the clone when the worker ran.

##### What the Controller runs after a landing

After the implementation role's commits land and after either role's members are carried out, the Controller runs **one**
script of the managed repository: `scripts/prepare-ai-review.sh <base commit> <stage> <work item>`, from the repository root,
with stage `plan` for the plan role and `post-fix` for the implementation role (the two invocations the gate used to print).
It runs it only when it can show that nothing the worker could have written is on what the interpreter loads or what the
generator copies, and it never runs anything else of the managed tree (no other script, module, hook or test).

- **The checks before the run** (`controller/generator_run.py`):
  1. *The landing.* The implementation role's landed range (`carry base..landed head`, read as name-status records with renames
     split) may touch nothing under `scripts/` or `.workflow-manager/`, whatever the status letter. A range that cannot be read,
     or a head that is not a descendant of the base, is refused the same way. The plan role makes no commit, so the tree check
     is its whole guard.
  2. *The tree.* Every file of `scripts/` that `.workflow-manager/installation.json` records must be a regular file whose
     digest equals the record's, and `scripts/prepare-ai-review.sh` must be one of them; the directory may hold no other entry
     than those, `__pycache__` and names starting with `.` (a `sitecustomize.py`, a module that shadows the standard library, a
     sourceless `.pyc`, an extension module and a package directory are all refused). The landing check and the tree check are
     both needed: the record is a committed file, so a landing that edits a script and its digest together passes the tree check
     alone.
  3. *The copy sources.* The generator copies the changed paths and every line of the context list into the bundle and its
     archive. Each changed path (tracked, and untracked as the generator will see it) must be a regular file with no link among
     its parent directories and must resolve inside the repository; each context-list line must be relative, contain no `..`
     and resolve inside the repository. A link of any target is refused, because a review bundle never needs one.
- **The environment.** The generator runs with `PYTHONSAFEPATH=1`, `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, a private
  empty `PYTHONPYCACHEPREFIX` (no cached bytecode is read, forged or not) and a private empty `PYTHONPATH` (so the generator's
  own `scripts/:$PYTHONPATH` has no empty element, which Python would read as the working directory). Every other `PYTHON*`
  variable, `BASH_ENV` and `ENV` are removed, and a private `bin/` first on `PATH` holds one `python3` link to the Controller's
  own interpreter. Git runs with hooks, filters, fsmonitor and transports off. The run is bounded to 600 seconds and keeps the
  last 4 KiB of each stream, withheld whole if it holds private-key armour.
- **After the run** the tree and the copy sources are checked again; one that no longer passes ends the run, because the
  generator is the one process that ran with the managed tree writable under it.
- **The endings.** A run that exits `0` and passes the second check is followed by a second `reconcile`, and its answer is the
  ending (`FINISHED` is `FINISHED`; the plan role's second `invalid` is `FAILED` `reconcile_invalid`, with no gate, because the
  bundle was just regenerated). Any refusal or failure ends at the same gate as before,
  `carry_bundle_regeneration_pending`, with `reconciliation_evidence.regeneration = {cause, detail}` and the cause leading the
  gate text; the steps in the text are the ones you run by hand. The causes and their remedies are in
  [common problems](../common-problems.md#a-routed-apply-stopped-at-carry_bundle_regeneration_pending).
- **Recovery.** The record's `regeneration` block holds the stage (`checked`, `started`, `finished`). `resume` of a run cut off
  at any of them runs the checks and the generator again from the start, so the bundle is regenerated once more before the
  second `reconcile`; `--abandon` and a drifted release run no generator.
- **Still not done:** the Controller commits, amends and writes no generation record beyond what the generator itself writes
  under `.ai-review/`, and it does not run the next stage (`/review-plan`, `/review-implementation`, an approval).

The routed path is proved adversarially through the job in `tests/test_codex_review_safety.py`
(credentials, symlinks and special files, submodules, hooks, crafted refs, forged headers; one test per cause of the deny set).

**The live proof** is opt-in and operator-run: `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_review_live -v`
(one real Codex turn per stage, skipped on `CI`). Its measured result belongs in `tests/harness_contract/codex/review_role.txt`.
**It has not been run yet** (it spends the operator's Codex usage), so that file does not exist and no figure below is measured.

#### What a Codex review round costs

One local review round per stage. A figure is filled in only from the live proof's measurement, never estimated; until the
operator runs the proof every measured cell reads `not measured` (reason: the live proof was not run, so no token count
exists). Costs are API list-price equivalents at `controller/prices.json` (read 2026-11-03), not what a subscription charges.

| stage | input tokens | cached-input tokens | output tokens | cost at `gpt-6.1-sol` rates | same tokens at the Claude reviewer's rates | worst case of a loop (6 local rounds) | percent of the Codex weekly reading for one job |
| --- | --- | --- | --- | --- | --- | --- | --- |
| plan | not measured | not measured | not measured | not measured | not measured | not measured | not measured |
| implementation | not measured | not measured | not measured | not measured | not measured | not measured | not measured |

The formulas, which need only the measured tokens: round cost = `input x input rate + cached-input x cache_read rate +
output x output rate`, with the rates of `gpt-6.1-sol` (2.0, 0.1 and 10.0 USD per million tokens) or of the Claude model
the review is routed to (`claude-opus-5-5`: 4.0, 0.2 and 20.0). The worst case of a loop is the round cost times the local
round cap (6 by default; the `review.local_round_cap` setting or `--review-local-round-cap` sets it). The weekly hold applies to the Codex provider's own
reading (`usage.PROVIDER_CODEX`, held by `usage.codex_weekly_pause_at_percent`, 95 by default): a loop pauses when that
reading reaches the cap, so the number of rounds before the hold is `(cap - reading now) / (percent of one job)`. The
percent of one job comes from the usage reading before and after the live run, and reads `not measured` where the proof had
no reading.

#### Routing the two apply roles to Codex

H4e3 ([#334](https://github.com/RodrigoFAbreu/workflow-controller/issues/334)) lets routing select Codex for
`apply-plan-review` and `apply-implementation-review`. They run as the adapter role `local-applier`, which both adapters
declare, so `--role-harness apply-plan-review=codex` (or a routing config or a fallback chain naming `codex`) is accepted with
a model and an effort the adapter lists. `codex` is still refused for every other worker role. **The default is unchanged**:
with no routing config, setting or `--role-harness`, both roles run on `claude-code` and every Claude job record is as before.

A review applier edits files that a clone made from committed state does not hold (the plan role's plan edits are uncommitted),
so the job *carries* a closed set of files into the Codex clone and back (`controller/codex_carry.py`):

- **The set.** The plan role's set is derived from the managed state: the plan, the registry, the mapping and the artifacts
  files of the work item, and the state file. The implementation role has none: the Controller first commits a pending feedback
  entry through the Workflow's own writer, so the clone starts from a clean, committed state. Both roles also get the item's
  `feedback/`, `plan-inputs/` and `current/` directories as read-only inputs. What is carried back is the role's
  author-written inputs only: for the plan role `plan-inputs/REVIEW_REQUEST.md`, `TEST_RESULTS.md` and `CONTEXT_FILES.txt`; for
  the implementation role `current/REVIEW_REQUEST.md`, `IMPLEMENTATION_SUMMARY.md`, `TEST_RESULTS.md`, `CONTEXT_FILES.txt` and
  `PLAN.md`. Generated files (the manifest, the bundle, `.pin/`, staging leftovers) are discarded, never carried back.
- **The guards.** Before the launch a dirty path outside the set refuses the job. Every member is read without following a link
  and refused if it is a symbolic link, a special file, a submodule, outside the repository (a path whose parent directory is a link out is outside), over the size bound or holds a
  private-key armour line; a hardlink is read as bytes, and a member is always written by a rename, never through a link. After the run, before anything is written, the clone is checked: no commit, branch or tag made by the plan role, no
  change to the clone's `.git`, the same intent-to-add set, nothing changed outside the set, and the managed repository's members
  and this work item's own entry of the state file not changed by someone else meanwhile (another item's concurrent entry is
  deliberately kept). Only the changed members are written, atomically, with the state file
  last and only this work item's entry replaced, under the Workflow's own state lock (another item's concurrent entry is kept).
- **The refusals.** One cause each, listed with their remedies in
  [common problems](../common-problems.md#a-codex-apply-was-refused-carry_): `carry_foreign_dirt`,
  `carry_commit_pending_failed`, `carry_member_refused`, `carry_unexpected_commit`, `clone_git_changed`,
  `carry_index_changed`, `carry_out_of_set`, `carry_managed_changed`, `carry_state_foreign` and `carry_clone_gone`. A refusal
  after the worker ran is `FAILED` with the clone kept and named.
- **The endings.** After the carry out (and, for the implementation role, after the existing landing of the worker's commits)
  the Controller asks the Workflow's `reconcile`. A `FINISHED` answer, and the plan role's one expected `invalid` answer (the
  bundle is stale because the revision moved), end `GATE_BLOCKED` at the Controller's own gate
  `carry_bundle_regeneration_pending` unless the guarded generator run
  ([below](#what-the-controller-runs-after-a-landing)) regenerated the bundle first; the gate then names the cause and the steps
  the operator runs. Any other answer is the resolution's own ending. The gate is the Controller's, not a refusal, and is not
  one of the carry causes.
- **Recovery.** The record's `codex_carry` block holds the stage (`preparing`, `carried_in`, `worker_ran`, `out`, `done`).
  `resume`, `--abandon`, a drifted Workflow release and the post-spawn exception handler recover through the carry: a carry out
  that was killed at a member boundary is finished by `resume`; `--abandon` and a drifted release write nothing into the managed
  repository and keep the clone when the worker ran.

##### What the Controller does not do after a landing

After the implementation role's commits land and after either role's members are carried out, the Controller:

- does not run `scripts/prepare-ai-review.sh`, or any other script, module, generator, hook or test of the managed repository's
  working tree;
- does not regenerate, repair, withdraw or bind the review bundle under `.ai-review/<work item>/current/`: it writes no
  generated file there, only the role's carried-out author-written inputs;
- does not commit, amend or write a generation record in the managed repository, beyond landing the worker's own commits and
  the commit of a pending feedback entry made before the clone;
- does not run the next stage (`/review-plan`, `/review-implementation`, an approval).

The only Workflow code it runs after a landing is what it already runs on every step: the Workflow's queries, `reconcile` and
the recorder driver from a private copy of the installed release's verified bytes, never the managed tree's copies. Regenerating
the bundle is left to you, or to a Claude session you start, through the steps the gate prints. After an implementation-role
landing, read the landed range (`git log -p <carry base>..<landed head>`, which the gate names) before regenerating: the
generator executes `scripts/` and any module the landing added to its import path. Running the generator safely is
[H4e3b](../items/workflow-controller-h4e3b.md).

The routed path is proved adversarially through the job in `tests/test_codex_apply_safety.py` (one test per cause, links,
hardlinks and special files, writes outside the set, a forged state file, hooks, crafted refs, and a planted
`sitecustomize.py` or `secrets.py` that runs nothing) and its endings in `tests/test_job_codex_apply.py`; the guarded generator
run is proved in `tests/test_generator_run.py` (the checks) and `tests/test_codex_apply_regeneration.py` (every plant and every
cause through the job, both roles).

**The live proof** is opt-in and operator-run: `CONTROLLER_LIVE_CODEX_TESTS=1 python3 -B -m unittest tests.test_codex_apply_live -v`
(one real Codex turn per role, skipped on `CI`; it asserts the gate, the clone gone, what the managed repository holds and the
`usage_record`, and prints the measured tokens). Its output belongs in `tests/harness_contract/codex/apply_roles.txt`.
**It has not been run yet** (it spends the operator's Codex usage), so that file does not exist and no cost is measured.

## The shared command renderer

`controller/command_files.py` holds `render_command(name, arguments, commands_dir)`, a pure function over the shared
command files in `.claude/commands/` (contract: `docs/ai-workflow/COMMAND_FILES.md`). It reads the requested file (and, to
refuse an argument string that names a user-only command, every sibling command file), applies what the
contract tells an adapter to do for each frontmatter key, replaces every `$ARGUMENTS` with the argument string verbatim, leaving the body's line endings untouched
and returns a `RenderedCommand` (the prompt, the description, the argument hint, `state_writer` and `review-subject`).
There is no per-harness copy of a command. Nothing calls it yet: the `codex` adapter still declares no worker role and no
launcher is registered, so the Claude launch, the reviewer, the tester, the settings, the options, the exit codes and the
gates are unchanged.

It refuses, with `CommandRenderError` (code `COMMAND_RENDER_REFUSED`, the `cause`, the command and the file in its
evidence): a name that is not a bare lower-case command name or has no file (`unknown_command`), a file that cannot be
read, malformed frontmatter with the line named (a missing `state_writer` is reported at the closing `---` line), and an argument string given to a command that declares no
`argument-hint` or has no `$ARGUMENTS` in its body (`arguments_not_accepted`). A missing commands directory is
`MissingCommandsDirectoryError`. User-only commands are refused with `UserOnlyCommandError`; see
[Workers](workers.md#a-codex-worker-and-the-user-only-commands).

## Keeping the page current

Run `python3 tools/harness_table.py` to print the tables without touching the
page, and `python3 tools/harness_table.py --write` to rewrite the region between
the two markers above. `python3 tools/check_docs.py` fails when that region is
not what the generator prints, so a change to the registry that is not followed
by a regeneration fails the test run. To add a harness, see
[Adding a harness or a git host](development.md#adding-a-harness-or-a-git-host).
