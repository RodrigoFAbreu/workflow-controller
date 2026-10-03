# Troubleshooting

[Back to the documentation map](../README.md)

Start with `workflow-controller explain <repo>`. It is read-only, always exits
`0`, and prints the pending job files (each with the command that clears it),
the lifecycle lock's state, the next decision with its evidence and, at a
gate, exactly what a human must do. `workflow-controller status` shows what is
running across the whole runtime root.

## By exit code

The normative table is in
[ADR 0001, "Exit codes"](../adr/0001-controller-generation-1-architecture.md#exit-codes).
What each code usually means in practice:

| Exit | Meaning | What to do |
|---|---|---|
| `0` | done, nothing pending | nothing |
| `2` | the command line did not parse | global options such as `--work-item` go *before* the subcommand, and the repository last: `workflow-controller --work-item <id> explain <repo>` |
| `10` | stopped at a human gate | the normal end of a run. `explain` says what to do; do it, then `run` again |
| `15` | the next action is valid but not automated (declined) | do it yourself; `explain` names the phase and command |
| `16` | `run` reached `--max-steps` with work left | `run` again, or raise `--max-steps` |
| `20` | fail-closed refusal: unmanaged or drifted repository, malformed state, an unreconciled job file, a bad routing config, an unusable settings file, a changed Workflow release, a failed Workflow query or protocol operation | read the message; for a pending job run `workflow-controller resume <repo>`; for the settings file see [The settings file is refused](#the-settings-file-is-refused-settingserror); for the last three see [Workflow releases and Workflow's queries](#workflow-releases-and-workflows-queries) |
| `30` | a worker ran and failed its expected outcome | `explain` shows what was checked; fix and rerun, or finish the step by hand |
| `35` | a worker stopped without completing its action | `explain` and the job's log show why |
| `40` | the Controller was interrupted | `workflow-controller resume <repo>` |
| `45` | the worktree is held: another Controller, or a worker that may still be running | wait, or follow the message; see [Restart](workers.md#restart-resume-re-attaches) |
| `50` | a newer Controller generation is installed | rerun with the new version |

## Common situations

**"I pressed Ctrl-C."** Only the Controller stopped. The worker keeps running
headless and keeps the worktree locked. Run `workflow-controller resume <repo>`
to re-attach and let it finish, or end the worker's process group (the
message printed on Ctrl-C names it) and then `resume`. See
[Ctrl-C ends only the Controller](workers.md#restart-resume-re-attaches).

**"The drain timed out and the command exited 45."** A worker's background
processes were still alive when the drain bound ran out after it ended (the
`worker.drain_detach_seconds` setting, 3 hours by default; the message
names the bound applied). Nothing was killed. End the named processes, or
run `workflow-controller resume <repo>` to wait again;
`resume --drain-timeout SECONDS` sets a different bound for that wait.
See [the drain bound](workers.md#owned-processes-the-daemon-list-and-the-drain-bound).

**"`step` refuses because of a pending job."** A previous job was never
reconciled. `workflow-controller resume <repo>` reconciles it. A record
`resume` cannot reconcile needs
`workflow-controller resume --abandon <job-id> <repo>`; see
[Job dispositions](workers.md#job-dispositions).

**"What is the worker doing?"** `workflow-controller follow <repo>` attaches
from any terminal and changes nothing. See
[Observing workers](workers.md#observing-workers).

**"The milestone stopped at `checks_pending`, `checks_failing` or
`integration_required`."** These are readiness gates on the milestone's pull
request. See the gate table in
[Milestone branches and pull requests](milestone-branches.md).

**"The milestone stopped at `pr_title_invalid`."** In squash mode, the plan
declares no valid pull request title, and the title on the pull request is
not a valid Conventional Commit either. The plan cannot be amended after
acceptance: set a valid title on the pull request on GitHub, then `run`
again. See
[Squash merges and the pull request title](milestone-branches.md#squash-merges-and-the-pull-request-title).

**"The milestone stopped at `release_notes_invalid`."** In squash mode with
release notes, the milestone's notes section cannot be carried by the squash
commit. See
[Release notes in the pull request body](milestone-branches.md#release-notes-in-the-pull-request-body).

**"The milestone stopped at `merge_pending` or `merge_held`."** The
repository opted in to auto-merge, and the Controller has not merged the
ready pull request: GitHub has not computed it mergeable yet, something
other than the checks blocks it (a required review, for example), a
merge was refused, or GitHub reports a conflict (`merge_pending`); or a
person converted it back to a draft (`merge_held`). The gate's message
names GitHub's state and the exits. A `run` waits at `merge_pending`
(except on a conflict) for up to `merge.wait_seconds`; merging on GitHub
with "Squash and merge" always works too. Two `merge_pending` gates
say the Controller has already merged, or may have, and it sends nothing
more: "GitHub accepted the merge ... it is not visible yet" (the send
succeeded, or the squash commit is on `main` while GitHub's reads still
show the pull request open), and "GitHub may already have merged" (the
three attempts are spent, one was interrupted before its outcome was
recorded, and `main` does not show the squash commit yet). Both clear
once GitHub shows the merge; if it never does, merge by hand or close
the pull request and run `milestone-binding --new-pr`. After three
refused merges the step refuses (exit `20`) and the Controller sends no
more: merge on GitHub. See
[Auto-merge and the release wait](milestone-branches.md#auto-merge-and-the-release-wait).

**"The milestone stopped at `release_pending` or `release_failed`."** The
pull request was squash-merged and the Controller is waiting for the
release of the squash commit before it closes out. `release_pending`: the
publishing workflow's run (`release_workflow`, `main.yml` by default)
has not appeared or is still running; a `run` waits for it. If no run
appears, publish by hand. `release_failed` (the title says the release "did not publish"): a run
failed, or a run succeeded without publishing a release. For a failed run,
"Re-run failed jobs" on the run the gate names; when the workflow
succeeded and published nothing there is no failed run to re-run. Publish
by hand; the next step classifies again and closes out once the
release exists. See
[Checking a release by hand](ci-and-releases.md#checking-a-release-by-hand).

**"`step` exits 45 while a `run` waits."** A `run` waiting at a pending
gate holds the target's lifecycle lock for the whole wait, so a second
`step` or `run` on that target exits `45`. The message names the
waiting run ("Run <id> holds it: it is waiting at <gate> until
<deadline> ... no worker is running"); the longer text about recorded
worker process groups and `worker_anchor` appears only when the holder is
a worker. `status` lists the open run, and `follow --run <id>` shows its
`waiting at <gate> until <deadline>` line. Wait for it, or press Ctrl-C
in the waiting `run` (it prints one line, "interrupted while waiting at
<gate> ...", exits `130`, and ends `interrupted`; the next step continues
from the binding's last state).

**"An auto-merged milestone closed out, but `run` did not plan the next
one."** That is intended: after an auto-merged milestone's release
settles and it closes out, the step ends with exit `0` (reason
`closed_out_released`). `run` again to plan the next milestone. One
exception: when the checkout is already on the trunk (the squash was
read from `main` itself), close-out does not fast-forward `main`, and the
next step stops at the `fast_forward_trunk` gate with the exact command
(`git merge --ff-only origin/main`, with your remote's name); run it, then
`run` again.

**"The pull request was closed, or merged too early."** The milestone is in a
refusal state until you choose an exit. See
[When a milestone gets stuck](milestone-branches.md#when-a-milestone-gets-stuck-the-refusal-state-exits).

**"A merged milestone did not produce a release."** `main.yml` classified
the merge `NO_CHANGE`: under the `conventional_commit` trigger, the pull
request's title had a type that releases nothing (`docs`, `chore`, `ci` or
`test` here); under `version_change`, the version did not change. See
[Releasing](ci-and-releases.md#releasing).

**"`main.yml` failed with `INVALID_SUBJECT`."** A commit on `main` since the
last release tag has a subject the policy cannot classify, for example a
squash message edited in the merge dialog. Nothing was tagged. Add the
commit to `release.bump_overrides` in a pull request. See
[An unclassifiable subject](ci-and-releases.md#an-unclassifiable-subject-invalid_subject).

**"The `PR title` check failed."** The pull request's title is not a
Conventional Commit, or its type is not in the policy's `change_types`, or it
marks a breaking change (`!`) on a type that releases nothing. The check's
log names which. Edit the title; the check re-runs. On a milestone pull
request in squash mode, a valid title the plan declares always replaces the
one on GitHub, so change the plan's `Pull request title:` line instead,
through a plan amendment; a plan that declares no valid title leaves a
title you set on GitHub in place.

**"A work item I just planned is refused as malformed."** A work item whose
state declares a `registry_path` is refused (`MalformedTargetRegistryError`,
exit `20`) while that registry file does not exist, before any decision,
under every Workflow release. Workflow routes the work item, which records
the path, before `/milestone-plan` writes the registry, so a `/milestone-plan`
that stopped in between leaves this state (Workflow 2.6.0's status query
calls it row 7, `NEEDS_EDIT`). The Controller cannot launch the command
again from there: finish `/milestone-plan <id>` in a supervised session,
then `run` again.

## The settings file

The user settings file is described in
[The settings file](runtime.md#the-settings-file).
`workflow-controller settings path` prints the one in use, and
`workflow-controller settings show` each value with its source.

### The settings file is refused (`SettingsError`)

Every command except `settings path` reads the settings file before it
does its work. A file that cannot be used stops the command with
`SettingsError` (exit `20`): no job record is written and no worker
runs. The file is never
rewritten. The message names the file and the key, and the evidence has
`path` and the dotted `key`. A missing file is never an error.

The file is refused when:

- it cannot be read, is not UTF-8 or not JSON, or repeats a key;
- `schema_version` is not `1`;
- a section such as `worker` is not an object;
- a setting has the wrong type or is out of bounds (a boolean is not an
  integer). The message gives the bounds;
- the routing section is malformed: a part that is not an object, an
  unusable model or effort value, or a `routing.schema_version` (a
  `--routing-config` file pasted in whole: remove that line);
- `_table_generation` or `_defaults_written` is malformed, or
  `_defaults_written` names a key the file does not hold. These are the
  Controller's own bookkeeping. Fix the entry, or delete it: a deleted
  `_table_generation` is written again by the next fill, and a value
  whose `_defaults_written` entry is gone counts as one you set, so no
  release moves it.

Fix the named key by hand, then run again. To start over, move the file
aside: the next `step`, `run`, `resume` or `milestone-binding` creates a
fresh one with every default.

### Warnings about the settings file

These are warnings on stderr, not refusals. The command goes on.

- **`has unknown key(s) ...; they are ignored`.** The file holds keys
  this release does not know, perhaps a misspelt one. They have no
  effect. `workflow-controller settings clean` removes them.
- **`... last filled by a newer Controller release ... whose keys these
  probably are`.** A newer release shares the file and added keys this
  one does not know. Leave them: the newer release uses them. Here
  `settings clean` refuses (exit `20`) and removes nothing, because this
  release cannot tell that release's keys from retired ones. Run
  `settings clean` from the newest installed release, if at all.
- **`could not fill the settings file ...`.** The file or its directory
  cannot be written, so the missing keys were not added. The file's
  current values still apply, and missing keys take their built-in
  defaults. Make the directory writable, or ignore the warning.

## Workflow releases and Workflow's queries

What each Workflow release changes is in
[Supported Workflow releases](installation.md#supported-workflow-releases)
and [Workflow's queries](automation.md#workflows-queries-260-and-later);
protocol mode (2.7.0 and later) is in
[Protocol mode](automation.md#protocol-mode-workflow-27-and-later).

### The repository is refused as unmanaged or unsupported

The Controller admits only a Workflow installation that Workflow Manager
verifies, at a Workflow release it has been validated against
(`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES`: 2.5.1 and 2.6.0) or,
from 1.7.0, one that ships the orchestration protocol (2.7.0 and later;
[Protocol mode](automation.md#protocol-mode-workflow-27-and-later)).
Run Workflow Manager's `verify` command against the target to see what is
wrong with the installation.

Any other release is refused with `UNSUPPORTED_WORKFLOW_VERSION`
(exit `20`). The evidence's `reason` says which case it is:

- `outside_supported_line`: the release is not in a supported line (2.5 or
  2.6), for example 2.4.0, and lists no protocol script. The message names
  the supported lines and the validated releases;
- `unvalidated_release`: the line is supported but this exact release has
  not been validated, for example 2.6.1 or 2.5.0, and it lists no protocol
  script. The message names the line and the validated releases;
- `no_protocol` (1.7.0 and later): the release is newer than every
  supported line, but its installation record's `managed` map does not list
  `scripts/workflow_protocol.py`, so it cannot be driven in protocol mode;
- `unsupported_protocol_major` (1.7.0 and later): the protocol script is
  listed, but `describe` does not answer protocol major 1. The evidence
  says what it answered.

A listed protocol script that cannot answer `describe` at all is refused
with `WORKFLOW_PROTOCOL_FAILED` instead
([below](#workflow_protocol_failed-and-workflow_protocol_unsupported)).

A Controller before 1.7.0 refuses 2.7.0 as `outside_supported_line`.

Both carry `supported_workflow_lines`, a sorted list; before 1.3.0 this key
was `supported_workflow_line`, a string. Either install a Controller that
admits the release, or move the target to an admitted one through Workflow
Manager ([Moving a target](installation.md#moving-a-target-to-another-workflow-release)).

### `WORKFLOW_RELEASE_CHANGED` and `workflow_release_changed`

**Before a decision** (exit `20`). The target's installed release, read
again right after the repository preflight, is not the one `inspect`
admitted when the command started: someone ran Workflow Manager's `update`,
or the close-out switched to a trunk that runs another release. Nothing was
decided or launched, and no job was recorded. Run the command again: a
fresh `inspect` admits the installed release if the Controller supports it.

The evidence has `admitted` and `installed`. `installed: null`, with the
parser's error under `manifest_error` (`UNMANAGED_REPOSITORY` or
`MALFORMED_INSTALLATION_MANIFEST`), means `.workflow-manager/installation.json`
is missing or cannot be read: restore it (Workflow Manager's `verify` shows
what is wrong) before running again.

The preflight runs before the check, so the refusal says what it already
did, and only the decision was refused:

- an action it completed (`preflight_action` other than `none`, for example
  `bound` or `closed_out`) was recorded;
- a gate it returned (`preflight_gate`) was **not** recorded, and the next
  invocation finds it again;
- whenever `preflight_events` contains `closed`, the milestone's close-out
  completed and was recorded. This is the ordinary result of merging a
  milestone whose branch ran an older release than the trunk.

**At a job's verification.** A job is verified under the release its record
carries (`target_workflow_version`). If the installed release changed while
the worker ran, or cannot be established because the manifest is missing or
unreadable, the job ends `FAILED` with reason `workflow_release_changed`
(`step` and `run` exit `30`), whose `workflow_error` evidence has `recorded`
and `installed` (`null`, with `manifest_error`, for an unreadable manifest),
and nothing is left pending. The worker's outcome was not checked under
either release; `explain` shows what to do next under the release now
installed. `workflow-controller resume <repo>` reaches the same outcome for
an interrupted job, but while the manifest is still unreadable it refuses at
`inspect` (exit `20`) and leaves the job record untouched until the
manifest is restored.

**In protocol mode** the identity is wider than the release number: it is
the release `describe` reports and the sha256 of every script the
installation record's `managed` map lists under `scripts/`. Before a
decision, a changed digest, or a protocol script that no longer answers,
is the same `WORKFLOW_RELEASE_CHANGED` refusal, with `changed_scripts` (or
`protocol_error`) in the evidence. At a job's outcome it is checked first,
before the worker's outcome and before `reconcile`: a difference ends the
job `FAILED` `workflow_release_changed`, whatever the worker did, and
`reconcile` is not run. A file in `scripts/` that the map does not list
never counts.

**A target whose own work edits a managed script.** When the target is a
Workflow repository, a checkpoint or worker that edits one of the
Workflow's managed scripts (a `scripts/workflow_*.py`) ends every such job
`FAILED` `workflow_release_changed`. This is not a defect: the job was
decided under scripts that no longer exist, and the Workflow is never
asked to judge it under others (a 2.6.0 target's queries refuse edited
script bytes the same way, as `query_script_modified`). Workflow Manager's
`verify` then reports the installation as drifted, and the target is
refused until the installed scripts are restored (Workflow Manager's
`update` or repair).

**`resume` after such an edit.** A plain `workflow-controller resume
<repo>` tolerates a drifted installation for exactly this case: a pending
protocol job whose recorded script digests differ from the files' bytes
now is ended `FAILED` `workflow_release_changed`, from the installation
record and the bytes alone, without running any Workflow script. A legacy
job, or a protocol job whose digests are unchanged, still refuses with the
drift (exit `20`) and is left untouched, and `resume --abandon` keeps the
strict check.

### `WORKFLOW_QUERY_FAILED` and `workflow_query_failed`

For a 2.6.0 target the Controller asks Workflow for the feedback path and
the plan-review publication status. A query that cannot give an answer the
Controller may act on is never answered by the 2.5.1 rule:

- **at a decision** it refuses with `WORKFLOW_QUERY_FAILED` (exit `20`);
  nothing is launched and no job is recorded;
- **at a job's verification** the job ends `FAILED` with reason
  `workflow_query_failed` (`step` and `run` exit `30`), whose
  `workflow_error` carries the same code and evidence, and nothing is left
  pending. `resume` reconciles an interrupted job to the same outcome.

The evidence's `reason` names the step that failed:

| `reason` | Meaning | What to do |
|---|---|---|
| `query_script_modified` | the target's `scripts/workflow_state.py` or `scripts/workflow_fingerprint.py` is missing, unreadable, or not the admitted release's bytes (the evidence has the path and both digests). Nothing was executed | run Workflow Manager's `verify`; restore the file, then run again |
| `query_private_copy_failed` | the Controller could not create, write or remove its private copy in the temporary directory (for example a full disk) | free space in `$TMPDIR`, then run again |
| `query_git_not_isolated` | the query's Git would run a program the target or the operator configures, and the Controller cannot switch it off without perhaps changing Workflow's answer, or cannot switch it off at all. The evidence's `facility` says which: `hook` (an executable `post-index-change` hook, with its `path`: `git diff` fires it when it refreshes the index), a `hook.<name>.event` key (a configured hook for that event, from any configuration file, with its `scope`), `fsmonitor` (a `core.fsmonitor` program, or `GIT_TEST_FSMONITOR`, with the `program`; with Git before 2.36, which runs a boolean value as a program, any non-empty `core.fsmonitor`, with `git_release` too), `filter` (a filter program Git would have run on a path the query hashes, with the drivers in `filters`: the query ran, Git started the Controller's probe instead, and the answer is discarded), a `hook.<name>.command` key (a configured hook in the target's own `.git/config`, `config.worktree` or a file they include), `submodule` (a populated submodule, with its `path`), a Git setting (Git ignored the Controller's override: Git before 2.31, or a later `GIT_CONFIG_PARAMETERS` in the environment), `GIT_CONFIG_COUNT` (not a count), `index` (the target's index is not a regular file) or `git` (a Git command failed; `git_argv` and `detail` say which). No program the facility names was run. Two hook refusals are stricter than Git: a `hook.<name>.event` whose hook `hook.<name>.enabled = false` turns off, and, with `core.hooksPath` set empty (Git then runs no hook), an executable `post-index-change` at the worktree's root | for `hook`, remove the hook or its executable bit; for a hook key, remove the hook or its `post-index-change` event; for `fsmonitor`, unset `core.fsmonitor` or set it to `true` (Git's own daemon, which needs Git 2.36 or later; with an older Git, unset it or set it empty), or unset `GIT_TEST_FSMONITOR`. For `filter`, remove the `filter=` attribute from the plan-stage files, which the status query hashes. `git diff` also re-hashes any tracked file whose index entry is stale: refresh the index in the target (`git update-index --refresh`, which runs the filter there, under your control) for a file whose content is unchanged, and stage, commit or restore an edited one. For `submodule`, deinitialize it in the target (`git submodule deinit <path>`, which empties its directory). Otherwise run the Controller with Git 2.31 or later and without a conflicting `GIT_CONFIG_PARAMETERS`. Then run again |
| `query_launch_failed`, `query_timeout` | the interpreter could not start, or the query did not finish in time (the `timeouts.workflow_query_seconds` setting, 120 s by default) | run again; if it persists, run the query by hand (below) |
| `query_failed` | Workflow's query exited non-zero without an answer, usually with a traceback: for example an undecidable state file, an unknown or `null` `feedback_layout`, or a deleted plan document | the evidence's `stderr` tail has Workflow's own error; fix what it names, or run Workflow's own command yourself |
| `query_output_invalid` | the answer did not have the documented shape | a contract change the Controller does not act on: report it |

To see Workflow's own answer, run the query in the target:
`python3 scripts/workflow_fingerprint.py --resolve-feedback-path <id>` or
`python3 scripts/workflow_state.py --plan-review-publication-status <id>`.
Run this way, the query is not isolated: it runs the target's scripts in
place, under the target's own Git configuration, hooks and filters, and Git
may refresh the target's index.

### `WORKFLOW_PROTOCOL_FAILED` and `WORKFLOW_PROTOCOL_UNSUPPORTED`

For a protocol-mode target (1.7.0 and later) the Controller asks the
Workflow's `scripts/workflow_protocol.py` for its decisions and outcomes. An
operation that gives no answer the Controller may act on is never answered
by a rule of the Controller's own:

- **at a decision** it refuses with `WORKFLOW_PROTOCOL_FAILED` (exit `20`);
  nothing is launched and no job is recorded;
- **at a job's outcome** (`reconcile`) the job ends `FAILED` with reason
  `workflow_protocol_failed`, whose `workflow_error` carries the code and
  evidence.

The evidence's `reason` names the step: `protocol_script_modified` (a
managed script is missing or not a regular file; nothing ran),
`protocol_private_copy_failed`, `protocol_git_not_isolated` (as
`query_git_not_isolated` above, with the same remedies),
`protocol_launch_failed`, `protocol_timeout`
(`timeouts.workflow_query_seconds`), `protocol_no_document` (no single JSON
answer; a missing module is named), `protocol_envelope_invalid` (the
answer does not match the vendored schema) and `protocol_refused` (the
Workflow's own refusal, with its `code`, `message` and `retryable`).
`WORKFLOW_PROTOCOL_UNSUPPORTED` (exit `20`) means the Workflow answered for
a protocol major other than 1. To see the Workflow's own answer, run
`python3 scripts/workflow_protocol.py --protocol-major 1 --repo-root .
next-action --work-item <id>` in the target (not isolated, as above).

### Protocol-mode gates and job failures

Each gate below launches nothing; `explain` shows the Workflow's row,
disposition and action beside it.

- `workflow_unhealthy`: the Workflow's `verify`, run before every
  decision, reported a failing check; the gate names each one and its
  detail. A failing `state_valid` can be transient (a planning worker
  interrupted between routing the item and writing its registry);
  `installation_release_matches` fails when the installation record names
  a release other than the installed scripts', which the Workflow
  Manager's `update` or `repair` puts right. Repair what the checks name,
  then run again.
- `workflow_unknown_disposition`, `workflow_unknown_action`,
  `workflow_unknown_worker_role`: the Workflow answered a value this
  Controller release does not know (a later protocol minor may add one).
  Install a Controller that knows it, or take the step by hand.
- `workflow_user_only_action`: an `automatic` answer whose worker is
  `user_only`. The Controller never launches it.
- `workflow_invocation_mismatch`: the Workflow's invocation text for the
  action differs from the command the Controller renders. The Workflow's
  `reconcile` would refuse the job, so nothing is launched. Report it as a
  Workflow release defect; it is not a command to run.
- `decision_unstable`: three decisions in one step were stale by the time
  they were checked again; the gate names the last two state identities
  (or work-item lists). Something else is changing the target: let it
  finish, then run again.
- `no_progress_repeated`: the last two jobs for the same work item and
  action both ended with no progress, so a third is not launched. Read
  their records (`workflow-controller status`, `follow`), then change the
  work item: take the action by hand, or fix what stops the worker. The count
  restarts once the work item's state identity differs from the one the last
  counted job left, so running again without a change gets the same gate. A
  job that made progress, or a gate, also resets it.

A protocol job can also end `FAILED` (`step` and `run` exit `30`) with:

- `decision_stale_at_launch`: the decision was no longer the Workflow's
  answer immediately before the worker would start. No worker ran, nothing
  is pending, and the next step decides again;
- `reconcile_invalid`: the Workflow's `reconcile` judged the job `invalid`;
  its reasons are in the record verbatim;
- `completion_not_committed_at_head`: after a checkpoint the working
  tree's `WORKFLOW_STATE.json` records a completion that `HEAD` does not.
  A human commits it, as the checkpoint commit would have, or discards it,
  as for the legacy-mode gate.

### The stale-plan-bundle gate under Workflow 2.6.0

At a plan-review-ready phase (`AWAITING_LOCAL_PLAN_REVIEW`,
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` or `AWAITING_PLAN_APPROVAL`), when
Workflow reports the plan-review bundle not `BOUND`, the Controller stops at
the stale-plan-bundle gate. At `AWAITING_PLAN_APPROVAL` it replaces the
approval gate, because `/approve-review plan` would refuse. Its
`what_is_required` quotes Workflow's remedy and detail. What to do depends
on the row:

- **Row 4b (`BUNDLE_UNVERIFIED`) or 4c (`LEGACY_UNVERIFIED`)**: the content
  is unchanged but the bundle is missing, withdrawn, rejected or stale.
  Follow the gate's `safe_resume_command`: the author-file steps, in the
  author-input directory it names (`.ai-review/<id>/plan-inputs/` when it
  exists, else `.ai-review/<id>/current/`, the directory Workflow 2.6.0's
  generator reads), then the generator command. Step 0 restores the author
  files from a `current.rejected-*` quarantine when a withdrawal left one.
  Regenerating alone, Workflow's own remedy, fails while the author files
  are absent or belong to an earlier round.
- **Row 4a (`CONTENT_DRIFTED`)**: the working tree no longer matches the
  content the bundle was bound to. The Controller offers **no restore**. The
  gate's command is `workflow-controller --work-item <id> explain <repo>`,
  which changes nothing, and a human decides. No restore is offered because
  the Controller cannot establish that `.ai-review/<id>/current/files/`
  still holds the bound state, and cannot keep a path from changing between
  a printed step and its execution. The bound state covers the existence,
  mode and bytes of each protected path the bundle's `MANIFEST.md` lists,
  and the plan-stage classification sets of the item's artifacts
  declarations file (`docs/ai-workflow/registry/<id>-artifacts.json`). A
  restore overwrites the working tree's post-binding edits, which may exist
  nowhere else: keep them first, and re-apply them at the next editing
  phase. Once the working tree matches the bound state again, the next step
  reads `BOUND` and continues.

In every row, Workflow's other alternative, withdrawing with `/milestone-plan`
at this phase, is quoted in `what_is_required` only as a consequence: it
discards both recorded review stages. It is never the `safe_resume_command`,
and the Controller never runs it.

The `REJECTED`-marker gate at a ready phase gives the same author-file steps
under Workflow 2.6.0, `AWAITING_PLAN_APPROVAL` included (under 2.5.1 it
names the bare generator there, as before).

### `plan_review_binding_inconsistent` and `unexpected_plan_review_status`

Both stop the Controller at any plan phase of a `"2.1"`/`"2.2"` item on a
2.6.0 target and run nothing. Their command is
`workflow-controller --work-item <id> explain <repo>`, which changes nothing:

- `plan_review_binding_inconsistent`: Workflow refuses to report the status
  (`PlanReviewBindingInconsistentError`, rows 4d and 6) because the item's
  plan-review binding record contradicts its phase. The gate quotes
  Workflow's message, which names the contradiction, and a human
  reconciles it;
- `unexpected_plan_review_status`: Workflow answered a status that does not
  belong to the phase, or answered for another phase. Workflow 2.6.0 never
  does either, so this is a contract change the Controller does not act on.

## The manual-external review gate says "do not send" the bundle

At `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` or
`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, before it offers the
bundle for external review, the Controller checks that the stage's
review ledger in Workflow's state matches the bundle:

- the ledger is well formed;
- its `review_content_id` is the one in the bundle's `MANIFEST.md`;
- a local-stage `APPROVE` is recorded for it.

If one check fails, the gate's reason is
`manual_external_ledger_incoherent`. Its text starts
`do not send <bundle> for external review`, then names the failed check,
the manifest's id and the ledger's id (or `none`). Do not send the
bundle: the ledger does not describe it, so a verdict on it would not
match what Workflow has recorded. The Controller repairs nothing; Workflow owns the ledger.

The way out depends on the stage, and is a human decision:

- **Plan stage.** Withdraw the plan with `/milestone-plan <id>`. It
  returns the item to `REVISING_PLAN`, and the republished plan gets a
  fresh local review. The withdrawal discards both recorded plan-review
  stages, and the withdrawn content can never be bound again unchanged:
  only an edit plus regeneration can. The Controller never runs this
  command; the gate names it as text, as the user's decision.
- **Implementation stage.** No Workflow command moves this phase back,
  so the Workflow state needs explicit resolution by the user before any
  external review.

Rerunning `/review-plan`, `/review-implementation` or `explain` does not
help: the first two do not write the ledger at this phase, and `explain`
derives the same gate again. When the ledger is coherent, the gate is
the usual one.
