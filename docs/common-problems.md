# Common problems

> For: anyone whose run stopped and wants the quick fix. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0.

Start with `workflow-controller explain <repo>`. It is read-only, exits 0 (but refuses with exit 20 when the repository is not admitted, as on an unmanaged one, and under Controller 1.7.0 also when `verify` reports `warn`), and prints the pending job files with the command that clears each one, the next decision with its evidence and, at a gate, exactly what a person must do. Each entry below is the symptom, the one-line fix and where the detail is. What each exit status means is in [exit codes](exit-codes.md); the long account of every stop is the [troubleshooting guide](guide/troubleshooting.md).

## The run stopped and exited 10

Not a failure: it is a [gate stop](glossary.md#gate-stop), the Controller waiting for you. Run `workflow-controller explain <repo>`, do what it says, then run again. See [run](run.md#steps).

## The run stopped at a Workflow 2.8 automatic gate

`explain` reports `workflow_unknown_disposition` and shows the action (`plan.satisfy`, `implementation.satisfy` or `acceptance.satisfy`) but no command to run. Run `/satisfy-gate plan|implementation|acceptance <id>` yourself in a Claude session. At acceptance, `explain` may first report `functional_evidence_needed` (record the functional result) or `pr_evidence_needed` (push the pull request, wait for its checks, then run `/satisfy-gate acceptance <id>`), or you can set `gates.acceptance.human: true`, commit the file, run `/adopt-gate-policy` in a Claude session, and accept with `/accept-milestone`. See [run](run.md#steps), which has an example command. To switch the repository to human gates, see the Workflow's [gates page](https://github.com/RodrigoFAbreu/workflow/blob/main/docs/gates.md), commit the policy file (`docs/ai-workflow/GATE_POLICY.json`) and run `/adopt-gate-policy` in a Claude session (see the next entry and the [gate policy reference](https://github.com/RodrigoFAbreu/workflow/blob/main/payload/docs/ai-workflow/GATE_POLICY.md), which explains when to commit and adopt it).

## The run refuses with "protocol verify gave an answer outside the protocol" and `'warn'` (exit 20)

Fixed in Controller 1.7.1: it treats a `warn` check as advisory, so `explain`, `step` and `run` carry on and `explain` shows the check's detail as a `verify <check>: warn: ...` line. A status the protocol has not defined is still refused with exit 20. Controller 1.7.0 does not know `warn`: the Workflow's `verify` reported a check as `warn`, so it refuses `explain`, `step` and `run` (`inspect` still works); upgrade to 1.7.1 or later. The `warn` itself has a cause to clear: for example, the gate policy file (`docs/ai-workflow/GATE_POLICY.json`) is present, committed or not, and not adopted yet. Commit the policy file, then run `/adopt-gate-policy` in a Claude session (the timing advice is under Human gates in [run](run.md#steps)). Some causes are not cleared by adoption. To see which one, run `python3 scripts/workflow_protocol.py --protocol-major 1 --repo-root . verify` in the repository and read the `gate_policy` check's detail.

A lowered gate is such a cause. While an adoption that lowers a gate (for example, switching a gate back to automatic) is the newest adoption, Workflow 2.8.0 and later report `warn`; Controller 1.7.1 shows it and carries on, and 1.7.0 refuses. The warning stays until a later adoption that lowers nothing becomes the newest. Do not adopt again only to hide it: the Workflow treats the warning as the expected signal of a lowered gate. See the [gate policy reference](https://github.com/RodrigoFAbreu/workflow/blob/main/payload/docs/ai-workflow/GATE_POLICY.md) and [compatibility](compatibility.md).

## The run exited 16

`run` reached `--max-steps` with work still outstanding. Run it again, or raise `--max-steps`. See [run](run.md).

## A step refuses because of a pending job (exit 20)

A previous [job](glossary.md#job) was never reconciled. Run `workflow-controller resume <repo>`. A record `resume` cannot reconcile needs `workflow-controller resume --abandon <job-id> <repo>`. See [job dispositions](guide/workers.md#job-dispositions).

## A worker failed or stopped (exit 30 or 35)

`explain` shows what was checked and why the job's expected outcome was not met. Fix what it names and run again, or finish the step by hand. The job's log, which `workflow-controller follow <repo>` and `status` point to, shows what the worker did. See [troubleshooting](guide/troubleshooting.md#common-situations).

## The command exited 45 and nothing was launched

The worktree is held: another Controller, a `run` waiting for checks or a merge, or a worker that may still be running holds its lock. Nothing is wrong. Wait, or follow the message; after a Ctrl-C or a drain timeout, `workflow-controller resume <repo>` re-attaches. See [restart](guide/workers.md#restart-resume-re-attaches) and [the drain bound](guide/workers.md#owned-processes-the-daemon-list-and-the-drain-bound).

## The command exited 17 and nothing was started

The usage budget held the job back before it started, so there is no job to resume. The message says which of two causes it was:

- **A timed pause**: the Claude or Codex window would pass its threshold (`usage.pause_at_percent`, `usage.weekly_pause_at_percent`). `run` normally waits for it by itself; `step`, or a wait longer than `usage.max_wait_seconds`, stops instead. Run again after the resume time the message names (in local time).
- **A cap**: the run or the repository has spent `usage.run_cap_percent` or `usage.repository_cap_percent`, counting work still outstanding. No reset time is waited for. Raise the cap or `--usage-cap`, start a new run for a run cap, or for a repository cap run again after the five-hour window resets, when its spending falls.

`workflow-controller usage` shows the readings and reservations. See [pausing for usage limits](run.md#pausing-for-usage-limits), [`usage`](guide/commands.md#usage) and [exit codes](exit-codes.md).

## A worker hit a usage limit while it ran

The budget forecasts, it cannot stop a job that exceeds its forecast. A job that meets the limit mid-way fails (exit 30). Check `workflow-controller usage`, wait for the reset, then run `explain` and run again. Recognising this case and waiting for it automatically is a roadmap item (C8b).

## I pressed Ctrl-C

Only the Controller stopped. The worker keeps running headless and keeps the worktree locked. Run `workflow-controller resume <repo>` to re-attach and let it finish. See [troubleshooting](guide/troubleshooting.md#common-situations).

## The repository is refused as unmanaged or unsupported (exit 20)

Workflow Manager must verify the installation, and the Controller must admit its Workflow release. Run `workflow-manager verify <repo>` to see what is wrong with the installation, and check [compatibility](compatibility.md) for the release. See [troubleshooting](guide/troubleshooting.md#the-repository-is-refused-as-unmanaged-or-unsupported).

## A step refuses with WORKFLOW_RELEASE_CHANGED

The repository's Workflow release changed after the command started, usually because Workflow Manager's `update` ran. Nothing was launched; run again and the new release is admitted if the Controller supports it. See [troubleshooting](guide/troubleshooting.md#workflow_release_changed-and-workflow_release_changed).

## A plan or milestone was refused as malformed

A work item whose state names a registry file that does not exist is refused before any decision. Finish the planning command in a supervised session, then run again. See [troubleshooting](guide/troubleshooting.md#common-situations).

## The settings file is refused (exit 20)

The message names the file and the key. Fix that key by hand and run again, or move the file aside and the next `step`, `run`, `resume` or `milestone-binding` creates a fresh one. `workflow-controller settings path` prints the file in use. See [troubleshooting](guide/troubleshooting.md#the-settings-file-is-refused-settingserror).

## The milestone stopped at a pull request or release gate

`checks_pending`, `checks_failing`, `integration_required`, `pr_title_invalid`, `merge_pending`, `release_pending` and the others are named gates with their own fixes. See [milestone branches](guide/milestone-branches.md) and [troubleshooting](guide/troubleshooting.md#common-situations).
