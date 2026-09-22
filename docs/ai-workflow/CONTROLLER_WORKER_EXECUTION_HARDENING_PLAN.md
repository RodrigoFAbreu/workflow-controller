# Controller worker-execution hardening (Revision 3)

Work item: `workflow-controller-worker-execution-hardening`
Governing workflow version: `2.2` (this repository's `docs/ai-workflow/WORKFLOW_CONFIG.json`
default at creation)
Base commit: `27be244d19df1c5c21e6c7c9fb9b83143c7e45a2` (the acceptance commit of
`workflow-controller-protocol-2-2-compatibility`)

## Goal

Harden Controller Generation 1's worker execution and post-worker reconciliation against
two defects observed while dogfooding Controller on a real RepFlow repository
(`docs/ACTIVE_MILESTONE.md`):

1. Lifecycle workers launch with `--permission-mode acceptEdits` by default. That mode
   allows file edits but, in a non-interactive `claude -p` worker, denies the Bash/Python
   invocations every Workflow command needs (`workflow_state.state_transaction(...)`,
   `./scripts/prepare-ai-review.sh`, ...). The default becomes `auto`; an explicit
   `--permission-mode` still wins.
2. A job is marked `FINISHED` as soon as the expected Workflow phase transition is
   observed, even when the durable artifacts that transition promises are incoherent.
   Observed: `/apply-plan-review` moved `REVISING_PLAN -> AWAITING_LOCAL_PLAN_REVIEW`
   (via `publish_plan_revision`, which runs *before* bundle regeneration) while
   `prepare-ai-review.sh` failed, leaving state at `plan_revision` 11 and the current
   plan bundle still at revision 10. Controller must not call that a success, and must
   not launch the next review against that stale bundle either.

## Non-goals

Carried verbatim from the milestone scope: Workflow Manager bootstrap classification of
`.workflow-manager/installation.json`; any change to Workflow's own `/apply-plan-review`
publication ordering (`scripts/` and `.claude/commands/` are frozen Workflow release
content here); agent/harness portability or multi-harness runtime work. Also out of
scope: recomputing `review_content_id`/`bundle_id` inside Controller (see "Scope
judgments" below).

## Investigation

### Permission mode

- `controller/job.py:139-143`: `DEFAULT_PERMISSION_MODE = "acceptEdits"`, with a comment
  reserving `bypassPermissions` for disposable repositories.
- `controller/cli.py:150`: `--permission-mode` defaults to `None`;
  `controller/cli.py:376` passes `args.permission_mode or job.DEFAULT_PERMISSION_MODE`
  to `job.execute_step`, whose own keyword default is also `DEFAULT_PERMISSION_MODE`
  (`job.py:1709`). This is the only `execute_step` call site in `controller/`
  (`grep -rn "execute_step(" controller/`), so `step` and `run` share it.
- `controller/worker.py:251-281`: `launch()` takes `permission_mode` with **no default**
  and places it verbatim in argv (`--permission-mode <mode>`). Its docstring
  (`worker.py:258-261`) restates the `acceptEdits` posture and must be updated to match;
  the no-default rule itself stays (the caller states its posture explicitly).
- The installed `claude` CLI (`claude --help`, Claude Code 2.1.280) lists
  `"acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"` as
  `--permission-mode` choices, so `auto` is a real, accepted value. Controller does not
  validate the mode string itself and continues not to: the `claude` CLI is the authority
  over which modes exist.
- Existing coverage: `tests/test_cli.py:104-118` only checks global-option *parsing*
  order; `tests/test_worker.py:45,257` pass `acceptEdits` explicitly. Nothing asserts
  what mode a default `step`/`run` actually launches with, or that an override reaches the
  worker argv -- that is what CP1 adds.
- The live integration tests (`tests/test_integration_disposable_repo.py:722,871`) pass
  `--permission-mode bypassPermissions` explicitly against throwaway repositories and are
  unaffected.

### Reconciliation

- Verification rule: `_verify_transition` (`job.py:934-990`, `execute_step` step 8) and
  its resume twin `_row2_verified` (`job.py:1295-1336`, used by `_reconcile_launched`/
  `_reconcile_completed`) verify iff the worker outcome is `SUCCESS`/`INTERRUPTED`, the
  fresh post-phase is in the row's `to_any_of`, and -- only when the row has a
  `predicate` -- that predicate holds. Predicates today exist solely to disambiguate a
  **self-loop** (`to_any_of` containing `from_phase`; `property_table_violations`,
  `job.py:610-715`, enforces "predicate iff self-loop", NO_PHASE row 7 excepted).
- The five rows that land on a plan-review-awaiting phase by moving *out* of their
  from-phase carry no predicate at all, so for them the phase alone decides:
  `("PLANNING", "2.1"|"2.2"|"1", "/milestone-plan")`,
  `("REVISING_PLAN", "2.1"|"2.2", "/apply-plan-review")`. Row 7
  (`NO_PHASE`, `/milestone-plan`) checks only that one new work item appeared. The `"1"`
  self-loop row `("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review")` already
  requires the generator-written bundle digest to change (`_predicate_row5_bundle_regenerated`),
  but not that the regenerated bundle matches the published revision.
- Why the phase is not enough, from the frozen command text itself:
  `.claude/commands/apply-plan-review.md` step 5 calls `publish_plan_revision` (which sets
  both `plan_revision` and `phase = AWAITING_LOCAL_PLAN_REVIEW`,
  `scripts/workflow_state.py:7755-7800`) *before* rerunning `prepare-ai-review.sh`;
  `.claude/commands/milestone-plan.md` step 3 does the same before step 6 generates the
  bundle. A generator failure after that point leaves exactly the observed state.
- The authoritative artifact that says which revision the current bundle belongs to is
  the generator's own `MANIFEST.md`: `render_manifest_md` (`scripts/workflow_fingerprint.py:3090-3150`)
  writes `stage: plan`, `bundle_id`, `review_content_id`, `work_item_id`,
  `plan_revision`, `base_commit`, `generation_head` as labelled lines. On a generation
  failure the generator either never writes (preflight refusal, e.g.
  `prepare-ai-review.sh:95`'s `mismatch_revision`) -- leaving the previous round's
  manifest in place -- or withdraws the bundle (`finalize_bundle_generation` ->
  `withdraw_bundle`, `scripts/workflow_fingerprint.py`: writes the `REJECTED` marker,
  removes the archive and `MANIFEST.md`, renames `current/` to a sibling
  `current.rejected-<token>/`, and -- only when every step completes -- **removes its own
  marker**). So a completed withdrawal leaves no `current/` and no marker: the author-written
  `REVIEW_REQUEST.md`/`TEST_RESULTS.md`/`CONTEXT_FILES.txt` survive only inside the
  quarantine directory, and the generator's next run recreates them as empty stubs
  (`prepare-ai-review.sh:361-372`). The marker persists only while a withdrawal is in
  progress or after one fails partway (`BundleWithdrawalError`, marker naming the failed
  step and surviving paths); a later successful generation clears it
  (`clear_rejected_marker_if_present`). The generator's own preflight already
  treats "state `plan_revision` != plan metadata revision" as incoherent; Controller reads
  the same fact from the output side.
- Controller already reads `MANIFEST.md` (`evidence.read_manifest_fields`,
  `evidence.py:214-228`) but only its `bundle_id`/`generation_head` lines, and never
  imports `scripts/` (`target_state.py:12`, `evidence.py:429`) -- a boundary this plan
  keeps.
- A `FAILED` job stops `controller run` (`cli.py:395`, exit 30), but nothing stops a
  later `controller step` from deciding `/review-plan` at `AWAITING_LOCAL_PLAN_REVIEW`
  (`evidence._decide_awaiting_local_plan_review`, `evidence.py:520-555`) against the
  stale bundle. The existing `REJECTED`-marker pre-check in `evidence.decide`
  (`evidence.py:962-990`) is the precedent for a bundle-level gate checked ahead of the
  per-phase handlers.

### Artifact declaration

`docs/ai-workflow/registry/workflow-controller-worker-execution-hardening-artifacts.json`
was generated by `workflow_state.generate_artifacts_declarations(...,
work_item_type="product")` and then corrected in `SELF_REVIEWING_PLAN`, before any
approval exists: the default product template names neither `controller/`, `tests/` nor
`pyproject.toml` (they would be unclassified, i.e. fail closed), and it excludes
`README.md` at the implementation stage although CP1 edits it. Corrections, matching the
previous Controller work item's own declaration: `controller/`/`tests/` added to
`plan_stage.excluded_prefixes` and `implementation_stage.protected_prefixes`;
`pyproject.toml` to `plan_stage.excluded_paths` and `implementation_stage.protected_paths`;
`README.md` moved from `implementation_stage.excluded_paths` to
`implementation_stage.protected_paths`. Every other tracked top-level path
(`.claude/`, `.github/`, `.gitignore`, `CLAUDE.md`, `docs/`, `scripts/`,
`.workflow-manager/`) is covered by the template unchanged.

## Scope judgments (for the reviewer to confirm or cut)

- **The coherence invariant is revision-level, not content-level.** Controller checks
  that the current plan bundle's manifest declares `stage: plan`, the right
  `work_item_id`, and `plan_revision` equal to the work item's fresh post-state
  `plan_revision`. It does **not** recompute `review_content_id`/`bundle_id` -- that would
  duplicate Workflow's fingerprinting logic, which the milestone scope asks us not to do.
  Consequence, stated honestly: a regeneration failure on a round that did *not* advance
  `plan_revision` is invisible to this check; Workflow's own `/review-plan` binding
  checks remain the authority there.
- **CP4 (the decision-time gate) goes slightly beyond "reconciliation".** Without it the
  new `FAILED` status is advisory -- the very next `controller step` would launch
  `/review-plan` against the stale bundle. It reuses CP2's single coherence reader, so it
  adds no second definition of "coherent". If the reviewer considers it out of scope, CP4
  can be dropped without affecting CP1-CP3/CP5.
- **Status on a failed postcondition is `FAILED`** (with a new
  `reason: "postcondition_not_satisfied"`), not `INCOMPLETE`: `INCOMPLETE` is keyed on the
  observed *phase* (`_INCOMPLETE_EFFECT_PHASES`), and here the phase is a genuine
  completion phase with incoherent artifacts. That is the outcome on `execute_step` and on
  resume of a `COMPLETED` record. Resume of a `LAUNCHED` record has no worker outcome to
  fail and keeps `_reconcile_launched`'s existing three-way split (`job.py:1373-1403`),
  which the postcondition changes only by making "verified" harder to reach -- so a
  failed postcondition there does **not** have a single outcome:
  - durable state moved (observed phase != pre-phase, or target HEAD != pre-state HEAD)
    and the postcondition fails -> the existing fail-closed `UnreconcilableJobError`
    (row 4). This is the RepFlow shape on the `"2.1"`/`"2.2"` rows, whose publication moves
    the phase. The error's `evidence` gains the same `postcondition_detail` so the human
    sees *why* the moved state did not verify.
  - phase **and** HEAD both unchanged -> `INTERRUPTED` (row 3), exactly as today. Only the
    `"1"` self-loop row (`AWAITING_EXTERNAL_PLAN_REVIEW` -> itself) can land here with
    work partly done: `publish_plan_revision` sets a `"1"` item's phase to
    `AWAITING_EXTERNAL_PLAN_REVIEW` (`scripts/workflow_state.py:7788-7805`), i.e. leaves it
    unchanged, and a plan-stage command commits nothing, so a worker killed after
    publishing but before regenerating is indistinguishable, on the two signals row 3
    reads, from one killed before doing anything. This plan does not add a third signal:
    `INTERRUPTED` means "a fresh `step` may retry", and for a `"1"` item the retry *is* the
    legal recovery -- the unchanged bundle still carries the `Reviewed bundle ID:` the
    on-file feedback binds to, so the next decision is `/apply-plan-review` again
    (`evidence._decide_awaiting_external_plan_review`, `evidence.py:640-700`), and
    `publish_plan_revision` is idempotent for a repeated revision (`workflow_state.py:7799`).
    CP5 pins this case explicitly rather than leaving it implied.

## Checkpoints

<!-- generated by workflow_state.render_registry_markdown -- do not hand-edit -->
| id | name | depends_on | complexity | session_target |
| --- | --- | --- | --- | --- |
| CP1 | Default lifecycle-worker permission mode to auto, preserving explicit --permission-mode overrides, with CLI/execute_step/worker-argv regression coverage | - | 2 | 1 |
| CP2 | Single plan-bundle coherence reader in evidence.py (manifest stage/work_item_id/plan_revision vs. state plan_revision), with per-clause tests | - | 2 | 1 |
| CP3 | Postcondition column on ExpectedOutcome, evaluated identically by execute_step and resume (LAUNCHED and COMPLETED), attached to every plan-bundle-producing row; table properties and existing-fixture repair | CP2 | 4 | 1 |
| CP4 | Decision-time gate refusing to launch plan review against an incoherent or withdrawn plan bundle at AWAITING_LOCAL_PLAN_REVIEW/AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW (REJECTED gate included), advertising an executable author-file write-or-refresh-then-regenerate recovery | CP2 | 3 | 1 |
| CP5 | Regression reproducing the observed partial /apply-plan-review (state at revision 11, bundle at 10) on execute, COMPLETED/LAUNCHED resume, next-step, recovery-guidance and CLI paths, with a durable pre-fix demonstration | CP3, CP4 | 4 | 1 |
| CP6 | Full verification: Controller suite, green frozen Workflow conformance suites, live disposable-repo runs including one default-auto lifecycle worker and a stale-bundle recovery drill | CP1, CP2, CP3, CP4, CP5 | 3 | 1 |

### CP1 -- default lifecycle-worker permission mode `auto`

Change `controller/job.py`'s `DEFAULT_PERMISSION_MODE` to `"auto"` and rewrite its
comment: `auto` is the default for lifecycle workers against a managed repository;
`bypassPermissions` remains reserved for disposable throwaway repositories and is never
a default; any explicit `--permission-mode` is passed through unchanged. Update the
matching `worker.launch` docstring sentence (`worker.py:258-261`) and `README.md:65`'s
option list to name the default. `cli.py`'s `args.permission_mode or
job.DEFAULT_PERMISSION_MODE` logic is already correct and stays.

**Files**: `controller/job.py`, `controller/worker.py` (docstring only), `README.md`.

**Tests** (`tests/test_cli.py`, `tests/test_job.py`):
- `job.DEFAULT_PERMISSION_MODE == "auto"`, and `inspect.signature(job.execute_step)`'s
  `permission_mode` default is `"auto"`.
- Through the real CLI `step` path (patching `job.execute_step` the way `test_cli.py`'s
  existing step tests stub collaborators): with no `--permission-mode`, `execute_step`
  receives `permission_mode="auto"`; with `--permission-mode acceptEdits` and with
  `--permission-mode bypassPermissions`, it receives exactly that value. Same pair for
  `run`.
- End to end through `execute_step` with `tests/fake_claude.py` as `claude_bin` and
  `FAKE_CLAUDE_DIAG_FILE` set: the worker's observed argv contains
  `--permission-mode auto` by default and `--permission-mode acceptEdits` when passed
  explicitly -- proving the value reaches the real subprocess argv, not only a function
  argument.

### CP2 -- one plan-bundle coherence reader

Extend `evidence.read_manifest_fields` additively with the manifest's `stage:`,
`work_item_id:` and `plan_revision:` labelled lines (same `read_labelled_line` idiom;
absent lines read as `None`; existing keys and callers unchanged). Add
`evidence.plan_bundle_coherence(root, work_item_id, plan_revision) -> tuple[bool, str]`
returning `(coherent, detail)`: coherent iff the plan-stage bundle dir
(`resolve_bundle_dir(..., phase=<a plan-stage phase>)`, i.e. `.ai-review/<id>/current/`)
has a readable `MANIFEST.md` whose `stage` is `plan`, whose `work_item_id` equals
`work_item_id`, whose `bundle_id` is present, and whose `plan_revision` parses as an int
equal to `plan_revision`. `detail` names the first failing clause and the observed
value (e.g. `"manifest plan_revision 10 != state plan_revision 11"`), for the job
record's `reconciliation_evidence` and the CP4 gate text. A `plan_revision` of `None` on
the state side is incoherent (fail closed). This is the single definition CP3 and CP4
both call.

Extend `tests/fixtures.build_manifest_text` with optional `stage`, `work_item_id`,
`plan_revision` keyword arguments (default `None` = line omitted, so every existing
caller's output is byte-identical).

**Files**: `controller/evidence.py`, `tests/fixtures.py`, `tests/test_evidence.py`.

**Tests**: coherent manifest -> `(True, ...)`; each clause failing independently
(missing manifest, withdrawn `current/`, `stage: implementation`, wrong `work_item_id`,
missing/`abc`/stale `plan_revision`, missing `bundle_id`, state `plan_revision=None`) ->
`(False, <detail naming that clause>)`.

### CP3 -- postconditions in the ExpectedOutcome table

Add a data column to `ExpectedOutcome`: `postcondition: PostconditionFn | None` plus
`postcondition_phases: frozenset[str]` (the observed phases on which it is evaluated;
empty iff no postcondition). A postcondition is distinct from the existing self-loop
`predicate` and is not subject to its "predicate iff self-loop" rule.
`PostconditionFn` is `(root, work_item_id, pre_state) -> tuple[bool, str]`, evaluated
fresh against disk. Define `_postcondition_plan_bundle_coherent`: resolve the work item
id (for row 7, where `work_item_id` is `None`, the single new key from
`pre_state["pre_work_item_keys"]`, exactly as `_observe_post_phase` does; no single new
key -> not satisfied), read its fresh post-state `plan_revision` through
`target_state.read`, and return `evidence.plan_bundle_coherence(...)`. Any read failure
is "not satisfied" (never a false verification).

Attach it to every row whose action publishes a plan revision and then generates a plan
bundle, evaluated on the plan-review-awaiting destination phase:
- `("PLANNING", "2.1"|"2.2", "/milestone-plan")` and row 7 (`NO_PHASE`) on
  `{"AWAITING_LOCAL_PLAN_REVIEW"}`;
- `("PLANNING", "1", "/milestone-plan")` on `{"AWAITING_EXTERNAL_PLAN_REVIEW"}` --
  attached for uniform fail-closed behaviour, **not** because the `"1"` branch publishes
  a revision the way `"2.1"`/`"2.2"` does (manual-external round 1, `O2`). The frozen
  command says the opposite: its `"1"` branch performs "no
  `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` reads or writes beyond the one just
  performed" (`.claude/commands/milestone-plan.md:59-63`), so that branch never itself
  moves the phase this row expects nor writes the `plan_revision` the postcondition
  compares. (The row's existing `writer_calls` entry for `publish_plan_revision` points at the
  `[2.1]`-marked sub-step of step 3, `.claude/commands/milestone-plan.md:208`, which the
  `"1"` branch does not execute; that entry predates this work item and is left as is.) The postcondition's only claim is
  therefore conditional: *if* a `"1"` `/milestone-plan` job is ever observed at
  `AWAITING_EXTERNAL_PLAN_REVIEW`, it verifies only when the state's `plan_revision`
  matches a plan-stage manifest for that item -- and a `None` `plan_revision`, the
  expected value for a state entry the `"1"` branch never wrote, reads as not satisfied,
  so such a job lands `FAILED` rather than `FINISHED`. That is the intended fail-closed
  reading, not a claim that `"1"` publishes;
- `("REVISING_PLAN", "2.1"|"2.2", "/apply-plan-review")` on
  `{"AWAITING_LOCAL_PLAN_REVIEW"}`;
- `("AWAITING_EXTERNAL_PLAN_REVIEW", "1", "/apply-plan-review")` on
  `{"AWAITING_EXTERNAL_PLAN_REVIEW"}` (alongside its existing digest predicate).

Not attached to `/review-plan` or `/record-manual-plan-review` rows: neither generates a
bundle.

Wire it into **both** verification sites with identical semantics: `_verify_transition`
and `_row2_verified` evaluate the postcondition after the phase/predicate clauses pass,
when `observed_phase_after in outcome.postcondition_phases`; failure yields
`reason: "postcondition_not_satisfied"` and adds the returned `detail` as
`postcondition_detail` in the `TransitionNotObservedError`-shaped evidence
(`execute_step` -> `FAILED`; `_reconcile_completed` -> `FAILED`; `_reconcile_launched`
-> unchanged three-way split per "Scope judgments": moved state -> `UnreconcilableJobError`,
now carrying `postcondition_detail` in its `evidence` when that was the failing clause --
`_reconcile_launched` stops discarding `_row2_verified`'s reason for this one purpose;
unchanged phase and HEAD -> `INTERRUPTED`). Factor the shared clause into one helper so the two
sites cannot drift again (the drift `_row2_verified`'s own docstring records, revision
64's `B1`).

Extend `property_table_violations` with the column's structural rules:
`postcondition_phases` non-empty iff `postcondition` is set, and
`postcondition_phases ⊆ to_any_of`. Each gets a negative instantiation in
`tests/test_job_validation.py` (a synthetic broken row), per that file's "a property that
cannot fail is not a property" convention. `PRE_STATE_FIELDS` and the durable record
schema are unchanged: the postcondition reads only `pre_work_item_keys` (already
captured) and fresh disk state.

**Existing-fixture repair (migration risk inside the test suite)**: every existing test
that expects `FINISHED` for one of the seven rows above currently has its fake worker
write only `WORKFLOW_STATE.json`. Those fixtures must now also write a coherent plan
`MANIFEST.md`. Add `FAKE_CLAUDE_WRITES` to `tests/fake_claude.py` (a JSON list of
`{"path", "text"}` objects, applied after the existing single-file
`FAKE_CLAUDE_WRITE_PATH`/`_TEXT` write, which stays for backward compatibility), and
update the affected tests in `tests/test_job.py`, `tests/test_job_validation.py`,
`tests/test_resume.py` (for resume, the coherent manifest is written into the target
before `resume` runs). No assertion is weakened: each such test keeps asserting
`FINISHED`, now with the artifact the transition promises actually present.

**Files**: `controller/job.py`, `tests/fake_claude.py`, `tests/test_job.py`,
`tests/test_job_validation.py`, `tests/test_resume.py`.

**Tests**: per row group, `FINISHED` with a coherent manifest and `FAILED`
(`postcondition_not_satisfied`) with a stale one, on both `execute_step` and
`_reconcile_completed`; on `_reconcile_launched`, per row group, `FINISHED` with a coherent
manifest and, with a stale one, `UnreconcilableJobError` whose `evidence` carries
`postcondition_detail` (phase moved) -- plus the `"1"` self-loop unchanged-phase/HEAD case
-> `INTERRUPTED` (CP5 carries the RepFlow-shaped instances of both); row 7 with zero/two
new keys -> not satisfied; property negatives above.

### CP4 -- refuse to review a stale plan bundle

In `evidence.decide`, immediately after the `REJECTED`-marker pre-check and before the
per-phase handlers, for a work item at `AWAITING_LOCAL_PLAN_REVIEW` or
`AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` (the two phases whose automatic actions consume the
current plan bundle), call `plan_bundle_coherence(root, work_item_id,
work_item.plan_revision)`; if incoherent, return a `HumanGate` (not automatic, not
declined), checked before the local-review BLOCK gate -- the same "bundle-level facts
first" ordering the `REJECTED` check already uses.

**Recovery guidance must be executable** (manual-external round 1, `I1`). The bare
`_regeneration_command` (`evidence.py:953-956`, `scripts/prepare-ai-review.sh <base-sha>
plan <id>`) is *not* a working recovery for this state: the observed failure leaves the
bundle's author-written `REVIEW_REQUEST.md`/`TEST_RESULTS.md` describing the previous
round, and the generator's own closing checks refuse (`review_content_id` restatement,
`assert_review_request_states_review_content_id`) or withdraw the bundle (`stage: plan
(revision N)`/`head:` lines, `assert_test_results_consistent_with_plan_review_request`)
on exactly that -- `/apply-plan-review` step 5 requires both refreshed *before* the
generator runs. Nor can recovery be routed through `/apply-plan-review`: the item is
already past `REVISING_PLAN`, and the Controller decision table maps no action to it from
either gated phase. There is no other Workflow command that performs these refreshes, so
the gate states them as ordered human steps. A new `_plan_bundle_recovery_steps(root,
work_item) -> tuple[str, ...]` in `evidence.py` returns, in this order:
1. **write or refresh** `<bundle_dir>/REVIEW_REQUEST.md` so that it states `review_content_id:
   <hex>` with the value from the single canonical plan-stage entry point
   `REVIEW_PROTOCOL.md`'s "Computing `review_content_id`" names --
   `workflow_fingerprint.compute_review_content_id_plan_stage_for_work_item(repo_root,
   "<id>")[0]` -- never the previous round's value;
2. **write or refresh** `<bundle_dir>/TEST_RESULTS.md` so that its labelled lines read
   `stage: plan (revision <state plan_revision>)` and `head: <output of git rev-parse
   HEAD>`;
3. `scripts/prepare-ai-review.sh <base_commit> plan <id>` -- with the work item's own
   `base_commit` substituted (`<base-sha>` only when the state carries none) -- with the
   precondition clause: "if this refuses at preflight (`base_commit`, `plan_revision`
   mirror vs. registry, or plan-stage metadata such as the plan document's `(Revision N)`
   marker), its message names the upstream plan/registry artifact to repair first"
   (plan-stage preflight, `prepare-ai-review.sh:73-117`; round-2 local review, `O1`).

**Absent `current/` or absent author files** (round-2 local review, `I1`/`O2`): when
`<bundle_dir>` itself or any of `REVIEW_REQUEST.md`/`TEST_RESULTS.md`/`CONTEXT_FILES.txt`
is missing -- a completed withdrawal (see "Investigation"), or a first-round
`/milestone-plan` generator failure before step 6 wrote them -- steps 1-2 are rendered as
"write" rather than "refresh", each names `REVIEW_PROTOCOL.md`'s "Review request format"
as the file's required shape (so a bare one-line `review_content_id:` file is not what the
gate asks for), and a step 0 is prepended: "write `<bundle_dir>/CONTEXT_FILES.txt`",
naming, when one exists, the newest `.ai-review/<id>/current.rejected-*/` quarantine
directory as the source of the previous round's author files to restore from. Which
variant renders is decided from `root`-relative existence checks only; Controller never
copies the files itself.

`<bundle_dir>` is rendered as the resolved plan-stage path, `<id>` and the revision as
their actual values. `what_is_required` states the coherence detail, then "before any
plan review runs, perform in order:" and the numbered steps; `safe_resume_command` is the
same numbered sequence on one line (it names the *sequence* to run, of which the
generator is the last element -- never the generator alone); `artifact_path` is the
bundle's `MANIFEST.md`. Controller performs none of these writes itself: they are
Workflow author-file edits and a Workflow script, and Controller still imports nothing
from `scripts/`.

**`REJECTED`-marker gate at the two gated phases** (round-2 local review, `I1`): the
pre-existing marker gate keeps its precedence (checked first, for every
`BUNDLE_BEARING_PHASES` member) and its evidence line, but at `AWAITING_LOCAL_PLAN_REVIEW`
and `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` its `what_is_required`/`safe_resume_command`
become the marker's recorded detail (naming the failed withdrawal step and surviving
paths, to be cleared first) followed by `_plan_bundle_recovery_steps` -- never
`_regeneration_command` alone. A marker only survives an in-progress or partially failed
withdrawal, after which `current/`'s author files may be stale or absent exactly as above,
and the generator's own success is what clears the marker. At every other bundle-bearing
phase the marker gate and its `_regeneration_command` are unchanged (outside this work
item's plan-stage scope).

`AWAITING_EXTERNAL_PLAN_REVIEW` (`"1"`) is deliberately **not** gated: there the stale
state's legal recovery is re-running `/apply-plan-review`, which is exactly what the
existing decision already selects (see "Scope judgments", `LAUNCHED` bullet).

**Existing-fixture repair**: the gate fires on any test that calls `evidence.decide` (or
reaches it through `execute_step`/`resume`/the CLI) at either gated phase with a synthetic
`MANIFEST.md` lacking `stage`/`work_item_id`/`plan_revision`, or with none. Known
surface: `tests/test_evidence.py`, `tests/test_job_validation.py` (the
`evidence.decide` calls at lines 351-454 and the `_write_state_phase_env` executions at
`AWAITING_LOCAL_PLAN_REVIEW`), `tests/test_decision.py`, `tests/test_job.py`,
`tests/test_resume.py`, `tests/test_cli.py`. Each such test that asserts today's decision
gets a coherent manifest fixture (same repair rule as CP3, no assertion weakened); CP4 is
complete only when the full Controller suite is green, not the narrow subset alone.

**Files**: `controller/evidence.py`, `tests/test_evidence.py`,
`tests/test_job_validation.py`, and whichever of `tests/test_decision.py`,
`tests/test_job.py`, `tests/test_resume.py`, `tests/test_cli.py` the repair rule above
reaches.

**Tests**: at each of the two phases, stale manifest -> gate, no action; coherent
manifest -> today's decision unchanged; `REJECTED` still wins over staleness when both
hold; `AWAITING_EXTERNAL_PLAN_REVIEW` and every implementation-stage phase are
unaffected. Recovery-guidance regression: for a stale gate, `safe_resume_command` and
`what_is_required` each name, in order, `REVIEW_REQUEST.md` with `review_content_id` and
`compute_review_content_id_plan_stage_for_work_item`, then `TEST_RESULTS.md` with
`stage: plan (revision <N>)` (the state's actual revision) and `head:`, then
`scripts/prepare-ai-review.sh <the work item's base_commit> plan <id>`; and
`safe_resume_command` is never equal to `_regeneration_command(phase, id)` alone -- the
assertion that keeps Controller from advertising the non-working command again; the
generator step carries the preflight-refusal clause (`O1`). Withdrawn variant: with
`current/` absent and a `current.rejected-<token>/` sibling present, the gate fires on the
missing-manifest clause, steps read "write", step 0 names `CONTEXT_FILES.txt` and the
quarantine directory, and `REVIEW_PROTOCOL.md`'s request format is named; the same holds
with `current/` present but `REVIEW_REQUEST.md`/`TEST_RESULTS.md`/`CONTEXT_FILES.txt`
absent (first-round shape, `O2`). `REJECTED` gate: at each of the two gated phases, a
marker present -> the marker gate (precedence kept) whose `safe_resume_command` and
`what_is_required` contain the marker detail and CP4's ordered steps and are never
`_regeneration_command(phase, id)` alone; at `AWAITING_EXTERNAL_PLAN_REVIEW` and an
implementation-stage bundle-bearing phase the marker gate is byte-for-byte today's.

### CP5 -- regression: the observed partial `/apply-plan-review`

Reproduce the RepFlow case exactly, as a durable test class in `tests/test_job.py`
(execute path) and `tests/test_resume.py` (resume path):

- Target repository seeded with a `"2.2"` work item at `REVISING_PLAN`,
  `plan_revision: 10`, and a plan bundle `MANIFEST.md` at `plan_revision: 10`, plus the
  `REVIEW_FEEDBACK.md` a `REVISE` round would carry.
- Fake worker (`FAKE_CLAUDE_WRITES`) performs only the state half of step 5:
  `plan_revision: 11`, `phase: AWAITING_LOCAL_PLAN_REVIEW`; manifest untouched; exits
  `SUCCESS`.
- Assert `execute_step` returns `FAILED`, `transition_verified: False`,
  `observed_phase_after: AWAITING_LOCAL_PLAN_REVIEW`,
  `reconciliation_evidence.reason == "postcondition_not_satisfied"` and a
  `postcondition_detail` naming revisions 10 and 11.
- Assert a following `execute_step` on the same target returns `GATE_BLOCKED` (CP4) and
  launches no worker (fake worker configured to fail if invoked, via
  `FAKE_CLAUDE_REQUIRE_FILE` pointing at a path that never exists).
- Resume path, `COMPLETED`: a `COMPLETED` record for the same job, reconciled against the
  same target state, becomes `FAILED` with the same reason, never `FINISHED`.
- Resume path, `LAUNCHED` (the interruption/restart case, `I2`): a `LAUNCHED` record for
  the same job (`pre_state.phase: REVISING_PLAN`, `pre_state.target_head` = the unchanged
  target HEAD), reconciled against the same revision-11-state/revision-10-bundle target,
  raises `UnreconcilableJobError` -- never `FINISHED`, never `INTERRUPTED` -- with
  `evidence["postcondition_detail"]` naming revisions 10 and 11; positive control with a
  revision-11 manifest -> `FINISHED`.
- `"1"` self-loop, `LAUNCHED`, unchanged phase and HEAD: a `"1"` item at
  `AWAITING_EXTERNAL_PLAN_REVIEW` whose worker got as far as publishing `plan_revision: 11`
  but left the revision-10 bundle and the round's feedback in place reconciles to
  `INTERRUPTED` (row 3), and the following decision is the automatic `/apply-plan-review`
  retry, not a gate -- pinning the documented outcome in "Scope judgments".
- Recovery guidance: the `GATE_BLOCKED` record's gate (and `controller explain`'s
  rendering of it) carries CP4's three ordered steps with revision `11` and the seeded
  `base_commit`, never the bare generator command.
- Positive control: same seed, fake worker also writes a revision-11 manifest ->
  `FINISHED`.
- Pre-fix demonstration made durable: with `unittest.mock.patch.dict` over
  `job._EXPECTED_OUTCOMES_BY_KEY` replacing the `("REVISING_PLAN", "2.2",
  "/apply-plan-review")` row with a copy whose postcondition is removed
  (`dataclasses.replace`), the identical scenario verifies as `FINISHED` -- proving the
  postcondition is what makes the test fail closed.
- CLI: `controller step` over the same seed exits `EXIT_WORKER_FAILED` (30).

**Files**: `tests/test_job.py`, `tests/test_resume.py`, `tests/test_cli.py`.

### CP6 -- full verification

Run in this repository's `unittest` idiom (no `pytest` is installed):

1. `python3 -m unittest discover -s tests -t .` -- the Controller suite.
2. The seven frozen Workflow conformance suites green at base, each as
   `python3 scripts/<name>_test.py`: `workflow_acceptance_matrix_test.py`,
   `workflow_fingerprint_generalization_test.py`, `workflow_fingerprint_test.py`,
   `workflow_integration_test.py`, `workflow_state_completion_obligations_test.py`,
   `workflow_state_test.py`, `workflow_test_harness_test.py` (re-listed at
   implementation time from the suites green at base). As in the previous milestone,
   `workflow_fingerprint_demo_test.py` and `workflow_state_demo_test.py` are excluded
   because they already fail at base against Workflow-repository-only artifacts; named
   here, not silently dropped.
3. With a real `CLAUDE_BIN`, run `tests/test_integration_disposable_repo.py` (its live
   tests still pass `bypassPermissions` explicitly, which also exercises the override
   path), and one additional manual live `workflow-controller step` against a disposable
   repository with **no** `--permission-mode`, recording the job record's
   `worker.permission_denials` (expected empty) and `transition_verified` in
   `TEST_RESULTS.md` -- the real-world check that `auto` lets a lifecycle worker run its
   Workflow Bash/Python operations. **This is a stop condition, not only a record**
   (manual-external round 1, `O3`): a non-empty `worker.permission_denials` or
   `transition_verified: false` halts CP6 for investigation -- it is the only live proof
   the new default permits the operations that motivated it, so it is not waived by (1)
   and (2) being green.
4. A live stale-bundle recovery drill in the same kind of disposable repository, using
   only the frozen Workflow entry points (no worker): after a real `/milestone-plan` has
   produced a revision-1 plan bundle, bump the plan document's `(Revision N)` marker,
   regenerate registry/mapping at revision 2 and call `publish_plan_revision` exactly as
   `/apply-plan-review` step 5 does, leaving the bundle at revision 1; confirm
   `workflow-controller step` gates with CP4's text; perform the gate's three steps
   verbatim; confirm `prepare-ai-review.sh` succeeds and the next `workflow-controller
   explain` decides `/review-plan` (no gate). **Withdrawal leg** (round-2 local review,
   `I1`): repeat from a freshly published revision 3, this time performing the gate's
   `REVIEW_REQUEST.md` step but deliberately skipping its `TEST_RESULTS.md` step, so the
   generator's closing check withdraws the bundle (`current/` quarantined); confirm
   `workflow-controller step` gates again, now with the withdrawn-variant text (write,
   `CONTEXT_FILES.txt`, quarantine directory named) and never the bare generator; perform
   that gate's steps verbatim; confirm the generator succeeds and `workflow-controller
   explain` decides `/review-plan`. Record the commands and outputs in
   `TEST_RESULTS.md`. A drill (either leg) that needs any step the gate did not name is a
   CP4 defect and halts CP6.

Zero failures across (1) and (2) is the exit condition; (3) and (4) must also each meet
their own stated stop conditions.

**Files**: none (verification only).

## Review decisions

### Manual-external plan review, round 1 (`REVISE`, 0 Blocking / 2 Important / 3 Optional)

All five findings were validated against the repository and **accepted**; none rejected.

- **I1 -- accepted.** Confirmed: `evidence._regeneration_command` (`controller/evidence.py:953-956`)
  returns only `scripts/prepare-ai-review.sh <base-sha> plan <id>`, while
  `.claude/commands/apply-plan-review.md` step 5 requires `REVIEW_REQUEST.md`'s
  `review_content_id` and `TEST_RESULTS.md`'s `stage: plan (revision N)`/`head:` lines to
  be refreshed before the generator runs. Applied in CP4 ("Recovery guidance must be
  executable"): a three-step ordered recovery, the gate text stating the prerequisites,
  a regression assertion that the advertised command is never the bare generator (CP4,
  CP5), and a live drill following the gate verbatim (CP6 item 4).
- **I2 -- accepted.** Confirmed: `_reconcile_launched` (`controller/job.py:1350-1403`)
  splits unverified records into `INTERRUPTED` (phase and HEAD unchanged) and
  `UnreconcilableJobError` (anything moved), and Revision 1 claimed a single outcome.
  Applied: "Scope judgments" now states both `LAUNCHED` outcomes; CP3 adds
  `_reconcile_launched` tests per row group and carries `postcondition_detail` into the
  unreconcilable evidence; CP5 adds the RepFlow-shaped `LAUNCHED` case and the `"1"`
  self-loop unchanged-phase/HEAD `INTERRUPTED` case.
- **O1 -- accepted.** Confirmed: `tests/test_job_validation.py:351-454` calls
  `evidence.decide` at both gated phases with manifests lacking the new fields. CP4 now
  names it and the rest of the repair surface, and requires the full suite green.
- **O2 -- accepted.** Confirmed at `.claude/commands/milestone-plan.md:59-63`. CP3's row
  list now states the `"1"` `PLANNING` attachment's actual, conditional rationale.
- **O3 -- accepted.** CP6 item 3 is now a stop condition.

### Local-model plan review, round 2 (`REVISE`, 0 Blocking / 1 Important / 2 Optional)

All three findings were validated against the repository and **accepted** (I1 with a
corrected premise); none rejected.

- **I1 -- accepted, premise corrected.** The finding's substance holds: a withdrawal
  after `publish_plan_revision` (e.g. a stale `TEST_RESULTS.md` failing
  `assert_test_results_consistent_with_plan_review_request` inside
  `finalize_bundle_generation`) leaves `current/` quarantined, and Revision 2's
  "refresh" steps then point at files that no longer exist, while the generator recreates
  them only as empty stubs (`prepare-ai-review.sh:361-372`). Its premise that the
  `REJECTED` gate is what fires next is true only for an in-progress or partially failed
  withdrawal: `withdraw_bundle` (`scripts/workflow_fingerprint.py`) ends with
  `marker_path.unlink()` once every step completes ("Only a withdrawal that completes every
  step removes its own marker"), so the ordinary withdrawn state has no marker and no
  `current/`, and CP4's own missing-manifest clause (already a CP2 test case, "withdrawn
  `current/`") is what gates. Applied to both: "Investigation" now states the withdrawal
  mechanics; CP4's steps are "write or refresh", with a withdrawn/absent-author-file
  variant that prepends `CONTEXT_FILES.txt`, names the `current.rejected-*` quarantine as
  the source to restore from, and names `REVIEW_PROTOCOL.md`'s request format; the
  `REJECTED` gate at the two gated phases keeps its precedence but advertises the marker
  detail plus CP4's steps, never `_regeneration_command` alone; CP4 adds the matching
  regressions and CP6 item 4 a withdrawal leg.
- **O1 -- accepted.** Confirmed at `prepare-ai-review.sh:73-117` (`mismatch_base`,
  `mismatch_revision`, `resolve_plan_stage_metadata` errors). CP4's generator step now
  carries the one-clause preflight-refusal precondition; no Controller-side check added.
- **O2 -- accepted.** Confirmed: `.claude/commands/milestone-plan.md` step 6 writes the
  three author files fresh, so a first-round failure may leave none. Covered by the same
  "write or refresh" wording and absent-file variant as I1, with its own CP4 test case.

## Open decisions (`docs/TECHNICAL_DECISIONS.md`)

`docs/TECHNICAL_DECISIONS.md` does not exist in this repository (nor do
`docs/DOMAIN_GLOSSARY.md`/`docs/UX_FLOWS.md`); nothing to finalize. The three choices a
reviewer should explicitly confirm are listed under "Scope judgments" above.

## Verification

Per checkpoint, narrowest subset: `python3 -m unittest tests.test_cli tests.test_job
tests.test_worker` (CP1); `tests.test_evidence` (CP2); `tests.test_job
tests.test_job_validation tests.test_resume` (CP3); `tests.test_evidence
tests.test_job_validation tests.test_decision` (CP4, then the full suite per its fixture-repair
rule); `tests.test_job tests.test_resume tests.test_cli` (CP5). CP6
is the full run.

## Migration / data-integrity notes

- No `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` field and no persisted job-record field
  is added or changed. `reconciliation_evidence` gains an optional
  `postcondition_detail` key and a new `reason` value only on the new failure path (and
  `UnreconcilableJobError.evidence`, an in-memory exception payload, gains the same key);
  records already on disk remain valid under `validate_record`, which never reads
  `reconciliation_evidence` at all (`grep -n reconciliation_evidence controller/job.py`:
  only the three writers at `job.py:1439,1899,1903`).
- Behavioural change for existing users: a default `step`/`run` now launches workers in
  `auto` mode. Anyone relying on `acceptEdits` must pass `--permission-mode acceptEdits`.
  If `auto` is unavailable for an account, the worker fails visibly (a non-`SUCCESS`
  outcome, `FAILED` record) rather than silently degrading; the override is the remedy.
- Jobs that previously would have been `FINISHED` with a stale plan bundle are now
  `FAILED`, and the next step gates instead of launching `/review-plan`. That is the
  intended change.
