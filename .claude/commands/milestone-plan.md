---
description: Plan the next incomplete milestone/checkpoint and stop for external plan review.
argument-hint: "[work-item-id] [base-sha]"
state_writer: true
review-subject: bundle
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter the `PLANNING` state of `docs/ai-workflow/MILESTONE_WORKFLOW.md`.

Optional arguments: `$ARGUMENTS` may name an **existing work item to plan**
and/or a **base commit SHA** to diff from, work-item id first. Resolution is
by lookup against `docs/ai-workflow/WORKFLOW_STATE.json`, never by shape, and
never a guess:

- **no argument** -- identify the milestone from
  `docs/ACTIVE_MILESTONE.md`/`docs/ROADMAP.md` (step 1) and use the
  milestone's starting commit (the completion commit of the previous
  milestone, from `docs/ACTIVE_MILESTONE.md`/`git log`) as the base;
- **one argument** -- a key of `work_items` selects that work item (base
  commit resolved as in the no-argument case); anything else is the base
  commit SHA and must resolve via `git rev-parse --verify <arg>^{commit}`.
  This keeps the historical `/milestone-plan <base-sha>` form working
  unchanged, since a base SHA is never a `work_items` key;
- **two arguments** -- `<work-item-id> <base-sha>`, in that order, nothing
  inferred.

An argument that is neither a `work_items` key nor a resolvable commit is a
refusal naming both attempted resolutions -- never a guess, and never a
silently created work item: the id argument selects an **existing** entry
only, exactly like `[work-item-id]` on every other command. A brand-new
milestone's id is still derived in step 1, not passed here.

`<bundle_dir>`/`<feedback_dir>` below resolve per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir(repo_root, work_item_id,
stage="plan")`/`resolve_feedback_dir(repo_root, work_item_id)`). This is a
plan-stage command, so `<bundle_dir>` is **always**
`.ai-review/<work_item_id>/current/` -- the `stage="plan"` argument is
required and load-bearing, never decorative: without it the resolver takes
its compatibility branch and answers the flat `.ai-review/current/` for a
work item with nothing under `.ai-review/<work_item_id>/` yet (a
brand-new milestone's first plan bundle, or the first
`/milestone-plan <child-id>` on a remediation child), while the generator
writes and validates the scoped one. `<feedback_dir>` takes no stage
argument: `feedback/` is stage-agnostic and keeps the scoped-else-flat
rule for every stage alike.

0. **Dual-mode branch** (Workflow v2.1, WF1b): read
   `docs/ai-workflow/WORKFLOW_CONFIG.json` (missing/corrupt before
   activation defaults to `default_workflow_version: "1"`) and
   `docs/ai-workflow/WORKFLOW_STATE.json` (missing entirely is equivalent
   to "no work items yet"). If the milestone identified in step 1 already
   has a `work_items[id]` entry, its own `governing_workflow_version`
   (fixed at that entry's creation) governs this run, never the current
   config default. Otherwise this is a fresh milestone and the config's
   *current* `default_workflow_version` governs it.
   - **`governing_workflow_version: "1"`** (today, always, until
     `WF-Activate` runs): steps 1-7 below execute exactly as written, with
     no `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` reads or writes
     beyond the one just performed — this branch is v1-inert by
     construction, verified by a golden-output test (`WF8a-ii`).
   - **`governing_workflow_version: "2.1"`**: steps 1-7 below still
     execute (the plan-production/self-review/gate mechanics are
     version-independent), plus the additional sub-steps marked **[2.1]**
     interleaved below.
   - **`governing_workflow_version: "2.2"`** (workflow-2.5.0,
     `D-Implementation-Review-Version-Activation`): takes the identical
     `"2.1"` branch immediately above -- this command's own plan-production/
     self-review/gate mechanics, and the two-stage plan-review protocol
     they hand off to, are unaffected by the `"2.2"` implementation-review
     bump; `TWO_STAGE_PLAN_REVIEW_VERSIONS` already covers both.
   - **Explicitly selected target** (version-independent): when
     `$ARGUMENTS` named a `work_items` key, **that entry is the target** --
     step 1 does not re-derive one from
     `docs/ACTIVE_MILESTONE.md`/`docs/ROADMAP.md`, and its own stored
     `governing_workflow_version` governs this run. Refuse, naming the id,
     if that entry is at a terminal phase. This is the only way to plan a
     work item that is not `active_work_item_id`, and
     `route_work_item(...)` deliberately leaves an unrelated active item
     alone (`D1`: `active_work_item_id` is a resume-focus pointer, not an
     execution lock), so the pointer is **not** repointed here and every
     later command for this item must be given the same id explicitly.
     The case this exists for is a **remediation child**
     (`<parent-id>-remediation-<n>`, `parent_work_item_id` non-null,
     created by `/apply-functional-review`'s broad branch): its scope is
     the parent's own functional-review deferral note, not a roadmap
     milestone; its `plan_path`/`registry_path`/`base_commit` were fixed
     at creation, so step 1 `[2.1]` re-declares exactly those values and
     supplies only the still-`null` `mapping_path`
     (`route_work_item`'s resume branch accepts a null-to-value fill and
     refuses a genuine conflict with
     `ws.WorkItemDeclarationFactConflictError`). Do not pass a `<base-sha>`
     that differs from such an entry's own `base_commit`; the same refusal
     catches it.
1. Inspect Git state (`git status --short`, `git log --oneline -10`) and read
   `docs/ACTIVE_MILESTONE.md` and `docs/ROADMAP.md` to identify the next
   incomplete milestone/checkpoint.
   - **[2.1]** Derive a `work_item_id` slug from the milestone (matching
     `^[a-z0-9][a-z0-9_-]{0,63}$`, e.g. `milestone-9`), or reuse the
     existing entry's id if resuming a milestone already present in
     `work_items`. **Declare** (not yet populate) this milestone's own
     `plan_path`/`registry_path`/`mapping_path`/`base_commit` --
     `docs/ai-workflow/WORKFLOW_V2_PLAN.md`-equivalent, `docs/ai-workflow/registry/<work_item_id>-registry.json`,
     `docs/ai-workflow/requirements/<work_item_id>-mapping.json`, and the
     resolved base commit respectively (`D-Fingerprint-Generalization`).
     Call `workflow_state.route_work_item(...)`, passing all four **and
     `repo_root`**, (D1's create-or-resume routing: creates a fresh
     `work_items[id]` entry fixing `governing_workflow_version` from the
     config default at this moment, or advances
     `plan_revision`/`state_revision` on an existing non-terminal entry --
     the same call also idempotently accepts these four facts on a
     *resumed*, pre-declared entry that still has some or all of them
     `null`, e.g. a synthetic dry-run item created with only `plan_path`
     set; refuses a terminal-phase id reuse, and refuses a genuine conflict
     on an already-non-null fact) and persist the returned state to
     `docs/ai-workflow/WORKFLOW_STATE.json`. On a **fresh** id,
     `repo_root` also gates the id against `D-Checkpoint-Ownership`'s
     origination reference (`WFR-66`): a `work_item_id` that has ever
     appeared there is permanently non-reusable and refuses with
     `ws.WorkItemIdReusedError` (report the refusal and stop; use a
     different id). An undecidable reference read refuses with
     `ws.IdentityReferenceUndecidableError` -- present `.evidence` to the
     user and, only on their explicit authorization of the literal it
     derives, clear it with `workflow_state.authorize_identity_reference_gap(...)`
     before retrying `route_work_item(...)`.
2. Load only documentation relevant to that milestone: the linked execution
   guide, the reference guide only for unresolved detail, and any of
   `docs/DOMAIN_GLOSSARY.md`, `docs/UX_FLOWS.md`,
   `docs/adr/0002-offline-first-local-database-source-of-truth.md`,
   `docs/adr/0003-layered-modular-architecture.md`,
   `docs/TECHNICAL_DECISIONS.md` only as the task actually touches those
   areas. Do not read `docs/agent-context/` or completed-milestone docs
   unless a specific historical decision is unresolved.
3. Produce or update the execution/reference plan for this milestone
   (checkpoints, files touched, tests to add, migration needs if any).
   - **[2.1]** Build the checkpoint list as `{id, name, depends_on,
     complexity, session_target}` entries and the requirement map as
     `{requirement_id: {description, checkpoint_ids}}`, then call
     `workflow_state.generate_registry(...)`/`generate_mapping(...)`
     (validates D-Selection's topological-order rule and D3's
     bidirectional coverage rule at generation time, never left to be
     discovered at review time) and
     `workflow_state.write_registry_and_mapping(...)` to **populate** real
     content at the paths step 1 `[2.1]` just declared:
     `docs/ai-workflow/registry/<work_item_id>-registry.json` and
     `docs/ai-workflow/requirements/<work_item_id>-mapping.json` — the
     sole writer either file should ever have (D-Registry). Before writing,
     this call also checks every checkpoint id genuinely *new* to this
     revision (absent from whatever registry currently sits on disk)
     against `D-Checkpoint-Ownership`'s origination reference (`WFR-66`) —
     an id kept live across revisions is never rechecked, only one
     reintroduced after retirement — refusing with `ws.CheckpointIdReusedError`
     (pick a different id) or `ws.IdentityReferenceUndecidableError` (same
     `authorize_identity_reference_gap` escape as above, scoped to this
     `(work_item_id, checkpoint_id)` pair). Embed
     `workflow_state.render_registry_markdown(registry)`'s output as the
     plan document's own generated, human-readable checkpoint table —
     never hand-edited, never itself hashed. In the same pass, call
     `workflow_state.generate_artifacts_declarations(work_item_id, plan_path,
     registry_path, mapping_path, work_item_type=<this item's own
     work_item_type>)` and write its result to
     `docs/ai-workflow/registry/<work_item_id>-artifacts.json` — the
     default `plan_stage` **and** `implementation_stage` classification
     template (`D-Fingerprint-Generalization`), never left for a later
     approval command to invent. `work_item_type` is required and is
     never defaulted: the implementation-stage half is type-specific (a
     `process` item's deliverable tree is this repository's own workflow
     tooling, a `product` item's is the application source), and a
     generator that never saw the type emitted an implementation-stage
     classification naming nothing but its own file — which then failed
     closed on the item's own deliverable at the first implementation
     bundle, after the plan-approval gate (salvage audit `B4`).
     `SELF_REVIEWING_PLAN` (step 4) must confirm the inherited
     `excluded_paths`/`excluded_prefixes` actually fit this item's own
     plan footprint, **at both stages**, before the bundle is generated.
     The template leaves a path under any directory it does not name at
     all unclassified (fail-closed) rather than guessing. It makes exactly
     one judgment rather than deferring it: `docs/ai-workflow/` is
     excluded at both stages, since everything under it is either this
     workflow's own bookkeeping or *another* work item's plan-stage
     content (salvage audit `B7` — two concurrent work items are `D1`'s
     normal case, and hand-enumerating every sibling's plan document in
     every new declaration is what previously got missed at review time).
     A work item whose deliverable genuinely **is** a workflow design
     document under that prefix must move that exact path into
     `implementation_stage.protected_paths` here, the way
     `workflow-v2-1-core-artifacts.json` does for
     `docs/ai-workflow/MILESTONE_WORKFLOW.md` and
     `docs/ai-workflow/REVIEW_PROTOCOL.md`.

     **The declaration is not identity-neutral** (salvage audit `I9`).
     Its own bytes are excluded from both projections, but each stage's
     `review_content_id` hashes that stage's classification *sets*
     alongside the protected content, and those sets are read out of this
     file: editing `plan_stage.*` moves the plan-stage digest and stales
     `plan_approval`; editing `implementation_stage.*` moves the
     implementation-stage digest and stales `technical_approval`. That is
     the intended contract — what is excluded is a reviewed fact — which
     is exactly why step 4 must get this right **now**, before any
     approval exists. A declaration repaired after an approval must be
     carried back through that stage's own review/approval gate: see
     `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Repairing an artifact
     declaration after an approval". Never widen an exclusion to avoid a
     re-review — widening is itself a reviewed-fact change, and it moves
     the digest anyway. Then, in
     the same operation, call `workflow_state.publish_plan_revision(state,
     work_item_id, plan_revision, now)` and persist the returned state to
     `docs/ai-workflow/WORKFLOW_STATE.json` — the sole point that mirrors
     this revision's `plan_revision` into the state file and performs
     `D-Plan-Review-Stages`' phase transition into
     `AWAITING_LOCAL_PLAN_REVIEW`, before this revision's bundle is ever
     generated (`D-Plan-Revision-Publication`, `WFR-65`).
   - **[2.1]** **Staging step, required before any bundle is generated**
     (salvage audit `B6`): mark this item's own `plan_path`,
     `registry_path`, `mapping_path` and
     `docs/ai-workflow/registry/<work_item_id>-artifacts.json`
     **intent-to-add** — `git add -N -- <those four paths>` — and leave
     them that way. Do **not** commit them: the plan-approval commit
     `/approve-review plan` creates is what commits all four, together
     with `WORKFLOW_STATE.json`, as its own four-or-five-member set
     (`resolve_plan_stage_approval_commit_paths`).

     This step is load-bearing, not housekeeping.
     `workflow_fingerprint.resolve_plan_stage_metadata` — the resolver
     every plan-stage read goes through, including
     `scripts/prepare-ai-review.sh`'s own plan-stage preflight — requires
     each of the three declared paths to be **in the Git index**
     (`_validate_plan_stage_metadata_path`'s tracked-path check,
     `GPT-R32-001`/`GPT-R33-002`: an untracked file dropped anywhere in
     the worktree must never become authoritative metadata). A freshly
     created work item's four files are new and untracked, so without
     this step the very next step's generator refuses with
     `InvalidPlanStageMetadataPathError: plan_path '<path>' is not a
     tracked path` before writing any bundle content at all.
     Intent-to-add is the right form: it makes the paths index-visible
     without staging content, and
     `workflow_state.stage_plan_approval_commit_paths`' own pre-staging
     index-isolation check provably ignores an unstaged intent-to-add
     marker (`git diff --name-only --cached HEAD` does not report one),
     so `/approve-review plan` step 5 still starts from a clean index.
     `prepare-ai-review.sh`'s own internal `git add -N` does not
     substitute for this: it runs *after* the plan-stage preflight that
     needs it, and its exit trap resets every path it marked.
4. Enter `SELF_REVIEWING_PLAN`: critically check the plan for missing
   requirements, migration risk, usability gaps, unnecessary complexity, and
   missing tests. Revise the plan in place — do not write a separate
   self-review journal.
5. Check the plan against every "Open decision" row in
   `docs/TECHNICAL_DECISIONS.md` it touches — flag any it would silently
   finalize instead of deciding for the user.
6. Enter `AWAITING_EXTERNAL_PLAN_REVIEW`. This is the complete
   author-written input set the generator hard-requires; a missing or
   stale entry is not a warning, it *withdraws* the bundle
   (`finalize_bundle_generation` quarantines `current/` and deletes the
   archive):
   - do **not** write `<bundle_dir>/PLAN.md` — since `WFR-67` the
     generator derives it, unconditionally, from a private pinned
     snapshot of every plan-stage protected path, and
     `prepare-ai-review.sh` deliberately leaves it out of its
     author-stub list at this stage. Apply plan edits to the
     authoritative plan document (`plan_path`) only; an edit made to the
     bundle copy is silently overwritten;
   - write `<bundle_dir>/CONTEXT_FILES.txt` listing only the docs a
     reviewer genuinely needs beyond the plan itself;
   - write `<bundle_dir>/REVIEW_REQUEST.md` per the format in
     `docs/ai-workflow/REVIEW_PROTOCOL.md` (stage: `plan`), stating this
     round's `review_content_id: <hex>` as a plain labelled line
     (`assert_review_request_states_review_content_id`). Obtain the value
     from the single canonical entry point
     `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Computing
     `review_content_id`" names for this stage -- never a second, ad hoc
     computation, and never a value carried over from a previous round;
   - write `<bundle_dir>/TEST_RESULTS.md` **fresh for this round**,
     opening with the two labelled lines
     `assert_test_results_consistent_with_plan_review_request` requires:
     `stage: plan (revision N)` with `N` equal to this round's
     `plan_revision`, and `head: <sha>` equal to this generation's own
     HEAD. An empty stub (the file `prepare-ai-review.sh` creates when it
     is missing) or a copy carried forward from an earlier round fails
     this check and withdraws the bundle;
   - run `./scripts/prepare-ai-review.sh <base-sha> plan <work_item_id>`
     (`work_item_id` is **required** for the plan stage, never resolved
     from the live `active_work_item_id` -- `D-Fingerprint-Generalization`).
7. **`REJECTED`-bundle refusal, this command's sole assertion, immediately
   preceding the hand-off report** (`WFR-67`): call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here — for a report-only consuming act, this single
   assertion is also the mutation-guard assertion. Report the bundle
   location and **stop**. Do not implement anything. This is a hard gate —
   wait for `<feedback_dir>/REVIEW_FEEDBACK.md`.
