# How it works

> For: anyone who wants to understand what the Controller does. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

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
and for a repository that turns `human_approval` on. From Workflow 2.8 the
three approvals (plan approval, technical approval, acceptance) follow the
repository's gate policy and by default the Workflow satisfies them itself from
complete evidence (see the [glossary](../glossary.md#approval-gate)).
The Controller never crosses a gate that needs a person: the commands a
human must run are user-only, and the Controller refuses to launch them even
if asked. Controller 1.7.0 also stops at Workflow 2.8 automatic gates, until a
later release (roadmap C10), because its protocol 1.0 action catalogue cannot
launch the gate-satisfaction actions.
