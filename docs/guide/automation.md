# What the Controller automates, and how it stays safe

> For: anyone who wants to know what the Controller launches by itself and how it stays safe. Last checked with: Controller 1.7.1; Workflow 2.6.0, 2.7.0, 2.8.0 and 2.9.0.

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
  intentional stop (a durable handoff record, exit 50; every status is in [exit codes](../exit-codes.md)), never an
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

## Scenario evidence

The Controller does not run the lifecycle scenarios; they are test-side
(`tools/run_scenarios.py`, see [Lifecycle scenarios](development.md#lifecycle-scenarios)).
They run whole lifecycle paths against fake workers in a sandbox and write
`functional_evidence` records bound to a commit. A `passed` record means a
scripted path ran on that commit, not that a real worker behaves; a later
step lets the gate policy accept a milestone on such records.

## Notifications from an unattended run

From 1.9.0 the Controller is a producer for SignalHub, so a run you are not
watching tells you what it is doing and, above all, when it is blocked. It
sends from records it already keeps (a finished job, a run that closed, a
branch gate, a line of `usage-events.jsonl`), never from a separate source,
and the events are the closed table in
[`notifications`](commands.md#notifications). The ones that need you are
the `user_gate` (a human gate, or a merge left to a person) and
`lane_blocked` (a failed job, a refusal, a gate that needs a fix); by default
they are the highest severities. A stop the Workflow itself answers with
the disposition `blocked` is a `lane_blocked` ("Blocked: <work item>"), not a
`user_gate`, because no person's decision is asked for. The exception is a
gate the step records through its fall-through, with a `protocol_gate` and no
`protocol` block: that one stays a `user_gate`, as it was before `review_wait`
existed. Any other human gate (an
approval, an acceptance, a merge left to a person, a BLOCK-resolution gate)
stays a `user_gate`. A wait that needs no decision from you sends
`review_wait` (`INFO`/`LOW`, "Waiting for the external review: <work item>"):
the wait for an external reviewer's verdict on a Workflow-governed target (a
legacy target keeps `user_gate` for every gate at those phases) and the
functional tester's fault stops, each titled "Waiting: <stop>
(<work item>)", except `isolation_breach`, whose text says a person must look
at what changed, and which stays a `user_gate`. A wait whose `user_gate` an
earlier version already delivered is not announced again. A finished step, a
usage pause and a run that closed are `INFO`/`LOW`.

To set it up:

1. Give the Controller the server address and a key. Either export
   `SIGNALHUB_URL` and `SIGNALHUB_API_KEY_FILE` (or `SIGNALHUB_API_KEY`) in
   the environment the run starts in, or set `notifications.url` and
   `notifications.api_key_file` in the settings file once, which every launch
   path then finds. The key itself is never stored in the settings file or
   the repository.
2. Run `workflow-controller notifications` to see what was found, and
   `workflow-controller notifications --test` to send one test event.
3. Turn off or re-rank what you do not want in `notifications.events`
   ([the notifications section](runtime.md#the-notifications-section)).

Sending never steers the lifecycle. It happens on a background thread, is
bounded in time and attempts, and a notice that cannot be delivered is
recorded in `runs/<run_id>/notifications.jsonl` and nothing else: the step
is not blocked, failed or delayed, and a repeat of the same occurrence (a
retry, a `resume`) carries the same `Idempotency-Key` and does not notify
twice. There is no outbox that retries later. `resume` derives again the
notice of a job whose record outlived the process that was to send it, if
that job ended within the last 30 minutes and its run log shows the notice
never left; a notice lost between a close-out and the queue (the
`pr_merged` of a merge seen by a process that then died) is not recovered.
A watchdog that restarts after a killed process should run `resume` before
`run`. Why it is built this way is in
[ADR 0012](../adr/0012-signalhub-notifications.md).

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

In protocol mode the Controller itself launches `/prepare-functional-review`
(which writes and commits the checklist) and `/apply-functional-review`
(which applies findings) without a prompt. Under an automatic acceptance gate it also
runs the functional testing itself (see
"The automated functional review" in [Protocol mode](#protocol-mode-workflow-27-and-later)); under a human gate,
or with `functional.automated` false, the testing stays with a person.

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
| `automatic` | a launch, when the action id is one the Controller knows (thirteen, from `plan.start` to `pr.apply_review`), its worker is not `user_only` and its worker role is known; otherwise a blocked gate (`workflow_unknown_action`, `workflow_unknown_worker_role`, `workflow_user_only_action`) |
| `validation` | a launch of `/satisfy-gate <stage> <work item>` on the `satisfy-gate` role (`satisfy-gate-acceptance` for `acceptance.satisfy`), when the action id is `plan.satisfy`, `implementation.satisfy` or `acceptance.satisfy`, its worker is not `user_only` and its role is `validator`; any other id, worker or pairing of id and disposition is blocked as above (`workflow_unknown_action`, `workflow_unknown_worker_role`, `workflow_user_only_action`) |
| `human_gate`, `external_gate` | a gate carrying the Workflow's reason, remedy and alternatives |
| `blocked` | a gate carrying the Workflow's reason code, text and remedy |
| `complete` | nothing to do |
| any other | a blocked gate (`workflow_unknown_disposition`) |

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
reports that this Controller release cannot launch (`unknown_action_ids`
with `--json`): an invented id, or the catalogue id it has no
command for (no catalogue id is left without one in this release). Such an id is never a refusal; it is blocked only if it
becomes the next action, as `workflow_unknown_action`.

**Automatic gates.** A `validation` answer runs the Workflow's own
`/satisfy-gate`; the Controller reads no `GATE_POLICY.json` and checks no
requirement itself. When every requirement is met the phase moves, and the
job sends `approval_recorded` (and `milestone_accepted` at acceptance)
only because the phase moved. When a requirement is unmet nothing is written
and the phase is unchanged: the step ends with no progress and sends only
`step_done`, `explain` shows the unmet requirement from the worker's report,
and the loop guard (`no_progress_repeated`) lets two such steps run and blocks
the third. A gate that needs evidence the Controller cannot produce stays a stop. The
functional evidence of row 38e is produced by the Controller itself under an automatic acceptance
gate (next section); it stays a stop under a human gate and with `functional.automated` false.

**The pull request defect loop.** Under an automatic acceptance gate the Controller
closes the pull request rows itself:

- At row 38f (`pr_evidence_needed`) or 38g (`pr_approval_needed`), for a work item with a
  milestone-branch binding and a pull request, the Controller runs the Workflow's one fixed
  `gh pr list` query for the item's anchor commit and hands the verbatim output to the Workflow
  with `record-external-result --kind pr_review_result`. It parses nothing: the Workflow reads
  the result, and the reported fact only arms the Workflow's own query.
- At row 38f, while the checks are pending and `ci_green` is the one thing unmet, with no
  `CHANGES_REQUESTED` decision and no failing check, the step reports nothing and waits
  (`pr_checks_pending`, polled by `run` within the `merge.wait_seconds` budget at the
  `merge.poll_seconds` interval; `step` never waits, so it is a stop that names the pending
  checks, or "no checks reported" when the pull request has none). A pending rollup never hides an
  approval (row 38g) or an objection.
- A result equal to the last one the Controller sent is not sent again: the step stops
  `pr_report_unchanged` ("reported; the report changed nothing"). An operator waiting on a person's
  approval may see one refused `/satisfy-gate acceptance` worker after such a report, because the
  Workflow then answers row 38h before it returns to 38g.
- Row 38d (`pr.apply_review`, `pr_query_due` or `pr_review_actionable`) is launched like any
  automatic action: `/apply-pr-review <id>` on the `apply-pr-review` role (Opus, `xhigh`,
  overridable in the routing file and the settings file's role names). The Workflow's command
  chooses the branch: it refreshes the fact and lets acceptance pass, or reopens the work item
  into remediation for a red pull request, a `CHANGES_REQUESTED` decision or a changed head.
  The Controller reads no fact and chooses no branch.

A person is asked only where one is needed: row 38g with the approval still missing, row 38g under
a human acceptance gate (`/accept-milestone`; the Controller reports nothing there), a stop the
Controller itself ends in (`pr_report_no_pull_request`, `pr_report_forge_unavailable`,
`pr_report_refused`, `pr_report_stale`), and the functional review's own faults (below). Pending checks are never a decision for a
person: a step that did not wait ends on `pr_checks_pending` with no notification, and a `run` whose
`merge.wait_seconds` budget ran out with only the checks pending sends `checks_wait` (`INFO`/`LOW`, "Waiting for the pull request's checks (<work item>)"); so does a stop in a repository without auto-merge. Either
sends `lane_blocked` only when the checks have been pending over four hours, counted from the last change the Controller
recorded on the binding (a push, or the pull request marked ready or edited). A `lane_blocked` an earlier version delivered
for the same stop is not followed by a `checks_wait`. A red pull request found after acceptance, in
the merge flow, still ends at that flow's `checks_failing` stop, with a person (roadmap step C10d).

**The automated functional review.** At row 38e (`functional.evidence.external`, the Workflow
wants `functional_evidence` for the head) under an **automatic** acceptance gate, and with
`functional.automated` true (the default), the Controller runs the functional testing itself instead
of stopping. Rows 37 (no current checklist) and 38 (unconsumed findings) come first, as before: the
Workflow answers them first and the Controller launches `/prepare-functional-review` and
`/apply-functional-review`. At row 38e the step:

1. reads the checklist (the "Functional review checklist" section of `docs/ACTIVE_MILESTONE.md` at
   the head) and the flows it lists. The recognised shapes are table rows whose first cell is a
   short label, bold labelled paragraphs (`**A. ...`), headings that start with a label or `Flow`,
   and a numbered list; flow ids are the Controller's (`flow-1`, ...). A checklist with none stops
   at `checklist_unrecognised` before any job, sandbox or usage reservation exists;
2. builds a **sandbox** under `<runtime root>/functional/<job_id>/`: a `git clone --no-hardlinks`
   of the item's branch at its head, whose `origin` is a scratch bare repository; a `bin/` first on
   `PATH` with a fake `gh`, `claude`, `codex` and `ssh` (each logs its arguments and refuses);
   `GIT_ALLOW_PROTOCOL=file` and `GIT_SSH_COMMAND=false`; scratch `HOME` and `XDG_*`; a private `TMPDIR`
   (mode 0700, named `wcf-*`) under `$XDG_RUNTIME_DIR` or `/tmp` rather than inside the sandbox folder,
   because Claude's sandbox puts its bridge sockets there and a Unix socket path may not exceed 107
   bytes (counted as the filesystem encodes the path), which the default runtime folder does (the
   Controller records the folder's path in the sandbox, so it removes it with the sandbox whatever
   `XDG_RUNTIME_DIR` is later, and removes it at once if recording it fails, and
   stops at `functional_tester_unavailable` before launch if no folder is short enough); and no other
   tool on `PATH`;
3. starts an independent tester, a `functional_test` job on the `functional-tester` role (Sonnet 5.5,
   `medium`, one agent, `Bash` only), inside Claude Code's own shell sandbox (bubblewrap, `socat`,
   a kernel that accepts a seccomp filter and the native `claude` build are required -- the build check is by identity: the `claude` that would run must be an ELF whose SHA-256 digest is in `VERIFIED_NATIVE_BUILDS` (`controller/functional_review.py`), the builds on which the enforcement spike proved the seccomp helper blocks Unix sockets, so a launcher, script, package-CLI bundle or a newer build that is neither in that list nor recorded by `workflow-controller verify-claude` ends at `functional_tester_unavailable` before any launch (the command runs the live enforcement probes on the build and records its digest in the runtime root, [`verify-claude`](commands.md#verify-claude)); the probe and the launch examine and execute the same resolved real path; the writable locations are the output folder, the scratch directories and the
   locations derived from the repository's `.gitignore`, which live in `<sandbox>/writable/<name>/` outside the clone while the clone holds a
   link to each (so the settings never allow a write below a denied path, whichever precedence a Claude build applies, and Claude Code 2.1.304 and later can be verified; a tool that deletes and recreates a linked folder is refused, an `environment` fault, not a product `defect`); no network and no Unix socket; the
   product files, the checklist and `.git` are read-only). It writes `RESULTS.json`, one entry per
   flow, each with a log file and, for a failure, the reason `defect` (the product did not behave as
   the flow expects) or `environment` (the flow could not run in the sandbox);
4. checks that the real repository, its origin and the job's private Claude home are unchanged and
   that the clone still matches the candidate the Controller captured, then records **one
   `functional_evidence` result per flow** at the sandbox head with `record-external-result`
   (`reporter` `workflow-controller <version>`, `run_ref` `controller:<job_id>`).

The outcomes: `recorded` (every flow passed; the next decision is the Workflow's, normally
acceptance), `findings` (a `defect` flow failed: the Controller writes the findings file at the path
the Workflow names, under a header that names the tested head, checklist and run, so a retest with the
identical failure text is not skipped as already applied; the next decision is row 38 and the
`/apply-functional-review` worker, then a new technical approval and a fresh tester run at the new
head), `environment_limited` (every failed flow is `environment`: the results are recorded, no
findings are written and the applier is not started) and `unchanged` (the Controller already tested
this head and recorded every flow, yet the Workflow still needs evidence, for example a policy's
`required_flows` the checklist does not cover: one `functional_unchanged` stop naming the Workflow's
detail, no repeated run).

The stops, each with its remedy in the message: `flows_not_runnable` (the `environment_limited`
case: run those flows by hand and record them, or change the checklist so they run offline),
`checklist_unrecognised`, `functional_tester_unavailable` (no `claude` binary, the role refused, `bwrap` or `socat`
missing, no seccomp filter, a `claude` build not yet verified (run `workflow-controller verify-claude`), the fresh clone not matching the manifest, or no
folder short enough for the tester's `TMPDIR`; each cause with its remedy is in
[common problems](../common-problems.md#functional_tester_unavailable)), `results_invalid` (the stop text carries a bounded excerpt of the tester's final message,
which usually names the cause when `RESULTS.json` was not written), `isolation_breach` (a difference in the real repository, its path
origin or the private Claude home; the sandbox is kept for inspection and nothing is recorded; a
person must look before anything is tested again), `tested_bytes_unbound` (the clone no longer
matches the candidate after the run; nothing is recorded), `recording_refused` (the Workflow
refused a flow's result; results accepted before it stand) and `functional_rounds_exhausted` (the
tester has ended in `findings` three times for the work item; the count is read from job records
and nothing resets it). `functional_unchanged` sends `lane_blocked`, `isolation_breach` sends `user_gate`, and every other
one, `functional_rounds_exhausted` included, sends `review_wait` (`INFO`/`LOW`, see
[notifications](#notifications-from-an-unattended-run)). The remedy of the others is to test the head by hand
and record the evidence as before, or to set `functional.automated` false, which restores the old
stop at row 38e for the repository so the tester is not launched again. A usage limit is handled
like any job's.

**What the sandbox does not claim.** The Controller binds the candidate it handed over and keeps it
read-only to the tester, but it cannot bind which copy of the product the tester ran: a copy edited
under the tester's own scratch `TMPDIR` that passes is not detected (the prompt forbids it, and the
record names the digest, reporter and run reference for an audit). The tester is a model, and the
Workflow trusts reported evidence, as it does a person's. The sandbox has no network, no real GitHub,
no real model and no tool outside `PATH`; a pull-request flow therefore **cannot run in the sandbox**
(the Controller's `controller/forge.py` and the Workflow's `scripts/workflow_forge.py` keep
refusing a scratch `gh` and a scratch origin, and neither is relaxed): such a flow is `environment`
and ends at `flows_not_runnable` until a person runs it. A Codex tester does not exist yet (the Codex
adapter declares only the reviewer role). `explain` at row 38e shows, read-only, the head, the
checklist blob, the flow ids, the previous functional job, the `.gitignore` patterns the writable-location derivation
refused (each with its reason) and any sandbox kept by an earlier breach.
The design record is [ADR 0017](../adr/0017-automated-functional-review.md).

**A protocol minor release.** A value a 1.x minor release adds to a closed enum
of the answer is never refused as outside the protocol. `protocol_schema.ENUM_RULES`
gives each of the twenty enum locations a rule: `identity` (the protocol name:
only a major change refuses), `advisory` (shown, changes nothing), `blocked` (a
decision depends on it, so the step stops naming the value and launches nothing) or
`ignored` (the Controller reads no decision from it). An unlisted action id, worker
role or disposition is a blocked gate before any launch; an unlisted reconcile class
or record stage, which arrives after the worker ran, ends the job `FAILED`
(`workflow_protocol_failed`) with the evidence reason `workflow_unknown_value`,
naming the field and the value.

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
stops at the `no_progress_repeated` gate. After even one such job, `explain`
shows what that worker reported (JSON: `last_worker_report`), so a refused
`/satisfy-gate` names the requirement it found unmet before the gate applies. Once the
`no_progress_repeated` gate applies, `explain --json` has no `last_worker_report`: the
report is in `gate.what_is_required` instead.

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
| `reconcile` fails, or answers a class the Controller does not know | `FAILED`, `workflow_protocol_failed` (for an unknown class, the evidence reason `workflow_unknown_value` names it) |
| `invalid` | `FAILED`, `reconcile_invalid`, with the Workflow's reasons verbatim |
| `progress` for `implementation.checkpoint`, with a completion the working tree records but `HEAD` does not | `FAILED`, `completion_not_committed_at_head` |
| `progress`, `gate_reached` | `FINISHED` |
| `no_progress` from a worker that succeeded | `FINISHED`, with `progress: "none"`; no `resume` is needed |
| `no_progress` from an interrupted worker, or one whose end was not recorded | `INTERRUPTED` |

## Implementation-review apply rounds

In legacy mode (Workflow 2.5.1 and 2.6.0), where the Controller decides
itself, `/apply-implementation-review` at a `"2.2"` `APPLYING_REVIEW_FEEDBACK`
has three safeguards. In protocol mode the Workflow decides, the worker's
task is the bare command and `reconcile` judges the outcome, so none of them
applies; from Workflow 2.9.1 the review and apply commands also commit a
`REVISE` state write themselves. The legacy safeguards:

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
