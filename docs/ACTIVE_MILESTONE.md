# Active Milestone

## Milestone

`workflow-controller-generation-1` — Controller Generation 1.

Work-item facts fixed by the outer supervisor before planning (do not
re-derive these; they encode constraints discovered by inspecting the
frozen Workflow v2.3.1 installation in this repository):

- `work_item_id`: `workflow-controller-generation-1`
- `work_item_type`: `product`
- `work_item_kind`: `product`
- `governing_workflow_version`: `2.1` (from `WORKFLOW_CONFIG.json`'s
  current `default_workflow_version`; gives the two-stage
  local-then-manual-external plan review)
- `plan_path`: `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`
- `registry_path`: `docs/ai-workflow/registry/workflow-controller-generation-1-registry.json`
- `mapping_path`: `docs/ai-workflow/requirements/workflow-controller-generation-1-mapping.json`
- `base_commit`: current `main` HEAD at planning time

## Goal

Build **Controller Generation 1**: the smallest robust Controller that can
safely supervise Workflow-driven work, and that is strong enough to become
the outer orchestrator for subsequent Controller generations.

The long-term architecture is:

```
Workflow  ->  Workflow Manager / Bootstrapper  ->  Workflow Controller  ->  managed development repositories
```

The Controller **automates operation of** the Workflow. It does not replace
Workflow lifecycle semantics or Workflow Manager responsibilities. These
four concerns stay separate and must not be collapsed:

| Layer | Owns |
|---|---|
| Workflow | legal lifecycle states and transitions |
| Workflow Manager | distribution, install, update, verify |
| Controller | orchestration and job execution |
| Claude workers | bounded reasoning and execution |

Generation 1 does **not** need to be the final Controller. It must be real,
tested, restartable, review-converged, and capable of supervising fresh
Claude Code workers while preserving Workflow state and human approval
boundaries.

## Required capabilities

### 1. Managed repository inspection

Accept a local repository path. Before doing anything else: integrate with
Workflow Manager status/verify; refuse unmanaged repositories; refuse
invalid or drifted Workflow installations where operation would be unsafe;
record the installed Workflow version. Do not reimplement Workflow Manager
install/update/drift semantics.

### 2. Workflow state reader

Read the target's authoritative Workflow state and identify the active work
item, its type/kind, current phase, checkpoint state, review state and
approval state. Fail closed on malformed, ambiguous or unsupported state.
Never repair Workflow state directly.

### 3. Next-action decision engine

A bounded phase -> next-action mapping derived from frozen Workflow
v2.3.1's actual command/lifecycle contract. Every decision must be
explainable: observed phase, evidence, selected action, automatic vs
manual, reason. Unsupported states fail closed with a useful explanation
rather than guessing.

### 4. Fresh worker orchestration

A worker abstraction that launches a separate fresh Claude Code process,
sets the target repository, gives it one bounded task, waits synchronously
for completion, and captures result/exit status/output sufficient for
Controller decisions — distinguishing success, failure, interruption and
ambiguity. The supervising Controller process stays alive and retains its
own state while the worker runs.

**Proven mechanism** (validated by the outer supervisor before planning):

```
claude -p "<bounded task or /slash-command>" \
  --output-format json \
  --permission-mode <acceptEdits|bypassPermissions> \
  < /dev/null
```

Run with `cwd` set to the target repository. Exit code plus the JSON result
object supply everything the Controller needs: `session_id`, `is_error`,
`subtype`, `terminal_reason`, `stop_reason`, `result`, `num_turns`,
`permission_denials`, `total_cost_usd`, `duration_ms`. Project slash
commands **do** resolve and fully execute in `--print` mode; redirect stdin
from `/dev/null` to avoid a 3s stdin wait. Worker permission posture:
`acceptEdits` against this repository, `bypassPermissions` against
disposable throwaway repositories.

### 5. Real Workflow-action execution

Prove the Controller can use a fresh worker to perform at least one genuine
Workflow-owned operation in a **disposable** managed repository, producing a
durable Workflow state change the Controller observes afterward. The
Controller must record pre-state, launch the worker, wait, re-read the
repository, validate post-state, and refuse to claim success if the expected
transition did not happen.

### 6. Human-gate detection

Distinguish automation-safe work from manual/user gates. Never fabricate
manual external review, user plan approval, technical approval, or
functional/user acceptance. At a human gate, stop cleanly and report:
repository, work item, phase, what is required, bundle/artifact path if
relevant, and the safe resume command. Never use `USER_OVERRIDE` merely to
achieve autonomy.

Frozen Workflow v2.3.1 has exactly **six** hard gates
(`MILESTONE_WORKFLOW.md`, "Hard gates summary"):
`AWAITING_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL`,
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `AWAITING_TECHNICAL_APPROVAL`,
`AWAITING_FUNCTIONAL_REVIEW`, `AWAITING_USER_ACCEPTANCE`. For a `"2.1"`
item the plan edge is refined into `AWAITING_LOCAL_PLAN_REVIEW` ->
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` -> `AWAITING_PLAN_APPROVAL`.
`/approve-review` and `/accept-milestone` carry
`disable-model-invocation: true` — only the user can invoke them.

### 7. Durable resume

Prove the Controller can start a job, persist state, be interrupted/exited,
restart, reconcile Controller job state against authoritative Workflow/Git
state, and continue or fail closed appropriately. It must not blindly
replay an operation that already succeeded before an interruption.

### 8. Failure handling

Prove: worker non-zero exit is not success; worker termination/interruption
is not success; a missing expected Workflow transition is not success;
malformed state fails closed; unmanaged/drifted repositories fail closed;
stale Controller execution metadata is reconciled or explicitly rejected.

### 9. Generation handoff primitive

A running Controller generation must **never** mutate or reload the
implementation it is currently executing. Every run is pinned to an
immutable Controller source identity, preferably a Git commit.

If generation A supervises development that moves the workflow-controller
repository from commit A to B/C, then A continues under A's code and rules;
does not import or reload source from B/C; does not update its active
execution environment in place; does not `git pull`, reinstall or
dynamically reload changed Controller code. Once a new generation is
approved, A records a durable handoff and exits; a fresh process starts
from the new approved generation and validates existing Workflow and
Controller job state before resuming.

**Restart is the upgrade mechanism. Hot reload is forbidden.** No
`importlib.reload`, no in-place package upgrade of the running Controller
environment, no source-change auto-reload.

Generation A must detect that the repository now contains a different
approved Controller source identity and: keep executing A until its current
orchestration boundary; persist a pending-handoff record; stop
intentionally; provide enough information to start the next generation;
never silently adopt the new code. Prove this in a controlled test.
Generation 1 need not autonomously spawn Generation 2, but the handoff
contract must be real and tested.

### 10. Minimal CLI

A practical CLI with the equivalent of: inspect/status; run/step; resume;
explain/waiting. Exact command names are an implementation choice. Do not
build a daemon, GUI, web service, remote fleet scheduler or cloud worker
system.

## Execution isolation and Controller-owned state

Design Controller execution so its running generation stays stable while a
target repository changes: a pinned runtime worktree/check-out, a packaged
immutable source snapshot, or another explicit immutable-generation
mechanism. The Controller records the exact source identity it started
from, and its operational state must not depend on source files currently
being edited by its workers.

Workflow state remains authoritative for Workflow lifecycle state.
Controller-owned orchestration/job state is persisted **separately** and
must never replace `WORKFLOW_STATE.json`. It must be sufficient to answer:
controller generation/source identity; target repository; active work item;
observed Workflow phase before the action; selected action; worker/session
identity if available; worker result; observed Workflow phase afterward;
whether execution stopped normally, failed, or was interrupted; whether a
human gate is pending; whether a generation handoff is pending.

Controller runtime state lives outside tracked development source, in an
ignored runtime directory, so Controller development never overwrites its
own execution record.

## Repository layout for this milestone

| Path | Role |
|---|---|
| `controller/` | Controller package source (implementation deliverable) |
| `tests/` | automated tests (implementation deliverable) |
| `pyproject.toml` | packaging and CLI entry point (implementation deliverable) |
| `.controller/` | Controller-owned runtime state — **gitignored**, never tracked |

## Mandatory artifacts-declaration constraints

**This is load-bearing and must be handled during `SELF_REVIEWING_PLAN`,
before the plan bundle is generated and before any approval exists.**

`generate_artifacts_declarations(..., work_item_type="product")` emits a
template whose deliverable trees are RepFlow-Android-shaped (`app/`,
`gradle/`, `config/`, `docs/adr/`). **None of this milestone's own
deliverable paths are classified by that template**, at either stage. Any
unclassified changed path raises `UnclassifiedPathError` and fails closed.
The plan-stage projection is recomputed *all the way through
implementation*, so an unclassified deliverable breaks the plan-stage
freshness check mid-implementation, after the plan-approval hard gate has
already advanced durable state (this is exactly salvage audit `B4`/`I8`).

Therefore `docs/ai-workflow/registry/workflow-controller-generation-1-artifacts.json`
must be edited before bundle generation so that:

- `plan_stage.excluded_prefixes` gains `controller/` and `tests/`, and
  `plan_stage.excluded_paths` gains `pyproject.toml` — implementation
  content, not plan-stage design content;
- `implementation_stage.protected_prefixes` gains `controller/` and
  `tests/`, and `implementation_stage.protected_paths` gains
  `pyproject.toml` — this milestone's actual deliverable, which
  `technical_approval` must bind.

Every entry needs a real one-line justification. Do **not** widen an
exclusion to avoid a re-review. `.claude/commands/` and `scripts/` stay
**excluded** at both stages: they are workflow-manager-managed frozen
Workflow v2.3.1 files that this milestone must never modify, and a
workflow-manager update to them must never stale this item's approvals.

## Testing requirements

At minimum, automated tests must cover:

1. managed repository verification;
2. unmanaged repo refusal;
3. drift/invalid install refusal;
4. state parsing;
5. malformed/ambiguous state refusal;
6. supported phase -> action mapping;
7. manual gate detection;
8. worker launch success;
9. worker failure;
10. worker interruption;
11. pre/post Workflow state validation;
12. durable resume after interruption;
13. no replay of an already-completed action;
14. immutable generation identity;
15. Controller source changes while the old generation keeps running unchanged;
16. generation handoff;
17. Controller-owned runtime state surviving source-repository modification;
18. disposable managed-repository real Workflow action.

Do **not** use RepFlow as the primary Generation 1 integration fixture.

## Explicitly out of scope

Generation 2; broad Controller lifecycle automation; review-convergence
automation; RepFlow orchestration; remote fleet management; daemons; cloud
workers; GUIs; web services.

## Acceptance criteria

Generation 1 is complete only when all of the following hold: the
Controller is a real executable tool, not an experiment; repository
inspection works; Workflow state interpretation works; a next-action
decision is explainable; a fresh Claude worker can be spawned and awaited;
a genuine Workflow action is executed through that worker; the Controller
verifies the resulting state transition; failure and interruption are
handled; durable restart/resume is proven; human gates are detected and
respected; the running Controller is pinned to an immutable source
identity; source changes cannot hot-reload into the running generation;
explicit generation handoff is implemented and tested; automated tests are
green; disposable integration evidence is green; the final fresh review
returns 0 Blocking / 0 Important; Workflow milestone acceptance completes;
the repository is clean and documented.

## Current checkpoint

`CP1` (package skeleton, packaging, ignored runtime layout, refusal
taxonomy, and the pinned immutable Controller source identity) is
**complete**, verified by `python3 tests/test_runtime.py`,
`python3 tests/test_identity.py` and `python3 tests/test_package_structure.py`
(43 tests total, all green). Delivered: `pyproject.toml`;
`controller/__init__.py`, `__main__.py`, `errors.py`, `runtime.py`,
`identity.py`, `cli.py` (skeleton -- `inspect`/`explain`/`step`/`run`/
`resume` are wired to their real behaviour by CP2-CP9; `status` is fully
functional); `controller/GENERATION.json`; the `.gitignore` entry for
`.controller/`; and `tests/fixtures.py`, `tests/test_runtime.py`,
`tests/test_identity.py`, `tests/test_package_structure.py`.

`CP2` (managed-repository inspection: Workflow Manager `status`/`verify`
integration, unmanaged/drifted/unsupported-installation refusals) is
**complete**, verified by `python3 tests/test_managed_repo.py` (18 tests,
all green, including two real-`workflow-manager` integration cases run
against this repository's own live installation and a real `bootstrap
--profile runtime` fixture -- neither skipped, since a real
`workflow-manager` is installed in this environment) and by re-running
`tests/test_runtime.py`, `tests/test_identity.py` and
`tests/test_package_structure.py` (no regressions; 43 tests, all green).
Delivered: `controller/managed_repo.py` (`inspect(path, *,
manager_bin=None) -> ManagedRepository`, the five ordered fail-closed
checks: not-a-repository, unmanaged, malformed manifest, unavailable
Manager executable, drifted installation with `verify`'s exit code as the
sole admission signal and `status`'s as a corroborating one, and the
closed `SUPPORTED_INSTALLATIONS = {("2.3.1", "runtime"), ("2.3.1",
"full")}` `(workflow_version, profile)` admission set); seven new
`controller/errors.py` refusals (`NotARepositoryError`,
`UnmanagedRepositoryError`, `MalformedInstallationManifestError`,
`WorkflowManagerUnavailableError`, `DriftedInstallationError`,
`UnsupportedWorkflowVersionError`, `UnsupportedInstallProfileError`);
`tests/test_managed_repo.py`; `tests/fixtures.py` extended with
managed-repository and stub-`workflow-manager` fixture builders;
`controller/__init__.py`'s eager-import literal gains `managed_repo`
between `identity` and `cli`. `controller/cli.py`'s `inspect` command
stays unwired (`NotImplementedError`) -- it needs CP3's target-state
reader too, per CP1's own comment, and CP3 is not yet implemented.

Next: `CP3` (read-only Workflow state reader: active work item, phase,
checkpoints, review and approval state, fail-closed on malformed or
ambiguous state), depends on `CP1`.

## Current blockers

None.

## Active plan

`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md` (to be created by
`/milestone-plan`, which records the plan document path on the work item's
`WORKFLOW_STATE.json` entry).

## Functional review checklist

Empty. `/prepare-functional-review` writes the numbered checklist for the
active work item into this section; `/apply-functional-review` and
`/accept-milestone` read it back from here.

<!--
This file is `workflow_state.FUNCTIONAL_CHECKLIST_PATH`. It is
repository-local state: workflow-manager generates it once at bootstrap and
never overwrites it on update.
-->
