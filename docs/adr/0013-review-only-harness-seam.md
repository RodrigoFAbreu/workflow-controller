# ADR 0013: A review-only harness seam: the Controller couriers a verdict from a distinct reviewer

Status: accepted for the design (2026-10-14); the Codex harness stays
unregistered until its operator pre-step is recorded (see
[What is not yet switched on](#what-is-not-yet-switched-on)). See
`docs/ai-workflow/CONTROLLER_CODEX_REVIEWER_PLAN.md` for the full design
record (work item `workflow-controller-codex-reviewer`, `docs/ROADMAP.md`
step C7, released as 1.10.0). This document records how the Controller runs
the cross-model review itself, which reading of the orchestration protocol
that rests on, the boundary the reviewer runs inside, and what C10 may reuse.
The operator's view is [the settings](../guide/runtime.md#what-it-holds),
[pausing for usage limits](../run.md#pausing-for-usage-limits) and
[the troubleshooting page](../common-problems.md#a-review-the-controller-ran-did-not-finish).

## Context

At the external review gates (`plan.review.external`,
`implementation.review.external`) the Controller stopped with a human gate
and a person, or a lane script, gave the bundle to a reviewer of another
model family and pasted the verdict back. The Workflow requires distinct
reviewer models for a gate policy that asks for them, so the review has to
come from a different vendor than the Claude worker that wrote the code.
Starting that reviewer by hand costs time at every gate and a lane that is
otherwise unattended stops waiting for it.

## Decisions

1. **Opt-in, off by default.** A review spends the owner's Codex allowance
   and runs a model that can execute commands. `reviewer.harness` is unset by
   default and every decision, record, route and exit code is then exactly as
   before. An unknown name refuses at the first review step (never silently
   treated as a human gate), so a mistyped harness cannot look configured.

2. **The protocol obligation 5 reading.** Protocol obligation 5 says a
   consumer runs an `automatic` action only, never a gate's action; a gate
   is resolved outside the orchestrator's run, by a person or by
   `record-external-result`. The reading this design relies on: the action of
   an `external_gate` is *produce a verdict by a party other than the
   orchestrator's own harness and hand it to the Workflow*. The Controller
   does not run the gate's action as an `automatic` action and does not
   resolve the gate itself. A Codex process of a distinct harness and vendor,
   in a disposable copy, produces the verdict, and the verdict enters only
   through `record-external-result`, which applies every Workflow guard
   (content id, distinct reviewer models, stage already recorded) and may
   refuse. The Controller then asks `next-action` again (obligation 7) and
   follows what the Workflow answers. It is the courier for an external
   party, as a lane script or a person was, not the party that satisfies the
   gate. **Open:** the Workflow side has not yet confirmed this reading. The
   confirmation is an issue on the Workflow repository, and its answer is to
   be recorded here; until then the opt-in keeps the default untouched.

3. **It hooks in at two rows only.** The branch in `execute_step` applies
   when the answer is an `external_gate` whose `action.id` is
   `plan.review.external` or `implementation.review.external` **and** whose
   snapshot phase is `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` or
   `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`. The action id alone
   cannot separate the rows, because rows 21, 31a and 35 emit the same ids
   at other phases; those stay gates. The phase narrows only whether the
   Controller couriers the action, so the action is still chosen by its id
   and arguments (obligation 4).

4. **The seam.** `controller/review.py` holds a frozen `ReviewHarness` and a
   closed registry `HARNESSES`. A harness only produces a verdict file from a
   bundle; it never touches the repository, the state file or a job record.
   A second reviewer later is a registry entry and its fake.

5. **Review-only, in a disposable copy, behind an enforced boundary.** The
   reviewer runs in a copy of the worktree with its own one-commit
   repository (no `.git` of the real one is ever copied), `workspace-write`
   sandbox, approval policy `never`, command network access off, a
   Controller-written minimal Codex home, an exact environment allowlist and
   a pinned model provider. Before each invocation the Controller re-checks
   the effective configuration, the outside configuration layers, the
   features and the startup-loaded home. Detection (a before and after
   snapshot of the real repository, its feedback directory and the state
   identity) is added to that boundary, never instead of it: any difference
   voids the verdict. The copy is removed on every exit path.

6. **The Controller verifies the header and does not write it.** The prompt
   carries the exact header values, read from the bundle's `MANIFEST.md`.
   The verdict's header lines are compared exactly (one label table,
   including the stage's revision line, which the Workflow's parser does not
   check), with one bounded retry in the same job. A second mismatch ends the
   job `FAILED`, nothing is ingested, the gate stays open and the manual route
   applies.

7. **Supervision (the precedent C10 reuses).** The reviewer runs in its own
   session with the ownership tag in its environment, a hard
   `reviewer.timeout_seconds` deadline and a kill of its process group. The
   Controller process alone holds the lifecycle lock: the Codex group gets no
   lock descriptor. So when the Controller dies the lock is free, `resume`
   takes it as usual, and the pending non-terminal record is what stops a new
   `step`. It reuses the process-group, ownership and reaping primitives, not
   `worker._Supervision` (which parses Claude stream-json).

8. **Limits.** Admission goes through the usage gate for the Codex provider
   (readings from the operator's home merged with every retained review home).
   A limit, a timeout or an empty run ends the job `INCOMPLETE` (exit 35, the
   reservation charged by the measured delta, a `lane_blocked` notification),
   never `FAILED`. A limit during the job is not an in-run wait: the next
   `run` is held at admission by the retained rollout until the reset. A
   mid-job wait is C8b's pattern and stays out of scope.

9. **Runtime compatibility.** A review job is a new record kind. Review
   records carry job `schema_version` 2, so a 1.9.0 `resume` refuses one at
   `validate_record` (`unknown_schema_version`) instead of misreading it.
   1.10.0 accepts versions 1 and 2, each for its own kinds. A runtime holding
   a review record must be `resume`d with 1.10.0 first (see
   [compatibility](../compatibility.md#downgrading-after-a-review-job)).

## What C10 reuses

C10's automated functional review is the second consumer of the seam: a
distinct-provider process that produces a verdict from a bundle in a
disposable copy, supervised from a Controller-held lock, ingested only through
the Workflow. It reuses decisions 4 to 8 unchanged, and the
`record_external_result` wrapper in `controller/protocol.py`.

## What is not yet switched on

The design depends on facts only a real `codex exec` shows: the exact
invocation, the sandbox probe, the command that composes the configuration
layers without starting anything, the state entries Codex writes into an
empty home, and the environment the installed Codex needs. Those constants are
marked provisional in `controller/review.py`, and `review.PRE_STEP_CAPTURED`
is false, so `HARNESSES` is empty and `reviewer.harness: codex` is refused as
unregistered until the operator records the capture under
`tests/harness_contract/codex/` and sets the flag. The tests register the
harness themselves against a fake Codex (`tests/fake_codex.py`).

## Consequences

- With `reviewer.harness` unset, nothing changes for anyone.
- A refused or failed review leaves the external gate open, so the manual
  route is always available.
- The cost is a Codex allowance spent per review, bounded by
  `reviewer.timeout_seconds` and the usage gate.
