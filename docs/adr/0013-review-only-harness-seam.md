# ADR 0013: A review-only harness seam: the Controller couriers a verdict from a distinct reviewer

Status: accepted (2026-10-14); the Workflow side confirmed the reading in
decision 2 on 2026-10-15 (workflow#28, with five conditions), and the operator
pre-step was recorded the same day, so the Codex harness is registered (see
[What the pre-step fixed](#what-the-pre-step-fixed)). See
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
   gate.

   **Confirmed by the Workflow side (2026-10-15).** The question was filed as
   [workflow#28](https://github.com/RodrigoFAbreu/workflow/issues/28) and answered
   *yes, with conditions*. Verbatim:

   > Section 5.5 of the Orchestration Protocol says a gate is resolved "outside
   > the orchestrator's run, by a person or by `record-external-result`". Consumer
   > obligation 8 says external verdicts enter through `record-external-result`
   > only. Obligation 5 ("never a gate's action") forbids the orchestrator from
   > acting as the reviewer in its own harness, or from resolving the gate itself.
   > It does not forbid launching an independent reviewer whose verdict the
   > Workflow then judges. Independence is enforced on the Workflow side, not by
   > trust in the courier: `ingest_manual_review_verdict` runs every guard, and
   > the Workflow can refuse the verdict.
   >
   > 1. The verdict is produced by a different provider and model family from the
   >    local stage's reviewer, stated in its `Reviewer model:` line. It is
   >    produced in a disposable copy with no write access to the real repository.
   > 2. The courier passes the verdict text verbatim. It never edits, filters or
   >    retries a verdict into a different status, and it records every verdict it
   >    obtains, `REVISE` included. A limit or failure retry re-runs only an
   >    attempt that produced no verdict.
   > 3. The verdict enters through `record-external-result` only. On a refusal,
   >    the orchestrator re-decides with `next-action` and never works around the
   >    guard.
   > 4. The courier handles only the two verdict kinds at those rows. It never
   >    resolves a `user_only` action. Under a human gate policy the approval
   >    stays a person's: recording a verdict is not an approval.
   > 5. It is opt-in. When unset, the gate stops as today.

   The issue stays open: the owner approved a short paragraph in
   `ORCHESTRATION_PROTOCOL.md` naming this courier as admissible with these
   conditions, shipping in W8 (`review-convergence`, Workflow 2.12.0) as
   documentation only. That paragraph is the pending follow-up.

   Each condition and the code that meets it:

   | condition | met by |
   | --- | --- |
   | 1. distinct provider and model, disposable copy | `expected_headers` pins `Reviewer model: openai/<model>`; the Workflow's `distinct_reviewer_models` guard at ingest (D13) refuses the same family; `build_copy` makes the copy and the boundary of decision 5 gives it no write access to the real repository (detection voids any change) |
   | 2. verbatim, every verdict recorded, retries only without a verdict | `verify_verdict` compares and never edits; every verdict the harness obtains (the first of a header-mismatch retry, a void one, a `REVISE`) is kept with its digest, `Status:` and disposition in the review record's `verdicts` and its text under `<runtime>/reviews/<job id>-verdict-<n>.md` (`_retain_verdict`, `_obtained`, `_with_disposition`); see the D5 reading below |
   | 3. `record-external-result` only; refusals re-decided | `_ingest_review` is the only path in; a refusal ends the job `FAILED` with the Workflow's code and the next step re-decides with `next-action` |
   | 4. only the two rows, nothing `user_only` | `_review_gate` accepts only rows 14 and 28 at their manual external phases; no `user_only` action is ever run; recording a verdict is not an approval |
   | 5. opt-in | `reviewer.harness` is unset by default |

   **The D5 retry under condition 2.** D5 allows one bounded retry when the
   verdict's header lines do not equal the bundle's values. The reading: a
   verdict whose header does not match cannot bind at `record-external-result`
   (the Workflow refuses a verdict that does not name the bundle it reviews), so
   it is not a verdict the Workflow could judge, and the retry asks the same
   reviewer for the same review with the exact values. The retry does not
   *replace* the first verdict in the record: the first verdict is obtained and
   recorded (digest, `Status:`, disposition `not-ingested: header mismatch,
   retried once`) beside the second, and nothing is edited, filtered or turned
   into a different status by the Controller. A retry after a limit or a failure
   runs only when the attempt produced no verdict.

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

## What the pre-step fixed

The design depended on facts only a real `codex exec` shows, and D12's operator
pre-step recorded them on 2026-10-15 against `codex-cli 0.162.1` (seven real
runs). The captures are under `tests/harness_contract/codex/` with their
digests, and `controller/review.py` takes its constants from them;
`review.PRE_STEP_CAPTURED` is true and `HARNESSES` registers Codex. What the
captures changed:

- **The reviewer needs `code_mode_host`.** gpt-6.1-sol's catalog is
  `tool_mode="code_mode_only"`, so with the feature disabled every shell and
  file call fails. It is in `TOOL_ALLOWLIST`; the other 37 disables stay and
  an unknown name fails closed.
- **Sub-agent (collaboration) tools are an allowed tool family.** No flag or
  `-c` key removes them (a pinned `model_catalog_json` would, and is not used).
  A spawned sub-agent inherits the sandbox, network-off policy, model and
  effort, but roughly doubles the spend. The prompt asks the reviewer not to
  spawn, and the Controller detects a spawn (a rollout whose
  `session_meta.source.subagent.thread_spawn` is set) and reports it, with its
  token usage, in the job record and the run's events.
- **Project trust and bundled skills are pinned.** Without
  `projects={"<copy>"={trust_level="untrusted"}}` Codex rewrites the minimal
  home's `config.toml` and trusts the copy; without
  `skills.bundled.enabled=false` five bundled skills enter the model context.
  Both overrides are required by `check_argv` and compared in the composed
  configuration.
- **Control (b) is the app-server `config/read` method with layers**, driven
  over JSON-RPC by `compose_config` with the exec's own overrides. It judges
  forbidden keys by origin (`chatgpt_base_url` always reports a packaged
  default), compares the sandbox table and the project trust table whole, and
  keys its layer dispositions on the nine `ConfigLayerSource` types, refusing an
  unknown one.
- **A limit is read from Codex's own rollout**, across every rollout of the job
  (a sub-agent writes its own): the `task_complete` event's
  `codex_error_info`, `rate_limit_reached_type` and a window at 100 percent. The
  transcript on stderr includes every command's output and is never read.
- **The home's state entries** are exactly the observed set; nothing is
  provisional, so an entry a Codex upgrade adds names itself and refuses.

Open facts the captures do not settle are listed in the fixtures' README: no
real usage-limit event was captured, refresh-token rotation was not observed,
and `/etc/codex` cannot be relocated (control (a) is its only control).

## Consequences

- With `reviewer.harness` unset, nothing changes for anyone.
- A refused or failed review leaves the external gate open, so the manual
  route is always available.
- The cost is a Codex allowance spent per review, bounded by
  `reviewer.timeout_seconds` and the usage gate.
