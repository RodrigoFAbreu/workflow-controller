# Controller automatic lifecycle orchestration (Revision 5)

Work item: `workflow-controller-automatic-lifecycle-orchestration`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `abd34a01bb1afef0a376b11128569920c4dd2210` (the acceptance commit of
`workflow-controller-worker-execution-hardening`; `d2a5de0`, the `pyproject.toml` patch-version
bump on top of it, is inside this milestone's diff range and is not reverted)
Lifecycle authority: installed Workflow 2.5.1 (`.claude/commands/`, `scripts/workflow_state.py`,
`scripts/workflow_fingerprint.py`, `scripts/prepare-ai-review.sh`) and each target work item's own
`governing_workflow_version`.

## Goal

Controller Generation 1 today *reports* every implementation-stage phase and launches workers
only for the plan stage. This milestone makes it *drive* the automation-safe implementation-stage
actions, so that one `workflow-controller run` carries a `"2.2"` work item from `IMPLEMENTING`
through checkpoint implementation, the final self-review pass, local implementation review, and
local `REVISE` remediation, until the next genuine human gate. It does this without weakening any
Workflow guarantee:

1. **One general dispatch rule** replaces the phase-specific report-only sets. A worker is
   launched only when the evidence-selected next action is model-invocable, is not user-only, is
   not gated, and has a declared, verifiable `ExpectedOutcome`. Every other case is reported, as a
   gate or a decline. Unknown or ambiguous states still fail closed.
2. **Implementation-review orchestration**:
   - `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` -> `/milestone-implement`;
   - `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` -> `/review-implementation`;
   - `APPLYING_REVIEW_FEEDBACK` -> `/apply-implementation-review`;
   - an already-pasted, admissible manual external verdict at
     `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` -> `/record-manual-implementation-review`.

   The Controller never performs the manual external review and never runs
   `/approve-review`.
3. **Expected outcomes with artifact postconditions** for every newly automated command. A phase
   transition alone never counts as success when the implementation bundle, the review ledger or
   the checkpoint completion it promises is stale, missing, rejected or bound to the wrong
   revision/content.
4. **Protocol-2.2 correctness**. The `APPLYING_REVIEW_FEEDBACK` guidance is corrected, and
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` becomes aware of the reviewer role and the
   bundle/content.
5. **Worker lifecycle and concurrency safety**. At most one lifecycle worker per target worktree.
   No replacement is launched until the previous worker is definitively terminal. Silence or a long
   run is never treated as termination.
6. **Role-based model/effort routing**. Five lifecycle roles default to Claude Opus 5.5 at `xhigh`.
   Review workers are fresh, single-agent sessions. Explicit CLI/config overrides take precedence.

## Non-goals

Carried verbatim from the milestone scope:
- release/wheel/GitHub Actions/pipx runtime isolation;
- Workflow Manager `.workflow-manager/installation.json` classification;
- Workflow `/apply-plan-review` publication ordering;
- shared feedback-storage redesign;
- multi-harness portability;
- live `--follow` streaming UX.

Also out of scope:
- any edit to `scripts/` or `.claude/commands/` (frozen Workflow 2.5.1 release content);
- recomputing `review_content_id`/`bundle_id` inside Controller;
- automating `/apply-functional-review`, `/prepare-functional-review`, `AMENDING_PLAN`'s
  `/milestone-plan`, or `"2.1"` external-verdict consumption (see "Scope judgments");
- a Controller generation bump (`controller/GENERATION.json` stays `1`; a generation bump is a
  release decision).

## Investigation

### What Generation 1 does today at the implementation stage

- `controller/decision.py:191-229`: `REPORT_ONLY_PHASES` holds every implementation-stage phase.
  `DECLINED_PHASES` (`IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION`, `AMENDING_PLAN`,
  `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`) names a command but never runs it (exit 15). The other
  four are not all static:
  - `APPLYING_REVIEW_FEEDBACK` is a static gate (exit 10);
  - `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`
    and `AWAITING_FUNCTIONAL_REVIEW` go through `evidence._EVIDENCE_HANDLERS`
    (`controller/evidence.py:995-1002`);
  - `AWAITING_FUNCTIONAL_REVIEW` can return an *automatic* decision (see "Two latent defects"
    below).
- `controller/decision.py:748-753`: the `APPLYING_REVIEW_FEEDBACK` gate text says "no Workflow
  command can legally run from this phase". That is wrong:
  - `.claude/commands/apply-implementation-review.md:10-49` skips its own
    `enter_applying_review_feedback` call exactly when the phase is already
    `APPLYING_REVIEW_FEEDBACK`, and then runs steps 1-8;
  - for `"2.2"` this is the *normal* entry, because `record_local_implementation_review`/
    `record_manual_implementation_review` (`scripts/workflow_state.py:12621`, `:12737`) set
    `APPLYING_REVIEW_FEEDBACK` directly on `REVISE`.
- `controller/evidence.py:765-816`: `_decide_awaiting_external_implementation_review` is
  version-agnostic. It never reads the reviewer role, the `implementation_review_stages` ledger or
  the manifest's content identity. For `"2.2"` this phase is reached only through a recorded manual
  `APPROVE` (`record_manual_implementation_review`), so "no feedback on file -> hand the bundle to
  an external reviewer" is wrong there. The real next action is the user-only
  `/approve-review implementation`, provided both ledger stages approve the current content.
- `controller/target_state.py:126-156`: `WorkItemView` does not expose
  `implementation_review_stages` at all.
- `controller/job.py:538-663`: `EXPECTED_OUTCOMES` has eleven rows, all plan-stage.
- `controller/decision.py:312-341`: `SELECTED_COMMANDS` is four plan-stage files.
  `DELIBERATELY_NOT_SELECTED_COMMANDS` gives `milestone-implement`/`apply-implementation-review`
  the reason "revision 10's report-only set" and `review-implementation` the reason "would close a
  review loop with no external reviewer in it". The second reason no longer holds for `"2.2"`: the
  local stage is followed by a mandatory manual external stage that Controller cannot perform.

### Workflow 2.5.1's implementation-stage transitions (the lifecycle authority)

| Command | From | Writer (frozen text) | To |
|---|---|---|---|
| `/milestone-implement` (`"2.1"`/`"2.2"`, one checkpoint per invocation) | `IMPLEMENTING` | `complete_checkpoint`, `milestone-implement.md:178-209` (step 1f) | `IMPLEMENTING` (more checkpoints) or `SELF_REVIEWING_IMPLEMENTATION` (last one) |
| same | `SELF_REVIEWING_IMPLEMENTATION`, or `IMPLEMENTING` with every checkpoint `COMPLETE` (the `NO_CHECKPOINT` path after a plan re-approval) | `record_bundle_generation(stage="implementation")`, `milestone-implement.md:288-356` (step 4) | `bundle_generation_target_phase`: `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` (`"2.2"`) / `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` (`"2.1"`) |
| `/review-implementation` (`"2.2"` authoritative branch) | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `record_local_implementation_review`, `review-implementation.md:436-481` (A6) | `APPROVE` -> `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` (ledger `LOCAL_MODEL_IMPLEMENTATION_REVIEW` recorded); `REVISE` -> `APPLYING_REVIEW_FEEDBACK`; `BLOCK` -> unchanged (no-op) |
| `/record-manual-implementation-review` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `record_manual_implementation_review`, `record-manual-implementation-review.md:101-129` (step 7) | `APPROVE` -> `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` (ledger `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` recorded); `REVISE` -> `APPLYING_REVIEW_FEEDBACK`; `BLOCK` -> unchanged |
| `/apply-implementation-review` (`"2.2"`) | `APPLYING_REVIEW_FEEDBACK` | `record_bundle_generation(stage="post-fix")`, `apply-implementation-review.md:131-197` (step 7) | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` |
| `/approve-review implementation` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | user-only (`disable-model-invocation`, confirmation guard) | never selected |

Two facts from the frozen text drive the postcondition design:

- **The phase is published before the bundle exists.** `record_bundle_generation`
  (`scripts/workflow_state.py:11231-11322`) sets the target phase and bumps
  `implementation_revision`/`reviewed_implementation_head`. Both commands then commit that state
  write, and only afterwards run `prepare-ai-review.sh`. A generator failure after that point
  leaves the item at a review-awaiting phase with a previous round's bundle, or with none. This is
  the same class of defect the previous milestone closed at the plan stage.
- **The implementation-stage manifest carries the round identity.**
  `render_manifest_md_implementation_stage` (`scripts/workflow_fingerprint.py:3448-3560`) writes
  the following as labelled lines:
  - `stage: implementation` (also for `post-fix`);
  - `bundle_id`, `review_content_id`, `work_item_id`, `base_commit`;
  - `reviewed_implementation_head`, `implementation_revision`, `generation_head`.

  `generation_head` is the commit the generator ran at. Because the generation-record commit
  always lands immediately before generation, `generation_head` advances on every successful
  generation, including a `same_content` post-fix whose `implementation_revision` does not change.

The `"2.2"` review-stage writers leave `WORKFLOW_STATE.json` **uncommitted**, because neither
command authorizes a commit. So `HEAD` does not move across a review job, and a review's effect is
visible only in the working-tree state file. That is the file Controller already reads.

**The frozen `/apply-implementation-review` text does not commit that pending write** (revision 1
said it did; that was wrong):
- step 0 skips its own entry write when the phase is already `APPLYING_REVIEW_FEEDBACK`;
- step 6 commits only "coherent fixes";
- step 7 stages exactly `WORKFLOW_STATE.json` into the generation-record commit T itself
  (`.claude/commands/apply-implementation-review.md:131-165`).

For the ordinary role, `validate_bundle_generation_record_commit` requires `phase` in T's own
field diff against its parent (`scripts/workflow_state.py:11682-11687`, `OPUS-R101-001`). After a
local or manual `REVISE`, the last *committed* phase is the previous T's
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, and T sets the same value again. So a worker that follows
the frozen text literally lands a malformed T, `prepare-ai-review.sh`'s preflight refuses, and
the item sits at `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` over a malformed T that only history
repair can fix. (The recovered role, used by a `same_content` round, is not affected: it requires
only that T's parent phase be one of `RECOVERED_BUNDLE_GENERATION_RECORD_LEGAL_SOURCE_PHASES`,
which include `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, `scripts/workflow_state.py:11394-11404`,
`:11727-11735`.)

Every precedent in this repository committed the pending phase in a *separate, operator-made*
commit before T: `4a72ae8`->`7ed782a`, `613b950`->`404f05b`, `f14f362`->`1545267`, and, for the
`"2.1"` entry write, `9770235`->`7a9a4c5`. That is operator practice, not command text. A fresh
worker has no instruction to do it. "The pending review-stage state write" under Design decides
how an automated round lands a valid T.

### Two latent defects found while planning

- **`/apply-functional-review` crashes `execute_step`.**
  - `evidence._decide_awaiting_functional_review` (`controller/evidence.py:945-952`) returns an
    *automatic* `/apply-functional-review` when findings are unconsumed.
  - No `ExpectedOutcome` row exists for that triple, so `execute_step` step 4
    (`_expected_transition` -> `_expected_outcome_for`, `controller/job.py:918-939`) raises a bare
    `AssertionError`. That is an uncaught traceback, not a `ControllerError`/exit code.
  - The crash happens after the `PLANNED` flush (`controller/job.py:1925-1927`), so it also
    leaves a `PLANNED` job record behind (see "Migration").
  - `tests/test_evidence.py:878-886` asserts the automatic decision but never runs it through
    `execute_step`.
  - CP3's general rule fixes this structurally: no row means declined.
- **The plan-stage `BLOCK` self-loop predicate can never verify a real `BLOCK`.**
  - `_predicate_row3_block_feedback_current` (`controller/job.py:446-463`) compares the feedback's
    `Reviewed bundle ID:` with `pre_state["bundle_id"]`, which is `work_item.current_bundle_id`
    (`controller/job.py:292`).
  - Workflow initialises `current_bundle_id` to `None` (`scripts/workflow_state.py:7639`) and no
    writer ever sets it (`grep -n 'current_bundle_id"\]\s*=' scripts/workflow_state.py`: none).
  - So a genuine local `BLOCK` (feedback carrying a real bundle id) always fails the predicate.
    `execute_step` records it `FAILED`. On the `LAUNCHED` resume path the same failure, with
    phase and `HEAD` unchanged, yields `INTERRUPTED` instead (`controller/job.py:1483-1502`). A
    feedback file *missing* the binding line satisfies it (`None == None`).
  - The unit tests seed `current_bundle_id="b" * 64` (`tests/test_job_validation.py:589`, etc.), a
    state no real Workflow produces.
  - CP2 fixes this by reading the pre-state *manifest* bundle id and requiring it to be non-null.

### Baseline test state

`python3 -m unittest discover -s tests -t .` at base: 514 tests, 1 failure, 1 error, 2 skipped.
Both non-passing tests read *this repository's own* live `WORKFLOW_STATE.json` and assume that
`active_work_item_id` names an entry:
- `tests.test_target_state.ReadHappyPathTest.test_real_repository_own_workflow_state_reads_cleanly`;
- `...RegistryCompleteTest.test_this_repository_own_registry_reads_as_a_boolean`.

`active_work_item_id` was `null` after the previous milestone's acceptance. Creating this work item
restores it, and both then pass (re-run recorded in this round's `TEST_RESULTS.md`). They are not
defects in Controller code.

### Artifact declaration

`docs/ai-workflow/registry/workflow-controller-automatic-lifecycle-orchestration-artifacts.json`
was generated with `workflow_state.generate_artifacts_declarations(...,
work_item_type="product")`. It was then corrected in `SELF_REVIEWING_PLAN`, before any approval
exists, with the same corrections the previous Controller work item made:
- `controller/` and `tests/` are added to `plan_stage.excluded_prefixes` and to
  `implementation_stage.protected_prefixes`;
- `pyproject.toml` is added to `plan_stage.excluded_paths` and to
  `implementation_stage.protected_paths`;
- `README.md` moves from `implementation_stage.excluded_paths` to
  `implementation_stage.protected_paths`, because CP8 edits it.

`docs/adr/` is already implementation-protected and plan-excluded in the product template, and CP8
edits `docs/adr/0001-controller-generation-1-architecture.md`. Every other tracked top-level path
is covered by the template unchanged: `.claude/`, `.github/`, `.gitignore`, `CLAUDE.md`, `docs/`,
`scripts/`, `.workflow-manager/`.

## Design

### The general automatic-dispatch rule

`evidence.decide` stays the single decision entry point. Its phase handlers keep *selecting* the
next action from evidence, or returning a gate. Whether a selected action is **launched** is then
decided in one place, for every phase alike, by `decision.classify_selected_action(phase, version,
command)`:

- **automatic** iff `(phase, governing_workflow_version, command token)` is a member of
  `decision.AUTOMATIC_TRIPLES`;
- **declined** otherwise, with the uniform reason: "`<phase>` selects `<command>`, which is
  model-invocable, but no verifiable ExpectedOutcome is declared for (`<phase>`, `<version>`,
  `<command>`), so this Controller reports it instead of launching it".

**Action representation.** There is one normalisation, and it is the one `_expected_outcome_for`
already applies (`controller/job.py:930`):
- `ExpectedOutcome.action` and the third member of every `AUTOMATIC_TRIPLES` entry are the
  slash-prefixed command token, e.g. `"/milestone-plan"`;
- `classify_selected_action` receives the full `Decision.action.command` (e.g.
  `"/milestone-plan wi-1"`) and compares `command.split()[0]`;
- `SELECTED_COMMANDS` keeps holding bare file stems (`"milestone-plan"`). A test asserts
  `{a[1:] for (_, _, a) in AUTOMATIC_TRIPLES} <= SELECTED_COMMANDS`.

`AUTOMATIC_TRIPLES` is a literal frozenset in `decision.py`. `decision` cannot import `job`, which
is later in `DEPENDENCY_ORDER`. It is held equal to `{(eo.from_phase, eo.governing_version,
eo.action) for eo in job.EXPECTED_OUTCOMES}` by a two-directional test, the same pattern the
`KNOWN_PHASES` copies use. So "has a declared, verifiable expected outcome" and "is launched" can
never drift apart. Adding a future automated action means adding one `ExpectedOutcome` row plus the
matching triple; the test fails if only one is added.

Everything else the rule relies on is already mechanical:
- "model-invocable and not user-only" is `classify_command_files`'s total partition (checked at
  test time against the installed command files) plus `worker.USER_ONLY_COMMANDS` (checked at
  launch time);
- "not otherwise blocked" is the handler's gate;
- unknown phases, vocabulary phases and unrecognised command files still raise
  `NoSupportedActionError`.

`REPORT_ONLY_PHASES`/`DECLINED_PHASES`/`GATE_REPORT_PHASES`/`_DECLINED_COMMAND_BY_PHASE` are
replaced by per-phase handlers that each return either a gate or a selected command. Their names
remain importable only if a test still needs them. CP3 decides that; no behavior may depend on them.

The rule's effect, phase by phase:

| Phase | `"1"` | `"2.1"` | `"2.2"` |
|---|---|---|---|
| `IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION` | declined (the `"1"` branch writes no state, so no transition is observable) | **automatic** `/milestone-implement`, unless the plan-approval gate applies | **automatic** `/milestone-implement`, unless the plan-approval gate applies |
| `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | n/a | n/a | **automatic** `/review-implementation`, unless REJECTED/stale/BLOCK gates apply |
| `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | n/a | n/a | gate (no manual verdict), gate (BLOCK / inadmissible, including a `REVISE` bound to another bundle), or **automatic** `/record-manual-implementation-review` (admissible `APPROVE`/`REVISE`) |
| `APPLYING_REVIEW_FEEDBACK` | gate, corrected text | gate, corrected text | **automatic** `/apply-implementation-review` (admissible `REVISE`, no unverified earlier attempt; with the pending-write task addendum when it applies), else gate |
| `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | unchanged gate | unchanged gate | ledger- and feedback-aware gate (see CP4) |
| `AWAITING_FUNCTIONAL_REVIEW` | unchanged gates; unconsumed findings now **declined** instead of crashing | same | same |
| `AMENDING_PLAN` | declined (no row) | declined | declined |
| every plan-stage phase | unchanged at every reachable combination (see below) | same | same |

**Decisions that change at combinations no Workflow writer reaches.** Every existing automatic
handler is version-agnostic (`controller/decision.py:513-538`; `controller/evidence.py:601-607`,
`685-689`, `752-757`, `947-951`). So these combinations are automatic today, have no
`ExpectedOutcome` row, and flip to **declined**:
- `(REVISING_PLAN | AWAITING_LOCAL_PLAN_REVIEW | AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW, "1")`;
- `(AWAITING_EXTERNAL_PLAN_REVIEW, "2.1" | "2.2")`;
- any phase with `governing_workflow_version` `None` (the `NO_PHASE` bootstrap row keeps its own
  `(NO_PHASE, None, "/milestone-plan")` triple and is unaffected).

Each of these crashes `execute_step` today with the bare `AssertionError` described under "Two
latent defects", so declining is an improvement. It is still a behavior change, stated here and
pinned by CP3's own test. No reachable plan-stage combination changes.

### The pending review-stage state write (row 18)

Investigation establishes that a worker following the frozen `/apply-implementation-review` text
literally lands a malformed generation-record commit T whenever the `"2.2"` review-stage write
that moved the item to `APPLYING_REVIEW_FEEDBACK` is still uncommitted. That is the normal case
after an automated local `REVISE` or an ingested manual `REVISE`. The Controller may not edit the
frozen command, and it never writes the target itself. The decision:

1. **A pinned task addendum, for row 18 only, only when the write is pending.** At `"2.2"`
   `APPLYING_REVIEW_FEEDBACK`, the Controller reads the work item's committed phase at `HEAD`
   (`git show HEAD:docs/ai-workflow/WORKFLOW_STATE.json`, CP1's `committed_work_item`). If it is
   not `APPLYING_REVIEW_FEEDBACK`, the selected `Action` carries
   `task_addendum = PENDING_REVIEW_STAGE_WRITE_ADDENDUM`, formatted with the work item id and the
   committed phase. `execute_step` launches the task `<command>\n\n<addendum>`. The constant's
   text is pinned byte-for-byte by a test, names no user-only command
   (`worker._assert_not_user_only` checks the whole task), and says:

   > Controller note (pending review-stage state write): `docs/ai-workflow/WORKFLOW_STATE.json`
   > carries this work item's uncommitted review-stage write (working-tree phase
   > `APPLYING_REVIEW_FEEDBACK`; `HEAD` records `<committed phase>`). Before any other commit this
   > command makes, commit that pending change alone: stage exactly
   > `docs/ai-workflow/WORKFLOW_STATE.json`, unmodified from what the review-stage writer
   > produced, with a message whose final paragraph is the single trailer
   > `Workflow-Work-Item: <work_item_id>` and no other Workflow trailer. This is the same shape
   > `/milestone-implement` step 2 uses for its state-only transition commit. Without it, step 7's
   > generation-record commit has no phase change in its own diff, and
   > `validate_bundle_generation_record_commit` rejects it (`OPUS-R101-001`).

   The addendum makes the Controller do what the operator did in every precedent, no more. It
   authorizes exactly one state-only commit of a write a Workflow writer already produced. It is
   recorded in the job record as `selected_action.task_addendum`. When `HEAD` already records
   `APPLYING_REVIEW_FEEDBACK` (a human, or an earlier attempt, already made the commit), no
   addendum is attached, and the task is the bare command, as for every other row.
2. **A malformed-T gate, so a non-compliant worker is diagnosed, not mis-advised.** If `HEAD`
   carries this work item's ordinary-role `Workflow-Bundle-Generation-Record` trailer set, and the
   work item's committed phase at `HEAD^` equals its committed phase at `HEAD`, then T is
   malformed by `OPUS-R101-001`'s own rule. The implementation-bundle gate (CP4B) then reports
   that instead of the regeneration steps, which the generator's preflight would refuse. It names
   the commit and the rule. It states that no Workflow command repairs it: a human repairs the
   unpushed history, so that the pending write lands alone before T, and then reruns step 7. The
   `safe_resume_command` is `workflow-controller explain --work-item <id>`.
3. **Verification.**
   - CP7 runs a compliant scripted worker (`FINISHED`) and a literal one that ignores the
     addendum (`FAILED`, then the malformed-T gate, with nothing launched).
   - CP9's live run exercises a real `/apply-implementation-review` round, with a malformed T as
     a named stop condition.

The rejected alternatives:
- A Controller gate naming the state commit for a human to make keeps every task bare, but puts a
  human action into every `REVISE` round, so R4's "without human action" would not hold.
- Documenting that R4 does not hold was rejected for the same reason.

"Scope judgments" lists the addendum for the reviewer to confirm or cut. Cutting it falls back to
the gate alternative, with R4 amended.

### Worker routing

A new module, `controller/routing.py`, sits between `evidence` and `worker` in `DEPENDENCY_ORDER`.
It declares:

- `ROLE_ROUTES`: role -> `Route(model, effort, single_agent)`.

  | Role | Selected by | Default model | Default effort | Single-agent |
  |---|---|---|---|---|
  | `milestone-implement` | `/milestone-implement` from `IMPLEMENTING` with a checkpoint outstanding | `claude-opus-5-5` | `xhigh` | no |
  | `milestone-implement-self-review` | `/milestone-implement` from `SELF_REVIEWING_IMPLEMENTATION`, or from `IMPLEMENTING` with `registry_complete` true (the final pass that runs step 2's full-diff self-review) | `claude-opus-5-5` | `xhigh` | yes |
  | `apply-plan-review` | `/apply-plan-review` | `claude-opus-5-5` | `xhigh` | no |
  | `apply-implementation-review` | `/apply-implementation-review` | `claude-opus-5-5` | `xhigh` | no |
  | `review-plan` | `/review-plan` | `claude-opus-5-5` | `xhigh` | yes |
  | `review-implementation` | `/review-implementation` | `claude-opus-5-5` | `xhigh` | yes |
  | `milestone-plan`, `record-manual-plan-review`, `record-manual-implementation-review` | those commands | inherit (no flag passed) | inherit | no |

- `resolve_route(role, *, cli_model, cli_effort, cli_role_models, cli_role_efforts, config) ->
  ResolvedRoute`. Precedence, per field (model and effort independently):
  1. `--role-model ROLE=M` / `--role-effort ROLE=E`;
  2. `--model` / `--effort`;
  3. the `--routing-config` file's role entry;
  4. its `default` entry;
  5. the built-in `ROLE_ROUTES` value;
  6. "inherit", meaning the flag is omitted so the `claude` CLI's own configuration applies.

  `single_agent` is **not** overridable: a review role is always single-agent.
- `SUBAGENT_TOOLS`: the Claude Code tool names that spawn subagents or workflows. The exact
  names are confirmed at implementation time against the installed CLI (`claude --help`, the tool
  list) and by CP9's live probe. `Agent` and `Workflow` are the expected names for
  Claude Code 2.1.280. If the probe shows that `Skill` can run a forked (subagent) skill, `Skill`
  joins the set as well. CP9's live review job then confirms that the review command itself still
  runs under that disallow list.

`worker.launch` gains keyword-only `model`, `effort` and `disallowed_tools` (all default `None` =
omitted). Argv is `claude -p <task> --output-format json --permission-mode <m> [--model M]
[--effort E] [--disallowedTools A,B]`. The disallow list is one comma-joined argv element, placed
last so the CLI's variadic option cannot swallow anything after it. `launch` never passes
`--resume`, `--continue`/`-c`, `--fork-session` or `--session-id`, so every worker is a fresh
session. A test pins that token set as forbidden. Model and effort strings are passed through
unvalidated: the `claude` CLI is the authority, as it already is for `--permission-mode`. Role
*names* are a closed set, and an unknown one is refused before any launch:
- in `--role-model`/`--role-effort`, by the argparse `type=` validator: a usage error, exit 2;
- in the `--routing-config` file, as `RoutingConfigError`: exit 20, like every other malformed
  config.

The resolved route is recorded in the job record's `worker_route` from the `PLANNED` flush onward:
`{role, model, effort, single_agent, fresh_session: true, sources: {model, effort}}`, where each
source is one of `role-cli`/`cli`/`config-role`/`config-default`/`default`/`inherit`.

### Concurrency and worker lifecycle

- **The lifecycle lock** is `fcntl.flock(LOCK_EX | LOCK_NB)` on an `O_RDONLY` descriptor of the
  target worktree's own git directory (`git rev-parse --absolute-git-dir`). It is taken by
  `execute_step` and by `resume`.
  - It writes nothing into the target.
  - It excludes every Controller on the machine working on the same worktree, whatever runtime
    root each one uses.
  - The descriptor is passed to the worker (`subprocess.Popen(..., pass_fds=(fd,))`). The kernel
    keeps a `flock` held while *any* process holds that open file description. So if the
    Controller process dies mid-job, the orphaned worker (and any descendant that inherited the
    descriptor) keeps the lock until it really exits. "The previous worker is still alive" is then
    answered by the kernel, not inferred from silence or elapsed time.
  - Contention is `LifecycleWorkerActiveError` (a new `ControllerError`), mapped to the new exit
    code **45**. Contention means exactly `flock` raising `BlockingIOError`
    (`EWOULDBLOCK`/`EAGAIN`). `cli.main` maps every `ControllerError` to 20
    (`controller/cli.py:635-637`), so `LifecycleWorkerActiveError` gets its own `except` clause
    *before* that blanket handler. A test pins that ordering. The error launches nothing and
    reconciles nothing.
  - **Every other lock failure is not contention** (round 4, O4). An `OSError` from opening the
    git directory or from `flock` with any other errno (`ENOLCK`, `EBADF`, `EINVAL`, ...) is
    `LifecycleLockError`, a new `ControllerError` that is a sibling of
    `LifecycleWorkerActiveError`, not a subclass. It exits 20 through the blanket handler, and its
    message names the path, the operation (`open` or `flock`) and the errno's name. It never
    carries exit 45's "worker active" text, and no `OSError` from the lock escapes uncaught. One
    consequence is stated in "Scope judgments": a filesystem whose `flock` emulation refuses an
    exclusive lock on a read-only directory descriptor cannot be driven.
  - Its message names the lock path and how to find every holder: `fuser -v <git-dir>` /
    `lsof +d`. When a `LAUNCHED` record for the target carries `worker_process` whose liveness
    verdict (below) is `active`, the message also
    names that pid/pgid, and states that any *other* holder is a process that inherited the
    descriptor (for example, a stray background descendant of an earlier worker). Such a process
    keeps the lock until it exits. That is correct, not a false positive: the lock is released
    only when nothing from the earlier worker's process tree still holds it.
  - `probe_lifecycle_lock` (the read-only `explain`/`inspect` report) never acquires the lock,
    because `flock` has no non-acquiring test and an acquire-and-release probe could make a
    concurrent `step` exit 45 spuriously. It reads `/proc/locks` and matches a `FLOCK` entry by
    `major:minor:inode`.
    - **The device is never `os.stat().st_dev`** (round 2, I1). `/proc/locks` prints the
      superblock's device. On btrfs, `st_dev` is the subvolume's anonymous device instead, so an
      `st_dev` match never finds the entry and reports `free` while the lock is held. This
      repository's own `/home` is a btrfs subvolume. Holding the lock on its git directory
      reproduces the mismatch: `os.fstat` gives `00:34:<ino>`, while `/proc/locks` gives
      `00:1d:<ino>` and the mount's `MAJ:MIN` is `0:29` (= `0x1d`).
    - So the probe opens one `O_RDONLY` descriptor of the git directory (opening acquires
      nothing), and takes both identifiers from the kernel's own view of it. The device is the
      `MAJ:MIN` field of the `/proc/self/mountinfo` line whose mount id equals the `mnt_id:` line
      of `/proc/self/fdinfo/<fd>`. `mountinfo` prints the same superblock device `/proc/locks`
      does, and matching by mount id avoids path-prefix matching across bind mounts and symlinks.
      `mountinfo` is decimal and `/proc/locks` hexadecimal, so both are parsed to integers before
      comparing. The inode is `fdinfo`'s `ino:` line only, never `st_ino` (round 3, O1): on
      overlayfs `st_ino` can be a lower layer's inode rather than the one `/proc/locks` prints,
      so a fallback to it could answer `free` from a value the kernel did not report.
    - **The premise behind `free`** (round 4, I2): "no entry matches" proves the lock is free only
      if `/proc/locks` lists every held `flock`. That holds only when it is read through a `/proc`
      mounted for the init pid namespace. The kernel filters `/proc/locks` by the pid namespace of
      the `/proc` mount it is read through (`fs/locks.c`, `locks_show`/`locks_translate_pid`). An
      entry whose recorded owner pid is not visible in that namespace is omitted. A `flock`
      records the pid that *took* it, which here is the Controller. So once the Controller dies
      and is reaped, a lock that an orphaned worker still holds through the inherited descriptor
      disappears from `/proc/locks` in every container with its own `/proc`, while an acquire is
      still refused. Only the init namespace keeps printing the dead taker's pid. Reproduced on
      the development machine with `unshare -Urpf --mount-proc`: while the taker lives, the entry
      is shown in both namespaces. After it dies with a child still holding the lock, the entry
      is gone in the child namespace and still shown in the init one, and both acquires are
      refused.
    - So the probe also reads `os.readlink("/proc/self/ns/pid")`. The init namespace is
      `pid:[4026531836]`, the kernel's constant `PROC_PID_INIT_INO`; no other namespace can get
      that number. The check fails safe. If the reader is in the init namespace and `/proc/self`
      resolves, the mount's namespace is the init one too. A reader in any other namespace answers
      `unknown`, even where its `/proc` happens to be the init namespace's.
    - The answer is `held` when a holder entry matches, in any namespace: an entry that is shown
      is a real holder. `->` waiter lines are not holders. It is `free` only when `/proc/locks`,
      `fdinfo`, `mountinfo` and the namespace link were all read and parsed, the namespace is
      `pid:[4026531836]`, and no entry matches. Every other outcome is `unknown`, never `free`:
      - an unreadable `/proc/locks`, or an unreadable namespace link;
      - an `fdinfo` without `mnt_id:` or without `ino:`, or a mount id absent from `mountinfo`;
      - a line in any of them that does not parse;
      - no matching entry, read from outside the init pid namespace.
- **Recorded lock and worker process.** From this milestone on, the `PLANNED` flush records
  `lifecycle_lock: {"path": <git-dir>}`. That marks the record as written under the lock, so any
  worker it spawned inherited the descriptor. `worker.launch` gains an `on_spawn(WorkerProcess)`
  callback, invoked after `Popen` returns and before `communicate()`. `execute_step` uses it to
  flush `worker_process: {pid, pgid, start_ticks, boot_id, pid_namespace, hostname, machine_id}`
  into the still-`LAUNCHED` record:
  - `start_ticks` is `/proc/<pid>/stat` field 22. The worker cannot have been reaped yet, because
    `communicate()` has not run, so the entry is readable whenever `/proc` is. `stat` fields are
    counted after the line's last `)`, because the `comm` field may contain spaces and
    parentheses.
  - `boot_id` is `/proc/sys/kernel/random/boot_id`, a random id the kernel draws on every boot.
  - `pid_namespace` is `os.readlink("/proc/self/ns/pid")`, for example `pid:[4026531836]`. It is
    the Controller's own namespace, which is the one `Popen`'s pid and the pgid are numbered in.
  - `hostname` is `socket.gethostname()`.
  - `machine_id` is the trimmed content of `/etc/machine-id` (round 4, O2). It is `null` when that
    file is unreadable, empty, or reads `uninitialized`.

  Each is `null` where it cannot be read. One patchable reader, `worker.read_process_context()`,
  returns the current `{boot_id, pid_namespace, hostname, machine_id}`. The spawn-time capture and
  every later liveness check both call it, so tests can change the context without touching
  `/proc`. If the flush fails, the worker's process group is killed and reaped before the error
  propagates, so no untracked worker survives.
  - **The `on_spawn` failure path** (round 4, O4). A process exists by the time `on_spawn` runs, so
    its failure is never a "worker never started" outcome. `worker.launch` invokes `on_spawn`
    *outside* the `try` that converts `Popen`'s `OSError` into `WorkerLaunchError`
    (`controller/worker.py:290-310`). So a flush failure, even one that is itself an `OSError`,
    is never converted into `WorkerLaunchError`, and never recorded `WorkerNotStarted`, which
    the relaunch bound skips. The group is killed and reaped, and then the original error
    propagates unchanged. The record stays `LAUNCHED`, carrying `lifecycle_lock` and no
    `worker_process`. So the next `step` refuses on it, and `resume` reconciles it from evidence
    once the lock is free.
- **A worker that never started is terminal at once** (round 3, O3). `worker.launch` raises
  before any process exists in two cases: `UserOnlyCommandError`, before spawning, and
  `WorkerLaunchError`, when `Popen` itself fails (`controller/worker.py:276`, `:290-310`; for
  example a wrong `--claude-bin`). Today the record stays `LAUNCHED`
  (`controller/job.py:1934-1946`). Under the refusal below, that would force a `resume` although
  the Controller knows for certain that no worker exists. So `execute_step` catches exactly those
  two errors from `worker.launch`, persists the record as `FAILED` with `transition_verified:
  false` and `reconciliation_evidence: {"code": "WorkerNotStarted", "error": <class name>,
  "message": ..., "evidence": <the error's evidence>}`, and then re-raises the same error, so the
  exit code is today's. The apply relaunch bound never counts such a record (CP4B), because no
  worker ran against the bundle.
- **No launch over an unreconciled job.** Under the lock, before deciding, `execute_step` refuses
  with `PendingJobReconciliationError` (exit 20, through the blanket `ControllerError` handler)
  while any job file `resume` would reconcile or raise on for this target is pending:
  - a record whose `target_repo` is this target and whose status is not terminal: `PLANNED`,
    `LAUNCHED`, `COMPLETED`, or a status outside the enumeration, which `resume` also raises on;
  - any entry of `sorted(<runtime>/jobs/.glob("*.json"))` that does not parse as a JSON object.
    That is the scan `resume` makes (`controller/job.py:1612-1613`). It is not recursive, so
    `jobs/abandoned/`, whose copies are unparseable by construction, is outside it (round 4, O4).
    Such a file's target cannot be read, and it could be this target's `LAUNCHED` record, so it
    counts (fail closed). `resume` already aborts for every target on a file that does not parse
    as JSON (`controller/job.py:1616-1622`). It skips a parseable non-object (`:1624`), which
    this refusal still counts.

  One read-only function, `job.pending_reconciliation_jobs(runtime, managed_repo, identity)`,
  makes this scan and never raises on a job file. The refusal and `explain` both call it (round 4,
  O3; CP5).

  The message names each pending job id (the file stem, for an unparseable file). For each one it
  names the command that clears it, running `validate_record` to tell which applies:
  `workflow-controller resume <repo>` for a record `resume` can reconcile, and
  `workflow-controller resume --abandon <JOB_ID> <repo>` for one it cannot (an unparseable file, a
  record failing `validate_record`, or an unknown status). The one exception is a record whose
  `controller_generation` is newer than the running Controller's (`validate_record` case 1,
  `newer_controller_generation`, `controller/job.py:1209-1216`). `--abandon` refuses that record,
  so for it the message names the recorded generation and says that Controller's own `resume`
  clears it (round 3, O5). A `LAUNCHED` record left by a
  Controller that died can no longer be silently replaced by a second worker. (The name is
  deliberately distinct from the existing `UnreconcilableJobError`, which means something else.)
- **Liveness-aware resume.** `resume` takes the lock (held -> exit 45, nothing reconciled). The one
  exception is a target root that no longer resolves to a directory. Then there is no worktree to
  protect and no git directory to lock, so `resume` takes no lock and proceeds exactly as today:
  `validate_record` case 2 raises for a non-terminal record and reports a terminal one. A root
  that still exists but whose git directory cannot be resolved is an error (exit 20), never a
  skipped lock. For a
  `LAUNCHED` record carrying `worker_process`, it additionally refuses to reconcile while the
  recorded process group may still have a running member. The lock is the primary proof; this
  is a second, independent check. It catches a worker that is still running but no longer holds
  the descriptor. A zombie holds no descriptor and is not running, so it can never make this check
  refuse with no override (round 4, I1). Every record carrying `worker_process` also carries
  `lifecycle_lock`, because this milestone's `execute_step` writes both.
- **The liveness verdict** (round 3, I1). `worker.classify_worker_liveness(worker_process) ->
  "active" | "inactive" | "unverifiable"` compares the recorded context with
  `read_process_context()`. It is keyed on `boot_id` first, because a pid or pgid means something
  only inside the boot and the pid namespace that numbered it. "Equal" below means both values
  were read and are the same; a `null` on either side is never equal.
  "The same host" means that both hostnames were read and are equal, *and* both `machine_id`s
  were read and are equal (round 4, O2). A hostname alone is not unique: `localhost`, `ubuntu`
  and `raspberrypi` are defaults, VMs get cloned, and a rescheduled StatefulSet pod keeps its
  name.
  1. **Same boot** (equal `boot_id`s: the same kernel instance).
     - Equal `pid_namespace`s: the process test below decides, whatever the hostname, since a
       hostname can change within a boot.
     - Otherwise: `unverifiable`. The kernel is the same, but the recorded numbers may belong to
       another pid namespace, for example another container.
  2. **Another boot of the same host** (both `boot_id`s read and different, the same host): the
     recorded boot is over, so no process from it can exist. The verdict is `inactive`, and the
     free lock corroborates it. After a reboot, crash or power loss during a worker, `resume`
     therefore reconciles the record from evidence, with no operator step.
  3. **Another or unidentifiable host** (both `boot_id`s read and different, not the same host):
     `unverifiable`. This is a runtime root shared between machines, or a machine whose identity
     cannot be established, and nothing observable here speaks for the other one. It includes
     two machines with one hostname whose `machine_id`s differ, and any host with no readable
     `machine_id`.
  4. **No boot identity** (a `null` `boot_id` on either side, for example no `/proc`). On the
     same host, and unless both `pid_namespace`s were read and differ, the process test runs.
     `not live` means `inactive`, because no running member of the recorded group exists now,
     whichever boot recorded it. Any other answer means `unverifiable`, because without a boot
     identity a live member may be a later boot's reuse of the same number. Any other
     combination is `unverifiable`. This is the row where `/proc` may be unavailable, so the
     `killpg` form below is reachable here.

  **The process test** (rows 1 and 4, round 4 I1). It answers `live`, `not live` or
  `possibly live`. A process counts as *running* only if the state field of its
  `/proc/<pid>/stat` (field 3) is neither `Z` (zombie) nor `X` (dead). An exited process that
  nobody has reaped keeps its pid, its `start_ticks` and its process group, and both
  `os.kill(pid, 0)` and `os.killpg(pgid, 0)` succeed on it. Reproduced on the development
  machine: a `setsid` leader that exits unreaped reads `Z` with its original `start_ticks`, and
  both calls succeed. Inside `unshare -Urpf --mount-proc`, where the reading process is PID 1
  and does not reap, a group whose leader has been reaped and whose only survivor is a zombie
  member still passes `killpg`. The worker runs in its own session (`controller/worker.py:302`),
  so when the Controller dies it is reparented to the nearest subreaper or to the namespace's
  PID 1. In a container whose PID 1 does not reap (`sleep infinity`, `tail -f /dev/null`, any
  image run without `--init`), an orphaned worker that exits stays a zombie indefinitely.
  - **The `/proc` form** is used whenever the Controller's `/proc` numbers processes in its own
    pid namespace, that is, `os.readlink("/proc/self")` equals `str(os.getpid())`. Then:
    1. The leader's `/proc/<pid>/stat` shows the recorded `start_ticks` and a running state:
       `live`.
    2. The leader's pid is present with *different* `start_ticks`: `not live`. The pid was reused
       after the recorded group ended, because Linux never reuses a pid that still names a process
       group, zombie members included.
    3. Otherwise, that is, the leader is absent, the leader is a zombie with the recorded
       `start_ticks`, or `start_ticks` is `null`, the group's members decide. Every
       `/proc/<n>/stat` is scanned, and the answer is `live` iff some running process has
       `pgrp` (field 5) equal to the recorded pgid. Members can outlive their leader. An entry
       that disappears during the scan is skipped, because it exited. If `/proc` cannot be
       listed, or a `stat` line does not parse, the `/proc` form has no answer.
  - **The `killpg` form** is used only where the `/proc` form is unavailable or has no answer.
    `os.killpg(pgid, 0)` raising `ProcessLookupError` is `not live`: no process of that group
    exists, zombie or not, in the Controller's own namespace, which is the one `killpg` uses.
    Success or `PermissionError` is only `possibly live`, because `killpg` cannot tell a zombie
    from a running process.

  In row 1, `live` is `active`, `not live` is `inactive`, and `possibly live` is
  `unverifiable`. So `active` is the only verdict that positively observes a *running* member of
  the recorded group in this boot and pid namespace. A zombie never makes a record `active`, so
  a container whose PID 1 does not reap cannot hold one there. Every `active` refusal ends when
  that running process exits or is ended.

  `resume` acts on the verdict:
  - `active`: the record is left `LAUNCHED` and returned with `resume_marked: {"outcome":
    "worker_active", ...}`, and `cmd_resume` exits 45. The message names the recorded pid and
    pgid as the process group to end.
  - `unverifiable`: the record is left `LAUNCHED` and returned with `resume_marked: {"outcome":
    "worker_unverifiable", ...}`, and `cmd_resume` also exits 45. The message names the recorded
    and current `hostname`, `machine_id`, `boot_id` and `pid_namespace`, and, for a `possibly
    live` answer, that `killpg` cannot tell a running member from a zombie. It names **no**
    process group to kill, because the recorded one cannot be tied to anything running here. And
    it names the operator disposition,
    `resume --abandon <JOB_ID> --acknowledge-unverifiable-worker <repo>`.
  - `inactive`: reconciliation proceeds as for any other record.
- **No kill advice across boots.** An exit-45 message names the recorded pid/pgid only when that
  record's verdict is `active`. This holds for `resume`'s liveness refusal and for the
  lock-acquisition refusal in `execute_step`, `resume` and `--abandon` alike. For any other
  verdict, the lock-acquisition message names the lock holders through `fuser -v <git-dir>`/`lsof
  +d` only, and says why the recorded process group is not named. After a reboot, that group's
  number may name an unrelated live group of the user's.
- **Records written before this milestone** carry no `lifecycle_lock`. For them the lock proves
  nothing: their worker never inherited it. They keep exactly today's reconciliation. The
  "the lock already proves no process from that job holds the descriptor" reasoning applies only
  to records carrying `lifecycle_lock`. That includes one that crashed between the `LAUNCHED`
  flush and `Popen`, where no worker exists at all.
- **A terminal disposition for every unreconcilable record.** Today `_reconcile_launched` raises
  `UnreconcilableJobError` without persisting (`controller/job.py:1504-1520`), and `resume`
  aborts on that first raise (`:1616-1665`), so the record stays `LAUNCHED` forever. Combined with
  the refusal above, that would wedge the target permanently, before the evidence-based gate this
  milestone adds is ever shown. Realistic triggers are exactly this milestone's failure class: the
  Controller dies during a final-pass `/milestone-implement` or an `/apply-implementation-review`,
  the orphaned worker commits T, and then its generator fails.
  - **Record carries `lifecycle_lock`.** The lock is free (`resume` holds it), and the liveness
    verdict is `inactive` (or the record carries no `worker_process`), so the worker has
    definitively ended. "Unreconcilable" is then a verdict about a
    finished job. `_reconcile_launched` first persists it as `FAILED`, with
    `transition_verified: false`, `reconciled_at`, and
    `reconciliation_evidence: {"code": "UnreconcilableJobError", ...}`. The evidence carries the
    pre/observed phase and `HEAD`, plus `postcondition_detail` where applicable. Only then does it
    raise `UnreconcilableJobError` as today, so this `resume` still exits 20 and a human sees it
    once. `FAILED` is exactly what `_reconcile_completed` already records for moved state that
    does not verify. The next `resume` passes the now-terminal record, and the next `step` decides
    from evidence: for the trigger above, CP4B's regenerate or malformed-T gate.
  - **Record without `lifecycle_lock`** (written before this milestone). Unreconcilable stays
    raise-without-persist, because a pre-milestone orphan may still be alive and the lock cannot
    prove otherwise. `resume --abandon` (next item) is its way out.
- **`workflow-controller resume --abandon JOB_ID`** (round 2, O2) is the operator disposition for
  every pending job file `resume` cannot reconcile, not only pre-milestone records. `resume` raises
  `StaleJobRecordError` without persisting in three cases (`controller/job.py:1616-1665`):
  - a non-terminal record that fails `validate_record` (an unknown schema, a vanished work item,
    an unreadable target state, ...);
  - a status outside the enumeration;
  - a file that does not parse.

  Under the refusal above, each of these stops `step`/`run` for the target, and `resume` alone can
  never clear it. A `LAUNCHED` record whose liveness verdict is `unverifiable` is a fourth case:
  `resume` leaves it `LAUNCHED` (exit 45), and deciding that the worker on another host or in
  another namespace is gone is operator judgment, which is what `--abandon` exists for.

  `--abandon` runs under the lock and launches nothing. It calls `validate_record` only to record
  its failure reason as evidence, never to decide. It reads only the fields it needs.
  - **Which file** (round 3, O5). `JOB_ID` must name a pending job file directly: a non-empty
    bare file stem with no `/`, no NUL, and not `.` or `..`, such that `<runtime>/jobs/<JOB_ID>.json` is one of the
    entries `sorted(jobs_dir.glob("*.json"))` yields. That is the same scan the
    pending-reconciliation refusal and `resume` make. The entry must be a regular file, not a
    symlink, whose resolved parent is `<runtime>/jobs/` itself. Anything else is refused before any
    read, naming the constraint and the pending files' stems. `runtime.write_json`/`write_bytes`
    contain writes to the runtime
    *root*, not to `jobs/` (`controller/runtime.py:98-106`, `:120-128`). So without this
    constraint, a `JOB_ID` of `../identity` would pass containment, and the runtime root's own
    `identity.json` would count as an unknown-`schema_version` file and be replaced by a minimal
    job record. A symlink under `jobs/` would redirect the replacement the same way.
  - **Marked `FAILED` in place:** a parseable record with a known `schema_version`, whose
    `target_repo` is this target and whose status is not terminal. It gets
    `reconciliation_evidence: {"code": "OperatorAbandoned", "abandoned_status": <old status>,
    "validity": <validate_record's failure reason, or null>}`. This covers a pre-milestone
    unreconcilable record, a record failing `validate_record` cases 2-4, and an unknown status. A
    record carrying `worker_process` also gets `"worker_liveness": {"verdict": ..., "recorded":
    {...}, "current": {...}}`, with both contexts.
  - **Replaced:** a file that does not parse as a JSON object, or a record with an unknown
    `schema_version`. No field of it can be trusted, so its original bytes are first copied
    unchanged to `<runtime>/jobs/abandoned/<file name>`, which is outside the `jobs/*.json` glob.
    The file is then replaced by a minimal terminal record: `job_id` is the file stem,
    `target_repo` is `null`, status is `FAILED`, and `reconciliation_evidence` is
    `{"code": "OperatorAbandoned", "original": "jobs/abandoned/<file name>"}`. Both writes go
    through the existing `runtime.write_bytes`/`runtime.write_json`. Its target cannot be read,
    so any target's `--abandon` accepts it, and the message says so.
  - **The liveness verdict decides for a record carrying `worker_process`:**
    - `active`: refused, naming the recorded pid/pgid. No flag overrides it. It becomes abandonable
      when that running member exits or is ended; a zombie never counts (round 4, I1);
    - `unverifiable`: refused unless `--acknowledge-unverifiable-worker` is given. The refusal
      names the recorded and current `hostname`, `machine_id`, `boot_id` and `pid_namespace`, and
      the flag. With
      the flag, the record is marked `FAILED` as above, and `worker_liveness` records the
      acknowledged verdict. The flag is accepted only with `--abandon`; anywhere else it is a
      usage error (exit 2);
    - `inactive`: accepted, with no flag.
  - **Refused:**
    - a `JOB_ID` that does not name a pending job file directly (above);
    - a terminal record;
    - a record for another target;
    - a record the liveness verdict refuses (above);
    - a parseable record whose `controller_generation` is an integer newer than the running
      Controller's, whatever its `schema_version`. This is checked before the marking and
      replacement rules above. That newer Controller's own `resume`/`--abandon` handles it.

  `PendingJobReconciliationError`, `resume`'s `StaleJobRecordError` for a non-terminal record,
  and a pre-milestone `UnreconcilableJobError` all name `resume --abandon <JOB_ID> <repo>` in their
  messages. The exception is a newer generation's record, for which they name that generation
  instead. `resume`'s `worker_unverifiable` message names the flagged form.

  **No pending job file wedges a target permanently.** Every refusal of `--abandon` is one of:
  - transient: the lock is held, or a running member of the recorded group, never a zombie, is
    observed in this boot and pid namespace. Either ends when that process exits or is ended. An
    exited worker that nobody reaps, for example under a container PID 1 that does not reap,
    holds no descriptor and is not running, so it holds neither (round 4, I1);
  - a pointer to the Controller that can act (a newer generation);
  - a pointer to the acknowledgement that overrides it (`unverifiable`);
  - a malformed `JOB_ID`, where the message names the pending files' stems.

  A reboot during a worker needs neither `--abandon` nor the flag: row 2 of the verdict makes
  the record `inactive`, and `resume` reconciles it.
- **Time is not termination.** `DEFAULT_WORKER_TIMEOUT` becomes `None`: `communicate()` waits until
  the worker returns control. `--timeout` stays as the explicit operator opt-in. When set, it kills
  and reaps the whole process group before classifying, as today. The Controller has no other path
  that concludes a worker has ended.
- **Ctrl-C ends only the Controller** (round 4, usability note). With no default timeout, Ctrl-C
  is the operator's natural way to stop. But the worker runs in its own session, so it keeps
  running headless, holding the lock and the worktree. So when `KeyboardInterrupt` reaches
  `execute_step` after the `worker_process` flush, it writes one line to stderr and then re-raises
  the interrupt unchanged, so the exit behavior is today's. The line names the worker's pid and
  pgid, says that it keeps running and holds the worktree, and names both ways on: wait for it,
  or end the group; then run `workflow-controller resume <repo>`. The record stays `LAUNCHED`
  with `worker_process`, so `resume` then applies the liveness verdict. The Controller does not
  forward the signal to the worker. Ending the worker is the operator's decision, not a side
  effect of stopping the Controller.
- **Durable state over prose.** No decision or verification reads the worker's `result` text.
  `result_excerpt` is report data. A test proves that success-claiming prose over an unchanged
  state is `FAILED`, and an AST test pins that `WorkerResult.result`/`raw_json` feed only
  `_worker_dict`.

## Scope judgments (for the reviewer to confirm or cut)

- **Coherence is checked at the revision and binding level, never at the content level.** This
  keeps the previous milestone's boundary. Controller compares labelled manifest lines, ledger
  fields, feedback binding lines and committed state against each other and against `HEAD`. It
  never recomputes `review_content_id`/`bundle_id` and imports nothing from `scripts/`.
  Consequence, stated honestly: an *uncommitted* protected-content edit made after generation is
  invisible to Controller. Workflow's own commands (`/review-implementation` A3,
  `/record-manual-implementation-review` step 5) recompute and refuse on it, and the worker's
  refusal then lands the job `FAILED` or `INTERRUPTED`.
- **The lock is per target worktree, which is stricter than "per work item".** Two work items in
  one worktree share a git index and working tree, so running two workers there is unsafe anyway.
  The same work item driven from *two different worktrees* by Controllers using *different runtime
  roots* is not excluded by this lock. There, Workflow's own `D-Checkpoint-Ownership` claims
  arbitrate `/milestone-implement`, and the review commands' binding checks refuse stale input.
  This residual case is documented, not closed.
- **`"2.1"` external-verdict consumption stays report-only.** At `"2.1"`
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, applying a pasted external `REVISE` through
  `/apply-implementation-review` is model-invocable. But `"2.1"` feedback carries no reviewer-role
  or content-id binding, and there is no local stage. This milestone keeps that phase's `"2.1"`
  behavior byte-identical, per "preserve existing supported protocol behavior". It could be added
  later with one row plus an admissibility evaluator. `"2.1"` `/milestone-implement` *is*
  automated: its outcome is fully verifiable.
- **Automated manual-verdict ingestion mirrors the plan stage.** At
  `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, an admissible
  `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` verdict is ingested automatically, exactly as Gen-1
  already does for `/record-manual-plan-review`. The milestone scope authorizes this ("may consume
  a valid already-supplied manual external verdict"). `BLOCK` and inadmissible verdicts stay
  gates. The frozen command's "already pasted into `REVIEW_FEEDBACK.md` in the current turn"
  wording is satisfied by the paste itself: the Controller launches the ingestion only after a
  human has placed the verdict on disk, and never writes that file. One narrowing beyond the
  command's own rules: a manual `REVISE` whose `Reviewed bundle ID` differs from the manifest's is
  a gate, not an ingestion. The command treats that mismatch as advisory, but
  `/apply-implementation-review` step 1 then refuses on it, so ingesting it would wedge the item
  at `APPLYING_REVIEW_FEEDBACK`. A mismatched `APPROVE` stays advisory, as in the command.
- **The row-18 task addendum** ("The pending review-stage state write"). This is the one place the
  Controller's worker task is not the bare selected command. It is attached only at `"2.2"`
  `APPLYING_REVIEW_FEEDBACK`, only while `HEAD` does not yet record that phase, and it authorizes
  exactly the one state-only commit every precedent's operator made. Cut it, and the fallback is a
  gate naming that commit for a human, with R4 amended to drop "without human action".
- **`/milestone-implement`'s final pass is routed single-agent.** The routing table distinguishes
  checkpoint implementation, which may fan out bounded helpers, from the final self-review pass,
  which may not. It uses only durable state (`phase`, `registry_complete`). This applies the
  standing "delegation for doing, never for judging" policy. Cut it if the reviewer prefers one
  `/milestone-implement` route.
- **Roles not named in the milestone scope inherit.** `milestone-plan`,
  `record-manual-plan-review` and `record-manual-implementation-review` pass no `--model`/`--effort`,
  which preserves today's behavior. The config file can route them explicitly.
- **Default timeout becomes unbounded.** This is a behavior change for `step`/`run` without
  `--timeout`: a genuinely hung worker now holds `run` until someone ends it. Ctrl-C stops only
  the Controller, and says so, naming the worker's process group (Design). The exit-45 message
  names the process group to kill when a running member of the recorded group is observed in
  this boot and pid namespace (`active`), and never otherwise. The alternative, a longer finite
  default, still
  converts a long test run into termination, which the scope forbids. "Wait forever" cannot
  become "wedged forever": every unreconcilable record gets a terminal disposition, a reboot
  during a worker reconciles without an operator step, and the exit-45 message names every lock
  holder, including stray descendants.
- **`--acknowledge-unverifiable-worker` is the one operator override of the liveness check**
  (round 3, I1). It applies only to a record from another or unidentifiable host or from another
  pid namespace, to one from a context with no boot identity whose recorded group may be live
  now, and to one where only `killpg` could be asked and it could not tell a running member from a
  zombie. Nothing observable here can decide whether that worker still runs. R8's "no replacement worker launches until the previous
  one is definitively terminal" then rests on the operator's explicit statement, which the record
  keeps as `worker_liveness`. The flag never overrides `active` or a held lock. The alternative,
  refusing forever, is the wedge round 3 found.
- **A host is identified by its hostname and its `machine_id` together** (round 4, O2). Row 2's
  "another boot of the same host -> `inactive`" needs both to be read and equal, so two machines
  that share a hostname and a runtime root fall to `unverifiable` when their `machine_id`s differ
  or cannot be read. The residual assumption: two machines never share *both*. An image that bakes
  one `/etc/machine-id` into every container, run under a reused hostname, breaks it. The lock
  would still exclude the second Controller where the filesystem propagates `flock` across
  hosts, but some network mounts do not (for example NFS with `local_lock`). A host with no
  readable `machine_id` pays one acknowledged `--abandon` after a reboot during a worker, instead
  of none.
- **Within one boot, a pid-namespace number can be recycled** after its namespace dies (round 4,
  answer to the first unresolved question). So equal `pid_namespace` strings recorded across two
  container lifetimes do not prove that the namespace is the same. The leader's `start_ticks`
  check catches a reused leader pid. The narrow residual is a record from a dead container,
  kept in a runtime root that outlives it. Its leader is absent, and in a later container that
  got the same namespace number, a *running* group with the recorded pgid has lost its own
  leader. That record reads `active` until that group ends. It is documented, not closed.
- **The lifecycle lock needs `flock` to accept an exclusive lock on a read-only directory
  descriptor** (round 4, O4). `flock(2)` documents that NFS clients since Linux 2.6.12 emulate
  `flock` with byte-range locks, so an exclusive lock there needs a file opened for writing, and a
  directory cannot be opened for writing. CIFS since Linux 5.5 also emulates `flock` with SMB
  byte-range locks; how those treat a directory handle is not documented, and is not verified
  here. Where the filesystem refuses the lock, `step`, `resume` and `--abandon` exit 20 with
  `LifecycleLockError`, naming the errno. That is a behavior change: today the Controller drives
  such a worktree without any lock. Proceeding without the lock would give up R8, and
  locking a regular file would need a writable descriptor on a file in the target, which the
  design avoids. So this is the fail-closed direction. An NFS mount with `local_lock=flock` or
  `local_lock=all` takes the lock locally, and then it excludes only Controllers on the same
  machine.
- **In a container, the lock probe mostly answers `unknown`** (round 4, I2). Outside the init pid
  namespace, "no matching entry" does not prove the lock is free, so `explain`/`inspect` report
  `unknown` there whenever nobody visibly holds it. `step` is unaffected, because acquisition
  decides.
- **Convergence is bounded by `--max-steps`, not by a new round counter.** A local
  `REVISE`->apply->review cycle is two jobs, so the default 20 steps allows roughly ten rounds before
  exit 16. Judgment-based circuit breakers ("findings confined to review apparatus") need a human
  and stay out of Controller. One mechanical bound is added: an `/apply-implementation-review`
  attempt that did not verify is never relaunched automatically against the same bundle, that
  is, while the manifest's `bundle_id` is still the one the attempt started from (CP4B). Once
  such an attempt has passed its own step 4, the bundle no longer matches its manifest, and every
  retry would refuse at step 1.
- **The plan-stage `BLOCK`-predicate fix changes one plan-stage outcome.** A genuine local
  plan-review `BLOCK` job now verifies `FINISHED` instead of `FAILED`, and the next decision is the
  existing `BLOCK` gate either way. The fix is required for the new implementation-stage `BLOCK`
  row to be correct, and the two predicates share one helper. The reviewer may cut the plan-stage
  half. The implementation-stage row is correct either way.

## Checkpoints

<!-- generated by workflow_state.render_registry_markdown -- do not hand-edit -->
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Plan-stage decision golden generated first from base code, and implementation-stage evidence readers: WorkItemView.implementation_review_stages/technical_review_block_pins, implementation-stage MANIFEST.md labels, implementation_bundle_coherence, two-stage ledger reads, committed work-item and checkpoint reads, the generation-record view and the admissibility evaluators, with per-clause tests | - | 4 | 1 |
| CP2 | ExpectedOutcome rows, per-phase postconditions and predicates for every newly automated implementation-stage command, execute/resume parity, WriterCall trailing-call allowlist, and the plan-stage BLOCK-predicate bundle-id fix | CP1 | 5 | 1 |
| CP3 | General automatic-dispatch rule (automatic iff a declared ExpectedOutcome triple exists, declined otherwise, including at unreachable version combinations), golden-asserted plan-stage decisions, IMPLEMENTING/SELF_REVIEWING_IMPLEMENTATION dispatch, the /apply-functional-review AssertionError fix, and every named test whose assertion changes | CP2 | 4 | 1 |
| CP4 | Implementation-review decision handlers: REJECTED and implementation-bundle coherence gates, 2.2 local review, admissible manual-verdict ingestion, the corrected APPLYING_REVIEW_FEEDBACK gate, and ledger-, feedback- and pin-aware AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW | CP1, CP3 | 4 | 1 |
| CP4B | Implementation-bundle recovery and row-18 remediation: generation-record-aware recovery gate (malformed record, provenance-only, regeneration with the stage derived from the record's parent phase), 2.2 APPLYING_REVIEW_FEEDBACK automation with the pending-write task addendum, and the apply relaunch bound | CP4 | 4 | 1 |
| CP5 | Worker lifecycle and concurrency safety: worker-inherited worktree lock with a non-acquiring, pid-namespace-aware probe, recorded lock and worker process, refusal while an earlier job is pending reconciliation, boot- and host-keyed, start_ticks- and zombie-aware liveness, a terminal disposition for unreconcilable jobs and resume --abandon, exit 45, unbounded default worker timeout | - | 5 | 1 |
| CP6 | Role-based model/effort routing: routing table with Opus 5.5 xhigh defaults for the five named lifecycle roles, single-agent fresh-session review workers, CLI/config override precedence, worker argv and job-record route provenance | - | 3 | 1 |
| CP7 | End-to-end lifecycle regression suite with a scripted fake worker: checkpoints, self-review, local REVISE, remediation with the pending-write addendum, fresh review, manual gate, manual-verdict ingestion, fail-closed bundle cases, literal-worker and relaunch-bound apply rounds, same_content recovery, resume, concurrency and unchanged plan stage | CP3, CP4, CP4B, CP5, CP6 | 5 | 1 |
| CP8 | Operator documentation: README CLI surface, options, job dispositions and safety model; ADR 0001 exit-code table and automatic-dispatch rule; exit-code consistency test rewired to the live CLI constants | CP3, CP4, CP4B, CP5, CP6 | 3 | 1 |
| CP9 | Full verification: Controller suite, frozen Workflow conformance suites, live disposable-repository implementation-lifecycle run with a real apply round and routing evidence, live single-agent probe, and a live concurrency drill | CP7, CP8 | 4 | 1 |

Checkpoints are implemented in table order. CP5 and CP6 have no logical dependency on CP1-CP4B,
but they touch the same `job.execute_step`, so they follow CP4B to avoid rebasing the same function
twice. Revision 2 split revision 1's CP4 into CP4 (the per-phase review handlers) and CP4B (the
implementation-bundle recovery gates, the row-18 task addendum and the apply relaunch bound),
because round 1's findings roughly doubled that checkpoint's scope. `CP4B` is a new id; no id is
reused.

<!-- CP1 -->
### CP1 -- implementation-stage evidence readers

**First act: the plan-stage decision golden** (see CP3 for its content and normalisation). It is
generated before any other change of this milestone, from the base commit's unchanged
`decision.py`/`evidence.py`, by the checked-in `tests/golden/generate_plan_stage_decisions.py`.
It lands in this checkpoint, together with its test (`tests/test_golden_plan_stage_decisions.py`).
That test is then green at every later checkpoint, which also shows that CP1's unwired readers and
CP2's `job.py`-only changes alter no decision.

All other additions are pure, read-only functions in
`controller/target_state.py`/`controller/evidence.py`, and all of them fail closed. None is wired
into a decision yet.

- `WorkItemView.implementation_review_stages: dict | None` and
  `WorkItemView.technical_review_block_pins: tuple[dict, ...]`, read verbatim from the entry
  (additive; `tests/fixtures.build_work_item_view` defaults them to `None`/`()`).
- `evidence._MANIFEST_LABELS` gains `review_content_id`, `implementation_revision`,
  `reviewed_implementation_head` and `worktree_root`, keeping the existing absent -> `None`
  semantics. Existing keys and callers are unchanged.
- `evidence.implementation_bundle_coherence(root, work_item, current_head, *,
  require_current_generation_head: bool) -> (coherent, clause, detail)`: the single definition of
  "the current implementation bundle belongs to this work item's current implementation round".
  Coherent iff the implementation-stage `<bundle_dir>` (`resolve_bundle_dir(..., phase=<an
  implementation-stage phase>)`, scoped-else-flat) has a readable `MANIFEST.md` whose:
  - `stage` is `implementation`;
  - `work_item_id` matches;
  - `bundle_id` is present;
  - `worktree_root` equals the target's `git rev-parse --show-toplevel`: the value
    `current_worktree_root_and_head` records and `assert_local_generation_matches` compares
    (`scripts/workflow_fingerprint.py:2607-2612`, `:2690-2699`). Every consuming command runs that
    check;
  - `implementation_revision` is a decimal equal to the state's `implementation_revision`;
  - `reviewed_implementation_head` equals the state's `reviewed_implementation_head`;
  - `generation_head` equals `current_head`, **only when `require_current_generation_head`**.

  The flag is `False` at `APPLYING_REVIEW_FEEDBACK` alone, because `HEAD` is legitimately ahead
  of `generation_head` there: the pending-write commit (the row-18 addendum's, or a human's) and
  any fix commits land before T. `/recover-implementation-provenance` is not legal from that phase
  (`bundle_generation_recovered_role_legal_committed_phases`), and the command's own step 7
  (`resolve_bundle_generation_outcome`) is what handles those commits. So demanding a current
  `generation_head` there would refuse a legitimate round. That is the whole rationale. Revision 1
  also called a retry after an interruption "legal". That holds only for an interruption before the
  command's step 4: step 4 writes rejection notes into `<bundle_dir>/IMPLEMENTATION_SUMMARY.md`,
  which `compute_bundle_id` hashes, so once step 4 has run every retry refuses at step 1 on a
  bundle-id mismatch. CP4B's relaunch bound handles that case.

  A `None` on any state side is incoherent. `detail` names the first failing clause and its
  observed value, and a separate `clause` code (`absent`, `stage`, `work_item_id`, `bundle_id`,
  `worktree_root`, `implementation_revision`, `reviewed_implementation_head`, `generation_head`)
  lets CP4B choose the right recovery text without parsing prose.
- `evidence.read_implementation_review_ledger(work_item) -> LedgerView`: the ledger's
  `review_content_id`, and each stage's `{verdict, bundle_id, round}` or `None`. It is shape-tolerant
  and fail-closed: a malformed ledger reads as "no stage recorded".
- `evidence.committed_work_item(root, work_item_id, rev="HEAD") -> dict | None`: the work item's
  entry in `git show <rev>:docs/ai-workflow/WORKFLOW_STATE.json`. `None` means unreadable or
  absent.
- `evidence.committed_checkpoint_statuses(root, rev="HEAD") -> dict[str, str] | None`: the
  `checkpoints` statuses of the work items in that committed file, keyed
  `(work_item_id, checkpoint_id)`. This is the same committed fact
  `workflow_state.committed_checkpoint_status` reads. `None` means unreadable.
- `evidence.generation_record_view(root, work_item_id, manifest_generation_head) ->
  GenerationRecordView`, read with plain `git log`/`git show`, and never by importing `scripts/`:
  - `head_role`: `"ordinary"`, `"recovered"` or `None`. `HEAD`'s trailer set is classified exactly
    as `_bundle_generation_record_role` does, and only for a `Workflow-Bundle-Generation-Record`
    value naming this work item;
  - `head_phase`/`parent_phase`: the work item's committed phase at `HEAD` and at `HEAD^`;
  - `newer_records`: the first-parent commits in `manifest_generation_head..HEAD` carrying this
    work item's generation-record trailer, newest first, and `None` when
    `manifest_generation_head` is not an ancestor of `HEAD`;
  - `latest_record_parent_phase`: the committed phase at the parent of `newer_records[0]`.
- Admissibility evaluators, in the existing `ClauseFailure`/`AdmissibilityResult` shape:
  - `evaluate_manual_implementation_stage_admissibility(feedback, manifest, review_request,
    work_item, current_head)`: role exactly `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` (no alias,
    unlike the plan stage); `Status` valid; the three `WFR-03` binding lines present; `Work item`
    and `Reviewed base commit` equal; `Reviewed review content ID` equal to the ledger's
    `review_content_id` (hard); the ledger's `review_content_id` equal to the manifest's;
    `<bundle_dir>/REVIEW_REQUEST.md`'s `review_content_id:` line equal to the manifest's (the
    command reads that file at its step 4, and the generator asserted the equality at generation,
    so a difference means a post-generation edit); a `LOCAL_MODEL_IMPLEMENTATION_REVIEW` `APPROVE`
    recorded and no manual stage yet recorded; `generation_head` and `worktree_root` current (the
    coherence clauses). A `bundle_id` mismatch is **advisory for `APPROVE` and a hard failure for
    `REVISE`**: the command itself treats it as advisory, but an ingested mismatched `REVISE` moves
    the item to `APPLYING_REVIEW_FEEDBACK`, where `/apply-implementation-review` step 1 refuses on
    the same mismatch. This mirrors `validate_manual_implementation_review_preconditions` plus the
    command's step 5/6 as a filter: a verdict it admits is never one the command, or the command
    the admitted verdict leads to, refuses on evidence Controller can see.
  - `evaluate_apply_implementation_review_admissibility(feedback, manifest, work_item,
    current_head)`: binding lines present; `Reviewed bundle ID` equal to the manifest `bundle_id`
    (hard, per `apply-implementation-review.md` step 1's `assert_feedback_matches_bundle`);
    `Work item`/`Reviewed base commit` equal. Two further clauses are **not** the command's own:
    step 1 checks neither role nor `Status`, and it pins and then applies a `BLOCK`. They are this
    Controller's deliberately narrower automation scope, fail closed:
    - role exactly `LOCAL_MODEL_IMPLEMENTATION_REVIEW` or `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`;
    - `Status` exactly `REVISE`.

    The automated `"2.2"` loop reaches `APPLYING_REVIEW_FEEDBACK` only through a two-stage writer's
    `REVISE` branch (`record_local_implementation_review`/`record_manual_implementation_review`);
    a `BLOCK` from either writer is a no-op that never leaves its review phase. Any other verdict
    on file at this phase is therefore human territory. Revision 1 called an `APPROVE` here
    "incoherent", and that was wrong: a late fix entered from `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
    over the manual `APPROVE` is legal (`enter_applying_review_feedback` admits that phase), and if
    interrupted it sits here with that `APPROVE` on file. A `BLOCK` here means the file changed
    after the transition, and `review-implementation.md` A7 reserves a `BLOCK` for explicit user
    resolution. There is deliberately **no** `generation_head` clause, for the reason stated under
    `implementation_bundle_coherence`.

`tests/fixtures.py`:
- `build_manifest_text` gains the three new optional labels (default omitted, so existing output
  is byte-identical);
- `build_implementation_manifest_text(...)`/`write_implementation_manifest(...)` helpers, the
  counterparts of the plan-stage ones.

**Files**: `controller/target_state.py`, `controller/evidence.py`, `tests/fixtures.py`,
`tests/test_evidence.py`, `tests/test_target_state.py`, `tests/golden/generate_plan_stage_decisions.py`,
`tests/golden/plan_stage_decisions.json`, `tests/test_golden_plan_stage_decisions.py`.

**Tests**:
- the golden reproduces from the code as it stands (green from this checkpoint on);
- per clause, one failing case each for `implementation_bundle_coherence`, covering:
  - an absent `current/`;
  - a flat-layout bundle;
  - `stage: plan`;
  - the wrong work item;
  - no `bundle_id`;
  - a different `worktree_root`;
  - a stale or non-decimal `implementation_revision`;
  - the wrong `reviewed_implementation_head`;
  - a `generation_head` behind `HEAD`;
  - a state-side `None`;
- a positive case;
- a malformed-ledger case per shape;
- `committed_checkpoint_statuses`/`committed_work_item` against a real temporary git repository,
  where a committed `COMPLETE`/phase differs from an uncommitted working-tree value;
- `generation_record_view` against a real temporary git repository:
  - an ordinary T whose parent records `APPLYING_REVIEW_FEEDBACK`;
  - an ordinary T whose parent records the same phase as T (the malformed shape);
  - a recovered-role (`Workflow-Supersedes`) T;
  - an excluded-only commit after T (`head_role` `None`, `newer_records` still naming T);
  - a non-ancestor `generation_head`;
  - a trailer naming another work item, which is ignored;
- each admissibility clause failing independently, plus:
  - the `APPROVE` bundle-id advisory;
  - the `REVISE` bundle-id hard failure;
  - the `REVIEW_REQUEST.md` mismatch;
  - an apply-stage `APPROVE`/`BLOCK`, each refused with the corrected reason.
<!-- /CP1 -->

<!-- CP2 -->
### CP2 -- expected outcomes for the implementation stage

**Per-phase postconditions.** The single `postcondition`/`postcondition_phases` pair becomes
`postconditions: tuple[tuple[frozenset[str], PostconditionFn], ...]`. Property rules:
- the phase sets are pairwise disjoint;
- each set is a subset of `to_any_of`;
- a row may have none.

The existing seven plan-stage postcondition rows migrate mechanically to single-entry tuples, and
their evaluated behavior is unchanged (pinned by the existing CP3/CP5 tests of the previous
milestone). `_row_clauses_failure` evaluates the entry whose set contains the observed phase.
`execute_step`, `_reconcile_completed` and `_reconcile_launched` keep sharing that one helper.

**New pre-state field**: `bundle_manifest_bundle_id` (the pre-state manifest's `bundle_id`), added
to `PRE_STATE_FIELDS` and to both `_capture_pre_state` branches (`None` for the bootstrap). A
record written before this milestone lacks it, and every predicate reading it treats absence as
"not satisfied" (fail closed).

**Predicates and postconditions**:
- `_predicate_checkpoint_completed_durably(root, wid, pre_state)`. At least one checkpoint:
  - is `COMPLETE` in the fresh state but was not `COMPLETE` in `pre_state["checkpoints"]`;
  - is also `COMPLETE` in `committed_checkpoint_statuses(root, "HEAD")`.

  In addition, `HEAD != pre_state["target_head"]`. Inputs: `checkpoints`, `target_head`.
- `_postcondition_self_review_entered_durably` (the `SELF_REVIEWING_IMPLEMENTATION` entry of rows
  12/13):
  - the fresh `registry_complete is True`;
  - every registry checkpoint is `COMPLETE` in `committed_checkpoint_statuses(root, "HEAD")`;
  - the work item's committed phase at `HEAD` (`committed_work_item`) is
    `SELF_REVIEWING_IMPLEMENTATION`.

  It deliberately does not require a checkpoint to have *newly* completed. From `IMPLEMENTING`
  with every checkpoint already `COMPLETE` (the `NO_CHECKPOINT` path after a plan re-approval,
  `milestone-implement.md:104-113`), step 2 commits the `SELF_REVIEWING_IMPLEMENTATION`
  transition alone (`:257-263`). A run that stops right after that commit is a legal partial run,
  and requiring a newly completed checkpoint would record it `FAILED`. On the ordinary path, step
  1f's own checkpoint commit makes all three clauses hold as well.
- `_postcondition_implementation_bundle_coherent`: `implementation_bundle_coherence` against the
  fresh post-state and the live `HEAD`, plus no `REJECTED` marker (`rejected_marker_detail`).
- `_postcondition_implementation_bundle_regenerated`: the coherent clause, plus the manifest's
  `generation_head != pre_state["bundle_manifest_generation_head"]`. A new generation must have run
  this job. This catches a `same_content` post-fix whose revision does not move.
- `_predicate_local_implementation_block_current`: fresh `REVIEW_FEEDBACK.md` with role exactly
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW`, `Status: BLOCK`, and `Reviewed bundle ID` equal to a
  non-null `pre_state["bundle_manifest_bundle_id"]`.
- `_postcondition_local_implementation_approve_recorded`:
  - the fresh ledger's `LOCAL_MODEL_IMPLEMENTATION_REVIEW` is `APPROVE` with `bundle_id` equal to
    the current manifest `bundle_id`;
  - the ledger's `review_content_id` equals the manifest's `review_content_id`;
  - the manual stage is `None`;
  - the feedback on file is role `LOCAL_MODEL_IMPLEMENTATION_REVIEW`, `Status: APPROVE`, with
    `Reviewed bundle ID` = the manifest `bundle_id`.

  There is no clause on the feedback's own content-id line. `review-implementation.md` A5
  (`:429-431`) requires the value "as its own labelled line" but never names the label, so a
  correct run using another label would be recorded `FAILED`. The ledger clause already binds the
  content.
- `_postcondition_local_implementation_revise_recorded`: feedback role
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW`, `Status: REVISE`, with `Reviewed bundle ID` equal to a
  non-null `pre_state["bundle_manifest_bundle_id"]` and `Work item` matching.
- `_postcondition_manual_implementation_approve_recorded`: the ledger records both stages
  `APPROVE` against the manifest's `review_content_id`, and the manual stage's `bundle_id` equals
  the feedback's `Reviewed bundle ID`, recorded verbatim by contract.
- `_postcondition_manual_implementation_revise_recorded`: feedback role
  `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, `Status: REVISE`, and the ledger's manual stage still
  `None`.

**Rows** (seven new, `EXPECTED_OUTCOMES` 11 -> 18):

| # | from_phase | version | action | to_any_of | predicate (self-loop) | postconditions | writer_calls |
|---|---|---|---|---|---|---|---|
| 12 | `IMPLEMENTING` | `2.1` | `/milestone-implement` | `IMPLEMENTING`, `SELF_REVIEWING_IMPLEMENTATION`, `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | checkpoint completed durably | `{SELF_REVIEWING_IMPLEMENTATION}` -> self-review entered durably; `{AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW}` -> bundle coherent | `complete_checkpoint`, `milestone-implement.md`, step `1f` |
| 13 | `IMPLEMENTING` | `2.2` | same | same, with `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | same | same, local phase | same |
| 14 | `SELF_REVIEWING_IMPLEMENTATION` | `2.1` | same | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | -- | bundle coherent | `record_bundle_generation`, step `4` |
| 15 | `SELF_REVIEWING_IMPLEMENTATION` | `2.2` | same | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | -- | bundle coherent | same |
| 16 | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `2.2` | `/review-implementation` | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, `APPLYING_REVIEW_FEEDBACK`, `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | local BLOCK current | manual phase -> local APPROVE recorded; applying -> local REVISE recorded | `record_local_implementation_review`, bullet `BLOCK` (as plan row 3) |
| 17 | `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `2.2` | `/record-manual-implementation-review` | `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, `APPLYING_REVIEW_FEEDBACK` | -- | external -> manual APPROVE recorded; applying -> manual REVISE recorded | `record_manual_implementation_review`, step `7` |
| 18 | `APPLYING_REVIEW_FEEDBACK` | `2.2` | `/apply-implementation-review` | `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | -- | bundle regenerated | `record_bundle_generation`, step `7` |

No `"1"` row: the `"1"` branch of `/milestone-implement` writes no state, so a job could not be
verified, and it stays declined.

`_INCOMPLETE_EFFECT_PHASES` stays empty. Every "worker stopped early" case either leaves the phase
unchanged (`FAILED` on execute; `INTERRUPTED` on `LAUNCHED` resume when `HEAD` is also unchanged)
or moves `HEAD`/state without verifying (`FAILED`/`UnreconcilableJobError`), exactly as for the plan
stage.

**Property 5 against the frozen text.** Each new `WriterCall` must pass
`property_declaration_against_artifact_violations`, which scans for no *different*
`workflow_state.<fn>(` after the declared call inside the declared span. Step `1f` legitimately
calls `workflow_state.committed_checkpoint_status(` (a read) and
`workflow_state.release_checkpoint(` (the claim-record release) after `complete_checkpoint`.
`WriterCall` therefore gains `trailing_calls: tuple[tuple[str, str], ...]`, an explicit,
per-call allowlist of `(function, justification)`. The property skips only those named functions,
and only after the declared call. A negative test proves that an undeclared trailing write still
fails, and that a `trailing_calls` entry naming a function absent from the span is itself a
violation, so the allowlist cannot silently go stale. The same mechanism applies wherever else the
frozen text requires it; none is added speculatively. If the first-match `bullet` locator for
row 16's `BLOCK` finds a bullet in `review-implementation.md`'s advisory branch before A6,
`BranchSpec` gains an optional `within` parent-step label (`A6`) with its own negative test. The
property is never weakened to pass.

**Plan-stage row-3 fix.** `_predicate_row3_block_feedback_current` switches its input from
`bundle_id` (`current_bundle_id`, never written) to `bundle_manifest_bundle_id`, and requires both
sides to be non-null. Rows 3/3' (`"2.1"`/`"2.2"` `/review-plan`) update `predicate_inputs`. The
`tests/test_job_validation.py` fixtures that seed `current_bundle_id="b" * 64` instead seed a plan
manifest carrying that bundle id. A regression reproduces the real shape (`current_bundle_id:
null`, genuine `BLOCK` feedback bound to the manifest's bundle id) and asserts `FINISHED` (it is
`FAILED` at base); a mutant with the binding line removed asserts not satisfied.

**Files**: `controller/job.py`, `tests/test_job.py`, `tests/test_job_validation.py`,
`tests/test_resume.py`, `tests/fixtures.py`.

**Tests**, per row:
- verified with coherent artifacts;
- `FAILED` with `postcondition_not_satisfied` and a detail, for each postcondition clause (stale
  revision, wrong `reviewed_implementation_head`, `generation_head` behind `HEAD`, missing
  manifest, `REJECTED` marker, ledger bound to other content, feedback bound to another bundle,
  wrong role, uncommitted checkpoint completion, uncommitted `SELF_REVIEWING_IMPLEMENTATION`);
- rows 12/13 from `IMPLEMENTING` with every checkpoint already `COMPLETE`, where the worker stops
  after step 2's committed transition: `FINISHED` (the `NO_CHECKPOINT` partial run);
- row 16's local `APPROVE` with the content id under a label other than
  `Reviewed review content ID:`: `FINISHED`;
- predicate-false self-loops;
- the same on `_reconcile_completed`;
- on `_reconcile_launched`: moved state with a failed postcondition -> `UnreconcilableJobError`
  carrying `postcondition_detail`, and unchanged phase and `HEAD` -> `INTERRUPTED`.

Property tests:
- the table is clean;
- negative instantiations for per-phase postcondition overlap, a stray phase and `trailing_calls`
  misuse;
- `property_record_completeness_violations` is clean with the new field.
<!-- /CP2 -->

<!-- CP3 -->
### CP3 -- the general dispatch rule

1. **The plan-stage decision golden** is generated as CP1's first act and asserted here.
   - **Coverage.** The full evidence matrix the existing plan-stage tests use, for every
     plan-stage phase at every version at which a Workflow writer can reach it:
     - `PLANNING`, `AWAITING_PLAN_APPROVAL` and `AMENDING_PLAN` at `"1"`/`"2.1"`/`"2.2"`;
     - `REVISING_PLAN`, `AWAITING_LOCAL_PLAN_REVIEW` and `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`
       at `"2.1"`/`"2.2"`;
     - `AWAITING_EXTERNAL_PLAN_REVIEW` at `"1"`.

     The matrix includes the stale-bundle and `REJECTED` variants. For each case the golden holds
     `automatic`, `declined`, `action.command`, every `gate` field, `evidence` and `reason`.
   - **Normalisation.** Before serialising, one function shared by the generator and the test
     replaces:
     - the temporary target root, wherever it appears (`HumanGate.repository`, `artifact_path`,
       any text field), with `<ROOT>`;
     - every 40-hex-digit token with `<SHA>`. These are live `HEAD`/`generation_head`/base SHAs.
       They vary run to run, because `fixtures.commit_all` does not pin dates, and the
       admissibility gate text embeds them (`controller/evidence.py:407-412`).

     Fixture-constant ids (`"b" * 64` and similar) stay literal. The golden is canonical JSON
     (`sort_keys`, fixed indent), and the test requires the normalised re-derivation to be
     byte-equal.
   - **The single permitted difference** is `reason` on declined decisions (`AMENDING_PLAN`),
     because the uniform decline wording replaces "revision 10's scope". The test names that
     exception explicitly. The unreachable combinations (Design) are not in the golden; item 6
     pins their change.
2. `decision.AUTOMATIC_TRIPLES`, `classify_selected_action`, the uniform decline reason, the action
   normalisation (Design, "Action representation"), and the two-directional equality test against
   `job.EXPECTED_OUTCOMES`.
3. Handlers for `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION`:
   - select `/milestone-implement <id>`;
   - gate first when `plan_approval` is not `{"status": "CURRENT", ...}`. The `what_is_required`
     text is "`/milestone-implement`'s entry validation (step 1a) refuses on a missing or stale
     plan approval". The `safe_resume_command` is `workflow-controller explain --work-item <id>`,
     never a user-only command. Launching a worker that the command's own step 1a refuses is
     wasted spend.
4. `AWAITING_FUNCTIONAL_REVIEW`'s unconsumed-findings case now flows through
   `classify_selected_action` and is declined. `SELECTED_COMMANDS` gains `milestone-implement`,
   `review-implementation`, `apply-implementation-review` and `record-manual-implementation-review`.
   `DELIBERATELY_NOT_SELECTED_COMMANDS` keeps the functional-review commands,
   `bootstrap-workflow-v2` and `prepare-review`, with reasons rewritten to the rule. The
   `classify_command_files` partition test against the installed commands is updated to the new
   partition, and remains total.
5. Every per-phase set (`REPORT_ONLY_PHASES`, ...) is either deleted or reduced to a documented
   alias with no behavior. A test asserts that no module outside `decision.py` references them.
   **One interim set, removed by CP4/CP4B.** `_PHASES_AWAITING_EVIDENCE_HANDLER` holds
   `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`, `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` and
   `APPLYING_REVIEW_FEEDBACK`. `classify_selected_action` declines any action selected at those
   phases, so every one keeps its base, non-launching decision until the checkpoint that installs
   its gates. Without it, rows 16-18 (CP2) would make `/review-implementation` automatic at
   CP3's commit, with none of CP4's `REJECTED`/coherence/`BLOCK` gates in front of it. CP4 removes
   the first two phases, and CP4B removes the set, with a test that it no longer exists.
6. **The unreachable combinations.** A test enumerates the combinations "Decisions that change at
   combinations no Workflow writer reaches" names. It asserts each is declined with the uniform
   reason, and that `execute_step` at each returns a `DECLINED` record. At base each one raised
   `AssertionError`.
7. **Tests whose assertions change.** Each change is an intended behavior change, never a
   weakening. Each test is rewritten to assert the new behavior, and none is deleted without a
   replacement that asserts the same property under the rule.
   - `tests/test_decision.py`:
     - `ScopeAssertionTest` (`:143-185`). `REPORT_ONLY_EIGHT` and `AUTOMATIC_PHASES` assert
       `AWAITING_EXTERNAL_PLAN_REVIEW` automatic at the fixture default `"2.1"`
       (`tests/fixtures.py:253`), a combination that flips to declined. They become one
       per-`(phase, version)` expectation table, derived from `AUTOMATIC_TRIPLES` and asserted for
       every known phase at `"1"`, `"2.1"`, `"2.2"` and `None`.
     - `TwoShapeAssertionTest` (`:187-232`). The declined and gate sets are re-keyed by
       `(phase, version)`. The shape invariant is kept for every combination: a gate never carries
       an action, and a decline always does.
     - `ProtocolTwoTwoCompatibilityParityTest` (`:234-287`). Version-independent classification no
       longer holds, by design. Its two classification tests become "each `(phase, version)`
       classification equals `AUTOMATIC_TRIPLES` membership".
       `test_known_phases_unaffected_still_twenty_including_both_2_2_phases` is unchanged.
     - `CommandFilePartitionTest` (`:378-470`) and `_EXPECTED_SELECTED`/`_EXPECTED_NOT_SELECTED`
       (`:55-62`) move to the new partition.
     - `Revision64PhaseWideningTest.test_all_three_are_members_of_known_phases_and_report_only`
       (`:575-583`) keeps its `KNOWN_PHASES` half. Its `REPORT_ONLY_PHASES` half becomes:
       `AMENDING_PLAN` is declined by the rule (it has no row), and the two implementation-review
       phases are members of `_PHASES_AWAITING_EVIDENCE_HANDLER`. CP4 changes the second clause
       again.
   - `tests/test_job.py::test_declined_phase_writes_declined_no_worker_spawned` (`:189-209`) and
     `tests/test_cli.py::test_declined_phase_reports_the_declined_action` (`:301-311`) assert
     `DECLINED` at `"2.1"` `IMPLEMENTING` with `plan_approval=None`. That combination is now the
     plan-approval gate, so each test becomes a gate assertion. A second test per file re-pins
     `DECLINED` at `"1"` `IMPLEMENTING`, which has no row.
   - `tests/test_evidence.py::AwaitingFunctionalReviewTest.test_unconsumed_findings_are_automatic`
     (`:875-886`) becomes the declined assertion.
   - `tests/test_integration_disposable_repo.py` `Protocol22ImplementationReviewGatesTest`
     (`:548-552` calls `job.execute_step` without `claude_bin`). Under CP3 alone, state 1
     (`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`) is still declined, through the interim set. The test
     nevertheless gains `claude_bin` pointing at `tests/fake_claude.py`, with
     `FAKE_CLAUDE_REQUIRE_FILE` naming a path that never exists (fail-if-invoked) for every
     state. So from this checkpoint on, no ordinary unit run can ever reach the real `claude`
     binary through it. CP4 and CP4B rewrite its states.

**Files**: `controller/decision.py`, `controller/evidence.py`, `tests/test_decision.py`,
`tests/test_evidence.py`, `tests/test_job.py`, `tests/test_cli.py`,
`tests/test_integration_disposable_repo.py`.

**Tests**:
- the CP1 golden is unchanged;
- `AUTOMATIC_TRIPLES` equals the table keys in both directions, and its stems are a subset of
  `SELECTED_COMMANDS`;
- `"1"` `IMPLEMENTING` is declined with the uniform reason;
- `"2.1"`/`"2.2"` `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION` are automatic;
- a stale or absent plan approval gates;
- `execute_step` at `AWAITING_FUNCTIONAL_REVIEW` with unconsumed findings returns a `DECLINED`
  record, not an `AssertionError`. This is the regression, and it fails at base;
- item 6's unreachable-combination test;
- a synthetic triple removed from `AUTOMATIC_TRIPLES` via `mock.patch` turns the same decision into
  a decline. This proves the rule, not the handler, launches;
- the three interim phases are declined even though their triples exist.
<!-- /CP3 -->

<!-- CP4 -->
### CP4 -- implementation-review handlers

Ordering inside `evidence.decide` keeps today's "bundle-level facts first" rule:
1. the `REJECTED` marker, for every bundle-bearing phase;
2. the implementation-bundle gate, **at `"2.2"` only**, at the four phases of the new
   `IMPLEMENTATION_BUNDLE_CONSUMING_PHASES`: `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`,
   `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`, `APPLYING_REVIEW_FEEDBACK` and
   `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`. The first two exist only at `"2.2"`. At
   `"1"`/`"2.1"` `APPLYING_REVIEW_FEEDBACK` nothing is automatic, and the phase table promises the
   corrected gate there, so no bundle gate stands in front of it (round 2, O4).
   `"1"`/`"2.1"` `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` stays byte-identical (below);
3. the per-phase handler.

`AWAITING_LOCAL_IMPLEMENTATION_REVIEW` and `APPLYING_REVIEW_FEEDBACK` join `BUNDLE_BEARING_PHASES`.
Both `/review-implementation` (A3, A6) and `/apply-implementation-review` (step 1) call
`assert_bundle_not_rejected`, at every version.

- **The `REJECTED` gate's text, by version and phase** (round 2, I3). Today
  `_rejected_marker_gate` sends every bundle-bearing phase outside `PLAN_BUNDLE_CONSUMING_PHASES`
  to the bare `_regeneration_command` (`controller/evidence.py:1142-1171`).
  - **Why the bare command is wrong at `"2.2"`.** A marker survives only an in-progress or
    partially failed withdrawal (`withdraw_bundle`, `scripts/workflow_fingerprint.py:2388-2446`).
    After one, the author files may be stale or gone. Two of their lines are hard generator
    preconditions: `IMPLEMENTATION_SUMMARY.md`'s `implementation_revision:` and
    `REVIEW_REQUEST.md`'s `review_content_id:`. The previous milestone stopped advertising the
    bare generator at the plan stage for this reason, and this milestone makes the next action
    automatic at the `"2.2"` implementation phases.
  - **Four branches, evaluated in order:**
    1. **`"2.2"`, at a phase in `IMPLEMENTATION_BUNDLE_CONSUMING_PHASES`.** The text is the
       plan-stage branch's marker-clearing clause, verbatim ("resolve the failure the REJECTED
       marker at `<path>` records (`<detail>`); do not delete surviving author files unless that
       specific failure requires it -- a successful generation then clears the marker itself").
       Then come the ordered steps of `_implementation_bundle_recovery_steps`, including the
       generator `<stage>` derivation and the preflight clause. `what_is_required` reads "...;
       then, before any implementation review runs, perform in order: ...".
       `safe_resume_command` is the clause plus the steps, `; `-joined, assembled exactly as the
       plan-stage branch does it. The helper lands in this checkpoint, because this gate needs it
       first. CP4B specifies its steps (the bundle gate's third case) and reuses it there.
       At `APPLYING_REVIEW_FEEDBACK`, the bundle to restore is the one the feedback reviewed, so
       step 0 names the newest `current.rejected-*` quarantine as the source. If the regenerated
       `bundle_id` still differs from the feedback's `Reviewed bundle ID`, CP4B's admissibility
       clause gates on that mismatch next, and nothing launches.
    2. **A phase in `PLAN_BUNDLE_CONSUMING_PHASES`.** Today's plan-stage text, unchanged.
    3. **`"1"`/`"2.1"` `APPLYING_REVIEW_FEEDBACK`** (round 3, O4; round 4, O1). This phase is
       newly bundle-bearing, so no existing `REJECTED` text there has to stay byte-identical.
       - **How it is reached.** At `"1"`/`"2.1"`, `/apply-implementation-review`'s
         `enter_applying_review_feedback` call runs *before* its step 1. So a human who runs the
         command at `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` over a marker lands the item here
         with the marker still present.
       - **Why the command itself cannot be named.** `/apply-implementation-review` step 1 calls
         `assert_bundle_not_rejected` at every version
         (`.claude/commands/apply-implementation-review.md:93-96`). The marker is cleared only by
         a fresh, successful generation (`scripts/workflow_fingerprint.py:2138-2175`). So the
         command refuses until something else has happened.
       - **The text.** It is the same marker-clearing clause, then the facts. The bundle the
         feedback reviewed was withdrawn. `/apply-implementation-review` is legal from this
         phase, because it skips its own entry transition, but its step 1 refuses until a
         successful generation clears the marker. So the human restores the bundle from the
         newest `current.rejected-*` quarantine and regenerates it. They rerun
         `/apply-implementation-review` only if the regenerated `bundle_id` equals the
         feedback's `Reviewed bundle ID`, which step 1's `assert_feedback_matches_bundle`
         requires. Otherwise the verdict has to be obtained again, for the regenerated bundle.
       - **No generator command.** The bare generator is the advice the previous milestone and
         round 2 retired, because the author files may be stale. A regenerated bundle also
         cannot in general reproduce the reviewed `bundle_id`, because `CHANGED_FILES.txt`
         records worktree status. Nothing is automatic at `"1"`/`"2.1"`, so a human decides.
       - `safe_resume_command` is `workflow-controller explain --work-item <id>`. It names no
         command that `assert_bundle_not_rejected` refuses.
    4. **Every other bundle-bearing phase.** Today's bare text, byte-identical, because
       byte-identity binds exactly here. This covers every other plan-stage phase (including `"1"`
       `AWAITING_EXTERNAL_PLAN_REVIEW`) and `"1"`/`"2.1"`
       `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`.
       `test_rejected_marker_gate_elsewhere_is_unchanged` (`tests/test_evidence.py:1024-1047`)
       pins both, and stays unchanged.

- **The implementation-bundle gate, first form.** It calls `implementation_bundle_coherence` with
  `require_current_generation_head=True`, except at `APPLYING_REVIEW_FEEDBACK` (see CP1). In this
  checkpoint an incoherent bundle is a gate that names the failing clause and its detail, with
  `safe_resume_command` `workflow-controller explain --work-item <id>`. The existing
  `generation_head`-only provenance variant keeps today's text. CP4B replaces both texts with the
  clause-specific, generation-record-aware recovery. The gate never advertises the bare
  `_regeneration_command` alone, the rule the previous milestone established for the plan stage.
- **`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`** (`"2.2"`): a `LOCAL_MODEL_IMPLEMENTATION_REVIEW`
  `BLOCK` on file -> gate: "explicit user resolution is required before any further command runs"
  (`review-implementation.md` A7). This mirrors the plan-stage handler's any-local-BLOCK rule and is
  fail closed. Otherwise `/review-implementation <id>` is selected, and the rule makes it automatic.
- **`AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`** (`"2.2"`). Every sub-case except the last
  is a gate:
  - no feedback, or feedback that is not the manual role -> the genuine manual gate: "upload
    `<bundle_dir>` (bundle_id, review_content_id from the ledger) to a manual external reviewer
    and paste the verdict...". It names the values the user must hand over (the "At every stop"
    requirement of `IMPLEMENTATION_REVIEW_WORKFLOW.md`);
  - inadmissible (`evaluate_manual_implementation_stage_admissibility`) -> a gate naming every
    failing clause. A `REVISE` bound to another bundle says why it is not ingested: the
    `/apply-implementation-review` step-1 refusal it would lead to;
  - admissible `BLOCK` -> the user-resolution gate;
  - admissible `APPROVE`/`REVISE` -> `/record-manual-implementation-review <id>` is selected (and
    automatic by the rule). Advisories are carried into `evidence`.
- **`APPLYING_REVIEW_FEEDBACK`**, every version, in this checkpoint: the corrected gate. It says "an
  implementation-review remediation is in progress or was interrupted;
  `/apply-implementation-review` is legal from this phase (it skips its own entry transition), so
  rerun it once the feedback on file is confirmed current". The `safe_resume_command` is
  `/apply-implementation-review <id>`. This replaces the wrong "no Workflow command can legally run"
  text. It stays the `"1"`/`"2.1"` behavior. CP4B adds the `"2.2"` automatic path in front of it.
- **`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`**:
  - `"1"`/`"2.1"`: byte-identical to today, pinned by a golden in the CP3 style over the existing
    `tests/test_evidence.py` cases.
  - `"2.2"`: after the `REJECTED` and bundle gates, the handler reads the ledger, the feedback on
    file and `technical_review_block_pins`, in this order:
    1. A `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` `REVISE`/`BLOCK` on file that is bound to the
       current content -> gate: "a later manual verdict is on file; `/apply-implementation-review`
       is legal from this phase (it re-enters `APPLYING_REVIEW_FEEDBACK`); a human decides whether
       to reopen remediation". Never automatic.
    2. The user-only `/approve-review implementation` is named **only** when every clause of
       `technical_approval_gate_reachable` that Controller can see holds. The ledger records both
       stages `APPROVE` against the manifest's `review_content_id`. `REVIEW_FEEDBACK.md` is on file
       with `Status` `REVISE` or `APPROVE` (`approval_gate_reachable`,
       `scripts/workflow_state.py:10411-10417`; `/approve-review` step 1 reads the file,
       `.claude/commands/approve-review.md:108-135`). And no `technical_review_block_pins` entry
       names the manifest's `bundle_id` (`is_technical_review_block_pinned`). Gate text: "both
       implementation-review stages approved the current content; a human runs the user-only
       `/approve-review implementation`".
    3. Otherwise -> a gate naming what is missing: the incomplete or other-content ledger, the
       absent feedback, the `BLOCK` status, or the pin. The text says that
       `/approve-review implementation` would refuse, and why. The `safe_resume_command` is
       `workflow-controller explain --work-item <id>`.

    Clauses Controller cannot see, such as uncommitted protected-content edits, are the same
    known limit stated in "Scope judgments". `/approve-review`'s own recomputation refuses on them.
- `_PHASES_AWAITING_EVIDENCE_HANDLER` shrinks to `{APPLYING_REVIEW_FEEDBACK}`.
- `_capture_pre_state`'s bundle read already resolves the implementation-stage `<bundle_dir>` by
  phase; nothing to change there.

**Tests whose assertions change** (intended, never weakened):
- `tests/test_evidence.py`:
  - `AwaitingManualExternalImplementationReviewTest.test_verdict_on_file_names_record_manual_implementation_review_and_never_launches`
    and `test_revise_status_also_names_record_manual_implementation_review`. With admissible
    fixtures (coherent manifest, local `APPROVE` in the ledger) each becomes an automatic
    assertion. The original "names it, never launches" shape is re-pinned for an inadmissible
    verdict.
  - `AwaitingExternalImplementationReviewTest.test_2_2_item_reports_identically_to_2_1_at_this_reused_terminal_phase`
    becomes the three `"2.2"` sub-cases above. The `"2.1"` half of its comparison is kept by the
    golden.
- `tests/test_decision.py::Revision64PhaseWideningTest.test_awaiting_local_implementation_review_is_declined_naming_review_implementation`
  becomes an `evidence.decide` test: automatic at `"2.2"` with a coherent bundle, and the
  coherence gate without one.
- `tests/test_integration_disposable_repo.py` `Protocol22ImplementationReviewGatesTest`, rewritten
  so that its fixture carries a coherent implementation manifest (real `generation_head`,
  `worktree_root`, revisions) and a ledger where a state needs one:
  - **state 1** (`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`) is now automatic. It runs with a no-op
    fake worker and asserts one invocation, `automatic: true`, and `FAILED` (no transition). It
    never reaches the real binary;
  - **states 2 and 3** (no manual verdict; manual `BLOCK`) stay gates. Their text changes as
    specified above;
  - **state 4** as seeded (manual `APPROVE`, no ledger) becomes the inadmissible-verdict gate,
    naming the missing local-stage clause. A new **state 4'** seeds the local `APPROVE` ledger
    entry, so the same verdict is admissible and launches `/record-manual-implementation-review`
    under the no-op fake;
  - **state 5** as seeded (no ledger) becomes the ledger-incomplete gate, whose
    `safe_resume_command` is `workflow-controller explain --work-item <id>`, not
    `/approve-review implementation`. A new **state 5'** seeds a complete ledger plus an `APPROVE`
    feedback, and names `/approve-review implementation`;
  - every state not meant to launch runs with the fail-if-invoked fake
    (`FAKE_CLAUDE_REQUIRE_FILE`). The `_run` helper's blanket `assertFalse(automatic)` becomes a
    per-state expectation. Its "never `/approve-review`/`/accept-milestone` as an automatic
    action" assertion is kept for every state.

The same fixture-repair rule as before applies to every other existing test that asserts a
decision at an affected phase: it gets coherent fixtures, and no assertion is weakened.

**Files**: `controller/evidence.py`, `controller/decision.py`, `tests/test_evidence.py`,
`tests/test_decision.py`, `tests/test_job_validation.py`, `tests/fixtures.py`,
`tests/test_integration_disposable_repo.py`.

**Tests**:
- every sub-case above, one test each, including version splits;
- the advisory-only bundle-id mismatch still admits a manual `APPROVE`, and a mismatched manual
  `REVISE` gates;
- a local-role feedback at the manual phase is never admitted;
- `REJECTED` wins over staleness, including at `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` and
  `APPLYING_REVIEW_FEEDBACK`;
- the `REJECTED` gate's text (round 2, I3):
  - one test per `"2.2"` phase in `IMPLEMENTATION_BUNDLE_CONSUMING_PHASES`, four in all. Each
    asserts the marker-clearing clause and the ordered recovery steps in both `what_is_required`
    and `safe_resume_command`, and that `safe_resume_command` is never
    `_regeneration_command(...)` alone;
  - `"1"` and `"2.1"` `APPLYING_REVIEW_FEEDBACK` (branch 3, round 4 O1). `what_is_required`
    holds the marker-clearing clause, the statement that the reviewed bundle was withdrawn, that
    `/apply-implementation-review` refuses until a successful generation clears the marker, the
    restore from the newest `current.rejected-*` quarantine, and the rerun condition on the
    feedback's `Reviewed bundle ID`. `safe_resume_command` is exactly
    `workflow-controller explain --work-item <id>`, so it names no command that
    `assert_bundle_not_rejected` refuses. Neither field contains `_regeneration_command(...)`'s
    text or `prepare-ai-review.sh`;
  - `test_rejected_marker_gate_elsewhere_is_unchanged` (`tests/test_evidence.py:1024-1047`) stays
    unchanged. It is the byte-identical pin for `"1"` `AWAITING_EXTERNAL_PLAN_REVIEW` and `"2.1"`
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`;
  - `AwaitingManualExternalImplementationReviewTest.test_bundle_bearing_phase_reports_a_withdrawn_bundle_ahead_of_the_ordinary_row`
    (`:809`) keeps its assertions: the marker's detail in `what_is_required` and the marker's
    `artifact_path`. Both hold under the new `"2.2"` text. It is listed so the text change at
    this phase is not silent;
- `"1"`/`"2.1"` `APPLYING_REVIEW_FEEDBACK` with an absent or incoherent implementation bundle
  reaches the corrected gate, never a bundle gate (round 2, O4);
- the corrected `APPLYING_REVIEW_FEEDBACK` text no longer contains "no Workflow command can
  legally run" (regression);
- `"2.2"` `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`, the inverted revision-1 test (I3):
  - a complete ledger with an `APPROVE` feedback names `/approve-review implementation`;
  - the same ledger with the feedback absent, with `Status: BLOCK`, or with a
    `technical_review_block_pins` entry for the manifest's `bundle_id` does **not** name it, and
    each gate names its missing clause;
  - a half-empty ledger does not name it either;
- the golden over `"1"`/`"2.1"` `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is unchanged.
<!-- /CP4 -->

<!-- CP4B -->
### CP4B -- implementation-bundle recovery, row-18 remediation and its bounds

- **The implementation-bundle gate, final form.** It replaces CP4's generic text. Its inputs are
  `implementation_bundle_coherence`'s `clause` and CP1's `generation_record_view`, all read-only
  `git`/file facts. It never uses Controller job records to choose the generator stage. Evaluated
  in order:
  1. **Malformed T.** `head_role == "ordinary"` and `head_phase == parent_phase` -> the
     malformed-T gate ("The pending review-stage state write", item 2). This check comes first,
     because every other recovery ends in a generator run that the generator's preflight would
     refuse.
  2. **Provenance only.** The only failing clause is `generation_head`, and no generation-record
     commit for this work item lies in `generation_head..HEAD`. So `HEAD` moved only through
     non-record commits after T.
     - **When the recovery command applies** (round 2, O3). That is
       `/recover-implementation-provenance`'s use case only when those commits are excluded-only.
       If any of them changes protected content (for example, a human's code fix committed at
       `AWAITING_LOCAL_IMPLEMENTATION_REVIEW`), the command refuses with
       `ImplementationProvenanceRecoveryNotApplicableError`, because the content changed
       (`scripts/workflow_state.py:12178-12190`). Controller does not classify paths (Scope
       judgments), so the gate carries the facts and conditions its text on them.
     - **Evidence.** The gate lists each commit in `generation_head..HEAD`, oldest first, with its
       subject and its changed paths (`git log --reverse --name-only`).
     - **Text.** "if every listed commit changes only paths the implementation stage excludes
       (the `implementation_stage` excluded paths/prefixes of
       `workflow_fingerprint.artifacts_path_for_work_item(<id>)`), a human runs
       `/recover-implementation-provenance`. Otherwise the listed content is not what was
       reviewed, and that command refuses: revert those commits, or carry the change through a
       review round, before any implementation review runs". `safe_resume_command` carries the
       same condition.
     - The command stays user-only and never automatic. It is named only at a phase where it is
       legal (`bundle_generation_recovered_role_legal_committed_phases`). At
       `APPLYING_REVIEW_FEEDBACK` the clause is never evaluated.
     - `"1"`/`"2.1"` `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` keeps today's unconditional text,
       byte-identical (CP4).
  3. **Everything else** is the ordered recovery steps from `_implementation_bundle_recovery_steps`
     (landed in CP4 for the `REJECTED` gate), mirroring the plan-stage helper. This includes a
     `generation_head`-only failure whose
     `generation_head..HEAD` *does* contain a newer generation-record commit. That is the
     `same_content` post-fix whose generator failed: `record_bundle_generation(...,
     outcome="same_content")` leaves `implementation_revision`/`reviewed_implementation_head`
     unchanged (`scripts/workflow_state.py:11262-11322`), so only `generation_head` lags, and
     `HEAD` is the new T itself. `/recover-implementation-provenance` would refuse there, with
     `ImplementationProvenanceRecoveryNotApplicableError` "already the current ...
     commit -- nothing to recover" (`scripts/workflow_state.py:12182-12190`). The steps are:
     - (0, when `current/` or an author file is absent or zero bytes) write `CONTEXT_FILES.txt`,
       naming the newest `current.rejected-*` quarantine as the source to restore from;
     - write or refresh `IMPLEMENTATION_SUMMARY.md` so it states `implementation_revision: <state
       value>` (`assert_stage_completeness`);
     - write or refresh `REVIEW_REQUEST.md`'s `review_content_id:` from the canonical
       implementation-stage entry point `REVIEW_PROTOCOL.md` names
       (`workflow_state.approval_review_content_id(repo_root, stage="implementation",
       base_commit=<base>, head="HEAD", work_item_type=<type>, work_item_id=<id>,
       artifacts_path=workflow_fingerprint.artifacts_path_for_work_item(<id>))`);
     - write or refresh `TEST_RESULTS.md`;
     - run `scripts/prepare-ai-review.sh <base_commit> <stage> <id>`. `<stage>` is derived from
       Workflow's own durable fact, the committed phase of the parent of this work item's most
       recent generation-record commit: `SELF_REVIEWING_IMPLEMENTATION` means `implementation`,
       and any other phase means `post-fix`. `/milestone-implement`'s T always has a
       `SELF_REVIEWING_IMPLEMENTATION` parent, because step 1f's checkpoint commit or step 2's
       state-only commit records it. A post-fix T's parent never records it. With no
       generation-record commit found, or with a `generation_head` that is not an ancestor of
       `HEAD`, both forms are named, each with its producing command. The generator step carries
       the "if this refuses at preflight, its message names the upstream artifact to repair first"
       clause.

  Neither this gate nor the `"2.2"` `REJECTED` gate (CP4, branch 1) ever advertises the bare
  `_regeneration_command` alone. The `"1"`/`"2.1"` `REJECTED` text keeps today's bare form only
  where byte-identity binds (CP4, branch 4). At the newly bundle-bearing `"1"`/`"2.1"`
  `APPLYING_REVIEW_FEEDBACK` it names no generator at all (CP4, branch 3).
- **`APPLYING_REVIEW_FEEDBACK`, the `"2.2"` automatic path**, in front of CP4's corrected gate:
  1. the feedback on file must be admissible by `evaluate_apply_implementation_review_admissibility`
     (CP1: `REVISE` from one of the two `"2.2"` roles, bound to the manifest's `bundle_id`).
     Inadmissible or absent -> the corrected gate, naming the failing clauses;
  2. **the relaunch bound.** It keys on the bundle, not on job order (round 2, O1).
     - **Which job.** Let J be the most recent *terminal* `/apply-implementation-review` job this
       Controller launched for the work item from `APPLYING_REVIEW_FEEDBACK` (round 4, O3). A
       non-terminal record is never J. `step` refuses on it before deciding (CP5), and `explain`
       reports it as pending, ahead of its decision (CP5). Once `resume` reconciles it, it is
       terminal and can be J.
     - **When it applies.** J ended in a status other than `FINISHED`, and J's
       `pre_state.bundle_manifest_bundle_id` equals the current manifest's `bundle_id`. That
       equality is exactly "the same bundle J may already have edited at its step 4". Controller
       reads the manifest's own `bundle_id` line, which step 4 never changes; step 4 changes only
       the recomputed id. A null `bundle_manifest_bundle_id` on J counts as equal (fail closed).
       No launched J can carry one, because the bundle gate refuses a launch without a readable
       manifest `bundle_id`.
     - **When it lapses.** Exactly when a new generation replaces the manifest. Suppose a human
       completes the round and the next local review outside this Controller, and a later
       `REVISE` returns the item to `APPLYING_REVIEW_FEEDBACK` with J still the most recent apply
       job. The manifest is new, so the bound no longer applies. Revision 2 keyed on "the most
       recent job launched for the work item" alone, and would keep firing there until some other
       job launched.
     - **Result.** A gate, and nothing is relaunched automatically. The gate names J's id and its
       status. It explains that if that attempt passed its own step 4, `<bundle_dir>` no longer
     matches its manifest's `bundle_id`, so every retry would refuse at step 1. And it names the
     human's options: restore the edited bundle file from the bundle's archive
     (`review-bundle.tar.gz` beside `current/`) and rerun the command in a supervised session, or
     complete the round by hand. The `safe_resume_command` is
     `workflow-controller explain --work-item <id>`. "Launched" means the record reached
     `LAUNCHED`: it carries `expected_transition`, and a no-launch record never counts. Nor does
     a record whose `reconciliation_evidence.code` is `"WorkerNotStarted"` (CP5, round 3 O3): the
     Controller recorded that no worker process ever existed, so nothing touched the bundle. Such
     a record is skipped when choosing J, and an earlier attempt behind it still counts. The bound
     is deliberately conservative for an interruption before step 4, which costs one human
     action; the alternative is an unbounded series of doomed Opus/`xhigh` launches;
  3. otherwise `/apply-implementation-review <id>` is selected, and it is automatic by the rule.
     The `Action` carries the pending-write `task_addendum` exactly when `committed_work_item`'s
     phase at `HEAD` is not `APPLYING_REVIEW_FEEDBACK`.
- **Where the job history comes from.** `evidence` cannot import `job`. `evidence.decide` gains the
  keyword-only `last_apply_job: LaunchedJobView | None = None`, a small `evidence` dataclass of
  `job_id`, `command` token, `from_phase`, `status` and `pre_bundle_manifest_bundle_id`.
  `job.last_launched_apply_job_view(runtime, target_root, work_item_id)` builds it from the
  runtime's own job records: the most recent *terminal* record for the work item that reached
  `LAUNCHED` with the `/apply-implementation-review` token from `APPLYING_REVIEW_FEEDBACK`, or
  `None`. Both call sites pass it: `execute_step` (`controller/job.py:1875`) and `cmd_explain`
  (`controller/cli.py:302`). So both see the same J and make the same decision.
  - **`explain` and `step` agree while a job is pending too** (round 4, O3). Under CP5, `step`
    refuses with `PendingJobReconciliationError` *before* deciding, while `cmd_explain` only
    calls `evidence.decide` (`controller/cli.py:290-303`). Suppose the Controller is killed
    during an apply, for example by Ctrl-C, which orphans the worker. J is still `LAUNCHED`, and
    the manifest is unchanged. Counting that J, `explain` would report the relaunch-bound gate
    and advise a supervised rerun while the orphan is still editing the worktree. So J counts
    only terminal records, and CP5 makes `explain` report the pending job files first, with the
    clearing command the refusal names.
  - **It reads tolerantly** (round 3, O5). It never raises on a job file. It skips a file that
    does not parse as a JSON object, a record whose `target_repo` is not this target (including
    `--abandon`'s minimal record, whose `target_repo` is `null`), and a record missing a field it
    reads. Otherwise `explain` would fail in exactly the state the pending-reconciliation refusal
    reports, which is where the operator needs it. `step` is unaffected, because under CP5 the
    refusal stops it first.
  - It skips `WorkerNotStarted` records (above). No such record exists before CP5, so this
    checkpoint's test seeds one by hand.
- **Task assembly.** `decision.Action` gains `task_addendum: str | None = None` (additive; every
  existing construction is unchanged). `execute_step` launches `action.command` when it is `None`,
  and `f"{action.command}\n\n{action.task_addendum}"` otherwise. It records the addendum as
  `selected_action.task_addendum`. `_expected_outcome_for` keeps keying on
  `action.command.split()[0]`, and `worker._assert_not_user_only` still sees the whole task.
- `_PHASES_AWAITING_EVIDENCE_HANDLER` is deleted, with a test that it no longer exists.

**Files**: `controller/evidence.py`, `controller/decision.py`, `controller/job.py`,
`controller/cli.py`, `tests/test_evidence.py`, `tests/test_decision.py`, `tests/test_job.py`,
`tests/test_cli.py`, `tests/fixtures.py`, `tests/test_integration_disposable_repo.py`.

**Tests**:
- the recovery gate, per ordered case, against a real temporary git repository:
  - malformed ordinary T -> the malformed-T gate, never the regeneration steps;
  - an excluded-only commit after T -> `/recover-implementation-provenance`, not automatic;
  - a `same_content` post-fix T whose generator failed (`Workflow-Supersedes` trailer, revision
    unchanged, `HEAD` = T) -> the regeneration steps with `<stage>` `post-fix`, never
    `/recover-implementation-provenance` (I4's regression);
  - an ordinary post-fix T whose generator failed -> `post-fix`, and a final-pass T whose
    generator failed -> `implementation`, each derived from T's parent's committed phase;
  - no generation-record commit, and a non-ancestor `generation_head` -> both forms named;
  - `safe_resume_command` is never `_regeneration_command(...)` alone;
- `APPLYING_REVIEW_FEEDBACK` at `"2.2"`:
  - an admissible `REVISE` with the pending write -> automatic, and the addendum is present and
    formatted with the id and the committed phase;
  - the same with `HEAD` already recording `APPLYING_REVIEW_FEEDBACK` -> automatic with no
    addendum;
  - inadmissible (`APPROVE`, `BLOCK`, wrong role, bundle mismatch, absent) -> gates;
  - a previous `FAILED`, `INTERRUPTED` or `INCOMPLETE` apply job from this phase -> the
    relaunch-bound gate, with the fail-if-invoked fake proving nothing launches;
  - a previous `FINISHED` apply job, or a previous job at another phase -> no bound;
  - a previous `FAILED` apply job whose `pre_state.bundle_manifest_bundle_id` differs from the
    current manifest's -> no bound, and the apply launches (O1). This is the round completed
    outside this Controller: a human finished the apply and the next local review, and a later
    `REVISE` returned the item to `APPLYING_REVIEW_FEEDBACK` with that failed job still the most
    recent apply job. The same job with a matching id, or with a null one, is the bound;
  - a hand-seeded `FAILED` apply record with `reconciliation_evidence.code: "WorkerNotStarted"`
    and a matching `bundle_manifest_bundle_id` -> no bound, and the apply launches. The same
    record in front of an earlier real `FAILED` attempt at the same bundle -> the bound, naming
    the earlier job (round 3, O3);
  - `explain` and `step` report the same gate;
  - a non-terminal (`LAUNCHED`) apply record is never J: with nothing else on file, no bound
    applies; with an earlier terminal `FAILED` apply at the same bundle behind it, that earlier
    job is J (round 4, O3);
  - `explain` with an unparseable file and another target's record under `jobs/` still reports
    its decision, and `last_launched_apply_job_view` returns the right view or `None` without
    raising (round 3, O5);
- the addendum constant is pinned byte-for-byte, names no member of `worker.USER_ONLY_COMMANDS`,
  and passes `_assert_not_user_only` when formatted;
- `execute_step` launches the task with the addendum exactly when the decision carries one (the
  fake worker's diagnostic file records the task), and records it in `selected_action`;
- `Protocol22ImplementationReviewGatesTest` gains **state 6** (`"2.2"` `APPLYING_REVIEW_FEEDBACK`,
  admissible `REVISE`, pending write): automatic under the no-op fake, with the addendum in the
  recorded task. It also gains **state 7** (the same state after state 6's unverified job): the
  relaunch-bound gate under the fail-if-invoked fake.
<!-- /CP4B -->

<!-- CP5 -->
### CP5 -- worker lifecycle and concurrency

Implements "Concurrency and worker lifecycle" above:
- `controller/lock.py` (new, placed after `runtime` in `DEPENDENCY_ORDER`):
  - `acquire_lifecycle_lock(target_root) -> LifecycleLock`, a context manager exposing `.fd` and
    `.path`. Only `BlockingIOError` from `flock` is `LifecycleWorkerActiveError`. Every other
    `OSError` from the `os.open` or the `flock` is `LifecycleLockError`, naming the path, the
    operation and the errno's name (round 4, O4);
  - `probe_lifecycle_lock(target_root) -> "held" | "free" | "unknown"`, which reads
    `/proc/locks` by the mount device and inode of the git directory's own descriptor (`fdinfo`
    `mnt_id` -> `mountinfo` `MAJ:MIN`, never `st_dev`; `fdinfo` `ino:`, never `st_ino`; see
    Design) and never acquires anything. It answers `free` only from the init pid namespace
    (`/proc/self/ns/pid` is `pid:[4026531836]`), read through one patchable reader,
    `lock.read_pid_namespace()`. Anywhere else, no matching entry is `unknown` (round 4, I2);
- `worker.launch(..., pass_fds, on_spawn)`, with `on_spawn` invoked outside the `try` that
  converts `Popen`'s `OSError` (Design, round 4 O4);
- `WorkerProcess`, with `pid`, `pgid`, `start_ticks`, `boot_id`, `pid_namespace`, `hostname` and
  `machine_id`; `worker.read_process_context()`; and `worker.classify_worker_liveness(worker_process)`,
  the boot-keyed verdict (Design, round 3 I1). Rows 2 and 4 need the same host, meaning equal
  hostnames *and* equal `machine_id`s (round 4, O2). Its process test is zombie-aware (round 4,
  I1). The `/proc` form reads `stat` state and `pgrp`, and scans members when the leader is
  absent, is a zombie, or has no recorded `start_ticks`. The `killpg` form is used only where
  the `/proc` form is unavailable or has no answer, and its "live" is only `possibly live`,
  never `active`. The `/proc` root and `os.killpg` are reached through patchable seams, so the
  zombie and scan cases can also be tested with fixture `stat` files;
- `execute_step`'s lock scope, which covers the whole call, the `lifecycle_lock` and
  `worker_process` flushes, and the pending-reconciliation refusal;
- `execute_step`'s `WorkerNotStarted` disposition: a `UserOnlyCommandError` or
  `WorkerLaunchError` from `worker.launch` persists the record `FAILED` before it is re-raised
  (round 3, O3);
- `resume`'s lock (none when the target root no longer resolves), its liveness verdict with the
  `worker_active`/`worker_unverifiable` outcomes, and the terminal `FAILED` disposition for an
  unreconcilable record carrying `lifecycle_lock`;
- `workflow-controller resume --abandon JOB_ID [--acknowledge-unverifiable-worker]`, the operator
  disposition for every pending job file `resume` cannot reconcile and for an `unverifiable`
  record, with the `JOB_ID` constraint (Design). `--abandon` is named in the three messages the
  Design lists, except for a newer generation's record, where they name that generation;
- the write-containment scan's one read-only `os.open` acceptance. `lock.py` opens the git
  directory with `os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)`, because Python's
  `open()` cannot open a directory. The package-wide scan
  (`tests/test_write_containment.py:91-105`, `:161`) flags every `open` whose mode argument is not
  a string literal, so it would fail on that call. The scan gains exactly one acceptance: an
  `os.open` whose flags argument is `os.O_RDONLY` alone, or a `|`-combination of `os.O_RDONLY`
  with only `os.O_DIRECTORY` and `os.O_CLOEXEC`, is a read (round 3 accepted the lone form). An
  `os.open` with `O_WRONLY`, `O_RDWR`, `O_CREAT`, `O_TRUNC`,
  `O_APPEND` or a non-literal flags name is still flagged. The `/proc` reads use `open(path)`,
  which the scan already treats as a read. `--abandon`'s writes go through `runtime.write_json`/
  `runtime.write_bytes`, which the scan already exempts;
- the `errors.LifecycleWorkerActiveError`/`LifecycleLockError`/`PendingJobReconciliationError`
  classes. `LifecycleLockError` is a sibling of `LifecycleWorkerActiveError`, never a subclass, so
  the exit-45 clause cannot catch it;
- `job.pending_reconciliation_jobs(...)`, the one read-only scan behind the refusal and behind
  `explain`'s pending report (round 4, O3). `explain` prints the pending job files, and for each
  the clearing command the refusal names, *ahead of* its decision, with a line stating that
  `step`/`run` refuse until they are cleared. `explain --json` gains `pending_jobs: [{job_id,
  status, clearing_command}]`, which is empty when nothing is pending. `explain`'s exit code is
  unchanged;
- the Ctrl-C line (Design): a `KeyboardInterrupt` reaching `execute_step` after the
  `worker_process` flush writes one stderr line naming the worker's pid and pgid and
  `workflow-controller resume <repo>`, and is re-raised unchanged;
- `cli.EXIT_WORKER_ACTIVE = 45`. `cli.main` catches `LifecycleWorkerActiveError` in its own
  `except` clause *before* the blanket `ControllerError` -> 20 handler (`controller/cli.py:635-637`),
  and `cmd_resume` maps a `worker_active` outcome to 45;
- the exit-45 message: the lock path, `fuser -v <git-dir>`/`lsof +d`, the recorded worker
  pid/pgid only when a `LAUNCHED` record carries one whose verdict is `active`, and the "any other
  holder inherited the descriptor" sentence. For any other verdict it says why the recorded group
  is not named;
- `explain`/`inspect` report "lifecycle lock: held/free/unknown" via the probe;
- `DEFAULT_WORKER_TIMEOUT = None`.

**Files**: `controller/lock.py`, `controller/worker.py`, `controller/job.py`, `controller/cli.py`,
`controller/errors.py`, `controller/__init__.py` (`__all__` order), `tests/fake_claude.py` (a
`FAKE_CLAUDE_HANG_UNTIL_FILE` mode that exits when a file appears, so tests end workers
deterministically), `tests/test_lock.py` (new), `tests/test_worker.py`, `tests/test_job.py`,
`tests/test_job_validation.py`, `tests/test_resume.py`, `tests/test_cli.py`,
`tests/test_package_structure.py` (its `DEPENDENCY_ORDER` list, `:29`, gains `lock` after
`runtime`), `tests/test_write_containment.py`, `tests/process_fixtures.py` (new: the
subreaper zombie helper and the `unshare` probe runner, round 4).

**Tests whose assertions change** (intended, never weakened). Round 2 (I2) named the first two.
A sweep of every test file for the rest of this checkpoint's effects found the others.
- **The two end-to-end interruption tests** in `tests/test_resume.py`:
  - `EndToEndInterruptionTest.test_a_worker_killed_mid_run_leaves_a_launched_record_that_resume_reconciles`
    (`:954`);
  - `BootstrapEndToEndInterruptionTest.test_a_bootstrap_worker_killed_mid_run_leaves_a_null_work_item_id_record_that_resume_reconciles`
    (`:1006`).

  Each runs a real `execute_step` in a child process with `FAKE_CLAUDE_HANG=1`. That is a 3600 s
  sleep, in the worker's own session (`controller/worker.py:302`). Each then `SIGKILL`s the
  Controller and immediately asserts that `resume` reconciles the record `INTERRUPTED`. Under
  this checkpoint the record carries `lifecycle_lock` and `worker_process`, and the orphaned fake
  worker still holds the inherited lock. So `resume` must refuse: that is the behavior this
  checkpoint introduces. Each test takes the orphan case's shape:
  1. the child runs with `FAKE_CLAUDE_HANG_UNTIL_FILE`, naming a release file under the test's
     temporary directory, instead of `FAKE_CLAUDE_HANG`;
  2. **the kill trigger** (round 3, O2). Today each test sends `SIGKILL` as soon as *any* job file
     appears (`tests/test_resume.py:982-990`, and the bootstrap twin). That is the `PLANNED`
     flush, which under the new shape races the spawn. A kill before `Popen` leaves no worker, so
     `resume` reconciles at once instead of refusing. A kill between `Popen` and the `on_spawn`
     flush leaves no `worker_process`, so nothing records the group to wait on or kill. So the
     test polls (bounded) until the on-disk record carries `worker_process`, and only then sends
     `SIGKILL`;
  3. after the `SIGKILL`, `job.resume` raises `LifecycleWorkerActiveError`. The record on disk
     stays `LAUNCHED`, with both fields populated. The bootstrap variant keeps its null
     `work_item_id`, which is the point of its row-7 fixture;
  4. the test creates the release file, then waits (bounded) until the recorded
     `worker_process` group has no running member and the lock is free;
  5. `resume` then reconciles the record `INTERRUPTED`, as today;
  6. the test finally asserts that `os.killpg(pgid, 0)` raises `ProcessLookupError`, so no fake
     worker outlived it.

  An `addCleanup` is registered before the child starts. When it runs, it creates the release
  file, re-reads every job file under the test's runtime, and kills each recorded
  `worker_process` group (`SIGKILL`, ignoring `ProcessLookupError`). Because it reads the records
  at cleanup time, not at registration, it also covers a failure before `worker_process` was
  flushed. So no orphan survives even a failed assertion. Today both tests leak theirs. Every
  suite run leaves two `tests/fake_claude.py` sleepers alive for up to an hour. At least ten were
  alive on the development machine while round 2 was applied, and round 3's reviewer counted 7
  before one run and 9 after it. The same trigger and cleanup apply to the new orphan-case and
  unreconcilable-orphan tests below, and CP9 checks that the suite leaves none behind.
- **The exact write sequence.** The `on_spawn` flush of `worker_process` is a second `LAUNCHED`
  write. It must come after `Popen`, and the first `LAUNCHED` write must come before it, so that a
  crash between the two leaves a `LAUNCHED` record carrying `lifecycle_lock`. Three tests pin the
  sequence exactly, and each gains that second `LAUNCHED` entry:
  - `tests/test_job.py` `LaunchPathTest.test_write_sequence_prefix_is_planned_launched_completed`
    (`:287`) now asserts the prefix `["PLANNED", "LAUNCHED", "LAUNCHED", "COMPLETED"]`;
  - `tests/test_job_validation.py`
    `TransitionVerificationTest.test_successful_worker_reaches_finished_with_exact_status_sequence`
    (`:525`, assert `:538`) and
    `TwoPointTwoPlanReviewTransitionTest.test_row1_planning_reaches_awaiting_local_plan_review`
    (`:697`, assert `:706`) now assert `["PLANNED", "LAUNCHED", "LAUNCHED", "COMPLETED",
    "FINISHED"]`.

  A new test asserts that the second `LAUNCHED` write differs from the first only by
  `worker_process` and `updated_at`.
- **The exact `COMPLETED` key set.** `tests/test_job.py`
  `LaunchPathTest.test_completed_record_contains_every_schema_field_cp6_owns` (`:336`,
  `expected_keys` at `:348-353`) gains `lifecycle_lock` and `worker_process` here. CP6 adds
  `worker_route`.
- **Unchanged, by design:**
  - `tests/test_resume.py` `Case2UnresolvableSubjectTest.test_launched_record_naming_a_deleted_target_raises_and_aborts`
    (`:286`) and `...test_terminal_record_with_deleted_target_is_surfaced_unreadable_never_raises`
    (`:308`). Both delete the target and then call `resume`. They pass unchanged because
    `resume` takes no lock on a root that no longer resolves (Design); a lock taken first would
    raise before `validate_record` ran.
  - `test_planned_flush_carries_and_omits_the_right_fields` (`:296`) and
    `test_launched_flush_adds_only_expected_transition` (`:318`) read the *first* `PLANNED`/
    `LAUNCHED` write. `lifecycle_lock` joins at `PLANNED`, and neither test asserts its absence.
    The first test's absent-field list gains `worker_process`, which strengthens it.
  - Tests that call `execute_step`/`resume` twice in a row on one target, for example
    `tests/test_job.py:657`, `tests/test_resume.py:656` and
    `tests/test_integration_disposable_repo.py` `Protocol22ImplementationReviewGatesTest`. Each
    earlier record is terminal, and the lock is released when the call returns, so they are
    unaffected.

**Tests**:
- A second `acquire` on the same worktree fails while the first is held, from another process
  too.
- A subprocess that inherited the descriptor keeps it held after the parent exits. A background
  grandchild that inherited it keeps it held after both the child and the parent are gone, and
  the exit-45 message then names the recorded worker *and* says another holder exists.
- `probe_lifecycle_lock` on the repository's own filesystem (I1). The target is a temporary git
  repository created under the checkout's gitignored `build/` directory, not under `/tmp`. On the
  development machine `/tmp` is tmpfs, where `st_dev` equals the mount device and the defect
  cannot show; the checkout is on btrfs. The probe reports `held` while another process holds the
  lock, and `free` otherwise. Run outside the init pid namespace, as in some CI containers, the
  test expects `unknown` in place of `free`. Where `st_dev` differs from the mount device there, the test also
  asserts that the matched `/proc/locks` entry carries the mount device, not `st_dev`: the round-2
  reproduction, kept as a regression. The probe never takes the lock: a concurrent `acquire` made
  while the probe runs, in a tight loop, never fails because of the probe.
- `probe_lifecycle_lock` with patched `/proc` reads:
  - a holder entry at the mount device and the inode, with a different `st_dev` -> `held`;
  - an entry at `st_dev` and the inode only -> `free` (that entry is not this file);
  - only a `->` waiter line for the file -> `free`;
  - an unreadable `/proc/locks`, an `fdinfo` without `mnt_id:`, a mount id absent from
    `mountinfo`, or a line that does not parse -> `unknown`, never `free`;
  - an `fdinfo` with `mnt_id:` but no `ino:`, and a `/proc/locks` with no matching entry ->
    `unknown`, never `free`, even where an entry at `st_ino` would match (round 3, O1);
  - with `lock.read_pid_namespace()` patched (round 4, I2):
    - to a non-init namespace (`pid:[4026532862]`), with no matching entry -> `unknown`, never
      `free`;
    - to the same namespace, with a matching holder entry -> `held`;
    - to an unreadable link, with no matching entry -> `unknown`. With a matching holder entry
      it is still `held`, because an entry that is shown is a real holder.
- `probe_lifecycle_lock` in a real child pid namespace (round 4, I2). This runs only where
  `unshare -Urpf --mount-proc true` succeeds, that is, where unprivileged user namespaces are
  available, and is skipped otherwise, naming why. Inside `unshare -Urpf --mount-proc`, a
  process takes the lock on a git directory under `build/`, forks a child that inherits the
  descriptor, and exits. The namespace's PID 1 reaps it, and then:
  - the probe answers `unknown`, never `free`;
  - an `acquire` there raises `LifecycleWorkerActiveError`;
  - before the taker dies, the same probe answers `held`.
- Lock errors other than contention (round 4, O4). With `fcntl.flock` patched to raise
  `OSError(ENOLCK)`, and again `OSError(EBADF)`, `step`, `resume` and `--abandon` each exit 20 with
  `LifecycleLockError`. The message names the errno, and none of them contains exit 45's
  "worker active" text. With `os.open` patched to raise `PermissionError`, the result is the same.
  No `OSError` escapes `cli.main`. `LifecycleLockError` is not a subclass of
  `LifecycleWorkerActiveError`.
- An in-process `execute_step` with a hanging fake worker: while it is running (run in a thread),
  a second `execute_step` raises `LifecycleWorkerActiveError`, `cli.main` maps it to 45 (not 20),
  and the fake worker's invocation counter stays at 1.
- **The orphan case:**
  1. a child process runs `execute_step` and is `SIGKILL`ed while its fake worker hangs, once the
     record on disk carries `worker_process` (the kill trigger above);
  2. `step` exits 45;
  3. `resume` exits 45 and the record stays `LAUNCHED` with `lifecycle_lock` and
     `worker_process` populated;
  4. after the worker is released, `resume` reconciles it (`INTERRUPTED`: phase and `HEAD`
     unchanged);
  5. the next `step` launches exactly one worker.
- **The unreconcilable orphan (I2).** As above, but before exiting, the released fake worker
  commits a state change that moves the phase and fails the row's postcondition: a T with no
  regenerated bundle.
  - `resume` exits 20 with `UnreconcilableJobError`, and the record on disk is now `FAILED`, with
    `reconciliation_evidence.code == "UnreconcilableJobError"` and a `postcondition_detail`;
  - a second `resume` passes it as terminal history;
  - the next `step` does not refuse with `PendingJobReconciliationError`: it reaches the
    evidence-based decision (CP4B's regeneration gate) and launches nothing.
- **A record written before this milestone** (no `lifecycle_lock`) that is unreconcilable: `resume`
  raises and leaves it `LAUNCHED`, as today. `step` then refuses with
  `PendingJobReconciliationError`. `resume --abandon <job-id>` marks it `FAILED`
  (`OperatorAbandoned`), and the next `step` proceeds. `--abandon` refuses a terminal record, a
  record for another target, and a record whose verdict is `active`, with or without
  `--acknowledge-unverifiable-worker`.
- **`--abandon` beyond pre-milestone records** (O2):
  - A `LAUNCHED` record whose work item has vanished (`validate_record` case 2). `step` refuses
    with `PendingJobReconciliationError`, naming `resume --abandon <job-id>`. `resume` raises
    `StaleJobRecordError`, naming the same command. `--abandon` marks the record `FAILED`, with
    `validity` recorded. The next `resume` reports it as terminal history, and the next `step`
    proceeds.
  - A record with a status outside the enumeration: the same sequence.
  - An unparseable file under `jobs/`:
    - `step` refuses, naming `--abandon <file stem>`;
    - `--abandon`, run from any target, copies the original bytes unchanged to
      `jobs/abandoned/` and replaces the file with the minimal terminal record;
    - the next `resume` and `step` for every target proceed.
  - A record whose `controller_generation` is newer is refused, naming that generation, and is
    left untouched. `step`'s `PendingJobReconciliationError` and `resume`'s `StaleJobRecordError`
    for it name that generation, not `--abandon` (round 3, O5).
  - **The `JOB_ID` constraint** (round 3, O5). Each is refused before anything is read or written,
    naming the constraint and the pending stems, and the runtime is byte-identical afterwards:
    `../identity`, `../handoff`, `abandoned/<stem>`, an absolute path, `.`, `..`, a stem with no
    file under `jobs/`, and a `jobs/<stem>.json` that is a symlink to the runtime root's
    `identity.json`.
- Liveness, the process test (round 1, O3): a recorded pid whose `/proc/<pid>/stat` shows a
  *different* `start_ticks` reads inactive even though `os.killpg` would succeed. A leader that has
  exited while a running group member survives reads active. A null `start_ticks` falls to the
  member scan where `/proc` is available (a running member -> `active`), and to the `killpg` form
  where it is not (`possibly live` -> `unverifiable`, never `active`). Each is exercised with real
  processes, and the reused-pid case also with patched `/proc` reads.
- **Liveness, zombies** (round 4, I1). This development machine's `systemd` reaps orphans, so a
  test cannot wait for an orphan to become a zombie. So each case uses a helper process that
  makes itself a child subreaper (`prctl(PR_SET_CHILD_SUBREAPER)` through `ctypes`) and never
  reaps. The helper writes the group's pid, pgid and `start_ticks` to a file, then waits for a
  release file. Cleanup ends the helper, so its zombies are reparented and reaped. The two
  groups:
  - **an unreaped zombie leader**: the helper spawns a `setsid` leader that exits;
  - **a group whose only survivor is an unreaped zombie member**: the leader forks a member and
    exits, the helper reaps the leader, and then the member exits.

  For each, the test first asserts the premise: `stat` shows `Z`, and `os.killpg(pgid, 0)`
  succeeds. Then, with a `LAUNCHED` record carrying `lifecycle_lock` and a `worker_process`
  built from those numbers in the current context:
  - `classify_worker_liveness` is `inactive`;
  - `resume` reconciles the record (`INTERRUPTED`) and does not exit 45;
  - a copy of the record, before `resume`, is accepted by `--abandon` without
    `--acknowledge-unverifiable-worker`.

  Patched-`/proc` cases cover the rest: a leader in state `X` reads not running, and a zombie
  leader with a running member reads `active`. With the `/proc` form unavailable, a zombie group
  is `possibly live`, which gives `unverifiable`, never `active`. A `/proc` whose `self` link does
  not name the Controller's own pid makes the `/proc` form unavailable. An unreadable `/proc`
  listing, or an unparseable `stat` line, falls to the `killpg` form.
- **Liveness, the boot-keyed verdict** (round 3, I1). Each case runs with
  `worker.read_process_context` patched. Wherever a pgid is involved, the recorded pgid is a real
  process group that is live in this boot (a test-owned sleeper, cleaned up), so every case where
  the verdict must not consult it proves that it does not:
  - **another boot of the same host** (`boot_id` differs; hostname and `machine_id` equal):
    `classify_worker_liveness` is `inactive`. `resume` reconciles the `LAUNCHED` record (`INTERRUPTED`: phase and `HEAD`
    unchanged) and does not exit 45. The same record, before `resume`, is accepted by `--abandon`
    with no flag. This is the reboot scenario: neither command leaves it wedged;
  - **an unreadable `boot_id`**, recorded `null` in one case and unreadable now in another. The
    process test runs: a dead recorded group is `inactive` and `resume` reconciles it. A live one
    is `unverifiable`: `resume` exits 45 with `worker_unverifiable` and a message naming no pgid.
    `--abandon` refuses without the flag and marks it `FAILED` with it;
  - **another host** (`boot_id` and hostname both differ): `unverifiable`. `resume` exits 45 with
    `worker_unverifiable`, and the message names the recorded and current hostname,
    `machine_id` and `boot_id`, names `--acknowledge-unverifiable-worker`, and contains no pgid. `--abandon` without the flag
    refuses, naming the flag. With it, the record is `FAILED` (`OperatorAbandoned`), with
    `worker_liveness.verdict == "unverifiable"` and both contexts, and the next `step` proceeds;
  - **another boot with an equal hostname but a different `machine_id`**, and again with a
    `machine_id` recorded `null`, and again unreadable now: `unverifiable`, never `inactive`
    (round 4, O2). `--abandon` refuses each without the flag;
  - **no boot identity with an equal hostname but a different `machine_id`**: `unverifiable`,
    without running the process test;
  - **the same boot with a different hostname** and the same namespace: the process test decides
    (live -> `active`, dead -> `inactive`). A different `machine_id` changes nothing here: row 1
    does not read it;
  - **the same boot with a different or unreadable `pid_namespace`**: `unverifiable`;
  - **no kill advice across boots:** with a changed `boot_id` and the lock held by another
    process, the lock-acquisition exit-45 message from `step`, `resume` and `--abandon` does not
    contain the recorded pgid. With the same context and a live group (`active`), it does;
  - `--acknowledge-unverifiable-worker` without `--abandon` is a usage error (exit 2).
- **A worker that never started** (round 3, O3):
  - `step --claude-bin <nonexistent path>` raises `WorkerLaunchError` with today's exit code. The
    record on disk is `FAILED` with `reconciliation_evidence.code == "WorkerNotStarted"`, and no
    `worker_process`;
  - the next `step` (with a working fake) does not refuse with `PendingJobReconciliationError`,
    and launches exactly one worker;
  - the same for `UserOnlyCommandError`, raised by `worker.launch` for a user-only task that a
    patched decision supplies.
- A `SIGSTOP`ped worker counts as active; after `SIGCONT` and exit, the lock is released.
- Hand-built `LAUNCHED` records without `worker_process` or `lifecycle_lock` reconcile exactly as
  today, and the existing tests that seed them are unchanged. Records written by a real
  `execute_step` from this checkpoint on carry both fields: see "Tests whose assertions change"
  above.
- A target root deleted before `resume`: no lock is taken, and `validate_record` case 2 decides
  as today (the two unchanged `Case2UnresolvableSubjectTest` tests). A root that exists but is
  no longer a git repository -> exit 20, with nothing reconciled.
- The write-containment scan's read-only `os.open` acceptance: `lock.py`'s call passes, and so
  does a lone `os.O_RDONLY`. Negative instantiations stay flagged: `O_WRONLY`, `O_RDWR`,
  `O_CREAT`, and a flags argument held in a variable.
- A non-terminal record with a free lock -> `PendingJobReconciliationError` (exit 20) and no
  launch.
- A `PLANNED` record left behind by the base-version `/apply-functional-review` crash: `step`
  refuses, and `resume` reconciles it to `INTERRUPTED` (row 1). The next `step` then declines
  (CP3).
- The `on_spawn` flush failure kills the group (no surviving process) before raising. It is not
  recorded `WorkerNotStarted` (round 4, O4). With the flush patched to raise an `OSError`, the
  error that propagates is that `OSError`, not `WorkerLaunchError`. The record on disk stays
  `LAUNCHED`, with `lifecycle_lock` and no `worker_process`. The next `step` refuses with
  `PendingJobReconciliationError`, and `resume` reconciles the record (`INTERRUPTED`).
- **`explain` while a job is pending** (round 4, O3). This is a `"2.2"` item at
  `APPLYING_REVIEW_FEEDBACK` with an admissible `REVISE`, and a `LAUNCHED` apply record (J)
  whose `bundle_manifest_bundle_id` equals the manifest's:
  - `explain` reports the pending job, naming `workflow-controller resume <repo>`, ahead of its
    decision. It does not report the relaunch-bound gate;
  - `explain --json`'s `pending_jobs` names J and that command;
  - `step` refuses with `PendingJobReconciliationError`, naming the same job and command;
  - after `resume` reconciles J to `INTERRUPTED`, both `explain` and `step` report the
    relaunch-bound gate, and `pending_jobs` is empty.
- The pending-reconciliation scan ignores `jobs/abandoned/` (round 4, O4). A file under it that
  does not parse neither makes `step` refuse nor appears in `explain`'s `pending_jobs`, and
  `--abandon abandoned/<stem>` is refused by the `JOB_ID` constraint.
- An empty `JOB_ID` is refused by the `JOB_ID` constraint before anything is read, like the
  other malformed names.
- **Ctrl-C** (Design). `worker.launch` is patched to invoke `on_spawn` with a real test-owned
  sleeper's `WorkerProcess` and then raise `KeyboardInterrupt`. Then:
  - the `KeyboardInterrupt` propagates out of `execute_step` unchanged;
  - stderr names the sleeper's pid and pgid and `workflow-controller resume`;
  - the record stays `LAUNCHED` with `worker_process`;
  - the sleeper is still running until the test's cleanup ends it.
- `DEFAULT_WORKER_TIMEOUT is None`, and a worker that is silent for longer than any former default
  is still waited for. The test uses a short sleep with a patched former constant, asserting that
  no kill path runs.
- Prose over state: a `SUCCESS` worker whose `result` claims the transition while state is
  unchanged -> `FAILED`. An AST test pins that no decision/verification code reads
  `WorkerResult.result`/`raw_json`.
- Exit-code table: `EXIT_WORKER_ACTIVE == 45`, distinct from every other code. An AST test pins
  that the `LifecycleWorkerActiveError` clause precedes the `ControllerError` clause in
  `cli.main`. `LifecycleLockError` reaches the blanket clause (exit 20).
<!-- /CP5 -->

<!-- CP6 -->
### CP6 -- role-based routing

Implements "Worker routing" above:
- `controller/routing.py`;
- the CLI global options `--model`, `--effort`, `--role-model ROLE=MODEL` (repeatable),
  `--role-effort ROLE=EFFORT` (repeatable) and `--routing-config PATH`. The config file is JSON,
  `{"schema_version": 1, "default": {"model"?, "effort"?}, "roles": {ROLE: {"model"?,
  "effort"?}}}`. In the config file, unknown roles or keys, a wrong `schema_version`, or
  unparseable JSON -> `RoutingConfigError` (exit 20) before any launch. On the command line, an
  unknown `ROLE` or a value without `=` in `--role-model`/`--role-effort` is rejected by the
  argparse `type=` validator, a usage error (exit 2);
- `job.execute_step(..., routing: RoutingOptions)`, which derives the role from `(phase, command,
  registry_complete)`, resolves it, records `worker_route`, and passes
  `model`/`effort`/`disallowed_tools` to `worker.launch`.

**Files**: `controller/routing.py`, `controller/worker.py`, `controller/job.py`, `controller/cli.py`,
`controller/__init__.py`, `tests/test_routing.py` (new), `tests/test_worker.py`, `tests/test_cli.py`,
`tests/test_job.py`, `tests/test_package_structure.py` (its `DEPENDENCY_ORDER` list gains `routing`
between `evidence` and `worker`).

**Test whose assertion changes** (intended): `tests/test_job.py`
`LaunchPathTest.test_completed_record_contains_every_schema_field_cp6_owns` (`:336`) gains
`worker_route` in `expected_keys`, next to CP5's `lifecycle_lock` and `worker_process`.

**Tests**:
- Each of the five named roles resolves to `claude-opus-5-5`/`xhigh` by default.
- The self-review role is single-agent.
- The three inherit roles pass neither flag.
- Each precedence level beats the one below it, per field independently.
- `single_agent` cannot be overridden.
- Argv through `tests/fake_claude.py`'s diagnostic file shows `--model`/`--effort` and, for review
  roles only, `--disallowedTools` naming `SUBAGENT_TOOLS`.
- No argv ever contains a session-reuse flag.
- The job record's `worker_route.sources` matches the winning level.
- An invalid config refuses (exit 20) before a `PLANNED` record is written. An unknown role in
  `--role-model`/`--role-effort` exits 2, and writes nothing.
<!-- /CP6 -->

<!-- CP7 -->
### CP7 -- end-to-end lifecycle regression

`tests/fake_claude.py` gains `FAKE_CLAUDE_SCRIPT`, the path of a JSON file mapping an exact task
string to an ordered list of per-invocation action lists. A counter file beside it records
invocations. The actions are:
- `write {path, text}` (`{HEAD}` in `text` is substituted with the target's live `HEAD`);
- `commit {paths, message}`;
- `delete {path}`.

The worker behaves like a real Workflow command's observable effect, including its commit
ordering: record commit, then manifest with `generation_head: {HEAD}`. So each scenario runs
through the real `cli.main`/`execute_step`, the real verification and the real next decision.

`tests/test_lifecycle_orchestration.py` (new) covers these scenarios, each on a real temporary git
target:

1. **`run` from `IMPLEMENTING`, 2 checkpoints, `"2.2"`**:
   - CP-a -> `IMPLEMENTING`;
   - CP-b -> `SELF_REVIEWING_IMPLEMENTATION`;
   - the final pass -> `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` (coherent manifest);
   - `/review-implementation` `REVISE` -> `APPLYING_REVIEW_FEEDBACK`, with the state write left
     uncommitted, as the real command leaves it;
   - `/apply-implementation-review` -> `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` (new generation).
     Its task carries the pending-write addendum (the script is keyed on that exact task string).
     The scripted worker follows it: first the state-only commit, then a fix commit, then T, then
     the manifest. The test asserts that T's parent records `APPLYING_REVIEW_FEEDBACK`;
   - `/review-implementation` `APPROVE` -> `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`;
   - stop at the manual gate: exit 10, `GATE_BLOCKED`, and exactly six workers launched, whose
     job records show the expected roles, models and efforts;
   - a second `run` launches nothing (the counter is unchanged).
2. **Manual verdict**: the test writes an admissible manual `APPROVE`. `run` launches
   `/record-manual-implementation-review` once and stops at the `/approve-review implementation`
   gate (exit 10). No worker ever receives a user-only command. The same scenario with a manual
   `REVISE` loops through apply and a fresh local review. With a manual `BLOCK` nothing launches.
3. **Fail-closed artifacts**. For each automated command, the worker publishes the phase but leaves
   the bundle stale, missing (withdrawn `current/` plus quarantine), `REJECTED`, bound to the wrong
   `review_content_id`, or leaves a checkpoint `COMPLETE` uncommitted. Each yields:
   - `FAILED` with the postcondition detail;
   - the next `step` gates and launches nothing. For a `REJECTED` bundle the gate is CP4's
     `"2.2"` `REJECTED` branch: the marker-clearing clause plus the ordered recovery steps, never
     the bare generator. For the other cases it is CP4B's bundle gate, in its ordered case;
   - `resume` of the same job as `COMPLETED` -> `FAILED`, and as `LAUNCHED` ->
     `UnreconcilableJobError`.

   Positive controls: coherent artifacts -> `FINISHED`. A durable pre-fix demonstration per row
   (`mock.patch.dict` over `_EXPECTED_OUTCOMES_BY_KEY` removing that row's postconditions) verifies
   `FINISHED`, proving the postcondition is what fails closed.
4. **Resume/reconciliation**: `LAUNCHED`/`COMPLETED` records for each new row, reconciled against
   coherent and incoherent targets, plus the orphan/active cases from CP5 at an
   implementation-stage phase. This includes the I2 case: the Controller dies during an
   `/apply-implementation-review`, and the orphaned worker commits T while its generator fails.
   `resume` persists `FAILED` and exits 20, and the next `step` reaches CP4B's regeneration gate
   with `<stage>` `post-fix`, launching nothing.
5. **Plan stage unchanged**: the previous milestone's end-to-end plan-stage scenarios (existing
   `tests/test_job.py` stale-bundle class) run unmodified. The CP1 golden stays green.
6. **Manual/user gates launch nothing**: across every gate phase and sub-case, a fake worker
   configured to fail if invoked (`FAKE_CLAUDE_REQUIRE_FILE` naming a path that never exists)
   never runs.
7. **A literal apply worker** (I1). The scripted `/apply-implementation-review` worker ignores the
   addendum and follows the frozen text literally: a fix commit, then T staging only
   `WORKFLOW_STATE.json`, then no manifest, because the real generator's preflight would refuse.
   - The job is `FAILED`, with the "bundle regenerated" postcondition detail.
   - The next `step` reports the malformed-T gate, naming T and `OPUS-R101-001`, never the
     regeneration steps, and launches nothing.
   - The same scenario with the pending write already committed by the "human" before the run
     gets no addendum and verifies `FINISHED`.
8. **The apply relaunch bound** (I5). The scripted apply worker rewrites
   `<bundle_dir>/IMPLEMENTATION_SUMMARY.md` (its step 4) and then exits without transitioning,
   so the job is `FAILED`. The next `run` stops at the relaunch-bound gate (exit 10), and the
   fail-if-invoked fake proves no second worker ran.
9. **A `same_content` post-fix whose generator failed** (I4). The scripted worker lands a
   recovered-role T (`Workflow-Supersedes`, revision unchanged) and no manifest. The job is
   `FAILED`. The next `step` names the regeneration steps with `post-fix`, and never names
   `/recover-implementation-provenance`.
10. **The `NO_CHECKPOINT` partial run** (O4). From `IMPLEMENTING` with every checkpoint already
    `COMPLETE`, the scripted worker makes only step 2's state-only `SELF_REVIEWING_IMPLEMENTATION`
    commit and stops. The job is `FINISHED`, and the next `run` launches the final pass.

**Files**: `tests/fake_claude.py`, `tests/fixtures.py`, `tests/test_lifecycle_orchestration.py`.
<!-- /CP7 -->

<!-- CP8 -->
### CP8 -- documentation

- `README.md`:
  - the CLI table (`run` now reaches implementation-stage gates);
  - the global options (`--model`, `--effort`, `--role-model`, `--role-effort`,
    `--routing-config`; `--timeout` now defaults to no limit);
  - the routing table and precedence;
  - the concurrency paragraph (worktree lock, exit 45 and what it names, `resume` while a worker
    lives);
  - the job dispositions: `resume` persists `FAILED` for an unreconcilable record written by this
    version, and `resume --abandon JOB_ID` is the operator disposition for every pending job file
    `resume` cannot reconcile (one written before this version, one failing `validate_record`,
    an unknown status, an unparseable file) and what it refuses. `JOB_ID` is a bare stem of a
    file directly under `jobs/`. A launch that never started a worker is `FAILED`
    (`WorkerNotStarted`) at once and needs no `resume`;
  - the liveness verdict (round 3, I1): a reboot, crash or power loss during a worker needs only
    `resume`. A host is its hostname *and* `/etc/machine-id` together, so a host with no readable
    machine id, or two machines sharing a hostname, fall to `unverifiable` (round 4, O2). A
    record from another host or pid namespace, or one with no boot identity whose recorded group
    may be live, is `unverifiable`: `resume` exits 45 without naming a process group,
    and `resume --abandon JOB_ID --acknowledge-unverifiable-worker` is the operator's statement
    that the worker is gone. The flag never overrides a running worker observed in this boot, or
    a held lock. A worker that exited but was never reaped (a zombie, for example under a
    container PID 1 that does not reap) does not count as running (round 4, I1). This is the
    residual case the documentation states, with its manual disposition;
  - `explain`/`inspect`'s `lifecycle lock: held/free/unknown`, and that `unknown` means the probe
    could not prove the lock free, never that it is free. It could not establish the lock's
    device or inode, or it runs outside the init pid namespace. In a container, `/proc/locks`
    omits a lock whose taker has died, even while an orphaned worker still holds it, so there
    `unknown` is the usual answer when nobody visibly holds the lock (round 4, I2). `step`
    decides by acquiring, never by the probe;
  - `explain` reports pending job files, with each one's clearing command, ahead of its decision
    (round 4, O3);
  - a lock failure other than contention exits 20 with `LifecycleLockError`, naming the errno. A
    worktree on a filesystem whose `flock` emulation refuses an exclusive lock on a read-only
    directory descriptor (NFS without `local_lock=flock|all`) cannot be driven (round 4, O4);
  - Ctrl-C ends only the Controller: the worker runs in its own session and keeps running,
    headless, holding the worktree. The Controller's stderr line names its pid and pgid. So do
    the job record's `worker_process` under `<runtime>/jobs/`, and the exit-45 message of a later
    `step` or `resume` while that worker is `active`. End the group or wait, then run `resume`
    (round 4, usability note);
  - the apply-round behavior: the row-18 pending-write addendum, the malformed-T gate, and the
    relaunch bound;
  - the safety model ("selects only from its own four-command selected set" becomes the general
    rule plus the eight-command selected set; user-only commands unchanged).
- `docs/adr/0001-controller-generation-1-architecture.md`:
  - the exit-code table gains 45;
  - code 15's meaning becomes "no verifiable expected outcome is declared for the selected
    action";
  - a short "Automatic-dispatch rule" subsection;
  - the `resume` disposition rule.
- **`tests/test_plan_document_consistency.py`'s exit-code half.** It binds the ADR's "Exit codes"
  table *bidirectionally* to the completed `docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`'s table
  (`:60`, `:224-234`, `:372-374`), so adding 45 and rewording 15 would fail it. Decision:
  **rewire it to a live source, and scope the historical comparison.** The completed Gen-1 plan
  document is not edited: it is a closed milestone's record, and a living ADR must not be bound
  bidirectionally to a frozen historical one.
  - The live-document assertion becomes: the ADR table's code set equals the values of `cli`'s
    `EXIT_*` constants, in both directions. Every constant has exactly one row, and every row a
    constant.
  - The Gen-1 plan comparison becomes one-directional: every Gen-1-plan row is still in the ADR.
    It has exactly one named exception, code 15's meaning, reworded by this work item. The test
    names the code and this work item's id. A new ADR-only row (45) is allowed by construction.
  - `exit_code_violations` and its existing negative/positive instantiations are unchanged: they
    test the bidirectional comparison function itself on synthetic text. The new helper gets its
    own negative instantiations: a missing constant row, an extra row, a dropped Gen-1 row, and a
    changed Gen-1 meaning outside the named exception.
- `tests/test_checklist_corrections.py`: only if the README/ADR text it pins changes, in which
  case it is updated, not weakened.

**Files**: `README.md`, `docs/adr/0001-controller-generation-1-architecture.md`,
`tests/test_plan_document_consistency.py`, and `tests/test_checklist_corrections.py` if needed.
<!-- /CP8 -->

<!-- CP9 -->
### CP9 -- full verification

In this repository's `unittest` idiom (no `pytest`):

1. `python3 -m unittest discover -s tests -t .` -- zero failures, including the two
   live-state-dependent baseline tests, now passing (see "Baseline test state"). The set of live
   `tests/fake_claude.py` processes (`pgrep -f tests/fake_claude.py`) is recorded before and after
   the run, and the run adds none (round 3, O2).
2. The frozen Workflow conformance suites that are green at base, each as
   `python3 scripts/<name>_test.py`:
   - `workflow_acceptance_matrix_test.py`;
   - `workflow_fingerprint_generalization_test.py`;
   - `workflow_fingerprint_test.py`;
   - `workflow_integration_test.py`;
   - `workflow_state_completion_obligations_test.py`;
   - `workflow_state_test.py`;
   - `workflow_test_harness_test.py`.

   `workflow_fingerprint_demo_test.py` and `workflow_state_demo_test.py` are excluded, as in the
   previous milestones, because they fail at base against Workflow-repository-only artifacts. They
   are named here, not silently dropped. The list is re-derived at implementation time from what is
   green at base.
3. **Live implementation-lifecycle run** (`CONTROLLER_LIVE_WORKER=1`, real `claude`; spend is
   expected).
   - **Setup.** In a disposable managed repository (`workflow-manager bootstrap`, release from
     `VALIDATED_WORKFLOW_RELEASES`, `"2.2"` activated), seed a trivial one-checkpoint work item at
     `IMPLEMENTING` with a `CURRENT` plan approval. The seed is produced **by frozen Workflow's own
     writer functions** (`route_work_item`, `write_registry_and_mapping`, `publish_plan_revision`,
     the two plan-review stage writers, `apply_plan_approval` with a fixture confirmation literal)
     in that throwaway repository only, never by hand-written JSON. It is test setup for a
     disposable fixture, not a Controller capability.
   - **Phase A.** `workflow-controller run` with no `--model`/`--effort`/`--timeout`. It must stop
     at `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` (exit 10). If the local review returned
     `REVISE` along the way, Phase A has already run one real apply round.
   - **Phase B, a real apply round, always run.** Acting as the human in this disposable
     repository, the test writes an admissible manual `REVISE` verdict naming one small, concrete
     finding in the checkpoint's own code (fixture text), bound to the current bundle and content.
     A second `run` must then:
     - ingest it through `/record-manual-implementation-review`, which leaves the `REVISE` state
       write uncommitted;
     - launch the real `/apply-implementation-review` with the pending-write addendum;
     - land a well-formed T whose parent records `APPLYING_REVIEW_FEEDBACK`, and a regenerated
       bundle;
     - launch a fresh `/review-implementation`, stopping at the manual gate again or at a local
       `BLOCK`.
   - **Required outcome.** Every job must be `FINISHED` with `transition_verified: true`, empty
     `permission_denials`, and a `worker_route` naming `claude-opus-5-5`/`xhigh` for the five
     named roles. Every review job's `worker_route.single_agent` must be `true`.
   - **Recorded evidence.** `TEST_RESULTS.md` records:
     - each job's session id, route, pre/post phase, wall-clock and cost, and the worker's
       reported model (`modelUsage` in the JSON result);
     - the apply round's commit sequence (state-only commit, fixes, T), with T's parent's
       committed phase.
   - **Stop conditions**, which halt CP9 for investigation and are not waived by (1)/(2) being green:
     - a `FAILED` job;
     - a malformed generation-record commit, or a generator preflight refusal;
     - Phase A stopping at a local `BLOCK`, which leaves Phase B unreachable and so gives no live
       apply evidence;
     - a non-empty `permission_denials`;
     - a model other than the routed one;
     - any worker launched at the manual gate.
4. **Live single-agent probe**: workers launched through `worker.launch` with a review route, one
   per delegation path. Each task explicitly asks the worker to delegate through:
   - the `Agent` tool;
   - the `Workflow` tool;
   - the `Skill` tool, invoking a skill that runs in a forked subagent (a fixture skill in the
     disposable repository).

   Each result must show the path unavailable or denied. This confirms the `SUBAGENT_TOOLS` names
   against the installed CLI, and whether `Skill` belongs in the set (see "Worker routing"). A
   probe that *can* spawn a subagent by any path is a CP6 defect.
5. **Live concurrency drill**:
   1. start `workflow-controller step` on the disposable repository at a phase whose worker takes
      real time;
   2. `SIGKILL` the Controller process;
   3. confirm that `step` and `resume` exit 45 while the worker lives. Record exactly what
      `fuser -v <git-dir>` names: the worker's own process, and any descendant of the installed
      `claude` binary (for example, a Bash-tool child) that inherited the descriptor. Whether such
      descendants inherit it is unverified; this drill settles it. Record also whether the
      exit-45 message told the recorded worker apart from other holders, and confirm that
      `explain` reports `lifecycle lock: held`. The disposable repository sits on the same
      filesystem as this checkout (btrfs on the development machine), so this also checks the
      probe's device source against a real worker (I1 of round 2);
   4. let the worker finish, including any holder named in 3;
   5. confirm that `explain` now reports `lifecycle lock: free`, that `resume` reconciles the
      record from its real outcome, and that the next `step` proceeds.

   The drill runs in the init pid namespace, on a host whose `systemd` reaps orphans, so it
   cannot show round 4's two container cases. CP5's subreaper zombie tests and its `unshare`
   probe test cover them, and `TEST_RESULTS.md` records whether the `unshare` test ran or was
   skipped.

   Commands and outputs are recorded in `TEST_RESULTS.md`.

Zero failures across (1) and (2) is the exit condition, and (3)-(5) must each meet their stated
stop conditions.

**Files**: none (verification only), apart from the live-test additions in
`tests/test_integration_disposable_repo.py` that (3)-(5) run through, which stay opt-in behind
`CONTROLLER_LIVE_WORKER=1`.
<!-- /CP9 -->

## Open decisions (`docs/TECHNICAL_DECISIONS.md`)

`docs/TECHNICAL_DECISIONS.md` does not exist in this repository (nor do
`docs/DOMAIN_GLOSSARY.md`/`docs/UX_FLOWS.md`), so there is nothing to finalize. The choices a
reviewer should confirm explicitly are listed under "Scope judgments" above.

## Review-round decisions

**`LOCAL_MODEL_PLAN_REVIEW` round 1 (`REVISE`, plan revision 1 -> 2).** Every finding was
reproduced against the repository before it was applied. None is rejected.

| Finding | Disposition |
|---|---|
| I1 | Accepted. The Investigation claim is corrected (the frozen `/apply-implementation-review` text does not commit the pending write). New Design section "The pending review-stage state write": the row-18 task addendum, the malformed-T gate (CP4B), a CP7 literal-worker scenario, and CP9 Phase B's live apply round. The addendum is a scope judgment the reviewer may cut. |
| I2 | Accepted. A terminal `FAILED` disposition for an unreconcilable record carrying `lifecycle_lock`, and `resume --abandon` for pre-milestone records (Design, CP5, CP8, Migration). |
| I3 | Accepted. `/approve-review implementation` is named only when the ledger is complete, the feedback on file is `REVISE`/`APPROVE`, and no pin names the bundle. The CP4 test is inverted accordingly. |
| I4 | Accepted. The generation-record-aware recovery order (CP4B), with the generator `<stage>` derived from T's parent's committed phase instead of job records. A `same_content` failure test is added. |
| I5 | Accepted. The rationales are corrected (CP1). The apply `Status` clause narrows to `REVISE`, and the relaunch bound is added (CP4B). |
| I6 | Accepted. The unreachable-combination behavior change is stated (Design) and R10 is amended to match. Every test whose assertion changes is named (CP3, CP4). The action and golden normalisations are specified, and the golden moves to CP1's first act. |
| I7 | Accepted. Fail-if-invoked fakes from CP3 on. The state 1/4/5 expectation changes, plus the new states 4', 5', 6 and 7, are named (CP4, CP4B). |
| I8 | Accepted. Role-name errors are scoped (CLI exit 2, config exit 20). The exit-45 clause precedes the blanket handler. The error is renamed `PendingJobReconciliationError`. The doc-consistency test is rewired to `cli`'s constants, with a scoped Gen-1 comparison (CP8). |
| O1 | Accepted. The exit-45 message names the recorded worker and other holders; CP9's drill records `fuser`. |
| O2 | Accepted. The probe reads `/proc/locks` and never acquires. |
| O3 | Accepted. The liveness check uses `start_ticks`. |
| O4 | Accepted. The rows 12/13 `SELF_REVIEWING_IMPLEMENTATION` postcondition uses committed registry completion and the committed phase. |
| O5 | Accepted. `worktree_root` and `REVIEW_REQUEST.md` clauses are added, and a mismatched manual `REVISE` is a gate. |
| O6 | Accepted. The feedback content-id label clause is dropped from the local `APPROVE` postcondition. |
| O7 | Accepted. CP9's probe covers `Agent`, `Workflow` and a forked `Skill`. |
| O8 | Accepted. All four wording corrections are applied (Investigation, Design, Migration). |

Found while applying, and fixed in the same revision: `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` joins
`BUNDLE_BEARING_PHASES` next to `APPLYING_REVIEW_FEEDBACK`, because `/review-implementation` also
calls `assert_bundle_not_rejected` (A3, A6). CP3's interim `_PHASES_AWAITING_EVIDENCE_HANDLER`
keeps rows 16-18 from launching before the checkpoint that installs their gates.

**`LOCAL_MODEL_PLAN_REVIEW` round 2 (`REVISE`, plan revision 2 -> 3).** Every finding was
reproduced against the repository before it was applied, and none is rejected. The six questions
the round answered ("accept" on each) need no plan change.

| Finding | Disposition |
|---|---|
| I1 | Accepted, and reproduced on this repository's btrfs `/home`: `st_dev` `00:34`, while `/proc/locks` and `findmnt` give `0x1d`/`0:29`. The probe takes its device from `/proc/self/fdinfo/<fd>`'s `mnt_id` -> `/proc/self/mountinfo` `MAJ:MIN`, and its inode from `fdinfo`'s `ino:`. It answers `unknown`, never `free`, when either cannot be established (Design, CP5). CP5 tests it on the checkout's own filesystem (not `/tmp`, which is tmpfs there) and with patched `/proc` reads. CP9's drill checks `explain` against a real worker. |
| I2 | Accepted. Both `tests/test_resume.py` interruption tests are named in CP5, with their new shape: `resume` refuses while the orphan lives, the orphan is released through `FAKE_CLAUDE_HANG_UNTIL_FILE`, then `resume` reconciles `INTERRUPTED`. An `addCleanup` kills the recorded group, so the leak ends. At least ten leaked `fake_claude.py` sleepers were alive while this round was applied. |
| I3 | Accepted. CP4 specifies the `REJECTED` gate by version and phase. At `"2.2"` implementation-bundle phases it is the marker-clearing clause plus `_implementation_bundle_recovery_steps`, which moves to CP4. The plan-consuming pair is unchanged, and every other phase, including `"1"`/`"2.1"`, keeps today's bare text. CP4B's sentence is scoped to match. `test_rejected_marker_gate_elsewhere_is_unchanged` is named as the unchanged pin, with one new test per `"2.2"` phase. CP7 scenario 3 states the text. |
| O1 | Accepted. The relaunch bound keys on the failed job's `pre_state.bundle_manifest_bundle_id` equalling the current manifest's, with a test for a round completed outside this Controller (CP4B, Scope judgments, Migration). |
| O2 | Accepted. `resume --abandon` covers every pending job file `resume` cannot reconcile, and the three messages name it. The pending-reconciliation refusal's treatment of an unparseable file is stated (Design, CP5, CP8, Migration). |
| O3 | Accepted. The `"2.2"` provenance-only gate lists the intervening commits and their paths as evidence, and names `/recover-implementation-provenance` only on the condition that they are excluded-only. `"1"`/`"2.1"` stays byte-identical (CP4B). |
| O4 | Accepted. The implementation-bundle gate runs at `"2.2"` only (CP4), matching the phase table's `"1"`/`"2.1"` corrected gate at `APPLYING_REVIEW_FEEDBACK`. |

Found while applying, by sweeping every test file for CP5's other effects, and fixed in the same
revision (CP5 and CP6 name each one):
- `resume` takes no lock on a target root that no longer resolves. Otherwise the two
  `Case2UnresolvableSubjectTest` deleted-target tests would fail before `validate_record` ran.
- `tests/test_write_containment.py`'s package-wide scan flags `lock.py`'s `os.open(...,
  os.O_RDONLY)`, so it gains one narrowly specified read-only acceptance, with negative
  instantiations.
- The `on_spawn` flush is a second `LAUNCHED` write. Three exact status-sequence assertions, and
  the exact `COMPLETED` key set (CP5, then CP6), are named.
- `tests/test_package_structure.py`'s `DEPENDENCY_ORDER` gains `lock` (CP5) and `routing` (CP6).

**`LOCAL_MODEL_PLAN_REVIEW` round 3 (`REVISE`, plan revision 3 -> 4).** Every finding was
checked against the repository and the plan's own text before it was applied, and none is
rejected. The round's answers to the three unresolved questions are applied as given: accept,
with O5's `JOB_ID` constraint; O4; and accept, with the lone `os.O_RDONLY` form.

| Finding | Disposition |
|---|---|
| I1 | Accepted, first option. Confirmed from the plan's own text: the row-1 "cannot decide" rule plus `--abandon`'s "still reads active" refusal left a record from a previous boot abandonable never. `/proc/sys/kernel/random/boot_id` is readable here and changes every boot. The liveness verdict is now keyed on `boot_id` first. The same boot runs the process test when the pid namespace matches. Another boot of the same host is `inactive`, so `resume` reconciles after a reboot. Another host, another namespace, or no boot identity with a live recorded group is `unverifiable`: `resume` exits 45 and names no pgid, and `--abandon --acknowledge-unverifiable-worker` disposes of it. The process test is reachable where `boot_id` is `null`. The exit-45 message names a pgid only for `active`. `worker_process` gains `pid_namespace`: the reviewer's "same boot, whatever the hostname" rule would otherwise trust pid numbers from another container on the same kernel. The "never permanent" claims are restated with the full list of refusals (Design, Scope judgments, CP5, CP8, Migration). CP5 tests a changed `boot_id`, a changed hostname, an unreadable `boot_id` and a changed namespace, against a live recorded pgid, so each case proves the old number is not consulted. |
| O1 | Accepted. The inode comes from `fdinfo`'s `ino:` only. A missing `ino:` answers `unknown`, with a patched-`/proc` test (Design, CP5). |
| O2 | Accepted. Confirmed at `tests/test_resume.py:982-990`: the kill fires on the first job file. The rewritten tests, and the new orphan-case and unreconcilable-orphan tests, kill only after `worker_process` is on disk. The `addCleanup` is registered before the child starts and re-reads the records when it runs. CP9 checks that the suite leaves no `fake_claude.py` process behind (CP5, CP9). |
| O3 | Accepted, first option. Confirmed at `controller/job.py:1934-1946` and `controller/worker.py:276`, `:290-310`. `execute_step` persists `FAILED` (`WorkerNotStarted`) before re-raising `WorkerLaunchError`/`UserOnlyCommandError`, with today's exit code. Found while applying: CP4B's relaunch bound counts every record that reached `LAUNCHED`, so without a matching exclusion a mistyped `--claude-bin` would have gated the next apply. The bound now skips `WorkerNotStarted` records (Design, CP4B, CP5, Migration). |
| O4 | Accepted. `"1"`/`"2.1"` `APPLYING_REVIEW_FEEDBACK` gets its own `REJECTED` branch: the marker-clearing clause plus the corrected gate's text, and no generator command. The bare form stays only where byte-identity binds (CP4 branches 3 and 4, CP4B). |
| O5 | Accepted, all four parts. Confirmed that `controller/runtime.py:98-106`, `:120-128` contain writes to the runtime root, not to `jobs/`. `JOB_ID` must be a bare stem naming a regular, non-symlink file directly under `jobs/`, from the same scan `resume` makes. `--abandon` calls `validate_record` for evidence only. A newer generation's record is named as that generation's, not as `--abandon`'s. `last_launched_apply_job_view` reads tolerantly, so `explain` works while a file is pending (Design, CP4B, CP5). |

**`LOCAL_MODEL_PLAN_REVIEW` round 4 (`REVISE`, plan revision 4 -> 5).** Every finding was
reproduced or checked against the repository before it was applied, and none is rejected. The
round's answers to the three unresolved questions ("accept" on each) need no plan change. Its
answers to the five challenge areas point back to I1, O2 and O4, which are applied below.

| Finding | Disposition |
|---|---|
| I1 | Accepted. Reproduced here. A `setsid` leader that exits unreaped reads `Z` in `/proc/<pid>/stat`, with its original `start_ticks`, and `os.kill(pid, 0)` and `os.killpg(pgid, 0)` both succeed. Inside `unshare -Urpf --mount-proc`, a group whose leader was reaped and whose only survivor is a zombie member still passes `killpg`. The process test is now zombie-aware. The `/proc` form counts only running processes, meaning a state other than `Z`/`X`. It scans members by `pgrp` when the leader is absent, is a zombie, or has no `start_ticks`. It is used whenever `/proc` numbers the Controller's own namespace. The `killpg` form is used only where the `/proc` form is unavailable or has no answer, and its "live" is `possibly live`, which is `unverifiable`, never `active`. So `active` means a running member was observed, and every `active` refusal ends when that process exits or is ended. The "transient" claims are restated to match (Design, Scope judgments, CP5, CP8, Migration). CP5 tests an unreaped zombie leader and a zombie-only group, using a subreaper helper, because this host's `systemd` reaps orphans. Each reads `inactive`, `resume` reconciles it, and `--abandon` accepts it without the flag. |
| I2 | Accepted. Reproduced here with `unshare -Urpf --mount-proc`: once the taker dies with a child still holding the lock, the `/proc/locks` entry disappears in the child namespace, while an acquire is still refused. The init namespace keeps showing it, with the dead pid. The probe now answers `free` only when `/proc/self/ns/pid` is `pid:[4026531836]` (`PROC_PID_INIT_INO`). Outside the init namespace, "no match" is `unknown`, and a shown entry is still `held`. The premise is stated in the Design and in CP8's README text. CP5 tests a patched namespace reading, and a live `unshare` variant where unprivileged user namespaces exist, skipped otherwise. |
| O1 | Accepted as suggested. Confirmed: `/apply-implementation-review` step 1 calls `assert_bundle_not_rejected` at every version (`.claude/commands/apply-implementation-review.md:93-96`). At `"1"`/`"2.1"`, its entry transition runs before step 1, which is how the state arises. Branch 3 keeps "no generator command". It states the withdrawal, the refusal until a successful generation clears the marker, the restore from the newest `current.rejected-*` quarantine, and the rerun condition on the feedback's `Reviewed bundle ID`. Its `safe_resume_command` is `workflow-controller explain --work-item <id>` (CP4, and CP4's test). |
| O2 | Accepted, the cheap option, plus the alternative's statement. `worker_process` and `read_process_context()` gain `machine_id` (`/etc/machine-id`, readable here). Rows 2 and 4 need the same host: equal hostnames *and* equal `machine_id`s. A different or unreadable `machine_id` falls to row 3 (`unverifiable`). "Scope judgments" states the residual assumption: an image that bakes one machine id into every container, run under a reused hostname. It also states the cost: a host with no readable machine id needs one acknowledged `--abandon` after a reboot during a worker (Design, CP5, CP8, Migration). |
| O3 | Accepted, all three parts. Confirmed that `cmd_explain` only calls `evidence.decide` (`controller/cli.py:290-303`). The relaunch bound's J counts only terminal records (CP4B). `explain` reports the pending job files and their clearing commands ahead of its decision, through the same `job.pending_reconciliation_jobs` scan the refusal uses. `explain --json` gains `pending_jobs` (Design, CP5). CP4B and CP5 each add a test. |
| O4 | Accepted, all three parts. The pending scan is named as the non-recursive `jobs/*.json` glob, which excludes `jobs/abandoned/`, with a test (Design, CP5). The `on_spawn` callback runs outside the `try` that converts `Popen`'s `OSError`, so its failure is never `WorkerLaunchError` or `WorkerNotStarted`, with a test (Design, CP5). Only `BlockingIOError` is contention (exit 45). Any other `os.open`/`flock` error is `LifecycleLockError` (exit 20), naming the errno, with a test. Found while applying: `flock(2)` documents that NFS emulates `flock` with byte-range locks, whose exclusive form needs a writable descriptor. So a worktree on NFS without `local_lock` cannot be driven. That is stated as a scope judgment and a behavior change (Design, Scope judgments, CP8, Migration). |

Also applied from the round's notes:
- An empty `JOB_ID` is refused explicitly (Design, CP5).
- The Ctrl-C note. CP8's README text says that Ctrl-C ends only the Controller, and where the
  pgid is shown. `execute_step` also writes the worker's pid and pgid to stderr on
  `KeyboardInterrupt` after the `worker_process` flush, then re-raises it unchanged (Design, CP5).
- The first unresolved question's "weaker point", recycled pid-namespace numbers within one boot,
  is stated in "Scope judgments" as a documented residual. It is narrowed by the leader's
  `start_ticks` check and by the member scan.

Found while applying: the process test's `/proc` form is valid only when `/proc` numbers the
Controller's own namespace. So it checks that `/proc/self` resolves to `os.getpid()`, and
otherwise uses the `killpg` form, which always numbers in the caller's namespace (Design, CP5).
CP9's drill cannot show either container case, so CP9 now says which CP5 tests cover them.

## Verification

Narrowest subsets, per checkpoint:

| Checkpoint | Modules |
|---|---|
| CP1 | `tests.test_golden_plan_stage_decisions tests.test_evidence tests.test_target_state` |
| CP2 | `tests.test_job tests.test_job_validation tests.test_resume` |
| CP3 | `tests.test_decision tests.test_evidence tests.test_job tests.test_cli tests.test_golden_plan_stage_decisions tests.test_integration_disposable_repo` |
| CP4 | `tests.test_evidence tests.test_decision tests.test_job_validation tests.test_integration_disposable_repo`, then the full suite per its fixture-repair rule |
| CP4B | `tests.test_evidence tests.test_decision tests.test_job tests.test_cli tests.test_integration_disposable_repo`, then the full suite |
| CP5 | `tests.test_lock tests.test_worker tests.test_job tests.test_job_validation tests.test_resume tests.test_cli tests.test_package_structure tests.test_write_containment` |
| CP6 | `tests.test_routing tests.test_worker tests.test_cli tests.test_job` |
| CP7 | `tests.test_lifecycle_orchestration` |
| CP8 | `tests.test_plan_document_consistency tests.test_checklist_corrections` |
| CP9 | the full run |

## Migration / data-integrity notes

- **No target-repository state changes shape.** Controller still never writes
  `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` or any file in a target. The lifecycle lock is an
  `flock` on an existing directory descriptor and creates nothing.
- **Job-record schema stays `schema_version: 1`, additively:**
  - `pre_state.bundle_manifest_bundle_id`;
  - `lifecycle_lock`;
  - `worker_route`;
  - `worker_process`;
  - `selected_action.task_addendum`;
  - `worker_process` carries `pid_namespace` and `machine_id` (round 4, O2) beside `boot_id` and
    `hostname`;
  - `resume_marked.outcome: "worker_active"` and `"worker_unverifiable"`;
  - new `reason`/`postcondition_detail` values, and the `reconciliation_evidence.code` values
    `UnreconcilableJobError`/`OperatorAbandoned`/`WorkerNotStarted` on a `FAILED` record, with
    `worker_liveness` on an abandoned record that carried `worker_process`.

  No new job status is added: the terminal disposition reuses `FAILED`. `validate_record` reads
  none of these fields. Records on disk from earlier versions stay valid. A predicate or liveness
  check that needs a field such a record lacks fails closed (predicate) or falls back to today's
  rule (liveness, lock-guarded).
- **Non-terminal records left by an earlier version.** The new pending-reconciliation refusal
  makes each one a hard stop for `step`/`run` until `resume` runs. In particular, the base
  version's `/apply-functional-review` crash leaves a `PLANNED` record (`controller/job.py:1925-1927`).
  Under this milestone that forces exactly one `resume`, which reconciles it `INTERRUPTED` (row 1).
  A pre-milestone `LAUNCHED` record gets today's reconciliation. If that raises
  `UnreconcilableJobError`, the record stays `LAUNCHED`, because the lock cannot prove its worker
  gone, and `resume --abandon JOB_ID` is the operator's way out. The same command clears every
  other job file `resume` cannot reconcile: a record failing `validate_record`, an unknown
  status, or an unparseable file (Design). Its refusals are:
  - a held lock, or a running member of the recorded group, never a zombie, observed in this
    boot and pid namespace. Either ends when that process exits or is ended (round 4, I1);
  - a record from a newer Controller generation, which that Controller clears;
  - an `unverifiable` record (another or unidentifiable host, another pid namespace, no boot
    identity and a possibly live recorded group, or a `killpg`-only answer that cannot rule out a
    zombie), which `--acknowledge-unverifiable-worker` overrides;
  - a malformed `JOB_ID`, which the message corrects.

  A reboot during a worker makes its record `inactive` on a host with a readable `machine_id`, so
  `resume` alone clears it. Without one, the record is `unverifiable`, and the flag clears it. The
  refusal is therefore never permanent (round 3, I1; round 4, I1 and O2).
- **`--abandon` writes only the Controller's own runtime**: a record marked `FAILED` in place, or,
  for an unparseable file, a byte-for-byte copy under `jobs/abandoned/` plus a minimal terminal
  record in its place. Nothing in a target is touched.
- **Forward-incompatibility.** A record written by this version for a new implementation-stage row
  would make an *older* Gen-1 checkout's `resume` hit its "no known expected transition"
  `AssertionError`. Downgrading the Controller while such records are non-terminal is unsupported.
  `controller/GENERATION.json` stays `1` (see Non-goals).
- **Behavior changes for existing users**, each intended:
  - `run` now continues through the implementation stage instead of stopping at exit 15;
  - workers for the five named roles launch with `--model claude-opus-5-5 --effort xhigh`
    unless overridden;
  - review workers cannot spawn subagents;
  - `step`/`run` without `--timeout` wait for the worker indefinitely;
  - `step`/`run`/`resume` refuse with exit 45 while another Controller or a previous worker holds
    the worktree;
  - `step`/`run`/`resume` exit 20 with `LifecycleLockError` where the lock cannot be taken for any
    reason other than contention. That includes a worktree on NFS without
    `local_lock=flock|all`, which today's Controller drives without a lock (round 4, O4);
  - `explain` reports pending job files, with each one's clearing command, ahead of its decision,
    and `explain --json` gains `pending_jobs` (round 4, O3);
  - `explain`/`inspect` report `lifecycle lock: unknown`, never `free`, when run outside the init
    pid namespace and nobody visibly holds the lock (round 4, I2);
  - Ctrl-C during a worker prints the worker's pid and pgid and `resume` before the interrupt
    propagates as today;
  - `step`/`run` refuse with exit 20 while an earlier job for the target is unreconciled (run
    `resume` first);
  - `resume` persists `FAILED` for an unreconcilable record written by this version (it still
    exits 20 on that call), and `resume --abandon JOB_ID [--acknowledge-unverifiable-worker]` is
    new;
  - `resume` exits 45, reconciling nothing, for a record whose worker is `active` or
    `unverifiable`;
  - a launch that raises `WorkerLaunchError` or `UserOnlyCommandError` now leaves a `FAILED`
    (`WorkerNotStarted`) record instead of a `LAUNCHED` one. The exit code is unchanged, and the
    next `step` proceeds with no `resume`;
  - the `/apply-implementation-review` worker's task carries the pending-write addendum when the
    review-stage write is uncommitted;
  - after an `/apply-implementation-review` attempt that did not verify, the Controller gates
    instead of relaunching, until a new generation replaces the bundle that attempt started
    from;
  - a manual `REVISE` bound to a bundle other than the current one is a gate, not an ingestion;
  - a genuine local plan-review `BLOCK` job is `FINISHED`, where it used to be `FAILED`;
  - `/apply-functional-review` is declined (exit 15) where it used to crash, and so are the
    unreachable version combinations the Design names.
