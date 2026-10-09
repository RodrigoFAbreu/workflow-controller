# Run the Controller on a repository

> For: anyone driving a Workflow-managed repository with the Controller. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0.

Goal: run the Workflow lifecycle on a [target](glossary.md#target) until the next step only a person may take.

## Prerequisites

- The Controller is [installed](install.md).
- The repository is a Git worktree with Workflow installed through [Workflow Manager](https://github.com/RodrigoFAbreu/workflow-manager#readme), at a release the [compatibility page](compatibility.md) lists. Workflow itself is described in the [Workflow repository](https://github.com/RodrigoFAbreu/workflow#readme).
- Nothing else is working on the repository: only one worker at a time may.

Global options go before the subcommand and the repository goes last, as in `workflow-controller --work-item <id> explain <repo>`.

## Steps

1. Look first. `explain` is read-only and exits 0, including at a gate (it refuses with exit 20, like every command, when the repository is not admitted, and under Controller 1.7.0 also when `verify` reports `warn`): it names the pending job files, the next decision with its evidence and, at a gate, what you must do. `inspect` verifies the repository and summarises its Workflow state.

   ```bash
   workflow-controller inspect .
   workflow-controller explain .
   ```

2. Do one step, or run until a gate. [`step` performs exactly one automatic action](glossary.md#step-and-run), verifies it and stops. `run` repeats steps until a gate, a failure or the step limit (20 by default). `--follow` shows the worker's activity as it happens.

   ```bash
   workflow-controller step .
   workflow-controller run --follow .
   ```

3. At a [gate stop](glossary.md#gate-stop) the Controller stops with exit status 10.

   - **Up to Workflow 2.7:** plan approval, implementation approval and milestone acceptance are yours.
   - **On Workflow 2.8:** these three follow the repository's gate policy
     (`docs/ai-workflow/GATE_POLICY.json`). By default the Workflow satisfies them from
     complete evidence: all three for a version 2.2 item; a version 2.1 item (the default of a
     repository bootstrapped before Workflow 2.9) keeps implementation approval with a person; a version 1
     item keeps plan and implementation approval with a person.
   - **What Controller 1.7.0 does there:** it still stops at an automatic gate until a later
     release, because the Workflow answers with a disposition it does not know (`explain`
     reports `workflow_unknown_disposition`; nothing is launched). `explain` shows the action
     (`plan.satisfy`, `implementation.satisfy` or `acceptance.satisfy`) but no command. Run it
     yourself in a Claude session: `/satisfy-gate plan <id>`, `/satisfy-gate implementation <id>`
     or `/satisfy-gate acceptance <id>` (the Workflow's
     [gates page](https://github.com/RodrigoFAbreu/workflow/blob/main/docs/gates.md) describes
     them), then run again.
   - **Acceptance may ask for evidence first:** `explain` can instead report
     `functional_evidence_needed` or `pr_evidence_needed`. Controller 1.7.0 does not collect
     either for you.
     - `functional_evidence_needed`: have one functional result recorded per required flow, for
       example `python3 scripts/workflow_protocol.py record-external-result --work-item <id> --kind functional_evidence --input result.json`.
       The input fields are in "Evidence kinds" in the Workflow's [gate policy reference](https://github.com/RodrigoFAbreu/workflow/blob/main/payload/docs/ai-workflow/GATE_POLICY.md).
     - `pr_evidence_needed`: push the pull request, wait for its checks, then run
       `/satisfy-gate acceptance <id>`, which asks GitHub itself. Automatic acceptance needs an
       open, pushed pull request. A repository without milestone branches has no pull request at
       acceptance, so it must make acceptance a person's (next item).
     - Or make acceptance a person's: set `gates.acceptance.human: true`, commit the file and run
       `/adopt-gate-policy` (see Human gates below), then accept with `/accept-milestone`. The external reviews and, unless functional evidence is recorded,
       the functional review stay yours.
   - **Human gates:** to switch a repository to human gates, see the same gates page. Commit
     the policy file (`docs/ai-workflow/GATE_POLICY.json`), then run `/adopt-gate-policy` in a
     Claude session, best before a review bundle is generated, for example between milestones
     (adopting at a plan-stage stop makes the plan bundle stale; the adoption preview lists
     what it will stale). Until it is adopted the Workflow reports the unadopted policy as
     `warn`. Controller 1.7.1 and later treat a `warn` check as advisory: nothing is refused
     and `explain` shows the detail as a `verify <check>: warn: ...` line in its evidence.
     An undefined status is still refused with exit 20.
     Controller 1.7.0 does not know `warn` and refuses `explain`, `step` and `run` with
     exit 20 and "protocol verify gave an answer outside the protocol ... 'warn'"; upgrade
     it. Returning a gate to automatic is a lowering adoption, and while it is the newest
     adoption Workflow 2.8.0 and later report `warn` too. The
     [gate policy reference](https://github.com/RodrigoFAbreu/workflow/blob/main/payload/docs/ai-workflow/GATE_POLICY.md) explains when to commit and adopt it.
   - At any other gate stop, do what `explain` says, then run again.

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

Run `workflow-controller explain .` first. For a pending job, `workflow-controller resume .`. For anything else see [common problems](common-problems.md), and [exit codes](exit-codes.md) for what the status means.

Related: [every command and option](guide/commands.md), [how it works](guide/how-it-works.md), [update and roll back](update.md).
