# Workflow Controller Roadmap

## Purpose

This roadmap captures the planned evolution of Workflow Controller from the completed end-to-end lifecycle orchestration baseline into a releasable, observable, portable, resilient, and operator-friendly runtime.

The roadmap is ordered by dependency and operational value. Correctness, runtime isolation, and migration safety come before broader portability and ergonomics.

---

## 0. Baseline — Automatic Lifecycle Orchestration

**Status:** Complete

Milestone: `workflow-controller-automatic-lifecycle-orchestration`

This milestone establishes the baseline that all later work builds on.

Delivered capabilities:

- automatic dispatch of automation-safe Workflow actions;
- `/milestone-implement` automation during `IMPLEMENTING`;
- automatic local implementation review;
- automatic `/apply-implementation-review` remediation loops;
- repeated local `REVISE -> apply -> fresh review` convergence;
- stop at genuine manual/user gates;
- ingestion of an already-supplied manual external implementation verdict;
- expected-outcome and durable postcondition coverage for implementation-stage actions;
- protocol 2.2 implementation-review correctness fixes;
- fail-closed durable-state checks for uncommitted checkpoint completion / implementation phase transitions;
- per-target lifecycle locking;
- worker-process identity and orphan-worker recovery;
- pending-job reconciliation and `resume --abandon`;
- explicit role-based model/effort routing;
- single-agent review routing;
- CLI/config routing overrides;
- expanded end-to-end lifecycle regression coverage.

This is the first Controller version that can drive the implementation lifecycle end to end until a true manual gate.

### Known follow-ups carried forward

These are not blockers for the baseline, but should remain visible in later milestones:

1. Manual external implementation-review gate can advertise a stale/missing ledger `review_content_id` after a failed local-review postcondition.
2. Some generated resume hints use the invalid form:
   `workflow-controller explain --work-item <id>`
   instead of putting the global option before the subcommand.
3. Explicit regression tests are still missing for apply-review retry/relaunch accounting after:
   - `OperatorAbandoned`;
   - `UnreconcilableJobError`.
4. Operator-facing liveness/recovery wording and rare process-state edge cases may still benefit from follow-up hardening.

---

# 1. Release Runtime Isolation + Live Observability

**Priority:** Immediate / High

**Status:** 1.1-1.3 complete; 1.4 next

Suggested milestone:
`workflow-controller-release-runtime-observability`

Milestone `workflow-controller-release-runtime-observability` delivered sections 1.1-1.3 and
reached `MILESTONE_COMPLETE` on 2026-09-24 (version 1.1.0, still generation 1). Its narrative is
archived at `docs/milestones/completed/workflow-controller-release-runtime-observability.md`.
Section 1.4's follow-ups were not folded into it and remain open.

This milestone turns Controller from a source-tree-oriented development tool into a properly versioned, distributable runtime and makes running workers observable in real time.

## 1.1 Runtime isolation and packaging

**Status:** Complete (`workflow-controller-release-runtime-observability`)

Goals:

- establish one authoritative Controller version source;
- use semantic versioning;
- add `workflow-controller --version`;
- build a Python wheel;
- install Controller non-editably through pipx;
- ensure installed Controller behavior is independent of later source-tree edits;
- support upgrade and rollback between released versions;
- record runtime/release identity in durable job records;
- expose runtime identity through status/diagnostics.

### Required packaged-runtime fix

A real non-editable pipx installation exposed a packaging defect:

- `inspect` works;
- worker-launching commands fail because `controller.identity` assumes the installed Controller lives inside a Git checkout and uses `git archive` against that assumed source origin.

Required end state:

- released/non-editable Controller must launch workers without requiring the original source checkout;
- installed package directories must not be assumed to be Git repositories;
- runtime identity must come from packaged/release metadata, not a mutable checkout;
- source/development execution may remain supported, but release correctness must not depend on `direct_url.json` resolving to an existing local repository;
- renaming/deleting the original source checkout after wheel installation must not break the installed runtime.

## 1.2 GitHub Actions CI and release pipeline

**Status:** Complete (`workflow-controller-release-runtime-observability`)

Required CI structure:

- Controller tests and frozen Workflow conformance suites run in CI;
- independent suites use parallel matrix jobs;
- `strategy.fail-fast: false`;
- ordinary push/PR validation uses same-ref concurrency cancellation:
  - group by workflow + ref;
  - `cancel-in-progress: true`;
- old same-branch CI runs should be cancelled when a newer run makes them obsolete;
- tagged release runs should not be casually cancelled by unrelated newer tags;
- standard Ubuntu runners;
- hermetic jobs with no shared mutable state;
- no live Claude/API-spend dependency for normal CI unless explicitly justified.

Release requirements:

- release/package publication depends on all required validation jobs;
- version/tag consistency validation;
- duplicate/overwritten release versions refused;
- wheel build and verification before publication;
- immutable GitHub Release containing the wheel;
- pipx installation from the released wheel;
- release/rollback documentation;
- release provenance recorded in durable Controller jobs.

## 1.3 Live worker observability

**Status:** Complete (`workflow-controller-release-runtime-observability`)

Add a first-class way to see what Controller-launched workers are doing while they run.

Desired UX:

```text
workflow-controller run . --follow
```

and ideally an attach flow such as:

```text
workflow-controller follow
```

or equivalent.

Requirements:

- stream Claude worker output using a supported streaming format such as `stream-json`;
- show useful observable events:
  - assistant-visible messages;
  - tool calls;
  - command/test execution;
  - tool results where useful;
  - worker running/waiting state;
  - Controller lifecycle transitions;
  - final worker result;
- tee the stream into durable per-job logs;
- preserve the existing structured final worker result;
- allow a second terminal to attach to an already-running job;
- follower disconnect must not stop or alter the worker;
- following must be presentation-only and must not change:
  - command selection;
  - routing;
  - model/effort;
  - permission mode;
  - lifecycle locking;
  - expected outcomes;
  - reconciliation;
  - Workflow state;
  - retries/failure handling;
- hidden chain-of-thought is not exposed.

## 1.4 Follow-up patches to fold in where appropriate

**Status:** Not started -- the next milestone to plan

- correct misordered `--work-item` resume hints;
- improve manual-external gate behavior when local review ledger/content is incoherent;
- add explicit abandoned/unreconcilable apply-review relaunch-bound tests;
- improve active-job/status presentation while observability work is already touching runtime diagnostics.

---

# 2. Workflow / Workflow Manager Migration Hardening

**Priority:** High

This is an adjacent Workflow/Workflow Manager track required before real RepFlow migration can be trusted.

## 2.1 `.workflow-manager/installation.json` classification

Observed during disposable RepFlow migration:

- force-bootstrap of Workflow Manager metadata into an already-active legacy work item introduced `.workflow-manager/installation.json`;
- active review-content classification did not know how to classify it;
- `/apply-plan-review` advanced Workflow state but bundle regeneration failed.

Required fix:

- known Workflow Manager metadata must be safely classified during active work items;
- do not solve this by blindly excluding arbitrary directories.

## 2.2 `/apply-plan-review` publication ordering / recovery

Observed failure:

- plan state advanced from revision 10 to 11;
- bundle regeneration failed;
- durable phase claimed `AWAITING_LOCAL_PLAN_REVIEW`;
- current review bundle remained revision 10.

Required hardening:

- Workflow should not durably advertise a review-ready state while the required current bundle is stale/missing;
- recovery semantics must be explicit and deterministic.

Controller already fails closed on stale plan bundles; Workflow publication should still be hardened at the source.

## 2.3 Stale shared feedback ownership

Repeatedly observed:

- `.ai-review/feedback/REVIEW_FEEDBACK.md` from a completed work item can block a new unrelated work item;
- manual deletion is currently required.

Short-term fix:

- safely clean/archive/replace feedback owned by a terminal/completed work item.

Longer-term direction:

- per-work-item feedback storage so unrelated work items do not contend on one flat file.

## 2.4 Plan approval commit closure

Known Workflow correctness defect:

- a new protected plan-stage file can be included in reviewed content but omitted from the plan approval commit;
- post-approval review-content recomputation then differs.

Required fix:

- approval commit member set must derive from the full declared protected-path set, not only standard plan artifacts.

## 2.5 Amendment/checkpoint concurrency

Known follow-up:

- approved-plan amendment and checkpoint activity can race across worktrees.

Required:

- define safe quiescence/ownership semantics across worktrees.

## 2.6 Amendment artifact cleanup

Investigate remaining non-gating observations around:

- empty/stale `AMENDMENT_DIFF.patch`;
- amendment review artifact consistency.

---

# 3. RepFlow Disposable Migration Re-run

**Priority:** High after milestones 1–2

Use RepFlow as the real-world dogfood target again.

Goal:

- reproduce the previous migration scenario from a fresh disposable copy;
- confirm the previously observed failures are gone.

Required exercise:

1. Clone/copy the real RepFlow repository into a disposable location.
2. Preserve its active legacy Workflow state.
3. Bootstrap/update to the supported Workflow installation.
4. Verify Workflow Manager metadata classification.
5. Let Controller take over lifecycle execution.
6. Exercise plan review/remediation.
7. Exercise implementation lifecycle if safe/appropriate.
8. Verify:
   - no stale-bundle success;
   - no stale-feedback ownership blockage;
   - no packaging/runtime dependency on Controller source checkout;
   - no duplicate worker launch;
   - no unexpected state transition;
   - no migration damage to RepFlow artifacts/history.

If this passes cleanly, proceed to the real repository.

---

# 4. Real RepFlow Migration and Dogfooding

**Priority:** After disposable migration passes

Migrate the real RepFlow Android repository to the current Workflow + Controller stack.

Goals:

- resume the existing redesign/product work under Controller;
- validate Controller against a non-synthetic, long-lived repository;
- use RepFlow as the main integration/dogfood workload.

Important constraint:

- do not mid-flight rewrite existing work-item governance/version bindings;
- migrate through supported Workflow semantics.

RepFlow should become the primary real-world proving ground for later Controller improvements.

---

# 5. Harness / Agent Portability

**Priority:** Medium

Current Controller worker execution is Claude-specific.

Goal:

- make lifecycle orchestration independent of a single agent harness.

Possible providers/harnesses:

- Claude Code;
- Codex/OpenAI coding harness;
- future supported local/remote agents.

Required architecture:

- worker launcher abstraction;
- capability detection;
- normalized model/effort/permission semantics;
- per-role harness routing;
- provider-specific configuration outside lifecycle semantics;
- durable job records include resolved harness/provider identity;
- reviews remain fresh/single-agent where policy requires;
- lifecycle correctness must not depend on provider-specific prompt behavior.

Do not weaken Workflow guarantees to accommodate a provider.

---

# 6. Concurrency, Multi-Worktree, and Durable Worker Leases

**Priority:** Medium / Later generation

The current one-worker-per-target lock is intentionally conservative.

Future goals:

- safe concurrency where Workflow semantics permit it;
- robust per-worktree and per-work-item ownership;
- durable worker leases;
- crash recovery across Controller process loss;
- clearer orphan-worker recovery;
- safe parallel work on unrelated work items/repositories;
- no accidental duplicate lifecycle worker;
- stronger reconciliation of jobs that outlive the launching Controller.

## 6.1 Feedback storage redesign

Move away from one shared flat feedback file.

Desired direction:

```text
.ai-review/<work-item>/feedback/...
```

or equivalent per-work-item scoped storage.

Goals:

- unrelated work items never block each other;
- review history is easier to inspect;
- concurrency semantics become simpler.

## 6.2 Multi-worktree correctness

Define and test:

- checkpoint claims across worktrees;
- amendments across worktrees;
- lifecycle lock scope;
- runtime root identity;
- active-worker discovery across worktrees.

---

# 7. Operator UX and Diagnostics

**Priority:** Medium after core runtime is stable

Make Controller easier to operate without reading JSON files manually.

Desired improvements:

## 7.1 `status`

Show concise current state:

- Controller version/build;
- target repository;
- active work item;
- Workflow phase;
- checkpoint progress;
- implementation/plan revision;
- current worker;
- pending reconciliation jobs;
- lifecycle lock owner;
- current route/model/effort;
- manual gate if present.

## 7.2 `explain`

Improve actionable guidance:

- commands must parse exactly as printed;
- distinguish automatic action, manual gate, recovery action, and decline;
- show why an action is blocked;
- avoid stale protocol-specific text.

## 7.3 `doctor`

Potential command:

```text
workflow-controller doctor .
```

Checks:

- package/runtime identity;
- Workflow installation;
- Controller/Workflow compatibility;
- runtime directory permissions;
- stale/pending jobs;
- lock state;
- missing tools;
- routing configuration;
- release/update status;
- known migration hazards.

## 7.4 Job inspection

Avoid requiring:

```text
jq ...
.controller/jobs/*.json
```

Provide direct commands for:

- latest job;
- latest worker output;
- job history;
- reconciliation state;
- durable event log;
- follow/attach.

---

# 8. Routing and Cost/Efficiency Improvements

**Priority:** Ongoing after routing foundation

The current routing layer supports model and effort overrides.

Future improvements:

- configurable default policy by role;
- repo-local vs user-global routing config;
- validation/listing of supported roles;
- `workflow-controller routing show`;
- optional cost-aware policies;
- explicit escalation policy:
  - implementation/remediation at medium/high;
  - normal review at high;
  - difficult/final review at xhigh;
- usage/cost recording per lifecycle role;
- preserve user override precedence.

Potential future integration:

- `claude-context-monitor` / usage-aware scheduling;
- pause/resume based on provider usage limits;
- orchestrator escalation when ambiguity exceeds worker scope.

---

# 9. Controller Orchestrator / Escalation Layer

**Priority:** Later

Controller should remain the deterministic lifecycle driver.

A higher-level orchestrator may be added for:

- ambiguous decisions;
- systemic defect detection;
- bounded retry policy;
- usage-aware pause/resume;
- cross-repository coordination;
- deciding when a human must be asked.

The orchestrator must not replace Workflow guarantees or mutate state outside legal Workflow commands.

---

# 10. Longer-Term Quality and Maintenance

Ongoing work:

- reduce duplicated lifecycle bookkeeping;
- keep review/current-state data canonical and generated where possible;
- maintain strong golden/property tests;
- preserve fail-closed semantics;
- improve migration safety;
- keep release notes/changelog;
- document backwards-compatibility policy;
- periodically prune obsolete Generation-1 compatibility branches after supported migrations are complete.

---

# Suggested Execution Order

```text
0. Automatic lifecycle orchestration                    COMPLETE
   |
1. Release/runtime isolation + live observability       NEXT
   |
2. Workflow / Workflow Manager migration hardening
   |
3. RepFlow disposable migration re-run
   |
4. Real RepFlow migration + dogfooding
   |
5. Harness / agent portability
   |
6. Concurrency / multi-worktree / worker leases
   |
7. Operator UX + diagnostics
   |
8. Routing / cost-efficiency improvements
   |
9. Orchestrator / escalation layer
   |
10. Ongoing quality / maintenance
```

---

# Roadmap Principles

1. **Workflow remains authoritative.**
   Controller automates the lifecycle; it does not replace Workflow guarantees.

2. **Fail closed.**
   Ambiguous state, stale artifacts, unknown worker ownership, or unverifiable runtime identity must not silently advance the lifecycle.

3. **Durable state over worker prose.**
   Repository state, Workflow state, committed history, review ledgers, bundles, and job records are authoritative.

4. **One correctness model across runtimes.**
   Source execution, wheel installs, different agent harnesses, and observability modes must reconcile to the same lifecycle semantics.

5. **Manual gates stay manual.**
   External review, explicit approvals, functional acceptance, and other user-owned decisions are not automated away.

6. **Dogfood continuously.**
   Workflow Controller should operate on its own repository and on RepFlow as realistic integration targets.

7. **Prefer bounded milestones.**
   Correctness and migration safety are easier to review when changes are scoped and independently accepted.
