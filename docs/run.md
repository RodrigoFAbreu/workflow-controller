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
   - **What the Controller does there:** it launches the Workflow's `/satisfy-gate` itself, as a
     worker (Haiku 5.5 for plan and implementation approval, Sonnet 5.5 for acceptance), and a run goes on when the gate is satisfied. When a requirement is unmet nothing is
     recorded, the run stops after two such steps and `explain` shows the unmet requirement.
     Controller 1.7.0 to 1.13.1 stop at an automatic gate instead, because the Workflow answers
     with a disposition they do not know (`explain` reports `workflow_unknown_disposition`;
     nothing is launched). Run the command yourself in a Claude session then:
     `/satisfy-gate plan <id>`, `/satisfy-gate implementation <id>`
     or `/satisfy-gate acceptance <id>` (the Workflow's
     [gates page](https://github.com/RodrigoFAbreu/workflow/blob/main/docs/gates.md) describes
     them), then run again.
   - **Acceptance may ask for evidence first:** `explain` can instead report
     `functional_evidence_needed`, `pr_evidence_needed` or `pr_approval_needed`.
     - `functional_evidence_needed`: from Controller 1.18.0 the Controller runs the functional
       review itself under an automatic acceptance gate: an independent tester runs the checklist's
       flows in a sandbox and the Controller records one result per flow (see "The automated functional review" in the
       [automation guide](guide/automation.md#protocol-mode-workflow-27-and-later)). It stops for a
       person only for a fault (`flows_not_runnable`, `functional_tester_unavailable`,
       `isolation_breach` and the others listed there); then test the head by hand and record one
       result per required flow, for example `python3 scripts/workflow_protocol.py record-external-result --work-item <id> --kind functional_evidence --input result.json`,
       or set `functional.automated` to `false` to keep this stop for the repository. Controllers
       before 1.18.0, and a human acceptance gate, always stop here.
       The input fields are in "Evidence kinds" in the Workflow's [gate policy reference](https://github.com/RodrigoFAbreu/workflow/blob/main/payload/docs/ai-workflow/GATE_POLICY.md).
     - `pr_evidence_needed`, `pr_approval_needed`: the Controller handles these itself under an
       automatic acceptance gate. It queries GitHub for the pull request, reports the result to the
       Workflow, waits (`run` only, within `merge.wait_seconds`) while the checks are the only thing pending, and launches
       `/apply-pr-review` when the Workflow asks for it, so a run carries on once the checks are
       green and a red pull request reopens the work item. It stops for a person only when the
       pull request still needs an approval, has no pull request or no GitHub answer, or the
       Workflow refuses the report (see "The pull request defect loop" in the
       [automation guide](guide/automation.md)). Automatic acceptance needs an
       open, pushed pull request. A repository without milestone branches has no pull request at
       acceptance, so it must make acceptance a person's (next item).
     - Or make acceptance a person's: set `gates.acceptance.human: true`, commit the file and run
       `/adopt-gate-policy` (see Human gates below), then accept with `/accept-milestone`. The external reviews and the functional review stay yours
       (with `functional.automated` true the Controller still runs the functional review when the acceptance gate is automatic; under a human gate it does not).
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

Before each job the Controller reads the Claude windows (a Codex job you
start yourself is checked against the Codex windows, see
[`usage`](guide/commands.md#usage)), forecasts what the job will cost from earlier jobs of the same role and
model, and does not start one that would not fit. It checks before the
job record exists, so a pause leaves nothing half finished. The limits
are the `usage.*` settings ([The settings file](guide/runtime.md#what-it-holds));
`usage.enabled: false` turns the check off.

- `run` waits for a timed pause: it sleeps, with no lock held, until the
  window resets plus `usage.resume_grace_seconds`, then goes through the
  whole boundary again (a newer Controller generation is noticed, the
  state and the decision are read afresh) and does not count the wait as
  a step. Ctrl-C works while it waits. It records `usage_paused` and
  `usage_resumed` in the run's events.
- `step`, a wait longer than `usage.max_wait_seconds`, and a run or
  repository cap stop with exit 17 and start nothing. The message says
  which: a timed pause names the resume time; a cap names no resume time: a run cap
  is never lifted by a reset (raise it, or start a new run), and a
  repository cap counts only the current five-hour window, so it clears
  when that window resets.
- A worker can still reach the limit while it runs, because the forecast is
  only an estimate. If it had changed nothing (no commit, no tracked-file
  edit), `run` treats the job as interrupted by the limit: it waits as
  above for the reset the provider reported and repeats the step, which
  uses no step of `--max-steps`. This also applies with `usage.enabled:
  false`. Three limited attempts in a row stop the run with exit 17, and so
  does a wait over `usage.max_wait_seconds`; `step` stops with exit 17 and
  names the reset time. A worker that had already changed state ends
  `FAILED` (exit 30) and is not repeated automatically. See
  [a worker hit a usage limit while it ran](common-problems.md#a-worker-hit-a-usage-limit-while-it-ran).
- `--usage-cap PERCENT` caps this run's Claude usage; the default is
  `usage.run_cap_percent`.

`workflow-controller usage` shows the readings and gates workers you start
yourself ([`usage`](guide/commands.md#usage)). To bound the work by steps
instead, `run --max-steps N` still applies; the default comes from the
`run.max_steps` setting.

```bash
workflow-controller run --max-steps 1 .
```

A run that stops because it used all its steps with work outstanding exits 16; run again to continue.

## The review round cap

A protocol run does not review a stage for ever. The plan stage and the
implementation stage each have a local and an external round count (given by
the Workflow from 2.15.0), and each count has a cap: `review.local_round_cap`
(default 6) and `review.external_round_cap` (default 4). When a count reaches
its cap the step stops with the `review_round_cap_reached` gate and launches
nothing: `<stage>.apply_review` at either cap, `<stage>.review.local` at the
local cap, and the external review the Controller would run at the external
cap. The run exits `10`. `workflow-controller explain <repo>` names the stage,
which cap fired, the count, the cap and the three ways on. To continue the
loop, raise that cap for one command:

```bash
workflow-controller run --review-external-round-cap 6 .
```

`--review-local-round-cap N` does the same for the local cap. The cap applies
to that command only; the next plain `run` stops again. Against a Workflow
that gives no split counts, one round number is checked against the external
cap, as the single cap was. `review.round_cap` and `--review-round-cap` still
work, deprecated, for one more release: they set every cap you have not set
yourself.
See [a run stopped at the review round cap](common-problems.md#a-run-stopped-at-the-review-round-cap).

## Letting the Controller run the external review

By default a run stops at an external review gate (`plan.review.external`,
`implementation.review.external`) and waits for a person to give the bundle
to a reviewer of another model family and paste the verdict. With
`reviewer.harness` set in the [settings file](guide/runtime.md#what-it-holds)
the Controller starts that reviewer itself, in a disposable copy of the
worktree, checks that the verdict's header matches the bundle exactly and
hands it to the Workflow. The review passes the same usage gate as a
worker, against the Codex windows, so a hold exits `17` like any other.
Every other gate, and every repository with `reviewer.harness` unset, is
unchanged.

`review.mode` (or `--review-mode` for one command) chooses the reviewer: `auto`
(the default) runs the Codex reviewer only when `reviewer.harness` is set, and a
fresh Claude reviewer only where the repository adopted a lowered
`distinct_reviewer_models` gate policy; `cross-model` needs a distinct provider;
`single-provider` runs the Claude reviewer under that lowered policy; `manual`
always stops at the gate. A choice that cannot run stops at the
`review_mode_unavailable` gate, which names the ways on
([troubleshooting](common-problems.md#a-run-stopped-at-review_mode_unavailable)).
A repository that sets nothing behaves as before. `explain` shows what the next
step will do.

Either reviewer is given a severity bar: judge only what the milestone changed,
call a finding Blocking or Important only for real harm, and leave wording,
polish and extra hardening Optional, which never starts another round. Change
it with `reviewer.brief` in the settings file
([what it holds](guide/runtime.md#what-it-holds)); it is read only from a file
outside the repository under review.

The prompt also fixes two things the brief cannot replace. From the second
external round, when the Controller has verified that the bundle's
`CHANGES_SINCE_REVIEW.md` is for the current bundle and holds a section for the
external reviewer, the reviewer is pointed at that section: check the previous
findings, review the changes, and state `Review scope: changes` only on a
`REVISE` or `BLOCK`. An approval is always a review of the whole bundle. Every
round it is told the finding format the Workflow's ingest enforces: one
Markdown list item (`- `) per finding, `None.` for an empty section, and a
status that matches the findings. A prose-only `REVISE` is refused as malformed
and ends the job `FAILED`. Text in the review request or the changes file may
narrow what the reviewer looks at but never what counts as Blocking.

A review that ends without a verdict leaves the gate open, so the manual
route still works. A limit, a timeout or an empty run ends the job
`INCOMPLETE` (exit `35`) and needs a new `run`, which waits at admission for
the reset. See [the troubleshooting entry](common-problems.md#a-review-the-controller-ran-did-not-finish).
A reviewer may spawn a sub-agent (Codex offers no way to remove that tool); the
prompt asks it not to, and when it does the job record and the step's event
name the spawn and its token usage, because a spawn roughly doubles the spend.
Every verdict the reviewer produced, a header-mismatch first attempt and a
`REVISE` included, is kept in the job record with its digest, status and what
became of it. The design is [ADR 0013](adr/0013-review-only-harness-seam.md).

## Notifications

A run you leave alone can push its events to SignalHub: a finished step, a
human gate, a blocker, a merge, a release, a usage pause and the end of the
run. Point the Controller at the server and a key file once, with
`notifications.url` and `notifications.api_key_file` in the
[settings file](guide/runtime.md#what-it-holds) (or the `SIGNALHUB_URL` and
`SIGNALHUB_API_KEY_FILE` variables), and check it:

```bash
workflow-controller notifications
workflow-controller notifications --test
```

Each event type can be turned off or re-ranked
([the notifications section](guide/runtime.md#the-notifications-section));
`notifications.enabled: false` stops all of them. Sending never slows or
fails a step. See [unattended runs](guide/automation.md#notifications-from-an-unattended-run).

## What you should see

`explain` prints a decision and, at a gate, the instruction for a human. `run` prints one line per step and ends with the reason it stopped and an exit status; [exit codes](exit-codes.md) says what each means. A normal end is exit 10 at a gate, or exit 0 when nothing is pending. On a directory Workflow Manager does not manage, every command that needs one refuses with exit 20 and says there is no `.workflow-manager/installation.json`.

Which commands the Controller runs by itself and which it leaves to you is in [What the Controller automates](guide/automation.md); the lifecycle itself is drawn in the [lifecycle diagram](ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg).

## If it fails

Run `workflow-controller explain .` first. For a pending job, `workflow-controller resume .`. For anything else see [common problems](common-problems.md), and [exit codes](exit-codes.md) for what the status means.

Related: [every command and option](guide/commands.md), [how it works](guide/how-it-works.md), [update and roll back](update.md).
