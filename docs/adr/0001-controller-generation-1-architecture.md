# ADR 0001: Workflow Controller, Generation 1 -- architecture and interface

Status: accepted. See `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md` for the
full design record (seventy-one plan revisions, seventy local review
rounds, and manual external rounds at 31, 67 and 71). This document
records the decisions
most likely to be revisited by Generation 2, and carries the one table
that is normative rather than descriptive: the exit-code contract below.

Amended by `workflow-controller-automatic-lifecycle-orchestration`
(`docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md`):
the scope paragraph under "Context", the exit-code table (code 45, and
code 15's meaning), "Automatic-dispatch rule" and "`resume` and job
dispositions".

## Context

The Controller automates operation of the Workflow (frozen v2.5.1 --
`controller.managed_repo.VALIDATED_WORKFLOW_RELEASES` is the admission
gate, and a target on any other release is refused). Admission widened to
2.6.0 by [ADR 0006](0006-workflow-release-admission-and-per-release-contracts.md) (1.3.0). It
sits between the Workflow Manager (which owns install/update/verify/drift
for a managed repository) and a managed development repository's own
Workflow lifecycle:

```
Workflow  ->  Workflow Manager / Bootstrapper  ->  Workflow Controller  ->  managed development repositories
```

Two structural invariants run through the whole design:

1. **The Controller is a reader of Workflow state and a launcher of
   workers. It is never a writer of Workflow state.** Every durable
   Workflow lifecycle change is made by a *worker* running a real
   Workflow command in the target repository -- never by the Controller
   editing `WORKFLOW_STATE.json` directly.
2. **The Controller never crosses a human gate.** The user-only denylist
   is *derived* from the installed `.claude/commands/*.md`, as the union
   of two recognisers -- the qualified
   `workflow_state.validate_...confirmation` guard literal, and the
   front-matter `disable-model-invocation: true` flag -- never from a
   hand-written name list. At the 2.5.1 reference release that union is
   four commands: `/approve-review`, `/accept-milestone`,
   `/recover-implementation-provenance` (guard only) and
   `/request-plan-amendment` (flag only). None is ever selected or
   executed by the Controller. Neither recogniser alone is total: each
   misses exactly one of the last two, which is why the rule is the
   union.

Generation 1's original scope (`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`,
"Scope reassessment (revision 10)") was narrower than "automate every
phase": it executed automatic actions only at the plan-stage phases
(`PLANNING`, `REVISING_PLAN`, `AWAITING_LOCAL_PLAN_REVIEW`,
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`/`AWAITING_EXTERNAL_PLAN_REVIEW`),
and at every other phase it observed, decided and reported. That was a
deliberate narrowing, not an oversight: nine consecutive local plan-review
rounds found Blocking defects exclusively in the automatic execution of
implementation- and functional-stage commands, and none in the plan-stage
ones.

The work item `workflow-controller-automatic-lifecycle-orchestration`
(`docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md`)
widened that scope within Generation 1 (`controller/GENERATION.json` stays
`1`). It did so by replacing the phase-specific report-only sets with one
rule, below, and by giving every newly automated implementation-stage
action a verifiable expected outcome with artifact postconditions. The
functional-review stage, `AMENDING_PLAN` and `"2.1"` external-verdict
consumption are still reported, never launched.

## Decisions Generation 2 is most likely to revisit

**Exit code as the Workflow Manager's authoritative verdict.** The
Controller never reimplements Workflow Manager's own install/verify/drift
semantics. `managed_repo.inspect()` takes `workflow-manager verify`'s and
`... status`'s exit codes as the sole admission signal for a target
repository -- never `status`'s stdout prose, and never `status`'s exit
code alone (it exits `0` even on an *unmanaged* repository, so on its own
it carries no admission signal). A future generation that wants richer
drift diagnostics should extend the Manager's own contract, not add a
second, parallel inspection path in the Controller.

**The four-outcome worker classification, including `AMBIGUOUS`.**
`controller.worker.launch()` classifies a completed worker run into
`SUCCESS`, `FAILURE`, `AMBIGUOUS` or `INTERRUPTED` rather than a bare
return code: `SUCCESS` requires both a zero exit code and a parsed final
JSON message with no `is_error: true`; a parsed `is_error: true` or a
non-zero exit is `FAILURE`; a worker that exits `0` but whose stdout does
not parse as the expected structured result is `AMBIGUOUS` -- a signal
that a human should read the transcript, never silently folded into
`SUCCESS` or `FAILURE`. `AMBIGUOUS` exists because a `0` exit code is
necessary but not sufficient evidence that the requested Workflow command
actually ran to completion.

**Persist-before-launch as the basis of durable resume.** Every job record
is flushed to disk in two stages -- `PLANNED` (decision made, nothing
launched yet) then `LAUNCHED` (the worker is about to be spawned) --
*before* `controller.worker.launch()` is ever called. `controller.job.resume()`
reconciles a record left in a non-terminal state against authoritative
Workflow/Git state precisely because both flushes are durable ahead of the
launch: a crash at any point has a durable record to reconcile against,
and `resume()` never relaunches a worker -- it contains no call to
`worker.launch` at all.

**Generation-number comparison against the source repository's committed
`HEAD` as the handoff trigger, with both halves reading that same `HEAD`.**
The running generation is pinned once, from the origin Controller source
repository's committed `HEAD` at process start (never the worktree, and
never re-resolved for the life of the process). The *approved* generation
is resolved the same way, fresh, at every orchestration boundary:
`git -C <origin_source_root> show HEAD:controller/GENERATION.json`. Reading
both numbers from the same coordinate system (`HEAD`, never the worktree)
is what keeps an ordinary uncommitted edit to `GENERATION.json` from ever
being misread as an approved generation bump, and is what makes "equal
generation, different commit" (ordinary in-generation development) a
distinguishable, non-handoff outcome from "approved generation is greater"
(a real handoff).

## Exit codes

The CLI is designed to be driven by an outer supervisor, so its exit codes
are part of its contract. This is the copy an outer supervisor's author
should read to write a `case` statement; `README.md` points here rather
than repeating it. `tests/test_plan_document_consistency.py` binds this
table two ways. Its codes must equal the values of `controller.cli`'s
`EXIT_*` constants, in both directions, one row per constant. And every
row of the completed Generation 1 plan's own table
(`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`, "Exit codes") must still be
here, unchanged, with one named exception: code 15's meaning, which
`workflow-controller-automatic-lifecycle-orchestration` reworded.

| Code | Meaning |
|---|---|
| 0 | requested work completed; nothing is pending |
| 2 | CLI usage error (argparse default) |
| 10 | stopped cleanly at a human gate — the normal, expected outcome for a supervised run |
| 15 | no verifiable expected outcome is declared for the selected action — it is model-invocable and not user-only, but it is reported (declined) instead of launched |
| 16 | `run` hit `--max-steps` with work still outstanding |
| 17 | stopped before starting because of the usage budget; nothing was started |
| 20 | fail-closed refusal (`ControllerError`) — unmanaged/drifted repo, malformed state, unsupported phase |
| 30 | a worker ran and failed |
| 35 | a worker ran and stopped without completing its action — it refused for a stated reason, or was cut short — leaving no durable Workflow change to verify |
| 40 | the Controller itself was interrupted, or `resume` reconciled a record to `INTERRUPTED` |
| 45 | the target worktree is held — another Controller or a previous worker holds its lifecycle lock, or a recorded worker may still be running — so nothing was launched, reconciled or abandoned |
| 50 | a generation handoff is pending; the running generation stopped intentionally |

Exit 10 and exit 50 are successes of the design, not errors: conflating
them with 0 would let an outer supervisor mistake "waiting for a human"
for "finished"; conflating them with 20 would make normal operation look
like failure.

Exit 45 is a wait, not a failure. It comes from `step`/`run` (the lock is
held), from `resume` (the lock is held, or it left a record `LAUNCHED`
because that record's worker is `active` or `unverifiable`) and from
`resume --abandon` (the lock is held, or the recorded worker is `active`,
or `unverifiable` without `--acknowledge-unverifiable-worker`). Its message
names what holds the worktree and what ends the hold. The refusals this
work item added that are not a held worktree exit 20 through the blanket
`ControllerError` row: a pending, unreconciled job file
(`PendingJobReconciliationError`), a lock that cannot be taken for any
reason other than contention (`LifecycleLockError`,
`GitDirectoryUnresolvableError`), a malformed `--routing-config` file
(`RoutingConfigError`) and a refused `--abandon`
(`JobAbandonRefusedError`).

## Automatic-dispatch rule

Added by `workflow-controller-automatic-lifecycle-orchestration`.
`controller.evidence.decide` stays the single decision entry point: each
phase handler either selects the next action from evidence or returns a
gate. Whether a selected action is *launched* is then decided in one place,
for every phase alike (`controller.decision.classify_selected_action`,
applied by `apply_dispatch_rule`):

- **automatic** iff `(phase, governing_workflow_version, command token)` is
  a member of `controller.decision.AUTOMATIC_TRIPLES`;
- **declined** (exit 15) otherwise, with one uniform reason naming the
  phase, the command and the missing `(phase, version, command)` triple.

`AUTOMATIC_TRIPLES` is a literal copy of the keys of
`controller.job.EXPECTED_OUTCOMES`, held equal to them in both directions
by a test. So "is launched" and "has a declared, verifiable expected
outcome" cannot drift apart: automating another action means adding one
`ExpectedOutcome` row and its triple together. The phase-specific
report-only and declined sets the original Generation 1 carried are gone.

An expected outcome is verified against durable Workflow and Git state,
never against the worker's own report. A phase transition alone is never
success. Each implementation-stage row also checks the artifact
postconditions it promises: the checkpoint completion committed at
`HEAD`, a coherent implementation bundle whose `generation_head` is the
live `HEAD`, or a review ledger bound to the manifest's
`review_content_id`. A stale, withdrawn, `REJECTED` or mis-bound artifact
fails the job.

At the Workflow 2.5.1 reference release, the automatic set is:

- the plan stage, unchanged at every reachable `(phase, version)`;
- `IMPLEMENTING` and `SELF_REVIEWING_IMPLEMENTATION` ->
  `/milestone-implement`, at `"2.1"`/`"2.2"`, and only once the plan
  approval is `CURRENT` (otherwise a gate);
- at `"2.2"` only: `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` ->
  `/review-implementation`; `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
  -> `/record-manual-implementation-review`, only for an already-pasted,
  admissible manual `APPROVE`/`REVISE`; and `APPLYING_REVIEW_FEEDBACK` ->
  `/apply-implementation-review`, for an admissible `REVISE`.

Everything else is a gate or a decline. That includes performing the
manual external review, `/approve-review`, `/accept-milestone`, the
functional-review stage, `AMENDING_PLAN`, and consuming a `"2.1"`
external verdict. No triple names a user-only command, and the worker
layer refuses one independently even if handed it.

## `resume` and job dispositions

Also added by `workflow-controller-automatic-lifecycle-orchestration`.
Persist-before-launch (above) still holds. Every job this version launches
also runs under a per-worktree lifecycle lock: an exclusive `flock` on the
target worktree's own git directory, which the worker inherits. The
`PLANNED` record names that lock (`lifecycle_lock`), and the worker's
process identity (`worker_process`) is flushed into the `LAUNCHED` record
as soon as the worker is spawned. The disposition rule:

- `step`/`run` refuse (exit 20) while any job file for the target is
  pending reconciliation, so a `LAUNCHED` record left by a Controller that
  died is never silently replaced by a second worker.
- `resume` reconciles each pending record from evidence, under the lock. It
  leaves a `LAUNCHED` record alone, and exits 45, while that record's
  recorded worker is `active` or `unverifiable`.
- When `resume` cannot reconcile a record carrying `lifecycle_lock`, that
  record's worker has definitively ended: `resume` holds the lock, and the
  recorded worker is not running. So `resume` persists the record
  `FAILED` (`UnreconcilableJobError`) before it exits 20, and the next
  `step` decides from evidence.
- Every other pending job file `resume` cannot reconcile is disposed of by
  the operator, with `resume --abandon JOB_ID`. That covers a record
  written before this version, one failing `validate_record`, an unknown
  status, an unparseable file, and a record whose worker is
  `unverifiable`. It is marked `FAILED` (`OperatorAbandoned`) in place,
  or, when no field of it can be trusted, its bytes are set aside under
  `jobs/abandoned/` and it is replaced by a minimal terminal record. A
  record from another or unidentifiable host, from another pid namespace,
  or with no boot identity and a recorded group that may be live, is
  `unverifiable`. It needs `--acknowledge-unverifiable-worker`, the
  operator's statement that its worker is gone. Nothing overrides a held
  lock, or a running member of the recorded group observed in this boot
  and pid namespace.
- A launch that never started a worker is `FAILED` (`WorkerNotStarted`) at
  once, and needs no `resume`.
- Time is never termination. There is no default worker timeout;
  `--timeout` is an explicit opt-in.

`resume` still never launches a worker, and `--abandon` launches nothing
and writes only the Controller's own runtime. Two kinds of `jobs/` entry
are cleared by neither, only by hand. An entry that is not a regular file
(a symlink, directory, FIFO or other special file) is never opened or
replaced, and every refusal names it for removal by hand. A regular file
the Controller cannot read has to be made readable again, or removed.

## No hot reload

A running Controller generation must never mutate or reload the
implementation it is currently executing. This is enforced four ways:
the running process's own package root is an immutable, content-addressed
snapshot under `<runtime root>/source/<tree_digest>/`, so a late import or
resource read resolves inside bytes the origin worktree cannot reach;
`controller.identity.current()` returns a cached singleton and never
re-resolves; a structural source-scan test asserts no reload primitive
(`importlib.reload`, `imp.reload`, and their aliases) appears anywhere in
`controller/`; and a live end-to-end subprocess test proves the property
holds under a real generation bump while a run is paused mid-loop.

Generation 1 does not autonomously spawn Generation 2. A pending handoff
is a durable record plus an intentional stop (exit 50); starting the next
generation is a human act, and the handoff record carries the exact
command.
