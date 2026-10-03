# What the Controller automates, and how it stays safe

> For: anyone who wants to know what the Controller launches by itself and how it stays safe. Last checked with: Controller 1.7.0; Workflow 2.6.0 and 2.7.0.

[Back to the documentation map](../README.md)

## Safety model

- **Never writes Workflow state.** `controller/target_state.py` exposes
  no write function at all; every durable Workflow lifecycle change is
  made by a worker running a real Workflow command in the target
  repository.
- **Never crosses a human gate.** The decision engine launches only by
  the automatic-dispatch rule, whose declared outcomes cover only
  commands in its eight-command selected set (`/milestone-plan`,
  `/review-plan`, `/record-manual-plan-review`, `/apply-plan-review`,
  `/milestone-implement`, `/review-implementation`,
  `/apply-implementation-review`, `/record-manual-implementation-review`).
  The worker layer refuses to execute any of the four user-only commands
  (`controller.worker.USER_ONLY_COMMANDS`) even if handed one directly,
  including inside a task's text -- two independent mechanisms fed by two
  different sources. In protocol mode the Workflow's own `next-action`
  takes the decision engine's place: only an `automatic` answer naming an
  action the Controller knows, and a worker that is not `user_only`, is
  launched ([Protocol mode](#protocol-mode-workflow-27-and-later)).
- **Never trusts a worker's word.** Every launched job is verified
  against durable Workflow and Git state, including the artifacts its
  command promises; the worker's own report is never read to decide.
- **One worker per worktree, and never a replacement too early.** The
  lifecycle lock, the recorded worker and the processes it owns decide
  when the previous worker has ended, never elapsed time or the end of a
  turn. A job is reconciled only after its worker has ended and its owned
  work has drained.
- **Acts only under the Workflow release it admitted.** Before every
  decision the installed release is read again and must still be the one
  `inspect` admitted, and every job is verified under the release its
  record carries. A changed or unreadable installation refuses the
  decision, or fails the job, rather than acting under the wrong rules. For
  a 2.6.0 target, what Workflow itself answers (the feedback path, whether a
  plan-review bundle is current) is asked of Workflow, running only the
  admitted release's script bytes ([below](#workflows-queries-260-and-later)).
  For a protocol-mode target the release `describe` reports and the
  digests of the Workflow's managed scripts must also be the ones admitted.
- **Never hot-reloads.** A running Controller generation executes from an
  immutable, content-addressed snapshot of its own source and never
  mutates or reloads it; a newer approved generation triggers an
  intentional stop (a durable handoff record, exit 50), never an
  in-process update.
- **Merges only the accepted head, never rewrites.** The Controller
  merges a pull request only when the repository opts in
  ([Auto-merge and the release wait](milestone-branches.md#auto-merge-and-the-release-wait)),
  and then only one way: GitHub's own squash merge of exactly the
  acceptance commit, after `/accept-milestone`, after the Controller has
  seen that commit everywhere and every check green, bound to that commit
  by `--match-head-commit`, so GitHub performs it or refuses it. The
  only `gh pr merge` call is in the GitHub boundary
  (`controller/forge.py`), and it carries `--squash` and
  `--match-head-commit` and none of `--auto`, `--disable-auto`,
  `--admin` or `--delete-branch`; a test scans `controller/` for any
  other shape. The Controller never enables GitHub's auto-merge request,
  so nothing it leaves on GitHub can merge a later head. No code path
  pushes to the trunk ref, closes a pull request or deletes a branch.
  The Controller never force-pushes, resets, rebases, amends, deletes a
  ref or moves a tag: every branch push is a fast-forward and every tag
  push creates a new ref. Without the opt-in, a human merges every pull
  request.

## Automatic dispatch

Each phase handler selects the next action from the target's evidence, or
returns a human gate. Whether a selected action is launched is decided by
one rule, the same at every phase: it is launched **iff** a verifiable
expected outcome is declared for `(phase, governing workflow version,
command)` (`controller.decision.AUTOMATIC_TRIPLES`, held equal to the
keys of `controller.job.EXPECTED_OUTCOMES` by a test). Otherwise it is
reported as declined (exit 15), with a reason that names the phase, the
command and the missing triple. Unknown or ambiguous states still fail
closed (exit 20).

This rule is legacy mode's. A protocol-mode target (2.7.0 and later) is
decided by the Workflow instead; see
[Protocol mode](#protocol-mode-workflow-27-and-later). For both legacy-mode
Workflow releases, 2.5.1 and 2.6.0, the rule makes the same launches
automatic:

| Phase | Version | Command |
|---|---|---|
| no work item yet | none | `/milestone-plan` |
| `PLANNING` | `"1"`, `"2.1"`, `"2.2"` | `/milestone-plan` (2.6.0: see below) |
| `AWAITING_LOCAL_PLAN_REVIEW` | `"2.1"`, `"2.2"` | `/review-plan` |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `"2.1"`, `"2.2"` | `/record-manual-plan-review` |
| `REVISING_PLAN` | `"2.1"`, `"2.2"` | `/apply-plan-review` (2.6.0: see below) |
| `AWAITING_EXTERNAL_PLAN_REVIEW` | `"1"` | `/apply-plan-review` |
| `IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION` | `"2.1"`, `"2.2"` | `/milestone-implement`, once the plan approval is `CURRENT` and `HEAD` records every checkpoint completion and phase transition the working tree records |
| `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `"2.2"` | `/review-implementation` |
| `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `"2.2"` | `/record-manual-implementation-review`, only for an already-pasted, admissible manual `APPROVE`/`REVISE` |
| `APPLYING_REVIEW_FEEDBACK` | `"2.2"` | `/apply-implementation-review`, for an admissible `REVISE` |

Every one of these is still gated first when its evidence says so: a
`REJECTED` bundle marker, a stale or incoherent bundle, a `BLOCK` verdict,
an inadmissible verdict, a missing or stale plan approval, a checkpoint
`COMPLETE` or a `SELF_REVIEWING_IMPLEMENTATION` transition that the
working tree's `WORKFLOW_STATE.json` records but `HEAD` does not. Everything
else is a gate or a decline. That includes performing the manual external
review, `/approve-review`, `/accept-milestone`, the functional-review
stage (`/apply-functional-review` is declined), `AMENDING_PLAN`, and
consuming a `"2.1"` external implementation verdict. A `"1"` work item's
`/milestone-implement` is declined, because that branch writes no state
the Controller could verify.

What differs between the releases is at the plan stage, for `"2.1"` and
`"2.2"` items. Under 2.6.0, `/milestone-plan` and `/apply-plan-review` end
by binding the new plan-review bundle (Workflow's `bind_plan_review_bundle`),
which is what moves the item to `AWAITING_LOCAL_PLAN_REVIEW`, and the job
succeeds only when Workflow then reports that bundle `BOUND` there. If the
bundle generation fails, the item stays at `PLANNING` or `REVISING_PLAN`
with its revision published but not bound (row 9, `PUBLISHED_UNBOUND`), the
job fails, and the next step launches the same command with the explicit
work-item id again, which is Workflow's own documented remedy. The
Controller never launches `/milestone-plan` at a plan-review-ready phase:
under 2.6.0 that withdraws the item and discards both review stages.

A launched job is verified against durable Workflow and Git state, never
against what the worker says. A phase change alone is not success: each
implementation-stage outcome also checks the artifacts it promises (the
checkpoint completion committed at `HEAD`, a coherent implementation
bundle generated at the live `HEAD`, a review ledger bound to the
bundle's content). A stale, withdrawn, `REJECTED` or mis-bound artifact
makes the job `FAILED` (exit 30), and `run` stops there. The next decision
launches nothing either. After an uncommitted checkpoint completion or
`SELF_REVIEWING_IMPLEMENTATION` transition, for example, it is a gate
naming each uncommitted fact, because another `/milestone-implement`
worker could build on the completion or commit it. A human commits the
completion as the Workflow step would have, with its trailers, or
discards it and reruns the step.

## Workflow's queries (2.6.0 and later)

For a 2.6.0 target the Controller keeps no rule of its own for two facts. It
asks Workflow:

| Fact | Query | Asked when |
|---|---|---|
| where a work item's review feedback lives | `scripts/workflow_fingerprint.py --resolve-feedback-path <id>` | any decision or verification that reads a verdict or a functional-review finding |
| whether its plan-review bundle is current | `scripts/workflow_state.py --plan-review-publication-status <id>` | every plan-stage phase of a `"2.1"`/`"2.2"` item, and the plan-stage job's verification |

A 2.5.1 target never runs a query: the Controller's own rules apply exactly
as before. A `"1"` item has no publication status, so for it only the
feedback path is asked.

**How a query runs.** The Controller reads the target's two scripts, checks
their sha256 against the digests it holds for the admitted release, copies
exactly those bytes into a private temporary directory and runs them there
with its own interpreter (`-B -E -s`, stdin closed, a timeout), in the
target's root. A modified script is never executed, and no module from the
target's `scripts/` directory can run.

The scripts run Git in the target (`git hash-object`, `git diff
--name-only`, `git ls-files`), so the Controller also prepares that Git.
Every Git command of the query reads a private copy of the target's index,
may use no transport, and runs with settings that override the target's
configuration: no hook runs (neither `.git/hooks` nor a `core.hooksPath`
directory nor a configured hook), no filter driver (a `filter=` attribute's
`clean`, `smudge` or `process` program, whichever configuration file
defines it) and no fsmonitor. The Controller checks that Git applies each
setting before the query runs.

A query gives Workflow's answer or none, so a program is switched off only
where that cannot change the answer. Three kinds of program can change it:
a hook or an fsmonitor program may edit the files Workflow hashes, an
fsmonitor program decides which paths Git re-checks, and a clean filter
changes the bytes `git hash-object` hashes, which are Workflow's content
identity. Where Git would run one, the Controller refuses the query
(`WORKFLOW_QUERY_FAILED`, reason `query_git_not_isolated`):

- **A hook the query fires.** `git diff` fires `post-index-change` when it
  refreshes the index, and that is the only hook the queries' Git fires. An
  executable `post-index-change` hook in the hooks directory (`.git/hooks`,
  or `core.hooksPath`), or a configured hook for that event from any
  configuration file, refuses the query before it runs. Hooks for other
  events are switched off, and the query runs.
- **An fsmonitor program.** `core.fsmonitor` naming a program, from any
  configuration file, refuses the query before it runs. Git's built-in
  daemon (`core.fsmonitor = true`, from Git 2.36 on) is Git's own code and
  finds what a full check finds, so it is switched off and the query runs.
  Git before 2.36 has no boolean there: it runs `true`, `false` or any
  other non-empty value as a program found on `PATH`, so with that Git a
  boolean refuses the query too.
- **A filter program Git would run.** A driver with a `clean` or `process`
  program gets a probe of the Controller's in its place and is marked
  `required`. Where Git would run the program, it starts the probe instead,
  which records the driver and fails, and so does Git. The Controller then
  refuses, whatever the query answered. A driver that no path the query
  hashes selects is never started, and the query answers as usual, so Git
  LFS installed for every repository does not refuse a query that hashes
  no LFS file. Only Git knows which paths a filter applies to, so this is
  the one refusal made after the query has run. The program still never
  runs.

The Controller also refuses the query before it runs when it cannot switch
something off: a hook command in the target's own Git configuration, a
populated submodule, or a Git that ignores the settings (before 2.31). The
queries never ask Git for a patch, a log, a checkout or a fetch, so diff
drivers, signature programs and transports have nothing to run.

The query writes nothing to the target, `.git` included. The one exception
is a timestamp: in a repository with a split index (`core.splitIndex`),
Git's read of the shared index moves the modification time of
`.git/sharedindex.<hash>` on, even through the private copy, and never its
content. The private directory is removed whatever the outcome.

**A query that fails is never answered by the old rule.** At a decision it
refuses (`WORKFLOW_QUERY_FAILED`, exit `20`) and nothing is launched. During
a job's verification it makes the job `FAILED` with reason
`workflow_query_failed`, and no job is left pending. See
[Troubleshooting](troubleshooting.md#workflow_query_failed-and-workflow_query_failed).

**The plan stage under the publication status.** Workflow answers a status
and a row of its table. The Controller reads only the answer's status, row,
remedy, detail and advisory, never Workflow's binding record itself:

| Phase | Status | Outcome |
|---|---|---|
| a ready phase (`AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL`) | `BOUND` (rows 2, 3) | the phase's own decision, as under 2.5.1, with the row in its evidence |
| a ready phase | `CONTENT_DRIFTED` (4a), `BUNDLE_UNVERIFIED` (4b), `LEGACY_UNVERIFIED` (4c) | the stale-plan-bundle gate ([Troubleshooting](troubleshooting.md#the-stale-plan-bundle-gate-under-workflow-260)); at `AWAITING_PLAN_APPROVAL` it replaces the approval gate |
| a non-ready phase (`PLANNING`, `REVISING_PLAN`, `AMENDING_PLAN`) | rows 5, 7-11 | the phase's own decision, as under 2.5.1, with the row in its evidence |
| any plan phase | Workflow refuses the status (rows 4d, 6) | the `plan_review_binding_inconsistent` gate |
| any plan phase | any other status, or an answer for another phase | the `unexpected_plan_review_status` gate |
| any plan phase | the query fails | refused, exit `20` |

None of these gates launches anything, and no `safe_resume_command` is
Workflow's withdrawal (`/milestone-plan` at a ready phase), which the gates
quote only as the alternative that discards both review stages.

## Protocol mode (Workflow 2.7 and later)

From 1.7.0 a Workflow release with no per-release contract is admitted by
capability: its installation record lists `scripts/workflow_protocol.py`
and `describe` answers protocol major 1 (2.7.0 does;
[compatibility](../compatibility.md)).
For such a target the Controller keeps no lifecycle rule of its own. The
Workflow's `next-action` says what to do next, and its `reconcile` says
whether a job made progress. 2.5.1 and 2.6.0 stay in legacy mode, exactly
as described above. The design record is
[ADR 0010](../adr/0010-orchestration-protocol-admission-by-capability.md).

**How an operation runs.** `describe`, `next-action` and `reconcile` run
the way the queries above do: from a private copy of the Workflow's
script set, with the Controller's own interpreter, under the same Git
isolation and its refusals, and within `timeouts.workflow_query_seconds`.
The script set is every `scripts/<name>.py` the installation record's
`managed` map lists (a `*_test.py` excepted), and its digests are derived
afresh for every call; a file in `scripts/` that the map does not list is
never copied. Every answer is validated against the protocol schema the
Controller vendors (`controller/protocol_schema.json`). An operation that
fails, or an answer that does not validate, is `WORKFLOW_PROTOCOL_FAILED`;
an answer for a protocol major the Controller does not speak is
`WORKFLOW_PROTOCOL_UNSUPPORTED`. Both exit `20` at a decision, and nothing
is launched.

**The protocol preflight.** Every step, and `explain`, first runs the
Workflow's `verify`. An unhealthy answer is the `workflow_unhealthy` gate,
naming each failing check and its detail; nothing is decided or launched.

**Decisions.** `next-action`'s disposition becomes the Controller's
decision:

| Disposition | Decision |
|---|---|
| `automatic` | a launch, when the action id is one the Controller knows (twelve, from `plan.start` to `functional.apply_findings`), its worker is not `user_only` and its worker role is known; otherwise a blocked gate (`workflow_unknown_action`, `workflow_unknown_worker_role`, `workflow_user_only_action`) |
| `human_gate`, `external_gate` | a gate carrying the Workflow's reason, remedy and alternatives |
| `blocked` | a gate carrying the Workflow's reason code, text and remedy |
| `complete` | nothing to do |
| `validation`, or any other | a blocked gate (`workflow_unknown_disposition`) |

The worker's command is rendered by the Controller from the action id and
the work item (`/<command> <work_item_id>`; `plan.start` adds the trunk
base, as for a target with no work item). The Workflow's own
`invocation` text is only compared: when it differs, nothing is launched
and the gate is `workflow_invocation_mismatch`, a Workflow release defect
to report. At `IMPLEMENTING` and `SELF_REVIEWING_IMPLEMENTATION` the
committed-state gate of legacy mode still applies before a launch.
`explain` shows the Workflow's row, disposition and action, and with
`--json` a `protocol` block; a legacy target's output is unchanged.
`inspect` lists, as an advisory, any action id the Workflow's `describe`
reports that this Controller release does not know (`unknown_action_ids`
with `--json`). Such an id is never a refusal; it is blocked
(`workflow_unknown_action`) only if it becomes the next action.

**Before a launch.** A decision is checked again against the Workflow
before the job is recorded and once more immediately before the worker
starts (`next-action --expect-state-identity`; for `plan.start`, the same
answer and the same work items). A stale answer is discarded and the step
decides again, at most three times; then it stops at the
`decision_unstable` gate. A decision found stale immediately before the
spawn leaves a `FAILED` job with `reconciliation_evidence.code`
`decision_stale_at_launch`: no worker started, nothing is pending, and the
next step decides again. When the last two jobs for the same work item and
action both ended with no progress, a third is not launched: the step
stops at the `no_progress_repeated` gate.

**Outcomes.** A protocol job is judged by the Workflow's `reconcile` on the
decision it was launched for (kept as `jobs/<job_id>/decision.json`), both
when its worker ends and on `resume`. The job record's `protocol` block
keeps the decision, its digest, the release and script digests it was
decided under, and `reconcile`'s answer; `status` shows the reconcile
class and any invalid reasons on the job's line. In order:

| What is found | Job |
|---|---|
| the release, or a managed script's digest, differs from the one the job was decided under | `FAILED`, `workflow_release_changed`; `reconcile` is not run |
| the worker failed or timed out | `FAILED`, `worker_outcome`; `reconcile` is not run |
| `reconcile` fails, or answers a class the Controller does not know | `FAILED`, `workflow_protocol_failed` |
| `invalid` | `FAILED`, `reconcile_invalid`, with the Workflow's reasons verbatim |
| `progress` for `implementation.checkpoint`, with a completion the working tree records but `HEAD` does not | `FAILED`, `completion_not_committed_at_head` |
| `progress`, `gate_reached` | `FINISHED` |
| `no_progress` from a worker that succeeded | `FINISHED`, with `progress: "none"`; no `resume` is needed |
| `no_progress` from an interrupted worker, or one whose end was not recorded | `INTERRUPTED` |

## Implementation-review apply rounds

`/apply-implementation-review` at a `"2.2"` `APPLYING_REVIEW_FEEDBACK`
has three safeguards:

- **The pending-write addendum.** After an automated local `REVISE`, or
  an ingested manual `REVISE`, the review-stage write that moved the work
  item to `APPLYING_REVIEW_FEEDBACK` is normally still uncommitted. A
  worker that followed the command text literally would then land a
  malformed generation-record commit. So, only while `HEAD` does not yet
  record `APPLYING_REVIEW_FEEDBACK`, the worker's task is the command
  followed by a pinned Controller note. The note authorizes exactly one
  extra act: commit that pending state write alone, with a
  `Workflow-Work-Item` trailer, before any other commit. It names no
  user-only command. `explain` prints it, and the job record keeps it as
  `selected_action.task_addendum`. This is the only place a worker's task
  is not the bare selected command.
- **The malformed-`T` gate.** If a worker lands the generation-record
  commit `T` anyway, with no phase change in its own diff,
  the next decision names that commit and says that no Workflow command
  repairs it. A human repairs the unpushed history so the pending write
  lands alone before `T`, then reruns the command's step 7. The
  Controller does not offer the ordinary regeneration steps there,
  because the generator would refuse them.
- **The relaunch bound.** An `/apply-implementation-review` attempt that
  did not verify is never relaunched automatically against the bundle it
  started from. Once such an attempt passed its own step 4, the bundle no
  longer matches its manifest, so every retry would refuse at step 1. The
  gate names the earlier job and the human's two ways on: restore the
  edited bundle file from `review-bundle.tar.gz` and rerun the command in
  a supervised session, or complete the round by hand. A new bundle
  generation lifts the bound.
