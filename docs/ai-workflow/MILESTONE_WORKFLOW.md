# Milestone Workflow (Semi-Autonomous)

Operational state machine for planning, implementing, and closing out a
milestone with an external reviewer and the user in the loop. This document
is the authoritative owner of the workflow states and gates. It does not
duplicate architecture, migration, or testing rules — see `CLAUDE.md` for
routing to those.

Commands in `.claude/commands/` implement these states. Bundle mechanics are
owned by `docs/ai-workflow/REVIEW_PROTOCOL.md` and
`scripts/prepare-ai-review.sh`.

## State reference

For every state: entry condition, allowed actions, required artifacts, exit
condition, and whether Claude stops.

**Vocabulary states.** Four of the sections below describe a *phase of the
work* that no writer ever persists as a `phase` value in
`docs/ai-workflow/WORKFLOW_STATE.json`: `SELF_REVIEWING_PLAN`,
`AWAITING_TECHNICAL_APPROVAL`, `FIXING_FUNCTIONAL_FINDINGS` and
`AWAITING_USER_ACCEPTANCE`. They are accepted by `workflow_state.py`'s
`KNOWN_PHASES` allowlist — which is deliberately a union of the v1 and v2.1
vocabularies, not a transition graph — so a hand-written or historical
state file carrying one still validates. But every command that passes
through the described work writes the *next* persisted phase directly, so
none of the four is ever a value you will find in a live state file, and
none of them is a state a command can be resumed from. Each section is
marked accordingly, and
`scripts/workflow_state_completion_obligations_test.py` holds that list to
exactly these four: a phase that gains a writer, or a fifth that loses
one, fails there rather than leaving this note quietly wrong (workflow
system audit, convergence pass 12, ledger row `O34`).

### PLANNING

- **Entry**: `docs/ACTIVE_MILESTONE.md` names an incomplete milestone/checkpoint,
  or the user asks to start the next one.
- **Allowed actions**: read `docs/ACTIVE_MILESTONE.md`, `docs/ROADMAP.md`, and
  only the milestone/reference/ADR docs relevant to the target checkpoint;
  draft or update the execution/reference plan.
- **Artifacts**: draft execution plan (and reference plan, if new decisions
  are introduced).
- **Exit**: a concrete, checkpointed plan exists.
- **Stop for user/reviewer?** No.

### AMENDING_PLAN (`workflow-2.4.0`, `D-Plan-Amendment-1`)

A re-entry into plan revision/review for a work item whose plan was already
approved and whose implementation has already begun -- additive to v2.3.1's
existing states/transitions, not a replacement for any of them. Applies to
`governing_workflow_version: "1"`, `"2.1"`, and (`workflow-2.5.0`,
`D-Implementation-Review-Version-Activation`) `"2.2"` work items alike; only
the downstream two-stage-vs-single-stage plan review that follows it still
branches on governing version, unchanged.

- **Entry**: `/request-plan-amendment [work-item-id]` -- a real, persisted
  phase, unlike the vocabulary states above, because the mechanism must
  survive an interruption between the request and the first post-request
  `/milestone-plan` call. Reachable only from `IMPLEMENTING` or
  `SELF_REVIEWING_IMPLEMENTATION` (see those sections' own re-entry note
  below), with no checkpoint `IN_PROGRESS` or claimed
  (`AmendmentCheckpointActiveError` otherwise, XMODEL-R4-B1 -- checked
  authoritatively inside `request_plan_amendment` itself, against both
  `WORKFLOW_STATE.json` and the shared filesystem checkpoint-claim record,
  not only this command's own preflight read; the claim record must be
  checked separately because it is published, step 1d, *before*
  `WORKFLOW_STATE.json` shows anything IN_PROGRESS, so a claim can be
  outstanding while state still looks idle), no open
  `/approve-review plan` transaction, the current `plan_approval`'s own
  approval commit still discoverable and an ancestor of `HEAD`, and every
  checkpoint id in the work item's own current registry of the shape
  `CP<digits>[A-Z]?` -- `CP` followed by one or more digits, optionally
  followed by exactly one uppercase letter (widened, workflow-2.5.1,
  `D-Checkpoint-Id-Anchor-Grammar-Widening`, to admit the pre-`2.4.0`
  inserted-checkpoint lettering convention, e.g. `CP4B`, that predates this
  mechanism) (`AmendmentCheckpointIdShapeError` otherwise, naming every
  offending id, before anything is superseded -- IMPL2-R1). The
  independent, second half of XMODEL-R4-B1's fix lives on the other side
  of the same race: `transition_checkpoint_in_progress` itself refuses
  (`IllegalCheckpointStartPhaseError`) to publish a checkpoint's own
  `IN_PROGRESS` once the work item has left `IMPLEMENTING` -- so a claim
  acquired before this entry transition commits, but not yet reflected in
  state, cannot publish live implementation state on top of an
  already-superseded `plan_approval` either.
- **Allowed actions**: none besides the request itself, which is one
  atomic transition: `plan_approval.status` becomes `SUPERSEDED`; one entry
  is appended to the work item's own append-only `amendment_history`
  (bounded, content-addressed -- a single `pre_amendment_approval_commit`,
  never a stored copy of the plan/registry documents); `amendment_base_commit`
  is set to the pre-amendment `HEAD`. Completion accounting is provisional
  while an item sits here: the live `checkpoints` map still reads
  all-`COMPLETE` under a plan that is being rewritten, and only the next
  reconciliation (below) resolves it.
- **Artifacts**: the `amendment_history` entry; a commit of
  `docs/ai-workflow/WORKFLOW_STATE.json` alone.
- **Exit**: the very next `/milestone-plan [work-item-id]` invocation resumes
  it through that command's own existing dual-mode branch, exactly like any
  other non-terminal entry -- no new plan-review machinery. The entire
  two-stage local-then-manual-external review protocol (or the single-stage
  one, for a `"1"` item) and the `AWAITING_PLAN_APPROVAL` gate below run
  completely unmodified for the amended plan. **Checkpoint reconciliation**
  happens exactly once per amendment, folded into the eventual
  `/approve-review plan`'s own `apply_plan_approval` computation: a
  registry checkpoint unchanged in both content and prose stays `COMPLETE`;
  one whose registry row or checkpoint content changed is rewritten to
  `NEEDS_REVALIDATION` and re-run through the ordinary, unmodified
  `/milestone-implement` path; one dropped from the amended registry is
  removed from the live `checkpoints` map (its history survives in the
  amendment's own `checkpoints_snapshot` and in git history via its commit
  trailers). **While drafting the amended plan**, delimit every registry
  checkpoint id with a `<!-- CPn -->`/`<!-- /CPn -->` anchor pair (one or
  more, non-overlapping, around that checkpoint's own content; `n` is the
  same widened `CP<digits>[A-Z]?` shape the entry condition above checks) --
  add the anchors now, not after `/approve-review plan` step 4c's
  `validate_post_anchor_coverage` refuses naming the first uncovered id.
  `validate_post_anchor_coverage` consults the identical
  `checkpoint_id_supports_anchor` predicate the entry condition above uses,
  so the post-side check accepts exactly the same widened shape, never a
  second, independently-maintained grammar.
- **Stop for user/reviewer?** No. `/request-plan-amendment` itself carries
  the same mechanism-independent user-only guard `/approve-review` and
  `/accept-milestone` use (Claude cannot invoke it), but once a human has
  invoked it, work continues autonomously from here exactly as it would
  from `PLANNING`.

### SELF_REVIEWING_PLAN

*Vocabulary state — never persisted (see "Vocabulary states" above).*
`/milestone-plan` does this work inside its own step 4 and then writes
`AWAITING_LOCAL_PLAN_REVIEW` (`TWO_STAGE_PLAN_REVIEW_VERSIONS` --
`"2.1"`/`"2.2"` alike, `workflow-2.5.0`) or `AWAITING_EXTERNAL_PLAN_REVIEW`
(`"1"`) directly, through `publish_plan_revision`.

- **Entry**: a draft plan exists.
- **Allowed actions**: critically review the plan for missing requirements,
  migration risk, usability gaps, unnecessary complexity, missing tests;
  revise the plan in place.
- **Artifacts**: revised plan with self-review notes folded in (not a
  separate journal file).
- **Exit**: plan reflects the self-review; no known gaps left unaddressed or
  unflagged.
- **Stop for user/reviewer?** No.

### AWAITING_EXTERNAL_PLAN_REVIEW

- **Entry**: self-review is complete.
- **Allowed actions**: run `scripts/prepare-ai-review.sh <base-sha> plan
  <work_item_id>` to export the bundle -- `work_item_id` is **required**
  for the plan stage, never resolved from the live `active_work_item_id`
  (`D-Fingerprint-Generalization`). No implementation.
- **Artifacts**: `.ai-review/<work_item_id>/current/` (plan stage) and
  `.ai-review/<work_item_id>/review-bundle.tar.gz`.
- **Exit**: external reviewer places feedback at
  `.ai-review/feedback/REVIEW_FEEDBACK.md` (feedback stays flat, stage-
  agnostic, unlike the plan-stage bundle directory itself).
- **Stop for user/reviewer?** Yes — hard gate. Claude must stop here.

### REVISING_PLAN

- **Entry**: `.ai-review/feedback/REVIEW_FEEDBACK.md` exists.
- **Allowed actions**: validate every finding against the repository; apply
  accepted findings to the plan; document evidence-based rejections.
- **Artifacts**: revised plan; rejection rationale (inline in the plan or
  review bundle, not a new standalone doc).
- **Exit**: all blocking/important findings resolved or rejected with
  evidence, **and** the most recently reviewed round's status was `REVISE`
  or `APPROVE` (never `BLOCK`) — proceeds to `AWAITING_PLAN_APPROVAL`. A
  `REVISE` round with zero blocking findings left reaches that gate exactly
  as readily as an `APPROVE` round; this condition reads only the
  review-round artifact, never any existing approval record (non-circular
  by construction — see `AWAITING_PLAN_APPROVAL` below).
- **Stop for user/reviewer?** Only if a rejection or major plan change needs
  reviewer sign-off before implementation; otherwise proceed.

### AWAITING_LOCAL_PLAN_REVIEW (`governing_workflow_version` in `TWO_STAGE_PLAN_REVIEW_VERSIONS` only)

Part of the two-stage local-then-manual-external plan-review protocol
(`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s `D-Plan-Review-Stages`), inserted
between `REVISING_PLAN` and `AWAITING_PLAN_APPROVAL`. Scoped entirely to
work items whose `governing_workflow_version` is a member of
`TWO_STAGE_PLAN_REVIEW_VERSIONS = {"2.1", "2.2"}` (`workflow-2.5.0`,
`D-Implementation-Review-Version-Activation` widens the membership test
that was `"2.1"` alone through `workflow-v2-1-core`/`workflow-2.4.0`) — a
`"1"` item never enters this state; its `REVISING_PLAN` exits straight to
`AWAITING_PLAN_APPROVAL`, unchanged.

- **Entry**: `REVISING_PLAN`'s exit condition is met, or `/apply-plan-review`
  has just applied an accepted plan edit (its `TWO_STAGE_PLAN_REVIEW_VERSIONS`-only
  revised exit step, below -- widened `workflow-2.5.0` from a bare `"2.1"`
  check).
- **Allowed actions**: run `/review-plan` (recommended in a fresh session,
  for genuine independence from the session that wrote the plan — strongly
  recommended operational guidance, not a verified precondition).
- **Artifacts**: `REVIEW_FEEDBACK.md` (`Reviewer role: LOCAL_MODEL_PLAN_REVIEW`);
  for an `APPROVE` verdict only, a new `plan_review_stages` ledger entry.
- **Exit, verdict-specific** (see the transition table below):
  - **`APPROVE`**: `/review-plan` records the completed
    `LOCAL_MODEL_PLAN_REVIEW` stage against the current `review_content_id`
    and transitions to `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`. Only a
    current local `APPROVE` may satisfy that transition.
  - **`REVISE`**: `/review-plan` writes `REVIEW_FEEDBACK.md` only (no
    ledger entry); transitions to `REVISING_PLAN`; `/apply-plan-review` is
    then required.
  - **`BLOCK`**: no ledger write, no transition; remains at
    `AWAITING_LOCAL_PLAN_REVIEW` — explicit user resolution required.
- **Stop for user/reviewer?** Yes — the current session's turn ends here. A
  fresh, independent session is strongly recommended before running
  `/review-plan`.

### AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW (`governing_workflow_version` in `TWO_STAGE_PLAN_REVIEW_VERSIONS` only)

- **Entry**: the `plan_review_stages` ledger records a
  `LOCAL_MODEL_PLAN_REVIEW` stage completed with `verdict: APPROVE` against
  the *current* plan-stage `review_content_id` — by construction, the only
  way to reach this state.
- **Allowed actions**: the user uploads the exact bundle `/review-plan`
  named (path, `bundle_id`, `review_content_id`) to a manual external
  reviewer (recommended: ChatGPT) and pastes its feedback into
  `REVIEW_FEEDBACK.md` (`Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW`) —
  manual end-to-end, identical in mechanism to today's single external
  review, only gated on the local stage having completed first. Once
  feedback is pasted, run `/record-manual-plan-review` to ingest it.
- **Artifacts**: `REVIEW_FEEDBACK.md` (`Reviewer role: MANUAL_EXTERNAL_PLAN_REVIEW`);
  for an `APPROVE` verdict only, the ledger's second stage entry.
- **Exit, verdict-specific** (see the transition table below):
  - **`APPROVE`**: `/record-manual-plan-review` records the completed
    `MANUAL_EXTERNAL_PLAN_REVIEW` stage against the current
    `review_content_id` and transitions to `AWAITING_PLAN_APPROVAL`.
  - **`REVISE`**: `/record-manual-plan-review` records nothing in the
    ledger; transitions to `REVISING_PLAN`; `/apply-plan-review` is then
    required (identical mechanism to today's single-stage `REVISE`
    handling).
  - **`BLOCK`**: no ledger write, no transition; remains at
    `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` — explicit user resolution
    required.
- **Stop for user/reviewer?** Yes — hard gate, identical in kind to the
  `"1"` state machine's own `AWAITING_EXTERNAL_PLAN_REVIEW`.

**Verdict/state transition table**, for both `TWO_STAGE_PLAN_REVIEW_VERSIONS`-only states above:

| Current state | Verdict | Writer | Next state/action | Validation preconditions |
|---|---|---|---|---|
| `AWAITING_LOCAL_PLAN_REVIEW` | `APPROVE` | `/review-plan` | record local stage (`verdict: APPROVE`) against current `review_content_id` → `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `governing_workflow_version` in `TWO_STAGE_PLAN_REVIEW_VERSIONS`; `phase == AWAITING_LOCAL_PLAN_REVIEW`; recomputed `bundle_id`/`review_content_id` match `MANIFEST.md`/`REVIEW_REQUEST.md` (not stale) |
| `AWAITING_LOCAL_PLAN_REVIEW` | `REVISE` | `/review-plan` | write `REVIEW_FEEDBACK.md` only, no ledger write → `REVISING_PLAN`; `/apply-plan-review` required | same as above |
| `AWAITING_LOCAL_PLAN_REVIEW` | `BLOCK` | `/review-plan` | no ledger write, no transition; remains `AWAITING_LOCAL_PLAN_REVIEW` | same as above; explicit user resolution required before any further command |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `APPROVE` | `/record-manual-plan-review` | record manual stage (`verdict: APPROVE`, plus the feedback's own `bundle_id`) against current `review_content_id` → `AWAITING_PLAN_APPROVAL` | `governing_workflow_version` in `TWO_STAGE_PLAN_REVIEW_VERSIONS`; `phase == AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW`; a current `LOCAL_MODEL_PLAN_REVIEW` `APPROVE` recorded for the same `review_content_id`; `REVIEW_FEEDBACK.md`'s `Reviewer role:` is `MANUAL_EXTERNAL_PLAN_REVIEW` or the legacy `manual_external_plan_review`; its `review_content_id` matches the current recomputed value (**hard**, blocks ingestion) — its `bundle_id` matching the current recomputed value is **advisory only** (warns, naming both, never blocks); no `MANUAL_EXTERNAL_PLAN_REVIEW` stage already recorded against this `review_content_id` (rejects duplicate ingestion) |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `REVISE` | `/record-manual-plan-review` | no ledger write → `REVISING_PLAN`; `/apply-plan-review` required | same as above |
| `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | `BLOCK` | `/record-manual-plan-review` | no ledger write, no transition; remains `AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW` | same as above; explicit user resolution required |

Malformed, missing, or unparseable `REVIEW_FEEDBACK.md` content at either
row is refused before any state change, naming what failed to parse.

Any protected plan edit after either or both stages complete invalidates
both — by the recomputation rule (validity is by recomputation, not an
active clear step), never an explicit clear — and the work item's next
required stage is always `AWAITING_LOCAL_PLAN_REVIEW`
(`/apply-plan-review`'s `TWO_STAGE_PLAN_REVIEW_VERSIONS`-only revised exit
step, below -- widened `workflow-2.5.0` from a bare `"2.1"` check),
whether the
edit was driven by a local-model or a manual-external `REVISE`. No path
re-enters manual-external review without a fresh local pass first.

### AWAITING_PLAN_APPROVAL

- **Entry**: `REVISING_PLAN`'s exit condition is met. For a work item whose
  `governing_workflow_version` is a member of `TWO_STAGE_PLAN_REVIEW_VERSIONS`
  (`"2.1"` or, since `workflow-2.5.0`, `"2.2"` — **this membership test, not
  the bare `"2.1"` literal, is what actually gates entry here; stating it as
  the literal would silently let a `"2.2"` item bypass both plan-review
  stages the moment `"2.2"` exists**), one further condition applies: the
  work item's `plan_review_stages` ledger must additionally record both
  `LOCAL_MODEL_PLAN_REVIEW` and `MANUAL_EXTERNAL_PLAN_REVIEW` completed, in
  that order, against the *current* plan-stage `review_content_id` (the
  two-stage local-then-manual-external plan-review protocol,
  `/review-plan`/`/record-manual-plan-review`) — a single reviewed round is
  necessary but no longer sufficient for such an item. A `"1"` item's entry
  condition is exactly `REVISING_PLAN`'s exit condition, unchanged.
- **Allowed actions**: run `/approve-review plan`, which chooses the
  approval basis (`EXTERNAL_APPROVE` when the current
  `REVIEW_FEEDBACK.md`'s status is exactly `APPROVE` and its bundle ID
  matches the just-recomputed one exactly; otherwise `USER_OVERRIDE`, which
  requires literal override text naming the exact work item and stage in
  the same turn). `BLOCK` never reaches either basis. No plan edits, no
  implementation.
- **Artifacts**: none new until `/approve-review plan` runs.
- **Exit**: `/approve-review plan` writes `plan_approval` and creates the
  one plan-approval commit.
- **Stop for user/reviewer?** Yes — hard gate. Only the user can invoke
  `/approve-review` (mechanism-independent guard: `disable-model-invocation:
  true`, plus a specificity check on the literal `user_confirmation` text).

### IMPLEMENTING

- **Entry**: current HEAD is the plan-approval commit or a checkpoint-commit
  descendant of it, `plan_approval.status == CURRENT`, and a freshly
  recomputed plan-stage `review_content_id` matches
  `plan_approval.approved_review_content_id`.
- **Allowed actions**: implement checkpoint by checkpoint; run the narrowest
  relevant checks per checkpoint; review each coherent diff; create
  authorized intermediate commits; update `docs/ACTIVE_MILESTONE.md` as
  checkpoints complete.
- **Artifacts**: source/test changes; updated `docs/ACTIVE_MILESTONE.md`; commits.
- **Exit**: all plan checkpoints implemented, **or** (`workflow-2.4.0`,
  `D-Plan-Amendment-1`) an authorized `/request-plan-amendment
  [work-item-id]` re-enters `AMENDING_PLAN` above -- never Claude's own
  choice to make.
- **Stop for user/reviewer?** No — continue across checkpoints without
  stopping, subject to the stop conditions in `AGENTS.md`.

### SELF_REVIEWING_IMPLEMENTATION

- **Entry**: all checkpoints implemented.
- **Allowed actions**: full internal review of the diff; fix blocking and
  important findings; run required verification (narrow, then full suite
  per `CLAUDE.md` commands).
- **Artifacts**: fixed diff; verification results (actually run, not
  assumed).
- **Exit**: no known blocking/important self-review findings remain open,
  **or** (`workflow-2.4.0`, `D-Plan-Amendment-1`) an authorized
  `/request-plan-amendment [work-item-id]` re-enters `AMENDING_PLAN` above.
- **Stop for user/reviewer?** No.

### AWAITING_LOCAL_IMPLEMENTATION_REVIEW (`governing_workflow_version: "2.2"` only)

Part of the two-stage local-then-manual-external implementation-review
protocol (`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s
`D-Implementation-Review-Stages`, `workflow-2.5.0`), mirroring
`AWAITING_LOCAL_PLAN_REVIEW` above function-for-function, substituted for
the implementation stage — inserted between `SELF_REVIEWING_IMPLEMENTATION`/
`APPLYING_REVIEW_FEEDBACK` and the existing terminal
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`. Scoped entirely to `"2.2"` work
items — a `"1"`/`"2.1"` item never enters this state; both exits target
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` directly, byte-for-byte unchanged.

- **Entry**: for a `"2.2"` item only — `SELF_REVIEWING_IMPLEMENTATION`'s
  exit (first round) or `APPLYING_REVIEW_FEEDBACK`'s (or a `"2.2"` item's
  functional-review bounded-fix branch's) post-fix exit (later rounds),
  both through `record_bundle_generation`'s own version-dependent
  `bundle_generation_target_phase(stage, governing_workflow_version)`
  resolver — the sole writer for both entries; there is no separate
  `transition_to_awaiting_local_implementation_review` writer.
- **Allowed actions**: run `/review-implementation`, which for a `"2.2"`
  item becomes the authoritative `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage
  writer (its existing `"1"`/`"2.1"` advisory-only behavior is unchanged).
- **Artifacts**: `REVIEW_FEEDBACK.md` (`Reviewer role:
  LOCAL_MODEL_IMPLEMENTATION_REVIEW`); for an `APPROVE` verdict only, a new
  `implementation_review_stages` ledger entry.
- **Exit, verdict-specific** (see the transition table below):
  - **`APPROVE`**: `/review-implementation` records the completed
    `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage against the current
    implementation-stage `review_content_id` and transitions to
    `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`.
  - **`REVISE`**: `/review-implementation` writes `REVIEW_FEEDBACK.md` only
    (no ledger entry); transitions directly to `APPLYING_REVIEW_FEEDBACK`
    (the command itself performs the phase write, exactly as
    `/review-plan`'s `REVISE` branch does for `REVISING_PLAN` —
    `/apply-implementation-review` makes no `enter_applying_review_feedback`
    call for a `"2.2"` item, since it finds `phase` already there).
  - **`BLOCK`**: no ledger write, no transition; remains at
    `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` — explicit user resolution
    required.
- **Stop for user/reviewer?** Yes — the current session's turn ends here,
  the same "stop, do not auto-continue" pattern the plan-side stage uses.

### AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW (`governing_workflow_version: "2.2"` only)

- **Entry**: the `implementation_review_stages` ledger records a
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` stage completed with `verdict:
  APPROVE` against the *current* implementation-stage `review_content_id` —
  by construction, the only way to reach this state.
- **Allowed actions**: the user uploads the bundle to a manual external
  reviewer and pastes its feedback into `REVIEW_FEEDBACK.md` (`Reviewer
  role: MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`); runs
  `/record-manual-implementation-review` (new command, mirrors
  `/record-manual-plan-review` exactly, substituted for the implementation
  stage — mechanical, model-independent, not a user-authority gate, never
  edits source/plan/registry/mapping/bundle content) to ingest it.
- **Artifacts**: `REVIEW_FEEDBACK.md` (`Reviewer role:
  MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`); for an `APPROVE` verdict only,
  the ledger's second stage entry.
- **Exit, verdict-specific** (see the transition table below):
  - **`APPROVE`**: `/record-manual-implementation-review` records the
    completed `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` stage (including the
    feedback's own `bundle_id` verbatim) against the current
    `review_content_id` and transitions to
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` — **the existing phase name,
    reused as the terminal "ready for approval" phase**, exactly as
    `AWAITING_PLAN_APPROVAL` kept its own pre-existing name across
    `D-Plan-Review-Stages`; no new terminal name is introduced.
  - **`REVISE`**: no ledger write; transitions directly to
    `APPLYING_REVIEW_FEEDBACK`.
  - **`BLOCK`**: no ledger write, no transition; remains at
    `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` — explicit user
    resolution required.
- **Stop for user/reviewer?** Yes — hard gate, identical in kind to today's
  single `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`.

**Verdict/state transition table**, for both `"2.2"`-only states above
(mirrors the plan-side table exactly, substituted for the implementation
stage):

| Current state | Verdict | Writer | Next state/action | Validation preconditions |
|---|---|---|---|---|
| `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `APPROVE` | `/review-implementation` | record local stage → `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | item is `"2.2"`; `phase == AWAITING_LOCAL_IMPLEMENTATION_REVIEW`; recomputed `bundle_id`/`review_content_id` match `MANIFEST.md`/`REVIEW_REQUEST.md` |
| `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `REVISE` | `/review-implementation` | write `REVIEW_FEEDBACK.md`, no ledger write → `APPLYING_REVIEW_FEEDBACK` | same as above |
| `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` | `BLOCK` | `/review-implementation` | no ledger write, no transition | same as above; explicit user resolution required |
| `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `APPROVE` | `/record-manual-implementation-review` | record manual stage → `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` | item is `"2.2"`; `phase == AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`; a current local `APPROVE` recorded for the same `review_content_id`; feedback role is `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW`; `review_content_id` match is **hard**; `bundle_id` match is **advisory only**; no duplicate ingestion |
| `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `REVISE` | `/record-manual-implementation-review` | no ledger write → `APPLYING_REVIEW_FEEDBACK` | same as above |
| `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` | `BLOCK` | `/record-manual-implementation-review` | no ledger write, no transition | same as above; explicit user resolution required |

Malformed, missing, or unparseable `REVIEW_FEEDBACK.md` content at either
row is refused before any state change, naming what failed to parse. Any
protected implementation-stage content change invalidates both stages by
recomputation (`review_content_id` no longer matches), never an explicit
clear step; the next required stage is always
`AWAITING_LOCAL_IMPLEMENTATION_REVIEW`. No path re-enters manual-external
review without a fresh local pass first — see
`docs/ai-workflow/WORKFLOW_V2_PLAN.md`'s consolidated "where the two-stage
mirror genuinely diverges" disposition (`workflow-2.5.0` `#### 2.5.0
disposition record`) for the specific points, all traced to the
implementation stage's commit-anchored identity/provenance, where this
mirror is not exact.

### AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW

- **Entry**: self-review and verification are complete. For a `"2.2"` item,
  reached only after both `AWAITING_LOCAL_IMPLEMENTATION_REVIEW` and
  `AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` above have each recorded
  an `APPROVE` against the current implementation-stage `review_content_id`
  — the existing phase name is reused as the terminal "ready for approval"
  phase rather than introducing a new one (see those sections' own exit
  conditions). A `"1"`/`"2.1"` item's entry condition is exactly
  self-review-and-verification-complete, unchanged.
- **Allowed actions**: run
  `scripts/prepare-ai-review.sh <base-sha> implementation` to export the
  bundle. No further implementation. Optionally, run `/review-implementation`
  — a non-gating, model-independent second opinion on the current bundle;
  it writes the current `.ai-review/feedback/REVIEW_FEEDBACK.md` (or the
  scoped equivalent) once its own pre-write guards pass, but still writes
  no `docs/ai-workflow/WORKFLOW_STATE.json` and never advances this state,
  so it remains non-gating and adds no new gate — just no longer "writes
  nothing".
- **Artifacts**: `.ai-review/current/` (implementation stage) with the real
  diff, changed files, tests run, decisions; `.ai-review/review-bundle.tar.gz`.
- **Exit**: external reviewer places feedback at
  `.ai-review/feedback/REVIEW_FEEDBACK.md`.
- **Stop for user/reviewer?** Yes — hard gate. Claude must stop here. Do not
  mark the milestone accepted.

### APPLYING_REVIEW_FEEDBACK

- **Entry**: implementation review feedback exists.
- **Allowed actions**: reproduce and validate each finding; fix all
  blocking/important findings; explain evidence-based rejections; rerun
  relevant tests; commit coherent fixes; regenerate the bundle
  (`post-fix` stage) if another review round is needed.
- **Artifacts**: fixed diff; updated `.ai-review/current/` (post-fix stage);
  commits.
- **Exit**: all blocking/important findings resolved or rejected with
  evidence, **and** the most recently reviewed round's status was `REVISE`
  or `APPROVE` (never `BLOCK`) — proceeds to `AWAITING_TECHNICAL_APPROVAL`.
- **Stop for user/reviewer?** Only if unresolved blocking findings or major
  rework remain; otherwise proceed.

### AWAITING_TECHNICAL_APPROVAL

*Vocabulary state — never persisted (see "Vocabulary states" above).*
It names the gate, not a stored value: the item sits at
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` until
`apply_technical_approval` writes `AWAITING_FUNCTIONAL_REVIEW` in one
step. `technical_approval_gate_reachable` is what actually decides
whether the gate is open.

- **Entry**: `APPLYING_REVIEW_FEEDBACK`'s exit condition is met, no
  protected path is dirty (`WORKFLOW_STATE.json`/`WORKFLOW_CONFIG.json`
  dirtiness never blocks this), and current committed content matches
  `reviewed_implementation_head` exactly (the bundle generator's own,
  sole-writer field — set at `implementation`/`post-fix` stage). For a
  `"2.2"` item, `technical_approval_gate_reachable`'s entry condition gains
  exactly the ledger check `plan_approval_gate_reachable` already applies
  for a `TWO_STAGE_PLAN_REVIEW_VERSIONS` plan item: both
  `LOCAL_MODEL_IMPLEMENTATION_REVIEW` and
  `MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW` must be recorded `APPROVE`
  against the current implementation-stage `review_content_id`
  (`workflow-2.5.0`, `D-Implementation-Review-Stages`). A `"1"`/`"2.1"`
  item's condition is exactly today's shared rule, unchanged.
- **Allowed actions**: run `/approve-review implementation`, which chooses
  the approval basis exactly as `AWAITING_PLAN_APPROVAL` does above. No
  further implementation changes.
- **Artifacts**: none new until the command runs.
- **Exit**: `/approve-review implementation` writes `technical_approval`
  and creates a metadata-only technical-approval commit (zero production/
  test changes).
- **Stop for user/reviewer?** Yes — hard gate, same enforcement as
  `AWAITING_PLAN_APPROVAL`.

### AWAITING_FUNCTIONAL_REVIEW

- **Entry**: `technical_approval.status == CURRENT`.
- **Allowed actions**: confirm automated verification state; write a concise
  manual functional-review checklist (setup, test data, exact flows,
  expected results, known limitations); update `docs/ACTIVE_MILESTONE.md`.
  Optionally, run `/review-functional` — a non-gating, report-only,
  model-independent second opinion on the checklist's completeness and
  evidence reproducibility; it writes nothing and never advances this
  state, so it adds no new gate.
- **Artifacts**: functional-review checklist (in `docs/ACTIVE_MILESTONE.md` or a
  file it links to).
- **Exit**: user performs functional testing and places findings at
  `.ai-review/feedback/FUNCTIONAL_REVIEW.md`.
- **Stop for user/reviewer?** Yes — hard gate. Claude must stop here.
- **One acceptance command, and what to do when it refuses.** Once
  functional review is clean, `/accept-milestone` is the only acceptance
  command. For a work item with a `docs/ai-workflow/WORKFLOW_STATE.json`
  entry it additionally requires the item's own registry to be terminal —
  every checkpoint `COMPLETE`, recomputed fresh from
  `workflow_state.select_next_checkpoint(work_item, registry)`, never read
  from a phase value written earlier — and refuses by name
  (`IncompleteOwnCheckpointsError`, and the advisory pre-flight
  `milestone_complete_gate_reachable`) when it is not. The refusal is not a
  dead end; it names the outstanding checkpoint, and there are exactly
  three supported ways forward:
  - the checkpoint is still part of this milestone's scope → finish it
    with `/milestone-implement`, then come back to this gate;
  - functional testing produced a finding fixable inside the approved
    scope → `/apply-functional-review`'s **bounded** branch, which marks
    `technical_approval` `STALE`, lands the fix, regenerates the `post-fix`
    bundle and returns the item to a fresh
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`;
  - the finding is new or wider scope → `/apply-functional-review`'s
    **broad** branch, which creates a `<parent-id>-remediation-<n>` child
    work item that runs its own full cycle, and blocks the parent's
    acceptance until it is itself `MILESTONE_COMPLETE`.

  There is deliberately **no** command that records functional acceptance
  of a partial round. `D-Scoped-Remediation-Acceptance` (resolves
  `WF8B-002`) once added a second, mutually exclusive command here,
  `/accept-scoped-remediation`, for exactly that case; it was retired
  (ledger `I10` in `docs/ai-workflow/audit/WORKFLOW_DEFECT_LEDGER.md`)
  because its entry precondition — `AWAITING_FUNCTIONAL_REVIEW` with a
  non-terminal own registry — has no producer in any supported lifecycle,
  so the command could only ever refuse. A work item with no state entry
  (an ordinary `"1"` item that never got one) has no registry to check —
  `/accept-milestone` is the only reachable command, unchanged.

### FIXING_FUNCTIONAL_FINDINGS

*Vocabulary state — never persisted (see "Vocabulary states" above).*
`/apply-functional-review` runs from `AWAITING_FUNCTIONAL_REVIEW`; its
bounded branch's own `record_bundle_generation` is what moves the item,
to `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`.

- **Entry**: `.ai-review/feedback/FUNCTIONAL_REVIEW.md` exists.
- **Allowed actions**: classify each finding (defect, usability issue,
  missing requirement, enhancement, expected behavior); reproduce where
  practical; fix defects/usability issues/missing requirements; add
  regression tests; commit coherent fixes; prepare a revised checklist.
- **General functional-remediation cycle** (`D-Functional-Remediation`,
  `WF4c`): for a work item with a `docs/ai-workflow/WORKFLOW_STATE.json`
  entry (either governing version — this is not a `"2.1"`-only mechanism),
  each finding requiring a fix routes through exactly one of three
  branches, decided before any source/test edit lands:
  - **No code change**: the finding is resolved without touching
    source/tests (e.g. a narrative-only checklist correction). Returns
    directly to `AWAITING_FUNCTIONAL_REVIEW`; `technical_approval` is left
    completely untouched.
  - **Bounded code change**: a normal, contained fix. **Stale-before-edit
    ordering, hard requirement**: `technical_approval.status` is set to
    `STALE` and persisted to `WORKFLOW_STATE.json` *before* the first
    edit — never after. The fix is made, committed, and the bundle is
    regenerated at the `post-fix` stage; regeneration is the step that
    writes the new `reviewed_implementation_head` (D-Approval-Commits'
    sole writer for that field), which is what makes `/approve-review
    implementation` reachable again. This re-enters
    `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`: a fresh implementation-
    review round and a fresh `/approve-review implementation` are
    required before `AWAITING_FUNCTIONAL_REVIEW` is reachable again for
    this item — Claude stops here, it does not loop back into functional
    review directly.
  - **Broad/multi-finding remediation, as a child work item**: findings
    whose fix spans substantially more work than a single contained
    round are never implemented inline. A distinct child work item
    (`work_item_id: "<parent-id>-remediation-<n>"`, `n` derived
    deterministically) is created — same `work_item_type` as the parent,
    carrying `parent_work_item_id`, with its own registry/mapping and its
    own `base_commit` (the parent's implementation head at branch time).
    It routes through the full normal cycle
    (`AWAITING_EXTERNAL_PLAN_REVIEW` → ... → `MILESTONE_COMPLETE`) via the
    ordinary commands, exactly like any other work item, because it is
    one. The parent's own registry, mapping, and completed-checkpoint
    history are never mutated by this branch; the deferral is recorded in
    the parent's own functional-review checklist, naming the child id.
- **Artifacts**: fixed diff with regression tests; revised functional
  checklist (naming any remediation child work-item id); commits.
- **Exit**: all findings addressed or explicitly deferred with rationale
  (a deferral to a remediation child counts as a rationale).
- **Stop for user/reviewer?** Returns to `AWAITING_FUNCTIONAL_REVIEW` for
  another pass unless the user explicitly waives it — except a round with
  any bounded-code-change finding, which stops at the fresh
  `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` gate instead (above).

### AWAITING_USER_ACCEPTANCE

*Vocabulary state — never persisted (see "Vocabulary states" above).*
`/accept-milestone` runs from `AWAITING_FUNCTIONAL_REVIEW` and writes
`MILESTONE_COMPLETE` in one step. The acceptance gate's own reachability
check accepts either phase name, so a historical state file carrying
this value is still honoured — nothing writes it.

- **Entry**: functional review is clean (or remaining items are explicitly
  deferred/waived by the user).
- **Allowed actions**: none besides answering questions — no further code
  changes.
- **Artifacts**: none new.
- **Exit**: user explicitly accepts the milestone.
- **Stop for user/reviewer?** Yes — hard gate. Claude must stop here.

### MILESTONE_COMPLETE

- **Entry**: explicit user acceptance received.
- **Parent-completion block** (`D-Functional-Remediation`, `WF4c`,
  resolves `GPT-R9-016`): for a work item with a
  `docs/ai-workflow/WORKFLOW_STATE.json` entry, this state is unreachable
  while any other work item names it as `parent_work_item_id` and has not
  itself reached `MILESTONE_COMPLETE` — a reverse lookup over
  `work_items`, requiring no new field on the parent.
  `/accept-milestone` refuses outright, naming every still-incomplete
  child, rather than completing a parent whose broad remediation work is
  still open in a child item elsewhere.
- **Own-checkpoint-completion block** (`D-Scoped-Remediation-Acceptance`,
  resolves `WF8B-002`): for a work item with a
  `docs/ai-workflow/WORKFLOW_STATE.json` entry and a non-null
  `registry_path`, this state is additionally unreachable while the
  item's **own** registry has any checkpoint that is not `COMPLETE`
  (including one still `IN_PROGRESS`) — `complete_work_item` computes this
  via `select_next_checkpoint`, independent of, and in addition to, the
  parent-completion block above. This guard is fail-closed by construction
  (revision 23, `GPT-R36-001`): a registry-backed item's registry argument
  must be explicitly supplied and must declare that item's own
  `work_item_id`, or `complete_work_item` itself refuses
  (`RegistryCoverageError`) rather than silently treating an omitted or
  foreign registry as "nothing to check." `/accept-milestone` refuses outright,
  naming the actual phase and the outstanding checkpoint, rather than
  completing an item whose own last checkpoint has never been attempted.
  This is the same defect class the parent-completion block already
  resolves, applied to the item's own registry instead of a child work
  item's: see `D-Scoped-Remediation-Acceptance` for the full design of
  this block. The non-terminal acceptance path that decision paired it
  with, `/accept-scoped-remediation`, has been retired (ledger `I10`); the
  block itself stays, and the `AWAITING_FUNCTIONAL_REVIEW` section above
  lists the supported ways forward when it fires.
- **Allowed actions**: final verification confirmation; update
  `docs/ROADMAP.md` and `docs/ACTIVE_MILESTONE.md`; archive the milestone's
  plans to `docs/milestones/completed/`; create the final completion commit
  if one is still needed; prepare `docs/ACTIVE_MILESTONE.md` for the next
  milestone's `PLANNING` state.
- **Artifacts**: updated roadmap/status docs; archived plans; completion
  commit.
- **Exit**: next milestone is ready for `PLANNING`.
- **Stop for user/reviewer?** No further action — do not begin implementing
  the next milestone. Wait for the user to invoke `/milestone-plan`.

## Hard gates summary

Claude must stop and wait for a human/external input at exactly six points:

1. `AWAITING_EXTERNAL_PLAN_REVIEW`
2. `AWAITING_PLAN_APPROVAL`
3. `AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW`
4. `AWAITING_TECHNICAL_APPROVAL`
5. `AWAITING_FUNCTIONAL_REVIEW`
6. `AWAITING_USER_ACCEPTANCE`

`AWAITING_PLAN_APPROVAL` and `AWAITING_TECHNICAL_APPROVAL` are each enforced
by `/approve-review`'s mechanism-independent user-only guard
(`disable-model-invocation: true`, plus a literal, specificity-checked
`user_confirmation`) — only the user can exit either gate, never Claude
autonomously. `/accept-milestone` carries the same guard for
`AWAITING_USER_ACCEPTANCE`.

Two of the six — `AWAITING_TECHNICAL_APPROVAL` and
`AWAITING_USER_ACCEPTANCE` — are **vocabulary states** ("Vocabulary
states" above): a gate is a point where Claude must stop, not a value a
writer persists. Both are enforced by the guards named in the paragraph
above and by `technical_approval_gate_reachable` /
`milestone_complete_gate_reachable`, which read the phase the item is
*actually* at (`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` and
`AWAITING_FUNCTIONAL_REVIEW` respectively). Nothing about the gates
depends on those two names ever being written, and ledger `D3` records
the disproven claim that it did.

`/accept-scoped-remediation` (`D-Scoped-Remediation-Acceptance`, resolves
`WF8B-002`) was a second, mutually exclusive user-only command reachable
from the same `AWAITING_FUNCTIONAL_REVIEW` gate as `/accept-milestone`. It
never added a gate, and it has since been retired outright (ledger `I10`):
its entry precondition had no producer in any supported lifecycle. The
hard gate count was **6** with it and is **6** without it.

`/review-implementation` and `/review-functional` (`workflow-v2-3`) add no
gate either: both are optional, non-gating actions reachable from inside an
existing gate (`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` and
`AWAITING_FUNCTIONAL_REVIEW` respectively) — neither writes
`docs/ai-workflow/WORKFLOW_STATE.json` nor advances `phase` — the same
"optional action inside an existing gate, not a new one" reasoning this
section already applies to the plan stage's two-stage refinement below.
`/review-implementation` (`workflow-v2-3-followups`) does write a review
artifact, `.ai-review/feedback/REVIEW_FEEDBACK.md` (or the scoped
equivalent), once its own pre-write guards pass — but not `WORKFLOW_STATE.json`
and not a `phase` transition, so it stays non-gating for the same reason.
`/review-functional` writes nothing at all, unchanged.

For a work item whose `governing_workflow_version` is in
`TWO_STAGE_PLAN_REVIEW_VERSIONS` (`"2.1"` or, since `workflow-2.5.0`,
`"2.2"`), the edge from `REVISING_PLAN` to `AWAITING_PLAN_APPROVAL` is
further refined into `REVISING_PLAN → AWAITING_LOCAL_PLAN_REVIEW →
AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW → AWAITING_PLAN_APPROVAL` (two-stage
local-then-manual-external plan review) — a refinement of the existing
edge, not two additional hard gates layered on top of it. This repository's
own workflow-v2-1-core work item is fixed at `governing_workflow_version:
"1"` for its entire execution, so the refined edge does not apply to it;
see `docs/ai-workflow/WORKFLOW_V2_PLAN.md` (`D-Plan-Review-Stages`) for the
full mechanism, owned by a later checkpoint.

For a `governing_workflow_version: "2.2"` work item only
(`workflow-2.5.0`, `D-Implementation-Review-Stages`), the edge from
`SELF_REVIEWING_IMPLEMENTATION`/`APPLYING_REVIEW_FEEDBACK` to
`AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` is likewise further refined into
`... → AWAITING_LOCAL_IMPLEMENTATION_REVIEW →
AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW →
AWAITING_EXTERNAL_IMPLEMENTATION_REVIEW` (two-stage
local-then-manual-external implementation review) — again a refinement of
an existing edge onto its existing terminal phase name, not a seventh hard
gate. The hard gate count was **6** before `workflow-2.5.0` and stays **6**
after it. A `"1"`/`"2.1"` item's edge is unaffected; see
`docs/ai-workflow/WORKFLOW_V2_PLAN.md` (`D-Implementation-Review-Stages`)
for the full `"2.2"`-only mechanism.

Between gates, Claude may work autonomously, subject to the stop conditions
already defined in `AGENTS.md` (ambiguous product behavior, architecture
changes, new dependency categories, schema migrations, destructive data
operations, repeated verification failures, unrelated working-tree changes).

## Feedback file locations

- Plan/implementation review: `.ai-review/feedback/REVIEW_FEEDBACK.md`
- Functional review: `.ai-review/feedback/FUNCTIONAL_REVIEW.md`

`REVIEW_FEEDBACK.md` is written by `/review-plan` (the
`LOCAL_MODEL_PLAN_REVIEW` stage) and, since `workflow-v2-3-followups`, also
by `/review-implementation` once its own pre-write guards pass — otherwise
read, never written, by Claude. `FUNCTIONAL_REVIEW.md` stays read-only:
nothing this repository's commands write it, including
`/review-functional`. See `docs/ai-workflow/REVIEW_PROTOCOL.md` for the
required feedback structure.
