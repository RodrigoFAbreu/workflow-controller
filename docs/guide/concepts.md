# Concepts

[Back to the documentation map](../README.md)

What the Controller is for, how it relates to Workflow and Workflow Manager,
what happens during a milestone, and what the words used in the rest of the
documentation mean.

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
summary"), plus the merge unless the repository opts in to auto-merge.
The Controller never crosses one: the commands a
human must run are user-only, and the Controller refuses to launch them even
if asked. Reducing these gates where automated evidence is enough is on the
[roadmap](../ROADMAP.md).

## Glossary

- **Target**: the repository the Controller operates on (`<repo>` in
  commands). It must have a Workflow installation that Workflow Manager
  verifies, at a Workflow release the Controller admits: 2.5.1 or 2.6.0,
  or (from 1.7.0) a release that ships the Workflow Orchestration Protocol,
  such as 2.7.0
  (see [Supported Workflow releases](installation.md#supported-workflow-releases)).
- **Work item / milestone**: one unit of planned work in the Workflow state,
  identified by its id, for example `workflow-controller-adaptive-test-sharding`.
- **Phase**: where a work item is in the Workflow, for example `IMPLEMENTING`
  or `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`.
- **Governing version**: the Workflow protocol version a work item is bound
  to (`"1"`, `"2.1"`, `"2.2"`). It is fixed per work item and is not the
  Workflow release number.
- **Workflow release**: the release of Workflow a target has installed, for
  example `2.6.0`, as recorded in `.workflow-manager/installation.json`. The
  Controller admits a target by it, and chooses by it what it reads from
  Workflow and what it asks Workflow's own queries.
- **Workflow mode**: how the Controller drives an admitted target. In
  **legacy mode** (2.5.1, 2.6.0) it decides from the Workflow state by its
  own rules. In **protocol mode** (a release admitted by capability, such
  as 2.7.0) the Workflow's own `next-action` decides the next action and
  its `reconcile` judges each job's outcome (see
  [Protocol mode](automation.md#protocol-mode-workflow-27-and-later)).
- **Gate**: a point where the Controller stops and says what a human must do.
  `run` exits `10` there; that is the normal, expected end of a run.
- **Worker**: one fresh `claude` session running one Workflow command. It
  never continues an earlier session.
- **Job**: the Controller's record of one worker: what was launched, why,
  the expected outcome, and what actually happened. Stored under the runtime
  root in `jobs/`.
- **Step / run**: `step` performs one automatic action; `run` repeats steps
  until a gate, a failure or its step limit.
- **Expected outcome**: what durable change a command must make for its job
  to count as a success. A worker's own report is never trusted; only
  Workflow and Git state are.
- **Bundle**: the review package the Workflow generates for a plan or an
  implementation, bound to the exact content reviewed.
- **Lifecycle lock**: an exclusive lock on the target worktree's Git
  directory that ensures only one worker at a time operates on it.
- **Runtime root**: the directory holding the Controller's own state (jobs,
  run logs, milestone binding records), by default
  `~/.local/state/workflow-controller`.
- **Binding**: the Controller's record tying a milestone to its branch and
  pull request.
- **Repository policy**: `.workflow-controller/policy.json`, committed in the
  target. It turns on milestone branches, pull requests and releases, and
  says how, including the merge method and the release trigger.
- **Pull request title**: under squash merges, the subject of the commit
  that lands on the trunk. A milestone plan declares it, and under the
  `conventional_commit` release trigger its Conventional Commit type
  (`feat`, `fix`, `docs`, ...) decides the release. See
  [Releasing](ci-and-releases.md#releasing).
- **Version and generation**: the version (for example `1.4.0`) names a
  release, and the release tag is its only authority. A Controller built
  from a checkout reports the highest release tag the checkout contains
  (or, before this repository's cutover, `pyproject.toml`'s static
  version). The generation (`controller/GENERATION.json`) is the
  compatibility axis for job records and handoff between a running
  Controller and a newer one. Every release so far is generation 1.
- **Runtime kind**: what code is running: `package` (an installed wheel),
  `source` (an editable checkout) or `unidentified`.
