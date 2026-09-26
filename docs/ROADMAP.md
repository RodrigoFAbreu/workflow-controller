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

**Status:** 1.1-1.3 complete; 1.4's worker lifecycle ownership hotfix complete (accepted 2026-09-26), its four listed patches still open

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

**Status:** Implemented, in review (`workflow-controller-adaptive-test-sharding`, plan
`docs/ai-workflow/CONTROLLER_ADAPTIVE_TEST_SHARDING_PLAN.md`, ADR
`docs/adr/0005-adaptive-test-sharding.md`). The operator requested it directly, ahead of 1.4,
which stays the next roadmap item.

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

Left for later: failing a run on a leaked process (D7), refreshing the committed timing profile
from real CI runs, and the `OwnershipTest` first-sighting `source` race that needs a Controller
decision (CP5 notes).

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

**Status:** One urgent correctness hotfix complete in this slot (`workflow-controller-worker-lifecycle-ownership`,
accepted 2026-09-26 under Workflow 2.5.1; the narrative is archived at
`docs/milestones/completed/workflow-controller-worker-lifecycle-ownership.md`). The four patches
below remain open and are the next milestone to plan (1.6 waits for the Workflow 2.6.x release)

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
name) are in ADR 0004. It was not folded together with the patches below, which stay listed:

- correct misordered `--work-item` resume hints;
- improve manual-external gate behavior when local review ledger/content is incoherent;
- add explicit abandoned/unreconcilable apply-review relaunch-bound tests;
- improve active-job/status presentation while observability work is already touching runtime diagnostics.

---

# 1.5 Trunk Branch / PR / Release Orchestration

**Priority:** Immediate / High

**Status:** Complete (`workflow-controller-trunk-branch-pr-release-orchestration`, accepted 2026-09-25 under Workflow 2.5.1). The narrative is archived at `docs/milestones/completed/workflow-controller-trunk-branch-pr-release-orchestration.md`. The first automatic release (1.2.0, README "Runbook: the first automatic release") is the supervised rollout still to run.

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
- `pyproject.toml` is the single human-maintained Controller version authority;
- version change on trunk drives one deterministic release transaction;
- validate/build/verify before tag creation;
- immutable tags;
- resumable partial publication only when the tag identifies the expected validated commit;
- no automatic PR merge;
- generic Git / forge operations stay outside Workflow lifecycle semantics.

This milestone must finish independently under Workflow 2.5.1. It must not wait for Workflow 2.6.x.

## 1.6 Post-Workflow-2.6 compatibility integration

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
- every refusal happens before pipx runs: a `source` runtime, `status` not `active: none`, `gh` unavailable, an unknown version, and a downgrade across a generation (`controller/GENERATION.json`) without an explicit flag, because the older generation refuses the newer one's job records (`StaleJobRecordError`, see the README's rollback notes);
- requesting the already-installed version is a no-op that says so;
- `doctor` (7.3) reports release/update status by reusing `update --check`.

Initially GitHub-only via `gh`; release discovery moves behind the forge adapter (7.6) when that boundary exists.

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

# Suggested Execution Order

```text
0. Automatic lifecycle orchestration                         COMPLETE
   |
1. Release/runtime isolation + live observability            COMPLETE (1.1-1.3)
   |
1.5 Trunk/branch/PR/release orchestration                    COMPLETE
   |                    \
   |                     \  Workflow Manager 2.6 hardening runs in parallel
   |                      \
1.6 Controller <-> released Workflow 2.6 integration         AFTER BOTH COMPLETE
   |
1.7 Workflow Orchestration Protocol decoupling
   |
2. RepFlow disposable migration / real migration validation
   |
3. Harness Adapter Protocol + multi-harness/model portability
   |
4. Forge Adapter maturation + PR-review/remediation loop
   |
5. Structured Observation API + web dashboard + analytics
   |
6. Concurrency / multi-worktree / worker leases
   |
7. Operator UX / diagnostics
   |
8. Routing / cost-efficiency / usage-aware scheduling
   |
9. Orchestrator / escalation layer
   |
10. Ongoing quality / maintenance
```

The exact numeric headings above remain historical roadmap sections; this execution order is the dependency-oriented target sequence.

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
