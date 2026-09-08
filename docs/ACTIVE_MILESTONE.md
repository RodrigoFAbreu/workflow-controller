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

`CP3` (read-only Workflow state reader: active work item, phase,
checkpoints, review and approval state, fail-closed on malformed or
ambiguous state) is **complete**, verified by `python3 -m unittest
tests.test_target_state` (34 tests, all green, including two integration
cases run against this repository's own real `WORKFLOW_STATE.json` and
registry) and by re-running the full suite (`python3 -m unittest discover
-s tests`: 95 tests, all green, no regressions). Delivered:
`controller/target_state.py` (`read(managed_repo) -> WorkflowSnapshot`,
producing frozen `WorkflowSnapshot`/`WorkItemView` dataclasses;
`select_work_item(snapshot, *, work_item_id=None) -> WorkItemView`
implementing the explicit-override / `active_work_item_id` /
sole-non-terminal-item / `AmbiguousWorkItemError` selection order; the
closed, literal, test-verified-equal-to-frozen-Workflow-v2.3.1
seventeen-member `KNOWN_PHASES` set; the three-outcome
`registry_complete` derivation -- `None` with no declared
`registry_path`, `True`/`False` for a resolvable self-consistent
registry, `MalformedTargetRegistryError` for an unresolvable/unreadable/
unparseable/cross-linked one -- deliberately never reproducing the
out-of-scope `StalePlanApprovalRegistryReadError` check; and
`incomplete_children`'s reverse lookup over sibling work items). Five new
`controller/errors.py` refusals (`MissingWorkflowStateError`,
`MalformedWorkflowStateError`, `UnknownPhaseError`,
`AmbiguousWorkItemError`, `MalformedTargetRegistryError`).
`tests/test_target_state.py`, including an AST-scan structural proof that
the module contains no writing call and defines no write function.
`tests/fixtures.py` extended with target-state fixture builders.
`controller/__init__.py`'s eager-import literal gains `target_state`
between `managed_repo` and `cli`. `controller/cli.py`'s `inspect` command
still stays unwired -- CP3 delivers only the reader module the plan
names for this checkpoint's own files; wiring it into `cli.py` is CP4's
and later checkpoints' concern, not restated here.

`CP4` (next-action decision engine part 1: the phase -> action mapping
over frozen Workflow v2.3.1's seventeen phases, the user-only denylist,
and the explainable `Decision` shape) is **complete**, verified by
`python3 -m unittest tests.test_decision` (25 tests, all green) and by
re-running the full suite (`python3 -m unittest discover -s tests`: 120
tests, all green, no regressions). Delivered: `controller/decision.py`
(`decide(managed_repo, snapshot, work_item) -> Decision`, the frozen
`Decision`/`Action`/`HumanGate` dataclasses; the phase -> action mapping
over all seventeen known phases -- the two pure-automatic phases
(`PLANNING`, `REVISING_PLAN`), the five report-only phases split into the
two automation-safe `declined=True` ones (`IMPLEMENTING`,
`SELF_REVIEWING_IMPLEMENTATION`) and the three genuine human gates
(`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `APPLYING_REVIEW_FEEDBACK`,
`AWAITING_FUNCTIONAL_REVIEW`), the `AWAITING_PLAN_APPROVAL` user-only gate,
`LEGACY_READY`'s and `MILESTONE_COMPLETE`'s own inert outcomes, and the
four vocabulary phases raising `NoSupportedActionError`; the
qualified-literal `workflow_state.validate_...confirmation` recogniser
(`carries_user_confirmation_guard`), the command-file partition property
(`classify_command_files`, enumerating the target repository's own
`.claude/commands/*.md` fresh at call time -- never a copied list) and the
derived three-command `USER_ONLY_COMMANDS` set
(`derive_user_only_commands`); the closed, literal, two-directionally-
tested-equal-to-`target_state.KNOWN_PHASES` seventeen-member
`decision.KNOWN_PHASES` copy). Two new `controller/errors.py` refusals
(`NoSupportedActionError`, `HumanGateError`). `tests/test_decision.py`,
covering the table-driven per-phase mapping, the two-directional
known-phase-set equality, the scope assertion (`automatic=False` at the
five report-only phases, `automatic=True` at the six automatic triples),
the two-shape assertion (gate-bearing vs. declined), the user-only
denylist derivation (including the proxy-derivation-yields-two pin and
both discriminating-recogniser fixtures), the command-file partition
property (including the sixteenth-file negative case), and the
never-returns-a-user-only-command total assertion.
`tests/fixtures.py` extended with `build_work_item_view`,
`copy_real_commands_dir` (this repository's own `.claude/commands/` is
itself a live frozen-v2.3.1 fifteen-file installation, reused as the
fixture) and `write_command_file`. `controller/__init__.py`'s eager-import
literal gains `decision` between `identity` and `managed_repo`, matching
the plan's dependency order.

**Three phases remain provisional pending CP4B** (`AWAITING_LOCAL_PLAN_REVIEW`,
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_EXTERNAL_PLAN_REVIEW` on a
`"1"`-governed item): `decide()` resolves each to its ordinary-case
automatic row without reading any `.ai-review/` evidence, since nothing in
this checkpoint's own dependency graph (`job`/`cli`, which depend on
CP4B's `evidence`, never on `decision` directly) acts on a `Decision` yet.
Each such `Decision.reason` states this explicitly. `HumanGate.artifact_path`
is `None` on every gate CP4 produces for the same reason -- CP4B's
`evidence.py` owns the plan-stage/implementation-stage `<bundle_dir>`/
`<feedback_dir>` resolution.

`CP4B` (next-action decision engine part 2: evidence-reading
disambiguations -- feedback role and status, checklist trailer, consumed
marker, incomplete children -- and human-gate classification) is
**complete**, verified by `python3 -m unittest tests.test_evidence` (40
tests, all green) and by re-running the full suite (`python3 -m unittest
discover -s tests`: 160 tests, all green, no regressions). Delivered:
`controller/evidence.py` (`decide(managed_repo, snapshot, work_item) ->
Decision`, CP4B's own entry point -- checks the withdrawn-`REJECTED`-
bundle outcome ahead of every other row for every bundle-bearing phase,
resolves the three phases CP4 left as an ordinary-case placeholder
(`AWAITING_LOCAL_PLAN_REVIEW`, `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`,
`AWAITING_EXTERNAL_PLAN_REVIEW` on a `"1"`-governed item) with a real
evidence read, sharpens the two evidence-driven report-only phases
(`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`'s four sub-cases including the
`generation_head`-behind-`HEAD` -> `/recover-implementation-provenance`
case, and `AWAITING_FUNCTIONAL_REVIEW`'s four sub-cases via the
`Workflow-Functional-Checklist` trailer, the `FUNCTIONAL_REVIEW.consumed`
marker, and `incomplete_children`), and falls through unchanged to
`controller.decision.decide` for every phase needing no `.ai-review/`
read). Implements: the provenance-block text model
(`provenance_block`/`read_labelled_line`, matching only a line inside the
block before the first `## ` heading, so a body section quoting a label
as prose is never read); the two-rule `<bundle_dir>` resolver
(`resolve_bundle_dir`, unconditionally scoped at the four plan-stage
phases with no existence gate, scoped-else-flat elsewhere) and the
stage-less scoped-else-flat `<feedback_dir>`/`REJECTED`-marker resolvers
(`resolve_feedback_dir`, `resolve_rejected_marker_path`); the
manual-stage and `"1"`-path plan-review admissibility clause tables
(`evaluate_manual_stage_admissibility` -- hard on role/`Status:`/the
three `WFR-03` fields'/`review_content_id`/local-approval/
`generation_head` clauses, advisory only on `Reviewed bundle ID:`;
`evaluate_apply_plan_review_admissibility` -- `Reviewed bundle ID:` hard
instead, no role/content-id reads); the local stage's own BLOCK
exemption (role+`Status:` alone, no admissibility rule, so a genuine
BLOCK with one malformed binding field still gates rather than
launching); the round-scoped `Workflow-Functional-Checklist` trailer
reader (`functional_checklist_evidence`, a first-parent `git log` walk
within `base_commit..head`) and the `FUNCTIONAL_REVIEW.md`/
`FUNCTIONAL_REVIEW.consumed` presence-then-content-hash reader
(`functional_review_findings_consumed`). No new `controller/errors.py`
refusal was needed -- every evidence-reading disambiguation this
checkpoint owns resolves to `Decision` content (a gate naming the
failing clause, never a raised exception), matching the plan's own
per-checkpoint refusal-taxonomy summary, which names none for CP4B.
`controller/__init__.py`'s eager-import literal gains `evidence` between
`target_state` and `cli`, matching the plan's dependency order.
`tests/test_evidence.py` (40 tests) covers: the text model; both
`<bundle_dir>` rules including the plan-stage existence-gate fixture;
the `REJECTED` marker at both layouts and its withdrawn-bundle-first
integration into `decide()`; `AWAITING_LOCAL_PLAN_REVIEW`'s ordinary/
BLOCK/exemption/loop-closure cases; `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`'s
arrival test (round 3's B1 negative), admissibility hard clauses (round
38's `EXT-PLAN-R38-B1` discriminating fixture), the advisory `bundle_id`
case (`EXT-PLAN-R38-I1`), the legacy lowercase role spelling, and BLOCK;
`AWAITING_EXTERNAL_PLAN_REVIEW`'s (`"1"`) three sub-cases including the
hard-`bundle_id` contrast with the manual column; both implementation-
stage and functional-review report sub-cases. `tests/fixtures.py` gains
`build_target_git_repo`/`commit_all`/`current_head` (a real Git
repository fixture `controller.evidence`'s own `git rev-parse`/`git log`
calls need, distinct from CP2's manifest-only fixture) and
`write_review_feedback`/`build_review_feedback_text`/`write_manifest`/
`build_manifest_text`/`write_rejected_marker`.

`CP5` (fresh Claude worker abstraction: bounded task launch, synchronous
wait, success/failure/interruption/ambiguity classification) is
**complete**, verified by `python3 -m unittest tests.test_worker` (17
tests, all green) and by re-running the full suite (`python3 -m unittest
discover -s tests -p "test_*.py"`: 177 tests, all green, no regressions).
Delivered: `controller/worker.py` (`launch(task, *, cwd, permission_mode,
timeout, claude_bin=None) -> WorkerResult`, running exactly the proven
mechanism above via `subprocess.Popen` with `stdin=subprocess.DEVNULL`,
`start_new_session=True` and an explicit `env=` built from `os.environ`
with `PYTHONPATH` removed; the frozen `WorkerResult` dataclass carrying
`outcome` plus the nine named JSON fields (`session_id`, `is_error`,
`subtype`, `terminal_reason`, `stop_reason`, `result`, `num_turns`,
`permission_denials`, `total_cost_usd`, `duration_ms`), raw `stdout`/
`stderr`, and `raw_json`; the fixed four-outcome classification order
(`INTERRUPTED` on a timeout or negative return code, checked first;
`FAILURE` on non-zero exit or exit-0-with-`is_error:true`; `AMBIGUOUS` on
exit 0 with an undecidable body; `SUCCESS` otherwise); `REQ-26`'s
worker-stdout JSON candidate-span rule (`_parse_worker_stdout` -- exactly
one trailing newline stripped, the whole remaining span parsed as exactly
one JSON document via `json.loads`'s own "no trailing data" behaviour, a
non-UTF-8 byte tolerated by `errors="replace"` at decode time rather than
raising); a timeout's `os.killpg` of the worker's own process group
(never only the direct child, since `communicate()`'s own timeout
handling kills nothing); and the second, independent denylist layer
(`USER_ONLY_COMMANDS`, a literal three-name copy kept deliberately
separate from CP4's `decision.derive_user_only_commands`, plus the
leading-`` ` ``/`/`-and-trailing-punctuation text model and whole-token
equality check, raising `UserOnlyCommandError` before any subprocess is
spawned). Five new `controller/errors.py` refusals (`WorkerLaunchError`,
raised when the subprocess itself cannot start; `WorkerFailedError`,
`WorkerInterruptedError`, `WorkerAmbiguousResultError`, reserved for CP6's
own use against a completed `WorkerResult`; `UserOnlyCommandError`).
`controller/__init__.py`'s eager-import literal gains `worker` between
`evidence` and `cli`, matching the plan's dependency order.
`tests/fake_claude.py`, a small stdlib script (env-var-driven, since
`launch()`'s argv shape is fixed) standing in for the real `claude`
binary: emits a chosen stdout/exit code, hangs (optionally spawning a
tracked grandchild to prove the whole process group is reaped, not only
the direct child), or sends itself `SIGTERM`; also writes a diagnostic
record (`cwd`, whether stdin was already at EOF, `PYTHONPATH`) so the
launch-mechanics cases need not depend on the case under classification
test. `tests/test_worker.py` (17 tests) covers every case the plan names:
success with fields extracted; non-zero exit; exit-0-with-`is_error:true`;
non-JSON stdout, a JSON array, missing required fields, and two
concatenated JSON documents (`REQ-26`) all classifying `AMBIGUOUS`; a
hung worker under a short timeout classifying `INTERRUPTED` with its
process-group grandchild confirmed reaped; a self-`SIGTERM`-ed worker
classifying `INTERRUPTED`; all three `USER_ONLY_COMMANDS` names refused
(bare, backticked, and sentence-final-punctuated) with no process spawned
in the bare case, plus a substring-of-a-name negative case; `cwd`, closed
`stdin`, and the absent `PYTHONPATH` all confirmed from the worker's own
observed view; and a nonexistent `claude_bin` raising `WorkerLaunchError`.
The real `claude` binary is deliberately not exercised here -- CP9's
opt-in integration test is the only call site that does.

`CP6` (job execution part 1: durable Controller-owned job records,
pre-state capture, persist-before-launch, worker launch and result
recording) is **complete**, verified by `python3 -m unittest tests.test_job`
(11 tests, all green) and by re-running the full suite (`python3 -m
unittest discover -s tests -p "test_*.py"`: 188 tests, all green, no
regressions). Delivered: `controller/job.py` (`execute_step(managed_repo,
*, work_item_id=None, identity, runtime, permission_mode="acceptEdits",
timeout=None, claude_bin=None) -> JobRecord | Decision`, the single
function CP6B/CP7 extend in place rather than replace -- **CP6 owns steps
1-6**: inspect and capture `pre_state` per the single `PRE_STATE_FIELDS`
declaration (sixteen fields, including the two Controller-owned
derivations this checkpoint newly implements -- `bundle_generated_digest`,
a SHA-256 over the generator-written subset of `<bundle_dir>`, and
`functional_review_consumed_blob`, `FUNCTIONAL_REVIEW.md`'s current Git
blob hash); decide (`controller.evidence.decide`) under the **positive**
launch guard -- a worker is launched only when `decision.automatic` is
`True`, so a gate, a decline, and the two-member no-action class
(`LEGACY_READY`/`MILESTONE_COMPLETE`, which writes no job record at all
and returns the bare `Decision`) each short-circuit before any launch is
considered; a pending-generation-handoff check reading
`<runtime_root>/handoff.json` directly through `runtime.read_json` (CP8's
own `handoff` module does not exist yet); the job record written in two
flushes -- `PLANNED` (the full identity block, `pre_state`, and
`selected_action`, deliberately omitting `worker_outcome`, `worker`,
`observed_phase_after`, `transition_verified` and `expected_transition`)
then `LAUNCHED` (adding `expected_transition` only, looked up from a
closed six-row `to_any_of` table transcribed from the plan's own CP6B
`ExpectedOutcome` table) -- both flushed before the worker is spawned;
`worker.launch`; and the `COMPLETED` record, which also durably captures
the worker's raw stdout/stderr under
`<runtime_root>/jobs/<job_id>/worker.std{out,err}` via a new
`runtime.write_bytes` (added alongside `runtime.write_json`, sharing its
atomic write/fsync/replace/containment-guard mechanics through a new
private `_atomic_write` helper). `tests/test_job.py` (11 tests): the
no-action class returns a `Decision` with the jobs directory left
unchanged; a manual-gate phase (`APPLYING_REVIEW_FEEDBACK`) and a
declined report-only phase (`IMPLEMENTING`) each write their own
single-flush record with no worker process spawned (asserted via
`FAKE_CLAUDE_DIAG_FILE`'s absence); a pending handoff pre-empts an
otherwise-automatic `PLANNING` decision, again with no process spawned;
the `LAUNCHED` record is confirmed durable on disk *before* the worker
starts, from inside the worker itself via a new `FAKE_CLAUDE_REQUIRE_FILE`
env var added to `tests/fake_claude.py` for exactly this assertion; the
first three persisted `status` values for one `execute_step` are exactly
`["PLANNED", "LAUNCHED", "COMPLETED"]`, spied through
`controller.job.runtime.write_json`; the `PLANNED`-flush record is
asserted two-sidedly (carries `schema_version`/`controller_generation`/
`target_repo`/`work_item_id`/`status`/`selected_action` and
`set(pre_state) == PRE_STATE_FIELDS`; omits `worker_outcome`/`worker`/
`observed_phase_after`/`transition_verified`/`expected_transition` -- key
absent, not present-and-null) and the `LAUNCHED` flush is confirmed to add
only `expected_transition`; the final `COMPLETED` record's key set is
asserted exactly equal to every field CP6 itself owns; and
`controller_source_commit`/`controller_source_tree_digest` are recorded
exactly as the passed-in `identity` resolved them, including the `None`
commit a `"worktree"`-kind identity carries. `controller/__init__.py`'s
eager-import literal gains `job` between `worker` and `cli`, matching the
plan's dependency order (`job -> {managed_repo, target_state, evidence,
worker, handoff} -> decision -> {identity, runtime, errors}`).
`execute_step`'s own `identity`/`runtime` parameters deliberately shadow
this module's `controller.identity`/`controller.runtime` imports inside
that one function's body -- every helper that actually performs I/O is
defined at module scope instead, taking the runtime root as an explicit
`runtime_root` parameter, so `controller.job.runtime.write_json` stays the
one thing ever called and stays spyable. CP6's own slice of
`execute_step` ends at a `COMPLETED` record -- it does not yet read
post-state, decide `transition_verified`, or write `FINISHED`/`FAILED`/
`INCOMPLETE`; that is CP6B's extension of this same function.

Next: `CP6B` (job execution part 2: fresh post-state re-read and
expected-transition verification, with `FINISHED` written only after it
passes), depends on `CP6`.

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
