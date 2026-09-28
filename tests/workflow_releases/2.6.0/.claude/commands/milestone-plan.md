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
argument: `feedback/` is stage-agnostic and resolves by the item's durable
`feedback_layout` for every stage alike (`D-Feedback-Layout`,
workflow-2.6.0): `.ai-review/<work_item_id>/feedback/` unconditionally, by
construction, for a `feedback_layout: "scoped"` item (every item created
under workflow-2.6.0); the unchanged legacy scoped-else-flat rule for an
item without the field. `REVIEW_PROTOCOL.md`'s "Bundle location" is the
normative definition.

`<plan_inputs_dir>` below is
`workflow_fingerprint.resolve_plan_review_inputs_dir(repo_root,
work_item_id)` -- `.ai-review/<work_item_id>/plan-inputs/`, a sibling of
`current/` (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0). The plan-stage
author files are written there, **never** into `<bundle_dir>`: the
generator assembles the plan bundle in a staging directory, copies them
into it byte-for-byte, and renames it onto `current/` only once the whole
generation succeeds, so nothing this command writes touches `current/`
until that final rename (`REVIEW_PROTOCOL.md`'s "Bundle location").

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
   - **[2.1] Plan-review entry** (`TWO_STAGE_PLAN_REVIEW_VERSIONS` items
     with an existing `work_items` entry only; `D-Plan-Review-Bundle-Binding`,
     workflow-2.6.0). A fresh milestone with no entry yet skips this bullet
     (it is row 7, `NEEDS_EDIT`, by construction); a `"1"`-governed item
     never runs it. In order, before step 1 and before any other write:
     1. **Row-1 refusal, before any write** (`LPR-R4-002`): call
        `workflow_state.assert_plan_review_entry_phase(work_item,
        work_item_id, command="/milestone-plan")`. A phase that is neither
        ready (`AWAITING_LOCAL_PLAN_REVIEW`,
        `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`, `AWAITING_PLAN_APPROVAL`)
        nor non-ready (`PLANNING`, `REVISING_PLAN`, `AMENDING_PLAN`) refuses
        with `PlanReviewPhaseNotPlanStageError` -- report it and stop;
        `WORKFLOW_STATE.json` is untouched, including step 1's mirror
        advance. The message names `/request-plan-amendment <id>` only at
        `IMPLEMENTING`/`SELF_REVIEWING_IMPLEMENTATION`: the amendment
        mechanism is the sanctioned route back to planning, and `2.5.1`'s
        silent re-entry into plan review from `IMPLEMENTING` is gone.
     2. **Ready-phase withdrawal** (`LPR-R3-001`; `LPR-R4-003`/`-004`/`-006`;
        `LPR-R5-004`). At a ready phase this command withdraws the item
        from review, deciding from the phase alone: it never calls the
        status function first and never computes the fresh plan-stage id,
        so a bumped `(Revision N)` title or a deleted protected path can
        never block this exit. First call
        `workflow_state.assert_plan_review_withdrawal_allowed(repo_root,
        work_item_id, explicit_id=<True only when $ARGUMENTS' first
        argument is a work_items key>)`, which refuses before any write
        with `PlanReviewWithdrawalNeedsExplicitIdError` for the no-argument
        form and the one-argument `<base-sha>` form (neither names the
        target -- report the current phase and what a withdrawal would
        discard, and name `/milestone-plan <id>` as the deliberate form),
        with `PlanApprovalInProgressError` when an open plan-approval
        journal names this item (name `/approve-review plan <id>`'s own
        resume and takeover path), and with
        `PlanApprovalJournalUnavailableError` for an unreadable journal
        (never read as "no transaction in progress"). Then call
        `workflow_state.state_transaction(repo_root, lambda state:
        workflow_state.withdraw_plan_review(state, work_item_id, now))`: it
        moves the phase to `AMENDING_PLAN` when the item's last
        `amendment_history` entry is unresolved, else to `REVISING_PLAN`,
        and writes the `plan_review_binding` record `CONSUMED` from the
        `BOUND` record's own `bound` content (any other record, or none,
        gets the fail-closed legacy marker at the current mirror, so the
        next bind needs one revision advance). It never touches the bundle,
        the pin or `<plan_inputs_dir>`. Report which phase and record it
        left, which phase it entered, that **both** recorded plan-review
        stages are discarded (a later bind of different content reads them
        as absent), and that the withdrawn content is consumed and can
        never re-bind -- only an edit plus regeneration can. At a non-ready
        phase no withdrawal is attempted, so a re-run after a crash that
        followed a withdrawal simply finds the non-ready row.
     3. **Marker, then the status, once** (`LPR-R2-001`, `LPR-R3-006`):
        call `workflow_state.state_transaction(repo_root, lambda state:
        workflow_state.ensure_plan_review_binding_marker(state,
        work_item_id, now))` -- row 5 (`LEGACY_UNMARKED`, a `2.5.1` item in
        `REVISING_PLAN`/`AMENDING_PLAN` with no record) gets the fail-closed
        legacy marker, or, for an open amendment with a recorded
        `approved_review_content_id`, the non-legacy `CONSUMED` record from
        it; otherwise a no-op. Then evaluate
        `workflow_state.plan_review_publication_status(repo_root, state,
        work_item_id)` once, after any withdrawal (the same table
        `python3 scripts/workflow_state.py --plan-review-publication-status
        <id>` prints). `NEEDS_EDIT` (rows 7, 10) and `EDIT_IN_PROGRESS`
        (row 11) take the normal path, steps 1-7. `NEEDS_REVISION` (row 8)
        resumes at step 5's publication point at the registry's revision;
        `PUBLISHED_UNBOUND` (row 9) resumes at step 6 -- regenerate if no
        bundle verifies for the published content, then bind -- and never
        re-advances the revision. `PlanReviewBindingInconsistentError`
        (row 6: a non-ready phase holding a `BOUND` record) is reported and
        stops. This command reads no feedback file, so it runs no feedback
        check in any row; for an `AMENDING_PLAN` item,
        `request_plan_amendment`'s own `CONSUMED` record is the binding.
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
     `docs/ai-workflow/WORKFLOW_STATE.json`. For a
     `TWO_STAGE_PLAN_REVIEW_VERSIONS` item the resume branch runs only at
     `PLANNING`, `REVISING_PLAN` or `AMENDING_PLAN`
     (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0, `LPR-R4-002`): at a
     ready phase it refuses with `ws.PlanReviewInProgressError`, at any
     other phase with `ws.PlanReviewPhaseNotPlanStageError`, before any
     write -- step 0's plan-review entry has already withdrawn a ready
     item or refused a non-plan-stage one, so a sanctioned run never meets
     either. On a **fresh** id,
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
     the digest anyway. This step does **not** publish
     (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0, `LPR-R4-001`): steps
     4 and 5 can still edit the plan document and this declaration, so
     `publish_plan_revision` -- the author's "edits declared complete" act
     -- runs at step 5's publication point, after every protected edit
     this command can make, and still before this revision's bundle is
     ever generated (`D-Plan-Revision-Publication`, `WFR-65`). The
     registry, mapping, `<work_item_id>-artifacts.json` and table written
     here, and the staging step below, stay here so self-review reads a
     complete, index-visible plan.
   - **[2.1]** **Staging step, required before any publish and before any
     bundle is generated** (salvage audit `B6`; `LPR-R3-003`, workflow-2.6.0:
     the publish now computes the fresh plan-stage id, which reads these
     same index-visible paths, so it would refuse with the same error): mark this item's own `plan_path`,
     `registry_path`, `mapping_path` and
     `docs/ai-workflow/registry/<work_item_id>-artifacts.json`
     **intent-to-add** — `git --literal-pathspecs add -N -- <those four
     paths>` (literal, so each names exactly itself) — and leave
     them that way. Do **not** commit them: the plan-approval commit
     `/approve-review plan` creates is what commits all four, together
     with `WORKFLOW_STATE.json` and every other declared protected path,
     as its own member set (`resolve_plan_stage_approval_commit_paths`,
     `D-Plan-Approval-Closure`, workflow-2.6.0).

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
     `workflow_state.assert_plan_approval_index_clean` -- the empty-index
     check `stage_plan_approval_commit_paths` runs first -- provably
     ignores an unstaged intent-to-add marker (`git diff --name-only
     --cached HEAD` does not report one),
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
   - **[2.1]** **Publication point, after step 5 and immediately before
     step 6** (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0, `LPR-R4-001`,
     section 5.3 item 2): the one place this command publishes, after
     every step that can edit a plan-stage protected path (the plan
     document, the registry, the mapping, or `<work_item_id>-artifacts.json`'s
     `plan_stage` sets -- steps 3, 4 and 5). In order:
     1. re-run `workflow_state.generate_registry(...)`/`generate_mapping(...)`/
        `write_registry_and_mapping(...)` at this round's `plan_revision`
        (the value step 1's `route_work_item` set) -- a byte-identical
        no-op when steps 4-5 changed no checkpoint or requirement, required
        when they did;
     2. re-embed `workflow_state.render_registry_markdown(registry)`'s
        output into the plan document, replacing the table already there,
        **unconditionally** (ledger row `I22`);
     3. re-apply step 3's intent-to-add staging step (`git
        --literal-pathspecs add -N`,
        idempotent -- it covers any plan-stage file step 4 created);
     4. call `workflow_state.state_transaction(repo_root, lambda state:
        workflow_state.publish_plan_revision(state, work_item_id,
        plan_revision, now, review_content_id=F))`, where `F` is the fresh
        plan-stage id computed inside that same mutator, after items 1-3,
        through the single canonical entry point
        `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Computing
        `review_content_id`" names for this stage. The publish is
        **mirror-only**: it mirrors `plan_revision` into the state file and
        writes the `PUBLISHED` record, and leaves the phase at `PLANNING`,
        `REVISING_PLAN` or `AMENDING_PLAN` -- `AWAITING_LOCAL_PLAN_REVIEW`
        is written only by step 6's bind. It refuses before any write with
        `ws.ConsumedPlanReviewContentError` when this content is the
        consumed (already reviewed or withdrawn) content -- edit the plan
        first; `ws.LegacyPlanReviewBindingUnknownError` when step 0's
        marker was never written; `ws.PlanReviewInProgressError`/
        `ws.PlanReviewPhaseNotPlanStageError` outside the plan-stage
        allow-list; `ws.PlanReviewBindingInconsistentError` for a `BOUND`
        record at a non-ready phase. Report the refusal and stop.

     `WFR-65`'s ordering (mirror published before any bundle is generated)
     holds because step 6 follows this point. A later edit to a protected
     path, before step 6 binds, makes the bind refuse
     (`ws.PlanReviewNotPublishedError`); re-run this publication point.
6. Enter `AWAITING_EXTERNAL_PLAN_REVIEW` (`"1"`), or -- for a
   `TWO_STAGE_PLAN_REVIEW_VERSIONS` item -- `AWAITING_LOCAL_PLAN_REVIEW`,
   which is written by this step's own **bind** below, never by the
   publish (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0, `LPR-R5-003`).
   This is the complete author-written input set the generator
   hard-requires; a missing or stale entry is not a warning, it fails the
   generation. At the plan stage the generator assembles into a staging
   directory, so a failed generation discards only its own staging
   artifacts: it never withdraws the previous `current/`, its archive or
   its pin, and writes no `REJECTED` marker (the deliberately revised
   plan-stage `WFR-67` semantics, `LPR-R3-002`; `REVIEW_PROTOCOL.md`'s
   "Bundle location"):
   - do **not** write `<bundle_dir>/PLAN.md` — since `WFR-67` the
     generator derives it, unconditionally, from a private pinned
     snapshot of every plan-stage protected path, and
     `prepare-ai-review.sh` deliberately leaves it out of its
     author-stub list at this stage. Apply plan edits to the
     authoritative plan document (`plan_path`) only; an edit made to the
     bundle copy is silently overwritten;
   - write `<plan_inputs_dir>/CONTEXT_FILES.txt` listing only the docs a
     reviewer genuinely needs beyond the plan itself;
   - write `<plan_inputs_dir>/REVIEW_REQUEST.md` per the format in
     `docs/ai-workflow/REVIEW_PROTOCOL.md` (stage: `plan`), stating this
     round's `review_content_id: <hex>` as a plain labelled line
     (`assert_review_request_states_review_content_id`). Obtain the value
     from the single canonical entry point
     `docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Computing
     `review_content_id`" names for this stage -- never a second, ad hoc
     computation, and never a value carried over from a previous round;
   - write `<plan_inputs_dir>/TEST_RESULTS.md` **fresh for this round**,
     opening with the two labelled lines
     `assert_test_results_consistent_with_plan_review_request` requires:
     `stage: plan (revision N)` with `N` equal to this round's
     `plan_revision`, and `head: <sha>` equal to this generation's own
     HEAD. An empty stub (the generator stubs a file missing from both
     `<plan_inputs_dir>` and `current/`) or a copy carried forward from an
     earlier round fails this check and fails the generation;
   - run `./scripts/prepare-ai-review.sh <base-sha> plan <work_item_id>`
     (`work_item_id` is **required** for the plan stage, never resolved
     from the live `active_work_item_id` -- `D-Fingerprint-Generalization`).
     **[2.1] If the generator fails** after the publication point
     (`LPR-R5-001`), the item is left at its non-ready phase with a
     `PUBLISHED` record -- row 9, `PUBLISHED_UNBOUND`, recoverable. Report
     the failure and name the explicit-id re-run, `/milestone-plan
     <work_item_id>`, whose entry resumes at row 9 (regenerate, then bind,
     never re-advancing the revision) -- never a review command, since
     `/review-plan` refuses at a non-ready phase. Stop.
   - **[2.1] Bind, straight after the generator succeeds**
     (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0, section 5.3 item 3):
     call `binding = workflow_state.verify_plan_review_bundle(repo_root,
     work_item_id)`, then `workflow_state.state_transaction(repo_root,
     lambda state: workflow_state.bind_plan_review_bundle(state,
     work_item_id, binding=binding, now=now))` -- the same call
     `/apply-plan-review` step 7' makes, and the **sole** writer of
     `AWAITING_LOCAL_PLAN_REVIEW`: it writes that phase, `current_bundle_id`
     and the `BOUND` record, only for a verified bundle of the published
     content. The verifier refuses by cause with
     `ws.PlanReviewBundleUnverifiedError` (no bundle, a rejected one, a
     stale-revision manifest, a `current/`/manifest/archive disagreement)
     or `ws.ReviewedContentDriftError` (the worktree drifted from what the
     bundle captured); the bind refuses with `ws.PlanReviewNotPublishedError`
     (the content is not the published content -- re-run step 5's
     publication point), `ws.ConsumedPlanReviewContentError`,
     `ws.LegacyPlanReviewBindingUnknownError`,
     `ws.PlanReviewAlreadyReadyError`, `ws.PlanReviewPhaseNotPlanStageError`
     or `ws.PlanReviewBindingInconsistentError`. On any refusal the item
     stays at row 9 (or row 11 after a further edit); report the refusal
     and the same explicit-id re-run, and stop.
7. **`REJECTED`-bundle refusal, this command's sole assertion, immediately
   preceding the hand-off report** (`WFR-67`): call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here — for a report-only consuming act, this single
   assertion is also the mutation-guard assertion. For a
   `TWO_STAGE_PLAN_REVIEW_VERSIONS` item it follows step 6's bind
   (workflow-2.6.0). Report the bundle
   location and **stop** -- for a `TWO_STAGE_PLAN_REVIEW_VERSIONS` item,
   also the phase the bind wrote (`AWAITING_LOCAL_PLAN_REVIEW`), the bound
   `bundle_id` and `review_content_id`, and that `/review-plan
   <work_item_id>` is next. Do not implement anything. This is a hard gate —
   wait for `<feedback_dir>/REVIEW_FEEDBACK.md`, and print that exact
   resolved path (`workflow_fingerprint.resolve_feedback_dir(repo_root,
   work_item_id)`, `D-Feedback-Layout`, workflow-2.6.0) as where the
   reviewer's feedback must land, never a hard-coded flat path.
