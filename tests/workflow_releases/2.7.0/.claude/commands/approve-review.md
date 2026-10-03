---
description: Approve the plan or implementation stage for the active (or named) work item. User-only -- never invoked by Claude autonomously.
argument-hint: <plan|implementation> [work-item-id]
disable-model-invocation: true
state_writer: true
review-subject: bundle
---

**State-writer discipline (D1, item 354):** every `docs/ai-workflow/WORKFLOW_STATE.json` write this command performs -- everywhere a step below says "persist the returned state" -- is performed by calling `workflow_state.state_transaction(repo_root, mutator)`, never by a separate read-then-write: `state_transaction` holds `.ai-review/runtime/WORKFLOW_STATE.lock` (`workflow_state.state_lock`, `fcntl.flock(LOCK_EX)`) across the complete re-read -> apply-the-named-function -> canonical-serialize -> atomic-publish sequence in one process invocation, so `mutator` is the exact transition function each step below names (e.g. `lambda state: workflow_state.<fn>(state, ...)`), applied to freshly re-read state rather than to a snapshot taken before the lock was acquired.

Enter `AWAITING_PLAN_APPROVAL` or `AWAITING_TECHNICAL_APPROVAL`
(`docs/ai-workflow/MILESTONE_WORKFLOW.md`) for the stage named in
`$ARGUMENTS` (`plan` or `implementation`), for the work item also named in
`$ARGUMENTS` or, if omitted, `active_work_item_id`.

`<bundle_dir>`/`<feedback_dir>` below resolve per
`docs/ai-workflow/REVIEW_PROTOCOL.md`'s "Bundle location"
(`workflow_fingerprint.resolve_bundle_dir`/`resolve_feedback_dir`). This
command runs at both stages, so pass the stage it was invoked for:
`resolve_bundle_dir(repo_root, work_item_id, stage="plan")` on the plan
stage, which is always `.ai-review/<work_item_id>/current/`, and
`resolve_bundle_dir(repo_root, work_item_id)` (no stage argument, the
scoped-else-flat compatibility rule) on the technical/implementation
stage, whose bundle may genuinely be on the flat layout when it was
generated without `prepare-ai-review.sh`'s optional `[work-item-id]`
argument. `<feedback_dir>` takes no stage argument at either stage.
`resolve_feedback_dir` decides `<feedback_dir>` by the item's durable
`feedback_layout` (`D-Feedback-Layout`, workflow-2.6.0):
`.ai-review/<work_item_id>/feedback/` unconditionally, by construction, for
a `feedback_layout: "scoped"` item; the unchanged legacy scoped-else-flat
rule for an item without the field.

**This command is user-only by construction.** `disable-model-invocation:
true` is the primary, harness-enforced control (blocks the SlashCommand
tool). Claude must never invoke it on the user's own behalf, including as a
step of another command's execution, independent of that flag.

**User-only guard, mechanism (2)**: refuse to write anything unless the
user's own current-turn message supplies literal confirmation text naming
the exact `work_item_id` and the exact stage (`plan`/`implementation`)
being approved (`workflow_state.validate_user_confirmation`). Never
fabricate, infer, or carry over this text from a prior turn.

**Verified coverage of mechanism (1)** (`docs/ai-workflow/WORKFLOW_V2_PLAN.md`
D2, resolves `OPUS-R6-010`): this installation's own bundled frontmatter
reference documents `disable-model-invocation: true` as blocking only "the
SlashCommand tool" -- it does not name the Skill exposure path at all. The
installed Claude Code changelog separately confirms skill-aware handling of
`disable-model-invocation: true` (a fix for skills with that flag failing
when invoked via `/<skill>` mid-message), suggesting real coverage there
too. A same-session empirical test was inconclusive: this harness's model
tool surface exposes commands to Claude exclusively through the `Skill`
tool (no distinct `SlashCommand` tool is present in the available tool
set), but the skill-routing table is fixed at session start, so a
newly-flagged command reliably returns "Unknown skill" whether or not the
flag actually gates it -- a decisive test requires invoking a flagged
command via `Skill` from a **fresh session** after this file lands. Until
that fresh-session result is recorded, treat mechanism (2) above as the
actually load-bearing control for the Skill exposure path, not mechanism
(1) alone.

0. **Dual-mode branch** (Workflow v2.1, `WF4a-ii`; widened workflow-2.5.0 to
   a third `governing_workflow_version` branch): resolve the target work
   item (named argument, or `active_work_item_id`) and read its
   `governing_workflow_version` from `docs/ai-workflow/WORKFLOW_STATE.json`.
   - **`governing_workflow_version: "1"`**: steps 1-7 execute exactly as
     written.
   - **`governing_workflow_version: "2.1"`**: steps 1-7 execute identically;
     for the plan stage only, step 1's gate-reachability check additionally
     requires `workflow_state.plan_approval_gate_reachable(...)`'s
     `TWO_STAGE_PLAN_REVIEW_VERSIONS` branch (the `plan_review_stages`
     ledger, populated by `/review-plan`/`/record-manual-plan-review`,
     `D-Plan-Review-Stages`) — fully live, and, since workflow-2.5.0, no
     longer inert for this repository's own installation either
     (`workflow-2.5.0` correction, `LOCAL_MODEL_PLAN_REVIEW` round 5): this
     repository now carries two `"2.1"`-governed work items
     (`plan-amendment-mechanism`, `implementation-review-two-stage`) whose
     own plan-stage approvals exercise this branch directly. The prior
     text's claim that this branch was "inert... because this repository's
     own work item is fixed at `"1"`" was true only while
     `workflow-v2-1-core` was this repository's sole tracked work item; it
     no longer is, and this branch is not, and never was, inert in general
     — it governs every `"2.1"`-governed work item anywhere this Workflow
     is installed. **The plan stage is approvable only at
     `AWAITING_PLAN_APPROVAL`** (workflow-2.6.0, implementation review
     round 1): refuse at step 1, naming the actual phase, for any other
     phase. The ledger check alone does not decide it -- content that was
     dual-approved, then withdrawn, displaced from the single `consumed`
     slot and restored can re-bind at `AWAITING_LOCAL_PLAN_REVIEW` while
     the content-keyed ledger still reads its two `APPROVE`s -- and
     `apply_plan_approval` itself refuses such an item with
     `PlanApprovalPhaseError`, before any journal is opened.
   - **`governing_workflow_version: "2.2"`** (workflow-2.5.0,
     `D-Implementation-Review-Version-Activation`): steps 1-7 execute
     identically to the `"2.1"` branch immediately above for the **plan**
     stage — the two-stage plan-review protocol is unaffected by this
     version bump, since `TWO_STAGE_PLAN_REVIEW_VERSIONS` already covers
     both `"2.1"`/`"2.2"`. For the **implementation** stage only, step 1's
     gate-reachability check additionally applies
     `workflow_state.technical_approval_gate_reachable(...)`'s own
     `"2.2"`-only ledger check, active exactly when this item's own
     `governing_workflow_version` is `"2.2"` — mirroring the plan stage's
     existing `plan_review_stages` check exactly, substituted for the
     implementation-stage ledger (both `LOCAL_MODEL_IMPLEMENTATION_REVIEW`/
     `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` required `APPROVE` against the
     current `review_content_id`, `D-Implementation-Review-Stages`). Step
     1 passes this item's real `governing_workflow_version`,
     `implementation_review_stages`, and the current implementation-stage
     `review_content_id` to that call **unconditionally, for every
     governing version, not only when it is `"2.2"`** — these three are
     required parameters on that function now (round 7's `I1`), so this
     command must always state the work item's actual values rather than
     branch on version to decide whether to pass them at all; the
     function's own internal `!= "2.2"` check is what keeps a `"1"`/`"2.1"`
     item's outcome unchanged.
   - **Any other `governing_workflow_version`** (including one this module
     does not recognize): refuse cleanly, naming the actual value — never
     guess which branch above applies (round-7 optional finding 2, the same
     explicit-refusal discipline `/milestone-implement`'s own step 0
     states).
1. **Confirm gate reachability**: read `<feedback_dir>/REVIEW_FEEDBACK.md`'s
   most recently reviewed round status and bundle ID
   (`workflow_fingerprint.parse_review_feedback_binding_fields`) —
   missing/mismatched `Reviewed bundle ID:`/`Reviewed base commit:`/
   `Work item:` fields are not fatal to reading the file (an
   `EXTERNAL_APPROVE` basis simply becomes unreachable, per step 3), but
   report the mismatch naming both values (`WFR-03`).
   **Implementation stage only — durable `BLOCK`-verdict pin, defense in
   depth** (`D2a`, `WF8c` item (a)): recompute the current implementation-
   stage `bundle_id` now (the same recomputation step 2 repeats and
   displays; reuse this value through steps 1-3 of this invocation rather
   than recomputing it a third time). If the feedback's `status` is
   exactly `BLOCK`: call `workflow_state.state_transaction(repo_root,
   lambda state: workflow_state.record_technical_review_block_pin(state,
   work_item_id, bundle_id=<the current bundle_id>,
   review_content_id=<the current implementation-stage review_content_id>,
   now=<now>))` and persist the returned state — idempotent, exactly as
   `/apply-implementation-review` step 1's own writer call, present here
   only as a second, independent recording path in case that command's
   own write was never reached. The pin write stays here, ahead of the
   gate check below (`LPR-R2-008`): the gate wrapper computes `pinned =
   workflow_state.is_technical_review_block_pinned(work_item, <the current
   bundle_id>)` on the state **re-read after the call above**, so a pin
   just recorded is already reflected, and passes it as `pinned_block` to
   `technical_approval_gate_reachable`; pass the wrapper's
   `inputs["pinned_block"]` to `resolve_approval_basis` in step 3. Plan
   stage: `pinned_block` is never computed or passed — D2a is an
   implementation-stage-only mechanism.
   **The gate check is one call** (workflow-2.7.0, `D-OP-Next`,
   `LPR-R1-003`): on that re-read state, call
   `workflow_state.plan_approval_gate_status(repo_root, state,
   work_item_id)` for the plan stage, or
   `workflow_state.technical_approval_gate_status(repo_root, state,
   work_item_id)` for the implementation stage -- the same read-only
   wrappers the orchestration protocol's `next-action` reads, so the
   command and the catalogue cannot disagree about the gate's inputs. Each
   returns `{reachable, cause, inputs}`: it runs step 2's generation check
   first, then, at the plan stage of a `"2.1"`/`"2.2"` item, step 2's
   bundle-bound check, and at the implementation stage and the `"1"` plan
   stage the computation of the current `bundle_id`, and then computes the
   inputs below and calls the pure predicate. The wrapper calls
   `workflow_state.approval_gate_reachable(status)` for the plan stage on a
   `"1"` item (`plan_approval_gate_reachable(...)` on a `"2.1"`/`"2.2"`
   item — both governed by `TWO_STAGE_PLAN_REVIEW_VERSIONS`), or
   `workflow_state.technical_approval_gate_reachable(..., pinned_block=pinned,
   governing_workflow_version=work_item.get("governing_workflow_version"),
   implementation_review_stages=work_item.get("implementation_review_stages"),
   current_review_content_id=<the current implementation-stage
   review_content_id, already recomputed above>)` for the implementation
   stage, **at every governing version alike, unconditionally** — never only
   on a `"2.2"` branch (workflow-2.5.0 REVISE round 7's own `I1`: these
   three are required keyword-only parameters on this function now, exactly
   like `plan_approval_gate_reachable`'s own equivalents always have been;
   omitting any of them raises `TypeError` instead of silently defaulting,
   so a caller can no longer forget `governing_workflow_version` for a
   `"2.2"` item and have the ledger check silently skipped. The function's
   own `!= "2.2"` branch is what keeps this behavior-preserving for a
   `"1"`/`"2.1"` item — passing its real `governing_workflow_version` and
   `implementation_review_stages` here changes nothing for it, since both
   values are already computed at this point in the step regardless of
   version). The latter's `protected_path_dirty` argument is
   `workflow_state.any_protected_path_dirty(...)` (`WF4a-iii`), called with
   the implementation-stage classification
   (`workflow_fingerprint.load_implementation_stage_classification(...)`) —
   `WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json` dirtiness never blocks this,
   by construction of that classification. Its
   `head_matches_reviewed_implementation_head` argument (`WF4c`,
   D-Approval-Commits, revised `WF8B-003`) is **never** a bare
   `work_item["reviewed_implementation_head"] == <live HEAD SHA>` equality
   — that bare form was proven permanently self-invalidating (a mandatory
   post-generation durability commit is always one commit ahead of the
   value it just wrote, re-breaking the equality on every round; see
   `docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s "WF8b finding disposition
   (revision 27 → 28)"). The wrapper calls
   `workflow_state.implementation_provenance_interval_reachable(repo_root,
   work_item, base_commit)`, which is `True` exactly when
   `workflow_state.verify_implementation_provenance_interval(...)` finds a
   valid interval: live HEAD is exactly the discovered current
   `Workflow-Bundle-Generation-Record: <work_item_id>/<implementation_revision>`
   commit `T` (never merely a descendant of it), `reviewed_implementation_head`
   is reachable from `T` via `T`'s own first-parent chain, every commit
   strictly between them either classifies implementation-stage
   excluded-only or, if it is itself a historical
   `Workflow-Bundle-Generation-Record` commit for this pair (a prior
   same-content-republication link, `WF8c` (c)), satisfies its own
   role-specific contract and chain-continuity instead, and `T` itself
   passes its own role-specific commit contract — the ordinary role
   (touches only `WORKFLOW_STATE.json`, changes only the five allowed
   fields, carries exactly the two-trailer ordinary set) or the
   **recovered** role (`WF8c` (c): touches only `WORKFLOW_STATE.json`,
   changes only `phase`/`state_revision`/`last_transition` —
   `reviewed_implementation_head`/`implementation_revision` byte-identical
   to its parent — carries exactly the three-trailer
   `Workflow-Bundle-Generation-Record` + `Workflow-Work-Item` +
   `Workflow-Supersedes` set, and its own `Workflow-Supersedes` trailer
   names exactly the immediately preceding generation-record commit in the
   chain). On the `implementation_provenance_stale` cause, call
   `verify_implementation_provenance_interval` directly and report its raised exception's message — it names the
   concrete reason (no record commit found, a further unrecorded commit
   landed past `T`, `reviewed_implementation_head` not an ancestor, a
   merge/non-first-parent interval, a protected path inside the interval,
   a malformed record commit, or a broken supersession chain — a fork, a
   cycle, a skipped link, or a non-terminal member failing its own role
   contract) rather than a bare boolean. A `None`
   `reviewed_implementation_head` (nothing has ever generated a bundle for
   this work item) is caught the same way, by
   `BundleGenerationRecordNotFoundError`. `reachable: false` stops here —
   report the wrapper's `cause`, the first failing input in its order:
   `bundle_generation_mismatch` (the generation check), then
   `plan_review_bundle_unbound` (the two-stage plan stage's bundle-bound
   check) or `bundle_unverified` (the current bundle cannot be hashed,
   `MissingRequiredBundleFileError`), then the predicate's own order --
   `review_block_pinned`, `no_review_round` (no `REVIEW_FEEDBACK.md`),
   `review_blocked` (a status other than `REVISE`/`APPROVE`, including a
   `BLOCK`), `protected_path_dirty`, `implementation_provenance_stale`,
   `review_ledger_stale` -- and do not proceed. **This order is a
   deliberate change from 2.6.0** (`LPR-R3-004`), which ran the predicate
   before step 2's checks: a predicate computed for a bundle generated at
   another HEAD or in another worktree says nothing about this checkout,
   so its remedy comes first. A pinned `BLOCK` with HEAD moved past
   `generation_head` therefore reports `bundle_generation_mismatch` (the
   remedy, for an excluded-only commit, is
   `/recover-implementation-provenance <id>`), and after that recovery
   `review_block_pinned`. The command refuses in both orders; only which
   refusal is named first changed.
   *The recovered-role commit shape and its supersession-chain validation
   are implemented (`WF8c` (c), reachable via `/apply-implementation-review`'s
   and `/apply-functional-review`'s own `resolve_bundle_generation_outcome`-
   driven `record_bundle_generation` call) and, separately, via the
   dedicated `/recover-implementation-provenance` command (`WF8c` (b)),
   invocable only from `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` itself,
   for a bundle staled by a concurrent excluded-only commit landing after
   `T` while no new implementation round has started — the only phase from
   which `record_bundle_generation`'s own legal source phases are
   unreachable without first re-entering either kind of remediation cycle.
   `record_bundle_generation`'s legality is stage-specific
   (`workflow-v2-3-followups` continued scope,
   `BUNDLE_GENERATION_LEGAL_SOURCE_PHASES_BY_STAGE`), not a flat two-phase
   set: `stage="implementation"` only from `SELF_REVIEWING_IMPLEMENTATION`;
   `stage="post-fix"` from `APPLYING_REVIEW_FEEDBACK` (an ordinary
   implementation-review REVISE round) or from `AWAITING_FUNCTIONAL_REVIEW`
   (`/apply-functional-review`'s own bounded-fix branch) — the latter only
   when `technical_approval.status == "STALE"`
   (`BundleGenerationRequiresStaleTechnicalApprovalError` otherwise). This
   third, functional-review source weakens none of this command's own
   stale-bundle/approval guards: the `STALE` gate means a `CURRENT`
   approval can never take this path at all; `/apply-functional-review`
   step 4 still re-checks the `REJECTED`-bundle marker (`WFR-67`) before
   generating, the same as the ordinary post-fix path; and
   `resolve_bundle_generation_outcome` still independently re-derives the
   outcome from real Git content, never merely trusting which phase the
   caller arrived from.*
2. **Recompute fresh**: `bundle_id` over the current bundle and the
   stage-appropriate `review_content_id` (`scripts/workflow_fingerprint.py`)
   over the working tree. Display both, and the protected/excluded path
   lists, to the user. **Local worktree/HEAD staleness check**
   (`D-Bundle-Manifest`, resolves `OPUS-R6-016`): this command runs inside
   a real, current worktree, so step 1's gate wrapper calls
   `workflow_fingerprint.assert_local_generation_matches(repo_root,
   <bundle_dir>/MANIFEST.md)` as its first check, and a
   `WorktreeOrHeadMismatchError` has already stopped the command there as
   `bundle_generation_mismatch`, naming both the recorded and current
   worktree_root/HEAD (the wrapper's `inputs["generation_check"]`) — this
   is the actual first-party Milestone-8 incident (a stale bundle read
   from a different worktree). Never skip this because the recomputed
   `bundle_id` happens to still match; the two checks catch different
   failure modes. **`REJECTED`-bundle refusal, first of two, both stages**
   (`WFR-67`): also call
   `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
   work_item_id)` here; a `BundleRejectedError` stops the command, naming
   the marker path and its recorded detail.
   **Plan stage, `TWO_STAGE_PLAN_REVIEW_VERSIONS` items only — bundle-bound
   check** (`D-Plan-Review-Bundle-Binding`, workflow-2.6.0, section 5.3
   item 4): step 1's plan gate wrapper calls
   `workflow_state.assert_plan_review_bundle_bound(repo_root,
   work_item_id)` right after the generation check, which re-runs the
   plan bundle verifier and requires a
   `BOUND` `plan_review_binding` record for exactly the bundle's
   `review_content_id` (a `2.5.1` item at `AWAITING_PLAN_APPROVAL` with no
   record is accepted when its bundle verifies; nothing is written).
   Report its returned advisory (the wrapper's
   `inputs["bundle_bound_advisory"]`), if any: a `bundle_id` differing from
   `current_bundle_id` -- a wrapper-only regeneration after the bind -- is
   advisory only and never blocks the approval. A refusal stopped the
   command at step 1 as `plan_review_bundle_unbound`; report the error's
   message (`inputs["bundle_bound"]`), which names the remedy:
   `ReviewedContentDriftError` (row 4a: restore the bound bytes from
   `<bundle_dir>/files/<path>`, or withdraw with `/milestone-plan <id>`),
   `PlanReviewBundleUnverifiedError` (rows 4b/4c: regenerate, or
   withdraw), `PlanReviewBindingInconsistentError` (row 4d: withdraw with
   `/milestone-plan <id>`). A `"1"` item and the implementation stage are
   unchanged.
3. **Resolve the basis**: call `workflow_state.resolve_approval_basis(...)`
   with the feedback round's status/bundle_id, the freshly recomputed
   current bundle_id, this turn's literal `user_confirmation` text (if the
   user has not supplied it this turn, ask for it naming the exact
   `work_item_id` and stage, then stop and wait — never guess it),
   `work_item_id`, `stage`, and — implementation stage only — step 1's
   `pinned_block` (`D2a`, `WF8c` item (a)): the same positive-membership
   fact already passed to `technical_approval_gate_reachable`, so a
   pinned bundle refuses through this call too even if some other path
   ever reached step 3 without step 1's own gate check. Plan stage: never
   passed (D2a is implementation-stage-only). `BlockCannotApproveError`/
   `UserConfirmationRejectedError` stop the command; report the concrete
   reason.
4. **Build the record**: `workflow_state.build_approval_record(...)`, with
   `reviewed_content_commit` left unset for the plan stage (permanently
   null, `D-Approval-Commits`/`GPT-R9-006`) and set to the current
   `reviewed_implementation_head` for the implementation stage.
   **`review_content_manifest`, exact source** (self-discovered during
   this item's own `/accept-milestone` pre-flight, closed as continued
   `workflow-v2-3-followups` scope): whichever
   `workflow_fingerprint.compute_review_content_id_plan_stage[_at_commit[_for_work_item]]`/
   `compute_review_content_id_implementation_stage[_at_commit]` variant
   step 2 used to recompute `review_content_id` returns `(digest,
   projection)` — `build_approval_record`'s `review_content_manifest`
   argument is `projection["review_content_manifest"]`, the projection's
   own inner flat list of `{path, exists, mode, blob}` entries, **never**
   `projection` itself, which carries a field of the identical name one
   level up. Passing the whole projection now raises
   `InvalidApprovalRecordError` (`validate_approval_record`'s own shape
   check, added to close this exact trap after it silently produced two
   independently-malformed approval records — plan and technical — for
   this same work item).
4a. **Plan stage only — resolve the complete commit member set, before any
    durable mutation** (`D-Approval-Commits`' "Conditional fifth commit
    member", `GPT-R67-001`; generalized beyond `workflow-v2-1-core`'s own
    case; widened to the declared protected set plus removals by
    `D-Plan-Approval-Closure`, workflow-2.6.0). **Interim scope guard, `workflow-v2-1-core` only** (`WF8c`
    items 347/352's own retirement condition, stated as a condition
    rather than an open question): before anything else, if
    `work_item_id == "workflow-v2-1-core"` and `stage == "plan"`, read
    `docs/ai-workflow/WORKFLOW_V2_PLAN.md` and check whether it still
    contains the heading text `Bootstrap plan-approval procedure`. If it
    does, **stop**: report that `workflow-v2-1-core`'s own plan-stage
    approvals still go through `/bootstrap-workflow-v2`'s own checklist,
    not this command, until that section is withdrawn from
    `D-Approval-Commits` (gated on a technical approval covering this
    work item's own content, per the reconciliation table's `347-353`
    row) — do not attempt steps 4b onward. Every other work item, and
    `workflow-v2-1-core` itself once that heading is withdrawn, proceeds
    normally below.

    Call `plan =
    workflow_state.resolve_fresh_plan_approval_members(repo_root,
    work_item_id, state_path=Path("docs/ai-workflow/WORKFLOW_STATE.json"))`
    (`plan` — referenced by that name in every step below;
    `D-Plan-Approval-Closure`, workflow-2.6.0). It runs, read-only and in
    order:
    - **the empty-index precondition** — `git diff --no-renames
      --name-only -z --cached HEAD` must be empty. `DirtyIndexBeforeStagingError` names the staged
      paths and the usual cause, a staged `git mv` of a protected path:
      unstage both sides (`git --literal-pathspecs restore --staged -- <old>
      <new>`, with `GIT_GLOB_PATHSPECS`/`GIT_NOGLOB_PATHSPECS`/
      `GIT_ICASE_PATHSPECS` unset -- Git refuses `--literal-pathspecs`
      combined with any of them), keep the
      rename in the working tree, and re-run — the approval commit stages
      the removal and the addition itself;
    - **the member set**
      (`workflow_fingerprint.resolve_plan_stage_approval_commit_paths`):
      every declared `plan_stage.protected_paths` entry of the worktree
      declaration (the plan doc, registry JSON and mapping file always
      among them), `WORKFLOW_STATE.json`, this work item's own
      `<work_item_id>-artifacts.json` when it is both pending (its
      working-tree bytes differ from `HEAD`) and fresh (byte-identical to
      the copy the bundle captured), and the **removals** — every path
      protected under `HEAD`'s committed declaration and tracked at `HEAD`
      that is absent from both the current declaration and the worktree,
      staged as a deletion. A rename is a removal plus an addition. A path
      the current declaration no longer protects but that is still in the
      worktree is not a member: no deletion is staged for it, and the
      classification gates govern it from then on. A first approval (no
      declaration at `HEAD`) has no removals;
    - **freshness per member kind**, against the bound bundle
      (`<bundle_dir>`, which step 2 has already verified; never
      `.ai-review/<id>/.pin`): a protected member's worktree bytes must
      equal the bundle's `files/<path>` capture when one exists, otherwise
      its blob at the bundle's own `MANIFEST.md` `base_commit`, and with
      neither it refuses; a removal member must be neither captured nor
      listed in the bundle's `## Protected paths`; the artifacts
      declaration keeps its pending-and-fresh rule above;
      `WORKFLOW_STATE.json` is not compared.

    Any member failure is `ReviewedContentDriftError`, naming the path and
    the member kind (a stale declaration's
    `StaleArtifactsDeclarationError` is chained under it) — stop and report
    it; do not stage, do not commit, do not open a transaction. Most
    protected-member edits are already refused at step 2 (they move the
    fresh plan-stage id); this check is what refuses a removal member the
    bound bundle still captured and an artifacts-declaration byte edit
    outside its hashed key sets, and it stays defense in depth for the
    rest. `MissingWorkItemArtifactsDeclarationError` here means this work
    item's own declaration is genuinely absent from the working tree, not
    merely uncommitted — the same fail-closed error step (2)'s
    recomputation already raises for that case, never masked.
    `fifth_member_path = plan.artifacts_declaration_path` (`None` if the
    declaration is not a member) and `pinned_sha256 =
    plan.artifacts_declaration_sha256` — referenced by those names below
    for later re-verification. Implementation stage:
    unchanged, no resolution step — its four members are fixed, and none
    of steps 4b onward below apply to it; it keeps its own pre-`WF8c`
    write/commit/verify shape exactly as before, resuming at step 6's
    "Implementation stage" bullet.
4b. **Plan stage only — the plan-approval transaction precondition**
    (`WF8c` item 352, ownership-aware and guard-aware, mirroring
    `/bootstrap-workflow-v2`'s own step 0): call `evidence =
    workflow_state.plan_approval_takeover_evidence(repo_root)`. A
    non-`None` `evidence["journal"]` means a transaction from a prior,
    interrupted invocation of this command is already open; this fresh
    invocation never holds its `owner_token`. Report
    `evidence["owner_token"]`, `evidence["outcome"]`, `evidence["progress"]`'s
    last completed step (or that none is recorded), and
    `evidence["guard"]`'s state, and **stop** — do not open a second
    journal (`open_plan_approval_journal` would refuse this anyway,
    `PlanApprovalTransactionInProgressError`, but reporting first gives
    the user the exact recovery literal rather than a bare exception).
    **Target check, before the recovery below**: the journal is a single,
    repository-wide object, so the open transaction it describes may
    belong to a work item that is *not* this invocation's resolved
    target. Report `evidence["journal"]["work_item_id"]` alongside the
    values above, and when it differs from this invocation's own resolved
    `work_item_id`, say so plainly and tell the user to re-run targeting
    that work item — never take over, complete, or roll back another work
    item's approval under this one's invocation.
    `take_over_plan_approval_transaction` takes the resolved
    `work_item_id` as a **required** argument and refuses a mismatch with
    `PlanApprovalTakeoverWorkItemMismatchError`, having mutated nothing,
    so this is a fail-closed check in production rather than a rule this
    step is trusted to remember.

    Recovery: the user supplies the literal takeover authorization
    `workflow_state.plan_approval_takeover_authorization_literal(evidence)`
    (plus, if `evidence["guard"]` is held, the release literal
    `workflow_state.plan_approval_guard_release_authorization_literal(...)`)
    on a subsequent turn; only then call
    `workflow_state.take_over_plan_approval_transaction(repo_root,
    work_item_id=<this invocation's resolved target>, ...)` to obtain a
    fresh `owner_token`, then resume forward-completion or rollback per
    `evidence["outcome"]` at step 6a below using that token, skipping
    straight past steps 4c-6 (a taken-over transaction already has its
    journal open; never open a second one for it). A `None`
    `evidence["journal"]` means no transaction is open — proceed to 4c.
    This takeover flow is unchanged by workflow-2.6.0's
    `D-Repo-Global-Lifecycle`, and it is why a taken-over transaction never
    reaches step 4d or first-commit staging: it resumes at 6a, and from
    there reaches only 6a1, 6b or 6c. A reservation made at 4d before the
    takeover stays live, because the journal keeps the old token in
    `previous_owner_tokens`.

    **Every entry on an item with an open amendment, and the one lifecycle
    call it makes** (workflow-2.6.0, `D-Repo-Global-Lifecycle`; an open
    amendment is a non-empty `amendment_history` whose last entry's
    `resolved_at_plan_revision` is still `null`). Nothing else in this
    command touches the amendment witness, and on an item with no open
    amendment every row's lifecycle call is a no-op:

    | Entry | How reached | Lifecycle call on an open-amendment item |
    | --- | --- | --- |
    | 4b | fresh invocation, journal open | none; reports and stops |
    | 4c | fresh invocation, no journal | none; opens the journal |
    | 4d | directly after 4c, same invocation only | the reservation (`OPEN` → `RESOLVING`) |
    | 5, 6.x | directly after 4d, same invocation only | staging in `first_commit` mode, asserting the 4d reservation |
    | 6a | in session after 6.x, or after a 4b takeover | none; classifies |
    | 6a1 | 6a `COMMITTED` plus `TREE_CONTENT`, in session or taken over | the held check, then staging in `amend_recovery` mode |
    | 6b | 6a `NOT_COMMITTED`, or a failure in 4d through 6.3a | capture `journal_tokens`, roll back, then the release |
    | 6c → 6c1 → 6d | 6a (or 6a1) verified `COMMITTED` | the advance, with the journal and the verified commit, then close the journal |
    | 6a `AMBIGUOUS` | any | none; stops |

    The multi-invocation journal never holds the repository-global
    lifecycle lock (primitive 9). The reservation is the `RESOLVING`
    witness itself, a durable record. Each lifecycle call takes (9) alone,
    for one short acquisition, outside every
    `plan_approval_guarded_mutation` window, and refuses inside one
    (`LifecycleLockOrderError`).
4c. **Plan stage only — open the failure-atomicity transaction**
    (`WF8c` items 347/349, the durable crash-resumable journal, this
    invocation's own first durable mutation, called before any Git
    staging and before any `WORKFLOW_STATE.json` write of any kind).
    `REJECTED`-bundle refusal, second of two, both stages (`WFR-67`):
    first re-call `workflow_fingerprint.assert_bundle_not_rejected(repo_root,
    work_item_id)` — a withdrawal landing between step 2 and here must
    still be caught. Read `docs/ai-workflow/WORKFLOW_STATE.json`'s
    current working-tree bytes fresh and parse them (`pre_state`). Build
    the record via step 4 above (`record`, `approval_now = <now>`) if not
    already built.

    **workflow-2.4.0, D-Plan-Amendment-4 — reconciliation inputs, read here
    and only here.** Call `metadata =
    workflow_fingerprint.resolve_plan_stage_metadata(repo_root,
    work_item_id)` -- one additional, cheap, idempotent resolver call, not
    a value inherited from an earlier step -- and read `metadata.plan_path`'s
    current working-tree bytes as `post_plan_text` and
    `metadata.registry_path`'s current working-tree bytes, parsed as JSON,
    as `post_registry`: the exact bytes the two-stage review just approved.
    When `pre_state`'s own `work_items[work_item_id]` has an open amendment
    (`amendment_history` non-empty and its last entry's
    `resolved_at_plan_revision` still `null`), also call `pre_plan_text,
    pre_registry = workflow_state.load_pre_amendment_snapshot(repo_root,
    work_item_id, metadata.plan_path, metadata.registry_path,
    amendment_history[-1])` -- never a re-read of the live
    `plan_path`/`registry_path`, which by this point in `AMENDING_PLAN`'s
    lifecycle already hold the *post*-amendment content. No open amendment:
    `pre_plan_text`/`pre_registry` stay `None`. All four values are read
    exactly once, at this call site, in this order, and forwarded verbatim
    into `open_plan_approval_journal` below -- never re-derived, and never
    consulted by this step itself.

    Then call
    `journal = workflow_state.open_plan_approval_journal(repo_root,
    work_item_id=work_item_id, base_commit=base_commit, pre_state=pre_state,
    record=record, approval_now=approval_now, expected_bundle_id=<step 2's
    bundle_id>, expected_review_content_id=<step 2's review_content_id>,
    applicable_paths=plan.paths, removal_paths=plan.removal_paths,
    fifth_member_applies=<step 4a's resolved
    fifth member is not None>, fifth_member_sha256=<step 4a's pinned
    sha256 or None>, user_confirmation=<this turn's literal confirmation
    text>, quiescence_authorization="not required at the permanent site:
    steps 6.2/6c's own fresh whole-file compare-and-swap
    (plan_approval_state_matches_pre_transaction) closes the concurrent-
    writer race the bootstrap's quiescence window instead bridges by
    convention", pre_registry=pre_registry, pre_plan_text=pre_plan_text,
    post_registry=post_registry, post_plan_text=post_plan_text)`.
    `owner_token = journal["owner_token"]`. Unlike the
    now-superseded prior revision of this step, **no**
    `docs/ai-workflow/WORKFLOW_STATE.json` working-tree write happens
    here or anywhere below — the transaction's expected post-approval
    bytes were already computed, once, pinned inside `journal` itself,
    and are never re-derived. (This is the one documented exception to
    this file's own top-of-file "State-writer discipline" paragraph: that
    paragraph's `state_transaction`/`state_lock` primitive protects the
    *ordinary* read-modify-write writers `D1`/item 354 enumerate; this
    specific write is durability-critical enough that `WFR-63` gives it
    its own dedicated, journal-backed transaction instead, verified by
    the freshness re-checks steps 6.2/6c run immediately before ever
    touching the file.)
4d. **Plan stage only — reserve the amendment's resolution** (workflow-2.6.0,
    `D-Repo-Global-Lifecycle`, INV-10; directly after 4c, in the invocation
    that opened the journal, and only there): call
    `workflow_state.reserve_amendment_resolution(repo_root, work_item_id,
    journal, now=<now>)`. It returns `None`, doing nothing, for an item with
    no open amendment in the journal's pinned pre-state. Otherwise it takes
    the lifecycle lock (9) alone, outside every guarded window, runs the
    mixed-release lag probe and the witness predicate list as the
    resolution side, and requires this worktree's working-tree
    `amendment_history` to end in the witness's own unresolved seq N and the
    journal's pinned post-state to resolve that same entry. It then
    rewrites the `OPEN` witness to `RESOLVING`, carrying the reservation
    (this journal's `owner_token`, this worktree, its branch, the pinned
    `approved_review_content_id` and resolution digest). From then on, and
    before any commit exists, no second worktree can begin an approval
    commit for the same sequence. Every refusal happens before anything is
    staged: run step 6b's rollback (`NOT_COMMITTED`; its release is a
    no-op), then stop and report the exception's own message and
    `exc.evidence`:
    - `AmendmentResolutionReservedError`: another open transaction reserved
      this sequence. It names the resolver worktree, its branch, the reserved
      `approved_review_content_id` and `reserved_at`. Wait for it. If it was
      abandoned, use the evidence-bound literal the error offers
      (`clear amendment resolution <wi> <sha256>`, which
      `workflow_state.clear_amendment_resolution` accepts);
    - `StaleLifecycleStateError`: the amendment is already resolved in this
      repository and this worktree's `HEAD` does not show it. Merge the
      resolved amendment first. This refuses an *identical* amended plan
      too, deliberately: an identical approval would still be a second
      approval commit;
    - `AmendmentResolutionConflictError`: a different resolution of the
      same sequence is committed somewhere. Discard the divergent approval
      and merge the recorded one. It is never collapsed and never offered a
      literal;
    - `LaggingWorktreeAmendmentError`, `AmendmentBootstrapConflictError`,
      `AmendmentWitnessUnavailableError`, `LifecycleStateUnreadableError`:
      the mixed-release, upgrade-bootstrap and fail-closed refusals
      `request-plan-amendment.md` step 1 describes.
5. **Plan stage only — stage every non-state approval member, then pin
   the fifth, if resolved** (guarded, ordinary,
   `step="step-5-stage-and-pin"`): inside
   `with workflow_state.plan_approval_guarded_mutation(repo_root,
   owner_token=owner_token, step="step-5-stage-and-pin", now=<now>):`,
   **first** call
   `workflow_state.assert_plan_approval_member_set_unchanged(repo_root,
   journal)` — step 4a's resolution and freshness re-run against the tree
   about to be staged, required to equal the journal's pinned members and
   removals (`PlanApprovalMemberSetChangedError`, or any refusal step 4a
   names). Then stage through the one staging entry,
   `workflow_state.stage_plan_approval_members(repo_root, journal,
   mode=workflow_state.PLAN_APPROVAL_STAGING_FIRST_COMMIT)`
   (workflow-2.6.0, `D-Repo-Global-Lifecycle`'s "No bypass"). On an item
   with an open amendment it first checks, reading the witness without
   taking any lock, that this journal's own 4d reservation is recorded
   (`AmendmentResolutionHeldError` otherwise, before anything is staged).
   It then stages every member of `journal["applicable_paths"]` except
   `docs/ai-workflow/WORKFLOW_STATE.json` — the declared protected paths,
   the fifth member if step 4a resolved one, and the removals — in **one**
   `workflow_state.stage_plan_approval_commit_paths` call, which stages a
   member absent from the worktree (a removal) as a deletion. It never
   uses `git add -A`/`git add .`, and never makes two separate calls. When
   the journal pins a fifth member, the same entry then, inside this same
   window, verifies its staged blob against the pinned sha256
   (`workflow_state.verify_staged_blob_sha256`), closing the race window
   between resolution and staging before progress advances.
   `AmendmentResolutionHeldError`/`StagedBlobMismatchError`/
   `DirtyIndexBeforeStagingError`/`UnexpectedStagedPathSetError`/
   `PlanApprovalMemberSetChangedError`/`ReviewedContentDriftError` inside
   this window: let the exception propagate out of the `with` block (the
   guard still releases via its own `finally`, without advancing
   progress) straight to step 6b's rollback.
   Implementation stage: not applicable.
6. **Plan stage only — create the approval commit**, two further
   guarded sub-steps plus one unguarded structural assertion:
   - **6.2 the state-pin compare-and-swap and pin** (guarded, ordinary,
     `step="step-6.1b-state-pin"`): **first**, `WF8c` item 348(gg)'s
     sub-step 6.1a compare-and-swap — call
     `workflow_state.plan_approval_state_matches_pre_transaction(repo_root,
     journal["pre_procedure_state_sha256"])`. `False` means
     `docs/ai-workflow/WORKFLOW_STATE.json`'s working-tree bytes have
     changed since the journal captured them (a legitimate concurrent
     `state_transaction` write, most likely) — raise inside the `with`
     block (never commit a post-state derived from superseded bytes) so
     the guard releases without advancing progress, then run step 6b's
     rollback and stop, reporting that the transaction was opened
     against now-stale state and must be retried fresh. `True`: call
     `blob_sha = workflow_state.pin_plan_approval_state_blob(repo_root,
     base64.b64decode(journal["expected_post_state_b64"]))` then
     `workflow_state.verify_staged_plan_approval_state_blob(repo_root,
     journal["expected_post_state_sha256"])` to close the race window
     between the pin and the commit.
   - **6.3 staged-set assertion** (unguarded, read-only): call
     `workflow_state.assert_staged_path_set_within(repo_root,
     journal["applicable_paths"])` — the complete staged diff, read
     NUL-delimited so a non-ASCII member compares as itself rather than
     as `core.quotePath`'s quoted display form, must name no path outside
     the members — a subset assertion, since a member byte-identical to
     `HEAD` legitimately produces no diff entry.
     `UnexpectedStagedPathSetError`: run step 6b's rollback and stop,
     naming the path, rather than let a pathspec-free commit absorb it
     silently.
   - **6.3a pre-commit closure proof** (unguarded, read-only;
     `D-Plan-Approval-Closure`, workflow-2.6.0): call
     `workflow_state.prove_plan_approval_index_closure(repo_root,
     journal)` — it writes the staged index as a tree (`git write-tree`),
     recomputes the plan-stage `review_content_id` against that tree and
     requires `journal["expected_review_content_id"]`, and requires every
     journal removal to be absent from it. `PlanApprovalClosureProofError`:
     no commit exists yet, so run step 6b's rollback (`NOT_COMMITTED`) and
     stop, reporting it — never an amend.
   - **6.4 the commit** (guarded, **destructive**,
     `step="step-6.5-commit"`): create **one plain, pathspec-free `git
     commit`** (no trailing `-- <paths>` — 6.3 already proved the index
     names no path outside the applicable set) carrying
     `Workflow-Plan-Approval: <full review_content_id>` +
     `Workflow-Work-Item: <id>` trailers. This command only writes the
     trailer; it never needs to search for one itself. The exact scoped
     trailer *lookup* later durability/freshness checks use is
     `workflow_state.discover_plan_approval_commit` (`WF4a-iii`). **These
     two lines must be the commit message's own final paragraph** — after
     any `Co-Authored-By:`/`Claude-Session:` lines, never before them
     (`OPUS-R129-001`): Git's `git interpret-trailers --parse`, the exact
     mechanism `discover_plan_approval_commit` uses, treats only the
     message's last paragraph as trailers, so a blank line after these
     two lines (e.g. one followed by `Co-Authored-By:`) silently discards
     both and makes the approval commit undiscoverable.

   Implementation stage: unchanged — a metadata-only commit (zero
   production/test changes) carrying `Workflow-Technical-Approval:
   <full review_content_id>` + `Workflow-Work-Item: <id>`, via the same
   direct `apply_technical_approval`/`state_transaction` write and plain
   `git commit` this stage has always used; none of 4b/4c/5/6.2-6.4/
   6a/6b/6c/6d below apply to it. **These two trailer lines must also be
   the commit message's own final paragraph** — after any
   `Co-Authored-By:`/`Claude-Session:` lines, never before them
   (`OPUS-R129-001`, the same rule `milestone-implement.md` step 1f,
   `bootstrap-workflow-v2.md` step 6, step 6.4 above, and
   `accept-milestone.md` step 6 already state): Git's `git
   interpret-trailers --parse` treats only the message's last paragraph
   as trailers, so a blank line after these two lines (e.g. one followed
   by `Co-Authored-By:`) silently discards both and makes the
   technical-approval commit undiscoverable. The exact scoped trailer
   lookup is `workflow_state.discover_technical_approval_commit`
   (`WF4a-iii`).

   **Implementation stage — post-commit verification of the commit this
   invocation just created** (workflow system audit, convergence pass 12,
   ledger row `I21`). The plan stage has step 6a's verification set; this
   stage had none, and its own dedicated validator had no live caller
   anywhere — implemented, documented and unit-tested, but never run, so a
   technical-approval commit that also mutated a *different* work item's
   entry or a top-level routing field, or that recorded
   `technical_approval` without transitioning `phase`, was accepted by
   every consumer. Immediately after the commit lands, call both:

   - `workflow_state.validate_technical_approval_commit(repo_root,
     <the new commit's SHA>, work_item_id)` — the exhaustive
     field-mutation check, the sibling
     `validate_bundle_generation_record_commit` has always applied to
     generation-record commits. It refuses via
     `MalformedTechnicalApprovalCommitError`, naming the offending fields
     or the other work item's id.
   - `workflow_state.verify_post_approval_manifest_match(repo_root,
     work_item, stage="implementation", base_commit=base_commit,
     commit=<the new commit's SHA>)` — the same post-approval identity
     re-verification the plan stage runs inside step 6a's
     `verify_plan_approval_commit`. `work_item` here is the state this
     stage's own `apply_technical_approval` write just produced, so it
     carries the record; a work item without one now raises the named
     `MissingApprovalRecordError` (workflow-2.6.0) instead of a raw
     `TypeError`. Nothing else about this stage changes.

   Both are applied **only to the commit this invocation just created**,
   never retroactively to discovered history: two technical-approval
   commits already in this repository predate the check and do not satisfy
   it (`9fd3c72` for `v2-1-dry-run`, which recorded the approval without a
   `phase` transition, and `ae51770` for `workflow-v2-1-core`, whose field
   set is wider than the contract), and history rewriting is not available
   — several such SHAs are pinned into `WORKFLOW_STATE.json` and approved
   bundles. Wiring the check into discovery instead would therefore have
   refused real, already-approved rounds; wiring it here constrains every
   commit this command creates from now on and re-judges nothing.

   On a failure: **stop**. The commit exists and must not be silently
   amended away — report the exact error, the commit SHA, and that the
   malformed commit needs human resolution before
   `AWAITING_FUNCTIONAL_REVIEW` can be trusted.
6a. **Plan stage only — classify the outcome from durable Git state**
    (`WF8c` items 347/350, never from this invocation's own exit status
    and never from `WORKFLOW_STATE.json`'s own content): call
    `outcome = workflow_state.classify_plan_approval_outcome(repo_root,
    journal)`.
    - **`COMMITTED`**: `commit =
      workflow_state.discover_plan_approval_commit(repo_root, work_item_id,
      journal["expected_review_content_id"], journal["base_commit"],
      head="HEAD")`, then run the one post-commit verification —
      `workflow_state.verify_plan_approval_commit(repo_root, journal,
      commit)` (`D-Plan-Approval-Closure`, workflow-2.6.0). Its truth is
      the committed transaction alone, never this invocation's own
      pre-commit `work_item` (which still carries no, or a
      `STALE`/`SUPERSEDED`, `plan_approval`, because the worktree state
      stays pre-approval until 6c): it verifies the committed
      `WORKFLOW_STATE.json` blob against the journal pin, derives the work
      item from that committed state, requires its
      `plan_approval.approved_review_content_id` and the identity
      recomputed at `commit` both to equal
      `journal["expected_review_content_id"]`, runs the two-sided
      path-set check (nothing outside `journal["applicable_paths"]` in the
      commit; every protected path exactly as approved and every removal
      absent), and checks the artifacts declaration's committed blob when
      it was a member. The in-session run and every resumed or taken-over
      run of this step call exactly this function with the journal — there
      is no other verification path. Passes: proceed to step 6c. Fails:
      pass the exception to
      `workflow_state.classify_post_commit_verification_failure(exc)`.
      - `RECORD_OR_INPUT` (`MissingApprovalRecordError`,
        `CommittedApprovalRecordMismatchError`, an unclassified path, any
        verifier-input or unforeseen error, and an extra path in the
        commit, `CommittedPathSetMismatchError` — the amend corrects
        content, never membership): **stop and report** with `HEAD`
        unchanged — never amend (INV-5). The journal stays open for a
        human.
      - `TREE_CONTENT` (a committed blob differing from the staged and
        pinned bytes, a protected path omitted or a removal still present,
        the committed tree recomputing to another
        identity or missing a protected member — only reachable via a
        genuine transaction-invariant violation, e.g. a `pre-commit`/
        `commit-msg` hook re-staging a file after step 5 through 6.3a ran
        but before `git commit` wrote the final tree, `WF8c` item
        348(ff)/(nn)): run **6a1, amend recovery** (guarded, ordinary then
        destructive,
      `step="step-7b-amend-stage"` then `step="step-7d-amend-commit"`) —
      inside one `plan_approval_guarded_mutation(..., step=
      "step-7b-amend-stage", ...)` window, unconditionally re-run step 5's
      staging-and-pin in full over `journal["applicable_paths"]` (never
      step 5's in-window re-resolution: `HEAD` is now the approval
      commit, so a fresh resolution would legitimately differ) and 6.2's
      compare-and-swap-and-pin (re-staging the *correct*,
      already-verified bytes is a harmless no-op for any member that was
      not actually corrupted); inside a second
      `plan_approval_guarded_mutation(..., step="step-7d-amend-commit",
      ...)` window, `git commit --amend --no-edit` (identical trailers,
      same parent, only the corrected tree differs); then re-run
      `verify_plan_approval_commit` against the amended SHA. Passes
      now: proceed to step 6c with the amended commit as "the" commit.
      Fails again: **stop and report** — never a second amend, never a
      second commit (`WF8c` item 348(jj)).

      **6a1 holds the resolution; it never reserves** (workflow-2.6.0,
      `D-Repo-Global-Lifecycle`). At 6a1's entry, before the
      `step-7b-amend-stage` window opens, call `proof =
      workflow_state.assert_amendment_resolution_held(repo_root,
      work_item_id, journal)`: the lifecycle lock (9) alone, no predicate
      list, and no witness write. Its digest is the journal's pinned
      post-state entry, never whatever `HEAD`'s committed state blob says,
      since that blob may be the very defect 6a1 repairs. It accepts only a
      `RESOLVING` witness reserved by this journal's `owner_token` or one of
      its `previous_owner_tokens` (in session or after a takeover), or a
      `RESOLVED` witness with the pinned digest (another worktree
      self-healed it from the pre-amend commit). For an item with no open
      amendment it returns a proof without taking anything.
      `AmendmentResolutionHeldError`: **stop and report** under this
      step's existing rule — no amend, `HEAD` and the journal left as
      found. The re-staging inside the `step-7b-amend-stage` window is then
      `workflow_state.stage_plan_approval_members(repo_root, journal,
      mode=workflow_state.PLAN_APPROVAL_STAGING_AMEND_RECOVERY,
      resolution_held=proof)`, followed by 6.2's compare-and-swap-and-pin
      as above.
    - **`NOT_COMMITTED`**: run **6b, rollback** below, then stop.
    - **`AMBIGUOUS`**: **stop immediately** — report the journal's own
      identity and the live Git state (`HEAD`, whether a matching-trailer
      commit exists and what its parent is) side by side; never guess,
      never roll back, never forward-complete (`classify_plan_approval_outcome`'s
      own contract). This is a hard gate requiring human resolution; the
      journal is left exactly as found.
6b. **Plan stage only — rollback, on `NOT_COMMITTED` or any failure from
    step 4d through 6.3a**. **First capture the journal's tokens**
    (workflow-2.6.0, `D-Repo-Global-Lifecycle`): re-read it with
    `workflow_state.read_plan_approval_journal(repo_root)` and keep
    `journal_tokens = [journal["owner_token"],
    *journal["previous_owner_tokens"]]`. This must happen before the
    rollback, which closes the journal and so loses the list. A reservation
    made before a takeover carries a token that is by then only in
    `previous_owner_tokens`. Then the rollback itself (guarded,
    destructive, `step="rollback-index-reset"`; deliberately **not**
    `plan_approval_guarded_mutation`, because the rollback call already
    closes the journal and its own owner-progress record as part of a
    successful rollback, so advancing a progress record *after* the yield,
    the way every other guarded step above does, would write one for a
    transaction already gone): acquire the guard directly,
    `lease = workflow_state.acquire_plan_approval_guard(repo_root,
    holder_owner_token=owner_token, step="rollback-index-reset",
    now=<now>)`; inside a `try`/`finally` that releases it
    (`workflow_state.release_plan_approval_guard(repo_root, lease)`),
    call `workflow_state.rollback_plan_approval_transaction(repo_root,
    owner_token=owner_token)` — resets the whole index back to `HEAD`
    (working tree untouched), verifies the reset, and closes the
    journal; writes **zero** `docs/ai-workflow/WORKFLOW_STATE.json`
    bytes, since nothing was ever written to it in the first place.
    `PlanApprovalRollbackInvariantViolationError`/
    `PlanApprovalRollbackVerificationError`: stop and report — the
    journal is deliberately left in place rather than a claimed rollback
    going unverified. **Once the rollback has succeeded**, call
    `workflow_state.release_amendment_resolution(repo_root, work_item_id,
    journal_tokens)`. It takes the lifecycle lock (9) alone and rewrites a
    `RESOLVING` witness whose reservation carries one of `journal_tokens`
    back to `OPEN`, but only while neither the resolver's `HEAD` nor its
    branch tip shows a resolution. Otherwise it is a no-op, as it is on an
    item with no open amendment. So a takeover followed by a
    `NOT_COMMITTED` rollback restores `OPEN` in band, with no orphan test
    and no literal, on a detached `HEAD` too. Report the *original* failure
    that triggered the rollback, not a generic one, once it completes.
    Implementation stage has no equivalent step — its write set was never
    widened by 4a, so its existing failure surface is unchanged.
6c. **Plan stage only — materialize** (guarded, destructive, `step=
    "step-8b-materialize"`, `WF8c` items 347/348(hh)/(mm), reached only
    from 6a's `COMMITTED` branch after verification passes): inside one
    `plan_approval_guarded_mutation(..., step="step-8b-materialize", ...)`
    window, decode `pre_state`/`post_state` from the journal's own
    `pre_procedure_state_b64`/`expected_post_state_b64` and call
    `target = workflow_state.classify_plan_approval_materialize_target(repo_root,
    work_item_id, pre_state, post_state)` — the target-scoped
    classification over *only* this work item's own `work_items` entry,
    so a different work item's legitimate concurrent write never factors
    into it.
    - `NOOP` (this work item's own entry already equals `post_state`'s —
      already materialized, or reconciled by hand): write nothing,
      proceed to step 6c1.
    - `WRITE`: run the fresh, whole-file freshness re-check *immediately*
      before writing —
      `workflow_state.plan_approval_state_matches_pre_transaction(repo_root,
      journal["pre_procedure_state_sha256"])`. `True` (nothing else in
      the file has changed since journal-open): call
      `workflow_state.materialize_plan_approval_state(repo_root, commit,
      journal["expected_post_state_sha256"])`, then proceed to step 6c1.
      `False` (a *different* work item's own entry was legitimately
      updated in the working tree between journal-open and now): **do
      not write** — the durable commit already exists and is not at
      risk, only this working-tree materialization is deferred. **Stop**
      without closing the journal; report that the approval commit
      landed at `<commit>` but the working-tree
      `docs/ai-workflow/WORKFLOW_STATE.json` could not be safely
      overwritten without discarding another work item's own concurrent
      change, and that re-running this command will re-observe the same
      `COMMITTED` outcome and either retry this same check (if the other
      change is still the only difference) or, once a human applies this
      work item's own entry from the commit onto the current file by
      hand, take the `NOOP` branch above and close the journal.
    - `PlanApprovalMaterializeDivergentEntryError` (this work item's
      *own* entry, not some other work item's, matches neither
      `pre_state` nor `post_state`): **stop** without closing the
      journal — something else wrote to this exact work item's own
      state between journal-open and now; no automatic reconciliation is
      safe to attempt. Report the divergence and require human
      resolution.
6c1. **Plan stage only — bind the resolution** (workflow-2.6.0,
    `D-Repo-Global-Lifecycle`; reached only after 6c's `NOOP` or `WRITE`
    branch succeeds, and before 6d): call
    `workflow_state.advance_amendment_witness(repo_root, work_item_id,
    journal=journal, commit=<the verified commit>)`. It takes the lifecycle
    lock (9) alone and runs the witness predicate list, whose first step
    advances this journal's `RESOLVING` reservation to `RESOLVED` now that
    this worktree's `HEAD` carries the reserved resolution. It is
    idempotent: `RESOLVED` with the same digest is a no-op, except that
    `resolved_commit` — a label no check reads — is refreshed to the
    verified commit when another worktree advanced the witness from the
    pre-amend commit. It is a no-op for an item with no open amendment.
    `AmendmentResolutionHeldError` or any other lifecycle refusal: **stop**
    without closing the journal, and report it. The journal must outlive
    the `RESOLVING` state, because the reservation orphan test reads an
    open journal as a live transaction. If this step is lost, the next
    holder of (9) in any worktree self-heals the witness from this `HEAD`.
6d. **Plan stage only — close the journal** (guarded, destructive,
    `step="step-8a-close-journal"`, reached only after step 6c1 succeeds,
    which itself follows 6c's `NOOP` or `WRITE` branch): inside one final
    `plan_approval_guarded_mutation(..., step="step-8a-close-journal",
    ...)` window, call `workflow_state.close_plan_approval_journal(repo_root)`.
    Confirm the approval left nothing behind immediately afterward —
    `WF8c` item 348(e), narrowed by `D-Plan-Approval-Closure`
    (workflow-2.6.0): the index is clean (`git diff --name-only --cached
    HEAD` empty) and no member is dirty (`git --literal-pathspecs status
    --porcelain -- <journal["applicable_paths"]>` empty — literal, so a
    member such as `*.md` names only itself). A whole-tree `git status
    --porcelain` is no longer the check: a path the approved declaration
    stopped protecting but that is still in the worktree is by design not
    a member (step 4a), so its uncommitted state legitimately survives the
    approval, as does any unrelated working-tree change. Recovery
    re-entering at any `progress` step converges through
    `verify_plan_approval_commit` plus 6c/6c1/6d; the approval-trailer commit
    count for this round stays exactly 1.
7. Report the new phase (`IMPLEMENTING` or `AWAITING_FUNCTIONAL_REVIEW`) and
   **stop**. Never chain into the next state's actions in the same
   invocation.

   **workflow-2.4.0, D-Plan-Amendment-4 — report the reconciliation
   outcome (`IMPL6-B1`)**: plan stage only, and only when this
   invocation's own `apply_plan_approval` call resolved an amendment (step
   4c read a non-`None` `pre_registry`/`pre_plan_text` pair for it). Read
   `amendment_history[-1]["reconciliation_outcome"]` from the
   just-materialized `docs/ai-workflow/WORKFLOW_STATE.json` (the exact map
   `reconcile_checkpoints_after_amendment` returned, keyed by checkpoint
   id) and report it alongside the phase, by id: which ids were
   `retained`, which were demoted to `needs_revalidation` directly (their
   own registry row or checkpoint content changed) versus
   `needs_revalidation_dependency` (unchanged themselves, demoted only
   because a dependency was), which were `dropped`, and which are `new`.
   This is the operator-visible distinction between "reconciliation ran
   and legitimately did nothing" (every id `retained`) and "it never ran"
   (no amendment resolved this round, so this paragraph does not apply at
   all) -- never omit it for a round that did resolve an amendment, and
   never fabricate it by re-deriving from anything other than this exact
   field.
