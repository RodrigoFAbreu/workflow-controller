# Glossary

> For: anyone reading the Workflow or Controller documentation. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

The words these projects use. The first group belongs to the Workflow and is
worded so that another repository can reuse it unchanged. The second group is
the Controller's own.

## Workflow terms

### Work item

One unit of planned work tracked by the Workflow, identified by an id. Its
record lives in `docs/ai-workflow/WORKFLOW_STATE.json` in the repository the
Workflow is installed in, and that file is the ground truth for where the work
item is.

### Milestone

A work item that is planned, implemented, reviewed and accepted as one piece,
and that normally lands as one pull request.

### Phase

Where a work item is in the Workflow's lifecycle, for example `PLANNING`,
`IMPLEMENTING`, `AWAITING_PLAN_APPROVAL` or `MILESTONE_COMPLETE`. Phases that
begin with `AWAITING_` are points where the Workflow waits. The shipped
[lifecycle diagram](ai-workflow/diagrams/workflow-v2-1-lifecycle.drawio.svg)
draws them all.

### Checkpoint

One slice of a milestone's plan, implemented and committed on its own. A work
item has one or more checkpoints, done one per implementation step.

### Approval gate

A point where the Workflow stops until a person decides. There are six hard
gates. Three only a person can pass: plan approval, implementation approval
(also called technical approval) and milestone acceptance. The other three wait
for the external plan review, the external implementation review and the
functional review. A tool may run everything between gates, but never
crosses one for you.

### Bundle

The review package the Workflow generates for a plan or an implementation. It
is bound to the exact content that was reviewed, so a change after the review
makes the approval stale.

### Binding

A recorded tie between two things that must stay together. The Workflow binds a
published plan review to the plan content it reviewed. The Controller binds a
milestone to its branch and pull request (see
[Milestone binding](#milestone-binding) below).

### Protocol

The Workflow Orchestration Protocol: a small set of commands a Workflow
installation answers so a tool can drive it without knowing its internals
(`describe`, `next-action` and `reconcile`). Workflow 2.7.0 is the first
release that ships it, and major version 1 is the one the Controller speaks.

## Controller terms

### Target

The repository the Controller operates on, written `<repo>` in commands. It
needs a Workflow installation that Workflow Manager verifies, at a release the
[compatibility page](compatibility.md) lists.

### Workflow release

The version of the Workflow installed in a target, for example `2.6.0`, as
recorded in `.workflow-manager/installation.json`. It is not the same as a work
item's governing version, which is the protocol version that one work item was
started under and never changes.

### Step and run

`step` performs one automatic action. `run` repeats steps until it reaches a
gate, a failure or its step limit.

### Gate stop

What the Controller does at an approval gate: it stops, says what you must do,
and `run` exits with status 10. See [exit codes](exit-codes.md).

### Worker

One fresh `claude` session that runs one Workflow command. It never continues an
earlier session.

### Job

The Controller's record of one worker: what was launched, why, the outcome it
had to produce and what actually happened. Jobs are stored under the runtime
root.

### Expected outcome

The durable change a command must make for its job to count as a success. A
worker's own report is never trusted; only Workflow and Git state are.

### Lifecycle lock

An exclusive lock on the target's Git directory. It makes sure only one worker
at a time operates on a target.

### Runtime root

The directory that holds the Controller's own state (jobs, run logs and
milestone binding records), by default `~/.local/state/workflow-controller`.

### Milestone binding

The Controller's record that ties a milestone to its branch and pull request.

### Repository policy

`.workflow-controller/policy.json`, committed in a target. It turns on
milestone branches, pull requests and releases, and says how, including the
merge method and the release trigger.

### Workflow mode

How the Controller drives a target. In legacy mode (Workflow 2.5.1 and 2.6.0)
it decides from the state file by its own rules. In protocol mode (a release
admitted by the protocol, such as 2.7.0) the Workflow's own `next-action`
decides the next action and its `reconcile` judges each job's outcome.

### Pull request title

Under squash merges, the subject of the commit that lands on the trunk. A
milestone plan declares it, and under the `conventional_commit` release
trigger its Conventional Commit type (`feat`, `fix`, `docs`, ...) decides the
release. See [Releasing](guide/ci-and-releases.md#releasing).

### Runtime kind

What code is running: `package` (an installed wheel), `source` (an editable
checkout) or `unidentified`. `workflow-controller --version` prints it. See
[Runtime identity](guide/runtime.md#runtime-identity).

### Generation

The compatibility axis for job records and for handing over between a running
Controller and a newer one. Every release so far is generation 1. A version, in
contrast, names a release.
