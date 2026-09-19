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
**complete**, revalidated after the B2 plan amendment (revision 71) against
the widened "Supported Workflow baseline" policy, verified by
`python3 -m unittest tests.test_managed_repo` (24 tests, all green,
including two real-`workflow-manager` integration cases run against this
repository's own live installation and a real `--release-version 2.5.1
bootstrap --profile runtime` fixture -- neither skipped, since a real
`workflow-manager` is installed in this environment). Delivered:
`controller/managed_repo.py` (`inspect(path, *, manager_bin=None) ->
ManagedRepository`, the five ordered fail-closed checks: not-a-repository,
unmanaged, malformed manifest, unavailable Manager executable, drifted
installation with `verify`'s exit code as the sole admission signal and
`status`'s as a corroborating one, and the **two-tier** admission rule
that replaces the old closed `SUPPORTED_INSTALLATIONS` set:
`SUPPORTED_WORKFLOW_LINE = "2.5"` (a necessary pre-filter/diagnostic
classifier), `VALIDATED_WORKFLOW_RELEASES = {"2.5.1"}` (the actual
admission gate, exact membership), `SUPPORTED_PROFILES = {"runtime",
"full"}`, `REFERENCE_WORKFLOW_RELEASE = "2.5.1"` (the derivation pin) --
each refusal's evidence carries a `reason` of `outside_supported_line` or
`unvalidated_release` so the two failure classes are never conflated);
`_read_manifest` now accepts an empty-string `workflow_version` as a
*declared* (if useless) value rather than a malformed manifest, so `""`
and `"latest"` both reach and fail the version-admission gate rather than
the manifest-shape gate, per the plan's stated baseline-predicate cases;
the baseline predicate's own seven cases (`REQ-T18B`: `2.5.1` admitted;
`2.3.1`/`2.4.0`/`2.6.0`/non-dotted refuse `outside_supported_line`; `2.5.0`
and a `2.5.2` fixture whose `.claude/commands/` tree and
`scripts/workflow_state.py` are byte-identical to the reference tree both
refuse `unvalidated_release`, without ever reaching a command-file or
`KNOWN_PHASES` read); seven `controller/errors.py` refusals unchanged in
name (`NotARepositoryError`, `UnmanagedRepositoryError`,
`MalformedInstallationManifestError`, `WorkflowManagerUnavailableError`,
`DriftedInstallationError`, `UnsupportedWorkflowVersionError`,
`UnsupportedInstallProfileError`), their docstrings updated to name the
new constants; `tests/test_managed_repo.py` rewritten for the two-tier
rule; `tests/fixtures.py`'s `build_managed_repo`/
`write_installation_manifest` defaults moved from `"2.3.1"` to `"2.5.1"`
(this repository's own real, validated installation) and a new
`build_workflow_line_fixture` helper added for the `REQ-T18B` case.
`controller/cli.py`'s `inspect` command stays unwired
(`NotImplementedError`) -- it needs CP3's target-state reader too, per
CP1's own comment, and CP3 is not yet revalidated.

**Residual, expected cross-checkpoint breakage from this revalidation**
(not fixed here -- out of CP2's own scope, left for each checkpoint's own
revalidation in registry order): `tests.test_target_state` and
`tests.test_decision` still fail against the stale seventeen-phase
`KNOWN_PHASES` copy and fifteen-file command partition (CP3/CP4's own
files; unaffected by this checkpoint, reproduces the "2 failures and 6
errors" the plan's own "Supported Workflow baseline" section measured);
and `tests.test_cli`'s two `InspectCommandTest` assertions
(`test_text_report_names_repository_and_phase`,
`test_json_report_carries_the_full_work_item_payload`) now observe
`Workflow 2.5.1` from the shared `fixtures.build_managed_repo` default
instead of the stale `"2.3.1"` they assert -- CP9's own file, to be
corrected at CP9's revalidation alongside its other baseline-dependent
assertions.

`CP3` (read-only Workflow state reader: active work item, phase,
checkpoints, review and approval state, fail-closed on malformed or
ambiguous state) is **complete**, revalidated after the B2 plan amendment
(revision 71) against the widened twenty-phase known-phase set and the
`NoWorkItemYet` bootstrap sentinel, verified by `python3 -m unittest
tests.test_target_state` (39 tests, all green, including two integration
cases run against this repository's own real `WORKFLOW_STATE.json` and
registry). Delivered: `controller/target_state.py` (`read(managed_repo)
-> WorkflowSnapshot`, producing frozen `WorkflowSnapshot`/`WorkItemView`
dataclasses; `select_work_item(snapshot, *, work_item_id=None) ->
WorkItemView | NoWorkItemYetType` implementing the explicit-override /
`active_work_item_id` / sole-non-terminal-item / **`NoWorkItemYet`
(zero candidates, no explicit id)** / `AmbiguousWorkItemError` (more than
one candidate, or an explicit id absent from `work_items`) selection
order; the closed, literal, test-verified-equal-to-the-installed-
reference-release **twenty**-member `KNOWN_PHASES` set (revision 64:
widened from seventeen by `AMENDING_PLAN`,
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`,
`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`); the `NO_PHASE`
sentinel and its single reserved durable form `NO_PHASE_WIRE =
"__NO_PHASE__"` (revision 64, round 63's B2 -- the one in-memory value
every "there is no phase" field will carry, declared once here for CP4/
CP6/CP7 to import by reference rather than reinventing); the three-outcome
`registry_complete` derivation -- `None` with no declared
`registry_path`, `True`/`False` for a resolvable self-consistent
registry, `MalformedTargetRegistryError` for an unresolvable/unreadable/
unparseable/cross-linked one -- deliberately never reproducing the
out-of-scope `StalePlanApprovalRegistryReadError` check; and
`incomplete_children`'s reverse lookup over sibling work items). Five
`controller/errors.py` refusals unchanged in name
(`MissingWorkflowStateError`, `MalformedWorkflowStateError`,
`UnknownPhaseError`, `AmbiguousWorkItemError`,
`MalformedTargetRegistryError`), `AmbiguousWorkItemError`'s docstring
updated to state the zero-candidate carve-out.
`tests/test_target_state.py` gained the `NoWorkItemYet`/`NO_PHASE`
sentinel tests and the zero-non-terminal-candidates case was rewritten
from an `AmbiguousWorkItemError` assertion to a `NoWorkItemYet` one,
alongside an AST-scan structural proof that the module contains no
writing call and defines no write function. `controller/cli.py`'s
`inspect` command still stays unwired -- CP3 delivers only the reader
module the plan names for this checkpoint's own files; wiring it into
`cli.py` is CP4's and later checkpoints' concern, not restated here.

**Residual, expected cross-checkpoint breakage from this revalidation**
(not fixed here -- out of CP3's own scope, left for each checkpoint's own
revalidation in registry order, unchanged in shape from CP2's own note):
`tests.test_decision` still fails against the stale seventeen-phase
`decision.KNOWN_PHASES` copy and the fifteen-file command-file partition
(CP4's own scope -- `tests.test_target_state` itself is green again, so
the equality assertion now names the three phases `decision.KNOWN_PHASES`
is missing rather than failing to import); `tests.test_cli`'s two
`InspectCommandTest` assertions remain red for the same `"2.3.1"`-vs-
`"2.5.1"` reason CP2's revalidation already measured (CP9's own scope).
`python3 -m unittest discover -s tests`: 343 tests, 4 failures + 6 errors,
all ten in `tests.test_decision`/`tests.test_cli`, none newly introduced
by this checkpoint and none in `tests.test_target_state`,
`tests.test_managed_repo`, `tests.test_runtime`, `tests.test_identity` or
`tests.test_package_structure`.

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

`CP6B` (job execution part 2: fresh post-state re-read and
expected-transition verification, with `FINISHED` written only after it
passes) is **complete**, verified by `python3 -m unittest
tests.test_job_validation` (33 tests, all green), by re-running `python3
-m unittest tests.test_job` (11 tests, all green -- two updated to inspect
the intermediate `COMPLETED` write via the existing `_WriteSpy` rather
than `execute_step`'s own return value, which is now the terminal
`FINISHED`/`FAILED`/`INCOMPLETE` record), and by re-running the full suite
(`python3 -m unittest discover -s tests -p "test_*.py"`: 221 tests, all
green, no regressions). Delivered: `controller/job.py` extended with
steps 7-9 of `execute_step` -- a fresh `target_state.read`/
`select_work_item` re-read (never the `snapshot`/`work_item` captured
before the worker ran; a second `managed_repo.inspect()` is deliberately
out of scope, documented in the module's own docstring, since nothing else
in the package threads a `manager_bin` through `execute_step` yet), the
`transition_verified` rule stated in full at step 8 (`worker_outcome` is
`SUCCESS` or `INTERRUPTED` -- stated as a **positive**, total guard, never
a negative one -- **and** the observed post-phase is in
`expected_transition.to_any_of`, **and**, when that phase equals the
row's own `from_phase`, the row's predicate holds against `pre_state`,
evaluated fresh against disk), and step 9's `INCOMPLETE` precedence
(defined and dispatchable via a per-triple `_INCOMPLETE_EFFECT_PHASES`
mapping, empty for every one of Generation 1's own six rows -- revision
10's plan narrowing removed its sole producer, `/apply-implementation-
review` reaching `APPLYING_REVIEW_FEEDBACK`, which is not one of this
generation's automatic actions). The plan's own `ExpectedOutcome` table is
transcribed as real, declared data (`ExpectedOutcome`, `WriterCall`,
`BranchSpec` dataclasses; `EXPECTED_OUTCOMES`, six rows) with two real
predicates (row 3: a current-round `REVIEW_FEEDBACK.md` with `Status:
BLOCK`, `Reviewer role: LOCAL_MODEL_PLAN_REVIEW` and a matching `Reviewed
bundle ID:`; row 5: a freshly recomputed `bundle_generated_digest` that
differs from `pre_state`'s own), replacing CP6's own `_EXPECTED_TO_ANY_OF`
lookup table. **Row 5's plan-declared open item is resolved**: its own
`WriterCall.branch` is restated to point directly at `apply-plan-
review.md`'s own step-5 span (a new `BranchSpec(kind="step", label="5")`
marker form) rather than the governing-version bullet in step 0 the plan
found no way to resolve a cross-reference into -- step 5 already runs
unconditionally on both governing-version branches, so its own numbered
span already contains the declared `prepare-ai-review.sh` call in full,
with no "steps N-M execute" cross-reference needed. Six properties are
implemented and tested: coverage and predicate presence/validity
(`job.property_table_violations`, structural, over the table alone),
record completeness (`job.property_record_completeness_violations`,
`predicate_inputs ⊆ PRE_STATE_FIELDS`), declaration against artifact
(`job.property_declaration_against_artifact_violations`, a real text scan
of this repository's own frozen `.claude/commands/*.md` -- locates each
row's declared branch, confirms the declared call inside it, and confirms
no *different* durable write follows it in the same span), pair-keyed
writer reachability (asserted via real `controller.evidence.decide` calls
from each row's own `(from_phase, governing_version)`, never a second
declaration), and completion (documented narratively, since "produced on
completion" vs. "producible by the action's writers" is not a distinction
the table's own data can decide without executing the real command).
`tests/test_job_validation.py` (33 tests): the real table passes every
property, and each property's own negative instantiation (a duplicate
triple, a predicate-bearing row with no `to_any_of` self-membership and
vice versa, a non-`COMPLETION` writer kind, a `predicate_input` outside
`PRE_STATE_FIELDS`, an unlocatable branch, a call absent from its branch, a
further different durable write after the call, an unreadable command
file) fails construction, naming the row; end-to-end `execute_step` cases
through a fake worker (`tests/fake_claude.py`, extended with a new
`FAKE_CLAUDE_WRITE_PATH`/`FAKE_CLAUDE_WRITE_TEXT` pair so a fixture can
make the fake worker itself perform the target repository's own expected
state edit) covering: a real successful transition reaching `FINISHED`
with the persisted `status` sequence exactly `["PLANNED", "LAUNCHED",
"COMPLETED", "FINISHED"]`; a worker that changes nothing, and one that
moves the phase somewhere else entirely, each `FAILED` with
`TransitionNotObservedError` evidence naming the expected set and the
observed phase; an `AMBIGUOUS` worker not verified even when the
post-state happens to match; an `INTERRUPTED` worker whose durable
transition already landed, verified; row 3's `BLOCK` case both ways (a
current-round `Status: BLOCK` feedback verifies; no feedback does not);
row 5's mid-action case (a worker that exits 0 without ever completing the
bundle regeneration does not verify) and its own positive case (a
regenerated bundle verifies); and the `INCOMPLETE`-takes-precedence
mechanism exercised directly against a synthetic effect-only landing,
since no real Generation-1 fixture can reach it. `tests/test_job.py`'s
own two affected assertions (`test_completed_record_contains_every_
schema_field_cp6_owns`, `test_launched_record_present_on_disk_before_
worker_starts`) were updated to inspect the intermediate `COMPLETED`
write via `_WriteSpy` rather than `execute_step`'s own return value, for
the reason above -- no other CP6 test needed a change, since CP6's own
`PLANNED`/`LAUNCHED` assertions and its `statuses[:3]` prefix check are
unaffected by anything CP6B appends after `COMPLETED`.

`CP7` (durable resume: restart reconciliation against authoritative
Workflow/Git state, no replay of an already-completed action, stale-
metadata rejection) is **complete**, verified by `python3 -m unittest
tests.test_resume` (37 tests, all green) and by re-running the full suite
(`python3 -m unittest discover -s tests -p "test_*.py"`: 258 tests, all
green, no regressions). Delivered: `controller/job.py` extended with
`resume(managed_repo, *, identity, runtime) -> list[JobRecord]` and its
own named four-case validation pass, `validate_record(record, *,
managed_repo, identity) -> Validity` (the plan's own shorthand signature
elides the two keyword parameters case 1/case 2 actually need). `resume`
loads every job record under `<runtime>/jobs/*.json` whose own
`target_repo` equals `str(managed_repo.root)` -- "for this target
repository" -- since the runtime root is resolved from the Controller's
own origin checkout (`cli._dispatch`'s `origin = ident.origin_source_root
or ident.source_root`), never from the target, so one runtime root can
carry job history for more than one target repository over time; a
record for a different target is left completely untouched, neither
raising nor appearing in the result. `validate_record`'s four cases, in
the plan's own stated order: (1) uninterpretable -- unknown
`schema_version`, or `controller_generation` newer than the running one;
(2) unresolvable subject -- `managed_repo.root` no longer a directory, or
`work_item_id` absent from a fresh `target_state.read`; (3) `worker_outcome`
disagreeing with the step that owns the record's status -- keyed on
presence for `PLANNED`/`LAUNCHED` (any value, recognised or not, is a
refusal) and on membership in the closed four-outcome set for `COMPLETED`;
(4) `status`/`selected_action.declined` derived-field disagreement. Every
case returns an invalid `Validity` (never raises) carrying `terminal`,
decided from the record's own `status` against the ten-member closed
enumeration (`job.NON_TERMINAL_STATUSES`/`job.TERMINAL_STATUSES`, newly
exported) -- round 8's I2 carve-out: `resume` raises
`StaleJobRecordError` (new `controller/errors.py` refusal) only for a
non-terminal (or unrecognised/absent-status, fail-closed) invalid record,
aborting the whole call; a terminal one is returned in the result
augmented with a `resume_marked` block (`outcome: "malformed"` for case
4, `"unreadable"` for case 1/2) and is never rewritten on disk. Terminal
records generally are reported, never reconciled, never rewritten. The
closed reconciliation table's three non-terminal rows: `PLANNED` always
reconciles to `INTERRUPTED` (row 1, "anything" -- no post-state read at
all, since nothing was ever launched); `LAUNCHED` reconciles via a fresh
post-state re-read and the shared `_row2_verified` rule (row 2's own
`worker_outcome`/`to_any_of`/predicate clauses, restated once for both
`LAUNCHED` and `COMPLETED` rather than by substituting a fake outcome
value) to `FINISHED` when verified (row 2, never relaunch), to
`INTERRUPTED` when the observed phase and target `HEAD` both match
`pre_state` (row 3), or else to a raised `UnreconcilableJobError` (new
`controller/errors.py` refusal; row 4, covering both "phase unchanged,
predicate unsatisfied, `HEAD` moved" and "phase moved outside
`to_any_of`" without needing to inspect which); `COMPLETED` reconciles via
the same rule to `FINISHED` (row 2) or to `FAILED` with
`TransitionNotObservedError` evidence naming the worker outcome and the
observed transition separately (row 5, "exactly as CP6B step 8 would
have"). A status outside the closed ten-member enumeration (including an
absent one on an otherwise well-formed record) is dispatched as
non-terminal per the plan's own stated routing and raises
`StaleJobRecordError` from the table's own unknown-status row -- never
reconciled as `PLANNED`, never surfaced as terminal. `resume` contains no
call to `controller.worker.launch` at all, so "never relaunch" is
structural, not a runtime guard -- proven directly in
`tests/test_resume.py` by patching `worker.launch` to raise if ever
called. `tests/test_resume.py` (37 tests): every validation-pass case,
both branches of the terminal carve-out (including the two "terminality
cannot be determined" fixtures at cases 1/2, and the two ordering-pin
fixtures proving case 1 preempts the reconciliation table entirely for a
`COMPLETED`/`PLANNED` record whose post-state would otherwise look like a
legitimate outcome); the reconciliation table's five non-terminal rows,
including row 5's own no-op-worker instantiation (a `"1"`-governed
`/apply-plan-review` `LAUNCHED` record with `plan_revision`/
`state_revision` deliberately advanced, proving the stronger
`bundle_generated_digest` predicate is what is actually consulted); the
unknown-status row from both an unrecognised-string fixture and an
absent-`status` first-flush fixture; target-repository scoping (a
foreign, unresolvable record is left untouched by `resume` against a
different target); the "a subsequent `step` runs normally" property; and
one true end-to-end test driving a real `execute_step` worker in a
separate process, `SIGKILL`-ed mid-run, confirming the `LAUNCHED` record
lands on disk before `resume` reconciles it to `INTERRUPTED`.

`CP8` (generation handoff primitive: pending-handoff record, intentional
stop, and enforced absence of hot reload in the running generation) is
**complete**, verified by `python3 -m unittest tests.test_handoff` (27
tests, all green, including one real end-to-end test that starts the
installed `workflow-controller` console script as a live subprocess,
pauses it at its own orchestration boundary via the test-only
`--pause-file`/`WORKFLOW_CONTROLLER_TEST_HOOKS=1` hook, bumps the origin
checkout's `controller/GENERATION.json` and an observable source file
while paused, unpauses, and asserts: exit code 50; `handoff.json` names
`running`/`approved` generation and commit correctly; the process's own
final `identity.json` still reports the *running* generation and commit,
never the newer ones; and the paused snapshot directory still verifies
against its own recorded tree digest and never picked up the observable
source change) and by re-running the full suite (`python3 -m unittest
discover -s tests -p "test_*.py"`: 285 tests, all green, no regressions).
Delivered: `controller/handoff.py` (`Handoff`, a frozen dataclass carrying
`from_generation`/`from_commit`/`to_generation`/`to_commit`/`source_root`;
`detect(identity, source_root) -> Handoff | None`, comparing the running
(pinned) generation against the approved generation read fresh from the
origin source repository's own committed `HEAD` -- **never** the
worktree, deliberately with no fallback, unlike `identity._read_generation`'s
own bootstrap-state fallback, since an uncommitted `GENERATION.json` edit
must never be read as an approved generation; three branches -- greater
returns a `Handoff`, equal returns `None` (the branch that keeps ordinary
in-generation development from tripping a handoff), lower raises
`GenerationHandoffPendingError`; and `write_handoff_record`, which
publishes `<runtime_root>/handoff.json` with exactly the milestone brief's
own declared fields: running/approved generation and commit, the source
root, the timestamp, the complete/open job-id lists (supplied by the
caller, since classifying a job record's own status is `controller.job`'s
closed enumeration and `job` is later than `handoff` in the package's own
dependency order), and the exact command to start the next generation).
One new `controller/errors.py` refusal, `GenerationHandoffPendingError`
(the revert case only -- the ordinary "source moved forward" handoff never
raises); `SourceSnapshotError`'s own docstring gains `detect` as its
fourth named `raised_by` call site (malformed/unreadable
`HEAD:controller/GENERATION.json` at the origin). `controller/__init__.py`'s
eager-import literal gains `handoff` between `worker` and `job`, matching
the plan's dependency order (`job -> {..., handoff} -> decision -> ...`).
`controller/cli.py` is extended -- deliberately scoped to exactly what
CP8's own end-to-end test needs, leaving `cmd_step`/`cmd_inspect`/
`cmd_explain`/`cmd_resume` unwired for CP9 ("CLI completion") as before:
the `run` loop (steps run bounded by `--max-steps`), checking, at one
orchestration boundary per iteration (before every job it starts,
including the first, never mid-job) -- the same boundary, together, per
the plan's own text -- both the test-only `--pause-file` hook (inert
unless `WORKFLOW_CONTROLLER_TEST_HOOKS=1`, with a one-line stderr note
otherwise) and `handoff.detect()`; on a pending handoff, publishes
`handoff.json` (job ids classified via `controller.job.TERMINAL_STATUSES`)
and stops with exit 50 *before* any worker launches; otherwise calls
`controller.job.execute_step` once and maps its reachable return shapes to
this checkpoint's own slice of the exit-code contract (`GATE_BLOCKED`->10,
`DECLINED`->15, `HANDOFF_PENDING`->50, `FAILED`->30, `INCOMPLETE`->35,
`FINISHED`->loop continues, the no-action `Decision`->0); exhausting
`--max-steps` with work still outstanding exits 16. The full exit-code
table (0/10/15/16/30/35's remaining reachability, plus `step`/`resume`)
is CP9's own concern, not restated here. `tests/test_handoff.py` (27
tests) covers: `detect()`'s three branches plus the uncommitted-bump
negative case, the worktree-kind `from_commit: None` carry-through, the
unpinned-identity refusal, and the missing/malformed-`GENERATION.json`
refusals (all four naming `raised_by: "detect"`); `write_handoff_record`'s
full schema; `_classify_jobs`'s terminal/non-terminal partition; the
`--pause-file`/`WORKFLOW_CONTROLLER_TEST_HOOKS` gate (inert with no flag,
inert with the wrong value, blocks until the file is removed when
enabled); the run loop's own exit-code mapping via a monkeypatched
`job.execute_step` (every reachable status, the no-action `Decision`, a
`FINISHED`-then-no-action two-step loop, `--max-steps` exhaustion, and
that a detected handoff writes the record and stops before any launch is
even attempted); the equal-generation/different-commit case exercised
through the real run loop rather than only through `detect()`; and the
real subprocess end-to-end test described above.

`CP9` (CLI completion, disposable managed-repository real-Workflow-action
evidence, documentation, and full milestone verification) is **complete**,
verified by `python3 -m unittest tests.test_cli
tests.test_plan_document_consistency tests.test_integration_disposable_repo
-v` (47 tests, 46 green and 1 correctly skipped -- the live-worker
integration test is opt-in via `CONTROLLER_LIVE_WORKER=1`, run separately
per the plan's own "run for real, once, during CP9") and by re-running the
full suite (`python3 -m unittest discover -s tests -t .`: 332 tests, 331
green and 1 skipped, no regressions). Delivered: `controller/cli.py`
completed -- `inspect` (managed-repo verification + a Workflow state
summary, text and `--json`), `explain` (`controller.evidence.decide`'s
full `Decision`, text and `--json`, never launching a worker or writing a
job record), `step` (one call through a new shared `_run_one_step` helper
-- a generation-handoff check then `job.execute_step`, mapping every
reachable status to the exit-code table and stopping after exactly one
action, unlike `run`'s own loop over the same helper) and `resume`
(`job.resume`'s reconciled/reported records, text and `--json`, exiting
`40` when any record is `INTERRUPTED`) are all wired to their real
behaviour; `cmd_run` is refactored (behaviour-preserving, confirmed by the
full pre-existing `tests/test_handoff.py` suite staying green unchanged)
to share `_run_one_step` with `step` rather than duplicating the
per-status exit-code mapping. The full ten-row exit-code table
(`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`, "Exit codes") is now
implemented, including the two codes CP1-CP8 left unreachable from the
CLI (`2`, argparse's own default; `40`, added as `EXIT_INTERRUPTED`).
`tests/test_cli.py` (28 tests): the parser's own command-line surface,
including the `OPUS-R34-B2` global-option-ordering fixture pair
(`--permission-mode` before vs. after the subcommand); `inspect`/
`explain`'s text and `--json` reports across an automatic phase
(`PLANNING`), a gate (`AWAITING_PLAN_APPROVAL`) and a declined phase
(`IMPLEMENTING`), plus `explain`'s own never-writes-a-job-record proof;
`step`'s exit-code mapping (`10`/`15`/`30`/`35`/`50`/`0`, the no-action
`Decision`, and stopping after exactly one `FINISHED` call, contrasted
with `run`'s own looping); `step`'s own handoff-detection-first case,
built from a real bumped-generation origin checkout; `resume`'s no-records/
terminal-`FINISHED`/terminal-`INTERRUPTED`(`->40`)/`--json` cases, and its
own never-launches-a-worker proof (a monkeypatched `worker.launch` that
fails the test if called, against a `PLANNED` record). Two stale CP1-era
assertions in `tests/test_identity.py`
(`DecoyAndRealRouteTest.test_console_script_route_pins_and_contains_correctly`/
`test_decoy_package_in_working_directory_is_never_imported`) are updated
from expecting `cmd_step`'s old `NotImplementedError` stub to expecting its
real `UnmanagedRepositoryError` refusal (exit 20) against the same
unmanaged fixture checkout -- the pinning proof both tests exist for
(`identity.json` written before the command body runs) is unchanged and
still asserted.

`docs/adr/0001-controller-generation-1-architecture.md` (new): the
normative exit-code table (its own `## Exit codes` heading, read by the
document-consistency suite under the same heading-matching rule as the
plan's own `### Exit codes`), plus the decisions most likely to matter to
a later generation (exit code as the Workflow Manager's own verdict; the
four-outcome worker classification; persist-before-launch as the basis of
resume; generation-number-against-committed-`HEAD` as the handoff
trigger). `README.md` gains installation (including the
not-every-install-serves-`step`/`run`/`resume` rule), the CLI surface, the
runtime-state layout and the safety model, with the exit-code table by
reference rather than restated (`README.md` is excluded at both approval
stages; the ADR is protected at the implementation stage). A new CI
workflow, `.github/workflows/controller-tests.yml`, runs the Controller's
own default suite on push/PR alongside the existing frozen conformance
workflow, which it does not touch.

`tests/test_plan_document_consistency.py` (18 tests) implements the
document-consistency property's four in-scope halves -- checkpoint
complexities against the registry, the exit-codes table against the ADR,
round counts against `plan_revision - 1`, and Controller invocation lines
against `controller.cli.build_parser()` itself -- each asserted first
against the live plan/registry/ADR (green, including a pin that the
command-line recogniser finds exactly the plan's own measured seven
lines) and then against a negative/positive instantiation pinning its
polarity (a wrong complexity fails; the superseded half of a `**N** today`
-marked pair does not; an unmarked stale figure sharing a sentence with a
marked pair still fails; a row differing between the plan and the ADR
fails; emphasis-only differences do not; a stale wrapped round count
(`ran\nit N rounds`) still fails; the `OPUS-R34-B2` pair parses/fails
exactly as measured against the live parser; a bracketed usage synopsis
and a bare route mention naming no command are both excluded). This
suite's own complexity-half extraction is deliberately narrower than the
plan's fullest specification of it (documented in the module's own
docstring): a bare `**N**` is used only to detect a sentence's own
`**N** today` marking, for pairing/exclusion purposes, and is never itself
compared against the registry -- treating every bare bold decimal in 3300
lines of prose as a checkpoint-complexity claim is exactly the shape that
produces false positives against unrelated bold numbers sharing a sentence
with a `CPn` token, and this suite trades a slice of the fullest
specification's own coverage for zero false positives against the live
document.

`tests/test_integration_disposable_repo.py` (`REQ-T18`) implements the
disposable-repository real-Workflow-action evidence exactly as the plan's
own six steps describe: a throwaway repository under `tempfile.mkdtemp()`;
a real `workflow-manager bootstrap --profile full` installation (with the
plan's own documented fallback if the Manager is unreachable); a trivial
committed milestone (add one `hello.txt` file) for `/milestone-plan` to
plan; `workflow-controller --permission-mode bypassPermissions step <tmp>`
run as the real installed module against the real `claude` binary, with
`--allow-dirty-source` added (also before the subcommand, since it is a
global) exactly when `git status --porcelain -- controller pyproject.toml`
is non-empty at run time; asserting a genuine, durable Workflow state
change (a `work_items` entry at `AWAITING_LOCAL_PLAN_REVIEW`, its
registry/mapping/artifacts files, and the Controller's own job record
showing `FINISHED`/`transition_verified: true` with the worker's real
`session_id`); and printing the evidence line (worker `session_id`,
pre-/post-phase, wall-clock and worker duration) `TEST_RESULTS.md` records
verbatim. Opt-in via `CONTROLLER_LIVE_WORKER=1` (skipped by default, since
it requires a live `claude` binary, network access and real spend); its
one real run for this checkpoint is recorded in the implementation
bundle's `TEST_RESULTS.md`.

The milestone's ten required capabilities are now all delivered: 1
(CP2), 2 (CP3), 3 (CP4/CP4B), 4 (CP5), 5 (CP9's own disposable-repo
evidence), 6 (CP4/CP4B), 7 (CP7), 8 (phase-independent, CP2/CP3/CP5/
CP6B/CP7), 9 (CP8), 10 (CP9's own completed CLI surface).

## Current blockers

**Blocked at `SELF_REVIEWING_IMPLEMENTATION` (all nine registry checkpoints
CP1-CP9 complete, commit `a62ba4f`). The final self-review invocation of
`/milestone-implement` (steps 2-5: bundle generation) has not run and must
not run until this is resolved** -- no implementation-review bundle exists,
and none should be generated while it pretends this is resolved.

Two independent findings surfaced during the attempted self-review step 3
full-verification run (`CONTROLLER_LIVE_WORKER=1
python3 -m unittest tests.test_integration_disposable_repo`, `REQ-T18`):

- **B1 -- mechanical, CP9-local, no plan involvement.**
  `tests/test_integration_disposable_repo.py:200-210` reads
  `job_record["worker_outcome"]` as a dict (`.get("session_id")`,
  `.get("classification")`, `.get("duration_seconds")`); `controller/job.py`
  actually writes `worker_outcome` as a plain string, with `session_id`
  under `job_record["worker"]["session_id"]` and no `classification`/
  `duration_seconds` fields anywhere. A test/schema mismatch, fixable by
  ordinary `/apply-implementation-review`-style remediation once review
  reopens -- not gating this blocker's resolution.

- **B2 -- a genuine contradiction inside the *approved* plan
  (`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`, revision 62), not an
  implementation defect.** `REQ-T18`'s own fixture (CP9 section, "Disposable
  managed-repository integration evidence", ~L5911-5975) seeds a target
  repository with **zero** `work_items` entries (only
  `docs/ACTIVE_MILESTONE.md` seeded, no work-item created) and asserts step
  5's outcome that the target "gains a `work_items` entry at phase
  `AWAITING_LOCAL_PLAN_REVIEW`". But CP3's own target-selection rule
  (~L2318-2319: "...else, if exactly one non-terminal work item exists, that
  one; else `AmbiguousWorkItemError`") makes the Controller **refuse** at
  exactly zero candidates, before any worker launches -- reproduced live:
  `error: no active_work_item_id is set and the target repository's Workflow
  state does not resolve to exactly one non-terminal work item (0 found)`,
  exit 20. CP3 and CP9 disagree about what a zero-work-item target repo
  should do, and no row in CP4/CP4B's phase -> action mapping covers "no
  work item exists yet" at all.

  **The frozen command-file constraint that controls any repair**
  (`.claude/commands/milestone-plan.md`, unmodifiable): "An argument that is
  neither a `work_items` key nor a resolvable commit is a refusal naming
  both attempted resolutions -- never a guess, and never a silently created
  work item: the id argument selects an **existing** entry only... A
  brand-new milestone's id is still derived in step 1, not passed here."
  `/milestone-plan` only ever creates a first work item when invoked with
  **no** argument, deriving the id itself from `docs/ACTIVE_MILESTONE.md`.
  Passing an explicit new id is a hard refusal under frozen Workflow
  v2.3.1 -- the Controller can never make that path create anything.

  **Approved bounded amendment scope (six items), not yet applied to the
  plan:**
  1. `controller/target_state.py::select_work_item` -- split the existing
     `else -> AmbiguousWorkItemError` (zero candidates, no explicit
     `--work-item`) into a distinct non-error `NoWorkItemYet` outcome;
     `> 1` candidates stays `AmbiguousWorkItemError`, unchanged.
  2. `NoWorkItemYet` routes to a `Decision` whose action is bare
     `/milestone-plan` -- no argument.
  3. Frozen Workflow (not the Controller) derives/creates the new
     work-item id from `docs/ACTIVE_MILESTONE.md`.
  4. Post-state validation identifies the created entry by exact key-set
     difference (`post.work_items.keys() - pre.work_items.keys()`),
     requiring exactly one new key.
  5. Explicit `--work-item <nonexistent-id>` remains
     `AmbiguousWorkItemError`, unchanged -- disjoint from the bootstrap
     path per the frozen refusal above.
  6. `controller/decision.py::decide`'s existing 17-phase table is
     unchanged for real `WorkItemView`s; `NoWorkItemYet` is a distinct
     pre-phase branch, never an 18th invented Workflow phase.

  **Currently-normative plan/property/test projections this would touch**
  (not yet edited -- see "why this cannot be applied" below):
  `CONTROLLER_GEN1_PLAN.md` fail-closed conditions list (~L2274-2277),
  target-selection paragraph (~L2318-2319), the CP4/`decide()` "reads
  nothing outside `WorkItemView`" framing (~L2340), the six-row
  `ExpectedOutcome` table and its "exactly one row .../every row
  corresponds to a triple" bijection statement (~L3913-3936, ~L3966-3972),
  Property 1 Coverage (~L4107-4118) and Property 2 Pair-keyed writer
  reachability (~L4129-4137), the "properties 1-6 ... over the six rows"
  case enumeration (~L4379-4383), the "two of CP6B's six rows" count
  (~L4642-4646), and `REQ-T18`'s own fixture description (~L5900-5975,
  needs: explicit no-`--work-item` invocation, Workflow-derived id, exact
  key-set-difference post-check). Plus a new requirement id (distinct from
  `REQ-T18`, which remains the end-to-end integration proof) for the
  bootstrap architecture itself in
  `docs/ai-workflow/requirements/workflow-controller-generation-1-mapping.json`,
  and a possible complexity re-estimate for CP3/CP4/CP6/CP6B in
  `docs/ai-workflow/registry/workflow-controller-generation-1-registry.json`
  where the added branch/row genuinely changes it.

  **Why this cannot be applied under the currently installed Workflow:**
  `docs/ai-workflow/MILESTONE_WORKFLOW.md` gives `IMPLEMENTING` (and
  transitively `SELF_REVIEWING_IMPLEMENTATION`) an entry condition of
  `plan_approval.status == CURRENT` plus a matching plan-stage
  `review_content_id` -- and the only route back to `REVISING_PLAN` is a
  `REVISE` verdict from a plan-review phase
  (`AWAITING_LOCAL_PLAN_REVIEW`/`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`/
  `AWAITING_EXTERNAL_PLAN_REVIEW`), never from `IMPLEMENTING` or
  `SELF_REVIEWING_IMPLEMENTATION`. No installed command targets this
  transition either. **Frozen Workflow v2.3.1 exposes no legal path to
  amend an already-approved plan while implementation is underway** --
  confirmed by search across `MILESTONE_WORKFLOW.md`, `REVIEW_PROTOCOL.md`,
  `PLAN_REVIEW_WORKFLOW.md` and the v2.1 operator reference: none document
  a mid-implementation plan-amendment mechanism.

  **Deliberately not done, per explicit decision**: the approved plan was
  not edited; the six-item architecture was not implemented; `REQ-T18` was
  not weakened (no `PLANNING` work item pre-seeded into the fixture); no
  transition back into plan review was fabricated; no direct
  `workflow_state` transaction primitive was used to invent an undocumented
  amendment path; no second Controller work item was created as a
  substitute approval path; implementation was not resumed and no
  implementation-review bundle was generated.

**Resolution path -- update 2026-09-19: unblocked.** Workflow was migrated
to `2.5.1` (`workflow-2.4.0` introduced `/request-plan-amendment`;
`workflow-2.5.1`'s `D-Checkpoint-Id-Anchor-Grammar-Widening` widened the
checkpoint-id anchor shape from `CP<digits>` to `CP<digits>[A-Z]?`, which
is what admits this work item's own `CP4B`/`CP6B` ids). `/request-plan-amendment
workflow-controller-generation-1` was run by the user (commit `f927163`):
`plan_approval.status` is now `SUPERSEDED`, `amendment_history` gained one
entry recording this blocker's reason, `amendment_base_commit` is
`659ca390`, and the work item's phase is now **`AMENDING_PLAN`**.

**Update 2026-09-19: fully resolved, implementation resumed.** The amended
plan (revision 71, applying the six-item `NoWorkItemYet` bootstrap scope
above plus the revision-64/68 Workflow-2.5.1-baseline widening) went through
the full two-stage local-then-manual-external plan review and was approved
via `/approve-review plan` (commit `5b33010`); `plan_approval.status` is
`CURRENT` again and the work item's phase is `IMPLEMENTING`. The amendment's
reconciliation left every registry checkpoint `NEEDS_REVALIDATION` (none
reset to incomplete -- `last_completed_checkpoint_id` stays `CP9`), so
`/milestone-implement` is now revalidating each checkpoint in registry order
per its own resumable one-checkpoint-per-invocation session model, B1's
mechanical test/schema fix included in CP9's own revalidation pass.

- `CP1` revalidated: the amendment's diff against the approved revision-62
  plan touches nothing between this checkpoint's own `<!-- CP1 -->`/
  `<!-- /CP1 -->` anchors (confirmed by `git diff` over that span -- byte-
  identical). No code change was needed; re-verified green by `python3 -m
  unittest tests.test_runtime tests.test_identity tests.test_package_structure`
  (43 tests, all green, no regressions). `COMPLETE`.
- `CP2` revalidated: this checkpoint's own plan section *did* change
  (revision 64/68's two-tier `SUPPORTED_WORKFLOW_LINE` /
  `VALIDATED_WORKFLOW_RELEASES` baseline replacing the closed
  `SUPPORTED_INSTALLATIONS` set), so `controller/managed_repo.py`,
  `controller/errors.py`'s two affected docstrings, `tests/fixtures.py`
  and `tests/test_managed_repo.py` were rewritten to match -- see the
  "Current checkpoint" `CP2` entry above for the full delivered shape and
  its residual, expected cross-checkpoint breakage in `tests.test_cli`
  (CP9) and `tests.test_target_state`/`tests.test_decision` (CP3/CP4,
  already red before this checkpoint, unaffected by it). Verified by
  `python3 -m unittest tests.test_managed_repo` (24 tests, all green).
  `COMPLETE`.
- `CP3` revalidated: this checkpoint's own plan section *did* change
  (revision 63/64's B2 `NoWorkItemYet` bootstrap sentinel plus the
  seventeen-to-twenty-member `KNOWN_PHASES` widening), so
  `controller/target_state.py`, `controller/errors.py`'s
  `AmbiguousWorkItemError` docstring and `tests/test_target_state.py` were
  updated to match -- see the "Current checkpoint" `CP3` entry above for
  the full delivered shape (the `NO_PHASE`/`NO_PHASE_WIRE`/`NoWorkItemYet`
  sentinels, declared here for later checkpoints to import by reference)
  and its residual, expected cross-checkpoint breakage in
  `tests.test_decision` (CP4) and `tests.test_cli` (CP9), both already red
  before this checkpoint and unaffected by it. Verified by `python3 -m
  unittest tests.test_target_state` (39 tests, all green). `COMPLETE`.
- `CP4` revalidated: this checkpoint's own plan section *did* change
  (revision 64's twenty-phase `KNOWN_PHASES` widening, the three new
  report-only phases `AMENDING_PLAN`/`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`
  (declined)/`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` (gate), the
  seventeen-command-file partition (4 selected + 9 deliberately-not-selected
  + 4 user-only), the user-only denylist widened from a single
  guard-literal recogniser to the **union** of that recogniser and a new
  front-matter `disable-model-invocation: true` recogniser (round 63's
  `B6` -- `request-plan-amendment.md` and `recover-implementation-provenance.md`
  each carry only one of the two), and `decide_no_work_item(managed_repo)
  -> Decision`, the distinct, version-independent sibling entry point for
  a `NoWorkItemYet` target). `controller/decision.py` and
  `tests/test_decision.py` were rewritten to match.

  **One cross-checkpoint relocation, forced by the dependency graph**:
  `NO_PHASE`/`NO_PHASE_WIRE` (declared in `controller/target_state.py` by
  CP3's own revalidation) had to move into `controller/decision.py`
  instead, with `target_state.py` now importing and re-exporting them.
  `decide_no_work_item` is `NO_PHASE`'s first producer and
  `tests/test_package_structure.py`'s `DEPENDENCY_ORDER` places `decision`
  strictly *before* `target_state` (`decision` may never import
  `target_state`, only the reverse) -- so the one canonical sentinel object
  every `is NO_PHASE` comparison across CP6/CP7 depends on can only be
  owned by whichever module needs it earliest, which is `decision.py`, not
  `target_state.py`. `target_state.NO_PHASE is decision.NO_PHASE` is now an
  asserted property (`tests/test_decision.py`'s
  `DecideNoWorkItemTest.test_target_state_re_exports_the_same_canonical_sentinel`),
  and `tests.test_target_state` stays fully green (its own `NO_PHASE`
  assertions read the re-exported attribute, unaffected by where it is
  defined).

  Verified by `python3 -m unittest tests.test_decision` (38 tests, all
  green) and `python3 -m unittest discover -s tests` (356 tests: 2
  failures, 1 skip -- both failures pre-existing in `tests.test_cli`
  (`InspectCommandTest`'s stale `"2.3.1"` assertions against the shared
  fixtures' now-`"2.5.1"` default, CP2's own revalidation already
  documented these as CP9's residual scope), no errors, no regression
  against CP1-CP3's own revalidation baseline). `COMPLETE`.
- `CP4B` revalidated: this checkpoint's own plan section *did* change --
  revision 64 adds a **fourth** evidence-needing phase alongside the three
  CP4B already refined (`AWAITING_LOCAL_PLAN_REVIEW`,
  `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_EXTERNAL_PLAN_REVIEW`
  on a `"1"`-governed item): `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
  (`"2.2"`, revision 64's own "Reporting rules" table), CP4's own
  revalidation left as a fixed, evidence-independent gate report and named
  explicitly as pending this checkpoint ("CP4B sharpens all four alike",
  `controller/decision.py`'s module docstring). Added
  `_decide_awaiting_manual_external_implementation_review` to
  `controller/evidence.py`'s `_EVIDENCE_HANDLERS`, implementing the
  three-way sub-case revision 64 states verbatim ("the same read-only
  evidence the plan stage's manual gate uses one stage over"): no
  current-round `REVIEW_FEEDBACK.md`, or one declaring the *local*-stage
  role, hands the bundle to a reviewer; a `MANUAL_EXTERNAL_IMPLEMENTATION_
  REVIEW` verdict on file names `/record-manual-implementation-review` as
  next; an admissible `Status: BLOCK` needs explicit user resolution first
  -- **report-only at every sub-case** (unlike its plan-stage counterpart:
  "the Controller reports and never launches at any of the three"), so the
  plan-stage admissibility model (`evaluate_manual_stage_admissibility`,
  bundle-id/`generation_head` currency) is deliberately not re-derived,
  and the reviewer-role match is **exact-spelling only, no legacy-cased
  alias** (unlike `_normalize_role`'s plan-stage acceptance), mirroring
  `validate_manual_implementation_review_preconditions`
  (`scripts/workflow_state.py:12709`). Also added
  `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` to `BUNDLE_BEARING_
  PHASES`: `/record-manual-implementation-review` calls
  `assert_bundle_not_rejected` exactly as `/record-manual-plan-review`
  does at the plan stage, so a withdrawn implementation bundle must gate
  ahead of this phase's own ordinary rows too, the same as `AWAITING_
  EXTERNAL_IMPLEMENTATION_REVIEW` already does. No change was needed to
  `controller/decision.py` (its own fixed placeholder text for this phase
  stays defined, and reachable, for a direct `decision.decide` call in
  isolation -- `evidence.decide`'s `_EVIDENCE_HANDLERS` lookup now takes
  precedence for any caller going through CP4B's own entry point, which is
  every real caller per the plan's dependency graph). `tests/test_evidence.py`
  gained `AwaitingManualExternalImplementationReviewTest` (6 tests): no
  feedback, wrong (local-stage) role, lowercase role rejected (the
  no-legacy-alias negative, contrasting `AwaitingManualExternalPlanReviewTest`'s
  own `test_legacy_lowercase_role_is_accepted`), `Status: BLOCK`, an
  admissible verdict staying report-only (never `automatic`), and the
  `REJECTED`-marker-first override at this newly bundle-bearing phase.
  Verified by `python3 -m unittest tests.test_evidence` (46 tests, all
  green) and `python3 -m unittest discover -s tests` (362 tests: 2
  failures, 1 skip -- the same two pre-existing `tests.test_cli` failures
  CP4's own revalidation already documented as CP9's residual scope, no
  errors, no regression against CP1-CP4's own revalidation baseline).
  `COMPLETE`.
- `CP5` revalidated: this checkpoint's own second, independent denylist
  layer (`controller/worker.py`'s `USER_ONLY_COMMANDS`, a literal copy
  kept deliberately separate from CP4's `decision.derive_user_only_commands`)
  had gone stale under the amendment -- CP4's own revalidation already
  widened `derive_user_only_commands` from a single guard-literal
  recogniser to the union of that recogniser and the front-matter
  `disable-model-invocation: true` recogniser, which now derives **four**
  names (`approve-review`, `accept-milestone`,
  `recover-implementation-provenance`, and `request-plan-amendment`,
  the new command `workflow-2.4.0` introduced -- caught only by the
  flag recogniser, never the guard-literal one), while `worker.py`'s own
  copy still held the pre-amendment three. Added `"request-plan-amendment"`
  to `USER_ONLY_COMMANDS` and corrected the module's docstrings (the
  three-name/"qualified-literal property" description no longer matches:
  three of the four names carry the confirmation guard, the fourth only
  the front-matter flag). No other part of this checkpoint's own plan
  section changed (`launch()`'s mechanism, the four-outcome classification,
  the worker-stdout JSON candidate-span rule, the process-group timeout
  teardown, and the `PYTHONPATH`-stripped environment are all untouched
  by the amendment). `tests/test_worker.py` gained
  `test_task_naming_request_plan_amendment_refuses`, mirroring the
  existing per-name refusal cases. Verified by `python3 -m unittest
  tests.test_worker` (18 tests, all green) and `python3 -m unittest
  discover -s tests` (363 tests: 2 failures, 1 skip -- the same two
  pre-existing `tests.test_cli` failures CP4's own revalidation already
  documented as CP9's residual scope, no errors, no regression against
  CP1-CP4B's own revalidation baseline). `COMPLETE`.
- `CP6` revalidated: unlike CP2-CP5's own staleness-only revalidations,
  CP6's own plan section (steps 1-6) genuinely widened under the
  amendment -- revision 63/64's B2 row 7 (`NoWorkItemYet` bootstrap) needed
  real, new code, not just a stale-reference fix. `controller/job.py`
  gained: `PRE_STATE_FIELDS`'s seventeenth member, `pre_work_item_keys`
  (row 7's own `predicate_input`, a sorted-list snapshot of the
  pre-snapshot's `work_items` keys, captured for every call, normal or
  bootstrap alike); a `NoWorkItemYet` branch in `_capture_pre_state`
  (every work-item-scoped field its own report-only default, `phase:
  NO_PHASE`, `child_work_item_ids: []` rather than every top-level entry a
  naive `parent_work_item_id == None` comparison would wrongly match);
  `_identity_block`/`_no_launch_record`/`_expected_outcome_for`/
  `_expected_transition` changed to accept `work_item_id`/`observed_phase`/
  `governing_workflow_version` explicitly rather than deriving them from a
  `work_item` object a `NoWorkItemYet` target does not have; a new
  `_durable_pre_state` helper (`phase` written through the new
  `controller.decision.phase_to_wire`, every other field unchanged) used
  at every point a `pre_state` dict is embedded in a persisted record --
  without it, `runtime.write_json` raises `TypeError: Object of type
  _NoPhaseType is not JSON serializable` the first time a bootstrap job's
  `PLANNED` flush is attempted, caught by this checkpoint's own new tests;
  and row 7 itself, added to `EXPECTED_OUTCOMES` (`from_phase=NO_PHASE`,
  `governing_version=None`, `to_any_of={AWAITING_LOCAL_PLAN_REVIEW}`, the
  plan's own key-set-difference predicate implemented as
  `_predicate_row7_new_work_item_created`), which needed
  `property_table_violations`'s own property-3 converse check to gain the
  plan's own named `from_phase is NO_PHASE` exemption (row 7 carries a
  predicate for a defence-in-depth reason its own `to_any_of` containment
  never requires one for). `controller/decision.py` gained `phase_to_wire`/
  `phase_from_wire` -- the writer/reader pair "`NO_PHASE`'s durable form"
  declares but that did not previously exist anywhere in the package --
  imported and used by `job.py`'s own identity/pre-state/expected-transition
  writers. **CP6B's own steps 7-9 are untouched and do not yet support row
  7**: `execute_step` still raises `AttributeError` on `work_item.work_item_id`
  at step 7's fresh post-state re-read for a bootstrap job, by design --
  CP6's own new tests (`tests/test_job.py`'s `BootstrapRowSevenTest`)
  wrap the `execute_step` call in `assertRaises(AttributeError)` and
  assert only against what CP6's own steps 1-6 left durable on disk before
  that point (the `PLANNED`/`LAUNCHED`/`COMPLETED` flushes, `pre_work_item_keys`
  as the pre-state's seventeenth field, and the `NO_PHASE` round-trip
  across `pre_state.phase`/`observed_phase_before`/`expected_transition.from`,
  re-read fresh from disk through the new `phase_from_wire`, never from
  process memory) -- wiring steps 7-9 for row 7 is CP6B's own revalidation,
  next. `tests/test_job_validation.py`'s pre-existing `test_six_rows`
  became `test_seven_rows` (the only pre-existing assertion row 7 broke).
  `tests/test_decision.py` gained `PhaseWireRoundTripTest` (writer/reader
  correctness directly, independent of `job.py`'s own round trip, including
  the fail-closed refusal of a literal `null` and a bare `"None"` string).
  Verified by `python3 -m unittest tests.test_job tests.test_job_validation
  tests.test_decision tests.test_target_state tests.test_evidence` (167
  tests, all green) and `python3 -m unittest discover -s tests` (373
  tests: 2 failures, 1 skip -- the same two pre-existing `tests.test_cli`
  failures CP4's own revalidation already documented as CP9's residual
  scope, no errors, no regression against CP1-CP5's own revalidation
  baseline). `COMPLETE`.

**Next legal step**: a further `/milestone-implement` invocation continues
revalidating `CP6B` through `CP9` in registry order, wiring CP6B's own
steps 7-9 (a `work_item_id`-free fresh post-state re-read, and row 7's own
predicate/verification) for the `NoWorkItemYet` bootstrap CP6 above now
captures and flushes correctly but cannot itself verify, applying the
revision-64/68 Workflow-baseline changes (`VALIDATED_WORKFLOW_RELEASES`,
`NO_PHASE`'s round-trip schema) where each remaining checkpoint's own plan
section now requires them, through `CP9` where B1's `worker_outcome`
schema mismatch is also fixed, before the phase can re-enter
`SELF_REVIEWING_IMPLEMENTATION` and a fresh implementation-review bundle
is generated.

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
