# ADR 0001: Workflow Controller, Generation 1 -- architecture and interface

Status: accepted. See `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md` for the
full design record (sixty-two plan revisions, sixty-one local review
rounds and a manual external round). This document records the decisions
most likely to be revisited by Generation 2, and carries the one table
that is normative rather than descriptive: the exit-code contract below.

## Context

The Controller automates operation of the Workflow (frozen v2.3.1). It
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
2. **The Controller never crosses a human gate.** `/approve-review` and
   `/accept-milestone` carry `disable-model-invocation: true` and are
   never selected or executed by the Controller.

Generation 1's own scope (`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`,
"Scope reassessment (revision 10)") is narrower than "automate every
phase": it executes automatic actions only at the plan-stage phases
(`PLANNING`, `REVISING_PLAN`, `AWAITING_LOCAL_PLAN_REVIEW`,
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`/`AWAITING_EXTERNAL_PLAN_REVIEW`).
At every other phase it observes, decides and reports -- it never launches
a worker there. This is a deliberate narrowing, not an oversight: nine
consecutive local plan-review rounds found Blocking defects exclusively in
the automatic execution of implementation- and functional-stage commands,
and none in the plan-stage ones.

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
are part of its contract and are tested against the live parser and
against this table (`tests/test_plan_document_consistency.py`). This is
the copy an outer supervisor's author should read to write a `case`
statement; `README.md` points here rather than repeating it, since
`README.md` is not bound by `technical_approval` and this file is.

| Code | Meaning |
|---|---|
| 0 | requested work completed; nothing is pending |
| 2 | CLI usage error (argparse default) |
| 10 | stopped cleanly at a human gate — the normal, expected outcome for a supervised run |
| 15 | this generation declines an action it can see is automatable — the phase is automation-safe and a later generation may drive it, but Generation 1 does not |
| 16 | `run` hit `--max-steps` with work still outstanding |
| 20 | fail-closed refusal (`ControllerError`) — unmanaged/drifted repo, malformed state, unsupported phase |
| 30 | a worker ran and failed |
| 35 | a worker ran and stopped without completing its action — it refused for a stated reason, or was cut short — leaving no durable Workflow change to verify |
| 40 | the Controller itself was interrupted, or `resume` reconciled a record to `INTERRUPTED` |
| 50 | a generation handoff is pending; the running generation stopped intentionally |

Exit 10 and exit 50 are successes of the design, not errors: conflating
them with 0 would let an outer supervisor mistake "waiting for a human"
for "finished"; conflating them with 20 would make normal operation look
like failure.

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
