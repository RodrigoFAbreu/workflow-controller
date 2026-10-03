# Workflow Controller Roadmap

## Purpose

This roadmap captures the planned evolution of Workflow Controller from the completed end-to-end lifecycle orchestration baseline into a releasable, observable, portable, resilient, and operator-friendly runtime.

The roadmap is ordered by dependency and operational value. Correctness, runtime isolation, and migration safety come before broader portability and ergonomics.

## At a glance

**Where things stand (2026-10-03).** Controller 1.7.0 is the latest release. It admits Workflow
2.5.1 and 2.6.0 as before, and any Workflow that speaks Orchestration Protocol v1 (2.7.0 and later); this
repository runs Workflow 2.6.0. Every milestone through C9, the Controller
on Orchestration Protocol v1 (1.7, accepted 2026-10-03), is complete; C9 was released as 1.7.0 (PR #21, squash
`fd4e9a6`). C4 was released as 1.6.0 (PR #18, squash `f2ca24d`). C8 is next.
Workflow Manager also runs Workflow 2.6.0 and has its adaptive test sharding on `main`.

**Where this is heading: a kanban loop.** The Controller takes the next open roadmap item, plans
it, implements it, reviews it, tests it, merges it and releases it as a new increment. It then
starts the next item, until the roadmap is empty. In the end no human gate is left:
- plan approval and technical approval pass on the evidence of the local review plus an automated
  cross-model review;
- `/accept-milestone` passes when the automated functional review finds nothing;
- the pull request merges itself when its required checks are green;
- the release runs from `main`.

Usage limits are tracked, so a run never starts work it cannot finish before a limit resets.

**Two lanes run in parallel**, one milestone at a time in each: this repository, and Workflow
Manager together with the new `workflow` repository. The lanes meet at two points only: C9 needs
Workflow 2.7 (W1), and C10 needs Workflow 2.8 (W2).

**Controller lane, in order.** Each step is one small milestone that is released on its own. On
2026-10-03 the user reordered the steps after C4: C9 first (Workflow 2.7.0 is published), then C8,
then C5, then C6, C7, C10 and C11. The step numbers stay as they were.

| # | Step | Needs | Section |
|---|---|---|---|
| C1 | Squash merges, with the release version derived from a Conventional Commit pull request title (as SignalHub) (complete) | — | [11.1](#111-squash-merges-and-pr-title-versions) |
| C1b | Reap every child process: the Controller collects every finished child it holds as a subreaper, in every state, and test repositories turn off Git's automatic maintenance (complete) | — | [11.1.1](#1111-reaping-every-child-process) |
| C2 | CI reliability: fix the known timing flakes; make a re-run of a failed shard count (complete) | — | [11.2](#112-ci-reliability) |
| C3 | Settings file v1, the 1.4 cleanup patches, telemetry v0 (tokens, cache, cost and time per job), and release notes that follow the milestone (complete) | — | [1.4](#14-follow-up-patches-to-fold-in-where-appropriate), [8](#8-routing-and-costefficiency-improvements), [11.1.2](#1112-release-notes-follow-the-milestone) |
| C4 | Auto-merge after acceptance: the Controller squash-merges the accepted commit (never GitHub's auto-merge), waits for the release, closes out and stops (complete) | C1, C1b, C2 | [11.3](#113-auto-merge-and-release-wait) |
| C9 | The Controller on Orchestration Protocol v1: decisions first, then outcomes (complete) | W1 | [1.7](#17-workflowcontroller-orchestration-protocol-decoupling) |
| C8 | Usage budget: track Claude and Codex limits, forecast a job's cost, pause before a limit and resume after the reset | C3 (reads Codex limits without C7) | [11.6](#116-usage-budget) |
| C5 | SignalHub notifications: progress, blockers, merges, releases and usage pauses pushed to your devices | C3 | [11.4](#114-signalhub-notifications) |
| C6 | Automated lifecycle scenarios: disposable repositories, fake workers, no model usage | — | [11.5](#115-automated-lifecycle-scenarios) |
| C7 | A review-only harness seam with a Codex reviewer: the Controller runs the cross-model review itself | — | the smallest slice of [5](#5-harness--agent-portability) |
| C10 | Gate policy: automatic approvals and automatic acceptance on sufficient evidence, and the PR defect loop | W2, C6 | [1.8](#18-policy-driven-gates-and-automated-validation), [1.9](#19-pr-review-defect-loop-and-merge-readiness-identity) |
| C11 | The kanban runner: next roadmap item, one run, merge, release, next, until the roadmap is empty | C8, C10 | [11.7](#117-the-kanban-runner) |

**Manager and Workflow lane, in order.** Workflow Manager's roadmap owns these; they are listed here
because C9 and C10 depend on them.

| # | Step | Repository |
|---|---|---|
| M1 | Trunk model: protected `main`, pull-request-only changes, squash merges with a PR-title version, auto-merge, a Manager release; a stopgap test profile until M2 | Workflow Manager |
| M2 | Distribution rework: Workflow moves to its own repository and releases as downloadable packages (every earlier release published too); the Manager downloads, verifies, caches and installs them; its tests cover only the release in development and the upgrade path | Workflow Manager, `workflow` |
| W1 | Workflow 2.7, the first packaged release: Orchestration Protocol v1 and the `v2.6.0-001` follow-up | `workflow` |
| W2 | Workflow 2.8: gate policy, and a red or changes-requested pull request reopening the same work item | `workflow` |
| M3 | Workflow Manager and `workflow` driven by the Controller's loop | both |

**Deferred** because they do not unlock that operating model: concurrency and multi-worktree (6),
the observation dashboard (7.5), other forges (7.6), hot-reloadable routing (8), the orchestrator
(9), and assurance tiers. RepFlow (3, 4) migrates once, directly to a protocol-capable Workflow.

The numbered sections below keep their historical numbers; these tables are the current order.

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
5. `explain` and next-action selection exit with an error on a plan-stage `UnclassifiedPathError`
   (reported 2026-10-01 with 1.4.2: in `AMENDING_PLAN`, a checkpoint had added files under a path
   the approved plan did not classify, and the plan-review publication-status probe crashed while
   computing the plan-stage content id). The Controller then can neither explain nor select
   `/milestone-plan <id>`, the step that repairs the classification. It should fail closed with a
   named reason and that sanctioned resume command, never an error exit.
6. With Controller 1.5.0, `run` declines `/milestone-plan <id>` in `AMENDING_PLAN` (exit 15,
   "no verifiable ExpectedOutcome is declared for (AMENDING_PLAN, \"2.2\", /milestone-plan)"; reported
   by the Workflow Manager lane on 2026-10-02). Declare the amendment re-plan's outcome
   (`AMENDING_PLAN` -> `AWAITING_LOCAL_PLAN_REVIEW`, `plan_revision` + 1, a bound bundle, anchors
   validated). Until then the step runs as a headless worker.
7. Two timing tests fail rarely on CI and pass on a re-run, in code C4 did not touch:
   `test_resume.ReattachAfterControllerLossTest.test_r15_resume_re_attaches_to_a_waiting_worker_and_reconciles_it`
   (`RUNNING` missing from the history) and
   `test_worker.OwnershipTest.test_a_gated_escapee_is_published_as_group_then_as_tag` (a sampled
   source list). Widen their windows the way C2 did.
8. A `test_worker` supervision test (`/tmp/cp3-supervise-*`) leaves its fake worker and stdin
   anchor running when the test process is killed mid-test, because its cleanup never runs. Inside
   a Controller-launched worker those leftovers kept the job draining for the full three-hour
   bound (C4, 2026-10-02). Make the test's leftovers end with the test process, and consider
   naming known test leftovers sooner than the drain bound.
9. A message from another session can land in a Controller-launched worker mid-turn (C9,
   2026-10-03). The Controller then classes the worker `AMBIGUOUS`
   (`command_lifecycle_irregular`) and fails the job, although the work was done and the state was
   coherent; the next run continued. Tolerate, or name, a peer message in a worker session.
10. The milestone planner can write an artifact declaration from an old template that classifies
   none of the paths the plan changes (C9 plan revision 10: `controller/`, `tests/`, `tools/`,
   the guides). Both plan-review stages approved it, and the first checkpoint after `controller/`
   changed refused with `UnclassifiedPathError`, needing a plan amendment. Check, at plan review,
   that the declaration classifies every path the checkpoints name.

---

# 1. Release Runtime Isolation + Live Observability

**Priority:** Immediate / High

**Status:** 1.1-1.3 and 1.2.1 complete; 1.4's worker lifecycle ownership hotfix complete (accepted 2026-09-26), its four listed patches still open

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
- independent suites use parallel matrix jobs (since the adaptive test sharding milestone below,
  a matrix planned from recorded durations, no longer the hand-curated shard list);
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

## 1.2.1 Adaptive test sharding

**Status:** Complete (`workflow-controller-adaptive-test-sharding`, accepted 2026-09-27 under
Workflow 2.5.1; plan `docs/ai-workflow/CONTROLLER_ADAPTIVE_TEST_SHARDING_PLAN.md`, ADR
`docs/adr/0005-adaptive-test-sharding.md`; the narrative is archived at
`docs/milestones/completed/workflow-controller-adaptive-test-sharding.md`). The operator requested
it directly, ahead of 1.4, which stays the next roadmap item. Its two plan amendments each made one Controller change,
listed below for the next release's notes.

The Controller's full verification ran serially in about 10 minutes locally (483 s of Controller
tests plus 119 s of frozen conformance suites), while CI split the Controller suite into eight
hand-curated shards whose test times ranged from 3 s to 193 s. One deterministic inventory and
planner now serves both:

- `tools/test_shards.py` builds the inventory (everything `unittest discover` loads, plus the
  managed conformance suites), forms class-level atoms, and plans duration-balanced shards from
  recorded timings. Timing data is advisory: it decides where a test runs, never whether;
- `tools/run_tests.py` runs a selection in parallel shards locally, `--serial` as the reference,
  `--replay` of a recorded plan, and the CI `plan` / `exec-shard` / `aggregate` steps;
- every run proves at run time that each planned test ran exactly once, with no retries and no
  test tiers;
- `validate.yml` plans its matrix in a `plan` job, runs one `tests` job per shard, and gates on
  the always-run `tests-result` aggregate.

Controller behaviour changes for the next release's notes (both in `controller/worker.py`, both
through plan amendments; both shipped in 1.2.1, a bump-only release, since the milestone's merge
did not change the version):

- an owned process's `source` label now follows its current ownership basis, not the one it had
  when first seen, and a relabel is published (amendment 0). For example, a background process
  that leaves the worker's group with `setsid` is now always reported as owned by `tag`; a first
  sighting before its `setsid` used to leave it labelled `group`. Which processes are owned is
  unchanged;
- the drain detach bound is 10800 s (3 hours), up from 600 s, as an interim constant; making it
  configurable is listed under 1.4 (amendment 1).

Left for later: failing a run on a leaked process (D7) and refreshing the committed timing
profile from real CI runs.

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

**Status:** Complete. One urgent correctness hotfix came first in this slot
(`workflow-controller-worker-lifecycle-ownership`, accepted 2026-09-26 under Workflow 2.5.1; the
narrative is archived at `docs/milestones/completed/workflow-controller-worker-lifecycle-ownership.md`).
The four patches below and the settings file v1 were step C3
(`workflow-controller-settings-and-telemetry`, accepted 2026-10-01 under Workflow 2.6.0; plan
`docs/ai-workflow/CONTROLLER_SETTINGS_AND_TELEMETRY_PLAN.md`, ADR
`docs/adr/0008-controller-settings-file.md`; the narrative is archived at
`docs/milestones/completed/workflow-controller-settings-and-telemetry.md`), together with
telemetry v0 (section 8) and release notes from the milestone (11.1.2). Released as 1.5.0 (PR #16,
squash `f92f31b`).

**Hotfix: worker lifecycle ownership** (milestone `workflow-controller-worker-lifecycle-ownership`,
plan `docs/ai-workflow/CONTROLLER_WORKER_LIFECYCLE_OWNERSHIP_PLAN.md`, ADR
`docs/adr/0004-worker-lifecycle-ownership.md`). Workers that started verification in the background
and ended their turn were killed by the harness's print mode, and their jobs failed `AMBIGUOUS`
or finished while processes still ran. Workers now run with streaming input, one session kept
open while they own background work (tasks, Monitors, wakeups, background subagents); the
Controller ends a session only at a quiescent terminal turn, owns escaped descendants through an
ownership tag and a subreaper, keeps the lifecycle lock for the whole owned lifetime through a
stdin anchor, reconciles only after the worker has ended and its owned work has drained, and
`resume` re-attaches to a live job after a Controller loss. `status`, `inspect`, `explain` and
`follow` show waiting, draining and unsupervised workers. The harness limitations it documents
rather than solves (the wakeup-fire recogniser's residue, escape windows, daemon recognition by
name) are in ADR 0004. It was not folded together with the patches below, which C3 delivered:

- correct misordered `--work-item` resume hints;
- improve manual-external gate behavior when local review ledger/content is incoherent;
- add explicit abandoned/unreconcilable apply-review relaunch-bound tests;
- improve active-job/status presentation while observability work is already touching runtime diagnostics;
- make the drain detach bound and the other Controller tunables configurable, including a `--timeout` for `resume`'s re-attach drain; 10800 s is an interim constant, amendment 1 of 1.2.1.

**Settings file v1 (step C3).** One Controller settings file holds every value that is, or could
safely be, configurable without breaking the Controller: tunables such as the drain detach bound,
routing defaults, and later the auto-merge and review switches.
- The Controller writes every missing setting into the file with its default, so a new setting
  migrates itself.
- A setting that no longer exists is ignored and can be cleaned up.
- The file is the model a future UI edits.

---

# 1.5 Trunk Branch / PR / Release Orchestration

**Priority:** Immediate / High

**Status:** Complete (`workflow-controller-trunk-branch-pr-release-orchestration`, accepted 2026-09-25 under Workflow 2.5.1). The narrative is archived at `docs/milestones/completed/workflow-controller-trunk-branch-pr-release-orchestration.md`. The first automatic release, 1.2.0, was published by `main.yml` on 2026-09-26.

Milestone:

`workflow-controller-trunk-branch-pr-release-orchestration`

This milestone establishes the lightweight trunk-based repository lifecycle that later protocol and provider-neutral work will build on.

Required end state:

- one short-lived `milestone/<work-item-id>` branch per milestone;
- Draft PR created/reused through a repository-host boundary;
- human remains the only PR merger by policy;
- no permanent `develop` branch;
- fail closed when trunk moves in a way Workflow 2.5.1 cannot safely integrate;
- generic repository release policy with Controller as the first reference adopter;
- `pyproject.toml` is the single human-maintained Controller version authority (superseded by 11.1, step C1, and [ADR 0007](adr/0007-tag-derived-versions-and-squash-merges.md): the release tags are the only version authority);
- version change on trunk drives one deterministic release transaction;
- validate/build/verify before tag creation;
- immutable tags;
- resumable partial publication only when the tag identifies the expected validated commit;
- no automatic PR merge;
- generic Git / forge operations stay outside Workflow lifecycle semantics.

This milestone must finish independently under Workflow 2.5.1. It must not wait for Workflow 2.6.x.

## 1.6 Post-Workflow-2.6 compatibility integration

**Status:** Complete (`workflow-controller-workflow-2-6-integration`, accepted 2026-09-28 under
Workflow 2.5.1; plan `docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md`, ADR
`docs/adr/0006-workflow-release-admission-and-per-release-contracts.md`; the narrative is archived
at `docs/milestones/completed/workflow-controller-workflow-2-6-integration.md`). It admits Workflow
2.6.0 beside 2.5.1 by exact release and ships as Controller 1.3.0. E1-E5 are answered in ADR 0006:
`gitrepo.merge_trunk` stays unwired, and the ADR names the Workflow follow-up. 1.3.0 was released
on 2026-09-28, and this repository moved to Workflow 2.6.0 in pull request #6.

**Priority:** Immediately after both parallel milestones complete.

After:

1. Workflow Manager ships the review-artifact/concurrency hardening release (2.6.x); and
2. Controller finishes trunk/branch/PR/release orchestration under Workflow 2.5.1;

run a small integration milestone rather than mixing the two efforts while either contract is still moving.

Expected scope:

- update the Controller repository to the released Workflow 2.6.x through Workflow Manager (never by hand), between milestones;
- measure the released contract (phases, state fields, commands) and answer the trunk plan's open questions E1-E5 from it;
- replace Controller's duplicated feedback-path resolver (`controller/evidence.py`, `resolve_feedback_dir`) with Workflow's authoritative resolver/query;
- re-measure and re-anchor any expected-outcome/state-writer checks that still depend on Workflow internals, against the released 2.6 commands;
- admit the release in `managed_repo.VALIDATED_WORKFLOW_RELEASES`, update the version fixtures, and re-run the inventory and golden decision suites against every admitted release;
- test 2.5.1 -> 2.6.x migration, including an in-flight bound milestone;
- any branch/base/worktree binding that genuinely belongs to the released Workflow contract, including wiring `gitrepo.merge_trunk` to a released base-moving transition if E2-E4 provide one, or naming the narrow Workflow follow-up if they do not;
- preserve the current fail-closed behavior (`integration_required` plus the documented manual merge) until the actual released 2.6 contract has been measured.

The scope is planned from the actually released 2.6.x implementation, not from this list.

This should stay a compatibility/integration milestone, not become the full architectural decoupling project.

## 1.7 Workflow/Controller orchestration protocol decoupling

**Status:** Complete (`workflow-controller-orchestration-protocol-v1`, accepted 2026-10-03 under
Workflow 2.6.0; plan `docs/ai-workflow/CONTROLLER_ORCHESTRATION_PROTOCOL_V1_PLAN.md`; the narrative is
archived at `docs/milestones/completed/workflow-controller-orchestration-protocol-v1.md`). Released as
1.7.0 (PR #21, squash `fd4e9a6`). The description below is the original problem statement.

**Priority:** High after the 2.6 compatibility integration.

Goal:

> Controller should depend on a small, versioned public Workflow orchestration protocol, not on Workflow release numbers, artifact paths, internal helper names, or copied transition logic.

Target public Workflow protocol operations:

1. `describe`
   - Workflow release;
   - orchestration protocol version;
   - supported governing protocol versions;
   - declared capabilities.

2. `verify`
   - repository/Workflow installation health;
   - state readability and consistency;
   - protocol readiness.

3. `next-action`
   - normalized current state snapshot;
   - semantic action id;
   - arguments;
   - disposition (`automatic`, `validation`, `human_gate`, `external_gate`, `blocked`, `complete`);
   - generic worker requirements;
   - broad allowed result classes;
   - state revision / state identity used to make the decision.

4. `reconcile`
   - authoritatively classify the durable result of an action;
   - recognize valid same-phase progress such as `IMPLEMENTING -> IMPLEMENTING` with one checkpoint completed;
   - report progress, valid no-progress, human/external gate reached, completion, or invalid result;
   - return the new durable state identity.

5. `record-external-result`
   - ingest semantically typed external/manual evidence;
   - examples: manual external review verdict, PR-review result, functional evidence;
   - Workflow resolves its own storage/binding rules.

6. `resolve-artifact`
   - narrow escape hatch when a Controller genuinely needs a Workflow-owned artifact path;
   - semantic artifact kind rather than copied filesystem rules.

Optional/read-only convenience:

- `inspect`, primarily for CLI/UI/debugging; it should not be required on every orchestration loop if `next-action` already carries the relevant state snapshot.

Protocol design rules:

- stable semantic action ids, not internal Python function names;
- literal Workflow command text may be supplied as a rendered invocation, but must not be the protocol identity;
- protocol version is separate from:
  - Workflow release version;
  - work-item governing Workflow version;
- exact Workflow releases may remain "tested with" metadata, but should stop being the fundamental compatibility gate;
- unknown protocol major fails closed;
- every response is schema-validated and bound to repository/work-item/state identity;
- stale decisions are rejected if the state revision/identity changes before execution;
- broad stable error codes are exposed alongside precise Workflow-native diagnostics;
- Controller verifies generic execution facts, while Workflow owns lifecycle meaning.

Desired compatibility model:

```text
Workflow 2.6.0 ─┐
Workflow 2.7.0 ─┤
Workflow 2.9.3 ─┤── Orchestration Protocol v1 ── Controller
Workflow 3.x   ─┘
```

As long as the public protocol remains compatible, a new Workflow release should normally require no Controller lifecycle-code change.

## 1.8 Policy-driven gates and automated validation

The protocol must not hard-code today's human gates.

Future policy should permit repositories to choose, for example:

```text
plan cross-model approval       -> automatic advance
implementation technical review -> automatic advance
functional validation           -> automatic or human by policy
PR review / merge               -> external/human gate
```

Important separation:

- Workflow defines whether evidence satisfies a lifecycle gate;
- Controller executes the declared policy;
- harness/model selection remains Controller policy;
- PR merge remains human-only unless repository policy is deliberately changed later.

Potential future direction:

- remove ceremonial human plan approval once required local + cross-model review evidence is current and approved;
- potentially remove separate human implementation approval when technical acceptance is already established and PR review/merge is the true product/integration acceptance boundary;
- make functional review evidence-driven:
  - Workflow Manager / Controller may use disposable-repo, migration, integration, and E2E suites as automated functional evidence;
  - RepFlow may use emulator/device E2E, migration, UI and integration tests, while still requiring human visual/product acceptance for selected changes;
- allow risk/repository policy to decide whether a validation result auto-advances or stops for a human.

Automated validation proves that the configured evidence passed; it does not make the work item irreversible.

## 1.9 PR-review defect loop and merge-readiness identity

A technically accepted milestone must remain reopenable until the PR is actually merged.

Required end-state semantics:

```text
implementation
  -> technical review
  -> functional validation
  -> PR_READY
  -> human PR review
       -> merge
       -> or CHANGES_REQUESTED -> remediation -> re-review/re-validation -> PR_READY
```

Merge readiness must be bound to an exact identity, including at minimum the current PR head SHA and the evidence produced against that head.

A changed PR head makes previous merge-readiness evidence stale unless Workflow policy explicitly proves otherwise.

External facts and lifecycle meaning stay separated:

- forge adapter reports facts such as:
  - PR head changed;
  - review `CHANGES_REQUESTED`;
  - review `APPROVED`;
  - checks changed;
  - PR closed/reopened/merged;
- Workflow decides:
  - which evidence is stale;
  - whether implementation review must rerun;
  - whether functional validation must rerun;
  - whether targeted validation is sufficient;
  - which lifecycle action is next;
- Controller launches the required workers/actions.

A PR-review defect normally reopens the same work item rather than creating a new milestone.

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

- a stable **Harness Adapter Protocol** between Controller lifecycle logic and provider-specific execution;
- worker launcher abstraction;
- capability detection;
- normalized model/effort/permission semantics;
- normalized worker event stream;
- normalized usage/quota telemetry where providers expose it;
- per-role harness routing;
- provider-specific configuration outside lifecycle semantics;
- durable job records include resolved harness/provider/model/effort identity;
- reviews remain fresh/single-agent where policy requires;
- lifecycle correctness must not depend on provider-specific prompt behavior.

The Workflow protocol should return generic worker requirements (for example `implementation`, `independent_reviewer`, fresh-session requirement, independence requirement, subagent policy). Controller routing chooses the concrete harness/model/effort.

Examples:

```text
independent_reviewer
  -> Claude Code + Opus
  -> Codex + Sol
  -> another future harness/model

implementation
  -> Codex + Terra
  -> Claude Code + Sonnet
  -> another future harness/model
```

Workflow must never encode provider/model names as lifecycle semantics.

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

## 7.5 Observation API, structured event store, and web dashboard

The existing `follow` implementation is the first observation client, not the final architecture.

Target data flow:

```text
Harness raw stream
  -> Harness adapter
  -> normalized Controller events
  -> durable event/history store
       -> CLI `follow`
       -> local web dashboard
       -> analytics / historical reports
```

Observation must be passive by default:

- viewing/fetching the dashboard must not launch a model;
- it must not append to worker context;
- it must not change routing, Workflow state, retries, or lifecycle decisions;
- chat viewing, metrics, live status and historical analytics should consume already-produced harness/Controller/Workflow/forge telemetry;
- optional AI-powered summaries or semantic analysis must be explicit model-consuming features, separate from normal dashboard operation.

Store both:

- raw provider/harness payloads where useful for debugging;
- normalized events for stable UI/analytics.

Normalized event vocabulary should cover at least:

- assistant message;
- tool call;
- tool result;
- system event;
- usage update;
- quota/rate-limit event;
- Workflow action/transition;
- worker/job/run result;
- forge/PR observation;
- validation result.

Stable identity hierarchy:

```text
repository_id
  -> work_item_id
      -> controller_run_id
          -> job_id
              -> worker_session_id
```

This must let the dashboard answer:

- what is running in each repository;
- current Workflow release/protocol/phase/checkpoint/action;
- current harness/model/effort;
- live worker chat/tool activity;
- retries, failures, quota pauses and recovery;
- plan/implementation review convergence;
- time spent per lifecycle phase/checkpoint;
- token/cache/cost/context metrics where available;
- cost/tokens/time per successful checkpoint or approved review;
- model + effort efficiency by lifecycle role;
- harness efficiency independently from model efficiency;
- Workflow release/protocol efficiency comparisons;
- PR/readiness/check state;
- historical milestone/run/job timelines.

Metrics should be computed deterministically from stored telemetry whenever possible. Missing provider metrics should remain `unknown`, not be guessed by a model.

## 7.6 Forge / repository-host adapter protocol

Controller should be provider-neutral above a small repository-host adapter boundary.

Initial provider:

- GitHub, implemented with `gh` / GitHub APIs as appropriate.

Future providers may include:

- GitLab;
- Bitbucket;
- other compatible forges.

Normalized operations should cover concepts such as:

- describe provider/repository;
- resolve/open/reuse PR/MR;
- observe PR/MR;
- mark Draft/Ready;
- observe reviews;
- observe required checks;
- observe head/base identities;
- observe merged/closed/reopened state.

The Controller lifecycle must consume normalized forge facts, not GitHub-specific JSON shapes.

Correctness model:

- polling/query-on-demand is authoritative and sufficient for recovery;
- future webhooks are fast notifications only;
- after downtime, Controller re-queries the forge and reconstructs current truth;
- PR/MR readiness is bound to the exact observed head SHA;
- a head change is a deterministic stale-evidence trigger reported to Workflow;
- review `CHANGES_REQUESTED` is distinct from "head changed";
- Controller does not decide which Workflow evidence must be invalidated.

Current policy remains human-only merge. The adapter may observe merge state but must not auto-merge unless a future repository policy explicitly changes that invariant.

## 7.7 `update`: self-update from published releases

Replace the manual download / `sha256sum -c` / `pipx install --force` / `--version` sequence with one command:

```text
workflow-controller update            # the latest release
workflow-controller update 1.1.1      # a named release (also accepts v1.1.1); covers rollback
workflow-controller update --check    # report installed vs target; change nothing
```

Decided:

- `update` performs the install itself, running `pipx install --force` on the verified wheel, then confirms the new `--version` and reports `old -> new`;
- a source checkout or editable install (a `source` runtime) never self-updates: `update` refuses with an error that names the runtime kind and says to update the checkout instead;
- the release repository comes from the wheel itself: the release build records `GITHUB_REPOSITORY` in the build info as a new field (e.g. `release_repository`; a `schema_version` bump that `verify-wheel` checks), so a release wheel only updates from the repository that published it. Local builds record none;
- `--repo OWNER/NAME` overrides it (forks, mirrors, local builds). With neither a recorded repository nor `--repo`, `update` refuses rather than guess. No hardcoded fallback.

Required behaviour:

- downloads go through `gh release download` (the repository may be private: anonymous release URLs return 404), so `gh` missing or unauthenticated is an up-front refusal;
- verification before install: the wheel against the release's `SHA256SUMS` (integrity only, since both come from the same release), then the wheel's own build info against the release (`build_origin: release`, the expected `release_tag`, and a `source_commit` equal to the tag's commit). The `verify-wheel` logic moves from `tools/release.py` into the package, since `tools/` is not shipped;
- every refusal happens before pipx runs: a `source` runtime, `status` not `active: none`, `gh` unavailable, an unknown version, and a downgrade across a generation (`controller/GENERATION.json`) without an explicit flag, because the older generation refuses the newer one's job records (`StaleJobRecordError`, see the rollback notes in `docs/guide/installation.md`);
- requesting the already-installed version is a no-op that says so;
- `doctor` (7.3) reports release/update status by reusing `update --check`.

Initially GitHub-only via `gh`; release discovery moves behind the forge adapter (7.6) when that boundary exists.

# 8. Routing and Cost/Efficiency Improvements

**Priority:** Ongoing after routing foundation

**Status:** Partly complete. Step C3 (`workflow-controller-settings-and-telemetry`, accepted
2026-10-01; see 1.4) delivered telemetry v0, which records each job's session totals (turns,
tokens, cost, API time, per-model usage, wall times) and adds the read-only `telemetry` summary by
work item, run, date, role or model. It also made the routing defaults a section of the settings
file. The other items below remain open; hot-reloadable routing is deferred (see
[At a glance](#at-a-glance)).

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
- record model, harness, effort and Workflow/protocol dimensions separately so efficiency can be compared without conflating them;
- support derived metrics such as cost/token/time per checkpoint, review round, accepted plan, accepted implementation and successful validation;
- preserve user override precedence.
- make routing configuration hot-reloadable between lifecycle steps within one long-running `run` invocation:
  - re-read the effective routing policy immediately before each new worker is launched;
  - edits to repo-local/user-global routing config should affect later stages without restarting Controller;
  - a worker already running keeps the model/effort/harness it was launched with;
  - routing changes must never mutate an active worker session;
  - invalid routing edits should fail closed at the next dispatch boundary, before launching another worker;
  - record the exact resolved routing snapshot used for each job so historical runs remain auditable;
  - preserve deterministic precedence between CLI overrides, repo-local config, user-global config, and defaults;
  - future web UI should be able to edit routing policy safely and have it take effect on the next worker boundary.

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

# 11. Autonomous delivery

The steps of [At a glance](#at-a-glance) that no earlier section covers. Each is planned from the
code as it stands when its turn comes, not from this list.

## 11.1 Squash merges and PR-title versions

**Status:** Complete (`workflow-controller-squash-merge-tag-versioning`, accepted 2026-09-29 under
Workflow 2.6.0; plan `docs/ai-workflow/CONTROLLER_SQUASH_MERGE_TAG_VERSIONING_PLAN.md`, ADR
`docs/adr/0007-tag-derived-versions-and-squash-merges.md`; the narrative is archived at
`docs/milestones/completed/workflow-controller-squash-merge-tag-versioning.md`). The milestone never
changed the policy or `pyproject.toml`'s version line. The switch is the plan's Design H: PR #8 was
merged with a merge commit (`f77ff06`, `NO_CHANGE`), the repository settings changed to squash-only,
and the two-file cutover pull request (#9) is squash-merged, which releases 1.4.0. One change from
the plan (decided 2026-09-29): C1's binding is closed out by the first 1.4.0 step, not by 1.3.0,
because a close-out step goes straight on to `/milestone-plan`, which must not start before the
cutover. Merge mode is unchanged in 1.4.0, and the binding keeps the merge mode it was bound with.

**Step C1.** Checked against Workflow Manager: a Controller-only change.

- Squash merges only. The repository settings become squash-only, with the squash message set to
  the pull request title and body.
- The release version is derived from the pull request title, as in SignalHub:
  - the title must be a Conventional Commit (`feat: …`, `fix: …`, `feat!: …`), checked by a
    required check;
  - it becomes the squash commit's subject;
  - its type decides the bump: `feat` gives a minor release, `fix` a patch, and `!` a major;
  - `docs`, `chore` and `ci` merge without a release (decided 2026-09-29).
- **The Git tag is the only version authority, as in SignalHub (decided 2026-09-29).**
  - `pyproject.toml` no longer holds a version; the package's version is set at build time.
  - The release computes the next version from the latest release tag and the squash commit's
    type, and builds the wheel with it.
  - No milestone and no person edits a version anywhere.
  - This replaces 1.5's rule that `pyproject.toml` is the single human-maintained version
    authority. The release guide changes with it.
- The existing release transaction stays: build and verify before the tag, immutable tags, and a
  safe resume of an interrupted publication. Releases on `main` are serialized.
- Workflow's provenance checks look only at the active work item's history, so a completed item's
  squashed commits are never re-checked.
- Close-out gains a merged-by-squash state (`MERGED_SQUASHED`). The next item is planned from
  `main`'s head, with that base passed explicitly to `/milestone-plan`.
- A milestone branch is never rebased or updated from `main`.

## 11.1.1 Reaping every child process

**Status:** Complete (`workflow-controller-child-process-reaping`, accepted 2026-09-30 under
Workflow 2.6.0; plan `docs/ai-workflow/CONTROLLER_CHILD_PROCESS_REAPING_PLAN.md`, the 1.4.1
amendment to `docs/adr/0004-worker-lifecycle-ownership.md`; the narrative is archived at
`docs/milestones/completed/workflow-controller-child-process-reaping.md`). Released as 1.4.1
(PR #11, squash `e8cd8f9`, 2026-09-30). Its release notes reached neither the PR body nor the GitHub
release (see 11.1.2); they are in `docs/releases/1.4.1.md`.

**Step C1b** (added 2026-09-29, ahead of C2). On 2026-09-29 every Claude Code process on the host
aborted twice within ten minutes: the per-user process limit (125,849, threads included) was full
of zombie `git` processes held by the two lanes' Controllers.

- **Cause.** The Controller marks itself a child subreaper while a worker runs, so every orphan in
  the worker's tree is re-parented to it. It scans for orphans only while the worker is `WAITING` on
  owned background work, and collects only the ones those scans recorded (`_ADOPTED`, reaped at the
  drain). An orphan that appears while the worker runs a foreground command, such as a test run,
  is never recorded, and stays a zombie until the Controller exits. Git 2.55 starts detached
  background maintenance after commits, and test suites make thousands of commits in throwaway
  repositories: about 1,000 orphans per test run here, and 42,158 in one Workflow Manager run.
- **Fix.** On every supervision tick, in every state, collect every finished child the Controller
  holds except the worker and its anchor, each by its own pid (never `waitpid(-1)`, which could
  steal the worker's status), reading the direct children from `/proc/self/task/<tid>/children`.
  Do the same between steps and at the end of a run.
- **Tests.** A fake worker that orphans hundreds of short-lived processes while it is `RUNNING`, not
  only `WAITING`: the zombie count under the Controller stays near zero during the job and is zero
  after it. The existing orphan-reap tests keep passing.
- **Test hygiene.** This repository's throwaway test repositories turn off Git's automatic
  maintenance (`maintenance.auto=false`, `gc.auto=0`) in the shared test setup. Workflow Manager does
  the same in its own M1b.
- **Afterwards.** Both lanes drop their stopgap, one step per Controller process (`--max-steps 1`),
  once the patch release is installed. A long-lived Controller (C4, C11) depends on this fix.

## 11.1.2 Release notes follow the milestone

**Status:** Complete (`workflow-controller-settings-and-telemetry`, accepted 2026-10-01; see 1.4).
The design was narrowed during planning. A repository opts in through its policy
(`milestone_branches.pull_request.release_notes` and `{release_notes}`). Readiness then puts the
milestone's `## Release notes` section into the pull request body under a marker bound by the work
item id and a digest. The release publishes the verified blocks from its range's squash commits,
or refuses and names the fix (`tools/release.py notes-block`). This repository opts in through a
small pull request after 1.5.0. Until then, 1.5.0's notes are copied into `docs/releases/` by
hand. The description below is the original problem statement.

**Part of step C3** (added 2026-09-30, from C1b's functional review, flow H). `docs/README.md` says
that from 1.4.0 a milestone's release notes are its pull request body, which becomes the squash
commit's body. The code does not do that:
- in squash mode the Controller writes a fixed body (`squash_body` in `controller/milestone_branch.py`:
  the work item, the plan, the "Accepted at" line) and overwrites the pull request body at
  readiness, so notes written into the body are lost;
- the GitHub release's notes are the policy's `release.publication.notes`, `workflow-controller {tag}`.

So 1.4.1's notes, written in the milestone's "Pull request body" section, were published nowhere;
`docs/releases/1.4.1.md` keeps them by hand.

- A milestone's release-notes section (the narrative's "Pull request body" section) becomes the
  pull request body at readiness, above the Controller's own lines, so the squash commit carries it.
- The release publishes the squash commit's body as the GitHub release notes.
- Readiness still refuses a trailer-like line in the body (I8 of the C1 plan), and a milestone
  without a notes section keeps today's body.
- `docs/README.md`'s rule and the release guide are corrected to match.

## 11.2 CI reliability

**Status:** Complete (`workflow-controller-ci-reliability`, accepted 2026-09-30 under Workflow
2.6.0; plan `docs/ai-workflow/CONTROLLER_CI_RELIABILITY_PLAN.md`, the 1.4.2 amendments to
`docs/adr/0004-worker-lifecycle-ownership.md` and `docs/adr/0005-adaptive-test-sharding.md`; the
narrative is archived at `docs/milestones/completed/workflow-controller-ci-reliability.md`). The
three flakes are fixed, each with a regression that widens its window. A re-run of failed jobs
counts: each shard's latest attempt decides, superseded attempts are listed, and `tests-result`
fails when a job of the current attempt did not succeed. A leaked process fails the run (D7), and
nothing is retried automatically. Released as 1.4.2 (PR #13, squash `0de0fd5`).

**Step C2.** An unattended merge stalls on every flaky run, and the Workflow 2.6 milestone needed
five re-runs.

- Fix the known races:
  - `OwnershipTest`, which reads a process's command line while that process is still starting;
  - `CrossProcessEventSeqTest`;
  - D7.
- A re-run of only the failed jobs must count: today `tests-result` aggregates the first
  attempt's shard results, so only a full re-run clears a flake.
- Decide whether a shard is retried automatically, and how a retry is reported so that a real
  failure is never hidden.

## 11.3 Auto-merge and release wait

**Status:** Complete (`workflow-controller-auto-merge-release-wait`, accepted 2026-10-03 under
Workflow 2.6.0; plan `docs/ai-workflow/CONTROLLER_AUTO_MERGE_RELEASE_WAIT_PLAN.md`, ADR
`docs/adr/0009-auto-merge-and-release-wait.md`; the narrative is archived at
`docs/milestones/completed/workflow-controller-auto-merge-release-wait.md`). A repository opts in
through its policy (`milestone_branches.pull_request.auto_merge`), and the settings file's
`merge.auto` turns it off on the machine. One deliberate change from the description below: the
Controller does not enable GitHub's auto-merge, which could merge a later push. It sends one
`gh pr merge --squash --match-head-commit` at the acceptance commit per attempt, so GitHub merges
exactly that commit or refuses. Released as 1.6.0 (PR #18, squash `f2ca24d`). The description
below is the original problem statement.

**Step C4.**

- Once a milestone is accepted and its pull request is ready, the Controller enables GitHub
  auto-merge, and GitHub merges when the required checks are green.
- The Controller waits for the release run on `main`, records the published release, closes out,
  and stops.
- If a check goes red after acceptance, it stops and reports. The fix loop is C10.
- Until C10, `/accept-milestone` stays a human gate, and it is the last one.
- The settings file (C3) can turn auto-merge off.

## 11.4 SignalHub notifications

**Step C5.** Once runs are unattended, the operator needs to know what is happening and when
something is blocked, without watching a terminal. [SignalHub](https://github.com/RodrigoFAbreu/SignalHub)
is the owner's notification platform and already accepts generic events from any producer. The
Controller becomes one more producer over its public API (`POST /api/v1/events`), and SignalHub
needs no special case.

- **What is sent:**
  - a milestone started, planned or accepted;
  - **a blocker**: a human gate reached, a refusal, a failed job, red checks after acceptance, a
    stuck release;
  - a pull request merged;
  - a release published;
  - a usage pause and its resume (C8);
  - a run finished.
- **How each event is shaped:** a category and a severity, with a blocker at the highest severity,
  so SignalHub's per-device filters apply. Each event links to the pull request, release or run
  where there is one.
- **Idempotency:** each event carries an `Idempotency-Key` derived from its run, job or event
  identity, so a retry or a `resume` never sends a notification twice.
- **Configuration:** it lives in the settings file (C3): the server address, and **every event is
  configurable per event type** (the user's requirement, 2026-09-30):
  - on or off (for example, turn off the per-step state-switch events);
  - its category and severity (for example, raise "milestone accepted" from NORMAL to HIGH);
  - an event type's setting overrides its kind's, and the defaults are the mapping the lanes use by
    hand today: a finished step, a usage pause or a limit wait INFO/LOW; a Codex review, an approval
    recorded, a milestone started or accepted, a merge or a release INFO/NORMAL; a user gate
    ACTION_REQUIRED/HIGH; a blocker BLOCKED/CRITICAL.

  Until C5, the lanes read the same settings from `~/.config/signalhub/events.json` through a shared
  script, with the same event names. The API key comes from the environment or a file, never from the
  repository.
- **Notifications never steer the lifecycle.** A notification that cannot be delivered is retried
  a bounded number of times and recorded in the run's events. It never blocks, fails or changes a
  lifecycle step.
- **Standard library only.** The Controller calls the HTTP API directly, the same contract
  SignalHub's own Python SDK uses.

## 11.5 Automated lifecycle scenarios

**Step C6.**

- Disposable repositories, fake workers and fake forges run whole lifecycle paths without model
  usage.
- They become the automated functional evidence that C10 accepts in place of a manual functional
  review.

## 11.6 Usage budget

**Step C8.** The kanban loop and the two parallel lanes share one set of usage limits: the Claude
plan's 5-hour window, and Codex's limits.

- Track usage against those limits across every repository the Controller drives.
- Forecast a job's cost from telemetry v0 (C3), per role and model.
- Never start a job that cannot finish before a limit. Pause at a safe boundary instead, and resume
  automatically after the reset, so no work is left half finished.
- Record every pause and resume in the run's events.

## 11.7 The kanban runner

**Step C11.**

- Read the roadmap and pick the next open item.
- Run it end to end: plan, implement, review, test, accept, merge, release.
- Start the next item, until nothing is left.
- It stops for a human only when policy says the evidence is insufficient for the next decision,
  or when the usage budget says to wait.

---

# Execution order

The current order is the tables in [At a glance](#at-a-glance). Completed so far, in order:

```text
0.     Automatic lifecycle orchestration                     COMPLETE
1.1-3  Release/runtime isolation + live observability        COMPLETE (1.1.0)
1.5    Trunk/branch/PR/release orchestration                 COMPLETE (first automatic release 1.2.0)
1.4    Worker lifecycle ownership hotfix                     COMPLETE (in 1.2.0)
1.2.1  Adaptive test sharding                                COMPLETE (released as 1.2.1)
1.6    Workflow 2.6 compatibility integration                COMPLETE (released as 1.3.0)
11.1   C1: squash merges and PR-title versions               COMPLETE (released as 1.4.0 at its cutover)
11.1.1 C1b: reaping every child process                    COMPLETE (released as 1.4.1)
11.2   C2: CI reliability                                    COMPLETE (released as 1.4.2)
1.4    C3: settings, telemetry v0, release notes, 1.4 patches     COMPLETE (released as 1.5.0)
11.3   C4: auto-merge after acceptance and the release wait  COMPLETE (released as 1.6.0)
1.7    C9: the Controller on Orchestration Protocol v1       COMPLETE (released as 1.7.0)
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

5. **Gate ownership is explicit and policy-driven.**
   Controller must not hard-code today's manual gates. Workflow declares whether the current requirement is automatic, validation-driven, external, or human-owned. Human-only actions such as PR merge remain human-owned unless repository policy is deliberately changed.

6. **Public protocols, not implementation details.**
   Controller may depend on versioned Workflow, harness, forge and observation contracts, but not on copied artifact-path rules, internal Workflow helper names, provider-specific event formats, or forge-specific lifecycle semantics.

7. **External systems report facts; Workflow owns lifecycle meaning; Controller owns orchestration.**
   Harnesses report execution, forges report repository/PR facts, and Workflow decides what those facts mean for lifecycle validity and evidence freshness.

8. **Observation is passive by default.**
   CLI/web dashboards and metrics read durable events and telemetry without consuming model quota or changing execution.

9. **Dogfood continuously.**
   Workflow Controller should operate on its own repository and on RepFlow as realistic integration targets.

10. **Prefer bounded milestones.**
    Correctness and migration safety are easier to review when changes are scoped and independently accepted.
