# How it works

> For: anyone who wants to understand what the Controller does. Last checked with: Controller 1.7.0; Workflow 2.6.0, 2.7.0 and 2.8.0.

[Back to the documentation map](../README.md)

What the Controller is for, how it relates to Workflow and Workflow Manager,
and what happens during a milestone. The words used here and in the other
guides are defined in the [glossary](../glossary.md).

## Three pieces

```text
Workflow  ->  Workflow Manager  ->  Workflow Controller  ->  your repository
(the rules)   (installs them)       (drives them)
```

- **Workflow** is a development process installed into a repository: slash
  commands (`.claude/commands/`), state scripts (`scripts/`), a state file
  (`docs/ai-workflow/WORKFLOW_STATE.json`) and process documents
  (`docs/ai-workflow/`). It turns each piece of work into a *work item* that
  moves through phases: planning, plan review, approval, implementation
  checkpoint by checkpoint, implementation review, functional review,
  acceptance. Workflow decides what is legal at every step.
- **Workflow Manager** installs a specific Workflow release into a
  repository, updates it, and verifies that the installed files are exactly
  that release. It is a separate project with its own releases.
- **Workflow Controller** (this project) operates the Workflow. It reads the
  repository's Workflow state, decides the next step, runs it in a fresh
  Claude Code session (a *worker*), checks the result against the
  repository's durable state, and repeats, until it reaches a point only a
  human may pass.

Without the Controller, a person types each Workflow command in turn. With
it, `workflow-controller run <repo>` does the automatable steps and stops,
with an explanation, where a human is needed.

## A milestone, end to end

One work item, usually called a milestone, goes like this under the
Controller, for a repository with a `.workflow-controller/policy.json`:

| Stage | Who acts |
|---|---|
| Plan written (`/milestone-plan`) | Controller-launched worker |
| Branch `milestone/<id>` created, carrying the uncommitted plan | Controller |
| Local plan review (`/review-plan`), revisions (`/apply-plan-review`) | Controller-launched workers, repeated until the review approves |
| External plan review | **human**: give the bundle to an independent reviewer, paste the verdict |
| Recording the pasted verdict (`/record-manual-plan-review`) | Controller-launched worker |
| Plan approval (`/approve-review`) | **human** |
| Branch pushed, Draft pull request opened (from the plan approval commit on) | Controller |
| Implementation, one checkpoint at a time (`/milestone-implement`) | Controller-launched workers |
| Local implementation review and remediation | Controller-launched workers, repeated until the review approves |
| External implementation review | **human**, as for the plan |
| Technical approval (`/approve-review`) | **human** |
| Functional review | **human**, from a checklist the Workflow prepares (in protocol mode a Controller-launched worker prepares and commits the checklist, and applies the human's findings) |
| Acceptance (`/accept-milestone`) | **human** |
| Pull request title and body set, pull request marked ready once checks pass | Controller |
| Merge | **human**, on GitHub, with "Squash and merge" (or a merge commit, as the policy says); the Controller, when the policy opts in to [auto-merge](milestone-branches.md#auto-merge-and-the-release-wait) |
| Wait for the release (auto-merge only) | Controller |
| Switch back to `main` (close-out) | Controller |

The human steps are the Workflow's six hard gates
([`MILESTONE_WORKFLOW.md`](../ai-workflow/MILESTONE_WORKFLOW.md), "Hard gates
summary"), plus the merge unless the repository opts in to auto-merge. The
table shows them as a person's steps, which is how they work up to Workflow 2.7
and for a repository that turns `human_approval` on (or `human: true` for one gate). From Workflow 2.8 the
three approvals (plan approval, technical approval, acceptance) follow the
repository's gate policy and by default the Workflow satisfies them itself from
complete evidence (see the [glossary](../glossary.md#approval-gate)).
The Controller never crosses a gate that needs a person: the commands a
human must run are user-only, and the Controller refuses to launch them even
if asked. At a Workflow 2.8 automatic gate the Workflow answers with the
disposition `validation`, and the Controller launches its `/satisfy-gate` as a
worker (Controller 1.7.0 to 1.13.1 did not know the disposition: `explain` reported
`workflow_unknown_disposition` and nothing was launched).

## The usage record

Every worker job that completes, and every review job in which a harness attempt
ran, records what it used in a `usage_record` key of its job record, in one
format for every harness ([workers](workers.md#telemetry) lists the fields).
One converter per harness builds it from the harness's own output: the Claude
converter from the stream's `result` events, the Codex converter from the
rollouts of the job. The cost is the four priced token groups (input, cache-read,
cache-write, output) times their prices in `controller/prices.json`, never the
reasoning count, which is inside output. The price table is versioned and ships
with no model prices until one is taken from a provider's published list; a model
absent from the table gives cost `null` and quality `unknown`. A built record
keeps the table version it was computed under and is never rewritten.

The `--json` output of `resume` prints each job record as stored, so a job
that has `usage_record` (or `usage_record_failed`) shows that key there; a record
without it prints exactly as before. No other output changes.

