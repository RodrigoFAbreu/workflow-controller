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
| `20` | fail-closed refusal: unmanaged or drifted repository, malformed state, an unreconciled job file, a bad routing config, a changed Workflow release, a failed Workflow query | read the message; for a pending job run `workflow-controller resume <repo>`; for the last two see [Workflow releases and Workflow's queries](#workflow-releases-and-workflows-queries) |
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
processes were still alive 3 hours after it ended. Nothing was killed. End
the named processes, or run `workflow-controller resume <repo>` to wait again.
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

**"The pull request was closed, or merged too early."** The milestone is in a
refusal state until you choose an exit. See
[When a milestone gets stuck](milestone-branches.md#when-a-milestone-gets-stuck-the-refusal-state-exits).

**"A merged milestone did not produce a release."** The version in
`pyproject.toml` did not change, so `main.yml` classified the merge
`NO_CHANGE`. See [Releasing](ci-and-releases.md#releasing).

**"A work item I just planned is refused as malformed."** A work item whose
state declares a `registry_path` is refused (`MalformedTargetRegistryError`,
exit `20`) while that registry file does not exist, before any decision,
under every Workflow release. Workflow routes the work item, which records
the path, before `/milestone-plan` writes the registry, so a `/milestone-plan`
that stopped in between leaves this state (Workflow 2.6.0's status query
calls it row 7, `NEEDS_EDIT`). The Controller cannot launch the command
again from there: finish `/milestone-plan <id>` in a supervised session,
then `run` again.

## Workflow releases and Workflow's queries

What each Workflow release changes is in
[Supported Workflow releases](installation.md#supported-workflow-releases)
and [Workflow's queries](automation.md#workflows-queries-260-and-later).

### The repository is refused as unmanaged or unsupported

The Controller admits only a Workflow installation that Workflow Manager
verifies, at a Workflow release it has been validated against
(`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES`: 2.5.1 and 2.6.0).
Run Workflow Manager's `verify` command against the target to see what is
wrong with the installation.

A release outside that set is refused with `UNSUPPORTED_WORKFLOW_VERSION`
(exit `20`). The evidence's `reason` says which case it is:

- `outside_supported_line`: the release is not in a supported line (2.5 or
  2.6), for example 2.7.0. The message names the supported lines and the
  validated releases;
- `unvalidated_release`: the line is supported but this exact release has
  not been validated, for example 2.6.1 or 2.5.0. The message names the line
  and the validated releases.

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
| `query_git_not_isolated` | the Controller could not keep the query's Git from running a program the target configures, so nothing was run. The evidence's `facility` says why: a `hook.<name>.command` key (a configured hook in the target's own `.git/config`, `config.worktree` or a file they include), `submodule` (a populated submodule, with its `path`), a Git setting (Git ignored the Controller's override: Git before 2.31, or a later `GIT_CONFIG_PARAMETERS` in the environment), `GIT_CONFIG_COUNT` (not a count), `index` (the target's index is not a regular file) or `git` (a Git command failed; `git_argv` and `detail` say which) | remove the configured hook or the submodule from the target, or run the Controller with Git 2.31 or later and without a conflicting `GIT_CONFIG_PARAMETERS`; then run again |
| `query_launch_failed`, `query_timeout` | the interpreter could not start, or the query did not finish within 120 s | run again; if it persists, run the query by hand (below) |
| `query_failed` | Workflow's query exited non-zero without an answer, usually with a traceback: for example an undecidable state file, an unknown or `null` `feedback_layout`, or a deleted plan document | the evidence's `stderr` tail has Workflow's own error; fix what it names |
| `query_output_invalid` | the answer did not have the documented shape | a contract change the Controller does not act on: report it |

To see Workflow's own answer, run the query in the target:
`python3 scripts/workflow_fingerprint.py --resolve-feedback-path <id>` or
`python3 scripts/workflow_state.py --plan-review-publication-status <id>`.
Run this way, the query is not isolated: it runs the target's scripts in
place, under the target's own Git configuration, hooks and filters, and Git
may refresh the target's index.

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
