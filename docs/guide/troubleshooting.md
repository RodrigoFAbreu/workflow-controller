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
| `20` | fail-closed refusal: unmanaged or drifted repository, malformed state, an unreconciled job file, a bad routing config | read the message; for a pending job run `workflow-controller resume <repo>` |
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

**"The repository is refused as unmanaged or unsupported."** The Controller
admits only a Workflow installation that Workflow Manager verifies, at a
Workflow release it has been validated against
(`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES`). Run
Workflow Manager's `verify` command against the target to see what is wrong
with the installation.

**"A merged milestone did not produce a release."** The version in
`pyproject.toml` did not change, so `main.yml` classified the merge
`NO_CHANGE`. See [Releasing](ci-and-releases.md#releasing).
