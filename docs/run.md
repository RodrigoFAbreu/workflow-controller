# Run the Controller on a repository

> For: anyone driving a Workflow-managed repository with the Controller. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

Goal: run the Workflow lifecycle on a [target](glossary.md#target) until the next step only a person may take.

## Prerequisites

- The Controller is [installed](install.md).
- The repository is a Git worktree with Workflow installed through [Workflow Manager](https://github.com/RodrigoFAbreu/workflow-manager#readme), at a release the [compatibility page](compatibility.md) lists. Workflow itself is described in the [Workflow repository](https://github.com/RodrigoFAbreu/workflow#readme).
- Nothing else is working on the repository: only one worker at a time may.

Global options go before the subcommand and the repository goes last, as in `workflow-controller --work-item <id> explain <repo>`.

## Steps

1. Look first. `explain` is read-only and always exits 0: it names the pending job files, the next decision with its evidence and, at a gate, what you must do. `inspect` verifies the repository and summarises its Workflow state.

   ```bash
   workflow-controller inspect .
   workflow-controller explain .
   ```

2. Do one step, or run until a gate. [`step` performs exactly one automatic action](glossary.md#step-and-run), verifies it and stops. `run` repeats steps until a gate, a failure or the step limit (20 by default). `--follow` shows the worker's activity as it happens.

   ```bash
   workflow-controller step .
   workflow-controller run --follow .
   ```

3. At an [approval gate](glossary.md#approval-gate) the Controller stops with exit status 10. Plan approval, implementation approval and milestone acceptance are always yours, as are the external reviews and functional review. Do what `explain` says, then run again. From Workflow 2.8 a gate policy can make a gate automatic; Controller 1.7.0 itself has not changed.

4. Watch from another terminal. `follow` attaches to whatever is running and changes nothing; `status` shows every active run and job.

   ```bash
   workflow-controller follow .
   workflow-controller status
   ```

5. If the Controller or your terminal died while a worker ran, the worker kept going. Re-attach and let it finish.

   ```bash
   workflow-controller resume .
   ```

## Pausing for usage limits

The Controller has no usage-limit pause of its own yet. To stay inside a budget, bound the work: `run --max-steps 1` performs one step and exits, so a script or you can check your usage between steps, and `--max-steps N` bounds a longer run. The default comes from the `run.max_steps` setting ([The settings file](guide/runtime.md#the-settings-file)).

```bash
workflow-controller run --max-steps 1 .
```

A run that stops because it used all its steps with work outstanding exits 16; run again to continue.

## What you should see

`explain` prints a decision and, at a gate, the instruction for a human. `run` prints one line per step and ends with the reason it stopped and an exit status; [exit codes](exit-codes.md) says what each means. A normal end is exit 10 at a gate, or exit 0 when nothing is pending. On a directory Workflow Manager does not manage, every command that needs one refuses with exit 20 and says there is no `.workflow-manager/installation.json`.

Which commands the Controller runs by itself and which it leaves to you is in [What the Controller automates](guide/automation.md); the lifecycle itself is drawn in the [lifecycle diagram](ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg).

## If it fails

Run `workflow-controller explain .` first. For a pending job, `workflow-controller resume .`. For anything else see [Troubleshooting](guide/troubleshooting.md), and [exit codes](exit-codes.md) for what the status means.

Related: [every command and option](guide/commands.md), [how it works](guide/concepts.md), [update and roll back](update.md).
